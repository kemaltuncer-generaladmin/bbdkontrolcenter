"""Errors shared by the tablet service and the app distribution.

`service.py` re-exports them, so `from ..service import TabletError` keeps
working; `distribution.py` imports them from here to avoid an import cycle.
"""

from __future__ import annotations

from typing import Any, ClassVar


class TabletError(Exception):
    """Machine-readable tablet error: `{"detail": {"code", "message", ...}}`."""

    STATUS: ClassVar[dict[str, int]] = {
        "DEVICE_REVOKED": 401,
        "DEVICE_AUTH_INVALID": 401,
        "FORBIDDEN_DEVICE": 403,
        "INVALID_STUDENT_CODE": 401,
        "LOGIN_RATE_LIMITED": 429,
        "SESSION_CONFLICT": 409,
        "STUDENT_BLOCKED": 403,
        "OUTSIDE_USAGE_HOURS": 403,
        "DEVICE_LOCKED": 423,
        "NO_PROFILE": 409,
        "CANTEEN_UNAVAILABLE": 503,
        "DOWNLOAD_NOT_GRANTED": 409,
        "RELEASE_GONE": 410,
    }

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = self.STATUS[code]
        self.extra = extra

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.extra}


class NotFoundError(ValueError):
    """Management API: the addressed record does not exist (404)."""


class ConflictError(ValueError):
    """Management API: the record exists but its state forbids the action (409).

    The HTTP answer carries only the message (the core envelope keeps text);
    `code` and `extra` let callers inside the module tell conflicts apart.
    """

    def __init__(self, message: str, *, code: str | None = None, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.extra = extra

    def detail(self) -> str | dict[str, Any]:
        if not self.code:
            return str(self)
        return {"code": self.code, "message": str(self), **self.extra}


class CanteenUnavailableError(RuntimeError):
    """The canteen could not be reached (503)."""


class StorageError(RuntimeError):
    """The storage disk refused a write: 507 when it is full, 500 otherwise.

    The message is Turkish and safe to show; the OS error stays in the cause.
    """

    def __init__(self, message: str, *, status: int = 500) -> None:
        super().__init__(message)
        self.status = status
