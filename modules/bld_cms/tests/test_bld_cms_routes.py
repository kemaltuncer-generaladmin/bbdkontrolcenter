"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_cms_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9) ve panelden gelen gövde
tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_cms_backend.api import routes
from pydantic import ValidationError

GEREKCE = "İletişim telefonu güncellendi"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
BEKLENEN = {
    ("GET", "/content"): {"bld_cms.view"},
    ("GET", "/services"): {"bld_cms.view"},
    ("GET", "/posts"): {"bld_cms.view"},
    ("GET", "/revisions"): {"bld_cms.view"},
    ("GET", "/revisions/{revision_id}"): {"bld_cms.view"},
    ("PUT", "/content/{key}"): {"bld_cms.manage"},
    ("POST", "/services"): {"bld_cms.manage"},
    ("PATCH", "/services/{service_id}"): {"bld_cms.manage"},
    ("POST", "/posts"): {"bld_cms.manage"},
    ("PATCH", "/posts/{post_id}"): {"bld_cms.manage"},
    # Yeniden çizdirme YIKICI DEĞİLDİR: hiçbir kaydı değiştirmez, yalnız
    # yayındaki sayfayı depodaki hâline eşitler. `delete` iznine bağlamak,
    # yazan kişiyi yazdığını yayınlayamaz duruma düşürürdü.
    ("POST", "/revalidate"): {"bld_cms.manage"},
    ("POST", "/images"): {"bld_cms.manage"},
    # YIKICI: sözleşme yumuşak silme sunmuyor, kayıt geri gelmez.
    ("DELETE", "/services/{service_id}"): {"bld_cms.delete"},
    ("DELETE", "/posts/{post_id}"): {"bld_cms.delete"},
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


def test_silme_uclari_ayri_bir_izne_baglidir() -> None:
    # Ayrımın kendisi bir iş kuralıdır: "sitede görünmesin" (`is_published`)
    # `manage`e düşer, "kayıt yok olsun" `delete`e. İkisi tek anahtara
    # bağlansaydı, sayfayı gizlemek isteyen herkes sayfayı yok edebilirdi.
    silme = {anahtar for anahtar, izinler in BEKLENEN.items()
             if izinler == {"bld_cms.delete"}}
    assert silme == {("DELETE", "/services/{service_id}"),
                     ("DELETE", "/posts/{post_id}")}


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi. Varsayılan bugün
    # kapalı — ama geçidin `config/local.yaml` dosyası git dışıdır ve orada
    # açık olabilir; o kurulumda kuru prova sanılan istek gerçek yazma yapardı.
    govde = routes.WriteBody(reason=GEREKCE, dryRun=False)
    assert govde.dryRun is False

    with pytest.raises(ValidationError):
        routes.WriteBody(reason=GEREKCE, dry_run=False)

    # Alan hiç verilmezse `None` kalır ve varsayılanı SERVİS uygular.
    assert routes.WriteBody(reason=GEREKCE).dryRun is None


def test_tazeleme_bayragi_uc_degerlidir() -> None:
    # `None` = "modül ayarı ne diyorsa". Panel açık bir seçim yaptığında
    # gönderir; hiç göndermediğinde ayar geçerlidir ve o AÇIK. İki değerli
    # olsaydı (varsayılan `False`) olağan akış "kaydettim ama sitede yok" ile
    # biterdi.
    assert routes.WriteBody(reason=GEREKCE).revalidate is None
    assert routes.WriteBody(reason=GEREKCE, revalidate=False).revalidate is False
    assert routes.WriteBody(reason=GEREKCE, revalidate=True).revalidate is True


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir. Üst sınır 500: `00-genel.md` §3 panel uçları için
    # bunu söylüyor.
    with pytest.raises(ValidationError):
        routes.WriteBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.WriteBody(reason="x" * 501)
    assert routes.WriteBody(reason="x" * 500).reason


def test_kayit_alanlari_fields_altinda_yuvalidir() -> None:
    # Alanlar kökte olsaydı `reason`, `dryRun` ve `revalidate` ile aynı ad
    # alanını paylaşırlardı ve sözleşmeye `reason` adında bir alan
    # eklenemezdi. `extra="forbid"` yanlış yazılan alanı 422 ile geri verir.
    govde = routes.ServiceBody(reason=GEREKCE, fields={"title": "Etkinlik Catering"})
    assert govde.fields == {"title": "Etkinlik Catering"}

    with pytest.raises(ValidationError):
        routes.ServiceBody(reason=GEREKCE, title="Etkinlik Catering")


def test_icerik_govdesi_semasizdir() -> None:
    # `value` ŞEMASIZDIR: sunucu da içeriği doğrulamıyor (cms.md), yalnız
    # geçerli JSON olduğunu ve boyutunu denetliyor. Buraya bir şema koymak,
    # site yeni bir alan eklediğinde Kontrol Merkezi'nde de değişiklik
    # gerektirirdi — ve o değişiklik gelene kadar alan yazılamazdı.
    assert routes.ContentBody(reason=GEREKCE, value={"phone": "3124445577"}).value \
        == {"phone": "3124445577"}
    assert routes.ContentBody(reason=GEREKCE, value=[{"q": "s", "a": "c"}]).value \
        == [{"q": "s", "a": "c"}]
    assert routes.ContentBody(reason=GEREKCE).value is None


def test_yol_listesi_govdededir_sorguda_degil() -> None:
    # Sorgu dizesi İMZAYA GİRMİYOR (`00-genel.md` §1): yol süzgeçleri isteğe
    # girer, imzaya girmez. Yeniden çizdirilecek yolları sorguya koymak,
    # imzalanmamış bir alanla yayındaki sayfaları seçmek olurdu.
    govde = routes.RevalidateBody(reason=GEREKCE, paths=["/hizmetler"])
    assert govde.paths == ["/hizmetler"]
    assert routes.RevalidateBody(reason=GEREKCE).paths is None
