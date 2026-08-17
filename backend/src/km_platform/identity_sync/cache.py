"""Kadronun yerel önbelleği.

**NEDEN AYRI BİR DOSYA, ÇEKİRDEK VERİTABANI DEĞİL.** Önbellek merkezin
kopyasıdır; çekirdeğin `users` tablosu ise kurulumun kendi doğruluk kaydıdır.
İkisini aynı tabloya koymak, "bu satır merkezden mi geldi yoksa burada mı
doğdu" sorusunu cevapsız bırakır ve bir sonraki senkronda yerelde doğmuş
kaydın sessizce ezilmesine kapı açar.

**DOSYA 0600'DÜR.** İçinde `password_hash` (Argon2id) ve `secret_lookup`
duruyor — çevrimdışı giriş bunlar olmadan mümkün değil (ADR 0021 §2). Varsayılan
umask altında 0644 doğardı ve makinedeki her kullanıcı okurdu; `write_private`
bu yarışı dosya oluşturulurken kapatır.

**ÖNBELLEK SINIRSIZ ESKİYEMEZ** (ADR 0021 — Sonuçlar). Pasifleştirilen bir
kullanıcının çevrimdışı bir makinede sonsuza dek giriş yapabilmesi kabul
edilemez; yaş sınırı ayardan gelir ve `is_stale` bunu söyler.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from km_core.files.private import write_private

log = structlog.get_logger("km.identity_sync")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class RosterCache:
    path: Path

    # ------------------------------------------------------------- okuma

    def read(self) -> dict[str, Any] | None:
        """Önbelleği okur. Bozuk dosya `None` döner ve AÇILIŞI DÜŞÜRMEZ (K7):
        bozulmuş bir önbellek, senkronun bir sonraki turunda yeniden yazılır."""
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warning("kadro önbelleği okunamadı", path=str(self.path), error=str(error))
            return None
        return data if isinstance(data, dict) else None

    def revision(self) -> int | None:
        data = self.read()
        if data is None:
            return None
        value = data.get("revision")
        return int(value) if isinstance(value, int) else None

    def fetched_at(self) -> datetime | None:
        data = self.read()
        stamp = (data or {}).get("fetchedAt")
        if not stamp:
            return None
        try:
            return datetime.fromisoformat(str(stamp))
        except ValueError:
            return None

    def age_seconds(self) -> float | None:
        fetched = self.fetched_at()
        if fetched is None:
            return None
        return max(0.0, (_now() - fetched).total_seconds())

    def is_stale(self, max_age_hours: float) -> bool:
        """Sınır aşıldı mı? Önbellek HİÇ YOKSA da bayat sayılır — hiç kadro
        görmemiş bir kurulumun eski bir kadroya dayanması söz konusu değildir,
        ama "taze" demek de yanlış olurdu."""
        if max_age_hours <= 0:
            return False
        age = self.age_seconds()
        if age is None:
            return True
        return age > max_age_hours * 3600

    # ------------------------------------------------------------- yazma

    def write(self, payload: dict[str, Any]) -> None:
        record = {**payload, "fetchedAt": _now().isoformat(timespec="seconds")}
        write_private(self.path, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        log.info("kadro önbelleği güncellendi", revision=record.get("revision"))

    def clear(self) -> None:
        """Önbelleği siler — eşleme sıfırlanırken.

        İÇİNDE PIN HASH'İ VAR. Eşlemesi düşürülen bir kurulumda bayat kadroyu
        bırakmak, merkezle bağı kopmuş bir makinede eski kullanıcıların
        çevrimdışı girmeye devam etmesi demekti. Dosya yoksa sessiz geçilir.
        """
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:  # pragma: no cover — izin/kilit
            log.warning("kadro önbelleği silinemedi", error=str(error))

    def summary(self) -> dict[str, Any]:
        """Ekrana ve sağlık ucuna verilen özet. SIR ALANI ÇIKMAZ: yalnız sayılar
        ve zaman damgası."""
        data = self.read()
        if data is None:
            return {"present": False, "revision": None, "users": 0, "fetchedAt": None,
                    "ageSeconds": None}
        return {
            "present": True,
            "revision": data.get("revision"),
            "users": len(data.get("users") or []),
            "roles": len(data.get("roles") or []),
            "fetchedAt": data.get("fetchedAt"),
            "ageSeconds": self.age_seconds(),
        }
