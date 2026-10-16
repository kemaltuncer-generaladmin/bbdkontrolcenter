"""Ders programında çoklu sınıf + isteğe bağlı müfredat (`ui/panel/timetable-week.js`) — Node'da küçük DOM.

Kullanıcı (14.10.2026, iş 23): "ders eklerken sınıfı yani grubu da seçebilelim" — bir derse bir ya da
birden çok sınıf (ör. 8-A + 8-B birlikte Matematik). Bu test:

* sunucudan gelen ortak dersin hücrede "+ 8-B" ile göründüğünü,
* başka sınıfın programındaki ortak dersin salt okunur listelendiğini ("TYT Hüseyin programını aç"),
* düzenleyicide "Sınıf seç" ile sınıf eklenince ve müfredat seçilince PUT satırının
  `classGroupIds` (programın sınıfı ilk) + `programId` taşıdığını; ek sınıfı olmayan satırın bu
  alanları HİÇ göndermediğini (eski Akademi bilinmeyen alanı 422 ile reddeder),
* erişilebilirlik denetiminin temiz olduğunu doğrular. `node` yoksa atlanır.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

C1, C2, C3 = "c1", "c2", "c3"
CLASSES = [{"id": C1, "name": "8-A", "gradeLevel": 8}, {"id": C2, "name": "8-B", "gradeLevel": 8},
           {"id": C3, "name": "TYT Hüseyin", "gradeLevel": None}]
SLOTS = {"slots": [
    {"id": f"{day}{n}", "dayOfWeek": day, "order": n, "startTime": f"0{8 + n}:00", "endTime": f"0{8 + n}:40",
     "kind": "lesson"} for day in ("mon", "tue") for n in (1, 2)]}
SUBJECTS = [{"id": "matematik", "name": "Matematik", "level": "ortaokul"},
            {"id": "fizik", "name": "Fizik", "level": "lise"}]
MAT = {"id": "matematik", "name": "Matematik"}
TIMETABLE = {
    "classGroup": {"id": C1, "name": "8-A"}, "validFrom": "2026-09-01", "validTo": None, "version": 3,
    "entries": [
        {"id": "e1", "dayOfWeek": "mon", "bellSlotId": "mon1", "subject": MAT, "teacher": None, "room": None,
         "status": "active", "classGroups": [{"id": C1, "name": "8-A"}, {"id": C2, "name": "8-B"}],
         "ownerClassGroup": {"id": C1, "name": "8-A"}, "program": None},
        {"id": "e2", "dayOfWeek": "mon", "bellSlotId": "mon2", "subject": MAT, "teacher": None, "room": "204",
         "status": "active", "classGroups": [{"id": C1, "name": "8-A"}], "program": None},
    ],
    "sharedEntries": [
        {"id": "e9", "dayOfWeek": "tue", "bellSlotId": "tue1", "subject": {"id": "fizik", "name": "Fizik"},
         "teacher": {"id": "t1", "displayName": "Hüseyin Hoca"}, "room": None, "status": "active",
         "classGroups": [{"id": C3, "name": "TYT Hüseyin"}, {"id": C1, "name": "8-A"}],
         "ownerClassGroup": {"id": C3, "name": "TYT Hüseyin"}},
    ],
}
PROGRAMS = {"items": [{"id": "lgs-matematik", "title": "Matematik", "kind": "exam", "exam": "LGS",
                       "subject": MAT, "levelKey": "lgs", "levelLabel": "LGS", "levelOrder": 45,
                       "scope": "global", "curriculumVersion": "meb"}]}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { weekTab } = await load('./panels/bbd_akademi/lessons/timetable-week.js');
  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  const root = document.createElement('div');
  document.body.append(root);
  const toasts = [];
  const app = {
    root,
    can: () => true,
    enabled: () => true,
    toast: (text, tone) => toasts.push([text, tone]),
    api: {
      can: () => true,
      featureOff: () => false,
      get: (path, query) => api(`${path}${qs(query)}`),
      getCached: (path, query) => api(`${path}${qs(query)}`),
      put: (path, body) => api(path, { method: 'PUT', body }),
      invalidate: () => {},
    },
    lookups: {
      classGroups: async () => CLASSES,
      bellSlots: async () => SLOTS,
      subjects: async () => SUBJECTS,
      teachers: async () => [{ id: 't1', displayName: 'Hüseyin Hoca' }],
    },
  };
  const tab = weekTab(app, {});
  root.append(tab.node);
  await tab.reload();
  await sleep(30);
  const out = {};
  // Birden çok sınıf varken hiçbir sınıf kendiliğinden seçilmez (ilk sınıf "8-A" ön seçili gelmesin).
  const pick = root.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Sınıf');
  out.pickValue = pick?.value;
  out.pickFirst = pick?.querySelectorAll('option')[0]?.textContent;
  out.emptyText = root.querySelector('.ak-tt-week')?.textContent || root.textContent;
  out.readsBeforePick = calls.filter((c) => c.path.includes('/timetable')).length;
  tab.open(CLASSES[0].id);
  await sleep(30);
  const cell = (key) => root.querySelectorAll('.ak-tt-cell').find((node) => node.dataset.key === key);
  out.cellMon1 = cell('mon|mon1').textContent;
  out.cellMon2 = cell('mon|mon2').textContent;
  out.shared = root.querySelector('.ak-tt-alerts').textContent;
  cell('mon|mon2').click();
  await sleep(30);
  const side = root.querySelector('.ak-tt-side');
  out.sideLabels = side.querySelectorAll('.ak-tt-field-label').map((n) => n.textContent);
  const classSelect = side.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Sınıf seç');
  out.classOptions = classSelect.querySelectorAll('option').map((o) => o.textContent);
  classSelect.value = "c3";
  classSelect.dispatchEvent(new Event('change'));
  await sleep(20);
  out.chips = root.querySelector('.ak-tt-side').querySelectorAll('.ak-tt-classpick .kit-chip')
    .map((c) => c.querySelector('span')?.textContent || c.textContent);
  const programSelect = root.querySelector('.ak-tt-side').querySelectorAll('select')
    .find((s) => s.getAttribute('aria-label') === 'Müfredat (isteğe bağlı)');
  out.programOptions = programSelect.querySelectorAll('option').map((o) => o.textContent);
  programSelect.value = 'lgs-matematik';
  programSelect.dispatchEvent(new Event('change'));
  await sleep(10);
  out.violations = audit(root.querySelector('.ak-tt-side'));
  root.querySelectorAll('button').find((b) => b.textContent === 'Kaydet').click();
  await sleep(40);
  out.put = calls.filter((c) => c.method === 'PUT').map((c) => c.body);
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {
        f"GET /v1/admin/class-groups/{C1}/timetable*": TIMETABLE,
        f"PUT /v1/admin/class-groups/{C1}/timetable": TIMETABLE,
        "GET /v1/admin/curricula/programs*": PROGRAMS,
    }
    scenario = (
        f"const CLASSES = {json.dumps(CLASSES, ensure_ascii=False)};\n"
        f"const SLOTS = {json.dumps(SLOTS)};\nconst SUBJECTS = {json.dumps(SUBJECTS, ensure_ascii=False)};\n"
        + SCENARIO
    )
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_hucrede_ortak_sinif_ve_salt_okunur_ortak_ders(result: dict[str, Any]) -> None:
    assert "+ 8-B" in result["cellMon1"]
    assert "+" not in result["cellMon2"].replace("204", "")
    assert "Ortak dersler (1)" in result["shared"]
    assert "TYT Hüseyin + 8-A" in result["shared"]
    assert "TYT Hüseyin programını aç" in result["shared"]


def test_duzenleyicide_sinif_sec_ve_mufredat(result: dict[str, Any]) -> None:
    assert "Sınıflar" in result["sideLabels"] and "Müfredat (isteğe bağlı)" in result["sideLabels"]
    # programın sınıfı (8-A) seçenekte yok; diğer sınıflar var
    assert result["classOptions"] == ["+ Sınıf seç (birlikte ders)", "8-B", "TYT Hüseyin"]
    assert result["chips"] == ["8-A", "TYT Hüseyin"]
    assert result["programOptions"] == ["Müfredat yok", "LGS · Matematik"]
    assert result["violations"] == []


def test_put_satiri_coklu_sinif_ve_mufredat(result: dict[str, Any]) -> None:
    assert len(result["put"]) == 1
    entries = {row["bellSlotId"]: row for row in result["put"][0]["entries"]}
    assert entries["mon1"]["classGroupIds"] == [C1, C2]
    assert "programId" not in entries["mon1"]
    assert entries["mon2"]["classGroupIds"] == [C1, C3]
    assert entries["mon2"]["programId"] == "lgs-matematik"
    assert "classGroupId" not in entries["mon2"]
    assert result["put"][0]["expectedVersion"] == 3


def test_sinif_kendiliginden_secilmez(result: dict[str, Any]) -> None:
    """Kullanıcı (16.10): "Ders programı girerken 8. sınıf mevcut seçili geliyor" — birden çok sınıfta
    liste "Sınıf seçin" ile açılır, hiçbir sınıfın programı okunmaz; seçince (derin bağlantı) açılır."""
    assert result["pickValue"] == ""
    assert result["pickFirst"] == "Sınıf seçin"
    assert "Sınıf seçin" in result["emptyText"]
    assert result["readsBeforePick"] == 0
