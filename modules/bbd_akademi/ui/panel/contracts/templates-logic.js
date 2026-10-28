// BBD Akademi — Sözleşme madde şablonu düzenleyicisinin SAF modeli (A20–A22).
//
// İMPORT'SUZ ve DOM'suz: `.mjs` kopyası Node'da sınanır (`tests/test_akademi_sozlesme_belge.py`).
// Bir tür = `ContractClause[]`; madde = `{title: string|null, items: string[]}` (belgede "n. BAŞLIK:" + öğeler; öğe
// kendi "a)" önekini taşıyabilir). Bütün işlemler DEĞİŞMEZDİR (yeni dizi döner) — düzenleyici geri al / karşılaştır
// için eski hali tutabilir. Yer tutucular `{kurumAdi}` biçimindedir; yeni sözleşme şablonu kopyalarken çözülür.

export const TEMPLATE_KINDS = ['registration', 'meb', 'promissory', 'legal_notice'];
export const MAX_ITEM_LENGTH = 4000;
export const MAX_TITLE_LENGTH = 200;
export const MAX_CLAUSES = 200;

const TOKEN = /\{([A-Za-z][A-Za-z0-9_]*)\}/g;

/** Sunucudaki maddeler → düzenleyici modeli (başlık metin, en az bir öğe). */
export function cloneClauses(clauses) {
  return (Array.isArray(clauses) ? clauses : []).map((clause) => ({
    title: clause?.title ?? '',
    items: Array.isArray(clause?.items) && clause.items.length ? clause.items.map((item) => String(item ?? '')) : [''],
  }));
}

const copy = (clauses) => clauses.map((clause) => ({ title: clause.title, items: [...clause.items] }));
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export function addClause(clauses, at = clauses.length) {
  const out = copy(clauses);
  out.splice(clamp(at, 0, out.length), 0, { title: '', items: [''] });
  return out;
}

export function removeClause(clauses, index) {
  return copy(clauses).filter((_, position) => position !== index);
}

export function moveClause(clauses, index, delta) {
  const target = index + delta;
  if (index < 0 || index >= clauses.length || target < 0 || target >= clauses.length) return copy(clauses);
  const out = copy(clauses);
  const [item] = out.splice(index, 1);
  out.splice(target, 0, item);
  return out;
}

export function setTitle(clauses, index, title) {
  const out = copy(clauses);
  if (out[index]) out[index].title = String(title ?? '');
  return out;
}

export function addItem(clauses, index, at) {
  const out = copy(clauses);
  const clause = out[index];
  if (!clause) return out;
  clause.items.splice(clamp(at ?? clause.items.length, 0, clause.items.length), 0, '');
  return out;
}

export function setItem(clauses, index, itemIndex, text) {
  const out = copy(clauses);
  if (out[index] && itemIndex >= 0 && itemIndex < out[index].items.length) out[index].items[itemIndex] = String(text ?? '');
  return out;
}

/** Öğeyi siler; maddenin son öğesi silinirse madde boş bir öğeyle kalır (maddeyi silmek ayrı işlem). */
export function removeItem(clauses, index, itemIndex) {
  const out = copy(clauses);
  const clause = out[index];
  if (!clause) return out;
  clause.items.splice(itemIndex, 1);
  if (!clause.items.length) clause.items.push('');
  return out;
}

export function moveItem(clauses, index, itemIndex, delta) {
  const out = copy(clauses);
  const items = out[index]?.items;
  const target = itemIndex + delta;
  if (!items || itemIndex < 0 || itemIndex >= items.length || target < 0 || target >= items.length) return out;
  const [item] = items.splice(itemIndex, 1);
  items.splice(target, 0, item);
  return out;
}

/** Metne imleç konumunda yer tutucu ekler: `{text, caret}` (`key` süslü parantezli ya da yalın). */
export function insertToken(text, start, end, key) {
  const source = String(text ?? '');
  const token = `{${String(key ?? '').replace(/^\{|\}$/g, '')}}`;
  const from = clamp(Number.isInteger(start) ? start : source.length, 0, source.length);
  const to = clamp(Number.isInteger(end) ? end : from, from, source.length);
  return { text: source.slice(0, from) + token + source.slice(to), caret: from + token.length };
}

