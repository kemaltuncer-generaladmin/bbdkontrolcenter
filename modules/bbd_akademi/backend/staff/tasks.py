"""Zamanlanmış personel eşitlemesi — `module.yaml → tasks` bunu ilan eder.

Çekirdeğin manifest görev koşucusu henüz yazılmadı (bkz. `store_orders`);
asıl tetikleyici `register(ctx)` içinde başlayan döngüdür
(`StaffService.start`). Bu işleyici aynı işi elle ya da gelecekteki koşucudan
çağrılabilir kılar; bağlamla da bağlamsız da çalışır ve hata fırlatmaz (K7).
"""

from __future__ import annotations

from typing import Any

from ..module import live


async def run_staff_sync(ctx: Any = None) -> dict[str, Any]:
    staff = live().get("staff")
    if staff is None:
        return {"ok": False, "error": "Akademi modülü hazır değil; eşitleme atlandı."}
    try:
        state = await staff.push(source="schedule")
    except Exception as failure:  # noqa: BLE001 — zamanlanmış iş çekirdeği düşürmez
        return {"ok": False, "error": str(failure)}
    return {"ok": state.get("status") in ("ok", "partial"), "state": state}
