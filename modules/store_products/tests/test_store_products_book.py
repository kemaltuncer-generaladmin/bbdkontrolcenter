"""Kitap künyesi ve desi hesabı — saf kurallar + servis yolu.

BU DOSYADAKİ TESTLERİN ASIL İŞİ BİR EŞİTLİĞİ KİLİTLEMEK: ekranın gösterdiği
desi ile mağazanın hesapladığı desi AYNI olmak zorunda. Rakam müşterinin
checkout'ta ödeyeceği kargo ücretinin girdisi; ayrışırsa personel bir sayı
görüp başka bir sayıyla satış yapar.

Katsayılar mağaza tarafındaki `Bbd\\Shipping\\Support\\BookDimensions` ile
birebir aynıdır ve burada AÇIKÇA yazılı beklenen değerlerle sabitlenir — biri
değişirse test kırılır ve iki tarafın ayrıştığı o an görülür.

AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, CANLIYA YAZMAZ.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from store_products_backend import book
from store_products_backend.service import ProductsService
from store_products_fakes import FakeApi, FakeLog, FakeStore

#: Canlıda ölçülen nitelik kodları. `page_count`, `isbn` ve `desi` mağazanın
#: kargo hesabının okuduğu adlardır; kalan üçü aday listeden çözülür.
NITELIKLER = [
    {"id": 1, "code": "page_count", "type": "text", "adminName": "Sayfa sayısı"},
    {"id": 2, "code": "isbn", "type": "text", "adminName": "ISBN"},
    {"id": 3, "code": "desi", "type": "text", "adminName": "Desi"},
    {"id": 4, "code": "yayinevi", "type": "text", "adminName": "Yayınevi"},
    {"id": 5, "code": "yazar", "type": "text", "adminName": "Yazar"},
    {"id": 6, "code": "baski_yili", "type": "text", "adminName": "Baskı yılı"},
]

KITAP = {
    "id": 7, "sku": "BBD-176", "type": "simple", "status": 1, "name": "Deneme Föyü",
    "url_key": "deneme-foyu", "price": "120.00", "page_count": "176", "isbn": "9786051234567",
    "yayinevi": "BBD", "yazar": "Komisyon", "baski_yili": "2024",
}


def _service(api: FakeApi | None = None, **config: Any) -> tuple[ProductsService, FakeApi]:
    api = api or FakeApi({7: dict(KITAP)})
    api.attributes_payload = {"items": list(NITELIKLER), "meta": {}}
    service = ProductsService(
        api=api, store=FakeStore(), log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api


# ═══════════════════════════════════════════════ katsayılar ve formül

def test_katsayilar_magaza_tarafiyla_ayni_kalir() -> None:
    """Sabitler `BookDimensions` ile BİREBİR aynıdır.

    Bu test bir "kod okuma" testi değil bir KİLİTTİR: katsayı üç yerde yaşıyor
    (mağazadaki PHP, buradaki Python, panelin `desiRules` ile aldığı kopya) ve
    birinin sessizce kayması, müşteriden alınan ücretle Geliver'a beyan edilen
    desinin tutmaması demek.
    """
    assert book.DESI_DIVISOR == 3000
    assert book.PAGE_THICKNESS_MM == 0.04375
    assert book.COVER_THICKNESS_MM == 1.0
    assert book.TRIM_WIDTH_CM == 19.5
    assert book.TRIM_HEIGHT_CM == 27.5
    assert book.PACKAGING_MARGIN_CM == 1.0
    assert book.MAX_PAGE_COUNT == 5000
    assert book.DEFAULT_DESI == 1.0


def test_taban_alani_ambalaj_payiyla_hesaplanir() -> None:
    # (19,5 + 2×1) × (27,5 + 2×1) = 21,5 × 29,5
    assert book.footprint_cm2() == 634.25


def test_kalinlik_sayfadan_cikar_kapak_payi_kitap_basinadir() -> None:
    # (176 × 0,04375 mm + 1,0 mm) / 10 = (7,7 + 1,0) / 10 = 0,87 cm
    assert round(book.thickness_cm_for_pages(176), 6) == 0.87
    # Sayfasız bir kayıt yalnız kapak kalınlığındadır — sıfır DEĞİL.
    assert round(book.thickness_cm_for_pages(0), 6) == 0.1


def test_desi_zinciri_ucundan_ucuna() -> None:
    """176 sayfa → 0,87 cm → 634,25 cm² × 0,87 / 3000."""
    beklenen = 634.25 * 0.87 / 3000
    assert math.isclose(book.desi_for_pages(176), beklenen, rel_tol=1e-12)
    # Gerçek bir kitap 1 desinin çok altında; varsayılan bilerek cömert.
    assert book.desi_for_pages(176) < 0.2


def test_desiden_kalinliga_donus_ayni_noktaya_gelir() -> None:
    """Elle `desi` girilen üründe kalınlık raporlanabilmeli."""
    ileri = book.desi_for_pages(300)
    assert math.isclose(book.thickness_cm_for_desi(ileri),
                        book.thickness_cm_for_pages(300), rel_tol=1e-12)


def test_urun_basina_yuvarlanmaz_yuvarlama_pakette_bir_kez_olur() -> None:
    """5 ince kitap 5 desi ETMEZ; hepsi tek kolide 1 desi tutar."""
    birim = book.desi_for_pages(176)
    assert birim < 1.0
    assert book.billed_desi(birim, 5) == 1
    # Kalın kitap yığını gerçekten büyür; tavan yukarı yuvarlanır.
    assert book.billed_desi(book.desi_for_pages(1200), 10) == 12


def test_kargo_istenen_sepette_sonuc_asla_sifir_olamaz() -> None:
    assert book.billed_desi(0.0001, 1) == 1


# ═══════════════════════════════════════════════ öncelik sırası (üç basamak)

def test_elle_girilen_desi_hesabi_ezer() -> None:
    """Ölçüm modeli yener: paketi eline alıp ölçen kişinin kararı üstündür."""
    view = book.explain(desi_value="0.45", pages="176")
    assert view["source"] == book.SOURCE_ATTRIBUTE
    assert view["unitDesi"] == 0.45
    # Sayfa sayısı yine RAPORLANIR; yalnız hesaba girmez.
    assert view["pageCount"] == 176


def test_desi_yoksa_sayfa_sayisindan_hesaplanir() -> None:
    view = book.explain(desi_value="", pages="176")
    assert view["source"] == book.SOURCE_PAGE_COUNT
    assert math.isclose(view["unitDesi"], round(book.desi_for_pages(176), 4), rel_tol=1e-9)


def test_ikisi_de_yoksa_varsayilan_comerttir() -> None:
    view = book.explain(desi_value=None, pages=None)
    assert view["source"] == book.SOURCE_FALLBACK
    assert view["unitDesi"] == 1.0


def test_sayiya_cevrilemeyen_sayfa_sayisi_sifir_sayilmaz_yok_sayilir() -> None:
    """Canlıda `page_count` alanı "Fasikül" olan bir ürün var.

    0'a çevirip kitabı kapak kalınlığına indirmek sessiz bir hata olurdu:
    ürün gerçekte ne kadar yer kaplarsa kaplasın 0,02 desi görünürdü.
    """
    view = book.explain(pages="Fasikül")
    assert view["source"] == book.SOURCE_FALLBACK
    assert view["unitDesi"] == 1.0
    assert book.page_count("Fasikül") is None
    assert book.page_count("0") is None


def test_sanal_ve_indirilebilir_urun_kargoya_girmez() -> None:
    for kind in ("virtual", "downloadable"):
        view = book.explain(pages="176", kind=kind)
        assert view["source"] == book.SOURCE_NOT_SHIPPABLE
        assert view["billed"] == 0


def test_tahsilat_ve_uyelik_kalemleri_desi_saymaz() -> None:
    """Tip kontrolüne takılmasalar bile SKU listesi ikinci kapıdır."""
    for sku in book.NON_SHIPPING_SKUS:
        assert book.explain(pages="500", kind="simple", sku=sku)["billed"] == 0


def test_sayfa_tavani_varsayilana_dusurmez_tavanda_hesaplar() -> None:
    """Tavana çarpan ürün sessizce 1 desiye inmemeli — kalın bir set ucuzlardı."""
    view = book.explain(pages="9000")
    assert view["source"] == book.SOURCE_PAGE_COUNT
    assert view["capped"] is True
    assert math.isclose(view["unitDesi"], round(book.desi_for_pages(book.MAX_PAGE_COUNT), 4),
                        rel_tol=1e-9)


# ═══════════════════════════════════════════════ nitelik kodu çözümü

def test_kodlar_katalogdan_cozulur_uydurulmaz() -> None:
    codes = book.resolve_codes(NITELIKLER)
    assert codes["pageCount"] == "page_count"
    assert codes["publisher"] == "yayinevi"
    assert codes["publishYear"] == "baski_yili"


def test_sayfa_ve_desi_es_anlamli_kod_kabul_etmez() -> None:
    """Kargo hesabı bu ikisini ADIYLA okuyor.

    `sayfa_sayisi` diye bir nitelik düzenlenebilseydi personel sayfa sayısını
    "güncellemiş" olurdu ama kargo ücreti hiç değişmezdi — ürün varsayılan
    1,0 desiden ücretlendirilmeye devam ederdi ve kimse fark etmezdi.
    """
    codes = book.resolve_codes([{"code": "sayfa_sayisi"}, {"code": "desi_degeri"}])
    assert codes["pageCount"] == ""
    assert codes["desi"] == ""


def test_cozulemeyen_alan_ekranda_acilmaz_ve_nedeni_yazilir() -> None:
    """Var olmayan bir koda yazmak sessiz veri kaybıdır: Bagisto tanımadığı
    özniteliği yok sayar, istek 200 döner, personel "kaydettim" sanır."""
    codes = book.resolve_codes([{"code": "page_count"}])
    assert codes["publisher"] == ""
    specs = {item["key"]: item for item in book.field_specs(codes)}
    assert specs["publisher"]["available"] is False
    assert "yayinevi" in specs["publisher"]["reason"]
    # Alan listeden DÜŞÜRÜLMEZ: "neden yok" sorusunun cevabı ekranda durur.
    assert len(specs) == len(book.FIELD_CANDIDATES)


def test_cozulemeyen_alana_yazma_denemesi_hatadir() -> None:
    codes = book.resolve_codes([{"code": "page_count"}])
    errors = book.draft_errors({"publisher": "BBD"}, codes)
    assert "publisher" in errors
    assert book.patch_for({"publisher": "BBD"}, codes) == {}


def test_taninmayan_alan_sessizce_dusurulmez() -> None:
    codes = book.resolve_codes(NITELIKLER)
    assert "renk" in book.draft_errors({"renk": "mavi"}, codes)


# ═══════════════════════════════════════════════ doğrulama

def test_bos_deger_her_alanda_mesrudur() -> None:
    """Künye eksik olabilir; boş bırakmak "bilinmiyor" demenin doğru yolu."""
    for field in book.FIELD_CANDIDATES:
        assert book.field_error(field, "") == ""


def test_sayfa_sayisi_sayi_olmalidir_ve_tavani_vardir() -> None:
    assert "sayı olmalı" in book.field_error("pageCount", "Fasikül")
    assert "en çok" in book.field_error("pageCount", "9000")
    assert book.field_error("pageCount", "176") == ""


def test_isbn_on_ya_da_onuc_hane_ister_tire_saymaz() -> None:
    assert book.field_error("isbn", "978-605-123-456-7") == ""
    assert "10 ya da 13" in book.field_error("isbn", "12345")


def test_baski_yili_dort_hanelidir() -> None:
    assert book.field_error("publishYear", "2024") == ""
    assert book.field_error("publishYear", "24")


def test_desi_sifirdan_buyuk_olmali() -> None:
    assert book.field_error("desi", "0")
    assert book.field_error("desi", "0.21") == ""


def test_bos_deger_yazilir_cunku_yanlis_isbn_temizlenebilmeli() -> None:
    """Sırlarda geçerli olan "boş = dokunma" kuralı BURADA GEÇERSİZDİR:
    burada silinen bir şey geri yazılabilir, yanlış ISBN ise satışta kalır."""
    codes = book.resolve_codes(NITELIKLER)
    assert book.patch_for({"isbn": ""}, codes) == {"isbn": ""}


# ═══════════════════════════════════════════════ servis yolu

async def test_kunye_kitap_blogunu_ve_desi_dokumunu_tasir() -> None:
    service, _ = _service()
    result = await service.card(7)
    assert result["ok"] is True
    assert result["book"]["values"]["pageCount"] == "176"
    assert result["book"]["values"]["publisher"] == "BBD"
    assert result["book"]["desi"]["source"] == book.SOURCE_PAGE_COUNT
    # Panel katsayıları BURADAN alır; kendi sabitini yazmaz.
    assert result["book"]["rules"]["footprintCm2"] == 634.25


async def test_kitap_alani_yazilirken_dokunulmayan_nitelikler_geri_konur() -> None:
    """TUZAK 1 kitap alanları için de geçerli.

    Kısmi PUT `page_count`'u boşaltabilir ve boşalan sayfa sayısı ürünü kargo
    hesabında varsayılan 1,0 desiye çıkarır — yani sessizce paraya dokunur.
    """
    service, api = _service()
    result = await service.save(7, patch={}, book_patch={"isbn": "9789750000000"},
                                reason="ISBN yanlış girilmişti", actor="Test", dry_run=False)
    assert result["ok"] is True
    body = api.used("update_product")[0]["payload"]
    assert body["isbn"] == "9789750000000"
    assert body["page_count"] == "176"          # dokunulmadı ama gövdede
    assert body["yayinevi"] == "BBD"
    assert body["channel"] == "default" and body["locale"] == "tr"


async def test_sadece_ad_degisse_bile_kitap_alanlari_govdede_kalir() -> None:
    """Kitap sekmesine hiç girilmeden yapılan bir kaydetme de sayfayı korumalı."""
    service, api = _service()
    await service.save(7, patch={"name": "Yeni ad"}, reason="Ad düzeltmesi yapıldı",
                       actor="Test", dry_run=False)
    body = api.used("update_product")[0]["payload"]
    assert body["page_count"] == "176"
    assert body["baski_yili"] == "2024"


async def test_gecersiz_sayfa_sayisi_magazaya_hic_gitmez() -> None:
    service, api = _service()
    result = await service.save(7, patch={}, book_patch={"pageCount": "Fasikül"},
                                reason="Sayfa sayısı düzeltmesi", actor="Test", dry_run=False)
    assert result["ok"] is False
    assert result["field"] == "pageCount"
    assert api.used("update_product") == []


async def test_yazma_yaniti_yeni_desiyi_soyler() -> None:
    service, _ = _service()
    result = await service.save(7, patch={}, book_patch={"pageCount": "352"},
                                reason="Sayfa sayısı güncellendi", actor="Test", dry_run=False)
    assert result["ok"] is True
    assert result["desi"]["pageCount"] == 352
    assert result["desi"]["source"] == book.SOURCE_PAGE_COUNT


async def test_nitelik_listesi_okunamazsa_alanlar_acilmaz_ama_ekran_durur() -> None:
    """K7: kitap alanları çözülemese de künye açılır ve desi yine hesaplanır."""
    service, api = _service()
    api.fail.add("attributes")
    result = await service.card(7)
    assert result["ok"] is True
    assert all(not item["available"] for item in result["book"]["fields"])
    assert any("kitap alanları" in item for item in result["warnings"])
    # Ürünün kargo hesabındaki desisi, ekranın onu düzenleyip düzenleyememesinden
    # bağımsız bir gerçektir ve yine gösterilir.
    assert result["book"]["desi"]["source"] == book.SOURCE_PAGE_COUNT


async def test_nitelik_listesi_bir_kez_sorulur() -> None:
    service, api = _service()
    await service.card(7)
    await service.card(7)
    assert len(api.used("attributes")) == 1


async def test_okunamayan_nitelik_listesi_kalici_saklanmaz() -> None:
    """Geçici bir ağ hatası "bu mağazada kitap alanı yok"a dönüşmemeli."""
    service, api = _service()
    api.fail.add("attributes")
    assert await service.book_codes() == {}
    api.fail.discard("attributes")
    assert (await service.book_codes())["pageCount"] == "page_count"


# ═══════════════════════════════════════════════ toplu yazma

async def test_toplu_onizleme_fark_tablosu_uretir() -> None:
    service, _ = _service()
    result = await service.bulk_preview(kind="book", product_ids=[7], field="pageCount",
                                        mode="set", value="352")
    assert result["ok"] is True
    row = result["rows"][0]
    assert row["before"] == "176" and row["after"] == "352"
    assert result["summary"]["changed"] == 1


async def test_toplu_yazma_yalniz_sayfa_ve_desi_icin_acilir() -> None:
    """ISBN/yazar/yayınevi ürüne ÖZGÜdür; toplu yazmak onları bozardı."""
    service, _ = _service()
    result = await service.bulk_preview(kind="book", product_ids=[7], field="isbn",
                                        mode="set", value="9789750000000")
    assert result["ok"] is False
    assert "sayfa sayısı ve desi" in result["error"]


async def test_gecersiz_deger_onizlemeye_bile_girmez() -> None:
    service, _ = _service()
    result = await service.bulk_preview(kind="book", product_ids=[7], field="pageCount",
                                        mode="set", value="9000")
    assert result["ok"] is False


async def test_ayni_degerdeki_urun_atlanir_ama_tablodan_dusmez() -> None:
    service, _ = _service()
    result = await service.bulk_preview(kind="book", product_ids=[7], field="pageCount",
                                        mode="set", value="176")
    assert result["rows"][0]["skipped"] is True
    assert result["summary"]["changed"] == 0


async def test_bosaltma_ayri_bir_kiptir() -> None:
    """Yanlışlıkla girilmiş bir `desi` ölçümü hesabı EZMEYE devam eder;
    kaldırmanın tek yolu alanı boş yazmaktır."""
    api = FakeApi({7: {**KITAP, "desi": "0.9"}})
    service, _ = _service(api)
    result = await service.bulk_preview(kind="book", product_ids=[7], field="desi",
                                        mode="clear", value="")
    assert result["rows"][0]["before"] == "0.9"
    assert result["rows"][0]["after"] == "—"
    assert result["rows"][0]["skipped"] is False


async def test_toplu_uygulama_onizlemedeki_degeri_yazar() -> None:
    service, api = _service(bulk_direct_limit=10)
    preview = await service.bulk_preview(kind="book", product_ids=[7], field="pageCount",
                                         mode="set", value="352")
    result = await service.bulk_apply(token=preview["token"], reason="Seri sayfa düzeltmesi",
                                      actor="Test", dry_run=False)
    assert result["ok"] is True
    body = api.used("update_product")[0]["payload"]
    assert body["page_count"] == "352"
    # Aynı üründeki diğer künye alanları kısmi PUT ile boşalmadı.
    assert body["isbn"] == "9786051234567"


async def test_toplu_uc_yokken_sirali_yazma_kapalidir() -> None:
    """Varsayılan `bulk_direct_limit` 0'dır: ekran açıkça açılmadan yazmaz."""
    service, api = _service()
    preview = await service.bulk_preview(kind="book", product_ids=[7], field="pageCount",
                                         mode="set", value="352")
    assert preview["applicable"] is False
    result = await service.bulk_apply(token=preview["token"], reason="Seri sayfa düzeltmesi",
                                      actor="Test", dry_run=False)
    assert result["ok"] is False
    assert api.used("update_product") == []


