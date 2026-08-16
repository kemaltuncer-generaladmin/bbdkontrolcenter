"""İade verisinin saf dönüşümleri ve İADE TUTARI HESABI — ağa çıkmaz, durum
tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın tek işi para: kaç kalem, hangi adet, KDV dahil mi,
kargo geri veriliyor mu. Hesap servise gömülseydi tek satırı bile ağsız test
edilemezdi; burada hepsi girdi→çıktı fonksiyonudur.

YEDİ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. İki ayrı kayıt, tek olay. Bagisto'nun kredi notu PARAYI, BBD'nin RMA
    talebi SÜRECİ (sebep, kargo, durum) tutar → `merge_rows` ikisini
    birleştirir; aynı sipariş iki satır olarak görünmez.
 2. İade edilebilir adet sipariş adedi DEĞİLDİR → `order_view` faturalanan
    adetten iade edileni düşer. Parası alınmamış kalemi iade etmek parayı
    ikinci kez çıkarır.
 3. İndirim ve KDV kalemin tamamına yazılıdır; kısmi iadede ORANTILI pay
    alınır → `share` (yarım kuruş yukarı, `float` yok).
 4. Kargo bedeli otomatik iade EDİLMEZ; karar her iadede verilir ve daha önce
    iade edilmiş kargo ikinci kez verilmez → `compute`.
 5. Hesabın doğrusu mağazadadır ama personel bizim satırlarımızı görür →
    `compare` ikisini karşılaştırır; fark varsa ekran SUSMAZ.
 6. Mağaza durum adları sürüme göre değişir → `status_of` bilmediğini
    "Bilinmiyor" der, rastgele bir duruma eşlemez.
 7. Para telde ONDALIK, içeride KURUŞ → `to_kurus` / `from_kurus`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

# ============================================================ durum sözlüğü

ST_REQUESTED = "requested"
ST_APPROVED = "approved"
ST_AWAITING = "awaiting"
ST_RECEIVED = "received"
ST_REFUNDED = "refunded"
ST_REJECTED = "rejected"
ST_OTHER = "other"

STATUS_LABELS = {
    ST_REQUESTED: "Talep",
    ST_APPROVED: "Onaylandı",
    ST_AWAITING: "Ürün bekleniyor",
    ST_RECEIVED: "Ürün geldi",
    ST_REFUNDED: "İade edildi",
    ST_REJECTED: "Reddedildi",
    ST_OTHER: "Bilinmiyor",
}

#: Ekrandaki çip sırası — iş akışının sırası.
STATUS_ORDER = (ST_REQUESTED, ST_APPROVED, ST_AWAITING, ST_RECEIVED,
                ST_REFUNDED, ST_REJECTED, ST_OTHER)

#: Parası HENÜZ ÇIKMAMIŞ durumlar. "Bekleyen tutar" KPI'ı bunları toplar.
OPEN_STATUSES = (ST_REQUESTED, ST_APPROVED, ST_AWAITING, ST_RECEIVED)

#: Mağazadan gelen serbest metin → bizim altı durumumuz.
#:
#: `closed` BİLEREK YOK: kapanmış bir talep iade edilmiş de olabilir
#: reddedilmiş de. İkisinden birini seçmek, ekranda yanlış tutarı "iade
#: edildi" sütununa yazardı; bilinmeyen durum bilinmeyen kalır (TUZAK 6).
STATUS_ALIASES = {
    "requested": ST_REQUESTED, "request": ST_REQUESTED, "pending": ST_REQUESTED,
    "new": ST_REQUESTED, "open": ST_REQUESTED, "talep": ST_REQUESTED,
    "approved": ST_APPROVED, "accepted": ST_APPROVED, "onaylandi": ST_APPROVED,
    "awaiting": ST_AWAITING, "awaiting_item": ST_AWAITING, "item_pending": ST_AWAITING,
    "in_transit": ST_AWAITING, "shipped": ST_AWAITING,
    "received": ST_RECEIVED, "item_received": ST_RECEIVED, "delivered": ST_RECEIVED,
    "refunded": ST_REFUNDED, "completed": ST_REFUNDED, "complete": ST_REFUNDED,
    "paid": ST_REFUNDED,
    "rejected": ST_REJECTED, "declined": ST_REJECTED, "denied": ST_REJECTED,
    "canceled": ST_REJECTED, "cancelled": ST_REJECTED,
    # CANLIDA DOĞRULANDI (2026-08-16, `GET /api/admin/bbd/return-requests`
    # yanıtının `meta.statuses` sözlüğü): mağazadaki `rma_statuses` satırları
    # TÜRKÇELEŞTİRİLMİŞ durumda (2026_07_21_110000 göçü) ve uç durumu ADIYLA
    # veriyor. Bir dönem yalnız İngilizce adlar aranıyordu; o yüzden CANLI
    # talebin HEPSİ "Bilinmiyor" çipine düşüyordu. Aşağıdaki adlar tahmin
    # değil, mağazanın kendi sözlüğünden okunmuştur.
    "inceleniyor": ST_REQUESTED,
    "onaylandı": ST_APPROVED,
    "iade_bekleniyor": ST_AWAITING,
    "kargoda": ST_AWAITING,
    "iade_edildi": ST_REFUNDED,
    "reddedildi": ST_REJECTED,
    "iptal_edildi": ST_REJECTED,
    "ürün_iptal_edildi": ST_REJECTED,
    # "Çözüldü" (`Solved`) BİLEREK YOK — `closed` ile aynı gerekçe: çözülmüş
    # talebin parası geri verildi mi, değişimle mi kapandı belli değil.
}

# ============================================================= sebep · yöntem

REASON_LABELS = {
    "disliked": "Beğenmedi",
    "defective": "Ayıplı",
    "wrong_item": "Yanlış ürün",
    "late": "Geç teslim",
    "withdrawal": "Cayma hakkı",
    "other": "Diğer",
}

REASON_ALIASES = {
    "disliked": "disliked", "dislike": "disliked", "not_liked": "disliked",
    "defective": "defective", "damaged": "defective", "broken": "defective",
    "faulty": "defective", "ayipli": "defective",
    "wrong_item": "wrong_item", "wrong": "wrong_item", "wrong_product": "wrong_item",
    "late": "late", "late_delivery": "late", "delayed": "late",
    "withdrawal": "withdrawal", "cooling_off": "withdrawal", "cayma": "withdrawal",
    "other": "other",
    # CANLIDA DOĞRULANDI (2026-08-16): `rma_reasons` da Türkçeleştirilmiş ve uç
    # sebebi SERBEST METİN olarak veriyor. Bu beş başlık mağazanın kendi
    # sözlüğüdür; eşlenmeyeni `reason_label` ham metniyle gösterir, uydurmaz.
    "üretim_hatası": "defective",
    "kargoda_hasar_görmüş": "defective",
    "bozuk/kullanılamaz_geldi": "defective",
    "ürün_açıklaması_yanlış": "wrong_item",
    "ürün_elime_ulaşmadı": "late",
}

METHOD_LABELS = {
    "pos": "POS iadesi",
    "transfer": "Havale",
    "credit": "Mağaza kredisi",
    "exchange": "Değişim",
    "unknown": "Belirtilmedi",
}

METHOD_ALIASES = {
    "pos": "pos", "card": "pos", "credit_card": "pos", "cc": "pos", "iyzico": "pos",
    "paytr": "pos", "virtual_pos": "pos",
    "transfer": "transfer", "bank": "transfer", "banktransfer": "transfer",
    "money_transfer": "transfer", "eft": "transfer", "havale": "transfer",
    "credit": "credit", "store_credit": "credit", "wallet": "credit",
    "exchange": "exchange", "replacement": "exchange", "degisim": "exchange",
}

# ===================================================================== temel


def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return default


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar. Ürün listesinde
    binde bir görünen bu sapma, iade ekranında mutabakatı bozan bir kuruşluk
    fark demektir.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "").replace("₺", "")
    if raw == "":
        return None
    negative = raw.startswith("-")
    raw = raw.lstrip("+-")
    # Makine ondalığı nokta kullanır; virgül ya ondalık ayracıdır (tek başına)
    # ya da binlik (noktayla birlikte). İkisi de doğru okunur.
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    kurus = int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return -kurus if negative else kurus


def from_kurus(kurus: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: cayma süresi ve "bu ay iade"
    mağazanın takvim gününe göre işler; gece yarısından sonraki üç saatte UTC
    bir gün geride kalır ve ayın ilk iadesi geçen aya yazılırdı.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def month_bounds(today: str = "") -> tuple[str, str]:
    """İçinde bulunulan ayın ilk ve son günü (ISO)."""
    day = date.fromisoformat(today or today_iso())
    first = day.replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return first.isoformat(), (nxt - timedelta(days=1)).isoformat()


def days_between(start: str, end: str) -> int | None:
    """İki ISO gün arasındaki gün farkı. Çözülemezse `None` — sıfır DEĞİL."""
    try:
        return (date.fromisoformat(end[:10]) - date.fromisoformat(start[:10])).days
    except (TypeError, ValueError):
        return None


def iso_day(value: Any) -> str:
    """Metni ISO güne (YYYY-MM-DD) çevirir; çözülemezse BOŞ döner.

    TUZAK: uç noktaya `dateTo=abc` gelebilir (şema yalnız uzunluğa bakıyor).
    Ham metni `date.fromisoformat`'a vermek `ValueError` fırlatıp ekranı 500 ile
    düşürüyordu; burada boşa çevrilir ve çağıran varsayılan pencereye döner (K7).
    """
    raw = text(value)[:10]
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def pick(raw: Any, *names: str) -> Any:
    """Sözlükten ilk DOLU alanı seçer.

    Mağaza tek bir yazım kullanmıyor: Bagisto çekirdeği camelCase (`grandTotal`),
    BBD uçları ise ikisini karışık veriyor — aynı yanıtta `order_id` ile
    `orderIncrementId` yan yana durur (CANLIDA DOĞRULANDI 2026-08-16,
    `GET /api/admin/bbd/return-requests`). Tek ada bağlanmak, alanın adı
    farklıysa hata değil SESSİZLİK üretir: sütun boş görünür ve kimse fark
    etmez. Bu yüzden her okuma iki yazımı da dener.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    for name in names:
        if name in raw:
            return raw[name]
    return None


