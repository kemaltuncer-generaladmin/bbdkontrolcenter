"""Müşteri verisinin saf dönüşümleri — ağ yok, durum yok.

Her testin adı bir tuzağı söyler; test kırılırsa hangi kuralın bozulduğu
adından okunur.
"""

from __future__ import annotations

from store_customers_backend import analytics

# ============================================================= para ve tarih

def test_para_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    # `float("1234.35") * 100` bazı değerlerde 123434.99999 verir ve int() bir
    # kuruş aşağı yuvarlar; binlerce müşteride toplam görünür biçimde sapar.
    assert analytics.to_kurus("1234.35") == 123435
    assert analytics.to_kurus("1.250,00") == 125000
    assert analytics.to_kurus("1250,50") == 125050
    assert analytics.to_kurus("") is None
    assert analytics.to_kurus(None) is None


def test_tarih_alani_iki_bicimde_de_gunu_verir() -> None:
    assert analytics.day("2026-01-02T10:00:00Z") == "2026-01-02"
    assert analytics.day("2026-01-02 10:00:00") == "2026-01-02"
    assert analytics.day(None) == ""


def test_gun_farki_cozulemeyen_tarihte_none_doner() -> None:
    assert analytics.days_between("2026-01-01", "2026-01-31") == 30
    assert analytics.days_between("", "2026-01-31") is None


# ============================================================ eksik alanlar

def test_siparis_sayisi_okunamayinca_sifir_uydurulmaz() -> None:
    # Sıfır uydurmak, siparişi olan müşteriyi "hiç sipariş vermemiş"
    # segmentine düşürür ve kampanya yanlış kişilere gider.
    assert analytics.orders_of({"id": 1}) is None
    assert analytics.orders_of({"orders_count": 0}) == 0
    assert analytics.orders_of({"order_count": "7"}) == 7


def test_harcama_alan_adi_degisse_de_bulunur() -> None:
    assert analytics.spend_of({"total_spent": "150.00"}) == 15000
    assert analytics.spend_of({"total_base_grand_total": "150,00"}) == 15000
    assert analytics.spend_of({"id": 3}) is None


def test_ortalama_sepet_siparis_yokken_sifira_bolmez() -> None:
    assert analytics.average_basket(30000, 3) == 10000
    assert analytics.average_basket(30000, 0) is None
    assert analytics.average_basket(None, 3) is None


def test_ad_alani_yoksa_ad_soyaddan_kurulur() -> None:
    assert analytics.full_name({"first_name": "Ayşe", "last_name": "Yılmaz"}) == "Ayşe Yılmaz"
    assert analytics.full_name({"name": "Ali Veli"}) == "Ali Veli"
    assert analytics.full_name({}) == "(adsız)"


def test_izin_bayragi_yoksa_hayir_degil_bilinmiyor_doner() -> None:
    # KVKK'da "onay yok" ile "onay bilgisi tutulmuyor" aynı şey değildir.
    assert analytics.flag({"subscribed_to_news_letter": 1}, "newsletter") is True
    assert analytics.flag({"subscribed_to_news_letter": 0}, "newsletter") is False
    assert analytics.flag({}, "newsletter") is None


# ================================================================= segment

LIMITS = analytics.DEFAULT_THRESHOLDS


def test_hic_siparis_vermemis_musteri_ayri_segmenttir() -> None:
    assert analytics.segment_of(orders=0, spend=0, recency_days=None, tenure_days=5,
                                thresholds=LIMITS) == "none"


def test_siparis_toplami_okunamayan_musteri_tahmin_edilmez() -> None:
    assert analytics.segment_of(orders=None, spend=None, recency_days=10, tenure_days=10,
                                thresholds=LIMITS) == "unknown"


def test_cok_alan_ve_taze_musteri_sampiyondur() -> None:
    assert analytics.segment_of(orders=8, spend=400_000, recency_days=10, tenure_days=900,
                                thresholds=LIMITS) == "champion"


def test_sik_alan_ama_harcamasi_dusuk_musteri_sadiktir() -> None:
    assert analytics.segment_of(orders=8, spend=10_000, recency_days=10, tenure_days=900,
                                thresholds=LIMITS) == "loyal"


def test_eskiden_duzenli_alan_sessizlesen_musteri_riskli_sayilir() -> None:
    # 90–180 gün bandı: geri kazanma çabası düzenli alana değer, tek siparişlik
    # denemeye değmez.
    assert analytics.segment_of(orders=5, spend=100_000, recency_days=120, tenure_days=900,
                                thresholds=LIMITS) == "at_risk"
    assert analytics.segment_of(orders=1, spend=5_000, recency_days=120, tenure_days=900,
                                thresholds=LIMITS) == "sleeping"


