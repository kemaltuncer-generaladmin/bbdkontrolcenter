"""İade tutarı hesabı ve veri dönüşümleri — saf mantık, ağa çıkmaz.

Her testin adı, korunan tuzağı söyler.
"""

from __future__ import annotations

from typing import Any

from store_refunds_backend import calc
from store_refunds_fakes import (
    LIVE_ORDER,
    LIVE_REFUND,
    LIVE_RETURN_REQUEST,
    LIVE_SALES_STATS,
    ORDER,
)

TODAY = "2026-08-13"


def _view(**overrides: Any) -> dict[str, Any]:
    raw = {**ORDER, **overrides}
    return calc.order_view(raw)


# =================================================== TUZAK 2 — iade edilebilir adet

def test_faturalanmamis_kalem_iade_edilemez() -> None:
    # Parası hiç alınmamış kalemi "iade etmek" parayı ikinci kez çıkarır.
    lines = {line["sku"]: line for line in _view()["lines"]}
    assert lines["KTP-3"]["qtyInvoiced"] == 0
    assert lines["KTP-3"]["refundable"] == 0


def test_iade_edilebilir_adet_faturalanandan_iade_edileni_duser() -> None:
    lines = {line["sku"]: line for line in _view()["lines"]}
    assert lines["KTP-2"]["qtyInvoiced"] == 2
    assert lines["KTP-2"]["qtyRefunded"] == 1
    assert lines["KTP-2"]["refundable"] == 1


def test_hic_faturalanmamis_siparis_sipariste_adedine_duser_ve_bunu_soyler() -> None:
    # Faturasız akışta ekran çalışmaz duruma düşmemeli ama SESSİZ de kalmamalı.
    items = [{**item, "qty_invoiced": 0} for item in ORDER["items"]]
    view = _view(items=items)
    assert view["invoicedAny"] is False
    assert view["basis"] == "ordered"
    assert {line["sku"]: line["refundable"] for line in view["lines"]}["KTP-1"] == 3


# ==================================================== TUZAK 3 — orantılı pay

def test_kismi_iadede_kdv_ve_indirim_orantili_paylasilir() -> None:
    # 3 adetten 1'i: indirim ve KDV üçte bir gider, tamamı değil.
    view = _view()
    result = calc.compute(view["lines"], {"101": 1})
    row = result["lines"][0]
    assert row["base"] == 10_000        # 1 × 100,00 ₺
    assert row["discount"] == 1_000     # 30,00 ₺'nin üçte biri
    assert row["tax"] == 2_000          # 60,00 ₺'nin üçte biri
    assert row["total"] == 11_000
    assert result["total"] == 11_000


def test_yarim_kurus_yukari_yuvarlanir() -> None:
    # float ile bölmek kuruş kaydırıyor, mağazayla mutabakatı bozuyordu.
    assert calc.share(1_000, 1, 3) == 333
    assert calc.share(2_000, 1, 3) == 667
    assert calc.share(1, 1, 2) == 1
    assert calc.share(0, 1, 3) == 0
    assert calc.share(5_000, 3, 3) == 5_000


def test_iki_kalem_toplami_satir_satir_ayni_cikar() -> None:
    view = _view()
    result = calc.compute(view["lines"], {"101": 1, "102": 1})
    assert result["base"] == 15_000
    assert result["discount"] == 1_000
    assert result["tax"] == 3_000
    assert result["total"] == 17_000
    assert result["unitCount"] == 2
    assert sum(row["total"] for row in result["lines"]) == result["total"]


def test_iade_edilebilir_adetten_fazlasi_kirpilir_ve_bildirilir() -> None:
    # Sessizce kırpmak "3 adet iade ettim" sanan personel üretir.
    view = _view()
    result = calc.compute(view["lines"], {"102": 5})
    assert result["lines"][0]["qty"] == 1
    assert result["clamped"] == ["KTP-2"]


def test_secim_yoksa_hesap_bostur() -> None:
    result = calc.compute(_view()["lines"], {})
    assert result["empty"] is True
    assert result["total"] == 0


# ======================================================== TUZAK 4 — kargo

def test_kargo_iadesi_isaretlenmedikce_toplama_girmez() -> None:
    view = _view()
    result = calc.compute(view["lines"], {"101": 1},
                          shipping_available=view["shippingAvailable"])
    assert result["shipping"] == 0
    assert result["shippingAvailable"] == 4_800
    assert result["total"] == 11_000


