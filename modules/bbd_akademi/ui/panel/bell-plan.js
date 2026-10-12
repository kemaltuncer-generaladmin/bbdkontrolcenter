// BBD Akademi — zil planı ve anons adı için SAF mantık (DOM yok, import yok).
//
// Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md
// (§1 sahiplik, §3.2 zil planı, §3.3 durum ucu, §3.5 tatil/özel gün).
//
// NEDEN AYRI DOSYA. Bu dosya hiçbir şey import etmez; böylece Node ile
// doğrudan yüklenip birim testine girer (tests/test_akademi_bell_plan_logic.py).
// Ekran dosyaları (staff.js, timetable.js, institution-calendar.js) karar
// vermez, buradaki işlevleri çağırır.
//
// SÖZLEŞME ŞALTERLERİ. Takvim etkinliğinin `bell` alanı ve personelin
// `announceName`'i Akademi sözleşmesine henüz girmedi; backend'in
// `contract_features.py` şalterleri `/km/status` → `contractFeatures` ile
// gelir. Şalter kapalıyken gövdeye alan EKLENMEZ (`withBell`).

export const ANNOUNCE_NAME_MAX = 60;

/** Anons adı: baştaki/sondaki boşluk atılır, iç boşluklar teke iner. */
export function cleanAnnounceName(raw) {
  return String(raw ?? '').split(/\s+/).filter(Boolean).join(' ');
}

/** Hata cümlesi ya da null. Boş serbesttir (anons adı yok = genel çağrı). */
export function announceNameError(raw) {
  const value = cleanAnnounceName(raw);
  return value.length > ANNOUNCE_NAME_MAX
    ? `Anons adı en çok ${ANNOUNCE_NAME_MAX} karakter olabilir (şu an ${value.length}).` : null;
}

// ------------------------------------------------------- takvim: zil alanı

export const BELL_MODE_TEXT = {
  normal: 'Normal — haftalık zil saatleri',
  off: 'Tatil — zil ve anons çalmaz',
  profile: 'Saat seti',
};

/** Tür değişince önerilen zil: tatilde zil çalmaz (§3.5), diğerlerinde normal. */
export function defaultBellChoice(kind) {
  return kind === 'holiday' ? 'off' : 'normal';
}

/**
 * Seçenekler: normal, off ve KM saat setleri (`profile:<id>`). `profiles`
 * KM Zil Sistemi'nden gelir (`GET /api/bell/state` → `profiles`); yoksa
 * yalnız normal/off. `keep` kayıtlı ama listede olmayan seçimi korur.
 */
export function bellChoices(profiles, keep = '') {
  const options = [
    { value: 'normal', label: BELL_MODE_TEXT.normal },
    { value: 'off', label: BELL_MODE_TEXT.off },
  ];
  for (const profile of Array.isArray(profiles) ? profiles : []) {
    if (!profile || !profile.id) continue;
    options.push({ value: `profile:${profile.id}`, label: `Saat seti: ${profile.name || profile.id}` });
  }
  if (keep && !options.some((option) => option.value === keep)) {
    options.push({ value: keep, label: 'Saat seti (KM\'de bulunamadı)' });
  }
  return options;
}

/** Kayıtlı etkinliğin `bell` alanı → seçim. Alan yoksa türün varsayılanı. */
export function choiceFromBell(bell, kind) {
  if (!bell || typeof bell !== 'object' || !bell.mode) return defaultBellChoice(kind);
  if (bell.mode === 'profile') return bell.profileId ? `profile:${bell.profileId}` : 'normal';
  return bell.mode === 'off' ? 'off' : 'normal';
}

/** Seçim + "dersleri iptal et" → sözleşmedeki `bell` nesnesi. */
export function bellPayload(choice, cancelSessions) {
  const text = String(choice || 'normal');
  if (text.startsWith('profile:') && text.length > 'profile:'.length) {
    return { mode: 'profile', profileId: text.slice('profile:'.length), cancelSessions: Boolean(cancelSessions) };
  }
  return { mode: text === 'off' ? 'off' : 'normal', profileId: null, cancelSessions: Boolean(cancelSessions) };
}

/**
 * Gövdeye `bell` ekler — YALNIZ şalter açıksa. Kapalıyken gövde aynen döner:
 * sözleşmede olmayan alan Akademi'de 422 olurdu (vekil gövdeyi doğrulamaz).
 */
export function withBell(body, { enabled, choice, cancelSessions }) {
  if (!enabled) return body;
  return { ...body, bell: bellPayload(choice, cancelSessions) };
}

/** Etkinlik ayrıntısında tek satır. `profiles` adları çözmek için. */
export function bellText(bell, profiles = []) {
  if (!bell || typeof bell !== 'object' || !bell.mode) return null;
  let text = BELL_MODE_TEXT[bell.mode] || bell.mode;
  if (bell.mode === 'profile') {
    const found = (profiles || []).find((item) => item && item.id === bell.profileId);
    text = `Saat seti: ${found?.name || bell.profileId || '—'}`;
  }
  return bell.cancelSessions ? `${text} · dersler iptal` : text;
}

// ------------------------------------------------------- zil planı durumu

const SOURCES = ['akademi', 'km', 'none'];
export const PUSH_RESULT_TEXT = { applied: 'uygulandı', partial: 'kısmen uygulandı', failed: 'başarısız' };

const intOf = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0);
const boolOr = (...values) => {
  for (const value of values) if (typeof value === 'boolean') return value;
  return null;
};