# ═══════════════════════════════════════════════ seçimli nitelikler
#
# CANLIDA ÖLÇÜLDÜ (16.08.2026): `publisher`, `book_language`, `exam_type` ve
# `publish_type` `select` tipinde ve değerlerini SEÇENEK KİMLİĞİYLE saklıyor
# (bir kitabın yayınevi `76`dır, "Panama Yayıncılık" değil). Bu alana serbest
# metin yazmak, olmayan bir koda yazmakla AYNI sessiz kayıptır: Bagisto isteği
# kabul eder, ürün yayınevsiz kalır, personel "kaydettim" sanır.

SECIMLI = [
    {"id": 33, "code": "publisher", "type": "select", "adminName": "Yayınevi"},
    {"id": 37, "code": "page_count", "type": "text", "adminName": "Sayfa Sayısı"},
]

SECENEKLER = {"publisher": {"type": "select", "options": [
    {"value": "76", "label": "YILDIZLAR YARIŞIYOR YAYINLARI"},
    {"value": "10", "label": "Benim Başarı Dünyam"},
]}}


def test_secimli_nitelik_metin_kutusu_olarak_acilmaz() -> None:
    codes = book.resolve_codes(SECIMLI)
    specs = {item["key"]: item for item in book.field_specs(codes, SECENEKLER)}
    assert specs["publisher"]["type"] == "select"
    assert specs["publisher"]["available"] is True
    assert specs["publisher"]["options"] == [
        {"value": "76", "label": "YILDIZLAR YARIŞIYOR YAYINLARI"},
        {"value": "10", "label": "Benim Başarı Dünyam"},
    ]
    # Metin alanı METİN kalır: tip mağazadan geliyor, tahminle değil.
    assert specs["pageCount"]["type"] == "text"


