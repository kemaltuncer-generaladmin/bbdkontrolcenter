"""Görev formları (iş 53 `koc-gorev-formlari`; Akademi README §14 madde 97).

Kullanıcı (15.10.2026): "kontrol merkezinde koç öğrenciye soru vs atarken sıkıntı çekiyor;
bir sürü alan var, gerekli alanlar belli olmuyor, tipler kayıyor."

- `task-form-logic.js` (saf): tür → görünen / zorunlu alanlar sunucuyla aynı (koç görevi
  `validate_input`, kurum ataması `_required_missing`), alan başına Türkçe hata, gövde YALNIZ
  seçili türün içeriğini taşır, hedef soru önerisi, sunucu hata alanı → form kutusu,
  "kaydet ve yeni ekle" taslağı, önizleme satırları;
- koç görevi penceresi (DOM taklidi): tür kartları + zorunlu yıldızlar; tür değişince form
  iskeleti AYNI kalır (aynı düğümler, aynı sıra — kayma yok) ve ortak alanlar korunur;
  satır içi doğrulama (istek gitmeden); konu araması; "Kaydet ve yeni ekle"; sunucu
  reddinde girilenler kaybolmaz; sürüm çakışmasında tek sessiz yeniden deneme; deneme /
  hata tekrarı / video gövdeleri; Enter ile kaydetme; erişilebilirlik denetimi.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
export default async function ({ load }) {
  const m = await load('./panels/bbd_akademi/shared/task-form-logic.js');
  const base = (extra) => m.emptyForm({ date: '2026-10-15', estimatedMinutes: 40, ...extra });
  const errs = (form, opts) => m.validate(form, opts).errors;
  return {
    types: [m.typesFor('coach'), m.typesFor('date'), m.typesFor('offset')],
    rules: Object.fromEntries(['question_solving', 'topic_study', 'resource_section', 'video', 'review', 'exam',
      'mistake_review', 'reading', 'free'].map((t) => [t, [m.rulesFor(t, 'coach').need, m.rulesFor(t, 'date').need]])),
    coachErrors: {
      qs: errs(base({ type: 'question_solving', targetQuestions: '' })),
      topic: errs(base({ type: 'topic_study' })),
      review: errs(base({ type: 'review' })),
      res: errs(base({ type: 'resource_section', pageFrom: '9', pageTo: '4' })),
      video: errs(base({ type: 'video' })),
      exam: errs(base({ type: 'exam' })),
      free: errs(base({ type: 'free' })),
      mistake: errs(base({ type: 'mistake_review', mistakeCount: '70' })),
      reading: errs(base({ type: 'reading' })),
      basics: errs(m.emptyForm({ type: 'free', title: 'x', estimatedMinutes: 3, startTime: '25:00', endTime: '' })),
      clock: errs(base({ type: 'free', title: 'x', startTime: '18:00', endTime: '17:00' })),
      ok: errs(base({ type: 'question_solving', targetQuestions: '25' })),
    },
    planErrors: {
      review: errs(base({ type: 'review', subjectId: 'MAT' }), { mode: 'date' }),
      res: errs(base({ type: 'resource_section', resourceId: 'r1' }), { mode: 'date' }),
      resPages: errs(base({ type: 'resource_section', resourceId: 'r1', pageFrom: 3, pageTo: 9 }), { mode: 'date' }),
      reading: errs(base({ type: 'reading' }), { mode: 'date' }),
      past: errs(base({ type: 'free', title: 'x', date: '2026-10-01' }), { mode: 'date', today: '2026-10-15' }),
      offset: errs(m.emptyForm({ type: 'free', title: 'x', dayOffset: 9 }), { mode: 'offset', duration: 7 }),
      patch: errs(base({ type: 'question_solving', targetQuestions: '' }), { mode: 'patch' }),
    },
    // Tür değişse de eski türün içeriği gönderilmez (kaynak seçip videoya geçen koç).
    stale: m.coachTaskBody(base({ type: 'question_solving', targetQuestions: '25', subjectId: 'MAT', topicId: 't1',
      resourceId: 'r1', sectionIds: ['s1'], pageFrom: '3', pageTo: '9', videos: [{ videoId: 'abcdefghijk' }] })).task,
    exam: m.coachTaskBody(base({ type: 'exam', examId: 'e1', subjectId: 'MAT', examName: 'TYT-3' })).task,
    mistake: m.coachTaskBody(base({ type: 'mistake_review', subjectId: 'MAT', mistakeCount: '12' })).task,
    reading: m.coachTaskBody(base({ type: 'reading', targetPages: '30', startTime: '17:00', endTime: '17:40' })).task,
    resource: m.coachTaskBody(base({ type: 'resource_section', resourceId: 'r1', resourceTitle: 'Limit', sectionIds: ['s1'],
      sectionTitles: ['Türev 1'], questionFrom: '1', questionTo: '20', addToShelf: true })).task,
    plan: [m.planTask(base({ type: 'review', subjectId: 'MAT', topicId: 't1', topicName: 'Türev', resourceId: 'r1' }), 'date'),
      m.planTask(m.emptyForm({ type: 'mistake_review', dayOffset: 3, subjectId: 'MAT', mistakeCount: 5, estimatedMinutes: 30 }),
        'offset')],
    suggest: [m.suggestQuestions(40), m.suggestQuestions(60), m.suggestQuestions(90), m.suggestQuestions(3),
      m.suggestQuestions(40, 35), m.minutesForVideo(612)],
    titles: [m.autoTitle(base({ type: 'question_solving', topicName: 'Türev', targetQuestions: 25 })),
      m.autoTitle(base({ type: 'resource_section', resourceTitle: 'Limit', sectionTitles: ['A', 'B', 'C'], pageFrom: 3, pageTo: 9 })),
      m.autoTitle(base({ type: 'mistake_review', subjectName: 'Matematik' }))],
    server: [m.serverErrors('fields_required_for_type', { fields: ['targetQuestions'], type: 'question_solving' }, {}),
      m.serverErrors('fields_required_for_type', { fields: ['sectionIds'] }, { resourceId: 'r1' }),
      m.serverErrors('fields_required_for_type', { fields: ['topicId'] }, { type: 'review' }),
      m.serverErrors('validation_error', [{ field: 'body.tasks[0].pageTo', message: 'Bitiş sayfası başlangıçtan küçük.' }], {}),
      m.serverErrors('date_in_past', { field: 'body.tasks[0].date' }, {})],
    next: m.nextForm(base({ type: 'question_solving', subjectId: 'MAT', subjectName: 'Matematik', topicId: 't1',
      title: 'Türev', targetQuestions: '25', instructions: 'x', priority: 'high' })),
    preview: m.previewLines({ studentCount: 28, taskCount: 3, instanceCount: 84, busyStudentCount: 5,
      days: [{ date: '2026-10-13', taskCount: 2, busyStudentCount: 5 }], holidayShifted: [{ from: '2026-10-29', to: '2026-10-30' }],
      holidaySkipped: [] }),
  };
}
"""


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_kurallar_sunucuyla_ayni_ve_govde_yalniz_secili_tur() -> None:
    out = _run(LOGIC)
    coach, date, offset = out["types"]
    assert coach["primary"] == ["question_solving", "topic_study", "resource_section", "video", "review", "exam"]
    assert coach["other"] == ["mistake_review", "reading", "free"]
    assert "video" not in date["primary"] and "homework" in date["other"]
    assert "exam" not in offset["primary"] and "homework" not in offset["other"]
    rules = out["rules"]
    # [koç görevi, kurum ataması] zorunluları — Akademi README §14 97 tablosu.
    assert rules["question_solving"] == [["questions"], ["questions"]]
    assert rules["topic_study"] == [["subject"], ["subject"]]
    assert rules["resource_section"] == [["resource"], ["resource", "sections"]]
    assert rules["video"] == [["video"], ["video"]]
    assert rules["review"] == [["subject"], ["topic"]]
    assert rules["exam"] == [["exam"], ["exam"]]
    assert rules["mistake_review"] == [["mistakeCount"], ["mistakeCount"]]
    assert rules["reading"] == [[], ["readPages"]]
    assert rules["free"] == [["title"], ["title"]]
    e = out["coachErrors"]
    assert e["qs"] == {"questions": "Hedef soru sayısını yazın (1–1000)."}
    assert e["topic"] == {"subject": "Ders seçin."} and e["review"] == {"subject": "Ders seçin."}
    assert e["res"] == {"resource": "Kaynak seçin.", "pageTo": "Son sayfa ilk sayfadan küçük olamaz."}
    assert e["video"] == {"video": "En az bir video ekleyin."} and e["exam"] == {"exam": "Deneme seçin."}
    assert e["free"] == {"title": "Serbest görev için başlık yazın."}
    assert e["mistake"] == {"mistakeCount": "Hata sayısı 1–50 olmalı."} and e["reading"] == {}
    assert e["basics"] == {"date": "Gün seçin.", "estimatedMinutes": "Süre 5–600 dakika olmalı.",
                           "startTime": "Saati 17:30 gibi yazın."}
    assert e["clock"] == {"endTime": "Bitiş saati başlangıçtan sonra olmalı."} and e["ok"] == {}
    p = out["planErrors"]
    assert p["review"] == {"subject": "Konu seçin."}
    assert p["res"] == {"resource": "Bölüm seçin ya da sayfa aralığını (ilk–son) yazın."} and p["resPages"] == {}
    assert p["reading"] == {"readPages": "Hedef sayfa sayısını yazın (1–2000)."}
    assert p["past"] == {"date": "Geçmiş bir güne görev konamaz."}
    assert p["offset"] == {"date": "Gün no 1–7 arasında olmalı."}
    assert p["patch"] == {"questions": "Hedef soru sayısını yazın (1–1000)."}
    stale = out["stale"]
    assert stale["type"] == "question_solving" and stale["targetQuestions"] == 25 and stale["subjectId"] == "MAT"
    for key in ("resourceId", "sectionIds", "pageFrom", "pageTo"):
        assert key not in stale, key
    assert "videos" not in stale["coach"] and stale["coach"]["draft"] is True
    assert stale["title"] == "25 soru"                          # konu adı yoksa yalnız sayı
    assert out["exam"]["examId"] == "e1" and "subjectId" not in out["exam"] and out["exam"]["title"] == "TYT-3"
    assert out["mistake"]["mistakeReview"] == {"subjectId": "MAT", "topicId": None, "count": 12}
    assert out["reading"]["targetPages"] == 30 and out["reading"]["startTime"] == "17:00"
    res = out["resource"]
    assert res["resourceId"] == "r1" and res["sectionIds"] == ["s1"] and res["coach"]["questionFrom"] == 1
    assert res["coach"]["addToShelf"] is True and res["title"] == "Limit · Türev 1"
    review, offset_task = out["plan"]
    assert review["resourceId"] is None and review["topicId"] == "t1" and review["title"] == "Türev — tekrar"
    assert offset_task["dayOffset"] == 2 and "mistakeReview" not in offset_task and offset_task["title"] == "Hata tekrarı"
    assert out["suggest"] == [25, 40, 60, None, 35, 16]
    assert out["titles"] == ["Türev — 25 soru", "Limit · A, B +1 · s. 3–9", "Hata tekrarı — Matematik"]
    qs, sections, topic, page, past = out["server"]
    assert qs == {"questions": "Hedef soru sayısını yazın (1–1000)."}
    assert sections == {"resource": "Bölüm seçin ya da sayfa aralığını (ilk–son) yazın."}
    assert topic == {"subject": "Konu seçin."}
    assert page == {"pageTo": "Bitiş sayfası başlangıçtan küçük."} and past == {"date": "Geçmiş bir güne görev konamaz."}
    nxt = out["next"]
    assert nxt["type"] == "question_solving" and nxt["subjectId"] == "MAT" and nxt["priority"] == "high"
    assert nxt["topicId"] == "" and nxt["title"] == "" and nxt["targetQuestions"] == "" and nxt["instructions"] == ""
    assert out["preview"] == [
        "28 öğrenciye 3 görev (toplam 84).",
        "Çakışma: 5 öğrencinin aynı gün zaten görevi var (2026-10-13: 5 öğrenci).",
        "Tatil: 2026-10-29 → 2026-10-30 gününe kayar.",
    ]


