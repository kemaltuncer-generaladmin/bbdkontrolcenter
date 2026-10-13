// BBD Akademi — belge modelleri (karne, deneme, yoklama, program, ödev, plan,
// kurum cari, kitap, öğrenci kartı, veli listesi, kantin). SAF: DOM yok, import yok.
//
// NEDEN AYRI DOSYA. Bu dosya hiçbir şey import etmez; Node ile doğrudan
// yüklenip şablon testine girer (tests/test_akademi_documents_logic.py).
// Ekranlar veriyi Akademi'den zaten okuyor; burada o veri BELGE MODELİNE
// çevrilir ve `documents.js` modeli `POST /km/documents/preview` ile sunucuya
// verir. Sunucu kurum logolu A4 PDF'i çizer (backend/document_pdf.py).
//
// MODEL: { kind, document: { title, filename, orientation, parts: [Part] } }
//   Part    = { title, subtitle, meta: [[etiket, değer]], watermark, sections: [Section] }
//   Section = tiles | fields {items:[{label,value,tone}]} · table {headers, rows, align,
//             widths, footer, dense, empty} · bars · list · text · note · signatures · break
//   Hücre   = metin ya da {text, tone: good|bad|warn|muted|info|strong, bold}
//
// KVKK. Belgeye yalnız belgenin amacına gereken kişisel veri girer: telefon
// numarası hiçbir belgede açık yazılmaz (`maskPhone`; Akademi zaten maskeli
// gönderir, sunucu da ikinci kez maskeler); karnede okundu bilgisi, kantinde
// ürün dışı ayrıntı yoktur. Kurum carisi ile kantin AYRI belgelerdir (A12).

export const TZ = 'Europe/Istanbul';

const DAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
const RELATIONS = { mother: 'Anne', father: 'Baba', guardian: 'Vasi', grandparent: 'Büyükanne / büyükbaba',
  sibling: 'Kardeş', other: 'Diğer' };
export const DAY_LABEL = {
  mon: 'Pazartesi', tue: 'Salı', wed: 'Çarşamba', thu: 'Perşembe', fri: 'Cuma',
  sat: 'Cumartesi', sun: 'Pazar',
};

// ------------------------------------------------------------------ biçim

/** Türkçe ayraçlı sayı: 1419.5 → '1.419,5'. */
export function num(value, decimals = 0) {
  if (value === null || value === undefined || value === '' || Number.isNaN(Number(value))) return '—';
  const fixed = Math.abs(Number(value)).toFixed(decimals);
  const [whole, fraction] = fixed.split('.');
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  const sign = Number(value) < 0 && Number(fixed) !== 0 ? '-' : '';
  return `${sign}${grouped}${fraction ? `,${fraction}` : ''}`;
}

/** Net / ortalama: en çok iki ondalık, gereksiz sıfır yok (12,5 · 12 · 12,25). */
export function dec(value, max = 2) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  const rounded = Number(Number(value).toFixed(max));
  const places = Number.isInteger(rounded) ? 0 : (String(rounded).split('.')[1] || '').length;
  return num(rounded, Math.min(places, max));
}

/** Kuruş → '12.345,67 ₺'. */
export function money(minor) {
  if (minor === null || minor === undefined || Number.isNaN(Number(minor))) return '—';
  return `${num(Number(minor) / 100, 2)} ₺`;
}

/** Yüzde: 96.45 → '%96,5'. */
export function pct(value, decimals = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `%${dec(Number(value), decimals)}`;
}

/** 'YYYY-MM-DD…' → '13.10.2026'. */
export function fmtDate(iso) {
  if (!iso) return '—';
  const [year, month, day] = String(iso).slice(0, 10).split('-');
  return day && month && year ? `${day}.${month}.${year}` : String(iso);
}

function parts(value) {
  const date = new Date(String(value).replace(/(\.\d{3})\d+/, '$1'));
  if (Number.isNaN(date.getTime())) return null;
  const pieces = new Intl.DateTimeFormat('tr-TR', { timeZone: TZ, year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date);
  return Object.fromEntries(pieces.map((item) => [item.type, item.value]));
}

/** Zaman damgası → '13.10.2026 14:05' (İstanbul). */
export function fmtDateTime(value) {
  if (!value) return '—';
  const p = parts(value);
  return p ? `${p.day}.${p.month}.${p.year} ${p.hour}:${p.minute}` : String(value);
}

/** Zaman damgası ya da 'HH:MM' → '14:05'. */
export function fmtTime(value) {
  if (!value) return '';
  if (/^\d{2}:\d{2}/.test(String(value))) return String(value).slice(0, 5);
  const p = parts(value);
  return p ? `${p.hour}:${p.minute}` : '';
}

const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül',
  'Ekim', 'Kasım', 'Aralık'];

/** '2026-10' → 'Ekim 2026'. */
export function monthName(value) {
  const [year, month] = String(value || '').split('-').map(Number);
  return year && month ? `${MONTHS[month - 1]} ${year}` : String(value || '');
}

/** Türkiye telefonu → '0*** *** ** 67'. Maskeli gelen dokunulmaz.
 *  Geriye bakış (`(?<!…)`) KULLANILMAZ: eski WebKitGTK'da sözdizimi hatasıdır ve
 *  bütün paneli düşürür; önceki karakter yakalanıp aynen geri yazılır. */
const PHONE = /(^|[^\d*])((?:\+?90[\s.-]?)?\(?0?\s?[2-5]\d{2}\)?[\s.-]?\d{3}[\s.-]?\d{2}[\s.-]?(\d{2}))(?![\d*])/g;
export function maskPhone(text) {
  if (text === null || text === undefined) return '';
  return String(text).replace(PHONE, (_, before, _phone, last) => `${before}0*** *** ** ${last}`);
}

function clean(value, fallback = '—') {
  const text = value === null || value === undefined ? '' : String(value).trim();
  return text ? maskPhone(text) : fallback;
}

function studentName(ref) { return clean(ref?.displayName); }
function className(ref) { return clean(ref?.classGroup?.name || ref?.name, '—'); }

/** Dosya adı parçası (sunucu yine ASCII'ye çevirir). */
export function fileHint(...pieces) {
  return pieces.filter(Boolean).map((piece) => String(piece).trim()).join(' ').slice(0, 80);
}

/** Eşzamanlılığı sınırlı eşleme (toplu karne: Akademi'ye aynı anda en çok `limit` istek). */
export async function mapLimit(items, limit, fn, onProgress) {
  const list = [...items];
  const results = new Array(list.length);
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < list.length) {
      const index = next;
      next += 1;
      results[index] = await fn(list[index], index);   // eslint-disable-line no-await-in-loop
      done += 1;
      onProgress?.(done, list.length);
    }
  }
  await Promise.all(Array.from({ length: Math.max(1, Math.min(limit, list.length)) }, worker));
  return results;
}

function documentOf(kind, title, partList, { orientation = 'portrait', filename = '' } = {}) {
  return { kind, document: { title, filename: filename || title, orientation, parts: partList } };
}

/** Aynı türden belgeleri tek baskı işine birleştirir (her biri kendi sayfasından başlar). */
export function mergeDocs(models, { title = '', filename = '' } = {}) {
  const list = (models || []).filter((model) => model?.document);
  if (!list.length) return null;
  if (list.length === 1) return list[0];
  return documentOf(list[0].kind, title || list[0].document.title, list.flatMap((model) => model.document.parts), {
    orientation: list.some((model) => model.document.orientation === 'landscape') ? 'landscape' : 'portrait',
    filename: filename || `${title || list[0].document.title} ${list.length}`,
  });
}

function cell(text, tone = '', bold = false) {
  return tone || bold ? { text: String(text ?? ''), tone, bold } : String(text ?? '');
}

const CHANGE = { up: '↑ yükseldi', down: '↓ düştü', same: '= aynı' };
const CHANGE_TONE = { up: 'good', down: 'bad', same: 'muted' };

// ---------------------------------------------------------- 1. karne

