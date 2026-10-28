// BBD Akademi — Deneme Kulübü ekranının SAF mantığı (DOM yok; Node'da sınanır: tests/test_akademi_kulup_ekrani.py).
//
// Etiketler Akademi sözleşmesiyle birebir (bbd-akademi README §14 madde 141, devir/raporlar/81-kulup-hazir.md):
// web ve mobil aynı metinleri kullanır.

/** Davet durumu → [etiket, ton]. */
export const INVITE_STATUS = {
  sent: ['Gönderildi', 'info'],
  opened: ['Açıldı', 'info'],
  submitted: ['Form dolduruldu', 'warn'],
  processing: ['İşleniyor', 'warn'],
  completed: ['Tamamlandı', 'good'],
  failed: ['Başarısız', 'bad'],
  expired: ['Süresi doldu', 'dim'],
  cancelled: ['İptal', 'dim'],
};

/** Süzgeç çipleri (sıra = ekrandaki sıra). */
export const STATUS_CHIPS = [
  { key: '', label: 'Tümü' },
  { key: 'sent', label: 'Gönderildi' },
  { key: 'opened', label: 'Açıldı' },
  { key: 'processing', label: 'İşleniyor' },
  { key: 'completed', label: 'Tamamlandı' },
  { key: 'failed', label: 'Başarısız' },
  { key: 'expired', label: 'Süresi doldu' },
  { key: 'cancelled', label: 'İptal' },
];

export const INVITE_SOURCE = { store: 'BBDSTORE', branch: 'Kurumda' };

/** Ödeme yöntemi (Akademi `ClubPaymentMethod`). Kurumda kayıtta BBDSTORE seçilmez (o mağaza siparişidir). */
export const PAYMENT_METHOD = { cash: 'Nakit', card: 'POS', store: 'BBDSTORE', transfer: 'Havale / EFT' };
export const BRANCH_METHODS = ['cash', 'card', 'transfer'];

export const EXAM_TYPE = { LGS: 'LGS', TYT: 'TYT', TYT_AYT: 'TYT-AYT', AYT: 'AYT', other: 'Diğer' };

/** İşleme adımı (Akademi `ClubInviteStep`). */
export const INVITE_STEP = {
  student: 'KM\'de öğrenci açılıyor',
  mirror: 'Öğrenci Akademi\'ye geçiyor',
  contract: 'Sözleşme hazırlanıyor',
  payment: 'Tahsilat yazılıyor',
  app_invite: 'Uygulama daveti gidiyor',
  done: 'Bitti',
};

/** Hangi düğmeler bu davette anlamlı (Akademi kuralları: 81-kulup-hazir.md). */
export function inviteActions(invite) {
  const status = invite?.status || '';
  return {
    resend: ['sent', 'opened', 'expired'].includes(status),
    cancel: !['processing', 'completed', 'cancelled'].includes(status),
    retry: status === 'failed',
    match: status === 'failed',
  };
}

/** Liste sorgusu — boş süzgeç gönderilmez. */
export function inviteQuery(filters = {}) {
  const query = {};
  if (filters.status) query.status = filters.status;
  if (filters.source) query.source = filters.source;
  const text = String(filters.q || '').trim();
  if (text) query.q = text;
  return query;
}

/** Listenin izi (20 sn yoklamada yalnız değişince yeniden çiz). */
export function inviteFingerprint(items = []) {
  return (items || []).map((item) => [item.id, item.status, item.step || '', item.sendCount ?? 0,
    item.studentId || '', item.contractId || ''].join(':')).join('|');
}

/** Telefon: 0532…, +90 532…, 532… → 5321234567 (yoksa ham). */
export function normalizePhone(raw) {
  const digits = String(raw ?? '').replace(/\D/g, '');
  if (digits.length === 12 && digits.startsWith('90')) return digits.slice(2);
  if (digits.length === 11 && digits.startsWith('0')) return digits.slice(1);
  return digits;
}

/** "Yeni kulüp kaydı" penceresi → `admin_createClubInvite` gövdesi (source branch). */
export function branchInviteBody(draft = {}) {
  const body = {
    phone: normalizePhone(draft.phone),
    name: String(draft.name || '').trim() || null,
    source: 'branch',
    amountMinor: Math.max(0, Math.round(Number(draft.amountMinor) || 0)),
    paymentMethod: BRANCH_METHODS.includes(draft.paymentMethod) ? draft.paymentMethod : 'cash',
    paid: Boolean(draft.paid),
    send: draft.send !== false,
  };
  return body;
}