/** Acil kapatma açıklaması: Akademi kısa metin verir, KM nesne verebilir. */
export function overrideText(override) {
  if (!override) return '';
  if (typeof override === 'string') return override;
  return [override.reason || override.note || '', override.by ? `${override.by}` : '']
    .filter(Boolean).join(' · ');
}

/**
 * Durum yanıtı → kart özeti. İki kaynağın biçimi birlikte okunur:
 *   · KM Zil Sistemi `GET /api/bell/plan/status` (§3.3): `agent: {online, lastSeenAt}`
 *   · Akademi `admin_getBellPlanStatus` (`BellPlanStatus`): `agentOnline`,
 *     `agentLastSeenAt`, `lastPushAt`, `lastPushResult`, `stale`, gün başına `inSync`
 * Bilinmeyen alan yok sayılır; eksik alan `null` olur (kart o satırı çizmez).
 */
export function summarizePlanStatus(status, today) {
  const days = Array.isArray(status?.days) ? status.days.filter((day) => day && day.date) : [];
  const counts = { akademi: 0, km: 0, none: 0 };
  let voicesPending = 0;
  let outOfSync = 0;
  const overrides = [];
  for (const day of days) {
    counts[SOURCES.includes(day.source) ? day.source : 'none'] += 1;
    voicesPending += intOf(day.voicesPending);
    if (day.inSync === false) outOfSync += 1;
    if (day.override || day.source === 'km') overrides.push({ date: day.date, text: overrideText(day.override) });
  }
  const row = days.find((day) => day.date === today) || null;
  return {
    total: days.length,
    counts,
    voicesPending,
    outOfSync,
    overrides,
    bellEnabled: boolOr(status?.bellEnabled),
    sessionCalls: boolOr(status?.sessionCallsEnabled),
    agentOnline: boolOr(status?.agent?.online, status?.agentOnline),
    agentLastSeenAt: status?.agent?.lastSeenAt || status?.agentLastSeenAt || null,
    lastPushAt: status?.lastPushAt || null,
    lastPushResult: status?.lastPushResult || null,
    stale: status?.stale === true,
    today: row ? {
      date: row.date,
      mode: row.mode || 'normal',
      reason: row.reason || null,
      profileId: row.profileId || null,
      source: SOURCES.includes(row.source) ? row.source : 'none',
      calls: intOf(row.calls),
      voicesPending: intOf(row.voicesPending),
      override: overrideText(row.override) || null,
      lastDispatchAt: row.lastDispatchAt || null,
    } : null,
  };
}

/** Bugünün kipi tek cümle: "Normal", "Tatil — zil çalmaz · Cumhuriyet Bayramı", "Saat seti". */
export function todayModeText(today) {
  if (!today) return 'Bugün için plan yok';
  const base = today.mode === 'off' ? 'Tatil — zil çalmaz'
    : today.mode === 'profile' ? 'Saat seti' : 'Normal';
  return today.reason ? `${base} · ${today.reason}` : base;
}

/**
 * Akademi'den okuma başarısızsa KM Zil Sistemi'ne düşülür mü? Akademi işlemi
 * sözleşmede var ama sunucuda henüz yok (404/405/501) ya da kiracıda
 * `bell_plan_sync` bayrağı kapalı (`feature_disabled`) — bu durumlarda KM'nin
 * kendi durumu yine anlamlıdır. Yetki ya da bağlantı hatasında düşülmez.
 */
export function shouldFallBack(failure) {
  const status = Number(failure?.status || 0);
  return [404, 405, 501].includes(status) || failure?.code === 'feature_disabled';
}

/**
 * Durum okuma hatası → kullanıcı cümlesi. 404: uç bu sürümde yok (zil planı
 * henüz gelmedi), 403: izin yok. Ham hata ekrana çıkmaz.
 */
export function planStatusProblem(failure, source) {
  const status = Number(failure?.status || 0);
  if (source === 'akademi') {
    if (failure?.code === 'feature_disabled') {
      return { tone: 'info', text: 'Akademi\'de zil planı eşitlemesi (bell_plan_sync) bu kurum için kapalı; '
        + 'zil haftalık saatlerle çalışıyor.' };
    }
    if (status === 404) return { tone: 'info', text: 'Akademi zil planı durumunu henüz sunmuyor.' };
    if (status === 403) return { tone: 'info', text: 'Zil planı durumunu görme yetkiniz yok.' };
    if (failure?.code === 'bell_unavailable' || status === 503) {
      return { tone: 'warn', text: 'Akademi KM Zil Sistemi\'ne şu an ulaşamıyor; birazdan tekrar deneyin.' };
    }
    return { tone: 'warn', text: `Zil planı durumu Akademi'den okunamadı: ${failure?.message || 'bilinmeyen hata'}` };
  }
  if (status === 404) {
    return { tone: 'info', text: 'KM Zil Sistemi bu sürümde zil planını henüz desteklemiyor; zil haftalık '
      + 'saatlerle çalışıyor, Akademi planı devreye girince durum burada görünür.' };
  }
  if (status === 403) {
    return { tone: 'info', text: 'Zil planı durumunu görmek için Zil Sistemi görüntüleme izni (bell.view) gerekir.' };
  }
  return { tone: 'warn', text: `Zil planı durumu okunamadı: ${failure?.message || 'KM Zil Sistemi yanıt vermedi'}` };
}
