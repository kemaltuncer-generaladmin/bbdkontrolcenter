// KDS Yönetimi paneli — mutfak ekranının (kasaların) Kontrol Merkezi'nden yönetimi.
//
// NE YAPAR: kasaların bağlantı/yazıcı/kuyruk durumu; eşleme kodu üretimi ve
// iptal; kasada UZAKTAN uygulanan 24 yönetilen ayar (kilit politikası ve olay
// bazlı sesler dâhil); kasaya komut kuyruklama ve komut geçmişi; aktif
// siparişlerin kalemleri, revizyon geçmişi, revizyon yazma ve durum
// ilerletme; basılan fişlerin denetim dökümü.
//
// KASA ÇEKMECESİ, KASADAKİ AYARLAR EKRANININ AYNASIDIR (K-22 §6): on bölüm,
// kasadakiyle AYNI SIRADA, her bölümün düğmeleri de kendi bölümünde. Sıra
// keyfî değil — mutfaktaki personelle telefonda konuşan yönetici "üçüncü
// bölümdeki ses ayarı" dediğinde ikisi de aynı yere bakabilmeli.
//
// NE YAPMAZ:
//  · KASA GİBİ EŞLEŞMEZ. Kontrol Merkezi'nin kendi imzalı kapısı vardır
//    (K-21 sözleşmesi §1); bu panel yalnız kendi geçidiyle (`/api/bld_kds`)
//    konuşur, BLD sunucusuna doğrudan gitmez.
//  · KAYIT SİLMEZ. İptal edilen kasa listede kalır (`revoked_at` dolar),
//    revizyonlar üst üste yazılır, denetim satırı hiç silinmez.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder. TEK İSTİSNA kasa ayarlarıdır:
//    `GET /devices` yanıtındaki `can.settings` bayrağı `false` ise 24 ayar
//    alanı SALT OKUNUR çizilir ve yazma düğmesi nedeniyle birlikte kapanır.
//    Bu da bir yetki kapısı değil, K9'un ARAYÜZ yarısıdır — kapı `PATCH
//    /devices/{id}/settings` ucundaki `bld_kds.settings` iznidir. Bayrağın
//    sunucudan gelmesinin sebebi kabuğun panele izin listesi VERMEMESİDİR
//    (`ui-kernel.js` → `mountPanel`); panelin izni sorabileceği tek yer
//    sunucunun kendisi.
//  · SİPARİŞİN İSTENEN SAATİNİ değiştirmez — aşağıdaki "requested_at" notu.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · YÖNETİLEN AYARDA BOŞ = "yönetici dokunmadı". Üç değerli alanlar bilerek
//    açılır kutudur: onay kutusu `null` durumunu ANLATAMAZ ve `false` yazmak
//    kasada gerçek bir kilit doğurur. Sözleşme §2.2 bunu açıkça uyarıyor:
//    "alan eklenmesi bugünkü kasaları kilitlemez; kilit ancak yönetici açıkça
//    `false` yazınca doğar."
//  · Yalnız DEĞİŞEN ayar gönderilir (`formGrid.patch()`); tam gövde göndermek
//    dokunulmamış alanları yanlışlıkla yönetime almak olurdu.
//  · KURU PROVA KALDIRILDI (K-22 §4). Şalter yok, `dryRun` alanı gönderilmiyor
//    ve geçidin varsayılanı kapalı: bu ekrandan yapılan her yazma GERÇEKTİR.
//    Sunucudaki `dry_run` parametresi duruyor (sözleşme §4 additive) ama
//    buradan hiç kullanılmıyor; yanıtta yine de okunuyor — bir kurulum provayı
//    ayardan geri açarsa ekran "yapıldı" DEMEMELİ.
//  · GEREKÇE ZORUNLULUĞU DURUYOR. Kuru prova bir güvenlik ağıydı, gerekçe bir
//    denetim kaydıdır; biri kalktı diye öteki kalkmaz. Yıkıcı işlemler hâlâ
//    `confirmWithReason` ile onaylanır ve gerekçe `veykemtu_control_audit`
//    tablosuna yazılır.
//  · SUNUCU ADRESİ BİLEREK YÖNETİLMİYOR (K-22 §5). Alan salt okunur çizilir ve
//    nedeni yanında yazar: yanlış bir adres yazıldığı anda kasa yeni adrese
//    gider, oradan hiçbir şey alamaz ve DÜZELTMEYİ DE ALAMAZ — çünkü düzeltme
//    eski adresten gelecekti. Tek yazım hatasının mutfağı durdurduğu tek ayar.
//  · SES OLAYLARINDA `null` İLE BOŞ DİZE AYRI ŞEYLERDİR: `null` "dokunulmadı"
//    (kasa kendi listesini korur), boş dize "hiçbiri kapalı olmasın". Bu yüzden
//    beş onay kutusunun üstünde ayrı bir "dokunulmadı / yönetiliyor" seçimi
//    var — onay kutusu üçüncü hâli anlatamaz.
//  · Fiş kuyruğu sekmesi bir KUYRUK DEĞİL, DENETİM KAYDIDIR. KDS'in kendi
//    disk kuyruğu sunucuda yoktur (sözleşme §2.4); "bekleyen/hatalı" sayıları
//    cihaz sağlığından (`health.print_queue_*`) okunur.
//  · Sipariş revizyonunda kalem farkı değil TAM LİSTE gönderilir; boş liste
//    reddedilir. Siparişi bitirmek için durum ilerletme kullanılır.
//  · Revizyon SEÇENEK KİMLİĞİ ister (`option_value_ids`). Sunucu kimliği
//    veriyorsa olduğu gibi geri gönderilir — personel adede dokunurken seçenek
//    KORUNUR. Kimlik gelmiyorsa (eski sunucu) revizyon KAPALIDIR ve nedeni
//    yazılıdır: göndermek, "ekstra peynir"i sessizce düşürüp siparişi
//    ucuzlatırdı. Seçenekler ekranda görünür ama bu turda DÜZENLENMEZ.
//  · Ürün ekleme kataloğu (`GET /menu`) yoksa ekran durmaz: aranabilir seçici
//    yerine elle kimlik yoluna düşer ve nedenini yazar.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_kds/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_kds/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, clip, confirmWithReason, copyText, h,
  loadStyles, money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import { createPicker } from '../../ui-kit/picker.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';

const BASE = '/api/bld_kds';

/** Kasalar sekmesi AÇIKKEN tazeleme aralığı. Sekme kapalıyken hiç istek atılmaz. */
const LIVE_MS = 15000;

/** Gerekçe alt sınırı — sunucu da denetliyor (sözleşme §3), bu erken geri bildirim. */
const REASON_MIN = 10;

/** Revizyon ve durum uçlarında gerekçe üst sınırı (sözleşme §2.5). */
const REASON_MAX = 160;

// ---------------------------------------------------------------- sözlükler

/**
 * Sipariş durumları — `OrderStatusTransition::CODES` ile aynı sıra.
 * Renk TEK BAŞINA anlam taşımaz: her rozetin içinde yazı var.
 */
const STATUSES = [
  { key: 'yeni', label: 'Yeni', tone: 'info' },
  { key: 'onaylandi', label: 'Onaylandı', tone: 'info' },
  { key: 'hazirlaniyor', label: 'Hazırlanıyor', tone: 'warn' },
  { key: 'hazir', label: 'Hazır', tone: 'good' },
  { key: 'yolda', label: 'Yolda', tone: 'good' },
  { key: 'teslim_edildi', label: 'Teslim edildi', tone: 'dim' },
  { key: 'iptal', label: 'İptal', tone: 'bad' },
];

/**
 * İzin verilen sonraki durumlar — sunucudaki geçiş matrisinin KOPYASI.
 *
 * Burada durması bir yetkilendirme değil, bir GÖRÜNÜRLÜK kararıdır: olmayacak
 * bir düğmeyi çizip kullanıcıya 422 göstermek yerine hiç çizmiyoruz. Asıl kapı
 * sunucudadır ve geçersiz geçişi orada reddeder (K9).
 */
const NEXT_STATUS = {
  yeni: ['onaylandi', 'iptal'],
  onaylandi: ['hazirlaniyor', 'iptal'],
  hazirlaniyor: ['hazir', 'iptal'],
  hazir: ['yolda', 'teslim_edildi', 'iptal'],
  yolda: ['teslim_edildi', 'iptal'],
  teslim_edildi: [],
  iptal: [],
};

/**
 * `KitchenCommand::ALL` — sözleşme §2.3'ün beşi + K-22 §2'nin üçü.
 *
 * `danger: true` olanlar `bld_kds.devices` izni ister ve gerekçeli onaydan
 * geçer. İzin BURADA DENETLENMEZ (K9): görünürlük sunucuda süzülür, uç 403
 * dönerse ekran bunu söyler ve çalışmaya devam eder.
 */
const COMMANDS = [
  {
    key: 'test_receipt',
    label: 'Deneme fişi bas',
    danger: false,
    description: 'Kasadaki yazıcıdan bir deneme fişi çıkar. Sipariş verisi basılmaz.',
  },
  {
    key: 'silence_alarm',
    label: 'Alarmı sustur',
    danger: false,
    description: 'Çalan bağlantı/sipariş alarmını susturur. Ayarları değiştirmez.',
  },
  {
    key: 'clear_failed',
    label: 'Hatalı fişleri düşür',
    danger: true,
    description: 'Kasadaki HATALI fiş işleri silinir ve BİR DAHA BASILAMAZ. '
      + 'Basılmamış bir fiş varsa kalıcı olarak kaybolur.',
  },
  {
    key: 'clear_queue',
    label: 'Kuyruğun tamamını düşür',
    danger: true,
    description: 'Yalnız hatalılar değil, BEKLEYEN işler de düşer. Henüz '
      + 'basılmamış mutfak ve müşteri fişleri kalıcı olarak kaybolur; kuyruk '
      + 'temizlendikten sonra o siparişlerin fişi ancak elle yeniden bastırılarak '
      + 'çıkar.',
  },
  {
    key: 'update',
    label: 'Kasayı güncelle',
    danger: true,
    description: 'Kasa yeni sürümü indirir, kurar ve kendini yeniden başlatır. '
      + 'Kurulum boyunca ekran sipariş göstermez. Kurulum başarısız olursa kasa '
      + 'ESKİ SÜRÜMDE çalışmaya devam eder ve sonuç komut geçmişinde görünür — '
      + 'servis saatinde başlatmayın.',
  },
  {
    key: 'restart',
    label: 'Kasayı yeniden başlat',
    danger: true,
    description: 'Mutfak ekranı kapanıp yeniden açılır. Ekran başındaki personel '
      + 'birkaç saniye sipariş göremez; servis sırasında dikkatli kullanın.',
  },
  {
    key: 'unpair',
    label: 'Eşlemeyi kaldır',
    danger: true,
    description: 'Kasanın cihaz token\'ı silinir ve kasa EŞLEME EKRANINA döner. '
      + 'O andan itibaren HİÇ sipariş göremez; geri dönmesi için sahada yeni bir '
      + 'eşleme kodunun kasaya ELLE girilmesi gerekir. Uzaktan geri alınamaz.',
  },
];

const COMMAND_BY_KEY = new Map(COMMANDS.map((item) => [item.key, item]));

const REPRINT_TYPES = [
  { value: 'mutfak', label: 'Mutfak fişi' },
  { value: 'musteri', label: 'Müşteri fişi' },
  { value: 'kurye', label: 'Kurye fişi' },
];

const UNTOUCHED = 'Boş bırakılırsa yönetici dokunmamış sayılır: kasa kendi '
  + 'varsayılanını kullanır.';

/** Bölüm başlığındaki ipucu — K-22 §6 her bölümün başında bunu istiyor. */
const SECTION_HINT = 'BOŞ = YÖNETİCİ DOKUNMADI: kasa kendi varsayılanını '
  + 'kullanır. Bir alanı yönetimden çıkarmak için içini boşaltmak yeterlidir; '
  + 'yalnız DEĞİŞTİRDİĞİNİZ alanlar gönderilir.';

/** Yönetilen ayarı olmayan bölümler — orada "boş" diye bir şey yok, söylenir. */
const READONLY_HINT = 'BU BÖLÜMDE YÖNETİLEN AYAR YOKTUR: değerler kasanın '
  + 'bildirdiği gibi, SALT OKUNUR gösterilir. Buradaki düğmeler ayar yazmaz, '
  + 'kasaya komut gönderir.';

/** Üç değerli seçenek listesi — `null` durumunu onay kutusu anlatamaz. */
const tri = (on, off) => [
  { value: '', label: '— dokunulmadı' },
  { value: 'true', label: on },
  { value: 'false', label: off },
];

/** Olay bazlı seslerin ayar anahtarı (K-22 §1). */
const SOUND_KEY = 'disabled_sound_events';

/** Onay kutularının form anahtarı — gerçek ayar anahtarıyla karışmasın. */
const soundFieldKey = (name) => `${SOUND_KEY}__${name}`;

/** "dokunulmadı / yönetiliyor" seçimi. Onay kutusu üçüncü hâli anlatamaz. */
const SOUND_MODE_KEY = soundFieldKey('mode');

/**
 * KASADA TEK TEK KAPATILABİLEN SESLİ UYARILAR.
 *
 * Adlar kasadaki `KdsSoundEvent` enum adlarıyla BİREBİR aynıdır ve
 * camelCase'tir: telde taşınan bir VERİDİR, alan adı değil. `connectionLost`
 * BU LİSTEDE YOKTUR ve olmayacak — kapatılabilseydi mutfak, sunucuyla
 * bağlantının koptuğunu, yani siparişlerin hiç gelmediğini duyacak son uyarıyı
 * da kaybederdi.
 */
const SOUND_EVENTS = [
  { key: 'newOrder', label: 'Yeni sipariş' },
  { key: 'lateOrder', label: 'Geciken sipariş' },
  { key: 'printerError', label: 'Yazıcı hatası' },
  { key: 'subscriptionReminder', label: 'Abonelik hatırlatması' },
  { key: 'bbdOrder', label: 'BBD siparişi' },
];

const SOUND_LABELS = new Map(SOUND_EVENTS.map((item) => [item.key, item.label]));

/**
 * Kuyrukta bekleyen en eski iş bu süreyi aşarsa çekmecede UYARI çizilir.
 *
 * Sayı tek başına karar verdirmiyor: "3 bekleyen iş" yazıcı meşgulse
 * normaldir, ama en eskisi yarım saattir bekliyorsa kuyruk AKMIYOR demektir
 * ve o iki durum aynı sayıyla görünüyordu (K-22 §3).
 */
const QUEUE_STALE_MINUTES = 15;

/**
 * KASADAKİ AYARLAR EKRANININ AYNASI — on bölüm, AYNI SIRADA (K-22 §6).
 *
 * `kind` telde ne gideceğini söyler: 'int' sayı, 'bool' üç değerli, 'text'
 * metin, 'events' virgüllü olay listesi (beş onay kutusuna açılır). Hepsi
 * nullable; boş değer `null` olarak gider ve alanı yönetimden ÇIKARIR.
 *
 * `commands` o bölüme ait komut düğmeleri, `block` bölüme özel salt okunur
 * içerik (künye/telemetri), `revoke` cihaz iptali düğmesidir. Düğmeleri tek bir
 * "Komutlar" kartında toplamak, "deneme fişi"ni yazıcı ayarından, "alarmı
 * sustur"u ses ayarından ayırıyordu — oysa ikisi de aynı sorunun parçası.
 */