UI = r"""
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

export default async function ({ document, audit, sleep, api, load }) {
  const student = { id: 'stu-1', externalRef: 'STU-1', displayName: 'Elif Kaya', firstName: 'Elif', lastName: 'Kaya',
    classGroup: { id: 'c1', name: '11-A' }, status: 'active' };
  const day = (date, dow, extra = {}) => ({ date, dayOfWeek: dow, holiday: null, lessons: [], studyHours: [], tasks: [],
    plannedMinutes: 0, plannedQuestions: 0, capacityMinutes: 180, overCapacity: false, ...extra });
  const week = (version) => ({ student, weekStart: '2026-10-12', published: false, publishedAt: null, draftCount: 0, version,
    subjectMinutes: [], days: [day('2026-10-12', 'mon'), day('2026-10-13', 'tue'), day('2026-10-14', 'wed'),
      day('2026-10-15', 'thu'), day('2026-10-16', 'fri'), day('2026-10-17', 'sat', { holiday: 'Etkinlik' }), day('2026-10-18', 'sun')] });
  let version = 3;
  const puts = [];
  let nextPut = null;
  routes['GET /v1/admin/students/stu-1/coach-week'] = () => week(version);
  routes['PUT /v1/admin/students/stu-1/coach-week'] = (body) => {
    puts.push(body);
    if (nextPut) { const value = nextPut(body); nextPut = null; if (value) return value; }
    if (body.expectedVersion !== version) return { __error: { code: 'version_conflict', message: 'Sürüm çakışması' }, status: 409 };
    version += 1;
    return { week: week(version), results: [{ clientKey: body.ops[0].clientKey, taskId: `t${version}`, result: 'created',
      errorCode: null, warnings: [] }] };
  };
  routes['GET /v1/admin/students/stu-1/task-builder'] = { student, shelf: [{ resourceId: 'r1', title: 'Limit Yayınları',
    subject: { id: 'MAT', name: 'Matematik' }, progressPercent: 40, nextSection: { id: 's2', title: 'Türev 2', pageFrom: 21,
    pageTo: 30, questionCount: 35 } }], weakTopics: [{ topic: { id: 'top-turev', name: 'Türev kuralları', subjectId: 'MAT' },
    accuracyPercent: 45 }], dueReviews: [{ id: 'top-limit', name: 'Limit', subjectId: 'MAT' }], recentCombos: [] };
  routes['GET /v1/admin/students/stu-1/resources/r1/sections'] = { resourceId: 'r1', title: 'Limit Yayınları', onShelf: true,
    sections: [{ id: 's1', parentId: null, kind: 'section', title: 'Türev 1', pageFrom: 11, pageTo: 20, questionCount: 30,
      topicIds: [], done: true, accuracyPercent: 70 }, { id: 's2', parentId: null, kind: 'section', title: 'Türev 2', pageFrom: 21,
      pageTo: 30, questionCount: 35, topicIds: [], done: false, accuracyPercent: null }] };
  routes['GET /v1/students/stu-1/curriculum'] = { resolvedAt: '2026-10-15T08:00:00Z', gradeLevel: 11, track: 'SAY',
    examTarget: 'YKS', programs: [{ program: { id: 'p-mat-11', kind: 'school', subject: { id: 'MAT', name: 'Matematik' },
      title: 'Matematik', grade: 11, curriculumVersion: 'meb', scope: 'national', levelKey: '11', levelLabel: '11. sınıf' },
      source: 'rule', historical: false, completionPercent: 20, heat: [0, 0, 0, 0, 0], units: [{ id: 'u-turev', title: 'Türev',
        order: 1, completionPercent: 10, topics: [{ topicId: 'top-turev', title: 'Türev kuralları', order: 1, status: 'started',
          solvedCount: 0, correctCount: 0, wrongCount: 0, accuracyPercent: 45, lastStudiedAt: null, nextReviewAt: null, heatLevel: 1 },
        { topicId: 'top-uslu', title: 'Üslü ifadelerin türevi', order: 2, status: 'not_started', solvedCount: 0, correctCount: 0,
          wrongCount: 0, accuracyPercent: null, lastStudiedAt: null, nextReviewAt: null, heatLevel: 0 }] }] }] };
  routes['GET /v1/admin/exams'] = { items: [{ id: 'e1', name: 'TYT Deneme 3', date: '2026-10-18' }], nextCursor: null };
  routes['POST /v1/admin/videos/resolve'] = { video: { videoId: 'abcdefghijk', title: 'Türev anlatımı', channelTitle: 'Hoca',
    durationSeconds: 612, embeddable: true, available: true }, startSeconds: null, playlistId: null, warnings: [] };

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
    lookups: { classGroups: async () => [], studentGroups: async () => [], terms: async () => [],
      subjects: async () => [{ id: 'MAT', name: 'Matematik' }, { id: 'FIZ', name: 'Fizik' }] } };
  const { section } = await load('./panels/bbd_akademi/coaching.js');
  const view = section(app);
  root.append(view.node);
  view.shown({ coachingTab: 'week', student });
  await sleep(100);
  const out = {};
  const putsOf = () => puts.map((body) => body.ops[0]);

  click(byText(root, 'button', 'Görev ekle'));
  await sleep(80);
  let dialog = root.querySelector('.ak-tf-dialog');
  out.title = dialog.querySelector('.kit-dialog-title').textContent;
  out.cards = dialog.querySelectorAll('.ak-tf-card b').map((node) => node.textContent);
  out.others = dialog.querySelectorAll('.ak-tf-other').map((node) => node.textContent);
  out.checked = dialog.querySelector('[role="radio"][aria-checked="true"]')?.dataset.type;
  out.quick = dialog.querySelectorAll('.ak-tf-quick-chip').map((node) => node.textContent);
  const required = () => dialog.querySelectorAll('.ak-tf-field').filter((node) => !node.querySelector('.req').hidden
    && node.isConnected).map((node) => node.dataset.field);
  out.requiredQs = required();
  out.autoQuestions = dialog.querySelector('[aria-label="Hedef soru sayısı"]').value;
  out.dayChecked = dialog.querySelector('.ak-tf-day[aria-checked="true"]')?.textContent;
  out.a11y = audit(dialog);

  // İskelet sabit: tür değişince üst düzey bloklar ve "Daha fazla" düğmesi aynı düğüm, aynı sıra.
  const form = dialog.querySelector('.ak-tf');
  const skeleton = () => [...form.children].map((node) => node.className.split(' ')[0]).join('|');
  const area = form.querySelector('.ak-tf-area');
  const moreToggle = form.querySelector('.ak-tf-more-toggle');
  const before = skeleton();
  click(byText(dialog, 'button', '60 dk'));
  click(dialog.querySelectorAll('.ak-tf-day').find((node) => node.textContent.includes('16.10')));
  choose(dialog.querySelector('select[aria-label="Ders"]'), 'FIZ');
  const visits = {};
  for (const key of ['resource_section', 'video', 'exam', 'review', 'mistake_review', 'reading', 'free', 'topic_study',
    'question_solving']) {
    click(dialog.querySelector(`[data-type="${key}"]`));
    await sleep(5);
    visits[key] = { same: skeleton() === before && form.querySelector('.ak-tf-area') === area
      && form.querySelector('.ak-tf-more-toggle') === moreToggle,
      required: required(), fields: [...area.querySelectorAll('.ak-tf-field')].map((node) => node.dataset.field) };
  }
  out.visits = visits;
  out.kept = { minutes: dialog.querySelector('[aria-label="Süre (dakika)"]').value,
    day: dialog.querySelector('.ak-tf-day[aria-checked="true"]')?.textContent,
    subject: dialog.querySelector('select[aria-label="Ders"]').value };

  // Satır içi doğrulama: konu çalışmada ders yok → istek gitmez, hata dersin altında.
  click(dialog.querySelector('[data-type="topic_study"]'));
  choose(dialog.querySelector('select[aria-label="Ders"]'), '');
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(20);
  out.inline = { error: dialog.querySelector('[data-field="subject"] .ak-tf-error').textContent,
    summary: dialog.querySelector('.kit-dialog-error').textContent, puts: puts.length,
    invalid: dialog.querySelector('select[aria-label="Ders"]').getAttribute('aria-invalid') };
  // Konu araması: "türev" → öğrencinin müfredatından; seçince ders de dolar, hata kalkar.
  const search = dialog.querySelector('input[aria-label="Konu ara"]');
  search.dispatchEvent(new Event('focus'));
  type(search, 'türev kur');
  await sleep(220);
  out.results = dialog.querySelectorAll('.ak-tf-results .ak-tf-result b').map((node) => node.textContent);
  click(dialog.querySelector('.ak-tf-results .ak-tf-result'));
  await sleep(10);
  out.afterPick = { subject: dialog.querySelector('select[aria-label="Ders"]').value,
    chip: dialog.querySelector('.ak-tf-topic-chip')?.textContent,
    error: dialog.querySelector('[data-field="subject"] .ak-tf-error').textContent };

  // Kaydet ve yeni ekle: istek pencere açıkken gider, pencere kalır, ortak alanlar korunur.
  click(byText(dialog, 'button', 'Kaydet ve yeni ekle'));
  await sleep(80);
  dialog = root.querySelector('.ak-tf-dialog');
  out.firstSave = putsOf()[0];
  out.stillOpen = Boolean(dialog);
  out.savedLine = dialog.querySelector('.ak-tf-saved')?.textContent || '';
  out.afterNew = { type: dialog.querySelector('[role="radio"][aria-checked="true"]')?.dataset.type,
    subject: dialog.querySelector('select[aria-label="Ders"]').value, chip: dialog.querySelector('.ak-tf-topic-chip')?.textContent || '',
    day: dialog.querySelector('.ak-tf-day[aria-checked="true"]')?.textContent,
    minutes: dialog.querySelector('[aria-label="Süre (dakika)"]').value };

  // Sunucu reddi (eksik alan) → alanın altına yazılır, girilenler kalır.
  click(dialog.querySelector('[data-type="question_solving"]'));
  nextPut = () => ({ __error: { code: 'fields_required_for_type', message: 'Bu görev türü için gerekli alanlar eksik.',
    details: { fields: ['targetQuestions'], type: 'question_solving', index: 0 } } });
  const questions = dialog.querySelector('[aria-label="Hedef soru sayısı"]');
  type(questions, '30');
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(80);
  out.rejected = { open: Boolean(root.querySelector('.ak-tf-dialog')),
    error: dialog.querySelector('[data-field="questions"] .ak-tf-error').textContent,
    summary: dialog.querySelector('.kit-dialog-error').textContent, value: questions.value };

  // Sürüm çakışması: hafta sessizce yenilenir, aynı görev bir kez daha gönderilir.
  version += 1;   // başka biri değiştirdi
  const countBefore = puts.length;
  // Enter ile kaydet (süre kutusunda).
  dialog.querySelector('[aria-label="Süre (dakika)"]').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await sleep(120);
  out.conflict = { tries: puts.length - countBefore, closed: !root.querySelector('.ak-tf-dialog'),
    versions: puts.slice(countBefore).map((body) => body.expectedVersion), op: putsOf().pop() };

  // Deneme, hata tekrarı, video gövdeleri.
  const openNew = async () => { click(byText(root, 'button', 'Görev ekle')); await sleep(80); return root.querySelector('.ak-tf-dialog'); };
  dialog = await openNew();
  out.remembered = dialog.querySelector('[role="radio"][aria-checked="true"]')?.dataset.type;
  click(dialog.querySelector('[data-type="exam"]'));
  await sleep(20);
  choose(dialog.querySelector('select[aria-label="Deneme"]'), 'e1');
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(80);
  out.exam = putsOf().pop();
  dialog = await openNew();
  click(dialog.querySelector('[data-type="mistake_review"]'));
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(80);
  out.mistake = putsOf().pop();
  dialog = await openNew();
  click(dialog.querySelector('[data-type="video"]'));
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(20);
  out.videoError = dialog.querySelector('[data-field="video"] .ak-tf-error').textContent;
  const link = dialog.querySelector('input[aria-label="YouTube bağlantısı"]');
  link.value = 'https://youtu.be/abcdefghijk';
  link.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(60);
  out.videoMinutes = dialog.querySelector('[aria-label="Süre (dakika)"]').value;
  out.videoErrorAfter = dialog.querySelector('[data-field="video"] .ak-tf-error').textContent;
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(80);
  out.video = putsOf().pop();
  // Hızlı seçim: raftaki sıradaki bölüm → kaynak türü + bölüm seçili, öneri bölümden.
  dialog = await openNew();
  click(dialog.querySelectorAll('.ak-tf-quick-chip').find((node) => node.textContent.startsWith('Sıradaki')));
  await sleep(60);
  out.quickPick = { type: dialog.querySelector('[role="radio"][aria-checked="true"]')?.dataset.type,
    sections: dialog.querySelectorAll('.ak-tf-sections .kit-chip.on').map((node) => node.textContent),
    pageHint: dialog.querySelector('[data-field="pageFrom"] .ak-tf-hint').textContent,
    suggest: dialog.querySelector('.ak-tf-suggest')?.textContent };
  out.a11yResource = audit(dialog);
  click(byText(dialog, 'button', 'Ekle'));
  await sleep(80);
  out.resource = putsOf().pop();
  out.toasts = toasts.map(([text]) => text);
  return out;
}
"""


