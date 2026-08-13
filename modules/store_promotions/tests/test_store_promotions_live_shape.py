"""CANLI MAĞAZANIN GERÇEK YANIT BİÇİMİ — denetimde bulunan kusurların nöbeti.

Buradaki her test, `bbdstore.com.tr` üzerinde SALT OKUMA ile doğrulanmış bir
gerçeği koruyor. Modülün ilk hâli mağaza yanıtını snake_case sanıyordu; canlıda
API kaynağı camelCase yayınlıyor. Sonuç sessizdi: ekran açılıyor, tablo "—" ile
doluyor, her kural "Duraklatıldı" görünüyor ve kaydet düğmesi kaydı boşaltıyordu.

Doğrulanan uçlar ve gözlenen biçim:
  GET /api/admin/marketing/cart-rules            → startsFrom · couponType · timesUsed …
  GET /api/admin/marketing/cart-rules/{id}       → conditions/channels DOLU (listede null)
  GET /api/admin/marketing/cart-rules/{id}/coupons → usageLimit · timesUsed · expiredAt
  GET /api/admin/orders                          → grandTotal · couponCode, indirim YOK
  GET /api/admin/orders/{id}                     → discountAmount BURADA
  GET /api/admin/products?query=…                → `name` yok sayılıyor, `query` süzüyor
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_promotions_backend import analytics, promotions
from store_promotions_backend.service import PromotionsService
from store_promotions_fakes import KURAL, KURAL_LISTE_SATIRI, FakeApi, FakeLog, FakeStore

BUGUN = "2026-08-13"
GEREKCE = "Canlı biçim denetimi için gerekçe metni"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[PromotionsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = PromotionsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "page_size": 50, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ================================================ TUZAK 8 — iki alfabe


def test_camelcase_kural_satiri_bos_degil_dolu_okunur() -> None:
    row = promotions.cart_rule_row(KURAL, today=BUGUN)
    assert row["name"] == "Eylül kampanyası"
    assert row["code"] == "EYLUL"
    assert row["couponType"] == "specific"
    assert row["startsFrom"] == "2026-08-01"
    assert row["endsTill"] == "2026-09-30"
    assert row["status"] == "active", "takvim içindeki aktif kural 'Duraklatıldı' görünemez"
    assert row["value"] == 10.0
    assert row["valueLabel"] == "%10"
    assert row["usage"]["used"] == 12
    assert row["usage"]["limit"] == 100
    assert row["priority"] == 1


def test_snake_case_kayit_da_okunmaya_devam_eder() -> None:
    """Geriye dönük: eski/rehber biçimini de okuyabilmeliyiz."""
    raw = {"id": 3, "name": "Eski", "status": 1, "starts_from": "2026-01-01",
           "action_type": "by_percent", "discount_amount": "5", "times_used": 2}
    row = promotions.cart_rule_row(raw, today=BUGUN)
    assert row["status"] == "active"
    assert row["value"] == 5.0
    assert row["usage"]["used"] == 2


def test_ucretsiz_kargo_free_shipping_alanindan_okunur() -> None:
    """`apply_free_shipping` diye bir sütun YOK; canlıda `freeShipping` geliyor."""
    row = promotions.cart_rule_row({**KURAL, "freeShipping": 1}, today=BUGUN)
    assert row["freeShipping"] is True
    assert promotions.cart_rule_row(KURAL, today=BUGUN)["freeShipping"] is False


def test_musteri_basina_limit_usage_per_customer_alanindan_gelir() -> None:
    detail = promotions.cart_rule_detail(KURAL, today=BUGUN)
    assert detail["limits"]["perCustomer"] == 1
    assert detail["limits"]["perCoupon"] == 100


def test_camelcase_kupon_satiri_kullanimi_gorur() -> None:
    row = promotions.coupon_row({"id": 9, "code": "BBD-A1", "timesUsed": 4, "usageLimit": 4,
                                 "usagePerCustomer": 2, "expiredAt": None}, today=BUGUN)
    assert row["usage"]["used"] == 4
    assert row["usage"]["exhausted"] is True
    assert row["perCustomer"] == 2
    assert row["removable"] is False, "kullanılmış kupon kaldırılabilir görünemez"


# ============================== TUZAK 8 — oku-değiştir-yaz gövdeyi boşaltmasın


def test_yazma_govdesi_camelcase_kayittan_kurulur_ve_snake_case_gider() -> None:
    body = promotions.write_rule_body(KURAL, {"name": "Ekim kampanyası"})
    assert body["name"] == "Ekim kampanyası"
    # Dokunulmayan alanlar TAŞINMALI: eksik alan bu uçta sıfırlanıyor.
    assert body["uses_per_coupon"] == 100
    assert body["usage_per_customer"] == 1
    assert body["action_type"] == "by_percent"
    assert body["discount_amount"] == 10
    assert body["sort_order"] == 1
    assert body["coupon_type"] == 1
    assert body["status"] == 1
    assert body["channels"] == [1]
    assert body["customer_groups"] == [2]
    assert body["conditions"][0]["attribute"] == "cart|base_sub_total"
    # Gövdede TEK BİR camelCase anahtar bulunmamalı.
    assert not [key for key in body if any(ch.isupper() for ch in key)]


def test_ucretsiz_kargo_dogru_sutuna_yazilir() -> None:
    body = promotions.write_rule_body(KURAL, {"action": {"kind": "by_percent", "value": 10,
                                                         "freeShipping": True}})
    assert body["free_shipping"] == 1
    assert "apply_free_shipping" not in body, "olmayan sütun gövdeye konmaz"
    # Mağaza yöneticisinin ayarı taşınır, sıfırlanmaz.
    assert body["apply_to_shipping"] == 0
    assert body["uses_attribute_conditions"] == 0


def test_musteri_basina_limit_dogru_sutuna_yazilir() -> None:
    body = promotions.write_rule_body(KURAL, {"limits": {"perCustomer": 3}})
    assert body["usage_per_customer"] == 3
    assert "uses_per_customer" not in body


def test_ust_sinir_alani_canlida_yok_ve_kapali_gelir() -> None:
    """Canlı kural kaydı `maxDiscountAmount` taşımıyor — alan yazılmamalı."""
    detail = promotions.cart_rule_detail(KURAL, today=BUGUN)
    assert detail["action"]["maxDiscountSupported"] is False
    body = promotions.write_rule_body(KURAL, {"action": {"kind": "by_percent",
                                                         "maxDiscount": 5000}})
    assert promotions.MAX_DISCOUNT_KEY not in body


def test_alan_camelcase_geldiginde_de_desteklenmis_sayilir() -> None:
    raw = {**KURAL, "maxDiscountAmount": "20.00"}
    detail = promotions.cart_rule_detail(raw, today=BUGUN)
    assert detail["action"]["maxDiscountSupported"] is True
    assert detail["action"]["maxDiscount"] == 2000


# ================== canlıdaki ücretsiz kargo kampanyaları (kural #4, #5)

#: Canlı kayıt: indirim yok, kargo bedava. `by_percent` + `discountAmount: 0`.
KARGO_KURALI = {
    "id": 4, "name": "750 TL Üzeri Ücretsiz Kargo", "status": 1,
    "startsFrom": None, "endsTill": None, "couponType": 0, "timesUsed": 4,
    "usesPerCoupon": 0, "usagePerCustomer": 0, "conditionType": 1,
    "conditions": [{"attribute": "cart|base_sub_total", "attribute_type": "price",
                    "operator": ">=", "value": "750"}],
    "actionType": "by_percent", "discountAmount": 0, "discountQuantity": 0,
    "discountStep": "0", "applyToShipping": 0, "freeShipping": 1,
    "endOtherRules": 0, "usesAttributeConditions": 0, "sortOrder": 1,
    "channels": [{"id": 1}], "customerGroups": [{"id": 1}, {"id": 2}, {"id": 3}],
}


def test_kargo_kampanyasi_tabloda_yuzde_sifir_yazmaz() -> None:
    row = promotions.cart_rule_row(KARGO_KURALI, today=BUGUN)
    assert row["valueLabel"] == "ücretsiz kargo"
    assert row["freeShipping"] is True


def test_kargo_kampanyasinin_adi_degistirilebilir() -> None:
    """Sıfır indirimi koşulsuz reddeden doğrulama, canlıdaki iki kampanyanın
    adını bile değiştirilemez yapıyordu."""
    body = promotions.write_rule_body(KARGO_KURALI, {"name": "Kargo bedava"})
    assert promotions.rule_error(body) == ""
    assert body["free_shipping"] == 1


def test_sifir_indirim_ve_kargo_kapaliysa_kural_reddedilir() -> None:
    body = promotions.write_rule_body(
        KARGO_KURALI, {"action": {"kind": "by_percent", "value": 0, "freeShipping": False}})
    assert "hiçbir şey yapmaz" in promotions.rule_error(body)


async def test_kargo_kampanyasi_simulasyonda_kargoyu_bedava_yapar() -> None:
    api = FakeApi(rules={4: dict(KARGO_KURALI)})
    service, _, _ = _service(api)
    cart = {"items": [{"name": "Set", "price": 80000, "qty": 1}], "shipping": 3000}
    result = await service.simulate(cart=cart)
    assert result["result"]["freeShipping"] is True
    assert result["result"]["payable"] == 80000, "kargo bedeli düşmeli"


# ======================== liste ucu kapsamı ve koşulu vermiyor


async def test_liste_kapsam_vermediginde_kanal_suzgeci_kural_yutmaz() -> None:
    service, _, _ = _service()
    result = await service.rules(channel_id=99)
    assert result["scopeFilterable"] is False, "liste kanal vermiyor; ekran bunu bilmeli"
    assert len(result["items"]) == 1, "bilinmeyen kapsam 'eşleşmedi' sayılamaz"


async def test_kapsam_bilindiginde_kanal_suzgeci_gercekten_suzer() -> None:
    api = FakeApi()
    api.cart_rules = _sabit_liste([{**KURAL, "channels": [{"id": 1}]}])  # type: ignore[method-assign]
    service, _, _ = _service(api)
    assert (await service.rules(channel_id=1))["items"] != []
    assert (await service.rules(channel_id=2))["items"] == []


def _sabit_liste(items: list[dict[str, Any]]) -> Any:
    async def call(filters: Any = None, **_: Any) -> dict[str, Any]:
        return {"items": items, "meta": {}}
    return call


async def test_simulasyon_kosullari_tekil_kayittan_okur() -> None:
    """Liste `conditions: null` veriyor; buna bakan simülasyon 'koşul yok' sanıp
    "500 TL üzeri" kuralını 1 TL'lik sepete uygulardı."""
    service, api, _ = _service()
    cart = {"items": [{"name": "Kalem", "price": 1000, "qty": 1}]}
    result = await service.simulate(cart=cart, coupon="EYLUL")
    assert api.used("cart_rule"), "aktif kuralın ayrıntısı tekil okunmalı"
    assert result["result"]["applied"] == []
    assert "sepet tutarı" in result["result"]["skipped"][0]["reason"]


