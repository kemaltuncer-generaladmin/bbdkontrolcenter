"""Sipariş verisinin saf dönüşümleri. Ağa çıkmaz, servis kurulmaz."""

from __future__ import annotations

from typing import Any

from store_orders_backend import orders as ord_


def _order(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": 12, "increment_id": "1000012", "status": "processing",
        "created_at": "2026-08-10T09:30:00", "customer_first_name": "Ayşe",
        "customer_last_name": "Yılmaz", "customer_email": "ayse@ornek.com",
        "grand_total": "1250.00", "sub_total": "1000.00", "shipping_amount": "50.00",
        "discount_amount": "0.00", "tax_amount": "200.00",
        "grand_total_invoiced": "0.00", "grand_total_refunded": "0.00",
        "total_qty_ordered": 3, "channel_name": "Varsayılan",
        "items": [{"id": 5, "sku": "KLM-1", "name": "Kalem", "qty_ordered": 3,
                   "qty_invoiced": 0, "qty_shipped": 0, "price": "333.33",
                   "total": "1000.00"}],
        # Detay ucu bu üç koleksiyonu BOŞ DA OLSA gönderir; liste ucu hiç
        # göndermez. `has_detail()` ayrımı buna dayanır.
        "invoices": [], "shipments": [], "refunds": [], "comments": [],
        "shipping_address": {"first_name": "Ayşe", "last_name": "Yılmaz", "city": "Ankara",
                             "phone": "5321234567", "address": ["Atatürk Cad. 5"],
                             "postcode": "06000", "country_name": "Türkiye"},
    }
    base.update(extra)
    return base


# ================================================= TUZAK 1 — ödeme durumu

def test_odeme_durumu_faturalanan_tutardan_cikarilir() -> None:
    # Bagisto'da "ödendi mi" diye bir alan YOK; faturalanan tutar tahsil
    # edilmiş sayılır.
    assert ord_.payment_state(_order()) == "unpaid"
    assert ord_.payment_state(_order(grand_total_invoiced="600.00")) == "partial"
    assert ord_.payment_state(_order(grand_total_invoiced="1250.00")) == "paid"


def test_parasi_geri_verilen_siparis_odendi_gorunmez() -> None:
    row = _order(grand_total_invoiced="1250.00", grand_total_refunded="1250.00")
    assert ord_.payment_state(row) == "refunded"


# ================================================= TUZAK 2 — para çevrimi

def test_para_kurusa_float_olmadan_cevrilir() -> None:
    # float("1234.35") * 100 bazı değerlerde 123434.99999 verir ve int() bir
    # kuruş aşağı yuvarlar; gün sonu cirosu tutmaz.
    assert ord_.to_kurus("1234.35") == 123435
    assert ord_.to_kurus("1.250,00") == 125000
    assert ord_.to_kurus("") is None
    assert ord_.to_kurus("abc") is None
    assert ord_.kurus(None) == 0


# ============================================== TUZAK 3 — sipariş numarası

def test_siparis_numarasi_increment_id_kullanir_ic_kimlik_degil() -> None:
    row = ord_.order_row(_order())
    assert row["orderNo"] == "#1000012"
    assert row["id"] == 12                 # eylem uçlarına giden BUDUR


def test_siparis_no_bicimi_yer_tutuculari_doldurur() -> None:
    assert ord_.order_no(_order(), "BBD-{year}-{no}") == "BBD-2026-1000012"


def test_yer_tutucusuz_bicim_reddedilir() -> None:
    assert "yer tutucu" in ord_.format_error("sabit-metin")
    assert "Tanınmayan" in ord_.format_error("{no}-{kanal}")
    assert ord_.format_error("#{no}") == ""


# =================================================== TUZAK 4 — durum adları

def test_durum_adi_kullanicinin_verdigi_adla_ezilir() -> None:
    assert ord_.status_label("processing") == "Hazırlanıyor"
    assert ord_.status_label("processing", {"processing": "Paketleniyor"}) == "Paketleniyor"


