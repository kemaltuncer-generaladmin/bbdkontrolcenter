"""Taslağın SAF kuralları — ağ yok, servis yok.

Buradaki iddiaların hepsi `BLD/docs/control/orders.md` ve `settings.md`'den
okundu. Modülün kendi uydurduğu bir kurala karşı geçen test hiçbir şey
kanıtlamaz.
"""

from __future__ import annotations

from bld_manual_order_backend import draft as dr
from bld_manual_order_fakes import SERVER_TIME, SERVER_TIME_LATE, TODAY

# --------------------------------------------------------------------- telefon

def test_telefon_ulusal_bicime_iner_ve_ayni_numara_ayni_sonucu_verir() -> None:
    # Sözleşme: "0532 123 45 67", "+90 532 123 45 67" ve "5321234567" AYNI
    # numaradır. Yer tutucu e-posta bundan türüyor; ayrışsaydı aynı müşteri
    # ikinci kez açılırdı.
    for yazim in ("0532 123 45 67", "+90 532 123 45 67", "5321234567",
                  "90 532 123 45 67", "(0532) 123-45-67"):
        assert dr.national_phone(yazim) == "5321234567", yazim


def test_kirpma_uzunluga_bagli_koru_korune_degil() -> None:
    # Baştan iki karakter kesen bir kural, on haneli geçerli bir numaranın ilk
    # hanesini yerdi.
    assert dr.national_phone("9012345678") == "9012345678"   # 10 hane, dokunma
    assert dr.national_phone("905321234567") == "5321234567"  # 12 hane, `90` git
    assert dr.national_phone("05321234567") == "5321234567"   # 11 hane, `0` git
    assert dr.national_phone("") == ""


# --------------------------------------------------------------------- müşteri

def test_musteri_ya_kayitli_ya_yeni_olur_ikisi_birden_reddedilir() -> None:
    # Sunucu `customer_id` doluysa `customer` nesnesini SESSİZCE yok sayar:
    # personel "yeni müşteri açtım" sanır, sipariş BAŞKA hesaba yazılır ve
    # telefon kapandıktan sonra fark edilir.
    assert dr.customer_error(customer_id=312, name="", phone="") == ""
    assert dr.customer_error(customer_id=0, name="Acme", phone="5321234567") == ""

    ikisi = dr.customer_error(customer_id=312, name="Acme", phone="5321234567")
    assert "birini bırakın" in ikisi

    hicbiri = dr.customer_error(customer_id=0, name="", phone="")
    assert "Müşteri seçilmedi" in hicbiri


def test_yeni_musteri_telefonu_en_az_bir_rakam_ister() -> None:
    # Rakamsız bir giriş `tel-@bld.invalid` üretirdi ve o adres rakamsız
    # girilen İKİNCİ müşteriyle çakışır, iki ayrı arayan tek kayda düşerdi.
    assert dr.customer_error(customer_id=0, name="Acme", phone="bilinmiyor")
    assert dr.customer_error(customer_id=0, name="Acme", phone="+90 (532) 1") == ""
    assert dr.customer_error(customer_id=0, name="A", phone="5321234567")


# ----------------------------------------------------------------------- adres

def test_adres_yalniz_teslimatta_zorunlu() -> None:
    assert dr.address_error("pickup", None) == ""
    assert dr.address_error("delivery", None)
    assert dr.address_error("delivery", {"line1": "Örnek Mah.", "district": "Selçuklu"})
    assert dr.address_error("delivery", {"line1": "Örnek Mah.", "district": "Selçuklu",
                                         "city": "Konya"}) == ""


# --------------------------------------------------------------------- kalemler

def test_kalemler_oldugu_gibi_gecer_secenek_kimligi_dusmez() -> None:
    # `option_value_ids` düşseydi "ekstra peynir" silinir, sipariş ucuzlar,
    # mutfak yanlış yemeği yapardı — ve hata hiçbir yerde görünmezdi.
    kalemler, hata = dr.clean_items([
        {"menu_id": 88, "quantity": 2, "option_value_ids": [4, 9], "note": "acısız"},
    ])
    assert hata == ""
    assert kalemler[0]["option_value_ids"] == [4, 9]
    assert kalemler[0]["note"] == "acısız"


def test_ayni_urun_iki_satirda_birlestirilmez() -> None:
    # Notları farklı iki satır ("biri acısız") tek satıra indirgenirse mutfak
    # ikisini de aynı yapar.
    kalemler, hata = dr.clean_items([
        {"menu_id": 88, "quantity": 2, "note": "acısız"},
        {"menu_id": 88, "quantity": 3},
    ])
    assert hata == ""
    assert len(kalemler) == 2


