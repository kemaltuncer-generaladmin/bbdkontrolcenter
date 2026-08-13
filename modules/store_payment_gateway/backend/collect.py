"""Link ile tahsilatın saf hesapları — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranda üç şey paraya dokunuyor ve üçü de sessizce
yanlış olabilir: tutar kırılımı (KDV), banka durum eşlemesi ve SMS metninin
kaç parça gideceği. Servise gömülselerdi ağ olmadan tek satırı denenemezdi;
burada hepsi girdi→çıktı fonksiyonudur.

ALTI TUZAK — her birinin karşılığı bu dosyada bir fonksiyondur:

 1. Serbest tutar KDV'SİZ, ürün KDV'Lİ      → `breakdown` iki kalem tipini ayırır.
 2. Ürünün vergisi ürünün kendi kategorisi  → `tax_rate_for` orana kategoriden bakar.
 3. Bilinmeyen banka durumu "başarısız"     → `map_status` bilinmeyeni ASLA
    değildir; para çekilmiş olabilir           `failed` yapmaz, `unknown` yapar.
 4. Türkçe harf SMS'i üçe böler             → `sms_plan` parça sayısını ve
                                               suçlu karakterleri gösterir.
 5. Şablonda yazım hatası sessiz kalır      → `render_template` eksik yer
                                               tutucuyu geri bildirir.
 6. Süzgeç metni SQL'e gömülür              → `filter_clause` yalnız yer
                                               tutucu üretir, değer gömmez.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from km_sdk import normalize_msisdn, offending, plan_text, simplify

#: Gerekçenin en az uzunluğu. Geçit (store_api) da 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Tek tuşla yazılan kurumsal e-posta. Müşterinin gerçek adresi yoksa fatura
#: buraya düşer; boş bırakıp faturayı öksüz bırakmaktan iyidir.
DEFAULT_EMAIL = "fatura@bbdstore.com.tr"

#: Yerel talep durumları. Mağazadaki ham durum ayrıca `store_status` içinde
#: saklanır: eşlememizin yanlış olduğu anlaşılırsa ham veri elimizde kalsın.
DRAFT = "draft"
LINKED = "linked"
SENT = "sent"
PAID = "paid"
EXPIRED = "expired"
CANCELLED = "cancelled"
FAILED = "failed"
UNKNOWN = "unknown"
VOID_REQUIRED = "void_required"
SETTLED = "settled"

LOCAL_STATUSES = (DRAFT, LINKED, SENT, PAID, EXPIRED, CANCELLED, FAILED,
                  UNKNOWN, VOID_REQUIRED, SETTLED)

# Mağazadan gelen ham durum sözcükleri. Listeler AÇIK yazılır: tanımadığımız
# bir sözcüğü "başarısız" saymak, parası çekilmiş müşteriye ikinci link
# göndermek demektir.
_PAID_WORDS = frozenset({
    "paid", "success", "successful", "completed", "complete", "captured",
    "capture", "approved", "settled", "processed", "odendi", "basarili",
})
_PENDING_WORDS = frozenset({
    "pending", "created", "new", "waiting", "open", "active", "sent",
    "processing", "initiated", "in_progress", "bekliyor", "olusturuldu",
})
_EXPIRED_WORDS = frozenset({"expired", "timeout", "timed_out", "lapsed", "suresi_doldu"})
_CANCELLED_WORDS = frozenset({"cancelled", "canceled", "void", "voided", "revoked", "iptal"})
#: NET RED. Banka isteği geri çevirdi ve para ÇEKİLMEDİ; yeni link gönderilebilir.
_FAILED_WORDS = frozenset({
    "failed", "failure", "declined", "decline", "rejected", "reject", "denied",
    "do_not_honor", "insufficient_funds", "invalid_card", "card_error",
    "3d_failed", "auth_failed", "basarisiz", "reddedildi",
})
#: Para bankada ASILI. Provizyon alınmış ama sipariş kapanmamış; iptal (void)
#: gerekir. Bu durum "başarısız" DEĞİLDİR ve yeni link üretimini kilitler.
_VOID_WORDS = frozenset({
    "authorized", "authorization", "preauth", "pre_auth", "pre_authorized",
    "reserved", "on_hold", "hold", "void_required", "reversal_required",
    "orphan", "orphaned", "3d_ok_no_order", "provizyon",
})

_STATUS_LABELS = {
    DRAFT: "Taslak",
    LINKED: "Link üretildi",
    SENT: "SMS gönderildi",
    PAID: "Ödendi",
    EXPIRED: "Süresi doldu",
    CANCELLED: "İptal edildi",
    FAILED: "Banka reddetti",
    UNKNOWN: "Durum okunamadı",
    VOID_REQUIRED: "Provizyon açık",
    SETTLED: "Elden kapatıldı",
}

_STATUS_TONES = {
    DRAFT: "dim", LINKED: "info", SENT: "info", PAID: "good", EXPIRED: "dim",
    CANCELLED: "dim", FAILED: "bad", UNKNOWN: "warn", VOID_REQUIRED: "warn",
    SETTLED: "good",
}

#: Kilitli durumda o satır için YENİ LİNK ÜRETİLMEZ. `paid` bariz; `unknown`
#: ve `void_required` ise paranın çekilmiş OLABİLECEĞİ durumlardır — ikinci
#: link ikinci çekim riskidir.
_RELINK_LOCKED = frozenset({PAID, UNKNOWN, VOID_REQUIRED, SETTLED})

#: Para çekilmiş olabilir uyarısı. TEK YERDE durur: iki ayrı yerde yazılsaydı
#: biri düzeltilip diğeri unutulurdu.
DOUBLE_CHARGE_WARNING = (
    "Para çekilmiş olabilir; tekrar link göndermeyin. Önce banka ekstresinden "
    "ya da mağaza işlem kaydından doğrulayın."
)

_STATUS_NOTES = {
    PAID: "Ödeme alındı. Sipariş ve fatura mağaza tarafında oluşur.",
    FAILED: "Banka isteği geri çevirdi; para çekilmedi. Yeni link gönderilebilir.",
    UNKNOWN: DOUBLE_CHARGE_WARNING,
    VOID_REQUIRED: (
        "Provizyon alınmış ama sipariş kapanmamış. " + DOUBLE_CHARGE_WARNING
        + " Tutarın serbest kalması için bankadan iptal (void) istenmelidir."
    ),
    EXPIRED: "Bağlantının süresi doldu; ödeme alınmadı. Yeni link üretilebilir.",
    CANCELLED: "Bağlantı iptal edildi; ödeme alınmadı.",
    SETTLED: "Tahsilat elden (havale/nakit) kapatıldı; karttan çekim yapılmadı.",
}

#: Elden kapatma yönteminin MAĞAZADAKİ karşılığı.
#:
#: `POST /api/admin/transactions` gövdesi `paymentMethod` olarak Bagisto'nun
#: ödeme yöntemi KODUNU ister; "havale"/"nakit" kabul edilmez. Kodlar canlı
#: mağazanın kendi ayarından okundu (`configuration?slug=sales.payment_methods`):
#: `moneytransfer` = "Havale/EFT-FAST ile Ödeme", `cashondelivery` = "Kapıda
#: Ödeme". Kurulum farklıysa `settle_payment_methods` ayarı ezer.
SETTLE_METHODS = {"havale": "moneytransfer", "nakit": "cashondelivery"}

#: SMS şablonunun tanıdığı yer tutucular. Ekranda çip olarak gösterilir.
PLACEHOLDERS = {
    "ad": "Müşterinin adı soyadı",
    "tutar": "Tahsil edilecek toplam (ör. 1.250,00 TL)",
    "link": "Ödeme bağlantısı",
    "aciklama": "Personelin yazdığı açıklama",
    "kod": "Talep numarası (telefonda söylenir)",
    "kurum": "Gönderen kurum adı",
}

#: Varsayılan şablon Türkçe harf İÇERMEZ: 'ödemenizi' yazmak 160 karakterlik
#: sınırı 70'e düşürmüyor ama iki septet yiyor ve uzun metinlerde parça
#: sayısını artırıyor. Kullanıcı isterse Türkçe yazar, ekran maliyeti söyler.
#: Ayrıca KISADIR: tipik bir bağlantı ve talep numarasıyla doldurulduğunda
#: 160 karakterlik tek parçaya sığar. Uzun bir varsayılan, her gönderimde
#: sessizce iki kredi harcardı.
DEFAULT_TEMPLATE = (
    "Sayin {ad}, {tutar} odemenizi su baglantidan yapabilirsiniz: {link} "
    "Talep no: {kod} - {kurum}"
)

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_]+)\}")

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def text(value: Any) -> str:
    """Metne indirger ve kırpar. `None` boş dizedir, "None" değil."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def fold(value: Any) -> str:
    """Aksansız küçük harf — arama karşılaştırması için."""
    return text(value).translate(_TR_MAP).lower()


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def today_iso() -> str:
    """Yerel takvim günü. `utcnow()` kullanılmaz: gece yarısından sonraki üç
    saatte UTC bir gün geride kalır ve "son ödeme günü" yanlış yazılır."""
    return datetime.now(UTC).astimezone().date().isoformat()


