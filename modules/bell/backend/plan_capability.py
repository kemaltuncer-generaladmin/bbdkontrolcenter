"""`bell.plan` yeteneği — Akademi'den gelen zil planı (sözleşme §3.3).

    await apply_days(body, *, actor) → {"days": [{date, result, revision
                                          [, error, details]}],
                                        "voicesPending": int, "bellEnabled": bool}
        `body` = `PUT /bell/plan` gövdesi ({from, to, days}). `from`–`to`
        doğrulaması (en çok 15 gün, aralıktaki HER gün) gövde bütünüyle
        yapılır; bu yüzden yalnız gün listesi değil gövdenin tamamı alınır.
        `result` ∈ applied | unchanged | stale | overridden | rejected;
        `error` ∈ revision_conflict | unknown_profile (rejected günlerde).
    await status(from_date, to_date) → {bellEnabled, sessionCallsEnabled,
                                        agent: {online, lastSeenAt}, days: [...]}
    await override_day(date, mode="off", *, actor) → {"ok", "days"}   (yalnız bugün)
    await clear_override(date, *, actor) → {"ok", "days"}

Yapı hatası `ValueError` alt sınıfıyla yükselir; çağıran sınıfı import edemez
(K3), `getattr(hata, "code")` (`validation_error` | `override_today_only`),
`getattr(hata, "status")` (HTTP karşılığı) ve `getattr(hata, "details")`
(`{"path": …}`) ile ayırt eder.

ÇALMA YOLU AĞ BEKLEMEZ: plan yerel tablolara yazılır; tetik çalma anında
Akademi'ye ya da `bbd_students`'a gitmez.
"""

from __future__ import annotations

from typing import Any

from .service import BellService


class BellPlan:
    def __init__(self, service: BellService) -> None:
        self._service = service

    async def apply_days(self, body: Any, *, actor: str) -> dict[str, Any]:
        return await self._service.apply_days(body, actor=actor)

    async def status(self, from_date: Any, to_date: Any) -> dict[str, Any]:
        return await self._service.plan_status(from_date, to_date)

    async def override_day(self, day: Any, mode: str = "off", *,
                           actor: str) -> dict[str, Any]:
        return await self._service.override_day(day, mode, actor=actor)

    async def clear_override(self, day: Any, *, actor: str) -> dict[str, Any]:
        return await self._service.clear_override(day, actor=actor)
