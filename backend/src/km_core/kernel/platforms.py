"""Modül platform kapsamı (ADR 0022).

Her modül her platformda anlamlı değil. Hangi platformlarda çalışabildiğini
modül kendi manifestinde `platforms:` ile ilan eder; çekirdek bunu **veri**
olarak okur. Modül adı geçmez, modüle özel dal yoktur (K1).

**Alan yoksa modül her platformda yüklenir.** Varsayılan bilinçli olarak
"her yerde"dir: aksi hâlde bu yetenek eklendiği anda alanı olmayan mevcut
manifestlerin hepsi kapanırdı.

Eleme keşif aşamasında yapılır — yarı yüklü bir modül, olmayan bir modülden
kötüdür (ADR 0022 §2).
"""

from __future__ import annotations

import sys
from typing import Any

#: Manifestte geçerli olan platform adları. Şemadaki `platforms` listesiyle
#: aynı kümedir; şema tek kaynaktır, burası yalnız çalışma anındaki karşılığı.
PLATFORMS = ("linux", "windows", "macos")


def current_platform() -> str:
    """Çalışılan platformun manifestteki karşılığı.

    `sys.platform` değerleri manifest sözlüğüne çevrilir: `win32` → `windows`,
    `darwin` → `macos`, geri kalanı `linux`. Tanınmayan bir Unix türevi
    `linux` sayılır; bu, elemenin varsayılanını "yükle" tarafında tutar.
    """
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def declared_platforms(manifest_data: dict[str, Any]) -> list[str]:
    """Manifestte ilan edilen platformlar. Alan yoksa boş liste = kısıt yok."""
    declared = manifest_data.get("platforms")
    if not isinstance(declared, list):
        return []
    return [str(entry) for entry in declared]


def runs_on(manifest_data: dict[str, Any], platform: str) -> bool:
    """Modül bu platformda çalışır mı? İlan yoksa yanıt her zaman evettir."""
    declared = declared_platforms(manifest_data)
    return not declared or platform in declared


def skip_note(module_id: str, manifest_data: dict[str, Any]) -> str:
    """Elenen modülün açılış günlüğüne düşen satırı.

    Eleme `enabled: false` ile aynı sessizlikte olmaz: ekranını arayan yönetici
    nedenini günlükte bulur (ADR 0022 §2).
    """
    declared = ", ".join(declared_platforms(manifest_data))
    return f"{module_id} — bu platformda çalışmaz ({declared})"