def test_kargo_iadesi_isaretlenince_ayri_satir_olarak_eklenir() -> None:
    view = _view()
    result = calc.compute(view["lines"], {"101": 1}, refund_shipping=True,
                          shipping_available=view["shippingAvailable"])
    assert result["shipping"] == 4_800      # 40,00 + 8,00 KDV
    assert result["total"] == 15_800


def test_daha_once_iade_edilen_kargo_ikinci_kez_verilmez() -> None:
    view = _view(shipping_amount_refunded="48.00")
    assert view["shippingAvailable"] == 0
    result = calc.compute(view["lines"], {"101": 1}, refund_shipping=True,
                          shipping_available=view["shippingAvailable"])
    assert result["shipping"] == 0


def test_kesinti_toplami_negatife_dusurunce_isaretlenir() -> None:
    view = _view()
    result = calc.compute(view["lines"], {"101": 1}, adjustment_fee=99_000)
    assert result["negative"] is True


def test_govde_kargoyu_sifir_olarak_acikca_gonderir() -> None:
    # Alanı hiç göndermemek Bagisto'nun kendi varsayılanını uygulatıyordu.
    view = _view()
    result = calc.compute(view["lines"], {"101": 2})
    items, adjustments = calc.refund_body(result)
    assert items == {"101": 2}
    assert adjustments["shipping"] == "0.00"
    assert adjustments["adjustment_refund"] == "0.00"
    assert adjustments["adjustment_fee"] == "0.00"


# ================================================ TUZAK 5 — mağaza karşılaştırması

def test_magaza_hesabi_sorulamadiysa_uyusuyor_sayilmaz() -> None:
    verdict = calc.compare(17_000, None)
    assert verdict["matches"] is None
    assert "alınamadı" in verdict["message"]


def test_magaza_ayni_tutari_hesaplarsa_uyusur() -> None:
    verdict = calc.compare(17_000, 17_000)
    assert verdict["matches"] is True
    assert verdict["diff"] == 0


def test_magaza_farkli_hesaplarsa_ekran_susmaz() -> None:
    verdict = calc.compare(17_000, 17_500)
    assert verdict["matches"] is False
    assert verdict["diff"] == 500
    assert "MAĞAZANINKİDİR" in verdict["message"]


def test_magaza_onizlemesinden_toplam_zarfli_da_okunur() -> None:
    assert calc.store_total({"data": {"grand_total": "175.00"}}) == 17_500
    assert calc.store_total({"grandTotal": "175.00"}) == 17_500
    assert calc.store_total({"baska": 1}) is None


# ==================================================== TUZAK 6 — durum eşlemesi

def test_bilinmeyen_durum_rastgele_eslenmez() -> None:
    # `closed` iade de olabilir ret de; birini seçmek yanlış tutarı
    # "iade edildi" sütununa yazardı.
    assert calc.status_of("closed") == calc.ST_OTHER
    assert calc.status_label(calc.ST_OTHER, "closed") == "Bilinmiyor (closed)"
    assert calc.status_of("") == calc.ST_OTHER


def test_bilinen_durumlar_turkce_etiketine_cevrilir() -> None:
    assert calc.status_of("item_received") == calc.ST_RECEIVED
    assert calc.status_of("Awaiting Item") == calc.ST_AWAITING
    assert calc.STATUS_LABELS[calc.status_of("declined")] == "Reddedildi"


def test_yontem_serbest_metinden_okunur() -> None:
    assert calc.method_of("Kredi Kartı (Iyzico)") == "pos"
    assert calc.method_of("bank_transfer") == "transfer"
    assert calc.method_of("") == "unknown"


# ==================================================== TUZAK 1 — iki kaynak

def _refund(order_id: int, refund_id: int = 1, total: str = "110.00",
            created: str = "2026-08-10T09:00:00") -> dict[str, Any]:
    return {"id": refund_id, "increment_id": f"K-{refund_id}", "order_id": order_id,
            "created_at": created, "grand_total": total,
            "order": {"id": order_id, "increment_id": f"S-{order_id}"},
            "items": [{"name": "Matematik"}]}


