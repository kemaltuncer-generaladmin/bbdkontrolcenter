"""Fatura dönüşümleri — saf mantık. Ağ yok, durum yok."""

from __future__ import annotations

from store_invoices_backend import ledger
from store_invoices_fakes import (
    invoice_detail_raw,
    invoice_raw,
    invoice_raw_snake,
    shipment_raw,
)

# ============================================================== para ve oran


def test_para_kurusa_decimal_ile_cevrilir_float_yuvarlamasi_sizmaz() -> None:
    # `float("1234.35") * 100` bazı yorumlayıcılarda 123434.999… verir.
    assert ledger.to_kurus("1234.35") == 123435
    assert ledger.to_kurus("1.234,56") == 123456
    assert ledger.to_kurus("1,234.56") == 123456
    assert ledger.to_kurus("") == 0
    assert ledger.to_kurus(None) == 0
    assert ledger.to_kurus("saçma") == 0


def test_sifir_oran_ile_bilinmeyen_oran_ayri_seylerdir() -> None:
    # Muafiyet (0) ile "oran gelmedi" aynı kovaya düşerse beyan yanlış dolar.
    assert ledger.to_rate("0.0000") == 0.0
    assert ledger.to_rate(None) is None
    assert ledger.to_rate("") is None
    assert ledger.rate_label(0.0) == "%0"
    assert ledger.rate_label(None) == "Ayrıştırılamadı"
    assert ledger.rate_label(18.5) == "%18,50"


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert ledger.reason_error("kısa") != ""
    assert ledger.reason_error("") != ""
    assert ledger.reason_error("mali müşavir istedi") == ""


# ================================================================ satırlar

def test_fatura_satiri_canli_camelcase_alanlari_okur() -> None:
    # KUSUR KAYDI: alanlar `grand_total` diye aranıyordu, canlı `grandTotal`
    # veriyor. Tablo açılıyor ama bütün tutarlar 0, numara "#0" görünüyordu.
    row = ledger.invoice_row(invoice_raw())
    assert row["id"] == 7
    assert row["number"] == "7"
    assert row["orderId"] == 3               # canlıda yalnız `order.id` var
    assert row["orderNumber"] == "1000003"
    assert row["net"] == 10_000
    assert row["tax"] == 2_000
    assert row["total"] == 12_000
    assert row["customer"] == "Ayşe Yılmaz"  # liste satırında `customerName`
    assert row["email"] == "ayse@example.com"
    assert row["createdAt"] == "2026-08-01 10:22"
    assert row["stateLabel"] == "Ödendi"


def test_ayni_satir_snake_case_biciminde_de_okunur() -> None:
    # `pick` alan adını normalleştirir: mağaza biçim değiştirse de ekran
    # kırılmaz. İki biçim de AYNI rakamı vermeli.
    camel = ledger.invoice_row(invoice_raw())
    snake = ledger.invoice_row(invoice_raw_snake())
    for key in ("net", "tax", "total", "customer", "orderId", "state"):
        assert camel[key] == snake[key], key


def test_vkn_detay_kaydindaki_adres_dizisinden_cikar() -> None:
    # Canlıda fatura DETAYINDA adres `order.addresses` dizisindedir ve
    # faturalama adresi `addressType` ile ayrılır; liste satırında adres yok.
    row = ledger.invoice_row(invoice_detail_raw())
    assert row["taxId"] == "1234567890"
    assert row["company"] == "Yılmaz Ltd."
    assert row["partyKindLabel"] == "Kurumsal"
    assert ledger.invoice_row(invoice_raw())["taxId"] == ""