def test_uykuda_bandinda_sikliginin_onemi_kalmaz() -> None:
    # 180 günü geçince risk artık GERÇEKLEŞMİŞTİR; sadık da olsa uykudadır.
    assert analytics.segment_of(orders=9, spend=900_000, recency_days=200, tenure_days=900,
                                thresholds=LIMITS) == "sleeping"


def test_bir_yildan_uzun_suredir_almayan_musteri_kayiptir() -> None:
    assert analytics.segment_of(orders=4, spend=90_000, recency_days=400, tenure_days=900,
                                thresholds=LIMITS) == "lost"


def test_yeni_kayit_tek_siparis_yeni_segmentidir() -> None:
    assert analytics.segment_of(orders=1, spend=5_000, recency_days=3, tenure_days=4,
                                thresholds=LIMITS) == "new"


def test_esikler_ayardan_ezilebilir() -> None:
    sert = {**LIMITS, "recent_days": 5}
    assert analytics.segment_of(orders=1, spend=5_000, recency_days=10, tenure_days=4,
                                thresholds=sert) == "sleeping"


def test_musteri_satiri_segmenti_ve_kurusu_birlikte_uretir() -> None:
    row = analytics.customer_row({
        "id": 7, "first_name": "Ali", "last_name": "Veli", "email": "ali@ornek.tr",
        "status": 1, "orders_count": 4, "total_spent": "1200.00",
        "last_order_date": "2026-08-01", "created_at": "2024-01-01",
        "customer_group": {"id": 2, "name": "Öğretmen"},
    }, today="2026-08-13")
    assert row["spend"] == 120000
    assert row["avgBasket"] == 30000
    assert row["recencyDays"] == 12
    assert row["segment"] == "loyal"
    assert row["groupName"] == "Öğretmen"


# ==================================================================== KPI

def test_tekrar_eden_orani_alisveris_yapmayanlari_paydaya_koymaz() -> None:
    rows = [
        {"orders": 3, "spend": 30000, "tenureDays": 400},
        {"orders": 1, "spend": 10000, "tenureDays": 400},
        {"orders": 0, "spend": 0, "tenureDays": 5},
        {"orders": None, "spend": None, "tenureDays": 5},
    ]
    kpi = analytics.kpi_of(rows, today="2026-08-13")
    assert kpi["buyers"] == 2
    assert kpi["repeatRate"] == 50.0
    assert kpi["new"] == 2


def test_hicbir_harcama_okunamadiysa_ortalama_ybd_uydurulmaz() -> None:
    kpi = analytics.kpi_of([{"orders": None, "spend": None, "tenureDays": None}],
                           today="2026-08-13")
    assert kpi["avgLifetime"] is None
    assert kpi["repeatRate"] is None


def test_segment_sayaclari_bilinmeyeni_ayri_tutar() -> None:
    counts = analytics.segment_counts([{"segment": "loyal"}, {"segment": "unknown"},
                                       {"segment": "loyal"}])
    assert counts["loyal"] == 2
    assert counts["unknown"] == 1
    assert counts["lost"] == 0


# ================================================== tarama görünümü süzgeci

BASE_ROW = {
    "name": "Ayşe Yılmaz", "email": "ayse@ornek.tr", "phone": "5321234567",
    "city": "İzmir", "segment": "loyal", "groupId": 2, "status": True, "verified": True,
    "newsletter": True, "orders": 4, "spend": 120000, "createdAt": "2025-02-01",
    "lastOrderAt": "2026-08-01",
}


def test_segment_suzgeci_yalnizca_o_segmenti_birakir() -> None:
    assert analytics.match_row(BASE_ROW, segment="loyal") is True
    assert analytics.match_row(BASE_ROW, segment="lost") is False


def test_harcama_okunamayan_musteri_tutar_araligina_girmez() -> None:
    # Bilinmeyeni aralığa sokmak, "500 TL üstü harcayanlar" listesine harcaması
    # bilinmeyen müşterileri karıştırırdı.
    yok = {**BASE_ROW, "spend": None}
    assert analytics.match_row(yok, spend_min=1) is False


def test_arama_ad_eposta_telefon_ve_sehirde_birlikte_arar() -> None:
    assert analytics.match_row(BASE_ROW, q="ornek.tr") is True
    assert analytics.match_row(BASE_ROW, q="izmir") is True
    assert analytics.match_row(BASE_ROW, q="ankara") is False


