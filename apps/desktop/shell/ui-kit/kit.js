// Panel araç kutusu — DOM yardımcıları ve biçimlendirme.
//
// KONUM: bu dosya kabuğa aittir ve TEK KOPYADIR (ADR 0011). Paneller
// `../../ui-kit/kit.js` ile import eder; yol panelin KOPYALANMIŞ konumuna
// (`shell/panels/<id>/`) göredir. Panel kaynağı `modules/<id>/ui/panel/`
// içindeyken bu yol dosya sisteminde çözülmez — normaldir.
//
// Buradaki hiçbir şey modül adı bilmez ve iş kuralı taşımaz: yalnız DOM,
// biçim ve etkileşim. `icons.js` ve `style.css` ile aynı kategoridedir.
//
// SÜRÜM SÖZLEŞMESİ: yayınlanmış imza geriye dönük uyumsuz değiştirilmez.
// Yeni davranış yeni parametreyle gelir. Değişiklikler README.md'de.

export const KIT_VERSION = '1.0.0';

/** Tek satırda element: h('div', 'sinif', 'metin'). */
export const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// ------------------------------------------------------------------- para

/** Kuruşu okunur kılar: 1234567 → "12.345,67 ₺". */
export function money(kurus) {
  if (kurus === null || kurus === undefined || Number.isNaN(Number(kurus))) return '—';
  return `${(Number(kurus) / 100).toLocaleString('tr-TR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })} ₺`;
}

/** Kuruşu giriş alanı için sade sayıya çevirir: 20000 → "200,00". */
export function moneyInput(kurus) {
  return (Number(kurus || 0) / 100).toFixed(2).replace('.', ',');
}

/**
 * Kullanıcının yazdığı tutarı kuruşa çevirir. Çözülemezse null.
 *
 * Kabul edilenler: `1250` · `1250,50` · `1250.50` · `1.250,00` · `1 250,00`
 *
 * NEDEN MEVCUT PANELLERDEN DAHA GENİŞ: kantin sürümü noktalı binlik ayracını
 * reddediyordu (`1.250,00` → null). Orada tutar seçilemediği için sorun
 * değildi; mağazada personel tutarı elle yazıyor ve Türkçe klavyede doğal
 * yazım tam olarak `1.250,00`. Reddetmek, doğru yazan kullanıcıyı hatalı
 * göstermek olurdu.
 *
 * Belirsizlik kuralı: hem `.` hem `,` varsa SONUNCUSU ondalık ayracıdır.
 * Yalnız `.` varsa ve `1.250` gibi tam üçlü gruplanmışsa binliktir; aksi
 * hâlde (`1250.5`) ondalıktır.
 */
export function parseMoney(text) {
  let cleaned = String(text ?? '').trim().replace(/[\s ₺]/g, '');
  if (cleaned === '') return null;

  const lastDot = cleaned.lastIndexOf('.');
  const lastComma = cleaned.lastIndexOf(',');

  if (lastDot >= 0 && lastComma >= 0) {
    const decimal = lastDot > lastComma ? '.' : ',';
    const thousands = decimal === '.' ? ',' : '.';
    cleaned = cleaned.split(thousands).join('').replace(decimal, '.');
  } else if (lastComma >= 0) {
    cleaned = cleaned.replace(',', '.');
  } else if (lastDot >= 0 && /^\d{1,3}(\.\d{3})+$/.test(cleaned)) {
    cleaned = cleaned.split('.').join('');
  }

  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  return Math.round(Number(cleaned) * 100);
}

/** Tam sayı/ondalık Türkçe biçim: 1419 → "1.419". */
export function num(value, decimals = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return Number(value).toLocaleString('tr-TR', {
    minimumFractionDigits: decimals, maximumFractionDigits: decimals,
  });
}

/** 12.5 → "%12,5". Yüzde işareti Türkçede önde durur. */
export function percent(value, decimals = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return `%${num(value, decimals)}`;
}

/** Bayt → "1,4 MB". Yedek boyutu, dosya boyutu. */
export function bytes(value) {
  const size = Number(value || 0);
  if (!size) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(size) / Math.log(1024)));
  const scaled = size / 1024 ** index;
  return `${num(scaled, index === 0 ? 0 : 1)} ${units[index]}`;
}

// ------------------------------------------------------------------ tarih

const TR_MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran',
  'Temmuz', 'Ağustos', 'Eylül', 'Ekim', 'Kasım', 'Aralık'];
