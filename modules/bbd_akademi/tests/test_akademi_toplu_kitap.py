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


KITAP_SAT = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  routes['GET /v1/catalog/books'] = { page: 1, hasMore: false, items: [
    { productId: 'P1', title: 'TYT Matematik Soru Bankası', isbn: '9786050000001', author: null,
      publisher: 'Yayınevi', priceMinor: 25000, inStock: true, coverUrl: null },
    { productId: 'P2', title: 'Tükenen Kitap', isbn: null, author: null, publisher: null, priceMinor: 1000,
      inStock: false, coverUrl: null }] };
  routes['POST /v1/admin/book-sales'] = (body) => ({ id: 'sale-1', ref: 'KS-2026-0001', paymentMethod: body.paymentMethod,
    items: [], studentCount: 31, totalMinor: 31 * 50000, note: null, createdAt: '2026-10-13T10:00:00Z',
    createdBy: { id: 'u1', displayName: 'KM' }, counts: { awaitingPayment: 0, processing: 31, ordered: 0,
      orderFailed: 0, delivered: 0, cancelled: 0 }, lines: [] });
  const { app, root, toasts } = await makeApp(load, document);
  const { sellWizard } = await load('./panels/bbd_akademi/book-sales.js');
  const done = sellWizard(app);
  await sleep(30);
  const dialog = () => root.querySelector('.ak-wiz');
  const out = { steps: dialog().querySelectorAll('.ak-wiz-step').map((n) => n.textContent) };
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  out.emptyCart = dialog().querySelector('.ak-wiz-error').textContent;
  const search = dialog().querySelector('input[type="search"]');
  search.value = 'tyt mat';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(350);
  out.outOfStock = dialog().querySelector('.ak-bs-results').textContent.includes('Stokta yok');
  click(byText(dialog(), 'button', 'Ekle'));
  const qty = dialog().querySelector('.ak-bs-qty');
  qty.value = '2';
  qty.dispatchEvent(new Event('change', { bubbles: true }));
  out.note = dialog().querySelector('.ak-wiz-note').textContent;
  out.a11yBooks = audit(dialog());
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  out.emptyStudents = dialog().querySelector('.ak-wiz-error').textContent;
  click(byText(dialog(), '.kit-chip', '9-A'));
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  click(dialog().querySelector('.ak-wiz-finish'));
  await sleep(20);
  out.noMethod = dialog().querySelector('.ak-wiz-error').textContent;
  click(dialog().querySelectorAll('input[type="radio"]').find((r) => r.value === 'cari'));
  out.summary = dialog().querySelector('.ak-bs-summary').textContent;
  out.a11yPay = audit(dialog());
  click(dialog().querySelector('.ak-wiz-finish'));
  const sale = await done;
  out.saleRef = sale?.ref;
  out.create = sent.filter((c) => c.path === '/v1/admin/book-sales');
  out.toasts = toasts;
  return out;
}
"""

PAKET_UYGULA = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const bundle = { id: 'b-1', name: '12 SAY dönem başı', description: null, programs: [], resources: [
    { id: 'r1', title: 'AYT Fizik' }], resourcePackages: [], studyPlanTemplate: null, planStartOffsetDays: 0,
    rules: [{ gradeLevel: 12, track: 'SAY', examTarget: 'YKS' }], autoApply: true, appliedStudentCount: 0,
    createdAt: '2026-10-13T10:00:00Z', updatedAt: '2026-10-13T10:00:00Z' };
  routes['GET /v1/admin/templates'] = { items: [], nextCursor: null };
  routes['GET /v1/admin/assignment-bundles'] = { items: [bundle], nextCursor: null };
  routes['POST /v1/admin/assignment-bundles/b-1/preview'] = { studentCount: 420, alreadyAppliedCount: 3,
    excludedCount: 0, programAdds: 0, resourceAssignments: { created: 417, alreadyAssigned: 3 }, planTaskCount: 0,
    runsInBackground: true, groups: [] };
  routes['POST /v1/admin/assignment-bundles/b-1/apply'] = { id: 'job-9', kind: 'bundle_apply', status: 'queued',
    progress: { doneCount: 0, totalCount: 420 }, homework: null, bundle: null, error: null,
    createdAt: '2026-10-13T10:00:00Z', finishedAt: null };
  routes['GET /v1/admin/bulk-jobs/job-9'] = { id: 'job-9', kind: 'bundle_apply', status: 'succeeded',
    progress: { doneCount: 420, totalCount: 420 }, homework: null, bundle: { studentCount: 417,
      alreadyAppliedCount: 3, programOverridesCreated: 0, resourceAssignmentsCreated: 417,
      resourceAssignmentsRemoved: 0, programOverridesRemoved: 0, planAssignmentId: null }, error: null,
    createdAt: '2026-10-13T10:00:00Z', finishedAt: '2026-10-13T10:00:05Z' };
  const { app, root, toasts } = await makeApp(load, document);
  const templates = await load('./panels/bbd_akademi/templates.js');
  const view = templates.section(app);
  root.append(view.node);
  view.shown({ tab: 'bundles' });
  await sleep(60);
  const out = { tabs: root.querySelectorAll('[role="tab"]').map((t) => t.textContent),
    selected: root.querySelector('[role="tab"][aria-selected="true"]').textContent,
    rows: root.querySelectorAll('.ak-link').map((b) => b.textContent), a11yList: audit(root) };
  click(byText(root, 'button', 'Uygula'));
  await sleep(40);
  const dialog = () => root.querySelector('.kit-dialog');
  out.modes = dialog().querySelectorAll('input[type="radio"]').map((r) => r.value);
  click(dialog().querySelectorAll('input[type="radio"]').find((r) => r.value === 'rules'));
  out.rulesText = dialog().textContent.includes('12. sınıf · Sayısal · YKS hedefi');
  click(byText(dialog(), 'button', 'Önce say'));
  await sleep(30);
  out.preview = dialog().textContent.includes('417');
  out.a11yDialog = audit(dialog());
  click(byText(dialog(), 'button', 'Uygula'));
  await sleep(1800);
  out.previewBody = sent.find((c) => c.path.endsWith('/preview'))?.body;
  out.applyBody = sent.find((c) => c.path.endsWith('/apply'))?.body;
  out.polls = sent.filter((c) => c.path === '/v1/admin/bulk-jobs/job-9').length;
  out.toasts = toasts;
  return out;
}
"""

