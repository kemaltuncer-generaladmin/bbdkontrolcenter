"""Platform yeteneği `events.outbox` (ADR 0029) — kalıcı olay outbox'u.

Ayrıntı ve sözleşme `outbox.py` başlığında.
"""

from km_platform.events.outbox import (
    ORIGINS,
    SUBJECT_KINDS,
    EventOutbox,
    OutboxError,
    envelope,
    new_ulid,
)

__all__ = ["ORIGINS", "SUBJECT_KINDS", "EventOutbox", "OutboxError", "envelope", "new_ulid"]
