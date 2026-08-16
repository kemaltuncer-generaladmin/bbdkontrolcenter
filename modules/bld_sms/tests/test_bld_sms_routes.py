"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_sms_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen gövde
tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_sms_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Sipariş SMS metnine teslim saati eklendi"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/templates"): {"bld_sms.view"},
    ("GET", "/log"): {"bld_sms.view"},
    ("GET", "/announcement"): {"bld_sms.view"},
    ("GET", "/history"): {"bld_sms.view"},
    # YEREL hesap: ağa çıkmaz, denetim satırı yazmaz, gerekçe istemez.
    ("POST", "/measure"): {"bld_sms.view"},
    # SUNUCU önizlemesi: hiçbir SMS göndermez, sözleşme `view` ile verir.
    ("POST", "/templates/{key}/preview"): {"bld_sms.view"},
    ("PATCH", "/templates/{key}"): {"bld_sms.manage"},
    ("POST", "/send-test"): {"bld_sms.manage"},
    ("PUT", "/announcement"): {"bld_sms.manage"},
    # İKİ İZİN, "en az biri": kuru prova `manage` ile, gerçek gönderim
    # `announce` ile. Ayrım gövdeye bakılarak yapılır ve servis
    # `allow_send` ile ayrıca denetler.
    ("POST", "/announcement/run"): {"bld_sms.manage", "bld_sms.announce"},
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
    # açık kalır. Toplu duyuruda bu, herkesin yüzlerce SMS gönderebilmesi
    # demektir.
    bulunan = _endpoints()
    assert set(bulunan) == set(BEKLENEN), "uç listesi sözleşmeden ayrıştı"

    for anahtar, route in bulunan.items():
        izinler = _declared(route)
        assert izinler, f"{anahtar} izin ilan etmiyor (K9)"
        assert izinler == BEKLENEN[anahtar], f"{anahtar} izni değişmiş: {izinler}"


def test_toplu_gonderim_ayri_izin_anahtari_ister() -> None:
    # Şablon metnini düzeltmek ile yüzlerce müşteriye SMS göndermek aynı yetki
    # olamaz. Bu iddia tek satırda okunabilir dursun: `announce` YALNIZ
    # gönderim ucundadır ve gönderim ucu `manage`i de kabul eder (prova için).
    izinliler = {anahtar for anahtar, izinler in BEKLENEN.items()
                 if "bld_sms.announce" in izinler}
    assert izinliler == {("POST", "/announcement/run")}


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve modül varsayılanına dönerdi. Varsayılan
    # kapalı olduğu için toplu duyuruda bu, prova sanılan bir isteğin GERÇEK
    # GÖNDERİM olması demekti.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=True)
    assert govde.dryRun is True

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=True)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.ReasonBody(reason=GEREKCE).dryRun is None


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir. Sınırlar 00-genel.md §3'ten: 10–500.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 501)
    assert routes.ReasonBody(reason="x" * 500).reason


def test_sablon_govdesinde_title_alani_yoktur() -> None:
    # Şablonun ADI sistemin kendi sözlüğüdür. `extra="forbid"` sayesinde `title`
    # göndermeye çalışan bir çağrı SESSİZCE YOK SAYILMAZ, 422 alır — yönetici
    # adı değiştirdiğini sanıp kaydettiğinde ekran ona "oldu" dememeli.
    with pytest.raises(ValidationError):
        routes.TemplateBody(reason=GEREKCE, title="Yeni ad")

    # Kısmi yazma: iki alan da isteğe bağlı ve `None` "dokunulmadı" demektir.
    govde = routes.TemplateBody(reason=GEREKCE)
    assert govde.body is None
    assert govde.enabled is None


def test_olcum_govdesi_gerekce_istemez() -> None:
    # Zorunlu bir gerekçe, her tuş vuruşunda cümle yazdırırdı. Bu uç yerel bir
    # hesaptır: ağa çıkmaz, denetim satırı yazmaz.
    govde = routes.MeasureBody(body="Sayın {customer_name}", key="order_created")
    assert govde.sample == {}
    assert govde.allowed is None
    assert not hasattr(govde, "reason")


def test_onizleme_ornek_verisi_bos_sozluk_ile_none_ayri() -> None:
    # `sample` verilmezse sunucu GERÇEKÇİ örnek değerler üretir; boş sözlük
    # göndermek o davranışı kapatır ve önizleme "Sayın , siparişiniz…" diye
    # görünürdü. İkisi ayrı tutulmazsa bu ayrım kaybolur.
    assert routes.PreviewBody(reason=GEREKCE).sample is None
    assert routes.PreviewBody(reason=GEREKCE, sample={}).sample == {}


def test_duyuru_gonderim_govdesi_jeton_tasir() -> None:
    # Jeton kuru provanın çıktısıdır ve GERÇEK gönderimde şarttır. Alanın
    # varlığı burada sabitlenir; kuralın kendisi serviste sınanır.
    govde = routes.RunBody(reason=GEREKCE, confirm_recipients=186, token="abc")
    assert govde.confirm_recipients == 186
    assert govde.token == "abc"

    with pytest.raises(ValidationError):
        routes.RunBody(reason=GEREKCE, confirm_recipients=-1)