def _request(order_id: int, request_id: int = 5, status: str = "requested",
             created: str = "2026-08-09T09:00:00") -> dict[str, Any]:
    return {"id": request_id, "order_id": order_id, "status": status, "reason": "defective",
            "created_at": created, "amount": "110.00", "carrier": "Aras",
            "tracking_number": "AR-77", "order": {"increment_id": f"S-{order_id}"}}


def test_ayni_siparisin_talebi_ve_kredi_notu_tek_satirda_birlesir() -> None:
    rows = calc.merge_rows([calc.refund_row(_refund(12))],
                           [calc.request_row(_request(12))])
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "refund"           # para satırı kazanır
    assert row["status"] == calc.ST_REFUNDED
    assert row["reasonLabel"] == "Ayıplı"      # sebep talepten taşındı
    assert row["carrier"] == "Aras"
    assert row["tracking"] == "AR-77"
    assert row["requestId"] == 5


def test_talebin_kredi_notu_yoksa_kendi_satiri_olarak_kalir() -> None:
    rows = calc.merge_rows([calc.refund_row(_refund(12))],
                           [calc.request_row(_request(99, request_id=7))])
    assert len(rows) == 2
    open_row = next(row for row in rows if row["source"] == "request")
    assert open_row["status"] == calc.ST_REQUESTED
    assert open_row["orderId"] == 99


def test_birden_cok_kredi_notunda_talep_en_eskisine_baglanir() -> None:
    early = calc.refund_row(_refund(12, refund_id=1, created="2026-08-05T09:00:00"))
    late = calc.refund_row(_refund(12, refund_id=2, created="2026-08-11T09:00:00"))
    calc.merge_rows([late, early], [calc.request_row(_request(12))])
    assert early["requestId"] == 5
    assert late["requestId"] == 0


def test_liste_en_yeniden_eskiye_siralanir() -> None:
    rows = calc.merge_rows(
        [calc.refund_row(_refund(1, refund_id=1, created="2026-08-01T09:00:00")),
         calc.refund_row(_refund(2, refund_id=2, created="2026-08-12T09:00:00"))], [])
    assert [row["orderId"] for row in rows] == [2, 1]


# ======================================================= anahtar süzgeçler

def test_cayma_suresi_icindeki_talep_isaretlenir() -> None:
    row = calc.request_row({**_request(12), "order_created_at": "2026-08-05T09:00:00"})
    marks = calc.flags(row, today=TODAY, free_return_days=14)
    assert marks["withinWithdrawal"] is True
    assert marks["withdrawalDays"] == 8


def test_cayma_suresi_gecmis_talep_isaretlenmez() -> None:
    row = calc.request_row({**_request(12), "order_created_at": "2026-07-01T09:00:00"})
    assert calc.flags(row, today=TODAY, free_return_days=14)["withinWithdrawal"] is False


def test_bekleyen_urun_esigi_gecince_gec_kalmis_sayilir() -> None:
    row = calc.request_row(_request(12, status="awaiting", created="2026-08-01T09:00:00"))
    marks = calc.flags(row, today=TODAY, return_wait_days=7)
    assert marks["itemLate"] is True
    assert marks["waitingDays"] == 12


def test_pos_iadesi_basarisizsa_isaretlenir() -> None:
    row = calc.request_row({**_request(12), "pos_state": "failed"})
    assert calc.flags(row, today=TODAY)["posFailed"] is True


# ================================================================== özet

def test_bekleyen_tutar_yalniz_parasi_cikmamislari_toplar() -> None:
    rows = calc.merge_rows(
        [calc.refund_row(_refund(12, total="110.00", created="2026-08-10T09:00:00"))],
        [calc.request_row(_request(99, request_id=7, status="approved"))])
    summary = calc.summarize(rows, today=TODAY, sales_total=1_000_000)
    assert summary["pendingAmount"] == 11_000     # yalnız açık talep
    assert summary["pendingCount"] == 1
    assert summary["monthAmount"] == 11_000       # yalnız kredi notu
    assert summary["monthCount"] == 1


def test_iade_orani_satis_okunamadiysa_sifir_degil_bos() -> None:
    # Sıfır "bu ay hiç iade yok" demektir; bilinmeyen, satışın okunamadığıdır.
    assert calc.refund_rate(11_000, None) is None
    assert calc.refund_rate(11_000, 0) is None
    assert calc.refund_rate(11_000, 1_000_000) == 1.1


