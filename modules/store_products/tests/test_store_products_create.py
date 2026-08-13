"""Ürün açma OTOMASYONU — saf mantık ve akış. AĞA ÇIKMAZ, CANLIYA YAZMAZ.

Buradaki her test sahte geçitle (`FakeApi`) koşar; canlı mağazaya (1.419 ürün)
tek bir istek bile gitmez. Otomatiğe bağlanan altı iş:

  1. url_key ürün adından TÜRKÇE harfler katlanarak türetilir,
  2. çakışan url_key yazmadan ÖNCE numaralandırılır (TUZAK 6),
  3. seçilen kategorinin ÜST kategorileri ağaca göre eklenir,
  4. öznitelik ailesi kendiliğinden çözülür (mevcut davranış korunur),
  5. boş SEO alanları ad ve kısa açıklamadan (HTML düzleştirilerek) türetilir,
  6. stok girilmediyse depoya 0 yazılır — ürün "stokta yok" doğar.

Yedinci kural testlerin çoğunda tekrar eder: OTOMATİK DOLDURULAN HER ALAN
`auto` listesinde döner. Sessizce doldurulan alan panelde işaretlenemez.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_products_backend import catalog
from store_products_backend.service import ProductsService
from store_products_fakes import FakeApi, FakeLog, FakeStore

#: Kök · Kitap · (Roman, Şiir) — canlıdaki gibi kök kategori tepede durur.
AGAC = {"items": [{"id": 1, "name": "Root", "children": [
    {"id": 2, "name": "Kitap", "children": [
        {"id": 3, "name": "Roman"},
        {"id": 4, "name": "Şiir"},
    ]},
    {"id": 9, "name": "Kırtasiye"},
]}]}

GEREKCE = "Yeni kitap kaydı açılıyor"


def _service(api: FakeApi | None = None,
             **config: Any) -> tuple[ProductsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    api.tree_payload = AGAC
    api.families_payload = {"items": [{"id": 7, "code": "kitap", "name": "Kitap"}], "meta": {}}
    store = FakeStore()
    service = ProductsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


# ==================================================== 1 · TÜRKÇE harf katlama

def test_urun_adindan_url_anahtari_turkce_harfleri_katlar() -> None:
    # `unicodedata` ile normalleştirme `ı`yı boşa çıkarıyor ve "ısı" → "s"
    # gibi anlamsız anahtar üretiyor; harfler ÖNCE elle eşlenir.
    assert catalog.slugify("Şeker Portakalı") == "seker-portakali"
    assert catalog.slugify("Çalıkuşu") == "calikusu"
    assert catalog.slugify("Güneşi Uyandıralım") == "gunesi-uyandiralim"


def test_slug_noktalama_ve_bosluklari_tek_tireye_indirir() -> None:
    assert catalog.slugify("  Ağrı Dağı Efsanesi!!  ") == "agri-dagi-efsanesi"
    assert catalog.slugify("9. Sınıf — Matematik") == "9-sinif-matematik"


# ====================================================== 2 · çakışma artırımı

def test_cakisan_url_anahtari_iki_ile_baslar() -> None:
    # `-1` ilk kaydın kendisiymiş gibi durur ve iki ürünü ikizleştirirdi.
    assert catalog.next_url_key("Roman", ["roman"]) == "roman-2"
    assert catalog.next_url_key("Roman", ["roman", "roman-2"]) == "roman-3"


def test_bos_olan_ilk_numara_secilir_bosluk_atlanmaz() -> None:
    assert catalog.next_url_key("roman", ["roman", "roman-3"]) == "roman-2"


def test_cakisma_yoksa_anahtar_oldugu_gibi_kalir() -> None:
    assert catalog.next_url_key("Şiir", ["roman"]) == "siir"


def test_cok_uzun_ad_numara_eki_icin_kisaltilir() -> None:
    uzun = "a" * (catalog.URL_KEY_LIMIT + 40)
    assert catalog.next_url_key(uzun, []) == "a" * catalog.URL_KEY_LIMIT
    ikinci = catalog.next_url_key(uzun, ["a" * catalog.URL_KEY_LIMIT])
    assert len(ikinci) <= catalog.URL_KEY_LIMIT
    assert ikinci.endswith("-2")


def test_numaralar_tukenirse_uydurulmaz() -> None:
    # Rastgele ek üretmek, kullanıcının bir daha bulamayacağı bir URL yaratırdı.
    dolu = ["roman", *[f"roman-{index}" for index in range(2, 12)]]
    assert catalog.next_url_key("roman", dolu, limit=10) == ""


# ======================================================= 3 · üst kategoriler

def test_yaprak_secilince_ust_kategoriler_de_baglanir() -> None:
    index = catalog.category_index(AGAC)
    sonuc = catalog.expand_categories(index, [3])
    assert sonuc["ids"] == [2, 3]                 # Kitap → Roman
    assert sonuc["added"] == [2]
    assert [row["auto"] for row in sonuc["trail"]] == [True, False]


def test_kok_kategori_urune_baglanmaz() -> None:
    # Kök gerçek bir raf değil, ağacın tutamağıdır; yönetim ekranı da atamaz.
    index = catalog.category_index(AGAC)
    assert 1 not in catalog.expand_categories(index, [3])["ids"]


def test_kullanicinin_sectigi_ust_kategori_otomatik_sayilmaz() -> None:
    index = catalog.category_index(AGAC)
    sonuc = catalog.expand_categories(index, [2, 3])
    assert sonuc["ids"] == [2, 3]
    assert sonuc["added"] == []                   # ikisi de elle seçildi


def test_iki_dal_secilirse_her_ikisinin_ustu_toplanir() -> None:
    index = catalog.category_index(AGAC)
    sonuc = catalog.expand_categories(index, [4, 9])
    assert sonuc["ids"] == [2, 9, 4]              # önce üst düzey, sonra yaprak
    assert sonuc["added"] == [2]


def test_agacta_olmayan_kategori_dusurulmez_bildirilir() -> None:
    # Ağaç okunamadıysa kullanıcının seçimini silmek veri kaybı olurdu.
    sonuc = catalog.expand_categories({}, [3, 9])
    assert sonuc["ids"] == [3, 9]
    assert sonuc["unknown"] == [3, 9]
    assert sonuc["added"] == []


def test_bozuk_agac_donguye_girmez() -> None:
    dongu = {"items": [{"id": 5, "name": "A", "children": [{"id": 5, "name": "A tekrar"}]}]}
    index = catalog.category_index(dongu)
    assert catalog.category_trail(index, 5) == [5]


# ============================================================ 5 · SEO türetme

def test_bos_seo_alanlari_ad_ve_kisa_aciklamadan_turetilir() -> None:
    sonuc = catalog.seo_defaults(name="Şeker Portakalı",
                                 short_description="<p>Zezé'nin <strong>hikâyesi</strong>.</p>")
    assert sonuc["metaTitle"] == "Şeker Portakalı"
    assert sonuc["metaDescription"] == "Zezé'nin hikâyesi."
    assert sonuc["auto"] == ["metaTitle", "metaDescription"]


def test_kullanicinin_yazdigi_meta_ezilmez() -> None:
    sonuc = catalog.seo_defaults(name="Çalıkuşu", meta_title="Çalıkuşu — Reşat Nuri",
                                 meta_description="Elle yazılmış açıklama")
    assert sonuc["metaTitle"] == "Çalıkuşu — Reşat Nuri"
    assert sonuc["auto"] == []


def test_uzun_aciklama_sozcuk_sinirinda_kirpilir() -> None:
    metin = "kelime " * 60
    sonuc = catalog.seo_defaults(name="Ad", short_description=metin)
    assert len(sonuc["metaDescription"]) <= catalog.META_DESCRIPTION_LIMIT
    assert sonuc["metaDescription"].endswith("…")
    assert "kelim…" not in sonuc["metaDescription"]      # sözcük ortasından kesilmez


def test_html_duz_metne_indirilir_etiket_meta_alanina_sizmaz() -> None:
    ham = "<h2>Roman</h2><p>Bir&nbsp;kitap</p><script>alert('x')</script><p>ikinci</p>"
    assert catalog.plain_text(ham) == "Roman Bir kitap ikinci"


def test_kacirilmis_etiket_metni_etiket_sayilmaz() -> None:
    # Varlık çözümü etiketler atıldıktan SONRA yapılır; önce yapılsaydı
    # `&lt;script&gt;` gerçek etikete dönüşüp silinirdi.
    assert catalog.plain_text("&lt;b&gt;kalın&lt;/b&gt;") == "<b>kalın</b>"


def test_kisa_aciklama_bossa_uzun_aciklamaya_dusulur() -> None:
    sonuc = catalog.seo_defaults(name="Ad", short_description="<p><br></p>",
                                 description="<p>Uzun açıklama</p>")
    assert sonuc["metaDescription"] == "Uzun açıklama"


# ================================================== taslak (plan) — uçtan uca

async def test_taslak_hicbir_sey_yazmaz() -> None:
    service, api, store = _service()
    await service.plan(payload={"name": "Şeker Portakalı", "categoryIds": [3]})
    yazanlar = {"create_product", "update_product", "update_inventory", "update_product_status"}
    assert not [name for name, _, _ in api.calls if name in yazanlar]
    assert store.audit == []


async def test_taslak_url_anahtarini_addan_turetir_ve_isaretler() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-1", "name": "Güneşi Uyandıralım"})
    assert sonuc["draft"]["urlKey"] == "gunesi-uyandiralim"
    assert "urlKey" in sonuc["auto"]                       # panelde işaretlenecek
    assert sonuc["notes"]["urlKey"]


async def test_taslak_cakisan_anahtari_yazmadan_once_numaralandirir() -> None:
    service, api, _ = _service()
    api.taken_url_keys = {"calikusu", "calikusu-2"}
    sonuc = await service.plan(payload={"sku": "BBD-2", "name": "Çalıkuşu"})
    assert sonuc["draft"]["urlKey"] == "calikusu-3"
    assert sonuc["urlKeyCheck"]["changed"] is True
    assert "calikusu" in sonuc["notes"]["urlKey"]


async def test_kullanicinin_yazdigi_url_anahtari_serbestse_dokunulmaz() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-3", "name": "Çalıkuşu",
                                        "urlKey": "calikusu-ozel-baski"})
    assert sonuc["draft"]["urlKey"] == "calikusu-ozel-baski"
    assert "urlKey" not in sonuc["auto"]                   # elle yazıldı, otomatik değil


async def test_magaza_url_suzgecini_uygulamazsa_serbest_denmez() -> None:
    # Laravel tanımadığı sorgu parametresini SESSİZCE yok sayar; süzülmemiş
    # listeyi "çakışma yok" saymak 422'yi kaydet düğmesine bırakmaktı.
    service, api, _ = _service()
    api.taken_url_keys = {"baska-urun"}
    api.url_key_filter_honored = False
    sonuc = await service.plan(payload={"sku": "BBD-4", "name": "Çalıkuşu"})
    assert sonuc["urlKeyCheck"]["state"] == "unknown"
    assert any("doğrulanamadı" in item for item in sonuc["warnings"])


async def test_magaza_dusunce_taslak_yine_doner() -> None:
    service, api, _ = _service()
    api.fail.update({"products", "category_tree", "inventory_sources", "families"})
    sonuc = await service.plan(payload={"sku": "BBD-5", "name": "Çalıkuşu", "categoryIds": [3]})
    assert sonuc["ok"] is True                             # K7: ekran ayakta
    assert sonuc["connected"] is False
    assert sonuc["draft"]["urlKey"] == "calikusu"          # doğrulanamadı ama önerildi
    assert sonuc["draft"]["categoryIds"] == [3]            # seçim düşürülmedi


async def test_taslak_ust_kategoriyi_agactan_okuyup_ekler() -> None:
    service, api, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-6", "name": "Roman", "categoryIds": [3]})
    assert sonuc["draft"]["categoryIds"] == [2, 3]
    assert "categoryIds" in sonuc["auto"]
    assert [row["name"] for row in sonuc["categoryTrail"] if row["auto"]] == ["Kitap"]
    assert api.used("category_tree")                       # ağaç VARSAYILMADI, okundu


async def test_taslak_stok_girilmediyse_sifir_yazacagini_soyler() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-7", "name": "Roman"})
    assert sonuc["draft"]["stock"] == 0
    assert sonuc["draft"]["sourceId"] == 1
    assert "stock" in sonuc["auto"]
    assert "stokta yok" in sonuc["notes"]["stock"]


async def test_taslak_girilen_stogu_ezmez() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-8", "name": "Roman", "stock": 12})
    assert sonuc["draft"]["stock"] == 12
    assert "stock" not in sonuc["auto"]


async def test_taslak_fiyat_uydurmaz_ama_sessiz_de_kalmaz() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-9", "name": "Roman"})
    assert sonuc["draft"]["price"] is None                 # 0 yazmak 0 TL demekti
    assert any("Fiyat girilmedi" in item for item in sonuc["warnings"])


async def test_yeni_urun_pasif_dogar_ve_bu_soylenir() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-10", "name": "Roman"})
    assert sonuc["draft"]["status"] is False
    assert "status" in sonuc["auto"]


async def test_aile_ekranda_sorulmaz_kendiliginden_cozulur() -> None:
    service, _, _ = _service()
    sonuc = await service.plan(payload={"sku": "BBD-11", "name": "Roman"})
    assert sonuc["draft"]["attributeFamilyId"] == 7
    assert "attributeFamilyId" in sonuc["auto"]


# ================================================== ürün açma — uçtan uca akış

async def test_urun_acilinca_ayrintilar_ve_stok_da_yazilir() -> None:
    service, api, _ = _service()
    sonuc = await service.create(
        payload={"sku": "BBD-YENI", "name": "Şeker Portakalı", "categoryIds": [3],
                 "price": 12550, "shortDescription": "<p>Zezé'nin hikâyesi.</p>"},
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is True
    assert sonuc["id"] == 1500

    gonderilen = api.used("update_product")[0]["payload"]
    assert gonderilen["url_key"] == "seker-portakali"
    assert gonderilen["name"] == "Şeker Portakalı"
    assert gonderilen["meta_title"] == "Şeker Portakalı"
    assert gonderilen["meta_description"] == "Zezé'nin hikâyesi."
    assert gonderilen["price"] == "125.50"                 # kuruş → ondalık (TUZAK 4)
    assert gonderilen["categories"] == [2, 3]              # üst kategori de bağlandı
    assert gonderilen["status"] == 0                       # pasif doğdu
    # TUZAK 2: kanal ve dil HER yazma isteğinin gövdesinde gider.
    assert gonderilen["channel"] == "default"
    assert gonderilen["locale"] == "tr"


async def test_ayrintilar_taze_kayit_uzerine_yazilir() -> None:
    # TUZAK 1: kısmi PUT gövdede olmayan alanları NULL'layabiliyor; gövde
    # mağazadan TAZE okunan kaydın üstüne kurulur.
    service, api, _ = _service()
    await service.create(payload={"sku": "BBD-TAZE", "name": "Roman"},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    sira = [name for name, _, _ in api.calls]
    assert sira.index("create_product") < sira.index("product") < sira.index("update_product")


async def test_stok_girilmediyse_depoya_sifir_yazilir() -> None:
    service, api, _ = _service()
    await service.create(payload={"sku": "BBD-STOK", "name": "Roman"},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert api.used("update_inventory")[0]["quantities"] == {"1": 0}


async def test_kuru_provada_tek_istek_gider_hayali_kimlige_yazilmaz() -> None:
    service, api, _ = _service()
    sonuc = await service.create(payload={"sku": "BBD-PROVA", "name": "Roman"},
                                 reason=GEREKCE, actor="Ayşe", dry_run=True)
    assert sonuc["ok"] is True
    assert sonuc["dryRun"] is True
    assert api.used("update_product") == []
    assert api.used("update_inventory") == []
    assert [step["state"] for step in sonuc["steps"]] == ["dry_run", "planlandı", "planlandı"]


async def test_urun_acildi_ama_ayrinti_yazilamadiysa_gizlenmez() -> None:
    # K7: ürün mağazada DURUYOR; hangi adımın düştüğü ve kimliği söylenir.
    service, api, _ = _service()
    api.fail.add("update_product")
    sonuc = await service.create(payload={"sku": "BBD-YARIM", "name": "Roman"},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["id"] == 1500
    assert "açıldı" in sonuc["error"]
    assert [step["ok"] for step in sonuc["steps"]] == [True, False, True]


async def test_yazmadan_hemen_once_kapilan_anahtar_yeniden_numaralandirilir() -> None:
    # Onayla yazma arasında geçen sürede başka bir ekran aynı anahtarı almış
    # olabilir; panelin gösterdiği değere körü körüne güvenilmez (K9).
    service, api, _ = _service()
    api.taken_url_keys = {"roman"}
    sonuc = await service.create(payload={"sku": "BBD-KAPILDI", "name": "Roman",
                                          "urlKey": "roman"},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert api.used("update_product")[0]["payload"]["url_key"] == "roman-2"
    assert "urlKey" in sonuc["auto"]


async def test_kullanicinin_cikardigi_ust_kategori_yazarken_geri_konmaz() -> None:
    # Panel taslakta genişletilmiş listeyi gösterdi, kullanıcı `Kitap`ı çıkardı.
    # Yazarken yeniden genişletmek, gördüğü listeden başkasını yazmak olurdu.
    service, api, _ = _service()
    await service.create(payload={"sku": "BBD-ELLE", "name": "Roman", "categoryIds": [3],
                                  "expandParents": False},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert api.used("update_product")[0]["payload"]["categories"] == [3]


async def test_url_yoklamasi_bos_bir_alternatif_de_onerir() -> None:
    # Düzenleyicideki yoklama da çakışmada boşta olan anahtarı söyler; aynı
    # yanıttan hesaplanır, EK İSTEK atılmaz.
    service, api, _ = _service()
    api.list_payload = {"items": [{"id": 9, "sku": "X", "url_key": "kalem"}], "meta": {}}
    sonuc = await service.check_url_key(url_key="Kalem")
    assert sonuc["state"] == "taken"
    assert sonuc["free"] == "kalem-2"
    assert len(api.used("products")) == 1


async def test_gerekce_kisa_ise_hicbir_istek_gitmez() -> None:
    service, api, _ = _service()
    sonuc = await service.create(payload={"sku": "BBD-X", "name": "Roman"}, reason="kısa",
                                 actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert api.calls == []


async def test_acilan_urun_ve_adimlari_yerel_ize_yazilir() -> None:
    service, _, store = _service()
    await service.create(payload={"sku": "BBD-IZ", "name": "Roman"},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    eylemler = [row["action"] for row in store.audit]
    assert "create_product" in eylemler
    assert "create_details" in eylemler
    assert "create_stock" in eylemler
    assert all(row["reason"] == GEREKCE for row in store.audit)
