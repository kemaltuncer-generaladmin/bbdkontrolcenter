"""KDS Yönetimi — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — mutfak çalışmaya devam ediyorken yönetim ekranının
açılmaması, sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import KdsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")
    # Yazıcı İSTEĞE BAĞLI: yoksa modül yine yüklenir, yalnız baskı sunan
    # bölümler kapanır (K7). Servis bunu `/overview` yanıtında
    # `printer_available` ile bildirir; panel düğmeyi ona göre gösterir.
    printer = ctx.try_capability("printer")

    service = KdsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("KDS yönetimi hazır", printer=printer is not None,
                 dry_run_default=bool(ctx.config.get("dry_run_default", True)))
