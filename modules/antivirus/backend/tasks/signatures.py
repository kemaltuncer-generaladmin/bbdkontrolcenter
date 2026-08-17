"""Saatlik imza yaşı denetimi — `module.yaml → tasks.signature_freshness`.

YALNIZCA OKUR. `freshclam` buradan çalıştırılmaz: imzaları
`clamav-freshclam` servisi güncelliyor ve uygulama içinden ikinci bir freshclam
başlatmak kilit çakışması yaratır (ADR 0009 §5). Bu iş imza dosyasının yaşına
bakar, eşik aşılmışsa `antivirus.signatures_stale` yayınlar.

Olay duruma GİRİŞTE yayınlanır, sonra en fazla günde bir tekrarlanır; kararın
kendisi serviste (`AntivirusService._should_notify`). Saatlik bir iş her turda
olay yayınlasaydı, bildirim kanalı ilk günün sonunda kullanılamaz hâle gelirdi.

HATA FIRLATMAZ (K7).
"""

from __future__ import annotations

from typing import Any

from ..module import build_service, live


async def check(ctx: Any = None) -> dict[str, Any]:
    """İmza yaşını okur ve gerekiyorsa olay yayınlar. İstisna sızdırmaz."""
    service = build_service(ctx) if ctx is not None and hasattr(ctx, "capability") else live()
    if service is None:
        return {"ok": False, "error": "Antivirüs modülü hazır değil; imza denetimi atlandı."}
    try:
        return await service.check_signatures()
    except Exception as failure:  # noqa: BLE001 — modül sınırı (K7)
        return {"ok": False, "error": f"İmza denetimi patladı: {failure}"}
