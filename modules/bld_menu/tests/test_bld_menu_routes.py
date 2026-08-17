"""HTTP yüzeyinin sözleşmesi — izin kapıları, gerekçe politikası, gövde alanları.

Bu dosya iş kuralı sınamaz (o `test_bld_menu_service.py`'nin işi); ucun DIŞ
yüzeyini sabitler: her uç bir izin ilan ediyor mu (K9), silme uçları ayrı
anahtara mı bağlı, hangi uç gerekçe istiyor, ve panelden gelen gövde tam olarak
beklenen alan adlarını mı taşıyor.

İZİN İLE GEREKÇE AYRI SÜTUNLARDIR ve bilerek ayrı: `DELETE items` ayrı bir
izin anahtarı (`bld_menu.remove`) ister ama gerekçe İSTEMEZ. Tek sütuna
indirilseydi bu satır yazılamazdı — ve tam da o satır politikanın en kolay
yanlış anlaşılan yeri.
"""

from __future__ import annotations

import pytest
from bld_menu_backend.api import routes
from pydantic import ValidationError

GEREKCE = "17 Ağustos menüsü takvimden kuruldu"

#: Uç → (beklenen izinler, gerekçe istiyor mu). Tabloyu ELLE yazmak bilinçli:
#: `requires` çağrısını ve gövde modelini koddan okuyup kendine karşı
#: doğrulamak, hiçbir şey doğrulamazdı.
#:
#: DİKKAT — iki DELETE ucu `bld_menu.remove` ister, ötekiler `manage`. Bu
#: satırlar bir ayrımın kanıtı: yayından çekmek geri alınabilir, silmek değil.
#:
#: GEREKÇE SÜTUNUNUN ÖLÇÜTÜ: işlem müşteriye GÖRÜNÜR HÂLE GELİYOR mu ve GERİ
#: ALINMASI ZOR mu. Taslak kurmak ikisi de değil; yayınlamak ikisi de. Okumalar
#: gövde bile taşımıyor, onlarda sütun her zaman `False`.
BEKLENEN: dict[tuple[str, str], tuple[set[str], bool]] = {
    ("GET", "/calendar"): ({"bld_menu.view"}, False),
    ("GET", "/days/{date}"): ({"bld_menu.view"}, False),
    ("GET", "/days/{date}/stock"): ({"bld_menu.view"}, False),
    ("GET", "/products"): ({"bld_menu.view"}, False),
    ("POST", "/days"): ({"bld_menu.manage"}, False),
    ("PATCH", "/days/{date}"): ({"bld_menu.manage"}, False),
    ("DELETE", "/days/{date}"): ({"bld_menu.remove"}, True),
    ("POST", "/days/{date}/publish"): ({"bld_menu.manage"}, True),
    ("POST", "/days/{date}/unpublish"): ({"bld_menu.manage"}, True),
    ("POST", "/days/{date}/items"): ({"bld_menu.manage"}, False),
    ("PATCH", "/days/{date}/items/{item_id}"): ({"bld_menu.manage"}, False),
    ("DELETE", "/days/{date}/items/{item_id}"): ({"bld_menu.remove"}, False),
    ("POST", "/days/{date}/stock/preview"): ({"bld_menu.manage"}, False),
    ("PUT", "/days/{date}/stock"): ({"bld_menu.manage"}, False),
    ("POST", "/days/{date}/duplicate"): ({"bld_menu.manage"}, True),
}


def _endpoints() -> dict[tuple[str, str], object]:
    out = {}
    for route in routes.router.routes:
        for method in sorted(route.methods):
            out[(method, route.path)] = route
    return out


def _body_model(route: object) -> object | None:
    """Ucun gövde modeli — yoksa `None` (okuma uçları).

    FastAPI çözümlenmiş modeli `dependant.body_params` içinde tutuyor;
    fonksiyonun `__annotations__` değeri `from __future__ import annotations`
    yüzünden DİZE olduğu için oradan okunamaz.
    """
    for field in getattr(route.dependant, "body_params", []):  # type: ignore[attr-defined]
        return field.field_info.annotation
    return None


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
        assert izinler == BEKLENEN[anahtar][0], f"{anahtar} izni değişmiş: {izinler}"


