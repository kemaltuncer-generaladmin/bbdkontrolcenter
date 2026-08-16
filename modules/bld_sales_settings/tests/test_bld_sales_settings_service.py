"""Satış Ayarları servisi — iş kuralları. Ağa çıkmaz; `bld.api` taklit edilir."""

from __future__ import annotations

import json
from typing import Any

from bld_sales_settings_backend.service import SalesSettingsService
from bld_sales_settings_fakes import FakeApi, FakeBus, FakeLog, FakeStore

GEREKCE = "Kesim saati 08:00'e çekildi, ileri sipariş 7 güne indirildi"


def _service(**config: Any) -> tuple[SalesSettingsService, FakeApi, FakeStore, FakeBus]:
    api = FakeApi()
    store = FakeStore()
    bus = FakeBus()
    service = SalesSettingsService(api=api, store=store, log=FakeLog(),
                                   config=dict(config), publish=bus)
    return service, api, store, bus


def _call(api: FakeApi, name: str) -> list[dict[str, Any]]:
    return [payload for called, payload in api.calls if called == name]


# ====================================================== K7 — ekran ayakta kalır


async def test_gecit_duserse_okumalar_firlatmaz() -> None:
    # Yönetici "sunucuya ulaşılamıyor" ile "ayar yok" arasındaki farkı GÖRMEK
    # zorunda; boş bir form ikisini de aynı gösterirdi.
    service, api, _, _ = _service()
    api.fail.update({"sales_settings", "closed_days", "menu_stock"})

    ayarlar = await service.sales()
    assert ayarlar["ok"] is True and ayarlar["connected"] is False
    assert ayarlar["error"] and ayarlar["data"] == {}

    gunler = await service.closed_days()
    assert gunler["ok"] is True and gunler["connected"] is False and gunler["items"] == []

    stok = await service.stock(dates=["2026-08-17"])
    assert stok["ok"] is True and stok["connected"] is False
    assert stok["days"][0]["connected"] is False


async def test_ipuclari_okunamazsa_ayar_ekrani_yine_dolar() -> None:
    # `meta` yalnız gri ipucu metinleridir; onlar için ekranı boş bırakmak
    # yönetim ekranını kullanılamaz yapardı.
    service, api, _, _ = _service()
    api.fail.add("settings_reference")
    sonuc = await service.sales()
    assert sonuc["connected"] is True
    assert sonuc["data"]["order_cutoff"] == "08:00"
    assert sonuc["meta"] == {}


async def test_yarinin_menusu_yoksa_bugunun_stogu_yine_gorunur() -> None:
    # Yarın için menü henüz kurulmamış olabilir; bu bir hata değil, olağan bir
    # durumdur ve bugünün şeridini düşürmemeli.
    service, _, _, _ = _service()
    sonuc = await service.stock(dates=["2026-08-17", "2026-08-18"])
    assert sonuc["connected"] is True
    assert sonuc["days"][0]["connected"] is True
    assert sonuc["days"][1]["connected"] is False
    assert sonuc["days"][1]["code"] == "not_found"


# ============================================== kuru prova — üç kapının kanıtı


async def test_her_yazmada_acik_dry_run_gecilir() -> None:
    # Geçidin varsayılanına ASLA güvenilmez: `config/local.yaml` git dışıdır ve
    # orada `true` yazıyor olabilir. Bayrağı atlayan bir modül hiçbir şey
    # yazmadan `{"ok": true}` alır.
    service, api, _, _ = _service()
    okuma = await service.sales()

    await service.update_sales(settings={"order_cutoff": "09:30"}, reason=GEREKCE,
                               actor="Ayşe", token=okuma["baseline_token"])
    await service.pause(until=None, customer_message=None, reason=GEREKCE, actor="Ayşe")
    await service.resume(reason=GEREKCE, actor="Ayşe")
    await service.add_closed_day(date="2026-12-31", description=None, reason=GEREKCE,
                                 actor="Ayşe")
    await service.remove_closed_day(date="2026-08-30", reason=GEREKCE, actor="Ayşe")
    await service.set_stock(date="2026-08-17", capacity_total=120,
                            items=[{"item_id": 901, "capacity": None},
                                   {"item_id": 902, "capacity": 40}],
                            reason=GEREKCE, actor="Ayşe")

    yazmalar = [payload for name, payload in api.calls
                if name in ("update_sales_settings", "pause_ordering", "resume_ordering",
                            "create_closed_day", "delete_closed_day", "set_menu_stock")]
    assert len(yazmalar) == 6
    for payload in yazmalar:
        assert "dry_run" in payload, "geçide açık dry_run geçilmemiş"
        assert payload["dry_run"] is False