def test_koc_gorev_penceresi_kaymaz_dogrular_kaybetmez() -> None:
    out = _run(UI)
    assert out["title"] == "Koç görevi ekle"
    assert out["cards"] == ["Soru çözümü", "Konu çalışma", "Kaynak", "Video", "Tekrar", "Deneme"]
    assert out["others"] == ["Hata tekrarı", "Okuma", "Serbest görev"]
    assert out["checked"] == "question_solving"
    assert out["quick"] == ["Sıradaki: Limit Yayınları › Türev 2", "Zayıf: Türev kuralları (%45)", "Tekrar zamanı: Limit"]
    assert out["requiredQs"] == ["date", "estimatedMinutes", "questions"]
    assert out["autoQuestions"] == "25"                         # 40 dk → öneri hazır, tek tıkla kaydedilir
    assert out["dayChecked"].startswith("Pzt") or out["dayChecked"]
    assert out["a11y"] == []
    for key, visit in out["visits"].items():
        assert visit["same"], key                               # iskelet ve düğümler aynı: kayma yok
    v = out["visits"]
    assert v["resource_section"]["fields"] == ["resource", "questions", "pageFrom", "questionFrom"]
    assert v["video"]["fields"] == ["video", "subject"] and "video" in v["video"]["required"]
    assert v["exam"]["fields"] == ["exam"] and "exam" in v["exam"]["required"]
    assert v["free"]["fields"] == ["title"] and "title" in v["free"]["required"]
    assert "subject" in v["topic_study"]["required"] and "questions" in v["question_solving"]["required"]
    assert out["kept"] == {"minutes": "60", "day": out["kept"]["day"], "subject": "FIZ"}
    assert "16.10" in out["kept"]["day"]
    inline = out["inline"]
    assert inline["error"] == "Ders seçin." and inline["puts"] == 0 and inline["invalid"] == "true"
    assert inline["summary"] == "İşaretli alanı düzeltin."
    assert out["results"][0] == "Türev kuralları"
    assert out["afterPick"] == {"subject": "MAT", "chip": "Konu: Türev kuralları ✕", "error": ""}
    first = out["firstSave"]
    assert first["op"] == "create" and first["task"]["type"] == "topic_study" and first["task"]["date"] == "2026-10-16"
    assert first["task"]["estimatedMinutes"] == 60 and first["task"]["topicId"] == "top-turev"
    assert first["task"]["title"] == "Türev kuralları — konu çalışma" and first["task"]["coach"]["draft"] is True
    assert out["stillOpen"] and "eklendi" in out["savedLine"] and "Sıradaki görevi girin" in out["savedLine"]
    assert out["afterNew"]["type"] == "topic_study" and out["afterNew"]["subject"] == "MAT" and out["afterNew"]["chip"] == ""
    assert out["afterNew"]["minutes"] == "60" and "16.10" in out["afterNew"]["day"]
    rejected = out["rejected"]
    assert rejected["open"] and rejected["value"] == "30"
    assert rejected["error"] == "Hedef soru sayısını yazın (1–1000)."
    assert rejected["summary"] == "İşaretli alanları düzeltin; girdikleriniz duruyor."
    conflict = out["conflict"]
    assert conflict["tries"] == 2 and conflict["closed"] and conflict["versions"][1] == conflict["versions"][0] + 1
    assert conflict["op"]["task"]["targetQuestions"] == 30 and conflict["op"]["task"]["type"] == "question_solving"
    assert out["remembered"] == "question_solving"              # son kullanılan tür
    assert out["exam"]["task"]["type"] == "exam" and out["exam"]["task"]["examId"] == "e1"
    assert out["exam"]["task"]["title"] == "TYT Deneme 3" and "subjectId" not in out["exam"]["task"]
    assert out["mistake"]["task"]["mistakeReview"]["count"] == 10
    assert out["videoError"] == "En az bir video ekleyin." and out["videoErrorAfter"] == ""
    assert out["videoMinutes"] == "16"                          # 10:12 video → 11 + 5 dk
    video = out["video"]["task"]
    assert video["type"] == "video" and video["coach"]["videos"][0]["videoId"] == "abcdefghijk"
    assert video["title"] == "Türev anlatımı" and video["estimatedMinutes"] == 16
    quick = out["quickPick"]
    assert quick["type"] == "resource_section" and quick["sections"] == ["Türev 2 (s. 21–30) · 35 soru"]
    assert quick["pageHint"].startswith("Bölümden: s. 21–30") and quick["suggest"] in ("Öneri: 35", "")
    assert out["a11yResource"] == []
    resource = out["resource"]["task"]
    assert resource["resourceId"] == "r1" and resource["sectionIds"] == ["s2"] and resource["title"] == "Limit Yayınları · Türev 2"
    assert any(text.startswith("Görev taslak olarak eklendi") for text in out["toasts"])


