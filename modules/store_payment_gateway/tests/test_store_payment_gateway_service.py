"""Tahsilat servisi — iş kuralları. Ağa çıkmaz; `store.api` ve `notify` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_payment_gateway_backend import collect
from store_payment_gateway_backend.service import STANDALONE_METHOD, PaymentGatewayService
from store_payment_gateway_fakes import FakeApi, FakeLog, FakeNotify, FakeSmsResult, FakeStore

REASON = "Müşteri telefonda kartla ödemek istedi"

#: Alan adları CANLI YANITTAN alınmıştır: Bagisto admin API'si camelCase
#: döner (`taxCategoryId`, `specialPrice`, `price` ondalık METİN).
URUN = {"id": 7, "sku": "KTP-1", "name": "Deneme kitabı", "price": "100.0000",
        "specialPrice": None, "taxCategoryId": 3}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             notify: Any = None,
             **config: Any) -> tuple[PaymentGatewayService, FakeApi, FakeStore]:
    api = api or FakeApi({7: dict(URUN)})
    api.tax_categories_payload = {"items": [{"id": 3, "code": "Kitap Vergi", "name": "KDV"}]}
    api.tax_rates_payload = {"items": [{"id": 9, "taxCategoryId": 3, "taxRate": 20}]}
    store = store or FakeStore()
    service = PaymentGatewayService(
        api=api, store=store, log=FakeLog(), notify=notify,
        config={"sms_dry_run": True, "org_name": "BBD Store", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


async def _create(service: PaymentGatewayService, **body: Any) -> dict[str, Any]:
    payload = {"fullName": "Ayşe Yılmaz", "phone": "05321234567",
               "lines": [{"kind": "free", "amount": 125_000, "quantity": 1}], **body}
    return await service.create(payload=payload, actor="Kemal")


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_durum_ucu_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("health")
    result = await service.state()
    assert result["ok"] is True
    assert result["store"]["connected"] is False
    assert "patladı" in result["store"]["error"]


async def test_odeme_uclari_yayinda_degilse_acilista_soylenir() -> None:
    """`bbd_create_payment_link` geçitte METOT olarak var ama mağazadaki UÇ
    13.08.2026'da 404. Yalnız metoda bakan ekran "hazır" der; personel formu
    doldurup onayladıktan SONRA hata görürdü."""
    service, api, _ = _service()
    api.fail.add("bbd_payment_links")
    result = await service.state()
    assert result["ok"] is True
    assert result["payments"]["available"] is False
    assert result["payments"]["notice"]


async def test_odeme_uclari_yayindaysa_uyari_cikmaz() -> None:
    service, _, _ = _service()
    result = await service.state()
    assert result["payments"]["available"] is True
    assert result["payments"]["notice"] == ""


async def test_magaza_kapaliyken_odeme_ucu_yoklamasi_istek_atmaz() -> None:
    service, api, _ = _service()
    api.fail.add("health")
    result = await service.state()
    assert result["payments"]["available"] is False
    assert api.used("bbd_payment_links") == []


async def test_vergi_okunamazsa_onizleme_yine_calisir() -> None:
    service, api, _ = _service()
    api.fail.add("tax_categories")
    result = await service.preview({"fullName": "Ayşe", "phone": "5321234567",
                                    "lines": [{"kind": "free", "amount": 10_000}]})
    assert result["ok"] is True
    assert result["amounts"]["gross"] == 10_000


# ================================================================ önizleme

async def test_onizleme_hicbir_sey_yazmaz() -> None:
    service, api, store = _service()
    await service.preview({"fullName": "Ayşe", "phone": "5321234567",
                           "lines": [{"kind": "free", "amount": 10_000}]})
    assert store.requests == []
    assert store.events == []
    assert not [name for name, _, _ in api.calls if name.startswith("bbd_")]


async def test_onizleme_urunun_kendi_vergisini_uygular() -> None:
    service, _, _ = _service()
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "product", "productId": 7, "amount": 100_000, "quantity": 1}],
    })
    assert result["amounts"]["tax"] == 20_000
    assert result["amounts"]["gross"] == 120_000


async def test_onizleme_serbest_tutar_ve_urunu_birlikte_kirar() -> None:
    service, _, _ = _service()
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "free", "amount": 50_000},
                  {"kind": "product", "productId": 7, "amount": 100_000}],
    })
    assert result["amounts"]["net"] == 150_000
    assert result["amounts"]["tax"] == 20_000        # serbest tutardan vergi çıkmaz
    assert result["ready"] is True


async def test_vergi_orani_okunamayan_urun_tahsilati_durdurur() -> None:
    """Oran çözülemiyorsa ekran "KDV %0" yazıp geçmez: sessiz sıfır, faturayı
    KDV kadar eksik keser. Ön izleme hazır DEMEZ ve talep açılmaz."""
    service, api, store = _service()
    api.tax_categories_payload = {"items": [{"id": 3, "name": "KDV", "taxRates": None}]}
    api.tax_rates_payload = {"items": [{"id": 9, "taxRate": 20}]}   # kategori bağı YOK
    body = {"fullName": "Ayşe", "phone": "5321234567",
            "lines": [{"kind": "product", "productId": 7, "amount": 100_000}]}
    preview = await service.preview(body)
    assert preview["ready"] is False
    assert any("KDV oranı" in problem for problem in preview["problems"])

    created = await service.create(payload=body, actor="Kemal")
    assert created["ok"] is False
    assert store.requests == []


async def test_urun_aramasi_urun_basina_ek_istek_atmaz() -> None:
    """Geçidin hız kovası dakikada 55 istek. Vergi oranı için ürün başına
    detay çağırmak, 20 sonuçlu tek bir aramada kovayı tüketiyordu; oysa
    `taxCategoryId` zaten listede geliyor."""
    service, api, _ = _service()
    api.products_payload = {
        "items": [{"id": 100 + i, "sku": f"S{i}", "name": f"Kitap {i}",
                   "price": "100.0000", "taxCategoryId": 3} for i in range(20)],
        "meta": {"total": 20, "currentPage": 1, "perPage": 25},
    }
    result = await service.search_products(q="kitap")
    assert len(result["items"]) == 20
    assert result["items"][0]["taxRate"] == 20.0
    assert api.used("product") == []          # tek bir ürün detayı bile çekilmedi


async def test_urun_aramasi_okunamayan_orani_sifir_diye_gostermez() -> None:
    service, api, _ = _service()
    api.tax_rates_payload = {"items": [{"id": 9, "taxRate": 20}]}   # kategori bağı YOK
    api.products_payload = {"items": [{"id": 100, "sku": "S", "name": "Kitap",
                                       "price": "100.0000", "taxCategoryId": 3}],
                            "meta": {"total": 1}}
    result = await service.search_products(q="kitap")
    assert result["items"][0]["taxRate"] is None
    assert result["items"][0]["taxNote"]


async def test_vergi_tablolari_her_onizlemede_yeniden_cekilmez() -> None:
    service, api, _ = _service()
    body = {"fullName": "Ayşe", "phone": "5321234567",
            "lines": [{"kind": "free", "amount": 10_000}]}
    await service.preview(body)
    await service.preview(body)
    await service.preview(body)
    assert len(api.used("tax_rates")) == 1


async def test_vergi_okunamayinca_basarisiz_sonuc_onbellege_alinmaz() -> None:
    """Mağaza beş dakika boyunca "oran yok" demeye devam etmesin."""
    service, api, _ = _service()
    api.fail.add("tax_categories")
    await service.preview({"fullName": "Ayşe", "phone": "5321234567", "lines": []})
    api.fail.discard("tax_categories")
    result = await service.preview({
        "fullName": "Ayşe", "phone": "5321234567",
        "lines": [{"kind": "product", "productId": 7, "amount": 100_000}]})
    assert result["amounts"]["tax"] == 20_000


async def test_onizleme_eksikleri_sayar_ve_hazir_demez() -> None:
    service, _, _ = _service()
    result = await service.preview({"lines": []})
    assert result["ready"] is False
    assert any("tutar" in problem for problem in result["problems"])
    assert any("Ad soyad" in problem for problem in result["problems"])


async def test_onizlemede_eposta_varsayilani_doldurulur() -> None:
    service, _, _ = _service()
    result = await service.preview({"fullName": "Ayşe", "phone": "5321234567"})
    assert result["email"] == collect.DEFAULT_EMAIL


# ================================================================== kayıt

async def test_talep_yerel_kaydedilir_magazaya_yazilmaz() -> None:
    service, api, store = _service()
    result = await _create(service)
    assert result["ok"] is True
    assert result["code"].startswith("TAH-")
    assert store.requests[0]["gross"] == 125_000
    assert store.requests[0]["status"] == collect.DRAFT
    assert not [name for name, _, _ in api.calls if name.startswith("bbd_")]


async def test_telefonu_bozuk_talep_acilmaz() -> None:
    service, _, store = _service()
    result = await _create(service, phone="123")
    assert result["ok"] is False
    assert "5XXXXXXXXX" in result["error"]
    assert store.requests == []


async def test_tutarsiz_talep_acilmaz() -> None:
    service, _, _ = _service()
    result = await _create(service, lines=[])
    assert result["ok"] is False
    assert "tutar" in result["error"].lower()


# ============================================== bağlantı üretimi ve eksik uç

async def test_serbest_tahsilat_ucu_yoksa_ozellik_aciklamayla_kapalidir() -> None:
    # Sessizce patlamaz: talep kaydedilir, hangi ucun eksik olduğu yazılır.
    service, _api, store = _service()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert STANDALONE_METHOD in result["error"]
    assert result["standalone"] is False
    assert store.requests[0]["status"] == collect.DRAFT      # kayıt duruyor
    assert any(event["action"] == "link" and event["result"] == "hata"
               for event in store.events)


async def test_siparise_bagli_tahsilat_bugun_calisir() -> None:
    service, api, store = _service()
    created = await _create(service, orderId=42)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)
    assert result["ok"] is True
    call = api.used("bbd_create_payment_link")[0]
    assert call["order_id"] == 42
    assert call["amount"] == 125_000          # KURUŞ
    assert call["reason"] == REASON
    assert store.requests[0]["status"] == collect.LINKED
    assert store.requests[0]["token"] == "TKN-1"


async def test_serbest_tahsilat_uc_gelince_kendiliginden_acilir() -> None:
    service, api, _store = _service()
    api.add_standalone()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                                 send_sms=False)
    assert result["ok"] is True
    payload = api.used(STANDALONE_METHOD)[0]["payload"]
    assert payload["amount"] == 125_000
    assert payload["customer"]["phone"] == "5321234567"


async def test_gerekce_on_karakterden_kisaysa_baglanti_uretilmez() -> None:
    service, api, _ = _service()
    api.add_standalone()
    created = await _create(service)
    result = await service.start(created["id"], reason="kısa", actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used(STANDALONE_METHOD) == []


async def test_kuru_provada_baglanti_uretilmez_ve_durum_degismez() -> None:
    service, api, store = _service()
    api.add_standalone()
    created = await _create(service)
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=True)
    assert result["dryRun"] is True
    assert store.requests[0]["status"] == collect.DRAFT
    assert "Kuru prova" in result["notice"]


async def test_odenmis_talebe_ikinci_baglanti_uretilmez() -> None:
    # ÇİFT ÇEKİM KAPISI.
    service, api, store = _service()
    api.add_standalone()
    created = await _create(service)
    store.requests[0]["status"] = collect.PAID
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used(STANDALONE_METHOD) == []


async def test_durumu_okunamayan_talebe_yeni_baglanti_uretilmez() -> None:
    service, api, store = _service()
    api.add_standalone()
    created = await _create(service)
    store.requests[0]["status"] = collect.UNKNOWN
    result = await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert collect.DOUBLE_CHARGE_WARNING in result["error"]
    assert api.used(STANDALONE_METHOD) == []


# ============================================================ SMS üç fren

async def test_modul_kuru_provasi_acikken_gercek_sms_gitmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, api, _store = _service(notify=notify, sms_dry_run=True)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert notify.provider.sent == []
    assert "sms_dry_run" in result["notice"]


async def test_platform_kuru_provasi_acikken_gonderim_isaretlenmez() -> None:
    notify = FakeNotify(dry_run=True,
                        provider=None)
    notify.provider.result = FakeSmsResult(accepted=True, dry_run=True)
    service, api, _ = _service(notify=notify, sms_dry_run=False)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert result["dryRun"] is True


async def test_istegin_kendi_kuru_provasi_da_freni_tutar() -> None:
    notify = FakeNotify(dry_run=False)
    service, api, _ = _service(notify=notify, sms_dry_run=False)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=True)
    assert result["sent"] is False
    assert notify.provider.sent == []


async def test_beyaz_liste_disindaki_numaraya_gercek_sms_gitmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, api, _ = _service(notify=notify, sms_dry_run=False,
                               sms_allowlist=["5559998877"])
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is False
    assert "beyaz liste" in result["notice"]
    assert notify.provider.sent == []


async def test_uc_fren_de_kapaliyken_sms_gercekten_gider() -> None:
    notify = FakeNotify(dry_run=False)
    service, api, store = _service(notify=notify, sms_dry_run=False)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["sent"] is True
    assert notify.provider.sent[0][0] == "5321234567"
    assert "https://ode.me/TKN-1" in notify.provider.sent[0][1]
    assert store.requests[0]["sms_state"] == "sent"
    assert store.requests[0]["status"] == collect.SENT


async def test_baglantisiz_talebe_sms_gonderilmez() -> None:
    notify = FakeNotify(dry_run=False)
    service, _, _ = _service(notify=notify, sms_dry_run=False)
    created = await _create(service)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "bağlantı" in result["error"]


async def test_sms_yetenegi_yoksa_ekran_calisir_gonderim_reddedilir() -> None:
    service, api, _ = _service(notify=None, sms_dry_run=False)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    result = await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert result["sent"] is False
    assert "Bildirim yeteneği yok" in result["notice"]


# ================================================================ yoklama

async def _linked(notify: Any = None) -> tuple[PaymentGatewayService, FakeApi, FakeStore, int]:
    service, api, store = _service(notify=notify)
    api.add_standalone()
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    return service, api, store, created["id"]


async def test_yoklama_odendi_durumunu_yazar() -> None:
    service, api, store, request_id = await _linked()
    api.links_payload = {"items": [{"token": "TKN-1", "status": "paid", "orderId": 91}]}
    api.orders_by_id[91] = {"id": 91, "incrementId": "91",
                            "invoices": [{"id": 55, "incrementId": "55"}]}
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.PAID
    assert store.requests[0]["order_id"] == 91
    assert store.requests[0]["invoice_id"] == 55


async def test_fatura_siparisin_kendisinden_okunur_liste_suzgecine_guvenilmez() -> None:
    """CANLI TUZAK: `/admin/invoices?order_id=1` sipariş kimliğine değil,
    sipariş NUMARASININ PARÇASINA bakıyor ve mağazadaki 11 faturanın hepsini
    döndürüyor; fatura kaydı `orderId` alanını da boş bırakıyor. Fatura
    listesini süzüp ilk satırı almak, başka müşterinin fatura numarasını bu
    talebe yazmaktı."""
    service, api, store, request_id = await _linked()
    api.links_payload = {"items": [{"token": "TKN-1", "status": "paid", "orderId": 91}]}
    api.orders_by_id[91] = {"id": 91, "invoices": [{"id": 7}, {"id": 9}]}
    # Fatura listesi yanlış cevap verse bile ona hiç sorulmaz.
    api.invoices_payload = {"items": [{"id": 999}]}
    await service.poll(request_id)
    assert store.requests[0]["invoice_id"] == 7
    assert api.used("invoices") == []


async def test_yoklama_bilinmeyen_durumu_basarisiz_yazmaz() -> None:
    service, api, store, request_id = await _linked()
    api.links_payload = {"items": [{"token": "TKN-1", "status": "bank_says_something_new"}]}
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.UNKNOWN
    assert "başarısız" not in result["request"]["status"]["label"].lower()
    # Ham durum saklanır: eşlememiz yanlışsa veri elimizde kalsın.
    assert store.requests[0]["store_status"] == "bank_says_something_new"


async def test_magazada_baglanti_bulunamazsa_bilinmiyor_denir() -> None:
    service, api, _, request_id = await _linked()
    api.links_payload = {"items": []}
    result = await service.poll(request_id)
    assert result["request"]["status"]["code"] == collect.UNKNOWN
    assert collect.DOUBLE_CHARGE_WARNING in result["notice"]


async def test_yoklama_magaza_dusunce_durumu_bozmaz() -> None:
    service, api, store, request_id = await _linked()
    api.fail.add("bbd_payment_links")
    result = await service.poll(request_id)
    assert result["ok"] is True
    assert result["connected"] is False
    assert store.requests[0]["status"] == collect.LINKED     # eski durum korunur


# ======================================================== elden kapatma

async def _with_invoice(order_id: int = 42, invoice_id: int = 55) -> tuple[
        PaymentGatewayService, FakeApi, FakeStore, int]:
    service, api, store = _service()
    api.orders_by_id[order_id] = {"id": order_id, "invoices": [{"id": invoice_id}]}
    created = await _create(service, orderId=order_id)
    return service, api, store, created["id"]


async def test_elden_kapatma_magazaya_odeme_kaydi_yazar() -> None:
    """Gövde MAĞAZANIN şemasıdır: `POST /admin/transactions` zorunlu alanları
    `invoiceId` · `paymentMethod` · `amount`. Eski gövde (`order_id`/`method`/
    `reference`) canlıda 422 alırdı: elden kapatma mağazaya hiç işlenmezdi."""
    service, api, store, request_id = await _with_invoice()
    result = await service.settle(request_id, method="havale", reference="DEK-9",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    payload = api.used("record_transaction")[0]["payload"]
    assert set(payload) == {"invoiceId", "paymentMethod", "amount"}
    assert payload["invoiceId"] == 55
    assert payload["paymentMethod"] == "moneytransfer"   # mağazanın kod adı
    assert payload["amount"] == 1250.0                   # telde sayı, içeride kuruş
    assert store.requests[0]["status"] == collect.SETTLED
    assert store.requests[0]["settle_ref"] == "DEK-9"
    assert store.requests[0]["invoice_id"] == 55


async def test_nakit_beyani_magazanin_kendi_kod_adiyla_gider() -> None:
    service, api, _, request_id = await _with_invoice()
    await service.settle(request_id, method="nakit", reference="", amount=0,
                         reason=REASON, actor="Kemal", dry_run=False)
    assert api.used("record_transaction")[0]["payload"]["paymentMethod"] == "cashondelivery"


async def test_odeme_yontemi_kodu_ayardan_ezilebilir() -> None:
    service, api, _ = _service(settle_payment_methods={"havale": "banka_havalesi"})
    api.orders_by_id[42] = {"id": 42, "invoices": [{"id": 55}]}
    created = await _create(service, orderId=42)
    await service.settle(created["id"], method="havale", reference="", amount=0,
                         reason=REASON, actor="Kemal", dry_run=False)
    assert api.used("record_transaction")[0]["payload"]["paymentMethod"] == "banka_havalesi"


async def test_faturasi_olmayan_siparis_magazaya_islenmez_ama_beyan_durur() -> None:
    """Mağaza ödeme kaydını FATURAYA bağlıyor; faturası kesilmemiş siparişe
    kayıt yazılamaz. Beyan kaybolmaz, ekran nedenini söyler."""
    service, api, store = _service()
    api.orders_by_id[42] = {"id": 42, "invoices": []}
    created = await _create(service, orderId=42)
    result = await service.settle(created["id"], method="havale", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert api.used("record_transaction") == []
    assert "fatura" in result["notice"].lower()
    assert store.requests[0]["status"] == collect.SETTLED


async def test_dekont_numarasinin_magazada_karsiligi_olmadigi_soylenir() -> None:
    service, _, _, request_id = await _with_invoice()
    result = await service.settle(request_id, method="havale", reference="DEK-9",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert "saklanmaz" in result["notice"]


async def test_magaza_yazamazsa_beyan_yine_de_kaydedilir() -> None:
    service, api, store, request_id = await _with_invoice()
    api.fail.add("record_transaction")
    result = await service.settle(request_id, method="nakit", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert "mağazaya işlenemedi" in result["notice"]
    assert store.requests[0]["status"] == collect.SETTLED


async def test_parasi_cekilmis_olabilecek_talep_elden_kapatilmaz() -> None:
    service, _, store = _service()
    created = await _create(service, orderId=42)
    store.requests[0]["status"] = collect.VOID_REQUIRED
    result = await service.settle(created["id"], method="havale", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert collect.DOUBLE_CHARGE_WARNING in result["error"]


async def test_gecersiz_yontem_reddedilir() -> None:
    service, _, _ = _service()
    created = await _create(service)
    result = await service.settle(created["id"], method="kripto", reference="",
                                  amount=0, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False


# ================================================================ iptal

async def test_iptal_kaydi_silmez_baglantiyi_oldurur() -> None:
    service, api, store, request_id = await _linked()
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_cancel_payment_link")[0]["reason"] == REASON
    assert len(store.requests) == 1
    assert store.requests[0]["status"] == collect.CANCELLED


async def test_odenmis_tahsilat_iptal_edilmez() -> None:
    service, _, store, request_id = await _linked()
    store.requests[0]["status"] = collect.PAID
    result = await service.cancel(request_id, reason=REASON, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "İadeler" in result["error"]


# =============================================================== şablon

async def test_sablonda_link_yer_tutucusu_zorunludur() -> None:
    service, _, _ = _service()
    result = await service.save_template(body="Sayin {ad}, borcunuz {tutar}",
                                         reason=REASON, actor="Kemal")
    assert result["ok"] is False
    assert "{link}" in result["error"]


async def test_taninmayan_yer_tutucu_kaydedilmez() -> None:
    service, _, _ = _service()
    result = await service.save_template(body="Sayin {isim}, {link}", reason=REASON,
                                         actor="Kemal")
    assert result["ok"] is False
    assert "{isim}" in result["error"]


async def test_kaydedilen_sablon_gonderimde_kullanilir() -> None:
    notify = FakeNotify(dry_run=False)
    service, api, _ = _service(notify=notify, sms_dry_run=False)
    api.add_standalone()
    await service.save_template(body="Odeme: {link} - {kurum}", reason=REASON, actor="Kemal")
    created = await _create(service)
    await service.start(created["id"], reason=REASON, actor="Kemal", dry_run=False,
                        send_sms=False)
    await service.send_sms(created["id"], reason=REASON, actor="Kemal", dry_run=False)
    assert notify.provider.sent[0][1] == "Odeme: https://ode.me/TKN-1 - BBD Store"


# ================================================================ liste

async def test_liste_ozeti_tahsil_edileni_ve_bekleyeni_ayirir() -> None:
    service, _, store = _service()
    await _create(service)
    await _create(service, phone="5322223344")
    store.requests[0]["status"] = collect.PAID
    result = await service.requests()
    assert result["summary"]["collected"] == 125_000
    assert result["summary"]["waiting"] == 125_000


async def test_liste_okunamazsa_ekran_ayakta_kalir() -> None:
    service, _, store = _service()

    async def patla(*_: Any, **__: Any) -> Any:
        raise RuntimeError("depo patladı")

    store.fetch_one = patla  # type: ignore[method-assign]
    result = await service.requests()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []


async def test_acik_link_raporu_sms_gonderilmis_satirlari_da_kapsar() -> None:
    """Link üretilince durum `linked`, SMS gidince `sent` oluyor. Rapor yalnız
    `linked` süzdüğü sürece tam da göstermesi gereken satırları atlıyordu."""
    service, _, store = _service()
    await _create(service)
    store.reads.clear()
    await service.build_report("openlinks", {})
    sql, params = store.reads[-1]
    assert "status IN (?, ?)" in sql
    assert collect.LINKED in params and collect.SENT in params


# ============================================================ yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.printed: list[Any] = []

        async def print_file(self, path: Any, **_: Any) -> dict[str, Any]:
            self.printed.append(path)
            return {"printer": "HP"}

        async def status(self) -> dict[str, Any]:
            return {"ready": True}

    printer = FakePrinter()
    service, _, _ = _service()
    service._printer = printer  # testte yazıcı takılıyor
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"] or "bulunamadı" in result["error"]
    assert printer.printed == []