def test_bos_liste_ve_gecersiz_adet_reddedilir() -> None:
    assert dr.clean_items([])[1]
    assert dr.clean_items([{"menu_id": 88, "quantity": 0}])[1]
    assert dr.clean_items([{"menu_id": 88, "quantity": 1000}])[1]
    assert dr.clean_items([{"menu_id": 0, "quantity": 2}])[1]
    assert dr.clean_items([{"menu_id": 88, "quantity": 999}])[1] == ""


# ------------------------------------------------------------------ kesim saati

def test_kesim_gecmisse_bugun_kapali_ama_engel_degil() -> None:
    # Sunucu pencereyi `adminContext: true` ile bilerek atlıyor; ekranın işi
    # SÖYLEMEK. `cutoff_state` bir "hayır" değil, bir cümle üretir.
    acik = dr.cutoff_state(TODAY, cutoff="08:00", server_time=SERVER_TIME)
    assert acik["closed"] is False
    assert acik["today"] == TODAY

    kapali = dr.cutoff_state(TODAY, cutoff="08:00", server_time=SERVER_TIME_LATE)
    assert kapali["closed"] is True
    assert "kesim saatinde kapandı" in kapali["label"]
    assert "panelden giriyorsunuz" in kapali["label"]


def test_ileri_tarih_kapali_degil_gecmis_gun_kapali() -> None:
    ileri = dr.cutoff_state("2026-08-20", cutoff="08:00", server_time=SERVER_TIME_LATE)
    assert ileri["closed"] is False

    gecmis = dr.cutoff_state("2026-08-10", cutoff="08:00", server_time=SERVER_TIME)
    assert gecmis["closed"] is True
    assert "GEÇMİŞTE" in gecmis["label"]


def test_kesim_tanimsizsa_kapanis_da_yok() -> None:
    # `settings.md` tablosu: kesim saati `null` ise sipariş anında görünür ve
    # kapanış diye bir şey yoktur. Boş saati "00:00" sanmak, her günü kapalı
    # göstermek olurdu.
    durum = dr.cutoff_state(TODAY, cutoff=None, server_time=SERVER_TIME_LATE)
    assert durum["closed"] is False
    assert durum["cutoff"] == ""

    bozuk = dr.cutoff_state(TODAY, cutoff="yirmi bir", server_time=SERVER_TIME_LATE)
    assert bozuk["closed"] is False


# ------------------------------------------------------------------------ stok

def test_stok_haritasi_menu_id_ile_kurulur() -> None:
    # Sipariş kalemi ÜRÜNÜ (`menu_id`) taşır, günün menü satırını (`item_id`)
    # değil. Yanlış anahtarla kurulmuş bir harita hiçbir kalemi bulamaz ve
    # ekran sessizce "tavan aşımı yok" derdi.
    from bld_manual_order_fakes import MENU_STOCK

    index = dr.stock_index(MENU_STOCK)
    assert set(index["items"]) == {"88", "27"}
    assert index["items"]["27"]["remaining"] == 2
    assert index["day"]["remaining"] == 34


def test_tavan_asimi_uyari_uretir_ama_engel_degildir() -> None:
    from bld_manual_order_fakes import MENU_STOCK

    index = dr.stock_index(MENU_STOCK)
    uyarilar = dr.stock_warnings(index, [{"menu_id": 27, "quantity": 5}])
    assert len(uyarilar) == 1
    assert "tavan aşılıyor" in uyarilar[0]
    assert "kalan 2" in uyarilar[0] and "istenen 5" in uyarilar[0]
    # Uyarı bir "hayır" değil: `allowOvershoot: true` ile sipariş yine açılır.
    assert "yine de açılır" in uyarilar[0]


def test_tavansiz_kalem_uyari_uretmez() -> None:
    # `capacity` `null` ise `remaining` de `null` gelir. Sıfır sanmak, tavansız
    # her kalemde yanlış alarm olurdu. Adet GÜN tavanının altında seçildi:
    # gün toplamı ayrı bir uyarıdır ve bu testin ölçtüğü şey değil.
    from bld_manual_order_fakes import MENU_STOCK

    index = dr.stock_index(MENU_STOCK)
    assert dr.stock_warnings(index, [{"menu_id": 88, "quantity": 30}]) == []


def test_gun_toplami_asiminda_ayri_uyari_cikar() -> None:
    from bld_manual_order_fakes import MENU_STOCK

    index = dr.stock_index(MENU_STOCK)
    uyarilar = dr.stock_warnings(index, [{"menu_id": 88, "quantity": 40}])
    assert any("Gün toplamı aşılıyor" in satir for satir in uyarilar)


