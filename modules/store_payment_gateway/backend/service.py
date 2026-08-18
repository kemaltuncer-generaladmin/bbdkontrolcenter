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

#: Ödeme bağlantısı üreten geçit metodu — TEK YOL, HER TALEP İÇİN.
#: Varlığı çalışma anında yoklanır; yoksa ekran özelliği KAPALI ama AÇIKLAMALI
#: gösterir (sessizce patlatmaz).
#:
#: ADI BİR DÖNEM `bbd_create_payment_request`TI VE O METOT HİÇ VAR OLMADI.
#: Yoklama hep başarısız oluyordu, yani ekrandaki düğme hiç açılmadı. Geçit
#: aynı ucu (`POST /api/admin/bbd/payment-links`) baştan beri
#: `bbd_create_payment_link` adıyla sunuyor; bağlanan ad artık odur.
#:
#: SİPARİŞE BAĞLI/BAĞSIZ AYRIMI ARTIK YOK. Mağaza ucu gövdedeki `orderId`
#: alanını HİÇ OKUMAZ (`PaymentLinkController::store` yalnız
#: kind·amount·items·billing·description okur) ve geçit `order_id` verilen
#: çağrıyı açıkça reddeder. Siparişle ilişki mağazada, ödeme tamamlanınca
#: kurulur — bu yüzden iki dal tek çağrıya indi.
STANDALONE_METHOD = "bbd_create_payment_link"

#: Geçitte metot bulunamadığında ekrana yazılan metin.
#:
#: METİN BİLEREK GENELDİR: metodun adı bir gün değişirse ya da geçit sürümü
#: geride kalırsa ekran yine anlaşılır konuşsun. Eski metin "şu metot yazılmadı"
#: diyordu; bu, adı değişen bir metotta personeli var olmayan bir işi beklemeye
#: iterdi. Aranan ad metne KOD OLARAK girer, cümleye gömülmez.
LINK_METHOD_MISSING = (
    "Ödeme bağlantısı bu ekrandan üretilemiyor: geçitte bağlantı üreten metot "
    f"bulunamadı (store.api → {STANDALONE_METHOD}). Mağaza ucu yayında "
    "(POST /api/admin/bbd/payment-links); eksik olan geçit tarafıdır — metot "
    "yeniden adlandırılmış ya da geçit sürümü eski olabilir. Talep kaydedildi "
    "ve burada bekliyor; metot bağlanınca “Bağlantı üret” kendiliğinden "
    "açılacak. Bugün açık yol: Elden Kapatma sekmesinden havale/nakit beyanı."
)