TOPLU_ERTELE = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const row = (id, title) => ({ id, title, subject: { id: 'turkce', name: 'Türkçe' }, topics: [],
    target: { classGroupIds: ['c-9a'], studentGroupIds: [], studentIds: [] }, dueAt: '2026-12-01T20:00:00+03:00',
    stats: { assignedCount: 30, completedCount: 3, overdueCount: 0 }, createdBy: { displayName: 'KM' },
    createdAt: '2026-10-13T10:00:00Z', seriesId: 's-1', status: 'published' });
  routes['GET /v1/admin/homework'] = { items: [row('h1', 'Paragraf 1'), row('h2', 'Paragraf 2')], nextCursor: null };
  routes['POST /v1/admin/homework/bulk-actions'] = (body) => ({ action: body.action, affectedCount: 2, skipped: [] });
  const { app, root, toasts } = await makeApp(load, document);
  const homework = await load('./panels/bbd_akademi/homework.js');
  const view = homework.section(app);
  root.append(view.node);
  view.shown({});
  await sleep(60);
  const bar = root.querySelector('.ak-hw-bulk');
  const out = { barHiddenFirst: bar.hidden };
  root.querySelectorAll('.kit-row:not(.kit-head) input[type="checkbox"]').forEach((box) => click(box));
  await sleep(10);
  out.count = bar.textContent.includes('2 ödev seçili') && !bar.hidden;
  out.a11y = audit(root);
  click(byText(bar, 'button', 'Ertele'));
  await sleep(20);
  const dialog = root.querySelector('.kit-dialog');
  const days = dialog.querySelector('input');
  days.value = '3';
  days.dispatchEvent(new Event('input', { bubbles: true }));
  click(byText(dialog, 'button', 'Ertele'));
  await sleep(40);
  out.body = sent.find((c) => c.path === '/v1/admin/homework/bulk-actions')?.body;
  out.toasts = toasts;
  out.barHiddenAfter = bar.hidden;
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


def test_kitap_satisi_uc_adim() -> None:
    out = node_run(KITAP_SAT)
    assert out["steps"] == ["1Kitaplar", "2Öğrenciler", "3Ödeme"]
    assert "en az bir kitap" in out["emptyCart"]
    assert out["outOfStock"] is True                      # stokta olmayan sepete eklenemez
    assert "500" in out["note"] and "1 kitap" in out["note"]   # 2 adet × 250 ₺, öğrenci başına
    assert out["a11yBooks"] == []
    assert "En az bir sınıf" in out["emptyStudents"]
    assert out["noMethod"] == "Ödeme şeklini seçin."
    assert "Kurum carisine yaz" in out["summary"]
    assert out["a11yPay"] == []
    assert out["saleRef"] == "KS-2026-0001"
    assert len(out["create"]) == 1
    body = out["create"][0]["body"]
    assert body == {"items": [{"productId": "P1", "quantity": 2}], "studentIds": [], "groupIds": ["c-9a"],
                    "paymentMethod": "cari", "dueDate": None, "reference": None, "note": None}
    assert out["create"][0]["idempotencyKey"].startswith("km-wiz-")
    assert any("KS-2026-0001" in text for text, _ in out["toasts"])


def test_atama_paketi_kuralina_uyanlara_uygula_arka_plan() -> None:
    out = node_run(PAKET_UYGULA)
    assert out["tabs"] == ["Ödev şablonları", "Atama paketleri", "Plan şablonları"]
    assert out["selected"] == "Atama paketleri"
    assert out["rows"] == ["12 SAY dönem başı"]
    assert out["a11yList"] == []
    assert out["modes"] == ["pick", "filter", "rules"]
    assert out["rulesText"] is True
    assert out["preview"] is True
    assert out["a11yDialog"] == []
    expected = {"target": {"excludeStudentIds": [], "matchRules": True}, "startDate": None, "notify": True}
    assert out["previewBody"] == expected and out["applyBody"] == expected
    assert out["polls"] == 1                               # 202 → admin_getBulkJob
    assert any("417 öğrenciye uygulandı" in text for text, _ in out["toasts"])


def test_secilen_odevleri_toplu_ertele() -> None:
    out = node_run(TOPLU_ERTELE)
    assert out["barHiddenFirst"] is True
    assert out["count"] is True
    assert out["a11y"] == []
    assert out["body"] == {"homeworkIds": ["h1", "h2"], "action": "postpone", "shiftDays": 3, "notify": True}
    assert ["2 ödev ertelendi.", "good"] in out["toasts"]
    assert out["barHiddenAfter"] is True
