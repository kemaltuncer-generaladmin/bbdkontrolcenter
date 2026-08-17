"""Arayüzden değişen ayarların durduğu çekirdek deposu (ADR 0018 §4).

NEDEN DOSYAYA YAZILMIYOR. `config/local.yaml` elle düzenlenen, git dışı ve
yorum taşıyan bir dosyadır. Program onu yeniden yazsaydı yorumlar ve elle
yapılmış düzenlemeler kaybolurdu. Bu yüzden ekrandan değişen ayar buraya
yazılır ve zincirde `local.yaml`'ı **ezer**:

    default.yaml → environments/<env>.yaml → local.yaml → BURASI → ortam değişkeni

Tablo ADR'nin dediği dört alanı taşır: anahtar, değer, kim, ne zaman. "Kim" ve
"ne zaman" olmadan bir ayarın neden değiştiğini kimse bulamaz; denetim izine de
ayrıca kayıt düşer (`settings.update`).

SIR BURAYA YAZILMAZ (K8). Değerler düz JSON olarak durur ve şifrelenmez; şifre,
token ve anahtar `secrets` kasasına aittir. Ayar sözleşmesinde sır tipi yoktur
(ADR 0018 §5) ve ekran sır yazan bir alan çizmez.

GÖÇ ÇEKİRDEĞİNDİR. `settings` tablosu `mod_<id>_` önekli bir modül tablosu
değildir; sahibi `core`'dur ve `Store.apply_migration` ad denetimi bu sahiplik
üzerinden geçer (K5). Göç adı `km_core/security/migrations.py` içindeki
0001–0003 dizisiyle çakışmasın diye 0010'dan başlar.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from km_core.store.db import Store

log = structlog.get_logger("km.settings")

OWNER = "core"

#: Göç adı. Numara aralığı bilerek boşluklu: kimlik göçleri 0001–0003'ü
#: kullanıyor ve ileride oraya yeni bir göç eklendiğinde ad çakışmasın.
MIGRATION_NAME = "0010_settings_store"

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,       -- 'app.name', 'modules.antivirus.schedule'
    value       TEXT NOT NULL,          -- JSON; sır DEĞİL (K8)
    updated_by  TEXT,                   -- kim (users.id); açılıştan gelirse NULL
    updated_at  TEXT NOT NULL           -- ne zaman
);

CREATE INDEX IF NOT EXISTS idx_settings_updated ON settings (updated_at);
"""


async def apply_settings_migrations(store: Store) -> list[str]:
    """Uygulanmamışsa `settings` tablosunu açar; uygulanan göçleri döndürür."""
    applied = await store.applied_migrations(OWNER)
    if MIGRATION_NAME in applied:
        return []
    await store.apply_migration(OWNER, MIGRATION_NAME, MIGRATION_SQL)
    log.info("çekirdek göçü uygulandı", migration=MIGRATION_NAME)
    return [MIGRATION_NAME]


class SettingsStore:
    """`settings` tablosunun okunur/yazılır görünümü.

    Değer JSON olarak saklanır: ayar tipleri metin, sayı, mantıksal, liste
    olabiliyor ve hepsini metne çevirip geri okumaya çalışmak "false" ile
    `False`'ı ayırt edemez hâle getirirdi.
    """

    __slots__ = ("_store",)

    def __init__(self, store: Store) -> None:
        self._store = store

    async def values(self) -> dict[str, Any]:
        """Tüm geçersizler ayıklanmış anahtar → değer eşlemesi.

        BOZUK SATIR TÜM EKRANI DÜŞÜRMEZ (K7): çözülemeyen JSON atlanır ve
        loglanır; geri kalan ayarlar okunmaya devam eder.
        """
        rows = await self._store.fetch_all("SELECT key, value FROM settings")
        out: dict[str, Any] = {}
        for row in rows:
            key = str(row["key"])
            try:
                out[key] = json.loads(str(row["value"]))
            except ValueError:
                log.warning("ayar değeri okunamadı, atlandı", key=key)
        return out

    async def rows(self) -> list[dict[str, Any]]:
        """Kim/ne zaman bilgisiyle birlikte ham satırlar."""
        return await self._store.fetch_all(
            "SELECT key, value, updated_by, updated_at FROM settings ORDER BY key"
        )

    async def put(self, key: str, value: Any, *, actor_id: str | None) -> None:
        await self._store.execute(
            "INSERT INTO settings (key, value, updated_by, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_by = excluded.updated_by, "
            "updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), actor_id),
        )

    async def clear(self, key: str) -> None:
        """Ezmeyi kaldırır: değer bir alt katmana (dosya) döner.

        Satır silinir çünkü BURADAKİ SATIRIN VARLIĞI "ezildi" demektir; boş bir
        değer yazmak, ayarı boşa çekmekle ezmeyi kaldırmayı aynı şey yapardı.
        Ne olduğu kaybolmaz: değişiklik denetim izine düşer.
        """
        await self._store.execute("DELETE FROM settings WHERE key = ?", (key,))
