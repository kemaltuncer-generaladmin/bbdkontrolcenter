"""Uygulamada deneme, canlı sınav, yayınevi kazanım tablosu + eşleştirme (Akademi README §14 madde 120) — KM paneli.

Kullanıcı (16.10.2026): "çocuk denemesini tıklayacak ve optik form açılacak … biz de cevap anahtarı ve konu analiz
excelini yükleyeceğiz … yayınevlerine göre kazanım isimlendirmesi değişiyor. otomatik eşleştirme yapmalı … ayrıca
uzaktan ek süre verme vs".

- `exams/exams-app-logic.js` (saf, Node): tarih/saat ↔ ISO (İstanbul), LGS oturum önerisi, ayar doğrulama + gövde,
  kâğıt denemede yeni alanların gönderilmemesi, yerel geri sayım (sunucu saati farkı), canlı süzgeç / seçim özeti,
  ek süre gövdesi, eşleştirme karar durumu / sayaçlar / "Önerilenleri onayla" / karar gövdesi, konu araması;
- vekil: 16 yeni işlem izin listesinde, KM izni `bbd_akademi.exams` (gözetmen izni `exams.proctor` dahil);
- DOM: Deneme ekle → "Uygulamada" → kayıt gövdesi → detay Canlı sınav ile açılır → seçili öğrenciye +10 dk →
  duraklat; Yayınevi Excel'i sihirbazı ÖRNEK DOSYAYLA (`fixtures/CAP_TYT_0_KAZANIM_TABLOSU_CEVAP_ANAHTARLI.xls`: köprüye
  dosyanın kendisi gider; taklit Akademi önizlemeyi aynı dosyanın satırlarından kurar) → eşleştirme → uygula;
  Kazanım eşleştirmesi sekmesi (PUT) ve eşleme sözlüğü (sil).
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import EXAMS, km_permissions
from modules.bbd_akademi.tests.test_akademi_kocluk import PRELUDE, node_run

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
SAMPLE = Path(__file__).resolve().parent / "fixtures" / "CAP_TYT_0_KAZANIM_TABLOSU_CEVAP_ANAHTARLI.xls"

# --------------------------------------------------------------------------- saf mantık

LOGIC = r"""
import * as m from './logic.mjs';
const sections = [{ subjectId: 'turkce', title: 'Türkçe' }, { subjectId: 'matematik', title: 'Matematik' },
  { subjectId: 'fen-bilimleri', title: 'Fen Bilimleri' }, { subjectId: 'tc-inkilap-tarihi', title: 'İnkılap' },
  { subjectId: 'din-kulturu', title: 'Din Kültürü' }, { subjectId: 'ingilizce', title: 'İngilizce' }];
const draft = m.defaultAppDraft('tyt', '2026-10-18');
const lgs = m.sessionPreset('lgs', sections);
const lgsDraft = { ...m.defaultAppDraft('lgs', '2026-10-18'), sessions: lgs };
const now = Date.parse('2026-10-18T10:30:00+03:00');
const rows = [
  { student: { id: 's1', displayName: 'Elif Kaya', classGroup: { id: 'c1', name: '12-A' } }, state: 'in_progress',
    endsAt: '2026-10-18T11:00:00+03:00', remainingSeconds: 1800, lastSeenAt: '2026-10-18T10:27:00+03:00' },
  { student: { id: 's2', displayName: 'Ömer Şahin', classGroup: { id: 'c2', name: '12-B' } }, state: 'submitted' },
  { student: { id: 's3', displayName: 'İpek Işık', classGroup: { id: 'c1', name: '12-A' } }, state: 'not_started' },
];
const target = (title, level = 'topic') => ({ level, topicId: `t-${title}`, unitId: `u-${title}`, title, path: `TYT › ${title}`,
  subjectId: 'turkce', programId: 'p' });
const matches = [
  { matchKey: 'a', name: 'Sözcükte Anlam', subjectRaw: 'Türkçe', subjectId: 'turkce', confidence: 'exact', autoApplied: true,
    target: target('Sözcükte Anlam'), questions: [{ sectionKey: 'tr', questionNo: 1 }, { sectionKey: 'tr', questionNo: 2 },
      { sectionKey: 'tr', questionNo: 7 }], alternatives: [] },
  { matchKey: 'b', name: 'Söz Öbeklerinde Anlam', subjectRaw: 'Türkçe', subjectId: 'turkce', confidence: 'fuzzy', autoApplied: false,
    target: target('Sözcükte Anlam'), questions: [{ sectionKey: 'tr', questionNo: 3 }], alternatives: [target('Söz Sanatları')] },
  { matchKey: 'c', name: 'Cümlenin Ögeleri - Karma', subjectRaw: 'Türkçe', subjectId: 'turkce', confidence: 'none',
    autoApplied: false, target: null, questions: [{ sectionKey: 'tr', questionNo: 8 }], alternatives: [] },
  { matchKey: 'd', name: 'Hücrenin Yapısı', subjectRaw: 'Biyoloji', subjectId: 'biyoloji', confidence: 'synonym', autoApplied: true,
    target: target('Hücre', 'unit'), questions: [{ sectionKey: 'fen', questionNo: 16 }], alternatives: [] },
];
const decisions = new Map();
const before = m.matchCounts(matches, decisions);
const review = m.filterMatches(matches, decisions, { onlyReview: true }).map((row) => row.matchKey);
const accepted = m.acceptSuggestions(matches, decisions);
decisions.set('c', { skip: true });
decisions.set('d', { target: target('Hücre', 'unit') });
const programs = [{ id: 'p', title: 'TYT Türkçe', subject: { id: 'turkce' }, units: [
  { id: 'u1', title: 'Sözcükte Anlam', topics: [{ id: 't1', title: 'Gerçek ve Mecaz Anlam' }, { id: 't2', title: 'Söz Öbekleri' }] },
  { id: 'u2', title: 'Cümlenin Ögeleri', topics: [{ id: 't3', title: 'Yüklem ve Özne' }, { id: 't4', title: 'Eski', retired: true }] }] }];
