// BBD Akademi — "BBD Store'dan toplu ata"nın SAF mantığı (Akademi README §14 madde 116).
//
// DOM'suz (Node testinde doğrudan yüklenir). React `features/admin/resources/bulk-model.ts`
// ile aynı metinler ve aynı seçim modeli:
//
//   · tek tek seçilen ürünler (`picked`)
//   · "Süzülenlerin tümü" (`all` = o anki süzgecin kopyası + toplamı) — tek tek
//     kaldırılanlar `excluded`'a düşer; sunucu süzgeci kendisi çözer (`allMatching`).

export const T = {
  title: "BBD Store'dan toplu kaynak ata",
  open: "BBD Store'dan toplu ata",
  steps: ['Kitaplar', 'Kime', 'Önizle ve ata'],
  search: 'Kitap adı, yayınevi, yazar ya da ISBN',
  subject: 'Ders', grade: 'Sınıf', exam: 'Sınav', publisher: 'Yayınevi', kind: 'Tür', any: 'Tümü',
  inStock: 'Yalnız stokta olanlar',
  free: 'Yalnız ücretsiz',
  clear: 'Süzgeçleri temizle',
  selectAll: (n) => `Süzülenlerin tümünü seç (${fmt(n)})`,
  unselectAll: 'Tümünün seçimini kaldır',
  clearSelection: 'Seçimi temizle',
  more: 'Daha fazla',
  emptyTitle: 'Bu süzgeçte kitap yok',
  emptyText: 'Aramayı ya da süzgeçleri değiştirin.',
  neverSynced: 'BBD Store kataloğu henüz eşitlenmedi; ilk eşitleme saat başında. Kitap satışı (books) açık olmalı.',
  freeBadge: 'Ücretsiz',
  outOfStock: 'Stokta yok',
  removed: 'Mağazadan kalktı',
  freeNote: 'Atama ücretsizdir: öğrenciye / veliye sipariş, borç ya da ödeme kaydı oluşmaz.',
  noBooks: 'En az bir kitap seçin.',
  finish: 'Ücretsiz ata',
  notify: 'Rafına kitap düşen öğrencilere bildirim gönder',
  targetDate: 'Hedef bitiş günü (isteğe bağlı)',
  batches: 'Toplu atamalar',
  undo: 'Geri al',
  showAssignments: 'Atamaları gör',
  undone: 'Geri alındı',
};

export const KIND_LABELS = {
  topic_book: 'Konu anlatımı', question_bank: 'Soru bankası', fascicle: 'Fasikül', practice_exam: 'Deneme', other: 'Diğer',
};

/** En çok kaynak (sunucu `MAX_RESOURCES`). */
export const MAX_RESOURCES = 1500;

export const EMPTY_FILTERS = Object.freeze({
  q: '', gradeLevel: '', exam: '', subject: '', publisher: '', kind: '', inStock: false, free: false,
});

export function fmt(n) {
  return Number(n || 0).toLocaleString('tr-TR');
}

export function hasFilters(f) {
  return Boolean(f.q || f.gradeLevel || f.exam || f.subject || f.publisher || f.kind || f.inStock || f.free);
}

/** `admin_searchStoreCatalog` sorgusu (boş alanlar gönderilmez). */
export function catalogQuery(f, extra = {}) {
  const out = {};
  const q = String(f.q || '').trim();
  if (q) out.q = q;
  for (const key of ['gradeLevel', 'exam', 'subject', 'publisher', 'kind']) if (f[key]) out[key] = String(f[key]);
  if (f.inStock) out.inStock = true;
  if (f.free) out.free = true;
  return { ...out, ...extra };
}

/** Sözleşmenin `StoreCatalogFilter` gövdesi ("süzülenlerin tümü"). */
export function filterBody(f) {
  const q = String(f.q || '').trim();
  return {
    q: q || null,
    gradeLevel: f.gradeLevel || null,
    exam: f.exam || null,
    subject: f.subject || null,
    publisher: f.publisher || null,
    kind: f.kind || null,
    inStock: Boolean(f.inStock),
    free: Boolean(f.free),
  };
}

export function sameFilters(a, b) {
  return JSON.stringify(catalogQuery(a || EMPTY_FILTERS)) === JSON.stringify(catalogQuery(b || EMPTY_FILTERS));
}

