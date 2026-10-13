""""Sınıflar" sekmesi (sınıf seviyeyi taşır, göç 008) — Node'da küçük DOM üzerinde.

Seviyesi girilmemiş sınıf "Seviye seçin" ile işaretlenir, addan öneri doldurulur ama
YAZILMAZ; Kaydet yalnız o sınıfın PUT'unu gönderir. 9. sınıfta alan seçilemez.
Erişilebilirlik denetimi temiz olmalı. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_students/classes"
ROUTES: dict[str, Any] = {
    f"GET {BASE}": {"items": [
        {"name": "9-A", "gradeLevel": 9, "track": None, "examTarget": None, "defined": True,
         "studentCount": 12, "label": "9-A · 9. sınıf", "suggestion": None, "updatedAt": None},
        {"name": "11-SAY", "gradeLevel": None, "track": None, "examTarget": None, "defined": False,
         "studentCount": 8, "label": "11-SAY · seviye seçin",
         "suggestion": {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}, "updatedAt": None},
    ], "undefinedCount": 1},
    f"PUT {BASE}/11-SAY": {"name": "11-SAY", "gradeLevel": 11, "track": "SAY", "examTarget": "YKS",
                          "label": "11-SAY · 11. sınıf · SAY · YKS", "updatedStudents": 8},
}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { mountClasses } = await load('./panels/bbd_students/classes.js');
  const host = document.createElement('div');
  document.body.append(host);
  const toasts = [];
  mountClasses(host, { api, can: () => true, toast: (t) => toasts.push(t), dialogRoot: host });
  await sleep(30);
  const out = {};
  out.summary = host.querySelector('.st-status').textContent;
  const rows = host.querySelectorAll('.cl-row');
  out.missingBadge = rows[1].querySelector('.st-badge')?.textContent;
  out.suggestion = rows[1].querySelector('.cl-status').textContent;
  const selects = (row) => row.querySelectorAll('select');
  out.prefilled = selects(rows[1]).map((s) => s.value);
  out.trackDisabled9 = selects(rows[0])[1].disabled;
  out.saveDisabledDefined = rows[0].querySelector('button').disabled;
  out.violations = audit(host);
  out.putsBefore = calls.filter((c) => c.method === 'PUT').length;
  rows[1].querySelector('button').dispatchEvent(new Event('click', { bubbles: true }));
  await sleep(30);
  out.put = calls.filter((c) => c.method === 'PUT').map((c) => [c.path, c.body]);
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    try:
        data = run(SCENARIO, panels=("bbd_students",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_seviyesiz_sinif_isaretli_oneri_dolu_ama_yazilmamis(result: dict[str, Any]) -> None:
    assert "1 sınıfa seviye girilmemiş" in result["summary"]
    assert result["missingBadge"] == "Seviye seçin"
    assert result["suggestion"].startswith("Addan öneri: 11. sınıf · Sayısal · YKS")
    assert result["prefilled"] == ["11", "SAY", "YKS"]
    assert result["putsBefore"] == 0                 # öneri kendiliğinden yazılmaz


def test_alan_dokuzda_kapali_degismeyen_kayit_kapali(result: dict[str, Any]) -> None:
    assert result["trackDisabled9"] is True
    assert result["saveDisabledDefined"] is True


def test_kaydet_yalniz_o_sinifi_yazar(result: dict[str, Any]) -> None:
    assert result["put"] == [[f"{BASE}/11-SAY", {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}]]
    assert result["toasts"][0].startswith("“11-SAY”: 11-SAY · 11. sınıf · SAY · YKS — 8 öğrenci")


def test_erisilebilirlik_temiz(result: dict[str, Any]) -> None:
    assert result["violations"] == [], result["violations"]
