"""Ürün verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bagisto'nun ürün kaydı EAV'dir: aynı bilgi kayıttan kayda
farklı yerde durur, para ondalık gelir, stok ürünün üzerinde değildir. Bu
çeviriler servise gömülseydi tek satırı bile test edilemezdi; burada hepsi
girdi→çıktı fonksiyonu ve testler ağ olmadan çalışır.

ON TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Kısmi PUT alanları NULL'lar     → `write_body` OKU-DEĞİŞTİR-YAZ kurar.
 2. channel + locale her istekte    → `write_body` ikisini her zaman koyar.
 3. attribute_family_id salt okunur → `write_body` onu ASLA göndermez.
 4. Fiyat tek alan değil            → `price_view` üçünü birden çıkarır.
 5. Stok product.quantity'de değil  → `stock_of` envanter kaynaklarını toplar.
 6. url_key benzersiz               → `url_key_verdict` yazmadan önce bakar.
 7. sku değişikliği URL kırar       → ayrı yol, `write_body` sku'yu değiştirmez.
 8. status indeksleme ister         → `INDEX_NOTICE` metni ekranda gösterilir.
 9. Siparişli ürün silinmez         → silme yok; yalnız `status` 0/1.
10. Configurable fiyatı varyantta   → `write_body` o tipte fiyat göndermez.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: `status` yazıldıktan sonra vitrinin güncellenmesi indeksleme ister.
#: Ekran bunu SÖYLER; sessiz kalıp "neden değişmedi" sorusunu doğurmaz.
INDEX_NOTICE = "Vitrine yansıması birkaç dakika sürebilir (arama dizini yenilenir)."

#: Fiyatı kendi üzerinde taşımayan ürün tipleri. Configurable'ın fiyatı
#: varyantlarındadır; bundle/grouped bileşenlerinden hesaplanır.
PRICELESS_TYPES = ("configurable", "bundle", "grouped")

#: `write_body` içinde yazılabilir sayılan alanlar. Listede OLMAYAN hiçbir
#: alan gövdeye konmaz: tanımadığımız özniteliği geri göndermek, başka bir
#: ekranın aynı anda yazdığı değeri ezmektir.
EDITABLE = (
    "name", "url_key", "short_description", "description",
    "meta_title", "meta_keywords", "meta_description",
    "price", "cost", "special_price", "special_price_from", "special_price_to",
    "status", "visible_individually", "guest_checkout", "new", "featured",
    "weight", "tax_category_id",
)

#: Kuruş taşınan alanlar. İçeride int (kuruş), telde ondalık metin.
MONEY_FIELDS = ("price", "cost", "special_price")

#: Ekranın camelCase alan adları → Bagisto öznitelik adları.
#:
#: NEDEN EŞLEME: bizim JSON'umuz camelCase (proje kuralı), Bagisto özniteliği
#: snake_case ve isimleri her zaman örtüşmüyor (`specialFrom` ↔
#: `special_price_from`). Ekranın Bagisto adlarını bilmesi, EAV ayrıntısını
#: arayüze sızdırmak olurdu.
FIELD_ALIASES = {
    "name": "name",
    "urlKey": "url_key",
    "shortDescription": "short_description",
    "description": "description",
    "metaTitle": "meta_title",
    "metaKeywords": "meta_keywords",
    "metaDescription": "meta_description",
    "price": "price",
    "cost": "cost",
    "specialPrice": "special_price",
    "specialFrom": "special_price_from",
    "specialTo": "special_price_to",
    "status": "status",
    "visibleIndividually": "visible_individually",
    "weight": "weight",
    "taxCategoryId": "tax_category_id",
}


def normalize_patch(patch: Any) -> dict[str, Any]:
    """Ekranın gönderdiği yamayı Bagisto adlarına çevirir.

    Tanınmayan anahtar SESSİZCE DÜŞÜRÜLÜR: bilmediğimiz bir alanı mağazaya
    iletmek, yazım hatasını veri kaybına çevirir.
    """
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        target = FIELD_ALIASES.get(key, key if key in EDITABLE else "")
        if target:
            out[target] = value
    return out


#: Ürün tipi etiketleri — ekranda İngilizce tip adı görünmez.
TYPE_LABELS = {
    "simple": "Basit",
    "configurable": "Varyantlı",
    "virtual": "Sanal",
    "downloadable": "İndirilebilir",
    "grouped": "Gruplu",
    "bundle": "Set",
    "booking": "Randevulu",
}

STOCK_IN = "in"
STOCK_LOW = "low"
STOCK_OUT = "out"
STOCK_OFF = "off"

STOCK_LABELS = {
    STOCK_IN: "Stokta",
    STOCK_LOW: "Kritik",
    STOCK_OUT: "Tükendi",
    STOCK_OFF: "Takip kapalı",
}

#: Sağlık bulgusu türleri → ürün listesini besleyen sunucu tarafı çipler.
#: Bu üçü ÜRÜN LİSTESİ ucuyla süzülemez (stok envanterde, görsel ilişkide),
#: bu yüzden ayrı uçtan (bbd/catalog/issues) sayfalanarak gelir.
ISSUE_KINDS = ("out_of_stock", "low_stock", "no_image", "seo_missing")

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: indirim penceresi mağazanın
    takvim gününe göre açılıp kapanır ve gece yarısından sonraki üç saatte
    UTC bir gün geride kalır — indirim ekranda bitmiş, vitrinde sürüyor
    görünürdü.
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


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı
    değerlerde 123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar —
    1.419 üründe binde bir görünen, bulunması imkânsız bir sapmadır.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return None
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
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def from_kurus(kurus: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def slugify(value: str) -> str:
    """`Matematik Soru Bankası 9. Sınıf` → `matematik-soru-bankasi-9-sinif`.

    Türkçe harfler ÖNCE eşlenir: `unicodedata` ile normalleştirmek `ı`yı
    boşa çıkarır ve `ısı` → `s` gibi anlamsız anahtarlar üretir.
    """
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-zA-Z0-9]+", "-", folded).strip("-").lower()
    return re.sub(r"-{2,}", "-", folded)


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


# ============================================================ alan okuyucu

def attribute(raw: Any, *names: str) -> Any:
    """Ürün kaydında bir özniteliği nerede olursa olsun bulur.

    Bagisto admin API'si aynı ürünü iki biçimde verebiliyor: düz sözlük
    (`{"name": …}`) ya da EAV zarfı (`{"values": {"common": {"name": …}}}`).
    Hangisinin geleceği ürün tipine ve sürüme göre değişiyor; çağıran tarafın
    ikisini de bilmesi gerekseydi her okuma iki satır olurdu.
    """
    if not isinstance(raw, dict):
        return None
    values = raw.get("values") if isinstance(raw.get("values"), dict) else {}
    sources: list[dict[str, Any]] = [raw]
    for key in ("common", "channel_locale_specific", "locale_specific", "channel_specific"):
        nested = values.get(key)
        if isinstance(nested, dict):
            sources.append(nested)
    if values:
        sources.append(values)
    if isinstance(raw.get("additional"), dict):
        sources.append(raw["additional"])

    for name in names:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    # Değer VAR ama boş olabilir; "yok" ile "boş" ayrımı korunur.
    for name in names:
        for source in sources:
            if name in source:
                return source[name]
    return None


def image_url(raw: dict[str, Any]) -> str:
    """Kapak görseli. Bagisto `images[0]` sırasını kapak olarak kullanır."""
    images = raw.get("images")
    if isinstance(images, list) and images:
        first = images[0]
        if isinstance(first, dict):
            return text(first.get("url") or first.get("original_image_url") or first.get("path"))
        return text(first)
    return text(raw.get("base_image_url") or raw.get("image_url"))


def image_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Görseller — İLK eleman kapaktır (`reorder_product_images` sözleşmesi)."""
    images = raw.get("images")
    if not isinstance(images, list):
        return []
    out: list[dict[str, Any]] = []
    for index, item in enumerate(images):
        if not isinstance(item, dict):
            continue
        out.append({
            "id": as_int(item.get("id")),
            "url": text(item.get("url") or item.get("original_image_url") or item.get("path")),
            "cover": index == 0,
        })
    return out


