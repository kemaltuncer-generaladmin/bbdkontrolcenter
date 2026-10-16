// BBD Akademi — görev formu: saf mantık (DOM'suz; Node ile sınanır).
//
// Koç görevi (haftalık program, `admin_saveCoachWeek`) ve kurum plan ataması /
// şablon (`admin_createPlanAssignment`, `admin_*StudyPlanTemplate`) AYNI formu
// kullanır. Kullanıcı (15.10.2026): "bir sürü alan var, gerekli alanlar belli
// olmuyor, tipler kayıyor". Bu dosya formun TEK kural kaynağıdır:
//
//   · TÜR → görünen alanlar (`show`) ve zorunlular (`need`) — sunucunun
//     `fields_required_for_type` kurallarıyla birebir (Akademi README §14 madde 97
//     tablosu): koç görevi `kocluk.services.tasks.validate_input`, kurum ataması
//     `study.plan_admin._required_missing`. İstemci sunucudan GEVŞEK değildir
//     (kaydet → 422 sürprizi yok); yalnız iki yerde bilerek SIKIDIR: serbest görev
//     başlık ister, konu çalışması ders ister (ikisi de anlamsız kalmasın diye).
//   · Doğrulama alan başına Türkçe metin döner (alanın altına yazılır).
//   · Gövde yalnız SEÇİLİ türün alanlarını taşır: tür değişince girilen değerler
//     formda KORUNUR ama başka türün içeriği (ör. kaynak → video) gönderilmez.
//   · Akıllı varsayılan: hedef soru = süre ÷ 1,5 (5'e yuvarlı), seçili bölümlerin
//     soru sayısı biliniyorsa o.
//
// Metinler Flutter (`coaching_strings.dart`) ↔ React (`features/coaching/strings.ts`)
// ↔ KM aynıdır.

import { taskInput } from '../coaching-logic.js';

/** Tür adları (kartlar ve listeler). */
export const TYPE_LABELS = {
  question_solving: 'Soru çözümü',
  topic_study: 'Konu çalışma',
  resource_section: 'Kaynak',
  video: 'Video',
  review: 'Tekrar',
  exam: 'Deneme',
  mistake_review: 'Hata tekrarı',
  reading: 'Okuma',
  homework: 'Ödev',
  free: 'Serbest görev',
};

/** Kartın alt satırı: türün ne istediği bir bakışta. */
export const TYPE_HINTS = {
  question_solving: 'Hedef soru sayısı',
  topic_study: 'Ders ve konu',
  resource_section: 'Kitap, bölüm, sayfa',
  video: 'YouTube bağlantısı',
  review: 'Tekrar edilecek konu',
  exam: 'Kurumun denemesi',
  mistake_review: 'Hata defterinden',
  reading: 'Hedef sayfa',
  homework: 'Verilmiş ödev',
  free: 'Başlık yeterli',
};

/** Büyük kartlar (sabit sıra) ve "Diğer" satırı. */
export const PRIMARY_TYPES = ['question_solving', 'topic_study', 'resource_section', 'video', 'review', 'exam'];

/**
 * Formun kipine göre türler. `coach`: haftalık program (video var, ödev yok);
 * `date`: kurum plan ataması (video yalnız koç görevi, ödev var);
 * `offset`: şablon (deneme ve ödev belirli kayda bağlı → yok).
 */
export function typesFor(mode) {
  if (mode === 'coach') {
    return { primary: [...PRIMARY_TYPES], other: ['mistake_review', 'reading', 'free'] };
  }
  if (mode === 'offset') {
    return { primary: PRIMARY_TYPES.filter((key) => key !== 'video' && key !== 'exam'),
      other: ['mistake_review', 'reading', 'free'] };
  }
  return { primary: PRIMARY_TYPES.filter((key) => key !== 'video'), other: ['mistake_review', 'reading', 'homework', 'free'] };
}

export function allTypes(mode) {
  const { primary, other } = typesFor(mode);
  return [...primary, ...other];
}

