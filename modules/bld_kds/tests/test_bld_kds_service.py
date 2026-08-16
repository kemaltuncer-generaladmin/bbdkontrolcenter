"""KDS servisi — iş kuralları. Ağa çıkmaz; `bld.api` taklit edilir."""

from __future__ import annotations

import json
from typing import Any

from bld_kds_backend.service import KdsService
from bld_kds_fakes import DEVICE, ORDER, FakeApi, FakeBus, FakeLog, FakeStore

GEREKCE = "Yazıcı arızalandı, mutfak fiş basamıyor"


def _service(*, devices: list[dict[str, Any]] | None = None,
             orders: dict[int, dict[str, Any]] | None = None,
             **config: Any) -> tuple[KdsService, FakeApi, FakeStore, FakeBus]:
    api = FakeApi(devices=[dict(DEVICE)] if devices is None else devices,
                  orders=orders if orders is not None else {12: dict(ORDER)})
    store = FakeStore()
    bus = FakeBus()
    service = KdsService(api=api, store=store, log=FakeLog(), config=dict(config),
                         publish=bus)
    return service, api, store, bus


# ====================================================== K7 — ekran ayakta kalır

async def test_gecit_duserse_cihaz_ekrani_ayakta_kalir() -> None:
    # Mutfak çalışmaya devam ediyor. Yönetici "sunucuya ulaşılamıyor" ile
    # "cihaz kapandı" arasındaki farkı GÖRMEK zorunda; boş liste ikisini de
    # aynı gösterirdi.
    service, api, _, _ = _service()
    api.fail.add("devices")
    sonuc = await service.devices()
    assert sonuc["ok"] is True                # uç patlamaz
    assert sonuc["connected"] is False
    assert sonuc["items"] == []
    assert "patladı" in sonuc["error"]
    # Ayar sözleşmesi YEREL: geçit düşse bile form çizilebilir.
    assert len(sonuc["settings_spec"]) == 24
    assert len(sonuc["commands"]) == 8
    assert len(sonuc["sound_events"]) == 5


async def test_gecit_duserse_ozet_komut_fis_ve_siparis_uclari_da_ayakta_kalir() -> None:
    service, api, _, _ = _service()
    api.fail.update({"overview", "device_commands", "print_jobs",
                     "orders", "order_revisions"})
    for cagri in (service.overview(), service.device_commands(3), service.print_jobs(),
                  service.orders(), service.order_revisions(12)):
        sonuc = await cagri
        assert sonuc["ok"] is True
        assert sonuc["connected"] is False
        assert sonuc["error"]


async def test_gecit_duserse_tek_siparis_ucu_ok_false_der_ama_patlamaz() -> None:
    service, api, _, _ = _service()
    api.fail.add("order")
    sonuc = await service.order(12)
    assert sonuc["ok"] is False and sonuc["connected"] is False
    assert sonuc["order"] == {}


async def test_yazma_sirasinda_gecit_patlarsa_iz_hata_ile_kapanir() -> None:
    service, api, store, _ = _service()
    api.fail.add("send_command")
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="Kemal Tunçer", dry_run=False)
    assert sonuc["ok"] is False
    # "Ne yapmaya çalıştık" kaydı ağ koparsa geriye kalan TEK kanıttır.
    assert store.results("command:test_receipt") == ["denendi", "hata"]


async def test_denetim_izi_yazilamazsa_is_durmaz() -> None:
    service, api, store, _ = _service()
    store.broken = True
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="Kemal Tunçer", dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("send_command")


async def test_olay_dinleyicisi_patlarsa_is_yine_basarilidir() -> None:
    service, _, _, bus = _service()
    bus.fail = True
    sonuc = await service.revoke_device(3, reason=GEREKCE, actor="Kemal Tunçer",
                                        dry_run=False, allow_destructive=True)
    # Cihaz BLD'de iptal edildi; dinleyicinin patlaması onu geri getirmez.
    assert sonuc["ok"] is True


# ============================================================ gerekçe kapısı

