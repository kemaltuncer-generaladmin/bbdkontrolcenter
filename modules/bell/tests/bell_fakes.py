"""Zil testlerinin taklitleri. AĞA ÇIKMAZ.

Depo gerçektir (bkz. `conftest.py`); taklit edilenler yalnız DIŞARIYA açılan
üç yüzey: ses aygıtı, zamanlayıcı ve Vertex istemcisi.
"""

from __future__ import annotations

import hashlib
import io
import wave
from pathlib import Path
from typing import Any

from bell_backend.service import content_id


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def _add(self, level: str, message: str, **fields: Any) -> None:
        self.records.append((level, message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._add("info", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._add("warning", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._add("error", message, **fields)

    def text(self) -> str:
        return " ".join(f"{message} {fields}" for _, message, fields in self.records)


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


class FakeAudio:
    """`audio` yeteneğinin test yüzü. Çalınanları sırayla biriktirir."""

    def __init__(self, sounds_path: Path, *, ready: bool = True) -> None:
        self._path = Path(sounds_path)
        self._ready = ready
        self.played: list[tuple[str, int]] = []
        self.fail = ""

    def available(self) -> dict[str, Any]:
        return {"ready": self._ready, "soundsPath": str(self._path),
                "player": "paplay", "device": "default", "volume": 90}

    def sounds(self) -> list[dict[str, Any]]:
        if not self._path.is_dir():
            return []
        return sorted(
            ({"name": item.name, "stem": item.stem, "size": item.stat().st_size,
              "path": str(item)}
             for item in self._path.iterdir() if item.is_file()),
            key=lambda entry: str(entry["name"]),
        )

    def resolve(self, name: str) -> Path | None:
        if not name or "/" in name or "\\" in name:
            return None
        for item in self.sounds():
            path = Path(str(item["path"]))
            if path.name == name or path.stem == name:
                return path
        return None

    async def play(self, name: str, *, volume: int | None = None) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError(self.fail)
        self.played.append((name, int(volume or 0)))
        return {"played": True, "player": "paplay", "sound": name, "seconds": 1.0}


class FakeScheduler:
    """`scheduler` yeteneğinin test yüzü. Planı saklar, saat beklemez."""

    def __init__(self) -> None:
        self.plans: dict[str, list[Any]] = {}
        self.handlers: dict[str, Any] = {}
        self.cleared: list[str] = []

    def set_plan(self, owner: str, triggers: list[Any], handler: Any) -> int:
        self.plans[owner] = list(triggers)
        self.handlers[owner] = handler
        return len(triggers)

    def clear(self, owner: str) -> None:
        self.cleared.append(owner)
        self.plans.pop(owner, None)

    def state(self) -> dict[str, Any]:
        return {"running": True, "owners": {k: len(v) for k, v in self.plans.items()},
                "next": []}

    async def fire(self, owner: str, index: int) -> None:
        """Plandaki tetikleyiciyi elle çalıştırır."""
        await self.handlers[owner](self.plans[owner][index])


class FakeBridge:
    """Köprü istemcisinin test yüzü. Gönderilen komutları biriktirir."""

    def __init__(self, *, online: bool = True, lead_seconds: int = 60) -> None:
        self.lead_seconds = lead_seconds
        self.online = online
        self.sent: list[dict[str, Any]] = []
        self.uploaded: dict[str, bytes] = {}      # köprüdekiler
        self.agent_sounds: list[str] = []         # ajanın yerelindekiler
        self.error = ""

    async def try_send(self, items: list[dict[str, Any]], *,
                       play_at: str = "") -> dict[str, Any]:
        if self.error:
            return {"ok": False, "detail": self.error}
        self.sent.append({"items": items, "playAt": play_at})
        return {"ok": True, "detail": f"köprü kuyruğu #{len(self.sent)}"}

    async def try_status(self) -> dict[str, Any]:
        if self.error:
            return {"online": False, "error": self.error,
                    "sounds": [], "bridgeSounds": []}
        return {
            "online": self.online,
            "lastSeen": "2026-08-14T10:00:00+03:00",
            "lastAck": {},
            # Ajanın yerelindekiler ile köprüdekiler AYRI: gerçekte de öyle.
            "sounds": sorted(self.agent_sounds),
            "bridgeSounds": sorted(self.uploaded),
            "error": "",
        }

    async def upload(self, name: str, data: bytes) -> dict[str, Any]:
        # Köprü kimliği İÇERİKTEN üretir, gönderilen ada güvenmez.
        self.uploaded[content_id(data)] = data
        return {"ok": True}

    def forget_status(self) -> None:
        """Gerçek istemcide durum önbelleğini düşürür; burada yapacak iş yok."""

    async def prune(self, keep: list[str]) -> dict[str, Any]:
        stale = [key for key in self.uploaded if key not in set(keep)]
        for key in stale:
            del self.uploaded[key]
        return {"ok": True, "removed": len(stale)}


class FakeSpeech:
    """Vertex istemcisinin test yüzü.

    `script` her çağrıda sıradaki sonucu verir: `bytes` başarı, `Exception`
    hata. Liste biterse son davranış tekrarlanır — kuyruğun yeniden deneme
    turlarını yazarken her tur için ayrı satır girmek gerekmesin.
    """

    def __init__(self, *, model: str = "test-tts", voice: str = "Kore",
                 script: list[Any] | None = None) -> None:
        self.model = model
        self.voice = voice
        self.script = list(script or [])
        self.calls: list[str] = []

    async def synthesize(self, text: str) -> Any:
        from bell_backend.speech import Speech

        self.calls.append(text)
        step: Any = b""
        if self.script:
            step = self.script[0] if len(self.script) == 1 else self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        # Metne göre AYRI bayt: gerçek TTS de iki farklı cümle için aynı
        # dosyayı üretmez. Aynı baytı döndürmek, içerik adresli tekilleştirmeyi
        # yanlış yere tetikler ve testi sessizce yanıltır.
        data = step if isinstance(step, bytes) and step else wav_bytes(text=text)
        return Speech(data=data, seconds=1.0, model=self.model, voice=self.voice)


def wav_bytes(*, text: str = "", rate: int = 8000, frames: int = 400) -> bytes:
    """Geçerli bir WAV — testlerin diske yazdığı gerçek dosya.

    Örnekler `text`ten türetilir: farklı metin, farklı içerik, farklı özet.
    """
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    body = (seed * ((frames * 2) // len(seed) + 1))[: frames * 2]
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(body)
    return buffer.getvalue()
