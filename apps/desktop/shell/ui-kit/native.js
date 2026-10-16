// İşletim sistemi köprüsü — yerel bildirim ve dosya pencereleri (ADR 0032).
//
// PANELLER BURAYI KULLANIR (kit.js de yeniden dışa vurur):
//
//     import { notify, saveFile, openFile } from '../../ui-kit/kit.js';
//
//     await saveFile({ name: 'rapor.pdf', data: blob, filters: [{ name: 'PDF', extensions: ['pdf'] }] });
//     const [file] = await openFile({ accept: ['.csv'] });          // File nesneleri
//     notify('Yedek tamamlandı', '1.204 kayıt yazıldı.');
//
// MASAÜSTÜNDE işletim sisteminin KENDİ penceresi açılır (Kaydet/Aç iletişim
// kutusu, bildirim merkezi); dosyayı Rust yazar/okur, arayüze dosya sistemi
// izni açılmaz. TARAYICIDA (tasarım denemesi, Node sınaması) aynı çağrı
// `<a download>` / `<input type=file>` / `Notification` yoluna düşer — panel
// iki ayrı kod yolu yazmaz.

const tauriInvoke = () => globalThis.window?.__TAURI__?.core?.invoke || null;

/** Masaüstü kabuğunda mıyız? */
export function isDesktop() {
  return Boolean(tauriInvoke());
}

// ------------------------------------------------------------------ yardımcı

/** Blob / ArrayBuffer / Uint8Array / metin → Uint8Array. */
async function toBytes(data) {
  if (data == null) return new Uint8Array();
  if (data instanceof Uint8Array) return data;
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
  if (typeof data.arrayBuffer === 'function') return new Uint8Array(await data.arrayBuffer());
  return new TextEncoder().encode(String(data));
}

export function bytesToBase64(bytes) {
  let text = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
  }
  return btoa(text);
}

export function base64ToBytes(base64) {
  const text = atob(String(base64 || ''));
  const out = new Uint8Array(text.length);
  for (let i = 0; i < text.length; i += 1) out[i] = text.charCodeAt(i);
  return out;
}

const MIME = {
  pdf: 'application/pdf', csv: 'text/csv', txt: 'text/plain', json: 'application/json',
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', gif: 'image/gif',
  svg: 'image/svg+xml', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel', zip: 'application/zip', mp4: 'video/mp4', mp3: 'audio/mpeg',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
};

export function mimeOf(name) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  return MIME[ext] || 'application/octet-stream';
}

/** Kaydet penceresinin süzgeci dosya adından türetilir (verilmediyse). */
function filtersFor(name, filters) {
  if (Array.isArray(filters) && filters.length) return filters;
  const ext = String(name || '').includes('.') ? String(name).split('.').pop().toLowerCase() : '';
  return ext ? [{ name: ext.toUpperCase(), extensions: [ext] }] : [];
}

/** `accept` ('.png,image/*' ya da dizi) → yerel pencere süzgeci. */
export function acceptToFilters(accept) {
  const parts = (Array.isArray(accept) ? accept : String(accept || '').split(','))
    .map((part) => String(part).trim().toLowerCase())
    .filter(Boolean);
  if (!parts.length) return [];
  const families = { 'image/*': ['png', 'jpg', 'jpeg', 'webp', 'gif'], 'video/*': ['mp4', 'mov', 'webm'],
    'audio/*': ['mp3', 'wav', 'm4a'], 'application/pdf': ['pdf'], 'text/csv': ['csv'] };
  const extensions = new Set();
  for (const part of parts) {
    if (part.startsWith('.')) extensions.add(part.slice(1));
    else if (families[part]) families[part].forEach((ext) => extensions.add(ext));
  }
  return extensions.size ? [{ name: 'Uygun dosyalar', extensions: [...extensions] }] : [];
}

// ------------------------------------------------------------------ kaydet

/**
 * "Farklı kaydet". Kullanıcı vazgeçerse `null`, kaydederse yol (masaüstü) ya
 * da `true` (tarayıcı — yol bilinmez).
 *
 * @param {{name: string, data: Blob|ArrayBuffer|Uint8Array|string,
 *          filters?: {name: string, extensions: string[]}[]}} o
 */
export async function saveFile({ name = 'dosya', data, filters } = {}) {
  const bytes = await toBytes(data);
  const invoke = tauriInvoke();
  if (invoke) {
    const path = await invoke('native_save_file', {
      name, filters: filtersFor(name, filters), base64: bytesToBase64(bytes),
    });
    return path || null;
  }
  // Tarayıcı yolu: indirme bağlantısı. `__kmNativeDownload` işaretli: kabuğun
  // indirme yakalayıcısı bunu yeniden yakalamaz.
  const doc = globalThis.document;
  if (!doc) return null;
  const url = URL.createObjectURL(new Blob([bytes], { type: mimeOf(name) }));
  const link = doc.createElement('a');
  link.href = url;
  link.download = name;
  link.__kmNativeDownload = true;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  return true;
}

// --------------------------------------------------------------------- aç

/**
 * "Aç" penceresi. Seçilen dosyalar `File` nesnesi olarak döner (mevcut
 * `<input type=file>` kodu değişmeden çalışır). Vazgeçilirse boş dizi.
 *
 * @param {{accept?: string|string[], multiple?: boolean}} o
 */
export async function openFile({ accept = '', multiple = false } = {}) {
  const invoke = tauriInvoke();
  if (invoke) {
    const picked = await invoke('native_open_files', { filters: acceptToFilters(accept), multiple });
    return (picked || []).map((item) => new File([base64ToBytes(item.base64)], item.name,
      { type: mimeOf(item.name) }));
  }
  const doc = globalThis.document;
  if (!doc) return [];
  return new Promise((resolve) => {
    const input = doc.createElement('input');
    input.type = 'file';
    if (accept) input.accept = Array.isArray(accept) ? accept.join(',') : accept;
    input.multiple = Boolean(multiple);
    input.addEventListener('change', () => resolve([...(input.files || [])]));
    input.addEventListener('cancel', () => resolve([]));
    input.click();
  });
}

// --------------------------------------------------------------- bildirim

/**
 * İşletim sisteminin bildirim merkezine yazar.
 *
 * `whenInactive: true` → pencere ÖNDEYKEN bildirim gösterilmez (kullanıcı
 * zaten ekrana bakıyor; panel kendi toast'ını göstersin). Dönen değer
 * bildirimin gösterilip gösterilmediğidir.
 *
 * Kullanıcı Tercihler'de bildirimleri kapattıysa hiçbir şey gösterilmez.
 */
export async function notify(title, body = '', { whenInactive = false } = {}) {
  try {
    if (globalThis.localStorage?.getItem('km.pref.notify') === 'off') return false;
  } catch { /* depolama kapalı: varsayılan açık */ }
  const doc = globalThis.document;
  if (whenInactive && doc?.hasFocus?.()) return false;
  const invoke = tauriInvoke();
  try {
    if (invoke) {
      await invoke('native_notify', { title: String(title || ''), body: String(body || '') });
      return true;
    }
    const N = globalThis.Notification;
    if (!N) return false;
    if (N.permission !== 'granted' && (await N.requestPermission?.()) !== 'granted') return false;
    // eslint-disable-next-line no-new
    new N(String(title || ''), { body: String(body || '') });
    return true;
  } catch (error) {
    console.warn('[kit] bildirim gösterilemedi:', error);
    return false;
  }
}