const SETTINGS_SECTIONS = [
  {
    key: 'server',
    title: '1 · Sunucu',
    hint: READONLY_HINT,
    fields: [],
    block: 'server',
  },
  {
    key: 'printer',
    title: '2 · Yazıcı',
    hint: SECTION_HINT,
    fields: [
      { key: 'printer_device_path', label: 'Yazıcı aygıt yolu', kind: 'text', maxLength: 190,
        hint: 'Örnek: /dev/usb/lp0. Yanlış yol yazıcıyı ARIZALI gösterir.' },
      { key: 'printer_code_page', label: 'Kod sayfası', kind: 'int', min: 0, max: 255,
        hint: 'ESC/POS kod sayfası numarası. 0–255. Sahada doğrulanan değer 29; '
          + 'yanlışı bütün Türkçe harfleri boşluk bastırır.' },
    ],
    commands: ['test_receipt'],
  },
  {
    key: 'sound',
    title: '3 · Ses ve alarm',
    hint: SECTION_HINT,
    fields: [
      { key: 'sound_enabled', label: 'Ses', kind: 'bool', on: 'Açık', off: 'Kapalı',
        hint: 'Kapalıysa kasa hiç ses çıkarmaz — alarm da çalmaz.' },
      { key: 'volume_percent', label: 'Ses düzeyi (%)', kind: 'int', min: 0, max: 100,
        hint: '0–100. Hoparlörün kendi seviyesinden ayrıdır.' },
      { key: 'audio_sink', label: 'Ses çıkışı', kind: 'text', maxLength: 190,
        hint: 'PulseAudio/PipeWire çıkış adı. BOŞ BIRAKMAK burada "sistem '
          + 'varsayılanına dön" demektir ve yönetilen bir değerdir.' },
      { key: SOUND_KEY, label: 'Kapatılan sesli uyarılar', kind: 'events', wide: true,
        hint: 'Hangi olayın sesi kasada ÇALMASIN. İşaretli olan KAPALI demektir.' },
      { key: 'alarm_repeat_seconds', label: 'Alarm tekrarı (sn)', kind: 'int', min: 0, max: 120,
        hint: '0 = tekrar etme. 0–120.' },
      { key: 'alarm_max_repeats', label: 'En çok tekrar', kind: 'int', min: 0,
        hint: 'Alarm en çok kaç kez tekrarlansın. 0 = sınırsız değil, hiç tekrar yok.' },
      { key: 'alarm_silenceable', label: 'Alarm susturulabilir', kind: 'bool',
        on: 'Susturulabilir', off: 'Susturulamaz',
        hint: 'Kapalıysa personel alarmı elle susturamaz; alarm kendi süresince çalar.' },
    ],
    commands: ['silence_alarm'],
    block: 'sound',
  },
  {
    key: 'tts',
    title: '4 · Anons (TTS)',
    hint: SECTION_HINT,
    fields: [
      { key: 'tts_enabled', label: 'Sesli okuma', kind: 'bool', on: 'Açık', off: 'Kapalı',
        hint: 'Yeni sipariş geldiğinde kasa siparişi sesle okur.' },
      { key: 'tts_rate_percent', label: 'Okuma hızı (%)', kind: 'int', min: 50, max: 200,
        hint: '50–200. 100 = normal hız.' },
    ],
  },
  {
    key: 'timing',
    title: '5 · Zamanlama',
    hint: SECTION_HINT,
    fields: [
      { key: 'poll_seconds', label: 'Sipariş yoklama aralığı (sn)', kind: 'int', min: 3, max: 60,
        hint: 'Kasa yeni siparişi kaç saniyede bir sorar. 3–60. Alt sınır 3: '
          + '2 saniyelik yoklama saatte 1800 istek eder ve kasa 429 alıp sipariş '
          + 'göremez hâle gelir.' },
      { key: 'health_seconds', label: 'Sağlık bildirimi aralığı (sn)', kind: 'int', min: 10, max: 300,
        hint: 'Kasa kendi durumunu kaç saniyede bir bildirir. 10–300. Çevrimiçi '
          + 'sayılma eşiği 3 dakikadır; bundan büyük bir değer kasayı sürekli '
          + 'çevrimdışı gösterir. Komutlar da bu yanıtla taşınır.' },
      { key: 'connection_alarm_seconds', label: 'Bağlantı alarmı (sn)', kind: 'int',
        min: 10, max: 600,
        hint: 'Sunucuya bu kadar süre ulaşılamazsa alarm çalar. 10–600. Bu '
          + 'alarmın SESİ kapatılamaz.' },
    ],
  },
  {
    key: 'thresholds',
    title: '6 · Eşikler',
    hint: SECTION_HINT,
    fields: [
      { key: 'warning_after_minutes', label: 'Uyarı eşiği (dk)', kind: 'int', min: 1, max: 480,
        hint: 'Sipariş kartı bu süreden sonra uyarı rengine geçer. 1–480.' },
      { key: 'late_after_minutes', label: 'Gecikme eşiği (dk)', kind: 'int', min: 1, max: 480,
        hint: 'Sipariş bu süreden sonra GECİKMİŞ sayılır. 1–480. Uyarı eşiğinden '
          + 'küçük olamaz; sunucu küçükse SESSİZCE uyarı eşiğine çeker.' },
    ],
  },
  {
    key: 'touch',
    title: '7 · Dokunmatik',
    hint: SECTION_HINT,
    fields: [
      { key: 'touch_mode', label: 'Dokunmatik kip', kind: 'bool', on: 'Açık', off: 'Kapalı',
        hint: 'Dokunmatik monitörde daha büyük hedefler ve kaydırma jestleri.' },
    ],
  },
  {
    key: 'lock',
    title: '8 · Kilit politikası',
    hint: `${SECTION_HINT} BURADA BOŞ = SERBEST: kilit ancak açıkça “Kilitli” `
      + 'seçilince doğar.',
    fields: [
      { key: 'allow_settings', label: 'Ayarlar ekranı', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken personel kasanın ayarlar ekranını açamaz.' },
      { key: 'allow_server_change', label: 'Sunucu adresi ve eşleme', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken sunucu adresi değiştirilemez ve eşleme sıfırlanamaz. '
          + 'Kasanın merkezden koparılmasını engeller.' },
      { key: 'allow_window_controls', label: 'Pencere düğmeleri', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken tam ekrandan çıkma ve küçültme kapalıdır.' },
      { key: 'allow_order_edit', label: 'Kasadan sipariş düzenleme', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken kasadan revizyon yazılamaz. Bu ekrandan yazma bundan '
          + 'ETKİLENMEZ — kilit yalnız kasayı bağlar.' },
      { key: 'allow_manual_reprint', label: 'Elle yeniden basma', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken personel karttan/ayarlardan fiş yeniden bastıramaz.' },
      { key: 'allow_sales_control', label: 'Satış şalteri', kind: 'bool',
        on: 'Serbest', off: 'Kilitli',
        hint: 'Kilitliyken personel satışı durduramaz ve "bugün tükendi" '
          + 'işaretleyemez.' },
      { key: 'lock_message', label: 'Kilit mesajı', kind: 'text', maxLength: 160, wide: true,
        hint: 'Kilitli bir düğmeye basınca kasada görünecek metin. En çok 160 '
          + 'karakter. Düğme GİZLENMEZ, pasif görünür — gizlemek personeli '
          + '"bozuldu" sanısına iter.' },
    ],
  },
  {
    key: 'queue',
    title: '9 · Kuyruk',
    hint: READONLY_HINT,
    fields: [],
    commands: ['clear_failed', 'clear_queue'],
    block: 'queue',
  },
  {
    key: 'device',
    title: '10 · Cihaz',
    hint: READONLY_HINT,
    fields: [],
    commands: ['update', 'restart', 'unpair'],
    block: 'device',
    revoke: true,
  },
];

const SETTINGS_INDEX = new Map();
for (const section of SETTINGS_SECTIONS) {
  for (const field of section.fields) SETTINGS_INDEX.set(field.key, field);
}

// -------------------------------------------------------------------- durum

const EMPTY_STATE = {
  tab: 'overview',
  // KURU PROVA DURUMU YOK (K-22 §4): şalter kalktı, `dryRun` gönderilmiyor ve
  // bu ekrandan yapılan her yazma gerçek. Yanıttaki `dry_run` yine okunuyor —
  // bkz. `announce`.
  // Geçidin BLD'ye ulaşıp ulaşamadığı (K7). Okuma uçlarından toplanır; her
  // sekmenin uyarısı ve her yazma düğmesinin kapısı buradan beslenir.
  link: { connected: true, error: '' },
  overview: null,
  overviewError: '',
  overviewStale: false,
  devices: [],
  devicesLoaded: false,
  devicesError: '',
  devicesStale: false,
  orders: [],
  ordersLoaded: false,
  ordersError: '',
  ordersStale: false,
  orderChip: null,
  jobs: [],
  jobsLoaded: false,
  jobsError: '',
  jobsStale: false,
  jobDevice: '',
  // Ürün kataloğu bir kez okunur ve panel açık kaldığı sürece saklanır:
  // her sipariş çekmecesinde yeniden çekmek 1200/saat sınırını boşuna yakar.
  menu: [],
  menuLoaded: false,
  menuMissing: false,
  menuError: '',
  // Bu oturum KASA AYARI yazabilir mi (`bld_kds.settings`). Sunucudan gelir —
  // kabuğun panele verdiği bağlamda izin listesi yok, panelin izni sorabileceği
  // tek yer `GET /devices` yanıtı. Varsayılan `false`: yanıt gelmeden ayar
  // formu düzenlenebilir çizilseydi, yetkisi olmayan yönetici 24 alanı
  // doldurup 403 yerdi. Kapı zaten sunucuda (K9); buradaki karar görünürlük.
  canSettings: false,
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];            // cleanup'ta çağrılacak gerçek kaynak bırakıcılar
const timers = { live: null, pairing: null };

// ------------------------------------------------------------------ araçlar

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * gövdedeki `{ok:false, error}`. İkincisi burada tek yerde okunur.
 *
 * `error` hem düz metin hem de sözleşmedeki `{code, message, details}` nesnesi
 * olabilir; ikisi de aynı yerden çözülür.
 */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) {
    const raw = result.error;
    const message = typeof raw === 'string' ? raw : (raw.message || 'İşlem başarısız.');
    const error = new Error(message);
    error.code = typeof raw === 'string' ? '' : (raw.code || '');
    throw error;
  }
  return result;
}

/**
 * Liste ucunun sarmalayıcısı sabit değil: BLD tarafı `{data:[…]}` kullanıyor,
 * Kontrol Merkezi geçitleri `{items:[…]}` kullanıyor. İkisini de kabul etmek,
 * ekranın sessizce boş kalmasından iyidir; beklenen şekil raporda yazılıdır.
 */
function listOf(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of [...keys, 'data', 'items']) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Geçit ya da BLD sunucusu düştüğünde okuma uçları
 * `{ok:true, connected:false, error:"…"}` döndürür. Bu bir HATA DEĞİL, bir
 * DURUMDUR: veri gerçekten yok değil, ŞU AN OKUNAMIYOR. Fırlatmak yanlış olurdu
 * (çağıran taraf "istek patladı" sanardı); sessizce boş liste çizmek daha da
 * yanlış — yönetici "kasa yok" ile "sunucuya ulaşılamıyor"u ayırt EDEMEZ ve
 * mutfağı arayıp "kasalar kapalı mı" diye sorar. K7'nin var olma nedeni budur.
 *
 * @returns {boolean} veri güvenilir mi (yani yazılabilir/çizilebilir mi)
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
    };
    return false;
  }
  // Anahtar HİÇ YOKSA durum değişmez: her uç `connected` taşımak zorunda değil
  // ve taşımayan bir yanıt, bilinen bir kopukluğu "düzeldi" saymamalı.
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/**
 * Bağlantı kopukken çizilen uyarı; bağlantı varken `null`.
 *
 * `stale` doğruysa ekranda ESKİ VERİ duruyor demektir ve bunu YAZMAK zorunludur:
 * güncel sanılan bayat tablo, boş tablodan tehlikelidir — yönetici çevrimdışı
 * bir kasayı çevrimiçi, kapanmış bir siparişi açık sanır.
 */
function linkAlert({ stale = false, what = 'Liste', auto = false } = {}) {
  if (state.link.connected) return null;
  const tail = auto
    ? 'Tazeleme sürüyor; bağlantı geri geldiğinde ekran KENDİLİĞİNDEN düzelir.'
    : 'Bağlantı geri geldiğinde “Yenile” ile tazeleyin.';
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} `
    + (stale
      ? `${what} son başarılı okumadan kalma ve BAYAT: yeni kayıtlar, durum `
        + 'değişiklikleri ve kuyruk sayıları burada GÖRÜNMÜYOR olabilir. '
      : `${what} okunamadı. `)
    + tail, 'bad');
}

/** Yazma düğmesinin kapalı olma nedeni; yazılabiliyorsa boş dize. */
function linkBlock() {
  if (state.link.connected) return '';
  return `BLD sunucusuna ulaşılamıyor (${state.link.error}) — ulaşılamayan bir `
    + 'sunucuya gönderilen işlem yöneticiye "gitti" hissi verirdi. Bağlantı '
    + 'gelince düğme kendiliğinden açılır.';
}

/**
 * Yazma düğmesi. Bağlantı yoksa ya da `blocked` doluysa KAPALI çizilir ve
 * nedenini söyler — sessizce çalışmayan düğme bırakılmaz (kit README §"blockedButton").
 */
function writeButton(label, { variant = '', title = '', onClick, blocked = '' } = {}) {
  const why = blocked || linkBlock();
  if (why) return blockedButton(label, why, { variant });
  return button(label, { variant, title, onClick });
}

/**
 * Hata mesajını ekrana taşır. 403 ÖZEL: panel çökmez, yalnız söyler.
 * Arayüzde izin denetimi YOKTUR (K9) — görünürlük sunucuda süzülür.
 */
function failed(error, fallback = 'İşlem başarısız.') {
  const message = error?.status === 403
    ? 'Bu işlem için yetkiniz yok. Ekran açık kalır; yetki yöneticiden istenir.'
    : (error?.message || fallback);
  toast(message, error?.status === 403 ? 'warn' : 'bad');
  return message;
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status?.set(label);
  try {
    const result = await work();
    nodes.status?.set(statusText());
    return result;
  } catch (error) {
    nodes.status?.set(failed(error), true);
    return null;
  } finally {
    busy = false;
  }
}

/**
 * Gerekçeli onay. Gerekçe backend'e gider ve `veykemtu_control_audit`
 * tablosuna yazılır — arayüzde denetlemek YETMEZ, ama erken geri bildirim
 * kullanıcıyı sunucudan dönen 422'ye bırakmaktan iyidir.
 */
async function askReason({ title, description, confirmLabel, danger = true, max = 0 }) {
  const reason = await confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN} karakter) — denetim kaydına yazılır`,
  });
  if (!reason) return null;
  if (max && reason.length > max) {
    toast(`Gerekçe en çok ${max} karakter olabilir (şu an ${reason.length}).`, 'bad');
    return null;
  }
  return reason;
}

/**
 * Yazma gövdesi.
 *
 * `actor` BURADAN GÖNDERİLMEZ: sözleşme §3 zorunlu tutuyor ama değeri oturumdan
 * gelir ve geçit dolduruyor. Panelden göndermek, işlemi yapan kişiyi istemcinin
 * beyanına bırakmak olurdu — denetim kaydının değerini sıfırlar.
 *
 * `dryRun` DA GÖNDERİLMEZ (K-22 §4). Uç alanı kabul etmeye devam ediyor ama
 * bu ekranın gönderecek bir değeri yok: kuru prova arayüzden kaldırıldı ve
 * varsayılan kapandı. Fonksiyon yine de duruyor — gövdeyi tek yerden kurmak,
 * bir gün ortak bir alan eklenmesi gerektiğinde on çağrı yerine bir satır
 * değiştirmek demek.
 */
function writeBody(fields) {
  return { ...fields };
}

/** Kuru prova yanıtını okunur kılar: ne OLACAĞINI yazar. */
function describeWould(would) {
  if (!would || typeof would !== 'object') return 'sunucu ayrıntı vermedi';
  const parts = [];
  for (const [key, value] of Object.entries(would)) {
    if (value === null || value === undefined) { parts.push(`${key}: (boşaltılır)`); continue; }
    if (Array.isArray(value)) { parts.push(`${key}: ${value.length} kayıt`); continue; }
    if (typeof value === 'object') { parts.push(`${key}: ${Object.keys(value).length} alan`); continue; }
    parts.push(`${key}: ${value}`);
  }
  return parts.length ? parts.join(' · ') : 'değişiklik yok';
}

/**
 * Yazma sonucunu bildirir ve GERÇEKTEN yazılıp yazılmadığını döndürür.
 *
 * PANEL ARTIK KURU PROVA İSTEMİYOR (K-22 §4) ama yanıttaki bayrak YİNE DE
 * okunuyor ve bu bilinçli: `dry_run_default` bir MODÜL AYARIDIR, bir kurulum
 * onu geri açabilir. O durumda sunucu hiçbir şey yazmaz; ekranın "yapıldı"
 * demesi, bu panelin söyleyebileceği en tehlikeli yalan olurdu. İki ad birden
 * okunur: geçit→BLD telinde `dry_run` (sözleşme §4), panel→geçit sınırında
 * `dryRun` (store_orders deseni).
 */
function announce(result, doneText) {
  const dry = result?.dry_run ?? result?.dryRun ?? false;
  if (dry) {
    toast('KURU PROVA AÇIK — hiçbir şey yazılmadı. Olacaktı: '
      + `${describeWould(result?.would)}. Bu ekranda kuru prova şalteri yoktur; `
      + 'ayar modülde açık bırakılmış (modules.bld_kds.dry_run_default).', 'warn');
    return false;
  }
  toast(doneText, 'good');
  return true;
}

const statusInfo = (code) => STATUSES.find((item) => item.key === code)
  || { key: code, label: code || '—', tone: '' };

/** Mutlak zaman + göreli zaman birlikte: biri ötekini tamamlar, değiştirmez. */
function whenCell(iso, { empty = '—' } = {}) {
  if (!iso) return h('span', 'bk-sub', empty);
  const box = h('span', 'bk-when');
  const rel = h('b', undefined, ago(iso));
  rel.title = stampIso(iso);
  box.append(rel, h('span', 'bk-sub', stampIso(iso)));
  return box;
}