def test_bilinmeyen_durum_kodu_uydurulmaz_ham_gosterilir() -> None:
    # Bilinmeyen bir kodu "Bekliyor" göstermek yanlış bilgiyi doğru gibi sunardı.
    assert ord_.status_label("on_hold") == "on_hold"


def test_uydurma_durum_kodu_ayarlara_yazilmaz() -> None:
    clean = ord_.clean_status_names({"processing": "Paket", "uydurma": "X", "pending": ""})
    assert clean == {"processing": "Paket"}


# ============================================ TUZAK 5 — süzgeç ayrımı

def test_magazanin_uygulayamadigi_suzgec_taramaya_zorlar() -> None:
    assert ord_.needs_scan({"status": "processing"}) is False
    assert ord_.needs_scan({"carrier": "Aras"}) is True
    assert ord_.needs_scan({"noInvoice": True}) is True
    assert ord_.needs_scan({"chip": "late"}) is True
    assert ord_.needs_scan({"dateField": "shipped", "dateFrom": "2026-08-01"}) is True
    # Kanal ADI koda benzemez; uca göndermek süzgecin sessizce yok sayılmasıydı.
    assert ord_.needs_scan({"channel": "Varsayılan"}) is True


def test_magazaya_yalnizca_uyguladigi_suzgecler_gonderilir() -> None:
    sent = ord_.store_filters({"status": "processing", "carrier": "Aras", "city": "Ankara",
                               "channel": "Varsayılan", "dateFrom": "2026-08-01",
                               "totalMin": 100_00})
    assert sent == {"status": "processing", "date_from": "2026-08-01",
                    "grand_total_from": "100.00"}
    # Kanal ADI uca gitmez; modül ayarındaki kanal KODU servis tarafından eklenir.
    assert "channel" not in sent


def test_kargo_tarihi_suzgeci_magazaya_gonderilmez() -> None:
    # Mağaza yalnız sipariş tarihini süzebiliyor; kargo tarihini gönderip
    # uygulandığını varsaymak süzülmemiş listeyi süzülmüş göstermek olurdu.
    sent = ord_.store_filters({"dateField": "shipped", "dateFrom": "2026-08-01"})
    assert "date_from" not in sent


def test_arama_siparis_no_ve_takip_no_uzerinde_calisir() -> None:
    row = ord_.order_row(_order(shipments=[{"id": 1, "track_number": "AR123456"}]))
    assert ord_.matches(row, {"q": "AR1234"}) is True
    assert ord_.matches(row, {"q": "1000012"}) is True
    assert ord_.matches(row, {"q": "ayse"}) is True
    assert ord_.matches(row, {"q": "olmayan"}) is False


def test_kargo_tarihi_olmayan_siparis_kargo_araligina_girmez() -> None:
    row = ord_.order_row(_order())
    assert ord_.matches(row, {"dateField": "shipped", "dateFrom": "2026-01-01"}) is False
    assert ord_.matches(row, {"dateField": "created", "dateFrom": "2026-01-01"}) is True


# ======================================= TUZAK 7 — kısmi fatura ve kargo

def test_kismi_fatura_ikili_degil_uc_durumdur() -> None:
    assert ord_.fulfilment(0, 3) == "none"
    assert ord_.fulfilment(1, 3) == "partial"
    assert ord_.fulfilment(3, 3) == "full"


def test_satir_kismi_kargoyu_gosterir() -> None:
    row = ord_.order_row(_order(total_qty_shipped=1))
    assert row["shipmentState"] == "partial"
    assert row["shipmentLabel"] == "Kısmi"


# ======================================== TUZAK 6 — "geciken" göreli kavram

