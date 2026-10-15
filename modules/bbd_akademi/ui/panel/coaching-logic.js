// BBD Akademi — Koçluk yönetimi: saf mantık (DOM'suz, import'suz; Node ile sınanır).
//
// Sözleşme draft.3 koçluk paketi (6bbcd0b / 2d91b96): F-K2 koç eşlemesi, F-K3
// koç profili, F-K5 koçluk kartı, F-K6 görev oluşturucu, F-K7 haftalık program,
// F-K8 şablonla toplu atama, F-K9/F-K10 video, F-K12/F-K13 kontrol kuyruğu,
// F-K16 check-in, F-K17 görüşmeler, F-K18 hedef önerisi, F-K19 tebrik/rozet,
// F-K21/F-K22 metrik ve raporlar, F-K23 kütüphane.
//
// Metinler tasarım panolarıyla (`docs/design/prototype/KontrolKuyrugu`,
// `HaftalikProgram`, `GorevAta`, `Kocum`, `VideoKutuphanesi`) ve mobil/web
// istemcileriyle aynı tutulur ("Koç görevi", "Öğrencinin kendi işi",
// "Kapasite aşıldı", "Tekrar iste", "Konuşalım" …).

/** `CoachQueueKind` → metin. */
export const QUEUE_KINDS = {
  review: 'Kontrol bekleyen teslim',
  question: 'Cevapsız soru',
  message: 'Okunmamış mesaj',
  postpone_request: 'Erteleme talebi',
  unpublished_week: 'Program yayımlanmadı',
  meeting: 'Bugünkü görüşme',
  warning: 'Yeni erken uyarı',
  checkin_missing: 'Check-in doldurulmadı',
};

/** Kontrol kuyruğu durumu (`admin_listCoachReviews` `status`) → [metin, ton]. */
export const REVIEW_STATUS = {
  pending: ['Kontrol bekliyor', 'warn'],
  approved: ['Onaylandı', 'good'],
  redo: ['Tekrar istendi', 'bad'],
  commented: ['Yorumlandı', 'info'],
  none: ['—', 'dim'],
};

/** `CoachingRisk` → [metin, ton]. */
export const RISK = {
  none: ['Risk yok', 'dim'],
  low: ['Düşük', 'info'],
  medium: ['Orta', 'warn'],
  high: ['Yüksek', 'bad'],
};

export const EXAM_TREND = { up: '▲', down: '▼', flat: '=' };

/** Koç görevi türleri (`PlanTaskType`, video dahil). */
export const TASK_TYPES = {
  question_solving: 'Soru çözümü',
  resource_section: 'Kaynak bölümü',
  topic_study: 'Konu çalışması',
  video: 'Video',
  review: 'Tekrar',
  mistake_review: 'Hata tekrarı',
  reading: 'Okuma',
  exam: 'Deneme',
  homework: 'Ödev',
  free: 'Serbest görev',
};

export const PRIORITY = { low: 'Düşük', normal: 'Normal', high: 'Yüksek' };
export const PROOF_PHOTO = { off: 'Fotoğraf istenmez', optional: 'Fotoğraf isteğe bağlı', required: 'Fotoğraf zorunlu' };

/** Hazır kısa tepkiler (`CoachReviewDecision.reaction`, en çok 40 karakter). */
export const REACTIONS = ['👏', '🌟', '💪', '🎉', '✅'];

/** `CoachWeekSaveResult.results[].warnings` → metin. */
export const SAVE_WARNINGS = {
  over_capacity: 'Kapasite aşıldı',
  range_exceeds_section: 'Aralık bölümü aşıyor',
  holiday: 'Tatil günü',
  not_on_shelf: 'Kaynak öğrencinin rafında değil',
};

/** `TemplateAssignPreview` çözülemeyen satır nedeni → metin. */
export const UNRESOLVED = {
  no_matching_resource: 'Rafta uygun kaynak yok',
  no_remaining_sections: 'Kaynakta kalan bölüm yok',
  no_weak_topic: 'Zayıf konu bulunamadı',
  no_due_review: 'Tekrar zamanı gelen konu yok',
};

export const COPY_STATUS = { copied: ['Kopyalandı', 'good'], would_copy: ['Kopyalanacak', 'info'],
  skipped: ['Atlandı', 'dim'] };

/** Toplu koç atama satırı (`CoachBulkAssignResult.rows[].action`). */
export const BULK_ACTIONS = { assign: ['Koç atanacak', 'good'], change: ['Koç değişecek', 'warn'],
  skip: ['Atlandı (koçu var)', 'dim'] };