/** Eşleme kodunun kalan süresi. Dolmuşsa açıkça söyler. */
function remainingText(iso) {
  const ms = Date.parse(iso);
  if (!ms || Number.isNaN(ms)) return { text: 'süre bilinmiyor', over: true };
  const seconds = Math.round((ms - Date.now()) / 1000);
  if (seconds <= 0) return { text: 'süresi doldu', over: true };
  const minutes = Math.floor(seconds / 60);
  return {
    text: minutes > 0
      ? `${minutes} dk ${String(seconds % 60).padStart(2, '0')} sn kaldı`
      : `${seconds} sn kaldı`,
    over: false,
  };
}

function statusText() {
  const parts = [];
  // Bağlantı durumu HER ZAMAN ÖNDE: altındaki sayılar bayat olabilir.
  if (!state.link.connected) parts.push(`BLD'ye ulaşılamıyor — ${state.link.error}`);
  if (state.overview) {
    const devices = state.overview.devices || {};
    const line = `${num(devices.online)} / ${num(devices.total)} kasa çevrimiçi`;
    parts.push(state.link.connected ? line : `${line} (bayat)`);
  } else if (state.overviewError) {
    parts.push(`Özet alınamadı — ${state.overviewError}`);
  }
  return parts.join(' · ');
}

// --------------------------------------------------------------------- veri

// Her yükleyici üç hâli AYIRIR:
//   1. başarı           → veri tazelenir, bayat işareti kalkar
//   2. connected:false  → ESKİ VERİ DURUR ve BAYAT işaretlenir (K7 durumu)
//   3. fırlatan hata    → veri boşaltılır ve hata metni gösterilir
// İkisini birleştirmek, "sunucu yok" ile "kayıt yok"u aynı ekrana çıkarırdı.

async function loadOverview() {
  try {
    const payload = await call(`${BASE}/overview`);
    if (linkOk(payload)) {
      state.overview = payload;
      state.overviewStale = false;
      state.overviewError = '';
    } else {
      state.overviewStale = Boolean(state.overview);
    }
  } catch (error) {
    state.overview = null;
    state.overviewStale = false;
    state.overviewError = error.message || 'Bağlantı kurulamadı.';
  }
}

async function loadDevices() {
  try {
    const payload = await call(`${BASE}/devices`);
    // İZİN SÖZLEŞMEYLE BİRLİKTE GELİR ve bağlantı kopukken de gelir: geçit
    // düşse bile yanıt `can` taşır (servis `contract`i yerel kuruyor), yani
    // BLD'ye ulaşılamadığı için form gereksiz yere kilitlenmez.
    state.canSettings = payload?.can?.settings === true;
    if (linkOk(payload)) {
      state.devices = listOf(payload, 'devices');
      state.devicesStale = false;
      state.devicesError = '';
    } else {
      // Liste SİLİNMEZ: 15 saniyelik tazeleme her kopuklukta tabloyu
      // boşaltsaydı ekran yanıp sönerdi. Bayat olduğu yazılıyor.
      state.devicesStale = state.devices.length > 0;
    }
  } catch (error) {
    state.devices = [];
    state.devicesStale = false;
    state.devicesError = error.message || 'Kasalar okunamadı.';
  }
  state.devicesLoaded = true;
}

function orderQuery() {
  const values = nodes.orderFilters ? nodes.orderFilters.values() : {};
  const params = new URLSearchParams();
  // YALNIZ SÖZLEŞMEDE YAZAN İKİ PARAMETRE gönderilir (§2.5). Arama ve durum
  // için sunucu tarafı süzgeç YOKTUR; uydurulmuş bir `?q=` sessizce yok
  // sayılır ve kullanıcı süzdüğünü sanırdı. Onlar istemcide süzülür.
  if (values.includeCompleted) params.set('include_completed', 'true');
  if (values.range?.start) params.set('since', values.range.start);
  const query = params.toString();
  return query ? `?${query}` : '';
}

async function loadOrders() {
  try {
    const payload = await call(`${BASE}/orders${orderQuery()}`);
    if (linkOk(payload)) {
      state.orders = listOf(payload, 'orders');
      state.ordersStale = false;
      state.ordersError = '';
    } else {
      state.ordersStale = state.orders.length > 0;
    }
  } catch (error) {
    state.orders = [];
    state.ordersStale = false;
    state.ordersError = error.message || 'Siparişler okunamadı.';
  }
  state.ordersLoaded = true;
}

/**
 * Ürün kataloğu — "ürün ekle" seçicisini besler.
 *
 * 404 ile ÖTEKİ HATALAR AYRILIR: uç henüz yayında değilse bu bir arıza değil,
 * bilinen bir eksiktir ve ekran elle kimlik yoluna düşer. Gerçek bir hatada
 * (403, 500, bağlantı) kullanıcıya hatanın kendisi söylenir — ikisine aynı
 * metni yazmak, çalışan bir kurulumda "uç yok" demek olurdu.
 */
async function loadMenu() {
  if (state.menuLoaded) return;
  try {
    const payload = await call(`${BASE}/menu`);
    if (linkOk(payload)) {
      state.menu = listOf(payload, 'menu');
      state.menuError = '';
      state.menuMissing = false;
    } else {
      // Bağlantı yokken KATALOG ÖNBELLEĞE ALINMAZ: `menuLoaded` işaretlenirse
      // bağlantı geri geldiğinde liste bir daha hiç okunmaz ve seçici kalıcı
      // olarak elle kimlik yoluna düşerdi.
      state.menu = [];
      state.menuMissing = false;
      state.menuError = state.link.error;
      return;
    }
  } catch (error) {
    state.menu = [];
    state.menuMissing = error?.status === 404;
    state.menuError = state.menuMissing ? '' : (error?.message || 'Bağlantı kurulamadı.');
  }
  state.menuLoaded = true;
}

async function loadJobs() {
  const params = new URLSearchParams();
  if (state.jobDevice) params.set('device_id', state.jobDevice);
  params.set('limit', '200');
  try {
    const payload = await call(`${BASE}/print-jobs?${params.toString()}`);
    if (linkOk(payload)) {
      state.jobs = listOf(payload, 'print_jobs');
      state.jobsStale = false;
      state.jobsError = '';
    } else {
      state.jobsStale = state.jobs.length > 0;
    }
  } catch (error) {
    state.jobs = [];
    state.jobsStale = false;
    state.jobsError = error.message || 'Fiş kaydı okunamadı.';
  }
  state.jobsLoaded = true;
}

// =============================================================== 1. Genel

async function showOverview() {
  const host = nodes.body;
  host.replaceChildren(skeletonRows(3, 4));
  await loadOverview();
  if (state.tab !== 'overview') return;
  host.replaceChildren();

  const actions = h('div', 'bk-actions');
  actions.append(button('Yenile', { onClick: () => showOverview() }));
  host.append(actions);

  // Bağlantı yokken ilk çizilen şey NEDEN olmalı: sayılar bayatsa altındaki
  // her rozet yanıltıcıdır ve kullanıcı önce bunu okumalı.
  const warning = linkAlert({ stale: state.overviewStale, what: 'Özet sayıları' });
  if (warning) host.append(warning);

  if (!state.overview) {
    host.append(emptyState({
      title: state.link.connected ? 'Özet alınamadı' : 'Özet okunamıyor',
      text: state.link.connected
        ? state.overviewError
        : 'BLD sunucusuna ulaşılamadığı için hiç sayı okunamadı. Bağlantı gelince '
          + '“Tekrar dene” ile bu ekran dolar.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => showOverview() })],
    }));
    return;
  }

  const devices = state.overview.devices || {};
  const orders = state.overview.orders || {};
  const jobs = state.overview.print_jobs || {};

  host.append(kpiRow([
    {
      label: 'Çevrimiçi kasa',
      value: `${num(devices.online)} / ${num(devices.total)}`,
      tone: Number(devices.online) === 0 && Number(devices.total) > 0 ? 'bad' : 'good',
      title: 'Son 3 dakikada haber veren ve iptal edilmemiş kasalar.',
    },
    { label: 'Aktif sipariş', value: num(orders.active),
      title: 'Terminal duruma (teslim edildi / iptal) düşmemiş siparişler.' },
    { label: 'Bekleyen fiş', value: num(devices.queue_pending),
      tone: Number(devices.queue_pending) > 0 ? 'warn' : '',
      title: 'Kasaların bildirdiği bekleyen fiş işi toplamı.' },
    { label: 'Hatalı fiş', value: num(devices.queue_failed),
      tone: Number(devices.queue_failed) > 0 ? 'bad' : '',
      title: 'Kasaların bildirdiği HATALI fiş işi toplamı.' },
    { label: 'Geciken sipariş', value: num(orders.late),
      tone: Number(orders.late) > 0 ? 'bad' : '',
      title: 'Gecikme eşiğini aşmış siparişler.' },
  ]));

  if (Number(devices.printer_fault) > 0) {
    host.append(alertBox(
      `${num(devices.printer_fault)} kasada YAZICI ARIZALI. O kasalarda fiş çıkmaz ve `
      + 'mutfak siparişi kâğıtta göremez. Kasalar sekmesinden ilgili kasayı açıp aygıt '
      + 'yolunu doğrulayın ve deneme fişi gönderin.', 'bad'));
  }
  if (Number(devices.queue_failed) > 0) {
    host.append(alertBox(
      `${num(devices.queue_failed)} fiş işi HATALI durumda. Hatalı kuyruğu temizlemek `
      + 'basılmamış fişi kalıcı olarak siler; önce yazıcı arızasının giderildiğinden '
      + 'emin olun.', 'warn'));
  }
  if (Number(devices.revoked) > 0) {
    host.append(alertBox(
      `${num(devices.revoked)} kasa iptal edilmiş. İptal edilen kasa listede kalır ve `
      + 'sunucuya bağlanamaz; kayıt SİLİNMEZ.', 'info'));
  }
  if (Number(devices.total) === 0) {
    host.append(emptyState({
      title: 'Hiç kasa tanımlı değil',
      text: 'Mutfak ekranı sunucuya bağlanabilmek için önce burada tanımlanmalı ve '
        + 'eşleme koduyla eşleşmeli.',
      actions: [button('Kasalara git', {
        variant: 'primary', onClick: () => nodes.tabs.select('devices'),
      })],
    }));
  }

  const breakdown = h('div', 'bk-chips');
  for (const item of STATUSES) {
    const count = Number(orders.by_status?.[item.key] || 0);
    const chip = badge(`${item.label} · ${num(count)}`, count ? item.tone : 'dim');
    breakdown.append(chip);
  }
  host.append(card('Aktif siparişlerin durumu', breakdown,
    `Bugün toplam ${num(orders.today)} sipariş`));

  const facts = h('div', 'bk-facts');
  for (const [label, value, title] of [
    ['Tanımlı kasa', num(devices.total), ''],
    ['İptal edilmiş kasa', num(devices.revoked), 'Kayıt silinmez, yalnız pasifleşir.'],
    ['Yazıcı arızalı kasa', num(devices.printer_fault), ''],
    ['Bugün basılan fiş', num(jobs.today), 'Denetim kaydındaki satır sayısı.'],
    ['Sunucu saati', stampIso(state.overview.server_time),
      'Kasaların gecikme hesabı bu saate göre yapılır.'],
  ]) {
    const line = h('div', 'bk-fact');
    line.append(h('span', 'bk-sub', label), h('b', undefined, String(value)));
    if (title) line.title = title;
    facts.append(line);
  }
  host.append(card('Sayılar', facts));

  host.append(hintBox(
    'Bu ekran mutfak KASALARINI yönetir. "Bekleyen/hatalı fiş" sayıları kasaların '
    + 'kendi bildirdiği sağlık verisinden gelir; sunucuda bir fiş kuyruğu YOKTUR. '
    + 'Fiş kuyruğu sekmesindeki liste basılmış fişlerin DENETİM KAYDIDIR, bir iş '
    + 'kuyruğu değildir.'));
}

// ============================================================== 2. Kasalar

const DEVICE_COLUMNS = [
  {
    key: 'name',
    label: 'Kasa',
    width: 'minmax(0, 1.6fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(clip(h('b'), row.name, 28));
      box.append(h('span', 'bk-sub', `#${row.id}`));
      return box;
    },
  },
  {
    key: 'online',
    label: 'Bağlantı',
    width: 'minmax(0, 1.4fr)',
    sortable: true,
    sortValue: (row) => (row.online ? 1 : 0),
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(badge(row.online ? 'Çevrimiçi' : 'Çevrimdışı', row.online ? 'good' : 'bad'));
      const seen = h('span', 'bk-sub', row.last_seen_at ? ago(row.last_seen_at) : 'hiç görülmedi');
      if (row.last_seen_at) seen.title = stampIso(row.last_seen_at);
      box.append(seen);
      return box;
    },
  },
  {
    key: 'printer',
    label: 'Yazıcı',
    width: '128px',
    cell: (row) => {
      const health = row.health || null;
      if (!health || health.printer_ok === null || health.printer_ok === undefined) {
        const chip = badge('Bilinmiyor', 'dim');
        chip.title = 'Kasa henüz sağlık bildirimi göndermedi.';
        return chip;
      }
      return badge(health.printer_ok ? 'Çalışıyor' : 'ARIZALI', health.printer_ok ? 'good' : 'bad');
    },
  },
  {
    key: 'queue',
    label: 'Kuyruk',
    width: '164px',
    title: 'Kasanın kendi disk kuyruğu — sunucudaki denetim kaydı değil.',
    cell: (row) => {
      const health = row.health || {};
      const pending = Number(health.print_queue_pending || 0);
      const failed = Number(health.print_queue_failed || 0);
      const box = h('span', 'bk-stack');
      box.append(h('span', undefined, `${num(pending)} bekleyen`));
      if (failed) box.append(badge(`${num(failed)} hatalı`, 'bad'));
      // SAYI TEK BAŞINA YETMİYOR: "3 bekleyen iş" yazıcı meşgulse normaldir,
      // en eskisi yarım saattir bekliyorsa kuyruk AKMIYOR demektir ve iki
      // durum aynı sayıyla görünüyordu (K-22 §3).
      const oldest = health.queue_oldest_at;
      if (oldest) {
        const line = h('span', 'bk-sub', `en eskisi ${ago(oldest)}`);
        line.title = `Kuyrukta bekleyen en eski iş: ${stampIso(oldest)}`;
        box.append(line);
      } else if (!failed) {
        box.append(h('span', 'bk-sub', 'hatalı yok'));
      }
      return box;
    },
  },
  {
    key: 'version',
    label: 'Sürüm',
    width: '104px',
    cell: (row) => row.health?.app_version || '—',
  },
  {
    key: 'status',
    label: 'Durum',
    width: 'minmax(0, 1.2fr)',
    cell: (row) => {
      const box = h('span', 'bk-stack');
      if (row.revoked_at) {
        const chip = badge('İptal edildi', 'bad');
        chip.title = stampIso(row.revoked_at);
        box.append(chip);
        box.append(h('span', 'bk-sub', 'kayıt silinmedi'));
        return box;
      }
      box.append(badge('Etkin', 'good'));
      const pending = Number(row.pending_command_count || 0);
      if (pending) box.append(badge(`${num(pending)} komut bekliyor`, 'warn'));
      else if (row.pairing?.usable) box.append(h('span', 'bk-sub', 'eşleme bekliyor'));
      return box;
    },
  },
];

function showDevices() {
  const host = nodes.body;
  host.replaceChildren();

  nodes.deviceActions = h('div', 'bk-actions');
  host.append(nodes.deviceActions);

  // Uyarı kabı ayrı tutuluyor: 15 saniyelik tazeleme yalnız bu iki kabı
  // yeniliyor, sekmenin tamamını yeniden çizmiyor (kaydırma konumu korunur).
  nodes.deviceAlert = h('div', 'bk-alertbox');
  host.append(nodes.deviceAlert);

  nodes.deviceWrap = h('div', 'bk-table');
  host.append(nodes.deviceWrap);
  host.append(hintBox(
    'Satıra tıklayınca kasanın çekmecesi açılır: kasanın AYARLAR EKRANININ '
    + 'AYNASI (on bölüm, aynı sırada, 24 yönetilen alan), her bölümün kendi '
    + 'komut düğmeleri ve komut geçmişi oradadır. Bu sekme açıkken liste 15 '
    + 'saniyede bir kendiliğinden tazelenir; sekme kapanınca tazeleme durur.'));

  paintDevices();
  if (!state.devicesLoaded) {
    nodes.deviceWrap.replaceChildren(skeletonRows(5, 6));
    refreshDevices({ silent: true });
  }
}