/** Tek değerlendirme (AdminEvaluation) → karne parçası. */
export function reportCardPart(evaluation) {
  const ev = evaluation || {};
  const scaleMax = ev.scale?.max || 5;
  const metrics = ev.autoMetrics || {};
  const meta = [
    ['Öğrenci', studentName(ev.student)],
    ['Öğrenci no', clean(ev.student?.externalRef)],
    ['Sınıf', className(ev.student)],
    ['Dönem', clean(ev.period?.name)],
    ['Değerlendiren', clean(ev.evaluator?.displayName)],
    ['Yayım', ev.publishedAt ? fmtDate(ev.publishedAt) : 'Yayımlanmadı'],
  ];
  const averageChange = ev.average !== null && ev.average !== undefined
    && ev.previousAverage !== null && ev.previousAverage !== undefined
    ? Number(ev.average) - Number(ev.previousAverage) : null;
  const tiles = [
    { label: 'Genel ortalama', value: ev.average === null || ev.average === undefined ? '—'
      : `${dec(ev.average)} / ${scaleMax}`,
    tone: averageChange === null ? '' : averageChange >= 0 ? 'good' : 'bad' },
    { label: 'Önceki dönem', value: ev.previousAverage === null || ev.previousAverage === undefined
      ? '—' : `${dec(ev.previousAverage)} / ${scaleMax}` },
  ];
  if (metrics.attendance) tiles.push({ label: 'Devam oranı', value: pct(metrics.attendance.attendanceRatePercent) });
  if (metrics.homework) {
    tiles.push({ label: 'Ödev tamamlama', value: `${num(metrics.homework.completed)} / ${num(metrics.homework.assigned)}` });
  }
  if (metrics.planAdherencePercent !== undefined && metrics.planAdherencePercent !== null) {
    tiles.push({ label: 'Plan uyumu', value: pct(metrics.planAdherencePercent) });
  }
  if (metrics.examTrend?.lastNet !== undefined && metrics.examTrend?.lastNet !== null) {
    tiles.push({ label: 'Son deneme neti', value: dec(metrics.examTrend.lastNet) });
  }
  if (metrics.questions) tiles.push({ label: 'Çözülen soru', value: num(metrics.questions.count) });
  if (metrics.accuracyPercent !== undefined && metrics.accuracyPercent !== null) {
    tiles.push({ label: 'Doğruluk', value: pct(metrics.accuracyPercent) });
  }

  const sections = [{ kind: 'tiles', items: tiles.slice(0, 8), columns: 4 }];
  for (const block of ev.sections || []) {
    sections.push({
      kind: 'table', title: clean(block.title, 'Bölüm'),
      headers: ['Kriter', 'Değerlendirme', 'Önceki', 'Değişim', 'Not'],
      align: 'LCCCL', widths: [3.2, 2, 0.9, 1.3, 3],
      rows: (block.criteria || []).map((item) => [
        clean(item.title),
        item.value === null || item.value === undefined ? cell('—', 'muted')
          : cell(`${item.value}${item.label ? ` · ${item.label}` : ''}`,
            item.value <= Math.max(1, Math.floor(scaleMax / 2)) ? 'warn' : ''),
        item.previousValue === null || item.previousValue === undefined ? '—' : String(item.previousValue),
        item.change ? cell(CHANGE[item.change] || '', CHANGE_TONE[item.change]) : '—',
        clean(item.note, ''),
      ]),
      empty: 'Bu bölümde kriter yok.',
    });
  }
  const labels = (ev.scale?.labels || []).map((item) => `${item.value} = ${item.label}`).join(' · ');
  if (labels) sections.push({ kind: 'note', text: `Ölçek: ${labels}` });

  const nets = metrics.examTrend?.nets || [];
  if (nets.length) {
    sections.push({ kind: 'table', title: 'Deneme gelişimi', headers: ['Deneme', 'Tarih', 'Toplam net'],
      align: 'LCR', widths: [4, 1.4, 1.2], keep: true,
      rows: nets.map((item) => [clean(item.name), fmtDate(item.date), dec(item.totalNet)]) });
  }
  if (metrics.attendance) {
    const a = metrics.attendance;
    sections.push({ kind: 'fields', title: 'Devam durumu', columns: 4, items: [
      { label: 'Katıldı', value: num(a.present) }, { label: 'Devamsız', value: num(a.absent), tone: a.absent ? 'bad' : '' },
      { label: 'Geç', value: num(a.late) }, { label: 'İzinli', value: num(a.excused) },
    ] });
  }
  if ((ev.strengths || []).length) sections.push({ kind: 'list', title: 'Güçlü yönler', items: ev.strengths.map((item) => clean(item)) });
  if ((ev.improvements || []).length) {
    sections.push({ kind: 'list', title: 'Gelişim alanları', items: ev.improvements.map((item) => clean(item)) });
  }
  if (ev.comment) sections.push({ kind: 'text', title: 'Öğretmen görüşü', text: clean(ev.comment) });
  sections.push({ kind: 'signatures', items: ['Değerlendiren öğretmen', 'Kurum müdürü', 'Veli'] });

  return {
    title: 'Öğrenci Gelişim Karnesi',
    subtitle: [ev.period?.name, ev.title].filter(Boolean).map((item) => clean(item)).join(' · '),
    meta,
    watermark: ev.status === 'draft' ? 'Taslak' : '',
    sections,
  };
}

/** Bir ya da çok değerlendirme → karne belgesi (toplu: her öğrenci kendi sayfasından başlar). */
export function reportCardDoc(evaluations, { title = '' } = {}) {
  const list = (evaluations || []).filter(Boolean);
  const first = list[0];
  const single = list.length === 1;
  return documentOf('report_card', title || (single ? 'Öğrenci Gelişim Karnesi' : 'Gelişim karneleri'),
    list.map(reportCardPart), {
      filename: single ? fileHint(first?.student?.displayName, first?.period?.name)
        : fileHint(title || 'toplu', first?.period?.name, `${list.length} ogrenci`),
    });
}

// ---------------------------------------------------------- 2. deneme

/** Öğrencinin deneme sonucu (StudentExamResult). */
export function examResultDoc(result, student = {}) {
  const r = result || {};
  const exam = r.exam || {};
  const rank = (info) => (info ? `${num(info.rank)} / ${num(info.of)}` : '—');
  const sections = [];
  if (r.absent) {
    sections.push({ kind: 'note', text: 'Öğrenci bu denemeye katılmadı.' });
  } else {
    sections.push({ kind: 'tiles', columns: 4, items: [
      { label: 'Toplam net', value: dec(r.totalNet), tone: 'info' },
      { label: r.estimatedScore?.label || 'Tahmini puan',
        value: r.estimatedScore ? dec(r.estimatedScore.value) : '—' },
      { label: 'Kurum sırası', value: rank(r.ranks?.institution) },
      { label: 'Sınıf sırası', value: rank(r.ranks?.classGroup) },
      { label: 'Sınıf ortalaması', value: dec(r.classAverageNet) },
      { label: 'Kurum ortalaması', value: dec(r.institutionAverageNet) },
      { label: 'Hedef net', value: dec(r.targetNet) },
      { label: 'Hedefe uzaklık', value: dec(r.distanceToTargetNet) },
    ] });
    const rows = (r.sections || []).map((item) => {
      const below = item.classAverageNet !== null && item.classAverageNet !== undefined
        && Number(item.net) < Number(item.classAverageNet);
      return [clean(item.subject?.name || item.title), num(item.questionCount), num(item.correct),
        num(item.wrong), num(item.blank), cell(dec(item.net), below ? 'warn' : '', true),
        dec(item.classAverageNet), dec(item.institutionAverageNet)];
    });
    const total = (key) => (r.sections || []).reduce((sum, item) => sum + Number(item[key] || 0), 0);
    sections.push({ kind: 'table', title: 'Ders bazında sonuç',
      headers: ['Ders', 'Soru', 'Doğru', 'Yanlış', 'Boş', 'Net', 'Sınıf ort.', 'Kurum ort.'],
      align: 'LRRRRRRR', widths: [3, 1, 1, 1, 1, 1.1, 1.2, 1.2], rows,
      footer: ['Toplam', num(total('questionCount')), num(total('correct')), num(total('wrong')),
        num(total('blank')), dec(r.totalNet), dec(r.classAverageNet), dec(r.institutionAverageNet)] });
    if ((r.weakTopics || []).length) {
      sections.push({ kind: 'table', title: 'Gelişmesi gereken konular',
        headers: ['Konu', 'Yanlış', 'Boş', 'Soru', 'Hata oranı'], align: 'LRRRR',
        widths: [4, 1, 1, 1, 1.2],
        rows: r.weakTopics.slice(0, 25).map((item) => [clean(item.topic?.name), num(item.wrong),
          num(item.blank), num(item.questionCount), pct(Number(item.errorRate) * (item.errorRate <= 1 ? 100 : 1))]) });
    }
  }
  sections.push({ kind: 'signatures', items: ['Rehber öğretmen', 'Veli'] });
  const who = student.displayName ? student : {};
  return documentOf('exam_result', 'Deneme Sonuç Belgesi', [{
    title: 'Deneme Sonuç Belgesi',
    subtitle: [exam.name, exam.publisher].filter(Boolean).map((item) => clean(item)).join(' · '),
    meta: [['Öğrenci', studentName(who)], ['Öğrenci no', clean(who.externalRef)],
      ['Sınıf', className(who)], ['Deneme tarihi', fmtDate(exam.date)],
      ['Kitapçık', clean(r.bookletType)], ['Soru sayısı', num(exam.questionTotal)]],
    sections,
  }], { filename: fileHint(who.displayName, exam.name) });
}

