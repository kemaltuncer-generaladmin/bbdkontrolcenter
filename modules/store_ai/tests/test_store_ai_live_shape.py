"""CANLI GÖVDE BİÇİMİ — mağazanın gerçekten döndürdüğü veriye karşı denetim.

İçindeki sözlükler `GET /api/admin/catalog/products`,
`/catalog/products/1418` ve `/catalog/categories/tree` yanıtlarından
(bbdstore.com.tr, 2026-08-13, salt okuma) kısaltılarak alınmıştır; alan adları
ve değer tipleri olduğu gibidir.

NEDEN AYRI DOSYA. Modülün kendi uydurduğu snake_case kayda karşı geçen test
hiçbir şey kanıtlamıyordu: `test_store_ai_rules.py` yemyeşilken ekran canlıda
1.421 ürünün TAMAMINA "görseli yok" + "kategorisiz" + "URL anahtarı boş"
diyordu ve gerçek bulgu sayısı sıfırdı. Hata sessizdi — `url_key` diye bakan
kod istisna atmaz, alanı boş bulur.

Ağa çıkılmaz; buradaki sözlükler sabittir.
"""

from __future__ import annotations

from typing import Any

from store_ai_backend import rules
from store_ai_fakes import live_product

OPTIONS = {
    "min_description_chars": 120,
    "meta_title_max": 60,
    "meta_description_min": 70,
    "meta_description_max": 160,
    "price_outlier_factor": 5,
}

#: `GET /api/admin/catalog/products?per_page=1&locale=tr&channel=default`
#: yanıtındaki tek kayıt (1428 numaralı TEST ürünü), kısaltılmış.
LIVE_ROW: dict[str, Any] = {
    "id": 1428,
    "sku": "123456789123456789",
    "name": "TEST ÜRÜNÜDÜR",
    "type": "simple",
    "status": 1,
    "price": "2.0000",
    "formattedPrice": "₺2,00",
    "specialPrice": "1.0000",
    "quantity": 94,
    "baseImageUrl": "https://bbdstore.com.tr/storage/product/1428/test-urunu.png",
    "imagesCount": 1,
    "categoryId": None,
    "categoryName": None,
    "channel": "default",
    "locale": "tr",
    "urlKey": "test-urunudur",
    "shortDescription": "<p>TEST ÜRÜNÜDÜR</p>",
    "description": "<p>TEST ÜRÜNÜDÜR</p>",
    "metaTitle": "",
    "metaDescription": "",
    "createdAt": "2026-07-20 21:52:57",
    "updatedAt": "2026-08-02 11:38:16",
    "images": None,
    "categories": None,
    "variants": None,
}

#: `GET /api/admin/catalog/products/1418` — DETAY ucu. Liste ucunun aksine
#: `images` ve `categories` gerçek liste; ürün DÖRT kategoride.
LIVE_DETAIL: dict[str, Any] = {
    "id": 1418,
    "sku": "BBD2026SKU1411",
    "name": "Yıldızlar Yarışıyor TYT Deneme",
    "type": "simple",
    "status": 1,
    "price": "299.0000",
    "urlKey": "yildizlar-yars-tyt-deneme-32li-ucgenler-25-26",
    "metaTitle": "Yıldızlar Yarışıyor TYT AYT Üçgenler 32'li Deneme | BBD Store",
    "metaDescription": "",
    "shortDescription": "<p>Kısa tanıtım.</p>",
    "description": "<p>Uzun açıklama.</p>",
    "images": [{"id": 1419, "path": "product/1418/a.webp", "sortOrder": 0}],
    "categories": [{"id": 2, "name": "TYT"}, {"id": 4, "name": "Matematik"},
                   {"id": 7, "name": "AYT"}, {"id": 92, "name": "YY YAYINLARI"}],
    "updatedAt": "2026-07-20T21:52:57+03:00",
}

#: `GET /api/admin/catalog/categories/tree` — `parentId`, `parent_id` DEĞİL.
LIVE_TREE: list[dict[str, Any]] = [{
    "id": 1, "name": "Kök", "slug": "root", "status": 1, "parentId": None,
    "children": [{
        "id": 27, "name": "Sınava Göre", "slug": "sinava-gore", "parentId": 1,
        "children": [
            {"id": 2, "name": "TYT", "slug": "tyt", "parentId": 27, "children": []},
            {"id": 4, "name": "Matematik", "slug": "matematik", "parentId": 2, "children": []},
        ],
    }],
}]


