"""Koçluk yönetimi (KM-4; K1 koçluk bloğu + F-K17 görüşmeler + F-K10 video) — KM paneli.

Öğretmen = koç; KM kurumun koçluğunu tek yerden yönetir, her koç işini koç
adına yapabilir.

- `coaching-logic.js` (saf, Node): görev gövdesi (taslak, kanıt, ölçüt, video),
  hafta değişiklikleri (`weekOps`), kayıt özeti, kontrol / toplu onay / toplu koç
  atama / görüşme kararı / check-in sorusu / koç profili / dışa aktarım /
  kütüphane gövdeleri; metinler tasarım panoları ve istemcilerle aynı;
- `coaching.js` (DOM taklidiyle): kontrol kuyruğu → teslim → onay; haftalık
  program (derin bağlantı) → görev taşı → yayımla; Koçlar → toplu koç ata
  (önizleme → uygula);
- vekil: koçluk işlemleri izin listesinde, KM izinleri `permission_map`'ten,
  menüde "Öğrenciler" altında "Koçluk".
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
from modules.bbd_akademi.backend.permission_map import (
    LEARNING,
    LEARNING_SETUP,
    SETTINGS,
    VIEW,
    km_permissions,
)

ROOT = Path(__file__).resolve().parents[3]
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
import * as m from './coaching-logic.mjs';
const out = {
  task: m.taskInput({ date: '2026-10-15', type: 'question_solving', estimatedMinutes: '40', title: ' Türev testi ',
    subjectId: 'MAT', topicId: '', resourceId: 'r1', sectionIds: ['s1'], pageFrom: '12', pageTo: '20', targetQuestions: '',
    instructions: ' 1-20 arası ', questionFrom: '1', questionTo: '20', priority: 'high', proofPhoto: 'required',
    answersRequired: true, minAccuracyPercent: '70', videos: [], attachmentFileIds: ['f1'], addToShelf: true }),
  taskPublished: m.taskInput({ date: '2026-10-15', type: 'free', estimatedMinutes: 30 }, { draft: false }),
  taskErrors: [m.taskInput({ date: '', type: 'free', estimatedMinutes: 30 }).error,
    m.taskInput({ date: '2026-10-15', type: 'free', estimatedMinutes: 3 }).error,
    m.taskInput({ date: '2026-10-15', type: 'video', estimatedMinutes: 30, videos: [] }).error,
    m.taskInput({ date: '2026-10-15', type: 'free', estimatedMinutes: 30, pageFrom: 9, pageTo: 4 }).error],
  video: m.videoInput({ videoId: 'abcdefghijk', startSeconds: 0, endSeconds: 600, chapters: [{ atSeconds: 60, label: 'Giriş' }],
    checkpoints: [{ prompt: 'Türev nedir?', kind: 'single_choice', options: ['Eğim', ' ', 'Alan'], correctIndex: 0 },
      { prompt: 'x', kind: 'short_text' }] }, 2),
  ops: m.weekOps([
    { op: 'move', clientKey: 'a', taskId: 't1', date: '2026-10-14' },
    { op: 'move', clientKey: 'b', taskId: 't1', date: '2026-10-16', order: 2 },
    { op: 'update', clientKey: 'c', taskId: 't2', task: { date: '2026-10-15' } },
    { op: 'delete', clientKey: 'd', taskId: 't2' },
    { op: 'create', clientKey: 'e', task: { date: '2026-10-17' } }]),
  summary: m.saveSummary({ results: [{ clientKey: 'a', result: 'moved', warnings: ['over_capacity'] },
    { clientKey: 'b', result: 'error', errorCode: 'task_locked', warnings: [] },
    { clientKey: 'c', result: 'created', warnings: ['over_capacity', 'holiday'] }] }),
  review: [m.reviewBody({ clientUuid: 'u', decision: 'approve', reaction: '👏' }),
    m.reviewBody({ clientUuid: 'u', decision: 'redo', comment: 'ok' }),
    m.reviewBody({ clientUuid: 'u', decision: 'redo', comment: 'Sayfa 14 eksik', redoDate: '2026-10-16' })],
  bulkApprove: [m.bulkApproveBody({ taskIds: ['t1', 't2'] }),
    m.bulkApproveBody({ filter: { minAccuracyPercent: 80, photoRequired: false, coachId: null, studentId: '' }, comment: ' Süper ' }),
    m.bulkApproveBody({})],
  bulkAssign: [m.bulkAssignBody({ classGroupIds: ['c1'], gradeLevels: ['9', 'graduate'], coachId: 'k1' }),
    m.bulkAssignBody({ studentIds: ['s1'], coachId: 'k1', onExisting: 'change' }, { dryRun: false }),
    m.bulkAssignBody({ coachId: 'k1' }), m.bulkAssignBody({ studentIds: ['s1'] })],
  meeting: [m.meetingDecisionBody('decline', {}), m.meetingDecisionBody('reschedule', { startsAt: '2026-10-20T15:00:00+03:00' }),
    m.meetingDecisionBody('confirm', { room: 'Rehberlik', reason: '' })],
  checkin: [m.checkinQuestion({ kind: 'single_choice', prompt: 'Nasıl geçti?', options: 'İyi\nOrta\n\nKötü' }),
    m.checkinQuestion({ kind: 'scale', prompt: 'Motivasyon', scaleMax: 12 }), m.checkinQuestion({ kind: 'short_text', prompt: 'a' })],
  profile: [m.profilePut({ displayName: 'Hüseyin', subjects: [{ id: 'MAT' }], bio: null, messageHours: null,
    submissionDigest: 'hourly', meetingAutoConfirm: false }, { messageStart: '09:00', messageEnd: '18:00', displayName: 'Hüseyin hoca' }),
    m.profilePut({ subjects: [], messageHours: null }, { messageStart: '18:00', messageEnd: '09:00' })],
  exportBody: [m.exportBody({ kind: 'coach_performance', format: 'xlsx', from: '2026-09-01', to: '2026-10-01', coachIds: [] }),
    m.exportBody({ kind: 'coach_performance', format: 'zip', from: '2026-09-01', to: '2026-10-01' })],
  library: [m.libraryInput({ kind: 'youtube_clip', title: 'Türev', videoId: 'abcdefghijk', start: '1:00', end: '5:30',
    tags: 'türev, 11. sınıf', gradeLevels: ['11'] }), m.libraryInput({ kind: 'web_link', title: 'EBA', url: 'eba.gov.tr' })],
  playlist: [m.playlistIdOf('https://www.youtube.com/watch?v=abc&list=PLx1234567890'), m.playlistIdOf('https://youtu.be/x')],
  clock: [m.clockText(75), m.clockText(3725), m.parseClock('1:05'), m.parseClock('1:02:03'), m.parseClock('x')],
  week: [m.weekLabel('2026-10-12'), m.weekLabel('2026-10-26'), m.mondayOf('2026-10-15')],
  badge: [m.badgeText({ name: 'İstikrar', icon: 'medal' }), m.badgeText({ name: 'Yeni', icon: 'bilinmeyen' })],
  labels: [m.QUEUE_KINDS.review, m.SAVE_WARNINGS.over_capacity, m.MEETING_STATUS.no_show[0]],
};
process.stdout.write(JSON.stringify(out));
"""


