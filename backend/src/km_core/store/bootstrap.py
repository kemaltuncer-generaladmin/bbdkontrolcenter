"""Şemayı kuran TEK yer — hem yerel ayna hem merkez buradan geçer.

NEDEN TEK YER. Merkezdeki PostgreSQL ile yerel SQLite aynanın şeması birebir
aynı olmak zorundadır; ayrı iki kurulum yolu yazılsaydı biri diğerinden sessizce
ayrışırdı ve fark ancak bir satır merkeze gidip "böyle sütun yok" dediğinde,
yani üretimde görünürdü.

Sıra `km_core/http/app.py` içindeki açılış sırasının aynısıdır:

    1. `CORE_SCHEMA`            — kullanıcı, rol, oturum, denetim, kasa, çıktı
    2. çekirdek göçleri         — sır sütunları, revizyon, kadro yansıtması…
    3. ayar deposu göçü         — `settings`
    4. modül göçleri            — `modules/*/backend/migrations/*.sql`

Motoru bilmez: `Store` (SQLite) ve `PostgresStore` aynı yüzeyi sunar, lehçe
farkını `dialect.py` kapatır.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from km_core.store.base import StoreLike

log = structlog.get_logger("core.store.bootstrap")


async def apply_module_migrations(store: StoreLike, modules_dir: Path) -> list[str]:
    """`modules/*/backend/migrations/*.sql` dosyalarını uygular.

    Manifest OKUNMAZ, klasör taranır. Gerekçe: bu fonksiyon merkezde de koşar
    ve orada modül kodu yüklenmez — manifest doğrulaması, bağımlılık çözümü ve
    `enabled` denetimi kurulum tarafının işidir.

    KAPATILMIŞ MODÜLÜN TABLOSU DA AÇILIR. Bilinçlidir: modül yarın açıldığında
    merkezde tablosu hazır olsun; boş bir tablo zarar vermez, eksik olan tablo
    ilk yazmada kurulumu düşürür.
    """
    applied: list[str] = []
    for directory in sorted(modules_dir.glob("*/backend/migrations")):
        if not directory.is_dir():
            continue
        module_id = directory.parents[1].name
        done = await store.applied_migrations(module_id)
        for path in sorted(directory.glob("*.sql")):
            if path.name in done:
                continue
            await store.apply_migration(module_id, path.name, path.read_text(encoding="utf-8"))
            log.info("göç uygulandı", module=module_id, migration=path.name)
            applied.append(f"{module_id}/{path.name}")
    return applied


async def build_schema(store: StoreLike, modules_dir: Path | None = None) -> list[str]:
    """Şemanın tamamını kurar; uygulanan göçlerin adlarını döndürür.

    `modules_dir` verilmezse yalnız çekirdek kurulur — merkez servisi kendi
    testlerinde bunu kullanır.
    """
    # Geç import: bu modül `db.py` tarafından da görülebilecek kadar aşağıda
    # durur ve döngüsel import'a girmemelidir.
    from km_core.config.settings_store import apply_settings_migrations
    from km_core.security.migrations import apply_core_migrations
    from km_core.store.db import CORE_SCHEMA

    await store.execute_script(CORE_SCHEMA)

    applied: list[str] = []
    applied += await apply_core_migrations(store)
    applied += await apply_settings_migrations(store)
    if modules_dir is not None:
        applied += await apply_module_migrations(store, modules_dir)
    return applied
