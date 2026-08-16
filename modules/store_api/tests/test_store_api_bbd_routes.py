"""BBD uçlarının YOLLARI — mağazadaki rota dosyasına karşı sabitlenir.

NEDEN AYRI BİR DOSYA: bu testlerin konusu politika değil ADRESTİR. Yanlış yol
gürültü çıkarmaz; Laravel ya 404 döner (fark edilir) ya da 405 döner (fark
edilmez) ya da — en kötüsü — yolu tanıyıp BAŞKA bir işleyiciyi çalıştırır ve
ekran hatasız bir boş liste gösterir. `catalog/health` tam olarak bunu
yapıyordu. Bu yüzden burada yanıtın içeriği değil, İSTEĞİN KENDİSİ ölçülür:
hangi metot, hangi yol, hangi sorgu parametresi.

Hiçbir test ağa çıkmaz — `httpx.MockTransport` ile sahte sunucu kullanılır.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from store_api_backend.client import BY_DESIGN, StoreApi
from store_api_backend.errors import StoreApiError
from store_api_fakes import FakeLog, FakeSecrets, FakeStore

TOKEN = "12|cokGizliBelirtec"
NEDEN = "Yol doğrulaması için yazılmış gerekçe"
UUID = "3f1c2d4e-5a6b-4c8d-9e0f-1a2b3c4d5e6f"


async def _uyuma(_seconds: float) -> None:
    return None


def izle(payload: Any = None, status: int = 200,
         **options: Any) -> tuple[StoreApi, list[httpx.Request]]:
    """İstekleri kaydeden geçit. Yazma açık, kuru prova kapalı."""
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(status, json={"data": [], "meta": {}} if payload is None
                              else payload)

    ayar: dict[str, Any] = {"read_only": False, "dry_run_default": False}
    ayar.update(options)
    api = StoreApi(base_url="https://ornek.test", secrets=FakeSecrets({"store.admin_token": TOKEN}),
                   log=FakeLog(), store=FakeStore(),
                   transport=httpx.MockTransport(handler), **ayar)
    api._sleep = _uyuma
    return api, istekler


def yasak() -> Any:
    """Hiç çağrılmaması gereken taşıyıcı."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("mağazaya istek gitmemeliydi")
    return handler


# ===================================================== okuma yolları (GET)

OKUMA_YOLLARI = [
    ("bbd_carriers", (), {}, "/api/admin/bbd/shipping/rates"),
    ("bbd_shipping_rates", (), {}, "/api/admin/bbd/shipping/rates"),
    ("bbd_pos_terminals", (), {}, "/api/admin/bbd/payments/terminals"),
    ("bbd_reconciliation", (), {}, "/api/admin/bbd/payments/reconciliation"),
    ("bbd_notification_rules", (), {}, "/api/admin/bbd/notifications/rules"),
    ("bbd_review_requests", (), {}, "/api/admin/bbd/review-requests"),
    ("bbd_notifications", (), {}, "/api/admin/bbd/notifications"),
    ("bbd_catalog_health", (), {}, "/api/admin/bbd/catalog/health"),
    ("bbd_bestsellers", (), {}, "/api/admin/bbd/catalog/bestsellers"),
    ("bbd_ai_runs", (), {}, "/api/admin/bbd/ai/drafts"),
    ("bbd_trial_results", (4,), {}, "/api/admin/bbd/trial-club/exams/4/results"),
    ("bbd_audit_entry", (9,), {}, "/api/admin/bbd/audits/9"),
    ("bbd_audit", (), {}, "/api/admin/bbd/audits"),
    ("bbd_bundles", (), {}, "/api/admin/bbd/storefront/sets"),
    ("bbd_carousel", (), {}, "/api/admin/bbd/storefront/carousels"),
    ("bbd_trial_exams", (), {}, "/api/admin/bbd/deneme-kulubu/overview"),
    ("bbd_trial_members", (), {}, "/api/admin/bbd/deneme-kulubu/registrations"),
    ("bbd_backups", (), {}, "/api/admin/bbd/backups"),
    ("bbd_payment_links", (), {}, "/api/admin/bbd/payment-links"),
    ("bbd_payment_attempts", (), {}, "/api/admin/bbd/payments/attempts"),
    ("bbd_bld_jobs", (), {}, "/api/admin/bbd/bld/jobs"),
    ("bbd_return_requests", (), {}, "/api/admin/bbd/return-requests"),
    ("bbd_return_request_shipment", (8,), {}, "/api/admin/bbd/return-requests/8/shipment"),
    ("bbd_return_request_label_info", (8,), {},
     "/api/admin/bbd/return-requests/8/shipment/label"),
    ("bbd_mobile_settings", (), {}, "/api/admin/bbd/settings/mobile-app"),
]


@pytest.mark.parametrize(("metot", "args", "kwargs", "yol"), OKUMA_YOLLARI)
async def test_okuma_yollari_magazadaki_rotayla_ayni(metot: str, args: tuple[Any, ...],
                                                     kwargs: dict[str, Any], yol: str) -> None:
    api, istekler = izle()
    await getattr(api, metot)(*args, **kwargs)

    assert len(istekler) == 1
    assert istekler[0].method == "GET"
    assert istekler[0].url.path == yol


# ===================================================== yazma yolları

