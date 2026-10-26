"""BBD Akademi 78 — deneme sonucunda tam ayrıntılı analiz + "Karne (PDF)" (Node'da davranış).

Kullanıcı (26.10.2026): "tüm yanlışlar olsun ayrıntılı bir analiz istiyoruz sadece ilk 10 konu fln değil tam
ayrıntılı. mobil uygulamaya da buton ekleyelim bizim km den aldığımız gibi öğrenci direkt belge de alabilsin"
(Akademi README §14 madde 138).

- Tek istek (`students_getExamResultAnalysis`); süzgeç "Yanlış ve boş" varsayılan (önce yanlışlar, sonra boşlar) ·
  "Yanlış" · "Boş" · "Hepsi" ve ders seçimi istemcide (yeni istek yok).
- Satır "{bölüm} · {no}. soru" (+ "A kitapçığında n"), "Cevap: C · Doğru: A", konu / "Kazanım: …", "Konu eşlenmemiş".
- "Konu analizi": BÜTÜN konular ders ders, başlık "Ders · D · Y · B · net", "Zayıf" rozeti, sınıf / kurum %.
- `counts_only` → "Bu denemede soru ayrıntısı yok"; konu yoksa "Konu analizi yok".
- "Karne (PDF)": `students_getExamReportCard` → imzalı bağlantı → `fetchFile` → PDF açılır.
- Saf mantık (`exams-analysis-logic.js`) ayrıca.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

ANALYSIS = r"""
  const S1 = 'sec-mat', S2 = 'sec-fen';
  const MAT = { id: 'matematik', name: 'Matematik', shortName: 'MAT' };
  const FIZ = { id: 'fizik', name: 'Fizik', shortName: 'FİZ' };
  const topic = (id, name, subjectId, level = 'topic') => ({ id, name, subjectId, level, unitId: null });
  const q = (sectionId, questionNo, result, given, key, groupKey, topics = [], outcomes = [], questionNoA = questionNo) => ({
    sectionId, questionNo, questionNoA, globalNo: questionNo, groupKey, result, given, key, topics, outcomes,
    outcomeCode: null });
  const ANALYSIS = {
    exam: { id: 'E1', name: 'FULL AYT 2', type: 'ayt_say', publisher: null, date: '2026-10-26', isHistorical: false,
      deliveryMode: 'app', questionTotal: 6 },
    studentId: 'S1', absent: false, detail: 'questions', bookletType: 'B', wrongsPerCorrect: 4, weakErrorRate: 0.5,
    totals: { questionCount: 6, correct: 2, wrong: 2, blank: 2, cancelled: 1, net: 1.5 },
    sections: [{ id: S1, title: 'Matematik', subject: MAT, questionCount: 4, firstGlobalNo: 1 },
      { id: S2, title: 'Fen Bilimleri', subject: null, questionCount: 2, firstGlobalNo: 5 }],
    subjects: [
      { key: 'matematik', title: 'Matematik', subject: MAT, sectionIds: [S1], questionCount: 4, correct: 2, wrong: 1,
        blank: 1, cancelled: 1, net: 1.75 },
      { key: 'fizik', title: 'Fizik', subject: FIZ, sectionIds: [S2], questionCount: 2, correct: 0, wrong: 1, blank: 1,
        cancelled: 0, net: -0.25 }],
    questions: [
      q(S1, 1, 'cancelled', null, null, 'matematik', [topic('t-turev', 'Türev', 'matematik')]),
      q(S1, 2, 'blank', null, 'B', 'matematik', [topic('t-turev', 'Türev', 'matematik')]),
      q(S1, 3, 'wrong', 'C', 'A', 'matematik', [topic('t-limit', 'Limit', 'matematik')], ['Limiti hesaplar.'], 7),
      q(S1, 4, 'correct', 'D', 'D', 'matematik'),
      q(S2, 1, 'blank', null, 'E', 'fizik', [topic('t-elek', 'Elektrik (ünite)', 'fizik', 'unit')]),
      q(S2, 2, 'wrong', 'A', 'B', 'fizik'),
    ],
    topics: [
      { topic: { id: 't-turev', name: 'Türev', subjectId: 'matematik' }, level: 'topic', unitId: null,
        groupKey: 'matematik', questionCount: 2, correct: 1, wrong: 0, blank: 1, successRate: 0.5, errorRate: 0.5,
        classSuccessRate: 0.62, institutionSuccessRate: null, weak: true, inReviewQueue: false,
        questionRefs: [{ sectionId: S1, questionNo: 1 }, { sectionId: S1, questionNo: 2 }] },
      { topic: { id: 't-limit', name: 'Limit', subjectId: 'matematik' }, level: 'topic', unitId: null,
        groupKey: 'matematik', questionCount: 1, correct: 0, wrong: 1, blank: 0, successRate: 0, errorRate: 1,
        classSuccessRate: null, institutionSuccessRate: 0.4, weak: true, inReviewQueue: false, questionRefs: [] },
      { topic: { id: 't-elek', name: 'Elektrik (ünite)', subjectId: 'fizik' }, level: 'unit', unitId: 'u1',
        groupKey: 'fizik', questionCount: 1, correct: 0, wrong: 0, blank: 1, successRate: 0, errorRate: 1,
        classSuccessRate: null, institutionSuccessRate: null, weak: true, inReviewQueue: false, questionRefs: [] },
    ],
    unmatchedQuestionCount: 2,
  };