function paintDevices() {
  const wrap = nodes.deviceWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  // Yazma düğmesi bağlantıya göre her çizimde yeniden kuruluyor: bağlantı
  // gelince kendiliğinden açılmalı, kullanıcı sekme değiştirmek zorunda kalmamalı.
  nodes.deviceActions?.replaceChildren(
    button('Yenile', { onClick: () => refreshDevices({ silent: false }) }),
    writeButton('Yeni kasa tanımla', {
      variant: 'primary', onClick: () => createDevice(),
    }),
  );

  const warning = linkAlert({ stale: state.devicesStale, what: 'Kasa listesi', auto: true });
  nodes.deviceAlert?.replaceChildren(...(warning ? [warning] : []));

  let empty;
  if (state.devicesError) {
    empty = emptyState({
      title: 'Kasalar okunamadı',
      text: state.devicesError,
      actions: [button('Tekrar dene', {
        variant: 'primary', onClick: () => refreshDevices({ silent: false }),
      })],
    });
  } else if (!state.link.connected) {
    // "Kasa yok" DEMEK YASAK: sunucuya ulaşılamıyorken kaç kasa olduğu
    // bilinmiyor. Boş liste ile kopuk bağlantı aynı ekrana çıkmamalı.
    empty = emptyState({
      title: 'Kasa listesi okunamıyor',
      text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Kasa olup olmadığı `
        + 'şu an BİLİNMİYOR. Tazeleme sürüyor; bağlantı gelince liste kendiliğinden '
        + 'dolar.',
    });
  } else {
    empty = emptyState({
      title: 'Kasa yok',
      text: 'Mutfak ekranı sunucuya bağlanabilmek için önce burada tanımlanır; '
        + 'sonra üretilen eşleme kodu kasaya girilir.',
      actions: [writeButton('Yeni kasa tanımla', {
        variant: 'primary', onClick: () => createDevice(),
      })],
    });
  }

  nodes.deviceTable = dataTable({
    columns: DEVICE_COLUMNS,
    rows: state.devices,
    empty,
    onRow: (row) => openDevice(row.id),
  });
  wrap.append(nodes.deviceTable.node);
}

/** Canlı tazeleme — YALNIZ kasalar sekmesi açıkken. */
async function refreshDevices({ silent = true } = {}) {
  if (!silent) nodes.status?.set('Kasalar alınıyor…');
  await Promise.all([loadOverview(), loadDevices()]);
  if (state.tab !== 'devices') return;
  paintDevices();
  nodes.status?.set(statusText(), Boolean(state.devicesError) || !state.link.connected);
}

function startLive() {
  stopLive();
  timers.live = window.setInterval(() => {
    // Sekme kapandıysa hiç istek atma — zamanlayıcı zaten durdurulur, bu
    // yarış durumuna karşı ikinci kapıdır.
    if (state.tab !== 'devices') return;
    if (busy) return;
    refreshDevices({ silent: true });
  }, LIVE_MS);
}

function stopLive() {
  if (timers.live !== null) {
    window.clearInterval(timers.live);
    timers.live = null;
  }
}

function stopPairingTicker() {
  if (timers.pairing !== null) {
    window.clearInterval(timers.pairing);
    timers.pairing = null;
  }
}

async function createDevice() {
  const name = window.prompt('Yeni kasanın adı (örnek: Mutfak Kasası)', '');
  if (name === null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    toast('Kasa adı boş olamaz.', 'bad');
    return;
  }
  const reason = await askReason({
    title: 'Yeni kasa tanımla',
    description: `“${trimmed}” adıyla yeni bir kasa kaydı açılır ve bir eşleme kodu `
      + 'üretilir. Kod 10 dakika geçerlidir ve yalnız bir kez kullanılır.',
    confirmLabel: 'Kasayı tanımla',
    danger: false,
  });
  if (!reason) return;
  await withBusy('Kasa tanımlanıyor…', async () => {
    const result = await call(`${BASE}/devices`, {
      method: 'POST', body: writeBody({ name: trimmed, reason }),
    });
    if (announce(result, `“${trimmed}” tanımlandı.`)) {
      const code = result?.device?.pairing?.code || result?.pairing?.code;
      if (code) toast(`Eşleme kodu: ${code} — 10 dakika geçerli.`, 'good');
    }
    await refreshDevices({ silent: true });
  });
}

// ------------------------------------------------------------ kasa çekmecesi

async function openDevice(deviceId) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const onClose = () => { dropForms(); stopPairingTicker(); };
  // Çekmece KENDİ KENDİNE tazelenmiyor (açık form varken altından veri
  // değiştirmek, yazılmakta olan ayarı kaybettirirdi). Bağlantı koptuğunda
  // kullanıcının elinde bir tazeleme yolu olmalı; başlıktaki düğme o yol.
  let paint = null;
  const box = drawer(nodes.root, {
    title: 'Kasa yükleniyor…',
    subtitle: `#${deviceId}`,
    onClose,
    actions: [button('Yenile', {
      title: 'Kasa kaydını ve komut geçmişini yeniden oku',
      onClick: () => paint?.(),
    })],
  });
  closers.push(onClose);
  box.body.append(skeletonRows(6, 3));

  paint = async () => {
    dropForms();
    stopPairingTicker();
    box.body.replaceChildren(skeletonRows(6, 3));

    // Elde listeden kalan kayıt varsa çekmece onunla da çizilebilir; bağlantı
    // yokken bu kayıt BAYATTIR ve çekmecede öyle yazar.
    let device = state.devices.find((item) => String(item.id) === String(deviceId)) || null;
    let commands = [];
    let commandsRead = false;
    let stale = false;
    try {
      // Liste ucundan taze kayıt: çekmece açıkken listeyi tazeleyen zamanlayıcı
      // bu düğümleri değiştirmiyor, o yüzden ayrıca okunur.
      const devicePayload = await call(`${BASE}/devices`);
      if (linkOk(devicePayload)) {
        const fresh = listOf(devicePayload, 'devices')
          .find((item) => String(item.id) === String(deviceId));
        if (fresh) device = fresh;
      } else {
        stale = Boolean(device);
      }

      const commandPayload = await call(`${BASE}/devices/${deviceId}/commands`);
      if (linkOk(commandPayload)) {
        commands = listOf(commandPayload, 'commands');
        commandsRead = true;
      }
    } catch (error) {
      if (!device) {
        box.body.replaceChildren(emptyState({
          title: 'Kasa okunamadı',
          text: error.message || 'Bağlantı kurulamadı.',
          actions: [button('Kapat', { onClick: box.close })],
        }));
        return;
      }
      failed(error, 'Komut geçmişi okunamadı.');
    }

    if (!device) {
      box.body.replaceChildren(emptyState({
        title: state.link.connected ? 'Kasa bulunamadı' : 'Kasa okunamıyor',
        text: state.link.connected
          ? 'Kayıt listede yok. Liste tazelenmiş olabilir.'
          : `BLD sunucusuna ulaşılamıyor — ${state.link.error} Kasanın kaydı `
            + 'okunamadığı için çekmece açılamıyor.',
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }

    box.setTitle(device.name || `Kasa #${device.id}`);
    box.body.replaceChildren();
    paintDeviceDrawer(box, device, commands, forms, paint, { stale, commandsRead });
  };

  await paint();
}

function paintDeviceDrawer(box, device, commands, forms, repaint,
  { stale = false, commandsRead = true } = {}) {
  const revoked = Boolean(device.revoked_at);

  // ---------------------------------------------------------------- künye
  const head = h('div', 'bk-drawer-head');
  head.append(
    badge(device.online ? 'Çevrimiçi' : 'Çevrimdışı', device.online ? 'good' : 'bad'),
    revoked ? badge('İptal edildi', 'bad') : badge('Etkin', 'good'),
  );
  const seen = h('span', 'bk-sub',
    device.last_seen_at ? `son görülme ${ago(device.last_seen_at)}` : 'hiç görülmedi');
  if (device.last_seen_at) seen.title = stampIso(device.last_seen_at);
  head.append(seen);
  box.body.append(head);

  // BAĞLANTI UYARISI EN ÜSTTE. Altındaki "Çevrimiçi" rozeti bayat olabilir ve
  // kullanıcı bunu rozete bakmadan ÖNCE okumalı.
  const warning = linkAlert({ stale, what: 'Kasa kaydı' });
  if (warning) box.body.append(warning);

  if (revoked) {
    box.body.append(alertBox(
      `Bu kasa ${stampIso(device.revoked_at)} tarihinde iptal edildi ve sunucuya `
      + 'bağlanamaz. Kayıt geçmiş için duruyor; silinmez. Yeniden kullanmak için '
      + 'yeni bir kasa tanımlanmalıdır.', 'bad'));
  }

  // -------------------------------------------------------- sağlık uyarıları
  for (const warn of healthAlerts(device)) box.body.append(warn);

  // ------------------------------------------------------------------ ad
  //
  // Kasadaki ayarlar ekranında bunun karşılığı YOKTUR: ad Kontrol Merkezi'nin
  // kendi kaydıdır, kasaya hiç gitmez. Bu yüzden on bölümün dışında, üstte.
  box.body.append(nameCard(device, forms, repaint, revoked));

  // ------------------------- ayarlar ekranının aynası: on bölüm (K-22 §6)
  box.body.append(settingsCard(device, forms, repaint, revoked));

  // ------------------------------------------------------ fiş yeniden bas
  //
  // Ayrı kart, çünkü §6'daki on bölümün hiçbirinde yeri yok: bir AYAR ya da
  // kasa durumu değil, bir SİPARİŞ üzerinde yapılan iştir ve sipariş kimliği
  // ister.
  box.body.append(reprintCard(device, repaint, revoked));

  // -------------------------------------------------------- komut geçmişi
  box.body.append(commandHistoryCard(commands, commandsRead));
}

/**
 * Kasanın bildirdiği sağlıktan doğan uyarılar — çekmecenin EN ÜSTÜNDE.
 *
 * Hepsi "bu kasa şu an sipariş göremiyor / duyamıyor / basamıyor olabilir"
 * diyor ve altındaki ayar formundan ÖNCE okunmalı: ayar yazmaya gelen yönetici,
 * asıl sorunun ayar olmadığını görebilmeli.
 */
function healthAlerts(device) {
  const health = device.health || null;
  const out = [];
  if (!health) {
    out.push(alertBox(
      'Bu kasa hiç sağlık bildirimi göndermedi: yazıcı durumu, kuyruk sayıları ve '
      + 'sürüm bilinmiyor. Kasa henüz eşleşmemiş ya da hiç açılmamış olabilir.', 'info'));
    return out;
  }
  if (health.printer_ok === false) {
    out.push(alertBox(
      'Kasa YAZICININ ARIZALI olduğunu bildiriyor. Aygıt yolunu doğrulayın, sonra '
      + 'deneme fişi gönderip sonucu komut geçmişinden okuyun.', 'bad'));
  }
  // SES ARIZASI YAZICI ARIZASI KADAR CİDDİ: sesi olmayan mutfak siparişin
  // geldiğini duymaz ve ekrana bakmıyorsa hiç görmez.
  if (health.sound_ok === false) {
    out.push(alertBox(
      'Kasa SES ALTSİSTEMİNİN çalışmadığını bildiriyor. Yeni sipariş ve alarm '
      + 'sesleri çıkmıyor: mutfak siparişi yalnız ekrana bakarsa görür. Ses '
      + 'çıkışı adını (3. bölüm) doğrulayın.', 'bad'));
  }
  if (health.alarm_muted === true) {
    out.push(alertBox(
      'ALARM ŞU AN SUSTURULMUŞ'
      + (health.alarm_mute_reason ? ` — ${health.alarm_mute_reason}.` : '.')
      + ' Susturulduğu unutulan alarm, çalıştığı sanılan alarmdır: bağlantı '
      + 'koptuğunda ya da sipariş geciktiğinde kimse duymaz.', 'warn'));
  }
  if (health.last_error) {
    out.push(alertBox(`Kasanın bildirdiği son hata: ${health.last_error}`, 'warn'));
  }
  const stale = minutesSince(health.queue_oldest_at);
  if (stale !== null && stale >= QUEUE_STALE_MINUTES) {
    out.push(alertBox(
      `Kuyruktaki en eski iş ${ago(health.queue_oldest_at)} beklemeye başladı ve `
      + 'hâlâ basılmadı. Bekleyen iş SAYISI tek başına bunu göstermiyordu: kuyruk '
      + 'akmıyor demektir, önce yazıcıyı doğrulayın.', 'bad'));
  }
  return out;
}

/** Damganın üzerinden kaç dakika geçti; okunamazsa `null`. */
function minutesSince(iso) {
  if (!iso) return null;
  const ms = Date.parse(iso);
  if (!ms || Number.isNaN(ms)) return null;
  return (Date.now() - ms) / 60000;
}

/**
 * Eşleme kodu — 1. bölümün ("Sunucu") içinde durur.
 *
 * Kasadaki ayarlar ekranında da eşleme sunucu bölümünün altındadır: kod
 * girilene kadar kasanın sunucuyla hiçbir işi olamaz.
 */
function pairingBlock(device, repaint, revoked) {
  const body = h('div', 'bk-pairing');
  const pairing = device.pairing || null;

  if (pairing?.code && pairing.usable) {
    const row = h('div', 'bk-code-row');
    const code = h('code', 'bk-code', pairing.code);
    row.append(code);
    row.append(button('Kopyala', {
      title: 'Eşleme kodunu panoya kopyala',
      onClick: async () => {
        const done = await copyText(pairing.code);
        toast(done ? 'Eşleme kodu kopyalandı.' : 'Pano kullanılamadı.', done ? 'good' : 'warn');
      },
    }));
    body.append(row);

    const left = h('div', 'bk-left');
    const tick = () => {
      const info = remainingText(pairing.expires_at);
      left.textContent = info.text;
      left.classList.toggle('over', info.over);
      if (info.over) stopPairingTicker();
    };
    tick();
    stopPairingTicker();
    // Kalan süre canlı sayar; çekmece kapanınca ve panel kapanınca durur.
    timers.pairing = window.setInterval(tick, 1000);
    body.append(left);
    const expiry = h('span', 'bk-sub', `Son geçerlilik: ${stampIso(pairing.expires_at)}`);
    body.append(expiry);
  } else if (pairing?.code) {
    body.append(h('div', 'bk-left over', 'Kod kullanılmış ya da süresi dolmuş.'));
  } else {
    body.append(h('div', 'bk-sub',
      'Geçerli bir eşleme kodu yok. Kasa zaten eşleşmişse buna gerek de yoktur.'));
  }

  const actions = h('div', 'bk-actions');
  actions.append(writeButton('Yeni eşleme kodu üret', {
    blocked: revoked ? 'Kasa iptal edilmiş; iptal edilmiş bir kasa eşleşemez.' : '',
    title: 'Kod 10 dakika geçerlidir; yeni kod üretmek eskisini geçersiz kılar.',
    onClick: () => newPairingCode(device, repaint),
  }));
  body.append(actions);

  return body;
}

async function newPairingCode(device, repaint) {
  const reason = await askReason({
    title: 'Yeni eşleme kodu üret',
    description: `“${device.name}” için yeni bir kod üretilir ve VARSA ESKİ KOD `
      + 'GEÇERSİZ OLUR. Kasa o anda eşleşme ekranındaysa eski kodu artık kabul '
      + 'edilmez. Kod 10 dakika geçerlidir.',
    confirmLabel: 'Kod üret',
    danger: false,
  });
  if (!reason) return;
  await withBusy('Kod üretiliyor…', async () => {
    const result = await call(`${BASE}/devices/${device.id}/pairing-code`, {
      method: 'POST', body: writeBody({ reason }),
    });
    if (announce(result, 'Yeni eşleme kodu üretildi.')) {
      const code = result?.pairing?.code || result?.device?.pairing?.code;
      if (code) toast(`Eşleme kodu: ${code}`, 'good');
    }
    await repaint();
  });
}

function nameCard(device, forms, repaint, revoked) {
  const form = formGrid({
    fields: [{
      key: 'name',
      label: 'Kasa adı',
      type: 'text',
      required: true,
      maxLength: 120,
      wide: true,
      hint: 'Mutfakta hangi ekran olduğunu anlatan bir ad; fiş üstünde görünmez.',
    }],
    value: { name: device.name || '' },
  });
  forms.push(form);

  const actions = h('div', 'bk-actions');
  actions.append(writeButton('Adı kaydet', {
    variant: 'primary',
    blocked: revoked ? 'Kasa iptal edilmiş; kaydı değiştirilemez.' : '',
    onClick: async () => {
      if (!form.valid()) {
        form.showErrors();
        toast('Kasa adı zorunlu.', 'bad');
        return;
      }
      const patch = form.patch();
      if (!Object.keys(patch).length) {
        toast('Ad değişmedi.', 'warn');
        return;
      }
      const reason = await askReason({
        title: 'Kasa adını değiştir',
        description: `“${device.name}” → “${patch.name}”.`,
        confirmLabel: 'Adı kaydet',
        danger: false,
      });
      if (!reason) return;
      await withBusy('Ad kaydediliyor…', async () => {
        const result = await call(`${BASE}/devices/${device.id}`, {
          method: 'PATCH', body: writeBody({ name: patch.name, reason }),
        });
        announce(result, 'Kasa adı değişti.');
        await repaint();
      });
    },
  }));

  const body = h('div', 'bk-formbox');
  body.append(form.node, actions);
  return card('Kasa adı', body);
}

// ------------------------------------------------------------ 3. Ayarlar

/** Sunucudan gelen ayarları form taslağına çevirir: `null` → boş dize. */
function seedSettings(settings, fields) {
  const out = {};
  for (const field of fields) {
    const raw = settings?.[field.key];
    if (field.kind === 'events') {
      // ÜÇ HÂL, İKİ DEĞİL. `null` = dokunulmadı, `""` = hiçbiri kapalı değil,
      // dolu dize = kapalı olanların listesi. İlk ikisini onay kutuları
      // ayıramadığı için üstlerinde ayrı bir kip seçimi var.
      const managed = raw !== null && raw !== undefined;
      out[SOUND_MODE_KEY] = managed ? 'manage' : '';
      const off = new Set(managed
        ? String(raw).split(',').map((item) => item.trim()).filter(Boolean)
        : []);
      for (const event of SOUND_EVENTS) out[soundFieldKey(event.key)] = off.has(event.key);
      continue;
    }
    if (raw === null || raw === undefined) out[field.key] = '';
    else if (field.kind === 'bool') out[field.key] = String(raw);
    else out[field.key] = raw;
  }
  return out;
}

/**
 * Bölümün alanlarını `formGrid` alanlarına çevirir.
 *
 * 'events' TEK ALAN DEĞİL, ALTI ALANDIR: bir kip seçimi + beş onay kutusu.
 * Virgüllü dizeyi kullanıcıya elle yazdırmak, "newOrder" ile "new_order"
 * arasındaki farkı personelin ezberlemesini istemek olurdu.
 */
function sectionFields(fields, readOnly = false) {
  const out = [];
  for (const field of fields) {
    if (field.kind === 'events') { out.push(...soundEventFields(field)); continue; }
    out.push(settingsField(field));
  }
  // AYAR YAZMA YETKİSİ YOKSA ALANLAR SALT OKUNUR. Değerler görünmeye devam
  // eder — okumanın izni ayrı (`bld_kds.view`) ve "bu kasada yoklama kaç
  // saniyede" sorusu ayar yazmadan da sorulur. Kapatılan şey YAZMA
  // AFFORDANSIDIR: düzenlenebilir bir kutu, yazılabileceği sözünü verir.
  return readOnly ? out.map((field) => ({ ...field, readOnly: true })) : out;
}

function soundEventFields(field) {
  return [
    {
      key: SOUND_MODE_KEY,
      label: field.label,
      type: 'select',
      wide: true,
      options: [
        { value: '', label: '— dokunulmadı (kasa kendi listesini korur)' },
        { value: 'manage', label: 'Merkezden yönetiliyor (aşağıdaki kutular geçerli)' },
      ],
      hint: `${field.hint} “Dokunulmadı” seçiliyken kutular GÖNDERİLMEZ. `
        + '"Merkezden yönetiliyor" seçip hiçbir kutuyu işaretlememek, kasadaki '
        + 'BÜTÜN susturmaları kaldırır — bu ikisi ayrı isteklerdir.',
    },
    ...SOUND_EVENTS.map((event) => ({
      key: soundFieldKey(event.key),
      label: event.label,
      type: 'checkbox',
      hint: `İşaretliyse “${event.label}” sesi kasada ÇALMAZ.`,
    })),
    {
      key: `${SOUND_KEY}__note`,
      label: 'Bağlantı kopması alarmı',
      type: 'static',
      wide: true,
      hint: 'Listede yok ve KAPATILAMAZ. Kapatılabilseydi mutfak, sunucuyla '
        + 'bağlantının koptuğunu — yani siparişlerin hiç gelmediğini — duyacak '
        + 'son uyarıyı da kaybederdi.',
    },
  ];
}

/** Form alanına çevirir. Her alanda "boş = dokunulmadı" ipucu vardır. */
function settingsField(field) {
  const hint = `${field.hint} ${UNTOUCHED}`;
  if (field.kind === 'bool') {
    return {
      key: field.key,
      label: field.label,
      type: 'select',
      options: tri(field.on, field.off),
      hint,
      wide: Boolean(field.wide),
    };
  }
  if (field.kind === 'text') {
    return {
      key: field.key,
      label: field.label,
      type: 'text',
      maxLength: field.maxLength,
      placeholder: 'dokunulmadı',
      hint,
      wide: Boolean(field.wide),
    };
  }
  return {
    key: field.key,
    label: field.label,
    type: 'number',
    min: field.min,
    max: field.max,
    placeholder: 'dokunulmadı',
    hint,
    wide: Boolean(field.wide),
  };
}

/**
 * Beş onay kutusunu tele giden dizeye çevirir.
 *
 * @returns {string|null} `null` = dokunulmadı, `""` = hiçbiri kapalı olmasın
 */
function encodeSoundEvents(draft) {
  if (draft[SOUND_MODE_KEY] !== 'manage') return null;
  return SOUND_EVENTS
    .filter((event) => draft[soundFieldKey(event.key)] === true)
    .map((event) => event.key)
    .join(',');
}

/**
 * Kirli alanları tel biçimine çevirir.
 *
 * BOŞ DEĞER `null` OLARAK GİDER — "0" ya da "false" değil. Aradaki fark
 * ekranın tamamının anlamıdır: `null` yönetimden çıkarır, `false` kilitler.
 *
 * SES OLAYLARI TAM TASLAKTAN OKUNUR, kirli alanlardan değil: kullanıcı tek bir
 * kutuyu değiştirse bile tele giden şey LİSTENİN TAMAMIDIR. Yalnız değişeni
 * göndermek, dokunulmayan dört sesi sessizce açardı.
 */
function decodeSettingsPatch(patch, draft = {}) {
  const body = {};
  let soundTouched = false;
  for (const [key, value] of Object.entries(patch)) {
    if (key.startsWith(`${SOUND_KEY}__`)) { soundTouched = true; continue; }
    const field = SETTINGS_INDEX.get(key);
    if (!field) continue;
    if (value === '' || value === null || value === undefined) { body[key] = null; continue; }
    if (field.kind === 'bool') body[key] = value === true || value === 'true';
    else if (field.kind === 'int') body[key] = Number(value);
    else body[key] = String(value);
  }
  if (soundTouched) body[SOUND_KEY] = encodeSoundEvents(draft);
  return body;
}

/**
 * Değişikliği kullanıcıya İNSAN DİLİYLE özetler — onay ekranında okunacak.
 *
 * Uzun liste KIRPILIR: yirmi üç satırlık bir onay metni okunmaz, okunmayan
 * onay da onay değildir. Kaç alanın gizlendiği açıkça yazılır.
 */
function describeSettingsPatch(body, limit = 8) {
  const lines = [];
  for (const [key, value] of Object.entries(body)) {
    const field = SETTINGS_INDEX.get(key);
    if (!field) continue;
    let text;
    if (value === null) text = 'dokunulmadı (kasa kendi varsayılanını kullanır)';
    else if (field.kind === 'bool') text = value ? field.on : field.off;
    else if (field.kind === 'events') {
      // "disabled_sound_events: newOrder,bbdOrder" onay ekranında okunmuyordu.
      const names = String(value).split(',').filter(Boolean)
        .map((name) => SOUND_LABELS.get(name) || name);
      text = names.length
        ? `${names.join(', ')} SUSTURULACAK`
        : 'hiçbir ses kapalı olmayacak (hepsi çalacak)';
    } else text = String(value);
    lines.push(`${field.label}: ${text}`);
  }
  if (lines.length <= limit) return lines;
  const rest = lines.length - limit;
  return [...lines.slice(0, limit), `…ve ${rest} alan daha`];
}

/**
 * KASADAKİ AYARLAR EKRANININ AYNASI — on bölüm, aynı sırada (K-22 §6).
 *
 * Her bölüm kendi kartında durur, kendi ipucuyla açılır ve kendi düğmelerini
 * taşır. Yazma düğmesi TEK: bölümler ayrı formlar ama tek bir kısmi PATCH
 * gönderilir — üç bölümde birden değişiklik yapan yönetici üç kez gerekçe
 * yazmak zorunda kalmamalı.
 */
function settingsCard(device, forms, repaint, revoked) {
  const settings = device.settings || {};
  const body = h('div', 'bk-settings');
  // AYAR YAZMA AYRI İZİNDEDİR (`bld_kds.settings`, 17.08.2026 kullanıcı
  // kararı): sipariş durumunu ilerleten personel kasanın ayarına dokunmaz.
  // BURASI YETKİLENDİRME DEĞİL, GÖRÜNÜRLÜKTÜR — kapı `PATCH .../settings`
  // ucundadır (K9) ve bu bayrağı da o sunucu söylüyor.
  const yazabilir = state.canSettings;

  if (!yazabilir) {
    body.append(alertBox(
      'Kasa ayarlarını yazma yetkiniz yok (bld_kds.settings): alanlar SALT '
      + 'OKUNUR. Bu ekranın geri kalanı — sipariş durumu, revizyon, komutlar — '
      + 'açık kalır. Ayar yazma ayrı bir yetkidir çünkü yanlış bir yazıcı yolu '
      + 'fiş basımını tümüyle durdurur ve düzeltmesi yalnız merkezden gelir.',
      'warn'));
  }

  body.append(hintBox(
    'Bu bölümler kasanın AYARLAR EKRANIYLA AYNI SIRADADIR. Ayarlar KASADA '
    + 'uygulanır; boş bırakılan her alan "yönetici dokunmadı" demektir ve kasa '
    + 'kendi varsayılanıyla çalışır. Kilit politikasında bu ayrım kritiktir: boş '
    + 'bırakmak SERBEST demektir, kilit ancak açıkça "Kilitli" seçilince doğar. '
    + 'Yalnız DEĞİŞTİRDİĞİNİZ alanlar gönderilir.'));

  const sectionForms = [];
  for (const section of SETTINGS_SECTIONS) {
    const inner = h('div', 'bk-section');
    // İPUCU HER BÖLÜMÜN BAŞINDA (K-22 §6). Tek bir üst ipucu, sekiz bölüm
    // aşağıdaki kilit alanına gelindiğinde çoktan ekrandan çıkmış oluyordu.
    inner.append(hintBox(section.hint));

    if (section.fields.length) {
      const form = formGrid({
        fields: sectionFields(section.fields, !yazabilir),
        value: seedSettings(settings, section.fields),
      });
      forms.push(form);
      sectionForms.push(form);
      inner.append(form.node);
    }

    const block = sectionBlock(section, device, forms, repaint, revoked);
    if (block) inner.append(block);

    for (const node of sectionCommands(section, device, repaint, revoked)) {
      inner.append(node);
    }

    body.append(card(section.title, inner));
  }

  const actions = h('div', 'bk-actions');
  actions.append(writeButton('Ayarları kasaya yaz', {
    variant: 'primary',
    // Düğme KALDIRILMAZ, KAPATILIR ve nedenini söyler (kit README
    // §"blockedButton"): olmayan bir düğme, yöneticiye ekranın bozuk olduğunu
    // ya da ayarın hiç yazılamayacağını düşündürürdü. Yetki kimden istenir,
    // o yazıyor.
    blocked: !yazabilir
      ? 'Kasa ayarlarını yazmak ayrı yetki ister (bld_kds.settings); '
        + 'yöneticinizden isteyin. Sipariş durumu ve komutlar bu yetkiye bağlı '
        + 'değildir.'
      : (revoked ? 'Kasa iptal edilmiş; ayar yazmak etkisiz olurdu.' : ''),
    onClick: async () => {
      const broken = sectionForms.flatMap((form) => form.errors());
      if (broken.length) {
        sectionForms.forEach((form) => form.showErrors());
        toast(`${broken[0].message} Aralık dışındaki değer sunucuda da reddedilir.`, 'bad');
        return;
      }
      const merged = {};
      const draft = {};
      for (const form of sectionForms) {
        Object.assign(merged, form.patch());
        // TASLAĞIN TAMAMI da toplanır: ses olayları kirli alandan değil, beş
        // kutunun BÜTÜNÜNDEN okunur (bkz. `decodeSettingsPatch`).
        Object.assign(draft, form.draft());
      }
      const wire = decodeSettingsPatch(merged, draft);
      const keys = Object.keys(wire);
      if (!keys.length) {
        toast('Değişen ayar yok.', 'warn');
        return;
      }
      const lines = describeSettingsPatch(wire);
      const reason = await askReason({
        title: `${keys.length} ayar kasaya yazılacak`,
        description: `${device.name}: ${lines.join(' · ')}. Ayarlar kasa bir sonraki `
          + 'yoklamada aldığında yürürlüğe girer.',
        confirmLabel: 'Ayarları yaz',
        danger: false,
      });
      if (!reason) return;
      await withBusy('Ayarlar yazılıyor…', async () => {
        // AYARLAR `settings` ALTINDA YUVALANIR, kökte değil. Kökte olsalardı
        // `reason`/`dryRun` ile aynı ad alanını paylaşırlardı ve ileride
        // `reason` adında bir ayar eklenemezdi. `GET /devices` yanıtındaki
        // `device.settings` ile de simetriktir.
        const result = await call(`${BASE}/devices/${device.id}/settings`, {
          method: 'PATCH', body: writeBody({ settings: wire, reason }),
        });
        announce(result, `${keys.length} ayar yazıldı.`);
        await repaint();
      });
    },
  }));
  body.append(actions);

  if (device.settings_updated_at) {
    const line = h('div', 'bk-sub',
      `Son ayar yazımı: ${stampIso(device.settings_updated_at)}`);
    body.append(line);
  }

  return card('Kasanın ayarlar ekranı', body,
    '10 bölüm · 24 yönetilen alan · yalnız değişenler gönderilir');
}

// ------------------------------------------------- bölümlere özel içerik

/**
 * Bölüme özel SALT OKUNUR içerik. Yönetilen ayarı olmayan üç bölüm (Sunucu,
 * Kuyruk, Cihaz) ile ses telemetrisi buradan çizilir.
 */
function sectionBlock(section, device, forms, repaint, revoked) {
  if (section.block === 'server') return serverBlock(device, forms, repaint, revoked);
  if (section.block === 'sound') return soundBlock(device);
  if (section.block === 'queue') return queueBlock(device);
  if (section.block === 'device') return deviceBlock(device);
  return null;
}

/**
 * 1 · SUNUCU — adres SALT OKUNUR (K-22 §5), cihaz kimliği, eşleme.
 *
 * SUNUCU ADRESİ BİLEREK YÖNETİLMİYOR. Yanlış bir adres yazıldığı anda kasa
 * yeni adrese gider, oradan hiçbir şey alamaz ve DÜZELTMEYİ DE ALAMAZ — çünkü
 * düzeltme eski adresten gelecekti. Kasa sahada elle kurtarılana kadar sipariş
 * göremez; tek bir yazım hatasının mutfağı durdurduğu tek ayar budur. Gerekçe
 * ekranda yazar, çünkü alanı boş görüp "eksik kalmış" sanan biri onu eklemeye
 * çalışacaktır.
 */
function serverBlock(device, forms, repaint, revoked) {
  const box = h('div', 'bk-section-block');
  const pairing = device.pairing || null;
  // Adres kasanın kendi kaydıdır ve sunucu bu turda taşımıyor olabilir; yoksa
  // "bilinmiyor" yazılır, uydurulmaz.
  const address = device.server_url || device.health?.server_url || '';

  const form = formGrid({
    fields: [
      { key: 'server_url', label: 'Sunucu adresi', type: 'static', wide: true,
        hint: 'BURADAN DEĞİŞTİRİLEMEZ, bilerek. Yanlış bir adres yazıldığı anda '
          + 'kasa yeni adrese gider, oradan hiçbir şey alamaz ve DÜZELTMEYİ DE '
          + 'ALAMAZ — düzeltme eski adresten gelecekti. Kasa sahada elle '
          + 'kurtarılana kadar sipariş göremez. Adres değişikliği kasanın '
          + 'başında yapılır; merkezden yapılabilecek olan, kasadaki bu düğmeyi '
          + '8. bölümden KİLİTLEMEKTİR.' },
      { key: 'device_id', label: 'Cihaz kimliği', type: 'static',
        hint: 'Sunucudaki kayıt numarası. Fiş kuyruğu sekmesinde bu numarayla süzülür.' },
      { key: 'pairing_state', label: 'Eşleme durumu', type: 'static',
        hint: 'Kasa sunucuya cihaz token\'ıyla bağlanır; token eşleme kodundan doğar.' },
    ],
    value: {
      server_url: address || 'kasa bildirmiyor',
      device_id: `#${device.id}`,
      pairing_state: pairing?.usable
        ? 'Eşleme kodu bekliyor'
        : (pairing?.code ? 'Kod kullanılmış ya da süresi dolmuş' : 'Geçerli kod yok'),
    },
  });
  forms.push(form);
  box.append(form.node);
  box.append(pairingBlock(device, repaint, revoked));
  return box;
}

/** 3 · SES — alarmın ve ses altsisteminin ANLIK durumu (K-22 §3). */
function soundBlock(device) {
  const health = device.health || {};
  return factList([
    ['Ses altsistemi',
      health.sound_ok === null || health.sound_ok === undefined
        ? 'bilinmiyor' : (health.sound_ok ? 'Çalışıyor' : 'ÇALIŞMIYOR'),
      'Kasanın bildirdiği ses sağlığı. Çalışmıyorsa yeni sipariş ve alarm '
      + 'sesleri hiç çıkmaz.'],
    ['Alarm',
      health.alarm_muted === null || health.alarm_muted === undefined
        ? 'bilinmiyor'
        : (health.alarm_muted ? 'SUSTURULMUŞ' : 'Çalabilir'),
      'Susturulduğu unutulan alarm, çalıştığı sanılan alarmdır.'],
    ['Susturma nedeni', health.alarm_mute_reason || '—',
      'Kasanın alarmı neden susturduğu; personel susturmuş olabileceği gibi '
      + 'ses oynatıcı da bulunamamış olabilir.'],
  ]);
}

/** 9 · KUYRUK — sayı VE en eskisinin yaşı (K-22 §3). */
function queueBlock(device) {
  const health = device.health || {};
  const oldest = health.queue_oldest_at;
  return factList([
    ['Bekleyen fiş', num(health.print_queue_pending || 0),
      'Kasanın kendi disk kuyruğundaki basılmayı bekleyen iş sayısı.'],
    ['Hatalı fiş', num(health.print_queue_failed || 0),
      'Basılamamış ve hata almış işler. Düşürülürse bir daha basılmaz.'],
    ['En eski bekleyen iş', oldest ? ago(oldest) : '—',
      oldest
        ? `Kuyruğa giriş: ${stampIso(oldest)}. "3 bekleyen iş" yazıcı meşgulse `
          + 'normaldir; en eskisi yarım saattir bekliyorsa kuyruk AKMIYOR demektir.'
        : 'Kasa bu alanı bildirmiyor ya da kuyruk boş.'],
  ]);
}

/** 10 · CİHAZ — sürüm, son görülme, sağlık ayrıntısı. */
function deviceBlock(device) {
  const health = device.health || {};
  return factList([
    ['Sürüm', health.app_version || '—', 'Kasadaki uygulamanın bildirdiği sürüm.'],
    ['Son görülme', device.last_seen_at ? ago(device.last_seen_at) : 'hiç görülmedi',
      device.last_seen_at ? stampIso(device.last_seen_at) : ''],
    ['Son sağlık bildirimi', health.reported_at ? ago(health.reported_at) : '—',
      health.reported_at ? stampIso(health.reported_at) : 'Kasa hiç bildirim göndermedi.'],
    ['Yazıcı',
      health.printer_ok === null || health.printer_ok === undefined
        ? 'bilinmiyor' : (health.printer_ok ? 'Çalışıyor' : 'ARIZALI'),
      'Sağlık bildirmemiş cihaz ARIZALI SAYILMAZ; bilinmiyordur.'],
    ['Son hata', health.last_error || '—',
      'Kasanın bildirdiği son yazıcı/ağ hata metni.'],
  ]);
}

/** Etiket · değer · açıklama üçlülerinden künye kutusu. */
function factList(rows) {
  const box = h('div', 'bk-facts');
  for (const [label, value, title] of rows) {
    const line = h('div', 'bk-fact');
    line.append(h('span', 'bk-sub', label), h('b', undefined, String(value)));
    if (title) line.title = title;
    box.append(line);
  }
  return box;
}

// ------------------------------------------------------------- komutlar

/**
 * Bölümün komut düğmeleri — KASADAKİ DÜĞMENİN MERKEZDEKİ KARŞILIĞI.
 *
 * Düğmeler tek bir "Komutlar" kartında toplanmıyor artık: "deneme fişi" yazıcı
 * ayarının, "alarmı sustur" ses ayarının yanında duruyor. Aynı sorunun parçası
 * olan iki şeyi ekranın iki ucuna koymak, yöneticiyi ayar yazdıktan sonra
 * aşağı kaydırıp doğru düğmeyi aramaya zorluyordu.
 */
function sectionCommands(section, device, repaint, revoked) {
  const nodes = [];
  const blocked = revoked
    ? 'Kasa iptal edilmiş; komut kuyruğa atılsa da hiç teslim edilmez.' : '';
  const keys = section.commands || [];
  if (!keys.length && !section.revoke) return nodes;

  const row = h('div', 'bk-actions');
  for (const key of keys) {
    const command = COMMAND_BY_KEY.get(key);
    if (!command) continue;
    row.append(writeButton(command.label, {
      variant: command.danger ? 'danger' : '',
      title: command.description,
      blocked,
      onClick: () => sendCommand(device, command, {}, repaint),
    }));
  }

  // CİHAZ İPTALİ bir komut değil, bir SUNUCU kaydı işlemidir: kuyruğa girmez,
  // kasa çevrimdışıyken bile anında yürürlüğe girer. Yine de kasadaki "cihaz"
  // bölümünün karşılığı burasıdır ve yönetici onu burada arar.
  if (section.revoke && !revoked) {
    row.append(writeButton('Kasayı iptal et', {
      variant: 'danger',
      title: 'Kasa sunucuya bir daha bağlanamaz. Kayıt SİLİNMEZ, iptal edilmiş '
        + 'olarak kalır ve geri açılamaz.',
      onClick: () => revokeDevice(device, repaint),
    }));
  }
  nodes.push(row);

  if (keys.length) {
    nodes.push(hintBox(
      'Komutlar bir KUYRUĞA atılır; kasa bir sonraki yoklamada alır. Çevrimdışı bir '
      + 'kasa komutu ancak geri döndüğünde çalıştırır. Sonuç komut geçmişinde üç '
      + 'damgayla izlenir: oluşturuldu, iletildi, çalıştırıldı.'));
  }
  return nodes;
}

/**
 * Fiş yeniden basma — kendi kartında.
 *
 * On bölümün hiçbirinde yeri yok: bir ayar ya da kasa durumu değil, bir
 * SİPARİŞ üzerinde yapılan iştir ve sipariş kimliği ister.
 */
function reprintCard(device, repaint, revoked) {
  const body = h('div', 'bk-commands');
  const commandBlock = revoked
    ? 'Kasa iptal edilmiş; komut kuyruğa atılsa da hiç teslim edilmez.' : '';

  // `reprint` yük ister; düğme tek başına yeterli değil.
  const reprint = h('div', 'bk-reprint');
  const orderInput = h('input', 'kit-input');
  orderInput.type = 'text';
  orderInput.inputMode = 'numeric';
  orderInput.placeholder = 'Sipariş kimliği';
  orderInput.setAttribute('aria-label', 'Yeniden basılacak siparişin kimliği');

  const typeSelect = h('select', 'kit-select');
  typeSelect.setAttribute('aria-label', 'Fiş tipi');
  for (const type of REPRINT_TYPES) {
    const option = h('option', undefined, type.label);
    option.value = type.value;
    typeSelect.append(option);
  }

  reprint.append(h('span', 'kit-filter-label', 'Fiş yeniden bas'), orderInput, typeSelect);
  reprint.append(writeButton('Kuyruğa at', {
    title: 'Kasa bu siparişin fişini yeniden basar.',
    blocked: commandBlock,
    onClick: () => {
      const orderId = Number(String(orderInput.value).trim());
      if (!Number.isInteger(orderId) || orderId < 1) {
        toast('Sipariş kimliği bir tam sayı olmalı. Fiş kuyruğu sekmesinde yazılıdır.', 'bad');
        orderInput.focus();
        return;
      }
      sendCommand(device, {
        key: 'reprint',
        label: 'Fiş yeniden bas',
        danger: false,
        description: `${orderId} numaralı siparişin `
          + `${REPRINT_TYPES.find((item) => item.value === typeSelect.value)?.label} `
          + 'yeniden basılır.',
      }, { order_id: orderId, type: typeSelect.value }, repaint);
    },
  }));
  body.append(reprint);

  body.append(hintBox(
    'Sipariş kimliği Fiş kuyruğu sekmesinde yazılıdır. Komut bir KUYRUĞA atılır; '
    + 'kasa bir sonraki yoklamada alır ve fişi kendi yazıcısından basar. '
    + 'Kasadaki "elle yeniden bas" düğmesi 8. bölümden kilitlenmiş olsa bile bu '
    + 'yol AÇIKTIR: kilit personeli bağlar, merkezi değil.'));

  return card('Fiş yeniden bas', body,
    'Kasadaki düğmenin merkezdeki karşılığı · sipariş kimliği ister');
}

async function sendCommand(device, command, payload, repaint) {
  const reason = await askReason({
    title: command.label,
    description: `${device.name}: ${command.description}`,
    confirmLabel: command.danger ? command.label : 'Kuyruğa at',
    danger: command.danger,
  });
  if (!reason) return;
  await withBusy('Komut kuyruğa atılıyor…', async () => {
    const body = { command: command.key, reason };
    if (payload && Object.keys(payload).length) body.payload = payload;
    const result = await call(`${BASE}/devices/${device.id}/commands`, {
      method: 'POST', body: writeBody(body),
    });
    announce(result, `“${command.label}” kuyruğa atıldı.`);
    await repaint();
  });
}

function commandHistoryCard(commands, commandsRead = true) {
  if (!commandsRead) {
    // "Komut yok" DEMEK YASAK: geçmiş okunamadıysa kuyrukta bekleyen bir
    // `restart` olabilir ve yönetici ikinci kez göndermeye kalkar.
    return card('Komut geçmişi', emptyState({
      title: 'Komut geçmişi okunamıyor',
      text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Bu kasaya komut `
        + 'gönderilip gönderilmediği şu an BİLİNMİYOR.',
    }));
  }
  if (!commands.length) {
    return card('Komut geçmişi', emptyState({
      title: 'Komut yok',
      text: 'Bu kasaya henüz komut gönderilmedi.',
    }));
  }

  const table = dataTable({
    columns: [
      {
        key: 'command',
        label: 'Komut',
        width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('span', 'bk-stack');
          const known = COMMANDS.find((item) => item.key === row.command);
          box.append(h('b', undefined, known ? known.label : row.command));
          if (row.payload && typeof row.payload === 'object'
            && Object.keys(row.payload).length) {
            box.append(h('span', 'bk-sub', describeWould(row.payload)));
          }
          return box;
        },
      },
      { key: 'created_at', label: 'Oluşturuldu', width: 'minmax(0, 1fr)',
        cell: (row) => whenCell(row.created_at) },
      { key: 'delivered_at', label: 'İletildi', width: 'minmax(0, 1fr)',
        cell: (row) => whenCell(row.delivered_at, { empty: 'iletilmedi' }) },
      { key: 'executed_at', label: 'Çalıştırıldı', width: 'minmax(0, 1fr)',
        cell: (row) => whenCell(row.executed_at, { empty: 'çalıştırılmadı' }) },
      {
        key: 'succeeded',
        label: 'Sonuç',
        width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('span', 'bk-stack');
          if (row.executed_at === null || row.executed_at === undefined) {
            box.append(badge('Bekliyor', 'warn'));
          } else if (row.succeeded) {
            box.append(badge('Başarılı', 'good'));
          } else {
            box.append(badge('Başarısız', 'bad'));
          }
          if (row.result) box.append(clip(h('span', 'bk-sub'), row.result, 48));
          return box;
        },
      },
    ],
    rows: commands,
    dense: true,
  });

  return card('Komut geçmişi', table.node, 'En son 50 komut');
}

async function revokeDevice(device, repaint) {
  const reason = await askReason({
    title: 'Kasayı iptal et',
    description: `“${device.name}” sunucuya BİR DAHA BAĞLANAMAZ. Ekran başındaki `
      + 'personel siparişleri anında göremez olur. Kayıt silinmez, listede iptal '
      + 'edilmiş olarak kalır ve geri açılamaz — yeniden kullanmak için yeni bir '
      + 'kasa tanımlanmalıdır.',
    confirmLabel: 'Kasayı iptal et',
  });
  if (!reason) return;
  await withBusy('Kasa iptal ediliyor…', async () => {
    const result = await call(`${BASE}/devices/${device.id}/revoke`, {
      method: 'POST', body: writeBody({ reason }),
    });
    announce(result, `“${device.name}” iptal edildi.`);
    await repaint();
    await refreshDevices({ silent: true });
  });
}

// =========================================================== 4. Siparişler

function orderRows() {
  const values = nodes.orderFilters ? nodes.orderFilters.values() : {};
  // Sunucu tarafı süzgeç YALNIZ `include_completed` ve `since`; gerisi burada
  // süzülür ve durum satırı kaç kaydın süzüldüğünü yazar.
  let rows = applyFilters(state.orders, values, {
    q: { kind: 'search', fields: ['order_number', 'customer_name', 'customer_label', 'customer_phone'] },
    range: { kind: 'dateRange', field: 'created_at' },
  });
  if (state.orderChip) rows = rows.filter((row) => row.status === state.orderChip);
  return rows;
}

const ORDER_COLUMNS = [
  {
    key: 'order_number',
    label: 'Sipariş',
    width: 'minmax(0, 1.2fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(h('b', 'bk-code', row.order_number || `#${row.id}`));
      box.append(h('span', 'bk-sub', `#${row.id}`));
      return box;
    },
  },
  {
    key: 'status',
    label: 'Durum',
    width: '130px',
    sortable: true,
    cell: (row) => {
      const info = statusInfo(row.status);
      return badge(info.label, info.tone);
    },
  },
  {
    key: 'customer_name',
    label: 'Müşteri',
    width: 'minmax(0, 1.6fr)',
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(clip(h('b'), row.customer_name || row.customer_label || '—', 26));
      if (row.customer_phone) box.append(h('span', 'bk-sub', row.customer_phone));
      return box;
    },
  },
  {
    key: 'items',
    label: 'Kalem',
    width: '72px',
    align: 'num',
    sortable: true,
    sortValue: (row) => Number(row.item_count ?? row.items?.length ?? 0),
    cell: (row) => num(row.item_count ?? row.items?.length ?? 0),
  },
  {
    key: 'requested_at',
    label: 'İstenen zaman',
    width: 'minmax(0, 1.2fr)',
    cell: (row) => whenCell(row.requested_at, { empty: 'saat verilmedi' }),
  },
  {
    key: 'created_at',
    label: 'Girildi',
    width: 'minmax(0, 1.2fr)',
    sortable: true,
    cell: (row) => whenCell(row.created_at),
  },
  {
    key: 'revision_no',
    label: 'Revizyon',
    width: '104px',
    cell: (row) => {
      const count = Number(row.revision_no || 0);
      const box = h('span', 'bk-stack');
      box.append(count ? badge(`${num(count)}. revizyon`, 'warn') : h('span', 'bk-sub', 'yok'));
      if (row.is_subscription) box.append(badge('Abonelik', 'info'));
      return box;
    },
  },
];

function showOrders() {
  const host = nodes.body;
  host.replaceChildren();
  host.append(nodes.orderFilters.node, nodes.orderChips.node);

  nodes.orderAlert = h('div', 'bk-alertbox');
  host.append(nodes.orderAlert);

  nodes.orderWrap = h('div', 'bk-table');
  host.append(nodes.orderWrap);
  host.append(hintBox(
    'Tarih aralığının BAŞLANGICI sunucuya gider (`since`); arama, durum çipi ve '
    + 'aralığın bitişi ekrandaki kayıtlar üzerinde uygulanır — sipariş ucunda bu '
    + 'süzgeçler yoktur. "Tamamlananlar dâhil" kapalıyken sunucu yalnız aktif '
    + 'siparişleri verir.'));

  paintOrders();
  if (!state.ordersLoaded) {
    nodes.orderWrap.replaceChildren(skeletonRows(6, 7));
    refreshOrders();
  }
}

function paintOrders() {
  const wrap = nodes.orderWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  const warning = linkAlert({ stale: state.ordersStale, what: 'Sipariş listesi' });
  nodes.orderAlert?.replaceChildren(...(warning ? [warning] : []));

  const rows = orderRows();
  let empty;
  if (state.ordersError) {
    empty = emptyState({
      title: 'Siparişler okunamadı',
      text: state.ordersError,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshOrders() })],
    });
  } else if (!state.link.connected) {
    empty = emptyState({
      title: 'Sipariş listesi okunamıyor',
      text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Aktif sipariş olup `
        + 'olmadığı şu an BİLİNMİYOR; boş liste "sipariş yok" anlamına gelmez.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshOrders() })],
    });
  } else if (state.orderChip) {
    empty = emptyState({
      title: `“${statusInfo(state.orderChip).label}” durumunda sipariş yok`,
      text: 'Bu başlıkta bekleyen iş kalmadı.',
      actions: [button('Çipi kaldır', {
        onClick: () => { nodes.orderChips.set(null); state.orderChip = null; paintOrders(); },
      })],
    });
  } else {
    empty = emptyState({
      title: 'Süzgece uyan sipariş yok',
      text: 'Süzgeçleri gevşetin; "Tamamlananlar dâhil" kapalıysa yalnız aktif '
        + 'siparişler gelir.',
      actions: [button('Filtreyi temizle', { onClick: () => nodes.orderFilters.reset() })],
    });
  }

  nodes.orderTable = dataTable({
    columns: ORDER_COLUMNS,
    rows,
    empty,
    onRow: (row) => openOrder(row.id),
  });
  wrap.append(nodes.orderTable.node);

  const counts = {};
  for (const row of state.orders) counts[row.status] = (counts[row.status] || 0) + 1;
  nodes.orderChips.counts(counts);
  nodes.status?.set(`${num(rows.length)} / ${num(state.orders.length)} sipariş gösteriliyor `
    + `· ${statusText()}`, Boolean(state.ordersError) || !state.link.connected);
}

async function refreshOrders() {
  nodes.status?.set('Siparişler alınıyor…');
  await loadOrders();
  if (state.tab !== 'orders') return;
  paintOrders();
}

// ------------------------------------------------------- sipariş çekmecesi

async function openOrder(orderId) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: 'Sipariş yükleniyor…', subtitle: `#${orderId}`, onClose: dropForms,
  });
  closers.push(dropForms);
  box.body.append(skeletonRows(6, 3));

  const paint = async () => {
    dropForms();
    box.body.replaceChildren(skeletonRows(6, 3));

    let order = null;
    let revisions = [];
    let revisionsRead = false;
    try {
      const payload = await call(`${BASE}/orders/${orderId}`);
      // Bağlantı yoksa SİPARİŞ ÇEKMECESİ HİÇ AÇILMAZ: burada elde bayat bir
      // kopya yok (liste satırı sığdır) ve yarım künyeyle revizyon yazmak,
      // görülmeyen bir kalemi düşürmek demek olurdu.
      if (linkOk(payload)) {
        order = payload?.order || payload;
        const revisionPayload = await call(`${BASE}/orders/${orderId}/revisions`);
        if (linkOk(revisionPayload)) {
          revisions = listOf(revisionPayload, 'revisions');
          revisionsRead = true;
        }
      }
    } catch (error) {
      box.body.replaceChildren(emptyState({
        title: 'Sipariş okunamadı',
        text: error.message || 'Bağlantı kurulamadı.',
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }

    if (!state.link.connected) {
      box.body.replaceChildren(emptyState({
        title: 'Sipariş okunamıyor',
        text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Siparişin kalemleri `
          + 'okunamadığı için künye açılmıyor: eksik bir listeyle revizyon yazmak, '
          + 'görünmeyen kalemleri siparişten düşürürdü.',
        actions: [
          button('Tekrar dene', { variant: 'primary', onClick: () => paint() }),
          button('Kapat', { onClick: box.close }),
        ],
      }));
      return;
    }

    if (!order || !order.id) {
      box.body.replaceChildren(emptyState({
        title: 'Sipariş bulunamadı',
        text: 'Kayıt sunucuda yok ya da yanıt beklenen şekilde değil.',
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }

    box.setTitle(`${order.order_number || `#${order.id}`}`);
    box.body.replaceChildren();
    paintOrderDrawer(box, order, revisions, forms, paint, { revisionsRead });
  };

  await paint();
}

function paintOrderDrawer(box, order, revisions, forms, repaint,
  { revisionsRead = true } = {}) {
  const info = statusInfo(order.status);

  const head = h('div', 'bk-drawer-head');
  head.append(badge(info.label, info.tone));
  if (Number(order.revision_no || 0) > 0) {
    head.append(badge(`${num(order.revision_no)}. revizyon`, 'warn'));
  }
  if (order.is_subscription) head.append(badge('Abonelikten', 'info'));
  if (order.delivery_type) {
    head.append(badge(order.delivery_type === 'delivery' ? 'Adrese teslim' : 'Gel al', 'dim'));
  }
  box.body.append(head);

  const facts = h('div', 'bk-facts');
  for (const [label, value] of [
    ['Müşteri', order.customer_name || order.customer_label || '—'],
    ['Telefon', order.customer_phone || '—'],
    ['İstenen zaman', order.requested_at ? stampIso(order.requested_at) : 'saat verilmedi'],
    ['Girildi', order.created_at ? stampIso(order.created_at) : '—'],
  ]) {
    const line = h('div', 'bk-fact');
    line.append(h('span', 'bk-sub', label), h('b', undefined, String(value)));
    facts.append(line);
  }
  box.body.append(card('Sipariş', facts));

  if (order.customer_note) {
    box.body.append(card('Müşteri notu', h('div', 'bk-note', order.customer_note)));
  }

  if (order.is_subscription) {
    box.body.append(alertBox(
      'Bu sipariş bir ABONELİKTEN doğdu. Buradan yapılan revizyon YALNIZ BU GÜNÜ '
      + 'etkiler; aboneliğin kendisi değişmez.', 'info'));
  }

  box.body.append(itemsCard(order));
  box.body.append(revisionCard(order, forms, repaint));
  box.body.append(revisionHistoryCard(revisions, revisionsRead));
  box.body.append(statusCard(order, repaint));
}

function orderItems(order) {
  return Array.isArray(order.items) ? order.items : [];
}

/**
 * Seçenek kimliği eksik mi? — İKİ SUNUCU SÜRÜMÜNÜ DE TAŞIYAN KAPI.
 *
 * Revizyon ucu seçenek KİMLİĞİ ister (`items.*.option_value_ids`) ve sunucu
 * onu `?? []` ile okur: kimliksiz gönderilen kalem SEÇENEKSİZ fiyatlanır,
 * "ekstra peynir" sessizce düşer ve sipariş ucuzlar.
 *
 * Düzeltilmiş sunucu her kaleme `option_value_ids` ekliyor (adlar da kalıyor);
 * o zaman kimlikler olduğu gibi geri gönderilir ve seçeneğe dokunulmamış olur.
 * DÜZELTİLMEMİŞ sunucuda alan hiç gelmez — bu durumda revizyon AÇILMAZ.
 * İki yol da duruyor: panel sunucudan önce yayına girerse sessizce para
 * kaybettirmemeli.
 */
function optionsUnsafe(order) {
  return orderItems(order).some((item) => {
    const names = Array.isArray(item.options) ? item.options : [];
    const ids = Array.isArray(item.option_value_ids) ? item.option_value_ids : null;
    return names.length > 0 && (ids === null || ids.length === 0);
  });
}

function itemsCard(order) {
  const rows = orderItems(order);
  if (!rows.length) {
    return card('Kalemler', emptyState({
      title: 'Kalem yok',
      text: 'Siparişte görünen kalem kaydı bulunamadı.',
    }));
  }
  const table = dataTable({
    columns: [
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
      {
        key: 'options',
        label: 'Seçenekler',
        width: 'minmax(0, 1.6fr)',
        className: 'wrap',
        cell: (row) => (Array.isArray(row.options) && row.options.length
          ? row.options.join(', ') : '—'),
      },
      { key: 'quantity', label: 'Adet', width: '64px', align: 'num' },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)', className: 'wrap' },
    ],
    rows,
    dense: true,
    rowKey: (row) => String(row.order_menu_id ?? row.menu_id ?? ''),
  });
  return card('Kalemler', table.node,
    'Menü paketlerinin bileşen satırları burada görünmez — paket tek birimdir');
}

/**
 * Revizyon formu.
 *
 * Kalem FARKI değil TAM LİSTE gönderilir (sözleşme §2.5): burada bırakılan her
 * satır siparişin yeni hâlidir. Boş liste sunucuda reddedilir; siparişi
 * bitirmek durum ilerletmenin işidir.
 */
function revisionCard(order, forms, repaint) {
  const body = h('div', 'bk-revision');
  const source = orderItems(order);

  if (!source.length) {
    body.append(alertBox(
      'Kalemi olmayan bir siparişe revizyon yazılamaz: revizyon TAM LİSTE ister ve '
      + 'boş liste reddedilir.', 'info'));
    return card('Revizyon yaz', body);
  }

  const unsafe = optionsUnsafe(order);
  const draft = source.map((item) => ({
    menu_id: Number(item.menu_id),
    name: item.name,
    options: Array.isArray(item.options) ? item.options : [],
    option_value_ids: Array.isArray(item.option_value_ids) ? item.option_value_ids : null,
    quantity: Number(item.quantity) || 0,
    note: item.note || '',
    removed: false,
  }));
  const added = [];

  const list = h('div', 'bk-rev-list');
  const repaintList = () => {
    list.replaceChildren();
    draft.forEach((item, index) => paintRevisionRow(list, item, index, repaintList, false));
    added.forEach((item, index) => paintRevisionRow(list, item, index, () => {
      added.splice(index, 1);
      repaintList();
    }, true));
  };
  repaintList();
  body.append(list);

  // ---------------------------------------------------------- ürün ekle
  //
  // Revizyon kapalıysa hiç çizilmez: kullanıcıyı uygulanamayacak bir listeyi
  // kurmaya davet etmek, emeği çöpe atmaktır.
  if (!unsafe) {
    const adder = h('div', 'bk-rev-add-host');
    body.append(adder);
    mountProductAdder(adder, (item) => { added.push(item); repaintList(); });
  }

  const form = formGrid({
    fields: [
      { key: 'note', label: 'Revizyon notu (mutfağa)', type: 'textarea', maxLength: 1000,
        wide: true, hint: 'Mutfağın göreceği açıklama. İsteğe bağlı.' },
      { key: 'customer_note', label: 'Müşteri notu', type: 'textarea', maxLength: 1000,
        wide: true, hint: 'Siparişin müşteri notunu DEĞİŞTİRİR. Boş bırakılırsa '
          + 'mevcut not korunur.' },
    ],
    value: { note: '', customer_note: '' },
  });
  forms.push(form);
  body.append(form.node);

  const actions = h('div', 'bk-actions');
  if (unsafe) {
    actions.append(blockedButton('Revizyonu uygula',
      'Siparişte seçenekli kalem var ve sunucu seçenek KİMLİKLERİNİ vermiyor '
      + '(yalnız adları geliyor). Revizyon tam liste gönderir; kimliksiz gönderilen '
      + 'kalem seçeneksiz fiyatlanır ve "ekstra" satırları sessizce düşerdi.',
      { variant: 'primary' }));
    body.append(alertBox(
      'REVİZYON BU SİPARİŞTE KAPALI. Sunucu bu kalemlerin seçeneklerini yalnız ADIYLA '
      + 'veriyor, KİMLİĞİYLE değil; kimliksiz gönderilen kalem seçeneksiz fiyatlanır ve '
      + '"ekstra" satırları sessizce düşerdi. Sunucu tarafındaki düzeltme (kalem şekline '
      + '`option_value_ids` eklenmesi) yayına girdiğinde bu form kendiliğinden açılır — '
      + 'panelde yapılacak bir şey yoktur. O ana kadar seçenekli siparişler mutfak '
      + 'kasasından düzenlenir.', 'warn'));
  } else {
    actions.append(writeButton('Revizyonu uygula', {
      variant: 'primary',
      onClick: () => submitRevision(order, draft, added, form, repaint),
    }));
  }
  body.append(actions);

  body.append(hintBox(
    'Revizyon kalem FARKI değil, siparişin YENİ TAM HÂLİDİR: listede bıraktığınız '
    + 'her satır siparişte kalır, çıkardığınız satır düşer. Boş liste reddedilir — '
    + 'siparişi bitirmek için durum ilerletme kullanılır. Kayıt "Kontrol Merkezi" '
    + 'imzasıyla düşer, kasadan yapılan düzenlemeden ayrılır.'));

  return card('Revizyon yaz', body, 'Gerekçe zorunlu · tam liste gönderilir');
}

/**
 * "Ürün ekle" — katalog varsa ARANABİLİR SEÇİCİ, yoksa elle kimlik.
 *
 * İki yol da duruyor. `GET /menu` ucu 404 dönerse ekran çalışmayı sürdürür ve
 * neden seçici olmadığını yazar; kullanılamaz bir kutu bırakmak işi durdurmakla
 * aynı şeydir. Seçici tek seçimlidir: bir turda bir ürün eklenir ve eklenen
 * ürün listede görünür — toplu seçim, ne eklediğini kaybettirirdi.
 */
async function mountProductAdder(host, onAdd) {
  host.replaceChildren(h('div', 'bk-sub', 'Ürün listesi alınıyor…'));
  await loadMenu();
  host.replaceChildren();

  const qtyInput = h('input', 'kit-input');
  qtyInput.type = 'text';
  qtyInput.inputMode = 'numeric';
  qtyInput.value = '1';
  qtyInput.style.width = '76px';
  qtyInput.setAttribute('aria-label', 'Eklenecek adet');

  const noteInput = h('input', 'kit-input');
  noteInput.type = 'text';
  noteInput.maxLength = 255;
  noteInput.placeholder = 'Kalem notu (isteğe bağlı)';
  noteInput.setAttribute('aria-label', 'Kalem notu');

  /** Adedi okur; geçersizse söyler ve `null` döner. */
  const readQuantity = () => {
    const quantity = Number(String(qtyInput.value).trim());
    if (!Number.isInteger(quantity) || quantity < 1 || quantity > 999) {
      toast('Adet 1 ile 999 arasında bir tam sayı olmalı.', 'bad');
      qtyInput.focus();
      return null;
    }
    return quantity;
  };

  const push = (menuId, name) => {
    const quantity = readQuantity();
    if (quantity === null) return false;
    onAdd({
      menu_id: menuId,
      name,
      options: [],
      // Yeni kalemin seçeneği YOKTUR ve kimlik anahtarı hiç gönderilmez;
      // `null` bunu söyler (boş dizi "seçenekleri boşalt" demek olurdu).
      option_value_ids: null,
      quantity,
      note: noteInput.value.trim(),
      removed: false,
    });
    qtyInput.value = '1';
    noteInput.value = '';
    return true;
  };

  if (state.menu.length) {
    const picker = createPicker({
      items: state.menu.map((item) => ({
        id: item.menu_id,
        name: item.name,
        meta: item.price_kurus === null || item.price_kurus === undefined
          ? '' : money(item.price_kurus),
      })),
      placeholder: 'Ürün ara',
      single: true,
    });
    const pick = h('div', 'bk-rev-pick');
    pick.append(picker.node);

    const row = h('div', 'bk-rev-add');
    row.append(qtyInput, noteInput, button('Ürün ekle', {
      variant: 'primary',
      onClick: () => {
        const chosen = picker.selectedItems()[0];
        if (!chosen) {
          toast('Önce listeden bir ürün seçin.', 'bad');
          picker.focus();
          return;
        }
        if (push(Number(chosen.id), chosen.name)) picker.clear();
      },
    }));

    host.append(pick, row, hintBox(
      'Eklenen ürün SEÇENEKSİZ gider — seçenek seçimi bu turun kapsamında değil. '
      + 'Listedeki fiyat bilgi içindir; siparişin tutarını sunucu yeniden hesaplar.'));
    return;
  }

  const menuInput = h('input', 'kit-input');
  menuInput.type = 'text';
  menuInput.inputMode = 'numeric';
  menuInput.placeholder = 'Ürün kimliği (menu_id)';
  menuInput.setAttribute('aria-label', 'Eklenecek ürünün kimliği');

  const row = h('div', 'bk-rev-add');
  row.append(menuInput, qtyInput, noteInput, button('Ürün ekle', {
    onClick: () => {
      const menuId = Number(String(menuInput.value).trim());
      if (!Number.isInteger(menuId) || menuId < 1) {
        toast('Ürün kimliği bir tam sayı olmalı.', 'bad');
        menuInput.focus();
        return;
      }
      if (push(menuId, `Ürün #${menuId}`)) menuInput.value = '';
    },
  }));
  host.append(row);
  if (state.menuMissing) {
    host.append(hintBox('Ürün kataloğu ucu bu kurulumda yok, o yüzden aranabilir '
      + 'seçici çizilemedi ve ürün KİMLİKLE ekleniyor. Kimlik yanlışsa sunucu isteği '
      + 'reddeder — sessizce yanlış ürün eklenmez. Uç yayınlandığında seçici '
      + 'kendiliğinden gelir; panelde yapılacak bir şey yoktur.'));
  } else if (!state.link.connected) {
    host.append(alertBox(
      `Ürün listesi okunamıyor — ${state.link.error} Katalog önbelleğe ALINMADI: `
      + 'çekmece bir daha açıldığında liste yeniden denenir. Bu arada ürün kimlikle '
      + 'eklenebilir, ama revizyon zaten gönderilemez.', 'bad'));
  } else {
    host.append(alertBox(`Ürün listesi alınamadı — ${state.menuError} Ürün yine de `
      + 'kimlikle eklenebilir; kimlik yanlışsa sunucu isteği reddeder.', 'warn'));
  }
}

function paintRevisionRow(list, item, index, onChange, isNew) {
  const row = h('div', `bk-rev-row${item.removed ? ' removed' : ''}`);

  const label = h('span', 'bk-stack');
  label.append(clip(h('b'), item.name || `Ürün #${item.menu_id}`, 34));

  // SEÇENEKLER GÖRÜNÜR AMA DÜZENLENMEZ. Seçenek değiştirmek bu turun kapsamı
  // değil; kimlikler el değmeden geri gönderilir. Gizlemek olmazdı: personel
  // adedi neyin üzerinde değiştirdiğini görmeli ("Ekstra peynirli olan hangisi").
  if (item.options.length) {
    const opts = h('span', 'bk-rev-opts', item.options.join(' · '));
    opts.title = 'Seçenekler bu ekrandan değiştirilemez; olduğu gibi korunur.';
    label.append(opts);
  }

  const sub = [];
  if (isNew) sub.push('yeni kalem');
  sub.push(`menu_id ${item.menu_id}`);
  label.append(h('span', 'bk-sub', sub.join(' · ')));
  row.append(label);

  const qty = h('input', 'kit-input');
  qty.type = 'text';
  qty.inputMode = 'numeric';
  qty.value = String(item.quantity);
  qty.style.width = '72px';
  qty.disabled = item.removed;
  qty.setAttribute('aria-label', `${item.name} adedi`);
  qty.addEventListener('input', () => {
    const value = Number(String(qty.value).trim());
    item.quantity = Number.isInteger(value) ? value : 0;
    qty.classList.toggle('bad', !(Number.isInteger(value) && value >= 1 && value <= 999));
  });
  row.append(qty);

  const note = h('input', 'kit-input');
  note.type = 'text';
  note.maxLength = 255;
  note.value = item.note || '';
  note.placeholder = 'kalem notu';
  note.disabled = item.removed;
  note.setAttribute('aria-label', `${item.name} notu`);
  note.addEventListener('input', () => { item.note = note.value; });
  row.append(note);

  row.append(button(item.removed ? 'Geri al' : 'Çıkar', {
    variant: item.removed ? '' : 'ghost',
    title: item.removed
      ? 'Kalemi listeye geri koy'
      : 'Kalemi siparişten çıkar — revizyon uygulanana kadar kalıcı değil',
    onClick: () => {
      if (isNew) { onChange(); return; }
      item.removed = !item.removed;
      onChange();
    },
  }));

  list.append(row);
}

async function submitRevision(order, draft, added, form, repaint) {
  const all = [...draft.filter((item) => !item.removed), ...added];
  const bad = all.find((item) => !Number.isInteger(item.quantity)
    || item.quantity < 1 || item.quantity > 999);
  if (bad) {
    toast(`“${bad.name}” adedi 1 ile 999 arasında bir tam sayı olmalı.`, 'bad');
    return;
  }
  if (!all.length) {
    toast('Boş liste reddedilir. Siparişi bitirmek için durum ilerletmeyi kullanın.', 'bad');
    return;
  }

  const items = all.map((item) => {
    const line = { menu_id: item.menu_id, quantity: item.quantity };
    if (item.note) line.note = item.note;
    // SUNUCUDAN GELEN KİMLİK OLDUĞU GİBİ GERİ GİDER — boş dizi de dâhil.
    // Kaybın oluştuğu yer tam burasıydı: personel adede dokunurken seçeneğe
    // dokunmuyor, o yüzden seçenek listesi aynen korunmalı. Yeni eklenen
    // kalemde kimlik yoktur (`null`) ve anahtar hiç gönderilmez.
    if (Array.isArray(item.option_value_ids)) {
      line.option_value_ids = item.option_value_ids;
    }
    return line;
  });

  const removed = draft.filter((item) => item.removed);
  const changed = draft.filter((item) => !item.removed
    && item.quantity !== Number(orderItems(order).find(
      (row) => Number(row.menu_id) === item.menu_id)?.quantity || 0));

  const summary = [
    `${items.length} kalem kalacak`,
    removed.length ? `${removed.length} kalem çıkarılacak` : '',
    added.length ? `${added.length} kalem eklenecek` : '',
    changed.length ? `${changed.length} kalemin adedi değişecek` : '',
  ].filter(Boolean).join(' · ');

  const reason = await askReason({
    title: 'Revizyonu uygula',
    description: `${order.order_number || `#${order.id}`}: ${summary}. Sipariş YENİDEN `
      + 'FİYATLANIR, revizyon numarası artar ve mutfağa yeni fiş düşer. Geri alma '
      + 'yoktur; düzeltme ancak yeni bir revizyonla olur.',
    confirmLabel: 'Revizyonu uygula',
    max: REASON_MAX,
  });
  if (!reason) return;

  const draftValues = form.draft();
  await withBusy('Revizyon yazılıyor…', async () => {
    const body = { items, reason };
    if (String(draftValues.note || '').trim()) body.note = String(draftValues.note).trim();
    if (String(draftValues.customer_note || '').trim()) {
      body.customer_note = String(draftValues.customer_note).trim();
    }
    const result = await call(`${BASE}/orders/${order.id}/revisions`, {
      method: 'POST', body: writeBody(body),
    });
    announce(result, 'Revizyon yazıldı.');
    await repaint();
    await refreshOrders();
  });
}

function revisionHistoryCard(revisions, revisionsRead = true) {
  if (!revisionsRead) {
    // "Revizyon yok" DEMEK YASAK: sipariş daha önce değiştirilmiş olabilir ve
    // bunu görmeyen personel aynı düzeltmeyi ikinci kez yazar.
    return card('Revizyon geçmişi', emptyState({
      title: 'Revizyon geçmişi okunamıyor',
      text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Siparişin daha önce `
        + 'değiştirilip değiştirilmediği şu an BİLİNMİYOR.',
    }));
  }
  if (!revisions.length) {
    return card('Revizyon geçmişi', emptyState({
      title: 'Revizyon yok',
      text: 'Bu sipariş hiç değiştirilmedi.',
    }));
  }
  const table = dataTable({
    columns: [
      { key: 'revision_no', label: 'No', width: '56px', align: 'num' },
      { key: 'created_at', label: 'Zaman', width: 'minmax(0, 1.1fr)',
        cell: (row) => whenCell(row.created_at) },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)', className: 'wrap' },
      {
        key: 'refund_kurus',
        label: 'Para etkisi',
        width: 'minmax(0, 1fr)',
        cell: (row) => {
          const refund = Number(row.refund_kurus || 0);
          const extra = Number(row.extra_charge_kurus || 0);
          if (!refund && !extra) return h('span', 'bk-sub', 'yok');
          const box = h('span', 'bk-stack');
          if (refund) box.append(h('span', undefined, `İade ${money(refund)}`));
          if (extra) box.append(h('span', undefined, `Ek tahsilat ${money(extra)}`));
          return box;
        },
      },
    ],
    rows: revisions,
    dense: true,
    rowKey: (row) => String(row.revision_no),
  });
  return card('Revizyon geçmişi', table.node, 'Satır silinmez, üstüne yazılır');
}

function statusCard(order, repaint) {
  const known = Object.prototype.hasOwnProperty.call(NEXT_STATUS, order.status);
  const next = known ? NEXT_STATUS[order.status] : [];
  const body = h('div');

  if (!known) {
    // Sunucu tanımadığımız bir durum kodu döndürdü. "Son durum" demek yanlış
    // olurdu; ne olduğunu bilmediğimizi söylemek doğru olan.
    body.append(alertBox(
      `Siparişin durumu (“${order.status || 'boş'}”) bu ekranın tanıdığı yedi koddan `
      + 'biri değil. Hangi geçişlerin açık olduğu bilinemediği için düğme çizilmedi; '
      + 'sunucu yeni bir durum kodu yayınlamış olabilir.', 'warn'));
    return card('Durum', body);
  }

  if (!next.length) {
    body.append(alertBox(
      `Sipariş “${statusInfo(order.status).label}” durumunda ve bu durum SONDUR: `
      + 'buradan ilerletilemez. İptal, cari hesaba ters kayıt yazar ve geri alınması '
      + 'muhasebe düzeltmesidir — bu ekranın işi değildir.', 'info'));
    return card('Durum', body);
  }

  const row = h('div', 'bk-actions');
  for (const code of next) {
    const info = statusInfo(code);
    row.append(writeButton(info.label, {
      variant: code === 'iptal' ? 'danger' : 'primary',
      title: code === 'iptal'
        ? 'Sipariş iptal edilir; geri alınamaz.'
        : `Siparişi “${info.label}” durumuna geçir.`,
      onClick: () => advanceStatus(order, code, repaint),
    }));
  }
  body.append(row);
  body.append(hintBox(
    'Durum ilerletme mutfak akışını etkiler: fiş basımı ve müşteri bildirimi buna '
    + 'bağlıdır. Geri alma bu ekranda YOKTUR — kasadaki dar geri alma penceresi '
    + '(120 sn) yalnız dokunmatikte yanlışlıkla kaydırmak içindir.'));
  return card('Durum', body);
}

async function advanceStatus(order, code, repaint) {
  const info = statusInfo(code);
  const reason = await askReason({
    title: `Durumu “${info.label}” yap`,
    description: `${order.order_number || `#${order.id}`} siparişi `
      + `“${statusInfo(order.status).label}” → “${info.label}”. `
      + (code === 'iptal'
        ? 'İPTAL GERİ ALINAMAZ: cari hesaba ters kayıt düşer ve müşteri bilgilendirilir.'
        : 'Bu ekranda geri alma yoktur; yanlış geçiş ancak ileri adımlarla düzeltilir.'),
    confirmLabel: `“${info.label}” yap`,
    max: REASON_MAX,
  });
  if (!reason) return;
  await withBusy('Durum yazılıyor…', async () => {
    const result = await call(`${BASE}/orders/${order.id}/status`, {
      method: 'POST', body: writeBody({ status: code, reason }),
    });
    announce(result, `Durum “${info.label}” oldu.`);
    await repaint();
    await refreshOrders();
  });
}

// ========================================================== 5. Fiş kuyruğu

const JOB_COLUMNS = [
  {
    key: 'order_number',
    label: 'Sipariş',
    width: 'minmax(0, 1.2fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(h('b', 'bk-code', row.order_number || `#${row.order_id}`));
      box.append(h('span', 'bk-sub', `#${row.order_id}`));
      return box;
    },
  },
  {
    key: 'type',
    label: 'Tip',
    width: '128px',
    cell: (row) => {
      const known = REPRINT_TYPES.find((item) => item.value === row.type);
      return badge(known ? known.label : (row.type || '—'), 'dim');
    },
  },
  {
    key: 'revision',
    label: 'Revizyon',
    width: '96px',
    align: 'num',
    cell: (row) => (Number(row.revision || 0) ? num(row.revision) : 'ilk'),
  },
  {
    key: 'printed_at',
    label: 'Basıldığı an',
    width: 'minmax(0, 1.3fr)',
    sortable: true,
    cell: (row) => whenCell(row.printed_at, { empty: 'basılmadı' }),
  },
  {
    key: 'device_name',
    label: 'Kasa',
    width: 'minmax(0, 1.3fr)',
    cell: (row) => {
      const box = h('span', 'bk-stack');
      box.append(clip(h('b'), row.device_name || '—', 26));
      if (row.device_id) box.append(h('span', 'bk-sub', `#${row.device_id}`));
      return box;
    },
  },
];

function showJobs() {
  const host = nodes.body;
  host.replaceChildren();

  const bar = h('div', 'bk-actions');
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', 'Kasaya göre süz');
  const fill = () => {
    select.replaceChildren();
    const all = h('option', undefined, 'Tümü — kasa');
    all.value = '';
    select.append(all);
    for (const device of state.devices) {
      const option = h('option', undefined, device.name || `Kasa #${device.id}`);
      option.value = String(device.id);
      select.append(option);
    }
    select.value = String(state.jobDevice || '');
  };
  fill();
  // Kasa listesi fiş kayıtlarıyla birlikte gelir; kutu veri gelince yeniden
  // doldurulur — boş bir "Tümü" kutusu süzgeci kullanılamaz bırakırdı.
  nodes.jobDeviceFill = fill;
  select.addEventListener('change', () => {
    state.jobDevice = select.value;
    state.jobsLoaded = false;
    nodes.jobWrap.replaceChildren(skeletonRows(6, 5));
    refreshJobs();
  });
  bar.append(h('span', 'kit-filter-label', 'Kasa'), select,
    button('Yenile', { onClick: () => refreshJobs() }));
  host.append(bar);

  nodes.jobAlert = h('div', 'bk-alertbox');
  host.append(nodes.jobAlert);

  nodes.jobWrap = h('div', 'bk-table');
  host.append(nodes.jobWrap);
  host.append(hintBox(
    'BU BİR KUYRUK DEĞİL, DENETİM KAYDIDIR. Sunucuda basılmayı bekleyen fiş '
    + 'tutulmaz; kasa fişi kendi diskindeki kuyruktan basar ve bastığını buraya '
    + 'yazar. "Bekleyen" ve "hatalı" sayıları kasaların sağlık bildiriminden gelir '
    + 've Genel sekmesinde görünür.'));

  paintJobs();
  if (!state.jobsLoaded) {
    nodes.jobWrap.replaceChildren(skeletonRows(6, 5));
    refreshJobs();
  }
}

function paintJobs() {
  const wrap = nodes.jobWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  const warning = linkAlert({ stale: state.jobsStale, what: 'Fiş denetim kaydı' });
  nodes.jobAlert?.replaceChildren(...(warning ? [warning] : []));

  let empty;
  if (state.jobsError) {
    empty = emptyState({
      title: 'Fiş kaydı okunamadı',
      text: state.jobsError,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshJobs() })],
    });
  } else if (!state.link.connected) {
    empty = emptyState({
      title: 'Fiş kaydı okunamıyor',
      text: `BLD sunucusuna ulaşılamıyor — ${state.link.error} Fiş basılıp `
        + 'basılmadığı şu an BİLİNMİYOR; boş liste "fiş basılmadı" anlamına gelmez.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshJobs() })],
    });
  } else {
    empty = emptyState({
      title: 'Basılmış fiş yok',
      text: 'Seçilen kasada kayıtlı fiş bulunamadı. Kasa hiç fiş basmamış olabilir.',
    });
  }

  nodes.jobTable = dataTable({
    columns: JOB_COLUMNS,
    rows: state.jobs,
    empty,
    dense: true,
  });
  wrap.append(nodes.jobTable.node);
  nodes.status?.set(`${num(state.jobs.length)} fiş kaydı · ${statusText()}`,
    Boolean(state.jobsError) || !state.link.connected);
}

async function refreshJobs() {
  nodes.status?.set('Fiş kayıtları alınıyor…');
  if (!state.devicesLoaded) await loadDevices();
  await loadJobs();
  if (state.tab !== 'jobs') return;
  nodes.jobDeviceFill?.();
  paintJobs();
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel bk');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'overview', label: 'Genel' },
    { key: 'devices', label: 'Kasalar' },
    { key: 'orders', label: 'Siparişler' },
    { key: 'jobs', label: 'Fiş kuyruğu' },
  ], 'overview', (key) => showTab(key));

  // Sipariş süzgeçleri sekmeyle birlikte yok edilmez: filterBar global dinleyici
  // tutuyor (takvim) ve her sekme geçişinde yenisini kurmak onları biriktirirdi.
  nodes.orderFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px',
        placeholder: 'Sipariş no, müşteri, telefon' },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // dateRange kitin kendi takvimini kullanır.
      { kind: 'dateRange', key: 'range', label: 'Girildiği tarih', start: '', end: '' },
      { kind: 'toggle', key: 'includeCompleted', label: 'Tamamlananlar dâhil' },
    ],
    onChange: (values) => {
      // Sunucuya giden iki değer değiştiyse yeniden çekilir; gerisi istemcide.
      const serverSide = `${values.includeCompleted}|${values.range?.start || ''}`;
      if (serverSide !== nodes.lastServerFilter) {
        nodes.lastServerFilter = serverSide;
        refreshOrders();
        return;
      }
      paintOrders();
    },
    actions: [button('Yenile', { onClick: () => refreshOrders() })],
  });
  nodes.lastServerFilter = 'false|';

  nodes.orderChips = chipRow(
    STATUSES.map((item) => ({ key: item.key, label: item.label, count: 0 })),
    null,
    (key) => { state.orderChip = key; paintOrders(); },
  );

  nodes.status = statusLine();
  nodes.body = h('div', 'bk-body');

  // Şeritte artık yalnız sekmeler var: kuru prova şalteri kaldırıldı (K-22 §4)
  // ve yerine başka bir kip anahtarı KONMADI. Yazmanın emniyeti gerekçe kutusu
  // ve yıkıcı işlemlerin ayrı iznidir; ekranın tepesinde duran bir "gerçek mi
  // değil mi" şalteri, o iki kapıyı zayıflatan üçüncü bir kip yaratıyordu.
  const bar = h('div', 'bk-topbar');
  bar.append(nodes.tabs.node);

  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    // CANLI VERİ YALNIZ KASALAR SEKMESİNDE: kapalı sekme için istek üretmek,
    // 1200/saat oran sınırını boşuna yakar.
    if (key === 'devices') startLive(); else stopLive();
    ({
      overview: showOverview,
      devices: showDevices,
      orders: showOrders,
      jobs: showJobs,
    }[key] || showOverview)();
    nodes.status.set(statusText());
  }

  root.replaceChildren(view);
  showTab('overview');

  return () => {
    stopLive();
    stopPairingTicker();
    nodes.orderFilters?.destroy();     // takvim ve arama global dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
