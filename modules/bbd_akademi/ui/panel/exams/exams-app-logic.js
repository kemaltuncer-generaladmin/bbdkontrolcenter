// BBD Akademi — uygulamada deneme (optik form), canlı sınav ve yayınevi kazanım tablosu eşleştirmesinin
// SAF mantığı (Akademi README §14 madde 120; F-E9, F-E10, F-E11). Node ile sınanır.
//
// Kullanıcı (16.10.2026): "çocuk denemesini tıklayacak ve optik form açılacak … biz de cevap anahtarı ve konu
// analiz excelini yükleyeceğiz … yayınevlerine göre kazanım isimlendirmesi değişiyor. otomatik eşleştirme
// yapmalı … ayrıca uzaktan ek süre verme vs".
//
// Kurallar SUNUCUDADIR (süre, pencere, eşleştirme, puanlama). Burada yalnız: form taslağı ↔ istek gövdesi,
// doğrulama metinleri, canlı ekranda yerel geri sayım (sunucu saati farkıyla), süzgeç / seçim özeti, eşleştirme
// tablosunun karar durumu ve uygula gövdesi. Etiketler web (React) ve Flutter ile AYNI metindir.
//
// Bu dosya bilerek İMPORT'SUZ ve DOM'suzdur.

// ------------------------------------------------------------------ etiketler

export const DELIVERY = { paper: 'Kâğıt', app: 'Uygulamada' };

export const DELIVERY_HINT = {
  paper: 'Optik okuyucu, sonuç dosyası ya da elle giriş (bugünkü akış).',
  app: 'Öğrenci denemeyi uygulamada optik formla çözer; süre sunucudan, cevaplar cihazda da saklanır.',
};

export const BOOKLET_MODES = {
  single: 'Tek kitapçık',
  student_picks: 'Öğrenci başlarken seçer',
  assigned: 'Sistem dağıtır (A/B sırayla)',
};

export const RESULTS_RELEASE = {
  after_close: 'Sınav kapanınca (önerilen)',
  immediate: 'Öğrenci bitirince hemen',
  manual: 'Ben yayımlayınca',
};

/** `ExamAppSettings.state` → [metin, ton]. */
export const APP_STATE = {
  scheduled: ['Açılmadı', 'info'],
  open: ['Açık', 'good'],
  paused: ['Duraklatıldı', 'warn'],
  closed: ['Kapandı', 'dim'],
  finalized: ['Sonuçlandı', 'good'],
};

/** `ExamLiveRow.state` → [metin, ton]. */
export const LIVE_STATE = {
  not_started: ['Başlamadı', 'dim'],
  in_progress: ['Çözüyor', 'info'],
  submitted: ['Bitirdi', 'good'],
  expired: ['Süresi doldu', 'warn'],
};

export const SUBMIT_REASON = {
  manual: 'kendisi bitirdi',
  time_up: 'süre bitti',
  proctor: 'gözetmen bitirdi',
  auto_close: 'sistem kapattı',
};

export const SKIP_REASON = {
  not_started: 'henüz başlamadı',
  already_submitted: 'zaten bitirdi',
  not_in_progress: 'çözmüyor',
  clipped_by_window: 'pencere kapanışında kırpıldı',
};

/** Eşleşme güveni → [metin, ton]. Yalnız ilk üçü kendiliğinden uygulanır. */
export const CONFIDENCE = {
  exact: ['Birebir', 'good'],
  alias: ['Daha önce onaylandı', 'good'],
  synonym: ['Eş anlamlı', 'info'],
  fuzzy: ['Benzer — kontrol et', 'warn'],
  none: ['Eşleşmedi', 'bad'],
};

export const CONFIDENCE_ORDER = ['none', 'fuzzy', 'synonym', 'alias', 'exact'];

/** Türün yaygın süresi (dk): TYT 165, AYT 180, YDT 180, LGS iki oturum (75 + 80). */
export const DEFAULT_DURATION = {
  tyt: 165, ayt_say: 180, ayt_ea: 180, ayt_soz: 180, ayt_dil: 180, lgs: 155, midterm: 40, internal: 60,
};

