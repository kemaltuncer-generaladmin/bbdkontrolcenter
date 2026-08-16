"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_products_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen gövde
tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_products_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Zam sonrası fiyat güncellendi"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_products.view"},
    ("GET", "/products"): {"bld_products.view"},
    ("GET", "/products/{menu_id}"): {"bld_products.view"},
    ("GET", "/categories"): {"bld_products.view"},
    ("GET", "/audit"): {"bld_products.view"},
    ("GET", "/prefs"): {"bld_products.view"},
    # Tercih YEREL bir tablodur ve BLD'de hiçbir şey değiştirmez; bu yüzden
    # `view` yeter ve gerekçe istemez. `manage` istemek, sayfa boyutunu
    # değiştirmeyi ürün fiyatı yazmakla aynı kefeye koymak olurdu.
    ("PUT", "/prefs"): {"bld_products.view"},
    ("POST", "/products"): {"bld_products.manage"},
    ("PATCH", "/products/{menu_id}"): {"bld_products.manage"},
    # YIKICI: ayrı anahtar. Servis `allow_destructive` ile ikinci kez denetler.
    ("POST", "/products/{menu_id}/retire"): {"bld_products.retire"},
    ("PUT", "/products/{menu_id}/image"): {"bld_products.manage"},
    ("DELETE", "/products/{menu_id}/image"): {"bld_products.manage"},
    ("POST", "/products/{menu_id}/sold-out"): {"bld_products.manage"},
    ("DELETE", "/products/{menu_id}/sold-out"): {"bld_products.manage"},
    ("POST", "/categories"): {"bld_products.manage"},
    ("PATCH", "/categories/{category_id}"): {"bld_products.manage"},
}


def _endpoints() -> dict[tuple[str, str], object]:
    out = {}
    for route in routes.router.routes:
        for method in sorted(route.methods):
            out[(method, route.path)] = route
    return out


def _declared(route: object) -> set[str]:
    """Ucun `requires(...)` ile ilan ettiği izinler.

    `requires` bir kapanış (closure) döndürüyor ve izin demeti orada duruyor;
    FastAPI bağımlılığın kendisini saklamıyor. Kapanışı okumak kırılgan
    görünüyor ama alternatifi yok ve kırıldığında SESSİZ KALMIYOR: demet
    bulunamazsa küme boş çıkar ve aşağıdaki `assert izinler` düşer.
    """
    bulunan: set[str] = set()
    for param in route.dependant.dependencies:  # type: ignore[attr-defined]
        for cell in getattr(param.call, "__closure__", None) or ():
            icerik = cell.cell_contents
            if isinstance(icerik, tuple) and all(isinstance(x, str) for x in icerik):
                bulunan.update(icerik)
    return bulunan


def test_her_uc_izin_ilan_eder_ve_beklenen_izni_tasir() -> None:
    # K9: izin ilan etmeyen uç nokta reddedilir. Bir ucun `requires(...)`
    # bağımlılığını unutmak, o ucu OTURUM AÇAN HERKESE açar ve hiçbir iş
    # kuralı testi bunu yakalamaz — servis yine doğru çalışır, yalnız kapı
    # açık kalır.
    bulunan = _endpoints()
    assert set(bulunan) == set(BEKLENEN), "uç listesi sözleşmeden ayrıştı"

    for anahtar, route in bulunan.items():
        izinler = _declared(route)
        assert izinler, f"{anahtar} izin ilan etmiyor (K9)"
        assert izinler == BEKLENEN[anahtar], f"{anahtar} izni değişmiş: {izinler}"


