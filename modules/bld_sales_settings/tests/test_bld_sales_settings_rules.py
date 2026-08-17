"""Satış ayarlarının saf kuralları. Ağa çıkmaz, sahte gerekmez.

Her iddia sözleşmedeki bir cümleye karşılık gelir; uydurma sınır yok.
"""

from __future__ import annotations

from bld_sales_settings_backend import settings as rules

# ================================================================== sözleşme


def test_yazilabilir_alanlar_sunucunun_listesiyle_birebir_ortusur() -> None:
    """`SettingsRepository::controlWritableFields()` ile BİREBİR aynı olmalı.

    Bu liste iki yerde birden yaşıyor ve ayrıştığında arıza SESSİZ oluyor:
    17.08.2026'da panel `subscription_release_time`'ı `required` çizmeyi
    sürdürdü, alan sunucudan hiç gelmedi ve form doğrulamadan geçmediği için
    ekran YALNIZ O ALANI DEĞİL, HİÇBİR AYARI kaydedemez oldu. Sayıyı ve sırayı
    burada sabitlemek, aynı ayrışmanın bir dahaki sefere testte görünmesi
    içindir.
    """
    assert rules.WRITABLE_FIELDS == (
        "order_cutoff",
        "max_lookahead_days",
        "min_order_total_kurus",
        "delivery_fee_kurus",
        "payment_methods",
        "busy",
        "busy_message",
        "prep_minutes",
        "delivery_minutes",
        "busy_extra_minutes",
        "daily_menu_enabled",
        "auto_invoice",
    )
    # Panel etiketi olmayan bir alan fark tablosunda ham anahtar adıyla
    # görünürdü ("busy_extra_minutes: 15 → 30"); yönetici hangi ayara
    # baktığını okumalı.
    for alan in rules.WRITABLE_FIELDS:
        assert alan in rules.FIELD_LABELS


# ================================================================ kesim saati


def test_kesim_saati_bos_birakilabilir_ama_bicimsizi_reddedilir() -> None:
    # `null` GEÇERLİ: "kesim saati yok" demektir ve gün bazlı saatler devreye
    # girer (`gün.cutoff_time ?? ayar.order_cutoff`).
    govde, hatalar = rules.clean_patch({"order_cutoff": None})
    assert hatalar == []
    assert govde == {"order_cutoff": None}

    # Form boş alanı boş DİZE olarak gönderir; ikisi aynı anlama gelir.
    govde, hatalar = rules.clean_patch({"order_cutoff": ""})
    assert govde == {"order_cutoff": None}

    govde, hatalar = rules.clean_patch({"order_cutoff": "8:00"})
    assert hatalar and hatalar[0]["field"] == "order_cutoff"
    govde, hatalar = rules.clean_patch({"order_cutoff": "24:00"})
    assert hatalar and hatalar[0]["field"] == "order_cutoff"


def test_abonelik_serbest_birakma_saati_kaldirildi_ve_yazilamaz() -> None:
    # Ayar 17.08.2026'da sunucudan kaldırıldı: sipariş artık servis gününün
    # kesim anında mutfağa düşüyor. Alan yazılabilirler listesinde OLMAMALI —
    # panel onu `required` çizdiği sürece form doğrulamadan geçmiyor ve ekran
    # HİÇBİR ayarı kaydedemiyordu.
    assert "subscription_release_time" not in rules.WRITABLE_FIELDS

    # Gönderilirse reddedilir ama "tanımlı bir alan değil" DENMEZ: yönetici adı
    # yanlış yazdığını sanardı, oysa doğru yazmış ve ayar kalkmış.
    _, hatalar = rules.clean_patch({"subscription_release_time": "07:00"})
    assert hatalar and hatalar[0]["field"] == "subscription_release_time"
    assert "KALDIRILDI" in hatalar[0]["message"]
    assert "kesim saatinde" in hatalar[0]["message"]

    # Tek hata düşer; "bilinmeyen alan" süzgeci aynı anahtarı ikinci kez
    # yakalamamalı — iki cümle, yöneticiye iki ayrı sorun varmış gibi görünürdü.
    _, hatalar = rules.clean_patch({"subscription_release_time": ""})
    assert len(hatalar) == 1


# ================================================================== ileri gün


def test_ileri_gun_tavani_yedidir_ve_sifir_gecerlidir() -> None:
    # İş kararı 3: en fazla 7 gün ileri. Sunucu sabiti 30 olsa bile bu ekran
    # 7'nin üstünü kabul etmez; etseydi karar sessizce delinirdi.
    govde, hatalar = rules.clean_patch({"max_lookahead_days": 7})
    assert hatalar == [] and govde == {"max_lookahead_days": 7}

    # Sıfır GEÇERLİ: "yalnız bugüne sipariş".
    govde, hatalar = rules.clean_patch({"max_lookahead_days": 0})
    assert hatalar == [] and govde == {"max_lookahead_days": 0}

    _, hatalar = rules.clean_patch({"max_lookahead_days": 8})
    assert hatalar and hatalar[0]["field"] == "max_lookahead_days"
    _, hatalar = rules.clean_patch({"max_lookahead_days": 30})
    assert hatalar and hatalar[0]["field"] == "max_lookahead_days"
    _, hatalar = rules.clean_patch({"max_lookahead_days": -1})
    assert hatalar