def category_ids(raw: dict[str, Any]) -> list[int]:
    items = raw.get("categories")
    if not isinstance(items, list):
        return []
    out = []
    for item in items:
        found = as_int(item.get("id")) if isinstance(item, dict) else as_int(item)
        if found:
            out.append(found)
    return out


def category_names(raw: dict[str, Any]) -> list[str]:
    items = raw.get("categories")
    if not isinstance(items, list):
        return []
    return [text(item.get("name")) for item in items
            if isinstance(item, dict) and text(item.get("name"))]


# =================================================================== stok

def stock_of(inventories: Any) -> int:
    """Envanter kaynaklarındaki toplam adet (TUZAK 5).

    `product.quantity` alanı Bagisto'da vitrin için tutulan TÜRETİLMİŞ bir
    değerdir; kaynak kayıtları değiştiğinde geç güncellenir. Doğru sayı
    `inventory_sources` satırlarının toplamıdır.
    """
    if not isinstance(inventories, list):
        return 0
    total = 0
    for row in inventories:
        if not isinstance(row, dict):
            continue
        total += as_int(row.get("qty", row.get("quantity", row.get("in_stock_qty"))))
    return total


def inventory_rows(inventories: Any, sources: Any = None) -> list[dict[str, Any]]:
    """Kaynak bazlı stok satırları — düzenleme ekranı bunları gösterir."""
    names: dict[int, str] = {}
    for source in sources or []:
        if isinstance(source, dict):
            names[as_int(source.get("id"))] = text(source.get("name") or source.get("code"))

    rows: list[dict[str, Any]] = []
    for row in inventories if isinstance(inventories, list) else []:
        if not isinstance(row, dict):
            continue
        nested = row.get("inventory_source")
        source_id = as_int(row.get("inventory_source_id") or row.get("source_id")
                           or (nested.get("id") if isinstance(nested, dict) else 0))
        rows.append({
            "sourceId": source_id,
            "sourceName": names.get(source_id) or text(row.get("source_name")) or f"#{source_id}",
            "quantity": as_int(row.get("qty", row.get("quantity"))),
        })
    return rows


