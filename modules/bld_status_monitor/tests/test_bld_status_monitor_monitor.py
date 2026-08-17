"""Saf yardımcılar — biçim, parmak izi, kutu türetmesi ve ön denetim.

Bu dosya AĞA ÇIKMAZ ve DEPOYA DOKUNMAZ; `monitor.py`'nin tamamı yan etkisiz
olduğu için tek tek sınanabilir.
"""

from __future__ import annotations

from bld_status_monitor_backend import monitor as mon
from bld_status_monitor_fakes import HEALTHY, SUMMARY

# ============================================================== parmak izi

def test_ayni_hatanin_iki_tekrari_ayni_parmak_izini_verir() -> None:
    # Sözleşmedeki kuralın aynısı: "Sipariş 8421 basılamadı" ile "Sipariş 8422
    # basılamadı" AYNI olayın iki tekrarıdır. Ayrı sayılsalardı defter bir
    # günde okunamaz hâle gelirdi.
    ilk = mon.fingerprint(source="mutfakapp", code="printer_unreachable",
                          message="Sipariş 8421 basılamadı")
    ikinci = mon.fingerprint(source="mutfakapp", code="printer_unreachable",
                             message="Sipariş 8422 basılamadı")
    assert ilk == ikinci


def test_farkli_kaynak_ayri_parmak_izi_uretir() -> None:
    # Aynı mesaj iki bileşenden gelirse iki ayrı olaydır: biri düzeldiğinde
    # ötekinin de düzeldiğini sanmak, arızayı görünmez kılardı.
    assert mon.fingerprint(source="mutfakapp", code="x", message="aynı") != \
        mon.fingerprint(source="website", code="x", message="aynı")


def test_uuid_once_normallesir_sonra_sayilar() -> None:
    # UUID sonra çalıştırılsaydı sayı deseni UUID'nin içindeki rakam
    # öbeklerini yer ve her UUID başka bir dizeye dönerdi — tekilleştirme
    # sessizce hiçbir işe yaramazdı.
    ilk = mon.normalize_message("oturum 3f2504e0-4f89-11d3-9a0c-0305e82c3301 düştü")
    ikinci = mon.normalize_message("oturum 9c858901-8a57-4791-81fe-4c455b099bc9 düştü")
    assert ilk == ikinci == "oturum <id> düştü"


def test_normallesme_sayilari_isaretle_degistirir() -> None:
    assert mon.normalize_message("kuyrukta 41 iş var") == "kuyrukta <n> iş var"


# ================================================================ üç durum

def test_uc_durumlu_alan_none_kalir() -> None:
    # `bool(None)` yazmak pahalıya patlardı: sağlık bildirmemiş bir kasa
    # ARIZALI SAYILMAZ (`monitor.md` → "Üç durumlu alanlar korunur").
    assert mon.as_bool(None) is None
    assert mon.as_bool(False) is False
    assert mon.as_bool("evet") is True


def test_bildirmeyen_kasa_arizali_gosterilmez() -> None:
    satir = mon.device_row({"device_id": 5, "name": "Yeni Kasa", "online": True,
                            "printer_ok": None, "sound_ok": None, "alarm_muted": None})
    assert satir["printer_ok"] is None
    assert satir["state_tone"] == "good"
    assert satir["printer_label"] == "Yazıcı bildirilmedi"


def test_iptal_edilmis_kasa_listede_kalir_ve_isaretlenir() -> None:
    # "O kasa neredeydi" sorusunun cevabı listede olmalı (sözleşme).
    satir = mon.device_row({"device_id": 3, "online": False, "revoked": True})
    assert satir["revoked"] is True
    assert satir["state_label"] == "İptal edilmiş"


# ================================================================== özet

def test_ozet_eksik_govdede_bile_iskeleti_korur() -> None:
    # Alan eksik gelse bile panel `undefined` okumaz; sıfır ile "bilinmiyor"
    # ayrımı burada kaybolmaz.
    bos = mon.summary_view({})
    assert bos["health"]["status"] == "unknown"
    assert bos["events"]["open"] == dict.fromkeys(mon.LEVELS, 0)
    assert set(bos["events"]["by_source"]) == set(mon.SOURCES)


def test_saglik_hukmu_sunucudan_okunur_yeniden_hesaplanmaz() -> None:
    gorunum = mon.summary_view({"data": SUMMARY})
    assert gorunum["health"]["status"] == "degraded"
    assert gorunum["health"]["label"] == "Aksıyor"
    # Makine okunur etiketler Türkçeye çevrilir ama HAM HÂLİ DE KALIR: sunucuya
    # yeni bir sebep eklenirse ekran onu gizlemez, olduğu gibi gösterir.
    assert gorunum["health"]["reasons"] == ["printer_fault", "critical_event_open"]
    assert "Yazıcı arızası bildiren kasa var" in gorunum["health"]["reason_labels"]