/**
 * Tür → alanlar. Alan anahtarları: subject (ders + konu seçicisi), questions,
 * resource (kaynak + bölümler), pages, questionRange, video, exam, homework,
 * mistakeCount, readPages, title.
 * `need`: o kipte zorunlu olanlar (`*`).
 */
export function rulesFor(type, mode = 'coach') {
  const coach = mode === 'coach';
  switch (type) {
    case 'question_solving':
      return { show: ['subject', 'questions'], need: ['questions'] };
    case 'topic_study':
      return { show: ['subject'], need: ['subject'] };
    case 'resource_section':
      return { show: ['resource', 'pages', 'questions', ...(coach ? ['questionRange'] : [])],
        need: coach ? ['resource'] : ['resource', 'sections'] };
    case 'video':
      return { show: ['video', 'subject'], need: ['video'] };
    case 'review':
      return { show: ['subject'], need: coach ? ['subject'] : ['topic'] };
    case 'exam':
      return { show: ['exam'], need: ['exam'] };
    case 'mistake_review':
      return { show: ['mistakeCount', 'subject'], need: ['mistakeCount'] };
    case 'reading':
      return { show: ['readPages'], need: coach ? [] : ['readPages'] };
    case 'homework':
      return { show: ['homework'], need: ['homework'] };
    case 'free':
      return { show: ['title'], need: ['title'] };
    default:
      return { show: [], need: [] };
  }
}

/** Boş form taslağı (ortak alanlar + bütün türlerin içerik alanları). */
export function emptyForm(extra = {}) {
  return {
    type: 'question_solving', date: '', dayOffset: '', estimatedMinutes: 40, startTime: '', endTime: '',
    title: '', subjectId: '', subjectName: '', topicId: '', topicName: '',
    resourceId: '', resourceTitle: '', sectionIds: [], sectionTitles: [], sectionQuestions: null,
    pageFrom: '', pageTo: '', questionFrom: '', questionTo: '', targetQuestions: '', questionsTouched: false,
    targetPages: '', examId: '', examName: '', homeworkId: '', homeworkTitle: '', mistakeCount: 10,
    videos: [], instructions: '', priority: 'normal', proofPhoto: 'off', answersRequired: true,
    minAccuracyPercent: '', attachmentFileIds: [], addToShelf: false, note: '',
    ...extra,
  };
}

// ------------------------------------------------------------------ öneri

/**
 * Hedef soru önerisi: seçili bölümlerin soru sayısı biliniyorsa o (en çok 1000),
 * yoksa süre ÷ 1,5 → 5'e yuvarlı (40 dk → 25, 60 dk → 40). Süre yoksa null.
 */
export function suggestQuestions(minutes, sectionQuestions = null) {
  if (Number.isInteger(sectionQuestions) && sectionQuestions > 0) return Math.min(1000, sectionQuestions);
  const value = Number(minutes);
  if (!Number.isFinite(value) || value < 5) return null;
  return Math.max(5, Math.min(1000, Math.round(value / 1.5 / 5) * 5));
}

/** Video süresinden görev süresi: dakikaya yuvarla + 5 dk not alma payı (5–600). */
export function minutesForVideo(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value <= 0) return null;
  return Math.max(5, Math.min(600, Math.ceil(value / 60) + 5));
}

