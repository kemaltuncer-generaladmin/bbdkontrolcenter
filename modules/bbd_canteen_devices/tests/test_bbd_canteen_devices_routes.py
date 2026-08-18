"""HTTP yüzeyinin sözleşmesi — izin kapıları, PIN teyidi ve gövde alanları.

Bu dosya iş kuralı sınamaz (o `test_bbd_canteen_devices_service.py`'nin işi);
ucun DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), yıkıcı uç PIN
istiyor mu ve panelden gelen gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import inspect

import pytest
from bbd_canteen_devices_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Kantin kiosk cihazi degistirildi"

#: Uç → beklenen izin(ler). Tabloyu elle yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/kiosks"): {"bbd_canteen_devices.view"},
    ("GET", "/audit"): {"bbd_canteen_devices.view"},
    ("GET", "/printer"): {"bbd_canteen_devices.view"},
    ("POST", "/kiosks"): {"bbd_canteen_devices.manage"},
    ("PATCH", "/kiosks/{kiosk_id}"): {"bbd_canteen_devices.manage"},
    # KOD ÜRETMEK `manage`TE: günlük iş. İPTAL AYRI ANAHTARDA, çünkü iptal
    # edilen kiosk kantinde satış yapamaz ve kararı geri alınamaz.
    ("POST", "/kiosks/{kiosk_id}/pairing-code"): {"bbd_canteen_devices.manage"},
    ("POST", "/kiosks/{kiosk_id}/revoke"): {"bbd_canteen_devices.devices"},
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


def test_iptal_ucu_pin_teyidi_ister() -> None:
    """Yıkıcı işlem, izin yeterli olsa bile PIN sorar (CLAUDE.md).

    İki ayrı şey doğrulanır. Birincisi ŞEMA: PIN alanı zorunludur, gövdeden
    düşürülemez. İkincisi ÇAĞRI: uç gerçekten `confirm_pin` çağırıyor. Yalnız
    şemaya bakmak yetmezdi — zorunlu ama hiç doğrulanmayan bir alan, güvenlik
    sağladığı sanılan bir süstür.
    """
    with pytest.raises(ValidationError):
        routes.RevokeBody(reason=GEREKCE)

    kaynak = inspect.getsource(routes.revoke_kiosk)
    assert "confirm_pin(" in kaynak, "iptal ucu PIN teyidi çağırmıyor"

    # Yalnız iptal ucu ister: kod üretmek yıkıcı değildir ve her kod için PIN
    # sormak, personeli PIN'i ezberden ve dikkatsizce girmeye alıştırırdı.
    assert "confirm_pin(" not in inspect.getsource(routes.pairing_code)


def test_pin_govdede_tasinir_sorgu_dizesinde_degil() -> None:
    # Sorgu dizesi denetim kaydına, sunucu günlüğüne ve tarayıcı geçmişine
    # düşer; PIN oralarda kalıcı olurdu.
    imza = inspect.signature(routes.revoke_kiosk)
    assert "pin" not in imza.parameters
    assert "pin" in routes.RevokeBody.model_fields


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 161)

    assert routes.ReasonBody(reason=GEREKCE).reason == GEREKCE


def test_kuru_prova_alani_yoktur() -> None:
    """`bld_kds`ten BİLEREK AYRILAN nokta.

    Orada `dryRun` var çünkü BLD geçidinde karşılığı var. Kantinde yok; alan
    eklemek, "prova yaptım" diyen ama gerçekten yazan bir çağrı üretirdi.
    `extra="forbid"` sayesinde gönderilmesi de 422 ile geri döner.
    """
    assert "dryRun" not in routes.ReasonBody.model_fields
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dryRun=True)


def test_kod_ucu_baski_bayragi_tasir() -> None:
    # Ayrı bir "sonra bas" ucu YOK: kod hiçbir yere yazılmıyor, basım kodun düz
    # göründüğü tek anda yapılabilir.
    assert routes.PairingBody(reason=GEREKCE).print is False
    assert routes.PairingBody(reason=GEREKCE, print=True).print is True
