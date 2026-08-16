"""Saf dönüşümler — ağ yok, depo yok. Dört tuzağın her biri burada sınanır."""

from __future__ import annotations

from bld_kds_backend import devices as dev
from bld_kds_fakes import DEVICE as CIHAZ

# ================================================== TUZAK 3 — üç durumlu alanlar

def test_saglik_bildirmemis_cihaz_yazici_arizali_sayilmaz() -> None:
    # `printer_ok` YOK demek "arızalı" demek değil, "bilinmiyor" demektir.
    # `False` yazmak mutfağı olmayan bir arıza için ayağa kaldırırdı.
    ham = {**CIHAZ, "health": {}}
    satir = dev.device_row(ham)
    assert satir["health"]["printer_ok"] is None
    assert satir["printer_fault"] is False
    assert satir["health"]["reported"] is False


def test_yazici_acikca_arizaliysa_bayrak_kalkar() -> None:
    satir = dev.device_row({**CIHAZ, "health": {**CIHAZ["health"], "printer_ok": False}})
    assert satir["printer_fault"] is True


def test_dokunulmamis_kilit_kilit_sayilmaz() -> None:
    # YENİ 7 ALANIN VARSAYILANI `null`. Alan eklenmesi sahadaki kasaları
    # KİLİTLEMEZ; kilit ancak yönetici açıkça `false` yazınca doğar.
    satir = dev.device_row(CIHAZ)
    assert satir["locked"] == []
    assert satir["settings"]["allow_settings"] is None

    kilitli = dev.device_row({**CIHAZ, "settings": {**CIHAZ["settings"],
                                                    "allow_settings": False,
                                                    "allow_order_edit": True}})
    assert kilitli["locked"] == ["allow_settings"]


def test_ayar_sozlugu_her_zaman_24_anahtar_tasir() -> None:
    satir = dev.device_row({**CIHAZ, "settings": {}})
    assert len(satir["settings"]) == 24
    assert all(value is None for value in satir["settings"].values())
    # `updated_at` ayar DEĞİLDİR; damga ayrı alanda durur.
    assert "updated_at" not in satir["settings"]
    # K-22 §1'in yeni anahtarı da her zaman çizilir.
    assert satir["settings"]["disabled_sound_events"] is None


def test_online_sunucudan_gelir_iptal_edilmis_cihaz_cevrimici_olamaz() -> None:
    satir = dev.device_row({**CIHAZ, "revoked_at": "2026-08-13T10:00:00Z"})
    assert satir["online"] is False
    assert satir["revoked"] is True
    assert satir["state"] == "revoked"


def test_kullanilamaz_esleme_kodu_kod_sayilmaz() -> None:
    satir = dev.device_row({**CIHAZ, "pairing": {"code": None, "usable": False}})
    assert satir["pairing"]["usable"] is False
    assert satir["pairing"]["code"] is None


# ========================================================== komutlar

def test_bilinmeyen_komut_reddedilir() -> None:
    # Kuyruğa atılıp kasada sessizce yok sayılmak, yöneticiye "komut gitti"
    # demek ama hiçbir şey olmamak demektir.
    assert dev.command_error("shutdown") != ""
    assert "tanınan bir komut değil" in dev.command_error("shutdown")
    assert dev.command_error("") != ""
    for komut in dev.COMMANDS:
        if komut != dev.REPRINT:
            assert dev.command_error(komut) == ""


def test_reprint_yuku_denetlenir() -> None:
    assert dev.command_error("reprint", {}) != ""
    assert dev.command_error("reprint", {"order_id": 5}) != ""
    assert dev.command_error("reprint", {"order_id": 5, "type": "fatura"}) != ""
    assert dev.command_error("reprint", {"order_id": 5, "type": "mutfak"}) == ""