/** Boş başlık yerine gösterilecek/gönderilecek başlık ("Türev — 25 soru"). */
export function autoTitle(form) {
  const focus = form.topicName || form.subjectName || '';
  const count = intOf(form.targetQuestions);
  let text = '';
  switch (form.type) {
    case 'question_solving': text = `${focus ? `${focus} — ` : ''}${count ? `${count} soru` : 'soru çözümü'}`; break;
    case 'topic_study': text = focus ? `${focus} — konu çalışma` : ''; break;
    case 'resource_section': {
      const sections = (form.sectionTitles || []).slice(0, 2).join(', ')
        + ((form.sectionTitles || []).length > 2 ? ` +${form.sectionTitles.length - 2}` : '');
      const pages = intOf(form.pageFrom) ? `s. ${form.pageFrom}–${form.pageTo || '?'}` : '';
      text = [form.resourceTitle, sections, pages].filter(Boolean).join(' · ');
      break;
    }
    case 'video': text = form.videos?.[0]?.video?.title || (focus ? `${focus} — video` : ''); break;
    case 'review': text = focus ? `${focus} — tekrar` : ''; break;
    case 'exam': text = form.examName || ''; break;
    case 'mistake_review': text = `Hata tekrarı${focus ? ` — ${focus}` : ''}`; break;
    case 'reading': text = intOf(form.targetPages) ? `Okuma — ${intOf(form.targetPages)} sayfa` : ''; break;
    case 'homework': text = form.homeworkTitle || ''; break;
    default: text = '';
  }
  return text ? text.slice(0, 120) : '';
}

// ------------------------------------------------------------------ doğrulama

export const MESSAGES = {
  date: 'Gün seçin.',
  dateInPast: 'Geçmiş bir güne görev konamaz.',
  dayOffset: 'Gün no 1–{max} arasında olmalı.',
  minutes: 'Süre 5–600 dakika olmalı.',
  subject: 'Ders seçin.',
  topic: 'Konu seçin.',
  questions: 'Hedef soru sayısını yazın (1–1000).',
  resource: 'Kaynak seçin.',
  sections: 'Bölüm seçin ya da sayfa aralığını (ilk–son) yazın.',
  pageTo: 'Son sayfa ilk sayfadan küçük olamaz.',
  questionTo: 'Son soru ilk sorudan küçük olamaz.',
  number: 'Sayı 1 ya da büyük olmalı.',
  video: 'En az bir video ekleyin.',
  exam: 'Deneme seçin.',
  homework: 'Ödev seçin.',
  mistakeCount: 'Hata sayısı 1–50 olmalı.',
  readPages: 'Hedef sayfa sayısını yazın (1–2000).',
  readPagesRange: 'Hedef sayfa 1–2000 olmalı.',
  title: 'Serbest görev için başlık yazın.',
  accuracy: 'Başarı ölçütü %1–100 olmalı.',
  clock: 'Saati 17:30 gibi yazın.',
  clockPair: 'Bitiş saati için başlangıç saati de yazın.',
  clockEnd: 'Başlangıç saati için bitiş saati de yazın.',
  clockOrder: 'Bitiş saati başlangıçtan sonra olmalı.',
};

export const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

/** '' → null, '12' → 12, 'x' / '0' / '-3' → NaN. */
export function intOf(raw) {
  if (raw === null || raw === undefined) return null;
  const text = String(raw).trim();
  if (!text) return null;
  return /^\d+$/.test(text) && Number(text) > 0 ? Number(text) : NaN;
}

/**
 * Formu doğrular → `{errors: {alan: metin}, first: alan|null}`. Alan anahtarları
 * formdaki kutulardır (ör. `questions`, `pageTo`, `startTime`).
 * `mode`: coach | date | offset | patch; `today`: 'YYYY-MM-DD' (geçmiş gün denetimi),
 * `duration`: şablon süresi (gün no üst sınırı), `originalDate`: düzenlenen görevin günü.
 */
