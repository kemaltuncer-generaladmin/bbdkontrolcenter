"""Rapor basma kapısı — 17 mağaza modülünde birebir kopya olan güvenlik denetimi.

ADR 0033 §7. GÜVENLİK: yalnız modülün KENDİ rapor klasöründeki dosya basılır.
Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
döktürmeye açık kapı bırakırdı. Denetim tek yerde durduğu için düzeltmesi de
tek yerde yapılır.

Rapor klasörü ÇAĞRI ANINDA ve yalnız gerektiğinde çözülür (`allowed_dir`
çağrılabilir): kopyalarda `self._export_dir` yazıcı yokken hiç okunmuyordu;
davranış korunur.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: Kopyalardaki metinle aynı — panel bu yazıları gösterir.
NO_PRINTER = "Yazıcı yeteneği bu kurulumda yok."
REPORT_MISSING = "Basılacak rapor bulunamadı."
OUTSIDE_FOLDER = "Bu dosya rapor klasöründe değil; güvenlik gereği basılmaz."


async def print_report_file(printer: Any, path: str, *, allowed_dir: Callable[[], Path],
                            copies: int = 1, fail: Callable[[Exception], str]) -> dict[str, Any]:
    """Üretilmiş raporu yazıcıya gönderir (`printer` platform yeteneği)."""
    if printer is None:
        return {"ok": False, "error": NO_PRINTER}
    try:
        resolved = Path(path).expanduser().resolve(strict=True)
    except OSError:
        return {"ok": False, "error": REPORT_MISSING}
    allowed = allowed_dir().resolve()
    if not str(resolved).startswith(str(allowed) + os.sep):
        return {"ok": False, "error": OUTSIDE_FOLDER}
    try:
        result = await printer.print_file(resolved, title=resolved.name,
                                          copies=max(1, min(20, int(copies))))
    except Exception as failure:  # noqa: BLE001 — yazıcı dışarısı
        return {"ok": False, "error": fail(failure)}
    return {"ok": True, **result, "name": resolved.name}


async def printer_status_of(printer: Any, *, fail: Callable[[Exception], str]) -> dict[str, Any]:
    """Yazıcının durumu; yetenek yoksa ya da patlarsa `ready: False` + neden (K7)."""
    if printer is None:
        return {"ready": False, "error": NO_PRINTER}
    try:
        result: dict[str, Any] = await printer.status()
    except Exception as failure:  # noqa: BLE001 — K7
        return {"ready": False, "error": fail(failure)}
    return result
