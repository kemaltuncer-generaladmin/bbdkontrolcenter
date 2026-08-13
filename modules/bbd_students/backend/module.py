"""Öğrenci Yönetimi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import StudentService

#: Masaüstündeki hiyerarşide bu modülün rafı.
CATEGORY = "Öğrenci"


def register(ctx: ModuleContext) -> None:
    # Kantin geçidi olmadan bu modülün yapabileceği bir şey yok; manifestte
    # zorunlu ilan edildiği için çekirdek onu bizden önce yüklemiş olur.
    canteen = ctx.capability("canteen.api")

    # Kart PDF'i uygulama geneli hiyerarşiye yazılır:
    # Masaüstü/Kontrol Merkezi/Raporlar/Öğrenci/<yıl>/<ay>.
    service = StudentService(
        canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config,
        category=CATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
    )
    ctx.add_router(bind(service))
    ctx.log.info("öğrenci yönetimi hazır", kategori=CATEGORY)