async def test_sunucu_kuru_prova_yaparsa_islem_basarisiz_sayilir() -> None:
    # ÜÇÜNCÜ KAPI. Ekran "kaydedildi" deseydi, yönetici bunu ancak ertesi
    # sabah kesim saatinin eski değerde olduğunu görünce anlardı.
    service, api, store, _ = _service()
    okuma = await service.sales()
    api.force_dry_run = True

    sonuc = await service.update_sales(settings={"order_cutoff": "09:30"},
                                       reason=GEREKCE, actor="Ayşe",
                                       token=okuma["baseline_token"])
    assert sonuc["ok"] is False
    assert "KURU PROVA" in sonuc["error"]
    assert "HİÇBİR AYAR YAZILMADI" in sonuc["error"]
    # Sunucudaki değer gerçekten değişmedi.
    assert api.sales_data["order_cutoff"] == "08:00"
    # İz "hata" ile kapanır; "ok" yazılmaz.
    assert store.results("settings.sales") == ["denendi", "hata"]


async def test_onizleme_uzakta_hicbir_sey_degistirmez_ve_olay_yayinlamaz() -> None:
    service, api, store, bus = _service()
    sonuc = await service.pause(until=None, customer_message="Bugün kapalıyız.",
                                reason=GEREKCE, actor="Ayşe", preview=True)
    assert sonuc["ok"] is True and sonuc["preview"] is True
    assert _call(api, "pause_ordering")[0]["dry_run"] is True
    assert api.sales_data["ordering_enabled"] is True     # değişmedi
    # Kuru provada OLAY YAYINLANMAZ: BLD'de hiçbir şey değişmedi, dinleyicileri
    # "satış durduruldu" diye uyandırmak yalan olurdu.
    assert bus.events == []
    assert store.results("settings.ordering.pause") == ["denendi", "onizleme"]


async def test_onizleme_gecitten_hic_cikmadiysa_soylenir() -> None:
    # Geçit, kuru prova defterinde olmayan bir yola isteği HİÇ göndermiyor.
    # Ekrandaki tablo o zaman sunucudan gelmiyor demektir.
    service, api, _, _ = _service()
    api.force_not_sent = True
    sonuc = await service.resume(reason=GEREKCE, actor="Ayşe", preview=True)
    assert sonuc["ok"] is False
    assert "HİÇ GÖNDERİLMEDİ" in sonuc["error"]


async def test_sunucu_degisiklik_yok_derken_fark_gonderildiyse_uyarilir() -> None:
    # Sessiz geçmek, bu ekranın en pahalı hatasını görünmez yapardı.
    service, api, _, _ = _service()
    okuma = await service.sales()
    api.force_empty_changed = True
    sonuc = await service.update_sales(settings={"order_cutoff": "09:30"},
                                       reason=GEREKCE, actor="Ayşe",
                                       token=okuma["baseline_token"])
    assert sonuc["ok"] is True
    assert sonuc["changed"] == []
    assert "warning" in sonuc


# ==================================================== yoğunluk yarışı (busy)


