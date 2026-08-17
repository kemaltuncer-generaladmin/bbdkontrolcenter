"""Zamanlanmış tam tarama — `module.yaml → tasks.scheduled_scan` bunu çağırır.

ÇAĞRI BİÇİMİ SABİT DEĞİL. Çekirdeğin görev koşucusu manifestteki `handler`
biçimini sabitliyor, İMZAYI değil. Bu yüzden koşucu hem bağlamla hem bağlamsız
çağrıya dayanır: bağlam gelirse ondan servis kurulur, gelmezse `register(ctx)`
sırasında saklanan canlı servis kullanılır. Yanlış imza yüzünden sessizce hiç
koşmayan bir gece taraması, taranmadığını kimsenin bilmediği bir makine
demektir.

HATA FIRLATMAZ (K7): zamanlanmış işin patlaması zamanlayıcıyı düşürmez.

SONUÇ BEKLENİR. Elle başlatmadan farkı budur: ekran taramayı başlatıp
ilerlemeyi yoklar, zamanlanmış iş ise sonucu görmek zorundadır — koşunun
"başladı" demesi, taramanın yapıldığı anlamına gelmez.
"""

from __future__ import annotations

from typing import Any

from ..module import build_service, live


async def run_scheduled(ctx: Any = None) -> dict[str, Any]:
    """Gecelik tam taramayı koşar. İstisna sızdırmaz."""
    service = build_service(ctx) if ctx is not None and hasattr(ctx, "capability") else live()
    if service is None:
        return {"ok": False, "started": False,
                "error": "Antivirüs modülü hazır değil; zamanlanmış tarama atlandı."}
    try:
        return await service.scheduled_scan()
    except Exception as failure:  # noqa: BLE001 — modül sınırı (K7)
        return {"ok": False, "started": False, "error": f"Zamanlanmış tarama patladı: {failure}"}
