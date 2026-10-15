"""Belge şablonları (`ui/panel/documents-logic.js`) — Node ile + sunucu çizicisiyle uçtan uca.

`documents-logic.js` bilerek import'suz ve DOM'suz yazıldı: `.mjs` kopyası
Node'da yüklenir, her şablon sözleşme biçimindeki örnek veriyle çağrılır ve
çıkan belge modeli JSON olarak buraya döner. Sonra her model SUNUCUNUN
çizicisinden (`document_pdf.parse` + `render`) geçirilir: panelin kurduğu her
belge gerçekten PDF olur. KVKK: hiçbir modelde açık telefon numarası kalmaz.

`node` yoksa ATLANIR (çekirdek Python'dur, Node yalnız kabuk aracıdır).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend import document_pdf
from modules.bbd_akademi.backend.documents import KINDS

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")

SCRIPT = r"""
import * as d from './documents-logic.mjs';
const stu = { id: 's1', externalRef: 'STU-0001', displayName: 'Ayşe Yılmaz', firstName: 'Ayşe',
  lastName: 'Yılmaz', classGroup: { id: 'c8a', name: '8-A' }, status: 'active', gradeLevel: 8,
  track: null, examTarget: 'LGS' };
const stu2 = { ...stu, id: 's2', externalRef: 'STU-0002', displayName: 'Can Demir' };
const evaluation = (student, status) => ({ id: 'e1', student, period: { id: 'p1', name: '2025–2026 1. Dönem' },
  title: 'Dönem sonu değerlendirmesi', status,
  scale: { kind: 'numeric_5', min: 1, max: 5, labels: [{ value: 1, label: 'Başlangıç' }, { value: 5, label: 'Çok iyi' }] },
  sections: [{ id: 'a', title: 'Akademik', criteria: [
    { criterionId: 'k1', title: 'Derse katılım', value: 5, label: 'Çok iyi', previousValue: 4, change: 'up', note: 'Aktif' },
    { criterionId: 'k2', title: 'Ödev düzeni', value: 2, label: 'Gelişmeli', previousValue: 3, change: 'down', note: 'Veli 0532 123 45 67 ile görüşüldü' },
    { criterionId: 'k3', title: 'Okuma', value: null, label: null, previousValue: null, change: null, note: null }] }],
  average: 4.2, previousAverage: 3.9, comment: 'Çok gelişti.\nTebrikler.', strengths: ['Düzenli'], improvements: ['Ödev'],
  evaluator: { id: 't1', displayName: 'Mehmet Hoca' }, publishedAt: status === 'published' ? '2026-10-10T09:00:00+03:00' : null,
  reads: [{ kind: 'guardian', person: { id: 'g', displayName: 'GİZLİ OKUYAN' }, readAt: '2026-10-11T10:00:00Z' }],
  autoMetrics: { planAdherencePercent: 81.5, questions: { count: 1240, previousCount: 900 }, accuracyPercent: 71.25,
    examTrend: { nets: [{ examId: 'x', name: 'LGS-1', date: '2026-09-20', totalNet: 61.5 }], lastNet: 61.5, deltaFromPrevious: 3 },
    attendance: { present: 40, absent: 2, late: 1, excused: 1, attendanceRatePercent: 95.45 },
    homework: { assigned: 20, completed: 16, incomplete: 1, notDone: 2, reviewed: 12, completionRatePercent: 82.5 } } });
