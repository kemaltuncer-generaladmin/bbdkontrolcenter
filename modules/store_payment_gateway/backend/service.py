"""Link ile tahsilat — iş kuralları.

NE YAPAR. Personel bir form doldurur (ad soyad, telefon, e-posta, il/ilçe,
adres, açıklama ve TUTAR ya da ÜRÜN); ekran tutarı kırar ve SMS'i önizler;
gerekçeli onaydan sonra mağazada bir ödeme bağlantısı üretilir; bağlantı
SMS ile gider; müşteri kendi kartıyla öder; sipariş ve fatura mağaza
tarafında oluşur; ekran yoklamayla durumu gösterir.

NE DEĞİLDİR. Bu ekran POS İZLEME ekranı değildir: banka mutabakatı, terminal
ayarı, taksit/komisyon matrisi burada yoktur. Burada tek bir iş vardır —
uzaktaki müşteriden karttan para tahsil etmek.

PARANIN İKİ YÖNLÜ FRENİ:
  · Yazma çağrıları gerekçe + kuru prova taşır (geçit de gerekçesizi reddeder).
  · SMS'in ÜÇ KATMANLI freni vardır: modül ayarı (`sms_dry_run`), platform
    ayarı (`platform.notify.sms.dry_run`) ve isteğin kendi `dryRun` bayrağı.
    Üçü birden kapalı değilse GERÇEK SMS GİTMEZ. Ayrıca `sms_allowlist`
    doluysa yalnız listedeki numaralara gerçek mesaj çıkar.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): talep yerel tabloda durur,
`connected: False` + `error` döner, panel durumu anlatır ve elden kapatma
yolu açık kalır.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from km_sdk import (
    ExportError,
    SmsMessage,
    build_pdf,
    csv_bytes,
    money,
    number,
    report_dir,
    write_private,
)

from . import collect

#: Serbest (siparişe bağlı olmayan) tahsilat bağlantısı üreten geçit metodu.
#: Bugün `store_api` içinde YOK; varlığı çalışma anında yoklanır ve yoksa
#: ekran özelliği KAPALI ama AÇIKLAMALI gösterir (sessizce patlatmaz).
STANDALONE_METHOD = "bbd_create_payment_request"

STANDALONE_MISSING = (
    "Siparişe bağlı olmayan tahsilat bağlantısı için mağaza ucu henüz yayında "
    f"değil (store.api → {STANDALONE_METHOD}). Talep kaydedildi ve burada "
    "bekliyor; uç yayınlanınca “Bağlantı üret” kendiliğinden açılacak. "
    "Bugün açık yol: Elden Kapatma sekmesinden havale/nakit beyanı."
)

#: Ödeme uçlarının tamamı `/api/admin/bbd/*` altındadır ve mağazada HENÜZ
#: YAYINDA DEĞİL: 13.08.2026'da `bbd/payments/links`, `.../attempts` ve
#: `.../terminals` uçlarının üçü de 404 döndü (çekirdek uçları 200).
#: Bu yüzden panel açılışta bir kez YOKLAR ve durumu ekrana yazar; aksi
#: hâlde personel formu doldurup onayladıktan SONRA 404 görürdü.
PAYMENTS_MISSING = (
    "Mağazanın ödeme uçları (/api/admin/bbd/payments/*) yayında değil: bugün "
    "bu ekrandan ödeme bağlantısı ÜRETİLEMEZ ve durum yoklanamaz. Talepler "
    "kaydedilir ve bekler; Elden Kapatma (havale/nakit) çalışmaya devam eder."
)

#: Rapora ve CSV'ye giren en çok satır. Tahsilat talebi yılda birkaç bin
#: olur; 5.000 tavanı büyümeye yer bırakır ve bozuk sayfalamada belleği korur.
REPORT_ROW_CAP = 5_000

#: Vergi tabloları ve ürün→kategori eşlemesi için yerel önbellek ömrü (sn).
#:
#: NEDEN VAR: canlı ön izleme her 320 ms'de bir `/quote` çağırıyor ve her
#: çağrı vergi oranlarını yeniden çekiyordu. Geçidin hız kovası dakikada 55
#: istek; forma yazan tek bir personel bunu tek başına doldurup ekranı
#: bekletiyordu. Vergi oranı dakikalar içinde değişen bir veri değildir.
TAX_CACHE_TTL = 300

#: Ürünün KDV oranı okunamadığında ekrana yazılan engel.
RATE_UNKNOWN = (
    "Ürünün KDV oranı mağazadan okunamadı; tahsilat başlatılmaz. Vergisiz "
    "saymak faturayı KDV kadar eksik keser, %20 varsaymak da uydurma olur. "
    "Ürünü çıkarıp tutarı serbest kalem olarak yazabilirsiniz."
)


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class PaymentGatewayService:
    """Tahsilat ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 notify: Any = None, printer: Any = None, category: str = "Mağaza",
                 subcategory: str = "Finans", fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._notify = notify
        self._printer = printer
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._requests = store.table("requests")
        self._events = store.table("events")
        self._prefs = store.table("prefs")

        # Vergi tabloları ve ürün→vergi kategorisi eşlemesi. Süresi dolunca
        # yeniden çekilir; `register()` sırasında DEĞİL, ilk kullanımda.
        self._tax_cache: tuple[float, Any, Any] | None = None
        self._product_category: dict[int, int | None] = {}

    # ------------------------------------------------------------- ayarlar

    @property
    def _page_size(self) -> int:
        return max(10, min(200, collect.as_int(self._config.get("page_size"), 50)))

    @property
    def _default_email(self) -> str:
        return collect.text(self._config.get("default_email")) or collect.DEFAULT_EMAIL

    @property
    def _link_base(self) -> str:
        return collect.text(self._config.get("link_base_url"))

    @property
    def _prices_include_tax(self) -> bool:
        """Mağaza ayarının KOPYASI. Ürün fiyatı KDV dâhil girilmişse brütten
        ayrıştırılır; hariçse üstüne eklenir. Yanlış varsayım faturayı KDV
        kadar kaydırır, bu yüzden ayardan gelir ve ekranda yazar."""
        return bool(self._config.get("product_prices_include_tax", False))

    @property
    def _sender(self) -> str:
        return collect.text(self._config.get("sms_header"))

    @property
    def _org_name(self) -> str:
        return collect.text(self._config.get("org_name")) or "BBD Store"

    @property
    def _allowlist(self) -> list[str]:
        raw = self._config.get("sms_allowlist") or []
        return [collect.normal_phone(item) for item in raw if collect.text(item)]

    @property
    def _module_dry_run(self) -> bool:
        """Modülün kendi SMS freni. VARSAYILAN AÇIK."""
        return bool(self._config.get("sms_dry_run", True))

    @property
    def _settle_methods(self) -> dict[str, str]:
        """`havale`/`nakit` → mağazanın ödeme yöntemi KODU.

        Mağaza "havale" diye bir yöntem tanımıyor; `POST /admin/transactions`
        gövdesindeki `paymentMethod` Bagisto'nun kod adını ister. Varsayılan
        eşleme canlı mağazanın kendi ayarından okundu; kurulum farklıysa
        `settle_payment_methods` ayarıyla ezilir.
        """
        merged = dict(collect.SETTLE_METHODS)
        override = self._config.get("settle_payment_methods") or {}
        if isinstance(override, dict):
            for key, value in override.items():
                if collect.text(value):
                    merged[collect.text(key)] = collect.text(value)
        return merged

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    @property
    def _standalone(self) -> bool:
        return callable(getattr(self._api, STANDALONE_METHOD, None))

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "Mağazaya ulaşılamadı."

    # ------------------------------------------------------ yerel tablolar

    async def _record(self, *, request_id: int, action: str, reason: str = "", actor: str = "",
                      result: str = "", detail: Any = None) -> None:
        """Olay zinciri. Talep satırının ÜZERİNE yazılır (durum değişir); "kim,
        neden, ne zaman" burada kalır ve silinmez."""
        try:
            await self._store.execute(
                f"INSERT INTO {self._events} "
                "(request_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(request_id or 0), action, reason, actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), collect.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın
            self._log.warning("olay yazılamadı", action=action, error=str(failure))

    async def _update(self, request_id: int, **fields: Any) -> None:
        """Talep satırını günceller.

        Sütun adları YALNIZ kod içinden gelir (çağıranların anahtar sözcükleri);
        kullanıcı girdisi hiçbir zaman sütun adı olamaz. Değerler her zaman
        parametredir.
        """
        fields["updated_at"] = collect.now_iso()
        columns = ", ".join(f"{key} = ?" for key in fields)
        await self._store.execute(
            f"UPDATE {self._requests} SET {columns} WHERE id = ?",
            (*fields.values(), int(request_id)),
        )

    async def _row(self, request_id: int) -> dict[str, Any] | None:
        try:
            return await self._store.fetch_one(
                f"SELECT * FROM {self._requests} WHERE id = ?", (int(request_id),))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("talep okunamadı", requestId=request_id, error=str(failure))
            return None

    async def _pref(self, key: str) -> str:
        try:
            row = await self._store.fetch_one(
                f"SELECT value FROM {self._prefs} WHERE key = ?", (key,))
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", key=key, error=str(failure))
            return ""
        return collect.text(row["value"]) if row else ""

    async def _set_pref(self, key: str, value: str, actor: str) -> None:
        await self._store.execute(
            f"INSERT INTO {self._prefs} (key, value, actor, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, actor = excluded.actor, "
            "updated_at = excluded.updated_at",
            (key, value, actor, collect.now_iso()),
        )

    # ================================================================ durum

    async def state(self) -> dict[str, Any]:
        """Ekranın açılışta okuduğu durum: mağaza, SMS ve frenler.

        HATA FIRLATMAZ. Mağaza da SMS de kapalıyken bile bir cevap döner;
        ekran "şu kapalı, şunu yapabilirsiniz" der, beyaz sayfa göstermez.
        """
        store_state: dict[str, Any] = {"connected": False, "error": "", "readOnly": None,
                                       "dryRunDefault": True}
        try:
            health = await self._api.health()
            store_state["connected"] = bool(health.get("ok"))
            store_state["error"] = collect.text(health.get("error"))
        except Exception as failure:  # noqa: BLE001 — K7
            store_state["error"] = self._fail(failure)
        try:
            gateway = self._api.state()
            store_state["readOnly"] = bool(gateway.get("readOnly"))
            store_state["dryRunDefault"] = bool(gateway.get("dryRunDefault", True))
        except Exception as failure:  # noqa: BLE001 — geçit durumu okunamadı
            self._log.info("geçit durumu okunamadı", error=str(failure))

        payments_ok, payments_error = await self._payments_probe(store_state["connected"])
        sms_state = await self._sms_state()
        return {
            "ok": True,
            "store": store_state,
            "sms": sms_state,
            "payments": {"available": payments_ok, "error": payments_error,
                         "notice": "" if payments_ok else PAYMENTS_MISSING},
            "standalone": self._standalone,
            "standaloneNotice": "" if self._standalone else STANDALONE_MISSING,
            "defaultEmail": self._default_email,
            "orgName": self._org_name,
            "pricesIncludeTax": self._prices_include_tax,
            "linkBase": self._link_base,
        }

    async def _payments_probe(self, store_connected: bool) -> tuple[bool, str]:
        """Ödeme uçları yayında mı — TEK ve OKUMA amaçlı bir yoklama.

        NEDEN AÇILIŞTA: `bbd_create_payment_link` geçitte bir METOT olarak
        var ama mağazadaki UÇ 404. Yalnız metodun varlığına bakan ekran
        "her şey hazır" der, personel formu doldurur, gerekçe yazar, onaylar
        ve ancak o zaman 404 görür. Yoklamanın maliyeti bir GET'tir.
        """
        if not store_connected:
            return False, "Mağazaya ulaşılamadı."
        try:
            await self._api.bbd_payment_links({})
        except Exception as failure:  # noqa: BLE001 — K7
            return False, self._fail(failure)
        return True, ""

    async def _sms_state(self) -> dict[str, Any]:
        """SMS katmanının üç freni ve kurulum durumu — tek yerde."""
        state: dict[str, Any] = {
            "available": self._notify is not None,
            "configured": False,
            "moduleDryRun": self._module_dry_run,
            "platformDryRun": True,
            "allowlist": self._allowlist,
            "header": self._sender,
            "error": "" if self._notify is not None
                     else "Bildirim yeteneği bu kurulumda yok; SMS gönderilemez.",
        }
        if self._notify is None:
            return state
        try:
            ready = await self._notify.ready()
        except Exception as failure:  # noqa: BLE001 — durum ekranı ayakta kalmalı (K7)
            state["error"] = self._fail(failure)
            return state
        state["configured"] = bool(ready.get("configured"))
        state["platformDryRun"] = bool(ready.get("dryRun", True))
        state["header"] = state["header"] or collect.text(ready.get("header"))
        state["error"] = collect.text(ready.get("error"))
        state["live"] = not (state["moduleDryRun"] or state["platformDryRun"])
        return state

    # ============================================================== referans

    async def reference(self) -> dict[str, Any]:
        """Süzgeç ve formu besleyen referanslar: vergi oranları, şablon, durumlar."""
        out: dict[str, Any] = {
            "ok": True, "connected": True, "error": "",
            "taxCategories": [], "template": await self.template_body(),
            "placeholders": [{"key": key, "hint": hint}
                             for key, hint in collect.PLACEHOLDERS.items()],
            "statuses": [{"value": code, "label": collect.status_view(code)["label"]}
                         for code in collect.LOCAL_STATUSES],
            "defaultEmail": self._default_email,
        }
        try:
            categories = await self._api.tax_categories()
            rates = await self._api.tax_rates()
        except Exception as failure:  # noqa: BLE001 — K7
            out["connected"] = False
            out["error"] = self._fail(failure)
            return out

        for item in collect.rows_of(categories):
            rate = collect.tax_rate_for(collect.as_int(item.get("id")), categories, rates)
            out["taxCategories"].append({
                "id": collect.as_int(item.get("id")),
                "name": collect.text(item.get("name") or item.get("code")),
                # None = "okunamadı"; ekran bunu 0 diye göstermez.
                "rate": None if rate is None else float(rate),
                "note": RATE_UNKNOWN if rate is None else "",
            })
        return out

    async def _tax_tables(self) -> tuple[Any, Any]:
        """Vergi kategorileri + oranları — TTL'li. Okunamazsa boş liste (K7)."""
        now = time.monotonic()
        if self._tax_cache and now - self._tax_cache[0] < TAX_CACHE_TTL:
            return self._tax_cache[1], self._tax_cache[2]
        try:
            categories = await self._api.tax_categories()
            rates = await self._api.tax_rates()
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("vergi oranları okunamadı", error=str(failure))
            # Başarısız okuma ÖNBELLEĞE ALINMAZ: mağaza geri geldiğinde
            # beş dakika daha "oran yok" demeye devam etmesin.
            return {"items": []}, {"items": []}
        self._tax_cache = (now, categories, rates)
        return categories, rates

    async def _rate_lookup(self) -> Any:
        """`productId → KDV oranı` çözücüsü. Çözülemezse **None** döner.

        Vergi kategorileri ve oranları BİR KEZ çekilir, ürün başına değil:
        beş kalemlik bir tahsilatta on ayrı istek atmak hız kovasını (dk 55)
        boşuna tüketir ve ekranı yavaşlatır. Ürünün vergi kategorisi de
        çağrılar arasında hatırlanır — ön izleme her tuş vuruşunda çalışıyor.
        """
        categories, rates = await self._tax_tables()
        cache: dict[int, Decimal | None] = {}

        async def resolve(product_id: int) -> Decimal | None:
            if product_id in cache:
                return cache[product_id]
            category_id = self._product_category.get(product_id)
            if category_id is None:
                try:
                    product = await self._api.product(int(product_id))
                except Exception as failure:  # noqa: BLE001 — K7
                    self._log.warning("ürün okunamadı", productId=product_id,
                                      error=str(failure))
                    cache[product_id] = None
                    return None
                category_id = collect.product_category_id(product)
                self._product_category[product_id] = category_id
            cache[product_id] = collect.tax_rate_for(category_id, categories, rates)
            return cache[product_id]

        return resolve

    # ============================================================ ürün arama

    async def search_products(self, *, q: str = "", page: int = 1,
                              size: int = 0) -> dict[str, Any]:
        """Formdaki ürün seçicisini besler. SUNUCU TARAFI — 1.421 ürün çekilmez.

        `name` süzgeci mağazada GERÇEKTEN uygulanıyor (canlıda denendi:
        süzgeçsiz 1.421, `name=geometri` 67, `name=zzzzqqq` 0). Laravel
        tanımadığı parametreyi sessizce yok saydığı için bu doğrulanmadan
        varsayılamazdı.

        VERGİ ORANI ÜRÜN LİSTESİNDEN OKUNUR, ürün başına detay çağrısı
        YAPILMAZ: `taxCategoryId` zaten listede geliyor ve 20 sonuçlu bir
        aramada 20 ek istek atmak geçidin dakikada 55 isteklik kovasını tek
        seferde tüketip seçiciyi dakikalarca bekletiyordu.
        """
        per_page = size or 25
        filters: dict[str, Any] = {}
        needle = collect.text(q)
        if needle:
            filters["name"] = needle
        try:
            payload = await self._api.products(filters, page=page, per_page=per_page)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page}

        categories, rates = await self._tax_tables()
        rows: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            product_id = collect.as_int(item.get("id"))
            price, discounted = collect.product_price(item)
            category_id = collect.product_category_id(item)
            if product_id:
                self._product_category[product_id] = category_id
            rate = collect.tax_rate_for(category_id, categories, rates)
            rows.append({
                "id": product_id,
                "sku": collect.text(item.get("sku")),
                "name": collect.text(item.get("name")) or f"#{product_id}",
                "price": price,
                "discounted": discounted,
                # None = "okunamadı". Ekran bunu "KDV %0" diye YAZMAZ.
                "taxRate": None if rate is None else float(rate),
                "taxNote": RATE_UNKNOWN if rate is None else "",
            })
        meta = payload.get("meta") or {}
        return {"ok": True, "connected": True, "error": "", "items": rows,
                "total": collect.as_int(meta.get("total"), len(rows)),
                "page": collect.as_int(meta.get("currentPage"), page),
                "size": collect.as_int(meta.get("perPage"), per_page)}

    async def _resolve_lines(self, raw_lines: Any) -> tuple[list[Any], list[str], dict[str, Any]]:
        """Ekranın kalemlerini vergi oranlarıyla zenginleştirir ve tutarı kırar.

        Oran çözümü BURADA (ağ), kırılım `collect.breakdown` içinde (saf) durur.
        Önizleme ile kayıt aynı yoldan geçer: ekranda görülen rakam ile kayda
        giren rakamın farklı olması, bu tür ekranların klasik hatasıdır.
        """
        rate_of = await self._rate_lookup()
        resolved: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for raw in raw_lines or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if collect.text(item.get("kind")) == "product" or item.get("productId"):
                item["kind"] = "product"
                rate = await rate_of(collect.as_int(item.get("productId")))
                if rate is None:
                    # Oran uydurulmaz. Kalem listede kalır (personel neyi
                    # seçtiğini görsün) ama kırılım 0 KDV ile çizilir ve
                    # tahsilat "hazır" sayılmaz.
                    unresolved.append(collect.text(item.get("label"))
                                      or f"#{collect.as_int(item.get('productId'))}")
                item["taxRate"] = 0.0 if rate is None else float(rate)
                item["rateKnown"] = rate is not None
            resolved.append(item)

        lines, problems = collect.lines_from_payload(resolved)
        for label in unresolved:
            problems.append(f"{label}: {RATE_UNKNOWN}")
        amounts = collect.breakdown(lines, prices_include_tax=self._prices_include_tax)
        return lines, problems, amounts

    # ============================================================= önizleme

    async def preview(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Canlı ön izleme: tutar kırılımı + SMS metni + parça sayacı.

        HİÇBİR ŞEY YAZMAZ ve mağazaya yazma çağrısı yapmaz; bu yüzden gerekçe
        de istemez. Personel "Tahsilat başlat"a basmadan önce ne olacağını
        burada görür.
        """
        body = payload or {}
        _, problems, amounts = await self._resolve_lines(body.get("lines"))

        phone = collect.normal_phone(body.get("phone"))
        template = collect.text(body.get("template")) or await self.template_body()
        rendered = collect.render_template(template, {
            "ad": collect.text(body.get("fullName")),
            "tutar": collect.money_tr(amounts["gross"]),
            "link": collect.text(body.get("link")) or self._sample_link(),
            "aciklama": collect.text(body.get("note")),
            "kod": collect.text(body.get("code")) or "TAH-…",
            "kurum": self._org_name,
        })
        plan = collect.sms_plan(rendered["text"])

        problems.extend(filter(None, [
            collect.phone_error(phone) if phone else "Cep numarası yazılmadı.",
            "" if collect.text(body.get("fullName")) else "Ad soyad yazılmadı.",
        ]))
        if amounts["gross"] <= 0:
            problems.append("Tahsil edilecek tutar yok: serbest tutar yazın ya da ürün seçin.")

        return {
            "ok": True,
            "amounts": amounts,
            "sms": {**plan, "missing": rendered["missing"], "unknown": rendered["unknown"]},
            "phone": phone,
            "email": collect.text(body.get("email")) or self._default_email,
            "problems": problems,
            "ready": not problems,
            "smsState": await self._sms_state(),
            "standalone": self._standalone,
            "standaloneNotice": "" if self._standalone or collect.as_int(body.get("orderId"))
                                else STANDALONE_MISSING,
        }

    def _sample_link(self) -> str:
        base = self._link_base or "https://odeme.bbdstore.com.tr"
        return f"{base.rstrip('/')}/ORNEK"

    # ================================================================ kayıt

    async def create(self, *, payload: dict[str, Any], actor: str) -> dict[str, Any]:
        """Talebi TASLAK olarak kaydeder. Mağazaya hiçbir şey yazılmaz.

        Neden önce yerel kayıt: bağlantı üretilemese bile (mağaza kapalı,
        uç yayında değil) personelin doldurduğu bilgi kaybolmasın. Aksi hâlde
        müşteriyi telefonda ikinci kez sorgulamak gerekirdi.
        """
        body = payload or {}
        name = collect.text(body.get("fullName"))
        phone = collect.normal_phone(body.get("phone"))
        problem = collect.phone_error(phone)
        if not name:
            return {"ok": False, "error": "Ad soyad zorunlu."}
        if problem:
            return {"ok": False, "error": problem}

        _, problems, amounts = await self._resolve_lines(body.get("lines"))
        if problems:
            return {"ok": False, "error": " ".join(problems)}
        if amounts["gross"] <= 0:
            return {"ok": False,
                    "error": "Tahsil edilecek tutar yok: serbest tutar yazın ya da ürün seçin."}

        code = collect.request_code(uuid.uuid4().hex)
        row = (
            code, name, phone,
            collect.text(body.get("email")) or self._default_email,
            collect.text(body.get("city")), collect.text(body.get("district")),
            collect.text(body.get("address")), collect.text(body.get("note")),
            json.dumps(amounts["lines"], ensure_ascii=False),
            amounts["net"], amounts["tax"], amounts["gross"],
            collect.as_int(body.get("orderId")), collect.DRAFT, actor, collect.now_iso(),
        )
        try:
            await self._store.execute(
                f"INSERT INTO {self._requests} "
                "(code, full_name, phone, email, city, district, address, note, items, "
                " net, tax, gross, order_id, status, actor, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        except Exception as failure:  # noqa: BLE001 — kayıt açılamadı, iş burada durur
            self._log.warning("talep açılamadı", error=str(failure))
            return {"ok": False, "error": f"Talep kaydedilemedi: {self._fail(failure)}"}

        created = await self._by_code(code)
        request_id = collect.as_int((created or {}).get("id"))
        await self._record(request_id=request_id, action="create", actor=actor, result="ok",
                           detail={"gross": amounts["gross"], "code": code})
        return {"ok": True, "error": "", "id": request_id, "code": code,
                "amounts": amounts,
                "standalone": self._standalone or bool(collect.as_int(body.get("orderId"))),
                "standaloneNotice": "" if self._standalone or collect.as_int(body.get("orderId"))
                                    else STANDALONE_MISSING}

    async def _by_code(self, code: str) -> dict[str, Any] | None:
        try:
            return await self._store.fetch_one(
                f"SELECT * FROM {self._requests} WHERE code = ?", (code,))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("talep okunamadı", code=code, error=str(failure))
            return None

    # ================================================================ liste

    async def requests(self, *, q: str = "", status: str = "", start: str = "", end: str = "",
                       min_amount: int | None = None, max_amount: int | None = None,
                       page: int = 1, size: int = 0) -> dict[str, Any]:
        """Talep listesi — YEREL tablodan, sunucu tarafı sayfalı."""
        per_page = size or self._page_size
        where, params = collect.filter_clause(q=q, status=status, start=start, end=end,
                                              min_amount=min_amount, max_amount=max_amount)
        offset = max(0, (max(1, page) - 1) * per_page)
        try:
            total_row = await self._store.fetch_one(
                f"SELECT COUNT(*) AS total FROM {self._requests}{where}", tuple(params))
            rows = await self._store.fetch_all(
                f"SELECT * FROM {self._requests}{where} ORDER BY id DESC LIMIT ? OFFSET ?",
                (*params, per_page, offset))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "items": [], "total": 0, "page": page, "size": per_page, "pages": 0}

        total = collect.as_int((total_row or {}).get("total"), len(rows))
        items = [collect.request_view(row, link_base=self._link_base) for row in rows]
        return {
            "ok": True, "connected": True, "error": "",
            "items": items, "total": total, "page": max(1, page), "size": per_page,
            "pages": max(1, -(-total // per_page)) if total else 0,
            "summary": self._summary(items),
        }

    @staticmethod
    def _summary(items: list[dict[str, Any]]) -> dict[str, Any]:
        """Sayfa özeti. Renk tek başına anlam taşımasın diye ekran bu sayıları
        rozetin yanında yazar."""
        counts: dict[str, int] = {}
        collected = 0
        waiting = 0
        for item in items:
            code = item["status"]["code"]
            counts[code] = counts.get(code, 0) + 1
            if code in (collect.PAID, collect.SETTLED):
                collected += item["gross"]
            elif code in (collect.LINKED, collect.SENT, collect.DRAFT):
                waiting += item["gross"]
        return {"counts": counts, "collected": collected, "waiting": waiting}

    async def card(self, request_id: int) -> dict[str, Any]:
        """Talep detayı: kayıt + olay zinciri + mağazadaki POS denemeleri."""
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}
        view = collect.request_view(row, link_base=self._link_base)

        events: list[dict[str, Any]] = []
        try:
            raw_events = await self._store.fetch_all(
                f"SELECT * FROM {self._events} WHERE request_id = ? ORDER BY id DESC LIMIT 200",
                (int(request_id),))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran dursun
            raw_events = []
            self._log.warning("olaylar okunamadı", error=str(failure))
        for item in raw_events:
            events.append({
                "action": collect.text(item.get("action")),
                "reason": collect.text(item.get("reason")),
                "actor": collect.text(item.get("actor")),
                "result": collect.text(item.get("result")),
                "detail": collect.text(item.get("detail")),
                "createdAt": collect.text(item.get("created_at")),
            })

        attempts: list[dict[str, Any]] = []
        warning = ""
        if view["orderId"] or view["token"]:
            filters: dict[str, Any] = {}
            if view["orderId"]:
                filters["order_id"] = view["orderId"]
            elif view["token"]:
                filters["token"] = view["token"]
            try:
                payload = await self._api.bbd_payment_attempts(filters, page=1, per_page=25)
                attempts = [collect.attempt_row(item) for item in (payload.get("items") or [])]
            except Exception as failure:  # noqa: BLE001 — K7
                warning = f"POS denemeleri okunamadı: {self._fail(failure)}"

        return {"ok": True, "error": "", "request": view, "events": events,
                "attempts": attempts, "warning": warning,
                "items": json.loads(collect.text(row.get("items")) or "[]"),
                "canRelink": collect.can_relink(view)[0],
                "relinkBlock": collect.can_relink(view)[1]}

    # ============================================================ bağlantı

    async def start(self, request_id: int, *, reason: str, actor: str, dry_run: bool = True,
                    send_sms: bool = True) -> dict[str, Any]:
        """Gerekçeli onay → ödeme bağlantısı → (istenirse) SMS.

        Personelin tek düğmesi budur: onay, bağlantı ve mesaj tek gerekçeyle
        zincirlenir. Üçünü ayrı düğmeye bölmek, "onayladım ama link üretmeyi
        unuttum" durumunu üretiyordu.
        """
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}

        view = collect.request_view(row, link_base=self._link_base)
        allowed, block = collect.can_relink(view)
        if not allowed:
            # ÇİFT ÇEKİM KAPISI: parası çekilmiş olabilecek bir talebe ikinci
            # link üretmek, müşteriden iki kez para almaktır.
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"blocked": block})
            return {"ok": False, "error": block}

        gross = collect.as_int(row.get("gross"))
        if gross <= 0:
            return {"ok": False, "error": "Tutarı sıfır olan talep için bağlantı üretilmez."}

        order_id = collect.as_int(row.get("order_id"))
        await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                           result="denendi", detail={"gross": gross, "orderId": order_id})

        if not order_id and not self._standalone:
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"missing": STANDALONE_METHOD})
            return {"ok": False, "error": STANDALONE_MISSING, "standalone": False}

        try:
            if order_id:
                result = await self._api.bbd_create_payment_link(
                    order_id=order_id, amount=gross, reason=reason, actor=actor,
                    dry_run=dry_run)
            else:
                result = await getattr(self._api, STANDALONE_METHOD)(
                    payload=self._standalone_payload(row), reason=reason, actor=actor,
                    dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        link = collect.link_row(result.get("data") if isinstance(result.get("data"), dict)
                               else result)
        dry = bool(result.get("dryRun", dry_run))
        fields: dict[str, Any] = {"reason": reason, "actor": actor}
        if link["token"]:
            fields["token"] = link["token"]
        if link["url"]:
            fields["link"] = link["url"]
        if link["orderId"]:
            fields["order_id"] = link["orderId"]
        if not dry and (link["token"] or link["url"]):
            fields["status"] = collect.LINKED
        await self._update(request_id, **fields)
        await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                           result="dry_run" if dry else "ok",
                           detail={"token": link["token"], "url": link["url"]})

        out: dict[str, Any] = {"ok": True, "error": "", "dryRun": dry, "link": link,
                               "standalone": self._standalone or bool(order_id)}
        if dry:
            out["notice"] = ("Kuru prova: mağazaya istek gönderilmedi, bağlantı üretilmedi. "
                             "Gerçekten üretmek için kuru provayı kapatın.")
            return out
        if not link["token"] and not link["url"]:
            out["notice"] = ("Mağaza bağlantı bilgisi döndürmedi; talep bekliyor olarak "
                             "kaldı. Yoklama ile durumu izleyin.")
            return out
        if send_sms:
            out["sms"] = await self.send_sms(request_id, reason=reason, actor=actor,
                                             dry_run=dry_run)
        return out

    def _standalone_payload(self, row: Any) -> dict[str, Any]:
        """Serbest tahsilat için mağazaya gidecek gövde.

        Uç henüz yayında olmadığı için bu şeklin sözleşmesi BİZİM ÖNERİMİZDİR;
        uç yayınlandığında alan adları doğrulanmalıdır. Şekli burada tek yerde
        tutmak, uç geldiğinde değiştirilecek yeri belirginleştiriyor.
        """
        data = dict(row or {})
        return {
            "code": collect.text(data.get("code")),
            "amount": collect.as_int(data.get("gross")),
            "customer": {
                "name": collect.text(data.get("full_name")),
                "phone": collect.text(data.get("phone")),
                "email": collect.text(data.get("email")),
                "city": collect.text(data.get("city")),
                "district": collect.text(data.get("district")),
                "address": collect.text(data.get("address")),
            },
            "note": collect.text(data.get("note")),
            "items": json.loads(collect.text(data.get("items")) or "[]"),
        }

    async def cancel(self, request_id: int, *, reason: str, actor: str,
                     dry_run: bool = True) -> dict[str, Any]:
        """Bağlantıyı öldürür. KAYIT SİLİNMEZ (BBD veri silme yasağı)."""
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}
        view = collect.request_view(row, link_base=self._link_base)
        if view["status"]["code"] == collect.PAID:
            return {"ok": False,
                    "error": "Ödenmiş tahsilat iptal edilmez; iade için İadeler ekranı "
                             "kullanılır."}

        token = view["token"]
        await self._record(request_id=request_id, action="cancel", reason=reason, actor=actor,
                           result="denendi", detail={"token": token})
        dry = dry_run
        if token:
            try:
                result = await self._api.bbd_cancel_payment_link(token, reason=reason,
                                                                 actor=actor, dry_run=dry_run)
                dry = bool(result.get("dryRun", dry_run))
            except Exception as failure:  # noqa: BLE001 — K7
                await self._record(request_id=request_id, action="cancel", reason=reason,
                                   actor=actor, result="hata", detail={"error": str(failure)})
                return {"ok": False, "error": self._fail(failure)}

        if not dry:
            await self._update(request_id, status=collect.CANCELLED, reason=reason, actor=actor)
        await self._record(request_id=request_id, action="cancel", reason=reason, actor=actor,
                           result="dry_run" if dry else "ok")
        return {"ok": True, "error": "", "dryRun": dry}

    # ================================================================== SMS

    def _sms_gate(self, phone: str, dry_run: bool) -> tuple[bool, str]:
        """ÜÇ KATMANLI FREN + allowlist. Gerçek mesaj yalnız dördü de izin
        verirse çıkar; hangi katmanın tuttuğu ekrana YAZILIR."""
        if self._notify is None:
            return False, "Bildirim yeteneği yok."
        if dry_run:
            return False, "İsteğin kendi kuru provası açık."
        if self._module_dry_run:
            return False, "Modül ayarı: sms_dry_run açık (modules.store_payment_gateway)."
        allow = self._allowlist
        if allow and collect.normal_phone(phone) not in allow:
            return False, ("Numara SMS beyaz listesinde değil "
                           "(modules.store_payment_gateway.sms_allowlist).")
        return True, ""

    async def send_sms(self, request_id: int, *, reason: str, actor: str,
                       dry_run: bool = True) -> dict[str, Any]:
        """Ödeme bağlantısını SMS ile gönderir."""
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}
        view = collect.request_view(row, link_base=self._link_base)
        if not view["link"]:
            return {"ok": False,
                    "error": "Bu talebin ödeme bağlantısı yok; önce bağlantı üretin."}
        if view["status"]["code"] in (collect.PAID, collect.SETTLED):
            return {"ok": False, "error": "Tahsilat kapanmış; SMS gönderilmez."}

        rendered = collect.render_template(await self.template_body(), {
            "ad": view["fullName"], "tutar": collect.money_tr(view["gross"]),
            "link": view["link"], "aciklama": view["note"], "kod": view["code"],
            "kurum": self._org_name,
        })
        plan = collect.sms_plan(rendered["text"])
        phone = view["phone"]
        phone_problem = collect.phone_error(phone)
        if phone_problem:
            return {"ok": False, "error": phone_problem}

        allowed, block = self._sms_gate(phone, dry_run)
        if not allowed:
            await self._update(request_id, sms_state="dry_run", sms_at=collect.now_iso())
            await self._record(request_id=request_id, action="sms", reason=reason, actor=actor,
                               result="dry_run", detail={"blocked": block, "parts": plan["parts"]})
            return {"ok": True, "error": "", "sent": False, "dryRun": True, "plan": plan,
                    "notice": f"SMS GÖNDERİLMEDİ — {block}"}

        await self._record(request_id=request_id, action="sms", reason=reason, actor=actor,
                           result="denendi", detail={"parts": plan["parts"]})
        try:
            provider = await self._notify.sms()
            result = await provider.send([SmsMessage(to=phone, text=rendered["text"])],
                                         header=self._sender or None)
        except Exception as failure:  # noqa: BLE001 — SMS katmanı dışarısı (K7)
            await self._update(request_id, sms_state="error", sms_at=collect.now_iso())
            await self._record(request_id=request_id, action="sms", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": f"SMS gönderilemedi: {self._fail(failure)}",
                    "plan": plan}

        accepted = bool(getattr(result, "accepted", False))
        provider_dry = bool(getattr(result, "dry_run", False))
        await self._update(request_id,
                           sms_state="dry_run" if provider_dry else ("sent" if accepted
                                                                     else "error"),
                           sms_at=collect.now_iso(),
                           **({"status": collect.SENT}
                              if accepted and not provider_dry
                              and view["status"]["code"] in (collect.DRAFT, collect.LINKED)
                              else {}))
        await self._record(request_id=request_id, action="sms", reason=reason, actor=actor,
                           result="dry_run" if provider_dry else ("ok" if accepted else "hata"),
                           detail={"jobId": getattr(result, "job_id", ""),
                                   "parts": getattr(result, "parts", plan["parts"])})
        return {
            "ok": True, "error": "", "sent": accepted and not provider_dry,
            "dryRun": provider_dry, "plan": plan,
            "jobId": collect.text(getattr(result, "job_id", "")),
            "parts": collect.as_int(getattr(result, "parts", plan["parts"])),
            "notice": ("Platform kuru provası açık: mesaj sağlayıcıya gitmedi."
                       if provider_dry else ""),
        }

    # =============================================================== yoklama

    async def poll(self, request_id: int) -> dict[str, Any]:
        """Mağazadaki bağlantı/işlem durumunu okur ve yerel durumu eşler.

        BİLİNMEYEN DURUM "BAŞARISIZ" YAZILMAZ. Eşleme `collect.map_status`
        içindedir ve tanımadığı her sözcüğü `unknown` yapar; o satırda yeni
        link üretimi kilitlenir.
        """
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}
        view = collect.request_view(row, link_base=self._link_base)
        if not view["token"] and not view["orderId"]:
            return {"ok": True, "connected": True, "error": "", "changed": False,
                    "request": view,
                    "notice": "Bu talebin mağazada karşılığı yok; yoklanacak bir şey yok."}

        filters: dict[str, Any] = {}
        if view["token"]:
            filters["token"] = view["token"]
        if view["orderId"]:
            filters["order_id"] = view["orderId"]
        try:
            payload = await self._api.bbd_payment_links(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "changed": False, "request": view}

        rows = [collect.link_row(item) for item in (payload.get("items") or [])]
        match = next((item for item in rows
                      if item["token"] and item["token"] == view["token"]), None)
        if match is None and rows:
            match = rows[0]
        if match is None:
            # Kayıt bulunamadı: SESSİZ "başarısız" değil, açık "bilinmiyor".
            await self._apply_status(request_id, "", collect.UNKNOWN)
            refreshed = collect.request_view(await self._row(request_id) or row,
                                             link_base=self._link_base)
            return {"ok": True, "connected": True, "error": "", "changed": True,
                    "request": refreshed,
                    "notice": "Mağazada bu bağlantı bulunamadı. " + collect.DOUBLE_CHARGE_WARNING}

        verdict = collect.map_status(match["status"], local=view["status"]["code"])
        fields: dict[str, Any] = {"store_status": match["status"], "status": verdict["code"]}
        if match["orderId"]:
            fields["order_id"] = match["orderId"]
        if match["invoiceId"]:
            fields["invoice_id"] = match["invoiceId"]
        await self._update(request_id, **fields)

        invoice_note = ""
        if verdict["code"] == collect.PAID and not fields.get("invoice_id"):
            invoice_note = await self._find_invoice(request_id, fields.get("order_id")
                                                    or view["orderId"])

        await self._record(request_id=request_id, action="poll", result="ok",
                           detail={"storeStatus": match["status"], "mapped": verdict["code"]})
        refreshed = collect.request_view(await self._row(request_id) or row,
                                         link_base=self._link_base)
        return {"ok": True, "connected": True, "error": "",
                "changed": verdict["code"] != view["status"]["code"],
                "request": refreshed, "notice": invoice_note or verdict["note"]}

    async def _apply_status(self, request_id: int, store_status: str, code: str) -> None:
        await self._update(request_id, store_status=store_status, status=code)

    async def _find_invoice(self, request_id: int, order_id: int) -> str:
        """Ödeme sonrası oluşan faturayı bulur. Bulunamazsa YOK denir,
        uydurulmaz: fatura numarası müşteriye söylenen bir şeydir.

        SİPARİŞİN KENDİSİNDEN OKUNUR, fatura listesi SÜZÜLMEZ. Canlıda
        `GET /admin/invoices?order_id=<n>` sipariş kimliğine değil, sipariş
        NUMARASININ PARÇASINA bakıyor (`order_id=1` mağazadaki 11 faturanın
        HEPSİNİ döndürdü) ve fatura kaydı `orderId` alanını boş bırakıyor.
        İlk satırı almak, başka müşterinin fatura numarasını bu talebe
        yazmak demekti. `GET /admin/orders/{id}` ise faturaları gömülü ve
        kesin veriyor.
        """
        if not order_id:
            return ""
        try:
            order = await self._api.order(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            return f"Fatura okunamadı: {self._fail(failure)}"
        invoice_id = self._invoice_id_of(order)
        if not invoice_id:
            return "Ödeme alındı; fatura henüz oluşmamış olabilir (birkaç dakika sürer)."
        await self._update(request_id, invoice_id=invoice_id)
        return ""

    @staticmethod
    def _invoice_id_of(order: Any) -> int:
        """Sipariş detayındaki EN ESKİ faturanın kimliği (yoksa 0)."""
        rows = [item for item in ((order or {}).get("invoices") or [])
                if isinstance(item, dict)]
        ids = sorted(collect.as_int(item.get("id")) for item in rows)
        return next((value for value in ids if value), 0)

    # ========================================================= elden kapatma

    async def settle(self, request_id: int, *, method: str, reference: str, amount: int,
                     reason: str, actor: str, dry_run: bool = True) -> dict[str, Any]:
        """Havale/nakit beyanı — tahsilatı KARTSIZ kapatır.

        Bu bir BEYANDIR: parayı personel gördü ve kayda geçiriyor.

        MAĞAZA ÖDEMEYİ FATURAYA İŞLİYOR, siparişe değil: `POST
        /admin/transactions` zorunlu alanları `invoiceId`, `paymentMethod`,
        `amount`. Faturası olmayan talep için mağazaya istek HİÇ GİTMEZ;
        yazılamadığında da beyan yerelde durur ve ekran nedenini söyler —
        beyanı kaybetmek, mutabakatta açık bırakmaktır.
        """
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if method not in ("havale", "nakit"):
            return {"ok": False, "error": "Yöntem 'havale' ya da 'nakit' olmalı."}
        row = await self._row(request_id)
        if not row:
            return {"ok": False, "error": "Talep bulunamadı."}
        view = collect.request_view(row, link_base=self._link_base)
        if view["status"]["code"] in (collect.PAID, collect.SETTLED):
            return {"ok": False, "error": "Bu tahsilat zaten kapanmış."}
        if view["status"]["moneyMayBeTaken"]:
            return {"ok": False,
                    "error": "Karttan para çekilmiş olabilir; elden kapatmadan önce banka "
                             "durumu netleşmeli. " + collect.DOUBLE_CHARGE_WARNING}

        paid = collect.as_int(amount) or view["gross"]
        await self._record(request_id=request_id, action="settle", reason=reason, actor=actor,
                           result="denendi", detail={"method": method, "amount": paid})

        store_note = ""
        dry = dry_run
        invoice_id, invoice_note = await self._settle_invoice(view)
        if invoice_id:
            # GÖVDE ŞEKLİ MAĞAZANIN ŞEMASIDIR, bizim icadımız değil:
            # `POST /api/admin/transactions` zorunlu alanları `invoiceId`,
            # `paymentMethod`, `amount`. Kayıt SİPARİŞE değil FATURAYA
            # işlenir; tutar fatura toplamını aşarsa mağaza 400 döndürür.
            # `reference` (dekont no) mağazada karşılığı OLMAYAN alandır;
            # yerel satırda ve olay zincirinde durur — ekran bunu yazar.
            payload = {
                "invoiceId": invoice_id,
                "paymentMethod": self._settle_methods.get(method, method),
                "amount": collect.to_amount(paid),
            }
            try:
                result = await self._api.record_transaction(payload=payload, reason=reason,
                                                            actor=actor, dry_run=dry_run)
                dry = bool(result.get("dryRun", dry_run))
            except Exception as failure:  # noqa: BLE001 — K7
                store_note = (f"Beyan yerel kayda geçti ama mağazaya işlenemedi: "
                              f"{self._fail(failure)}")
                await self._record(request_id=request_id, action="settle", reason=reason,
                                   actor=actor, result="hata", detail={"error": str(failure)})
            else:
                if collect.text(reference):
                    store_note = ("Dekont/makbuz numarası mağazada saklanmaz; yalnız "
                                  "bu ekranda ve denetim kaydında durur.")
        else:
            store_note = invoice_note

        if not dry:
            fields: dict[str, Any] = {"status": collect.SETTLED, "settle_method": method,
                                      "settle_ref": collect.text(reference),
                                      "reason": reason, "actor": actor}
            if invoice_id and not view["invoiceId"]:
                fields["invoice_id"] = invoice_id
            await self._update(request_id, **fields)
        await self._record(request_id=request_id, action="settle", reason=reason, actor=actor,
                           result="dry_run" if dry else "ok",
                           detail={"method": method, "amount": paid,
                                   "reference": collect.text(reference)})
        return {"ok": True, "error": "", "dryRun": dry, "notice": store_note}

    async def _settle_invoice(self, view: dict[str, Any]) -> tuple[int, str]:
        """Elden kapatmanın işleneceği fatura. Yoksa 0 + NEDENİ.

        Mağaza ödeme kaydını faturaya bağlar; siparişi olmayan ya da henüz
        faturası kesilmemiş bir talep mağazaya işlenemez. Bu bir hata değil,
        SÖYLENMESİ gereken bir durumdur: beyan yerelde durur, mutabakatta
        elle eşleştirilir.
        """
        if view["invoiceId"]:
            return view["invoiceId"], ""
        if not view["orderId"]:
            return 0, ("Talep bir siparişe bağlı değil; mağazaya ödeme kaydı yazılmadı "
                       "(mağaza ödemeyi faturaya işliyor). Beyan yerel kayıtta ve "
                       "raporda görünür.")
        try:
            order = await self._api.order(int(view["orderId"]))
        except Exception as failure:  # noqa: BLE001 — K7
            return 0, (f"Siparişin faturası okunamadı ({self._fail(failure)}); beyan yerel "
                       "kayda geçti, mağazaya işlenmedi.")
        invoice_id = self._invoice_id_of(order)
        if not invoice_id:
            return 0, ("Siparişin henüz faturası yok; mağaza ödeme kaydını faturaya "
                       "işliyor. Beyan yerel kayıtta durur, fatura kesilince mağazada "
                       "elle eşleştirin.")
        return invoice_id, ""

    # ============================================================== şablon

    async def template_body(self) -> str:
        saved = await self._pref("sms_template")
        return saved or collect.text(self._config.get("sms_template")) or collect.DEFAULT_TEMPLATE

    async def template(self) -> dict[str, Any]:
        body = await self.template_body()
        sample = collect.render_template(body, {
            "ad": "Ayşe Yılmaz", "tutar": collect.money_tr(125_000),
            "link": self._sample_link(), "aciklama": "Deneme sınavı ücreti",
            "kod": "TAH-20260813-7F3A", "kurum": self._org_name,
        })
        return {"ok": True, "error": "", "body": body,
                "placeholders": [{"key": key, "hint": hint}
                                 for key, hint in collect.PLACEHOLDERS.items()],
                "sample": sample["text"], "unknown": sample["unknown"],
                "plan": collect.sms_plan(sample["text"])}

    async def save_template(self, *, body: str, reason: str, actor: str) -> dict[str, Any]:
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        text_body = collect.text(body)
        if not text_body:
            return {"ok": False, "error": "Şablon boş olamaz."}
        if "{link}" not in text_body:
            return {"ok": False,
                    "error": "Şablonda {link} yer tutucusu bulunmalı; bağlantısız SMS "
                             "müşteriye hiçbir işe yaramaz."}
        check = collect.render_template(text_body, {key: "x" for key in collect.PLACEHOLDERS})
        if check["unknown"]:
            return {"ok": False,
                    "error": "Tanınmayan yer tutucu: "
                             + ", ".join(f"{{{name}}}" for name in check["unknown"])}
        await self._set_pref("sms_template", text_body, actor)
        await self._record(request_id=0, action="template", reason=reason, actor=actor,
                           result="ok", detail={"length": len(text_body)})
        return {"ok": True, "error": "", "body": text_body,
                "plan": collect.sms_plan(text_body)}

    # ================================================================ rapor

    async def _scan(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        where, params = collect.filter_clause(**filters)
        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._requests}{where} ORDER BY id DESC LIMIT ?",
            (*params, REPORT_ROW_CAP + 1))
        truncated = len(rows) > REPORT_ROW_CAP
        return ([collect.request_view(row, link_base=self._link_base)
                 for row in rows[:REPORT_ROW_CAP]], truncated)

    async def export_csv(self, *, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            rows, truncated = await self._scan(filters or {})
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}

        headers = ["Talep no", "Tarih", "Müşteri", "Telefon", "E-posta", "Matrah", "KDV",
                   "Toplam", "Durum", "Sipariş", "Fatura", "Yöntem", "Personel"]
        table = [[row["code"], row["createdAt"], row["fullName"], row["phone"], row["email"],
                  money(row["net"]), money(row["tax"]), money(row["gross"]),
                  row["status"]["label"], row["orderId"] or "", row["invoiceId"] or "",
                  row["settleMethod"] or "kart", row["actor"]] for row in rows]

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        name = f"magaza-tahsilat-listesi-{stamp}.csv"
        try:
            path = write_private(self._export_dir / name, csv_bytes(headers, table))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "rows": len(table), "truncated": truncated}

    async def preview_report(self, kind: str,
                             params: dict[str, Any] | None = None) -> dict[str, Any]:
        produced = await self.build_report(kind, params or {})
        if not produced.get("ok"):
            return produced
        try:
            pages = await self._render_pages(Path(produced["path"]))
        except PreviewError as failure:
            return {**produced, "pages": [], "previewError": str(failure)}
        return {**produced, "pages": pages, "previewError": ""}

    async def build_report(self, kind: str, params: dict[str, Any]) -> dict[str, Any]:
        if kind not in ("collection", "openlinks"):
            return {"ok": False, "error": f"Bilinmeyen rapor: {kind}"}
        filters: dict[str, Any] = {"start": collect.text(params.get("start")),
                                   "end": collect.text(params.get("end"))}
        if kind == "openlinks":
            # SMS gidince durum `sent` oluyor; yalnız `linked` süzmek raporu
            # tam da göstermesi gereken satırlardan (link üretilmiş, mesaj
            # atılmış, hâlâ ödenmemiş) mahrum bırakıyordu.
            filters["statuses"] = [collect.LINKED, collect.SENT]
        try:
            rows, truncated = await self._scan(filters)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": self._fail(failure)}
        if not rows:
            return {"ok": False, "error": "Bu süzgeçte rapora girecek tahsilat yok."}

        stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
        content = self._report_pdf(kind, rows, truncated=truncated)
        name = (f"magaza-tahsilat-{'icmal' if kind == 'collection' else 'acik-link'}"
                f"-{stamp}.pdf")
        try:
            path = write_private(self._export_dir / name, content)
        except (OSError, ExportError) as failure:
            return {"ok": False, "error": str(failure)}
        self._log.info("tahsilat raporu üretildi", kind=kind, rows=len(rows), path=str(path))
        return {"ok": True, "error": "", "path": str(path), "name": name,
                "bytes": len(content), "rows": len(rows), "truncated": truncated}

    def _report_pdf(self, kind: str, rows: list[dict[str, Any]], *, truncated: bool) -> bytes:
        paid = [row for row in rows if row["status"]["code"] in (collect.PAID, collect.SETTLED)]
        waiting = [row for row in rows
                   if row["status"]["code"] in (collect.LINKED, collect.SENT, collect.DRAFT)]
        risky = [row for row in rows if row["status"]["moneyMayBeTaken"]]

        sections: list[dict[str, Any]] = [{
            "kind": "tiles", "title": "Özet",
            "tiles": [("Talep", number(len(rows))), ("Tahsil edilen", money(
                sum(row["gross"] for row in paid))),
                ("Bekleyen", money(sum(row["gross"] for row in waiting))),
                ("Doğrulanmalı", number(len(risky)))],
        }]
        subset = waiting if kind == "openlinks" else rows
        sections.append({
            "kind": "table", "title": "Açık bağlantılar" if kind == "openlinks" else "Tahsilat",
            "headers": ["Talep no", "Müşteri", "Telefon", "Toplam", "Durum"],
            "align": "LLLRL", "widths": [1.4, 2.4, 1.2, 1, 1.4],
            "rows": [[row["code"], row["fullName"], row["phone"], money(row["gross"]),
                      row["status"]["label"]] for row in subset[:400]],
        })
        if risky:
            # Bu bölüm raporun asıl sebebi: "başarısız" sanılıp ikinci link
            # gönderilen satırlar burada tek tek görünür.
            sections.append({
                "kind": "table", "title": "Bankadan doğrulanmalı (para çekilmiş olabilir)",
                "headers": ["Talep no", "Müşteri", "Toplam", "Ham durum"],
                "align": "LLRL", "widths": [1.4, 2.6, 1, 1.6],
                "rows": [[row["code"], row["fullName"], money(row["gross"]),
                          row["storeStatus"] or "—"] for row in risky[:200]],
            })
            sections.append({"kind": "note", "text": collect.DOUBLE_CHARGE_WARNING})
        if truncated:
            sections.append({"kind": "note",
                             "text": f"Liste {REPORT_ROW_CAP} satırda kesildi; "
                                     "aralığı daraltın."})
        return build_pdf(title="Tahsilat icmali" if kind == "collection" else "Açık ödeme linkleri",
                         subtitle=f"{len(rows)} talep · {collect.today_iso()}",
                         sections=sections, footer="Kontrol Merkezi · Mağaza")

    async def _render_pages(self, path: Path, *, max_pages: int = 12,
                            dpi: int = 110) -> list[str]:
        binary = shutil.which("pdftoppm")
        if not binary:
            raise PreviewError(
                "Önizleme üretilemedi: `pdftoppm` yok (poppler-utils kurulmalı). "
                "Rapor yine de kaydedildi ve yazdırılabilir.")
        with tempfile.TemporaryDirectory(prefix="km-tahsilat-onizleme-") as folder:
            target = Path(folder) / "sayfa"
            try:
                process = await asyncio.create_subprocess_exec(
                    binary, "-png", "-r", str(int(dpi)), "-f", "1", "-l", str(int(max_pages)),
                    str(path), str(target),
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                _, err = await asyncio.wait_for(process.communicate(), timeout=60)
            except TimeoutError:
                raise PreviewError("Önizleme üretilemedi: süre aşıldı.") from None
            except OSError as failure:
                raise PreviewError(f"Önizleme üretilemedi: {failure}") from failure
            if process.returncode != 0:
                raise PreviewError(
                    f"Önizleme üretilemedi: {err.decode(errors='replace').strip()}")
            return ["data:image/png;base64," + base64.b64encode(item.read_bytes()).decode("ascii")
                    for item in sorted(Path(folder).glob("sayfa*.png"))]

    async def print_report(self, path: str, *, copies: int = 1) -> dict[str, Any]:
        """Üretilmiş raporu yazıcıya gönderir.

        GÜVENLİK: yalnız BİZİM rapor klasörümüzdeki dosya basılabilir. Serbest
        yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
        döktürmeye açık kapı bırakırdı.
        """
        if self._printer is None:
            return {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            resolved = Path(path).expanduser().resolve(strict=True)
        except OSError:
            return {"ok": False, "error": "Basılacak rapor bulunamadı."}
        allowed = self._export_dir.resolve()
        if not str(resolved).startswith(str(allowed) + os.sep):
            return {"ok": False,
                    "error": "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."}
        try:
            result = await self._printer.print_file(resolved, title=resolved.name,
                                                    copies=max(1, min(20, int(copies))))
        except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
            return {"ok": False, "error": self._fail(failure)}
        return {"ok": True, **result, "name": resolved.name}

    async def printer_status(self) -> dict[str, Any]:
        if self._printer is None:
            return {"ready": False, "error": "Yazıcı yeteneği bu kurulumda yok."}
        try:
            return await self._printer.status()
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ready": False, "error": self._fail(failure)}
