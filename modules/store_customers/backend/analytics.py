"""Müşteri verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Müşteri ekranının zor kısmı veri çekmek değil, çekilen
veriyi YORUMLAMAK: RFM segmenti, yaşam boyu değer, tekrar eden oranı, altı
aylık harcama eğrisi, yorum puanı dağılımı. Bunlar servise gömülseydi tek
satırı bile ağsız test edilemezdi; burada hepsi girdi→çıktı fonksiyonudur.

ON TUZAK — her birinin karşılığı bu dosyada bir fonksiyondur:

 1. Müşteri SİLİNMEZ                → yalnız `status` 0/1; silme yolu yok.
 2. RFM nüfusa görelidir            → `segment_of` MUTLAK eşik kullanır
                                      (`thresholds`), çünkü Bagisto sipariş
                                      toplamıyla süzdürmüyor ve her satır için
                                      nüfusu taramak ekranı dakikalarca kilitler.
 3. Sipariş/harcama alan adı sürüme → `orders_of` / `spend_of` çok adlı okur;
    göre değişiyor                    bulamazsa `None` döner, SIFIR UYDURMAZ.
 4. Ortalama sepet sıfıra bölünür   → `average_basket` sipariş yoksa `None`.
 5. Ad alanı bazen yok              → `full_name` ad+soyaddan kurar.
 6. Tarih "2026-01-02 13:44:55"     → `day` ilk 10 karakteri alır, ISO varsayar.
 7. Yorumda spam durumu YOK         → `REVIEW_STATES` üçtür; spam yerel etiket.
 8. Puan süzgeci çoklu ve serbest   → `rating_list` 1..5 dışını atar.
 9. Anonimleştirme GERİ ALINAMAZ    → `reason_error` + ayrı izin + kuru prova.
10. Bülten iki yerde durur          → `consent_view` kayıt üzerindeki değeri
    (kayıt ve abone tablosu)          esas alır ve KAYNAĞINI söyler.
11. YANIT camelCase, İSTEK snake    → `pick` her adı iki biçimde de dener;
                                      yazma gövdeleri snake_case kalır.
12. Müşteri kaydında son sipariş     → `order_stats` / `apply_order_stats`
    tarihi ve şehir YOK               siparişlerden toplulaştırır.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (store_api) da 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Anonimleştirme uyarısı — ekranda ve onay kutusunda birebir bu metin çıkar.
ANONYMIZE_NOTICE = (
    "Anonimleştirme GERİ ALINAMAZ: ad, e-posta ve telefon kalıcı olarak silinir. "
    "Siparişler ve faturalar kalır; müşteriye bağlanamaz hâle gelir."
)

#: RFM segmenti varsayılan eşikleri. Hepsi ayarla ezilebilir (config).
#: Gün cinsinden olanlar SON SİPARİŞTEN bugüne, `champion_spend` kuruştur.
DEFAULT_THRESHOLDS = {
    "recent_days": 90,
    "sleeping_days": 180,
    "lost_days": 365,
    "loyal_orders": 3,
    "champion_orders": 6,
    "champion_spend": 250_000,
    "new_days": 30,
}

SEGMENT_LABELS = {
    "champion": "Şampiyon",
    "loyal": "Sadık",
    "new": "Yeni",
    "at_risk": "Riskli",
    "sleeping": "Uykuda",
    "lost": "Kayıp",
    "none": "Hiç sipariş vermemiş",
    "unknown": "Bilinmiyor",
}

#: Ekranda çip/süzgeç olarak görünen sıra. `unknown` süzgeçte YOKTUR: veri
#: eksikliği bir müşteri sınıfı değildir, satırda rozet olarak görünür.
SEGMENT_ORDER = ("champion", "loyal", "new", "at_risk", "sleeping", "lost", "none")

#: Bagisto'nun yorum durumları. SPAM YOKTUR (TUZAK 7) — operatörün "spam"
#: kararı mağazada reddedilmiş yoruma karşılık gelir ve ayrıca yerel etikete
#: yazılır; yargı kaybolmaz.
REVIEW_STATES = ("pending", "approved", "disapproved")

REVIEW_LABELS = {
    "pending": "Bekliyor",
    "approved": "Onaylı",
    "disapproved": "Reddedildi",
}

#: Yorum eylemi → mağazaya gidecek durum. `spam` de reddedilmiş olur; farkı
#: yerel etikettir (`mod_store_customers_review_flags.spam`).
REVIEW_ACTIONS = {
    "approve": "approved",
    "reject": "disapproved",
    "spam": "disapproved",
    "pending": "pending",
}

#: İzin/onay alanları — müşteri kaydında hangi anahtarla durduğu sürümden
#: sürüme değişiyor, hepsi denenir.
CONSENT_FIELDS = {
    "newsletter": ("subscribed_to_news_letter", "subscribed_to_newsletter", "newsletter"),
    "verified": ("is_verified", "verified", "email_verified"),
    "sms": ("sms_consent", "allow_sms", "subscribed_to_sms"),
    "gdpr": ("gdpr_consent", "kvkk_consent", "agreement_accepted"),
}

CONSENT_LABELS = {
    "newsletter": "Bülten aboneliği",
    "verified": "E-posta doğrulandı",
    "sms": "SMS izni",
    "gdpr": "KVKK aydınlatma onayı",
}


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow()` kullanılmaz: gece yarısından sonraki üç saatte UTC bir
    gün geride kalır ve "son 30 günde kaydolan" sayısı bir gün kayardı.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def text(value: Any) -> str:
    """`None` ile `0` ayrımını koruyarak metne çevirir."""
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
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar — binlerce müşterinin
    yaşam boyu değeri toplanınca görünür bir sapma olur.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_kurus(kurus: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def day(value: Any) -> str:
    """Tarih alanının GÜN kısmı (TUZAK 6).

    Bagisto kimi uçta `2026-01-02T10:00:00Z`, kimi uçta `2026-01-02 10:00:00`
    döndürüyor; ikisinin de ilk on karakteri ISO gündür.
    """
    return text(value)[:10]


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


#: Türkçe harf katlama tablosu. `str.casefold()` KULLANILMAZ: "İ" casefold'da
#: `i` + birleşen nokta üretiyor ve "İzmir" araması `izmir` yazan personeli
#: sonuçsuz bırakıyordu. Tablo, kitin `foldText` davranışıyla aynıdır.
_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "I": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
    "â": "a", "Â": "a", "î": "i", "Î": "i", "û": "u", "Û": "u",
})


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama anahtarı: "Öğrenci" → "ogrenci"."""
    return text(value).translate(_TR_FOLD).lower()