def test_tukendi_isareti_tavandan_ayri_soylenir() -> None:
    # `sold_out` mutfağın ELLE koyduğu işaret (malzeme bitti) ve sunucu o
    # kalemi `422 ITEM_UNAVAILABLE` ile REDDEDER — yani gerçekten engeldir.
    # Tavan aşımıyla aynı cümleye konsaydı personel "uyarıyı geçtim ama olmadı"
    # yaşardı.
    stok = {"day": {"remaining": None},
            "items": {"27": {"menu_id": 27, "name": "Tavuk Sote", "capacity": 60,
                             "remaining": 10, "sold_out": True}}}
    uyarilar = dr.stock_warnings(stok, [{"menu_id": 27, "quantity": 2}])
    assert len(uyarilar) == 1
    assert "TÜKENDİ" in uyarilar[0]
    assert "reddedebilir" in uyarilar[0]


# ---------------------------------------------------------------------- görünüm

def test_odeme_yontemi_account_kabul_etmez() -> None:
    # Cari hesap iş modelinden çıktı; sunucu `422 VALIDATION_FAILED` veriyor.
    assert dr.choice_error("account", dr.PAYMENT_METHODS, field="Ödeme yöntemi")
    assert dr.choice_error("cash", dr.PAYMENT_METHODS, field="Ödeme yöntemi") == ""
    assert dr.PAYMENT_METHODS == ("online", "cash")


def test_gerekce_opsiyonel_ama_ust_sinir_500() -> None:
    assert dr.reason_error("") == ""
    assert dr.reason_error("ok") == ""
    assert dr.reason_error("x" * 500) == ""
    assert dr.reason_error("x" * 501)


# ------------------------------------------------------- anlaşmalı sepet fiyatı

def test_anlasmali_tutar_bos_birakilinca_None_doner_hata_degil() -> None:
    # `None` = "anlaşma yok" ve alanın hiç gönderilmemesiyle EŞDEĞER: sunucu o
    # hâlde tutarı katalogdan hesaplar. Kutuyu hiç açmamış personel hata
    # görmemeli.
    for bos in (None, "", "   "):
        assert dr.clean_agreed_total(bos) == (None, "")


def test_anlasmali_tutar_tam_sayi_kurus_olarak_okunur() -> None:
    # 400,00 ₺ → 40000 kuruş. Bölme yok, dönüşüm yok: tel de ekran da kuruş
    # taşıyor.
    assert dr.clean_agreed_total(40000) == (40000, "")
    assert dr.clean_agreed_total("40000") == (40000, "")
    assert dr.clean_agreed_total(40000.0) == (40000, "")


def test_kurusun_altindaki_deger_KIRPILMAZ_reddedilir() -> None:
    # `int(40000.5)` sessizce 40000 üretir; personelin yazdığı sayıdan BAŞKA
    # bir tutarın siparişe düşmesi, bu alanın önlemek için var olduğu şeyin ta
    # kendisi. Kuruşun altında birim yok — cevap kırpmak değil reddetmek.
    kurus, hata = dr.clean_agreed_total(40000.5)
    assert kurus is None
    assert "TAM SAYI" in hata

    # `True` Python'da bir `int`tir ve elenmeseydi 1 kuruşluk sipariş geçerdi.
    assert dr.clean_agreed_total(True)[0] is None
    assert dr.clean_agreed_total("400,00")[0] is None
    assert dr.clean_agreed_total("dört yüz")[0] is None


def test_sifir_reddedilir_ve_mesaj_ne_yapilacagini_soyler() -> None:
    # "Bedava sipariş" bir fiyat kararı değil, boş bırakılmış bir kutunun
    # sessizce sıfıra düşmesidir. "En az 1 kuruş" demek personele ne
    # yapacağını söylemezdi.
    kurus, hata = dr.clean_agreed_total(0)
    assert kurus is None
    assert "BOŞ bırakın" in hata

    assert dr.clean_agreed_total(-1)[0] is None


def test_tavan_fazladan_sifira_karsi_akil_siniri() -> None:
    assert dr.clean_agreed_total(dr.MAX_AGREED_TOTAL_KURUS)[0] == dr.MAX_AGREED_TOTAL_KURUS

    kurus, hata = dr.clean_agreed_total(dr.MAX_AGREED_TOTAL_KURUS + 1)
    assert kurus is None
    # Binlik ayracı elle konuyor: `:n` yerel ayara bağlıdır ve `LC_ALL=C`
    # altında "1000000" basardı.
    assert "1.000.000 ₺" in hata


def test_sozlesme_anlasmali_tutar_sinirlarini_ekrana_verir() -> None:
    # Ekrana gömülü bir sayı, sunucu kuralı değiştiğinde sessizce yalan söyler.
    sinirlar = dr.screen_contract()["agreed_total"]
    assert sinirlar == {"min": dr.MIN_AGREED_TOTAL_KURUS,
                        "max": dr.MAX_AGREED_TOTAL_KURUS}