def stock_state(quantity: int, threshold: int, *, manage_stock: bool = True) -> str:
    if not manage_stock:
        return STOCK_OFF
    if quantity <= 0:
        return STOCK_OUT
    if threshold > 0 and quantity <= threshold:
        return STOCK_LOW
    return STOCK_IN


# ================================================================== fiyat

def special_state(special: int | None, start: str, end: str, today: str) -> str:
    """İndirimli fiyatın tarih penceresine göre durumu (TUZAK 4).

    `none` · `active` · `scheduled` (henüz başlamadı) · `expired` (bitti).
    Pencere yoksa ve indirim varsa `active` sayılır — Bagisto da öyle davranır.
    """
    if special is None or special <= 0:
        return "none"
    if start and today < start:
        return "scheduled"
    if end and today > end:
        return "expired"
    return "active"


def effective_price(price: int | None, special: int | None, state: str) -> int | None:
    """Müşterinin bugün ödeyeceği tutar."""
    if state == "active" and special is not None:
        return special
    return price


def price_view(raw: dict[str, Any], group_prices: Any = None, *,
               today: str = "") -> dict[str, Any]:
    """Fiyatın ÜÇ katmanı birden (TUZAK 4): liste · indirim · grup."""
    day = today or today_iso()
    price = to_kurus(attribute(raw, "price"))
    cost = to_kurus(attribute(raw, "cost"))
    special = to_kurus(attribute(raw, "special_price"))
    start = text(attribute(raw, "special_price_from"))[:10]
    end = text(attribute(raw, "special_price_to"))[:10]
    state = special_state(special, start, end, day)

    groups: list[dict[str, Any]] = []
    for row in group_prices if isinstance(group_prices, list) else []:
        if not isinstance(row, dict):
            continue
        value = to_kurus(row.get("value"))
        nested = row.get("customer_group")
        group_name = text(nested.get("name")) if isinstance(nested, dict) \
            else text(row.get("customer_group_name"))
        groups.append({
            "id": as_int(row.get("id")),
            "groupId": as_int(row.get("customer_group_id")),
            "groupName": group_name or "Grup",
            "qty": as_int(row.get("qty"), 1),
            "kind": text(row.get("value_type")) or "fixed",
            "value": value,
            "raw": text(row.get("value")),
        })

    return {
        "price": price,
        "cost": cost,
        "specialPrice": special,
        "specialFrom": start,
        "specialTo": end,
        "specialState": state,
        "effective": effective_price(price, special, state),
        "margin": margin_percent(price, cost),
        "groupPrices": groups,
    }


