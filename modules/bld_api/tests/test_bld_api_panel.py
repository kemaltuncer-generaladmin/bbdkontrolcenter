"""Panel alanları: yol, gövde, sayfalama, belge zarfı, gerekçe sınırları.

On üç alanın hepsini tek tek sınamak yerine, HER ALANDAN yanlış gitmesi en
pahalı olan davranış seçildi:

  · yolun doğru önekte olması (`/api/control/<alan>`, `/kds` DEĞİL),
  · kısmi yazmada "hiç gönderme" ile "boşalt" ayrımının korunması,
  · sayfalama parametre adının `per_page` olması (kardeş geçitte `limit`
    sanılıp sessiz veri kaybı yaşandı),
  · JSON olmayan yanıtların (CSV, fatura HTML) baytıyla ve başlıklarıyla
    birlikte dönmesi,
  · müşteri okumalarında `actor`'ün zorunlu olması (KVKK),
  · gerekçe üst sınırının yalnız sözleşmenin söylediği yerde uygulanması.

Hiçbir test ağa çıkmaz: `httpx.MockTransport`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from bld_api_backend.client import MAX_AGREED_TOTAL_KURUS, MAX_REASON, BldApi
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway

GEREKCE = "Panel testi için yazılmış yeterli uzunlukta gerekçe"
AKTOR = "Ayşe Yılmaz"


def kaydeden(kutu: list[httpx.Request], payload: Any = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        kutu.append(request)
        return httpx.Response(200, json=payload if payload is not None else {"ok": True})

    return handler


# --------------------------------------------------------------- yol öneki

async def test_panel_uclari_kds_onekine_gitmez() -> None:
    """Panel yolları kendi kovasında; `/kds` önekine düşen bir istek KDS
    bütçesini yer ve mutfağın kasa yönetimini kilitleyebilirdi."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.menu_calendar(date_from="2026-08-17", date_to="2026-08-23")
    await api.products()
    await api.order_list()
    await api.subscriptions()
    await api.monitor_events()
    await api.server_audit()

    yollar = [request.url.path for request in istekler]
    assert yollar == [
        "/api/control/menu/calendar",
        "/api/control/products",
        "/api/control/orders",
        "/api/control/subscriptions",
        "/api/control/monitor/events",
        "/api/control/audit",
    ]
    assert not any("/kds/" in yol for yol in yollar)


async def test_bozuk_tarih_istek_cikmadan_reddedilir() -> None:
    """Yol kuru prova defterine `YYYY-MM-DD` kalıbıyla kayıtlı: bozuk bir
    tarih, "uç sözleşmede yok" gibi görünen bir hataya dönerdi."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.menu_day("2026-8-1")

    assert hata.value.code == "payload"


# ------------------------------------------------------------ kısmi yazma

async def test_gonderilmeyen_alan_govdeye_konmaz_null_konur() -> None:
    """Sözleşme: alanı hiç göndermemek "dokunma", `null` göndermek "boşalt"."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.update_menu_day("2026-08-17", internal_note=None, reason=GEREKCE,
                              actor=AKTOR)

    govde = json.loads(istekler[0].content)
    assert govde["internal_note"] is None
    # Dokunulmayan alanlar gövdede HİÇ yok.
    for alan in ("title", "description", "package_price_kurus", "cutoff_time"):
        assert alan not in govde


