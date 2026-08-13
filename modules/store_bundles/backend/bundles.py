"""Set (paket) hesabının saf mantığı — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın tek gerçek işi bir hesaptır: "bu seti bu fiyata
satarsak kâr mı ediyoruz, zarar mı?". Hesap servise gömülseydi tek satırı bile
ağsız test edilemezdi. Burada her şey girdi→çıktı fonksiyonudur.

CANLIDAKİ SET DÜZENİ — ve neden Bagisto'nun `bundle` ürün tipi kullanılmıyor:

  Canlı mağazada set diye bir ürün tipi YOK. "Setler" kategorisi (id 42, slug
  `setler`) altında NORMAL ürünler duruyor; hangi kalemlerden oluştukları
  `product_cross_sells` bağlarıyla, vitrindeki görünürlükleri ise ana sayfa
  carousel'i (`theme_customizations`, type=image_carousel) ile kuruluyor.

  Bu düzenin taşımadığı üç bilgi var ve set hesabı tam onlara dayanıyor:
  ADET, BİLEŞEN İNDİRİMİ, ZORUNLU/OPSİYONEL. Çapraz satış bağı yalnız "şu
  ürünle şu ürün ilişkili" der. Bu yüzden bileşen künyesi yerel tabloda durur
  (bkz. migrations/001_bundles.sql); FİYAT, STOK ve DURUM ise her zaman
  mağazadan taze okunur — kopyalanmaz.

PARA. Her yerde kuruş (int). Bagisto ondalık metin gönderir; çevrimi
`to_kurus` yapar ve `float` KULLANMAZ: `float("1234.35") * 100` bazı
değerlerde 123434.99999 verir ve bir kuruş aşağı yuvarlar.

KDV. Vitrindeki set fiyatı KDV DAHİLDİR (Türkiye perakende); Bagisto'nun
`cost` özniteliği ise alış maliyetidir ve KDV HARİÇTİR. Kâr bu yüzden
"KDV hariç set fiyatı − maliyet" olarak hesaplanır; ikisini aynı torbaya
koymak seti olduğundan %20 kârlı gösterirdi.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (store_api) da 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Fiyatlandırma kipleri.
#: `fixed`   — set fiyatı elle yazılır.
#: `percent` — bileşen toplamından yüzde inilir; fiyat türetilir.
PRICING_MODES = ("fixed", "percent")

#: Bir sette kabul edilen en çok bileşen. Sınır keyfi değil: her bileşen için
#: mağazadan bir ürün okunuyor ve geçit dakikada 55 istek veriyor.
MAX_COMPONENTS = 24

#: Kâr durumu etiketleri — renk tek başına anlam taşımaz, ekran bunları yazar.
STATE_LABELS = {
    "profit": "Kârlı",
    "even": "Başabaş",
    "loss": "ZARARINA",
    "unknown": "Maliyet girilmemiş",
}


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
    """Bagisto'nun ondalık para değerini kuruşa çevirir. Çözülemezse `None`.

    `None` ile `0` ayrımı korunur: maliyeti girilmemiş bileşeni sıfır maliyetli
    saymak, zararına satılan seti kârlı gösterirdi.
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


