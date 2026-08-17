"""Saf yardımcılar — biçim, sözlük ve panel eşleştirmesi. AĞA ÇIKMAZ.

Buradaki testlerin tamamı yan etkisiz fonksiyonlara bakar; servis ve HTTP
katmanı ayrı dosyalarda sınanır.
"""

from __future__ import annotations

from bld_dashboard_backend import dashboard as db

# ============================================================ tip çevirisi

def test_bilinmeyen_deger_sifira_cevrilmez() -> None:
    # AYRIM HAYATİ: menü yayınlanmamışken `capacity_total` `null` döner ve
    # sıfıra çevirmek "gün doldu" demek olurdu — ekran satışın kapandığını
    # sanardı.
    assert db.opt_int(None) is None
    assert db.opt_int("") is None
    assert db.opt_int(0) == 0
    assert db.opt_int("34") == 34
    assert db.opt_int("otuz") is None


def test_uc_degerli_mantik_bilinmiyor_ile_yanlisi_ayirir() -> None:
    # Sağlık bildirmemiş bir kasa ARIZALI SAYILMAZ; `None` "bilinmiyor" der.
    assert db.as_bool(None) is None
    assert db.as_bool("") is None
    assert db.as_bool(False) is False
    assert db.as_bool("true") is True
    assert db.as_bool("hayır") is False
    assert db.as_bool("belki") is None


def test_gun_bicimi_denetlenir_bos_gun_serbesttir() -> None:
    # Boş gün "sunucu bugünü kullansın" demektir ve reddedilmez.
    assert db.date_error("") == ""
    assert db.date_error("2026-08-16") == ""
    assert db.date_error("16.08.2026")
    assert db.date_error("2026-13-01")


# ================================================================ bloklar

def test_kapasite_yayinlanmamis_menude_null_kalir() -> None:
    blok = db.capacity_block({
        "menu_published": False, "capacity_total": None, "sold_total": None,
        "fill_rate": None, "blocked_items": [],
    })
    assert blok["menu_published"] is False
    assert blok["capacity_total"] is None
    assert blok["fill_rate"] is None


def test_by_status_bes_kodla_tamamlanir() -> None:
    # Sözleşme beş kodun sipariş yokken bile `0` ile duracağını söylüyor; gövde
    # eksik gelirse ekranın savunma yazması gerekirdi. Eksiği burada doldurmak,
    # yığılmış çubuğun her yoklamada aynı beş dilimi çizmesi demektir —
    # dilim sayısı değişseydi grafik her turda sıçrardı.
    blok = db.orders_block({"by_status": {"yeni": 4}})
    assert set(blok["by_status"]) == set(db.ACTIVE_STATUS_CODES)
    assert blok["by_status"]["yeni"] == 4
    assert blok["by_status"]["yolda"] == 0


def test_by_status_terminal_kodlari_tasimaz() -> None:
    # `teslim_edildi` ve `iptal` aktif kümenin dışındadır ve sözleşme onları
    # `by_status` içinde saymıyor. Sunucu yanlışlıkla gönderirse de girmez:
    # aktif toplamı şişiren bir dilim, "kaç sipariş açık" sorusunu bozardı.
    blok = db.orders_block({"by_status": {"yeni": 1, "teslim_edildi": 41, "iptal": 2}})
    assert "teslim_edildi" not in blok["by_status"]
    assert "iptal" not in blok["by_status"]


def test_bloklar_bos_govdeden_de_ayni_sekilde_uretilir() -> None:
    # Bağlantı yokken panel AYNI ŞEKİLLİ gövde görmeli; alan yokluğu savunması
    # yazdırmak, o savunmanın unutulduğu tek satırda ekranı çökertirdi.
    for blok in (db.sales_block(None), db.orders_block(None), db.capacity_block(None),
                 db.subscriptions_block(None), db.devices_block(None)):
        assert isinstance(blok, dict)
    assert db.sales_block(None)["seconds_to_next_cutoff"] is None
    assert db.capacity_block(None)["blocked_items"] == []
    assert db.monitor_block(None)["health_tone"] == "dim"


