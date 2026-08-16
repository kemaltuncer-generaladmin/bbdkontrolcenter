"""Okuma önbelleği: NEYİN önbelleğe alındığı ve — asıl önemlisi — neyin ALINMADIĞI.

Önbellek burada bir hız numarası değil, tek hız kovasının (18/dk) on iki panel
ekranını taşımasının yolu. Ama yanlış veriyi önbelleğe almak, kazandığından
çok daha pahalıya patlar: "kaydettim ama listede yok" diyen bir personel,
kaydı ikinci kez yazar.

BU YÜZDEN KURAL DAR: önbellek yalnızca REFERANS veri içindir (kategori, ödeme
yöntemleri/ayar varsayılanları, seçici ürün kataloğu, denetim eylem sözlüğü).
Sipariş, stok sayısı, müşteri, abonelik ve fatura listesi ASLA önbelleğe
alınmaz. Aşağıdaki testlerin yarısı bu olumsuz iddiayı kanıtlar.

Hiçbir test ağa çıkmaz.
"""

from __future__ import annotations

import json

import httpx
import pytest
from bld_api_backend.errors import BldApiError
from bld_api_fakes import gateway

GEREKCE = "Önbellek testi için yazılmış yeterli uzunlukta gerekçe"
AKTOR = "Ayşe Yılmaz"


def sayan(kutu: list[str], payload: object) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        kutu.append(request.url.path)
        return httpx.Response(200, json=payload)

    return handler


# ------------------------------------------------ ÖNBELLEĞE ALINMAYANLAR

@pytest.mark.parametrize("cagri_adi", [
    "order_list", "menu_stock", "customers", "subscriptions", "invoices",
    "sms_templates", "monitor_events", "dashboard_overview", "sales_settings",
])
async def test_canli_veri_onbellege_alinmaz(cagri_adi: str) -> None:
    """İkinci çağrı sunucuya GİTMELİ. Bayat bir sipariş listesi, bayat bir
    stok sayısı ya da bayat bir satış şalteri, kazandığı istekten çok daha
    pahalıya patlar."""
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": [], "meta": {}}))

    cagrilar = {
        "order_list": lambda: api.order_list(),
        "menu_stock": lambda: api.menu_stock("2026-08-17"),
        "customers": lambda: api.customers(actor=AKTOR),
        "subscriptions": lambda: api.subscriptions(),
        "invoices": lambda: api.invoices(),
        "sms_templates": lambda: api.sms_templates(),
        "monitor_events": lambda: api.monitor_events(),
        "dashboard_overview": lambda: api.dashboard_overview(),
        "sales_settings": lambda: api.sales_settings(),
    }
    await cagrilar[cagri_adi]()
    await cagrilar[cagri_adi]()

    assert len(yollar) == 2, f"{cagri_adi} önbelleğe alınmış — alınmamalıydı"


# --------------------------------------------------- ÖNBELLEĞE ALINANLAR

async def test_kategori_listesi_onbellekten_doner() -> None:
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": [{"category_id": 3}]}))

    birinci = await api.categories()
    ikinci = await api.categories()

    assert len(yollar) == 1
    assert birinci == ikinci


async def test_denetim_eylem_sozlugu_onbellekten_doner() -> None:
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": [{"action": "menu.publish"}]}))

    await api.audit_actions()
    await api.audit_actions()

    assert len(yollar) == 1


async def test_ayar_referansi_yalniz_meta_tutar() -> None:
    """`GET /settings/sales` gövdesinde CANLI şalterler var; önbelleğe yalnız
    `meta` (ödeme yöntemleri + varsayılanlar) alınır."""
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {
        "data": {"ordering_enabled": True, "order_cutoff": "08:00"},
        "meta": {"available_payment_methods": ["online", "cash"],
                 "defaults": {"max_lookahead_days": 7}},
    }))

    referans = await api.settings_reference()
    await api.settings_reference()

    assert len(yollar) == 1
    assert referans["available_payment_methods"] == ["online", "cash"]
    assert "ordering_enabled" not in referans


async def test_secici_katalogu_butun_sayfalari_toplar() -> None:
    """Sayfa boyu doluysa tarama devam eder; `last_page` snake_case okunur —
    kardeş geçitte camelCase beklenip tarama ilk sayfada bitmişti."""
    yollar: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        yollar.append(str(request.url))
        sayfa = int(dict(request.url.params).get("page", "1"))
        return httpx.Response(200, json={
            "data": [{"menu_id": sayfa}],
            "meta": {"page": sayfa, "per_page": 100, "total": 3, "last_page": 3},
        })

    api, _, _, _ = gateway(handler)
    katalog = await api.product_picker()

    assert len(yollar) == 3
    assert katalog["total"] == 3
    assert katalog["truncated"] is False


