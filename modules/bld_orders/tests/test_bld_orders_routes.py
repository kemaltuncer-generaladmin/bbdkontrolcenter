"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_orders_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen gövde
tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_orders_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Müşteri telefonla iki porsiyon azalttı"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_orders.view"},
    ("GET", "/orders"): {"bld_orders.view"},
    ("GET", "/orders/{order_id}"): {"bld_orders.view"},
    ("GET", "/orders/{order_id}/revisions"): {"bld_orders.view"},
    ("GET", "/orders/{order_id}/invoice"): {"bld_orders.view"},
    ("GET", "/audit"): {"bld_orders.view"},
    ("POST", "/orders/{order_id}/revisions"): {"bld_orders.manage"},
    ("POST", "/orders/{order_id}/status"): {"bld_orders.manage"},
    # TEK İZİN, "en az biri" DEĞİL: ucun tek işi var ve o iş yıkıcı. `manage`
    # de kabul edilseydi, revizyon yazabilen herkes iade üretebilirdi.
    ("POST", "/orders/{order_id}/cancel"): {"bld_orders.cancel"},
    ("POST", "/export"): {"bld_orders.view"},
    ("PUT", "/prefs"): {"bld_orders.view"},
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


def test_iptal_ucu_manage_iznini_kabul_etmez() -> None:
    # Ayrı anahtarın tek anlamı bu: `manage` taşıyan biri buradan geçemez.
    # Kapı "en az biri" mantığıyla iki izne bağlansaydı, ayrımın kâğıt üstünde
    # kalması için tek bir yazım hatası yeterdi.
    iptal = _endpoints()[("POST", "/orders/{order_id}/cancel")]
    assert "bld_orders.manage" not in _declared(iptal)


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK YAZMA yapardı.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=True)
    assert govde.dryRun is True

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=True)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.ReasonBody(reason=GEREKCE).dryRun is None


def test_gerekce_semada_da_denetlenir_ve_ust_sinir_160() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir. Üst sınır 160'tır ÇÜNKÜ sunucu sütunu o kadar: 500
    # yazılsaydı kullanıcı 200 karakterlik gerekçeyi yazar ve 422 alırdı.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 161)
    assert routes.ReasonBody(reason="x" * 160).reason


def test_iptal_govdesi_iade_ve_bildirimi_acikca_tasir() -> None:
    # İkisinin de varsayılanı `True` (sözleşme). `refund=False` ödenmiş bir
    # siparişte SERBESTTİR: para elden iade edilmiş olabilir ve sistemin ikinci
    # kez iade üretmemesi gerekir.
    govde = routes.CancelBody(reason=GEREKCE)
    assert govde.refund is True
    assert govde.notify_customer is True
    assert routes.CancelBody(reason=GEREKCE, refund=False).refund is False

    with pytest.raises(ValidationError):
        routes.CancelBody(reason=GEREKCE, iade=False)


def test_revizyon_govdesi_kalem_listesini_kokte_degil_items_altinda_ister() -> None:
    govde = routes.RevisionBody(reason=GEREKCE,
                                items=[{"menu_id": 88, "quantity": 10}])
    assert govde.items == [{"menu_id": 88, "quantity": 10}]
    # Bilinmeyen alan 422 ile döner: sessizce düşen bir alan, gönderildiğini
    # sanan kullanıcıya hiçbir şey söylemezdi.
    with pytest.raises(ValidationError):
        routes.RevisionBody(reason=GEREKCE, menu_id=88)


def test_suzgec_govdesi_liste_ve_disa_aktarimda_ayni() -> None:
    # CSV ekrandaki kümenin ta kendisi olmalı: iki ayrı gövde, iki alanın
    # sessizce ayrışması demekti.
    ortak = set(routes.FilterBody.model_fields)
    assert ortak <= set(routes.ExportBody.model_fields)
    assert set(routes.ExportBody.model_fields) - ortak == {"max_rows"}


def test_tercih_govdesi_yalniz_dort_anahtar_tanir() -> None:
    assert set(routes.PrefsBody.model_fields) == {
        "page_size", "range_days", "poll_seconds", "auto_refresh"}
    with pytest.raises(ValidationError):
        routes.PrefsBody(page_size=1000)