def share(total: int, part: int, whole: int) -> int:
    """`total` kuruşun `part/whole` payı — yarım kuruş YUKARI (TUZAK 3).

    Kısmi iadede indirim ve KDV kalemin tamamına yazılıdır; 3 adetten 1'ini
    iade ederken üçte biri geri verilir. `float` ile bölmek kuruş kaydırıyor,
    mağazanın kendi hesabıyla mutabakatı bozuyordu.
    """
    if whole <= 0 or part <= 0 or not total:
        return 0
    if part >= whole:
        return int(total)
    value = (Decimal(int(total)) * part / whole).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(value)


# ================================================================== eşleme

#: Türkçe "İ" küçültülünce "i" + BİRLEŞEN NOKTA (U+0307) olur: `"İade".lower()`
#: → `"i̇ade"`. Sözlüğe düz `"iade_edildi"` yazan eşleşmeyi sessizce kaçırırdı;
#: normalleştirme bu noktayı atar.
_COMBINING_DOT = "\u0307"


def slug(value: Any) -> str:
    """Sözlük araması için anahtar: küçük harf, boşluk/tire yerine alt çizgi."""
    return (text(value).lower().replace(_COMBINING_DOT, "")
            .replace(" ", "_").replace("-", "_"))


def status_text(value: Any) -> str:
    """Durumun ADI. Sözlük de düz metin de kabul edilir.

    CANLIDA DOĞRULANDI (2026-08-16, `GET /api/admin/bbd/return-requests`):
    durum SÖZLÜK gelir — `{"id": 5, "title": "İade Edildi", "color": "#0d9488"}`.
    Bir dönem düz metin bekleniyordu; sözlüğü `str()`'e vermek ekrandaki rozete
    ham Python sözlüğünü yazıyordu ("Bilinmiyor ({'id': 5, ...})").
    """
    if isinstance(value, dict):
        return text(pick(value, "title", "name", "label", "code", "status"))
    return text(value)


