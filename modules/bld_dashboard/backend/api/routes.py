"""Kontrol Paneli — HTTP yüzeyi.

Her uçta `requires(...)` vardır (K9): arayüzde düğmeyi gizlemek yetkilendirme
değildir. `module.yaml` → `http.requires` taban izni verir, uçlar onu DARALTIR.

ÜÇ UÇ, İKİ İZİN:

    GET /overview   bld_dashboard.view     ekran sözleşmesi + tercih, AĞA ÇIKMAZ
    GET /summary    bld_dashboard.view     canlı gövde — yedi blok + akış
    PUT /prefs      bld_dashboard.manage   yalnız YEREL görüntüleme tercihi

`PUT /prefs` NEDEN `manage` İSTİYOR. `bld_orders` aynı işi `view` altında
tutuyor ve gerekçesi doğruydu: sayfa boyutunu değiştirmek bir iş yazması
değildir ve `manage` istemek, o modüldeki üç gerçek yazma ucuyla arasındaki
ayrımı anlamsızlaştırırdı. Burada ayrışacak bir yazma YOK — sözleşme bu alanda
tek bir yazma ucu saymıyor ve BLD'ye giden hiçbir çağrı bir şey değiştirmiyor.
İkisini de `view`e bağlasaydık `manage` hiçbir kapıyı açmayan bir anahtar
olurdu; rol matrisinde söz veren ama karşılığı olmayan bir satır, olmayan bir
izinden kötüdür.

YAZMA UCU YOK, `dry_run` DA YOK. Sözleşme bu alanı salt okunur ilan ediyor
(`dashboard.md` → "Bu alanda yazma ucu yoktur ve okumalar denetlenmez"), bu
yüzden burada `dryRun` alanı taşıyan tek bir gövde bulunmaz. `bld_api`'nin
"her yazmada açık `dry_run=`" kuralı konusuz kalır; ihlal değildir.

GEREKÇE DE YOK. Yıkıcı işlem yok, BLD'de değişen bir şey yok ve gerekçe
istemek denetim izine "yoklama aralığını 60 yaptım" satırları yazdırırdı.

Servis HTTP hatası fırlatmaz: `{"ok": False, "error": …}` döner ve ekran
mesajı gösterir. 4xx yalnız izin ve şema kapısından çıkar.

ROTA DOSYASI METOT ADLARINI SABİTLER, denetleyici ona uyar: `overview` →
`service().overview()`, `summary` → `service().summary()`, `save_prefs` →
`service().save_prefs()`. Ayrışma ne açılışta ne `route:list`te hata verir;
yalnız uç çağrılınca patlar.
"""

from __future__ import annotations

from typing import Any, ClassVar

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, Query, requires

from ..service import DashboardService

#: İzin anahtarları tek yerde durur: uçlar ve testler aynı dizeyi okur, yazım
#: hatası bir kapıyı sessizce açık bırakamaz.
VIEW = "bld_dashboard.view"
MANAGE = "bld_dashboard.manage"

router = APIRouter()
_service: DashboardService | None = None


def bind(service: DashboardService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> DashboardService:
    if _service is None:
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


# ================================================================== okuma

@router.get("/overview")
async def overview(
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Ekranın sözleşmesi ve kullanıcının tercihleri. AĞA ÇIKMAZ (K7).

    Panel bunu açılışta BİR KEZ çağırır; canlı gövde `/summary`den gelir.
    Etiketleri ve seviye adlarını her yoklamada tekrar göndermek, 30 saniyede
    bir değişmeyen bir sözlüğü tele koymak olurdu.
    """
    return await service().overview()


@router.get("/summary")
async def summary(
    date: str = Query("", max_length=10),
    location_id: int = Query(0, ge=0),
    user: CurrentUser = requires(VIEW),
) -> dict[str, Any]:
    """Gösterge panelinin canlı gövdesi. YOKLANAN UÇ BUDUR.

    Süzgeçler sorgu dizesindedir (gövde değil): bu bir okumadır ve
    kopyalanabilir bir adresinin olması, "şu günün paneline bak" demenin en
    kısa yolu. `date` boşsa sunucu bugünü kullanır.
    """
    return await service().summary(date=date, location_id=location_id)


# ============================================================ ekran tercihi

class PrefsBody(BaseModel):
    """Görüntüleme tercihi. BLD'ye HİÇBİR ŞEY GİTMEZ.

    `extra="forbid"`: yanlış yazılmış bir alan (`poll_second`) sessizce düşüp
    "hiç gönderilmemiş" sayılsaydı, kullanıcı kaydettiğini sanar ve ekran eski
    aralıkta yoklamaya devam ederdi. 422 ile geri dönmesi doğrudur.

    `actor` GÖVDEDEN ALINMAZ — oturumdan gelir. Tercih satırı kimin yazdığını
    tutuyor ve istemcinin o adı yazabilmesi, satırı imzasız bir deftere
    çevirirdi.
    """

    #: `ClassVar` ile işaretli, çünkü `km_sdk` pydantic'in `ConfigDict` tipini
    #: dışa vurmuyor ve modül pydantic'i doğrudan import etmiyor (K2). Düz
    #: sözlük pydantic için yeterli; işaret yalnız "bu bir alan değil" der.
    model_config: ClassVar[dict[str, Any]] = {"extra": "forbid"}

    #: Alt sınır 10 saniye: paylaşılan `bld-control-panel` kovası 3000/saat/IP
    #: ve bu ekran her yoklamada iki çağrı yapıyor. Tavan 300 — beş dakikada
    #: bir tazelenen bir ekranın "canlı" olduğunu iddia etmesi yanlış olurdu.
    poll_seconds: int | None = Field(default=None, ge=10, le=300)
    #: 0 = sunucunun varsayılan işletmesi.
    location_id: int | None = Field(default=None, ge=0)
    flow_limit: int | None = Field(default=None, ge=3, le=50)
    flow_enabled: bool | None = None


@router.put("/prefs")
async def save_prefs(
    body: PrefsBody,
    user: CurrentUser = requires(MANAGE),
) -> dict[str, Any]:
    values = {key: value for key, value in body.model_dump().items() if value is not None}
    if not values:
        # Boş gövde hiçbir şey değiştirmeden bir satır yazardı.
        return {"ok": False, "error": "Kaydedilecek bir tercih gönderilmedi."}
    return await service().save_prefs(values, actor=user.full_name)
