"""Zil Sistemi — iş kuralları.

ZİL GERÇEKTEN ÇALAR. Ders saatleri Ders Takvimi modülünden yetenek üzerinden
okunur (K3), ses `audio` platform yeteneğinden çıkar, tetikleme `scheduler`
platform yeteneğinden gelir. Bu modül yalnız kararı verir: hangi grup, hangi
kenarda (ders başı/sonu), hangi sesi, hangi düzeyde.

SESSİZ ARIZA YOKTUR. Her çalma denemesi günlüğe yazılır; çalamadıysa nedeniyle.
Çalmayan bir zil, fark edilmeyen bir arızadır.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from km_sdk import Trigger

DEFAULT_SOUND = "classic_electric"
DEFAULT_VOLUME = 85


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _default_group() -> dict[str, Any]:
    return {
        "enabled": True,
        "soundId": DEFAULT_SOUND,
        "volume": DEFAULT_VOLUME,
        "ringStart": True,
        "ringEnd": True,
        # Ders bazlı istisnalar. Anahtar "<gün>|<başlangıç>".
        # Değer: {start: false} / {end: false} → o kenarı sustur; {soundId} → farklı ses.
        "overrides": {},
    }


class BellService:
    def __init__(self, *, store: Any, log: Any, audio: Any, scheduler: Any,
                 schedule_reader: Any) -> None:
        self._store = store
        self._log = log
        self._audio = audio
        self._scheduler = scheduler
        self._read_week = schedule_reader
        self._table = store.table("settings")
        self._log_table = store.table("log")

    # -------------------------------------------------------------- okuma

    async def state(self) -> dict[str, Any]:
        settings = await self._settings()
        groups = await self._groups()

        audio_state = self._audio.available() if self._audio else {"ready": False}
        sounds = self._audio.sounds() if self._audio else []
        scheduler_state = self._scheduler.state() if self._scheduler else {"running": False}

        rows = await self._store.fetch_all(
            f"SELECT * FROM {self._log_table} ORDER BY id DESC LIMIT 60"
        )

        return {
            "settings": settings,
            "groups": groups,
            "audio": audio_state,
            "sounds": sounds,
            "scheduler": scheduler_state,
            "log": [dict(row) for row in rows],
            # Zil çalabilir mi: ses aygıtı, zamanlayıcı ve ders saati üçü de gerekli.
            "ready": bool(audio_state.get("ready")) and bool(scheduler_state.get("running"))
            and bool(groups) and bool(settings.get("enabled")),
        }

    async def _settings(self) -> dict[str, Any]:
        row = await self._store.fetch_one(f"SELECT * FROM {self._table} WHERE id = 1")
        if row is None:
            return {"version": 1, "enabled": True, "groups": {}}
        try:
            return self.normalize(json.loads(str(row["payload"])))
        except json.JSONDecodeError:
            self._log.warning("zil ayarı çözülemedi, varsayılana dönülüyor")
            return {"version": 1, "enabled": True, "groups": {}}

    async def _groups(self) -> list[dict[str, Any]]:
        """Ders Takvimi'nden haftalık plan. Yoksa boş — ekran bunu söyler (K7)."""
        if self._read_week is None:
            return []
        try:
            result = self._read_week()
            return list(await result if hasattr(result, "__await__") else result)
        except Exception as failure:  # noqa: BLE001 — takvim modülü dışarısı
            self._log.warning("ders takvimi okunamadı", error=str(failure))
            return []

    @staticmethod
    def normalize(state: Any) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {"version": 1, "enabled": True, "groups": {}}
        groups: dict[str, Any] = {}
        for key, value in (state.get("groups") or {}).items():
            if not isinstance(value, dict):
                continue
            base = _default_group()
            groups[str(key)] = {
                "enabled": value.get("enabled") is not False,
                "soundId": str(value.get("soundId") or base["soundId"]),
                "volume": max(0, min(100, int(value.get("volume") or base["volume"]))),
                "ringStart": value.get("ringStart") is not False,
                "ringEnd": value.get("ringEnd") is not False,
                "overrides": value.get("overrides") if isinstance(value.get("overrides"), dict) else {},
            }
        return {"version": 1, "enabled": state.get("enabled") is not False, "groups": groups}

    # -------------------------------------------------------------- yazma

    async def save(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        clean = self.normalize(payload)
        await self._store.execute(
            f"INSERT INTO {self._table} (id, payload, updated_at, updated_by) "
            f"VALUES (1, ?, ?, ?) "
            f"ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, "
            f"updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (json.dumps(clean, ensure_ascii=False), _now(), actor),
        )
        await self.reschedule()
        return {"ok": True, "settings": clean}

    async def adopt(self, payload: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """Tarayıcı belleğindeki eski ayarı bir kez içeri alır (yalnız boşsa)."""
        row = await self._store.fetch_one(f"SELECT id FROM {self._table} WHERE id = 1")
        if row is not None:
            return {"ok": False, "adopted": False, "reason": "already_present"}
        result = await self.save(payload, actor=actor)
        self._log.info("zil ayarı tarayıcı belleğinden alındı", actor=actor)
        return {"ok": True, "adopted": True, **result}

    # ---------------------------------------------------------- çalma

    async def ring(self, *, group_id: str = "", edge: str = "manual",
                   sound: str = "", volume: int | None = None) -> dict[str, Any]:
        """Zili çalar ve sonucu GÜNLÜĞE yazar (başarısızlık dahil)."""
        if self._audio is None:
            return await self._record(group_id, "", edge, sound, False,
                                      "Ses yeteneği yok.")

        settings = await self._settings()
        group = settings["groups"].get(group_id) or _default_group()
        name = sound or group["soundId"]
        level = group["volume"] if volume is None else int(volume)

        group_name = ""
        for item in await self._groups():
            if str(item.get("id")) == group_id:
                group_name = str(item.get("name") or "")
                break

        try:
            result = await self._audio.play(name, volume=level)
        except Exception as failure:  # noqa: BLE001 — ses dışarısı; günlük tutulmalı
            self._log.warning("zil çalınamadı", sound=name, error=str(failure))
            return await self._record(group_id, group_name, edge, name, False, str(failure))

        return await self._record(group_id, group_name, edge, name, True,
                                  f"{result.get('player')} · {result.get('seconds')} sn")

    async def _record(self, group_id: str, group_name: str, edge: str, sound: str,
                      ok: bool, detail: str) -> dict[str, Any]:
        await self._store.execute(
            f"INSERT INTO {self._log_table} (at, group_id, group_name, edge, sound, ok, detail) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_now(), group_id, group_name, edge, sound, 1 if ok else 0, detail),
        )
        return {"ok": ok, "sound": sound, "detail": detail}

    # ------------------------------------------------------- zamanlama

    async def reschedule(self) -> dict[str, Any]:
        """Ders saatlerinden tetikleyici tablosu kurar ve zamanlayıcıya verir.

        Ayar ya da takvim değişince yeniden çağrılır; zamanlayıcı planı tümüyle
        değiştirir (artık geçersiz tetikleyici kalmaz).
        """
        if self._scheduler is None:
            return {"ok": False, "error": "Zamanlayıcı yeteneği yok."}

        settings = await self._settings()
        if not settings.get("enabled"):
            self._scheduler.clear("bell")
            return {"ok": True, "triggers": 0, "reason": "ana şalter kapalı"}

        triggers: list[Any] = []
        for group in await self._groups():
            group_id = str(group.get("id") or "")
            config = settings["groups"].get(group_id) or _default_group()
            if not config["enabled"]:
                continue

            for day, blocks in (group.get("week") or {}).items():
                for block in blocks or []:
                    key = f"{day}|{block.get('start')}"
                    override = config["overrides"].get(key) or {}
                    sound = str(override.get("soundId") or config["soundId"])

                    if config["ringStart"] and override.get("start") is not False:
                        triggers.append(Trigger(
                            day=day, time=str(block.get("start")),
                            label=f"{group.get('name')} · {block.get('name') or 'ders'} başı",
                            payload={"groupId": group_id, "edge": "start", "sound": sound},
                        ))
                    if config["ringEnd"] and override.get("end") is not False:
                        triggers.append(Trigger(
                            day=day, time=str(block.get("end")),
                            label=f"{group.get('name')} · {block.get('name') or 'ders'} sonu",
                            payload={"groupId": group_id, "edge": "end", "sound": sound},
                        ))

        count = self._scheduler.set_plan("bell", triggers, self._on_trigger)
        return {"ok": True, "triggers": count}

    async def _on_trigger(self, trigger: Any) -> None:
        payload = getattr(trigger, "payload", {}) or {}
        await self.ring(
            group_id=str(payload.get("groupId") or ""),
            edge=str(payload.get("edge") or "start"),
            sound=str(payload.get("sound") or ""),
        )
