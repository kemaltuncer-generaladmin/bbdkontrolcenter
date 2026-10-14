"""Sınıfın DÖNEMİ (akademik yıl) — kural + mevcut sınıflara bir kerelik atama (göç 011).

Kullanıcı (2026-10-14, iş 30): "tüm mevcut sınıflara da 2026-2027 dönemini ata."
Dönem "YYYY-YYYY" biçimindedir (ikinci yıl = birinci + 1) ve 1 Eylül'de döner
(Europe/Istanbul): 2026-10 → "2026-2027", 2027-08 → "2026-2027", 2027-09 → "2027-2028".
Bu kural Akademi'nin müfredat setindekiyle (`academicYearOf`) aynıdır.

    current_year(today)   → "2026-2027"
    parse_year(value)     → "YYYY-YYYY" ya da ValueError (servis GroupError'a çevirir)
    year_choices(today)   → seçicide gösterilecek dönemler (güncel ±)
    YearBackfill.run()    → dönemi BOŞ sınıflara BACKFILL_YEAR yazar (tek işlem)

`YearBackfill` modül açılışında koşar (`module.class_upkeep`); idempotenttir:
yalnız `academic_year` boş (NULL ya da '') satırlara yazar. Yeni kod hiçbir
sınıfı dönemsiz bırakmaz (oluştururken varsayılan güncel dönem, düzenlemede boş
kabul edilmez); boş satır ancak eski sürümün (0.5.13) yazdığı satırdır.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any

#: Kullanıcı kararı: mevcut bütün sınıflar bu döneme atanır.
BACKFILL_YEAR = "2026-2027"
ACTOR = "sistem: sınıf dönemi"

_YEAR = re.compile(r"^(\d{4})-(\d{4})$")


def current_year(today: date) -> str:
    """Günün dönemi — 1 Eylül ve sonrası yeni dönem."""
    start = today.year if today.month >= 9 else today.year - 1
    return f"{start}-{start + 1}"


def parse_year(value: Any) -> str:
    """Gövdedeki dönem → "YYYY-YYYY". Boş, bozuk ya da ardışık olmayan yıl ValueError."""
    text = str(value or "").strip()
    match = _YEAR.match(text)
    if match is None:
        raise ValueError("Dönem “2026-2027” biçiminde olmalı.")
    first, second = int(match.group(1)), int(match.group(2))
    if second != first + 1 or not 2000 <= first <= 2100:
        raise ValueError("Dönemin ikinci yılı birinciden bir fazla olmalı (ör. 2026-2027).")
    return text


def year_choices(today: date, *, extra: list[str] | None = None) -> list[str]:
    """Seçicideki dönemler: önceki iki dönem, güncel, sonraki dönem + verideki diğerleri.

    Yeniden eskiye sıralı ("2027-2028", "2026-2027", …).
    """
    start = int(current_year(today)[:4])
    found = {f"{year}-{year + 1}" for year in range(start - 2, start + 2)}
    for value in extra or []:
        if value and _YEAR.match(str(value)):
            found.add(str(value))
    return sorted(found, reverse=True)


class YearBackfill:
    """Dönemi boş sınıflara `BACKFILL_YEAR` — tek işlem, revizyon +1, `group.changed`."""

    def __init__(self, *, store: Any, groups: Any, log: Any) -> None:
        self._store = store
        #: `GroupService` — sayaç (`_bump`), outbox (`_emit`), yayın (`after_commit`).
        self._groups_service = groups
        self._log = log
        self._groups = store.table("group")
        self._audit = store.table("group_log")

    async def pending(self) -> int:
        row = await self._store.fetch_one(
            f"SELECT COUNT(*) AS n FROM {self._groups} "
            f"WHERE academic_year IS NULL OR academic_year = ''")
        return int((row or {}).get("n") or 0)

    async def run(self, *, year: str = BACKFILL_YEAR, actor: str = ACTOR) -> int:
        """Dönemi boş satırları doldurur; yazılan satır sayısını döner (0 = yapılacak yok)."""
        if await self.pending() == 0:
            return 0
        try:
            return await self._run_in(year=year, actor=actor)
        except _Nothing:
            return 0

    async def _run_in(self, *, year: str, actor: str) -> int:
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        async with self._store.transaction():
            # Sayaç kilidi eşzamanlı koşuları sıralar; sayım İŞLEMİN İÇİNDE yeniden yapılır.
            revision = await self._groups_service._bump()
            count = await self.pending()
            if count == 0:
                # Başka bir koşu bitirdi: boş işlem — revizyon harcanmasın.
                raise _Nothing
            await self._store.execute(
                f"UPDATE {self._groups} SET academic_year = ?, revision = ? "
                f"WHERE academic_year IS NULL OR academic_year = ''", (year, revision))
            await self._store.execute(
                f"INSERT INTO {self._audit} (at, actor, action, group_id, revision, detail) "
                f"VALUES (?, ?, 'year_backfill', '', ?, ?)",
                (stamp, actor[:160], revision,
                 json.dumps({"academicYear": year, "classes": count}, ensure_ascii=False)))
            await self._groups_service._emit(revision)
        await self._groups_service.after_commit(revision)
        self._log.info("mevcut sınıflara dönem atandı", donem=year, sinif=count)
        return count


class _Nothing(Exception):
    """İşlemin içinde görüldü: yapılacak yok — geri al."""