def status_of(value: Any) -> str:
    """Mağaza durumu → bizim altı durumumuz. Tanınmayan `other` olur (TUZAK 6)."""
    return STATUS_ALIASES.get(slug(status_text(value)), ST_OTHER)


def status_label(key: str, raw: str = "") -> str:
    if key == ST_OTHER and text(raw):
        return f"Bilinmiyor ({text(raw)})"
    return STATUS_LABELS.get(key, STATUS_LABELS[ST_OTHER])


def reason_of(value: Any) -> str:
    key = slug(value)
    return REASON_ALIASES.get(key, "other" if key else "")


def reason_label(key: str, raw: str = "") -> str:
    """Sebep etiketi. Eşlenemeyen sebepte MAĞAZANIN KENDİ METNİ gösterilir.

    Sebep listesi mağaza yöneticisinden değişebilir; eşlenmeyeni yalnız "Diğer"
    diye göstermek, operatörden müşterinin gerçekte yazdığı gerekçeyi saklardı.
    """
    label = REASON_LABELS.get(key, "")
    if key == "other" and text(raw):
        return f"Diğer ({text(raw)})"
    return label


def method_of(value: Any) -> str:
    key = slug(value)
    if not key:
        return "unknown"
    if key in METHOD_ALIASES:
        return METHOD_ALIASES[key]
    # Ödeme yöntemi adı serbest metin ("Kredi Kartı (Iyzico)"); parça arar.
    for needle, target in METHOD_ALIASES.items():
        if needle in key:
            return target
    return "unknown"


def customer_of(raw: dict[str, Any]) -> str:
    """Müşteri adı — CANLI alan adları camelCase'tir.

    Kredi notu listesi `customerName`/`billedTo`, sipariş detayı
    `customerFirstName`/`customerLastName` ve (varsa) gömülü `customer.name`
    verir; misafir siparişte gömülü `customer` NULL gelir. Yalnız snake_case
    okumak tabloyu isimsiz bırakıyordu — hiçbiri tek başına yeterli değil.
    """
    name = text(pick(raw, "customer_full_name", "customerFullName", "customer_name",
                     "customerName", "billed_to", "billedTo"))
    if name:
        return name
    nested = pick(raw, "customer")
    if isinstance(nested, dict):
        joined = (f"{text(pick(nested, 'first_name', 'firstName'))} "
                  f"{text(pick(nested, 'last_name', 'lastName'))}").strip()
        found = joined or text(pick(nested, "name", "email"))
        if found:
            return found
    first = text(pick(raw, "customer_first_name", "customerFirstName"))
    last = text(pick(raw, "customer_last_name", "customerLastName"))
    return f"{first} {last}".strip()


