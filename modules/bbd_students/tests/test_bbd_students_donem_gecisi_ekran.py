"""DÖNEM GEÇİŞİ sihirbazı ekranı (iş 37 / İ4) — Node'da küçük DOM üzerinde.

Sınıflar şeridindeki "Dönem geçişi" sihirbazı sekmenin yerinde açar; her sınıfa işlem
seçilir (önizleme sunucudan), mezun istisnası işaretlenir, "Geçişi uygula" PIN'le
gönderilir (önizlemenin revizyonuyla), son geçiş "Geri al" ile geri çevrilir.
Erişilebilirlik denetimi temiz olmalı. `node` yoksa atlanır.
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

BASE = "/api/bbd_students/groups/rollover"
L7 = {"gradeLevel": 7, "track": None, "examTarget": None}
L8 = {"gradeLevel": 8, "track": None, "examTarget": "LGS"}

OVERVIEW = {
    "today": "2027-09-01", "currentYear": "2027-2028", "fromYear": "2026-2027",
    "toYear": "2027-2028", "effectiveFrom": "2027-09-01",
    "window": {"earliest": "2027-08-18", "latest": "2027-10-31"},
    "years": [{"academicYear": "2026-2027", "classes": 2}], "revision": 41, "history": [],
    "latest": {"id": "R0", "fromYear": "2025-2026", "toYear": "2026-2027",
               "effectiveFrom": "2026-09-01", "state": "applied", "createdBy": "Ayşe",
               "createdAt": "2026-09-01T09:00:00+03:00", "canUndo": True,
               "counts": {"created": 1, "moved": 2},
               "graduation": {"deactivated": 1, "scheduled": 0, "failed": 0},
               "pairs": [{"fromName": "6-A", "toName": "7-A", "toYear": "2026-2027",
                          "joined": False}]},
}

STUDENTS = [{"kantinId": "S1", "displayName": "Elif Kaya"},
            {"kantinId": "S3", "displayName": "Can Demir"}]

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const previews = [];
  globalThis.__apiHook = (method, path, options) => {
    if (method === 'POST' && path.endsWith('/rollover/preview')) {
      const body = options.body;
      previews.push(JSON.parse(JSON.stringify(body)));
      const keep = new Set(body.keepStudentIds || []);
      const mine = (body.items || []).find((item) => item.groupId === 'C1') || {};
      return {
        fromYear: '2026-2027', toYear: '2027-2028', effectiveFrom: body.effectiveFrom || '2027-09-01',
        closeDay: '2027-08-31', revision: 41, problems: [], notes: [], blocked: null,
        classes: [
          { groupId: 'C1', name: '7-A', kind: 'group', classLevel: __L7__, memberCount: 1,
            action: mine.action || 'promote', defaultAction: 'promote', newName: mine.name || '8-A',
            nameSuggested: '8-A', newLevel: __L8__, target: 'create', targetId: null, moving: 1, leaving: 0 },
          { groupId: 'C2', name: '8-A', kind: 'group', classLevel: __L8__, memberCount: 1,
            action: 'graduate', defaultAction: 'graduate', newName: null, nameSuggested: null,
            newLevel: null, target: null, targetId: null, moving: 0, leaving: keep.has('S3') ? 0 : 1 },
        ],
        graduates: keep.has('S3') ? [] : [{ studentId: 'S3', classes: ['8-A'] }],
        keptGraduates: keep.has('S3') ? [{ studentId: 'S3', classes: ['8-A'] }] : [],
        levelChanges: [{ studentId: 'S1', before: __L7__, after: __L8__, source: 'class' }],
        counts: { classes: 2, created: 1, joined: 0, archived: 2, kept: 0, moved: 1,
          graduates: keep.has('S3') ? 0 : 1, levelChanges: 1 },
      };
    }
    if (method === 'POST' && path === '__BASE__') {
      return { id: 'R1', state: 'applied', canUndo: true, counts: { created: 1 },
        graduation: { deactivated: 1, scheduled: 0, failed: 0 }, pairs: [] };
    }
    if (method === 'POST' && path.endsWith('/undo')) {
      return { id: 'R0', state: 'undone', undo: { deletedClasses: 1, restoredClasses: 2, notes: [] } };
    }
    return undefined;
  };
  const { mountGroups } = await load('./panels/bbd_students/groups.js');
  const host = document.createElement('div');
  document.body.append(host);
  const toasts = [];
  mountGroups(host, { api, can: () => true, students: () => __STUDENTS__,
    toast: (t) => toasts.push(t), dialogRoot: host });
  await sleep(30);
  const out = {};
  const entry = host.querySelectorAll('button').find((b) => b.textContent === 'Dönem geçişi');
  out.entryTitle = entry.title;
  entry.click();
  await sleep(40);
  out.title = host.querySelector('.yr-title').textContent;
  out.from = host.querySelector('select[aria-label="Kaynak dönem"]').value;
  out.to = host.querySelector('.yr-to').textContent;
  out.day = host.querySelector('input[aria-label="Geçiş günü"]').value;
  out.last = host.querySelector('.yr-last').textContent;
  out.rows = host.querySelectorAll('.yr-row').map((row) => row.textContent);
  out.newName = host.querySelector('input[aria-label="7-A için yeni sınıf adı"]').value;
  out.tiles = host.querySelectorAll('.st-kpi-tile').map((tile) => tile.textContent);
  out.graduates = host.querySelectorAll('.gr-member').map((row) => row.textContent);
  out.levels = host.querySelectorAll('.gr-history-row').map((row) => row.textContent);
  out.violations = audit(host);
  // İşlem değişir → yeni önizleme gövdesinde.
  const select = host.querySelector('select[aria-label="7-A için işlem"]');
  select.value = 'copy';
  select.dispatchEvent(new Event('change'));
  await sleep(30);
  // Mezun istisnası: işaret kalkınca keepStudentIds.
  const grad = host.querySelector('input[aria-label="Can Demir pasifleşsin"]');
  grad.checked = false;
  grad.dispatchEvent(new Event('change'));
  await sleep(30);
  out.previewBodies = previews;
  out.graduatesAfter = host.querySelectorAll('.gr-member').map((row) => row.textContent);
  // Uygula → PIN penceresi → gönder.
  host.querySelectorAll('button').find((b) => b.textContent === 'Geçişi uygula').click();
  await sleep(10);
  const dialog = host.querySelector('.kit-dialog');
  out.pinTitle = dialog.querySelector('.kit-dialog-title').textContent;
  out.pinText = dialog.querySelector('.kit-dialog-text').textContent;
  dialog.querySelector('input').value = '123456';
  dialog.querySelectorAll('button').find((b) => b.textContent === 'Geçişi uygula').click();
  await sleep(40);
  out.applyPost = calls.filter((c) => c.method === 'POST' && c.path === '__BASE__').map((c) => c.body);
  // Geri al → PIN → undo.
  host.querySelectorAll('button').find((b) => b.textContent === 'Bu geçişi geri al').click();
  await sleep(10);
  const undo = host.querySelector('.kit-dialog');
  undo.querySelector('input').value = '123456';
  undo.querySelectorAll('button').find((b) => b.textContent === 'Geri al').click();
  await sleep(40);
  out.undoPost = calls.filter((c) => c.path.endsWith('/undo')).map((c) => [c.path, c.body]);
  out.toasts = toasts;
  // Sınıflara dönüş.
  host.querySelectorAll('button').find((b) => b.textContent === '← Sınıflara dön').click();
  await sleep(20);
  out.backToList = Boolean(host.querySelector('.gr-list'));
  return out;
}
"""

