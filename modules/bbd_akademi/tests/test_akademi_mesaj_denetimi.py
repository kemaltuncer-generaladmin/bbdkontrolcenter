"""Mesaj denetimi (KM-1; F-K15 moderasyon + F-K21 KM mesaj denetimi) — KM paneli.

`coach_messaging` canlıda açık: öğrenci/veli "Şikâyet et" diyebiliyor; şikâyet
kuyruğu yalnız burada işlenir.

- `messages-logic.js` (saf, Node): gerekçe kuralı (en az 10), arama gövdesi
  (boş süzgeç gönderilmez, aralık ≤ 1 yıl, metin ≥ 3), karar / kilit gövdeleri,
  silinen mesajın özgün içeriği; metinler Flutter `ReportReason` ile aynı;
- `messages.js` (DOM taklidiyle): kuyruk → ayrıntı (bağlam, şikâyet edilen mesaj
  işaretli) → karar; gerekçesiz arama gitmez; arama → konuşmayı denetimde aç
  (özgün içerik, düzenleme geçmişi) → kilitle;
- vekil: yedi işlem izin listesinde, KM izni `settings`, menüde "Bugün" altında;
- yazışma saklama süresi (Akademi README §14 madde 84): başlıktaki "Saklama süresi"
  düğmesi `admin_putCoachingSettings` ile 1–10 yıl yazar (iyimser kilit).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import SETTINGS, km_permissions

ROOT = Path(__file__).resolve().parents[3]
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
import * as m from './messages-logic.mjs';
const tryIt = (fn) => { try { return fn(); } catch (error) { return `HATA: ${error.message}`; } };
const out = {
  reasons: Object.values(m.REPORT_REASONS),
  reasonProblems: [m.reasonProblem(''), m.reasonProblem('kısa'), m.reasonProblem('Veli şikâyeti 14.10')],
  search: [
    m.searchBody({ text: '  telefon ', kind: '', studentId: 's1', flaggedOnly: true }, { reason: ' Veli şikâyeti 14.10 ', caseRef: '' }),
    m.searchBody({}, { reason: 'Okul rehberlik talebi', caseRef: 'V-12' }, { cursor: 'c2', pageSize: 20 }),
  ],
  searchProblems: [m.searchProblem({ text: 'ab' }), m.searchProblem({ from: '2026-01-01', to: '2027-03-01' }),
    m.searchProblem({ from: '2026-10-10', to: '2026-10-01' }), m.searchProblem({ from: '2026-01-01', to: '2026-12-31', text: 'abc' })],
  decision: [tryIt(() => m.decisionBody('lock_thread', '  iletişim bilgisi istendi ')), tryIt(() => m.decisionBody('ban', 'xxxx')),
    tryIt(() => m.decisionBody('dismiss', 'no'))],
  lock: [tryIt(() => m.lockBody(true, 'zorbalık')), tryIt(() => m.lockBody(false, ''))],
  preview: [m.messagePreview({ body: 'Merhaba', attachments: [{ kind: 'image' }], card: null }),
    m.messagePreview({ body: null, deletedAt: 'x' }), m.messagePreview(null)],
  audit: [m.auditBody({ body: null, deletedAt: 'x', originalBody: 'ara beni', edits: [] }),
    m.auditBody({ body: 'son', edits: [{ previousBody: 'ilk', editedAt: 't' }] })],
  participant: m.participantText({ displayName: 'Ayşe Kaya', role: 'guardian' }),
  locked: m.LOCKED_TEXT,
  retention: {
    options: m.retentionOptions().map((o) => [o.value, o.label]),
    years: [m.retentionYears('3'), m.retentionYears(0), m.retentionYears('11'), m.retentionYears('2.5'), m.retentionYears(null)],
    text: [m.retentionText({ messageRetentionYears: 5 }), m.retentionText(null)],
    body: m.retentionPutBody({ readReceipts: true, version: 4, updatedAt: 'x', messageEditWindowMinutes: 5,
      messageRetentionYears: 2, questionPoolStaffIds: ['t1'], questionAnswerNotifyGuardian: true }, '3'),
    bad: tryIt(() => m.retentionPutBody({}, '0')),
  },
};
process.stdout.write(JSON.stringify(out));
"""


