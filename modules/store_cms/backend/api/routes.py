"""CMS — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yazan uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import CmsService

router = APIRouter()
_service: CmsService | None = None


def bind(service: CmsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> CmsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/pages")
async def pages(
    q: str = Query("", max_length=160),
    chip: str = Query("", max_length=24),
    channel: str = Query("", max_length=32),
    page: int = Query(1, ge=1, le=1_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    """Arama BAŞLIK, SLUG ve İÇERİK METNİNDE çalışır (mağaza ucu metinde arayamıyor)."""
    return await service().pages(q=q, chip=chip, channel=channel, page=page, size=size)


@router.get("/pages/{page_id}")
async def page_detail(
    page_id: int,
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().page(page_id)


@router.get("/pages/{page_id}/versions")
async def versions(
    page_id: int,
    limit: int = Query(30, ge=1, le=200),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().versions(page_id, limit=limit)


@router.get("/versions/{version_id}")
async def version_body(
    version_id: int,
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    """Sürümün tam içeriği — geri almadan önce okunup karşılaştırılır."""
    return await service().version_body(version_id)


@router.get("/legal")
async def legal(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().legal()


@router.get("/faq")
async def faq(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().faq()


@router.get("/redirects")
async def redirects(
    q: str = Query("", max_length=160),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().redirects(q=q)


@router.get("/search-terms")
async def search_terms(
    q: str = Query("", max_length=160),
    onlyZero: bool = Query(False),
    page: int = Query(1, ge=1, le=1_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    """`onlyZero`: sonuçsuz aramalar — katalog boşluğunu gösteren süzgeç."""
    return await service().search_terms(q=q, only_zero=onlyZero, page=page, size=size)


@router.get("/synonyms")
async def synonyms(
    q: str = Query("", max_length=160),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().synonyms(q=q)


@router.get("/sitemaps")
async def sitemaps(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().sitemaps()


@router.get("/blocks")
async def blocks(
    q: str = Query("", max_length=160),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().blocks(q=q)


@router.get("/menus")
async def menus(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    """Vitrin menüsü ucu mağazada YOK; sekme durumu anlatır, patlamaz (K7)."""
    return await service().menus()


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/audit")
async def audit(
    pageId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().audit(page_id=pageId, limit=limit)


# ================================================================== yazma

class SaveBody(BaseModel):
    #: {title, slug, content, metaTitle, metaDescription, metaKeywords}
    patch: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.put("/pages/{page_id}")
async def save_page(
    page_id: int,
    body: SaveBody,
    user: CurrentUser = requires("store_cms.manage"),
) -> dict[str, Any]:
    """Kaydetmeden ÖNCE mevcut hâl sürüm tablosuna yazılır; alınamazsa yazma yapılmaz."""
    return await service().save(page_id, patch=body.patch, reason=body.reason,
                                actor=user.full_name, dry_run=body.dryRun)


class CreateBody(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    slug: str = Field(default="", max_length=180)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/pages")
async def create_page(
    body: CreateBody,
    user: CurrentUser = requires("store_cms.manage"),
) -> dict[str, Any]:
    return await service().create(title=body.title, slug=body.slug, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


class RestoreBody(BaseModel):
    versionId: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/pages/{page_id}/restore")
async def restore(
    page_id: int,
    body: RestoreBody,
    user: CurrentUser = requires("store_cms.restore"),
) -> dict[str, Any]:
    """Geri alma İÇERİĞİN ÜSTÜNE YAZAR — ayrı izin ister. Satır silinmez;
    geri almadan önceki hâl de yeni bir sürüm olarak eklenir."""
    return await service().restore(page_id, version_id=body.versionId, reason=body.reason,
                                   actor=user.full_name, dry_run=body.dryRun)


class FaqPair(BaseModel):
    question: str = Field(min_length=2, max_length=300)
    answer: str = Field(default="", max_length=20_000)


class FaqBody(BaseModel):
    pairs: list[FaqPair] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/faq")
async def save_faq(
    body: FaqBody,
    user: CurrentUser = requires("store_cms.manage"),
) -> dict[str, Any]:
    """SSS sayfasının TAMAMI yeniden kurulur; listeden çıkardığınız soru gider."""
    return await service().save_faq(
        pairs=[{"question": pair.question, "answer": pair.answer} for pair in body.pairs],
        reason=body.reason, actor=user.full_name, dry_run=body.dryRun)


class RedirectBody(BaseModel):
    source: str = Field(min_length=1, max_length=400)
    target: str = Field(min_length=1, max_length=400)
    type: int = Field(default=301, ge=300, le=399)
    rewriteId: int = Field(default=0, ge=0)
    #: product | category | cms_page — mağaza bu alanı ZORUNLU istiyor.
    entity: str = Field(default="cms_page", max_length=24)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/redirects")
async def save_redirect(
    body: RedirectBody,
    user: CurrentUser = requires("store_cms.redirects"),
) -> dict[str, Any]:
    """Yanlış yönlendirme siteyi döngüye sokar; ayrı izin ve önden denetim vardır."""
    return await service().save_redirect(source=body.source, target=body.target, kind=body.type,
                                         rewrite_id=body.rewriteId, entity=body.entity,
                                         reason=body.reason, actor=user.full_name,
                                         dry_run=body.dryRun)


class ReasonBody(BaseModel):
    """Yalnız gerekçe taşıyan yıkıcı istek gövdesi."""

    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/redirects/{rewrite_id}/delete")
async def delete_redirect(
    rewrite_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_cms.redirects"),
) -> dict[str, Any]:
    """Silme DELETE değil POST: gerekçe gövdeyle taşınıyor ve gövdeli DELETE
    isteği ara katmanlarda sessizce düşebiliyor. Kayıt silinmeden önce denetim
    izine yazılır."""
    return await service().delete_redirect(rewrite_id, reason=body.reason,
                                           actor=user.full_name, dry_run=body.dryRun)


class SynonymBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    #: Virgülle ayrılmış tek metin de liste de kabul edilir; servis tek biçime çevirir.
    terms: str = Field(min_length=1, max_length=1000)
    synonymId: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/synonyms")
async def save_synonym(
    body: SynonymBody,
    user: CurrentUser = requires("store_cms.manage"),
) -> dict[str, Any]:
    return await service().save_synonym(name=body.name, terms=body.terms,
                                        synonym_id=body.synonymId, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


@router.post("/synonyms/{synonym_id}/delete")
async def delete_synonym(
    synonym_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_cms.manage"),
) -> dict[str, Any]:
    return await service().delete_synonym(synonym_id, reason=body.reason,
                                          actor=user.full_name, dry_run=body.dryRun)


class SitemapBody(BaseModel):
    fileName: str = Field(min_length=1, max_length=120)
    path: str = Field(default="/", max_length=200)
    sitemapId: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/sitemaps")
async def save_sitemap(
    body: SitemapBody,
    user: CurrentUser = requires("store_cms.sitemap"),
) -> dict[str, Any]:
    """Tanım DOSYA ÜRETMEZ; üretim ayrı uçtur."""
    return await service().save_sitemap(file_name=body.fileName, path=body.path,
                                        sitemap_id=body.sitemapId, reason=body.reason,
                                        actor=user.full_name, dry_run=body.dryRun)


@router.post("/sitemaps/{sitemap_id}/generate")
async def generate_sitemap(
    sitemap_id: int,
    body: ReasonBody,
    user: CurrentUser = requires("store_cms.sitemap"),
) -> dict[str, Any]:
    """AĞIR İŞ: yayındaki her ürün, kategori ve sayfa dolaşılır (1.419 ürün)."""
    return await service().generate_sitemap(sitemap_id, reason=body.reason,
                                            actor=user.full_name, dry_run=body.dryRun)


# ================================================================== rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    user: CurrentUser = requires("store_cms.view"),
) -> dict[str, Any]:
    """Sayfa listesi CSV'si — rapor klasörüne yazılır."""
    return await service().export_csv()
