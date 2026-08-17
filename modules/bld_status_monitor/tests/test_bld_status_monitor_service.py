"""İş kuralları — yerel gözlem defteri, yazma zinciri ve K7.

Bu dosyanın en önemli iki iddiası:

 1. UZAK SİSTEM DÜŞTÜĞÜNDE EKRAN AYAKTA KALIR ve bunu SÖYLER (K7). Okuma uçları
    `{"ok": True, "connected": False}` döner; istisna dışarı sızmaz.
 2. SUNUCUYA ULAŞMAYAN HATA YEREL DEFTERE DÜŞER. Bu modülün var olma sebebi bu
    ve başka hiçbir yerde karşılığı yok.
"""

from __future__ import annotations

from bld_status_monitor_backend import monitor as mon
from bld_status_monitor_fakes import (
    HEALTHY,
    FakeApi,
    FakeBus,
    FakeStore,
    make_service,
)

GEREKCE = "Yazıcı kablosu değiştirildi, deneme fişi başarılı"
AKTOR = "Ayşe Yılmaz"


# ================================================================== açılış

async def test_acilis_aga_cikmaz() -> None:
    # İzleme ekranının, izlediği sistem düştüğü için AÇILMAMASI sorunun
    # kendisini görünmez yapardı. Sözleşme, süzgeçler ve gerekçe sınırları
    # sunucusuz çizilebilmeli.
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.overview()
    assert sonuc["ok"] is True
    assert sonuc["connected"] is None
    assert api.names() == []
    assert sonuc["contract"]["components"]


# =================================================================== özet

async def test_ozet_tek_uc_yoklar() -> None:
    # `GET /summary` gövdesi `devices` bloğunu zaten taşıyor; kutuları çizmek
    # için ayrıca `/devices` çağırmak yoklama başına ikinci bir istek demekti
    # ve `00-genel.md` §2 bu ekran için TEK uç varsayıyor (saatte 60).
    api = FakeApi()
    servis = make_service(api=api)
    await servis.summary()
    assert api.names() == ["monitor_summary"]


async def test_ozet_dort_kutuyu_yerel_deftere_isler() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    sonuc = await servis.summary()
    assert sonuc["connected"] is True
    assert len(sonuc["tiles"]) == 4
    # Dört bileşen için birer `probe` satırı — bu, uzak tarafta karşılığı
    # OLMAYAN geçmiştir.
    assert len(depo.events) == 4
    assert {row["kind"] for row in depo.events} == {"probe"}


async def test_ayni_gozlem_ikinci_satir_acmaz_sayaci_artirir() -> None:
    # Ekran 60 saniyede bir yokluyor; her yoklamayı ayrı satır yazmak günde
    # 1.440 satır × dört bileşen demekti ve defter bir günde okunamaz hâle
    # gelirdi. Okunamayan bir defter, tutulmamış bir defterdir.
    depo = FakeStore()
    servis = make_service(store=depo)
    await servis.summary()
    await servis.summary()
    await servis.summary()
    assert len(depo.events) == 4
    assert {row["occurrence_count"] for row in depo.events} == {3}


async def test_ilk_gorulme_ani_hic_degismez() -> None:
    # "Bu ne zamandır oluyor" sorusunun cevabı `first_seen_at`tir.
    depo = FakeStore()
    servis = make_service(store=depo)
    await servis.summary()
    ilk = [row["first_seen_at"] for row in depo.events]
    await servis.summary()
    assert [row["first_seen_at"] for row in depo.events] == ilk


async def test_durum_degisince_yeni_satir_acilir() -> None:
    # `ok` → `degraded` geçişi AYRI bir satırdır: "ne zaman bozuldu, ne zaman
    # düzeldi" sorusu ancak böyle cevaplanır.
    api = FakeApi(summary=dict(HEALTHY))
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    await servis.summary()
    saglikli = len(depo.events)

    api.summary_payload = {
        **HEALTHY,
        "events": {**HEALTHY["events"], "by_source": {**HEALTHY["events"]["by_source"],
                                                      "website": 3}},
    }
    await servis.summary()
    assert len(depo.events) > saglikli
    assert "probe_degraded" in depo.codes()


# ======================================================= K7 — geçit düşünce

async def test_gecit_dusunce_ekran_ayakta_kalir_ve_soyler() -> None:
    api = FakeApi()
    api.fail.add("monitor_summary")
    servis = make_service(api=api)
    sonuc = await servis.summary()
    # `ok` UCUN SAĞLIĞINI anlatır, okumanın başarısını değil; ayrımı
    # `connected` taşır. Yalnız `ok`a bakan bir ekran "her şey yolunda" derdi.
    assert sonuc["ok"] is True
    assert sonuc["connected"] is False
    assert sonuc["error"]
    assert {kutu["status"] for kutu in sonuc["tiles"]} == {"unknown"}