/** Deneme analizi (ExamAnalysis) — sınıf/kurum geneli. */
export function examAnalysisDoc(analysis, { classGroupName = '' } = {}) {
  const a = analysis || {};
  const exam = a.exam || {};
  const sections = [{ kind: 'tiles', columns: 4, items: [
    { label: 'Katılan', value: num(a.participantCount) },
    { label: 'Katılmayan', value: num(a.absentCount), tone: a.absentCount ? 'warn' : '' },
    { label: 'Ortalama net', value: dec(a.averageNet), tone: 'info' },
    { label: 'Medyan net', value: dec(a.medianNet) },
  ] }];
  if ((a.sections || []).length) {
    sections.push({ kind: 'table', title: 'Ders ortalamaları', headers: ['Ders', 'Ortalama net', 'En yüksek net'],
      align: 'LRR', widths: [4, 1.4, 1.4], keep: true,
      rows: a.sections.map((item) => [clean(item.subject?.name), dec(item.averageNet), dec(item.maxNet)]) });
  }
  if ((a.classGroups || []).length > 1) {
    sections.push({ kind: 'bars', title: 'Sınıf ortalamaları (net)',
      items: a.classGroups.map((item) => ({ label: `${clean(item.classGroup?.name)} (${num(item.studentCount)})`,
        value: Number(item.averageNet || 0), display: dec(item.averageNet) })) });
  }
  if ((a.distribution || []).length) {
    sections.push({ kind: 'bars', title: 'Net dağılımı (öğrenci sayısı)',
      items: a.distribution.map((item) => ({ label: `${dec(item.fromNet)} – ${dec(item.toNet)} net`,
        value: Number(item.count || 0), display: `${num(item.count)} öğrenci` })) });
  }
  if ((a.ranking || []).length) {
    sections.push({ kind: 'table', title: 'Sıralama', headers: ['Sıra', 'Öğrenci', 'No', 'Sınıf', 'Toplam net', 'Türkiye sırası'],
      align: 'RLLLRR', widths: [0.7, 3, 1.2, 1.2, 1.2, 1.3],
      rows: a.ranking.map((item) => [num(item.rank), studentName(item.student), clean(item.student?.externalRef),
        className(item.student), dec(item.totalNet), item.publisherNationalRank ? num(item.publisherNationalRank) : '—']) });
  }
  if ((a.topMissedTopics || []).length) {
    sections.push({ kind: 'table', title: 'En çok yanlış yapılan konular', headers: ['Konu', 'Hata oranı', 'Yanlış', 'Soru no'],
      align: 'LRRL', widths: [4, 1.1, 1, 2],
      rows: a.topMissedTopics.slice(0, 30).map((item) => [clean(item.topic?.name),
        pct(Number(item.errorRate) * (item.errorRate <= 1 ? 100 : 1)), num(item.wrongCount),
        (item.questionNos || []).join(', ')]) });
  }
  return documentOf('exam_analysis', 'Deneme Analiz Raporu', [{
    title: 'Deneme Analiz Raporu',
    subtitle: [exam.name, exam.publisher, classGroupName || 'Bütün sınıflar'].filter(Boolean).map((item) => clean(item)).join(' · '),
    meta: [['Deneme', clean(exam.name)], ['Tarih', fmtDate(exam.date)], ['Kapsam', clean(classGroupName, 'Bütün sınıflar')],
      ['Sonuç sayısı', num(exam.resultCount)]],
    sections,
  }], { filename: fileHint(exam.name, classGroupName, 'analiz') });
}

/** Deneme sonuç listesi (AdminExamResult[]) — sınıf listesi, yatay. */
export function examResultsDoc(exam, rows, { classGroupName = '' } = {}) {
  const e = exam || {};
  const defs = [...(e.sections || [])].sort((a, b) => (a.order || 0) - (b.order || 0));
  const headers = ['Öğrenci', 'No', 'Sınıf', ...defs.map((item) => item.subject?.shortName || item.title || item.subject?.name || 'Ders'),
    'Toplam net', 'Puan', 'Kurum sırası'];
  const sorted = [...(rows || [])].sort((a, b) => (Number(b.totalNet ?? -1e9) - Number(a.totalNet ?? -1e9))
    || studentName(a.student).localeCompare(studentName(b.student), 'tr'));
  const body = sorted.map((row) => {
    const byId = new Map((row.sections || []).map((item) => [item.sectionId, item]));
    if (row.absent) {
      return { cells: [studentName(row.student), clean(row.student?.externalRef), className(row.student),
        ...defs.map(() => ''), cell('Katılmadı', 'muted'), '', ''], tone: 'muted' };
    }
    return [studentName(row.student), clean(row.student?.externalRef), className(row.student),
      ...defs.map((item) => dec(byId.get(item.id)?.net)), cell(dec(row.totalNet), '', true),
      dec(row.estimatedScore), row.ranks?.institution ? num(row.ranks.institution) : '—'];
  });
  const many = headers.length > 9;
  return documentOf('exam_results', 'Deneme Sonuç Listesi', [{
    title: 'Deneme Sonuç Listesi',
    subtitle: [e.name, classGroupName || 'Bütün sınıflar'].filter(Boolean).map((item) => clean(item)).join(' · '),
    meta: [['Deneme', clean(e.name)], ['Tarih', fmtDate(e.date)], ['Kapsam', clean(classGroupName, 'Bütün sınıflar')],
      ['Öğrenci', num(sorted.length)]],
    sections: [{ kind: 'table', headers, align: `LLL${'R'.repeat(defs.length)}RRR`,
      widths: [3, 1.2, 1, ...defs.map(() => 0.9), 1.1, 1, 1], rows: body, dense: many,
      empty: 'Bu deneme için sonuç yok.' }],
  }], { orientation: many ? 'landscape' : 'portrait', filename: fileHint(e.name, classGroupName, 'sonuclar') });
}

// ---------------------------------------------------------- 3. yoklama

const ATT_LABEL = { present: 'Var', absent: 'Yok', late: 'Geç', excused: 'İzinli' };
const ATT_TONE = { present: '', absent: 'bad', late: 'warn', excused: 'info' };

function sessionTitle(item) {
  const s = item?.scheduleItem || {};
  return [s.classGroup?.name, s.subject?.name].filter(Boolean).join(' · ') || 'Ders';
}

/** Günlük yoklama tahtası (AttendanceSessionSummary[]). */
export function attendanceDayDoc(date, sessions, { classGroupName = '' } = {}) {
  const list = [...(sessions || [])].sort((a, b) => String(a.scheduleItem?.startsAt || '')
    .localeCompare(String(b.scheduleItem?.startsAt || '')));
  const sum = (key) => list.reduce((total, item) => total + Number(item.counts?.[key] || 0), 0);
  const taken = list.filter((item) => item.taken).length;
  const cancelled = (item) => item.scheduleItem?.status === 'cancelled';
  const rows = list.map((item) => {
    const s = item.scheduleItem || {};
    const state = cancelled(item) ? cell('İptal', 'muted') : item.taken ? cell('Alındı', 'good')
      : cell('Bekliyor', 'warn', true);
    return [`${fmtTime(s.startsAt)}–${fmtTime(s.endsAt)}`, clean(s.classGroup?.name), clean(s.subject?.name),
      clean(s.teacher?.displayName), state, num(item.counts?.enrolled), num(item.counts?.present),
      item.counts?.absent ? cell(num(item.counts.absent), 'bad', true) : '0',
      num(item.counts?.late), num(item.counts?.excused)];
  });
  return documentOf('attendance_day', 'Günlük Yoklama Tahtası', [{
    title: 'Günlük Yoklama Tahtası',
    subtitle: `${fmtDate(date)}${classGroupName ? ` · ${clean(classGroupName)}` : ''}`,
    meta: [['Tarih', fmtDate(date)], ['Kapsam', clean(classGroupName, 'Bütün sınıflar')],
      ['Ders', num(list.length)], ['Yoklaması alınan', `${num(taken)} / ${num(list.filter((item) => !cancelled(item)).length)}`]],
    sections: [
      { kind: 'tiles', columns: 4, items: [
        { label: 'Var', value: num(sum('present')), tone: 'good' },
        { label: 'Yok', value: num(sum('absent')), tone: sum('absent') ? 'bad' : '' },
        { label: 'Geç', value: num(sum('late')) }, { label: 'İzinli', value: num(sum('excused')) },
      ] },
      { kind: 'table', headers: ['Saat', 'Sınıf', 'Ders', 'Öğretmen', 'Durum', 'Kayıtlı', 'Var', 'Yok', 'Geç', 'İzinli'],
        align: 'LLLLCRRRRR', widths: [1.3, 1.2, 1.8, 2, 1.1, 0.9, 0.7, 0.7, 0.7, 0.8], rows,
        empty: 'Bu gün için ders yok.' },
    ],
  }], { orientation: 'landscape', filename: fileHint('yoklama', date, classGroupName) });
}