def margin_percent(price: int | None, cost: int | None) -> float | None:
    """Satış fiyatı üzerinden kâr marjı. Maliyet yoksa `None` — sıfır DEĞİL."""
    if not price or cost is None:
        return None
    return round((price - cost) * 100 / price, 1)


# ============================================================== liste satırı

def product_row(raw: dict[str, Any], *, threshold: int = 0, today: str = "",
                inventories: Any = None) -> dict[str, Any]:
    """Ham ürün kaydı → tablonun beklediği satır. Para her yerde kuruş."""
    day = today or today_iso()
    kind = text(attribute(raw, "type")) or "simple"
    price = to_kurus(attribute(raw, "price"))
    special = to_kurus(attribute(raw, "special_price"))
    start = text(attribute(raw, "special_price_from"))[:10]
    end = text(attribute(raw, "special_price_to"))[:10]
    state = special_state(special, start, end, day)

    if inventories is not None:
        quantity = stock_of(inventories)
        exact = True
    else:
        # Liste ucu envanter satırlarını taşımaz; vitrin alanı yaklaşıktır ve
        # ekranda "~" ile gösterilir. Kesin sayı ürün açılınca gelir (TUZAK 5).
        quantity = as_int(attribute(raw, "quantity", "total_quantity"))
        exact = False

    manage = attribute(raw, "manage_stock")
    manages = True if manage is None else bool(as_int(manage, 1))
    names = category_names(raw)
    family = raw.get("attribute_family")
    family_name = text(family.get("name")) if isinstance(family, dict) else \
        text(attribute(raw, "attribute_family_name"))

    return {
        "id": as_int(raw.get("id")),
        "sku": text(attribute(raw, "sku")),
        "name": text(attribute(raw, "name")) or "(adsız)",
        "urlKey": text(attribute(raw, "url_key")),
        "type": kind,
        "typeLabel": TYPE_LABELS.get(kind, kind),
        "status": bool(as_int(attribute(raw, "status"), 0)),
        "price": price,
        "specialPrice": special,
        "specialState": state,
        "effectivePrice": effective_price(price, special, state),
        "stock": quantity,
        "stockExact": exact,
        "stockState": stock_state(quantity, threshold, manage_stock=manages),
        "categories": ", ".join(names),
        "categoryIds": category_ids(raw),
        "imageUrl": image_url(raw),
        "familyId": as_int(attribute(raw, "attribute_family_id")),
        "familyName": family_name,
        "updatedAt": text(raw.get("updated_at"))[:19],
        "createdAt": text(raw.get("created_at"))[:19],
    }


def issue_row(raw: dict[str, Any], *, threshold: int = 0) -> dict[str, Any]:
    """`bbd/catalog/issues` satırını ürün satırı şekline getirir.

    Bulgu ucu ürünün tamamını değil kimliğini ve sorunu döner; eksik alanlar
    boş bırakılır ve ekran `—` gösterir. Uydurma değer üretilmez.
    """
    product = raw.get("product") if isinstance(raw.get("product"), dict) else raw
    row = product_row(product, threshold=threshold)
    if not row["id"]:
        row["id"] = as_int(raw.get("product_id"))
    row["issue"] = text(raw.get("kind") or raw.get("issue"))
    row["issueDetail"] = text(raw.get("detail") or raw.get("message"))
    return row


# ======================================================== yazma gövdesi

