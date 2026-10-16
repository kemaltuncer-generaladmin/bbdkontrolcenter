// BBD Akademi — panelin BİLGİ MİMARİSİ (gezinme, ana sayfa ve öğrenci kartı yerleşimi).
//
// KULLANICI İSTEĞİ (15.10.2026): "kontrol merkezindeki tüm menüler ve o
// menülerin içerisindeki işlevleri de elden geçirip güzelce sadeleştirip kolay
// kullanım, full görselliğe girmeni istiyorum … bbd akademiye girince yan yana
// iki tane sol menü görünüyor." Önceki düzende (13.10.2026, altı üst bölüm)
// panel KM kabuğunun sol menüsünün yanına KENDİ ikinci sol menüsünü çiziyordu.
// Şimdi:
//
//   · TEK SOL GEZİNME: sol menü yalnız KM kabuğunundur. Panel içinde gezinme
//     ÜSTTEDİR — simgeli bölüm şeridi (`AREAS`, en çok on ana grup, dershane
//     yöneticisinin günlük akışına göre) ve seçili grubun ekranları için ikinci
//     satırda hap sekmeler (`SECTIONS`). Kabuk modül başına tek menü satırı
//     taşır (`ui.nav`; alt öğe ilanı yok — K1/K6), bu yüzden alt gezinme kabuğa
//     taşınmadı, panelin üstüne alındı.
//   · ANA SAYFA: panel büyük görsel kutucuklarla açılır (`homeTiles`); her
//     kutucuk bir ana grup, altında o grubun ekranları — her ekran ana
//     sayfadan TEK, her yerden en çok İKİ tıkta (grup + hap).
//   · KOÇLUK ÖNDE (lider 15.10.2026 20:00): koçun en sık işleri (kontrol
//     kuyruğu, haftalık program ve görev atama, koçluk öğrencileri, hoca
//     soruları, mesajlar, görüşmeler) Koçluk grubunun DOĞRUDAN haplarıdır. Koçluk
//     ekranının kendi sekmeleri burada `host` + `payload` ile hapa açılmıştır:
//     aynı ekran (`coaching.js`) tek kez kurulur, hap sekmeyi seçer.
//   · YALNIZ AÇIK OLAN GÖRÜNÜR: kurumda kapalı bir özelliğe (`feature`) bağlı
//     ekran gezinmede hiç yer almaz — `403 feature_disabled` hata kartı olarak
//     görülmez. Yönetici Ayarlar → Kurum ayarları → Özellikler'den açar.
//
// Hiçbir işlev kaybolmadı: her eski ekran aynı dosyasıyla bir ana grubun
// altında durur; eski grup ve ekran adları `ALIASES` ile yeni yerlerine gider
// (eski → yeni eşleme: modül README "Bilgi mimarisi").
//
// Bu dosya bilerek İMPORT'SUZ ve DOM'suzdur: `.mjs` kopyası Node'da yüklenip
// sınanır (`tests/test_akademi_bilgi_mimarisi.py`).

/**
 * Ana gruplar — sıra gezinme sırasıdır (günlük akış: önce bugün, öğrenci ve
 * koçluk). `icon`: `nav-icons.js` simge adı; `tone`: kutucuk rengi.
 */
