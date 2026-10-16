"""Bugün → Özet / Talepler, veli adına mazeret, plan uyum tablosu (2. denetim V3, V4, V9, V15).

Panel dosyaları Node'da küçük DOM'la (`tools/a11y/harness.py`) çalıştırılır; taklit Akademi `routes` ve
`__apiHook` ile verilir. Sınanan davranışlar:

* V3 Özet: erken uyarı (açık + yüksek) ve koçluk (soru · kontrol) kutuları özet yükünden; "Sizi bekleyenler"de
  cevapsız hoca sorusu, kontrol bekleyen koç görevi, açık mesaj şikâyeti (kuyruğun ilk sayfası, "100+"),
  yüksek önemli uyarı satırları ve "Aç" yönlendirmeleri. Bölüm kapalıysa (`null`) / yetki yoksa satır ve
  şikâyet isteği YOK.
* V4 Talepler → İzin: tür rozeti + ayrıntı (saat aralığı, erken çıkış, mazeret ders sayısı), mazerette
  "Dersleri göster" günün yoklamasından "09:20 Matematik"; onay metni türe göre (saat aralığında "yalnız bu
  saatlerle kesişen", mazerette seçilen "Gelmedi" dersleri, erken çıkışta "hemen değiştirmez").
* V15 Veli adına mazeret: tür "Mazeret" seçilince günün "Gelmedi" dersleri işaretli gelir, seçilenler
  `attendanceSessionIds` olarak gider, `to` = gün; pencere kapalıysa pencere ipucu ve istek GİTMEZ.
* V9 Uyum tablosu: içinde bulunulan hafta "Bu hafta" başlıklı, nötr tonlu; geçmiş hafta ısı rengiyle.
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

APP = r"""
const qs = (query) => {
  const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?${parts.join('&')}` : '';
};
function makeApp(root, api, { off = [], perms = null, features = null, gone = [], toasts = [] } = {}) {
  const call = (path, options) => api(path, options);
  const client = {
    can: (id) => !off.includes(id),
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
    root, api: client, toasts, can: (id) => client.can(id),
    has: (key) => perms === null || perms.includes(key),
    enabled: (flag) => features === null || features[flag] !== false,
    go: (key, payload) => gone.push([key, payload ?? null]),
    toast: (text, tone) => toasts.push([text, tone]),
    lookups: {
      classGroups: async () => [{ id: 'c1', name: '8-A' }],
      studentGroups: async () => [],
      subjects: async () => [{ id: 'matematik', name: 'Matematik' }],
      options: async (kind, { empty } = {}) => [{ value: '', label: empty || '' }],
    },
  };
}
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const buttons = (scope, label) => scope.querySelectorAll('button').filter((b) => b.textContent.trim() === label);
const dialog = (root) => root.querySelectorAll('.kit-dialog').at(-1);
"""

SCENARIO = r"""
export default async function ({ document, sleep, api, load, calls }) {
  const out = {};
  const hooks = {};
  globalThis.__apiHook = async (method, path, options) => {
    for (const [prefix, fn] of Object.entries(hooks)) {
      if (`${method} ${path}`.startsWith(prefix)) return fn(path, options);
    }
    return undefined;
  };
  const common = await load('./panels/bbd_akademi/common.js');

  // ----------------------------------------------------------------- V3 Özet
  {
    const dash = await load('./panels/bbd_akademi/today/dashboard.js');
    out.breakdown = [dash.warningBreakdown({ open: 5, high: 2, medium: 3, low: 0 }), dash.warningBreakdown(null)];
    const root = document.createElement('div');
    document.body.append(root);
    const gone = [];
    const app = makeApp(root, api, { gone });
    const view = dash.section(app);
    root.append(view.node);
    view.shown();
    await sleep(60);
    out.dashText = text(root);
    out.reportCalls = calls.filter((c) => c.path.startsWith('/v1/admin/messages/reports')).map((c) => c.path);
    const waiting = root.querySelectorAll('.ak-dash-wait');
    out.waitRows = waiting.map((row) => text(row));
    for (const row of waiting) {
      const label = text(row.querySelector('span'));
      const open = row.querySelector('button');
      if (open && ['Cevapsız hoca sorusu', 'Kontrol bekleyen koç görevi', 'Açık mesaj şikâyeti',
        'Yüksek önemli erken uyarı'].includes(label)) open.click();
    }
    out.gone = gone;
    view.destroy();
    root.remove();

    // Kapalı bölümler: earlyWarnings / coaching null, şikâyet kuyruğu yetkisi yok.
    hooks['GET /v1/admin/dashboard'] = () => ({ ...DASH, earlyWarnings: null, coaching: null });
    const before = calls.length;
    const root2 = document.createElement('div');
    document.body.append(root2);
    const app2 = makeApp(root2, api, { off: ['admin_listMessageReports'] });
    const view2 = dash.section(app2);
    root2.append(view2.node);
    view2.shown();
    await sleep(60);
    out.dashOffText = text(root2);
    out.dashOffReportCalls = calls.slice(before).filter((c) => c.path.startsWith('/v1/admin/messages/reports')).length;
    view2.destroy();
    root2.remove();
    delete hooks['GET /v1/admin/dashboard'];
  }

  // ------------------------------------------------------ V4 Talepler → İzin
  {
    const req = await load('./panels/bbd_akademi/today/requests.js');
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api);
    const view = req.section(app);
    root.append(view.node);
    view.shown();
    await sleep(60);
    const rows = root.querySelectorAll('.ak-req-kind');
    out.kindCells = rows.map((cell) => text(cell));
    const before = calls.length;
    buttons(rows[2], 'Dersleri göster')[0].click();
    await sleep(40);
    out.lessonText = text(rows[2].querySelector('.ak-req-lesson-list'));
    out.dayCalls = calls.slice(before).map((c) => c.path);

    const approveTexts = {};
    const approveButtons = buttons(root, 'Onayla');
    const ids = ['partial', 'early', 'retro', 'full'];
    for (let index = 0; index < approveButtons.length; index += 1) {
      approveButtons[index].click();
      await sleep(40);
      const box = dialog(root) || dialog(document);
      approveTexts[ids[index]] = text(box?.querySelector('.kit-dialog-text'));
      approveTexts[`${ids[index]}Title`] = text(box?.querySelector('.kit-dialog-title'));
      buttons(box, 'Vazgeç')[0]?.click();
      await sleep(10);
    }
    out.approveTexts = approveTexts;
    // Mazeretin günü önbellekte: onay penceresi aynı günü tekrar istemez.
    out.retroDayCalls = calls.filter((c) => c.path === '/v1/students/u3/attendance/days/2026-10-13').length;
    view.destroy();
    root.remove();
  }

  // -------------------------------------------- V15 veli adına mazeret (kart)
  {
    const { leaveOnBehalf, retroProblem } = await load('./panels/bbd_akademi/students/students-attendance.js');
    out.problems = [retroProblem(null, []), retroProblem({ retroExcuseOpen: false, retroExcuseMaxDays: 5, sessions: [] }, []),
      retroProblem({ retroExcuseOpen: true, sessions: [] }, []),
      retroProblem({ retroExcuseOpen: true, sessions: [{ status: 'absent', occurrenceKey: 'k' }] }, [])];
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api);
    const pending = leaveOnBehalf(app, 'u1', [{ id: 'g1', displayName: 'Ayşe Kaya', relationLabel: 'Anne' }]);
    await sleep(20);
    let box = dialog(root);
    out.retroHiddenAtStart = box.querySelector('.ak-retro').hidden;
    const kind = box.querySelectorAll('select').find((node) => node.querySelectorAll('option')
      .some((option) => option.value === 'retro_excuse'));
    kind.value = 'retro_excuse';
    kind.dispatchEvent(new Event('change'));
    await sleep(40);
    out.retroText = text(box.querySelector('.ak-retro'));
    out.retroChecks = box.querySelectorAll('.ak-retro input').map((node) => [node.checked, node.disabled]);
    buttons(box, 'İzni kaydet')[0].click();
    out.retroSaved = await pending;
    out.retroBody = calls.filter((c) => c.path === '/v1/admin/students/u1/leave-requests').map((c) => c.body).at(-1);

    // Pencere kapalı: istek gitmez, ipucu görünür.
    hooks['GET /v1/students/u1/attendance/days/'] = () => ({ ...DAY_U1, retroExcuseOpen: false, retroExcuseMaxDays: 3 });
    const before = calls.filter((c) => c.path === '/v1/admin/students/u1/leave-requests').length;
    const pending2 = leaveOnBehalf(app, 'u1', [{ id: 'g1', displayName: 'Ayşe Kaya' }]);
    await sleep(20);
    box = dialog(root);
    const kind2 = box.querySelectorAll('select').find((node) => node.querySelectorAll('option')
      .some((option) => option.value === 'retro_excuse'));
    kind2.value = 'retro_excuse';
    kind2.dispatchEvent(new Event('change'));
    await sleep(40);
    buttons(box, 'İzni kaydet')[0].click();
    await sleep(20);
    out.closedText = text(box);
    out.closedPosts = calls.filter((c) => c.path === '/v1/admin/students/u1/leave-requests').length - before;
    buttons(box, 'Vazgeç')[0].click();
    out.closedResult = await pending2;
    delete hooks['GET /v1/students/u1/attendance/days/'];
    root.remove();
  }

  // ------------------------------------------------------- V9 plan uyum tablosu
  {
    const monday = common.weekStart(common.todayIst());
    const previous = common.addDays(monday, -7);
    hooks['GET /v1/admin/plan/adherence'] = () => ({ computedAt: '2026-10-15T08:00:00+03:00', weeks: [previous, monday],
      nextCursor: null, items: [{ student: { id: 'u1', displayName: 'Ali Kaya' }, averagePercent: 40,
        cells: [{ weekStart: previous, adherencePercent: 40, plannedMinutes: 300, doneMinutes: 120 },
          { weekStart: monday, adherencePercent: 10, plannedMinutes: 300, doneMinutes: 30 }] }] });
    const plan = await load('./panels/bbd_akademi/work/plan.js');
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api);
    const view = plan.section(app);
    root.append(view.node);
    view.shown({ tab: 'adherence' });
    await sleep(80);
    const cells = root.querySelectorAll('.kit-table-row .ak-plan-heat, [role="row"] .ak-plan-heat')
      .filter((node) => !node.closest('.ak-plan-legend'));
    out.heat = cells.map((node) => [text(node), node.className, node.title || '']);
    out.planText = text(root);
    view.destroy();
    root.remove();
    delete hooks['GET /v1/admin/plan/adherence'];
  }
  return out;
}
"""

DASH: dict[str, Any] = {
    "date": "2026-10-15",
    "lessons": {"scheduled": 4, "ongoing": 1, "items": []},
    "attendance": {"sessionsTotal": 4, "sessionsTaken": 3, "absentCount": 2, "pendingSessionIds": ["s1"]},
    "presence": {"inInstitution": 80, "activeStudents": 120},
    "tablets": None,
    "pendingBookRequests": None,
    "institutionFinance": None,
    "canteen": None,
    "pendingRequests": {"leave": 2, "meeting": 0, "etut": 1},
    "earlyWarnings": {"open": 5, "high": 2, "medium": 3, "low": 0},
    "coaching": {"openQuestions": 3, "pendingReviews": 4},
}


def _leave(lid: str, kind: str, **extra: Any) -> dict[str, Any]:
    base = {"id": lid, "student": {"id": extra.pop("sid", "u2"), "displayName": "Elif Yılmaz",
                                   "classGroup": {"id": "c1", "name": "8-A"}},
            "from": "2026-10-13", "to": "2026-10-13", "reason": "illness", "description": None,
            "status": "requested", "createdAt": "2026-10-13T08:00:00+03:00",
            "createdBy": {"id": "p1", "displayName": "Ayşe Yılmaz"}, "kind": kind, "channel": "app",
            "affectedSessionIds": []}
    base.update(extra)
    return base


LEAVES = [
    _leave("l1", "partial", startTime="10:00", endTime="12:00", affectedSessionIds=["tt:a:1", "tt:b:1"]),
    _leave("l2", "early_leave", leaveAt="2026-10-13T14:30:00+03:00", pickupPersonName="Ayşe Kaya",
           channel="phone"),
    _leave("l3", "retro_excuse", sid="u3", affectedSessionIds=["tt:m:1"]),
    _leave("l4", "full_day", **{"from": "2026-10-20", "to": "2026-10-21"}),
]

DAY_U3 = {"date": "2026-10-13", "status": "partial", "retroExcuseOpen": True, "retroExcuseMaxDays": 7,
          "sessions": [
              {"sessionId": "x1", "occurrenceKey": "tt:m:1", "startsAt": "2026-10-13T09:20:00+03:00",
               "endsAt": "2026-10-13T10:00:00+03:00", "subject": {"id": "matematik", "name": "Matematik"},
               "teacher": None, "sessionStatus": "scheduled", "status": "absent", "lateMinutes": None,
               "leftEarlyAt": None, "excuseSource": None, "leaveRequestId": None, "pendingLeaveRequestId": "l3"},
              {"sessionId": "x2", "occurrenceKey": "tt:f:1", "startsAt": "2026-10-13T10:10:00+03:00",
               "endsAt": "2026-10-13T10:50:00+03:00", "subject": {"id": "fen", "name": "Fen"},
               "teacher": None, "sessionStatus": "scheduled", "status": "present", "lateMinutes": None,
               "leftEarlyAt": None, "excuseSource": None, "leaveRequestId": None, "pendingLeaveRequestId": None}]}

DAY_U1 = {"date": "2026-10-15", "status": "partial", "retroExcuseOpen": True, "retroExcuseMaxDays": 7,
          "sessions": [
              {"sessionId": "y1", "occurrenceKey": "tt:m:2", "startsAt": "2026-10-15T09:20:00+03:00",
               "endsAt": "2026-10-15T10:00:00+03:00", "subject": {"id": "matematik", "name": "Matematik"},
               "teacher": {"id": "t1", "displayName": "Hüseyin Hoca"}, "sessionStatus": "scheduled",
               "status": "absent", "lateMinutes": None, "leftEarlyAt": None, "excuseSource": None,
               "leaveRequestId": None, "pendingLeaveRequestId": None},
              {"sessionId": "y2", "occurrenceKey": "tt:t:2", "startsAt": "2026-10-15T10:10:00+03:00",
               "endsAt": "2026-10-15T10:50:00+03:00", "subject": {"id": "turkce", "name": "Türkçe"},
               "teacher": None, "sessionStatus": "scheduled", "status": "absent", "lateMinutes": None,
               "leftEarlyAt": None, "excuseSource": None, "leaveRequestId": None, "pendingLeaveRequestId": "lx"},
              {"sessionId": "y3", "occurrenceKey": "tt:f:2", "startsAt": "2026-10-15T11:00:00+03:00",
               "endsAt": "2026-10-15T11:40:00+03:00", "subject": {"id": "fen", "name": "Fen"},
               "teacher": None, "sessionStatus": "scheduled", "status": "present", "lateMinutes": None,
               "leftEarlyAt": None, "excuseSource": None, "leaveRequestId": None, "pendingLeaveRequestId": None}]}


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    import json

    routes = {
        "GET /v1/admin/dashboard": DASH,
        "GET /v1/admin/messages/reports*": {"items": [{"id": "r1"}, {"id": "r2"}], "nextCursor": "c2"},
        "GET /v1/admin/leave-requests*": {"items": LEAVES, "nextCursor": None},
        "GET /v1/students/u3/attendance/days/2026-10-13": DAY_U3,
        "GET /v1/students/u1/attendance/days/*": DAY_U1,
        "POST /v1/admin/students/u1/leave-requests": {"id": "l9", "status": "approved"},
    }
    scenario = (f"const DASH = {json.dumps(DASH)};\nconst DAY_U1 = {json.dumps(DAY_U1)};\n" + APP + SCENARIO)
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_ozet_erken_uyari_ve_kocluk(result: dict[str, Any]) -> None:
    text = result["dashText"]
    assert "Erken uyarı" in text and "5 (2 yüksek)" in text
    assert "Koçluk" in text and "3 soru · 4 kontrol" in text
    assert result["breakdown"] == ["2 yüksek · 3 orta", ""]


def test_ozet_sizi_bekleyenler(result: dict[str, Any]) -> None:
    rows = result["waitRows"]
    assert any(row.startswith("Cevapsız hoca sorusu3") for row in rows), rows
    assert any(row.startswith("Kontrol bekleyen koç görevi4") for row in rows), rows
    assert any(row.startswith("Açık mesaj şikâyeti2+") for row in rows), rows
    assert any(row.startswith("Yüksek önemli erken uyarı2") for row in rows), rows
    assert result["reportCalls"] == ["/v1/admin/messages/reports?status=open&limit=100"]
    assert result["gone"] == [
        ["questions", None],
        ["coaching", {"coachingTab": "queue"}],
        ["messages", {"messagesTab": "reports"}],
        ["insights", {"tab": "warnings"}],
    ]


def test_ozet_kapali_bolumler_cizilmez(result: dict[str, Any]) -> None:
    text = result["dashOffText"]
    assert "Erken uyarı" not in text and "Koçluk" not in text
    assert "hoca sorusu" not in text and "mesaj şikâyeti" not in text
    assert result["dashOffReportCalls"] == 0


def test_talepler_izin_turu(result: dict[str, Any]) -> None:
    cells = result["kindCells"]
    assert cells[0].startswith("Saat aralığı") and "10:00–12:00 · 2 ders" in cells[0]
    assert cells[1].startswith("Erken çıkış") and "14:30 · Teslim: Ayşe Kaya" in cells[1]
    assert cells[2].startswith("Mazeret") and "1 ders" in cells[2] and "Dersleri göster" in cells[2]
    assert cells[3].startswith("Tam gün")
    assert result["lessonText"] == "09:20 Matematik"
    assert result["dayCalls"] == ["/v1/students/u3/attendance/days/2026-10-13"]


def test_onay_metni_ture_gore(result: dict[str, Any]) -> None:
    texts = result["approveTexts"]
    assert "Yalnız bu saatlerle kesişen 2 dersin" in texts["partial"]
    assert "günün diğer dersleri değişmez" in texts["partial"]
    assert "hemen değiştirmez" in texts["early"] and "Ayşe Kaya" in texts["early"]
    assert '1 "Gelmedi" dersi: 09:20 Matematik' in texts["retro"]
    assert "Fen" not in texts["retro"]
    assert texts["retroTitle"].endswith("mazereti onayla")
    assert "20.10.2026 – 21.10.2026" in texts["full"] and "bütün derslerinin" in texts["full"]
    assert result["retroDayCalls"] == 1


def test_veli_adina_mazeret(result: dict[str, Any]) -> None:
    assert result["retroHiddenAtStart"] is True
    text = result["retroText"]
    assert "Matematik" in text and "Türkçe" in text and "Mazeret bekliyor" in text
    assert "Fen" not in text                                  # "Geldi" dersi listelenmez
    assert "en çok 7 gün geriye" in text
    assert result["retroChecks"] == [[True, False], [False, True]]
    body = result["retroBody"]
    assert body["kind"] == "retro_excuse" and body["attendanceSessionIds"] == ["tt:m:2"]
    assert body["from"] == body["to"]
    assert result["retroSaved"]["status"] == "approved"
    assert result["problems"][0].startswith("Günün yoklaması")
    assert "en çok 5 gün" in result["problems"][1]
    assert '"Gelmedi"' in result["problems"][2]
    assert result["problems"][3].startswith("Mazeret bildirilecek")


def test_veli_adina_mazeret_pencere_kapali(result: dict[str, Any]) -> None:
    assert result["closedPosts"] == 0
    assert "en çok 3 gün geriye" in result["closedText"]
    assert result["closedResult"] is None


def test_uyum_tablosu_bu_hafta(result: dict[str, Any]) -> None:
    heat = result["heat"]
    assert len(heat) >= 3, heat
    previous, live, average = heat[0], heat[1], heat[2]
    assert previous[0] == "%40" and "low" in previous[1]
    assert live[0] == "%10" and "ak-plan-heat-live" in live[1] and "low" not in live[1]
    assert live[2].startswith("Bu hafta sürüyor")
    assert average[0] == "%40"
    assert "Bu hafta" in result["planText"] and "bu hafta (sürüyor)" in result["planText"]