PLAN = r"""
const routes = {};
const sent = [];
globalThis.__apiHook = (method, path, options) => {
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: true, status: 200, data: { items: [], nextCursor: null }, headers: {} };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  if (value && value.__error) return { ok: false, status: value.status || 422, error: value.__error };
  return { ok: true, status: 200, data: value, headers: {} };
};
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const choose = (node, value) => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); };

export default async function ({ document, audit, sleep, api, load }) {
  routes['GET /v1/admin/curricula/programs'] = { items: [{ id: 'p-mat-11', kind: 'school', subject: { id: 'MAT', name: 'Matematik' },
    title: 'Matematik', grade: 11, levelKey: '11', levelLabel: '11. sınıf', levelOrder: 110 }] };
  routes['GET /v1/admin/curricula/programs/p-mat-11'] = { id: 'p-mat-11', units: [{ id: 'u-turev', title: 'Türev', order: 1,
    topics: [{ id: 'top-turev', title: 'Türev kuralları', order: 1 }] }] };
  routes['POST /v1/admin/plan/assignments/preview'] = { studentCount: 28, taskCount: 2, instanceCount: 56, students: [],
    days: [{ date: '2026-10-16', taskCount: 2, busyStudentCount: 4 }], holidayShifted: [], holidaySkipped: [], busyStudentCount: 4 };
  routes['POST /v1/admin/plan/assignments'] = (body) => ({ id: 'a1', title: null, target: body.target, studentCount: 28,
    tasks: body.tasks.map((task, i) => ({ ...task, id: `d${i}` })), templateId: null, createdBy: 'KM', createdAt: '2026-10-15T10:00:00Z',
    progress: { taskInstances: 56, done: 0, partial: 0, skipped: 0, planned: 56 } });
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toast = toaster(root);
  const app = { root, api: client, status: null, toast: (text, tone) => toast(text, tone),
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { classGroups: async () => [{ id: 'c1', name: '11-A' }], studentGroups: async () => [], terms: async () => [],
      subjects: async () => [{ id: 'MAT', name: 'Matematik' }] } };
  const { section } = await load('./panels/bbd_akademi/plan.js');
  const view = section(app);
  root.append(view.node);
  view.shown?.();
  await sleep(60);
  const out = {};
  click(byText(root, 'button', 'Yeni atama'));
  await sleep(60);
  const dialog = root.querySelector('.kit-dialog');
  click(byText(dialog, 'button', '11-A'));
  click(byText(dialog, 'button', '+ Görev ekle'));
  await sleep(30);
  const editor = () => dialog.querySelector('.ak-plan-editor');
  out.cards = editor().querySelectorAll('.ak-tf-card b').map((node) => node.textContent);
  out.others = editor().querySelectorAll('.ak-tf-other').map((node) => node.textContent);
  click(editor().querySelector('[data-type="review"]'));
  choose(editor().querySelector('select[aria-label="Ders"]'), 'MAT');
  click(byText(editor(), 'button', 'Listeye ekle'));
  await sleep(10);
  out.reviewError = editor().querySelector('[data-field="subject"] .ak-tf-error').textContent;
  out.reviewStar = !editor().querySelector('[data-field="subject"] .req').hidden;
  const search = editor().querySelector('input[aria-label="Konu ara"]');
  type(search, 'kural');
  await sleep(250);
  out.results = editor().querySelectorAll('.ak-tf-result b').map((node) => node.textContent);
  click(editor().querySelector('.ak-tf-result'));
  click(byText(editor(), 'button', 'Ekle ve yeni'));
  await sleep(20);
  out.afterAgain = { open: Boolean(editor()), type: editor()?.querySelector('[role="radio"][aria-checked="true"]')?.dataset.type,
    subject: editor()?.querySelector('select[aria-label="Ders"]').value };
  click(editor().querySelector('[data-type="question_solving"]'));
  click(byText(editor(), 'button', 'Listeye ekle'));
  await sleep(20);
  out.rows = dialog.querySelectorAll('.kit-row, [role="row"]').length;
  out.a11y = audit(dialog);
  click(byText(dialog, 'button', 'Atamayı oluştur'));
  await sleep(60);
  out.afterFirst = { open: Boolean(root.querySelector('.kit-dialog')), preview: dialog.querySelector('.ak-tf-preview').textContent,
    error: dialog.querySelector('.kit-dialog-error').textContent,
    posts: sent.filter((c) => c.method === 'POST').map((c) => c.path) };
  click(byText(dialog, 'button', 'Atamayı oluştur'));
  await sleep(80);
  out.posts = sent.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]);
  out.closed = !root.querySelector('.kit-dialog .ak-tf-preview');
  return out;
}
"""