def test_saglik_hukmu_sunucudan_gelir_turetilmez() -> None:
    # `health.status` SUNUCUNUN tek cümlelik hükmüdür; üç ekranın aynı duruma
    # bakıp farklı renk göstermesi hangisine inanılacağını belirsiz kılardı.
    blok = db.monitor_block({"health_status": "degraded", "critical_open": 1})
    assert blok["health_label"] == "Aksıyor"
    assert blok["health_tone"] == "warn"
    # Tanınmayan hüküm uydurulmaz: etiket ham değerdir, ton nötr.
    yabanci = db.monitor_block({"health_status": "kaput"})
    assert yabanci["health_label"] == "kaput"
    assert yabanci["health_tone"] == "dim"


def test_engellenen_kalem_listesi_on_ile_sinirli() -> None:
    kalemler = [{"menu_id": i, "name": f"Ürün {i}", "capacity": 10, "sold": 10}
                for i in range(20)]
    blok = db.capacity_block({"menu_published": True, "blocked_items": kalemler})
    assert len(blok["blocked_items"]) == 10


# ========================================================== bekleyen işler

def test_bekleyen_isler_seviyeye_gore_dizilir_grup_ici_sira_korunur() -> None:
    # Grup içi sırayı yeniden hesaplamak, sunucunun bildiği aciliyeti (kesim
    # saatine kalan süre gibi) kaybetmek olurdu.
    satirlar = db.pending_tasks([
        {"code": "quote_requests_new", "level": "warning", "title": "A"},
        {"code": "unreleased_orders", "level": "info", "title": "B"},
        {"code": "menu_missing", "level": "critical", "title": "C"},
        {"code": "printer_fault", "level": "warning", "title": "D"},
    ])
    assert [row["level"] for row in satirlar] == ["critical", "warning", "warning", "info"]
    assert [row["title"] for row in satirlar] == ["C", "A", "D", "B"]


def test_cumle_sunucudan_gelir_panel_kendi_metnini_yazmaz() -> None:
    satir = db.pending_task({
        "code": "menu_missing", "level": "critical",
        "title": "Yarının menüsü girilmemiş",
        "detail": "17 Ağustos için yayınlanmış menü yok.",
        "count": 1, "link": "/menu/days/2026-08-17",
    })
    assert satir["title"] == "Yarının menüsü girilmemiş"
    assert satir["detail"] == "17 Ağustos için yayınlanmış menü yok."
    assert satir["level_label"] == "Kritik"
    assert satir["tone"] == "bad"


def test_taninmayan_kod_yine_gosterilir() -> None:
    # Sunucu sözleşmeye yeni bir madde eklediğinde panelin onu sessizce
    # yutması, yöneticinin yapması gereken bir işi HİÇ GÖRMEMESİ olurdu.
    satir = db.pending_task({"code": "yeni_bir_sey", "level": "warning",
                             "title": "X", "link": "/orders"})
    assert satir["known"] is False
    assert satir["title"] == "X"
    assert satir["panel"] == "bld_orders"


def test_taninmayan_seviye_kritik_degil_bilgi_sayilir() -> None:
    # Kritik saymak, sunucunun yazım hatasını ekranda kırmızı bir alarma
    # çevirirdi.
    satir = db.pending_task({"code": "x", "level": "acil", "title": "X"})
    assert satir["level"] == "info"


def test_bekleyen_isler_on_iki_ile_sinirli() -> None:
    satirlar = db.pending_tasks([{"code": f"k{i}", "level": "warning", "title": str(i)}
                                 for i in range(30)])
    assert len(satirlar) == 12


# ======================================================= panel eşleştirmesi

