"""Saf dönüşümler ve doğrulamalar. Ağ yok, depo yok, sadece girdi→çıktı."""

from __future__ import annotations

from datetime import UTC, datetime

from bld_notifications_backend import notices as nt
from bld_notifications_fakes import NOTICE, STATS, STATS_UNTRACKABLE

SIMDI = datetime(2026, 8, 16, 9, 0, 0, tzinfo=UTC)


# =============================================================== düğme adresi

def test_dugme_adresi_yalniz_https_ve_uygulama_ici_yolu_kabul_eder() -> None:
    assert nt.action_url_error("https://benimlezzetdunyam.com.tr/duyuru") == ""
    assert nt.action_url_error("/abonelik") == ""
    assert nt.action_url_error("") == ""


def test_guvenilmeyen_semalar_reddedilir() -> None:
    # Duyuru ÜÇ istemcide birden açılıyor; güvenilmeyen bir şema en az birinde
    # çalıştırılabilir olurdu (sözleşme §POST doğrulama).
    for adres in ("http://ornek.com", "javascript:alert(1)", "data:text/html,x",
                  "ftp://ornek.com"):
        assert nt.action_url_error(adres), f"{adres} geçmemeliydi"


def test_sema_goreli_adres_de_reddedilir() -> None:
    # `//host/yol` GÖRELİ YOL DEĞİLDİR: tarayıcı onu mutlak adres sayar ve
    # kullanıcıyı başka bir siteye götürür. "`/` ile başlıyorsa uygulama
    # içidir" kuralı tam olarak burada yanlış cevap verirdi.
    assert nt.action_url_error("//kotu-site.example/tuzak")
    assert "mutlak" in nt.action_url_error("//kotu-site.example/tuzak")


def test_etiket_ve_adres_birlikte_verilir() -> None:
    assert nt.action_pair_error("Abone ol", "/abonelik") == ""
    assert nt.action_pair_error("", "") == ""
    assert nt.action_pair_error("Abone ol", "")      # adressiz etiket tıklanamaz
    assert nt.action_pair_error("", "/abonelik")     # etiketsiz düğme çizilemez


def test_etiket_uzunlugu_denetlenir() -> None:
    assert nt.action_pair_error("x" * 61, "/abonelik")


# ================================================================ kapatılabilme

def test_kapatilamaz_duyuru_yalniz_kritik_olabilir() -> None:
    # Kapatılamayan bir bilgilendirme, müşteri uygulamasını kullanılamaz hâle
    # getirir: şerit ekranda kalır ve kullanıcı onu kaldıramaz.
    assert nt.dismissible_error(False, "critical") == ""
    assert nt.dismissible_error(False, "info")
    assert nt.dismissible_error(False, "warning")
    assert nt.dismissible_error(True, "info") == ""


# ================================================================== pencere

def test_bitis_baslangictan_sonra_olmali() -> None:
    assert nt.window_error("2026-09-01T00:00:00Z", "2026-08-25T00:00:00Z", now=SIMDI)


def test_gecmis_bitis_reddedilir() -> None:
    # Doğduğu anda bitmiş bir duyuru, yöneticinin fark etmediği bir hatadır.
    problem = nt.window_error("", "2026-08-01T00:00:00Z", now=SIMDI)
    assert "geçmişte" in problem


def test_bos_pencere_serbesttir() -> None:
    assert nt.window_error("", "", now=SIMDI) == ""
    assert nt.window_error("2026-08-20T00:00:00Z", "", now=SIMDI) == ""


def test_bozuk_an_anlasilir_hata_verir() -> None:
    problem = nt.window_error("20.08.2026", "", now=SIMDI)
    assert "ISO 8601" in problem


# ============================================================= kısmi güncelleme

def test_bos_guncelleme_reddedilir() -> None:
    # Yalnız gerekçe taşıyan bir PATCH, hiçbir şey değiştirmeden denetim izine
    # satır yazardı.
    assert nt.patch_error({})


def test_durum_alani_guncellenemez() -> None:
    problem = nt.patch_error({"status": "published"})
    assert "status" in problem


def test_es_alanlar_birlikte_gonderilir() -> None:
    # Kural İKİSİNİN BİRLİKTE hâli hakkındadır ve sözleşmede tek duyuru okuyan
    # bir uç yok: yalnız birini alan bir PATCH, kuralı yerelde doğrulanamaz
    # bırakırdı.
    assert nt.patch_error({"starts_at": "2026-09-01T00:00:00Z"}, now=SIMDI)
    assert nt.patch_error({"action_url": "/abonelik"}, now=SIMDI)
    assert nt.patch_error({"starts_at": "2026-09-01T00:00:00Z",
                           "ends_at": "2026-09-10T00:00:00Z"}, now=SIMDI) == ""


def test_kapatilamaz_yapmak_duzeyi_de_ister() -> None:
    assert nt.patch_error({"dismissible": False}, now=SIMDI)
    assert nt.patch_error({"dismissible": False, "level": "critical"}, now=SIMDI) == ""
    assert nt.patch_error({"dismissible": False, "level": "info"}, now=SIMDI)


