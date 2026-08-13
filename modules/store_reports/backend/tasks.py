"""Zamanlanmış rapor koşucusu — `module.yaml → tasks` bunu çağırır.

ÇAĞRI BİÇİMİ SABİT DEĞİL. Çekirdeğin zamanlayıcısı henüz yazılmadı; şema
yalnızca `handler: 'backend.tasks:run_scheduled'` biçimini sabitliyor,
imzayı değil. Bu yüzden koşucu HEM bağlamla HEM bağlamsız çağrıya dayanır:
bağlam gelirse ondan servis kurulur, gelmezse `register(ctx)` sırasında
saklanan canlı servis kullanılır. Yanlış imza yüzünden sessizce hiç
çalışmayan bir zamanlanmış rapor, kullanıcının fark edemeyeceği bir arıza
olurdu.

İŞ SAATTE DEĞİL, SAATTEN SONRA ÇALIŞIR. Makine 07:00'de kapalıysa rapor
açılışta üretilir; "tam 07:00" şartı konsaydı hafta atlanırdı. Aynı gün iki
kez üretilmesini `run_due` engeller.
"""

from __future__ import annotations

from typing import Any

from .module import build_service, live


async def run_scheduled(ctx: Any = None) -> dict[str, Any]:
    """Vakti gelen zamanlanmış raporları üretir. Hata fırlatmaz."""
    service = build_service(ctx) if ctx is not None and hasattr(ctx, "capability") else live()
    if service is None:
        return {"ok": False, "error": "Raporlar modülü hazır değil; zamanlanmış iş atlandı.",
                "produced": 0}
    return await service.run_due()