YAZMA_YOLLARI = [
    ("bbd_send_notification", (), {"payload": {"title": "Duyuru", "body": "Metin"}},
     "POST", "/api/admin/bbd/notifications/send"),
    ("bbd_save_carousel_slot", (), {"payload": {"status": 1}, "slot_id": 3},
     "PATCH", "/api/admin/bbd/storefront/carousels/3"),
    ("bbd_cancel_payment_link", (12,), {}, "POST", "/api/admin/bbd/payment-links/12/cancel"),
    ("bbd_ai_apply", (UUID,), {"selections": []},
     "POST", f"/api/admin/bbd/ai/drafts/{UUID}/apply"),
    ("bbd_update_pos_terminal", ("kuveytturk",), {"payload": {"go_live": False}},
     "PUT", "/api/admin/bbd/payments/terminals/kuveytturk"),
    ("bbd_test_carrier", ("suratkargo",), {},
     "POST", "/api/admin/bbd/shipments/desi-rates/suratkargo/test"),
    ("bbd_bld_test", (), {}, "POST", "/api/admin/bbd/bld/test"),
    ("bbd_verify_backup", ("2026-08-14.sql.gz",), {},
     "POST", "/api/admin/bbd/backups/2026-08-14.sql.gz/verify"),
    ("bbd_reindex_catalog", (), {}, "POST", "/api/admin/bbd/catalog/reindex"),
    ("bbd_upload_trial_results", (4,), {"rows": []},
     "POST", "/api/admin/bbd/trial-club/exams/4/results"),
    ("bbd_publish_trial_results", (4,), {},
     "POST", "/api/admin/bbd/trial-club/exams/4/results/publish"),
    ("bbd_update_shipping_rates", (), {"payload": {"carriers": []}},
     "PUT", "/api/admin/bbd/shipping/rates"),
    ("bbd_refresh_price_list", (), {}, "POST", "/api/admin/bbd/shipping/price-list/refresh"),
    ("bbd_send_review_request", (77,), {}, "POST", "/api/admin/bbd/review-requests"),
    ("bbd_retry_bld_job", (5,), {}, "POST", "/api/admin/bbd/bld/jobs/5/retry"),
    ("bbd_reprint_order", (2392,), {}, "POST", "/api/admin/bbd/bld/orders/2392/reprint"),
    # 2026-08-15'te yayına giren iki uç. İkisi de daha önce ham 405
    # gösteriyordu: yol vardı, fiil tanımlı değildi.
    ("bbd_update_return_request", (8,), {"payload": {"status": 2}},
     "PUT", "/api/admin/bbd/return-requests/8"),
    ("bbd_save_trial_exam", (), {"payload": {"price": "250.00"}},
     "POST", "/api/admin/bbd/deneme-kulubu/overview"),
    # "Kargoya ver" — tek tıkla gönderi + teklif + etiket satın alma.
    ("bbd_dispatch_order", (91,), {"payload": {"packages": 1}},
     "POST", "/api/admin/bbd/orders/91/dispatch"),
    # İADE KARGO ZİNCİRİ — girdi GÖNDERİ değil TALEP kimliği.
    ("bbd_open_return_request_shipment", (8,), {},
     "POST", "/api/admin/bbd/return-requests/8/shipment"),
    ("bbd_sync_return_request_shipment", (8,), {},
     "POST", "/api/admin/bbd/return-requests/8/shipment/sync"),
    ("bbd_purchase_return_request_label", (8,), {"offer_id": "of_1"},
     "POST", "/api/admin/bbd/return-requests/8/shipment/label"),
]


@pytest.mark.parametrize(("metot", "args", "kwargs", "verb", "yol"), YAZMA_YOLLARI)
async def test_yazma_yollari_magazadaki_rotayla_ayni(metot: str, args: tuple[Any, ...],
                                                     kwargs: dict[str, Any], verb: str,
                                                     yol: str) -> None:
    api, istekler = izle()
    await getattr(api, metot)(*args, reason=NEDEN, **kwargs)

    assert len(istekler) == 1
    assert istekler[0].method == verb
    assert istekler[0].url.path == yol


async def test_karusel_guncellemesi_put_degil_patch_gider() -> None:
    """PUT gönderilse yol eşleşir ama metot eşleşmez: 404 değil 405 gelirdi."""
    api, istekler = izle()
    await api.bbd_save_carousel_slot(payload={"status": 0}, slot_id=7, reason=NEDEN)

    assert istekler[0].method == "PATCH"
    assert istekler[0].method != "PUT"


# ======================================= katalog sağlığı: tip YOLDA durur

async def test_katalog_sorunu_yola_konur_sorguya_degil() -> None:
    api, istekler = izle()
    await api.bbd_catalog_issues({"kind": "no_image"})

    assert istekler[0].url.path == "/api/admin/bbd/catalog/health/no_image"
    # Sorgu süzgeci olarak GÖNDERİLMEZ: Laravel onu sessizce yok sayıp özet
    # yanıtı döndürüyordu ve ekran hatasız bir boş liste görüyordu.
    assert "kind" not in istekler[0].url.params
    assert "issue" not in istekler[0].url.params


async def test_katalog_sorununun_yeni_adi_da_kabul_edilir() -> None:
    api, istekler = izle()
    await api.bbd_catalog_issues({"issue": "zero_price", "type": "simple"})

    assert istekler[0].url.path == "/api/admin/bbd/catalog/health/zero_price"
    assert istekler[0].url.params["type"] == "simple"      # kalan süzgeç geçer


@pytest.mark.parametrize("tip", ["low_stock", "", "NO_IMAGE", "../health"])
async def test_taninmayan_katalog_sorunu_istek_cikmadan_reddedilir(tip: str) -> None:
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_catalog_issues({"kind": tip})

    assert istekler == []

    assert hata.value.code == "payload"
    assert "no_image" in hata.value.message          # geçerli tipleri sayar


# ==================================== iade kargo zinciri: PARA KAPISI


async def test_iade_etiketi_satin_alirken_dryrun_govdeye_konur() -> None:
    """SESSİZ KURU PROVA TUZAĞI: mağazada bu ucun `dryRun` varsayılanı `true`.

    Alan hiç gitmezse GERÇEK satın alma isteği sessizce provaya düşer; ekran
    "etiket alındı" der ama müşteriye verilecek etiket yoktur.
    """
    import json as _json

    api, istekler = izle()
    await api.bbd_purchase_return_request_label(8, offer_id="of_9", reason=NEDEN, dry_run=False)

    govde = _json.loads(istekler[0].content)
    assert govde["dryRun"] is False           # açıkça yazıldı, varsayılana bırakılmadı
    assert govde["offerId"] == "of_9"


