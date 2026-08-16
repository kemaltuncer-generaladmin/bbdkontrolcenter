"""Bildirimler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — duyuru uçları sunucuya henüz dağıtılmamışken ekranın hiç
açılmaması, sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import NoticeService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = NoticeService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
    )
    ctx.add_router(bind(service))
    # Kuru prova varsayılanı LOG'A YAZILIR: açık kalmış bir kurulumda hiçbir
    # şey yayınlanmaz ve ekran "yayınlandı" der. Açılışta görülmesi, o sessiz
    # hâli aramaktan ucuzdur.
    ctx.log.info("Bildirimler hazır",
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)),
                 refresh_seconds=int(ctx.config.get("refresh_seconds", 120)))