def test_kalem_orani_alan_yokken_tutardan_turetilir_ve_isaretlenir() -> None:
    # Canlı fatura kalemlerinde `taxPercent` YOK; oran hep "Ayrıştırılamadı"
    # çıkıyordu. Tutardan türetilir ama türetildiği SÖYLENİR.
    items = ledger.invoice_items(invoice_raw())
    assert items[0]["rate"] == 20.0
    assert items[0]["rateDerived"] is True
    assert items[0]["net"] == 10_000
    assert items[0]["tax"] == 2_000
    # Oran alanı gelirse türetme yapılmaz.
    verili = ledger.invoice_items(invoice_raw_snake())
    assert verili[0]["rate"] == 20.0
    assert verili[0]["rateDerived"] is False


def test_kdv_tutari_hic_gelmezse_oran_sifir_degil_bilinmiyor() -> None:
    # 0 = muafiyet, None = bilinmiyor. İkisini birleştirmek beyanı bozar.
    bos, derived = ledger.item_rate(None, net=10_000, raw_tax=None)
    assert bos is None and derived is False
    sifir, derived = ledger.item_rate(None, net=10_000, raw_tax="0.00")
    assert sifir == 0.0 and derived is True


def test_vkn_yoksa_musteri_bireysel_sayilir() -> None:
    raw = invoice_raw()
    raw["billingAddress"] = {"firstName": "Ali", "lastName": "Demir"}
    row = ledger.invoice_row(raw)
    assert row["customer"] == "Ali Demir"
    assert row["partyKindLabel"] == "Bireysel"
    assert row["taxId"] == ""


def test_yasal_numara_eslenmediyse_satir_bunu_soyler() -> None:
    row = ledger.invoice_row(invoice_raw())
    assert row["legalMatched"] is False
    assert row["legalNo"] == ""

    eslenmis = ledger.invoice_row(invoice_raw(), legal={
        "series": "A2026", "number": 145, "legal_no": "A2026000000145",
        "issued_at": "2026-08-01"})
    assert eslenmis["legalMatched"] is True
    assert eslenmis["legalNo"] == "A2026000000145"


def test_bilinmeyen_durum_uydurulmaz_ham_haliyle_gosterilir() -> None:
    row = ledger.invoice_row(invoice_raw(state="frozen"))
    assert row["stateLabel"] == "frozen"
    assert row["stateTone"] == ""


def test_zarfli_kayit_da_okunur() -> None:
    # Geçit bazen {"data": {...}} zarfı veriyor; çağıranın bilmesi gerekmesin.
    row = ledger.invoice_row({"data": invoice_raw(11)})
    assert row["id"] == 11
    assert row["number"] == "11"


def test_irsaliye_satiri_canli_alanlari_okur() -> None:
    # KUSUR KAYDI: `carrier_title` / `track_number` / `inventory_source.name`
    # aranıyordu; canlı `carrierTitle` / `trackNumber` / `inventorySourceName`
    # veriyor. Taşıyıcı ve takip no sütunları boş görünüyordu.
    row = ledger.shipment_row(shipment_raw())
    assert row["id"] == 8
    assert row["orderId"] == 12
    assert row["orderNumber"] == "12"
    assert row["carrier"] == "Aras"
    assert row["trackNumber"] == "R123"
    assert row["source"] == "Varsayılan"
    assert row["totalQty"] == 2
    assert row["itemCount"] == 1
    assert row["customer"] == "veysel kemal TUNCER"   # `billingAddress`ten


def test_irsaliye_satiri_snake_case_biciminde_de_okunur() -> None:
    row = ledger.shipment_row({
        "id": 4, "order_id": 3, "created_at": "2026-08-02 09:00:00",
        "carrier_title": "Yurtiçi", "track_number": "YK123",
        "order": {"increment_id": "1000003"},
        "items": [{"id": 1}, {"id": 2}],
    })
    assert row["carrier"] == "Yurtiçi"
    assert row["trackNumber"] == "YK123"
    assert row["itemCount"] == 2


# =========================================================== süzgeç denetimi

