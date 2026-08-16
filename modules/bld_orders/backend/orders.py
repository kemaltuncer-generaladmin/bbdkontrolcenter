"""Sipariş Yönetimi — saf yardımcılar. AĞA ÇIKMAZ, DEPOYA YAZMAZ.

Burada yalnız biçim, etiket ve ön denetim var. İş kararı `service.py`'de, HTTP
kapısı `api/routes.py`'de durur; bu dosyanın tamamı yan etkisizdir ve tek tek
sınanabilir.

DURUM MAKİNESİ BURADA YOKTUR VE OLMAYACAKTIR. `OrderStatusTransition` geçiş
matrisi ile 120 saniyelik tek adım geri alma penceresi SUNUCUDADIR
(`BLD/docs/control/orders.md`). `bld_kds` matrisin bir kopyasını taşıyor ve
"ön denetim" diyerek gerekçelendiriyor; bu ekran o kopyayı BİLEREK almadı:

  · İki kopya sessizce ayrışır. Sunucu matrisi gevşerse ekran kullanıcının
    yapabileceği bir geçişi engeller ve engellendiğini SUNUCUYA HİÇ SORMADAN
    söyler — yani hatayı görmenin bir yolu bile kalmaz.
  · Sunucu reddi zaten anlaşılır: `INVALID_TRANSITION` + `details {from,to}`.
    Ekranın işi o cevabı Türkçe bir cümleye çevirmektir, tahmin etmek değil
    (`transition_message`).

Kod İngilizce+ASCII, yorum Türkçe (depo kuralı).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# =========================================================== gerekçe sınırı

#: Gerekçenin en az uzunluğu (`00-genel.md` §3). Arayüzde alanı zorunlu
#: göstermek yetkilendirme değildir (K9); backend'de TEKRAR doğrulanır.
MIN_REASON = 10

#: En çok. Sipariş alanının ÜÇ yazma ucunun üçü de 160 ile sınırlı
#: (`veykemtu_order_revisions.reason` sütunu); `00-genel.md` §3'ün genel 500
#: sınırı burada geçerli DEĞİLDİR. Taşan gerekçe kırpılmaz, sunucu 422 verir —
#: o yüzden burada da kırpmıyoruz, reddediyoruz: kırpılan bir gerekçe denetim
#: kaydına yarım cümle olarak düşerdi.
MAX_REASON = 160


# ================================================================= sözlükler

#: `OrderStatusTransition::CODES` — sözleşmede AÇIKÇA sayılıdır (`orders.md`
#: → "Durum kodları ... uydurulmaz"). Sıra admin panelindeki sıradır.
STATUS_CODES: tuple[str, ...] = (
    "yeni", "onaylandi", "hazirlaniyor", "hazir", "yolda", "teslim_edildi", "iptal",
)

STATUS_LABELS = {
    "yeni": "Yeni",
    "onaylandi": "Onaylandı",
    "hazirlaniyor": "Hazırlanıyor",
    "hazir": "Hazır",
    "yolda": "Yolda",
    "teslim_edildi": "Teslim edildi",
    "iptal": "İptal",
}

#: Ton TEK BAŞINA anlam taşımaz (kit kuralı 7): her rozetin içinde yazı var.
STATUS_TONES = {
    "yeni": "info",
    "onaylandi": "info",
    "hazirlaniyor": "warn",
    "hazir": "good",
    "yolda": "good",
    "teslim_edildi": "dim",
    "iptal": "bad",
}

#: Şeritte (stepper) çizilen ilerleme zinciri. `iptal` ZİNCİRDE DEĞİLDİR:
#: iptal bir aşama değil, zincirin dışına çıkıştır. Şeride konsaydı "yedinci
#: adıma yaklaşıyoruz" gibi okunurdu.
STATUS_CHAIN: tuple[str, ...] = (
    "yeni", "onaylandi", "hazirlaniyor", "hazir", "yolda", "teslim_edildi",
)

#: Buradan ileri gidilemeyen durumlar. Bu bir GEÇİŞ MATRİSİ DEĞİLDİR — yalnız
#: ekranın "bu sipariş kapandı" cümlesini kurabilmesi için. Sözleşme ikisini de
#: adıyla anıyor: `not_editable_reason` = `delivered` · `cancelled`.
TERMINAL_STATUSES = frozenset({"teslim_edildi", "iptal"})

#: `payment_method` — iş kararı 1 (cari hesap KALDIRILDI).
PAYMENT_METHODS: tuple[str, ...] = ("online", "cash")
PAYMENT_METHOD_LABELS = {"online": "Online", "cash": "Nakit"}

#: `payment_status` — sözleşmedeki dört değer.
PAYMENT_STATUSES: tuple[str, ...] = ("pending", "paid", "failed", "refunded")
PAYMENT_STATUS_LABELS = {
    "pending": "Bekliyor",
    "paid": "Ödendi",
    "failed": "Başarısız",
    "refunded": "İade edildi",
}
PAYMENT_STATUS_TONES = {
    "pending": "warn",
    "paid": "good",
    "failed": "bad",
    "refunded": "info",
}

DELIVERY_TYPES: tuple[str, ...] = ("delivery", "pickup")
DELIVERY_TYPE_LABELS = {"delivery": "Adrese teslim", "pickup": "Gel-al"}

#: `source` süzgeci — abonelikten üretilenleri ayırmak için.
SOURCES: tuple[str, ...] = ("all", "manual", "subscription")
SOURCE_LABELS = {"all": "Hepsi", "manual": "Elle verilen", "subscription": "Abonelikten"}

#: `not_editable_reason` değerleri (sözleşme). `null` = düzenlenebilir.
NOT_EDITABLE_LABELS = {
    "delivered": "Teslim edilmiş sipariş düzenlenemez.",
    "cancelled": "İptal edilmiş sipariş düzenlenemez.",
}

# =============================================================== geri alma

#: `OrderStatusTransition::UNDO_WINDOW_SECONDS`. Burada YALNIZ METİN İÇİN
#: durur ("120 saniye" yazabilmek ve geri sayımı sınırlamak için); geri almanın
#: mümkün olup olmadığına KARAR VERMEZ — o karar sunucunundur ve buradan
#: hesaplanamaz (son durum değişikliğinin anı yanıtta ayrı bir alan değil).
UNDO_WINDOW_SECONDS = 120

#: Sunucudan OKUNAN geri alma alanları. **Sözleşmede tanımlı değiller**
#: (`orders.md` geri alma penceresinden söz ediyor ama `GET /{order}` gövdesinde
#: böyle bir alan saymıyor) ve BU YÜZDEN uydurulmadılar: yalnız bu iki ad
#: okunur, başka bir ad gelirse ekran "bilinmiyor" der ve geri alma düğmesini
#: HİÇ ÇİZMEZ. Uydurulmuş bir geri sayım, dolmuş bir pencereyi açık gösterir ve
#: personel 422 yerken kendini yavaş sanardı.
UNDO_FLAG_KEY = "can_undo"
UNDO_UNTIL_KEY = "undo_until"


# ================================================================= yardımcı

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def text(value: Any) -> str:
    """Değeri kırpılmış metne çevirir. `None` boş dizedir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    """Tamsayıya çevirir; çevrilemiyorsa `default`. İstisna atmaz."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    """ÜÇ DURUMLU. `None` "sunucu söylemedi" demektir ve KORUNUR.

    `bool(None)` yazmak burada pahalıya patlardı: "geri alınabilir mi bilinmiyor"
    ile "geri alınamaz" aynı şey değil — ilki düğmeyi hiç çizdirmez, ikincisi
    nedenini yazan kapalı bir düğme çizdirir.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = text(value).lower()
    if lowered in {"1", "true", "evet", "yes", "on"}:
        return True
    if lowered in {"0", "false", "hayir", "hayır", "no", "off"}:
        return False
    return None