/**
 * `GoalProposalKind` → [metin, birim]. Kurumun tek hedef önerme akışı (Akademi sözleşme README §14 madde 80):
 * günlük soru / günlük süre / deneme neti eski "kurum önerisi"nin (`admin_putGoalSuggestion`) alanlarıdır.
 */
export const GOAL_KINDS = {
  weekly_questions: ['Haftalık soru sayısı', 'soru'],
  weekly_minutes: ['Haftalık çalışma süresi', 'dk'],
  daily_questions: ['Günlük soru sayısı', 'soru'],
  daily_minutes: ['Günlük çalışma süresi', 'dk'],
  subject_net: ['Ders neti', 'net'],
  exam_net: ['Deneme neti', 'net'],
  resource_finish: ['Kaynağı bitirme', '%'],
  reading: ['Okuma', 'sayfa'],
};

/** `GoalExamType` (deneme neti hedefinin deneme türü). */
export const GOAL_EXAM_TYPES = { lgs: 'LGS', tyt: 'TYT', ayt_say: 'AYT SAY', ayt_ea: 'AYT EA', ayt_soz: 'AYT SÖZ',
  ayt_dil: 'AYT DİL' };

/** Hedef değerinin üst sınırı (Akademi `MAX_TARGET`). */
export const GOAL_MAX = { daily_questions: 2000, daily_minutes: 960, exam_net: 500 };

export const GOAL_UNITS = { questions: 'soru', minutes: 'dk', net: 'net', percent: '%', pages: 'sayfa' };

export const GOAL_STATUS = {
  proposed: ['Önerildi', 'info'],
  accepted: ['Kabul edildi', 'good'],
  discussing: ['Konuşalım', 'warn'],
  withdrawn: ['Geri çekildi', 'dim'],
  expired: ['Süresi doldu', 'dim'],
};

export const TERM_RESULT = { kept: ['Tuttu', 'good'], partial: ['Kısmen', 'warn'], missed: ['Tutmadı', 'bad'] };

export const MEETING_STATUS = {
  requested: ['Talep edildi', 'warn'],
  confirmed: ['Onaylandı', 'good'],
  declined: ['Reddedildi', 'dim'],
  cancelled: ['İptal', 'dim'],
  done: ['Yapıldı', 'good'],
  no_show: ['Gelinmedi', 'bad'],
};

export const MEETING_MODES = { in_person: 'Yüz yüze', online: 'Çevrimiçi', phone: 'Telefon' };
export const MEETING_PARTICIPANTS = { student: 'Öğrenci', guardians: 'Veli', student_and_guardians: 'Öğrenci ve veli' };
export const MEETING_DURATIONS = [15, 20, 30, 45, 60];
export const REQUESTED_BY = { student: 'Öğrenci', guardian: 'Veli', coach: 'Koç', admin: 'Yönetim' };

/** Karar → [düğme, hangi durumlarda]. */
export const MEETING_DECISIONS = {
  confirm: ['Onayla', ['requested']],
  decline: ['Reddet', ['requested']],
  reschedule: ['Ertele / yeni saat', ['requested', 'confirmed']],
  cancel: ['İptal et', ['requested', 'confirmed']],
  done: ['Yapıldı', ['confirmed']],
  no_show: ['Gelinmedi', ['confirmed']],
};

/** Görüşme notu görünürlüğü (`CoachNoteVisibility`; öğrenci kartındaki koç notlarıyla aynı). */
export const NOTE_VISIBILITY = { private: 'Özel (yalnız kurum)', student: 'Öğrenci görür', guardians: 'Veliler görür',
  all: 'Öğrenci ve veliler' };

/** `LibraryItemKind` → metin. */
export const LIBRARY_KINDS = {
  youtube_video: 'YouTube videosu',
  youtube_clip: 'YouTube kesiti',
  youtube_playlist: 'YouTube oynatma listesi',
  web_link: 'Web bağlantısı',
  file: 'Dosya',
  text_note: 'Metin notu',
  task_pack: 'Görev paketi',
};

export const LIBRARY_STATUS = {
  active: ['Etkin', 'good'],
  video_unavailable: ['Video kaldırıldı', 'bad'],
  video_private: ['Video özele alındı', 'bad'],
};

export const PROPOSAL_STATUS = {
  none: ['—', 'dim'], pending: ['Öneri bekliyor', 'warn'], approved: ['Onaylandı', 'good'],
  rejected: ['Reddedildi', 'dim'],
};

