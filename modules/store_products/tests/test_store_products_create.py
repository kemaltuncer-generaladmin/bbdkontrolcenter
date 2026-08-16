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

import base64
import struct
import zlib
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

#: CANLIDA ÖLÇÜLEN kitap nitelikleri (16.08.2026). `publisher` seçimli ve
#: değerini SEÇENEK KİMLİĞİYLE saklıyor; gerisi metin. Baskı yılının kodu
#: `print_year` — ekranın aday listesinin ilk sırası bu ölçüme dayanıyor.
KITAP_NITELIKLERI = [
    {"id": 31, "code": "isbn", "type": "text", "adminName": "ISBN"},
    {"id": 32, "code": "author", "type": "text", "adminName": "Yazar"},
    {"id": 33, "code": "publisher", "type": "select", "adminName": "Yayınevi"},
    {"id": 37, "code": "page_count", "type": "text", "adminName": "Sayfa Sayısı"},
    {"id": 38, "code": "print_year", "type": "text", "adminName": "Baskı Yılı"},
    {"id": 39, "code": "desi", "type": "text", "adminName": "Desi"},
]

#: Yayınevi seçenekleri yalnız nitelik DETAYINDA geliyor (liste `options: null`
#: döndürüyor — canlıda ölçüldü); sahte de öyle davranır.
YAYINEVI_DETAY = {"id": 33, "code": "publisher", "type": "select", "options": [
    {"id": 76, "adminName": "YILDIZLAR YARIŞIYOR YAYINLARI"},
    {"id": 10, "adminName": "Benim Başarı Dünyam"},
]}


def _png(width: int = 900, height: int = 900) -> str:
    """En küçük geçerli PNG — base64. Gerçek dosya OKUNMAZ."""
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    chunk = struct.pack(">I", len(header)) + b"IHDR" + header
    chunk += struct.pack(">I", zlib.crc32(b"IHDR" + header))
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + chunk).decode("ascii")