def test_yikici_komut_kumesi_sabittir() -> None:
    assert dev.is_destructive("restart") is True
    assert dev.is_destructive("clear_failed") is True
    assert dev.is_destructive("test_receipt") is False
    assert dev.is_destructive("reprint") is False
    assert dev.is_destructive("silence_alarm") is False


def test_yeni_komutlar_taninir_ve_ucu_de_yikicidir() -> None:
    for komut in ("update", "unpair", "clear_queue"):
        assert komut in dev.COMMANDS
        assert dev.command_error(komut) == ""
        assert dev.COMMAND_LABELS[komut]
        # `update` ve `unpair` K-22 §2'de açıkça yıkıcı. `clear_queue` buraya
        # BİLEREK eklendi: yaptığı iş `clear_failed`in üst kümesidir (hatalı +
        # BEKLEYEN) ve dışarıda bırakmak, yalnız `manage` taşıyan birinin
        # kapıdan geçen `clear_failed`ten daha fazlasını yapabilmesi olurdu.
        assert dev.is_destructive(komut) is True


def test_komut_yuku_tanimadigi_anahtari_tasimaz() -> None:
    yuk = dev.command_payload("reprint", {"order_id": "12", "type": "MUTFAK",
                                          "printer": "/dev/lp9"})
    assert yuk == {"order_id": 12, "type": "mutfak"}
    assert dev.command_payload("restart", {"force": True}) == {}
    # Yeni komutlar YÜKSÜZDÜR; verilen yük taşınmaz.
    for komut in ("update", "unpair", "clear_queue"):
        assert dev.command_payload(komut, {"version": "9.9.9"}) == {}


# ================================================ K-22 §3 — zengin telemetri

def test_telemetri_alanlari_kunyeye_tasinir() -> None:
    satir = dev.device_row({**CIHAZ, "health": {
        **CIHAZ["health"], "print_queue_pending": 3,
        "queue_oldest_at": "2026-08-14T08:20:00Z",
        "last_error": "USB yazıcı yanıt vermiyor",
        "alarm_muted": True, "alarm_mute_reason": "ses oynatıcı bulunamadı",
        "sound_ok": False}})

    saglik = satir["health"]
    # "Kuyrukta 3 iş" ile "en eskisi 40 dakikadır bekliyor" ayrı bilgilerdir;
    # ikincisi olmadan sayı sahaya gitme kararını verdirmiyordu.
    assert saglik["print_queue_pending"] == 3
    assert saglik["queue_oldest_at"] == "2026-08-14T08:20:00Z"
    assert saglik["last_error"] == "USB yazıcı yanıt vermiyor"
    assert saglik["alarm_muted"] is True
    assert saglik["alarm_mute_reason"] == "ses oynatıcı bulunamadı"
    assert saglik["sound_ok"] is False
    assert satir["sound_fault"] is True
    assert satir["alarm_muted"] is True


def test_telemetri_bildirmeyen_eski_kasa_arizali_sayilmaz() -> None:
    # Alanlar OPSİYONEL: eski kasa göndermez, sunucu `null` yazar. `bool(None)`
    # yazmak olmayan bir ses arızası ve olmayan bir susturma uydururdu.
    satir = dev.device_row(CIHAZ)
    assert satir["health"]["sound_ok"] is None
    assert satir["health"]["alarm_muted"] is None
    assert satir["health"]["queue_oldest_at"] is None
    assert satir["health"]["last_error"] is None
    assert satir["sound_fault"] is False
    assert satir["alarm_muted"] is False


def test_sonucu_gelmemis_komut_basarisiz_sayilmaz() -> None:
    # `succeeded: None` "olmadı" değil, "sonuç bildirilmedi" demektir. Başarısız
    # saymak komutu ikinci kez gönderttirir; `restart` için bu ikinci kesintidir.
    satir = dev.command_row({"id": 1, "command": "restart",
                             "delivered_at": "2026-08-14T08:59:00Z",
                             "executed_at": "2026-08-14T08:59:05Z", "succeeded": None})
    assert satir["state"] == "sonucsuz"
    assert satir["succeeded"] is None
    assert satir["destructive"] is True


