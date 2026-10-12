"""Module registration. The canteen remains the student identity authority."""

from __future__ import annotations

import os
from pathlib import Path

from km_sdk import ModuleContext

from .api.routes import bind
from .service import TabletService


def storage_dir(ctx: ModuleContext) -> Path:
    """Where distributed APKs live; must survive redeploys.

    `storage_dir` setting → `$KM_DATA_DIR/bbd_tablet/apps` (the server image
    mounts its persistent `/data` there) → `<repo>/data/bbd_tablet/apps` in
    development. `/app` inside the image is not persistent.
    """
    configured = str(ctx.config.get("storage_dir") or "").strip()
    if configured:
        return Path(configured).expanduser()
    data = os.environ.get("KM_DATA_DIR", "").strip()
    if data:
        return Path(data) / "bbd_tablet" / "apps"
    return ctx.module_path.parents[1] / "data" / "bbd_tablet" / "apps"


def register(ctx: ModuleContext) -> None:
    service = TabletService(
        store=ctx.store,
        canteen=ctx.capability("canteen.api"),
        config=ctx.config,
        storage_dir=storage_dir(ctx),
    )
    ctx.add_router(bind(service))
    ctx.log.info("öğrenci tablet yönetimi hazır")
