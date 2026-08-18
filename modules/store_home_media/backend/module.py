"""Ana Ekran Görselleri — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. Mağaza erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7).

YAZICI YETENEĞİ ARTIK ALINMIYOR. Ekranın "yerleşim raporu" (PDF) ve CSV
çıktısı 18.08.2026'da kaldırıldı; ekranın tek işi ana ekrandaki görselleri
değiştirmek ve kâğıda basılacak bir çıktısı yok. Alınmayan yetenek ilan
edilmez (`module.yaml` → `consumes`).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import HomeMediaService


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")

    service = HomeMediaService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("ana ekran görselleri hazır")