def test_secenegi_okunamayan_secimli_alan_acilmaz_ve_nedeni_yazilir() -> None:
    """Kod çözüldü ama seçenek listesi okunamadı. Alanı yine de açmak,
    kimliği bilinmeyen bir değer yazdırmak olurdu."""
    codes = book.resolve_codes(SECIMLI)
    specs = {item["key"]: item for item in book.field_specs(codes, {
        "publisher": {"type": "select", "options": []}})}
    assert specs["publisher"]["available"] is False
    assert "seçenek" in specs["publisher"]["reason"]
    # Alan listeden DÜŞÜRÜLMEZ: "neden yok" sorusunun cevabı ekranda durur.
    assert specs["publisher"]["code"] == "publisher"


def test_listede_olmayan_secenek_kimligi_reddedilir() -> None:
    codes = book.resolve_codes(SECIMLI)
    hatalar = book.draft_errors({"publisher": "999"}, codes, SECENEKLER)
    assert "publisher" in hatalar
    assert "listede yok" in hatalar["publisher"]
    # Listedeki kimlik geçer.
    assert book.draft_errors({"publisher": "76"}, codes, SECENEKLER) == {}


def test_secimli_alanda_bos_deger_mesrudur() -> None:
    """Yayınevi bilinmiyor olabilir; zorunlu tutmak bilmeyeni uydurmaya
    zorlardı ve yanlış yayınevi yazdırırdı."""
    codes = book.resolve_codes(SECIMLI)
    assert book.draft_errors({"publisher": ""}, codes, SECENEKLER) == {}