"""

APP = r"""
  const gets = [];
  const fetched = [];
  const toasts = [];
  const routes = {};
  const app = {
    root: document.body,
    can: (id) => !(globalThis.__deny || []).includes(id),
    toast: (text, tone) => toasts.push([text, tone]),
    api: {
      can: (id) => !(globalThis.__deny || []).includes(id),
      featureOff: () => false,
      get: async (path, query = {}) => {
        gets.push(path);
        for (const [key, value] of Object.entries(routes)) {
          if (path.endsWith(key)) return typeof value === 'function' ? value(query || {}) : JSON.parse(JSON.stringify(value));
        }
        throw Object.assign(new Error('yok'), { status: 404 });
      },
      fetchFile: async (url, name) => { fetched.push([url, name]); return { filename: name, blob: new Blob(['%PDF'], { type: 'application/pdf' }) }; },
    },
  };
  globalThis.URL.createObjectURL = globalThis.URL.createObjectURL || (() => 'blob:x');
  globalThis.open = () => null;
"""

SCENARIO = r"""
export default async function ({ document, audit, sleep, load }) {
""" + APP + ANALYSIS + r"""
  routes['/exams/E1/analysis'] = ANALYSIS;
  routes['/exams/E1/report-card'] = { url: 'https://r2.example/k.pdf?sig=1', expiresAt: '2026-10-26T15:00:00+03:00',
    fileName: 'full-ayt-2-karne-elif-kaya.pdf', contentType: 'application/pdf', sizeBytes: 9000 };
  const { examAnalysisSection, reportCardButton } = await load('./panels/bbd_akademi/exams/exams-analysis.js');
  const root = document.createElement('div');
  document.body.append(root);
  const section = examAnalysisSection(app, { studentId: 'S1', examId: 'E1' });
  root.append(section.node);
  await section.reload();
  await sleep(20);
  const out = {};
  const chips = () => root.querySelectorAll('.kit-chips .kit-chip').map((chip) => chip.textContent);
  const rows = () => root.querySelectorAll('.ak-ex-analysis .kit-card')[0].querySelectorAll('.kit-row:not(.kit-head)')
    .map((row) => row.textContent);
  out.chips = chips();
  out.cards = root.querySelectorAll('.kit-card-title').map((node) => node.textContent);
  out.defaultRows = rows();
  out.violations = audit(root);
  const click = (label) => root.querySelectorAll('.kit-chips .kit-chip').find((chip) => chip.textContent.startsWith(label)).click();
  click('Hepsi');
  await sleep(5);
  out.allRows = rows().length;
  click('Hepsi');  // aynı çipe ikinci kez basmak seçimi KALDIRMAZ
  await sleep(5);
  out.stillAll = rows().length;
  click('Boş');
  await sleep(5);
  out.blankRows = rows();
  click('Fizik');
  await sleep(5);
  out.fizikBlank = rows();
  out.topicHeads = root.querySelectorAll('.ak-ex-topic-head').map((node) => node.textContent);
  click('Tüm dersler');
  await sleep(5);
  out.topicHeadsAll = root.querySelectorAll('.ak-ex-topic-head').map((node) => node.textContent);
  out.topicRows = root.querySelectorAll('.ak-ex-topic-group .kit-row:not(.kit-head)').map((row) => row.textContent);
  out.note = root.querySelector('.ak-ex-note')?.textContent;
  out.analysisCalls = gets.filter((p) => p.endsWith('/analysis')).length;

  const btn = reportCardButton(app, { studentId: 'S1', examId: 'E1' });
  root.append(btn);
  btn.click();
  await sleep(20);
  out.button = btn.textContent;
  out.fetched = fetched;
  out.reportCalls = gets.filter((p) => p === '/v1/students/S1/exams/E1/report-card').length;

  // counts_only + konu yok
  routes['/exams/E2/analysis'] = { ...ANALYSIS, detail: 'counts_only', questions: [], topics: [],
    unmatchedQuestionCount: 0 };
  const other = examAnalysisSection(app, { studentId: 'S1', examId: 'E2' });
  const root2 = document.createElement('div');
  document.body.append(root2);
  root2.append(other.node);
  await other.reload();
  await sleep(10);
  out.countsOnly = root2.textContent;
  out.countsOnlyFilters = root2.querySelectorAll('.kit-chip').map((chip) => chip.textContent);

  // yetkisiz: karne düğmesi kapalı
  globalThis.__deny = ['students_getExamReportCard'];
  out.blocked = reportCardButton(app, { studentId: 'S1', examId: 'E1' }).disabled;
  globalThis.__deny = [];
  return out;
}
"""

LOGIC = r"""
export default async function ({ load }) {
""" + ANALYSIS + r"""
  const L = await load('./panels/bbd_akademi/exams/exams-analysis-logic.js');
  const sections = new Map(ANALYSIS.sections.map((s) => [s.id, s]));
  return {
    def: L.filterQuestions(ANALYSIS.questions).map((x) => `${x.sectionId}:${x.questionNo}`),
    wrong: L.filterQuestions(ANALYSIS.questions, 'wrong', 'matematik').map((x) => x.questionNo),
    counts: L.filterCounts(ANALYSIS.questions),
    countsFizik: L.filterCounts(ANALYSIS.questions, 'fizik'),
    titleB: L.questionTitle(ANALYSIS.questions[2], sections, 'B'),
    titleA: L.questionTitle(ANALYSIS.questions[2], sections, 'A'),
    answer: L.answerText(ANALYSIS.questions[2]),
    blankAnswer: L.answerText(ANALYSIS.questions[1]),
    cancelled: L.answerText(ANALYSIS.questions[0]),
    unknown: L.resultOf({ result: 'yeni_durum' }),
    topic: L.topicText(ANALYSIS.questions[3]),
    outcome: L.outcomeText(ANALYSIS.questions[2]),
    pct: [L.ratePct(0.6667), L.ratePct(null), L.ratePct(0)],
    heading: L.subjectHeading(ANALYSIS.subjects[1]),
    groups: L.topicGroups(ANALYSIS).map((g) => [g.subject.key, g.topics.length]),
    options: L.subjectOptions(ANALYSIS).map((o) => o.label),
    single: L.subjectOptions({ ...ANALYSIS, subjects: [ANALYSIS.subjects[0]], topics: [] }),
    unmatched: L.unmatchedText(3),
  };
}
"""


def _run(script: str) -> Any:
    try:
        data = run(script, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node yok")
    assert data["errors"] == []
    return data["result"]


def test_analysis_section_behaviour() -> None:
    out = _run(SCENARIO)
    assert out["chips"] == ["Tüm dersler", "Matematik", "Fizik", "Yanlış ve boş4", "Yanlış2", "Boş2", "Hepsi6"]
    assert out["cards"] == ["Soru soru analiz", "Konu analizi"]
    # varsayılan: önce yanlışlar (Matematik 3, Fen 2), sonra boşlar (Matematik 2, Fen 1)
    rows = out["defaultRows"]
    assert len(rows) == 4
    assert rows[0].startswith("Matematik · 3. soru · A kitapçığında 7") and "Cevap: C · Doğru: A" in rows[0]
    assert "Yanlış" in rows[0] and "Limit" in rows[0] and "Kazanım: Limiti hesaplar." in rows[0]
    assert rows[1].startswith("Fen Bilimleri · 2. soru") and "Konu eşlenmemiş" in rows[1]
    assert rows[2].startswith("Matematik · 2. soru") and "Cevap: — · Doğru: B" in rows[2] and "Boş" in rows[2]
    assert rows[3].startswith("Fen Bilimleri · 1. soru") and "Elektrik (ünite)" in rows[3]
    assert out["violations"] == []
    assert out["allRows"] == 6 and out["stillAll"] == 6
    assert [r.split(" · ")[0] for r in out["blankRows"]] == ["Matematik", "Fen Bilimleri"]
    assert len(out["fizikBlank"]) == 1 and out["fizikBlank"][0].startswith("Fen Bilimleri · 1. soru")
    assert out["topicHeads"] == ["Fizik · 0 D · 1 Y · 1 B · -0,25 net"]
    assert out["topicHeadsAll"] == ["Matematik · 2 D · 1 Y · 1 B · 1,75 net", "Fizik · 0 D · 1 Y · 1 B · -0,25 net"]
    turev = next(r for r in out["topicRows"] if r.startswith("Türev"))
    assert "Zayıf" in turev and "%50" in turev and "%62" in turev and "–" in turev
    assert out["note"] == "2 soru konuya eşlenmediği için konu analizine girmedi."
    assert out["analysisCalls"] == 1  # süzgeç ve ders seçimi yeni istek atmaz
    assert out["reportCalls"] == 1 and out["fetched"] == [["https://r2.example/k.pdf?sig=1",
                                                          "full-ayt-2-karne-elif-kaya.pdf"]]
    assert out["button"] == "Karne (PDF)"
    assert "Bu denemede soru ayrıntısı yok" in out["countsOnly"]
    assert "Sonuç doğru / yanlış / boş sayılarıyla girildi" in out["countsOnly"]
    assert "Konu analizi yok" in out["countsOnly"]
    assert not any(c.startswith("Yanlış ve boş") for c in out["countsOnlyFilters"])
    assert out["blocked"] is True


def test_analysis_logic() -> None:
    out = _run(LOGIC)
    assert out["def"] == ["sec-mat:3", "sec-fen:2", "sec-mat:2", "sec-fen:1"]
    assert out["wrong"] == [3]
    assert out["counts"] == {"wrong_blank": 4, "wrong": 2, "blank": 2, "all": 6}
    assert out["countsFizik"] == {"wrong_blank": 2, "wrong": 1, "blank": 1, "all": 2}
    assert out["titleB"] == "Matematik · 3. soru · A kitapçığında 7"
    assert out["titleA"] == "Matematik · 3. soru"
    assert out["answer"] == "Cevap: C · Doğru: A"
    assert out["blankAnswer"] == "Cevap: — · Doğru: B"
    assert out["cancelled"] == "İptal (doğru sayıldı)"
    assert out["unknown"] == "blank"
    assert out["topic"] == "Konu eşlenmemiş"
    assert out["outcome"] == "Kazanım: Limiti hesaplar."
    assert out["pct"] == ["%67", "–", "%0"]
    assert out["heading"] == "Fizik · 0 D · 1 Y · 1 B · -0,25 net"
    assert out["groups"] == [["matematik", 2], ["fizik", 1]]
    assert out["options"] == ["Tüm dersler", "Matematik", "Fizik"]
    assert out["single"] == []
    assert out["unmatched"] == "3 soru konuya eşlenmediği için konu analizine girmedi."