def test_dogrulanmamis_suzgeci_dogrulanmislari_eler() -> None:
    assert analytics.match_row(BASE_ROW, status="unverified") is False
    assert analytics.match_row({**BASE_ROW, "verified": False}, status="unverified") is True


def test_son_siparis_araligi_hic_siparis_vermemisi_disarida_birakir() -> None:
    assert analytics.match_row({**BASE_ROW, "lastOrderAt": ""},
                               last_order_from="2026-01-01") is False


# ============================================================== sparkline

def test_alti_aylik_seri_bos_ayi_atlamaz_sifir_yazar() -> None:
    # Ayı atlamak eğriyi yalan söyletir: iki nokta arasında altı ay olabilirken
    # çizgi düz görünür.
    seri = analytics.monthly_spend(
        [{"createdAt": "2026-08-02", "total": 10000},
         {"createdAt": "2026-05-11", "total": 5000}],
        today="2026-08-13")
    assert [item["month"] for item in seri] == ["2026-03", "2026-04", "2026-05",
                                                "2026-06", "2026-07", "2026-08"]
    assert [item["total"] for item in seri] == [0, 0, 5000, 0, 0, 10000]


def test_ay_anahtarlari_yil_sinirini_dogru_gecer() -> None:
    assert analytics.month_keys("2026-02-05", 4) == ["2025-11", "2025-12", "2026-01", "2026-02"]


# ========================================================= süzgeç doğrulama

def test_uygulanmayan_suzgec_yakalanir() -> None:
    # Laravel tanımadığı parametreyi sessizce yok sayar; doğrulamasak
    # başkasının siparişini bu müşteriye ait gösterirdik.
    assert analytics.filter_honored([{"customerId": 7}, {"customerId": 7}], "customerId", 7) is True
    assert analytics.filter_honored([{"customerId": 7}, {"customerId": 9}], "customerId", 7) is False
    assert analytics.filter_honored([{"customerId": 0}], "customerId", 7) is None
    assert analytics.filter_honored([], "customerId", 7) is None


# ==================================================================== yorum

def test_puan_suzgeci_gecersiz_degerleri_atar() -> None:
    assert analytics.rating_list("5,4,9,x,0") == [4, 5]
    assert analytics.rating_list([1, 1, 3]) == [1, 3]
    assert analytics.rating_list(None) == []


def test_yorum_satiri_yerel_spam_etiketini_tasir() -> None:
    # Bagisto'da spam durumu YOK; operatörün kararı yerel etikette durur.
    row = analytics.review_row(
        {"id": 4, "rating": 2, "status": "disapproved", "comment": "kötü",
         "product": {"id": 9, "name": "Kalem"}, "customer": {"id": 3, "name": "Ali"}},
        flags={4: {"spam": 1, "reply": ""}})
    assert row["spam"] is True
    assert row["statusLabel"] == "Reddedildi"
    assert row["productName"] == "Kalem"
    assert row["hasReply"] is False


def test_bilinmeyen_yorum_durumu_bekliyor_sayilir() -> None:
    row = analytics.review_row({"id": 1, "status": "garip"})
    assert row["status"] == "pending"


def test_yorum_ozeti_ortalamayi_ve_yanitsizi_sayar() -> None:
    rows = [analytics.review_row({"id": 1, "rating": 5, "status": "approved"}),
            analytics.review_row({"id": 2, "rating": 1, "status": "pending"})]
    summary = analytics.review_summary(rows)
    assert summary["average"] == 3.0
    assert summary["pending"] == 1
    assert summary["unanswered"] == 2
    assert summary["breakdown"][0] == {"label": "5 yıldız", "value": 1}


def test_spam_eylemi_magazada_reddetmeye_karsilik_gelir() -> None:
    assert analytics.REVIEW_ACTIONS["spam"] == "disapproved"
    assert "spam" not in analytics.REVIEW_STATES


# ===================================================================== izin

def test_izin_gorunumu_dort_izni_de_listeler_ve_bilinmeyeni_isaretler() -> None:
    view = analytics.consent_view({"subscribed_to_news_letter": 1})
    states = {item["key"]: item["state"] for item in view}
    assert states["newsletter"] == "on"
    assert states["sms"] == "unknown"
    assert len(view) == 4


# ================================================================= gerekçe