def test_secim_degeri_sayiya_cevrilerek_yazilir() -> None:
    """Panel `<select>` kutusundan METİN gönderiyor, mağaza TAMSAYI bekliyor."""
    codes = book.resolve_codes(SECIMLI)
    assert book.patch_for({"publisher": "76"}, codes, SECENEKLER) == {"publisher": 76}
    assert book.patch_for({"publisher": ""}, codes, SECENEKLER) == {"publisher": ""}


def test_baski_yili_canlidaki_koddan_cozulur() -> None:
    """`print_year` ÖLÇÜLDÜ: katalog baskı yılını bu kodla tutuyor. Aday
    listesinde olmadığı sürece alan hiç açılmıyordu."""
    codes = book.resolve_codes([{"code": "print_year", "type": "text"}])
    assert codes["publishYear"] == "print_year"


async def test_secenekler_nitelik_detayindan_cekilir_ve_bir_kez_sorulur() -> None:
    """Liste ucu `options: null` döndürüyor (canlıda ölçüldü); seçenekler
    yalnız detay ucundan geliyor. Ek istek SEÇİMLİ alan başına birdir ve
    kodlarla birlikte saklanır."""
    service, api = _service()
    # `_service` nitelik listesini kendi kurgusuyla yazıyor; seçimli dünyayı
    # ondan SONRA koyuyoruz.
    api.attributes_payload = {"items": list(SECIMLI), "meta": {}}
    api.attributes_by_id = {33: {"id": 33, "code": "publisher", "type": "select",
                                 "options": [{"id": 76, "adminName": "YILDIZLAR"}]}}

    ilk = await service.reference()
    await service.reference()
    yayinevi = next(item for item in ilk["bookFields"] if item["key"] == "publisher")
    assert yayinevi["type"] == "select"
    assert yayinevi["options"] == [{"value": "76", "label": "YILDIZLAR"}]
    # Metin alanı için detay İSTENMEZ: yalnız seçimli olan sorulur.
    assert len(api.used("attribute")) == 1


