"""Kurum finansı raporları — PDF önizleme, yazdırma, PDF kaydet (63-finans-pdf).

Kullanıcı (16.10.2026): "finansal raporlarda pdf önizleme ve yazdırma olsun".

1. ŞABLON (Node): `documents-logic.js` cari hesaplar, tahsilatlar, gecikenler
   (borç yaşlandırma) ve aylık rapor modellerini EKRANDAKİ SÜZGEÇLERLE kurar:
   süzgeçler başlıkta, ara toplamlar kalın satır, genel toplam altbilgide;
   hiçbir modelde açık telefon kalmaz. Her model sunucunun çizicisinden geçer ve
   PDF'in metninde süzgeç, ara toplam ve "Sayfa X / Y" okunur.
2. UÇLAR: yeni türler `bbd_akademi.finance` ister (K9, A12); önizlemenin ilk
   yanıtı ilk sayfaları getirir, kalanı `POST /km/documents/pages` ile parça
   parça gelir; o uç yalnız modülün kendi çıktısını verir.

`node` / `pdftotext` / `pdftoppm` yoksa ilgili kısım ATLANIR.
`KM_63_ORNEK_PDF` ortam değişkeni bir klasör gösterirse örnek PDF'ler oraya yazılır.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.documents import pdf as document_pdf
from modules.bbd_akademi.backend.documents.service import KINDS
from modules.bbd_akademi.tests import test_akademi_documents as base
from modules.bbd_akademi.tests.akademi_fakes import Akademi, auth
from modules.bbd_akademi.tests.test_akademi_documents import branding_reply, pdf_text

#: Belge uçlarının fikstürü (`app_client`) aynen kullanılır — pytest onu adıyla bulur.
app_client = base.app_client

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

SCRIPT = r"""
import * as d from './documents-logic.mjs';
const stu = (id, name, klass) => ({ id, displayName: name, externalRef: `STU-${id}`, classGroup: { id: klass, name: klass } });
const accounts = [];
for (let i = 0; i < 70; i += 1) {
  accounts.push({ student: stu(`a${i}`, `Öğrenci Şükrü ${i}`, i % 2 ? '8-A' : '7-B'), openBalanceMinor: 125000 + i,
    overdueMinor: i % 3 ? 0 : 50000, creditMinor: i === 5 ? 1500 : 0, nextDueDate: '2026-11-01' });
}
const payments = [
  { id: 'p1', receiptNo: 'BBD-2026-000001', paidAt: '2026-10-01T09:00:00+03:00', amountMinor: 250000, method: 'cash',
    status: 'completed', allocations: [], creditAddedMinor: 0, recordedBy: { displayName: 'Ayşe Muhasebe' } },
  { id: 'p2', receiptNo: 'BBD-2026-000002', paidAt: '2026-10-02T09:00:00+03:00', amountMinor: 100000, method: 'transfer',
    status: 'completed', allocations: [], creditAddedMinor: 5000, recordedBy: { displayName: 'Ayşe Muhasebe' } },
  { id: 'p3', receiptNo: 'BBD-2026-000003', paidAt: '2026-10-03T09:00:00+03:00', amountMinor: 99900, method: 'cash',
    status: 'voided', allocations: [], creditAddedMinor: 0, recordedBy: null },
];
const overdue = [5, 29, 31, 75, 120, 400].map((days, i) => ({ student: stu(`o${i}`, `Geciken ${i}`, '8-A'),
  guardians: [{ guardian: { displayName: `Veli 0532 123 45 6${i}` } }], overdueMinor: 10000 * (i + 1),
  oldestDueDate: '2026-06-01', daysOverdue: days, chargeCount: i + 1, lastReminderAt: null }));
