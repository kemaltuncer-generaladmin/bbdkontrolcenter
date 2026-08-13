"""Kural dönüşümleri — saf mantık, ağa çıkmaz.

Her testin adı bir tuzağı söyler; tuzakların listesi `backend/promotions.py`
başındadır.
"""

from __future__ import annotations

from store_promotions_backend import promotions
from store_promotions_fakes import KURAL

BUGUN = "2026-08-13"


# ======================================================= TUZAK 3 — durum

def test_tarihi_gecmis_kural_status_bir_olsa_bile_aktif_gorunmez() -> None:
    raw = {**KURAL, "status": 1, "ends_till": "2026-07-01 00:00:00"}
    assert promotions.rule_status(raw, BUGUN) == "expired"


def test_baslangici_ileri_tarihli_kural_zamanlanmis_sayilir() -> None:
    raw = {**KURAL, "status": 1, "starts_from": "2026-09-01", "ends_till": ""}
    assert promotions.rule_status(raw, BUGUN) == "scheduled"


def test_status_sifir_ise_takvim_ne_derse_desin_duraklatilmistir() -> None:
    raw = {**KURAL, "status": 0}
    assert promotions.rule_status(raw, BUGUN) == "paused"


def test_takvimsiz_acik_kural_aktiftir() -> None:
    raw = {**KURAL, "status": 1, "starts_from": "", "ends_till": ""}
    assert promotions.rule_status(raw, BUGUN) == "active"


# ================================================= TUZAK 5 — yüzde ≠ kuruş

def test_yuzde_indirim_kurusa_cevrilmez() -> None:
    row = promotions.cart_rule_row({**KURAL, "action_type": "by_percent",
                                    "discount_amount": "10.0000"}, today=BUGUN)
    assert row["value"] == 10.0
    assert row["valueLabel"] == "%10"


def test_sabit_indirim_kurusa_cevrilir() -> None:
    row = promotions.cart_rule_row({**KURAL, "action_type": "cart_fixed",
                                    "discount_amount": "25.50"}, today=BUGUN)
    assert row["value"] == 2550
    assert row["valueLabel"] == "25,50 ₺"


def test_para_cevrimi_float_yuvarlamasina_takilmaz() -> None:
    assert promotions.to_kurus("1234.35") == 123435
    assert promotions.to_kurus("1.250,00") == 125000
    assert promotions.to_kurus("") is None


# ======================================== TUZAK 2 — bilinmeyen koşul silinmez

def test_tanimadigimiz_kosul_kayipsiz_geri_yazilir() -> None:
    raw = {**KURAL, "conditions": [
        {"attribute": "cart|base_sub_total", "attribute_type": "price",
         "operator": ">=", "value": "500.0000"},
        {"attribute": "cart|postcode", "attribute_type": "text",
         "operator": "==", "value": "34000"},
    ]}
    known, unknown = promotions.decode_conditions(raw["conditions"])
    assert [row["kind"] for row in known] == ["subtotal"]
    assert len(unknown) == 1

    body = promotions.write_rule_body(raw, {"name": "Yeni ad"})
    attributes = [item["attribute"] for item in body["conditions"]]
    assert "cart|postcode" in attributes, "mağazanın kendi koşulu yok edilmemeli"
    assert "cart|base_sub_total" in attributes


def test_kosul_degeri_kurus_ile_gidip_gelir() -> None:
    known, _ = promotions.decode_conditions(KURAL["conditions"])
    assert known[0]["value"] == 50000            # 500,00 ₺ → kuruş
    back = promotions.encode_conditions(known)
    assert back[0]["value"] == "500.00"


def test_ekran_kosulu_degistirirse_bilinmeyen_yine_korunur() -> None:
    raw = {**KURAL, "conditions": [
        {"attribute": "product|category_ids", "attribute_type": "multiselect",
         "operator": "{}", "value": "4,9"},
        {"attribute": "cart|state", "attribute_type": "text", "operator": "==", "value": "34"},
    ]}
    body = promotions.write_rule_body(raw, {"conditions": [
        {"kind": "subtotal", "operator": ">=", "value": 25000},
    ]})
    attributes = [item["attribute"] for item in body["conditions"]]
    assert attributes == ["cart|base_sub_total", "cart|state"]


# ============================================ TUZAK 1 — oku-değiştir-yaz

def test_dokunulmayan_alanlar_gonderilen_govdede_kalir() -> None:
    body = promotions.write_rule_body(KURAL, {"name": "Ekim kampanyası"})
    assert body["name"] == "Ekim kampanyası"
    assert body["uses_per_coupon"] == 100          # dokunulmadı ama gövdede
    assert body["action_type"] == "by_percent"
    assert body["coupon_code"] == "EYLUL"


def test_kanal_ve_grup_nesneden_kimlige_cevrilir() -> None:
    body = promotions.write_rule_body(KURAL, {})
    assert body["channels"] == [1]
    assert body["customer_groups"] == [2]


