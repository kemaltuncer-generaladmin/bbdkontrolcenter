// Panel araç kutusu — DOM yardımcıları ve biçimlendirme.
//
// Modül modülü import etmez (K3); bu dosya modülün KENDİ kopyasıdır.
// Kabuk bir bileşen kitaplığı sunmaz, tasarım dili CSS değişkenleriyle taşınır.

/** Tek satırda element: h('div', 'sinif', 'metin'). */
export const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

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

/** "200,00" / "200.5" / "200" → kuruş. Çözülemezse null. */
export function parseMoney(text) {
  const cleaned = String(text ?? '').trim().replace(/\s|₺/g, '').replace(',', '.');
  if (cleaned === '' || !/^\d+(\.\d{1,2})?$/.test(cleaned)) return null;
  return Math.round(Number(cleaned) * 100);
}

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

/** Aksansız, küçük harfli arama anahtarı: "Öğrenci" → "ogrenci". */
export function foldText(text) {
  return String(text ?? '')
    .toLocaleLowerCase('tr')
    .replace(/ı/g, 'i').replace(/ğ/g, 'g').replace(/ü/g, 'u')
    .replace(/ş/g, 's').replace(/ö/g, 'o').replace(/ç/g, 'c');
}

/** Düğme. `variant`: '' | 'primary' | 'danger' | 'ghost'. */
export function button(label, { variant = '', title = '', onClick } = {}) {
  const node = h('button', `kit-btn${variant ? ` kit-btn-${variant}` : ''}`, label);
  node.type = 'button';
  if (title) node.title = title;
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
 * Çekirdek henüz "yıkıcı işlemde PIN teyidi" mekanizmasını uygulamıyor
 * (manifestte ilan var, kodda karşılığı yok). Bu yüzden buradaki teyit
 * arayüz tarafındaki kapıdır; asıl kapı backend'deki izin denetimidir (K9).
 *
 * @returns {Promise<string|null>} gerekçe, ya da iptal edildiyse null
 */
export function confirmWithReason(root, { title, description, confirmLabel = 'Onayla',
  placeholder = 'Gerekçe (en az 3 karakter)', danger = true } = {}) {
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
      if (reason.length < 3) {
        error.textContent = 'Gerekçe en az 3 karakter olmalı.';
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

/** Panelin kendi stil dosyasını bir kez yükler. */
export function loadStyles(importMetaUrl, file = 'panel.css') {
  const href = new URL(`./${file}`, importMetaUrl).href;
  if (document.querySelector(`link[href="${href}"]`)) return;
  const link = h('link');
  link.rel = 'stylesheet';
  link.href = href;
  document.head.append(link);
}