async def test_secenek_detayi_okunamazsa_ekran_ayakta_kalir() -> None:
    """K7: detay ucu düşerse alan açılmaz ama künye ekranı yine çizilir."""
    service, api = _service()
    api.attributes_payload = {"items": list(SECIMLI), "meta": {}}
    api.fail.add("attribute")

    sonuc = await service.card(7)
    assert sonuc["ok"] is True
    yayinevi = next(item for item in sonuc["book"]["fields"] if item["key"] == "publisher")
    assert yayinevi["available"] is False
    assert "seçenek" in yayinevi["reason"]
    # Sayfa sayısı çalışmaya devam eder — bir alanın düşmesi diğerini düşürmez.
    assert sonuc["book"]["desi"]["source"] == book.SOURCE_PAGE_COUNT


# ═══════════════════════════════════════════════ öznitelik AİLESİ kapsamı
#
# CANLIDA ÖLÇÜLDÜ (16.08.2026): mağaza künye değerini niteliğin KATALOGDA
# olmasına göre değil ÜRÜNÜN AİLESİNDE olmasına göre yazıyor —
# `AdminCatalogProductUpdateProcessor::resolveAttributeCodes` gövdenin
# anahtarlarını ailenin nitelik listesiyle kesiştiriyor ve dışarıda kalan kodu
# HATA DA UYARI DA ÜRETMEDEN düşürüyor. `default` ailesi (id 1) kitap
# alanlarından yalnız `desi`yi taşıyor, `kitap` ailesi (id 2) dokuzunu birden.
#
# Ürün detayının `attributes` dizisi zaten aile kapsamlı geliyor (aile 1 → 22
# satır, aile 2 → 36 satır), yani kapsam EK İSTEK OLMADAN biliniyor.

