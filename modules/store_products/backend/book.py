"""Kitap künyesi ve desi hesabı — saf dönüşümler, ağa çıkmaz.

═══════════════════════════════════════════════════════════════════════════════
NEDEN AYRI DOSYA VE NEDEN BURADA BİR HESAP VAR

Mağaza kargo ücretini DESİ'den çıkarıyor ve katalogda hacim bilgisi yok:
`desi`, `length`, `width`, `height` nitelikleri boş, `weight` ise yer tutucu.
Elde gerçekten dolu olan tek ölçü `page_count` — kitabın kalınlığını belirleyen
şey de zaten odur. Sunucu bu yüzden desiyi sayfa sayısından hesaplıyor
(`packages/BBD/Shipping/src/Support/BookDimensions.php` ve `DesiCalculator.php`).

BU DOSYA O HESABIN AYNISINI YAPAR. Sebebi kozmetik değil: ürün ekranında sayfa
sayısı yazılırken desinin ANINDA görünmesi isteniyor ve o rakam, müşterinin
checkout'ta ödeyeceği kargo ücretinin girdisi. Ekranın gösterdiği desi ile
mağazanın hesapladığı desi ayrışırsa, personel bir sayı görüp başka bir sayıyla
satış yapar.

═══════════════════════════════════════════════════════════════════════════════
KATSAYILAR TEK YERDE, EKRANDA DEĞİL

Panel bu sabitleri KENDİ İÇİNE YAZMAZ; `rules()` ile sunucudan alır ve
aritmetiği onunla yapar. Aksi hâlde katsayı üç yerde (PHP · Python · JS)
yaşardı ve biri değiştiğinde diğer ikisi sessizce eski kalırdı.

Katsayıların KAYNAKLARI mağaza tarafındaki `BookDimensions` başında yazılı
(16 sayfa = 1 forma · 70 g/m² 1. hamurda forma 0,7 mm sırt · kapak+cilt payı
1 mm · MEB standardı 19,5×27,5 cm · kenar başına 1 cm koli payı). Burada
tekrarlanmaz; iki yerde iki ayrı gerekçe metni, biri güncellenip diğeri
unutulduğunda çelişir.

═══════════════════════════════════════════════════════════════════════════════
ÖNCELİK SIRASI — ÜÇ BASAMAK (`DesiCalculator::explainProduct` ile AYNI)

 1. Ürünün `desi` NİTELİĞİ doluysa O KAZANIR. Elle girilen değer bir ÖLÇÜMDÜR;
    buradaki hesap bir MODELDİR. Modelin ölçümü ezmesi, depoda paketi eline
    alıp ölçen kişinin kararını sessizce çöpe atmak olurdu.
 2. `page_count` okunabiliyorsa desi sayfadan hesaplanır.
 3. İkisi de yoksa `DEFAULT_DESI` = 1,0. Bilerek CÖMERT: gerçek bir kitap 0,2
    desi civarındadır. Veri eksikse müşteriden fazla almak, kargo faturasıyla
    açığı üstlenmekten iyidir.

═══════════════════════════════════════════════════════════════════════════════
NİTELİK KODLARI VARSAYILMAZ, ÇÖZÜLÜR

Kataloğun hangi kodu kullandığı kuruluma göre değişiyor. `resolve_codes` bunu
mağazanın NİTELİK LİSTESİNDEN çözer: aday adlar sırayla aranır, bulunan kod
kullanılır, bulunamayan alan EKRANDA HİÇ AÇILMAZ ve nedeni yazılır.

Uydurulmuş bir koda yazmanın bedeli sessizdir: Bagisto tanımadığı özniteliği
yok sayar, istek 200 döner, personel "kaydettim" der ve değer hiçbir yere
yazılmamıştır.

TİP DE VARSAYILMAZ. Canlıda yayınevi, kitap dili, sınav türü ve yayın tipi
`select` tipinde ve değerleri SEÇENEK KİMLİĞİ olarak duruyor (yayınevi = `76`).
Serbest metin yazmak, olmayan bir koda yazmakla aynı sessiz kayıptır; bu yüzden
`field_specs` tipi ve seçenekleri de taşır ve seçenekleri okunamayan alan
açılmaz.

SAYFA SAYISI VE DESİ BU ESNEKLİĞİN DIŞINDADIR ve tek bir kodu kabul eder
(`page_count`, `desi`): mağazanın kargo hesabı bu iki özniteliği ADIYLA
okuyor, yani eş anlamlı bir koda yazmak ekranda "güncellendi" gösterip kargo
ücretini hiç değiştirmezdi. Gerekçenin tamamı `FIELD_CANDIDATES` başında.

ÖZNİTELİK AİLESİ DE VARSAYILMAZ. "Nitelik katalogda var" ile "nitelik BU
ÜRÜNE yazılabilir" ayrı iki sorudur: mağaza gövdenin anahtarlarını ürünün
ailesinin nitelik listesiyle kesiştiriyor ve dışarıda kalan kodu hata da uyarı
da üretmeden düşürüyor. Canlıda `default` ailesi kitap alanlarından yalnız
`desi`yi taşıyor. `field_specs`/`draft_errors`/`patch_for` bu yüzden bir de
`family_codes` alır; `None` verilirse aile BİLİNMİYOR demektir ve kısıt
uygulanmaz.

═══════════════════════════════════════════════════════════════════════════════
TÜRKÇE ONDALIK VİRGÜL: OKUNURKEN KABUL, YAZILIRKEN ÇEVRİLİR

Okuyucular (`positive_float`, `page_count`) virgülü bilerek kabul ediyor —
personel `0,45` yazacak. Ama mağaza tarafı aynı değeri PHP `is_numeric()` ile
okuyor ve PHP ondalık ayracı olarak yalnız noktayı tanır. Ham yazılan virgül
mağazada HİÇ okunmaz ve ürün varsayılan 1,0 desiye düşer. Bu yüzden yazma
yolunda `canonical_number` var: doğrulayıcı ile yazıcı aynı kabulü paylaşır,
mağaza her zaman nokta ondalıklı bir sayı görür.
"""

