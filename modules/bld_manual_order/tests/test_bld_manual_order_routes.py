"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_manual_order_service.py`'nin işi); ucun
DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen
gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_manual_order_backend.api import routes
from pydantic import ValidationError

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_manual_order.view"},
    ("GET", "/customers"): {"bld_manual_order.view"},
    ("GET", "/products"): {"bld_manual_order.view"},
    ("GET", "/service-day"): {"bld_manual_order.view"},
    # `POST` AMA OKUMA: fiil gövde şeklinden seçildi (kalem listesi sorgu
    # dizesine sığmaz), yan etkiden değil. `manage` istemek, yazma yetkisinin
    # anlattığı şeyi bulandırırdı.
    ("POST", "/stock-check"): {"bld_manual_order.view"},
    # TEK YAZMA UCU. Sipariş açmak `view` ile yapılamaz; ekranı görebilen
    # herkesin mutfağa iş düşürebilmesi için sebep yok.
    ("POST", "/orders"): {"bld_manual_order.manage"},
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


def test_siparis_acma_ucu_view_iznini_kabul_etmez() -> None:
    # Ayrı anahtarın tek anlamı bu: yalnız `view` taşıyan biri buradan geçemez.
    yazma = _endpoints()[("POST", "/orders")]
    assert "bld_manual_order.view" not in _declared(yazma)


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK SİPARİŞ açardı.
    govde = routes.CreateBody(service_date="2026-08-18", delivery_type="pickup",
                              payment_method="cash",
                              items=[{"menu_id": 88, "quantity": 2}],
                              customer_id=312, dryRun=True)
    assert govde.dryRun is True

    with pytest.raises(ValidationError):
        routes.CreateBody(service_date="2026-08-18", delivery_type="pickup",
                          payment_method="cash",
                          items=[{"menu_id": 88, "quantity": 2}],
                          customer_id=312, dry_run=True)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.CreateBody(service_date="2026-08-18", delivery_type="pickup",
                             payment_method="cash",
                             items=[{"menu_id": 88, "quantity": 2}],
                             customer_id=312).dryRun is None


def test_gerekce_opsiyoneldir_ve_alt_siniri_yoktur() -> None:
    # SÖZLEŞME KARARI (`orders.md` → "POST /"): telefon siparişi açmak rutin
    # bir kayıt akışıdır. On karakterlik alt sınır burada uygulansaydı,
    # personel müşteriyle konuşurken "sipariş"/"asdasd" yazardı — sınırın
    # engellemek için var olduğu şeyin ta kendisi.
    ortak = {"service_date": "2026-08-18", "delivery_type": "pickup",
             "payment_method": "cash", "items": [{"menu_id": 88, "quantity": 2}],
             "customer_id": 312}
    assert routes.CreateBody(**ortak).reason == ""
    assert routes.CreateBody(**ortak, reason="ok").reason == "ok"
    assert routes.CreateBody(**ortak, reason="x" * 500).reason

    # Üst sınır 500'dür ve DURUYOR: `veykemtu_control_audit.reason` o kadar.
    with pytest.raises(ValidationError):
        routes.CreateBody(**ortak, reason="x" * 501)


def test_govde_bilinmeyen_alani_reddeder() -> None:
    # Sessizce düşen bir alan, gönderildiğini sanan kullanıcıya hiçbir şey
    # söylemezdi. `requested_at` özellikle sınanıyor: sözleşmede YOKTUR
    # (saati `OrderFactory` çözer) ve eklenmeye en yatkın alan odur.
    ortak = {"service_date": "2026-08-18", "delivery_type": "pickup",
             "payment_method": "cash", "items": [{"menu_id": 88, "quantity": 2}],
             "customer_id": 312}
    with pytest.raises(ValidationError):
        routes.CreateBody(**ortak, requested_at="2026-08-18T09:00:00Z")
    with pytest.raises(ValidationError):
        routes.CreateBody(**ortak, menu_id=88)


def test_yeni_musteri_govdesi_ad_ve_telefon_ister() -> None:
    # Ayrı ekrana gitmeden, AYNI gövdede açılır: personel telefonda, müşteri
    # hatta. İki alanın da şema kapısında istenmesi erken geri bildirimdir;
    # kuralı servis de denetliyor.
    govde = routes.NewCustomerBody(name="Acme Gıda", phone="0532 123 45 67")
    assert govde.name == "Acme Gıda"

    with pytest.raises(ValidationError):
        routes.NewCustomerBody(name="A", phone="5321234567")
    with pytest.raises(ValidationError):
        routes.NewCustomerBody(name="Acme Gıda", phone="")
    with pytest.raises(ValidationError):
        routes.NewCustomerBody(name="Acme Gıda", phone="5321234567", email="a@b.c")


def test_adres_govdesi_gel_al_siparisi_kesmez() -> None:
    # Alanlar `default=""` taşır ÇÜNKÜ `pickup` siparişte adres gövdesi hiç
    # gönderilmiyor. `required` yapmak, gel-al siparişini şema kapısında
    # keserdi; teslimatta boş kalmasını SERVİS reddeder.
    assert routes.AddressBody().line1 == ""
    assert routes.AddressBody(line1="Örnek Mah.", district="Selçuklu",
                              city="Konya").city == "Konya"
    with pytest.raises(ValidationError):
        routes.AddressBody(line1="x" * 256)


def test_stok_denetimi_govdesinde_kuru_prova_alani_yoktur() -> None:
    # Uç bir OKUMADIR: `dryRun` alanı olsaydı ekran "acaba yazdı mı" diye
    # sormaya başlardı.
    alanlar = set(routes.StockCheckBody.model_fields)
    assert alanlar == {"service_date", "items"}

    with pytest.raises(ValidationError):
        routes.StockCheckBody(service_date="2026-08-18", items=[], dryRun=True)


def test_musteri_kimligi_ve_yeni_musteri_ayri_alanlar() -> None:
    # İkisinin ayrı durması bilinçli: sunucu `customer_id` doluysa `customer`
    # nesnesini SESSİZCE yok sayar ve sipariş başka bir hesaba yazılırdı.
    # İkisi birden gönderilirse servis reddeder (şema değil — kural tek yerde
    # dursun ve gövdeyi elle kuran bir istemci de aynı cevabı alsın).
    alanlar = set(routes.CreateBody.model_fields)
    assert {"customer_id", "customer"} <= alanlar
    assert "requested_at" not in alanlar
    assert "actor" not in alanlar, "aktör oturumdan gelir, gövdeden değil"
