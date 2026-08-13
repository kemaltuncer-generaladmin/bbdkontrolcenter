// Zil sistemi — veri katmanı.
//
// KALICILIK ARTIK ÇEKİRDEKTE (K5):
//
//     GET  /api/bell/state
//     PUT  /api/bell/settings
//
// Tarayıcı belleğindeki eski ayar, çekirdek tarafı BOŞSA bir kez içeri alınır.
// `load()`/`save()` eşzamanlı kalır; okuma önbellekten, yazma kuyruklanarak.
//
// BURADA DERS SAATİ TUTULMAZ. Saatler Ders Takvimi modülünündür; zil onları
// `bbd_class_schedule.week` yeteneğinden okur (K3/K5). Burada yalnızca "hangi
// grup hangi sesi, hangi düzeyde, hangi derste çalsın" kararı durur.

const KEY = 'km.bell.v1';

export const DEFAULT_SOUND = 'classic_electric';
export const DEFAULT_VOLUME = 85;

/** Bir grubun ön tanımlı zil ayarı. */
export function defaultGroupSettings() {
  return {
    enabled: true,
    soundId: DEFAULT_SOUND,
    volume: DEFAULT_VOLUME,
    ringStart: true,
    ringEnd: true,
    // Ders bazlı istisnalar. Anahtar: "<gün>|<başlangıç>"
    // Değer: { start, end, soundId } — soundId null ise grubun sesi çalar.
    overrides: {},
  };
}

function normalize(state) {
  const groups = {};
  for (const [id, value] of Object.entries(state?.groups || {})) {
    const base = defaultGroupSettings();
    groups[id] = {
      enabled: value?.enabled !== false,
      soundId: typeof value?.soundId === 'string' ? value.soundId : base.soundId,
      volume: Math.min(100, Math.max(0, Number(value?.volume ?? base.volume))),
      ringStart: value?.ringStart !== false,
      ringEnd: value?.ringEnd !== false,
      overrides: typeof value?.overrides === 'object' && value.overrides ? value.overrides : {},
    };
  }
  return {
    version: 1,
    // Ana şalter: tek yerden tüm zilleri susturmak (deneme sınavı, tören).
    enabled: state?.enabled !== false,
    groups,
  };
}

let apiCall = null;
let cache = null;
let writeTimer = null;
let lastError = '';

function readLocal() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? normalize(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

/** Çekirdekten okur; boşsa yereldeki eski ayarı bir kez içeri alır. */
export async function init(api) {
  apiCall = api;
  try {
    const payload = await api('/api/bell/state');
    const settings = payload?.settings;
    const empty = !settings || Object.keys(settings.groups || {}).length === 0;
    if (empty) {
      const local = readLocal();
      if (local && Object.keys(local.groups || {}).length > 0) {
        const adopted = await api('/api/bell/adopt', { method: 'POST', body: { settings: local } });
        if (adopted.adopted) {
          try { localStorage.removeItem(KEY); } catch { /* önemsiz */ }
          cache = normalize(adopted.settings);
          return { migrated: true, error: '', state: payload };
        }
      }
    }
    cache = normalize(settings);
    return { migrated: false, error: '', state: payload };
  } catch (error) {
    lastError = error.message || String(error);
    cache = readLocal() || normalize(null);
    return { migrated: false, error: lastError, state: null };
  }
}

export function load() {
  return cache ? normalize(cache) : normalize(null);
}

/** Yazma gecikmeli: her anahtar değişiminde HTTP isteği atılmaz. */
export function save(state) {
  cache = normalize(state);
  if (!apiCall) return false;

  window.clearTimeout(writeTimer);
  writeTimer = window.setTimeout(async () => {
    try {
      await apiCall('/api/bell/settings', { method: 'PUT', body: { settings: cache } });
      lastError = '';
    } catch (error) {
      lastError = error.message || String(error);
      console.warn('zil ayarı kaydedilemedi', error);
    }
  }, 600);
  return true;
}

export function error() {
  return lastError;
}

export async function flush() {
  window.clearTimeout(writeTimer);
  if (!apiCall || !cache) return;
  try {
    await apiCall('/api/bell/settings', { method: 'PUT', body: { settings: cache } });
  } catch (error) {
    console.warn('zil ayarı kaydedilemedi', error);
  }
}

/** Grubun ayarını getirir; yoksa ön tanımlıyı yazar. */
export function settingsFor(state, groupId) {
  if (!state.groups[groupId]) state.groups[groupId] = defaultGroupSettings();
  return state.groups[groupId];
}

export function overrideKey(dayKey, start) {
  return `${dayKey}|${start}`;
}