/** Türkçe harf duyarsız karşılaştırma anahtarı. */
export function fold(text) {
  return String(text ?? '')
    .replace(/[İIı]/g, 'i').toLocaleLowerCase('tr-TR')
    .normalize('NFD').replace(/[̀-ͯ]/g, '')
    .replace(/ş/g, 's').replace(/ğ/g, 'g').replace(/ç/g, 'c').replace(/ö/g, 'o').replace(/ü/g, 'u')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Şık sayısı türden: LGS 4 (A–D), diğerleri 5 (A–E). */
export function defaultOptionCount(type) {
  return type === 'lgs' ? 4 : 5;
}

// ------------------------------------------------------------- tarih / saat

const IST_OFFSET_MS = 3 * 3_600_000;    // Türkiye 2016'dan beri sabit UTC+3

/** ISO zaman damgası → İstanbul {date: 'YYYY-MM-DD', clock: 'HH:MM'}; geçersizse boşlar. */
export function splitStamp(iso) {
  const ms = Date.parse(String(iso || ''));
  if (!Number.isFinite(ms)) return { date: '', clock: '' };
  const local = new Date(ms + IST_OFFSET_MS).toISOString();
  return { date: local.slice(0, 10), clock: local.slice(11, 16) };
}

/** İstanbul tarih + saat → '2026-10-18T10:00:00+03:00'; eksik / bozuksa null. */
export function joinStamp(date, clock) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(date || ''))) return null;
  const match = String(clock || '').trim().replace('.', ':').match(/^(\d{1,2}):?(\d{2})$/);
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return null;
  return `${date}T${match[1].padStart(2, '0')}:${match[2]}:00+03:00`;
}

function addMinutes(date, clock, minutes) {
  const start = joinStamp(date, clock);
  if (!start) return { date, clock };
  return splitStamp(new Date(Date.parse(start) + minutes * 60_000).toISOString());
}

/** Pencere uzunluğu (dk) ya da null. */
export function windowMinutes(draft) {
  const opens = Date.parse(joinStamp(draft?.opensDate, draft?.opensClock) || '');
  const closes = Date.parse(joinStamp(draft?.closesDate, draft?.closesClock) || '');
  if (!Number.isFinite(opens) || !Number.isFinite(closes)) return null;
  return Math.round((closes - opens) / 60_000);
}

// --------------------------------------------------- deneme tanımı (uygulamada)

/** Bölümün sayısal (LGS Sayısal oturumu) olup olmadığı: Matematik ve Fen. */
function numericSection(section) {
  const key = fold(`${section?.subjectId || ''} ${section?.title || ''}`);
  return /matematik|fen/.test(key);
}

/**
 * Oturum önerisi. LGS: Sözel Bölüm (Türkçe, İnkılap, Din, İngilizce — 75 dk) + Sayısal Bölüm (Matematik, Fen —
 * 80 dk). Diğer türlerde tek oturum (boş liste = bütün bölümler, süre denemenin süresi).
 */
export function sessionPreset(type, sections) {
  if (type !== 'lgs' || !Array.isArray(sections) || sections.length < 2) return [];
  const verbal = [];
  const numeric = [];
  sections.forEach((section, index) => (numericSection(section) ? numeric : verbal).push(index));
  if (!verbal.length || !numeric.length) return [];
  return [
    { key: 'sozel', title: 'Sözel Bölüm', durationMinutes: 75, sectionOrders: verbal },
    { key: 'sayisal', title: 'Sayısal Bölüm', durationMinutes: 80, sectionOrders: numeric },
  ];
}

/** Yeni uygulamada deneme taslağı: sınav günü 10:00, kapanış süre + 30 dk sonra (çeyreğe yuvarlı). */
export function defaultAppDraft(type, date) {
  const duration = DEFAULT_DURATION[type] || 120;
  const close = addMinutes(date, '10:00', Math.ceil((duration + 30) / 15) * 15);
  return {
    opensDate: date || '', opensClock: '10:00',
    closesDate: close.date || date || '', closesClock: close.clock || '13:15',
    durationMinutes: duration,
    sessions: [],
    bookletMode: 'single',
    optionCount: null,
    resultsRelease: 'after_close',
    graceSeconds: 120,
  };
}