export const LIBRARY_SCOPES = { all: 'Hepsi', institution: 'Kurum kütüphanesi', mine: 'Kişisel' };

/** `VideoResolveResult.warnings` → metin. */
export const VIDEO_WARNINGS = {
  not_embeddable: 'Gömülemiyor — görev "yalnız YouTube\'da açılır"',
  age_restricted: 'Yaş sınırlı — yalnız YouTube\'da açılır',
  made_for_kids: 'Çocuklara yönelik — izlenme ölçülmez, yalnız tamamlandı bilgisi',
  unlisted: 'Liste dışı video',
  live: 'Canlı yayın — süre yok',
};

/**
 * Koç rozeti simgesi (`CoachBadgeInput.icon` — uygulama ikon seti anahtarı) →
 * [metin, önizleme]. Uygulama bilmediği anahtarda genel rozet simgesini çizer.
 */
export const BADGE_ICONS = {
  medal: ['Madalya', '🏅'], star: ['Yıldız', '⭐'], flame: ['Alev (seri)', '🔥'], target: ['Hedef', '🎯'],
  sparkle: ['Parıltı', '✨'], trophy: ['Kupa', '🏆'], book: ['Kitap', '📘'], check: ['Tamam', '✅'],
};

/** Rozet: "🏅 İstikrar". */
export function badgeText(badge) {
  if (!badge) return '—';
  const icon = BADGE_ICONS[badge.icon]?.[1] || '🏅';
  return `${icon} ${badge.name || ''}`.trim();
}

export const CHECKIN_KINDS = { scale: 'Ölçek (1–N)', short_text: 'Kısa yanıt', single_choice: 'Tek seçim' };
export const SUBMISSION_DIGEST = { instant: 'Her teslimde', hourly: 'Saatlik özet', off: 'Kapalı' };

/** Koçluk dışa aktarımı (`CoachingExportRequest.kind`) → [metin, biçimler]. */
export const EXPORT_KINDS = {
  coach_performance: ['Koç performansı', ['xlsx', 'pdf']],
  coaching_term_reports: ['Koçluk dönem raporları', ['zip', 'xlsx']],
  weekly_programs: ['Haftalık programlar', ['xlsx', 'zip']],
};
export const EXPORT_FORMATS = { xlsx: 'Excel (XLSX)', pdf: 'PDF', zip: 'ZIP (öğrenci başına PDF)' };

// ------------------------------------------------------------------ biçim

/** 'YYYY-MM-DD' + n gün. */
export function addDaysIso(iso, days) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return date.toISOString().slice(0, 10);
}

/** Haftanın pazartesisi. */
export function mondayOf(iso) {
  const [year, month, day] = String(iso).slice(0, 10).split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  const shift = (date.getUTCDay() + 6) % 7;
  return addDaysIso(iso, -shift);
}

/** '2026-10-12' → '12–18 Ekim' (hafta etiketi). */
const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım',
  'Aralık'];
export function weekLabel(weekStart) {
  const end = addDaysIso(weekStart, 6);
  const [, m1, d1] = weekStart.split('-').map(Number);
  const [, m2, d2] = end.split('-').map(Number);
  return m1 === m2 ? `${d1}–${d2} ${MONTHS[m1 - 1]}` : `${d1} ${MONTHS[m1 - 1]} – ${d2} ${MONTHS[m2 - 1]}`;
}

/** Dakika → '1 sa 30 dk' / '45 dk'. */
export function minutesText(value) {
  const total = Math.max(0, Math.round(Number(value) || 0));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  if (!hours) return `${minutes} dk`;
  return minutes ? `${hours} sa ${minutes} dk` : `${hours} sa`;
}

