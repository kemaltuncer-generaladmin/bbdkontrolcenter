"""Çekirdeğin kendi metadata deposu (SQLite).

`km_core/store` = ÇEKİRDEĞİN kendi verisi: kullanıcı, rol, oturum, denetim izi,
modül göç kaydı ve modüllerin kendi tabloları.
`km_platform/database` = yönetilen UZAK veritabanları (BBD/BLD). Karıştırılmaz.

Modül tabloları da bu dosyadadır ama K5 gereği her modül YALNIZCA kendi
göçleriyle oluşturduğu tablolara yazar; tablo adları `mod_<modül_id>_` önekiyle
açılır ve göç kaydı modül kimliğiyle tutulur.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import aiosqlite

from km_core.files.private import FILE_MODE, ensure_private_dir

# Modül göçlerinin açabileceği tablo adı deseni — başka modülün alanına
# taşmayı imkânsız kılar.
MODULE_TABLE_PREFIX = "mod_{module_id}_"

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    owner       TEXT NOT NULL,          -- 'core' veya modül kimliği
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL,
    PRIMARY KEY (owner, name)
);

CREATE TABLE IF NOT EXISTS users (
    id                 TEXT PRIMARY KEY,
    first_name         TEXT NOT NULL,
    last_name          TEXT NOT NULL,
    title              TEXT,
    department         TEXT,
    org_scope          TEXT NOT NULL,
    phone_mobile       TEXT,
    phone_ext          TEXT,
    email              TEXT,
    note               TEXT,
    directory_visible  INTEGER NOT NULL DEFAULT 1,
    status             TEXT NOT NULL DEFAULT 'active',
    -- ADR 0016: giriş şifre iledir. PIN sütunları DÜŞÜRÜLMEZ — var olan
    -- kurulumda kimse kilitlenmesin diye bırakılır ve kullanılmaz.
    pin_hash           TEXT NOT NULL,
    pin_lookup         TEXT NOT NULL UNIQUE,
    pin_set_at         TEXT NOT NULL,
    password_hash      TEXT,
    secret_lookup      TEXT UNIQUE,
    password_set_at    TEXT,
    failed_attempts    INTEGER NOT NULL DEFAULT 0,
    locked_until       TEXT,
    last_login_at      TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    created_by         TEXT
);

CREATE TABLE IF NOT EXISTS roles (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    builtin     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS role_permissions (
    role_id     TEXT NOT NULL,
    permission  TEXT NOT NULL,          -- 'izin' veya 'izin:kapsam'
    PRIMARY KEY (role_id, permission)
);

CREATE TABLE IF NOT EXISTS user_roles (
    user_id  TEXT NOT NULL,
    role_id  TEXT NOT NULL,
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,       -- hash'lenmiş belirteç
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    last_seen   TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    user_id     TEXT,
    action      TEXT NOT NULL,
    scope       TEXT,
    result      TEXT NOT NULL,
    detail      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log (at);

CREATE TABLE IF NOT EXISTS secrets (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,          -- şifreli
    updated_at  TEXT NOT NULL
);

-- ADR 0019 — Çıktı Merkezi. Kayıt, dosyayı yazan çekirdek fonksiyonunda doğar;
-- yirmi modülün ortak kaydı olduğu için hiçbir modülün tablosu olamaz (K5).
-- `source` modül kimliğini VERİ olarak taşır; çekirdek ona göre dallanmaz (K1).
CREATE TABLE IF NOT EXISTS outputs (
    id             TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    user_id        TEXT,
    source         TEXT NOT NULL,
    kind           TEXT,
    title          TEXT,
    path           TEXT NOT NULL,
    bytes          INTEGER,
    pages          INTEGER,
    params_digest  TEXT,
    printed_count  INTEGER NOT NULL DEFAULT 0,
    last_printed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_outputs_created ON outputs (created_at);
"""


class Store:
    """Tek bağlantılı SQLite sarmalayıcı.

    SQLite tek yazara izin verir; sidecar tek süreçtir, bu yüzden tek bağlantı
    hem yeterli hem de yazma sırasını kendiliğinden düzenler. WAL açılır:
    okuma yazmayı bloklamaz.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        # Bu dosya öğrenci adı, veli telefonu ve modül tablolarını taşır.
        # Varsayılan umask altında 0644 oluşuyordu — makinedeki her kullanıcı
        # okuyabiliyordu. Klasör 0700, dosya ve WAL/SHM yoldaşları 0600.
        ensure_private_dir(self.path.parent)
        self._db = await aiosqlite.connect(self.path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(CORE_SCHEMA)
        await self._db.commit()
        self._restrict_files()

    def _restrict_files(self) -> None:
        """Veritabanı ve WAL/SHM yoldaşlarını 0600'e çeker.

        WAL kipinde veri bir süre `-wal` dosyasında durur; yalnız ana dosyayı
        daraltmak açığı kapatmaz. Dosyalar SQLite tarafından oluşturulduğu için
        izin sonradan uygulanır; hata durumunda açılış engellenmez (K7).
        """
        for suffix in ("", "-wal", "-shm"):
            companion = self.path.with_name(self.path.name + suffix)
            try:
                if companion.exists():
                    companion.chmod(FILE_MODE)
            except OSError:  # salt okunur bağlama vb. — açılışı düşürmez
                continue

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Depo açılmadı.")
        return self._db

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        async with self.db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        async with self.db.execute(sql, params) as cursor:
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        await self.db.execute(sql, params)
        await self.db.commit()

    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        await self.db.executemany(sql, rows)
        await self.db.commit()

    # ------------------------------------------------------------- göçler

    async def applied_migrations(self, owner: str) -> set[str]:
        rows = await self.fetch_all(
            "SELECT name FROM schema_migrations WHERE owner = ?", (owner,)
        )
        return {row["name"] for row in rows}

    async def apply_migration(self, owner: str, name: str, sql: str) -> None:
        """Göçü uygular ve kaydeder. Modül göçleri ad denetiminden geçer (K5)."""
        if owner != "core":
            _assert_module_tables(owner, sql)

        await self.db.executescript(sql)
        await self.db.execute(
            "INSERT INTO schema_migrations (owner, name, applied_at) "
            "VALUES (?, ?, datetime('now'))",
            (owner, name),
        )
        await self.db.commit()


_CREATE_TABLE = re.compile(
    r"create\s+table\s+(?:if\s+not\s+exists\s+)?[\"'`]?([a-z0-9_]+)", re.IGNORECASE
)
_CREATE_INDEX = re.compile(
    r"create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?[\"'`]?([a-z0-9_]+)", re.IGNORECASE
)


def _assert_module_tables(module_id: str, sql: str) -> None:
    """K5 kapısı: modül yalnızca kendi önekli tablolarını açabilir.

    Yorumla değil, kodla korunur — göç dosyası başka modülün tablosuna
    dokunmaya kalkarsa göç hiç uygulanmaz.
    """
    prefix = MODULE_TABLE_PREFIX.format(module_id=module_id)
    for match in _CREATE_TABLE.finditer(sql):
        name = match.group(1)
        if not name.startswith(prefix):
            raise ValueError(
                f"'{module_id}' modülü '{name}' tablosunu açamaz; ad '{prefix}' ile başlamalı (K5)."
            )
    for match in _CREATE_INDEX.finditer(sql):
        name = match.group(1)
        if not name.startswith(prefix):
            raise ValueError(
                f"'{module_id}' modülü '{name}' indeksini açamaz; ad '{prefix}' ile başlamalı (K5)."
            )
