"""Abonelikler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — abonelik siparişleri gece üretilmeye devam ediyorken
yönetim ekranının açılmaması, sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.

`notify` yeteneği İSTENMEZ: sözleşme SMS'ini ve OTP'yi BLD sunucusu gönderir,
Kontrol Merkezi yalnız tetikler (K3).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import SubscriptionsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = SubscriptionsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("abonelikler hazır",
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)))