def test_gecikme_esigi_ayarlanabilir_ve_tamamlanan_siparis_gecikmez() -> None:
    row = ord_.order_row(_order(), today="2026-08-14", late_days=3)
    assert row["lateDays"] == 4
    assert row["late"] is True

    gevsek = ord_.order_row(_order(), today="2026-08-14", late_days=10)
    assert gevsek["late"] is False

    iptal = ord_.order_row(_order(status="canceled"), today="2026-08-14", late_days=3)
    assert iptal["late"] is False


# ============================================= TUZAK 8 — iptal kapıları

def test_kargolanmis_siparis_iptal_edilemez() -> None:
    row = ord_.order_row(_order(total_qty_shipped=3))
    assert "iade" in ord_.cancel_block(row)


def test_iptal_penceresi_kapaninca_engellenir() -> None:
    row = ord_.order_row(_order())
    assert ord_.cancel_block(row, window_hours=0, now="2026-09-01T00:00:00+00:00") == ""
    engel = ord_.cancel_block(row, window_hours=24, now="2026-08-13T00:00:00+00:00")
    assert "pencere" in engel
    assert ord_.cancel_block(row, window_hours=24,
                             now="2026-08-10T12:00:00+00:00") == ""


def test_zaten_iptal_edilmis_siparis_tekrar_iptal_edilmez() -> None:
    row = ord_.order_row(_order(status="canceled"))
    assert "zaten" in ord_.cancel_block(row)


# ============================================= TUZAK 9 — adres birleştirme

def test_eksik_adres_parcasi_atlanir_none_yazilmaz() -> None:
    line = ord_.address_line({"address": ["Atatürk Cad. 5"], "city": "Ankara",
                              "country_name": None})
    assert line == "Atatürk Cad. 5, Ankara"
    assert ord_.address_line(None) == ""


# ================================================ toplulaştırma ve çipler

def test_iptal_edilen_siparis_ciroya_girmez() -> None:
    rows = [ord_.order_row(_order()),
            ord_.order_row(_order(id=13, status="canceled"))]
    total = ord_.summary(rows)
    assert total["count"] == 2
    assert total["liveCount"] == 1
    assert total["revenue"] == 125000
    assert total["average"] == 125000
    assert total["canceled"] == 1


def test_cip_sayaclari_her_cip_icin_ayri_hesaplanir() -> None:
    rows = [ord_.order_row(_order(), today="2026-08-10"),
            ord_.order_row(_order(id=13, status="pending"), today="2026-08-10"),
            ord_.order_row(_order(id=14, status="canceled"), today="2026-08-10")]
    counts = ord_.chip_counts(rows, "2026-08-10")
    assert counts["pending"] == 1
    assert counts["processing"] == 1
    assert counts["canceled"] == 1
    assert counts["today"] == 3


# ======================================================= toplu önizleme

def test_onizleme_atlanan_siparisin_nedenini_yazar() -> None:
    rows = [ord_.order_row(_order()),
            ord_.order_row(_order(id=13, status="canceled")),
            ord_.order_row(_order(id=14, total_qty_invoiced=3))]
    diff = ord_.batch_rows(rows, "invoice")
    assert diff[0]["skipped"] is False
    assert "iptal" in diff[1]["note"]
    assert "faturalanmış" in diff[2]["note"]


def test_faturasi_kesilmemis_siparis_kargoya_verilemez() -> None:
    diff = ord_.batch_rows([ord_.order_row(_order())], "ship")
    assert diff[0]["skipped"] is True
    assert "fatura" in diff[0]["note"]


def test_toplu_ozet_yalnizca_uygulanacaklari_toplar() -> None:
    rows = [ord_.order_row(_order(total_qty_invoiced=3)),
            ord_.order_row(_order(id=13, status="canceled"))]
    summary = ord_.batch_summary(ord_.batch_rows(rows, "ship"))
    assert summary == {"total": 2, "ready": 1, "skipped": 1, "amount": 125000}


# ================================================================ gerekçe

def test_kisa_gerekce_reddedilir() -> None:
    assert ord_.reason_error("ok") != ""
    assert ord_.reason_error("Müşteri iptal istedi") == ""
