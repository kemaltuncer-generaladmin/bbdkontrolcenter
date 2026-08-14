"""Müşteri aşama SMS'inin TETİKLEYİCİ tarafı — ağa çıkmaz, gerçek SMS gitmez.

Bu modül metin yazmaz ve mesaj göndermez; sorumluluğu tek cümledir: "hangi
sipariş hangi aşamaya geçti ve künyesi ne". Testler bunu iki yerden kanıtlar:
`FakeStageNotify.calls` (istek çıktı mı, hangi künyeyle) ve `dryRun` bayrağı
(gerçek gönderim istendi mi).

Bildirimler modülü BURADA IMPORT EDİLMEZ (K3).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_orders_backend import stages
from store_orders_backend.service import OrdersService
from store_orders_backend.tasks import run_stage_sms
from store_orders_fakes import FakeApi, FakeLog, FakeStageNotify, FakeStore

GEREKCE = "Kargo firmasına bugün teslim edildi, gönderi kaydı açılıyor."

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


def _service(*, notify: FakeStageNotify | None = None, api: FakeApi | None = None,
             **config: Any) -> tuple[OrdersService, FakeApi, FakeStageNotify | None]:
    api = api or FakeApi({12: dict(SIPARIS)})
    service = OrdersService(
        api=api, store=FakeStore(), log=FakeLog(), stage_notify=notify,
        config={"channel": "default", "page_size": 50, "stage_sms_dry_run": False,
                **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, notify


# ================================================== saf mantık: künye ve durum

def test_tutar_tl_yazilir_lira_isareti_kullanilmaz() -> None:
    # TUZAK 1: `₺` GSM-7 kümesinde yok; tek karakteri mesajı UCS-2'ye düşürüp
    # 160 karakterlik sınırı 70'e indiriyor ve her siparişte üç kredi yakıyor.
    assert stages.money_sms(124990) == "1.249,90 TL"
    assert "₺" not in stages.money_sms(124990)


def test_teslim_yazimlarinin_hepsi_taninir() -> None:
    # TUZAK 2: taşıyıcı tek yazım kullanmıyor; tek yazıma bağlanan kod teslim
    # edilmiş gönderiyi HİÇ görmez ve müşteri mesajı hiç almaz.
    for word in ("delivered", "DELIVERED", "completed", "Done", "teslim edildi",
                 "delivery-completed"):
        assert stages.is_delivered(word) is True, word


def test_taninmayan_durum_teslim_sayilmaz() -> None:
    # Bilinmeyen kelimeyi teslim kabul etmek, yoldaki bir gönderi için
    # "teslim edildi" yazmaktır ve o mesaj geri alınamaz.
    for word in ("in_transit", "out_for_delivery", "", "uydurma", "returned"):
        assert stages.is_delivered(word) is False, word


def test_takip_numarasi_once_gonderiden_sonra_tasiyicidan_okunur() -> None:
    # TUZAK 3: toplu "kargoya ver" takip numarası üretmez; numara taşıyıcının
    # kendi kaydındadır.
    row = {"id": 12, "orderNo": "#1000012", "customer": "Ayşe", "phone": "5321234567",
           "grandTotal": 125000, "createdDay": "2026-08-10", "carrier": "", "track": ""}
    ship = {"carrier": "Aras Kargo", "track": "735004", "trackUrl": "https://x/735004"}
    kunye = stages.stage_order(row, shipment=ship)
    assert kunye["track"] == "735004"
    assert kunye["trackUrl"] == "https://x/735004"
    assert kunye["carrier"] == "Aras Kargo"
    # Elle verilen değer taşıyıcıyı EZER: personelin girdiği numara doğrudur.
    assert stages.stage_order(row, shipment=ship, track="ELLE")["track"] == "ELLE"


def test_gun_penceresi_eski_siparisi_disarida_birakir() -> None:
    # TUZAK 4: pencere olmasaydı ilk tarama 1.800 müşteriye "siparişiniz
    # alındı" gönderirdi.
    assert stages.within_window("2026-08-14", days=3, today="2026-08-14") is True
    assert stages.within_window("2026-08-12", days=3, today="2026-08-14") is True
    assert stages.within_window("2026-08-01", days=3, today="2026-08-14") is False
    # Tarihi çözülemeyen kayıt TAZE SAYILMAZ: tam olarak yukarıdaki kazayı
    # üretirdi.
    assert stages.within_window("", days=3, today="2026-08-14") is False
    assert stages.within_window("", days=0, today="2026-08-14") is True


def test_iptal_siparise_alindi_mesaji_gitmez() -> None:
    assert stages.placed_block({"status": "canceled"}) != ""
    assert stages.placed_block({"status": "processing"}) == ""


def test_kargoya_verildi_taramayla_tetiklenmez() -> None:
    # İki yerden tetiklemek aynı işi iki kez yapmak olurdu.
    assert stages.sweep_stage_error("shipped") != ""
    assert stages.sweep_stage_error("order_placed") == ""
    assert stages.sweep_stage_error("delivered") == ""


# ============================================ "Kargoya ver" → shipped SMS'i

async def test_gercek_gonderiden_sonra_musteriye_sms_istenir() -> None:
    notify = FakeStageNotify(enabled=("shipped",))
    service, _, _ = _service(notify=notify)
    result = await service.ship(12, items={"5": 3}, carrier="Aras Kargo", track="735004",
                                source_id=1, reason=GEREKCE, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert len(notify.calls) == 1
    call = notify.calls[0]
    assert call["stage"] == "shipped"
    assert call["order"]["orderNo"] == "#1000012"
    assert call["order"]["track"] == "735004"
    assert call["order"]["phone"] == "5321234567"
    assert result["sms"]["sent"] is True


async def test_kuru_provada_musteriye_sms_istenmez() -> None:
    # Mağazada hiçbir şey değişmedi; "kargoya verildi" yazmak yalan olurdu ve
    # tekrar engelinin kaydını da boşa harcardı.
    notify = FakeStageNotify(enabled=("shipped",))
    service, _, _ = _service(notify=notify)
    await service.ship(12, items={"5": 3}, carrier="Aras Kargo", track="735004",
                       source_id=1, reason=GEREKCE, actor="Test", dry_run=True)
    assert notify.calls == []


async def test_asama_kapaliysa_sms_istenmez_ama_kargo_kaydi_acilir() -> None:
    notify = FakeStageNotify(enabled=())
    service, api, _ = _service(notify=notify)
    result = await service.ship(12, items={"5": 3}, carrier="Aras Kargo", track="735004",
                                source_id=1, reason=GEREKCE, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert api.used("create_shipment")               # kargo kaydı yine açıldı
    assert notify.calls == []
    assert "kapalı" in result["sms"]["note"]


async def test_bildirimler_kapaliysa_kargo_calisir_ve_neden_yazilir() -> None:
    service, api, _ = _service(notify=None)
    result = await service.ship(12, items={"5": 3}, carrier="Aras Kargo", track="735004",
                                source_id=1, reason=GEREKCE, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert api.used("create_shipment")
    assert result["sms"]["sent"] is False
    assert result["sms"]["note"]                     # SESSİZ GEÇMEZ


async def test_bildirimler_patlarsa_kargo_kaydi_ayakta_kalir() -> None:
    # K7: gönderi kaydı açılmıştır; mesajın gitmemesi onu geri almaz.
    notify = FakeStageNotify(enabled=("shipped",))
    service, _, _ = _service(notify=notify)
    notify.fail = True
    result = await service.ship(12, items={"5": 3}, carrier="Aras Kargo", track="735004",
                                source_id=1, reason=GEREKCE, actor="Test", dry_run=False)
    assert result["ok"] is True
    assert result["sms"]["sent"] is False


async def test_takip_numarasi_girilmediyse_tasiyici_kaydina_bakilir() -> None:
    notify = FakeStageNotify(enabled=("shipped",))
    api = FakeApi({12: dict(SIPARIS)})
    api.shipment_payload = {"items": [{"id": 9, "order_id": 12, "carrier": "Sürat Kargo",
                                       "tracking_number": "999888",
                                       "tracking_url": "https://s/999888",
                                       "status": "in_transit"}], "meta": {}}
    service, _, _ = _service(notify=notify, api=api)
    await service.ship(12, items={"5": 3}, carrier="", track="", source_id=1,
                       reason=GEREKCE, actor="Test", dry_run=False)
    assert notify.calls[0]["order"]["track"] == "999888"
    assert notify.calls[0]["order"]["trackUrl"] == "https://s/999888"


async def test_toplu_kargoda_siparis_ikinci_kez_okunmaz() -> None:
    # Toplu iş siparişi kalem adetleri için zaten okuyor; SMS künyesi için
    # ikinci bir `GET /orders/{id}` 200'lük bir işte 200 gereksiz istek olurdu.
    notify = FakeStageNotify(enabled=("shipped",))
    service, api, _ = _service(notify=notify)
    preview = await service.batch_preview(kind="ship", order_ids=[12])
    await service.batch_apply(token=preview["token"], reason=GEREKCE, actor="Test",
                              dry_run=True, carrier="Aras Kargo")
    before = len(api.args_of("order"))
    result = await service.batch_apply(token=preview["token"], reason=GEREKCE, actor="Test",
                                       dry_run=False, carrier="Aras Kargo")
    assert result["applied"] == 1
    # Gerçek uygulamada sipariş BİR kez okundu (kalemler için); SMS künyesi
    # aynı okumadan çıktı.
    assert len(api.args_of("order")) - before == 1
    assert len(notify.calls) == 1
    assert result["results"][0]["smsSent"] is True


async def test_toplu_iste_sms_sonucu_satir_satir_gorunur() -> None:
    notify = FakeStageNotify(enabled=("shipped",))
    notify.result = {"ok": True, "sent": False, "result": "no_phone",
                     "note": "Gönderilemedi: Müşterinin cep numarası yok."}
    service, _, _ = _service(notify=notify)
    preview = await service.batch_preview(kind="ship", order_ids=[12])
    await service.batch_apply(token=preview["token"], reason=GEREKCE, actor="Test",
                              dry_run=True, carrier="Aras Kargo")
    result = await service.batch_apply(token=preview["token"], reason=GEREKCE, actor="Test",
                                       dry_run=False, carrier="Aras Kargo")
    row = result["results"][0]
    assert row["ok"] is True                 # kargo kaydı açıldı
    assert row["smsSent"] is False
    assert "numara" in row["smsNote"]        # NEDEN gitmediği aynı tabloda


# ==================================================== tarama tetikleyicileri

async def test_yeni_siparis_taramasi_alindi_mesaji_ister() -> None:
    notify = FakeStageNotify(enabled=("order_placed",))
    api = FakeApi({12: dict(SIPARIS)})
    api.list_payload = {"items": [dict(SIPARIS)], "meta": {"total": 1}}
    service, _, _ = _service(notify=notify, api=api, stage_sms_lookback_days=0)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    assert result["ok"] is True
    assert result["sent"] == 1
    assert notify.calls[0]["stage"] == "order_placed"


async def test_gun_penceresi_disindaki_siparis_taramaya_girmez() -> None:
    notify = FakeStageNotify(enabled=("order_placed",))
    api = FakeApi({12: dict(SIPARIS)})
    api.list_payload = {"items": [{**SIPARIS, "created_at": "2020-01-01T09:00:00"}],
                        "meta": {"total": 1}}
    service, _, _ = _service(notify=notify, api=api, stage_sms_lookback_days=3)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    assert result["considered"] == 0
    assert notify.calls == []


async def test_zaten_haber_verilmis_siparis_yeniden_denenmez() -> None:
    notify = FakeStageNotify(enabled=("order_placed",))
    notify.done_ids["order_placed"] = [12]
    api = FakeApi({12: dict(SIPARIS)})
    api.list_payload = {"items": [dict(SIPARIS)], "meta": {"total": 1}}
    service, _, _ = _service(notify=notify, api=api, stage_sms_lookback_days=0)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    assert result["considered"] == 0
    assert notify.calls == []


async def test_teslim_taramasi_yalniz_teslim_edilmisleri_alir() -> None:
    notify = FakeStageNotify(enabled=("delivered",))
    api = FakeApi({12: dict(SIPARIS)})
    api.shipment_payload = {"items": [
        {"id": 9, "order_id": 12, "status": "delivered", "carrier": "Aras Kargo",
         "tracking_number": "735004", "tracking_url": "https://a/735004"},
        {"id": 10, "order_id": 99, "status": "in_transit"},
    ], "meta": {}}
    service, _, _ = _service(notify=notify, api=api)
    result = await service.stage_sweep(stage="delivered", actor="Test", dry_run=False)
    assert result["sent"] == 1
    assert [call["order"]["orderId"] for call in notify.calls] == [12]


async def test_teslim_taramasinda_durum_suzgeci_magazaya_gonderilmez() -> None:
    # Laravel tanımadığı parametreyi sessizce yok sayar; tanıyıp da başka bir
    # yazım beklerse listeyi sessizce BOŞALTIR ve hiçbir müşteri teslim SMS'i
    # almaz — hata da vermez. Ayıklama BURADA yapılır.
    notify = FakeStageNotify(enabled=("delivered",))
    api = FakeApi({12: dict(SIPARIS)})
    api.shipment_payload = {"items": [{"id": 9, "order_id": 12, "status": "delivered"}],
                            "meta": {}}
    service, _, _ = _service(notify=notify, api=api)
    await service.stage_sweep(stage="delivered", actor="Test", dry_run=False)
    assert api.args_of("bbd_shipments")[0][0] == {}


async def test_kargo_asamasi_taramayla_calistirilamaz() -> None:
    notify = FakeStageNotify(enabled=("shipped",))
    service, _, _ = _service(notify=notify)
    result = await service.stage_sweep(stage="shipped", actor="Test", dry_run=False)
    assert result["ok"] is False
    assert notify.calls == []


async def test_kapali_asamada_tarama_magazaya_hic_gitmez() -> None:
    notify = FakeStageNotify(enabled=())
    service, api, _ = _service(notify=notify)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    assert result["skipped"] is True
    assert api.used("orders") == []          # boşuna istek atılmadı
    assert "kapalı" in result["note"]


async def test_bildirimler_patlarsa_tarama_ekrani_ayakta_kalir() -> None:
    notify = FakeStageNotify(enabled=("order_placed",))
    notify.fail = True
    service, _, _ = _service(notify=notify)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    assert result["ok"] is True
    assert result["skipped"] is True


async def test_gonderi_listesi_okunamazsa_tarama_sessizce_bitmez() -> None:
    notify = FakeStageNotify(enabled=("delivered",))
    api = FakeApi({12: dict(SIPARIS)})
    api.fail.add("bbd_shipments")
    service, _, _ = _service(notify=notify, api=api)
    result = await service.stage_sweep(stage="delivered", actor="Test", dry_run=False)
    assert result["ok"] is True
    assert "okunamadı" in result["note"]


# ================================================== üçüncü fren + zamanlanmış iş

async def test_modul_freni_acikken_tarama_gercek_gonderim_istemez() -> None:
    notify = FakeStageNotify(enabled=("order_placed",))
    api = FakeApi({12: dict(SIPARIS)})
    api.list_payload = {"items": [dict(SIPARIS)], "meta": {"total": 1}}
    service, _, _ = _service(notify=notify, api=api, stage_sms_dry_run=True,
                             stage_sms_lookback_days=0)
    result = await service.stage_sweep(stage="order_placed", actor="Test", dry_run=False)
    # İSTEK ÇIKTI ama KURU PROVA olarak: fren tetikleyicide de var.
    assert notify.calls[0]["dryRun"] is True
    assert result["sent"] == 0


async def test_zamanlanmis_is_iki_asamayi_kosar_ve_patlamaz() -> None:
    notify = FakeStageNotify(enabled=("order_placed", "delivered"))
    api = FakeApi({12: dict(SIPARIS)})
    api.list_payload = {"items": [dict(SIPARIS)], "meta": {"total": 1}}
    api.shipment_payload = {"items": [], "meta": {}}
    service, _, _ = _service(notify=notify, api=api, stage_sms_lookback_days=0)

    # Zamanlayıcı bağlamsız da çağırabiliyor; koşucu canlı servise düşer.
    import store_orders_backend.module as module_mod
    module_mod._LIVE = service
    try:
        payload = await run_stage_sms()
    finally:
        module_mod._LIVE = None
    assert [run["stage"] for run in payload["runs"]] == list(stages.SWEEP_STAGES)
    assert payload["ok"] is True


async def test_zamanlanmis_is_modul_hazir_degilse_sessizce_atlar() -> None:
    import store_orders_backend.module as module_mod
    module_mod._LIVE = None
    payload = await run_stage_sms()
    assert payload["ok"] is False
    assert "hazır değil" in payload["error"]


# ======================================================= durum sorgusu (ekran)

async def test_ekran_asama_durumunu_ve_pencereyi_okur() -> None:
    notify = FakeStageNotify(enabled=("shipped",))
    service, _, _ = _service(notify=notify, stage_sms_lookback_days=5)
    state = await service.stage_state()
    assert state["available"] is True
    assert state["enabled"] == ["shipped"]
    assert state["lookbackDays"] == 5
    assert state["dryRun"] is False


async def test_bildirimler_yoksa_durum_nedenini_soyler() -> None:
    service, _, _ = _service(notify=None)
    state = await service.stage_state()
    assert state["available"] is False
    assert state["error"]
