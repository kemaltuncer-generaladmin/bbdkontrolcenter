"""Sır sütunu göçü — hiçbir kayıt kaybolmaz.

ADR 0016 reddedildi ama bu göç koştu ve geri ALINMADI; sütun/izin adları
"password" der, tutulan değer PIN'dir.

`CORE_SCHEMA` yalnız YENİ veritabanını kurar; `CREATE TABLE IF NOT EXISTS` var
olan tabloya sütun eklemez. Bir kez çalışmış makinede yeni sütunlar ancak
`km_core/security/migrations.py` ile gelir — bu testler o yolu gerçek bir
"eski" veritabanı kurarak yürütür.

Sınanan söz: sütunlar eklenir, PIN sütunları DÜŞÜRÜLMEZ, satırlar durur,
`users.set_pin` izni `users.set_password` olur ve göç iki kez uygulanmaz.

17.08.2026 — `0006_backfill_secret_lookup` eklendi: 0016'nın "önce sır belirle"
zorlaması kaldırıldığı için geride kalan satırların sırrı GİRİŞTE değil GÖÇTE
yeni sütuna taşınır. Kullanıcı orijinal PIN'iyle girmeye devam eder.
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
    # Eski sütunlar OLDUĞU GİBİ durur — kopyalanır, taşınmaz.
    assert satir["pin_hash"] == "argon2-hash"
    assert satir["pin_lookup"] == "pin-arama-degeri"


async def test_0006_eski_sirri_YENI_SUTUNA_TASIR(eski_depo: Store) -> None:
    """Geride kalan satır göçte onarılır; girişte "PIN belirle" denmez."""
    await apply_core_migrations(eski_depo)

    satir = await eski_depo.fetch_one("SELECT * FROM users WHERE id = 'eski-1'")
    assert satir is not None
    assert satir["password_hash"] == satir["pin_hash"]
    assert satir["secret_lookup"] == satir["pin_lookup"]
    assert satir["password_set_at"] == satir["pin_set_at"]


async def test_0006_SIRSIZ_yer_tutucu_satira_DOKUNMAZ(eski_depo: Store) -> None:
    """`pin_hash = ''` + `pin_lookup = 'pin-yok:<id>'` sırrın YOKLUĞUNU anlatır
    (`Identity.create_user`, kadro yansıtması). Yer tutucuyu sır sütununa
    taşımak, sırsız kaydı sırlıymış gibi gösterirdi."""
    await eski_depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('sirsiz', 'Sirsiz', 'Kayit', 'org', 'active', '', "
        "'pin-yok:sirsiz', 'dun', 'dun', 'dun')"
    )

    await apply_core_migrations(eski_depo)

    satir = await eski_depo.fetch_one("SELECT * FROM users WHERE id = 'sirsiz'")
    assert satir is not None
    assert satir["password_hash"] is None
    assert satir["secret_lookup"] is None


async def test_0006_CAKISAN_satiri_ATLAR_gocu_patlatmaz(eski_depo: Store) -> None:
    """Eski yol benzersizliği denetlemiyordu: bir kullanıcının eski sırrı,
    başkasının bugünkü PIN'iyle aynı olabilir. `secret_lookup` UNIQUE olduğu
    için o satır yazılamaz — göçü patlatmak tüm kurulumu açılışta düşürürdü."""
    await eski_depo.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
        "pin_lookup, pin_set_at, created_at, updated_at) "
        "VALUES ('yeni-1', 'Yeni', 'Kayit', 'org', 'active', '', "
        "'pin-yok:yeni-1', 'dun', 'dun', 'dun')"
    )
    await apply_core_migrations(eski_depo)

    # Sahneyi kur: 'eski-1' yine geride kalmış olsun, 'yeni-1' de bugün onun
    # eski sırrını taşısın. 0006'yı yeniden koşturmak için kaydı da kaldırılır
    # (gerçekte göç tek kez koşar).
    await eski_depo.execute(
        "UPDATE users SET password_hash = NULL, secret_lookup = NULL WHERE id = 'eski-1'"
    )
    await eski_depo.execute(
        "UPDATE users SET password_hash = 'h', secret_lookup = 'pin-arama-degeri' "
        "WHERE id = 'yeni-1'"
    )
    await eski_depo.execute(
        "DELETE FROM schema_migrations WHERE owner = 'core' AND name = ?",
        ("0006_backfill_secret_lookup",),
    )

    await apply_core_migrations(eski_depo)  # patlamamalı

    catisan = await eski_depo.fetch_one("SELECT * FROM users WHERE id = 'eski-1'")
    assert catisan is not None
    assert catisan["secret_lookup"] is None  # atlandı
    assert catisan["pin_lookup"] == "pin-arama-degeri"  # satır DURUYOR


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


async def test_sirsiz_kullanicilar_birbirini_ENGELLEMEZ(eski_depo: Store) -> None:
    """NULL değerler benzersizlik kısıtına takılmaz — sırsız yansıtılan iki
    merkez kaydı (ADR 0021) birbirini engellemez."""
    await apply_core_migrations(eski_depo)
    for user_id in ("sirsiz-1", "sirsiz-2"):
        await eski_depo.execute(
            "INSERT INTO users (id, first_name, last_name, org_scope, status, pin_hash, "
            "pin_lookup, pin_set_at, created_at, updated_at) "
            "VALUES (?, 'Baska', 'Kisi', 'org', 'active', '', ?, 'dun', 'dun', 'dun')",
            (user_id, f"pin-yok:{user_id}"),
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


async def test_goc_sonrasi_eski_kullanici_ORIJINAL_PINIYLE_GIRER(eski_depo: Store) -> None:
    """KİLİTLENME REGRESYONU — 17.08.2026.

    O gün olan şuydu: geride kalmış kullanıcı orijinal PIN'iyle girdi, 0016'dan
    kalan zorlama akışı devreye girdi, YENİ bir sır yazıldı, `secret_lookup`
    değişti ve orijinal PIN "bilinmeyen sır" ile reddedilir oldu.

    Doğru davranış: göç sırrı taşır, kullanıcı ORİJİNAL PIN'iyle DOĞRUDAN girer,
    kimseden yeni sır istenmez ve PIN ikinci kez de çalışır.
    """
    kimlik = Identity(eski_depo, pepper="deneme-pepper")
    # Kaydı gerçek bir PIN'e bağla: `pin_lookup` aynı HMAC ile üretilir.
    # GÖÇTEN ÖNCE yazılır — gerçek kurulumda satır zaten böyle duruyordu.
    from km_core.security.identity import _hasher

    await eski_depo.execute(
        "UPDATE users SET pin_hash = ?, pin_lookup = ? WHERE id = 'eski-1'",
        (_hasher.hash(PIN), kimlik.secret_lookup(PIN)),
    )

    await apply_core_migrations(eski_depo)
    await kimlik.ensure_builtin_roles()

    # Göçün sözü: arama yolu tek sırra indi ve iki sütun AYNI değeri gösteriyor.
    satir = await eski_depo.fetch_one("SELECT * FROM users WHERE id = 'eski-1'")
    assert satir is not None
    assert satir["pin_lookup"] == satir["secret_lookup"]

    sonuc = await kimlik.login(PIN)
    assert sonuc is not None
    assert sonuc.token, "oturum açılmadı — sır belirlemeye zorlanmış olabilir"

    # ORİJİNAL PIN İKİNCİ KEZ DE ÇALIŞIR. Kilitlenmenin görünür belirtisi buydu.
    ikinci = await kimlik.login(PIN)
    assert ikinci is not None and ikinci.token
