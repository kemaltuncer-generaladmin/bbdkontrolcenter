// BBD Akademi — "Sözleşmeler" ekranının SAF mantığı (etiketler, süzgeç → sorgu, "Belge çıkar" isteği kurucusu).
//
// Bu dosya bilerek İMPORT'SUZ ve DOM'suzdur: `.mjs` kopyası Node'da yüklenip sınanır
// (`tests/test_akademi_sozlesme_belge.py`). Etiketler Akademi sözleşmesindeki (80-api-taslak) metinlerle AYNIDIR;
// web ve mobil aynı metni gösterir, PDF de.
//
// İSTEK KURUCUSU (A17 `admin_buildContractDocuments`): seçili satırlar → `contractIds`; "süzgecin tamamı" →
// Akademi'nin anladığı süzgeç (`classId`, `signature`, `overdueOnly`) varsa `filter`, ekranda Akademi'nin toplu
// süzgecinde OLMAYAN bir koşul (arama, eğitim yılı, iptal) varsa liste sayfalanıp `contractIds`'e çevrilir —
// basılan küme her zaman ekranda görülen kümedir.

/** Sözleşmeyi değiştiren KM ekranlarının yayınladığı pencere olayı (Akademi SSE'si entegrasyon anahtarını almaz). */
export const CONTRACT_CHANGED_EVENT = 'bbd-akademi:contract-changed';

/** "Kayıt ve ödeme planı" sihirbazını açan pencere olayı (`{studentId, planId?}`; sihirbazı 80-km-sihirbaz yazar). */
export const OPEN_WIZARD_EVENT = 'bbd-akademi:open-contract-wizard';

/** ≤ bu kadar öğrenci senkron üretilir; fazlası iş olarak sıraya girer (A17/A18). */
export const SYNC_LIMIT = 40;

/** "Süzgecin tamamı" sayfalanarak kimliğe çevrilirken üst sınır. */
export const RESOLVE_MAX = 1000;

/** Belge türleri — sıra belgedeki sıradır. `signable`: ıslak imza alır. `clauses`: madde şablonu var. */
export const DOCUMENT_KINDS = [
  { key: 'registration', label: 'Öğrenci Kayıt Sözleşmesi', signable: true, clauses: true },
  { key: 'meb', label: 'MEB Kayıt Sözleşmesi', signable: true, clauses: true },
  { key: 'promissory', label: 'Senetler', signable: true, clauses: true },
  { key: 'payment_plan', label: 'Ödeme Planı', signable: false, clauses: false },
  { key: 'legal_notice', label: 'Yasal Uyarı', signable: false, clauses: true },
];
export const KIND_LABEL = Object.fromEntries(DOCUMENT_KINDS.map((kind) => [kind.key, kind.label]));
const KIND_ORDER = DOCUMENT_KINDS.map((kind) => kind.key);

/** İmza durumu → [etiket, ton]. */
export const SIGNATURE_STATES = {
  awaiting: ['İmza bekliyor', 'warn'],
  signed: ['İmzalı', 'good'],
  resign_required: ['Yeniden imza bekliyor', 'bad'],
};
export const SIGNATURE_SOURCES = { paper: 'Islak imza', paper_existing: 'İmzalı (basılı, eski)' };
export const CONTRACT_STATUS = {
  active: ['Geçerli', 'info'],
  cancelled: ['İptal', 'dim'],
  terminated: ['Sona erdi', 'dim'],
};
export const INSTALLMENT_METHODS = {
  cash: 'NAKİT', promissory: 'SENET', cheque: 'ÇEK', card: 'KREDİ KARTI', transfer: 'HAVALE/EFT',
  online_card: 'UYGULAMADAN KART',
};
export const MEB_CATEGORIES = {
  education: 'Eğitim', stationery: 'Kırtasiye', book: 'Kitap', meal: 'Yemek', breakfast: 'Kahvaltı',
  transport: 'Servis', uniform: 'Kıyafet', supplementary_course: 'Takviye kursu', study_hall: 'Etüt',
  dormitory: 'Yatakhane', other: 'Diğer',
};
export const DISCOUNT_REASONS = {
  scholarship: 'Burs', sibling: 'Kardeş indirimi', staff_child: 'Personel çocuğu indirimi',
  success: 'Başarı indirimi', martyr_veteran_child: 'Şehit/Gazi çocuğu indirimi', early_registration: 'Erken kayıt',
  other: 'Diğer',
};

