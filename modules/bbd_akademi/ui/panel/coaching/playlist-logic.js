// BBD Akademi — YouTube oynatma listesi: saf mantık (DOM'suz, import'suz; Node ile sınanır).
//
// Akademi README §14 madde 124 (iş 68-video-liste): liste bağlantısı → önizleme → "Liste olarak ekle" /
// "Videoları ayrı ekle"; liste ayrıntısı + "Listeyi yenile"; atama (Tüm liste · Seçili videolar · Güne yay ·
// Sırayla izle), yinelenen atama uyarısı, liste ilerlemesi. Metinler sözleşme / web / mobil ile aynı.

/** `PlaylistVideoStatus` → [metin, ton]. */
export const PLAYLIST_STATUS = {
  playable: ['Oynatılabilir', 'good'],
  not_embeddable: ['Yalnız YouTube\'da açılır', 'warn'],
  private: ['Özel video', 'bad'],
  deleted: ['Silinmiş video', 'bad'],
};

/** Atanabilir durumlar (özel / silinmiş atanamaz). */
export const ASSIGNABLE = new Set(['playable', 'not_embeddable']);

/** `PlaylistAssignmentMode` → metin. */
export const ASSIGN_MODES = { all: 'Tüm liste', selected: 'Seçili videolar', spread: 'Güne yay' };

/** Kiplerin kısa açıklaması (atama penceresi). */
export const ASSIGN_MODE_HINTS = {
  all: 'Listedeki bütün izlenebilir videolar tek görevde; her videonun ilerlemesi ayrı.',
  selected: 'Onay kutusuyla ya da aralıkla ("3–7") seçtiğiniz videolar tek görevde.',
  spread: 'Seçilen videolar plan günlerine dağıtılır; her gün ayrı görev.',
};

/** Güne yay ölçüsü. */
export const SPREAD_KINDS = { perDay: 'Her gün N video', minutesPerDay: 'Her gün ~X dk' };

/** `PlaylistAssignmentPreview.warnings` → metin. */
export const ASSIGN_WARNINGS = {
  exceeds_due_date: 'Dağıtım son tarihi aşıyor — son tarihi uzatın ya da günlük miktarı artırın.',
  has_not_embeddable: 'Bazı videolar yalnız YouTube\'da açılır (gömme kapalı).',
  truncated_playlist: 'Liste 200 videodan uzun; ilk 200 video alındı.',
  unknown_durations: 'Bazı videoların süresi bilinmiyor; dağıtımda 10 dk sayıldı.',
  all_duplicates: 'Seçili videoların hepsi bu öğrencilere zaten atanmış.',
};

/** Atlanan video / içe aktarma atlaması → metin. */
export const SKIP_REASONS = {
  private: 'Özel video',
  deleted: 'Silinmiş video',
  removed: 'YouTube\'dan kaldırıldı',
  not_in_playlist: 'Listede yok',
  duplicate: 'Zaten ekli',
};

/** Anahtarsız durumda gösterilen cümle (sunucu metniyle aynı). */
export const KEY_REQUIRED_TEXT = 'Oynatma listesi için YouTube API anahtarı gerekli (yönetici KM\'den girer) — şimdilik '
  + 'listedeki videoları tek tek ekleyebilirsin';

/** En çok saklanan video (sözleşme `VideoPlaylistDetail.videos` ≤ 200). */
export const MAX_VIDEOS = 200;

// ------------------------------------------------------------------ bağlantı

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;
const LIST_ID = /^[A-Za-z0-9_-]{2,64}$/;

/**
 * YouTube bağlantısı → {playlistId, videoId}. `list=` liste, `v=` / youtu.be / shorts / embed / live videodur.
 * Çıplak liste kimliği (PL…, UU…, OL…) de liste sayılır. Hiçbiri yoksa ikisi de null.
 */
