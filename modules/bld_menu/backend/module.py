"""Menü Yönetimi — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — satış devam ediyorken menü ekranının açılmaması, sorunun
kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import MenuService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = MenuService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    # KURU PROVA VARSAYILANI YOK ve günlüğe de yazılmaz: bu modülde ayardan
    # okunan bir varsayılan bilerek tanımlanmadı (bkz. `api/routes.py` başlığı).
    ctx.log.info("menü yönetimi hazır", location_id=ctx.config.get("location_id", 0))