/** Saniye → 'dd:ss' ya da 'ss:dd:ss'. */
export function clockText(seconds) {
  if (seconds === null || seconds === undefined || seconds === '') return '—';
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** 'dd:ss' / 'ss:dd:ss' / saniye → saniye (geçersiz → null). */
export function parseClock(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  if (/^\d+$/.test(raw)) return Number(raw);
  const parts = raw.split(':').map((part) => part.trim());
  if (parts.some((part) => !/^\d+$/.test(part)) || parts.length > 3) return null;
  return parts.reduce((total, part) => total * 60 + Number(part), 0);
}

/** Yüzde (null → '—'). */
export function pct(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `%${Number(value).toLocaleString('tr-TR', { maximumFractionDigits: digits })}`;
}

/** Saat değeri (null → '—'). */
export function hoursText(value) {
  if (value === null || value === undefined) return '—';
  const number = Number(value);
  return number < 1 ? `${Math.round(number * 60)} dk` : `${number.toLocaleString('tr-TR', { maximumFractionDigits: 1 })} sa`;
}

/** Hedef önerisi: "Haftalık soru sayısı · 600 soru (Matematik)". */
export function goalText(goal) {
  if (!goal) return '—';
  const [label, fallbackUnit] = GOAL_KINDS[goal.kind] || ['Hedef', ''];
  const unit = GOAL_UNITS[goal.unit] || fallbackUnit;
  const target = goal.targetValue === null || goal.targetValue === undefined ? '' : ` · ${goal.targetValue} ${unit}`.trimEnd();
  const extra = goal.subject?.name || goal.resource?.title || GOAL_EXAM_TYPES[goal.examType];
  return `${label}${target}${extra ? ` (${extra})` : ''}`;
}

/** Görevin kısa ayrıntısı: kaynak · bölüm · sayfa · soru. */
export function taskDetail(task) {
  if (!task) return '';
  const parts = [];
  if (task.resource?.title) parts.push(task.resource.title);
  if (task.sections?.length) parts.push(task.sections.map((section) => section.title).join(', '));
  if (task.pageFrom) parts.push(task.pageTo && task.pageTo !== task.pageFrom ? `s. ${task.pageFrom}–${task.pageTo}` : `s. ${task.pageFrom}`);
  const coach = task.coachTask;
  if (coach?.questionFrom) {
    parts.push(coach.questionTo ? `soru ${coach.questionFrom}–${coach.questionTo}` : `soru ${coach.questionFrom}`);
  } else if (task.targetQuestions) {
    parts.push(`${task.targetQuestions} soru`);
  }
  if (task.topic?.name) parts.push(task.topic.name);
  if (coach?.videos?.length) parts.push(`${coach.videos.length} video`);
  return parts.join(' · ');
}

/** Koç görevi mi (öğrencinin kendi işi değil)? */
export function isCoachTask(task) {
  return task?.source === 'coach';
}

/** Taslak mı (yayımlanana dek öğrenci görmez)? */
export function isDraft(task) {
  return Boolean(task?.coachTask && task.coachTask.published === false);
}

/** Teslimin D/Y/B özeti. */
export function resultText(item) {
  if (!item) return '—';
  const parts = [];
  if (item.correct !== null && item.correct !== undefined) parts.push(`${item.correct} D`);
  if (item.wrong !== null && item.wrong !== undefined) parts.push(`${item.wrong} Y`);
  if (item.blank !== null && item.blank !== undefined) parts.push(`${item.blank} B`);
  return parts.join(' · ') || '—';
}

// ---------------------------------------------------------------- gövdeler

/**
 * Görev formu taslağı → `PlanAssignmentTaskInput` (koç alanlarıyla). Boş alan
 * gönderilmez; `draft` haftalık programda taslak (yayımlanana dek gizli).
 * Hata varsa `{error}` döner.
 */
export function taskInput(form, { draft = true } = {}) {
  const minutes = Number(form.estimatedMinutes);
  if (!form.date) return { error: 'Gün seçin.' };
  if (!form.type || !TASK_TYPES[form.type]) return { error: 'Görev türünü seçin.' };
  if (!Number.isInteger(minutes) || minutes < 5 || minutes > 600) return { error: 'Süre 5–600 dakika olmalı.' };
  const task = { date: form.date, type: form.type, estimatedMinutes: minutes };
  const title = String(form.title || '').trim();
  if (title) task.title = title.slice(0, 120);
  if (form.subjectId) task.subjectId = form.subjectId;
  if (form.topicId) task.topicId = form.topicId;
  if (form.resourceId) task.resourceId = form.resourceId;
  if (Array.isArray(form.sectionIds) && form.sectionIds.length) task.sectionIds = form.sectionIds.slice(0, 20);
  const pageFrom = intOrNull(form.pageFrom);
  const pageTo = intOrNull(form.pageTo);
  if (pageFrom) task.pageFrom = pageFrom;
  if (pageTo) {
    if (pageFrom && pageTo < pageFrom) return { error: 'Bitiş sayfası başlangıçtan küçük olamaz.' };
    task.pageTo = pageTo;
  }
  const target = intOrNull(form.targetQuestions);
  if (target) task.targetQuestions = target;
  const note = String(form.note || '').trim();
  if (note) task.note = note.slice(0, 500);
  const coach = { draft: Boolean(draft) };
  const instructions = String(form.instructions || '').trim();
  if (instructions) coach.instructions = instructions.slice(0, 2000);
  const questionFrom = intOrNull(form.questionFrom);
  const questionTo = intOrNull(form.questionTo);
  if (questionFrom) coach.questionFrom = questionFrom;
  if (questionTo) {
    if (questionFrom && questionTo < questionFrom) return { error: 'Son soru ilk sorudan küçük olamaz.' };
    coach.questionTo = questionTo;
  }
  if (form.priority && form.priority !== 'normal') coach.priority = form.priority;
  coach.proof = { photo: form.proofPhoto || 'off', answersRequired: form.answersRequired !== false };
  const accuracy = intOrNull(form.minAccuracyPercent);
  if (accuracy) {
    if (accuracy < 1 || accuracy > 100) return { error: 'Başarı ölçütü %1–100 olmalı.' };
    coach.successCriterion = { minAccuracyPercent: accuracy };
  }
  if (Array.isArray(form.videos) && form.videos.length) coach.videos = form.videos.slice(0, 10).map(videoInput);
  if (Array.isArray(form.attachmentFileIds) && form.attachmentFileIds.length) {
    coach.attachmentFileIds = form.attachmentFileIds.slice(0, 3);
  }
  if (form.addToShelf) coach.addToShelf = true;
  task.coach = coach;
  if (form.type === 'video' && !(coach.videos && coach.videos.length)) return { error: 'Video görevine en az bir video ekleyin.' };
  return { task };
}

function intOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isInteger(number) && number > 0 ? number : null;
}

