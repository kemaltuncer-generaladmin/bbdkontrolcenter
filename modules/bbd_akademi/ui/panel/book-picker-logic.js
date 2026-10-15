// BBD Akademi — ortak kitap seçicisinin SAF mantığı (Akademi README §14 madde 98).
//
// React `features/academic/common/book-picker.ts` ve Flutter `book_models.dart` ile
// harfi harfine aynı metinler; DOM'suz (Node testinde doğrudan yüklenir).

export const T = {
  title: 'Kitap seç',
  search: 'Kitap adı, yayınevi ya da ISBN',
  targetOne: 'Bu öğrencinin kitapları',
  targetMany: 'Seçilen öğrencilerin kitapları',
  targetClass: 'Sınıfın kitapları',
  recent: 'Son kullanılanlar',
  all: 'Tüm kitaplar',
  grade: 'Sınıf',
  exam: 'Sınav',
  subject: 'Ders',
  publisher: 'Yayınevi',
  series: 'Seri',
  any: 'Tümü',
  inStock: 'Yalnız satışta olanlar',
  clear: 'Süzgeçleri temizle',
  emptyTitle: 'Bu süzgeçte kitap yok',
  emptyMessage: 'Aramayı ya da süzgeçleri değiştir.',
  removed: 'Mağazadan kalktı',
  outOfStock: 'Stokta yok',
  more: 'Daha fazla',
  chooseFromCatalog: 'Katalogdan kitap ekle',
  syncTitle: 'Mağaza eşitlemesi',
  never: 'Henüz eşitlenmedi; ilk eşitleme saat başında.',
  syncNow: 'Şimdi eşitle',
  syncQueued: 'Eşitleme başladı; birkaç saniye içinde güncellenir.',
};

export const count = (n) => `${Number(n || 0).toLocaleString('tr-TR')} kitap`;
export const onShelf = (n, total) => (total > 1 ? `${n}/${total} öğrencide` : 'Rafında');
export const pickLabel = (n) => (n ? `${n} kitabı seç` : 'Kitap seç');
export const lastSync = (when, n) => `Son eşitleme ${when} · ${Number(n || 0).toLocaleString('tr-TR')} kitap`;
export const syncFailed = (code) => `Son deneme başarısız (${code}); saat başında yeniden denenir.`;
export const gradeLabel = (v) => (v === 'graduate' ? 'Mezun' : `${v}. sınıf`);

export const EMPTY_FILTERS = Object.freeze({ q: '', gradeLevel: '', exam: '', subject: '', publisher: '', series: '', inStock: false });

export function hasFilters(f) {
  return Boolean(f.q || f.gradeLevel || f.exam || f.subject || f.publisher || f.series || f.inStock);
}

/** `catalog_searchLibrary` sorgusu (boş alanlar gönderilmez). */
export function libraryQuery(f, extra = {}) {
  const out = { scope: 'all', ...extra };
  const q = String(f.q || '').trim();
  if (q) {
    const digits = q.replace(/[\s-]/g, '');
    if (/^\d{10}(\d{3})?$/.test(digits)) out.isbn = digits;
    else out.q = q;
  }
  for (const key of ['gradeLevel', 'exam', 'subject', 'publisher', 'series']) if (f[key]) out[key] = f[key];
  if (f.inStock) out.inStock = true;
  return out;
}

/** "Yayınevi · 8. sınıf · LGS". */
export function bookMeta(r) {
  const grades = (r.gradeLevels || []).map(gradeLabel);
  return [r.publisher, grades.length > 2 ? `${grades[0]}–${grades[grades.length - 1]}` : grades.join(', '),
    (r.exams || []).join('/')].filter(Boolean).join(' · ');
}

export function storeBadge(r) {
  if (!r.store) return null;
  if (r.store.status === 'removed') return T.removed;
  return r.store.inStock ? null : T.outOfStock;
}

export const RECENT_KEY = 'bbd.recentBooks.v1';
const RECENT_MAX = 12;

/** Son kullanılanlar: seçilenler öne, tekil, en çok 12 (kişisel veri yok; yalnız kaynak özetleri). */
export function mergeRecent(picked, previous) {
  const seen = new Set(picked.map((r) => r.id));
  return [...picked, ...(previous || []).filter((r) => r && r.id && !seen.has(r.id))].slice(0, RECENT_MAX);
}

export function readRecent(storage = globalThis.localStorage) {
  try {
    const list = JSON.parse(storage?.getItem(RECENT_KEY) || '[]');
    return Array.isArray(list) ? list.filter((r) => r && typeof r.id === 'string').slice(0, RECENT_MAX) : [];
  } catch { return []; }
}

export function rememberRecent(picked, storage = globalThis.localStorage) {
  try { storage?.setItem(RECENT_KEY, JSON.stringify(mergeRecent(picked, readRecent(storage)))); } catch { /* özel kip */ }
}
