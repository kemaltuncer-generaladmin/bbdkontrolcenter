"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_kds_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen
gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_kds_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Yazıcı arızalandı, mutfak fiş basamıyor"

#: Uç → beklenen izin(ler). Tabloyu elle yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_kds.view"},
    ("GET", "/devices"): {"bld_kds.view"},
    ("GET", "/devices/{device_id}/commands"): {"bld_kds.view"},
    ("GET", "/print-jobs"): {"bld_kds.view"},
    ("GET", "/orders"): {"bld_kds.view"},
    ("GET", "/orders/{order_id}"): {"bld_kds.view"},
    ("GET", "/orders/{order_id}/revisions"): {"bld_kds.view"},
    ("GET", "/menu"): {"bld_kds.view"},
    ("POST", "/devices"): {"bld_kds.manage"},
    ("PATCH", "/devices/{device_id}"): {"bld_kds.manage"},
    ("POST", "/devices/{device_id}/pairing-code"): {"bld_kds.manage"},
    ("POST", "/devices/{device_id}/revoke"): {"bld_kds.devices"},
    # AYAR YAZMA `manage`TE DEĞİL (17.08.2026 kullanıcı kararı): sipariş
    # durumunu ilerleten personel kasanın ayarına dokunmaz. Ayar yazan TEK uç
    # budur — `PATCH /devices/{id}` yalnız adı değiştirir.
    ("PATCH", "/devices/{device_id}/settings"): {"bld_kds.settings"},
    # İKİ İZİN, "en az biri": yıkıcı komut ayrımı gövdeye bakılarak yapılır ve
    # servis `allow_destructive` ile ayrıca denetler.
    ("POST", "/devices/{device_id}/commands"): {"bld_kds.manage", "bld_kds.devices"},
    ("POST", "/orders/{order_id}/revisions"): {"bld_kds.manage"},
    ("POST", "/orders/{order_id}/status"): {"bld_kds.manage"},
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


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi. Varsayılan açık
    # olduğu için bu tur zararsız görünür — ama varsayılanı kapatan bir kurulumda
    # kuru prova sanılan istek GERÇEK YAZMA yapardı.
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
        routes.ReasonBody(reason="x" * 161)


def test_ayar_govdesi_yuvalidir() -> None:
    # Ayarlar `settings` altında durur, kökte değil: kökte olsalardı `reason` ve
    # `dryRun` ile aynı ad alanını paylaşırlardı ve `reason` adında bir ayar
    # eklenemezdi. `GET /devices` yanıtındaki `device.settings` ile de simetrik.
    govde = routes.SettingsBody(reason=GEREKCE, settings={"poll_seconds": 9})
    assert govde.settings == {"poll_seconds": 9}
    assert govde.token == ""

    with pytest.raises(ValidationError):
        routes.SettingsBody(reason=GEREKCE, poll_seconds=9)