/** Kayıtlı `Exam.app` → taslak (bölüm kimlikleri sıra numarasına çevrilir). */
export function appDraftFromExam(exam, sections) {
  const app = exam?.app;
  if (!app) return defaultAppDraft(exam?.type, exam?.date);
  const opens = splitStamp(app.opensAt);
  const closes = splitStamp(app.closesAt);
  const order = new Map((sections || []).map((section, index) => [section.id, index]));
  const sessions = (app.sessions || []).length > 1
    ? [...app.sessions].sort((a, b) => (a.order ?? 0) - (b.order ?? 0)).map((session) => ({
      key: session.key, title: session.title, durationMinutes: session.durationMinutes,
      sectionOrders: (session.sectionIds || []).map((id) => order.get(id)).filter((value) => value !== undefined),
    }))
    : [];
  return {
    opensDate: opens.date, opensClock: opens.clock, closesDate: closes.date, closesClock: closes.clock,
    durationMinutes: app.durationMinutes,
    sessions,
    bookletMode: app.bookletMode || 'single',
    optionCount: app.optionCount ?? null,
    resultsRelease: app.resultsRelease || 'after_close',
    graceSeconds: app.graceSeconds ?? 120,
  };
}

/** Oturumların toplam süresi; oturum yoksa denemenin süresi. */
export function totalDuration(draft) {
  const sessions = draft?.sessions || [];
  if (sessions.length) return sessions.reduce((sum, item) => sum + (Number(item.durationMinutes) || 0), 0);
  return Number(draft?.durationMinutes) || 0;
}

/** Taslak doğrulaması → ilk hata metni ya da null. `sectionCount`: formdaki bölüm sayısı. */
export function validateApp(draft, sectionCount) {
  if (!joinStamp(draft?.opensDate, draft?.opensClock)) return 'Açılış tarihini ve saatini (SS:DD) yazın.';
  if (!joinStamp(draft?.closesDate, draft?.closesClock)) return 'Kapanış tarihini ve saatini (SS:DD) yazın.';
  const window = windowMinutes(draft);
  if (window === null || window <= 0) return 'Kapanış açılıştan sonra olmalı.';
  const sessions = draft.sessions || [];
  if (!sessions.length) {
    const minutes = Number(draft.durationMinutes);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 600) return 'Süre 1–600 dakika olmalı.';
  }
  if (sessions.length > 4) return 'En çok 4 oturum tanımlanabilir.';
  const seen = new Map();
  for (const [index, session] of sessions.entries()) {
    if (!String(session.title || '').trim()) return `${index + 1}. oturumun adını yazın.`;
    const minutes = Number(session.durationMinutes);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 600) return `${index + 1}. oturumun süresi 1–600 dakika olmalı.`;
    if (!(session.sectionOrders || []).length) return `${index + 1}. oturuma en az bir bölüm seçin.`;
    for (const order of session.sectionOrders) {
      if (seen.has(order)) return `Bir bölüm iki oturumda olamaz (${order + 1}. bölüm).`;
      seen.set(order, index);
    }
  }
  if (sessions.length && seen.size !== sectionCount) {
    const missing = [];
    for (let order = 0; order < sectionCount; order += 1) if (!seen.has(order)) missing.push(order + 1);
    return `Her bölüm bir oturumda olmalı (oturumsuz: ${missing.join(', ')}. bölüm).`;
  }
  if (window < totalDuration(draft)) {
    return `Pencere (${window} dk) sınav süresinden (${totalDuration(draft)} dk) kısa olamaz.`;
  }
  return null;
}

function sessionKey(title, index, used) {
  let key = fold(title).replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 16) || `oturum-${index + 1}`;
  while (used.has(key)) key = `${key.slice(0, 13)}-${index + 1}`;
  used.add(key);
  return key;
}

