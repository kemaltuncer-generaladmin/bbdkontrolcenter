"""Ürünler servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_products_backend.service import ProductsService
from store_products_fakes import FakeApi, FakeLog, FakeStore

URUN = {
    "id": 5, "sku": "KLM-1", "type": "simple", "attribute_family_id": 3, "status": 1,
    "name": "Kalem", "url_key": "kalem", "price": "10.00", "categories": [{"id": 4}],
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[ProductsService, FakeApi, FakeStore]:
    api = api or FakeApi({5: dict(URUN)})
    store = store or FakeStore()
    service = ProductsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "low_stock_threshold": 5, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("products")
    result = await service.products()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_kunye_parcasi_patlarsa_gerisi_yine_dolar() -> None:
    service, api, _ = _service()
    api.fail.add("customer_group_prices")
    result = await service.card(5)
    assert result["ok"] is True
    assert result["product"]["sku"] == "KLM-1"
    assert result["price"]["groupPrices"] == []
    assert any("grup fiyatları" in item for item in result["warnings"])


# ================================================= sunucu tarafı sayfalama

async def test_liste_sunucudan_sayfali_gelir_tam_katalog_cekilmez() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [dict(URUN)],
                        "meta": {"total": 1419, "currentPage": 3, "perPage": 50, "lastPage": 29}}
    result = await service.products(page=3)
    assert result["total"] == 1419
    assert result["pages"] == 29
    assert api.used("products")[0]["all_pages"] is False
    assert api.used("products")[0]["page"] == 3


async def test_kanal_ve_dil_her_liste_isteginde_gider() -> None:
    service, api, _ = _service()
    await service.products()
    filters = api.calls[0][1][0]
    assert filters["channel"] == "default"
    assert filters["locale"] == "tr"


async def test_sku_benzeri_arama_sku_alanina_ad_araması_name_alanina_gider() -> None:
    service, api, _ = _service()
    await service.products(q="KLM-1")
    assert api.calls[0][1][0]["sku"] == "KLM-1"
    api.calls.clear()
    await service.products(q="kırmızı kalem")
    assert api.calls[0][1][0]["name"] == "kırmızı kalem"


async def test_stok_bulgulari_ayri_sayfali_uctan_gelir() -> None:
    # Tükenen/kritik/görselsiz ürün ÜRÜN LİSTESİ ucuyla süzülemez: stok
    # envanterde, görsel ilişkidedir. Tam listeyi çekip istemcide süzmek de
    # yasak; sağlık bulgusu ucu tam bunun içindir.
    service, api, _ = _service()
    api.issues_payload = {"items": [{"product_id": 9, "kind": "out_of_stock"}],
                          "meta": {"total": 12, "currentPage": 1, "lastPage": 1}}
    result = await service.products(chip="out_of_stock")
    assert result["source"] == "out_of_stock"
    assert result["items"][0]["id"] == 9
    assert api.used("bbd_catalog_issues")[0]["page"] == 1


async def test_bulgu_ucu_yoksa_ekran_durumu_anlatir() -> None:
    service, api, _ = _service()
    api.fail.add("bbd_catalog_issues")
    result = await service.products(chip="no_image")
    assert result["connected"] is False
    assert result["items"] == []


async def test_kategori_suzgeci_uygulanmadiysa_yalan_soylenmez() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 1, "categories": [{"id": 99}]}], "meta": {}}
    result = await service.products(category_id=4)
    assert result["categoryFilter"] is False


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.save(5, patch={"name": "X"}, reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("update_product") == []


async def test_kaydetme_oku_degistir_yaz_yapar() -> None:
    service, api, _ = _service()
    result = await service.save(5, patch={"name": "Kurşun kalem"},
                                reason="Ad yazım hatası düzeltildi", actor="Ali",
                                dry_run=False)
    assert result["ok"] is True
    body = api.used("update_product")[0]["payload"]
    assert body["name"] == "Kurşun kalem"
    assert body["url_key"] == "kalem"        # dokunulmayan alan korunur
    assert body["channel"] == "default"
    assert "attribute_family_id" not in body


async def test_sku_kaydet_ucundan_degistirilemez() -> None:
    service, api, _ = _service()
    result = await service.save(5, patch={"sku": "YENI"},
                                reason="SKU düzeltmesi yapılacak", actor="Ali")
    assert result["ok"] is False
    assert api.used("update_product") == []


async def test_sku_degisikligi_uyarisiyla_birlikte_doner() -> None:
    service, api, _ = _service()
    result = await service.change_sku(5, sku="KLM-2", reason="Tedarikçi kodu değişti",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["from"] == "KLM-1"
    assert "kırılır" in result["notice"]
    assert api.used("update_product")[0]["payload"]["sku"] == "KLM-2"


async def test_bozuk_sku_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.change_sku(5, sku="a b/c ç", reason="Kod düzeltmesi yapıldı",
                                      actor="Ali")
    assert result["ok"] is False


async def test_durum_degisikligi_indeksleme_uyarisi_dondurur() -> None:
    # TUZAK 8: indeksleme gecikmesi sessiz kalırsa "neden vitrinde değişmedi"
    # sorusu doğar.
    service, _, _ = _service()
    result = await service.set_status([5], active=False, reason="Stok kalmadı, kapatıldı",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert "birkaç dakika" in result["notice"]


async def test_pasiflestirme_toplu_ucu_kullanir_tek_tek_put_atmaz() -> None:
    service, api, _ = _service()
    await service.set_status([1, 2, 3], active=False, reason="Sezon dışı ürünler kapatıldı",
                             actor="Ali", dry_run=False)
    assert api.used("update_product_status")[0]["active"] is False
    assert api.used("update_product") == []


async def test_stok_mutlak_deger_olarak_yazilir() -> None:
    service, api, _ = _service()
    result = await service.set_stock(5, quantities={"1": 40},
                                     reason="Sayım sonrası düzeltme yapıldı", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is True
    assert api.used("update_inventory")[0]["quantities"] == {"1": 40}


# ======================================================== url_key yoklaması

async def test_url_anahtari_yazmadan_once_yoklanir() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 9, "sku": "X", "url_key": "kalem"}], "meta": {}}
    result = await service.check_url_key(url_key="Kalem", product_id=5)
    assert result["state"] == "taken"
    assert result["suggestion"] == "kalem"


async def test_url_yoklamasi_magaza_dusunce_bilinmiyor_der() -> None:
    service, api, _ = _service()
    api.fail.add("products")
    result = await service.check_url_key(url_key="kalem")
    assert result["state"] == "unknown"


# ============================================================ toplu işlem

async def test_toplu_islem_onizlemesiz_uygulanmaz() -> None:
    service, _, _ = _service()
    result = await service.bulk_apply(token="olmayan-jeton", reason="Toplu zam uygulanıyor",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "Fark tablosu görülmeden" in result["error"]


async def test_onizleme_fark_tablosu_ve_jeton_uretir() -> None:
    api = FakeApi({1: {"id": 1, "sku": "A", "name": "A", "type": "simple", "price": "100.00"},
                   2: {"id": 2, "sku": "B", "name": "B", "type": "simple", "price": "200.00"}})
    service, _, store = _service(api)
    preview = await service.bulk_preview(kind="price", product_ids=[1, 2], mode="percent",
                                         amount=10)
    assert preview["ok"] is True
    assert preview["summary"] == {"total": 2, "changed": 2, "skipped": 0, "up": 2, "down": 0,
                                  "netDelta": 3000}
    assert store.bulk[preview["token"]]["status"] == "preview"


async def test_onizleme_okunamayan_urunu_gizlemez() -> None:
    api = FakeApi({1: {"id": 1, "sku": "A", "name": "A", "type": "simple", "price": "100.00"}})
    service, _, _ = _service(api)
    preview = await service.bulk_preview(kind="price", product_ids=[1, 77], mode="percent",
                                         amount=5)
    assert preview["missing"] == [77]
    assert preview["summary"]["total"] == 1


async def test_toplu_pasiflestirme_ayri_izni_arka_kapidan_atlatamaz() -> None:
    # `store_products.bulk` iznine sahip ama `store_products.deactivate` izni
    # olmayan kullanıcı, toplu yolu kullanarak ürün pasifleştiremez (K9).
    service, api, _ = _service()
    preview = await service.bulk_preview(kind="status", product_ids=[5], active=False)
    result = await service.bulk_apply(token=preview["token"], reason="Sezon dışı kapatıldı",
                                      actor="Ali", dry_run=False, may_deactivate=False)
    assert result["ok"] is False
    assert "store_products.deactivate" in result["error"]
    assert api.used("update_product_status") == []


async def test_ayni_onizleme_iki_kez_uygulanmaz() -> None:
    service, _, _ = _service()
    preview = await service.bulk_preview(kind="status", product_ids=[5], active=False)
    first = await service.bulk_apply(token=preview["token"], reason="Sezon dışı kapatıldı",
                                     actor="Ali", dry_run=False, may_deactivate=True)
    assert first["ok"] is True
    second = await service.bulk_apply(token=preview["token"], reason="Sezon dışı kapatıldı",
                                      actor="Ali", dry_run=False, may_deactivate=True)
    assert second["ok"] is False
    assert "zaten uygulandı" in second["error"]


async def test_uygulama_onizlenen_tabloyu_kullanir_yeniden_hesaplamaz() -> None:
    service, _, store = _service()
    preview = await service.bulk_preview(kind="status", product_ids=[5], active=False)
    rows = json.loads(store.bulk[preview["token"]]["rows"])
    assert rows[0]["after"] == 0
    await service.bulk_apply(token=preview["token"], reason="Sezon dışı kapatıldı",
                             actor="Ali", dry_run=False, may_deactivate=True)
    assert store.bulk[preview["token"]]["status"] == "applied"


async def test_toplu_uc_yokken_fiyat_uygulamasi_reddedilir() -> None:
    # TUZAK 11: 1.419 üründe tek tek PUT dakikada 60 istek sınırını aşar ve
    # yarıda kalırsa kataloğun yarısı yeni yarısı eski fiyatta kalır.
    api = FakeApi({1: {"id": 1, "sku": "A", "name": "A", "type": "simple", "price": "100.00"}})
    service, api, _ = _service(api)
    preview = await service.bulk_preview(kind="price", product_ids=[1], mode="percent",
                                         amount=10)
    assert preview["applicable"] is False
    result = await service.bulk_apply(token=preview["token"], reason="Toplu zam uygulanıyor",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("update_product") == []


async def test_yonetici_acikca_izin_verdiyse_kucuk_secim_sirayla_yazilir() -> None:
    api = FakeApi({1: {"id": 1, "sku": "A", "name": "A", "type": "simple", "price": "100.00"}})
    service, api, _ = _service(api, bulk_direct_limit=10)
    preview = await service.bulk_preview(kind="price", product_ids=[1], mode="percent",
                                         amount=10)
    assert preview["applicable"] is True
    result = await service.bulk_apply(token=preview["token"], reason="Kağıt zammı yansıtıldı",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("update_product")[0]["payload"]["price"] == "110.00"


async def test_sirayla_yazma_siniri_asilirsa_hic_yazilmaz() -> None:
    urunler = {index: {"id": index, "sku": f"S{index}", "name": "X", "type": "simple",
                       "price": "100.00"} for index in range(1, 6)}
    service, api, _ = _service(FakeApi(urunler), bulk_direct_limit=2)
    preview = await service.bulk_preview(kind="price", product_ids=list(urunler),
                                         mode="percent", amount=10)
    api.calls.clear()
    result = await service.bulk_apply(token=preview["token"], reason="Toplu zam uygulanıyor",
                                      actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert api.used("update_product") == []


async def test_cok_buyuk_secim_onizlemeye_bile_girmez() -> None:
    service, _, _ = _service()
    result = await service.bulk_preview(kind="price", product_ids=list(range(1, 600)),
                                        mode="percent", amount=5)
    assert result["ok"] is False
    assert "500" in result["error"]


# ============================================================== denetim izi

async def test_her_yazma_gerekcesiyle_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.save(5, patch={"name": "Yeni ad"}, reason="Ad yazım hatası düzeltildi",
                       actor="Ayşe Yılmaz", dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "update_product"]
    assert kayitlar[-1]["reason"] == "Ad yazım hatası düzeltildi"
    assert kayitlar[-1]["actor"] == "Ayşe Yılmaz"
    assert kayitlar[-1]["result"] == "ok"


async def test_yazma_patlasa_bile_ne_yapmaya_calistigimiz_kaydedilir() -> None:
    service, api, store = _service()
    api.fail.add("update_product")
    await service.save(5, patch={"name": "Yeni ad"}, reason="Ad yazım hatası düzeltildi",
                       actor="Ali", dry_run=False)
    assert any(row["result"] == "hata" for row in store.audit)


async def test_denetim_izi_urune_gore_suzulur() -> None:
    service, _, _ = _service()
    await service.save(5, patch={"name": "A"}, reason="Ad yazım hatası düzeltildi",
                       actor="Ali", dry_run=False)
    result = await service.audit(product_id=5)
    assert result["items"]
    assert all(row["productId"] == 5 for row in result["items"])


# ================================================================= ayarlar

async def test_bulunmayan_ayar_anahtarina_yazilmaz() -> None:
    # Bulunmayan anahtara yazmak `core_config` içinde etkisiz bir satır açar ve
    # kullanıcı ayarı değiştirdiğini sanır — sessiz başarısızlık.
    service, api, _ = _service()
    api.config_payload = {"values": {}}
    result = await service.save_settings(back_order=True, reason="Arka sipariş açılıyor",
                                         actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert "arka sipariş" in result["skipped"]
    assert api.used("update_configuration") == []


async def test_bulunan_ayar_anahtari_yazilir() -> None:
    service, api, _ = _service()
    api.config_payload = {"values": {"catalog.inventory.stock_options.back_orders": 0}}
    result = await service.save_settings(back_order=True, reason="Arka sipariş açılıyor",
                                         actor="Ali", dry_run=False)
    assert result["skipped"] == []
    assert api.used("update_configuration")[0]["values"] == {
        "catalog.inventory.stock_options.back_orders": 1}


async def test_kritik_esik_yerel_tercihtir_magazaya_gitmez() -> None:
    service, api, store = _service()
    api.config_payload = {"values": {}}
    await service.save_settings(low_stock_threshold=12, reason="Eşik yükseltildi çünkü",
                                actor="Ali", dry_run=False)
    assert store.prefs["low_stock_threshold"] == "12"
    assert api.used("update_configuration") == []
    liste = await service.products()
    assert liste["threshold"] == 12


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


# ------------------------------------------------- öznitelik ailesi (varsayılan)

async def test_urun_acarken_aile_sorulmaz_tek_aile_kendiliginden_secilir() -> None:
    # Aile, çok satıcılı/çok ürün tipli mağazalar için var. Burada tek satıcı ve
    # tek ürün tipi olduğu için ürün açılışında sorulması anlamsız bir seçim
    # üretirdi. Panelde bu alanı soran hiçbir girdi YOK; zorunlu kaldığı sürece
    # panelden ürün açılamıyordu.
    service, api, _ = _service()
    api.families_payload = {"items": [{"id": 7, "code": "kitap", "name": "Kitap"}], "meta": {}}

    result = await service.create(payload={"sku": "BBD-YENI-1", "type": "simple"},
                                  reason="Yeni kitap kaydı açılıyor", actor="Ayşe",
                                  dry_run=True)

    assert result["ok"] is True
    gonderilen = api.used("create_product")[0]
    assert gonderilen["payload"]["attribute_family_id"] == 7


async def test_birden_cok_aile_varsa_varsayilan_olan_secilir() -> None:
    service, api, _ = _service()
    api.families_payload = {"items": [
        {"id": 3, "code": "mobilya", "name": "Mobilya"},
        {"id": 5, "code": "default", "name": "Varsayılan"},
    ], "meta": {}}

    await service.create(payload={"sku": "BBD-YENI-2"}, reason="Yeni kitap kaydı açılıyor",
                         actor="Ayşe", dry_run=True)

    assert api.used("create_product")[0]["payload"]["attribute_family_id"] == 5


async def test_acikca_verilen_aile_kimligi_ezilmez() -> None:
    service, api, _ = _service()
    api.families_payload = {"items": [{"id": 7, "code": "kitap", "name": "Kitap"}], "meta": {}}

    await service.create(payload={"sku": "BBD-YENI-3", "attributeFamilyId": 9},
                         reason="Yeni kitap kaydı açılıyor", actor="Ayşe", dry_run=True)

    assert api.used("create_product")[0]["payload"]["attribute_family_id"] == 9


async def test_hic_aile_yoksa_anlasilir_hata_doner_patlamaz() -> None:
    # K7: mağazada aile tanımlı değilse ekran çökmez, ne yapılacağını söyler.
    service, api, _ = _service()
    api.families_payload = {"items": [], "meta": {}}

    result = await service.create(payload={"sku": "BBD-YENI-4"},
                                  reason="Yeni kitap kaydı açılıyor", actor="Ayşe",
                                  dry_run=True)

    assert result["ok"] is False
    assert "aile" in result["error"].lower()
