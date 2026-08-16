"""Sipariş Yönetimi — iş kuralları.

VERİ BLD'DEDİR, KARAR SUNUCUDADIR, EKRAN BURADADIR. Sipariş, revizyon, ödeme
ve fatura kaydı BLD sunucusunda durur ve buraya `bld.api` geçidinden gelir
(K4); bu modül ham `httpx` kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel
tablolar yalnız BLD'de karşılığı olmayan iki şeyi saklar: yazma gerekçesinin
denetim izi ve bu ekrana özel görüntüleme tercihleri.

Neden kopya tutulmuyor: sipariş durumu dakikalar içinde değişiyor, mutfak
kasası da aynı kayda yazıyor. Yerel bir kopya her zaman bir tur geride olur ve
"hazırlanıyor" görünen bir sipariş çoktan teslim edilmiş olabilir. Yönetici
buna bakarak müşteriye yanlış bilgi verir.

DURUM MAKİNESİ YENİDEN YAZILMADI. Geçiş matrisi ve 120 saniyelik geri alma
penceresi `OrderStatusTransition`'da, yani sunucudadır. Bu servis geçişi
denemez, ön denetimini yapmaz ve pencereyi hesaplamaz; sunucunun cevabını
Türkçeleştirir ve `can_undo` alanını olduğu gibi yüzeye çıkarır
(`orders.undo_view`). Gerekçesi `orders.py` başlığında yazılı.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar. `ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır:
ayrımı `connected` taşır ve panelin onu OKUMASI gerekir — yalnız `ok`a bakan
bir ekran, geçit düştüğünde "sipariş yok" der ve K7'nin engellemek için var
olduğu yalanı söyler.

YAZMA ZİNCİRİ — üç yazma ucunun üçü de bu beş adımı bu sırayla uygular:

    1. gerekçe denetimi (10–160; arayüzde zorunlu göstermek yetmez, K9)
    2. TAZE OKUMA (sipariş aradan mutfaktan değişmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run=` HER ZAMAN AÇIKÇA verilir
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: iptal isteği gönderilirken ağ koparsa siparişin iptal
edilip edilmediği bilinmez; iz olmasa kimin denediği de bilinmezdi. Yıkıcı
işlemin ÇİFT SATIRI budur (ADR 0012) — "ne yapmaya çalıştık" ve "ne oldu"
ayrı iki kayıttır.

HER YAZMADA AÇIK `dry_run=`. Geçidin varsayılanına güvenilmez: `config/
local.yaml` git dışıdır ve orada `dry_run_default: true` yazıyor olabilir;
bayrağı atlayan bir çağrı hiçbir şey yazmadan `{"ok": true}` alır ve ekran
"kaydedildi" der (`bld_api/README.md`).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from km_sdk import report_dir, write_private

from . import orders as od

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Durum bu ekrandan değiştiğinde yayınlanan olay (manifest).
#: `bld_kds.order_status_changed` ADI ALINMIŞ (`bld_kds/backend/service.py`) ve
#: aynı adı ikinci kez yayınlamak, dinleyicinin olayın hangi ekrandan geldiğini
#: ayırt edememesi olurdu.
STATUS_EVENT = "bld_orders.order_status_changed"

#: İptal AYRI BİR OLAYDIR, `status_changed` ile `to="iptal"` değil: iptal para
#: hareketi (iade) ve stok serbest bırakma üretir. Dinleyicinin ikisini
#: gövdeye bakarak ayırması gerekseydi, iadeyi kaçıran bir dinleyici sessizce
#: yanlış çalışırdı.
CANCELLED_EVENT = "bld_orders.order_cancelled"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç yüzeyidir
#: ve `store.order.status_changed` bu biçimi kurdu. HTTP yanıtları ise
#: sözleşmenin snake_case sözlüğünü korur.

#: Dosya adında kabul edilen karakterler. Sunucunun verdiği ad DOĞRUDAN yola
#: KONMAZ: `Content-Disposition` uzaktan gelen bir dizedir ve içindeki `/` ya
#: da `..` dosyayı rapor klasörünün dışına yazdırırdı.
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class OrdersService:
    """Sipariş Yönetimi ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None, fallback_dir: Path | None = None,
                 category: str = "BLD", subcategory: str = "Siparişler") -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish
        self._category = category
        self._subcategory = subcategory
        self._fallback = fallback_dir or Path.home() / "km-raporlar"

        self._audit = store.table("audit")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _dry_run_default(self) -> bool:
        """İstemci `dryRun` alanını HİÇ göndermezse geçerli olan varsayılan.

        VARSAYILAN KAPALI. Açık bırakmak, panelden yapılan her yazmayı sessizce
        provaya çevirmek olurdu: ekran "yazıldı" der, sipariş değişmez ve fark
        ancak müşteri aradığında anlaşılır. Yedek değer de `False` — ayar
        dosyası okunamadığında sessiz bir prova, açık bir hatadan pahalıdır.
        """
        return bool(self._config.get("dry_run_default", False))

    @property
    def _page_size(self) -> int:
        """Sayfa boyutu varsayılanı. Tavan 100 (`00-genel.md` §5)."""
        return max(5, min(100, od.as_int(self._config.get("page_size"), 25)))

    @property
    def _default_range_days(self) -> int:
        """Süzgeç verilmediğinde panelin açtığı aralık.

        Sunucu süzgeçsiz istekte SON 7 GÜNÜ döndürüyor (sözleşme). Panelin
        açılışta aynı aralığı göstermesi, "listede neden 137 kayıt var"
        sorusunu ekranda cevaplar; sunucunun varsayılanını sessizce miras
        almak, aralığı hiçbir yerde yazmayan bir liste demekti.
        """
        return max(1, min(90, od.as_int(self._config.get("default_range_days"), 7)))

    @property
    def _export_max_rows(self) -> int:
        """Dışa aktarım satır tavanı. Sunucu tavanı 20000 (sözleşme)."""
        return max(100, min(20000, od.as_int(self._config.get("export_max_rows"), 5000)))

    @property
    def _export_dir(self) -> Path:
        # HER ÇAĞRIDA yeniden çözülür: ay değişince klasör kendiliğinden değişir.
        return report_dir(self._category, subcategory=self._subcategory,
                          fallback=self._fallback,
                          configured=str(self._config.get("export_path") or ""))

    def _dry(self, dry_run: bool | None) -> bool:
        return self._dry_run_default if dry_run is None else bool(dry_run)

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _fail(failure: Exception) -> str:
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin hata kodu. `BldApiError` import EDİLMEZ (K2/K3): başka bir
        modülün sınıfına bağlanmak, o modül yüklenmediğinde bu modülü de
        düşürürdü. Kod bir dizedir ve `getattr` ile okunur."""
        return str(getattr(failure, "code", "") or "")

    async def _record(self, *, action: str, reason: str, actor: str, result: str,
                      target_id: int = 0, detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN isteği bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_id, action, reason, actor, result, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("order", int(target_id or 0), action, od.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), od.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: sipariş BLD'de değişmiştir,
        dinleyicinin patlaması onu geri almaz (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit sayfalı uçları `{"items": [...], "meta": {...}}` biçiminde
        açıyor; `data` ve düz dizi de kabul edilir çünkü tek bir ada bağlanmak,
        ad tutmadığında ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    @staticmethod
    def _meta(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("meta"), dict):
            return dict(payload["meta"])
        return {}

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Tekil yanıttan kaydı çıkarır (`{"data": {...}}` ya da düz sözlük)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _server_time(payload: Any) -> str:
        """Yanıttaki `server_time`. Geri sayımların tabanı budur — istemcinin
        saati kaymış olabilir (`00-genel.md` §6)."""
        if isinstance(payload, dict):
            return od.text(payload.get("server_time"))
        return ""

    def _filters(self, *, service_date: str = "", date_from: str = "", date_to: str = "",
                 status: Any = None, delivery_type: str = "", customer_id: int = 0,
                 subscription_id: int = 0, source: str = "",
                 q: str = "") -> tuple[dict[str, Any], str]:
        """Süzgeçleri geçidin beklediği biçime çevirir. `(kwargs, hata)`.

        Denetim BURADA yapılır ki hem liste hem dışa aktarım aynı kuralı
        paylaşsın: iki yerde yazılmış bir süzgeç denetimi, CSV'nin ekrandakinden
        başka bir küme üretmesi demekti.
        """
        for value, field in ((service_date, "Servis günü"), (date_from, "Başlangıç"),
                             (date_to, "Bitiş")):
            problem = od.date_error(value, field=field)
            if problem:
                return {}, problem

        codes, problem = od.status_filter(status)
        if problem:
            return {}, problem

        problem = od.choice_error(delivery_type, od.DELIVERY_TYPES, field="Teslimat türü")
        if problem:
            return {}, problem
        problem = od.choice_error(source, od.SOURCES, field="Kaynak")
        if problem:
            return {}, problem

        if od.text(date_from) and od.text(date_to) and od.text(date_from) > od.text(date_to):
            return {}, "Başlangıç günü bitiş gününden sonra olamaz."

        return {
            "service_date": od.text(service_date),
            "date_from": od.text(date_from),
            "date_to": od.text(date_to),
            # Geçit listeyi kendisi virgülleyecek (`_csv`); boş liste `None`
            # olarak gider ve sorgu dizesinden tümüyle düşer.
            "status": codes or None,
            "delivery_type": od.text(delivery_type),
            "customer_id": int(customer_id) or None,
            "subscription_id": int(subscription_id) or None,
            "source": od.text(source),
            "q": od.text(q),
        }, ""

    # ================================================================= okuma

    async def overview(self) -> dict[str, Any]:
        """Panel açılışı — SÖZLEŞME VE TERCİH, ağa çıkmaz.

        Sipariş alanında bir "özet" ucu YOKTUR (`orders.md` sekiz ucu sayıyor);
        uydurulmadı. Ekranın açılışta ihtiyacı olan şey zaten sunucuda değil:
        durum kodları, ödeme sözlüğü, gerekçe sınırları ve kullanıcının kendi
        tercihleri. Bunları ağa çıkmadan vermek, geçit düşükken bile süzgeç
        şeridinin çizilebilmesi demektir (K7).
        """
        return {
            "ok": True,
            "connected": None,   # bu uç ağa çıkmaz; bağlantı listeden anlaşılır
            "error": "",
            "contract": od.screen_contract(),
            "prefs": await self.prefs(),
            "limits": {
                "page_size": self._page_size,
                "default_range_days": self._default_range_days,
                "export_max_rows": self._export_max_rows,
            },
        }

    async def orders(self, *, page: int = 1, per_page: int = 0,
                     **filters: Any) -> dict[str, Any]:
        """Sipariş listesi — SAYFALI, GEÇMİŞE BAKAR.

        `bld_kds`in `/orders` ucuyla karıştırılmamalı: o BUGÜNÜN aktif
        siparişlerini verir ve mutfak panosuyla aynı kümedir. Bu uç süzülür,
        sayfalanır ve fiyat taşır (ADR-08 mutfak kapsamını men ediyor, yönetim
        yüzeyini değil).
        """
        clean, problem = self._filters(**filters)
        if problem:
            # Süzgeç hatası bir BAĞLANTI sorunu değildir: `connected` bilinmiyor
            # ve liste boş. Ekran ikisini karıştırmasın diye `ok: False`.
            return {"ok": False, "connected": None, "error": problem,
                    "items": [], "meta": {}}

        size = max(5, min(100, int(per_page) or self._page_size))
        try:
            payload = await self._api.order_list(page=max(1, int(page)), per_page=size,
                                                 **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş listesi okunamadı", error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "meta": {}}

        rows = [od.order_row(raw) for raw in self._items(payload)]
        meta = self._meta(payload)
        return {
            "ok": True, "connected": True, "error": "", "items": rows,
            "meta": {**meta, "page": od.as_int(meta.get("page"), max(1, int(page))),
                     "per_page": od.as_int(meta.get("per_page"), size)},
            "server_time": self._server_time(payload),
            # Sayaçlar YALNIZ BU SAYFANIN kayıtlarını sayar. Toplamı vermek
            # ikinci bir istek ederdi ve "sayfadaki 25 kaydın 4'ü yeni" ile
            # "137 siparişin 4'ü yeni" karıştırılırsa yönetici yanlış karar
            # verir; panel bu ayrımı yazıyla söyler.
            "page_counts": self._counts(rows),
        }

    @staticmethod
    def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
        counts = dict.fromkeys(od.STATUS_CODES, 0)
        for row in rows:
            code = od.text(row.get("status"))
            if code in counts:
                counts[code] += 1
        return counts

    async def order(self, order_id: int) -> dict[str, Any]:
        """Tek sipariş — düzenlenebilir görünüm + geri alma penceresi.

        BİLEŞEN SATIRLARI LİSTEDE GÖRÜNMEZ (sözleşme B-19): günün menüsü bir
        paket satırı + sıfır fiyatlı bileşen satırları olarak yazılıyor.
        Personel paketi TEK BİRİM olarak düzenler, sunucu bileşenleri yeniden
        açar. Buradan geçen liste ne eklenir ne ayıklanır.
        """
        try:
            payload = await self._api.order_detail(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("sipariş okunamadı", orderId=order_id, error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "order": {}, "undo": {}}

        raw = self._record_of(payload, "data", "order")
        if not raw:
            return {"ok": False, "connected": True, "error": "Sipariş kaydı boş döndü.",
                    "order": {}, "undo": {}}

        stamp = self._server_time(payload) or od.now_iso()
        return {"ok": True, "connected": True, "error": "",
                "order": od.editable_view(raw),
                "undo": od.undo_view(raw, server_time=stamp),
                "server_time": stamp}

    async def order_revisions(self, order_id: int) -> dict[str, Any]:
        """Revizyon geçmişi — "ne değişti, kim değiştirdi" sorusunun cevabı."""
        try:
            payload = await self._api.order_revision_history(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("revizyon geçmişi okunamadı", orderId=order_id,
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure), "items": [], "order_id": int(order_id)}
        return {"ok": True, "connected": True, "error": "",
                "items": [od.revision_row(raw) for raw in self._items(payload)],
                "order_id": int(order_id)}

    async def order_invoice(self, order_id: int) -> dict[str, Any]:
        """Siparişin fatura BELGESİ — yoksa ÜRETMEZ (sözleşme).

        "Belge yok" bir HATA DEĞİLDİR ve öyle gösterilmez: siparişlerin çoğunda
        fatura belgesi hiç oluşturulmaz. Geçit `not_found` koduyla döner ve
        burada `missing: True`ya çevrilir; kırmızı bir hata kutusu, olağan bir
        durumu arıza gibi gösterirdi. Üretim `bld_invoices` alanının işidir ve
        bu ekranda kapısı YOKTUR.
        """
        try:
            payload = await self._api.order_invoice(int(order_id))
        except Exception as failure:  # noqa: BLE001 — K7
            code = self._code(failure)
            if code == "not_found":
                return {"ok": True, "connected": True, "error": "", "missing": True,
                        "invoice": {}, "order_id": int(order_id)}
            self._log.warning("fatura belgesi okunamadı", orderId=order_id,
                              error=str(failure))
            return {"ok": True, "connected": False, "error": self._fail(failure),
                    "code": code, "missing": False, "invoice": {},
                    "order_id": int(order_id)}

        raw = self._record_of(payload, "data", "invoice")
        return {"ok": True, "connected": True, "error": "", "missing": not raw,
                "invoice": od.invoice_row(raw) if raw else {}, "order_id": int(order_id)}

    async def audit(self, *, limit: int = 50) -> dict[str, Any]:
        """Bu EKRANDAN yapılan yazma denemelerinin YEREL izi.

        Sunucunun izi (`/api/control/audit`) ayrı bir sorunun cevabıdır ve ayrı
        bir modülün işidir. Buradaki satırlar sunucuya HİÇ ULAŞMAMIŞ denemeleri
        de içerir — tam olarak bu yüzden var.
        """
        count = max(1, min(500, int(limit) or 50))
        try:
            rows = await self._store.fetch_all(
                f"SELECT id, target_id, action, reason, actor, result, detail, created_at "
                f"FROM {self._audit} ORDER BY id DESC LIMIT ?", (count,))
        except Exception as failure:  # noqa: BLE001 — iz okunamadı, ekran ayakta (K7)
            self._log.warning("yerel iz okunamadı", error=str(failure))
            return {"ok": True, "connected": None, "error": "", "items": []}

        items = []
        for row in rows:
            try:
                detail = json.loads(row["detail"] or "{}")
            except (TypeError, ValueError):
                detail = {}
            items.append({
                "id": od.as_int(row["id"]), "order_id": od.as_int(row["target_id"]),
                "action": od.text(row["action"]), "reason": od.text(row["reason"]),
                "actor": od.text(row["actor"]), "result": od.text(row["result"]),
                "detail": detail, "created_at": od.text(row["created_at"]),
            })
        return {"ok": True, "connected": None, "error": "", "items": items}

    # ------------------------------------------------------ ekran tercihi

    #: Yazılabilen tercihler. Kapalı liste: tanınmayan bir anahtarı kabul etmek,
    #: yazım hatasını sessizce diske yazıp hiçbir yerde kullanmamak olurdu.
    PREF_KEYS = ("page_size", "range_days", "poll_seconds", "auto_refresh")

    async def prefs(self) -> dict[str, Any]:
        """Ekran tercihleri: yerel kayıt varsa o, yoksa modül ayarı.

        Tercihler BLD'yi ETKİLEMEZ; yalnız bu ekranın ne gösterdiğini belirler.
        """
        stored: dict[str, str] = {}
        try:
            rows = await self._store.fetch_all(f"SELECT key, value FROM {self._prefs}")
            stored = {str(row["key"]): str(row["value"]) for row in rows}
        except Exception as failure:  # noqa: BLE001 — tercih okunamadı, varsayılan yeter
            self._log.warning("tercih okunamadı", error=str(failure))

        auto = od.as_bool(stored.get("auto_refresh"))
        return {
            "page_size": max(5, min(100, od.as_int(stored.get("page_size"),
                                                   self._page_size))),
            "range_days": max(1, min(90, od.as_int(stored.get("range_days"),
                                                   self._default_range_days))),
            "poll_seconds": max(5, min(300, od.as_int(
                stored.get("poll_seconds"),
                od.as_int(self._config.get("poll_seconds"), 15)))),
            "auto_refresh": True if auto is None else auto,
        }

    async def save_prefs(self, values: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Görüntüleme tercihini yazar. BLD'YE HİÇBİR ŞEY GİTMEZ.

        Bu bir iş yazması değildir ve gerekçe istemez: kimse "sayfa boyutunu 50
        yaptım" gerekçesini denetim izinde aramaz. Gerekçeyi burada da zorunlu
        kılmak, gerçek yazmalardaki gerekçe alışkanlığını törenselleştirirdi.
        """
        unknown = sorted(set(values or {}) - set(self.PREF_KEYS))
        if unknown:
            return {"ok": False, "error": f"Tanınmayan tercih: {', '.join(unknown)}."}

        stamp = od.now_iso()
        for key, value in (values or {}).items():
            raw = "true" if value is True else "false" if value is False else od.text(value)
            try:
                await self._store.execute(
                    f"INSERT INTO {self._prefs} (key, value, actor, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                    "value = excluded.value, actor = excluded.actor, "
                    "updated_at = excluded.updated_at",
                    (key, raw, actor, stamp))
            except Exception as failure:  # noqa: BLE001 — K7
                self._log.warning("tercih yazılamadı", key=key, error=str(failure))
                return {"ok": False, "error": f"Tercih yazılamadı: {failure}"}
        return {"ok": True, "error": "", "prefs": await self.prefs()}

    # ================================================================= yazma

    async def create_revision(self, order_id: int, *, items: Any, reason: str, actor: str,
                              note: str = "", requested_at: str = "",
                              customer_note: str = "",
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş revizyonu yazar. TAM KALEM LİSTESİ gönderilir.

        Revizyonu sunucuda `Services\\OrderEditor` yürütür — mutfak kasasının
        kullandığı sınıfın ta kendisi. İkinci bir kopya yazılsaydı sipariş
        merkezden düzenlendiğinde iade kaydı sessizce oluşmayabilirdi.
        """
        problem = od.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        clean, problem = od.revision_items(items)
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)

        # TAZE OKUMA: gönderilen liste siparişin YENİ hâlidir. Ekranda on dakika
        # önce açılmış bir sepetle yazmak, aradan kasadan yapılmış bir
        # değişikliği geri alırdı.
        fresh = await self.order(order_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Sipariş okunamadı."}
        current = fresh["order"]
        if current.get("editable") is not True:
            # Kapıyı sunucu da tutuyor (422); buradaki denetim ağ turunu ve
            # anlamsız bir denetim satırını önler.
            return {"ok": False, "error": current.get("not_editable_label")
                    or "Bu sipariş düzenlenemez."}

        await self._record(action="order.revise", reason=reason, actor=actor, result=TRIED,
                           target_id=order_id,
                           detail={"items": len(clean), "dry_run": dry})

        try:
            # GÖVDEYİ GEÇİT KURAR. İkinci bir gövde kurmak, iki yerde
            # ayrışabilen bir sözleşme demek olurdu. `dry_run` AÇIKÇA geçer.
            result = await self._api.revise_order(
                int(order_id), items=clean, reason=od.text(reason), actor=actor,
                note=od.text(note)[:1000], requested_at=od.text(requested_at),
                customer_note=od.text(customer_note)[:1000], dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="order.revise", reason=reason, actor=actor,
                               result=FAILED, target_id=order_id,
                               detail={"error": str(failure), "dry_run": dry})
            return {"ok": False, "error": self._fail(failure), "code": self._code(failure)}

        await self._record(action="order.revise", reason=reason, actor=actor,
                           result=DRY if dry else DONE, target_id=order_id,
                           detail={"items": len(clean),
                                   "audit_id": od.as_int(result.get("audit_id"))
                                   if isinstance(result, dict) else 0})

        revision = self._record_of(result, "revision")
        order = self._record_of(result, "order")
        return {
            "ok": True, "error": "", "dry_run": dry,
            "would": result.get("would") if isinstance(result, dict) and dry else None,
            "revision": od.revision_row(revision) if revision and not dry else {},
            "order": od.order_row(order) if order and not dry else current,
        }

    async def set_status(self, order_id: int, *, status: str, reason: str, actor: str,
                         dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş durumunu değiştirir. GEÇİŞ MATRİSİ SUNUCUDADIR.

        Burada ön denetim YOKTUR ve bilinçlidir (`orders.py` başlığı): matrisin
        ikinci bir kopyası sessizce ayrışır ve ekran, sunucunun kabul edeceği
        bir geçişi hiç sormadan reddederdi. Sunucu reddederse cevabı
        Türkçeleştirilir ve isteğin çıktığı andaki durum cümleye eklenir.
        """
        problem = od.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        target = od.text(status).lower()
        if target not in od.STATUS_CODES:
            return {"ok": False, "error": f"'{status}' tanınan bir sipariş durumu değil."}
        if target == "iptal":
            # İPTAL AYRI BİR UÇTUR ve ayrı bir izin ister: para hareketi üretir
            # (iade), müşteriye SMS gider ve stok serbest kalır. Buradan geçmesi,
            # `bld_orders.cancel` iznini kâğıt üstünde bırakırdı.
            return {"ok": False, "error": "İptal bu uçtan yapılmaz: iade, SMS ve stok "
                                          "iadesi üreten ayrı bir uç ve ayrı bir izin var."}
        dry = self._dry(dry_run)

        fresh = await self.order(order_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Sipariş okunamadı."}
        current = fresh["order"]
        before = od.text(current.get("status"))

        await self._record(action="order.status", reason=reason, actor=actor, result=TRIED,
                           target_id=order_id,
                           detail={"from": before, "to": target, "dry_run": dry})

        try:
            result = await self._api.change_order_status(
                int(order_id), status=target, reason=od.text(reason), actor=actor,
                dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="order.status", reason=reason, actor=actor,
                               result=FAILED, target_id=order_id,
                               detail={"from": before, "to": target,
                                       "error": str(failure), "dry_run": dry})
            message = self._fail(failure)
            if self._code(failure) == "validation":
                hint = od.transition_hint(before, target)
                if hint:
                    message = f"{message} {hint}"
            return {"ok": False, "error": message, "code": self._code(failure)}

        await self._record(action="order.status", reason=reason, actor=actor,
                           result=DRY if dry else DONE, target_id=order_id,
                           detail={"from": before, "to": target})
        if dry:
            return {"ok": True, "error": "", "dry_run": True, "announced": False,
                    "would": result.get("would") if isinstance(result, dict) else None,
                    "order": current}

        # KURU PROVADA OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi,
        # dinleyicileri "durum değişti" diye uyandırmak yalan olurdu.
        await self._announce(STATUS_EVENT, {"orderId": int(order_id), "from": before,
                                            "to": target, "reason": od.text(reason),
                                            "actor": actor})
        order = self._record_of(result, "order", "data")
        return {"ok": True, "error": "", "dry_run": False, "announced": True,
                "order": od.order_row(order) if order else {**current, "status": target}}

    async def cancel(self, order_id: int, *, reason: str, actor: str, refund: bool = True,
                     notify_customer: bool = True, dry_run: bool | None = None,
                     allow_cancel: bool = False) -> dict[str, Any]:
        """Siparişi iptal eder. YIKICI — ayrı izin, gerekçe ve çift iz.

        `set_status(status="iptal")` İLE AYNI ŞEY DEĞİLDİR: burada para hareketi
        doğar (ödenmiş siparişin iadesi), müşteriye SMS gider ve porsiyonlar gün
        toplamı ile ürün tavanından DÜŞER, yani o kadar sipariş yeniden
        alınabilir hâle gelir.

        Abonelikten üretilmiş bir siparişi iptal etmek ABONELİĞİ DURDURMAZ —
        yalnız o günün siparişini düşürür (sözleşme).
        """
        if not allow_cancel:
            # ÇİFT KAPI (K9): uç noktada da denetleniyor. Arayüzde düğmeyi
            # gizlemek yetkilendirme değildir; istemci gövdeyi elle kurabilir.
            await self._record(action="order.cancel", reason=reason, actor=actor,
                               result=BLOCKED, target_id=order_id,
                               detail={"why": "izin yok"})
            return {"ok": False, "error": "Sipariş iptali ayrı bir yetki ister "
                                          "(`bld_orders.cancel`)."}

        problem = od.reason_error(reason)
        if problem:
            return {"ok": False, "error": problem}
        dry = self._dry(dry_run)

        fresh = await self.order(order_id)
        if not fresh.get("ok"):
            return {"ok": False, "error": fresh.get("error") or "Sipariş okunamadı."}
        current = fresh["order"]
        before = od.text(current.get("status"))

        detail = {"from": before, "refund": bool(refund),
                  "notify_customer": bool(notify_customer), "dry_run": dry}
        await self._record(action="order.cancel", reason=reason, actor=actor, result=TRIED,
                           target_id=order_id, detail=detail)

        try:
            result = await self._api.cancel_order(
                int(order_id), refund=bool(refund), notify_customer=bool(notify_customer),
                reason=od.text(reason), actor=actor, dry_run=dry)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="order.cancel", reason=reason, actor=actor,
                               result=FAILED, target_id=order_id,
                               detail={**detail, "error": str(failure)})
            message = self._fail(failure)
            if self._code(failure) == "conflict":
                message = f"{message} Sipariş zaten iptal edilmiş olabilir; listeyi tazeleyin."
            return {"ok": False, "error": message, "code": self._code(failure)}

        data = od.cancel_result(result)
        await self._record(action="order.cancel", reason=reason, actor=actor,
                           result=DRY if dry else DONE, target_id=order_id,
                           detail={**detail, "refund_kurus": data["refund_kurus"]})
        if dry:
            return {"ok": True, "error": "", "dry_run": True, "announced": False,
                    "would": result.get("would") if isinstance(result, dict) else None,
                    "data": data, "order": current}

        await self._announce(CANCELLED_EVENT, {
            "orderId": int(order_id), "from": before, "reason": od.text(reason),
            "actor": actor, "refundKurus": data["refund_kurus"],
            "refundCreated": data["refund_created"],
            "stockReleasedDay": data["stock_released"]["day"],
        })
        order = self._record_of(result, "order")
        return {"ok": True, "error": "", "dry_run": False, "announced": True,
                "data": data,
                "order": od.order_row(order) if order else {**current, "status": "iptal"}}

    # ============================================================ dışa aktarım

    async def export(self, *, actor: str, max_rows: int = 0,
                     **filters: Any) -> dict[str, Any]:
        """CSV dışa aktarım — sunucu üretir, dosya DİSKE yazılır.

        Baytlar OLDUĞU GİBİ yazılır: dosya UTF-8 BOM ile başlıyor (`EF BB BF`)
        ve BOM'suz kaydedilen bir CSV'yi açan muhasebeci "ğ" yerine kutu görür.
        Metne çevirip yeniden kodlamak tam olarak bunu bozardı.

        DOSYA KİŞİSEL VERİ TAŞIR (ad, telefon): `write_private` 0600 izinle
        yazar ve klasör 0700'dür.

        Bu uç sunucuda DENETLENMEZ (sözleşmede bilinçli eksik) ama YEREL iz
        yazılır: dosyanın kimin makinesinde durduğu sorusunun bir cevabı olmalı.
        """
        clean, problem = self._filters(**filters)
        if problem:
            return {"ok": False, "error": problem}

        limit = max(1, min(20000, int(max_rows) or self._export_max_rows))
        try:
            document = await self._api.export_orders(max_rows=limit, **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("dışa aktarım alınamadı", error=str(failure))
            return {"ok": False, "connected": False, "error": self._fail(failure),
                    "code": self._code(failure)}

        content = document.get("content") if isinstance(document, dict) else None
        if not isinstance(content, bytes | bytearray) or not content:
            return {"ok": False, "error": "Sunucu boş bir dosya döndürdü."}

        name = self._safe_name(od.text(document.get("filename")))
        try:
            path = write_private(self._export_dir / name, bytes(content))
        except OSError as failure:
            return {"ok": False, "error": f"Dosya yazılamadı: {failure}"}

        truncated = bool(document.get("truncated"))
        await self._record(action="order.export", reason="Sipariş listesi dışa aktarıldı",
                           actor=actor, result=DONE,
                           detail={"rows": document.get("total_rows"), "file": name,
                                   "truncated": truncated, "filters": clean})
        return {
            "ok": True, "connected": True, "error": "", "path": str(path), "name": name,
            "bytes": od.as_int(document.get("bytes"), len(content)),
            "total_rows": document.get("total_rows"),
            # KESİLMİŞ DOSYA HATA DEĞİLDİR (sözleşme): kesilmiş bir dosya, hiç
            # dosya olmamasından iyidir. Ama ekran bunu SÖYLEMEK zorunda —
            # muhasebeci eksik listeyi tam sanmamalı.
            "truncated": truncated,
            "max_rows": limit,
        }

    @staticmethod
    def _safe_name(raw: str) -> str:
        """Sunucunun verdiği dosya adını güvenli kılar.

        `Content-Disposition` UZAKTAN gelen bir dizedir; içindeki `/` ya da
        `..` dosyayı rapor klasörünün dışına yazdırırdı. Ad tümüyle
        temizlenirse damgalı bir ad üretilir — adsız dosya yazmaktansa.
        """
        name = _SAFE_NAME.sub("-", Path(raw).name).strip("-")
        if not name or not name.lower().endswith(".csv"):
            stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d-%H%M")
            return f"bld-siparisler-{stamp}.csv"
        return name[:120]