def test_kategori_silen_uc_yoktur() -> None:
    # Sözleşme `DELETE /categories/{id}` TANIMLAMIYOR: kategori silmek
    # altındaki ürünleri kategorisiz bırakır ve site menüsünü sessizce
    # boşaltır. Bir gün "tamamlık olsun" diye eklenirse bu test düşer.
    yollar = {(method, path) for method, path in _endpoints()}
    assert ("DELETE", "/categories/{category_id}") not in yollar
    # Ürün de SİLİNMEZ: satıştan kaldırma `retire`, `menu_status = 0` yazar.
    assert ("DELETE", "/products/{menu_id}") not in yollar


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK YAZMA yapabilirdi.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=False)
    assert govde.dryRun is False

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=False)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.ReasonBody(reason=GEREKCE).dryRun is None


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 501)


def test_kismi_govde_yuvalidir_ve_null_tasiyabilir() -> None:
    # Alanlar `fields` altında durur: kökte olsalardı `reason`/`dryRun` ile ad
    # alanını paylaşırlardı. Daha önemlisi, "gönderilmedi" ile "null yazıldı"
    # ancak anahtarın BULUNUP bulunmamasıyla ayrılıyor.
    govde = routes.PatchBody(reason=GEREKCE, fields={"description": None})
    assert govde.fields == {"description": None}
    assert "price_kurus" not in govde.fields

    with pytest.raises(ValidationError):
        routes.PatchBody(reason=GEREKCE, price_kurus=9000)


def test_urun_govdesi_kurus_ve_ad_sinirini_zorlar() -> None:
    # Para HER ZAMAN tam sayı kuruş (`00-genel.md` §6); negatif tutar yok.
    # Sıfır GEÇERLİ: paket bileşeni olarak satılan ekmek, ayran.
    bedava = routes.ProductCreateBody(reason=GEREKCE, name="Ekmek", price_kurus=0)
    assert bedava.price_kurus == 0
    assert bedava.minimum_qty == 1
    assert bedava.category_ids == []

    with pytest.raises(ValidationError):
        routes.ProductCreateBody(reason=GEREKCE, name="Ekmek", price_kurus=-1)
    with pytest.raises(ValidationError):
        routes.ProductCreateBody(reason=GEREKCE, name="X", price_kurus=100)
    with pytest.raises(ValidationError):
        routes.ProductCreateBody(reason=GEREKCE, name="A" * 129, price_kurus=100)


def test_gorsel_govdesi_base64_alanlarini_tasir() -> None:
    # Multipart YOKTUR: imza ham gövdeyi hashliyor ve gövdeyi yeniden kodlayan
    # herhangi bir vekil imzayı bozardı (`products.md`).
    govde = routes.ImageBody(reason=GEREKCE, filename="tavuk.jpg", content="/9j/4AAQ")
    assert govde.filename == "tavuk.jpg"
    assert govde.content.startswith("/9j/")

    with pytest.raises(ValidationError):
        routes.ImageBody(reason=GEREKCE, filename="tavuk.jpg", content="")
    with pytest.raises(ValidationError):
        routes.ImageBody(reason=GEREKCE, filename="tavuk.jpg", content="/9j/",
                         mime="image/jpeg")


def test_tukendi_notu_gerekceden_ayri_alandir() -> None:
    # `reason` mutfağa konuşur (`veykemtu_menu_soldout.reason` sütununa DA
    # yazılır), `note` deftere. İkisini tek alana indirmek, mutfak ekranındaki
    # "neden yok" cevabını iç yazışmayla karıştırırdı.
    govde = routes.SoldOutBody(reason=GEREKCE, note="Tedarikçi 15:00 sonrası getirecek")
    assert govde.note.startswith("Tedarikçi")
    assert routes.SoldOutBody(reason=GEREKCE).note == ""


def test_tercih_govdesi_gerekce_istemez() -> None:
    # Yerel tabloya yazıyor; denetlenecek bir eylem yok. Her sayfa boyutu
    # değişikliğinde gerekçe istemek gerekçenin kendisini anlamsızlaştırırdı.
    govde = routes.PrefsBody(values={"page_size": 50})
    assert govde.values == {"page_size": 50}

    with pytest.raises(ValidationError):
        routes.PrefsBody(values={}, reason=GEREKCE)
