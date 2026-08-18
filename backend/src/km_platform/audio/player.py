"""Ses yeteneği — yerel ses aygıtından dosya çalma.

PLATFORM YETENEĞİDİR, MODÜL DEĞİL (ADR 0006): silinemez altyapıdır, modüller
tüketir. Zil sistemi buna dayanır ama tek tüketicisi o olmak zorunda değil.

İKİ YOL: birincil `paplay` (PipeWire/PulseAudio), ses sunucusu düşerse `aplay`
(ALSA). İkisi de yoksa yetenek "hazır değil" der ve çağıran bunu görür —
sessizce başarısız olmaz, çünkü çalmayan bir zil fark edilmeyen bir arızadır.

Çalma AYRI SÜREÇTE ve zaman aşımlıdır: bozuk bir dosya ya da takılan ses
sunucusu çekirdeği kilitleyemez (K7).
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

#: Çalma en çok bu kadar sürer; aşarsa süreç öldürülür. Zil sesi birkaç saniyedir.
DEFAULT_TIMEOUT = 30.0


class AudioError(RuntimeError):
    """Ses çalınamadı."""


class AudioPlayer:
    """`audio` yeteneğinin uygulaması."""

    def __init__(self, config: Any, log: Any) -> None:
        section = config.section("platform.audio") or {}
        self._log = log
        self._player = str(section.get("player") or "paplay")
        self._fallback = str(section.get("fallback_player") or "aplay")
        self._device = str(section.get("device") or "default")
        self._volume = int(section.get("volume") or 90)
        self._backend = str(section.get("backend") or "pipewire")
        # YAZILABİLİR dizin: kullanıcının yüklediği sesler ve Vertex'in
        # ürettiği anonslar buraya iner. Git dışıdır ve sunucuda kalıcı diske
        # bağlanır.
        self._sounds = config.path("platform.audio.sounds_path", "data/sounds")
        # ÜRÜNLE GELEN hazır sesler — imajın içinde, salt okunur.
        #
        # BULUNAN ARIZA (18.08.2026): hazır sesler yalnız `data/sounds` altında
        # duruyordu. O klasör git dışı ve imaja kopyalanmıyor
        # (`deploy/server/Dockerfile` yalnız backend/modules/config/docs alıyor),
        # yani SUNUCUDA HİÇ SES YOKTU. `resolve()` `None` dönüyor,
        # `BellService._bell_items()` boş liste veriyor ve tetikleyici hiçbir şey
        # çalmadan dönüyordu — zil sessizce hiç çalmıyordu.
        #
        # Gerekçenin tamamı: `sounds/README.md`.
        self._builtin = Path(__file__).resolve().parent / "sounds"

    # ------------------------------------------------------------ durum

    def available(self) -> dict[str, Any]:
        """Hangi çalıcı bulundu, sesler nerede — ekran bunu göstersin."""
        primary = shutil.which(self._player)
        fallback = shutil.which(self._fallback)
        return {
            "ready": bool(primary or fallback),
            "backend": self._backend,
            "player": self._player if primary else "",
            "fallback": self._fallback if fallback else "",
            "device": self._device,
            "volume": self._volume,
            "soundsPath": str(self._sounds),
            "builtinPath": str(self._builtin),
        }

    def sounds(self) -> list[dict[str, Any]]:
        """Çalınabilir sesler: yazılabilir dizin + ürünle gelenler.

        AYNI ADDA İKİ DOSYA VARSA YAZILABİLİR DİZİN KAZANIR. Kullanıcı
        `classic_electric.wav` adıyla kendi kaydını yüklerse onun sesi çalar;
        ürünle gelen kopya yalnızca "hiç ses yok" durumunu ortadan kaldırır.
        """
        allowed = {".wav", ".ogg", ".mp3", ".flac", ".oga"}
        found: dict[str, dict[str, Any]] = {}
        # SIRA ÖNEMLİ: hazır sesler ÖNCE konur, kullanıcının dosyası üstüne
        # yazar. Ters sırada yükleme sessizce etkisiz kalırdı.
        for root in (self._builtin, self._sounds):
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if not path.is_file() or path.suffix.lower() not in allowed:
                    continue
                found[path.name] = {
                    "name": path.name, "stem": path.stem,
                    "size": path.stat().st_size, "path": str(path),
                    "builtin": root == self._builtin,
                }
        return sorted(found.values(), key=lambda item: str(item["name"]))

    def resolve(self, name: str) -> Path | None:
        """Ses adını dosyaya çevirir. Klasör dışına çıkılamaz."""
        if not name or "/" in name or "\\" in name or name.startswith("."):
            return None
        for item in self.sounds():
            path = Path(str(item["path"]))
            if path.name == name or path.stem == name:
                return path
        return None

    # ------------------------------------------------------------ çalma

    async def play(self, name: str, *, volume: int | None = None,
                   timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        """Sesi çalar. Dönüş: hangi çalıcıyla çalındı, ne kadar sürdü."""
        path = self.resolve(name)
        if path is None:
            raise AudioError(f"Ses bulunamadı: {name}")

        level = max(0, min(100, self._volume if volume is None else int(volume)))
        attempts: list[tuple[str, list[str]]] = []

        if shutil.which(self._player):
            command = [self._player]
            if self._player == "paplay":
                # paplay ses düzeyini 0..65536 ölçeğinde ister.
                command += [f"--volume={round(level * 65536 / 100)}"]
                if self._device and self._device != "default":
                    command += [f"--device={self._device}"]
            command.append(str(path))
            attempts.append((self._player, command))

        if shutil.which(self._fallback):
            command = [self._fallback]
            if self._fallback == "aplay" and self._device and self._device != "default":
                command += ["-D", self._device]
            command.append(str(path))
            attempts.append((self._fallback, command))

        if not attempts:
            raise AudioError(
                f"Ses çalıcı bulunamadı ({self._player} ya da {self._fallback}). "
                "Sistem paketleri kurulmalı."
            )

        errors: list[str] = []
        for player, command in attempts:
            started = asyncio.get_running_loop().time()
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as failure:
                errors.append(f"{player}: {failure}")
                continue

            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except TimeoutError:
                # Takılan çalıcı çekirdeği bekletmez.
                process.kill()
                await process.wait()
                errors.append(f"{player}: zaman aşımı ({timeout:.0f} sn)")
                continue

            elapsed = asyncio.get_running_loop().time() - started
            if process.returncode == 0:
                return {"played": True, "player": player, "sound": path.name,
                        "seconds": round(elapsed, 2), "volume": level}

            errors.append(f"{player}: çıkış {process.returncode} "
                          f"{(stderr or b'').decode(errors='replace')[:120]}")
            self._log.warning("ses çalınamadı, yedeğe geçiliyor", player=player)

        raise AudioError("Ses çalınamadı — " + " · ".join(errors))