/** Tek dersin yoklama çizelgesi (AttendanceSheet). */
export function attendanceSheetDoc(sheet) {
  const session = sheet?.session || {};
  const s = session.scheduleItem || {};
  const rows = [...(sheet?.rows || [])]
    .sort((a, b) => studentName(a.student).localeCompare(studentName(b.student), 'tr'))
    .map((row, index) => [num(index + 1), studentName(row.student), clean(row.student?.externalRef),
      row.status ? cell(ATT_LABEL[row.status] || row.status, ATT_TONE[row.status] || '', row.status === 'absent')
        : cell('İşaretlenmedi', 'muted'),
      row.lateMinutes ? `${num(row.lateMinutes)} dk` : '', clean(row.note, '')]);
  return documentOf('attendance_sheet', 'Yoklama Çizelgesi', [{
    title: 'Yoklama Çizelgesi',
    subtitle: `${sessionTitle(session)} · ${fmtDate(s.date || s.startsAt)} ${fmtTime(s.startsAt)}–${fmtTime(s.endsAt)}`,
    meta: [['Sınıf', clean(s.classGroup?.name)], ['Ders', clean(s.subject?.name)], ['Öğretmen', clean(s.teacher?.displayName)],
      ['Yoklama', session.taken ? `${fmtDateTime(session.takenAt)} · ${clean(session.takenBy?.displayName)}` : 'Alınmadı']],
    watermark: session.taken ? '' : 'Alınmadı',
    sections: [
      { kind: 'tiles', columns: 4, items: [
        { label: 'Var', value: num(session.counts?.present), tone: 'good' },
        { label: 'Yok', value: num(session.counts?.absent), tone: session.counts?.absent ? 'bad' : '' },
        { label: 'Geç', value: num(session.counts?.late) }, { label: 'İzinli', value: num(session.counts?.excused) },
      ] },
      { kind: 'table', headers: ['#', 'Öğrenci', 'No', 'Durum', 'Gecikme', 'Not'], align: 'RLLCRL',
        widths: [0.5, 3.2, 1.2, 1.3, 1, 3], rows, empty: 'Bu derste öğrenci yok.' },
      { kind: 'signatures', items: ['Ders öğretmeni', 'Müdür yardımcısı'] },
    ],
  }], { filename: fileHint('yoklama', s.classGroup?.name, s.subject?.name, fmtDate(s.date || s.startsAt)) });
}

const DAY_MARK = { present: { text: '+', tone: '' }, partial: { text: 'K', tone: 'warn' },
  absent: { text: 'D', tone: 'bad' }, excused: { text: 'İ', tone: 'info' }, none: { text: '', tone: '' } };

/** Aylık yoklama matrisi (AttendanceMatrix) — yatay A4, gün sütunları. */
export function attendanceMatrixDoc(matrix) {
  const m = matrix || {};
  const days = m.days || [];
  const headers = ['Öğrenci', ...days.map((day) => String(day).slice(8, 10)), 'Oran'];
  const rows = [...(m.rows || [])]
    .sort((a, b) => studentName(a.student).localeCompare(studentName(b.student), 'tr'))
    .map((row) => {
      const byDate = new Map((row.cells || []).map((item) => [item.date, item]));
      const rate = row.ratePercent;
      return [studentName(row.student), ...days.map((day) => {
        const mark = DAY_MARK[byDate.get(day)?.status] || DAY_MARK.none;
        return mark.tone ? { text: mark.text, tone: mark.tone } : mark.text;
      }), cell(pct(rate, 0), rate !== null && rate !== undefined && rate < 90 ? 'bad' : '', true)];
    });
  return documentOf('attendance_matrix', 'Aylık Yoklama Çizelgesi', [{
    title: 'Aylık Yoklama Çizelgesi',
    subtitle: `${clean(m.group?.name)} · ${monthName(m.month)}`,
    meta: [['Sınıf / grup', clean(m.group?.name)], ['Ay', monthName(m.month)], ['Öğrenci', num(rows.length)],
      ['Gün', num(days.length)]],
    sections: [
      { kind: 'table', headers, align: `L${'C'.repeat(days.length)}R`,
        widths: [4.2, ...days.map(() => 0.55), 1.1], rows, dense: true, empty: 'Bu ay için yoklama yok.' },
      { kind: 'note', text: 'İşaretler: + katıldı · D devamsız · K kısmen katıldı · İ izinli · boş: o gün ders yok. '
        + 'Oran, öğrencinin ay içindeki derslere katılım yüzdesidir.' },
      { kind: 'signatures', items: ['Sınıf öğretmeni', 'Müdür yardımcısı'] },
    ],
  }], { orientation: 'landscape', filename: fileHint('yoklama', m.group?.name, m.month) });
}

/** Devamsızlık listesi (ChronicAbsentee[]). */
export function absenteeListDoc(items, { from = '', to = '', groupName = '' } = {}) {
  const rows = [...(items || [])].sort((a, b) => Number(b.absenceRatePercent || 0) - Number(a.absenceRatePercent || 0))
    .map((item) => [studentName(item.student), clean(item.student?.externalRef), className(item.student),
      cell(pct(item.absenceRatePercent), Number(item.absenceRatePercent) >= 20 ? 'bad' : 'warn', true),
      `${num(item.absentSessions)} / ${num(item.sessions)}`, num(item.consecutiveAbsentDays),
      fmtDate(item.lastAbsentOn)]);
  return documentOf('absentee_list', 'Devamsızlık Listesi', [{
    title: 'Devamsızlık Listesi',
    subtitle: `${fmtDate(from)} – ${fmtDate(to)}${groupName ? ` · ${clean(groupName)}` : ''}`,
    meta: [['Dönem', `${fmtDate(from)} – ${fmtDate(to)}`], ['Kapsam', clean(groupName, 'Bütün sınıflar')],
      ['Öğrenci', num(rows.length)]],
    sections: [{ kind: 'table', headers: ['Öğrenci', 'No', 'Sınıf', 'Devamsızlık', 'Yok / ders', 'Ardışık gün', 'Son devamsızlık'],
      align: 'LLLRRRC', widths: [3, 1.2, 1, 1.2, 1.2, 1, 1.4], rows,
      empty: 'Bu aralıkta eşiği aşan devamsızlık yok.' }],
  }], { filename: fileHint('devamsizlik', from, to, groupName) });
}

// ---------------------------------------------------------- 4. ders programı

/** Zil dilimleri → satırlar (ders sırası), günler → sütunlar. */
export function weekGrid(bellSlots, entries, cellText) {
  const slots = (bellSlots || []).filter((slot) => slot && slot.kind !== 'break');
  const byDay = new Map();
  for (const slot of slots) {
    if (!byDay.has(slot.dayOfWeek)) byDay.set(slot.dayOfWeek, []);
    byDay.get(slot.dayOfWeek).push(slot);
  }
  for (const list of byDay.values()) {
    list.sort((a, b) => (Number(a.order || 0) - Number(b.order || 0)) || String(a.startTime).localeCompare(String(b.startTime)));
  }
  const used = new Set((entries || []).map((entry) => entry.dayOfWeek));
  const days = DAY_KEYS.filter((day) => byDay.has(day) && (DAY_KEYS.indexOf(day) < 5 || used.has(day)));
  const count = Math.max(0, ...days.map((day) => byDay.get(day).length));
  const bySlot = new Map();
  for (const entry of entries || []) {
    const key = `${entry.dayOfWeek}|${entry.bellSlotId}`;
    if (!bySlot.has(key)) bySlot.set(key, []);
    bySlot.get(key).push(entry);
  }
  const rows = [];
  for (let index = 0; index < count; index += 1) {
    const present = days.map((day) => byDay.get(day)[index]).filter(Boolean);
    const tally = new Map();
    for (const slot of present) tally.set(`${slot.startTime}–${slot.endTime}`, (tally.get(`${slot.startTime}–${slot.endTime}`) || 0) + 1);
    const time = [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] || '';
    const cells = days.map((day) => {
      const slot = byDay.get(day)[index];
      if (!slot) return '';
      const items = bySlot.get(`${day}|${slot.id}`) || [];
      const text = items.map(cellText).filter(Boolean).join('\n');
      const own = `${slot.startTime}–${slot.endTime}`;
      return own !== time && text ? `${text}\n(${own})` : text;
    });
    rows.push([`${index + 1}. ders\n${time}`, ...cells]);
  }
  const filled = (entries || []).filter((entry) => entry.status !== 'orphaned').length;
  return { days, rows, filled };
}