def now_iso() -> str:
    """Yerel iz için zaman damgası. UZAK VERİ DAMGALANMAZ — o sunucudan gelir."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 Zulu damgasını okur. Okunamazsa `None` — istisna atmaz."""
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def seconds_between(later: Any, earlier: Any) -> float | None:
    """İki damga arasındaki saniye. Biri okunamıyorsa `None`."""
    end = parse_iso(later)
    start = parse_iso(earlier)
    if end is None or start is None:
        return None
    return (end - start).total_seconds()


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    clean = text(value)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı; "
                "denetim kaydına bu metin yazılır.")
    if len(clean) > MAX_REASON:
        return (f"Gerekçe en çok {MAX_REASON} karakter olabilir "
                "(sunucu sütunu bu uzunlukta; taşan gerekçe kırpılmaz, reddedilir).")
    return ""


def date_error(value: Any, *, field: str) -> str:
    """Tarih `YYYY-MM-DD` mi? Boş değer serbesttir (süzgeç verilmemiş demektir).

    Biçim BURADA da denetlenir çünkü geçit bozuk tarihi `payload` koduyla
    reddediyor ve o hata ekranda "uç sözleşmede yok" gibi okunurdu.
    """
    raw = text(value)
    if not raw:
        return ""
    if not _DATE_ONLY.match(raw):
        return f"{field} `YYYY-MM-DD` biçiminde olmalı; '{raw}' verildi."
    return ""


