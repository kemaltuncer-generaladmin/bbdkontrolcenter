"""Menü Yönetimi — iş kuralları.

VERİ BLD'DEDİR, KARAR BURADADIR. Menü günü, kalemleri, yayın durumu ve stok
sayaçları BLD sunucusunda durur ve buraya `bld.api` geçidinden gelir (K4); bu
modül ham httpx kullanmaz ve UZAK VERİNİN KOPYASINI TUTMAZ. Yerel tablolar
yalnız BLD'de KARŞILIĞI OLMAYAN üç şeyi saklar: yazma gerekçesi (denetim izi),
stok tavanı önizlemesi (temel çizgi) ve ekran tercihi.

Neden kopya tutulmuyor: `sold` her siparişle ve her abonelik üretimiyle
değişiyor, kesim saati geçtiğinde gün kendiliğinden kapanıyor. Yerel bir kopya
her zaman bir tur geride olur ve "34 porsiyon kaldı" diyen bir ekran, aslında
dolmuş bir günü açık gösterir.

UZAK SİSTEM DÜŞERSE EKRAN AYAKTA KALIR (K7): okuma uçları
`{"ok": True, "connected": False, "error": ...}` döner, İSTİSNA DIŞARI SIZMAZ.
Uç yine 200 verir ve panel çökmez; istisna yalnız izin ve şema kapısından
çıkar.

`ok: True` OKUMANIN BAŞARISIZLIĞINI DEĞİL, UCUN SAĞLIĞINI anlatır: uç çalıştı,
cevabı "bağlanamadım"dır. Ayrımı taşıyan alan `connected`'dır ve ekranın onu
OKUMASI gerekir — yalnız `ok`a bakan bir panel geçit düştüğünde "menü yok" der
ve K7'nin engellemek için var olduğu yalanı söyler.

ÜÇ AYRI "BOŞ" VARDIR ve üçü ayrı alanla taşınır:

    connected: False           BLD'ye ulaşılamadı — hiçbir şey bilinmiyor
    endpoint_missing: True     uç henüz sunucuda yayında değil (K7, geçit
                               `control_endpoint_missing` veriyor)
    exists: False              uç çalıştı, o güne menü GİRİLMEMİŞ

Üçünü tek bir boş listeye indirmek, "sunucu kapalı" ile "perşembeye menü
girilmemiş"i aynı ekrana çevirirdi; ikincisi yöneticinin görmesi gereken
bilginin ta kendisidir.

ANAHTAR ADLARI. Yanıtlar ve gövdeler `docs/control/menu.md`'nin snake_case
sözlüğünü korur; TEK İSTİSNA gövdedeki `dryRun` alanıdır (panel→Kontrol Merkezi
sınırında camelCase, `store_orders` deseni). Çeviri tek yerde yapılır:
`dryRun` → geçidin `dry_run` argümanı.

GEREKÇE UÇ BAŞINA İSTENİR, HER YAZMADA DEĞİL. Ölçüt: işlem müşteriye GÖRÜNÜR
HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu.

    ister      publish · unpublish · delete_day · duplicate
    istemez    create_day · update_day · add_item · update_item · delete_item ·
               preview_stock · apply_stock

Gerekçe İSTEMEYEN metotlar `reason` PARAMETRESİ DE ALMAZ. Alanı imzada bırakıp
görmezden gelmek daha küçük bir değişiklik olurdu ama sessiz bir tuzak kurardı:
panel gerekçe göndermeye devam eder, hiçbir yere yazılmadığını kimse fark
etmezdi. Parametre yoksa yanlış çağrı `TypeError` verir.

`actor` HER metotta durur ve yerel denetim satırı her yazmada açılır; gerekçe
istenmeyen uçlarda `reason` sütunu boş kalır, SATIR boş kalmaz. "Kim yaptı"
sorusunun cevabı hiçbir yazmada kaybolmaz.

YAZMA ZİNCİRİ — her yazma ucu bu beş adımı bu sırayla uygular:

    1. gerekçe (isteniyorsa) ve alan denetimi — arayüzde zorunlu göstermek
       yetmez (K9)
    2. TAZE OKUMA (gün aradan yayınlanmış, silinmiş ya da değişmiş olabilir)
    3. yerel iz: `result="denendi"`  ← ağ koparsa geriye YALNIZ bu kalır
    4. geçit çağrısı — `dry_run=` HER ZAMAN AÇIK GEÇİLİR
    5. yerel iz: `ok` / `dry_run` / `hata`

Üçüncü adım kritiktir: "ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında tek
kanıttır. Bir gün yayınlanırken ağ koparsa günün yayına girip girmediği
bilinmez; iz olmasa kimin denediği de bilinmezdi.

`dry_run=` HİÇBİR ÇAĞRIDA ATLANMAZ. Geçidin varsayılanı `config/local.yaml`
ile geliyor, o dosya git dışında ve bugün `true` yazıyor: bayrağı atlayan bir
modül hiçbir şey yazmadan `{"ok": true}` alır ve ekran "kaydedildi" der.
"Kaydedildi" ile "sessizce atıldı" ayırt edilemez hâle gelir.

STOK İKİ ADIMLIDIR ve tek yazma yolu budur. `PUT stock` TAM LİSTE yazar:
gönderilmeyen kalemin tavanı `null`'a düşer. Önce kuru prova koşulur
(`dry_run=True`), sunucu uyarıları hesaplar ("bu tavan 60 satılmışın altında"),
tablo ve uyarılar yerel önizleme satırına yazılır; yönetici onayladığında
jetonla gelinir ve TEMEL ÇİZGİ DOĞRULANIR. Arada satış değiştiyse uygulama
reddedilir: yarım saat önce görülen sayılara bakarak tavan yazmak, doğru
kararı yanlış veriye dayandırmaktır.

Kuru provada OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi, dinleyicileri
"gün yayınlandı" diye uyandırmak yalan olurdu.
"""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from . import menu as mn

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Manifestteki olaylar. Kuru provada YAYINLANMAZ.
PUBLISHED_EVENT = "bld_menu.day_published"
UNPUBLISHED_EVENT = "bld_menu.day_unpublished"
STOCK_EVENT = "bld_menu.stock_changed"

#: Olay yükleri camelCase'tir: olay yolu Kontrol Merkezi'nin KENDİ iç yüzeyidir
#: ve `store.order.status_changed` bu biçimi kurdu. HTTP yanıtları ise
#: sözleşmenin snake_case sözlüğünü korur.

#: Stok önizlemesinin varsayılan ömrü (dakika).
PREVIEW_TTL_MINUTES = 30

#: Geçidin "uç henüz sunucuda yayında değil" kodu (`bld_api/errors.py`).
#: `not_found` ile AYRI şeydir: ilki "uç var, kayıt yok", ikincisi "bu uç
#: BLD'ye daha çıkmadı" demek ve ekranın söyleyeceği cümle bambaşka.
MISSING_CODE = "control_endpoint_missing"
NOT_FOUND_CODE = "not_found"