function timetableSection(grid) {
  return { kind: 'table', headers: ['Ders', ...grid.days.map((day) => DAY_LABEL[day])],
    align: `L${'C'.repeat(grid.days.length)}`, widths: [1.1, ...grid.days.map(() => 2)], rows: grid.rows,
    empty: 'Zil dilimi tanımlı değil.' };
}

/** Sınıfın haftalık ders programı (Timetable + zil dilimleri). */
export function classTimetableDoc({ classGroup, bellSlots, entries, validFrom, validTo }) {
  const active = (entries || []).filter((entry) => entry.status !== 'orphaned');
  const grid = weekGrid(bellSlots, active, (entry) => [clean(entry.subject?.name, ''),
    [entry.teacher?.displayName, entry.room].filter(Boolean).map((item) => clean(item)).join(' · ')]
    .filter(Boolean).join('\n'));
  const teachers = new Map();
  for (const entry of active) {
    const name = entry.teacher?.displayName || 'Öğretmen atanmadı';
    const key = `${entry.subject?.name || 'Ders'}|${name}`;
    teachers.set(key, (teachers.get(key) || 0) + 1);
  }
  return documentOf('timetable_class', 'Haftalık Ders Programı', [{
    title: 'Haftalık Ders Programı',
    subtitle: `${clean(classGroup?.name)} · ${fmtDate(validFrom)}${validTo ? ` – ${fmtDate(validTo)}` : ' itibarıyla'}`,
    meta: [['Sınıf', clean(classGroup?.name)], ['Geçerlilik', `${fmtDate(validFrom)} – ${validTo ? fmtDate(validTo) : 'süresiz'}`],
      ['Haftalık ders', num(grid.filled)]],
    sections: [timetableSection(grid),
      { kind: 'table', title: 'Ders ve öğretmenler', headers: ['Ders', 'Öğretmen', 'Haftalık saat'], align: 'LLR',
        widths: [2, 2, 1], keep: true,
        rows: [...teachers.entries()].sort((a, b) => a[0].localeCompare(b[0], 'tr'))
          .map(([key, hours]) => [...key.split('|').map((item) => clean(item)), num(hours)]) }],
  }], { orientation: 'landscape', filename: fileHint('program', classGroup?.name) });
}

/** Öğretmenin haftalık programı — bütün grupların programından (TimetableGrid) süzülür. */
export function teacherTimetableDoc({ teacher, bellSlots, groups, date }) {
  const mine = [];
  for (const group of groups || []) {
    for (const entry of group.entries || []) {
      if (entry.status !== 'orphaned' && entry.teacher?.id && entry.teacher.id === teacher?.id) {
        mine.push({ ...entry, group: group.group });
      }
    }
  }
  const grid = weekGrid(bellSlots, mine, (entry) => [clean(entry.group?.name, ''),
    [entry.subject?.name, entry.room].filter(Boolean).map((item) => clean(item)).join(' · ')].filter(Boolean).join('\n'));
  return documentOf('timetable_teacher', 'Öğretmen Ders Programı', [{
    title: 'Öğretmen Ders Programı',
    subtitle: `${clean(teacher?.displayName)} · ${fmtDate(date)} haftası`,
    meta: [['Öğretmen', clean(teacher?.displayName)], ['Hafta', fmtDate(date)], ['Haftalık ders', num(mine.length)],
      ['Sınıf / grup', num(new Set(mine.map((entry) => entry.group?.id)).size)]],
    sections: [timetableSection(grid)],
  }], { orientation: 'landscape', filename: fileHint('program', teacher?.displayName) });
}

// ---------------------------------------------------------- 5. ödev

function pagesText(item) {
  if (item.pageFrom && item.pageTo) return `s. ${item.pageFrom}–${item.pageTo}`;
  if (item.pageFrom) return `s. ${item.pageFrom}`;
  return '';
}

/** Sınıfın / kurumun ödev listesi (AdminHomework[]). */
export function homeworkListDoc(items, { scope = '', state = '' } = {}) {
  const rows = [...(items || [])].sort((a, b) => String(a.dueAt).localeCompare(String(b.dueAt))).map((item) => {
    const stats = item.stats || {};
    const ratio = stats.assignedCount ? Math.round((stats.completedCount / stats.assignedCount) * 100) : null;
    return item.status === 'cancelled'
      ? { cells: [clean(item.title), clean(item.subject?.name), pagesText(item), fmtDate(item.createdAt), fmtDate(item.dueAt),
        '', '', cell('İptal', 'muted')], tone: 'muted' }
      : [clean(item.title), clean(item.subject?.name), pagesText(item) || clean((item.topics || []).map((t) => t.name).join(', '), ''),
        fmtDate(item.createdAt), fmtDate(item.dueAt),
        `${num(stats.completedCount)} / ${num(stats.assignedCount)}${ratio === null ? '' : ` (%${ratio})`}`,
        stats.overdueCount ? cell(num(stats.overdueCount), 'bad', true) : '0', clean(item.createdBy?.displayName)];
  });
  return documentOf('homework_list', 'Ödev Listesi', [{
    title: 'Ödev Listesi',
    subtitle: [clean(scope, 'Bütün sınıflar'), state].filter(Boolean).join(' · '),
    meta: [['Kapsam', clean(scope, 'Bütün sınıflar')], ['Ödev', num(rows.length)]],
    sections: [{ kind: 'table', headers: ['Ödev', 'Ders', 'Kaynak / konu', 'Verildi', 'Teslim', 'Tamamlayan', 'Geciken', 'Veren'],
      align: 'LLLCCRRL', widths: [3, 1.4, 2, 1, 1, 1.4, 0.9, 1.6], rows, empty: 'Ödev yok.' }],
  }], { orientation: 'landscape', filename: fileHint('odevler', scope) });
}

const HW_STATUS = { pending: ['Bekliyor', 'warn'], completed: ['Tamamlandı', 'good'], overdue: ['Gecikti', 'bad'] };

/** Öğrencinin ödevleri (StudentHomework[]). */
export function studentHomeworkDoc(student, items) {
  const rows = [...(items || [])].sort((a, b) => String(a.dueAt).localeCompare(String(b.dueAt))).map((item) => {
    const [label, tone] = HW_STATUS[item.status] || [item.status || '—', ''];
    const c = item.completion;
    return [clean(item.title), clean(item.subject?.name), clean(item.resource?.title, '') + (pagesText(item) ? ` ${pagesText(item)}` : ''),
      fmtDate(item.dueAt), cell(label, tone), c ? `${num(c.correct)} D · ${num(c.wrong)} Y · ${num(c.blank)} B` : '—'];
  });
  const count = (status) => (items || []).filter((item) => item.status === status).length;
  return documentOf('homework_student', 'Öğrenci Ödev Listesi', [{
    title: 'Öğrenci Ödev Listesi',
    subtitle: `${studentName(student)} · ${className(student)}`,
    meta: [['Öğrenci', studentName(student)], ['Öğrenci no', clean(student?.externalRef)], ['Sınıf', className(student)]],
    sections: [
      { kind: 'tiles', columns: 3, items: [{ label: 'Tamamlanan', value: num(count('completed')), tone: 'good' },
        { label: 'Bekleyen', value: num(count('pending')) }, { label: 'Geciken', value: num(count('overdue')), tone: count('overdue') ? 'bad' : '' }] },
      { kind: 'table', headers: ['Ödev', 'Ders', 'Kaynak', 'Teslim', 'Durum', 'Sonuç'], align: 'LLLCCL',
        widths: [3, 1.4, 2.4, 1, 1.2, 1.8], rows, empty: 'Ödev yok.' },
    ],
  }], { filename: fileHint('odevler', student?.displayName) });
}

// ---------------------------------------------------------- 6. çalışma planı

const TASK_TYPE = { topic_study: 'Konu çalışma', question_solving: 'Soru çözme', resource_section: 'Kaynak',
  review: 'Tekrar', mistake_review: 'Hata tekrarı', reading: 'Okuma', exam: 'Deneme', homework: 'Ödev',
  free: 'Serbest', video: 'Video' };
