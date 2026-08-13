"""Ders Takvimi — iş kuralları.

Haftalık ders/teneffüs saatleri. Zil sistemi bu saatleri yetenek üzerinden
okur (K3) — kendi takvimini tutmaz.

VERİ TEK BELGE. Gruplar birlikte anlam taşır ve panel tümünü birden yazar;
satır satır normalize etmek kazanç sağlamaz. Belge her yazımda doğrulanır:
elle bozulmuş veri ekranı düşürmesin.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

#: Panelle aynı gün anahtarları (hafta içi). Zamanlayıcı da bu adları kullanır.
DAYS = ["mon", "tue", "wed", "thu", "fri"]

#: Grup renkleri — panelin sırayla dağıttığı palet.
COLORS = ["#5b8cff", "#12b5a4", "#f0883a", "#a86bff", "#e8567a", "#3ea55a"]

NAME_MAX = 40


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clock(value: Any) -> str | None:
    """'HH:MM' doğrular. Geçersizse `None`."""
    try:
        hour, minute = str(value).split(":")
        hour, minute = int(hour), int(minute)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour < 24 and 0 <= minute < 60):
        return None
    return f"{hour:02d}:{minute:02d}"


class ScheduleService:
    def __init__(self, *, store: Any, log: Any) -> None:
        self._store = store
        self._log = log
        self._table = store.table("document")

    async def read(self) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._table} WHERE id = 1")
        if row is None:
            return {"document": self._default(), "updatedAt": "", "updatedBy": "", "empty": True}
        try:
            payload = json.loads(str(row["payload"]))
        except json.JSONDecodeError:
            self._log.warning("takvim belgesi çözülemedi, varsayılana dönülüyor")
            payload = self._default()
        return {
            "document": self.normalize(payload),
            "updatedAt": str(row["updated_at"]),
            "updatedBy": str(row["updated_by"]),
            "empty": False,
        }

    async def write(self, document: dict[str, Any], *, actor: str) -> dict[str, Any]:
        clean = self.normalize(document)
        await self._store.execute(
            f"INSERT INTO {self._table} (id, payload, updated_at, updated_by) "
            f"VALUES (1, ?, ?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
            f"updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (json.dumps(clean, ensure_ascii=False), _now(), actor),
        )
        return {"ok": True, "document": clean, "updatedAt": _now(), "updatedBy": actor}

    async def adopt(self, document: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Tarayıcı belleğindeki eski veriyi bir kez içeri alır.

        YALNIZCA BOŞSA yazar: kalıcı veri varsa localStorage'ın üstüne yazmasına
        izin verilmez, yoksa iki makinede açılan panel birbirinin verisini ezer.
        """
        current = await self.read()
        if not current["empty"]:
            return {"ok": False, "adopted": False, "reason": "already_present",
                    "document": current["document"]}
        result = await self.write(document, actor=actor)
        self._log.info("takvim tarayıcı belleğinden alındı", actor=actor)
        return {"ok": True, "adopted": True, **result}

    # ---------------------------------------------------------- doğrulama

    @staticmethod
    def _default() -> dict[str, Any]:
        return {"version": 1, "groups": [
            {"id": "genel", "name": "Genel", "color": COLORS[0], "students": [],
             "week": {day: [] for day in DAYS}},
        ]}

    def normalize(self, state: Any) -> dict[str, Any]:
        """Eksik/bozuk alanları tamamlar — panelin `normalize()`'ıyla aynı sözleşme."""
        if not isinstance(state, dict) or not isinstance(state.get("groups"), list) \
                or not state["groups"]:
            return self._default()

        groups = []
        for index, group in enumerate(state["groups"][:60]):
            if not isinstance(group, dict):
                continue
            week: dict[str, list[dict[str, Any]]] = {day: [] for day in DAYS}
            source = group.get("week") if isinstance(group.get("week"), dict) else {}
            for day in DAYS:
                blocks = source.get(day)
                if not isinstance(blocks, list):
                    continue
                for block in blocks[:40]:
                    if not isinstance(block, dict):
                        continue
                    start, end = _clock(block.get("start")), _clock(block.get("end"))
                    if start is None or end is None:
                        continue
                    week[day].append({
                        "id": str(block.get("id") or f"{day}-{start}"),
                        "start": start,
                        "end": end,
                        "name": str(block.get("name") or "")[:NAME_MAX].strip(),
                    })
                week[day].sort(key=lambda item: item["start"])

            groups.append({
                "id": str(group.get("id") or f"grup-{index + 1}"),
                "name": str(group.get("name") or "Grup")[:60],
                "color": str(group.get("color") or COLORS[index % len(COLORS)]),
                "students": [str(item) for item in (group.get("students") or [])][:2000],
                "week": week,
            })

        return {"version": 1, "groups": groups or self._default()["groups"]}

    # --------------------------------------------------------- yetenek

    async def week(self) -> list[dict[str, Any]]:
        """`bbd_class_schedule.week` yeteneğinin verdiği özet — salt okunur.

        Zil sistemi bunu tüketir; kendi takvimini tutmaz (K3/K5).
        """
        current = await self.read()
        return current["document"]["groups"]