def camel(name: str) -> str:
    """`total_amount_spent` → `totalAmountSpent`."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def pick(raw: Any, *names: str) -> Any:
    """Kayıtta bir alanı, olası adlarından hangisi varsa oradan okur.

    TUZAK 11 — YANIT camelCase, İSTEK snake_case. Bagisto'nun yönetici API
    zarfı (`AdminCollectionEnvelopeNormalizer`) ÇIKTIYI camelCase'e çeviriyor:
    canlıda `createdAt` · `totalOrders` · `grandTotal` · `subscribedToNewsLetter`
    geliyor, `created_at` değil. Sorgu ve gövde tarafında ise camelCase SESSİZCE
    yok sayılıyor (`customerGroupId` süzgeci canlıda 12'nin tamamını döndürdü,
    `customer_group_id` 11 döndürdü) — yani okurken camel, yazarken snake.
    Bu yüzden okuma adları burada tek yerde iki biçimde de denenir; yazma
    gövdeleri snake_case kalır.

    "Yok" ile "boş" ayrımı korunur: önce dolu değer aranır, bulunamazsa boş
    ama VAR OLAN değer döner, o da yoksa `None`.
    """
    if not isinstance(raw, dict):
        return None
    keys: list[str] = []
    for name in names:
        keys.append(name)
        alias = camel(name)
        if alias != name:
            keys.append(alias)
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def days_between(start: str, end: str) -> int | None:
    """`start` ile `end` (ISO gün) arasındaki gün sayısı. Çözülemezse `None`."""
    try:
        first = date.fromisoformat(day(start))
        second = date.fromisoformat(day(end))
    except (TypeError, ValueError):
        return None
    return (second - first).days


# ============================================================ müşteri satırı

def full_name(raw: dict[str, Any]) -> str:
    """Ad Soyad (TUZAK 5): `name` yoksa ad+soyaddan kurulur."""
    joined = text(pick(raw, "name", "full_name"))
    if joined:
        return joined
    parts = [text(pick(raw, "first_name", "firstname")),
             text(pick(raw, "last_name", "lastname"))]
    return " ".join(part for part in parts if part) or "(adsız)"


def orders_of(raw: dict[str, Any]) -> int | None:
    """Sipariş sayısı (TUZAK 3). Alan yoksa `None` — sıfır UYDURULMAZ.

    Sıfır uydurmak, siparişi olan müşteriyi "hiç sipariş vermemiş" segmentine
    düşürür ve pazarlama listesi yanlış kişilere gider.
    """
    value = pick(raw, "orders_count", "order_count", "orders_total", "total_orders")
    if value is None:
        return None
    return max(0, as_int(value))


def spend_of(raw: dict[str, Any]) -> int | None:
    """Yaşam boyu harcama, KURUŞ (TUZAK 3). Alan yoksa `None`."""
    # `total_amount_spent` CANLIDA DOĞRULANDI: müşteri DETAY ucu
    # `totalAmountSpent` veriyor, liste ucu hiç vermiyor (o zaman `None` kalır
    # ve sipariş toplulaştırması devreye girer — bkz. `order_stats`).
    value = pick(raw, "total_spent", "total_amount_spent", "total_base_grand_total",
                 "lifetime_value", "revenue", "total_order_amount")
    if value is None:
        return None
    return to_kurus(value)


def average_basket(spend: int | None, orders: int | None) -> int | None:
    """Ortalama sepet (TUZAK 4). Sipariş yoksa bölme yapılmaz."""
    if spend is None or not orders:
        return None
    return round(spend / orders)


def group_of(raw: dict[str, Any]) -> tuple[int, str]:
    """Müşteri grubu (id, ad).

    CANLIDA gömülü nesnenin adı `group`tur (`{"id":2,"code":"general",
    "name":"Genel"}`); `customer_group` Bagisto'nun kendi ilişki adıdır ve
    başka uçlarda öyle gelebiliyor. İkisi de denenir, yoksa düz alanlar.
    """
    for key in ("group", "customer_group"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            return as_int(nested.get("id")), text(nested.get("name")) or "Grup"
    return as_int(pick(raw, "customer_group_id", "group_id")), \
        text(pick(raw, "customer_group_name", "group_name"))


def group_code_of(raw: dict[str, Any]) -> str:
    """Grubun KODU (`general` · `guest` · `wholesale`).

    Mağaza ayarında varsayılan grup KODLA tutuluyor
    (`customer.settings.create_new_account_options.default_group` = `general`),
    kimlikle değil; ayar ekranı bu yüzden koda ihtiyaç duyar.
    """
    for key in ("group", "customer_group"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            return text(nested.get("code"))
    return text(pick(raw, "customer_group_code", "group_code"))


def city_of(raw: dict[str, Any]) -> str:
    """Şehir — müşteri kaydında yoksa varsayılan adresten okunur."""
    direct = text(pick(raw, "city"))
    if direct:
        return direct
    addresses = raw.get("addresses")
    if isinstance(addresses, list):
        for item in addresses:
            if isinstance(item, dict) and text(item.get("city")):
                return text(item["city"])
    return ""


def flag(raw: dict[str, Any], kind: str) -> bool | None:
    """İzin bayrağı. Alan hiç yoksa `None` — "hayır" ile "bilmiyoruz" ayrıdır."""
    value = pick(raw, *CONSENT_FIELDS[kind])
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(as_int(value, 0))


def customer_row(raw: dict[str, Any], *, today: str = "",
                 thresholds: dict[str, int] | None = None) -> dict[str, Any]:
    """Ham müşteri kaydı → tablonun beklediği satır. Para her yerde kuruş."""
    stamp = today or today_iso()
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    orders = orders_of(raw)
    spend = spend_of(raw)
    last_order = day(pick(raw, "last_order_date", "last_order_at", "latest_order_date"))
    created = day(pick(raw, "created_at", "registered_at"))
    group_id, group_name = group_of(raw)

    recency = days_between(last_order, stamp) if last_order else None
    tenure = days_between(created, stamp) if created else None

    return {
        "id": as_int(raw.get("id")),
        "name": full_name(raw),
        "email": text(pick(raw, "email")),
        "phone": text(pick(raw, "phone", "mobile", "contact_number")),
        "groupId": group_id,
        "groupName": group_name,
        "status": bool(as_int(pick(raw, "status"), 1)),
        "verified": flag(raw, "verified"),
        "newsletter": flag(raw, "newsletter"),
        "orders": orders,
        "spend": spend,
        "avgBasket": average_basket(spend, orders),
        "lastOrderAt": last_order,
        "createdAt": created,
        "recencyDays": recency,
        "tenureDays": tenure,
        "city": city_of(raw),
        "segment": segment_of(orders=orders, spend=spend, recency_days=recency,
                              tenure_days=tenure, thresholds=limits),
        "channel": text(pick(raw, "channel_name", "channel")),
        # Sipariş/harcama mağazadan mı geldi (False) yoksa siparişlerden mi
        # sayıldı (True) — ekran bunu yazıyor, kullanıcı sayının nereden
        # geldiğini bilmeli.
        "computed": False,
        "spendPartial": False,
    }


# ================================================================ segmentasyon

def segment_of(*, orders: int | None, spend: int | None, recency_days: int | None,
               tenure_days: int | None, thresholds: dict[str, int] | None = None) -> str:
    """RFM segmenti — MUTLAK eşiklerle (TUZAK 2).

    Klasik RFM beşlik dilimleri nüfusa göre hesaplanır; bunun için her ekran
    açılışında tüm müşteri listesini taramak gerekirdi. Mağaza dakikada 60
    istek kabul ediyor ve bu tarama satır başına bir sayfa demek. Eşikler
    bunun yerine ayardan gelir: "90 günde alışveriş yapan tazedir" cümlesi
    işletmenin kendi kararıdır ve nüfus değişince kendiliğinden kaymaz —
    raporlar dönemler arasında karşılaştırılabilir kalır.

    TAZELİK BANTLARI (son siparişten bugüne):
      · `> lost_days`                    → kayıp
      · `sleeping_days .. lost_days`     → uykuda; risk artık gerçekleşmiştir,
                                           sıklık ayrımı anlamını yitirir
      · `recent_days .. sleeping_days`   → düzenli alan müşteri RİSKLİ (geri
                                           kazanmaya değer), seyrek alan uykuda
      · `<= recent_days`                 → taze; sıklık ve harcama karar verir

    Sipariş sayısı ya da harcama okunamadıysa `unknown` döner; tahmin edilmez.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    if orders is None:
        return "unknown"
    if orders <= 0:
        return "none"
    if recency_days is None:
        return "unknown"
    if recency_days > limits["lost_days"]:
        return "lost"
    if recency_days > limits["sleeping_days"]:
        return "sleeping"
    if recency_days > limits["recent_days"]:
        # Eskiden düzenli alan ama sesi kesilen müşteri "uykuda" değil RİSKLİ:
        # geri kazanma çabası buna değer, tek siparişlik denemeye değmez.
        return "at_risk" if orders >= limits["loyal_orders"] else "sleeping"
    if orders >= limits["champion_orders"] and (spend or 0) >= limits["champion_spend"]:
        return "champion"
    if orders >= limits["loyal_orders"]:
        return "loyal"
    if tenure_days is not None and tenure_days <= limits["new_days"]:
        return "new"
    return "loyal" if orders >= 2 else "new"


