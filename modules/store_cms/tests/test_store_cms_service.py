"""CMS servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_cms_backend.service import CmsService
from store_cms_fakes import PAGE, FakeApi, FakeLog, FakeStore

LEGAL = {
    "distance_sales": "mesafeli-satis-sozlesmesi",
    "refund": "iade-ve-cayma-hakki",
    "privacy": "gizlilik-politikasi",
    "cookies": "cerez-politikasi",
}


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[CmsService, FakeApi, FakeStore]:
    api = api or FakeApi()
    store = store or FakeStore()
    service = CmsService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "legal_slugs": LEGAL,
                "faq_slug": "sikca-sorulan-sorular", "site_url": "https://bbdstore.com.tr",
                **config},
        fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    return service, api, store


def _page(page_id: int, slug: str, *, title: str = "Sayfa", html: str = "",
          meta_title: str = "", meta_description: str = "") -> dict[str, Any]:
    return {
        "id": page_id,
        "translations": [{"locale": "tr", "page_title": title, "url_key": slug,
                          "html_content": html, "meta_title": meta_title,
                          "meta_description": meta_description, "meta_keywords": ""}],
        "channels": [{"id": 1, "code": "default"}],
        "updated_at": "2026-08-01T09:00:00",
    }


# ============================================================ K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    service, api, _ = _service()
    api.fail.add("cms_pages")
    result = await service.pages()
    assert result["ok"] is True              # uç patlamaz
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]


async def test_yonlendirmeler_okunamazsa_kirik_baglanti_denetimi_yine_calisir() -> None:
    service, api, _ = _service()
    api.fail.add("url_rewrites")
    result = await service.pages()
    assert result["connected"] is True
    assert result["total"] == 1


async def test_bloklar_okunamazsa_sekme_durumu_anlatir() -> None:
    service, api, _ = _service()
    api.fail.add("themes")
    result = await service.blocks()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []


# ================================================== arama ve süzme (ölçek)

async def test_arama_icerik_metninde_calisir_ve_alinti_doner() -> None:
    # Mağaza ucu içerik METNİNDE arayamıyor; sayfa sayısı onlarla ölçüldüğü
    # için tam liste çekilip burada süzülür (üründe bu YASAK).
    api = FakeApi([
        _page(1, "hakkimizda", title="Hakkımızda", html="<p>1998'den beri yayıncılık.</p>"),
        _page(2, "iade-ve-cayma-hakki", title="İade", html="<p>14 gün içinde iade.</p>"),
    ])
    service, _, _ = _service(api)
    result = await service.pages(q="yayıncılık")
    assert [row["slug"] for row in result["items"]] == ["hakkimizda"]
    assert "yayıncılık" in result["items"][0]["hit"]


async def test_tam_liste_tek_istekte_cekilir() -> None:
    service, api, _ = _service()
    await service.pages()
    assert api.used("cms_pages")[0]["all_pages"] is True


async def test_cipler_sayilari_dogru_verir() -> None:
    api = FakeApi([
        _page(1, "bos-sayfa", html=""),
        _page(2, "mesafeli-satis-sozlesmesi", html="<p>" + "A" * 400 + "</p>",
              meta_title="Başlık", meta_description="Açıklama"),
    ])
    service, _, _ = _service(api)
    result = await service.pages()
    assert result["counts"]["empty"] == 1
    assert result["counts"]["legal"] == 1
    assert result["counts"]["seo_missing"] == 1
    result = await service.pages(chip="legal")
    assert [row["slug"] for row in result["items"]] == ["mesafeli-satis-sozlesmesi"]


async def test_agac_dallara_ayrilir_bos_dal_gosterilmez() -> None:
    api = FakeApi([_page(1, "gizlilik-politikasi"), _page(2, "urun-rehberi")])
    service, _, _ = _service(api)
    result = await service.pages()
    dallar = {branch["key"] for branch in result["tree"]}
    assert dallar == {"legal", "other"}


# ================================================== gerekçe ve yazma kapısı

async def test_kisa_gerekce_backendde_de_reddedilir() -> None:
    # K9: arayüzde gizlemek yetkilendirme değildir; istemci şemayı atlatabilir.
    service, api, _ = _service()
    result = await service.save(7, patch={"title": "X"}, reason="ok", actor="Ali")
    assert result["ok"] is False
    assert "Gerekçe" in result["error"]
    assert api.used("save_cms_page") == []


async def test_kaydetmeden_once_surum_alinir() -> None:
    service, api, store = _service()
    result = await service.save(7, patch={"title": "Yeni Başlık"},
                                reason="yasal metin güncellendi", actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert len(store.versions) == 1
    kayit = store.versions[0]
    assert kayit["title"] == "Mesafeli Satış Sözleşmesi"       # ESKİ hâl saklandı
    assert kayit["actor"] == "Ayşe"
    assert api.used("save_cms_page")[0]["payload"]["page_title"] == "Yeni Başlık"


async def test_surum_alinamazsa_hic_yazilmaz() -> None:
    # Geri alınamayacak bir değişikliği sessizce uygulamak, bu ekranın tek
    # gerçek güvencesini kaldırırdı.
    store = FakeStore()
    store.broken = True
    service, api, _ = _service(store=store)
    result = await service.save(7, patch={"title": "X"}, reason="yeterince uzun gerekçe",
                                actor="Ali")
    assert result["ok"] is False
    assert "geri alınamayacak" in result["error"]
    assert api.used("save_cms_page") == []


async def test_kaydetme_icerigi_temizler() -> None:
    api = FakeApi([_page(7, "iade-ve-cayma-hakki")])
    service, _, _ = _service(api)
    await service.save(7, patch={"content": '<p>Metin</p><script>x()</script>'},
                       reason="içerik güncellendi", actor="Ali", dry_run=False)
    body = api.used("save_cms_page")[0]["payload"]
    assert body["html_content"] == "<p>Metin</p>"


async def test_adres_degisiminde_yonlendirme_uyarisi_doner() -> None:
    service, _, _ = _service()
    result = await service.save(7, patch={"slug": "yeni-adres"},
                                reason="adres sadeleştirildi", actor="Ali", dry_run=False)
    assert "301" in result["slugNotice"]


async def test_bos_yama_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.save(7, patch={}, reason="yeterince uzun gerekçe", actor="Ali")
    assert result["ok"] is False


async def test_ayni_adreste_ikinci_sayfa_acilmaz() -> None:
    service, api, _ = _service()
    result = await service.create(title="Mesafeli Satış Sözleşmesi", slug="",
                                  reason="ikinci kez açılıyor", actor="Ali")
    assert result["ok"] is False
    assert "zaten" in result["error"]
    assert api.used("save_cms_page") == []


# ============================================================ geri alma

async def test_geri_alma_eski_metni_yazar_ve_yeni_surum_birakir() -> None:
    service, api, store = _service()
    await service.save(7, patch={"title": "İkinci hâl"}, reason="ilk düzenleme yapıldı",
                       actor="Ali", dry_run=False)
    surum = store.versions[0]["id"]

    result = await service.restore(7, version_id=surum, reason="yanlış metin yayınlandı",
                                   actor="Veli", dry_run=False)
    assert result["ok"] is True
    # SİLME YOK, EKLEME VAR: geri almadan önceki hâl de saklandı.
    assert len(store.versions) == 2
    assert store.versions[1]["action"] == "restore"
    body = api.used("save_cms_page")[-1]["payload"]
    assert body["page_title"] == "Mesafeli Satış Sözleşmesi"


async def test_baska_sayfanin_surumune_donulemez() -> None:
    service, _, store = _service()
    await service.save(7, patch={"title": "X"}, reason="ilk düzenleme yapıldı", actor="Ali",
                       dry_run=False)
    store.versions[0]["page_id"] = 99
    result = await service.restore(7, version_id=1, reason="yanlış metin yayınlandı",
                                   actor="Ali")
    assert result["ok"] is False
    assert "başka bir sayfaya" in result["error"]


async def test_gecmis_en_yeni_basta_gelir() -> None:
    service, _, _ = _service()
    await service.save(7, patch={"title": "A"}, reason="ilk düzenleme yapıldı", actor="Ali",
                       dry_run=False)
    await service.save(7, patch={"title": "B"}, reason="ikinci düzenleme yapıldı", actor="Ali",
                       dry_run=False)
    result = await service.versions(7)
    assert [row["id"] for row in result["items"]] == [2, 1]


# ================================================================== SSS

async def test_sss_sayfasi_soru_cevaba_ayrilir() -> None:
    api = FakeApi([_page(3, "sikca-sorulan-sorular",
                         html="<h3>Kargo?</h3><p>2 gün.</p><h3>İade?</h3><p>14 gün.</p>")])
    service, _, _ = _service(api)
    result = await service.faq()
    assert result["available"] is True
    assert result["pageId"] == 3
    assert [pair["question"] for pair in result["pairs"]] == ["Kargo?", "İade?"]


async def test_sss_sayfasi_yoksa_sekme_ne_yapilacagini_soyler() -> None:
    api = FakeApi([_page(1, "hakkimizda")])
    service, _, _ = _service(api)
    result = await service.faq()
    assert result["available"] is False
    assert "sikca-sorulan-sorular" in result["reason"]


async def test_sss_kaydetmek_sayfanin_tamamini_yeniden_kurar() -> None:
    api = FakeApi([_page(3, "sikca-sorulan-sorular", html="<h3>Eski</h3><p>Cevap</p>")])
    service, _, _ = _service(api)
    result = await service.save_faq(pairs=[{"question": "Yeni", "answer": "Cevap"}],
                                    reason="sorular güncellendi", actor="Ali", dry_run=False)
    assert result["ok"] is True
    body = api.used("save_cms_page")[0]["payload"]
    assert body["html_content"] == "<h3>Yeni</h3><p>Cevap</p>"


async def test_sorusuz_sss_kaydedilmez() -> None:
    api = FakeApi([_page(3, "sikca-sorulan-sorular", html="<h3>S</h3><p>C</p>")])
    service, _, _ = _service(api)
    result = await service.save_faq(pairs=[], reason="sorular güncellendi", actor="Ali")
    assert result["ok"] is False


# =========================================================== yasal metinler

async def test_eksik_yasal_metin_bildirilir() -> None:
    api = FakeApi([_page(1, "gizlilik-politikasi", html="<p>" + "A" * 500 + "</p>")])
    service, _, _ = _service(api)
    result = await service.legal()
    assert result["missing"] == 3
    kayit = {item["key"]: item for item in result["items"]}
    assert kayit["privacy"]["found"] is True
    assert kayit["privacy"]["empty"] is False
    assert kayit["refund"]["found"] is False


async def test_cok_kisa_yasal_metin_bos_sayilir() -> None:
    api = FakeApi([_page(1, "cerez-politikasi", html="<p>Yakında</p>")])
    service, _, _ = _service(api)
    kayit = {item["key"]: item for item in (await service.legal())["items"]}
    assert kayit["cookies"]["found"] is True
    assert kayit["cookies"]["empty"] is True


# ========================================================= yönlendirmeler

async def test_yonlendirme_dongusu_yazmadan_once_yakalanir() -> None:
    service, api, _ = _service()
    api.rewrites = [{"id": 4, "request_path": "yeni", "target_path": "/eski",
                     "redirect_type": 301}]
    result = await service.save_redirect(source="/eski", target="/yeni", kind=301,
                                         reason="adres değişikliği yapıldı", actor="Ali")
    assert result["ok"] is False
    assert api.used("save_url_rewrite") == []


async def test_mevcut_yonlendirmeler_okunamazsa_yazilmaz() -> None:
    # Çakışma denetlenmeden yazmak, döngüyü canlıda keşfetmek demektir.
    service, api, _ = _service()
    api.fail.add("url_rewrites")
    result = await service.save_redirect(source="/eski", target="/yeni", kind=301,
                                         reason="adres değişikliği yapıldı", actor="Ali")
    assert result["ok"] is False
    assert api.used("save_url_rewrite") == []


async def test_gecerli_yonlendirme_yazilir_ve_yol_normallesir() -> None:
    service, api, store = _service()
    result = await service.save_redirect(source="https://bbdstore.com.tr/eski/",
                                         target="/yeni", kind=301,
                                         reason="adres değişikliği yapıldı", actor="Ali",
                                         dry_run=False)
    assert result["ok"] is True
    assert api.used("save_url_rewrite")[0]["payload"]["request_path"] == "eski"
    assert store.audit[-1]["action"] == "save_redirect"


# ============================================================ menü ve blok

async def test_menu_ucu_yoksa_sekme_nedenini_soyler_patlamaz() -> None:
    service, _, _ = _service()
    result = await service.menus()
    assert result["ok"] is True
    assert result["available"] is False
    assert "bbd_cms_menus" in result["reason"]


async def test_bloklar_salt_okunur_ve_icerigi_temizlenmis_gelir() -> None:
    service, api, _ = _service()
    api.themes_payload = {"items": [{"id": 2, "name": "Duyuru", "type": "static_content",
                                     "status": 1,
                                     "options": {"html": "<p>Metin</p><script>x()</script>"}}]}
    result = await service.blocks()
    assert result["editable"] is False
    assert result["items"][0]["html"] == "<p>Metin</p>"


# ============================================================ yol güvenliği

async def test_rapor_klasoru_disindaki_dosya_basilmaz() -> None:
    class FakePrinter:
        def __init__(self) -> None:
            self.printed: list[Any] = []

        async def print_file(self, path: Any, *, title: str = "", copies: int = 1) -> dict:
            self.printed.append(path)
            return {"printer": "test"}

        async def status(self) -> dict:
            return {"ready": True}

    printer = FakePrinter()
    service = CmsService(api=FakeApi(), store=FakeStore(), log=FakeLog(),
                         config={"locale": "tr"}, printer=printer,
                         fallback_dir=Path("/tmp/km-test-raporlar"))
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert printer.printed == []


async def test_denetim_izi_gerekceyi_saklar() -> None:
    service, _, store = _service()
    await service.save(7, patch={"title": "Yeni"}, reason="yasal metin güncellendi",
                       actor="Ayşe", dry_run=False)
    izler = [row for row in store.audit if row["action"] == "save"]
    assert izler[-1]["reason"] == "yasal metin güncellendi"
    assert izler[-1]["result"] == "ok"


async def test_denetim_izi_hatayi_da_yazar() -> None:
    api = FakeApi([dict(PAGE)])
    service, _, store = _service(api)
    api.fail.add("save_cms_page")
    result = await service.save(7, patch={"title": "Yeni"}, reason="yasal metin güncellendi",
                                actor="Ali")
    assert result["ok"] is False
    assert any(row["result"] == "hata" for row in store.audit)
