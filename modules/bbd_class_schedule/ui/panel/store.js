// Ders takvimi — veri katmanı.
//
// KALICILIK ARTIK ÇEKİRDEKTE. Veri modülün kendi tablosunda durur (K5):
//
//     GET  /api/bbd_class_schedule/groups
//     PUT  /api/bbd_class_schedule/groups
//
// Tarayıcı belleğindeki (localStorage) eski takvim, çekirdek tarafı BOŞSA
// bir kez içeri alınır ve sonra yerelden silinir. Doluysa dokunulmaz — iki
// makinede açılan panel birbirinin verisini ezmesin.
//
// `load()` ve `save()` eşzamanlı kalır: çağıran taraf değişmedi. Okuma
// önbellekten, yazma arka planda kuyruklanarak yapılır.

import { cleanName, DAYS, emptyWeek, newId } from './schedule.js';

const KEY = 'km.bbd_class_schedule.v1';

/** Grup renkleri — sırayla dağıtılır, kullanıcı seçmek zorunda kalmasın. */
export const COLORS = ['#5b8cff', '#12b5a4', '#f0883a', '#a86bff', '#e8567a', '#3ea55a'];

function defaultState() {
  return {
    version: 1,
    // Gruplar OPSİYONEL: tek plan yetiyorsa kullanıcı "Genel"in içinde kalır.
    groups: [{ id: newId(), name: 'Genel', color: COLORS[0], students: [], week: emptyWeek() }],
  };
}

/** Eksik/bozuk alanları tamamlar — elle düzenlenmiş veri ekranı düşürmesin. */
function normalize(state) {
  if (!state || !Array.isArray(state.groups) || state.groups.length === 0) return defaultState();

  return {
    version: 1,
    groups: state.groups.map((group, index) => {
      const week = emptyWeek();
      for (const day of DAYS) {
        const blocks = group?.week?.[day.key];
        if (Array.isArray(blocks)) {
          week[day.key] = blocks
            .filter((block) => block && typeof block.start === 'string' && typeof block.end === 'string')
            .map((block) => ({
              id: block.id || newId(),
              start: block.start,
              end: block.end,
              name: cleanName(block.name),
            }));
        }
      }
      return {
        id: group?.id || newId(),
        name: String(group?.name || 'Grup').slice(0, 60),
        color: group?.color || COLORS[index % COLORS.length],
        students: Array.isArray(group?.students) ? group.students : [],
        week,
      };
    }),
  };
}

let apiCall = null;
let cache = null;
let writeTimer = null;
let lastError = '';

/** Tarayıcı belleğindeki eski veri — yalnız göç için okunur. */
function readLocal() {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? normalize(JSON.parse(raw)) : null;
  } catch {
    return null;
  }
}

/**
 * Çekirdekten okur; boşsa yereldeki eski takvimi bir kez içeri alır.
 * `mount()` bunu ilk `load()` çağrısından ÖNCE beklemelidir.
 */
export async function init(api) {
  apiCall = api;
  try {
    const payload = await api('/api/bbd_class_schedule/groups');
    if (payload.empty) {
      const local = readLocal();
      if (local && local.groups?.length) {
        const adopted = await api('/api/bbd_class_schedule/adopt',
          { method: 'POST', body: { document: local } });
        if (adopted.adopted) {
          // Göç tamam: tek doğru kaynak artık çekirdek. Yerel kopya kafa karıştırmasın.
          try { localStorage.removeItem(KEY); } catch { /* önemsiz */ }
          cache = normalize(adopted.document);
          return { migrated: true, error: '' };
        }
      }
    }
    cache = normalize(payload.document);
    return { migrated: false, error: '' };
  } catch (error) {
    lastError = error.message || String(error);
    cache = readLocal() || defaultState();
    return { migrated: false, error: lastError };
  }
}

export function load() {
  return cache ? normalize(cache) : defaultState();
}

/**
 * Yazma. Panel her tuş vuruşunda çağırdığı için ÇEKİRDEĞE GÖNDERİM GECİKTİRİLİR;
 * yoksa her harf bir HTTP isteği olur.
 */
export function save(state) {
  cache = normalize(state);
  if (!apiCall) return false;

  window.clearTimeout(writeTimer);
  writeTimer = window.setTimeout(async () => {
    try {
      await apiCall('/api/bbd_class_schedule/groups',
        { method: 'PUT', body: { document: cache } });
      lastError = '';
    } catch (error) {
      lastError = error.message || String(error);
      console.warn('takvim kaydedilemedi', error);
    }
  }, 600);
  return true;
}

/** Son yazma hatası — ekran "kaydedilemedi" diyebilsin. */
export function error() {
  return lastError;
}

/** Bekleyen yazmayı hemen gönderir (panel kapanırken). */
export async function flush() {
  window.clearTimeout(writeTimer);
  if (!apiCall || !cache) return;
  try {
    await apiCall('/api/bbd_class_schedule/groups', { method: 'PUT', body: { document: cache } });
  } catch (error) {
    console.warn('takvim kaydedilemedi', error);
  }
}