/** Taslak → `ExamAppSettingsInput`. */
export function appBody(draft) {
  const used = new Set();
  const sessions = (draft.sessions || []).map((session, index) => ({
    key: /^[a-z0-9_-]{1,16}$/.test(session.key || '') && !used.has(session.key)
      ? (used.add(session.key), session.key) : sessionKey(session.title, index, used),
    title: String(session.title || '').trim(),
    sectionOrders: [...session.sectionOrders].sort((a, b) => a - b),
    durationMinutes: Number(session.durationMinutes),
  }));
  return {
    opensAt: joinStamp(draft.opensDate, draft.opensClock),
    closesAt: joinStamp(draft.closesDate, draft.closesClock),
    durationMinutes: sessions.length ? totalDuration({ sessions }) : Number(draft.durationMinutes),
    sessions,
    bookletMode: draft.bookletMode || 'single',
    optionCount: draft.optionCount ? Number(draft.optionCount) : null,
    resultsRelease: draft.resultsRelease || 'after_close',
    graceSeconds: Number.isInteger(Number(draft.graceSeconds)) ? Number(draft.graceSeconds) : 120,
  };
}

/**
 * Deneme gövdesinin uygulamada kısmı. Kâğıt deneme YENİ oluşturulurken alanlar hiç gönderilmez (eski Akademi
 * alanı tanımasa da kayıt kırılmaz; varsayılan `paper`). Düzenlemede sunucu alanı tanıyorsa (`deliveryMode`
 * yanıtta var) seçim açıkça yazılır.
 */
export function deliveryPart(draft, exam) {
  const mode = draft.deliveryMode === 'app' ? 'app' : 'paper';
  if (mode === 'app') return { deliveryMode: 'app', app: appBody(draft.app || {}) };
  if (exam && 'deliveryMode' in exam) return { deliveryMode: 'paper', app: null };
  return {};
}

/** `Exam.app` özeti (deneme başlığındaki künye satırları). */
export function appFacts(app, fmt) {
  if (!app) return [];
  const sessions = (app.sessions || []).length > 1
    ? app.sessions.map((item) => `${item.title} ${item.durationMinutes} dk`).join(' · ')
    : `${app.durationMinutes} dk`;
  const extra = app.extraMinutesAll ? ` (+${app.extraMinutesAll} dk ek süre herkese)` : '';
  return [
    ['Pencere', `${fmt(app.opensAt)} – ${fmt(app.closesAt)}`],
    ['Süre', `${sessions}${extra}`],
    ['Kitapçık', `${BOOKLET_MODES[app.bookletMode] || app.bookletMode} · ${app.optionCount === 4 ? '4 şık (A–D)' : '5 şık (A–E)'}`],
    ['Sonuç açıklama', RESULTS_RELEASE[app.resultsRelease] || app.resultsRelease],
  ];
}

// ----------------------------------------------------------- canlı sınav

/** Sunucu saati farkı (ms): `serverNow − cihaz saati`. */
export function clockOffset(serverNow, deviceNow = Date.now()) {
  const server = Date.parse(String(serverNow || ''));
  return Number.isFinite(server) ? server - deviceNow : 0;
}

/**
 * Satırın kalan süresi (sn). Duraklatmada ya da `endsAt` yokken sunucunun donmuş değeri; aksi halde
 * `endsAt − (cihaz saati + fark)`.
 */
export function remainingSeconds(row, { offsetMs = 0, now = Date.now(), paused = false } = {}) {
  if (!row || row.state !== 'in_progress') return null;
  if (paused || !row.endsAt) return row.remainingSeconds ?? null;
  const ends = Date.parse(row.endsAt);
  if (!Number.isFinite(ends)) return row.remainingSeconds ?? null;
  return Math.max(0, Math.round((ends - (now + offsetMs)) / 1000));
}

/** 3725 → '1:02:05', 305 → '05:05'. */
export function clockText(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return '—';
  const total = Math.max(0, Math.round(Number(seconds)));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const mm = String(minutes).padStart(2, '0');
  const ss = String(secs).padStart(2, '0');
  return hours ? `${hours}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** Son görülme: 'şimdi', '3 dk önce', '1 sa önce', '—'. */
export function seenText(iso, { offsetMs = 0, now = Date.now() } = {}) {
  const at = Date.parse(String(iso || ''));
  if (!Number.isFinite(at)) return '—';
  const seconds = Math.max(0, Math.round((now + offsetMs - at) / 1000));
  if (seconds < 45) return 'şimdi';
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} dk önce`;
  return `${Math.round(seconds / 3600)} sa önce`;
}