async def test_simulasyon_kosul_tutunca_indirimi_hesaplar() -> None:
    service, _, _ = _service()
    cart = {"items": [{"name": "Set", "price": 60000, "qty": 1}]}
    result = await service.simulate(cart=cart, coupon="EYLUL")
    assert result["result"]["discount"] == 6000        # %10 × 600,00 ₺
    assert result["partialRules"] == 0


async def test_kural_ayrintisi_okunamazsa_simulasyon_bunu_soyler() -> None:
    service, api, _ = _service()
    api.fail.add("cart_rule")
    result = await service.simulate(cart={"items": [{"price": 1000, "qty": 1}]})
    assert result["ok"] is True                       # K7 — ekran ayakta
    assert result["partialRules"] == 1
    assert "hesaba KATILMADI" in result["notice"]


def test_liste_satiri_kapsam_bilinmiyor_isaretlenir() -> None:
    assert promotions.cart_rule_row(KURAL_LISTE_SATIRI, today=BUGUN)["scopeKnown"] is False
    assert promotions.cart_rule_row(KURAL, today=BUGUN)["scopeKnown"] is True


# ============================ sipariş listesi indirim tutarı taşımıyor


async def test_indirim_tutari_siparis_detayindan_tamamlanir() -> None:
    api = FakeApi()
    api.orders_payload = {"items": [
        {"id": 11, "couponCode": "EYLUL", "grandTotal": 200, "status": "processing",
         "createdAt": "2026-08-01 10:00:00"},
    ], "meta": {}}
    api.order_details = {11: {"id": 11, "couponCode": "EYLUL", "grandTotal": 200,
                              "discountAmount": 20}}
    service, api, _ = _service(api)
    result = await service.performance(start="2026-08-01", end="2026-08-13")
    assert result["totals"]["couponRevenue"] == 20000
    assert result["totals"]["couponDiscount"] == 2000
    assert result["discountComplete"] is True
    assert api.used("order"), "indirim için sipariş detayı okunmalı"


