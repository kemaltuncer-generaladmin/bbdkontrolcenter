"""Promosyon servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_promotions_backend.service import PromotionsService
from store_promotions_fakes import KURAL, FakeApi, FakeLog, FakeStore

GEREKCE = "Eylül kampanyası müdür onayıyla güncellendi"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             printer: Any = None,
             **config: Any) -> tuple[PromotionsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = PromotionsService(
        api=api, store=store, log=FakeLog(), printer=printer,
        config={"channel": "default", "locale": "tr", "page_size": 50, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ==================================================== K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("cart_rules")
    result = await service.rules()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_kuponlar_okunamazsa_kural_kunyesi_yine_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("coupons")
    result = await service.rule(7)
    assert result["ok"] is True
    assert result["rule"]["name"] == "Eylül kampanyası"
    assert any("kuponlar" in item for item in result["warnings"])


async def test_referansin_bir_parcasi_patlarsa_gerisi_gelir() -> None:
    service, api, _ = _service()
    api.fail.add("configuration")
    result = await service.reference()
    assert result["ok"] is True
    assert [item["name"] for item in result["customerGroups"]] == ["Genel", "Bayi"]
    assert result["paymentMethods"] == []
    assert any("ödeme yöntemleri" in item for item in result["warnings"])


# ============================================ desteklenmeyen özellik gizlenmez

async def test_ilk_siparis_kosulu_gizlenmez_kapali_ve_gerekceli_gelir() -> None:
    service, _, _ = _service()
    result = await service.reference()
    kinds = {row["value"]: row for row in result["conditionKinds"]}
    assert kinds["firstOrder"]["available"] is False
    assert "yayınlanınca" in kinds["firstOrder"]["note"]
    assert kinds["subtotal"]["available"] is True


# ================================================== gerekçe ve izin ayrımı

async def test_gerekce_kisa_ise_magazaya_hic_gidilmez() -> None:
    service, api, _ = _service()
    result = await service.save_rule(7, patch={"name": "Yeni"}, reason="kısa", actor="Ayşe")
    assert result["ok"] is False
    assert "en az 10" in result["error"]
    assert api.calls == [], "gerekçe geçersizken uzak sisteme istek çıkmaz"


async def test_icerik_ucundan_durum_degistirilemez() -> None:
    service, api, _ = _service()
    await service.save_rule(7, patch={"name": "Ekim", "status": True}, reason=GEREKCE,
                            actor="Ayşe", dry_run=False)
    payload = api.used("save_cart_rule")[0]["payload"]
    assert payload["status"] == 1, "mağazadaki mevcut durum korunur"
    assert payload["name"] == "Ekim"


async def test_durum_ucu_kurali_yayina_alir_ve_takvimi_uyarir() -> None:
    api = FakeApi({7: {**KURAL, "status": 0, "ends_till": "2020-01-01"}})
    service, api, _ = _service(api)
    result = await service.set_rule_status(7, active=True, reason=GEREKCE, actor="Ayşe",
                                           dry_run=False)
    assert result["ok"] is True
    assert api.used("save_cart_rule")[0]["payload"]["status"] == 1
    assert "bitiş tarihi" in result["notice"]


async def test_yeni_kural_taslak_acilir() -> None:
    service, api, _ = _service()
    result = await service.create_rule(
        patch={"name": "Kasım", "action": {"kind": "by_percent", "value": 15}},
        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    payload = api.used("save_cart_rule")[0]["payload"]
    assert payload["status"] == 0
    assert payload["channels"] == [1], "kanal boş bırakılırsa kural hiçbir yerde çalışmaz"
    assert "TASLAK" in result["notice"]


# =============================================== oku-değiştir-yaz ve doğrulama

async def test_kaydetmeden_once_kural_taze_okunur() -> None:
    service, api, _ = _service()
    await service.save_rule(7, patch={"name": "Ekim"}, reason=GEREKCE, actor="Ayşe")
    assert api.calls[0][0] == "cart_rule"


async def test_gecersiz_yuzde_magazaya_gonderilmez() -> None:
    service, api, _ = _service()
    result = await service.save_rule(7, patch={"action": {"kind": "by_percent", "value": 150}},
                                     reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert api.used("save_cart_rule") == []


async def test_ust_sinir_desteklenmiyorsa_ekrana_soylenir() -> None:
    service, _, _ = _service()
    result = await service.save_rule(
        7, patch={"action": {"kind": "by_percent", "value": 10, "maxDiscount": 5000}},
        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert result["maxDiscountSkipped"] is True


async def test_bos_kosul_satiri_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.save_rule(
        7, patch={"conditions": [{"kind": "subtotal", "operator": ">=", "value": 0}]},
        reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert "sıfırdan büyük" in result["error"]
    assert api.used("save_cart_rule") == []


# ============================================================ denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.save_rule(7, patch={"name": "Ekim"}, reason=GEREKCE, actor="Ayşe",
                            dry_run=False)
    actions = [row["action"] for row in store.audit]
    assert actions == ["update_cart_rule", "update_cart_rule"]   # denendi + ok
    assert store.audit[0]["reason"] == GEREKCE
    assert store.audit[-1]["result"] == "ok"


async def test_uzak_hata_da_ize_yazilir() -> None:
    service, api, store = _service()
    api.fail.add("save_cart_rule")
    result = await service.save_rule(7, patch={"name": "Ekim"}, reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert store.audit[-1]["result"] == "hata"


# =============================================================== kuponlar

async def test_kuru_provada_kupon_uretilmez_ve_dosya_yazilmaz() -> None:
    service, _api, store = _service()
    result = await service.generate_coupons(7, prefix="BBD", count=5, length=8, reason=GEREKCE,
                                            actor="Ayşe", dry_run=True)
    assert result["ok"] is True
    assert result["codes"] == []
    assert result["path"] == ""
    assert store.batches == {}
    assert "Kuru prova" in result["notice"]


async def test_uretilen_kodlar_once_sonra_farkindan_bulunur(tmp_path: Path) -> None:
    api = FakeApi()
    api.coupon_pages = [
        {"items": [{"id": 1, "code": "ESKI-1", "times_used": 0, "usage_limit": 1}], "meta": {}},
        {"items": [{"id": 1, "code": "ESKI-1", "times_used": 0, "usage_limit": 1},
                   {"id": 2, "code": "BBD-A2", "times_used": 0, "usage_limit": 1},
                   {"id": 3, "code": "BBD-B3", "times_used": 0, "usage_limit": 1}], "meta": {}},
    ]
    service, api, store = _service(api, export_path=str(tmp_path))
    result = await service.generate_coupons(7, prefix="BBD", count=2, length=8, reason=GEREKCE,
                                            actor="Ayşe", dry_run=False)
    assert result["codes"] == ["BBD-A2", "BBD-B3"]
    assert result["produced"] == 2
    assert Path(result["path"]).exists()
    assert len(store.batches) == 1
    saved = json.loads(next(iter(store.batches.values()))["codes"])
    assert saved == ["BBD-A2", "BBD-B3"]


async def test_istenenden_az_kod_uretilirse_ekran_soyler(tmp_path: Path) -> None:
    api = FakeApi()
    api.coupon_pages = [
        {"items": [], "meta": {}},
        {"items": [{"id": 2, "code": "BBD-A2", "times_used": 0}], "meta": {}},
    ]
    service, api, _ = _service(api, export_path=str(tmp_path))
    result = await service.generate_coupons(7, prefix="BBD", count=5, length=8, reason=GEREKCE,
                                            actor="Ayşe", dry_run=False)
    assert result["produced"] == 1
    assert "az kod üretildi" in result["notice"]


async def test_uretecin_sinirlari_asilirsa_magazaya_gidilmez() -> None:
    service, api, _ = _service(coupon_max_count=100)
    result = await service.generate_coupons(7, prefix="BBD", count=500, length=8, reason=GEREKCE,
                                            actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert api.used("generate_coupons") == []


async def test_kullanilmis_kupon_kaldirilmaz() -> None:
    api = FakeApi()
    api.coupon_pages = [{"items": [{"id": 1, "code": "EYLUL-A1", "times_used": 3}], "meta": {}}]
    service, api, _ = _service(api)
    result = await service.remove_coupons(7, coupon_ids=[1], reason=GEREKCE, actor="Ayşe",
                                          dry_run=False)
    assert result["ok"] is False
    assert "EYLUL-A1" in result["error"]
    assert api.used("delete_coupons") == []


async def test_ikinci_sayfadaki_kullanilmis_kupon_da_yakalanir() -> None:
    """Tek sayfayla yetinen bir denetim, 200. koddan sonrasını görmezdi."""

    class SayfaliApi(FakeApi):
        async def coupons(self, rule_id: int, *, page: int = 1,
                          per_page: int | None = None) -> dict[str, Any]:
            self._record("coupons", rule_id, page=page, per_page=per_page)
            sayfa = {
                1: [{"id": 1, "code": "A1", "times_used": 0}],
                2: [{"id": 2, "code": "B2", "times_used": 4}],
            }[page]
            return {"items": sayfa, "meta": {"currentPage": page, "lastPage": 2}}

    service, api, _ = _service(SayfaliApi())
    result = await service.remove_coupons(7, coupon_ids=[1, 2], reason=GEREKCE, actor="Ayşe",
                                          dry_run=False)
    assert result["ok"] is False
    assert "B2" in result["error"]
    assert len(api.used("coupons")) == 2, "ikinci sayfa da okunmalı"
    assert api.used("delete_coupons") == []


async def test_kupon_ornegi_magazaya_hic_gitmez() -> None:
    service, api, _ = _service()
    result = service.code_preview(prefix="BBD-", length=8, fmt="alphanumeric")
    assert result["ok"] is True
    assert len(result["samples"]) == 3
    assert api.calls == []
    assert "Gerçek kodları mağaza üretir" in result["notice"]


# ============================================================ simülasyon

async def test_simulasyon_taslak_kurali_kaydetmeden_dener() -> None:
    service, api, _ = _service()
    cart = {"items": [{"name": "Kitap", "price": 20000, "qty": 1, "productId": 5,
                       "categoryIds": [4]}], "shipping": 0}
    result = await service.simulate(cart=cart, draft={
        "id": 0, "name": "Deneme", "conditionType": "all", "conditions": [],
        "action": {"kind": "by_percent", "value": 25},
        "limits": {"priority": 0}, "channels": [], "customerGroups": [],
    })
    assert result["ok"] is True
    names = [row["name"] for row in result["result"]["applied"]]
    assert "Deneme (taslak)" in names
    assert api.used("save_cart_rule") == [], "simülasyon MAĞAZAYA YAZMAZ"


async def test_simulasyon_taslagi_ayni_kimlikli_kaydin_yerine_koyar() -> None:
    service, _, _ = _service()
    cart = {"items": [{"name": "Kitap", "price": 100000, "qty": 1, "productId": 5,
                       "categoryIds": [4]}], "shipping": 0}
    result = await service.simulate(cart=cart, coupon="EYLUL", draft={
        "id": 7, "name": "Eylül kampanyası", "couponType": "specific", "couponCode": "EYLUL",
        "conditions": [], "action": {"kind": "by_percent", "value": 50},
        "limits": {}, "channels": [], "customerGroups": [],
    })
    assert result["ruleCount"] == 1, "kayıtlı kural taslakla değişti, iki kez sayılmadı"
    assert result["result"]["discount"] == 50000


async def test_magaza_dusse_de_simulasyon_calisir() -> None:
    service, api, _ = _service()
    api.fail.add("cart_rules")
    result = await service.simulate(cart={"items": [{"price": 1000, "qty": 1}]}, draft={
        "id": 0, "name": "Yalnız taslak", "conditions": [],
        "action": {"kind": "by_percent", "value": 10}, "limits": {},
    })
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["result"]["discount"] == 100


# ============================================================== performans

async def test_performans_kupon_kirilimini_siparisten_cikarir() -> None:
    api = FakeApi()
    api.orders_payload = {"items": [
        {"id": 1, "coupon_code": "EYLUL", "grand_total": "200.00", "discount_amount": "20.00",
         "status": "completed", "created_at": "2026-08-01"},
        {"id": 2, "coupon_code": "", "grand_total": "100.00", "discount_amount": "0",
         "status": "completed", "created_at": "2026-08-02"},
    ], "meta": {}}
    service, api, _ = _service(api)
    result = await service.performance(start="2026-08-01", end="2026-08-13")
    assert result["ok"] is True
    assert result["rows"][0]["code"] == "EYLUL"
    assert result["totals"]["couponRevenue"] == 20000
    assert api.used("orders")[0]["all_pages"] is True
    assert api.calls[0][1][0] == {"date_from": "2026-08-01", "date_to": "2026-08-13"}


async def test_performans_araligi_verilmezse_yapilandirmadan_gelir() -> None:
    service, api, _ = _service(performance_days=7)
    await service.performance(end="2026-08-13")
    filters = api.calls[0][1][0]
    assert filters == {"date_from": "2026-08-07", "date_to": "2026-08-13"}


async def test_siparis_tavani_asilirsa_eksiklik_bildirilir() -> None:
    api = FakeApi()
    api.orders_payload = {"items": [{"id": index, "coupon_code": "A", "grand_total": "10.00",
                                     "discount_amount": "1.00", "status": "completed",
                                     "created_at": "2026-08-02"} for index in range(150)],
                          "meta": {}}
    service, api, _ = _service(api, performance_order_cap=100)
    result = await service.performance(start="2026-08-01", end="2026-08-13")
    assert result["truncated"] is True
    assert result["orders"] == 100


# ================================================================== çıktı

async def test_rapor_klasoru_disindaki_dosya_basilmaz(tmp_path: Path) -> None:
    class FakePrinter:
        async def print_file(self, path: Path, **_: Any) -> dict[str, Any]:
            raise AssertionError("bu dosya basılmamalıydı")

    service, _, _ = _service(printer=FakePrinter(), export_path=str(tmp_path))
    kaçak = tmp_path.parent / "kacak.pdf"
    kaçak.write_bytes(b"%PDF-1.4")
    result = await service.print_report(str(kaçak))
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]


async def test_kural_listesi_csv_rapor_klasorune_yazilir(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.export_csv(kind="rules")
    assert result["ok"] is True
    assert result["rows"] == 1
    written = Path(result["path"])
    assert written.parent == tmp_path
    assert "Eylül kampanyası" in written.read_text(encoding="utf-8")
