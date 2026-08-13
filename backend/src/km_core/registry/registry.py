"""Yetenek kayıt defteri (registry).

Modüller birbirini import etmez (K3). Bir modül yeteneğini `provides` ile ilan
eder, buraya yazar; ihtiyacı olan `consumes` ile ilan eder, buradan çözer.
Platform yetenekleri (ssh, database, secrets…) de aynı deftere yazılır —
tüketen taraf için ikisi arasında fark yoktur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class CapabilityMissing(LookupError):
    """İstenen yetenek defterde yok."""


@dataclass(frozen=True, slots=True)
class Entry:
    capability: str
    provider: str          # 'core', 'platform' veya modül kimliği
    instance: Any


class Registry:
    def __init__(self) -> None:
        self._entries: dict[str, Entry] = {}

    def register(self, capability: str, instance: Any, *, provider: str) -> None:
        existing = self._entries.get(capability)
        if existing is not None:
            raise ValueError(
                f"'{capability}' yeteneği zaten '{existing.provider}' tarafından kayıtlı."
            )
        self._entries[capability] = Entry(capability, provider, instance)

    def unregister_provider(self, provider: str) -> None:
        """Modül düşerse bıraktığı yetenekler de defterden silinir (K7)."""
        for capability in [key for key, entry in self._entries.items() if entry.provider == provider]:
            del self._entries[capability]

    def resolve(self, capability: str) -> Any:
        entry = self._entries.get(capability)
        if entry is None:
            raise CapabilityMissing(f"'{capability}' yeteneği kayıtlı değil.")
        return entry.instance

    def try_resolve(self, capability: str) -> Any | None:
        entry = self._entries.get(capability)
        return entry.instance if entry is not None else None

    def has(self, capability: str) -> bool:
        return capability in self._entries

    def provider_of(self, capability: str) -> str | None:
        entry = self._entries.get(capability)
        return entry.provider if entry is not None else None

    def list(self) -> list[Entry]:
        return sorted(self._entries.values(), key=lambda entry: entry.capability)
