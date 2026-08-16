"""Ürün silme + “silinmiş” ibaresi + tek seçenekli alanlar.

AĞA ÇIKMAZ, GERÇEK DB KULLANMAZ, CANLIYA YAZMAZ: `store.api` sahtedir ve
sahte silme yalnız kendi sözlüğünden satır düşürür.

Bu dosyanın kanıtlamaya çalıştığı şey "silme çağrısı yapıldı mı" değil,
KARARIN DOĞRU VERİLDİĞİ: ne silineceği gösterilmeden silinmiyor mu, sayı
bilinmiyorken sıfır uydurulmuyor mu, biri patlayınca diğerleri gidiyor mu,
ve mağaza okunamazken geçmiş toptan "silinmiş" boyanmıyor mu.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from store_products_backend import deleted
from store_products_backend.service import ProductsService
from store_products_fakes import FakeApi, FakeLog, FakeStore, FakeStoreError

URUN = {
    "id": 5, "sku": "KLM-1", "type": "simple", "attribute_family_id": 3, "status": 1,
    "name": "Kalem", "url_key": "kalem", "price": "10.00", "categories": [{"id": 4}],
}
IKINCI = {**URUN, "id": 6, "sku": "KLM-2", "name": "Silgi", "url_key": "silgi"}

GEREKCE = "yanlış açılan ürün kaldırılıyor"


class GecitsizApi(FakeApi):
    """Geçidin BUGÜNKÜ hâli: ürün silme metodu YOK.

    Sınıf üzerinde `None`'a bağlanır çünkü servis ucu `getattr(..., None)` ile
    arıyor — yani "metot yok" ile "metot None" servis için aynı şeydir ve
    testte örnek üstünden `del` yapmak (metot sınıfta durduğu için) mümkün
    değil.
    """

    delete_product = None


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[ProductsService, FakeApi, FakeStore]:
    api = api or FakeApi({5: dict(URUN), 6: dict(IKINCI)})
    # Ürün açma yolu aile çözemezse hiç yazmaya gitmiyor; vergi kategorisi
    # testi o yolun sonuna bakıyor.
    api.families_payload = {"items": [{"id": 7, "code": "kitap", "name": "Kitap"}], "meta": {}}
    store = store or FakeStore()
    service = ProductsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "low_stock_threshold": 5, **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ====================================================== önizleme (dryRun kapısı)

async def test_onizleme_ne_silinecegini_gosterir_ve_hicbir_sey_silmez() -> None:
    service, api, _ = _service()
    api.bestsellers = {5: (3, 7)}

    result = await service.delete_preview([5])

    assert result["ok"] is True
    row = result["rows"][0]
    assert row["sku"] == "KLM-1"
    assert row["sales"]["orderCount"] == 3
    assert row["sales"]["soldQty"] == 7
    # ÖNİZLEME YAZMAZ: silme çağrısı hiç yapılmadı.
    assert api.used("delete_product") == []
    assert api.deleted_ids == []


async def test_onizleme_satis_gecmisi_bilinmiyorsa_sifir_uydurmaz() -> None:
    """Bilinmeyeni sıfır saymak, "hiç satılmamış" diye gösterip kullanıcıyı
    yanlış güvenle sildirmek olurdu. Silme geri alınamaz."""
    service, api, _ = _service()
    api.fail.add("bbd_bestsellers")

    result = await service.delete_preview([5])

    sales = result["rows"][0]["sales"]
    assert sales["state"] == "unknown"
    assert sales["orderCount"] is None
    assert result["summary"]["salesUnknown"] == 1


async def test_onizleme_baska_urunun_sayisini_bu_urune_yazmaz() -> None:
    """Listede yalnız BAŞKA ürünün satırı varsa o rakam buraya taşınmaz.

    Uç süzgeç almıyor ve HER ZAMAN sıralamanın başındaki ürünleri döndürüyor
    (canlıda ölçüldü). Eşleşme tam kimlik üzerinden yapılır; 99'un 12 siparişi
    5 numaralı ürünün rakamı olamaz.
    """
    service, api, _ = _service()
    api.bestsellers = {99: (12, 40)}

    result = await service.delete_preview([5])

    sales = result["rows"][0]["sales"]
    assert sales["orderCount"] == 0
    assert sales["soldQty"] == 0


async def test_onizleme_hic_satilmamis_urunde_sifir_der() -> None:
    """Tablo YALNIZ satılmış ürünleri tutar: tam tarama sonunda satır yoksa 0."""
    service, api, _ = _service()
    api.bestsellers = {99: (1, 1)}          # bu ürün tabloda yok = hiç satılmamış

    result = await service.delete_preview([5])

    assert result["rows"][0]["sales"] == {
        "state": "known", "orderCount": 0, "soldQty": 0, "lastOrderedAt": "",
        "note": "Bu ürün hiçbir siparişte geçmiyor."}


async def test_onizleme_yarim_kalan_taramada_sifir_uydurmaz() -> None:
    """Tarama kayıt sınırına dayandıysa "satır yok" = "hiç satılmamış" DEĞİLDİR.

    Sıfır göstermek, aslında satılmış bir ürünü yanlış güvenle sildirirdi;
    silme geri alınamaz.
    """
    service, api, _ = _service()
    api.bestsellers = {99: (1, 1)}
    api.bestsellers_truncated = True

    result = await service.delete_preview([5])

    sales = result["rows"][0]["sales"]
    assert sales["state"] == "unknown"
    assert sales["orderCount"] is None
    assert "sonuna kadar okunamadı" in sales["note"]
    assert result["summary"]["salesUnknown"] == 1


async def test_onizleme_satis_ozetini_secim_basina_bir_kez_tarar() -> None:
    """25 ürünlük seçimde 25 tarama hız kovasını (dakikada 55) bitirirdi."""
    service, api, _ = _service()
    api.bestsellers = {5: (3, 7), 6: (1, 1)}

    await service.delete_preview([5, 6])

    cagrilar = [args for name, args, _ in api.calls if name == "bbd_bestsellers"]
    assert len(cagrilar) == 1
    # Süzgeç GÖNDERİLMEZ: uç onu yok sayıyor, gönderen "süzdüm" sanırdı.
    assert cagrilar[0][0] is None
    assert api.used("bbd_bestsellers")[0]["all_pages"] is True


async def test_onizleme_gecitte_metot_yoksa_ekran_dusmez() -> None:
    """K7 dalı DURUYOR: geçit metodu bugün var, ekran ona bağımlı değil."""
    service, api, _ = _service()
    # Geçidin bu metodu tanımadığı hâl: servis `getattr(..., None)` ile arıyor.
    api.bbd_bestsellers = None

    result = await service.delete_preview([5])

    assert result["ok"] is True
    assert result["rows"][0]["sales"]["state"] == "unavailable"
    assert result["rows"][0]["sales"]["orderCount"] is None


async def test_onizleme_okunamayan_urun_listeye_girmez_ve_silinmez() -> None:
    service, _api, _ = _service()

    result = await service.delete_preview([5, 404])

    assert [row["id"] for row in result["rows"]] == [5]
    assert result["missing"] == [404]


async def test_onizleme_aktif_ve_varyantli_urun_icin_uyarir() -> None:
    service, api, _ = _service()
    api.products_by_id[5] = {**URUN, "variants": [{"id": 51}, {"id": 52}]}

    result = await service.delete_preview([5])

    assert result["rows"][0]["variantCount"] == 2
    metin = " ".join(result["warnings"])
    assert "VARYANTLARI DA silinir" in metin
    assert "AKTİF ürün var" in metin
    assert "RMA" in metin                    # veritabanı kısıtı söyleniyor


# ================================================================ silme

async def test_silme_gercekten_siler_ve_gerekce_denetim_izine_yazilir() -> None:
    service, api, store = _service()

    result = await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=False)

    assert result["ok"] is True
    assert result["deleted"] == [5]
    assert api.deleted_ids == [5]
    satirlar = [row for row in store.audit if row["action"] == "delete_product"]
    assert [row["result"] for row in satirlar] == ["denendi", "ok"]
    assert satirlar[-1]["reason"] == GEREKCE
    assert json.loads(satirlar[-1]["detail"])["sku"] == "KLM-1"


async def test_kisa_gerekce_backendde_reddedilir_ve_hicbir_sey_silinmez() -> None:
    """Arayüzde gizlemek yetkilendirme değildir (K9): istek elle de kurulabilir."""
    service, api, _ = _service()

    result = await service.delete_products([5], reason="ok", actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "en az 10 karakter" in result["error"]
    assert api.deleted_ids == []


async def test_kuru_prova_silmez() -> None:
    service, api, _ = _service()

    result = await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=True)

    assert result["ok"] is True
    assert result["dryRun"] is True
    assert api.deleted_ids == []             # istek gönderildi ama ürün DURUYOR


async def test_toplu_silmede_biri_patlayinca_digerleri_silinir() -> None:
    """Kısmi başarı GERÇEKÇİ raporlanır: hangisi neden silinemedi yazılır.

    Mağazada toplu uç var ama tek ürün patlayınca TAMAMINA 500 dönüyor ve
    hangisinin gittiği okunamıyor; bu yüzden sırayla silinir.
    """
    service, api, _ = _service()

    calls: list[int] = []
    orijinal = api.delete_product

    async def patlayan(product_id: int, **kwargs: Any) -> dict[str, Any]:
        calls.append(product_id)
        if product_id == 5:
            raise FakeStoreError("Mağaza hata verdi (500)", status=500, code="server")
        return await orijinal(product_id, **kwargs)

    api.delete_product = patlayan  # type: ignore[method-assign]

    result = await service.delete_products([5, 6], reason=GEREKCE, actor="Kemal", dry_run=False)

    assert calls == [5, 6]                   # biri patladı diye durulmadı
    assert result["ok"] is False
    assert result["deleted"] == [6]
    assert len(result["failed"]) == 1
    assert result["failed"][0]["sku"] == "KLM-1"
    # 500 anlaşılır hâle gelir: en olası neden iade talebi kısıtıdır.
    assert "RESTRICT" in result["failed"][0]["error"]


async def test_zaten_silinmis_urun_hata_degil_zaten_yok_sayilir() -> None:
    """Aynı düğmeye iki kez basmak hata göstermemeli."""
    service, _api, _ = _service()

    result = await service.delete_products([404], reason=GEREKCE, actor="Kemal", dry_run=False)

    assert result["ok"] is True
    assert result["failed"] == []
    assert result["missing"][0]["id"] == 404


async def test_okunamayan_urun_silinmez() -> None:
    """"Ürünü okuyamadım" ile "ürün yok" ayrı şeylerdir; ilkinde silinmez."""
    service, api, _ = _service()
    api.fail.add("product")

    result = await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert api.deleted_ids == []
    assert "okunamadı" in result["failed"][0]["error"]


async def test_tavani_asan_secim_reddedilir() -> None:
    service, _, _ = _service()

    result = await service.delete_products(list(range(1, 200)), reason=GEREKCE,
                                           actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "en çok 25" in result["error"]


async def test_yinelenen_kimlik_tek_kez_silinir() -> None:
    service, api, _ = _service()

    result = await service.delete_products([5, 5, 5], reason=GEREKCE, actor="Kemal",
                                           dry_run=False)

    assert result["deleted"] == [5]
    assert api.deleted_ids == [5]


async def test_gecitte_silme_ucu_yoksa_neden_soylenir_ve_ham_istek_atilmaz() -> None:
    """K4: modül mağazaya ham istek atmaz. Uç geçide eklenene kadar ekran
    "uç yok" der; sessizce başarısız olmaz ya da kendi istemcisini kurmaz."""
    service, _api, _ = _service(GecitsizApi({5: dict(URUN)}))

    result = await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "delete_product" in result["error"]
    assert result["failed"][0]["id"] == 5

    onizleme = await service.delete_preview([5])
    assert onizleme["capable"] is False      # panel düğmeyi kapatır


# ============================================== “silinmiş” ibaresi (saf kural)

def test_kalem_katalogda_yoksa_silinmis_sayilir() -> None:
    kalem = {"product_id": 5, "name": "Kalem", "sku": "KLM-1", "qty_ordered": 2}

    assert deleted.state_of(kalem, known_ids={9}, lookup_complete=True) == deleted.STATE_DELETED
    assert deleted.state_of(kalem, known_ids={5}, lookup_complete=True) == deleted.STATE_LIVE


def test_katalog_okunamadiysa_hicbir_kalem_silinmis_sayilmaz() -> None:
    """Mağaza bir dakika yanıt vermeyince bütün geçmişi kırmızı boyamak,
    "silinmiş" ibaresini bir daha güvenilmez kılardı."""
    kalem = {"product_id": 5, "name": "Kalem", "sku": "KLM-1"}

    assert deleted.state_of(kalem, known_ids=set(), lookup_complete=False) \
        == deleted.STATE_UNKNOWN


def test_ad_ve_sku_tasimayan_satir_silinmis_diye_isaretlenmez() -> None:
    assert deleted.state_of({"product_id": 0}, known_ids=set(), lookup_complete=True) \
        == deleted.STATE_UNKNOWN


def test_kimliksiz_ama_adli_kalem_silinmis_sayilir() -> None:
    """`order_items.product_id` NULL olabilir; kalem yine ürünü temsil eder."""
    kalem = {"product_id": None, "name": "Kalem", "sku": "KLM-1"}

    assert deleted.state_of(kalem, known_ids={5}, lookup_complete=True) == deleted.STATE_DELETED


def test_isaretleme_ozeti_ve_kirmizi_ton() -> None:
    rows = deleted.mark_items(
        [{"product_id": 5, "name": "Kalem", "sku": "KLM-1"},
         {"product_id": 9, "name": "Defter", "sku": "DFT-1"}],
        known_ids={9}, lookup_complete=True)

    assert deleted.summary(rows) == {"total": 2, "live": 1, "deleted": 1, "unknown": 0}
    silinen = rows[0]
    assert silinen["label"] == "silinmiş"
    assert silinen["tone"] == "bad"          # kırmızı
    assert rows[1]["label"] == ""


def test_benzersiz_kimlikler_tek_kez_sorulur() -> None:
    kalemler = [{"productId": 5}, {"product_id": 5}, {"productId": 9}]

    assert deleted.product_ids(kalemler) == [5, 9]


async def test_kalem_isaretleme_kataloga_soruyor_ve_404u_silinmis_sayiyor() -> None:
    service, _api, _ = _service()
    kalemler = [{"product_id": 5, "name": "Kalem", "sku": "KLM-1"},
                {"product_id": 404, "name": "Eski Kitap", "sku": "ESK-1"}]

    result = await service.mark_order_items(kalemler)

    assert result["lookupComplete"] is True
    assert result["items"][0]["deleted"] is False
    assert result["items"][1]["deleted"] is True
    assert result["items"][1]["label"] == "silinmiş"


async def test_kalem_isaretleme_magaza_dusunce_hicbirini_silinmis_yapmaz() -> None:
    service, api, _ = _service()
    api.fail.add("product")

    result = await service.mark_order_items([{"product_id": 5, "name": "Kalem"}])

    assert result["lookupComplete"] is False
    assert result["items"][0]["state"] == "unknown"
    assert result["items"][0]["deleted"] is False


async def test_gecmis_izi_silinen_urunu_kirmizi_isaretler() -> None:
    service, _api, _store = _service()
    await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=False)

    iz = await service.audit(product_id=5)

    assert iz["deletedIds"] == [5]
    assert iz["deletedLabel"] == "silinmiş"
    assert all(row["productDeleted"] for row in iz["items"])


async def test_kuru_provada_silinen_urun_gecmiste_silinmis_gorunmez() -> None:
    """`dry_run` gerçekten silmedi; izi "silinmiş" saymak yalan olurdu."""
    service, _, _ = _service()
    await service.delete_products([5], reason=GEREKCE, actor="Kemal", dry_run=True)

    iz = await service.audit(product_id=5)

    assert iz["deletedIds"] == []


# ====================================================== tek seçenekli alanlar

async def test_tek_secenekli_alanlar_gizlenir_ve_degeri_kendiliginden_gider() -> None:
    service, _api, _ = _service()

    referans = await service.reference()
    fields = referans["fields"]

    for key in ("channel", "locale", "currency", "sourceId", "taxCategoryId"):
        assert fields[key]["state"] == "single"
        assert fields[key]["visible"] is False
    assert fields["channel"]["auto"] == {"value": "default", "label": "Varsayılan"}
    assert fields["taxCategoryId"]["auto"] == {"value": 1, "label": "KDV"}


async def test_ikinci_kanal_acilinca_alan_geri_gelir() -> None:
    """SERT KODLAMA YOK: karar seçenek SAYISINDAN çıkıyor."""
    service, api, _ = _service()
    api.snapshot_parts["channels"] = [{"id": 1, "code": "default", "name": "Varsayılan"},
                                      {"id": 2, "code": "toptan", "name": "Toptan"}]
    api.snapshot_parts["inventory_sources"] = [{"id": 1, "name": "Merkez"},
                                               {"id": 2, "name": "Depo"}]

    fields = (await service.reference())["fields"]

    # Kanal ürünün alanı değil (her isteğe ayardan konuyor): alan çizilmez ama
    # "tek seçenek" yalanı da söylenmez — panel uyarı gösterir.
    assert fields["channel"]["state"] == "many"
    assert fields["channel"]["writable"] is False
    assert fields["channel"]["visible"] is False
    # Depo ürünün alanı: ikinci depo açılınca GERÇEK bir form alanı olarak döner.
    assert fields["sourceId"]["state"] == "many"
    assert fields["sourceId"]["visible"] is True
    assert len(fields["sourceId"]["options"]) == 2


async def test_secenek_okunamazsa_tek_secenek_varsayilmaz() -> None:
    """Boş listeden "demek ki tek tanesi var" sonucu çıkarmak, ürüne yanlış
    vergi kategorisi yazdırırdı."""
    service, api, _ = _service()
    api.fail.add("snapshot")

    fields = (await service.reference())["fields"]

    assert fields["taxCategoryId"]["state"] == "none"
    assert fields["taxCategoryId"]["auto"] is None
    assert fields["taxCategoryId"]["visible"] is False


async def test_tek_vergi_kategorisi_yeni_uruncte_kendiliginden_yazilir() -> None:
    service, _api, _ = _service()

    plan = await service.plan(payload={"sku": "YEN-1", "name": "Yeni Kitap"})

    assert plan["draft"]["taxCategoryId"] == 1
    assert "taxCategoryId" in plan["auto"]
    assert "KDV" in plan["notes"]["taxCategoryId"]


async def test_iki_vergi_kategorisinde_biri_sessizce_secilmez() -> None:
    service, api, _ = _service()
    api.snapshot_parts["tax_categories"] = [{"id": 1, "name": "KDV"},
                                            {"id": 2, "name": "KDV %20"}]

    plan = await service.plan(payload={"sku": "YEN-1", "name": "Yeni Kitap"})

    assert plan["draft"]["taxCategoryId"] == 0
    assert "taxCategoryId" not in plan["auto"]
    assert any("vergi kategorisi var" in item for item in plan["warnings"])


async def test_urun_acilirken_vergi_kategorisi_govdeye_konur() -> None:
    service, api, _ = _service()

    await service.create(payload={"sku": "YEN-1", "name": "Yeni Kitap"}, reason=GEREKCE,
                         actor="Kemal", dry_run=False)

    govde = api.used("update_product")[0]["payload"]
    assert govde["tax_category_id"] == 1
