// Öğrenci — saf model. DOM bilmez, ayrı test edilebilir.
//
// ALAN SAHİPLİĞİ (en kritik karar):
//
//   Kantin'in tuttukları  → opaque_id, display_name, parent_phone,
//                            spending_limit, is_blocked, balance
//   Kontrol Merkezi'nin    → ad, soyad, sınıf, okul no, öğrenci telefonu,
//                            veli adı, ikinci veli, durum, not
//
// Kantin'de sınıf, ad/soyad ayrımı ve öğrenci telefonu YOKTUR (öğrenci telefonu
// 2026-07-26 göçüyle silindi). Bu alanları kantine eklemek CANLI sisteme şema
// değişikliği demek olurdu; onun yerine burada, kendi kaydımızda tutuluyorlar
// ve kantin kaydına `kantinId` (opaque_id) ile bağlanıyorlar (K5).

export const STATUS = {
  active: 'Aktif',
  passive: 'Pasif',
};

/** Telefonu rakama indirger: "0532 111 22 33" → "5321112233". */
export function normalizePhone(input) {
  let digits = String(input ?? '').replace(/\D/g, '');
  if (digits.startsWith('90')) digits = digits.slice(2);
  if (digits.startsWith('0')) digits = digits.slice(1);
  return digits.slice(0, 10);
}

/** "5321112233" → "0532 111 22 33". Eksik numarayı olduğu gibi gösterir. */
export function formatPhone(digits) {
  const value = normalizePhone(digits);
  if (value.length !== 10) return value;
  return `0${value.slice(0, 3)} ${value.slice(3, 6)} ${value.slice(6, 8)} ${value.slice(8)}`;
}

/** Türkiye cep numarası: 10 hane ve 5 ile başlar. */
export function isMobile(digits) {
  const value = normalizePhone(digits);
  return value.length === 10 && value.startsWith('5');
}

/** "11a" → "11-A" · "  9 / b " → "9-B" · tanımadığını büyük harfe çevirip bırakır. */
export function normalizeClass(input) {
  const raw = String(input ?? '').trim().toLocaleUpperCase('tr');
  if (raw === '') return '';
  const match = raw.match(/^(\d{1,2})\s*[-/.]?\s*([A-ZÇĞİÖŞÜ]?)$/);
  if (!match) return raw.slice(0, 20);
  return match[2] ? `${match[1]}-${match[2]}` : match[1];
}

/** Sınıflar doğal sırayla: 9-A, 9-B, 10-A … (metin sıralaması 10'u 9'dan öne atar). */
export function classSortKey(value) {
  const match = String(value || '').match(/^(\d{1,2})(?:-([A-ZÇĞİÖŞÜ]))?/);
  if (!match) return [99, value || ''];
  return [Number(match[1]), match[2] || ''];
}

export function fullName(student) {
  return `${student.firstName || ''} ${student.lastName || ''}`.trim();
}


/** Kayıt geçerli mi? Hata listesi döner; boşsa geçerli. */
export function validate(student) {
  const errors = [];
  if (!String(student.firstName || '').trim()) errors.push('Ad zorunlu.');
  if (!String(student.lastName || '').trim()) errors.push('Soyad zorunlu.');
  if (student.parentPhone && !isMobile(student.parentPhone)) {
    errors.push('Veli telefonu 10 haneli cep numarası olmalı (5 ile başlar).');
  }
  if (student.studentPhone && !isMobile(student.studentPhone)) {
    errors.push('Öğrenci telefonu 10 haneli cep numarası olmalı.');
  }
  if (student.parentPhone2 && !isMobile(student.parentPhone2)) {
    errors.push('İkinci veli telefonu 10 haneli cep numarası olmalı.');
  }
  return errors;
}



/** Kuruşu okunur tutara çevirir: 12345 → "123,45 ₺". */
export function formatMoney(kurus) {
  if (kurus === null || kurus === undefined) return '—';
  const value = Number(kurus) / 100;
  return `${value.toLocaleString('tr-TR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ₺`;
}