/** Formdaki video (çözülmüş `VideoRef` + kesit) → `TaskVideoInput`. */
export function videoInput(video, index = 0) {
  const out = { order: video.order ?? index };
  if (video.libraryItemId) out.libraryItemId = video.libraryItemId;
  else out.videoId = video.videoId || video.video?.videoId || null;
  if (video.playlistId) out.playlistId = video.playlistId;
  if (video.startSeconds !== null && video.startSeconds !== undefined) out.startSeconds = video.startSeconds;
  if (video.endSeconds) out.endSeconds = video.endSeconds;
  if (video.chapters?.length) out.chapters = video.chapters.slice(0, 20);
  const checkpoints = cleanCheckpoints(video.checkpoints);
  if (checkpoints.length) out.checkpoints = checkpoints;
  if (video.requireOrder) out.requireOrder = true;
  return out;
}

/** Durak soruları → `VideoCheckpointInput[]` (soru metni 3 karakterden kısa olan düşer; en çok 3). */
export function cleanCheckpoints(list) {
  return (list || []).filter((item) => String(item?.prompt || '').trim().length >= 3).map((item) => {
    const out = { prompt: String(item.prompt).trim().slice(0, 300), kind: item.kind === 'short_text' ? 'short_text' : 'single_choice',
      atSeconds: item.atSeconds ?? null };
    if (out.kind === 'single_choice') {
      out.options = (item.options || []).map((text) => String(text).trim()).filter(Boolean).slice(0, 5);
      out.correctIndex = out.options.length ? Math.min(Number(item.correctIndex) || 0, out.options.length - 1) : null;
    }
    return out;
  }).slice(0, 3);
}

/** Var olan görev → form taslağı (düzenleme). */
export function taskForm(task) {
  const coach = task.coachTask || {};
  return {
    date: task.date,
    type: TASK_TYPES[task.type] ? task.type : 'free',
    estimatedMinutes: task.estimatedMinutes,
    title: task.title || '',
    subjectId: task.subject?.id || '',
    topicId: task.topic?.id || '',
    topicName: task.topic?.name || '',
    resourceId: task.resource?.id || '',
    resourceTitle: task.resource?.title || '',
    sectionIds: (task.sections || []).map((section) => section.id),
    pageFrom: task.pageFrom ?? '',
    pageTo: task.pageTo ?? '',
    targetQuestions: task.targetQuestions ?? '',
    note: task.note || '',
    instructions: coach.instructions || '',
    questionFrom: coach.questionFrom ?? '',
    questionTo: coach.questionTo ?? '',
    priority: coach.priority || 'normal',
    proofPhoto: coach.proof?.photo || 'off',
    answersRequired: coach.proof?.answersRequired !== false,
    minAccuracyPercent: coach.successCriterion?.minAccuracyPercent ?? '',
    videos: (coach.videos || []).map((video, index) => ({ videoId: video.video?.videoId, video: video.video,
      libraryItemId: video.libraryItemId, startSeconds: video.startSeconds, endSeconds: video.endSeconds,
      chapters: video.chapters || [], order: video.order ?? index, requireOrder: video.requireOrder,
      checkpoints: (video.checkpoints || []).map((item) => ({ atSeconds: item.atSeconds ?? null, prompt: item.prompt || '',
        kind: item.kind === 'short_text' ? 'short_text' : 'single_choice', options: item.options || [],
        correctIndex: item.correctIndex ?? 0 })) })),
    attachmentFileIds: (coach.attachments || []).map((file) => file.id),
  };
}

