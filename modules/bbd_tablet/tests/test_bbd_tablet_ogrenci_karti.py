"""Öğrenci kartındaki tablet hızlı işlemi (0.5.3, `bbd_tablet.student_quick`).

Öğrencinin tek kartı (BBD Akademi → Öğrenciler) tablet kısıtını görür ve tek
tıkla değiştirir: "Bugün sonuna kadar kısıtla" (onaylı) / "Kısıtlamayı kaldır".
Tablet ekranındaki öğrenci ayrıntısı "Öğrenci kartı" ile tek karta gider.
Node'da küçük bir DOM üzerinde (`tools/a11y`); `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_tablet/students"

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  let blocked = false;
  globalThis.__apiHook = (method, path) => {
    if (path === '/api/bbd_tablet/students/STU-1/blocks') {
      return blocked ? [{ active: true, until: '2999-01-01T23:59:00', reason: 'x' }] : [];
    }
    if (path === '/api/bbd_tablet/students/STU-1/block') { blocked = true; return { endedSessions: 1 }; }
    if (path === '/api/bbd_tablet/students/STU-1/unblock') { blocked = false; return { lifted: true }; }
    return undefined;
  };
  const tb = await load('./panels/bbd_tablet/index.js');
  const quick = await load('./panels/bbd_tablet/student-quick.js');
  const caps = tb.capabilities({ api });
  const root = document.createElement('div');
  document.body.append(root);
  const toasts = [];
  const part = caps['bbd_tablet.student_quick'](root, { studentId: 'STU-1', displayName: 'Elif',
    dialogRoot: document.body, toast: (t) => toasts.push(t) });
  await sleep(20);
  const out = { free: root.textContent, violations: audit(root) };
  root.querySelectorAll('button').find((b) => b.textContent.startsWith('Bugün sonuna')).click();
  await sleep(10);
  const dialog = document.querySelector('[role="dialog"]');
  out.dialogNamed = Boolean(dialog?.getAttribute('aria-labelledby'));
  dialog.querySelectorAll('button').find((b) => b.textContent === 'Kısıtla').click();
  await sleep(30);
  out.blockBody = calls.find((c) => c.path.endsWith('/block'))?.body;
  out.blocked = root.textContent;
  root.querySelectorAll('button').find((b) => b.textContent === 'Kısıtlamayı kaldır').click();
  await sleep(30);
  out.unblocked = root.textContent;
  out.toasts = toasts;
  out.endOfToday = quick.endOfToday(new Date(2026, 9, 13, 10, 5));
  out.active = [quick.activeBlock([{ active: true, until: '2000-01-01T00:00:00' }]),
    Boolean(quick.activeBlock([{ active: true, until: '2999-01-01T00:00:00' }]))];
  part.destroy();
  return out;
}
"""


def test_tablet_hizli_islemi() -> None:
    try:
        data = run(SCENARIO, panels=("bbd_tablet",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    r = data["result"]
    assert "serbest" in r["free"]
    assert r["violations"] == []
    assert r["dialogNamed"] is True
    assert r["blockBody"]["until"].endswith("T23:59:00")
    assert "kısıtlı" in r["blocked"]
    assert "serbest" in r["unblocked"]
    assert r["toasts"] == ["Tablet kısıtlandı; açık oturum kapatıldı.", "Tablet kısıtlaması kaldırıldı."]
    assert r["endOfToday"] == "2026-10-13T23:59:00"
    assert r["active"] == [None, True]


def test_tablet_ogrenci_ayrintisi_tek_karta_gider() -> None:
    panel = (ROOT / "modules/bbd_tablet/ui/panel/students.js").read_text(encoding="utf-8")
    index = (ROOT / "modules/bbd_tablet/ui/panel/index.js").read_text(encoding="utf-8")
    assert "button('Öğrenci kartı'" in panel and "app.openStudentCard(st.id)" in panel
    assert "ctx.capability('bbd_students.open')" in index