def status_filter(value: Any) -> tuple[list[str], str]:
    """Virgüllü durum süzgecini KANONİK listeye çevirir. `(kodlar, hata)`.

    TANINMAYAN KOD REDDEDİLİR, sessizce elenmez. Sunucu tanımadığı kodu
    süzgece koyar ve sonuç boş döner; ekran o boşluğu "bu aralıkta sipariş yok"
    diye gösterirdi. Yazım hatasını erken ve açıkça söylemek, doğru görünen boş
    bir listeden iyidir.
    """
    if value is None:
        return [], ""
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple | set):
        parts = [text(item) for item in value]
    else:
        return [], "Durum süzgeci okunamadı."

    wanted = [item for item in parts if item]
    unknown = sorted({item for item in wanted if item not in STATUS_CODES})
    if unknown:
        return [], (f"Tanınmayan sipariş durumu: {', '.join(unknown)}. "
                    f"Tanınanlar: {', '.join(STATUS_CODES)}.")
    # Sıra sözleşmedeki sıraya çekilir ve tekrarlar düşer: aynı süzgecin iki
    # farklı yazımı aynı isteği üretsin ki önbellek ve günlük okunur kalsın.
    return [code for code in STATUS_CODES if code in set(wanted)], ""


def choice_error(value: Any, allowed: tuple[str, ...], *, field: str) -> str:
    """Kapalı listeli süzgeç denetimi. Boş değer serbesttir."""
    raw = text(value)
    if not raw or raw in allowed:
        return ""
    return f"{field} yalnız şunlardan biri olabilir: {', '.join(allowed)}."


# ============================================================ satır biçimi

