"""SMS Paneli — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — SMS'ler gitmeye devam ediyorken yönetim ekranının
açılmaması, sorunun kendisini görünmez yapardı.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur ve
NETGSM İSTEMCİSİ DE YOKTUR: imza, zaman penceresi, nonce, oran sınırı ve kuru
prova taşıma geçidin işidir; SMS'i gönderen taraf ise BLD sunucusunun kendi
sağlayıcısıdır (`Services\\Sms\\SmsSender`).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import SmsService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")
    # `notify` İSTEĞE BAĞLI ve GÖNDERİM YOLU DEĞİL. Kontrol Merkezi'nin kendi
    # Netgsm şeridi burada tek bir soruya cevap verir — "bu makinede ayrı bir
    # SMS şeridi var mı" — ve panel bunu BLD'nin sağlayıcı durumundan AYRI bir
    # satırda yazar. Ayrım olmasaydı BLD'nin sırrı eksikken yönetici buradaki
    # Netgsm ayarını düzeltmeye çalışır ve hiçbir şey değişmezdi. Yetenek yoksa
    # ekran tümüyle çalışır (K7).
    notify = ctx.try_capability("notify")

    service = SmsService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        notify=notify,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    ctx.log.info("sms paneli hazır", platform_lane=notify is not None,
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)))
