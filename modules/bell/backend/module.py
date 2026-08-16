"""Zil Sistemi — giriş noktası.

`register(ctx)` BİLDİRİM AŞAMASIDIR: ağa çıkılmaz, kasadan sır okunmaz, ses
üretilmez. Yalnız nesneler kurulur ve deftere yazılır. Açılışta yapılması
gereken iş (eksik seslerin sıraya alınması, tetikleyicilerin kurulması, ajan
ses kitaplığının eşitlenmesi) çalışan döngüye görev olarak bırakılır; çekirdek
açılışı bunları beklemez.

Zamanlayıcı zaten modüller planlarını kurduktan SONRA başlıyor (bkz.
`km_core/http/app.py` lifespan) — ilk tur dolu tabloyla döner.
"""

from __future__ import annotations

import asyncio
from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .bridge import BellBridge
from .service import BellService
from .speech import VertexSpeech
from .voices import VoiceLibrary, number


def register(ctx: ModuleContext) -> None:
    config = ctx.config

    # Ses ve zamanlayıcı olmadan zil çalamaz; ikisi de zorunlu yetenek.
    audio = ctx.capability("audio")
    scheduler = ctx.capability("scheduler")
    secrets = ctx.capability("secrets")
    notify = ctx.try_capability("notify")

    speech = VertexSpeech(
        secrets=secrets,
        config=_section(config, "vertex"),
        log=ctx.log,
    )
    voices = VoiceLibrary(
        store=ctx.store,
        log=ctx.log,
        speech=speech,
        sounds_path=audio.available().get("soundsPath") or "data/sounds",
        config=_section(config, "vertex"),
    )
    bridge_config = _section(config, "bridge")
    bridge = BellBridge(
        secrets=secrets,
        log=ctx.log,
        base_url=str(bridge_config.get("base_url") or "https://bbdstore.com.tr"),
        timeout=number(bridge_config.get("timeout_seconds"), 20),
        # 0 geçerli: "tam saatinde gönder". `or` ile yazılsaydı 60'a dönerdi.
        lead_seconds=int(number(bridge_config.get("lead_seconds"), 60)),
    )

    service = BellService(
        store=ctx.store,
        log=ctx.log,
        audio=audio,
        scheduler=scheduler,
        voices=voices,
        bridge=bridge,
        notify=notify,
        play_locally=bool(config.get("play_locally", True)),
    )

    ctx.add_router(bind(service))
    # Ders Takvimi ekranı saatleri ve grupları BURADAN okur; kopyalamaz (K3).
    ctx.provide("bell.week", service.week)

    asyncio.get_running_loop().create_task(_bootstrap(service, ctx.log))
    ctx.log.info("zil sistemi hazır")


async def _bootstrap(service: BellService, log: Any) -> None:
    try:
        result = await service.bootstrap()
        log.info("zil tetikleyicileri kuruldu", triggers=result.get("triggers"))
        # Ajanın ses kitaplığını taze tutan döngü; kendini onarır (K7).
        service.start_sync()
    except Exception as failure:  # noqa: BLE001 — açılış görevi çekirdeği düşürmez (K7)
        log.error("zil açılış görevi patladı", error=str(failure))


def _section(config: dict[str, Any], name: str) -> dict[str, Any]:
    value = config.get(name)
    return value if isinstance(value, dict) else {}