/** Kaydedilecek biçim: kırpılmış; boş öğe atılır; öğesi ve başlığı olmayan madde atılır; boş başlık null. */
export function normalizeClauses(clauses) {
  return (Array.isArray(clauses) ? clauses : [])
    .map((clause) => ({ title: String(clause?.title ?? '').trim() || null,
      items: (clause?.items || []).map((item) => String(item ?? '').trim()).filter(Boolean) }))
    .filter((clause) => clause.items.length || clause.title);
}

export function clausesEqual(left, right) {
  return JSON.stringify(normalizeClauses(left)) === JSON.stringify(normalizeClauses(right));
}

/** Metindeki yer tutucu anahtarları (sırayla, tekil). */
export function tokensOf(text) {
  const out = [];
  for (const match of String(text ?? '').matchAll(TOKEN)) if (!out.includes(match[1])) out.push(match[1]);
  return out;
}

const keyOf = (placeholder) => String(placeholder?.key ?? '').replace(/^\{|\}$/g, '');

/** Katalogda (A20 `placeholders`) olmayan yer tutucular — yazım hatası uyarısı. */
export function unknownPlaceholders(clauses, placeholders) {
  const known = new Set((placeholders || []).map(keyOf));
  const used = new Set();
  for (const clause of normalizeClauses(clauses)) {
    for (const text of [clause.title || '', ...clause.items]) for (const key of tokensOf(text)) used.add(key);
  }
  return [...used].filter((key) => !known.has(key)).sort();
}

/**
 * Kaydetmeden önce doğrulama. Dönüş: hata metinleri (boş = geçerli). Maddeler 1'den numaralanır.
 */
export function validateClauses(clauses) {
  const errors = [];
  const normalized = normalizeClauses(clauses);
  if (!normalized.length) errors.push('Şablonda en az bir madde olmalı.');
  if (normalized.length > MAX_CLAUSES) errors.push(`En çok ${MAX_CLAUSES} madde olabilir.`);
  normalized.forEach((clause, index) => {
    const no = index + 1;
    if (!clause.items.length) errors.push(`${no}. maddede metin yok (başlığın altına en az bir öğe yazın).`);
    if (clause.title && clause.title.length > MAX_TITLE_LENGTH) {
      errors.push(`${no}. maddenin başlığı çok uzun (en çok ${MAX_TITLE_LENGTH} karakter).`);
    }
    clause.items.forEach((item, itemIndex) => {
      if (item.length > MAX_ITEM_LENGTH) {
        errors.push(`${no}. maddenin ${itemIndex + 1}. öğesi çok uzun (en çok ${MAX_ITEM_LENGTH} karakter).`);
      }
    });
  });
  return errors;
}

/** Yer tutucuyu örnek değeriyle doldurur (önizleme); bilinmeyen yer tutucu olduğu gibi kalır. */
export function resolveText(text, placeholders) {
  const map = new Map((placeholders || []).map((item) => [keyOf(item), item?.example ?? item?.label ?? '']));
  return String(text ?? '').replace(TOKEN, (whole, key) => (map.has(key) ? String(map.get(key)) : whole));
}

/** Canlı önizleme modeli: `[{no, title, items}]` — başlık Türkçe büyük harfle, yer tutucular örnekle. */
export function previewModel(clauses, placeholders) {
  return normalizeClauses(clauses).map((clause, index) => ({
    no: index + 1,
    title: clause.title ? resolveText(clause.title, placeholders).toLocaleUpperCase('tr-TR') : null,
    items: clause.items.map((item) => resolveText(item, placeholders)),
  }));
}

/** Kaç madde / öğe değişti (kaydet şeridi metni). */
export function changeSummary(before, after) {
  const left = normalizeClauses(before);
  const right = normalizeClauses(after);
  if (JSON.stringify(left) === JSON.stringify(right)) return '';
  const count = (list) => list.reduce((sum, clause) => sum + clause.items.length, 0);
  return `${right.length} madde, ${count(right)} öğe (önce ${left.length} madde, ${count(left)} öğe)`;
}
