"""Deneme Kulübü servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_trial_club_backend.service import TrialClubService
from store_trial_club_fakes import Events, FakeApi, FakeLog, FakeNotifier, FakeStore, exam, member

CSV = ("Ad Soyad;E-posta;Net;Puan\n"
       "Katılımcı 1;k1@ornek.com;42,5;380\n"
       "Katılımcı 2;k2@ornek.com;38;340\n")


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             notifier: Any = None, events: Any = None,
             **config: Any) -> tuple[TrialClubService, FakeApi, FakeStore]:
    api = api or FakeApi(exams=[exam()], members=[member(1), member(2)])
    store = store or FakeStore()
    service = TrialClubService(
        api=api, store=store, log=FakeLog(), notifier=notifier, publish=events,
        config={"page_size": 50, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_trial_exams")
    result = await service.exams()
    assert result["ok"] is True            # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_katilimci_sayisi_okunamasa_bile_kpi_seridi_gelir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_trial_members")
    result = await service.overview()
    assert result["ok"] is True
    assert result["tiles"]["exams"] == 1
    assert result["tiles"]["members"] is None      # sıfır UYDURULMAZ


# ================================================== uç yoksa sessiz patlama yok

async def test_ucu_olmayan_ozellik_kapali_ve_nedeni_yazili_gelir() -> None:
    service, _, _ = _service()
    features = service.features()
    assert features["addMember"]["available"] is False
    assert "bbd_add_trial_member" in features["addMember"]["reason"]

    result = await service.add_member(1, reason="Son dakika kaydı eklenecek", actor="Ali")
    assert result["ok"] is False
    assert result["pending"] is True
    assert "henüz yok" in result["error"]


async def test_bildirim_yetenegi_yoksa_tek_kisilik_hatirlatma_kapalidir() -> None:
    service, _, _ = _service()
    assert service.features()["notifyOne"]["available"] is False
    result = await service.notify_one(1, template="", channel="sms",
                                      reason="Sınav hatırlatması yapılacak", actor="Ali")
    assert result["ok"] is False
    assert "store_notifications" in result["error"]


async def test_bildirim_yetenegi_varsa_tek_kisiye_gider() -> None:
    # TEST DÜZELTİLDİ, KOD DEĞİL. Eski hâli `sent[0]["to"]` okuyordu; öyle bir
    # alan yok. `store.notify.send` yeteneğinin GERÇEK imzası
    # (`NotifySender.send`, store_notifications) alıcıyı `recipients` LİSTESİ
    # olarak alır — `to=` imzası Kargo modülünün sahtesinden kopyalanmış bir
    # kalıntı. Servis doğru imzayı çağırıyordu; sınayan satır yanlıştı.
    # Telefon ham değil ANAHTAR biçiminde gider: "05321234501" gibi bir metni
    # SMS sağlayıcısı reddeder.
    notifier = FakeNotifier()
    service, _, _ = _service(notifier=notifier)
    result = await service.notify_one(1, template="store:12", channel="sms",
                                      reason="Sınav hatırlatması yapılacak", actor="Ali",
                                      dry_run=False)
    assert result["ok"] is True
    assert notifier.sent[0]["recipients"] == ["5321234501"]
    assert notifier.sent[0]["template"] == "store:12"
    assert notifier.sent[0]["channel"] == "sms"
    assert notifier.sent[0]["dryRun"] is False


async def test_tek_kisilik_hatirlatma_denemeden_once_ize_yazilir() -> None:
    # Yetenek çağrısı patlarsa "ne yapmaya çalıştık" kaydı YALNIZ yerelde
    # kalır; gönderim uzakta yapılmış bile olabilir. Öbür yazma yollarının
    # hepsi istekten ÖNCE "denendi" düşüyor, bu yol düşmüyordu.
    class Patlayan:
        async def send(self, **_: Any) -> dict[str, Any]:
            raise RuntimeError("bildirim modülü düştü")

    service, _, store = _service(notifier=Patlayan())
    result = await service.notify_one(1, template="store:12", channel="sms",
                                      reason="Sınav hatırlatması yapılacak", actor="Ali",
                                      dry_run=False)
    assert result["ok"] is False
    kayitlar = [row for row in store.audit if row["action"] == "notify_one"]
    assert [row["result"] for row in kayitlar] == ["denendi", "hata"]


async def test_hatirlatma_izinde_cozulmus_sablon_gorunur() -> None:
    # Şablon Ayarlar sekmesindeki tercihten geliyorsa çağrıya boş `template`
    # parametresi gelir; iz RAW değeri yazsaydı hangi şablonun gittiği
    # kaydedilmezdi.
    notifier = FakeNotifier()
    service, _, store = _service(notifier=notifier)
    await service.save_settings(notify_direct_template="store:41", actor="Ali")
    await service.notify_one(1, template="", channel="sms",
                             reason="Sınav hatırlatması yapılacak", actor="Ali", dry_run=False)
    assert notifier.sent[0]["template"] == "store:41"
    kayitlar = [row for row in store.audit if row["action"] == "notify_one"]
    assert json.loads(kayitlar[-1]["detail"])["template"] == "store:41"


# ==================================================== tekil uç yok — süzgeç tuzağı

async def test_sunucu_id_suzgecini_yok_sayarsa_dogru_deneme_bulunur() -> None:
    # Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar ve tüm listeyi
    # döner; dönen listede kimlik eşleşmesi aranır.
    api = FakeApi(exams=[exam(1), exam(2, name="AYT Deneme")])
    api.ignore_filters = True
    service, _, _ = _service(api)
    result = await service.exam(2)
    assert result["ok"] is True
    assert result["exam"]["name"] == "AYT Deneme"


async def test_olmayan_deneme_uydurulmaz() -> None:
    service, _, _ = _service()
    result = await service.exam(77)
    assert result["ok"] is False
    assert "bulunamadı" in result["error"]


# ================================================================== kontenjan

async def test_kontenjan_istek_cikmadan_reddedilir_ve_nedenini_soyler() -> None:
    """Mağazada kontenjan diye bir KAYIT yok — 503 alınmadan burada durur.

    Üyelik ürünü `manage_stock = 0` ile kaydediliyor ve bu değer her
    kaydetmede yeniden yazılıyor; stok üzerinden kurulan bir kontenjan bir
    sonraki kaydetmede sessizce silinirdi. `core_config`'e yazmak daha kötüsü:
    hiçbir şey o sayıya bakmadığı için satış kontenjan dolduktan sonra da
    sürer ve fark ancak fazla üye kaydolunca anlaşılırdı.
    """
    service, api, store = _service()
    result = await service.set_capacity(1, capacity=60, reason="Ek salon açıldı bugün",
                                        actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["feature"] == "capacity"
    assert "vitrin metni" in result["error"]
    assert api.used("bbd_save_trial_exam") == []
    # Ne yapmaya çalışıldığı yerel izde KALIR.
    assert [row["result"] for row in store.audit if row["action"] == "set_capacity"] == ["uc_yok"]


async def test_kunye_kaydinda_kontenjan_gelirse_fiyat_da_yazilmaz() -> None:
    """Mağaza aynı isteği tümüyle reddediyor; sessizce düşürmek yanlış olurdu.

    Düşürseydik "kaydedildi" diyen ama kontenjanı değişmemiş bir ekran çıkardı.
    """
    service, api, _ = _service()
    result = await service.save_exam(1, payload={"capacity": 30, "feeKurus": 15035},
                                     reason="Kontenjan ve ücret güncellemesi", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is False
    assert result["feature"] == "capacity"
    assert api.used("bbd_save_trial_exam") == []


async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.save_exam(1, payload={"feeKurus": 15035}, reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("bbd_save_trial_exam") == []


async def test_yeni_deneme_acilamaz_ve_nedeni_soylenir() -> None:
    """Kimliksiz çağrı da AYNI üyelik ürününü günceller — "yeni" diye bir şey yok.

    Sessizce göndermek, "yeni deneme açtım" sanan kullanıcının var olan
    denemenin fiyatını değiştirmesi demekti.
    """
    service, api, _ = _service()
    result = await service.save_exam(None, payload={"feeKurus": 15035},
                                     reason="Yeni deneme açılmaya çalışılıyor", actor="Ali")
    assert result["ok"] is False
    assert result["feature"] == "newExam"
    assert api.used("bbd_save_trial_exam") == []


async def test_kapali_alanlarin_nedeni_ilan_edilir() -> None:
    """Kapalı alanın NEDENİ ekranda olmalı; boş bir `disabled` yeterli değil."""
    service, _, _ = _service()
    features = service.features()
    for key in ("capacity", "name", "schedule", "newExam"):
        assert features[key]["available"] is False
        assert len(features[key]["reason"]) > 40
    assert features["saveProfile"]["available"] is True


# ================================================================ deneme kaydı

async def test_vitrin_metni_ve_takvim_istek_cikmadan_reddedilir() -> None:
    """Mağazada bu alanların karşılığı yok; gönderilirse istek tümüyle reddedilir.

    Kulüp bu depoda TEK BİR sanal üründür ve takvimi yoktur; ad/açıklama ise
    dile bağlı ve panelden düzenleniyor.
    """
    service, api, _ = _service()
    for payload, beklenen in (
        ({"name": "TYT 2", "feeKurus": 15035}, "name"),
        ({"examDate": "2026-10-01", "feeKurus": 15035}, "schedule"),
        ({"venue": "A Salonu", "feeKurus": 15035}, "schedule"),
    ):
        result = await service.save_exam(1, payload=payload,
                                         reason="Künye güncellemesi yapılıyor", actor="Ali")
        assert result["ok"] is False
        assert result["feature"] == beklenen
    assert api.used("bbd_save_trial_exam") == []


async def test_uyelik_bedeli_kurustan_ondaliga_cevrilir() -> None:
    service, api, _ = _service()
    result = await service.save_exam(1, payload={"feeKurus": 15035, "isOpen": True},
                                     reason="Üyelik bedeli güncelleniyor", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is True
    # Gövde MAĞAZANIN iki alanı: `price` ve `isOpen`. Başka hiçbir şey gitmez.
    assert api.used("bbd_save_trial_exam")[0]["payload"] == {"price": "150.35", "isOpen": True}


async def test_degisen_alan_yoksa_istek_gonderilmez() -> None:
    service, api, _ = _service()
    result = await service.save_exam(1, payload={}, reason="Boş gövde denemesi yapıldı",
                                     actor="Ali")
    assert result["ok"] is False
    assert "üyelik bedeli" in result["error"]
    assert api.used("bbd_save_trial_exam") == []


# ============================================================= sonuç yükleme

async def test_onizlemesiz_sonuc_yazilmaz() -> None:
    service, api, _ = _service()
    result = await service.upload_apply(token="olmayan-jeton", reason="Sonuçlar yükleniyor",
                                        actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "Eşleştirme tablosu görülmeden" in result["error"]
    assert api.used("bbd_upload_trial_results") == []


async def test_onizleme_eslestirme_tablosu_ve_jeton_uretir() -> None:
    service, _, store = _service()
    preview = await service.upload_preview(1, content=CSV, filename="sonuc.csv")
    assert preview["ok"] is True
    assert preview["summary"]["matched"] == 2
    assert preview["summary"]["memberCount"] == 2
    assert store.uploads[preview["token"]]["status"] == "preview"


async def test_eslesmeyen_satir_onizlemede_gorunur_ama_yazilmaz() -> None:
    service, api, _ = _service()
    body = CSV + "Yabancı Kişi;yabanci@ornek.com;10;100\n"
    preview = await service.upload_preview(1, content=body)
    assert preview["summary"]["unmatched"] == 1
    await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                               actor="Ali", dry_run=False)
    written = api.used("bbd_upload_trial_results")[0]["rows"]
    assert [row["memberId"] for row in written] == [1, 2]


async def test_uygulama_onizlenen_satirlari_kullanir_yeniden_eslestirmez() -> None:
    service, api, store = _service()
    preview = await service.upload_preview(1, content=CSV)
    rows = json.loads(store.uploads[preview["token"]]["rows"])
    # Ekran açıkken katılımcı listesi değişse bile ONAYLANAN satırlar yazılır.
    api.member_rows = []
    await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                               actor="Ali", dry_run=False)
    assert api.used("bbd_upload_trial_results")[0]["rows"] == rows


async def test_ayni_yukleme_iki_kez_uygulanmaz() -> None:
    service, _, _ = _service()
    preview = await service.upload_preview(1, content=CSV)
    first = await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                                       actor="Ali", dry_run=False)
    assert first["ok"] is True
    second = await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                                        actor="Ali", dry_run=False)
    assert second["ok"] is False
    assert "zaten uygulandı" in second["error"]


async def test_jeton_kapatilamazsa_yazma_basarili_sayilir_ama_uyarilir() -> None:
    # SONUÇLAR MAĞAZAYA ZATEN YAZILDI. İstisnayı dışarı bırakmak 500 üretir;
    # kullanıcı yazmanın gitmediğini sanıp aynı jetonla yeniden dener ve
    # satırlar İKİNCİ kez yazılır. Bu yüzden sonuç ok döner, uyarı söylenir.
    class KapatilamayanStore(FakeStore):
        async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            if "_upload" in sql and sql.strip().startswith("UPDATE"):
                raise RuntimeError("disk dolu")
            await super().execute(sql, params)

    store = KapatilamayanStore()
    service, api, _ = _service(store=store)
    preview = await service.upload_preview(1, content=CSV)
    result = await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                                        actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["written"] == 2
    assert "TEKRAR uygulamayın" in result["warning"]
    assert len(api.used("bbd_upload_trial_results")) == 1
    assert any(row["result"] == "jeton_kapatilamadi" for row in store.audit)


async def test_yukleme_yayinlama_degildir_ve_bunu_soyler() -> None:
    service, _, _ = _service()
    preview = await service.upload_preview(1, content=CSV)
    result = await service.upload_apply(token=preview["token"], reason="Sonuçlar kurumdan geldi",
                                        actor="Ali", dry_run=False)
    assert "YAYINLANMADI" in result["notice"]


async def test_katilimcisiz_denemeye_sonuc_yuklenemez() -> None:
    api = FakeApi(exams=[exam()], members=[])
    service, _, _ = _service(api)
    result = await service.upload_preview(1, content=CSV)
    assert result["ok"] is False
    assert "katılımcı yok" in result["error"]


async def test_baska_denemenin_katilimcisi_eslestirmeye_girmez() -> None:
    # Sunucu `exam_id` süzgecini yok sayarsa başka denemenin katılımcısına
    # sonuç yazardık; kimlik taşımayan satır elenir.
    api = FakeApi(exams=[exam()], members=[member(1), member(2, exam_id=9)])
    api.ignore_filters = True
    service, _, _ = _service(api)
    preview = await service.upload_preview(1, content=CSV)
    assert preview["summary"]["memberCount"] == 1
    assert preview["summary"]["unmatched"] == 1


# ================================================================ yayınlama

async def test_yayinlama_uyarisiyla_ve_olayla_birlikte_doner() -> None:
    events = Events()
    service, api, _ = _service(events=events)
    result = await service.publish(1, reason="Kurum sonuçları onayladı", actor="Ali",
                                   dry_run=False)
    assert result["ok"] is True
    assert "katılımcıya görünür" in result["notice"]
    assert api.used("bbd_publish_trial_results")[0]["dry_run"] is False
    assert events.names() == ["store.trial.results_published"]


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    events = Events()
    service, _, _ = _service(events=events)
    await service.publish(1, reason="Kurum sonuçları onayladı", actor="Ali", dry_run=True)
    assert events.names() == []


# ================================================================= bildirim

async def test_toplu_bildirim_tek_istekte_gider() -> None:
    # Yüz kişiye tek tek çağrı atmak dakikada 55 istekli hız kovasını tüketir.
    service, api, _ = _service()
    result = await service.notify_bulk(1, audience="all", channel="sms",
                                       template="trial_reminder", message="Sınav yarın.",
                                       reason="Sınav öncesi hatırlatma", actor="Ali",
                                       dry_run=False)
    assert result["ok"] is True
    assert result["sent"] == 2
    assert len(api.used("bbd_send_notification")) == 1


async def test_gonderilen_alici_listesi_onizlenenle_ayni_kitledir() -> None:
    api = FakeApi(exams=[exam()],
                  members=[member(1, payment_status="pending"), member(2)])
    service, api, _ = _service(api)
    preview = await service.notify_preview(1, audience="unpaid", channel="sms",
                                           message="Ödeme bekleniyor.")
    assert preview["reachable"] == 1
    # Alıcıların KENDİSİ ekrana dönmez; sayı yeter.
    assert "recipients" not in preview

    await service.notify_bulk(1, audience="unpaid", channel="sms", template="",
                              message="Ödeme bekleniyor.", reason="Ödeme hatırlatması yapıldı",
                              actor="Ali", dry_run=False)
    body = api.used("bbd_send_notification")[0]["payload"]
    assert [row["memberId"] for row in body["recipients"]] == [1]
    assert body["context"] == {"trialExamId": 1, "audience": "unpaid"}


async def test_ulasilamayan_alici_gonderildi_sayilmaz() -> None:
    api = FakeApi(exams=[exam()], members=[member(1), member(2, phone="0312 555 44 33")])
    service, _, _ = _service(api)
    preview = await service.notify_preview(1, audience="all", channel="sms", message="Sınav.")
    assert preview["reachable"] == 1
    assert preview["unreachableCount"] == 1


async def test_sms_parca_sayisi_maliyet_icin_onizlenir() -> None:
    service, _, _ = _service()
    preview = await service.notify_preview(1, audience="all", channel="sms",
                                           message="Yarınki denemeniz saat 10:00'da başlıyor.")
    assert preview["smsParts"] == 1
    assert preview["estimatedSms"] == 2          # 1 parça × 2 alıcı


async def test_alici_siniri_asilirsa_hic_gonderilmez() -> None:
    api = FakeApi(exams=[exam()], members=[member(index) for index in range(1, 6)])
    service, api, _ = _service(api, max_recipients=3)
    result = await service.notify_bulk(1, audience="all", channel="sms", template="x",
                                       message="Sınav.", reason="Sınav öncesi hatırlatma",
                                       actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "sınır" in result["error"]
    assert api.used("bbd_send_notification") == []


async def test_bos_kitleye_gonderim_yapilmaz() -> None:
    service, api, _ = _service()          # ikisi de ödemiş
    result = await service.notify_bulk(1, audience="unpaid", channel="sms", template="x",
                                       message="Ödeme.", reason="Ödeme hatırlatması yapıldı",
                                       actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_send_notification") == []


# ============================================================== denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.publish(1, reason="Kurum sonuçları onayladı", actor="Ayşe Yılmaz",
                          dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "publish_results"]
    assert kayitlar[-1]["reason"] == "Kurum sonuçları onayladı"
    assert kayitlar[-1]["actor"] == "Ayşe Yılmaz"
    assert kayitlar[-1]["result"] == "ok"


async def test_yazma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store = _service()
    api.fail.add("bbd_publish_trial_results")
    await service.publish(1, reason="Kurum sonuçları onayladı", actor="Ali", dry_run=False)
    assert any(row["result"] == "hata" for row in store.audit)


async def test_denetim_izi_denemeye_gore_suzulur() -> None:
    service, _, _ = _service()
    await service.publish(1, reason="Kurum sonuçları onayladı", actor="Ali", dry_run=False)
    result = await service.audit(exam_id=1)
    assert result["items"]
    assert all(row["examId"] == 1 for row in result["items"])


# ================================================================= ayarlar

async def test_yerel_tercih_magazaya_gitmez() -> None:
    service, api, store = _service()
    result = await service.save_settings(near_full_percent=75, attendance_spare_rows=0,
                                         notify_channel="email", actor="Ali")
    assert result["ok"] is True
    assert store.prefs["near_full_percent"] == "75"
    assert api.used("bbd_save_trial_exam") == []
    settings = await service.settings()
    assert settings["local"]["nearFullPercent"] == 75
    assert settings["local"]["notifyChannel"] == "email"


async def test_tercih_yazilamazsa_ayarlar_ucu_cokmez() -> None:
    # K7: servis HTTP hatası FIRLATMAZ. İstisnayı dışarı bırakmak Ayarlar
    # sekmesini anlamsız bir 500 ile kapatırdı.
    class YazamayanStore(FakeStore):
        async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
            if "_prefs" in sql:
                raise RuntimeError("veritabanı kilitli")
            await super().execute(sql, params)

    service, _, _ = _service(store=YazamayanStore())
    result = await service.save_settings(near_full_percent=75, actor="Ali")
    assert result["ok"] is False
    assert "kaydedilemedi" in result["error"]
    assert result["changed"] == []


# =========================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_bastirilmaz() -> None:
    # Serbest yol kabul etmek `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    service, _, _ = _service()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False


async def test_yazici_yoksa_ekran_bunu_soyler() -> None:
    service, _, _ = _service()
    status = await service.printer_status()
    assert status["ready"] is False
    assert "Yazıcı" in status["error"]


async def test_yoklama_cizelgesi_rapor_klasorune_yazilir(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.build_report("attendance", {"examId": 1})
    assert result["ok"] is True
    written = Path(result["path"])
    assert written.parent == tmp_path
    assert written.read_bytes().startswith(b"%PDF")
    # 0600: rapor klasörüne yazılan her dosya yalnız sahibine açıktır.
    assert oct(written.stat().st_mode)[-3:] == "600"


async def test_katilimcisiz_denemeye_yoklama_uretilmez(tmp_path: Path) -> None:
    api = FakeApi(exams=[exam()], members=[])
    service, _, _ = _service(api, export_path=str(tmp_path))
    result = await service.build_report("attendance", {"examId": 1})
    assert result["ok"] is False
    assert "katılımcı yok" in result["error"]


async def test_sonucu_olmayan_denemeden_karne_uretilmez(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.build_report("cards", {"examId": 1})
    assert result["ok"] is False
    assert "yüklenmemiş" in result["error"]


async def test_kisi_basi_karne_uretilir(tmp_path: Path) -> None:
    api = FakeApi(exams=[exam()], members=[member(1), member(2)],
                  results=[{"id": 1, "member_id": 1, "name": "Katılımcı 1", "net": 42.5,
                            "score": 380, "correct": 45, "wrong": 10, "attended": True},
                           {"id": 2, "member_id": 2, "name": "Katılımcı 2", "net": 38.0,
                            "score": 340, "attended": True}])
    service, _, _ = _service(api, export_path=str(tmp_path))
    whole = await service.build_report("cards", {"examId": 1})
    single = await service.build_report("cards", {"examId": 1, "memberId": 2})
    assert whole["ok"] is True and single["ok"] is True
    # Her katılımcı KENDİ SAYFASINDA başlar; iki kişilik karne tek kişilikten
    # büyüktür.
    assert whole["bytes"] > single["bytes"]


async def test_sonuc_csv_si_rapor_klasorune_yazilir(tmp_path: Path) -> None:
    api = FakeApi(exams=[exam()], members=[member(1)],
                  results=[{"id": 1, "member_id": 1, "name": "Katılımcı 1", "net": 42.5}])
    service, _, _ = _service(api, export_path=str(tmp_path))
    result = await service.export_csv(1, scope="results")
    assert result["ok"] is True
    # Excel'in Türkçe yerelinde doğru açılsın diye UTF-8 BOM + noktalı virgül.
    assert Path(result["path"]).read_bytes().startswith(b"\xef\xbb\xbf")


async def test_bilinmeyen_rapor_uretilmez() -> None:
    service, _, _ = _service()
    result = await service.build_report("uydurma", {"examId": 1})
    assert result["ok"] is False


async def test_deneme_secilmeden_rapor_uretilmez() -> None:
    service, _, _ = _service()
    result = await service.build_report("attendance", {})
    assert result["ok"] is False
    assert "deneme seçilmeli" in result["error"]