def segment_options() -> list[dict[str, str]]:
    return [{"value": key, "label": SEGMENT_LABELS[key]} for key in SEGMENT_ORDER]


def segment_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = dict.fromkeys((*SEGMENT_ORDER, "unknown"), 0)
    for row in rows:
        key = row.get("segment") or "unknown"
        counts[key] = counts.get(key, 0) + 1
    return counts


def kpi_of(rows: list[dict[str, Any]], *, today: str = "",
           new_days: int = 30) -> dict[str, Any]:
    """Mini KPI şeridi: toplam · yeni · tekrar eden oranı · ortalama YBD.

    Oranın PAYDASI "sipariş vermiş müşteri"dir, tüm kayıt değil: hiç sipariş
    vermemişleri paydaya koymak, kayıt kampanyası yapan her mağazada tekrar
    oranını sahte biçimde düşürür.
    """
    stamp = today or today_iso()
    total = len(rows)
    fresh = 0
    buyers = 0
    repeaters = 0
    spends: list[int] = []
    for row in rows:
        tenure = row.get("tenureDays")
        if tenure is not None and tenure <= new_days:
            fresh += 1
        orders = row.get("orders")
        if orders:
            buyers += 1
            if orders >= 2:
                repeaters += 1
        spend = row.get("spend")
        if spend is not None:
            spends.append(spend)
    return {
        "total": total,
        "new": fresh,
        "newDays": new_days,
        "buyers": buyers,
        "repeatRate": round(repeaters * 100 / buyers, 1) if buyers else None,
        "avgLifetime": round(sum(spends) / len(spends)) if spends else None,
        "measuredAt": stamp,
    }