def reason_error(value: str) -> str:
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def _round(amount: Decimal) -> int:
    return int(amount.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ============================================================== ürün okuma

def camel(name: str) -> str:
    """`special_price` → `specialPrice`. Canlı yanıt camelCase konuşuyor."""
    head, _, rest = name.partition("_")
    if not rest:
        return name
    return head + "".join(part[:1].upper() + part[1:] for part in rest.split("_"))


def attribute_map(raw: Any) -> dict[str, Any]:
    """`attributes: [{code, value}]` dizisini kod→değer sözlüğüne çevirir.

    CANLIDA TEK MALİYET KAYNAĞI BURASI. `/catalog/products/{id}` yanıtının
    gövdesinde `cost` alanı YOKTUR; maliyet yalnız bu dizide `code: "cost"`
    satırı olarak gelir. Aynı şey `special_price` için de geçerli: gövdedeki
    `specialPrice` bazı ürünlerde `null` dönerken dizideki değer doludur.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for item in raw.get("attributes") or []:
        if isinstance(item, dict) and item.get("code"):
            out[str(item["code"])] = item.get("value")
    return out


def attribute(raw: Any, *names: str) -> Any:
    """Bagisto ürün kaydında bir özniteliği nerede olursa olsun bulur.

    Aynı ürün üç ayrı biçimde gelebiliyor ve hangisinin geleceği uca göre
    değişiyor:
      · düz camelCase gövde — `/api/admin/catalog/products` listesi ve
        `/api/admin/products` seçicisi (`specialPrice`, `baseImageUrl`);
      · `attributes: [{code, value}]` dizisi — ürün detayı (maliyet YALNIZ
        burada);
      · `values` EAV zarfı — bazı ürün tiplerinde.
    Üçü de taranır; snake_case ad verilir, camelCase karşılığı da denenir.
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
    attributes = attribute_map(raw)
    if attributes:
        sources.append(attributes)

    wanted: list[str] = []
    for name in names:
        for candidate in (name, camel(name)):
            if candidate not in wanted:
                wanted.append(candidate)

    # Önce DOLU değer aranır: detayda `specialPrice: null` dururken aynı ürünün
    # `attributes` dizisinde gerçek indirimli fiyat olabiliyor; boş olanı önce
    # kabul etmek indirimi görünmez yapardı.
    for name in wanted:
        for source in sources:
            if name in source and source[name] not in (None, ""):
                return source[name]
    for name in wanted:
        for source in sources:
            if name in source:
                return source[name]
    return None


def stock_of(raw: dict[str, Any], inventories: Any = None) -> int:
    """Stok ürünün üzerinde DEĞİL envanter kaynaklarındadır.

    Kaynak satırları verilmediyse vitrin alanı (`quantity`) kullanılır; o
    türetilmiş bir değerdir ve geç güncellenir — bu yüzden satır "yaklaşık"
    işaretlenir (`stockExact`).
    """
    if isinstance(inventories, list) and inventories:
        total = 0
        for row in inventories:
            if isinstance(row, dict):
                total += as_int(row.get("qty", row.get("quantity", row.get("in_stock_qty"))))
        return total
    return as_int(attribute(raw, "quantity", "total_quantity"))


def product_view(raw: Any, *, inventories: Any = None, today: str = "") -> dict[str, Any]:
    """Ham ürün kaydı → bileşen/set satırının ihtiyaç duyduğu alanlar.

    İndirimli fiyat TARİH PENCERESİYLE değerlendirilir: bugün geçerli değilse
    liste fiyatı kullanılır. Süresi geçmiş bir indirimi geçerli saymak, seti
    olduğundan ucuz gösterip kârı yanlış hesaplardı.
    """
    data = raw if isinstance(raw, dict) else {}
    price = to_kurus(attribute(data, "price"))
    special = to_kurus(attribute(data, "special_price"))
    start = text(attribute(data, "special_price_from"))[:10]
    end = text(attribute(data, "special_price_to"))[:10]
    day = today or ""
    active = special is not None and special > 0 and \
        not (start and day and day < start) and not (end and day and day > end)
    effective = special if active else price

    images = data.get("images")
    image = ""
    if isinstance(images, list) and images and isinstance(images[0], dict):
        image = text(images[0].get("url") or images[0].get("original_image_url"))
    if not image:
        # Canlıda alan `baseImageUrl`; `attribute` snake adın camel karşılığını
        # da deniyor.
        image = text(attribute(data, "base_image_url", "image_url"))

    return {
        "id": as_int(data.get("id")),
        "sku": text(attribute(data, "sku")),
        "name": text(attribute(data, "name")) or "(adsız)",
        "status": 1 if as_int(attribute(data, "status"), 0) else 0,
        "price": None if price is None else from_kurus(price),
        "effectivePrice": None if effective is None else from_kurus(effective),
        "specialActive": bool(active),
        "cost": attribute(data, "cost"),
        "stock": stock_of(data, inventories),
        "stockExact": bool(isinstance(inventories, list) and inventories),
        "typeLabel": text(attribute(data, "type")),
        "imageUrl": image,
        "urlKey": text(attribute(data, "url_key")),
    }


# ============================================================ bileşen künyesi

def normalize_component(raw: Any) -> dict[str, Any] | None:
    """Ekrandan/tablodan gelen bileşen tanımını temizler.

    Yalnız TANIM alanları (ürün, adet, indirim, zorunluluk) burada durur;
    fiyat/stok/durum tanımın parçası DEĞİLDİR — onlar mağazadan taze gelir ve
    `merge_component` ile eklenir. Karıştırılırsa ekran, kaydedildiği günün
    fiyatını bugünün fiyatı gibi gösterir.
    """
    if not isinstance(raw, dict):
        return None
    product_id = as_int(raw.get("productId") or raw.get("product_id"))
    if not product_id:
        return None
    return {
        "productId": product_id,
        "qty": max(1, as_int(raw.get("qty"), 1)),
        "discount": max(0, min(100, as_int(raw.get("discount")))),
        "required": bool(raw.get("required", True)),
    }


def normalize_components(rows: Any) -> list[dict[str, Any]]:
    """Tanım listesi. Aynı ürün iki kez verilirse adetler BİRLEŞTİRİLİR.

    Sessizce ikinci satırı düşürmek adedi kaybettirirdi; iki ayrı satır
    bırakmak da kullanıcının göremediği bir çift kayıt üretirdi.
    """
    out: list[dict[str, Any]] = []
    seen: dict[int, dict[str, Any]] = {}
    for raw in rows if isinstance(rows, list) else []:
        item = normalize_component(raw)
        if item is None:
            continue
        current = seen.get(item["productId"])
        if current is None:
            seen[item["productId"]] = item
            out.append(item)
            continue
        current["qty"] += item["qty"]
        current["required"] = current["required"] or item["required"]
    return out[:MAX_COMPONENTS]


def merge_component(definition: dict[str, Any], product: Any) -> dict[str, Any]:
    """Tanım + mağazadan gelen ürün kaydı → tablonun beklediği bileşen satırı.

    Ürün okunamadıysa satır `missing: True` ile döner ve fiyat alanları `None`
    kalır. Uydurma sıfır yazmak, eksik veriyi "bedava bileşen" yapardı.
    """
    row = {
        "productId": definition["productId"],
        "qty": definition["qty"],
        "discount": definition["discount"],
        "required": definition["required"],
        "sku": "", "name": f"#{definition['productId']}",
        "unitPrice": None, "cost": None, "stock": 0,
        "status": False, "missing": True, "typeLabel": "",
    }
    if not isinstance(product, dict):
        return row

    row["missing"] = False
    row["sku"] = text(product.get("sku"))
    row["name"] = text(product.get("name")) or row["name"]
    row["status"] = bool(as_int(product.get("status"), 0))
    row["stock"] = as_int(product.get("stock"))
    row["unitPrice"] = to_kurus(product.get("effectivePrice", product.get("price")))
    row["cost"] = to_kurus(product.get("cost"))
    row["typeLabel"] = text(product.get("typeLabel"))
    return row


def component_lines(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Her bileşenin satır toplamı: adet × birim − bileşen indirimi."""
    out: list[dict[str, Any]] = []
    for item in components:
        unit = item.get("unitPrice")
        gross = None if unit is None else unit * max(1, as_int(item.get("qty"), 1))
        discount = 0 if gross is None else _round(
            Decimal(gross) * Decimal(as_int(item.get("discount"))) / Decimal(100))
        out.append({
            **item,
            "lineGross": gross,
            "lineDiscount": discount,
            "lineNet": None if gross is None else gross - discount,
        })
    return out


# ================================================================== hesap

def stock_limit(components: list[dict[str, Any]]) -> tuple[int | None, str]:
    """Kaç set satılabilir — EN KISITLI ZORUNLU BİLEŞEN belirler.

    Opsiyonel bileşen sınırı belirlemez: yoksa set yine satılır. Bileşenin
    biri okunamadıysa sayı `None` döner; "sınırsız" demek yanlış olurdu.
    """
    limit: int | None = None
    culprit = ""
    for item in components:
        if not item.get("required", True):
            continue
        if item.get("missing"):
            return None, text(item.get("sku")) or f"#{item.get('productId')}"
        possible = as_int(item.get("stock")) // max(1, as_int(item.get("qty"), 1))
        if limit is None or possible < limit:
            limit = possible
            culprit = text(item.get("name")) or text(item.get("sku"))
    return limit, culprit


def compute(components: Any, *, pricing_mode: str = "fixed", discount_percent: int = 0,
            set_price: int | None = None, tax_rate: float = 20.0,
            prices_include_tax: bool = True) -> dict[str, Any]:
    """CANLI HESAP KUTUSU: bileşen toplamı → indirim → KDV → set fiyatı → kâr.

    `set_price` kuruş; `percent` kipinde YOK SAYILIR ve bileşen toplamından
    türetilir. Maliyeti girilmemiş zorunlu bileşen varsa kâr `None` döner ve
    durum `unknown` olur — sıfır maliyet varsayıp "kârlı" demek, bu ekranın
    var olma sebebini ortadan kaldırırdı.
    """
    lines = component_lines(list(components or []))
    required = [item for item in lines if item.get("required", True)]
    optional = [item for item in lines if not item.get("required", True)]

    gross = sum(item["lineGross"] or 0 for item in required)
    component_discount = sum(item["lineDiscount"] for item in required)
    after_discount = gross - component_discount
    optional_total = sum(item["lineNet"] or 0 for item in optional)

    mode = pricing_mode if pricing_mode in PRICING_MODES else "fixed"
    percent = max(0, min(100, as_int(discount_percent)))
    if mode == "percent":
        price: int | None = _round(Decimal(after_discount) * Decimal(100 - percent) / Decimal(100))
    else:
        price = None if set_price is None else max(0, as_int(set_price))

    rate = Decimal(str(max(0.0, float(tax_rate or 0))))
    if price is None:
        net = tax = None
    elif prices_include_tax:
        net = _round(Decimal(price) * Decimal(100) / (Decimal(100) + rate))
        tax = price - net
    else:
        net = price
        tax = _round(Decimal(price) * rate / Decimal(100))

    unknown_costs = [text(item.get("sku")) or f"#{item.get('productId')}"
                     for item in required if item.get("cost") is None]
    cost_total = None if unknown_costs else sum(
        (item.get("cost") or 0) * max(1, as_int(item.get("qty"), 1)) for item in required)

    if net is None or cost_total is None:
        profit: int | None = None
        margin: float | None = None
        state = "unknown"
    else:
        profit = net - cost_total
        margin = round(profit * 100 / net, 1) if net else None
        state = "profit" if profit > 0 else ("loss" if profit < 0 else "even")

    set_discount = None if price is None else after_discount - price
    savings = None
    if price is not None and after_discount > 0:
        savings = round((after_discount - price) * 100 / after_discount, 1)

    limit, limited_by = stock_limit(required)
    return {
        # Satır toplamları hesapla BİRLİKTE döner: ekran onları ikinci kez
        # hesaplasaydı iki ayrı yuvarlama kuralı doğar ve tablo ile kutu
        # birbirini tutmazdı.
        "lines": lines,
        "componentsTotal": gross,
        "componentDiscount": component_discount,
        "afterComponentDiscount": after_discount,
        "optionalTotal": optional_total,
        "setPrice": price,
        "setDiscount": set_discount,
        "savingsPercent": savings,
        "taxRate": float(tax_rate or 0),
        "tax": tax,
        "netPrice": net,
        "costTotal": cost_total,
        "profit": profit,
        "marginPercent": margin,
        "state": state,
        "stateLabel": STATE_LABELS[state],
        "unknownCosts": unknown_costs,
        "stockLimit": limit,
        "stockLimitedBy": limited_by,
        "requiredCount": len(required),
        "optionalCount": len(optional),
    }


def flags(calc: dict[str, Any], components: list[dict[str, Any]]) -> dict[str, bool]:
    """Anahtar süzgeçlerin dayandığı üç bulgu.

    `loss` yalnız maliyet BİLİNİYORken doğrudur: bilinmeyeni zarar saymak,
    maliyeti hiç girilmemiş bir kataloğu baştan sona kırmızı gösterirdi.
    """
    required = [item for item in components if item.get("required", True)]
    return {
        "loss": calc.get("state") == "loss",
        "outOfStock": any(as_int(item.get("stock")) < as_int(item.get("qty"), 1)
                          for item in required if not item.get("missing")),
        "passive": any(not item.get("status") for item in required if not item.get("missing")),
        "missing": any(item.get("missing") for item in components),
        "unpriced": bool(calc.get("unknownCosts")),
    }


def warning_text(marks: dict[str, bool]) -> str:
    """Rozetin yanında duracak yazı. Renk tek başına anlam taşımaz (kural 7)."""
    parts = []
    if marks.get("loss"):
        parts.append("zararına")
    if marks.get("outOfStock"):
        parts.append("bileşeni tükenmiş")
    if marks.get("passive"):
        parts.append("bileşeni pasif")
    if marks.get("missing"):
        parts.append("bileşeni okunamadı")
    return " · ".join(parts)


# ============================================================== set künyesi

def definition_row(raw: Any) -> dict[str, Any]:
    """Yerel tablodan ya da mağazadan gelen set TANIMINI ortak şekle getirir."""
    data = raw if isinstance(raw, dict) else {}
    mode = text(data.get("pricingMode") or data.get("pricing_mode")) or "fixed"
    # KDV oranı 0 OLABİLİR (kitapta yasal oran %0/%1). `or None` yazmak sıfırı
    # "belirtilmemiş" sayar ve modül ayarındaki %20'ye düşürürdü — kâr yanlış
    # çıkardı. Boşluk ile sıfır burada ayrı tutulur.
    raw_rate = data.get("taxRate", data.get("tax_rate"))
    try:
        rate = None if raw_rate in (None, "") else float(raw_rate)
    except (TypeError, ValueError):
        rate = None
    return {
        "productId": as_int(data.get("productId") or data.get("product_id")
                            or data.get("id")),
        "name": text(data.get("name")),
        "sku": text(data.get("sku")),
        "pricingMode": mode if mode in PRICING_MODES else "fixed",
        "discountPercent": max(0, min(100, as_int(data.get("discountPercent")
                                                  or data.get("discount_percent")))),
        "setPrice": to_kurus(data.get("setPrice", data.get("set_price"))),
        "taxRate": rate,
        "validFrom": text(data.get("validFrom") or data.get("valid_from"))[:10],
        "validTo": text(data.get("validTo") or data.get("valid_to"))[:10],
        "note": text(data.get("note")),
        "components": normalize_components(data.get("components")),
    }


def validity_state(valid_from: str, valid_to: str, today: str) -> str:
    """`always` · `scheduled` (başlamadı) · `active` · `expired`."""
    if not valid_from and not valid_to:
        return "always"
    if valid_from and today < valid_from:
        return "scheduled"
    if valid_to and today > valid_to:
        return "expired"
    return "active"


def sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """Liste sırası: önce sorunlular, sonra kâr farkına göre, sonra ad.

    Kullanıcı bu ekrana "hangi set beni yakıyor" diye geliyor; alfabetik sıra
    o soruyu cevaplamıyor.
    """
    marks = row.get("flags") or {}
    trouble = 0 if (marks.get("loss") or marks.get("outOfStock") or marks.get("passive")) else 1
    profit = row.get("calc", {}).get("profit")
    return (trouble, profit if profit is not None else 0, text(row.get("name")).lower())


def csv_table(rows: list[dict[str, Any]]) -> tuple[list[str], list[list[Any]]]:
    """Set listesinin CSV/PDF gövdesi. Para KURUŞ olarak döner; biçim çağıranın."""
    headers = ["Set", "SKU", "Bileşen", "Set fiyatı", "Bileşen toplamı", "Kazanç/kayıp",
               "Kâr", "Durum", "Uyarı"]
    table: list[list[Any]] = []
    for row in rows:
        calc = row.get("calc") or {}
        table.append([
            row.get("name", ""), row.get("sku", ""), len(row.get("components") or []),
            calc.get("setPrice"), calc.get("afterComponentDiscount"), calc.get("setDiscount"),
            calc.get("profit"), "Aktif" if row.get("status") else "Pasif",
            warning_text(row.get("flags") or {}) or "—",
        ])
    return headers, table
