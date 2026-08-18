"""Kantin Cihazları — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. Kantine erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — kantin çalışmaya devam ediyorken yönetim ekranının
açılmaması, sorunun kendisini görünmez yapardı.

Bu modül kantine YALNIZ `canteen.api` yeteneğinden bakar (K4). Ham `httpx`
yoktur: cihaz token'ı, yeniden deneme ve hata biçimi geçidin işidir ve tek
yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import CanteenDeviceService


def register(ctx: ModuleContext) -> None:
    # Kiosk kaydı kantindedir; geçit olmadan bu ekranın yapabileceği bir şey yok.
    canteen = ctx.capability("canteen.api")

    # Yazıcı İSTEĞE BAĞLI: yoksa modül yine yüklenir, yalnız "kodu bas" düğmesi
    # kapanır ve nedeni yazılır (K7).
    printer = ctx.try_capability("printer")

    service = CanteenDeviceService(
        canteen=canteen,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        printer=printer,
        publish=ctx.publish,
        # Eşleme fişi kantin raporlarıyla aynı rafa yazılır; `export_path`
        # ayarlanmadıysa uygulama geneli hiyerarşi geçerlidir.
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("kantin cihazları hazır", printer=printer is not None)
