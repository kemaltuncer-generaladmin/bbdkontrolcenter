"""Saf yardımcılar — biçim, etiket ve ön denetim.

Buradaki hiçbir test ağa çıkmaz ve depoya dokunmaz; `orders.py` tümüyle yan
etkisiz olduğu için tek tek sınanabilir.
"""

from __future__ import annotations

from bld_orders_backend import orders as od

# =============================================================== gerekçe

def test_gerekce_alt_ve_ust_sinir() -> None:
    assert od.reason_error("kısa")
    assert od.reason_error("x" * 161)
    assert od.reason_error("Müşteri iki porsiyon azalttı") == ""


def test_gerekce_ust_siniri_160_ve_kirpilmaz() -> None:
    # Kırpmak, denetim kaydına yarım cümle yazmak olurdu; sunucu da kırpmıyor.
    assert od.MAX_REASON == 160
    hata = od.reason_error("x" * 200)
    assert "160" in hata


# ================================================================ süzgeç

def test_tanınmayan_durum_kodu_reddedilir() -> None:
    # Sessizce elenseydi süzgeç sunucuya eksik giderdi, sonuç boş dönerdi ve
    # ekran bunu "bu aralıkta sipariş yok" diye gösterirdi.
    kodlar, hata = od.status_filter("yeni,hazrilaniyor")
    assert kodlar == []
    assert "hazrilaniyor" in hata


def test_durum_suzgeci_sozlesme_sirasina_cekilir_ve_tekrar_duser() -> None:
    kodlar, hata = od.status_filter("hazir,yeni,yeni")
    assert hata == ""
    assert kodlar == ["yeni", "hazir"]


def test_durum_suzgeci_liste_de_kabul_eder() -> None:
    kodlar, hata = od.status_filter(["iptal", "yeni"])
    assert (kodlar, hata) == (["yeni", "iptal"], "")


def test_bozuk_tarih_reddedilir_ama_bos_serbesttir() -> None:
    assert od.date_error("", field="Servis günü") == ""
    assert od.date_error("2026-08-16", field="Servis günü") == ""
    assert "YYYY-MM-DD" in od.date_error("2026-8-1", field="Servis günü")


def test_kapali_listeli_suzgec_denetimi() -> None:
    assert od.choice_error("", od.DELIVERY_TYPES, field="Teslimat türü") == ""
    assert od.choice_error("pickup", od.DELIVERY_TYPES, field="Teslimat türü") == ""
    assert od.choice_error("kargo", od.DELIVERY_TYPES, field="Teslimat türü")


# ============================================================ satır biçimi

def test_siparis_satiri_hicbir_alani_ayiklamaz() -> None:
    # Sözleşme additive büyüyor: bilinen alanları seçip gerisini atan bir
    # dönüşüm, sunucuya eklenen her yeni alanı sessizce düşürürdü.
    ham = {"id": 1, "status": "hazir", "yeni_alan": "korunmali",
           "payment_status": "paid", "payment_method": "cash",
           "delivery_type": "pickup"}
    satir = od.order_row(ham)
    assert satir["yeni_alan"] == "korunmali"
    assert satir["status_label"] == "Hazır"
    assert satir["payment_status_label"] == "Ödendi"
    assert satir["delivery_type_label"] == "Gel-al"


def test_abonelikten_gelen_siparis_iki_alandan_da_anlasilir() -> None:
    assert od.order_row({"is_subscription": True})["from_subscription"] is True
    assert od.order_row({"subscription_id": 7})["from_subscription"] is True
    assert od.order_row({"subscription_id": None})["from_subscription"] is False


def test_duzenlenebilirlik_sunucudan_gelir_hesaplanmaz() -> None:
    # `editable` yoksa `None` kalır ve ekran düzenlemeyi AÇMAZ: bilinmeyen bir
    # kilidi açık saymak, teslim edilmiş siparişe revizyon yazdırıp 422 aldırmak
    # olurdu.
    assert od.editable_view({"status": "hazir"})["editable"] is None
    kapali = od.editable_view({"status": "iptal", "editable": False,
                               "not_editable_reason": "cancelled"})
    assert kapali["editable"] is False
    assert "İptal edilmiş" in kapali["not_editable_label"]


def test_revizyon_kaynagi_cihaz_kimliginden_okunur() -> None:
    merkez = od.revision_row({"revision_no": 1, "created_by_device_id": None})
    kasa = od.revision_row({"revision_no": 2, "created_by_device_id": 3})
    assert merkez["origin_label"] == "Kontrol Merkezi"
    assert kasa["origin_label"] == "Mutfak kasası"
    assert kasa["created_by_device_id"] == 3


def test_revizyon_parasi_kurus_kalir() -> None:
    satir = od.revision_row({"refund_kurus": 36000, "extra_charge_kurus": 1500})
    assert satir["refund_kurus"] == 36000
    assert isinstance(satir["refund_kurus"], int)


# ============================================================ kalem denetimi