/** Çözüyor ama 2 dakikadır sesi yok (bağlantı / pil / uygulama kapandı) → uyarı. */
export function isQuiet(row, { offsetMs = 0, now = Date.now() } = {}) {
  if (row?.state !== 'in_progress') return false;
  const at = Date.parse(String(row.lastSeenAt || ''));
  return Number.isFinite(at) && now + offsetMs - at > 120_000;
}

/** Canlı tablo süzgeci: durum, sınıf, ad araması (Türkçe harf duyarsız). */
export function filterLive(rows, { state = '', classGroupId = '', q = '' } = {}) {
  const needle = fold(q);
  return (rows || []).filter((row) => (!state || row.state === state)
    && (!classGroupId || row.student?.classGroup?.id === classGroupId)
    && (!needle || fold(`${row.student?.displayName || ''} ${row.student?.schoolNo || ''}`).includes(needle)));
}

/** Satırlardaki sınıflar (süzgeç için), ada göre sıralı. */
export function liveClasses(rows) {
  const map = new Map();
  for (const row of rows || []) {
    const group = row.student?.classGroup;
    if (group?.id && !map.has(group.id)) map.set(group.id, group.name || group.id);
  }
  return [...map].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name, 'tr'));
}

/** Seçimin özeti: hangi düğmeler anlamlı. */
export function selectionInfo(rows) {
  const list = rows || [];
  const count = (state) => list.filter((row) => row.state === state).length;
  const finished = count('submitted') + count('expired');
  return {
    count: list.length,
    inProgress: count('in_progress'),
    notStarted: count('not_started'),
    finished,
    canExtend: list.length > 0 && count('in_progress') + count('not_started') > 0,
    canFinish: count('in_progress') > 0,
    canReopen: list.length === 1 && finished === 1,
  };
}

/** `skipped[]` → kısa açıklama ('2 öğrenci atlandı: Elif Kaya (zaten bitirdi), …'). */
export function skippedText(skipped, nameOf = (id) => id) {
  const list = skipped || [];
  if (!list.length) return '';
  const parts = list.slice(0, 4).map((item) => `${nameOf(item.studentId)} (${SKIP_REASON[item.code] || item.code})`);
  const more = list.length > 4 ? ` ve ${list.length - 4} öğrenci daha` : '';
  return `${list.length} öğrenci atlandı: ${parts.join(', ')}${more}.`;
}

/** İşlem sonucu bildirimi. */
export function actionToast(verb, result, nameOf) {
  const affected = Number(result?.affected) || 0;
  const skipped = skippedText(result?.skipped, nameOf);
  return `${verb}: ${affected} öğrenci.${skipped ? ` ${skipped}` : ''}`;
}

/** Ek süre gövdesi (`ExamLiveExtendRequest`). Boş seçim = herkese. */
export function extendBody({ minutes, studentIds = [], extendWindow = false, reason = '' }) {
  const value = Number(minutes);
  if (!Number.isInteger(value) || value < 1 || value > 240) return { error: 'Ek süre 1–240 dakika olmalı.' };
  const body = { minutes: value, extendWindow: Boolean(extendWindow) };
  if (studentIds.length) body.studentIds = [...studentIds];
  if (String(reason || '').trim()) body.reason = String(reason).trim().slice(0, 300);
  return { body };
}

// ------------------------------------------------- yayınevi tablosu + eşleştirme

/** Soru listesi → 'TYT Türkçe 1–2, 7 · TYT Fen 15' (bölüm başlığı, ardışıklar aralık). */
export function questionsText(questions, sectionTitle = (key) => key) {
  const bySection = new Map();
  for (const item of questions || []) {
    if (!bySection.has(item.sectionKey)) bySection.set(item.sectionKey, []);
    bySection.get(item.sectionKey).push(Number(item.questionNo));
  }
  const parts = [];
  for (const [key, numbers] of bySection) {
    const sorted = [...new Set(numbers)].sort((a, b) => a - b);
    const ranges = [];
    let start = sorted[0];
    let prev = sorted[0];
    for (const value of sorted.slice(1).concat([null])) {
      if (value !== null && value === prev + 1) { prev = value; continue; }
      ranges.push(start === prev ? String(start) : `${start}–${prev}`);
      start = value;
      prev = value;
    }
    parts.push(`${sectionTitle(key)} ${ranges.join(', ')}`);
  }
  return parts.join(' · ');
}

