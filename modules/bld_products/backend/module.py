"""Ürün Yönetimi — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — satış devam ediyorken katalog ekranının hiç açılmaması,
sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı, görselin base64 hazırlığı ve `dry_run` taşıma geçidin işidir ve tek
yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ProductsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = ProductsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("ürün yönetimi hazır",
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)))
