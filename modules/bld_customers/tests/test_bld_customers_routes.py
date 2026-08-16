"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_customers_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), doğru izni mi taşıyor ve
panelden gelen gövde tam olarak beklenen alan adlarını mı taşıyor.

BU EKRANDA İZİN TABLOSU DİĞERLERİNDEN AĞIRDIR. `bld_customers.view` bir okuma
izninden fazlasıdır: taşıyan kişi bütün müşteri telefon ve e-posta defterine
erişir. Bir ucun `requires(...)` bağımlılığını unutmak, o defteri OTURUM AÇAN
HERKESE açar ve hiçbir iş kuralı testi bunu yakalamaz — servis yine doğru
çalışır, yalnız kapı açık kalır.
"""

from __future__ import annotations

import pytest
from bld_customers_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Müşteri telefon numarasını değiştirdi, kayıt güncellendi"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_customers.view"},
    ("GET", "/customers"): {"bld_customers.view"},
    ("GET", "/customers/{customer_id}"): {"bld_customers.view"},
    ("GET", "/customers/{customer_id}/orders"): {"bld_customers.view"},
    ("GET", "/customers/{customer_id}/subscriptions"): {"bld_customers.view"},
    ("GET", "/customers/{customer_id}/addresses"): {"bld_customers.view"},
    ("GET", "/customers/{customer_id}/sms"): {"bld_customers.view"},
    ("GET", "/access-log"): {"bld_customers.view"},
    ("GET", "/audit"): {"bld_customers.view"},
    ("GET", "/prefs"): {"bld_customers.view"},
    # Tercih BLD'yi etkilemez ve müşteri verisine dokunmaz: `view` yeter.
    ("PUT", "/prefs"): {"bld_customers.view"},
    ("PATCH", "/customers/{customer_id}"): {"bld_customers.manage"},
    # YIKICI: yalnız üçüncü anahtar.
    ("POST", "/customers/{customer_id}/disable"): {"bld_customers.disable"},
    # ONARICI: iki izin, "en az biri".
    ("POST", "/customers/{customer_id}/enable"): {"bld_customers.manage",
                                                  "bld_customers.disable"},
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
    bulunan = _endpoints()
    assert set(bulunan) == set(BEKLENEN), "uç listesi sözleşmeden ayrıştı"

    for anahtar, route in bulunan.items():
        izinler = _declared(route)
        assert izinler, f"{anahtar} izin ilan etmiyor (K9)"
        assert izinler == BEKLENEN[anahtar], f"{anahtar} izni değişmiş: {izinler}"


def test_silme_ucu_yoktur() -> None:
    # SÖZLEŞMEDE SİLME UCU YOK VE OLMAYACAK: geçmiş siparişlerin müşterisi
    # olmayan kayıtlara dönüşmesi geri alınamaz bir kayıptır. Bir gün biri
    # "temizlik" için `DELETE` eklerse bu test düşer.
    fiiller = {method for (method, _) in _endpoints()}
    assert "DELETE" not in fiiller


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK YAZMA yapardı.
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
    # 500 sınırı müşteri alanının sınırıdır; 160'lık sıkı sınır sipariş
    # revizyonunundur ve bu ekranın işi değildir.
    assert routes.ReasonBody(reason="x" * 500).reason


def test_guncelleme_govdesi_yuvalidir() -> None:
    # Alanlar `fields` altında durur, kökte değil: kökte olsalardı `reason` ve
    # `dryRun` ile aynı ad alanını paylaşırlardı ve "gönderilmedi" ile "null
    # yazıldı" ayrımı kaybolurdu.
    govde = routes.UpdateBody(reason=GEREKCE, fields={"telephone": "5329876543"})
    assert govde.fields == {"telephone": "5329876543"}

    with pytest.raises(ValidationError):
        routes.UpdateBody(reason=GEREKCE, telephone="5329876543")


def test_yasak_alan_semada_degil_serviste_reddedilir() -> None:
    # `fields` serbest bir sözlüktür ve `email` ŞEMADAN GEÇER. Bilinçli: her
    # yasak alanın KENDİ GEREKÇESİ var ve pydantic'in üreteceği "extra fields
    # not permitted" cümlesi o gerekçeyi taşıyamaz. Kapı `people.patch_error`
    # içindedir ve tek cümleyle NEDEN olmadığını söyler.
    govde = routes.UpdateBody(reason=GEREKCE, fields={"email": "yeni@ornek.com"})
    assert govde.fields == {"email": "yeni@ornek.com"}


def test_tercih_govdesi_gerekce_istemez() -> None:
    # Tercih BLD'yi etkilemez ve KVKK erişim izine satır düşürmez; gerekçe
    # istemek, hiçbir şeyi denetlemeyen bir kutu göstermek olurdu.
    govde = routes.PrefsBody(values={"page_size": 50})
    assert govde.values == {"page_size": 50}

    with pytest.raises(ValidationError):
        routes.PrefsBody(values={"page_size": 50}, reason=GEREKCE)


def test_servis_baglanmadan_503() -> None:
    # `bind()` çağrılmadan uç kullanılırsa 503; `None` üzerinde `AttributeError`
    # patlaması, ekranda "sunucu hatası" diye görünürdü.
    onceki = routes._service
    routes._service = None
    try:
        with pytest.raises(routes.HTTPException) as bilgi:
            routes.service()
        assert bilgi.value.status_code == 503
    finally:
        routes._service = onceki
