// BBD Akademi — Mesaj denetimi: saf mantık (DOM'suz, import'suz; Node ile sınanır).
//
// Metinler mobil/web ile AYNIDIR: şikâyet nedenleri Flutter `ReportReason`
// (`messaging_models.dart`) ve React `REPORT_REASONS` (`messaging/model.ts`)
// ile birebir; kilit metni `MessagingStrings.readOnlyLocked`.
//
// Sözleşme (draft.3, b973148; PLATFORM §5 kural 6):
//   · yönetici yazışmayı YALNIZ şikâyet üzerinden görür (`admin_getMessageReport`
//     bağlamı en çok 10 + 10 mesaj; erişim denetim kaydına yazılır);
//   · KM denetim araması / konuşmayı açma / dışa aktarma GEREKÇE ister (en az
//     10 karakter; gerekçe URL'ye konmaz, gövdededir); aralık en çok 1 yıl.

/** `MessageReportReason` → metin (Flutter `ReportReason.label`). */
export const REPORT_REASONS = {
  inappropriate: 'Uygunsuz içerik',
  bullying: 'Zorbalık / baskı',
  contact_request: 'İletişim bilgisi istendi',
  spam: 'İstenmeyen mesaj',
  other: 'Diğer',
};

/** `MessageReportStatus` → [metin, ton]. */
export const REPORT_STATUS = {
  open: ['Bekliyor', 'warn'],
  dismissed: ['Reddedildi', 'dim'],
  actioned: ['İşlem yapıldı', 'good'],
};

/** Kuyruk süzgeci (sıra = ekran sırası). */
export const REPORT_FILTERS = [
  { key: 'open', label: 'Bekleyen' },
  { key: 'actioned', label: 'İşlem yapılan' },
  { key: 'dismissed', label: 'Reddedilen' },
];

/** `MessageReportDecision.decision` → [metin, açıklama]. */
export const DECISIONS = {
  dismiss: ['Şikâyeti reddet', 'Mesajda sorun yok; konuşma olduğu gibi kalır.'],
  warn_sender: ['Göndereni uyar', 'Gönderene kurum uyarısı gider; konuşma açık kalır.'],
  hide_message: ['Mesajı gizle', 'Mesaj alıcılardan gizlenir; özgün içerik denetim kaydında kalır.'],
  lock_thread: ['Konuşmayı kilitle', 'Konuşmaya yeni mesaj yazılamaz (kurum tarafından kilitlendi).'],
};

/** Verilmiş kararın kısa metni (`MessageReport.decision`). */
export const DECISION_DONE = {
  dismiss: 'Reddedildi',
  warn_sender: 'Gönderen uyarıldı',
  hide_message: 'Mesaj gizlendi',
  lock_thread: 'Konuşma kilitlendi',
};

/** `MessageThreadKind` → metin. */
export const THREAD_KINDS = {
  coach_student: 'Koç – öğrenci',
  coach_guardian: 'Koç – veli',
  question: 'Hoca sorusu',
  broadcast: 'Koç duyurusu',
};

/** `MessageThreadStatus` → [metin, ton]. */
export const THREAD_STATUS = {
  open: ['Açık', 'good'],
  archived: ['Arşivde', 'dim'],
  locked: ['Kilitli', 'bad'],
};

/** `MessageParticipantRole` → metin. */
export const ROLES = {
  coach: 'Koç',
  teacher: 'Öğretmen',
  student: 'Öğrenci',
  guardian: 'Veli',
  admin: 'Yönetici',
};

/** `MessageFlag` → [metin, ton]. */
export const FLAGS = {
  contact_info: ['İletişim bilgisi', 'warn'],
  reported: ['Şikâyet edildi', 'bad'],
};

/** Kilit metni — mobil `MessagingStrings.readOnlyLocked` ile aynı. */
export const LOCKED_TEXT = 'Bu konuşma kurum tarafından kilitlendi.';

export const AUDIT_REASON_MIN = 10;
export const AUDIT_REASON_MAX = 500;
export const NOTE_MIN = 3;
export const SEARCH_TEXT_MIN = 3;
export const MAX_RANGE_DAYS = 366;

/** Katılımcı → "Ad (Rol)". */
export function participantText(person) {
  if (!person) return '—';
  const role = ROLES[person.role];
  return role ? `${person.displayName || '—'} (${role})` : (person.displayName || '—');
}

