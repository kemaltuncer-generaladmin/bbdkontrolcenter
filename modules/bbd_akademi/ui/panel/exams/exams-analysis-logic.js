// BBD Akademi — deneme sonucu ayrıntılı analiz: saf mantık (Akademi README §14 madde 138).
//
// İMPORT YOK (Node testleri doğrudan yükler). Ekran `exams-analysis.js`. Metinler
// Akademi README §14 madde 138 ile AYNI (KM ↔ web ↔ Flutter): süzgeç "Yanlış ve boş"
// (varsayılan; önce yanlışlar, sonra boşlar) · "Yanlış" · "Boş" · "Hepsi"; ders
// seçimi "Tüm dersler" + ders adları; satır "{bölüm} · {no}. soru".

export const FILTERS = [
  { key: 'wrong_blank', label: 'Yanlış ve boş' },
  { key: 'wrong', label: 'Yanlış' },
  { key: 'blank', label: 'Boş' },
  { key: 'all', label: 'Hepsi' },
];

export const RESULT_LABELS = {
  correct: ['Doğru', 'good'],
  wrong: ['Yanlış', 'bad'],
  blank: ['Boş', 'warn'],
  cancelled: ['İptal', 'dim'],
};

export const TEXT = {
  questionsTitle: 'Soru soru analiz',
  questionsHint: 'Her sorunun cevabı, doğru cevabı ve konusu',
  topicsTitle: 'Konu analizi',
  topicsHint: 'Denemedeki bütün konular, ders ders',
  allSubjects: 'Tüm dersler',
  noMatch: 'Bu süzgece uyan soru yok',
  noMistakes: 'Yanlış ya da boş soru yok',
  countsOnlyTitle: 'Bu denemede soru ayrıntısı yok',
  countsOnlyText: 'Sonuç doğru / yanlış / boş sayılarıyla girildi; soru soru cevaplar yok.',
  noTopicsTitle: 'Konu analizi yok',
  noTopicsText: 'Sorular konulara eşlenince konu analizi burada görünür.',
  unmapped: 'Konu eşlenmemiş',
  weak: 'Zayıf',
  cancelled: 'İptal (doğru sayıldı)',
  reportCard: 'Karne (PDF)',
  // Soru soru deneme analizi (iş 83, Akademi README §14 madde 144): A4, en çok 2 sayfa, BÜTÜN sorular + kazanım.
  analysisPdf: 'Soru soru analizi (PDF)',
  analysisPdfBusy: 'Analiz hazırlanıyor…',
  analysisPdfFailed: 'Soru soru analizi hazırlanamadı. Lütfen tekrar deneyin.',
  noOutcome: 'Kazanım eşlenmemiş',
  reportCardBusy: 'Karne hazırlanıyor…',
  reportCardFailed: 'Karne hazırlanamadı. Lütfen tekrar deneyin.',
};

/** Bilinmeyen sonuç değeri boş gibi gösterilir (sözleşme README §10). */
export function resultOf(question) {
  return RESULT_LABELS[question?.result] ? question.result : 'blank';
}

/** Süzgeç + ders: varsayılan "Yanlış ve boş" önce yanlışları, sonra boşları verir (kitapçık sırası korunur). */
export function filterQuestions(questions, filter = 'wrong_blank', groupKey = '') {
  const list = (questions || []).filter((q) => !groupKey || q.groupKey === groupKey);
  if (filter === 'all') return list;
  if (filter === 'wrong') return list.filter((q) => resultOf(q) === 'wrong');
  if (filter === 'blank') return list.filter((q) => resultOf(q) === 'blank');
  return [...list.filter((q) => resultOf(q) === 'wrong'), ...list.filter((q) => resultOf(q) === 'blank')];
}

/** Süzgeç çiplerinin sayıları (seçili derse göre). */
export function filterCounts(questions, groupKey = '') {
  const list = (questions || []).filter((q) => !groupKey || q.groupKey === groupKey);
  const wrong = list.filter((q) => resultOf(q) === 'wrong').length;
  const blank = list.filter((q) => resultOf(q) === 'blank').length;
  return { wrong_blank: wrong + blank, wrong, blank, all: list.length };
}