def test_taninmayan_sebep_gizlenmez() -> None:
    gorunum = mon.summary_view({"health": {"status": "down", "reasons": ["disk_full"]}})
    assert gorunum["health"]["reason_labels"] == ["disk_full"]


def test_open_total_toplanmaz_sunucudan_okunur() -> None:
    # Dört seviyeyi toplamak, sunucu beşinci bir seviye eklediğinde sessizce
    # yanlış toplam üretirdi.
    gorunum = mon.summary_view({"events": {"open_total": 99,
                                           "open": {"info": 1, "warning": 1}}})
    assert gorunum["events"]["open_total"] == 99


# ================================================================= kutular

def test_dort_kutu_her_zaman_cizilir() -> None:
    kutular = mon.component_tiles(mon.summary_view({"data": SUMMARY}),
                                  SUMMARY["devices"], connected=True)
    assert [kutu["key"] for kutu in kutular] == list(mon.COMPONENT_KEYS)


def test_baglanti_yoksa_dort_kutu_da_bilinmiyor_olur() -> None:
    # `unknown` ile `down` KARIŞTIRILMAZ: ilki "soramadım", ikincisi "sordum,
    # kötü". İkisini aynı göstermek, kopmuş bir ağı çökmüş bir sisteme
    # çevirirdi.
    kutular = mon.component_tiles(mon.summary_view({}), {}, connected=False)
    assert {kutu["status"] for kutu in kutular} == {"unknown"}
    assert all(kutu["tone"] == "dim" for kutu in kutular)


def test_saglikli_sistemde_kutular_yesil() -> None:
    kutular = mon.component_tiles(mon.summary_view({"data": HEALTHY}),
                                  HEALTHY["devices"], connected=True)
    assert {kutu["status"] for kutu in kutular} == {"ok"}


def test_kasa_kutusu_donanim_arizasini_olaydan_bagimsiz_gorur() -> None:
    # Yazıcısı bozuk ama hata BİLDİRMEMİŞ bir kasa olay üretmez; yalnız olay
    # sayısına bakan bir kutu yeşil kalırdı.
    ozet = mon.summary_view({"data": HEALTHY})
    kutular = mon.component_tiles(ozet, {"total": 2, "online": 2, "printer_fault": 1},
                                  connected=True)
    kds = next(kutu for kutu in kutular if kutu["key"] == "kds")
    assert kds["status"] == "degraded"
    assert "1 kasada yazıcı arızası" in kds["notes"]


def test_hicbir_kasa_cevrimici_degilse_kutu_durdu_der() -> None:
    ozet = mon.summary_view({"data": HEALTHY})
    kutular = mon.component_tiles(ozet, {"total": 2, "online": 0}, connected=True)
    kds = next(kutu for kutu in kutular if kutu["key"] == "kds")
    assert kds["status"] == "down"


def test_kritik_olay_tek_kaynak_varken_atfedilir_coklukta_atfedilmez() -> None:
    # Sözleşme kritik olayın HANGİ bileşende olduğunu söylemiyor
    # (`critical_open` bütün için tek sayı). Tek kaynağın olayı varsa kritik
    # onun olmak zorundadır; ötesi TAHMİNDİR ve tahmin edilmez — yanlış ekibi
    # sahaya göndermek pahalıdır.
    tek = mon.summary_view({"events": {"critical_open": 1,
                                       "by_source": {"website": 2}}})
    kutular = mon.component_tiles(tek, {}, connected=True)
    web = next(kutu for kutu in kutular if kutu["key"] == "web")
    assert web["status"] == "down"

    coklu = mon.summary_view({"events": {"critical_open": 1,
                                         "by_source": {"website": 2, "platform": 1}}})
    kutular = mon.component_tiles(coklu, {}, connected=True)
    assert {kutu["status"] for kutu in kutular if kutu["open_events"] > 0} == {"degraded"}


# ============================================================ düzeltme defteri

def test_defter_yalniz_kapali_listedeki_eylemi_kabul_eder() -> None:
    # Defter satırı bir veritabanı kaydıdır; oradan okunan bir adı
    # `getattr(api, name)` ile çağırmak, deftere yazabilen birine geçidin
    # BÜTÜN metotlarını açardı.
    assert mon.runbook_error(key="printer.test", title="Test fişi", channel="bld.api",
                             action="kds.test_receipt", device_id=2) == ""
    hata = mon.runbook_error(key="kotu", title="Kötü", channel="bld.api",
                             action="cancel_order", device_id=2)
    assert "çalıştırılabilir bir eylem değil" in hata


def test_cihaz_isteyen_eylem_cihazsiz_kabul_edilmez() -> None:
    hata = mon.runbook_error(key="printer.test", title="Test fişi", channel="bld.api",
                             action="kds.test_receipt", device_id=0)
    assert "cihaz seçilmedi" in hata