#: Ödeme uçları canlıda yayında mı — açılışta bir kez yoklanır.
#:
#: BİR DÖNEM ÜÇÜ DE 404'TÜ, ARTIK DEĞİL. 13.08.2026'da `payment-links`,
#: `payments/attempts` ve `payments/terminals` uçlarının üçü de 404 dönüyordu;
#: yoklama bu yüzden eklenmişti. 16.08.2026'da ÜÇÜ DE 200 döndü (liste boş ama
#: uç ayakta). Yoklama KALIYOR: uç bir gün geri çekilirse ya da mağaza kapalıysa
#: personel formu doldurup gerekçe yazıp onayladıktan SONRA hata görmesin (K7).
#:
#: YOL ADI TUZAĞI: link ucunun öneki `payment-links`tir, `payments/links` DEĞİL.
#: Geçit doğru yolu çağırıyor; eski belgelerdeki `payments/links` yazımı canlıda
#: 404 döner ve "uç yok" yanılgısını bu üretmişti.
PAYMENTS_MISSING = (
    "Mağazanın ödeme uçlarına şu an ulaşılamadı (yoklama: "
    "GET /api/admin/bbd/payment-links): bu ekrandan ödeme bağlantısı "
    "ÜRETİLEMEZ ve durum yoklanamaz. Talepler kaydedilir ve bekler; "
    "Elden Kapatma (havale/nakit) çalışmaya devam eder."
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


#: SMS ayarları kartında, kuru prova açıkken ekrana yazılan metin.
#:
#: NEDEN BU KADAR AÇIK. Kimlik bilgisi girilmiş bir kurulumda ekran "SMS
#: hazır" der ve personel mesajın gittiğini sanır; oysa modülün kendi freni
#: (`sms_dry_run`) hâlâ açıktır ve tek bir mesaj çıkmaz. Belirti sebebi ele
#: vermediği için metin ayarın TAM ADINI ve nerede kapatılacağını yazar.
SMS_DRY_RUN_NOTICE = (
    "Bu ekrandan GERÇEK SMS GİTMİYOR: modül ayarı "
    "`modules.store_payment_gateway.sms_dry_run` AÇIK. Mesaj hazırlanır, "
    "parça sayacı çalışır, denetim izine yazılır — ama sağlayıcıya hiç "
    "ulaşmaz. Canlıya geçmek bilinçli bir karardır: ayarı `false` yapın ve "
    "önce `sms_allowlist` içine kendi numaranızı koyup deneyin."
)

#: Netgsm hesabının kantinle ORTAK olduğunu söyleyen metin.
#:
#: Kullanıcı kararı: BBDStore linkle ödeme, bbdkantin ile AYNI Netgsm
#: hesabını taşır. Ayrı hesap açılmadı, ayrı bakiye izlenmiyor. Ekran bunu
#: yazar ki başlık ya da parola değiştiren kişi kantinin SMS'lerini de
#: etkilediğini bilsin.
SHARED_ACCOUNT_NOTICE = (
    "Netgsm hesabı bbdkantin ile ORTAKTIR: buradaki kullanıcı adı, parola ve "
    "başlık kasada `notify.netgsm.*` anahtarlarında durur ve kurulumdaki tüm "
    "SMS gönderimleri aynı hesabı kullanır. Değiştirmek kantin mesajlarını da "
    "etkiler."
)

#: Kasaya yazıldıktan sonra ekrana yazılan uyarı — sessiz bir tuzağın karşılığı.
#:
#: `km_platform/notify` sağlayıcıyı İLK kullanımda kurup bellekte tutuyor.
#: Daha önce SMS göndermiş bir sunucuda yeni parola kasaya yazılsa bile,
#: süreç yeniden başlayana kadar eski kimlik bilgisi kullanılmaya devam eder.
#: Bunu söylememek, "parolayı düzelttim ama hâlâ 30 hatası alıyorum" demektir.
SMS_RELOAD_NOTICE = (
    "Kaydedildi. SMS katmanı sağlayıcıyı ilk kullanımda kurup bellekte "
    "tuttuğu için, bu sunucudan daha önce SMS gönderildiyse yeni bilgiler "
    "ancak uygulama yeniden başlatıldığında geçerli olur."
)


class PreviewError(RuntimeError):
    """Önizleme görüntüsü üretilemedi. Rapor yine de kaydedilmiştir."""


class PaymentGatewayService:
    """Tahsilat ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ."""

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 notify: Any = None, printer: Any = None, secrets: Any = None,
                 category: str = "Mağaza", subcategory: str = "Finans",
                 fallback_dir: Path | None = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._notify = notify
        self._printer = printer
        # Netgsm kimlik bilgisi KASADAN okunur ve kasaya yazılır (K8); modülün
        # `settings` tablosuna asla düşmez. Kasa yoksa ekran çalışır, yalnız
        # SMS ayarları kartı gerekçesiyle kapalı görünür (K7).
        self._secrets = secrets
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
            "standaloneNotice": "" if self._standalone else LINK_METHOD_MISSING,
            "defaultEmail": self._default_email,
            "orgName": self._org_name,
            "pricesIncludeTax": self._prices_include_tax,
            "linkBase": self._link_base,
        }

    async def _payments_probe(self, store_connected: bool) -> tuple[bool, str]:
        """Ödeme uçları yayında mı — TEK ve OKUMA amaçlı bir yoklama.

        NEDEN AÇILIŞTA: geçitte bir METODUN var olması, mağazadaki UCUN ayakta
        olduğunu göstermez. 13.08.2026'da tam bu olmuştu —
        `bbd_create_payment_link` geçitte duruyordu ama uç 404'tü; yalnız metoda
        bakan ekran "her şey hazır" diyor, personel formu dolduruyor, gerekçe
        yazıp onaylıyor ve hatayı ancak o zaman görüyordu.

        16.08.2026'da uç 200 dönüyor; yoklama yine de kalıyor, çünkü sorduğu
        soru tarihe değil ANA aittir: mağaza kapalıysa, belirteç düşmüşse ya da
        uç geri çekilirse cevap yine "hayır" olmalı (K7). Maliyeti bir GET'tir.
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

    # ========================================================= SMS kurulumu
    #
    # Kimlik bilgileri KASADA durur (K8), modülün `settings` tablosunda değil:
    # ayar tablosu düz metindir ve yedeğe, dışa aktarmaya, log'a düşer.
    #
    # ANAHTAR ADLARI `km_platform/notify` İLE AYNI OLMAK ZORUNDA ama oradan
    # import EDİLEMEZ (K2: modül yalnız `km_sdk` import eder). Bu yüzden
    # dizeler burada tekrar yazılır; ad değişirse iki yer birden değişir.

    SECRET_USERNAME = "notify.netgsm.username"
    SECRET_PASSWORD = "notify.netgsm.password"
    SECRET_HEADER = "notify.netgsm.header"

    #: Netgsm'de onaylı gönderici başlığının azami uzunluğu. Sağlayıcının
    #: sınırıdır; aşan başlık kod 40 ile reddedilir.
    HEADER_MAX = 11

    async def sms_settings(self) -> dict[str, Any]:
        """SMS ayarları kartının okuduğu durum. HATA FIRLATMAZ (K7).

        PAROLA GERİ VERİLMEZ — yalnız "kayıtlı mı" bilgisi döner. Kasadan
        okunan bir parolayı ekrana taşımak, onu tarayıcı belleğine, ağ
        günlüğüne ve hata raporuna da taşırdı.
        """
        state = await self._sms_state()
        out: dict[str, Any] = {
            "ok": True, "error": "",
            "available": self._secrets is not None,
            "username": "", "header": "", "passwordConfigured": False,
            "headerMax": self.HEADER_MAX,
            "sms": state,
            "dryRunNotice": SMS_DRY_RUN_NOTICE if self._module_dry_run else "",
            "sharedAccountNotice": SHARED_ACCOUNT_NOTICE,
            # Personelin karşılaşacağı ve DÜZELTEBİLECEĞİ sağlayıcı hataları
            # kartta önden yazılır; hatayı görünce ne yapacağını aramasın.
            "codeHelp": [{"code": code, "text": help_text}
                         for code, help_text in collect.PROVIDER_CODE_HELP.items()],
        }
        if self._secrets is None:
            out["error"] = ("Kasa (secrets) bu kurulumda yok; Netgsm bilgileri bu "
                            "ekrandan girilemez.")
            return out
        try:
            out["username"] = collect.text(await self._secrets.get(self.SECRET_USERNAME))
            out["passwordConfigured"] = bool(await self._secrets.get(self.SECRET_PASSWORD))
            out["header"] = collect.text(await self._secrets.get(self.SECRET_HEADER))
        except Exception as failure:  # noqa: BLE001 — kasa okunamadı, ekran ayakta kalır (K7)
            out["error"] = f"Kasa okunamadı: {self._fail(failure)}"
        return out

    async def save_sms_settings(self, *, username: str, password: str, header: str,
                                reason: str, actor: str) -> dict[str, Any]:
        """Netgsm kullanıcı adı/parola/başlığını KASAYA yazar.

        Parola BOŞ bırakılırsa mevcut değer korunur: ekran parolayı geri
        vermediği için, kaydetmek isteyen personel her seferinde parolayı
        yeniden yazmak zorunda kalmasın. Kayıtlı parola da yokken boş
        gönderilirse kaydetme REDDEDİLİR — yarım kurulum, hiç kurulmamış
        olmaktan daha kötüdür: ekran "hazır" der, mesaj gitmez.
        """
        problem = collect.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        if self._secrets is None:
            return {"ok": False,
                    "error": "Kasa (secrets) bu kurulumda yok; kaydedilemez."}

        user = collect.text(username)
        head = collect.text(header)
        secret = str(password or "").strip()
        if not user:
            return {"ok": False, "error": "Netgsm kullanıcı adı boş bırakılamaz."}
        if not head:
            return {"ok": False,
                    "error": "Gönderici başlığı boş bırakılamaz; başlıksız gönderim "
                             "Netgsm tarafında kod 40 ile reddedilir."}
        if len(head) > self.HEADER_MAX:
            return {"ok": False,
                    "error": f"Gönderici başlığı en çok {self.HEADER_MAX} karakter olabilir."}

        try:
            existing = bool(await self._secrets.get(self.SECRET_PASSWORD))
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": f"Kasa okunamadı: {self._fail(failure)}"}
        if not secret and not existing:
            return {"ok": False,
                    "error": "Netgsm parolası kasada yok; ilk kayıtta parola zorunludur."}

        try:
            await self._secrets.set(self.SECRET_USERNAME, user)
            if secret:
                await self._secrets.set(self.SECRET_PASSWORD, secret)
            await self._secrets.set(self.SECRET_HEADER, head)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": False, "error": f"Kasaya yazılamadı: {self._fail(failure)}"}

        # Denetim izine DEĞER DEĞİL, DEĞİŞENİN ADI yazılır (ADR 0012 + K8):
        # gerekçe ve kimin yazdığı kalır, parola hiçbir yere düşmez.
        await self._record(request_id=0, action="sms_settings", reason=reason, actor=actor,
                           result="ok",
                           detail={"fields": ["username", "header"]
                                             + (["password"] if secret else []),
                                   "header": head})
        settings = await self.sms_settings()
        return {**settings, "ok": True, "notice": SMS_RELOAD_NOTICE}

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
            # SİPARİŞ KİMLİĞİ ARTIK MUAFİYET DEĞİL: siparişe bağlı talep de aynı
            # geçit metodunu kullanıyor (mağaza ucu `orderId` okumaz). Metot
            # yoksa iki yol da kapalıdır ve ekran ikisi için de aynı şeyi söyler.
            "standaloneNotice": "" if self._standalone else LINK_METHOD_MISSING,
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
                "standalone": self._standalone,
                "standaloneNotice": "" if self._standalone else LINK_METHOD_MISSING}

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
        # `warning` GERÇEK ARIZA içindir (mağazaya ulaşılamadı); `attemptsNote`
        # ise NORMAL durumu anlatır. İkisini tek alana koymak, ödemesi henüz
        # yapılmamış HER talepte uyarı kutusu çıkarırdı — oysa o hâl beklenen
        # hâldir ve uyarı gürültüsü gerçek arızayı görünmez yapar.
        attempts_note = ""
        if view["orderId"]:
            # SÜZGEÇ ADI `orderId` — CAMEL CASE. `PaymentAttemptController::applyFilters`
            # yalnız `state · orderId · from · to` okur. `order_id` göndermek hata
            # DEĞİL, SESSİZLİK üretir: Laravel parametreyi yok sayar ve uç
            # SÜZÜLMEMİŞ listeyi döndürür. Canlıda ölçüldü (16.08.2026, salt GET):
            #   ?order_id=999999 → 17 satır (yok sayıldı)
            #   ?orderId=999999  →  0 satır (süzdü)
            # Ekran o 17 satırı "bu talebin POS denemeleri" diye çiziyordu; yani
            # yanlış veri OLMAKLA kalmıyor, BAŞKA müşterilerin maskeli kart
            # numaralarını bu talebin çekmecesinde gösteriyordu.
            try:
                payload = await self._api.bbd_payment_attempts({"orderId": view["orderId"]},
                                                               page=1, per_page=25)
                attempts = [collect.attempt_row(item) for item in (payload.get("items") or [])]
            except Exception as failure:  # noqa: BLE001 — K7
                warning = f"POS denemeleri okunamadı: {self._fail(failure)}"
        elif view["token"]:
            # BELİRTEÇLE DENEME ARANAMAZ: uçta böyle bir süzgeç yok. Süzgeçsiz
            # çağırıp "bunlar bu talebin denemeleri" demek uydurmadır; boş liste
            # ve NEDENİNİ söyleyen bir not doğrusudur.
            attempts_note = ("POS denemeleri sipariş numarasıyla listelenir; bu talepte "
                             "sipariş henüz yok. Müşteri ödemeyi tamamlayıp mağazada "
                             "sipariş oluşunca denemeler burada görünür.")

        return {"ok": True, "error": "", "request": view, "events": events,
                "attempts": attempts, "warning": warning, "attemptsNote": attempts_note,
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

        if not self._standalone:
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"missing": STANDALONE_METHOD})
            return {"ok": False, "error": LINK_METHOD_MISSING, "standalone": False}

        body, problem = self._link_payload(row)
        if problem:
            # Mağazanın reddedeceğini BİLDİĞİMİZ istek gönderilmez: hata
            # personele alan alan söylenir, 422'nin ardından değil (K9).
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"payload": problem})
            return {"ok": False, "error": problem}

        problem = await self._retire_previous(request_id, view, reason=reason, actor=actor,
                                              dry_run=dry_run)
        if problem:
            return {"ok": False, "error": problem}

        try:
            # SİPARİŞ KİMLİĞİ GÖVDEYE KONMAZ. İki bağımsız sebep var: mağaza ucu
            # `orderId` alanını hiç okumaz (`PaymentLinkController::store`
            # gövdeden yalnız kind·amount·items·billing·description alır) ve
            # geçit `order_id` verilen çağrıyı doğrudan reddeder. Talebin
            # siparişi YEREL satırda durmaya devam eder; mağaza tarafındaki bağ
            # müşteri ödemeyi tamamlayınca kurulur ve bize `poll()` ile
            # `link_row["orderId"]` üzerinden geri gelir.
            result = await self._api.bbd_create_payment_link(
                **body, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": self._fail(failure)}

        link = collect.link_row(result.get("data") if isinstance(result.get("data"), dict)
                               else result)
        dry = bool(result.get("dryRun", dry_run))
        fields: dict[str, Any] = {"reason": reason, "actor": actor}
        # KURU PROVADA BELİRTEÇ/ADRES YAZILMAZ. Mağaza provada zaten link
        # üretmiyor (işlemi geri sarıyor), ama yanıtta bir şey gelirse onu yerel
        # satıra yazmak asıl tehlikeyi doğururdu: `send_sms` yalnız `link`
        # alanının dolu olmasına bakıyor ve müşteriye HİÇ VAR OLMAYAN bir ödeme
        # adresi giderdi.
        if not dry:
            if link["token"]:
                fields["token"] = link["token"]
            if link["url"]:
                fields["link"] = link["url"]
            if link["orderId"]:
                fields["order_id"] = link["orderId"]
            # Sayısal kimlik yerel satıra YAZILIR: yoklama ve iptal uçlarının
            # istediği anahtar budur (gerekçesi `collect.link_row` içinde).
            fields["link_id"] = link["id"]
            if link["token"] or link["url"]:
                fields["status"] = collect.LINKED
        await self._update(request_id, **fields)
        await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                           result="dry_run" if dry else "ok",
                           detail={"token": link["token"], "url": link["url"]})

        out: dict[str, Any] = {"ok": True, "error": "", "dryRun": dry, "link": link,
                               "standalone": self._standalone}
        if dry:
            # "İstek gönderilmedi" değil, "HİÇBİR ŞEY YAZILMADI": mağaza kuru
            # provada sepeti gerçekten kurup toplamı hesaplar ve işlemi geri
            # sarar (`PaymentLinkService::create` → `DB::rollBack`). Ekrana
            # yazılan cümle bu yüzden linke odaklanır: link üretilmedi.
            out["notice"] = ("Kuru prova: bağlantı ÜRETİLMEDİ, mağazada hiçbir kayıt "
                             "oluşmadı. Gerçekten üretmek için kuru provayı kapatın.")
            return out
        if not link["token"] and not link["url"]:
            out["notice"] = ("Mağaza bağlantı bilgisi döndürmedi; talep bekliyor olarak "
                             "kaldı. Yoklama ile durumu izleyin.")
            return out
        if send_sms:
            out["sms"] = await self.send_sms(request_id, reason=reason, actor=actor,
                                             dry_run=dry_run)
        return out

    async def _retire_previous(self, request_id: int, view: dict[str, Any], *, reason: str,
                               actor: str, dry_run: bool) -> str:
        """Yenisini üretmeden ÖNCE eldeki bağlantıyı mağazada kapatır.

        Döner: engel metni; boşsa yola devam edilir.

        İKİ ÖDENEBİLİR LİNK AYNI BORÇ İÇİN DURAMAZ. Eski davranışta "Yeni
        bağlantı üret" yerel satırdaki `token`/`link` alanlarını üzerine yazıyor,
        mağazadaki eski kaydı ise ELLEMİYORDU. `PaymentLinkService::persistLink`
        yalnız INSERT yapar; eski link `expires_at` dolana kadar (varsayılan 48
        saat) ödenebilir kalır. Sonuç zinciri ölçüldü:

          · Müşteri elindeki İLK SMS'i öderse, o belirteç artık yerel satırda
            yok — yoklama onu hiç aramaz. Talep sonsuza kadar "SMS gönderildi"
            görünür ve personel parayı İKİNCİ KEZ ister.
          · İkinci linki de öderse aynı borç iki kez tahsil edilmiş olur.

        İPTAL BAŞARISIZSA YENİ LİNK ÜRETİLMEZ. "Kapatamadım ama yenisini
        ürettim" tam olarak yukarıdaki iki-link durumudur; doğru cevap
        personele NEDEN kapatılamadığını söyleyip durmaktır (K7).

        BU AYNI ZAMANDA İKİNCİ BİR ÇİFT ÇEKİM AĞIDIR. Yerel durum eskimişse
        (bizde `linked`, mağazada ÖDENMİŞ) `can_relink` kapısı geçilir ama
        mağaza iptali 409 `LINK_ALREADY_PAID` ile reddeder — ve o ret burada
        yeni link üretimini durdurur. Yani "yereli yoklamadan ikinci link"
        yolu da kapanmış olur; personel ret metninden ödemenin alındığını
        öğrenir.

        KURU PROVADA da aynı yol yürünür (iptal de provaya girer): prova
        gerçeğin provası olmalı, daha kısa bir yolun provası değil.
        """
        if not view["linkId"]:
            # Kapatılacak bir bağlantı yok (ilk üretim) ya da kayıt sayısal
            # kimlik sütunundan önce üretilmiş. İkinci hâlde iptal çağrısı
            # zaten yapılamaz; `cancel()` bunu ayrıca söylüyor.
            return ""
        try:
            await self._api.bbd_cancel_payment_link(view["linkId"], reason=reason, actor=actor,
                                                    dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                               result="hata",
                               detail={"retire": view["linkId"], "error": str(failure)})
            return ("Önceki bağlantı mağazada kapatılamadı, bu yüzden yenisi ÜRETİLMEDİ: "
                    f"{self._fail(failure)} — iki ödenebilir bağlantı aynı anda durursa "
                    "müşteriden iki kez tahsilat yapılabilir. Önceki bağlantının durumunu "
                    "“Durumu yokla” ile kontrol edin.")
        # Eski belirteç ÜZERİNE YAZILMADAN ÖNCE olay zincirine düşer: talep
        # satırı değişse de "hangi bağlantı vardı" sorusu cevapsız kalmaz
        # (olaylar silinmez).
        await self._record(request_id=request_id, action="link", reason=reason, actor=actor,
                           result="dry_run" if dry_run else "ok",
                           detail={"retired": {"linkId": view["linkId"],
                                               "token": view["token"], "url": view["link"]}})
        return ""

    def _link_payload(self, row: Any) -> tuple[dict[str, Any], str]:
        """Yerel talepten MAĞAZANIN sözleşmesine göre çağrı gövdesi.

        Döner: `(gövde, engel)`. `engel` doluysa mağazaya istek GÖNDERİLMEZ ve
        metin doğrudan ekrana yazılır.

        SÖZLEŞME BİZİM İCADIMIZ DEĞİL. `PaymentLinkController::store` gövdeden
        yalnız `kind` · `amount` · `items` · `billing` · `description`
        alanlarını okur; başka her alan sessizce düşer (eski gövdedeki `code`,
        `customer`, `note` alanlarının başına gelen buydu).

        DÖRT KARAR VE GEREKÇELERİ:

        1. TUTAR ONDALIK METİNDİR ("1250.00"), kuruş tam sayısı DEĞİL. Mağaza
           `amount`ı TL sanıp sepetin hesapladığı toplamla kuruş kuruş
           karşılaştırıyor (`AmountGate::matches`); 125.000 kuruşu olduğu gibi
           göndermek 1.250,00 TL'lik bir tahsilat için 125.000,00 TL isteyen bir
           istek demekti — ki mağaza onu 422 AMOUNT_DRIFT ile reddeder. Çevrim
           `collect.from_kurus` içinde, TEK YERDE ve `Decimal` ile yapılır.

        2. TÜR, KALEMLERİN KENDİSİNDEN GELİR. Ürün seçilmişse `product`, yalnız
           serbest tutar varsa `custom`. Mağaza `custom`ta `items`i, `product`ta
           `amount`ı REDDEDER; ikisini birden göndermek karma sepet demektir ve
           mağaza karma sepeti bilerek yasaklamıştır ("hangi tutar geçerli"
           sorusunun cevabı yoktur).

           ÜRÜNDE GEÇERLİ FİYAT MAĞAZANINKİDİR. `product` türünde tutar kapısı
           YOKTUR: müşteriden ürünün o anki fiyatı (+ mağazanın kendi vergisi)
           tahsil edilir, bizim ekranımızda görünen kırılım değil. Fark
           çıkarsa mağaza yanıtındaki `snapshot`/`amount` alanında görünür ve
           `poll()` ile ekrana döner.

        3. KARMA TALEP MAĞAZAYA HİÇ GİTMEZ. Serbest tutar ile ürün aynı talepte
           varsa burada durdurulur. Ürünleri gönderip serbest tutarı düşürmek
           EKSİK TAHSİLAT olurdu; serbest tutarı gönderip ürünleri düşürmek de
           müşteriye neyin satıldığını kaybettirirdi.

        4. FATURA ADRESİ ZORUNLUDUR ve alan adları ÇAPRAZDIR:
              yerel `city` (İL)   → mağaza `state`
              yerel `district`    → mağaza `city`
           Bu tahmin değil, koddan okundu: `PaymentLinkService::validateBilling`
           `state` alanını `TurkishProvinces::codeFor` ile 81 ilin listesine
           karşı doğruluyor (bulamazsa "il tanınmadı" diye reddediyor), `city`
           alanına ise dokunmuyor ve onu bankanın `BillAddrCity` alanına
           geçiriyor. Yani mağazanın "city"si bizim ilçemizdir.
        """
        data = dict(row or {})
        try:
            lines = json.loads(collect.text(data.get("items")) or "[]")
        except ValueError:
            lines = None
        if not isinstance(lines, list):
            # Kalem listesi okunamadı: BOŞ SAYILMAZ. Boş saymak ürünlü bir talebi
            # sessizce serbest tutara çevirirdi — müşteriye ürünsüz, mağazaya
            # kalemsiz bir tahsilat gitmesi demek. Ekran bozukluğu söyler (K7:
            # hata ekranı düşürmez, anlaşılır metne çevrilir).
            return {}, ("Talebin kalem listesi okunamadı (kayıt bozuk görünüyor); bağlantı "
                        "üretilmedi. Talebi yeniden açın.")
        products = [line for line in lines
                    if isinstance(line, dict) and collect.as_int(line.get("productId"))]
        free = [line for line in lines
                if isinstance(line, dict) and not collect.as_int(line.get("productId"))]

        billing, missing = self._billing(data)
        if missing:
            return {}, (
                f"Fatura bilgisi eksik: {', '.join(missing)}. Mağaza bu alanları ZORUNLU "
                "tutuyor (banka 3D doğrulamasında kart sahibi bloğunu istiyor) ve eksikken "
                "bağlantıyı üretmez. Talebi eksiksiz bilgiyle yeniden açın."
            )
        if products and free:
            return {}, (
                "Bu talepte hem ürün hem serbest tutar var; mağaza karma sepetle bağlantı "
                "üretmiyor. Ürünleri ve serbest tutarı AYRI taleplere bölün — birini sessizce "
                "düşürmek eksik tahsilat olurdu."
            )

        # Açıklama mağazada 250 karakterde kesiliyor; kesmeyi burada yapıyoruz ki
        # denetim izine yazdığımız metin ile mağazada duran metin aynı olsun.
        # Talep numarası BAŞA yazılır: gövdede `orderId` gitmediği için mağazadaki
        # satırı yerel talebe bağlayan tek iz budur.
        code = collect.text(data.get("code"))
        note = collect.text(data.get("note"))
        description = f"{code} · {note}" if code and note else (code or note)

        body: dict[str, Any] = {"billing": billing, "description": description[:250]}
        if products:
            body["kind"] = "product"
            # Mağaza fiyatı ÜRÜNÜN KENDİSİNDEN okur; birim fiyat gönderilmez.
            body["items"] = [{"productId": collect.as_int(line.get("productId")),
                              "quantity": max(1, collect.as_int(line.get("quantity"), 1))}
                             for line in products]
        else:
            body["kind"] = "custom"
            body["amount"] = collect.from_kurus(collect.as_int(data.get("gross")))
        return body, ""

    def _billing(self, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """Fatura adresi bloğu ve DOLDURULMAMIŞ alanların adları.

        YALNIZ BOŞLUK DENETLENİR, il listesi burada TUTULMAZ. Mağaza ili kendi
        `TurkishProvinces` listesiyle doğruluyor; aynı listenin bir kopyasını
        burada tutmak, iki listenin zamanla ayrışması demekti (mağaza kodu da
        tam bu gerekçeyle tek kaynak kullanıyor). Boşluk denetimi ise ayrışmaz:
        boş alan her sürümde reddedilir ve personele 422 beklemeden söylenir.

        Ad/soyad ayrımı `collect.split_name` içindedir (gerekçesi orada).
        """
        first, last = collect.split_name(data.get("full_name"))
        address = collect.text(data.get("address"))
        province = collect.text(data.get("city"))          # yerel "İl"
        district = collect.text(data.get("district"))      # yerel "İlçe"
        phone = collect.normal_phone(data.get("phone"))
        email = collect.text(data.get("email")) or self._default_email

        missing = [label for label, value in (
            ("ad", first), ("soyad", last), ("adres", address),
            ("il", province), ("ilçe", district), ("cep telefonu", phone),
        ) if not value]

        return {
            "firstName": first,
            "lastName": last,
            "email": email,
            # Numara yerel kayıttaki normalleştirilmiş biçimiyle (5XXXXXXXXX)
            # gider; ülke kodunu mağaza `PhoneNumber::parse` ile kendisi ekler
            # ve bankaya `Cc`/`Subscriber` diye AYRI alanlarda yazar. Burada
            # önek eklemek, tek parça beklenen bir alanı ikinci kez biçimlemek
            # olurdu.
            "phone": phone,
            # Adres satırı DİZİ gönderilir: mağaza çok satırlı adresi dizi
            # bekliyor ve metni de kabul ediyor; dizi vermek satır sonlarının
            # tek satıra ezilmesini önler.
            "address": [address] if address else [],
            "city": district,        # mağazanın `city`si İLÇEDİR (bkz. `_link_payload`)
            "state": province,       # il — mağaza bunu 81 ilin listesiyle doğrular
            "country": "TR",         # mağaza yalnız TRY tahsil ediyor
        }, missing

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

        # İPTAL SAYISAL KİMLİKLE ÇAĞRILIR, `token` İLE DEĞİL. Rota
        # `->whereNumber('id')` ile daraltılmış; `token` (= mağazanın `code`u)
        # 12 haneli harf-rakam karışımı bir dizedir ve geçit onu `key.isdigit()`
        # denetiminde reddeder. Eskiden çağrıya `token` gidiyordu: ekran
        # düşmüyordu (K7) ama "İptal" HER SEFERİNDE ret alıyordu — yanlış giden
        # bir bağlantıyı kapatmanın yolu yoktu.
        link_id = view["linkId"]
        await self._record(request_id=request_id, action="cancel", reason=reason, actor=actor,
                           result="denendi", detail={"linkId": link_id, "token": view["token"]})
        dry = dry_run
        if link_id:
            try:
                result = await self._api.bbd_cancel_payment_link(link_id, reason=reason,
                                                                 actor=actor, dry_run=dry_run)
                dry = bool(result.get("dryRun", dry_run))
            except Exception as failure:  # noqa: BLE001 — K7
                await self._record(request_id=request_id, action="cancel", reason=reason,
                                   actor=actor, result="hata", detail={"error": str(failure)})
                return {"ok": False, "error": self._fail(failure)}
        elif view["token"]:
            # Belirteç var ama sayısal kimlik yok: bu satır kimlik sütunundan
            # ÖNCE üretilmiş. Yerel satırı "iptal edildi" yapmak, mağazada
            # ödenebilir duran bir bağlantıyı kapatıldı sanmaktır — söylenmez.
            return {"ok": False,
                    "error": ("Bu bağlantının mağazadaki sayısal kimliği yerel kayıtta yok "
                              "(kayıt bu alan eklenmeden önce üretilmiş), bu yüzden mağaza "
                              "tarafında iptal edilemiyor. Önce “Durumu yokla” deyin: kimlik "
                              "bulunursa satıra yazılır ve iptal açılır.")}

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
            # Sağlayıcı kodunun AÇIK METNİ hata satırının yanına konur. Ham
            # "[40] Gönderici başlığı sistemde tanımlı değil" cümlesi doğrudur
            # ama personele ne yapacağını söylemez; `provider_hint` onu söyler.
            hint = collect.provider_hint(failure)
            await self._update(request_id, sms_state="error", sms_at=collect.now_iso())
            await self._record(request_id=request_id, action="sms", reason=reason, actor=actor,
                               result="hata", detail={"error": str(failure)})
            return {"ok": False, "error": f"SMS gönderilemedi: {self._fail(failure)}",
                    "hint": hint, "plan": plan}

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
        # YOKLAMA BAĞLANTIYA BAKAR, SİPARİŞE DEĞİL. Sipariş kimliği link
        # listesinde ARANABİLİR BİR ŞEY DEĞİL (uç `order_id` süzgeci tanımıyor);
        # elde yalnız sipariş no varken mağazaya gitmek, "bulamadım" yerine
        # "başkasının linkini buldum" ile dönme riskidir.
        if not view["linkId"] and not view["token"]:
            return {"ok": True, "connected": True, "error": "", "changed": False,
                    "request": view,
                    "notice": "Bu talebin mağazada karşılığı yok; yoklanacak bir şey yok."}

        try:
            match = await self._find_link(view)
        except Exception as failure:  # noqa: BLE001 — K7
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "changed": False, "request": view}

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
        if match["id"] and not view["linkId"]:
            # Arama yoluyla bulunduysa sayısal kimlik YEREL SATIRA YAZILIR:
            # sonraki yoklama listeye hiç uğramaz, tekil ucu çağırır.
            fields["link_id"] = match["id"]
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

    async def _find_link(self, view: dict[str, Any]) -> dict[str, Any] | None:
        """Bu talebin MAĞAZADAKİ bağlantısı — bulunamazsa `None`.

        "BULAMADIM" İLE "BAŞKASINI BULDUM" AYNI ŞEY DEĞİLDİR. Bu metodun tek
        işi bu ayrımı korumak; eskiden korumuyordu ve bedeli ölçüldü: ödenmemiş
        bir talep, mağazadaki BAŞKA bir müşterinin ödenmiş linkine bakıp
        "Ödendi" oluyor, üstüne o linkin `orderId`/`invoiceId` alanları yerel
        satıra yazılıyordu. Personel hem takibi bırakıyor hem — `paid` yeniden
        link üretimini kilitlediği için — yeni bağlantı üretemiyordu.

        HATANIN İKİ AYRI KAYNAĞI VARDI, İKİSİ DE KAPATILDI:

        1. GÖNDERİLEN SÜZGEÇ OKUNMUYORDU. `PaymentLinkController::index`
           yalnız `status` ve `q` okur; `token`/`order_id` diye bir süzgeç
           YOKTUR. Laravel tanımadığı sorgu parametresini sessizce yok sayar —
           yani istek hata vermez, "en yeni 50 link"i döndürür
           (`orderByDesc('id')`). Canlıda ölçüldü (16.08.2026, salt GET):
           `payments/attempts?order_id=999999` → 17 satır (süzgeç yok sayıldı),
           `?orderId=999999` → 0 satır. Aynı sessizlik link listesinde de var.

        2. EŞLEŞME YOKKEN İLK SATIR ALINIYORDU (`match = rows[0]`). Süzgeç
           çalışsa bile 50'lik sayfa taşınca aynı yere çıkardı.

        SIRA — kesinden belirsize:
          · `linkId` varsa TEKİL UÇ (`GET /payment-links/{id}`). Ya istenen
            kaydı verir ya 404; üçüncü ihtimal yoktur.
          · yoksa `q` ile ARAMA: uç `q`yu `code` üzerinde de LIKE ile arıyor.
            Dönen satırlar yine DOĞRULANIR — LIKE başka kodlara da uyabilir ve
            arama yalnız ADAY üretir, karar üretmez.

        TEKİL UÇ GEÇİTTE YOKSA arama yoluna düşülür (geçit sürümü geride
        kalabilir; `_standalone` yoklamasıyla aynı gerekçe). Bu GÜVENLİ bir
        geri çekilmedir: arama yolu daha zayıftır ama yanlış satırı asla kabul
        etmez — belirteç birebir doğrulanıyor.
        """
        if view["linkId"] and callable(getattr(self._api, "bbd_payment_link", None)):
            try:
                payload = await self._api.bbd_payment_link(view["linkId"])
            except Exception as failure:   # 404 ile kopukluk AYRI şeyler (aşağıda)
                # "KAYIT YOK" İLE "ULAŞAMADIM" AYNI CEVAP DEĞİLDİR ve fark
                # paranın doğruluğunu belirler: ilkinde bağlantı mağazada
                # gerçekten yoktur (durum bilinmiyor, çift çekim uyarısı
                # verilir), ikincisinde YEREL DURUM KORUNUR — ağ koptu diye
                # "Ödendi"yi silmek ya da "bilinmiyor" yazmak veriyi bozardı.
                # Geçit ayrımı zaten yapıyor (`code="not_found"`); sınıfı
                # import etmeden alan adına bakılır (K3).
                if getattr(failure, "code", "") == "not_found":
                    return None
                raise
            found = collect.link_row(payload)
            # Tekil uç istenen kaydı döndürür; yine de kimliği doğrulanır:
            # yanlış kaydı sessizce kabul etmektense "bilmiyorum" demek doğrudur.
            return found if found["id"] == view["linkId"] else None

        if not view["token"]:
            # Arayacak bir kod yok. Boş `q` ile istek atmak uca "hepsini ver"
            # demektir ve sonuç yine eşleşmezdi; istek hiç gönderilmez.
            return None
        payload = await self._api.bbd_payment_links({"q": view["token"]})
        rows = [collect.link_row(item) for item in (payload.get("items") or [])]
        # LIKE ARAMASI ADAY ÜRETİR, KARAR ÜRETMEZ: belirteç birebir doğrulanır.
        return next((item for item in rows
                     if item["token"] and item["token"] == view["token"]), None)

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
                # `defaultBody` EKRANIN GERİ DÖNÜŞ YOLU: personel şablonu
                # bozduğunda hazır metni elle yeniden yazmak zorunda kalmasın.
                # Sunucudan gelir, panelde kopyası tutulmaz — iki yerde duran
                # bir varsayılan er geç birbirinden ayrılır.
                "defaultBody": collect.DEFAULT_TEMPLATE,
                "required": collect.REQUIRED_PLACEHOLDER,
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
        if collect.REQUIRED_PLACEHOLDER not in text_body:
            return {"ok": False, "error": collect.LINK_REQUIRED_ERROR}
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
