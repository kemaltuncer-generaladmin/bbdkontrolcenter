"""Ders Takvimi — salt okunur ayna.

Sınanan tek soru: kaynak ne verirse versin, ekran BOŞ DÖNMEZ ve neyin
olduğunu söyler. Boş bir takvim ile ulaşılamayan bir takvim kullanıcı için
aynı şey değildir.
"""

from __future__ import annotations

from typing import Any

from class_schedule_backend.service import DAYS, ScheduleService


class FakeLog:
    def __init__(self) -> None:
        self.records: list[str] = []

    def info(self, message: str, **fields: Any) -> None:
        self.records.append(message)

    def warning(self, message: str, **fields: Any) -> None:
        self.records.append(f"{message} {fields}")

    def error(self, message: str, **fields: Any) -> None:
        self.records.append(f"{message} {fields}")


WEEK = {
    "times": {"mon": [{"id": "mon-0840", "time": "08:40", "label": "teneffüs"}]},
    "groups": [{"id": "ilayda", "name": "İlayda"}],
    "settings": {"enabled": True},
}


async def test_zil_verisini_oldugu_gibi_gosterir() -> None:
    async def read_week() -> dict[str, Any]:
        return WEEK

    result = await ScheduleService(log=FakeLog(), read_week=read_week).read()

    assert result["available"] is True
    assert result["times"]["mon"][0]["time"] == "08:40"
    assert [item["name"] for item in result["groups"]] == ["İlayda"]
    # Eksik günler boş listeyle tamamlanır; ekran her gün için sütun çizebilsin.
    assert set(result["times"]) == set(DAYS)
    assert result["times"]["sun"] == []


async def test_senkron_yetenek_de_kabul_edilir() -> None:
    """Yetenek `async` olmak zorunda değil; ikisi de çalışmalı."""
    result = await ScheduleService(log=FakeLog(), read_week=lambda: WEEK).read()
    assert result["available"] is True


async def test_zil_modulu_yoksa_nedeni_soylenir() -> None:
    result = await ScheduleService(log=FakeLog(), read_week=None).read()

    assert result["available"] is False
    assert "Zil Sistemi" in result["reason"]
    assert result["times"] == {day: [] for day in DAYS}


async def test_kaynak_patlarsa_ekran_dusmez() -> None:
    async def broken() -> dict[str, Any]:
        raise RuntimeError("bağlantı koptu")

    log = FakeLog()
    result = await ScheduleService(log=log, read_week=broken).read()

    assert result["available"] is False
    assert "bağlantı koptu" in result["reason"]
    assert log.records                       # sessizce yutulmadı


async def test_beklenmedik_yanit_bos_ekran_uretmez() -> None:
    result = await ScheduleService(log=FakeLog(), read_week=lambda: ["liste"]).read()

    assert result["available"] is False
    assert result["reason"] != ""


async def test_bozuk_alanlar_toparlanir() -> None:
    """Kaynak yarım veri verse bile ekran çizilebilmeli."""
    result = await ScheduleService(
        log=FakeLog(),
        read_week=lambda: {"times": "sözlük değil", "groups": None},
    ).read()

    assert result["available"] is True
    assert result["times"] == {day: [] for day in DAYS}
    assert result["groups"] == []


async def test_yazma_yuzeyi_yok() -> None:
    """Salt okunurluk sözde değil, yüzeyde olmalı."""
    surface = {name for name in dir(ScheduleService) if not name.startswith("_")}
    assert surface == {"read"}
