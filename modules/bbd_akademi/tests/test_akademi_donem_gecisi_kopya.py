"""Ders programı › "Dönem geçişi kopyası" (iş 37 / İ4) — Node'da küçük DOM.

KM dönem geçişi eski sınıfları yeni döneme taşır (7-A → 8-A); ders programı ve sınıf
müfredatı Akademi'dedir. Pencere KM'nin son geçişinin eşlemesini okur, sınıfları Akademi'de
ad + dönemle bulur (eski: arşivdeki, yeni: canlı), her eşleme için programı
(`admin_copyTimetable`, kaynak gün = eski üyeliklerin son günü) ve — hedef dönem güncel
dönemken — sınıf müfredatını (`admin_changeClassGroupCurricula`) kopyalar; Akademi'ye
henüz eşitlenmemiş sınıfı ayrıca söyler. `node` yoksa atlanır.
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
NEW = (f"{_TODAY.year}-{_TODAY.year + 1}" if _TODAY.month >= 9
       else f"{_TODAY.year - 1}-{_TODAY.year}")
OLD = f"{int(NEW[:4]) - 1}-{NEW[:4]}"

LATEST = {"id": "R1", "fromYear": OLD, "toYear": NEW, "effectiveFrom": f"{NEW[:4]}-09-01",
          "closeDay": f"{NEW[:4]}-08-31", "state": "applied", "pairs": [
              {"fromName": "7-A", "toName": "8-A", "toYear": NEW, "joined": False},
              {"fromName": "11-SAY", "toName": "12-SAY", "toYear": NEW, "joined": False},
              {"fromName": "TYT", "toName": "TYT", "toYear": NEW, "joined": False}]}
CLASSES = {"items": [
    {"id": "old7", "name": "7-A", "kmClassName": "7-A", "academicYear": OLD, "archived": True},
    {"id": "new8", "name": "8-A", "kmClassName": "8-A", "academicYear": NEW, "archived": False},
    # Geçen yılın 8-A'sı (mezun edildi) — yeni 8-A ile karışmamalı.
    {"id": "old8", "name": "8-A", "kmClassName": "8-A", "academicYear": OLD, "archived": True},
    {"id": "old11", "name": "11-SAY", "kmClassName": "11-SAY", "academicYear": OLD, "archived": True},
    {"id": "oldTyt", "name": "TYT", "kmClassName": "TYT", "academicYear": OLD, "archived": True},
    {"id": "newTyt", "name": "TYT", "kmClassName": "TYT", "academicYear": NEW, "archived": False},
]}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { rolloverCopyDialog, matchRolloverPairs } = await load('./panels/bbd_akademi/lessons/timetable-copy.js');
  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  globalThis.__apiHook = (method, path, options) => {
    if (method === 'POST' && path === '/v1/admin/timetable/copy') {
      const target = options.body.targets[0].classGroupId;
      if (target === 'newTyt') return { results: [{ classGroupId: target, status: 'conflict', conflicts: [] }] };
      return { results: [{ classGroupId: target, status: options.body.dryRun ? 'would_copy' : 'copied',
        conflicts: [] }] };
    }
    if (method === 'POST' && path.endsWith('/curricula')) return { items: [], current: true };
    return undefined;
  };
  const root = document.createElement('div');
  document.body.append(root);
  const toasts = [];
  const app = {
    root,
    toast: (text, tone) => toasts.push([text, tone]),
    kmGet: (path) => api(path),
    api: {
      can: () => true,
      featureOff: () => false,
      get: (path, query) => api(`${path}${qs(query)}`),
      post: (path, body, options) => api(path, { method: 'POST', body, key: options?.key }),
      invalidate: () => {},
    },
  };
  const out = {};
  out.matched = matchRolloverPairs(__LATEST__, __CLASSES__.items)
    .map((item) => [item.pair.toName, item.source?.id || null, item.target?.id || null]);
  const pending = rolloverCopyDialog(app);
  await sleep(40);
  const dialog = root.querySelector('.kit-dialog');
  out.title = dialog.querySelector('.kit-dialog-title').textContent;
  out.text = dialog.querySelector('.kit-dialog-text').textContent;
  out.chips = dialog.querySelectorAll('.kit-chip').map((chip) => [chip.textContent, chip.getAttribute('aria-pressed')]);
  out.labels = dialog.querySelectorAll('.kit-field-label').map((node) => node.textContent);
  out.violations = audit(dialog);
  dialog.querySelectorAll('button').find((b) => b.textContent === 'Çakışmaları denetle').click();
  await sleep(30);
  out.dry = calls.filter((c) => c.path === '/v1/admin/timetable/copy' && c.body.dryRun).length;
  dialog.querySelectorAll('button').find((b) => b.textContent === 'Kopyala').click();
  await sleep(60);
  const result = await pending;
  out.copies = calls.filter((c) => c.path === '/v1/admin/timetable/copy' && !c.body.dryRun).map((c) => c.body);
  out.curricula = calls.filter((c) => c.path.includes('/curricula')).map((c) => [c.method, c.path, c.body]);
  out.results = result.results.map((row) => [row.from, row.to, row.program?.status, row.curricula]);
  out.toasts = toasts;
  out.drawer = root.querySelector('.kit-drawer, .ak-drawer, [role="dialog"]')?.textContent || '';
  return out;
}
"""


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    routes = {
        "GET /api/bbd_students/groups/rollover": {"latest": LATEST},
        "GET /v1/admin/class-groups?archived=true": CLASSES,
        "GET /v1/admin/class-groups/old7/curricula*": {"items": [
            {"program": {"id": "lgs-matematik"}}, {"program": {"id": "lgs-fen"}}]},
        "GET /v1/admin/class-groups/oldTyt/curricula*": {"items": []},
    }
    scenario = (SCENARIO.replace("__LATEST__", json.dumps(LATEST))
                .replace("__CLASSES__", json.dumps(CLASSES)))
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_eslesme_ad_ve_donemle_eski_arsivden_yeni_canlidan(out: dict[str, Any]) -> None:
    assert out["matched"] == [["8-A", "old7", "new8"], ["12-SAY", "old11", None],
                              ["TYT", "oldTyt", "newTyt"]]


def test_pencere_eslenenleri_ve_eksigi_soyler(out: dict[str, Any]) -> None:
    assert out["title"] == f"Dönem geçişi kopyası · {OLD} → {NEW}"
    assert "12-SAY" in out["text"] and "henüz yok" in out["text"]
    assert out["chips"] == [["7-A → 8-A", "true"], ["TYT → TYT", "true"]]
    assert "Sınıf müfredatı da kopyalansın" in out["labels"]
    assert out["violations"] == []
    assert out["dry"] == 2


def test_kopyala_program_ve_mufredat(out: dict[str, Any]) -> None:
    first, second = out["copies"]
    assert first["sourceClassGroupId"] == "old7" and first["targets"][0]["classGroupId"] == "new8"
    assert first["sourceDate"] == f"{NEW[:4]}-08-31"
    assert first["keepTeachers"] is True and first["dryRun"] is False
    assert second["targets"][0]["classGroupId"] == "newTyt"
    assert ["POST", "/v1/admin/class-groups/new8/curricula",
            {"add": ["lgs-matematik", "lgs-fen"], "remove": []}] in out["curricula"]
    assert out["results"] == [["old7", "new8", "copied", 2], ["oldTyt", "newTyt", "conflict", 0]]
    assert out["toasts"][-1][0] == "1 sınıfın programı kopyalandı · 1 sınıf atlandı."