def _node_logic(folder: Path) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (folder / "coaching-logic.mjs").write_bytes((PANEL / "coaching-logic.js").read_bytes())
    (folder / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=folder, capture_output=True, text=True, timeout=60,
                          check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def test_saf_mantik(tmp_path: Path) -> None:
    out = _node_logic(tmp_path)
    task = out["task"]["task"]
    assert task["title"] == "Türev testi" and task["estimatedMinutes"] == 40
    assert task["pageFrom"] == 12 and task["pageTo"] == 20 and "targetQuestions" not in task
    coach = task["coach"]
    assert coach["draft"] is True                       # yeni görev taslak: yayımlanana dek öğrenci görmez
    assert coach["instructions"] == "1-20 arası" and coach["questionFrom"] == 1 and coach["questionTo"] == 20
    assert coach["priority"] == "high" and coach["proof"] == {"photo": "required", "answersRequired": True}
    assert coach["successCriterion"] == {"minAccuracyPercent": 70}
    assert coach["attachmentFileIds"] == ["f1"] and coach["addToShelf"] is True
    assert out["taskPublished"]["task"]["coach"]["draft"] is False
    assert out["taskErrors"] == ["Gün seçin.", "Süre 5–600 dakika olmalı.", "Video görevine en az bir video ekleyin.",
                                 "Bitiş sayfası başlangıçtan küçük olamaz."]
    video = out["video"]
    assert video["videoId"] == "abcdefghijk" and video["order"] == 2 and video["endSeconds"] == 600
    assert video["checkpoints"] == [{"prompt": "Türev nedir?", "kind": "single_choice", "atSeconds": None,
                                     "options": ["Eğim", "Alan"], "correctIndex": 0}]
    # Aynı görevin iki taşıması tek `move`; silinen görevin güncellemesi düşer.
    assert out["ops"] == [
        {"op": "move", "clientKey": "a", "taskId": "t1", "date": "2026-10-16", "order": 2},
        {"op": "delete", "clientKey": "d", "taskId": "t2"},
        {"op": "create", "clientKey": "e", "task": {"date": "2026-10-17"}},
    ]
    assert out["summary"] == {"saved": 2, "errors": [{"clientKey": "b", "errorCode": "task_locked"}],
                              "warnings": ["Kapasite aşıldı", "Tatil günü"]}
    approve, redo_short, redo = out["review"]
    assert approve == {"body": {"clientUuid": "u", "decision": "approve", "reaction": "👏"}}
    assert redo_short["error"].startswith("Tekrar isterken gerekçe")
    assert redo["body"] == {"clientUuid": "u", "decision": "redo", "redoDate": "2026-10-16", "comment": "Sayfa 14 eksik"}
    selected, filtered, empty = out["bulkApprove"]
    assert selected == {"body": {"taskIds": ["t1", "t2"]}}
    assert filtered == {"body": {"filter": {"minAccuracyPercent": 80}, "comment": "Süper"}}
    assert empty["error"] == "Onaylanacak teslim seçin."
    preview, change, no_target, no_coach = out["bulkAssign"]
    assert preview["body"] == {"target": {"classGroupIds": ["c1"], "gradeLevels": [9, "graduate"]}, "coachId": "k1",
                               "onExisting": "skip", "dryRun": True}
    assert change["error"].startswith("Koçu değişecek öğrenciler için")
    assert no_target["error"].startswith("Sınıf, grup") and no_coach["error"] == "Koç seçin."
    decline, reschedule, confirm = out["meeting"]
    assert decline["error"].startswith("Reddetme gerekçesi")
    assert reschedule["body"] == {"decision": "reschedule", "startsAt": "2026-10-20T15:00:00+03:00"}
    assert confirm["body"] == {"decision": "confirm", "room": "Rehberlik"}
    choice, scale, short = out["checkin"]
    assert choice["question"] == {"kind": "single_choice", "prompt": "Nasıl geçti?", "options": ["İyi", "Orta", "Kötü"]}
    assert scale["error"] == "Ölçek üst değeri 3–10 olmalı." and short["error"].startswith("Soru en az 3")
    profile, bad_hours = out["profile"]
    assert profile["body"] == {"subjectIds": ["MAT"], "bio": None, "messageHours": {"start": "09:00", "end": "18:00"},
                               "submissionDigest": "hourly", "meetingAutoConfirm": False, "displayName": "Hüseyin hoca"}
    assert bad_hours["error"].startswith("Mesaj saatleri")
    good_export, bad_export = out["exportBody"]
    assert good_export["body"] == {"kind": "coach_performance", "format": "xlsx", "from": "2026-09-01", "to": "2026-10-01"}
    assert bad_export["error"] == "Bu tür için biçim seçin."
    clip, link = out["library"]
    assert clip["body"] == {"kind": "youtube_clip", "title": "Türev", "videoId": "abcdefghijk", "startSeconds": 60,
                            "endSeconds": 330, "tags": ["türev", "11. sınıf"], "gradeLevels": [11]}
    assert link["error"].startswith("Geçerli bir bağlantı")
    assert out["playlist"] == ["PLx1234567890", None]
    assert out["clock"] == ["1:15", "1:02:05", 65, 3723, None]
    assert out["week"] == ["12–18 Ekim", "26 Ekim – 1 Kasım", "2026-10-12"]
    assert out["badge"] == ["🏅 İstikrar", "🏅 Yeni"]
    assert out["labels"] == ["Kontrol bekleyen teslim", "Kapasite aşıldı", "Gelinmedi"]


def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    expected = {
        "admin_getCoachQueue": VIEW, "admin_listCoachingStudents": VIEW, "admin_getCoachingCard": VIEW,
        "admin_listCoaches": VIEW, "admin_getCoachMetrics": VIEW, "admin_listCoachRoster": VIEW,
        "admin_listCoachReviews": LEARNING, "admin_bulkApproveCoachTasks": LEARNING, "admin_saveCoachWeek": LEARNING,
        "admin_publishCoachWeek": LEARNING, "admin_bulkKudos": LEARNING,
        "admin_bulkAssignCoach": LEARNING_SETUP, "admin_createInstitutionLibraryItem": LEARNING_SETUP,
        "admin_decideLibraryProposal": LEARNING_SETUP, "admin_createCoachBadge": LEARNING_SETUP,
        "admin_exportCoaching": LEARNING_SETUP,
        "admin_putCoachProfile": SETTINGS, "admin_putCheckinQuestionSet": SETTINGS,
    }
    for op_id, permission in expected.items():
        assert op_id in ops, op_id
        assert km_permissions(ops[op_id]) == (permission,), op_id
    assert ops["admin_listCoachMeetings"].feature_flag == "coach_meetings"
    assert ops["admin_resolveVideoLink"].feature_flag == "video_tasks"
    assert ops["admin_listCheckins"].feature_flag == "self_review"
    # Yalnız KM yüzeyindeki yönetim işleri (mobil yönetici ekranında yok).
    for op_id in ("admin_putCoachProfile", "admin_createCoachBadge",
                  "admin_createInstitutionLibraryItem", "admin_exportCoaching"):
        assert ops[op_id].surface == "km", op_id


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
  return { ok: true, status: payload.method === 'GET' ? 200 : 200, data: value, headers: {} };
};
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const choose = (node, value) => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); };
"""

UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  const student = { id: 'stu-1', externalRef: 'STU-1', displayName: 'Elif Kaya', firstName: 'Elif', lastName: 'Kaya',
    classGroup: { id: 'c1', name: '9-A' }, status: 'active' };
  const coach = { id: 'k1', displayName: 'Hüseyin hoca' };
  const task = (extra = {}) => ({ id: 't1', clientUuid: null, date: '2026-10-13', startTime: null, endTime: null,
    estimatedMinutes: 40, type: 'question_solving', title: 'Türev testi', source: 'coach', locked: true, status: 'done',
    order: 0, subject: { id: 'MAT', name: 'Matematik' }, topic: null, resource: { id: 'r1', title: 'Limit Yayınları' },
    sections: [{ id: 's1', title: 'Türev 1' }], pageFrom: 12, pageTo: 20, targetQuestions: 20, homeworkId: null, examId: null,
    readingShelfItemId: null, targetPages: null, mistakeReview: null, todoId: null, note: null, assignment: null,
    completion: null, postponeRequest: null, movedFromTaskId: null, movedToTaskId: null, createdAt: '2026-10-12T08:00:00Z',
    updatedAt: null, coachTask: { instructions: '1-20', questionFrom: 1, questionTo: 20, topics: [], videos: [],
      attachments: [], priority: 'normal', published: true, review: { status: 'pending' } }, ...extra });
  routes['GET /v1/admin/coaching/coaches'] = { items: [{ coach: { ...coach, photo: null, subjects: [{ id: 'MAT', name: 'Matematik' }] },
    studentCount: 31, maxStudents: 30, pendingReviews: 4, openQuestions: 1, medianFirstResponseHours: 2.5,
    weeksPublishedPercent: 80, studentAdherencePercent: 72, meetings30d: 3 }] };
  routes['GET /v1/admin/coaching/reviews'] = { items: [{ task: task(), student, submittedAt: '2026-10-13T17:00:00Z',
    correct: 16, wrong: 3, blank: 1, accuracyPercent: 80, minutes: 45, targetMinutes: 40, difficulty: 3, needsHelp: true,
    photos: [], videos: [], note: 'Son iki soruyu anlamadım', suggestRedo: false }], nextCursor: null };
  routes['POST /v1/admin/coaching/reviews/t1/decision'] = (body) => task({ coachTask: { ...task().coachTask, review: { status: body.decision === 'approve' ? 'approved' : 'redo' } } });
  const day = (date, dow, tasks = [], extra = {}) => ({ date, dayOfWeek: dow, holiday: null, lessons: [], studyHours: [],
    tasks, plannedMinutes: tasks.reduce((t, x) => t + x.estimatedMinutes, 0), plannedQuestions: 0, capacityMinutes: 180,
    overCapacity: false, ...extra });
  const week = (version, taskDate, draftCount = 1, published = false) => ({ student, weekStart: '2026-10-12', published,
    publishedAt: null, draftCount, version, subjectMinutes: [{ subject: { id: 'MAT', name: 'Matematik' }, minutes: 40 }],
    days: [day('2026-10-12', 'mon'), day('2026-10-13', 'tue'), day('2026-10-14', 'wed'), day('2026-10-15', 'thu'),
      day('2026-10-16', 'fri'), day('2026-10-17', 'sat', [], { holiday: 'Cumartesi etkinliği' }), day('2026-10-18', 'sun')]
      .map((item) => (item.date === taskDate ? { ...item, tasks: [task({ date: taskDate, status: 'planned',
        coachTask: { ...task().coachTask, published: !draftCount, review: { status: 'none' } } })], plannedMinutes: 40 } : item)) });
  routes['GET /v1/admin/students/stu-1/coach-week'] = week(3, '2026-10-14');
  routes['PUT /v1/admin/students/stu-1/coach-week'] = (body) => ({ week: week(4, body.ops[0].date),
    results: [{ clientKey: body.ops[0].clientKey, taskId: 't1', result: 'moved', errorCode: null, warnings: ['over_capacity'] }] });
  routes['POST /v1/admin/students/stu-1/coach-week/publish'] = () => week(5, '2026-10-16', 0, true);
  routes['POST /v1/admin/coach-assignments/bulk'] = (body) => ({ applied: !body.dryRun, assignCount: 2, changeCount: 0, skipCount: 1,
    rows: [{ student, currentCoach: null, action: 'assign' }, { student: { ...student, id: 'stu-2', displayName: 'Can Ak' },
      currentCoach: coach, action: 'skip' }], capacity: { coach, studentCount: 33, maxStudents: 30, exceeded: true } });

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
    lookups: { classGroups: async () => [{ id: 'c1', name: '9-A' }], studentGroups: async () => [], terms: async () => [],
      subjects: async () => [], async options(kind, { empty } = {}) {
        const list = await this[kind]();
        const mapped = list.map((item) => ({ value: item.id, label: item.name }));
        return empty !== undefined ? [{ value: '', label: empty }, ...mapped] : mapped;
      } } };
  const { section } = await load('./panels/bbd_akademi/coaching.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(80);
  const out = {};
  out.title = root.querySelector('.ak-title')?.textContent;
  out.tabs = root.querySelectorAll('.kit-tabs [role="tab"]').map((tab) => tab.textContent.trim());
  out.reviewQuery = sent.find((c) => c.path === '/v1/admin/coaching/reviews')?.query;
  const reviewRow = () => root.querySelectorAll('.kit-row').find((row) => row.textContent.includes('Türev testi'));
  out.rowText = reviewRow()?.textContent || '';
  out.a11yQueue = audit(view.node);
  // Teslim → onayla (hızlı tepki + yorum).
  click(reviewRow());
  await sleep(60);
  let drawer = root.querySelector('.kit-drawer');
  out.drawerText = drawer?.textContent || '';
  out.a11yReview = audit(drawer);
  choose(drawer.querySelector('select'), '👏');
  type(drawer.querySelector('textarea'), 'Eline sağlık');
  click(byText(drawer, 'button', 'Onayla'));
  await sleep(60);
  out.decision = sent.filter((c) => c.path === '/v1/admin/coaching/reviews/t1/decision').map((c) => c.body);
  out.rowsAfter = reviewRow() ? 1 : 0;

  // Derin bağlantı: öğrencinin haftalık programı.
  view.shown({ coachingTab: 'week', student });
  await sleep(100);
  out.weekQuery = sent.find((c) => c.path === '/v1/admin/students/stu-1/coach-week' && c.method === 'GET')?.query;
  out.dayCount = root.querySelectorAll('.ak-coach-day').length;
  out.weekText = root.querySelector('.ak-coach-week')?.textContent || '';
  out.a11yWeek = audit(view.node);
  const card = root.querySelector('.ak-coach-task.coach');
  choose(card.querySelector('select'), '2026-10-16');
  await sleep(60);
  out.save = sent.filter((c) => c.path === '/v1/admin/students/stu-1/coach-week' && c.method === 'PUT').map((c) => c.body);
  click(byText(root, 'button', 'Yayımla'));
  await sleep(30);
  const dialog = root.querySelector('.kit-dialog');
  out.a11yPublish = audit(dialog);
  click(byText(dialog, 'button', 'Yayımla'));
  await sleep(60);
  out.publish = sent.filter((c) => c.path === '/v1/admin/students/stu-1/coach-week/publish').map((c) => c.body);
  out.publishedText = root.querySelectorAll('.kit-kpi-tile')[0]?.textContent || '';

  // Koçlar → toplu koç ata: önizleme → uygula.
  click(byText(root, '[role="tab"]', 'Koçlar'));
  await sleep(80);
  out.coachRow = root.querySelectorAll('.ak-coach-pane').map((pane) => pane.hidden ? '' : pane.textContent).join('');
  click(byText(root, 'button', 'Toplu koç ata'));
  await sleep(60);
  const assign = root.querySelectorAll('.kit-dialog').pop();
  out.a11yAssign = audit(assign);
  choose(assign.querySelector('select'), 'k1');
  click(byText(assign, 'button', '9-A'));
  click(byText(assign, 'button', 'Önizle'));
  await sleep(80);
  drawer = root.querySelectorAll('.kit-drawer').pop();
  out.previewText = drawer?.textContent || '';
  out.a11yPreview = audit(drawer);
  click(byText(drawer, 'button', 'Uygula'));
  await sleep(80);
  out.assign = sent.filter((c) => c.path === '/v1/admin/coach-assignments/bulk').map((c) => c.body);
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


def test_kontrol_haftalik_program_ve_toplu_atama() -> None:
    out = node_run(UI)
    assert out["title"] == "Koçluk"
    assert out["tabs"][:2] == ["Kuyruk", "Öğrenciler"] and "Görüşmeler" in out["tabs"]
    assert out["reviewQuery"] == {"status": "pending", "limit": 50}
    assert "Elif Kaya" in out["rowText"] and "Anlamadığım yer var" in out["rowText"]
    assert out["a11yQueue"] == [] and out["a11yReview"] == []
    assert "Son iki soruyu anlamadım" in out["drawerText"] and "16 D · 3 Y · 1 B" in out["drawerText"]
    (decision,) = out["decision"]
    assert decision["decision"] == "approve" and decision["reaction"] == "👏" and decision["comment"] == "Eline sağlık"
    assert len(decision["clientUuid"]) >= 8
    assert out["rowsAfter"] == 0                        # onaylanan teslim kuyruktan düşer
    assert out["weekQuery"] == {"week": "2026-10-12"}
    assert out["dayCount"] == 7
    assert "Koç görevi" in out["weekText"] and "Taslak" in out["weekText"] and "Cumartesi etkinliği" in out["weekText"]
    assert out["a11yWeek"] == [] and out["a11yPublish"] == []
    (save,) = out["save"]
    assert save["weekStart"] == "2026-10-12" and save["expectedVersion"] == 3
    assert save["ops"] == [{"op": "move", "clientKey": "mv-t1", "taskId": "t1", "date": "2026-10-16"}]
    assert out["publish"] == [{"weekStart": "2026-10-12", "notify": True}]
    assert "Yayımlandı" in out["publishedText"]
    assert "Hüseyin hoca" in out["coachRow"] and "31 / 30" in out["coachRow"]
    assert out["a11yAssign"] == [] and out["a11yPreview"] == []
    assert "Kapasite aşıldı" in out["previewText"] and "Koç atanacak" in out["previewText"]
    preview, applied = out["assign"]
    assert preview == {"target": {"classGroupIds": ["c1"]}, "coachId": "k1", "onExisting": "skip", "dryRun": True,
                       "startsOn": preview["startsOn"]}
    assert applied["dryRun"] is False and applied["target"] == {"classGroupIds": ["c1"]}
    assert any("Görev taşındı. Uyarı: Kapasite aşıldı." in text for text, _ in out["toasts"])
    assert any(text.startswith("2 öğrenciye koç atandı") for text, _ in out["toasts"])