#: Aile 1'deki bir ürün: kitap alanlarından yalnız `desi` var.
AILESIZ_KITAP = {
    "id": 8, "sku": "BBD-OZEL-TAHSILAT", "type": "simple", "status": 1, "name": "Tahsilat",
    "url_key": "tahsilat", "attributes": [
        {"code": "sku", "value": "BBD-OZEL-TAHSILAT"},
        {"code": "name", "value": "Tahsilat"},
        {"code": "desi", "value": ""},
    ],
}


def test_ailede_olmayan_nitelik_katalogda_varsa_bile_acilmaz() -> None:
    """Dördüncü hâl: kod çözüldü ama ürünün ailesi o niteliği taşımıyor."""
    codes = book.resolve_codes(NITELIKLER)
    specs = {item["key"]: item
             for item in book.field_specs(codes, None, {"sku", "name", "desi"})}
    assert specs["isbn"]["code"] == "isbn"          # katalogda VAR
    assert specs["isbn"]["available"] is False      # ama bu ailede yazılamaz
    assert "AİLESİNDE yok" in specs["isbn"]["reason"]
    # Ailenin taşıdığı alan açık kalır: kısıt toptan kapatma değil.
    assert specs["desi"]["available"] is True


def test_aile_bilinmiyorsa_kisit_uygulanmaz() -> None:
    """`None` ile boş küme AYNI ŞEY DEĞİL.

    Aile şeması okunamadığında bütün künye alanlarını kapatmak, geçici bir ağ
    hatasını kalıcı bir eksikliğe çevirirdi — modülün her yerinde reddedilen
    davranışın ta kendisi.
    """
    codes = book.resolve_codes(NITELIKLER)
    acik = {item["key"]: item for item in book.field_specs(codes, None, None)}
    kapali = {item["key"]: item for item in book.field_specs(codes, None, set())}
    assert acik["isbn"]["available"] is True
    assert kapali["isbn"]["available"] is False