async def test_indirim_okunamazsa_sifir_yazilmaz_eksik_denir() -> None:
    api = FakeApi()
    api.orders_payload = {"items": [
        {"id": 12, "couponCode": "EYLUL", "grandTotal": 200, "status": "processing",
         "createdAt": "2026-08-01 10:00:00"},
    ], "meta": {}}
    api.fail.add("order")
    service, _, _ = _service(api)
    result = await service.performance(start="2026-08-01", end="2026-08-13")
    assert result["discountComplete"] is False
    assert result["totals"]["costRatio"] is None, "eksik veriyle oran hesaplanamaz"
    assert result["rows"][0]["discountKnown"] is False


async def test_kuponsuz_siparis_icin_detay_okunmaz() -> None:
    api = FakeApi()
    api.orders_payload = {"items": [
        {"id": 13, "couponCode": None, "grandTotal": 100, "status": "processing",
         "createdAt": "2026-08-01 10:00:00"},
    ], "meta": {}}
    service, api, _ = _service(api)
    await service.performance(start="2026-08-01", end="2026-08-13")
    assert api.used("order") == [], "kuponsuz sipariş için ek istek atılmaz"


def test_camelcase_siparis_alanlari_toplanir() -> None:
    rows = analytics.coupon_performance([
        {"id": 1, "couponCode": "A", "grandTotal": "50.00", "discountAmount": "5.00",
         "status": "processing", "createdAt": "2026-08-01"},
    ])
    assert rows["totals"]["couponRevenue"] == 5000
    assert rows["totals"]["couponDiscount"] == 500