async def test_gerekcesiz_yazma_reddedilir() -> None:
    # Arayüzde alanı zorunlu göstermek yetkilendirme değildir (K9): istemci
    # gövdeyi elle kurabilir.
    service, api, _, _ = _service()
    cagrilar = [
        service.create_device(name="Yeni Kasa", reason="kısa", actor="K"),
        service.rename_device(3, name="Yeni Ad", reason="kısa", actor="K"),
        service.pairing_code(3, reason="", actor="K"),
        service.revoke_device(3, reason="kısa", actor="K", allow_destructive=True),
        service.push_settings(3, settings={"poll_seconds": 9}, reason="kısa", actor="K"),
        service.send_command(3, command="test_receipt", reason="kısa", actor="K"),
        service.create_revision(12, items=[{"menu_id": 4, "quantity": 1}], reason="kısa",
                                actor="K"),
        service.set_order_status(12, status="hazir", reason="kısa", actor="K"),
    ]
    for cagri in cagrilar:
        sonuc = await cagri
        assert sonuc["ok"] is False
        assert "Gerekçe" in sonuc["error"]
    assert api.writes() == []


# ============================================================== kuru prova
#
# K-22 §4 kuru provayı ARAYÜZDEN kaldırdı ve varsayılanı kapattı; PARAMETRE
# ile testleri DURUYOR. Sözleşme §4 additive: `dry_run=True` veren bir çağrı
# hâlâ prova yapmalı, yoksa geriye dönük uyumsuz bir kaldırma olurdu.

async def test_kuru_provada_uzaga_cagri_gitmez_ama_iz_yazilir() -> None:
    service, api, store, bus = _service()
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="Kemal Tunçer", dry_run=True)
    assert sonuc["ok"] is True and sonuc["dry_run"] is True
    assert api.writes() == []                        # BLD'ye tek satır yazılmadı
    assert store.results("command:test_receipt") == ["denendi", "dry_run"]
    assert bus.events == []                          # kuru provada olay yok
    assert sonuc["would"]["command"] == "test_receipt"


async def test_kuru_prova_varsayilan_kapalidir() -> None:
    # K-22 §4: panel `dryRun` alanını artık HİÇ göndermiyor. Varsayılan açık
    # kalsaydı panelden yapılan her yazma sessizce bir provaya döner, ekran
    # "yazıldı" der ve kasada hiçbir şey değişmezdi.
    service, api, _, _ = _service()
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="K")
    assert sonuc["dry_run"] is False
    assert api.used("send_command")[0]["dry_run"] is False


async def test_kuru_prova_ayardan_yine_de_acilabilir() -> None:
    # Varsayılanı gitti, YETENEĞİ değil: bir kurulum provayı geri açabilir.
    service, api, _, _ = _service(dry_run_default=True)
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="K")
    assert sonuc["dry_run"] is True
    assert api.writes() == []


async def test_gerekce_zorunlulugu_kuru_prova_kalkinca_da_durur() -> None:
    # Kuru prova bir GÜVENLİK AĞIYDI, gerekçe bir DENETİM KAYDI. Ağ kalktı
    # diye kayıt kalkmaz; ikisi ayrı şeylerdir (K-22 §4).
    service, api, _, _ = _service()
    sonuc = await service.send_command(3, command="test_receipt", reason="kısa",
                                       actor="K")
    assert sonuc["ok"] is False and "Gerekçe" in sonuc["error"]
    assert api.writes() == []


async def test_kuru_provada_olay_yayinlanmaz_gerceginde_yayinlanir() -> None:
    service, _, _, bus = _service()
    await service.revoke_device(3, reason=GEREKCE, actor="K", dry_run=True,
                                allow_destructive=True)
    assert bus.events == []

    await service.revoke_device(3, reason=GEREKCE, actor="K", dry_run=False,
                                allow_destructive=True)
    assert bus.names() == ["bld_kds.device_revoked"]
    assert bus.events[0][1]["deviceId"] == 3


async def test_durum_degisikligi_kuru_provada_olay_yayinlamaz() -> None:
    service, _, _, bus = _service()
    await service.set_order_status(12, status="hazir", reason=GEREKCE, actor="K",
                                   dry_run=True)
    assert bus.events == []

    await service.set_order_status(12, status="hazir", reason=GEREKCE, actor="K",
                                   dry_run=False)
    assert bus.names() == ["bld_kds.order_status_changed"]
    assert bus.events[0][1]["to"] == "hazir"


# ======================================================= yıkıcı işlem kapıları

