"""Kargo servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_shipping_backend.service import ShippingService
from store_shipping_fakes import Events, FakeApi, FakeLog, FakeNotifier, FakeStore

SIPARIS = {
    "id": 91, "increment_id": "S-91", "status": "processing", "grand_total": "500.00",
    "total_qty_ordered": 3, "total_qty_invoiced": 3, "total_qty_shipped": 0,
    "customer_full_name": "Ayşe Yılmaz",
    "shipping_address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
    "items": [{"sku": "A", "qty_ordered": 1, "weight": 0.5, "width": 20, "height": 10,
               "length": 15}],
}

GONDERI = {
    "id": 5, "order_id": 91, "order_number": "S-91", "tracking_number": "1234567890",
    "customer_name": "Ayşe Yılmaz", "carrier": "yurtici", "status": "in_transit",
    "desi": 1.2, "weight": 0.4, "price": "45.00", "payer": "sender",
    "created_at": "2026-08-01T10:00:00", "last_movement_at": "2026-08-02T09:00:00",
    "address": {"city": "İstanbul", "district": "Adalar", "phone": "5321234567"},
    "movements": [{"at": "2026-08-02T09:00:00", "status": "in_transit"}],
}

RATES = {
    "free_shipping_threshold": "300.00",
    "cod_fee": "8.00",
    "carriers": [{"code": "yurtici", "tiers": [
        {"min": 0, "max": 2, "price": "45.00"},
        {"min": 2, "max": 0, "price": "65.00"},
    ]}],
}

GEREKCE = "Müşteri talebi üzerine düzeltildi"
UZUN_GEREKCE = "Müşteri adresi düzeltildi, etiket yeniden satın alındı"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             notifier: Any = None, events: Any = None, printer: Any = None,
             **config: Any) -> tuple[ShippingService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = ShippingService(
        api=api, store=store, log=FakeLog(), notifier=notifier, publish=events,
        printer=printer,
        config={"channel": "default", "locale": "tr", "idle_days": 3, **config},
        fallback_dir=Path("/tmp/km-test-kargo"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_gonderi_listesi_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_shipments")
    result = await service.shipments()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_saglanan_yetenek_uzak_sistem_dusse_de_istisna_firlatmaz() -> None:
    # `store.shipment.byOrder` Siparişler ekranında kullanılıyor; buradan
    # sızan bir istisna o ekranı da düşürürdü (K7).
    service, api, _ = _service()
    api.fail.add("bbd_shipments")
    result = await service.by_order(91)
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []


async def test_bolge_tablosu_okunamazsa_ucret_hesabi_devam_eder() -> None:
    class BozukStore(FakeStore):
        async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
            if "_zones" in sql:
                raise RuntimeError("disk yok")
            return await super().fetch_all(sql, params)

    service, api, _ = _service(store=BozukStore())
    api.shipments_payload = {"items": [dict(GONDERI)], "meta": {"total": 1}}
    result = await service.shipments()
    assert result["ok"] is True
    assert result["items"][0]["trackingNo"] == "1234567890"


# ================================================== gerekçe iki kez denetlenir

async def test_kisa_gerekce_ile_gonderi_acilmaz() -> None:
    service, api, _ = _service()
    api.order_by_id = {91: dict(SIPARIS)}
    result = await service.create_shipment(91, carrier="yurtici", packages=1, desi_value=2,
                                           weight=1, payer="sender", cod=0, note="",
                                           reason="kısa", actor="Ali", dry_run=True)
    assert result["ok"] is False
    assert api.used("bbd_create_shipment") == []


async def test_etiket_satin_almada_on_karakterlik_gerekce_yetmez() -> None:
    # Uçtaki şema 20 istiyor; servis de istiyor — istemci şemayı atlatabilir (K9).
    service, api, _ = _service()
    result = await service.purchase(5, offer_id="of-1", reason="On karakter",
                                    actor="Ali", dry_run=True)
    assert result["ok"] is False
    assert "20 karakter" in result["error"]
    assert api.used("bbd_purchase_shipment") == []


async def test_iade_gonderisi_de_uzun_gerekce_ister() -> None:
    # İade etiketi de faturalanır; ucuz bir gerekçeyle açılmamalı.
    service, api, _ = _service()
    result = await service.retour(5, reason="On karakter", actor="Ali", dry_run=True)
    assert result["ok"] is False
    assert api.used("bbd_return_shipment") == []


# ================================================== para harcayan iş — izler

async def test_etiket_satin_alma_denemesi_istek_gitmeden_ize_yazilir() -> None:
    # İstek zaman aşımına uğrarsa uzakta uygulanmış olabilir; "ne yapmaya
    # çalıştık" kaydı yerelde kalmalı.
    events = Events()
    service, _api, store = _service(events=events)
    result = await service.purchase(5, offer_id="of-1", reason=UZUN_GEREKCE, actor="Ali",
                                    dry_run=False)
    assert result["ok"] is True
    assert result["trackingNo"] == "1234567890"
    assert result["fee"] == 3450
    actions = [row["result"] for row in store.audit if row["action"] == "purchase_label"]
    assert actions == ["denendi", "ok"]
    assert "store.shipment.purchased" in events.names()


async def test_satin_alma_patlarsa_hata_da_ize_yazilir() -> None:
    service, api, store = _service()
    api.fail.add("bbd_purchase_shipment")
    result = await service.purchase(5, offer_id="of-1", reason=UZUN_GEREKCE, actor="Ali",
                                    dry_run=False)
    assert result["ok"] is False
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]


async def test_teklif_secilmeden_etiket_alinmaz() -> None:
    service, api, _ = _service()
    result = await service.purchase(5, offer_id="", reason=UZUN_GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_purchase_shipment") == []


async def test_kuru_prova_varsayilan_olarak_gecide_tasinir() -> None:
    service, api, _ = _service()
    await service.purchase(5, offer_id="of-1", reason=UZUN_GEREKCE, actor="Ali")
    assert api.used("bbd_purchase_shipment")[0]["dry_run"] is True


# ============================================================== sihirbaz

async def test_gonderi_taslagi_acmak_etiket_satin_almaz() -> None:
    # İki adım bilerek ayrı: ilki ücretsiz ve düzeltilebilir, ikincisi değil.
    events = Events()
    service, api, _ = _service(events=events)
    api.order_by_id = {91: dict(SIPARIS)}
    result = await service.create_shipment(91, carrier="yurtici", packages=1, desi_value=1.2,
                                           weight=0.4, payer="sender", cod=0, note="",
                                           reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["shipmentId"] == 77
    assert api.used("bbd_purchase_shipment") == []
    assert result["body"]["billedDesi"] == 2
    assert "store.shipment.created" in events.names()


async def test_kargolanmis_siparise_yeniden_gonderi_acilmaz() -> None:
    service, api, _ = _service()
    api.order_by_id = {91: {**SIPARIS, "total_qty_shipped": 3}}
    result = await service.create_shipment(91, carrier="yurtici", packages=1, desi_value=1,
                                           weight=1, payer="sender", cod=0, note="",
                                           reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert "kargolandı" in result["error"]


async def test_olcusuz_gonderi_engellenir_uyari_metniyle() -> None:
    service, api, _ = _service()
    api.order_by_id = {91: dict(SIPARIS)}
    result = await service.create_shipment(91, carrier="yurtici", packages=1, desi_value=0,
                                           weight=0, payer="sender", cod=0, note="",
                                           reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert "Desi ve ağırlık boş" in result["error"]


async def test_teslimat_yapilmayan_bolge_engellemez_ama_uyarir() -> None:
    # Uyarı ile engel ayrı şeylerdir: personel bilerek gönderebilmeli.
    service, api, store = _service()
    api.order_by_id = {91: dict(SIPARIS)}
    store.zones = [{"city": "İstanbul", "district": "Adalar", "zone": "Ada",
                    "surcharge": 2500, "delivers": 0, "note": ""}]
    result = await service.create_shipment(91, carrier="yurtici", packages=1, desi_value=2,
                                           weight=1, payer="sender", cod=0, note="",
                                           reason=GEREKCE, actor="Ali")
    assert result["ok"] is True
    assert any("teslimat yapılmıyor" in line for line in result["warnings"])


# ============================================================== teklif/ücret

async def test_teklifler_ucuzdan_pahaliya_siralanir_ve_kurusa_cevrilir() -> None:
    service, api, _ = _service()
    api.offers_payload = {"items": [
        {"id": "b", "carrier": "aras", "price": "65.00"},
        {"id": "a", "carrier": "yurtici", "price": "45.00"},
    ]}
    result = await service.offers(5)
    assert [item["id"] for item in result["items"]] == ["a", "b"]
    assert result["items"][0]["price"] == 4500


async def test_ucret_dokumu_ucretsiz_kargo_esigini_uygular() -> None:
    service, api, store = _service()
    api.rates_payload = dict(RATES)
    api.order_by_id = {91: dict(SIPARIS)}       # sepet 500 TL ≥ eşik 300 TL
    store.zones = [{"city": "İstanbul", "district": "Adalar", "zone": "Ada",
                    "surcharge": 2500, "delivers": 1, "note": ""}]
    result = await service.quote(order_id=91, carrier="yurtici", desi_value=1.2, weight=0.4,
                                 payer="sender", cod=0)
    assert result["units"] == 2
    assert result["zone"]["surcharge"] == 2500
    assert result["quote"]["free"] is True
    assert result["quote"]["total"] == 0


async def test_siparis_okunamazsa_teklif_yine_de_hesaplanir() -> None:
    # Ücret dökümü sipariş olmadan da anlamlıdır (eşik uygulanmaz, o kadar).
    service, api, _ = _service()
    api.rates_payload = dict(RATES)
    api.fail.add("order")
    result = await service.quote(order_id=91, carrier="yurtici", desi_value=3, weight=0)
    assert result["ok"] is True
    assert result["quote"]["total"] == 6500


# ========================================================== ücretlendirme

async def test_tutarsiz_desi_matrisi_magazaya_yazilmaz() -> None:
    # Açıkta kalan desi aralığı vitrinde "ücret hesaplanamadı"ya dönüşür.
    service, api, _ = _service()
    result = await service.save_rates(
        carriers=[{"code": "yurtici", "tiers": [{"min": 0, "max": 2, "price": 4500},
                                                {"min": 4, "max": 0, "price": 6500}]}],
        free_threshold=30000, cod_fee=800, promise="", reason=GEREKCE, actor="Ali",
        dry_run=False)
    assert result["ok"] is False
    assert any("açıkta" in line for line in result["problems"])
    assert api.used("bbd_update_shipping_rates") == []


async def test_tutarli_matris_ondalik_bicimde_gonderilir() -> None:
    service, api, _ = _service()
    result = await service.save_rates(
        carriers=[{"code": "yurtici", "tiers": [{"min": 0, "max": 2, "price": 4500},
                                                {"min": 2, "max": 0, "price": 6500}]}],
        free_threshold=30000, cod_fee=800, promise="2-3 iş günü", reason=GEREKCE,
        actor="Ali", dry_run=False)
    assert result["ok"] is True
    body = api.used("bbd_update_shipping_rates")[0]["payload"]
    assert body["carriers"][0]["tiers"][0]["price"] == "45.00"
    assert body["free_shipping_threshold"] == "300.00"


async def test_matris_okumasi_kademe_sorunlarini_ekrana_tasir() -> None:
    service, api, _ = _service()
    api.rates_payload = {"carriers": [{"code": "aras", "tiers": [
        {"min": 0, "max": 2, "price": "45.00"}, {"min": 5, "max": 0, "price": "65.00"}]}]}
    result = await service.rates()
    assert result["problems"]


# ================================================================ bölge

async def test_bolge_satiri_silinmez_teslimat_yapilmiyor_isaretlenir() -> None:
    service, _, store = _service()
    await service.save_zone(city="Hakkâri", district="", zone="Uzak", surcharge=4000,
                            delivers=False, note="anlaşma yok", reason=GEREKCE, actor="Ali")
    assert store.zones[0]["delivers"] == 0
    listed = await service.zones()
    assert listed["items"][0]["delivers"] is False
    # Vitrine yansımadığı ekranda söylenir; "kaydettim, vitrin değişti" yanılgısı olmasın.
    assert listed["storeSynced"] is False


async def test_ilsiz_bolge_satiri_reddedilir() -> None:
    service, _, store = _service()
    result = await service.save_zone(city="", district="Adalar", zone="Ada", surcharge=0,
                                     delivers=True, note="", reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert store.zones == []


# ========================================================== kargoya hazır

async def test_kargoya_hazir_sekmesi_yarim_suzmeyi_soyler() -> None:
    # Mağaza "gönderisi olmayan sipariş" süzgeci sunmuyor; kalan adet sayfa
    # içinde kontrol edilir ve `total` ile `shown` AYRI döner.
    service, api, _ = _service()
    api.orders_payload = {"items": [dict(SIPARIS), {**SIPARIS, "id": 92,
                                                    "total_qty_shipped": 3}],
                          "meta": {"total": 2, "currentPage": 1, "perPage": 50, "lastPage": 1}}
    result = await service.ready()
    assert result["total"] == 2
    assert result["shown"] == 1
    assert result["items"][0]["orderId"] == 91
    assert result["blocked"][0]["blocked"] == "Tüm kalemler kargolandı."


async def test_kargoya_hazir_satiri_otomatik_desi_tahminini_tasir() -> None:
    service, api, _ = _service()
    api.orders_payload = {"items": [dict(SIPARIS)], "meta": {"total": 1}}
    result = await service.ready()
    measures = result["items"][0]["measures"]
    assert measures["desi"] == 1.0            # 20×10×15 / 3000
    assert measures["units"] == 1
    assert measures["complete"] is True


# =============================================================== liste

async def test_turetilmis_bulgu_suzgeci_sayfa_icinde_uygulanir_ve_yazilir() -> None:
    service, api, _ = _service()
    api.shipments_payload = {
        "items": [dict(GONDERI), {**GONDERI, "id": 6, "last_movement_at": "2026-08-13T09:00:00",
                                  "created_at": "2026-08-13T08:00:00"}],
        "meta": {"total": 2, "currentPage": 1, "perPage": 50, "lastPage": 1},
    }
    result = await service.shipments(flag="late")
    assert result["total"] == 2
    assert result["shown"] <= 2
    assert result["flag"] == "late"


async def test_suzgecler_gecide_aktarilir() -> None:
    service, api, _ = _service()
    await service.shipments(carrier="Aras", status="in_transit", city="İstanbul",
                            date_from="2026-08-01", payer="receiver", desi_min=2)
    filters = api.args_of("bbd_shipments")[0][0]
    assert filters["carrier"] == "aras"
    assert filters["status"] == "in_transit"
    assert filters["payer"] == "receiver"
    assert filters["desi_from"] == 2
    assert filters["channel"] == "default"


async def test_liste_sunucudan_sayfali_gelir_tam_tarama_yapilmaz() -> None:
    service, api, _ = _service()
    api.shipments_payload = {"items": [dict(GONDERI)],
                             "meta": {"total": 900, "currentPage": 3, "perPage": 50,
                                      "lastPage": 18}}
    result = await service.shipments(page=3)
    assert result["total"] == 900
    assert result["pages"] == 18
    assert api.used("bbd_shipments")[0]["all_pages"] is False


# ============================================================== senkron

async def test_toplu_senkronda_biri_patlarsa_gerisi_devam_eder() -> None:
    class YarimApi(FakeApi):
        async def bbd_sync_shipment(self, shipment_id: int, **kwargs: Any) -> dict[str, Any]:
            if shipment_id == 6:
                raise RuntimeError("taşıyıcı yanıt vermedi")
            return await super().bbd_sync_shipment(shipment_id, **kwargs)

    service, _, _ = _service(api=YarimApi())
    result = await service.sync([5, 6, 7], reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["done"] == 2
    assert result["failed"] == [6]
    assert result["ok"] is False


async def test_toplu_senkron_ust_sinira_takilir() -> None:
    service, api, _ = _service(label_batch_limit=2)
    result = await service.sync([1, 2, 3], reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("bbd_sync_shipment") == []


# ============================================================== etiket

async def test_etiket_alinamayan_gonderi_sessizce_atlanmaz() -> None:
    # Eksik etiketi atlayıp "hazır" demek, kargoya etiketsiz paket çıkarır.
    service, api, _ = _service()
    api.label_bytes = {}
    result = await service.build_report("labels", {"shipmentIds": [5, 6]})
    assert result["ok"] is False
    assert "satın alındıktan sonra" in result["error"]


async def test_etiket_secilmeden_sayfa_uretilmez() -> None:
    service, _, _ = _service()
    result = await service.build_report("labels", {"shipmentIds": []})
    assert result["ok"] is False


async def test_etiket_toplu_isi_ust_sinira_takilir() -> None:
    service, api, _ = _service(label_batch_limit=1)
    result = await service.build_report("labels", {"shipmentIds": [5, 6]})
    assert result["ok"] is False
    assert "en çok 1 etiket" in result["error"]
    assert api.used("bbd_shipment_label") == []


async def test_bilinmeyen_belge_turu_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.build_report("fatura", {})
    assert result["ok"] is False


# ============================================================ manifesto

async def test_manifesto_uretilince_hangi_gonderiler_girdigi_kaydedilir() -> None:
    service, api, store = _service()
    api.shipment_by_id = {5: dict(GONDERI)}
    result = await service.build_report("manifest", {"shipmentIds": [5], "driver": "Veli Şoför"})
    assert result["ok"] is True
    assert result["count"] == 1
    assert store.manifests[0]["count"] == 1
    assert json.loads(store.manifests[0]["shipments"]) == ["1234567890"]
    Path(result["path"]).unlink(missing_ok=True)


async def test_okunamayan_gonderi_manifestoya_girmez_ama_sayilir() -> None:
    service, api, _ = _service()
    api.shipment_by_id = {5: dict(GONDERI)}
    result = await service.build_report("manifest", {"shipmentIds": [5, 6]})
    assert result["ok"] is True
    assert result["missing"] == [6]
    Path(result["path"]).unlink(missing_ok=True)


# ============================================================== yazdırma

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.printed: list[Path] = []

        async def print_file(self, path: Path, **kwargs: Any) -> dict[str, Any]:
            self.printed.append(path)
            return {"printer": "HP"}

    printer = FakePrinter()
    service, _, _ = _service(printer=printer)
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]
    assert printer.printed == []


async def test_yazici_yoksa_uc_anlasilir_hata_doner() -> None:
    service, _, _ = _service()
    result = await service.print_report("/tmp/km-test-kargo/x.pdf")
    assert result["ok"] is False
    assert "Yazıcı yeteneği" in result["error"]


# ============================================================== bildirim

async def test_bildirim_yetenegi_yoksa_uc_sessizce_basari_donmez() -> None:
    service, _, _ = _service()
    result = await service.notify_customer(5, template="shipment_status", reason=GEREKCE,
                                           actor="Ali")
    assert result["ok"] is False
    assert "store_notifications" in result["error"]


async def test_bildirim_yetenegi_varsa_takip_no_ile_gonderilir() -> None:
    notifier = FakeNotifier()
    service, api, _ = _service(notifier=notifier)
    api.shipment_by_id = {5: dict(GONDERI)}
    result = await service.notify_customer(5, template="shipment_status", reason=GEREKCE,
                                           actor="Ali", dry_run=True)
    assert result["ok"] is True
    assert notifier.sent[0]["data"]["trackingNo"] == "1234567890"
    assert notifier.sent[0]["to"] == "5321234567"


# ============================================================== tercihler

async def test_bilinmeyen_etiket_bicimi_tercihe_yazilmaz() -> None:
    service, _, store = _service()
    result = await service.save_settings(label_format="a3-9up", reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert store.prefs == {}


async def test_tercih_yazilinca_ayarlar_yeni_degeri_doner() -> None:
    service, _, _ = _service()
    await service.save_settings(label_format="a4-4up", default_carrier="Aras",
                                idle_days=5, reason=GEREKCE, actor="Ali")
    settings = await service.settings()
    assert settings["labelFormat"] == "a4-4up"
    assert settings["defaultCarrier"] == "aras"
    assert settings["idleDays"] == 5