/** Hedefin karar gövdesi: konu düzeyinde `topicId`, ünite düzeyinde yalnız `unitId`. */
export function targetDecision(matchKey, target, { remember = true } = {}) {
  if (!target) return { matchKey, skip: true, remember: false };
  return target.level === 'unit' && target.unitId
    ? { matchKey, unitId: target.unitId, remember }
    : { matchKey, topicId: target.topicId, remember };
}

/**
 * Satırın şu anki durumu. `decision`: yöneticinin bu ekrandaki kararı ({target} | {skip: true}) ya da yok.
 *   state: 'auto' (kendiliğinden uygulanır) | 'decided' (yönetici seçti) | 'skipped' | 'review' (karar bekliyor)
 */
export function rowState(row, decision) {
  if (decision?.skip) return { state: 'skipped', target: null };
  if (decision?.target) return { state: 'decided', target: decision.target };
  if (row?.autoApplied && row.target) return { state: 'auto', target: row.target };
  return { state: 'review', target: row?.target || null };
}

/** Karar bekleyen satır: kendiliğinden uygulanmayan ve henüz karar verilmemiş (`fuzzy` / `none`). */
export function needsReview(row, decision) {
  return rowState(row, decision).state === 'review';
}

/** Tablo süzgeci: yalnız kontrol gerekenler, güven, ders, arama (ad / kod / hedef). */
export function filterMatches(rows, decisions, { onlyReview = false, confidence = '', subjectId = '', q = '' } = {}) {
  const needle = fold(q);
  return (rows || []).filter((row) => {
    const decision = decisions?.get?.(row.matchKey);
    if (onlyReview && !needsReview(row, decision)) return false;
    if (confidence && row.confidence !== confidence) return false;
    if (subjectId && (row.subjectId || row.subjectRaw) !== subjectId) return false;
    if (!needle) return true;
    const { target } = rowState(row, decision);
    return fold(`${row.name} ${row.code || ''} ${row.subjectRaw || ''} ${target?.title || ''}`).includes(needle);
  });
}

/** Sayaçlar: satır, soru, eşlenen soru, kontrol bekleyen, atlanan, güven dağılımı. */
export function matchCounts(rows, decisions) {
  const out = { rows: 0, questions: 0, mappedQuestions: 0, review: 0, reviewQuestions: 0, skipped: 0,
    byConfidence: { exact: 0, alias: 0, synonym: 0, fuzzy: 0, none: 0 } };
  for (const row of rows || []) {
    const questions = (row.questions || []).length;
    const { state, target } = rowState(row, decisions?.get?.(row.matchKey));
    out.rows += 1;
    out.questions += questions;
    out.byConfidence[row.confidence] = (out.byConfidence[row.confidence] || 0) + 1;
    if (state === 'review') { out.review += 1; out.reviewQuestions += questions; }
    if (state === 'skipped') out.skipped += 1;
    if ((state === 'auto' || state === 'decided') && target) out.mappedQuestions += questions;
  }
  out.rate = out.questions ? out.mappedQuestions / out.questions : 0;
  return out;
}

/**
 * "Önerilenleri onayla": karar bekleyen ve önerisi olan (`fuzzy`) satırlara ilk öneriyi karar olarak yazar.
 * `none` satırlar dokunulmadan kalır. Yeni karar sayısını döndürür.
 */
export function acceptSuggestions(rows, decisions) {
  let added = 0;
  for (const row of rows || []) {
    if (!needsReview(row, decisions.get(row.matchKey))) continue;
    const suggestion = row.target || row.alternatives?.[0] || null;
    if (!suggestion) continue;
    decisions.set(row.matchKey, { target: suggestion });
    added += 1;
  }
  return added;
}

/** Ekrandaki kararlar → API `decisions[]` (yalnız yöneticinin dokunduğu satırlar). */
export function decisionsBody(decisions, { remember = true } = {}) {
  const out = [];
  for (const [matchKey, decision] of decisions || []) {
    if (decision?.skip) out.push({ matchKey, skip: true, remember: false });
    else if (decision?.target) out.push(targetDecision(matchKey, decision.target, { remember }));
  }
  return out;
}

