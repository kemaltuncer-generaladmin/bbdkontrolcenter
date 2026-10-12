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

from km_sdk import ModuleContext, Trigger

from .announce import BellAnnounce
from .api.routes import bind
from .bridge import BellBridge
from .service import DAYS, BellService
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
        page_tts_timeout=number(_section(config, "page").get("tts_timeout_seconds"), 20),
    )

    ctx.add_router(bind(service))
    # Ders Takvimi ekranı saatleri ve grupları BURADAN okur; kopyalamaz (K3).
    ctx.provide("bell.week", service.week)
    # Öğrenci çağırma — mobil geçit ve benzeri tüketiciler (sözleşme §5).
    ctx.provide("bell.announce", BellAnnounce(service))

    _plan_page_preparation(ctx, service, scheduler)

    asyncio.get_running_loop().create_task(_bootstrap(service, ctx.log))
    ctx.log.info("zil sistemi hazır")


def _plan_page_preparation(ctx: ModuleContext, service: BellService, scheduler: Any) -> None:
    """Gece: dizindeki öğrencilerin çağrı seslerini önden hazırla (İSTEĞE BAĞLI).

    Varsayılan KAPALIDIR (`page.prepare_nightly: false`): ilk gece okulun
    bütün öğrencileri için Vertex çağrısı demektir. Açılınca yalnız yeni ya
    da adı değişen öğrenciler üretilir (önbellek metin özetine bağlı).

    Öğrenci dizini `bbd_students.directory` yeteneğinden, TETİKLEME ANINDA
    çözülür (isteğe bağlı tüketim): modül yükleme sırası önemli olmasın.
    """
    section = _section(ctx.config, "page")
    if not bool(section.get("prepare_nightly", False)):
        return
    at = str(section.get("prepare_time") or "03:15")

    async def handler(_trigger: Any) -> None:
        directory = ctx.try_capability("bbd_students.directory")
        if directory is None:
            ctx.log.info("çağrı sesi hazırlığı atlandı — öğrenci dizini yok")
            return
        try:
            await service.prepare_students(directory)
        except Exception as failure:  # noqa: BLE001 — gece görevi çekirdeği düşürmez (K7)
            ctx.log.warning("çağrı sesi hazırlığı patladı", error=str(failure))

    scheduler.set_plan("bell-page", [
        Trigger(day=day, time=at, label="öğrenci çağrı sesleri", payload={"phase": "page"})
        for day in DAYS
    ], handler)


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
