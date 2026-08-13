"""Katalog sağlığı kuralları — saf mantık. Ağa çıkmaz, DB kullanmaz."""

from __future__ import annotations

from typing import Any

from store_ai_backend import rules
from store_ai_fakes import product

OPTIONS = {
    "min_description_chars": 120,
    "meta_title_max": 60,
    "meta_description_min": 70,
    "meta_description_max": 160,
    "price_outlier_factor": 5,
}


def _facts(*raw: dict[str, Any]) -> list[dict[str, Any]]:
    return [rules.product_facts(item) for item in raw]


def _kinds(findings: list[dict[str, Any]], target_id: int) -> set[str]:
    return {item["kind"] for item in findings if item["targetId"] == target_id}


# ============================================================== temel okuma

def test_aciklama_uzunlugu_html_etiketleri_sayilmadan_olculur() -> None:
    # `<p>&nbsp;</p>` on beş karakterdir; ham uzunluğa bakan bir kural onu
    # "dolu açıklama" sayar ve kataloğun yarısındaki boşluğu gizler.
    assert rules.strip_html("<p>&nbsp;</p>") == ""
    assert rules.strip_html("<p>Kalem</p>") == "Kalem"


def test_eav_zarfindaki_alan_da_bulunur() -> None:
    raw = {"id": 1, "values": {"common": {"name": "Kalem", "sku": "K-1"}}}
    facts = rules.product_facts(raw)
    assert facts["name"] == "Kalem"
    assert facts["sku"] == "K-1"


def test_fiyat_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    assert rules.to_kurus("1234.35") == 123435
    assert rules.to_kurus("1.250,00") == 125000
    assert rules.to_kurus("") is None


# ================================================================= bulgular

def test_gorseli_olmayan_urun_agir_bulgu_uretir() -> None:
    findings = rules.scan_products(_facts(product(1, images=[])), options=OPTIONS)
    hit = next(item for item in findings if item["kind"] == rules.MISSING_IMAGE)
    assert hit["severity"] == "high"
    assert hit["id"] == f"{rules.MISSING_IMAGE}:1"


def test_kisa_aciklama_yetersiz_sayilir_saglam_aciklama_sayilmaz() -> None:
    kisa = rules.scan_products(_facts(product(1, description="<p>Kalem.</p>")), options=OPTIONS)
    assert rules.MISSING_DESCRIPTION in _kinds(kisa, 1)
    saglam = rules.scan_products(_facts(product(2)), options=OPTIONS)
    assert rules.MISSING_DESCRIPTION not in _kinds(saglam, 2)


def test_kategorisiz_urun_bulgu_uretir() -> None:
    findings = rules.scan_products(_facts(product(1, categories=[])), options=OPTIONS)
    assert rules.MISSING_CATEGORY in _kinds(findings, 1)


def test_barkod_katalogda_hic_tanimli_degilse_kural_kendiliginden_kapanir() -> None:
    # TUZAK 2: alan hiç yoksa "barkodu eksik" bulgusu 1.419 satırlık anlamsız
    # bir kırmızı duvar üretir ve kullanıcı ekrana bir daha bakmaz.
    facts = _facts(product(1), product(2))
    assert rules.barcode_defined(facts) is False
    findings = rules.scan_products(facts, options=OPTIONS)
    assert not [item for item in findings if item["kind"] == rules.MISSING_BARCODE]


def test_barkod_alani_varken_bos_olan_urun_bulgu_uretir() -> None:
    facts = _facts(product(1, barcode="8690000000001"), product(2, barcode=""))
    assert rules.barcode_defined(facts) is True
    findings = rules.scan_products(facts, options=OPTIONS)
    assert rules.MISSING_BARCODE in _kinds(findings, 2)
    assert rules.MISSING_BARCODE not in _kinds(findings, 1)


def test_seo_sorunlarinin_hepsi_tek_bulguda_toplanir() -> None:
    findings = rules.scan_products(
        _facts(product(1, meta_title="", meta_description="kısa")), options=OPTIONS)
    hit = next(item for item in findings if item["kind"] == rules.SEO_BROKEN)
    assert "meta başlık boş" in hit["detail"]
    assert "meta açıklama" in hit["detail"]


def test_cift_url_anahtari_her_iki_urunde_de_isaretlenir() -> None:
    findings = rules.scan_products(
        _facts(product(1, url_key="kalem"), product(2, url_key="Kalem")), options=OPTIONS)
    hits = [item for item in findings if item["kind"] == rules.DUPLICATE_URL_KEY]
    assert {item["targetId"] for item in hits} == {1, 2}