def test_gecen_ayin_iadesi_bu_ay_toplamina_girmez() -> None:
    rows = [calc.refund_row(_refund(12, created="2026-07-28T09:00:00"))]
    summary = calc.summarize(rows, today=TODAY)
    assert summary["monthAmount"] == 0
    assert summary["counts"][calc.ST_REFUNDED] == 1


def test_ay_siniri_yerel_takvime_gore_bulunur() -> None:
    assert calc.month_bounds("2026-02-13") == ("2026-02-01", "2026-02-28")
    assert calc.month_bounds("2026-12-31") == ("2026-12-01", "2026-12-31")


def test_pano_istatistiginden_satis_tutari_okunur() -> None:
    assert calc.sales_of({"total": "10000.00"}) == 1_000_000
    assert calc.sales_of({"data": {"revenue": "10000,00"}}) == 1_000_000
    assert calc.sales_of({"baska": "x"}) is None


# =================================================================== para

def test_para_ondaligi_kurusa_cevrilirken_float_sapmasi_olmaz() -> None:
    assert calc.to_kurus("1234.35") == 123_435
    assert calc.to_kurus("1.250,00") == 125_000
    assert calc.to_kurus("-40.00") == -4_000
    assert calc.to_kurus("") is None
    assert calc.to_kurus(None) is None
    assert calc.from_kurus(123_435) == "1234.35"


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert calc.reason_error("ok") != ""
    assert calc.reason_error("müşteri ayıplı ürün bildirdi") == ""


# ======================================================= CANLI BİÇİM (camelCase)
#
# Bu bölümün tamamı canlı mağazadan alınmış yanıtlara bakar. Modül bir süre
# YALNIZ snake_case ve gömülü `order` okuyordu: ekran açılıyor, tablo "—" ile
# doluyor ve kimse fark etmiyordu. Testler o hatanın geri gelmesini engeller.

def test_canli_kredi_notunda_siparis_numarasi_ve_musteri_okunur() -> None:
    row = calc.refund_row(LIVE_REFUND)
    assert row["orderNumber"] == "5"            # `orderIncrementId`
    assert row["customer"] == "veysel kemal TUNCER"   # `customerName`
    assert row["email"] == "vveyselkemall@gmail.com"  # `customerEmail`
    assert row["orderDate"] == "2026-07-10"     # `orderDate`, gömülü order YOK
    assert row["amount"] == 839_300             # ondalık → kuruş
    assert row["date"] == "2026-07-25"


def test_canli_kredi_notu_listesi_kalem_vermez_adet_toplami_kullanilir() -> None:
    # `items` liste ucunda HER ZAMAN boş; `totalQty` iade edilen adettir.
    assert LIVE_REFUND["items"] == []
    assert calc.refund_row(LIVE_REFUND)["itemCount"] == 7


def test_canli_siparisin_musteri_ve_tarih_alanlari_okunur() -> None:
    view = calc.order_view(LIVE_ORDER)
    # Gömülü `customer` NULL: ad, siparişin kendi camelCase alanlarından gelir.
    assert view["customer"] == "veysel kemal TUNCER"
    assert view["email"] == "vveyselkemall@gmail.com"
    assert view["orderDate"] == "2026-07-10 02:33:36"
    assert view["grandTotal"] == 839_300


def test_canli_siparis_kalemi_camelcase_adet_alanlarindan_okunur() -> None:
    line = calc.order_view(LIVE_ORDER)["lines"][0]
    assert line["sku"] == "BBD2026SKU0540"
    assert (line["qtyOrdered"], line["qtyInvoiced"], line["qtyRefunded"]) == (7, 7, 0)
    assert line["refundable"] == 7
    assert line["unitPrice"] == 119_900


def test_gomulu_musteri_nesnesi_camelcase_adlari_da_okunur() -> None:
    raw = {**LIVE_ORDER, "customerFirstName": "", "customerLastName": "",
           "customer": {"firstName": "Ayşe", "lastName": "Yılmaz"}}
    assert calc.order_view(raw)["customer"] == "Ayşe Yılmaz"