def write_body(current: dict[str, Any], patch: dict[str, Any], *, channel: str,
               locale: str, sku: str = "") -> dict[str, Any]:
    """OKU-DEĞİŞTİR-YAZ gövdesi (TUZAK 1, 2, 3, 7, 10).

    Bagisto'nun ürün güncelleme işlemcisi gövdedeki alanları modele doğrudan
    veriyor. Yalnız değişen alanı göndermek, aynı istekte yer almayan ama
    işlemcinin varsaydığı alanları (`url_key`, `status`) NULL'a düşürebiliyor;
    bu yüzden gövde mevcut değerlerden kurulur ve üzerine değişiklik yazılır.

    `sku` gövdeye HER ZAMAN konur ama `patch` içinden ASLA alınmaz: SKU
    değişikliği `product_flat`'ı yeniden yazar ve eski vitrin bağlantılarını
    kırar — ayrı onay isteyen ayrı bir yoldur (TUZAK 7).

    `attribute_family_id` hiçbir koşulda gönderilmez (TUZAK 3): aile
    değişikliği ürünün öznitelik kümesini değiştirir, düzenleme ekranının işi
    değildir.
    """
    kind = text(attribute(current, "type")) or "simple"
    body: dict[str, Any] = {}

    for key in EDITABLE:
        value = attribute(current, key)
        if value is None:
            continue
        body[key] = from_kurus(to_kurus(value) or 0) if key in MONEY_FIELDS else value

    for key, value in (patch or {}).items():
        if key not in EDITABLE:
            continue
        if key in MONEY_FIELDS:
            body[key] = "" if value in (None, "") else from_kurus(value)
        elif key == "status":
            body[key] = 1 if value else 0
        else:
            body[key] = value

    if kind in PRICELESS_TYPES:
        # Configurable ürünün fiyatı varyantlarındadır; buradan yazmak
        # vitrinde görünmeyen ama raporlara giren hayalet fiyat üretir.
        for key in ("price", "special_price", "special_price_from", "special_price_to"):
            body.pop(key, None)

    # Boş tarih penceresi NULL olarak gitmeli; "" bazı sürümlerde 1970 oluyor.
    for key in ("special_price_from", "special_price_to"):
        if key in body and text(body[key]) == "":
            body[key] = None

    body["channel"] = channel
    body["locale"] = locale
    if sku:
        body["sku"] = sku
    body.pop("attribute_family_id", None)
    return body


def url_key_verdict(requested: str, items: Any, *, product_id: int = 0) -> dict[str, Any]:
    """url_key çakışması var mı — YAZMADAN ÖNCE (TUZAK 6).

    Süzgeci sunucunun gerçekten uyguladığını doğrular: Laravel tanımadığı
    sorgu parametresini SESSİZCE yok sayar ve tüm kataloğun ilk sayfasını
    döndürür. O yanıtı "çakışma var" saymak doğru yazan kullanıcıyı durdurur,
    "çakışma yok" saymak ise 422'yi kaydet düğmesine bırakır. Üçüncü bir
    cevap gerekiyor: `unknown`.
    """
    wanted = text(requested).lower()
    rows = [row for row in (items or []) if isinstance(row, dict)]
    if not wanted:
        return {"state": "empty", "takenBy": 0, "message": "URL anahtarı boş olamaz."}

    matches = [row for row in rows if text(attribute(row, "url_key")).lower() == wanted]
    others = [row for row in matches if as_int(row.get("id")) != int(product_id or 0)]

    if others:
        owner = others[0]
        return {"state": "taken", "takenBy": as_int(owner.get("id")),
                "message": f"Bu URL anahtarı `{text(attribute(owner, 'sku'))}` ürününde kullanılıyor."}
    if rows and not matches:
        return {"state": "unknown", "takenBy": 0,
                "message": "Mağaza url_key süzgecini uygulamadı; benzersizlik doğrulanamadı. "
                           "Kaydetme sırasında mağaza reddedebilir."}
    return {"state": "free", "takenBy": 0, "message": "URL anahtarı kullanılabilir."}


def filter_honored(items: Any, key: str, expected: Any) -> bool | None:
    """Sunucu gönderdiğimiz süzgeci gerçekten uyguladı mı?

    `None` = anlaşılamadı (satırlar o alanı taşımıyor ya da boş taşıyor).
    Bilinmeyeni "evet" saymak, ekranda süzülmemiş listeyi süzülmüş gibi
    göstermek olurdu; "hayır" saymak da alanı hiç dönmeyen bir uçta boş yere
    uyarı çıkarırdı.
    """
    rows = [row for row in (items or []) if isinstance(row, dict)]
    if not rows:
        return None
    seen = False
    for row in rows:
        value = row.get(key, attribute(row, key))
        if value is None or value == "" or value == []:
            continue
        seen = True
        if isinstance(value, list):
            if expected not in value and str(expected) not in [str(v) for v in value]:
                return False
        elif str(value) != str(expected):
            return False
    return True if seen else None


# ================================================== toplu işlem önizlemesi

#: Yuvarlama biçimleri. `penny99` perakendede en çok istenen: 129,00 → 128,99.
ROUNDINGS = ("none", "whole", "penny99", "half")