def order_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Sipariş satırı — YALNIZ ETİKET EKLER, hiçbir alanı ayıklamaz.

    Sözleşme additive büyüyor (`AGENTS.md` §2.3): bilinen alanları seçip
    gerisini atan bir dönüşüm, sunucuya eklenen her yeni alanı SESSİZCE
    düşürürdü. Satır olduğu gibi geçer; üzerine yalnız ekranın kendi etiketleri
    eklenir ve hepsi `*_label` / `*_tone` ile adlandırılır ki sözleşmeden gelen
    alanla karışmasın.
    """
    status = text(raw.get("status"))
    method = text(raw.get("payment_method"))
    pay_status = text(raw.get("payment_status"))
    delivery = text(raw.get("delivery_type"))
    return {
        **raw,
        "status_label": STATUS_LABELS.get(status, status or "—"),
        "status_tone": STATUS_TONES.get(status, "dim"),
        "payment_method_label": PAYMENT_METHOD_LABELS.get(method, method or "—"),
        "payment_status_label": PAYMENT_STATUS_LABELS.get(pay_status, pay_status or "—"),
        "payment_status_tone": PAYMENT_STATUS_TONES.get(pay_status, "dim"),
        "delivery_type_label": DELIVERY_TYPE_LABELS.get(delivery, delivery or "—"),
        # Sözleşmede `is_subscription` var ama abonelikten üretilmiş bir siparişi
        # yalnız `subscription_id` ile bildiren bir sunucu sürümü de aynı şeyi
        # söylüyor; ikisinden biri doluysa satır abonelik satırıdır.
        "from_subscription": bool(raw.get("is_subscription")) or as_int(
            raw.get("subscription_id")) > 0,
        "is_terminal": status in TERMINAL_STATUSES,
    }


def editable_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Düzenlenebilir görünüm — `OrderPresenter::editable()` çıktısı + etiket.

    `editable` alanı sunucudan gelir ve BURADA HESAPLANMAZ: "teslim edilmiş mi"
    sorusunu duruma bakarak cevaplamak, sunucunun ileride ekleyeceği başka bir
    kilidi (ödeme bekleyen sipariş, kapalı gün) görmezden gelmek olurdu.
    """
    row = order_row(raw)
    editable = as_bool(raw.get("editable"))
    why = text(raw.get("not_editable_reason"))
    return {
        **row,
        # `None` = sunucu söylemedi. O durumda ekran düzenlemeyi AÇMAZ ve
        # nedenini yazar: bilinmeyen bir kilidi açık saymak, teslim edilmiş bir
        # siparişe revizyon yazdırıp 422 aldırmaktı.
        "editable": editable,
        "not_editable_label": NOT_EDITABLE_LABELS.get(why, why),
    }


def revision_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Revizyon geçmişi satırı. Para KURUŞ gelir ve KURUŞ kalır.

    `created_by_device_id` `null` ise revizyon MERKEZDEN geldi, dolu ise mutfak
    kasasından (sözleşme). Ayrımı ekranda göstermek şart: "kim değiştirdi"
    sorusunun cevabı iki ayrı yer.
    """
    device_id = raw.get("created_by_device_id")
    from_center = device_id in (None, "", 0)
    return {
        "revision_no": as_int(raw.get("revision_no")),
        "reason": text(raw.get("reason")),
        "note": text(raw.get("note")) or None,
        "refund_kurus": as_int(raw.get("refund_kurus")),
        "extra_charge_kurus": as_int(raw.get("extra_charge_kurus")),
        "created_by_device_id": None if from_center else as_int(device_id),
        "created_at": text(raw.get("created_at")) or None,
        "origin": "control" if from_center else "kitchen",
        "origin_label": "Kontrol Merkezi" if from_center else "Mutfak kasası",
    }


def invoice_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Fatura künyesi. Belge ÜRETİLMEZ, yalnız var olana bakılır (sözleşme)."""
    return {
        "id": as_int(raw.get("id")),
        "invoice_no": text(raw.get("invoice_no")) or None,
        "order_id": as_int(raw.get("order_id")),
        "status": text(raw.get("status")) or None,
        "issued_at": text(raw.get("issued_at")) or None,
        "total_kurus": as_int(raw.get("total_kurus")),
        "html_url": text(raw.get("html_url")) or None,
    }


