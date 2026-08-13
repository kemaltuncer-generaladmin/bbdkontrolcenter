"""Kimlik ve yetki (ADR 0007, docs/identity-model.md).

Kimlik ÇEKİRDEĞE aittir; modül değildir, kapatılamaz.

Giriş kullanıcı adsızdır: PIN hem kimliği hem girişi belirler.
  · PIN en az 6 hane, yalnızca rakam, benzersiz.
  · Argon2id ile hash'lenir; düz PIN hiçbir yerde saklanmaz.
  · Ayrıca sabit anahtarlı (pepper) `pin_lookup` üretilir — girişte tüm
    kullanıcıları tarayıp tek tek Argon2 denemek yerine tek satır bulunur.
  · Kullanıcı bulunamasa bile doğrulama süresi harcanır: yanıt süresinden
    "bu PIN kimseye ait değil" bilgisi sızmaz.

K10 — rol adı sorulmaz. Dışarıya açılan tek yetki sorusu `has_permission`.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

import structlog
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from km_core.store.db import Store

log = structlog.get_logger("km.security")

_hasher = PasswordHasher()

# Kullanıcı bulunamadığında da doğrulama maliyeti ödensin diye kullanılan
# sahte hash. Zamanlama farkını kapatır.
_DUMMY_HASH = _hasher.hash("000000-bulunamadi")

BASIT_PINLER = {"123456", "000000", "111111", "654321", "123123", "112233"}

# Ön tanımlı roller (docs/permissions.md). İzinler modüllerin manifestlerinden
# gelir; burada yalnız rollerin VARLIĞI garanti edilir.
BUILTIN_ROLES = [
    ("admin", "Admin", "Tam yetki."),
    ("bld_staff", "BLD Personeli", "BLD sunucuları ve veritabanı."),
    ("bbd_staff", "BBD Personeli", "BBD sunucuları ve veritabanı."),
    ("org_staff", "Kurum Personeli", "Zil, baskı, rehber."),
    ("accountant", "Mali Müşavir", "Mali ekranlar: fatura, cari, vergilendirme, raporlar."),
]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(slots=True)
class CurrentUser:
    id: str
    first_name: str
    last_name: str
    org_scope: str
    roles: list[str]
    permissions: set[str] = field(default_factory=set)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def has_permission(self, key: str, scope: str | None = None) -> bool:
        """Yetki denetiminin TEK yolu. Rol adı sorulmaz (K10)."""
        if key in self.permissions:
            return scope is None
        if f"{key}:*" in self.permissions:
            return True
        return scope is not None and f"{key}:{scope}" in self.permissions


class PinError(ValueError):
    """PIN sözleşmeye uymuyor."""


class Identity:
    def __init__(self, store: Store, pepper: str, *, pin_min_length: int = 6,
                 max_failed_attempts: int = 5, lockout_minutes: int = 15,
                 session_idle_minutes: int = 30) -> None:
        self.store = store
        self._pepper = pepper.encode("utf-8")
        self.pin_min_length = pin_min_length
        self.max_failed_attempts = max_failed_attempts
        self.lockout_minutes = lockout_minutes
        self.session_idle_minutes = session_idle_minutes

    # ------------------------------------------------------------------ PIN

    def _lookup(self, pin: str) -> str:
        return hmac.new(self._pepper, pin.encode("utf-8"), hashlib.sha256).hexdigest()

    def validate_pin(self, pin: str) -> None:
        if not pin.isdigit():
            raise PinError("PIN yalnızca rakamlardan oluşur.")
        if len(pin) < self.pin_min_length:
            raise PinError(f"PIN en az {self.pin_min_length} hane olmalı.")
        if pin in BASIT_PINLER or len(set(pin)) == 1:
            raise PinError("Bu PIN fazla basit.")
        # ardışık artan/azalan dizi
        digits = [int(char) for char in pin]
        if all(b - a == 1 for a, b in pairwise(digits)):
            raise PinError("Ardışık PIN kullanılamaz.")
        if all(a - b == 1 for a, b in pairwise(digits)):
            raise PinError("Ardışık PIN kullanılamaz.")

    # --------------------------------------------------------------- roller

    async def ensure_builtin_roles(self) -> None:
        for role_id, name, description in BUILTIN_ROLES:
            await self.store.execute(
                "INSERT INTO roles (id, name, description, builtin) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name",
                (role_id, name, description),
            )

    async def grant_defaults(self, permissions: list[dict[str, Any]]) -> None:
        """Modül manifestlerindeki `default_roles` önerilerini uygular.

        Yalnızca EKLER; yöneticinin sonradan kaldırdığı izni geri getirmemek
        için mevcut satıra dokunmaz.
        """
        rows = []
        for permission in permissions:
            key = permission["key"]
            entry = f"{key}:*" if permission.get("scoped") else key
            for role_id in permission.get("default_roles") or []:
                rows.append((role_id, entry))
        if rows:
            await self.store.execute_many(
                "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
                rows,
            )

    # ----------------------------------------------------------- kullanıcı

    async def create_user(self, *, first_name: str, last_name: str, org_scope: str,
                          pin: str, roles: list[str], created_by: str | None = None,
                          **extra: Any) -> str:
        self.validate_pin(pin)
        lookup = self._lookup(pin)

        if await self.store.fetch_one("SELECT id FROM users WHERE pin_lookup = ?", (lookup,)):
            raise PinError("Bu PIN başka bir kullanıcıda kayıtlı.")

        user_id = str(uuid.uuid4())
        await self.store.execute(
            "INSERT INTO users (id, first_name, last_name, title, department, org_scope, "
            "phone_mobile, phone_ext, email, note, directory_visible, status, pin_hash, "
            "pin_lookup, pin_set_at, created_at, updated_at, created_by) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                user_id, first_name, last_name, extra.get("title"), extra.get("department"),
                org_scope, extra.get("phone_mobile"), extra.get("phone_ext"), extra.get("email"),
                extra.get("note"), int(extra.get("directory_visible", True)), "active",
                _hasher.hash(pin), lookup, now(), now(), now(), created_by,
            ),
        )
        await self.store.execute_many(
            "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
            [(user_id, role) for role in roles],
        )
        await self.audit(created_by, "users.create", result="ok", detail=user_id)
        return user_id

    async def user_count(self) -> int:
        row = await self.store.fetch_one("SELECT COUNT(*) AS n FROM users")
        return int(row["n"]) if row else 0

    async def permissions_of(self, user_id: str) -> set[str]:
        rows = await self.store.fetch_all(
            "SELECT rp.permission FROM role_permissions rp "
            "JOIN user_roles ur ON ur.role_id = rp.role_id WHERE ur.user_id = ?",
            (user_id,),
        )
        return {row["permission"] for row in rows}

    async def roles_of(self, user_id: str) -> list[str]:
        rows = await self.store.fetch_all(
            "SELECT role_id FROM user_roles WHERE user_id = ? ORDER BY role_id", (user_id,)
        )
        return [row["role_id"] for row in rows]

    # ------------------------------------------------------------- giriş

    async def login(self, pin: str) -> tuple[str, CurrentUser] | None:
        """PIN ile giriş. Başarısızsa None — sebep AYIRT EDİLMEZ."""
        row = await self.store.fetch_one(
            "SELECT * FROM users WHERE pin_lookup = ?", (self._lookup(pin),)
        )

        if row is None:
            # Sahte doğrulama: yanıt süresi kullanıcı varmış gibi geçsin.
            try:
                _hasher.verify(_DUMMY_HASH, pin)
            except VerifyMismatchError:
                pass
            await self.audit(None, "auth.login", result="fail", detail="bilinmeyen PIN")
            return None

        locked_until = row.get("locked_until")
        if locked_until and datetime.fromisoformat(locked_until) > datetime.now(UTC):
            await self.audit(row["id"], "auth.login", result="fail", detail="kilitli")
            return None

        try:
            _hasher.verify(row["pin_hash"], pin)
        except VerifyMismatchError:
            await self._register_failure(row)
            return None

        if row["status"] != "active":
            await self.audit(row["id"], "auth.login", result="fail", detail="pasif kullanıcı")
            return None

        await self.store.execute(
            "UPDATE users SET failed_attempts = 0, locked_until = NULL, last_login_at = ? "
            "WHERE id = ?",
            (now(), row["id"]),
        )

        token = secrets.token_urlsafe(32)
        expires = datetime.now(UTC) + timedelta(minutes=self.session_idle_minutes)
        await self.store.execute(
            "INSERT INTO sessions (token, user_id, created_at, last_seen, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash_token(token), row["id"], now(), now(), expires.isoformat(timespec="seconds")),
        )

        await self.audit(row["id"], "auth.login", result="ok")
        return token, await self._current_user(row)

    async def _register_failure(self, row: dict[str, Any]) -> None:
        attempts = int(row["failed_attempts"]) + 1
        locked = None
        if attempts >= self.max_failed_attempts:
            locked = (datetime.now(UTC) + timedelta(minutes=self.lockout_minutes)).isoformat(
                timespec="seconds"
            )
        await self.store.execute(
            "UPDATE users SET failed_attempts = ?, locked_until = ? WHERE id = ?",
            (attempts, locked, row["id"]),
        )
        await self.audit(
            row["id"], "auth.login", result="fail",
            detail=f"hatalı PIN ({attempts}){' — kilitlendi' if locked else ''}",
        )

    async def resolve_session(self, token: str) -> CurrentUser | None:
        row = await self.store.fetch_one(
            "SELECT s.user_id, s.expires_at, u.* FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (_hash_token(token),),
        )
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(UTC):
            await self.store.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))
            return None
        if row["status"] != "active":
            return None

        # Boşta kalma süresi her istekte tazelenir.
        expires = datetime.now(UTC) + timedelta(minutes=self.session_idle_minutes)
        await self.store.execute(
            "UPDATE sessions SET last_seen = ?, expires_at = ? WHERE token = ?",
            (now(), expires.isoformat(timespec="seconds"), _hash_token(token)),
        )
        return await self._current_user(row)

    async def logout(self, token: str) -> None:
        await self.store.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))

    async def _current_user(self, row: dict[str, Any]) -> CurrentUser:
        user_id = row["id"]
        return CurrentUser(
            id=user_id,
            first_name=row["first_name"],
            last_name=row["last_name"],
            org_scope=row["org_scope"],
            roles=await self.roles_of(user_id),
            permissions=await self.permissions_of(user_id),
        )

    # ------------------------------------------------------------- denetim

    async def audit(self, user_id: str | None, action: str, *, result: str,
                    scope: str | None = None, detail: str | None = None) -> None:
        """Denetim izi. PIN ve sır değerleri BURAYA YAZILMAZ."""
        await self.store.execute(
            "INSERT INTO audit_log (at, user_id, action, scope, result, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (now(), user_id, action, scope, result, detail),
        )


def _hash_token(token: str) -> str:
    """Oturum belirteci veritabanında düz durmaz."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
