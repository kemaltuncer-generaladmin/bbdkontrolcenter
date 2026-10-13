// BBD Akademi — panelin BİLGİ MİMARİSİ (menü ve öğrenci kartı yerleşimi).
//
// KULLANICI İSTEĞİ (13.10.2026): "Uygulama çok karışık olmaya başlamış,
// sadeleşip basitleşsin." Panel eskiden 21 bölümü beş başlık altında düz liste
// olarak, öğrenci kartı 15 sekmeyi yan yana gösteriyordu. Şimdi:
//
//   · menüde en fazla ALTI üst bölüm (`AREAS`), görev odaklı Türkçe adlarla;
//     her üst bölümün alt ekranları (`SECTIONS`) yalnız o bölüm seçiliyken
//     altında açılır;
//   · öğrenci kartında en çok altı sekme (`STUDENT_CARD`); geri kalanı sekme
//     içinde katlanır bölüm;
//   · öğrenci YÖNETİMİ de burada (kullanıcı isteği 13.10.2026): "Öğrenciler"
//     bölümü KM Öğrenci Yönetimi'nin listesini, toplu işlemlerini ve gruplarını
//     çizer; öğrencinin tek kartında "Kayıt ve veliler" sekmesi KM kaydıdır;
//   · YALNIZ AÇIK OLAN GÖRÜNÜR: kurumda kapalı bir özelliğe (`feature`) bağlı
//     ekran menüde hiç yer almaz — `403 feature_disabled` hata kartı olarak
//     görülmez. Yönetici Ayarlar → Kurum ayarları → Özellikler'den açar.
//
// Hiçbir işlev kaybolmadı: her eski ekran aynı dosyasıyla bir üst bölümün
// altında durur (eski → yeni eşleme: modül README "Bilgi mimarisi").
//
// Bu dosya bilerek İMPORT'SUZ ve DOM'suzdur: `.mjs` kopyası Node'da yüklenip
// sınanır (`tests/test_akademi_bilgi_mimarisi.py`).

/** Üst bölümler — sıra menü sırasıdır (kullanım sıklığına göre). */
export const AREAS = [
  { key: 'today', label: 'Bugün', hint: 'Günün özeti, bekleyen talepler ve duyurular.' },
  { key: 'students', label: 'Öğrenciler', hint: 'Öğrenci listesi, sınıflar, gruplar ve veliler; '
    + 'öğrenci kartı.' },
  { key: 'lessons', label: 'Dersler ve Program', hint: 'Ders programı ve etüt, yoklama, ödev, '
    + 'çalışma planı ve kurum takvimi.' },
  { key: 'progress', label: 'Ölçme ve Gelişim', hint: 'Denemeler, değerlendirmeler, kaynaklar, '
    + 'müfredat ve öğrenme araçları.' },
  { key: 'money', label: 'Kantin ve Finans', hint: 'Kurum cari hesabı, kantin kuralları ve kitap '
    + 'talepleri — iki para ayrı tutulur.' },
  { key: 'admin', label: 'Ayarlar', hint: 'Kurum ayarları ve özellikler, personel, KVKK talepleri, '
    + 'senkron ve entegrasyon.' },
];

/**
 * Ekranlar. `requires`: KM izinleri (biri yeterli). `file`: ekran modülü
 * (`export function section(app)`). `area`: üst bölüm. `feature`: kurum
 * özellik bayrağı — kapalıysa ekran menüde yok. Yeni ekran = bir dosya + bir satır.
 */