def test_uygulanmayan_suzgec_yakalanir() -> None:
    rows = [{"state": "paid"}, {"state": "pending"}]
    assert ledger.filter_honored(rows, "state", "paid") is False
    assert ledger.filter_honored([{"state": "paid"}], "state", "paid") is True
    # Satır yoksa karar verilemez; "yok sayıldı" demek yanlış olurdu.
    assert ledger.filter_honored([], "state", "paid") is None
    assert ledger.filter_honored(rows, "state", "") is None


# ============================================================= seri numarası

def test_numara_boslugu_bulunur_ve_cumleye_cevrilir() -> None:
    gaps = ledger.number_gaps([143, 144, 148, 149])
    assert gaps == [{"from": 145, "to": 147, "count": 3}]
    assert ledger.gap_message("A2026", gaps) == "A2026 serisinde 145-147 eksik."


def test_tek_numaralik_bosluk_aralik_gibi_yazilmaz() -> None:
    gaps = ledger.number_gaps([10, 12])
    assert ledger.gap_message("B2026", gaps) == "B2026 serisinde 11 eksik."


def test_serinin_devami_bosluk_sayilmaz() -> None:
    # 1,2,3 sonrası henüz kesilmemiş numaralar boşluk değildir.
    assert ledger.number_gaps([1, 2, 3]) == []
    assert ledger.number_gaps([]) == []
    assert ledger.number_gaps([5]) == []


def test_cok_sayida_bosluk_kisaltilarak_gosterilir() -> None:
    gaps = ledger.number_gaps([1, 3, 5, 7, 9, 11, 13, 15])
    assert len(gaps) == 7
    message = ledger.gap_message("A2026", gaps)
    assert "+2 aralık daha" in message


def test_siradaki_numara_en_buyugun_bir_fazlasidir() -> None:
    assert ledger.next_number([143, 144, 148], start=1) == 149
    assert ledger.next_number([], start=100) == 100
    assert ledger.next_number([5], start=100) == 100


def test_yasal_numara_seri_ve_sira_ile_uretilir() -> None:
    assert ledger.compose_legal_no("A2026", 145) == "A2026000000145"
    assert ledger.compose_legal_no("A2026", 145, pad=3) == "A2026145"
    assert ledger.compose_legal_no("A2026", 0) == ""


def test_eksik_seri_ya_da_sifir_numara_reddedilir() -> None:
    assert ledger.legal_error(series="", number=5) != ""
    assert ledger.legal_error(series="A2026", number=0) != ""
    assert ledger.legal_error(series="A2026", number=5) == ""
    assert ledger.legal_error(series="A2026", number=5, legal_no="x" * 40) != ""


# ============================================================ toplulaştırma

def test_oran_kirilimi_kalemlerden_toplanir() -> None:
    rows = [ledger.invoice_row(invoice_raw(1)), ledger.invoice_row(invoice_raw(2))]
    details = {
        1: [{"rate": 20.0, "net": 10_000, "tax": 2_000},
            {"rate": 10.0, "net": 5_000, "tax": 500}],
        2: [{"rate": 20.0, "net": 4_000, "tax": 800}],
    }
    table = ledger.rate_rows(rows, details)
    by_label = {row["rateLabel"]: row for row in table}
    assert by_label["%20"]["net"] == 14_000
    assert by_label["%20"]["tax"] == 2_800
    assert by_label["%20"]["invoices"] == 2
    assert by_label["%10"]["invoices"] == 1
    assert by_label["%20"]["total"] == 16_800


def test_kalem_yoksa_oran_fatura_basligindan_turetilir() -> None:
    # KUSUR KAYDI: canlı LİSTE ucu her faturada `items: []` veriyor. Kalem
    # bekleyen kırılım, dönem icmalinin TAMAMINI "Ayrıştırılamadı" yapıyordu —
    # yani mali müşavirin bakacağı tek tablo boş çıkıyordu.
    rows = [ledger.invoice_row(invoice_raw(1))]
    table = ledger.rate_rows(rows, {})
    assert len(table) == 1
    assert table[0]["rateLabel"] == "%20"
    assert table[0]["net"] == 10_000
    assert table[0]["derived"] is True