def test_elle_yapilan_adim_komut_tasiyamaz() -> None:
    # `manual` kanallı bir kaydın gerçek bir komut taşıması, çalıştırılamayan
    # bir düğmenin çalışıyormuş gibi görünmesi olurdu.
    assert mon.runbook_error(key="sunucu.restart", title="Servisi yeniden başlat",
                             channel="manual", action=mon.MANUAL_ACTION,
                             device_id=0) == ""
    hata = mon.runbook_error(key="sunucu.restart", title="Servisi yeniden başlat",
                             channel="manual", action="kds.restart", device_id=2)
    assert "geçitten geçmeyen" in hata


def test_defter_anahtari_serbest_metin_degildir() -> None:
    assert "Defter anahtarı" in mon.runbook_error(
        key="Kötü Anahtar!", title="x" * 5, channel="bld.api",
        action="kds.test_receipt", device_id=1)


def test_elle_yapilan_kayit_calistirilabilir_gorunmez() -> None:
    satir = mon.runbook_row({"key": "sunucu.restart", "title": "Servisi başlat",
                             "channel": "manual", "action": mon.MANUAL_ACTION,
                             "device_id": 0, "enabled": 1})
    assert satir["runnable"] is False
    assert satir["action_label"] == "Elle yapılır"


def test_yikici_eylem_uyarisiyla_birlikte_gelir() -> None:
    satir = mon.runbook_row({"key": "kasa.restart", "title": "Yeniden başlat",
                             "channel": "bld.api", "action": "kds.restart",
                             "device_id": 2, "enabled": 1})
    assert satir["destructive"] is True
    assert satir["warning"]


def test_yeniden_basim_ve_esleme_kaldirma_defterde_yok() -> None:
    # `reprint` sipariş kimliği ister ve o kimlik OLAYA ÖZELDİR: deftere
    # "8421 numaralı fişi bas" yazmak ertesi gün başka bir siparişi bastırırdı.
    # `unpair` ise bir düzeltme değil, kasayı sahada yeni kod girilene kadar
    # sipariş göremez hâle getirmektir.
    assert "kds.reprint" not in mon.RUNBOOK_ACTIONS
    assert "kds.unpair" not in mon.RUNBOOK_ACTIONS


def test_defter_komutlari_kds_sozlesmesindeki_adlari_tasir() -> None:
    # Sunucuda karşılığı olmayan bir ad, kuyruğa atılıp kasada sessizce yok
    # sayılan bir komut demektir; yönetici "gitti" sanır.
    taninan = {"test_receipt", "reprint", "clear_failed", "silence_alarm",
               "restart", "update", "unpair", "clear_queue"}
    assert {spec.command for spec in mon.RUNBOOK_ACTIONS.values()} <= taninan


# ================================================================ süzgeç

def test_taninmayan_seviye_reddedilir_sessizce_elenmez() -> None:
    # Sunucu tanımadığı kodu süzgece koyar ve sonuç boş döner; ekran o boşluğu
    # "hata yok" diye gösterirdi — izleme ekranının en tehlikeli yalanı bu.
    kodlar, hata = mon.csv_filter("error,kritik", mon.LEVELS, field="seviye")
    assert kodlar == []
    assert "Tanınmayan seviye" in hata


def test_seviye_suzgeci_sozlesme_sirasina_cekilir() -> None:
    kodlar, hata = mon.csv_filter("critical,warning,warning", mon.LEVELS, field="seviye")
    assert (kodlar, hata) == (["warning", "critical"], "")


def test_since_gun_ya_da_an_kabul_eder_bozugu_reddeder() -> None:
    assert mon.since_error("2026-08-16") == ""
    assert mon.since_error("2026-08-16T09:00:00Z") == ""
    assert "okunamadı" in mon.since_error("dün")


def test_arama_anahtari_aksansiz_esler() -> None:
    assert mon.foldable("Yazıcı") == "yazici"
    assert mon.foldable("ŞÖFÖR") == "sofor"


# ============================================================== sözleşme

def test_ekran_sozlesmesi_varsayilan_suzgeci_isaretler() -> None:
    # `info` varsayılan süzgeçte GİZLİDİR: bilgi seviyesindeki olaylar sayıca
    # en kalabalık olanlardır ve gerçek hataları görünmez kılarlar.
    sozlesme = mon.screen_contract()
    gizli = [item for item in sozlesme["levels"] if not item["in_default_filter"]]
    assert [item["code"] for item in gizli] == ["info"]
    assert sozlesme["default_levels"] == ["warning", "error", "critical"]


def test_ekran_sozlesmesi_dort_kutuyu_ve_kanallari_tasir() -> None:
    sozlesme = mon.screen_contract()
    assert [item["key"] for item in sozlesme["components"]] == list(mon.COMPONENT_KEYS)
    assert [item["code"] for item in sozlesme["channels"]] == list(mon.CHANNELS)
    assert sozlesme["reason"] == {"min": 10, "max": 160, "note_max": 500}
