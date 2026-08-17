"""Antivirüs — giriş noktası.

`register(ctx)` BİLDİRİM AŞAMASIDIR: tarama başlatılmaz, clamd'e bağlanılmaz,
imza indirilmez. Yalnız nesneler kurulur, yetenek deftere yazılır, router ve
tarama takvimi bildirilir. ClamAV kurulu olmasa bile modül yüklenir ve ekran
durumu anlatır (K7) — "kurulu değil" bir arıza değil, bir durumdur.

İKİ ZAMANLANMIŞ İŞ (module.yaml → tasks):
  · `backend.tasks.scan:run_scheduled`      — gecelik tam tarama
  · `backend.tasks.signatures:check`        — saatlik imza yaşı denetimi

Bu iki dosya işin TEK giriş noktasıdır; buradaki kurulum da onları çağırır,
kendi kopyasını çalıştırmaz. Aksi hâlde manifestte yazan handler ile gerçekte
koşan kod ayrışır ve manifest yalan söylemeye başlar.

TAKVİM ZAMANLAYICI YETENEĞİNE KURULUR. Manifestteki cron ifadesi haftalık
tetikleyicilere çevrilir (`service.cron_to_triggers`); zamanlayıcı yeteneği
cron değil haftalık tablo üzerine kuruludur (`km_platform/scheduler`).

İMZA DENETİMİ KENDİ DÖNGÜSÜNDE. Saatlik iş, haftalık tabloya 168 tetikleyici
yazmak yerine basit bir uyku döngüsüyle koşar; döngü kendini onarır (K7).

`notify`, `ssh` ve `secrets` manifestte İSTEĞE BAĞLI ilan edilmiştir ve bu
sürümde ÇÖZÜLMEZ: uzak sunucuda tarama ve SMS uyarısı henüz yok. Bulaşma
haberi `antivirus.threat_found` olayıyla veri yoluna düşer; bildirim ona
bağlanır (ADR 0009). Kullanılmayan bir yeteneği çözmek, ekrana yansımayan
sahte bir bağımlılık olurdu.
"""

from __future__ import annotations

import asyncio
from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .engine import ClamAvEngine
from .service import AntivirusService

#: İmza yaşı denetiminin sıklığı — manifestteki `0 * * * *` ile aynı anlam.
SIGNATURE_INTERVAL = 3600.0

#: Açılışta ilk denetim biraz beklenir: çekirdek daha yeni ayağa kalktı,
#: freshclam ilk indirmesini sürdürüyor olabilir.
FIRST_CHECK_DELAY = 30.0


class ScanProvider:
    """`antivirus.scan` yeteneğinin gövdesi (module.yaml → provides).

    SALT OKUR + BAŞLAT. Yetenek dışarıya yalnız "tara" ve "son sonuç" verir;
    ayar yazan, kayıt silen bir yordam dışa vurulmaz. Tüketici çıktığında
    (yükleme taraması, yedek taraması) motor istemcisi km_platform altına
    yükseltilecek — bu yüzden yüzey bilerek küçük tutuldu (ADR 0009).
    """

    def __init__(self, service: AntivirusService) -> None:
        self._service = service

    async def scan(self, kind: str = "quick", *, actor: str = "") -> dict[str, Any]:
        return await self._service.start(kind, actor=actor)

    async def last(self) -> dict[str, Any] | None:
        return await self._service.last()

    async def status(self) -> dict[str, Any]:
        return await self._service.engine_status()


#: Zamanlanmış görevlerin ulaşabilmesi için canlı servis burada tutulur.
#: `module.yaml → tasks` handler'ı çekirdek tarafından bağlamsız da
#: çağrılabiliyor; o durumda tek tutamak budur.
_LIVE: AntivirusService | None = None


def build_service(ctx: ModuleContext) -> AntivirusService:
    return AntivirusService(
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        engine=ClamAvEngine(config=ctx.config, log=ctx.log),
        publish=ctx.publish,
    )


def live() -> AntivirusService | None:
    return _LIVE


def register(ctx: ModuleContext) -> None:
    global _LIVE

    # Zamanlayıcı olmadan gecelik tarama kurulamaz; manifestte zorunlu ilan
    # edilmiştir, yoksa modül zaten devre dışı bırakılır.
    scheduler = ctx.capability("scheduler")

    service = build_service(ctx)
    _LIVE = service

    ctx.provide("antivirus.scan", ScanProvider(service))
    ctx.add_router(bind(service))

    triggers = service.triggers()
    scheduler.set_plan("antivirus", triggers, _on_trigger)

    asyncio.get_running_loop().create_task(
        _signature_loop(ctx.log), name="antivirus-signatures")
    ctx.log.info("antivirüs hazır", schedule=service.schedule, triggers=len(triggers))


async def _on_trigger(trigger: Any) -> None:
    """Zamanlayıcı tetikledi — manifestteki handler'ı çağırır.

    Import GÖVDEDE: `tasks/scan.py` bu dosyayı içe aktarıyor; tepede yazmak
    döngüsel import olurdu. Tetikleyici seyrek koşar, maliyeti yok.
    """
    from .tasks import scan as scan_task

    await scan_task.run_scheduled()


async def _signature_loop(log: Any) -> None:
    """Saatlik imza yaşı denetimi. Döngü ASLA ölmez (K7)."""
    from .tasks import signatures as signature_task

    await asyncio.sleep(FIRST_CHECK_DELAY)
    while True:
        try:
            await signature_task.check()
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 — bir tur patlarsa sonraki koşar
            log.error("imza denetimi patladı", error=str(failure))
        await asyncio.sleep(SIGNATURE_INTERVAL)