def test_kdv_tutari_hic_gelmeyen_fatura_ayristirilamadi_kalir() -> None:
    # Türetme, veriyi UYDURMAZ: tutar yoksa oran da yoktur.
    raw = invoice_raw(1)
    raw.pop("taxAmount")
    raw.pop("baseTaxAmount")
    row = ledger.invoice_row(raw)
    assert row["headerRate"] is None
    table = ledger.rate_rows([row], {})
    assert table[0]["rateLabel"] == "Ayrıştırılamadı"


def test_bilinmeyen_oran_tablonun_sonunda_durur() -> None:
    bilinmeyen = invoice_raw(2)
    bilinmeyen.pop("taxAmount")
    bilinmeyen.pop("baseTaxAmount")
    rows = [ledger.invoice_row(invoice_raw(1)), ledger.invoice_row(bilinmeyen)]
    details = {1: [{"rate": 20.0, "net": 1_000, "tax": 200}]}
    table = ledger.rate_rows(rows, details)
    assert table[-1]["rateLabel"] == "Ayrıştırılamadı"


def test_donem_icmali_iadeleri_duser_ve_eslenmeyeni_sayar() -> None:
    rows = [
        ledger.invoice_row(invoice_raw(1, created="2026-08-01 09:00:00")),
        ledger.invoice_row(invoice_raw(2, created="2026-08-02 09:00:00"),
                           legal={"series": "A2026", "number": 1,
                                  "legal_no": "A2026000000001"}),
    ]
    summary = ledger.period_summary(rows, {}, [{"total": 3_000}])
    assert summary["count"] == 2
    assert summary["total"] == 24_000
    assert summary["refundTotal"] == 3_000
    assert summary["netTotal"] == 21_000
    assert summary["missingLegal"] == 1
    assert summary["missingNumbers"] == ["1"]
    assert [item["day"] for item in summary["byDay"]] == ["2026-08-01", "2026-08-02"]


def test_muhasebe_csv_yasal_numara_sutunu_tasir() -> None:
    rows = [ledger.invoice_row(invoice_raw(1))]
    headers, table = ledger.csv_table(rows, lambda value: str(value))
    assert "Yasal fatura no" in headers
    assert table[0][1] == "—"


# ========================================================= yerel süzgeçler

def test_yerel_arama_numarayi_musteriyi_ve_yasal_numarayi_tarar() -> None:
    # Mağaza `search` parametresini SESSİZCE YOK SAYIYOR (canlıda denendi);
    # arama sayfa üzerinde yerel yapılır.
    row = ledger.invoice_row(invoice_raw(), legal={"legal_no": "A2026000000145",
                                                   "series": "A2026", "number": 145})
    assert ledger.matches_query(row, "ayşe") is True
    assert ledger.matches_query(row, "A2026") is True
    assert ledger.matches_query(row, "1000003") is True
    assert ledger.matches_query(row, "yok böyle bir şey") is False
    assert ledger.matches_query(row, "") is True     # boş süzgeç elemez


def test_yerel_tutar_araligi_kurusla_calisir() -> None:
    row = ledger.invoice_row(invoice_raw())          # 120,00 TL = 12.000 kuruş
    assert ledger.matches_total(row, 10_000, 20_000) is True
    assert ledger.matches_total(row, 12_001, None) is False
    assert ledger.matches_total(row, None, 11_999) is False
    assert ledger.matches_total(row, None, None) is True


def test_sessizce_yok_sayilan_suzgecler_listede_kayitli() -> None:
    # Bu liste canlıya karşı denenmiş bulgudur; kod bir daha göndermesin.
    assert "search" in ledger.IGNORED_FILTERS
    assert "grand_total_from" in ledger.IGNORED_FILTERS
    assert set(ledger.SERVER_FILTERS) == {"state", "date_from", "date_to", "order_id"}