/** Seçim modeli. `initial`: önceden seçili kaynaklar ([{id, title}], ör. Kütüphane'den). */
export function selection({ resources = [] } = {}) {
  const picked = new Map();        // productId → ürün
  const extra = new Map(resources.map((item) => [item.id, item]));   // kaynak kimliği → özet
  let all = null;                  // {filters, total}
  const excluded = new Set();
  return {
    get all() { return all; },
    get picked() { return picked; },
    get resources() { return extra; },
    /** Satır işaretli mi (o anki süzgeç "tümü" kopyasıyla aynıysa hariç tutulmamış her satır). */
    isOn(item, filters) {
      if (all && sameFilters(all.filters, filters)) return !excluded.has(item.productId);
      return picked.has(item.productId);
    },
    toggle(item, filters) {
      if (all && sameFilters(all.filters, filters)) {
        if (excluded.has(item.productId)) excluded.delete(item.productId); else excluded.add(item.productId);
        return;
      }
      if (picked.has(item.productId)) picked.delete(item.productId); else picked.set(item.productId, item);
    },
    selectAll(filters, total) { all = { filters: { ...filters }, total: Number(total) || 0 }; excluded.clear(); },
    clearAll() { all = null; excluded.clear(); },
    clear() { all = null; excluded.clear(); picked.clear(); extra.clear(); },
    removeResource(id) { extra.delete(id); },
    /** Yaklaşık sayı (kesini önizleme verir; "tümü" ile tek tek seçilenler örtüşebilir). */
    count() {
      return (all ? Math.max(0, all.total - excluded.size) : 0) + picked.size + extra.size;
    },
    empty() { return this.count() === 0; },
    /** Sözleşme gövdesinin kaynak kısmı. */
    body() {
      return {
        productIds: [...picked.keys()].map(String),
        resourceIds: [...extra.keys()],
        allMatching: all ? filterBody(all.filters) : null,
        excludeProductIds: all ? [...excluded].map(String) : [],
      };
    },
  };
}

/** "12 kitap seçildi" — "tümü" varsa süzgeç özetiyle. */
export function selectionText(sel) {
  const n = sel.count();
  if (!n) return 'Henüz kitap seçilmedi.';
  const parts = [`${fmt(n)} kitap seçildi`];
  if (sel.all) parts.push('süzgecin tümü');
  if (n > MAX_RESOURCES) parts.push(`en çok ${fmt(MAX_RESOURCES)} kitap atanabilir`);
  return parts.join(' · ');
}

/** Ürün satırının alt bilgisi: yayınevi · tür · sınıf / sınav. */
export function itemMeta(item) {
  const grades = (item.gradeLevels || []).map((g) => (g === 'graduate' ? 'Mezun' : `${g}. sınıf`));
  return [item.publisher, KIND_LABELS[item.kind] || '', [...grades, ...(item.exams || [])].join(', ')]
    .filter(Boolean).join(' · ');
}

/** Önizleme özeti (sırayla gösterilen satırlar). */
export function previewLines(p) {
  if (!p) return [];
  const lines = [
    `${fmt(p.resourceCount)} kitap × ${fmt(p.studentCount)} öğrenci → ${fmt(p.newCount)} yeni raf kaydı.`,
  ];
  if (p.alreadyCount) lines.push(`${fmt(p.alreadyCount)} zaten rafında; yeniden atanmaz.`);
  const removed = (p.skipped || []).filter((s) => s.reason === 'removed');
  const missing = (p.skipped || []).filter((s) => s.reason !== 'removed');
  if (removed.length) {
    lines.push(`${fmt(removed.length)} ürün mağazadan kalkmış; atanmayacak: `
      + `${removed.slice(0, 3).map((s) => s.title).filter(Boolean).join(', ')}${removed.length > 3 ? '…' : ''}`);
  }
  if (missing.length) lines.push(`${fmt(missing.length)} seçim bulunamadı ya da arşivlenmiş; atlanacak.`);
  if (p.outOfStockCount) lines.push(`${fmt(p.outOfStockCount)} kitap şu an stokta yok (yine de atanır; rafta görünür).`);
  if (p.inline === false) lines.push('Büyük atama: arka planda yapılır, ilerleme bu pencerede görünür.');
  lines.push(T.freeNote);
  return lines;
}

/** Sonuç bildirimi. */
export function resultText(r) {
  if (!r) return 'Toplu atama tamamlandı.';
  if (!r.createdCount) return 'Seçilen kitaplar bu öğrencilerin rafında zaten vardı; yeni atama yapılmadı.';
  const parts = [`${fmt(r.newCount)} kitap ${fmt(r.studentCount)} öğrencinin rafına eklendi`];
  if (r.alreadyCount) parts.push(`${fmt(r.alreadyCount)} zaten vardı`);
  if (r.notifiedCount) parts.push(`${fmt(r.notifiedCount)} bildirim`);
  return `${parts.join(' · ')}.`;
}

/** Hedefteki sınıfların ortak seviyesi ("Seviyeye uygun öner"); yoksa ''. */
export function commonGrade(classes, classGroupIds) {
  const levels = new Set();
  for (const id of classGroupIds || []) {
    const item = (classes || []).find((c) => c.id === id);
    if (item && item.gradeLevel !== null && item.gradeLevel !== undefined) levels.add(String(item.gradeLevel));
  }
  return levels.size === 1 ? [...levels][0] : '';
}
