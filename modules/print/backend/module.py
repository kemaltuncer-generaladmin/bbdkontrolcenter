"""Çıktı Merkezi — giriş noktası.

`register(ctx)` BİLDİRİM AŞAMASIDIR: yazıcıya sorulmaz, dosya taranmaz, kayıt
budanmaz. Yalnız servis kurulur ve router deftere yazılır. Budama (ADR 0019 §5)
açılışı bekletmemek için çalışan döngüye görev olarak bırakılır.

YAZICI İSTEĞE BAĞLIDIR. Yetenek yoksa modül yine yüklenir: liste, süzgeç ve
önizleme çalışır, yalnız yeniden bas düğmesi nedeniyle birlikte kapanır (K7).
"""

from __future__ import annotations

import asyncio
from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .service import OutputsService


def register(ctx: ModuleContext) -> None:
    service = OutputsService(
        store=ctx.store,
        log=ctx.log,
        printer=ctx.try_capability("printer"),
        config=ctx.config,
    )
    ctx.add_router(bind(service))

    asyncio.get_running_loop().create_task(_bootstrap(service, ctx.log))
    ctx.log.info("çıktı merkezi hazır", keep_days=service.keep_days)


async def _bootstrap(service: OutputsService, log: Any) -> None:
    try:
        result = await service.prune()
        log.info("çıktı kaydı budandı", removed=result["removed"],
                 keep_days=result["keepDays"])
    except Exception as failure:  # noqa: BLE001 — açılış görevi çekirdeği düşürmez (K7)
        log.error("çıktı kaydı budanamadı", error=str(failure))
