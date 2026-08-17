"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_status_monitor_service.py`'nin işi);
ucun DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden
gelen gövde tam olarak beklenen alan adlarını mı taşıyor.

ROTA ↔ SERVİS AYRIŞMASI da burada yakalanır. Rota dosyasındaki `service().X()`
çağrısı ile servisteki metot adının ayrışması ne açılışta ne `route:list`'te
hata verir; yalnız uç çağrılınca patlar. Aşağıdaki son test bunu ÇAĞIRMADAN
sabitler.
"""

from __future__ import annotations

import pytest
from bld_status_monitor_backend.api import routes
from bld_status_monitor_backend.service import StatusMonitorService
from bld_status_monitor_fakes import FakeApi, FakeStore, make_service
from pydantic import ValidationError

GEREKCE = "Yazıcı kablosu değiştirildi, deneme fişi başarılı"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/overview"): {"bld_status_monitor.view"},
    ("GET", "/summary"): {"bld_status_monitor.view"},
    ("GET", "/devices"): {"bld_status_monitor.view"},
    ("GET", "/events"): {"bld_status_monitor.view"},
    ("GET", "/events/{event_id}"): {"bld_status_monitor.view"},
    ("GET", "/log"): {"bld_status_monitor.view"},
    ("GET", "/history"): {"bld_status_monitor.view"},
    ("GET", "/audit"): {"bld_status_monitor.view"},
    ("GET", "/runbook"): {"bld_status_monitor.view"},
    ("POST", "/events/{event_id}/resolve"): {"bld_status_monitor.manage"},
    ("PUT", "/runbook/{key}"): {"bld_status_monitor.manage"},
    ("POST", "/runbook/{key}/run"): {"bld_status_monitor.manage"},
    ("PUT", "/prefs"): {"bld_status_monitor.view"},
}

#: Uç → servisin çağrılan metodu. Rota dosyası adları SABİTLER; servis ona
#: uyar. İki taraf ayrışırsa uç çağrılana kadar hiçbir şey hata vermez.
SERVIS_METOTLARI = {
    "overview", "summary", "devices", "events", "event", "local_log", "history",
    "audit", "runbook", "resolve_event", "save_runbook", "run_runbook", "save_prefs",
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


def test_okuma_uclari_manage_istemez() -> None:
    # İzleme ekranının OKUMA tarafı `view` ile açılmalı: yönetici sorunu
    # görebilmeli ama komut göndermek için ayrı bir anahtar istemeli. Okumaya
    # `manage` koymak, ekranı yalnız komut verebilenlere açardı ve "bir şey
    # çalışmıyor" diyen kişi hiçbir şey göremezdi.
    for (method, path), route in _endpoints().items():
        if method != "GET":
            continue
        assert _declared(route) == {"bld_status_monitor.view"}, path


def test_rota_dosyasindaki_servis_metotlari_serviste_var() -> None:
    # TUZAK 2: rota ile denetleyici metot adının ayrışması ne açılışta ne
    # `route:list`'te hata verir — yalnız uç çağrılınca patlar. Faz 1+2'de 13
    # test bu yüzden düşmüştü.
    for name in SERVIS_METOTLARI:
        assert callable(getattr(StatusMonitorService, name, None)), \
            f"servis '{name}' metodunu taşımıyor; rota onu çağırıyor"


class _Kullanici:
    """Oturumun testlik yüzü. `actor` GÖVDEDEN DEĞİL buradan gelir."""

    full_name = "Ayşe Yılmaz"

    def has_permission(self, key: str, scope: str | None = None) -> bool:
        return True


async def test_her_uc_gercekten_cagrilabiliyor() -> None:
    # ADI TUTAN AMA İMZASI TUTMAYAN bir metot, yukarıdaki `getattr` testini
    # geçer ve yalnız uç çağrılınca patlar. On üç ucun on üçü burada
    # GERÇEKTEN çağrılır; argümanlar FastAPI'nin çözeceği değerlerle verilir
    # (uçları doğrudan çağırınca `Query(...)` nesneleri varsayılan kalır ve
    # süzgeç denetimi onları metin sanardı).
    routes.bind(make_service(api=FakeApi(), store=FakeStore()))
    user = _Kullanici()
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=False)

    assert (await routes.overview(user=user))["ok"] is True
    assert (await routes.summary(user=user))["connected"] is True
    assert (await routes.devices(user=user))["items"]
    assert (await routes.events(
        source="", level="", code="", device_id=0, since="", resolved="", q="",
        page=1, per_page=0, user=user))["items"]
    assert (await routes.event(3311, user=user))["ok"] is True
    assert (await routes.local_log(
        source="", result="", kind="", q="", limit=0, user=user))["ok"] is True
    assert (await routes.history(limit=0, user=user))["items"]
    assert (await routes.audit(limit=50, user=user))["ok"] is True
    assert (await routes.runbook(user=user))["ok"] is True

    assert (await routes.resolve_event(
        3311, routes.ResolveBody(reason=GEREKCE, dryRun=False), user=user))["ok"] is True
    assert (await routes.save_runbook("yazici.test", routes.RunbookBody(
        reason=GEREKCE, title="Yazıcı arızasında test fişi", channel="bld.api",
        action="kds.test_receipt", device_id=2), user=user))["ok"] is True
    assert (await routes.run_runbook("yazici.test", govde, user=user))["ok"] is True
    assert (await routes.save_prefs(
        routes.PrefsBody(poll_seconds=90), user=user))["ok"] is True


async def test_modul_baglanmadan_uc_cagrilirsa_503_doner() -> None:
    # Bağlanmamış bir router `None` servise giderdi ve `AttributeError` ile
    # 500 verirdi; 503 "henüz hazır değil" demektir ve tekrar denenebilir.
    from km_sdk import HTTPException

    routes._service = None
    with pytest.raises(HTTPException) as hata:
        routes.service()
    assert hata.value.status_code == 503
    routes.bind(make_service(api=FakeApi(), store=FakeStore()))


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi — yani kuru prova
    # sanılan bir istek GERÇEK KOMUT gönderirdi.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=True)
    assert govde.dryRun is True

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=True)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.ReasonBody(reason=GEREKCE).dryRun is None


def test_gerekce_semada_da_denetlenir_ve_ust_sinir_160() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 161)
    assert routes.ReasonBody(reason="x" * 160).reason


def test_cozum_govdesi_notu_500_karakterle_sinirlar() -> None:
    # `note` İSTEĞE BAĞLI ve sözleşmede en çok 500 karakter. Birleştirmeyi
    # (reason + "\n" + note) SUNUCU yapar; burada tekrarlanmaz.
    assert routes.ResolveBody(reason=GEREKCE).note == ""
    assert routes.ResolveBody(reason=GEREKCE, note="x" * 500).note
    with pytest.raises(ValidationError):
        routes.ResolveBody(reason=GEREKCE, note="x" * 501)
    with pytest.raises(ValidationError):
        routes.ResolveBody(reason=GEREKCE, notlar="x")


def test_defter_govdesi_bilinmeyen_alani_reddeder() -> None:
    govde = routes.RunbookBody(reason=GEREKCE, title="Yazıcı arızasında test fişi",
                               channel="bld.api", action="kds.test_receipt",
                               device_id=2)
    assert govde.enabled is True
    assert govde.device_id == 2
    # Bilinmeyen alan 422 ile döner: sessizce düşen bir alan, gönderildiğini
    # sanan kullanıcıya hiçbir şey söylemezdi.
    with pytest.raises(ValidationError):
        routes.RunbookBody(reason=GEREKCE, title="x" * 5, komut="restart")
    with pytest.raises(ValidationError):
        routes.RunbookBody(reason=GEREKCE, title="ab")


def test_tercih_govdesi_yalniz_uc_anahtar_tanir_ve_alt_siniri_korur() -> None:
    assert set(routes.PrefsBody.model_fields) == {
        "poll_seconds", "page_size", "auto_refresh"}
    # 15 saniyenin altı paylaşılan `bld-control-panel` kovasını boşuna yakar.
    with pytest.raises(ValidationError):
        routes.PrefsBody(poll_seconds=5)
    with pytest.raises(ValidationError):
        routes.PrefsBody(page_size=1000)
    assert routes.PrefsBody(poll_seconds=60).poll_seconds == 60