def match_row(row: dict[str, Any], *, q: str = "", segment: str = "", group_id: int = 0,
              status: str = "", city: str = "", newsletter: str = "",
              orders_min: int | None = None, orders_max: int | None = None,
              spend_min: int | None = None, spend_max: int | None = None,
              registered_from: str = "", registered_to: str = "",
              last_order_from: str = "", last_order_to: str = "") -> bool:
    """Tarama sonucunu süzer — segment görünümünde sunucu süzgeci yoktur.

    Yalnız TARAMA yolunda kullanılır (nüfusun tamamı elimizdeyken). Canlı liste
    yolunda süzme sunucudadır; 50 satırlık sayfayı burada süzmek kullanıcıya
    "10 müşteri var" dedirtirdi.
    """
    needle = fold(q)
    if needle:
        haystack = fold(" ".join([row.get("name", ""), row.get("email", ""),
                                  row.get("phone", ""), row.get("city", "")]))
        if needle not in haystack:
            return False
    if segment and row.get("segment") != segment:
        return False
    if group_id and as_int(row.get("groupId")) != int(group_id):
        return False
    if status in ("0", "1") and bool(row.get("status")) is not (status == "1"):
        return False
    if status == "unverified" and row.get("verified") is not False:
        return False
    if city and fold(city) not in fold(row.get("city")):
        return False
    if newsletter in ("1", "0") and bool(row.get("newsletter")) is not (newsletter == "1"):
        return False

    orders = row.get("orders")
    if orders_min is not None and (orders is None or orders < orders_min):
        return False
    if orders_max is not None and (orders is None or orders > orders_max):
        return False
    spend = row.get("spend")
    if spend_min is not None and (spend is None or spend < spend_min):
        return False
    if spend_max is not None and (spend is None or spend > spend_max):
        return False

    created = row.get("createdAt") or ""
    if registered_from and (not created or created < registered_from):
        return False
    if registered_to and (not created or created > registered_to):
        return False
    last = row.get("lastOrderAt") or ""
    if last_order_from and (not last or last < last_order_from):
        return False
    return not (last_order_to and (not last or last > last_order_to))


