// Sözleşme kimliği — ekran modeli (göç 014, iş 80-km-kimlik). DOM ve import YOK:
// Node'da da yüklenir (tests/test_bbd_students_kimlik_arayuz.py). Kural backend'le
// (`backend/identity.py`) AYNIDIR; sunucu yine doğrular, ekran anında gösterir.

/** Konya ilçeleri önce (kurum Konya'da), sonra büyük iller — yalnız ÖNERİ, serbest metin. */
export const KONYA_DISTRICTS = [
  'Selçuklu', 'Meram', 'Karatay', 'Ereğli', 'Akşehir', 'Beyşehir', 'Çumra', 'Seydişehir',
  'Ilgın', 'Cihanbeyli', 'Kulu', 'Karapınar', 'Kadınhanı', 'Sarayönü', 'Bozkır', 'Yunak',
  'Doğanhisar', 'Hüyük', 'Altınekin', 'Hadim', 'Çeltik', 'Güneysınır', 'Emirgazi', 'Taşkent',
  'Tuzlukçu', 'Derebucak', 'Akören', 'Ahırlı', 'Derbent', 'Halkapınar', 'Yalıhüyük',
];
export const CITIES = [
  'Konya', 'Ankara', 'İstanbul', 'İzmir', 'Karaman', 'Aksaray', 'Antalya', 'Mersin', 'Niğde',
  'Afyonkarahisar', 'Isparta', 'Eskişehir', 'Kayseri', 'Nevşehir', 'Bursa', 'Adana',
];

export const GENDERS = [['', 'Seçilmedi'], ['female', 'Kadın'], ['male', 'Erkek']];

/** TC kimlik no sorunu (Türkçe) ya da null. Boş değer sorun değildir (isteğe bağlı). */
export function nationalIdProblem(raw) {
  const value = String(raw ?? '').replace(/\s+/g, '');
  if (!value) return null;
  if (!/^[0-9]+$/.test(value)) return 'TC kimlik no yalnız rakam olmalı.';
  if (value.length !== 11) return 'TC kimlik no 11 hane olmalı.';
  if (value[0] === '0') return 'TC kimlik no 0 ile başlayamaz.';
  const d = [...value].map(Number);
  // JS'de % negatif sonuç verebilir: resmî kural pozitif kalanı ister.
  const tenth = ((((d[0] + d[2] + d[4] + d[6] + d[8]) * 7 - (d[1] + d[3] + d[5] + d[7])) % 10) + 10) % 10;
  const eleventh = d.slice(0, 10).reduce((sum, item) => sum + item, 0) % 10;
  if (tenth !== d[9] || eleventh !== d[10]) return 'TC kimlik no geçersiz — doğrulama haneleri tutmuyor.';
  return null;
}

export function validNationalId(raw) {
  const value = String(raw ?? '').replace(/\s+/g, '');
  return value.length > 0 && nationalIdProblem(value) === null;
}

/** "10000000146" → "*******0146" (yalnız son 4 hane). */
export function maskNationalId(raw) {
  const value = String(raw ?? '').replace(/\s+/g, '');
  return value ? `*******${value.slice(-4)}` : '';
}

/** Ev / iş telefonu (sabit hat da olur) → "3322223344" ya da null. */
export function cleanAnyPhone(raw) {
  let digits = String(raw ?? '').replace(/\D/g, '');
  if (digits.startsWith('0090')) digits = digits.slice(4);
  else if (digits.startsWith('90') && digits.length === 12) digits = digits.slice(2);
  else if (digits.startsWith('0') && digits.length === 11) digits = digits.slice(1);
  return digits.length === 10 && '2345'.includes(digits[0]) ? digits : null;
}

/** "3322223344" → "0332 222 33 44". */
export function showAnyPhone(raw) {
  const digits = cleanAnyPhone(raw);
  if (!digits) return String(raw ?? '');
  return `0${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6, 8)} ${digits.slice(8)}`;
}

export function emailProblem(raw) {
  const value = String(raw ?? '').trim();
  if (!value) return null;
  return value.length <= 120 && /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(value)
    ? null : 'E-posta geçersiz (ör. ad@alan.com).';
}

/** "01.05.2009" ya da "2009-05-01" → "2009-05-01"; boş → ''; tanınmazsa null. */
export function isoDay(raw) {
  const text = String(raw ?? '').trim();
  if (!text) return '';
  let match = /^(\d{1,2})[./](\d{1,2})[./](\d{4})$/.exec(text);
  let iso = text;
  if (match) iso = `${match[3]}-${match[2].padStart(2, '0')}-${match[1].padStart(2, '0')}`;
  match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) return null;
  const day = new Date(`${iso}T00:00:00Z`);
  return Number.isNaN(day.getTime()) || day.toISOString().slice(0, 10) !== iso ? null : iso;
}

/** "2009-05-01" → "01.05.2009". */
export function showDay(iso) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso ?? ''));
  return match ? `${match[3]}.${match[2]}.${match[1]}` : String(iso ?? '');
}

/** Adres yeterli mi (senet / sözleşme): açık adres + il. */
export function addressComplete(address) {
  return Boolean(address && String(address.line || '').trim() && String(address.city || '').trim());
}

/**
 * Ekrandaki alan sorunları → [{key, message}] (sunucu da aynı kuralla reddeder).
 * `form`: {student: {...}, guardians: [{slot, ...}]} — düz metin değerler.
 */