def test_kisa_gerekce_reddedilir() -> None:
    assert analytics.reason_error("ok") != ""
    assert analytics.reason_error("Müşteri talebi üzerine kapatıldı") == ""


# ======================================= CANLI BİÇİM — yanıt camelCase (TUZAK 11)
#
# Aşağıdaki sözlükler bbdstore.com.tr'den SALT OKUMA ile alınmış gerçek
# yanıtların kısaltılmışıdır. Sahte snake_case veriyle yazılmış testler
# geçerken ekran canlıda baştan sona "—" ve "Bilinmiyor" gösteriyordu; bu
# bölüm o hatanın geri gelmesini engeller.

CANLI_MUSTERI_LISTE = {
    "id": 26, "firstName": "Beray", "lastName": "Şaman", "name": "Beray Şaman",
    "email": "ornek@ornek.tr", "phone": "5078554233", "gender": None, "dateOfBirth": None,
    "channelId": 1, "status": 1, "subscribedToNewsLetter": True, "isVerified": 0,
    "isSuspended": 0, "createdAt": "2026-08-02T11:41:49+03:00",
    "updatedAt": "2026-08-02T11:41:49+03:00",
    "group": {"id": 2, "code": "general", "name": "Genel"},
}

CANLI_MUSTERI_DETAY = {
    **CANLI_MUSTERI_LISTE, "id": 12, "totalAddresses": 1, "totalOrders": 13,
    "totalAmountSpent": 35499, "isVerified": 1,
}

CANLI_SIPARIS = {
    "id": 19, "incrementId": "19", "status": "processing", "statusLabel": "Processing",
    "channelId": 1, "isGuest": False, "customerId": 1, "customerName": "Mehmet Berkay Şaman",
    "totalItemCount": 1, "totalQtyOrdered": 1, "grandTotal": 2, "baseGrandTotal": 2,
    "formattedGrandTotal": "₺2,00", "location": "Selçuklu, 42, TR",
    "createdAt": "2026-08-13 18:27:17",
}

CANLI_ADRES = {
    "id": 34, "customerId": None, "addressType": "customer", "firstName": "veysel kemal",
    "lastName": "TUNCER", "companyName": "deneme", "address": "güldiken 1382.sk",
    "city": "MERKEZ", "state": "Kırşehir", "country": "TR", "postcode": "40000",
    "phone": "05337695687", "vatId": None, "defaultAddress": False,
}


def test_canli_musteri_kaydi_camelcase_alanlardan_okunur() -> None:
    row = analytics.customer_row(CANLI_MUSTERI_LISTE, today="2026-08-13")
    assert row["name"] == "Beray Şaman"
    assert row["groupId"] == 2 and row["groupName"] == "Genel"   # gömülü ad `group`
    assert row["createdAt"] == "2026-08-02"                      # `createdAt`
    assert row["newsletter"] is True                             # `subscribedToNewsLetter`
    assert row["verified"] is False                              # `isVerified` = 0
    assert row["tenureDays"] == 11


def test_canli_musteri_detayi_siparis_ve_harcamayi_verir() -> None:
    row = analytics.customer_row(CANLI_MUSTERI_DETAY, today="2026-08-13")
    assert row["orders"] == 13                                   # `totalOrders`
    assert row["spend"] == 3_549_900                             # `totalAmountSpent`, ONDALIK
    assert row["verified"] is True


def test_canli_siparis_satiri_camelcase_alanlardan_okunur() -> None:
    row = analytics.order_row(CANLI_SIPARIS)
    assert row["customerId"] == 1        # süzgecin doğrulanması buna dayanır
    assert row["increment"] == "19"
    assert row["statusLabel"] == "Processing"
    assert row["total"] == 200           # `grandTotal` ONDALIK: 2 TL = 200 kuruş
    assert row["items"] == 1
    assert row["city"] == "Selçuklu"     # `location` = "şehir, il, ülke"


def test_canli_adres_satiri_camelcase_alanlardan_okunur() -> None:
    row = analytics.address_row(CANLI_ADRES)
    assert row["name"] == "veysel kemal TUNCER"
    assert row["title"] == "deneme"                # `companyName`
    assert row["city"] == "MERKEZ" and row["district"] == "Kırşehir"
    assert row["default"] is False                 # `defaultAddress`
    assert row["vatId"] == ""                      # `vatId` null