/** Konuşmanın katılımcıları tek satır. */
export function participantsText(list) {
  return (list || []).map(participantText).join(', ') || '—';
}

/** Mesaj gövdesi özeti: silinen / ek / kart. */
export function messagePreview(message, max = 120) {
  if (!message) return 'Mesaj silinmiş';
  if (message.deletedAt || message.body === null) return 'Mesaj silindi';
  const parts = [];
  if (message.body) parts.push(String(message.body));
  const files = (message.attachments || []).length;
  if (files) parts.push(`${files} ek`);
  if (message.card?.title) parts.push(`Kart: ${message.card.title}`);
  const text = parts.join(' · ') || '—';
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** Ek türü → metin. */
export function attachmentText(attachment) {
  const kind = { image: 'Görsel', pdf: 'PDF', voice: 'Sesli not' }[attachment?.kind] || 'Ek';
  return attachment?.kind === 'voice' && attachment.durationSeconds
    ? `${kind} (${attachment.durationSeconds} sn)` : kind;
}

/** Denetim gerekçesi denetimi → hata metni ya da null. */
export function reasonProblem(reason) {
  const text = String(reason || '').trim();
  if (text.length < AUDIT_REASON_MIN) {
    return `Gerekçe en az ${AUDIT_REASON_MIN} karakter olmalı (her erişim denetim kaydına yazılır).`;
  }
  if (text.length > AUDIT_REASON_MAX) return `Gerekçe en çok ${AUDIT_REASON_MAX} karakter olabilir.`;
  return null;
}

/** `MessageAuditReason` gövdesi. */
export function auditReason({ reason, caseRef } = {}) {
  const body = { reason: String(reason || '').trim() };
  const ref = String(caseRef || '').trim();
  body.caseRef = ref ? ref.slice(0, 64) : null;
  return body;
}

function dayIndex(iso) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
  if (!year || !month || !day) return null;
  return Date.UTC(year, month - 1, day) / 86_400_000;
}

/**
 * Arama süzgeci denetimi → hata metni ya da null. Sözleşme: metin en az 3
 * karakter; aralık en çok 1 yıl (`422 range_too_long`); bitiş başlangıçtan önce olamaz.
 */
export function searchProblem(filters = {}) {
  const text = String(filters.text || '').trim();
  if (text && text.length < SEARCH_TEXT_MIN) return `Metin araması en az ${SEARCH_TEXT_MIN} karakter olmalı.`;
  if (text.length > 100) return 'Metin araması en çok 100 karakter olabilir.';
  if (filters.from && filters.to) {
    const from = dayIndex(filters.from);
    const to = dayIndex(filters.to);
    if (from !== null && to !== null) {
      if (to < from) return 'Bitiş tarihi başlangıçtan önce olamaz.';
      if (to - from > MAX_RANGE_DAYS) return 'Tarih aralığı en çok 1 yıl olabilir.';
    }
  }
  return null;
}

/** `MessageSearchRequest` gövdesi (boş süzgeç gönderilmez). */
export function searchBody(filters = {}, reason = {}, { cursor = null, pageSize = 50 } = {}) {
  const body = auditReason(reason);
  const text = String(filters.text || '').trim();
  if (filters.studentId) body.studentId = filters.studentId;
  if (filters.staffId) body.staffId = filters.staffId;
  if (filters.kind) body.kind = filters.kind;
  if (filters.from) body.from = filters.from;
  if (filters.to) body.to = filters.to;
  if (text) body.text = text;
  body.flaggedOnly = Boolean(filters.flaggedOnly);
  body.pageSize = pageSize;
  if (cursor) body.cursor = cursor;
  return body;
}

/** `MessageReportDecision` gövdesi; geçersizse hata fırlatır. */
export function decisionBody(decision, note) {
  if (!DECISIONS[decision]) throw new Error('Karar seçin.');
  const text = String(note || '').trim();
  if (text.length < NOTE_MIN) throw new Error(`Not en az ${NOTE_MIN} karakter olmalı.`);
  return { decision, note: text.slice(0, 1000) };
}