export function validate(form, { mode = 'coach', today = '', duration = null, originalDate = '' } = {}) {
  const errors = {};
  const put = (key, text) => { if (!errors[key]) errors[key] = text; };
  if (mode === 'offset') {
    const day = intOf(form.dayOffset);
    const max = duration || 60;
    if (!day || day > max) put('date', MESSAGES.dayOffset.replace('{max}', String(max)));
  } else if (!form.date) {
    put('date', MESSAGES.date);
  } else if (mode !== 'coach' && today && form.date < today && form.date !== originalDate) {
    put('date', MESSAGES.dateInPast);
  }
  const minutes = intOf(form.estimatedMinutes);
  if (!minutes || minutes < 5 || minutes > 600) put('estimatedMinutes', MESSAGES.minutes);
  const start = String(form.startTime || '').trim();
  const end = String(form.endTime || '').trim();
  if (start && !CLOCK.test(start)) put('startTime', MESSAGES.clock);
  if (end && !CLOCK.test(end)) put('endTime', MESSAGES.clock);
  if (end && !start) put('startTime', MESSAGES.clockPair);
  if (CLOCK.test(start) && !end && mode === 'coach') put('endTime', MESSAGES.clockEnd);
  if (CLOCK.test(start) && CLOCK.test(end) && end <= start) put('endTime', MESSAGES.clockOrder);

  if (mode !== 'patch') {
    const { show, need } = rulesFor(form.type, mode === 'patch' ? 'date' : mode);
    const needs = new Set(need);
    if (needs.has('subject') && !form.subjectId) put('subject', MESSAGES.subject);
    if (needs.has('topic') && !form.topicId) put('subject', MESSAGES.topic);
    if (show.includes('resource') && needs.has('resource') && !form.resourceId) put('resource', MESSAGES.resource);
    if (show.includes('pages')) {
      const first = intOf(form.pageFrom);
      const last = intOf(form.pageTo);
      if (Number.isNaN(first)) put('pageFrom', MESSAGES.number);
      if (Number.isNaN(last)) put('pageTo', MESSAGES.number);
      if (first && last && last < first) put('pageTo', MESSAGES.pageTo);
      if (needs.has('sections') && form.resourceId && !(form.sectionIds || []).length && !(first && last)) {
        put('resource', MESSAGES.sections);
      }
    }
    if (show.includes('questionRange')) {
      const first = intOf(form.questionFrom);
      const last = intOf(form.questionTo);
      if (Number.isNaN(first)) put('questionFrom', MESSAGES.number);
      if (Number.isNaN(last)) put('questionTo', MESSAGES.number);
      if (first && last && last < first) put('questionTo', MESSAGES.questionTo);
    }
    if (show.includes('questions')) {
      const count = intOf(form.targetQuestions);
      if (count === null && needs.has('questions')) put('questions', MESSAGES.questions);
      else if (count !== null && (!count || count > 1000)) put('questions', MESSAGES.questions);
    }
    if (needs.has('video') && !(form.videos || []).length) put('video', MESSAGES.video);
    if (needs.has('exam') && !form.examId) put('exam', MESSAGES.exam);
    if (needs.has('homework') && !form.homeworkId) put('homework', MESSAGES.homework);
    if (show.includes('mistakeCount')) {
      const count = intOf(form.mistakeCount);
      if (!count || count > 50) put('mistakeCount', MESSAGES.mistakeCount);
    }
    if (show.includes('readPages')) {
      const pages = intOf(form.targetPages);
      if (pages === null && needs.has('readPages')) put('readPages', MESSAGES.readPages);
      else if (pages !== null && (!pages || pages > 2000)) put('readPages', MESSAGES.readPagesRange);
    }
    if (needs.has('title') && !String(form.title || '').trim()) put('title', MESSAGES.title);
  } else {
    const count = intOf(form.targetQuestions);
    if (form.type === 'question_solving' && (!count || count > 1000)) put('questions', MESSAGES.questions);
    else if (count !== null && (!count || count > 1000)) put('questions', MESSAGES.questions);
  }
  if (mode === 'coach') {
    const accuracy = intOf(form.minAccuracyPercent);
    if (Number.isNaN(accuracy) || (accuracy !== null && accuracy > 100)) put('minAccuracyPercent', MESSAGES.accuracy);
  }
  const keys = Object.keys(errors);
  return { errors, first: keys.length ? FIELD_ORDER.find((key) => errors[key]) || keys[0] : null };
}