export const SECTIONS = [
  { key: 'dashboard', label: 'Özet', file: './dashboard.js', requires: ['view'], area: 'today' },
  { key: 'requests', label: 'Talepler', file: './requests.js', requires: ['requests'], area: 'today' },
  { key: 'announcements', label: 'Duyurular', file: './announcements.js', requires: ['announcements'],
    area: 'today' },
  { key: 'students', label: 'Öğrenciler', file: './students.js', requires: ['view'], area: 'students' },
  { key: 'timetable', label: 'Ders programı ve etüt', file: './timetable.js', requires: ['view'],
    area: 'lessons' },
  { key: 'attendance', label: 'Yoklama', file: './attendance.js', requires: ['view'], area: 'lessons' },
  { key: 'homework', label: 'Ödevler', file: './homework.js', requires: ['view'], area: 'lessons' },
  { key: 'plan', label: 'Çalışma planı', file: './plan.js', requires: ['view'], area: 'lessons' },
  { key: 'calendar', label: 'Kurum takvimi', file: './institution-calendar.js', requires: ['view'],
    area: 'lessons' },
  { key: 'exams', label: 'Denemeler', file: './exams.js', requires: ['view'], area: 'progress' },
  { key: 'evaluations', label: 'Değerlendirmeler', file: './evaluations.js',
    requires: ['evaluations', 'learning_setup'], area: 'progress' },
  { key: 'resources', label: 'Kaynaklar', file: './resources.js', requires: ['view'], area: 'progress' },
  { key: 'curriculum', label: 'Müfredat', file: './curriculum.js', requires: ['view'], area: 'progress' },
  { key: 'reading', label: 'Okuma', file: './reading.js', requires: ['view'], area: 'progress' },
  { key: 'decks', label: 'Bilgi kartları', file: './decks.js', requires: ['learning_setup'],
    area: 'progress', feature: 'flashcards' },
  { key: 'insights', label: 'Erken uyarı', file: './insights.js', requires: ['view'], area: 'progress',
    feature: 'early_warning' },
  { key: 'finance', label: 'Kurum finansı', file: './finance.js', requires: ['finance'], area: 'money',
    feature: 'finance' },
  { key: 'canteen', label: 'Kantin', file: './canteen.js', requires: ['view'], area: 'money',
    feature: 'canteen' },
  { key: 'books', label: 'Kitap talepleri', file: './books.js', requires: ['view'], area: 'money',
    feature: 'books' },
  { key: 'settings', label: 'Kurum ayarları', file: './settings.js', requires: ['settings'], area: 'admin' },
  { key: 'staff', label: 'Personel', file: './staff.js', requires: ['staff'], area: 'admin' },
  { key: 'privacy', label: 'KVKK talepleri', file: './privacy.js', requires: ['privacy'], area: 'admin' },
];

/**
 * Eski bağlantı adları → ekran. Başka ekranlar ve kayıtlı derin bağlantılar
 * (`app.go('panel')`, `ctx.payload.section` …) kırılmasın.
 */
export const ALIASES = { panel: 'dashboard', ozet: 'dashboard', bugun: 'today', kvkk: 'privacy',
  ayarlar: 'settings', personel: 'staff', 'institution-calendar': 'calendar', kantin: 'canteen' };

/**
 * Özellik açık mı. `features` null (okunamadı) ya da bayrak listede yoksa
 * AÇIK sayılır — "bilmiyorum" hiçbir zaman "gizle" demek değildir; karar
 * Akademi'de kalır.
 */
export function featureOn(features, flag) {
  if (!flag) return true;
  if (!features || typeof features !== 'object') return true;
  return features[flag] !== false;
}

/** `ctx`: {has(izin) → bool, features}. */
export function sectionVisible(section, { has = () => true, features = null } = {}) {
  if (!section) return false;
  return (section.requires || []).some((permission) => has(permission)) && featureOn(features, section.feature);
}

/** Görünür üst bölümler ve altlarındaki görünür ekranlar (boş üst bölüm çıkar). */
export function buildNav(ctx = {}, sections = SECTIONS, areas = AREAS) {
  return areas
    .map((area) => ({ ...area, sections: sections.filter((section) => section.area === area.key
      && sectionVisible(section, ctx)) }))
    .filter((area) => area.sections.length > 0);
}

/**
 * `app.go(anahtar)` hedefi: üst bölüm anahtarı → ilk ekranı; ekran anahtarı →
 * kendisi; eski ad → `ALIASES`. Görünmeyen ekran `null` (çağıran söyler).
 */
export function resolveTarget(nav, key) {
  const wanted = ALIASES[key] || key;
  for (const area of nav) {
    if (area.key === wanted) return { area: area.key, section: area.sections[0].key };
  }
  for (const area of nav) {
    const section = area.sections.find((item) => item.key === wanted);
    if (section) return { area: area.key, section: section.key };
  }
  return null;
}