def test_her_uc_gerekce_politikasindaki_govdeyi_tasir() -> None:
    """Gerekçe sütunu ile ucun GÖVDE MODELİ birbirini tutmalı.

    Politikayı yalnız belgeye yazmak yetmez: alanı olmayan bir gövdeye gerekçe
    göndermek 422 verir ve alanı olan bir gövdeye göndermemek de. Tablo ile kod
    ayrıştığı anda paneli hangi tarafın kırdığı belirsizleşir.
    """
    for anahtar, route in _endpoints().items():
        model = _body_model(route)
        ister = BEKLENEN[anahtar][1]
        if model is None:
            # Okuma uçları gövde taşımaz; gerekçe sütunu zorunlu olarak `False`.
            assert ister is False, f"{anahtar}: gövdesiz uç gerekçe isteyemez"
            continue
        assert ("reason" in model.model_fields) is ister, \
            f"{anahtar}: gövde modeli ({model.__name__}) gerekçe sütunuyla uyuşmuyor"


def test_silme_uclari_ucuncu_anahtara_bagli() -> None:
    # Ayrım kâğıt üstünde kalmasın: `manage` taşıyan biri günü silememelidir.
    # Yayından çekmek `manage`te KALIR — geri alınabilir bir şalter, geri
    # alınamaz bir silmeyle aynı kapıdan geçmemeli.
    silen = {yol for (metot, yol) in BEKLENEN if metot == "DELETE"}
    assert silen == {"/days/{date}", "/days/{date}/items/{item_id}"}
    for yol in silen:
        assert BEKLENEN[("DELETE", yol)][0] == {"bld_menu.remove"}
    assert BEKLENEN[("POST", "/days/{date}/unpublish")][0] == {"bld_menu.manage"}


def test_izin_ile_gerekce_ayri_sorulardir() -> None:
    """`DELETE items`: AYRI İZİN ister, gerekçe İSTEMEZ.

    Politikanın en kolay yanlış anlaşılan yeri burası. "Yıkıcı işlem gerekçe
    ister" diye özetlemek bu satırı yanlış yazdırırdı: silme iznini gerekçeye
    bağlayan bir kural, kalem silmeyi de gerekçeli yapardı ve menü kurarken
    yapılan olağan bir düzeltme yeniden yavaşlardı.

    Gün silme İKİSİNİ DE ister; ayrım ölçekte: gün, TÜM kalemleriyle gider.
    """
    izin, gerekce = BEKLENEN[("DELETE", "/days/{date}/items/{item_id}")]
    assert izin == {"bld_menu.remove"}
    assert gerekce is False

    izin, gerekce = BEKLENEN[("DELETE", "/days/{date}")]
    assert izin == {"bld_menu.remove"}
    assert gerekce is True


def test_kuru_prova_bayragi_yalniz_dryRun_adiyla_kabul_edilir() -> None:
    # `dry_run` da kabul edilseydi, yanlış yazılan ad sessizce düşer, alan
    # "hiç gönderilmemiş" sayılır ve varsayılana dönerdi. Bu modülde varsayılan
    # kapalı olduğu için zararsız görünür — ama alanı açıkça `true` göndermek
    # isteyen bir çağrı, adı yanlış yazdığında GERÇEK YAZMA yapardı.
    govde = routes.ReasonBody(reason=GEREKCE, dryRun=True)
    assert govde.dryRun is True
    assert govde.dry() is True

    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, dry_run=False)


def test_bayrak_gelmezse_gercek_yazmadir() -> None:
    # VARSAYILAN AYARDAN OKUNMAZ: `config/local.yaml` git dışıdır ve orada
    # `true` yazsaydı panelden yapılan her yazma sessizce provaya döner,
    # ekran "kaydedildi" derdi.
    assert routes.ReasonBody(reason=GEREKCE).dry() is False


def test_gerekce_semada_da_denetlenir() -> None:
    # Servis ayrıca denetliyor (K9 — çift kapı); buradaki kapı erken geri
    # bildirim içindir. Üst sınır 500'dür: sipariş revizyonundaki 160'lık
    # sınır bu alanda geçerli değil ve buraya kopyalanmadı.
    #
    # SINIRLAR POLİTİKAYLA DEĞİŞMEDİ: gerekçe artık az yerde isteniyor ama
    # istendiği yerde hâlâ 10–500 karakter. Sınırı gevşetmek yanlış çözüm
    # olurdu — sorun gerekçenin kendisinde değil, gereksiz yere sorulmasındaydı.
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="kısa")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason="x" * 501)
    assert routes.ReasonBody(reason="x" * 500).reason