const monthly = { from: '2026-01-01', to: '2026-10-16', months: [
  { month: '2026-09', byMethod: [{ method: 'cash', amountMinor: 100000 }, { method: 'card', amountMinor: 50000 }],
    collectedMinor: 150000, voidedMinor: 0, refundedMinor: 10000, netMinor: 140000, chargedMinor: 400000, discountMinor: 0 },
  { month: '2026-10', byMethod: [{ method: 'transfer', amountMinor: 70000 }],
    collectedMinor: 70000, voidedMinor: 5000, refundedMinor: 0, netMinor: 70000, chargedMinor: 300000, discountMinor: 2500 }],
  totals: { byMethod: [{ method: 'cash', amountMinor: 100000 }, { method: 'card', amountMinor: 50000 },
    { method: 'transfer', amountMinor: 70000 }], collectedMinor: 220000, voidedMinor: 5000, refundedMinor: 10000,
    netMinor: 210000, chargedMinor: 700000, discountMinor: 2500 } };
const out = {
  accounts: d.financeAccountsDoc(accounts, { filters: [['Arama', null], ['Sınıf', ''], ['Durum', 'Gecikmesi olan']] }),
  payments: d.financePaymentsDoc(payments, { filters: [['Tahsilat tarihi', '01.10.2026 – 16.10.2026'], ['Yöntem', ''], ['Durum', '']] }),
  overdue: d.financeOverdueDoc(overdue, { filters: [['Sınıf', '8-A'], ['Gecikme', '1+ gün'], ['Tarih itibarıyla', '16.10.2026']] }),
  monthly: d.financeMonthlyDoc(monthly, { filters: [['Dönem', '01.01.2026 – 16.10.2026']] }),
  aging: [0, 1, 30, 31, 60, 61, 90, 91, 999].map((days) => d.agingOf(days).key),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def models(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    folder = tmp_path_factory.mktemp("finans-rapor")
    (folder / "documents-logic.mjs").write_bytes((PANEL / "documents-logic.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    completed = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                               check=False, timeout=60, cwd=folder)
    assert completed.returncode == 0, completed.stderr
    data: dict[str, Any] = json.loads(completed.stdout)
    return data


def _table(model: dict[str, Any]) -> dict[str, Any]:
    return next(section for section in model["document"]["parts"][0]["sections"]
                if section["kind"] == "table")


def _render(model: dict[str, Any], name: str) -> bytes:
    content = document_pdf.render(
        document_pdf.parse(model["document"]),
        document_pdf.Letterhead(name="BBD Akademi Kadıköy", detail="Moda Cad. 1"),
        document_pdf.Stamp(number="AK-TEST-1", issued="16.10.2026 16:30", author="Ayşe Muhasebe"))
    target = os.environ.get("KM_63_ORNEK_PDF")
    if target:
        Path(target).mkdir(parents=True, exist_ok=True)
        (Path(target) / f"{name}.pdf").write_bytes(content)
    return content


def test_yaslandirma_dilimleri(models: dict[str, Any]) -> None:
    assert models["aging"] == ["1-30", "1-30", "1-30", "31-60", "31-60", "61-90", "61-90", "90+", "90+"]


def test_modeller_bilinen_turde_suzgecler_baslikta(models: dict[str, Any]) -> None:
    kinds = {name: models[name]["kind"] for name in ("accounts", "payments", "overdue", "monthly")}
    assert kinds == {"accounts": "finance_accounts", "payments": "finance_payments",
                     "overdue": "finance_overdue", "monthly": "finance_monthly"}
    assert all(kind in KINDS for kind in kinds.values())
    meta = models["accounts"]["document"]["parts"][0]["meta"]
    # Boş arama yazılmaz; seçilmemiş süzgeç "Tümü" yazar (unutulmadığı görünsün).
    assert meta == [["Sınıf", "Tümü"], ["Durum", "Gecikmesi olan"]]
    assert models["monthly"]["document"]["orientation"] == "landscape"


def test_ara_ve_genel_toplamlar(models: dict[str, Any]) -> None:
    accounts = _table(models["accounts"])
    subtotals = [row["cells"][0] for row in accounts["rows"]
                 if isinstance(row, dict) and row.get("tone") == "strong"]
    assert subtotals == ["7-B ara toplam (35)", "8-A ara toplam (35)"]
    assert accounts["footer"][0] == "Genel toplam (70 hesap)"
    payments = _table(models["payments"])
    # İptal toplamlara girmez: 2.500 + 1.000 = 3.500 ₺.
    assert payments["footer"][3] == "3.500,00 ₺"
    assert payments["rows"][2]["tone"] == "muted"
    overdue = _table(models["overdue"])
    groups = [row["cells"][0] for row in overdue["rows"] if isinstance(row, dict) and row.get("tone") == "info"]
    assert groups == ["Gecikme: 1–30 gün", "Gecikme: 31–60 gün", "Gecikme: 61–90 gün",
                      "Gecikme: 90 günden fazla"]
    assert overdue["footer"][3] == "2.100,00 ₺"
    monthly = _table(models["monthly"])
    assert monthly["footer"][0] == "Toplam" and monthly["footer"][6] == "2.200,00 ₺"


def test_acik_telefon_yok(models: dict[str, Any]) -> None:
    phone = re.compile(r"0?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
    assert not phone.search(json.dumps(models, ensure_ascii=False))


def test_pdf_metninde_suzgec_ara_toplam_ve_sayfa(models: dict[str, Any], tmp_path: Path) -> None:
    for name in ("accounts", "payments", "overdue", "monthly"):
        content = _render(models[name], f"akademi-{name}")
        assert content.startswith(b"%PDF")
    text = pdf_text(_render(models["accounts"], "akademi-accounts"), tmp_path)
    if text is None:
        pytest.skip("pdftotext yok")
    pages = text.count("\f") or 1
    assert "Gecikmesi olan" in text and "7-B ara toplam (35)" in text
    assert f"Sayfa 1 / {pages}" in text and "Öğrenci Şükrü 12" in text
    overdue = pdf_text(_render(models["overdue"], "akademi-overdue"), tmp_path) or ""
    assert "90 günden fazla" in overdue and "0*** *** ** 6" in overdue


async def test_finans_listesi_izin_ister_ve_sayfalar_parca_parca(
        app_client: Any, akademi: Akademi, models: dict[str, Any], tmp_path: Path) -> None:
    http, _ = app_client
    akademi.reply(branding_reply)
    body = {"kind": "finance_accounts", "document": models["accounts"]["document"]}
    # Kurum carisi: yalnız görüntüleme izni yetmez (K9).
    denied = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("viewer"), json=body)
    assert denied.status_code == 403
    response = await http.post("/api/bbd_akademi/km/documents/preview", headers=auth("cashier"), json=body)
    data = response.json()
    assert data["ok"] is True, data
    if not shutil.which("pdftoppm"):
        return
    # İlk yanıt yalnız ilk sayfaları getirir; kalanı parça parça.
    assert data["shown"] <= 4 and data["pageCount"] >= data["shown"] >= 1
    more = await http.post("/api/bbd_akademi/km/documents/pages", headers=auth("cashier"),
                           json={"path": data["path"], "first": 1, "last": 2})
    assert more.json()["ok"] is True and len(more.json()["pages"]) in (1, 2)
    # Modülün çıktı klasörü dışındaki dosya verilmez.
    outside = tmp_path.parent / "baska.pdf"
    outside.write_bytes(Path(data["path"]).read_bytes())
    foreign = await http.post("/api/bbd_akademi/km/documents/pages", headers=auth("cashier"),
                              json={"path": str(outside), "first": 1, "last": 1})
    assert foreign.json()["ok"] is False
    missing = await http.post("/api/bbd_akademi/km/documents/pages", headers=auth("cashier"),
                              json={"path": "/etc/hosts", "first": 1, "last": 1})
    assert missing.json()["ok"] is False