def test_teslim_edilmis_ama_yaniti_gecikmis_komut_takildi_der() -> None:
    kuyrukta = dev.command_row({"id": 1, "command": "test_receipt"})
    assert kuyrukta["state"] == "kuyrukta"

    yolda = dev.command_row({"id": 2, "command": "test_receipt",
                             "delivered_at": dev.now_iso()})
    assert yolda["state"] == "yolda"

    takildi = dev.command_row({"id": 3, "command": "test_receipt",
                               "delivered_at": "2026-01-01T00:00:00Z"})
    assert takildi["state"] == "takildi"


# ================================================== TUZAK 1/2 — ayar denetimi

def test_aralik_disi_ayar_reddedilir_kirpilmaz() -> None:
    # Sunucu `normalize()` ile KIRPIYOR: 2 gönderen yönetici sessizce 3 alır ve
    # yazdığının tutmadığını fark etmez. Burada reddedilir ve NEDEN söylenir.
    temiz, hatalar = dev.clean_settings({"poll_seconds": 2})
    assert temiz == {}
    assert hatalar and "3-60" in hatalar[0]

    temiz, hatalar = dev.clean_settings({"poll_seconds": 5})
    assert temiz == {"poll_seconds": 5}
    assert hatalar == []


def test_tanimayan_ayar_anahtari_sessizce_atilmaz() -> None:
    temiz, hatalar = dev.clean_settings({"pol_seconds": 5})
    assert temiz == {}
    assert hatalar and "yönetilen bir ayar değil" in hatalar[0]


def test_null_her_alanda_gecerlidir_kilidi_kaldirmanin_tek_yolu() -> None:
    temiz, hatalar = dev.clean_settings({"allow_settings": None, "poll_seconds": None})
    assert hatalar == []
    assert temiz == {"allow_settings": None, "poll_seconds": None}


def test_bos_dize_yalniz_audio_sink_icin_deger_tasir() -> None:
    # `audio_sink` boş dizesi "varsayılan çıkışa dön" demektir ve KORUNUR;
    # diğer alanlarda boş dize `null` ile eşdeğerdir.
    temiz, _ = dev.clean_settings({"audio_sink": "", "printer_device_path": ""})
    assert temiz["audio_sink"] == ""
    assert temiz["printer_device_path"] is None


def test_kilit_mesaji_160_karakterle_sinirli() -> None:
    _, hatalar = dev.clean_settings({"lock_message": "x" * 161})
    assert hatalar and "160" in hatalar[0]
    temiz, hatalar = dev.clean_settings({"lock_message": "Yönetici kilitledi."})
    assert hatalar == [] and temiz["lock_message"] == "Yönetici kilitledi."


def test_bool_alana_metin_yazilamaz() -> None:
    _, hatalar = dev.clean_settings({"sound_enabled": "belki"})
    assert hatalar != []


def test_ayni_deger_farka_girmez() -> None:
    mevcut = dev.settings_view(CIHAZ["settings"])
    assert dev.settings_diff(mevcut, {"poll_seconds": 5}) == []
    fark = dev.settings_diff(mevcut, {"poll_seconds": 9})
    assert len(fark) == 1 and fark[0]["from"] == 5 and fark[0]["to"] == 9


def test_gecikme_esigi_uyaridan_kucukse_onizleme_uyarir() -> None:
    # Sunucu bunu SESSİZCE düzeltiyor: `late_after_minutes` uyarı eşiğine
    # çekiliyor. Kısmi yazmada yönetici bunu hiç görmez — önizlemede yazıyoruz.
    mevcut = dev.settings_view(CIHAZ["settings"])          # uyarı 10, geciken 20
    assert dev.threshold_warning(mevcut, {"poll_seconds": 9}) == ""
    uyari = dev.threshold_warning(mevcut, {"warning_after_minutes": 30})
    assert "30 dakikaya çekecek" in uyari