def test_ailede_olmayan_koda_yazma_denemesi_hatadir_ve_yamaya_girmez() -> None:
    codes = book.resolve_codes(NITELIKLER)
    aile = {"sku", "name", "desi"}
    hatalar = book.draft_errors({"isbn": "9786051234567"}, codes, None, aile)
    assert "isbn" in hatalar
    # Kapı ile yazıcı AYNI kararı verir: yama sessizce dolmaz.
    assert book.patch_for({"isbn": "9786051234567", "desi": "1"}, codes, None, aile) == {
        "desi": "1"}


async def test_urunun_ailesinde_olmayan_kunye_alani_kartta_acilmaz() -> None:
    service, _api = _service(FakeApi({8: dict(AILESIZ_KITAP)}))
    sonuc = await service.card(8)

    alanlar = {item["key"]: item for item in sonuc["book"]["fields"]}
    assert alanlar["isbn"]["available"] is False
    assert alanlar["desi"]["available"] is True


async def test_ailede_olmayan_alana_kaydetme_magazaya_hic_gitmez() -> None:
    """Sessiz kaybın kendisi: istek 200 döner, personel "kaydettim" der.

    Ekran o alanı zaten çizmiyor ama istek elle kurulabilir (K9); kapı
    backend'de de durur.
    """
    service, api = _service(FakeApi({8: dict(AILESIZ_KITAP)}))
    sonuc = await service.save(8, patch={}, book_patch={"isbn": "9786051234567"},
                               reason="ISBN girişi yapılıyor", actor="Test", dry_run=False)

    assert sonuc["ok"] is False
    assert "AİLESİNDE yok" in sonuc["error"]
    assert api.used("update_product") == []


# ═══════════════════════════════════════════ seçenek okunamama KALICI DEĞİL

async def test_secenek_ucu_duzelince_secimli_alan_kendiliginden_gelir() -> None:
    """Alanın kendi gerekçesi "bağlantı düzelince kendiliğinden gelir" diyor.

    Eksik çözüm saklanırsa gelmez: `book_codes` bir daha hiç sormaz ve alan
    sidecar yeniden başlayana kadar kapalı kalırdı.
    """
    service, api = _service()
    api.attributes_payload = {"items": list(SECIMLI), "meta": {}}
    api.attributes_by_id = {33: {"id": 33, "code": "publisher", "type": "select",
                                 "options": [{"id": 76, "adminName": "YILDIZLAR"}]}}
    api.fail.add("attribute")

    kopuk = await service.reference()
    assert next(item for item in kopuk["bookFields"]
                if item["key"] == "publisher")["available"] is False

    api.fail.discard("attribute")
    duzelen = await service.reference()
    yayinevi = next(item for item in duzelen["bookFields"] if item["key"] == "publisher")
    assert yayinevi["available"] is True
    assert yayinevi["options"] == [{"value": "76", "label": "YILDIZLAR"}]