def test_yorum_satiri_camelcase_alanlardan_da_okunur() -> None:
    row = analytics.review_row({"id": 5, "rating": 4, "status": "pending",
                                "createdAt": "2026-08-01 10:00:00",
                                "verifiedPurchase": 1,
                                "product": {"id": 3, "name": "Deneme"},
                                "customer": {"id": 12, "name": "Kemal"}})
    assert row["customerId"] == 12 and row["productId"] == 3
    assert row["createdAt"].startswith("2026-08-01")
    assert row["verifiedBuyer"] is True


def test_grup_kodu_ayar_icin_okunabilir() -> None:
    # Varsayılan grup ayarı mağazada KODLA tutuluyor (`general`), kimlikle değil.
    assert analytics.group_code_of(CANLI_MUSTERI_LISTE) == "general"


def test_sehir_siparisin_location_alanindan_ayiklanir() -> None:
    assert analytics.location_city("MERKEZ, Kırşehir, TR") == "MERKEZ"
    assert analytics.location_city(None) == ""


# ============================== sipariş toplulaştırması (TUZAK 12)

def test_siparisler_musteriye_gore_toplulastirilir() -> None:
    rows = [analytics.order_row(item) for item in [
        {"id": 1, "customerId": 12, "grandTotal": "10.00", "createdAt": "2026-01-05 09:00:00",
         "location": "MERKEZ, Kırşehir, TR"},
        {"id": 2, "customerId": 12, "grandTotal": "5.50", "createdAt": "2026-03-09 09:00:00",
         "location": "Selçuklu, 42, TR"},
        {"id": 3, "customerId": 1, "grandTotal": "2.00", "createdAt": "2026-02-02 09:00:00"},
    ]]
    stats = analytics.order_stats(rows)
    assert stats[12]["orders"] == 2
    assert stats[12]["spend"] == 1550
    assert stats[12]["lastOrderAt"] == "2026-03-09"
    assert stats[12]["city"] == "Selçuklu"        # EN SON siparişin şehri
    assert stats[1]["orders"] == 1


def test_misafir_siparisi_hicbir_musteriye_yazilmaz() -> None:
    # Canlıda `customerId: null` sipariş var; ciroyu rastgele bir müşteriye
    # yazmak onu yanlışlıkla şampiyon gösterirdi.
    rows = [analytics.order_row({"id": 9, "customerId": None, "grandTotal": "7750.00",
                                 "createdAt": "2026-05-05 09:00:00"})]
    assert analytics.order_stats(rows) == {}


def test_toplulastirma_bos_alanlari_doldurur_magazaninkini_ezmez() -> None:
    row = analytics.customer_row(CANLI_MUSTERI_LISTE, today="2026-08-13")
    stats = {26: {"orders": 4, "spend": 900_000, "lastOrderAt": "2026-08-01",
                  "city": "Konya", "partial": False}}
    analytics.apply_order_stats(row, stats, today="2026-08-13")
    assert row["orders"] == 4 and row["spend"] == 900_000
    assert row["lastOrderAt"] == "2026-08-01" and row["recencyDays"] == 12
    assert row["city"] == "Konya"
    assert row["computed"] is True
    assert row["segment"] != "unknown"

    dolu = analytics.customer_row(CANLI_MUSTERI_DETAY, today="2026-08-13")
    analytics.apply_order_stats(dolu, {12: {"orders": 1, "spend": 100, "lastOrderAt": "",
                                            "city": "", "partial": False}}, today="2026-08-13")
    assert dolu["orders"] == 13 and dolu["spend"] == 3_549_900   # mağazanınki kazandı


def test_siparissiz_musteri_hic_siparis_vermemis_sayilir() -> None:
    # Tarama EKSİKSİZKEN "bu müşteri hiç sipariş vermemiş" bir bilgidir,
    # eksiklik değil; segment "none" olur, "unknown" değil.
    row = analytics.customer_row(CANLI_MUSTERI_LISTE, today="2026-08-13")
    analytics.apply_order_stats(row, {}, today="2026-08-13")
    assert row["orders"] == 0 and row["segment"] == "none"


def test_tutari_okunamayan_siparis_toplama_girmez_ama_isaretlenir() -> None:
    rows = [analytics.order_row({"id": 1, "customerId": 5, "grandTotal": "10.00",
                                 "createdAt": "2026-02-01 09:00:00"}),
            analytics.order_row({"id": 2, "customerId": 5, "grandTotal": None,
                                 "createdAt": "2026-02-02 09:00:00"})]
    stats = analytics.order_stats(rows)
    assert stats[5]["orders"] == 2 and stats[5]["spend"] == 1000
    assert stats[5]["partial"] is True