def test_gerekce_istemeyen_govde_gerekceyi_sessizce_yutmaz() -> None:
    """Muaf bir gövdeye `reason` göndermek 422 verir — `extra="forbid"`.

    Sessizce yok saymak, gerekçe yazdığını sanan ve hiçbir yere yazılmayan bir
    metin bırakırdı. Panel bu uçlarda gerekçe SORMAMALI ve o kararın tek
    gürültülü kanıtı budur.
    """
    for model in (routes.DayCreateBody, routes.ItemCreateBody):
        with pytest.raises(ValidationError):
            model(date="2026-08-17", menu_id=27, reason=GEREKCE)
    with pytest.raises(ValidationError):
        routes.ItemPatchBody(quantity=2, reason=GEREKCE)
    with pytest.raises(ValidationError):
        routes.WriteBody(reason=GEREKCE)


def test_gerekce_istemeyen_govde_aktor_da_kabul_etmez() -> None:
    """`actor` GÖVDEDEN ALINMAZ — oturumdan gelir; politika bunu değiştirmedi.

    Gerekçeyi kaldırırken aktörü gövdeye açmak, denetim izini imzalanmamış bir
    deftere çevirirdi: silinmeyen bir satıra istediği adı yazan biri, işi
    başkasının üstüne bırakabilirdi.
    """
    with pytest.raises(ValidationError):
        routes.ItemCreateBody(menu_id=27, actor="Başkası")
    with pytest.raises(ValidationError):
        routes.ReasonBody(reason=GEREKCE, actor="Başkası")


def test_kismi_yazmada_gonderilmeyen_alan_govdeye_girmez() -> None:
    # `exclude_unset` kısmi yazmanın bel kemiği: alanı hiç göndermemek
    # "dokunma", `null` göndermek "boşalt". Varsayılanı `None` olan bir modelde
    # bu iki niyet ancak böyle ayrılabilir.
    dokunma = routes.DayPatchBody(title="Yeni başlık")
    assert dokunma.changes() == {"title": "Yeni başlık"}

    bosalt = routes.DayPatchBody(internal_note=None)
    assert bosalt.changes() == {"internal_note": None}
    assert "title" not in bosalt.changes()


def test_gun_govdesinde_tasima_ve_durum_alanlari_reddedilir() -> None:
    # `date`, `location_id` ve `status` bilerek yok. Sessizce yok sayılsalardı
    # yönetici günü taşıdığını ya da yayınladığını sanırdı.
    for alan in ("date", "location_id", "status"):
        with pytest.raises(ValidationError):
            routes.DayPatchBody(**{alan: "2026-08-18"})


def test_kalem_guncellemede_urun_degistirilemez() -> None:
    # Ürünü değiştirmek kalemi silip yenisini eklemektir ve denetim izinde iki
    # ayrı satır olarak görünmelidir.
    with pytest.raises(ValidationError):
        routes.ItemPatchBody(menu_id=27)
    assert routes.ItemCreateBody(menu_id=27).menu_id == 27


def test_stok_uygulama_govdesi_tabloyu_tasimaz() -> None:
    # Tablo jetonun arkasındadır: gövdede tekrar gönderilseydi, onaylanan tablo
    # ile uygulanan tablonun aynı olduğunu kanıtlamanın yolu kalmazdı.
    govde = routes.StockApplyBody(token="abcdefgh1234")
    assert govde.token == "abcdefgh1234"
    with pytest.raises(ValidationError):
        routes.StockApplyBody(token="abcdefgh1234", capacity_total=120)


def test_stok_onizleme_govdesi_tam_listedir() -> None:
    # Fark değil TAM LİSTE: `capacity_total` gönderilmezse `None` kalır ve
    # "tavan yok" anlamına gelir; alanın hiç gönderilmemesiyle `null`
    # gönderilmesi burada AYNI şeydir çünkü tablo bütünüyle yeniden yazılıyor.
    govde = routes.StockPreviewBody(items=[{"item_id": 901, "capacity": None}])
    assert govde.capacity_total is None
    assert govde.items == [{"item_id": 901, "capacity": None}]
