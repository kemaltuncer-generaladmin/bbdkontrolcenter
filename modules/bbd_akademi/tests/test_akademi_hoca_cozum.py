"""Hoca soruları (Akademi README §14 madde 63) — KM paneli.

Kullanıcı (14.10.2026): "Hoca uzaktan soru çözümünü ses kaydı veya yazarak atabilsin, görsel de
ekleyebilsin." KM paneli mobil öğretmen ekranıyla AYNI uçları kullanır (A13):

- `questions-logic.js` (saf, Node): SLA düzeyi, süzgeç sorgusu, ek denetimi, yükleme alanları, yanıt
  gövdesi, tarayıcı kayıt MIME'ı (yalnız `audio/mp4`), ayar PUT gövdesi;
- `questions.js` (DOM taklidiyle): liste → ayrıntı çekmecesi → yanıt (`admin_answerCoachQuestion`, varsayılan
  yanıtlayan sorumlu hoca), iletişim bilgisi uyarısında "yine de gönder", soru ayarları
  (`admin_putCoachingSettings`: havuz + veliye bildirim, iyimser kilit);
- vekil: yeni üç işlem izin listesinde, çok parçalı yükleme işaretli, KM izni `learning`.
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
from modules.bbd_akademi.backend.permission_map import LEARNING, km_permissions

ROOT = Path(__file__).resolve().parents[3]
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
import * as q from './questions-logic.mjs';
const now = Date.parse('2026-10-14T12:00:00Z');
const out = {
  sla: [
    q.slaLevel({ status: 'open', slaDueAt: '2026-10-14T11:00:00Z', overdue: false }, now),
    q.slaLevel({ status: 'open', slaDueAt: '2026-10-14T14:00:00Z', overdue: false }, now),
    q.slaLevel({ status: 'forwarded', slaDueAt: '2026-10-15T14:00:00Z', overdue: false }, now),
    q.slaLevel({ status: 'answered', slaDueAt: '2026-10-14T11:00:00Z', overdue: true }, now),
    q.slaLevel({ status: 'open', slaDueAt: '2026-10-16T11:00:00Z', overdue: true }, now),
  ],
  filters: [q.filterQuery('waiting'), q.filterQuery('overdue', 's1'), q.filterQuery('all'), q.filterQuery('yok')],
  context: q.contextLabel({ subject: { name: 'Fizik' }, topic: { name: 'Newton' }, page: 12, questionNo: 4 }),
  clock: [q.clock(0), q.clock(37.4), q.clock(120)],
  kinds: [q.partKind({ type: 'audio/mp4', name: 'a.m4a' }), q.partKind({ type: '', name: 'b.M4A' }),
    q.partKind({ type: 'image/png', name: 'c.png' }), q.partKind({ type: 'application/pdf', name: 'd.pdf' }),
    q.partKind({ type: 'audio/webm', name: 'e.webm' })],
  checks: [q.checkPart({ type: 'audio/mp4', name: 'a.m4a', size: 1000 }, { durationSeconds: 121 }),
    q.checkPart({ type: 'audio/mp4', name: 'a.m4a', size: 3 * 1024 * 1024 }),
    q.checkPart({ type: 'image/jpeg', name: 'x.jpg', size: 500000 }),
    q.checkPart({ type: 'audio/webm', name: 'e.webm', size: 10 })],
  fields: [q.uploadFields('voice', { durationSeconds: 37.4, staffId: 's1' }), q.uploadFields('image'),
    q.uploadFields('voice', { durationSeconds: 500 })],
  bodies: [q.answerBody({ clientUuid: 'k1', text: '  çözüm  ', fileIds: ['f1'], staffId: 's1' }),
    q.answerBody({ clientUuid: 'k2', text: '   ', fileIds: [] }),
    q.answerBody({ clientUuid: 'k3', text: 'tel', acknowledge: true })],
  mime: [q.recorderMime((t) => t === 'audio/mp4'), q.recorderMime((t) => t === 'audio/webm;codecs=opus'),
    q.recorderMime(undefined), q.recorderMime(() => { throw new Error('x'); })],
  responder: [q.defaultResponder({ coach: { id: 'c' } }), q.defaultResponder({ coach: { id: 'c' }, forwardedTo: { id: 'f' } }),
    q.defaultResponder(null)],
  settings: q.settingsPutBody({ quietHours: { start: '22:00', end: '07:30' }, dailyQuestionQuota: 10,
    messageEditWindowMinutes: 5, version: 3, updatedAt: 'x' }, { questionPoolStaffIds: ['s1'] }),
  summary: q.answerSummary({ answers: [{ author: { displayName: 'Hüseyin hoca' }, text: 'Kısa yanıt',
    images: [{}, {}], voice: [{}] }] }),
};
process.stdout.write(JSON.stringify(out));
"""