/** Hata özetinde ve odakta sıra (yukarıdan aşağı). */
export const FIELD_ORDER = ['type', 'date', 'estimatedMinutes', 'title', 'subject', 'questions', 'resource', 'pageFrom', 'pageTo',
  'video', 'exam', 'homework', 'mistakeCount', 'readPages', 'questionFrom', 'questionTo', 'startTime', 'endTime',
  'minAccuracyPercent'];

/** Sunucu `fields_required_for_type.details.fields` / `validation_error` alanı → form kutusu. */
const SERVER_FIELDS = {
  targetQuestions: 'questions', resourceId: 'resource', sectionIds: 'resource', topicId: 'subject', subjectId: 'subject',
  examId: 'exam', homeworkId: 'homework', mistakeReview: 'mistakeCount', targetPages: 'readPages', 'coach.videos': 'video',
  coach: 'video', videoId: 'video', pageFrom: 'pageFrom', pageTo: 'pageTo', startTime: 'startTime', endTime: 'endTime',
  date: 'date', estimatedMinutes: 'estimatedMinutes', title: 'title', questionTo: 'questionTo', questionFrom: 'questionFrom',
  readingShelfItemId: 'readPages',
};

/**
 * Akademi hatası → alan hataları. `code`/`details` AkademiError'dan. Bilinmeyen alan
 * `_form` anahtarına düşer (formun altındaki genel hata).
 */
export function serverErrors(code, details, form = {}) {
  const out = {};
  if (code === 'fields_required_for_type') {
    const fields = Array.isArray(details?.fields) ? details.fields : [];
    for (const name of fields) {
      const key = SERVER_FIELDS[name] || '_form';
      const fallback = { questions: MESSAGES.questions, resource: form.resourceId ? MESSAGES.sections : MESSAGES.resource,
        subject: form.type === 'review' ? MESSAGES.topic : MESSAGES.subject, exam: MESSAGES.exam, homework: MESSAGES.homework,
        mistakeCount: MESSAGES.mistakeCount, readPages: MESSAGES.readPages, video: MESSAGES.video }[key];
      out[key] = fallback || 'Bu görev türü için gerekli alanlar eksik.';
    }
    if (!fields.length) out._form = 'Bu görev türü için gerekli alanlar eksik.';
    return out;
  }
  const list = Array.isArray(details) ? details : (details?.field ? [details] : []);
  for (const item of list) {
    const raw = String(item?.field || '').split('.').pop().replace(/\[\d+\]$/, '');
    const key = SERVER_FIELDS[raw];
    if (key) out[key] = item.message || out[key] || 'Geçersiz değer.';
  }
  if (code === 'date_in_past') out.date = MESSAGES.dateInPast;
  if (code === 'invalid_time_range') out.endTime = MESSAGES.clockOrder;
  return out;
}

/** Haftalık program kaydında görev başına hata kodu → metin. */
export const SAVE_ERRORS = {
  plan_task_not_found: 'Görev bulunamadı (silinmiş olabilir).',
  not_coach_task: 'Öğrencinin kendi işi değiştirilemez.',
  resource_not_on_shelf: 'Kaynak öğrencinin rafında değil — "rafa da ekle"yi işaretleyin.',
  topic_not_found: 'Konu bulunamadı.',
  homework_not_found: 'Ödev bulunamadı.',
  exam_not_found: 'Deneme bulunamadı.',
  task_locked: 'Görev kilitli (tamamlanmış ya da kontrol edilmiş).',
  fields_required_for_type: 'Bu görev türü için gerekli alanlar eksik.',
  validation_error: 'Bazı alanlar geçersiz.',
};

export function saveErrorText(code) {
  return SAVE_ERRORS[code] || `Kaydedilemedi (${code || 'hata'}).`;
}

// ------------------------------------------------------------------ gövde