/**
 * Hafta değişiklikleri → `CoachWeekSave.ops`. `changes`: [{op, clientKey, taskId?, date?, order?, task?}].
 * Aynı görevin ardışık taşımaları tek `move`'a iner; silinen görevin önceki işlemleri düşer.
 */
export function weekOps(changes) {
  const out = [];
  const index = new Map();
  for (const change of changes || []) {
    const key = change.taskId || change.clientKey;
    if (change.op === 'delete' && change.taskId) {
      for (let i = out.length - 1; i >= 0; i -= 1) if (out[i].taskId === change.taskId) out.splice(i, 1);
      index.delete(key);
      out.push({ op: 'delete', clientKey: change.clientKey, taskId: change.taskId });
      continue;
    }
    if (change.op === 'move' && index.has(key) && out[index.get(key)].op === 'move') {
      Object.assign(out[index.get(key)], { date: change.date, order: change.order ?? null });
      continue;
    }
    const op = { op: change.op, clientKey: change.clientKey };
    if (change.taskId) op.taskId = change.taskId;
    if (change.date) op.date = change.date;
    if (change.order !== undefined && change.order !== null) op.order = change.order;
    if (change.task) op.task = change.task;
    index.set(key, out.length);
    out.push(op);
  }
  return out.slice(0, 200);
}

/** Kaydetme sonucu → {saved, errors[{clientKey, errorCode}], warnings[metin]}. */
export function saveSummary(result) {
  const rows = result?.results || [];
  const errors = rows.filter((row) => row.result === 'error').map((row) => ({ clientKey: row.clientKey,
    errorCode: row.errorCode || 'error' }));
  const warnings = [...new Set(rows.flatMap((row) => row.warnings || []))].map((key) => SAVE_WARNINGS[key] || key);
  return { saved: rows.length - errors.length, errors, warnings };
}

/** Kontrol kararı gövdesi (`CoachReviewDecision`). */
export function reviewBody({ clientUuid, decision, comment, reaction, redoDate }) {
  if (!['approve', 'redo', 'comment'].includes(decision)) return { error: 'Karar seçin.' };
  const body = { clientUuid, decision };
  const text = String(comment || '').trim();
  if (decision === 'redo') {
    if (text.length < 3) return { error: 'Tekrar isterken gerekçe yazın (öğrenci görür).' };
    if (!redoDate) return { error: 'Tekrar için yeni gün seçin.' };
    body.redoDate = redoDate;
  }
  if (decision === 'comment' && !text) return { error: 'Yorum yazın.' };
  if (text) body.comment = text.slice(0, 1000);
  if (reaction) body.reaction = String(reaction).slice(0, 40);
  return { body };
}

/** Toplu onay gövdesi: seçili görevler ya da süzgeç. */
export function bulkApproveBody({ taskIds = [], filter = null, reaction = '', comment = '' } = {}) {
  const body = {};
  if (taskIds.length) body.taskIds = taskIds.slice(0, 200);
  else if (filter) {
    const clean = {};
    for (const [key, value] of Object.entries(filter)) {
      if (value !== '' && value !== null && value !== undefined && value !== false) clean[key] = value;
    }
    body.filter = clean;
  } else return { error: 'Onaylanacak teslim seçin.' };
  if (reaction) body.reaction = reaction;
  const text = String(comment || '').trim();
  if (text) body.comment = text.slice(0, 500);
  return { body };
}

