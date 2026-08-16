"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_invoices_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), panelden gelen gövde
tam olarak beklenen alan adlarını mı taşıyor ve SÖZLEŞMEDE OLMAYAN bir uç
eklenmiş mi.
"""

from __future__ import annotations

import pytest
from bld_invoices_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Müşteri sipariş için belge talep etti"

#: Uç → beklenen izin(ler). Tabloyu elle yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/invoices"): {"bld_invoices.view"},
    ("GET", "/invoices/{invoice_id}"): {"bld_invoices.view"},
    ("GET", "/archive"): {"bld_invoices.view"},
    ("GET", "/audit"): {"bld_invoices.view"},
    ("POST", "/invoices"): {"bld_invoices.manage"},
    # YIKICI: ayrı anahtar. `manage` taşıyan biri belge kesebilir ama
    # kesilmiş bir belgeyi geçersiz kılamaz.
    ("POST", "/invoices/{invoice_id}/void"): {"bld_invoices.void"},
    ("POST", "/invoices/{invoice_id}/html"): {"bld_invoices.view"},
    ("POST", "/preview"): {"bld_invoices.view"},
    ("POST", "/print"): {"bld_invoices.view"},
    ("GET", "/printer"): {"bld_invoices.view"},
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
    # kuralı testi bunu yakalamaz — servis yine doğru çalışır, kapı açık kalır.
    bulunan = _endpoints()
    assert set(bulunan) == set(BEKLENEN), "uç listesi sözleşmeden ayrıştı"

    for anahtar, route in bulunan.items():
        izinler = _declared(route)
        assert izinler, f"{anahtar} izin ilan etmiyor (K9)"
        assert izinler == BEKLENEN[anahtar], f"{anahtar} izni değişmiş: {izinler}"


def test_duzenleme_ve_silme_ucu_yoktur() -> None:
    # Sözleşme `PATCH` ve `DELETE` tanımlamıyor: düzenlenebilen bir belge,
    # elindeki kâğıtla sistemdeki kayıt farklı olan bir müşteri üretir; silinen
    # bir belge seride "44 nerede" sorusunu cevapsız bırakır. Uç eklemek
    # sözleşmeyi tek taraflı değiştirmek olur.
    fiiller = {method for method, _ in _endpoints()}
    assert "PATCH" not in fiiller
    assert "DELETE" not in fiiller
    assert "PUT" not in fiiller


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi. Varsayılanı açık olan
    # bir kurulumda kuru prova sanılan istek GERÇEK BELGE keserdi.
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


def test_belge_kesme_govdesi_iki_kipi_de_tasir() -> None:
    # Kip alanlarının ikisi de gövdede duruyor; hangisinin dolu olacağına
    # servis karar veriyor. Alan adları sözleşmenin snake_case sözlüğünde.
    siparis = routes.CreateBody(reason=GEREKCE, order_id=8421)
    assert siparis.order_id == 8421
    assert siparis.subscription_id == 0

    donem = routes.CreateBody(reason=GEREKCE, subscription_id=18,
                              period_start="2026-08-01", period_end="2026-08-31",
                              subscription_payment_id=41)
    assert (donem.period_start, donem.period_end) == ("2026-08-01", "2026-08-31")

    # Uydurma alan 422 ile geri döner: sessizce düşen bir alan, gönderdiğini
    # sanan bir istemci üretir.
    with pytest.raises(ValidationError):
        routes.CreateBody(reason=GEREKCE, order_id=1, invoice_no="BLD-2026-000044")


def test_rapor_govdesi_yalniz_bilinen_alanlari_kabul_eder() -> None:
    govde = routes.PreviewBody(kind="invoice", invoice_id=44)
    assert govde.kind == "invoice"
    assert govde.status == ""

    with pytest.raises(ValidationError):
        routes.PreviewBody(kind="invoice", invoiceId=44)


def test_servis_baglanmadan_ucler_503_der() -> None:
    # Router modül düzeyinde duruyor; `bind()` çağrılmadan gelen bir istek
    # `None` üzerinde patlamak yerine anlaşılır bir 503 vermeli.
    routes._service = None
    with pytest.raises(Exception, match="hazır değil"):
        routes.service()
