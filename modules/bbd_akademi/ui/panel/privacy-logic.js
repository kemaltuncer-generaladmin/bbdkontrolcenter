// KVKK talepleri — import'suz hüküm mantığı (Node testi:
// tests/test_akademi_kvkk.py). Ekran: privacy.js.

export const KIND = { account_deletion: 'Hesap silme', data_export: 'Veri indirme' };

export const STATUS = {
  received: ['Yeni', 'warn'],
  processing: ['İşleniyor', 'info'],
  completed: ['Tamamlandı', 'good'],
  rejected: ['Reddedildi', 'dim'],
  cancelled: ['Talep sahibi vazgeçti', 'dim'],
};

export const SUBJECT = { person: 'Veli / personel', student: 'Öğrenci' };

const DAY_MS = 24 * 60 * 60 * 1000;
const OPEN = ['received', 'processing'];

/** Açık talepte son güne kalan tam gün (geçmişse negatif); kapalı talepte null. */
export function daysLeft(row, now = Date.now()) {
  if (!row || !OPEN.includes(row.status) || !row.dueAt) return null;
  const due = Date.parse(row.dueAt);
  if (Number.isNaN(due)) return null;
  return Math.floor((due - now) / DAY_MS);
}

/**
 * Son gün rozeti: `{text, tone}` ya da null (kapalı talep). Süresi geçen
 * kırmızı (Akademi'nin `overdue`'su da yeterli), 7 gün ve altı sarı.
 */
export function dueBadge(row, now = Date.now()) {
  const left = daysLeft(row, now);
  if (row?.overdue && OPEN.includes(row?.status)) return { text: 'Süresi geçti', tone: 'bad' };
  if (left === null) return null;
  if (left < 0) return { text: 'Süresi geçti', tone: 'bad' };
  if (left === 0) return { text: 'Bugün son gün', tone: 'warn' };
  if (left <= 7) return { text: `${left} gün kaldı`, tone: 'warn' };
  return { text: `${left} gün kaldı`, tone: '' };
}

/**
 * Akademi'nin kendi 403'ü (`forbidden`) mü? Vekilin KM izni reddi
 * `details.operationId` taşır; Akademi'ninki taşımaz. Akademi'nin reddi KM
 * panel anahtarının `privacy.requests` iznini taşımadığı anlamına gelir.
 */
export function keyLacksPermission(error) {
  return Boolean(error && error.code === 'forbidden' && !error.details?.operationId);
}

/** Talep durumuna göre yapılabilecekler. */
export function actionsFor(row) {
  return {
    decide: row?.status === 'received',
    reopen: Boolean(row?.accessReopenable),
  };
}

/** Ret gerekçesi: kırpılmış, en az 10 karakter (KVKK m.13: ret gerekçeli). */
export const REJECT_MIN = 10;
export const NOTE_MAX = 500;
export function cleanNote(text) {
  const value = String(text ?? '').trim().slice(0, NOTE_MAX);
  return value || null;
}
