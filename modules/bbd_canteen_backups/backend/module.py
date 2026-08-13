"""Kantin Yedekleri — giriş noktası."""

from __future__ import annotations

from pathlib import Path

from km_sdk import ModuleContext

from .api.routes import bind
from .service import BackupService


def register(ctx: ModuleContext) -> None:
    canteen = ctx.capability("canteen.api")

    # İndirilen kopyalar depo kökündeki data/backups altına yazılır (git dışı).
    configured = str(ctx.config.get("local_path") or "").strip()
    local_dir = Path(configured) if configured else ctx.module_path.parents[1] / "data" / "backups"

    service = BackupService(canteen=canteen, store=ctx.store, log=ctx.log,
                            config=ctx.config, local_dir=local_dir)
    ctx.add_router(bind(service))
    ctx.log.info("kantin yedekleri hazır", local=str(local_dir))