async def test_cihaz_iptali_ayri_izin_ister() -> None:
    # `bld_kds.manage` taşıyan biri kasayı iptal EDEMEZ. Uç noktada da
    # denetlenir; burada tekrar denetlenmesi K9'un çift kapısıdır.
    service, api, store, bus = _service()
    sonuc = await service.revoke_device(3, reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is False
    assert "bld_kds.devices" in sonuc["error"]
    assert api.calls == []                       # taze okuma bile yapılmadı
    assert store.results("revoke_device") == ["engellendi"]
    assert bus.events == []


async def test_yikici_komutlar_ayri_izin_ister_zararsizlar_istemez() -> None:
    service, api, store, _ = _service()
    for komut in ("restart", "clear_failed"):
        sonuc = await service.send_command(3, command=komut, reason=GEREKCE, actor="K",
                                           dry_run=False)
        assert sonuc["ok"] is False
        assert "bld_kds.devices" in sonuc["error"]
        assert store.results(f"command:{komut}") == ["engellendi"]
    assert api.writes() == []

    izinli = await service.send_command(3, command="restart", reason=GEREKCE, actor="K",
                                        dry_run=False, allow_destructive=True)
    assert izinli["ok"] is True
    assert api.used("send_command")[0]["command"] == "restart"

    zararsiz = await service.send_command(3, command="silence_alarm", reason=GEREKCE,
                                          actor="K", dry_run=False)
    assert zararsiz["ok"] is True


async def test_yeni_komutlar_da_ayri_izin_ister() -> None:
    # `update` kurulum boyunca ekranı alır, `unpair` kasanın token'ını siler ve
    # kasa sahada elle eşleştirilene kadar HİÇ sipariş göremez, `clear_queue`
    # bekleyen fişleri de düşürür. Üçü de `bld_kds.manage` ile yapılamaz.
    service, api, store, _ = _service()
    for komut in ("update", "unpair", "clear_queue"):
        sonuc = await service.send_command(3, command=komut, reason=GEREKCE, actor="K",
                                           dry_run=False)
        assert sonuc["ok"] is False
        assert "bld_kds.devices" in sonuc["error"]
        assert store.results(f"command:{komut}") == ["engellendi"]
    assert api.writes() == []

    for komut in ("update", "unpair", "clear_queue"):
        izinli = await service.send_command(3, command=komut, reason=GEREKCE, actor="K",
                                            dry_run=False, allow_destructive=True)
        assert izinli["ok"] is True
    # YÜKSÜZ komutta geçide `payload=None` gider: boş sözlük göndermek,
    # sözleşmede olmayan bir `"payload": {}` alanı taşımak olurdu.
    assert [cagri["command"] for cagri in api.used("send_command")] == [
        "update", "unpair", "clear_queue"]
    assert all(cagri["payload"] is None for cagri in api.used("send_command"))


async def test_bilinmeyen_komut_reddedilir_ve_uzaga_gitmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.send_command(3, command="shutdown", reason=GEREKCE, actor="K",
                                       dry_run=False, allow_destructive=True)
    assert sonuc["ok"] is False
    assert "tanınan bir komut değil" in sonuc["error"]
    assert api.calls == []


async def test_izin_denetimi_gerekce_denetiminden_once_gelir() -> None:
    # Yetkisiz kişiye "gerekçen kısa" demek, yetkisi olduğunu düşündürür ve
    # doğru gerekçeyle tekrar denemesine yol açar.
    service, _, store, _ = _service()
    sonuc = await service.send_command(3, command="restart", reason="kısa", actor="K")
    assert "bld_kds.devices" in sonuc["error"]
    assert store.results("command:restart") == ["engellendi"]


# ============================================================== taze okuma

async def test_yazmadan_once_cihaz_taze_okunur() -> None:
    service, api, _, _ = _service()
    await service.rename_device(3, name="Mutfak 2", reason=GEREKCE, actor="K",
                                dry_run=False)
    assert [ad for ad, _, _ in api.calls] == ["devices", "rename_device"]


async def test_iptal_edilmis_cihaza_komut_gitmez() -> None:
    service, api, store, _ = _service(
        devices=[{**DEVICE, "revoked_at": "2026-08-13T10:00:00Z", "online": False}])
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="K", dry_run=False)
    assert sonuc["ok"] is False
    assert "İptal edilmiş" in sonuc["error"]
    assert api.writes() == []
    assert store.results("command:test_receipt") == ["engellendi"]