const TASK_STATUS = { planned: ['Planlandı', ''], done: ['Yapıldı', 'good'], partial: ['Kısmen', 'warn'],
  skipped: ['Atlandı', 'bad'], moved: ['Ertelendi', 'muted'] };

/** Haftalık çalışma planı (PlanWeek). */
export function studyPlanDoc(student, week) {
  const w = week || {};
  const summary = w.summary || {};
  const minutes = (value) => (value ? `${num(Math.floor(value / 60))} sa ${num(value % 60)} dk` : '0 dk');
  const sections = [{ kind: 'tiles', columns: 4, items: [
    { label: 'Planlanan görev', value: num(summary.plannedTasks) },
    { label: 'Yapılan', value: num(summary.doneTasks), tone: 'good' },
    { label: 'Planlanan süre', value: minutes(summary.plannedMinutes) },
    { label: 'Plana uyum', value: pct(summary.adherencePercent), tone: 'info' },
  ] }];
  for (const day of w.days || []) {
    const tasks = [...(day.tasks || [])].sort((a, b) => (Number(a.order || 0) - Number(b.order || 0)));
    const title = `${DAY_LABEL[day.dayOfWeek] || ''} ${fmtDate(day.date)}`;
    if (day.holiday) {
      sections.push({ kind: 'note', title, text: `Tatil: ${clean(day.holiday.title)}` });
      continue;
    }
    sections.push({ kind: 'table', title, headers: ['Saat', 'Görev', 'Tür', 'Ders / konu', 'Süre', 'Durum'],
      align: 'LLLLRC', widths: [1.1, 3.2, 1.3, 2.4, 0.8, 1.1], keep: tasks.length <= 12,
      rows: tasks.map((task) => {
        const [label, tone] = TASK_STATUS[task.status] || [task.status || '—', ''];
        return [task.startTime ? `${task.startTime}${task.endTime ? `–${task.endTime}` : ''}` : '—',
          clean(task.title), TASK_TYPE[task.type] || clean(task.type), [task.subject?.name, task.topic?.name]
            .filter(Boolean).map((item) => clean(item)).join(' · ') || '—',
          `${num(task.estimatedMinutes)} dk`, cell(label, tone)];
      }), empty: 'Bu gün için görev yok.' });
  }
  sections.push({ kind: 'signatures', items: ['Koç / rehber öğretmen', 'Öğrenci', 'Veli'] });
  return documentOf('study_plan', 'Haftalık Çalışma Planı', [{
    title: 'Haftalık Çalışma Planı',
    subtitle: `${studentName(student)} · ${fmtDate(w.weekStart)} – ${fmtDate(w.weekEnd)}`,
    meta: [['Öğrenci', studentName(student)], ['Öğrenci no', clean(student?.externalRef)], ['Sınıf', className(student)],
      ['Hafta', `${fmtDate(w.weekStart)} – ${fmtDate(w.weekEnd)}`]],
    sections,
  }], { filename: fileHint('plan', student?.displayName, w.weekStart) });
}

// ---------------------------------------------------------- 7. kurum cari

const LINE_KIND = { charge: 'Borç', charge_cancelled: 'Borç iptali', payment: 'Tahsilat', payment_voided: 'Tahsilat iptali',
  credit_applied: 'Alacak mahsubu' };
export const PAYMENT_METHOD = { cash: 'Nakit', transfer: 'Havale/EFT', card: 'Kart', other: 'Diğer', online_card: 'Çevrimiçi kart' };
const A12 = 'Bu belge KURUM CARİ hesabına aittir; kantin bakiyesi ve kantin harcamaları bu belgede yer almaz.';

/** Hesap ekstresi (FinanceStatement). */
export function statementDoc(student, statement) {
  const s = statement || {};
  const lines = s.lines || [];
  const totalCharge = lines.reduce((sum, line) => sum + Number(line.chargeMinor || 0), 0);
  const totalPayment = lines.reduce((sum, line) => sum + Number(line.paymentMinor || 0), 0);
  return documentOf('finance_statement', 'Hesap Ekstresi', [{
    title: 'Hesap Ekstresi',
    subtitle: `Kurum cari hesabı · ${fmtDate(s.from)} – ${fmtDate(s.to)}`,
    meta: [['Öğrenci', studentName(student)], ['Öğrenci no', clean(student?.externalRef)], ['Sınıf', className(student)],
      ['Dönem', `${fmtDate(s.from)} – ${fmtDate(s.to)}`]],
    sections: [
      { kind: 'tiles', columns: 4, items: [
        { label: 'Dönem başı bakiye', value: money(s.openingBalanceMinor) },
        { label: 'Dönem borcu', value: money(totalCharge) },
        { label: 'Dönem tahsilatı', value: money(totalPayment), tone: 'good' },
        { label: 'Dönem sonu bakiye', value: money(s.closingBalanceMinor), tone: Number(s.closingBalanceMinor) > 0 ? 'bad' : 'good' },
      ] },
      { kind: 'table', headers: ['Tarih', 'İşlem', 'Açıklama', 'Borç', 'Tahsilat', 'Bakiye', 'Belge'],
        align: 'CLLRRRL', widths: [1.1, 1.3, 3.4, 1.3, 1.3, 1.4, 1.3],
        rows: lines.map((line) => {
          const voided = line.kind === 'charge_cancelled' || line.kind === 'payment_voided';
          return { cells: [fmtDate(line.date), LINE_KIND[line.kind] || clean(line.kind), clean(line.description),
            line.chargeMinor ? money(line.chargeMinor) : '', line.paymentMinor ? money(line.paymentMinor) : '',
            money(line.balanceMinor), clean(line.refId, '')], tone: voided ? 'muted' : '' };
        }),
        footer: ['', '', 'Dönem toplamı', money(totalCharge), money(totalPayment), money(s.closingBalanceMinor), ''],
        empty: 'Bu dönemde hareket yok.' },
      ...(Number(s.creditMinor) ? [{ kind: 'note', text: `Kullanılmamış alacak: ${money(s.creditMinor)}` }] : []),
      { kind: 'note', text: A12 },
      { kind: 'signatures', items: ['Muhasebe', 'Veli'] },
    ],
  }], { filename: fileHint('ekstre', student?.displayName, s.from, s.to) });
}

/** Tahsilat makbuzu (FinanceReceipt). */
export function receiptDoc(receipt) {
  const r = receipt || {};
  const p = r.payment || {};
  const voided = p.status === 'voided';
  return documentOf('finance_receipt', 'Tahsilat Makbuzu', [{
    title: 'Tahsilat Makbuzu',
    subtitle: `Makbuz no ${clean(r.receiptNo)} · ${fmtDateTime(p.paidAt || r.issuedAt)}`,
    meta: [['Makbuz no', clean(r.receiptNo)], ['Tarih', fmtDateTime(p.paidAt || r.issuedAt)],
      ['Öğrenci', studentName(r.student)], ['Öğrenci no', clean(r.student?.externalRef)], ['Sınıf', className(r.student)],
      ['Ödeme şekli', PAYMENT_METHOD[p.method] || clean(p.method)]],
    watermark: voided ? 'İptal' : '',
    sections: [
      { kind: 'tiles', columns: 3, items: [
        { label: 'Tahsil edilen', value: money(p.amountMinor), tone: voided ? 'muted' : 'good' },
        { label: 'İşlem sonrası bakiye', value: money(r.balanceAfterMinor) },
        { label: 'İşlem sonrası alacak', value: money(r.creditAfterMinor) },
      ] },
      { kind: 'table', title: 'Kapatılan borçlar', headers: ['Açıklama', 'Tutar'], align: 'LR', widths: [5, 1.5],
        rows: (p.allocations || []).map((item) => [clean(item.description), money(item.amountMinor)]),
        footer: p.creditAddedMinor ? ['Alacak olarak ayrılan', money(p.creditAddedMinor)] : null,
        empty: 'Tutar alacak olarak kaydedildi.' },
      { kind: 'fields', columns: 2, items: [
        { label: 'Kurum', value: clean(r.institution?.name) },
        { label: 'Vergi bilgisi', value: clean(r.institution?.taxInfo) },
        { label: 'Açıklama', value: clean(p.note) },
        { label: 'Kaydeden', value: clean(p.recordedBy?.displayName) },
      ] },
      ...(voided ? [{ kind: 'note', text: `Bu tahsilat ${fmtDateTime(p.voidedAt)} tarihinde iptal edildi. Gerekçe: ${clean(p.voidReason)}` }] : []),
      { kind: 'note', text: A12 },
      { kind: 'signatures', items: ['Tahsil eden', 'Ödeyen'] },
    ],
  }], { filename: fileHint('makbuz', r.receiptNo) });
}