const bellSlots = [
  ...['mon', 'tue', 'wed', 'thu', 'fri'].flatMap((day) => [
    { id: `${day}-1`, dayOfWeek: day, startTime: '09:00', endTime: '09:40', kind: 'lesson', order: 1 },
    { id: `${day}-b`, dayOfWeek: day, startTime: '09:40', endTime: '09:50', kind: 'break', order: 2 },
    { id: `${day}-2`, dayOfWeek: day, startTime: day === 'fri' ? '10:00' : '09:50', endTime: '10:30', kind: 'lesson', order: 3 }]),
];
const entries = [
  { id: 'e1', dayOfWeek: 'mon', bellSlotId: 'mon-1', subject: { id: 'mat', name: 'Matematik' }, teacher: { id: 't1', displayName: 'Mehmet Hoca' }, room: 'A1', status: 'active' },
  { id: 'e2', dayOfWeek: 'fri', bellSlotId: 'fri-2', subject: { id: 'tr', name: 'Türkçe' }, teacher: null, room: null, status: 'active' },
  { id: 'e3', dayOfWeek: 'tue', bellSlotId: 'eski', subject: { id: 'fen', name: 'Fen' }, teacher: null, room: null, status: 'orphaned' },
];
const session = (id, taken, absent) => ({ id, taken, takenAt: taken ? '2026-10-13T06:05:00Z' : null,
  takenBy: taken ? { id: 't1', displayName: 'Mehmet Hoca' } : null,
  counts: { enrolled: 20, present: 20 - absent, absent, late: 0, excused: 0 },
  scheduleItem: { id: id, date: '2026-10-13', startsAt: '2026-10-13T06:00:00Z', endsAt: '2026-10-13T06:40:00Z',
    subject: { id: 'mat', name: 'Matematik' }, teacher: { id: 't1', displayName: 'Mehmet Hoca' },
    classGroup: { id: 'c8a', name: '8-A' }, status: id === 'iptal' ? 'cancelled' : 'scheduled' } });

const out = {};
out.fmt = { num: d.num(1419.5, 1), neg: d.num(-0.004, 2), money: d.money(1234567), dec: d.dec(12.5), dec0: d.dec(12),
  pct: d.pct(96.45), date: d.fmtDate('2026-10-13T00:00:00Z'), dt: d.fmtDateTime('2026-10-13T17:05:00Z'),
  time: d.fmtTime('2026-10-13T06:00:00Z'), month: d.monthName('2026-10'),
  mask: d.maskPhone('Veli 0532 123 45 67 · +90 532 1234567 · tel:05321234567 · no 12345678901 · 0*** *** ** 67') };
let maxRunning = 0; let running = 0; const progress = [];
out.mapLimit = await d.mapLimit([1, 2, 3, 4, 5, 6], 2, async (value) => {
  running += 1; maxRunning = Math.max(maxRunning, running);
  await new Promise((resolve) => setTimeout(resolve, 5)); running -= 1; return value * 2;
}, (done, total) => progress.push(`${done}/${total}`));
out.maxRunning = maxRunning; out.progress = progress;

const models = {};
models.report_card = d.reportCardDoc([evaluation(stu, 'published')]);
models.report_card_bulk = d.reportCardDoc([evaluation(stu, 'published'), evaluation(stu2, 'draft')], { title: '8-A karneleri' });
models.exam_result = d.examResultDoc({ exam: { id: 'x', name: 'LGS Deneme 3', type: 'lgs', publisher: 'Yayın', date: '2026-10-01', questionTotal: 90 },
  bookletType: 'A', absent: false, totalNet: 72.5, estimatedScore: { value: 412.3, isEstimate: true, label: 'Tahmini LGS puanı' },
  ranks: { institution: { rank: 3, of: 120 }, classGroup: { rank: 1, of: 30 }, publisherNational: null },
  classAverageNet: 60.2, institutionAverageNet: 58, targetNet: 80, distanceToTargetNet: 7.5,
  sections: [{ sectionId: 's1', subject: { id: 'tr', name: 'Türkçe' }, title: 'Türkçe', questionCount: 20, correct: 17, wrong: 2, blank: 1, net: 16.33, classAverageNet: 14, institutionAverageNet: 13.5 },
    { sectionId: 's2', subject: { id: 'mat', name: 'Matematik' }, title: 'Matematik', questionCount: 20, correct: 10, wrong: 6, blank: 4, net: 8, classAverageNet: 11, institutionAverageNet: 10 }],
  weakTopics: [{ topic: { id: 't', name: 'Üslü sayılar' }, wrong: 3, blank: 1, questionCount: 4, errorRate: 0.75, examCount: 2, inReviewQueue: true }] }, stu);
