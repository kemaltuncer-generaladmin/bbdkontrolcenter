"""Durum Monitörü — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — İZLEME EKRANININ, İZLEDİĞİ SİSTEM DÜŞTÜĞÜ İÇİN
AÇILMAMASI, sorunun kendisini görünmez yapardı. Bu modülde K7 bir nezaket
değil, işin tanımı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx`, ham
`subprocess` ve doğrudan SSH yoktur: imzalı `X-Control-Signature` başlığı,
zaman penceresi, nonce hatırlama, oran sınırı ve kuru prova taşıma geçidin
işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import StatusMonitorService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = StatusMonitorService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("durum monitörü hazır",
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)),
                 poll_seconds=int(ctx.config.get("poll_seconds", 60) or 60))