# ================================================================== siparişler

def location_city(value: Any) -> str:
    """Sipariş satırındaki `location` alanının ŞEHİR kısmı.

    Canlıda `"MERKEZ, Kırşehir, TR"` biçiminde geliyor ve ilk parça teslimat
    şehridir (aynı siparişin adres kaydında `city` = `MERKEZ`). Müşteri
    kaydının kendisinde şehir YOK; şehir süzgecinin tek dürüst kaynağı budur.
    """
    return text(value).split(",")[0].strip()


def order_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Müşterinin sipariş satırı. Satır tıklanınca `store_orders` açılır."""
    return {
        "id": as_int(pick(raw, "id", "order_id")),
        "increment": text(pick(raw, "increment_id", "order_number")),
        "status": text(pick(raw, "status")),
        "statusLabel": text(pick(raw, "status_label")) or text(pick(raw, "status")),
        "total": to_kurus(pick(raw, "grand_total", "base_grand_total", "total")),
        "createdAt": text(pick(raw, "created_at"))[:19],
        "items": as_int(pick(raw, "total_qty_ordered", "items_count")),
        "customerId": as_int(pick(raw, "customer_id")),
        "city": location_city(pick(raw, "location")),
    }


def order_stats(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Sipariş satırlarını müşteriye göre toplulaştırır.

    NEDEN GEREKLİ. Bagisto'nun müşteri LİSTE ucu sipariş sayısı, harcama ve
    SON SİPARİŞ TARİHİ vermiyor; detay ucu ilk ikisini veriyor ama tarihi o da
    vermiyor. Üçü olmadan RFM segmenti hesaplanamaz ve ekran baştan sona "—"
    ile "Bilinmiyor" dolardı. Sipariş ucu bunların üçünü de taşıyor ve mağaza
    17 siparişlik; nüfusu taramak zaten yapılan işe bir tarama daha ekler.

    Misafir siparişleri (`customerId` = 0) toplulaştırmaya girmez: kime ait
    olduğu bilinmeyen ciroyu bir müşteriye yazmak, yanlış müşteriyi şampiyon
    gösterir.
    """
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        customer_id = as_int(row.get("customerId"))
        if not customer_id:
            continue
        bucket = out.setdefault(customer_id, {"orders": 0, "spend": 0, "lastOrderAt": "",
                                              "city": "", "partial": False})
        bucket["orders"] += 1
        total = row.get("total")
        if total is None:
            # Tutarı okunamayan sipariş sayıya girer, TOPLAMA girmez; harcama
            # eksik olduğunu söyleyebilmek için işaretlenir.
            bucket["partial"] = True
        else:
            bucket["spend"] += int(total)
        stamp = day(row.get("createdAt"))
        if stamp and stamp > bucket["lastOrderAt"]:
            bucket["lastOrderAt"] = stamp
            if row.get("city"):
                bucket["city"] = text(row["city"])
        elif not bucket["city"] and row.get("city"):
            bucket["city"] = text(row["city"])
    return out


