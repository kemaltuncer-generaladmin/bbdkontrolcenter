"""CANLI MAĞAZANIN yanıt biçimine karşı gerileme testleri.

Buradaki sözlükler `https://bbdstore.com.tr/api/admin/*` uçlarından SALT
OKUMA ile alınmış gerçek yanıtların kısaltılmışıdır (kişisel alanlar
değiştirilmiştir). Amaç tek: modül kendi uydurduğu alan adlarına değil,
mağazanın GERÇEKTEN gönderdiği alanlara baksın.

Her test bir kez CANLIYA KARŞI görülmüş bir arızayı kilitler; ad yerine
sözlüğü değiştiren biri testi kırar ve arıza geri gelmez.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_shipping_backend import analytics, shipping
from store_shipping_backend.service import ShippingService
from store_shipping_fakes import FakeApi, FakeLog, FakeStore

#: `GET /api/admin/orders?per_page=1` — sipariş LİSTESİ satırı. Adres, fatura
#: ve kargo adedi YOKTUR; hepsi camelCase.
CANLI_LISTE_SIPARISI = {
    "id": 19, "incrementId": "19", "status": "processing", "statusLabel": "Processing",
    "channelId": 1, "channelName": "Benim Başarı Dünyam", "isGuest": False,
    "customerId": 1, "customerEmail": "ornek@example.com",
    "customerName": "Mehmet Berkay Şaman",
    "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "totalItemCount": 1, "totalQtyOrdered": 1, "orderCurrencyCode": "TRY",
    "grandTotal": 2, "baseGrandTotal": 2, "formattedGrandTotal": "₺2,00",
    "location": "Selçuklu, 42, TR",
    "createdAt": "2026-08-13 18:27:17", "updatedAt": "2026-08-13 18:27:20",
    "items": [{"id": 20, "sku": "123456789123456789", "name": "TEST ÜRÜNÜDÜR",
               "qtyOrdered": 1}],
}

#: `GET /api/admin/orders/19` — sipariş AYRINTISI. Adresler `addresses`
#: listesinde `addressType` ile ayrılır; kalem adetleri kalemin üzerindedir.
CANLI_AYRINTI_SIPARISI = {
    "id": 19, "incrementId": "19", "status": "processing",
    "customerFirstName": "Mehmet Berkay", "customerLastName": "Şaman",
    "paymentMethod": "kuveytturk", "paymentTitle": "Kredi/Banka Kartı ile Öde",
    "totalItemCount": 1, "totalQtyOrdered": 4,
    "grandTotal": 500, "grandTotalInvoiced": 500, "shippingAmount": 0,
    "createdAt": "2026-08-13 18:27:17",
    "addresses": [
        {"id": 161, "addressType": "order_billing", "firstName": "Fatura",
         "lastName": "Adresi", "address": "Fatura Mh 1/1", "city": "Ankara",
         "state": "06", "country": "TR", "phone": "5070000000"},
        {"id": 160, "addressType": "order_shipping", "firstName": "Berkay",
         "lastName": "Şamanovski", "address": "Kosova Mh Çöğün sokak 17/11",
         "city": "Selçuklu", "state": "42", "country": "TR", "phone": "5078554233"},
    ],
    "items": [{"id": 20, "sku": "A", "name": "TEST", "weight": 0.5, "qtyOrdered": 4,
               "qtyShipped": 1, "qtyInvoiced": 4}],
    "invoices": [{"id": 18, "state": "paid", "totalQty": 4}],
    "shipments": [],
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=Path("/tmp/km-test-kargo"),
    )
    return service, api, store


def _order_filters(api: FakeApi) -> dict[str, Any]:
    return api.args_of("orders")[0][0]


# ================================================== 1 · kanal süzgeci tuzağı

async def test_kanal_suzgeci_koda_degil_kimlige_cevrilerek_gider() -> None:
    # CANLI: `channel=default` → 0 sipariş, `channel=1` → 17 sipariş.
    # Kodu olduğu gibi göndermek "Kargoya hazır" sekmesini boş bırakıyordu.
    service, api, _ = _service()
    api.orders_payload = {"items": [CANLI_LISTE_SIPARISI], "meta": {"total": 1}}
    await service.ready()
    assert _order_filters(api)["channel"] == 1


async def test_kanal_kodu_magazada_yoksa_suzgec_hic_gonderilmez() -> None:
    # Eşleşmeyen bir değer göndermek listeyi tümden boşaltır; süzmemek
    # tek kanallı mağazada zararsızdır.
    service, api, _ = _service(channel="olmayan-kanal")
    api.orders_payload = {"items": [CANLI_LISTE_SIPARISI], "meta": {"total": 1}}
    result = await service.ready()
    assert "channel" not in _order_filters(api)
    assert result["connected"] is True
    assert len(result["items"]) == 1


async def test_kanal_listesi_okunamazsa_siparisler_yine_de_listelenir() -> None:
    service, api, _ = _service()
    api.fail.add("channels")
    api.orders_payload = {"items": [CANLI_LISTE_SIPARISI], "meta": {"total": 1}}
    result = await service.ready()
    assert "channel" not in _order_filters(api)
    assert result["connected"] is True


async def test_kanal_kimligi_bir_kez_cozulur() -> None:
    # Kanal listesi her sayfa çevirmede yeniden istenirse hız kovası boşuna
    # tüketilir; geçit önbelleklese bile çağrı sayılır.
    service, api, _ = _service()
    api.orders_payload = {"items": [], "meta": {}}
    await service.ready()
    await service.ready()
    assert len(api.used("channels")) == 1


# ============================================ 2 · liste ucunun eksik alanları

async def test_canli_liste_bicimindeki_siparis_hazir_sayilir() -> None:
    # Liste ucu faturalanan/kargolanan adedi HİÇ göndermiyor. Bunu "0" sayıp
    # "ödeme beklemede" demek listedeki her siparişi engelliyordu; sekme boş
    # açılıyor ve kimse fark etmiyordu.
    service, api, _ = _service()
    api.orders_payload = {"items": [CANLI_LISTE_SIPARISI], "meta": {"total": 1}}
    result = await service.ready()
    assert result["shown"] == 1
    assert result["blocked"] == []
    assert result["items"][0]["blocked"] == ""


async def test_dogrulanmamis_hazirlik_ekrana_soylenir() -> None:
    service, api, _ = _service()
    api.orders_payload = {"items": [CANLI_LISTE_SIPARISI], "meta": {"total": 1}}
    result = await service.ready()
    assert result["verified"] is False
    assert result["items"][0]["paymentKnown"] is False
    assert result["items"][0]["shipmentKnown"] is False


async def test_canli_liste_satirinda_musteri_ve_tutar_bos_kalmaz() -> None:
    # `customer_full_name` / `grand_total` diye alan YOK; boş satır "—" dolu
    # bir tablo üretiyordu.
    row = shipping.ready_row(CANLI_LISTE_SIPARISI, today="2026-08-14")
    assert row["customer"] == "Mehmet Berkay Şaman"
    assert row["total"] == 200                       # grandTotal: 2 → 200 kuruş
    assert row["orderNumber"] == "19"
    assert row["paymentTitle"] == "Kredi/Banka Kartı ile Öde"
    assert row["createdAt"] == "2026-08-13 18:27:17"
    assert row["waitingDays"] == 1


async def test_liste_ucunda_adres_gelmedigi_soylenir() -> None:
    row = shipping.ready_row(CANLI_LISTE_SIPARISI)
    assert row["addressKnown"] is False
    assert row["city"] == ""


# ================================================ 3 · adres listeden ayıklanır

async def test_teslimat_adresi_addresses_listesinden_secilir() -> None:
    address = shipping.shipping_address(CANLI_AYRINTI_SIPARISI)
    assert address["city"] == "Selçuklu"


async def test_fatura_adresi_teslimat_adresi_yerine_kullanilmaz() -> None:
    # Yanlış adres = paketin yanlış şehre gitmesi. Teslimat adresi yoksa
    # BOŞ dönülür, fatura adresine düşülmez.
    only_billing = {"addresses": [row for row in CANLI_AYRINTI_SIPARISI["addresses"]
                                  if row["addressType"] == "order_billing"]}
    assert shipping.shipping_address(only_billing) == {}


async def test_eski_bicimdeki_shipping_address_alani_da_calisir() -> None:
    assert shipping.shipping_address(
        {"shipping_address": {"city": "İzmir"}})["city"] == "İzmir"


async def test_ilce_yerine_il_kodu_gelse_de_bolge_eslesmesi_yapilir() -> None:
    # Canlıda ilçe `state` alanında (plaka kodu) geliyor; eşleme buna bakar.
    zones = [{"city": "Selçuklu", "district": "42", "zone": "Konya çevre",
              "surcharge": 1500, "delivers": 1, "note": ""}]
    row = shipping.ready_row(CANLI_AYRINTI_SIPARISI, zones=zones)
    assert row["zone"] == "Konya çevre"


# ======================================= 4 · kalem bazlı adet ve ödeme kanıtı

async def test_kismen_kargolanan_adet_kalemlerden_okunur() -> None:
    # Sipariş başlığında `totalQtyShipped` yok; kalan adet ancak kalemin
    # `qtyShipped` alanından çıkar. Okunmazsa 4 kalemin tamamı yeniden
    # kargoya verilirdi.
    state = shipping.ready_state(CANLI_AYRINTI_SIPARISI)
    assert state["shipped"] == 1
    assert state["pending"] == 3
    assert state["shipmentKnown"] is True
    assert state["ready"] is True


async def test_fatura_kaydinin_varligi_odeme_kaniti_sayilir() -> None:
    state = shipping.ready_state(CANLI_AYRINTI_SIPARISI)
    assert state["paymentKnown"] is True
    assert state["blocked"] == ""


async def test_faturasiz_siparis_hala_engellenir() -> None:
    # Kanıt VARSA ve olumsuzsa engel sürer: düzeltme "her şeyi hazır say"
    # demek değildir.
    order = {**CANLI_AYRINTI_SIPARISI, "invoices": [], "grandTotalInvoiced": 0,
             "items": [{"qtyOrdered": 4, "qtyShipped": 0}]}
    state = shipping.ready_state(order)
    assert state["ready"] is False
    assert state["blocked"] == "Ödeme/fatura beklemede."


async def test_tamami_kargolanmis_siparis_listeye_girmez() -> None:
    order = {**CANLI_AYRINTI_SIPARISI,
             "items": [{"qtyOrdered": 4, "qtyShipped": 4, "qtyInvoiced": 4}]}
    state = shipping.ready_state(order)
    assert state["ready"] is False
    assert state["blocked"] == "Tüm kalemler kargolandı."


async def test_kalem_adedi_camelcase_alandan_okunur() -> None:
    # `qty_ordered` yok; okunmazsa adet 1 sayılıp toplam ağırlık dörtte bire
    # düşüyordu.
    measures = shipping.auto_measures(CANLI_AYRINTI_SIPARISI["items"])
    assert measures["pieces"] == 4
    assert measures["weight"] == 2.0


async def test_musteri_adi_ad_ve_soyaddan_kurulur() -> None:
    assert shipping.customer_name(CANLI_AYRINTI_SIPARISI) == "Mehmet Berkay Şaman"


# ================================================== 5 · sihirbaz canlı sipariş

async def test_sihirbaz_canli_siparisin_teslimat_bolgesini_bulur() -> None:
    service, api, store = _service()
    api.order_by_id[19] = CANLI_AYRINTI_SIPARISI
    store.zones.append({"id": 1, "city": "Selçuklu", "district": "42",
                        "zone": "Konya çevre", "surcharge": 2000, "delivers": 0,
                        "note": "", "updated_at": ""})
    result = await service.quote(order_id=19, carrier="yurtici", desi_value=1.0, weight=0.5)
    assert result["zone"]["found"] is True
    assert result["zone"]["surcharge"] == 2000


async def test_ucretsiz_kargo_esigi_canli_tutar_alaniyla_calisir() -> None:
    # Sepet toplamı `grandTotal`; `grand_total` okunduğu için eşik hiç
    # uygulanmıyordu.
    service, api, _ = _service()
    api.order_by_id[19] = CANLI_AYRINTI_SIPARISI          # grandTotal: 500
    api.rates_payload = {"free_shipping_threshold": "300.00", "cod_fee": "0",
                         "carriers": [{"code": "yurtici",
                                       "tiers": [{"min": 0, "max": 0, "price": "45.00"}]}]}
    result = await service.quote(order_id=19, carrier="yurtici", desi_value=1.0, weight=0.5)
    assert result["quote"]["free"] is True
    assert result["quote"]["total"] == 0


async def test_teslimat_yapilmayan_bolge_uyarisi_canli_adresle_cikar() -> None:
    service, api, store = _service()
    api.order_by_id[19] = CANLI_AYRINTI_SIPARISI
    store.zones.append({"id": 1, "city": "Selçuklu", "district": "42", "zone": "Uzak",
                        "surcharge": 0, "delivers": 0, "note": "", "updated_at": ""})
    result = await service.create_shipment(
        19, carrier="yurtici", packages=1, desi_value=2.0, weight=1.0, payer="sender",
        cod=0, note="", reason="Müşteri talebi üzerine gönderi açıldı", actor="Ali",
        dry_run=True)
    assert result["ok"] is True
    assert any("teslimat yapılmıyor" in line for line in result["warnings"])


# ============================================ 6 · kâr/zarar alanı boş kalmasın

async def test_musteriden_tahsil_edilen_kargo_bedeli_satira_tasinir() -> None:
    # `collectedFee` hiç eşlenmediği için "Tahsil edilen" KPI'ı her zaman 0,
    # "Fark" her zaman zarar görünüyordu.
    row = shipping.shipment_row({"id": 1, "carrier": "yurtici", "status": "delivered",
                                 "price": "45.00", "shipping_amount": "60.00",
                                 "payer": "sender"})
    assert row["collectedFee"] == 6000
    summary = analytics.money_summary([row])
    assert summary["collected"] == 6000
    assert summary["missingCollected"] == 0
    assert summary["margin"] == 1500


async def test_tahsilat_bilinmiyorsa_sifir_gibi_gosterilmez() -> None:
    row = shipping.shipment_row({"id": 1, "carrier": "yurtici", "status": "delivered",
                                 "price": "45.00", "payer": "sender"})
    assert row["collectedFee"] is None
    assert analytics.money_summary([row])["missingCollected"] == 1


# ================================================== 7 · K7 — sessiz başarısızlık

async def test_tercih_yazilamazsa_kaydedildi_denmez() -> None:
    service, _, store = _service()

    async def patlat(sql: str, params: tuple[Any, ...] = ()) -> None:
        if "_prefs" in sql:
            raise RuntimeError("disk dolu")

    store.execute = patlat  # type: ignore[method-assign]
    result = await service.save_settings(label_format="a4-4up",
                                         reason="Etiket biçimi değiştirildi",
                                         actor="Ali")
    assert result["ok"] is False
    assert "etiket biçimi" in result["error"]
