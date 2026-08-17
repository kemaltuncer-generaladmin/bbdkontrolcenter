"""Merkezdeki PostgreSQL deposu — doğruluk kaynağı.

`km_core/store/db.py` → yerel SQLite AYNA (okuma).
`km_core/store/postgres.py` → merkez (yazma, doğruluk kaynağı).
`km_platform/database` → yönetilen UZAK BBD/BLD veritabanları. Üçü ayrı şeydir.

Arayüz `Store` ile BİREBİR AYNIDIR; çağıran kod hangisiyle konuştuğunu bilmez.
Modüllerin yazdığı SQLite lehçesi `dialect.py` ile çevrilir, böylece 49 modülün
383 ifadesine ve 51 göç dosyasına dokunulmaz (K6).

## Tip uyarlaması neden var

SQLite gevşektir: `True` yazarsın, `1` okursun. asyncpg katıdır ve `integer`
sütununa `bool` verilince yazma anında patlar. Bu fark SQL'de görünmez, veride
görünür — bu yüzden `_adapt` parametreleri sınırda düzeltir ve düzeltemediğini
SQL'i ve parametre tiplerini söyleyerek yükseltir. Sessiz kayıp olmaz.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import asyncpg
import structlog

from km_core.store.db import _assert_module_tables
from km_core.store.dialect import to_postgres, translate_ddl

log = structlog.get_logger("core.store.postgres")

#: Havuz sınırları. Merkez servisi tek süreçtir; havuz onun eşzamanlı
#: isteklerini karşılar, kurulum sayısını değil.
POOL_MIN = 1
POOL_MAX = 10


class PostgresStore:
    """`Store` ile aynı yüzeyi sunan PostgreSQL deposu."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool: asyncpg.Pool[Any] | None = None
        self._primary_keys: dict[str, list[str]] = {}

    # ----------------------------------------------------------- yaşam döngüsü

    async def open(self) -> None:
        self._pool = await asyncpg.create_pool(self.dsn, min_size=POOL_MIN, max_size=POOL_MAX)
        await self.execute_script(_CORE_BOOTSTRAP)
        await self._load_primary_keys()

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @property
    def pool(self) -> asyncpg.Pool[Any]:
        if self._pool is None:
            raise RuntimeError("Depo açılmadı.")
        return self._pool

    # ------------------------------------------------------------- birincil anahtar

    async def _load_primary_keys(self) -> None:
        """Tablo → birincil anahtar sütunları. `INSERT OR REPLACE` bunu ister.

        Önbelleğe alınır çünkü her yazmada katalog sorgulamak yazma yolunu iki
        kat yavaşlatırdı. Göç uygulandığında tazelenir.
        """
        rows = await self.pool.fetch(
            """
            SELECT c.relname AS tablo, a.attname AS sutun, array_position(i.indkey, a.attnum) AS sira
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
            WHERE i.indisprimary AND n.nspname = current_schema()
            ORDER BY c.relname, sira
            """
        )
        keys: dict[str, list[str]] = {}
        for row in rows:
            keys.setdefault(str(row["tablo"]), []).append(str(row["sutun"]))
        self._primary_keys = keys

    def _pk_of(self, table: str) -> list[str]:
        return self._primary_keys.get(table.lower(), [])

    # -------------------------------------------------------------------- sorgu

    def _translate(self, sql: str) -> str:
        return to_postgres(sql, pk_of=self._pk_of)

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        rows = await self.pool.fetch(self._translate(sql), *_adapt(params))
        return [dict(row) for row in rows]

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        row = await self.pool.fetchrow(self._translate(sql), *_adapt(params))
        return dict(row) if row is not None else None

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        statement = self._translate(sql)
        try:
            await self.pool.execute(statement, *_adapt(params))
        except asyncpg.PostgresError as error:
            raise _explain(error, statement, params) from error

    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        statement = self._translate(sql)
        batch = [_adapt(row) for row in rows]
        if not batch:
            return
        try:
            await self.pool.executemany(statement, batch)
        except asyncpg.PostgresError as error:
            raise _explain(error, statement, batch[0]) from error

    async def execute_script(self, sql: str) -> None:
        """Çok ifadeli DDL. Göç dosyaları buradan geçer."""
        await self.pool.execute(translate_ddl(sql))

    async def table_columns(self, table: str) -> set[str]:
        """`Store.table_columns` ile aynı soru, PostgreSQL'in kataloğundan."""
        rows = await self.pool.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = $1",
            table,
        )
        return {str(row["column_name"]) for row in rows}

    # ------------------------------------------------------------------- göçler

    async def applied_migrations(self, owner: str) -> set[str]:
        rows = await self.fetch_all(
            "SELECT name FROM schema_migrations WHERE owner = ?", (owner,)
        )
        return {str(row["name"]) for row in rows}

    async def apply_migration(self, owner: str, name: str, sql: str) -> None:
        """Göçü uygular ve kaydeder. Modül göçleri ad denetiminden geçer (K5).

        Denetim `Store` ile AYNI fonksiyondan gelir — iki motor için iki ayrı
        kural yazılsaydı biri gevşer ve K5 o taraftan delinirdi.
        """
        if owner != "core":
            _assert_module_tables(owner, sql)

        async with self.pool.acquire() as connection, connection.transaction():
            await connection.execute(translate_ddl(sql))
            await connection.execute(
                "INSERT INTO schema_migrations (owner, name, applied_at) "
                "VALUES ($1, $2, now()::text)",
                owner,
                name,
            )
        # Yeni tablo açılmış olabilir; `INSERT OR REPLACE` çevirisi anahtarı bilmeli.
        await self._load_primary_keys()


# Çekirdek şema `db.py`'deki CORE_SCHEMA'dan gelir; burada yalnız göç defteri
# açılır, çünkü `applied_migrations` şemadan ÖNCE sorulur.
_CORE_BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    owner       TEXT NOT NULL,
    name        TEXT NOT NULL,
    applied_at  TEXT NOT NULL,
    PRIMARY KEY (owner, name)
);
"""


def _adapt(params: Sequence[Any]) -> list[Any]:
    """SQLite'ın gevşek değerlerini asyncpg'nin beklediği tiplere çevirir.

    YALNIZ kesin olan dönüşüm yapılır. `bool` → `int` güvenlidir: SQLite zaten
    böyle saklıyordu, sütun tipi BIGINT'tir ve `True`/`1` aynı satırı üretir.

    Tahmin YAPILMAZ. Örneğin `int` değeri TEXT sütununa uydurmak için `str`'e
    çevirmek, `id=5` ile `id='5'` satırlarını sessizce birleştirirdi; öyle bir
    uyuşmazlık `_explain` ile GÖRÜNÜR hâle gelir ve çağıran düzeltilir.
    """
    return [int(value) if isinstance(value, bool) else value for value in params]


def _explain(
    error: asyncpg.PostgresError, sql: str, params: Sequence[Any]
) -> RuntimeError:
    """Tip uyuşmazlığını SQL'i ve parametre TİPLERİYLE anlatır.

    Parametre DEĞERLERİ loglanmaz: öğrenci adı, veli telefonu ve PIN hash'i bu
    yoldan geçiyor (`km_core/files/private.py` denetim bulgusu).
    """
    types = ", ".join(type(value).__name__ for value in params)
    log.error("merkez yazması reddedildi", sql=sql, param_types=types, error=str(error))
    return RuntimeError(
        f"Merkez veritabanı ifadeyi reddetti: {error}\n"
        f"SQL: {sql}\nParametre tipleri: [{types}]"
    )
