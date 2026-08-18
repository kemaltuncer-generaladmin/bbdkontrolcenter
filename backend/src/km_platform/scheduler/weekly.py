"""Zamanlayıcı yeteneği — haftalık zaman tablosuna göre tetikleme.

PLATFORM YETENEĞİDİR, MODÜL DEĞİL (ADR 0006).

TASARIM: cron değil, **haftalık tablo**. Zil sistemi "pazartesi 09:00" der;
takvim değişince tablo yeniden verilir, zamanlayıcı kendini yeniler.

ÜÇ ZOR DURUM ve çözümleri:

1. **Uyku / askıya alma.** Dizüstü kapağı kapanıp açıldığında saat ileri
   sıçrar. Döngü her uyanışta "kaçırdım mı" diye bakar; `grace_seconds`
   içindeki tetikleyici geç de olsa çalışır, daha eskisi ATLANIR — akşam
   açılan makinede sabahki zillerin arka arkaya çalması felakettir.
2. **Saat değişimi / yaz saati.** Uyku sadece `sleep(1)` ile değil, her
   turda duvar saatine bakarak ilerler; kayma kendini bir sonraki turda düzeltir.
3. **Çift tetikleme.** Her tetikleyicinin son çalışma damgası tutulur; aynı
   dakika içinde ikinci kez çalışmaz.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: Gün anahtarları — Ders Takvimi modülüyle aynı sıra (pazartesi=0).
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

#: Tetikleyici bu kadar geç kalırsa yine çalışır; fazlası atlanır.
DEFAULT_GRACE_SECONDS = 90

#: Döngünün uyanma sıklığı. Saniye hassasiyeti gerekmiyor; zil dakika bazlı.
TICK_SECONDS = 5.0


#: İşin saat dilimi. Ayarla değiştirilebilir (`platform.scheduler.timezone`).
#:
#: MAKİNENİN SAATİNE GÜVENİLMEZ — ölçülmüş bir arıza (18.08.2026). Eskiden
#: `datetime.now().astimezone()` kullanılıyordu, yani konteynerin duvar saati.
#: Çekirdek artık sunucuda koşuyor (ADR 0026) ve sunucu imajı ne `TZ` ne
#: `tzdata` taşıyor: konteyner UTC'de koşuyor. İstanbul UTC+3 olduğu için
#: 08:40'a kurulan zil, konteynerin saati 08:40 olunca — yani İSTANBUL'DA
#: 11:40'ta — çalıyordu. Belirti "zil çalmıyor"du; elle çalma anlık olduğu
#: için sorunsuz görünüyor ve arızayı gizliyordu.
DEFAULT_TIMEZONE = "Europe/Istanbul"


def business_zone(name: str = DEFAULT_TIMEZONE) -> tzinfo:
    """Adı verilen saat dilimi; tanınmazsa makinenin yereli.

    Yedeğe düşmek SESSİZ OLMAZ: çağıran uyarıyı günlüğe yazar. Sessiz bir
    yedek, tam da kaçtığımız arızayı geri getirirdi.
    """
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now().astimezone().tzinfo or UTC


def now_local(zone: tzinfo | None = None) -> datetime:
    """İŞİN DUVAR SAATİ, saat dilimi bilgisiyle.

    Zil "pazartesi 09:00"da çalar — bu okulun saatidir, sunucunun değil.
    Saat dilimi bilgisi iliştirilir ki karşılaştırmalar açık olsun ve yaz saati
    geçişinde `weekday()`/`hour` kullanıcının gördüğü saati versin.
    """
    return datetime.now(zone or business_zone())


@dataclass(slots=True)
class Trigger:
    """Tek bir tetikleyici: hangi gün, hangi saat, hangi etiketle."""

    day: str                      # 'mon' … 'sun'
    time: str                     # 'HH:MM'
    label: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def minutes(self) -> int | None:
        """Gün içi dakika — geçersiz saat `None`."""
        try:
            hour_text, minute_text = self.time.split(":")
            hour, minute = int(hour_text), int(minute_text)
        except (ValueError, AttributeError):
            return None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        return hour * 60 + minute

    def key(self) -> str:
        return f"{self.day}|{self.time}|{self.label}"


class WeeklyScheduler:
    """`scheduler` yeteneğinin uygulaması.

    Kullanım (modül tarafı):
        scheduler.set_plan("bell", triggers, handler)
        scheduler.clear("bell")
    """

    def __init__(self, log: Any, *, grace_seconds: int = DEFAULT_GRACE_SECONDS,
                 timezone: str = DEFAULT_TIMEZONE) -> None:
        self._log = log
        self._grace = grace_seconds
        self._zone_name = str(timezone or DEFAULT_TIMEZONE)
        self._zone = business_zone(self._zone_name)
        # TANINMAYAN AD SESSİZ GEÇMEZ: yedeğe düşmek, zilin yanlış saatte
        # çalması demektir ve bu ekranda okunabilmeli.
        if str(getattr(self._zone, "key", "")) != self._zone_name:
            log.warning("zamanlayıcı saat dilimi tanınmadı, makinenin yereli kullanılıyor",
                        istenen=self._zone_name)
        self._plans: dict[str, tuple[list[Trigger], Callable[[Trigger], Awaitable[None]]]] = {}
        self._fired: dict[str, str] = {}      # tetikleyici anahtarı → 'YYYY-MM-DD HH:MM'
        self._task: asyncio.Task[None] | None = None
        self._started_at: datetime | None = None

    # ------------------------------------------------------------ yaşam

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._started_at = now_local(self._zone)
        self._task = asyncio.create_task(self._loop(), name="km-scheduler")
        self._log.info("zamanlayıcı başladı", grace=self._grace)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        self._log.info("zamanlayıcı durdu")

    # ------------------------------------------------------------- plan

    def set_plan(self, owner: str, triggers: list[Trigger],
                 handler: Callable[[Trigger], Awaitable[None]]) -> int:
        """Sahibin tüm planını DEĞİŞTİRİR. Geçersiz saatler atılır."""
        valid = [item for item in triggers if item.minutes() is not None and item.day in DAYS]
        self._plans[owner] = (valid, handler)
        dropped = len(triggers) - len(valid)
        if dropped:
            self._log.warning("geçersiz tetikleyici atlandı", owner=owner, count=dropped)
        self._log.info("plan güncellendi", owner=owner, triggers=len(valid))
        return len(valid)

    def clear(self, owner: str) -> None:
        self._plans.pop(owner, None)

    def state(self) -> dict[str, Any]:
        """Ekranın "çalışıyor mu, sırada ne var" sorusuna cevabı."""
        now = now_local(self._zone)
        upcoming = self.next_triggers(now, limit=5)
        return {
            "running": self._task is not None and not self._task.done(),
            "startedAt": self._started_at.isoformat(timespec="seconds") if self._started_at else None,
            "owners": {owner: len(items) for owner, (items, _) in self._plans.items()},
            "graceSeconds": self._grace,
            # EKRAN HANGİ SAATE GÖRE ÇALDIĞINI YAZMALI. Saat dilimi sessiz
            # kaldığında yanlış saatte çalan zil "hiç çalmıyor" gibi okunuyordu.
            "timezone": self._zone_name,
            "now": now.isoformat(timespec="seconds"),
            "next": upcoming,
        }

    def next_triggers(self, now: datetime | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
        """Sıradaki tetikleyiciler — hafta döngüsünde ileri doğru aranır."""
        now = now or now_local(self._zone)
        found: list[tuple[datetime, str, Trigger]] = []

        for owner, (triggers, _) in self._plans.items():
            for trigger in triggers:
                minutes = trigger.minutes()
                if minutes is None:
                    continue
                target_day = DAYS.index(trigger.day)
                for ahead in range(8):
                    candidate = (now + timedelta(days=ahead)).replace(
                        hour=minutes // 60, minute=minutes % 60, second=0, microsecond=0)
                    if candidate.weekday() != target_day or candidate <= now:
                        continue
                    found.append((candidate, owner, trigger))
                    break

        found.sort(key=lambda item: item[0])
        return [
            {
                "at": when.isoformat(timespec="minutes"),
                "inMinutes": int((when - now).total_seconds() // 60),
                "owner": owner,
                "day": trigger.day,
                "time": trigger.time,
                "label": trigger.label,
                "payload": trigger.payload,
            }
            for when, owner, trigger in found[:limit]
        ]

    # ------------------------------------------------------------ döngü

    async def _loop(self) -> None:
        while True:
            try:
                await self._tick(now_local(self._zone))
            except asyncio.CancelledError:
                raise
            except Exception as failure:  # noqa: BLE001 — döngü ASLA ölmemeli (K7)
                self._log.error("zamanlayıcı turu patladı", error=str(failure))
            await asyncio.sleep(TICK_SECONDS)

    async def _tick(self, now: datetime) -> None:
        day = DAYS[now.weekday()]
        stamp = now.strftime("%Y-%m-%d %H:%M")
        now_minutes = now.hour * 60 + now.minute

        for owner, (triggers, handler) in list(self._plans.items()):
            for trigger in triggers:
                if trigger.day != day:
                    continue
                minutes = trigger.minutes()
                if minutes is None:
                    continue

                # Gecikme: uyanışta kaçırılmış olabilir.
                late = (now_minutes - minutes) * 60 + now.second
                if late < 0 or late > self._grace:
                    continue

                key = f"{owner}|{trigger.key()}"
                if self._fired.get(key) == stamp:
                    continue
                self._fired[key] = stamp

                try:
                    await handler(trigger)
                except Exception as failure:  # noqa: BLE001 — bir tetikleyici diğerini düşürmez
                    self._log.error("tetikleyici patladı", owner=owner,
                                    label=trigger.label, error=str(failure))

        # Damga defteri sınırsız büyümesin: bugünden eski kayıtlar atılır.
        today = now.strftime("%Y-%m-%d")
        if len(self._fired) > 512:
            self._fired = {key: value for key, value in self._fired.items()
                           if value.startswith(today)}