def apply_order_stats(row: dict[str, Any], stats: dict[int, dict[str, Any]], *,
                      today: str = "", thresholds: dict[str, int] | None = None,
                      ) -> dict[str, Any]:
    """Müşteri satırını sipariş toplulaştırmasıyla tamamlar (yerinde değiştirir).

    Mağazadan gelen değer VARSA ona dokunulmaz; toplulaştırma yalnız BOŞ
    alanları doldurur. Böylece detay ucunun verdiği `totalAmountSpent` ile
    bizim saydığımız sipariş toplamı çelişmez, mağazanınki kazanır.
    """
    limits = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    stamp = today or today_iso()
    bucket = stats.get(as_int(row.get("id")))
    if bucket is None:
        # Siparişi hiç olmayan müşteri: tarama yapılabildiyse bu bir BİLGİDİR,
        # eksiklik değil — "hiç sipariş vermemiş" segmenti buradan doğar.
        if row.get("orders") is None:
            row["orders"] = 0
            row["spend"] = 0
            row["avgBasket"] = None
            row["computed"] = True
    else:
        if row.get("orders") is None:
            row["orders"] = bucket["orders"]
            row["computed"] = True
        if row.get("spend") is None:
            row["spend"] = bucket["spend"]
            row["spendPartial"] = bool(bucket["partial"])
            row["computed"] = True
        row["avgBasket"] = average_basket(row.get("spend"), row.get("orders"))
        if not row.get("lastOrderAt") and bucket["lastOrderAt"]:
            row["lastOrderAt"] = bucket["lastOrderAt"]
            row["recencyDays"] = days_between(bucket["lastOrderAt"], stamp)
        if not row.get("city") and bucket["city"]:
            row["city"] = bucket["city"]
    row["segment"] = segment_of(orders=row.get("orders"), spend=row.get("spend"),
                                recency_days=row.get("recencyDays"),
                                tenure_days=row.get("tenureDays"), thresholds=limits)
    return row


