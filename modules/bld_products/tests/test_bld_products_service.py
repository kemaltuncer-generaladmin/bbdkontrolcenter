"""İş kurallarının testi. AĞA ÇIKMAZ.

Üç iddia grubu var ve üçü de bu ekranın hata sınıflarına karşılık geliyor:

  1. K7 — geçit düşerse okuma UÇLARI HATA FIRLATMAZ, `connected: False` döner.
  2. Yazma zinciri — gerekçe kapısı, taze okuma, `denendi` izi, açık `dry_run`.
  3. Sözleşmenin sert kenarları — paket ürününün fiyatı, tanınmayan alan,
     kategori döngüsü, yıkıcı işlemin ayrı izni.
"""

from __future__ import annotations

import pytest
from bld_products_fakes import (
    AKTOR,
    CATEGORIES,
    GEREKCE,
    PACKAGE_PRODUCT,
    PRODUCT,
    FakeApi,
    FakeStore,
    make_service,
)

pytestmark = pytest.mark.asyncio


# ==================================================================== okuma

async def test_liste_satirlari_ve_sayfa_kunyesi_doner() -> None:
    service, _, _, _ = make_service()
    sonuc = await service.products()
    assert sonuc["ok"] is True
    assert sonuc["connected"] is True
    assert sonuc["items"][0]["menu_id"] == 27
    assert sonuc["meta"]["per_page"] == 25
    # Süzgeç sözleşmesi YEREL üretilir: geçit düşse bile kutular çizilebilir.
    assert sonuc["filters"]["default_status"] == "all"


async def test_tek_karakterlik_arama_istege_konmaz() -> None:
    # Sözleşme "en az 2 karakter" diyor; göndermek `422` üretir ve kullanıcı
    # yazmaya devam ederken hata görürdü.
    service, api, _, _ = make_service()
    await service.products(q="T")
    assert api.used("products")[0]["q"] == ""
    await service.products(q="Ta")
    assert api.used("products")[1]["q"] == "Ta"


async def test_gecit_dusunce_okuma_firlatmaz_ve_connected_false_doner() -> None:
    # K7: uç yine 200 verir ve panel çökmez. `ok: True` OKUMANIN başarısını
    # değil UCUN sağlığını anlatır; ayrımı `connected` taşır.
    service, api, _, _ = make_service()
    api.fail = {"products", "categories", "product"}

    liste = await service.products()
    assert liste["ok"] is True and liste["connected"] is False
    assert liste["items"] == [] and liste["error"]
    # Süzgeçler yine geldi: ekran boş bir kabukla değil, çalışır bir kabukla kalır.
    assert liste["filters"]["statuses"]

    ozet = await service.overview()
    assert ozet["ok"] is True and ozet["connected"] is False

    kategoriler = await service.categories()
    assert kategoriler["ok"] is True and kategoriler["connected"] is False

    # Tekil okuma AYRI: kayıt gerçekten yoksa ekran formu açmamalı.
    tekil = await service.product(27)
    assert tekil["ok"] is False and tekil["product"] == {}


async def test_ozet_sayaclari_meta_toplamindan_okunur() -> None:
    # Tam listeyi çekip saymak, 80 ürünlük katalogda dört sayfa indirmek
    # demekti ve sayı yine sunucudakiyle aynı çıkardı.
    service, api, _, _ = make_service(api=FakeApi(products=[
        dict(PRODUCT),
        {**PRODUCT, "menu_id": 28, "status": False},
        {**PRODUCT, "menu_id": 29, "sold_out_today": True},
    ]))
    sonuc = await service.overview()
    assert sonuc["counts"]["total"] == 3
    assert sonuc["counts"]["inactive"] == 1
    assert sonuc["counts"]["sold_out"] == 1
    assert sonuc["counts"]["categories"] == 3
    # Görselsiz ürün sayısı ANCAK tam tarama ile bulunur; -1 "bilinmiyor"
    # demektir ve panel kutuyu çizmez. Sıfır yazmak yalan olurdu.
    assert sonuc["counts"]["no_image"] == -1
    assert api.used("products")[0]["per_page"] == 1


async def test_kategori_agaci_derinlikle_doner() -> None:
    service, _, _, _ = make_service()
    sonuc = await service.categories()
    assert [row["depth"] for row in sonuc["items"]] == [0, 0, 1]


# ============================================================== yazma zinciri

