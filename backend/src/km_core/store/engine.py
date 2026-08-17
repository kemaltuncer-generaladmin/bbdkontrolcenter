"""Hangi depo motoruyla açılacağına karar veren TEK yer.

Kontrol Merkezi iki biçimde koşar:

  · **KM Sunucu** (Coolify) — doğruluk kaynağı. `PostgresStore`, veri
    PostgreSQL'de. Bütün kurulumlar buraya bakar (ADR 0026).
  · **Yerel** — geliştirme, testler ve betikler. `Store`, veri SQLite'ta.

Seçim ORTAM DEĞİŞKENİYLE yapılır, ayar dosyasıyla değil: ayar deposunun kendisi
veritabanında duruyor ve hangi veritabanı olduğunu okumadan önce bilmek gerek —
"ayarı okumak için ayarı okumak" döngüsü. Değişken yoksa SQLite seçilir, yani
bugüne kadar çalışan hiçbir şey değişmez.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import structlog

from km_core.store.base import StoreLike

log = structlog.get_logger("core.store.engine")

#: Motor seçimi: `postgres` ya da `sqlite` (varsayılan).
ENGINE_ENV = "KM_STORE_ENGINE"

#: PostgreSQL bağlantı adresi. `KM_STORE_ENGINE=postgres` ise ZORUNLUDUR.
DSN_ENV = "KM_CENTRAL_DSN"

EngineName = Literal["sqlite", "postgres"]


class EngineError(RuntimeError):
    """Motor seçimi eksik ya da tutarsız.

    Açılışta ve GÜRÜLTÜLÜ patlar. Sessizce SQLite'a düşmek çok daha kötü
    olurdu: sunucu ayağa kalkar, kimse fark etmez ve herkesin ortak verisi
    konteynerin geçici diskine yazılmaya başlardı — ilk dağıtımda silinmek
    üzere.
    """


def selected_engine() -> EngineName:
    value = os.environ.get(ENGINE_ENV, "").strip().lower()
    if not value or value == "sqlite":
        return "sqlite"
    if value == "postgres":
        return "postgres"
    raise EngineError(
        f"{ENGINE_ENV} yalnız 'sqlite' ya da 'postgres' olabilir; '{value}' geldi."
    )


def create_store(sqlite_path: Path) -> StoreLike:
    """Seçili motorun deposunu kurar. AÇMAZ — açmak çağıranın işi."""
    # Geç import: `asyncpg` yalnız sunucuda gerekiyor ve yerel kurulumda
    # kurulu olmayabilir; modül başında import etmek SQLite yolunu da düşürürdü.
    engine = selected_engine()
    if engine == "sqlite":
        from km_core.store.db import Store

        log.info("depo motoru", engine="sqlite", path=str(sqlite_path))
        return Store(sqlite_path)

    dsn = os.environ.get(DSN_ENV, "").strip()
    if not dsn:
        raise EngineError(
            f"{ENGINE_ENV}=postgres seçildi ama {DSN_ENV} tanımlı değil. "
            "Sunucu bağlanacağı veritabanını TAHMİN ETMEZ."
        )

    from km_core.store.postgres import PostgresStore

    log.info("depo motoru", engine="postgres", host=_host_of(dsn))
    return PostgresStore(dsn)


def _host_of(dsn: str) -> str:
    """Adresin yalnız sunucu/veritabanı kısmı — PAROLA LOGLANMAZ."""
    tail = dsn.rsplit("@", 1)[-1]
    return tail or "?"