class MenuService:
    """Menü Yönetimi ekranının tüm iş kuralları. HTTP hatası FIRLATMAZ.

    Servis bir istisna ile cevap verseydi ekran beyaz bir hata sayfası
    gösterirdi; burada her yol `{"ok": ..., "error": ...}` ile biter ve panel
    kullanıcıya ne olduğunu YAZAR. 4xx yalnız izin ve şema kapısından çıkar.
    """

    def __init__(self, *, api: Any, store: Any, log: Any, config: dict[str, Any],
                 publish: Any = None) -> None:
        self._api = api
        self._store = store
        self._log = log
        self._config = config or {}
        self._publish = publish

        self._audit = store.table("audit")
        self._preview = store.table("stock_preview")
        self._prefs = store.table("prefs")

    # ------------------------------------------------------------- ayarlar

    @property
    def _location(self) -> int | None:
        """Vitrin kimliği; `0` "alanı hiç gönderme" demektir.

        Sıfırı `None`'a çevirmek bir kolaylık değil bir karar: tek vitrinli
        kurulumda her isteğe kimlik iliştirmek, sunucudaki `Location::getDefault()`
        ile ekranın gömülü sayısının bir gün ayrışması riskini alır.
        """
        value = mn.as_int(self._config.get("location_id"), 0)
        return value if value > 0 else None

    @property
    def _calendar_max_days(self) -> int:
        return max(1, min(92, mn.as_int(self._config.get("calendar_max_days"), 92)))

    @property
    def _refresh_seconds(self) -> int:
        return max(30, mn.as_int(self._config.get("calendar_refresh_seconds"), 120))

    @property
    def _preview_ttl(self) -> int:
        return max(1, mn.as_int(self._config.get("stock_preview_ttl_minutes"),
                                PREVIEW_TTL_MINUTES))

    @property
    def _product_limit(self) -> int:
        return max(1, min(2000, mn.as_int(self._config.get("product_limit"), 400)))

    # ------------------------------------------------------ yerel tablolar

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
        return {
            "calendar_refresh_seconds": mn.as_int(stored.get("calendar_refresh_seconds"),
                                                  self._refresh_seconds),
            "product_limit": mn.as_int(stored.get("product_limit"), self._product_limit),
        }

    async def _record(self, *, action: str, actor: str, result: str,
                      reason: str = "", target_date: str = "", target_id: int = 0,
                      detail: Any = None) -> None:
        """Yerel denetim izi. BLD de `veykemtu_control_audit` tutuyor
        (`00-genel.md` §8); bu satır ONUN YERİNE DEĞİL, ONDAN ÖNCE yazılır.

        Ayrım önemli: uzak kayıt yalnız sunucuya ULAŞAN istekleri bilir. Ağ
        koparsa, geçit patlarsa ya da istek yarıda kalırsa "kim neyi denedi"
        sorusunun cevabı yalnız burada kalır.

        `reason` VARSAYILANI BOŞ, `actor` ZORUNLU. Gerekçe istenmeyen uçlarda
        satır yine açılır ve `reason` sütunu boş kalır (kolon `DEFAULT ''`);
        satırı hiç açmamak, hızlanma uğruna izin kendisini atmak olurdu.
        """
        try:
            await self._store.execute(
                f"INSERT INTO {self._audit} "
                "(target_type, target_date, target_id, action, reason, actor, result, "
                "detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("daily_menu", mn.text(target_date), int(target_id or 0), action,
                 mn.text(reason), actor, result,
                 json.dumps(detail or {}, ensure_ascii=False), mn.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — iz yazılamadı, iş durmasın (K7)
            self._log.warning("denetim izi yazılamadı", action=action, error=str(failure))

    async def _announce(self, event: str, payload: dict[str, Any]) -> None:
        """Olayı veri yoluna bırakır (K3).

        Yayın BAŞARISIZ OLSA BİLE iş başarılıdır: gün BLD'de yayınlanmıştır,
        dinleyicinin patlaması onu geri almaz (K7).
        """
        if self._publish is None:
            return
        try:
            await self._publish(event, payload)
        except Exception as failure:  # noqa: BLE001 — dinleyici bizi düşürmez (K7)
            self._log.warning("olay yayınlanamadı", event=event, error=str(failure))

    # ------------------------------------------------------------- yardımcı

    @staticmethod
    def _code(failure: Exception) -> str:
        """Geçidin makine okur neden kodu; yoksa boş dize."""
        return str(getattr(failure, "code", "") or "")

    @classmethod
    def _message(cls, failure: Exception) -> str:
        code = cls._code(failure)
        if code == MISSING_CODE:
            return ("Bu uç BLD sunucusunda henüz yayında değil. Ekran çalışmaya "
                    "devam ediyor; sunucu güncellendiğinde veri kendiliğinden gelir.")
        message = str(failure).strip()
        return message or "BLD sunucusuna ulaşılamadı."

    @classmethod
    def _read_failure(cls, failure: Exception, **extra: Any) -> dict[str, Any]:
        """Okuma hatasının ORTAK zarfı. `ok` yine `True`: uç çalıştı, cevabı
        "bağlanamadım"dır ve panel bunu ekranda yazar (K7)."""
        code = cls._code(failure)
        return {"ok": True, "connected": False, "error": cls._message(failure),
                "code": code, "endpoint_missing": code == MISSING_CODE, **extra}

    @classmethod
    def _write_failure(cls, failure: Exception) -> dict[str, Any]:
        """Yazma hatasının ortak zarfı. `conflict` kodu ayrı taşınır: panel o
        durumda TAZELEYİP tekrar sorar, kullanıcıya ham 409 göstermez
        (`00-genel.md` §7.2 — `CONFLICT` bilinçli olarak geniş tutuldu)."""
        return {"ok": False, "error": cls._message(failure), "code": cls._code(failure)}

    def _guard(self, reason: str) -> str:
        """Gerekçe denetimi — YALNIZ gerekçe İSTEYEN dört uçta çağrılır.

        `publish` · `unpublish` · `delete_day` · `duplicate`. Ötekiler bu
        yardımcıyı hiç çağırmaz çünkü `reason` parametreleri de yok.
        """
        return mn.reason_error(reason)

    @staticmethod
    def _today() -> str:
        """Bugünün yerel günü.

        Sunucunun `server_time` alanı ISO 8601 UTC'dir ve GÜN SINIRI için
        kullanılamaz: 23:30 TSİ, UTC'de bir önceki gündür ve "geçmişe menü
        kurulamaz" kuralı gece yarısına doğru yanlış tarafa düşerdi. Gün
        kararları yerel takvimden okunur; asıl kapı zaten sunucudadır (K9).
        """
        return datetime.now().strftime("%Y-%m-%d")  # noqa: DTZ005 — yerel gün bilinçli

    @staticmethod
    def _rows(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtından satırları çıkarır.

        Geçit zarfı ZATEN AÇIYOR (`_list` → `{"items": [...]}`); `data` ve düz
        dizi de kabul edilir çünkü tek bir ada bağlanmak, ad tutmadığında
        ekranı SESSİZCE boş gösterirdi.
        """
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("items", "data"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [row for row in value if isinstance(row, dict)]
        return []

    @staticmethod
    def _record_of(payload: Any, *keys: str) -> dict[str, Any]:
        """Geçidin tekil yanıtından kaydı çıkarır (`{"data": {...}}` ya da düz)."""
        if not isinstance(payload, dict):
            return {}
        for key in keys:
            value = payload.get(key)
            if isinstance(value, dict):
                return value
        return payload

    @staticmethod
    def _was_dry(payload: Any) -> bool:
        """Sunucu isteği KURU PROVA olarak mı işledi.

        Yanıttan okunur, istekten değil: bir kurulum geçidin varsayılanını
        açık bırakırsa ekran "yapıldı" DEMEMELİ. `dry_run` alanı sözleşmenin
        yazma zarfında her zaman var (`00-genel.md` §3.1).
        """
        return bool(isinstance(payload, dict) and payload.get("dry_run"))

    async def _fresh_day(self, date: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Günün TAZE hâli. `(gün, hata_zarfı)` döner; biri her zaman boştur.

        Yazmadan hemen önce okumak, aradan yayınlanmış / silinmiş / kalemi
        değişmiş bir güne körlemesine yazmayı önler.
        """
        try:
            payload = await self._api.menu_day(date, location_id=self._location)
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) == NOT_FOUND_CODE:
                return None, {"ok": False, "code": NOT_FOUND_CODE,
                              "error": f"{date} gününe menü girilmemiş."}
            return None, self._write_failure(failure)
        row = self._record_of(payload, "data")
        if not row:
            return None, {"ok": False, "code": NOT_FOUND_CODE,
                          "error": f"{date} gününe menü girilmemiş."}
        return mn.day_view(row), {}

    # ================================================================= okuma

    def contract(self) -> dict[str, Any]:
        """Panelin ekranı çizmek için ihtiyaç duyduğu SABİTLER — ağa çıkmaz.

        Geçit düşse bile form, rozetler ve açıklama listesi çizilebilir (K7).
        Durum etiketleri, sipariş kapalılık nedenleri ve alan sınırları panelde
        TEKRAR YAZILMAZ: iki kopya bir gün ayrışır ve ekran, sunucunun hiç
        kullanmadığı bir kodu etiketlemeye çalışır.
        """
        return {
            "statuses": [{"code": code, "label": mn.STATUS_LABELS[code],
                          "tone": mn.STATUS_TONES[code]} for code in mn.STATUSES],
            "not_orderable": [{"code": code, "label": mn.NOT_ORDERABLE_LABELS[code]}
                              for code in mn.NOT_ORDERABLE_REASONS],
            "limits": {
                # Gerekçe sınırları DEĞİŞMEDİ ama artık yalnız DÖRT uçta
                # geçerli: publish · unpublish · gün silme · kopyalama.
                # Ötekiler gerekçe alanını hiç kabul etmiyor (422).
                "reason_min": mn.MIN_REASON, "reason_max": mn.MAX_REASON,
                "title": mn.MAX_TITLE, "description": mn.MAX_DESCRIPTION,
                "internal_note": mn.MAX_INTERNAL_NOTE, "label": mn.MAX_LABEL,
                "quantity": mn.MAX_QUANTITY, "sort_step": mn.SORT_STEP,
                "calendar_max_days": self._calendar_max_days,
            },
            "refresh_seconds": self._refresh_seconds,
            "location_id": self._location or 0,
        }

    async def calendar(self, *, date_from: str, date_to: str) -> dict[str, Any]:
        """Takvim ızgarası — aralıktaki HER GÜN, menüsü olmayanlar dâhil."""
        contract = self.contract()
        for value in (date_from, date_to):
            problem = mn.date_error(value)
            if problem:
                return {"ok": False, "connected": True, "error": problem,
                        "items": [], "meta": {}, **contract}
        if mn.text(date_to) < mn.text(date_from):
            return {"ok": False, "connected": True, "error": "Bitiş günü başlangıçtan önce.",
                    "items": [], "meta": {}, **contract}
        span = self._span(date_from, date_to)
        if span > self._calendar_max_days:
            # SESSİZCE KIRPILMAZ: kırpılmış bir aralık, ekranda menüsü olan
            # günleri "menü girilmemiş" diye gösterirdi.
            return {"ok": False, "connected": True,
                    "error": (f"Takvim aralığı en çok {self._calendar_max_days} gün "
                              f"olabilir; {span} gün istendi."),
                    "items": [], "meta": {}, **contract}

        try:
            payload = await self._api.menu_calendar(date_from=date_from, date_to=date_to,
                                                    location_id=self._location)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("takvim okunamadı", date_from=date_from, date_to=date_to,
                              error=str(failure))
            return self._read_failure(failure, items=[], meta={}, **contract)

        rows = [mn.calendar_row(row) for row in self._rows(payload)]
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        return {"ok": True, "connected": True, "error": "", "endpoint_missing": False,
                "items": rows, "meta": dict(meta or {}),
                "summary": self._summary(rows), **contract}

    @staticmethod
    def _span(date_from: str, date_to: str) -> int:
        start = datetime.strptime(mn.text(date_from), "%Y-%m-%d").replace(tzinfo=UTC)
        end = datetime.strptime(mn.text(date_to), "%Y-%m-%d").replace(tzinfo=UTC)
        return (end - start).days + 1

    @staticmethod
    def _summary(rows: list[dict[str, Any]]) -> dict[str, int]:
        """Ay üstündeki sayaçlar. Renk tek başına anlam taşımaz (kit kuralı 7):
        rozetlerin yanında bu sayılar yazılı durur."""
        return {
            "published": sum(1 for row in rows if row["status"] == mn.PUBLISHED),
            "draft": sum(1 for row in rows if row["status"] == mn.DRAFT),
            "missing": sum(1 for row in rows if not row["has_menu"]),
            "closed": sum(1 for row in rows if row["closed"]),
            "sold_out": sum(1 for row in rows if row["sold_out"]),
        }

    async def day(self, date: str) -> dict[str, Any]:
        """Tek günün tam menüsü. O güne menü yoksa `exists: False`.

        BOŞ GÖVDE DÖNDÜRÜLMEZ: "menü yok" ile "boş menü var" ayırt edilebilmeli
        ve ekran birincisinde "gün kur" düğmesini, ikincisinde "kalem ekle"yi
        gösterir.
        """
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "connected": True, "error": problem,
                    "exists": False, "day": {}}
        try:
            payload = await self._api.menu_day(date, location_id=self._location)
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) == NOT_FOUND_CODE:
                return {"ok": True, "connected": True, "error": "", "exists": False,
                        "endpoint_missing": False, "day": {}, "date": mn.text(date)}
            self._log.warning("gün okunamadı", date=date, error=str(failure))
            return self._read_failure(failure, exists=False, day={}, date=mn.text(date))
        row = self._record_of(payload, "data")
        if not row:
            return {"ok": True, "connected": True, "error": "", "exists": False,
                    "endpoint_missing": False, "day": {}, "date": mn.text(date)}
        return {"ok": True, "connected": True, "error": "", "endpoint_missing": False,
                "exists": True, "day": mn.day_view(row), "date": mn.text(date)}

    async def stock(self, date: str) -> dict[str, Any]:
        """Gün ve kalem stok durumu. ÖNBELLEĞE ALINMAZ (geçit de almıyor):
        bir dakika bayat bir sayı, dolmuş bir günü açık gösterirdi."""
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "connected": True, "error": problem, "stock": {}}
        try:
            payload = await self._api.menu_stock(date, location_id=self._location)
        except Exception as failure:  # noqa: BLE001 — K7
            if self._code(failure) == NOT_FOUND_CODE:
                return {"ok": True, "connected": True, "error": "", "exists": False,
                        "endpoint_missing": False, "stock": {}}
            self._log.warning("stok okunamadı", date=date, error=str(failure))
            return self._read_failure(failure, exists=False, stock={})
        return {"ok": True, "connected": True, "error": "", "endpoint_missing": False,
                "exists": True, "stock": mn.stock_view(self._record_of(payload, "data"))}

    async def products(self) -> dict[str, Any]:
        """Kalem eklerken açılan seçicinin ürün kataloğu.

        `bld_menu.view` YETER: bu bir okumadır ve ürün adını görmek, menüye
        kalem eklemek demek değildir. Yazma kapısı `POST /days/{date}/items`
        üzerindedir ve `bld_menu.manage` ister.

        YALNIZ SATIŞTAKİ ÜRÜNLER. Satıştan kaldırılmış bir ürünü içeren gün
        yayınlanamıyor (`ITEM_UNAVAILABLE`); listede göstermek, yöneticiye
        kurduğu menünün yayınlanmayacağını ancak yayın düğmesinde söylemek
        olurdu.
        """
        try:
            payload = await self._api.product_picker(only_active=True)
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("ürün listesi okunamadı", error=str(failure))
            return self._read_failure(failure, items=[], total=0, truncated=False)
        envelope = payload if isinstance(payload, dict) else {}
        rows = [mn.product_row(row) for row in self._rows(payload)]
        limit = self._product_limit
        # İKİ AYRI KIRPMA VAR. `server_truncated` sunucudaki tam taramanın
        # sınıra dayandığını söyler, `truncated` ekranın kaç satır çizdiğini.
        # Tek bayrağa indirmek, "listede yok" diyen kullanıcıya hangi sınırı
        # gevşetmesi gerektiğini söyleyemezdi.
        server_truncated = bool(envelope.get("truncated"))
        return {"ok": True, "connected": True, "error": "", "endpoint_missing": False,
                "items": rows[:limit],
                "total": mn.as_int(envelope.get("total"), len(rows)),
                "truncated": server_truncated or len(rows) > limit,
                "server_truncated": server_truncated}

    # ================================================================= yazma

    async def create_day(self, *, date: str, fields: dict[str, Any],
                         items: list[dict[str, Any]], actor: str,
                         dry_run: bool) -> dict[str, Any]:
        """Yeni gün kurar. Gün HER ZAMAN `draft` doğar. GEREKÇE İSTEMEZ.

        Yayın ayrı bir eylemdir ve ayrı bir denetim satırı bırakır: yarım
        girilmiş bir perşembe kaydedildiği anda müşteriye görünmemeli. Gerekçe
        o yayın adımında istenir — taslak kurmak bir taahhüt değildir.
        """
        problem = mn.past_error(date, self._today())
        if problem:
            return {"ok": False, "error": problem}
        clean, problem = self._day_fields(fields, allow_image=False)
        if problem:
            return {"ok": False, "error": problem}
        rows, problem = self._new_items(items)
        if problem:
            return {"ok": False, "error": problem}

        detail = {"date": date, "item_count": len(rows),
                  "package_price_kurus": clean.get("package_price_kurus")}
        await self._record(action="day.create", actor=actor, result=TRIED,
                           target_date=date, detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.create_menu_day(
                date=date, items=rows, location_id=self._location,
                actor=actor, dry_run=dry_run, **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="day.create", actor=actor,
                               result=FAILED, target_date=date,
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("day.create", result, actor=actor,
                                  date=date, detail=detail)

    async def update_day(self, date: str, *, fields: dict[str, Any],
                         actor: str, dry_run: bool) -> dict[str, Any]:
        """Gün başlığı/fiyatı/notu — KISMİ yazar. GEREKÇE İSTEMEZ.

        Gönderilmeyen alan değişmez; `null` gönderilen alan temizlenir. İkisi
        ayrı: alanı hiç göndermemek "dokunma", `null` göndermek "boşalt".
        Ayrım uçta `model_fields_set` ile korunur ve geçide yalnız GÖNDERİLEN
        anahtarlar geçer — geçidin `UNSET` nöbetçisi ötekileri gövdeye koymaz.
        """
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}
        if not fields:
            return {"ok": False,
                    "error": ("Değişen alan yok. Gönderilmeyen alan değişmediği için "
                              "boş bir güncelleme denetim izine satır yazıp hiçbir şey "
                              "yapmazdı.")}
        clean, problem = self._day_fields(fields)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        # KESİM SAATİ GEÇMİŞ GÜNE FİYAT YAZILMAZ (sözleşme): o gün için sipariş
        # kapandı ve girmiş siparişler eski fiyattan; sunucu 409 veriyor, biz
        # nedenini cümleyle söylüyoruz (K9 — çift kapı).
        blocked = self._price_lock(current, clean)
        if blocked:
            await self._record(action="day.update", actor=actor,
                               result=BLOCKED, target_date=date, target_id=current["id"],
                               detail={"fields": sorted(clean)})
            return {"ok": False, "error": blocked, "code": "conflict"}

        detail = {"date": date, "fields": sorted(clean)}
        await self._record(action="day.update", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.update_menu_day(
                date, location_id=self._location, actor=actor,
                dry_run=dry_run, **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="day.update", actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("day.update", result, actor=actor,
                                  date=date, day_id=current["id"], detail=detail)

    async def delete_day(self, date: str, *, reason: str, actor: str, dry_run: bool,
                         allow_remove: bool = False) -> dict[str, Any]:
        """Günü siler — GERİ ALINAMAZ, kalemleriyle birlikte gider.

        AYRI İZİN İSTER (`bld_menu.remove`). Uç noktada da denetlenir; burada
        TEKRAR denetlenmesi K9'un çift kapısıdır: izin kararı servisin kendi
        elinde olmalı, çağıranın doğru uçtan geldiğine güvenmemeli.
        """
        if not allow_remove:
            await self._record(action="day.delete", reason=reason, actor=actor,
                               result=BLOCKED, target_date=date,
                               detail={"missing_permission": "bld_menu.remove"})
            return {"ok": False, "error": "Menü günü silmek için `bld_menu.remove` yetkisi gerekli."}
        problem = self._guard(reason) or mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        if current["status"] == mn.PUBLISHED:
            await self._record(action="day.delete", reason=reason, actor=actor,
                               result=BLOCKED, target_date=date, target_id=current["id"],
                               detail={"status": current["status"]})
            return {"ok": False, "code": "conflict",
                    "error": ("Yayınlanmış bir gün silinemez. Önce yayından çekin — "
                              "o güne sipariş girmişse yayından çekmek de engellenir "
                              "ve bu doğrudur: siparişin bağlandığı menü kaybolmamalı.")}

        detail = {"date": date, "item_count": current["item_count"]}
        await self._record(action="day.delete", reason=reason, actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.delete_menu_day(
                date, location_id=self._location, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="day.delete", reason=reason, actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("day.delete", result, reason=reason, actor=actor,
                                  date=date, day_id=current["id"], detail=detail)

    async def publish(self, date: str, *, reason: str, actor: str,
                      dry_run: bool) -> dict[str, Any]:
        """Günü yayına alır. Ön denetimler kuru provada da koşar."""
        problem = self._guard(reason) or mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        problem = mn.publish_error(current)
        if problem:
            await self._record(action="publish", reason=reason, actor=actor,
                               result=BLOCKED, target_date=date, target_id=current["id"],
                               detail={"item_count": current["item_count"]})
            return {"ok": False, "error": problem}

        detail = {"date": date, "item_count": current["item_count"],
                  "package_price_kurus": current["package_price_kurus"]}
        await self._record(action="publish", reason=reason, actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.publish_menu_day(
                date, location_id=self._location, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="publish", reason=reason, actor=actor, result=FAILED,
                               target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        settled = await self._settle("publish", result, reason=reason, actor=actor,
                                     date=date, day_id=current["id"], detail=detail)
        if settled["ok"] and not settled["dry_run"]:
            await self._announce(PUBLISHED_EVENT, {
                "date": date, "itemCount": current["item_count"],
                "packagePriceKurus": current["package_price_kurus"], "actor": actor})
        return settled

    async def unpublish(self, date: str, *, reason: str, actor: str,
                        dry_run: bool) -> dict[str, Any]:
        """Günü taslağa çeker.

        `bld_menu.manage` YETER, `remove` istemez: bu geri alınabilir bir
        şalterdir — gün, kalemleri ve tavanlarıyla yerinde durur ve yeniden
        yayınlanabilir. Sunucu ayrıca o güne sipariş girmişse engelliyor.
        """
        problem = self._guard(reason) or mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        if current["status"] != mn.PUBLISHED:
            return {"ok": False, "code": "conflict",
                    "error": "Bu gün zaten yayında değil."}

        detail = {"date": date, "item_count": current["item_count"]}
        await self._record(action="unpublish", reason=reason, actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.unpublish_menu_day(
                date, location_id=self._location, reason=reason, actor=actor,
                dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="unpublish", reason=reason, actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        settled = await self._settle("unpublish", result, reason=reason, actor=actor,
                                     date=date, day_id=current["id"], detail=detail)
        if settled["ok"] and not settled["dry_run"]:
            await self._announce(UNPUBLISHED_EVENT,
                                 {"date": date, "reason": mn.text(reason), "actor": actor})
        return settled

    # -------------------------------------------------------------- kalemler

    async def add_item(self, date: str, *, fields: dict[str, Any],
                       actor: str, dry_run: bool) -> dict[str, Any]:
        """Güne kalem ekler. GEREKÇE İSTEMEZ.

        Şikâyetin çıkış noktası burasıydı: bir güne beş ürün koymak beş kez
        ürün seçici + form + gerekçe diyaloğu demekti.
        """
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}
        clean, problem = self._item_fields(fields, require_menu_id=True)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        # AYNI ÜRÜN İKİ KEZ EKLENMEZ (`veykemtu_daily_menu_item_essiz`). Sunucu
        # 409 veriyor; buradaki kapı "Tavuk Sote zaten menüde" cümlesini kurar.
        if any(item["menu_id"] == clean["menu_id"] for item in current["items"]):
            await self._record(action="item.create", actor=actor,
                               result=BLOCKED, target_date=date, target_id=current["id"],
                               detail={"menu_id": clean["menu_id"]})
            return {"ok": False, "code": "conflict",
                    "error": ("Bu ürün o gün zaten menüde. Adet ya da etiket değiştirmek "
                              "için mevcut kalemi düzenleyin.")}

        detail = {"date": date, "menu_id": clean["menu_id"]}
        await self._record(action="item.create", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.create_menu_item(
                date, location_id=self._location, actor=actor,
                dry_run=dry_run, **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="item.create", actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("item.create", result, actor=actor,
                                  date=date, day_id=current["id"], detail=detail)

    async def update_item(self, date: str, item_id: int, *, fields: dict[str, Any],
                          actor: str, dry_run: bool) -> dict[str, Any]:
        """Kalemi günceller — KISMİ. `menu_id` YAZILAMAZ. GEREKÇE İSTEMEZ.

        YAYINDAKİ günün kaleminde de istemez; bu bilinçli bir karardır ve
        politikanın kendi ölçütüyle gerilimlidir (bkz. `api/routes.py` →
        `ItemPatchBody`).
        """
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}
        if not fields:
            return {"ok": False, "error": "Değişen alan yok."}
        clean, problem = self._item_fields(fields, require_menu_id=False)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        if not any(item["id"] == int(item_id) for item in current["items"]):
            return {"ok": False, "code": NOT_FOUND_CODE,
                    "error": "Kalem o günün menüsünde yok; ekran tazelenmiş olabilir."}

        detail = {"date": date, "item_id": int(item_id), "fields": sorted(clean)}
        await self._record(action="item.update", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.update_menu_item(
                date, int(item_id), actor=actor, dry_run=dry_run, **clean)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="item.update", actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("item.update", result, actor=actor,
                                  date=date, day_id=current["id"], detail=detail)

    async def delete_item(self, date: str, item_id: int, *, actor: str,
                          dry_run: bool, allow_remove: bool = False) -> dict[str, Any]:
        """Kalemi siler. AYRI İZİN İSTER (`bld_menu.remove`) — çift kapı (K9).

        İZİN DURUYOR, GEREKÇE KALKTI. İkisi ayrı sorulardır: "kim silebilir"
        değişmedi, yalnız "neden" sorusu seyrekleşti. Gün silmenin aksine tek
        kalem silmek menü kurarken yapılan olağan bir düzeltmedir.
        """
        if not allow_remove:
            await self._record(action="item.delete", actor=actor,
                               result=BLOCKED, target_date=date,
                               detail={"item_id": int(item_id),
                                       "missing_permission": "bld_menu.remove"})
            return {"ok": False, "error": "Kalem silmek için `bld_menu.remove` yetkisi gerekli."}
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        row = next((item for item in current["items"] if item["id"] == int(item_id)), None)
        if row is None:
            return {"ok": False, "code": NOT_FOUND_CODE,
                    "error": "Kalem o günün menüsünde yok; ekran tazelenmiş olabilir."}

        detail = {"date": date, "item_id": int(item_id), "menu_id": row["menu_id"],
                  "name": row["name"]}
        await self._record(action="item.delete", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.delete_menu_item(
                date, int(item_id), actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="item.delete", actor=actor,
                               result=FAILED, target_date=date, target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        return await self._settle("item.delete", result, actor=actor,
                                  date=date, day_id=current["id"], detail=detail)

    # ------------------------------------------------------------------ stok

    async def preview_stock(self, date: str, *, capacity_total: Any,
                            items: Any, actor: str) -> dict[str, Any]:
        """Tavan tablosunun KURU PROVASI. Jeton ve uyarılar döner. GEREKÇE İSTEMEZ.

        Asıl işi budur: yönetici tavanı yazmadan ÖNCE kaç siparişin altında
        kaldığını görür. Sunucu `warnings` bloğunu kuru provada da hesaplıyor.
        Emniyet gerekçede değil bu iki adımlı akıştadır.
        """
        problem = mn.date_error(date) or mn.capacity_error(capacity_total)
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        rows, problem = mn.stock_rows(items, known={item["id"] for item in current["items"]})
        if problem:
            return {"ok": False, "error": problem}

        stock = await self.stock(date)
        if not stock.get("ok") or not stock.get("connected"):
            return {"ok": False, "error": stock.get("error") or "Stok durumu okunamadı."}
        baseline = mn.baseline_of(stock.get("stock") or {})

        proposed = {"capacity_total": mn.opt_int(capacity_total), "items": rows}
        await self._record(action="stock", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={"date": date, "dry_run": True,
                                   "capacity_total": proposed["capacity_total"]})
        try:
            result = await self._api.set_menu_stock(
                date, capacity_total=proposed["capacity_total"], items=rows,
                location_id=self._location, actor=actor, dry_run=True)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="stock", actor=actor, result=FAILED,
                               target_date=date, target_id=current["id"],
                               detail={"date": date, "dry_run": True,
                                       "error": str(failure)})
            return self._write_failure(failure)

        view = mn.stock_result_view(self._record_of(result, "would", "data"))
        token = secrets.token_urlsafe(18)
        await self._save_preview(token, date=date, proposed=proposed, baseline=baseline,
                                 warnings=view["warnings"], actor=actor)
        await self._record(action="stock", actor=actor, result=DRY,
                           target_date=date, target_id=current["id"],
                           detail={"date": date, "warning_count": len(view["warnings"])})
        return {"ok": True, "dry_run": True, "error": "", "token": token,
                "expires_in_minutes": self._preview_ttl, "preview": view,
                "warnings": view["warnings"]}

    async def apply_stock(self, date: str, *, token: str,
                          actor: str) -> dict[str, Any]:
        """Önizlenen tavan tablosunu uygular. GEREKÇE İSTEMEZ.

        JETON ZORUNLU. Tavan yazmak tek adımda yapılabilirdi ama yapılmıyor:
        `PUT stock` TAM LİSTE yazar ve gönderilmeyen kalemin tavanı kalkar.
        Yöneticinin onayladığı tablonun UYGULANAN tablo olduğunu kanıtlayan tek
        şey bu satırdır. Temel çizgi de burada doğrulanır — arada satış
        değiştiyse gördüğü sayılar artık doğru değildir. Jeton + temel çizgi,
        bir gerekçe metninin veremeyeceği bir güvence verir.
        """
        problem = mn.date_error(date)
        if problem:
            return {"ok": False, "error": problem}
        row = await self._load_preview(token)
        if row is None:
            return {"ok": False, "error": ("Önizleme bulunamadı ya da süresi doldu. "
                                           "Tavan tablosunu yeniden hesaplatın.")}
        if mn.text(row.get("menu_date")) != mn.text(date):
            return {"ok": False, "error": "Önizleme başka bir güne ait."}

        proposed = json.loads(row.get("proposed") or "{}")
        baseline = json.loads(row.get("baseline") or "{}")

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        stock = await self.stock(date)
        if not stock.get("ok") or not stock.get("connected"):
            return {"ok": False, "error": stock.get("error") or "Stok durumu okunamadı."}
        drift = mn.baseline_drift(baseline, mn.baseline_of(stock.get("stock") or {}))
        if drift:
            await self._record(action="stock", actor=actor, result=BLOCKED,
                               target_date=date, target_id=current["id"],
                               detail={"date": date, "drift": drift})
            return {"ok": False, "code": "conflict",
                    "error": ("Önizlemeden bu yana satışlar değişti (" + "; ".join(drift)
                              + "). Tavanı yeniden hesaplatın: yarım saat önceki "
                                "sayılara bakarak tavan yazmak, doğru kararı yanlış "
                                "veriye dayandırmak olurdu.")}

        rows, problem = mn.stock_rows(proposed.get("items"),
                                      known={item["id"] for item in current["items"]})
        if problem:
            return {"ok": False, "code": "conflict",
                    "error": f"Önizlenen tablo artık geçerli değil: {problem}"}

        await self._record(action="stock", actor=actor, result=TRIED,
                           target_date=date, target_id=current["id"],
                           detail={"date": date, "dry_run": False, "token": token,
                                   "capacity_total": proposed.get("capacity_total")})
        try:
            result = await self._api.set_menu_stock(
                date, capacity_total=mn.opt_int(proposed.get("capacity_total")), items=rows,
                location_id=self._location, actor=actor, dry_run=False)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="stock", actor=actor, result=FAILED,
                               target_date=date, target_id=current["id"],
                               detail={"date": date, "error": str(failure)})
            return self._write_failure(failure)

        was_dry = self._was_dry(result)
        view = mn.stock_result_view(self._record_of(result, "data", "would"))
        if not was_dry:
            await self._close_preview(token)
        await self._record(action="stock", actor=actor,
                           result=DRY if was_dry else DONE, target_date=date,
                           target_id=current["id"],
                           detail={"date": date, "warning_count": len(view["warnings"])})
        if not was_dry:
            await self._announce(STOCK_EVENT, {
                "date": date, "capacityTotal": mn.opt_int(proposed.get("capacity_total")),
                "warningCount": len(view["warnings"]), "actor": actor})
        return {"ok": True, "error": "", "dry_run": was_dry, "data": view,
                "warnings": view["warnings"],
                "audit_id": mn.as_int((result or {}).get("audit_id"))
                if isinstance(result, dict) else 0}

    # -------------------------------------------------------------- kopyalama

    async def duplicate(self, date: str, *, target_date: str, overwrite: bool,
                        reason: str, actor: str, dry_run: bool) -> dict[str, Any]:
        """Bir günün menüsünü başka bir güne kopyalar.

        Haftalık menü kuran yönetici için tek gerçek zaman kazancı budur.
        Hedef gün HER ZAMAN `draft` doğar, kaynak yayında olsa bile; görsel ve
        iç not kopyalanmaz (güne özgüdürler).
        """
        problem = (self._guard(reason)
                   or mn.duplicate_error(date, target_date, self._today()))
        if problem:
            return {"ok": False, "error": problem}

        current, failed = await self._fresh_day(date)
        if current is None:
            return failed
        if not current["items"]:
            return {"ok": False,
                    "error": ("Kaynak günde kalem yok; kopyalamanın kazandıracağı bir "
                              "şey olmazdı. Önce kaynağa kalem ekleyin.")}

        detail = {"source_date": date, "target_date": target_date,
                  "overwrite": bool(overwrite), "item_count": current["item_count"]}
        await self._record(action="duplicate", reason=reason, actor=actor, result=TRIED,
                           target_date=target_date, target_id=current["id"],
                           detail={**detail, "dry_run": dry_run})
        try:
            result = await self._api.duplicate_menu_day(
                date, target_date=target_date, overwrite=bool(overwrite),
                location_id=self._location, reason=reason, actor=actor, dry_run=dry_run)
        except Exception as failure:  # noqa: BLE001 — K7
            await self._record(action="duplicate", reason=reason, actor=actor,
                               result=FAILED, target_date=target_date,
                               target_id=current["id"],
                               detail={**detail, "error": str(failure)})
            return self._write_failure(failure)

        # DENETİM HEDEFİ HEDEF GÜNDÜR (sözleşme): "bu gün nereden geldi"
        # sorusu hedef tarihle sorulur, kaynak `detail` içinde durur.
        return await self._settle("duplicate", result, reason=reason, actor=actor,
                                  date=target_date, detail=detail)

    # ------------------------------------------------------------- iç yardım

    async def _settle(self, action: str, result: Any, *, actor: str,
                      date: str, reason: str = "", day_id: int = 0,
                      detail: dict[str, Any] | None = None) -> dict[str, Any]:
        """Yazma yanıtını kapatır: izi düşer, ekran zarfını kurar.

        KURU PROVA YANITTAN OKUNUR, İSTEKTEN DEĞİL. Bir kurulum geçidin
        varsayılanını açık bırakırsa ekran "yapıldı" DEMEMELİ; `dry_run`
        alanı sözleşmenin yazma zarfında her zaman var.
        """
        was_dry = self._was_dry(result)
        await self._record(action=action, reason=reason, actor=actor,
                           result=DRY if was_dry else DONE, target_date=date,
                           target_id=day_id, detail=detail or {})
        payload = result if isinstance(result, dict) else {}
        return {"ok": True, "error": "", "dry_run": was_dry,
                "audit_id": mn.as_int(payload.get("audit_id")),
                "data": self._record_of(payload, "data") if not was_dry else {},
                "would": self._record_of(payload, "would") if was_dry else {}}

    @classmethod
    def _price_lock(cls, current: dict[str, Any], fields: dict[str, Any]) -> str:
        """Kesim saati geçmiş güne fiyat yazmayı engelleyen cümle.

        `cutoff_passed` bilgisi gün gövdesinde YOK (takvim satırında var), bu
        yüzden burada yalnız YAYINLANMIŞ gün + geçmiş tarih birleşimi
        yakalanır. Asıl kapı sunucudadır ve 409 verir; buradaki kapı sık
        karşılaşılan hâli anlaşılır bir cümleye çevirir, hepsini değil.
        """
        touches_price = ("package_price_kurus" in fields
                         or "components_sellable" in fields)
        if not touches_price or current.get("status") != mn.PUBLISHED:
            return ""
        if mn.text(current.get("date")) >= cls._today():
            return ""
        return ("Geçmiş ve yayınlanmış bir günün fiyatı değiştirilemez: o gün için "
                "sipariş kapandı ve girmiş siparişler eski fiyattan. Fiyatı "
                "değiştirmek yalnız raporları bozardı.")

    @staticmethod
    def _day_fields(fields: dict[str, Any], *,
                    allow_image: bool = True) -> tuple[dict[str, Any], str]:
        """Gün gövdesini doğrular ve geçide gidecek anahtarları süzer.

        GÖNDERİLMEYEN ANAHTAR SÖZLÜĞE KONMAZ: geçidin `UNSET` nöbetçisi onu
        gövdeye koymaz ve alan BLD'de değişmez. `None` ise konur ve alanı
        temizler — "dokunma" ile "boşalt" ayrımı buradan geçer.

        `allow_image` GÜN KURARKEN KAPALI: geçidin `create_menu_day` imzasında
        `image_path` yok (sözleşme de gün kurulurken görsel almıyor) ve alanı
        sessizce düşürmek yerine reddetmek gerekiyor — düşürülseydi görseli
        gün kurulurken veren bir çağrı, sebebini hiçbir yerde okuyamadan
        görselsiz bir gün üretirdi.
        """
        allowed = ["title", "description", "internal_note", "package_price_kurus",
                   "components_sellable", "cutoff_time", "capacity_total"]
        if allow_image:
            allowed.append("image_path")
        out: dict[str, Any] = {}
        for key in allowed:
            if key not in fields:
                continue
            out[key] = fields[key]
        unknown = sorted(set(fields) - set(allowed))
        if unknown:
            if unknown == ["image_path"]:
                return {}, ("Görsel gün kurulurken verilemez; gün oluştuktan sonra "
                            "düzenleme ucundan yazılır.")
            return {}, (f"Bu alanlar bu uçtan yazılamaz: {', '.join(unknown)}. "
                        "`date`, `location_id` ve `status` bilerek yazılamaz — günü "
                        "taşımak yeni gün kurmaktır, durum değişimi kendi ucundadır.")

        if "title" in out and out["title"] is not None:
            out["title"] = mn.text(out["title"], mn.MAX_TITLE) or None
        if "description" in out and out["description"] is not None:
            out["description"] = mn.text(out["description"], mn.MAX_DESCRIPTION) or None
        if "internal_note" in out and out["internal_note"] is not None:
            out["internal_note"] = mn.text(out["internal_note"], mn.MAX_INTERNAL_NOTE) or None
        if "image_path" in out and out["image_path"] is not None:
            out["image_path"] = mn.text(out["image_path"]) or None

        if "package_price_kurus" in out:
            problem = mn.price_error(out["package_price_kurus"])
            if problem:
                return {}, problem
            out["package_price_kurus"] = mn.opt_int(out["package_price_kurus"])
        if "capacity_total" in out:
            problem = mn.capacity_error(out["capacity_total"])
            if problem:
                return {}, problem
            out["capacity_total"] = mn.opt_int(out["capacity_total"])
        if "cutoff_time" in out:
            problem = mn.cutoff_error(out["cutoff_time"])
            if problem:
                return {}, problem
            out["cutoff_time"] = mn.text(out["cutoff_time"]) or None
        if "components_sellable" in out:
            # BOŞALTILAMAZ. `null` göndermek metin alanlarında "temizle" demek
            # ama bir bayrakta karşılığı yok: `as_bool(None)` sessizce `False`
            # üretir ve kalem satışını kapatırdı — yönetici alanı temizlediğini
            # sanarken o günün bileşen satışını kapatmış olurdu.
            if out["components_sellable"] is None:
                return {}, ("`components_sellable` boşaltılamaz; açık ya da kapalı "
                            "olarak gönderilmeli.")
            out["components_sellable"] = mn.as_bool(out["components_sellable"])
        return out, ""

    @staticmethod
    def _item_fields(fields: dict[str, Any], *,
                     require_menu_id: bool) -> tuple[dict[str, Any], str]:
        """Kalem gövdesini doğrular.

        `menu_id` YALNIZ EKLEMEDE kabul edilir. Ürünü değiştirmek kalemi silip
        yenisini eklemektir ve denetim izinde iki ayrı satır olarak
        görünmelidir — tek satırda "menu_id 12'den 27'ye" yazan bir kayıt,
        çorbanın tavuk sote olduğu bir günü açıklayamaz.
        """
        allowed = ["quantity", "sort_order", "label", "price_override_kurus",
                   "is_required", "sellable_alone", "capacity"]
        if require_menu_id:
            allowed.append("menu_id")
        unknown = sorted(set(fields) - set(allowed))
        if unknown:
            if "menu_id" in unknown:
                return {}, ("Kalemin ürünü değiştirilemez. Ürünü değiştirmek kalemi "
                            "silip yenisini eklemektir ve denetim izinde iki ayrı satır "
                            "olarak görünmelidir.")
            return {}, f"Bu alanlar kalemde yazılamaz: {', '.join(unknown)}."

        out = {key: fields[key] for key in allowed if key in fields}
        if require_menu_id:
            menu_id = mn.as_int(out.get("menu_id"))
            if menu_id <= 0:
                return {}, "Ürün seçilmedi."
            out["menu_id"] = menu_id
        if "quantity" in out:
            if out["quantity"] is None:
                return {}, "Porsiyon adedi boşaltılamaz; bir sayı gönderilmeli."
            problem = mn.quantity_error(out["quantity"])
            if problem:
                return {}, problem
            out["quantity"] = mn.as_int(out["quantity"], 1)
        if "sort_order" in out and out["sort_order"] is not None:
            out["sort_order"] = max(0, mn.as_int(out["sort_order"]))
        if "label" in out and out["label"] is not None:
            out["label"] = mn.text(out["label"], mn.MAX_LABEL) or None
        if "price_override_kurus" in out and out["price_override_kurus"] is not None:
            # SIFIR GEÇERLİDİR: paket içinde satılan ekmek/ayran fiyatsız
            # olabilir. Paket fiyatının aksine burada sıfır bir anlam taşır.
            amount = mn.opt_int(out["price_override_kurus"])
            if amount is None or amount < 0:
                return {}, "Kalem fiyatı tam sayı kuruş olmalı ve negatif olamaz."
            out["price_override_kurus"] = amount
        if "capacity" in out:
            problem = mn.capacity_error(out["capacity"])
            if problem:
                return {}, problem
            out["capacity"] = mn.opt_int(out["capacity"])
        for key in ("is_required", "sellable_alone"):
            if key in out:
                # Bayrak BOŞALTILAMAZ — `null` sessizce `False` olurdu ve
                # "zorunlu kalem" işaretini kaldırmak paketin düşmesini
                # engellemek demek; niyetin açıkça yazılması gerekir.
                if out[key] is None:
                    return {}, f"`{key}` boşaltılamaz; açık ya da kapalı gönderilmeli."
                out[key] = mn.as_bool(out[key])
        return out, ""

    @classmethod
    def _new_items(cls, items: Any) -> tuple[list[dict[str, Any]], str]:
        """Gün kurulurken verilen kalem listesi. BOŞ OLABİLİR — önce günü kur,
        kalemleri sonra ekle akışı sözleşmede açıkça destekleniyor."""
        if items is None:
            return [], ""
        if not isinstance(items, list):
            return [], "Kalem listesi bir dizi olmalı."
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for index, raw in enumerate(items):
            if not isinstance(raw, dict):
                return [], "Kalem listesinin her satırı bir kayıt olmalı."
            clean, problem = cls._item_fields(raw, require_menu_id=True)
            if problem:
                return [], problem
            if clean["menu_id"] in seen:
                return [], ("Aynı ürün iki kez eklenmiş. Bir günde bir ürün tek "
                            "kalem olur (adet `quantity` ile verilir).")
            seen.add(clean["menu_id"])
            clean.setdefault("quantity", 1)
            clean.setdefault("sort_order", (index + 1) * mn.SORT_STEP)
            out.append(clean)
        return out, ""

    # ----------------------------------------------------------- önizlemeler

    async def _save_preview(self, token: str, *, date: str, proposed: dict[str, Any],
                            baseline: dict[str, Any], warnings: list[dict[str, Any]],
                            actor: str, reason: str = "") -> None:
        try:
            await self._store.execute(
                f"INSERT INTO {self._preview} "
                "(token, menu_date, proposed, baseline, warnings, actor, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (token, mn.text(date), json.dumps(proposed, ensure_ascii=False),
                 json.dumps(baseline, ensure_ascii=False),
                 json.dumps(warnings, ensure_ascii=False), actor, mn.text(reason),
                 mn.now_iso()),
            )
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("stok önizlemesi yazılamadı", date=date, error=str(failure))

    async def _load_preview(self, token: str) -> dict[str, Any] | None:
        """Süresi dolmamış, henüz uygulanmamış önizleme satırı."""
        clean = mn.text(token)
        if not clean:
            return None
        try:
            row = await self._store.fetch_one(
                f"SELECT * FROM {self._preview} WHERE token = ?", (clean,))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("stok önizlemesi okunamadı", error=str(failure))
            return None
        if not row or mn.text(row.get("status")) != "dry_run":
            return None
        try:
            created = datetime.fromisoformat(mn.text(row.get("created_at")).replace("Z", "+00:00"))
        except ValueError:
            return None
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        if datetime.now(UTC) - created > timedelta(minutes=self._preview_ttl):
            return None
        return dict(row)

    async def _close_preview(self, token: str) -> None:
        try:
            await self._store.execute(
                f"UPDATE {self._preview} SET status = 'applied', applied_at = ? "
                "WHERE token = ?", (mn.now_iso(), mn.text(token)))
        except Exception as failure:  # noqa: BLE001 — K7
            self._log.warning("önizleme kapatılamadı", error=str(failure))
