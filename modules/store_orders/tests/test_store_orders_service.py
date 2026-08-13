"""Siparişler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_orders_backend.service import OrdersService
from store_orders_fakes import FakeApi, FakeLog, FakeStore

#: `invoices`/`shipments` BOŞ DA OLSA burada: detay ucu onları her zaman
#: gönderiyor, liste ucu hiç göndermiyor. `has_detail()` ayrımı buna dayanır.
SIPARIS: dict[str, Any] = {
    "id": 12, "increment_id": "1000012", "status": "processing",
    "created_at": "2026-08-10T09:30:00", "customer_first_name": "Ayşe",
    "customer_last_name": "Yılmaz", "customer_email": "ayse@ornek.com",
    "grand_total": "1250.00", "sub_total": "1000.00", "shipping_amount": "50.00",
    "discount_amount": "0.00", "tax_amount": "200.00", "grand_total_invoiced": "1250.00",
    "grand_total_refunded": "0.00", "total_qty_ordered": 3, "channel_name": "Varsayılan",
    "items": [{"id": 5, "sku": "KLM-1", "name": "Kalem", "qty_ordered": 3, "qty_invoiced": 3,
               "qty_shipped": 0, "price": "333.33", "total": "1000.00"}],
    "invoices": [], "shipments": [], "refunds": [], "comments": [],
    "shipping_address": {"first_name": "Ayşe", "city": "Ankara", "phone": "5321234567"},
}

#: Henüz faturalanmamış sipariş — toplu fatura önizlemesinin gerçek girdisi.
ODENMEMIS: dict[str, Any] = {
    **SIPARIS, "grand_total_invoiced": "0.00",
    "items": [{**SIPARIS["items"][0], "qty_invoiced": 0}],
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[OrdersService, FakeApi, FakeStore]:
    api = api or FakeApi({12: dict(SIPARIS)})
    store = store or FakeStore()
    service = OrdersService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "page_size": 50, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("orders")
    result = await service.orders()
    assert result["ok"] is True              # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_kunye_parcasi_patlarsa_gerisi_yine_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("transactions")
    result = await service.card(12)
    assert result["ok"] is True
    assert result["order"]["orderNo"] == "#1000012"
    assert result["transactions"] == []
    assert any("ödeme işlemleri" in item for item in result["warnings"])


async def test_iade_talebi_ucu_yoksa_sekme_kapanir_ekran_calisir() -> None:
    # BBD'ye özel RMA ucu henüz yayında değilse künye yine dolar; ekran o
    # bölümü "yayında değil" diye kapatır.
    service, api, _ = _service()
    api.fail.add("bbd_return_requests")
    result = await service.card(12)
    assert result["ok"] is True
    assert result["returnsAvailable"] is False


# ================================================== iki okuma kipi (TUZAK 5)

async def test_basit_suzgecte_liste_sunucudan_sayfali_gelir() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(SIPARIS)],
                        "meta": {"total": 11, "currentPage": 1, "perPage": 50, "lastPage": 1}}
    result = await service.orders(filters={"status": "processing"})
    assert result["scanned"] is False
    assert result["total"] == 11
    assert api.used("orders")[0]["all_pages"] is False


async def test_magazanin_suzemedigi_filtre_taramaya_gecirir() -> None:
    service, api, _ = _service()
    result = await service.orders(filters={"carrier": "Aras"})
    assert result["scanned"] is True
    assert api.used("orders")[0]["all_pages"] is True
    assert result["items"] == []             # taşıyıcısı olmayan sipariş elenir


async def test_kanal_kodu_uca_hic_gonderilmez_yerelde_suzulur() -> None:
    # CANLIDA DOĞRULANDI: `/orders?channel=` kanal KİMLİĞİ bekliyor.
    # `channel=default` sıfır kayıt döndürüyor ve Laravel hata da vermiyor —
    # kanal kodunu göndermek listeyi sessizce boşaltırdı.
    service, api, _ = _service()
    uyan = await service.orders(filters={"channel": "Varsayılan"})
    assert "channel" not in api.calls[0][1][0]
    assert len(uyan["items"]) == 1

    api.calls.clear()
    uymayan = await service.orders(filters={"channel": "Mobil"})
    assert uymayan["items"] == []


async def test_kanal_kimligi_ayarliysa_tamsayi_olarak_gider() -> None:
    service, api, _ = _service(channel_id=1)
    await service.orders(filters={"status": "processing"})
    assert api.calls[0][1][0]["channel"] == 1


async def test_taramada_magaza_suzgecleri_yine_gonderilir() -> None:
    service, api, _ = _service()
    await service.orders(filters={"chip": "pending", "status": "pending"})
    sent = api.calls[0][1][0]
    assert "channel" not in sent
    assert sent["status"] == "pending"


async def test_tarama_kipinde_sayfalama_yerel_yapilir() -> None:
    kayitlar = {index: {**SIPARIS, "id": index, "increment_id": f"10000{index}"}
                for index in range(1, 8)}
    service, _, _ = _service(FakeApi(kayitlar))
    result = await service.orders(filters={"chip": "processing"}, page=2, size=3)
    assert result["total"] == 7
    assert result["pages"] == 3
    assert len(result["items"]) == 3


async def test_ozet_cip_sayaclarini_cipten_bagimsiz_hesaplar() -> None:
    # Bir çipe basınca diğer çiplerin sayacı sıfırlanırsa kullanıcı seçimini
    # kaldırmadan başka çipe geçemez.
    kayitlar = {1: {**SIPARIS, "id": 1}, 2: {**SIPARIS, "id": 2, "status": "canceled"}}
    service, _, _ = _service(FakeApi(kayitlar))
    result = await service.overview(filters={"chip": "canceled"})
    assert result["counts"]["processing"] == 1
    assert result["counts"]["canceled"] == 1
    assert result["summary"]["count"] == 1      # mini toplam ÇİPE göre


async def test_ozet_ciroya_iptalleri_katmaz() -> None:
    kayitlar = {1: {**SIPARIS, "id": 1}, 2: {**SIPARIS, "id": 2, "status": "canceled"}}
    service, _, _ = _service(FakeApi(kayitlar))
    result = await service.overview()
    assert result["summary"]["revenue"] == 125000


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.cancel(12, reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("cancel_order") == []


async def test_iptal_penceresi_backendde_de_uygulanir() -> None:
    service, api, _ = _service(cancel_window_hours=1)
    result = await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali",
                                  dry_run=False)
    assert result["ok"] is False
    assert "pencere" in result["error"]
    assert api.used("cancel_order") == []


async def test_engellenen_iptal_de_denetim_izine_yazilir() -> None:
    service, _, store = _service(cancel_window_hours=1)
    await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali", dry_run=False)
    assert any(row["result"] == "engellendi" for row in store.audit)


async def test_iptal_yazmadan_once_siparisi_taze_okur() -> None:
    service, api, _ = _service()
    await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali", dry_run=False)
    assert api.args_of("order") == [(12,)]
    assert api.used("cancel_order")[0]["dry_run"] is False


async def test_kargolanmis_siparis_iptal_edilmez() -> None:
    kayit = {**SIPARIS, "total_qty_shipped": 3,
             "items": [{**SIPARIS["items"][0], "qty_shipped": 3}]}
    service, api, _ = _service(FakeApi({12: kayit}))
    result = await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali",
                                  dry_run=False)
    assert result["ok"] is False
    assert api.used("cancel_order") == []


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    yayinlar: list[tuple[str, dict[str, Any]]] = []

    async def publish(name: str, payload: dict[str, Any]) -> None:
        yayinlar.append((name, payload))

    api = FakeApi({12: dict(SIPARIS)})
    service = OrdersService(api=api, store=FakeStore(), log=FakeLog(),
                            config={"channel": "default"}, publish=publish,
                            fallback_dir=Path("/tmp/km-test-raporlar"))
    await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali", dry_run=True)
    assert yayinlar == []
    await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali", dry_run=False)
    assert yayinlar[0][0] == "store.order.status_changed"
    assert yayinlar[0][1]["to"] == "canceled"


async def test_olay_dinleyicisi_patlarsa_iptal_yine_basarilidir() -> None:
    async def publish(name: str, payload: dict[str, Any]) -> None:
        raise RuntimeError("dinleyici patladı")

    api = FakeApi({12: dict(SIPARIS)})
    service = OrdersService(api=api, store=FakeStore(), log=FakeLog(),
                            config={"channel": "default"}, publish=publish,
                            fallback_dir=Path("/tmp/km-test-raporlar"))
    result = await service.cancel(12, reason="Müşteri vazgeçti bugün", actor="Ali",
                                  dry_run=False)
    assert result["ok"] is True


async def test_musteriye_posta_gonderen_not_varsayilan_degildir() -> None:
    service, api, _ = _service()
    await service.add_comment(12, comment="Kargo gecikti", notify=False,
                              reason="Müşteri bilgilendirildi", actor="Ali", dry_run=False)
    assert api.used("add_order_comment")[0]["notify"] is False


async def test_bos_kalemle_kargoya_verilmez() -> None:
    service, api, _ = _service()
    result = await service.ship(12, items={}, carrier="Aras", track="X", source_id=1,
                                reason="Kargoya verildi bugün", actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("create_shipment") == []


async def test_fatura_bos_kalemle_tamamini_faturalar() -> None:
    service, api, _ = _service()
    result = await service.invoice(12, items={}, reason="Tahsilat tamamlandı", actor="Ali",
                                   dry_run=False)
    assert result["ok"] is True
    assert result["partial"] is False
    assert api.used("create_invoice")[0]["items"] is None


# ============================================================ toplu işlem

async def test_toplu_islem_onizlemesiz_uygulanmaz() -> None:
    service, _, _ = _service()
    result = await service.batch_apply(token="olmayan-jeton", reason="Toplu fatura kesiliyor",
                                       actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "önizlemeyi yeniden alın" in result["error"]


async def test_onizleme_jeton_uretir_ve_atlananlari_gosterir() -> None:
    kayitlar = {12: dict(ODENMEMIS), 13: {**ODENMEMIS, "id": 13, "status": "canceled"}}
    service, _, store = _service(FakeApi(kayitlar))
    preview = await service.batch_preview(kind="invoice", order_ids=[12, 13])
    assert preview["ok"] is True
    assert preview["summary"] == {"total": 2, "ready": 1, "skipped": 1, "amount": 125000}
    assert store.batch[preview["token"]]["status"] == "preview"


async def test_onizleme_okunamayan_siparisi_gizlemez() -> None:
    service, _, _ = _service()
    preview = await service.batch_preview(kind="invoice", order_ids=[12, 77])
    assert preview["missing"] == [77]
    assert preview["summary"]["total"] == 1


async def test_ayni_onizleme_iki_kez_uygulanmaz() -> None:
    service, _, _ = _service()
    preview = await service.batch_preview(kind="ship", order_ids=[12])
    first = await service.batch_apply(token=preview["token"], reason="Toplu kargoya veriliyor",
                                      actor="Ali", dry_run=False)
    assert first["ok"] is True
    second = await service.batch_apply(token=preview["token"], reason="Toplu kargoya veriliyor",
                                       actor="Ali", dry_run=False)
    assert second["ok"] is False
    assert "zaten uygulandı" in second["error"]


async def test_uygulama_onizlenen_listeyi_kullanir_yeniden_hesaplamaz() -> None:
    kayitlar = {12: dict(ODENMEMIS), 13: {**ODENMEMIS, "id": 13, "status": "canceled"}}
    service, api, store = _service(FakeApi(kayitlar))
    preview = await service.batch_preview(kind="invoice", order_ids=[12, 13])
    satirlar = json.loads(store.batch[preview["token"]]["rows"])
    assert [row["skipped"] for row in satirlar] == [False, True]
    await service.batch_apply(token=preview["token"], reason="Toplu fatura kesiliyor",
                              actor="Ali", dry_run=False)
    # İptal edilmiş sipariş uca HİÇ gitmez.
    assert [args[0] for args in api.args_of("create_invoice")] == [12]


async def test_toplu_kargo_yalnizca_faturalanmis_kalemi_gonderir() -> None:
    service, api, _ = _service()
    preview = await service.batch_preview(kind="ship", order_ids=[12])
    await service.batch_apply(token=preview["token"], reason="Toplu kargoya veriliyor",
                              actor="Ali", dry_run=False)
    payload = api.used("create_shipment")[0]["payload"]
    assert payload["shipment"]["items"] == {"5": 3}


async def test_cok_buyuk_secim_onizlemeye_bile_girmez() -> None:
    service, _, _ = _service()
    result = await service.batch_preview(kind="ship", order_ids=list(range(1, 400)))
    assert result["ok"] is False
    assert "200" in result["error"]


# ================================================================= etiket

async def test_kargo_kaydi_olmayan_siparisin_etiketi_sessizce_atlanmaz() -> None:
    service, _, _ = _service()
    result = await service.labels([12])
    assert result["files"][0]["error"] != ""
    assert result["ok"] is False


# ================================================================ ayarlar

async def test_ayarlar_yalnizca_yerel_yazilir_magazaya_gitmez() -> None:
    service, api, store = _service()
    result = await service.save_settings(status_names={"processing": "Paketleniyor"},
                                         order_no_format="BBD-{no}", cancel_window_hours=48,
                                         late_days=5, reason="Ekran adlandırması güncellendi",
                                         actor="Ali")
    assert result["ok"] is True
    assert store.prefs["order_no_format"] == "BBD-{no}"
    assert store.prefs["cancel_window_hours"] == "48"
    assert api.used("configuration") == []


async def test_bozuk_siparis_no_bicimi_kaydedilmez() -> None:
    service, _, store = _service()
    result = await service.save_settings(order_no_format="sabit",
                                         reason="Biçim değiştiriliyor", actor="Ali")
    assert result["ok"] is False
    assert "order_no_format" not in store.prefs


async def test_kaydedilen_durum_adi_listede_kullanilir() -> None:
    service, api, _ = _service()
    await service.save_settings(status_names={"processing": "Paketleniyor"},
                                reason="Ekran adlandırması güncellendi", actor="Ali")
    api.list_payload = {"items": [dict(SIPARIS)], "meta": {}}
    liste = await service.orders(filters={"status": "processing"})
    assert liste["items"][0]["statusLabel"] == "Paketleniyor"


async def test_magaza_siparis_ayarlari_salt_okunur_doner() -> None:
    service, api, _ = _service()
    api.config_payload = {"values": {"sales.order_settings.minimum_order.enable": 1}}
    result = await service.settings()
    assert result["storeAvailable"] is True
    assert result["store"][0]["key"] == "sales.order_settings.minimum_order.enable"


async def test_magaza_ayari_okunamazsa_yerel_tercihler_yine_doner() -> None:
    service, api, _ = _service()
    api.fail.add("configuration")
    result = await service.settings()
    assert result["storeAvailable"] is False
    assert result["local"]["orderNoFormat"] == "#{no}"


# ============================================================== denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.invoice(12, items=None, reason="Tahsilat tamamlandı", actor="Ayşe Yılmaz",
                          dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "invoice"]
    assert kayitlar[-1]["reason"] == "Tahsilat tamamlandı"
    assert kayitlar[-1]["actor"] == "Ayşe Yılmaz"
    assert kayitlar[-1]["result"] == "ok"


async def test_yazma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store = _service()
    api.fail.add("create_invoice")
    await service.invoice(12, items=None, reason="Tahsilat tamamlandı", actor="Ali",
                          dry_run=False)
    assert any(row["result"] == "hata" for row in store.audit)


async def test_denetim_izi_siparise_gore_suzulur() -> None:
    service, _, _ = _service()
    await service.invoice(12, items=None, reason="Tahsilat tamamlandı", actor="Ali",
                          dry_run=False)
    result = await service.audit(order_id=12)
    assert result["items"]
    assert all(row["orderId"] == 12 for row in result["items"])


# =========================================================== yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_bastirilmaz() -> None:
    # Serbest yol kabul etmek `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    service, _, _ = _service()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False


async def test_yazici_yoksa_ekran_bunu_soyler() -> None:
    service, _, _ = _service()
    status = await service.printer_status()
    assert status["ready"] is False
    assert "Yazıcı" in status["error"]


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.build_report("uydurma", {})
    assert result["ok"] is False