/** Formdaki gün → `YYYY-MM-DD`; görev kipine yabancı alanlar atılır. */
function pickContent(form, mode) {
  const { show } = rulesFor(form.type, mode);
  const has = new Set(show);
  const out = { ...form };
  if (!has.has('subject')) { out.subjectId = ''; out.topicId = ''; }
  if (!has.has('resource')) { out.resourceId = ''; out.sectionIds = []; out.addToShelf = false; }
  if (!has.has('pages')) { out.pageFrom = ''; out.pageTo = ''; }
  if (!has.has('questionRange')) { out.questionFrom = ''; out.questionTo = ''; }
  if (!has.has('questions')) out.targetQuestions = '';
  if (!has.has('video')) out.videos = [];
  return out;
}

/**
 * Koç görevi → `PlanAssignmentTaskInput` (koç alanlarıyla; `admin_saveCoachWeek`).
 * Önce `validate` çağrılmış olmalı; yine de hata varsa `{error}` döner.
 */
export function coachTaskBody(form, { draft = true } = {}) {
  const clean = pickContent(form, 'coach');
  const title = String(clean.title || '').trim() || autoTitle(clean);
  const built = taskInput({ ...clean, title }, { draft });
  if (built.error) return built;
  const task = built.task;
  const start = String(form.startTime || '').trim();
  const end = String(form.endTime || '').trim();
  if (start && end) { task.startTime = start; task.endTime = end; }
  if (form.type === 'exam' && form.examId) task.examId = form.examId;
  if (form.type === 'homework' && form.homeworkId) task.homeworkId = form.homeworkId;
  if (form.type === 'reading' && intOf(form.targetPages)) task.targetPages = intOf(form.targetPages);
  if (form.type === 'mistake_review') {
    task.mistakeReview = { subjectId: form.subjectId || null, topicId: form.topicId || null,
      count: intOf(form.mistakeCount) || 10 };
  }
  return { task };
}

/**
 * Kurum ataması / şablon görevi (plan-tasks.js `taskBody`'nin girdisi: görünüm
 * adları `_view` ile). `mode`: date | offset.
 */
export function planTask(form, mode = 'date') {
  const clean = pickContent(form, mode);
  const out = {
    type: clean.type,
    estimatedMinutes: intOf(clean.estimatedMinutes),
    startTime: String(clean.startTime || '').trim() || null,
    endTime: String(clean.endTime || '').trim() || null,
    subjectId: clean.subjectId || null,
    topicId: clean.topicId || null,
    resourceId: clean.resourceId || null,
    sectionIds: clean.resourceId ? [...(clean.sectionIds || [])].slice(0, 20) : [],
    targetQuestions: intOf(clean.targetQuestions) || null,
    targetPages: clean.type === 'reading' ? intOf(clean.targetPages) || null : null,
    note: String(clean.note || '').trim() || null,
    _view: { subjectName: clean.subjectName || '', topicName: clean.topicName || '', resourceTitle: clean.resourceTitle || '',
      sectionTitles: clean.sectionTitles || [], examName: clean.examName || '', homeworkTitle: clean.homeworkTitle || '' },
  };
  if (mode === 'offset') {
    out.dayOffset = (intOf(clean.dayOffset) || 1) - 1;
  } else {
    out.date = clean.date;
    out.pageFrom = intOf(clean.pageFrom) || null;
    out.pageTo = intOf(clean.pageTo) || null;
    out.examId = clean.type === 'exam' ? clean.examId || null : null;
    out.homeworkId = clean.type === 'homework' ? clean.homeworkId || null : null;
    out.mistakeReview = clean.type === 'mistake_review'
      ? { subjectId: clean.subjectId || null, topicId: clean.topicId || null, count: intOf(clean.mistakeCount) || 10 } : null;
  }
  const typed = String(clean.title || '').trim();
  out.title = typed || autoTitle(clean) || null;
  return out;
}

