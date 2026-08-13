"""CANLI GÖVDE BİÇİMİ — mağazanın gerçekten döndürdüğü yanıtlara karşı.

İçindeki sözlükler `bbdstore.com.tr` üzerinde SALT OKUNARAK alınan
`GET /api/admin/orders`, `/orders/20`, `/catalog/products`,
`/catalog/categories/tree`, `/customers`, `/invoices` ve `/transactions`
yanıtlarından kısaltılmıştır; alan adları ve değer tipleri OLDUĞU GİBİDİR —
uydurulmuş snake_case veriye karşı geçen test hiçbir şey kanıtlamıyordu.

Buradaki her testin arkasında ekranda GÖRÜLMÜŞ bir arıza vardır: hepsi
sessizdi, hiçbiri istisna atmıyordu.
"""

from __future__ import annotations

from typing import Any

from store_reports_backend import analytics as an
from store_reports_backend import builders

# ------------------------------------------------------------- canlı gövdeler

#: `GET /api/admin/orders` — LİSTE satırı. Adres YOK, para dökümü YOK,
#: `items` var ama SIĞ.
LIVE_ORDER_LIST: dict[str, Any] = {
    "id": 20,
    "incrementId": "20",
    "status": "processing",
    "statusLabel": "Processing",
    "channelId": 1,
    "channelName": "Benim Başarı Dünyam",
    "isGuest": False,
    "customerId": 1,
    "customerEmail": "tuncerdjdhjd@gmail.com",
    "customerName": "",
    "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "couponCode": None,
    "totalItemCount": 1,
    "totalQtyOrdered": 2,
    "orderCurrencyCode": "TRY",
    "grandTotal": 4,
    "baseGrandTotal": 4,
    "formattedGrandTotal": "₺4,00",
    "location": "Selçuklu, 42, TR",
    "createdAt": "2026-08-13 19:54:51",
    "updatedAt": "2026-08-13 19:54:54",
    "items": [{"id": 21, "sku": "123456789123456789", "name": "TEST ÜRÜNÜDÜR",
               "qtyOrdered": 2,
               "productImage": "https://bbdstore.com.tr/storage/product/1428/x.png"}],
}

#: `GET /api/admin/orders/20` — DETAY. Adres `addresses[]` dizisinde, grup
#: `customer.group.name` altında, kalem para dökümlü.
LIVE_ORDER_DETAIL: dict[str, Any] = {
    "id": 20,
    "incrementId": "20",
    "status": "processing",
    "channelName": "Benim Başarı Dünyam",
    "customerEmail": "tuncerdjdhjd@gmail.com",
    "customerFirstName": None,
    "customerLastName": None,
    "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "totalItemCount": 1,
    "grandTotal": 4,
    "grandTotalRefunded": 0,
    "subTotal": 4,
    "taxAmount": 0,
    "discountAmount": 0,
    "shippingAmount": 0,
    "createdAt": "2026-08-13 19:54:51",
    "customer": {"id": 1, "email": "berkaysaman29@gmail.com",
                 "name": "Mehmet Berkay Şaman",
                 "group": {"id": 2, "code": "general", "name": "Genel"}},
    "addresses": [
        {"id": 165, "addressType": "order_billing", "city": "Ankara", "state": "06"},
        {"id": 164, "addressType": "order_shipping", "city": "Selçuklu", "state": "42"},
    ],
    "items": [{"id": 21, "sku": "123456789123456789", "type": "simple",
               "name": "TEST ÜRÜNÜDÜR", "productId": 1428, "qtyOrdered": 2,
               "price": 2, "total": 4, "baseTotal": 4, "taxAmount": 0,
               "taxPercent": 0, "discountAmount": 0}],
}

#: `GET /api/admin/catalog/products` — `categories` NULL, kategori düz alanda.
LIVE_PRODUCT: dict[str, Any] = {
    "id": 1427, "sku": "SET-1", "name": "Set", "type": "simple", "status": 1,
    "price": "2780.0000", "quantity": 20, "categoryId": 12, "categoryName": "Setler",
    "channel": "default", "locale": "tr", "categories": None, "attributes": None,
}