def _service(api: FakeApi | None = None, *, with_book: bool = False,
             **config: Any) -> tuple[ProductsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    api.tree_payload = AGAC
    api.families_payload = {"items": [{"id": 7, "code": "kitap", "name": "Kitap"}], "meta": {}}
    if with_book:
        api.attributes_payload = {"items": list(KITAP_NITELIKLERI), "meta": {}}
        api.attributes_by_id = {33: dict(YAYINEVI_DETAY)}
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
    assert [step["state"] for step in sonuc["steps"]] == [
        "dry_run", "planlandı", "planlandı", "planlandı", "planlandı"]
    assert [step["step"] for step in sonuc["steps"]] == [
        "create", "details", "book", "inventory", "images"]


async def test_urun_acildi_ama_ayrinti_yazilamadiysa_gizlenmez() -> None:
    # K7: ürün mağazada DURUYOR; hangi adımın düştüğü ve kimliği söylenir.
    service, api, _ = _service()
    api.fail.add("update_product")
    sonuc = await service.create(payload={"sku": "BBD-YARIM", "name": "Roman"},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["id"] == 1500
    assert "açıldı" in sonuc["error"]
    # create · details(düştü) · book(atlandı) · inventory · images(atlandı)
    assert [step["ok"] for step in sonuc["steps"]] == [True, False, True, True, True]
    assert [step["step"] for step in sonuc["steps"]][1] == "details"


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


# ═══════════════════════════════ kitap künyesi — ürün AÇARKEN
#
# Kullanıcının şikâyeti buydu: "ürün eklerken bir ürünün sahip olduğu tüm
# alanları ekleyemiyoruz — resim, ISBN, yazar, yayın, şu bu çoğu şey yok."
# Aşağıdaki testler künyenin ürün açılırken GERÇEKTEN yazıldığını ve
# yazılamayan alanın sessizce yutulmadığını kilitler.

async def test_kitap_kunyesi_urun_acilirken_yazilir() -> None:
    service, api, _ = _service(with_book=True)
    sonuc = await service.create(
        payload={"sku": "BBD-KUNYE", "name": "Roman",
                 "book": {"isbn": "9786051234567", "author": "Komisyon",
                          "pageCount": "176", "publishYear": "2025", "publisher": "76"}},
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is True
    kunye = next(step for step in sonuc["steps"] if step["step"] == "book")
    assert kunye["ok"] is True and kunye["state"] == "ok"

    # İKİNCİ `update_product` künyenin kendisidir: birincisi ayrıntılar.
    govde = api.used("update_product")[1]["payload"]
    assert govde["isbn"] == "9786051234567"
    assert govde["author"] == "Komisyon"
    assert govde["page_count"] == "176"
    assert govde["print_year"] == "2025"
    # SEÇİMLİ ALAN SAYIYA ÇEVRİLİR: mağaza seçenek kimliğini tamsayı bekliyor.
    assert govde["publisher"] == 76


async def test_kunye_yazma_yolu_duzenlemeyle_ayni_kalir() -> None:
    """Ayrı bir yazma yolu İCAT EDİLMEDİ: `save` çağrılıyor.

    Kanıt, denetim izindeki eylem adı: künye adımı `create_book` gibi yeni bir
    ad değil, düzenleme ekranının kullandığı `update_product` satırını yazıyor.
    İkinci bir yol açılsaydı doğrulama, TUZAK 1 koruması ve denetim satırı
    orada yeniden yazılmak zorunda kalırdı.
    """
    service, _, store = _service(with_book=True)
    await service.create(payload={"sku": "BBD-IZ2", "name": "Roman",
                                  "book": {"isbn": "9786051234567"}},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert "update_product" in [row["action"] for row in store.audit]


async def test_kunye_adimi_kaydi_taze_okuyup_yazar() -> None:
    """TUZAK 1 künye adımında da geçerli.

    Künye, ayrıntı yazmasından SONRA gidiyor ve o adım kaydı zaten değiştirdi.
    Taze okumadan yazmak, az önce yazılan adı ve kategorileri geri alabilirdi;
    bu yüzden sıra oku-yaz-oku-yaz olmalı.
    """
    service, api, _ = _service(with_book=True)
    await service.create(payload={"sku": "BBD-TUZAK", "name": "Roman",
                                  "book": {"pageCount": "176", "isbn": "9786051234567"}},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    sira = [name for name, _, _ in api.calls
            if name in ("create_product", "product", "update_product")]
    assert sira == ["create_product", "product", "update_product",
                    "product", "update_product"]


async def test_bos_kunye_alani_magazaya_gonderilmez() -> None:
    """Ürün AÇARKEN temizlenecek bir şey yok: boş alanı yamaya koymak, mağazaya
    hiçbir şeyi değiştirmeyen bir yazma yaptırırdı. (Düzenlemede boş yazmak
    "bu bilgiyi sil" demektir ve orada meşrudur — ayrımı `_draft` koyuyor.)"""
    service, api, _ = _service(with_book=True)
    await service.create(payload={"sku": "BBD-BOSALAN", "name": "Roman",
                                  "book": {"isbn": "9786051234567", "author": "",
                                           "publishYear": "   "}},
                         reason=GEREKCE, actor="Ayşe", dry_run=False)
    govde = api.used("update_product")[1]["payload"]
    assert govde["isbn"] == "9786051234567"
    # Boş gelen alanlar yamaya HİÇ girmedi: gövdede yalnız mağazadaki mevcut
    # değerleriyle (yani hiç) durabilirler.
    assert govde.get("author", "") == ""
    assert govde.get("print_year", "") == ""


async def test_cozulemeyen_nitelige_yazilmaz_ve_nedeni_doner() -> None:
    """Olmayan bir koda yazmak sessiz kayıptır: Bagisto isteği 200 ile kabul
    eder, personel "kaydettim" der, değer hiçbir yere yazılmamıştır.

    ÜRÜN DE AÇILMAZ: geri alınamayan bir kayıt açıp ardından "bu alan yok"
    demek, kullanıcıyı yarım bir ürünle bırakırdı.
    """
    service, api, _ = _service()          # nitelik listesi boş: hiçbir kod çözülmez
    sonuc = await service.create(payload={"sku": "BBD-YOK", "name": "Roman",
                                          "book": {"isbn": "9786051234567"}},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["field"] == "isbn"
    assert "nitelik yok" in sonuc["error"]
    assert api.used("create_product") == []          # ürün HİÇ açılmadı


async def test_secenegi_okunamayan_secimli_alan_yazilmaya_calisilmaz() -> None:
    """Seçenek kimliği bilinmeden yazılan değer mağazada hiçbir yere oturmaz.

    Nitelik VAR ve kodu çözüldü; okunamayan şey seçenek listesi. Alanı yine de
    açıp serbest metin yazdırmak, "yayınevi" yazıp kaydeden personelin ürününü
    yayınevsiz bırakırdı.
    """
    service, api, _ = _service(with_book=True)
    api.attributes_by_id = {}             # detay okunamıyor → seçenek yok
    sonuc = await service.create(payload={"sku": "BBD-SECENEK", "name": "Roman",
                                          "book": {"publisher": "76"}},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert sonuc["ok"] is False
    assert sonuc["field"] == "publisher"
    assert "seçenek" in sonuc["error"]
    assert api.used("create_product") == []


# ═══════════════════════════════════════ görseller — zincirin SON adımı

async def test_gorsel_urun_kimligi_olustuktan_sonra_yuklenir() -> None:
    """TASARIM KISITI: yükleme ucu ürün kimliği istiyor
    (`POST /catalog/products/{id}/images`) ve kimlik ancak ürün doğunca
    oluşuyor. Görsel bu yüzden zincirin beşinci adımıdır, birincisi değil.
    """
    service, api, _ = _service()
    sonuc = await service.create(
        payload={"sku": "BBD-GORSEL", "name": "Roman"},
        images=[{"filename": "kapak.png", "mime": "image/png", "content": _png()},
                {"filename": "arka.png", "mime": "image/png", "content": _png()}],
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is True
    sira = [name for name, _, _ in api.calls]
    assert sira.index("create_product") < sira.index("upload_product_image")
    # Görseller AÇILAN ürünün kimliğine gitti, hayalî bir kimliğe değil.
    assert {row["productId"] for row in api.uploaded_images} == {sonuc["id"]}
    # SIRA KORUNUR: listenin ilk dosyası kapaktır (`position` 1'den başlar).
    assert [row["file"] for row in api.uploaded_images] == ["kapak.png", "arka.png"]
    assert [row["position"] for row in api.uploaded_images] == [1, 2]

    adim = next(step for step in sonuc["steps"] if step["step"] == "images")
    assert adim["state"] == "ok" and len(adim["uploaded"]) == 2


async def test_kuru_provada_gorsel_yuklenmez_urun_yaratilmaz() -> None:
    service, api, _ = _service()
    sonuc = await service.create(
        payload={"sku": "BBD-PROVA2", "name": "Roman"},
        images=[{"filename": "kapak.png", "mime": "image/png", "content": _png()}],
        reason=GEREKCE, actor="Ayşe", dry_run=True)

    assert sonuc["dryRun"] is True
    assert api.used("upload_product_image") == []
    assert api.uploaded_images == []
    adim = next(step for step in sonuc["steps"] if step["step"] == "images")
    assert adim["state"] == "planlandı"
    assert adim["planned"] == 1            # kaç dosyayı kapsadığı söylenir


async def test_bir_gorsel_patlarsa_digerleri_yuklenir_urun_ayakta_kalir() -> None:
    """K7 DOSYA BAŞINADIR. Üçüncü dosya bozuksa dördüncüsü yine gider ve ürün
    yerinde kalır; durmak kullanıcıyı ürünü silip baştan açmaya iterdi."""
    service, api, _ = _service()
    api.failing_images = {"bozuk.png"}
    sonuc = await service.create(
        payload={"sku": "BBD-KISMI", "name": "Roman"},
        images=[{"filename": "kapak.png", "mime": "image/png", "content": _png()},
                {"filename": "bozuk.png", "mime": "image/png", "content": _png()},
                {"filename": "arka.png", "mime": "image/png", "content": _png()}],
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    # ÜRÜN AÇILDI ve kimliği döndü — yarım kalmadı.
    assert sonuc["id"] == 1500
    assert sonuc["ok"] is False                       # eksik sessizce yutulmaz
    assert [row["file"] for row in api.uploaded_images] == ["kapak.png", "arka.png"]

    adim = next(step for step in sonuc["steps"] if step["step"] == "images")
    assert adim["state"] == "kısmi"
    # DÜŞEN DOSYA ADIYLA DÖNER: "2 görsel yüklenemedi" hangisini küçülteceğini
    # söylemiyordu.
    assert [row["file"] for row in adim["failed"]] == ["bozuk.png"]
    assert "bozuk.png" in sonuc["error"]


async def test_reddedilen_dosya_magazaya_hic_gonderilmez() -> None:
    """Tür/boyut reddi istek kurulmadan verilir: hız kovasından pay bile
    harcanmaz ve hata anlaşılır çıkar."""
    service, api, _ = _service()
    sonuc = await service.create(
        payload={"sku": "BBD-PDF", "name": "Roman"},
        images=[{"filename": "katalog.jpg", "mime": "image/jpeg",
                 "content": base64.b64encode(b"%PDF-1.7 " + b"0" * 80).decode("ascii")}],
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["id"] == 1500                        # ürün yine açıldı
    assert api.used("upload_product_image") == []     # istek HİÇ kurulmadı
    adim = next(step for step in sonuc["steps"] if step["step"] == "images")
    assert "PDF" in adim["failed"][0]["error"]


# ═══════════════════════════════════════════════════════════ gerileme

async def test_gorselsiz_kunyesiz_ekleme_eskisi_gibi_calisir() -> None:
    """Yeni alanlar ESKİ AKIŞA dokunmamalı: künye ve görsel boşken ürün açma
    tam olarak eskisi gibi çalışır ve fazladan tek istek bile atmaz."""
    service, api, _ = _service()
    sonuc = await service.create(payload={"sku": "BBD-SADE", "name": "Şeker Portakalı",
                                          "categoryIds": [3], "price": 12550},
                                 reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is True and sonuc["id"] == 1500
    assert api.used("upload_product_image") == []
    # TEK `update_product` gider: künye boşken ikinci tur atılmaz.
    assert len(api.used("update_product")) == 1
    assert api.used("update_product")[0]["payload"]["categories"] == [2, 3]
    assert api.used("update_inventory")[0]["quantities"] == {"1": 0}

    durumlar = {step["step"]: step["state"] for step in sonuc["steps"]}
    assert durumlar == {"create": "ok", "details": "ok", "book": "atlandı",
                        "inventory": "ok", "images": "atlandı"}


# ══════════════════════════════════════════ öznitelik AİLESİ — künyeyi taşıyan
#
# CANLIDA ÖLÇÜLDÜ (16.08.2026). Mağazada iki aile var:
#
#   id 1  `default` / "Varsayılan"  → 28 nitelik; kitap alanlarından yalnız
#                                     `desi`. İçinde 2 kalem var, ikisi de
#                                     kargoya girmeyen (`NON_SHIPPING_SKUS`).
#   id 2  `kitap`   / "Kitap"       → 36 nitelik; dokuz künye alanının hepsi.
#                                     Katalogdaki 1.420 gerçek kitap burada.
#
# ADA BAKAN eski kural `default` olanı seçiyordu ve sonucu SESSİZDİ: mağaza
# gövdeyi ailenin nitelik listesiyle kesiştirip fazlasını hata üretmeden
# düşürüyor (`AdminCatalogProductUpdateProcessor::resolveAttributeCodes`).
# Ürün "açıldı" diye görünüyor, ISBN/yazar/yayınevi/sayfa sayısı hiçbir yere
# yazılmıyordu. Sayfa sayısı gidince kargo hesabı da varsayılan 1,0 desiye
# çıkıyordu — 176 sayfalık bir kitabın gerçeği 0,18 — ve her siparişte
# müşteriden fazla kargo alınıyordu. Geri dönüşü de yok: aile ürün açıldıktan
# sonra gönderilmiyor (TUZAK 3).

#: Canlı aile listesi — `kitap` ÖNDE, `default` arkada (mağazanın verdiği sıra).
AILELER = {"items": [{"id": 2, "code": "kitap", "name": "Kitap"},
                     {"id": 1, "code": "default", "name": "Varsayılan"}], "meta": {}}

#: Aile detayları. Kodlar canlıdaki gruplardan alındı.
AILE_DETAY = {
    1: {"id": 1, "code": "default", "name": "Varsayılan", "attributeGroups": [
        {"id": 1, "code": "general", "name": "Genel", "attributes": [
            {"id": 1, "code": "sku", "type": "text"},
            {"id": 2, "code": "name", "type": "text"},
            {"id": 3, "code": "url_key", "type": "text"},
            {"id": 8, "code": "status", "type": "boolean"}]},
        {"id": 5, "code": "shipping", "name": "Nakliye", "attributes": [
            {"id": 39, "code": "desi", "type": "text"}]}]},
    2: {"id": 2, "code": "kitap", "name": "Kitap", "attributeGroups": [
        {"id": 9, "code": "general", "name": "Genel", "attributes": [
            {"id": 1, "code": "sku", "type": "text"},
            {"id": 2, "code": "name", "type": "text"},
            {"id": 3, "code": "url_key", "type": "text"},
            {"id": 8, "code": "status", "type": "boolean"}]},
        {"id": 11, "code": "book_details", "name": "Kitap Bilgileri", "attributes": [
            {"id": 31, "code": "isbn", "type": "text"},
            {"id": 32, "code": "author", "type": "text"},
            {"id": 33, "code": "publisher", "type": "select"},
            {"id": 37, "code": "page_count", "type": "text"},
            {"id": 38, "code": "print_year", "type": "text"}]},
        {"id": 14, "code": "shipping", "name": "Kargo", "attributes": [
            {"id": 39, "code": "desi", "type": "text"}]}]},
}


def _iki_aile() -> tuple[Any, FakeApi, Any]:
    """Canlıdaki iki aileli dünya, kitap nitelikleri açık."""
    service, api, store = _service(with_book=True)
    api.families_payload = dict(AILELER)
    api.families_by_id = {key: dict(value) for key, value in AILE_DETAY.items()}
    return service, api, store


async def test_yeni_urun_kunyeyi_tasiyan_aileye_acilir_adi_default_olana_degil() -> None:
    service, api, _ = _iki_aile()

    await service.create(payload={"sku": "BBD-YENI-KITAP", "name": "Roman"},
                         reason=GEREKCE, actor="Ayşe", dry_run=True)

    # Adı "Varsayılan" olan aile 1 DEĞİL, künyeyi taşıyan aile 2 seçilir.
    assert api.used("create_product")[0]["payload"]["attribute_family_id"] == 2


async def test_aile_bir_kez_cozulur_her_urunde_yeniden_sorulmaz() -> None:
    service, api, _ = _iki_aile()
    await service.create(payload={"sku": "BBD-1", "name": "Roman"}, reason=GEREKCE,
                         actor="Ayşe", dry_run=True)
    await service.create(payload={"sku": "BBD-2", "name": "Şiir"}, reason=GEREKCE,
                         actor="Ayşe", dry_run=True)

    assert len(api.used("families")) == 1
    assert len(api.used("family")) == 2      # iki aile, birer kez


async def test_ayardaki_aile_kimligi_olcumu_ezer() -> None:
    """`default_family_id` kurulumun kesin sözüdür; şema tahminine sorulmaz."""
    service, api, _ = _service(with_book=True, default_family_id=1)
    api.families_payload = dict(AILELER)
    api.families_by_id = {key: dict(value) for key, value in AILE_DETAY.items()}

    await service.create(payload={"sku": "BBD-AYAR", "name": "Roman"}, reason=GEREKCE,
                         actor="Ayşe", dry_run=True)

    assert api.used("create_product")[0]["payload"]["attribute_family_id"] == 1
    assert api.used("families") == []        # ayar varsa liste hiç sorulmaz


async def test_aile_semasi_okunamazsa_eski_ada_bakan_kural_surer() -> None:
    """K7 + gerileme: şema okunamadığında akış durmaz, eski davranışa döner."""
    service, api, _ = _iki_aile()
    api.fail.add("family")

    await service.create(payload={"sku": "BBD-KOPUK", "name": "Roman"}, reason=GEREKCE,
                         actor="Ayşe", dry_run=True)

    assert api.used("create_product")[0]["payload"]["attribute_family_id"] == 1


async def test_kunye_hedef_ailede_yoksa_urun_HIC_ACILMAZ() -> None:
    """Sessiz kaybın kapısı: ürün açılıp künye düşerse geri dönüşü yok.

    İstek elle kurulabilir (K9) ve `attributeFamilyId` açıkça verilebilir;
    kapı bu yüzden backend'de durur.
    """
    service, api, _ = _iki_aile()

    sonuc = await service.create(
        payload={"sku": "BBD-YANLIS-AILE", "name": "Roman", "attributeFamilyId": 1,
                 "book": {"isbn": "9786051234567", "pageCount": "176"}},
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is False
    assert "AİLESİNDE yok" in sonuc["error"]
    assert "isbn" in sonuc["fieldErrors"]
    # ÜRÜN AÇILMADI: geri alınamayan bir kayıt yaratılmadı.
    assert api.used("create_product") == []


async def test_kunyeli_urun_dogru_ailede_sorunsuz_acilir() -> None:
    """Kapı kapatmıyor, YANLIŞI kapatıyor: doğru ailede künye yazılır."""
    service, api, _ = _iki_aile()

    sonuc = await service.create(
        payload={"sku": "BBD-DOGRU-AILE", "name": "Roman",
                 "book": {"isbn": "9786051234567", "pageCount": "176"}},
        reason=GEREKCE, actor="Ayşe", dry_run=False)

    assert sonuc["ok"] is True
    kunye = next(step for step in sonuc["steps"] if step["step"] == "book")
    assert kunye["state"] == "ok"
    govde = api.used("update_product")[-1]["payload"]
    assert govde["isbn"] == "9786051234567"
    assert govde["page_count"] == "176"


async def test_acma_formu_alanlari_hedef_aileye_gore_gelir() -> None:
    """Panel `bookFieldsOnCreate` çizer: yazılacak yeri olmayan alan gösterilmez.

    `bookFields` (katalog kapsamlı) toplu yazma ekranı için OLDUĞU GİBİ kalır;
    iki liste iki ayrı soruya cevap verir.
    """
    service, api, _ = _iki_aile()
    api.families_payload = {"items": [{"id": 1, "code": "default", "name": "Varsayılan"}],
                            "meta": {}}

    referans = await service.reference()
    acma = {item["key"]: item for item in referans["bookFieldsOnCreate"]}
    katalog = {item["key"]: item for item in referans["bookFields"]}

    assert acma["isbn"]["available"] is False and "AİLESİNDE yok" in acma["isbn"]["reason"]
    assert acma["desi"]["available"] is True
    assert katalog["isbn"]["available"] is True