def test_plan_atamasi_ortak_form_ve_onizleme() -> None:
    out = _run(PLAN)
    assert out["cards"] == ["Soru çözümü", "Konu çalışma", "Kaynak", "Tekrar", "Deneme"]   # video yalnız koç görevi
    assert out["others"] == ["Hata tekrarı", "Okuma", "Ödev", "Serbest görev"]
    assert out["reviewError"] == "Konu seçin." and out["reviewStar"]          # kurum ataması: tekrar → konu
    assert out["results"][0] == "Türev kuralları"
    assert out["afterAgain"] == {"open": True, "type": "review", "subject": "MAT"}
    assert out["a11y"] == []
    first = out["afterFirst"]
    assert first["open"] and first["posts"] == ["/v1/admin/plan/assignments/preview"]
    assert "28 öğrenciye 2 görev" in first["preview"] and "Çakışma: 4 öğrencinin" in first["preview"]
    assert first["error"].startswith("Önizleme hazır: 28 öğrenci, 4 çakışma")
    (_, preview), (path, body) = out["posts"]
    assert path == "/v1/admin/plan/assignments" and preview["tasks"] == body["tasks"]
    review, questions = body["tasks"] if body["tasks"][0]["type"] == "review" else body["tasks"][::-1]
    assert review["topicId"] == "top-turev" and review["subjectId"] == "MAT" and review["title"] == "Türev kuralları — tekrar"
    assert questions["type"] == "question_solving" and questions["targetQuestions"] == 25 and questions["subjectId"] == "MAT"
    assert body["target"] == {"classGroupIds": ["c1"], "studentGroupIds": [], "studentIds": []}
    assert out["closed"]
