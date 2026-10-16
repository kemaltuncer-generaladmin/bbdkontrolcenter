"""Öğrenciler › Sınıflar — DÖNEM sütunu ve süzgeci (iş 30) — Node'da küçük DOM üzerinde.

"Dönem" = sınıfın akademik yılı (KM'nin; Akademi `ClassGroup.academicYear`). Eski Akademi alanı
taşımıyorsa dönem KM Sınıflar listesinden (ad eşlemesi) okunur. Akademi'nin `termId`'si "Yarıyıl"
sütunudur. Liste varsayılan güncel dönemle süzülür; "Tüm dönemler" hepsini gösterir. `node` yoksa atlanır.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

_TODAY = date.today()
YEAR = f"{_TODAY.year}-{_TODAY.year + 1}" if _TODAY.month >= 9 else f"{_TODAY.year - 1}-{_TODAY.year}"
OLD = f"{int(YEAR[:4]) - 1}-{int(YEAR[:4])}"


def row(cid: str, name: str, **extra: Any) -> dict[str, Any]:
    return {"id": cid, "name": name, "kmClassName": name, "gradeLevel": None, "track": None, "termId": None,
            "studentCount": 3, "active": True, "archived": False, "curricula": [], **extra}


SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load }) {
  const { classesView } = await load('./panels/bbd_akademi/students/students-groups.js');
  const make = async (items) => {
    const root = document.createElement('div');
    document.body.append(root);
    const app = {
      root, can: () => true, toast: () => {}, go: () => {},
      api: { getCached: async () => ({ items }), get: async () => ({}), invalidate: () => {}, can: () => true,
        featureOff: () => false },
      lookups: { terms: async () => [], teachers: async () => [] },
      kmRead: (name, method, args = {}) => api({
        'bbd_students.groups_read:list': `/api/bbd_students/groups?archived=${args.archived}`,
        'bbd_students.groups_read:rollover': '/api/bbd_students/groups/rollover',
      }[`${name}:${method}`]),
    };
    const view = classesView(app, { openStudent: () => {} });
    root.append(view.node);
    await view.reload();
    await sleep(30);
    return root;
  };
  const names = (root) => root.querySelectorAll('.kit-table-row, [role="row"]')
    .map((r) => r.textContent).filter((t) => t.includes('Sınıf-'));
  const out = {};
  // Yeni Akademi: academicYear satırda.
  const fresh = await make(__FRESH__);
  out.headers = fresh.textContent;
  out.freshDefault = names(fresh);
  const select = fresh.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Dönem');
  out.yearValue = select.value;
  select.value = ''; select.dispatchEvent(new Event('change'));
  out.freshAll = names(fresh);
  out.violations = audit(fresh);
  // Eski Akademi: dönem KM listesinden.
  const legacy = await make(__LEGACY__);
  out.legacyDefault = names(legacy);
  return out;
}
"""


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    fresh = [row("1", "Sınıf-A", academicYear=YEAR), row("2", "Sınıf-B", academicYear=OLD)]
    legacy = [row("3", "Sınıf-C"), row("4", "Sınıf-D")]
    scenario = SCENARIO.replace("__FRESH__", json.dumps(fresh)).replace("__LEGACY__", json.dumps(legacy))
    routes = {"GET /api/bbd_students/groups?archived=all": {"groups": [
        {"id": "k3", "name": "Sınıf-C", "academicYear": YEAR, "legacyClassName": None},
        {"id": "k4", "name": "Sınıf-D", "academicYear": OLD, "legacyClassName": None}]}}
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_donem_sutunu_ve_varsayilan_guncel_donem(out: dict[str, Any]) -> None:
    assert "Dönem" in out["headers"] and "Yarıyıl" in out["headers"]
    assert out["yearValue"] == YEAR
    assert len(out["freshDefault"]) == 1 and "Sınıf-A" in out["freshDefault"][0]
    assert len(out["freshAll"]) == 2
    assert out["violations"] == []


def test_eski_akademide_donem_km_listesinden(out: dict[str, Any]) -> None:
    assert len(out["legacyDefault"]) == 1 and "Sınıf-C" in out["legacyDefault"][0]