export function parseYoutubeLink(text) {
  const raw = String(text || '').trim();
  const out = { playlistId: null, videoId: null };
  if (!raw) return out;
  if (!/[/?.=]/.test(raw)) {
    if (/^(PL|UU|OL|FL|RD|LL)[A-Za-z0-9_-]{8,62}$/.test(raw)) out.playlistId = raw;
    else if (VIDEO_ID.test(raw)) out.videoId = raw;
    return out;
  }
  const list = raw.match(/[?&#]list=([A-Za-z0-9_-]{2,64})/);
  if (list) out.playlistId = list[1];
  const v = raw.match(/[?&#]v=([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])/);
  const short = raw.match(/(?:youtu\.be\/|\/shorts\/|\/embed\/|\/live\/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])/);
  out.videoId = v?.[1] || short?.[1] || null;
  return out;
}

/**
 * Tek video bağlantısı: listeye ait parametreler (`list`, `index`, `pp`) atılır, başlangıç (`t`) kalır. Bağlantı
 * çözülemezse ve video kimliği biliniyorsa kanonik `watch?v=` döner.
 */
export function singleVideoUrl(text, videoId = null) {
  const raw = String(text || '').trim();
  try {
    const url = new URL(/^https?:\/\//i.test(raw) ? raw : `https://${raw}`);
    if (!/youtu\.?be/i.test(url.hostname)) throw new Error('youtube değil');
    for (const key of ['list', 'index', 'pp', 'start_radio']) url.searchParams.delete(key);
    if (!parseYoutubeLink(url.toString()).videoId) {
      return videoId ? `https://www.youtube.com/watch?v=${videoId}` : raw;
    }
    return url.toString();
  } catch {
    return videoId ? `https://www.youtube.com/watch?v=${videoId}` : raw;
  }
}

/** Bağlantıda oynatma listesi var mı. */
export function isPlaylistLink(text) {
  return Boolean(parseYoutubeLink(text).playlistId);
}

/** Önizleme gövdesi (`VideoPlaylistPreviewRequest`): bağlantı ya da çıplak kimlik. */
export function previewBody(text) {
  const raw = String(text || '').trim();
  if (!raw) return { error: 'Oynatma listesi bağlantısını yapıştırın.' };
  const { playlistId } = parseYoutubeLink(raw);
  if (!playlistId) return { error: 'Bağlantıda oynatma listesi (list=…) yok.' };
  if (!/[/?.=]/.test(raw) && LIST_ID.test(raw)) return { body: { playlistId: raw } };
  return { body: { url: raw.slice(0, 2048) } };
}

// ------------------------------------------------------------------ biçim

/** Saniye → '1 sa 40 dk' / '45 dk' / '< 1 dk'. */
export function durationText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total > 0 && total < 60) return '< 1 dk';
  const minutes = Math.round(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} dk`;
  return rest ? `${hours} sa ${rest} dk` : `${hours} sa`;
}

/** Saniye → 'dd:ss' / 's:dd:ss' (video satırı). */
export function clock(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = String(total % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}

/** Liste özeti: "12 video · 1 sa 40 dk" (+ " · 2 videonun süresi bilinmiyor"). */
export function summaryLine(playlist) {
  if (!playlist) return '';
  const count = playlist.playableCount ?? playlist.videoCount ?? 0;
  const parts = [`${count} video`];
  if (playlist.totalSeconds) parts.push(durationText(playlist.totalSeconds));
  if (playlist.unknownDurationCount) parts.push(`${playlist.unknownDurationCount} videonun süresi bilinmiyor`);
  return parts.join(' · ');
}

/** Durum sayıları: "2 gömülemeyen · 1 özel · 1 silinmiş" (sıfırlar yazılmaz; hepsi oynuyorsa boş). */
export function countsText(counts) {
  if (!counts) return '';
  return [
    counts.notEmbeddable ? `${counts.notEmbeddable} gömülemeyen` : '',
    counts.private ? `${counts.private} özel` : '',
    counts.deleted ? `${counts.deleted} silinmiş` : '',
  ].filter(Boolean).join(' · ');
}

/** Listedeki videolardan durum sayıları (önizlemede `counts` yoksa). */
export function countsOf(videos) {
  const out = { playable: 0, notEmbeddable: 0, private: 0, deleted: 0 };
  for (const video of videos || []) {
    if (video.status === 'not_embeddable') out.notEmbeddable += 1;
    else if (video.status === 'private') out.private += 1;
    else if (video.status === 'deleted') out.deleted += 1;
    else out.playable += 1;
  }
  return out;
}

/** Fark metni: sunucunun `message`ı; yoksa sayılardan ("3 yeni video, 1 kaldırıldı" / "Değişiklik yok"). */
export function diffText(result) {
  if (result?.message) return result.message;
  const added = result?.addedVideoIds?.length ?? result?.addedCount ?? 0;
  const removed = result?.removedVideoIds?.length ?? result?.removedCount ?? 0;
  if (!added && !removed) return 'Değişiklik yok';
  return [added ? `${added} yeni video` : '', removed ? `${removed} kaldırıldı` : ''].filter(Boolean).join(', ');
}

/** İlerleme: "4/12 izlendi · 1 sa 40 dk kaldı". */
export function progressText(done, total, remainingSeconds) {
  const parts = [`${done || 0}/${total || 0} izlendi`];
  if (remainingSeconds) parts.push(`${durationText(remainingSeconds)} kaldı`);
  else if (total && done >= total) parts.push('tamamlandı');
  return parts.join(' · ');
}

/** Öğrencinin liste kartı satırı: "Türev — 12 video · 4/12 izlendi · 1 sa 40 dk kaldı". */
export function cardLine(view) {
  if (!view) return '';
  return `${view.title} — ${view.videoCount} video · ${progressText(view.completedCount, view.videoCount,
    view.remainingSeconds)}`;
}

// ------------------------------------------------------------------ seçim

/** Atanabilir videolar (liste sırasıyla). */
export function assignableVideos(videos) {
  return (videos || []).filter((video) => ASSIGNABLE.has(video.status));
}

/**
 * Aralık metni → {from, to} | {error}. "3–7", "3-7", "3 - 7", "3..7", tek sayı "5". `max` listedeki son sıra.
 * Boş metin → {from: null, to: null}.
 */
export function parseRange(text, max = MAX_VIDEOS) {
  const raw = String(text || '').trim();
  if (!raw) return { from: null, to: null };
  const match = raw.match(/^(\d{1,3})\s*(?:[-–—]|\.\.)\s*(\d{1,3})$/) || raw.match(/^(\d{1,3})$/);
  if (!match) return { error: 'Aralığı "3–7" biçiminde yazın.' };
  const from = Number(match[1]);
  const to = Number(match[2] ?? match[1]);
  if (from < 1 || to < 1) return { error: 'Sıra 1\'den başlar.' };
  if (from > to) return { error: 'Aralığın başı sonundan büyük olamaz.' };
  if (to > max) return { error: `Listede ${max} video var.` };
  return { from, to };
}

/** Aralıktaki atanabilir videoların kimlikleri. */
export function rangeIds(videos, from, to) {
  if (!from || !to) return [];
  return assignableVideos(videos).filter((video) => video.position >= from && video.position <= to)
    .map((video) => video.videoId);
}

/**
 * Seçimin özeti: seçili kimlikler ∪ aralık (yalnız atanabilir videolar, liste sırasıyla).
 * Dönüş {ids, count, seconds, unknown} — `unknown` süresi bilinmeyen video sayısı.
 */
export function selectionOf(videos, picked = [], range = null) {
  const chosen = new Set(picked);
  if (range?.from && range?.to) for (const id of rangeIds(videos, range.from, range.to)) chosen.add(id);
  const ids = [];
  let seconds = 0;
  let unknown = 0;
  for (const video of assignableVideos(videos)) {
    if (!chosen.has(video.videoId)) continue;
    ids.push(video.videoId);
    const duration = video.video?.durationSeconds;
    if (duration) seconds += duration; else unknown += 1;
  }
  return { ids, count: ids.length, seconds, unknown };
}

/** Seçim satırı: "5 video seçildi · 48 dk" (+ " · 1 videonun süresi bilinmiyor"). */
export function selectionText(selection) {
  if (!selection?.count) return 'Video seçilmedi';
  const parts = [`${selection.count} video seçildi`];
  if (selection.seconds) parts.push(durationText(selection.seconds));
  if (selection.unknown) parts.push(`${selection.unknown} videonun süresi bilinmiyor`);
  return parts.join(' · ');
}

// ------------------------------------------------------------------ gövdeler

const tagList = (text) => [...new Set(String(text || '').split(',').map((tag) => tag.trim().slice(0, 30))
  .filter(Boolean))].slice(0, 10);

const gradeList = (list) => (list || []).map((level) => (level === 'graduate' ? 'graduate' : Number(level)))
  .filter((level) => level === 'graduate' || (level >= 5 && level <= 12));

/**
 * İçe aktarma gövdesi (`PlaylistImportRequest`). `form`: playlistId, mode (`playlist` | `videos`), videoIds
 * (videos kipinde seçilenler; boş = hepsi), title, description, subjectId, topicIds, tags (virgüllü metin ya da
 * dizi), gradeLevels, exams (yalnız kütüphane), overrides ({videoId: {title, subjectId, topicIds, tags}}).
 * `target`: 'library' | 'pool' (havuzda not ≤ 500, sınav yok).
 */
export function importBody(form, target = 'library') {
  if (!form?.playlistId) return { error: 'Önce listeyi önizleyin.' };
  if (!['playlist', 'videos'].includes(form.mode)) return { error: 'Ekleme biçimini seçin.' };
  const body = { playlistId: form.playlistId, mode: form.mode };
  if (form.mode === 'videos') body.videoIds = [...new Set(form.videoIds || [])].slice(0, MAX_VIDEOS);
  const title = String(form.title || '').trim();
  if (form.mode === 'playlist' && title) {
    if (title.length < 2) return { error: 'Başlık en az 2 karakter olmalı.' };
    body.title = title.slice(0, 120);
  }
  const description = String(form.description || '').trim();
  if (description) body.description = description.slice(0, target === 'pool' ? 500 : 1000);
  if (form.subjectId) body.subjectId = form.subjectId;
  const topicIds = (form.topicIds || []).filter(Boolean);
  if (topicIds.length) body.topicIds = topicIds.slice(0, 20);
  const tags = Array.isArray(form.tags) ? tagList(form.tags.join(',')) : tagList(form.tags);
  if (tags.length) body.tags = tags;
  const grades = gradeList(form.gradeLevels);
  if (grades.length) body.gradeLevels = grades;
  if (target === 'library' && Array.isArray(form.exams) && form.exams.length) body.exams = form.exams;
  const overrides = [];
  for (const [videoId, value] of Object.entries(form.overrides || {})) {
    if (!VIDEO_ID.test(videoId) || !value) continue;
    if (form.mode === 'videos' && body.videoIds.length && !body.videoIds.includes(videoId)) continue;
    const row = { videoId };
    const own = String(value.title || '').trim();
    if (form.mode === 'videos' && own.length >= 2) row.title = own.slice(0, 120);
    if (value.subjectId) row.subjectId = value.subjectId;
    if (value.topicIds?.length) row.topicIds = value.topicIds.slice(0, 20);
    const ownTags = Array.isArray(value.tags) ? tagList(value.tags.join(',')) : tagList(value.tags);
    if (ownTags.length) row.tags = ownTags;
    if (Object.keys(row).length > 1) overrides.push(row);
  }
  if (overrides.length) body.videoOverrides = overrides.slice(0, MAX_VIDEOS);
  return { body };
}

/** İçe aktarma sonucu → toast metni. */
export function importSummary(result) {
  const added = result?.addedCount ?? (result?.items || []).length;
  const skipped = result?.skipped || [];
  if (result?.mode === 'playlist') {
    return added ? 'Liste eklendi.' : 'Liste eklenemedi.';
  }
  const parts = [`${added} video eklendi`];
  const duplicate = skipped.filter((row) => row.reason === 'duplicate').length;
  const other = skipped.length - duplicate;
  if (duplicate) parts.push(`${duplicate} zaten vardı`);
  if (other) parts.push(`${other} atlandı (özel / silinmiş)`);
  return parts.join(' · ');
}

/** Hafta günleri (`DayOfWeek`) — "her gün" varsayılanı. */
export const DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Güne yay ayarı → {spread} | {error} (`PlaylistSpread`): `spreadKind` 'perDay' ("Her gün N video", 1–20) ya da
 * 'minutesPerDay' ("Her gün ~X dk", 5–600); `daysOfWeek` en az bir gün; `skipHolidays` varsayılan açık.
 */
export function spreadOf(state) {
  const days = (state?.daysOfWeek || []).filter((day) => DAYS.includes(day));
  if (!days.length) return { error: 'Görev verilecek en az bir gün seçin.' };
  const spread = { perDay: null, minutesPerDay: null, daysOfWeek: DAYS.filter((day) => days.includes(day)),
    skipHolidays: state.skipHolidays !== false };
  if (state.spreadKind === 'minutesPerDay') {
    const minutes = Number(String(state.minutesPerDay ?? '').trim());
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 600) return { error: 'Günlük süre 5–600 dk olmalı.' };
    spread.minutesPerDay = minutes;
  } else {
    const count = Number(String(state.perDay ?? '').trim());
    if (!Number.isInteger(count) || count < 1 || count > 20) return { error: 'Günlük video sayısı 1–20 olmalı.' };
    spread.perDay = count;
  }
  return { spread };
}

/**
 * Atama gövdesi (`PlaylistAssignmentRequest`) → {body} | {error}.
 * `state`: source {libraryItemId | videoPoolItemId | playlistId}, target (HomeworkTarget), mode,
 * picked (seçili kimlikler), range {from, to}, startDate, dueDate, spreadKind ('perDay' | 'minutesPerDay'),
 * perDay, minutesPerDay, daysOfWeek, skipHolidays, requireOrder, title, instructions, skipDuplicates, notify.
 */
export function assignBody(state) {
  const source = state?.source || {};
  const body = {};
  if (source.libraryItemId) body.libraryItemId = source.libraryItemId;
  else if (source.videoPoolItemId) body.videoPoolItemId = source.videoPoolItemId;
  else if (source.playlistId) body.playlistId = source.playlistId;
  else return { error: 'Atanacak listeyi seçin.' };
  const target = state.target || {};
  const targetCount = (target.classGroupIds || []).length + (target.studentGroupIds || []).length
    + (target.studentIds || []).length;
  if (!targetCount) return { error: 'En az bir sınıf, çalışma grubu ya da öğrenci seçin.' };
  body.target = {
    classGroupIds: target.classGroupIds || [],
    studentGroupIds: target.studentGroupIds || [],
    studentIds: target.studentIds || [],
  };
  if (!ASSIGN_MODES[state.mode]) return { error: 'Atama biçimini seçin.' };
  body.mode = state.mode;
  if (!ISO_DATE.test(String(state.startDate || ''))) return { error: 'Başlangıç gününü seçin.' };
  body.startDate = state.startDate;
  if (state.dueDate) {
    if (!ISO_DATE.test(state.dueDate)) return { error: 'Son tarih geçersiz.' };
    if (state.dueDate < state.startDate) return { error: 'Son tarih başlangıç gününden önce olamaz.' };
    body.dueDate = state.dueDate;
  } else {
    body.dueDate = null;
  }
  const picked = [...new Set(state.picked || [])];
  const range = state.range || {};
  if (state.mode !== 'all') {
    if (picked.length) body.videoIds = picked.slice(0, MAX_VIDEOS);
    if (range.from && range.to) {
      if (range.from > range.to) return { error: 'Aralığın başı sonundan büyük olamaz.' };
      body.rangeFrom = range.from;
      body.rangeTo = range.to;
    }
    // "Seçili videolar" ve "Güne yay" seçimle çalışır (pencere başta bütün izlenebilir videoları seçer).
    if (!body.videoIds && !body.rangeFrom) {
      return { error: 'En az bir video seçin (onay kutusu ya da "3–7" aralığı).' };
    }
  }
  if (state.mode === 'spread') {
    const spread = spreadOf(state);
    if (spread.error) return { error: spread.error };
    body.spread = spread.spread;
  } else {
    body.spread = null;
  }
  body.requireOrder = Boolean(state.requireOrder);
  const title = String(state.title || '').trim();
  if (title) {
    if (title.length < 2) return { error: 'Başlık en az 2 karakter olmalı.' };
    body.title = title.slice(0, 120);
  }
  const instructions = String(state.instructions || '').trim();
  if (instructions) body.instructions = instructions.slice(0, 2000);
  body.skipDuplicates = state.skipDuplicates !== false;
  body.notify = state.notify !== false;
  return { body };
}

// ------------------------------------------------------------------ önizleme

const MONTHS = ['Oca', 'Şub', 'Mar', 'Nis', 'May', 'Haz', 'Tem', 'Ağu', 'Eyl', 'Eki', 'Kas', 'Ara'];

/** '2026-10-21' → '21 Eki'. */
export function shortDate(iso) {
  const [, month, day] = String(iso || '').slice(0, 10).split('-').map(Number);
  if (!month || !day) return '';
  return `${day} ${MONTHS[month - 1]}`;
}

/** Gün aralığı: "21 Eki" ya da "21 Eki – 25 Eki". */
export function dateSpan(first, last) {
  if (!first) return '';
  return !last || first === last ? shortDate(first) : `${shortDate(first)} – ${shortDate(last)}`;
}

/** Önizleme başlığı: "24 öğrenci · 8 video · 1 sa 20 dk · 5 gün (21 Eki – 25 Eki)". */
export function previewHeadline(preview) {
  if (!preview) return '';
  const parts = [`${preview.studentCount} öğrenci`, `${preview.videoCount} video`];
  if (preview.totalSeconds) parts.push(durationText(preview.totalSeconds));
  const span = dateSpan(preview.firstDate, preview.lastDate);
  parts.push(preview.taskCount > 1 ? `${preview.taskCount} gün (${span})` : span);
  return parts.filter(Boolean).join(' · ');
}

/** Gün satırı: "21 Eki · 3 video · 32 dk". */
export function dayLine(day) {
  return [shortDate(day.date), `${(day.videoIds || []).length} video`, day.seconds ? durationText(day.seconds) : '']
    .filter(Boolean).join(' · ');
}

/** Yinelenen özeti: "3 öğrencide 5 video zaten atanmış" (yoksa boş). */
export function duplicateText(preview) {
  if (!preview?.duplicateStudentCount) return '';
  return `${preview.duplicateStudentCount} öğrencide ${preview.duplicateVideoCount} video zaten atanmış`;
}

/** Atlanan videolar: "2 video atlanacak (1 özel, 1 YouTube'dan kaldırıldı)". */
export function skippedText(list) {
  if (!list?.length) return '';
  const by = new Map();
  for (const row of list) by.set(row.reason, (by.get(row.reason) || 0) + 1);
  const parts = [...by].map(([reason, count]) => `${count} ${(SKIP_REASONS[reason] || reason).toLocaleLowerCase('tr-TR')}`);
  return `${list.length} video atlanacak (${parts.join(', ')})`;
}

/** Atama satırı (liste): "Tüm liste · 12 video · 24 öğrenci · 21 Eki – 25 Eki · Sırayla". */
export function assignmentLine(row) {
  if (!row) return '';
  return [ASSIGN_MODES[row.mode] || row.mode, `${row.videoCount} video`, `${row.studentCount} öğrenci`,
    dateSpan(row.firstDate, row.lastDate), row.requireOrder ? 'Sırayla' : ''].filter(Boolean).join(' · ');
}

/** Yüzde (0–100, null → '—'). */
export function percentText(value) {
  if (value === null || value === undefined) return '—';
  return `%${Math.round(Number(value))}`;
}