async def test_secenegi_hic_olmayan_nitelik_her_seferinde_yeniden_sorulmaz() -> None:
    """"Seçeneği YOK" ile "seçeneği OKUNAMADI" ayrı şeylerdir.

    Birincisi tam bir cevaptır ve saklanır; ikisini karıştırmak seçeneksiz bir
    niteliği her çağrıda yeniden sorduran sonsuz bir tur üretirdi.
    """
    service, api = _service()
    api.attributes_payload = {"items": list(SECIMLI), "meta": {}}
    api.attributes_by_id = {33: {"id": 33, "code": "publisher", "type": "select",
                                 "options": []}}

    await service.reference()
    await service.reference()
    assert len(api.used("attributes")) == 1
    assert len(api.used("attribute")) == 1


# ═══════════════════════════════════════ Türkçe ondalık virgül · kanonikleşme
#
# KM tarafı virgülü BİLEREK kabul ediyor (`positive_float` noktaya çeviriyor),
# yani `1,2` ekranda doğru görünür ve doğrulamadan geçer. Mağaza tarafı ise
# PHP `is_numeric()` ile okuyor ve PHP ondalık ayracı olarak yalnız noktayı
# tanır: `is_numeric("1,2")` FALSE'tur. Ham yazılan virgül mağazada HİÇ
# okunmaz, hesap varsayılan 1,0 desiye düşer — ekran "2 desi" derken müşteriden
# 1 desi tahsil edilir.

def test_virgullu_desi_noktaya_cevrilerek_yazilir() -> None:
    codes = book.resolve_codes(NITELIKLER)
    yama = book.patch_for({"desi": "1,2", "pageCount": "176", "publishYear": "2024"}, codes)
    assert yama["desi"] == "1.2"
    assert yama["page_count"] == "176"
    assert yama["baski_yili"] == "2024"


def test_yamada_hicbir_alanda_virgul_kalmaz() -> None:
    """KİLİT: `patch_for` çıktısında virgül bulunmaz.

    Doğrulayıcı virgülü kabul ettiği sürece yazıcının onu temizlemesi
    zorunludur; ikisi ayrışırsa ekranda geçen değer mağazada okunamaz.
    """
    codes = book.resolve_codes(NITELIKLER)
    yama = book.patch_for({"desi": "0,45", "pageCount": "176,0", "publishYear": "2024"}, codes)
    assert not any("," in str(value) for value in yama.values())


def test_kanoniklestirme_degeri_bozmaz() -> None:
    # Tam sayı desi kuyruk sıfır taşımaz; okunamayan değer OLDUĞU GİBİ kalır
    # (kapı `draft_errors`tır ve zaten reddeder — burada sayı UYDURULMAZ).
    assert book.canonical_number("desi", "2,0") == "2"
    assert book.canonical_number("desi", "0,45") == "0.45"
    assert book.canonical_number("pageCount", "176,5") == "176"
    assert book.canonical_number("desi", "Fasikül") == "Fasikül"
    assert book.canonical_number("isbn", "978-605") == "978-605"
    assert book.canonical_number("desi", "") == ""


def test_toplu_yazmada_da_virgul_temizlenir() -> None:
    """Toplu uygulama satırın `value` alanını doğrudan gövdeye koyuyor
    (`patch_for`den geçmiyor); kanonikleştirme orada da gerekli."""
    satirlar = book.bulk_rows([{"id": 7, "sku": "A", "name": "A", "book": {"desi": ""}}],
                              field="desi", mode="set", amount="0,45")
    assert satirlar[0]["value"] == "0.45"
    assert satirlar[0]["after"] == "0.45"


async def test_virgullu_desi_magazaya_nokta_ile_gider() -> None:
    """Tel üzerinde ölçüm: gövdeye giren gerçek değer."""
    service, api = _service()
    await service.save(7, patch={}, book_patch={"desi": "0,45"},
                       reason="Depoda ölçüldü", actor="Test", dry_run=False)

    govde = api.used("update_product")[0]["payload"]
    assert govde["desi"] == "0.45"


def test_sifira_inen_olcum_yazilmaz_tam_gosterim_kullanilir() -> None:
    """Dört ondalıkta `0`a düşen bir desi, mağazada "ölçüm yok" demek olurdu.

    `positiveFloat` sıfırı eliyor: yazılan `0`, elle girilmiş ölçümü sessizce
    silip hesabı sayfa sayısına döndürürdü.
    """
    yazilan = book.canonical_number("desi", "0,00001")
    assert book.positive_float(yazilan) is not None
    assert "," not in yazilan
