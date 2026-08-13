"""Dahili AI Ekranları — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Katalog taraması ve AI çağrısı ilk istekle
başlar — modül yüklenirken 29 sayfalık tarama yapmak açılışı dakikalarca
bekletirdi ve mağaza erişilemezse modül hiç yüklenemezdi (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import AiService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Ürün/<yıl>/<ay>
#:
#: NEDEN "Ürün": bu ekranın ürettiği iki rapor da katalog hakkındadır (sağlık
#: bulguları ve o bulguları gidermenin maliyeti). Ayrı bir "AI" rafı açmak,
#: aynı işin çıktısını iki klasöre bölerdi.
CATEGORY = "Mağaza"
SUBCATEGORY = "Ürün"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")

    service = AiService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("dahili AI ekranları hazır", printer=printer is not None)