async def test_mutfak_yogunlugu_degistirdiyse_ustune_yazilmaz() -> None:
    # Yönetici formu 09:00'da açtı, mutfak 09:10'da yoğunluğu açtı, yönetici
    # 09:30'da kaydediyor. Yarım saat önceki hâli geri yazmak, mutfağın
    # kararını sessizce iptal etmek olurdu.
    service, api, store, _ = _service()
    okuma = await service.sales()
    api.sales_data["busy"] = True                      # mutfak ekranı değiştirdi

    sonuc = await service.update_sales(settings={"busy": False}, reason=GEREKCE,
                                       actor="Ayşe", token=okuma["baseline_token"])
    assert sonuc["ok"] is False
    assert sonuc["conflict"] is True
    assert sonuc["conflicts"][0]["field"] == "busy"
    assert api.sales_data["busy"] is True              # dokunulmadı
    assert _call(api, "update_sales_settings") == []   # istek HİÇ gitmedi
    assert store.results("settings.sales") == ["engellendi"]


async def test_dokunulmayan_alan_yaristan_etkilenmez() -> None:
    # Mutfağın değiştirdiği bir anahtar yüzünden kesim saati kaydını reddetmek,
    # ekranı kullanılamaz yapardı.
    service, api, _, _ = _service()
    okuma = await service.sales()
    api.sales_data["busy"] = True

    sonuc = await service.update_sales(settings={"order_cutoff": "09:30"},
                                       reason=GEREKCE, actor="Ayşe",
                                       token=okuma["baseline_token"])
    assert sonuc["ok"] is True
    assert api.sales_data["order_cutoff"] == "09:30"
    assert api.sales_data["busy"] is True              # mutfağın kararı duruyor
    # Gövdede YALNIZ kirli alan var: tam gövde göndermek `busy`yi geri çevirirdi.
    assert _call(api, "update_sales_settings")[0]["fields"] == {"order_cutoff": "09:30"}


async def test_yoklama_okumasi_yeni_taban_uretmez() -> None:
    # Panel dakikada bir "mutfak yoğunluğu açtı mı" diye bakıyor. Her bakışta
    # yeni jeton üretmek, yarım saattir açık duran formun tabanını sessizce
    # "şu an" hâline çeker ve yarış denetimini işlevsiz bırakırdı — tam da
    # engellemek için var olduğu şeyi.
    service, api, store, _ = _service()
    form = await service.sales()
    assert form["baseline_token"]
    assert len(store.baselines) == 1

    for _ in range(5):
        yoklama = await service.sales(baseline=False)
        assert yoklama["baseline_token"] == ""
    assert len(store.baselines) == 1, "yoklama taban çizgisi tablosunu şişiriyor"

    # Eski jeton hâlâ geçerli ve yarışı yakalıyor.
    api.sales_data["busy"] = True
    sonuc = await service.update_sales(settings={"busy": False}, reason=GEREKCE,
                                       actor="Ayşe", token=form["baseline_token"])
    assert sonuc["ok"] is False and sonuc["conflict"] is True


async def test_jetonsuz_yazma_kabul_edilir_ama_yaris_yakalanamaz() -> None:
    # Eski bir istemciyi kırmak yerine, yarışı yakalayamadığını bilmek yeterli.
    service, api, _, _ = _service()
    api.sales_data["busy"] = True
    sonuc = await service.update_sales(settings={"busy": False}, reason=GEREKCE,
                                       actor="Ayşe", token="")
    assert sonuc["ok"] is True
    assert api.sales_data["busy"] is False


# ================================================================ yazma zinciri


async def test_iz_gecit_cagrisindan_once_dusulur() -> None:
    # Ağ koparsa geriye YALNIZ bu satır kalır: "kim neyi denedi" sorusunun
    # cevabı uzak kayıtta YOK, çünkü uzak kayıt yalnız ULAŞAN isteği bilir.
    service, api, store, _ = _service()
    okuma = await service.sales()
    api.fail.add("update_sales_settings")

    sonuc = await service.update_sales(settings={"prep_minutes": 55}, reason=GEREKCE,
                                       actor="Ayşe", token=okuma["baseline_token"])
    assert sonuc["ok"] is False
    assert store.results("settings.sales") == ["denendi", "hata"]
    assert json.loads(store.actions("settings.sales")[0]["detail"])["changes"][0]["field"] \
        == "prep_minutes"