async def test_ulasilamayan_sunucu_yerel_deftere_duser() -> None:
    # BU MODÜLÜN VAR OLMA SEBEBİ. Geçit koptuğunda sunucuya HİÇBİR ŞEY
    # ULAŞMAZ; `veykemtu_monitor_events` bu arızayı asla göremez.
    api = FakeApi()
    api.fail.add("monitor_summary")
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    await servis.summary()
    assert len(depo.events) == 1
    satir = depo.events[0]
    assert satir["kind"] == "fault"
    assert satir["source"] == "kontrol_merkezi"
    assert satir["result"] == "unknown"


async def test_dagitilmamis_uc_hata_degil_uyaridir() -> None:
    # Sunucu tarafı paralel yazılıyor. Her dakika bir kırmızı satır, gerçek
    # arızaların arasına yalancı bir alarm koyardı.
    api = FakeApi()
    api.fail.add("monitor_summary")
    api.fail_code = "control_endpoint_missing"
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    sonuc = await servis.summary()
    assert sonuc["endpoint_missing"] is True
    assert depo.events[0]["level"] == "warning"


async def test_defter_yazilamazsa_ekran_yine_calisir() -> None:
    # İzleme ekranının yazamadığı için düşmesi, izlediği sistemden önce
    # kendisinin çökmesi olurdu.
    depo = FakeStore()
    depo.broken = True
    servis = make_service(store=depo)
    sonuc = await servis.summary()
    assert sonuc["ok"] is True
    assert sonuc["connected"] is True


# ============================================================ sağlık olayı

async def test_ilk_yoklama_olay_yayinlamaz() -> None:
    # "Değişti" diyebilmek için önce bir öncekini bilmek gerekir; uydurulmuş
    # bir önceki, her açılışta sahte bir alarm üretirdi.
    yol = FakeBus()
    servis = make_service(bus=yol)
    await servis.summary()
    assert yol.names() == []


async def test_hukum_degisince_bir_kez_yayinlanir() -> None:
    api = FakeApi(summary=dict(HEALTHY))
    yol = FakeBus()
    servis = make_service(api=api, bus=yol)
    await servis.summary()
    await servis.summary()          # aynı hüküm — yayın YOK
    assert yol.names() == []

    api.summary_payload = {**HEALTHY, "health": {"status": "down",
                                                 "reasons": ["no_device_online"]}}
    await servis.summary()
    assert yol.names() == ["bld_status_monitor.health_changed"]
    _, yuk = yol.events[0]
    assert (yuk["previous"], yuk["status"]) == ("ok", "down")

    await servis.summary()          # yine aynı hüküm — ikinci yayın YOK
    assert len(yol.events) == 1


# ================================================================== olaylar

async def test_olay_listesi_varsayilan_seviye_suzgecini_gonderir() -> None:
    # Panel süzgeci temizlediğinde sunucunun SESSİZ varsayılanına düşmek,
    # ekranda hiçbir yerde yazmayan bir süzgeç demekti.
    api = FakeApi()
    servis = make_service(api=api)
    await servis.events()
    cagri = api.used("monitor_events")[0]
    assert cagri["level"] == ["warning", "error", "critical"]
    assert cagri["resolved"] == "false"


async def test_taninmayan_seviye_istegi_gonderilmez() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.events(level="kritik")
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_olay_satiri_alanlari_ayiklamaz() -> None:
    # Sözleşme additive büyüyor: bilinen alanları seçip gerisini atan bir
    # dönüşüm, sunucuya eklenen her yeni alanı sessizce düşürürdü.
    api = FakeApi(events=[{"id": 1, "source": "website", "level": "warning",
                           "message": "x", "yeni_alan": "korunmali"}])
    servis = make_service(api=api)
    sonuc = await servis.events()
    assert sonuc["items"][0]["yeni_alan"] == "korunmali"
    assert sonuc["items"][0]["source_label"] == "Web sitesi"


# ============================================================== çözme (yazma)

async def test_cozme_izinsiz_engellenir_ve_iz_birakir() -> None:
    # ÇİFT KAPI (K9): uç noktada da denetleniyor. Arayüzde düğmeyi gizlemek
    # yetkilendirme değildir; istemci gövdeyi elle kurabilir.
    api = FakeApi()
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    sonuc = await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR,
                                       allow_manage=False)
    assert sonuc["ok"] is False
    assert depo.results("monitor.resolve") == [mon.BLOCKED]
    assert api.names() == []