// ------------------------------------------------------------ öğrenci kartı

/**
 * Öğrenci kartı: en çok 6 sekme, her sekmede katlanır bölümler (tek bölümlü
 * sekme katlanmadan çizilir). Bölüm anahtarları
 * eski sekme anahtarlarıdır (`app.go('students', {studentId, tab: 'exams'})`
 * çalışmaya devam eder). Sekmenin İLK görünür bölümü açık gelir, diğerleri
 * kapalıdır ve açılınca yüklenir (ilk açılışta tek bölümün istekleri gider).
 * `requires`: KM izni; `feature`: kurum bayrağı.
 */
export const STUDENT_CARD = [
  { key: 'summary', label: 'Özet', parts: [
    { key: 'overview', label: 'Genel bakış' },
    { key: 'goals', label: 'Hedef ve öğrenme özeti' },
  ] },
  // Öğrenci yönetimi BBD Akademi'ye taşındı (13.10.2026): KM kaydı (kimlik, ana
  // sınıf ve gruplar, veliler, kantin ayarları, durum) bu sekmededir. Bölümü
  // Öğrenci Yönetimi modülü çizer (`bbd_students.record` arayüz yeteneği);
  // yetenek yoksa (modül kapalı / izin yok) sekme görünmez.
  { key: 'record', label: 'Kayıt ve veliler', parts: [
    { key: 'record', label: 'Kimlik, sınıf, veliler ve kantin', capability: 'record' },
  ] },
  { key: 'program', label: 'Program ve yoklama', parts: [
    { key: 'schedule', label: 'Ders programı' },
    { key: 'attendance', label: 'Yoklama' },
    { key: 'requests', label: 'İzin, görüşme ve etüt talepleri' },
  ] },
  { key: 'work', label: 'Ödev ve çalışma', parts: [
    { key: 'homework', label: 'Ödevler' },
    { key: 'learning', label: 'Çalışma planı, hata defteri ve okuma' },
    { key: 'study', label: 'Çalışma kayıtları' },
    { key: 'flashcards', label: 'Bilgi kartları', feature: 'flashcards' },
  ] },
  { key: 'progress', label: 'Ölçme ve gelişim', parts: [
    { key: 'exams', label: 'Denemeler' },
    { key: 'curriculum', label: 'Müfredat ve ilerleme' },
    { key: 'resources', label: 'Kaynaklar' },
    { key: 'coaching', label: 'Koç notları ve değerlendirmeler' },
  ] },
  { key: 'money', label: 'Kurum cari ve kantin', parts: [
    { key: 'finance', label: 'Kurum cari', requires: 'finance', feature: 'finance' },
    { key: 'canteen', label: 'Kantin hareketleri', feature: 'canteen' },
  ] },
];

/**
 * Görünür sekmeler ve bölümleri (boş sekme çıkar). `ctx.capabilities`:
 * {record: bool} — başka modülün çizdiği bölümler (yoksa gizli).
 */
export function studentCard(ctx = {}, card = STUDENT_CARD) {
  const has = ctx.has || (() => true);
  const capabilities = ctx.capabilities || {};
  return card
    .map((tab) => ({ ...tab, parts: tab.parts.filter((part) => (!part.requires || has(part.requires))
      && featureOn(ctx.features, part.feature)
      && (!part.capability || Boolean(capabilities[part.capability]))) }))
    .filter((tab) => tab.parts.length > 0);
}

/**
 * Kart içi hedef: sekme anahtarı → o sekme (ilk bölüm); bölüm (eski sekme)
 * anahtarı → sekmesi + o bölüm. Bilinmeyen/görünmeyen → ilk sekme.
 */
export function locatePart(tabs, key) {
  for (const tab of tabs) {
    if (tab.key === key) return { tab: tab.key, part: tab.parts[0].key };
  }
  for (const tab of tabs) {
    if (tab.parts.some((part) => part.key === key)) return { tab: tab.key, part: key };
  }
  const first = tabs[0];
  return first ? { tab: first.key, part: first.parts[0].key } : null;
}
