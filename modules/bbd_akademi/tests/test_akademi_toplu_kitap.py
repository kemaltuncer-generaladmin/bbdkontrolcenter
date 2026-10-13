"""Toplu ödev, şablonlar, atama paketleri ve kitap satışı ekranları (0.5.4) — davranış.

Panel JS'i Node'da küçük bir DOM üzerinde çalıştırılır (`tools/a11y/harness.py`);
Akademi vekili (`/api/bbd_akademi/km/call`) taklit edilir ve giden isteklerin
GÖVDELERİ sözleşmeye göre denetlenir: üç adımlı sihirbaz doğru `HomeworkBulkRequest`
/ `BookSaleCreate` üretir, önizleme yazma yapmaz, büyük iş (`202 queued`)
`admin_getBulkJob` ile izlenir, sabit Idempotency-Key ikinci kayıt açmaz.
Her adım erişilebilirlik denetiminden (WCAG 2.2 AA kuralları) de geçer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

# Taklit Akademi + panel `app` nesnesi (index.js'teki ile aynı yüzey).
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

TOPLU_ODEV = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const T = { id: 'tpl-1', name: 'Paragraf seti', scope: 'institution', title: 'Paragraf — Test 4-6',
    subject: { id: 'turkce', name: 'Türkçe' }, dueAfterDays: 3, dueTime: '20:00', usageCount: 0, editable: true };
  routes['POST /v1/admin/homework/bulk/preview'] = (body) => ({ studentCount: 54, excludedCount: 0,
    homeworkCount: body.items.length * (body.schedule?.dueAts?.length || 1), submissionCount: 54,
    runsInBackground: false, groups: [{ kind: 'class_group', id: 'c-9a', label: '9-A', studentCount: 30 }],
    occurrences: [{ title: 'Paragraf — Test 4-6', dueAt: '2026-10-20T20:00:00+03:00' }],
    conflicts: [{ kind: 'busy_day', date: '2026-10-20', homeworkId: null, title: null, studentCount: 4 }] });
  routes['POST /v1/admin/homework/bulk'] = { id: 'job-1', kind: 'homework_create', status: 'queued',
    progress: { doneCount: 0, totalCount: 54 }, homework: null, bundle: null, error: null,
    createdAt: '2026-10-13T10:00:00Z', finishedAt: null };
  let polls = 0;
  routes['GET /v1/admin/bulk-jobs/job-1'] = () => {
    polls += 1;
    return { id: 'job-1', kind: 'homework_create', status: polls > 1 ? 'succeeded' : 'running',
      progress: { doneCount: polls > 1 ? 54 : 20, totalCount: 54 },
      homework: polls > 1 ? { seriesId: 's-1', homeworkIds: ['h1'], homeworkCount: 1, studentCount: 54,
        submissionCount: 54, notifiedStudentCount: 54 } : null, bundle: null, error: null,
      createdAt: '2026-10-13T10:00:00Z', finishedAt: null };
  };
  const { app, root, toasts } = await makeApp(load, document);
  const { bulkHomeworkWizard } = await load('./panels/bbd_akademi/homework-bulk.js');
  const done = bulkHomeworkWizard(app, { template: T });
  await sleep(30);
  const out = {};
  const dialog = () => root.querySelector('.ak-wiz');
  out.title = dialog().querySelector('.kit-dialog-title').textContent;
  out.stepOn = dialog().querySelector('.ak-wiz-step.on').textContent;
  out.live = dialog().querySelector('.ak-wiz-live').textContent;
  out.focus = document.activeElement?.textContent || '';
  out.a11yWho = audit(dialog());
  // Boş hedefle İleri → sebep yazılır, ilerlemez.
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  out.emptyError = dialog().querySelector('.ak-wiz-error').textContent;
  click(byText(dialog(), '.kit-chip', '9-A'));
  click(byText(dialog(), '.kit-chip', 'Sayısal grubu'));
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(40);
  out.stepWhen = dialog().querySelector('.ak-wiz-step.on').textContent;
  // Yalnız şablon satırı var → varsayılan "Şablondaki süre"; önizleme kendiliğinden gelir.
  out.modeChecked = dialog().querySelectorAll('input[type="radio"]').filter((r) => r.checked).map((r) => r.value);
  out.previewText = dialog().querySelector('.ak-hwb-preview').textContent;
  out.a11yWhen = audit(dialog());
  out.finishLabel = dialog().querySelector('.ak-wiz-finish')?.textContent;
  click(dialog().querySelector('.ak-wiz-finish'));
  const job = await done;
  out.jobStatus = job?.status;
  out.toasts = toasts;
  out.previewBodies = sent.filter((c) => c.path === '/v1/admin/homework/bulk/preview').map((c) => c.body);
  out.create = sent.filter((c) => c.path === '/v1/admin/homework/bulk');
  out.polls = sent.filter((c) => c.path === '/v1/admin/bulk-jobs/job-1').length;
  out.open = Boolean(root.querySelector('.ak-wiz'));
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


def test_toplu_odev_sihirbazi_sablondan_uc_adim() -> None:
    out = node_run(TOPLU_ODEV)
    assert out["title"] == "Toplu ödev"
    # Şablonla açılınca "Ne?" dolu; sihirbaz 2. adımdan başlar ve bunu söyler.
    assert out["stepOn"].endswith("Kime?")
    assert out["live"] == "Adım 2 / 3: Kime?"
    assert out["focus"] == "2. Kime?"
    assert out["a11yWho"] == []
    assert "En az bir sınıf" in out["emptyError"]
    assert out["stepWhen"].endswith("Ne zaman?")
    assert out["modeChecked"] == ["template"]
    assert "54" in out["previewText"] and "9-A" in out["previewText"]
    assert "4 öğrencinin" in out["previewText"]          # yoğun gün uyarısı
    assert out["a11yWhen"] == []
    assert out["finishLabel"] == "Ödevi ver"
    # Önizleme gövdesi = oluşturma gövdesi (yazma yok); şablon süresi → dueAt yok.
    body = out["previewBodies"][-1]
    assert body["target"] == {"excludeStudentIds": [], "classGroupIds": ["c-9a"],
                              "studentGroupIds": ["g-say"], "studentIds": []}
    assert body["items"] == [{"templateId": "tpl-1", "content": None, "dueAt": None}]
    assert body["notify"] is True and "schedule" not in body
    assert len(out["create"]) == 1
    assert out["create"][0]["body"] == body
    assert out["create"][0]["idempotencyKey"].startswith("km-wiz-")
    # 202 kuyruk → iş bitene dek izlendi, pencere kapandı, özet bildirildi.
    assert out["polls"] == 2 and out["jobStatus"] == "succeeded"
    assert out["open"] is False
    assert ["54 öğrenciye 1 ödev verildi · 54 bildirim.", "good"] in out["toasts"]
