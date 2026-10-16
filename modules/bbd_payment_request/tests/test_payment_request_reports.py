"""Ödeme Talebi — PDF raporları (63-finans-pdf).

Talepler (durum süzgeci + arama, durum ara toplamı), nakit tahsilatlar
(başarısızlar toplama girmez) ve borçlu öğrenciler (sınıf ara toplamı) ortak
şablondan çıkar; süzgeçler başlıkta, hazırlayan altbilgidedir; veli telefonu
yazılmaz. Kantine ulaşılamazsa rapor uydurulmaz. Kalan sayfalar yalnız
modülün rapor klasöründen verilir. Çıktı geçici klasöre yazılır.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_payment_request_backend.reports import PaymentReports, fold


class Log:
    def info(self, *args: Any, **kwargs: Any) -> None:
        pass

    def warning(self, *args: Any, **kwargs: Any) -> None:
        pass


class Workspace:
    def __init__(self, connected: bool = True) -> None:
        self.connected = connected

    async def workspace(self) -> dict[str, Any]:
        if not self.connected:
            return {"connected": False, "error": "kantin kapalı"}
        requests = [
            {"ref": f"PR-{index}", "studentName": f"Öğrenci Şule {index}", "amount": 1000 * (index + 1),
             "status": ["PENDING", "PAID", "EXPIRED"][index % 3], "createdAt": 1_760_000_000_000 + index,
             "ageDays": index} for index in range(9)]
        students = [{"kantinId": f"k{index}", "displayName": f"Öğrenci Şule {index}",
                     "parentPhone": "05321234567", "hasPhone": index != 2,
                     "balance": 2500 * index - 2500, "isBlocked": index == 3} for index in range(6)]
        return {"connected": True, "requests": requests, "students": students}


def text_of(path: Path) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        pytest.skip("pdftotext yok")
    out = subprocess.run([binary, "-layout", str(path), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def keep(path: Path, name: str) -> None:
    target = os.environ.get("KM_63_ORNEK_PDF")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(target) / f"{name}.pdf")


def build(store: Any, tmp_path: Path, *, connected: bool = True) -> PaymentReports:
    folder = tmp_path / "rapor"
    folder.mkdir(exist_ok=True)
    return PaymentReports(service=Workspace(connected), store=store, log=Log(),
                          fallback_dir=tmp_path / "yedek", folder=lambda: folder)


def test_katlama() -> None:
    assert fold("ŞULE Işık") == "sule isik"


async def test_talepler_suzgec_ve_durum_ara_toplami(store: Any, tmp_path: Path) -> None:
    reports = build(store, tmp_path)
    result = await reports.produce("requests", status="PENDING", query="şule",
                                   author="Ayşe Muhasebe", preview=False)
    assert result["ok"] is True, result
    path = Path(result["path"])
    keep(path, "odeme-talepleri")
    text = text_of(path)
    assert "Durum: Bekliyor · Arama: şule" in text and "Ayşe Muhasebe" in text
    # PENDING: 0, 3, 6 → 10 + 40 + 70 = 120,00 ₺ (kâğıtta ₺ ya da TL).
    assert "Bekliyor ara toplam (3)" in text and "Genel toplam (3 talep)" in text
    assert "120,00" in text
    assert "Ödendi ara toplam" not in text


async def test_borclu_listesi_sinif_ara_toplami_telefon_yok(store: Any, tmp_path: Path) -> None:
    reports = build(store, tmp_path)
    classes = {"k2": "8-A", "k3": "8-A", "k4": "7-B", "k5": "7-B"}
    result = await reports.produce("debtors", classes=classes, author="Ayşe", preview=False)
    assert result["ok"] is True, result
    path = Path(result["path"])
    keep(path, "borclu-ogrenciler")
    text = text_of(path)
    assert "7-B ara toplam (2)" in text and "8-A ara toplam (2)" in text
    assert "Genel toplam (4 öğrenci)" in text
    assert "0532" not in text and "05321234567" not in text
    assert "KANTİN hesabına aittir" in text


async def test_nakit_tahsilat_basarisiz_toplama_girmez(store: Any, tmp_path: Path) -> None:
    table = store.table("collection")
    for local_id, amount, status in (("c1", 5000, "ok"), ("c2", 2500, "ok"), ("c3", 9900, "failed")):
        await store.execute(
            f"INSERT INTO {table} (local_id, kantin_id, student_name, amount, balance_after, status, "
            f"reason, note, created_at, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (local_id, "k1", "Öğrenci Şule 1", amount, 0, status,
             "limit aşıldı" if status != "ok" else "", "", "2026-10-16T10:00:00+00:00", "Ayşe"))
    reports = build(store, tmp_path)
    result = await reports.produce("collections", author="Ayşe", preview=False)
    assert result["ok"] is True, result
    text = text_of(Path(result["path"]))
    assert "Toplam (2 tahsilat)" in text and "75,00" in text
    assert "Başarısız denemeler (1)" in text and "limit aşıldı" in text


async def test_kantin_yoksa_rapor_uydurulmaz(store: Any, tmp_path: Path) -> None:
    reports = build(store, tmp_path, connected=False)
    result = await reports.produce("requests")
    assert result == {"ok": False, "error": "Kantine ulaşılamadı: kantin kapalı"}
    assert (await reports.produce("bilinmeyen"))["ok"] is False


async def test_onizleme_ve_sayfalar_yalniz_kendi_klasorunden(store: Any, tmp_path: Path) -> None:
    reports = build(store, tmp_path)
    result = await reports.produce("requests")
    assert result["ok"] is True
    if not shutil.which("pdftoppm"):
        return
    assert result["shown"] >= 1 and result["pageCount"] >= 1
    assert (await reports.pages(result["path"], 1, 1))["ok"] is True
    outside = tmp_path / "baska.pdf"
    outside.write_bytes(Path(result["path"]).read_bytes())
    assert (await reports.pages(str(outside), 1, 1))["ok"] is False