# ============================================================ ödeme yöntemleri


def test_cari_hesap_kabul_edilmez() -> None:
    # İş kararı 1: cari hesap Faz 0'da kaldırıldı. Sözleşme `account`
    # gönderilirse 422 veriyor; panel de onu göndermemeli.
    _, hata = rules.clean_payment_methods(["online", "account"])
    assert "account" in hata
    assert "cari hesap" in hata

    liste, hata = rules.clean_payment_methods(["online", "cash"])
    assert hata == "" and liste == ["online", "cash"]


def test_odeme_yontemi_listesi_bos_olamaz_ve_tekrar_tasimaz() -> None:
    # Boş liste, hiçbir ödeme yöntemi olmayan bir satış kanalı demekti.
    _, hata = rules.clean_payment_methods([])
    assert hata
    _, hata = rules.clean_payment_methods(["cash", "cash"])
    assert "tekrar" in hata
    _, hata = rules.clean_payment_methods("cash")
    assert hata


# ==================================================================== dakika


def test_dakika_alanlari_araligin_disinda_reddedilir_sessizce_duzeltilmez() -> None:
    # `LocationGate` eskiden aralık dışını SESSİZCE varsayılana çeviriyordu.
    # Sessizce düzeltilen bir ayar, yöneticinin girdiğini sandığı değerle
    # çalışmadığını hiç öğrenmemesi demektir.
    for alan in rules.MINUTE_FIELDS:
        govde, hatalar = rules.clean_patch({alan: 40})
        assert hatalar == [] and govde == {alan: 40}

        _, hatalar = rules.clean_patch({alan: 0})
        assert hatalar and hatalar[0]["field"] == alan
        _, hatalar = rules.clean_patch({alan: 481})
        assert hatalar and hatalar[0]["field"] == alan
        # Değer varsayılana ÇEVRİLMEZ: gövdeye hiç girmez.
        govde, _ = rules.clean_patch({alan: 481})
        assert alan not in govde


def test_para_alanlari_tam_sayi_kurustur() -> None:
    govde, hatalar = rules.clean_patch({"min_order_total_kurus": 0})
    assert hatalar == [] and govde == {"min_order_total_kurus": 0}
    _, hatalar = rules.clean_patch({"delivery_fee_kurus": -1})
    assert hatalar and hatalar[0]["field"] == "delivery_fee_kurus"


# ============================================================== salt okunurlar


def test_salt_okunur_alanlar_reddedilir() -> None:
    # `is_open` çalışma saatlerinden türer; `daily_package_menu_id` göç yazar
    # ve yanlış bir kimlik günün menüsünü sıfır liraya sattırır.
    for alan in ("is_open", "daily_package_menu_id"):
        _, hatalar = rules.clean_patch({alan: 1})
        assert hatalar and hatalar[0]["field"] == alan


def test_satis_salteri_bu_govdeden_yazilamaz() -> None:
    # Şalterin kendi uçları var. Gerekçesiz ve süresiz çevirmek, tam da
    # durdurmanın en sık hatasını (açmayı unutmak) üretirdi.
    _, hatalar = rules.clean_patch({"ordering_enabled": False})
    assert hatalar and hatalar[0]["field"] == "ordering_enabled"
    assert "kendi uçları" in hatalar[0]["message"]


def test_bilinmeyen_alan_sessizce_dusurulmez() -> None:
    # Sözleşmede olmayan bir anahtar sunucuda da yok sayılır ve yönetici
    # "kaydettim" der, hiçbir şey değişmez. Yanlış yazılmış bir alan adının
    # tek görünür olduğu yer burasıdır.
    _, hatalar = rules.clean_patch({"order_cutof": "08:00"})
    assert hatalar and hatalar[0]["field"] == "order_cutof"


def test_bos_govde_hata_verir() -> None:
    _, hatalar = rules.clean_patch({})
    assert hatalar and "Değişen alan yok" in hatalar[0]["message"]


# ================================================================ fark ve yarış


def test_odeme_yontemi_sirasi_degisiklik_sayilmaz() -> None:
    # `["cash","online"]` ile `["online","cash"]` aynı ayardır; birini
    # öbürüne "değişiklik" saymak, denetim izine hiç olmamış bir olay yazardı.
    once = {"payment_methods": ["online", "cash"]}
    sonra = {"payment_methods": ["cash", "online"]}
    assert rules.diff(once, sonra, ("payment_methods",)) == []

    sonra = {"payment_methods": ["cash"]}
    fark = rules.diff(once, sonra, ("payment_methods",))
    assert len(fark) == 1 and fark[0]["field"] == "payment_methods"


