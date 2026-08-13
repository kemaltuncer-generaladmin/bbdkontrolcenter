"""Katalog sağlığı — KURAL TABANLI denetim. AI YOK, ağ YOK, durum YOK.

NEDEN AYRI DOSYA. Bu ekranın yarısı yapay zekâ değildir: görselsiz ürün,
kategorisiz ürün, boş kategori, kırık SEO, çift `url_key` ve fiyat
anormalliği bir modelin değil bir kuralın işidir. Kural, girdi→çıktı
fonksiyonu olarak burada durur; testler ağ olmadan çalışır ve "neden bulgu
çıktı" sorusunun cevabı tek satırda okunur.

BU DOSYA `store_products/backend/catalog.py` İLE BENZER YARDIMCILAR TAŞIR
(EAV okuyucu, kuruş çevirici). Kopya bilinçlidir: modül modülü import etmez
(K3) ve iki ekranın eşik/etiket tanımları zamanla ayrışacaktır. Ortaklaşması
gerekirse yeri `km_sdk`'dir, komşu modül değil.

YEDİ TUZAK — hepsinin karşılığı burada bir fonksiyon, testlerde bir addır:

 0. YAZIM BİÇİMİ. Mağaza alan adlarını **camelCase** veriyor (`urlKey`,
    `metaTitle`, `specialPrice`, `imagesCount`, `categoryId`), Bagisto'nun
    veritabanı sütunları ve belgeleri ise snake_case. `url_key` diye bakan kod
    hiçbir şey bulmaz ve İSTİSNA DA ATMAZ: alan "boş" görünür ve kural her
    üründe tetiklenir. 2026-08-13'te canlıda ölçüldü — snake_case okuyan sürüm
    1.421 ürünün TAMAMINA "görseli yok" + "kategorisiz" + "URL anahtarı boş"
    diyordu; gerçek bulgu sayısı sıfırdı. `spellings()` iki yazımı da çözer.

 1. EAV: aynı alan kayıttan kayda düz sözlükte ya da `values.*` zarfında
    durur → `attribute()` ikisine de bakar.
 2. "Yok" ile "boş" aynı şey değildir. Katalogda barkod ÖZNİTELİĞİ hiç
    tanımlı değilse "barkodu eksik" bulgusu 1.419 satırlık anlamsız bir
    kırmızı duvar üretir → `barcode_defined()` kuralı kendiliğinden kapatır.
 3. Varyantlı/set/gruplu ürünün fiyatı kendisinde değildir → fiyat kuralları
    o tipleri atlar, yoksa tüm varyantlı ürünler "fiyatı 0" görünürdü.
 4. Ortalama değil MEDYAN. Tek bir 9.000 ₺'lik set, kategorinin ortalamasını
    yukarı çekip gerçek anormallikleri gizler.
 5. Pasif ürün vitrinde değildir; onun SEO'su bulgu değildir → varsayılan
    olarak yalnız aktif ürün denetlenir.
 6. Aynı ürün birden çok kurala takılabilir. Bulgu kimliği `kural:hedef`
    biçimindedir; susturma listesi ve seçim bu kimliğe göre çalışır, ürün
    kimliğine göre değil.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Barkod hangi öznitelik adıyla tutuluyorsa. Bagisto çekirdeğinde barkod
#: alanı YOKTUR; kurulumlar bunu ek öznitelik olarak açar ve adı değişir.
BARCODE_NAMES = ("barcode", "ean", "gtin", "isbn", "upc", "mpn")

#: Fiyatı kendi üzerinde taşımayan tipler (TUZAK 3).
PRICELESS_TYPES = ("configurable", "bundle", "grouped")

MISSING_IMAGE = "no_image"
MISSING_DESCRIPTION = "thin_description"
MISSING_BARCODE = "no_barcode"
MISSING_CATEGORY = "no_category"
PRICE_ANOMALY = "price_anomaly"
EMPTY_CATEGORY = "empty_category"
SEO_BROKEN = "seo_broken"
DUPLICATE_URL_KEY = "duplicate_url_key"

#: Bulgu türü → (ekranda görünen ad, ağırlık). Ağırlık sıralama içindir:
#: yüksek olan üstte durur, çünkü listeyi yukarıdan aşağı okuyan kişi önce
#: satışı durduran şeyi görmeli.
KIND_LABELS: dict[str, str] = {
    MISSING_IMAGE: "Görsel yok",
    MISSING_DESCRIPTION: "Açıklama yetersiz",
    MISSING_BARCODE: "Barkod yok",
    MISSING_CATEGORY: "Kategorisiz",
    PRICE_ANOMALY: "Fiyat anormal",
    EMPTY_CATEGORY: "Boş kategori",
    SEO_BROKEN: "SEO eksik/kırık",
    DUPLICATE_URL_KEY: "Çift URL anahtarı",
}

SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}

#: Önem anahtarı → ekranda/raporda okunacak ad. Anahtarın kendisi (`high`)
#: İngilizce ve ASCII'dir çünkü kod tanımlayıcısıdır; kullanıcıya giden her
#: yerde Türkçesi yazar. CSV de kullanıcıya gider: `disabledRules` çevrilirken
#: dışa aktarmanın "high/medium/low" yazması gözden kaçmıştı.
SEVERITY_LABELS = {"high": "Ağır", "medium": "Orta", "low": "Hafif"}

#: Bulgunun hangi araçla düzeltilebileceği. Ekran "Bu bulguyu AI ile gider"
#: düğmesini bu eşlemeden çizer; eşlemesi olmayan bulgu elle düzeltilir.
KIND_TOOLS: dict[str, str] = {
    MISSING_DESCRIPTION: "product_description",
    SEO_BROKEN: "seo_meta",
    MISSING_IMAGE: "",
    MISSING_BARCODE: "",
    MISSING_CATEGORY: "",
    PRICE_ANOMALY: "",
    EMPTY_CATEGORY: "",
    DUPLICATE_URL_KEY: "",
}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü. `utcnow()` gece yarısından sonraki üç saatte
    bir gün geride kalır ve aylık bütçe bir gün erken/geç döner."""
    return datetime.now(UTC).astimezone().date().isoformat()