models.exam_result_absent = d.examResultDoc({ exam: { name: 'LGS Deneme 4', date: '2026-10-08' }, absent: true }, stu);
models.exam_analysis = d.examAnalysisDoc({ exam: { id: 'x', name: 'LGS Deneme 3', date: '2026-10-01', resultCount: 58 },
  participantCount: 56, absentCount: 2, averageNet: 60.25, medianNet: 61,
  sections: [{ sectionId: 's1', subject: { id: 'tr', name: 'Türkçe' }, averageNet: 14, maxNet: 20 }],
  classGroups: [{ classGroup: { id: 'c8a', name: '8-A' }, averageNet: 62, studentCount: 28 }, { classGroup: { id: 'c8b', name: '8-B' }, averageNet: 58.5, studentCount: 28 }],
  distribution: [{ fromNet: 0, toNet: 30, count: 4 }, { fromNet: 30, toNet: 60, count: 20 }],
  ranking: [{ rank: 1, student: stu, totalNet: 88.5, publisherNationalRank: 120 }],
  topMissedTopics: [{ topic: { id: 't', name: 'Üslü sayılar' }, errorRate: 0.62, wrongCount: 35, questionNos: [3, 7] }] }, { classGroupName: '' });
models.exam_results = d.examResultsDoc({ id: 'x', name: 'LGS Deneme 3', date: '2026-10-01', sections: [
  { id: 's1', title: 'Türkçe', order: 1, subject: { id: 'tr', name: 'Türkçe', shortName: 'TÜR' } },
  { id: 's2', title: 'Matematik', order: 2, subject: { id: 'mat', name: 'Matematik', shortName: 'MAT' } }] },
  [{ student: stu2, absent: false, sections: [{ sectionId: 's1', net: 10 }, { sectionId: 's2', net: 5.5 }], totalNet: 15.5, estimatedScore: 300, ranks: { institution: 9 } },
   { student: stu, absent: false, sections: [{ sectionId: 's1', net: 18 }, { sectionId: 's2', net: 17.25 }], totalNet: 35.25, estimatedScore: 450.5, ranks: { institution: 1 } },
   { student: { ...stu, id: 's3', displayName: 'Ece Kaya' }, absent: true, sections: [], totalNet: null, ranks: {} }], { classGroupName: '8-A' });
models.attendance_day = d.attendanceDayDoc('2026-10-13', [session('b', false, 0), session('a', true, 2), session('iptal', false, 0)]);
models.attendance_sheet = d.attendanceSheetDoc({ session: session('a', true, 1), rows: [
  { student: stu2, status: 'absent', note: null, lateMinutes: null }, { student: stu, status: 'late', lateMinutes: 7, note: 'Servis' },
  { student: { ...stu, id: 's9', displayName: 'Zeynep Ak' }, status: null }] });
models.attendance_matrix = d.attendanceMatrixDoc({ group: { id: 'c8a', name: '8-A', kind: 'km_class' }, month: '2026-10',
  days: Array.from({ length: 31 }, (_, i) => `2026-10-${String(i + 1).padStart(2, '0')}`),
  rows: [{ student: stu, ratePercent: 96.4, cells: [{ date: '2026-10-01', status: 'present', sessions: 6 }, { date: '2026-10-02', status: 'absent', sessions: 6 }, { date: '2026-10-03', status: 'excused', sessions: 6 }, { date: '2026-10-05', status: 'partial', sessions: 6 }] },
    { student: stu2, ratePercent: 72, cells: [] }] });
models.absentee_list = d.absenteeListDoc([{ student: stu, absenceRatePercent: 12.5, absentSessions: 10, sessions: 80, consecutiveAbsentDays: 2, lastAbsentOn: '2026-10-10' },
  { student: stu2, absenceRatePercent: 25, absentSessions: 20, sessions: 80, consecutiveAbsentDays: 4, lastAbsentOn: '2026-10-12' }], { from: '2026-09-15', to: '2026-10-13', groupName: '8-A' });
models.timetable_class = d.classTimetableDoc({ classGroup: { id: 'c8a', name: '8-A' }, bellSlots, entries, validFrom: '2026-09-15', validTo: null });
models.timetable_teacher = d.teacherTimetableDoc({ teacher: { id: 't1', displayName: 'Mehmet Hoca' }, bellSlots, date: '2026-10-13',
  groups: [{ group: { id: 'c8a', name: '8-A' }, entries }, { group: { id: 'c8b', name: '8-B' }, entries: [
    { id: 'z', dayOfWeek: 'wed', bellSlotId: 'wed-2', subject: { id: 'mat', name: 'Matematik' }, teacher: { id: 't1', displayName: 'Mehmet Hoca' }, room: 'B2', status: 'active' },
    { id: 'y', dayOfWeek: 'wed', bellSlotId: 'wed-1', subject: { id: 'mat', name: 'Matematik' }, teacher: { id: 't2', displayName: 'Başka' }, room: 'B2', status: 'active' }] }] });
