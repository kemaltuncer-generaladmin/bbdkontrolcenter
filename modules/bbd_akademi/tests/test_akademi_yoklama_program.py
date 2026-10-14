"""Yoklama yönetimi (KM-2) + program kurucu / ders oturumları (KM-3) + tarihli sınıf üyelikleri (F14).

`ui/panel/attendance*.js`, `sessions.js`, `timetable-templates.js`, `timetable-week.js` (önizleme) Node'da
küçük DOM'la (`tools/a11y/harness.py`) çalıştırılır; taklit Akademi `routes` ve `__apiHook` ile verilir.
Sınanan davranışlar:

* Pano: sayılar, alınmamış ders + "Öğretmene hatırlat" isteği, varlık ipuçları bayrağı kapalıyken açık not.
* Yoklama listesi: düzeltme penceresi kapalı (`locked`) listede kayıt gerekçeli `corrections` olur ve
  yalnız DEĞİŞEN satırı taşır; sunucuya ulaşılamazsa yoklama kuyruğa girer, `sync` ile gönderilir.
* Ders oturumu: çoklu sınıf "8-A + 8-B", gerekçeli iptal gövdesi; yoklaması alınmış derste iptal
  isteği GİTMEZ (önce yoklama silinmeli).
* Haftalık ızgara önizlemesi: PUT ile aynı gövde `preview`'a gider; öğrenci çakışması, öğretmensiz
  satır ve öğretmen yükü yazılır, hiçbir şey kaydedilmez.
* Saf işlevler: ayar gövdesi ve sınırları, aralık sınırı, senkron sonucu, şablon denetimi.
`node` yoksa atlanır.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

APP = r"""
const qs = (query) => {
  const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?${parts.join('&')}` : '';
};
function makeApp(root, api, { off = [], toasts = [], gone = [] } = {}) {
  const call = async (path, options) => {
    const reply = await api(path, options);
    if (reply && reply.__code) throw Object.assign(new Error(reply.message || reply.__code), { code: reply.__code });
    return reply;
  };
  const client = {
    can: () => true,
    featureOff: (id) => off.includes(id),
    get: (p, q) => call(`${p}${qs(q)}`),
    getCached: (p, q) => call(`${p}${qs(q)}`),
    post: (p, b) => call(p, { method: 'POST', body: b }),
    put: (p, b) => call(p, { method: 'PUT', body: b }),
    patch: (p, b) => call(p, { method: 'PATCH', body: b }),
    del: (p) => call(p, { method: 'DELETE' }),
    invalidate: () => {},
    pages(path, query = {}, { limit = 50 } = {}) {
      let done = false;
      return { get done() { return done; }, async next() {
        const data = await call(`${path}${qs({ ...query, limit })}`);
        done = true;
        return data?.items || [];
      } };
    },
  };
  return {
    root, api: client, toasts, can: (id) => client.can(id), has: () => true, enabled: () => true,
    go: (key, payload) => gone.push([key, payload]),
    toast: (text, tone) => toasts.push([text, tone]),
    lookups: {
      classGroups: async () => [{ id: 'c1', name: '8-A' }, { id: 'c2', name: '8-B' }],
      teachers: async () => [{ id: 't1', displayName: 'Hüseyin Hoca' }, { id: 't2', displayName: 'Ayşe Hoca' }],
      subjects: async () => [{ id: 'matematik', name: 'Matematik', level: 'ortaokul' }],
      terms: async () => [],
      bellSlots: async () => SLOTS,
      options: async (kind, { empty } = {}) => [{ value: '', label: empty || '' }, { value: 'c1', label: '8-A' }],
    },
  };
}
const SLOTS = { slots: ['mon', 'tue'].flatMap((day) => [1, 2].map((n) => ({ id: `${day}${n}`, dayOfWeek: day, order: n,
  startTime: `0${8 + n}:00`, endTime: `0${8 + n}:40`, kind: 'lesson' }))) };
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ');
const buttons = (scope, label) => scope.querySelectorAll('button').filter((b) => b.textContent.trim() === label);
const dialog = (root) => root.querySelectorAll('.kit-dialog').at(-1);
"""

SCENARIO = r"""
export default async function ({ document, sleep, api, load, calls, audit }) {
  const out = {};
  const hooks = {};
  globalThis.__apiHook = async (method, path, options) => {
    for (const [prefix, fn] of Object.entries(hooks)) {
      if (`${method} ${path}`.startsWith(prefix)) return fn(path, options);
    }
    return undefined;
  };
  globalThis.localStorage?.clear?.();

  // ------------------------------------------------------------- saf işlevler
  const manage = await load('./panels/bbd_akademi/attendance-manage.js');
  const sync = await load('./panels/bbd_akademi/attendance-sync.js');
  const tpl = await load('./panels/bbd_akademi/timetable-templates.js');
  const draft = manage.settingsDraft({ lateSuggestAfterMinutes: 10, absentSuggestAfterMinutes: 40,
    teacherEditWindow: { kind: 'hours', hours: 24 }, thresholds: { unexcusedSteps: [5, 10] }, version: 7 });
  out.settingsBody = manage.settingsBody({ ...draft, endOfDayDigestAt: '17:30', noShowsBeforeApproval: '' }, 7);
  const bad = (patch) => { try { manage.settingsBody({ ...draft, ...patch }, 1); return null; } catch (e) { return e.message; } };
  out.settingsErrors = [bad({ absentSuggestAfterMinutes: 5 }), bad({ editHours: 99 }), bad({ unexcusedSteps: '1,2,3,4,5,6' }),
    bad({ endOfDayDigestAt: '25:00' })];
  out.range = [manage.rangeProblem('2026-01-01', '2026-12-31'), manage.rangeProblem('2026-01-01', '2027-01-02'),
    manage.rangeProblem('2026-02-01', '2026-01-01')];
  const queue = [{ clientUuid: 'a', sessionId: 's1', label: 'Mat' }, { clientUuid: 'b', sessionId: 's2', label: 'Fen' },
    { clientUuid: 'c', sessionId: 's3', label: 'Tür' }, { clientUuid: 'd', sessionId: 's4', label: 'Sos' }];
  const applied = sync.applyResults(queue, [{ clientUuid: 'a', result: 'applied' }, { clientUuid: 'b', result: 'queued_for_review' },
    { clientUuid: 'c', result: 'conflict' }]);
  out.syncSummary = { ...applied.summary, rest: applied.rest.map((item) => item.clientUuid) };
  out.tplProblems = [tpl.templateProblem([]), tpl.templateProblem([{ dayOfWeek: 'mon', bellSlotId: 'mon1', subjectId: 'm' },
    { dayOfWeek: 'mon', bellSlotId: 'mon1', subjectId: 'f' }]), tpl.templateProblem([{ dayOfWeek: 'mon', bellSlotId: 'mon1', subjectId: 'm' }])];
  out.tplFromTimetable = tpl.entriesFromTimetable({ entries: [
    { dayOfWeek: 'mon', bellSlotId: 'mon1', subject: { id: 'matematik' }, teacher: { id: 't1' }, room: '204', status: 'active' },
    { dayOfWeek: 'tue', bellSlotId: 'x', subject: { id: 'fen' }, status: 'orphaned' }] });

  // --------------------------------------------------------------------- pano
  {
    const root = document.createElement('div');
    document.body.append(root);
    const toasts = [];
    const app = makeApp(root, api, { off: ['admin_listAttendanceInconsistencies'], toasts });
    const tab = manage.boardTab(app, {});
    root.append(tab.node);
    tab.reload();
    await sleep(40);
    out.boardText = text(root);
    out.a11y = { board: audit(root) };
    buttons(root, 'Öğretmene hatırlat')[0].click();
    await sleep(20);
    out.remindCalls = calls.filter((c) => c.path.endsWith('/remind')).map((c) => [c.method, c.path]);
    out.boardToasts = toasts;
    root.remove();
  }

  // ---------------------------------------- yoklama listesi: kilitli → düzeltme
  {
    const attendance = await load('./panels/bbd_akademi/attendance.js');
    const root = document.createElement('div');
    document.body.append(root);
    const toasts = [];
    const app = makeApp(root, api, { toasts });
    const view = attendance.section(app);
    root.append(view.node);
    view.shown({ attendanceSessionId: SID, date: '2026-10-13' });
    await sleep(60);
    out.sheetText = text(root.querySelector('.ak-att-sheet'));
    out.a11y.day = audit(root);
    const rows = root.querySelectorAll('.ak-att-row');
    buttons(rows[1], 'Gelmedi')[0].click();
    await sleep(10);
    buttons(root, 'Düzeltmeyi kaydet')[0].click();
    await sleep(20);
    buttons(dialog(root), 'Kaydet')[0].click();          // "gelmedi" onayı
    await sleep(20);
    const reasonBox = dialog(root);
    const input = reasonBox.querySelector('input');
    input.value = 'Veli izin belgesi getirdi';
    input.dispatchEvent(new Event('input'));
    buttons(reasonBox, 'Düzeltmeyi kaydet')[0].click();
    await sleep(40);
    out.correction = calls.filter((c) => c.path.endsWith('/corrections')).map((c) => c.body);
    out.sheetPuts = calls.filter((c) => c.method === 'PUT' && c.path.includes('/attendance/sessions/')).length;

    // Sunucuya ulaşılamadı: kilitsiz listede kayıt kuyruğa girer, "Şimdi gönder" sync'e gider.
    hooks[`PUT /v1/admin/attendance/sessions/${SID2}`] = () => ({ __code: 'km_unavailable', message: 'ağ yok' });
    view.shown({ attendanceSessionId: SID2, date: '2026-10-13' });
    await sleep(60);
    buttons(root.querySelectorAll('.ak-att-row')[0], 'Geç')[0].click();
    await sleep(10);
    buttons(root, 'Düzeltmeyi kaydet')[0].click();
    await sleep(40);
    out.offlineBanner = text(root.querySelector('.ak-att-offline'));
    out.queueSize = sync.readQueue().length;
    hooks['POST /v1/admin/attendance/sync'] = (path, options) => ({ results: options.body.items.map((item) => ({
      clientUuid: item.clientUuid, sessionId: item.sessionId, result: 'applied', version: 2 })) });
    buttons(root, 'Şimdi gönder')[0].click();
    await sleep(60);
    const syncCall = calls.filter((c) => c.path === '/v1/admin/attendance/sync').at(-1);
    out.syncBody = syncCall?.body;
    out.queueAfter = sync.readQueue().length;
    out.attToasts = toasts;
    view.destroy();
    root.remove();
  }

  // ------------------------------------------------------------ ders oturumu
  {
    const sessions = await load('./panels/bbd_akademi/sessions.js');
    const root = document.createElement('div');
    document.body.append(root);
    const toasts = [];
    const gone = [];
    const app = makeApp(root, api, { toasts, gone });
    sessions.openClassSession(app, 's1', {});
    await sleep(40);
    out.drawer = text(root.querySelector('.kit-drawer'));
    out.a11y.session = audit(root);
    buttons(root, 'Dersi iptal et')[0].click();
    await sleep(20);
    const box = dialog(root);
    const area = box.querySelector('textarea');
    area.value = 'Öğretmen hasta';
    area.dispatchEvent(new Event('input'));
    buttons(box, 'Dersi iptal et')[0].click();
    await sleep(40);
    out.cancel = calls.filter((c) => c.path === '/v1/admin/sessions/s1/cancel').map((c) => c.body);
    out.sessionToasts = toasts.slice();
    for (const node of root.querySelectorAll('.kit-drawer')) node.remove();

    sessions.openClassSession(app, 's2', {});
    await sleep(40);
    buttons(root, 'Dersi iptal et').at(-1).click();
    await sleep(20);
    out.takenDialog = text(dialog(root));
    out.cancelS2 = calls.filter((c) => c.path === '/v1/admin/sessions/s2/cancel').length;
    root.remove();
  }

  // --------------------------------------------------- veli adına izin (kart)
  {
    const { leaveOnBehalf } = await load('./panels/bbd_akademi/students-attendance.js');
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api, {});
    const pending = leaveOnBehalf(app, 'u1', [{ id: 'g1', displayName: 'Ayşe Kaya', relationLabel: 'Anne' }]);
    await sleep(20);
    out.leaveDialog = text(dialog(root));
    buttons(dialog(root), 'İzni kaydet')[0].click();
    out.leaveSaved = await pending;
    out.leave = calls.filter((c) => c.path === '/v1/admin/students/u1/leave-requests').map((c) => c.body);
    out.noGuardian = await leaveOnBehalf(app, 'u1', []);
    root.remove();
  }

  // ------------------------------------------------- haftalık ızgara önizlemesi
  {
    const week = await load('./panels/bbd_akademi/timetable-week.js');
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api, {});
    const tab = week.weekTab(app, {});
    root.append(tab.node);
    await tab.reload();
    await sleep(40);
    buttons(root, 'Çakışmaları denetle')[0].click();
    await sleep(40);
    out.preview = calls.filter((c) => c.path.endsWith('/timetable/preview')).map((c) => c.body);
    out.previewText = text(root.querySelector('.ak-tt-preview'));
    out.a11y.preview = audit(root.querySelector('.ak-tt-alerts'));
    out.previewPuts = calls.filter((c) => c.method === 'PUT' && c.path.endsWith('/timetable')).length;
    root.remove();
  }
  return out;
}
"""

SID = "tt:e1:2026-10-13"
SID2 = "tt:e2:2026-10-13"
STUDENTS = [
    {"id": "u1", "displayName": "Ali Kaya", "externalRef": "A1", "status": "active", "classGroup": {"id": "c1", "name": "8-A"}},
    {"id": "u2", "displayName": "Can Er", "externalRef": "A2", "status": "active", "classGroup": {"id": "c1", "name": "8-A"}},
]


def _item(sid: str, taken: bool) -> dict[str, Any]:
    return {"id": sid, "taken": taken, "takenAt": "2026-10-13T09:05:00+03:00" if taken else None,
            "takenBy": {"id": "t1", "displayName": "Hüseyin Hoca"} if taken else None,
            "counts": {"enrolled": 2, "present": 2, "absent": 0, "late": 0, "excused": 0},
            "scheduleItem": {"id": sid, "kind": "lesson", "date": "2026-10-13", "status": "scheduled",
                             "startsAt": "2026-10-13T09:00:00+03:00", "endsAt": "2026-10-13T09:40:00+03:00",
                             "subject": {"id": "matematik", "name": "Matematik"},
                             "teacher": {"id": "t1", "displayName": "Hüseyin Hoca"},
                             "classGroup": {"id": "c1", "name": "8-A"},
                             "classGroups": [{"id": "c1", "name": "8-A"}, {"id": "c2", "name": "8-B"}]}}


def _sheet(sid: str, *, locked: bool) -> dict[str, Any]:
    return {"session": _item(sid, True), "version": 4, "locked": locked,
            "editableUntil": "2026-10-13T23:59:00+03:00",
            "rows": [{"student": student, "status": "present", "autoExcused": False} for student in STUDENTS]}


def _session(sid: str, *, taken: bool) -> dict[str, Any]:
    return {"id": sid, "occurrenceKey": f"tt:e1:{sid}", "kind": "lesson", "date": "2026-10-20",
            "startsAt": "2026-10-20T09:00:00+03:00", "endsAt": "2026-10-20T09:40:00+03:00", "bellSlotId": "mon1",
            "subject": {"id": "matematik", "name": "Matematik"}, "topic": None,
            "group": {"id": "c1", "name": "8-A", "kind": "km_class"},
            "groups": [{"id": "c1", "name": "8-A", "kind": "km_class"}, {"id": "c2", "name": "8-B", "kind": "km_class"}],
            "studentCount": 30, "scheduledTeacher": {"id": "t1", "displayName": "Hüseyin Hoca"},
            "effectiveTeacher": {"id": "t2", "displayName": "Ayşe Hoca"}, "isSubstitute": True, "room": "204",
            "status": "scheduled", "statusReason": None, "makeupOfSessionId": None, "makeupSessionId": None,
            "announce": True, "version": 3, "updatedAt": None,
            "attendance": {"taken": taken, "takenAt": None, "takenBy": None, "editableUntil": None, "locked": False,
                           "counts": {"enrolled": 30, "present": 0, "absent": 0, "late": 0, "excused": 0, "unmarked": 30}}}


BOARD = {"date": "2026-10-13", "expectedStudents": 120, "presentStudents": 101, "absentStudents": 7, "lateStudents": 4,
         "excusedStudents": 3, "sessionsTotal": 12, "sessionsTaken": 10, "sessionsPending": 1,
         "pending": [_item(SID, False)], "computedAt": "2026-10-13T10:00:00+03:00"}
TIMETABLE = {"classGroup": {"id": "c1", "name": "8-A"}, "validFrom": "2026-09-01", "validTo": None, "version": 5,
             "entries": [{"id": "e1", "dayOfWeek": "mon", "bellSlotId": "mon1", "subject": {"id": "matematik",
                          "name": "Matematik"}, "teacher": None, "room": None, "status": "active",
                          "classGroups": [{"id": "c1", "name": "8-A"}], "program": None}],
             "sharedEntries": []}
PREVIEW = {"conflicts": [], "unassignedTeacherEntryIndexes": [0],
           "studentConflicts": [{"student": {"displayName": "Ali Kaya"}, "dayOfWeek": "mon", "bellSlotId": "mon1",
                                 "entryIndex": 0, "conflictingGroup": {"id": "g9", "name": "TYT Hüseyin", "kind": "group"},
                                 "conflictingEntryId": None}],
           "teacherMinutes": [{"teacher": {"id": "t1", "displayName": "Hüseyin Hoca"}, "minutes": 80}]}


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    cancelled = {**_session("s1", taken=False), "status": "cancelled", "statusReason": "Öğretmen hasta"}
    routes = {
        "GET /v1/admin/attendance/board*": BOARD,
        "GET /v1/admin/attendance/early-leaves*": {"items": []},
        f"POST /v1/admin/attendance/sessions/{SID}/remind": {"notifiedCount": 1},
        "GET /v1/admin/attendance/sessions?*": {"items": [_item(SID, True), _item(SID2, True)]},
        f"GET /v1/admin/attendance/sessions/{SID}": _sheet(SID, locked=True),
        f"GET /v1/admin/attendance/sessions/{SID2}": _sheet(SID2, locked=False),
        f"POST /v1/admin/attendance/sessions/{SID}/corrections": _sheet(SID, locked=True),
        "GET /v1/admin/sessions/s1": _session("s1", taken=False),
        "GET /v1/admin/sessions/s2": _session("s2", taken=True),
        "POST /v1/admin/sessions/s1/cancel": cancelled,
        "GET /v1/admin/class-groups/c1/timetable*": TIMETABLE,
        "POST /v1/admin/class-groups/c1/timetable/preview": PREVIEW,
        "GET /v1/admin/curricula/programs*": {"items": []},
        "POST /v1/admin/students/u1/leave-requests": {"id": "l1", "status": "approved"},
    }
    scenario = (f"const SID = {json.dumps(SID)};\nconst SID2 = {json.dumps(SID2)};\n" + APP + SCENARIO)
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_ayar_govdesi_ve_sinirlar(result: dict[str, Any]) -> None:
    body = result["settingsBody"]
    assert body["teacherEditWindow"] == {"kind": "hours", "hours": 24}
    assert body["thresholds"]["unexcusedSteps"] == [5, 10]
    assert body["reminders"]["endOfDayDigestAt"] == "17:30"
    assert body["etut"] == {"noShowsBeforeApproval": None}
    assert body["leave"]["enabledKinds"] == ["full_day", "partial", "early_leave", "retro_excuse"]
    assert body["expectedVersion"] == 7
    errors = result["settingsErrors"]
    assert "“Gelmedi” önerisi" in errors[0]
    assert "1–72" in errors[1]
    assert "en çok 5" in errors[2]
    assert "17:30" in errors[3]


def test_aralik_ve_senkron_ve_sablon(result: dict[str, Any]) -> None:
    assert result["range"][0] is None
    assert "1 yıl" in result["range"][1] and "önce" in result["range"][2]
    summary = result["syncSummary"]
    assert (summary["applied"], summary["review"], summary["conflict"]) == (1, 1, 1)
    assert summary["rest"] == ["d"]                     # yanıtta olmayan kuyrukta kalır
    problems = result["tplProblems"]
    assert "En az bir" in problems[0] and "aynı gün" in problems[1] and problems[2] is None
    assert result["tplFromTimetable"] == [{"dayOfWeek": "mon", "bellSlotId": "mon1", "subjectId": "matematik",
                                           "teacherId": "t1", "room": "204"}]


def test_pano_hatirlat_ve_kapali_ipucu(result: dict[str, Any]) -> None:
    text = result["boardText"]
    assert "Beklenen öğrenci" in text and "120" in text and "10 / 12" in text
    assert "Matematik · 8-A + 8-B" in text
    assert "kurumda kapalı" in text and "attendance_signals" in text
    assert result["remindCalls"] == [["POST", f"/v1/admin/attendance/sessions/{SID}/remind"]]
    assert result["boardToasts"][-1] == ["Öğretmene hatırlatma gönderildi.", "good"]


def test_kilitli_listede_gerekceli_duzeltme(result: dict[str, Any]) -> None:
    assert "düzeltme penceresi kapandı" in result["sheetText"]
    assert "Geçmiş" in result["sheetText"] and "Yoklamayı sil" in result["sheetText"]
    assert result["correction"] == [{"reason": "Veli izin belgesi getirdi", "expectedVersion": 4,
                                     "rows": [{"studentId": "u2", "status": "absent", "note": None}]}]


def test_ulasilamazsa_kuyruk_ve_senkron(result: dict[str, Any]) -> None:
    assert "1 yoklama bu bilgisayarda gönderilmeyi bekliyor" in result["offlineBanner"]
    assert result["queueSize"] == 1
    item = result["syncBody"]["items"][0]
    assert item["sessionId"] == SID2 and item["baseVersion"] == 4 and item["markAllPresent"] is False
    assert {row["studentId"]: row["status"] for row in item["rows"]} == {"u1": "late", "u2": "present"}
    assert item["markedAt"] and item["clientUuid"]
    assert result["queueAfter"] == 0
    assert ["1 bekleyen yoklama gönderildi.", "good"] in result["attToasts"]


def test_oturum_cekmecesi_ve_iptal(result: dict[str, Any]) -> None:
    drawer = result["drawer"]
    assert "Matematik · 8-A + 8-B" in drawer
    assert "Ayşe Hoca (yerine)" in drawer
    assert result["cancel"] == [{"reason": "Öğretmen hasta", "notifyStudents": True}]
    assert ["Ders iptal edildi.", "good"] in result["sessionToasts"]
    # Yoklaması alınmış ders: iptal isteği gitmez, önce yoklamayı silmek söylenir.
    assert "Yoklaması alınmış ders iptal edilemez" in result["takenDialog"]
    assert result["cancelS2"] == 0


def test_izgara_onizlemesi_kaydetmez(result: dict[str, Any]) -> None:
    assert len(result["preview"]) == 1
    body = result["preview"][0]
    assert body["expectedVersion"] == 5 and body["entries"][0]["subjectId"] == "matematik"
    text = result["previewText"]
    assert "1 öğrenci çakışması" in text and "Ali Kaya" in text and "TYT Hüseyin" in text
    assert "öğretmen atanmadı" in text
    assert "Hüseyin Hoca 1,3 sa" in text
    assert result["previewPuts"] == 0


def test_erisilebilirlik(result: dict[str, Any]) -> None:
    assert result["a11y"] == {"board": [], "day": [], "session": [], "preview": []}


def test_veli_adina_izin(result: dict[str, Any]) -> None:
    assert "Ayşe Kaya · Anne" in result["leaveDialog"]
    assert len(result["leave"]) == 1
    body = result["leave"][0]
    assert body["kind"] == "full_day" and body["reason"] == "illness" and body["guardianId"] == "g1"
    assert body["from"] == body["to"] and body["approve"] is True and body["channel"] == "phone"
    assert body["leaveAt"] is None and body["startTime"] is None
    assert result["leaveSaved"]["status"] == "approved"
    assert result["noGuardian"] is None                 # velisiz öğrencide pencere açılmaz
