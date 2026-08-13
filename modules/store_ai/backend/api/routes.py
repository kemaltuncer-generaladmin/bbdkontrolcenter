"""Dahili AI Ekranları — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. Gerekçe `min_length=10` ile ŞEMADA doğrulanır, ayrıca serviste
tekrar denetlenir — istemci şemayı atlatabilir.

DÖRT İZİN, DÖRT AYRI ZARAR:
  · `view`   okur; hiçbir şey harcamaz, hiçbir şey yazmaz.
  · `run`    PARA harcar (jeton). Mağazaya yazmaz.
  · `apply`  MAĞAZAYA YAZAR. Para harcamaz.
  · `manage` bütçeyi ve denetim eşiklerini değiştirir; susturur.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin/şema kapısından çıkar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import AiService

router = APIRouter()
_service: AiService | None = None


def bind(service: AiService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> AiService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/tools")
async def tools(
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    """Araç kartları, uç durumu ve bütçe. Uç yayında değilse kartlar kapalı gelir."""
    return await service().tools()


@router.get("/catalog")
async def catalog(
    refresh: bool = Query(False),
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    """Kural tabanlı katalog denetimi. AI YOKTUR, para harcamaz.

    `refresh=true` taze tarama zorlar: 1.419 üründe ~29 istek eder, bu yüzden
    varsayılan olarak bellekteki taze sonuç döner ve tarama zamanı yazılır.
    """
    return await service().catalog(refresh=refresh)


@router.get("/runs")
async def runs(
    tool: str = Query("", max_length=48),
    status: str = Query("", max_length=16),
    page: int = Query(1, ge=1, le=10_000),
    size: int = Query(0, ge=0, le=200),
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().history(tool=tool, status=status, page=page, size=size)


@router.get("/runs/{token}")
async def run_detail(
    token: str,
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().run_detail(token)


@router.get("/usage")
async def usage(
    days: int = Query(30, ge=1, le=180),
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().usage(days=days)


@router.get("/settings")
async def settings(
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    """Ayarları GÖRMEK `view` yeter; DEĞİŞTİRMEK `manage` ister. Bütçe rakamı
    ekranın her yerinde görünüyor, salt okunur göstermek doğru olan."""
    return await service().settings()


# =================================================== çalıştırma (PARA HARCAR)

class RunBody(BaseModel):
    targetIds: list[int] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=10, max_length=255)
    #: VARSAYILAN TRUE. Kuru prova model çağrısı yapmaz, jeton harcamaz ve
    #: neyin gönderileceğini söyler. Gerçek çalıştırma bilinçli bir karardır.
    dryRun: bool = True


@router.post("/tools/{tool}/run")
async def run_tool(
    tool: str,
    body: RunBody,
    user: CurrentUser = requires("store_ai.run"),
) -> dict[str, Any]:
    """AI aracını çalıştırır. ÖNERİ ÜRETİR, HİÇBİR ŞEY YAZMAZ.

    Ayrı izin anahtarı, çalışmanın MAĞAZAYA değil BÜTÇEYE dokunmasındandır:
    jeton harcayan bir işlem, yazmayan olsa bile onaya bağlıdır (ADR 0012).
    """
    return await service().run_tool(tool, target_ids=body.targetIds, params=body.params,
                                    reason=body.reason, actor=user.full_name,
                                    dry_run=body.dryRun)


# ====================================================== uygulama (YAZAR)

class ApplyBody(BaseModel):
    #: Onaylanan satırların kimlikleri (`hedef:alan`). BOŞ LİSTE "hepsi"
    #: DEĞİLDİR; servis boş seçimi reddeder.
    selections: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=10, max_length=255)
    dryRun: bool = True


@router.post("/runs/{token}/apply")
async def apply(
    token: str,
    body: ApplyBody,
    user: CurrentUser = requires("store_ai.apply"),
) -> dict[str, Any]:
    """Onaylanan önerileri mağazaya uygular.

    Uygulama fark tablosunu YENİDEN HESAPLAMAZ: jetonla saklanan satırları
    okur ve onları gönderir. Kullanıcının okuduğu metin ile mağazaya giden
    metin aynıdır — ekranın kimliği budur.
    """
    return await service().apply(token=token, selections=body.selections, reason=body.reason,
                                 actor=user.full_name, dry_run=body.dryRun)


# ============================================================ bulgu susturma

class MuteBody(BaseModel):
    findingKey: str = Field(min_length=3, max_length=64)
    kind: str = Field(default="", max_length=32)
    targetId: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/catalog/mute")
async def mute(
    body: MuteBody,
    user: CurrentUser = requires("store_ai.manage"),
) -> dict[str, Any]:
    """Bulguyu sustur. SİLME YOKTUR: satır kalır, susturma satırı eklenir."""
    return await service().mute(finding_key=body.findingKey, kind=body.kind,
                                target_id=body.targetId, reason=body.reason,
                                actor=user.full_name)


class UnmuteBody(BaseModel):
    findingKey: str = Field(min_length=3, max_length=64)
    reason: str = Field(min_length=10, max_length=255)


@router.post("/catalog/unmute")
async def unmute(
    body: UnmuteBody,
    user: CurrentUser = requires("store_ai.manage"),
) -> dict[str, Any]:
    return await service().unmute(finding_key=body.findingKey, reason=body.reason,
                                  actor=user.full_name)


# =============================================================== ayarlar

class SettingsBody(BaseModel):
    monthlyBudget: int | None = Field(default=None, ge=0, le=100_000_000)   # kuruş
    budgetBehavior: str | None = Field(default=None, max_length=8)
    budgetEstimateWhenOffline: bool | None = None
    includePassive: bool | None = None
    disabledTools: list[str] | None = None
    minDescriptionChars: int | None = Field(default=None, ge=0, le=5_000)
    metaTitleMax: int | None = Field(default=None, ge=10, le=200)
    metaDescriptionMin: int | None = Field(default=None, ge=0, le=500)
    metaDescriptionMax: int | None = Field(default=None, ge=10, le=500)
    priceOutlierFactor: int | None = Field(default=None, ge=2, le=100)
    reason: str = Field(min_length=10, max_length=255)


#: Ekranın camelCase alanı → tercih anahtarı. Eşleme AÇIKTIR: gövdeyi
#: doğrudan servise vermek, gelecekte eklenen bir alanı sessizce ayara
#: çevirirdi.
_SETTING_FIELDS = {
    "monthlyBudget": "monthly_budget",
    "budgetBehavior": "budget_behavior",
    "budgetEstimateWhenOffline": "budget_estimate_when_offline",
    "includePassive": "include_passive",
    "disabledTools": "disabled_tools",
    "minDescriptionChars": "min_description_chars",
    "metaTitleMax": "meta_title_max",
    "metaDescriptionMin": "meta_description_min",
    "metaDescriptionMax": "meta_description_max",
    "priceOutlierFactor": "price_outlier_factor",
}


@router.post("/settings")
async def save_settings(
    body: SettingsBody,
    user: CurrentUser = requires("store_ai.manage"),
) -> dict[str, Any]:
    """Bütçe ve denetim eşikleri. `manage` ister: bütçe para politikasıdır."""
    values: dict[str, Any] = {}
    for field, key in _SETTING_FIELDS.items():
        value = getattr(body, field)
        if value is not None:
            values[key] = value
    return await service().save_settings(values=values, reason=body.reason,
                                         actor=user.full_name)


# ================================================================= rapor

class PreviewBody(BaseModel):
    kind: str = Field(max_length=24)          # health | usage
    days: int = Field(default=30, ge=1, le=180)


@router.post("/preview")
async def preview(
    body: PreviewBody,
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().preview(body.kind, {"days": body.days})


class PrintBody(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    copies: int = Field(default=1, ge=1, le=20)


@router.post("/print")
async def print_report(
    body: PrintBody,
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().print_report(body.path, copies=body.copies)


@router.get("/printer")
async def printer(
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    return await service().printer_status()


@router.post("/export")
async def export(
    user: CurrentUser = requires("store_ai.view"),
) -> dict[str, Any]:
    """Tüm katalog sağlığı bulguları — rapor klasörüne CSV. Görünen sayfanın
    CSV'sini panel kendisi üretir (sunucuya hiç gitmez)."""
    return await service().export_csv()