def _node_logic(folder: Path) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (folder / "messages-logic.mjs").write_bytes((PANEL / "messages-logic.js").read_bytes())
    (folder / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=folder, capture_output=True, text=True, timeout=60,
                          check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def test_saf_mantik(tmp_path: Path) -> None:
    out = _node_logic(tmp_path)
    # Flutter `ReportReason` / React `REPORT_REASONS` ile aynı metinler.
    assert out["reasons"] == ["Uygunsuz içerik", "Zorbalık / baskı", "İletişim bilgisi istendi",
                              "İstenmeyen mesaj", "Diğer"]
    assert out["reasonProblems"][0].startswith("Gerekçe en az 10")
    assert out["reasonProblems"][1].startswith("Gerekçe en az 10")
    assert out["reasonProblems"][2] is None
    first, second = out["search"]
    assert first == {"reason": "Veli şikâyeti 14.10", "caseRef": None, "studentId": "s1", "text": "telefon",
                     "flaggedOnly": True, "pageSize": 50}
    assert second == {"reason": "Okul rehberlik talebi", "caseRef": "V-12", "flaggedOnly": False, "pageSize": 20,
                      "cursor": "c2"}
    assert out["searchProblems"] == ["Metin araması en az 3 karakter olmalı.", "Tarih aralığı en çok 1 yıl olabilir.",
                                     "Bitiş tarihi başlangıçtan önce olamaz.", None]
    assert out["decision"][0] == {"decision": "lock_thread", "note": "iletişim bilgisi istendi"}
    assert out["decision"][1] == "HATA: Karar seçin."
    assert out["decision"][2].startswith("HATA: Not en az 3")
    assert out["lock"][0] == {"locked": True, "reason": "zorbalık"}
    assert out["lock"][1].startswith("HATA: Gerekçe en az 3")
    assert out["preview"] == ["Merhaba · 1 ek", "Mesaj silindi", "Mesaj silinmiş"]
    assert out["audit"][0] == {"text": "ara beni", "deleted": True, "edits": []}
    assert out["audit"][1] == {"text": "son", "deleted": False, "edits": [{"previousBody": "ilk", "editedAt": "t"}]}
    assert out["participant"] == "Ayşe Kaya (Veli)"
    assert out["locked"] == "Bu konuşma kurum tarafından kilitlendi."
    ret = out["retention"]
    assert ret["options"][0] == ["1", "1 yıl"] and ret["options"][1] == ["2", "2 yıl (varsayılan)"]
    assert len(ret["options"]) == 10 and ret["options"][-1] == ["10", "10 yıl"]
    assert ret["years"] == [3, None, None, None, None]
    assert ret["text"] == ["5 yıl", "2 yıl"]
    assert ret["body"] == {"readReceipts": True, "messageRetentionYears": 3, "questionPoolStaffIds": ["t1"],
                           "questionAnswerNotifyGuardian": True, "expectedVersion": 4}
    assert ret["bad"] == "HATA: Saklama süresi 1–10 yıl olmalı."


def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    for op_id in ("admin_listMessageReports", "admin_getMessageReport", "admin_decideMessageReport",
                  "admin_lockMessageThread", "admin_searchMessages", "admin_openAuditThread",
                  "admin_exportThread"):
        assert op_id in ops, op_id
        assert ops[op_id].feature_flag == "coaching"
        # Başkasının yazışması hassastır: yalnız kurum ayarı yetkisi.
        assert km_permissions(ops[op_id]) == (SETTINGS,)
    assert ops["admin_searchMessages"].method == "POST"   # gerekçe URL'ye konmaz


PRELUDE = r"""
const routes = {};
const sent = [];
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
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const choose = (node, value) => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); };
"""

UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  const student = { id: 'stu-1', externalRef: 'STU-1', displayName: 'Elif Kaya', firstName: 'Elif', lastName: 'Kaya',
    classGroup: { id: 'c', name: '9-A' }, status: 'active' };
  const coach = { id: 'coach-1', displayName: 'Hüseyin hoca', role: 'coach', photo: null };
  const guardian = { id: 'g-1', displayName: 'Ayşe Kaya', role: 'guardian', photo: null };
  const msg = (id, body, extra = {}) => ({ id, threadId: 't1', sender: coach, body, attachments: [], card: null,
    createdAt: '2026-10-14T08:00:00Z', deliverAt: null, delivered: true, editedAt: null, deletedAt: null,
    flags: [], readByOthers: null, clientUuid: null, ...extra });
  const report = (extra = {}) => ({ id: 'r1', threadId: 't1', threadKind: 'coach_guardian', student,
    message: msg('m2', 'Bana WhatsApp yaz 0532 000 00 00', { flags: ['contact_info', 'reported'] }),
    reporter: guardian, reason: 'contact_request', note: 'Numara istedi', status: 'open', decision: null,
    decidedBy: null, decidedAt: null, createdAt: '2026-10-14T09:00:00Z', ...extra });
  routes['GET /v1/admin/messages/reports'] = { items: [report()], nextCursor: null };
  routes['GET /v1/admin/messages/reports/r1'] = { ...report(), context: [msg('m1', 'Ödevini gördüm'),
    msg('m2', 'Bana WhatsApp yaz 0532 000 00 00', { flags: ['contact_info', 'reported'] }), msg('m3', 'Tamam')] };
  routes['POST /v1/admin/messages/reports/r1/decision'] = (body) => report({ status: 'actioned', decision: body.decision,
    decidedBy: { id: 'a', displayName: 'Müdür' }, decidedAt: '2026-10-14T10:00:00Z' });
  const thread = (status) => ({ id: 't1', kind: 'coach_guardian', status, student, participants: [coach, guardian],
    subject: null, lastMessageAt: null, lastMessagePreview: null, unreadCount: 0, hiddenByStudent: false, messageCount: 3,
    questionId: null, composer: { canSend: status !== 'locked', readOnlyReason: status === 'locked' ? 'locked' : null,
      quietHoursActive: false, deliverAt: null, readReceipts: true }, createdAt: '2026-10-01T08:00:00Z' });
  routes['POST /v1/admin/messages/search'] = { items: [{ threadId: 't1', threadKind: 'coach_guardian', student,
    participants: [coach, guardian], messageId: 'm2', sender: coach, excerpt: 'Bana WhatsApp yaz', createdAt: '2026-10-14T08:00:00Z',
    deleted: true, edited: true, flags: ['contact_info'] }], nextCursor: null };
  routes['POST /v1/admin/messages/threads/t1/audit-view'] = { thread: thread('open'), accessLogId: 'log-1', nextCursor: null,
    items: [msg('m1', 'Ödevini gördüm', { originalBody: null, edits: [{ previousBody: 'Ödevini gördm', editedAt: '2026-10-14T08:01:00Z' }],
      editedAt: '2026-10-14T08:01:00Z' }),
      msg('m2', null, { deletedAt: '2026-10-14T08:30:00Z', originalBody: 'Bana WhatsApp yaz 0532 000 00 00', edits: [] })] };
  routes['POST /v1/admin/messages/threads/t1/lock'] = (body) => thread(body.locked ? 'locked' : 'open');

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const toast = toaster(root);
  const app = { root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { staff: async () => [], async options() { return [{ value: '', label: 'Bütün personel' }]; } } };
  const { section } = await load('./panels/bbd_akademi/messages.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(60);
  const out = {};
  out.title = root.querySelector('.ak-title')?.textContent;
  out.tabs = root.querySelectorAll('[role="tab"]').map((tab) => tab.textContent.trim());
  out.listQuery = sent.find((c) => c.path === '/v1/admin/messages/reports')?.query;
  out.rowText = root.querySelector('button.kit-row')?.textContent || '';
  out.a11yList = audit(view.node);
  click(root.querySelector('button.kit-row'));
  await sleep(60);
  let drawer = root.querySelector('.kit-drawer');
  out.drawerText = drawer?.textContent || '';
  out.highlighted = drawer.querySelectorAll('.ak-msg-item.on').length;
  out.a11yDrawer = audit(drawer);
  click(byText(drawer, 'button', 'Karar ver'));
  await sleep(30);
  let dialog = root.querySelector('.kit-dialog');
  out.a11yDecision = audit(dialog);
  choose(dialog.querySelector('select'), 'lock_thread');
  type(dialog.querySelector('textarea'), 'Veliden numara istendi, konuşma kilitlendi.');
  click(byText(dialog, 'button', 'Kararı kaydet'));
  await sleep(60);
  out.decision = sent.filter((c) => c.path === '/v1/admin/messages/reports/r1/decision').map((c) => c.body);
  out.rowAfter = root.querySelector('button.kit-row')?.textContent || '';
  drawer = root.querySelector('.kit-drawer');
  out.decideGone = !byText(drawer, 'button', 'Karar ver');
  // Şikâyetten "Konuşmanın tamamını denetle": gerekçesiz açılmaz.
  click(byText(drawer, 'button', 'Konuşmanın tamamını denetle'));
  await sleep(40);
  out.auditTabOn = root.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim();
  out.reasonError = root.querySelector('.ak-msg-reason .ak-msg-error')?.textContent || '';
  out.auditCallsBefore = sent.filter((c) => c.path.endsWith('/audit-view')).length;
  // Gerekçesiz arama gitmez.
  click(byText(root, 'button', 'Ara'));
  await sleep(20);
  out.searchWithoutReason = sent.filter((c) => c.path === '/v1/admin/messages/search').length;
  type(root.querySelector('.ak-msg-reason-input'), 'Veli şikâyeti, 14.10 görüşmesi');
  click(byText(root, 'button', 'Ara'));
  await sleep(60);
  out.search = sent.filter((c) => c.path === '/v1/admin/messages/search').map((c) => c.body);
  out.a11yAudit = audit(view.node);
  const hit = root.querySelectorAll('.kit-row').find((row) => row.textContent.includes('silindi · düzenlendi'));
  click(hit);
  await sleep(60);
  drawer = root.querySelectorAll('.kit-drawer').pop();
  out.auditView = sent.filter((c) => c.path === '/v1/admin/messages/threads/t1/audit-view').map((c) => c.body);
  out.auditText = drawer?.textContent || '';
  out.a11yThread = audit(drawer);
  click(byText(drawer, 'button', 'Konuşmayı kilitle'));
  await sleep(30);
  dialog = root.querySelectorAll('.kit-dialog').pop();
  type(dialog.querySelector('textarea'), 'İletişim bilgisi paylaşımı');
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.lock = sent.filter((c) => c.path === '/v1/admin/messages/threads/t1/lock').map((c) => c.body);
  out.lockedShown = root.querySelectorAll('.kit-drawer').pop()?.textContent.includes('Bu konuşma kurum tarafından kilitlendi.');
  out.toasts = toasts;
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


def test_sikayet_karar_ve_denetim() -> None:
    out = node_run(UI)
    assert out["title"] == "Mesaj denetimi"
    assert out["tabs"] == ["Şikâyetler", "Denetim araması"]
    assert out["listQuery"] == {"status": "open", "limit": 50}
    assert "Elif Kaya" in out["rowText"] and "İletişim bilgisi istendi" in out["rowText"]
    assert out["a11yList"] == [] and out["a11yDrawer"] == [] and out["a11yDecision"] == []
    assert "Numara istedi" in out["drawerText"] and "Ödevini gördüm" in out["drawerText"]
    assert out["highlighted"] == 1                      # şikâyet edilen mesaj işaretli
    assert out["decision"] == [{"decision": "lock_thread", "note": "Veliden numara istendi, konuşma kilitlendi."}]
    assert "Konuşma kilitlendi" in out["rowAfter"]
    assert out["decideGone"] is True
    # Şikâyetten denetime geçiş: gerekçe yazılmadan konuşma açılmaz.
    assert out["auditTabOn"] == "Denetim araması"
    assert "gerekçe" in out["reasonError"].lower()
    assert out["auditCallsBefore"] == 0
    assert out["searchWithoutReason"] == 0
    (search,) = out["search"]
    assert search["reason"] == "Veli şikâyeti, 14.10 görüşmesi" and search["flaggedOnly"] is False
    assert out["a11yAudit"] == [] and out["a11yThread"] == []
    (opened,) = out["auditView"]
    assert opened["reason"] == "Veli şikâyeti, 14.10 görüşmesi" and opened["pageSize"] == 100
    # Silinen mesajın özgün içeriği ve düzenleme geçmişi yalnız denetimde görünür.
    assert "Bana WhatsApp yaz 0532 000 00 00" in out["auditText"] and "Önceki sürümler (1)" in out["auditText"]
    assert out["lock"] == [{"locked": True, "reason": "İletişim bilgisi paylaşımı"}]
    assert out["lockedShown"] is True


RETENTION_UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  const current = { quietHours: { start: '22:00', end: '07:30' }, readReceipts: true, contactInfoPolicy: 'warn',
    questionSlaBusinessHours: 24, dailyQuestionQuota: 10, videoCompletionPercent: 90, maxStudentsPerCoach: null,
    messageRetentionYears: 2, guardianDigestDefault: 'weekly', meetingReminderMinutes: [60],
    questionPoolStaffIds: [], questionAnswerNotifyGuardian: false, messageEditWindowMinutes: 5, version: 7,
    updatedAt: '2026-10-14T08:00:00Z' };
  routes['GET /v1/admin/messages/reports'] = { items: [], nextCursor: null };
  routes['GET /v1/admin/coaching/settings'] = current;
  routes['PUT /v1/admin/coaching/settings'] = (body) => ({ ...current, ...body, version: 8 });
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const app = { root, api: client, status: null, toast: (text, tone) => { toasts.push([text, tone]); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { staff: async () => [], async options() { return []; } } };
  const { section } = await load('./panels/bbd_akademi/messages.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(40);
  const out = {};
  click(byText(root, 'button', 'Saklama süresi'));
  await sleep(40);
  const dialog = root.querySelector('.kit-dialog');
  out.dialogText = dialog?.textContent || '';
  out.selected = dialog.querySelector('select').value;
  out.a11y = audit(dialog);
  choose(dialog.querySelector('select'), '3');
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.put = sent.filter((c) => c.method === 'PUT' && c.path === '/v1/admin/coaching/settings').map((c) => c.body);
  out.closed = !root.querySelector('.kit-dialog');
  out.toasts = toasts;
  return out;
}
"""


def test_yazisma_saklama_suresi() -> None:
    out = node_run(RETENTION_UI)
    assert "Yazışma saklama süresi" in out["dialogText"] and "Hocaya sor" in out["dialogText"]
    assert "Şu an: 2 yıl" in out["dialogText"] and out["selected"] == "2"
    assert out["a11y"] == []
    (body,) = out["put"]
    assert body["messageRetentionYears"] == 3 and body["expectedVersion"] == 7
    assert "version" not in body and "updatedAt" not in body and "messageEditWindowMinutes" not in body
    assert body["questionPoolStaffIds"] == [] and body["questionAnswerNotifyGuardian"] is False
    assert out["closed"] is True
    assert out["toasts"] == [["Yazışma saklama süresi 3 yıl olarak kaydedildi.", "good"]]