process.stdout.write(JSON.stringify({
  fold: m.fold('  İSTİKRAR  Çalışma Öğün '),
  stamps: [m.splitStamp('2026-10-18T07:05:00Z'), m.joinStamp('2026-10-18', '9.5'), m.joinStamp('2026-10-18', '09:05'),
    m.joinStamp('2026-10-18', '25:00'), m.joinStamp('', '10:00')],
  draft,
  lgs,
  lgsTotal: m.totalDuration(lgsDraft),
  valid: [m.validateApp(draft, 4), m.validateApp({ ...draft, closesClock: '11:00' }, 4),
    m.validateApp({ ...draft, closesDate: '2026-10-17' }, 4), m.validateApp({ ...draft, opensClock: 'x' }, 4),
    m.validateApp(lgsDraft, 6), m.validateApp({ ...lgsDraft, sessions: [lgs[0]] }, 6),
    m.validateApp({ ...lgsDraft, sessions: [lgs[0], { ...lgs[1], sectionOrders: [0, 1, 2] }] }, 6)],
  body: m.appBody(lgsDraft),
  bodySingle: m.appBody({ ...draft, optionCount: '4' }),
  delivery: [m.deliveryPart({ deliveryMode: 'paper' }, null), m.deliveryPart({ deliveryMode: 'paper' }, { deliveryMode: 'app' }),
    m.deliveryPart({ deliveryMode: 'app', app: draft }, null).app.closesAt],
  fromExam: m.appDraftFromExam({ type: 'lgs', app: { opensAt: '2026-10-18T07:00:00Z', closesAt: '2026-10-18T10:00:00Z',
    durationMinutes: 155, bookletMode: 'assigned', optionCount: 4, resultsRelease: 'manual', graceSeconds: 60,
    sessions: [{ key: 'sayisal', title: 'Sayısal', order: 2, durationMinutes: 80, sectionIds: ['x2'] },
      { key: 'sozel', title: 'Sözel', order: 1, durationMinutes: 75, sectionIds: ['x1'] }] } }, [{ id: 'x1' }, { id: 'x2' }]),
  offset: m.clockOffset('2026-10-18T10:30:05+03:00', now),
  remaining: [m.remainingSeconds(rows[0], { now, offsetMs: 0 }), m.remainingSeconds(rows[0], { now, paused: true }),
    m.remainingSeconds(rows[1], { now }), m.clockText(3725), m.clockText(305), m.clockText(null)],
  seen: [m.seenText(rows[0].lastSeenAt, { now }), m.seenText('2026-10-18T10:29:50+03:00', { now }), m.isQuiet(rows[0], { now })],
  filter: [m.filterLive(rows, { classGroupId: 'c1' }).map((r) => r.student.id), m.filterLive(rows, { q: 'ipek isik' }).map((r) => r.student.id),
    m.filterLive(rows, { state: 'submitted' }).map((r) => r.student.id)],
  classes: m.liveClasses(rows),
  selection: [m.selectionInfo([rows[0], rows[2]]), m.selectionInfo([rows[1]])],
  skipped: m.skippedText([{ studentId: 's2', code: 'already_submitted' }], (id) => rows.find((r) => r.student.id === id).student.displayName),
  extend: [m.extendBody({ minutes: 10, studentIds: ['s1'], extendWindow: true, reason: ' elektrik ' }), m.extendBody({ minutes: 0 }).error,
    m.extendBody({ minutes: 5 })],
  questions: m.questionsText(matches[0].questions.concat([{ sectionKey: 'fen', questionNo: 3 }]), (k) => ({ tr: 'TYT Türkçe', fen: 'TYT Fen' }[k])),
  before, review, accepted,
  after: m.matchCounts(matches, decisions),
  decisions: m.decisionsBody(decisions),
  states: matches.map((row) => m.rowState(row, decisions.get(row.matchKey)).state),
  search: [m.searchTopics(programs, 'ögeleri').map((x) => [x.level, x.title]), m.searchTopics(programs, 'soz obek').map((x) => x.path)],
  exam: [m.examFromPreview({ publisher: 'ÇAP', booklets: ['A', 'B'], sections: [{ key: 'tyt-turkce', title: 'TYT Türkçe', subjectId: 'turkce',
    questionCount: 40 }, { key: 'x', title: 'X', subjectId: null, questionCount: 5 }] }, { name: 'ÇAP TYT 0', date: '2026-10-18', type: 'tyt',
    subjects: { x: 'fizik' } }), m.examFromPreview({ sections: [{ key: 'x', title: 'X', subjectId: null, questionCount: 5 }] },
    { name: 'a', type: 'tyt' }).error],
}));
"""


def test_saf_mantik(tmp_path: Path) -> None:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (tmp_path / "logic.mjs").write_bytes((PANEL / "exams/exams-app-logic.js").read_bytes())
    (tmp_path / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=tmp_path, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    assert out["fold"] == "istikrar calisma ogun"
    assert out["stamps"] == [{"date": "2026-10-18", "clock": "10:05"}, None, "2026-10-18T09:05:00+03:00", None, None]
    assert out["draft"] == {"opensDate": "2026-10-18", "opensClock": "10:00", "closesDate": "2026-10-18", "closesClock": "13:15",
                            "durationMinutes": 165, "sessions": [], "bookletMode": "single", "optionCount": None,
                            "resultsRelease": "after_close", "graceSeconds": 120}
    assert out["lgs"] == [{"key": "sozel", "title": "Sözel Bölüm", "durationMinutes": 75, "sectionOrders": [0, 3, 4, 5]},
                          {"key": "sayisal", "title": "Sayısal Bölüm", "durationMinutes": 80, "sectionOrders": [1, 2]}]
    assert out["lgsTotal"] == 155
    ok, short, before, bad_clock, lgs_ok, missing, twice = out["valid"]
    assert ok is None and lgs_ok is None
    assert short == "Pencere (60 dk) sınav süresinden (165 dk) kısa olamaz."
    assert before == "Kapanış açılıştan sonra olmalı."
    assert bad_clock.startswith("Açılış tarihini")
    assert missing == "Her bölüm bir oturumda olmalı (oturumsuz: 2, 3. bölüm)."
    assert twice == "Bir bölüm iki oturumda olamaz (1. bölüm)."
    assert out["body"] == {"opensAt": "2026-10-18T10:00:00+03:00", "closesAt": "2026-10-18T13:15:00+03:00",
                           "durationMinutes": 155, "sessions": [
                               {"key": "sozel", "title": "Sözel Bölüm", "sectionOrders": [0, 3, 4, 5], "durationMinutes": 75},
                               {"key": "sayisal", "title": "Sayısal Bölüm", "sectionOrders": [1, 2], "durationMinutes": 80}],
                           "bookletMode": "single", "optionCount": None, "resultsRelease": "after_close", "graceSeconds": 120}
    assert out["bodySingle"]["sessions"] == [] and out["bodySingle"]["durationMinutes"] == 165
    assert out["bodySingle"]["optionCount"] == 4
    assert out["delivery"] == [{}, {"deliveryMode": "paper", "app": None}, "2026-10-18T13:15:00+03:00"]
    from_exam = out["fromExam"]
    assert from_exam["opensClock"] == "10:00" and from_exam["closesClock"] == "13:00"
    assert [s["key"] for s in from_exam["sessions"]] == ["sozel", "sayisal"]
    assert from_exam["sessions"][0]["sectionOrders"] == [0] and from_exam["bookletMode"] == "assigned"
    assert out["offset"] == 5000
    assert out["remaining"] == [1800, 1800, None, "1:02:05", "05:05", "—"]
    assert out["seen"] == ["3 dk önce", "şimdi", True]
    assert out["filter"] == [["s1", "s3"], ["s3"], ["s2"]]
    assert out["classes"] == [{"id": "c1", "name": "12-A"}, {"id": "c2", "name": "12-B"}]
    first, second = out["selection"]
    assert first["canExtend"] and first["canFinish"] and not first["canReopen"]
    assert second["canReopen"] and not second["canFinish"] and not second["canExtend"]
    assert out["skipped"] == "1 öğrenci atlandı: Ömer Şahin (zaten bitirdi)."
    assert out["extend"] == [{"body": {"minutes": 10, "extendWindow": True, "studentIds": ["s1"], "reason": "elektrik"}},
                             "Ek süre 1–240 dakika olmalı.", {"body": {"minutes": 5, "extendWindow": False}}]
    assert out["questions"] == "TYT Türkçe 1–2, 7 · TYT Fen 3"
    assert out["before"]["review"] == 2 and out["before"]["mappedQuestions"] == 4 and out["before"]["questions"] == 6
    assert out["review"] == ["b", "c"]
    assert out["accepted"] == 1
    assert out["after"]["review"] == 0 and out["after"]["skipped"] == 1 and out["after"]["mappedQuestions"] == 5
    assert out["decisions"] == [{"matchKey": "b", "topicId": "t-Sözcükte Anlam", "remember": True},
                                {"matchKey": "c", "skip": True, "remember": False},
                                {"matchKey": "d", "unitId": "u-Hücre", "remember": True}]
    assert out["states"] == ["auto", "decided", "skipped", "decided"]
    by_unit, by_topic = out["search"]
    assert by_unit == [["unit", "Cümlenin Ögeleri"], ["topic", "Yüklem ve Özne"]]      # emekli konu yok
    assert by_topic == ["TYT Türkçe › Sözcükte Anlam › Söz Öbekleri"]
    made, error = out["exam"]
    assert made["exam"] == {"name": "ÇAP TYT 0", "type": "tyt", "publisher": "ÇAP", "date": "2026-10-18",
                            "bookletTypes": ["A", "B"], "classGroupIds": [], "isHistorical": False,
                            "sections": [{"subjectId": "turkce", "title": "TYT Türkçe", "questionCount": 40, "order": 1},
                                         {"subjectId": "fizik", "title": "X", "questionCount": 5, "order": 2}]}
    assert error == "“X” bölümünün dersini seçin."


# --------------------------------------------------------------------------- vekil

NEW_OPERATIONS = {
    "admin_getExamLive": ("GET", "/v1/admin/exams/{examId}/live"),
    "admin_extendExamTime": ("POST", "/v1/admin/exams/{examId}/live/extend"),
    "admin_pauseExam": ("POST", "/v1/admin/exams/{examId}/live/pause"),
    "admin_resumeExam": ("POST", "/v1/admin/exams/{examId}/live/resume"),
    "admin_finishExamAttempts": ("POST", "/v1/admin/exams/{examId}/live/finish"),
    "admin_reopenExamAttempt": ("POST", "/v1/admin/exams/{examId}/live/reopen"),
    "admin_setExamWindow": ("POST", "/v1/admin/exams/{examId}/live/window"),
    "admin_uploadExamQuestionBooklet": ("PUT", "/v1/admin/exams/{examId}/question-booklet"),
    "admin_deleteExamQuestionBooklet": ("DELETE", "/v1/admin/exams/{examId}/question-booklet"),
    "admin_previewExamPublisherTable": ("POST", "/v1/admin/exam-publisher-tables/preview"),
    "admin_getExamPublisherTable": ("GET", "/v1/admin/exam-publisher-tables/{publisherTableId}"),
    "admin_applyExamPublisherTable": ("POST", "/v1/admin/exam-publisher-tables/{publisherTableId}/apply"),
    "admin_getExamTopicMatching": ("GET", "/v1/admin/exams/{examId}/topic-matching"),
    "admin_putExamTopicMatching": ("PUT", "/v1/admin/exams/{examId}/topic-matching"),
    "admin_listExamTopicAliases": ("GET", "/v1/admin/exam-topic-aliases"),
    "admin_deleteExamTopicAlias": ("DELETE", "/v1/admin/exam-topic-aliases/{aliasId}"),
}


def test_vekil_izinleri() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    for op_id, (method, path) in NEW_OPERATIONS.items():
        assert op_id in ops, op_id
        assert (ops[op_id].method, ops[op_id].path) == (method, path), op_id
        assert km_permissions(ops[op_id]) == (EXAMS,), op_id
        assert ops[op_id].surface == "both", op_id
    # Gözetmen (öğretmen) izni de denemeler iznine düşer; duraklat / yeniden aç / pencere yalnız yönetim.
    assert ops["admin_extendExamTime"].permissions == ("exams.manage", "exams.proctor")
    assert ops["admin_pauseExam"].permissions == ("exams.manage",)
    assert ops["admin_previewExamPublisherTable"].multipart and ops["admin_uploadExamQuestionBooklet"].multipart
    # Öğrencinin kendi oturum uçları (exams.take:self) anahtarla çağrılamaz → vekilde yok.
    assert not any("/attempt" in op.path for op in OPERATIONS)


# ----------------------------------------------------------- örnek dosya → taklit önizleme

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
SUBJECTS = {"Türkçe": "turkce", "Tarih-1": "tarih", "Coğrafya-1": "cografya", "Felsefe": "felsefe",
            "Felsefe (Seçmeli)": "felsefe", "Din Kül. ve Ahl. Bil.": "din-kulturu", "Matematik-1": "matematik",
            "Geometri": "geometri", "Fizik": "fizik", "Kimya": "kimya", "Biyoloji": "biyoloji"}
SECTION_SUBJECTS = {"TYT Türkçe": "turkce", "TYT Sosyal": "sosyal-bilimler", "TYT Matematik": "matematik",
                    "TYT Fen": "fen-bilimleri"}


def _rows(path: Path) -> list[list[str | None]]:
    """Standart kütüphaneyle XLSX okuma (uzantı .xls, içerik OOXML)."""
    with zipfile.ZipFile(path) as book:
        shared = ["".join(t.text or "" for t in si.iter(f"{_NS}t"))
                  for si in ET.fromstring(book.read("xl/sharedStrings.xml")).findall(f"{_NS}si")]
        sheet = min(name for name in book.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml", name))
        root = ET.fromstring(book.read(sheet))
    out: list[list[str | None]] = []
    for row in root.iter(f"{_NS}row"):
        cells: dict[int, str | None] = {}
        for cell in row.findall(f"{_NS}c"):
            column = re.match(r"[A-Z]+", cell.get("r", "A")).group()  # type: ignore[union-attr]
            index = 0
            for char in column:
                index = index * 26 + ord(char) - 64
            value = cell.find(f"{_NS}v")
            text = value.text if value is not None else None
            cells[index - 1] = shared[int(text)] if cell.get("t") == "s" and text is not None else text
        out.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
    return out


def _slug(text: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    return re.sub(r"[^a-z0-9]+", "-", text.translate(table).lower()).strip("-")


def sample_preview() -> dict[str, Any]:
    rows = _rows(SAMPLE)
    header = [str(cell or "") for cell in rows[2]]
    col = {name: header.index(name) for name in ("Kitapçık", "Test", "Ders", "A Soru", "B Soru", "Cevap", "Kazanım Kodu")}
    data = [row for row in rows[3:] if row and row[col["Kitapçık"]] == "A"]
    sections: list[dict[str, Any]] = []
    keys_a: dict[str, list[tuple[int, str]]] = {}
    keys_b: dict[str, list[tuple[int, str]]] = {}
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in data:
        test, subject = str(row[col["Test"]]), str(row[col["Ders"]])
        key = _slug(test)
        if not any(item["key"] == key for item in sections):
            sections.append({"key": key, "title": test, "examType": "tyt", "subjectId": SECTION_SUBJECTS[test],
                             "questionCount": 0, "subjects": []})
        section = next(item for item in sections if item["key"] == key)
        section["questionCount"] += 1
        a_no, b_no, answer = int(str(row[col["A Soru"]])), int(str(row[col["B Soru"]])), str(row[col["Cevap"]])
        keys_a.setdefault(key, []).append((a_no, answer))
        keys_b.setdefault(key, []).append((b_no, answer))
        name = str(row[col["Kazanım Kodu"] + 1] or "").strip()
        group = groups.setdefault((subject, name), {"subject": subject, "name": name, "code": row[col["Kazanım Kodu"]],
                                                    "questions": []})
        group["questions"].append({"sectionKey": key, "questionNo": a_no})
    matches = []
    by_conf = {"exact": 0, "alias": 0, "synonym": 0, "fuzzy": 0, "none": 0}
    for index, group in enumerate(groups.values()):
        subject_id = SUBJECTS[group["subject"]]
        target = {"level": "topic", "topicId": f"{subject_id}-{index}", "unitId": f"{subject_id}-u", "title": group["name"][:40],
                  "path": f"TYT {group['subject']} › {group['name'][:40]}", "subjectId": subject_id,
                  "programId": f"tyt-{subject_id}"}
        if "Karma" in group["name"]:
            confidence, target_value = "none", None
        elif len(group["name"]) > 60:
            confidence, target_value = "fuzzy", target
        else:
            confidence, target_value = ("exact", "synonym", "alias")[index % 3], target
        by_conf[confidence] += 1
        matches.append({"matchKey": f"{_slug(group['subject'])}|{_slug(group['name'])}", "subjectRaw": group["subject"],
                        "subjectId": subject_id, "name": group["name"], "code": group["code"],
                        "questions": group["questions"], "target": target_value, "confidence": confidence,
                        "score": 1.0 if confidence == "exact" else 0.7, "alternatives": [],
                        "autoApplied": confidence in ("exact", "alias", "synonym")})
    question_count = sum(len(m["questions"]) for m in matches)
    auto = sum(len(m["questions"]) for m in matches if m["autoApplied"])
    return {
        "id": "pt-1", "fileName": SAMPLE.name, "fileSha256": "0" * 64, "fileKind": "xlsx",
        "publisher": rows[0][0], "examName": rows[0][2], "code": rows[1][0], "gradeText": rows[1][2], "examType": "tyt",
        "sections": sections, "booklets": ["A", "B"],
        "answerKeys": {"A": {k: "".join(a for _, a in sorted(v)) for k, v in keys_a.items()},
                       "B": {k: "".join(a for _, a in sorted(v)) for k, v in keys_b.items()}},
        "matches": matches,
        "stats": {"questionCount": question_count, "uniqueOutcomes": len(matches), "byConfidence": by_conf,
                  "autoMatchedQuestions": auto, "autoMatchRate": auto / question_count},
        "errors": [], "appliedExamId": None, "appliedAt": None, "createdAt": "2026-10-16T20:00:00Z",
    }


def test_ornek_dosya_okunur() -> None:
    preview = sample_preview()
    assert (preview["publisher"], preview["examName"], preview["code"], preview["gradeText"]) == (
        "ÇAP", "ÇAP TYT 0", "7661", "12.Sınıf, Mezun")
    assert [(s["title"], s["questionCount"]) for s in preview["sections"]] == [
        ("TYT Türkçe", 40), ("TYT Sosyal", 25), ("TYT Matematik", 40), ("TYT Fen", 20)]
    assert preview["stats"]["questionCount"] == 125
    assert preview["answerKeys"]["A"]["tyt-turkce"].startswith("DBABCA")
    # B kitapçığı "B Soru" sütunundan: B1 = A4 (B), B2 = A3 (A), B3 = A2 (B), B4 = A1 (D)
    assert preview["answerKeys"]["B"]["tyt-turkce"].startswith("BABD")


# ------------------------------------------------------------------------- DOM: tanım + canlı

APP = r"""
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const toast = toaster(root);
  const subjects = [{ id: 'turkce', name: 'Türkçe' }, { id: 'sosyal-bilimler', name: 'Sosyal Bilimler' },
    { id: 'matematik', name: 'Matematik' }, { id: 'fen-bilimleri', name: 'Fen Bilimleri' }];
  const app = { root, api: client, status: null,
    toast: (text, tone) => { toasts.push([text, tone]); toast(text, tone); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { classGroups: async () => [{ id: 'c1', name: '12-A' }], studentGroups: async () => [], terms: async () => [],
      subjects: async () => subjects, async options() { return []; } } };
  const last = () => root.querySelectorAll('.kit-dialog').pop();
"""

LIVE = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
""" + APP + r"""
  const out = {};
  const student = (id, name, cls) => ({ id, displayName: name, classGroup: { id: cls, name: cls === 'c1' ? '12-A' : '12-B' } });
  const appSettings = (state = 'open') => ({ opensAt: '2026-10-18T10:00:00+03:00', closesAt: '2026-10-18T13:15:00+03:00',
    durationMinutes: 165, sessions: [{ key: 'main', title: 'TYT', order: 1, durationMinutes: 165, sectionIds: [], opensAt: null }],
    bookletMode: 'single', optionCount: 5, resultsRelease: 'after_close', graceSeconds: 120, state, pausedAt: state === 'paused'
      ? '2026-10-18T10:40:00+03:00' : null, extraMinutesAll: 0, questionBooklet: null, finalizedAt: null });
  const row = (s, state, extra = {}) => ({ student: s, state, sessionKey: 'main', bookletType: 'A', answeredCount: state === 'not_started' ? 0 : 37,
    questionTotal: 125, startedAt: null, endsAt: state === 'in_progress' ? new Date(Date.now() + 3600_000).toISOString() : null,
    remainingSeconds: state === 'in_progress' ? 3600 : null, lastSeenAt: state === 'in_progress' ? new Date().toISOString() : null,
    appExitCount: 0, extraMinutes: 0, submittedAt: null, submitReason: state === 'submitted' ? 'manual' : null, lateSync: false,
    reopenCount: 0, ...extra });
  const items = [row(student('s1', 'Elif Kaya', 'c1'), 'in_progress', { appExitCount: 4 }),
    row(student('s2', 'Ömer Şahin', 'c2'), 'submitted'), row(student('s3', 'İpek Işık', 'c1'), 'not_started')];
  const live = (state = 'open', extra = {}) => ({ examId: 'ex-1', name: 'ÇAP TYT 0', type: 'tyt', serverNow: new Date().toISOString(),
    app: appSettings(state), counters: { totalCount: 3, notStarted: 1, inProgress: 1, submitted: 1, expired: 0 }, items,
    canManage: true, ...extra });

  routes['GET /v1/admin/exams'] = { items: [], nextCursor: null };
  routes['GET /v1/catalog/exam-templates'] = { items: [{ type: 'tyt', name: 'TYT', sections: [
    { subjectId: 'turkce', title: 'Türkçe', questionCount: 40 }, { subjectId: 'sosyal-bilimler', title: 'Sosyal Bilimler', questionCount: 25 },
    { subjectId: 'matematik', title: 'Matematik', questionCount: 40 }, { subjectId: 'fen-bilimleri', title: 'Fen Bilimleri', questionCount: 20 }] }] };
  routes['GET /v1/admin/curricula/programs'] = { items: [] };
  routes['POST /v1/admin/exams'] = (body) => ({ id: 'ex-1', status: 'draft', resultCount: 0, answerKeyStatus: 'none',
    questionTopicStatus: 'none', createdAt: '2026-10-16T20:00:00Z', studentIds: [], ...body,
    sections: body.sections.map((s, i) => ({ ...s, id: `sec-${i}`, subject: subjects.find((x) => x.id === s.subjectId) })),
    app: body.app ? appSettings('open') : null });
  routes['GET /v1/admin/exams/ex-1/live'] = () => live();
  routes['POST /v1/admin/exams/ex-1/live/extend'] = (body) => ({ affected: 1, skipped: [], live: live('open') , echo: body });
  routes['POST /v1/admin/exams/ex-1/live/pause'] = () => ({ affected: 1, skipped: [], live: live('paused') });

  const { section } = await load('./panels/bbd_akademi/exams/exams.js');
  const view = section(app);
  root.append(view.node);
  view.shown({});
  await sleep(60);
  out.headerButtons = root.querySelectorAll('.ak-head button').map((b) => b.textContent.trim());
  out.viewTabs = root.querySelectorAll('.kit-tabs [role="tab"]').map((tab) => tab.textContent.trim());

  // 1) Deneme ekle → Uygulamada.
  click(byText(root, 'button', 'Deneme ekle'));
  await sleep(60);
  let dialog = last();
  type(dialog.querySelectorAll('input.kit-input')[0], 'ÇAP TYT 0');
  const cards = dialog.querySelectorAll('.ak-ex-delivery-card');
  out.cards = cards.map((c) => c.textContent);
  out.appHiddenBefore = dialog.querySelector('.ak-ex-app').closest('.kit-field').hidden;
  click(cards[1]);
  await sleep(10);
  out.appHiddenAfter = dialog.querySelector('.ak-ex-app').closest('.kit-field').hidden;
  click(byText(dialog, 'button', 'Türe göre doldur'));
  await sleep(60);
  out.summary = dialog.querySelector('.ak-ex-app-summary').textContent;
  out.a11yDialog = audit(dialog);
  click(byText(dialog, 'button', 'Denemeyi tanımla'));
  await sleep(120);
  out.created = sent.filter((c) => c.path === '/v1/admin/exams' && c.method === 'POST').map((c) => c.body);

  // 2) Detay canlı ekranla açılır.
  await sleep(60);
  const detailTabs = root.querySelectorAll('.kit-tabs').pop().querySelectorAll('[role="tab"]').map((tab) => tab.textContent.trim());
  out.detailTabs = detailTabs;
  const pane = root.querySelector('.ak-ex-live');
  out.liveText = pane?.textContent || '';
  out.a11yLive = audit(pane);
  const lines = pane.querySelectorAll('.kit-row').filter((line) => !line.classList.contains('kit-head'));
  out.rowCount = lines.length;
  out.selectionHidden = pane.querySelector('.ak-ex-live-selection').hidden;
  const check = lines[0].querySelector('input');
  check.checked = true;
  check.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(10);
  const bar = pane.querySelector('.ak-ex-live-selection');
  out.selectionText = bar.textContent;
  click(bar.querySelector('.kit-menu button'));
  click(byText(bar, 'button', '+10 dk'));
  await sleep(40);
  dialog = last();
  out.extendDialog = dialog.textContent;
  click(byText(dialog, 'button', 'Ek süreyi ver'));
  await sleep(80);
  out.extend = sent.filter((c) => c.path.endsWith('/live/extend')).map((c) => c.body);

  // 3) Duraklat (onaylı).
  click(byText(pane, 'button', 'Duraklat'));
  await sleep(20);
  dialog = last();
  click(byText(dialog, 'button', 'Duraklat'));
  await sleep(80);
  out.pause = sent.filter((c) => c.path.endsWith('/live/pause')).length;
  out.pausedText = pane.textContent;
  out.toasts = toasts.map(([text]) => text);
  view.destroy();
  return out;
}
"""


def test_uygulamada_deneme_tanimi_ve_canli_sinav() -> None:
    out = node_run(LIVE)
    assert "Yayınevi Excel'i yükle" in out["headerButtons"] and "Deneme ekle" in out["headerButtons"]
    assert out["viewTabs"] == ["Denemeler", "Analiz ve karşılaştırma", "Eşleme sözlüğü"]
    assert out["cards"][0].startswith("Kâğıt") and out["cards"][1].startswith("Uygulamada")
    assert out["appHiddenBefore"] is True and out["appHiddenAfter"] is False
    assert out["summary"] == "Pencere 195 dk · sınav 165 dk"
    assert out["a11yDialog"] == []
    (created,) = out["created"]
    assert created["deliveryMode"] == "app" and len(created["sections"]) == 4
    assert created["app"]["durationMinutes"] == 165 and created["app"]["sessions"] == []
    assert created["app"]["opensAt"].endswith("T10:00:00+03:00") and created["app"]["closesAt"].endswith("T13:15:00+03:00")
    assert created["app"]["bookletMode"] == "single" and created["app"]["resultsRelease"] == "after_close"
    assert "studentIds" not in created                      # tek tek öğrenci seçilmedi → alan gönderilmez
    assert out["detailTabs"][0] == "Canlı sınav" and "Kazanım eşleştirmesi" in out["detailTabs"]
    assert "Hedef öğrenci" in out["liveText"] and "Çözüyor" in out["liveText"] and "Elif Kaya" in out["liveText"]
    assert "37 / 125" in out["liveText"]
    assert out["a11yLive"] == []
    assert out["rowCount"] == 3 and out["selectionHidden"] is True
    assert out["selectionText"].startswith("1 öğrenci seçildi")
    assert "+10 dk" in out["extendDialog"] and "Pencere kapanışını da aynı kadar uzat" in out["extendDialog"]
    (extend,) = out["extend"]
    assert extend == {"minutes": 10, "extendWindow": True, "studentIds": ["s1"]}
    assert out["pause"] == 1 and "Sınav duraklatıldı" in out["pausedText"] and "Sürdür" in out["pausedText"]
    assert any(text.startswith("+10 dk ek süre verildi: 1 öğrenci") for text in out["toasts"])


# ------------------------------------------------------------------ DOM: yayınevi sihirbazı

WIZARD = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
""" + APP + r"""
  const out = {};
  const preview = __PREVIEW__;
  globalThis.FileReader = class {
    readAsDataURL(file) {
      setTimeout(() => { this.result = `data:application/octet-stream;base64,${file.__b64}`;
        this.onprogress?.({ lengthComputable: true, loaded: 1, total: 1 }); this.onload?.(); }, 0);
    }
  };
  const file = { name: '__NAME__', size: __SIZE__, type: '', lastModified: 1, __b64: '__B64__' };
  routes['POST /v1/admin/exam-publisher-tables/preview'] = () => preview;
  routes['GET /v1/admin/exam-publisher-tables/pt-1'] = () => preview;
  routes['GET /v1/admin/curricula/programs'] = { items: [{ id: 'tyt-turkce', exam: 'TYT', title: 'TYT Türkçe', subject: { id: 'turkce' } }] };
  routes['GET /v1/admin/curricula/programs/tyt-turkce'] = { id: 'tyt-turkce', title: 'TYT Türkçe', exam: 'TYT', subject: { id: 'turkce' },
    units: [{ id: 'u-cumle', title: 'Cümlenin Ögeleri', order: 1, topics: [{ id: 't-oge', title: 'Yüklem ve Özne', order: 1 }] },
      { id: 'u-yazim', title: 'Noktalama İşaretleri', order: 2, topics: [{ id: 't-virgul', title: 'Virgül', order: 1 }] }] };
  routes['POST /v1/admin/exam-publisher-tables/pt-1/apply'] = (body) => ({ exam: { id: 'ex-9', name: body.exam?.name },
    alreadyApplied: false, appliedQuestions: 125, mappedQuestions: 121, unmappedQuestions: 4, aliasesSaved: 7, resultsRecomputed: false });

  const { publisherWizard } = await load('./panels/bbd_akademi/exams/exams-publisher.js');
  const pending = publisherWizard(app);
  await sleep(40);
  let dialog = last();
  out.a11yFile = audit(dialog);
  const input = dialog.querySelectorAll('input').find((item) => item.type === 'file');
  Object.defineProperty(input, 'files', { value: [file], configurable: true });
  input.dispatchEvent(new Event('change', { bubbles: true }));
  click(byText(dialog, 'button', 'İleri'));
  await sleep(120);
  const upload = sent.find((c) => c.path === '/v1/admin/exam-publisher-tables/preview');
  out.upload = { filename: upload.upload.filename, field: upload.upload.field, size: upload.upload.dataBase64.length,
    same: upload.upload.dataBase64 === file.__b64 };
  const pane = () => dialog.querySelectorAll('.ak-wiz-pane').find((item) => !item.hidden);
  out.facts = pane().querySelector('.ak-ex-summary').textContent;
  out.nameValue = pane().querySelectorAll('input.kit-input')[0].value;
  out.a11yPreview = audit(dialog);
  click(byText(dialog, 'button', 'İleri'));
  await sleep(60);
  const rows = () => pane().querySelectorAll('.ak-ex-match .kit-row').filter((line) => !line.classList.contains('kit-head'));
  out.summary = pane().querySelector('.ak-ex-match-summary').textContent;
  out.reviewRows = rows().map((line) => line.textContent);
  out.a11yMatch = audit(dialog);
  click(byText(pane(), 'button', 'Önerilenleri onayla'));
  await sleep(20);
  out.afterAccept = rows().map((line) => line.textContent);
  // Kalan "Eşleşmedi" satırlardan birine konu seç, diğerini eşlemeden bırak.
  const karma = rows().find((line) => line.textContent.includes('Cümlenin Ögeleri - Karma'));
  click(byText(karma, 'button', 'Konu seç'));
  await sleep(80);
  const picker = last();
  out.pickerText = picker.textContent;
  out.a11yPicker = audit(picker);
  type(picker.querySelector('.ak-search'), 'yüklem');
  await sleep(220);
  const option = picker.querySelectorAll('.ak-ex-topic-option').find((item) => item.textContent.includes('Yüklem ve Özne'));
  click(option);
  click(byText(picker, 'button', 'Bu konuyu seç'));
  await sleep(40);
  const noktalama = rows().find((line) => line.textContent.includes('Noktalama İşaretleri - Karma'));
  click(byText(noktalama, 'button', 'Eşleme'));
  await sleep(20);
  out.finalSummary = pane().querySelector('.ak-ex-match-summary').textContent;
  click(byText(dialog, 'button', 'Uygula'));
  const result = await pending;
  out.result = result;
  out.apply = sent.filter((c) => c.path === '/v1/admin/exam-publisher-tables/pt-1/apply').map((c) => c.body);
  out.toasts = toasts.map(([text]) => text);
  return out;
}
"""


def _scenario(text: str, preview: dict[str, Any]) -> str:
    data = SAMPLE.read_bytes()
    return (text.replace("__PREVIEW__", json.dumps(preview, ensure_ascii=False))
            .replace("__NAME__", SAMPLE.name).replace("__SIZE__", str(len(data)))
            .replace("__B64__", base64.b64encode(data).decode()))


def test_yayinevi_sihirbazi_ornek_dosyayla() -> None:
    preview = sample_preview()
    out = node_run(_scenario(WIZARD, preview))
    assert out["upload"]["filename"] == SAMPLE.name and out["upload"]["field"] == "file" and out["upload"]["same"]
    facts = out["facts"]
    for part in ("ÇAP", "ÇAP TYT 0 · kod 7661", "12.Sınıf, Mezun", "TYT Türkçe 40 · TYT Sosyal 25 · TYT Matematik 40 · TYT Fen 20",
                 "toplam 125 soru", "A, B", "A: 125/125 · B: 125/125"):
        assert part in facts, part
    assert out["nameValue"] == "ÇAP TYT 0"
    assert out["a11yFile"] == [] and out["a11yPreview"] == [] and out["a11yMatch"] == [] and out["a11yPicker"] == []
    stats = preview["stats"]
    review = stats["byConfidence"]["fuzzy"] + stats["byConfidence"]["none"]
    assert f"125 soru · {stats['uniqueOutcomes']} benzersiz kazanım" in out["summary"]
    assert f"{review} kazanım" in out["summary"] and "kontrol bekliyor" in out["summary"]
    assert len(out["reviewRows"]) == review                       # tablo kontrol gerekenlerle açılır
    assert any("Benzer — kontrol et" in row for row in out["reviewRows"])
    assert any("Eşleşmedi" in row and "Konu seçilmedi" in row for row in out["reviewRows"])
    assert len(out["afterAccept"]) == stats["byConfidence"]["none"]   # öneri onaylananlar süzgeçten düştü
    assert "Önerilenleri" not in out["pickerText"] and "Yayınevi kazanımı: “Cümlenin Ögeleri - Karma”" in out["pickerText"]
    assert "Bütün kazanımların kararı verildi." in out["finalSummary"] or stats["byConfidence"]["none"] > 2
    (body,) = out["apply"]
    assert body["acceptSuggestions"] is False and body["applyAnswerKeys"] is True
    assert body["exam"]["name"] == "ÇAP TYT 0" and body["exam"]["type"] == "tyt" and body["exam"]["publisher"] == "ÇAP"
    assert body["exam"]["bookletTypes"] == ["A", "B"]
    assert [s["subjectId"] for s in body["exam"]["sections"]] == ["turkce", "sosyal-bilimler", "matematik", "fen-bilimleri"]
    decisions = {d["matchKey"]: d for d in body["decisions"]}
    fuzzy = [m for m in preview["matches"] if m["confidence"] == "fuzzy"]
    for match in fuzzy:
        assert decisions[match["matchKey"]] == {"matchKey": match["matchKey"], "topicId": match["target"]["topicId"], "remember": True}
    karma = next(m for m in preview["matches"] if m["name"] == "Cümlenin Ögeleri - Karma")
    assert decisions[karma["matchKey"]] == {"matchKey": karma["matchKey"], "topicId": "t-oge", "remember": True}
    noktalama = next(m for m in preview["matches"] if m["name"] == "Noktalama İşaretleri - Karma")
    assert decisions[noktalama["matchKey"]] == {"matchKey": noktalama["matchKey"], "skip": True, "remember": False}
    assert not any(decisions.get(m["matchKey"]) for m in preview["matches"] if m["autoApplied"])   # otomatikler gövdede yok
    assert out["result"]["exam"]["id"] == "ex-9"
    assert any(text.startswith("125 soru uygulandı · 121 soru konuya eşlendi · 4 soru eşleşmeden kaldı") for text in out["toasts"])


# ----------------------------------------------------- DOM: eşleştirmeyi düzenle + sözlük

EDIT = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
""" + APP + r"""
  const out = {};
  const preview = __PREVIEW__;
  const exam = { id: 'ex-9', name: 'ÇAP TYT 0', type: 'tyt', sections: [] };
  routes['GET /v1/admin/exams/ex-9/topic-matching'] = { examId: 'ex-9', publisher: 'ÇAP', matches: preview.matches, stats: preview.stats };
  routes['PUT /v1/admin/exams/ex-9/topic-matching'] = (body) => ({ examId: 'ex-9', publisher: 'ÇAP', matches: preview.matches,
    stats: preview.stats, echo: body });
  routes['GET /v1/admin/exam-topic-aliases'] = { items: [{ id: 'al-1', publisher: 'ÇAP', subjectId: 'turkce', name: 'Söz Öbeklerinde Anlam',
    code: '21.1.4', target: { level: 'topic', topicId: 't2', unitId: 'u1', title: 'Söz Öbekleri', path: 'TYT Türkçe › Sözcükte Anlam › Söz Öbekleri',
      subjectId: 'turkce', programId: 'p' }, usageCount: 3, approvedBy: 'Ayşe Yılmaz', updatedAt: '2026-10-16T18:00:00Z' }], nextCursor: null };
  routes['DELETE /v1/admin/exam-topic-aliases/al-1'] = {};

  const { matchingTab, aliasesPanel } = await load('./panels/bbd_akademi/exams/exams-publisher.js');
  const tab = matchingTab(app, exam, { onChanged: () => { out.changed = true; } });
  root.append(tab.node);
  tab.reload();
  await sleep(60);
  out.saveDisabled = byText(tab.node, 'button', 'Değişiklikleri kaydet').disabled;
  out.a11yTab = audit(tab.node);
  const line = tab.node.querySelectorAll('.kit-row').find((item) => item.textContent.includes('Noktalama İşaretleri - Karma'));
  click(byText(line, 'button', 'Eşleme'));
  await sleep(10);
  click(byText(tab.node, 'button', 'Değişiklikleri kaydet (1)'));
  await sleep(60);
  out.put = sent.filter((c) => c.method === 'PUT').map((c) => c.body);

  const panel = aliasesPanel(app);
  root.append(panel.node);
  panel.reload();
  await sleep(60);
  out.aliasText = panel.node.textContent;
  out.a11yAliases = audit(panel.node);
  click(byText(panel.node, 'button', 'Sil'));
  await sleep(20);
  click(byText(last(), 'button', 'Sil'));
  await sleep(60);
  out.deleted = sent.filter((c) => c.method === 'DELETE').map((c) => c.path);
  out.aliasAfter = panel.node.textContent;
  return out;
}
"""


def test_eslestirmeyi_duzenle_ve_sozluk() -> None:
    preview = sample_preview()
    out = node_run(_scenario(EDIT, preview))
    assert out["saveDisabled"] is True and out["a11yTab"] == []
    key = next(m["matchKey"] for m in preview["matches"] if m["name"] == "Noktalama İşaretleri - Karma")
    assert out["put"] == [{"decisions": [{"matchKey": key, "skip": True, "remember": False}]}]
    assert out["changed"] is True
    assert "Söz Öbeklerinde Anlam" in out["aliasText"] and "Ayşe Yılmaz" in out["aliasText"]
    assert out["a11yAliases"] == []
    assert out["deleted"] == ["/v1/admin/exam-topic-aliases/al-1"]
    assert "Söz Öbeklerinde Anlam" not in out["aliasAfter"]