def to_kurus(value: Any) -> int:
    """Mağazadan gelen ondalık tutarı kuruşa çevirir. Çözülemezse 0.

    Para İÇERİDE HER YERDE KURUŞTUR (tam sayı). Ondalık kayan noktayla
    taşımak, 1.250,00'ın 1249,999999 olarak yuvarlanmasına açık kapıdır.
    """
    if value is None or value == "":
        return 0
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return 0


def from_kurus(kurus: Any) -> str:
    """Kuruşu ondalık METNE çevirir: 125000 → '1250.00'. (Rapor/CSV içindir.)"""
    return f"{Decimal(as_int(kurus)) / 100:.2f}"


def to_amount(kurus: Any) -> float:
    """Kuruşu mağazanın beklediği JSON SAYISINA çevirir: 125000 → 1250.0.

    `POST /api/admin/transactions` gövdesinde `amount` SAYIDIR (şemada
    `type: number`). Metin göndermek Laravel'in `numeric` kuralını çoğu
    sürümde geçer ama sözleşme sayı ister; ondalığı iki basamağa sabitleyip
    sayıya çeviriyoruz.
    """
    return float(f"{Decimal(as_int(kurus)) / 100:.2f}")


def money_tr(kurus: Any) -> str:
    """SMS metnine ve rapora giren Türkçe para gösterimi: 125000 → '1.250,00 TL'.

    `₺` işareti KULLANILMAZ: GSM-7 kümesinde yok, tek başına tüm mesajı
    UCS-2'ye düşürüp 160 karakterlik sınırı 70'e indiriyor.
    """
    amount = Decimal(as_int(kurus)) / 100
    whole, _, fraction = f"{amount:.2f}".partition(".")
    negative = whole.startswith("-")
    digits = whole.lstrip("-")
    groups = []
    while len(digits) > 3:
        groups.insert(0, digits[-3:])
        digits = digits[:-3]
    groups.insert(0, digits)
    return f"{'-' if negative else ''}{'.'.join(groups)},{fraction} TL"