/** "Matematik · 7. soru" (+ " · A kitapçığında 9" kitapçık A değilse ve numara farklıysa). */
export function questionTitle(question, sectionsById, bookletType) {
  const section = sectionsById?.get?.(question.sectionId) || sectionsById?.[question.sectionId];
  const base = `${section?.title || 'Soru'} · ${question.questionNo}. soru`;
  if (bookletType && bookletType !== 'A' && question.questionNoA && question.questionNoA !== question.questionNo) {
    return `${base} · A kitapçığında ${question.questionNoA}`;
  }
  return base;
}

/** "Cevap: C · Doğru: A"; iptalde "İptal (doğru sayıldı)". */
export function answerText(question) {
  if (question.result === 'cancelled') return TEXT.cancelled;
  return `Cevap: ${question.given || '—'} · Doğru: ${question.key || '—'}`;
}

export function topicText(question) {
  const names = (question.topics || []).map((topic) => topic.name).filter(Boolean);
  return names.length ? names.join(' · ') : TEXT.unmapped;
}

/**
 * Kazanım satırı. Sunucu `outcomeTexts` veriyorsa (iş 83: yayınevi kazanımı → yoksa konu/ünite adı) "kod · metin";
 * boş dizi → "Kazanım eşlenmemiş". Eski sunucu (alan yok) → `outcomes`.
 */
export function outcomeText(question) {
  if (Array.isArray(question.outcomeTexts)) {
    const texts = question.outcomeTexts.filter(Boolean);
    if (!texts.length) return TEXT.noOutcome;
    const code = question.outcomeCode ? `${question.outcomeCode} · ` : '';
    return `Kazanım: ${code}${texts.join(' · ')}`;
  }
  const list = (question.outcomes || []).filter(Boolean);
  return list.length ? `Kazanım: ${list.join(' · ')}` : '';
}

/** 0.6667 → "%67"; yok → "–". */
export function ratePct(rate) {
  if (rate === null || rate === undefined || Number.isNaN(Number(rate))) return '–';
  return `%${Math.round(Number(rate) * 100)}`;
}

function netPart(value) {
  if (value === null || value === undefined) return '';
  return ` · ${Number(value).toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} net`;
}

/** Ders başlığı: "Fizik · 8 D · 4 Y · 2 B · 7,00 net". */
export function subjectHeading(subject) {
  return `${subject.title} · ${subject.correct} D · ${subject.wrong} Y · ${subject.blank} B${netPart(subject.net)}`;
}

/** Konuları ders sırasıyla gruplar: [{subject, topics}] — dersi `subjects`'te olmayan konu sona, kendi grubunda. */
export function topicGroups(analysis, groupKey = '') {
  const subjects = analysis?.subjects || [];
  const order = new Map(subjects.map((subject, index) => [subject.key, index]));
  const groups = new Map();
  for (const topic of analysis?.topics || []) {
    const key = topic.groupKey || topic.topic?.subjectId || '';
    if (groupKey && key !== groupKey) continue;
    if (!groups.has(key)) {
      const subject = subjects.find((item) => item.key === key)
        || { key, title: key, correct: 0, wrong: 0, blank: 0, net: null };
      groups.set(key, { subject, topics: [] });
    }
    groups.get(key).topics.push(topic);
  }
  return [...groups.values()].sort((a, b) => (order.get(a.subject.key) ?? 1e9) - (order.get(b.subject.key) ?? 1e9));
}

export function unmatchedText(count) {
  return count > 0 ? `${count} soru konuya eşlenmediği için konu analizine girmedi.` : '';
}

/** Ders seçimi seçenekleri: yalnız birden çok ders varsa. */
export function subjectOptions(analysis) {
  const subjects = (analysis?.subjects || []).filter((subject) => subject.questionCount > 0
    || (analysis?.topics || []).some((topic) => topic.groupKey === subject.key));
  if (subjects.length < 2) return [];
  return [{ key: '', label: TEXT.allSubjects }, ...subjects.map((subject) => ({ key: subject.key, label: subject.title }))];
}
