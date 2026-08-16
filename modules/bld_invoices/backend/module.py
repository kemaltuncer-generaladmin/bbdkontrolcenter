"""Faturalar — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — sunucudaki fatura uçları henüz yayında değil ve ekranın
o yüzden hiç açılmaması, "uç eksik" ile "modül bozuk" arasındaki farkı
görünmez kılardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı başlık, zaman penceresi, nonce, oran sınırı ve kuru prova taşıma
geçidin işidir.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import InvoicesService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/BLD/Faturalar/<yıl>/<ay>
CATEGORY = "BLD"
SUBCATEGORY = "Faturalar"


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")
    # Yazıcı İSTEĞE BAĞLI: yoksa modül yine yüklenir ve belge yine üretilir,
    # yalnız baskı düğmesi kapanır (K7).
    printer = ctx.try_capability("printer")

    service = InvoicesService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("faturalar hazır", printer=printer is not None,
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)))
