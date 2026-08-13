"""Arama ve SEO — saf dönüşümler ve servis kuralları. Ağa çıkmaz."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_cms_backend import seo
from store_cms_backend.service import CmsService
from store_cms_fakes import FakeApi, FakeLog, FakeStore

GEREKCE = "arama sonucu düzeltiliyor"


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[CmsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = CmsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr",
                "site_url": "https://bbdstore.com.tr", **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


def _term(term_id: int, term: str, *, results: int = 5, uses: int = 1) -> dict[str, Any]:
    # Canlıda doğrulanan biçim: camelCase.
    return {"id": term_id, "term": term, "results": results, "uses": uses,
            "redirectUrl": None, "locale": "tr", "channel": None,
            "updatedAt": "2026-08-11T23:43:47+03:00"}


# ======================================================= URL yeniden yazma

def test_yonlendirme_satiri_camelcase_yanittan_da_okunur() -> None:
    # Komşu pazarlama uçları camelCase dönüyor; snake_case beklemek listeyi
    # boş göstermek olurdu.
    row = seo.rewrite_row({"id": 3, "requestPath": "/eski", "targetPath": "/yeni",
                           "redirectType": 302, "entityType": "product"})
    assert row["source"] == "/eski"
    assert row["target"] == "/yeni"
    assert row["type"] == 302
    assert row["entityLabel"] == "Ürün"


def test_kayit_turu_yoksa_cms_sayfasi_varsayilir() -> None:
    row = seo.rewrite_row({"id": 1, "request_path": "a", "target_path": "b"})
    assert row["entityType"] == seo.DEFAULT_ENTITY


def test_ayni_kaynak_iki_kez_yonlendiriliyorsa_isaretlenir() -> None:
    rows = [seo.rewrite_row({"id": 1, "request_path": "/eski", "target_path": "/bir"}),
            seo.rewrite_row({"id": 2, "request_path": "eski/", "target_path": "/iki"}),
            seo.rewrite_row({"id": 3, "request_path": "/baska", "target_path": "/uc"})]
    assert seo.mark_conflicts(rows) == 2
    assert [row["conflict"] for row in rows] == [True, True, False]


def test_bilinmeyen_kayit_turu_reddedilir() -> None:
    assert seo.entity_error("kategori") != ""
    assert seo.entity_error("category") == ""


async def test_yonlendirme_govdesinde_kayit_turu_gider() -> None:
    # Alan mağazada ZORUNLU; göndermeyen istek doğrulamadan dönerdi.
    service, api, _ = _service()
    result = await service.save_redirect(source="/eski", target="/yeni", kind=301,
                                         entity="product", reason=GEREKCE, actor="Ali",
                                         dry_run=False)
    assert result["ok"] is True
    assert api.used("save_url_rewrite")[0]["payload"]["entity_type"] == "product"


async def test_yonlendirme_silme_gerekce_ister_ve_ize_yazilir() -> None:
    service, api, store = _service()
    api.rewrites = [{"id": 5, "request_path": "eski", "target_path": "/yeni"}]

    kisa = await service.delete_redirect(5, reason="kısa", actor="Ali")
    assert kisa["ok"] is False
    assert api.used("delete_url_rewrite") == []

    result = await service.delete_redirect(5, reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["notice"]
    assert [row["action"] for row in store.audit] == ["delete_redirect", "delete_redirect"]
    # Silmeden ÖNCE kaydın ne olduğu yazılır: sonradan "neydi" diye sorulamaz.
    assert "eski" in store.audit[0]["detail"]


# ========================================================= arama terimleri

def test_sonucsuz_arama_isaretlenir_ve_ozet_bosa_gideni_sayar() -> None:
    rows = [seo.term_row(_term(1, "matematik", results=322, uses=6)),
            seo.term_row(_term(2, "Orjınal", results=0, uses=4)),
            seo.term_row(_term(3, "yks sıralama", results=0, uses=2))]
    assert [row["zeroResults"] for row in rows] == [False, True, True]
    ozet = seo.term_summary(rows)
    assert ozet == {"total": 3, "zero": 2, "uses": 12, "zeroUses": 6, "redirected": 0}


def test_siralama_sonucsuzlari_ve_cok_aranani_one_alir() -> None:
    rows = [seo.term_row(_term(1, "bulundu", results=9, uses=99)),
            seo.term_row(_term(2, "az aranan", results=0, uses=1)),
            seo.term_row(_term(3, "cok aranan", results=0, uses=7))]
    rows.sort(key=seo.term_sort_key)
    assert [row["term"] for row in rows] == ["cok aranan", "az aranan", "bulundu"]


async def test_sonucsuz_suzgeci_yalniz_bulunamayanlari_birakir() -> None:
    api = FakeApi()
    api.terms = [_term(1, "matematik", results=322), _term(2, "orjınal", results=0)]
    service, _, _ = _service(api)

    hepsi = await service.search_terms()
    assert hepsi["total"] == 2
    assert hepsi["summary"]["zero"] == 1

    sadece = await service.search_terms(only_zero=True)
    assert [row["term"] for row in sadece["items"]] == ["orjınal"]
    # Süzgeç açıkken de özet TÜM terimleri anlatır: "19 terimin 8'i sonuçsuz"
    # cümlesi süzgece göre değişseydi ekranda anlamsız olurdu.
    assert sadece["summary"]["total"] == 2


async def test_terim_listesi_sayfa_sayfa_toplanir() -> None:
    api = FakeApi()
    api.terms = [_term(index, f"terim {index}") for index in range(1, 121)]
    service, _, _ = _service(api)
    result = await service.search_terms(size=25)
    assert result["total"] == 120
    assert len(result["items"]) == 25
    assert result["capped"] is False
    assert len(api.used("search_terms")) == 3        # 50 + 50 + 20


async def test_terim_listesi_tavana_dayanirsa_ekran_soyler() -> None:
    api = FakeApi()
    api.terms = [_term(index, f"terim {index}") for index in range(1, 700)]
    service, _, _ = _service(api)
    result = await service.search_terms()
    assert result["capped"] is True
    assert len(api.used("search_terms")) == 10       # TERM_PAGES tavanı


async def test_terimler_okunamazsa_ekran_ayakta_kalir() -> None:
    api = FakeApi()
    api.fail.add("search_terms")
    service, _, _ = _service(api)
    result = await service.search_terms()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["error"]


# =========================================================== eş anlamlılar

def test_kelimeler_virgulle_ayrilir_ve_tekrar_atilir() -> None:
    assert seo.synonym_terms(" kalem, tükenmez ,kalem, ") == ["kalem", "tükenmez"]
    assert seo.synonym_terms(["kalem", "Kalem", "pen"]) == ["kalem", "pen"]
    # TUZAK: mağaza LİSTE değil, virgülle ayrılmış TEK metin bekliyor.
    assert seo.synonym_text(["kalem", "tükenmez"]) == "kalem,tükenmez"


def test_tek_kelimelik_grup_reddedilir() -> None:
    assert "iki kelime" in seo.synonym_error("Kalem", "kalem")


def test_ayni_kelime_iki_gruba_konamaz() -> None:
    mevcut = [seo.synonym_row({"id": 1, "name": "Kalem", "terms": "kalem,tükenmez"})]
    hata = seo.synonym_error("Yazı", "tükenmez, pen", mevcut)
    assert "tükenmez" in hata
    # Aynı kaydın kendisi düzenleniyorsa çakışma sayılmaz.
    assert seo.synonym_error("Kalem", "kalem,tükenmez,pen", mevcut, synonym_id=1) == ""


async def test_gruplar_okunamazsa_yazma_yapilmaz() -> None:
    # Çakışma denetlenmeden yazmak, iki grubu canlıda çakıştırmak demektir.
    api = FakeApi()
    api.fail.add("search_synonyms")
    service, _, _ = _service(api)
    result = await service.save_synonym(name="Kalem", terms="kalem,tükenmez",
                                        reason=GEREKCE, actor="Ali")
    assert result["ok"] is False
    assert api.used("create_search_synonym") == []


async def test_grup_kaydedilirken_kelimeler_tek_metin_olarak_gider() -> None:
    service, api, store = _service()
    result = await service.save_synonym(name="Kalem", terms=" kalem , tükenmez ",
                                        reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert api.used("create_search_synonym")[0]["payload"]["terms"] == "kalem,tükenmez"
    assert store.audit[-1]["action"] == "save_synonym"


async def test_grup_guncellemesi_ayri_uca_gider() -> None:
    api = FakeApi()
    api.synonym_rows = [{"id": 4, "name": "Kalem", "terms": "kalem,tükenmez"}]
    service, _, _ = _service(api)
    result = await service.save_synonym(name="Kalem", terms="kalem,tükenmez,pen",
                                        synonym_id=4, reason=GEREKCE, actor="Ali",
                                        dry_run=False)
    assert result["ok"] is True
    assert api.used("update_search_synonym")
    assert api.used("create_search_synonym") == []


async def test_grup_silme_gerekce_ister() -> None:
    service, api, _ = _service()
    result = await service.delete_synonym(4, reason="kısa", actor="Ali")
    assert result["ok"] is False
    assert api.used("delete_search_synonym") == []


# =========================================================== site haritası

def test_site_haritasi_satiri_iki_yazimi_da_okur_ve_adresi_kurar() -> None:
    row = seo.sitemap_row({"id": 1, "file_name": "sitemap.xml", "path": "/",
                           "generated_at": "2026-08-13T12:00:00"},
                          base_url="https://bbdstore.com.tr")
    assert row["fileName"] == "sitemap.xml"
    assert row["generatedAt"] == "2026-08-13T12:00:00"
    assert row["url"] == "https://bbdstore.com.tr/sitemap.xml"


def test_dosya_adi_xml_olmali_ve_klasor_icermemeli() -> None:
    assert ".xml" in seo.sitemap_error("sitemap.txt", "/")
    assert "klasör" in seo.sitemap_error("maps/sitemap.xml", "/")
    assert seo.sitemap_error("sitemap.xml", "/") == ""


def test_ayni_yolda_ayni_dosya_iki_kez_tanimlanamaz() -> None:
    mevcut = [seo.sitemap_row({"id": 2, "fileName": "sitemap.xml", "path": "/"})]
    assert "zaten tanımlı" in seo.sitemap_error("sitemap.xml", "/", mevcut)
    assert seo.sitemap_error("sitemap.xml", "/urunler", mevcut) == ""


def test_ozet_son_uretim_zamanini_verir() -> None:
    rows = [seo.sitemap_row({"id": 1, "fileName": "a.xml", "generatedAt": "2026-08-01T09:00:00"}),
            seo.sitemap_row({"id": 2, "fileName": "b.xml", "generatedAt": "2026-08-13T12:00:00"}),
            seo.sitemap_row({"id": 3, "fileName": "c.xml"})]
    ozet = seo.sitemap_summary(rows)
    assert ozet["never"] == 1
    assert ozet["lastGeneratedAt"] == "2026-08-13T12:00:00"


async def test_tanim_kaydedilir_ama_dosya_uretilmedigi_soylenir() -> None:
    service, api, _ = _service()
    result = await service.save_sitemap(file_name="sitemap.xml", path="/", reason=GEREKCE,
                                        actor="Ali", dry_run=False)
    assert result["ok"] is True
    # `str.lower()` kullanılmaz: `İ` harfi birleşen noktaya ayrılıyor ve
    # eşleşme sessizce kaçıyor (aynı tuzak `content.fold` içinde de var).
    assert "ÜRETİLMEDİ" in result["notice"]
    assert api.used("generate_sitemap") == []


async def test_uretim_izi_once_ve_sonra_yazilir() -> None:
    service, api, store = _service()
    result = await service.generate_sitemap(3, reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is True
    assert result["files"] == 3
    assert [row["result"] for row in store.audit] == ["denendi", "ok"]
    assert api.used("generate_sitemap")[0]["dry_run"] is False


async def test_uretim_patlarsa_ekran_hata_metnini_alir() -> None:
    api = FakeApi()
    api.fail.add("generate_sitemap")
    service, _, store = _service(api)
    result = await service.generate_sitemap(3, reason=GEREKCE, actor="Ali", dry_run=False)
    assert result["ok"] is False
    assert result["error"]
    assert store.audit[-1]["result"] == "hata"
