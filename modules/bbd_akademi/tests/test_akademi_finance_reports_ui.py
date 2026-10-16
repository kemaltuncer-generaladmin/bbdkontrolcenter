"""Kurum finansı → tek tip PDF araç çubuğu (Önizle · Yazdır · PDF kaydet), Node'da.

63-finans-pdf. "Cari hesaplar" sekmesinde:
* "Önizle": süzgeç (Durum = Gecikmesi olan) belgeye gider, BÜTÜN sayfalar
  okunur (imleç sonuna kadar), önizleme penceresi yakınlaştırma ve sayfa
  sayısıyla açılır, erişilebilirlik denetiminden geçer; kalan sayfalar
  `POST /km/documents/pages` ile gelir.
* "PDF kaydet": masaüstünde kabuğun kaydet penceresi (`pdf_save`) çağrılır.
* "Yazdır": yazdırma penceresi (yazıcı, kopya, sayfa aralığı, yön); geçersiz
  aralık reddedilir, geçerlisi `printer_print`e yazıcı/kopya/aralıkla gider.
`node` yoksa atlanır.
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
  const account = (i) => ({ student: { id: `s${i}`, displayName: `Öğrenci ${i}`, externalRef: `N${i}`,
    classGroup: { id: 'c8a', name: '8-A' } }, openBalanceMinor: 1000 * i, overdueMinor: 500, creditMinor: 0,
    nextDueDate: '2026-11-01' });
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path === '/api/bbd_akademi/km/call') {
      const body = options.body || {};
      const query = body.query || {};
      if (body.path === '/v1/admin/finance/summary') return { ok: true, status: 200, headers: {},
        data: { openMinor: 0, overdueMinor: 0, creditMinor: 0, collectedMinor: 0, byMethod: [] } };
      if (body.path === '/v1/admin/finance/accounts') {
        // İki sayfa: rapor imleci sonuna kadar okumalı (ekranda yalnız ilk sayfa var).
        const second = query.cursor === 'c2';
        const items = second ? [account(3)] : [account(1), account(2)];
        return { ok: true, status: 200, headers: {}, data: { items, nextCursor: second ? null : 'c2' } };
      }
      return { ok: true, status: 200, data: { items: [], nextCursor: null }, headers: {} };
    }
    if (path === '/api/bbd_akademi/km/documents/preview') {
      const preview = options.body.preview;
      return { ok: true, path: '/srv/Raporlar/Akademi/Kurum cari/cari-hesaplar.pdf', name: 'cari-hesaplar.pdf',
        bytes: 2048, number: 'AK-CRL-261016-1630-AB12', parts: 1,
        pages: preview ? ['data:image/png;base64,iVBORw0KGgo='] : [], pageCount: preview ? 3 : 0,
        shown: preview ? 1 : 0, first: 1, previewError: '' };
    }
    if (path === '/api/bbd_akademi/km/documents/pages') {
      return { ok: true, first: options.body.first, pages: ['data:image/png;base64,iVBORw0KGgo=',
        'data:image/png;base64,iVBORw0KGgo='] };
    }
    if (path === '/api/outputs/document') return { ok: true, name: 'cari-hesaplar.pdf', bytes: 5, data: 'JVBERi0=' };
    return undefined;
  };
  const invoked = [];
  const ak = await load('./panels/bbd_akademi/index.js');
  const root = document.createElement('div');
  document.body.append(root);
  ak.mount(root, { panel: { id: 'bbd_akademi' }, api, payload: { section: 'finance' },
    capability: () => null, open: () => {} });
  await sleep(200);
  const out = {};
  const buttons = () => root.querySelectorAll('button');
  const byLabel = (label) => buttons().find((b) => b.getAttribute('aria-label') === label);
  const preview = byLabel('Cari hesaplar — önizle');
  out.toolbar = ['Cari hesaplar — önizle', 'Cari hesaplar — yazdır', 'Cari hesaplar — PDF kaydet']
    .map((label) => Boolean(byLabel(label)));
  // Süzgeç: Durum = Gecikmesi olan.
  const stateSelect = root.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Durum');
  stateSelect.value = 'overdue';
  stateSelect.dispatchEvent(new (globalThis.Event || window.Event)('change', { bubbles: true }));
  await sleep(80);
  preview.click();
  await sleep(200);
  const sent = calls.filter((call) => call.path === '/api/bbd_akademi/km/documents/preview');
  const doc = sent[0]?.body?.document;
  out.sentKind = sent[0]?.body?.kind;
  out.meta = doc?.parts?.[0]?.meta;
  out.rowCount = (doc?.parts?.[0]?.sections || []).find((s) => s.kind === 'table')?.rows?.length;
  out.accountCalls = calls.filter((call) => call.path === '/api/bbd_akademi/km/call'
    && call.body?.path === '/v1/admin/finance/accounts').map((call) => call.body?.query?.state);
  const dialog = root.querySelector('[role="dialog"]');
  out.dialog = Boolean(dialog);
  out.zoom = dialog ? ['Uzaklaştır', 'Yakınlaştır'].map((label) =>
    Boolean(dialog.querySelectorAll('button').find((b) => b.getAttribute('aria-label') === label))) : [];
  out.foot = dialog?.querySelector('.kit-preview-foot')?.textContent || '';
  const more = dialog?.querySelectorAll('button').find((b) => (b.textContent || '').startsWith('Sonraki sayfaları'));
  out.moreText = more?.textContent || '';
  more?.click();
  await sleep(80);
  out.pagesAfterMore = dialog ? dialog.querySelectorAll('img').length : 0;
  out.pagesCall = calls.filter((call) => call.path === '/api/bbd_akademi/km/documents/pages').map((call) =>
    [call.body.first, call.body.last]);
  dialog.querySelectorAll('button').find((b) => b.getAttribute('aria-label') === 'Yakınlaştır').click();
  out.zoomLabel = dialog.querySelector('.kit-rv-zoom-label')?.textContent;
  out.violations = audit(root);
  const Key = globalThis.KeyboardEvent || window.KeyboardEvent;
  (document.activeElement || document.body).dispatchEvent(new Key('keydown', { key: 'Escape', bubbles: true }));
  await sleep(20);
  out.closed = !root.querySelector('[role="dialog"]');

  // Masaüstü kabuğu: PDF kaydet → kaydet penceresi; Yazdır → seçenek penceresi.
  window.__TAURI__ = { core: { invoke: async (command, args) => {
    invoked.push([command, args]);
    if (command === 'printers') return { printers: ['HP LaserJet', 'Brother'], effective: 'HP LaserJet', selected: '',
      system_default: 'HP LaserJet' };
    if (command === 'printer_print') return args.printer || 'HP LaserJet';
    if (command === 'pdf_save') return `/Users/test/Downloads/${args.name}`;
    return null;
  } } };
  byLabel('Cari hesaplar — PDF kaydet').click();
  await sleep(150);
  out.saved = invoked.filter(([command]) => command === 'pdf_save').map(([, args]) => args.name);

  byLabel('Cari hesaplar — yazdır').click();
  await sleep(120);
  const printDialog = root.querySelector('.kit-rv-print');
  out.printDialog = Boolean(printDialog);
  out.printViolations = printDialog ? audit(root) : ['pencere yok'];
  const select = printDialog.querySelector('select');
  out.printerOptions = select.querySelectorAll('option').map((o) => o.value);
  select.value = 'Brother';
  const inputs = printDialog.querySelectorAll('input');
  inputs[0].value = '2';
  inputs[1].value = '3-1';
  const okBtn = printDialog.querySelectorAll('button').find((b) => b.textContent === 'Yazdır');
  okBtn.click();
  await sleep(60);
  out.rangeError = printDialog.querySelector('.kit-dialog-error')?.textContent || '';
  inputs[1].value = '1-2, 3';
  okBtn.click();
  await sleep(200);
  out.printed = invoked.filter(([command]) => command === 'printer_print').map(([, args]) =>
    [args.printer, args.copies, args.pages]);
  out.printClosed = !root.querySelector('.kit-rv-print');
  delete window.__TAURI__;
  return out;
}
"""