async def test_gerekce_backendde_de_denetlenir_ve_istek_gitmez() -> None:
    # K9 — çift kapı: arayüzde zorunlu göstermek, istemcinin gövdeyi elle
    # kurmasını engellemez.
    service, api, store, _ = make_service()
    sonuc = await service.create_product(name="Karnıyarık", price_kurus=9500,
                                         reason="kısa", actor=AKTOR)
    assert sonuc["ok"] is False and sonuc["blocked"] is True
    assert api.writes() == []
    assert store.audit == []


async def test_yazmadan_once_denendi_izi_duser() -> None:
    # "Ne yapmaya çalıştık" kaydı, çağrı yarıda kaldığında TEK kanıttır:
    # sunucunun defteri hiç ulaşmamış isteği bilmez.
    service, api, store, _ = make_service()
    api.fail = {"create_product"}
    sonuc = await service.create_product(name="Karnıyarık", price_kurus=9500,
                                         reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is False
    assert store.results("product.create") == ["denendi", "hata"]


async def test_basarili_yazma_izi_ok_ile_kapanir_ve_kimlik_yanittan_gelir() -> None:
    service, _, store, _ = make_service()
    sonuc = await service.create_product(name="Karnıyarık", price_kurus=9500,
                                         reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is True and sonuc["dry_run"] is False
    assert store.results("product.create") == ["denendi", "ok"]
    # Açma işleminde kimlik ancak yanıtta belli olur; sonuç satırı onu taşır.
    assert store.actions("product.create")[1]["target_id"] == 99


async def test_dry_run_her_cagriya_ACIKCA_gecilir() -> None:
    # Geçidin varsayılanına GÜVENİLMEZ: `config/local.yaml` git dışıdır ve
    # orada `dry_run_default: true` yazıyor olabilir. Bayrağı atlayan bir
    # çağrı hiçbir şey yazmadan `{"ok": true}` alır ve ekran "kaydedildi" der.
    service, api, _, _ = make_service()
    await service.create_product(name="Karnıyarık", price_kurus=9500, reason=GEREKCE,
                                 actor=AKTOR)
    await service.update_product(27, fields={"priority": 5}, reason=GEREKCE, actor=AKTOR)
    await service.mark_sold_out(27, reason=GEREKCE, actor=AKTOR)
    await service.clear_sold_out(27, reason=GEREKCE, actor=AKTOR)
    await service.clear_image(27, reason=GEREKCE, actor=AKTOR)
    await service.create_category(name="Tatlı", reason=GEREKCE, actor=AKTOR)
    await service.update_category(3, fields={"priority": 5}, reason=GEREKCE, actor=AKTOR)

    yazmalar = [(ad, kwargs) for ad, _, kwargs in api.calls if ad in set(api.writes())]
    assert yazmalar, "hiç yazma çağrısı kaydedilmedi"
    for ad, kwargs in yazmalar:
        assert "dry_run" in kwargs, f"{ad} bayrağı hiç göndermemiş"
        assert isinstance(kwargs["dry_run"], bool), f"{ad} bayrağı None geçirmiş"


async def test_kuru_provada_yanittaki_bayrak_okunur() -> None:
    # Bir kurulum provayı ayardan geri açarsa ekran "yapıldı" DEMEMELİ.
    service, api, store, _ = make_service(config={"dry_run_default": True})
    sonuc = await service.update_product(27, fields={"price_kurus": 10000},
                                         reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is True and sonuc["dry_run"] is True
    assert sonuc["would"]["price_kurus"] == 10000
    assert store.results("product.update") == ["denendi", "dry_run"]
    # Bayrak geçide de AÇIKÇA gitti; geçidin kendi varsayılanına bırakılmadı.
    assert api.used("update_product")[0]["dry_run"] is True


async def test_iz_yazilamazsa_is_durmaz() -> None:
    # K7: yerel defterin patlaması ürünün fiyatını yazmamayı gerektirmez.
    service, api, store, _ = make_service(store=FakeStore())
    store.broken = True
    sonuc = await service.update_product(27, fields={"priority": 3}, reason=GEREKCE,
                                         actor=AKTOR)
    assert sonuc["ok"] is True
    assert api.used("update_product")[0]["priority"] == 3


# ======================================================= sözleşmenin kenarları

async def test_paket_urunune_fiyat_yazilmaz_ve_istek_gonderilmez() -> None:
    # Paket ürününün gerçek fiyatı o günün paket fiyatıdır; buraya tutar
    # yazmak günün menüsünü YANLIŞ TUTARA satardı.
    service, api, _, _ = make_service(api=FakeApi(products=[dict(PACKAGE_PRODUCT)]))
    sonuc = await service.update_product(41, fields={"price_kurus": 12000},
                                         reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is False and sonuc["blocked"] is True
    assert "paket" in sonuc["error"].lower()
    assert api.writes() == []

    # Aynı ürünün ADI yazılabilir: kilit yalnız fiyattadır.
    ok = await service.update_product(41, fields={"name": "Günün Menüsü (öğle)"},
                                      reason=GEREKCE, actor=AKTOR)
    assert ok["ok"] is True


async def test_pasiflestirme_guncelleme_ucundan_yapilamaz() -> None:
    # `PATCH status: false` ile `retire` sunucuda AYNI sonucu üretir
    # (`menu_status = 0`). İkisine farklı izin verip birini serbest bırakmak,
    # `bld_products.retire` iznini süs hâline getirirdi.
    service, api, _, _ = make_service()
    sonuc = await service.update_product(27, fields={"status": False}, reason=GEREKCE,
                                         actor=AKTOR)
    assert sonuc["ok"] is False and sonuc["blocked"] is True
    assert api.writes() == []

    # Yeniden AÇMAK serbesttir: ürünü satışa döndürmek yıkıcı değildir ve
    # sözleşmede ayrı bir "restore" ucu yok.
    geri = await service.update_product(27, fields={"status": True}, reason=GEREKCE,
                                        actor=AKTOR)
    assert geri["ok"] is True
    assert api.used("update_product")[0]["status"] is True


async def test_taninmayan_alan_reddedilir_sessizce_dusurulmez() -> None:
    # Laravel bilmediği alanı yok sayar: "kaydedildi" diyen ekranın arkasında
    # hiçbir yere yazılmamış bir değer kalırdı.
    service, api, _, _ = make_service()
    sonuc = await service.update_product(27, fields={"price": 100}, reason=GEREKCE,
                                         actor=AKTOR)
    assert sonuc["ok"] is False and "price" in sonuc["error"]
    assert api.writes() == []


async def test_bos_sayi_alani_sessizce_sifira_donmez() -> None:
    # Panelde temizlenen kutu `null` gönderiyor. `as_int` ile 0'a çevirmek,
    # kullanıcının silmek istediği değeri "sıfır" yapardı — fiyatta bu, ürünü
    # bedava satmak demek.
    service, api, _, _ = make_service()
    for alan in ("price_kurus", "minimum_qty", "priority"):
        sonuc = await service.update_product(27, fields={alan: None}, reason=GEREKCE,
                                             actor=AKTOR)
        assert sonuc["ok"] is False, alan
    assert api.writes() == []


async def test_bos_kismi_govde_reddedilir() -> None:
    # Yalnız `reason` taşıyan bir `PATCH` hiçbir şey değiştirmeden denetim
    # izine satır yazardı.
    service, api, _, _ = make_service()
    sonuc = await service.update_product(27, fields={}, reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_kategori_listesi_tam_listedir_ve_bos_dizi_gecerlidir() -> None:
    # `category_ids` gönderilirse pivot tablo ona EŞİTLENİR; boş dizi ürünü
    # bütün kategorilerden çıkarır ve bu meşru bir eylemdir.
    service, api, _, _ = make_service()
    await service.update_product(27, fields={"category_ids": []}, reason=GEREKCE,
                                 actor=AKTOR)
    assert api.used("update_product")[0]["category_ids"] == []


async def test_negatif_fiyat_ve_adet_reddedilir_sifir_gecerli() -> None:
    service, api, _, _ = make_service()
    kotu = await service.create_product(name="Ekmek", price_kurus=-1, reason=GEREKCE,
                                        actor=AKTOR)
    assert kotu["ok"] is False
    # Sıfır fiyat GEÇERLİ: paket bileşeni olarak satılan ekmek, ayran.
    iyi = await service.create_product(name="Ekmek", price_kurus=0, reason=GEREKCE,
                                       actor=AKTOR)
    assert iyi["ok"] is True
    assert api.used("create_product")[0]["price_kurus"] == 0


# ================================================================== yıkıcı

async def test_izinsiz_satistan_kaldirma_engellenir_ve_ize_yazilir() -> None:
    # Uç noktadaki `requires` ilk kapıdır; bu ikincisidir (K9 — çift kapı).
    service, api, store, bus = make_service()
    sonuc = await service.retire_product(27, reason=GEREKCE, actor=AKTOR,
                                         allow_destructive=False)
    assert sonuc["ok"] is False and sonuc["blocked"] is True
    assert api.writes() == []
    assert store.results("product.delete") == ["engellendi"]
    assert bus.events == []


async def test_satistan_kaldirma_olayi_yayinlar_ve_kayit_silmez() -> None:
    service, api, store, bus = make_service()
    sonuc = await service.retire_product(27, reason=GEREKCE, actor=AKTOR,
                                         allow_destructive=True)
    assert sonuc["ok"] is True
    assert sonuc["data"]["soft_deleted"] is True
    # Tek yazma çağrısı YUMUŞAK kaldırmadır; gerçek bir silme ucu yok.
    assert api.writes() == ["delete_product"]
    assert bus.names() == ["bld_products.product_retired"]
    assert store.results("product.delete") == ["denendi", "ok"]


async def test_kuru_provada_olay_yayinlanmaz() -> None:
    # BLD'de hiçbir şey değişmedi; dinleyicileri uyandırmak yalan olurdu.
    service, _, _, bus = make_service(config={"dry_run_default": True})
    sonuc = await service.retire_product(27, reason=GEREKCE, actor=AKTOR,
                                         allow_destructive=True)
    assert sonuc["dry_run"] is True
    assert bus.events == []


async def test_zaten_kaldirilmis_urun_icin_istek_gonderilmez() -> None:
    # "Kaldırdım ama listede duruyor" yanılgısını önler; geri açma yolu yazılır.
    service, api, _, _ = make_service(
        api=FakeApi(products=[{**PRODUCT, "status": False}]))
    sonuc = await service.retire_product(27, reason=GEREKCE, actor=AKTOR,
                                         allow_destructive=True)
    assert sonuc["ok"] is False
    assert "Satışta" in sonuc["error"]
    assert api.writes() == []


async def test_olay_dinleyicisi_patlarsa_is_basarili_kalir() -> None:
    # K7: ürün BLD'de satıştan kaldırıldı; dinleyicinin patlaması onu geri
    # getirmez.
    service, _, _, bus = make_service()
    bus.fail = True
    sonuc = await service.retire_product(27, reason=GEREKCE, actor=AKTOR,
                                         allow_destructive=True)
    assert sonuc["ok"] is True


# ================================================================== görsel

async def test_gorsel_icerigi_denetim_izine_yazilmaz() -> None:
    # `00-genel.md` §8.2: görselde yalnız künye yazılır. Ham base64 izi
    # okunamaz ve tabloyu yönetilemez kılardı.
    service, api, store, _ = make_service()
    icerik = "data:image/jpeg;base64," + ("A" * 400)
    sonuc = await service.set_image(27, content=icerik, filename="tavuk.jpg",
                                    reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is True
    for row in store.actions("product.image"):
        assert icerik not in row["detail"]
        assert "AAAA" not in row["detail"]
    # Künye yine de duruyor: hangi dosya, kaç bayt, hangi tür.
    assert store.detail(1)["bytes"] == 184320
    assert store.detail(1)["mime"] == "image/jpeg"
    # İçerik geçide OLDUĞU GİBİ gider; base64 çözümü geçidin işidir.
    assert api.used("set_product_image")[0]["content"] == icerik


async def test_bos_gorsel_gonderilmez() -> None:
    service, api, _, _ = make_service()
    sonuc = await service.set_image(27, content="", filename="x.jpg", reason=GEREKCE,
                                    actor=AKTOR)
    assert sonuc["ok"] is False
    assert api.writes() == []


async def test_gorseli_olmayan_urunden_gorsel_silmek_hata_degildir() -> None:
    # Sonuç odaklı: istenen son hâl zaten geçerli. Taze okuma bile yapılmaz —
    # ekranın gördüğü hâl bayat olabilir.
    service, api, _, _ = make_service(
        api=FakeApi(products=[{**PRODUCT, "image_url": None}]))
    sonuc = await service.clear_image(27, reason=GEREKCE, actor=AKTOR)
    assert sonuc["ok"] is True
    assert api.used("delete_product_image")


# ================================================================= tükendi

async def test_tukendi_gerekcesi_mutfaga_gider_not_ayri_kalir() -> None:
    service, api, store, _ = make_service()
    sonuc = await service.mark_sold_out(27, note="Tedarikçi 15:00 sonrası getirecek",
                                        reason="Tavuk tedariki gelmedi, bugünlük kapatıldı",
                                        actor=AKTOR)
    assert sonuc["ok"] is True
    cagri = api.used("mark_product_sold_out")[0]
    assert cagri["reason"].startswith("Tavuk tedariki")
    assert cagri["note"] == "Tedarikçi 15:00 sonrası getirecek"
    # Not denetim izine de düşer; gerekçe zaten her satırda var.
    assert store.detail(0)["note"].startswith("Tedarikçi")


async def test_bos_not_null_olarak_gider() -> None:
    # Boş dize "boş bir not" demektir; `null` "not yok".
    service, api, _, _ = make_service()
    await service.mark_sold_out(27, note="", reason=GEREKCE, actor=AKTOR)
    assert api.used("mark_product_sold_out")[0]["note"] is None


# ============================================================== kategoriler

async def test_kategori_dongusu_istek_gonderilmeden_yakalanir() -> None:
    # Çekirdek `NestedTree` böyle bir kaydı kabul edip ağacı bozardı ve hata
    # ancak site menüsü çizilemediğinde fark edilirdi.
    service, api, _, _ = make_service()
    sonuc = await service.update_category(4, fields={"parent_id": 5}, reason=GEREKCE,
                                          actor=AKTOR)
    assert sonuc["ok"] is False and sonuc["blocked"] is True
    assert api.writes() == []


async def test_kategoriyi_koke_tasimak_serbesttir() -> None:
    # `parent_id: null` GERÇEK BİR DEĞERDİR; düşürülseydi bir alt kategoriyi
    # kökten ayırmak imkânsız olurdu.
    service, api, _, _ = make_service()
    sonuc = await service.update_category(5, fields={"parent_id": None}, reason=GEREKCE,
                                          actor=AKTOR)
    assert sonuc["ok"] is True
    assert api.used("update_category")[0]["parent_id"] is None


async def test_kategori_slug_gondermez() -> None:
    # `permalink_slug` addan üretilir; elle yazdırmak sitedeki adresi yönetici
    # yazım hatasına bağlardı.
    service, api, _, _ = make_service()
    await service.create_category(name="Tatlı", reason=GEREKCE, actor=AKTOR)
    assert "slug" not in api.used("create_category")[0]


async def test_kategori_okunamazsa_dongu_denetimi_yazmaya_izin_vermez() -> None:
    # Denetim yapılamadıysa yazmak, ağacı bozma riskini kör olarak almaktır.
    service, api, _, _ = make_service(api=FakeApi(categories=list(CATEGORIES)))
    api.fail = {"categories"}
    sonuc = await service.update_category(4, fields={"parent_id": 3}, reason=GEREKCE,
                                          actor=AKTOR)
    assert sonuc["ok"] is False
    assert api.writes() == []


# =================================================== yerel iz ve tercihler

async def test_yerel_iz_okunur_ve_detay_cozulur() -> None:
    service, _, _, _ = make_service()
    await service.create_product(name="Karnıyarık", price_kurus=9500, reason=GEREKCE,
                                 actor=AKTOR)
    sonuc = await service.audit_trail()
    assert sonuc["ok"] is True
    assert sonuc["items"][0]["action"] == "product.create"
    assert isinstance(sonuc["items"][0]["detail"], dict)


async def test_tercih_taninmayan_anahtari_reddeder() -> None:
    # Sessizce yutulan bir tercih, kaydettiğini sanan kullanıcıya her açılışta
    # eski ekranı gösterirdi.
    service, _, store, _ = make_service()
    sonuc = await service.save_prefs({"tema": "koyu"}, actor=AKTOR)
    assert sonuc["ok"] is False
    assert store.prefs == {}


async def test_tercih_yazilir_ve_geri_okunur() -> None:
    service, _, _, _ = make_service()
    sonuc = await service.save_prefs({"page_size": 500, "status_filter": "inactive"},
                                     actor=AKTOR)
    # Tavan sunucununkidir; 500 sessizce 100'e iner.
    assert sonuc["page_size"] == 100
    assert sonuc["status_filter"] == "inactive"