/** `MessageThreadLock` gövdesi. */
export function lockBody(locked, reason) {
  const text = String(reason || '').trim();
  if (text.length < NOTE_MIN) throw new Error(`Gerekçe en az ${NOTE_MIN} karakter olmalı.`);
  return { locked: Boolean(locked), reason: text.slice(0, 500) };
}

/** Bağlam mesajları içinde şikâyet edilen mesaj mı? */
export function isReported(message, report) {
  return Boolean(message && report?.message && message.id === report.message.id);
}

/**
 * Denetim görünümündeki mesajın gösterilecek gövdesi: silinmişse özgün içerik
 * (yalnız denetimde döner), düzenlenmişse güncel gövde + önceki sürümler.
 */
export function auditBody(message) {
  if (!message) return { text: '—', deleted: false, edits: [] };
  const deleted = Boolean(message.deletedAt || message.body === null);
  const text = deleted ? (message.originalBody ?? '(özgün içerik yok)') : (message.body ?? '');
  const edits = (message.edits || []).map((edit) => ({ previousBody: edit.previousBody,
    editedAt: edit.editedAt }));
  return { text, deleted, edits };
}

// ---------------------------------------------------------------- yazışma saklama süresi
//
// Akademi README §14 madde 84: koç yazışmaları (ekleri, sesli notlar, "Hocaya
// sor" soruları) ilişkinin / öğrenimin bitişinden `messageRetentionYears` yıl
// sonra gece işiyle silinir. Ayar `CoachingSettings` içindedir (1–10, vars. 2);
// yazma `admin_putCoachingSettings` (iyimser kilit `expectedVersion`).

export const RETENTION_MIN = 1;
export const RETENTION_MAX = 10;
export const RETENTION_DEFAULT = 2;

/** Seçenekler: 1 yıl … 10 yıl (değer metin; `select` ile uyumlu). */
export function retentionOptions() {
  const out = [];
  for (let year = RETENTION_MIN; year <= RETENTION_MAX; year += 1) {
    out.push({ value: String(year), label: year === RETENTION_DEFAULT ? `${year} yıl (varsayılan)` : `${year} yıl` });
  }
  return out;
}

/** Geçerli yıl sayısı ya da `null` (tam sayı, 1–10). */
export function retentionYears(value) {
  const text = String(value ?? '').trim();
  if (!/^\d+$/.test(text)) return null;
  const year = Number(text);
  return year >= RETENTION_MIN && year <= RETENTION_MAX ? year : null;
}

/** Ekrandaki özet: "2 yıl". Ayar okunamadıysa varsayılan. */
export function retentionText(settings) {
  return `${retentionYears(settings?.messageRetentionYears) ?? RETENTION_DEFAULT} yıl`;
}

/**
 * `admin_putCoachingSettings` gövdesi: okunan ayarlar + yeni süre; salt okunur
 * alanlar düşer, `expectedVersion` eklenir. Geçersiz süre → hata.
 */
export function retentionPutBody(current, years) {
  const year = retentionYears(years);
  if (year === null) throw new Error(`Saklama süresi ${RETENTION_MIN}–${RETENTION_MAX} yıl olmalı.`);
  const body = { ...(current || {}), messageRetentionYears: year };
  delete body.messageEditWindowMinutes;
  delete body.updatedAt;
  const version = Number(body.version) || 1;
  delete body.version;
  body.expectedVersion = version;
  if (!Array.isArray(body.questionPoolStaffIds)) body.questionPoolStaffIds = [];
  body.questionAnswerNotifyGuardian = Boolean(body.questionAnswerNotifyGuardian);
  return body;
}

/** Diyalog açıklaması (yasal metinle aynı dil). */
export const RETENTION_DESCRIPTION = 'Koç yazışmaları, ekleri, sesli notlar ve “Hocaya sor” soru ve yanıtları, koçluk '
  + 'ilişkisinin (koç değişti ya da eşleme bitti) ya da öğrenimin (öğrenci pasif / mezun) bitişinden bu kadar yıl '
  + 'sonra her gece kendiliğinden silinir; ekler dosya deposundan da kaldırılır. Kayıt dondurma bitiş sayılmaz. Açık '
  + 'şikâyeti olan konuşma şikâyet karara bağlanana kadar silinmez. KVKK aydınlatma metni varsayılan 2 yılı söyler; '
  + 'süreyi değiştirirseniz metni de güncelleyin.';