export const AREAS = [
  { key: 'today', label: 'Bugün', icon: 'sun', tone: 'blue',
    hint: 'Günün özeti, sizi bekleyen işler ve talepler (izin, görüşme, etüt).' },
  { key: 'students', label: 'Öğrenciler', icon: 'users', tone: 'indigo',
    hint: 'Öğrenci listesi, sınıflar, veliler, toplu işlemler ve öğrencinin tek kartı.' },
  { key: 'coaching', label: 'Koçluk', icon: 'compass', tone: 'violet',
    hint: 'Kontrol kuyruğu, haftalık program ve görev atama, hoca soruları, mesajlar, görüşmeler.' },
  { key: 'lessons', label: 'Dersler ve Program', icon: 'calendar', tone: 'teal',
    hint: 'Ders programı ve etüt, ders oturumları (iptal, telafi, yerine öğretmen), kurum takvimi, müfredat, akıllı '
      + 'tahta ders kayıtları.' },
  { key: 'attendance', label: 'Yoklama', icon: 'check', tone: 'green',
    hint: 'Günlük yoklama, pano, devamsızlık raporları ve düzeltme kuyruğu.' },
  { key: 'work', label: 'Ödev ve Deneme', icon: 'book', tone: 'amber',
    hint: 'Ödev (tek ya da toplu), çalışma planı, denemeler, değerlendirmeler, kaynaklar, okuma, şablonlar.' },
  { key: 'comms', label: 'İletişim', icon: 'megaphone', tone: 'rose',
    hint: 'Duyurular (uygulama ve SMS), veli toplantısı randevuları, SMS sağlığı ve kuyruğu.' },
  { key: 'money', label: 'Finans ve Kantin', icon: 'wallet', tone: 'orange',
    hint: 'Kurum cari hesabı ve tahsilat, kantin, kitap satışı ve talepleri — iki para ayrı tutulur.' },
  { key: 'reports', label: 'Raporlar', icon: 'chart', tone: 'cyan',
    hint: 'Bütün raporlar tek yerde (yoklama, ders dökümü, finans, deneme, okuma) ve erken uyarı.' },
  { key: 'admin', label: 'Ayarlar', icon: 'gear', tone: 'slate',
    hint: 'Kurum ayarları ve özellikler, personel, KVKK talepleri, senkron ve entegrasyon.' },
];

/**
 * Ekranlar. `requires`: KM izinleri (biri yeterli). `file`: ekran modülü
 * (`export function section(app)`). `area`: ana grup. `feature`: kurum
 * özellik bayrağı — kapalıysa ekran gezinmede yok. `reads`: okuma işlemleri
 * (biri izinliyse görünür). `host` + `payload`: aynı ekran dosyasının bir
 * sekmesine açılan hap (ekran tek kez kurulur; `shown(payload)` sekmeyi seçer).
 * Yeni ekran = bir dosya + bir satır.
 */