/** Toplu koç atama gövdesi. */
export function bulkAssignBody({ classGroupIds = [], studentGroupIds = [], studentIds = [], gradeLevels = [], coachId,
  startsOn = '', onExisting = 'skip', changeReason = '', handoverNote = '' }, { dryRun = true } = {}) {
  if (!coachId) return { error: 'Koç seçin.' };
  const target = {};
  if (classGroupIds.length) target.classGroupIds = classGroupIds;
  if (studentGroupIds.length) target.studentGroupIds = studentGroupIds;
  if (studentIds.length) target.studentIds = studentIds;
  if (gradeLevels.length) target.gradeLevels = gradeLevels.map((level) => (/^\d+$/.test(String(level)) ? Number(level) : level));
  if (!Object.keys(target).length) return { error: 'Sınıf, grup, seviye ya da öğrenci seçin.' };
  const body = { target, coachId, onExisting, dryRun: Boolean(dryRun) };
  if (startsOn) body.startsOn = startsOn;
  const reason = String(changeReason || '').trim();
  if (onExisting === 'change') {
    if (!reason) return { error: 'Koçu değişecek öğrenciler için değişiklik gerekçesi yazın.' };
    body.changeReason = reason.slice(0, 300);
  }
  const note = String(handoverNote || '').trim();
  if (note) body.handoverNote = note.slice(0, 2000);
  return { body };
}

/** Hedef önerisi gövdesi (`GoalProposalCreate`). */
export function goalBody(form) {
  if (!GOAL_KINDS[form.kind]) return { error: 'Hedef türünü seçin.' };
  const value = Number(form.targetValue);
  if (form.targetValue === '' || form.targetValue === null || Number.isNaN(value) || value < 0) {
    return { error: 'Hedef değeri yazın.' };
  }
  if (form.kind === 'subject_net' && !form.subjectId) return { error: 'Ders seçin.' };
  if (form.kind === 'resource_finish' && !form.resourceId) return { error: 'Kaynak seçin.' };
  if (form.kind === 'exam_net' && !GOAL_EXAM_TYPES[form.examType]) return { error: 'Deneme türünü seçin.' };
  const max = GOAL_MAX[form.kind];
  if (max !== undefined && value > max) return { error: `Hedef değer en çok ${max} olabilir.` };
  const body = { kind: form.kind, targetValue: value };
  if (form.subjectId && form.kind === 'subject_net') body.subjectId = form.subjectId;
  if (form.resourceId && form.kind === 'resource_finish') body.resourceId = form.resourceId;
  if (form.kind === 'exam_net') body.examType = form.examType;
  if (form.dueDate) body.dueDate = form.dueDate;
  const note = String(form.note || '').trim();
  if (note) body.note = note.slice(0, 500);
  return { body };
}

/** Check-in sorusu taslağı → `CheckinQuestion` (geçersizse hata metni). */
export function checkinQuestion(form) {
  const prompt = String(form.prompt || '').trim();
  if (prompt.length < 3) return { error: 'Soru en az 3 karakter olmalı.' };
  if (!CHECKIN_KINDS[form.kind]) return { error: 'Soru türünü seçin.' };
  const question = { kind: form.kind, prompt: prompt.slice(0, 200) };
  if (form.id) question.id = form.id;
  if (form.kind === 'scale') {
    const max = Number(form.scaleMax || 5);
    if (!Number.isInteger(max) || max < 3 || max > 10) return { error: 'Ölçek üst değeri 3–10 olmalı.' };
    question.scaleMax = max;
  }
  if (form.kind === 'single_choice') {
    const options = (Array.isArray(form.options) ? form.options : String(form.options || '').split('\n'))
      .map((item) => String(item).trim()).filter(Boolean);
    if (options.length < 2) return { error: 'Tek seçimli soruda en az iki seçenek yazın.' };
    if (options.length > 5) return { error: 'En çok beş seçenek olabilir.' };
    question.options = options.map((item) => item.slice(0, 80));
  }
  return { question };
}

/** Görüşme kararı gövdesi. */
export function meetingDecisionBody(decision, form = {}) {
  if (!MEETING_DECISIONS[decision]) return { error: 'Karar seçin.' };
  const body = { decision };
  const reason = String(form.reason || '').trim();
  if (reason) body.reason = reason.slice(0, 300);
  if (decision === 'decline' && !reason) return { error: 'Reddetme gerekçesi yazın (talep edene gider).' };
  if (decision === 'reschedule') {
    if (!form.startsAt) return { error: 'Yeni tarih ve saati seçin.' };
    body.startsAt = form.startsAt;
  }
  if (form.room) body.room = String(form.room).slice(0, 40);
  if (form.onlineUrl) body.onlineUrl = form.onlineUrl;
  return { body };
}

