"""Öğretmen = koç (kullanıcı kararı 14.10.2026) — KM ekranları ve personel rolleri.

Kullanıcı: "Koç = öğretmen. Hüseyin Hoca'yı ekledim, o öğretmen aynı zamanda." ·
"Yönetici de aynı zamanda hem yönetici hem öğretmen."

- Personel rolleri: "Yönetici", "Öğretmen / Koç" (tek öğretmen/koç rolü; çoklu seçim).
- Öğrenci detayı → Koçluk → "Koç" iç sekmesi: seçici `admin_listCoaches`'tır — Öğretmen / Koç
  rolündeki bütün etkin personel (yönetici + öğretmen olan dahil), ayrı koç tanımı yok.
  Koç ata (`admin_createCoachAssignment`), koçu değiştir (`admin_changeCoach`), eşlemeyi bitir
  (`admin_endCoachAssignment`) sözleşme gövdeleriyle gider; kapasite aşımı uyarılır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run
from modules.bbd_akademi.backend.staff import ROLE_LABELS

# Taklit Akademi + panel `app` nesnesi (test_akademi_toplu_kitap ile aynı düzen).
PRELUDE = r"""
const routes = {};          // 'METHOD /v1/...' → veri | (body, payload) => veri
const sent = [];            // vekile giden çağrılar (method, path, body, query, idempotencyKey)
globalThis.__apiHook = (method, path, options) => {
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: false, status: 404, error: { code: 'not_found', message: `taklit yok: ${key}` } };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  if (value && value.__error) return { ok: false, status: value.status || 422, error: value.__error };
  return { ok: true, status: 200, data: value, headers: {} };
};
async function makeApp(load, document, { allowed = null } = {}) {
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const ctx = { api: (path, options) => api(path, options) };
  const client = createClient(ctx);
  if (allowed) client.setOperations(allowed.map((operationId) => ({ operationId, allowed: true })));
  const toasts = [];
  const toast = toaster(root);
  const app = {
    root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: (key, payload) => { app.went = [key, payload]; },
    openPanel: () => {}, capability: () => null, feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: {
      classGroups: async () => [{ id: 'c-9a', name: '9-A' }, { id: 'c-9b', name: '9-B' }],
      studentGroups: async () => [{ id: 'g-say', name: 'Sayısal grubu' }],
      subjects: async () => [{ id: 'turkce', name: 'Türkçe' }, { id: 'matematik', name: 'Matematik' }],
      terms: async () => [], staff: async () => [], teachers: async () => [], bellSlots: async () => ({}),
      async options() { return []; },
    },
  };
  return { app, root, toasts };
}
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
"""

KOC = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const ref = (id, displayName) => ({ id, displayName });
  let history = [];
  routes['GET /v1/admin/students/stu-1/coach-assignments'] = () => ({ items: history });
  routes['GET /v1/admin/coaching/coaches'] = { items: [
    { coach: { id: 's-huseyin', displayName: 'Hüseyin Hoca', photo: null, subjects: [] }, studentCount: 20,
      maxStudents: 20, pendingReviews: 0, openQuestions: 0, medianFirstResponseHours: null,
      weeksPublishedPercent: null, studentAdherencePercent: null, meetings30d: 0 },
    { coach: { id: 's-merve', displayName: 'Merve Ak', photo: null, subjects: [] }, studentCount: 2,
      maxStudents: null, pendingReviews: 0, openQuestions: 0, medianFirstResponseHours: null,
      weeksPublishedPercent: null, studentAdherencePercent: null, meetings30d: 0 },
  ] };
  const assignment = (id, coach, extra = {}) => ({ id, student: { id: 'stu-1', displayName: 'Elif Kaya' }, coach,
    status: 'active', startsOn: '2026-10-14', endsOn: null, endReason: null, handoverNote: null,
    assignedBy: 'km:user:7:Test%20Y%C3%B6netici', createdAt: '2026-10-14T08:00:00Z', ...extra });
  routes['POST /v1/admin/coach-assignments'] = (body) => {
    history = [assignment('a-1', ref(body.coachId, 'Hüseyin Hoca'))];
    return { assignment: history[0],
      capacity: { coach: ref(body.coachId, 'Hüseyin Hoca'), studentCount: 21, maxStudents: 20, exceeded: true } };
  };
  routes['POST /v1/admin/students/stu-1/coach-change'] = (body) => {
    const ended = { ...history[0], status: 'ended', endsOn: '2026-10-14', endReason: body.reason };
    history = [assignment('a-2', ref(body.newCoachId, 'Merve Ak')), ended];
    return { ended, created: history[0], transferredTaskCount: 0, archivedThreadCount: 0,
      capacity: { coach: ref(body.newCoachId, 'Merve Ak'), studentCount: 3, maxStudents: null, exceeded: false } };
  };
  routes['POST /v1/admin/coach-assignments/a-2/end'] = (body) => {
    history = history.map((row) => (row.id === 'a-2' ? { ...row, status: 'ended', endsOn: body.endsOn,
      endReason: body.reason } : row));
    return history[0];
  };
  const { app, root, toasts } = await makeApp(load, document);
  const { coachingPanel } = await load('./panels/bbd_akademi/coaching/students-coaching.js');
  const panel = coachingPanel(app, 'stu-1');
  root.append(panel.node);
  panel.shown();
  await sleep(40);
  const out = {};
  const dialog = () => root.querySelector('.kit-dialog');
  const field = (label, selector = 'input, textarea, select') => dialog().querySelectorAll('.kit-field')
    .find((wrap) => wrap.querySelector('.kit-field-label')?.textContent.replace('*', '').trim() === label)
    ?.querySelector(selector);
  const setValue = (node, value, event = 'input') => { node.value = value; node.dispatchEvent(new Event(event, { bubbles: true })); };
  out.tabs = root.querySelectorAll('[role="tab"]').map((tab) => tab.textContent.trim());
  out.emptyText = panel.node.textContent.includes('Koç atanmamış');
  out.hint = panel.node.textContent.includes('Öğretmen / Koç');
  out.a11yTab = audit(panel.node);
  // Koç ata: seçici = bütün öğretmen / koçlar (yönetici + öğretmen Hüseyin dahil), yük yanında.
  click(byText(root, 'button', 'Koç ata'));
  await sleep(20);
  out.assignTitle = dialog().querySelector('.kit-dialog-title').textContent;
  out.assignOptions = dialog().querySelector('select').querySelectorAll('option').map((o) => o.textContent);
  out.a11yAssign = audit(dialog());
  setValue(dialog().querySelector('select'), 's-huseyin', 'change');
  click(byText(dialog(), 'button', 'Ata'));
  await sleep(60);
  out.assignBody = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/coach-assignments').map((c) => c.body);
  out.current = panel.node.textContent.includes('Hüseyin Hoca') && panel.node.textContent.includes('Test Yönetici (KM)');
  // Koçu değiştir: şu anki koç seçicide yok; gerekçe ve devir notu zorunlu.
  click(byText(root, 'button', 'Koçu değiştir'));
  await sleep(20);
  out.changeOptions = dialog().querySelector('select').querySelectorAll('option').map((o) => o.textContent);
  setValue(dialog().querySelector('select'), 's-merve', 'change');
  setValue(field('Gerekçe'), 'Koç izinli');
  setValue(field('Devir notu'), 'TYT deneme planı sürüyor');
  click(byText(dialog(), 'button', 'Değiştir'));
  await sleep(60);
  out.changeBody = sent.filter((c) => c.path === '/v1/admin/students/stu-1/coach-change').map((c) => c.body);
  // Eşlemeyi bitir.
  click(byText(root, 'button', 'Eşlemeyi bitir'));
  await sleep(20);
  setValue(field('Gerekçe'), 'Dönem sonu');
  click(byText(dialog(), 'button', 'Bitir'));
  await sleep(60);
  out.endBody = sent.filter((c) => c.path === '/v1/admin/coach-assignments/a-2/end').map((c) => c.body);
  out.afterEnd = Boolean(byText(root, 'button', 'Koç ata'));
  out.historyRows = panel.node.querySelectorAll('.kit-row').length;
  out.toasts = toasts.map(([text, tone]) => [text, tone]);
  return out;
}
"""


