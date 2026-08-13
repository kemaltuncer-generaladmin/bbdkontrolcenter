"""Setler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_bundles_backend.service import BundlesService
from store_bundles_fakes import FakeApi, FakeLog, FakeStore, light, product

from km_sdk import ExportError

SET = product(100, name="9. Sınıf Tam Set", sku="SET-9", price="500.00", cost=None, stock=0)
KITAP = product(11, name="Matematik", sku="MAT-9", price="200.00", cost="120.00", stock=8)
DEFTER = product(12, name="Defter", sku="DFT-1", price="100.00", cost="60.00", stock=4)

GEREKCE = "Yaz kampanyası için set fiyatı düzeltildi"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             printer: Any = None, **config: Any) -> tuple[BundlesService, FakeApi, FakeStore]:
    api = api or FakeApi({100: dict(SET), 11: dict(KITAP), 12: dict(DEFTER)})
    api.fail.add("bbd_bundles")           # BBD ucu canlıda YAYINDA DEĞİL (404)
    api.lookup_items = [
        light(100, name="9. Sınıf Tam Set", sku="SET-9", price=500.0),
        light(11, name="Matematik", sku="MAT-9", price=200.0),
        light(12, name="Defter", sku="DFT-1", price=100.0),
    ]
    api.category_of = {100: 42, 11: 2, 12: 2}     # yalnız 100 "Setler" kategorisinde
    store = store or FakeStore()
    service = BundlesService(
        api=api, store=store, log=FakeLog(), printer=printer,
        config={"channel": "default", "locale": "tr", "set_category_id": 42, "tax_rate": 20,
                "cache_seconds": 0, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


def _plan(store: FakeStore, **over: Any) -> None:
    """Yerel künye — bileşen adedi/indirimi/zorunluluğu yalnız burada durur."""
    row = {
        "product_id": 100, "name": "9. Sınıf Tam Set", "sku": "SET-9",
        "pricing_mode": "fixed", "discount_percent": 0, "set_price": 30_000,
        "tax_rate": None, "valid_from": "", "valid_to": "", "note": "",
        "components": '[{"productId": 11, "qty": 1, "discount": 0, "required": true},'
                      ' {"productId": 12, "qty": 2, "discount": 0, "required": true}]',
        "actor": "Ali", "updated_at": "2026-08-13T09:00:00+00:00",
    }
    row.update(over)
    store.plans[int(row["product_id"])] = row


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, store = _service()
    _plan(store)
    api.fail.add("product_lookup")
    api.fail.add("product")
    result = await service.bundles()
    assert result["ok"] is True                 # uç patlamaz
    assert result["connected"] is False
    assert len(result["items"]) == 1            # yerel künye yine listelenir
    assert result["items"][0]["readable"] is False
    assert result["items"][0]["calc"]["state"] == "unknown"


async def test_bbd_ucu_yoksa_kategori_yoluna_dusulur_ve_soylenir() -> None:
    service, api, _ = _service()
    result = await service.bundles()
    assert result["source"] == "category"
    assert "42" in result["notice"]
    assert api.used("product_lookup")[0]["per_page"] <= 50
    suzgec = next(args[0] for name, args, _ in api.calls if name == "product_lookup")
    assert suzgec["category_id"] == 42
    assert suzgec["channel"] == "default"        # kanal + dil her istekte gider
    assert suzgec["locale"] == "tr"


async def test_set_listesi_katalogun_tamami_degil_yalniz_o_kategoridir() -> None:
    """`/catalog/products` `category_id`'yi SESSİZCE YOK SAYIYOR (canlıda
    doğrulandı: süzgeçli de süzgeçsiz de `total: 1421`). O uçtan okunsaydı
    kataloğun ilk sayfası "set listesi" diye ekrana gelirdi."""
    service, api, _ = _service()
    result = await service.bundles()
    assert api.count("products") == 0                    # süzmeyen uca hiç gidilmez
    assert [row["id"] for row in result["items"]] == [100]


async def test_kategoride_50den_cok_set_varsa_eksiklik_soylenir() -> None:
    # Seçici ucu sayfalamıyor; sessizce kırpılmış liste "hepsi bu" gibi görünür.
    service, api, _ = _service()
    api.lookup_items = [light(500 + index, name=f"Set {index}", sku=f"S{index}")
                        for index in range(60)]
    api.category_of = {500 + index: 42 for index in range(60)}
    result = await service.bundles()
    assert result["missed"] == 10
    assert "EKSİK" in result["notice"]


async def test_bbd_ucu_yayina_girince_birincil_kaynak_olur() -> None:
    service, api, _ = _service()
    api.fail.discard("bbd_bundles")
    api.bbd_bundle_items = [{
        "id": 100, "name": "Set", "sku": "SET-9", "pricingMode": "fixed", "setPrice": "300.00",
        "components": [{"product_id": 11, "qty": 1}],
    }]
    result = await service.bundles()
    assert result["source"] == "bbd"
    assert api.count("product_lookup") == 0     # kategori taraması yapılmaz


# ================================================== hesabın kaynağı sunucudur

async def test_canli_hesap_fiyati_istemciden_almaz() -> None:
    # İstemcinin gönderdiği tutarla hesaplamak, ekranda kârlı görünen bir setin
    # gerçekte zarar etmesine kapı açardı (K9 ile aynı mantık).
    service, _, _ = _service()
    result = await service.calc(
        components=[{"productId": 11, "qty": 1, "unitPrice": 1, "cost": 1}],
        pricing_mode="fixed", set_price=30_000)
    assert result["components"][0]["unitPrice"] == 20_000     # mağazadaki 200,00
    assert result["components"][0]["cost"] == 12_000
    assert result["calc"]["profit"] == 25_000 - 12_000


async def test_liste_kar_zarar_hesabini_tasir() -> None:
    service, _, store = _service()
    _plan(store)                                 # set 300,00 · bileşen 200 + 2×100
    result = await service.bundles()
    row = result["items"][0]
    assert row["calc"]["componentsTotal"] == 40_000
    assert row["calc"]["setPrice"] == 30_000
    assert row["calc"]["setDiscount"] == 10_000
    assert row["calc"]["costTotal"] == 12_000 + 2 * 6_000
    assert row["calc"]["state"] == "profit"


async def test_kunyede_fiyat_yoksa_magazadaki_urun_fiyati_gecerlidir() -> None:
    # Müşterinin ödediği tutar ürünün kendi fiyatıdır; uydurma bir fiyatla kâr
    # hesaplamak ekranı yalancı yapar.
    service, _, store = _service()
    _plan(store, set_price=None)
    row = (await service.bundles())["items"][0]
    assert row["calc"]["setPrice"] == 50_000     # SET ürününün fiyatı 500,00


async def test_bileseni_tukenmis_set_isaretlenir() -> None:
    service, api, store = _service()
    _plan(store)
    api.products_by_id[12] = product(12, name="Defter", sku="DFT-1", price="100.00",
                                     cost="60.00", stock=1)   # 2 adet isteniyor
    row = (await service.bundles())["items"][0]
    assert row["flags"]["outOfStock"] is True
    assert "bileşeni tükenmiş" in row["warning"]


async def test_bilesen_okuma_tavani_asilirsa_sessiz_kalinmaz() -> None:
    service, _, store = _service(component_fetch_cap=10)
    parts = ", ".join(f'{{"productId": {index}, "qty": 1}}' for index in range(200, 240))
    _plan(store, components=f"[{parts}]")
    result = await service.bundles()
    assert result["skipped"] > 0
    assert "tavan" in result["notice"]


async def test_bilesen_arama_gercekten_suzer() -> None:
    """Seçici ucu `name` süzgecini TANIMIYOR, sessizce yok sayıyor (canlıda
    `name=Geometri` → 1.421 kayıt). `name` gönderen sürümde kullanıcı ne
    yazarsa yazsın kataloğun ilk 40 ürünü geliyordu."""
    service, api, _ = _service()
    result = await service.lookup(q="Defter")
    assert [row["sku"] for row in result["items"]] == ["DFT-1"]
    assert api.used("product_lookup")[0]["per_page"] <= 50
    suzgec = next(args[0] for name, args, _ in api.calls if name == "product_lookup")
    assert suzgec["query"] == "Defter"
    assert "name" not in suzgec           # yok sayılan parametreye güvenilmez


async def test_maliyet_canli_bicimden_okunur_ve_kar_hesaplanir() -> None:
    # Canlı detay yanıtında `cost` gövdede değil `attributes` dizisindedir.
    # Gövdeden okuyan sürümde kâr sütunu baştan sona “—” çıkıyordu.
    service, _, store = _service()
    _plan(store)
    row = (await service.bundles())["items"][0]
    assert row["calc"]["costTotal"] == 24_000
    assert row["calc"]["state"] != "unknown"
    assert row["components"][0]["cost"] == 12_000


async def test_bilesen_stogu_envanter_kaynagindan_gelir() -> None:
    service, _, store = _service()
    _plan(store)
    row = (await service.bundles())["items"][0]
    assert row["components"][0]["stock"] == 8      # inventories → kesin sayı


async def test_kunyede_fiyat_yokken_magaza_fiyati_formda_donmaz() -> None:
    """`planPrice` künyedeki fiyattır, hesabınki değil. Düzenleyici hesabın
    sonucunu forma yazsaydı ilk kaydetmede o günkü mağaza fiyatı yerele donar,
    sonraki zam ekrana bir daha yansımazdı."""
    service, _, store = _service()
    _plan(store, set_price=None)
    row = (await service.bundles())["items"][0]
    assert row["planPrice"] is None
    assert row["calc"]["setPrice"] == 50_000      # mağazadaki ürün fiyatı


# ============================================================ gerekçe kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, store = _service()
    result = await service.save(100, payload={"components": [{"productId": 11}]},
                                reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert store.plans == {}
    assert api.used("bbd_save_bundle") == []


async def test_pasiflestirme_gerekcesiz_yapilmaz() -> None:
    service, api, _ = _service()
    result = await service.set_status(100, active=False, reason="dur", actor="Ali")
    assert result["ok"] is False
    assert api.used("update_product_status") == []


# ================================================================== yazma

async def test_kunye_yerel_yazilir_ve_magazaya_yazilmadigi_soylenir() -> None:
    service, api, store = _service()
    api.fail.add("bbd_save_bundle")              # uç henüz yayında değil
    result = await service.save(100, payload={
        "name": "9. Sınıf Tam Set", "components": [{"productId": 11, "qty": 1}],
        "setPrice": "300.00"}, reason=GEREKCE, actor="Ali", dry_run=False)

    assert result["ok"] is True
    assert result["stored"] is False
    assert "YAZILMADI" in result["notice"]
    assert store.plans[100]["set_price"] == 30_000
    assert store.audit[-1]["result"] == "yerel"


async def test_uc_yayindayken_kunye_magazaya_da_gider() -> None:
    service, api, store = _service()
    result = await service.save(100, payload={"components": [{"productId": 11, "qty": 2,
                                                              "discount": 10}]},
                                reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["stored"] is True
    gonderilen = api.used("bbd_save_bundle")[0]["payload"]
    assert gonderilen["components"] == [{"product_id": 11, "qty": 2, "discount": 10,
                                         "required": True}]
    assert store.plans[100]["components"]


async def test_kuru_prova_magazaya_yazmadigini_soyler() -> None:
    service, _, store = _service()
    result = await service.save(100, payload={"components": [{"productId": 11}]},
                                reason=GEREKCE, actor="Ali", dry_run=True)
    assert result["stored"] is False
    assert "Kuru prova" in result["notice"]
    assert store.plans[100]["components"]        # künye yine de saklanır


async def test_bilesensiz_set_kaydedilmez() -> None:
    service, _, store = _service()
    result = await service.save(100, payload={"components": []}, reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert "bileşen" in result["error"]
    assert store.plans == {}


async def test_set_kendi_bileseni_olamaz() -> None:
    service, _, _ = _service()
    result = await service.save(100, payload={"components": [{"productId": 100}]},
                                reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert "kendi bileşeni" in result["error"]


async def test_kunye_yazilamazsa_basarili_gorunmez() -> None:
    service, _, store = _service()
    store.broken = True
    result = await service.save(100, payload={"components": [{"productId": 11}]},
                                reason=GEREKCE, actor="Ali")
    assert result["ok"] is False


async def test_silme_yok_pasiflestirme_var() -> None:
    service, api, store = _service()
    result = await service.set_status(100, active=False, reason=GEREKCE, actor="Ali",
                                      dry_run=False)
    assert result["ok"] is True
    cagri = api.calls[-1]
    assert cagri[0] == "update_product_status"
    assert cagri[2]["active"] is False
    assert store.audit[-1]["action"] == "deactivate"
    # Künye SİLİNMEZ: satılmış setlerin geçmişini okumak için gerekir.
    assert not any(row["action"].startswith("delete") for row in store.audit)


async def test_gerekce_denetim_izine_yazilir() -> None:
    service, _, _ = _service()
    await service.save(100, payload={"components": [{"productId": 11}]},
                       reason=GEREKCE, actor="Ayşe", dry_run=False)
    kayit = await service.audit(bundle_id=100)
    assert kayit["items"][0]["reason"] == GEREKCE
    assert kayit["items"][0]["actor"] == "Ayşe"


# ================================================================== rapor

async def test_rapor_klasoru_disindaki_dosya_basilmaz(tmp_path: Path) -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.printed: list[Path] = []

        async def print_file(self, path: Path, *, title: str = "",
                             copies: int = 1) -> dict[str, Any]:
            self.printed.append(path)
            return {"printer": "HP"}

    printer = FakePrinter()
    rapor_dizini = tmp_path / "raporlar"
    rapor_dizini.mkdir()
    disarida = tmp_path / "gizli.pdf"
    disarida.write_bytes(b"%PDF-1.4")

    service, _, _ = _service(printer=printer, export_path=str(rapor_dizini))

    result = await service.print_report(str(disarida))
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]
    assert printer.printed == []

    icerde = rapor_dizini / "set-listesi.pdf"
    icerde.write_bytes(b"%PDF-1.4")
    assert (await service.print_report(str(icerde)))["ok"] is True


async def test_yazici_yoksa_ekran_calismaya_devam_eder() -> None:
    service, _, _ = _service()
    assert (await service.printer_status())["ready"] is False
    assert (await service.print_report("/tmp/x.pdf"))["ok"] is False


async def test_csv_bos_listede_dosya_uretmez() -> None:
    service, api, _ = _service()
    api.lookup_items = []
    result = await service.export_csv()
    assert result["ok"] is False


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    assert (await service.build_report("bilinmeyen", {}))["ok"] is False


# ====================================================== K7 — çıktı üretimi

async def test_pdf_uretilemezse_uc_patlamaz_sebebini_soyler(monkeypatch: Any) -> None:
    """`build_pdf` `reportlab` yoksa `ExportError` atıyor ve çağrısı `try`'ın
    DIŞINDAYDI: istisna route'a kadar çıkıp 500 üretiyordu. Ekran o durumda
    bomboş kalır ve sebebini söyleyemezdi (K7)."""
    from store_bundles_backend import service as service_module

    service, _, store = _service()
    _plan(store)

    def patlat(**_: Any) -> bytes:
        raise ExportError("PDF için `reportlab` gerekli.")

    monkeypatch.setattr(service_module, "build_pdf", patlat)
    result = await service.build_report("sets", {})
    assert result["ok"] is False
    assert "reportlab" in result["error"]

    # Önizleme de aynı yoldan geçer; kit `reportChain` yalnız `{ok, error}` okur.
    onizleme = await service.preview("sets")
    assert onizleme["ok"] is False


async def test_csv_uretilemezse_uc_patlamaz(monkeypatch: Any) -> None:
    """`csv_bytes` çağrısı `write_private`'ın ARGÜMANIYDI ve `try` yalnız
    `OSError` yakalıyordu; `ExportError` sızıyordu."""
    from store_bundles_backend import service as service_module

    service, _, store = _service()
    _plan(store)

    def patlat(*_: Any, **__: Any) -> bytes:
        raise ExportError("CSV yazılamadı.")

    monkeypatch.setattr(service_module, "csv_bytes", patlat)
    result = await service.export_csv()
    assert result["ok"] is False
    assert "CSV" in result["error"]


# ============================================================ carousel rozeti

async def test_carousel_slotunun_hedef_kimligi_urun_sanilmaz() -> None:
    """Slot hem `productId` hem `target_id` taşıyabiliyor ve `target_id` ürün
    olmak zorunda değil (kategori/CMS hedefi de olabiliyor). Her ikisini birden
    toplayan sürüm, o kimlikle çakışan BAŞKA bir sete “ana sayfa” rozeti
    takıyordu — ekranda düzeltilemeyen sessiz bir yalan."""
    service, api, store = _service(carousel_id=12)
    api.fail.discard("bbd_carousel")
    api.carousel_items = [{"productId": 999, "target_id": 100}]
    _plan(store)
    row = (await service.bundles())["items"][0]
    assert row["id"] == 100
    assert row["onCarousel"] is False


async def test_carousel_urunu_rozet_alir() -> None:
    service, api, store = _service(carousel_id=12)
    api.fail.discard("bbd_carousel")
    api.carousel_items = [{"productId": 100}]
    _plan(store)
    assert (await service.bundles())["items"][0]["onCarousel"] is True


async def test_carousel_kapaliysa_uc_hic_cagrilmaz() -> None:
    # `carousel_id: 0` = rozet çizilmez (config/schema.json). Yine de istek
    # atmak, dakikada 55 istekli geçitten boşuna pay yerdi.
    service, api, store = _service(carousel_id=0)
    api.fail.discard("bbd_carousel")
    _plan(store)
    await service.bundles()
    assert api.count("bbd_carousel") == 0