async def test_iade_etiketi_kuru_provada_para_harcamaz() -> None:
    import json as _json

    api, istekler = izle()
    await api.bbd_purchase_return_request_label(8, reason=NEDEN, dry_run=True)

    govde = _json.loads(istekler[0].content)
    assert govde["dryRun"] is True
    # Teklif seçilmediyse alan HİÇ gitmez: boş dize mağazada geçerli bir
    # teklif kimliği değildir ve gönderilmesi 422 üretirdi.
    assert "offerId" not in govde


async def test_iade_gonderisi_acan_uc_satin_alma_alani_gondermez() -> None:
    """`purchaseNow` ve akrabaları bu uçta SESSİZCE YOK SAYILMAZ, 422 alır."""
    import json as _json

    api, istekler = izle()
    await api.bbd_open_return_request_shipment(8, reason=NEDEN, dry_run=False)

    govde = _json.loads(istekler[0].content)
    for yasak_alan in ("purchaseNow", "willAccept", "buyLabel", "autoPurchase"):
        assert yasak_alan not in govde


async def test_iade_etiketi_kunyesi_pdf_indirmeden_sorulur() -> None:
    api, istekler = izle({"rmaId": 8, "isPurchased": False, "labelStored": False})
    sonuc = await api.bbd_return_request_label_info(8)

    assert istekler[0].url.params["format"] == "json"
    assert sonuc["isPurchased"] is False


async def test_iade_etiketi_ham_pdf_olarak_istenir() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "application/pdf"
        return httpx.Response(200, content=b"%PDF-1.4 sahte",
                              headers={"Content-Type": "application/pdf"})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)
    icerik = await api.bbd_return_request_label(8)

    assert icerik.startswith(b"%PDF")


async def test_iade_kargosu_yoksa_ekran_talep_yok_sanmaz() -> None:
    """Gönderi yokken uç 409 `RETURN_SHIPMENT_MISSING` verir — 404 DEĞİL.

    Canlıda ölçüldü (2026-08-16, talep 1 ve 2). `not_found` koduna düşseydi
    ekran "talep bulunamadı" derdi ve operatör olmayan bir arıza arardı.
    """
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"error": {
            "code": "RETURN_SHIPMENT_MISSING",
            "message": "Bu talebin iade gönderisi yok. Önce iade gönderisini açın.",
            "details": {"rmaId": 8}}})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_return_request_label_info(8)

    assert hata.value.code == "conflict"
    assert hata.value.code != "not_found"
    assert hata.value.details["rmaId"] == 8