const hw = (id, status, due, stats) => ({ id, title: `Ödev ${id}`, subject: { id: 'mat', name: 'Matematik' }, pageFrom: 10, pageTo: 14,
  topics: [], dueAt: due, createdAt: '2026-10-01T09:00:00Z', createdBy: { id: 't1', displayName: 'Mehmet Hoca' }, stats, status });
models.homework_list = d.homeworkListDoc([hw('2', 'published', '2026-10-20T20:59:00Z', { assignedCount: 30, completedCount: 21, overdueCount: 3 }),
  hw('1', 'cancelled', '2026-10-15T20:59:00Z', { assignedCount: 30, completedCount: 0, overdueCount: 0 })], { scope: '8-A' });
models.homework_student = d.studentHomeworkDoc(stu, [{ id: 'h', title: 'Paragraf', subject: { name: 'Türkçe' }, resource: { id: 'r', title: 'Kaynak' },
  pageFrom: 3, pageTo: 5, dueAt: '2026-10-14T20:59:00Z', status: 'completed', completion: { correct: 18, wrong: 2, blank: 0 } },
  { id: 'h2', title: 'Problemler', subject: null, resource: null, dueAt: '2026-10-12T20:59:00Z', status: 'overdue', completion: null }]);
models.study_plan = d.studyPlanDoc(stu, { weekStart: '2026-10-12', weekEnd: '2026-10-18',
  summary: { plannedTasks: 10, doneTasks: 7, plannedMinutes: 600, doneMinutes: 420, adherencePercent: 70 },
  days: [{ date: '2026-10-12', dayOfWeek: 'mon', availableMinutes: 120, plannedMinutes: 90, doneMinutes: 60, tasks: [
    { id: 'k', title: 'Üslü sayılar tekrar', type: 'review', status: 'done', order: 1, startTime: '18:00', endTime: '18:45', estimatedMinutes: 45,
      subject: { name: 'Matematik' }, topic: { name: 'Üslü sayılar' } }] },
    { date: '2026-10-13', dayOfWeek: 'tue', holiday: { title: 'Cumhuriyet' }, tasks: [] },
    { date: '2026-10-14', dayOfWeek: 'wed', tasks: [] }] });
models.finance_statement = d.statementDoc(stu, { from: '2026-09-01', to: '2026-10-13', openingBalanceMinor: 0, closingBalanceMinor: 1250000, creditMinor: 5000,
  lines: [{ date: '2026-09-01', kind: 'charge', description: 'Eylül taksiti', chargeMinor: 2500000, paymentMinor: 0, balanceMinor: 2500000, refId: 'CHG-1' },
    { date: '2026-09-05', kind: 'payment', description: 'Nakit', chargeMinor: 0, paymentMinor: 1250000, balanceMinor: 1250000, refId: 'BBD-0001' },
    { date: '2026-09-06', kind: 'payment_voided', description: 'İptal', chargeMinor: 0, paymentMinor: 0, balanceMinor: 1250000, refId: null }] });
models.finance_receipt = d.receiptDoc({ receiptNo: 'BBD-000123', issuedAt: '2026-10-13T08:00:00Z',
  institution: { name: 'BBD Akademi', address: 'Moda', taxInfo: 'Kadıköy VD 123' }, student: stu,
  payment: { id: 'p', receiptNo: 'BBD-000123', paidAt: '2026-10-13T08:00:00Z', amountMinor: 1250000, method: 'cash', status: 'voided',
    allocations: [{ chargeId: 'c', description: 'Ekim taksiti', amountMinor: 1200000 }], creditAddedMinor: 50000, creditUsedMinor: 0,
    note: null, recordedBy: { id: 'k', displayName: 'Kasa' }, voidedAt: '2026-10-13T09:00:00Z', voidReason: 'Yanlış tutar' },
  balanceAfterMinor: 0, creditAfterMinor: 50000 });