def test_finans_arac_cubugu_onizle_kaydet_yazdir() -> None:
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    assert out["toolbar"] == [True, True, True]
    assert out["sentKind"] == "finance_accounts"
    # Ekrandaki süzgeç belgeye aynen gider ve başlıkta yazar.
    assert out["meta"] == [["Sınıf", "Tümü"], ["Durum", "Gecikmesi olan"]]
    assert "overdue" in out["accountCalls"]
    # Bütün sayfalar okundu: 3 hesap + 0 grup (tek sınıf).
    assert out["rowCount"] == 3
    assert out["dialog"] is True and out["zoom"] == [True, True]
    assert "3 sayfa" in out["foot"] and "ilk 1 sayfa" in out["foot"]
    assert "2 sayfa kaldı" in out["moreText"]
    assert out["pagesCall"] == [[2, 3]] and out["pagesAfterMore"] == 3
    assert out["zoomLabel"] == "%125"
    assert out["violations"] == []
    assert out["closed"] is True
    assert out["saved"] == ["cari-hesaplar.pdf"]
    assert out["printDialog"] is True and out["printViolations"] == []
    assert out["printerOptions"] == ["HP LaserJet", "Brother"]
    assert "Sayfa aralığı anlaşılamadı" in out["rangeError"]
    assert out["printed"] == [["Brother", 2, "1-2, 3"]]
    assert out["printClosed"] is True
