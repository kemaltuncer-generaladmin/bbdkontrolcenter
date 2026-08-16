"""Site İçeriği — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — site ayakta ve müşteriye görünüyorken içerik ekranının
hiç açılmaması, sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı ve kuru prova taşıma geçidin işidir ve tek yerde durur.

YAZICI YETENEĞİ KULLANILMAZ. Bu ekranın çıktısı kâğıt değil, herkese açık bir
web sayfasıdır; "içerik dökümü bas" gibi bir istek yoktu ve olmayan bir isteğe
bağımlılık ilan etmek, modülü gereksizce yazıcıya bağlardı.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import CmsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = CmsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
    )
    ctx.add_router(bind(service))
    ctx.log.info(
        "Site içeriği hazır",
        # İkisi de sahada "neden hiçbir şey yazılmıyor" sorusunun cevabı olur:
        # kuru prova varsayılanı açık kaldıysa yazma hiç gitmez, tazeleme
        # varsayılanı kapalıysa yazma gider ama site eski görünür.
        dry_run_default=bool(ctx.config.get("dry_run_default", False)),
        revalidate_after_save=bool(ctx.config.get("revalidate_after_save", True)),
    )