def test_bos_kalem_listesi_reddedilir() -> None:
    # Gönderilen liste siparişin YENİ hâlidir; boş liste siparişi boşaltırdı ve
    # iptalin kendi ucu var.
    kalemler, hata = od.revision_items([])
    assert kalemler == []
    assert "TAM kalem listesini" in hata


def test_kalem_oldugu_gibi_gecer_secenekler_dusmez() -> None:
    kalemler, hata = od.revision_items(
        [{"menu_id": 88, "quantity": 10, "option_value_ids": [4, 9], "note": "az tuz",
          "ileride_eklenen": 1}])
    assert hata == ""
    assert kalemler[0]["option_value_ids"] == [4, 9]
    assert kalemler[0]["ileride_eklenen"] == 1


def test_kalem_sinirlari() -> None:
    assert od.revision_items([{"quantity": 1}])[1]
    assert od.revision_items([{"menu_id": 1, "quantity": 0}])[1]
    assert od.revision_items([{"menu_id": 1, "quantity": 1000}])[1]
    assert od.revision_items([{"menu_id": 1, "quantity": 1,
                               "option_value_ids": [0]}])[1]
    assert od.revision_items([{"menu_id": 1, "quantity": 1, "note": "x" * 256}])[1]


# ============================================================== geri alma

def test_sunucu_can_undo_gondermezse_pencere_bilinmez() -> None:
    # UYDURULMAZ: hesaplanmış bir geri sayım, dolmuş bir pencereyi açık
    # gösterir ve personel 422 yerken kendini yavaş sanardı.
    gorunum = od.undo_view({"status": "hazir", "updated_at": "2026-08-16T09:00:00Z"},
                           server_time="2026-08-16T09:00:30Z")
    assert gorunum["known"] is False
    assert gorunum["can_undo"] is False
    assert gorunum["seconds_left"] is None
    assert "can_undo" in gorunum["reason"]


def test_geri_sayim_sunucu_saatine_gore_hesaplanir() -> None:
    # Taban `server_time`: istemcinin saati kaymış olabilir (`00-genel.md` §6).
    gorunum = od.undo_view({"can_undo": True, "undo_until": "2026-08-16T09:01:30Z"},
                           server_time="2026-08-16T09:00:30Z")
    assert gorunum["known"] is True
    assert gorunum["can_undo"] is True
    assert gorunum["seconds_left"] == 60


def test_dolmus_pencere_negatif_saniye_gostermez() -> None:
    gorunum = od.undo_view({"can_undo": True, "undo_until": "2026-08-16T09:00:00Z"},
                           server_time="2026-08-16T09:00:30Z")
    assert gorunum["seconds_left"] == 0
    assert gorunum["can_undo"] is False


def test_geri_sayim_pencere_boyunu_asamaz() -> None:
    # Sunucudan gelen uzak bir tarih, 120 saniyelik pencereyi ekranda saatlere
    # çevirmemeli.
    gorunum = od.undo_view({"can_undo": True, "undo_until": "2026-08-16T10:00:00Z"},
                           server_time="2026-08-16T09:00:00Z")
    assert gorunum["seconds_left"] == od.UNDO_WINDOW_SECONDS


# ============================================================== iptal yanıtı

def test_iptal_yaniti_stok_iadesini_her_zaman_tasir() -> None:
    # Ekran bunu göstermezse yönetici "neden birden 12 yer açıldı" diye sorar.
    bos = od.cancel_result({})
    assert bos["stock_released"] == {"day": 0, "items": []}
    assert bos["warnings"] == []

    dolu = od.cancel_result({"data": {"refund_kurus": 216000, "refund_created": True,
                                      "sms_sent": True,
                                      "stock_released": {"day": 12, "items": [
                                          {"menu_id": 88, "quantity": 12}]}},
                             "warnings": ["Ödeme `paid` ama iade üretilmedi."]})
    assert dolu["stock_released"]["day"] == 12
    assert dolu["stock_released"]["items"][0]["menu_id"] == 88
    assert dolu["warnings"]


# ================================================================ sözleşme

def test_ekran_sozlesmesi_yedi_durumu_ve_zinciri_tasir() -> None:
    sozlesme = od.screen_contract()
    assert [item["code"] for item in sozlesme["statuses"]] == list(od.STATUS_CODES)
    # `iptal` ŞERİTTE DEĞİL: iptal bir aşama değil, zincirin dışına çıkıştır.
    assert "iptal" not in sozlesme["chain"]
    assert sozlesme["reason"] == {"min": 10, "max": 160}


def test_gecis_matrisi_burada_yok() -> None:
    # Sunucunun matrisinin ikinci bir kopyası sessizce ayrışır ve ekran,
    # sunucunun kabul edeceği bir geçişi hiç sormadan reddederdi. Bu testin
    # tek işi o kararı kilitlemek.
    isimler = dir(od)
    assert not any("MATRIX" in ad or "STATUS_NEXT" in ad for ad in isimler)
    sozlesme = od.screen_contract()
    assert all("next" not in item for item in sozlesme["statuses"])
