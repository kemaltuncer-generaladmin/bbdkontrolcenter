"""Öğretmen aylık ders dökümü — puantaj (Akademi U19, README §14 madde 79; `admin_getTeacherMonthlyLoad`).

`ui/panel/lessons/sessions.js` "Aylık ders dökümü" sekmesi Node'da küçük DOM'la (`tools/a11y/harness.py`) çalıştırılır:

* Ay gezinmesi ve sınırları (Aralık → Ocak, şubat sonu), Türkçe ay adı.
* Tablo: öğretmen satırları + Toplam, yoklama bekleyen / öğretmensiz uyarıları; satır tıklaması Oturum takvimini
  o öğretmen ve ayla süzer (`admin_listClassSessions` `teacherId` + ay aralığı).
* CSV: UTF-8 BOM, `;` ayraç, CRLF, formül enjeksiyonu etkisiz; özet ve ders ders döküm (puantaj durumu Akademi
  kuralıyla aynı: yoklaması alınmış + iptal / tatil değil = gerçekleşti, dersi verene yazılır).
* İzin yoksa sekme yalnız açıklama gösterir, istek atmaz.
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
function makeApp(root, api, { deny = [], toasts = [] } = {}) {
  const get = (p, q) => api(`${p}${qs(q)}`);
  const client = {
    can: (id) => !deny.includes(id), get, getCached: get, invalidate: () => {},
    pages(path, query = {}, { limit = 50 } = {}) {
      let done = false;
      return { get done() { return done; }, async next() { done = true; return (await get(path, { ...query, limit }))?.items || []; } };
    },
    async all(path, query = {}, { limit = 100 } = {}) { return (await get(path, { ...query, limit }))?.items || []; },
  };
  return { root, api: client, toasts, can: (id) => client.can(id), has: () => true, enabled: () => true,
    go: () => {}, toast: (text, tone) => toasts.push([text, tone]),
    lookups: { teachers: async () => [{ id: 't1', displayName: 'Hüseyin Hoca' }, { id: 't2', displayName: 'Ayşe Hoca' }],
      options: async (kind, { empty } = {}) => [{ value: '', label: empty || '' }, { value: 'c1', label: '8-A' }] } };
}
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ');
const buttons = (scope, label) => scope.querySelectorAll('button').filter((b) => b.textContent.trim() === label);

export default async function ({ document, sleep, api, load, calls, audit }) {
  const out = {};
  const blobs = [];
  URL.createObjectURL = (blob) => { blobs.push(blob); return `blob:${blobs.length}`; };
  URL.revokeObjectURL = () => {};
  const ses = await load('./panels/bbd_akademi/lessons/sessions.js');
  out.months = [ses.shiftMonth('2026-01', -1), ses.shiftMonth('2026-12', 1), ses.shiftMonth('2026-10', 0)];
  out.range = [ses.monthRange('2026-02'), ses.monthRange('2028-02'), ses.monthRange('2026-12')];
  out.label = ses.monthLabel('2026-10');
  out.csv = ses.csvText(['a', 'b'], [['=1+1', 'x;y'], ['"q"', 5], [null, '-3']]);
  const now = Date.parse('2026-10-15T12:00:00+03:00');
  const base = { status: 'scheduled', effectiveTeacher: { id: 't1' }, startsAt: '2026-10-14T09:00:00+03:00',
    attendance: { taken: false } };
  out.states = [
    ses.loadState({ ...base, attendance: { taken: true } }, now),
    ses.loadState(base, now),
    ses.loadState({ ...base, startsAt: '2026-10-20T09:00:00+03:00' }, now),
    ses.loadState({ ...base, status: 'cancelled' }, now),
    ses.loadState({ ...base, status: 'holiday' }, now),
    ses.loadState({ ...base, status: 'orphaned' }, now),
    ses.loadState({ ...base, status: 'orphaned', attendance: { taken: true } }, now),
    ses.loadState({ ...base, effectiveTeacher: null }, now),
  ];

  {
    const root = document.createElement('div');
    document.body.append(root);
    const toasts = [];
    const app = makeApp(root, api, { toasts });
    const view = ses.section(app);
    root.append(view.node);
    view.shown({ tab: 'load', month: '2026-10' });
    await sleep(60);
    out.text = text(root);
    out.a11y = audit(root);
    out.loadCalls = calls.filter((c) => c.path.startsWith('/v1/admin/teacher-load')).map((c) => c.path);
    buttons(root, 'CSV indir')[0].click();
    await sleep(10);
    out.summaryCsv = blobs.length ? await blobs.at(-1).text() : null;
    out.summaryHead = blobs.length ? [...new Uint8Array(await blobs.at(-1).arrayBuffer()).slice(0, 3)] : null;
    out.summaryType = blobs.at(-1)?.type;
    buttons(root, 'Ders ders döküm (CSV)')[0].click();
    await sleep(40);
    out.detailCsv = blobs.length > 1 ? await blobs.at(-1).text() : null;
    out.detailCalls = calls.filter((c) => c.path.startsWith('/v1/admin/sessions?')).map((c) => c.path);
    buttons(root, '‹')[0].click();
    await sleep(40);
    out.prevMonth = calls.filter((c) => c.path.startsWith('/v1/admin/teacher-load')).at(-1).path;
    buttons(root, '›')[0].click();
    await sleep(40);
    const before = calls.length;
    const row = root.querySelectorAll('.kit-row').find((node) => text(node).includes('Hüseyin Hoca'));
    row.click();
    await sleep(60);
    out.drill = calls.slice(before).filter((c) => c.path.startsWith('/v1/admin/sessions?')).map((c) => c.path);
    out.toasts = toasts;
    view.destroy();
    root.remove();
  }
  {
    const root = document.createElement('div');
    document.body.append(root);
    const app = makeApp(root, api, { deny: ['admin_getTeacherMonthlyLoad'] });
    const view = ses.section(app);
    root.append(view.node);
    const before = calls.length;
    view.shown({ tab: 'load' });
    await sleep(30);
    out.deniedText = text(root);
    out.deniedCalls = calls.slice(before).filter((c) => c.path.startsWith('/v1/admin/teacher-load')).length;
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


HUSEYIN = _counts(plannedCount=20, deliveredCount=18, deliveredMinutes=720, lessonCount=15, makeupCount=1, etutCount=2,
                  cancelledCount=1, coveredByOthersCount=1, pendingCount=2)
AYSE = _counts(plannedCount=10, deliveredCount=9, deliveredMinutes=360, lessonCount=9, substituteCount=1,
               substituteMinutes=40, upcomingCount=1)
LOAD = {"month": "2026-10", "from": "2026-10-01", "to": "2026-10-31", "asOf": "2026-10-15T12:00:00+03:00",
        "rows": [{"teacher": {"id": "t2", "displayName": "=Ayşe Hoca"}, **AYSE},
                 {"teacher": {"id": "t1", "displayName": "Hüseyin Hoca"}, **HUSEYIN}],
        "totals": {key: HUSEYIN[key] + AYSE[key] for key in COUNTS}, "unassignedCount": 3}


def _session(sid: str, **extra: Any) -> dict[str, Any]:
    base = {"id": sid, "occurrenceKey": f"tt:{sid}", "kind": "lesson", "date": "2026-10-13",
            "startsAt": "2026-10-13T09:00:00+03:00", "endsAt": "2026-10-13T09:40:00+03:00",
            "subject": {"id": "matematik", "name": "Matematik"}, "group": {"id": "c1", "name": "8-A"},
            "groups": [{"id": "c1", "name": "8-A"}, {"id": "c2", "name": "8-B"}],
            "scheduledTeacher": {"id": "t1", "displayName": "Hüseyin Hoca"},
            "effectiveTeacher": {"id": "t2", "displayName": "Ayşe Hoca"}, "isSubstitute": True, "status": "scheduled",
            "statusReason": None, "attendance": {"taken": True, "counts": {"enrolled": 30, "present": 27, "late": 1}}}
    return {**base, **extra}


SESSIONS = {"items": [
    _session("s2", startsAt="2026-10-14T10:00:00+03:00", endsAt="2026-10-14T10:40:00+03:00", date="2026-10-14",
             status="cancelled", statusReason="Seminer; okul gezisi", isSubstitute=False,
             effectiveTeacher={"id": "t1", "displayName": "Hüseyin Hoca"}, attendance={"taken": False}),
    _session("s1"),
], "nextCursor": None}


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {"GET /v1/admin/teacher-load*": LOAD, "GET /v1/admin/sessions?*": SESSIONS}
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_ay_yardimcilari(result: dict[str, Any]) -> None:
    assert result["months"] == ["2025-12", "2027-01", "2026-10"]
    assert result["range"] == [{"from": "2026-02-01", "to": "2026-02-28"}, {"from": "2028-02-01", "to": "2028-02-29"},
                               {"from": "2026-12-01", "to": "2026-12-31"}]
    assert result["label"] == "Ekim 2026"


def test_csv_bicimi(result: dict[str, Any]) -> None:
    assert result["csv"] == "\ufeffa;b\r\n'=1+1;\"x;y\"\r\n\"\"\"q\"\"\";5\r\n;'-3\r\n"


def test_puantaj_durumu_akademi_kuraliyla_ayni(result: dict[str, Any]) -> None:
    assert result["states"] == ["delivered", "pending", "upcoming", "cancelled", "holiday", "orphaned", "delivered",
                                "unassigned"]


def test_tablo_uyarilar_ve_erisilebilirlik(result: dict[str, Any]) -> None:
    text = result["text"]
    assert "Aylık ders dökümü" in text and "Ekim 2026" in text
    assert "Hüseyin Hoca" in text and "=Ayşe Hoca" in text and "Toplam" in text
    assert "Gerçekleşen ders" in text and "27" in text          # 18 + 9
    assert "2 dersin saati geçti ama yoklaması alınmadı" in text
    assert "3 derste öğretmen atanmamış" in text
    assert "dersi verene yazılır" in text
    assert result["loadCalls"][0] == "/v1/admin/teacher-load?month=2026-10"
    assert result["prevMonth"] == "/v1/admin/teacher-load?month=2026-09"
    assert result["a11y"] == []


def test_ozet_csv(result: dict[str, Any]) -> None:
    lines = result["summaryCsv"].lstrip("\ufeff").split("\r\n")
    assert lines[0].startswith("Ay;Öğretmen;Programdaki;Gerçekleşen;Ders;Telafi;Etüt;Yerine girdiği;İptal;")
    assert lines[0].endswith("Gerçekleşen süre (dk);Gerçekleşen süre (saat);Yerine girdiği süre (dk)")
    assert lines[1] == "2026-10;'=Ayşe Hoca;10;9;9;0;0;1;0;0;0;1;360;6,00;40"
    assert lines[2] == "2026-10;Hüseyin Hoca;20;18;15;1;2;0;1;1;2;0;720;12,00;0"
    assert lines[3] == "2026-10;TOPLAM;30;27;24;1;2;1;1;1;2;1;1080;18,00;40"
    assert result["summaryHead"] == [0xEF, 0xBB, 0xBF] and lines[-1] == ""  # UTF-8 BOM (Excel)
    assert result["summaryType"].startswith("text/csv")


def test_ders_ders_dokum_csv(result: dict[str, Any]) -> None:
    assert result["detailCalls"][0] == "/v1/admin/sessions?from=2026-10-01&to=2026-10-31&limit=100"
    lines = result["detailCsv"].lstrip("\ufeff").split("\r\n")
    assert lines[0].split(";")[-3:] == ["Puantaj", "Puantaja yazılan", "Gerekçe / not"]
    # tarih sırası: 13 Ekim (yerine ders, gerçekleşti → Ayşe'ye) önce, 14 Ekim iptal sonra
    assert lines[1] == ("2026-10-13;Salı;09:00;09:40;40;Ders;Matematik;8-A + 8-B;Hüseyin Hoca;Ayşe Hoca;Evet;"
                        "Planlı;28/30 geldi;Gerçekleşti;Ayşe Hoca;")
    assert lines[2].endswith(";İptal;Alınmadı;İptal;;\"Seminer; okul gezisi\"")
    assert ["2 ders indirildi.", "good"] in result["toasts"]


def test_satirdan_ogretmenin_ayi(result: dict[str, Any]) -> None:
    assert "/v1/admin/sessions?from=2026-10-01&to=2026-10-31&teacherId=t1&limit=50" in result["drill"]


def test_izin_yoksa_istek_yok(result: dict[str, Any]) -> None:
    assert "personel yönetimi yetkisi" in result["deniedText"]
    assert result["deniedCalls"] == 0
