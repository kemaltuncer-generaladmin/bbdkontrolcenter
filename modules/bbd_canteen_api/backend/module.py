"""Kantin geçidi — giriş noktası.

`register(ctx)` içinde iş YAPILMAZ: yalnızca yetenek deftere yazılır. Kantine
ilk istek, bir modül gerçekten veri isteyene kadar atılmaz — cihaz kaydı da o
ana ertelenir. Tek istisna kantin olaylarının çekme yedeğidir (§6.1): döngü
çalışan olay döngüsüne görev olarak bırakılır, açılışı beklemez.
"""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .client import CanteenApi
from .events import CanteenEvents


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
        connect_timeout=float(ctx.config.get("connect_timeout_seconds") or 5),
    )

    ctx.provide("canteen.api", api)

    # Kantin → KM olayları (km-merkezi-senkron §6.2). Outbox platform
    # yeteneğidir; öğrenci kaydı Öğrenci Yönetimi'nindir (K5) — ikisi de
    # ÇAĞRI ANINDA çözülür (yükleme sırası önemsiz, yokluk modülü düşürmez).
    def outbox() -> Any:
        return ctx.try_capability("events.outbox")

    def students() -> Any:
        return ctx.try_capability("bbd_students.sync")

    events = CanteenEvents(store=ctx.store, canteen=api, log=ctx.log, outbox=outbox,
                           students=students)
    ctx.add_router(bind(events))
    box = outbox()
    if box is not None:
        # Kapanışta birleştirme tamponundaki bakiye olayları yazılır.
        box.on_close(events.close)
    if bool(ctx.config.get("events_pull", True)):
        events.start()
    ctx.log.info("kantin geçidi hazır", base_url=base_url)