def node_run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_rol_etiketleri_ogretmen_koc_tek_rol() -> None:
    assert ROLE_LABELS == {"admin": "Yönetici", "teacher": "Öğretmen / Koç"}
    staff_js = (Path(__file__).resolve().parents[1] / "ui" / "panel" / "admin/staff.js").read_text(encoding="utf-8")
    assert "{ key: 'teacher', label: 'Öğretmen / Koç' }" in staff_js
    assert "'coach'" not in staff_js  # ayrı koç rolü / listesi yok


def test_koc_sekmesi_ata_degistir_bitir() -> None:
    out = node_run(KOC)
    assert out["tabs"][0] == "Koç"
    assert out["emptyText"] and out["hint"]
    assert out["a11yTab"] == [] and out["a11yAssign"] == []
    assert out["assignTitle"] == "Koç ata"
    # seçici: bütün öğretmen / koçlar, yükleriyle
    assert out["assignOptions"] == ["Öğretmen / Koç seçin", "Hüseyin Hoca · 20/20 öğrenci", "Merve Ak · 2 öğrenci"]
    assert len(out["assignBody"]) == 1
    body = out["assignBody"][0]
    assert body["studentId"] == "stu-1" and body["coachId"] == "s-huseyin" and len(body["startsOn"]) == 10
    assert out["current"]
    assert ["Hüseyin Hoca atandı.", "good"] in out["toasts"]
    assert any(tone == "warn" and "kapasiteyi aştı (21/20)" in text for text, tone in out["toasts"])
    # değiştir: şu anki koç listede yok
    assert out["changeOptions"] == ["Öğretmen / Koç seçin", "Merve Ak · 2 öğrenci"]
    change = out["changeBody"][0]
    assert change["newCoachId"] == "s-merve" and change["reason"] == "Koç izinli"
    assert change["handoverNote"] == "TYT deneme planı sürüyor" and len(change["effectiveOn"]) == 10
    assert ["Koç değişti: Merve Ak.", "good"] in out["toasts"]
    end = out["endBody"][0]
    assert end["reason"] == "Dönem sonu" and len(end["endsOn"]) == 10
    assert out["afterEnd"]  # eşleme bitince yeniden "Koç ata"
    assert ["Eşleme bitti.", "good"] in out["toasts"]
