"""Kantin raporları — ortak finansal PDF şablonu (63-finans-pdf).

* Modülün kendi reportlab kopyası kaldırıldı; `export.py` ortak üreticiyi
  (`km_sdk.build_pdf`) verir ve davranış korunur (para biçimi, CSV kuralı).
* Ekrandaki süzgeçler (dönem, arama, sıralama) PDF'in başlığında yazar;
  hazırlayan kişi altbilgidedir; tek günlük özet "Gün Sonu" raporudur ve
  tahsilatları ayrı bölümde taşır (ciroya eklenmez).
* Öğrenci listesi PDF'i aramaya uyan öğrencileri sınıf ara toplamlarıyla basar.
* Önizleme ilk sayfaları getirir; kalan sayfalar yalnız modülün rapor
  klasöründen verilir.
Çıktı geçici klasöre yazılır (`export_path`), masaüstüne değil.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_canteen_reports_backend import export
from bbd_canteen_reports_backend.service import ReportService, fold
from canteen_reports_fakes import FakeCanteen, FakeLog, FakeStore, at, sale


def build(canteen: FakeCanteen, folder: Path) -> ReportService:
    return ReportService(canteen=canteen, store=FakeStore(), log=FakeLog(),
                         config={"export_path": str(folder)}, fallback_dir=folder / "yedek")


def text_of(path: Path) -> str:
    binary = shutil.which("pdftotext")
    if not binary:
        pytest.skip("pdftotext yok")
    out = subprocess.run([binary, "-layout", str(path), "-"], capture_output=True, check=True)
    return out.stdout.decode("utf-8")


def keep(path: Path, name: str) -> None:
    """`KM_63_ORNEK_PDF` verilmişse örnek PDF oraya kopyalanır (rapor için)."""
    target = os.environ.get("KM_63_ORNEK_PDF")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        shutil.copy(path, Path(target) / f"{name}.pdf")


def canteen() -> FakeCanteen:
    rows = [sale("2026-10-16", student=f"s{index}", name=f"Öğrenci Çağrı {index}",
                 total=1250 + index, hour=8 + index % 6) for index in range(12)]
    rows.append(sale("2026-10-16", student="s0", total=900, hour=13,
                     reversed_at=at("2026-10-16", 14), reason="yanlış ürün"))
    students = [{"id": f"s{index}", "name": f"Öğrenci Çağrı {index}",
                 "balance": 5000 if index % 3 == 0 else -100} for index in range(12)]
    collections = {"totalCollected": 15000, "totalCash": 10000, "count": 2, "entries": [
        {"studentId": "s0", "studentName": "Öğrenci Çağrı 0", "amount": 10000, "method": "cash",
         "createdAt": at("2026-10-16", 15)},
        {"studentId": "s3", "studentName": "Öğrenci Çağrı 3", "amount": 5000, "method": "link",
         "createdAt": at("2026-10-16", 16)}]}
    return FakeCanteen(rows, collections=collections, students=students)


def test_export_ortak_ureticiyi_verir_davranis_korunur() -> None:
    import km_sdk

    assert export.build_pdf is km_sdk.build_pdf
    assert export.money(1234567) == "12.345,67 ₺"
    assert export.csv_bytes(["Ürün"], [["Süt"]]).startswith(b"\xef\xbb\xbf")


def test_katlama_ekrandaki_aramayla_ayni() -> None:
    assert fold("ÇAĞRI Işık") == "cagri isik"


async def test_gun_sonu_raporu_suzgec_hazirlayan_tahsilat(tmp_path: Path) -> None:
    service = build(canteen(), tmp_path)
    classes = {f"s{index}": "8-A" if index % 2 else "7-B" for index in range(12)}
    result = await service.export("summary_pdf", {"start": "2026-10-16", "end": "2026-10-16",
                                                  "classes": classes}, author="Ayşe Muhasebe")
    assert result["ok"] is True, result
    path = Path(result["path"])
    assert path.name.startswith("kantin-gun-sonu-")
    keep(path, "kantin-gun-sonu")
    text = text_of(path)
    assert "Kantin Gün Sonu Raporu" in text
    assert "Dönem: 16.10.2026" in text and "Ayşe Muhasebe" in text
    assert "Tahsilat dökümü (2)" in text and "Nakit tahsilat" in text
    assert "KANTİN hesabına aittir" in text
    assert "Sayfa 1 /" in text


async def test_ogrenci_listesi_arama_siralama_ara_toplam(tmp_path: Path) -> None:
    service = build(canteen(), tmp_path)
    classes = {f"s{index}": "8-A" if index % 2 else "7-B" for index in range(12)}
    result = await service.export("students_pdf", {
        "start": "2026-10-16", "end": "2026-10-16", "classes": classes,
        "query": "çağrı 1", "sort": "name", "orientation": "landscape"}, author="Ayşe")
    assert result["ok"] is True, result
    path = Path(result["path"])
    keep(path, "kantin-ogrenci-harcama")
    assert b"/MediaBox [ 0 0 841.8898 595.2756 ]" in path.read_bytes()
    text = text_of(path)
    # Arama "çağrı 1": 1, 10, 11 — 7-B'de 10; 8-A'da 1 ve 11.
    assert "Arama: çağrı 1" in text and "Sıralama: Ada göre" in text
    assert "7-B ara toplam (1)" in text and "8-A ara toplam (2)" in text
    assert "Genel toplam (3 öğrenci)" in text
    assert "Öğrenci Çağrı 2" not in text


async def test_onizleme_ilk_sayfalar_ve_sayfalar_yalniz_kendi_klasorunden(tmp_path: Path) -> None:
    service = build(canteen(), tmp_path)
    params = {"start": "2026-10-16", "end": "2026-10-16", "classes": {}}
    quiet = await service.preview("summary_pdf", {**params, "preview": False})
    assert quiet["ok"] is True and quiet["pages"] == []
    if not shutil.which("pdftoppm"):
        return
    shown = await service.preview("summary_pdf", params)
    assert shown["ok"] is True and 1 <= shown["shown"] <= 4 and shown["pageCount"] >= 1
    more = await service.pages(shown["path"], 1, 1)
    assert more["ok"] is True and len(more["pages"]) == 1
    outside = tmp_path.parent / f"{tmp_path.name}-baska.pdf"
    outside.write_bytes(Path(shown["path"]).read_bytes())
    assert (await service.pages(str(outside), 1, 1))["ok"] is False
    assert (await service.pages("/etc/hosts", 1, 1))["ok"] is False


async def test_karne_ve_urun_pdf_ortak_sablonla(tmp_path: Path) -> None:
    service = build(canteen(), tmp_path)
    params: dict[str, Any] = {"start": "2026-10-10", "end": "2026-10-16", "classes": {}}
    products = await service.export("products_pdf", params, author="Ayşe")
    student = await service.export("student_pdf", {**params, "studentId": "s0"}, author="Ayşe")
    assert products["ok"] and student["ok"]
    keep(Path(student["path"]), "kantin-ogrenci-karnesi")
    text = text_of(Path(student["path"]))
    assert "Öğrenci Kantin Karnesi" in text and "Dönem: 10.10.2026 – 16.10.2026" in text