def _node_logic(folder: Path) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (folder / "questions-logic.mjs").write_bytes((PANEL / "questions-logic.js").read_bytes())
    (folder / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(
        ["node", "run.mjs"], cwd=folder, capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def test_saf_mantik(tmp_path: Path) -> None:
    out = _node_logic(tmp_path)
    assert out["sla"] == ["late", "soon", "ok", "done", "late"]
    assert out["filters"] == [{"status": "open"}, {"overdue": True, "coachId": "s1"}, {}, {"status": "open"}]
    assert out["context"] == "Fizik · Newton · s. 12 · 4. soru"
    assert out["clock"] == ["0:00", "0:37", "2:00"]
    assert out["kinds"] == ["voice", "voice", "image", "pdf", None]
    assert out["checks"][0] == "Sesli not en çok 2 dakika olabilir."
    assert out["checks"][1] == "Ses dosyası en çok 2 MB olabilir."
    assert out["checks"][2] == ""
    assert out["checks"][3].startswith("Yalnız görsel")
    assert out["fields"] == [
        {"kind": "message_voice", "durationSeconds": "37", "staffId": "s1"},
        {"kind": "message_attachment"},
        {"kind": "message_voice", "durationSeconds": "120"},
    ]
    assert out["bodies"][0] == {"clientUuid": "k1", "attachmentFileIds": ["f1"], "text": "çözüm", "staffId": "s1"}
    assert out["bodies"][1] is None
    assert out["bodies"][2] == {"clientUuid": "k3", "attachmentFileIds": [], "text": "tel", "acknowledgeContactInfo": True}
    # Akademi yalnız m4a/AAC kabul eder: webm kaydeden tarayıcıda kayıt kapalı (dosya yüklenir).
    assert out["mime"] == ["audio/mp4", None, None, None]
    assert out["responder"] == ["c", "f", ""]
    s = out["settings"]
    assert s["expectedVersion"] == 3 and "version" not in s and "updatedAt" not in s
    assert "messageEditWindowMinutes" not in s and "quietHours" not in s
    assert s["questionPoolStaffIds"] == ["s1"] and s["questionAnswerNotifyGuardian"] is False
    assert s["dailyQuestionQuota"] == 10
    assert out["summary"] == "Hüseyin hoca: Kısa yanıt · 2 görsel · sesli not"


def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    for op_id in ("admin_getCoachQuestion", "admin_answerCoachQuestion", "admin_uploadCoachQuestionFile",
                  "admin_listCoachQuestions"):
        assert op_id in ops, op_id
        assert ops[op_id].feature_flag == "coach_questions"
        assert km_permissions(ops[op_id]) == (LEARNING,)
    assert ops["admin_uploadCoachQuestionFile"].multipart is True
    assert ops["admin_answerCoachQuestion"].multipart is False
    assert ops["admin_getCoachQuestion"].path == "/v1/admin/coaching/questions/{questionId}"


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
"""

UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  const question = (extra = {}) => ({ id: 'q1', threadId: 't1',
    student: { id: 'stu-1', displayName: 'Elif Kaya', classGroup: { id: 'c', name: '9-A' } },
    coach: { id: 's-huseyin', displayName: 'Hüseyin hoca' }, forwardedTo: null, status: 'open',
    text: 'Net kuvveti nasıl bulurum?', images: [],
    context: { taskId: null, resource: null, sectionTitle: null, page: 12, questionNo: 4,
      topic: { id: 'tp', name: 'Newton' }, subject: { id: 'fizik', name: 'Fizik' } },
    askedAt: '2026-10-14T08:00:00Z', firstAnsweredAt: null, slaDueAt: '2026-10-13T08:00:00Z', overdue: true,
    studentFeedback: null, mistakeId: null, route: 'pool', feedbackNote: null, answers: [], ...extra });
  routes['GET /v1/admin/coaching/questions'] = { items: [question()], nextCursor: null };
  routes['GET /v1/admin/coaching/questions/q1'] = question();
  let answerCalls = 0;
  routes['POST /v1/admin/coaching/questions/q1/answer'] = (body) => {
    answerCalls += 1;
    if (answerCalls === 1) return { __error: { code: 'contact_info_warning', message: 'İletişim bilgisi.' }, status: 422 };
    return question({ status: 'answered', firstAnsweredAt: '2026-10-14T09:00:00Z', answers: [
      { id: 'm1', author: { id: 's-huseyin', displayName: 'Hüseyin hoca' }, role: 'teacher', text: body.text,
        images: [], voice: [], files: [], createdAt: '2026-10-14T09:00:00Z', delivered: true }] });
  };
  routes['GET /v1/admin/coaching/settings'] = { quietHours: { start: '22:00', end: '07:30' }, readReceipts: true,
    contactInfoPolicy: 'warn', questionSlaBusinessHours: 24, dailyQuestionQuota: 10, videoCompletionPercent: 90,
    maxStudentsPerCoach: null, messageRetentionYears: 2, guardianDigestDefault: 'weekly',
    meetingReminderMinutes: [1440, 60], questionPoolStaffIds: [], questionAnswerNotifyGuardian: false,
    messageEditWindowMinutes: 5, version: 2, updatedAt: null };
  routes['PUT /v1/admin/coaching/settings'] = (body) => ({ ...body, version: 3 });

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const toast = toaster(root);
  const teachers = [{ value: 's-huseyin', label: 'Hüseyin hoca' }, { value: 's-merve', label: 'Merve Ak' }];
  const app = { root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { teachers: async () => [], staff: async () => [], async options() { return teachers; } } };
  const { section } = await load('./panels/bbd_akademi/questions.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(60);
  const out = {};
  out.title = root.querySelector('.ak-title')?.textContent;
  out.rows = root.querySelectorAll('button.kit-row').length;
  out.rowText = root.querySelector('button.kit-row')?.textContent || '';
  out.listQuery = sent.find((c) => c.path === '/v1/admin/coaching/questions')?.query;
  out.a11yList = audit(view.node);
  click(root.querySelector('button.kit-row'));
  await sleep(60);
  const drawer = root.querySelector('.kit-drawer');
  out.drawerTitle = drawer?.querySelector('.kit-drawer-title')?.textContent;
  out.drawerText = drawer?.textContent.includes('Net kuvveti nasıl bulurum?');
  out.recordDisabled = byText(drawer, 'button', 'Tarayıcıdan sesli not kaydet')?.disabled;
  out.a11yDrawer = audit(drawer);
  const textarea = drawer.querySelector('textarea');
  textarea.value = 'Önce serbest cisim diyagramı çiz. Ara: 0532 000 00 00';
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  click(byText(drawer, 'button', 'Yanıtı gönder'));
  await sleep(60);
  const confirmDialog = root.querySelectorAll('.kit-dialog').find((d) => d.textContent.includes('İletişim bilgisi'));
  out.confirm = Boolean(confirmDialog);
  click(byText(confirmDialog, 'button', 'Yine de gönder'));
  await sleep(80);
  out.answers = sent.filter((c) => c.path === '/v1/admin/coaching/questions/q1/answer').map((c) => ({ body: c.body, key: c.idempotencyKey }));
  out.afterAnswer = root.querySelector('.kit-drawer')?.textContent.includes('Önce serbest cisim diyagramı');
  out.rowAfter = root.querySelector('button.kit-row')?.textContent || '';
  click(byText(root.querySelector('.kit-drawer'), 'button', 'Kapat'));
  await sleep(20);
  // Soru ayarları: havuz + veliye bildirim; iyimser kilit.
  click(byText(root, 'button', 'Soru ayarları'));
  await sleep(60);
  const dialog = root.querySelector('.kit-dialog');
  out.settingsTitle = dialog?.querySelector('.kit-dialog-title')?.textContent;
  out.a11ySettings = audit(dialog);
  click(byText(dialog, 'button', 'Merve Ak'));
  const check = dialog.querySelector('input[type="checkbox"]');
  check.checked = true;
  check.dispatchEvent(new Event('change', { bubbles: true }));
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.settingsPut = sent.filter((c) => c.method === 'PUT' && c.path === '/v1/admin/coaching/settings').map((c) => c.body);
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


def test_liste_yanit_ve_ayarlar() -> None:
    out = node_run(UI)
    assert out["title"] == "Hoca soruları"
    assert out["rows"] == 1
    assert "Elif Kaya" in out["rowText"] and "Gecikti" in out["rowText"] and "Öğretmen havuzuna" in out["rowText"]
    assert out["listQuery"]["status"] == "open"
    assert out["a11yList"] == [] and out["a11yDrawer"] == [] and out["a11ySettings"] == []
    assert out["drawerTitle"] == "Elif Kaya" and out["drawerText"]
    # MediaRecorder (m4a) yoksa tarayıcı kaydı kapalı; m4a dosyası yüklenir.
    assert out["recordDisabled"] is True
    # İletişim bilgisi uyarısı → "yine de gönder": aynı clientUuid, onaylı ikinci istek.
    assert out["confirm"]
    first, second = out["answers"]
    assert first["body"]["clientUuid"] == second["body"]["clientUuid"]
    assert first["body"]["staffId"] == "s-huseyin"            # varsayılan: sorumlu hoca
    assert first["body"]["text"].startswith("Önce serbest cisim")
    assert "acknowledgeContactInfo" not in first["body"] and second["body"]["acknowledgeContactInfo"] is True
    assert out["afterAnswer"]
    assert "Yanıtlandı" in out["rowAfter"]
    assert ["Yanıt gönderildi; öğrenciye bildirim gitti.", "good"] in out["toasts"]
    assert out["settingsTitle"] == "Soru ayarları"
    (put,) = out["settingsPut"]
    assert put["expectedVersion"] == 2 and put["questionPoolStaffIds"] == ["s-merve"]
    assert put["questionAnswerNotifyGuardian"] is True
    assert "version" not in put and "messageEditWindowMinutes" not in put
    assert ["Soru ayarları kaydedildi.", "good"] in out["toasts"]