def text(value: Any) -> str:
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

    `float` KULLANILMAZ: `float("1234.35") * 100` bazı değerlerde 123434.999
    verir ve `int()` bir kuruş aşağı yuvarlar. Fiyat anormalliği ararken bir
    kuruşluk sapma bulguyu değiştirmez ama rapordaki toplamı bozar.
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


#: `spellings()` belleği. Tarama 1.421 ürün × ~15 alan = ~21.000 çağrı eder;
#: her seferinde düzenli ifade çalıştırmak taramayı gereksiz yere uzatır.
_SPELLINGS: dict[str, tuple[str, ...]] = {}

_CAMEL_BREAK = re.compile(r"(?<!^)(?=[A-Z])")


def spellings(name: str) -> tuple[str, ...]:
    """Bir alan adının aranacak TÜM yazımları (TUZAK 0).

    `url_key` → `('url_key', 'urlKey')` · `urlKey` → `('urlKey', 'url_key')`.
    Tek kelimelik adlar (`sku`, `price`) tek yazımla döner.

    Verilen yazım HER ZAMAN önce denenir: çağıran hangi adı yazdıysa kayıtta o
    varsa o kazanır; testlerin snake_case sabitleri de böyle çalışmaya devam
    eder.
    """
    cached = _SPELLINGS.get(name)
    if cached is not None:
        return cached
    forms = [name]
    if "_" in name:
        head, *rest = name.split("_")
        camel = head + "".join(part[:1].upper() + part[1:] for part in rest if part)
        if camel != name:
            forms.append(camel)
    else:
        snake = _CAMEL_BREAK.sub("_", name).lower()
        if snake != name:
            forms.append(snake)
    out = tuple(forms)
    _SPELLINGS[name] = out
    return out


def _expand(names: tuple[str, ...]) -> list[str]:
    """Ad listesini yazım varyantlarıyla genişletir; sıra korunur."""
    out: list[str] = []
    for name in names:
        for form in spellings(name):
            if form not in out:
                out.append(form)
    return out


def fold(value: Any) -> str:
    """Türkçe duyarsız karşılaştırma anahtarı: `Öğrenci` ile `ogrenci` eşleşir."""
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return folded.casefold()


