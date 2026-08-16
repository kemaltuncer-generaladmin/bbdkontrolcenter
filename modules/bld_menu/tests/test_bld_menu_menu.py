"""Saf dönüşümlerin sınavı — `backend/menu.py`. Ağ yok, depo yok.

Buradaki iddiaların çoğu sözleşmenin bir cümlesine karşılık gelir; hangisine,
her testin ilk satırında yazıyor.
"""

from __future__ import annotations

from bld_menu_backend import menu as mn
from bld_menu_fakes import CALENDAR, DAY, STOCK, STOCK_RESULT

# ================================================================ doğrulama

def test_gerekce_alt_ve_ust_sinir() -> None:
    # `00-genel.md` §3: en az 10, en çok 500 karakter.
    assert mn.reason_error("kısa")
    assert mn.reason_error("x" * 501)
    assert mn.reason_error("x" * 500) == ""
    assert mn.reason_error("Tavuk tedariki azaldı") == ""


def test_kesim_saati_bos_birakilabilir() -> None:
    # `null` = "küresel ayar geçerli" ve bu alanın EN SIK kullanılan hâli.
    assert mn.cutoff_error(None) == ""
    assert mn.cutoff_error("") == ""
    assert mn.cutoff_error("08:00") == ""
    assert mn.cutoff_error("23:59") == ""
    assert mn.cutoff_error("24:00")
    assert mn.cutoff_error("8:00")
    assert mn.cutoff_error("08:60")


def test_paket_fiyati_sifir_kabul_etmez_ama_bos_kabul_eder() -> None:
    # Sözleşme: verilirse > 0. Sıfır "bedava paket" demektir ve `LineResolver`
    # onu zaten reddediyor; `null` ise "paket satılmıyor".
    assert mn.price_error(None) == ""
    assert mn.price_error(18000) == ""
    assert mn.price_error(0)
    assert mn.price_error(-1)


def test_tavan_sifiri_kabul_eder_negatifi_etmez() -> None:
    # `null` = sınırsız, `0` = "bugün satılmıyor" ve GEÇERLİ bir değer.
    assert mn.capacity_error(None) == ""
    assert mn.capacity_error(0) == ""
    assert mn.capacity_error(120) == ""
    assert mn.capacity_error(-1)


def test_null_tavan_ile_sifir_tavan_ayri_kalir() -> None:
    # İkisini aynı sayıya indirmek satışı sessizce kapatır ya da açardı.
    assert mn.opt_int(None) is None
    assert mn.opt_int("") is None
    assert mn.opt_int(0) == 0


def test_gecmise_menu_kurulmaz_ama_bugune_kurulur() -> None:
    # Sabah kesim saatinden önce o günün menüsünü kurmak olağan iştir.
    assert mn.past_error("2026-08-16", "2026-08-17")
    assert mn.past_error("2026-08-17", "2026-08-17") == ""
    assert mn.past_error("2026-08-18", "2026-08-17") == ""
    assert mn.past_error("2026-13-01", "2026-08-17")


def test_yayin_on_denetimi_uc_kurali_da_uygular() -> None:
    # Sözleşme → publish: gün taslak olmalı, en az bir kalem bulunmalı, paket
    # fiyatı yoksa bileşen satışı açık olmalı.
    yayinda = mn.day_view(DAY)
    assert "zaten yayında" in mn.publish_error(yayinda)

    taslak = mn.day_view({**DAY, "status": "draft"})
    assert mn.publish_error(taslak) == ""

    kalemsiz = mn.day_view({**DAY, "status": "draft", "items": []})
    assert "en az bir kalem" in mn.publish_error(kalemsiz)

    satilamaz = mn.day_view({**DAY, "status": "draft", "package_price_kurus": None,
                             "components_sellable": False})
    assert "hiçbir şey satılamaz" in mn.publish_error(satilamaz)

    # Paket yok ama kalemler tek tek satılıyor → geçerli.
    bilesen = mn.day_view({**DAY, "status": "draft", "package_price_kurus": None,
                           "components_sellable": True})
    assert mn.publish_error(bilesen) == ""


def test_ayni_gune_kopyalama_reddedilir() -> None:
    assert mn.duplicate_error("2026-08-17", "2026-08-17", "2026-08-17")
    assert mn.duplicate_error("2026-08-17", "2026-08-24", "2026-08-17") == ""
    assert mn.duplicate_error("2026-08-17", "2026-08-10", "2026-08-17")