#: `GET /api/admin/catalog/categories/tree` — İÇ İÇE, tepede tek "Kök".
LIVE_CATEGORY_TREE: dict[str, Any] = {"items": [{
    "id": 1, "name": "Kök", "slug": "root", "parentId": None, "children": [
        {"id": 27, "name": "Sınava Göre", "parentId": 1, "children": [
            {"id": 2, "name": "TYT", "parentId": 27, "children": [
                {"id": 3, "name": "Türkçe", "parentId": 2, "children": []},
                {"id": 4, "name": "Matematik", "parentId": 2, "children": []},
            ]},
        ]},
    ],
}]}

#: `GET /api/admin/customers` — grup `group.name` altında.
LIVE_CUSTOMER: dict[str, Any] = {
    "id": 1, "firstName": "Mehmet Berkay", "lastName": "Şaman",
    "name": "Mehmet Berkay Şaman", "email": "TuncerDjdhjd@gmail.com",
    "channelId": 1, "status": 1,
    "group": {"id": 2, "code": "general", "name": "Genel"},
}

#: `GET /api/admin/invoices` — sayısal `orderId` YOK, sipariş NUMARASI var.
LIVE_INVOICE: dict[str, Any] = {
    "id": 19, "incrementId": "19", "orderIncrementId": "20", "state": "paid",
    "subTotal": 4, "grandTotal": 4, "taxAmount": 0,
    "createdAt": "2026-08-13 19:54:55",
}

#: `GET /api/admin/transactions` — `type` teknik kod, okunur ad `paymentTitle`.
LIVE_TRANSACTION: dict[str, Any] = {
    "id": 18, "transactionId": "776806", "invoiceId": 19, "orderId": 20,
    "amount": 4, "status": "success", "type": "kuveytturk",
    "paymentMethod": "kuveytturk", "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "createdAt": "2026-08-13 19:54:55",
}


def _params(**extra: Any) -> dict[str, Any]:
    base = {"start": "2026-08-01", "end": "2026-08-31", "prevStart": "", "prevEnd": "",
            "granularity": "day", "compare": "", "channel": "", "customerGroup": "",
            "category": "", "carrier": "", "churnDays": 180, "stockoutDays": 30,
            "deadStockDays": 90}
    base.update(extra)
    return base


# ============================================== camelCase ve para dönüşümü

def test_canli_liste_satiri_camelcase_alanlari_okunur() -> None:
    """snake_case bekleyen kod hiçbir şey bulmaz ve İSTİSNA DA ATMAZ."""
    row = an.order_row(LIVE_ORDER_LIST)
    assert row["grandTotal"] == 400            # ondalık 4 → 400 kuruş
    assert row["day"] == "2026-08-13"
    assert row["hour"] == 19
    assert row["number"] == "20"
    assert row["channel"] == "Benim Başarı Dünyam"
    assert row["payment"] == "Kredi/Banka Kartı ile Öde"
    assert row["itemCount"] == 1


def test_para_decimal_ile_cevrilir_float_bir_kurus_kaybettiriyordu() -> None:
    """`float("1.005") * 100` = 100.49999999999999 → yarıyı yukarı 100 yapardı."""
    assert an.to_kurus("1.005") == 101
    assert an.to_kurus("0.005") == 1
    assert an.to_kurus("1234.56") == 123_456
    assert an.to_kurus(4) == 400
    assert an.to_kurus("bozuk") == 0


# =========================================================== sığ kalem tuzağı

def test_listedeki_sig_kalem_ciro_hesabina_girmez() -> None:
    """Liste `items` veriyor ama fiyatsız; kabul eden kod SIFIR ciro gösterirdi."""
    order = an.order_row(LIVE_ORDER_LIST)
    assert an.line_rows(LIVE_ORDER_LIST, order) == []
    assert order["hasItems"] is False          # servis siparişi detaydan okumalı


def test_detaydaki_kalem_tam_okunur() -> None:
    order = an.order_row(LIVE_ORDER_DETAIL)
    lines = an.line_rows(LIVE_ORDER_DETAIL, order)
    assert len(lines) == 1
    assert lines[0]["productId"] == 1428
    assert lines[0]["qty"] == 2
    assert lines[0]["total"] == 400


# ================================================================ adres/şehir