# ---------------------------------------------- yazma önbelleği düşürür

async def test_urun_yazmasi_secici_katalogunu_dusurur() -> None:
    """TTL'in dolmasını beklemek, yeni eklenen ürünü on beş dakika seçicide
    görünmez kılardı — tam da "kaydettim ama listede yok" arızası."""
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": [], "meta": {}}))

    await api.product_picker()
    await api.product_picker()
    assert len(yollar) == 1

    await api.create_product(name="Karnıyarık", price_kurus=9500, reason=GEREKCE,
                             actor=AKTOR)
    await api.product_picker()

    # 1 (ilk okuma) + 1 (yazma) + 1 (önbellek düştüğü için yeniden okuma)
    assert len(yollar) == 3


async def test_kategori_yazmasi_kategori_dalini_dusurur() -> None:
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": [], "meta": {}}))

    await api.categories()
    await api.update_category(3, priority=20, reason=GEREKCE, actor=AKTOR)
    await api.categories()

    assert len(yollar) == 3


async def test_ayar_yazmasi_ayar_referansini_dusurur() -> None:
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": {}, "meta": {}}))

    await api.settings_reference()
    await api.pause_ordering(reason=GEREKCE, actor=AKTOR)
    await api.settings_reference()

    assert len(yollar) == 3


# -------------------------------------------------- L2 anlık görüntü (K7)

async def test_anlik_goruntu_depoya_yazilir_ve_surec_yeniden_baslasa_da_durur() -> None:
    yollar: list[str] = []
    api, depo, _, _ = gateway(sayan(yollar, {"data": [], "meta": {}}))

    await api.reference_snapshot()
    assert "reference" in depo.snapshot
    ilk_istek_sayisi = len(yollar)
    assert ilk_istek_sayisi == 4  # kategori + ayar + ürün kataloğu + eylem sözlüğü

    # "Süreç yeniden başladı": yeni geçit, aynı depo. L1 boş, L2 dolu.
    yeni_yollar: list[str] = []
    yeni, _, _, _ = gateway(sayan(yeni_yollar, {"data": [], "meta": {}}))
    yeni._snapshot = api._snapshot
    goruntu = await yeni.reference_snapshot()

    assert yeni_yollar == []
    assert goruntu["stale"] is False
    assert set(goruntu) >= {"categories", "settings", "products", "audit_actions"}


async def test_bld_erisilemezken_son_bilinen_hal_bayat_isaretiyle_doner() -> None:
    """K7: ekran ayakta kalır ama verinin bayat olduğunu GİZLEMEZ."""
    calisan: list[str] = []
    api, _, _, _ = gateway(sayan(calisan, {"data": [{"category_id": 3}]}))
    await api.reference_snapshot()

    def kopuk(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("ağ koptu")

    cevrimdisi, _, gunluk, _ = gateway(kopuk)
    cevrimdisi._snapshot = api._snapshot
    goruntu = await cevrimdisi.reference_snapshot(refresh=True)

    assert goruntu["stale"] is True
    assert goruntu["errors"], "hangi parçaların alınamadığı söylenmeli"
    assert goruntu["categories"]["items"] == [{"category_id": 3}]
    assert "referans görüntüsü eksik" in gunluk.text()


async def test_anlik_goruntu_yokken_erisim_hatasi_yukari_gider() -> None:
    """Bayat veri iyi, UYDURMA veri değil: hiç görüntü yoksa hata gizlenmez."""
    def kopuk(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("ağ koptu")

    api, _, _, _ = gateway(kopuk)
    with pytest.raises(BldApiError) as hata:
        await api.reference_snapshot()

    assert hata.value.code == "transport"


async def test_eksik_goruntu_tam_olanin_uzerine_yazilmaz() -> None:
    calisan: list[str] = []
    api, depo, _, _ = gateway(sayan(calisan, {"data": [{"category_id": 3}]}))
    await api.reference_snapshot()
    onceki = json.loads(depo.snapshot["reference"][0])

    def kopuk(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("ağ koptu")

    cevrimdisi, _, _, _ = gateway(kopuk)
    cevrimdisi._snapshot = api._snapshot
    await cevrimdisi.reference_snapshot(refresh=True)

    assert json.loads(depo.snapshot["reference"][0]) == onceki


async def test_onbellek_kapatilabilir() -> None:
    yollar: list[str] = []
    api, _, _, _ = gateway(sayan(yollar, {"data": []}), reference_ttl=0)

    await api.categories()
    await api.categories()

    assert len(yollar) == 2