def test_ayar_sozlesmesi_24_alan_ve_7_kilit_ilan_eder() -> None:
    spec = dev.settings_spec()
    assert len(spec) == 24
    assert sum(1 for item in spec if item["lock"]) == 7
    poll = next(item for item in spec if item["key"] == "poll_seconds")
    assert (poll["low"], poll["high"]) == (3, 60)

    # Beş onay kutusunun adları SÖZLEŞMEDEN gelir; panel kendi listesini
    # tutmaz ve `connectionLost` sözleşmede hiç görünmez.
    sesler = next(item for item in spec if item["key"] == "disabled_sound_events")
    assert sesler["kind"] == "events"
    assert [secenek["key"] for secenek in sesler["options"]] == [
        "newOrder", "lateOrder", "printerError", "subscriptionReminder", "bbdOrder"]
    assert all(not item["options"] for item in spec
               if item["key"] != "disabled_sound_events")


# ============================================ K-22 §1 — olay bazlı sesler

def test_olay_listesi_kanonik_siraya_cekilir_ve_tekrarlar_duser() -> None:
    # Kanonikleştirme görsel değil: sunucudan `lateOrder,newOrder` gelip
    # panelden `newOrder,lateOrder` gitseydi `settings_diff` olmayan bir
    # değişiklik görür ve `settings_updated_at` boş yere ileri atılırdı.
    assert dev.sound_events_text(" lateOrder , newOrder ,newOrder") == "newOrder,lateOrder"
    assert dev.sound_events_text("") == ""
    # OKUMA yönü hoşgörülü: tanımadığı adı eler, çökmez.
    assert dev.sound_events_text("newOrder,uydurmaOlay") == "newOrder"


def test_yazma_yonunde_tanimayan_olay_adi_reddedilir() -> None:
    # Sunucu sessizce eliyor ve orada doğru: yöneticinin yazım hatası mutfağı
    # sessiz bırakmamalı. Burada REDDEDİLİR — sessizce elenen bir ad, "beş sesi
    # de kapattım" sanan yöneticiye hiçbir şey söylemezdi.
    assert dev.sound_events_error("newOrder,lateOrder") == ""
    assert "Tanınmayan" in dev.sound_events_error("newOrder,uydurmaOlay")


def test_baglanti_alarmi_kapatilamaz() -> None:
    hata = dev.sound_events_error("newOrder,connectionLost")
    assert "connectionLost" in hata
    _, hatalar = dev.clean_settings({"disabled_sound_events": "connectionLost"})
    assert hatalar and "kapatılamaz" in hatalar[0]


def test_olay_listesinde_bos_dize_ile_null_ayri_seylerdir() -> None:
    # `null` = "yönetici dokunmadı" (kasa kendi listesini korur)
    # `""`   = "hiçbiri kapalı olmasın" (hepsini aç)
    temiz, hatalar = dev.clean_settings({"disabled_sound_events": ""})
    assert hatalar == [] and temiz == {"disabled_sound_events": ""}

    temiz, hatalar = dev.clean_settings({"disabled_sound_events": None})
    assert hatalar == [] and temiz == {"disabled_sound_events": None}

    temiz, _ = dev.clean_settings({"disabled_sound_events": "bbdOrder,newOrder"})
    assert temiz == {"disabled_sound_events": "newOrder,bbdOrder"}


def test_ayni_ses_listesi_farka_girmez() -> None:
    mevcut = dev.settings_view({**CIHAZ["settings"],
                                "disabled_sound_events": "lateOrder,newOrder"})
    assert mevcut["disabled_sound_events"] == "newOrder,lateOrder"
    assert dev.settings_diff(mevcut, {"disabled_sound_events": "newOrder,lateOrder"}) == []
    fark = dev.settings_diff(mevcut, {"disabled_sound_events": ""})
    assert len(fark) == 1 and fark[0]["to"] == ""