async def test_cozme_once_denendi_sonra_ok_yazar() -> None:
    # Ağ koparsa geriye YALNIZ ilk satır kalır; yıkıcı işlemin ÇİFT SATIRI
    # budur (ADR 0012).
    depo = FakeStore()
    servis = make_service(store=depo)
    sonuc = await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR,
                                       note="USB kablosu kopmuştu.", dry_run=False,
                                       allow_manage=True)
    assert sonuc["ok"] is True
    assert depo.results("monitor.resolve") == [mon.TRIED, mon.DONE]


async def test_cozme_gecit_patlarsa_hata_satiri_yazar() -> None:
    api = FakeApi()
    api.fail.add("resolve_monitor_event")
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    sonuc = await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR,
                                       dry_run=False, allow_manage=True)
    assert sonuc["ok"] is False
    assert depo.results("monitor.resolve") == [mon.TRIED, mon.FAILED]


async def test_zaten_cozulmus_olay_ikinci_kez_cozulmez() -> None:
    # Sunucu 409 veriyor; erken okumak kullanıcıya ham bir çakışma yerine
    # kendi cümlesini göstermek demektir. İkinci bir çözüm notu ilkini
    # gizlerdi (sözleşme).
    api = FakeApi(detail={"id": 3311, "source": "mutfakapp", "level": "error",
                          "resolved_at": "2026-08-16T09:05:00Z",
                          "resolved_by_actor": "Mehmet Kaya"})
    servis = make_service(api=api)
    sonuc = await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR,
                                       dry_run=False, allow_manage=True)
    assert sonuc["ok"] is False
    assert "Mehmet Kaya" in sonuc["error"]
    assert "resolve_monitor_event" not in api.names()


async def test_kuru_prova_bayragi_her_zaman_acikca_gecer() -> None:
    # Geçidin varsayılanına GÜVENİLMEZ: `config/local.yaml` git dışıdır ve
    # orada `dry_run_default: true` yazıyor olabilir.
    api = FakeApi()
    servis = make_service(api=api)
    await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR, dry_run=True,
                               allow_manage=True)
    assert api.used("resolve_monitor_event")[0]["dry_run"] is True

    await servis.resolve_event(3311, reason=GEREKCE, actor=AKTOR, dry_run=None,
                               allow_manage=True)
    assert api.used("resolve_monitor_event")[1]["dry_run"] is False


async def test_kisa_gerekce_serviste_de_reddedilir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.resolve_event(3311, reason="kısa", actor=AKTOR,
                                       allow_manage=True)
    assert sonuc["ok"] is False
    assert api.names() == []


# ============================================================ düzeltme defteri

async def _defter_kur(servis: object, **degisiklik: object) -> dict:
    kayit = {"title": "Yazıcı arızasında test fişi", "description": "",
             "channel": "bld.api", "action": "kds.test_receipt", "device_id": 2,
             "enabled": True, "reason": GEREKCE, "actor": AKTOR}
    kayit.update(degisiklik)
    return await servis.save_runbook("printer.test", **kayit)  # type: ignore[attr-defined]


async def test_defter_kaydi_yazilir_ve_iz_birakir() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    sonuc = await _defter_kur(servis)
    assert sonuc["ok"] is True
    assert depo.results("runbook.save") == [mon.DONE]
    assert depo.runbook["printer.test"]["action"] == "kds.test_receipt"


async def test_defter_taninmayan_eylemi_kabul_etmez() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    sonuc = await _defter_kur(servis, action="cancel_order")
    assert sonuc["ok"] is False
    assert depo.runbook == {}


async def test_komut_izinsiz_engellenir_ve_iz_birakir() -> None:
    api = FakeApi()
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    await _defter_kur(servis)
    sonuc = await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                                     allow_manage=False)
    assert sonuc["ok"] is False
    assert depo.results("runbook.run") == [mon.BLOCKED]
    assert "send_command" not in api.names()


async def test_komut_gecitten_gecer_ve_dry_run_acikca_gider() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await _defter_kur(servis)
    sonuc = await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                                     dry_run=False, allow_manage=True)
    assert sonuc["ok"] is True
    cagri = api.used("send_command")[0]
    assert cagri["command"] == "test_receipt"
    assert cagri["dry_run"] is False
    # Yüksüz komutta `payload` VERİLMEZ ve `None` kalır; geçit `None` yükü
    # gövdeye hiç koymuyor (`client.py`: `if payload is not None`). Boş bir
    # sözlük göndermek, sözleşmede olmayan bir alan eklemek olurdu.
    assert cagri["payload"] is None


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    # BLD'de hiçbir şey değişmedi; dinleyicileri "komut gitti" diye uyandırmak
    # yalan olurdu.
    yol = FakeBus()
    servis = make_service(bus=yol)
    await _defter_kur(servis)
    sonuc = await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                                     dry_run=True, allow_manage=True)
    assert sonuc["dry_run"] is True
    assert "bld_status_monitor.command_sent" not in yol.names()

    await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                             dry_run=False, allow_manage=True)
    assert "bld_status_monitor.command_sent" in yol.names()