// ---------------------------------------------------------- 8. kitap

const BOOK_STATUS = { requested: 'Talep edildi', rejected: 'Reddedildi', cancelled: 'İptal', expired: 'Süresi doldu',
  awaiting_payment: 'Ödeme bekliyor', approved: 'Onaylandı', ordering: 'Sipariş veriliyor', ordered: 'Sipariş verildi',
  order_failed: 'Sipariş başarısız', delivered: 'Teslim edildi' };
const BOOK_PAY = { card: 'Kart ile ödeme', cari: 'Kurum carisine borç' };

/** Kitap talep / satış belgesi (BookRequest). */
export function bookRequestDoc(request) {
  const r = request || {};
  const product = r.product || {};
  return documentOf('book_request', 'Kitap Talep Belgesi', [{
    title: r.status === 'delivered' ? 'Kitap Teslim Belgesi' : 'Kitap Talep Belgesi',
    subtitle: `Talep ${clean(r.ref)} · ${BOOK_STATUS[r.status] || clean(r.status)}`,
    meta: [['Talep no', clean(r.ref)], ['Talep tarihi', fmtDateTime(r.requestedAt)], ['Öğrenci', studentName(r.student)],
      ['Öğrenci no', clean(r.student?.externalRef)], ['Sınıf', className(r.student)], ['Talep eden', clean(r.requestedBy?.displayName)]],
    sections: [
      { kind: 'table', title: 'Kitap', headers: ['Kitap', 'Yayınevi', 'ISBN', 'Adet', 'Birim fiyat', 'Tutar'],
        align: 'LLLRRR', widths: [3.4, 1.6, 1.5, 0.7, 1.2, 1.2],
        rows: [[clean(product.title), clean(product.publisher), clean(product.isbn), num(r.quantity),
          money(product.priceMinor), money(r.totalMinor)]],
        footer: ['Toplam', '', '', num(r.quantity), '', money(r.totalMinor)] },
      { kind: 'fields', columns: 3, items: [
        { label: 'Durum', value: BOOK_STATUS[r.status] || clean(r.status) },
        { label: 'Ödeme', value: BOOK_PAY[r.decision?.paymentChoice] || '—' },
        { label: 'Sipariş no', value: clean(r.order?.incrementId) },
        { label: 'Karar', value: r.decision ? `${fmtDateTime(r.decision.decidedAt)} · ${clean(r.decision.by?.displayName)}` : '—' },
        { label: 'Teslim', value: r.deliveredAt ? fmtDateTime(r.deliveredAt) : '—' },
        { label: 'Not', value: clean(r.note) },
      ] },
      { kind: 'table', title: 'Geçmiş', headers: ['Tarih', 'Durum', 'İşlem yapan'], align: 'LLL', widths: [1.4, 2, 2],
        rows: (r.history || []).map((item) => [fmtDateTime(item.at), BOOK_STATUS[item.status] || clean(item.status),
          clean(item.actorLabel)]), keep: true },
      ...(r.decision?.paymentChoice === 'cari' ? [{ kind: 'note', text: 'Bedel kurum cari hesabına borç olarak yazılır (kantin bakiyesinden düşülmez).' }] : []),
      { kind: 'signatures', items: ['Teslim eden', 'Teslim alan (veli / öğrenci)'] },
    ],
  }], { filename: fileHint('kitap', r.ref, r.student?.displayName) });
}

const SALE_LINE = { awaiting_payment: 'Ödeme bekliyor', approved: 'Sipariş sırada', ordering: 'Sipariş veriliyor',
  ordered: 'Sipariş verildi', order_failed: 'Sipariş düştü', delivered: 'Teslim edildi', cancelled: 'İptal',
  expired: 'Ödeme süresi doldu' };
const SALE_PAY = { cari: 'Kurum carisine yazıldı', cash: 'Nakit tahsil edildi', card: 'Kartla (POS) tahsil edildi',
  pay_link: 'Veliye ödeme bağlantısı' };
const SALE_TONE = { delivered: 'good', ordered: 'good', order_failed: 'bad', awaiting_payment: 'warn',
  cancelled: 'muted', expired: 'muted' };

/** Kitap satışı belgesi (BookSale, satırlarıyla): kitaplar, öğrenci satırları, teslim imzası (0.5.4). */
export function bookSaleDoc(sale) {
  const s = sale || {};
  const items = s.items || [];
  const lines = s.lines || [];
  const perStudent = items.reduce((sum, item) => sum + (Number(item.totalMinor) || 0), 0);
  return documentOf('book_sale', 'Kitap Satış Belgesi', [{
    title: 'Kitap Satış Belgesi',
    subtitle: `Satış ${clean(s.ref)} · ${SALE_PAY[s.paymentMethod] || clean(s.paymentMethod)}`,
    meta: [['Satış no', clean(s.ref)], ['Tarih', fmtDateTime(s.createdAt)], ['Satan', clean(s.createdBy?.displayName)],
      ['Öğrenci', num(s.studentCount ?? lines.length)], ['Toplam', money(s.totalMinor)]],
    sections: [
      { kind: 'table', title: 'Kitaplar (öğrenci başına)', headers: ['Kitap', 'Yayınevi', 'ISBN', 'Adet', 'Birim fiyat', 'Tutar'],
        align: 'LLLRRR', widths: [3.4, 1.6, 1.5, 0.7, 1.2, 1.2],
        rows: items.map((item) => [clean(item.title), clean(item.publisher), clean(item.isbn), num(item.quantity),
          money(item.unitPriceMinor), money(item.totalMinor)]),
        footer: ['Öğrenci başına', '', '', '', '', money(perStudent)] },
      { kind: 'table', title: 'Öğrenciler', headers: ['Öğrenci', 'Sınıf', 'Durum', 'Sipariş', 'Tutar', 'Teslim alan imza'],
        align: 'LLLLRL', widths: [2.6, 0.9, 1.6, 1.1, 1.1, 2.0], dense: true,
        rows: lines.map((line) => [studentName(line.student), className(line.student),
          cell(SALE_LINE[line.status] || clean(line.status), SALE_TONE[line.status] || ''),
          line.order?.incrementId ? `#${clean(line.order.incrementId)}` : '—', money(line.totalMinor), '']),
        footer: ['Toplam', '', '', '', money(s.totalMinor), ''], empty: 'Satır yok.' },
      { kind: 'note', text: 'Bedel yalnız kurum carisindedir; kantin bakiyesinden düşülmez.' },
      ...(s.note ? [{ kind: 'text', text: clean(s.note) }] : []),
      { kind: 'signatures', items: ['Teslim eden', 'Kurum yetkilisi'] },
    ],
  }], { filename: fileHint('kitap-satisi', s.ref) });
}

/**
 * Toplu kalıcı silme sonucu (bbd_students `POST /students/delete` yanıtı): öğrenci
 * başına "silindi" ya da engelin nedeni (0.5.4). KM verisidir; kişisel veri yalnız ad.
 */
export function studentDeletionDoc(result, { at = '' } = {}) {
  const r = result || {};
  const rows = r.results || [];
  return documentOf('student_deletion', 'Kalıcı Silme Sonucu', [{
    title: 'Öğrenci Kalıcı Silme Sonucu',
    subtitle: `${num(r.deleted || 0)} öğrenci silindi · ${num(r.blocked || 0)} öğrenci silinmedi`,
    meta: [['İşlem', fmtDateTime(at || new Date().toISOString())], ['Seçilen', num(rows.length)]],
    sections: [
      { kind: 'table', title: 'Öğrenciler', headers: ['Öğrenci', 'Sonuç', 'Neden'], align: 'LLL', widths: [2.4, 1.1, 4.2],
        rows: rows.map((row) => [clean(row.displayName || row.studentId),
          cell(row.deleted ? 'Silindi' : 'Silinmedi', row.deleted ? 'good' : 'bad', true),
          row.deleted ? 'KM kaydı silindi; kantinde anonimleşir; BBD Akademi KVKK planına göre siler.' : clean(row.message)]),
        empty: 'Sonuç yok.' },
      { kind: 'note', text: 'Kalıcı silme geri alınamaz. Silinmeyen öğrencilerde engel çözülünce işlem yeniden yapılabilir.' },
      { kind: 'signatures', items: ['İşlemi yapan', 'Onaylayan'] },
    ],
  }], { filename: fileHint('kalici-silme', `${num(r.deleted || 0)}-ogrenci`) });
}

