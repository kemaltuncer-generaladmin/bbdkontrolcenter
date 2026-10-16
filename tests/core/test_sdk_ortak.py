"""km_sdk ortak yardımcıları (ADR 0033 §7) — kopyalardan taşınan davranış.

`as_int`/`to_kurus` 14/11 mağaza modülünde, rapor basma kapısı 17, yazıcı
durumu 19 modülde birebir kopyaydı. Davranış aynen korunmalı: bu testler
kopyaların sözleşmesini tek yerde sabitler.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from km_sdk import as_int, print_report_file, printer_status_of, to_kurus


@pytest.mark.parametrize(("value", "expected"), [
    ("12", 12), (" 12 ", 12), ("12.7", 12), ("12,7", 12), (7, 7), (None, 0), ("abc", 0), ("", 0),
])
def test_as_int(value: Any, expected: int) -> None:
    assert as_int(value) == expected


def test_as_int_varsayilan() -> None:
    assert as_int("yok", default=-1) == -1


@pytest.mark.parametrize(("value", "expected"), [
    ("1234.35", 123435), ("1.234,35", 123435), ("1,234.35", 123435), ("12,5", 1250),
    ("0.005", 1), (" 10 ", 1000), (None, None), ("", None), ("abc", None), (3, 300),
])
def test_to_kurus(value: Any, expected: int | None) -> None:
    assert to_kurus(value) == expected


class FakePrinter:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[Path, str, int]] = []
        self.fail = fail

    async def print_file(self, path: Path, *, title: str, copies: int) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("kuyruk dolu")
        self.calls.append((path, title, copies))
        return {"job": "J-1"}

    async def status(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("cups yok")
        return {"ready": True, "name": "HP"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_rapor_klasorundeki_dosya_basilir(tmp_path: Path) -> None:
    report = tmp_path / "rapor.pdf"
    report.write_bytes(b"%PDF")
    printer = FakePrinter()
    result = _run(print_report_file(printer, str(report), allowed_dir=lambda: tmp_path,
                                    copies=50, fail=str))
    assert result == {"ok": True, "job": "J-1", "name": "rapor.pdf"}
    assert printer.calls == [(report.resolve(), "rapor.pdf", 20)]  # kopya 1..20


def test_klasor_disi_dosya_basilmaz(tmp_path: Path) -> None:
    inside = tmp_path / "raporlar"
    inside.mkdir()
    outside = tmp_path / "gizli.txt"
    outside.write_text("x")
    printer = FakePrinter()
    result = _run(print_report_file(printer, str(outside), allowed_dir=lambda: inside, fail=str))
    assert result["ok"] is False and "güvenlik" in result["error"]
    # `../` ile kaçış da çözülmüş yola bakılarak engellenir.
    sneaky = inside / ".." / "gizli.txt"
    assert _run(print_report_file(printer, str(sneaky), allowed_dir=lambda: inside, fail=str))["ok"] is False
    assert printer.calls == []


def test_yazici_yoksa_klasor_hic_cozulmez(tmp_path: Path) -> None:
    def explode() -> Path:
        raise AssertionError("yazıcı yokken rapor klasörü okunmamalı")

    result = _run(print_report_file(None, str(tmp_path / "x.pdf"), allowed_dir=explode, fail=str))
    assert result == {"ok": False, "error": "Yazıcı yeteneği bu kurulumda yok."}


def test_eksik_dosya_ve_yazici_hatasi(tmp_path: Path) -> None:
    missing = _run(print_report_file(FakePrinter(), str(tmp_path / "yok.pdf"),
                                     allowed_dir=lambda: tmp_path, fail=str))
    assert missing == {"ok": False, "error": "Basılacak rapor bulunamadı."}
    report = tmp_path / "r.pdf"
    report.write_bytes(b"%PDF")
    failed = _run(print_report_file(FakePrinter(fail=True), str(report), allowed_dir=lambda: tmp_path,
                                    fail=lambda error: f"hata: {error}"))
    assert failed == {"ok": False, "error": "hata: kuyruk dolu"}


def test_yazici_durumu() -> None:
    assert _run(printer_status_of(FakePrinter(), fail=str)) == {"ready": True, "name": "HP"}
    assert _run(printer_status_of(None, fail=str)) == {"ready": False,
                                                       "error": "Yazıcı yeteneği bu kurulumda yok."}
    assert _run(printer_status_of(FakePrinter(fail=True), fail=lambda e: f"! {e}")) == {
        "ready": False, "error": "! cups yok"}