/** Önizleme künyesi (kv satırları). */
export function previewFacts(preview, typeName = (type) => type) {
  if (!preview) return [];
  const sections = (preview.sections || []).map((item) => `${item.title} ${item.questionCount}`).join(' · ');
  const total = (preview.sections || []).reduce((sum, item) => sum + (Number(item.questionCount) || 0), 0);
  const keys = Object.entries(preview.answerKeys || {}).map(([booklet, map]) => {
    const length = Object.values(map || {}).reduce((sum, text) => sum + String(text || '').length, 0);
    return `${booklet}: ${length}/${total}`;
  }).join(' · ');
  return [
    ['Yayınevi', preview.publisher || '—'],
    ['Deneme', [preview.examName, preview.code ? `kod ${preview.code}` : ''].filter(Boolean).join(' · ') || '—'],
    ['Sınıf', preview.gradeText || '—'],
    ['Tür', preview.examType ? typeName(preview.examType) : 'Algılanamadı — aşağıdan seçin'],
    ['Bölümler', `${sections || '—'} (toplam ${total} soru)`],
    ['Kitapçıklar', (preview.booklets || []).join(', ') || '—'],
    ['Cevap anahtarı', keys || 'Yok'],
  ];
}

/** Yeni deneme gövdesi tablodan: bölümler test sırasıyla, katalog dersiyle. Eksik ders → hata metni. */
export function examFromPreview(preview, { name, date, type, publisher, subjects = {} } = {}) {
  const sections = [];
  for (const [index, item] of (preview?.sections || []).entries()) {
    const subjectId = subjects[item.key] || item.subjectId;
    if (!subjectId) return { error: `“${item.title}” bölümünün dersini seçin.` };
    sections.push({ subjectId, title: item.title, questionCount: Number(item.questionCount), order: index + 1 });
  }
  if (!sections.length) return { error: 'Tabloda bölüm bulunamadı.' };
  if (!String(name || '').trim()) return { error: 'Deneme adını yazın.' };
  if (!type) return { error: 'Deneme türünü seçin.' };
  return {
    exam: {
      name: String(name).trim().slice(0, 120),
      type,
      publisher: String(publisher || preview?.publisher || '').trim().slice(0, 80) || null,
      date,
      bookletTypes: (preview?.booklets || []).length ? [...preview.booklets] : ['A'],
      sections,
      classGroupIds: [],
      isHistorical: false,
    },
  };
}

/** Türün müfredat ailesi (program `exam` alanı): LGS / TYT / AYT. */
export function familyOf(type) {
  if (type === 'lgs') return 'LGS';
  if (type === 'tyt') return 'TYT';
  if (String(type || '').startsWith('ayt_')) return 'AYT';
  return null;
}

/**
 * Konu seçicinin arama sonucu: programların ünite / konu listesinden (Türkçe harf duyarsız). Ünite başlığı
 * eşleşirse ünite de seçilebilir (ünite düzeyi eşleme). En çok `limit` sonuç.
 */
export function searchTopics(programs, q, { limit = 40 } = {}) {
  const needle = fold(q);
  const out = [];
  for (const program of programs || []) {
    for (const unit of program.units || []) {
      if (unit.retired) continue;
      const unitHit = !needle || fold(unit.title).includes(needle);
      if (unitHit) {
        out.push({ level: 'unit', unitId: unit.id, topicId: unit.topics?.find((topic) => !topic.retired)?.id || null,
          title: unit.title, path: `${program.title} › ${unit.title}`, subjectId: program.subject?.id || program.subjectId || '',
          programId: program.id });
      }
      for (const topic of unit.topics || []) {
        if (topic.retired) continue;
        if (!unitHit && !fold(topic.title).includes(needle)) continue;
        out.push({ level: 'topic', unitId: unit.id, topicId: topic.id, title: topic.title,
          path: `${program.title} › ${unit.title} › ${topic.title}`, subjectId: program.subject?.id || program.subjectId || '',
          programId: program.id });
      }
      if (out.length >= limit) return out.slice(0, limit);
    }
  }
  return out.slice(0, limit);
}