# ================================================================ görünümler

def test_gun_gorunumu_sozlesmenin_alanlarini_tasir() -> None:
    view = mn.day_view(DAY)
    assert view["date"] == "2026-08-17"
    assert view["package_price_kurus"] == 18000
    assert view["status"] == "published"
    assert view["status_label"] == "Yayında"
    assert view["item_count"] == 2
    # Sıra SUNUCUDAN gelir ve korunur: çorba → ana yemek.
    assert [item["sort_order"] for item in view["items"]] == [10, 20]
    assert view["items"][0]["name"] == "Günün Çorbası: Mercimek"
    # `label` boşsa `None` kalır; boş dizeye çevrilmez.
    assert view["items"][1]["label"] is None


def test_tanimsiz_durum_taslaga_duser() -> None:
    # Sunucudan tanınmayan bir durum gelirse ekran onu YAYINDA göstermemeli:
    # yayında sanılan bir taslak, müşteriye görünmeyen bir menüyü görünüyor
    # sanmaktır.
    assert mn.day_view({**DAY, "status": "arsiv"})["status"] == "draft"


def test_takvimde_menusu_olmayan_gun_de_gelir() -> None:
    # Eksik günü atmak, ızgarayı çizen ekranı boşlukları kendi hesaplamaya
    # zorlardı ve "22 Ağustos'a menü girilmemiş" tam da görülmesi gereken şey.
    rows = [mn.calendar_row(row) for row in CALENDAR]
    assert rows[0]["has_menu"] is True
    assert rows[1]["has_menu"] is False
    assert rows[1]["id"] is None
    assert rows[1]["status"] is None


def test_kapali_gun_nedeni_turkce_etiket_alir() -> None:
    row = mn.calendar_row(CALENDAR[0])
    assert row["not_orderable_reason"] == "cutoff_passed"
    assert row["not_orderable_label"] == "Kesim saati geçti"


def test_tanimsiz_kapalilik_nedeni_ham_haliyle_gecer() -> None:
    # Boş bırakmak, ekranda nedensiz bir "sipariş alınmıyor" rozeti bırakırdı.
    row = mn.calendar_row({**CALENDAR[0], "not_orderable_reason": "yeni_sebep"})
    assert row["not_orderable_label"] == "yeni_sebep"


def test_tukendi_rozeti_yalniz_tavan_varken_cikar() -> None:
    # `remaining_total` `null` iken tavan hiç konmamıştır; "tükendi" demek
    # yanlış olurdu. Sıfır ile `null` karıştırılmaz.
    dolu = mn.calendar_row({**CALENDAR[0], "remaining_total": 0})
    assert dolu["sold_out"] is True

    tavansiz = mn.calendar_row({**CALENDAR[0], "capacity_total": None,
                                "remaining_total": None})
    assert tavansiz["sold_out"] is False


# ====================================================================== stok

def test_kalan_negatife_dusmez_ve_oversold_bayragi_cikar() -> None:
    # Sözleşme: tavan satılmışın altına çekilirse `remaining` 0 olur ve gerçek
    # bilgiyi `oversold` taşır. "-14 porsiyon kaldı" ekranda anlamsızdır.
    line = mn.stock_line({"capacity": 50, "sold": 64, "sold_orders": 44,
                          "sold_subscriptions": 20})
    assert line["remaining"] == 0
    assert line["oversold"] is True
    assert line["full"] is True


def test_tavansiz_satirda_kalan_null_kalir() -> None:
    # Sıfır "doldu", `null` "tavan konmamış" — ikisi karıştırılmamalı.
    line = mn.stock_line({"capacity": None, "sold": 86})
    assert line["remaining"] is None
    assert line["full"] is False
    assert line["oversold"] is False


def test_abonelik_payi_ayri_durur() -> None:
    # İş kararı 6: abonelikler stoku ÖNCE rezerve eder ve yönetici "34 porsiyon
    # kaldı" derken bunun 20'sinin aboneliğe ayrıldığını bilmek zorunda.
    line = mn.stock_line(STOCK["day"])
    assert line["sold"] == 86
    assert line["sold_orders"] == 66
    assert line["sold_subscriptions"] == 20
    assert line["sold_orders"] + line["sold_subscriptions"] == line["sold"]