# ======================================================== camelCase okunuyor mu

def test_canli_kayittaki_camelcase_alanlarin_hepsi_okunur() -> None:
    # TUZAK 0: her biri ayrı bir kuralı besliyor. Biri okunamazsa o kural
    # kataloğun TAMAMINDA tetiklenir ve hiçbir istisna atılmaz.
    facts = rules.product_facts(LIVE_ROW)
    assert facts["urlKey"] == "test-urunudur"
    assert facts["price"] == 200                      # "2.0000" → 200 kuruş
    assert facts["specialPrice"] == 100
    assert facts["shortDescription"] == "TEST ÜRÜNÜDÜR"
    assert facts["updatedAt"] == "2026-08-02 11:38:16"


def test_liste_ucu_gorselleri_null_verir_sayi_imagescount_alanindadir() -> None:
    # `images: null` + `imagesCount: 1`. Yalnız `images` listesine bakan sürüm
    # 1.421 ürünün hepsine "Görsel yok" (AĞIR) bulgusu üretiyordu.
    assert rules.image_count(LIVE_ROW) == 1
    findings = rules.scan_products([rules.product_facts(LIVE_ROW)], options=OPTIONS)
    assert rules.MISSING_IMAGE not in {item["kind"] for item in findings}


def test_gorseli_gercekten_olmayan_urun_yine_yakalanir() -> None:
    # Düzeltme kuralı köreltmemeli: kapak da sayı da yoksa bulgu ÇIKMALI.
    row = live_product(3, imagesCount=0, baseImageUrl=None)
    findings = rules.scan_products([rules.product_facts(row)], options=OPTIONS)
    assert rules.MISSING_IMAGE in {item["kind"] for item in findings}


def test_liste_ucunda_kategori_categoryid_alanindan_okunur() -> None:
    # `categories: null` + `categoryId: 2`. Okunmazsa her ürün "Kategorisiz".
    assert rules.category_ids(live_product(9)) == [2]
    findings = rules.scan_products([rules.product_facts(live_product(9))], options=OPTIONS)
    assert rules.MISSING_CATEGORY not in {item["kind"] for item in findings}


def test_canli_kayitta_seo_bulgusu_gercek_eksiklikten_cikar() -> None:
    # 1428 numaralı üründe meta başlık ve meta açıklama GERÇEKTEN boş; bulgu
    # çıkmalı ama gerekçesi "urlKey boş" OLMAMALI — o alan dolu.
    findings = rules.scan_products([rules.product_facts(LIVE_ROW)], options=OPTIONS)
    seo = next(item for item in findings if item["kind"] == rules.SEO_BROKEN)
    assert "meta başlık boş" in seo["detail"]
    assert "URL anahtarı boş" not in seo["detail"]


def test_saglam_canli_urun_hic_bulgu_uretmez() -> None:
    # EN ÖNEMLİ SINAV: eksiksiz bir canlı kayıt TEK bir bulgu bile üretmemeli.
    # Snake_case okuyan sürüm bu kayda dört bulgu birden veriyordu.
    findings = rules.scan_products([rules.product_facts(live_product(11))], options=OPTIONS)
    assert findings == []


def test_cift_url_anahtari_canli_yazimda_da_yakalanir() -> None:
    # `urlKey` okunamadığında hepsi boş çıkıyor ve boş anahtar atlandığı için
    # bu kural HİÇ tetiklenmiyordu — sessizce ölü bir kural.
    facts = [rules.product_facts(live_product(1, urlKey="kalem")),
             rules.product_facts(live_product(2, urlKey="Kalem"))]
    hits = [item for item in rules.scan_products(facts, options=OPTIONS)
            if item["kind"] == rules.DUPLICATE_URL_KEY]
    assert {item["targetId"] for item in hits} == {1, 2}