def test_pasif_urun_varsayilan_olarak_denetlenmez() -> None:
    facts = _facts(product(1, status=0, images=[]))
    assert rules.scan_products(facts, options=OPTIONS) == []
    assert rules.scan_products(facts, options=OPTIONS, include_passive=True)


# ============================================================ fiyat kuralları

def test_varyantli_urunun_bos_fiyati_bulgu_degildir() -> None:
    # TUZAK 3: configurable ürünün fiyatı varyantlarındadır; buradan bakmak
    # tüm varyantlı ürünleri "fiyatı yok" gösterirdi.
    findings = rules.scan_products(
        _facts(product(1, type="configurable", price="")), options=OPTIONS)
    assert rules.PRICE_ANOMALY not in _kinds(findings, 1)


def test_satilabilir_urunun_sifir_fiyati_bulgu_uretir() -> None:
    findings = rules.scan_products(_facts(product(1, price="0")), options=OPTIONS)
    hit = next(item for item in findings if item["kind"] == rules.PRICE_ANOMALY)
    assert "boş ya da sıfır" in hit["detail"]


def test_indirmeyen_indirim_yakalanir() -> None:
    findings = rules.scan_products(
        _facts(product(1, price="100.00", special_price="120.00")), options=OPTIONS)
    hit = next(item for item in findings if item["kind"] == rules.PRICE_ANOMALY)
    assert "indirim indirmiyor" in hit["detail"]


def test_maliyeti_satis_fiyatinin_ustunde_olan_urun_yakalanir() -> None:
    findings = rules.scan_products(
        _facts(product(1, price="100.00", cost="150.00")), options=OPTIONS)
    hit = next(item for item in findings if item["kind"] == rules.PRICE_ANOMALY)
    assert "Maliyet" in hit["detail"]


def test_medyan_ortalama_degildir_tek_pahali_urun_denetimi_koreltmez() -> None:
    # TUZAK 4: tek bir 9.000 ₺'lik set, ortalamayı yukarı çekip virgülü kaymış
    # ürünü "normal" gösterirdi.
    rows = [product(index, price="100.00") for index in range(1, 6)]
    rows.append(product(9, price="9000.00"))
    facts = _facts(*rows)
    assert rules.category_medians(facts)[7] == 10000
    findings = rules.scan_products(facts, options=OPTIONS)
    assert rules.PRICE_ANOMALY in _kinds(findings, 9)


def test_az_urunlu_kategoride_fiyat_anormalligi_aranmaz() -> None:
    facts = _facts(product(1, price="100.00"), product(2, price="9000.00"))
    assert rules.category_medians(facts) == {}
    findings = rules.scan_products(facts, options=OPTIONS)
    assert rules.PRICE_ANOMALY not in _kinds(findings, 2)


# ============================================================= kategori/özet

def test_urunu_olmayan_kategori_bulgu_uretir_kok_atlanir() -> None:
    facts = _facts(product(1))
    categories = [{"id": 1, "name": "Kök", "root": True},
                  {"id": 7, "name": "Kitap"}, {"id": 8, "name": "Boş raf"}]
    findings = rules.empty_categories(facts, categories)
    assert [item["targetId"] for item in findings] == [8]
    assert findings[0]["targetType"] == "category"


def test_susturulan_bulgu_listeden_dusrulur_digerleri_kalir() -> None:
    # TUZAK 6: susturma bulgu kimliğine göredir, ürün kimliğine göre değil;
    # aynı ürünün başka bulgusu görünmeye devam eder.
    findings = rules.scan_products(_facts(product(1, images=[], categories=[])),
                                   options=OPTIONS)
    kalan = rules.drop_ignored(findings, {f"{rules.MISSING_IMAGE}:1"})
    assert rules.MISSING_IMAGE not in _kinds(kalan, 1)
    assert rules.MISSING_CATEGORY in _kinds(kalan, 1)


def test_siralama_agir_bulguyu_uste_alir_ve_kararlidir() -> None:
    findings = rules.scan_products(
        _facts(product(1, images=[], meta_title="")), options=OPTIONS)
    sirali = rules.sort_findings(findings)
    assert sirali[0]["severity"] == "high"
    assert rules.sort_findings(list(reversed(findings))) == sirali


def test_ozet_kac_kaydin_etkilendigini_sayar() -> None:
    findings = rules.scan_products(
        _facts(product(1, images=[], categories=[]), product(2)), options=OPTIONS)
    summary = rules.summarize(findings, scanned=2)
    assert summary["affected"] == 1
    assert summary["clean"] == 1
    assert summary["bySeverity"]["high"] == 2