/** Koç profili GET → PUT gövdesi (`CoachProfilePut`). */
export function profilePut(profile, form) {
  const start = form.messageStart ?? profile.messageHours?.start ?? '';
  const end = form.messageEnd ?? profile.messageHours?.end ?? '';
  const body = {
    subjectIds: form.subjectIds ?? (profile.subjects || []).map((subject) => subject.id),
    bio: String(form.bio ?? profile.bio ?? '').trim() || null,
    messageHours: start && end ? { start, end } : null,
    submissionDigest: form.submissionDigest || profile.submissionDigest || 'instant',
    meetingAutoConfirm: Boolean(form.meetingAutoConfirm ?? profile.meetingAutoConfirm),
  };
  const name = String(form.displayName ?? '').trim();
  if (name && name !== profile.displayName) body.displayName = name.slice(0, 80);
  if (body.subjectIds.length > 10) return { error: 'En çok 10 branş seçilebilir.' };
  if (start && end && start >= end) return { error: 'Mesaj saatleri: bitiş başlangıçtan sonra olmalı.' };
  return { body };
}

/** Koçluk dışa aktarım gövdesi. */
export function exportBody(form) {
  const spec = EXPORT_KINDS[form.kind];
  if (!spec) return { error: 'Dışa aktarım türünü seçin.' };
  if (!spec[1].includes(form.format)) return { error: 'Bu tür için biçim seçin.' };
  if (!form.from || !form.to) return { error: 'Tarih aralığı seçin.' };
  if (form.to < form.from) return { error: 'Bitiş tarihi başlangıçtan önce olamaz.' };
  const body = { kind: form.kind, format: form.format, from: form.from, to: form.to };
  if (form.termId) body.termId = form.termId;
  if (form.coachIds?.length) body.coachIds = form.coachIds.slice(0, 100);
  if (form.studentIds?.length) body.studentIds = form.studentIds.slice(0, 2000);
  return { body };
}

/** Kütüphane öğesi formu → `LibraryItemInput`. */
export function libraryInput(form) {
  if (!LIBRARY_KINDS[form.kind]) return { error: 'Öğe türünü seçin.' };
  const title = String(form.title || '').trim();
  if (title.length < 2) return { error: 'Başlık en az 2 karakter olmalı.' };
  const body = { kind: form.kind, title: title.slice(0, 120) };
  if (form.kind.startsWith('youtube_')) {
    if (form.kind === 'youtube_playlist') {
      if (!form.playlistId) return { error: 'Oynatma listesi bağlantısını çözün.' };
      body.playlistId = form.playlistId;
    } else {
      if (!form.videoId) return { error: 'YouTube bağlantısını çözün.' };
      body.videoId = form.videoId;
    }
    if (form.kind === 'youtube_clip') {
      const start = parseClock(form.start);
      const end = parseClock(form.end);
      if (start === null || end === null || end <= start) return { error: 'Kesit için başlangıç ve bitiş yazın.' };
      body.startSeconds = start;
      body.endSeconds = end;
    }
  }
  if (form.kind === 'web_link') {
    const url = String(form.url || '').trim();
    if (!/^https?:\/\/\S+$/i.test(url)) return { error: 'Geçerli bir bağlantı yazın (https://…).' };
    body.url = url.slice(0, 2048);
  }
  if (form.kind === 'file') {
    if (!form.fileId) return { error: 'Dosya yükleyin.' };
    body.fileId = form.fileId;
  }
  if (form.kind === 'text_note') {
    const text = String(form.text || '').trim();
    if (!text) return { error: 'Not metnini yazın.' };
    body.text = text.slice(0, 4000);
  }
  const tags = String(form.tags || '').split(',').map((tag) => tag.trim()).filter(Boolean);
  if (tags.length) body.tags = tags.slice(0, 10).map((tag) => tag.slice(0, 30));
  if (form.subjectId) body.subjectId = form.subjectId;
  if (Array.isArray(form.topicIds) && form.topicIds.length) body.topicIds = form.topicIds.slice(0, 20);
  if (Array.isArray(form.exams) && form.exams.length) body.exams = form.exams;
  if (Array.isArray(form.gradeLevels) && form.gradeLevels.length) {
    body.gradeLevels = form.gradeLevels.map((level) => (/^\d+$/.test(String(level)) ? Number(level) : level));
  }
  if (form.kind === 'task_pack' && Array.isArray(form.tasks)) body.tasks = form.tasks.slice(0, 30);
  return { body };
}

/** YouTube bağlantısından oynatma listesi kimliği (yoksa null). */
export function playlistIdOf(url) {
  const match = String(url || '').match(/[?&]list=([A-Za-z0-9_-]{10,64})/);
  return match ? match[1] : null;
}