async def test_bos_kismi_yazma_istek_cikmadan_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.update_customer(312, reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


async def test_stok_satirinda_item_id_zorunlu() -> None:
    """Liste TAM listedir: `item_id` taşımayan satır, o kalemin tavanını
    sessizce kaldırırdı."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.set_menu_stock("2026-08-17", capacity_total=120,
                                 items=[{"capacity": 60}], reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


# -------------------------------------------------------------- sayfalama

async def test_sayfalama_parametresi_per_page_adiyla_gider() -> None:
    """Kardeş geçitte bu ad `limit`ti ve yanlış ad taramayı SESSİZCE kırpmıştı.
    BLD denetleyicisi `per_page` okuyor (OrderController::index)."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.order_list(page=3, per_page=40)

    params = dict(istekler[0].url.params)
    assert params["page"] == "3"
    assert params["per_page"] == "40"
    assert "limit" not in params


async def test_sayfa_boyu_yuz_uzerinde_kirpilir() -> None:
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.products(per_page=500)

    assert dict(istekler[0].url.params)["per_page"] == "100"


async def test_denetim_izinin_varsayilan_sayfa_boyu_ellidir() -> None:
    """`00-genel.md` §5'in tek istisnası: denetim izi bir tarama ekranıdır."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.server_audit()
    await api.products()

    assert dict(istekler[0].url.params)["per_page"] == "50"
    assert dict(istekler[1].url.params)["per_page"] == "25"


async def test_liste_yaniti_items_ve_meta_olarak_acilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "data": [{"id": 8421}],
            "meta": {"page": 1, "per_page": 25, "total": 137, "last_page": 6},
            "server_time": "2026-08-16T09:00:00Z",
        })

    api, _, _, _ = gateway(handler)
    sonuc = await api.order_list()

    assert sonuc["items"] == [{"id": 8421}]
    assert sonuc["meta"]["last_page"] == 6


async def test_virgullu_suzgec_listeden_de_metinden_de_kurulur() -> None:
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.order_list(status=["yeni", "onaylandi"])
    await api.order_list(status="yeni,onaylandi")

    assert dict(istekler[0].url.params)["status"] == "yeni,onaylandi"
    assert dict(istekler[1].url.params)["status"] == "yeni,onaylandi"


async def test_uc_degerli_bayrak_suzgeci_yok_ile_false_ayrilir() -> None:
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.products()
    await api.products(sold_out=False)
    await api.products(sold_out=True)

    assert "sold_out" not in dict(istekler[0].url.params)
    assert dict(istekler[1].url.params)["sold_out"] == "false"
    assert dict(istekler[2].url.params)["sold_out"] == "true"


# ---------------------------------------------------- JSON olmayan yanıtlar

async def test_csv_disa_aktarim_bayti_ve_basliklariyla_doner() -> None:
    """`content` BAYTTIR: dosya UTF-8 BOM ile başlıyor ve BOM'suz kaydedilen
    bir CSV'yi açan muhasebeci "ğ" yerine kutu görür."""
    govde = "﻿siparis_no,toplam_kurus\r\nBLD-8421,216000\r\n".encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=govde, headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="bld-siparisler.csv"',
            "X-Total-Rows": "137",
            "X-Truncated": "false",
        })

    api, _, _, _ = gateway(handler)
    belge = await api.export_orders(date_from="2026-08-01", date_to="2026-08-16")

    assert belge["content"] == govde
    assert belge["content"].startswith(b"\xef\xbb\xbf")
    assert belge["filename"] == "bld-siparisler.csv"
    assert belge["total_rows"] == 137
    assert belge["truncated"] is False
    assert belge["content_type"] == "text/csv"


async def test_kesilmis_disa_aktarim_hata_degil_bayrakla_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x", headers={
            "Content-Type": "text/csv", "X-Truncated": "true", "X-Total-Rows": "20000",
        })

    api, _, _, _ = gateway(handler)
    belge = await api.export_orders(max_rows=20000)

    assert belge["truncated"] is True