/** Kurumda kayıt penceresinin doğrulaması (Türkçe; alan → ileti). */
export function branchInviteErrors(draft = {}) {
  const errors = {};
  if (!/^5\d{9}$/.test(normalizePhone(draft.phone))) errors.phone = 'Cep numarası 10 haneli olmalı ve 5 ile başlamalı.';
  const amount = Number(draft.amountMinor);
  if (draft.amountMinor === null || draft.amountMinor === undefined || draft.amountMinor === ''
    || Number.isNaN(amount) || amount < 0) errors.amountMinor = 'Tutarı yazın (ücretsizse 0).';
  if (!BRANCH_METHODS.includes(draft.paymentMethod)) errors.paymentMethod = 'Ödeme yöntemini seçin.';
  return errors;
}

// ------------------------------------------------------------------ kulüp ayarları (`club.*`)

export const SETTING_KEYS = ['className', 'packageName', 'examType', 'examCount', 'seasonStart', 'seasonEnd',
  'priceMinor', 'inviteValidDays', 'smsText', 'autoInviteFromStore', 'mersisNo', 'taxOffice', 'taxNo', 'email'];

export const SETTING_DEFAULTS = {
  className: 'Deneme Kulübü', packageName: '', examType: 'TYT', examCount: null, seasonStart: '', seasonEnd: '',
  priceMinor: null, inviteValidDays: 7,
  smsText: 'Benim Başarı Dünyam Deneme Kulübü kaydınızı tamamlamak için: {link}',
  autoInviteFromStore: true, mersisNo: '', taxOffice: '', taxNo: '', email: '',
};

/** `/v1/admin/settings` yanıtından kulüp ayarları (iç içe `club` ya da düz `club.x`). */
export function clubSettings(raw) {
  const nested = raw && typeof raw.club === 'object' && raw.club ? raw.club : {};
  const out = { ...SETTING_DEFAULTS };
  for (const key of SETTING_KEYS) {
    const value = key in nested ? nested[key] : raw?.[`club.${key}`];
    if (value !== undefined) out[key] = value;
  }
  return out;
}

/** Ayar formunun doğrulaması. */
export function settingsErrors(draft = {}) {
  const errors = {};
  if (!String(draft.className || '').trim()) errors.className = 'Sınıf adı boş olamaz.';
  if (String(draft.className || '').length > 20) errors.className = 'Sınıf adı en çok 20 karakter.';
  const sms = String(draft.smsText || '');
  if (!sms.includes('{link}')) errors.smsText = 'SMS metni {link} yer tutucusunu içermeli.';
  else if (sms.length > 160) errors.smsText = `SMS metni en çok 160 karakter (şu an ${sms.length}).`;
  const count = draft.examCount;
  if (count !== null && count !== '' && count !== undefined
    && (!Number.isInteger(Number(count)) || Number(count) < 1 || Number(count) > 200)) {
    errors.examCount = 'Deneme sayısı 1–200 arası olmalı.';
  }
  const days = Number(draft.inviteValidDays);
  if (!Number.isInteger(days) || days < 1 || days > 60) errors.inviteValidDays = 'Geçerlilik 1–60 gün olmalı.';
  if (draft.seasonStart && draft.seasonEnd && draft.seasonEnd < draft.seasonStart) {
    errors.seasonEnd = 'Sezon bitişi başlangıçtan önce olamaz.';
  }
  if (draft.email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(draft.email).trim())) {
    errors.email = 'Geçerli bir e-posta adresi yazın.';
  }
  return errors;
}

function normalizeSetting(key, value) {
  if (['examCount', 'priceMinor'].includes(key)) {
    return value === '' || value === null || value === undefined ? null : Math.round(Number(value));
  }
  if (key === 'inviteValidDays') return Math.round(Number(value) || 7);
  if (key === 'autoInviteFromStore') return Boolean(value);
  if (['seasonStart', 'seasonEnd'].includes(key)) return value ? String(value) : null;
  return typeof value === 'string' ? value.trim() : value;
}

/** Yalnız değişen kulüp ayarları → `admin_patchSettings` gövdesi `{club: {...}}`; değişiklik yoksa null. */
export function settingsPatch(original, draft) {
  const before = clubSettings({ club: original || {} });
  const changed = {};
  for (const key of SETTING_KEYS) {
    const a = normalizeSetting(key, before[key]);
    const b = normalizeSetting(key, draft[key]);
    if (JSON.stringify(a ?? null) !== JSON.stringify(b ?? null)) changed[key] = b;
  }
  return Object.keys(changed).length ? { club: changed } : null;
}

/** İzleyici durumu → şerit tonu (ok/disabled sakin; diğerleri uyarı). */
export function watchTone(status) {
  const state = status?.state || '';
  if (state === 'ok') return (status?.counts?.invalidPhone || status?.counts?.cancelFailed) ? 'warn' : 'good';
  if (['disabled', 'pending', 'feature_off'].includes(state)) return 'info';
  return 'warn';
}