async def test_iptal_edilmis_cihaza_esleme_kodu_uretilmez() -> None:
    # YETKİ GEREKÇESİ: iptal `bld_kds.devices`e, kod üretimi `bld_kds.manage`e
    # bağlı. Kod üretilebilseydi yalnız `manage` taşıyan biri, `devices` taşıyan
    # kişinin kararını geri alır ve iptal edilen kasayı mutfağa döndürürdü.
    service, api, _, _ = _service(
        devices=[{**DEVICE, "revoked_at": "2026-08-13T10:00:00Z", "online": False}])
    sonuc = await service.pairing_code(3, reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is False
    assert "yeniden eşleştirilemez" in sonuc["error"]
    assert api.writes() == []


async def test_ikinci_kez_iptal_engellenir() -> None:
    service, api, _, _ = _service(
        devices=[{**DEVICE, "revoked_at": "2026-08-13T10:00:00Z", "online": False}])
    sonuc = await service.revoke_device(3, reason=GEREKCE, actor="K", dry_run=False,
                                        allow_destructive=True)
    assert sonuc["ok"] is False and "zaten iptal" in sonuc["error"]
    assert api.writes() == []


async def test_ayni_adli_ikinci_cihaz_acilmaz() -> None:
    # Aynı adlı iki kasa, iptal düğmesine basan kişiyi yazı tura atmaya bırakır.
    service, api, _, _ = _service()
    sonuc = await service.create_device(name="mutfak kasası", reason=GEREKCE, actor="K",
                                        dry_run=False)
    assert sonuc["ok"] is False and "zaten var" in sonuc["error"]
    assert api.writes() == []


async def test_cevrimdisi_cihaza_komut_engellenmez_ama_soylenir() -> None:
    # Kapanmış bir kasaya "aç" diyememek olurdu; komut kuyrukta bekler.
    service, api, _, _ = _service(devices=[{**DEVICE, "online": False}])
    sonuc = await service.send_command(3, command="test_receipt", reason=GEREKCE,
                                       actor="K", dry_run=False)
    assert sonuc["ok"] is True
    assert sonuc["queued_offline"] is True
    assert api.used("send_command")


async def test_bulunmayan_cihaz_anlasilir_hata_verir() -> None:
    service, api, _, _ = _service(devices=[])
    sonuc = await service.rename_device(99, name="Yok", reason=GEREKCE, actor="K")
    assert sonuc["ok"] is False and sonuc["error"] == "Cihaz bulunamadı."
    assert api.writes() == []


# =============================================================== ayar itme

async def test_degismeyen_ayar_icin_uca_istek_gitmez() -> None:
    # Aynı değeri yeniden yazmak `settings_updated_at` damgasını ileri atar ve
    # "en son ne zaman dokunuldu" sorusunun cevabını bozar.
    service, api, _, _ = _service()
    sonuc = await service.push_settings(3, settings={"poll_seconds": 5}, reason=GEREKCE,
                                        actor="K", dry_run=False)
    assert sonuc["ok"] is True and sonuc["changed"] is False
    assert api.writes() == []


async def test_aralik_disi_ayar_uca_hic_gitmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.push_settings(3, settings={"poll_seconds": 2}, reason=GEREKCE,
                                        actor="K", dry_run=False)
    assert sonuc["ok"] is False and sonuc["errors"]
    assert api.calls == []


async def test_kilit_kapatan_ayar_onizlemede_uyari_dondurur() -> None:
    service, _, _, _ = _service()
    sonuc = await service.push_settings(3, settings={"allow_order_edit": False},
                                        reason=GEREKCE, actor="K", dry_run=True)
    assert sonuc["dry_run"] is True
    assert any("kapanacak" in uyari for uyari in sonuc["warnings"])
    assert sonuc["diff"][0]["lock"] is True


async def test_kuru_prova_jetonu_uretir_ve_gercek_uygulamada_dogrulanir() -> None:
    service, api, store, _ = _service()
    prova = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=True)
    token = prova["token"]
    assert token and store.previews[token]["status"] == "dry_run"
    assert api.writes() == []

    gercek = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                         actor="K", dry_run=False, token=token)
    assert gercek["ok"] is True
    assert api.used("update_device_settings")[0]["settings"] == {"poll_seconds": 9}
    assert store.previews[token]["status"] == "applied"


