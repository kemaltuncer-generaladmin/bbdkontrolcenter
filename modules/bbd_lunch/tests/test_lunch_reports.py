"""Öğle yemeği — PDF raporları (63-finans-pdf).

Aylık rapor gün gün porsiyon/tutar ve öğrenci başına (sınıf ara toplamı)
toplar; yalnız kantine işlenmiş ve geri alınmamış satırlar toplama girer.
Gün dökümü başarısız ve geri alınan satırı sebebiyle yazar. Süzgeç (ay/gün)
başlıkta, hazırlayan altbilgidedir. Kalan sayfalar yalnız modülün klasöründen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_lunch_backend.reports import LunchReports, month_bounds, tr_day


class Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass


def text_of(path: Path) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        pytest.skip("pdftotext yok")
    out = subprocess.run([binary, "-layout", str(path), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


async def seed(store: Any) -> None:
    batches, entries = store.table("batch"), store.table("entry")
    await store.execute(
        f"INSERT INTO {batches} (batch_ref, service_date, product_id, product_name, unit_price, "
        f"created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("B1", "2026-10-15", 9, "Öğle Yemeği", 8500, "2026-10-15T09:00:00+00:00", "Ayşe Mutfak"))
    rows = [
        ("L1", "2026-10-15", "k1", "Çağla Şen", "8-A", 8500, "created", None),
        ("L2", "2026-10-15", "k2", "Ömer Işık", "7-B", 8500, "duplicate", None),
        ("L3", "2026-10-15", "k3", "Gül Can", "7-B", 8500, "failed", None),
        ("L4", "2026-10-15", "k4", "Ali Er", "8-A", 8500, "created", "2026-10-15T12:00:00+00:00"),
        ("L5", "2026-10-16", "k1", "Çağla Şen", "8-A", 8500, "created", None),
        ("L6", "2026-09-30", "k1", "Çağla Şen", "8-A", 8500, "created", None),
    ]
    for local_id, day, kantin, name, klass, amount, status, reversed_at in rows:
        await store.execute(
            f"INSERT INTO {entries} (local_id, batch_ref, service_date, kantin_id, student_name, "
            f"class_name, amount, status, reason, reversed_at, reversed_reason, created_at) "
            f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (local_id, "B1", day, kantin, name, klass, amount, status,
             "limit aşıldı" if status == "failed" else "", reversed_at,
             "yanlış öğrenci" if reversed_at else "", "2026-10-15T09:00:00+00:00"))


def build(store: Any, tmp_path: Path) -> LunchReports:
    folder = tmp_path / "rapor"
    folder.mkdir(exist_ok=True)
    return LunchReports(store=store, log=Log(), fallback_dir=tmp_path / "yedek",
                        folder=lambda: folder)


def keep(path: Path, name: str) -> None:
    target = os.environ.get("KM_63_ORNEK_PDF")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(target) / f"{name}.pdf")


def test_ay_sinirlari_ve_gun_adi() -> None:
    assert month_bounds("2026-02") == ("2026-02-01", "2026-02-28")
    assert tr_day("2026-10-16") == "16.10.2026 Cuma"


async def test_aylik_rapor_yalniz_islenenleri_toplar(store: Any, tmp_path: Path) -> None:
    await seed(store)
    reports = build(store, tmp_path)
    result = await reports.produce("month", month="2026-10", author="Ayşe Muhasebe", preview=False)
    assert result["ok"] is True, result
    path = Path(result["path"])
    keep(path, "ogle-yemegi-aylik")
    text = text_of(path)
    assert "Ay: Ekim 2026" in text and "Ayşe Muhasebe" in text
    # İşlenen: L1, L2, L5 → 3 porsiyon, 255,00; başarısız ve geri alınan sayılmaz; eylül dışarıda.
    assert "Genel toplam (2 öğrenci)" in text and "255,00" in text
    assert "8-A ara toplam (1)" in text and "7-B ara toplam (1)" in text
    assert "15.10.2026 Perşembe" in text


async def test_gun_dokumu_basarisiz_ve_geri_alinan(store: Any, tmp_path: Path) -> None:
    await seed(store)
    reports = build(store, tmp_path)
    result = await reports.produce("day", day="2026-10-15", author="Ayşe", preview=False)
    assert result["ok"] is True, result
    path = Path(result["path"])
    keep(path, "ogle-yemegi-gun")
    text = text_of(path)
    assert "Başarısız: limit aşıldı" in text and "Geri alındı: yanlış öğrenci" in text
    assert "2 porsiyon" in text and "Ayşe Mutfak" in text


async def test_sayfalar_yalniz_kendi_klasorunden(store: Any, tmp_path: Path) -> None:
    await seed(store)
    reports = build(store, tmp_path)
    assert (await reports.produce("hafta"))["ok"] is False
    result = await reports.produce("month", month="2026-10")
    assert result["ok"] is True
    if not shutil.which("pdftoppm"):
        return
    assert (await reports.pages(result["path"], 1, 1))["ok"] is True
    outside = tmp_path / "baska.pdf"
    outside.write_bytes(Path(result["path"]).read_bytes())
    assert (await reports.pages(str(outside), 1, 1))["ok"] is False