def test_indirmeyen_indirim_canli_yazimda_yakalanir() -> None:
    facts = [rules.product_facts(live_product(1, price="100.0000", specialPrice="120.0000"))]
    hit = next(item for item in rules.scan_products(facts, options=OPTIONS)
               if item["kind"] == rules.PRICE_ANOMALY)
    assert "indirim indirmiyor" in hit["detail"]


# ================================================ eksik veriyle kural kapanır

def test_liste_ucu_yalniz_birincil_kategoriyi_verir() -> None:
    # Detayda dört kategori var, listede bir tane. Eksikliğin kendisi
    # işaretlenmeli; işaretlenmezse boş kategori denetimi sessizce yalan söyler.
    assert rules.category_partial(LIVE_ROW) is True
    assert rules.category_partial(LIVE_DETAIL) is False
    assert rules.category_ids(LIVE_DETAIL) == [2, 4, 7, 92]
    assert rules.category_data_partial([rules.product_facts(live_product(1))]) is True


def test_detay_kaydinda_kategori_bilgisi_tamdir() -> None:
    facts = rules.product_facts(LIVE_DETAIL)
    assert facts["categoryIds"] == [2, 4, 7, 92]
    assert facts["categoryPartial"] is False


# ===================================================== kategori ağacı yazımı

def test_kategori_agaci_parentid_yazimiyla_duzlestirilir() -> None:
    flat = rules.flatten_categories(LIVE_TREE)
    by_id = {node["id"]: node for node in flat}
    assert by_id[1]["root"] is True                # kök: parentId yok
    assert by_id[27]["root"] is False
    assert by_id[2]["name"] == "TYT"
    assert len(flat) == 4


def test_agactan_gelen_kategoriler_bos_kategori_kuralini_besler() -> None:
    # Kategori bilgisi TAM olduğunda (detay kaydı) kural yine çalışmalı:
    # 4 numaralı kategoride ürün var, sahte 99 numaralıda yok.
    facts = [rules.product_facts(LIVE_DETAIL)]
    categories = rules.flatten_categories(LIVE_TREE) + [{"id": 99, "name": "Boş raf"}]
    findings = rules.empty_categories(facts, categories)
    assert {item["targetId"] for item in findings} == {27, 99}


# ============================================================ para ve zaman

def test_dort_ondalikli_fiyat_kurusa_tam_cevrilir() -> None:
    # Bagisto fiyatı dört ondalıkla veriyor ("299.0000"); float ile çarpmak
    # bazı değerlerde bir kuruş aşağı yuvarlardı.
    assert rules.to_kurus("299.0000") == 29900
    assert rules.to_kurus("1234.3500") == 123435
    assert rules.to_kurus("2") == 200


def test_saat_dilimsiz_zaman_damgasi_oldugu_gibi_tasinir() -> None:
    # "2026-08-02 11:38:16" YERELdir; UTC sayıp kaydırmak yanlış olurdu.
    assert rules.product_facts(LIVE_ROW)["updatedAt"] == "2026-08-02 11:38:16"


# =================================================== iki yazım da desteklenir

def test_snake_case_kayit_da_okunmaya_devam_eder() -> None:
    # Geriye dönük: BBD uçları ya da EAV zarfı snake_case verirse kırılmamalı.
    raw = {"id": 5, "url_key": "kalem", "meta_title": "Başlık",
           "special_price": "1.00", "images": [{"id": 1}], "categories": [{"id": 3}]}
    facts = rules.product_facts(raw)
    assert facts["urlKey"] == "kalem"
    assert facts["metaTitle"] == "Başlık"
    assert facts["specialPrice"] == 100
    assert facts["categoryIds"] == [3]


def test_dolu_yazim_bos_yazima_yeglenir() -> None:
    # Aynı kayıtta iki yazım birden varsa DOLU olan kazanır; yoksa ekran
    # doğru veriyi yok sayıp boş alanı gösterirdi.
    raw = {"id": 5, "metaTitle": "Gerçek başlık", "meta_title": ""}
    assert rules.product_facts(raw)["metaTitle"] == "Gerçek başlık"


def test_yazim_cozucu_iki_yonlu_calisir() -> None:
    assert rules.spellings("url_key") == ("url_key", "urlKey")
    assert rules.spellings("urlKey") == ("urlKey", "url_key")
    assert rules.spellings("sku") == ("sku",)