/** Ayar `contracts.defaultCopies` okunamazsa (izin yok / henüz yok) kullanılan nüshalar. */
export const DEFAULT_COPIES = { registration: 2, meb: 2, promissory: 1, payment_plan: 1, legal_notice: 1 };

/** Liste süzgeç çipleri: anahtar → A1 sorgusu. */
export const LIST_CHIPS = [
  { key: 'all', label: 'Tümü', query: {} },
  { key: 'awaiting', label: 'İmza bekleyen', query: { signature: 'awaiting' } },
  { key: 'resign_required', label: 'Yeniden imza bekleyen', query: { signature: 'resign_required' } },
  { key: 'signed', label: 'İmzalı', query: { signature: 'signed' } },
  { key: 'cancelled', label: 'İptal', query: { status: 'cancelled' } },
];

/** Atlanan öğrencinin sebebi (A17 `skipped[].code`). Bilinmeyen kod Akademi'nin kendisiyle görünür. */
export const SKIP_TEXT = {
  contract_missing_fields: 'Eksik bilgi',
  contract_not_found: 'Sözleşmesi yok',
  no_contract: 'Sözleşmesi yok',
  no_signed_revision: 'İmzalı sürümü yok',
  no_overdue: 'Gecikmiş taksiti yok',
  no_installments: 'Taksiti yok',
  not_ready: 'Belgesi üretilmemiş',
};

const clean = (value) => (value === undefined || value === null || value === '' ? undefined : value);

/**
 * Ekrandaki süzgeç durumu → A1 (`admin_listContracts`) sorgusu.
 * @param {{chip?: string, classId?: string, academicYear?: string, q?: string, overdueOnly?: boolean}} filters
 */
export function listQuery(filters = {}) {
  const chip = LIST_CHIPS.find((item) => item.key === filters.chip) || LIST_CHIPS[0];
  const query = { ...chip.query, classId: clean(filters.classId), academicYear: clean(filters.academicYear),
    q: clean(String(filters.q ?? '').trim()), overdueOnly: filters.overdueOnly ? true : undefined };
  return Object.fromEntries(Object.entries(query).filter(([, value]) => value !== undefined));
}

/**
 * "Süzgecin tamamı" Akademi'nin toplu süzgecine (A17 `filter`) çevrilebiliyor mu? Çevrilebiliyorsa süzgeç,
 * değilse `null` (çağıran listeyi sayfalayıp kimliklere çevirir). Akademi süzgeci: `classId`, `signature`,
 * `overdueOnly`. Arama, eğitim yılı ve "İptal" çipi orada yok.
 */
export function documentsFilter(filters = {}) {
  const query = listQuery(filters);
  if (query.q || query.academicYear || query.status) return null;
  const filter = {};
  if (query.classId) filter.classId = query.classId;
  if (query.signature) filter.signature = query.signature;
  if (query.overdueOnly) filter.overdueOnly = true;
  return filter;
}

/** 1–3 arası tam sayı nüsha (geçersizse varsayılan). */
export function copiesOf(value, fallback = 1) {
  const number = Math.round(Number(value));
  if (!Number.isFinite(number) || number < 1) return Math.min(3, Math.max(1, Number(fallback) || 1));
  return Math.min(3, number);
}

/** Ayardaki nüsha varsayılanı ile taslak varsayılanını birleştirir (eksik tür → taslak). */
export function defaultCopies(setting) {
  const out = {};
  for (const key of KIND_ORDER) out[key] = copiesOf(setting?.[key], DEFAULT_COPIES[key]);
  return out;
}

/** Seçili türleri belge sırasına dizer; bilinmeyeni atar, tekrarı siler. */
export function orderedKinds(kinds) {
  const wanted = new Set(kinds || []);
  return KIND_ORDER.filter((key) => wanted.has(key));
}

/**
 * "Belge çıkar" formu → A17 gövdesi. Hata varsa `{errors: [...]}`; yoksa `{body}`.
 *
 * @param {object} form
 *   target: {contractIds?: string[], studentIds?: string[], filter?: object}  — biri dolu olmalı
 *   kinds: string[]  — en az bir tür
 *   copies: {kind: number}  — 1–3 (yalnız seçili türler gönderilir)
 *   promissoryScope: 'all' | 'open'  — yalnız senet seçiliyse gönderilir
 *   revision: 'latest' | 'signed'
 */