export const SECTIONS = [
  { key: 'dashboard', label: 'Özet', file: './today/dashboard.js', requires: ['view'], area: 'today' },
  { key: 'requests', label: 'Talepler', file: './today/requests.js', requires: ['requests'], area: 'today' },
  // `kmOnly`: Akademi izni olmayan ama öğrenci yönetimi izni olan kullanıcıya
  // (muhasebe, org_staff) panel YALNIZ bu bölümle açılır — KM kaydı (0.5.3).
  { key: 'students', label: 'Öğrenciler', file: './students/students.js', requires: ['view'], area: 'students',
    kmOnly: true },
  // Koçluk (KM-4; öğretmen = koç). `host: 'coaching'` hapları koçluk ekranının (coaching.js) sekmeleridir.
  { key: 'coachQueue', label: 'Kontrol kuyruğu', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_getCoachQueue', 'admin_listCoachReviews'],
    payload: { coachingTab: 'queue' } },
  { key: 'coachWeek', label: 'Haftalık program', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_getCoachWeek'], payload: { coachingTab: 'week' } },
  { key: 'coachStudents', label: 'Koçluk öğrencileri', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_listCoachingStudents'], claims: ['studentId'],
    payload: { coachingTab: 'students' } },
  // Hoca soruları (Akademi README §14 madde 63): öğrencinin hocaya sorusu; yanıt metin + görsel + sesli not.
  { key: 'questions', label: 'Hoca soruları', file: './coaching/questions.js', requires: ['learning'], area: 'coaching',
    feature: 'coach_questions' },
  // Hata defteri gelen kutusu (Akademi README §14 madde 104): öğrencinin yeni hata kayıtları + öğretmen adına çözüm
  // (metin, görsel, sesli not, PDF, video bağlantısı).
  { key: 'mistakeInbox', label: 'Hata defteri gelen kutusu', file: './coaching/mistake-inbox.js', requires: ['learning'],
    area: 'coaching', feature: 'coach_questions' },
  // Mesaj denetimi (F-K15 moderasyon + F-K21 denetim): şikâyet kuyruğu, karar, kilit, gerekçeli arama.
  { key: 'coachMeetings', label: 'Görüşmeler', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_listCoachMeetings'], payload: { coachingTab: 'meetings' } },
  { key: 'coachCoaches', label: 'Koçlar ve atama', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_listCoaches', 'admin_bulkAssignCoach'],
    payload: { coachingTab: 'coaches' } },
  { key: 'coachLibrary', label: 'Kütüphane', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_listCoachLibrary', 'admin_listLibraryProposals', 'admin_listVideoPool'],
    payload: { coachingTab: 'library' } },
  // 16.10.2026 (Akademi README §14 madde 115): Check-in bu hapın ikinci görünümüdür — 10 hap sınırında "Şablonlar"a
  // yer açıldı (eski `coachCheckins` adı ALIASES'ta).
  { key: 'coachMotivation', label: 'Tebrik ve check-in', file: './coaching/coaching.js', requires: ['view'],
    area: 'coaching', feature: 'coaching', host: 'coaching',
    reads: ['admin_listCoachBadges', 'admin_listCelebrationCandidates', 'admin_listCheckins', 'admin_getCheckinQuestionSet'],
    payload: { coachingTab: 'motivation' } },
  // Koçluk şablonları (Akademi README §14 madde 115): metin şablonları + program şablonları tek yerde.
  { key: 'coachTemplates', label: 'Şablonlar', file: './coaching/coaching.js', requires: ['view'], area: 'coaching',
    feature: 'coaching', host: 'coaching', reads: ['admin_listCoachTextTemplates', 'admin_listStudyPlanTemplates'],
    payload: { coachingTab: 'templates' } },
  { key: 'timetable', label: 'Ders programı ve etüt', file: './lessons/timetable.js', requires: ['view'],
    area: 'lessons' },
  // Ders oturumları (KM-3): tek dersin iptali, telafisi, yerine öğretmen; öğretmen haftası.
  { key: 'sessions', label: 'Ders oturumları', file: './lessons/sessions.js', requires: ['view'], area: 'lessons' },
  { key: 'calendar', label: 'Kurum takvimi', file: './lessons/institution-calendar.js', requires: ['view'],
    area: 'lessons' },
  { key: 'curriculum', label: 'Müfredat', file: './lessons/curriculum.js', requires: ['view'], area: 'lessons' },
  // Ders kayıtları (F-DK, Akademi README §14 madde 110): akıllı tahtalar ve derslikler, kayıtlar (gizle / sil / kim
  // izledi / paylaşım), depolama kotası, ayarlar — web / mobil yöneticiyle aynı uçlar. Ayarları yazmak kurum ayarıdır.
  { key: 'lessonRecordings', label: 'Ders kayıtları', file: './recordings/lesson-recordings.js', requires: ['recordings'],
    area: 'lessons', feature: 'lesson_recordings',
    reads: ['admin_listBoards', 'admin_listRecordings', 'admin_getRecordingQuota', 'admin_getRecordingSettings'] },
  { key: 'attendance', label: 'Yoklama', file: './attendance/attendance.js', requires: ['view'], area: 'attendance' },
  { key: 'homework', label: 'Ödevler', file: './work/homework.js', requires: ['view'], area: 'work' },
  { key: 'plan', label: 'Çalışma planı', file: './work/plan.js', requires: ['view'], area: 'work' },
  { key: 'exams', label: 'Denemeler', file: './exams/exams.js', requires: ['view'], area: 'work' },
  { key: 'evaluations', label: 'Değerlendirmeler', file: './exams/evaluations.js',
    requires: ['evaluations', 'learning_setup'], area: 'work' },
  { key: 'resources', label: 'Kaynaklar', file: './resources/resources.js', requires: ['view'], area: 'work' },
  { key: 'reading', label: 'Okuma', file: './work/reading.js', requires: ['view'], area: 'work' },
  { key: 'decks', label: 'Bilgi kartları', file: './work/decks.js', requires: ['learning_setup'],
    area: 'work', feature: 'flashcards' },
  // Şablonlar (0.5.4): ödev şablonu, atama paketi, plan şablonu TEK yerde (F-A25/F-A26). Çalışma
  // planındaki eski "Şablonlar" sekmesi buraya birleşti (aynı gövdeydi).
  { key: 'templates', label: 'Şablonlar', file: './work/templates.js', requires: ['view'], area: 'work' },
  { key: 'announcements', label: 'Duyurular', file: './comms/announcements.js', requires: ['announcements'],
    area: 'comms' },
  // Veli toplantısı randevu takvimi (Akademi README §14 madde 90): öğretmen saatleri, veli adına randevu, liste PDF'i.
  { key: 'parentMeetings', label: 'Veli toplantısı', file: './comms/parent-meetings.js', requires: ['requests'],
    area: 'comms' },
  // SMS (sms-kale §14 madde 58 + kurum SMS'i madde 88): eskiden Ayarlar → Kurum ayarları → SMS sekmesiydi.
  { key: 'sms', label: 'SMS', file: './comms/sms.js', requires: ['settings'], area: 'comms',
    reads: ['admin_getSmsHealth'] },
  // 16.10.2026: İletişim grubuna taşındı (Koçluk 10 hap sınırı — hata defteri gelen kutusu koçun önünde).
  { key: 'messages', label: 'Mesajlar', file: './comms/messages.js', requires: ['settings'], area: 'comms',
    feature: 'coaching' },
  { key: 'finance', label: 'Kurum finansı', file: './finance/finance.js', requires: ['finance'], area: 'money',
    feature: 'finance' },
  { key: 'canteen', label: 'Kantin', file: './finance/canteen.js', requires: ['view'], area: 'money',
    feature: 'canteen' },
  // Kitap satışı (0.5.4, F-A27): yönetici öğrencilere kurumdan kitap satar (üç adım).
  { key: 'bookSales', label: 'Kitap satışı', file: './finance/book-sales.js', requires: ['view'], area: 'money',
    feature: 'books' },
  { key: 'books', label: 'Kitap talepleri', file: './finance/books.js', requires: ['view'], area: 'money',
    feature: 'books' },
  // Rapor merkezi: dağınık raporların (yoklama, ders dökümü, finans, deneme, okuma…) tek giriş kapısı.
  { key: 'reportHub', label: 'Rapor merkezi', file: './reports/reports.js', requires: ['view'], area: 'reports' },
  { key: 'insights', label: 'Erken uyarı', file: './reports/insights.js', requires: ['view'], area: 'reports',
    feature: 'early_warning' },
  { key: 'settings', label: 'Kurum ayarları', file: './settings.js', requires: ['settings'], area: 'admin' },
  { key: 'staff', label: 'Personel', file: './staff.js', requires: ['staff'], area: 'admin' },
  { key: 'privacy', label: 'KVKK talepleri', file: './privacy.js', requires: ['privacy'], area: 'admin' },
];

/**
 * Eski bağlantı adları → ekran ya da grup. Başka ekranlar ve kayıtlı derin
 * bağlantılar (`app.go('panel')`, `ctx.payload.section` …) kırılmasın. Eski
 * grup anahtarları (13.10.2026 düzeni) yeni gruplarına gider.
 */
export const ALIASES = { panel: 'dashboard', ozet: 'dashboard', bugun: 'today', kvkk: 'privacy',
  ayarlar: 'settings', personel: 'staff', 'institution-calendar': 'calendar', kantin: 'canteen',
  'kitap-satisi': 'bookSales', 'book-sales': 'bookSales',
  sablonlar: 'templates', 'assignment-bundles': 'templates', sorular: 'questions', 'hoca-sorulari': 'questions',
  oturumlar: 'sessions', 'ders-oturumlari': 'sessions', 'ders-kayitlari': 'lessonRecordings', tahtalar: 'lessonRecordings',
  recordings: 'lessonRecordings', 'veli-toplantisi': 'parentMeetings',
  'parent-meetings': 'parentMeetings',
  'mesaj-sikayetleri': 'messages', 'mesaj-denetimi': 'messages', moderation: 'messages', mesajlar: 'messages',
  kocluk: 'coaching', 'koc-gorusmeleri': 'coachMeetings', 'kontrol-kuyrugu': 'coachQueue',
  coachCheckins: 'coachMotivation', 'check-in': 'coachMotivation', 'kocluk-sablonlari': 'coachTemplates',
  'metin-sablonlari': 'coachTemplates',
  'haftalik-program': 'coachWeek', 'gorev-ata': 'coachWeek',
  raporlar: 'reports', rapor: 'reportHub', iletisim: 'comms', yoklama: 'attendance', odev: 'homework',
  // Eski grup anahtarları (0.5.2 — altı üst bölüm).
  progress: 'work' };

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

/**
 * `ctx`: {has(izin) → bool, can(işlem) → bool, features, kmOnly}. `kmOnly`
 * (Akademi izni yok, öğrenci yönetimi izni var — 0.5.3): yalnız `kmOnly`
 * işaretli bölümler görünür, Akademi izinlerine bakılmaz (o bölüm KM
 * verisiyle çalışır). `reads` verilen ekran, okuma işlemlerinden biri
 * izinliyse görünür (`can` verilmezse izinli sayılır — karar vekilde).
 */
export function sectionVisible(section, { has = () => true, can = () => true, features = null,
  kmOnly = false } = {}) {
  if (!section) return false;
  if (kmOnly) return Boolean(section.kmOnly);
  return (section.requires || []).some((permission) => has(permission)) && featureOn(features, section.feature)
    && (!section.reads || section.reads.some((operationId) => can(operationId)));
}

/** Görünür ana gruplar ve altlarındaki görünür ekranlar (boş grup çıkar). */
export function buildNav(ctx = {}, sections = SECTIONS, areas = AREAS) {
  return areas
    .map((area) => ({ ...area, sections: sections.filter((section) => section.area === area.key
      && sectionVisible(section, ctx)) }))
    .filter((area) => area.sections.length > 0);
}

/**
 * Eşleşen hap: aynı ekranı taşıyan haplardan `payload`'a uyan; uyan yoksa
 * `claims` alanlarından birini taşıyan (ör. `{studentId}` → koçluk
 * öğrencileri) — yoksa null.
 */
function hostedMatch(sections, section, payload) {
  if (!section.host || !payload || typeof payload !== 'object') return null;
  const siblings = sections.filter((item) => item.host === section.host && item.payload);
  return siblings.find((item) => Object.entries(item.payload).every(([name, value]) => payload[name] === value))
    || siblings.find((item) => (item.claims || []).some((name) => payload[name] !== undefined && payload[name] !== null)
      && Object.keys(item.payload).every((name) => !(name in payload)))
    || null;
}

/**
 * `app.go(anahtar, payload)` hedefi: grup anahtarı → ilk ekranı; ekran
 * anahtarı → kendisi; eski ad → `ALIASES`. Paylaşılan ekranın hapında
 * (`host`) `payload` başka bir hapı işaret ediyorsa (ör. `{coachingTab:
 * 'week'}`) o hap seçilir. Görünmeyen ekran `null` (çağıran söyler).
 */
export function resolveTarget(nav, key, payload = null) {
  const wanted = ALIASES[key] || key;
  const pick = (area, section) => {
    const match = hostedMatch(area.sections, section, payload);
    return { area: area.key, section: (match || section).key };
  };
  for (const area of nav) {
    if (area.key === wanted) return pick(area, area.sections[0]);
  }
  for (const area of nav) {
    const section = area.sections.find((item) => item.key === wanted);
    if (section) return pick(area, section);
  }
  return null;
}

/**
 * Ana sayfa kutucukları: her görünür grup bir kutucuk; altında ekranları
 * (ana sayfadan tek tık). Tek grup görünüyorsa ana sayfa YOKTUR (`[]`) — tek
 * kapılı bir ana sayfa fazladan tık olurdu; panel doğrudan o gruba açılır.
 */
export function homeTiles(nav) {
  if (nav.length < 2) return [];
  return nav.map((area) => ({ key: area.key, label: area.label, hint: area.hint, icon: area.icon,
    tone: area.tone, links: area.sections.map((section) => ({ key: section.key, label: section.label })) }));
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
  // "Müfredat" (iş 23): öğrencinin müfredat seti (varsayılan / elle / sınıftan) + ilerleme.
  { key: 'progress', label: 'Müfredat ve gelişim', parts: [
    { key: 'curriculum', label: 'Müfredat seti ve ilerleme' },
    { key: 'exams', label: 'Denemeler' },
    { key: 'resources', label: 'Kaynaklar' },
    { key: 'coaching', label: 'Koç notları ve değerlendirmeler' },
  ] },
  { key: 'money', label: 'Cari, kantin ve kitap', parts: [
    { key: 'finance', label: 'Kurum cari', requires: 'finance', feature: 'finance' },
    { key: 'canteen', label: 'Kantin hareketleri', feature: 'canteen' },
    { key: 'books', label: 'Kurumdan alınan kitaplar', feature: 'books' },
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
