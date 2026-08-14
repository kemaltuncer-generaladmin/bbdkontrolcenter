"""Siparişler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek deftere yazılır ve router bağlanır. Mağaza erişilemez olsa bile
modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .service import OrdersService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Satış/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Satış"


class OrderCard:
    """`store.order.card` yeteneğinin gövdesi.

    Müşteriler ve Talepler ekranları bir siparişin künyesini buradan okur; K3
    gereği o modüller bu modülü import ETMEZ. Yetenek SALT OKURDUR: sipariş
    yazan hiçbir yordam dışarı verilmez, yazma her zaman bu modülün kendi
    uçlarından ve kendi izinlerinden geçer.
    """

    def __init__(self, service: OrdersService) -> None:
        self._service = service

    async def card(self, order_id: int) -> dict[str, Any]:
        return await self._service.card(int(order_id))


#: Zamanlanmış aşama taramasının ulaşabilmesi için canlı servis burada tutulur.
#: `module.yaml → tasks` handler'ı çekirdek tarafından bağlamsız da
#: çağrılabiliyor; o durumda tek tutamak budur.
_LIVE: OrdersService | None = None


def build_service(ctx: ModuleContext) -> OrdersService:
    return OrdersService(
        api=ctx.capability("store.api"),
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=ctx.try_capability("printer"),
        publish=ctx.publish,
        # MÜŞTERİ AŞAMA SMS'İ — sipariş alındı · kargoya verildi · teslim
        # edildi. İSTEĞE BAĞLI: Bildirimler ekranı kapalıysa sipariş akışı
        # aynen çalışır, yalnız müşteri bilgilendirilmez ve ekran nedenini
        # söyler (K7). Metin, fren ve tekrar engeli o modülün içindedir;
        # burada modül IMPORT EDİLMEZ (K3), yalnız yetenek çözülür.
        stage_notify=ctx.try_capability("store.notify.stage"),
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )


def live() -> OrdersService | None:
    return _LIVE


def register(ctx: ModuleContext) -> None:
    global _LIVE
    printer = ctx.try_capability("printer")
    stage_notify = ctx.try_capability("store.notify.stage")

    service = build_service(ctx)
    _LIVE = service
    ctx.provide("store.order.card", OrderCard(service))
    ctx.add_router(bind(service))
    ctx.log.info("siparişler hazır", printer=printer is not None,
                 stageSms=stage_notify is not None)