const TR_DAYS = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz'];

export const monthName = (index) => TR_MONTHS[index] ?? '';
export const dayNames = () => [...TR_DAYS];

/** ISO gün (YYYY-MM-DD) → "12 Ağustos 2026 Çarşamba". */
export function longDate(iso) {
  if (!iso) return '—';
  const date = new Date(`${iso}T00:00:00`);
  return date.toLocaleDateString('tr-TR', {
    day: 'numeric', month: 'long', year: 'numeric', weekday: 'long',
  });
}

/** Yerel saate göre bugünün ISO günü — `toISOString()` UTC'ye kayar, kullanılmaz. */
export function todayIso(offsetDays = 0) {
  const now = new Date();
  now.setDate(now.getDate() + offsetDays);
  return isoOf(now);
}

export function isoOf(date) {
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** Epoch-ms → "12.08.2026 17:49". */
export function stamp(ms) {
  if (!ms) return '—';
  return new Date(Number(ms)).toLocaleString('tr-TR', {
    day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

/** ISO zaman damgası (UTC) → yerel kısa gösterim. */
export function stampIso(text) {
  if (!text) return '—';
  const parsed = new Date(text.endsWith('Z') || text.includes('+') ? text : `${text}Z`);
  return Number.isNaN(parsed.getTime()) ? text : stamp(parsed.getTime());
}

/**
 * "3 gün önce" / "5 dakika önce". Mutlak zamanı DEĞİŞTİRMEZ, tamamlar:
 * çağıran taraf ikisini birden gösterir (`title` içinde mutlak zaman).
 */
export function ago(value) {
  const ms = typeof value === 'number' ? value : Date.parse(value);
  if (!ms || Number.isNaN(ms)) return '—';
  const seconds = Math.round((Date.now() - ms) / 1000);
  if (seconds < 60) return 'az önce';
  const steps = [[60, 'dakika'], [60, 'saat'], [24, 'gün'], [7, 'hafta']];
  let amount = seconds;
  let label = 'saniye';
  for (const [factor, name] of steps) {
    if (amount < factor) break;
    amount = Math.floor(amount / factor);
    label = name;
  }
  return `${amount} ${label} önce`;
}

// ------------------------------------------------------------------ metin

/** Aksansız, küçük harfli arama anahtarı: "Öğrenci" → "ogrenci". */
export function foldText(text) {
  return String(text ?? '')
    .toLocaleLowerCase('tr')
    .replace(/ı/g, 'i').replace(/ğ/g, 'g').replace(/ü/g, 'u')
    .replace(/ş/g, 's').replace(/ö/g, 'o').replace(/ç/g, 'c');
}

/** Uzun metni kısaltır ve tamamını `title` içine koyar — bilgi kaybolmaz. */
export function clip(node, text, max = 40) {
  const full = String(text ?? '');
  node.textContent = full.length > max ? `${full.slice(0, max - 1)}…` : full;
  if (full.length > max) node.title = full;
  return node;
}

/**
 * Gecikmeli çağrı. Arama kutusunda her tuşta istek atmamak için.
 * Dönen fonksiyonun `.cancel()` metodu vardır — panel kapanırken çağrılmalı.
 */
export function debounce(fn, delay = 260) {
  let timer = null;
  const wrapped = (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), delay);
  };
  wrapped.cancel = () => window.clearTimeout(timer);
  return wrapped;
}

/** Panoya kopyalar; başarıyı bildirir. Tarayıcı reddederse sessizce false. */
export async function copyText(text) {
  try {
    await navigator.clipboard?.writeText(String(text ?? ''));
    return true;
  } catch {
    return false;
  }
}

// ------------------------------------------------------------------- CSV

/**
 * Görünen listenin anlık CSV'si. Sunucuya gitmez.
 *
 * Kalıcı çıktı (rapor PDF'i, arşiv) BÖYLE ÜRETİLMEZ: onu sidecar üretir ve
 * masaüstündeki rapor klasörüne yazar (`report.js`). Buradaki yol yalnız
 * "şu an ekranda ne varsa onu Excel'de aç" içindir.
 */
export function csvBlob(headers, rows, filename) {
  const escape = (cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`;
  const body = [headers, ...rows].map((line) => line.map(escape).join(';')).join('\r\n');
  // BOM olmadan Excel UTF-8'i tanımıyor ve 'ğ' bozuluyor.
  const blob = new Blob([`﻿${body}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = h('a');
  link.href = url;
  link.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  link.click();
  URL.revokeObjectURL(url);
  return rows.length;
}

// ----------------------------------------------------------------- düğme

/** Düğme. `variant`: '' | 'primary' | 'danger' | 'ghost'. */
export function button(label, { variant = '', title = '', onClick, disabled = false } = {}) {
  const node = h('button', `kit-btn${variant ? ` kit-btn-${variant}` : ''}`, label);
  node.type = 'button';
  if (title) node.title = title;
  if (disabled) node.disabled = true;
  if (onClick) node.addEventListener('click', onClick);
  return node;
}

/** Kısa bildirim şeridi. Aynı anda tek tane durur. */
export function toaster(root) {
  const node = h('div', 'kit-toast');
  node.setAttribute('role', 'status');
  root.append(node);
  let timer = null;

  return (message, tone = '') => {
    window.clearTimeout(timer);
    node.textContent = message;
    node.className = `kit-toast${tone ? ` ${tone}` : ''} show`;
    timer = window.setTimeout(() => { node.className = 'kit-toast'; }, 5200);
  };
}

/**
 * Onay penceresi. Yıkıcı işlemler için GEREKÇE zorunludur.
 *
 * Mağaza modüllerinde PIN teyidi yerine bu kapı kullanılır (ADR 0012).
 * Buradaki denetim ARAYÜZ tarafındaki kapıdır; asıl kapı backend'dedir —
 * gerekçe uzunluğu orada da doğrulanır ve denetim kaydına yazılır (K9).
 *
 * @returns {Promise<string|null>} gerekçe, ya da iptal edildiyse null
 */
export function confirmWithReason(root, { title, description, confirmLabel = 'Onayla',
  placeholder = 'Gerekçe (en az 3 karakter)', danger = true, minLength = 3 } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');

    card.append(h('h3', 'kit-dialog-title', title));
    if (description) card.append(h('p', 'kit-dialog-text', description));

    const input = h('input', 'kit-input');
    input.type = 'text';
    input.placeholder = placeholder;
    input.maxLength = 255;
    card.append(input);

    const error = h('div', 'kit-dialog-error');
    card.append(error);

    const actions = h('div', 'kit-dialog-actions');
    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    const submit = () => {
      const reason = input.value.trim();
      if (reason.length < minLength) {
        error.textContent = `Gerekçe en az ${minLength} karakter olmalı.`;
        input.focus();
        return;
      }
      close(reason);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter') submit();
    };

    actions.append(
      button('Vazgeç', { onClick: () => close(null) }),
      button(confirmLabel, { variant: danger ? 'danger' : 'primary', onClick: submit }),
    );
    card.append(actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    root.append(overlay);
    input.focus();
  });
}

/** Gerekçesiz basit onay. `window.confirm` kabuğun stilini taşımıyor. */
export function confirmSimple(root, { title, description, confirmLabel = 'Evet',
  danger = false } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', title));
    if (description) card.append(h('p', 'kit-dialog-text', description));

    const close = (value) => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(false);
      if (event.key === 'Enter') close(true);
    };

    const actions = h('div', 'kit-dialog-actions');
    actions.append(
      button('Vazgeç', { onClick: () => close(false) }),
      button(confirmLabel, { variant: danger ? 'danger' : 'primary', onClick: () => close(true) }),
    );
    card.append(actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(false);
    });
    document.addEventListener('keydown', onKey);
    root.append(overlay);
    card.querySelector('.kit-btn-primary, .kit-btn-danger')?.focus();
  });
}

// ------------------------------------------------------------------ stil

/** Bir stil dosyasını bir kez yükler. */
export function loadStyles(importMetaUrl, file = 'panel.css') {
  const href = new URL(`./${file}`, importMetaUrl).href;
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = h('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.append(link);
}

// Kitin KENDİ stili import anında yüklenir: paylaşılan tek kopya olduğu için
// her panelin ayrıca yüklemesine gerek yok ve çift yükleme engellenir.
// Panelin KENDİ `panel.css` dosyası ise `mount()` içinde yüklenmelidir —
// dosya tepesinde yüklenirse, kabuk yetenek çözümü sırasında hiç açılmayan
// panelleri de import ettiği için kullanılmayan stiller head'e sızar.
loadStyles(import.meta.url, 'kit.css');
