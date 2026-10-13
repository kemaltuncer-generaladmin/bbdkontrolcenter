// Hızlı güncelleme — saf model (DOM'suz, import'suz; Node'da sınanır).
//
// KULLANICI İSTEĞİ (13.10.2026): "Adam bir öğrenciyi güncellemek için
// saatlerce uğraşıp durmasın." Öğrenci kartında her alan SATIR İÇİ düzenlenir:
// tıkla → yaz → Enter (ya da alandan çık) → o alan HEMEN kaydedilir; "Geri al"
// eski değeri geri yazar. Esc değişikliği bırakır. Hedef: tek alan ≤ 3
// etkileşim, < 1 sn.
//
// Bu dosya kuralı tutar: alanın sunucuya giden değeri ve hata cümlesi
// (`inlineValue`), geri alma yükü (`undoPatch`), Akademi'ye ulaşma durumu
// (`deliveryState`). Ekran `record.js`'tedir.

/** Satır içi düzenlenen kayıt alanları ve Türkçe adları (sıra = sekme sırası). */
export const INLINE_FIELDS = [
  ['firstName', 'Ad'], ['lastName', 'Soyad'], ['schoolNo', 'Okul No'], ['studentPhone', 'Öğrenci telefonu'],
  ['className', 'Ana sınıf'], ['gradeLevel', 'Seviye'], ['track', 'Alan'], ['examTarget', 'Hedef sınav'],
  ['note', 'Not'],
];

const LABELS = Object.fromEntries(INLINE_FIELDS);

export function fieldLabel(key) {
  return LABELS[key] || key;
}

function digits(text) {
  let value = String(text ?? '').replace(/\D/g, '');
  if (value.startsWith('0090')) value = value.slice(4);
  else if (value.startsWith('90') && value.length === 12) value = value.slice(2);
  else if (value.startsWith('0') && value.length === 11) value = value.slice(1);
  return value;
}

/** "11a" → "11-A" (student.js `normalizeClass` ile aynı kural). */
export function normalizeClassName(input) {
  const raw = String(input ?? '').trim().toLocaleUpperCase('tr');
  if (raw === '') return '';
  const match = raw.match(/^(\d{1,2})\s*[-/.]?\s*([A-ZÇĞİÖŞÜ]?)$/);
  if (!match) return raw.slice(0, 20);
  return match[2] ? `${match[1]}-${match[2]}` : match[1];
}

/**
 * Ekrandaki metin → sunucuya gidecek değer ya da hata.
 * @returns {{value: any} | {error: string}}
 */
export function inlineValue(key, raw) {
  const text = String(raw ?? '');
  switch (key) {
    case 'firstName':
    case 'lastName': {
      const value = text.replace(/\s+/g, ' ').trim();
      if (!value) return { error: `${fieldLabel(key)} boş bırakılamaz.` };
      if (value.length > 60) return { error: `${fieldLabel(key)} en çok 60 karakter.` };
      return { value };
    }
    case 'studentPhone': {
      if (!text.trim()) return { value: '' };
      const value = digits(text);
      if (value.length !== 10 || !value.startsWith('5')) {
        return { error: 'Öğrenci telefonu cep numarası olmalı (+90 5xx xxx xx xx).' };
      }
      return { value };
    }
    case 'className':
      return { value: normalizeClassName(text) };
    case 'schoolNo':
      return text.trim().length > 20 ? { error: 'Okul no en çok 20 karakter.' } : { value: text.trim() };
    case 'note':
      return text.length > 500 ? { error: 'Not en çok 500 karakter.' } : { value: text.trim() };
    case 'gradeLevel': {
      if (text === '' || text === null) return { value: null };
      if (text === 'graduate') return { value: 'graduate' };
      const value = Number(text);
      return Number.isInteger(value) && value >= 5 && value <= 12 ? { value } : { error: 'Seviye 5–12 ya da mezun.' };
    }
    case 'track':
    case 'examTarget':
      return { value: text === '' ? null : text };
    default:
      return { value: text };
  }
}

/** Değer değişti mi (boş/null/undefined eşittir; sayı/metin eşittir). */
export function sameValue(a, b) {
  const norm = (value) => (value === null || value === undefined ? '' : String(value));
  return norm(a) === norm(b);
}

/**
 * Tek alanın PATCH gövdesi. Ad ya da soyad değişince ikisi birlikte gider
 * (kantindeki görünen ad ikisinden kurulur).
 */
export function patchFor(student, key, value) {
  const body = { [key]: value };
  if (key === 'firstName') body.lastName = student.lastName ?? '';
  if (key === 'lastName') body.firstName = student.firstName ?? '';
  return body;
}

/** "Geri al": alanın kayıttan ÖNCEKİ değeri (aynı kurala göre). */
export function undoPatch(before, key) {
  const old = before[key] ?? (['gradeLevel', 'track', 'examTarget'].includes(key) ? null : '');
  return patchFor(before, key, old);
}

/**
 * Akademi'ye ulaştı mı? Yazmanın outbox sırası (`seq`, yazmadan hemen sonra
 * okunan `lastSeq`) ile geçidin teslim imleci (`status.deliveredSeq`)
 * karşılaştırılır (km-merkezi-senkron §8, Ayarlar → Senkron kartıyla aynı veri).
 *
 * @returns {{state: 'delivered'|'pending'|'late'|'off'|'unknown', text: string}}
 */
export function deliveryState({ seq, status, elapsedMs = 0 }) {
  if (!status || status.failed || seq === null || seq === undefined) {
    return { state: 'unknown', text: '' };
  }
  if (status.enabled === false) {
    return { state: 'off', text: 'Akademi iletimi kapalı (Ayarlar → Senkron).' };
  }
  const delivered = Number(status.deliveredSeq ?? -1);
  if (delivered >= Number(seq)) {
    const seconds = (Math.max(0, elapsedMs) / 1000).toLocaleString('tr-TR', { maximumFractionDigits: 1 });
    return { state: 'delivered', text: `Akademi'ye ulaştı ✓ (${seconds} sn)` };
  }
  if (elapsedMs > 10_000) {
    return { state: 'late', text: 'Akademi\'ye henüz ulaşmadı — iletim sürüyor, Ayarlar → Senkron\'dan izlenebilir.' };
  }
  return { state: 'pending', text: 'Akademi\'ye iletiliyor…' };
}

/** Klavye kısayolu: yazı alanında değilken `e` düzenlemeyi açar. */
export function shortcut(event, typing) {
  if (event.ctrlKey || event.altKey || event.metaKey) return null;
  if (event.key === 'Escape') return 'escape';
  if (typing) return null;
  if (event.key === 'e' || event.key === 'E') return 'edit';
  return null;
}