/** Plan görevi (taskListField satırı) → form taslağı. */
export function formFromPlanTask(task = {}, extra = {}) {
  const view = task._view || {};
  // Otomatik kurulmuş başlık boş açılır: içerik değişince yeniden kurulsun.
  const generated = task.title && task.title === autoTitle({ ...emptyForm(), ...task, ...view,
    targetQuestions: task.targetQuestions ?? '', mistakeCount: task.mistakeReview?.count ?? 10 });
  return emptyForm({
    type: task.type || 'question_solving',
    date: task.date || '',
    dayOffset: task.dayOffset !== undefined && task.dayOffset !== null ? Number(task.dayOffset) + 1 : '',
    estimatedMinutes: task.estimatedMinutes ?? 40,
    startTime: task.startTime || '', endTime: task.endTime || '',
    title: generated ? '' : task.title || '',
    subjectId: task.subjectId || task.mistakeReview?.subjectId || '', subjectName: view.subjectName || '',
    topicId: task.topicId || task.mistakeReview?.topicId || '', topicName: view.topicName || '',
    resourceId: task.resourceId || '', resourceTitle: view.resourceTitle || '',
    sectionIds: task.sectionIds || [], sectionTitles: view.sectionTitles || [],
    pageFrom: task.pageFrom ?? '', pageTo: task.pageTo ?? '',
    targetQuestions: task.targetQuestions ?? '', questionsTouched: task.targetQuestions !== null && task.targetQuestions !== undefined,
    targetPages: task.targetPages ?? '', examId: task.examId || '', examName: view.examName || '',
    homeworkId: task.homeworkId || '', homeworkTitle: view.homeworkTitle || '',
    mistakeCount: task.mistakeReview?.count ?? 10, note: task.note || '',
    ...extra,
  });
}

/** Koç görevi (haftalık programdaki `PlanTask`) → form taslağı. */
export function formFromCoachTask(task) {
  const coach = task.coachTask || {};
  return emptyForm({
    type: TYPE_LABELS[task.type] ? task.type : 'free',
    date: task.date, estimatedMinutes: task.estimatedMinutes ?? 40,
    startTime: task.startTime || '', endTime: task.endTime || '',
    title: task.title || '',
    subjectId: task.subject?.id || task.mistakeReview?.subjectId || '', subjectName: task.subject?.name || '',
    topicId: task.topic?.id || task.mistakeReview?.topicId || '', topicName: task.topic?.name || '',
    resourceId: task.resource?.id || '', resourceTitle: task.resource?.title || '',
    sectionIds: (task.sections || []).map((section) => section.id),
    sectionTitles: (task.sections || []).map((section) => section.title),
    pageFrom: task.pageFrom ?? '', pageTo: task.pageTo ?? '',
    targetQuestions: task.targetQuestions ?? '', questionsTouched: true,
    targetPages: task.targetPages ?? '', examId: task.examId || '', examName: '',
    homeworkId: task.homeworkId || '', mistakeCount: task.mistakeReview?.count ?? 10,
    note: task.note || '', instructions: coach.instructions || '',
    questionFrom: coach.questionFrom ?? '', questionTo: coach.questionTo ?? '',
    priority: coach.priority || 'normal', proofPhoto: coach.proof?.photo || 'off',
    answersRequired: coach.proof?.answersRequired !== false,
    minAccuracyPercent: coach.successCriterion?.minAccuracyPercent ?? '',
    videos: (coach.videos || []).map((video, index) => ({ videoId: video.video?.videoId, video: video.video,
      libraryItemId: video.libraryItemId, startSeconds: video.startSeconds, endSeconds: video.endSeconds,
      chapters: video.chapters || [], order: video.order ?? index, requireOrder: video.requireOrder,
      checkpoints: (video.checkpoints || []).map((item) => ({ atSeconds: item.atSeconds ?? null, prompt: item.prompt || '',
        kind: item.kind === 'short_text' ? 'short_text' : 'single_choice', options: item.options || [],
        correctIndex: item.correctIndex ?? 0 })) })),
    attachmentFileIds: (coach.attachments || []).map((file) => file.id),
  });
}

