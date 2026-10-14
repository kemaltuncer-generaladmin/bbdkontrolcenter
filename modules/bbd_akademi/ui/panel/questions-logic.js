// BBD Akademi — Hoca soruları: saf mantık (Akademi README §14 madde 63).
//
// Bilerek İMPORT'SUZ ve DOM'suz: `.mjs` kopyası Node'da yüklenip sınanır
// (`tests/test_akademi_hoca_cozum.py`). Ekran `questions.js`'tedir.

/** Soru durumları → [etiket, rozet tonu]. */
export const STATUS = {
  open: ['Bekliyor', 'warn'],
  forwarded: ['Yönlendirildi', 'info'],
  answered: ['Yanıtlandı', 'good'],
  closed: ['Kapandı', 'dim'],
};

/** Sorunun kime gittiği (`CoachQuestion.route`). */
export const ROUTES = {
  coach: 'Koçuna',
  subject_teacher: 'Dersin öğretmenine',
  pool: 'Öğretmen havuzuna',
};

/** Süzgeçler → sorgu (`admin_listCoachQuestions`). */
export const FILTERS = [
  { key: 'waiting', label: 'Bekleyen', query: { status: 'open' } },
  { key: 'overdue', label: 'Geciken', query: { overdue: true } },
  { key: 'answered', label: 'Yanıtlanan', query: { status: 'answered' } },
  { key: 'closed', label: 'Kapalı', query: { status: 'closed' } },
  { key: 'all', label: 'Tümü', query: {} },
];

export function filterQuery(key, teacherId = '') {
  const found = FILTERS.find((item) => item.key === key) || FILTERS[0];
  const query = { ...found.query };
  if (teacherId) query.coachId = teacherId;
  return query;
}

/** Sesli not üst sınırı (sunucu dosyadan da ölçer). */
export const MAX_VOICE_SECONDS = 120;
/** Görsel üst sınırı (sunucu ≤ 2 MB, EXIF siler); PDF 10 MB. */
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
export const MAX_PDF_BYTES = 10 * 1024 * 1024;
export const MAX_ATTACHMENTS = 5;

/**
 * SLA düzeyi: `late` (aşıldı, kırmızı), `soon` (4 saatten az, sarı), `ok`,
 * `done` (bekleyen değil).
 */
export function slaLevel(question, now = Date.now()) {
  if (!question || !['open', 'forwarded'].includes(question.status)) return 'done';
  const due = Date.parse(question.slaDueAt);
  if (question.overdue || (Number.isFinite(due) && due < now)) return 'late';
  if (Number.isFinite(due) && due - now < 4 * 3600 * 1000) return 'soon';
  return 'ok';
}

export const SLA = { late: ['Gecikti', 'bad'], soon: ['Süre azalıyor', 'warn'], ok: ['Bekliyor', 'info'] };

/** "Fizik · Newton'un Yasaları · s. 12 · 4. soru". */
export function contextLabel(context) {
  if (!context) return '';
  return [context.subject?.name, context.topic?.name, context.resource?.title, context.sectionTitle,
    context.page ? `s. ${context.page}` : '', context.questionNo ? `${context.questionNo}. soru` : '']
    .filter(Boolean).join(' · ');
}