models.book_request = d.bookRequestDoc({ id: 'b', ref: 'KT-0042', student: stu, status: 'delivered',
  product: { productId: 'p', title: 'LGS Matematik Soru Bankası', isbn: '978-0', publisher: 'Yayın', priceMinor: 45000 }, quantity: 2, totalMinor: 90000,
  note: null, requestedAt: '2026-10-01T09:00:00Z', requestedBy: { id: 'g', displayName: 'Veli Yılmaz' },
  decision: { decidedAt: '2026-10-02T09:00:00Z', by: { id: 'a', displayName: 'Müdür' }, paymentChoice: 'cari' },
  order: { orderId: 'o', incrementId: '100045', status: 'complete' }, deliveredAt: '2026-10-05T09:00:00Z',
  history: [{ status: 'requested', at: '2026-10-01T09:00:00Z', actorLabel: 'Veli' }, { status: 'delivered', at: '2026-10-05T09:00:00Z', actorLabel: null }] });
models.book_sale = d.bookSaleDoc({ id: 's', ref: 'KS-2026-0007', paymentMethod: 'cari', studentCount: 2, totalMinor: 100000,
  note: 'Dönem başı', createdAt: '2026-10-13T09:00:00Z', createdBy: { id: 'u', displayName: 'Müdür' },
  items: [{ productId: 'p', title: 'TYT Matematik', isbn: '978-1', publisher: 'Yayın', unitPriceMinor: 25000, quantity: 2, totalMinor: 50000 }],
  counts: { awaitingPayment: 0, processing: 0, ordered: 1, orderFailed: 1, delivered: 0, cancelled: 0 },
  lines: [{ id: 'l1', ref: 'KS-1', student: stu, status: 'ordered', totalMinor: 50000, order: { incrementId: '100046' } },
    { id: 'l2', ref: 'KS-2', student: { ...stu, displayName: 'Ali Ak 0532 111 22 67' }, status: 'order_failed', totalMinor: 50000, order: null }] });
models.student_deletion = d.studentDeletionDoc({ deleted: 1, blocked: 1, results: [
  { studentId: 'STU-1', displayName: 'Ayşe Yılmaz', deleted: true, message: 'Kalıcı silindi.' },
  { studentId: 'STU-2', displayName: 'Can Demir', deleted: false, code: 'balance_not_zero',
    message: 'Kantin bakiyesi 42,50 ₺ borç — önce tahsil edin ve sıfırlayın.' }] }, { at: '2026-10-13T18:00:00Z' });
models.student_card = d.studentCardDoc({ student: stu, schoolNo: '1234', studentPhoneMasked: '0555 111 22 33',
  guardians: [{ id: 'g2', displayName: 'Baba Yılmaz', phoneMasked: '0*** *** ** 12', slot: 2, canManageCanteen: false, hasAppAccount: false },
    { id: 'g1', displayName: 'Anne Yılmaz', phoneMasked: '0532 *** ** 67', slot: 1, relation: 'mother', primary: true, canManageCanteen: true, hasAppAccount: true }],
  programs: [{ id: 'pr', subject: { name: 'Matematik' }, title: 'LGS Matematik', unitCount: 8, topicCount: 64 }],
  studentGroups: [{ id: 'sg', name: 'Etüt A' }], syncedAt: '2026-10-13T05:00:00Z' });
models.guardian_list = d.guardianListDoc([
  { id: 'g1', displayName: 'Anne Yılmaz', phoneMasked: '0532 123 45 67', hasAppAccount: true, students: [stu, { ...stu2, classGroup: { id: 'c8b', name: '8-B' } }] },
  { id: 'g3', displayName: 'Veli Demir', phoneMasked: '0*** *** ** 99', hasAppAccount: false, students: [{ ...stu2 }] }],
  { classGroupId: 'c8a', classGroupName: '8-A' });
models.guardian_list_details = d.guardianListDoc(d.guardiansFromDetails([
  { student: stu2, guardians: [{ id: 'x', displayName: 'Baba Demir', phoneMasked: '0*** *** ** 12', slot: 2, relation: 'father', hasAppAccount: false },
    { id: 'y', displayName: 'Anne Demir', phoneMasked: '0505 123 45 67', slot: 1, relation: null, primary: true, hasAppAccount: true }] },
  { student: stu, guardians: [] }, null]), { classGroupId: 'c8a', classGroupName: '8-A' });