def month_keys(today: str, months: int = 6) -> list[str]:
    """Son `months` ayın `YYYY-MM` anahtarları, eskiden yeniye."""
    stamp = date.fromisoformat(day(today))
    keys: list[str] = []
    year, month = stamp.year, stamp.month
    for _ in range(months):
        keys.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(keys))


def monthly_spend(orders: list[dict[str, Any]], *, today: str = "",
                  months: int = 6) -> list[dict[str, Any]]:
    """Altı aylık harcama serisi (sparkline). Boş ay SIFIRDIR, atlanmaz.

    Ayı atlamak eğriyi yalan söyletir: iki komşu nokta arasında altı ay
    olabilirken çizgi düz görünür.
    """
    stamp = today or today_iso()
    buckets = dict.fromkeys(month_keys(stamp, months), 0)
    for row in orders:
        key = day(row.get("createdAt"))[:7]
        if key in buckets:
            buckets[key] += int(row.get("total") or 0)
    return [{"month": key, "total": value} for key, value in buckets.items()]


def filter_honored(rows: list[dict[str, Any]], key: str, expected: Any) -> bool | None:
    """Sunucu gönderdiğimiz süzgeci gerçekten uyguladı mı?

    Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar ve tüm listenin
    ilk sayfasını döndürür. Müşteri çekmecesinde bu, başkasının siparişlerini
    bu müşteriye ait göstermek demektir — en kötü hata sınıfı.

    `None` = anlaşılamadı (satırlar o alanı taşımıyor).
    """
    if not rows:
        return None
    seen = False
    for row in rows:
        value = row.get(key)
        if value in (None, "", 0):
            continue
        seen = True
        if str(value) != str(expected):
            return False
    return True if seen else None


# ===================================================================== adresler

def address_row(raw: dict[str, Any]) -> dict[str, Any]:
    parts = [text(pick(raw, "address1", "address", "address_line1")),
             text(pick(raw, "address2", "address_line2"))]
    return {
        "id": as_int(raw.get("id")),
        "title": text(pick(raw, "company_name", "first_name")) or "Adres",
        "name": " ".join(x for x in [text(pick(raw, "first_name")),
                                     text(pick(raw, "last_name"))] if x),
        "line": " ".join(part for part in parts if part),
        "district": text(pick(raw, "state", "district")),
        "city": text(pick(raw, "city")),
        "postcode": text(pick(raw, "postcode", "postal_code")),
        "country": text(pick(raw, "country_name", "country")),
        "phone": text(pick(raw, "phone", "mobile")),
        "default": bool(as_int(pick(raw, "default_address", "is_default"), 0)),
        "vatId": text(pick(raw, "vat_id", "tax_id")),
    }


# ====================================================================== yorum

