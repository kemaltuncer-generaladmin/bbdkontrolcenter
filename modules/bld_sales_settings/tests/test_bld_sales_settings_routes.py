"""HTTP yüzeyinin sözleşmesi — izin kapıları ve gövde alan adları.

Bu dosya iş kuralı sınamaz (o `test_bld_sales_settings_service.py`'nin işi);
ucun DIŞ yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), doğru izni mi
taşıyor ve panelden gelen gövde tam olarak beklenen alan adlarını mı taşıyor.
"""

from __future__ import annotations

import pytest
from bld_sales_settings_backend.api import routes
from pydantic import ValidationError

GEREKCE = "Kesim saati 08:00'e çekildi, ileri sipariş 7 güne indirildi"

#: Uç → beklenen izin(ler). Tabloyu ELLE yazmak bilinçli: `requires` çağrısını
#: koddan okuyup kendine karşı doğrulamak, hiçbir şey doğrulamazdı.
#:
#: ÜÇ İZİN AYRIMI BURADA SABİTLENİR. `ordering` anahtarı satış kanalının
#: açık/kapalı olmasını yönetir; `manage` satış açıkken geçerli kuralları
#: değiştirir. Bir gün biri `POST /ordering/pause` ucunu `manage`e düşürürse
#: bu test düşer — ve düşmesi gerekir: o değişiklik, kesim saatini
#: değiştirebilen herkese satışı kapatma yetkisi verirdi.
BEKLENEN = {
    ("GET", "/sales"): {"bld_sales_settings.view"},
    ("GET", "/closed-days"): {"bld_sales_settings.view"},
    ("GET", "/stock"): {"bld_sales_settings.view"},
    ("GET", "/audit"): {"bld_sales_settings.view"},
    ("GET", "/prefs"): {"bld_sales_settings.view"},
    ("PUT", "/sales"): {"bld_sales_settings.manage"},
    ("PUT", "/stock/{date}"): {"bld_sales_settings.manage"},
    ("POST", "/prefs"): {"bld_sales_settings.manage"},
    ("POST", "/ordering/pause"): {"bld_sales_settings.ordering"},
    ("POST", "/ordering/resume"): {"bld_sales_settings.ordering"},
    ("POST", "/closed-days"): {"bld_sales_settings.ordering"},
    ("DELETE", "/closed-days/{date}"): {"bld_sales_settings.ordering"},
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


def test_satis_salteri_ayri_izin_ister() -> None:
    # Ayrımın kendisi bir iddiadır ve tek satırda okunabilir olmalı: satışı
    # durduran/açan ve kapalı gün ekleyip kaldıran dört uç `manage` ile
    # AÇILAMAZ.
    salter = {("POST", "/ordering/pause"), ("POST", "/ordering/resume"),
              ("POST", "/closed-days"), ("DELETE", "/closed-days/{date}")}
    for anahtar in salter:
        assert BEKLENEN[anahtar] == {"bld_sales_settings.ordering"}
        assert "bld_sales_settings.manage" not in BEKLENEN[anahtar]


def test_kuru_prova_bayragi_iki_degerlidir() -> None:
    # ÜÇÜNCÜ HÂL YOK. `bld_kds`'teki `dryRun: bool|None` kalıbı burada
    # kullanılmadı: `None` dalı, modül ayarının (ya da git dışı
    # `config/local.yaml`'ın) yanlış olduğu bir kurulumda HER yazmayı sessizce
    # provaya çevirirdi ve bu ekranda o hata başarıdan ayırt edilemez.
    assert routes.ReasonBody(reason=GEREKCE).preview is False
    assert routes.ReasonBody(reason=GEREKCE, preview=True).preview is True

    # Yanlış yazılan ad sessizce DÜŞMEZ: `extra="forbid"` 422 üretir. Sessizce
    # düşseydi, kuru prova sandığı bir istek gerçek yazma yapardı.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=True)
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dryRun=True)


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 501)


def test_ayar_govdesi_yuvalidir() -> None:
    # Ayarlar `settings` altında durur, kökte değil: kökte olsalardı `reason`
    # ve `preview` ile aynı ad alanını paylaşırlardı. `GET /sales` yanıtındaki
    # `data` ile de simetrik.
    govde = routes.SalesBody(reason=GEREKCE, settings={"order_cutoff": "09:30"})
    assert govde.settings == {"order_cutoff": "09:30"}
    assert govde.token == ""

    with pytest.raises(ValidationError):
        routes.SalesBody(reason=GEREKCE, order_cutoff="09:30")


def test_durdurma_govdesi_musteri_mesajini_ayri_tasir() -> None:
    # `reason` MÜŞTERİYE GÖSTERİLMEZ; ikisinin ayrı alanlar olması bilinçlidir
    # ("buzdolabı arızası" cümlesi müşteriye söylenecek şey değildir).
    govde = routes.PauseBody(reason=GEREKCE, until=None,
                             customer_message="Bugün sipariş alamıyoruz.")
    assert govde.until is None
    assert govde.customer_message == "Bugün sipariş alamıyoruz."

    with pytest.raises(ValidationError):
        routes.PauseBody(reason=GEREKCE, customer_message="x" * 301)


def test_stok_govdesi_tam_liste_tasir() -> None:
    govde = routes.StockBody(reason=GEREKCE, capacity_total=120,
                             items=[{"item_id": 901, "capacity": None}])
    assert govde.capacity_total == 120
    assert govde.items[0]["item_id"] == 901

    # `null` gün tavanı GEÇERLİ ve "sınırsız" demektir; negatif değil.
    assert routes.StockBody(reason=GEREKCE).capacity_total is None
    with pytest.raises(ValidationError):
        routes.StockBody(reason=GEREKCE, capacity_total=-1)


def test_tercih_govdesi_gerekce_istemez() -> None:
    # Ekran tercihi BLD'ye hiç gitmez ve satışı etkilemez; gerekçe istemek
    # kullanıcıyı anlamsız bir kutuya zorlamak olurdu. Yazma izni yine de
    # `manage` — tablo kullanıcı başına değil kurulum başınadır.
    govde = routes.PrefBody(key="tab", value="stock")
    assert govde.key == "tab"
    with pytest.raises(ValidationError):
        routes.PrefBody(key="tab", value="stock", reason=GEREKCE)