from __future__ import annotations

import math
from typing import Any

# ═════════════════════════════════════════════════════════ katsayılar
#
# Hepsi mağaza tarafındaki `Bbd\Shipping\Support\BookDimensions` sabitleriyle
# BİREBİR aynıdır. Değişirlerse iki tarafta birden değişmeleri gerekir; bunu
# kilitleyen test `test_store_products_book.py` içindedir.

#: Türkiye'de hacimsel ağırlık böleni (cm³ → desi). Yurt içi kara kargo 3000
#: kullanır; havayolu 5000'dir ve bu mağaza kara kargo çalışıyor.
DESI_DIVISOR = 3000

#: Tek SAYFANIN (yaprağın yarısının) kalınlığı, mm. 70 g/m² 1. hamur.
PAGE_THICKNESS_MM = 0.04375

#: Ön + arka kapak ve amerikan cilt tutkal payı, mm. KİTAP BAŞINA sayılır —
#: 3 adet kitap 3 kapak demektir.
COVER_THICKNESS_MM = 1.0

#: Kitap formatı (kesim ölçüsü), cm. Katalogda ebat niteliği hiç doldurulmadığı
#: için ürün başına çıkarılamıyor; tek sabit kullanılıyor.
TRIM_WIDTH_CM = 19.5
TRIM_HEIGHT_CM = 27.5

#: Koli/zarf payı — en ve boyun HER kenarına eklenir, cm. Kargoya giden şey
#: çıplak kitap değil kolidir ve firma ölçüyü ondan alır.
PACKAGING_MARGIN_CM = 1.0

#: Güvenilir kabul edilen en yüksek sayfa sayısı. Alan elle doldurulur ve
#: yanlış girilen bir sayı doğrudan paraya dokunur; bu tavan bir hanelik yazım
#: hatasının sepete onlarca desi eklemesini engeller. Tavana çarpan ürün yine
#: sayfadan hesaplanır, sessizce varsayılana DÜŞMEZ.
MAX_PAGE_COUNT = 5000

#: Ne desisi ne sayfa sayısı bilinen ürün için varsayılan.
DEFAULT_DESI = 1.0

#: Kargoya HİÇ girmeyen ürün tipleri ve stok kodları.
NON_SHIPPING_TYPES = ("virtual", "downloadable")
NON_SHIPPING_SKUS = ("BBD-OZEL-TAHSILAT", "deneme-kulubu-uyelik")

#: Desi kaynağı — hangi basamaktan geçildiği.
SOURCE_ATTRIBUTE = "attribute"
SOURCE_PAGE_COUNT = "page_count"
SOURCE_FALLBACK = "fallback"
SOURCE_NOT_SHIPPABLE = "not_shippable"

SOURCE_LABELS = {
    SOURCE_ATTRIBUTE: "Elle girilen desi (ölçüm — hesabı ezer)",
    SOURCE_PAGE_COUNT: "Sayfa sayısından hesaplandı",
    SOURCE_FALLBACK: "Varsayılan (ne desi ne sayfa sayısı var)",
    SOURCE_NOT_SHIPPABLE: "Kargoya girmiyor",
}


def footprint_cm2() -> float:
    """Paketin taban alanı (ambalaj payı dahil), cm²."""
    return (TRIM_WIDTH_CM + 2 * PACKAGING_MARGIN_CM) * (TRIM_HEIGHT_CM + 2 * PACKAGING_MARGIN_CM)


def thickness_cm_for_pages(page_count: int) -> float:
    """Bir kitabın kalınlığı (cm): iç sayfalar + kapak."""
    pages = max(0, min(int(page_count), MAX_PAGE_COUNT))
    return (pages * PAGE_THICKNESS_MM + COVER_THICKNESS_MM) / 10


def desi_for_thickness_cm(thickness_cm: float) -> float:
    """Verilen yığın kalınlığının desisi — YUVARLANMAMIŞ."""
    return max(0.0, float(thickness_cm)) * footprint_cm2() / DESI_DIVISOR


def desi_for_pages(page_count: int) -> float:
    """Tek bir kitabın desisi — YUVARLANMAMIŞ.

    YUVARLANMAZ: yuvarlama paketin tamamında BİR KEZ yapılır. Ürün başına
    yuvarlansaydı 5 ince kitap 5 desi ederdi; oysa hepsi tek kolide 1 desi
    tutuyor.
    """
    return desi_for_thickness_cm(thickness_cm_for_pages(page_count))