def revision_items(items: Any) -> tuple[list[dict[str, Any]], str]:
    """Revizyon kalemlerini DENETLER, ayıklamaz. `(kalemler, hata)` döner.

    TAM LİSTE GÖNDERİLİR, KALEM FARKI DEĞİL: gönderilen liste siparişin YENİ
    hâlidir. Panelin "değişmeyeni göndermeye gerek yok" diye yalnız değişenleri
    yollaması siparişi BOŞALTIRDI.

    KALEM OLDUĞU GİBİ GEÇER. Bilinen dört alanı seçip gerisini atmak
    `option_value_ids`'i düşürürdü: "ekstra peynir" silinir, sipariş ucuzlar,
    mutfak yanlış yemeği yapar ve hata hiçbir yerde görünmez. Denetim yalnız
    zorunlu alanların varlığını ve sınırlarını doğrular.
    """
    if not isinstance(items, list) or not items:
        return [], ("Revizyon siparişin TAM kalem listesini ister; boş liste "
                    "siparişi boşaltır. Siparişi kapatmak için iptal ucu kullanılır.")

    clean: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return [], f"{index}. kalem okunamadı."
        if as_int(item.get("menu_id")) < 1:
            return [], f"{index}. kalemde ürün (menu_id) eksik."
        quantity = as_int(item.get("quantity"))
        if quantity < 1 or quantity > 999:
            return [], f"{index}. kalemin adedi 1-999 arasında olmalı."

        options = item.get("option_value_ids")
        if options is not None:
            if not isinstance(options, list):
                return [], f"{index}. kalemin seçenek listesi okunamadı."
            if any(as_int(option) < 1 for option in options):
                return [], f"{index}. kalemde geçersiz seçenek kimliği."

        if len(text(item.get("note"))) > 255:
            return [], f"{index}. kalemin notu en çok 255 karakter olabilir."

        clean.append(dict(item))

    return clean, ""


# ============================================================== geri alma

def undo_view(order: dict[str, Any], *, server_time: Any = "") -> dict[str, Any]:
    """Geri alma penceresini SUNUCUDAN okur; hesaplamaz, tahmin etmez.

    `known: False` "sunucu söylemedi" demektir ve ekran geri alma düğmesini HİÇ
    ÇİZMEZ — nedenini yazar. Pencereyi buradan hesaplamak mümkün değil: son
    durum değişikliğinin ANI yanıtta ayrı bir alan olarak yok, `updated_at` ise
    revizyonla da ilerliyor. Yani "updated_at + 120 sn" bir revizyondan sonra
    hiç var olmamış bir geri alma penceresi gösterirdi.

    `seconds_left` yalnız SUNUCUNUN VERDİĞİ son tarihten çıkar ve taban
    `server_time`'dır (`00-genel.md` §6: "panel geri sayımları bunu temel alır;
    istemcinin saati kaymış olabilir").
    """
    flag = as_bool(order.get(UNDO_FLAG_KEY))
    until = text(order.get(UNDO_UNTIL_KEY))

    if flag is None and not until:
        return {
            "known": False,
            "can_undo": False,
            "until": None,
            "seconds_left": None,
            "window_seconds": UNDO_WINDOW_SECONDS,
            "reason": (f"Sunucu bu sipariş için `{UNDO_FLAG_KEY}` alanını "
                       "göndermiyor; geri alma penceresinin açık olup olmadığı "
                       "buradan bilinemez. Yanlış geçiş, izin verilen bir "
                       "sonraki adımla düzeltilir."),
        }

    left: float | None = None
    if until:
        gap = seconds_between(until, server_time or now_iso())
        if gap is not None:
            # Negatif kalan yazılmaz: dolmuş bir pencere "-8 sn" değil, kapalıdır.
            left = max(0.0, min(float(UNDO_WINDOW_SECONDS), gap))

    can = bool(flag) if flag is not None else bool(left)
    if left == 0.0:
        can = False

    return {
        "known": True,
        "can_undo": can,
        "until": until or None,
        "seconds_left": None if left is None else int(left),
        "window_seconds": UNDO_WINDOW_SECONDS,
        "reason": "" if can else "Geri alma penceresi kapalı.",
    }


# =============================================================== hata dili

