// BBD Akademi — Denemeler bölümünün ortak sözlük ve yardımcıları.
//
// exams.js ve parçaları (exams-results / exams-import / exams-keys /
// exams-analytics) aynı etiketleri ve küçük yardımcıları buradan alır; döngüsel
// import olmasın diye sabitler bölüm dosyasında DEĞİL burada durur.
//
// NET SUNUCUDA HESAPLANIR: buradaki `estimateNet` yalnız form doldurulurken yol
// gösterir. Kaydedilen net, sıra ve puan her zaman Akademi'nin yanıtından gelir.

import { h } from '../../../ui-kit/kit.js';
import { badge } from '../../../ui-kit/layout.js';
import { newKey } from '../api.js';
import { gradeText, num } from '../common.js';

// Deneme türünün TEK adı (Akademi V17): Akademi şablon ucu `ExamTemplate.name` (`exams/templates.py`
// `TYPE_NAMES`), uygulama (React / Flutter `ExamTypes.label`) ve panel aynı adı yazar.
export const EXAM_TYPES = {
  lgs: 'LGS', tyt: 'TYT', ayt_say: 'AYT Sayısal', ayt_ea: 'AYT Eşit Ağırlık',
  ayt_soz: 'AYT Sözel', ayt_dil: 'YDT (AYT Dil)', midterm: 'Ara sınav (yazılı)', internal: 'Kurum içi',
};

export const EXAM_STATUS = {
  draft: ['Taslak', 'dim'],
  results_pending: ['Sonuç girişinde', 'warn'],
  published: ['Yayımlandı', 'good'],
};

/** Cevap anahtarı / soru-konu eşlemesi doluluğu. */
export const FILL_STATUS = { none: ['Yok', 'dim'], partial: ['Eksik', 'warn'], complete: ['Tam', 'good'] };

export const SCORING_RULES = {
  lgs_3to1: '3 yanlış 1 doğruyu götürür (LGS)',
  yks_4to1: '4 yanlış 1 doğruyu götürür (YKS)',
  none: 'Yanlış doğruyu götürmez',
  custom: 'Özel kural',
};

export const RESULT_SOURCE = { manual: 'Elle', import: 'Toplu yükleme', answers: 'Cevap dizisi' };

export const IMPORT_STATUS = {
  uploaded: ['Yüklendi', 'info'],
  analyzing: ['Çözümleniyor', 'info'],
  needs_mapping: ['Sütun eşlemesi bekliyor', 'warn'],
  needs_review: ['İnceleme bekliyor', 'warn'],
  ready: ['Kaydetmeye hazır', 'info'],
  committing: ['Kaydediliyor', 'info'],
  committed: ['Kaydedildi', 'good'],
  failed: ['Başarısız', 'bad'],
  rolled_back: ['Geri alındı', 'dim'],
};

export const IMPORT_KINDS = {
  results: 'Sonuç dosyası (doğru / yanlış / boş)',
  answers: 'Cevap dizisi dosyası (optik okuyucu)',
};

/** Türden gelen ailesi — müfredat programındaki `exam` alanıyla eşleşir. */
export function examFamily(type) {
  if (type === 'lgs') return 'LGS';
  if (type === 'tyt') return 'TYT';
  if (String(type || '').startsWith('ayt_')) return 'AYT';
  return null;
}

/**
 * Türe göre hazır bölüm düzeni — TEK KAYNAK Akademi `catalog_listExamTemplates`
 * (`/v1/catalog/exam-templates`, README §14 madde 75). Panelde elle kopya
 * TUTULMAZ: ders anahtarları müfredat kataloğundandır (LGS İnkılap
 * `tc-inkilap-tarihi`, TYT alt testleri ayrı, AYT `tarih-1` …); uygulama
 * (Flutter + web) aynı listeyi okur. Şablon sürümlü platform verisidir: ETag'li
 * önbellek, bir saat taze.
 */
export const EXAM_TEMPLATES_PATH = '/v1/catalog/exam-templates';

