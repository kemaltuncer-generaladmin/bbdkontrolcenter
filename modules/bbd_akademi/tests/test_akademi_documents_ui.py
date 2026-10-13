"""Belge düğmeleri ve önizleme penceresi — panel Node'da küçük bir DOM üzerinde açılır.

Yoklama ekranında "Günlük tahta · önizle": belge modeli sunucuya gider, önizleme
penceresi `role=dialog` + ad ile açılır, sayfa görüntülerinin `alt` metni vardır,
erişilebilirlik denetiminden geçer; Esc kapatır ve odak düğmeye döner. Masaüstü
kabuğunda (Tauri) "Yazdır" önizlemesiz üretir ve PDF'i doğrudan cihazın yazıcısına
verir; kabuk yoksa yazıcı düğmesi kapalıdır ve sebebi yazar. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run
from modules.bbd_akademi.tests.test_akademi_erisilebilirlik import ROUTES

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const session = { id: 'tt:1', taken: true, takenAt: '2026-10-13T06:05:00Z', takenBy: { id: 't', displayName: 'Mehmet Hoca' },
    counts: { enrolled: 2, present: 1, absent: 1, late: 0, excused: 0 },
    scheduleItem: { id: 'tt:1', kind: 'lesson', date: '2026-10-13', startsAt: '2026-10-13T06:00:00Z', endsAt: '2026-10-13T06:40:00Z',
      subject: { id: 'mat', name: 'Matematik' }, teacher: { id: 't', displayName: 'Mehmet Hoca' },
      classGroup: { id: 'c', name: '8-A' }, status: 'scheduled' } };
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path === '/api/bbd_akademi/km/call') {
      const body = options.body || {};
      if (body.path === '/v1/admin/attendance/sessions') return { ok: true, status: 200, data: { items: [session] }, headers: {} };
      return { ok: true, status: 200, data: { items: [], nextCursor: null }, headers: {} };
    }
    if (path === '/api/bbd_akademi/km/documents/preview') {
      const preview = options.body.preview;
      return { ok: true, path: '/srv/Raporlar/Akademi/Yoklama/yoklama-gunluk.pdf', name: 'yoklama-gunluk.pdf',
        bytes: 1000, number: 'AK-YKG-261013-0900-AB12', parts: 1,
        pages: preview ? ['data:image/png;base64,iVBORw0KGgo=', 'data:image/png;base64,iVBORw0KGgo='] : [],
        pageCount: preview ? 3 : 0, shown: preview ? 2 : 0, previewError: '' };
    }
    if (path === '/api/outputs/document') return { ok: true, name: 'yoklama-gunluk.pdf', bytes: 5, data: 'JVBERi0=' };
    return undefined;
  };
  const ak = await load('./panels/bbd_akademi/index.js');
  const root = document.createElement('div');
  document.body.append(root);
  ak.mount(root, { panel: { id: 'bbd_akademi' }, api, payload: { section: 'attendance' },
    capability: () => null, open: () => {} });
  await sleep(150);
  const out = {};
  const find = (text) => root.querySelectorAll('button').find((b) => b.textContent === text);
  const preview = find('Günlük tahta · önizle');
  out.hasButton = Boolean(preview);
  preview.click();
  await sleep(120);
  const dialog = root.querySelector('[role="dialog"]');
  out.dialog = Boolean(dialog);
  out.modal = dialog?.getAttribute('aria-modal');
  const labelId = dialog?.getAttribute('aria-labelledby');
  out.label = labelId ? root.querySelector(`#${labelId}`)?.textContent : null;
  out.alts = dialog ? dialog.querySelectorAll('img').map((image) => image.getAttribute('alt')) : [];
  out.foot = dialog?.querySelector('.kit-preview-foot')?.textContent || '';
  out.note = dialog?.querySelector('.kit-preview-note')?.textContent || '';
  out.printDisabled = dialog ? dialog.querySelectorAll('button').find((b) => b.textContent === 'Yazdır')?.disabled : null;
  out.violations = audit(root);
  const sent = calls.filter((call) => call.path === '/api/bbd_akademi/km/documents/preview');
  out.sentKind = sent[0]?.body?.kind;
  out.sentPreview = sent[0]?.body?.preview;
  out.sentTitle = sent[0]?.body?.document?.parts?.[0]?.title;
  const Key = globalThis.KeyboardEvent || window.KeyboardEvent;
  (document.activeElement || document.body).dispatchEvent(new Key('keydown', { key: 'Escape', bubbles: true }));
  await sleep(20);
  out.closed = !root.querySelector('[role="dialog"]');
  out.focusBack = document.activeElement === preview;

  const invoked = [];
  window.__TAURI__ = { core: { invoke: async (command, args) => {
    invoked.push(command);
    if (command === 'printers') return { printers: ['HP LaserJet'], effective: 'HP LaserJet', selected: '', system_default: 'HP LaserJet' };
    if (command === 'printer_print') return args?.name ? 'HP LaserJet' : '';
    return null;
  } } };
  const direct = root.querySelectorAll('button').find((b) => b.textContent === 'Yazdır' && (b.title || '').startsWith('Günlük'));
  direct.click();
  await sleep(150);
  out.invoked = invoked;
  const all = calls.filter((call) => call.path === '/api/bbd_akademi/km/documents/preview');
  out.directPreview = all[all.length - 1]?.body?.preview;
  out.outputs = calls.filter((call) => call.path === '/api/outputs/document').map((call) => call.body.path);
  out.dialogAfterPrint = Boolean(root.querySelector('[role="dialog"]'));
  delete window.__TAURI__;
  return out;
}
"""


def test_onizleme_penceresi_erisilebilir_ve_dogrudan_yazdir() -> None:
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    assert out["hasButton"] is True
    assert out["dialog"] is True and out["modal"] == "true"
    assert out["label"] == "yoklama-gunluk.pdf"
    assert out["alts"] == ["Sayfa 1", "Sayfa 2"]
    assert "AK-YKG-261013-0900-AB12" in out["foot"] and "ilk 2 sayfa" in out["foot"]
    assert out["printDisabled"] is True                       # kabuk yok: yazıcı kapalı + sebep
    assert "masaüstü" in out["note"]
    assert out["violations"] == []
    assert (out["sentKind"], out["sentPreview"], out["sentTitle"]) == (
        "attendance_day", True, "Günlük Yoklama Tahtası")
    assert out["closed"] is True and out["focusBack"] is True
    # Masaüstünde "Yazdır": önizlemesiz üretim → PDF baytları → cihazın yazıcısı.
    assert out["directPreview"] is False
    assert out["outputs"] == ["/srv/Raporlar/Akademi/Yoklama/yoklama-gunluk.pdf"]
    assert "printer_print" in out["invoked"]
    assert out["dialogAfterPrint"] is False