async def test_iade_kargosu_sorgusu_gonderi_kimligi_degil_talep_kimligi_kullanir() -> None:
    """`bbd_return_shipment(shipment_id)` BAŞKA bir uçtur; ikisi karışmamalı."""
    api, istekler = izle()
    await api.bbd_return_request_shipment(8)
    await api.bbd_return_shipment(8, reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/return-requests/8/shipment"
    assert istekler[1].url.path == "/api/admin/bbd/shipments/8/return"


# ======================================= set üyeliği: TEK yazma ucu vardır


async def test_set_uyeligi_membership_ucuna_gider() -> None:
    """Set bir KATEGORİDİR: `POST/PUT storefront/sets` mağazada YOK.

    Eski yol 404 değil 405 alıyordu (yol GET olarak tanımlı) ve geçit onu
    "uç henüz yayında değil" diye çeviriyordu — ekran olmayan bir dağıtımı
    bekliyordu.
    """
    import json as _json

    api, istekler = izle({"action": "add", "categoryId": 42, "affected": [1427]})
    sonuc = await api.bbd_set_membership([1427], action="add", reason=NEDEN)

    assert istekler[0].method == "POST"
    assert istekler[0].url.path == "/api/admin/bbd/storefront/sets/membership"
    govde = _json.loads(istekler[0].content)
    assert govde["productIds"] == [1427]
    assert govde["action"] == "add"
    assert sonuc["affected"] == [1427]


async def test_set_uyeliginde_cikarma_da_ayni_uctan_gider() -> None:
    import json as _json

    api, istekler = izle({"action": "remove", "categoryId": 42, "affected": [1427]})
    await api.bbd_set_membership([1427, 1428], action="remove", reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/storefront/sets/membership"
    assert _json.loads(istekler[0].content)["action"] == "remove"


@pytest.mark.parametrize("eylem", ["create", "", "delete", "update"])
async def test_taninmayan_set_eylemi_istek_cikmadan_reddedilir(eylem: str) -> None:
    """Mağaza 422 döner; onu beklemek yerine burada söylenir."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_set_membership([1427], action=eylem, reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"
    assert "add" in hata.value.message and "remove" in hata.value.message


async def test_set_eylemi_bosluk_ve_buyuk_harften_etkilenmez() -> None:
    """`ADD ` mağazaya `add` olarak gider: ekran metnini uç sözleşmesi sanmayız."""
    import json as _json

    api, istekler = izle()
    await api.bbd_set_membership([1427], action=" ADD ", reason=NEDEN)

    assert _json.loads(istekler[0].content)["action"] == "add"


async def test_bos_urun_listesi_magazaya_gonderilmez() -> None:
    """Hiçbir şeyi değiştirmeyen bir yazma denetim izini kirletir."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_set_membership([], action="add", reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"


# ================================================= çok satanlar (bestsellers)


async def test_cok_satanlar_bbd_sayfalama_sozlesmesini_kullanir() -> None:
    """BBD zarfı sayfa boyunu `limit` ile alır; `per_page` sessizce yok sayılır."""
    api, istekler = izle({"data": [{"productId": 183, "soldQty": 2, "orderCount": 1}],
                          "meta": {"page": 1, "limit": 25, "total": 1, "last_page": 1}})
    sonuc = await api.bbd_bestsellers(page=1, per_page=25)

    assert istekler[0].url.path == "/api/admin/bbd/catalog/bestsellers"
    assert istekler[0].url.params["limit"] == "25"
    assert "per_page" not in istekler[0].url.params
    assert sonuc["items"][0]["productId"] == 183
    # BBD'nin snake_case `meta`sı çekirdek adlarıyla da okunabilir olmalı.
    assert sonuc["meta"]["lastPage"] == 1


async def test_cok_satanlar_tam_tarama_sayfalari_sirayla_ister() -> None:
    """Uç süzgeç almadığı için tek ürünün rakamı ancak TAM TARAMAYLA bulunur."""
    sayfalar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sayfa = int(request.url.params["page"])
        sayfalar.append(request.url.params["page"])
        return httpx.Response(200, json={
            "data": [{"productId": 100 + sayfa, "soldQty": 1, "orderCount": 1}],
            "meta": {"page": sayfa, "limit": 1, "total": 3, "last_page": 3}})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)
    sonuc = await api.bbd_bestsellers(per_page=1, all_pages=True)

    assert sayfalar == ["1", "2", "3"]
    assert [row["productId"] for row in sonuc["items"]] == [101, 102, 103]
    assert sonuc["truncated"] is False


# ================================ bilerek yazılmayan üç uç: sessiz 404 YOK

#: Mağazanın BİLEREK yazmadığı uçlar. Liste 2026-08-15'te ÜÇTEN İKİYE indi:
#: iade talebi güncellemesi (`PUT bbd/return-requests/{id}`) yayına girdi ve
#: artık gerçek bir istek gönderiyor. Para hareketi doğuran iki DURUM GEÇİŞİ
#: hâlâ kapalı ama kapı artık mağazada (409) — geçit taraflı ilan etmiyor.
BILEREK_YOK = [
    ("bbd_refund_payment", (3,), {"amount": 1000}, "PARA HAREKETİ"),
    ("bbd_restore_backup", ("2026-08-14.sql.gz",), {}, "CANLI VERİYİ EZER"),
]


@pytest.mark.parametrize(("metot", "args", "kwargs", "gerekce"), BILEREK_YOK)
async def test_bilerek_yazilmayan_uc_nedenini_soyler(metot: str, args: tuple[Any, ...],
                                                     kwargs: dict[str, Any],
                                                     gerekce: str) -> None:
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await getattr(api, metot)(*args, reason=NEDEN, **kwargs)

    assert istekler == []                                  # istek HİÇ gitmedi
    assert hata.value.code == BY_DESIGN
    assert hata.value.code != "bbd_endpoint_missing"       # "bekle" DEMİYOR
    assert "BİLEREK" in hata.value.message
    assert gerekce in hata.value.message


async def test_bilerek_yok_ile_yayinda_degil_ayri_kodlardir() -> None:
    """Panel ikisini ayırt edebilmeli: biri beklenir, diğeri beklenmez."""
    def eksik(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="404 Sayfa Bulunamadı")

    api, _ = izle()
    api._transport = httpx.MockTransport(eksik)
    with pytest.raises(StoreApiError) as yayinda_degil:
        await api.bbd_ai_tools()

    assert yayinda_degil.value.code == "bbd_endpoint_missing"
    assert BY_DESIGN != "bbd_endpoint_missing"


# ============================================ 405: yol var, eylem yok

async def test_yol_var_metot_yoksa_anlasilir_hata_doner() -> None:
    """405 daha önce genel `http` koduna düşüyordu; ekran nedenini söyleyemiyordu."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(405, text="Method Not Allowed")

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_reorder_carousel(order=[1, 2], reason=NEDEN)

    assert hata.value.status == 405
    assert hata.value.code == "bbd_endpoint_missing"
    assert "PUT" in hata.value.message


# ==================================== sayfalama: BBD `limit`, çekirdek `per_page`

async def test_bbd_koleksiyonu_limit_gonderir_per_page_degil() -> None:
    """Canlı ölçüm: `per_page=2` → 25 satır, `limit=2` → 2 satır (2026-08-14)."""
    api, istekler = izle()
    await api.bbd_payment_links({})

    params = istekler[0].url.params
    assert params["limit"] == "50"
    assert "per_page" not in params


async def test_cekirdek_koleksiyonu_per_page_gondermeye_devam_eder() -> None:
    """BBD düzeltmesi çekirdeğe SIZMAMALI: iki uç yüzeyi ayrı sözleşmedir."""
    api, istekler = izle()
    await api.orders({})

    params = istekler[0].url.params
    assert params["per_page"] == "50"
    assert "limit" not in params


async def test_bbd_meta_adlari_cekirdek_adlariyla_eslenir() -> None:
    api, _ = izle({"data": [{"id": 1}],
                   "meta": {"page": 2, "limit": 25, "total": 60, "last_page": 3}})
    sonuc = await api.bbd_payment_links({})

    meta = sonuc["meta"]
    assert meta["currentPage"] == 2 and meta["perPage"] == 25 and meta["lastPage"] == 3
    # Sunucunun kendi adları KALIR: çeviri ekleme, silme değil.
    assert meta["page"] == 2 and meta["limit"] == 25 and meta["last_page"] == 3


async def test_tam_tarama_bbd_zarfinda_son_sayfaya_kadar_gider() -> None:
    """`last_page` okunmasaydı tarama İLK SAYFADA biterdi: sunucu 25 satır
    döndürüyor, geçit 50 istiyor ve "eksik dolu sayfa = son sayfa" kuralı
    devreye giriyordu — yani sessiz veri kaybı."""
    sayfalar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sayfa = request.url.params["page"]
        sayfalar.append(sayfa)
        return httpx.Response(200, json={
            "data": [{"id": int(sayfa) * 100 + n} for n in range(25)],
            "meta": {"page": int(sayfa), "limit": 25, "total": 75, "last_page": 3},
        })

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)
    sonuc = await api.bbd_trial_members(all_pages=True)

    assert sayfalar == ["1", "2", "3"]
    assert len(sonuc["items"]) == 75


# ============================================ taşıyıcı listesi: doğru kaynak

async def test_tasiyicilar_desi_satirlarindan_degil_tanimlardan_okunur() -> None:
    """Desi satırı taşıyıcı değildir: pano 3 taşıyıcıyı 15 sanıyordu."""
    api, istekler = izle({
        "carriers": [
            {"code": "suratkargo", "title": "Sürat Kargo", "active": True, "tiers": [{}, {}]},
            {"code": "aras", "title": "Aras Kargo", "active": False, "tiers": [{}]},
        ],
        "free_shipping_threshold": "500.00",
        "meta": {"total": 2, "tierCount": 3},
    })
    sonuc = await api.bbd_carriers()

    assert istekler[0].url.path == "/api/admin/bbd/shipping/rates"
    assert [item["code"] for item in sonuc["items"]] == ["suratkargo", "aras"]
    assert sonuc["meta"]["total"] == 2          # kademe sayısı değil taşıyıcı sayısı


# ==================================== gövde kapıları: istek çıkmadan durur

async def test_odeme_linki_govdesi_magazanin_okudugu_alanlari_tasir() -> None:
    api, istekler = izle()
    await api.bbd_create_payment_link(
        amount="125.50", billing={"firstName": "Ayşe", "lastName": "Yılmaz"},
        description="Telefonla sipariş", reason=NEDEN)

    import json
    govde = json.loads(istekler[0].content)
    assert govde["kind"] == "custom"
    assert govde["amount"] == "125.50"           # ONDALIK TL, kuruş değil
    assert govde["billing"]["firstName"] == "Ayşe"
    assert "orderId" not in govde                # sunucu okumuyor


async def test_odeme_linkinde_dryRun_govdeye_ACIKCA_yazilir() -> None:
    """GEÇEN SEFER SESSİZ VE TAM TERSİYDİ: gerçek bağlantı üretimi mağazada
    kuru provaya düşüyordu, yani ekrandan HİÇBİR ZAMAN gerçek bir tahsilat
    bağlantısı üretilemiyordu.

    Zinciri: `PaymentLinkController::store` bayrağı
    `$this->dryRun($request, true)` ile okuyor (VARSAYILAN true, "para ile
    ilgili uç" sigortası) ve `DryRun::parse` alan HİÇ GELMEDİĞİNDE varsayılana
    düşüyor. Geçit tarafında `_write` ise `dryRun`u yalnız kuru provada gövdeye
    ekliyordu; `dry_run=False` iken alan hiç gitmiyordu. Sonuç: mağaza
    `DB::rollBack()` yapıyor, `{"dryRun": true}` döndürüyor, ekran "kuru prova:
    bağlantı üretilmedi" diyor — oysa personel kuru provayı KAPATMIŞTI.

    Bu testin katmanı ÖNEMLİ: modül taklidi `_write`e hiç uğramadığı için bu
    hatayı göremez. Ölçüm telden geçen gövdenin üzerinde yapılır.
    """
    import json

    api, istekler = izle()
    await api.bbd_create_payment_link(
        amount="125.00", billing={"firstName": "Ayşe", "lastName": "Yılmaz"},
        reason=NEDEN, dry_run=False)

    govde = json.loads(istekler[0].content)
    assert "dryRun" in govde                     # alan HİÇ gitmiyordu
    assert govde["dryRun"] is False


async def test_odeme_linki_kuru_provada_dryRun_true_gider() -> None:
    import json

    api, istekler = izle()
    await api.bbd_create_payment_link(
        amount="125.00", billing={"firstName": "A", "lastName": "B"},
        reason=NEDEN, dry_run=True)

    assert json.loads(istekler[0].content)["dryRun"] is True


async def test_odeme_linkinde_dryRun_varsayilani_gecidin_ayarindan_gelir() -> None:
    """`dry_run=None` verilmediğinde karar geçidin `dry_run_default`ıdır —
    `_write` ile AYNI kural (`effective_dry_run`). İki ayrı yerde iki ayrı
    kural yazılsaydı, ayarın açık olduğu bir kurulumda gövde `false` derken
    denetim izi `true` yazardı."""
    import json

    api, istekler = izle(dry_run_default=True)
    await api.bbd_create_payment_link(
        amount="125.00", billing={"firstName": "A", "lastName": "B"}, reason=NEDEN)

    assert json.loads(istekler[0].content)["dryRun"] is True


async def test_tek_odeme_linki_sayisal_kimlikle_okunur() -> None:
    """Liste ucu `token`/`order_id` süzgeci tanımıyor ve sayfa başına 50 satır
    veriyor; aranan link sayfada yoksa liste boş DÖNMÜYOR, en yeni linkleri
    döndürüyor. Belirli bir linki okumanın tek kesin yolu tekil uçtur."""
    api, istekler = izle(payload={"data": {"id": 41, "code": "TKN-1"}})
    sonuc = await api.bbd_payment_link(41)

    assert istekler[0].method == "GET"
    assert istekler[0].url.path == "/api/admin/bbd/payment-links/41"
    assert sonuc["code"] == "TKN-1"


async def test_odeme_linkinde_siparis_kimligi_reddedilir() -> None:
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_create_payment_link(order_id=7, amount="125.00",
                                          billing={"firstName": "A", "lastName": "B"},
                                          reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"
    assert "orderId" in hata.value.message


async def test_odeme_linkinde_kurus_tam_sayisi_reddedilir() -> None:
    """Kuruş gönderilirse sunucu onu TL sanar: garantili 422 AMOUNT_DRIFT."""
    api, _ = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_create_payment_link(amount=12_500,  # type: ignore[arg-type]
                                          billing={"firstName": "A", "lastName": "B"},
                                          reason=NEDEN)

    assert hata.value.code == "payload"
    assert "AMOUNT_DRIFT" in hata.value.message


async def test_odeme_linkinde_fatura_bilgisi_zorunludur() -> None:
    api, _ = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_create_payment_link(amount="10.00", reason=NEDEN)

    assert hata.value.code == "payload"
    assert "billing" in hata.value.message


async def test_urun_linki_urun_listesi_ister() -> None:
    api, _ = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_create_payment_link(kind="product",
                                          billing={"firstName": "A", "lastName": "B"},
                                          reason=NEDEN)

    assert hata.value.code == "payload"
    assert "items" in hata.value.message


@pytest.mark.parametrize("kimlik", ["abc", "", "12a", "TOKEN-9"])
async def test_odeme_linki_iptalinde_kod_degil_sayisal_kimlik_istenir(kimlik: str) -> None:
    """`whereNumber('id')` yüzünden sayısal olmayan kimlik 404 üretiyordu."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_cancel_payment_link(kimlik, reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"
    assert "`id`" in hata.value.message


async def test_sayisal_gorunumlu_kod_gecitte_ayirt_edilemez() -> None:
    """SINIRI AÇIKÇA YAZIYORUZ: 12 haneli `code` de yalnızca rakamdır.

    Geçit onu `id`den ayıramaz ve ayırmaya ÇALIŞMAZ — "9 haneden uzunsa
    koddur" gibi bir tahmin, bir gün büyüyen bir birincil anahtarı geçersiz
    ilan ederdi. Kod gönderilirse istek rotaya UYAR, denetleyiciye ulaşır ve
    mağaza "kayıt bulunamadı" der; o yanıt artık `bbd_endpoint_missing` değil
    `not_found` olarak görünür (bkz. aşağıdaki test). Doğru düzeltme çağıran
    taraftadır: liste yanıtı `id` ve `code` alanlarının ikisini de veriyor.
    """
    api, istekler = izle()
    await api.bbd_cancel_payment_link("481516234278", reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/payment-links/481516234278/cancel"


async def test_denetleyiciye_ulasan_404_uc_yok_sayilmaz() -> None:
    """Zarf kanıtlıyor: `{"error": {...}}` = istek denetleyiciye ULAŞTI."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND",
                                                   "message": "Ödeme bağlantısı bulunamadı.",
                                                   "details": {}}})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_cancel_payment_link(481516234278, reason=NEDEN)

    assert hata.value.code == "not_found"
    assert hata.value.code != "bbd_endpoint_missing"
    assert "Ödeme bağlantısı bulunamadı." in hata.value.message


async def test_rota_hic_eslesmediginde_uc_yayinda_degil_denir() -> None:
    """Laravel'in kendi 404'ü `error` alanında düz metin taşır."""
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "404 Sayfa Bulunamadı",
                                         "description": "Aradığınız sayfa bulunamadı."})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_pos_terminals()

    assert hata.value.code == "bbd_endpoint_missing"


@pytest.mark.parametrize("kimlik", [734, "734", "0034"])
async def test_odeme_linki_iptali_sayisal_kimlikle_gecer(kimlik: Any) -> None:
    api, istekler = izle()
    await api.bbd_cancel_payment_link(kimlik, reason=NEDEN)

    assert istekler[0].url.path == f"/api/admin/bbd/payment-links/{kimlik}/cancel"


@pytest.mark.parametrize("kimlik", [17, "17", "kisa-uuid", UUID.replace("-", "")])
async def test_ai_taslak_kimligi_uuid_olmali(kimlik: Any) -> None:
    """Sunucuda `run` kavramı yok; taslak kimliği 36 karakterlik UUID."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_ai_apply(kimlik, selections=[], reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"
    assert "UUID" in hata.value.message


@pytest.mark.parametrize("kod", ["Kuveytturk!", "", "pos/1", "kuveyt türk"])
async def test_pos_terminal_kimligi_kod_olmali(kod: str) -> None:
    """Rota kısıtı `[a-z0-9_-]+`; dışındaki dize denetleyiciye hiç ulaşmamalı."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_update_pos_terminal(kod, payload={"go_live": False}, reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"


async def test_pos_terminal_kodu_kucuk_harfe_indirilir_ve_yola_kacirilir() -> None:
    api, istekler = izle()
    await api.bbd_update_pos_terminal("KuveytTurk", payload={"go_live": False}, reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/payments/terminals/kuveytturk"


async def test_sayisal_terminal_kimligi_magazadan_gecerli_kod_listesini_alir() -> None:
    """SINIR: "3" rota kısıtına UYAR (rakam da izinli), bu yüzden geçitte
    durdurulamaz. Ama sunucu tanınmayan kodu 404 ile ve İZİN VERİLEN KODLARI
    sayarak reddediyor; ekran artık "uç yok" değil "böyle bir terminal yok"
    görüyor."""
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(404, json={"error": {
            "code": "NOT_FOUND", "message": "Tanınmayan terminal kodu.",
            "details": {"allowed": ["kuveytturk"]}}})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_update_pos_terminal("3", payload={"go_live": False}, reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/payments/terminals/3"
    assert hata.value.code == "not_found"
    assert "Tanınmayan terminal kodu." in hata.value.message


@pytest.mark.parametrize("govde", [
    {},
    {"title": "", "body": "Metin"},
    {"title": "Duyuru"},
    {"title": "A" * 101, "body": "Metin"},
    {"title": "Duyuru", "body": "B" * 501},
    {"channel": "email", "to": ["a@b.c"], "subject": "Konu"},
])
async def test_bildirim_govdesi_baslik_ve_metin_ister(govde: dict[str, Any]) -> None:
    """Uç yalnız toplu push gönderir: `channel`/`to`/`template_id` okunmaz."""
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_send_notification(payload=govde, reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "payload"


async def test_bildirim_gonderimi_kanal_alanlarini_da_tasir() -> None:
    """Fazladan alan DÜŞÜRÜLMEZ: geçit gövdeyi kırpmaz, yalnız eksiği söyler."""
    api, istekler = izle()
    await api.bbd_send_notification(
        payload={"title": "Duyuru", "body": "Metin", "channel": "push"}, reason=NEDEN)

    import json
    govde = json.loads(istekler[0].content)
    assert govde["title"] == "Duyuru"
    assert govde["channel"] == "push"


# ===================================== 503/409 "uç yok" diye gösterilmez

async def test_push_yapilandirilmamis_hatasi_uc_yok_sayilmaz() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"code": "PUSH_NOT_CONFIGURED",
                                                   "message": "Bildirim altyapısı yok."}})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_send_notification(payload={"title": "T", "body": "M"}, reason=NEDEN)

    assert hata.value.code == "server"
    assert hata.value.code != "bbd_endpoint_missing"


async def test_kampanya_bildirimi_kapali_hatasi_cakisma_olarak_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"message": "Kampanya bildirimleri kapalı."})

    api, _ = izle()
    api._transport = httpx.MockTransport(handler)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_send_notification(payload={"title": "T", "body": "M"}, reason=NEDEN)

    assert hata.value.code == "conflict"
    assert "Kampanya" in hata.value.message


# =========================== iade talebi güncellemesi: gövde ELENİR, uydurulmaz

async def test_iade_talebinde_yalniz_yazilabilen_alanlar_gonderilir() -> None:
    """Öncelik ve atama mağazada TUTULMUYOR; gövdeye konmaz.

    Konsaydı mağaza 422 (bilinmeyen alan) ya da 503 (atama sütunu yok)
    döndürür ve AYNI İSTEKTEKİ durum/not da yazılmazdı — kullanıcı "kaydettim"
    der, hiçbir şey değişmezdi.
    """
    import json

    api, istekler = izle()
    await api.bbd_update_return_request(
        8, payload={"status": 2, "note": "Kargo geldi", "priority": "high",
                    "assignee": "Ayşe"},
        reason=NEDEN)

    govde = json.loads(istekler[0].content)
    assert govde["status"] == 2
    assert govde["note"] == "Kargo geldi"
    assert "priority" not in govde
    assert "assignee" not in govde


async def test_iade_talebinde_yazilabilen_alan_yoksa_istek_cikmaz() -> None:
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_update_return_request(8, payload={"priority": "high"}, reason=NEDEN)

    assert istekler == []
    assert hata.value.code == "validation"
    assert "status" in hata.value.message and "note" in hata.value.message


async def test_iade_talebinde_para_geciren_durum_magazaya_BIRAKILIR() -> None:
    """Kimlik 5/8 listesi geçitte TUTULMAZ: iki tarafın ayrışmasına kapı açardı.

    Karar mağazadadır ve 409 ile gelir; geçit isteği gönderir, engellemez.
    """
    api, istekler = izle(
        {"code": "RMA_MONEY_TRANSITION_BLOCKED",
         "message": "Bu geçiş BANKAYA PARA İADESİ gönderir."},
        status=409)

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_update_return_request(8, payload={"status": 5}, reason=NEDEN)

    assert len(istekler) == 1                       # istek GİTTİ, geçit susturmadı
    assert "PARA" in hata.value.message.upper()


# ============================ deneme künyesi: kontenjan istek çıkmadan durur

async def test_deneme_kunyesinde_kontenjan_istek_cikmadan_reddedilir() -> None:
    """Mağaza da 503 döner; geçit onu BEKLETMEDEN söyler.

    Kontenjan bu mağazada bir kayıt değil — üyelik ürünü stok takibi kapalı
    kaydediliyor ve "kontenjan" yalnız vitrin metni. Sayı gönderilseydi
    fiyat ve durum da yazılmazdı.
    """
    api, istekler = izle()
    api._transport = httpx.MockTransport(yasak())

    with pytest.raises(StoreApiError) as hata:
        await api.bbd_save_trial_exam(payload={"price": "250.00", "capacity": 120},
                                      reason=NEDEN)

    assert istekler == []
    assert hata.value.status == 503
    assert "kontenjan" in hata.value.message.lower()


async def test_deneme_kunyesinde_vitrin_metni_gonderilmez() -> None:
    """Ad/açıklama dile bağlıdır ve panelden düzenlenir; gövdeye konmaz."""
    import json

    api, istekler = izle()
    await api.bbd_save_trial_exam(
        payload={"price": "250.00", "isOpen": True, "name": "Mart Denemesi"},
        exam_id=41, reason=NEDEN)

    assert istekler[0].url.path == "/api/admin/bbd/deneme-kulubu/overview/41"
    assert istekler[0].method == "PUT"
    govde = json.loads(istekler[0].content)
    assert govde == {"price": "250.00", "isOpen": True}


async def test_siki_dogrulayan_uclarda_gerekce_govdeye_konmaz() -> None:
    """`reason` gövdeye konsaydı, gerekçesi olan HER istek 422 alırdı.

    `RmaTransition` ve `DenemeKulubuProfile` tanımadıkları alanı sessizce yok
    saymıyor, isteği reddediyor — ve bu doğru bir karar (`statu: 7` yazan bir
    istemci 200 alıp talebi olduğu yerde bırakırdı). Gerekçe zaten
    `X-Bbd-Reason` başlığıyla gidiyor; gövdedeki kopya bir kolaylıktı.
    """
    import json

    api, istekler = izle()
    await api.bbd_update_return_request(8, payload={"note": "iç not"}, reason=NEDEN)
    await api.bbd_save_trial_exam(payload={"isOpen": False}, reason=NEDEN)

    for istek in istekler:
        govde = json.loads(istek.content)
        assert "reason" not in govde, f"{istek.url.path} gövdesinde gerekçe var"
        # Başlıkta DURUYOR — ASCII dışı karakterler yüzde kodlanmış hâlde
        # (HTTP başlığı latin-1 taşır; `_header_safe` bunu güvene alıyor).
        assert istek.headers["X-Bbd-Reason"]


async def test_diger_bbd_uclarinda_gerekce_govdede_kalmaya_devam_eder() -> None:
    """İstisna DAR olmalı: bu uçlar gerekçeyi kendi tablolarına yazıyor."""
    import json

    api, istekler = izle()
    await api.bbd_create_backup(scope=["db"], reason=NEDEN)

    assert json.loads(istekler[0].content)["reason"] == NEDEN


# ================================== "KARGOYA VER": iki biçimli yanıt + dryRun

def _pdf_veren(istekler: list[httpx.Request], status: int = 200) -> Any:
    """Etiketi gövdede, künyeyi BAŞLIKTA döndüren uç — canlıdaki sözleşme."""
    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(
            status, content=b"%PDF-1.4 etiket",
            headers={
                "Content-Type": "application/pdf",
                "X-Bbd-Shipment-Id": "77",
                "X-Bbd-Order-Id": "91",
                "X-Bbd-Tracking-Number": "1234567890",
                "X-Bbd-Provider": "HEPSIJET",
                "X-Bbd-Purchased": "1",
                "Set-Cookie": "oturum=gizli",
            })
    return handler


async def test_kargoya_ver_PDF_govdesini_ve_kunye_basliklarini_birlikte_dondurur() -> None:
    """`raw=True` başlıkları atardı, `raw=False` PDF'i JSON sanardı.

    Takip numarası PDF gövdesine sığmaz; başlıkta gelir. İkisinden birini
    kaybetmek "etiket var ama takip numarası yok" ya da tam tersi demekti.
    """
    api, istekler = izle()
    api._transport = httpx.MockTransport(_pdf_veren(istekler))
    zarf = await api.bbd_dispatch_order(91, payload={"packages": 1}, reason=NEDEN)

    assert zarf["contentType"] == "application/pdf"
    assert zarf["content"].startswith(b"%PDF")
    assert zarf["json"] is None
    assert zarf["headers"]["x-bbd-tracking-number"] == "1234567890"
    assert zarf["headers"]["x-bbd-purchased"] == "1"


async def test_kargoya_ver_yalnizca_X_Bbd_basliklarini_tasir() -> None:
    # `Set-Cookie` gibi başlıkların ekranlara sızması için hiçbir sebep yok.
    api, istekler = izle()
    api._transport = httpx.MockTransport(_pdf_veren(istekler))
    zarf = await api.bbd_dispatch_order(91, payload={}, reason=NEDEN)

    assert all(ad.startswith("x-bbd-") for ad in zarf["headers"])
    assert "set-cookie" not in zarf["headers"]


async def test_etiket_indirilemediginde_JSON_govde_cozulur() -> None:
    api, _ = izle({"labelReady": False, "shipmentId": 77,
                   "message": "Etiket indirilemedi."})
    zarf = await api.bbd_dispatch_order(91, payload={}, reason=NEDEN)

    assert zarf["contentType"] == "application/json"
    assert zarf["json"]["labelReady"] is False
    assert zarf["content"] == b""


async def test_kargoya_ver_dryRun_govdeye_ACIKCA_yazilir() -> None:
    """Mağazadaki varsayılan `true`; alan gitmezse gerçek istek sessizce
    kuru provaya düşer ve ekran "gönderildi" derken paket yerinde kalır."""
    import json

    api, istekler = izle()
    api._transport = httpx.MockTransport(_pdf_veren(istekler))
    await api.bbd_dispatch_order(91, payload={"packages": 1}, reason=NEDEN, dry_run=False)

    govde = json.loads(istekler[0].content)
    assert govde["dryRun"] is False
    assert govde["packages"] == 1


async def test_kargoya_ver_kuru_provada_dryRun_true_gider() -> None:
    import json

    api, istekler = izle()
    await api.bbd_dispatch_order(91, payload={}, reason=NEDEN, dry_run=True)

    assert json.loads(istekler[0].content)["dryRun"] is True


async def test_kargoya_ver_gerekcesi_govdeye_konmaz_baslikta_gider() -> None:
    # Uç gövdeyi sıkı doğruluyor; tanımadığı alan 422 ile geri döner.
    import json

    api, istekler = izle()
    await api.bbd_dispatch_order(91, payload={"packages": 2}, reason=NEDEN)

    assert "reason" not in json.loads(istekler[0].content)
    assert istekler[0].headers["X-Bbd-Reason"]


async def test_etiket_ucu_HAM_PDF_ister() -> None:
    """Uç künye JSON'u değil PDF'in kendisini döndürüyor (mağaza değişikliği).

    `Accept` başlığı bunu söyler; söylemeseydi sunucu künye gövdesine
    düşebilir ve toplu etiket sayfası JSON birleştirmeye çalışırdı.
    """
    api, istekler = izle()

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(200, content=b"%PDF-1.4",
                              headers={"Content-Type": "application/pdf"})

    api._transport = httpx.MockTransport(handler)
    icerik = await api.bbd_shipment_label(5)

    assert icerik.startswith(b"%PDF")
    assert istekler[-1].url.path == "/api/admin/bbd/shipments/5/label"
    assert istekler[-1].headers["Accept"] == "application/pdf"