async def test_ayni_degeri_yeniden_yazmak_istek_uretmez() -> None:
    # "Kaydedildi" demek, yöneticiye olmayan bir değişikliği bildirmek olurdu.
    service, api, store, _ = _service()
    okuma = await service.sales()
    sonuc = await service.update_sales(settings={"order_cutoff": "08:00"},
                                       reason=GEREKCE, actor="Ayşe",
                                       token=okuma["baseline_token"])
    assert sonuc["ok"] is True
    assert sonuc["changes"] == [] and sonuc["changed"] == []
    assert "yazma yapılmadı" in sonuc["note"]
    assert _call(api, "update_sales_settings") == []
    assert store.results("settings.sales") == ["ok"]


async def test_gerekce_serviste_de_denetlenir() -> None:
    # Arayüzde zorunlu göstermek yetkilendirme değildir (K9): istemci gövdeyi
    # elle kurabilir.
    service, api, _, _ = _service()
    for cagri in (
        service.update_sales(settings={"busy": True}, reason="kısa", actor="Ayşe"),
        service.pause(until=None, customer_message=None, reason="kısa", actor="Ayşe"),
        service.resume(reason="kısa", actor="Ayşe"),
        service.add_closed_day(date="2026-12-31", description=None, reason="kısa",
                               actor="Ayşe"),
        service.remove_closed_day(date="2026-08-30", reason="kısa", actor="Ayşe"),
        service.set_stock(date="2026-08-17", capacity_total=None, items=[],
                          reason="kısa", actor="Ayşe"),
    ):
        sonuc = await cagri
        assert sonuc["ok"] is False
        assert "Gerekçe" in sonuc["error"]
    assert api.calls == []          # hiçbiri sunucuya gitmedi