ROUTES: dict[str, Any] = {
    f"GET {BASE}": OVERVIEW,
    "GET /api/bbd_students/groups*": {"years": ["2026-2027"], "groups": []},
}


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    scenario = (SCENARIO.replace("__STUDENTS__", json.dumps(STUDENTS, ensure_ascii=False))
                .replace("__L7__", json.dumps(L7)).replace("__L8__", json.dumps(L8))
                .replace("__BASE__", BASE))
    try:
        data = run(scenario, panels=("bbd_students",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_sihirbaz_siniflar_seridinden_acilir_ve_ozet(out: dict[str, Any]) -> None:
    assert "yeni döneme" in out["entryTitle"]
    assert out["title"] == "Dönem geçişi"
    assert (out["from"], out["to"], out["day"]) == ("2026-2027", "2027-2028", "2027-09-01")
    assert "2025-2026 → 2026-2027" in out["last"] and "6-A → 7-A (2026-2027)" in out["last"]
    assert "Dönem geçişi kopyası" in out["last"]
    assert out["rows"][0] == "SınıfİşlemYeni dönemdeSonuç"
    assert "7-A" in out["rows"][1] and "1 öğrenci taşınır" in out["rows"][1]
    assert "1 öğrenci pasifleşir" in out["rows"][2]
    assert out["newName"] == "8-A"
    assert "Yeni sınıf1" in out["tiles"] and "Mezun (pasifleşir)1" in out["tiles"]
    assert out["graduates"][0].startswith("Can Demir")
    assert out["levels"] == ["Elif Kaya7. sınıf → 8. sınıf · LGSyeni sınıftan"]
    assert out["violations"] == []


def test_islem_ve_mezun_istisnasi_onizlemeye_gider(out: dict[str, Any]) -> None:
    first, changed, kept = out["previewBodies"][:3]
    assert first["items"] == [] and first["bumpOthers"] is True
    assert {"groupId": "C1", "action": "copy"} in changed["items"]
    assert kept["keepStudentIds"] == ["S3"]
    assert out["graduatesAfter"][0].startswith("Can Demir")   # listede kalır, işaretsiz


def test_uygula_pin_ve_revizyonla_geri_al_pin(out: dict[str, Any]) -> None:
    assert out["pinTitle"] == "2026-2027 → 2027-2028 geçişi uygulansın mı?"
    assert "Geri al" in out["pinText"]
    [body] = out["applyPost"]
    assert body["pin"] == "123456" and body["expectedRevision"] == 41
    assert body["keepStudentIds"] == ["S3"] and body["fromYear"] == "2026-2027"
    assert out["undoPost"] == [[f"{BASE}/R0/undo", {"pin": "123456"}]]
    assert any("Dönem geçişi uygulandı" in text for text in out["toasts"])
    assert any("Geçiş geri alındı" in text for text in out["toasts"])
    assert out["backToList"] is True