async def test_fatura_html_metin_olarak_doner() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Accept"] == "*/*"
        return httpx.Response(200, content="<html>İPTAL</html>".encode(),
                              headers={"Content-Type": "text/html; charset=utf-8"})

    api, _, _, _ = gateway(handler)
    belge = await api.invoice_html(44)

    assert "İPTAL" in belge["text"]
    assert belge["content_type"] == "text/html"


# ------------------------------------------------------------------- KVKK

async def test_musteri_okumasi_aktorsuz_yapilamaz() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    for cagri in (
        lambda: api.customers(actor=""),
        lambda: api.customer(312, actor="A"),
        lambda: api.customer_orders(312, actor=""),
        lambda: api.customer_addresses(312, actor=" "),
    ):
        with pytest.raises(BldApiError) as hata:
            await cagri()
        assert hata.value.code == "actor_required"


async def test_musteri_okumasinda_aktor_sorgu_dizesine_konur() -> None:
    """Sunucu bu alanı sorgu dizesinde bekliyor ve eksikse 422 veriyor;
    imzaya girmemesi sözleşmede yazılı ve bilinçli."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"data": [], "meta": {}}))

    await api.customers(actor=AKTOR, q="acme")

    params = dict(istekler[0].url.params)
    assert params["actor"] == AKTOR
    assert params["q"] == "acme"


# --------------------------------------------------------------- gerekçe

async def test_panel_gerekcesi_bes_yuz_karakteri_asamaz() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.publish_menu_day("2026-08-17", reason="A" * (MAX_REASON + 1),
                                   actor=AKTOR)

    assert hata.value.code == "reason_required"


async def test_sipariş_iptalinde_gerekce_siniri_yuz_altmis() -> None:
    """`veykemtu_order_revisions.reason` sütunu 160; sözleşme bu üç uçta
    daha dar bir sınır söylüyor."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.cancel_order(8421, reason="A" * 161, actor=AKTOR)

    assert hata.value.code == "reason_required"


async def test_kds_uclarina_bes_yuz_karakter_siniri_tasinmadi() -> None:
    """K-21 sözleşmesi cihaz uçlarında bir üst sınır SÖYLEMİYOR; uydurulmuş
    bir sınır, bugün çalışan bir çağrıyı yarın reddederdi."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.revoke_device(3, reason="A" * (MAX_REASON + 50), actor=AKTOR)

    assert istekler, "istek gönderilmeliydi"


# -------------------------------------------------------- gövde denetimleri

async def test_cari_hesap_odeme_kipi_kabul_edilmez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_subscription(customer_id=312, start_date="2026-09-01",
                                      service_days=[1], default_quantity=20,
                                      payment_mode="account", reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


async def test_gunun_menusu_kipinde_sabit_liste_gonderilmez() -> None:
    """`menu_mode = daily_menu` iken `lines` sunucuda 422 üretir; menü o günün
    yayınlanmış menüsünden gelir."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.create_subscription(customer_id=312, start_date="2026-09-01",
                                  service_days=[1, 2], default_quantity=20,
                                  location_id=1, lines=[{"menu_id": 27}],
                                  reason=GEREKCE, actor=AKTOR)

    assert "lines" not in json.loads(istekler[0].content)


async def test_subesiz_abonelik_istegi_hic_cikmaz() -> None:
    """`location_id` BLD'de zorunlu; eksikliği istek gitmeden kesilir.

    Bu eksiklik uzun süre 422 üretiyordu ve abonelik Kontrol Merkezi'nden HİÇ
    açılamıyordu. Sessizce bir varsayılan koymak daha kötü olurdu: yanlış şube,
    siparişleri başka bir mutfağa yollar.
    """
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_subscription(customer_id=312, start_date="2026-09-01",
                                      service_days=[1], default_quantity=20,
                                      reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


async def test_sube_listesi_gecitten_okunur() -> None:
    """Şube seçicinin kaynağı — `GET /settings/locations`."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {
        "data": [{"id": 1, "name": "BLD Merkez Mutfak", "enabled": True}],
        "meta": {"default_location_id": 1},
    }))

    cevap = await api.locations()

    assert cevap["items"][0]["id"] == 1
    assert cevap["meta"]["default_location_id"] == 1
    assert istekler[0].url.path.endswith("/settings/locations")


async def test_atla_ve_adet_birlikte_gonderilemez() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_subscription_exception(18, service_date="2026-08-20", skip=True,
                                                quantity_override=12, reason=GEREKCE,
                                                actor=AKTOR)

    assert hata.value.code == "payload"


async def test_fatura_iki_kipten_birini_ister() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    for cagri in (
        lambda: api.create_invoice(reason=GEREKCE, actor=AKTOR),
        lambda: api.create_invoice(order_id=8421, subscription_id=18, reason=GEREKCE,
                                   actor=AKTOR),
    ):
        with pytest.raises(BldApiError) as hata:
            await cagri()
        assert hata.value.code == "payload"


async def test_kapatilamayan_duyuru_yalniz_kritik_seviyede() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_notification(title="Bakım", body="Metin", level="info",
                                      dismissible=False, reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


async def test_duyuru_dugmesi_etiket_ve_adres_birlikte_ister() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.create_notification(title="Bakım", body="Metin",
                                      action_url="https://ornek.test", reason=GEREKCE,
                                      actor=AKTOR)

    assert hata.value.code == "payload"


async def test_deneme_smsi_sablon_veya_metin_ister_ikisi_birden_degil() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    for cagri in (
        lambda: api.send_test_sms(phone="5321234567", reason=GEREKCE, actor=AKTOR),
        lambda: api.send_test_sms(phone="5321234567", template_key="order_created",
                                  body="metin", reason=GEREKCE, actor=AKTOR),
    ):
        with pytest.raises(BldApiError) as hata:
            await cagri()
        assert hata.value.code == "payload"


async def test_tanınmayan_odeme_yontemi_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.mark_subscription_payment_paid(41, method="account", reason=GEREKCE,
                                                 actor=AKTOR)

    assert hata.value.code == "payload"


async def test_bos_revizyon_listesi_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await api.revise_order(8421, items=[], reason=GEREKCE, actor=AKTOR)

    assert hata.value.code == "payload"


# ------------------------------------------- elle sipariş (`POST /orders`)

async def test_elle_siparis_yolu_fiili_ve_govdesi() -> None:
    """Sözleşmenin gövdesi birebir gider — alan uydurulmaz, alan düşürülmez."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"ok": True, "data": {"id": 8421}}))

    await api.create_order(
        customer_id=312, service_date="2026-08-18", delivery_type="delivery",
        address={"line1": "Örnek Mah. 12. Sk No:3", "district": "Selçuklu",
                 "city": "Konya", "note": "Zili çalmayın"},
        payment_method="cash",
        items=[{"menu_id": 88, "quantity": 12, "option_value_ids": [7], "note": None}],
        customer_note="Fatura kuruma kesilecek", actor=AKTOR,
    )

    assert istekler[0].method == "POST"
    # KDS önekine DÜŞMEZ: `control/kds` altında bu ucun karşılığı yok, mutfak
    # kasası sipariş açmaz.
    assert istekler[0].url.path == "/api/control/orders"
    govde = json.loads(istekler[0].content)
    assert govde["customer_id"] == 312
    assert govde["service_date"] == "2026-08-18"
    assert govde["delivery_type"] == "delivery"
    assert govde["payment_method"] == "cash"
    assert govde["address"]["note"] == "Zili çalmayın"
    assert govde["customer_note"] == "Fatura kuruma kesilecek"
    # KALEM OLDUĞU GİBİ: `option_value_ids` düşseydi "ekstra peynir" silinir,
    # sipariş ucuzlar, mutfak yanlış yemeği yapardı.
    assert govde["items"] == [
        {"menu_id": 88, "quantity": 12, "option_value_ids": [7], "note": None},
    ]
    # SÖZLEŞMEDE OLMAYAN ALAN UYDURULMAZ: saati sunucu çözüyor.
    assert "requested_at" not in govde


async def test_elle_sipariste_kuru_prova_bayragi_govdeye_konur() -> None:
    """Yol defterde: bayrak telde gider ve istek gerçekten çıkar.

    Defterde olmasaydı istek HİÇ gönderilmez, ekran "prova geçti" sanırdı.
    """
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler, {"ok": True, "dry_run": True}))

    sonuc = await api.create_order(
        customer_id=312, service_date="2026-08-18", delivery_type="pickup",
        payment_method="online", items=[{"menu_id": 88, "quantity": 2}],
        actor=AKTOR, dry_run=True,
    )

    assert sonuc.get("sent") is not False
    assert json.loads(istekler[0].content)["dry_run"] is True


async def test_anlasmali_sepet_tutari_govdeye_YALNIZ_doluyken_konur() -> None:
    """`None` = anlaşma yok ve bu, alanın hiç gönderilmemesiyle EŞDEĞER.

    `null` yollamak denetim izine hiçbir şey anlatmayan bir alan yazdırırdı;
    sunucu da ikisini eşdeğer sayıyor (`isset()`).
    """
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    ortak = {"customer_id": 312, "service_date": "2026-08-18",
             "delivery_type": "pickup", "payment_method": "cash",
             "items": [{"menu_id": 88, "quantity": 2}], "actor": AKTOR}

    await api.create_order(**ortak)
    assert "agreed_total_kurus" not in json.loads(istekler[0].content)

    # 400,00 ₺ → 40000 kuruş. Telde de ekranda da kuruş: bölme yok.
    await api.create_order(**ortak, agreed_total_kurus=40000)
    assert json.loads(istekler[1].content)["agreed_total_kurus"] == 40000


async def test_yeni_musteri_kipinde_kimlik_gonderilmez() -> None:
    """Telefonda ilk kez arayan: `customer` nesnesi, `customer_id` YOK."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.create_order(
        customer={"name": "Acme Gıda", "phone": "0532 123 45 67"},
        service_date="2026-08-18", delivery_type="pickup", payment_method="cash",
        items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR,
    )

    govde = json.loads(istekler[0].content)
    assert govde["customer"] == {"name": "Acme Gıda", "phone": "0532 123 45 67"}
    assert "customer_id" not in govde


async def test_alma_sipariste_adres_govdeye_konmaz() -> None:
    """`pickup` siparişte adres alanı hiç gitmez: sunucu onu zaten `null` yapıyor
    ve göndermek denetim izine hiç kullanılmayan bir adres yazdırırdı."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.create_order(
        customer_id=312, service_date="2026-08-18", delivery_type="pickup",
        payment_method="cash", items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR,
    )

    assert "address" not in json.loads(istekler[0].content)


@pytest.mark.parametrize("cagri", [
    # Cari hesap iş modelinden kalktı; sunucu 422 verirdi.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="pickup", payment_method="account",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Müşterisiz: sipariş kimin hesabına yazılacağı belirsiz kalırdı.
    lambda api: api.create_order(service_date="2026-08-18", delivery_type="pickup",
                                 payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # İkisi birden: sunucu `customer_id`'yi seçer, `customer` sessizce yok
    # sayılır ve ekran yeni müşteri açtığını sanırdı.
    lambda api: api.create_order(customer_id=312, customer={"name": "Acme",
                                                            "phone": "5321234567"},
                                 service_date="2026-08-18", delivery_type="pickup",
                                 payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Numarasız yeni müşteri: yer tutucu e-posta telefondan türüyor.
    lambda api: api.create_order(customer={"name": "Acme", "phone": " "},
                                 service_date="2026-08-18", delivery_type="pickup",
                                 payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Kalemsiz sipariş mutfağa boş bir fiş olarak düşerdi.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="pickup", payment_method="cash",
                                 items=[], actor=AKTOR),
    # Teslimat var, adres yok: kurye nereye götüreceğini bilemezdi.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="delivery", payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Adres eksik: `city` boş.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="delivery",
                                 address={"line1": "Örnek Mah.", "district": "Selçuklu"},
                                 payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Bozuk servis günü.
    lambda api: api.create_order(customer_id=312, service_date="18.08.2026",
                                 delivery_type="pickup", payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR),
    # Sıfır anlaşmalı tutar: "bedava sipariş" bir fiyat kararı değil, boş
    # bırakılmış bir kutudur. Sunucu da `min:1` diyor.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="pickup", payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR,
                                 agreed_total_kurus=0),
    # Tavan aşımı: fazladan basılmış sıfırlara karşı akıl sınırı.
    lambda api: api.create_order(customer_id=312, service_date="2026-08-18",
                                 delivery_type="pickup", payment_method="cash",
                                 items=[{"menu_id": 88, "quantity": 2}], actor=AKTOR,
                                 agreed_total_kurus=MAX_AGREED_TOTAL_KURUS + 1),
], ids=["odeme", "musterisiz", "iki-musteri", "numarasiz", "kalemsiz", "adressiz",
        "eksik-adres", "bozuk-gun", "sifir-anlasma", "asiri-anlasma"])
async def test_elle_siparis_govdesi_istek_cikmadan_denetlenir(cagri: Any) -> None:
    """Hatalı gövde hız kovasından pay HARCAMADAN durur ve hata anlaşılır olur."""
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _, _ = gateway(handler)
    with pytest.raises(BldApiError) as hata:
        await cagri(api)

    assert hata.value.code == "payload"


async def test_abonelik_alt_yollari_kimlik_sanilmaz() -> None:
    """Sunucuda sabit parçalı yollar `{subscription}` önünde kayıtlı olmalı;
    istemci tarafındaki karşılığı, kimliğin o parçaların yerine konmaması."""
    istekler: list[httpx.Request] = []
    api, _, _, _ = gateway(kaydeden(istekler))

    await api.release_subscription_order(8455, reason=GEREKCE, actor=AKTOR)
    await api.mark_subscription_payment_paid(41, method="cash", reason=GEREKCE,
                                             actor=AKTOR)
    await api.cancel_subscription_contract(7, reason=GEREKCE, actor=AKTOR)

    assert [request.url.path for request in istekler] == [
        "/api/control/subscriptions/orders/8455/release",
        "/api/control/subscriptions/payments/41/mark-paid",
        "/api/control/subscriptions/contracts/7/cancel",
    ]


def test_yazma_metotlari_kds_ve_panel_yollarini_karistirmiyor() -> None:
    """KDS metotları duruyor: `bld_kds` onlara bağlı ve adları değişmedi."""
    for ad in ("orders", "order", "order_revisions", "create_order_revision",
               "set_order_status", "devices", "overview", "print_jobs"):
        assert hasattr(BldApi, ad)