# ============================================================== liste satırı

def refund_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Bagisto kredi notu → tablo satırı. Bu satır PARAYI temsil eder.

    CANLIDA DOĞRULANDI (2026-08-13, son ölçüm 2026-08-16, `/api/admin/refunds`):
    yanıt camelCase'tir, gömülü `order` nesnesi YOKTUR ve liste ucunda `items`
    HER ZAMAN BOŞTUR (kalemler yalnız `/refunds/{id}` detayında gelir). Sipariş
    numarası `orderIncrementId`, müşteri `customerName`/`billedTo`, sipariş
    tarihi `orderDate`, adet `totalQty` alanlarındadır. Yalnız snake_case +
    gömülü `order` okumak tabloyu "—" ile dolduruyordu; iki yazım da kabul
    edilir çünkü Bagisto sürüm yükseltmelerinde alan adını değiştirebiliyor ve
    yanlış ad hata değil BOŞ SÜTUN üretir.
    """
    order = pick(raw, "order")
    order = order if isinstance(order, dict) else {}
    refund_id = as_int(raw.get("id"))
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    payment = pick(order, "payment")
    payment_title = text(pick(payment, "method_title", "method")) if isinstance(payment, dict) \
        else ""
    if not payment_title:
        payment_title = text(
            pick(raw, "payment_title", "paymentTitle", "payment_method", "paymentMethod")
            or pick(order, "payment_title", "paymentTitle", "payment_method", "paymentMethod"))
    method = method_of(payment_title)

    created = text(pick(raw, "created_at", "createdAt"))
    return {
        "key": f"refund-{refund_id}",
        "source": "refund",
        "id": refund_id,
        "requestId": 0,
        # Bagisto kredi notuna seri numarası vermez; ekranda "#8" görünür.
        "number": text(pick(raw, "increment_id", "incrementId")) or f"#{refund_id}",
        "orderId": as_int(pick(raw, "order_id", "orderId") or order.get("id")),
        "orderNumber": text(pick(raw, "order_increment_id", "orderIncrementId")
                            or pick(order, "increment_id", "incrementId")),
        "createdAt": created[:19],
        "date": created[:10],
        "customer": customer_of(raw) or customer_of(order),
        "email": text(pick(raw, "customer_email", "customerEmail")
                      or pick(order, "customer_email", "customerEmail")),
        # Liste ucu kalemleri vermiyor; `totalQty` iade edilen ADETtir. Sütun
        # bu yüzden "Adet" diye etiketlenir — "kalem" demek yanlış olurdu.
        "itemCount": len(items) or as_int(pick(raw, "total_qty", "totalQty")),
        "itemNames": ", ".join(text(pick(item, "name")) for item in items
                               if isinstance(item, dict))[:160],
        "amount": to_kurus(pick(raw, "grand_total", "grandTotal")) or 0,
        "subTotal": to_kurus(pick(raw, "sub_total", "subTotal")) or 0,
        "taxAmount": to_kurus(pick(raw, "tax_amount", "taxAmount")) or 0,
        "shippingAmount": to_kurus(pick(raw, "shipping_amount", "shippingAmount")) or 0,
        "discountAmount": to_kurus(pick(raw, "discount_amount", "discountAmount")) or 0,
        "adjustmentRefund": to_kurus(pick(raw, "adjustment_refund", "adjustmentRefund")) or 0,
        "adjustmentFee": to_kurus(pick(raw, "adjustment_fee", "adjustmentFee")) or 0,
        # Kredi notu VAR demek para çıktı demektir; başka durumu olamaz.
        "status": ST_REFUNDED,
        "statusLabel": STATUS_LABELS[ST_REFUNDED],
        "statusRaw": "",
        "reason": "",
        "reasonLabel": "",
        "method": method,
        "methodLabel": METHOD_LABELS[method],
        "carrier": "",
        "tracking": "",
        "posState": "",
        "posMessage": "",
        "orderDate": text(pick(raw, "order_date", "orderDate")
                          or pick(order, "created_at", "createdAt"))[:10],
        "hasRefund": True,
    }


def request_row(raw: dict[str, Any]) -> dict[str, Any]:
    """BBD iade talebi (RMA) → tablo satırı. Bu satır SÜRECİ temsil eder.

    CANLIDA DOĞRULANDI (2026-08-16, `GET /api/admin/bbd/return-requests`): uç
    YAYINDA ve alan adları KARIŞIK yazımdadır — `order_id`/`created_at` snake,
    `orderIncrementId`/`customerName`/`itemCount` camel. Durum düz metin değil
    SÖZLÜKTÜR (`{"id", "title", "color"}`) ve başlıklar Türkçedir.

    Bir dönem "bu uç henüz yayında değil, alan adları kesinleşmedi" deniyordu;
    artık böyle değil. O varsayımla yazılan okuma canlı veride sipariş
    numarasını boş bırakıyor, durum rozetine de ham sözlüğü basıyordu. `pick`
    yine iki yazımı da dener (bkz. `pick`), bulunmayanı boş bırakır — uydurma
    değer üretilmez.
    """
    request_id = as_int(pick(raw, "id", "request_id"))
    order = pick(raw, "order")
    order = order if isinstance(order, dict) else {}
    status_raw = status_text(pick(raw, "status", "state"))
    key = status_of(status_raw)
    reason = reason_of(pick(raw, "reason", "reason_code", "reasonCode"))
    method = method_of(pick(raw, "refund_method", "refundMethod", "method", "resolution"))
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    created = text(pick(raw, "created_at", "createdAt", "opened_at"))
    pos_state = text(pick(raw, "pos_state", "posState", "refund_state")).lower()

    return {
        "key": f"request-{request_id}",
        "source": "request",
        "id": 0,
        "requestId": request_id,
        "number": text(pick(raw, "increment_id", "number", "code")) or f"T{request_id}",
        "orderId": as_int(pick(raw, "order_id", "orderId") or order.get("id")),
        "orderNumber": text(pick(raw, "order_increment_id", "orderIncrementId")
                            or pick(order, "increment_id", "incrementId")),
        "createdAt": created[:19],
        "date": created[:10],
        "customer": customer_of(raw) or customer_of(order),
        "email": text(pick(raw, "customer_email", "customerEmail")
                      or pick(order, "customer_email", "customerEmail")),
        "itemCount": len(items) or as_int(pick(raw, "item_count", "itemCount")),
        "itemNames": ", ".join(text(pick(item, "name", "product_name")) for item in items
                               if isinstance(item, dict))[:160],
        # Talep aşamasında tutar TAHMİNDİR: kesin rakam kalem seçildikten sonra
        # hesaplanır. Ekran bunu "beklenen" diye gösterir.
        "amount": to_kurus(pick(raw, "amount", "estimated_amount", "estimatedAmount",
                                "grand_total")) or 0,
        "subTotal": 0, "taxAmount": 0, "shippingAmount": 0, "discountAmount": 0,
        "adjustmentRefund": 0, "adjustmentFee": 0,
        "status": key,
        "statusLabel": status_label(key, status_raw),
        "statusRaw": status_raw,
        "reason": reason,
        "reasonLabel": reason_label(reason, text(pick(raw, "reason", "reason_code",
                                                      "reasonCode"))),
        "method": method,
        "methodLabel": METHOD_LABELS[method],
        "carrier": text(pick(raw, "carrier", "shipping_carrier", "carrier_name")),
        "tracking": text(pick(raw, "tracking_number", "trackingNumber", "tracking_no")),
        "posState": pos_state,
        "posMessage": text(pick(raw, "pos_message", "posMessage", "refund_error")),
        "orderDate": text(pick(raw, "order_created_at", "orderCreatedAt", "order_date",
                               "orderDate")
                          or pick(order, "created_at", "createdAt"))[:10],
        "hasRefund": key == ST_REFUNDED,
    }


#: Talebin taşıdığı, kredi notunda KARŞILIĞI OLMAYAN alanlar. Birleştirmede
#: kredi notu satırına bunlar taşınır.
_CARRIED = ("reason", "reasonLabel", "carrier", "tracking", "posState", "posMessage")


def merge_rows(refunds: list[dict[str, Any]],
               requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """İki kaynağı tek listeye indirir (TUZAK 1).

    Aynı siparişin hem kredi notu hem talebi varsa TEK satır gösterilir: kredi
    notu parayı, talep sebebi/kargoyu taşır. İkisini ayrı satır bırakmak
    "bekleyen tutar" KPI'ını iki kez sayardı.

    Bir siparişte birden çok kredi notu olabilir (kısmi iadeler). Talebin
    bilgisi EN ESKİ kredi notuna taşınır — talep zincirin başındadır.
    """
    by_order: dict[int, list[dict[str, Any]]] = {}
    for row in refunds:
        # Siparişi çözülemeyen satır (orderId 0) EŞLEŞTİRME ANAHTARI OLAMAZ:
        # aksi hâlde siparişsiz iki kayıt "aynı sipariş" sanılıp birleşirdi.
        if row["orderId"]:
            by_order.setdefault(row["orderId"], []).append(row)
    for mates in by_order.values():
        mates.sort(key=lambda row: row.get("createdAt") or "")

    out = list(refunds)
    for request in requests:
        mates = (by_order.get(request["orderId"]) or []) if request["orderId"] else []
        if not mates:
            out.append(request)
            continue
        target = mates[0]
        for field in _CARRIED:
            if not target.get(field) and request.get(field):
                target[field] = request[field]
        target["requestId"] = request["requestId"]
        if request["method"] != "unknown" and target["method"] == "unknown":
            target["method"] = request["method"]
            target["methodLabel"] = METHOD_LABELS[request["method"]]

    out.sort(key=lambda row: (row.get("createdAt") or "", row.get("number") or ""), reverse=True)
    return out


def flags(row: dict[str, Any], *, today: str = "", free_return_days: int = 14,
          return_wait_days: int = 7) -> dict[str, Any]:
    """Satırın anahtar süzgeçleri. Hesabı ETKİLEMEZ, personeli uyarır."""
    day = today or today_iso()
    basis = row.get("orderDate") or row.get("date") or ""
    age = days_between(basis, day)
    waiting = days_between(row.get("date") or "", day)
    late = bool(row.get("status") == ST_AWAITING and waiting is not None
                and waiting >= return_wait_days)
    return {
        "withinWithdrawal": bool(age is not None and 0 <= age <= free_return_days),
        "withdrawalDays": age,
        "posFailed": text(row.get("posState")).lower() in ("failed", "error", "declined"),
        "itemLate": late,
        "waitingDays": waiting if late else None,
    }


def decorate(rows: list[dict[str, Any]], *, today: str = "", free_return_days: int = 14,
             return_wait_days: int = 7) -> list[dict[str, Any]]:
    for row in rows:
        row["flags"] = flags(row, today=today, free_return_days=free_return_days,
                             return_wait_days=return_wait_days)
    return rows


# ============================================================ sipariş kalemi

def order_view(raw: dict[str, Any], *, basis: str = "invoiced",
               shipping_refunded: int | None = None) -> dict[str, Any]:
    """Sipariş → iade edilebilir kalemler + kargo temeli (TUZAK 2).

    İADE EDİLEBİLİR = faturalanan − iade edilen. Faturalanmamış kalemin parası
    hiç alınmamıştır; onu "iade etmek" ikinci kez para çıkarır ve mutabakatta
    açık bırakır. Siparişin hiçbir kalemi faturalanmamışsa (faturasız akış)
    ekran bunu SÖYLER ve ayar açıkça `ordered` yapılmadıkça iade açılmaz.

    `shipping_refunded` DIŞARIDAN verilir. CANLIDA DOĞRULANDI: sipariş detayı
    `shipping_amount_refunded` alanını YAYINLAMIYOR; yalnız siparişin kendi
    alanına bakmak "hiç kargo iade edilmemiş" demek olurdu ve kargo bedeli
    ikinci kez geri verilebilirdi (TUZAK 4). Değer, siparişin kesilmiş kredi
    notlarından toplanır (`shipping_refunded_of`).
    """
    items = raw.get("items") if isinstance(raw.get("items"), list) else []
    invoiced_any = any(as_int(pick(item, "qty_invoiced", "qtyInvoiced")) > 0
                       for item in items if isinstance(item, dict))
    use_ordered = basis == "ordered" or not invoiced_any

    lines: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ordered = as_int(pick(item, "qty_ordered", "qtyOrdered"))
        invoiced = as_int(pick(item, "qty_invoiced", "qtyInvoiced"))
        refunded = as_int(pick(item, "qty_refunded", "qtyRefunded"))
        shipped = as_int(pick(item, "qty_shipped", "qtyShipped"))
        available = (ordered if use_ordered else invoiced) - refunded
        lines.append({
            "id": as_int(pick(item, "id")),
            "sku": text(pick(item, "sku")),
            "name": text(pick(item, "name")) or "(adsız kalem)",
            "qtyOrdered": ordered,
            "qtyInvoiced": invoiced,
            "qtyRefunded": refunded,
            "qtyShipped": shipped,
            "refundable": max(0, available),
            "unitPrice": to_kurus(pick(item, "price")) or 0,
            "lineTotal": to_kurus(pick(item, "total", "base_total")) or 0,
            "tax": to_kurus(pick(item, "tax_amount", "taxAmount")) or 0,
            "discount": to_kurus(pick(item, "discount_amount", "discountAmount")) or 0,
        })

    shipping_total = to_kurus(pick(raw, "shipping_amount", "shippingAmount")) or 0
    shipping_tax = to_kurus(pick(raw, "shipping_tax_amount", "shippingTaxAmount")) or 0
    already = shipping_refunded if shipping_refunded is not None else (
        to_kurus(pick(raw, "shipping_amount_refunded", "shippingAmountRefunded")) or 0)

    return {
        "orderId": as_int(raw.get("id")),
        "orderNumber": text(pick(raw, "increment_id", "incrementId")),
        "orderDate": text(pick(raw, "created_at", "createdAt"))[:19],
        "customer": customer_of(raw),
        "email": text(pick(raw, "customer_email", "customerEmail")),
        "orderStatus": text(pick(raw, "status")),
        "grandTotal": to_kurus(pick(raw, "grand_total", "grandTotal")) or 0,
        "totalRefunded": to_kurus(pick(raw, "grand_total_refunded",
                                       "grandTotalRefunded")) or 0,
        "lines": lines,
        "shippingTotal": shipping_total + shipping_tax,
        "shippingRefunded": already,
        "shippingAvailable": max(0, shipping_total + shipping_tax - already),
        "basis": "ordered" if use_ordered else "invoiced",
        "invoicedAny": invoiced_any,
    }


def shipping_refunded_of(rows: list[Any]) -> int:
    """Kesilmiş kredi notlarında müşteriye ZATEN geri verilmiş kargo (kuruş).

    Sipariş detayı bu toplamı yayınlamıyor; tek doğru kaynak siparişin kredi
    notlarıdır. KDV dahil toplanır çünkü `order_view` kargo bedelini de KDV
    dahil hesaplıyor — iki tarafın tanımı ayrılırsa fark kuruş değil, tam bir
    kargo bedeli olur.
    """
    total = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total += to_kurus(pick(row, "shipping_amount", "shippingAmount")) or 0
        total += to_kurus(pick(row, "shipping_tax_amount", "shippingTaxAmount")) or 0
    return max(0, total)


# ================================================================== HESAP

def compute(lines: list[dict[str, Any]], quantities: dict[str, Any], *,
            refund_shipping: bool = False, shipping_available: int = 0,
            adjustment_refund: int = 0, adjustment_fee: int = 0) -> dict[str, Any]:
    """İADE TUTARI — satır satır, gizli toplam yok (TUZAK 3, 4).

    Her satır: adet × birim fiyat − indirim payı + KDV payı. Altında kargo
    iadesi, ek iade ve kesinti AYRI satır olarak durur. Ekran bu sözlüğü
    olduğu gibi çizer; hiçbir kalem "toplamın içinde" kaybolmaz.

    Adet, kalemin iade edilebilir adedini AŞAMAZ: fazlası sessizce kırpılmaz,
    `clamped` ile bildirilir — kullanıcı ne istediğini ve ne olduğunu görür.
    """
    rows: list[dict[str, Any]] = []
    clamped: list[str] = []
    base = 0
    discount = 0
    tax = 0

    for line in lines:
        wanted = as_int(quantities.get(str(line["id"])), 0)
        if wanted <= 0:
            continue
        qty = min(wanted, int(line["refundable"]))
        if qty < wanted:
            clamped.append(line["sku"] or str(line["id"]))
        if qty <= 0:
            continue
        whole = int(line["qtyOrdered"]) or qty
        line_base = int(line["unitPrice"]) * qty
        line_discount = share(int(line["discount"]), qty, whole)
        line_tax = share(int(line["tax"]), qty, whole)
        rows.append({
            "id": line["id"],
            "sku": line["sku"],
            "name": line["name"],
            "qty": qty,
            "unitPrice": int(line["unitPrice"]),
            "base": line_base,
            "discount": line_discount,
            "tax": line_tax,
            "total": line_base - line_discount + line_tax,
        })
        base += line_base
        discount += line_discount
        tax += line_tax

    shipping = int(shipping_available) if refund_shipping else 0
    refund_extra = max(0, int(adjustment_refund))
    fee = max(0, int(adjustment_fee))
    total = base - discount + tax + shipping + refund_extra - fee

    return {
        "lines": rows,
        "base": base,
        "discount": discount,
        "tax": tax,
        "shipping": shipping,
        "shippingAvailable": int(shipping_available),
        "shippingIncluded": bool(refund_shipping),
        "adjustmentRefund": refund_extra,
        "adjustmentFee": fee,
        "total": total,
        "itemCount": len(rows),
        "unitCount": sum(row["qty"] for row in rows),
        "clamped": clamped,
        "empty": not rows and shipping == 0 and refund_extra == 0,
        "negative": total < 0,
    }


def refund_body(result: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    """Hesabı mağazanın beklediği gövdeye çevirir.

    `items` {kalemId: adet}; para alanları ONDALIK metin gider (TUZAK 7).
    Kargo iadesi işaretlenmediyse alan `0.00` olarak AÇIKÇA gönderilir:
    alanı hiç göndermemek, Bagisto'nun kendi varsayılanını uygulamasına ve
    ekranda "hariç" yazarken kargonun geri verilmesine yol açıyordu.
    """
    items = {str(row["id"]): int(row["qty"]) for row in result["lines"]}
    adjustments = {
        "shipping": from_kurus(result["shipping"]),
        "adjustment_refund": from_kurus(result["adjustmentRefund"]),
        "adjustment_fee": from_kurus(result["adjustmentFee"]),
    }
    return items, adjustments


def store_total(payload: Any) -> int | None:
    """Mağazanın kendi önizlemesinden genel toplamı çıkarır.

    Uç henüz olgunlaşmadığı için yanıt zarflı da (`{data: {...}}`) düz de
    gelebiliyor. Bulunamazsa `None` döner — sıfır UYDURULMAZ; sıfır "mağaza
    da sıfır dedi" anlamına gelir ve ekranı yanıltırdı.
    """
    if not isinstance(payload, dict):
        return None
    sources: list[dict[str, Any]] = [payload]
    for key in ("data", "refund", "totals"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for name in ("grand_total", "grandTotal", "total", "base_grand_total"):
            if name in source:
                found = to_kurus(source[name])
                if found is not None:
                    return found
    return None


def compare(local: int, store: int | None) -> dict[str, Any]:
    """Bizim hesabımız ile mağazanınki uyuşuyor mu (TUZAK 5).

    Üç cevap vardır ve üçü de söylenir: uyuşuyor · uyuşmuyor · SORULAMADI.
    Sorulamayanı "uyuşuyor" saymak, ekranda doğrulanmış görünen ama
    doğrulanmamış bir tutarla para çıkarmak olurdu.
    """
    if store is None:
        return {"matches": None, "diff": 0, "storeTotal": None,
                "message": "Mağaza önizlemesi alınamadı; aşağıdaki tutar Kontrol Merkezi'nin "
                           "hesabıdır. Mağaza kendi hesabını uygularsa fark oluşabilir."}
    diff = int(store) - int(local)
    if diff == 0:
        return {"matches": True, "diff": 0, "storeTotal": int(store),
                "message": "Mağaza aynı tutarı hesapladı."}
    return {"matches": False, "diff": diff, "storeTotal": int(store),
            "message": "Mağazanın hesabı farklı. Uygulanacak tutar MAĞAZANINKİDİR; "
                       "farkı çözmeden onaylamayın."}


# ================================================================== özet

def summarize(rows: list[dict[str, Any]], *, today: str = "",
              sales_total: int | None = None) -> dict[str, Any]:
    """KPI şeridi: bekleyen tutar · bu ay iade · iade oranı %."""
    day = today or today_iso()
    start, end = month_bounds(day)

    pending = [row for row in rows if row["status"] in OPEN_STATUSES]
    month = [row for row in rows
             if row["status"] == ST_REFUNDED and start <= (row.get("date") or "") <= end]

    counts = {key: 0 for key in STATUS_ORDER}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    month_amount = sum(row["amount"] for row in month)
    return {
        "pendingAmount": sum(row["amount"] for row in pending),
        "pendingCount": len(pending),
        "monthAmount": month_amount,
        "monthCount": len(month),
        "refundRate": refund_rate(month_amount, sales_total),
        "salesTotal": sales_total,
        "monthStart": start,
        "monthEnd": end,
        "counts": counts,
        "total": len(rows),
    }


def refund_rate(refunded: int, sales: int | None) -> float | None:
    """İade oranı %. Satış rakamı okunamadıysa `None` — sıfır DEĞİL.

    Sıfır göstermek "bu ay hiç iade yok" demektir; oysa bilinmeyen, satışın
    okunamadığıdır. Ekran `—` gösterir ve nedenini yazar.
    """
    if sales is None or sales <= 0:
        return None
    return round(refunded * 100 / sales, 1)


#: Pano yanıtında dönem satışını taşıyabilecek alan adları. `total_sales`
#: CANLIDA DOĞRULANDI (`/api/admin/dashboard/stats?type=total-sales`):
#: `{"statistics": {"total_sales": {"previous": 8, "current": 16, ...}}}`.
#: Eski liste bu adı hiç aramadığı için iade oranı KPI'ı mağaza ayaktayken
#: bile HER ZAMAN "—" gösteriyordu.
_SALES_FIELDS = ("total_sales", "totalSales", "total", "revenue", "sales", "grand_total",
                 "current")

#: Alan bir sözlükse dönem değeri bu anahtarlardadır (`current` = bu dönem).
_SALES_INNER = ("current", "total", "revenue", "formatted_total")


def sales_of(payload: Any) -> int | None:
    """Pano istatistiğinden dönem satış tutarını çıkarır; bulunamazsa `None`."""
    if isinstance(payload, list):
        # `/dashboard/stats` zarfsız TEK ELEMANLI liste dönebiliyor.
        payload = next((item for item in payload if isinstance(item, dict)), None)
    if not isinstance(payload, dict):
        return None
    sources: list[dict[str, Any]] = [payload]
    for key in ("data", "statistics", "current", "total"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    for source in sources:
        for name in _SALES_FIELDS:
            value = source.get(name)
            if isinstance(value, dict):
                value = next((value[key] for key in _SALES_INNER
                              if value.get(key) not in (None, "")), None)
            if value is None or isinstance(value, (dict, list)):
                continue
            found = to_kurus(value)
            if found is not None and found > 0:
                return found
    return None