// ---------------------------------------------------------- 9. öğrenci kartı

const STUDENT_STATUS = { active: 'Aktif', inactive: 'Pasif', missing: 'KM kaydı yok' };
const TRACK = { SAY: 'Sayısal', EA: 'Eşit ağırlık', SOZ: 'Sözel', DIL: 'Dil' };

/** Öğrenci kimlik / özet kartı (AdminStudentDetail). */
export function studentCardDoc(detail) {
  const d = detail || {};
  const s = d.student || {};
  const guardians = [...(d.guardians || [])].sort((a, b) => Number(a.slot || 9) - Number(b.slot || 9));
  return documentOf('student_card', 'Öğrenci Özet Kartı', [{
    title: 'Öğrenci Özet Kartı',
    subtitle: `${studentName(s)} · ${className(s)}`,
    meta: [['Öğrenci', studentName(s)], ['Öğrenci no', clean(s.externalRef)], ['Okul no', clean(d.schoolNo)],
      ['Sınıf', className(s)]],
    sections: [
      { kind: 'fields', title: 'Kayıt', columns: 3, items: [
        { label: 'Durum', value: STUDENT_STATUS[s.status] || clean(s.status), tone: s.status === 'active' ? 'good' : 'warn' },
        { label: 'Sınıf düzeyi', value: s.gradeLevel === null || s.gradeLevel === undefined ? '—' : `${s.gradeLevel}. sınıf` },
        { label: 'Alan', value: TRACK[s.track] || '—' },
        { label: 'Hedef sınav', value: clean(s.examTarget) },
        { label: 'Öğrenci telefonu', value: clean(d.studentPhoneMasked) },
        { label: 'Gruplar', value: (d.studentGroups || []).map((item) => clean(item.name)).join(', ') || '—' },
      ] },
      { kind: 'table', title: 'Veliler', headers: ['Veli', 'Yakınlık', 'Telefon', 'Uygulama hesabı', 'Kantin yönetimi'],
        align: 'LLLCC', widths: [2.4, 1.6, 1.8, 1.2, 1.2], keep: true,
        rows: guardians.map((item) => [clean(item.displayName), [RELATIONS[item.relation] || `${num(item.slot)}. veli`,
          item.primary ? 'birincil' : ''].filter(Boolean).join(' · '), maskPhone(item.phoneMasked) || '—',
          item.hasAppAccount ? 'Var' : 'Yok', item.canManageCanteen ? 'Evet' : 'Hayır']),
        empty: 'Kayıtlı veli yok.' },
      { kind: 'table', title: 'Programlar', headers: ['Ders', 'Program', 'Ünite', 'Konu'], align: 'LLRR',
        widths: [1.6, 3.4, 0.8, 0.8], rows: (d.programs || []).map((item) => [clean(item.subject?.name), clean(item.title),
          num(item.unitCount), num(item.topicCount)]), empty: 'Atanmış program yok.' },
      { kind: 'note', text: `Kayıt Kontrol Merkezi'nden eşitlenir · son eşitleme ${fmtDateTime(d.syncedAt)}. `
        + 'Telefon numaraları KVKK gereği maskelidir.' },
    ],
  }], { filename: fileHint('ogrenci', s.displayName) });
}

// ---------------------------------------------------------- 10. veli listesi

/** Öğrenci ayrıntıları (AdminStudentDetail[]) → veli kayıtları (guardianListDoc girdisi). */
export function guardiansFromDetails(details) {
  const items = [];
  for (const detail of details || []) {
    for (const guardian of detail?.guardians || []) {
      items.push({ ...guardian, students: [detail.student] });
    }
  }
  return items;
}

/** Sınıfın veli listesi — veli kayıtlarından sınıfın öğrencileri süzülür. */
export function guardianListDoc(guardians, { classGroupId = '', classGroupName = '' } = {}) {
  const rows = [];
  for (const guardian of guardians || []) {
    for (const student of guardian.students || []) {
      if (!student || (classGroupId && student.classGroup?.id !== classGroupId)) continue;
      rows.push({ student, guardian });
    }
  }
  rows.sort((a, b) => studentName(a.student).localeCompare(studentName(b.student), 'tr')
    || Number(a.guardian.slot || 9) - Number(b.guardian.slot || 9)
    || clean(a.guardian.displayName).localeCompare(clean(b.guardian.displayName), 'tr'));
  const students = new Set(rows.map((row) => row.student.id));
  const relation = (guardian) => [RELATIONS[guardian.relation] || (guardian.slot ? `${num(guardian.slot)}. veli` : 'Veli'),
    guardian.primary ? 'birincil' : ''].filter(Boolean).join(' · ');
  return documentOf('guardian_list', 'Veli Listesi', [{
    title: 'Veli Listesi',
    subtitle: clean(classGroupName, 'Bütün sınıflar'),
    meta: [['Sınıf', clean(classGroupName, 'Bütün sınıflar')], ['Öğrenci', num(students.size)], ['Veli kaydı', num(rows.length)]],
    sections: [
      { kind: 'table', headers: ['Öğrenci', 'No', 'Veli', 'Yakınlık', 'Telefon', 'Uygulama'], align: 'LLLLLC',
        widths: [2.5, 1.1, 2.5, 1.5, 1.8, 0.9],
        rows: rows.map(({ student, guardian }) => [studentName(student), clean(student.externalRef),
          clean(guardian.displayName), relation(guardian), maskPhone(guardian.phoneMasked) || '—',
          guardian.hasAppAccount ? 'Var' : 'Yok']),
        empty: 'Bu sınıfta velisi kayıtlı öğrenci yok.' },
      { kind: 'note', text: 'Telefon numaraları KVKK gereği maskelidir; tam numara yalnız Kontrol Merkezi öğrenci kaydında görülür.' },
    ],
  }], { filename: fileHint('veliler', classGroupName) });
}

// ---------------------------------------------------------- 11. kantin

/** Kantin hesap dökümü (CanteenSummary + CanteenTransaction[]) — kurum carisinden AYRI (A12). */
export function canteenDoc(student, summary, transactions, { from = '', to = '' } = {}) {
  const c = summary || {};
  const list = [...(transactions || [])].sort((a, b) => String(a.at).localeCompare(String(b.at)));
  const spent = list.filter((item) => !item.reversed).reduce((sum, item) => sum + Number(item.totalMinor || 0), 0);
  return documentOf('canteen_statement', 'Kantin Hesap Dökümü', [{
    title: 'Kantin Hesap Dökümü',
    subtitle: `${studentName(student)} · ${fmtDate(from)} – ${fmtDate(to)}`,
    meta: [['Öğrenci', studentName(student)], ['Öğrenci no', clean(student?.externalRef)], ['Sınıf', className(student)],
      ['Dönem', `${fmtDate(from)} – ${fmtDate(to)}`]],
    sections: [
      { kind: 'tiles', columns: 4, items: [
        { label: 'Kantin bakiyesi', value: money(c.balanceMinor), tone: Number(c.balanceMinor) < 0 ? 'bad' : 'good' },
        { label: 'Kantin borcu', value: money(c.debtMinor), tone: Number(c.debtMinor) > 0 ? 'bad' : '' },
        { label: 'Günlük limit', value: c.dailyLimitMinor === null || c.dailyLimitMinor === undefined ? 'Limitsiz' : money(c.dailyLimitMinor) },
        { label: 'Dönem harcaması', value: money(spent) },
      ] },
      ...(c.isBlocked || c.guardianBlocked ? [{ kind: 'note', text: `Kantin kullanımı kapalı (${c.guardianBlocked ? 'veli' : 'kurum'} kararı).` }] : []),
      { kind: 'table', title: 'Harcamalar', headers: ['Tarih', 'Ürünler', 'Tutar', 'Durum'], align: 'LLRC',
        widths: [1.4, 5, 1.2, 1],
        rows: list.map((item) => ({ cells: [fmtDateTime(item.at),
          (item.items || []).map((line) => `${num(line.qty)}× ${clean(line.name)}`).join(', ') || '—',
          money(item.totalMinor), item.reversed ? cell('İptal', 'muted') : ''], tone: item.reversed ? 'muted' : '' })),
        footer: ['', 'Toplam (iptaller hariç)', money(spent), ''], empty: 'Bu dönemde kantin harcaması yok.' },
      { kind: 'note', text: 'Bu belge KANTİN hesabına aittir; kurum cari hesabı (taksit, kitap borcu) bu belgede yer almaz.' },
    ],
  }], { filename: fileHint('kantin', student?.displayName, from, to) });
}
