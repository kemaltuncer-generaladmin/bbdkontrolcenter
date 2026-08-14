"""Zamanlanmış aşama taraması — `module.yaml → tasks` bunu çağırır.

İKİ AŞAMANIN TETİKLEYİCİSİ BURADADIR: "siparişiniz alındı" ve "teslim edildi".
Üçüncüsü ("kargoya verildi") taramaya girmez; onun tetikleyicisi personelin
kendi eylemidir (`OrdersService.ship`).

ÇAĞRI BİÇİMİ SABİT DEĞİL. Çekirdeğin zamanlayıcısı henüz yazılmadı; şema
yalnızca `handler: 'backend.tasks:run_stage_sms'` biçimini sabitliyor, imzayı
değil. Bu yüzden koşucu HEM bağlamla HEM bağlamsız çağrıya dayanır: bağlam
gelirse ondan servis kurulur, gelmezse `register(ctx)` sırasında saklanan canlı
servis kullanılır. Yanlış imza yüzünden sessizce hiç çalışmayan bir tarama,
müşterinin hiç haber alamaması demek olurdu.

KURU PROVA VARSAYILAN AÇIK. Zamanlanmış iş kimsenin başında durmadığı iştir;
gerçek gönderim üç frenin üçünün de bilerek kapatılmasını ister
(`stage_sms_dry_run` · `lifecycle_sms_dry_run` · `platform.notify.sms.dry_run`).

HATA FIRLATMAZ: bir aşamanın patlaması diğerini durdurmaz (K7).
"""

from __future__ import annotations

from typing import Any

from . import stages
from .module import build_service, live

#: Zamanlanmış işin denetim izine yazılan aktör adı. Kullanıcı adı yazmak
#: yanlış olurdu: bu işi kimse tıklamadı.
ACTOR = "Zamanlanmış tarama"


async def run_stage_sms(ctx: Any = None) -> dict[str, Any]:
    """Tarama ile tetiklenen aşamaları sırayla koşar. İstisna sızdırmaz."""
    service = build_service(ctx) if ctx is not None and hasattr(ctx, "capability") else live()
    if service is None:
        return {"ok": False, "error": "Siparişler modülü hazır değil; aşama taraması atlandı.",
                "runs": []}

    runs: list[dict[str, Any]] = []
    for stage in stages.SWEEP_STAGES:
        try:
            # `dry_run=False` = "tetikleyicinin kendi freni kapalı". Gerçek SMS
            # yine de modül ayarı (`stage_sms_dry_run`) ve platform ayarı
            # kapanmadan çıkmaz; tarama kendi başına para harcayamaz.
            runs.append(await service.stage_sweep(stage=stage, actor=ACTOR, dry_run=False))
        except Exception as failure:  # noqa: BLE001 — biri patlarsa diğeri koşsun (K7)
            runs.append({"ok": False, "stage": stage, "error": str(failure), "results": []})
    return {"ok": any(run.get("ok") for run in runs), "error": "", "runs": runs}
