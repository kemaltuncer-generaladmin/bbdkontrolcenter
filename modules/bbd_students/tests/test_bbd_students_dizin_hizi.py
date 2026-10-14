"""`bbd_students.directory` — önbellek, tek uçuş ve sorgu bütçesi.

Mobil geçit dizini iki dakikada bir `since` ile, gece tam olarak çeker;
telefonla giriş her denemede `get`/`find_by_phone` sorar. Bu yolların kantine
ve veritabanına kaç kez gittiği burada bağlanır. Depo GERÇEK SQLite'tır,
kantin sahtedir; ağa çıkılmaz.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend.directory import StudentDirectory
from bbd_students_backend.service import StudentService
from bbd_students_fakes import FakeLog

from km_core.contracts.module import ScopedStore
from km_core.store.db import Store

MIGRATIONS = Path(__file__).resolve().parents[1] / "backend" / "migrations"


class CountingStore(ScopedStore):
    """Modül deposu + ifade sayacı."""

    statements: int = 0

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        type(self).statements += 1
        return await super().fetch_all(sql, params)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        type(self).statements += 1
        return await super().fetch_one(sql, params)

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        type(self).statements += 1
        await super().execute(sql, params)


class Canteen:
    def __init__(self, students: list[dict[str, Any]], *, delay: float = 0.0) -> None:
        self.rows = students
        self.delay = delay
        self.list_calls = 0
        self.single_calls = 0

    async def students(self) -> list[dict[str, Any]]:
        self.list_calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return [dict(row) for row in self.rows]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        self.single_calls += 1
        for row in self.rows:
            if row["id"] == opaque_id:
                return dict(row)
        error = RuntimeError("404")
        error.status = 404  # type: ignore[attr-defined]
        raise error

    async def upsert_student(self, opaque_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        for row in self.rows:
            if row["id"] == opaque_id:
                row.update(changes)
        return {"id": opaque_id, **changes}

    async def dashboard(self) -> dict[str, Any]:
        return {}


def kantin(opaque_id: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"id": opaque_id, "displayName": name, "parentPhone": "", "balance": 0,
            "spendingLimit": None, "isBlocked": False, "updatedAt": 1791786600000, **extra}


@pytest.fixture
async def store(tmp_path: Path) -> AsyncIterator[CountingStore]:
    raw = Store(tmp_path / "km.sqlite")
    await raw.open()
    for path in sorted(MIGRATIONS.glob("*.sql")):
        await raw.apply_migration("bbd_students", path.name, path.read_text(encoding="utf-8"))
    CountingStore.statements = 0
    yield CountingStore(raw, "bbd_students")
    await raw.close()


def okul(count: int = 50) -> Canteen:
    return Canteen([kantin(f"STU-{index:03d}", f"Öğrenci {index}",
                           parentPhone=f"532{index:07d}") for index in range(count)])


async def test_sicak_dizin_kantine_ve_veritabanina_GITMEZ(store: CountingStore) -> None:
    canteen = okul()
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    # soğuk: 1 kantin + 5 sorgu (profiller + mezar taşları, km-merkezi-senkron §3.3; sınıf
    # tanımları — göç 008; sınıf kayıtları + birleştirme durumu — sınıf = grup, göç 009).
    # Öğrenci sayısından bağımsız.
    assert len((await directory.list())["students"]) == 50
    assert canteen.list_calls == 1 and store.statements == 5

    store_before = store.statements
    await directory.list()
    await directory.list(since=datetime(2026, 1, 1, tzinfo=UTC))
    assert [row["id"] for row in await directory.find_by_phone("5320000007")] == ["STU-007"]
    assert (await directory.get("STU-010"))["id"] == "STU-010"  # type: ignore[index]
    assert canteen.list_calls == 1
    assert canteen.single_calls == 0
    assert store.statements == store_before


async def test_esZamanli_soguk_cagrilar_TEK_kantin_istegi(store: CountingStore) -> None:
    canteen = okul()
    canteen.delay = 0.05
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    results = await asyncio.gather(*(directory.list() for _ in range(10)))
    assert all(len(result["students"]) == 50 for result in results)
    assert canteen.list_calls == 1


async def test_dizinde_olmayan_ogrenci_canli_okunur(store: CountingStore) -> None:
    canteen = okul(3)
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    await directory.list()
    canteen.rows.append(kantin("STU-YENI", "Yeni Öğrenci"))   # liste önbellekte eski
    assert (await directory.get("STU-YENI"))["id"] == "STU-YENI"  # type: ignore[index]
    assert canteen.single_calls == 1
    assert await directory.get("STU-YOK") is None


async def test_profil_yazilinca_dizin_yeniden_kurulur_kantine_gitmeden(
        store: CountingStore) -> None:
    canteen = okul(5)
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    service = StudentService(canteen=canteen, store=store, log=FakeLog(),
                             on_change=directory.invalidate)
    await directory.list()
    await service.set_curriculum(["STU-001"], {"gradeLevel": 8})
    record = next(row for row in (await directory.list())["students"] if row["id"] == "STU-001")
    assert record["gradeLevel"] == 8
    assert canteen.list_calls == 1          # yalnız profil değişti; kantin listesi geçerli


async def test_kantine_yazilinca_kantin_listesi_de_tazelenir(store: CountingStore) -> None:
    canteen = okul(5)
    directory = StudentDirectory(canteen=canteen, store=store, log=FakeLog())
    service = StudentService(canteen=canteen, store=store, log=FakeLog(),
                             on_change=directory.invalidate)
    await directory.list()
    await service.update_student("STU-002", {"parentPhone": "5059998877"})
    assert [row["id"] for row in await directory.find_by_phone("5059998877")] == ["STU-002"]
    # 1: ilk liste · 2: bırakılan dizin tazelenir. Servisin kantindeki adı
    # artık tekil okumadır (tam liste çekilmez).
    assert canteen.list_calls == 2


async def test_donen_kayit_kopyadir_onbellek_bozulmaz(store: CountingStore) -> None:
    directory = StudentDirectory(canteen=okul(2), store=store, log=FakeLog())
    first = (await directory.list())["students"][0]
    first["firstName"] = "BOZUK"
    first["guardians"].clear()
    first["canteen"]["balance"] = 999
    again = (await directory.list())["students"][0]
    assert again["firstName"] != "BOZUK"
    assert again["guardians"] and again["canteen"]["balance"] == 0


async def test_as_of_kantin_listesinin_okundugu_an(store: CountingStore) -> None:
    directory = StudentDirectory(canteen=okul(1), store=store, log=FakeLog())
    before = datetime.now(UTC).replace(microsecond=0)
    result = await directory.list()
    as_of = datetime.fromisoformat(result["asOf"])
    assert before <= as_of <= datetime.now(UTC)
    # Önbellekten gelen ikinci yanıt aynı anı söyler (liste daha eski olabilir).
    assert (await directory.list())["asOf"] == result["asOf"]


async def test_revision_ayni_goruntude_sabit_yeniden_kurulunca_artar(
        store: CountingStore) -> None:
    directory = StudentDirectory(canteen=okul(3), store=store, log=FakeLog())
    first = await directory.list()
    assert isinstance(first["revision"], int)
    # Aynı anlık görüntü: aynı sürüm (since'li çağrı da aynı görüntüden).
    assert (await directory.list())["revision"] == first["revision"]
    assert (await directory.list(datetime(2020, 1, 1, tzinfo=UTC)))["revision"] \
        == first["revision"]
    # Profil yazıldı: kantin listesi geçerli, görüntü yeniden kurulur → yeni sürüm.
    directory.invalidate(canteen=False)
    second = await directory.list()
    assert second["revision"] > first["revision"]
    directory.invalidate()
    assert (await directory.list())["revision"] > second["revision"]