async def test_onizlemeden_farkli_istek_jetonla_gecemez() -> None:
    # Onaylanan tablo neyse o uygulanır.
    service, api, _, _ = _service()
    prova = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=True)
    sonuc = await service.push_settings(3, settings={"poll_seconds": 45}, reason=GEREKCE,
                                        actor="K", dry_run=False, token=prova["token"])
    assert sonuc["ok"] is False and "önizlemeden farklı" in sonuc["error"]
    assert api.writes() == []


async def test_aradan_ayar_degistiyse_onizleme_gecersizdir() -> None:
    # İki yönetici aynı cihaza yazarsa ikincisi birincinin değişikliğini
    # GÖRMEDEN ezerdi.
    service, api, _, _ = _service()
    prova = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=True)
    api.device_rows = [{**DEVICE, "settings": {**DEVICE["settings"], "volume_percent": 20}}]
    sonuc = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=False, token=prova["token"])
    assert sonuc["ok"] is False and "ayarları değişti" in sonuc["error"]
    assert api.writes() == []


async def test_suresi_dolmus_onizleme_reddedilir() -> None:
    service, api, store, _ = _service()
    prova = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=True)
    store.previews[prova["token"]]["created_at"] = "2026-01-01T00:00:00Z"
    sonuc = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=False, token=prova["token"])
    assert sonuc["ok"] is False and "kuru provayı tekrarlayın" in sonuc["error"]
    assert api.writes() == []


