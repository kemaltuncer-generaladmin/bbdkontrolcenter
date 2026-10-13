// Veli listesi — saf model, DOM bilmez (Node testi:
// tests/test_bbd_students_veli_arayuz.py).
//
// Sıra ÖNCELİKTİR; ilk veli BİRİNCİLDİR (yuva 1: telefonu kantinde, veli
// kantin kontrolleri ve uygulama girişi bununla). Kural backend'dedir
// (`backend/guardians.py`); burası ekranın aynı kuralı ÖNCEDEN göstermesi
// içindir — asıl kapı backend'dir.

export const MAX_GUARDIANS = 4;

export const RELATIONS = [
  ['', 'Yakınlık seçin'],
  ['mother', 'Anne'],
  ['father', 'Baba'],
  ['guardian', 'Vasi'],
  ['grandparent', 'Büyükanne / büyükbaba'],
  ['sibling', 'Kardeş'],
  ['other', 'Diğer'],
];

const RELATION_LABEL = Object.fromEntries(RELATIONS.filter(([value]) => value));

export function relationLabel(value) {
  return RELATION_LABEL[value] || '';
}

/** "+90 (532) 111-22-33" → "5321112233"; cep değilse null. Backend `normalize_phone` ile aynı. */
export function cleanPhone(input) {
  let digits = String(input ?? '').replace(/\D/g, '');
  if (!digits) return null;
  if (digits.startsWith('0090')) digits = digits.slice(4);
  else if (digits.startsWith('90') && digits.length === 12) digits = digits.slice(2);
  else if (digits.startsWith('0') && digits.length === 11) digits = digits.slice(1);
  return digits.length === 10 && digits.startsWith('5') ? digits : null;
}

/** "5321112233" → "+90 532 111 22 33"; geçersizi olduğu gibi bırakır. */
export function showPhone(input) {
  const value = cleanPhone(input);
  if (!value) return String(input ?? '');
  return `+90 ${value.slice(0, 3)} ${value.slice(3, 6)} ${value.slice(6, 8)} ${value.slice(8)}`;
}

/** Sunucunun listesi → düzenlenebilir satırlar. */
export function fromServer(items) {
  return (Array.isArray(items) ? items : [])
    .slice()
    .sort((a, b) => Number(a.slot) - Number(b.slot))
    .map((item) => ({ name: String(item.name || ''), phone: String(item.phone || ''),
      relation: String(item.relation || ''), siblings: item.siblings || [] }));
}

/** Satır hataları: `[{index, field, message}]` (boş = geçerli). */
export function validate(rows) {
  const errors = [];
  if (rows.length > MAX_GUARDIANS) {
    errors.push({ index: null, field: null, message: `En çok ${MAX_GUARDIANS} veli eklenebilir.` });
  }
  const seen = new Map();
  rows.forEach((row, index) => {
    const order = index + 1;
    if (!String(row.phone || '').trim()) {
      errors.push({ index, field: 'phone', message: `${order}. velinin telefonu boş.` });
      return;
    }
    const phone = cleanPhone(row.phone);
    if (!phone) {
      errors.push({ index, field: 'phone',
        message: `${order}. velinin telefonu geçersiz — cep numarası olmalı (+90 5xx xxx xx xx).` });
      return;
    }
    if (seen.has(phone)) {
      errors.push({ index, field: 'phone', message: `${order}. velinin telefonu ${seen.get(phone)}. veliyle aynı.` });
      return;
    }
    seen.set(phone, order);
    if (String(row.name || '').length > 80) {
      errors.push({ index, field: 'name', message: `${order}. velinin adı en çok 80 karakter olabilir.` });
    }
  });
  return errors;
}

/** Gönderilecek gövde (sıra korunur). */
export function toPayload(rows) {
  return { items: rows.map((row) => ({ name: String(row.name || '').trim().replace(/\s+/g, ' '),
    phone: cleanPhone(row.phone) || String(row.phone || '').trim(), relation: row.relation || '' })) };
}

/** İki liste arasında gönderilecek bir fark var mı? */
export function changed(before, after) {
  const key = (rows) => JSON.stringify(toPayload(rows).items);
  return key(before) !== key(after);
}

export function add(rows) {
  if (rows.length >= MAX_GUARDIANS) return rows;
  return [...rows, { name: '', phone: '', relation: '', siblings: [] }];
}

export function remove(rows, index) {
  return rows.filter((_, position) => position !== index);
}

/** `index`'teki veliyi birincil yapar (başa alır; diğerlerinin sırası korunur). */
export function makePrimary(rows, index) {
  if (index <= 0 || index >= rows.length) return rows;
  return [rows[index], ...rows.filter((_, position) => position !== index)];
}

/** Birincil değişince kullanıcıya söylenecek cümle (yoksa ''). */
export function primaryNote(before, after) {
  const was = cleanPhone(before[0]?.phone);
  const now = cleanPhone(after[0]?.phone);
  if (was === now) return '';
  if (!now) return 'Birincil veli kalmadı: kantin kartındaki veli telefonu boşalır.';
  return 'Birincil veli değişti: kantin kaydındaki veli telefonu yeni birincil veliye geçer.';
}