def test_yarisi_yalniz_yazilan_alanlar_icin_arar() -> None:
    # Mutfak `busy` anahtarını değiştirmiş olabilir. Yönetici yalnız kesim
    # saatini yazıyorsa bu bir çakışma DEĞİLDİR: dokunulmayan alan gövdeye
    # hiç girmiyor ve reddetmek ekranı kullanılamaz yapardı.
    taban = {"busy": False, "order_cutoff": "08:00"}
    taze = {"busy": True, "order_cutoff": "08:00"}
    assert rules.conflicts(taban, taze, ["order_cutoff"]) == []

    carpisma = rules.conflicts(taban, taze, ["busy"])
    assert len(carpisma) == 1
    assert carpisma[0]["field"] == "busy"
    assert carpisma[0]["baseline"] is False
    assert carpisma[0]["current"] is True


# =================================================================== durdurma


def test_durdurma_bitisi_gecmiste_olamaz_ve_otuz_gunu_asamaz() -> None:
    simdi = "2026-08-16T09:00:00Z"
    assert rules.pause_error(None, now=simdi) == ""              # süresiz geçerli
    assert rules.pause_error("", now=simdi) == ""
    assert rules.pause_error("2026-08-16T15:00:00Z", now=simdi) == ""

    assert "geçmişte" in rules.pause_error("2026-08-16T08:00:00Z", now=simdi)
    assert "30 gün" in rules.pause_error("2026-10-01T09:00:00Z", now=simdi)
    assert "ISO 8601" in rules.pause_error("yarın", now=simdi)


def test_musteri_mesaji_ve_kapali_gun_aciklamasi_sinirlanir() -> None:
    assert rules.customer_message_error("x" * 300) == ""
    assert rules.customer_message_error("x" * 301)
    assert rules.closed_day_error("2026-08-30", "x" * 160) == ""
    assert rules.closed_day_error("2026-08-30", "x" * 161)
    assert rules.closed_day_error("30.08.2026", None)


def test_gerekce_kapisi() -> None:
    assert rules.reason_error("kısa")
    assert rules.reason_error("x" * 501)
    assert rules.reason_error("Kesim saati sabaha çekildi") == ""


# ======================================================================= stok


def test_stok_satirinda_kalem_kimligi_zorunludur() -> None:
    # Liste TAM LİSTEDİR: eksik satır, o kalemin tavanını sessizce kaldırırdı.
    _, hata = rules.clean_stock(120, [{"capacity": 60}])
    assert "item_id" in hata

    _, hata = rules.clean_stock(120, [{"item_id": 901, "capacity": None},
                                      {"item_id": 901, "capacity": 5}])
    assert "iki kez" in hata


def test_bos_tavan_sinirsiz_demektir_sifirdan_farklidir() -> None:
    govde, hata = rules.clean_stock("", [{"item_id": 901, "capacity": ""},
                                         {"item_id": 902, "capacity": 0}])
    assert hata == ""
    assert govde["capacity_total"] is None
    assert govde["items"][0]["capacity"] is None
    assert govde["items"][1]["capacity"] == 0


def test_negatif_tavan_reddedilir() -> None:
    _, hata = rules.clean_stock(-1, [])
    assert hata
    _, hata = rules.clean_stock(None, [{"item_id": 901, "capacity": -5}])
    assert hata


def test_stok_ozeti_abonelik_payini_ayri_tutar() -> None:
    # `sold` REZERVE porsiyondur ve abonelikler onu önceden tutar (iş kararı
    # 5). Yönetici "34 porsiyon kaldı" dediğinde bunun 20'sinin aboneliğe
    # ayrıldığını bilmelidir.
    ozet = rules.stock_summary({
        "date": "2026-08-17",
        "day": {"capacity": 120, "sold": 86, "sold_orders": 66,
                "sold_subscriptions": 20, "remaining": 34, "full": False},
        "items": [{"item_id": 902, "menu_id": 27, "name": "Tavuk Sote", "capacity": 60,
                   "sold": 60, "sold_orders": 46, "sold_subscriptions": 14,
                   "remaining": 0, "full": True, "sold_out": False}],
        "blocking": {"day": False, "items": [27]},
    })
    assert ozet["day"]["sold_subscriptions"] == 20
    assert ozet["items"][0]["full"] is True
    # `sold_out` mutfağın ELLE koyduğu işaret, `full` tavanın dolması. Bir ürün
    # tavanı dolmadan da tükenmiş olabilir; ikisi ayrı alandır.
    assert ozet["items"][0]["sold_out"] is False
    assert ozet["blocking"]["items"] == [27]