def transition_hint(current: Any, target: Any) -> str:
    """Reddedilen bir durum geçişinin YANINA yazılan açıklama.

    Sunucu `INVALID_TRANSITION` + `details {"from","to"}` döndürüyor
    (`00-genel.md` §7.2) ama geçit `error.details` bloğunu TAŞIMIYOR: elimizde
    yalnız `code="validation"` ve Türkçe mesaj var. Bu yüzden ayrıntıyı
    sunucudan okumak yerine, EKRANIN KENDİ BİLDİĞİ iki değeri (isteğin çıktığı
    an okunan durum + hedef) cümleye ekliyoruz. Matris hâlâ kopyalanmıyor:
    burada hangi geçişin geçerli olduğuna dair TEK BİR İDDİA yok.
    """
    source = text(current)
    wanted = text(target)
    if not source or not wanted:
        return ""
    return (f"Sipariş istek anında '{STATUS_LABELS.get(source, source)}' "
            f"durumundaydı ve '{STATUS_LABELS.get(wanted, wanted)}' istendi; "
            "izin verilen geçişleri sunucu belirler.")


def cancel_result(payload: Any) -> dict[str, Any]:
    """İptal yanıtının `data` bloğunu eksiksiz bir iskelete oturtur.

    `stock_released` İPTALİN EN ÖNEMLİ YAN ETKİSİDİR: düşen porsiyonlar gün
    toplamı ve ürün tavanından geri gelir, yani o kadar sipariş yeniden
    alınabilir. Ekran bunu göstermezse yönetici "neden birden 12 yer açıldı"
    diye sorar. Bu yüzden alan eksik gelse bile iskelette DURUR ve panel
    `undefined` okumaz.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else {}
    stock = data.get("stock_released")
    stock = stock if isinstance(stock, dict) else {}
    items = stock.get("items")
    items = items if isinstance(items, list) else []

    warnings = payload.get("warnings") if isinstance(payload, dict) else None
    return {
        "refund_kurus": as_int(data.get("refund_kurus")),
        "refund_created": bool(data.get("refund_created")),
        "sms_sent": bool(data.get("sms_sent")),
        "stock_released": {
            "day": as_int(stock.get("day")),
            "items": [
                {"menu_id": as_int(item.get("menu_id")),
                 "quantity": as_int(item.get("quantity"))}
                for item in items if isinstance(item, dict)
            ],
        },
        "warnings": [text(item) for item in warnings if text(item)]
        if isinstance(warnings, list) else [],
    }


# ================================================================ sözleşme

def screen_contract() -> dict[str, Any]:
    """Panelin süzgeç şeridini ve rozetlerini çizmek için okuduğu sözlük.

    YEREL VE AĞSIZ: geçit düşse bile süzgeçler, etiketler ve gerekçe sınırları
    çizilebilir (K7). Panelin bu listeleri kendi içinde tutması, sözleşme
    değiştiğinde iki yerde düzeltme demekti.
    """
    return {
        "statuses": [
            {"code": code, "label": STATUS_LABELS[code], "tone": STATUS_TONES[code],
             "in_chain": code in STATUS_CHAIN, "terminal": code in TERMINAL_STATUSES}
            for code in STATUS_CODES
        ],
        "chain": list(STATUS_CHAIN),
        "payment_methods": [
            {"code": code, "label": PAYMENT_METHOD_LABELS[code]} for code in PAYMENT_METHODS
        ],
        "payment_statuses": [
            {"code": code, "label": PAYMENT_STATUS_LABELS[code],
             "tone": PAYMENT_STATUS_TONES[code]}
            for code in PAYMENT_STATUSES
        ],
        "delivery_types": [
            {"code": code, "label": DELIVERY_TYPE_LABELS[code]} for code in DELIVERY_TYPES
        ],
        "sources": [{"code": code, "label": SOURCE_LABELS[code]} for code in SOURCES],
        "reason": {"min": MIN_REASON, "max": MAX_REASON},
        "undo_window_seconds": UNDO_WINDOW_SECONDS,
    }