/**
 * "Kaydet ve yeni ekle": sıradaki görevin taslağı. Gün, tür, süre, ders ve
 * kanıt kuralları KALIR; içerik (başlık, konu, bölüm, video, deneme, talimat) boşalır.
 */
export function nextForm(form) {
  return emptyForm({
    type: form.type, date: form.date, dayOffset: form.dayOffset, estimatedMinutes: form.estimatedMinutes,
    subjectId: form.subjectId, subjectName: form.subjectName,
    resourceId: form.type === 'resource_section' ? form.resourceId : '',
    resourceTitle: form.type === 'resource_section' ? form.resourceTitle : '',
    priority: form.priority, proofPhoto: form.proofPhoto, answersRequired: form.answersRequired,
    mistakeCount: form.mistakeCount,
  });
}

/** "Daha fazla seçenek" altında DOLU olan isteğe bağlı alan sayısı (başlıkta rozet). */
export function filledOptional(form, mode = 'coach') {
  const keys = ['startTime', 'note'];
  if (form.type !== 'free') keys.push('title');
  if (mode === 'coach') keys.push('instructions', 'minAccuracyPercent');
  let count = keys.filter((key) => String(form[key] ?? '').trim()).length;
  if (mode === 'coach') {
    if (form.priority && form.priority !== 'normal') count += 1;
    if (form.proofPhoto && form.proofPhoto !== 'off') count += 1;
    if (form.answersRequired === false) count += 1;
    if ((form.attachmentFileIds || []).length) count += 1;
  }
  return count;
}

/** Plan ataması önizlemesi (`PlanAssignmentPreview`) → okunur satırlar. */
export function previewLines(preview, fmt = (iso) => iso) {
  if (!preview) return [];
  const lines = [`${preview.studentCount} öğrenciye ${preview.taskCount} görev (toplam ${preview.instanceCount}).`];
  if (preview.busyStudentCount) {
    const days = (preview.days || []).filter((day) => day.busyStudentCount)
      .map((day) => `${fmt(day.date)}: ${day.busyStudentCount} öğrenci`).join(', ');
    lines.push(`Çakışma: ${preview.busyStudentCount} öğrencinin aynı gün zaten görevi var (${days}).`);
  } else {
    lines.push('Çakışma yok: görev günlerinde öğrencilerin başka yapılmamış görevi yok.');
  }
  for (const shift of preview.holidayShifted || []) lines.push(`Tatil: ${fmt(shift.from)} → ${fmt(shift.to)} gününe kayar.`);
  if ((preview.holidaySkipped || []).length) {
    lines.push(`Tatil: ${preview.holidaySkipped.map(fmt).join(', ')} günündeki görevler atlanacak.`);
  }
  return lines;
}

// ------------------------------------------------------------------ hedefin müfredatı (Akademi README §14 madde 102)

/**
 * Çok öğrenciye atamada programın kapsamı: `targetStudentCount` / `target.studentCount`.
 * Hepsinde (kesişim) → "hepsinde"; bir kısmında (birleşim) → "3/5 öğrencide"; 0 → yalnız sınıf seviyesinden.
 * Tek öğrencide ya da sayı yoksa boş. Web (`coverageText`) ve mobil (`coverageText`) aynı metin.
 */
export function coverageText(count, total) {
  if (count === null || count === undefined || !total) return '';
  if (count <= 0) return 'sınıf seviyesinden';
  if (count >= total) return total > 1 ? 'hepsinde' : '';
  return `${count}/${total} öğrencide`;
}

/** Hedefin programları (`recommended`): çok öğrencide olan önce, eşitlikte sunucu sırası (seviye → ders) korunur. */
export function targetPrograms(items) {
  return (items || [])
    .map((program, index) => ({ program, index }))
    .filter(({ program }) => program?.recommended)
    .sort((a, b) => ((b.program.targetStudentCount ?? 0) - (a.program.targetStudentCount ?? 0)) || (a.index - b.index))
    .map(({ program }) => program);
}
