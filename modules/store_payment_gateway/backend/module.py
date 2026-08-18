"""Link ile Ödeme — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız
servis kurulur ve router bağlanır. Mağaza da SMS de erişilemez olsa bile
modül yüklenir; ekran neyin kapalı olduğunu söyler ve elden kapatma yolu
açık kalır (K7).
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import PaymentGatewayService

#: Masaüstündeki rapor hiyerarşisinde bu modülün rafı:
#: Raporlar/Mağaza/Finans/<yıl>/<ay>
CATEGORY = "Mağaza"
SUBCATEGORY = "Finans"


def register(ctx: ModuleContext) -> None:
    store_api = ctx.capability("store.api")
    # SMS ve yazıcı İSTEĞE BAĞLIDIR: yoksa ekran kurulur, o bölümler kapalı
    # ve GEREKÇELİ görünür. `capability()` zorunlu olsaydı SMS'i açılmamış bir
    # kurulumda tahsilat ekranı hiç yüklenmezdi.
    notify = ctx.try_capability("notify")
    printer = ctx.try_capability("printer")
    # Netgsm kimlik bilgileri KASADA durur (K8). Kasa da isteğe bağlı alınır:
    # yokluğunda ekran açılır, yalnız SMS ayarları kartı gerekçesiyle kapalı
    # görünür — tahsilat ekranının tamamı bir yapılandırma eksiği yüzünden
    # yüklenmemeliydi (K7).
    secrets = ctx.try_capability("secrets")

    service = PaymentGatewayService(
        api=store_api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        notify=notify,
        printer=printer,
        secrets=secrets,
        category=CATEGORY,
        subcategory=SUBCATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("link ile ödeme hazır", sms=notify is not None, printer=printer is not None,
                 vault=secrets is not None)