def review_row(raw: dict[str, Any], *, flags: dict[int, dict[str, Any]] | None = None,
               reply_field: str = "reply") -> dict[str, Any]:
    """Ham yorum kaydı → moderasyon tablosunun satırı.

    `flags` bu modülün YEREL etiketleridir (spam işareti, yanıt kopyası);
    Bagisto'da karşılığı yoktur (TUZAK 7).
    """
    local = (flags or {}).get(as_int(raw.get("id"))) or {}
    product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
    customer = raw.get("customer") if isinstance(raw.get("customer"), dict) else {}
    status = text(pick(raw, "status")).lower() or "pending"
    reply = text(pick(raw, reply_field, "reply", "admin_reply")) or text(local.get("reply"))
    return {
        "id": as_int(raw.get("id")),
        "rating": max(0, min(5, as_int(pick(raw, "rating", "stars")))),
        "title": text(pick(raw, "title", "subject")),
        "comment": text(pick(raw, "comment", "body", "review")),
        "status": status if status in REVIEW_STATES else "pending",
        "statusLabel": REVIEW_LABELS.get(status, status),
        "productId": as_int(product.get("id") or pick(raw, "product_id")),
        "productName": text(product.get("name") or pick(raw, "product_name")) or "(ürün yok)",
        "customerId": as_int(customer.get("id") or pick(raw, "customer_id")),
        "customerName": text(customer.get("name") or pick(raw, "customer_name", "name"))
                        or "Misafir",
        "createdAt": text(pick(raw, "created_at"))[:19],
        "reply": reply,
        "hasReply": bool(reply),
        "spam": bool(as_int(local.get("spam"), 0)),
        "verifiedBuyer": bool(as_int(pick(raw, "verified_purchase", "is_verified_buyer"), 0)),
    }


def rating_list(value: Any) -> list[int]:
    """Puan süzgecini normalleştirir (TUZAK 8): "5,4,x,9" → [4, 5]."""
    if value is None:
        return []
    raw = value if isinstance(value, (list, tuple)) else str(value).split(",")
    out = {as_int(item, 0) for item in raw}
    return sorted(item for item in out if 1 <= item <= 5)


def rating_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1–5 puan dağılımı — yığılmış çubuk için. Sayı her zaman yazılır."""
    counts = {star: 0 for star in range(1, 6)}
    for row in rows:
        star = as_int(row.get("rating"))
        if star in counts:
            counts[star] += 1
    return [{"label": f"{star} yıldız", "value": counts[star]} for star in range(5, 0, -1)]


def review_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Yorum kümesinin özeti: sayılar + ortalama puan + yanıtsız sayısı."""
    scored = [as_int(row.get("rating")) for row in rows if as_int(row.get("rating"))]
    return {
        "total": len(rows),
        "pending": len([row for row in rows if row.get("status") == "pending"]),
        "approved": len([row for row in rows if row.get("status") == "approved"]),
        "rejected": len([row for row in rows if row.get("status") == "disapproved"]),
        "spam": len([row for row in rows if row.get("spam")]),
        "unanswered": len([row for row in rows if not row.get("hasReply")]),
        "average": round(sum(scored) / len(scored), 2) if scored else None,
        "breakdown": rating_breakdown(rows),
    }


# ====================================================================== izin

def consent_view(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Müşterinin ŞU ANKİ izinleri (TUZAK 10).

    Değer bulunamayan izin "hayır" değil BİLİNMİYOR olarak döner: KVKK
    kayıtlarında "onay yok" ile "onay bilgisi tutulmuyor" aynı şey değildir.
    """
    out: list[dict[str, Any]] = []
    for kind, label in CONSENT_LABELS.items():
        value = flag(raw, kind)
        out.append({
            "key": kind,
            "label": label,
            "value": value,
            "state": "unknown" if value is None else ("on" if value else "off"),
            "source": "Müşteri kaydı",
        })
    return out


def gdpr_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_int(raw.get("id")),
        "customerId": as_int(pick(raw, "customer_id")),
        "kind": text(pick(raw, "type", "request_type")) or "unknown",
        "status": text(pick(raw, "status")) or "pending",
        "message": text(pick(raw, "message", "reason")),
        "createdAt": text(pick(raw, "created_at"))[:19],
    }