async def test_pasiflestirilmis_kayit_calistirilmaz() -> None:
    # SİLME YOK, PASİFLEŞTİRME VAR: silinen bir anahtar denetim izini okunamaz
    # kılardı.
    api = FakeApi()
    servis = make_service(api=api)
    await _defter_kur(servis, enabled=False)
    sonuc = await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                                     allow_manage=True)
    assert sonuc["ok"] is False
    assert "send_command" not in api.names()


async def test_elle_yapilan_adim_calistirilmaz_ve_nedeni_yazilir() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    await servis.save_runbook("sunucu.restart", title="Servisi yeniden başlat",
                              description="Sunucuya SSH ile girilip yeniden başlatılır.",
                              channel="manual", action=mon.MANUAL_ACTION, device_id=0,
                              enabled=True, reason=GEREKCE, actor=AKTOR)
    sonuc = await servis.run_runbook("sunucu.restart", reason=GEREKCE, actor=AKTOR,
                                     allow_manage=True)
    assert sonuc["ok"] is False
    assert "ssh" in sonuc["error"]
    assert api.names() == []


async def test_defterde_olmayan_anahtar_calistirilmaz() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.run_runbook("yok.olan", reason=GEREKCE, actor=AKTOR,
                                     allow_manage=True)
    assert sonuc["ok"] is False
    assert api.names() == []


async def test_komut_patlarsa_yerel_deftere_de_dusier() -> None:
    # Komut gönderilirken ağ koparsa kasanın komutu alıp almadığı bilinmez;
    # hem yazma izi hem gözlem defteri satır tutar.
    api = FakeApi()
    depo = FakeStore()
    servis = make_service(api=api, store=depo)
    await _defter_kur(servis)
    api.fail.add("send_command")
    sonuc = await servis.run_runbook("printer.test", reason=GEREKCE, actor=AKTOR,
                                     dry_run=False, allow_manage=True)
    assert sonuc["ok"] is False
    assert depo.results("runbook.run") == [mon.TRIED, mon.FAILED]
    assert any(row["kind"] == "fault" for row in depo.events)


# =============================================================== geçmiş

async def test_gecmis_iki_defteri_birlestirir_ve_eskiden_yeniye_sirali() -> None:
    # "Kasayı yeniden başlattık ve on dakika sonra düzeldi" cümlesi ancak iki
    # defter aynı çizelgede olunca kurulabilir.
    depo = FakeStore()
    servis = make_service(store=depo)
    await servis.summary()
    await _defter_kur(servis)
    sonuc = await servis.history()
    assert sonuc["ok"] is True
    damgalar = [item["at"] for item in sonuc["items"]]
    assert damgalar == sorted(damgalar)
    assert any(item["title"].startswith("runbook.save") for item in sonuc["items"])


async def test_yerel_defter_kaynaga_gore_suzulur() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    await servis.summary()
    sonuc = await servis.local_log(source="website")
    assert [item["source"] for item in sonuc["items"]] == ["website"]


async def test_yerel_defter_taninmayan_sonucu_reddeder() -> None:
    servis = make_service()
    sonuc = await servis.local_log(result="bozuk")
    assert sonuc["ok"] is False


async def test_defter_okunamazsa_ekran_bos_liste_ile_ayakta_kalir() -> None:
    depo = FakeStore()
    depo.read_broken = True
    servis = make_service(store=depo)
    sonuc = await servis.local_log()
    assert (sonuc["ok"], sonuc["items"]) == (True, [])


# ================================================================ tercih

async def test_taninmayan_tercih_yazilmaz() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    sonuc = await servis.save_prefs({"renk": "mavi"}, actor=AKTOR)
    assert sonuc["ok"] is False
    assert depo.prefs == {}


async def test_yoklama_araligi_alt_sinira_cekilir() -> None:
    depo = FakeStore()
    servis = make_service(store=depo)
    await servis.save_prefs({"poll_seconds": 120}, actor=AKTOR)
    assert (await servis.prefs())["poll_seconds"] == 120


async def test_cihaz_listesi_dar_yuzdur_ve_ayar_tasimaz() -> None:
    api = FakeApi()
    servis = make_service(api=api)
    sonuc = await servis.devices()
    assert sonuc["connected"] is True
    assert sonuc["items"][0]["state_tone"] == "warn"     # çevrimiçi ama yazıcı bozuk
    assert sonuc["meta"]["printer_fault"] == 1