def thickness_cm_for_desi(desi_value: float) -> float:
    """Desiden yığın kalınlığına geri dönüş, cm.

    Elle `desi` girilmiş ürünler için gerekir: kalınlıkları bilinmez ama
    yığındaki payları yine de gösterilebilmelidir.
    """
    return max(0.0, float(desi_value)) * DESI_DIVISOR / footprint_cm2()


def billed_desi(unit_desi: float, quantity: int = 1) -> int:
    """Ücretlendirilecek desi — ADET KALINLIĞA uygulanır, hacme değil.

    KÜSURAT YUKARI YUVARLANIR: kargo firması da öyle yapar (1,2 desi = 2 desi).
    Aşağı yuvarlamak, aradaki farkı her siparişte mağazanın üstlenmesi demekti.
    Kargo istenen bir sepette sonuç asla 0 olamaz.
    """
    return max(1, math.ceil(float(unit_desi) * max(1, int(quantity))))


# ═══════════════════════════════════════════════════════ sayı okuyucular

def page_count(value: Any) -> int | None:
    """Sayfa sayısı; okunamıyorsa `None`.

    Alan elle doldurulur ve HER ZAMAN SAYI DEĞİLDİR — canlıda `page_count`
    değeri "Fasikül" olan bir ürün var. Sayıya çevrilemeyen değer YOK sayılır;
    0'a çevirip kitabı kapak kalınlığına indirmek sessiz bir hata olurdu.
    """
    if isinstance(value, bool) or value is None:
        return None
    try:
        pages = int(float(str(value).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return None
    return pages if pages > 0 else None


def positive_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


# ═══════════════════════════════════════════════════════════ desi dökümü

def explain(*, desi_value: Any = None, pages: Any = None, kind: str = "",
            sku: str = "") -> dict[str, Any]:
    """Tek ürünün desi dökümü — hangi basamaktan geçtiğiyle birlikte.

    `DesiCalculator::explainProduct()` ile AYNI sıra ve AYNI sonuç. Dökümün
    kendisi ekranda gösterilir: "0,18 desi" demek yetmez, personel o rakamın
    nereden geldiğini görmeden sayfayı düzeltmeye karar veremez.
    """
    kind = str(kind or "").strip().lower()
    sku = str(sku or "").strip()

    if kind in NON_SHIPPING_TYPES or sku in NON_SHIPPING_SKUS:
        return {"source": SOURCE_NOT_SHIPPABLE, "sourceLabel": SOURCE_LABELS[SOURCE_NOT_SHIPPABLE],
                "pageCount": page_count(pages), "thicknessCm": 0.0, "unitDesi": 0.0,
                "billed": 0}

    measured = positive_float(desi_value)
    if measured is not None:
        return {"source": SOURCE_ATTRIBUTE, "sourceLabel": SOURCE_LABELS[SOURCE_ATTRIBUTE],
                "pageCount": page_count(pages),
                "thicknessCm": round(thickness_cm_for_desi(measured), 4),
                "unitDesi": round(measured, 4), "billed": billed_desi(measured)}

    found = page_count(pages)
    if found is not None:
        thickness = thickness_cm_for_pages(found)
        unit = desi_for_thickness_cm(thickness)
        return {"source": SOURCE_PAGE_COUNT, "sourceLabel": SOURCE_LABELS[SOURCE_PAGE_COUNT],
                "pageCount": found, "thicknessCm": round(thickness, 4),
                "unitDesi": round(unit, 4), "billed": billed_desi(unit),
                "capped": int(found) > MAX_PAGE_COUNT}

    return {"source": SOURCE_FALLBACK, "sourceLabel": SOURCE_LABELS[SOURCE_FALLBACK],
            "pageCount": None, "thicknessCm": round(thickness_cm_for_desi(DEFAULT_DESI), 4),
            "unitDesi": DEFAULT_DESI, "billed": billed_desi(DEFAULT_DESI)}


def rules() -> dict[str, Any]:
    """Katsayılar + açıklama — PANEL BUNU KULLANIR, kendi sabitini yazmaz.

    Ekranın anlık hesabı bu değerlerle yapılır; böylece katsayı tek yerde
    (mağaza tarafındaki `BookDimensions`, buradaki kopyası testle kilitli)
    yaşar ve panel onu ezberlemez.
    """
    return {
        "desiDivisor": DESI_DIVISOR,
        "pageThicknessMm": PAGE_THICKNESS_MM,
        "coverThicknessMm": COVER_THICKNESS_MM,
        "trimWidthCm": TRIM_WIDTH_CM,
        "trimHeightCm": TRIM_HEIGHT_CM,
        "packagingMarginCm": PACKAGING_MARGIN_CM,
        "footprintCm2": round(footprint_cm2(), 4),
        "maxPageCount": MAX_PAGE_COUNT,
        "defaultDesi": DEFAULT_DESI,
        "formula": ("kalınlık_cm = (sayfa × 0,04375 mm + 1,0 mm kapak) / 10 · "
                    "desi = taban_cm² × kalınlık_cm / 3000 · "
                    "taban = (19,5+2) × (27,5+2) = 634,25 cm²"),
        "note": ("Elle girilen `desi` bir ÖLÇÜMDÜR ve bu hesabı EZER. Sayfa sayısı da "
                 "yoksa ürün 1,0 desi sayılır — bilerek cömert, çünkü eksik beyan edilen "
                 "desinin farkını mağaza öder."),
    }


# ═════════════════════════════════════════════════════════ nitelik kodları

#: KARGO HESABININ OKUDUĞU İKİ KOD. Mağaza tarafındaki `DesiCalculator`
#: bunları HARFİ HARFİNE okuyor (`$product->page_count`, `$product->desi`).
PAGE_CODE = "page_count"
DESI_CODE = "desi"

#: Ekranın alanı → mağazada aranacak nitelik kodları, SIRAYLA.
#:
#: ═══════════════════════════════════════════════════════════════════════════
#: SAYFA SAYISI VE DESİNİN ADAY LİSTESİ NEDEN TEK ELEMANLI
#:
#: Diğer alanlarda eş anlamlı kod aramak yardımcıdır: kataloğun `yayinevi` mi
#: `publisher` mı kullandığı bir adlandırma tercihidir ve ikisi de aynı işi
#: görür. Sayfa sayısı ve desi ÖYLE DEĞİL: mağazanın kargo hesabı bu iki
#: özniteliği ADIYLA okuyor. Katalogda `sayfa_sayisi` diye bir nitelik olsa ve
#: ekran onu düzenlese, personel sayfa sayısını "güncellemiş" olurdu ama kargo
#: ücreti hiç değişmezdi — ürün varsayılan 1,0 desiden ücretlendirilmeye devam
#: ederdi ve bunu kimse fark etmezdi.
#:
#: Yani buradaki darlık bir eksiklik değil, PARANIN DOĞRU YERDEN HESAPLANMASI
#: kuralıdır: yalnız gerçekten etkisi olan alan düzenlenebilir.
#:
#: SIRA EKRANDAKİ SIRADIR: künye alanları önce, elle ölçülen desi en sonda —
#: desi bir künye bilgisi değil, hesabı ezen bir istisnadır ve listenin
#: başında dururken kazara doldurulması kolaydı.
FIELD_CANDIDATES = {
    "pageCount": (PAGE_CODE,),
    "isbn": ("isbn", "isbn_no", "isbn13"),
    "publisher": ("publisher", "yayinevi", "yayin_evi", "yayinci"),
    "author": ("author", "yazar", "writer"),
    # `print_year` ÖLÇÜLDÜ (canlı nitelik listesi, 16.08.2026): katalog baskı
    # yılını bu kodla tutuyor. Aday listesinin başına konmasının sebebi
    # tahmin değil ölçüm; diğerleri başka bir kurulumda karşılığı olabilecek
    # adlar olarak arkada durur.
    "publishYear": ("print_year", "publish_year", "publication_year", "baski_yili",
                    "basim_yili", "yayin_yili", "year"),
    "bookLanguage": ("book_language", "kitap_dili"),
    "examType": ("exam_type", "sinav_turu"),
    "publishType": ("publish_type", "yayin_tipi"),
    "desi": (DESI_CODE,),
}

#: Alanların ekranda görünen adı ve ipucu.
FIELD_LABELS = {
    "pageCount": "Sayfa sayısı",
    "isbn": "ISBN",
    "publisher": "Yayınevi",
    "author": "Yazar",
    "publishYear": "Baskı yılı",
    "bookLanguage": "Kitap dili",
    "examType": "Sınav türü",
    "publishType": "Yayın tipi",
    "desi": "Desi (elle ölçüm)",
}

#: Sayı olarak yazılan alanlar. Bagisto metin sütununda tutuyor olabilir ama
#: ekran sayı doğrular: "Fasikül" yazan bir `page_count` canlıda zaten var ve
#: kargo hesabını sessizce varsayılana düşürüyor.
NUMERIC_FIELDS = ("pageCount", "publishYear", "desi")

#: SEÇENEKLİ nitelik tipleri.
#:
#: ═══════════════════════════════════════════════════════════════════════════
#: NEDEN TİP ÖNEMLİ — ÖLÇÜLDÜ (canlı, 16.08.2026)
#:
#: `publisher`, `book_language`, `exam_type` ve `publish_type` mağazada
#: `select` tipinde ve değerleri SEÇENEK KİMLİĞİ olarak saklanıyor: bir kitabın
#: yayınevi `76`dır, "Panama Yayıncılık" değil. Bu alana serbest metin yazmak,
#: var olmayan bir koda yazmakla AYNI sessiz kayıptır — Bagisto isteği kabul
#: eder, personel "kaydettim" der ve vitrinde yayınevi değişmemiştir.
#:
#: Bu yüzden tip VARSAYILMAZ, mağazanın nitelik satırından okunur; seçenekleri
#: okunamayan seçimli alan EKRANDA HİÇ AÇILMAZ ve nedeni yazılır.
OPTION_TYPES = ("select", "multiselect")


def resolve_codes(attributes: Any) -> dict[str, str]:
    """Mağazanın nitelik listesinden bu ekranın alanlarını çözer.

    `attributes` nitelik satırlarıdır (`code` alanı taşıyan sözlükler).
    Bulunamayan alan BOŞ döner ve ekran onu çizmez — var olmayan bir koda
    yazmak, Bagisto'nun isteği 200 ile kabul edip değeri hiç yazmaması demektir
    ve personel "kaydettim" sanır.
    """
    codes: set[str] = set()
    for item in attributes or []:
        if isinstance(item, dict):
            code = str(item.get("code") or "").strip()
            if code:
                codes.add(code)
        elif isinstance(item, str) and item.strip():
            codes.add(item.strip())

    resolved: dict[str, str] = {}
    for field, candidates in FIELD_CANDIDATES.items():
        resolved[field] = next((code for code in candidates if code in codes), "")
    return resolved


def attribute_kind(row: Any) -> str:
    """Nitelik satırından tip. Okunamayan tip METİN sayılır.

    Metne düşmek güvenli tarafa düşmektir: metin alanına metin yazmak en kötü
    ihtimalle işe yaramaz, seçimli alana metin yazmak ise sessizce yanlış veri
    üretir. Tersi varsayım (bilinmeyeni seçim saymak) çalışan bir metin alanını
    kapatırdı.
    """
    if not isinstance(row, dict):
        return "text"
    return str(row.get("type") or row.get("attributeType") or "text").strip().lower() or "text"


def option_rows(detail: Any) -> list[dict[str, str]]:
    """Nitelik DETAYINDAN seçenek listesi → `[{"value": "76", "label": "…"}]`.

    `value` METİNDİR: panelin `<select>` kutusu değerleri metin olarak
    karşılaştırıyor ve ürün kaydından okunan değer de metne indiriliyor
    (`view`). İkisinin aynı biçimde olması, kayıtlı yayınevinin listede
    SEÇİLİ gelmesini sağlar; sayı/metin karışımı alanı boş gösterirdi.

    Yazarken kimlik yeniden sayıya çevrilir (`patch_for`) — mağaza sayı
    bekliyor.
    """
    body = detail.get("data") if isinstance(detail, dict) and isinstance(
        detail.get("data"), dict) else detail
    rows = (body or {}).get("options") if isinstance(body, dict) else None
    out: list[dict[str, str]] = []
    for item in rows or []:
        if not isinstance(item, dict):
            continue
        value = _text(item.get("id") or item.get("value"))
        label = _text(item.get("adminName") or item.get("admin_name")
                      or item.get("label") or item.get("name"))
        if value:
            out.append({"value": value, "label": label or value})
    return out


def family_code_set(codes: Any) -> set[str] | None:
    """Aile nitelik kodlarını kümeye indirger; `None` verilirse `None` kalır.

    `None` ile boş küme AYRI ŞEYLERDİR ve karıştırılırsa kural tersine döner:
    `None` "ailenin şeması OKUNAMADI" demektir (kısıt uygulanmaz), boş küme
    "aile okundu ve hiç nitelik taşımıyor" demektir (her alan kapanır).
    """
    if codes is None:
        return None
    return {str(code).strip() for code in codes if str(code).strip()}


def field_specs(codes: dict[str, str],
                catalog: dict[str, Any] | None = None,
                family_codes: Any = None) -> list[dict[str, Any]]:
    """Panelin çizeceği alanların tarifi — çözülemeyenler NEDENİYLE birlikte.

    Çözülemeyen alan listeden DÜŞÜRÜLMEZ, `available: False` ile döner:
    "yayınevi neden yok" sorusunun cevabı ekranda durmalı, kullanıcının
    tahmin etmesine bırakılmamalı.

    `catalog` mağazadan çözülen nitelik künyesidir —
    `{kod: {"type": …, "options": [{"value", "label"}]}}`. Verilmezse her alan
    METİN sayılır: bu, bu dosyanın ağa çıkmadığı ve tipin ancak mağazadan
    öğrenilebildiği anlamına gelir, "tip yoktur" anlamına değil.

    `family_codes` ÜRÜNÜN ÖZNİTELİK AİLESİNİN taşıdığı kodlardır; `None` ise
    aile bilinmiyor demektir ve kısıt UYGULANMAZ (ağ hatasını kalıcı bir
    eksikliğe çevirmemek için).

    DÖRT HÂL VARDIR:
      · kod çözüldü, alan yazılabilir               → `available: True`
      · katalogda karşılık gelen nitelik yok        → `available: False` + neden
      · nitelik katalogda var, ÜRÜNÜN AİLESİNDE YOK → `available: False` + neden
      · nitelik SEÇİMLİ ama seçenekleri okunamadı   → `available: False` + neden

    ═══════════════════════════════════════════════════════════════════════════
    ÜÇÜNCÜ HÂL NEDEN AYRI — ÖLÇÜLDÜ (canlı, 16.08.2026)

    Mağaza değeri niteliğin katalogda olmasına göre değil ÜRÜNÜN AİLESİNDE
    olmasına göre yazıyor: `AdminCatalogProductUpdateProcessor` gövdenin
    anahtarlarını ailenin nitelik listesiyle kesiştiriyor ve kesişimin dışında
    kalan kodu HATA DA UYARI DA ÜRETMEDEN düşürüyor. Canlıda `default` ailesi
    (id 1) kitap alanlarından yalnız `desi`yi taşıyor; `kitap` ailesi (id 2)
    dokuzunu birden. Yani "katalogda `isbn` var" sorusunun cevabı EVET olduğu
    hâlde, aile 1'deki bir ürüne ISBN yazmak sessiz kayıptır.
    """
    known = catalog or {}
    family = family_code_set(family_codes)
    out: list[dict[str, Any]] = []
    for field, candidates in FIELD_CANDIDATES.items():
        code = codes.get(field, "")
        entry = known.get(code) if isinstance(known.get(code), dict) else {}
        selectable = attribute_kind(entry) in OPTION_TYPES if entry else False
        options = [item for item in (entry or {}).get("options") or []
                   if isinstance(item, dict)]

        reason = ""
        if not code:
            reason = ("Katalogda bu nitelik yok. Aranan kodlar: "
                      + ", ".join(f"`{name}`" for name in candidates)
                      + ". Nitelikler sekmesinden açılabilir; açılmadan bu alan yazılamaz.")
        elif family is not None and code not in family:
            reason = (f"`{code}` katalogda tanımlı ama ÜRÜNÜN ÖZNİTELİK AİLESİNDE yok. "
                      "Mağaza değerleri ailenin nitelik listesine göre yazıyor: ailede "
                      "olmayan koda gönderilen değer 200 ile kabul edilir ve hiçbir yere "
                      "konmaz. Alan bu yüzden açılmadı — nitelik Aileler ekranından bu "
                      "aileye eklenirse kendiliğinden gelir.")
        elif selectable and not options:
            reason = (f"`{code}` seçenekli (select) bir nitelik ama seçenek listesi "
                      "okunamadı. Seçenek kimliği bilinmeden yazmak, mağazanın isteği "
                      "kabul edip değeri hiçbir yere koymaması demektir; alan bu yüzden "
                      "açılmadı. Bağlantı düzelince kendiliğinden gelir.")

        out.append({
            "key": field,
            "code": code,
            "label": FIELD_LABELS[field],
            "numeric": field in NUMERIC_FIELDS,
            # Panel alanı BUNA göre çiziyor: `select` ise açılır kutu, aksi
            # hâlde metin kutusu. Karar mağazadan geliyor, panelde sabit değil.
            "type": "select" if selectable else "text",
            "options": options,
            "available": bool(code) and not reason,
            "reason": reason,
        })
    return out


def view(read: Any, codes: dict[str, str]) -> dict[str, Any]:
    """Ürün kaydından kitap künyesini okur.

    `read` bir okuyucudur: `read(kod)` verilen nitelik kodunun değerini döner
    (`catalog.attribute` bunu yapıyor). Fonksiyon geçirilmesi bilinçli — bu
    dosya EAV zarfını çözmeyi bilmez ve bilmemeli.
    """
    values: dict[str, Any] = {}
    for field, code in (codes or {}).items():
        values[field] = "" if not code else _text(read(code))
    return values


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ═══════════════════════════════════════════════════════════ doğrulama

def field_error(field: str, value: Any) -> str:
    """Alan kaydedilebilir mi — kullanıcıya gösterilecek metin ("" = kabul).

    BOŞ DEĞER HER ALANDA MEŞRUDUR: künye eksik olabilir ve boş bırakmak
    "bilinmiyor" demenin doğru yoludur. Desi boşsa hesap sayfa sayısına düşer;
    sayfa sayısı da boşsa varsayılana. Zorunlu tutmak, bilmeyeni uydurmaya
    zorlardı.
    """
    text = _text(value)
    if not text:
        return ""

    if field == "pageCount":
        pages = page_count(text)
        if pages is None:
            return ("Sayfa sayısı bir sayı olmalı. Sayı olmayan değer kargo hesabında YOK "
                    "sayılır ve ürün varsayılan 1,0 desiye düşer — canlıda `page_count` "
                    "alanına “Fasikül” yazılmış bir ürün bu yüzden pahalı görünüyor.")
        if pages > MAX_PAGE_COUNT:
            return (f"Sayfa sayısı en çok {MAX_PAGE_COUNT} olabilir. Daha büyük bir sayı "
                    "neredeyse her zaman bir hane fazla yazılmış demektir ve sepete onlarca "
                    "desi ekler.")
        return ""

    if field == "desi":
        value_ = positive_float(text)
        if value_ is None:
            return ("Desi sıfırdan büyük bir sayı olmalı. Boş bırakırsanız hesap sayfa "
                    "sayısından yapılır — çoğu kitapta doğru olan budur.")
        if value_ > 300:
            return "Desi 300'den büyük olamaz; bu bir palet ölçüsüdür, kitap değil."
        return ""

    if field == "publishYear":
        try:
            year = int(text)
        except ValueError:
            return "Baskı yılı dört haneli bir sayı olmalı (örnek: 2024)."
        if not 1000 <= year <= 2999:
            return "Baskı yılı 1000–2999 aralığında olmalı."
        return ""

    if field == "isbn":
        digits = [char for char in text if char.isdigit() or char in "Xx"]
        if len(digits) not in (10, 13):
            return ("ISBN 10 ya da 13 haneli olmalı (tire ve boşluk sayılmaz). "
                    f"Girilen değerde {len(digits)} hane var.")
        return ""

    if len(text) > 180:
        return f"{FIELD_LABELS.get(field, field)} en fazla 180 karakter olabilir."
    return ""


def option_error(field: str, value: Any, options: Any) -> str:
    """Seçimli alanın değeri katalogdaki listede var mı ("" = kabul).

    BOŞ MEŞRUDUR (bkz. `field_error`): yayınevi bilinmiyor olabilir.

    Listede olmayan bir kimlik REDDEDİLİR. Bagisto bu alanı seçenek kimliğiyle
    saklıyor; tanımadığı kimliği sessizce yok sayar ve ürün yayınevsiz kalır.
    Personel formu doldurup kaydettiği için bunu fark etmesinin hiçbir yolu
    olmazdı.
    """
    text = _text(value)
    if not text:
        return ""
    known = {_text(item.get("value")): _text(item.get("label"))
             for item in options or [] if isinstance(item, dict)}
    if text in known:
        return ""
    label = FIELD_LABELS.get(field, field)
    if not known:
        return (f"{label} için katalogda seçenek tanımlı değil; değer yazılamaz. "
                "Nitelikler sekmesinden seçenek eklenebilir.")
    return (f"{label} katalogdaki listeden seçilir ve `{text}` o listede yok. "
            "Mağaza tanımadığı seçenek kimliğini sessizce yok sayar — ürün alanı "
            "boş kalırdı.")


def draft_errors(draft: Any, codes: dict[str, str],
                 catalog: dict[str, Any] | None = None,
                 family_codes: Any = None) -> dict[str, str]:
    """Formun tamamının hataları — alan → metin.

    ÇÖZÜLEMEYEN NİTELİĞE YAZMA DENEMESİ DE HATADIR. Ekran o alanı çizmiyor
    ama istek elle de kurulabilir; arayüzde gizlemek yetkilendirme değildir
    (K9) ve burada susmak, hiçbir yere yazılmayan bir "kaydedildi" üretirdi.

    Aynı gerekçe SEÇENEKLERİ OKUNAMAYAN seçimli alan ve ÜRÜNÜN AİLESİNDE
    OLMAYAN nitelik için de geçerli: ikisinde de değer mağazada hiçbir yere
    oturmaz.
    """
    form = draft if isinstance(draft, dict) else {}
    specs = {item["key"]: item for item in field_specs(codes or {}, catalog, family_codes)}
    errors: dict[str, str] = {}
    for field, value in form.items():
        if field not in FIELD_CANDIDATES:
            errors[field] = f"`{field}` bu ekranın kitap alanlarından biri değil."
            continue
        spec = specs[field]
        if not spec["code"]:
            errors[field] = (f"Katalogda `{field}` alanına karşılık gelen nitelik yok; "
                             "yazılsaydı mağaza isteği kabul eder ama değeri hiçbir yere "
                             "koymazdı.")
            continue
        if not spec["available"]:
            errors[field] = spec["reason"]
            continue
        problem = (option_error(field, value, spec["options"])
                   if spec["type"] == "select" else field_error(field, value))
        if problem:
            errors[field] = problem
    return errors


def canonical_number(field: str, value: Any) -> str:
    """Sayısal künye alanını MAĞAZANIN OKUYABİLECEĞİ metne indirger.

    ═══════════════════════════════════════════════════════════════════════════
    NEDEN GEREKLİ — VİRGÜL SESSİZCE PARAYA DOKUNUYOR

    Bu dosyanın okuyucuları Türkçe yazımı bilerek kabul ediyor
    (`positive_float`/`page_count` virgülü noktaya çeviriyor), yani `1,2`
    ekranda 1,2 desi olarak DOĞRU görünüyor ve doğrulamadan geçiyor. Ama
    mağaza tarafı aynı değeri PHP `is_numeric()` ile okuyor
    (`DesiCalculator::positiveFloat`) ve PHP ondalık ayracı olarak yalnız
    noktayı tanır: `is_numeric("1,2")` FALSE'tur. Ham yazılan `1,2` mağazada
    HİÇ okunmaz, hesap sayfa sayısına ya da varsayılan 1,0 desiye düşer —
    ekran "2 desi" derken müşteriden 1 desi tahsil edilir.

    Çözüm doğrulayıcıyı katılaştırmak DEĞİL (kullanıcıyı Türkçe yazımdan
    caydırırdı), yazarken kanonikleştirmek: doğrulayıcı ile yazıcı aynı kabulü
    paylaşır, mağaza ise her zaman nokta ondalıklı bir sayı görür.

    OKUNAMAYAN DEĞER OLDUĞU GİBİ BIRAKILIR: kapı `draft_errors`tır ve zaten
    reddeder; burada uydurma bir sayı üretmek, kullanıcının yazdığından başka
    bir şeyi kaydetmek olurdu.
    """
    text = _text(value)
    if not text:
        return ""
    if field == "desi":
        number = positive_float(text)
        if number is None:
            return text
        # Dört ondalık desi için fazlasıyla yeter: mağaza ücretlendirmede
        # küsuratı zaten YUKARI yuvarlıyor (`billed_desi`).
        short = f"{number:.4f}".rstrip("0").rstrip(".")
        # SIFIRA İNEN ÖLÇÜM YAZILMAZ. `0,00001` gibi bir değer dört ondalıkta
        # `0`a düşer ve mağaza `0`ı "ölçüm yok" sayar (`positiveFloat` sıfırı
        # eler): ölçüm sessizce SİLİNİR ve hesap sayfa sayısına döner. Böyle
        # bir değerde tam gösterim kullanılır — PHP `is_numeric` üstel yazımı
        # da kabul ediyor.
        return short if positive_float(short) is not None else repr(number)
    if field == "pageCount":
        pages = page_count(text)
        return text if pages is None else str(pages)
    if field == "publishYear":
        try:
            return str(int(float(text.replace(",", "."))))
        except (TypeError, ValueError):
            return text
    return text


def patch_for(draft: Any, codes: dict[str, str],
              catalog: dict[str, Any] | None = None,
              family_codes: Any = None) -> dict[str, Any]:
    """Ekran taslağı → Bagisto nitelik kodlarıyla yama.

    BOŞ DEĞER YAZILIR ve bu bilinçli: kitap künyesinde boş bırakmak
    "bilinmiyor" demektir ve yanlış girilmiş bir ISBN'i temizlemenin başka
    yolu yoktur. Sırlarda geçerli olan "boş = dokunma" kuralı burada geçerli
    DEĞİLDİR — burada silinen bir şey geri yazılabilir.

    SEÇİMLİ ALAN SAYIYA ÇEVRİLİR: mağaza seçenek kimliğini tamsayı bekliyor,
    panel ise `<select>` kutusundan metin gönderiyor.

    SAYISAL ALAN KANONİKLEŞTİRİLİR: `1,2` gibi Türkçe yazım mağazada hiç
    okunmuyor (bkz. `canonical_number`).

    ÜRÜNÜN AİLESİNDE OLMAYAN KOD YAMAYA GİRMEZ — `draft_errors` ile aynı kapı.
    Girseydi gövdeye konur, mağaza sessizce düşürür ve denetim satırında
    "yazıldı" diye görünürdü.

    DEĞER BURAYA DOĞRULANMIŞ GELİR (`draft_errors` kapıdır). Yine de sayıya
    çevrilemeyen bir kimlik gelirse alan BOŞALTILIR: uydurma bir kimlik
    yazmak, ürüne başka bir yayınevi yazmak demek olurdu.
    """
    form = draft if isinstance(draft, dict) else {}
    specs = {item["key"]: item for item in field_specs(codes or {}, catalog, family_codes)}
    family = family_code_set(family_codes)
    patch: dict[str, Any] = {}
    for field, value in form.items():
        code = (codes or {}).get(field, "")
        if not code or field not in FIELD_CANDIDATES:
            continue
        if family is not None and code not in family:
            continue
        text = _text(value)
        if specs[field]["type"] == "select":
            patch[code] = int(text) if text.isdigit() else ""
            continue
        patch[code] = canonical_number(field, text) if field in NUMERIC_FIELDS else text
    return patch


# ═══════════════════════════════════════════════ toplu işlem fark tablosu

def bulk_rows(rows: Any, *, field: str, mode: str, amount: Any) -> list[dict[str, Any]]:
    """Toplu sayfa sayısı / desi yazma fark tablosu.

    `mode`:
      · `set`   — mutlak değer yazılır.
      · `clear` — alan BOŞALTILIR. Desi için asıl kullanışlı olan budur:
                  yanlışlıkla girilmiş bir ölçüm hesabı ezmeye devam eder ve
                  onu kaldırmanın tek yolu boş yazmaktır.

    `skipped` satırlar UYGULANMAZ ama tablodan DÜŞÜRÜLMEZ: "bu ürün neden
    değişmedi" sorusunun cevabı, satırın hiç görünmemesinden iyidir.
    """
    if field not in ("pageCount", "desi"):
        raise ValueError(f"Toplu kitap yazması yalnız sayfa sayısı ve desi için: {field}")
    if mode not in ("set", "clear"):
        raise ValueError(f"Bilinmeyen kip: {mode}")

    # KANONİKLEŞTİRME BURADA DA GEREKLİ: toplu uygulama satırın `value`
    # alanını doğrudan gövdeye koyuyor (`patch_for`den geçmiyor), yani `0,45`
    # yazan bir toplu iş mağazada okunamayan bir desi bırakırdı. Fark tablosu
    # da yazılacak GERÇEK değeri göstermelidir; kullanıcı önizlemede `0,45`
    # görüp üründe `0.45` bulmamalı.
    target = "" if mode == "clear" else canonical_number(field, amount)
    out: list[dict[str, Any]] = []
    for row in rows or []:
        before = _text((row.get("book") or {}).get(field))
        note = ""
        skipped = before == target
        if skipped:
            note = "Zaten bu değerde."
        elif mode == "set":
            problem = field_error(field, target)
            if problem:
                skipped = True
                note = problem
        out.append({
            "id": row.get("id"), "sku": row.get("sku"), "name": row.get("name"),
            "before": before or "—", "after": target or "—",
            # `delta` sayı değil BAYRAKTIR: `diff_summary` "değişen satır"
            # sayısını bununla buluyor ve metin alanında artış/azalış diye bir
            # şey yok. 0/1 kullanmak, özet fonksiyonunu kitap alanları için
            # ikinci kez yazmaktan iyi.
            "delta": 0 if skipped else 1,
            "skipped": skipped, "note": note,
            "value": target,
        })
    return out