async def test_iz_yazilamazsa_is_durmaz() -> None:
    service, api, store, _ = _service()
    store.broken = True
    sonuc = await service.resume(reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert api.sales_data["ordering_enabled"] is True


async def test_dinleyici_patlarsa_islem_yine_basarilidir() -> None:
    # Satış BLD'de durduruldu; dinleyicinin patlaması onu geri açmaz (K7).
    service, api, _, bus = _service()
    bus.fail = True
    sonuc = await service.pause(until=None, customer_message=None, reason=GEREKCE,
                                actor="Ayşe")
    assert sonuc["ok"] is True
    assert api.sales_data["ordering_enabled"] is False


# ================================================================ satış şalteri


async def test_durdurma_olayi_yayinlanir_ve_musteri_mesaji_ayri_gider() -> None:
    service, api, _, bus = _service()
    sonuc = await service.pause(until=None, customer_message="Teknik arıza.",
                                reason="Buzdolabı arızalandı, satış durduruldu",
                                actor="Ayşe")
    assert sonuc["ok"] is True
    cagri = _call(api, "pause_ordering")[0]
    # `reason` müşteriye GÖSTERİLMEZ; ikisi ayrı alanlardır.
    assert cagri["customer_message"] == "Teknik arıza."
    assert cagri["reason"] != cagri["customer_message"]
    assert bus.events[0][0] == "bld_sales_settings.ordering_paused"


async def test_durdurma_bitisi_gecmisteyse_istek_gitmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.pause(until="2026-08-16T08:00:00Z", customer_message=None,
                                reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is False
    assert "geçmişte" in sonuc["error"]
    assert _call(api, "pause_ordering") == []


async def test_zaten_acik_satisi_acmak_hata_degildir() -> None:
    # İşlem sonuç odaklıdır; "zaten açıktı" diye hata vermek, yöneticiyi
    # satışın kapalı olduğuna inandırırdı.
    service, _, _, bus = _service()
    sonuc = await service.resume(reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert sonuc["already_open"] is True
    assert bus.events[0][0] == "bld_sales_settings.ordering_resumed"


# ================================================================ kapalı günler


async def test_kapali_gun_eklenir_ve_cakisma_soylenir() -> None:
    service, api, _, _ = _service()
    sonuc = await service.add_closed_day(date="2026-12-31", description="Yılbaşı",
                                         reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert any(row["date"] == "2026-12-31" for row in api.closed)

    tekrar = await service.add_closed_day(date="2026-12-31", description="Yılbaşı",
                                          reason=GEREKCE, actor="Ayşe")
    assert tekrar["ok"] is False
    assert tekrar["code"] == "conflict"


async def test_kayitli_olmayan_kapali_gun_silinmeye_calisilirsa_soylenir() -> None:
    # "Zaten öyle" hoşgörüsü uygulanmaz: var olmayan bir tatili silmeye çalışan
    # yönetici muhtemelen yanlış tarihe bakıyor ve bunu bilmeli.
    service, _, store, _ = _service()
    sonuc = await service.remove_closed_day(date="2026-11-11", reason=GEREKCE,
                                            actor="Ayşe")
    assert sonuc["ok"] is False
    assert "kayıtlı değil" in sonuc["error"]
    assert store.results("settings.closed_day.delete") == ["denendi", "hata"]


# ======================================================================= stok


async def test_stok_yazmasi_tam_liste_ister() -> None:
    # Menüye aradan kalem eklenmişse ekrandaki liste eksiktir ve eksik gönderim
    # o kalemin tavanını SESSİZCE kaldırırdı.
    service, api, _, _ = _service()
    sonuc = await service.set_stock(date="2026-08-17", capacity_total=120,
                                    items=[{"item_id": 901, "capacity": None}],
                                    reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is False
    assert sonuc["stale"] is True
    assert "Tavuk Sote" in sonuc["error"]
    assert _call(api, "set_menu_stock") == []


async def test_stok_yazmasi_uyarilari_aynen_tasir() -> None:
    # Tavanı satılmışın altına çekmek serbesttir ve 409 vermez; ama yanıt bunu
    # açıkça söyler ve ekran onu yazar.
    service, api, store, _ = _service()
    sonuc = await service.set_stock(date="2026-08-17", capacity_total=120,
                                    items=[{"item_id": 901, "capacity": None},
                                           {"item_id": 902, "capacity": 40}],
                                    reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is True
    assert sonuc["warnings"][0]["code"] == "capacity_below_sold"
    assert sonuc["changes"][0]["item_id"] == 902
    assert api.stock_data["2026-08-17"]["items"][1]["capacity"] == 40
    assert store.results("menu.stock") == ["denendi", "ok"]


async def test_menude_olmayan_kalem_gonderilirse_reddedilir() -> None:
    service, api, _, _ = _service()
    sonuc = await service.set_stock(date="2026-08-17", capacity_total=120,
                                    items=[{"item_id": 901, "capacity": None},
                                           {"item_id": 902, "capacity": 40},
                                           {"item_id": 903, "capacity": 10}],
                                    reason=GEREKCE, actor="Ayşe")
    assert sonuc["ok"] is False
    assert "bulunmayan kalem" in sonuc["error"]
    assert _call(api, "set_menu_stock") == []


# ================================================================ yerel tablo


async def test_denetim_izi_ve_tercihler_yerelde_kalir() -> None:
    service, api, _, _ = _service()
    await service.resume(reason=GEREKCE, actor="Ayşe")
    iz = await service.audit(limit=10)
    assert iz["ok"] is True
    assert iz["items"][0]["action"] == "settings.ordering.resume"

    once = len(api.calls)
    yazma = await service.set_pref(key="tab", value="stock", actor="Ayşe")
    assert yazma["ok"] is True
    tercih = await service.prefs()
    assert tercih["tab"] == "stock"
    # Tercih BLD'ye HİÇ gitmez.
    assert len(api.calls) == once

    assert (await service.set_pref(key="uydurma", value="1", actor="Ayşe"))["ok"] is False


async def test_vitrin_kimligi_sifirsa_gecide_gonderilmez() -> None:
    # 0 = varsayılan vitrin. Sıfırı geçirmek, olmayan bir vitrine yazmaya
    # çalışmak olurdu.
    service, api, _, _ = _service(location_id=0)
    await service.sales()
    assert _call(api, "sales_settings")[0]["location_id"] is None

    service, api, _, _ = _service(location_id=4)
    await service.sales()
    assert _call(api, "sales_settings")[0]["location_id"] == 4
