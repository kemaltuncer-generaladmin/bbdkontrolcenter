"""Ders Takvimi — salt okunur ayna.

BU MODÜLÜN VERİSİ YOKTUR. Haftalık zil saatleri ve gruplar Zil Sistemi'nindir;
buradan yalnız `bell.week` yeteneği üzerinden okunur (K3/K5).

Yazma yolu BİLEREK yoktur. "Şimdilik gizli bir düzenleme ucu dursun" demek,
iki ekranda iki ayrı doğru kaynak yaratmak demektir — bu modülün 0.1'de
yaşadığı sorun tam olarak buydu.

Kaynak yoksa (Zil Sistemi silinmiş ya da düşmüş) ekran BOŞ DÖNMEZ, nedenini
söyler. Boş bir takvim ile ulaşılamayan bir takvim kullanıcı için aynı şey
değildir.
"""

from __future__ import annotations

from typing import Any

#: Panelle aynı gün anahtarları. Zil Sistemi de bu adları kullanır.
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


class ScheduleService:
    def __init__(self, *, log: Any, read_week: Any = None) -> None:
        self._log = log
        self._read_week = read_week

    async def read(self) -> dict[str, Any]:
        """Zil Sistemi'nin haftalık planı. Okunamazsa nedeni yanında gelir."""
        empty: dict[str, Any] = {
            "times": {day: [] for day in DAYS},
            "groups": [],
            "source": "bell",
            "available": False,
            "reason": "",
        }

        if self._read_week is None:
            empty["reason"] = (
                "Zil Sistemi modülü çözümlenemedi. Haftalık saatler orada tutulur; "
                "modül olmadan gösterilecek bir plan yok."
            )
            return empty

        try:
            result = self._read_week()
            payload = await result if hasattr(result, "__await__") else result
        except Exception as failure:  # noqa: BLE001 — zil modülü dışarısı (K7)
            self._log.warning("zil saatleri okunamadı", error=str(failure))
            empty["reason"] = f"Zil Sistemi'nden okunamadı: {failure}"
            return empty

        if not isinstance(payload, dict):
            empty["reason"] = "Zil Sistemi beklenmedik bir yanıt verdi."
            return empty

        times = payload.get("times") if isinstance(payload.get("times"), dict) else {}
        groups = payload.get("groups") if isinstance(payload.get("groups"), list) else []

        return {
            "times": {day: list(times.get(day) or []) for day in DAYS},
            "groups": list(groups),
            "settings": payload.get("settings") or {},
            "source": "bell",
            "available": True,
            "reason": "",
        }