async def test_jeton_zorunlu_degildir() -> None:
    # Sözleşmede jeton isteyen bir uç yok; verilmezse yazma yine çalışır.
    service, api, _, _ = _service()
    sonuc = await service.push_settings(3, settings={"poll_seconds": 9}, reason=GEREKCE,
                                        actor="K", dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("update_device_settings")


async def test_olay_bazli_ses_ayari_uca_kanonik_dizeyle_gider() -> None:
    service, api, _, _ = _service()
    sonuc = await service.push_settings(
        3, settings={"disabled_sound_events": "bbdOrder, newOrder"},
        reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is True
    gonderilen = api.used("update_device_settings")[0]["settings"]
    assert gonderilen == {"disabled_sound_events": "newOrder,bbdOrder"}


async def test_kapatilamayan_ses_olayi_uca_hic_gitmez() -> None:
    # Sunucu sessizce eliyor; Kontrol Merkezi söylüyor. Sessizce elenen bir ad,
    # "kapattım" sanan yöneticiye hiçbir şey söylemezdi.
    service, api, _, _ = _service()
    sonuc = await service.push_settings(
        3, settings={"disabled_sound_events": "connectionLost"},
        reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is False and sonuc["errors"]
    assert api.calls == []


async def test_sesleri_acmak_bos_dizeyle_yazilir_null_ile_degil() -> None:
    # `null` kasayı kendi listesiyle baş başa bırakır; "hepsini aç" demenin
    # tek yolu boş dizedir.
    service, api, _, _ = _service(
        devices=[{**DEVICE, "settings": {**DEVICE["settings"],
                                         "disabled_sound_events": "newOrder"}}])
    sonuc = await service.push_settings(3, settings={"disabled_sound_events": ""},
                                        reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("update_device_settings")[0]["settings"] == {
        "disabled_sound_events": ""}


async def test_kilidi_kaldirmak_icin_null_yazilir() -> None:
    service, api, _, _ = _service(
        devices=[{**DEVICE, "settings": {**DEVICE["settings"], "allow_settings": False}}])
    sonuc = await service.push_settings(3, settings={"allow_settings": None},
                                        reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("update_device_settings")[0]["settings"] == {"allow_settings": None}


# ================================================================ revizyon

async def test_revizyon_tam_liste_gonderir() -> None:
    service, api, store, _ = _service()
    kalemler = [{"menu_id": 4, "quantity": 3}, {"menu_id": 5, "quantity": 1}]
    sonuc = await service.create_revision(12, items=kalemler, reason="Müşteri kalem ekledi",
                                          actor="Kemal Tunçer", dry_run=False)
    assert sonuc["ok"] is True
    gonderilen = api.used("create_order_revision")[0]
    # Kalem FARKI değil, siparişin YENİ hâli gider.
    assert gonderilen["items"] == kalemler
    assert gonderilen["reason"] == "Müşteri kalem ekledi"
    assert gonderilen["actor"] == "Kemal Tunçer"
    assert store.results("create_revision") == ["denendi", "ok"]


async def test_revizyon_kalemi_ayiklanmadan_gecer() -> None:
    # SEÇENEK KİMLİĞİ DÜŞERSE "ekstra peynir" silinir, sipariş ucuzlar, mutfak
    # yanlış yemeği yapar ve hata hiçbir yerde görünmez. Bilinen alanları seçip
    # gerisini atan bir dönüşüm tam da bunu yapardı.
    service, api, _, _ = _service()
    kalem = {"menu_id": 4, "quantity": 2, "option_value_ids": [7, 9], "note": "az acı",
             "order_menu_id": 88, "name": "Mercimek Çorbası"}
    await service.create_revision(12, items=[kalem], reason="Müşteri kalem ekledi",
                                  actor="K", dry_run=False)
    assert api.used("create_order_revision")[0]["items"] == [kalem]


async def test_bos_revizyon_listesi_reddedilir_ve_uca_gitmez() -> None:
    service, api, _, _ = _service()
    sonuc = await service.create_revision(12, items=[], reason="Müşteri kalem ekledi",
                                          actor="K", dry_run=False)
    assert sonuc["ok"] is False
    assert "TAM kalem listesini" in sonuc["error"]
    assert api.calls == []


async def test_revizyon_yazmadan_once_siparis_taze_okunur() -> None:
    service, api, _, _ = _service()
    await service.create_revision(12, items=[{"menu_id": 4, "quantity": 1}],
                                  reason="Müşteri kalem ekledi", actor="K", dry_run=False)
    assert [ad for ad, _, _ in api.calls] == ["order", "create_order_revision"]


async def test_okunamayan_siparise_revizyon_yazilmaz() -> None:
    service, api, _, _ = _service(orders={})
    sonuc = await service.create_revision(99, items=[{"menu_id": 4, "quantity": 1}],
                                          reason="Müşteri kalem ekledi", actor="K",
                                          dry_run=False)
    assert sonuc["ok"] is False
    assert api.writes() == []


# ============================================================== durum akışı

async def test_matris_disi_gecis_uca_gitmez() -> None:
    service, api, store, _ = _service()
    sonuc = await service.set_order_status(12, status="teslim_edildi", reason=GEREKCE,
                                           actor="K", dry_run=False)
    assert sonuc["ok"] is False
    assert api.writes() == []
    assert store.results("set_status") == ["engellendi"]


async def test_gecerli_gecis_uca_gider_ve_izi_kapanir() -> None:
    service, api, store, _ = _service()
    sonuc = await service.set_order_status(12, status="hazir", reason=GEREKCE, actor="K",
                                           dry_run=False)
    assert sonuc["ok"] is True
    assert api.used("set_order_status")[0]["status"] == "hazir"
    assert store.results("set_status") == ["denendi", "ok"]


# ============================================================ okuma biçimleri

async def test_ozet_yazici_yetenegini_bildirir() -> None:
    service, _, _, _ = _service()
    sonuc = await service.overview()
    assert sonuc["printer_available"] is False       # yetenek verilmedi
    assert sonuc["devices"]["offline"] == 1
    assert sonuc["print_jobs"]["today"] == 42


async def test_gecitte_olmayan_urun_listesi_ekrani_dusurmez() -> None:
    # `bld.api` bu turda `menu()` taşımıyor. Metodu doğrudan çağırmak
    # `AttributeError` ile ekranı düşürürdü; uç nedenini yazıp boş döner (K7).
    service, api, _, _ = _service()
    assert not hasattr(api, "menu")
    sonuc = await service.menu()
    assert sonuc["ok"] is False
    assert sonuc["data"] == []
    assert "elle girin" in sonuc["error"]


async def test_urun_listesi_geldiginde_satirlar_ayiklanmadan_gecer() -> None:
    service, api, _, _ = _service()
    urunler = [{"menu_id": 4, "name": "Mercimek Çorbası", "price_kurus": 4500,
                "options": [{"id": 7, "name": "Ekstra peynir"}]}]

    async def _menu() -> Any:
        return {"data": urunler}

    api.menu = _menu                                # type: ignore[attr-defined]
    sonuc = await service.menu()
    assert sonuc["ok"] is True and sonuc["connected"] is True
    # Seçenek ağacı BLD'nin sözlüğüdür ve büyüyor; ayıklanmaz.
    assert sonuc["data"] == urunler


async def test_urun_listesi_patlarsa_ekran_ayakta_kalir() -> None:
    service, api, _, _ = _service()

    async def _patlar() -> Any:
        raise RuntimeError("menu patladı")

    api.menu = _patlar                              # type: ignore[attr-defined]
    sonuc = await service.menu()
    assert sonuc["ok"] is False and sonuc["data"] == []
    assert "patladı" in sonuc["error"]


async def test_fis_limiti_kirpilmaz_gecide_oldugu_gibi_gider() -> None:
    # Ayardaki 100 bir VARSAYILANDIR, tavan değil: panel 200 satır istiyor ve
    # sessizce 100 alsaydı eksikliği yalnız listenin dibine bakan biri görürdü.
    service, api, _, _ = _service()
    await service.print_jobs(limit=200)
    assert api.used("print_jobs")[0]["limit"] == 200

    await service.print_jobs()
    assert api.used("print_jobs")[1]["limit"] == 100


async def test_komut_limiti_gecide_gonderilmez_yerelde_kirpilir() -> None:
    # Geçidin `device_commands` metodu limit ALMIYOR; göndermek `TypeError`
    # ile ekranı düşürürdü. Sunucu zaten son 50'yi tutuyor.
    service, api, _, _ = _service()
    api.command_rows = [{"id": index, "command": "test_receipt"} for index in range(10)]
    sonuc = await service.device_commands(3, limit=4)
    assert api.used("device_commands") == [{}]      # anahtar argüman gitmedi
    assert len(sonuc["items"]) == 4


async def test_fis_ucu_kuyruk_olmadigini_soyler() -> None:
    # "0 kayıt" ile "kuyruk boş" aynı şey değildir: KDS'in disk kuyruğu
    # sunucuda YOK, bekleyen iş sayısı cihaz sağlığından okunur.
    service, _, _, _ = _service()
    sonuc = await service.print_jobs()
    assert sonuc["audit_only"] is True


async def test_liste_yaniti_data_items_ve_duz_liste_bicimlerini_cozer() -> None:
    # Geçit zarfı normalde kendi açıyor ve DÜZ DİZİ veriyor; yine de tek bir
    # sarmalayıcı adına bağlanmak, geçidin bir metodu zarfı açmadan geçtiğinde
    # ekranı SESSİZCE boş gösterirdi.
    service, api, _, _ = _service()
    for govde in ({"data": [dict(DEVICE)]}, {"items": [dict(DEVICE)]}, [dict(DEVICE)]):
        api.devices = _sabit(govde)                 # type: ignore[method-assign]
        sonuc = await service.devices()
        assert len(sonuc["items"]) == 1
        assert sonuc["items"][0]["name"] == "Mutfak Kasası"


def _sabit(govde: Any) -> Any:
    async def _cagri() -> Any:
        return govde
    return _cagri


async def test_esleme_kodu_denetim_izine_yazilmaz() -> None:
    # Kod bir SIRDIR ve iz satırı silinmez; koda yazsaydık kodun ömrü 10 dakika
    # yerine sonsuz olurdu.
    service, _, store, _ = _service()
    sonuc = await service.pairing_code(3, reason=GEREKCE, actor="K", dry_run=False)
    assert sonuc["pairing"]["code"] == "ABCD-2345"
    izler = json.dumps(store.audit, ensure_ascii=False)
    assert "ABCD-2345" not in izler


async def test_aktor_ize_yazilir() -> None:
    service, _, store, _ = _service()
    await service.send_command(3, command="test_receipt", reason=GEREKCE,
                               actor="Kemal Tunçer", dry_run=False)
    assert {row["actor"] for row in store.audit} == {"Kemal Tunçer"}