def test_pencereyi_bosaltmak_serbesttir() -> None:
    # `None` "temizle" demektir ve geçerli bir değerdir.
    assert nt.patch_error({"starts_at": None, "ends_at": None}, now=SIMDI) == ""


# ================================================================= görünümler

def test_live_sunucudan_gelir_yeniden_hesaplanmaz() -> None:
    # Sunucu `live: true` diyorsa satır da öyle der; pencere istemcide yeniden
    # yorumlanmaz. Saati kaymış bir panel, aksi hâlde duyuruyu bir gün erken
    # "bitmiş" gösterirdi.
    satir = nt.notice_row({**NOTICE, "live": True}, server_time="2026-08-16T09:00:00Z")
    assert satir["live"] is True
    assert satir["visibility"] == "live"


def test_gorunurluk_uc_ayri_durumu_ayirir() -> None:
    ortak = {**NOTICE, "live": False}
    gelecek = nt.notice_row({**ortak, "starts_at": "2026-08-20T00:00:00Z",
                             "ends_at": "2026-08-31T00:00:00Z"},
                            server_time="2026-08-16T09:00:00Z")
    assert gelecek["visibility"] == "scheduled"

    bitmis = nt.notice_row({**ortak, "starts_at": "2026-07-01T00:00:00Z",
                            "ends_at": "2026-08-01T00:00:00Z"},
                           server_time="2026-08-16T09:00:00Z")
    assert bitmis["visibility"] == "expired"

    taslak = nt.notice_row({**ortak, "status": "draft"},
                           server_time="2026-08-16T09:00:00Z")
    assert taslak["visibility"] == "draft"

    arsiv = nt.notice_row({**ortak, "status": "archived", "live": False},
                          server_time="2026-08-16T09:00:00Z")
    assert arsiv["visibility"] == "archived"


def test_sunucu_gorunmuyor_diyorsa_ekran_gorunuyor_demez() -> None:
    # Penceresi uygun ama `live: false` gelen kayıt: sebep BİLİNMİYOR ve
    # uydurulmaz. Sessizce "görünüyor" demek yalan olurdu.
    satir = nt.notice_row({**NOTICE, "live": False, "starts_at": None, "ends_at": None},
                          server_time="2026-08-16T09:00:00Z")
    assert satir["visibility"] == "hidden"


def test_kitlesi_herkes_olan_duyuru_olculemez_isaretlenir() -> None:
    satir = nt.notice_row({**NOTICE, "audience": "all"})
    assert satir["trackable"] is False


def test_bitise_kalan_sure_sunucu_saatiyle_olculur() -> None:
    satir = nt.notice_row({**NOTICE, "ends_at": "2026-08-17T09:00:00Z"},
                          server_time="2026-08-16T09:00:00Z")
    assert satir["ends_in_hours"] == 24.0


# ================================================================ istatistik

def test_olculemeyen_duyuruda_null_korunur() -> None:
    # SIFIR "kimse görmedi" demektir, `null` "ölçülemiyor". İkisini
    # karıştırmak, çalışan bir duyuruyu başarısız gösterirdi.
    gorunum = nt.stats_view(STATS_UNTRACKABLE)
    assert gorunum["trackable"] is False
    assert gorunum["seen_count"] is None
    assert gorunum["seen_rate"] is None
    assert gorunum["daily"] is None


def test_olculebilen_duyuruda_sayilar_gecer() -> None:
    gorunum = nt.stats_view(STATS)
    assert gorunum["seen_count"] == 84
    assert gorunum["dismissed_count"] == 51
    assert gorunum["seen_rate"] == 0.39
    assert [gun["seen"] for gun in gorunum["daily"]] == [46, 22]


def test_trackable_bayragi_yoksa_kitleden_turetilir() -> None:
    gorunum = nt.stats_view({"id": 5, "audience": "all"})
    assert gorunum["trackable"] is False


# ================================================================== denetim

def test_denetim_kunyesi_govdenin_tamamini_tasimaz() -> None:
    # Sözleşme §"Denetim eylemleri": başlık + kitle + gövde UZUNLUĞU.
    kunye = nt.audit_detail({"title": "Bayram", "audience": "customers",
                             "body": "x" * 300, "level": "info"})
    assert kunye == {"title": "Bayram", "audience": "customers", "level": "info",
                     "body_length": 300}
    assert "body" not in kunye


def test_sozlukler_sozlesmedeki_degerleri_tasir() -> None:
    referans = nt.reference()
    assert [item["value"] for item in referans["levels"]] == ["info", "warning", "critical"]
    assert [item["value"] for item in referans["audiences"]] == ["all", "customers",
                                                                "subscribers"]
    assert [item["value"] for item in referans["statuses"]] == ["draft", "published",
                                                               "archived"]
    # Kitlesi `all` olan duyuru ölçülemez ve sözleşme bunu açıkça söylüyor.
    assert referans["audiences"][0]["trackable"] is False
    assert referans["limits"]["title_max"] == 160
    assert referans["limits"]["body_max"] == 2000
