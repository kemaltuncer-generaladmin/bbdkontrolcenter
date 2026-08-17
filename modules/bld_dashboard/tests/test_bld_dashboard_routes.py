"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_dashboard_service.py`'nin işi); ucun
DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen
gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_dashboard_backend.api import routes
from pydantic import ValidationError

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_dashboard.view"},
    ("GET", "/summary"): {"bld_dashboard.view"},
    # TEK YAZMA VE O DA YEREL. `manage` istemesinin sebebi `api/routes.py`
    # başlığında: bu modülde BLD'ye giden yazma yok, ikisini de `view`e
    # bağlamak `manage`i hiçbir kapıyı açmayan bir anahtar yapardı.
    ("PUT", "/prefs"): {"bld_dashboard.manage"},
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


def test_yazma_ucu_yok_okuma_uclari_get_tercih_put() -> None:
    # Sözleşme bu alanı SALT OKUNUR ilan ediyor (`dashboard.md`): BLD'ye giden
    # tek bir yazma yok. Buraya bir `POST` eklendiği gün bu test düşer ve
    # ekleyen kişi sözleşmeyi okumak zorunda kalır.
    metotlar = {metot for metot, _ in _endpoints()}
    assert metotlar == {"GET", "PUT"}
    assert not any(yol.startswith(("/orders", "/menu"))
                   for _, yol in _endpoints()), "gösterge paneli başka alanın ucunu açmaz"


def test_tercih_govdesi_dryrun_kabul_etmez() -> None:
    # Bu modülde kuru prova KAVRAMI YOK: yazma ucu olmayan bir ekranın provası
    # da olmaz. `dryRun` alanını kabul eden bir gövde, olmayan bir güvenlik
    # ağının varmış gibi görünmesi olurdu.
    with pytest.raises(ValidationError):
        routes.PrefsBody(dryRun=True)
    with pytest.raises(ValidationError):
        routes.PrefsBody(dry_run=True)


def test_tercih_govdesi_yanlis_yazilan_alani_reddeder() -> None:
    # `extra="forbid"` olmasaydı `poll_second` sessizce düşer, alan "hiç
    # gönderilmemiş" sayılır ve kullanıcı kaydettiğini sanarak eski aralıkta
    # yoklamaya devam ederdi.
    with pytest.raises(ValidationError):
        routes.PrefsBody(poll_second=45)

    assert routes.PrefsBody(poll_seconds=45).poll_seconds == 45


def test_yoklama_araligi_semada_da_sinirlanir() -> None:
    # Çift kapı (K9): servis ayrıca kırpıyor, buradaki kapı erken geri
    # bildirim. Alt sınır 10 saniye — paylaşılan `bld-control-panel` kovası
    # 3000/saat/IP ve bu ekran her yoklamada İKİ çağrı yapıyor.
    with pytest.raises(ValidationError):
        routes.PrefsBody(poll_seconds=5)
    with pytest.raises(ValidationError):
        routes.PrefsBody(poll_seconds=301)
    assert routes.PrefsBody(poll_seconds=10).poll_seconds == 10
    assert routes.PrefsBody(poll_seconds=300).poll_seconds == 300


def test_akis_satir_sayisi_semada_da_sinirlanir() -> None:
    with pytest.raises(ValidationError):
        routes.PrefsBody(flow_limit=2)
    with pytest.raises(ValidationError):
        routes.PrefsBody(flow_limit=51)
    assert routes.PrefsBody(flow_limit=3).flow_limit == 3


def test_bos_govde_hicbir_alan_tasimaz_ve_varsayilanlari_servise_birakir() -> None:
    # Hiç alan gönderilmezse hepsi `None` kalır ve varsayılanı SERVİS uygular;
    # uç kendi varsayılanını yazsaydı iki yerde iki değer olurdu.
    govde = routes.PrefsBody()
    assert govde.model_dump() == {"poll_seconds": None, "location_id": None,
                                  "flow_limit": None, "flow_enabled": None}


def test_servis_baglanmadan_uc_cagrilirsa_503_verir() -> None:
    # Modül yüklenmeden uç çağrılırsa `AttributeError` yerine açık bir 503
    # dönmeli: "NoneType has no attribute" mesajı kullanıcıya hiçbir şey
    # anlatmaz.
    onceki = routes._service
    routes._service = None
    try:
        # `HTTPException` km_sdk üzerinden geliyor; sınıfı burada import etmek
        # yerine `status_code` alanına bakıyoruz — test, istisnanın TÜRÜNE
        # değil ANLAMINA bağlansın.
        with pytest.raises(routes.HTTPException) as hata:
            routes.service()
        assert getattr(hata.value, "status_code", 0) == 503
    finally:
        routes._service = onceki
