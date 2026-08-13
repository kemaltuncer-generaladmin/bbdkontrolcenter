"""Kantin Raporları — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import ReportService

#: Masaüstündeki hiyerarşide bu modülün rafı.
CATEGORY = "Kantin"


def register(ctx: ModuleContext) -> None:
    # Rapor kaynağı kantindir; geçit olmadan okunacak bir şey yok.
    canteen = ctx.capability("canteen.api")

    # Baskı platform yeteneğidir; modül `lp` çağırmaz (K4). Yazıcı yoksa modül
    # yine yüklenir, yalnız yazdırma düğmesi hata anlatır (K7).
    printer = ctx.capability("printer")

    # Çıktı yolu uygulama geneli hiyerarşiden gelir:
    # Masaüstü/Kontrol Merkezi/Raporlar/Kantin/<yıl>/<ay>. `export_path`
    # doluysa kullanıcının açık tercihi kazanır.
    service = ReportService(
        canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config,
        printer=printer, category=CATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("kantin raporları hazır", kategori=CATEGORY)
