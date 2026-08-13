"""Manuel Yedekleme — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Yıkıcı uçlarda gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca
serviste tekrar denetlenir — istemci şemayı atlatabilir.

YEDEK ADI GÖVDEDE TAŞINIR, YOLDA DEĞİL. Ad dosya adıdır; yolda taşımak
`%2f`/`..` kaçışlarıyla uğraşmak demektir ve bir gün biri o kaçışı kaçırır.
Gövdedeki değer serviste beyaz listeyle denetlenir (`inventory.safe_name`).

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import BackupsService

router = APIRouter()
_service: BackupsService | None = None


def bind(service: BackupsService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> BackupsService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/backups")
async def backups(
    user: CurrentUser = requires("store_backups.view"),
) -> dict[str, Any]:
    """Envanter + durum bandı + disk göstergesi. Liste onlarca satır; sayfalama yok."""
    return await service().backups()


@router.get("/audit")
async def audit(
    name: str = Query("", max_length=200),
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = requires("store_backups.view"),
) -> dict[str, Any]:
    return await service().audit(name=name, limit=limit)


@router.post("/export")
async def export(
    user: CurrentUser = requires("store_backups.view"),
) -> dict[str, Any]:
    """Envanter CSV'si — rapor rafına yazılır (Raporlar/Mağaza/Denetim/yıl/ay)."""
    return await service().export_csv()


# ================================================================== yazma

class CreateBody(BaseModel):
    #: database · uploads · config — servis süzer ve sabit sıraya sokar.
    scope: list[str] = Field(default_factory=list)
    note: str = Field(default="", max_length=255)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/backups")
async def create(
    body: CreateBody,
    user: CurrentUser = requires("store_backups.manage"),
) -> dict[str, Any]:
    return await service().create(scope=body.scope, note=body.note, reason=body.reason,
                                  actor=user.full_name, dry_run=body.dryRun)


class NameBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post("/backups/verify")
async def verify(
    body: NameBody,
    user: CurrentUser = requires("store_backups.manage"),
) -> dict[str, Any]:
    """Sağlama toplamı denetimi. VERİ DEĞİŞTİRMEZ; gerekçe kullanıcıdan istenmez."""
    return await service().verify(body.name, actor=user.full_name)


@router.post("/backups/download")
async def download(
    body: NameBody,
    user: CurrentUser = requires("store_backups.manage"),
) -> dict[str, Any]:
    """Yedeği yerel diske indirir. Büyük dosyada sunucudaki yola yönlendirir."""
    return await service().download(body.name, actor=user.full_name)


# --------------------------------------------------------- yıkıcı uçlar

class RestoreBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/backups/restore")
async def restore(
    body: RestoreBody,
    user: CurrentUser = requires("store_backups.restore"),
) -> dict[str, Any]:
    """DEPODAKİ EN YIKICI UÇ. Ayrı izin (`store_backups.restore`, yalnız admin),
    gerekçe ve kuru prova varsayılanı; servis ayrıca önce güvenlik yedeği aldırır
    ve alınamazsa geri yüklemeyi hiç başlatmaz."""
    return await service().restore(body.name, reason=body.reason, actor=user.full_name,
                                   dry_run=body.dryRun)


class DeleteBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/backups/delete")
async def delete(
    body: DeleteBody,
    user: CurrentUser = requires("store_backups.delete"),
) -> dict[str, Any]:
    """Yaş kapısı burada uygulanır; mağaza ucu yayınlanana kadar açıklamalı
    `ok: False` döner (sessizce patlamaz)."""
    return await service().delete(body.name, reason=body.reason, actor=user.full_name)
