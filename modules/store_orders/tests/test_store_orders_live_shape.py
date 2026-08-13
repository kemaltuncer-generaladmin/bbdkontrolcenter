"""CANLI YANIT BİÇİMİ — bbdstore.com.tr'den SALT OKUNARAK alınmış gövdeler.

NEDEN AYRI DOSYA. Diğer testler modülün kendi uydurduğu snake_case gövdeye
karşı geçiyordu; mağaza camelCase döndürüyor. Kendi uydurduğu veriye karşı
geçen test, hiçbir şey kanıtlamaz. Buradaki sözlükler
`GET /api/admin/orders`, `GET /api/admin/orders/19` ve
`GET /api/admin/orders/12` yanıtlarından KISALTILARAK alınmıştır; alan adları
ve değer tipleri (para ONDALIK sayı, tarih boşluk ayraçlı ve saat dilimsiz)
olduğu gibidir.

Bu dosyanın tek işi: mağaza gövdesi değişirse ya da biri alan adlarını
"düzeltirse" ekranın sessizce boşalmasını engellemek.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from store_orders_backend import orders as ord_
from store_orders_backend.service import OrdersService
from store_orders_fakes import FakeApi, FakeLog, FakeStore

#: `GET /api/admin/orders` satırı — SIĞ. Fatura, gönderi, not, ara toplam,
#: KDV ve faturalanan tutar burada YOKTUR.
LISTE_SATIRI: dict[str, Any] = {
    "id": 19, "incrementId": "19", "status": "processing", "statusLabel": "Processing",
    "channelId": 1, "channelName": "Benim Başarı Dünyam", "isGuest": False,
    "customerId": 1, "customerEmail": "berkaysaman29@gmail.com",
    "customerName": "Mehmet Berkay Şaman", "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "couponCode": None, "totalItemCount": 1, "totalQtyOrdered": 1,
    "orderCurrencyCode": "TRY", "grandTotal": 2, "baseGrandTotal": 2,
    "formattedGrandTotal": "₺2,00", "location": "Selçuklu, 42, TR",
    "createdAt": "2026-08-13 18:27:17", "updatedAt": "2026-08-13 18:27:20",
    "items": [{"id": 20, "sku": "123456789123456789", "name": "TEST ÜRÜNÜDÜR",
               "qtyOrdered": 1}],
}

#: `GET /api/admin/orders/19` — DETAY. Adresler `addresses[]` dizisindedir,
#: müşteri grubu `customer.group.name` altındadır, ödeme yöntemi düz alandır.
DETAY: dict[str, Any] = {
    "id": 19, "incrementId": "19", "status": "processing",
    "channelName": "Benim Başarı Dünyam", "isGuest": False,
    "customerEmail": "berkaysaman29@gmail.com", "customerFirstName": "Mehmet Berkay",
    "customerLastName": "Şaman", "shippingTitle": "Hepsijet - Hepsijet",
    "paymentMethod": "kuveytturk", "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "couponCode": None, "totalItemCount": 1, "totalQtyOrdered": 1,
    "grandTotal": 2, "grandTotalInvoiced": 2, "grandTotalRefunded": 0,
    "subTotal": 2, "taxAmount": 0, "discountAmount": 0, "shippingAmount": 0,
    "createdAt": "2026-08-13 18:27:17", "updatedAt": "2026-08-13 18:27:20",
    "customer": {"id": 1, "email": "berkaysaman29@gmail.com", "phone": "05337695687",
                 "group": {"id": 2, "code": "general", "name": "Genel"}},
    "addresses": [
        {"id": 161, "addressType": "order_billing", "firstName": "Berkay",
         "lastName": "Şamanovski", "companyName": "", "address": "Kosova Mh Çöğün sokak 17/11",
         "city": "Selçuklu", "state": "42", "country": "TR", "postcode": "",
         "email": "berkaysaman29@gmail.com", "phone": "5078554233"},
        {"id": 160, "addressType": "order_shipping", "firstName": "Berkay",
         "lastName": "Şamanovski", "companyName": "", "address": "Kosova Mh Çöğün sokak 17/11",
         "city": "Selçuklu", "state": "42", "country": "TR", "postcode": "",
         "email": "berkaysaman29@gmail.com", "phone": "5078554233"},
    ],
    "items": [{"id": 20, "sku": "123456789123456789", "type": "simple",
               "name": "TEST ÜRÜNÜDÜR", "productId": 1428, "qtyOrdered": 1,
               "qtyShipped": 0, "qtyInvoiced": 1, "qtyCanceled": 0, "qtyRefunded": 0,
               "price": 2, "total": 2, "baseTotal": 2, "taxAmount": 0,
               "discountAmount": 0, "createdAt": "2026-08-13 18:27:17"}],
    "invoices": [{"id": 18, "incrementId": "18", "state": "paid", "totalQty": 1,
                  "subTotal": 2, "grandTotal": 2, "taxAmount": 0,
                  "createdAt": "2026-08-13 18:27:20"}],
    "shipments": [], "refunds": [], "comments": [],
}

#: `GET /api/admin/orders/12` gönderi bölümü — kargolanmış sipariş.
KARGOLU: dict[str, Any] = {
    **DETAY, "id": 12, "incrementId": "12", "status": "completed",
    "items": [{**DETAY["items"][0], "id": 13, "qtyOrdered": 2, "qtyInvoiced": 2,
               "qtyShipped": 2}],
    "totalQtyOrdered": 2,
    "shipments": [{"id": 8, "status": None, "totalQty": 2, "totalWeight": 0,
                   "carrierCode": None, "carrierTitle": "R", "trackNumber": "R",
                   "emailSent": True, "inventorySourceName": "Varsayılan",
                   "createdAt": "2026-08-02 11:38:16"}],
}


def _service(api: FakeApi, **config: Any) -> OrdersService:
    return OrdersService(api=api, store=FakeStore(), log=FakeLog(),
                         config={"page_size": 50, **config},
                         fallback_dir=Path("/tmp/km-test-raporlar"))


# ======================================================= alan adı çözümleme

def test_pick_iki_yazimi_da_cozer() -> None:
    # Uç camelCase döndürüyor, Bagisto'nun sütunları ve belgeleri snake_case.
    # Tek yazıma bağlanmak ekranı sessizce boşaltırdı.
    assert ord_.pick({"grandTotal": 2}, "grand_total") == 2
    assert ord_.pick({"grand_total": 2}, "grand_total") == 2
    assert ord_.pick({"couponCode": None}, "coupon_code", default="") == ""
    assert ord_.pick(None, "grand_total", default="yok") == "yok"


def test_pick_sirasi_yazima_degil_ada_gore() -> None:
    # Canlı POS işleminde hem `type` ("kuveytturk") hem `paymentTitle`
    # ("Kredi/Banka Kartı ile Öde") var. Önce bütün adları birebir arayan bir
    # `pick`, `type`ı bulup öne geçirir ve ekranda sağlayıcı kodu yazardı.
    islem = {"type": "kuveytturk", "paymentMethod": "kuveytturk",
             "paymentTitle": "Kredi/Banka Kartı ile Öde"}
    assert ord_.pick(islem, "payment_title", "type") == "Kredi/Banka Kartı ile Öde"


def test_canli_detay_satiri_bos_degil_para_dogru_cevrilir() -> None:
    row = ord_.order_row(DETAY, today="2026-08-13")
    assert row["id"] == 19
    assert row["orderNo"] == "#19"
    assert row["customer"] == "Mehmet Berkay Şaman"
    assert row["email"] == "berkaysaman29@gmail.com"
    assert row["channel"] == "Benim Başarı Dünyam"
    assert row["createdDay"] == "2026-08-13"
    # Bagisto parayı ONDALIK gönderiyor: 2 = ₺2,00 = 200 kuruş.
    assert row["grandTotal"] == 200
    assert row["subTotal"] == 200
    assert row["invoiced"] == 200
    assert row["itemCount"] == 1
    assert row["detailed"] is True


def test_canli_detayda_odeme_yontemi_ve_musteri_grubu_dolu() -> None:
    # Ödeme yöntemi düz `paymentTitle`, müşteri grubu `customer.group.name`.
    # İkisi de eski `payment.method_title` / `customer_group` yolunda YOK.
    row = ord_.order_row(DETAY)
    assert row["paymentMethod"] == "Kredi/Banka Kartı ile Öde"
    assert row["customerGroup"] == "Genel"


def test_adres_addresses_dizisinden_cikarilir() -> None:
    # Canlı detayda `billing_address` / `shipping_address` diye alan YOK.
    billing = ord_.address_view(ord_.address_of(DETAY, "billing"))
    shipping = ord_.address_view(ord_.address_of(DETAY, "shipping"))
    assert billing["name"] == "Berkay Şamanovski"
    assert billing["phone"] == "5078554233"
    assert "Kosova Mh Çöğün sokak 17/11" in shipping["line"]
    assert shipping["city"] == "Selçuklu"
    assert ord_.order_row(DETAY)["city"] == "Selçuklu"


def test_kalem_adetleri_camelcase_alanlardan_okunur() -> None:
    items = ord_.item_rows(DETAY)
    assert items[0]["sku"] == "123456789123456789"
    assert items[0]["quantity"] == 1
    assert items[0]["invoiced"] == 1
    assert items[0]["shipped"] == 0
    assert items[0]["unitPrice"] == 200


def test_gonderi_ve_fatura_satirlari_camelcase_alanlardan_okunur() -> None:
    assert ord_.invoice_rows(DETAY)[0] == {
        "id": 18, "no": "18", "state": "paid", "total": 200,
        "createdAt": "2026-08-13 18:27:20",
    }
    gonderi = ord_.shipment_rows(KARGOLU)[0]
    assert gonderi["carrier"] == "R"
    assert gonderi["track"] == "R"
    assert gonderi["quantity"] == 2


# ==================================================== sığ satır ≠ bilgi yok

def test_liste_satiri_sig_isaretlenir() -> None:
    row = ord_.order_row(LISTE_SATIRI, today="2026-08-13")
    assert row["detailed"] is False
    assert row["grandTotal"] == 200          # listede olan alan yine okunur
    assert row["customer"] == "Mehmet Berkay Şaman"
    assert row["paymentMethod"] == "Kredi/Banka Kartı ile Öde"


def test_faturalanan_tutar_bilinmiyorsa_odenmedi_denmez() -> None:
    # Liste ucu `grandTotalInvoiced` göndermiyor. "Ödenmedi" demek, tahsil
    # edilmiş her siparişi ödenmemiş göstermek olurdu.
    assert ord_.payment_state(LISTE_SATIRI) == "unknown"
    assert ord_.payment_state(DETAY) == "paid"
    assert ord_.order_row(LISTE_SATIRI)["paymentLabel"] == "Bilinmiyor"


def test_sig_satir_kargolanmamis_sayilmaz() -> None:
    # Sığ satırda gönderi kaydı YOKTUR; "kargoya verilmemiş" süzgecine
    # düşürmek, kargolanmış siparişleri de listelemek olurdu.
    sig = ord_.order_row(LISTE_SATIRI, today="2026-08-13")
    assert ord_.matches(sig, {"noShipment": True}) is False
    assert ord_.matches(sig, {"chip": "shipped", "today": "2026-08-13"}) is False
    assert ord_.matches(sig, {"chip": "processing", "today": "2026-08-13"}) is True


def test_sig_satir_gecikmis_sayilmaz() -> None:
    sig = ord_.order_row(LISTE_SATIRI, today="2026-09-20", late_days=3)
    assert sig["late"] is False
    detay = ord_.order_row(DETAY, today="2026-09-20", late_days=3)
    assert detay["late"] is True


def test_sehir_liste_satirinda_location_alanindan_gelir() -> None:
    # Liste ucu adres taşımıyor ama "Selçuklu, 42, TR" özetini veriyor.
    assert ord_.order_row(LISTE_SATIRI)["city"] == "Selçuklu"


def test_detay_isteyen_suzgecler_bildirilir() -> None:
    assert ord_.needs_detail({"noInvoice": True}) is True
    assert ord_.needs_detail({"paymentState": "paid"}) is True
    assert ord_.needs_detail({"chip": "late"}) is True
    assert ord_.needs_detail({"dateField": "shipped"}) is True
    assert ord_.needs_detail({"q": "R"}) is True
    # Bunlar liste satırıyla karşılanır; boşuna istek atılmaz.
    assert ord_.needs_detail({"chip": "today"}) is False
    assert ord_.needs_detail({"city": "Selçuklu"}) is False


# ======================================================= kanal süzgeci (uç)

def test_kanal_store_filters_icine_hic_girmez() -> None:
    # `/orders?channel=` kanal KİMLİĞİ bekliyor; kod göndermek listeyi
    # sessizce boşaltır (canlıda: channel=default → 0 kayıt).
    sent = ord_.store_filters({"channel": "Benim Başarı Dünyam", "status": "processing"})
    assert "channel" not in sent
    assert sent["status"] == "processing"


# ==================================================== iptal penceresi / saat

def test_iptal_penceresi_saat_dilimsiz_damgayi_yerel_sayar() -> None:
    """Mağaza "2026-08-13 18:27:17" gönderiyor: saat dilimi YOK ve bu YEREL saat.

    Damgayı UTC saymak, siparişi mağazanın UTC farkı kadar (canlıda +3 saat)
    GENÇLEŞTİRİR: iki saat önce verilmiş bir sipariş, bir saatlik iptal
    penceresi kapalıyken hâlâ iptal edilebilir görünür — pencerenin tek işi
    buydu. Test makinesi UTC ise fark oluşmaz ve denetim boşa döner; ama
    hiçbir zaman YANLIŞ başarısızlık üretmez.
    """
    def damga(**delta: float) -> str:
        return (datetime.now().astimezone().replace(tzinfo=None)
                - timedelta(**delta)).strftime("%Y-%m-%d %H:%M:%S")

    taze = ord_.order_row({**DETAY, "createdAt": damga(minutes=5)})
    assert ord_.cancel_block(taze, window_hours=1) == ""

    # Pencereden eski ama saat farkından yeni: UTC sayan kod burada ENGEL
    # KOYMAZ ve iptal penceresi hiç çalışmamış olur.
    eski = ord_.order_row({**DETAY, "createdAt": damga(hours=2)})
    assert "İptal penceresi kapandı" in ord_.cancel_block(eski, window_hours=1)


# ============================================================ servis düzeyi

async def test_tarama_gerektiginde_siparisleri_detaylandirir() -> None:
    # Liste ucu sığ; "faturası kesilmemiş" süzgeci detay olmadan karar veremez.
    api = FakeApi({19: dict(DETAY)}, shallow=[dict(LISTE_SATIRI)])
    result = await _service(api).orders(filters={"noInvoice": True})
    assert api.args_of("order") == [(19,)]         # detay çekildi
    assert result["partial"] is False
    assert result["items"] == []                   # sipariş tamamen faturalı


async def test_detay_tavani_asilirsa_ekran_soyler() -> None:
    sig = [{**LISTE_SATIRI, "id": index, "incrementId": str(index)}
           for index in (19, 18, 17)]
    api = FakeApi({19: dict(DETAY)}, shallow=sig)
    result = await _service(api, detail_cap=1).orders(filters={"noShipment": True})
    assert result["partial"] is True
    assert len(api.args_of("order")) == 1          # tavan tuttu


async def test_detay_onbellegi_ayni_siparisi_iki_kez_cekmez() -> None:
    api = FakeApi({19: dict(DETAY)}, shallow=[dict(LISTE_SATIRI)])
    service = _service(api)
    await service.orders(filters={"noShipment": True})
    await service.orders(filters={"noShipment": True})
    assert len(api.args_of("order")) == 1


async def test_detay_okunamazsa_liste_ayakta_kalir() -> None:
    api = FakeApi({19: dict(DETAY)}, shallow=[dict(LISTE_SATIRI)])
    api.fail.add("order")
    result = await _service(api).orders(filters={"noShipment": True})
    assert result["ok"] is True
    assert result["connected"] is True
    assert result["items"] == []                   # karar verilemeyen satır elenir


async def test_magaza_ayarlari_zarfsiz_gelirse_okunamadi_denir() -> None:
    # `/configuration?slug=…` tek elemanlı dizi döndürüyor ve zarfı geçit
    # açıyor. Zarf bozulursa boş tabloyu "bu bölümde ayar yok" diye göstermek,
    # okunamadığını gizlemek olurdu.
    api = FakeApi({19: dict(DETAY)})
    api.config_payload = {}
    result = await _service(api).settings()
    assert result["storeAvailable"] is False
    assert "okunamadı" in result["error"]
    assert result["local"]["orderNoFormat"] == "#{no}"


async def test_magaza_ayarlari_sozluk_gelirse_listelenir() -> None:
    api = FakeApi({19: dict(DETAY)})
    api.config_payload = {"values": {"sales.order_settings.reorder.admin": "1"}}
    result = await _service(api).settings()
    assert result["storeAvailable"] is True
    assert result["store"] == [{"key": "sales.order_settings.reorder.admin", "value": "1"}]