def test_sehir_addresses_dizisinden_okunur() -> None:
    """`shipping_address` diye bir alan YOK; teslimat adresi fatura adresini yener."""
    assert an.order_row(LIVE_ORDER_DETAIL)["city"] == "Selçuklu"


def test_sig_satirda_sehir_location_ozetinden_gelir() -> None:
    """Liste ucu adres taşımıyor; şehir raporu boş çıkmasın diye `location`."""
    assert an.order_row(LIVE_ORDER_LIST)["city"] == "Selçuklu"


# ============================================================== müşteri grubu

def test_musteri_grubu_customer_group_name_altindan_okunur() -> None:
    assert an.order_row(LIVE_ORDER_DETAIL)["customerGroup"] == "Genel"


def test_liste_satirinda_grup_yoktur_uydurulmaz() -> None:
    """Liste grubu hiç taşımıyor; boş bırakılır ki servis müşteriden eşleştirsin."""
    assert an.order_row(LIVE_ORDER_LIST)["customerGroup"] == ""


def test_musteri_kunyesi_grubu_ve_epostayi_normalize_eder() -> None:
    row = an.customer_row(LIVE_CUSTOMER)
    assert row["group"] == "Genel"
    assert row["email"] == "tuncerdjdhjd@gmail.com"     # eşleşme küçük harfle


def test_liste_satirinin_epostasi_da_kucuk_harfe_iner() -> None:
    """İki taraf farklı harflerle gelirse eşleşme sessizce tutmazdı."""
    row = an.order_row({**LIVE_ORDER_LIST, "customerEmail": "Tuncerdjdhjd@Gmail.com"})
    assert row["customerEmail"] == "tuncerdjdhjd@gmail.com"


# ================================================================== kategori

def test_urun_kategorisi_duz_alandan_okunur() -> None:
    """`categories` canlıda `null`; yalnız ona bakan kod hepsini "Kategorisiz" yapardı."""
    assert an.product_row(LIVE_PRODUCT)["categories"] == ["Setler"]
    assert an.product_row(LIVE_PRODUCT)["stock"] == 20
    assert an.product_row(LIVE_PRODUCT)["price"] == 278_000


def test_kategori_agaci_duzlestirilir_kok_listeye_girmez() -> None:
    names = an.category_names(LIVE_CATEGORY_TREE)
    assert names == ["Matematik", "Sınava Göre", "TYT", "Türkçe"]
    assert "Kök" not in names                  # ürün künyesinde o adla kategori yok


def test_duz_kategori_listesi_de_bosalmaz() -> None:
    """Uç ileride düz liste dönerse koşulsuz kök atlama hepsini yok ederdi."""
    flat = {"items": [{"id": 3, "name": "Türkçe"}, {"id": 4, "name": "Matematik"}]}
    assert an.category_names(flat) == ["Matematik", "Türkçe"]


# ============================================================ diğer uç biçimleri

def test_fatura_siparis_numarasina_duser() -> None:
    row = an.invoice_row(LIVE_INVOICE)
    assert row["orderId"] == 20                # `orderIncrementId`
    assert row["state"] == "paid"
    assert row["grandTotal"] == 400


def test_islem_yontemi_teknik_kod_yerine_okunur_adi_gosterir() -> None:
    row = an.transaction_row(LIVE_TRANSACTION)
    assert row["method"] == "Kredi/Banka Kartı ile Öde"
    assert row["grandTotal"] == 400


# ================================================== "sıfır" ile "bilinmiyor"

def test_sig_satirda_kargo_sifir_degil_bilinmiyor_diye_yazilir() -> None:
    """Liste kargo bedelini taşımıyor; 0'ı "tahsil edilmedi" saymak yanlıştı."""
    data = {"orders": [an.order_row(LIVE_ORDER_LIST)], "shipments": []}
    result = builders.build("shipping_pnl", data, _params())
    assert any("bilinmiyor" in note for note in result["notes"])


def test_detayli_satirda_bilinmiyor_notu_cikmaz() -> None:
    data = {"orders": [an.order_row(LIVE_ORDER_DETAIL)], "shipments": []}
    result = builders.build("shipping_pnl", data, _params())
    assert not any("bilinmiyor" in note for note in result["notes"])
