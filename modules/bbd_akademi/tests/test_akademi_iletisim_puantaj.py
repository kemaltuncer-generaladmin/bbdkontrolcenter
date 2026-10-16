"""Puantaj ay kapatma ve veli toplantısı randevu takvimi (Akademi README §14 madde 90; İ7, İ8).

`ui/panel/sessions.js` "Aylık ders dökümü" sekmesi ve `ui/panel/parent-meetings.js` Node'da küçük DOM'la
(`tools/a11y/harness.py`) çalıştırılır:

* Kapanmış ay: "Bu ay kapatıldı" + kapanıştan sonraki fark tablosu (`changesSinceClose`) + "Yeniden aç".
* Biten açık ay: "Ayı kapat" → pencere (yoklaması alınmamış ders onayı + not) → `admin_closeTeacherLoadMonth`
  gövdesi; yanıt (kapanmış döküm) yeniden istek atmadan çizilir.
* Veli toplantısı: gün gün saatler, dolu / boş, öğrenci (sınıf) · veli, "Listeyi yazdır"; saat planı hesabı Akademi
  `plan_slots` kuralıyla aynı; erişilebilirlik denetimi temiz.
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

SCENARIO = r"""
const qs = (query) => {
  const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${v}`);
  return parts.length ? `?${parts.join('&')}` : '';
};
function makeApp(root, api, { toasts = [] } = {}) {
  const get = (p, q) => api(`${p}${qs(q)}`);
  const client = {
    can: () => true, featureOff: () => false, get, getCached: get, invalidate: () => {},
    post: (p, body) => api(p, { method: 'POST', body }),
    async all(path, query = {}) { return (await get(path, query))?.items || []; },
  };
  return { root, api: client, toasts, can: () => true, has: () => true, enabled: () => true,
    go: () => {}, toast: (text, tone) => toasts.push([text, tone]),
    lookups: { teachers: async () => [{ id: 't1', displayName: 'Burak Şahin' }, { id: 't2', displayName: 'Hüseyin Yılmaz' }] } };
}
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ');
const buttons = (scope, label) => scope.querySelectorAll('button').filter((b) => b.textContent.trim() === label);
const dialog = (root) => root.querySelectorAll('.kit-dialog').at(-1);

export default async function ({ document, sleep, api, load, calls, audit }) {
  const out = {};
  const ses = await load('./panels/bbd_akademi/lessons/sessions.js');
  const pm = await load('./panels/bbd_akademi/parent-meetings.js');
  out.canClose = [
    ses.canCloseMonth({ from: '2026-08-01', totals: { upcomingCount: 0 } }, '2026-10-15'),
    ses.canCloseMonth({ from: '2026-10-01', totals: { upcomingCount: 4 } }, '2026-10-15'),
    ses.canCloseMonth({ from: '2026-11-01', totals: { upcomingCount: 0 } }, '2026-10-15'),
    ses.canCloseMonth({ from: '2026-08-01', totals: {}, closure: { closedAt: 'x' } }, '2026-10-15'),
  ];
  out.planned = [pm.plannedCount('10:00', '11:00', 10, 5), pm.plannedCount('10:00', '13:00', 10, 0),
    pm.plannedCount('10:00', '10:05', 10), pm.plannedCount('11:00', '10:00', 10), pm.plannedCount('1000', '11:00', 10)];
  out.states = [pm.slotState({}), pm.slotState({ booking: { status: 'scheduled' } }),
    pm.slotState({ booking: { status: 'done' } })];

  // --- kapanmış ay: fark tablosu ve yeniden aç
  {
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api);
    const view = ses.section(app);
    root.append(view.node);
    view.shown({ tab: 'load', month: '2026-09' });
    await sleep(60);
    out.closedText = text(root);
    out.closedA11y = audit(root);
    out.reopenButtons = buttons(root, 'Yeniden aç').length;
    out.closeButtonsOnClosed = buttons(root, 'Ayı kapat').length;
    root.remove();
  }
  // --- biten açık ay: ayı kapat
  {
    const root = document.createElement('div');
    document.body.append(root);
    const toasts = [];
    const app = makeApp(root, api, { toasts });
    const view = ses.section(app);
    root.append(view.node);
    view.shown({ tab: 'load', month: '2026-08' });
    await sleep(60);
    out.openText = text(root);
    buttons(root, 'Ayı kapat')[0].click();
    await sleep(30);
    const box = dialog(root) || dialog(document);
    out.dialogText = text(box);
    const check = box.querySelector('input[type="checkbox"]');
    check.checked = true;
    check.dispatchEvent(new Event('change'));
    check.dispatchEvent(new Event('input'));
    const note = box.querySelector('textarea');
    note.value = 'Ağustos bordrosu';
    note.dispatchEvent(new Event('input'));
    buttons(box, 'Ayı kapat')[0].click();
    await sleep(60);
    out.closeBody = calls.filter((c) => c.method === 'POST' && c.path === '/v1/admin/teacher-load/close').map((c) => c.body);
    out.afterClose = text(root);
    out.loadGets = calls.filter((c) => c.path === '/v1/admin/teacher-load?month=2026-08').length;
    out.toasts = toasts;
    root.remove();
  }
  // --- veli toplantısı
  {
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api);
    const view = pm.section(app);
    root.append(view.node);
    view.shown({});
    await sleep(80);
    out.pmText = text(root);
    out.pmA11y = audit(root);
    out.pmCalls = calls.filter((c) => c.path.startsWith('/v1/admin/parent-meeting-slots')).map((c) => c.path);
    out.pmButtons = ['Saat aç', 'Listeyi yazdır', 'Veli adına randevu', 'Saati kapat', 'Randevuyu iptal et']
      .map((label) => buttons(root, label).length);
    root.remove();
  }
  return out;
}
"""

COUNTS = ("plannedCount", "deliveredCount", "deliveredMinutes", "lessonCount", "makeupCount", "etutCount",
          "substituteCount", "substituteMinutes", "cancelledCount", "coveredByOthersCount", "pendingCount",
          "upcomingCount")


def _counts(**values: int) -> dict[str, int]:
    return {key: values.get(key, 0) for key in COUNTS}


BURAK = _counts(plannedCount=20, deliveredCount=18, deliveredMinutes=720, lessonCount=18, pendingCount=2)
CLOSED = {"month": "2026-09", "from": "2026-09-01", "to": "2026-09-30", "asOf": "2026-10-04T10:00:00+03:00",
          "rows": [{"teacher": {"id": "t1", "displayName": "Burak Şahin"}, **BURAK}], "totals": BURAK,
          "unassignedCount": 0,
          "closure": {"closedAt": "2026-10-04T10:00:00+03:00", "closedBy": "Hüseyin Yılmaz", "note": "Eylül bordrosu",
                      "pendingAtClose": 2},
          "changesSinceClose": [{"teacher": {"id": "t1", "displayName": "Burak Şahin"}, "closed": BURAK,
                                 "live": {**BURAK, "deliveredCount": 19, "deliveredMinutes": 760, "pendingCount": 1}}]}
OPEN = {"month": "2026-08", "from": "2026-08-01", "to": "2026-08-31", "asOf": "2026-10-15T10:00:00+03:00",
        "rows": [{"teacher": {"id": "t1", "displayName": "Burak Şahin"}, **BURAK}], "totals": BURAK,
        "unassignedCount": 0, "closure": None, "changesSinceClose": []}
CLOSED_AUG = {**OPEN, "closure": {"closedAt": "2026-10-15T10:01:00+03:00", "closedBy": "Hüseyin Yılmaz",
                                  "note": "Ağustos bordrosu", "pendingAtClose": 2}}

ELIF = {"id": "s1", "displayName": "Elif Kaya", "firstName": "Elif", "lastName": "Kaya", "externalRef": "STU-1",
        "status": "active", "classGroup": {"id": "c1", "name": "8-A"}}
SLOTS = {"items": [
    {"id": "p1", "staff": {"id": "t1", "displayName": "Burak Şahin"}, "title": "1. dönem veli toplantısı",
     "startsAt": "2030-10-19T10:00:00+03:00", "endsAt": "2030-10-19T10:10:00+03:00", "mode": "in_person",
     "location": "Oda 3", "audience": "related", "status": "booked", "createdAt": "2030-10-01T10:00:00+03:00",
     "booking": {"meetingRequestId": "m1", "student": ELIF, "bookedBy": {"id": "g1", "displayName": "Ayşe Kaya"},
                 "bookedAt": "2030-10-02T10:00:00+03:00", "note": "Matematik", "status": "scheduled"}},
    {"id": "p2", "staff": {"id": "t1", "displayName": "Burak Şahin"}, "title": "1. dönem veli toplantısı",
     "startsAt": "2030-10-19T10:15:00+03:00", "endsAt": "2030-10-19T10:25:00+03:00", "mode": "in_person",
     "location": "Oda 3", "audience": "all", "status": "open", "createdAt": "2030-10-01T10:00:00+03:00",
     "booking": None},
]}


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {
        "GET /v1/admin/teacher-load?month=2026-09": CLOSED,
        "GET /v1/admin/teacher-load?month=2026-08": OPEN,
        "POST /v1/admin/teacher-load/close": CLOSED_AUG,
        "GET /v1/admin/parent-meeting-slots*": SLOTS,
    }
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_kurallar_akademi_ile_ayni(result: dict[str, Any]) -> None:
    assert result["canClose"] == [True, False, False, False]
    assert result["planned"] == [4, 18, 0, 0, 0]
    assert result["states"] == [["Boş", ""], ["Dolu", "info"], ["Yapıldı", "good"]]


def test_kapanmis_ay_degismez_fark_uyarir(result: dict[str, Any]) -> None:
    text = result["closedText"]
    assert "Bu ay kapatıldı" in text and "Hüseyin Yılmaz" in text and "Eylül bordrosu" in text
    assert "kapanışta 2 dersin yoklaması alınmamıştı" in text
    assert "Kapanıştan sonra 1 öğretmenin sayısı" in text
    assert "18 → 19" in text and "720 → 760" in text
    assert result["reopenButtons"] == 1 and result["closeButtonsOnClosed"] == 0
    assert result["closedA11y"] == []


def test_ayi_kapat(result: dict[str, Any]) -> None:
    assert "Ay bitti" in result["openText"]
    assert "Ağustos 2026 — ayı kapat" in result["dialogText"]
    assert "2 dersin yoklaması alınmadı" in result["dialogText"]
    assert result["closeBody"] == [{"month": "2026-08", "note": "Ağustos bordrosu", "acknowledgePending": True}]
    assert "Bu ay kapatıldı" in result["afterClose"]
    assert result["loadGets"] == 1, "kapanış yanıtı yeniden istek atmadan çizilir"
    assert ["Ağustos 2026 kapatıldı.", "good"] in result["toasts"]


def test_veli_toplantisi_listesi(result: dict[str, Any]) -> None:
    text = result["pmText"]
    assert "Veli toplantısı" in text and "1/2 dolu" in text
    assert "Elif Kaya (8-A) · Ayşe Kaya" in text and "Matematik" in text
    assert "Bütün velilere açık" in text and "Oda 3" in text
    assert result["pmButtons"] == [1, 1, 1, 1, 1]
    assert result["pmCalls"][0].startswith("/v1/admin/parent-meeting-slots?from=")
    assert result["pmA11y"] == []