export function buildDocumentsRequest(form = {}) {
  const errors = [];
  const kinds = orderedKinds(form.kinds);
  if (!kinds.length) errors.push('En az bir belge türü seçin.');
  const target = form.target || {};
  const body = {};
  const contractIds = [...new Set((target.contractIds || []).filter(Boolean).map(String))];
  const studentIds = [...new Set((target.studentIds || []).filter(Boolean).map(String))];
  if (contractIds.length) body.contractIds = contractIds;
  if (studentIds.length) body.studentIds = studentIds;
  if (!contractIds.length && !studentIds.length) {
    if (target.filter && typeof target.filter === 'object') body.filter = { ...target.filter };
    else errors.push('Belge çıkarılacak sözleşmeleri seçin.');
  }
  body.kinds = kinds;
  const copies = {};
  for (const kind of kinds) copies[kind] = copiesOf(form.copies?.[kind], DEFAULT_COPIES[kind]);
  const options = { copies };
  if (kinds.includes('promissory')) options.promissoryScope = form.promissoryScope === 'open' ? 'open' : 'all';
  options.revision = form.revision === 'signed' ? 'signed' : 'latest';
  body.options = options;
  return errors.length ? { errors } : { body };
}

/** "Gecikenlere Yasal Uyarı" tek tık isteği. */
export function legalNoticeRequest(copySetting) {
  return buildDocumentsRequest({ target: { filter: { overdueOnly: true } }, kinds: ['legal_notice'],
    copies: { legal_notice: defaultCopies(copySetting).legal_notice }, revision: 'latest' }).body;
}

/** Hedefteki sözleşme sayısı biliniyorsa iş sıraya mı girer? (Bilgi metni için; karar Akademi'de.) */
export function willQueue(count) {
  return Number(count) > SYNC_LIMIT;
}

/**
 * A17/A18 yanıtını ekranın anlayacağı biçime sokar.
 * @returns {{state: 'ready'|'queued'|'empty'|'failed', url, fileName, jobId, included, skipped: object[]}}
 */
export function documentsOutcome(response) {
  const data = response || {};
  const skipped = (Array.isArray(data.skipped) ? data.skipped : []).map((item) => ({
    studentId: item?.student?.id || null,
    name: item?.student?.displayName || item?.student?.fullName || item?.student?.name || 'Öğrenci',
    classGroup: item?.student?.classGroup?.name || '',
    code: item?.code || '',
    reason: SKIP_TEXT[item?.code] || item?.message || item?.code || 'Atlandı',
    missing: (Array.isArray(item?.missingFields) ? item.missingFields : [])
      .map((field) => field?.label || field?.field).filter(Boolean),
  }));
  const included = Number(data.includedCount || 0);
  let state = 'failed';
  if (data.status === 'queued' || data.status === 'running') state = 'queued';
  else if (data.status === 'ready' && data.file?.url) state = 'ready';
  else if (data.status === 'ready' || (data.status === undefined && !data.file)) state = 'empty';
  return { state, url: data.file?.url || null, fileName: data.file?.fileName || '', jobId: data.jobId || null,
    included, skipped, error: data.error || null };
}

/** Atlananlar için tek satırlık açıklama ("Eksik bilgi: TC kimlik no, ev adresi"). */
export function skippedLine(item) {
  return item.missing?.length ? `${item.reason}: ${item.missing.join(', ')}` : item.reason;
}

/**
 * Listenin "değişti mi" izi — görünür ilk sayfanın kimlik + sürüm + imza + durum + tutar bilgisi. Yoklamada
 * iz değişmediyse liste yeniden çizilmez (seçim ve kaydırma korunur).
 */
export function listFingerprint(items, size = 50) {
  return (Array.isArray(items) ? items : []).slice(0, size).map((row) => [row?.id, row?.latestRevisionNo,
    row?.signature?.state, row?.signature?.signedRevisionNo, row?.status, row?.totalMinor].join(':')).join('|');
}

/** Eğitim yılı seçenekleri: bugünün yılından (ay ≥ başlangıç ayı ise bu yıl) geriye ve bir ileri. */
export function academicYearOptions(today = new Date(), startMonth = 9, back = 3) {
  const year = today.getFullYear();
  const month = today.getMonth() + 1;
  const current = month >= startMonth ? year : year - 1;
  const out = [];
  for (let first = current + 1; first >= current - back; first -= 1) out.push(`${first}-${first + 1}`);
  return out;
}

/** Sözleşme numarası metni: her zaman string ("BBD1-1000", eski basılı "72128"). */
export function contractNumber(row) {
  const value = row?.number;
  return value === null || value === undefined || value === '' ? '—' : String(value);
}

/** Öğrencinin ad soyadı (StudentRef). */
export function studentName(student) {
  return student?.displayName || student?.fullName || student?.name || '—';
}