models.canteen_statement = d.canteenDoc(stu, { balanceMinor: 15000, debtMinor: 0, dailyLimitMinor: null, isBlocked: false, guardianBlocked: true },
  [{ id: 't2', at: '2026-10-12T10:00:00Z', totalMinor: 2500, reversed: true, items: [{ name: 'Tost', qty: 1 }] },
   { id: 't1', at: '2026-10-11T10:00:00Z', totalMinor: 3500, reversed: false, items: [{ name: 'Su', qty: 2 }, { name: 'Simit', qty: 1 }] }],
  { from: '2026-10-01', to: '2026-10-13' });
out.models = models;
// Akademi README §14 madde 85: öğretmen kontrolü sayılır; eski Akademi yeni sayaçları göndermez.
out.tally = [d.homeworkTally({ assigned: 1250, completed: 1000, incomplete: 3, notDone: 0 }),
  d.homeworkTally({ assigned: 5, completed: 5 }), d.homeworkTally(null)];
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    folder = tmp_path_factory.mktemp("belge-mantik")
    (folder / "documents-logic.mjs").write_bytes((PANEL / "documents-logic.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    completed = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                               check=False, timeout=60, cwd=folder)
    assert completed.returncode == 0, completed.stderr
    data: dict[str, Any] = json.loads(completed.stdout)
    return data


def _texts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _texts(item)]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


def _section(model: dict[str, Any], title: str, part: int = 0) -> dict[str, Any]:
    for section in model["document"]["parts"][part]["sections"]:
        if section.get("title") == title:
            return section
    raise AssertionError(f"bölüm yok: {title}")


def test_turkce_bicim(result: dict[str, Any]) -> None:
    fmt = result["fmt"]
    assert fmt["num"] == "1.419,5" and fmt["neg"] == "0,00" and fmt["money"] == "12.345,67 ₺"
    assert (fmt["dec"], fmt["dec0"], fmt["pct"]) == ("12,5", "12", "%96,5")
    assert fmt["date"] == "13.10.2026" and fmt["dt"] == "13.10.2026 20:05" and fmt["time"] == "09:00"
    assert fmt["month"] == "Ekim 2026"
    assert fmt["mask"] == ("Veli 0*** *** ** 67 · 0*** *** ** 67 · tel:0*** *** ** 67 · "
                           "no 12345678901 · 0*** *** ** 67")


def test_toplu_isin_eszamanliligi_sinirli(result: dict[str, Any]) -> None:
    assert result["mapLimit"] == [2, 4, 6, 8, 10, 12]
    assert result["maxRunning"] == 2
    assert result["progress"][-1] == "6/6" and len(result["progress"]) == 6


def test_her_model_bilinen_turde_ve_sunucuda_pdf_olur(result: dict[str, Any]) -> None:
    letterhead = document_pdf.Letterhead(name="BBD Akademi", detail="Moda")
    stamp = document_pdf.Stamp(number="AK-T", issued="13.10.2026 20:00", author="Test")
    for name, model in result["models"].items():
        assert model["kind"] in KINDS, name
        document = document_pdf.parse(model["document"])
        assert document_pdf.render(document, letterhead, stamp).startswith(b"%PDF"), name


def test_hicbir_belgede_acik_telefon_yok(result: dict[str, Any]) -> None:
    phone = re.compile(r"(?:\+?90)?\s?0?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
    for name, model in result["models"].items():
        leaked = [text for text in _texts(model) if phone.search(text)]
        assert leaked == [], f"{name}: {leaked}"


def test_karne(result: dict[str, Any]) -> None:
    model = result["models"]["report_card"]
    part = model["document"]["parts"][0]
    assert part["title"] == "Öğrenci Gelişim Karnesi" and part["watermark"] == ""
    assert ["Öğrenci", "Ayşe Yılmaz"] in part["meta"] and ["Sınıf", "8-A"] in part["meta"]
    tiles = part["sections"][0]
    assert tiles["kind"] == "tiles" and tiles["items"][0] == {
        "label": "Genel ortalama", "value": "4,2 / 5", "tone": "good"}
    criteria = _section(model, "Akademik")
    assert criteria["rows"][0][1] == "5 · Çok iyi"
    assert criteria["rows"][1][1] == {"text": "2 · Gelişmeli", "tone": "warn", "bold": False}
    assert criteria["rows"][1][3]["text"] == "↓ düştü"
    assert criteria["rows"][2][1]["text"] == "—"
    assert "GİZLİ OKUYAN" not in json.dumps(model, ensure_ascii=False)   # okundu bilgisi yok
    assert part["sections"][-1] == {"kind": "signatures",
                                    "items": ["Değerlendiren öğretmen", "Kurum müdürü", "Veli"]}
    bulk = result["models"]["report_card_bulk"]["document"]
    assert bulk["title"] == "8-A karneleri" and len(bulk["parts"]) == 2
    assert bulk["parts"][1]["watermark"] == "Taslak"
    # ödev: öğretmen kontrolü sayılır (Akademi README §14 madde 85; uygulamayla aynı metin)
    assert {"label": "Ödev tamamlama", "value": "16 / 20 tamamlandı · 1 eksik · 2 yapmadı"} in tiles["items"]


def test_odev_sayac_satiri(result: dict[str, Any]) -> None:
    assert result["tally"] == ["1.000 / 1.250 tamamlandı · 3 eksik", "5 / 5 tamamlandı", "0 / 0 tamamlandı"]


def test_deneme_belgeleri(result: dict[str, Any]) -> None:
    exam = result["models"]["exam_result"]
    table = _section(exam, "Ders bazında sonuç")
    assert table["footer"] == ["Toplam", "40", "27", "8", "5", "72,5", "60,2", "58"]
    assert table["rows"][1][5] == {"text": "8", "tone": "warn", "bold": True}   # sınıf ort. altında
    weak = _section(exam, "Gelişmesi gereken konular")
    assert weak["rows"][0][4] == "%75"
    absent = result["models"]["exam_result_absent"]["document"]["parts"][0]["sections"]
    assert absent[0] == {"kind": "note", "text": "Öğrenci bu denemeye katılmadı."}
    listing = result["models"]["exam_results"]
    rows = listing["document"]["parts"][0]["sections"][0]["rows"]
    assert [row[0] if isinstance(row, list) else row["cells"][0] for row in rows] == [
        "Ayşe Yılmaz", "Can Demir", "Ece Kaya"]                            # nete göre, katılmayan sonda
    assert listing["document"]["parts"][0]["sections"][0]["headers"][3:5] == ["TÜR", "MAT"]


def test_yoklama_belgeleri(result: dict[str, Any]) -> None:
    day = result["models"]["attendance_day"]
    assert day["document"]["orientation"] == "landscape"
    rows = day["document"]["parts"][0]["sections"][1]["rows"]
    assert [row[4]["text"] for row in rows] == ["Bekliyor", "Alındı", "İptal"]  # aynı saat: geliş sırası
    sheet = result["models"]["attendance_sheet"]["document"]["parts"][0]
    statuses = [row[3]["text"] if isinstance(row[3], dict) else row[3] for row in sheet["sections"][1]["rows"]]
    assert statuses == ["Geç", "Yok", "İşaretlenmedi"]                     # ada göre sıralı
    matrix = result["models"]["attendance_matrix"]["document"]
    table = matrix["parts"][0]["sections"][0]
    assert len(table["headers"]) == 33 and table["dense"] is True and matrix["orientation"] == "landscape"
    first = table["rows"][0]
    assert first[1] == "+" and first[2] == {"text": "D", "tone": "bad"} and first[3] == {
        "text": "İ", "tone": "info"} and first[5] == {"text": "K", "tone": "warn"}
    assert table["rows"][1][-1] == {"text": "%72", "tone": "bad", "bold": True}
    absentees = result["models"]["absentee_list"]["document"]["parts"][0]["sections"][0]["rows"]
    assert absentees[0][0] == "Can Demir" and absentees[0][3]["tone"] == "bad"


def test_ders_programi(result: dict[str, Any]) -> None:
    table = result["models"]["timetable_class"]["document"]["parts"][0]["sections"][0]
    assert table["headers"] == ["Ders", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma"]
    assert table["rows"][0][0] == "1. ders\n09:00–09:40"
    assert table["rows"][0][1] == "Matematik\nMehmet Hoca · A1"
    assert table["rows"][1][5] == "Türkçe\n(10:00–10:30)"                  # farklı saat yazılır
    assert len(table["rows"]) == 2                                           # teneffüs satırı yok
    teacher = result["models"]["timetable_teacher"]["document"]["parts"][0]
    grid = teacher["sections"][0]["rows"]
    assert grid[0][1] == "8-A\nMatematik · A1" and grid[1][3] == "8-B\nMatematik · B2"
    assert grid[0][3] == ""                                                  # başka öğretmenin dersi yok
    assert ["Haftalık ders", "2"] in teacher["meta"]


def test_odev_plan_cari_kitap_kart_veli_kantin(result: dict[str, Any]) -> None:
    models = result["models"]
    homework = models["homework_list"]["document"]["parts"][0]["sections"][0]["rows"]
    assert homework[0]["tone"] == "muted" and homework[1][5] == "21 / 30 (%70)"
    plan = models["study_plan"]["document"]["parts"][0]["sections"]
    assert plan[1]["rows"][0][5] == {"text": "Yapıldı", "tone": "good", "bold": False}
    assert plan[2] == {"kind": "note", "title": "Salı 13.10.2026", "text": "Tatil: Cumhuriyet"}
    statement = models["finance_statement"]["document"]["parts"][0]["sections"]
    assert statement[1]["footer"] == ["", "", "Dönem toplamı", "25.000,00 ₺", "12.500,00 ₺",
                                      "12.500,00 ₺", ""]
    assert statement[1]["rows"][2]["tone"] == "muted"
    assert any("KURUM CARİ" in section.get("text", "") for section in statement)
    receipt = models["finance_receipt"]["document"]["parts"][0]
    assert receipt["watermark"] == "İptal" and ["Ödeme şekli", "Nakit"] in receipt["meta"]
    book = models["book_request"]["document"]["parts"][0]
    assert book["title"] == "Kitap Teslim Belgesi"
    assert book["sections"][0]["rows"][0][5] == "900,00 ₺"
    card = models["student_card"]["document"]["parts"][0]
    guardians = _section(models["student_card"], "Veliler")["rows"]
    assert [row[0] for row in guardians] == ["Anne Yılmaz", "Baba Yılmaz"]  # yuva sırası
    assert [row[1] for row in guardians] == ["Anne · birincil", "2. veli"]
    assert card["sections"][0]["items"][4]["value"] == "0*** *** ** 33"
    veli = models["guardian_list"]["document"]["parts"][0]
    assert [row[0] for row in veli["sections"][0]["rows"]] == ["Ayşe Yılmaz", "Can Demir"]
    assert ["Öğrenci", "2"] in veli["meta"]
    detail_rows = models["guardian_list_details"]["document"]["parts"][0]["sections"][0]["rows"]
    assert [(row[2], row[3], row[4]) for row in detail_rows] == [
        ("Anne Demir", "1. veli · birincil", "0*** *** ** 67"), ("Baba Demir", "Baba", "0*** *** ** 12")]
    canteen = models["canteen_statement"]["document"]["parts"][0]["sections"]
    harcama = next(section for section in canteen if section.get("title") == "Harcamalar")
    assert harcama["footer"][2] == "35,00 ₺" and harcama["rows"][1]["tone"] == "muted"
    assert any("KANTİN" in section.get("text", "") for section in canteen)
    assert canteen[1]["text"].startswith("Kantin kullanımı kapalı (veli")


def test_kitap_satisi_ve_kalici_silme_belgeleri(result: dict[str, Any]) -> None:
    """0.5.4: kitap satış belgesi (öğrenci satırı + imza sütunu) ve toplu silme sonucu."""
    sale = result["models"]["book_sale"]
    assert sale["kind"] == "book_sale"
    students = _section(sale, "Öğrenciler")
    assert [row[0] for row in students["rows"]] == ["Ayşe Yılmaz", "Ali Ak 0*** *** ** 67"]
    assert students["footer"][4] == "1.000,00 ₺"
    deletion = result["models"]["student_deletion"]
    assert deletion["kind"] == "student_deletion"
    rows = _section(deletion, "Öğrenciler")["rows"]
    assert rows[0][1]["text"] == "Silindi" and "42,50 ₺" in rows[1][2]