/** Türün şablonu (`{type, name, scoring, scoringEditable, sections[], subjects[]}`) ya da `null`. */
export async function examTemplate(app, type) {
  const data = await app.api.getCached(EXAM_TEMPLATES_PATH, {}, { ttlMs: 60 * 60_000 });
  return (data?.items || []).find((item) => item.type === type) || null;
}

/** Yanlış kaç doğruyu götürür: 3, 4, n ya da null (götürmez). */
export function wrongsPerCorrect(exam) {
  const rule = exam?.scoring?.rule;
  if (rule === 'lgs_3to1') return 3;
  if (rule === 'yks_4to1') return 4;
  if (rule === 'custom') return Number(exam.scoring.wrongsPerCorrect) || null;
  if (rule === 'none') return null;
  if (exam?.type === 'lgs') return 3;
  return examFamily(exam?.type) ? 4 : null;
}

/** Yol gösteren net tahmini (kesin değer Akademi'de hesaplanır). */
export function estimateNet(exam, correct, wrong) {
  const per = wrongsPerCorrect(exam);
  const value = (Number(correct) || 0) - (per ? (Number(wrong) || 0) / per : 0);
  return Math.round(value * 100) / 100;
}

export function scoringText(exam) {
  const rule = exam?.scoring?.rule;
  if (rule === 'custom') return `${exam.scoring.wrongsPerCorrect || '?'} yanlış 1 doğruyu götürür (özel)`;
  return SCORING_RULES[rule] || '—';
}

export function sectionLabel(section) {
  return section?.subject?.shortName || section?.title || section?.subject?.name || 'Bölüm';
}

export function totalQuestions(exam) {
  return (exam?.sections || []).reduce((sum, section) => sum + (Number(section.questionCount) || 0), 0);
}

export function sortedSections(exam) {
  return [...(exam?.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
}

/** Excel sütun harfi: 0 → A, 25 → Z, 26 → AA. */
export function columnLetter(index) {
  let value = Number(index);
  if (!Number.isInteger(value) || value < 0) return '?';
  let text = '';
  do {
    text = String.fromCharCode(65 + (value % 26)) + text;
    value = Math.floor(value / 26) - 1;
  } while (value >= 0);
  return text;
}

/** Öğrenci hücresi: ad + sınıf / seviye. */
export function studentCell(student) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', student?.displayName || '—'),
    h('small', '', [student?.classGroup?.name, student?.gradeLevel ? gradeText(student.gradeLevel) : '']
      .filter((part) => part && part !== '—').join(' · ')));
  return node;
}

/** Deneme adı + yayın/tarih alt satırı (liste ve başlıklarda). */
export function examTitleCell(exam) {
  const node = h('div', 'ak-cell-stack');
  const head = h('span', 'ak-ex-name');
  head.append(h('b', '', exam?.name || '—'));
  if (exam?.isHistorical) head.append(badge('Geçmiş kayıt', 'dim'));
  node.append(head, h('small', '', [EXAM_TYPES[exam?.type] || exam?.type, exam?.publisher]
    .filter(Boolean).join(' · ')));
  return node;
}

/** Sayı hücresi için net yazımı: 12,25. */
export function netText(value) {
  return value === null || value === undefined ? '—' : num(value, 2);
}

/** Sayı → "%42". `ratio` 0–1. */
export function ratioText(ratio) {
  return ratio === null || ratio === undefined ? '—' : `%${num(Number(ratio) * 100, 0)}`;
}

/**
 * Çok parçalı dosya yükleme (köprü `upload`): okuma ilerlemesi `progress`
 * denetleyicisine (common `uploadProgress`) yazılır.
 */
export async function uploadFile(app, path, file, { fields = {}, progress = null, key = newKey('up') } = {}) {
  const clean = {};
  for (const [name, value] of Object.entries(fields)) {
    if (value === undefined || value === null || value === '') continue;
    clean[name] = typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value);
  }
  const result = await app.api.call('POST', path, {
    key,
    upload: {
      file,
      field: 'file',
      fields: clean,
      onProgress: (percent) => {
        progress?.reading(percent);
        if (percent >= 100) progress?.sending();
      },
    },
  });
  progress?.done();
  return result;
}

/** Kısa bekleme (yoklama döngüleri için). */
export const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
