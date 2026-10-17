"""Ders programı HAFTA TAKVİMİ (`ui/panel/lessons/timetable-calendar.js`; Akademi README §14 madde 125, iş 69).

Kullanıcı (17.10.2026): "kontrol merkezinde ders programı girme kısmı çok karışık. hafta seçince o haftanın programı
gelmeli işte öbür haftaya geçince boşalmalı" · "takvim olacak ekranda. bu hafta önceki hafta sonraki hafta diye ok
tuşları olacak yanında. sınıf seçip program girecez şak şak".

Node'da küçük DOM'la (`tools/a11y/harness.py`) bütün akış sınanır:

* sınıf kendiliğinden seçilmez (birden çok sınıfta "Sınıf seçin"; seçilmeden hafta okunmaz);
* ‹ Önceki · Bu hafta · Sonraki › haftayı değiştirir, her hafta `admin_getTimetableWeek` ile ayrı okunur;
  PageDown da sonraki haftaya geçer;
* hücrede ders, öğretmen, derslik, ortak sınıf ("+ 8-B"), "yalnız bu hafta" rozeti, çakışma; başka sınıfın ortak dersi
  salt okunur; tatil günü "Tatil — ders yok"; geçmiş hafta salt okunur (pencere açılmaz);
* boş hücre → pencere: bu sınıfın dersi önde, ders seçince öğretmen kendiliğinden, öğretmen zorunlu; her değişiklik YALNIZ
  O HAFTA (lider mesajı 2: soru yok, tarih seçici yok), gövde `{scope: this_week, expectedVersion, cells}`; ek sınıf yoksa `classGroupIds` / `programId` gitmez
  (eski Akademi bilinmeyen alanı 422 ile reddeder); ek sınıf + müfredat;
* Delete → boş hücre; sürükle-bırak → taşıma (iki hücre); Ctrl+C / Ctrl+V — ek pencere açılmaz;
* boş hafta: "Bu hafta için program yok · Geçen haftayı kopyala" → `admin_copyTimetableWeek` (kaynak geçen hafta);
  "Bu haftayı kopyala →" (sonraki hafta hazır seçili) çoklu hafta + "Dönem sonuna kadar";
* erişilebilirlik denetimi temiz; saf yardımcılar (hafta adı, şablon hücreleri, kopya özeti).
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

CLASSES = [{"id": "c1", "name": "8-A", "gradeLevel": 8}, {"id": "c2", "name": "8-B", "gradeLevel": 8},
           {"id": "c3", "name": "TYT Hüseyin", "gradeLevel": None}]
SLOTS = {"slots": [
    {"id": f"L{n}", "dayOfWeek": day, "order": n, "startTime": f"0{8 + n}:00", "endTime": f"0{8 + n}:40",
     "kind": "lesson"} for day in ("mon", "tue", "wed") for n in (1, 2)]}
SUBJECTS = [{"id": "fizik", "name": "Fizik", "level": "lise"}, {"id": "matematik", "name": "Matematik", "level": "ortaokul"},
            {"id": "turkce", "name": "Türkçe", "level": "ortaokul"}]
PROGRAMS = {"items": [{"id": "lgs-matematik", "title": "Matematik", "kind": "exam", "exam": "LGS",
                       "subject": {"id": "matematik", "name": "Matematik"}, "levelKey": "lgs", "levelLabel": "LGS",
                       "levelOrder": 45, "scope": "global", "curriculumVersion": "meb"}]}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const cal = await load('./panels/bbd_akademi/lessons/timetable-calendar.js');
  const hrs = await load('./panels/bbd_akademi/lessons/timetable-hours.js');
  const { AkademiError } = await load('./panels/bbd_akademi/api.js');
  const common = await load('./panels/bbd_akademi/common.js');
  const { slotLayout } = await load('./panels/bbd_akademi/lessons/timetable-shared.js');
  const out = { pure: {}, puts: [], posts: [], hourPuts: [] };
  const today = common.todayIst();
  const W0 = common.weekStart(today);
  const W1 = common.addDays(W0, 7);
  const W2 = common.addDays(W0, 14);
  const WP = common.addDays(W0, -7);
  out.weeks = { W0, W1, W2, WP };

  // ------------------------------------------------------------ saf yardımcılar
  out.pure.labels = [cal.weekLabel('2026-10-12'), cal.weekLabel('2026-10-26'), cal.weekLabel('2025-12-29')];
  out.pure.short = cal.shortWeekLabel('2026-10-26');
  out.pure.head = cal.dayHead('mon', '2026-10-19');
  out.pure.rel = ['2026-10-12', '2026-10-19', '2026-10-05', '2026-11-02'].map((w) => cal.weekRelation(w, '2026-10-17'));
  const layout = slotLayout(SLOTS.slots);
  out.pure.template = cal.templateCells([
    { dayOfWeek: 'mon', bellSlotId: 'L1', subjectId: 'matematik', teacherId: 't1', room: null, classGroupIds: ['c2'] },
    { dayOfWeek: 'mon', bellSlotId: 'yok', subjectId: 'fizik' },
    { dayOfWeek: 'tue', bellSlotId: 'L2', subjectId: 'fizik', programId: 'p1' },
  ], layout, 'c1');
  out.pure.cellOf = cal.cellOf({ subject: { id: 'matematik' }, teacher: { id: 't1' }, room: '204',
    classGroups: [{ id: 'c1' }], program: null }, { dayOfWeek: 'tue', bellSlotId: 'L1' }, 'c1');
  const evening = { startTime: '15:00', lessonMinutes: 40, breakMinutes: 10, lessonsPerDay: 4,
    lunch: { afterLesson: 2, minutes: 30 }, days: [{ dayOfWeek: 'sat', startTime: '09:00', lessonsPerDay: 2, off: false },
      { dayOfWeek: 'sun', startTime: null, lessonsPerDay: null, off: true }] };
  const periods = hrs.computePeriods(evening);
  out.pure.monday = periods.filter((p) => p.dayOfWeek === 'mon').map((p) => `${p.id} ${p.startTime}-${p.endTime}`);
  out.pure.saturday = periods.filter((p) => p.dayOfWeek === 'sat').map((p) => `${p.id} ${p.startTime}`);
  out.pure.sunday = periods.filter((p) => p.dayOfWeek === 'sun').length;
  out.pure.preview = hrs.previewRows(periods);
  out.pure.problem = [hrs.hoursProblem({ ...evening, startTime: '22:30' }), hrs.hoursProblem({ ...evening, lessonMinutes: 5 }),
    hrs.hoursProblem(evening)];
  out.pure.removal = hrs.removalText({ count: 3, lessonNumbers: [7, 8], items: [{ classGroup: { name: '8-A' } }] });
  out.pure.draftRound = hrs.settingsOf(hrs.draftOf(evening));
  out.pure.summary = cal.copySummary([{ weekStart: '2026-10-19', status: 'copied' },
    { weekStart: '2026-10-26', status: 'conflict' }, { weekStart: '2026-10-05', status: 'past' }]);

  // ------------------------------------------------------------ taklit Akademi
  const MAT = { id: 'matematik', name: 'Matematik' };
  const HUS = { id: 't1', displayName: 'Hüseyin Hoca' };
  const day = (week, i, key, extra = {}) => ({ date: common.addDays(week, i), dayOfWeek: key,
    past: common.addDays(week, i) < today, holiday: null, ...extra });
  const days = (week) => ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].map((key, i) => day(week, i, key,
    key === 'wed' && week === W1 ? { holiday: { title: 'Ara tatil', kind: 'holiday' } } : {}));
  let version = 4;
  // Haftanın ders saatleri (Akademi README §14 madde 128): satırlar buradan; ders + teneffüs.
  const PERIODS = [];
  for (const key of ['mon', 'tue', 'wed']) {
    PERIODS.push({ id: 'L1', dayOfWeek: key, order: 1, lessonNo: 1, kind: 'lesson', startTime: '09:00', endTime: '09:40', label: '1. ders' });
    PERIODS.push({ id: 'B1', dayOfWeek: key, order: 2, lessonNo: null, kind: 'break', startTime: '09:40', endTime: '09:50', label: 'Teneffüs' });
    PERIODS.push({ id: 'L2', dayOfWeek: key, order: 3, lessonNo: 2, kind: 'lesson', startTime: '09:50', endTime: '10:30', label: '2. ders' });
  }
  const HOURS = { weekStart: W0, source: 'inherited', inheritedFrom: WP, appliesUntil: null, version: 0,
    settings: { startTime: '09:00', lessonMinutes: 40, breakMinutes: 10, lessonsPerDay: 2, lunch: null,
      days: [{ dayOfWeek: 'thu', startTime: null, lessonsPerDay: null, off: true }] },
    periods: PERIODS, updatedAt: null, updatedBy: null };
  const weekData = (week) => {
    const base = { classGroup: { id: 'c1', name: '8-A' }, weekStart: week, weekEnd: common.addDays(week, 6),
      version, editable: week >= W0, days: days(week), entries: [], sharedEntries: [], conflicts: [],
      previousWeekEntryCount: 0, updatedAt: null, updatedBy: null, hours: { ...HOURS, weekStart: week } };
    if (week === W1 || week === WP) {
      base.entries = [
        { id: 'e1', dayOfWeek: 'mon', bellSlotId: 'L1', subject: MAT, teacher: HUS, room: null, status: 'active',
          classGroups: [{ id: 'c1', name: '8-A' }, { id: 'c2', name: '8-B' }], ownerClassGroup: { id: 'c1', name: '8-A' },
          program: null, date: week, validFrom: '2026-09-01', validTo: null, repeat: 'weekly' },
        { id: 'e2', dayOfWeek: 'mon', bellSlotId: 'L2', subject: MAT, teacher: null, room: '204', status: 'active',
          classGroups: [{ id: 'c1', name: '8-A' }], ownerClassGroup: { id: 'c1', name: '8-A' }, program: null,
          date: week, validFrom: week, validTo: common.addDays(week, 6), repeat: 'this_week' },
      ];
      base.sharedEntries = [
        { id: 'e9', dayOfWeek: 'tue', bellSlotId: 'L1', subject: { id: 'fizik', name: 'Fizik' }, teacher: HUS,
          room: null, status: 'active', classGroups: [{ id: 'c3', name: 'TYT Hüseyin' }, { id: 'c1', name: '8-A' }],
          ownerClassGroup: { id: 'c3', name: 'TYT Hüseyin' }, program: null, date: common.addDays(week, 1),
          validFrom: null, validTo: null, repeat: 'weekly' },
      ];
      base.conflicts = [{ kind: 'teacher', dayOfWeek: 'mon', bellSlotId: 'L1', entryIndex: 0, teacher: HUS,
        room: null, conflictingClassGroup: { id: 'c2', name: '8-B' } }];
    }
    if (week === W2) base.previousWeekEntryCount = 2;
    return base;
  };
  globalThis.__apiHook = (method, path, options) => {
    const hoursMatch = path.match(/\/timetable\/weeks\/(\d{4}-\d{2}-\d{2})\/hours$/);
    if (hoursMatch && method === 'PUT') {
      out.hourPuts.push({ week: hoursMatch[1], body: options.body });
      if (!options.body.confirmRemovals && options.body.settings.lessonsPerDay < 2) {
        throw new AkademiError({ code: 'timetable_hours_removes_lessons', status: 409, message: 'x',
          details: { count: 3, lessonNumbers: [2], items: [{ classGroup: { id: 'c1', name: '8-A' }, dayOfWeek: 'mon', bellSlotId: 'L2', lessonNo: 2 }] } });
      }
      return { hours: { ...HOURS, source: 'week' }, removedLessonCount: options.body.confirmRemovals ? 3 : 0, updatedSessionCount: 4 };
    }
    const match = path.match(/\/timetable\/weeks\/(\d{4}-\d{2}-\d{2})(\/cells|\/copy)?/);
    if (!match) return undefined;
    const [, week, tail] = match;
    if (method === 'GET') return weekData(week);
    if (method === 'PUT' && tail === '/cells') {
      out.puts.push({ week, body: options.body });
      version += 1;
      return weekData(week);
    }
    if (method === 'POST' && tail === '/copy') {
      out.posts.push({ week, body: options.body });
      version += 1;
      const targets = options.body.targetWeekStarts || [];
      return { results: targets.map((w) => ({ weekStart: w, status: 'copied', conflicts: [] })), version };
    }
    return undefined;
  };

  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '').map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  const root = document.createElement('div');
  document.body.append(root);
  const toasts = [];
  const gone = [];
  const app = {
    root,
    can: () => true,
    enabled: () => true,
    go: (key, payload) => gone.push([key, payload]),
    toast: (text, tone) => toasts.push([text, tone]),
    api: {
      can: () => true,
      featureOff: () => false,
      get: (path, query) => api(`${path}${qs(query)}`),
      getCached: (path, query) => api(`${path}${qs(query)}`),
      put: (path, body) => api(path, { method: 'PUT', body }),
      post: (path, body) => api(path, { method: 'POST', body }),
      invalidate: () => {},
    },
    lookups: {
      classGroups: async () => CLASSES,
      bellSlots: async () => SLOTS,
      subjects: async () => SUBJECTS,
      teachers: async () => [HUS, { id: 't2', displayName: 'Ayşe Hoca' }],
      terms: async () => [{ id: 'term1', name: '2026-2027 Güz', startsOn: common.addDays(W0, -40),
        endsOn: common.addDays(W0, 60), current: true }],
    },
  };
  const text = (node) => (node ? node.textContent : '');
  const buttons = (scope, label) => scope.querySelectorAll('button').filter((b) => b.textContent === label
    || b.getAttribute('aria-label') === label);
  const cell = (key) => root.querySelectorAll('.ak-cal-cell').find((node) => node.dataset.key === key);
  const dialog = () => document.querySelector('.kit-overlay');
  const lastWeekGet = () => calls.filter((c) => c.method === 'GET' && c.path.includes('/timetable/weeks/')).map((c) => c.path).pop();

  const tab = cal.calendarTab(app, {});
  root.append(tab.node);
  await tab.reload();
  await sleep(30);
  const pick = root.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Sınıf');
  out.pickValue = pick?.value;
  out.pickFirst = pick?.querySelectorAll('option')[0]?.textContent;
  out.emptyText = text(root);
  out.readsBeforePick = calls.filter((c) => c.path.includes('/timetable/weeks/')).length;
  out.titleInitial = text(root.querySelector('.ak-cal-week'));
  out.relInitial = text(root.querySelector('.ak-cal-rel'));

  // Sınıf seç → bu hafta; Sonraki hafta › → gelecek hafta.
  pick.value = 'c1';
  pick.dispatchEvent(new Event('change'));
  await sleep(30);
  out.getW0 = lastWeekGet();
  buttons(root, 'Sonraki hafta')[0].click();
  await sleep(30);
  out.getW1 = lastWeekGet();
  out.relW1 = text(root.querySelector('.ak-cal-rel'));
  out.cellMon1 = text(cell('mon|L1'));
  out.cellMon2 = text(cell('mon|L2'));
  out.cellTue1 = text(cell('tue|L1'));
  out.cellWed1 = text(cell('wed|NEW'));
  out.heads = root.querySelectorAll('.ak-cal-dayhead').map((n) => n.textContent);
  out.banner = text(root.querySelector('.ak-cal-banner'));
  out.summary = text(root.querySelector('.ak-cal-foot'));
  out.a11yGrid = audit(root);

  // Ortak ders hücresi: pencere açılmaz, sahibini söyler.
  cell('tue|L1').click();
  await sleep(10);
  out.sharedDialog = Boolean(dialog());
  out.sharedToast = toasts[toasts.length - 1]?.[0];

  // "+ Ders ekle" → pencere (saat varsayılanı: günün son dersinden 10 dk sonra, 40 dk).
  cell('tue|NEW').click();
  await sleep(20);
  let box = dialog();
  out.dialogTitle = text(box.querySelector('.kit-dialog-title'));
  const subject = box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Ders');
  out.firstGroup = subject.querySelectorAll('optgroup')[0]?.label;
  out.radios = box.querySelectorAll('input').filter((i) => i.type === 'radio').length;
  out.dateFields = root.querySelectorAll('input').filter((i) => /tarih|hafta seç/i.test(i.getAttribute('aria-label') || '')).length;
  subject.value = 'matematik';
  subject.dispatchEvent(new Event('change'));
  await sleep(10);
  const teacher = box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Öğretmen');
  out.suggested = teacher.value;
  teacher.value = '';
  buttons(box, 'Kaydet')[0].click();
  await sleep(10);
  out.teacherError = text(box.querySelector('.kit-dialog-error'));
  out.putsAfterError = out.puts.length;
  teacher.value = 't1';
  const room = box.querySelectorAll('input').find((i) => i.getAttribute('aria-label') === 'Derslik');
  room.value = '305';
  out.a11yDialog = audit(box);
  buttons(box, 'Kaydet')[0].click();
  await sleep(30);
  out.dialogClosed = !dialog();

  // Dolu hücre → ek sınıf + müfredat + "Bu hafta ve sonraki haftalar".
  cell('mon|L2').click();
  await sleep(30);
  box = dialog();
  out.removeButton = buttons(box, 'Dersi kaldır').length;
  box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Öğretmen').value = 't2';
  const add = box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Birlikte ders yapan sınıf ekle');
  add.value = 'c3';
  add.dispatchEvent(new Event('change'));
  const program = box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Müfredat (isteğe bağlı)');
  out.programOptions = program.querySelectorAll('option').map((o) => o.textContent);
  program.value = 'lgs-matematik';
  buttons(box, 'Kaydet')[0].click();
  await sleep(30);

  // Delete → soru yok, yalnız bu hafta.
  cell('mon|L1').focus();
  cell('mon|L1').dispatchEvent(new KeyboardEvent('keydown', { key: 'Delete' }));
  await sleep(30);
  out.deleteDialog = Boolean(dialog());

  // Sürükle-bırak: mon1 → tue2 taşı, "Bu hafta ve sonraki haftalar".
  const drop = new Event('drop', { bubbles: true, cancelable: true });
  drop.dataTransfer = { getData: () => 'mon|L1' };
  cell('tue|NEW').dispatchEvent(drop);
  await sleep(30);
  out.moveDialog = Boolean(dialog());
  out.moveToast = toasts[toasts.length - 1]?.[0];

  // Ctrl+C / Ctrl+V.
  cell('mon|L2').dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true }));
  cell('tue|NEW').dispatchEvent(new KeyboardEvent('keydown', { key: 'v', ctrlKey: true }));
  await sleep(30);

  // Bu haftayı kopyala → (sonraki hafta hazır seçili) + bir hafta daha.
  buttons(root, 'Bu haftayı kopyala →')[0].click();
  await sleep(30);
  box = dialog();
  out.copyText = text(box);
  const chips = box.querySelectorAll('.ak-cal-copy .kit-chip');
  out.firstChip = text(chips[0]);
  chips[1].click();
  buttons(box, 'Kopyala')[0].click();
  await sleep(40);

  // PageDown → sonraki hafta (boş): "Geçen haftayı kopyala".
  cell('mon|L1').dispatchEvent(new KeyboardEvent('keydown', { key: 'PageDown' }));
  await sleep(30);
  out.getW2 = lastWeekGet();
  out.emptyBanner = text(root.querySelector('.ak-cal-banner'));
  out.emptyCopyButton = root.querySelectorAll('.ak-cal-banner button').filter((b) => b.textContent.startsWith('Geçen haftayı kopyala')).length;
  out.copyHiddenWhenEmpty = buttons(root, 'Bu haftayı kopyala →')[0]?.hidden;
  root.querySelectorAll('.ak-cal-banner button').find((b) => b.textContent.startsWith('Geçen haftayı kopyala')).click();
  await sleep(40);

  // Serbest saat: perşembe "+ Ders ekle" → 17:15–18:40 (bitiş önce girilirse hata).
  out.hoursButtons = buttons(root, 'Ders saatleri').length;
  cell('thu|NEW').click();
  await sleep(20);
  box = dialog();
  const timeField = (label) => box.querySelectorAll('input').find((i) => i.getAttribute('aria-label') === label);
  timeField('Başlangıç saati').value = '17:15';
  timeField('Bitiş saati').value = '17:00';
  const subj = box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Ders');
  subj.value = 'matematik';
  subj.dispatchEvent(new Event('change'));
  box.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Öğretmen').value = 't1';
  buttons(box, 'Kaydet')[0].click();
  await sleep(10);
  out.timeError = text(box.querySelector('.kit-dialog-error'));
  timeField('Bitiş saati').value = '18:40';
  buttons(box, 'Kaydet')[0].click();
  await sleep(30);
  out.freeClosed = !dialog();
  out.rowHeads = root.querySelectorAll('.ak-tt-rowhead').map((n) => n.textContent);

  // Geçmiş hafta: salt okunur.
  buttons(root, 'Bu hafta')[0].click();
  await sleep(30);
  buttons(root, 'Önceki hafta')[0].click();
  await sleep(30);
  out.getWP = lastWeekGet();
  out.pastBanner = text(root.querySelector('.ak-cal-banner'));
  const before = out.puts.length;
  cell('mon|L2').click();
  await sleep(10);
  out.pastDialog = Boolean(dialog());
  out.pastToast = toasts[toasts.length - 1]?.[0];
  out.pastDraggable = cell('mon|L1').draggable === true;
  out.putsInPast = out.puts.length - before;
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {"GET /v1/admin/curricula/programs*": PROGRAMS}
    scenario = (
        f"const CLASSES = {json.dumps(CLASSES, ensure_ascii=False)};\n"
        f"const SLOTS = {json.dumps(SLOTS)};\nconst SUBJECTS = {json.dumps(SUBJECTS, ensure_ascii=False)};\n"
        + SCENARIO
    )
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_saf_yardimcilar(result: dict[str, Any]) -> None:
    pure = result["pure"]
    assert pure["labels"] == ["12–18 Ekim 2026", "26 Ekim – 1 Kasım 2026", "29 Aralık 2025 – 4 Ocak 2026"]
    assert pure["short"] == "26 Eki – 1 Kas"
    assert pure["head"] == ["Pzt", "19 Ekim"]
    assert pure["rel"] == ["Bu hafta", "Gelecek hafta", "Geçen hafta", "3 hafta sonra"]
    assert pure["template"]["skipped"] == 1
    assert pure["template"]["cells"] == [
        {"dayOfWeek": "mon", "bellSlotId": "L1", "subjectId": "matematik", "teacherId": "t1", "room": None,
         "classGroupIds": ["c1", "c2"]},
        {"dayOfWeek": "tue", "bellSlotId": "L2", "subjectId": "fizik", "teacherId": None, "room": None,
         "programId": "p1"},
    ]
    assert pure["cellOf"] == {"dayOfWeek": "tue", "bellSlotId": "L1", "subjectId": "matematik", "teacherId": "t1",
                              "room": "204"}
    assert pure["summary"].startswith("1 haftaya kopyalandı · 1 haftada çakışma var, yazılmadı (26 Eki – 1 Kas)")


def test_sinif_kendiliginden_secilmez(result: dict[str, Any]) -> None:
    """Birden çok sınıfta liste "Sınıf seçin" ile açılır, hiçbir hafta okunmaz; açılış bu haftadır."""
    assert result["pickValue"] == ""
    assert result["pickFirst"] == "Sınıf seçin"
    assert "Sınıf seçin" in result["emptyText"]
    assert result["readsBeforePick"] == 0
    assert result["relInitial"] == "Bu hafta"


def test_hafta_oklari_ve_haftanin_okunusu(result: dict[str, Any]) -> None:
    w = result["weeks"]
    assert result["getW0"] == f"/v1/admin/class-groups/c1/timetable/weeks/{w['W0']}"
    assert result["getW1"] == f"/v1/admin/class-groups/c1/timetable/weeks/{w['W1']}"
    assert result["relW1"] == "Gelecek hafta"
    assert result["getW2"].endswith(w["W2"])  # PageDown
    assert result["getWP"].endswith(w["WP"])  # Bu hafta → ‹ Önceki hafta


def test_hucreler(result: dict[str, Any]) -> None:
    mon1 = result["cellMon1"]
    assert "Matematik" in mon1 and "Hüseyin Hoca" in mon1 and "+ 8-B" in mon1 and "Çakışma" in mon1
    mon2 = result["cellMon2"]
    assert "yalnız bu hafta" in mon2 and "Öğretmen yok" in mon2 and "204" in mon2
    assert "ortak: TYT Hüseyin" in result["cellTue1"]
    assert "Tatil — ders yok" in result["cellWed1"]
    assert any("Ara tatil" in head for head in result["heads"])
    assert "1 çakışma" in result["banner"] and "Hüseyin Hoca" in result["banner"]
    assert "Bu hafta 2 ders" in result["summary"]
    assert result["sharedDialog"] is False and "TYT Hüseyin" in result["sharedToast"]
    assert result["a11yGrid"] == []


def test_bos_hucreye_sak_sak_giris(result: dict[str, Any]) -> None:
    assert result["dialogTitle"].startswith("Salı") and "Yeni ders" in result["dialogTitle"]
    assert result["firstGroup"] == "Bu sınıfta"
    assert result["radios"] == 0 and result["dateFields"] == 0  # soru yok, tarih seçici yok
    assert result["suggested"] == "t1"  # bu sınıfta Matematik'i Hüseyin Hoca veriyor
    assert result["teacherError"] == "Öğretmen seçin." and result["putsAfterError"] == 0
    assert result["a11yDialog"] == []
    assert result["dialogClosed"] is True
    first = result["puts"][0]
    assert first["week"] == result["weeks"]["W1"]
    assert first["body"] == {"scope": "this_week", "expectedVersion": 4, "cells": [
        {"dayOfWeek": "tue", "bellSlotId": "T0950-1030", "subjectId": "matematik", "teacherId": "t1", "room": "305"}]}


def test_ek_sinif_ve_mufredat(result: dict[str, Any]) -> None:
    assert result["removeButton"] == 1
    assert result["programOptions"] == ["Müfredat yok", "LGS · Matematik"]
    body = result["puts"][1]["body"]
    assert body["scope"] == "this_week" and body["expectedVersion"] == 5
    assert body["cells"] == [{"dayOfWeek": "mon", "bellSlotId": "L2", "subjectId": "matematik", "teacherId": "t2",
                              "room": "204", "classGroupIds": ["c1", "c3"], "programId": "lgs-matematik"}]


def test_kaldir_tasi_kopyala(result: dict[str, Any]) -> None:
    assert result["deleteDialog"] is False and result["moveDialog"] is False
    assert "taşındı" in result["moveToast"]
    assert result["puts"][2]["body"]["cells"] == [{"dayOfWeek": "mon", "bellSlotId": "L1", "subjectId": None}]
    assert result["puts"][2]["body"]["scope"] == "this_week"
    move = result["puts"][3]["body"]
    assert move["scope"] == "this_week"
    assert move["cells"] == [
        {"dayOfWeek": "mon", "bellSlotId": "L1", "subjectId": None},
        {"dayOfWeek": "tue", "bellSlotId": "L1", "subjectId": "matematik", "teacherId": "t1", "room": None,
         "classGroupIds": ["c1", "c2"]},  # "+ Ders ekle"ye bırakılan ders aynı saatte (L1) o güne
    ]
    paste = result["puts"][4]["body"]
    assert paste["scope"] == "this_week"
    assert paste["cells"] == [{"dayOfWeek": "tue", "bellSlotId": "L2", "subjectId": "matematik", "teacherId": None,
                               "room": "204"}]


def test_haftayi_kopyala_ve_gecen_haftayi_kopyala(result: dict[str, Any]) -> None:
    w = result["weeks"]
    assert "Dönem sonuna kadar her hafta" in result["copyText"]
    assert result["firstChip"].startswith("Sonraki hafta")
    copy = result["posts"][0]
    assert copy["week"] == w["W1"]
    assert len(copy["body"]["targetWeekStarts"]) == 2 and copy["body"]["throughDate"] is None
    assert w["W2"] in copy["body"]["targetWeekStarts"]
    assert result["emptyCopyButton"] == 1 and result["copyHiddenWhenEmpty"] is True
    assert "Bu hafta için program yok" in result["emptyBanner"] and "Geçen hafta 2 ders vardı" in result["emptyBanner"]
    previous = result["posts"][1]
    assert previous["week"] == w["W1"] and previous["body"]["targetWeekStarts"] == [w["W2"]]


def test_gecmis_hafta_salt_okunur(result: dict[str, Any]) -> None:
    assert "Geçmiş hafta — salt okunur" in result["pastBanner"]
    assert result["pastDialog"] is False and result["putsInPast"] == 0
    assert result["pastDraggable"] is False
    assert "Geçmiş" in result["pastToast"]


def test_ders_saatleri_hesap(result: dict[str, Any]) -> None:
    """Önizleme hesabı Akademi `periods_of` ile aynı: ders + teneffüs, öğle arası teneffüsün yerine, gün ezmesi."""
    pure = result["pure"]
    assert pure["monday"] == ["L1 15:00-15:40", "B1 15:40-15:50", "L2 15:50-16:30", "OA 16:30-17:00",
                              "L3 17:00-17:40", "B3 17:40-17:50", "L4 17:50-18:30"]
    assert pure["saturday"] == ["L1 09:00", "B1 09:40", "L2 09:50"] and pure["sunday"] == 0
    assert pure["preview"]["days"] == ["mon", "tue", "wed", "thu", "fri", "sat"]
    assert [row["label"] for row in pure["preview"]["rows"]] == ["1. ders", "2. ders", "Öğle arası", "3. ders", "4. ders"]
    assert pure["preview"]["rows"][3]["cells"][5] == ""  # cumartesi 2 ders
    assert "Pazartesi dersleri gece yarısını geçiyor" in pure["problem"][0]
    assert pure["problem"][1].startswith("Ders süresi") and pure["problem"][2] == ""
    assert pure["removal"].startswith("3 ders 7.–8. saatlerde (8-A)")
    assert pure["draftRound"] == {"startTime": "15:00", "lessonMinutes": 40, "breakMinutes": 10, "lessonsPerDay": 4,
                                  "lunch": {"afterLesson": 2, "minutes": 30},
                                  "days": [{"dayOfWeek": "sat", "startTime": "09:00", "lessonsPerDay": 2, "off": False},
                                           {"dayOfWeek": "sun", "startTime": None, "lessonsPerDay": None, "off": True}]}


def test_serbest_saatli_ders(result: dict[str, Any]) -> None:
    """Kullanıcı 17.10: "her dersin saati free eklenecek" — güne ders eklerken saat aralığı girilir."""
    assert result["hoursButtons"] == 0
    assert result["timeError"] == "Bitiş saati başlangıçtan sonra olmalı."
    assert result["freeClosed"] is True
    last = result["puts"][-2] if result["puts"][-1]["week"] != result["weeks"]["W2"] else result["puts"][-1]
    assert last["week"] == result["weeks"]["W2"]
    assert last["body"]["cells"] == [{"dayOfWeek": "thu", "bellSlotId": "T1715-1840", "subjectId": "matematik",
                                      "teacherId": "t1", "room": "204"}]  # derslik bu sınıftaki matematikten önerilir


def test_satirlar_ders_saatlerinden_zil_baglantisi_yok(result: dict[str, Any]) -> None:
    heads = result["rowHeads"]
    assert heads[0] == "1." and all(not head.startswith("Teneffüs") for head in heads)
    src = (ROOT / "modules" / "bbd_akademi" / "ui" / "panel" / "lessons" / "timetable-calendar.js").read_text("utf-8")
    assert "bellsStale" not in src and "Zil saatleri" not in src and "zil verisi" not in src
    shell = (ROOT / "modules" / "bbd_akademi" / "ui" / "panel" / "lessons" / "timetable.js").read_text("utf-8")
    assert "timetable-bell-status" not in shell and "'bells'" not in shell
