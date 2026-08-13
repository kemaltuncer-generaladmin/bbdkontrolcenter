"""Kantin geçidi — giriş noktası.

`register(ctx)` içinde iş YAPILMAZ: yalnızca yetenek deftere yazılır. Kantine
ilk istek, bir modül gerçekten veri isteyene kadar atılmaz — cihaz kaydı da o
ana ertelenir.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .client import CanteenApi


def register(ctx: ModuleContext) -> None:
    base_url = str(ctx.config.get("base_url") or "").strip()
    if not base_url:
        raise RuntimeError(
            "Kantin adresi yok. config/local.yaml → modules.bbd_canteen_api.base_url"
        )

    api = CanteenApi(
        base_url=base_url,
        device_name=str(ctx.config.get("device_name") or "Kontrol Merkezi"),
        secrets=ctx.capability("secrets"),
        log=ctx.log,
        timeout=float(ctx.config.get("timeout_seconds") or 20),
    )

    ctx.provide("canteen.api", api)
    ctx.log.info("kantin geçidi hazır", base_url=base_url)