def test_kargo_bedeli_kredi_notlarindan_dusulur() -> None:
    # Sipariş `shipping_amount_refunded` YAYINLAMIYOR; tek kaynak kredi notları.
    view = calc.order_view(LIVE_ORDER)
    assert view["shippingAvailable"] == 12_000
    already = calc.shipping_refunded_of([{**LIVE_REFUND, "shippingAmount": 100,
                                          "shippingTaxAmount": 20}])
    assert already == 12_000
    kisik = calc.order_view(LIVE_ORDER, shipping_refunded=already)
    assert kisik["shippingAvailable"] == 0


def test_canli_talebin_durumu_sozlukten_okunur_ham_sozluk_ekrana_yazilmaz() -> None:
    # Uç durumu SÖZLÜK veriyor. `str()`'e vermek rozete ham Python sözlüğünü
    # ("Bilinmiyor ({'id': 5, ...})") yazıyordu; üstelik Türkçe başlık
    # aranmadığı için CANLI talebin hepsi "Bilinmiyor" çipine düşüyordu.
    row = calc.request_row(LIVE_RETURN_REQUEST)
    assert row["status"] == calc.ST_REFUNDED
    assert row["statusLabel"] == "İade edildi"
    assert "{" not in row["statusLabel"]


def test_canli_talepte_siparis_numarasi_camelcase_alandan_okunur() -> None:
    # `orderIncrementId` aranmadığı için "Sipariş" sütunu boş kalıyordu.
    row = calc.request_row(LIVE_RETURN_REQUEST)
    assert row["orderNumber"] == "11"
    assert row["orderId"] == 11
    assert row["customer"] == "veysel kemal TUNCER"
    assert row["itemCount"] == 1
    assert row["date"] == "2026-07-20"


def test_canli_turkce_durum_basliklari_kendi_cipine_dusuyor() -> None:
    assert calc.status_of({"title": "İnceleniyor"}) == calc.ST_REQUESTED
    assert calc.status_of({"title": "Onaylandı"}) == calc.ST_APPROVED
    assert calc.status_of({"title": "Kargoda"}) == calc.ST_AWAITING
    assert calc.status_of({"title": "Reddedildi"}) == calc.ST_REJECTED
    # "Çözüldü" parası çıktı mı söylemiyor — `closed` gibi BİLİNMEZ kalır.
    assert calc.status_of({"title": "Çözüldü"}) == calc.ST_OTHER


def test_canli_turkce_sebep_eslenir_eslenmeyen_ham_metniyle_gorunur() -> None:
    assert calc.request_row(LIVE_RETURN_REQUEST)["reasonLabel"] == "Ayıplı"
    # Mağaza yöneticisi yeni bir sebep tanımlarsa operatör onu OLDUĞU GİBİ
    # görür; yalnız "Diğer" demek müşterinin yazdığını saklamak olurdu.
    yeni = {**LIVE_RETURN_REQUEST, "reason": "Yanlış Beden Seçtim"}
    assert calc.request_row(yeni)["reasonLabel"] == "Diğer (Yanlış Beden Seçtim)"


def test_pano_toplam_satisi_canli_bicimden_okunur() -> None:
    # `statistics.total_sales.current` — bu ad aranmadığı için iade oranı
    # mağaza ayaktayken bile hep "—" görünüyordu.
    assert calc.sales_of(LIVE_SALES_STATS) == 1_600
    assert calc.sales_of(LIVE_SALES_STATS[0]) == 1_600


def test_bozuk_tarih_bos_gune_cevrilir() -> None:
    # Uç şeması yalnız uzunluğa bakıyor; ham metin `fromisoformat`'ı patlatırdı.
    assert calc.iso_day("abc") == ""
    assert calc.iso_day("2026-13-45") == ""
    assert calc.iso_day("") == ""
    assert calc.iso_day("2026-08-13T10:00:00") == "2026-08-13"


def test_siparisi_cozulemeyen_kayitlar_birbirine_birlesmez() -> None:
    # orderId 0 "aynı sipariş" demek değildir; anahtar olarak kullanılamaz.
    orphan = calc.refund_row({"id": 3, "created_at": "2026-08-10T09:00:00",
                              "grand_total": "50.00"})
    rows = calc.merge_rows([orphan], [calc.request_row(
        {"id": 9, "status": "requested", "created_at": "2026-08-09T09:00:00"})])
    assert len(rows) == 2
    assert orphan["requestId"] == 0
