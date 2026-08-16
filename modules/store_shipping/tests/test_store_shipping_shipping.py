"""Kargo dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_shipping_backend import shipping

# ================================================================== para


def test_ondalik_para_kurusa_cevrilirken_bir_kurus_kaybolmaz() -> None:
    # `float("34.35") * 100` bazı değerlerde 3434.999… verir ve int() bir kuruş
    # aşağı yuvarlar. Decimal ile yuvarlama HALF_UP yapılır.
    assert shipping.to_kurus("34.35") == 3435
    assert shipping.to_kurus("8.615") == 862
    assert shipping.to_kurus("1.250,00") == 125000
    assert shipping.to_kurus("1,250.00") == 125000


def test_bos_ve_bozuk_para_degeri_sifir_degil_none_dondurur() -> None:
    # 0 ile "ücret girilmemiş" farklı şeylerdir: birincisi ücretsiz kargo.
    assert shipping.to_kurus(None) is None
    assert shipping.to_kurus("") is None
    assert shipping.to_kurus("abc") is None
    assert shipping.to_kurus("0") == 0


# ================================================================== desi


def test_desi_hacimsel_agirliktir_bolen_ayardan_gelir() -> None:
    assert shipping.desi(30, 20, 10, divisor=3000) == 2.0
    assert shipping.desi(30, 20, 10, divisor=5000) == 1.2


def test_olcusu_eksik_kutuda_desi_uydurulmaz() -> None:
    # Bir kenarı bilinmiyorsa hacim de bilinmiyordur; 0 döner ve ekran
    # "ölçüleri elle girin" der.
    assert shipping.desi(30, 0, 10) == 0.0
    assert shipping.desi("", "", "") == 0.0


def test_ucret_desi_ile_agirligin_buyugunden_ve_yukari_yuvarlanarak_cikar() -> None:
    # TUZAK 1 ve 2: taşıyıcı ikisinin büyüğünü alır ve tavana yuvarlar.
    assert shipping.billed_units(1.2, 0.5) == 2       # desi büyük
    assert shipping.billed_units(1.2, 4.4) == 5       # ağırlık büyük
    assert shipping.billed_units(3.0, 3.0) == 3       # tam sayıysa büyütülmez
    assert shipping.billed_units(0, 0) == 0           # ölçü yoksa 0, 1 değil


def test_kalemlerden_otomatik_olcu_eksik_urunu_soyler() -> None:
    result = shipping.auto_measures([
        {"sku": "A", "qty_ordered": 2, "weight": 0.5, "width": 20, "height": 10, "length": 15},
        {"sku": "B", "qty_ordered": 1, "weight": 1.0},
    ])
    assert result["weight"] == 2.0
    assert result["complete"] is False
    assert result["missing"] == ["B"]


# =========================================================== ücretlendirme


TIERS = [
    {"min": 0, "max": 2, "price": 4500},
    {"min": 2, "max": 5, "price": 6500},
    {"min": 5, "max": 0, "price": 9500},        # açık uçlu son kademe
]


def test_desi_kademesi_bulunur_acik_uclu_son_kademe_calisir() -> None:
    assert shipping.rate_for(1, TIERS)["price"] == 4500
    assert shipping.rate_for(4, TIERS)["price"] == 6500
    assert shipping.rate_for(40, TIERS)["price"] == 9500


def test_kademesi_olmayan_desi_sessizce_sifir_ucret_uretmez() -> None:
    verdict = shipping.rate_for(3, [{"min": 0, "max": 2, "price": 4500}])
    assert verdict["found"] is False
    assert verdict["price"] == 0


def test_kademe_bosluğu_ve_ortusmesi_kullaniciya_soylenir() -> None:
    # TUZAK 3: boşluk vitrinde "ücret hesaplanamadı", örtüşme keyfî ücret demek.
    bosluk = shipping.tier_problems([{"min": 0, "max": 2, "price": 100},
                                     {"min": 4, "max": 6, "price": 200}])
    assert any("açıkta" in line for line in bosluk)

    ortusme = shipping.tier_problems([{"min": 0, "max": 4, "price": 100},
                                      {"min": 2, "max": 6, "price": 200}])
    assert any("örtüşüyor" in line for line in ortusme)

    assert shipping.tier_problems(TIERS) == []


def test_ortadaki_kademe_acik_uclu_olamaz() -> None:
    problems = shipping.tier_problems([{"min": 0, "max": 0, "price": 100},
                                       {"min": 5, "max": 9, "price": 200}])
    assert any("açık uçlu ama sonuncu değil" in line for line in problems)


def test_ucretsiz_kargo_esigi_yalniz_gonderici_odemelide_calisir() -> None:
    # Alıcı ödemeli gönderide tutarı taşıyıcı alıcıdan tahsil eder; mağazanın
    # eşiği oraya karışmaz.
    gonderici = shipping.quote(units=1, tiers=TIERS, basket_total=50000,
                               free_threshold=30000, payer="sender")
    assert gonderici["free"] is True
    assert gonderici["total"] == 0

    alici = shipping.quote(units=1, tiers=TIERS, basket_total=50000,
                           free_threshold=30000, payer="receiver")
    assert alici["free"] is False
    assert alici["total"] == 4500


def test_ucret_dokumu_her_kalemi_ayri_gosterir() -> None:
    result = shipping.quote(units=3, tiers=TIERS, zone_surcharge=1500, cod=True, cod_fee=800)
    labels = [line["label"] for line in result["lines"]]
    assert "Bölgesel ek ücret" in labels
    assert "Kapıda ödeme hizmet bedeli" in labels
    assert result["total"] == 6500 + 1500 + 800


# ================================================================== bölge


ZONES = [
    {"city": "İstanbul", "district": "", "zone": "Merkez", "surcharge": 0, "delivers": 1},
    {"city": "İstanbul", "district": "Adalar", "zone": "Ada", "surcharge": 2500, "delivers": 1},
    {"city": "Hakkâri", "district": "", "zone": "Uzak", "surcharge": 4000, "delivers": 0},
]


def test_en_ozel_bolge_kazanir_ilce_ili_ezer() -> None:
    # TUZAK 9: Adalar'a giden gönderi il satırının 0 ek ücretiyle çıkmamalı.
    ada = shipping.zone_for("İstanbul", "Adalar", ZONES)
    assert ada["surcharge"] == 2500
    assert ada["scope"] == "district"

    kadikoy = shipping.zone_for("İstanbul", "Kadıköy", ZONES)
    assert kadikoy["surcharge"] == 0
    assert kadikoy["scope"] == "city"


def test_bolge_eslesmesi_aksansiz_ve_buyuk_kucuk_harften_bagimsizdir() -> None:
    assert shipping.zone_for("hakkari", "", ZONES)["zone"] == "Uzak"
    assert shipping.zone_for("HAKKÂRİ", "", ZONES)["delivers"] is False


def test_tanimsiz_bolgeye_ceza_yazilmaz() -> None:
    verdict = shipping.zone_for("Sinop", "", ZONES)
    assert verdict["found"] is False
    assert verdict["surcharge"] == 0
    assert verdict["delivers"] is True


# ================================================================== durum


def test_tasiyicinin_durum_yazimi_bizim_sozluge_cekilir() -> None:
    assert shipping.status_of("IN_TRANSIT") == "in_transit"
    assert shipping.status_of("Picked-Up") == "picked_up"
    assert shipping.status_of("canceled") == "cancelled"


def test_bilinmeyen_durum_yolda_varsayilmaz() -> None:
    # TUZAK 5: teslim edilememiş bir gönderiyi "yolda" göstermek, aramayı
    # geciktirir. Bilinmeyen durum ham kalır ve ekran öyle işaretler.
    code = shipping.status_of("kargo_deposunda_bekliyor")
    assert code == "kargo_deposunda_bekliyor"
    assert shipping.status_label(code) == "kargo_deposunda_bekliyor"


def test_teslim_edilmis_gonderi_geciken_sayilmaz() -> None:
    # TUZAK 6: teslim edilen gönderi 40 gündür hareketsizdir ve bu normaldir.
    assert shipping.idle_days("2026-07-01", status="delivered", today="2026-08-13") is None
    assert shipping.idle_days("2026-08-10", status="in_transit", today="2026-08-13") == 3


def test_okunamayan_tarih_sifir_gun_degil_none_dondurur() -> None:
    assert shipping.days_between("", "2026-08-13") is None
    assert shipping.days_between("bozuk", "2026-08-13") is None
    assert shipping.days_between("2026-08-13", "2026-08-13") == 0


# ================================================================ satırlar


HAM = {
    "id": 5, "order_id": 91, "order_number": "S-91", "tracking_number": "1234567890",
    "customer_name": "Ayşe Yılmaz", "carrier": "yurtici", "status": "in_transit",
    "desi": 1.2, "weight": 0.4, "price": "45.00", "payer": "sender",
    "created_at": "2026-08-01T10:00:00", "last_movement_at": "2026-08-02T09:00:00",
    "address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
}


def test_gonderi_satiri_para_kurus_desi_yuvarlanmis_gelir() -> None:
    row = shipping.shipment_row(HAM, today="2026-08-13", idle_limit=3, zones=ZONES)
    assert row["fee"] == 4500
    assert row["units"] == 2                 # 1,2 desi → 2 desi faturalanır
    assert row["carrierLabel"] == "Yurtiçi Kargo"
    assert row["statusLabel"] == "Yolda"
    assert row["zone"] == "Ada"
    assert row["ageDays"] == 12


def test_uzun_suredir_hareketsiz_gonderi_geciken_isaretlenir() -> None:
    row = shipping.shipment_row(HAM, today="2026-08-13", idle_limit=3, zones=ZONES)
    assert row["idleDays"] == 11
    assert "late" in row["flags"]


def test_teslim_edilmis_ama_tahsil_edilmemis_kapida_odeme_isaretlenir() -> None:
    raw = {**HAM, "status": "delivered", "payer": "receiver", "cod_amount": "250.00",
           "cod_collected": False, "delivered_at": "2026-08-05T12:00:00"}
    row = shipping.shipment_row(raw, today="2026-08-13", idle_limit=3)
    assert row["cod"] == 25000
    assert "cod" in row["flags"]
    assert "late" not in row["flags"]        # teslim edilen geciken sayılmaz
    assert row["deliveryDays"] == 4


def test_hareket_gecmisi_en_yeni_ustte_siralanir() -> None:
    rows = shipping.movement_rows([
        {"at": "2026-08-01T10:00:00", "status": "created"},
        {"at": "2026-08-03T08:00:00", "status": "in_transit", "location": "Kadıköy Şube"},
    ])
    assert rows[0]["status"] == "in_transit"
    assert rows[0]["statusLabel"] == "Yolda"
    assert rows[1]["status"] == "created"


# =========================================================== kargoya hazır


def test_kismi_kargolanmis_siparisin_kalani_hala_kargoya_hazirdir() -> None:
    # TUZAK 8: "gönderisi yok" demek `shipped == 0` demek DEĞİLDİR.
    state = shipping.ready_state({"status": "processing", "total_qty_ordered": 5,
                                  "total_qty_invoiced": 5, "total_qty_shipped": 2})
    assert state["ready"] is True
    assert state["pending"] == 3


def test_odemesi_alinmamis_siparis_kargoya_hazir_degildir() -> None:
    state = shipping.ready_state({"status": "pending", "total_qty_ordered": 2,
                                  "total_qty_invoiced": 0, "total_qty_shipped": 0})
    assert state["ready"] is False
    assert "Ödeme" in state["blocked"]


def test_iptal_edilmis_siparis_kargoya_hazir_listesine_girmez() -> None:
    state = shipping.ready_state({"status": "canceled", "total_qty_ordered": 2,
                                  "total_qty_invoiced": 2, "total_qty_shipped": 0})
    assert state["ready"] is False


# ================================================================ taşıyıcı


def test_kimlik_bilgisi_maskelenir_son_dort_hane_kalir() -> None:
    assert shipping.mask("1234567890123") == "•••••••••0123"
    assert shipping.mask("") == ""
    assert shipping.mask("abc") == "•••"


def test_tasiyici_satiri_kimlikleri_maskeli_matrisi_denetlenmis_verir() -> None:
    row = shipping.carrier_row({
        "code": "aras", "active": 1, "api_key": "SUPERSECRETKEY99",
        "tiers": [{"min": 0, "max": 2, "price": "45.00"}],
        "tested_at": "2026-08-10T09:00:00", "tested_ok": True,
    }, today="2026-08-13")
    assert row["label"] == "Aras Kargo"
    assert row["apiKey"].endswith("EY99")
    assert "SUPERSECRET" not in row["apiKey"]
    assert row["tiers"][0]["price"] == 4500
    assert row["testAgeDays"] == 3


# ================================================================ sihirbaz


def test_sihirbaz_govdesi_faturalanacak_desiyi_de_tasir() -> None:
    body = shipping.wizard_body(order_id=91, carrier="MNG", packages=2, desi_value=1.2,
                                weight=0.4, payer="alici", cod=25000, note="kırılacak")
    assert body["carrier"] == "mng"
    assert body["billedDesi"] == 2
    assert body["payer"] == "receiver"
    assert body["codAmount"] == "250.00"


def test_alici_odemeli_ama_tutarsiz_secim_uyarilir() -> None:
    problems = shipping.wizard_problems(carrier="aras", desi_value=1, weight=0, packages=1,
                                        payer="receiver", cod=0)
    assert any("tahsil edilecek tutar" in line for line in problems)

    ters = shipping.wizard_problems(carrier="aras", desi_value=1, weight=0, packages=1,
                                    payer="sender", cod=5000)
    assert any("tahsil edilmez" in line for line in ters)


def test_teslimat_yapilmayan_bolge_sihirbazda_soylenir() -> None:
    problems = shipping.wizard_problems(carrier="aras", desi_value=1, weight=0, packages=1,
                                        payer="sender", cod=0, delivers=False)
    assert any("teslimat yapılmıyor" in line for line in problems)


def test_para_harcayan_islemde_gerekce_daha_uzun_istenir() -> None:
    assert shipping.reason_error("On karakter", shipping.MIN_REASON) == ""
    assert shipping.reason_error("On karakter", shipping.MIN_PURCHASE_REASON) != ""
    assert shipping.reason_error("Müşteri adresi düzeltildi, etiket yenilendi",
                                 shipping.MIN_PURCHASE_REASON) == ""


# ============================================ bayrak okuması: üç biçim, tek kural

def test_JSON_BOOLEAN_bayragi_taniyor_tasiyicilar_pasif_gorunmuyor() -> None:
    # CANLIDA ÇIKAN ARIZA (2026-08-16): mağaza `active` alanını JSON boolean
    # gönderiyor. Eski okuma `bool(as_int(...))` idi; `as_int(True)` →
    # `int("True")` → ValueError → varsayılan 0 → ÜÇ TAŞIYICI DA "Pasif".
    # Sihirbaz önerecek firma bulamıyor, "Taşıyıcı listesi okunamadı" diyor ve
    # KARGO EKRANI GÖNDERİ YAPAMIYORDU.
    canli = [
        {"code": "hepsijet", "title": "Hepsijet", "active": True},
        {"code": "surat", "title": "Sürat Kargo", "active": True},
        {"code": "yurtici", "title": "Yurtiçi Kargo", "active": False},
    ]
    satirlar = [shipping.carrier_row(row) for row in canli]
    assert [row["active"] for row in satirlar] == [True, True, False]
    assert len([row for row in satirlar if row["active"]]) == 2


def test_bayrak_uc_bicimde_de_ayni_okunur() -> None:
    # Bagisto `core_config` satırlarını METİN tutuyor; aynı alan bool, sayı ya
    # da metin olarak gelebiliyor. Üçü de aynı sonucu vermeli.
    for dogru in (True, 1, "1", "true", "TRUE", "yes", "on", "evet"):
        assert shipping.flag(dogru) is True, dogru
    for yanlis in (False, 0, "0", "false", "FALSE", "no", "off", "hayir"):
        assert shipping.flag(yanlis) is False, yanlis


def test_bayrak_yoksa_VARSAYILAN_donuyor_deger_varsa_varsayilan_ezilmiyor() -> None:
    # `delivers` alanının varsayılanı True'dur (bilgi yoksa teslimat var
    # sayılır). Ama açıkça False geldiyse varsayılan EZİLMELİ.
    assert shipping.flag(None, True) is True         # alan yok → varsayılan
    assert shipping.flag("", True) is True           # boş → varsayılan
    assert shipping.flag(False, True) is False       # açık ret → varsayılan EZİLİR
    assert shipping.flag("0", True) is False


def test_teslimat_yapilmayan_bolge_teslimat_var_diye_okunmuyor() -> None:
    # ESKİ ARIZA: `bool(as_int(False, 1))` → `True`. "Teslimat yapılmıyor"
    # işaretli bir bölge "teslimat var" diye okunuyordu; paket yola çıkıyor,
    # taşıyıcı teslim edemiyordu.
    zones = [{"city": "Hakkari", "district": "", "zone": "Uzak",
              "surcharge": 0, "delivers": False, "note": ""}]
    assert shipping.zone_for("Hakkari", "", zones)["delivers"] is False


def test_etiket_hazir_bayragi_sifir_metniyle_kandirilmaz() -> None:
    # `bool("0")` → True. Olmayan etiket "hazır" görünürse kullanıcı basmayı
    # bekler ve kâğıt hiç çıkmaz.
    satir = shipping.shipment_row({"status": "created", "has_label": "0"})
    assert satir["labelReady"] is False