/** `0:37`. */
export function clock(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

/** Ek türü: sesli not / görsel / PDF (sunucu türü sihirli bayttan yine bulur). */
export function partKind(file) {
  const type = String(file?.type || '').toLowerCase();
  const name = String(file?.name || '').toLowerCase();
  if (type === 'audio/mp4' || type === 'audio/x-m4a' || type === 'audio/aac' || name.endsWith('.m4a')) return 'voice';
  if (type === 'application/pdf' || name.endsWith('.pdf')) return 'pdf';
  if (type.startsWith('image/') || /\.(jpe?g|png|webp)$/.test(name)) return 'image';
  return null;
}

/** Yükleme öncesi denetim (UX; asıl kapı Akademi'dedir). Hata metni ya da ''. */
export function checkPart(file, { durationSeconds = null } = {}) {
  const kind = partKind(file);
  if (!kind) return 'Yalnız görsel (JPEG, PNG, WebP), PDF ya da m4a ses dosyası eklenebilir.';
  const size = Number(file?.size) || 0;
  if (kind === 'image' && size > MAX_IMAGE_BYTES * 4) return 'Görsel çok büyük (en çok 8 MB seçin; sunucu küçültür).';
  if (kind === 'pdf' && size > MAX_PDF_BYTES) return 'PDF en çok 10 MB olabilir.';
  if (kind === 'voice') {
    if (size > MAX_IMAGE_BYTES) return 'Ses dosyası en çok 2 MB olabilir.';
    if (durationSeconds !== null && durationSeconds > MAX_VOICE_SECONDS) return 'Sesli not en çok 2 dakika olabilir.';
  }
  return '';
}

/** `admin_uploadCoachQuestionFile` form alanları. */
export function uploadFields(kind, { durationSeconds = null, staffId = '' } = {}) {
  const fields = { kind: kind === 'voice' ? 'message_voice' : 'message_attachment' };
  if (kind === 'voice' && durationSeconds) {
    fields.durationSeconds = String(Math.min(MAX_VOICE_SECONDS, Math.max(1, Math.round(durationSeconds))));
  }
  if (staffId) fields.staffId = staffId;
  return fields;
}

/** `admin_answerCoachQuestion` gövdesi. Boşsa null. */
export function answerBody({ clientUuid, text = '', fileIds = [], staffId = '', acknowledge = false }) {
  const clean = String(text || '').trim();
  if (!clean && !fileIds.length) return null;
  const body = { clientUuid, attachmentFileIds: fileIds.slice(0, MAX_ATTACHMENTS) };
  if (clean) body.text = clean.slice(0, 2000);
  if (staffId) body.staffId = staffId;
  if (acknowledge) body.acknowledgeContactInfo = true;
  return body;
}

/**
 * Tarayıcı kaydı için MIME: Akademi yalnız `audio/mp4` (m4a / AAC) kabul eder.
 * `isTypeSupported` (MediaRecorder) verilir; desteklenen yoksa null — o zaman
 * ekran "dosya yükleyin" der (Chrome 126+, Safari ve Edge mp4 kaydeder).
 */
export function recorderMime(isTypeSupported) {
  const candidates = ['audio/mp4;codecs=mp4a.40.2', 'audio/mp4;codecs=mp4a', 'audio/mp4'];
  if (typeof isTypeSupported !== 'function') return null;
  for (const type of candidates) {
    try {
      if (isTypeSupported(type)) return type;
    } catch {
      // bazı tarayıcılar bilinmeyen codec'te atar
    }
  }
  return null;
}

/** Yanıtı yazan öğretmen için varsayılan: yönlendirilen, yoksa sorumlu hoca. */
export function defaultResponder(question) {
  return question?.forwardedTo?.id || question?.coach?.id || '';
}

/**
 * `admin_putCoachingSettings` gövdesi: okunan ayarlar + değişiklikler; salt
 * okunur alanlar düşer, `expectedVersion` eklenir (iyimser kilit).
 */
export function settingsPutBody(current, changes = {}) {
  const body = { ...(current || {}), ...changes };
  delete body.messageEditWindowMinutes;
  delete body.updatedAt;
  const version = Number(body.version) || 1;
  delete body.version;
  body.expectedVersion = version;
  if (!Array.isArray(body.questionPoolStaffIds)) body.questionPoolStaffIds = [];
  body.questionAnswerNotifyGuardian = Boolean(body.questionAnswerNotifyGuardian);
  return body;
}

/** Yanıtın görünür özeti (liste satırı). */
export function answerSummary(question) {
  const answers = question?.answers || [];
  if (!answers.length) return '';
  const last = answers[answers.length - 1];
  const parts = [];
  if (last.text) parts.push(last.text.length > 60 ? `${last.text.slice(0, 57)}…` : last.text);
  if (last.images?.length) parts.push(`${last.images.length} görsel`);
  if (last.voice?.length) parts.push('sesli not');
  return `${last.author?.displayName || 'Hoca'}: ${parts.join(' · ')}`;
}
