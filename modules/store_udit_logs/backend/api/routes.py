"""UDİT İşlem Kayıtları — HTTP yüzeyi.

SALT OKUNUR: burada tek bir POST/PUT/DELETE ucu denetim kaydına DOKUNMAZ.
Yazan tek uç `POST /preview` ve `POST /export`'tur; onlar da kaydı değil
RAPOR DOSYASINI üretir. Denetim kaydını düzenleyen ya da silen bir uç
eklenirse ekranın kalıcı notu ("Bu ekran salt okunurdur") yalana döner.

Her uçta `requires(...)` vardır (K9): arayüzde gizlemek yetkilendirme
değildir. Döküm uçları AYRI anahtar ister (`store_udit_logs.export`) — kaydı
okumak ile kaydı binlerce satır hâlinde dışarı çıkarmak aynı şey değildir.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import AuditService

router = APIRouter()
_service: AuditService | None = None


def bind(service: AuditService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> AuditService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/entries")
async def entries(
    start: str = Query("", max_length=32),
    end: str = Query("", max_length=32),
    q: str = Query("", max_length=160),
    user: str = Query("", max_length=120),
    action: str = Query("", max_length=48),
    entity: str = Query("", max_length=48),
    entityId: int = Query(0, ge=0),
    ip: str = Query("", max_length=64),
    result: str = Query("", max_length=24),
    source: str = Query("", max_length=16),
    destructive: bool = Query(False),
    reasoned: bool = Query(False),
    cursor: str = Query("", max_length=512),
    size: int = Query(0, ge=0, le=250),
    user_: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    """İmleçli sayfa. TARİH ARALIĞI ZORUNLU — boşsa liste değil kural döner."""
    return await service().entries(start=start, end=end, q=q, user=user, action=action,
                                   entity=entity, entity_id=entityId, ip=ip, result=result,
                                   source=source, destructive=destructive, reasoned=reasoned,
                                   cursor=cursor, size=size)


@router.get("/entry")
async def entry(
    key: str = Query("", max_length=128),
    user: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    """Tek kaydın kırpılmamış hâli. Anahtar `r:<id>` (mağaza) ya da
    `g:<istek kimliği>` (geçit) biçimindedir; sayısal kimlik TEK BAŞINA
    yetmez, iki kaynakta da aynı numara vardır."""
    return await service().entry(key)


@router.get("/history")
async def history(
    entity: str = Query("", max_length=48),
    entityId: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    days: int = Query(0, ge=0, le=400),
    user: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    """`store.audit.for` yeteneğinin HTTP yüzü — 19 panelin "İşlem geçmişi"
    sekmesi bunu çağırır. İmza dar tutulur; genişletme isteği gelirse panel
    bu ekrana geçer (`open('store_udit_logs', {entity, entityId})`)."""
    return await service().history(entity, entityId, limit=limit, days=days)


@router.get("/reference")
async def reference(
    user: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    return await service().reference()


@router.get("/exports")
async def exports(
    limit: int = Query(50, ge=1, le=500),
    user: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    """Bu ekrandan alınan dökümlerin izi — kim, ne zaman, hangi aralığı aldı."""
    return await service().exports(limit=limit)


# ================================================================== rapor

class ReportBody(BaseModel):
    """Rapor süzgeci. `kind` `reportChain` tarafından gövdeye konur."""

    kind: str = Field(default="dump", max_length=24)
    start: str = Field(default="", max_length=32)
    end: str = Field(default="", max_length=32)
    q: str = Field(default="", max_length=160)
    user: str = Field(default="", max_length=120)
    action: str = Field(default="", max_length=48)
    entity: str = Field(default="", max_length=48)
    entityId: int = 0
    ip: str = Field(default="", max_length=64)
    result: str = Field(default="", max_length=24)
    source: str = Field(default="", max_length=16)
    destructive: bool = False
    reasoned: bool = False


@router.post("/preview")
async def preview(
    body: ReportBody,
    user: CurrentUser = requires("store_udit_logs.export"),
) -> dict[str, Any]:
    """Denetim dökümü (`dump`) ya da tek kaydın geçmişi (`record`).

    Kaydı OKUMAK `view` iznidir; kaydı dosyaya dökmek ayrı anahtardır: döküm
    IP adresi ve personel adı taşır ve binaya girdiği gibi çıkmaz.
    """
    return await service().preview(body.kind, body.model_dump(), actor=user.full_name)


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_udit_logs.export"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_udit_logs.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    body: ReportBody,
    user: CurrentUser = requires("store_udit_logs.export"),
) -> dict[str, Any]:
    """Aralığın TAMAMININ CSV'si — rapor klasörüne yazılır ve iz bırakır.
    Görünen sayfanın CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv(body.model_dump(), actor=user.full_name)