def test_yol_oneki_panele_cevrilir_kod_degil() -> None:
    # Eşleştirme YOLUN İLK PARÇASINA bakar: `menu_missing` de `menu_draft` de
    # `/menu/...` ile başlar ve ikisi de aynı ekrana gider. Ondört kodu tek tek
    # eşleyen bir tablo, on beşinci kod eklendiğinde tıklanamaz satır üretirdi.
    assert db.panel_for_link("/menu/days/2026-08-17")["panel"] == "bld_menu"
    assert db.panel_for_link("/menu/anything/else")["panel"] == "bld_menu"
    assert db.panel_for_link("/monitor/devices")["panel"] == "bld_status_monitor"
    assert db.panel_for_link("/settings")["panel"] == "bld_sales_settings"


def test_bilinmeyen_yol_panel_vermez() -> None:
    # Hiçbir yere gitmeyen bir düğme, bozuk bir düğmedir: panel o satıra düğme
    # koymaz ve ham yolu yazıyla gösterir.
    sonuc = db.panel_for_link("/bilinmeyen/alan")
    assert sonuc["panel"] == ""
    assert sonuc["link"] == "/bilinmeyen/alan"

    for bos in ("", None, "menu/days", "https://baska.site/menu"):
        assert db.panel_for_link(bos)["panel"] == ""


def test_sorgu_dizesi_ve_tarih_baglami_tasinir() -> None:
    sonuc = db.panel_for_link("/subscriptions/requests?status=yeni")
    assert sonuc["panel"] == "bld_subscriptions"
    assert sonuc["payload"]["status"] == "yeni"
    assert sonuc["payload"]["path"] == ["requests"]

    gun = db.panel_for_link("/menu/days/2026-08-17")
    assert gun["payload"]["date"] == "2026-08-17"


def test_acik_verilen_tarih_yoldan_tahmin_edileni_yener() -> None:
    sonuc = db.panel_for_link("/menu/days/2026-08-17?date=2026-08-20")
    assert sonuc["payload"]["date"] == "2026-08-20"


# ================================================================== akış

def test_akis_satiri_musteri_telefonunu_tasimaz() -> None:
    # Gösterge paneli açık bir ekranda saatlerce durur; numaraya ihtiyacı olan
    # zaten Sipariş Yönetimi ekranına gider.
    satir = db.flow_row({"id": 8421, "order_number": "BLD-8421",
                         "status": "hazirlaniyor", "customer_phone": "5321234567",
                         "customer_name": "Mehmet Kaya"})
    assert "customer_phone" not in satir
    assert satir["customer_name"] == "Mehmet Kaya"
    assert satir["status_label"] == "Hazırlanıyor"
    assert satir["status_tone"] == "warn"


def test_bilinmeyen_durum_kodu_ham_haliyle_gosterilir() -> None:
    # Uydurma bir etiket yazmak, sunucunun eklediği yeni bir durumu yanlış
    # adla göstermek olurdu.
    satir = db.flow_row({"id": 1, "status": "beklemede"})
    assert satir["status_label"] == "beklemede"
    assert satir["status_tone"] == "dim"


# ============================================================== sözleşme

def test_ekran_sozlesmesi_aga_cikmadan_cizilebilir_her_seyi_tasir() -> None:
    # Geçit düşükken bile rozetler ve seviye adları çizilebilmeli: boş bir
    # ekran "sunucu düştü" ile "bugün hiç sipariş yok" arasındaki farkı
    # anlatamaz (K7).
    sozlesme = db.screen_contract()
    assert len(sozlesme["active_status_codes"]) == 5
    assert len(sozlesme["task_codes"]) == 14
    assert sozlesme["level_labels"]["critical"] == "Kritik"
    assert sozlesme["panel_routes"]["orders"] == "bld_orders"


def test_panel_kimlikleri_modul_klasoru_adlandirmasina_uyar() -> None:
    # Yazım hatası (`bld_subscription`) yalnız TIKLANDIĞINDA anlaşılırdı:
    # kabuk bilmediği bir kimliği sessizce yok sayar ve düğme hiçbir şey
    # yapmaz. Burada sabitlemek, o sessizliği teste çevirir.
    for panel in db.PANEL_ROUTES.values():
        assert panel.startswith("bld_"), panel
        assert panel.islower() and " " not in panel