# ================================================== TUZAK 4 — revizyon

def test_bos_revizyon_listesi_reddedilir() -> None:
    # Gönderilen liste siparişin YENİ hâlidir; boş liste siparişi boşaltır.
    for gövde in ([], None, "kalem yok"):
        temiz, hata = dev.revision_items(gövde)
        assert temiz == [] and hata != ""
        assert "TAM kalem listesini" in hata


def test_revizyon_kalemi_denetlenir() -> None:
    assert dev.revision_items([{"quantity": 2}])[1] != ""
    assert dev.revision_items([{"menu_id": 4, "quantity": 0}])[1] != ""
    assert dev.revision_items([{"menu_id": 4, "quantity": 1000}])[1] != ""
    assert dev.revision_items([{"menu_id": 4, "quantity": 2, "note": "x" * 256}])[1] != ""


def test_revizyon_kalemi_tam_listeyi_kasanin_govdesiyle_ayni_kurar() -> None:
    temiz, hata = dev.revision_items([
        {"menu_id": 4, "quantity": 2, "option_value_ids": [7, 9], "note": "az acı"},
        {"menu_id": 5, "quantity": 1},
    ])
    assert hata == ""
    assert temiz == [{"menu_id": 4, "quantity": 2, "option_value_ids": [7, 9],
                      "note": "az acı"},
                     {"menu_id": 5, "quantity": 1}]


# ========================================================== durum geçişi

def test_matris_disi_gecis_anlasilir_cumleyle_reddedilir() -> None:
    assert dev.status_error("yeni", "onaylandi") == ""
    assert dev.status_error("yeni", "teslim_edildi") != ""
    assert "yalnız şunlara" in dev.status_error("yeni", "teslim_edildi")
    assert "son durumdur" in dev.status_error("teslim_edildi", "iptal")
    assert "zaten" in dev.status_error("hazir", "hazir")
    assert dev.status_error("yeni", "kargoda") != ""


def test_durumu_okunamayan_siparis_burada_engellenmez() -> None:
    # Bilinmeyen bir durumdan geçişi ekranın reddetmesi, sunucunun kabul
    # edeceği bir işi engellemek olurdu. Karar sunucunundur.
    assert dev.status_error("", "onaylandi") == ""
    assert dev.status_error("bilinmeyen", "onaylandi") == ""


# ============================================================ gerekçe

def test_kisa_gerekce_reddedilir_uzun_gerekce_de() -> None:
    assert dev.reason_error("kısa") != ""
    assert dev.reason_error("   ") != ""
    assert dev.reason_error("x" * 161) != ""
    assert dev.reason_error("Yazıcı arızası nedeniyle") == ""


# ============================================================ özet ve fiş

def test_ozet_eksik_bolumu_sifir_gostermez_soyler() -> None:
    gorunum = dev.overview_view({"devices": {"total": 3, "online": 1, "revoked": 1}})
    assert gorunum["devices"]["offline"] == 1
    assert gorunum["sections"] == {"devices": True, "orders": False, "print_jobs": False}
    # Yedi durumun hepsi her zaman çizilir; gelmeyeni 0 gösterir.
    assert len(gorunum["orders"]["by_status"]) == 7


def test_fis_satiri_kurus_bolmez_ve_denetim_kaydidir() -> None:
    satir = dev.print_job_row({"id": 4, "order_id": 12, "order_number": "S-12",
                               "type": "mutfak", "revision": 2,
                               "printed_at": "2026-08-14T08:00:00Z",
                               "device_id": 3, "device_name": "Mutfak Kasası"})
    assert satir["order_id"] == 12 and satir["revision"] == 2

    revizyon = dev.revision_row({"revision_no": 2, "reason": "Müşteri kalem ekledi",
                                 "refund_kurus": 0, "extra_charge_kurus": 1550})
    assert revizyon["extra_charge_kurus"] == 1550
