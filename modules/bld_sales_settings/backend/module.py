"""Satış Ayarları — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — satış şalterinin ne durumda olduğunu göremeyen bir
yönetici, ekranın hiç açılmamasından daha kötü bir yerde kalmaz; açılmayan
ekran sorunun kendisini de görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import SalesSettingsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = SalesSettingsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    # Vitrin kimliği günlüğe yazılır: yanlış vitrine yazan bir kurulumun tek
    # erken işareti budur ve ayarların hangi vitrine gittiği hiçbir ekranda
    # ayrıca sorulmuyor.
    ctx.log.info("Satış ayarları hazır",
                 location_id=ctx.config.get("location_id", 0),
                 stock_days=ctx.config.get("stock_days", 2))
