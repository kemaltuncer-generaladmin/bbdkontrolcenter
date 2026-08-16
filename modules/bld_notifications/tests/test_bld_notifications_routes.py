"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_notifications_service.py`'nin işi);
ucun DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden
gelen gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_notifications_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Bayram kapanışı duyurusu hazırlandı"

#: Uç → beklenen izin(ler). Tabloyu elle yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/notices"): {"bld_notifications.view"},
    ("GET", "/notices/{notification_id}/stats"): {"bld_notifications.view"},
    ("GET", "/audit"): {"bld_notifications.view"},
    ("POST", "/notices"): {"bld_notifications.manage"},
    ("PATCH", "/notices/{notification_id}"): {"bld_notifications.manage"},
    # DIŞA DÖNÜK: ayrı anahtar. Yayınlanan duyuru bütün hedef kitleye gider ve
    # geri alma ucu yoktur; arşiv de yayındaki duyuruyu anında görünmez yapar.
    ("POST", "/notices/{notification_id}/publish"): {"bld_notifications.publish"},
    ("POST", "/notices/{notification_id}/archive"): {"bld_notifications.publish"},
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


def test_silme_ucu_yoktur() -> None:
    # Kayıt SİLİNMEZ, pasifleştirilir (kit kuralı 8). Arşiv bir `POST`'tur;
    # `DELETE` fiili, yaptığı işi yanlış anlatırdı.
    fiiller = {metot for metot, _ in _endpoints()}
    assert "DELETE" not in fiiller


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — bir kurulum
    # varsayılanı açık bıraktıysa kuru prova sanılan istek GERÇEK YAZMA yapardı.
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


def test_yeni_duyuru_govdesinde_durum_alani_yoktur() -> None:
    # Duyuru HER ZAMAN `draft` doğar (sözleşme §POST); durumu gövdeden almak,
    # yayını ayrı bir eylem ve ayrı bir izin olmaktan çıkarırdı.
    with pytest.raises(ValidationError):
        routes.NoticeCreateBody(reason=GEREKCE, title="Başlık", body="Gövde",
                                status="published")


def test_kismi_govde_yalniz_gonderileni_tasir() -> None:
    # "Gönderildi mi" ile "boş gönderildi mi" ayrımını `model_fields_set`
    # taşır: `None` GERÇEK BİR DEĞERDİR ve "bu alanı temizle" demektir.
    sadece_baslik = routes.NoticePatchBody(reason=GEREKCE, title="Yeni")
    assert sadece_baslik.changes() == {"title": "Yeni"}

    pencere_silme = routes.NoticePatchBody(reason=GEREKCE, starts_at=None, ends_at=None)
    assert pencere_silme.changes() == {"starts_at": None, "ends_at": None}

    assert routes.NoticePatchBody(reason=GEREKCE).changes() == {}


def test_kismi_govdede_durum_yazilamaz() -> None:
    with pytest.raises(ValidationError):
        routes.NoticePatchBody(reason=GEREKCE, status="archived")


def test_sozlesme_sinirlari_semada_da_durur() -> None:
    with pytest.raises(ValidationError):
        routes.NoticeCreateBody(reason=GEREKCE, title="x" * 161, body="Gövde")
    with pytest.raises(ValidationError):
        routes.NoticeCreateBody(reason=GEREKCE, title="Başlık", body="x" * 2001)
    with pytest.raises(ValidationError):
        routes.NoticeCreateBody(reason=GEREKCE, title="Başlık", body="Gövde",
                                action_label="x" * 61)