export function problems(form, today = new Date().toISOString().slice(0, 10)) {
  const out = [];
  const s = form.student || {};
  const add = (key, message) => out.push({ key, message });
  const tc = nationalIdProblem(s.nationalId);
  if (tc) add('student.nationalId', tc);
  if (String(s.foreignId || '').trim().length > 20) add('student.foreignId', 'Pasaport / yabancı kimlik no en çok 20 karakter.');
  const birth = isoDay(s.birthDate);
  if (birth === null) add('student.birthDate', 'Tarih GG.AA.YYYY biçiminde olmalı.');
  else if (birth && !(birth >= '1900-01-01' && birth < today)) add('student.birthDate', 'Doğum tarihi geçmişte olmalı.');
  if (isoDay(s.registeredOn) === null) add('student.registeredOn', 'Tarih GG.AA.YYYY biçiminde olmalı.');
  if (String(s.homePhone || '').trim() && !cleanAnyPhone(s.homePhone)) add('student.homePhone', 'Ev telefonu 0xxx xxx xx xx biçiminde olmalı.');
  for (const g of form.guardians || []) {
    const gtc = nationalIdProblem(g.nationalId);
    if (gtc) add(`g${g.slot}.nationalId`, `${g.slot}. veli: ${gtc}`);
    if (String(g.workPhone || '').trim() && !cleanAnyPhone(g.workPhone)) add(`g${g.slot}.workPhone`, `${g.slot}. velinin iş telefonu 0xxx xxx xx xx biçiminde olmalı.`);
    const mail = emailProblem(g.email);
    if (mail) add(`g${g.slot}.email`, `${g.slot}. veli: ${mail}`);
  }
  return out;
}

const STUDENT_KEYS = ['nationalId', 'foreignId', 'birthDate', 'gender', 'registeredOn', 'schoolName',
  'homePhone', 'motherName', 'fatherName'];
const GUARDIAN_KEYS = ['fullName', 'nationalId', 'foreignId', 'occupation', 'workPhone', 'email'];
const norm = (value) => String(value ?? '').trim().replace(/\s+/g, ' ');
const sameAddress = (a, b) => ['line', 'district', 'city'].every((key) => norm(a?.[key]) === norm(b?.[key]));

/**
 * Sunucu hâli + form → YALNIZ değişen alanların gövdesi (PUT /students/{id}/identity).
 * Değişiklik yoksa null. Tarihler ISO'ya, telefonlar rakama çevrilir.
 */
export function diffBody(server, form) {
  const body = {};
  const s0 = server?.student || {};
  const s1 = form.student || {};
  const student = {};
  for (const key of STUDENT_KEYS) {
    let value = norm(s1[key]);
    if (key === 'birthDate' || key === 'registeredOn') value = isoDay(value) || value;
    if (key === 'homePhone') value = cleanAnyPhone(value) || value;
    if (key === 'nationalId') value = value.replace(/\s+/g, '');
    if (value !== norm(s0[key])) student[key] = value || null;
  }
  if (!sameAddress(s0.address, s1.address)) student.address = { ...emptyAddress(), ...(s1.address || {}) };
  if (Object.keys(student).length) body.student = student;
  const people = [];
  const before = new Map((server?.guardians || []).map((row) => [row.slot, row]));
  for (const g of form.guardians || []) {
    const old = before.get(g.slot) || {};
    const row = {};
    for (const key of GUARDIAN_KEYS) {
      let value = norm(g[key]);
      if (key === 'workPhone') value = cleanAnyPhone(value) || value;
      if (key === 'nationalId') value = value.replace(/\s+/g, '');
      if (key === 'email') value = value.toLowerCase();
      if (value !== norm(old[key])) row[key] = value || null;
    }
    for (const key of ['homeAddress', 'workAddress']) {
      if (!sameAddress(old[key], g[key])) row[key] = { ...emptyAddress(), ...(g[key] || {}) };
    }
    if (Object.keys(row).length) people.push({ slot: g.slot, phone: old.phone || g.phone || null, ...row });
  }
  if (people.length) body.guardians = people;
  return Object.keys(body).length ? body : null;
}

export function emptyAddress() {
  return { line: '', district: '', city: '' };
}

/** Sunucu görünümü → düzenlenebilir form (null → ''). */
export function toForm(view) {
  const s = view?.student || {};
  const address = (value) => ({ ...emptyAddress(), ...(value || {}) });
  const text = (value) => (value === null || value === undefined ? '' : String(value));
  return {
    student: {
      nationalId: text(s.nationalId), foreignId: text(s.foreignId), birthDate: showDay(text(s.birthDate)),
      gender: text(s.gender), registeredOn: showDay(text(s.registeredOn)), schoolName: text(s.schoolName),
      homePhone: s.homePhone ? showAnyPhone(s.homePhone) : '', motherName: text(s.motherName),
      fatherName: text(s.fatherName), address: address(s.address),
    },
    guardians: (view?.guardians || []).map((g) => ({
      slot: g.slot, phone: g.phone, relation: g.relation, relationLabel: g.relationLabel || '',
      fullName: text(g.fullName), nationalId: text(g.nationalId), foreignId: text(g.foreignId),
      occupation: text(g.occupation), workPhone: g.workPhone ? showAnyPhone(g.workPhone) : '',
      email: text(g.email), homeAddress: address(g.homeAddress), workAddress: address(g.workAddress),
    })),
  };
}

/** Görünümdeki eksik listesi → kısa satır ("Eksik: Öğrenci TC kimlik no, Veli ev adresi"). */
export function missingLine(missing) {
  const labels = (missing || []).map((item) => item.label || item).filter(Boolean);
  return labels.length ? `Sözleşme için eksik: ${labels.join(', ')}.` : 'Sözleşme bilgileri tam.';
}
