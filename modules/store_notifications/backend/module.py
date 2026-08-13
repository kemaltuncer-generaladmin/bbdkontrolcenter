"""Bildirimler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur, yetenek deftere yazılır ve router bağlanır. Mağaza erişilemez olsa
bile modül yüklenir ve ekran durumu anlatır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import NotificationsService, NotifySender

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Müşteri/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Müşteri"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    printer = ctx.try_capability("printer")
    # `notify` YALNIZ SMS katmanının durumunu göstermek ve [Bağlantıyı sına]
    # için kullanılır; müşteriye giden bildirim mağazadan geçer (K4). Yetenek
    # kapalıysa Kanallar sekmesindeki SMS kutusu "kurulmamış" der, ekran çalışır.
    notify = ctx.try_capability("notify")

    service = NotificationsService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        notify=notify,
        publish=ctx.publish,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )

    # `store.notify.send` — Siparişler, Talepler, İadeler ve Deneme Kulübü
    # ekranları müşteriye bildirim göndermek için bunu çağırır. Dört ekranın
    # kendi gönderim yolunu kurması, sessiz saat ve limit disiplinini dört kez
    # (ve dört farklı biçimde yanlış) kurmak olurdu (K3).
    ctx.provide("store.notify.send", NotifySender(service))

    ctx.add_router(bind(service))
    ctx.log.info("bildirimler hazır", printer=printer is not None, sms=notify is not None)