def reason_error(reason: str) -> str:
    """Gerekçe denetimi. Backend'de DE yapılır (K9)."""
    if len(text(reason)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına yazılır."
    return ""


def phone_error(raw: str) -> str:
    """Numara denetimi. SMS gitmeden ÖNCE yapılır: sağlayıcı reddettiğinde
    kullanıcı 'gönderildi' sanıp beklemesin."""
    try:
        normalize_msisdn(text(raw))
    except ValueError:
        return "Cep numarası 10 haneli olmalı ve 5 ile başlamalı (5XXXXXXXXX)."
    return ""


def normal_phone(raw: str) -> str:
    """Numarayı 5XXXXXXXXX biçimine indirger; çözülemezse ham hâlini döner."""
    try:
        return normalize_msisdn(text(raw))
    except ValueError:
        return text(raw)


def request_code(seed: str = "") -> str:
    """Personelin telefonda okuyabileceği kısa talep numarası: TAH-20260813-7F3A.

    Yerel kimliği (`id`) okutmuyoruz: art arda giden sayılar müşteriye başka
    müşterilerin talep sayısını da söyler.
    """
    stamp = datetime.now(UTC).astimezone().strftime("%Y%m%d")
    tail = (text(seed) or datetime.now(UTC).strftime("%H%M%S%f"))[-6:]
    return f"TAH-{stamp}-{tail.upper()}"


# ================================================================ tutar/KDV

@dataclass(frozen=True)
class Line:
    """Tahsilat kalemi.

    `kind`: 'free' (serbest tutar) | 'product' (mağaza ürünü)
    `unit_net`/`unit_gross`: kuruş. Hangisinin dolu olduğu `tax_included`
    bayrağına göre `breakdown` içinde çözülür.
    """

    kind: str
    label: str
    quantity: int
    amount: int
    tax_rate: Decimal
    product_id: int = 0
    sku: str = ""
    #: False = oran mağazadan OKUNAMADI. Kırılım 0 KDV ile çizilir ama ekran
    #: "%0" yazmaz; sıfır ile "bilinmiyor" aynı şey değildir.
    rate_known: bool = True


def _rate_value(row: dict[str, Any]) -> Decimal | None:
    """Bir oran satırından yüzdeyi okur.

    CANLI ALAN ADI `taxRate`'TİR. Bagisto'nun admin API'si koleksiyonları
    camelCase'e çeviriyor (`AdminCollectionEnvelopeNormalizer`); yalnız
    `tax_rate` aramak canlıda HER ZAMAN boş döner ve KDV sessizce sıfırlanır.
    Snake_case biçim eski sürümler ve testler için korunur.
    """
    for key in ("taxRate", "tax_rate", "rate"):
        if key in row:
            found = _decimal(row.get(key))
            if found is not None:
                return found
    return None


def tax_rate_for(category_id: int, categories: Any, rates: Any) -> Decimal | None:
    """Ürünün KENDİ vergi kategorisinin oranı.

    ÜÇ DURUM, ÜÇÜ DE AYRI: kategori yoksa `Decimal(0)` (ürün vergisiz, KESİN
    cevap), oran çözüldüyse oranın kendisi, çözülemediyse **None**.

    NEDEN None VE NEDEN SIFIR DEĞİL: çözülemeyeni sıfır saymak, faturayı KDV
    kadar eksik keser ve ekranda "KDV %0" diye SESSİZCE doğru görünür. %20
    varsaymak da aynı ölçüde uydurmadır. `None` dönülür; çağıran taraf bunu
    ekranda "vergi oranı okunamadı" diye yazar ve tahsilatı başlatmaz.

    CANLI TUZAK: `/settings/tax-categories` LİSTESİ `taxRates: null` döner
    (oranlar yalnız TEKİL uçta gömülüdür) ve `/settings/tax-rates` satırları
    kategori kimliği TAŞIMAZ. Yani bugün geçitten gelen iki listeyle dolu bir
    kategorinin oranı çözülemez; `store.api → tax_category(id)` metodu
    gerekiyor (README'de istendi). O gün gelene kadar burası dürüstçe None
    döner.
    """
    wanted = as_int(category_id)
    if not wanted:
        # Ürünün vergi kategorisi yok: KDV'siz. Canlı katalogda 1.421 ürünün
        # tamamı böyle (`taxCategoryId: null`).
        return Decimal(0)

    rate_ids: list[int] = []
    for item in rows_of(categories):
        if as_int(item.get("id")) != wanted:
            continue
        # Bagisto vergi kategorisi oranları ilişki olarak gömülü ya da yalnız
        # kimlik listesi olarak gelebiliyor; ikisi de karşılanır.
        for rate in item.get("taxRates") or item.get("tax_rates") or []:
            if isinstance(rate, dict):
                found = _rate_value(rate)
                if found is not None:
                    return found
                rate_ids.append(as_int(rate.get("id")))
            else:
                rate_ids.append(as_int(rate))

    for item in rows_of(rates):
        same_category = as_int(item.get("taxCategoryId")
                               or item.get("tax_category_id")) == wanted
        if same_category or (rate_ids and as_int(item.get("id")) in rate_ids):
            found = _rate_value(item)
            if found is not None:
                return found
    return None


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def product_category_id(row: Any) -> int:
    """Ürün satırından vergi kategorisi kimliği.

    CANLI ALAN ADI `taxCategoryId`'DİR ve ürün LİSTESİNDE de gelir; bu yüzden
    oran için ürün başına ayrıca detay çekmeye gerek yoktur (geçidin dakikada
    55 istek kovası 20 sonuçluk bir aramada tek başına dolardı).
    """
    data = row if isinstance(row, dict) else {}
    return as_int(data.get("taxCategoryId") or data.get("tax_category_id"))


def product_price(row: Any, *, today: str = "") -> tuple[int, bool]:
    """Ürünün o an geçerli fiyatı (kuruş) ve indirimli olup olmadığı.

    İNDİRİM TARİHLİDİR: `specialPrice` dolu olsa bile penceresi geçmişse
    müşteriden liste fiyatı çekilir. Pencereyi denetlemeden indirimli fiyatı
    yazmak, süresi dolmuş kampanyayı yeniden açmak olurdu.

    Canlı alan adları camelCase'tir (`specialPrice`, `specialPriceFrom/To`);
    snake_case biçim yalnız geriye uyumluluk için okunur.
    """
    data = row if isinstance(row, dict) else {}
    listed = to_kurus(data.get("price"))
    special_raw = data.get("specialPrice", data.get("special_price"))
    special = to_kurus(special_raw)
    if special <= 0 or (listed and special >= listed):
        return listed, False

    day = text(today) or today_iso()
    start = text(data.get("specialPriceFrom") or data.get("special_price_from"))[:10]
    end = text(data.get("specialPriceTo") or data.get("special_price_to"))[:10]
    if (start and day < start) or (end and day > end):
        return listed, False
    return special, True


def rows_of(payload: Any) -> list[dict[str, Any]]:
    """Geçidin `{"items": [...]}` zarfını da düz listeyi de kabul eder.

    Referans uçları sürüme göre ikisinden birini döndürüyor; çağıran tarafın
    hangisi geldiğini bilmek zorunda kalması, her ekranda aynı `if`i
    tekrarlatırdı.
    """
    if isinstance(payload, dict):
        rows = payload.get("items")
        return [row for row in (rows or []) if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


def breakdown(lines: list[Line], *, prices_include_tax: bool = False) -> dict[str, Any]:
    """Tutar kırılımı: matrah · KDV · toplam.

    İKİ KALEM TİPİ AYRI DAVRANIR (ekranın en kritik kuralı):
      · `free`    — SERBEST TUTAR KDV'SİZ gider. Personelin yazdığı rakam
                    neyse müşteriden o çekilir; üstüne vergi eklenmez.
      · `product` — ürünün KENDİ vergi kategorisi geçerlidir.

    `prices_include_tax` mağaza ayarının kopyasıdır: Bagisto'da ürün fiyatı
    KDV dâhil de girilebiliyor. Dâhilse fiyat brütten ayrıştırılır, hariçse
    üstüne eklenir. Yanlış varsayım faturayı KDV kadar kaydırırdı.
    """
    rows: list[dict[str, Any]] = []
    net_total = 0
    tax_total = 0

    for line in lines:
        quantity = max(1, as_int(line.quantity, 1))
        rate = line.tax_rate if line.kind == "product" else Decimal(0)
        total = as_int(line.amount) * quantity

        if rate <= 0:
            net, tax = total, 0
        elif prices_include_tax:
            net_dec = (Decimal(total) * 100 / (100 + rate)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP)
            net = int(net_dec)
            tax = total - net
        else:
            net = total
            tax = int((Decimal(total) * rate / 100).quantize(
                Decimal(1), rounding=ROUND_HALF_UP))

        net_total += net
        tax_total += tax
        rows.append({
            "kind": line.kind,
            "label": line.label,
            "quantity": quantity,
            "unit": as_int(line.amount),
            "net": net,
            "tax": tax,
            "gross": net + tax,
            "taxRate": float(rate),
            "productId": as_int(line.product_id),
            "sku": line.sku,
            "taxFree": line.kind == "free",
            "rateKnown": bool(line.rate_known),
        })

    return {
        "lines": rows,
        "net": net_total,
        "tax": tax_total,
        "gross": net_total + tax_total,
        "pricesIncludeTax": bool(prices_include_tax),
    }


def lines_from_payload(payload: Any) -> tuple[list[Line], list[str]]:
    """Ekranın gönderdiği kalemleri `Line` listesine çevirir.

    VERGİ ORANI BURADA ÇÖZÜLMEZ: ürün kalemleri `taxRate` alanını dolu
    getirmelidir (servis oranı mağazadan bir kez çeker). Bu fonksiyonun ağa
    çıkmaması, tutar hesabının ağsız test edilebilmesinin tek şartıdır.

    Tanınmayan kalem SESSİZCE DÜŞMEZ: hata listesine yazılır. Para söz
    konusuyken sessiz düşürme, eksik tahsilat demektir.
    """
    lines: list[Line] = []
    problems: list[str] = []

    for index, raw in enumerate(payload or [], start=1):
        if not isinstance(raw, dict):
            problems.append(f"{index}. kalem okunamadı.")
            continue
        kind = text(raw.get("kind")) or ("product" if raw.get("productId") else "free")
        amount = as_int(raw.get("amount"))
        quantity = max(1, as_int(raw.get("quantity"), 1))
        if amount <= 0:
            problems.append(f"{index}. kalemin tutarı sıfır ya da eksi.")
            continue
        if kind == "product":
            product_id = as_int(raw.get("productId"))
            if not product_id:
                problems.append(f"{index}. kalemde ürün seçilmemiş.")
                continue
            rate = _decimal(raw.get("taxRate")) or Decimal(0)
            lines.append(Line(kind="product", label=text(raw.get("label")) or f"#{product_id}",
                              quantity=quantity, amount=amount, tax_rate=rate,
                              product_id=product_id, sku=text(raw.get("sku")),
                              rate_known=bool(raw.get("rateKnown", True))))
        else:
            lines.append(Line(kind="free", label=text(raw.get("label")) or "Serbest tutar",
                              quantity=quantity, amount=amount, tax_rate=Decimal(0)))

    return lines, problems


# =============================================================== durum eşlemesi

def map_status(raw: Any, *, local: str = "") -> dict[str, Any]:
    """Mağazanın ham durumunu ekranın diline çevirir.

    EKRANIN EN ÖNEMLİ KURALI BURADA: tanımadığımız hiçbir sözcük `failed`
    olmaz. "Başarısız" yazmak personele "tekrar link gönder" dedirtir;
    provizyonu alınmış bir karta ikinci link göndermek müşteriden iki kez
    para çekmektir. Tanımadığımız her şey `unknown` olur ve o satırda yeni
    link üretimi KİLİTLENİR.
    """
    word = fold(raw).replace("-", "_").replace(" ", "_")
    if not word:
        code = local if local in LOCAL_STATUSES else UNKNOWN
        if not local:
            code = UNKNOWN
    elif word in _PAID_WORDS:
        code = PAID
    elif word in _VOID_WORDS:
        code = VOID_REQUIRED
    elif word in _FAILED_WORDS:
        code = FAILED
    elif word in _EXPIRED_WORDS:
        code = EXPIRED
    elif word in _CANCELLED_WORDS:
        code = CANCELLED
    elif word in _PENDING_WORDS:
        code = LINKED if local in ("", DRAFT, LINKED) else local
    else:
        code = UNKNOWN

    if code not in LOCAL_STATUSES:
        code = UNKNOWN

    return {
        "code": code,
        "label": _STATUS_LABELS[code],
        "tone": _STATUS_TONES[code],
        "note": _STATUS_NOTES.get(code, ""),
        "relinkLocked": code in _RELINK_LOCKED,
        "moneyMayBeTaken": code in (UNKNOWN, VOID_REQUIRED),
        "raw": text(raw),
    }


def status_view(code: str) -> dict[str, Any]:
    """Yerel durumun ekran görünümü (ham durum yokken kullanılır)."""
    safe = code if code in LOCAL_STATUSES else UNKNOWN
    return {
        "code": safe,
        "label": _STATUS_LABELS[safe],
        "tone": _STATUS_TONES[safe],
        "note": _STATUS_NOTES.get(safe, ""),
        "relinkLocked": safe in _RELINK_LOCKED,
        "moneyMayBeTaken": safe in (UNKNOWN, VOID_REQUIRED),
        "raw": "",
    }


def link_row(payload: Any) -> dict[str, Any]:
    """Mağazadan gelen ödeme linki kaydından ekranın okuduğu alanlar.

    HER ALAN İKİ BİÇİMDE ARANIR. Bagisto'nun admin yüzeyi camelCase döner;
    `/api/admin/bbd/*` uçları HENÜZ YAYINDA DEĞİL (bugün 404) ve alan adları
    yayınlandığında doğrulanmalıdır. İki biçimi de okumak, uç geldiğinde
    ekranın boş "—" ile dolmasını önler.
    """
    row = payload if isinstance(payload, dict) else {}
    return {
        "token": text(row.get("token") or row.get("id")),
        "url": text(row.get("url") or row.get("link") or row.get("paymentUrl")
                    or row.get("payment_url")),
        "status": text(row.get("status") or row.get("state")),
        "orderId": as_int(row.get("orderId") or row.get("order_id")),
        "invoiceId": as_int(row.get("invoiceId") or row.get("invoice_id")),
        "amount": to_kurus(row.get("amount") or row.get("grandTotal")
                           or row.get("grand_total")),
        "expiresAt": text(row.get("expiresAt") or row.get("expires_at")),
    }


def attempt_row(payload: Any) -> dict[str, Any]:
    """POS denemesi. Kart verisi sunucuda maskelidir; burada maskeleme yapılmaz
    — yapılsaydı "maskeledik" yanılgısı üretir ve ham veri yine ekrana gelirdi."""
    row = payload if isinstance(payload, dict) else {}
    verdict = map_status(row.get("status") or row.get("result"))
    # Canlı `order_transactions.data` sözlüğünde kart `masked_number`,
    # sağlayıcı `gateway` adıyla duruyor; BBD ucu yayınlanınca hangi adı
    # kullandığı doğrulanmalı. İki biçim de okunur.
    inner = row.get("data") if isinstance(row.get("data"), dict) else {}
    return {
        "id": as_int(row.get("id")),
        "createdAt": text(row.get("createdAt") or row.get("created_at")),
        "amount": to_kurus(row.get("amount")),
        "card": text(row.get("maskedCard") or row.get("masked_card") or row.get("card")
                     or row.get("cardLastFour") or row.get("card_last_four")
                     or inner.get("masked_number")),
        "bank": text(row.get("bank") or row.get("provider") or row.get("pos")
                     or row.get("paymentMethod") or inner.get("gateway")),
        "installment": as_int(row.get("installment") or inner.get("installment"), 1),
        "errorCode": text(row.get("errorCode") or row.get("error_code")),
        "errorMessage": text(row.get("errorMessage") or row.get("error_message")),
        "status": verdict,
    }


# ==================================================================== SMS

def render_template(body: str, values: dict[str, Any]) -> dict[str, Any]:
    """Şablonu doldurur ve EKSİK/TANINMAYAN yer tutucuları geri bildirir.

    Doldurulamayan yer tutucu olduğu gibi bırakılır: boşa çevirmek
    "Sayin , 0,00 TL" gibi bir mesajı müşteriye gönderir; süslü parantezi
    ekranda görmek personeli uyarır.
    """
    missing: list[str] = []
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in PLACEHOLDERS:
            unknown.append(name)
            return match.group(0)
        value = text(values.get(name))
        if not value:
            missing.append(name)
            return match.group(0)
        return value

    rendered = _PLACEHOLDER_RE.sub(replace, text(body))
    return {
        "text": rendered,
        "missing": sorted(set(missing)),
        "unknown": sorted(set(unknown)),
    }


def sms_plan(message: str) -> dict[str, Any]:
    """Metnin kaç SMS gideceği, hangi karakterlerin pahalılaştırdığı ve
    sadeleştirilmiş hâli. Gönderilmeden ÖNCE ekranda durur — üç parça SMS'i
    faturada görmek geç olur."""
    body = text(message)
    plan = plan_text(body)
    plain = simplify(body)
    plain_plan = plan_text(plain)
    return {
        "text": body,
        "length": len(body),
        "units": plan.units,
        "parts": plan.parts,
        "remaining": plan.remaining,
        "unicode": plan.unicode,
        "encoding": plan.encoding or "",
        "offending": offending(body),
        "simplified": plain,
        "simplifiedParts": plain_plan.parts,
        "savedParts": max(0, plan.parts - plain_plan.parts),
    }


# ============================================================ süzgeç → SQL

def filter_clause(*, q: str = "", status: str = "", statuses: Any = (), start: str = "",
                  end: str = "", min_amount: int | None = None,
                  max_amount: int | None = None) -> tuple[str, list[Any]]:
    """Talep listesinin WHERE parçasını üretir — DEĞER GÖMMEZ, yer tutucu koyar.

    Süzgeç metni doğrudan SQL'e yazılsaydı, müşteri adına tırnak koyan
    personel sorguyu kırardı (en iyi ihtimalle). Değerler her zaman
    parametredir; burada üretilen dize yalnız `?` içerir.
    """
    parts: list[str] = []
    params: list[Any] = []

    needle = text(q)
    if needle:
        parts.append("(full_name LIKE ? OR phone LIKE ? OR code LIKE ? OR email LIKE ? "
                     "OR note LIKE ?)")
        params.extend([f"%{needle}%"] * 5)
    if status and status in LOCAL_STATUSES:
        parts.append("status = ?")
        params.append(status)
    # Çoklu durum: "açık link" raporu hem `linked` hem `sent` ister. Tek
    # `status` ile yazıldığında SMS gitmiş her satır rapordan düşüyordu —
    # yani rapor tam da göstermesi gereken satırları göstermiyordu.
    wanted = [code for code in (statuses or ()) if code in LOCAL_STATUSES]
    if wanted:
        parts.append(f"status IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    # Tarih karşılaştırması GÜN ÖNEKİ üzerinden yapılır. `created_at` ISO
    # damgadır ve saat dilimi eki taşır ("…+03:00"); tam damgayla kıyaslamak
    # son günün kayıtlarını dışarıda bırakırdı.
    if text(start):
        parts.append("substr(created_at, 1, 10) >= ?")
        params.append(text(start)[:10])
    if text(end):
        parts.append("substr(created_at, 1, 10) <= ?")
        params.append(text(end)[:10])
    if min_amount is not None:
        parts.append("gross >= ?")
        params.append(as_int(min_amount))
    if max_amount is not None:
        parts.append("gross <= ?")
        params.append(as_int(max_amount))

    return (" WHERE " + " AND ".join(parts)) if parts else "", params


# ============================================================== kayıt görünümü

def request_view(row: Any, *, link_base: str = "") -> dict[str, Any]:
    """Yerel talep satırını ekranın JSON'una çevirir (camelCase)."""
    data = dict(row or {})
    verdict = status_view(text(data.get("status")))
    if text(data.get("store_status")):
        verdict = map_status(data.get("store_status"), local=text(data.get("status")))

    url = text(data.get("link"))
    token = text(data.get("token"))
    if not url and token and link_base:
        url = f"{link_base.rstrip('/')}/{token}"

    return {
        "id": as_int(data.get("id")),
        "code": text(data.get("code")),
        "fullName": text(data.get("full_name")),
        "phone": text(data.get("phone")),
        "email": text(data.get("email")),
        "city": text(data.get("city")),
        "district": text(data.get("district")),
        "address": text(data.get("address")),
        "note": text(data.get("note")),
        "net": as_int(data.get("net")),
        "tax": as_int(data.get("tax")),
        "gross": as_int(data.get("gross")),
        "orderId": as_int(data.get("order_id")),
        "invoiceId": as_int(data.get("invoice_id")),
        "token": token,
        "link": url,
        "status": verdict,
        "storeStatus": text(data.get("store_status")),
        "smsState": text(data.get("sms_state")),
        "smsAt": text(data.get("sms_at")),
        "settleMethod": text(data.get("settle_method")),
        "settleRef": text(data.get("settle_ref")),
        "reason": text(data.get("reason")),
        "actor": text(data.get("actor")),
        "createdAt": text(data.get("created_at")),
        "updatedAt": text(data.get("updated_at")),
    }


def can_relink(view: dict[str, Any]) -> tuple[bool, str]:
    """Bu talep için yeni link üretilebilir mi ve üretilemiyorsa NEDEN."""
    status = view.get("status") or {}
    if status.get("relinkLocked"):
        return False, status.get("note") or DOUBLE_CHARGE_WARNING
    return True, ""
