"""Vergilendirme servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir.

SAHTE VERİ CANLI BİÇİMDEDİR. Mağaza yanıtları camelCase gelir
(`taxRate`/`isZip`/`taxCategoryId`/`createdAt`); testler bir zamanlar
snake_case sahte veriyle geçiyordu ve ekran canlıda boş açılıyordu. Buradaki
kayıtlar 2026-08-13'te `bbdstore.com.tr` yanıtlarından alınmıştır.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_tax_backend.capability import TaxRates
from store_tax_backend.service import TaxService
from store_tax_fakes import FakeApi, FakeLog, FakeStore

ORAN_20 = {"id": 1, "identifier": "KDV %20", "taxRate": 20, "country": "TR",
           "state": "", "isZip": False, "zipCode": "", "zipFrom": None, "zipTo": None,
           "createdAt": "2026-07-20T22:45:38+03:00"}
ORAN_10 = {"id": 2, "identifier": "KDV %10", "taxRate": "10.0000", "country": "TR",
           "state": "", "isZip": False, "zipCode": "*"}
KATEGORI_STD = {"id": 5, "code": "standart", "name": "Standart", "taxRates": [{"id": 1}]}
KATEGORI_INDIRIMLI = {"id": 6, "code": "indirimli", "name": "İndirimli", "taxRates": [2]}

URUN = {"id": 7, "sku": "KLM-1", "name": "Kalem", "urlKey": "kalem", "status": 1,
        "visibleIndividually": True, "price": "10.00", "taxCategoryId": 6}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[TaxService, FakeApi, FakeStore]:
    api = api or FakeApi(rates=[dict(ORAN_20), dict(ORAN_10)],
                         categories=[dict(KATEGORI_STD), dict(KATEGORI_INDIRIMLI)],
                         products={7: dict(URUN)})
    store = store or FakeStore()
    service = TaxService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("tax_rates")
    result = await service.rates()
    assert result["ok"] is True             # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_yetenek_patlarsa_tuketen_ekran_dusmez() -> None:
    service, api, _ = _service()
    api.fail.add("tax_categories")
    rates = TaxRates(service, FakeLog(), ttl=0)
    table = await rates.table()
    assert table["connected"] is False
    assert await rates.rates() == []
    assert await rates.for_category(5) is None


# ======================================== canlı alan adları (camelCase, TUZAK 7)

async def test_oran_yuzdesi_camelCase_yanittan_okunur() -> None:
    # CANLI: {"identifier":…, "taxRate":20, "isZip":false, "zipCode":""}
    # snake_case okuyan sürümde tablo "Oran okunamadı" ile doluyordu.
    service, _, _ = _service()
    row = next(item for item in (await service.rates())["items"] if item["id"] == 1)
    assert row["percent"] == 20.0
    assert row["percentLabel"] == "%20"
    assert row["zipLabel"] == "Tüm posta kodları"
    assert row["region"] == "Türkiye"


async def test_snake_case_yanit_da_okunmaya_devam_eder() -> None:
    # Mağaza paketi biçim değiştirirse ekran boşalmasın: iki ad da denenir.
    api = FakeApi(rates=[{"id": 1, "identifier": "KDV", "tax_rate": "8.5000",
                          "country": "TR", "is_zip": 0, "zip_code": "*"}], categories=[])
    service, _, _ = _service(api)
    assert (await service.rates())["items"][0]["percent"] == 8.5


async def test_kategori_oran_iliskisi_camelCase_ve_nesne_listesiyle_okunur() -> None:
    service, _, _ = _service()
    result = await service.categories()
    row = next(item for item in result["items"] if item["id"] == 5)
    assert row["rateIds"] == [1]
    assert row["ratesKnown"] is True
    assert row["percentLabel"] == "%20"


async def test_iliski_null_gelirse_ORANSIZ_DENMEZ() -> None:
    # CANLI: liste ucu her kategori için "taxRates": null döndürüyor; aynı
    # kategorinin tekil ucunda ilişki dolu. `null` "oranı yok" DEĞİLDİR.
    api = FakeApi(rates=[dict(ORAN_20)],
                  categories=[{"id": 5, "code": "std", "name": "Standart", "taxRates": None}])
    service, _, _ = _service(api)
    kategoriler = await service.categories()
    assert kategoriler["items"][0]["ratesKnown"] is False
    assert kategoriler["items"][0]["rateCount"] is None
    assert kategoriler["membershipKnown"] is False
    assert "taxRates: null" in kategoriler["membershipNotice"]

    oranlar = await service.rates()
    satir = oranlar["items"][0]
    assert satir["unassigned"] is False           # "bağlantısız" DENMEZ
    assert oranlar["counts"]["unassigned"] == 0


async def test_iliski_okunamazsa_kategori_guncellemesi_REDDEDILIR() -> None:
    # Bagisto kısmi `taxrates` kabul etmiyor: gönderilmeyen oran kategoriden
    # düşer. Mevcut ilişkiyi göremeden yazmak sessiz silme olurdu.
    api = FakeApi(rates=[dict(ORAN_20)],
                  categories=[{"id": 5, "code": "std", "name": "Standart", "taxRates": None}])
    service, _, _ = _service(api)
    result = await service.save_category(5, payload={"code": "std", "name": "Standart",
                                                     "rateIds": [1]},
                                         reason="Kategori adı düzeltiliyor", actor="Ali",
                                         dry_run=False)
    assert result["ok"] is False
    assert api.used("save_tax_category") == []


# ==================================================== oran ↔ kategori ilişkisi

async def test_oranin_hangi_kategorilerde_oldugu_ters_ceviriyle_bulunur() -> None:
    # Kategori kaydı oranları taşıyor, oran kaydı kategorileri taşımıyor.
    service, _, _ = _service()
    result = await service.rates()
    by_id = {row["id"]: row for row in result["items"]}
    assert by_id[1]["categoryIds"] == [5]
    assert by_id[2]["categoryIds"] == [6]


async def test_yerel_gecerlilik_notu_oran_satirinda_gorunur() -> None:
    service, _, store = _service()
    await service.save_effective(1, effective_from="2099-01-01", note="Tebliğ", actor="Ayşe")
    result = await service.rates()
    row = next(item for item in result["items"] if item["id"] == 1)
    assert row["effectiveFrom"] == "2099-01-01"
    assert row["effectiveNote"] == "Tebliğ"
    assert store.effective[1]["actor"] == "Ayşe"


async def test_gecmis_tarihli_gecerlilik_notu_reddedilir() -> None:
    service, _, store = _service()
    result = await service.save_effective(1, effective_from="2000-01-01", note="", actor="Ayşe")
    assert result["ok"] is False
    assert store.effective == {}


# =============================================================== süzgeçler

async def test_hicbir_urune_atanmamis_suzgeci_kategorisiz_orani_bulur() -> None:
    api = FakeApi(rates=[dict(ORAN_20), dict(ORAN_10)], categories=[dict(KATEGORI_STD)])
    service, _, _ = _service(api)
    result = await service.rates(flag="unassigned")
    assert [row["id"] for row in result["items"]] == [2]
    assert result["counts"]["unassigned"] == 1


async def test_cakisan_kural_suzgeci_ayni_kategorideki_ortusen_oranlari_bulur() -> None:
    api = FakeApi(rates=[dict(ORAN_20), dict(ORAN_10)],
                  categories=[{"id": 5, "code": "std", "name": "Standart", "taxRates": [1, 2]}])
    service, _, _ = _service(api)
    result = await service.rates(flag="conflict")
    assert {row["id"] for row in result["items"]} == {1, 2}
    assert result["conflicts"][0]["kind"] == "conflict"


async def test_arama_ad_oran_ve_bolgede_eslesir() -> None:
    service, _, _ = _service()
    assert len((await service.rates(q="%10"))["items"]) == 1
    assert len((await service.rates(q="türkiye"))["items"]) == 2
    assert len((await service.rates(q="yok"))["items"]) == 0


async def test_oran_araligi_okunamayan_orani_disarida_birakir() -> None:
    api = FakeApi(rates=[dict(ORAN_20), {"id": 3, "identifier": "Bozuk", "taxRate": "",
                                         "country": "TR"}],
                  categories=[])
    service, _, _ = _service(api)
    result = await service.rates(min_percent=0)
    assert [row["id"] for row in result["items"]] == [1]


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.save_rate(1, payload={"name": "A", "percent": 20, "country": "TR"},
                                     reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("save_tax_rate") == []


async def test_gecerlilik_tarihi_MAGAZAYA_GITMEZ() -> None:
    # Bagisto vergi oranında tarih alanı yok; göndermek Laravel'in alanı
    # sessizce yok saymasına ve kullanıcının "ayarladım" sanmasına yol açardı.
    service, api, store = _service()
    result = await service.save_rate(1, payload={"name": "KDV %20", "percent": 20,
                                                 "country": "TR"},
                                     effective_from="2099-01-01", note="Tebliğ",
                                     reason="Tebliğ ile oran güncellendi", actor="Ali",
                                     dry_run=False)
    assert result["ok"] is True
    gonderilen = api.used("save_tax_rate")[0]["payload"]
    assert "effective_from" not in gonderilen
    assert "effectiveFrom" not in gonderilen
    assert store.effective[1]["effective_from"] == "2099-01-01"


async def test_gecerlilik_notu_kuru_provada_YAZILMAZ() -> None:
    # Kuru prova mağazaya hiç gitmiyor; notu yazmak "oran değişti" izlenimi verirdi.
    service, _, store = _service()
    await service.save_rate(1, payload={"name": "KDV %20", "percent": 20, "country": "TR"},
                            effective_from="2099-01-01",
                            reason="Kuru prova denemesi yapıldı", actor="Ali", dry_run=True)
    assert store.effective == {}


async def test_oran_yazmasi_oku_degistir_yaz_yapar() -> None:
    service, api, _ = _service()
    await service.save_rate(1, payload={"percent": 18, "name": "KDV %18", "country": "TR"},
                            reason="Oran tebliğ ile düştü", actor="Ali", dry_run=False)
    body = api.used("save_tax_rate")[0]["payload"]
    assert body["tax_rate"] == "18.0000"
    assert body["zip_code"] == "*"          # mevcut kayıttan korundu
    assert body["is_zip"] == 0


async def test_gecmis_faturalar_uyarisi_her_yazma_yanitinda_doner() -> None:
    service, _, _ = _service()
    result = await service.save_rate(1, payload={"name": "KDV %20", "percent": 20,
                                                 "country": "TR"},
                                     reason="Yıllık oran gözden geçirmesi", actor="Ali",
                                     dry_run=False)
    assert "GEÇMİŞ faturaları etkilemez" in result["notice"]


async def test_oransiz_kategori_magazaya_hic_gitmez() -> None:
    service, api, _ = _service()
    result = await service.save_category(None, payload={"code": "yeni", "name": "Yeni",
                                                        "rateIds": []},
                                         reason="Yeni kategori açılıyor", actor="Ali")
    assert result["ok"] is False
    assert "en az bir oran" in result["error"]
    assert api.used("save_tax_category") == []


async def test_kategori_guncellemesinde_oran_listesi_tam_gider() -> None:
    service, api, _ = _service()
    await service.save_category(5, payload={"code": "standart", "name": "Standart",
                                            "rateIds": [1, 2]},
                                reason="İkinci oran kategoriye eklendi", actor="Ali",
                                dry_run=False)
    assert api.used("save_tax_category")[0]["payload"]["taxrates"] == [1, 2]


# ============================================================ ürün eşlemesi

async def test_eslemede_bilinmeyen_vergi_kategorisi_atanmamis_DENMEZ() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 7, "sku": "KLM-1", "name": "Kalem"}],
                        "meta": {"total": 1}}
    result = await service.mapping()
    assert result["items"][0]["taxKnown"] is False
    assert result["taxKnown"] is False


async def test_eslemede_camelCase_vergi_kategorisi_okunur() -> None:
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 7, "sku": "KLM-1", "name": "Kalem",
                                   "taxCategoryId": 6}], "meta": {"total": 1}}
    result = await service.mapping()
    assert result["items"][0]["taxKnown"] is True
    assert result["items"][0]["taxCategoryName"] == "İndirimli"


async def test_esleme_suzgeci_uygulanamadiysa_BOS_TABLO_SESSIZ_KALMAZ() -> None:
    # Canlıda liste ucu `taxCategoryId` alanını doldurmuyor; süzgeç boş liste
    # üretiyor. "Bu kategoride ürün yok" sanılmasın diye ekran açıklama alır.
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 7, "sku": "KLM-1", "name": "Kalem",
                                   "taxCategoryId": None}], "meta": {"total": 1}}
    result = await service.mapping(tax_category_id=6)
    assert result["items"] == []
    assert "ANLAMINA GELMEZ" in result["note"]


async def test_urun_govdesi_camelCase_alanlari_koruyarak_gider() -> None:
    # OKU-DEĞİŞTİR-YAZ: yanıt camelCase, gövde snake_case (OpenAPI şeması böyle).
    service, api, _ = _service()
    preview = await service.assign_preview(product_ids=[7], tax_category_id=5)
    await service.assign_apply(token=preview["token"], reason="Kalem standart orana taşındı",
                               actor="Ali", dry_run=False)
    body = api.used("update_product")[0]["payload"]
    assert body["url_key"] == "kalem"
    assert body["visible_individually"] == 1          # JSON boolean 0/1'e indirgenir
    assert body["tax_category_id"] == 5


async def test_katalog_taramasi_alan_yoksa_HICBIR_SEY_kaydetmez() -> None:
    # Sıfır yazmak "hiçbir ürüne atanmamış" süzgecini yanlış yakardı.
    service, api, store = _service()
    api.list_payload = {"items": [{"id": 7, "sku": "A"}], "meta": {}}
    result = await service.scan_usage(actor="Ali")
    assert result["ok"] is False
    assert store.usage == {}


async def test_katalog_taramasi_sifir_urunlu_kategoriyi_de_yazar() -> None:
    # "Taranmadı" ile "ürünü yok" ayrımı ancak böyle korunur.
    service, api, store = _service()
    api.list_payload = {"items": [{"id": 7, "taxCategoryId": 6}], "meta": {}}
    result = await service.scan_usage(actor="Ali")
    assert result["ok"] is True
    assert store.usage[6]["product_count"] == 1
    assert store.usage[5]["product_count"] == 0


async def test_onizleme_gorulmeden_toplu_esleme_uygulanmaz() -> None:
    service, api, _ = _service()
    result = await service.assign_apply(token="yokboylejeton", reason="Yanlış kategori düzeltildi",
                                        actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert "Önizleme bulunamadı" in result["error"]
    assert api.used("update_product") == []


async def test_ayni_onizleme_iki_kez_uygulanamaz() -> None:
    service, _, _ = _service()
    preview = await service.assign_preview(product_ids=[7], tax_category_id=5)
    token = preview["token"]
    await service.assign_apply(token=token, reason="Yanlış kategori düzeltildi", actor="Ali",
                               dry_run=False)
    ikinci = await service.assign_apply(token=token, reason="Yanlış kategori düzeltildi",
                                        actor="Ali", dry_run=False)
    assert ikinci["ok"] is False
    assert "zaten uygulandı" in ikinci["error"]


async def test_onizleme_urunu_TAZE_okur_ve_zaten_dogru_olani_atlar() -> None:
    service, api, _ = _service()
    preview = await service.assign_preview(product_ids=[7], tax_category_id=6)
    assert api.used("product") or api.args_of("product") == [(7,)]
    assert preview["rows"][0]["skipped"] is True
    assert preview["summary"]["changed"] == 0


async def test_esleme_siniri_asilirsa_onizleme_bile_acilmaz() -> None:
    # Mağazada toplu uç yok; 500 ürünü tek tek yazmak hız sınırına takılır ve
    # yarıda kalırsa kataloğun yarısı yanlış KDV'de kalır.
    service, _, _ = _service(assign_limit=2)
    result = await service.assign_preview(product_ids=[1, 2, 3], tax_category_id=5)
    assert result["ok"] is False
    assert "en çok 2 ürün" in result["error"]


async def test_esleme_kapaliysa_uygulama_reddedilir() -> None:
    service, api, _ = _service(assign_limit=0)
    result = await service.assign_preview(product_ids=[7], tax_category_id=5)
    assert result["ok"] is False
    assert api.used("product") == []


async def test_esleme_uygulamasi_oku_degistir_yaz_ile_gider() -> None:
    service, api, store = _service()
    preview = await service.assign_preview(product_ids=[7], tax_category_id=5)
    result = await service.assign_apply(token=preview["token"],
                                        reason="Kalem standart orana taşındı", actor="Ali",
                                        dry_run=False)
    assert result["ok"] is True
    body = api.used("update_product")[0]["payload"]
    assert body["tax_category_id"] == 5
    assert body["sku"] == "KLM-1"           # dokunulmayan alan korundu
    assert body["channel"] == "default"
    assert store.assign[preview["token"]]["status"] == "applied"


async def test_bir_urun_patlarsa_gerisi_yazilir_ve_sayilir() -> None:
    api = FakeApi(rates=[dict(ORAN_20)], categories=[dict(KATEGORI_STD)],
                  products={7: dict(URUN), 8: {**URUN, "id": 8, "sku": "KLM-2"}})
    service, _, _ = _service(api)
    preview = await service.assign_preview(product_ids=[7, 8], tax_category_id=5)
    api.fail.add("update_product")
    result = await service.assign_apply(token=preview["token"], reason="Toplu düzeltme yapıldı",
                                        actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert result["applied"] == 0
    assert result["failed"] == 2


async def test_denetim_izi_gerekce_ve_kimlikle_yazilir() -> None:
    service, _, store = _service()
    await service.save_rate(1, payload={"name": "KDV %20", "percent": 20, "country": "TR"},
                            reason="Yıllık oran gözden geçirmesi", actor="Ayşe", dry_run=False)
    kayit = [row for row in store.audit if row["result"] == "ok"]
    assert kayit and kayit[0]["reason"] == "Yıllık oran gözden geçirmesi"
    assert kayit[0]["actor"] == "Ayşe"
    assert kayit[0]["target"] == "rate:1"


# ============================================================== KDV icmali

def _invoice(**extra: Any) -> dict[str, Any]:
    # Canlı fatura yanıtının biçimi: camelCase alanlar, `items` LİSTE ucunda boş.
    base = {"id": 1, "incrementId": "1", "createdAt": "2026-08-05 10:00:00",
            "channelName": "Web", "baseSubTotal": "1000.00", "baseTaxAmount": "200.00",
            "baseGrandTotal": "1200.00",
            "items": [{"taxPercent": "20", "baseTotal": "1000.00",
                       "baseTaxAmount": "200.00"}]}
    base.update(extra)
    return base


async def test_icmal_uc_belge_kumesini_de_ceker() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [_invoice()], "meta": {}}
    result = await service.summary(start="2026-08-01", end="2026-08-31")
    assert result["ok"] is True
    assert result["totals"]["netTax"] == 20_000
    assert api.used("invoices")[0]["all_pages"] is True
    assert api.args_of("orders")[0][0]["status"] == "canceled"


async def test_belge_tavanina_dayanilirsa_ICMAL_URETILMEZ() -> None:
    # Eksik belgeyle hesaplanan matrah beyanı eksiltir ve fark edilmez.
    service, api, _ = _service(document_cap=100)
    api.invoice_payload = {"items": [_invoice(id=index) for index in range(100)], "meta": {}}
    result = await service.summary(start="2026-08-01", end="2026-08-31")
    assert result["ok"] is False
    assert "tavana" in result["error"]


async def test_sunucu_tarih_suzgecini_uygulamadiysa_ICMAL_URETILMEZ() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [_invoice(createdAt="2026-01-05 10:00:00")], "meta": {}}
    result = await service.summary(start="2026-08-01", end="2026-08-31")
    assert result["ok"] is False
    assert "tarih süzgecini uygulamadı" in result["error"]


async def test_belge_okunamazsa_icmal_uretilmez_ama_uc_patlamaz() -> None:
    service, api, _ = _service()
    api.fail.add("refunds")
    result = await service.summary(start="2026-08-01", end="2026-08-31")
    assert result["ok"] is False
    assert result["connected"] is False
    assert "Belge listesi okunamadı (iade)" in result["error"]


async def test_ters_tarih_araligi_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.summary(start="2026-08-31", end="2026-08-01")
    assert result["ok"] is False


async def test_tanimli_oranlar_okunamazsa_icmal_uyarir_ama_uretilir() -> None:
    service, api, _ = _service()
    api.invoice_payload = {"items": [_invoice()], "meta": {}}
    api.fail.add("tax_rates")
    result = await service.summary(start="2026-08-01", end="2026-08-31")
    assert result["ok"] is True
    assert result["ratesConnected"] is False
    assert any("Tanımlı oranlar okunamadı" in item for item in result["warnings"])


async def test_kanal_suzgeci_MAGAZAYA_GONDERILMEZ_yerelde_uygulanir() -> None:
    # FATURA/İADE: canlıda `?channel=zzzz` bile bütün kayıtları döndürüyor;
    # Laravel tanımadığı parametreyi yok sayıyor ve göndermek "süzdüm"
    # yanılgısı üretirdi.
    #
    # SİPARİŞ: burası tam tersi ve tehlikeli olan da bu. Sipariş ucu kanalı
    # GERÇEKTEN uyguluyor, üstelik kimlik bekliyor (2026-08-16: `channel=1`
    # → 18, `channel=default` → 0). Kanal adını iptal listesine göndermek
    # listeyi sessizce boşaltır ve rapor "bu dönemde iptal yok" derdi.
    # Üç ucun da kanalsız çağrıldığı bu yüzden sınanıyor.
    service, api, _ = _service()
    api.invoice_payload = {"items": [_invoice(id=1, channelName="Web"),
                                     _invoice(id=2, channelName="Mobil")], "meta": {}}
    result = await service.summary(start="2026-08-01", end="2026-08-31", channel="Mobil")
    assert "channel" not in api.args_of("invoices")[0][0]
    assert "channel" not in api.args_of("refunds")[0][0]
    assert "channel" not in api.args_of("orders")[0][0]
    assert result["documents"]["invoices"] == 1
    assert [row["channel"] for row in result["channels"]] == ["Mobil"]
    assert any("Kanal süzgeci" in item for item in result["warnings"])


# ================================================================== rapor

async def test_rapor_klasoru_disindaki_dosya_BASILMAZ() -> None:
    class SahteYazici:
        async def print_file(self, path: Any, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("bu çağrılmamalıydı")

    service, _, _ = _service()
    # Yazıcı yeteneği test kurulumunda doğrudan yerleştirilir.
    service._printer = SahteYazici()
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.build_report("uydurma", {})
    assert result["ok"] is False
    assert "Bilinmeyen rapor" in result["error"]


async def test_yazici_yoksa_ekran_calismaya_devam_eder() -> None:
    service, _, _ = _service()
    assert (await service.printer_status())["ready"] is False
    assert (await service.print_report("/tmp/x.pdf"))["ok"] is False


# ================================================================ yetenek

async def test_yetenek_kategori_bazli_oran_dondurur() -> None:
    service, _, _ = _service()
    rates = TaxRates(service, FakeLog(), ttl=0)
    found = await rates.for_category(5)
    assert found is not None
    assert found["percent"] == 20.0
    assert await rates.for_category(999) is None


async def test_yetenek_onbellegi_tekrar_istek_atmaz_ve_dusurulebilir() -> None:
    service, api, _ = _service()
    rates = TaxRates(service, FakeLog(), ttl=600)
    await rates.table()
    await rates.table()
    assert len(api.used("tax_rates")) == 1
    rates.forget()
    await rates.table()
    assert len(api.used("tax_rates")) == 2


# ================================================================== CSV

async def test_oran_csv_rapor_klasorune_yazilir(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.export_csv("rates")
    assert result["ok"] is True
    assert result["rows"] == 2
    assert Path(result["path"]).read_bytes().startswith(b"\xef\xbb\xbf")


async def test_esleme_jetonunun_farki_yeniden_HESAPLANMAZ() -> None:
    # Kullanıcı neyi onayladıysa o uygulanır: jetondaki satırlar okunur.
    service, _, store = _service()
    preview = await service.assign_preview(product_ids=[7], tax_category_id=5)
    kayitli = json.loads(store.assign[preview["token"]]["rows"])
    assert kayitli[0]["afterId"] == 5
    assert kayitli[0]["beforeId"] == 6


# ============================== DERİN İNCELEME — onarılan kusurların testleri

async def test_kunyesiz_yanit_DUZENLEMEYE_ACILMAZ() -> None:
    # Mağaza 200 dönüp boş/tanınmayan gövde verirse `rate_row` yine de satır
    # üretiyordu: id 0, ad "#0", oran "okunamadı". Çekmece o hayali kaydı
    # düzenlemeye açıyor ve kullanıcı kaydettiğinde ne olacağını bilmiyordu.
    service, api, _ = _service()

    async def bos_kunye(rate_id: int) -> dict[str, Any]:
        return {}

    api.tax_rate = bos_kunye                      # type: ignore[assignment]
    result = await service.rate(1)
    assert result["ok"] is False
    assert result["connected"] is True            # mağaza ayakta, veri anlamsız
    assert "künyesi okunamadı" in result["error"]
    assert "rate" not in result


async def test_sifir_kimlikli_guncelleme_MAGAZAYA_HIC_GITMEZ() -> None:
    # `PUT /rates/0` yolundan gelen 0 "yeni kayıt" gibi görünüyor (`if rate_id:`
    # yanlış) ama geçide `rate_id=0` gidiyordu ve `0 is None` yanlış olduğu için
    # mağazaya `PUT /settings/tax-rates/0` atılıyordu.
    service, api, _ = _service()
    result = await service.save_rate(
        0, payload={"name": "X", "percent": 10, "country": "TR"},
        reason="sıfır kimlik denemesi", actor="Ali", dry_run=True)
    assert result["ok"] is False
    assert "kimliği geçersiz" in result["error"]
    assert api.args_of("save_tax_rate") == []
    assert api.args_of("tax_rate") == []


async def test_sifir_kimlikli_kategori_guncellemesi_de_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.save_category(
        0, payload={"code": "c", "name": "n", "rateIds": [1]},
        reason="sıfır kimlik denemesi", actor="Ali", dry_run=True)
    assert result["ok"] is False
    assert "kimliği geçersiz" in result["error"]
    assert api.args_of("save_tax_category") == []


async def test_sifir_kimlikli_gecerlilik_notu_yazilmaz() -> None:
    service, _, store = _service()
    result = await service.save_effective(0, effective_from="", note="x", actor="Ali")
    assert result["ok"] is False
    assert store.effective == {}


async def test_magaza_KAPALIYKEN_onizleme_BULUNAMADI_DEMEZ() -> None:
    # Kategori listesi okunamazken arama boş dönüyor ve ekran "hedef vergi
    # kategorisi mağazada bulunamadı" diyordu. Kategori duruyor, mağaza
    # kapalıydı: kullanıcıyı var olmayan bir veri sorununu aramaya yollardı.
    service, api, _ = _service()
    api.fail.add("tax_rates")
    result = await service.assign_preview(product_ids=[7], tax_category_id=5)
    assert result["ok"] is False
    assert result["connected"] is False
    assert "bulunamadı" not in result["error"]
    assert "okunamadı" in result["error"]


async def test_kategori_listesi_okunamazsa_TARAMA_HICBIR_SEY_YAZMAZ() -> None:
    # Yalnız üründe GÖRÜLEN kategoriler yazılıyordu; ürünü kalmamış bir kategori
    # eski (sıfır olmayan) sayısıyla asılı kalıyor ve «hiçbir ürüne atanmamış»
    # süzgeci sessizce yanlış çalışıyordu.
    service, api, store = _service()
    store.usage[5] = {"tax_category_id": 5, "product_count": 12, "scanned_at": "eski"}
    api.list_payload = {"items": [{"id": 7, "taxCategoryId": 6}], "meta": {}}
    api.fail.add("tax_rates")
    result = await service.scan_usage(actor="Ali")
    assert result["ok"] is False
    assert "KAYDEDİLMEDİ" in result["error"]
    assert store.usage[5]["product_count"] == 12      # eski sayı bozulmadı
    assert 6 not in store.usage                       # yarım tablo yazılmadı


async def test_eslemede_kategori_ucu_dusunce_ekran_SEBEBINI_SOYLER() -> None:
    # Ürün listesi geliyor ama vergi kategorisi listesi ayrı uçtan ve ayrı
    # düşebiliyor. Düştüğünde açılır kutu boşalıyor, "Fark tablosunu göster"
    # hiçbir zaman çalışmıyor ve sebebi ekranda yazmıyordu.
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 7, "sku": "KLM-1", "name": "Kalem",
                                   "taxCategoryId": 6}],
                        "meta": {"total": 1, "currentPage": 1, "perPage": 50, "lastPage": 1}}
    api.fail.add("tax_rates")
    result = await service.mapping()
    assert result["connected"] is True                # ürün listesi okundu
    assert result["categoriesConnected"] is False
    assert result["categories"] == []
    assert "Vergi kategorisi listesi okunamadı" in result["note"]
    assert result["categoriesError"]


async def test_iliski_okunamazken_CSV_sifir_kategori_YAZMAZ(tmp_path: Path) -> None:
    # Ekran «okunamadı» derken CSV `0` yazıyordu: aynı veriden iki farklı cevap.
    # Kâğıttaki 0, mali müşavire "bu oran hiçbir kategoride kullanılmıyor" diye
    # okunur. Canlıda liste ucu ilişkiyi hiç döndürmediği için bu satır kâğıtta
    # HER ZAMAN 0 çıkıyordu.
    api = FakeApi(rates=[dict(ORAN_20)],
                  categories=[{"id": 5, "code": "standart", "name": "Standart",
                               "taxRates": None}])
    service, _, _ = _service(api=api, export_path=str(tmp_path))
    result = await service.export_csv("rates")
    assert result["ok"] is True
    metin = Path(result["path"]).read_text(encoding="utf-8-sig")
    assert "okunamadı" in metin
    assert ";0;" not in metin


async def test_iliski_okunabiliyorsa_CSV_gercek_sayiyi_yazar(tmp_path: Path) -> None:
    service, _, _ = _service(export_path=str(tmp_path))
    result = await service.export_csv("rates")
    metin = Path(result["path"]).read_text(encoding="utf-8-sig")
    assert "okunamadı" not in metin


async def test_iliski_okunamazken_PDF_nedenini_dipnota_dusurur(tmp_path: Path) -> None:
    api = FakeApi(rates=[dict(ORAN_20)],
                  categories=[{"id": 5, "code": "standart", "name": "Standart",
                               "taxRates": None}])
    service, _, _ = _service(api=api, export_path=str(tmp_path))
    result = await service.build_report("rates", {})
    assert result["ok"] is True
    assert result["bytes"] > 0