def strip_html(value: Any) -> str:
    """Açıklama uzunluğunu ETİKETSİZ ölçer.

    Bagisto açıklaması zengin metindir. `<p>&nbsp;</p>` on beş karakterdir ve
    ham uzunluğa bakan bir kural onu "dolu açıklama" sayar — kataloğun
    yarısında tam olarak bu vardır.
    """
    raw = text(value)
    out: list[str] = []
    inside = False
    for char in raw:
        if char == "<":
            inside = True
        elif char == ">":
            inside = False
        elif not inside:
            out.append(char)
    cleaned = "".join(out).replace("&nbsp;", " ").replace("&amp;", "&")
    return " ".join(cleaned.split())


def _sources(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Bir özniteliğin bulunabileceği tüm sözlükler (TUZAK 1)."""
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
    return sources


def attribute(raw: Any, *names: str) -> Any:
    """Ürün kaydında bir özniteliği nerede ve HANGİ YAZIMDA olursa olsun bulur.

    TUZAK 0 + TUZAK 1: `attribute(raw, "url_key")` canlıdaki `urlKey` alanını
    da bulur. Dolu değer boş değere yeğlenir — kayıtta hem `metaTitle: "Ad"`
    hem `meta_title: ""` varsa dolu olan kazanır.
    """
    if not isinstance(raw, dict):
        return None
    sources = _sources(raw)
    wanted = _expand(names)

    for name in wanted:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    for name in wanted:
        for source in sources:
            if name in source:
                return source[name]
    return None


def has_key(raw: Any, *names: str) -> bool:
    """Öznitelik kayıtta TANIMLI mı — değeri boş olsa bile (TUZAK 2).

    Yazım biçimine takılmaz: `has_key(raw, "meta_title")` canlıdaki
    `metaTitle` alanını da görür. Görmeseydi fark tablosunun "Önce" sütunu
    her satırda "—" derdi (TUZAK 0).
    """
    if not isinstance(raw, dict):
        return False
    sources = _sources(raw)
    return any(name in source for name in _expand(names) for source in sources)


def image_count(raw: dict[str, Any]) -> int:
    """Ürünün görsel sayısı.

    TUZAK 0 — LİSTE UCU GÖRSELLERİ VERMEZ. `/api/admin/catalog/products`
    yanıtında `images` alanı **null**'dır; sayı `imagesCount`, kapak ise
    `baseImageUrl` alanındadır (canlıda doğrulandı, 2026-08-13). Yalnız
    `images` listesine bakan sürüm kataloğun tamamını "görseli yok" sayıyordu.
    Tam liste ancak ürün DETAYINDA gelir ve tarama detay okumaz.
    """
    images = attribute(raw, "images")
    if isinstance(images, list):
        return len(images)
    counted = attribute(raw, "images_count")
    if counted is not None and text(counted) != "":
        return max(0, as_int(counted, 0))
    return 1 if text(attribute(raw, "base_image_url", "image_url")) else 0


def category_ids(raw: dict[str, Any]) -> list[int]:
    """Ürünün kategori numaraları.

    TUZAK 0 — LİSTE UCU TEK KATEGORİ VERİR. Yanıtta `categories` **null**'dır;
    yalnız `categoryId` (BİRİNCİL kategori) gelir. Canlıda 1418 numaralı ürün
    dört kategoride ama listede yalnız `categoryId: 2` görünüyor. Bu yüzden
    `category_partial()` çağıranı uyarır ve boş kategori denetimi kapanır.
    """
    items = attribute(raw, "categories")
    if isinstance(items, list):
        out = []
        for item in items:
            found = as_int(item.get("id")) if isinstance(item, dict) else as_int(item)
            if found:
                out.append(found)
        return out
    single = as_int(attribute(raw, "category_id"))
    return [single] if single else []


def category_partial(raw: dict[str, Any]) -> bool:
    """Kategori bilgisi EKSİK mi — yalnız birincil kategori mi geldi?

    `categories` listesi varsa bilgi tamdır. Yoksa elimizdeki tek şey
    `categoryId`'dir ve ürünün öteki kategorileri görünmez.
    """
    return not isinstance(attribute(raw, "categories"), list)


# ============================================================ ürün özeti

def product_facts(raw: dict[str, Any]) -> dict[str, Any]:
    """Ham ürün kaydı → denetimin baktığı düz gerçekler. Para kuruş."""
    kind = text(attribute(raw, "type")) or "simple"
    return {
        "id": as_int(raw.get("id")),
        "sku": text(attribute(raw, "sku")),
        "name": text(attribute(raw, "name")) or "(adsız)",
        "type": kind,
        "priceless": kind in PRICELESS_TYPES,
        "status": bool(as_int(attribute(raw, "status"), 0)),
        "price": to_kurus(attribute(raw, "price")),
        "cost": to_kurus(attribute(raw, "cost")),
        "specialPrice": to_kurus(attribute(raw, "special_price")),
        "urlKey": text(attribute(raw, "url_key")),
        "metaTitle": text(attribute(raw, "meta_title")),
        "metaDescription": text(attribute(raw, "meta_description")),
        "description": strip_html(attribute(raw, "description")),
        "shortDescription": strip_html(attribute(raw, "short_description")),
        "barcode": text(attribute(raw, *BARCODE_NAMES)),
        "barcodeDefined": has_key(raw, *BARCODE_NAMES),
        "images": image_count(raw),
        "categoryIds": category_ids(raw),
        "categoryPartial": category_partial(raw),
        "updatedAt": text(attribute(raw, "updated_at"))[:19],
    }


def category_data_partial(facts: list[dict[str, Any]]) -> bool:
    """Taramadaki kategori bilgisi eksik mi (TUZAK 0).

    Tek bir üründe bile tam kategori listesi yoksa boş kategori denetimi
    yanıltıcıdır: ikincil kategorileri göremediğimiz için dolu kategoriler boş
    görünür. Bu durumda kural — barkod kuralı gibi — kendiliğinden kapanır.
    """
    return any(item.get("categoryPartial") for item in facts)


def barcode_defined(facts: list[dict[str, Any]]) -> bool:
    """Katalogda barkod özniteliği HİÇ tanımlı mı (TUZAK 2).

    Hiçbir üründe alan yoksa kural anlamsızdır ve kapatılır. "Hepsinde boş"
    ile "hiç yok" arasındaki farkı görmezden gelen bir denetim, ekranı
    okunamaz kılar ve kullanıcı bir daha bakmaz.
    """
    return any(item.get("barcodeDefined") for item in facts)


# ============================================================ fiyat medyanı

def median(values: list[int]) -> int:
    """Medyan (TUZAK 4). Boş listede 0."""
    if not values:
        return 0
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def category_medians(facts: list[dict[str, Any]], *, min_rows: int = 4) -> dict[int, int]:
    """Kategori → fiyat medyanı.

    `min_rows` altındaki kategori HESABA GİRMEZ: üç ürünlü bir kategoride
    medyan, "anormal" demeye yetecek kadar bilgi taşımaz ve her yeni ürün
    kendi kendisinin aykırısı olur.
    """
    buckets: dict[int, list[int]] = {}
    for item in facts:
        if item["priceless"] or not item["price"]:
            continue
        for category in item["categoryIds"]:
            buckets.setdefault(category, []).append(item["price"])
    return {key: median(rows) for key, rows in buckets.items()
            if len(rows) >= min_rows and median(rows) > 0}


# =================================================================== kurallar

def _finding(kind: str, target_id: int, *, severity: str, name: str, sku: str = "",
             detail: str = "", target_type: str = "product") -> dict[str, Any]:
    return {
        "id": f"{kind}:{target_id}",
        "kind": kind,
        "kindLabel": KIND_LABELS.get(kind, kind),
        "severity": severity,
        "targetType": target_type,
        "targetId": int(target_id),
        "sku": sku,
        "name": name,
        "detail": detail,
        "tool": KIND_TOOLS.get(kind, ""),
    }


def scan_products(facts: list[dict[str, Any]], *, options: dict[str, Any] | None = None,
                  include_passive: bool = False) -> list[dict[str, Any]]:
    """Ürün bazlı bulgular. Aynı ürün birden çok kurala takılabilir (TUZAK 6)."""
    opts = options or {}
    min_chars = as_int(opts.get("min_description_chars"), 120)
    title_max = as_int(opts.get("meta_title_max"), 60)
    desc_min = as_int(opts.get("meta_description_min"), 70)
    desc_max = as_int(opts.get("meta_description_max"), 160)
    factor = max(2, as_int(opts.get("price_outlier_factor"), 5))

    rows = [item for item in facts if include_passive or item["status"]]
    barcodes_on = barcode_defined(facts)
    medians = category_medians(rows)
    findings: list[dict[str, Any]] = []

    for item in rows:
        head = {"name": item["name"], "sku": item["sku"]}

        if item["images"] == 0:
            findings.append(_finding(MISSING_IMAGE, item["id"], severity="high", **head,
                                     detail="Vitrinde görseli olmayan ürün tıklanmıyor."))

        length = len(item["description"]) + len(item["shortDescription"])
        if length < min_chars:
            findings.append(_finding(
                MISSING_DESCRIPTION, item["id"], severity="medium", **head,
                detail=f"Etiketsiz metin {length} karakter; alt sınır {min_chars}."))

        if barcodes_on and not item["barcode"]:
            findings.append(_finding(MISSING_BARCODE, item["id"], severity="low", **head,
                                     detail="Barkod alanı katalogda var ama bu üründe boş."))

        if not item["categoryIds"]:
            findings.append(_finding(
                MISSING_CATEGORY, item["id"], severity="high", **head,
                detail="Hiçbir kategoride değil; yalnız aramayla bulunur."))

        seo = _seo_problem(item, title_max=title_max, desc_min=desc_min, desc_max=desc_max)
        if seo:
            findings.append(_finding(SEO_BROKEN, item["id"], severity="medium", **head,
                                     detail=seo))

        price = _price_problem(item, medians, factor=factor)
        if price:
            findings.append(_finding(PRICE_ANOMALY, item["id"], severity="high", **head,
                                     detail=price))

    findings.extend(duplicate_url_keys(rows))
    return findings


def _seo_problem(item: dict[str, Any], *, title_max: int, desc_min: int,
                 desc_max: int) -> str:
    """SEO bulgusunun METNİ; sorun yoksa boş. Tek bulguda tüm sebepler yazar —
    kullanıcı aynı ürün için üç ayrı satır okumak zorunda kalmasın."""
    problems: list[str] = []
    if not item["urlKey"]:
        problems.append("URL anahtarı boş")
    if not item["metaTitle"]:
        problems.append("meta başlık boş")
    elif len(item["metaTitle"]) > title_max:
        problems.append(f"meta başlık {len(item['metaTitle'])} karakter (>{title_max})")
    if not item["metaDescription"]:
        problems.append("meta açıklama boş")
    elif len(item["metaDescription"]) < desc_min:
        problems.append(f"meta açıklama {len(item['metaDescription'])} karakter (<{desc_min})")
    elif len(item["metaDescription"]) > desc_max:
        problems.append(f"meta açıklama {len(item['metaDescription'])} karakter (>{desc_max})")
    return " · ".join(problems)


def _price_problem(item: dict[str, Any], medians: dict[int, int], *, factor: int) -> str:
    """Fiyat anormalliğinin METNİ; sorun yoksa boş.

    Varyantlı/set/gruplu ürün ATLANIR (TUZAK 3): fiyatı varyantlarındadır ve
    kendi alanı boş ya da sıfırdır.
    """
    if item["priceless"]:
        return ""
    price = item["price"]
    if not price:
        return "Satılabilir ürünün fiyatı boş ya da sıfır."

    special = item["specialPrice"]
    if special is not None and special > 0 and special >= price:
        return (f"İndirimli fiyat ({special / 100:.2f}) liste fiyatından "
                f"({price / 100:.2f}) düşük değil; indirim indirmiyor.")

    cost = item["cost"]
    if cost is not None and cost > 0 and cost > price:
        return f"Maliyet ({cost / 100:.2f}) satış fiyatının ({price / 100:.2f}) üstünde."

    for category in item["categoryIds"]:
        middle = medians.get(category)
        if not middle:
            continue
        if price > middle * factor:
            return (f"Kategori medyanının {factor} katından pahalı "
                    f"(medyan {middle / 100:.2f}).")
        if price * factor < middle:
            return (f"Kategori medyanının {factor}'te birinden ucuz "
                    f"(medyan {middle / 100:.2f}); virgül kaymış olabilir.")
    return ""


def duplicate_url_keys(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Çift `url_key`. Vitrinde biri diğerini gölgeler ve hangisinin açılacağı
    Bagisto sürümüne göre değişir — sessiz kalması en pahalı bulgudur."""
    seen: dict[str, list[dict[str, Any]]] = {}
    for item in facts:
        key = fold(item["urlKey"])
        if key:
            seen.setdefault(key, []).append(item)

    out: list[dict[str, Any]] = []
    for key, rows in seen.items():
        if len(rows) < 2:
            continue
        others = ", ".join(row["sku"] or f"#{row['id']}" for row in rows[1:])
        for item in rows:
            out.append(_finding(
                DUPLICATE_URL_KEY, item["id"], severity="high", name=item["name"],
                sku=item["sku"],
                detail=f"`{key}` {len(rows)} üründe kullanılıyor (diğerleri: {others})."))
    return out


def empty_categories(facts: list[dict[str, Any]], categories: list[dict[str, Any]],
                     *, include_passive: bool = False) -> list[dict[str, Any]]:
    """Ürünü olmayan kategoriler.

    Sayım YALNIZ taranan ürünler üzerinden yapılır; tarama kırpıldıysa
    (`truncated`) çağıran bunu ekranda söyler, yoksa dolu kategori boş
    görünür. Kök düğüm (parent_id yok) atlanır: kök zaten ürün taşımaz.
    """
    used: set[int] = set()
    for item in facts:
        if not include_passive and not item["status"]:
            continue
        used.update(item["categoryIds"])

    out: list[dict[str, Any]] = []
    for node in categories:
        node_id = as_int(node.get("id"))
        if not node_id or node.get("root"):
            continue
        if node_id in used:
            continue
        out.append(_finding(
            EMPTY_CATEGORY, node_id, severity="low", target_type="category",
            name=text(node.get("name")) or f"#{node_id}",
            detail="Bu kategoride aktif ürün yok; vitrinde boş sayfa açılıyor."))
    return out


def flatten_categories(tree: Any, *, depth: int = 0) -> list[dict[str, Any]]:
    """Kategori ağacını düz listeye indirir; kök düğüm işaretlenir."""
    out: list[dict[str, Any]] = []
    nodes = tree if isinstance(tree, list) else (tree or {}).get("children") or []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        out.append({
            "id": as_int(node.get("id")),
            "name": text(node.get("name")) or f"#{as_int(node.get('id'))}",
            "depth": depth,
            # TUZAK 0: ağaç ucu `parentId` veriyor, `parent_id` değil. Yalnız
            # snake_case'e bakan sürüm her düğümü köksüz sanıyordu; kök yine
            # `depth == 0` sayesinde doğru çıkıyordu ama tesadüfen.
            "root": depth == 0 and not attribute(node, "parent_id"),
        })
        children = attribute(node, "children") or attribute(node, "child_categories")
        if children:
            out.extend(flatten_categories(children, depth=depth + 1))
    return out


# ================================================================ süzme/özet

def drop_ignored(findings: list[dict[str, Any]],
                 ignored: set[str] | list[str] | None) -> list[dict[str, Any]]:
    """Susturulmuş bulguları çıkarır (TUZAK 6: kimlik `kural:hedef`)."""
    keys = set(ignored or ())
    return [item for item in findings if item["id"] not in keys]


def sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Önce ağır bulgu, sonra tür, sonra ad. Sabit sıra: aynı tarama iki kez
    çalıştığında satırlar yer değiştirirse kullanıcı listeye güvenmiyor."""
    return sorted(findings, key=lambda item: (
        -SEVERITY_ORDER.get(item["severity"], 0), item["kind"], fold(item["name"]),
        item["targetId"]))


def summarize(findings: list[dict[str, Any]], *, scanned: int = 0,
              disabled: list[str] | None = None) -> dict[str, Any]:
    """KPI şeridinin ve raporun okuduğu özet."""
    by_kind: dict[str, int] = {}
    by_severity = {"high": 0, "medium": 0, "low": 0}
    targets: set[tuple[str, int]] = set()
    for item in findings:
        by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        if item["severity"] in by_severity:
            by_severity[item["severity"]] += 1
        targets.add((item["targetType"], item["targetId"]))
    return {
        "total": len(findings),
        "scanned": scanned,
        "affected": len(targets),
        "clean": max(0, scanned - len({t[1] for t in targets if t[0] == "product"})),
        "byKind": [{"kind": kind, "label": KIND_LABELS.get(kind, kind),
                    "count": by_kind.get(kind, 0)} for kind in KIND_LABELS],
        "bySeverity": by_severity,
        # Ekranda okunacak ADLA döner: kural anahtarı (`empty_category`)
        # İngilizce ve ASCII'dir, arayüz metni Türkçe.
        "disabledRules": [KIND_LABELS.get(kind, kind) for kind in (disabled or [])],
    }
