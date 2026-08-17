"""Kontrol Paneli — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — işletmenin canlı özetini gösteren ekranın, sunucu
düştüğünde hiç açılmaması sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama ve oran
sınırı geçidin işidir ve tek yerde durur.

`ctx.publish` ALINMIYOR: bu ekran BLD'de hiçbir şey değiştirmiyor, dolayısıyla
duyurulacak bir olayı da yok (`module.yaml` → `events.publishes: []`).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import DashboardService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = DashboardService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
    )
    ctx.add_router(bind(service))
    # KURU PROVA VARSAYILANI GÜNLÜĞE YAZILMIYOR çünkü bu modülde yok: yazma
    # ucu olmayan bir ekranın kuru provası da olmaz. Günlüğe yazılan iki şey,
    # açılışta sahada en çok sorulan iki soru.
    ctx.log.info("kontrol paneli hazır",
                 poll_seconds=ctx.config.get("poll_seconds", 30),
                 flow_enabled=ctx.config.get("flow_enabled", True))