def test_kupon_tipi_kapatilinca_kod_alani_da_temizlenir() -> None:
    body = promotions.write_rule_body(KURAL, {"couponType": "none"})
    assert body["coupon_type"] == 0
    assert body["coupon_code"] == ""


# ============================================== TUZAK 7 — üst sınır alanı

def test_ust_sinir_magazada_yoksa_govdeye_konmaz() -> None:
    body = promotions.write_rule_body(KURAL, {"action": {"kind": "by_percent", "value": 10,
                                                         "maxDiscount": 5000}})
    assert promotions.MAX_DISCOUNT_KEY not in body


def test_ust_sinir_magazada_varsa_kurus_ondaligiyla_yazilir() -> None:
    raw = {**KURAL, promotions.MAX_DISCOUNT_KEY: "0.0000"}
    body = promotions.write_rule_body(raw, {"action": {"kind": "by_percent", "value": 10,
                                                       "maxDiscount": 5000}})
    assert body[promotions.MAX_DISCOUNT_KEY] == "50.00"


# ================================================== yeni kural ve doğrulama

def test_yeni_kural_her_zaman_taslak_acilir() -> None:
    body = promotions.new_rule_body(
        {"name": "Deneme", "status": True, "action": {"kind": "by_percent", "value": 5}},
        channel_ids=[1], group_ids=[2, 3])
    assert body["status"] == 0, "yeni kural kendiliğinden yayına girmez"
    assert body["channels"] == [1]
    assert body["customer_groups"] == [2, 3]


def test_yuzde_yuzden_buyuk_olamaz() -> None:
    body = promotions.write_rule_body(KURAL, {"action": {"kind": "by_percent", "value": 140}})
    assert "0 ile 100" in promotions.rule_error(body)


def test_bitis_baslangictan_once_olamaz() -> None:
    body = promotions.write_rule_body(KURAL, {"startsFrom": "2026-09-01",
                                              "endsTill": "2026-08-01"})
    assert "Bitiş tarihi" in promotions.rule_error(body)


def test_kupon_tipi_secili_ama_kod_yoksa_reddedilir() -> None:
    body = promotions.write_rule_body(
        KURAL, {"couponType": "specific", "couponCode": "", "autoGenerated": False})
    assert "Kupon kodu zorunlu" in promotions.rule_error(body)


def test_bos_kategori_kosulu_sessizce_gecmez() -> None:
    problem = promotions.condition_error([{"kind": "category", "operator": "{}", "value": []}])
    assert "hiçbir kayıt seçilmedi" in problem


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert promotions.reason_error("kısa")
    assert promotions.reason_error("Eylül kampanyası bitti") == ""


# ================================================== kullanım ve kupon satırı

def test_limitsiz_kullanimda_oran_yoktur_sifir_yazilmaz() -> None:
    view = promotions.usage_view(12, 0)
    assert view["ratio"] is None
    assert view["label"] == "12/sınırsız"
    assert view["exhausted"] is False


def test_limit_dolunca_tukenmis_isaretlenir() -> None:
    view = promotions.usage_view(100, 100)
    assert view["exhausted"] is True
    assert view["ratio"] == 100.0


def test_kullanilmis_kupon_kaldirilabilir_isaretlenmez() -> None:
    row = promotions.coupon_row({"id": 3, "code": "A1", "times_used": 2, "usage_limit": 5},
                                today=BUGUN)
    assert row["removable"] is False
    assert row["stateLabel"] == "Kullanılabilir"


def test_suresi_gecmis_kupon_durumunu_soyler() -> None:
    row = promotions.coupon_row({"id": 4, "code": "B2", "times_used": 0, "usage_limit": 1,
                                 "expired_at": "2026-01-01"}, today=BUGUN)
    assert row["state"] == "expired"
    assert row["stateLabel"] == "Süresi doldu"


def test_bu_hafta_dolan_kural_isaretlenir() -> None:
    row = promotions.cart_rule_row({**KURAL, "ends_till": "2026-08-16"}, today=BUGUN,
                                   expiring_days=7)
    assert row["expiringSoon"] is True
    uzak = promotions.cart_rule_row({**KURAL, "ends_till": "2026-12-16"}, today=BUGUN,
                                    expiring_days=7)
    assert uzak["expiringSoon"] is False


# ============================================================ katalog kuralı

def test_katalog_kuralinda_sepet_eylemi_kabul_edilmez() -> None:
    current = {"name": "Vitrin", "status": 1, "action_type": "by_percent",
               "discount_amount": "5", "conditions": [], "channels": [1], "customer_groups": [2]}
    body = promotions.write_catalog_body(current, {"action": {"kind": "buy_x_get_y", "value": 3}})
    assert body["action_type"] == "by_percent"
