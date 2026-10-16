"""Toplu satış — parti dökümü PDF'i (63-finans-pdf).

İşlenmiş ve geri alınmamış satırlar toplanır; hata ve geri alınan satır
sebebiyle yazılır; parti ve satış günü başlıkta, hazırlayan altbilgidedir.
Bilinmeyen parti rapor üretmez; kalan sayfalar yalnız modülün klasöründen.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_bulk_sale_backend.reports import BulkSaleReports


class Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass


class Service:
    async def batch_detail(self, batch_ref: str) -> dict[str, Any]:
        if batch_ref != "B1":
            return {"ok": False, "error": "Parti bulunamadı."}
        item = [{"qty": 2, "name": "Su"}, {"qty": 1, "name": "Poğaça"}]
        return {"ok": True, "batch": {"batch_ref": "B1", "sale_date": "2026-10-16", "mode": "shared",
                                      "created_by": "Ayşe Kantin", "note": "Gezi günü"},
                "entries": [
                    {"student_name": "Çağla Şen", "items": item, "amount": 4500, "status": "created"},
                    {"student_name": "Ömer Işık", "items": item, "amount": 4500, "status": "failed",
                     "reason": "limit aşıldı"},
                    {"student_name": "Gül Can", "items": item, "amount": 4500, "status": "created",
                     "reversed_at": "2026-10-16T12:00:00+00:00", "reversed_reason": "gelmedi"}]}


def text_of(path: str) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        pytest.skip("pdftotext yok")
    out = subprocess.run([binary, "-layout", path, "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def build(tmp_path: Path) -> BulkSaleReports:
    folder = tmp_path / "rapor"
    folder.mkdir(exist_ok=True)
    return BulkSaleReports(service=Service(), log=Log(), fallback_dir=tmp_path / "yedek",
                           folder=lambda: folder)


async def test_parti_dokumu(tmp_path: Path) -> None:
    reports = build(tmp_path)
    result = await reports.produce("B1", author="Ayşe Muhasebe", preview=False)
    assert result["ok"] is True, result
    text = text_of(result["path"])
    assert "Satış günü: 16.10.2026 · Parti: B1 · İşleyen: Ayşe Kantin" in text
    assert "Toplam (1 işlenen)" in text and "45,00" in text
    assert "Hata: limit aşıldı" in text and "Geri alındı: gelmedi" in text
    assert "2× Su, 1× Poğaça" in text and "Not: Gezi günü" in text


async def test_bilinmeyen_parti_ve_sayfa_kapisi(tmp_path: Path) -> None:
    reports = build(tmp_path)
    assert await reports.produce("YOK") == {"ok": False, "error": "Parti bulunamadı."}
    result = await reports.produce("B1")
    assert result["ok"] is True
    if not shutil.which("pdftoppm"):
        return
    assert (await reports.pages(result["path"], 1, 1))["ok"] is True
    outside = tmp_path / "baska.pdf"
    outside.write_bytes(Path(result["path"]).read_bytes())
    assert (await reports.pages(str(outside), 1, 1))["ok"] is False
