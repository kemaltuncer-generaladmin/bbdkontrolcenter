"""Senkron çıktı kaydını async depoya taşıyan kuyruk.

NEDEN GEREKLİ. `outputs_log` senkrondur ve öyle kalmak zorundadır: dosyayı
yazan derin kod (rapor üreticileri, PDF yazıcıları) async değil. Depo ise
async — hem `Store` hem `PostgresStore`. Aradaki tek köprü bu kuyruk.

Eskiden köprü yoktu çünkü gerek de yoktu: `outputs_log` kendi `sqlite3`
bağlantısını açıp yazıyordu. Bu yol motora çivilenmişti; backend sunucuya
taşınıp depo PostgreSQL olunca çıktılar üretilmeye devam eder ama listede HİÇ
görünmezdi.

## Kayıp olmaz, ama bekleyen de olmaz

Satır kuyruğa düşer ve `record_write` hemen döner — dosyayı yazan kod
veritabanı bekleyerek yavaşlamaz. Arka plandaki tüketici satırları sırayla
işler. Kapanışta kuyruk BOŞALTILIR; yarım kalan kayıt bırakılmaz.

Kuyruk sınırlıdır. Dolarsa EN ESKİ satır düşer ve bu loglanır: sınırsız kuyruk,
depo uzun süre erişilemezse belleği şişirir ve asıl işi (dosya üretmeyi)
öldürürdü. Kayıt kaybı dosyayı götürmez (K7).
"""

from __future__ import annotations

import asyncio
from collections import deque

import structlog

from km_core.files.outputs_log import OUTPUT_COLUMNS, OutputRow, OutputWriter
from km_core.store.base import StoreLike

log = structlog.get_logger("km.files.outputs.queue")

#: Bekleyen en fazla satır. Normalde kuyruk hep boştur; bu sınır yalnız depo
#: erişilemezken belleği korur.
MAX_PENDING = 1000

_INSERT = (
    "INSERT INTO outputs (id, created_at, user_id, source, kind, title, path, "
    "bytes, pages, params_digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class OutputsQueue:
    """`outputs` satırlarını arka planda depoya yazar."""

    def __init__(self, store: StoreLike) -> None:
        self._store = store
        self._pending: deque[OutputRow] = deque(maxlen=MAX_PENDING)
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._dropped = 0

    def writer(self) -> OutputWriter:
        """`outputs_log.use_writer()` içine verilecek senkron kapı."""

        def write(row: OutputRow) -> None:
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
                log.warning("çıktı kaydı kuyruğu dolu — en eski satır düştü",
                            dropped=self._dropped)
            self._pending.append(row)
            self._wakeup.set()

        return write

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="outputs-queue")

    async def stop(self) -> None:
        """Tüketiciyi durdurur ve kuyruğu BOŞALTIR."""
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._drain()

    async def _run(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            await self._drain()

    async def _drain(self) -> None:
        while self._pending:
            row = self._pending.popleft()
            try:
                await self._store.execute(
                    _INSERT, tuple(row[column] for column in OUTPUT_COLUMNS)
                )
            except Exception as failure:  # noqa: BLE001 — kayıt kaybı dosyayı götürmez (K7)
                log.warning("çıktı kaydı yazılamadı", path=row.get("path"),
                            error=str(failure))
