"""Kargo Yönetimi — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur, yetenek deftere yazılır ve router bağlanır. Mağaza erişilemez
olsa bile modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ShippingService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Kargo/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Kargo"


class ShipmentLookup:
    """`store.shipment.byOrder` yeteneğinin YÜZEYİ.

    Servisin tamamı değil, TEK metodu paylaşılır: Siparişler ekranının kargo
    sekmesine gönderi listesi lazım, gönderi iptal etme yetkisi değil. Dar
    yüzey, yeteneği tüketen modülün yanlışlıkla yazma yapmasını imkânsız
    kılar (K3).
    """

    def __init__(self, service: ShippingService) -> None:
        self._service = service

    async def by_order(self, order_id: int) -> dict[str, Any]:
        """Siparişin gönderileri ve son hareketleri. İSTİSNA FIRLATMAZ."""
        return await self._service.by_order(int(order_id))


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")
    notifier = ctx.try_capability("store.notify.send")
    # "Kargoya verildi" aşama SMS'i. Bildirimler ekranı kapalıysa gönderi
    # yine açılır ve etiket yine basılır; yalnız müşteriye mesaj gitmez ve
    # sonuç bunu satır olarak söyler (K7).
    stage_notify = ctx.try_capability("store.notify.stage")

    service = ShippingService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        notifier=notifier,
        stage_notify=stage_notify,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.provide("store.shipment.byOrder", ShipmentLookup(service))
    ctx.add_router(bind(service))
    ctx.log.info("kargo yönetimi hazır", printer=printer is not None,
                 notify=notifier is not None, stageNotify=stage_notify is not None)
