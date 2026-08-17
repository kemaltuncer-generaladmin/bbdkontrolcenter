"""Sır sütunu göçü — hiçbir kayıt kaybolmaz.

ADR 0016 reddedildi ama bu göç koştu ve geri ALINMADI; sütun/izin adları
"password" der, tutulan değer PIN'dir.

`CORE_SCHEMA` yalnız YENİ veritabanını kurar; `CREATE TABLE IF NOT EXISTS` var
olan tabloya sütun eklemez. Bir kez çalışmış makinede yeni sütunlar ancak
`km_core/security/migrations.py` ile gelir — bu testler o yolu gerçek bir
"eski" veritabanı kurarak yürütür.

Sınanan söz: sütunlar eklenir, PIN sütunları DÜŞÜRÜLMEZ, satırlar durur,
`users.set_pin` izni `users.set_password` olur ve göç iki kez uygulanmaz.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import aiosqlite
import pytest

from km_core.security.identity import Identity
from km_core.security.migrations import (
    CORE_MIGRATIONS,
    apply_core_migrations,
    table_columns,
)
from km_core.store.db import Store

# GÖÇ ÖNCESİNDEKİ tablo. Bilerek elle yazıldı: `CORE_SCHEMA`'dan
# türetilseydi, o dosya değiştiğinde test "eski veritabanı"nı sınamayı sessizce
# bırakırdı.
ESKI_SEMA = """
CREATE TABLE users (
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
    pin_hash           TEXT NOT NULL,
    pin_lookup         TEXT NOT NULL UNIQUE,
    pin_set_at         TEXT NOT NULL,
    failed_attempts    INTEGER NOT NULL DEFAULT 0,
    locked_until       TEXT,
    last_login_at      TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    created_by         TEXT
);
"""

PIN = "482913"


@pytest.fixture
async def eski_depo(tmp_path: Path) -> AsyncIterator[Store]:
    """Şifre sütunları olmayan, içinde kayıt duran bir veritabanı."""
    path = tmp_path / "eski.sqlite"
    async with aiosqlite.connect(path) as raw:
        await raw.executescript(ESKI_SEMA)
        await raw.execute(
            "INSERT INTO users (id, first_name, last_name, org_scope, status, "
            "pin_hash, pin_lookup, pin_set_at, created_at, updated_at) "
            "VALUES ('eski-1', 'Eski', 'Kayit', 'org', 'active', "
            "'argon2-hash', 'pin-arama-degeri', 'dun', 'dun', 'dun')"
        )
        await raw.commit()

    store = Store(path)
    await store.open()  # CORE_SCHEMA çalışır ama var olan tabloya dokunmaz
    yield store
    await store.close()


async def test_sifre_sutunlari_var_olan_tabloya_EKLENIR(eski_depo: Store) -> None:
    assert "password_hash" not in await table_columns(eski_depo, "users")

    await apply_core_migrations(eski_depo)

    sutunlar = await table_columns(eski_depo, "users")
    assert {"password_hash", "secret_lookup", "password_set_at"} <= sutunlar


async def test_PIN_sutunlari_dusurulmez(eski_depo: Store) -> None:
    await apply_core_migrations(eski_depo)

    sutunlar = await table_columns(eski_depo, "users")
    assert {"pin_hash", "pin_lookup", "pin_set_at"} <= sutunlar


async def test_hicbir_satir_kaybolmaz(eski_depo: Store) -> None:
    await apply_core_migrations(eski_depo)

    satir = await eski_depo.fetch_one("SELECT * FROM users WHERE id = 'eski-1'")
    assert satir is not None
    assert satir["first_name"] == "Eski"
    assert satir["pin_hash"] == "argon2-hash"
    assert satir["pin_lookup"] == "pin-arama-degeri"
    # Yeni sütunlar boş doğar: kullanıcı ilk girişte PIN'ini kurar.
    assert satir["password_hash"] is None
    assert satir["secret_lookup"] is None


async def test_secret_lookup_BENZERSIZ_olur(eski_depo: Store) -> None:
    """`ALTER TABLE` UNIQUE sütun ekleyemez; benzersizlik indeksle kurulur."""
    await apply_core_migrations(eski_depo)
    await eski_depo.execute(
        "UPDATE users SET secret_lookup = 'aynidegeri' WHERE id = 'eski-1'"
    )
    await eski_depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('eski-2', 'Baska', 'Kisi', 'org', 'active', 'h', 'p2', 'dun', 'dun', 'dun')"
    )

    with pytest.raises(aiosqlite.IntegrityError):
        await eski_depo.execute(
            "UPDATE users SET secret_lookup = 'aynidegeri' WHERE id = 'eski-2'"
        )


async def test_sifresiz_kullanicilar_birbirini_ENGELLEMEZ(eski_depo: Store) -> None:
    """NULL değerler benzersizlik kısıtına takılmaz — göçte kimse kilitlenmez."""
    await apply_core_migrations(eski_depo)
    await eski_depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('eski-2', 'Baska', 'Kisi', 'org', 'active', 'h', 'p2', 'dun', 'dun', 'dun')"
    )
    rows = await eski_depo.fetch_all("SELECT id FROM users WHERE secret_lookup IS NULL")
    assert len(rows) == 2


async def test_izin_anahtari_users_set_password_olur(eski_depo: Store) -> None:
    await eski_depo.execute_many(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission) VALUES (?, ?)",
        [("admin", "users.set_pin"), ("admin", "users.view"), ("bld_staff", "users.set_pin")],
    )

    await apply_core_migrations(eski_depo)

    izinler = {
        (row["role_id"], row["permission"])
        for row in await eski_depo.fetch_all("SELECT role_id, permission FROM role_permissions")
    }
    assert ("admin", "users.set_password") in izinler
    assert ("bld_staff", "users.set_password") in izinler
    assert ("admin", "users.set_pin") not in izinler
    assert ("bld_staff", "users.set_pin") not in izinler
    # Başka izinlere DOKUNULMAZ.
    assert ("admin", "users.view") in izinler


async def test_goc_iki_kez_uygulanmaz(eski_depo: Store) -> None:
    ilk = await apply_core_migrations(eski_depo)
    ikinci = await apply_core_migrations(eski_depo)

    # Sayı ELLE YAZILMAZ: yeni bir göç eklendiğinde bu testin "iki kez
    # uygulanmaz" sözünü sınamayı bırakıp sayaç tartışmasına dönmesi
    # gerekmiyor.
    assert len(ilk) == len(CORE_MIGRATIONS)
    assert ikinci == []
    assert await eski_depo.applied_migrations("core") == set(ilk)


async def test_yeni_veritabaninda_da_calisir(tmp_path: Path) -> None:
    """Sütunlar `CORE_SCHEMA` ile zaten gelmiştir; göç adımı atlanır, patlamaz."""
    store = Store(tmp_path / "yeni.sqlite")
    await store.open()
    try:
        assert len(await apply_core_migrations(store)) == len(CORE_MIGRATIONS)
        assert "revision" in await table_columns(store, "users")
    finally:
        await store.close()


async def test_goc_sonrasi_eski_kullanici_ESKI_PINIYLE_taninir(eski_depo: Store) -> None:
    """Göç tamamlandıktan sonra eski kayıt kilitlenmez: PIN'i tanınır, yeni
    PIN kurması istenir."""
    await apply_core_migrations(eski_depo)

    kimlik = Identity(eski_depo, pepper="deneme-pepper")
    await kimlik.ensure_builtin_roles()
    # Kaydı gerçek bir PIN'e bağla: `pin_lookup` aynı HMAC ile üretilir.
    from km_core.security.identity import _hasher

    await eski_depo.execute(
        "UPDATE users SET pin_hash = ?, pin_lookup = ? WHERE id = 'eski-1'",
        (_hasher.hash(PIN), kimlik.secret_lookup(PIN)),
    )

    sonuc = await kimlik.login(PIN)
    assert sonuc is not None
    assert sonuc.must_set_password is True
    assert sonuc.token is None
