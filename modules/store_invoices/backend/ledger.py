"""Fatura verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın zor kısmı ağ değil ARİTMETİK: matrah/KDV
ayrıştırması, dönem icmali, seri numarası boşluğu, yasal numara birleştirme.
Bunlar servise gömülseydi tek satırı bile ağ olmadan test edilemezdi.

SEKİZ TUZAK — hepsinin karşılığı burada bir fonksiyondur:

 1. Bagisto PDF'i YASAL FATURA DEĞİLDİR   → `LEGAL_NOTICE`, ekranda kalıcı.
 2. Para telde ondalık, içeride kuruş     → `to_kurus` Decimal ile çevirir.
 3. KDV oranı kalemdedir, faturada değil  → `rate_rows` kalemlerden toplar;
                                             fatura kaleminde ORAN ALANI DA
                                             YOK, `item_rate` tutardan türetir.
 4. Seri numarası Bagisto'da YOK          → yerel tablo + `number_gaps`.
 5. Alan adları camelCase gelir           → `pick` alt tire/büyük harf farkını
                                             yok sayarak arar (aşağıdaki not).
 6. Süzgeç sessizce yok sayılabilir       → `filter_honored` sonucu denetler.
 7. İrsaliye = gönderi kaydı, belge değil → `shipment_row` + taslak damgası.
 8. Müşteri künyesi üç ayrı yerde durur   → `party` üçünü de tarar.

ALAN ADI TUZAĞI — CANLIYA KARŞI DOĞRULANDI. `bagisto/bagisto-api` yönetici
uçları alanları **camelCase** veriyor: `grandTotal`, `subTotal`, `taxAmount`,
`incrementId`, `orderIncrementId`, `createdAt`, `trackNumber`,
`inventorySourceName`. Bagisto'nun veritabanı sütunları snake_case olduğu için
`grand_total` yazmak doğal görünür ama HİÇBİRİ eşleşmez: tablo sessizce sıfır
dolu gelir, kimse fark etmez. Bu yüzden `pick` adları normalleştirerek arar;
her iki biçim de çalışır ve mağaza normalleştirmeyi değiştirse bile ekran
kırılmaz.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: EKRANDA KALICI olarak duran uyarı. Depoda hiçbir e-Fatura/e-Arşiv
#: entegrasyonu yok ve Bagisto'nun ürettiği PDF mali belge değildir; bu metin
#: gizlenemez, kapatılamaz. Yasal belge dış sistemde kesilir, numarası bu
#: ekrandan eşlenir.
LEGAL_NOTICE = (
    "Bu ekrandaki PDF Bagisto'nun sipariş dökümüdür; YASAL e-Arşiv/e-Fatura "
    "belgesi DEĞİLDİR. Sistemde GİB entegrasyonu yoktur. Yasal fatura dış "
    "sistemde kesilir ve numarası buradan eşlenir."
)

#: İrsaliye sekmesinin damgası. Bagisto'daki gönderi kaydı sevkiyat izidir;
#: matbu/e-irsaliye belgesi değildir.
DELIVERY_NOTICE = (
    "İrsaliye kaydı Bagisto'nun gönderi izidir; yasal sevk irsaliyesi "
    "değildir. Üretilen döküm yalnızca depo/kargo çıkışında kullanılır."
)

#: Bagisto fatura durumları. Bilinmeyen durum ham hâliyle gösterilir —
#: uydurma etiket, veriyi yanlış okumaktan kötüdür.
STATE_LABELS = {
    "paid": "Ödendi",
    "pending": "Bekliyor",
    "overdue": "Gecikti",
    "refunded": "İade edildi",
    "canceled": "İptal",
    "cancelled": "İptal",
}

STATE_TONES = {
    "paid": "good",
    "pending": "warn",
    "overdue": "bad",
    "refunded": "info",
    "canceled": "dim",
    "cancelled": "dim",
}

#: Bu durumdaki siparişe fatura KESİLMEZ; toplu kesme adaylarından elenir.
#: `completed` listede YOK: faturası kesilmiş sipariş zaten fatura çapraz
#: denetiminde eleniyor, durumdan elemek gereksiz yere aday gizlerdi.
UNINVOICEABLE_STATUSES = ("canceled", "cancelled", "closed", "fraud")

#: `update_invoice_status` ile yazılabilen durumlar. Listede olmayan bir
#: durumu göndermek Bagisto'da 422 üretir; ekran o düğmeyi hiç açmaz.
WRITABLE_STATES = ("paid", "pending", "overdue")

#: Yasal numaranın varsayılan basamak sayısı. GİB biçimi 3 harf + 4 hane yıl +
#: 9 hane sıra; seri kodu yılı da taşıdığı için burada yalnız sıra padlenir.
DEFAULT_PAD = 9

#: Rapor için tam liste taranırken kabul edilen üst sınır. Bir yılda ~6.000
#: fatura bekleniyor; 20.000 tavanı bozuk `meta` yüzünden sonsuz taramayı da
#: keser.
REPORT_ROW_CAP = 20_000


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    UTC kullanılmaz: dönem icmali mali takvim gününe göre kapanır ve gece
    yarısından sonraki üç saatte UTC bir gün geride kalır — ayın son günü
    kesilen fatura bir sonraki döneme düşerdi.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


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


def to_kurus(value: Any) -> int:
    """Ondalık para değerini kuruşa çevirir. Çözülemezse 0.

    TUZAK: burada `float` KULLANILMAZ. `float("1234.35") * 100` bazı
    değerlerde 123434.999… verir ve `int()` bir kuruş aşağı yuvarlar. Bir
    dönemde altı bin fatura toplanınca bu sapma icmalde görünür hâle gelir.
    """
    if value is None:
        return 0
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return 0
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return 0
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def to_rate(value: Any) -> float | None:
    """KDV oranı: `"18.0000"` → 18.0. Çözülemezse `None` (0 DEĞİL).

    Sıfır oran (istisna/muafiyet) ile "oran bilinmiyor" farklı şeylerdir;
    ikisini 0'da birleştirmek mali müşavire yanlış matrah gösterir.
    """
    if value is None or text(value) == "":
        return None
    try:
        return float(Decimal(str(value).replace(",", ".")))
    except (InvalidOperation, ValueError, TypeError):
        return None


def rate_label(rate: float | None) -> str:
    if rate is None:
        return "Ayrıştırılamadı"
    if float(rate).is_integer():
        return f"%{int(rate)}"
    return f"%{rate:.2f}".replace(".", ",")


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def fold_key(name: Any) -> str:
    """`grand_total` · `grandTotal` · `GRAND-TOTAL` → `grandtotal`.

    Alan adını biçiminden arındırır; `pick` bununla arar.
    """
    return str(name).replace("_", "").replace("-", "").lower()


def _folded(source: dict[str, Any]) -> dict[str, Any]:
    """Anahtarları normalleştirilmiş kopya. İLK gelen kazanır: `taxAmount` ile
    `tax_amount` aynı sözlükte durursa (Bagisto bazı uçlarda ikisini de
    veriyor) önce yazılan korunur; ikisi de aynı değeri taşıyor."""
    out: dict[str, Any] = {}
    for key, value in source.items():
        folded = fold_key(key)
        if folded not in out:
            out[folded] = value
    return out


def pick(raw: Any, *names: str) -> Any:
    """Kayıtta bir alanı nerede ve hangi yazımla olursa olsun bulur.

    İKİ AYRI SORUNU BİRDEN ÇÖZER:

    1. ZARF. Bagisto admin API'si aynı kaydı düz sözlük ya da
       `{"data": {...}}` / `{"attributes": {...}}` zarfıyla verebiliyor.
    2. YAZIM. Alanlar telde camelCase (`grandTotal`), veritabanında snake_case
       (`grand_total`). Çağıran hangisini yazarsa yazsın bulunur — yanlış
       yazım sessiz sıfır üretirdi.
    """
    if not isinstance(raw, dict):
        return None
    sources: list[dict[str, Any]] = [_folded(raw)]
    for key in ("data", "attributes", "invoice"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            sources.append(_folded(nested))
    wanted = [fold_key(name) for name in names]
    for name in wanted:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    for name in wanted:
        for source in sources:
            if name in source:
                return source[name]
    return None


def _sub(raw: Any, *keys: str) -> dict[str, Any]:
    """İç içe sözlük parçası; yoksa boş sözlük döner."""
    for key in keys:
        value = pick(raw, key)
        if isinstance(value, dict):
            return value
    return {}


def short_stamp(value: Any) -> str:
    """`2026-08-01 10:22:03` → `2026-08-01 10:22`. Boşsa boş kalır."""
    raw = text(value).replace("T", " ")
    return raw[:16] if raw else ""


def day_of(value: Any) -> str:
    """Kayıt zamanının takvim günü (ISO)."""
    return short_stamp(value)[:10]


# =============================================================== fatura satırı

def billing_address(raw: Any) -> dict[str, Any]:
    """Fatura adresi — CANLIDA ÜÇ AYRI YERDE DURUYOR, üçü de aranır.

     · gönderi kaydında düz alan:  `billingAddress: {...}`
     · fatura/sipariş detayında:   `order.addresses: [{addressType: "order_billing", …}]`
     · fatura LİSTESİNDE:          hiç yok — çağıran `customerName`e düşer.

    Liste ucunda adres gelmediği için VKN/TCKN yalnız çekmecede (detay) dolar;
    listede boş görünmesi eksiklik değil, mağazanın verdiği kadarıdır.
    """
    if not isinstance(raw, dict):
        return {}
    direct = _sub(raw, "billing_address")
    if direct:
        return direct
    rows = pick(raw, "addresses")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and "billing" in text(pick(row, "address_type")).lower():
                return row
        for row in rows:
            if isinstance(row, dict):
                return row
    return {}


def party(raw: dict[str, Any]) -> dict[str, str]:
    """Müşteri künyesi: ad, VKN/TCKN, kurumsal mı.

    VKN/TCKN Bagisto'da ayrı alan değildir; fatura adresindeki `vatId`
    kullanılır. Kurumsal ayrımı `companyName` ya da `vatId` varlığından
    çıkar — mağazada müşteri tipi diye bir alan yok.
    """
    order = _sub(raw, "order")
    source = billing_address(raw) or billing_address(order)

    name = text(pick(source, "name")) or " ".join(
        part for part in (text(pick(source, "first_name")), text(pick(source, "last_name")))
        if part
    ).strip()
    if not name:
        # Liste uçlarında adres yok; müşteri adı düz alanda geliyor.
        name = text(pick(raw, "customer_full_name", "customer_name", "shipped_to")) \
            or text(pick(order, "customer_full_name", "customer_name"))

    company = text(pick(source, "company_name"))
    tax_id = text(pick(source, "vat_id", "tax_id"))
    email = text(pick(source, "email")) or text(pick(raw, "customer_email")) \
        or text(pick(order, "customer_email"))
    return {
        # Misafir siparişte `customerName` canlıda `null` geliyor ama e-posta
        # dolu; "—" yazmak elimizdeki tek künyeyi saklamak olurdu.
        "name": name or company or email or "—",
        "company": company,
        "taxId": tax_id,
        "email": email,
        "kind": "corporate" if (company or tax_id) else "individual",
        "kindLabel": "Kurumsal" if (company or tax_id) else "Bireysel",
    }


def item_rate(raw_percent: Any, *, net: int, raw_tax: Any) -> tuple[float | None, bool]:
    """Kalemin KDV oranı ve bu oranın TÜRETİLMİŞ olup olmadığı.

    CANLIDA DOĞRULANDI: `/api/admin/invoices/{id}` kalemleri `taxPercent`
    ALANINI TAŞIMIYOR (sipariş kalemleri taşıyor, fatura kalemleri değil).
    Alan yoksa oran KDV tutarının matraha bölümünden türetilir ve bayrakla
    işaretlenir — icmalde "türetilmiştir" notu bu bayraktan çıkar.

    KDV tutarı hiç gelmemişse (`None`) oran BİLİNMİYOR'dur; sıfır yazmak
    muafiyet gibi görünür ve beyanı yanlış doldururdu. Tutar 0 ise oran
    gerçekten %0'dır — ikisi ayrı şeydir.
    """
    given = to_rate(raw_percent)
    if given is not None:
        return given, False
    if net <= 0 or text(raw_tax) == "":
        return None, False
    return round(to_kurus(raw_tax) / net * 100, 2), True


def invoice_items(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Fatura kalemleri. Oran alanı gelmediği için `item_rate` türetir."""
    items = pick(raw, "items", "invoice_items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        net = to_kurus(pick(item, "total", "base_total"))
        raw_tax = pick(item, "tax_amount", "base_tax_amount")
        rate, derived = item_rate(pick(item, "tax_percent", "tax_rate"), net=net, raw_tax=raw_tax)
        out.append({
            "name": text(pick(item, "name")),
            "sku": text(pick(item, "sku")),
            "qty": as_int(pick(item, "qty", "quantity")),
            "unit": to_kurus(pick(item, "price", "base_price")),
            "net": net,
            "tax": to_kurus(raw_tax),
            "discount": to_kurus(pick(item, "discount_amount", "base_discount_amount")),
            "rate": rate,
            "rateDerived": derived,
        })
    return out


def invoice_row(raw: Any, *, legal: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tek fatura → ekranın ve raporun beklediği düz satır.

    `legal` bizim yerel eşleme kaydımızdır (dış sistemde kesilen faturanın
    numarası). Mağazada karşılığı yoktur; olmadığında satır "eşlenmedi" der.
    """
    if not isinstance(raw, dict):
        raw = {}
    order = _sub(raw, "order")
    state = text(pick(raw, "state", "status")).lower()
    items = invoice_items(raw)
    who = party(raw)
    record = legal or {}

    net = to_kurus(pick(raw, "sub_total", "base_sub_total"))
    raw_tax = pick(raw, "tax_amount", "base_tax_amount")
    tax = to_kurus(raw_tax)
    total = to_kurus(pick(raw, "grand_total", "base_grand_total"))
    # FATURA BAŞLIĞINDAN oran. Liste ucu kalemleri BOŞ dizi olarak veriyor
    # (canlıda doğrulandı: 16 faturanın 16'sında `items: []`), yani icmalin
    # oran kırılımı kalem beklerse tamamen "Ayrıştırılamadı" çıkardı. Başlık
    # tutarlarından türetilen oran, hiç oran yoktan iyidir — ve türetilmiş
    # olduğu satırda yazar.
    header_rate, header_derived = item_rate(None, net=net, raw_tax=raw_tax)
    discount = to_kurus(pick(raw, "discount_amount", "base_discount_amount"))
    shipping = to_kurus(pick(raw, "shipping_amount", "base_shipping_amount"))

    legal_no = text(record.get("legal_no"))
    return {
        "id": as_int(pick(raw, "id")),
        "number": text(pick(raw, "increment_id")) or f"#{as_int(pick(raw, 'id'))}",
        # Fatura LİSTESİNDE düz `orderId` yok; sipariş kimliği `order.id`de
        # duruyor. Detayda ikisi de olabiliyor — ikisi de aranır.
        "orderId": as_int(pick(raw, "order_id") or pick(order, "id")),
        "orderNumber": text(pick(order, "increment_id")) or text(pick(raw, "order_increment_id")),
        "createdAt": short_stamp(pick(raw, "created_at")),
        "day": day_of(pick(raw, "created_at")),
        "customer": who["name"],
        "company": who["company"],
        "taxId": who["taxId"],
        "email": who["email"],
        "partyKind": who["kind"],
        "partyKindLabel": who["kindLabel"],
        "net": net,
        "tax": tax,
        "discount": discount,
        "shipping": shipping,
        "total": total,
        "state": state,
        "stateLabel": STATE_LABELS.get(state, text(pick(raw, "state")) or "—"),
        "stateTone": STATE_TONES.get(state, ""),
        "itemCount": len(items),
        "rates": sorted({item["rate"] for item in items if item["rate"] is not None}),
        "headerRate": header_rate,
        "headerRateDerived": header_derived,
        "legalNo": legal_no,
        "legalSeries": text(record.get("series")),
        "legalNumber": as_int(record.get("number")),
        "legalDate": text(record.get("issued_at")),
        "legalMatched": bool(legal_no),
    }


def shipment_row(raw: Any) -> dict[str, Any]:
    """Gönderi kaydı → irsaliye satırı. Belge değil, sevk izidir."""
    if not isinstance(raw, dict):
        raw = {}
    order = _sub(raw, "order")
    items = pick(raw, "items", "shipment_items")
    count = len([item for item in items if isinstance(item, dict)]) if isinstance(items, list) else 0
    return {
        "id": as_int(pick(raw, "id")),
        "orderId": as_int(pick(raw, "order_id") or pick(order, "id")),
        "orderNumber": text(pick(raw, "order_increment_id")) or text(pick(order, "increment_id")),
        "createdAt": short_stamp(pick(raw, "created_at")),
        "day": day_of(pick(raw, "created_at")),
        "carrier": text(pick(raw, "carrier_title", "carrier_code", "carrier")),
        "trackNumber": text(pick(raw, "track_number", "tracking_number")),
        "customer": party(raw)["name"],
        "totalQty": as_int(pick(raw, "total_qty")),
        "itemCount": count,
        # Canlıda kaynak düz alanda geliyor (`inventorySourceName`); iç içe
        # `inventory_source.name` biçimi eski uçlarda kaldı, ikisi de okunur.
        "source": text(pick(raw, "inventory_source_name"))
                  or text(pick(_sub(raw, "inventory_source"), "name")),
    }


# ============================================================ süzgeç denetimi

#: CANLIYA KARŞI DENENDİ (`/api/admin/invoices`). Bu dört süzgeç gerçekten
#: uygulanıyor: `state=pending` → 0 satır, `order_id=19` → 1 satır,
#: `date_from/date_to` aralığı 16 faturayı 7'ye düşürüyor.
SERVER_FILTERS = ("state", "date_from", "date_to", "order_id")

#: CANLIDA DENENDİ VE HEPSİ SESSİZCE YOK SAYILDI — 16 faturanın 16'sı geri
#: geldi. Laravel tanımadığı sorgu parametresini yok sayar; bunları gönderip
#: "süzdüm" saymak, tüm dönemi gösteren bir listeyi süzülmüş sanmak olurdu.
#: Bu yüzden GÖNDERİLMEZ; karşılıkları sayfa üzerinde YEREL uygulanır ve
#: ekran kaç satırın yerel elendiğini söyler.
IGNORED_FILTERS = ("search", "q", "increment_id", "grand_total_from", "grand_total_to",
                   "customer_name")


def matches_query(row: dict[str, Any], query: str) -> bool:
    """Yerel arama: fatura no, yasal no, sipariş no, müşteri, VKN, e-posta."""
    needle = text(query).lower()
    if not needle:
        return True
    haystack = " ".join(text(row.get(key)).lower() for key in
                        ("number", "legalNo", "orderNumber", "customer", "company",
                         "taxId", "email"))
    return needle in haystack


def matches_total(row: dict[str, Any], min_total: int | None, max_total: int | None) -> bool:
    """Yerel tutar aralığı. Değerler KURUŞ."""
    total = as_int(row.get("total"))
    if min_total is not None and total < int(min_total):
        return False
    return not (max_total is not None and total > int(max_total))


def filter_honored(rows: list[dict[str, Any]], key: str, wanted: Any) -> bool | None:
    """Gönderilen süzgeç gerçekten uygulandı mı?

    Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar. Süzülmemiş
    listeyi süzülmüş gibi göstermek, "bu ayda 3 fatura var" diyen bir ekran
    üretir; oysa liste bütün ayları taşıyordur. Satır yoksa karar verilemez
    (`None`) — yok saymakla boş sonuç aynı şey değildir.
    """
    if not rows:
        return None
    target = text(wanted).lower()
    if not target:
        return None
    return all(text(row.get(key)).lower() == target for row in rows)


# ============================================================== seri numarası

def compose_legal_no(code: str, number: Any, pad: Any = DEFAULT_PAD) -> str:
    """`A2026` + 145 → `A2026000000145`. Numara yoksa boş döner."""
    value = as_int(number)
    if value <= 0:
        return ""
    digits = max(1, min(16, as_int(pad, DEFAULT_PAD)))
    return f"{text(code)}{value:0{digits}d}"


def legal_error(*, series: str, number: Any, legal_no: str = "") -> str:
    """Yasal numara eşlemesi kabul edilebilir mi.

    Elle girilen alan olduğu için doğrulama gevşek ama SESSİZ DEĞİL: seri
    kodu ve pozitif sıra numarası zorunludur, aksi hâlde numara boşluğu
    denetimi anlamını yitirir.
    """
    if not text(series):
        return "Seri kodu zorunlu; boşluk denetimi seriye göre çalışır."
    if len(text(series)) > 16:
        return "Seri kodu en çok 16 karakter olabilir."
    if as_int(number) <= 0:
        return "Sıra numarası pozitif bir tam sayı olmalı."
    if len(text(legal_no)) > 32:
        return "Yasal fatura numarası en çok 32 karakter olabilir."
    return ""


def number_gaps(numbers: Iterable[Any]) -> list[dict[str, int]]:
    """Bir seride kullanılmış numaralar arasındaki boşluklar.

    Yalnız EN KÜÇÜK ile EN BÜYÜK arasına bakılır: serinin henüz kesilmemiş
    devamı boşluk değildir. Boşluk mali müşavir için kırmızı bayraktır —
    atlanan numara ya iptal edilmiş faturadır ya da kaydı girilmemiştir.
    """
    values = sorted({as_int(item) for item in numbers if as_int(item, 0) > 0})
    gaps: list[dict[str, int]] = []
    for previous, current in pairwise(values):
        if current - previous > 1:
            gaps.append({"from": previous + 1, "to": current - 1,
                         "count": current - previous - 1})
    return gaps


def gap_message(code: str, gaps: list[dict[str, int]], *, limit: int = 5) -> str:
    """`A2026 serisinde 145-147 eksik.` — ekranda görünen cümle."""
    if not gaps:
        return ""
    shown = gaps[:limit]
    parts = [str(gap["from"]) if gap["from"] == gap["to"] else f"{gap['from']}-{gap['to']}"
             for gap in shown]
    tail = "" if len(gaps) <= limit else f" (+{len(gaps) - limit} aralık daha)"
    return f"{text(code)} serisinde {', '.join(parts)} eksik{tail}."


def next_number(numbers: Iterable[Any], start: Any = 1) -> int:
    """Seride önerilecek sıradaki numara: en büyüğün bir fazlası."""
    values = [as_int(item) for item in numbers if as_int(item, 0) > 0]
    floor = max(1, as_int(start, 1))
    return max([*values, floor - 1]) + 1


# ================================================================ toplulaştırma

def rate_rows(invoices: list[dict[str, Any]],
              details: dict[int, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Oran bazlı matrah/KDV/toplam — mali müşavirin baktığı tablo.

    Oran KALEMDEDİR ve canlıda kalemde de ORAN ALANI YOKTUR: `item_rate`
    tutardan türetir ve `rateDerived` ile işaretler; bu bayrak satıra
    `derived` olarak taşınır ve icmalde not olarak yazılır. Liste ucu
    kalemleri hiç getirmiyorsa fatura toplamı "Ayrıştırılamadı" satırına
    düşer; sıfır orana yazmak muafiyet gibi görünürdü.
    """
    buckets: dict[Any, dict[str, Any]] = {}
    seen: dict[Any, set[int]] = {}
    parts = details or {}

    def key_of(rate: float | None) -> Any:
        return "?" if rate is None else round(float(rate), 4)

    def bucket(rate: float | None) -> dict[str, Any]:
        key = key_of(rate)
        if key not in buckets:
            buckets[key] = {"rate": None if rate is None else float(rate),
                            "rateLabel": rate_label(rate), "net": 0, "tax": 0,
                            "total": 0, "invoices": 0, "derived": False}
            seen[key] = set()
        return buckets[key]

    for invoice in invoices:
        invoice_id = as_int(invoice.get("id"))
        items = parts.get(invoice.get("id")) or []
        # Kalem yoksa (liste ucu `items: []` veriyor) fatura BAŞLIĞINDAN
        # türetilen orana düşer. KDV tutarı hiç gelmemişse `headerRate` None
        # kalır ve satır "Ayrıştırılamadı" olur — sıfıra yazmak muafiyet gibi
        # görünür ve beyanı yanlış doldururdu.
        sources = items or [{"rate": invoice.get("headerRate"),
                             "rateDerived": invoice.get("headerRateDerived"),
                             "net": invoice.get("net"), "tax": invoice.get("tax")}]
        for item in sources:
            slot = bucket(item.get("rate"))
            slot["net"] += as_int(item.get("net"))
            slot["tax"] += as_int(item.get("tax"))
            if item.get("rateDerived"):
                slot["derived"] = True
            seen[key_of(item.get("rate"))].add(invoice_id)

    rows = list(buckets.values())
    for key, row in zip(buckets.keys(), rows, strict=False):
        row["invoices"] = len(seen[key])
        row["total"] = row["net"] + row["tax"]
    # Bilinmeyen oran EN SONA: tabloyu okuyan önce çözülmüş oranları görsün.
    rows.sort(key=lambda row: (row["rate"] is None, row["rate"] or 0))
    return rows


def period_summary(invoices: list[dict[str, Any]],
                   details: dict[int, list[dict[str, Any]]] | None = None,
                   refunds: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Dönem icmali. Net/KDV/toplam, oran kırılımı, gün kırılımı, eşleşmeyenler."""
    rows = rate_rows(invoices, details)
    net = sum(as_int(item.get("net")) for item in invoices)
    tax = sum(as_int(item.get("tax")) for item in invoices)
    total = sum(as_int(item.get("total")) for item in invoices)
    refund_total = sum(as_int(item.get("total")) for item in (refunds or []))

    days: dict[str, dict[str, Any]] = {}
    for invoice in invoices:
        slot = days.setdefault(invoice.get("day") or "—",
                               {"day": invoice.get("day") or "—", "count": 0, "total": 0})
        slot["count"] += 1
        slot["total"] += as_int(invoice.get("total"))

    missing = [item for item in invoices if not item.get("legalMatched")]
    return {
        "count": len(invoices),
        "net": net,
        "tax": tax,
        "total": total,
        "refundCount": len(refunds or []),
        "refundTotal": refund_total,
        # Net tahsilat: iadeler düşülmüş toplam. Beyanın kendisi değildir,
        # mali müşavirin karşılaştırma yaptığı rakamdır.
        "netTotal": total - refund_total,
        "byRate": rows,
        # Oran alanı mağazadan gelmediği için tutardan türetildiyse icmal
        # bunu YAZAR: mali müşavir rakamın nereden geldiğini bilmeli.
        "ratesDerived": any(row.get("derived") for row in rows),
        "byDay": sorted(days.values(), key=lambda item: item["day"]),
        "missingLegal": len(missing),
        "missingNumbers": [item["number"] for item in missing[:50]],
    }


def csv_table(rows: list[dict[str, Any]], money: Any) -> tuple[list[str], list[list[Any]]]:
    """Muhasebe biçimi CSV: yasal numara DA taşır — asıl amaç budur."""
    headers = ["Fatura no", "Yasal fatura no", "Tarih", "Sipariş no", "Müşteri", "VKN/TCKN",
               "Tip", "Matrah", "KDV", "İndirim", "Kargo", "Toplam", "Durum"]
    table = [[row["number"], row["legalNo"] or "—", row["createdAt"], row["orderNumber"],
              row["customer"], row["taxId"] or "—", row["partyKindLabel"],
              money(row["net"]), money(row["tax"]), money(row["discount"]),
              money(row["shipping"]), money(row["total"]), row["stateLabel"]]
             for row in rows]
    return headers, table
