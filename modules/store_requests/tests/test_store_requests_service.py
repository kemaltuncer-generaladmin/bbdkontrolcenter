"""Talepler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from store_requests_backend.service import BULK_LIMIT, REPORT_ROW_CAP, RequestsService
from store_requests_fakes import FakeApi, FakeBus, FakeLog, FakeStore

from km_sdk import ExportError

GECERLI_GEREKCE = "müşteri ürünü kullanmadan iade etti"


def _acilis(hours_ago: float = 2) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat()


# CANLI MAĞAZANIN İADE TALEBİ ŞEKLİ. İki alan bir kez sessizce yanlış okundu:
#
#  · `status` BİR SÖZLÜKTÜR (`{id, title, color}`), düz metin değil. Metin
#    sanmak ekranda Python sözlük gösterimini yazdırıyordu.
#  · `items[].id` TALEBİN KALEM SATIRIDIR (`rma_items.id`), sipariş kaleminin
#    kimliği değil. Mağaza kalem güncellemesini bu kimlikle adresliyor;
#    `order_item_id` göndermek yanlış satırı güncellemeye açık kapı bırakırdı.
TALEP = {
    "id": 5, "code": "RMA-5", "type": "return",
    "status": {"id": 3, "title": "İnceleniyor", "color": "#f59e0b"},
    "priority": "high",
    "channel": "web", "subject": "Kalem bozuk geldi", "order_id": 42,
    "order_number": "SIP-42", "customer": {"id": 9, "name": "Veli Yılmaz"},
    "created_at": _acilis(2),
    "messages": [{"id": 1, "author_type": "customer", "body": "Ürün bozuk",
                  "created_at": "2026-08-10T09:00:00Z"}],
    "items": [{"id": 71, "rma_id": 5, "order_item_id": 11, "quantity": 2,
               "resolution": "return"},
              {"id": 72, "rma_id": 5, "order_item_id": 12, "quantity": 0,
               "resolution": "return"}],
}

#: Mağazanın durum sözlüğü — liste yanıtının `meta`sında geliyor.
DURUM_SOZLUGU = [
    {"id": 1, "title": "Beklemede"}, {"id": 2, "title": "Onaylandı"},
    {"id": 3, "title": "İnceleniyor"}, {"id": 4, "title": "Kargoya verildi"},
    {"id": 5, "title": "İade Edildi"}, {"id": 6, "title": "Çözüldü"},
    {"id": 7, "title": "Reddedildi"}, {"id": 8, "title": "Kalem İptal"},
    {"id": 9, "title": "İptal"},
]

# CANLI MAĞAZANIN ŞEKLİ. `GET /api/admin/orders/{id}` yanıtı
# `AdminCollectionEnvelopeNormalizer`den geçiyor ve alan adları camelCase:
# `incrementId · grandTotal · createdAt · shippingTitle · qtyOrdered ·
# qtyRefunded · qtyCanceled`. Sahte veriyi snake_case yazmak, testin geçtiği
# ama ekranın "—" ile dolduğu bir modül üretir; bu sözlük 2026-08-13'te canlı
# mağazadan okunan 19 numaralı siparişin alan adlarını birebir taşır.
SIPARIS = {
    "id": 42, "incrementId": "SIP-42", "grandTotal": "60.00", "status": "processing",
    "statusLabel": "İşleniyor", "createdAt": "2026-08-10 11:05:00",
    "shippingTitle": "Hepsijet - Hepsijet",
    "items": [
        {"id": 11, "sku": "KLM-1", "name": "Kalem", "qtyOrdered": 3, "price": "10.00",
         "qtyRefunded": 0, "qtyCanceled": 0},
        {"id": 12, "sku": "DFT-2", "name": "Defter", "qtyOrdered": 1, "price": "30.00",
         "qtyRefunded": 0, "qtyCanceled": 0},
    ],
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             bus: FakeBus | None = None, printer: Any = None, **config: Any,
             ) -> tuple[RequestsService, FakeApi, FakeStore, FakeBus]:
    api = api or FakeApi({5: json.loads(json.dumps(TALEP))}, {42: SIPARIS})
    store = store or FakeStore()
    bus = bus or FakeBus()
    service = RequestsService(
        api=api, store=store, log=FakeLog(), publish=bus, printer=printer,
        config={"page_size": 50, "board_column_size": 5, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store, bus


# ============================================================ K7 — ayakta kalma

async def test_uzak_uc_yayinda_degilse_ekran_ayakta_kalir() -> None:
    # `/api/admin/bbd/return-requests` yazım aşamasında; geçit 404'ü anlaşılır
    # bir hataya çeviriyor. Ekran çökmez, durumu anlatır.
    service, api, _, _ = _service()
    api.fail.add("bbd_return_requests")
    result = await service.requests()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_siparis_okunamazsa_talep_yine_acilir() -> None:
    service, api, _, _ = _service()
    api.fail.add("order")
    card = await service.card(5)
    assert card["ok"] is True
    assert card["request"]["code"] == "RMA-5"
    assert card["items"] == []
    assert any("sipariş" in warning for warning in card["warnings"])


async def test_talep_okunamazsa_yerel_notlar_yine_gosterilir() -> None:
    service, api, store, _ = _service()
    await service.add_note(5, body="Müşteri ikinci kez aradı", actor="Ayşe")
    api.fail.add("bbd_return_request")
    card = await service.card(5)
    assert card["ok"] is False
    assert card["notes"][0]["body"] == "Müşteri ikinci kez aradı"
    assert store.notes                      # not yerelde duruyor


async def test_panoda_bir_sutun_patlasa_digerleri_dolar() -> None:
    service, api, _, _ = _service()
    api.list_payload = {"items": [dict(TALEP)], "meta": {"total": 3}}
    result = await service.board()
    assert result["connected"] is True
    assert len(result["columns"]) == 6
    assert result["columns"][0]["total"] == 3


# ===================================================== sunucu tarafı sayfalama

async def test_liste_sunucudan_sayfali_gelir_tam_liste_cekilmez() -> None:
    service, api, _, _ = _service()
    api.list_payload = {"items": [dict(TALEP)],
                        "meta": {"total": 143, "currentPage": 2, "perPage": 50, "lastPage": 3}}
    result = await service.requests(page=2)
    assert result["total"] == 143
    assert result["pages"] == 3
    assert api.used("bbd_return_requests")[0]["page"] == 2
    assert api.used("bbd_return_requests")[0]["per_page"] == 50


async def test_pano_sutun_basina_bir_istek_atar_ve_gercek_toplami_okur() -> None:
    # Tek büyük sayfayı çekip gruplamak daha az istek ederdi ama sütun
    # başlığındaki sayı yalan olurdu.
    service, api, _, _ = _service()
    api.list_payload = {"items": [], "meta": {"total": 7}}
    result = await service.board()
    assert len(api.used("bbd_return_requests")) == 6      # durum sayısı kadar
    assert all(call["per_page"] == 5 for call in api.used("bbd_return_requests"))
    assert result["columns"][0]["total"] == 7


# ======================================================= gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _, _ = _service()
    for call in (
        service.update(5, status=6, reason="ok", actor="Ayşe"),
        service.decide(5, approve=True, reason="ok", actor="Ayşe"),
        service.bulk([5], action="close", reason="ok", actor="Ayşe"),
        service.set_items(5, selection={"11": 1}, reason="ok", actor="Ayşe"),
    ):
        result = await call
        assert result["ok"] is False
        assert "Gerekçe" in result["error"]
    assert api.used("bbd_update_return_request") == []


async def test_ic_not_uzaga_hic_gonderilmez() -> None:
    # "internal" bayrağının müşteri portalında yanlış yorumlanması geri
    # alınamaz bir sızıntı olurdu; not mağazaya HİÇ gitmiyor.
    service, api, store, _ = _service()
    result = await service.add_note(5, body="Bu müşteri daha önce de iade etti", actor="Ayşe")
    assert result["ok"] is True
    assert result["local"] is True
    assert api.used("bbd_update_return_request") == []
    assert store.notes[0]["body"].startswith("Bu müşteri")


async def test_musteri_yaniti_kapali_ve_nedenini_soyluyor() -> None:
    """Mağazanın iade ucu müşteriye mesaj YAZMIYOR — istek hiç gitmez.

    Eskiden `{"message": …, "internal": False}` gövdesiyle gidiyordu; mağaza
    böyle bir alan tanımadığı için isteği 422 ile reddediyordu ve kullanıcı ham
    hata metni görüyordu. Şimdi neden yapılamadığı yazıyor ve düğme zaten
    kapalı geliyor (`features().reply`).
    """
    service, api, store, _ = _service()
    result = await service.reply(5, body="Kargo kodunuz hazır", reason=GECERLI_GEREKCE,
                                 actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert result["blocked"] is True
    assert result["feature"] == "reply"
    assert "Bagisto panelinden" in result["error"]
    assert api.used("bbd_update_return_request") == []
    # Denemenin kendisi yerel ize geçer: ne yapmaya çalıştığımız kaybolmaz.
    assert [row["result"] for row in store.audit if row["action"] == "reply"] == ["uc_yok"]


async def test_oncelik_ve_atama_istek_cikmadan_reddedilir() -> None:
    """Mağazada iki alanın da karşılığı yok; gövdeye konsaydı DURUM DA yazılmazdı.

    Bu, sessiz hatanın en pahalı biçimiydi: kullanıcı "durumu da değiştirdim"
    sanıyor, mağaza gövdenin tamamını reddediyordu.
    """
    service, api, _, _ = _service()
    for kwargs in ({"priority": "urgent"}, {"assignee": "Ayşe"}):
        result = await service.update(5, status=6, reason=GECERLI_GEREKCE, actor="Ayşe",
                                      **kwargs)
        assert result["ok"] is False
        assert result["blocked"] is True
    assert api.used("bbd_update_return_request") == []


async def test_para_geciren_durum_gerekce_yazildiktan_sonra_degil_once_durur() -> None:
    """Kimlik 5 ve 8 bankaya para gönderir; mağaza 409 döner, biz önce söyleriz.

    K9: buradaki denetim mağazanınkinin YERİNE GEÇMEZ. Amacı kullanıcıyı
    gerekçe yazıp gönderdikten SONRA reddetmemek.
    """
    service, api, _, _ = _service()
    for durum in (5, 8):
        result = await service.update(5, status=durum, reason=GECERLI_GEREKCE, actor="Ayşe",
                                      dry_run=False)
        assert result["ok"] is False
        assert "BANKA" in result["error"].upper()
    assert api.used("bbd_update_return_request") == []


async def test_ic_not_zincirde_yerel_olarak_gorunur() -> None:
    service, _, _, _ = _service()
    await service.add_note(5, body="Kargo şubesi aradı", actor="Ayşe")
    card = await service.card(5)
    sides = [item["side"] for item in card["thread"]]
    assert "internal" in sides
    note = next(item for item in card["thread"] if item["side"] == "internal")
    assert note["local"] is True


async def test_bilinmeyen_durum_yazilmadan_reddedilir() -> None:
    service, api, _, _ = _service()
    result = await service.update(5, status="uydurma", reason=GECERLI_GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert api.used("bbd_update_return_request") == []


async def test_durum_magazanin_kimligiyle_gider_ekranin_anahtariyla_degil() -> None:
    """Dokuz durumu altıya indiren eşleme TEK YÖNLÜ: yazarken kullanılmaz.

    "Kapandı" sütunundaki bir talebi ekran anahtarıyla kaydetmek, onu üç ayrı
    mağaza durumundan (Çözüldü · Kalem İptal · İptal) hangisine götüreceğini
    bilemezdi — ve ikincisi bankaya para gönderiyor.
    """
    service, api, _, _ = _service()
    await service.update(5, status=6, reason=GECERLI_GEREKCE, actor="Ayşe", dry_run=False)
    payload = api.used("bbd_update_return_request")[0]["payload"]
    assert payload == {"status": 6}


async def test_kuru_prova_varsayilan_olarak_aciktir() -> None:
    service, api, _, _ = _service()
    await service.update(5, status=6, reason=GECERLI_GEREKCE, actor="Ayşe")
    assert api.used("bbd_update_return_request")[0]["dry_run"] is True


# ================================================= iade edilecek kalem seçimi

async def test_siparis_adedini_asan_secim_yazilmaz() -> None:
    service, api, _, _ = _service()
    result = await service.set_items(5, selection={"11": 9}, reason=GECERLI_GEREKCE,
                                     actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert "en çok 3 adet" in result["error"]
    assert api.used("bbd_update_return_request") == []


async def test_gecerli_secim_tutar_tahminiyle_birlikte_yazilir() -> None:
    service, api, _, _ = _service()
    result = await service.set_items(5, selection={"11": 2, "12": 1}, reason=GECERLI_GEREKCE,
                                     actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert result["estimate"]["amount"] == 2 * 1000 + 3000
    payload = api.used("bbd_update_return_request")[0]["payload"]
    # KİMLİK TALEBİN KALEM SATIRIDIR (71/72), sipariş kaleminin (11/12) değil.
    assert payload["items"] == [{"id": 71, "quantity": 2}, {"id": 72, "quantity": 1}]


async def test_siparis_okunamadiysa_secim_dogrulanamaz_ve_yazilmaz() -> None:
    service, api, _, _ = _service()
    api.fail.add("order")
    result = await service.set_items(5, selection={"11": 1}, reason=GECERLI_GEREKCE,
                                     actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert "doğrulanamadığı" in result["error"]
    assert api.used("bbd_update_return_request") == []


# ======================================================================= karar

async def test_onay_para_iade_etmez_iadeler_ekranina_devreder() -> None:
    # Buradan iade başlatmak, iade izni olmayan personele para iade ettirirdi.
    service, api, store, bus = _service()
    result = await service.decide(5, approve=True, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=False)
    assert result["ok"] is True
    assert result["handedOff"] is True
    # Onay "Accept" (2) yazar — para hareketi doğurmaz.
    assert api.used("bbd_update_return_request")[0]["payload"] == {"status": 2}
    assert store.handoff[0]["amount"] == 2000          # 2 adet × 10,00 ₺
    assert bus.events[0][0] == "store_requests.approved"
    assert bus.events[0][1]["orderId"] == 42


async def test_kalem_secilmeden_iade_talebi_onaylanmaz() -> None:
    bos = json.loads(json.dumps(TALEP))
    bos["items"] = []
    service, api, _, _ = _service(FakeApi({5: bos}, {42: SIPARIS}))
    result = await service.decide(5, approve=True, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=False)
    assert result["ok"] is False
    assert "kalem seçilmeden" in result["error"]
    assert api.used("bbd_update_return_request") == []


async def test_bilgi_talebi_kalem_olmadan_da_onaylanir() -> None:
    bilgi = json.loads(json.dumps(TALEP))
    bilgi["type"] = "info"
    bilgi["items"] = []
    service, _, store, bus = _service(FakeApi({5: bilgi}, {42: SIPARIS}))
    result = await service.decide(5, approve=True, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=False)
    assert result["ok"] is True
    assert result["handedOff"] is False       # devredilecek kalem yok
    assert store.handoff == []
    assert bus.events == []


async def test_kuru_provada_iade_devri_yazilmaz() -> None:
    service, _, store, bus = _service()
    result = await service.decide(5, approve=True, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=True)
    assert result["ok"] is True
    assert result["handedOff"] is False
    assert store.handoff == []
    assert bus.events == []


async def test_olay_dinleyicisi_patlarsa_onay_geri_alinmaz() -> None:
    service, _, store, _ = _service(bus=FakeBus(fail=True))
    result = await service.decide(5, approve=True, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=False)
    assert result["ok"] is True
    assert result["handedOff"] is True
    assert store.handoff                       # devir kaydı yerelde duruyor


async def test_ret_devir_yapmaz() -> None:
    service, api, store, bus = _service()
    result = await service.decide(5, approve=False, reason=GECERLI_GEREKCE, actor="Ayşe",
                                  dry_run=False)
    assert result["status"] == "rejected"
    assert api.used("bbd_update_return_request")[0]["payload"] == {"status": 7}
    assert store.handoff == []
    assert bus.events == []


# ================================================================ toplu işlem

async def test_toplu_islem_sirayla_yazar_ve_biri_patlarsa_gerisi_surer() -> None:
    class Kirilgan(FakeApi):
        async def bbd_update_return_request(self, request_id: int, **kwargs: Any):
            if request_id == 2:
                raise RuntimeError("uç reddetti")
            return await super().bbd_update_return_request(request_id, **kwargs)

    service, _, store, _ = _service(Kirilgan())
    result = await service.bulk([1, 2, 3], action="close", reason=GECERLI_GEREKCE, actor="Ayşe",
                                dry_run=False)
    assert result["applied"] == 2
    assert result["failed"] == [2]
    assert result["ok"] is False
    assert any(row["result"] == "hata" for row in store.audit)


async def test_toplu_islem_tavani_asilamaz() -> None:
    service, api, _, _ = _service()
    result = await service.bulk(list(range(1, BULK_LIMIT + 2)), action="close",
                                reason=GECERLI_GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert str(BULK_LIMIT) in result["error"]
    assert api.used("bbd_update_return_request") == []


# ============================================================== denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_gecer() -> None:
    service, _, store, _ = _service()
    await service.update(5, status=6, reason=GECERLI_GEREKCE, actor="Ayşe",
                         dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "update"]
    assert kayitlar[-1]["reason"] == GECERLI_GEREKCE
    assert kayitlar[-1]["actor"] == "Ayşe"
    assert kayitlar[-1]["result"] == "ok"


async def test_uzak_uc_patlarsa_ne_yapmaya_calistigimiz_yerelde_kalir() -> None:
    service, api, store, _ = _service()
    api.fail.add("bbd_update_return_request")
    result = await service.update(5, status=6, reason=GECERLI_GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]


# ==================================================================== rapor

async def test_yol_dogrulamasi_rapor_klasoru_disini_bastirmaz() -> None:
    class SahteYazici:
        def __init__(self) -> None:
            self.printed: list[Any] = []

        async def print_file(self, path: Any, *, title: str = "", copies: int = 1):
            self.printed.append(path)
            return {"printer": "HP"}

        async def status(self):
            return {"ready": True}

    printer = SahteYazici()
    service, _, _, _ = _service(printer=printer)
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]
    assert printer.printed == []


async def test_yazici_yoksa_ekran_hata_metni_alir() -> None:
    service, _, _, _ = _service()
    assert (await service.printer_status())["ready"] is False
    result = await service.print_report("/tmp/km-test-raporlar/x.pdf")
    assert result["ok"] is False


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _, _ = _service()
    result = await service.build_report("uydurma", {})
    assert result["ok"] is False
    assert "Bilinmeyen rapor" in result["error"]


async def test_rma_formu_talep_secilmeden_uretilmez() -> None:
    service, _, _, _ = _service()
    result = await service.build_report("rma", {"requestId": 0})
    assert result["ok"] is False
    assert "talep seçilmeli" in result["error"]


# =================================================================== referans

async def test_referans_durum_sozlugu_gelmezse_ekran_calisir() -> None:
    """Durum açılırı boş kalır ama ekran ÇÖKMEZ ve nedenini yazar (K7)."""
    service, api, _, _ = _service()
    api.fail.add("bbd_return_requests")
    result = await service.reference()
    assert result["ok"] is True
    assert result["storeStatuses"] == []
    assert "durum açılırı boş kaldı" in result["storeStatusError"]
    assert len(result["statuses"]) == 6       # sabit listeler yine dolu


async def test_para_geciren_durumlar_listeden_cikarilmaz_isaretlenir() -> None:
    """Çıkarmak "böyle bir durum yok" demek olurdu; oysa var, panelden verilir."""
    api = FakeApi({5: json.loads(json.dumps(TALEP))}, {42: SIPARIS})
    api.list_payload = {"items": [], "meta": {"statuses": DURUM_SOZLUGU}}
    service, _, _, _ = _service(api)

    result = await service.reference()
    engelli = {row["id"]: row for row in result["storeStatuses"] if row["blocked"]}
    assert sorted(engelli) == [5, 8]
    assert all("BANKA" in row["reason"].upper() for row in engelli.values())
    # Onay geçişi engelli DEĞİL ama panel düğmesini açtığını söylüyor.
    onay = next(row for row in result["storeStatuses"] if row["id"] == 2)
    assert onay["blocked"] is False
    assert onay["unlocksPanelRefund"] is True


async def test_kapali_isler_nedeniyle_birlikte_ilan_edilir() -> None:
    """Düğme kapalıysa NEDENİ ekranda olmalı; boş bir `disabled` yeterli değil."""
    service, _, _, _ = _service()
    features = service.features()
    for key in ("priority", "assign", "reply"):
        assert features[key]["available"] is False
        assert len(features[key]["reason"]) > 40
    for key in ("status", "note", "items", "decide"):
        assert features[key]["available"] is True


# ============================================ canlı mağaza alan adları (regresyon)

async def test_canli_siparis_ozeti_camelcase_alanlardan_doldurulur() -> None:
    # 2026-08-13'te canlıdan okunan yanıt: modül `increment_id`/`grand_total`
    # arıyordu, mağaza `incrementId`/`grandTotal` gönderiyor. Sonuç: çekmecede
    # sipariş kartı açılıyor ama Tarih/Tutar/Kargo satırları "—" kalıyordu.
    service, _, _, _ = _service()
    card = await service.card(5)
    order = card["order"]
    assert order["number"] == "SIP-42"
    assert order["total"] == 6_000                    # 60,00 ₺ → kuruş
    assert order["createdAt"].startswith("2026-08-10")
    assert order["statusLabel"] == "İşleniyor"
    assert order["shipping"] == "Hepsijet - Hepsijet"


async def test_eski_snake_case_siparis_de_okunmaya_devam_eder() -> None:
    # Uç sürümü geri dönerse ekran boşalmasın: iki yazım da tutar.
    eski = {"id": 42, "increment_id": "SIP-42", "grand_total": "60.00",
            "created_at": "2026-08-10T11:05:00Z", "status": "processing",
            "shipping_title": "Yurtiçi", "items": []}
    service, _, _, _ = _service(FakeApi({5: json.loads(json.dumps(TALEP))}, {42: eski}))
    order = (await service.card(5))["order"]
    assert order["number"] == "SIP-42"
    assert order["total"] == 6_000
    assert order["shipping"] == "Yurtiçi"


async def test_siparis_yoksa_ozet_bos_sozluk_olur_uydurulmaz() -> None:
    service, api, _, _ = _service()
    api.fail.add("order")
    assert (await service.card(5))["order"] == {}


# ================================================== rapor üretimi ayakta kalmalı

async def test_pdf_uretilemezse_istisna_degil_hata_metni_doner() -> None:
    # `build_pdf` reportlab yoksa `ExportError` fırlatır. Try'ın dışında
    # kalırsa istisna route'a kadar çıkar ve 500 olur; servis HTTP hatası
    # FIRLATMAZ, `{"ok": False, "error": …}` döner (K7).
    import store_requests_backend.service as modul

    def patla(**_kwargs: Any) -> bytes:
        raise ExportError("PDF için `reportlab` gerekli.")

    service, _, _, _ = _service()
    onceki = modul.build_pdf
    modul.build_pdf = patla
    try:
        sonuc = await service.build_report("rma", {"requestId": 5})
    finally:
        modul.build_pdf = onceki
    assert sonuc["ok"] is False
    assert "reportlab" in sonuc["error"]


async def test_tam_tavan_kadar_kayit_eksik_diye_damgalanmaz() -> None:
    # Tam 2.000 kayıtlık EKSİKSİZ rapor "eksik olabilir" damgası yerse,
    # kullanıcı olmayan veriyi aramaya gider.
    service, api, _, _ = _service()
    sayfa = 50
    api.pages = [
        {"items": [dict(TALEP, id=index * 1000 + row) for row in range(sayfa)],
         "meta": {"total": REPORT_ROW_CAP, "lastPage": REPORT_ROW_CAP // sayfa,
                  "currentPage": index}}
        for index in range(1, REPORT_ROW_CAP // sayfa + 1)
    ]
    rows, truncated = await service._scan({})
    assert len(rows) == REPORT_ROW_CAP
    assert truncated is False