def round_price(kurus: int, rounding: str) -> int:
    if kurus <= 0:
        return 0
    if rounding == "whole":
        return ((kurus + 50) // 100) * 100
    if rounding == "half":
        return ((kurus + 25) // 50) * 50
    if rounding == "penny99":
        whole = ((kurus + 50) // 100) * 100
        return max(99, whole - 1)
    return kurus


def apply_price_rule(price: int | None, *, mode: str, amount: int, rounding: str = "none") -> int:
    """Tek ürünün yeni fiyatı. `amount`: yüzdede tam sayı, diğerlerinde kuruş."""
    base = int(price or 0)
    if mode == "percent":
        target = base + (base * int(amount)) // 100
    elif mode == "amount":
        target = base + int(amount)
    elif mode == "set":
        target = int(amount)
    else:
        raise ValueError(f"Bilinmeyen fiyat kipi: {mode}")
    return round_price(max(0, target), rounding)


def bulk_price_rows(rows: list[dict[str, Any]], *, mode: str, amount: int,
                    rounding: str = "none") -> list[dict[str, Any]]:
    """Fark tablosu — ÖNİZLEME ZORUNLU, bu liste gösterilmeden uygulanmaz."""
    out: list[dict[str, Any]] = []
    for row in rows:
        if row.get("type") in PRICELESS_TYPES:
            out.append({"id": row["id"], "sku": row["sku"], "name": row["name"],
                        "before": row.get("price"), "after": row.get("price"), "delta": 0,
                        "skipped": True,
                        "note": "Fiyatı varyantlarında; toplu güncellemeden çıkarıldı."})
            continue
        before = int(row.get("price") or 0)
        after = apply_price_rule(before, mode=mode, amount=amount, rounding=rounding)
        out.append({"id": row["id"], "sku": row["sku"], "name": row["name"],
                    "before": before, "after": after, "delta": after - before,
                    "skipped": False, "note": ""})
    return out


def bulk_stock_rows(rows: list[dict[str, Any]], *, mode: str,
                    amount: int) -> list[dict[str, Any]]:
    """Stok fark tablosu. `set` mutlak, `add` fark ekler (negatif de olur)."""
    out: list[dict[str, Any]] = []
    for row in rows:
        before = int(row.get("stock") or 0)
        after = max(0, int(amount)) if mode == "set" else max(0, before + int(amount))
        out.append({"id": row["id"], "sku": row["sku"], "name": row["name"],
                    "before": before, "after": after, "delta": after - before,
                    "skipped": not row.get("stockExact", True),
                    "note": "" if row.get("stockExact", True)
                            else "Stok yaklaşık okundu; uygulamadan önce ürün açılmalı."})
    return out


def bulk_category_rows(rows: list[dict[str, Any]], *, action: str, category_id: int,
                       category_name: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        ids = list(row.get("categoryIds") or [])
        has = int(category_id) in ids
        if action == "add":
            after = ids if has else [*ids, int(category_id)]
        elif action == "remove":
            after = [item for item in ids if item != int(category_id)]
        else:
            raise ValueError(f"Bilinmeyen kategori eylemi: {action}")
        out.append({"id": row["id"], "sku": row["sku"], "name": row["name"],
                    "before": len(ids), "after": len(after), "delta": len(after) - len(ids),
                    "categoryIds": after, "skipped": after == ids,
                    "note": "" if after != ids else f"`{category_name}` zaten böyle."})
    return out


def diff_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Fark tablosunun tek satırlık özeti — onay kutusunda bu yazar."""
    changed = [row for row in rows if not row.get("skipped") and row.get("delta")]
    return {
        "total": len(rows),
        "changed": len(changed),
        "skipped": len([row for row in rows if row.get("skipped")]),
        "up": len([row for row in changed if row["delta"] > 0]),
        "down": len([row for row in changed if row["delta"] < 0]),
        "netDelta": sum(row["delta"] for row in changed),
    }


# =================================================================== ağaç

def category_options(tree: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """41 kategorilik ağacı düz seçenek listesine indirir (girintili ad ile)."""
    out: list[dict[str, Any]] = []
    nodes = tree if isinstance(tree, list) else (tree or {}).get("children") or []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        name = text(node.get("name")) or f"#{as_int(node.get('id'))}"
        out.append({
            "id": as_int(node.get("id")),
            "name": name,
            "depth": depth,
            "label": f"{'— ' * depth}{name}",
        })
        children = node.get("children") or node.get("child_categories")
        if children:
            out.extend(category_options(children, depth=depth + 1))
    return out
