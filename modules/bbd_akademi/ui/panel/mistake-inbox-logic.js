// BBD Akademi — Hata defteri gelen kutusu: saf mantık (Akademi README §14 madde 104).
//
// Bilerek İMPORT'SUZ ve DOM'suz: `.mjs` kopyası Node'da yüklenip sınanır
// (`tests/test_akademi_hata_gelen_kutusu.py`). Ekran `mistake-inbox.js`'tedir.
// Metinler Flutter / React ile aynı (`mistake_inbox_strings.dart`).

/** Gelen kutusu durumları → [etiket, rozet tonu]. */
export const STATUS = {
  new: ['Yeni', 'info'],
  waiting: ['Yanıt bekliyor', 'warn'],
  answered: ['Yanıtlandı', 'good'],
  closed: ['Yanıt gerekmiyor', 'dim'],
};

/** Kayıt kime neden düştü (`MistakeInboxItem.route`). */
export const ROUTES = {
  coach: 'Koçuna',
  subject_teacher: 'Dersin öğretmenine',
  pool: 'Öğretmen havuzuna',
};

/** Süzgeçler (`admin_listMistakeInbox.status`). */
export const FILTERS = [
  { key: 'waiting', label: 'Bekleyen' },
  { key: 'new', label: 'Yeni' },
  { key: 'answered', label: 'Yanıtlanan' },
  { key: 'closed', label: 'Kapatılan' },
  { key: 'all', label: 'Hepsi' },
];

/** Liste sorgusu: durum + öğretmen (sorumlu) + öğrenci + ders süzgeci. */
export function filterQuery(key, { staffId = '', studentId = '', subjectId = '' } = {}) {
  const found = FILTERS.find((item) => item.key === key) || FILTERS[0];
  const query = { status: found.key };
  if (staffId) query.staffId = staffId;
  if (studentId) query.studentId = studentId;
  if (subjectId) query.subjectId = subjectId;
  return query;
}

export const MAX_ATTACHMENTS = 6;
export const MAX_VOICE = 2;

/** Video bağlantısı (http / https, boşluksuz; sözleşme deseni `^https?://\S+$`). */
export function validVideoUrl(raw) {
  const value = String(raw || '').trim();
  if (!value || /\s/.test(value)) return false;
  try {
    const url = new URL(value);
    return (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.host);
  } catch {
    return false;
  }
}

/** `admin_uploadMistakeAnswerFile` form alanları (tür sunucuda sihirli bayttan bulunur). */
export function uploadFields(kind, { durationSeconds = null, staffId = '' } = {}) {
  const fields = {};
  if (kind === 'voice' && durationSeconds) {
    fields.durationSeconds = String(Math.min(120, Math.max(1, Math.round(durationSeconds))));
  }
  if (staffId) fields.staffId = staffId;
  return fields;
}

/** `admin_answerMistake` gövdesi. Metin, ek ya da geçerli bağlantı yoksa null. */
export function answerBody({ clientUuid, text = '', fileIds = [], videoUrl = '', staffId = '' }) {
  const clean = String(text || '').trim();
  const video = validVideoUrl(videoUrl) ? String(videoUrl).trim() : '';
  if (!clean && !fileIds.length && !video) return null;
  const body = { clientUuid, attachmentFileIds: fileIds.slice(0, MAX_ATTACHMENTS) };
  if (clean) body.text = clean.slice(0, 2000);
  if (video) body.videoUrl = video;
  if (staffId) body.staffId = staffId;
  return body;
}

/** Kaynağın kısa özeti ("Bilfen Soru Bankası · s. 48 · 7. soru"; Flutter `MistakeSource.label`). */
export function sourceLabel(source) {
  if (!source) return '';
  if (source.kind === 'resource') {
    return [source.resourceTitle || 'Kaynak', source.sectionTitle, source.page ? `s. ${source.page}` : '',
      source.questionNo ? `${source.questionNo}. soru` : ''].filter(Boolean).join(' · ');
  }
  if (source.kind === 'exam' || source.kind === 'external_exam') {
    return [source.examName || 'Deneme', source.examQuestionNo ? `${source.examQuestionNo}. soru` : '']
      .filter(Boolean).join(' · ');
  }
  return source.description || 'Diğer';
}

/** "Fizik · Newton'un Yasaları · Bilfen · s. 48". */
export function mistakeLabel(mistake) {
  if (!mistake) return '';
  return [mistake.subject?.name, mistake.topic?.name, sourceLabel(mistake.source)].filter(Boolean).join(' · ');
}

/** Varsayılan yanıtlayan: kaydın sorumlusu. */
export function defaultResponder(item) {
  return item?.assignedTo?.id || '';
}

/** Son çözümün görünür özeti (liste satırı). */
export function answerSummary(item) {
  const answers = item?.mistake?.teacherAnswers || [];
  if (!answers.length) return '';
  const last = answers[answers.length - 1];
  const parts = [];
  if (last.text) parts.push(last.text.length > 60 ? `${last.text.slice(0, 57)}…` : last.text);
  if (last.images?.length) parts.push(`${last.images.length} görsel`);
  if (last.voice?.length) parts.push('sesli anlatım');
  if (last.files?.length) parts.push(`${last.files.length} PDF`);
  if (last.videoUrl) parts.push('video');
  return `${last.author?.displayName || 'Öğretmen'}: ${parts.join(' · ')}`;
}