# ================== kupon taraması: sunucu sayfa boyunu 50'ye kırpıyor


async def test_kupon_sayfa_boyu_sunucunun_kabul_ettigi_degerdir() -> None:
    """`per_page=200` istemek işe yaramıyor; geçit 50'ye kırpıyor. 200 yazmak
    'dört bin kod tarıyorum' sanısı üretiyordu."""
    service, api, _ = _service(export_path="/tmp/km-test-raporlar")
    await service.export_csv(kind="coupons", rule_id=7)
    assert api.used("coupons")[0]["per_page"] == 50


async def test_uretim_oncesi_liste_okunamazsa_kupon_URETILMEZ() -> None:
    """Farkı alamayacaksak üretmeyiz: parti dosyası eksik çıkardı ve kullanıcı
    dağıtacağı kodların tamamını hiçbir zaman göremezdi."""
    service, api, _ = _service()
    api.fail.add("coupons")
    result = await service.generate_coupons(7, prefix="BBD", count=5, length=8,
                                            reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert api.used("generate_coupons") == [], "mağazaya istek gitmemeli"


async def test_tarama_eksikse_gormedigimiz_kupon_kaldirilmaz() -> None:
    """"Listede bulunamadı" ile "hiç kullanılmamış" aynı şey değildir."""

    class YarimApi(FakeApi):
        async def coupons(self, rule_id: int, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
            self._record("coupons", rule_id, page=page, per_page=per_page)
            if page > 1:
                raise RuntimeError("ağ koptu")
            return {"items": [{"id": 1, "code": "A1", "timesUsed": 0}],
                    "meta": {"currentPage": 1, "lastPage": 5}}

    service, api, _ = _service(YarimApi())
    result = await service.remove_coupons(7, coupon_ids=[1, 900], reason=GEREKCE,
                                          actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert "doğrulanamadı" in result["error"]
    assert api.used("delete_coupons") == []


async def test_ureteç_siniri_tarayabildigimiz_kadardir() -> None:
    """Ayar 50.000 dese de fark alabildiğimiz kadarına izin verilir."""
    service, _, _ = _service(coupon_max_count=50_000)
    reference = await service.reference()
    assert reference["limits"]["maxCount"] == 3000
    result = await service.generate_coupons(7, prefix="BBD", count=4000, length=8,
                                            reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert "3000" in result["error"]


# ============================== ürün arama süzgeci gerçekten uygulanmalı


async def test_urun_aramasi_query_parametresiyle_gider() -> None:
    """`/api/admin/products` `name` parametresini TANIMIYOR; Laravel onu sessizce
    yok sayıp 1.421 ürünün tamamını döndürüyordu."""
    service, api, _ = _service()
    await service.products("matematik")
    filters = api.sent("product_lookup")[0][0]
    assert filters == {"query": "matematik"}
    assert "name" not in filters


# ================================ ödeme yöntemleri: sessiz boş açılır yok


async def test_ayar_ucu_dizi_dondurse_de_odeme_yontemleri_okunur() -> None:
    api = FakeApi()
    api.config_payload = {  # type: ignore[assignment]
        "slug": "sales.payment_methods", "values": {
            "sales.payment_methods.cashondelivery.active": "1",
            "sales.payment_methods.cashondelivery.title": "Kapıda Ödeme",
        }}
    service, _, _ = _service(api)
    result = await service.reference()
    assert result["paymentMethods"] == [{"value": "cashondelivery", "label": "Kapıda Ödeme"}]


async def test_odeme_yontemi_gelmezse_kosul_gizlenmez_serbest_metin_olur() -> None:
    """Canlıda geçit bu ucu `{}`'ye düşürüyor; ekran sessiz boş açılır göstermez."""
    service, _, _ = _service()
    result = await service.reference()
    assert result["paymentMethods"] == []
    assert any("ödeme yöntemleri" in item for item in result["warnings"])
    payment = next(row for row in result["conditionKinds"] if row["value"] == "payment")
    assert payment["available"] is True, "koşul gizlenmez"
    assert payment["freeText"] is True
    assert payment["note"]