def test_sold_out_ile_full_ayri_alanlardir() -> None:
    # `sold_out` MUTFAĞIN elle koyduğu işaret, `full` tavanın dolması. Bir ürün
    # tavanı dolmadan da tükenmiş olabilir (malzeme bitti).
    line = mn.stock_line({"capacity": 100, "sold": 10, "sold_out": True})
    assert line["sold_out"] is True
    assert line["full"] is False


def test_stok_gorunumu_blocking_listesini_tasir() -> None:
    view = mn.stock_view(STOCK)
    assert view["day"]["remaining"] == 34
    assert [item["item_id"] for item in view["items"]] == [901, 902]
    assert view["items"][1]["full"] is True
    # `blocking.items` ÜRÜN kimlikleridir (menu_id), kalem kimlikleri değil.
    assert view["blocking"]["items"] == [27]


def test_sunucunun_oversold_bayragi_ezilmez() -> None:
    # `sold` iki bileşenli ve sunucu onu bizden iyi biliyor; kendi hesabımız
    # yalnız alan hiç gelmediğinde devreye girer.
    view = mn.stock_result_view({**STOCK_RESULT, "items": [
        {"item_id": 902, "capacity": 60, "sold": 70, "oversold": False}]})
    assert view["items"][0]["oversold"] is False

    turetilen = mn.stock_result_view({**STOCK_RESULT, "items": [
        {"item_id": 902, "capacity": 60, "sold": 70}]})
    assert turetilen["items"][0]["oversold"] is True


def test_uyari_kodu_turkce_etiket_alir() -> None:
    view = mn.stock_result_view(STOCK_RESULT)
    assert view["warnings"][0]["code"] == "capacity_below_sold"
    assert view["warnings"][0]["label"] == "Tavan satılmışın altında"


# ============================================================== tavan tablosu

def test_tavan_tablosu_item_id_ister() -> None:
    rows, problem = mn.stock_rows([{"capacity": 60}])
    assert rows == []
    assert "item_id" in problem


def test_tavan_tablosu_eksik_kalemi_yakalar() -> None:
    # TAM LİSTEDİR: gönderilmeyen kalemin tavanı kalkar. Aradan silinmiş bir
    # kalem yüzünden eksik gönderilen tablo, başka bir kalemin tavanını
    # sessizce kaldırırdı.
    rows, problem = mn.stock_rows([{"item_id": 901, "capacity": None}],
                                  known={901, 902})
    assert rows == []
    assert "902" in problem


def test_tavan_tablosu_artik_olmayan_kalemi_yakalar() -> None:
    rows, problem = mn.stock_rows([{"item_id": 901, "capacity": None},
                                   {"item_id": 999, "capacity": 5}], known={901})
    assert rows == []
    assert "999" in problem


def test_tavan_tablosu_ayni_kalemi_iki_kez_kabul_etmez() -> None:
    rows, problem = mn.stock_rows([{"item_id": 901, "capacity": 1},
                                   {"item_id": 901, "capacity": 2}])
    assert rows == []
    assert "iki kez" in problem


def test_temel_cizgi_satislari_saklar_tavanlari_degil() -> None:
    # Yönetici tavanı satılmışa BAKARAK seçiyor; değişen şey satış olduğunda
    # karar yeniden verilmelidir.
    baseline = mn.baseline_of(mn.stock_view(STOCK))
    assert baseline["day_sold"] == 86
    assert baseline["items"]["902"] == 60


def test_temel_cizgi_kaymasi_insan_okur_cumle_uretir() -> None:
    before = {"day_sold": 86, "items": {"901": 86, "902": 60}}
    now = {"day_sold": 90, "items": {"901": 90, "902": 60}}
    drift = mn.baseline_drift(before, now)
    assert any("gün toplamı" in line for line in drift)
    assert any("901" in line for line in drift)
    assert not any("902" in line for line in drift)


def test_kaymayan_temel_cizgi_bos_liste_dondurur() -> None:
    same = {"day_sold": 86, "items": {"901": 86}}
    assert mn.baseline_drift(same, dict(same)) == []


# ================================================================ sıralama

def test_sonraki_sira_onar_onar_artar() -> None:
    # Araya kalem sokmayı bütün listeyi yeniden numaralamadan mümkün kılar;
    # sunucu da aynı kuralı uyguluyor ve iki hesabın ayrışması, ekranda görülen
    # sıranın kaydettikten sonra değişmesi demek olurdu.
    assert mn.next_sort_order([]) == 10
    assert mn.next_sort_order(mn.day_view(DAY)["items"]) == 30
