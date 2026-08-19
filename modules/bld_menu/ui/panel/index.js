// Menü Yönetimi paneli — günlük menü takviminin Kontrol Merkezi'nden yönetimi.
//
// NE YAPAR: ay ızgarasında hangi güne ne girildiğini gösterir (yayında ·
// taslak · menü yok · kapalı gün · tükendi); seçilen günün başlığını,
// açıklamasını, iç notunu, paket fiyatını, kesim saatini ve bileşen satışını
// düzenler; kalem ekler, sırasını ve fiyat geçersiz kılmasını değiştirir,
// zorunlu/tek satılır işaretlerini kurar; günü yayınlar ve taslağa çeker;
// PAKET FİYATI GİRİLEN GÜNÜ KENDİLİĞİNDEN SATIŞA AÇAR (modül ayarı
// `auto_open_on_price`) ve takvim sütunundaki düğmeyle bunu bugünden ileriye
// toplu uygular;
// PORSİYON TAVANLARINI (gün toplamı + kalem başına) yazar; bir günü başka güne
// kopyalar.
//
// SATILAN ŞEY SABİT BİR KATALOG DEĞİL, GÜN GÜN GİRİLEN MENÜDÜR. Ürünlerin
// kendisi Ürün Kataloğu ekranındadır; burada yalnız "hangi gün, hangi üründen,
// kaç porsiyon, ne fiyata" sorusu vardır.
//
// NE YAPMAZ:
//  · ÜRÜN OLUŞTURMAZ. Seçici yalnız SATIŞTAKİ ürünleri listeler; satıştan
//    kalkmış bir ürünü içeren gün zaten yayınlanamıyor (`ITEM_UNAVAILABLE`).
//  · GÖRSEL YÜKLEMEZ. Sözleşmede günün görseli için bir yükleme ucu yok;
//    alan göreli bir YOL tutuyor ve burada yol olarak yazılıyor. Çalışmayan
//    bir yükleme düğmesi çizmek, kitin 1.2.0 notundaki hatayı tekrarlamak
//    olurdu.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//  · SİLDİĞİNİ GERİ GETİREMEZ — aşağıdaki "silme" notu.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · HAFTA SONU GRİ BOYANIR AMA SATIŞ KANALI AÇIKTIR (iş kuralı 3). Gri, ay
//    ızgarasının kendi boyasıdır ve "menü yok" demektir; "sipariş alınmaz"
//    demez. Cumartesi girilen bir sipariş Pazartesi'ye yazılır. Bu cümle
//    ekranda `hintBox` ile yazılı durur, çünkü gri hücre tek başına yanlış
//    okunmaya çok müsait.
//  · `null` TAVAN İLE SIFIR TAVAN AYRI ŞEYDİR. Boş bırakılan tavan
//    "sınırsız", sıfır "bugün satılmıyor" demektir. Ekran ikisini ayrı yazar;
//    "0/0" gibi bir gösterim ikisini birleştirirdi.
//  · KALAN NEGATİFE DÜŞMEZ. Tavan satılmışın altına çekilirse kalan `0`
//    görünür ve gerçek bilgiyi "fazla satılmış" rozeti taşır.
//  · `sold` REZERVEDİR, TESLİM DEĞİL, ve ABONELİK PAYI AYRI GÖSTERİLİR
//    (iş kararı 6): "34 porsiyon kaldı" diyen yönetici, bunun 20'sinin
//    aboneliğe ayrıldığını bilmek zorunda.
//  · YENİDEN YAYINLAMAK SAYAÇLARI SIFIRLAMAZ. Taslağa çekip tekrar yayınlamak
//    satılmış porsiyonları geri getirmez; tavan aynı sayının üstüne oturur.
//    Bu, hem yayın onayında hem stok sekmesinde yazılı.
//  · TAVAN TABLOSU TAM LİSTEDİR. Gönderilmeyen kalemin tavanı kalkar; bu
//    yüzden yazma İKİ ADIMLIDIR — önce kuru prova (uyarılarla), sonra jetonla
//    uygulama. Arada satış değişirse uygulama reddedilir ve tablo yeniden
//    hesaplatılır.
//  · SİLME GERİ ALINAMAZ ve ayrı yetki ister (`bld_menu.remove`). Yayından
//    çekmek ayrı bir iştir, geri alınabilir ve `bld_menu.manage`e düşer.
//  · GÜNE ÖZEL KESİM SAATİ BOŞ BIRAKILABİLİR; boşken küresel ayar geçerlidir
//    ve ekran o değeri yazar.
//  · GEREKÇE ARTIK HER YAZMADA SORULMAZ — aşağıdaki "gerekçe politikası".
//    Kalem eklemek, gün düzenlemek ve tavan yazmak doğrudan yazar; taahhüt
//    anları (yayınla · yayından çek · günü sil · kopyala) gerekçe ister.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_menu/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_menu/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, confirmSimple, confirmWithReason, h, isoOf, loadStyles, longDate, money,
  num, pollLoop, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { monthCalendar } from '../../ui-kit/calendar.js';
import { dateField } from '../../ui-kit/datefield.js';
import { formGrid } from '../../ui-kit/form.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, progress,
  skeletonRows, splitView, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { createPicker } from '../../ui-kit/picker.js';
import { dataTable } from '../../ui-kit/table.js';

const BASE = '/api/bld_menu';

// ------------------------------------------------------ gerekçe politikası
//
// GEREKÇE BU ALANDA HER YAZMADA SORULMAZ. Yalnız iki şartı BİRDEN taşıyan
// uçlarda sorulur: sonucu müşteriye görünür hâle gelir VE geri alınması
// zordur. Bu tanım dört ucu seçer ve `BLD/docs/control/menu.md` → "Gerekçe
// politikası" bölümü bağlayıcıdır:
//
//     POST   days/{date}/publish      EVET   müşteri o güne sipariş verebilir
//     POST   days/{date}/unpublish    EVET   satış kanalından düşer
//     DELETE days/{date}              EVET   gün + kalemleri geri alınamaz gider
//     POST   days/{date}/duplicate    EVET   toplu ve hedefin üzerine yazabilir
//
//     POST/PATCH days                 hayır  taslak kurmak taahhüt değil
//     POST/PATCH/DELETE .../items     hayır  kalem girmek menü yazmaktır
//     PUT    days/{date}/stock        hayır  tavan yeniden yazılabilir
//
// NEDEN GEVŞEDİ: bir güne beş ürün koymak beş kez on karakterlik gerekçe
// demekti ve ürettiği metinler "düzeltme", "ok", "asdasd" oldu — yani sınırın
// engellemek için var olduğu şeyin ta kendisi. Az yerde istenen gerekçe, çok
// yerde istenenden daha değerlidir: dört satırlık bir denetim izi okunur,
// dört yüz satırlık bir "düzeltme" listesi okunmaz.
//
// GEVŞEMEYEN: `actor` her yazmada gider (oturumdan, gövdeden değil) ve denetim
// satırı her yazmada açılır. Seyrekleşen tek soru "neden".
//
// YIKICI AMA GEREKÇESİZ olan tek iş kalem silmedir: onay YİNE İSTENİR
// (`confirmSimple` + `danger`), yalnız gerekçe kutusu kalkar. Kitin 8. kuralı
// "yıkıcı işlem onaysız geçmez" der; gerekçe o onayın bir alanıydı, kendisi
// değil.

/** Gerekçe alt sınırı — sunucu da denetliyor (`00-genel.md` §3). */
const REASON_MIN = 10;

/** Panel uçlarında gerekçe üst sınırı. Sipariş revizyonundaki 160 BURADA GEÇMEZ. */
const REASON_MAX = 500;

/** Takvim tazeleme aralığı; sunucu ayarı gelene kadar geçerli yedek. */
const FALLBACK_REFRESH_MS = 120000;

const EMPTY_STATE = {
  month: '',
  selected: '',
  rows: [],
  byDate: {},
  meta: {},
  summary: {},
  contract: null,
  day: null,
  exists: false,
  stock: null,
  //: Stok HANGİ GÜN için okundu. Boş = hiç okunmadı.
  //:
  //: STOK ARTIK GÜN AÇILIŞINDA ÇEKİLMİYOR. Her gün seçiminde `/days/{iso}` ile
  //: BİRLİKTE `/days/{iso}/stock` de isteniyordu; oysa `state.stock` ekranda
  //: TEK YERDE kullanılıyor: "Stok" sekmesi. Yani kullanıcı Kalemler
  //: sekmesinde çalışırken her gün değişiminde boşuna ikinci bir istek
  //: gidiyordu — masaüstü → KM sunucusu → BLD sunucusu, iki atlamalı bir yol.
  //: Gün açılışı böylece iki istekten bire indi.
  stockFor: '',
  stockError: '',
  products: [],
  productsError: '',
  tab: 'day',
  preview: null,
  //: HENÜZ KAYDEDİLMEMİŞ kalemler. Kutudan seçilen ürün önce buraya düşer,
  //: tabloda "kaydedilmedi" rozetiyle görünür ve sunucuya YALNIZ "Kalemleri
  //: kaydet" denince gider.
  //:
  //: NEDEN BÖYLE: her seçim anında tek tek POST atılıyordu. Beş kalemlik bir
  //: menü beş ayrı yazma, beş ayrı gün tazelemesi ve beş ayrı takvim
  //: tazelemesi demekti; arada biri patlarsa gün yarım kalıyordu ve hangi
  //: kalemin girdiği ekrandan okunamıyordu. Kullanıcı da istediğini böyle
  //: tarif etti: "yazıp seçeyim, otomatik aşağıya gitsin, bitince en son
  //: kaydedilsin."
  draft: [],
  //: BU OTURUMDA hızlı ekleme kutusundan eklenmiş kalem kimlikleri. Yalnız
  //: onlar tek tıkla geri alınır: yanlış tuşla eklenmiş, üzerinde henüz hiçbir
  //: şey düzenlenmemiş bir kalemi silmek için onay penceresi açmak, hızlı
  //: eklemenin kazandırdığı süreyi geri alırdı. Eski bir kalemi silmek onay
  //: ister — orada kaybolan gerçek emek var.
  //:
  //: DİZİ, KÜME DEĞİL ve HİÇ YERİNDE DEĞİŞTİRİLMEZ: `{ ...EMPTY_STATE }` sığ
  //: kopya yapıyor, yani yerinde değiştirilen bir küme/dizi bütün mount'lar
  //: arasında paylaşılırdı. Her yazma yeni dizi üretir.
  justAdded: [],
  connected: true,
  error: '',
};

let api = null;
let toast = null;
let state = { ...EMPTY_STATE };
let busy = false;
let poll = null;
//: Panel hâlâ ekranda mı. İlk takvim yanıtı gelmeden panel kapatılırsa,
//: yanıtın ardından kurulacak yoklama döngüsü SAHİPSİZ kalır ve `cleanup`
//: onu durduramaz — kapalı bir panel sunucuyu yoklamaya devam ederdi.
let alive = false;
const nodes = {};
const closers = [];

// ------------------------------------------------------------------ ağ

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * `{ok: false, error}` gövdesi (servis istisna fırlatmıyor, K7). İkincisini de
 * fırlatılır hâle getirmek, çağıran yerlerde tek bir `try` bırakır.
 */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) {
    const raw = result.error;
    const message = typeof raw === 'string' ? raw : (raw.message || 'İşlem başarısız.');
    const error = new Error(message);
    error.code = result.code || (typeof raw === 'string' ? '' : (raw.code || ''));
    throw error;
  }
  return result;
}

/**
 * Gerekçeli onay — YALNIZ DÖRT ÇAĞRI YERİ vardır ve hepsi bir taahhüttür:
 * `doPublish`, `doUnpublish`, `doDeleteDay`, `openDuplicate`. Listenin
 * gerekçesi dosya tepesindeki "gerekçe politikası" bölümündedir.
 *
 * Beşinci bir çağrı yeri eklemek, politikayı sunucudaki tabloyla (ve
 * `docs/control/menu.md` ile) çelişkiye düşürür: orada gerekçe istenmeyen bir
 * uca burada gerekçe sormak, kullanıcıdan hiçbir yere yazılmayan bir metin
 * istemek olurdu.
 */
function askReason({ title, description, confirmLabel, danger = false }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN}, en çok ${REASON_MAX} karakter)`,
  });
}

/**
 * Gerekçesiz ama YIKICI işlemin onayı.
 *
 * Kitin 8. kuralı yıkıcı işlemin onaydan geçmesini istiyor; gerekçe o onayın
 * bir ALANIYDI, kendisi değil. Alan kalkınca onay penceresi kalır — silinen
 * şeyin ne olduğu ve geri gelmeyeceği yine ekranda yazar.
 */
function confirmDestructive({ title, description, confirmLabel }) {
  return confirmSimple(nodes.root, { title, description, confirmLabel, danger: true });
}

/**
 * Yazma sarmalayıcı: aynı anda tek yazma, hata mesajı, tazeleme.
 *
 * `dry_run` GÖNDERİLMEZ ve bu bilinçli: uç bayrağı kabul ediyor ama panel onu
 * hiç kullanmıyor, yani buradan yapılan her yazma gerçektir. Yanıttaki
 * `dry_run` yine de OKUNUYOR — bir kurulum geçidin varsayılanını açık
 * bırakırsa ekran "kaydedildi" DEMEMELİ.
 */
async function write(path, body, { success, after, expectDryRun = false } = {}) {
  if (busy) return null;
  busy = true;
  nodes.status.set(expectDryRun ? 'Hesaplanıyor…' : 'Yazılıyor…');
  try {
    const result = await call(path, { method: body.method || 'POST', body: body.payload });
    // `expectDryRun` YALNIZ stok önizlemesi içindir: orada kuru prova İSTENEN
    // sonuçtur. Ötekilerde `dry_run: true` bir arızadır — panel bayrağı hiç
    // göndermiyor, yani geçit ya da sunucu isteği kendiliğinden provaya
    // çevirmiş demektir ve ekran "kaydedildi" DEMEMELİ.
    if (result?.dry_run && !expectDryRun) {
      toast('Sunucu isteği KURU PROVA olarak işledi; hiçbir şey yazılmadı. '
        + 'Geçit ayarını denetleyin.', 'warn');
    } else if (success) {
      toast(success, 'good');
    }
    await after?.(result);
    return result;
  } catch (failure) {
    // `conflict` "tazele ve tekrar sor" demektir (`00-genel.md` §7.2); ham 409
    // göstermek yerine ekranı tazeliyoruz ki kullanıcı güncel hâli görsün.
    toast(failure.message, 'bad');
    if (failure.code === 'conflict') await reloadDay({ quiet: true });
    return null;
  } finally {
    busy = false;
    nodes.status.set(statusText());
  }
}

// -------------------------------------------------------------- yardımcı

function statusText() {
  if (!state.connected) return state.error || 'BLD sunucusuna ulaşılamıyor.';
  const summary = state.summary || {};
  const parts = [
    `${num(summary.published || 0)} yayında`,
    `${num(summary.draft || 0)} taslak`,
    `${num(summary.missing || 0)} menü yok`,
  ];
  if (summary.closed) parts.push(`${num(summary.closed)} kapalı gün`);
  if (summary.sold_out) parts.push(`${num(summary.sold_out)} tükendi`);
  return `Bağlı · ${parts.join(' · ')}`;
}

/** Küresel kesim saati — gün alanı boşken geçerli olan değer. */
function globalCutoff() {
  return state.meta?.default_cutoff_time || '—';
}

/** Ay ızgarasının 42 hücresinin tam aralığı.
 *
 * Takvimle AYNI hesabı yapar (pazartesi başlangıç, sabit 42 hücre): eksik bir
 * aralık, ızgaranın kenarındaki günleri "menü girilmemiş" diye gösterirdi ve
 * yönetici o günlere ikinci kez menü kurmaya çalışırdı.
 */
function gridRange(monthKey) {
  const year = Number(monthKey.slice(0, 4));
  const month = Number(monthKey.slice(5, 7));
  const first = new Date(year, month - 1, 1);
  const lead = (first.getDay() + 6) % 7;
  const start = new Date(first);
  start.setDate(1 - lead);
  const end = new Date(start);
  end.setDate(start.getDate() + 41);
  return { from: isoOf(start), to: isoOf(end) };
}

function monthOf(iso) {
  return String(iso || todayIso()).slice(0, 7);
}

/** Tavan/kalan çifti — `null` "sınırsız", sıfır "doldu". Karıştırılmaz. */
function capacityText(capacity, remaining) {
  if (capacity === null || capacity === undefined) return 'sınırsız';
  return `${num(remaining ?? 0)} / ${num(capacity)}`;
}

/** `card()` çocuklarına boşluk vermiyor; forma kendi sarmalayıcısını verir. */
function padded(node) {
  const box = h('div', 'bm-formbox');
  box.append(node);
  return box;
}

/** Bu oturumda hızlı eklenmiş bir kalem mi (tek tıkla geri alınır). */
function isJustAdded(itemId) {
  return state.justAdded.includes(itemId);
}

/** Hızlı ekleme izini — dizi HER ZAMAN yeniden üretilir (`EMPTY_STATE` notu). */
function markJustAdded(itemId) {
  if (!isJustAdded(itemId)) state.justAdded = [...state.justAdded, itemId];
}

function forgetJustAdded(itemId) {
  state.justAdded = state.justAdded.filter((id) => id !== itemId);
}

/** Gün değişti: "az önce ekledim" izi o güne aitti, yenisine taşınmaz. */
function clearJustAdded() {
  state.justAdded = [];
}

// ------------------------------------------------------------- takvim

function badgeFor(iso, info) {
  // ROZETSİZ HÜCRE BİR BİLGİDİR. "Menü girilmemiş" için uydurma bir rozet
  // çizmek, ızgaranın en çok görülmesi gereken durumunu ötekilerle aynı görsel
  // ağırlığa indirirdi; açıklama listesinde "rozet yok = menü girilmemiş"
  // yazıyor ve boş hücre o cümleyle okunuyor.
  if (!info || !info.has_menu) return null;
  const box = h('span', 'bm-cell');
  const tone = info.sold_out ? 'bad' : (info.status === 'published' ? 'good' : 'warn');
  const label = info.sold_out ? 'Tükendi'
    : (info.status === 'published' ? 'Yayın' : 'Taslak');
  box.append(h('span', `bm-cell-tag ${tone}`, label));
  if (info.capacity_total !== null && info.capacity_total !== undefined) {
    box.append(h('span', 'bm-cell-num',
      `${num(info.remaining_total ?? 0)}/${num(info.capacity_total)}`));
  } else if (info.item_count) {
    box.append(h('span', 'bm-cell-num', `${num(info.item_count)} kalem`));
  }
  return box;
}

function dayTitleFor(iso, info) {
  if (!info) return '';
  if (!info.has_menu) return `${longDate(iso)} — menü girilmemiş`;
  const lines = [`${longDate(iso)} — ${info.status_label || ''}`];
  if (info.title) lines.push(info.title);
  if (info.package_price_kurus !== null && info.package_price_kurus !== undefined) {
    lines.push(`Paket: ${money(info.package_price_kurus)}`);
  }
  if (info.capacity_total !== null && info.capacity_total !== undefined) {
    lines.push(`Kalan: ${num(info.remaining_total ?? 0)} / ${num(info.capacity_total)} porsiyon`);
  }
  if (!info.orderable && info.not_orderable_label) {
    lines.push(`Sipariş alınmıyor: ${info.not_orderable_label}`);
  }
  return lines.join('\n');
}

async function reloadCalendar({ quiet = false } = {}) {
  const monthKey = state.month || monthOf(todayIso());
  const range = gridRange(monthKey);
  try {
    const result = await call(`${BASE}/calendar?date_from=${range.from}&date_to=${range.to}`);
    state.connected = result.connected !== false;
    state.error = result.error || '';
    state.rows = result.items || [];
    state.meta = result.meta || {};
    state.summary = result.summary || {};
    // SÖZLEŞME YEREL VE HER YANITTA GELİYOR (K7): geçit düşse bile rozetler,
    // açıklama listesi ve alan sınırları çizilebilsin diye servis onu okuma
    // hatasında da yolluyor. Bu yüzden `??` ile korunur — bir sonraki başarısız
    // tazeleme, elimizdeki sözleşmeyi silmemeli.
    state.contract = result.limits
      ? { limits: result.limits,
          refresh_seconds: result.refresh_seconds,
          // Otomatik satışa açma AYARDAN gelir, panelde kopyası tutulmaz:
          // iki yerde duran bir bayrak, kapatıldığı hâlde çalışmaya devam
          // eden bir otomatik yayın demek olurdu.
          auto_open_on_price: result.auto_open_on_price !== false }
      : state.contract;
    state.byDate = Object.fromEntries(state.rows.map((row) => [row.date, row]));
  } catch (failure) {
    state.connected = false;
    state.error = failure.message;
    if (!quiet) toast(failure.message, 'bad');
  }

  const closed = {};
  for (const row of state.rows) {
    if (row.closed) closed[row.date] = row.closed_reason || 'Satışa kapalı gün';
  }
  nodes.calendar.update({ days: state.byDate, holidays: closed,
    selected: state.selected || null });
  nodes.summary.replaceChildren(summaryRow());
  nodes.status.set(statusText());
  if (nodes.refreshHint) {
    nodes.refreshHint.textContent = state.connected
      ? '' : 'Veri eski olabilir: son tazeleme başarısız.';
  }
}

/**
 * Takvim hücresini AĞA ÇIKMADAN günceller — YALNIZ kalem sayısı için.
 *
 * NEDEN VAR: kalem eklemek/silmek takvimde tek bir günün kalem sayısını
 * değiştiriyor, ama her yazmadan sonra 42 GÜNLÜK ızgara yeniden çekiliyordu.
 * Yol kısa değil: masaüstü → KM sunucusu → BLD sunucusu, iki atlama. Beş
 * kalemlik bir menüde bu beş gereksiz tam takvim isteği demekti ve ekranın
 * "çok yavaş" hissini büyük ölçüde bu üretiyordu.
 *
 * DURUM (yayında/taslak) BURADA UYDURULMAZ. Onu yalnız yayın/yayından çekme
 * değiştirir ve o akışlar takvimi gerçekten yeniden çeker. Yoklama döngüsü de
 * kalan her şeyi kendiliğinden düzeltir — yani en kötü ihtimalle bir rozet
 * bir tur geç tazelenir, yanlış bir şey gösterilmez.
 */
function patchCalendarDay() {
  const iso = state.selected;
  const row = state.byDate[iso];
  if (!row || !nodes.calendar) return;
  // `state.rows` ile `state.byDate` AYNI nesneleri paylaşıyor (`byDate` ondan
  // türetiliyor); satırı değiştirmek ikisini birden günceller.
  row.item_count = state.day?.item_count ?? row.item_count;

  const closed = {};
  for (const item of state.rows) {
    if (item.closed) closed[item.date] = item.closed_reason || 'Satışa kapalı gün';
  }
  nodes.calendar.update({ days: state.byDate, holidays: closed, selected: iso });
  nodes.status.set(statusText());
}

function summaryRow() {
  const summary = state.summary || {};
  return kpiRow([
    { label: 'Yayında', value: num(summary.published || 0), tone: 'good',
      title: 'Bu ayda müşteriye görünen gün sayısı.' },
    { label: 'Taslak', value: num(summary.draft || 0), tone: 'warn',
      title: 'Girilmiş ama yayınlanmamış gün sayısı.' },
    { label: 'Menü yok', value: num(summary.missing || 0),
      title: 'Bu ayda hiç menü girilmemiş gün sayısı.' },
    { label: 'Tükendi', value: num(summary.sold_out || 0), tone: 'bad',
      title: 'Tavanı dolmuş gün sayısı. Tavanı olmayan gün burada sayılmaz.' },
  ]);
}

// ------------------------------------------------------------ gün yükleme

/**
 * Gün + stok verisini tazeler ve EKRANI ÇİZMEZ.
 *
 * Çizimden ayrılmasının nedeni hızlı kalem eklemedir: her eklemeden sonra sağ
 * panoyu baştan çizmek, arama kutusunu da yok eder ve ODAK KAYBOLUR — hızlı
 * eklemenin bütün kazancı tam olarak odağın kutuda kalmasıdır. Veri tazeleme
 * ile çizim ayrı durunca, ekleme sonrası yalnız kalem tablosu yeniden çizilir.
 *
 * @returns {Promise<boolean>} kullanıcı aradan gün değiştirdiyse `false`
 */
async function fetchDay(iso) {
  const dayResult = await call(`${BASE}/days/${iso}`);
  if (state.selected !== iso) return false;
  state.exists = Boolean(dayResult.exists);
  // BOŞ SÖZLÜK "veri var" DEĞİLDİR. Servis bağlanamadığında ya da gün
  // olmadığında `{}` döndürüyor ve `payload || null` yazmak onu doğru
  // sanardı; ekran da `day.title` okumaya çalışıp çökerdi.
  state.day = state.exists ? (dayResult.day || null) : null;
  // Stok bu gün için TAZE DEĞİL: Stok sekmesi açılırsa istenecek.
  dropStock();
  return true;
}

/** Stok okumasını geçersiz kılar — gün değişti ya da tavan yazıldı. */
function dropStock() {
  state.stock = null;
  state.stockFor = '';
  state.stockError = '';
}

/**
 * Stok durumunu SEÇİLİ GÜN için okur — yalnız Stok sekmesi açılınca.
 *
 * Önbelleğe alınmaz, alınmamalı (geçit de almıyor): `sold` rezerve edilmiş
 * porsiyondur, abonelikler onu önceden tutar ve bir dakika bayat bir sayı
 * dolmuş bir günü açık gösterirdi. "Tembel" ile "önbellekli" ayrı şeyler:
 * burada yalnız İSTENMEDEN çekilmiyor.
 */
async function ensureStock() {
  const iso = state.selected;
  if (!iso || state.stockFor === iso) return;
  try {
    const result = await call(`${BASE}/days/${iso}/stock`);
    if (state.selected !== iso) return;      // aradan gün değişti
    state.stock = result?.stock?.day ? result.stock : null;
    state.stockError = state.stock ? '' : (result?.error || '');
  } catch (failure) {
    if (state.selected !== iso) return;
    state.stock = null;
    state.stockError = failure.message;
  }
  // BAŞARISIZLIKTA DA İŞARETLENİR: aksi hâlde `paintStock` her çizimde
  // yeniden dener ve düşmüş bir sunucuyu döven bir döngü doğardı.
  state.stockFor = iso;
}

async function reloadDay({ quiet = false } = {}) {
  if (!state.selected) return;
  const iso = state.selected;
  if (!quiet) nodes.right.replaceChildren(skeletonRows(6, 3));
  try {
    if (!await fetchDay(iso)) return;        // kullanıcı aradan gün değiştirdi
    state.preview = null;
  } catch (failure) {
    if (state.selected !== iso) return;
    state.exists = false;
    state.day = null;
    dropStock();
    toast(failure.message, 'bad');
  }
  paintDay();
}

/**
 * Kalem eklendikten/kaldırıldıktan sonraki tazeleme: veriyi yeniler ama YALNIZ
 * kalem tablosunu yeniden çizer. `mark` verilirse o üründen doğan yeni kalem
 * "az önce eklendi" diye işaretlenir — kimliği sunucudan gelen yanıtta değil,
 * tazelenmiş listede aranır, çünkü seçici menüde ZATEN OLAN ürünleri hiç
 * göstermiyor: bir gün içinde bir `menu_id` en çok bir kez bulunur.
 */
async function refreshItems({ mark = [] } = {}) {
  const iso = state.selected;
  try {
    if (!await fetchDay(iso)) return;
  } catch (failure) {
    toast(failure.message, 'bad');
    return;
  }
  // `mark` bir LİSTEDİR: toplu kayıttan sonra girenlerin HEPSİ tek tıkla geri
  // alınabilmeli. Tek kimlikle sınırlıyken yalnız sonuncusu geri alınabiliyordu.
  for (const menuId of mark) {
    const fresh = (state.day?.items || []).find((item) => item.menu_id === menuId);
    if (fresh) markJustAdded(fresh.id);
  }
  redrawItems();
}

/**
 * Ürün kataloğu — bir kez okunur ve panelin ömrü boyunca saklanır.
 *
 * `force` YENİDEN DENEME İÇİNDİR: liste bir kez hata verdiğinde önbellek onu
 * kalıcı kılıyordu ve hızlı ekleme kutusu, bağlantı düzelse bile boş kalırdı.
 * Kutu artık bir "yeniden dene" düğmesi çiziyor ve tek yolu bu bayrak.
 */
async function ensureProducts({ force = false } = {}) {
  if (force) {
    state.products = [];
    state.productsError = '';
  } else if (state.products.length || state.productsError) {
    return;
  }
  try {
    const result = await call(`${BASE}/products`);
    state.products = result.items || [];
    state.productsError = result.connected === false ? (result.error || '') : '';
    if (result.truncated) {
      toast('Ürün listesi kırpıldı; aramayı daraltın.', 'warn');
    }
  } catch (failure) {
    state.productsError = failure.message;
  }
}

// ================================================================ sağ pano

/**
 * Kalem sekmesinin CANLI düğümlerini bırakır.
 *
 * Bu düğümler sekme/gün değişince DOM'dan düşüyor ama `nodes` üzerinde asılı
 * kalıyordu; ölü bir seçiciye `setItems` çağırmak ya da ölü bir arama kutusuna
 * odak vermek sessizce hiçbir şey yapmaz — sessiz bir arıza da arızadır.
 */
function dropItemNodes() {
  // Seçici bekleyen bir arama çizimi tutuyor olabilir (debounce); düğüm
  // DOM'dan düşerken o da bırakılmalı.
  nodes.quickPicker?.destroy?.();
  nodes.quickPicker = null;
  nodes.quickSearch = null;
  nodes.quickNote = null;
  nodes.itemsBox = null;
  nodes.draftBar = null;
  // Yuva sekmeyle birlikte gidiyor; bırakılmazsa `syncPackageAlert()` DOM'dan
  // düşmüş bir düğümü doldurmaya devam ederdi.
  nodes.packageAlert = null;
}

function paintDay() {
  dropItemNodes();
  nodes.right.replaceChildren();
  if (!state.selected) {
    nodes.right.append(emptyState({
      title: 'Soldaki takvimden bir gün seçin',
      text: 'Izgarada her günün rozeti o günün durumunu söyler: yayında, taslak, '
        + 'tükendi ya da rozetsiz (menü girilmemiş).',
    }));
    return;
  }

  const info = state.byDate[state.selected] || {};
  nodes.right.append(dayHeader(info));

  if (!state.exists) {
    nodes.right.append(emptyState({
      title: `${longDate(state.selected)} — menü girilmemiş`,
      text: 'Bu güne ya sıfırdan bir menü kurabilir ya da başka bir günün menüsünü '
        + 'kopyalayabilirsiniz. Kopyalanan gün her zaman TASLAK doğar.',
      actions: [
        button('Gün kur', { variant: 'primary', onClick: () => openDayCreator() }),
        button('Başka günden kopyala', { onClick: () => openDuplicate({ toHere: true }) }),
      ],
    }));
    return;
  }

  const tabs = tabBar([
    { key: 'day', label: 'Gün bilgileri' },
    { key: 'items', label: 'Kalemler' },
    { key: 'stock', label: 'Stok' },
  ], state.tab, (key) => { state.tab = key; paintTab(); });
  tabs.badge('items', state.day?.item_count || 0);
  nodes.tabs = tabs;
  nodes.tabBody = h('div', 'bm-tabbody');
  nodes.right.append(tabs.node, nodes.tabBody);
  paintTab();
}

function paintTab() {
  if (!nodes.tabBody) return;
  // FORM GLOBAL DİNLEYİCİ TUTAR (tarih alanı) ve sekme her değiştiğinde yenisi
  // kuruluyor. Eskisini bırakmazsak kapalı bir formun takvimi `document`
  // üzerinde dinlemeye devam eder ve panel açıldıkça birikirler.
  nodes.dayForm?.destroy();
  nodes.dayForm = null;
  dropItemNodes();
  nodes.tabBody.replaceChildren();
  ({ day: paintDayForm, items: paintItems, stock: paintStock }[state.tab]
    || paintDayForm)();
}

function dayHeader(info) {
  const head = h('div', 'bm-dayhead');
  const left = h('div', 'bm-dayhead-main');
  left.append(h('h2', 'bm-daytitle', longDate(state.selected)));

  const marks = h('div', 'bm-marks');
  if (state.exists && state.day) {
    marks.append(badge(state.day.status_label,
      state.day.status === 'published' ? 'good' : 'warn'));
    marks.append(badge(state.day.cutoff_time
      ? `Kesim ${state.day.cutoff_time} (güne özel)`
      : `Kesim ${globalCutoff()} (genel ayar)`, 'info'));
  } else {
    marks.append(badge('Menü yok', 'dim'));
  }
  if (info.closed) marks.append(badge(info.closed_reason || 'Kapalı gün', 'bad'));
  if (!info.orderable && info.not_orderable_label) {
    marks.append(badge(`Sipariş alınmıyor: ${info.not_orderable_label}`, 'dim'));
  }
  left.append(marks);

  const actions = h('div', 'bm-dayactions');
  if (state.exists && state.day) {
    if (state.day.status === 'published') {
      actions.append(commitGroup(
        button('Yayından çek', {
          title: 'Gün satış kanalından düşer ve taslağa döner.',
          onClick: () => doUnpublish(),
        }),
        'Müşteriye görünürlüğü kaldırır · gerekçe istenir',
      ));
    } else {
      actions.append(commitGroup(
        button('Yayınla', {
          variant: 'primary',
          title: 'Yayınlandığı anda müşteri bu güne sipariş verebilir.',
          onClick: () => doPublish(),
        }),
        'Müşteriye açar · gerekçe istenir',
      ));
    }
    actions.append(button('Kopyala', { onClick: () => openDuplicate({ toHere: false }) }));
    actions.append(button('Sil', { variant: 'danger', onClick: () => doDeleteDay() }));
  }
  head.append(left, actions);
  return head;
}

/**
 * Taahhüt düğmesinin çerçevesi — yayınla ve yayından çek burada durur.
 *
 * Gerekçe artık YALNIZ taahhüt anlarında isteniyorsa, o an ekranda da öyle
 * görünmeli: aynı şeritte duran "Kopyala" ve "Sil" ile aynı ağırlıkta çizilen
 * bir yayın düğmesi, kullanıcıya diyaloğun neden yalnız burada açıldığını
 * anlatmaz. Çerçeve RENKLE DEĞİL YAZIYLA ayrışır (kit kuralı 7): altındaki
 * satır ne olacağını ve gerekçe isteneceğini önceden söyler.
 */
function commitGroup(control, note) {
  const box = h('div', 'bm-commit');
  box.append(control, h('span', 'bm-commit-note', note));
  return box;
}

// --------------------------------------------------------- gün bilgileri

function paintDayForm() {
  const day = state.day;
  const form = formGrid({
    fields: [
      { key: 'title', label: 'Başlık', type: 'text', maxLength: 120, wide: true,
        placeholder: 'Ev Yemeği Menüsü',
        hint: 'Müşteriye menü kartının üstünde görünür.' },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500,
        wide: true, hint: 'Müşteriye görünür. Kalemleri tek tek yazmak zorunda değilsiniz.' },
      { key: 'internal_note', label: 'İç not', type: 'textarea', maxLength: 255,
        wide: true, hint: 'MÜŞTERİYE GİTMEZ. Tedarikçi, mutfak notu gibi bilgiler için.' },
      { key: 'package_price_kurus', label: 'Paket fiyatı', type: 'money',
        hint: 'Boş bırakılırsa paket satılmaz, yalnız kalemler tek tek satılır. '
          + 'Sıfır kabul edilmez.' },
      { key: 'components_sellable', label: 'Kalemler tek tek satılabilir',
        type: 'checkbox',
        hint: 'Kapalıysa yalnız paket satılır. Paket fiyatı da yoksa gün yayınlanamaz.' },
      { key: 'cutoff_time', label: 'Güne özel kesim saati', type: 'text',
        maxLength: 5, placeholder: 'ss:dd',
        hint: `Boş bırakılırsa genel ayar geçerli: ${globalCutoff()}. `
          + 'Yerel saat (Europe/Istanbul).' },
      { key: 'capacity_total', label: 'Gün toplam tavanı (porsiyon)', type: 'number',
        min: 0,
        hint: 'Boş = sınırsız, 0 = bugün satılmıyor. Tavanları Stok sekmesinden de '
          + 'yazabilirsiniz.' },
      { key: 'image_path', label: 'Görsel yolu', type: 'text', wide: true,
        placeholder: 'veykemtu/daily/2026-08-17.jpg',
        hint: 'Göreli yol. Bu ekranda yükleme yok; dosya sunucuya başka bir yoldan '
          + 'konur ve yolu buraya yazılır.' },
    ],
    value: {
      title: day.title || '',
      description: day.description || '',
      internal_note: day.internal_note || '',
      package_price_kurus: day.package_price_kurus,
      components_sellable: day.components_sellable,
      cutoff_time: day.cutoff_time || '',
      capacity_total: day.capacity_total,
      image_path: day.image_path || '',
    },
    onChange: () => {
      saveButton.disabled = form.dirty().length === 0;
    },
  });
  nodes.dayForm = form;

  const saveButton = button('Değişiklikleri kaydet', {
    variant: 'primary',
    disabled: true,
    onClick: () => saveDay(form),
  });

  const facts = h('div', 'bm-facts');
  facts.append(
    fact('Kalem sayısı', `${num(day.item_count)} kalem`),
    fact('Kalemlerin toplamı', money(day.items_total_kurus)),
    fact('Paket fiyatı', day.package_price_kurus === null
      ? 'paket satılmıyor' : money(day.package_price_kurus)),
    fact('Yayına alındı', day.published_at ? day.published_at : '—'),
  );

  // "Vazgeç" formu SUNUCUDAKİ hâlden yeniden çizer. `form.reset(form.draft())`
  // yazmak, kirli durumu temizleyip DEĞİŞTİRİLMİŞ değerleri bırakırdı —
  // kullanıcı vazgeçtiğini sanırken ekranda hâlâ kendi yazdığı duruyor olurdu.
  const actions = h('div', 'bm-formactions');
  actions.append(saveButton, button('Vazgeç', { onClick: () => paintTab() }));

  // Uyarı EN ÜSTTE: fiyat alanının hemen üstünde duruyor ki fiyatı yazmaya
  // gelen kullanıcı, yazdığı fiyatın neden işe yaramadığını aynı ekranda
  // okusun. Özetin altına konsaydı kaydırma gerektirirdi.
  nodes.tabBody.append(packageAlertSlot());

  nodes.tabBody.append(
    card('Gün bilgileri', padded(form.node),
      'Gönderilmeyen alan değişmez; boşaltılan alan temizlenir.'),
    actions,
    card('Özet', facts),
    hintBox('Kesim saati geçmiş ve yayınlanmış bir günün fiyatı değiştirilemez: '
      + 'o gün için sipariş kapandı ve girmiş siparişler eski fiyattan. Fiyatı '
      + 'değiştirmek yalnız raporları bozardı.'),
  );
}

function fact(label, value) {
  const row = h('div', 'bm-fact');
  row.append(h('span', 'bm-fact-label', label), h('b', 'bm-fact-value', String(value)));
  return row;
}

/**
 * "Paket fiyatı girildi ama paket satılamaz" uyarısı — sorun yoksa `null`.
 *
 * ## Neden bir uyarı gerekiyor
 *
 * BLD paketi ancak ÜRÜNÜ ÇÖZÜLEBİLEN en az bir ZORUNLU kalem varsa satışa
 * açıyor; yoksa günün menü ucunda `package` alanı `null` dönüyor ve sitede
 * paket kartı HİÇ ÇİZİLMİYOR (`DailyMenu::packageBlockReason` →
 * `no_components`). Fiyat kaydedilmiş, gün yayında, takvim yeşil — ama
 * müşteri paketi sepete koyamıyor. Ekranda tek bir işaret yoktu; yönetici
 * sorunu ancak müşteri şikâyetinden öğreniyordu.
 *
 * UYARI, YAYINI ENGELLEMEZ. Paketsiz ama kalemleri tek tek satılan bir gün
 * geçerli bir gündür ve o yolu kapatmak, olmayan bir hatayı düzeltmek için
 * çalışan bir akışı kırmak olurdu. Burada anlatılan şey bir çelişki: fiyat
 * girilmiş olması paketin satılmasının İSTENDİĞİNİ söylüyor.
 *
 * ÜÇÜNCÜ ENGEL (vitrinin "Günün Menüsü" ürünü tanımsız — `no_product`)
 * BURADAN GÖRÜLEMEZ: o bilgi bu ekranın okuduğu hiçbir uçta yok ve
 * uydurulamaz. Sunucuda `php artisan veykemtu:setup` bir kez koşturulur;
 * BLD o hâli kendi günlüğüne yazıyor.
 */
function packageBlockedAlert() {
  const day = state.day;
  if (!day) return null;
  // `null` = paket satılmıyor, bu bir arıza değil olağan iş akışı.
  if (day.package_price_kurus === null || day.package_price_kurus === undefined) return null;

  const items = day.items || [];
  const missingRequired = items.length > 0 && !items.some((row) => row.is_required);
  const draft = day.status !== 'published';
  if (!missingRequired && !draft) return null;

  // İKİ ENGEL AYRI CÜMLE: biri paketin İÇİNİ (zorunlu kalem yok), öteki
  // günün GÖRÜNÜRLÜĞÜNÜ (taslak) anlatıyor. Tek cümlede birleştirmek,
  // yalnız birine takılmış bir günde yanlış olanı da söylemek olurdu.
  const lines = [`Paket fiyatı girilmiş (${money(day.package_price_kurus)}) ama bu gün `
    + 'satışta değil:'];
  if (missingRequired) {
    lines.push('· Hiçbir kalem "zorunlu" işaretli değil — BLD içi boş bir paketi '
      + 'satışa açmıyor, sitede paket kartı hiç çizilmiyor.');
  }
  if (draft) {
    lines.push('· Gün taslakta — müşteri bu günü hiç görmüyor.');
  }
  if (!items.length) {
    lines.push('· Güne kalem girilmemiş; önce kalemleri ekleyin.');
  }

  const box = alertBox(lines.join('\n'), 'warn');

  // Kalemsiz günde düğüm çizilmez: basılınca sunucunun "önce kalem ekleyin"
  // diyeceği bir düğme, çalışmayan bir düğmedir.
  if (items.length) {
    const actions = h('div', 'bm-alertactions');
    actions.append(button('Satışa aç', {
      variant: 'primary',
      title: 'Eksik "zorunlu kalem" işaretlerini kurar ve günü yayına alır.',
      onClick: () => openSale(),
    }));
    box.append(actions);
  }
  return box;
}

/**
 * Uyarının SABİT YUVASI — sekme çizilirken bir kez konur, sonra doldurulur.
 *
 * Neden yuva: kalem tablosu tek başına yeniden çiziliyor (`redrawItems`) ve
 * doğrudan eklenen bir uyarı kutusu o tazelemeyi görmezdi. "Zorunlu" kutusunu
 * işaretleyen kullanıcı, düzelttiği hâlde ekranda duran uyarıyı okumaya devam
 * ederdi — düzeltmenin işe yaramadığını söyleyen bir yalan.
 */
function packageAlertSlot() {
  nodes.packageAlert = h('div', 'bm-packagealert');
  syncPackageAlert();
  return nodes.packageAlert;
}

/** Yuvayı günün son hâline göre doldurur ya da boşaltır. */
function syncPackageAlert() {
  const slot = nodes.packageAlert;
  if (!slot) return;
  const alert = packageBlockedAlert();
  slot.replaceChildren(...(alert ? [alert] : []));
}

async function saveDay(form) {
  const patch = form.patch();
  if (!Object.keys(patch).length) {
    toast('Değişen alan yok.', 'warn');
    return;
  }
  // BOŞ METİN `null` OLARAK GİDER: "dokunma" ile "boşalt" ayrımı sunucuda
  // duruyor ve boş dize üçüncü bir hâl yaratırdı ("boş ama dolu").
  const payload = {};
  for (const [key, value] of Object.entries(patch)) {
    payload[key] = typeof value === 'string' && value.trim() === '' ? null : value;
  }
  // GEREKÇE SORULMAZ (politika): gün bilgisi düzenlemek taahhüt değil, menüyü
  // kurmanın kendisidir. Kullanıcı yine de kaydedildiğini GÖRÜR — `success`
  // toast'ı bilerek duruyor; sessiz bir yazma, kaydedip kaydetmediğini
  // bilmeyen kullanıcı üretir.
  const saved = await write(`${BASE}/days/${state.selected}`,
    { method: 'PATCH', payload },
    { success: `Gün bilgileri kaydedildi (${Object.keys(payload).length} alan).`,
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });

  /*
   * FİYAT GİRİLDİYSE GÜN KENDİLİĞİNDEN SATIŞA AÇILIR (ayar:
   * `auto_open_on_price`). Bu işletmede fiyat yazmak "bu menü satılacak"
   * demenin kendisi; ikinci bir yayın adımı yalnızca unutuluyordu ve gün
   * taslakta kaldığı için müşteri menüyü hiç görmüyordu.
   *
   * ÇAĞRI `after` İÇİNDEN YAPILMAZ: `write()` kendi `busy` kilidini ancak
   * döndükten sonra bırakıyor ve içeriden çağrılan ikinci yazma sessizce
   * `null` dönerdi — hiçbir hata görünmeden hiçbir şey olmazdı.
   *
   * `null` FİYAT TETİKLEMEZ: alanı boşaltmak "paket satılmasın" demek ve o
   * günü yayına itmek, kullanıcının yaptığının tam tersi olurdu.
   */
  if (saved && payload.package_price_kurus !== null
      && payload.package_price_kurus !== undefined
      && state.contract?.auto_open_on_price !== false) {
    await openSale({ auto: true });
  }
}

// -------------------------------------------------------------- kalemler

function paintItems() {
  // Sıra ÖNEMLİ: arama kutusu tablonun ÜSTÜNDE durur. Altta olsaydı liste
  // uzadıkça aşağı kayar ve beş kalem ekleyen yönetici her seferinde onu
  // aramak zorunda kalırdı — kutunun sabit kalması akışın kendisidir.
  nodes.itemsBox = h('div', 'bm-itemsbox');
  nodes.itemsBox.append(itemsTable());

  // Uyarı BU SEKMEDE DE DURUYOR: düzeltme buradan yapılıyor ("Düzenle" →
  // "Zorunlu kalem"). Yalnız gün sekmesinde kalsaydı, sorunu okuyan
  // kullanıcı çözümün olduğu ekrana geçince cümleyi kaybederdi.
  nodes.tabBody.append(packageAlertSlot());

  nodes.tabBody.append(
    quickAddCard(),
    card('Kalemler', nodes.itemsBox,
      'Sıra sunucudan gelir: çorba → ana yemek → pilav → tatlı.'),
    hintBox('Adet, fiyat geçersiz kılma, etiket, zorunluluk ve tek satılabilirlik '
      + 'SATIR ÜSTÜNDEN düzenlenir: hızlı ekleme kalemi listeye koyar, ayrıntı '
      + '"Düzenle" ile girilir. Böyle olması, beş kalem eklerken beş kez form '
      + 'doldurmayı gereksiz kılar.'),
    hintBox('Kalemin ürünü değiştirilemez. Ürünü değiştirmek kalemi silip yenisini '
      + 'eklemektir ve denetim izinde iki ayrı satır olarak görünmelidir.'),
  );

  // Ürün listesi ağdan gelir; kutu ONU BEKLEMEDEN çizilir ve dolunca dolar.
  // Beklemek, sekmeye her geçişte boş bir ekran demekti.
  ensureProducts().then(() => fillQuickAdd());
}

/** Kalem tablosu — hızlı eklemeden sonra TEK BAŞINA yeniden çizilir. */
function itemsTable() {
  const table = dataTable({
    columns: [
      { key: 'sort_order', label: 'Sıra', width: '70px', align: 'num',
        // Taslakta sıra YOK ve uydurulmaz: numarayı sunucu veriyor (mevcut en
        // büyüğe 10 ekleyerek). Burada tahmin etmek, kaydettikten sonra
        // değişen bir sayı göstermek olurdu.
        cell: (row) => (row.draft ? '—' : String(row.sort_order ?? '')) },
      { key: 'name', label: 'Kalem', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('div', 'bm-itemname');
          box.append(h('b', undefined, row.name || `#${row.menu_id}`));
          const marks = h('div', 'bm-itemmarks');
          // ROZET, FARKLI DÜĞMENİN AÇIKLAMASIDIR: bu satırda "Sil" yerine
          // "Geri al" duruyor ve nedeni yazılı olmalı (kit kuralı 7).
          if (row.draft) marks.append(badge('Kaydedilmedi', 'warn'));
          if (isJustAdded(row.id)) marks.append(badge('Az önce eklendi', 'info'));
          if (row.is_required) marks.append(badge('Zorunlu', 'info'));
          if (!row.sellable_alone) marks.append(badge('Yalnız pakette', 'dim'));
          if (row.sold_out) marks.append(badge('Mutfak tükendi dedi', 'bad'));
          box.append(marks);
          return box;
        } },
      { key: 'quantity', label: 'Adet', width: '70px', align: 'num' },
      { key: 'unit_price_kurus', label: 'Birim', width: '110px', align: 'num',
        cell: (row) => (row.price_override_kurus === null
          ? `${money(row.unit_price_kurus)} (ürün fiyatı)`
          : `${money(row.unit_price_kurus)} (özel)`) },
      { key: 'capacity', label: 'Tavan', width: '110px', align: 'num',
        cell: (row) => (row.capacity === null ? 'sınırsız' : `${num(row.capacity)} porsiyon`) },
      { key: 'actions', label: '', width: '175px',
        cell: (row) => {
          const box = h('div', 'bm-rowactions');
          if (row.draft) {
            // Taslak satırda "Düzenle" YOK: düzenlenecek bir kayıt henüz
            // sunucuda yok ve boş bir formu açmak yanlış söz verirdi.
            box.append(button('Çıkar', {
              variant: 'ghost',
              title: 'Henüz kaydedilmedi; listeden çıkarılır.',
              onClick: () => dropDraft(row.menu_id),
            }));
            return box;
          }
          box.append(button('Düzenle', { variant: 'ghost', onClick: () => openItemEditor(row) }));
          box.append(isJustAdded(row.id)
            ? button('Geri al', {
              variant: 'ghost',
              title: 'Bu kalemi az önce siz eklediniz: tek tıkla kaldırılır, onay '
                + 'sorulmaz. Üzerinde henüz hiçbir düzenleme yok.',
              onClick: () => undoItem(row),
            })
            : button('Sil', { variant: 'ghost', onClick: () => doDeleteItem(row) }));
          return box;
        } },
    ],
    // TASLAK SATIRLAR SONDA: sunucu yeni kalemi listenin sonuna koyuyor,
    // tablo da onları orada göstermeli — kaydedince satır YERİNDEN OYNAMASIN.
    rows: [...(state.day?.items || []), ...draftRows()],
    empty: emptyState({
      title: 'Bu günde kalem yok',
      text: 'Yukarıdaki kutuya ürün adını yazıp Enter\'a basın. Kalemler önce '
        + 'listeye düşer, en sonda "Kalemleri kaydet" ile birlikte yazılır. '
        + 'Yayınlamak için en az bir kalem gerekli: kalemsiz bir gün müşteriye '
        + 'boş bir menü kartı gösterirdi.',
    }),
  });
  return table.node;
}

/**
 * Taslaktaki kalemleri tablo satırı biçimine çevirir.
 *
 * DEĞERLER `saveDraft()` İLE BİREBİR AYNI OLMAK ZORUNDA: bu satırlar
 * kaydedilmeden önceki hâli gösteriyor ve rozetler ("Zorunlu") buradan
 * çiziliyor. İkisi ayrışırsa tablo, kaydedilince değişecek bir hâl gösterir.
 */
function draftRows() {
  return state.draft.map((row) => ({
    ...row,
    draft: true,
    id: 0,
    quantity: 1,
    price_override_kurus: null,
    capacity: null,
    is_required: true,
    sellable_alone: true,
  }));
}

/**
 * "Kalemleri kaydet" çubuğu — taslak boşken HİÇ ÇİZİLMEZ.
 *
 * Boşken sönük bir düğme bırakmak, kullanıcıya basılacak bir şey varmış gibi
 * gösterirdi; kaydedilecek bir şey yoksa çubuk da yoktur.
 */
function draftBar() {
  if (!state.draft.length) return null;
  const bar = h('div', 'bm-draftbar');
  bar.append(h('span', 'bm-draftcount',
    `${num(state.draft.length)} kalem henüz kaydedilmedi`));
  bar.append(button('Kalemleri kaydet', {
    variant: 'primary',
    onClick: () => saveDraft(),
  }));
  bar.append(button('Tümünü çıkar', {
    variant: 'ghost',
    title: 'Kaydedilmemiş kalemlerin hepsini listeden çıkarır.',
    onClick: () => { state.draft = []; redrawItems(); resetQuickSearch(); },
  }));
  return bar;
}

/** Tabloyu, sekme rozetini ve seçicinin kalan ürünlerini birlikte tazeler. */
function redrawItems() {
  if (!nodes.itemsBox) return;
  // Gün aradan silinmiş ya da kaybolmuşsa kalem tablosu yeniden çizilemez;
  // tam çizim doğru ekranı ("menü girilmemiş") getirir.
  if (!state.exists || !state.day) { paintDay(); return; }
  const bar = draftBar();
  nodes.itemsBox.replaceChildren(...(bar ? [bar, itemsTable()] : [itemsTable()]));
  nodes.tabs?.badge('items', (state.day.item_count || 0) + state.draft.length);
  // Kalem listesi değiştiyse "zorunlu kalem yok" cümlesi de değişmiş olabilir.
  syncPackageAlert();
  fillQuickAdd();
}

// ------------------------------------------------------- hızlı kalem ekleme
//
// ASIL İŞ BU. Eskiden bir güne ürün koymak dört adımdı: çekmeceyi aç → ürünü
// bul → formu doldur → gerekçe yaz. Beş ürün için beş tur, her turda odak
// başka yerde. Şimdi gün düzenleyicinin içinde duran tek bir arama kutusu var:
// yaz → Enter → kalem listeye düşer → kutu temizlenir → ODAK KUTUDA KALIR.
//
// ODAK AYRINTISI İŞİN TAMAMIDIR. Odak kaybolursa kullanıcı her kalemden sonra
// fareye uzanır ve kazanılan süre geri gider; bu yüzden ekleme sonrası sağ pano
// BAŞTAN ÇİZİLMEZ (`refreshItems` yalnız tabloyu tazeler) ve kutuya odak
// açıkça geri verilir.
//
// SEÇİCİ YENİDEN YAZILMADI (ADR 0011): `createPicker` zaten gruplu, aranabilir
// ve kırpma uyarısını kendisi veriyor. Kitin vermediği iki şey var — sorguyu
// temizlemek ve Enter'ı yakalamak — ve kit ORTAK MAL olduğu için bu turda ona
// yazılmıyor. İkisi de seçicinin KENDİ düğümü üzerinden yapılıyor; bağımlılık
// burada yazılı dursun ki kit bir gün `clearQuery()`/`onSubmit` verdiğinde
// buranın sadeleşeceği bilinsin.

function quickAddCard() {
  const box = h('div', 'bm-quickadd');

  const picker = createPicker({
    items: [],
    groupLabel: 'Kategori',
    placeholder: 'Ürün ara — Enter kalemi ekler',
    single: true,
    // TIKLAMA DA ANINDA EKLER: seçim ile ekleme arasına bir "Ekle" düğmesi
    // koymak, tek seçimli bir listede ikinci bir tıklama istemekti.
    onChange: (ids) => {
      const menuId = Number(ids[0] || 0);
      if (menuId) quickAdd(menuId);
    },
  });
  nodes.quickPicker = picker;

  // Kitin `focus()` metodu var, `clearQuery()` yok. Kutuyu doğrudan tutmanın
  // tek yolu seçicinin düğümü; sınıf adı yerine TİP seçicisi kullanıldı, çünkü
  // "seçicinin arama kutusu" bir `type=search` olmaya sınıf adını korumaktan
  // daha çok bağlı. Bulunamazsa Enter kısayolu düşer, tıklama çalışmaya
  // devam eder — kısayolun yokluğu ekranı kilitlemez.
  const search = picker.node.querySelector('input[type="search"]');
  nodes.quickSearch = search || null;

  picker.node.addEventListener('keydown', (event) => {
    // YALNIZ ARAMA KUTUSU: liste satırı da bir `button` ve Enter'ı kendisi
    // tıklamaya çeviriyor. Buradan da tıklarsak kalem İKİ KEZ eklenirdi.
    if (event.key !== 'Enter' || event.target !== search) return;
    event.preventDefault();
    const first = picker.node.querySelector('button.pk-row');
    if (first) first.click();
    else toast('Aramaya uyan ürün yok; yazımı kısaltın.', 'warn');
  });

  nodes.quickNote = h('div', 'bm-quicknote');
  box.append(picker.node, nodes.quickNote);

  return card('Hızlı kalem ekleme', box,
    'Yaz, Enter\'a bas: kalem ADET 1 ve ÜRÜNÜN KENDİ FİYATIYLA listenin sonuna '
    + 'düşer. Kutu temizlenir, odak kutuda kalır — arka arkaya ekleyebilirsin. '
    + 'Hiçbiri hemen yazılmaz; hepsi "Kalemleri kaydet" ile birlikte kaydedilir.');
}

/** Seçicinin listesi = SATIŞTAKİ ürünler eksi bu güne zaten girilmiş olanlar. */
function fillQuickAdd() {
  const picker = nodes.quickPicker;
  const note = nodes.quickNote;
  // Düğümler YEREL DEĞİŞKENE ALINIR: bu işlev ağ beklemesinden sonra da
  // çağrılıyor ve o arada sekme değişmiş olabilir. `nodes` üzerinden okumak,
  // ölmüş bir kutuyu doldurmak ya da yenisini yarıda kesmek olurdu.
  if (!picker || !note) return;

  // TASLAKTAKİLER DE ÇIKAR: aynı ürünü ikinci kez seçmek mümkün olmamalı.
  const used = new Set([
    ...(state.day?.items || []).map((item) => item.menu_id),
    ...state.draft.map((item) => item.menu_id),
  ]);
  const rows = state.products
    .filter((row) => !used.has(row.menu_id))
    .map((row) => ({
      id: row.menu_id,
      name: row.name,
      group: row.category || 'Diğer',
      meta: `${money(row.price_kurus)}${row.sold_out ? ' · mutfak tükendi dedi' : ''}`,
    }));
  picker.setItems(rows);

  note.replaceChildren();
  if (state.productsError) {
    // ESKİ METİN BİR SÖZ VERİYORDU VE TUTMUYORDU ("ürün kimliğini elle
    // girebilirsiniz" — öyle bir alan hiç yoktu). Yerine çalışan bir çıkış
    // yolu kondu: yeniden deneme düğmesi.
    note.append(
      alertBox(`Ürün listesi alınamadı: ${state.productsError} `
        + 'Bu liste gelmeden kalem eklenemez.', 'warn'),
      button('Ürün listesini yeniden dene', {
        onClick: async () => {
          note.replaceChildren(h('p', 'bm-quick-line', 'Ürünler okunuyor…'));
          await ensureProducts({ force: true });
          fillQuickAdd();
        },
      }),
    );
    return;
  }
  note.append(h('p', 'bm-quick-line',
    `${num(rows.length)} ürün eklenebilir · bu güne girilmiş ${num(used.size)} ürün `
    + 'listede yok · yalnız SATIŞTAKİ ürünler listelenir (satıştan kalkmış bir '
    + 'ürünü içeren gün yayınlanamıyor).'));
}

/**
 * Kalemi ANINDA ekler: diyalog yok, gerekçe yok, form yok.
 *
 * `sort_order` GÖNDERİLMEZ — sunucu mevcut en büyüğe 10 ekliyor, yani kalem
 * listenin sonuna düşüyor. Burada hesaplamak, iki yerde duran aynı kuralın bir
 * gün ayrışması demekti.
 *
 * `price_override_kurus` de gönderilmez: ürünün kendi fiyatı geçerli olur.
 */
function quickAdd(menuId) {
  // AĞA ÇIKMAZ. Kalem taslağa düşer, tabloda hemen görünür ve kutu boşalır;
  // yazma "Kalemleri kaydet" denince, hepsi birden yapılır.
  const product = state.products.find((row) => row.menu_id === menuId);
  if (!product) {
    toast('Ürün listede yok; listeyi tazeleyin.', 'warn');
    return;
  }
  if (state.draft.some((row) => row.menu_id === menuId)
      || (state.day?.items || []).some((row) => row.menu_id === menuId)) {
    toast(`"${product.name}" bu güne zaten girilmiş.`, 'warn');
    resetQuickSearch();
    return;
  }

  state.draft.push({
    menu_id: menuId,
    name: product.name,
    unit_price_kurus: product.price_kurus,
    sold_out: Boolean(product.sold_out),
  });
  redrawItems();
  // Odak SON: `redrawItems` seçiciyi yeniden doldurunca odak `body`'ye düşer.
  resetQuickSearch();
}

/** Taslaktan tek kalem çıkarır — henüz kaydedilmediği için onay sorulmaz. */
function dropDraft(menuId) {
  state.draft = state.draft.filter((row) => row.menu_id !== menuId);
  redrawItems();
  resetQuickSearch();
}

/**
 * Taslaktaki kalemlerin TAMAMINI kaydeder.
 *
 * Sırayla gider: sunucu `sort_order`'ı mevcut en büyüğe 10 ekleyerek veriyor,
 * yani ekleme sırası listedeki sırayı belirliyor. Paralel göndermek sırayı
 * kaybettirirdi.
 *
 * YARIM KALMA AÇIKÇA ANLATILIR: biri patlarsa geri kalanlar taslakta DURUR ve
 * ekran kaçının girdiğini söyler. Sessizce hepsini düşürmek, kullanıcının
 * yazdığı menüyü kaybetmesi olurdu.
 */
async function saveDraft() {
  if (busy || !state.draft.length) return;
  busy = true;
  nodes.status.set(`${state.draft.length} kalem yazılıyor…`);

  const kalan = [];
  let yazilan = 0;
  let hata = '';
  for (const row of state.draft) {
    try {
      /*
       * `is_required: true` — HIZLI EKLEMENİN SESSİZ HATASI BURADAYDI.
       *
       * Buraya `false` yazılıyordu ve sonucu ekranda görünmüyordu: BLD
       * paketi ancak ÜRÜNÜ ÇÖZÜLEBİLEN en az bir ZORUNLU kalem varsa
       * satıyor (`DailyMenu::packageBlockReason` → `no_components`). Bütün
       * kalemleri hızlı eklemeyle giren bir yönetici, güne 250 TL paket
       * fiyatı yazıp yayınlıyor, kaydedildi mesajını görüyor ve sitede
       * paket kartını hiç bulamıyordu — kalemler tek tek satılıyor, paket
       * `null` dönüyordu.
       *
       * `true` HEM SÖZLEŞMENİN HEM DE ÖTEKİ EKRANIN VARSAYILANI: alan hiç
       * gönderilmediğinde BLD `is_required`i `true` yazıyor, veritabanı
       * kolonunun varsayılanı `true` ve TastyIgniter gün düzenleyicisinde
       * yeni satırın kutusu işaretli geliyor. Tek ayrışan bu satırdı.
       *
       * İstisna (pakette bulunsun ama tükendiğinde paketi düşürmesin) satır
       * üstündeki "Düzenle" ile kurulur; olağan hâl zorunluluktur.
       */
      await call(`${BASE}/days/${state.selected}/items`, {
        method: 'POST',
        body: { menu_id: row.menu_id, quantity: 1,
                is_required: true, sellable_alone: true },
      });
      yazilan += 1;
    } catch (failure) {
      hata = failure.message;
      kalan.push(row);
    }
  }

  const yeni = state.draft.filter((row) => !kalan.includes(row))
    .map((row) => row.menu_id);
  state.draft = kalan;
  busy = false;

  if (!hata) {
    toast(`${yazilan} kalem kaydedildi.`, 'good');
  } else if (yazilan) {
    toast(`${yazilan} kalem kaydedildi, ${kalan.length} tanesi kaydedilemedi `
      + `(${hata}). Kaydedilemeyenler listede duruyor.`, 'warn');
  } else {
    toast(`Hiçbir kalem kaydedilemedi: ${hata}`, 'bad');
  }

  await refreshItems({ mark: yeni });
  patchCalendarDay();
  nodes.status.set(statusText());
  resetQuickSearch();

  /*
   * KALEMLER YAZILDIKTAN SONRA da satışa açma denenir — ve TAM BURADA, tek
   * tek her kalemden sonra değil.
   *
   * Sıra çoğu zaman "önce fiyat, sonra kalemler": fiyat kaydedildiğinde gün
   * kalemsizdi ve `publish_error()` haklı olarak yayını engellemişti. Tetik
   * yalnız fiyatta kalsaydı gün sonsuza kadar taslakta kalırdı.
   *
   * Döngünün İÇİNDE çağrılsaydı ilk kalem yazılır yazılmaz gün yayına
   * çıkardı ve müşteri, geri kalanı yazılırken tek kalemlik bir menü görürdü.
   */
  if (yazilan && state.day?.package_price_kurus !== null
      && state.day?.package_price_kurus !== undefined
      && state.contract?.auto_open_on_price !== false) {
    await openSale({ auto: true });
  }
}

/** Az önce eklenmiş kalemi tek tıkla geri alır — onay penceresi yok. */
async function undoItem(row) {
  await write(`${BASE}/days/${state.selected}/items/${row.id}`,
    { method: 'DELETE', payload: {} },
    { success: `"${row.name}" kaldırıldı.`,
      after: async () => {
        forgetJustAdded(row.id);
        await refreshItems();
        patchCalendarDay();
        resetQuickSearch();
      } });
}

/**
 * Arama kutusunu temizler ve odağı ona geri verir.
 *
 * Seçici sorguyu kendi `input` dinleyicisinden okuyor, yani kutunun değerini
 * değiştirmek tek başına listeyi süzülmemiş hâline döndürmez; olayı taklit
 * etmek, kite yazmadan bunu yapmanın tek yolu.
 *
 * ODAK HER HÂLDE GERİ VERİLİR: tıklamayla eklendiğinde odak tıklanan satırdaydı
 * ve seçici o satırı yeniden çizince odak `body`'ye düşer.
 */
function resetQuickSearch() {
  const search = nodes.quickSearch;
  if (!search) return;
  if (search.value !== '') {
    search.value = '';
    search.dispatchEvent(new Event('input', { bubbles: true }));
  }
  search.focus();
}

function openItemEditor(row) {
  const form = formGrid({
    fields: [
      { key: 'label', label: 'Etiket', type: 'text', maxLength: 120, wide: true,
        placeholder: 'Günün Çorbası: Mercimek',
        hint: 'Boş bırakılırsa ürünün kendi adı görünür.' },
      { key: 'quantity', label: 'Pakette kaç porsiyon', type: 'number', min: 1, max: 99 },
      { key: 'sort_order', label: 'Sıra', type: 'number', min: 0,
        hint: 'Onar onar artar; araya kalem sokmak için 15 gibi bir değer yazın.' },
      { key: 'price_override_kurus', label: 'Fiyat geçersiz kılma', type: 'money',
        hint: 'Boş bırakılırsa ürünün kendi fiyatı geçerli. Sıfır geçerlidir '
          + '(paket içinde satılan ekmek, ayran).' },
      { key: 'capacity', label: 'Ürün tavanı (porsiyon)', type: 'number', min: 0,
        hint: 'Boş = sınırsız, 0 = bu kalem bugün satılmıyor.' },
      { key: 'is_required', label: 'Zorunlu kalem', type: 'checkbox',
        hint: 'Zorunlu kalem tükenirse paketin tamamı satıştan düşer.' },
      { key: 'sellable_alone', label: 'Tek başına satılabilir', type: 'checkbox',
        hint: 'Kapalıysa bu kalem yalnız paketin içinde satılır.' },
    ],
    value: {
      label: row.label || '',
      quantity: row.quantity,
      sort_order: row.sort_order,
      price_override_kurus: row.price_override_kurus,
      capacity: row.capacity,
      is_required: row.is_required,
      sellable_alone: row.sellable_alone,
    },
    onChange: () => { saveButton.disabled = form.dirty().length === 0; },
  });

  const saveButton = button('Kaydet', {
    variant: 'primary',
    disabled: true,
    onClick: async () => {
      const patch = form.patch();
      if (!Object.keys(patch).length) return;
      const payload = {};
      for (const [key, value] of Object.entries(patch)) {
        payload[key] = typeof value === 'string' && value.trim() === '' ? null : value;
      }
      // GEREKÇE SORULMAZ (politika): kalemin adedini ya da etiketini düzeltmek
      // menüyü kurmanın parçası. Kaydedildiği yine GÖRÜNÜR.
      //
      // TEK TIKLIK GERİ ALMA BURADA BİTER: "Geri al" düğmesinin gerekçesi
      // kalemin üzerinde hiçbir emek olmamasıydı. Kullanıcı etiketini,
      // fiyatını ya da tavanını yazdıysa o emek var; bundan sonra silmek onay
      // ister.
      forgetJustAdded(row.id);
      handle.close();
      await write(`${BASE}/days/${state.selected}/items/${row.id}`,
        { method: 'PATCH', payload },
        { success: `"${row.name}" güncellendi (${Object.keys(payload).join(', ')}).`,
          after: async () => { await refreshItems(); patchCalendarDay(); } });
    },
  });

  const handle = drawer(nodes.root, {
    title: row.name,
    subtitle: `${longDate(state.selected)} · ürün #${row.menu_id}`,
    actions: [saveButton],
    onClose: () => form.destroy(),
  });
  handle.body.append(form.node,
    hintBox('Ürün burada değiştirilemez: kalemi silip yenisini eklemek gerekir ve '
      + 'denetim izinde iki ayrı satır olarak görünür.'));
  closers.push(handle.close);
}

/**
 * Kalem silme — GEREKÇESİZ ama ONAYLI.
 *
 * Gerekçe kutusu politikayla birlikte kalktı; onay penceresi kalktı DEĞİL.
 * "Az önce eklendi" işaretli satırlar bu yoldan geçmez: onların düğmesi
 * "Geri al"dır ve tek tıkla çalışır (`undoItem`).
 */
async function doDeleteItem(row) {
  const ok = await confirmDestructive({
    title: `"${row.name}" kalemini sil`,
    description: 'GERİ ALINAMAZ: kalemin etiketi, fiyat geçersiz kılması ve tavanı '
      + 'birlikte gider. Bugünün siparişlerinde kullanılmış bir kalemi sunucu ayrıca '
      + 'engeller — geçmiş bozulmaz, ama mutfağın bugün pişirdiği bir kalemi listeden '
      + 'düşürmek istemezsiniz.',
    confirmLabel: 'Kalemi sil',
  });
  if (!ok) return;
  await write(`${BASE}/days/${state.selected}/items/${row.id}`,
    { method: 'DELETE', payload: {} },
    { success: `"${row.name}" silindi.`,
      after: async () => { await refreshItems(); patchCalendarDay(); } });
}

// ------------------------------------------------------------------ stok

function paintStock() {
  // İLK AÇILIŞTA OKUNUR. Gün açılışında çekilmiyor (bkz. `state.stockFor`);
  // sekmeye ilk geçişte bir kez istenir, sonra sekme değiştikçe yeniden
  // istenmez — gün değişene ya da tavan yazılana kadar.
  if (state.stockFor !== state.selected) {
    nodes.tabBody.append(skeletonRows(4, 3));
    ensureStock().then(() => {
      // Sekme hâlâ açık mı ve gün aynı mı: ikisi de değiştiyse çizim başka
      // bir ekranın üstüne yazardı.
      if (state.tab === 'stock' && nodes.tabBody) paintTab();
    });
    return;
  }

  const stock = state.stock;
  if (!stock) {
    nodes.tabBody.append(alertBox('Stok durumu okunamadı'
      + (state.stockError ? `: ${state.stockError}` : '.')
      + ' Tavanları yazmadan önce satılmış porsiyonların görünmesi gerekiyor.',
      'warn'));
    nodes.tabBody.append(button('Yeniden dene', {
      onClick: () => { dropStock(); paintTab(); },
    }));
    return;
  }

  const draft = {
    capacity_total: stock.day.capacity,
    items: Object.fromEntries((stock.items || [])
      .map((item) => [String(item.item_id), item.capacity])),
  };

  const dayBox = h('div', 'bm-stockday');
  dayBox.append(stockMeter('Gün toplamı', stock.day));
  const dayInput = numberInput(stock.day.capacity, (value) => {
    draft.capacity_total = value;
    previewButton.disabled = false;
  });
  const dayField = h('label', 'bm-capfield');
  dayField.append(h('span', 'bm-capfield-label', 'Gün toplam tavanı (porsiyon)'),
    dayInput);
  dayBox.append(dayField);

  const rows = (stock.items || []).map((item) => ({ ...item, id: item.item_id }));
  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kalem', width: 'minmax(0, 2fr)' },
      { key: 'sold', label: 'Rezerve', width: '170px', align: 'num',
        cell: (row) => `${num(row.sold)} (${num(row.sold_orders)} sipariş + `
          + `${num(row.sold_subscriptions)} abonelik)`,
        title: 'Rezerve edilmiş porsiyon — teslim edilmiş değil. İptaller düşülür, '
          + 'abonelikler önceden yer tutar.' },
      { key: 'state', label: 'Kalan', width: '190px', cell: (row) => itemMeter(row) },
      { key: 'capacity', label: 'Yeni tavan', width: '130px',
        cell: (row) => numberInput(row.capacity, (value) => {
          draft.items[String(row.item_id)] = value;
          previewButton.disabled = false;
        }) },
    ],
    rows,
    empty: emptyState({ title: 'Kalem yok', text: 'Önce güne kalem ekleyin.' }),
  });

  const previewButton = button('Tavanları hesapla (kuru prova)', {
    variant: 'primary',
    disabled: true,
    onClick: () => previewStock(draft),
  });
  const actions = h('div', 'bm-formactions');
  actions.append(previewButton);
  // HER ÇİZİMDE YENİ KUTU: eskisini saklamak, sekme değiştikten sonra geri
  // dönüldüğünde artık geçersiz bir jetonun düğmesini ekranda bırakırdı.
  nodes.previewBox = h('div', 'bm-previewbox');

  nodes.tabBody.append(
    card('Gün toplamı', dayBox,
      'İki tavan vardır ve hangisi önce dolarsa satışı o kapatır.'),
    card('Kalem tavanları', table.node),
    actions,
    nodes.previewBox,
    hintBox('Boş tavan SINIRSIZ, sıfır "bugün satılmıyor" demektir; ikisi ayrı '
      + 'şeydir. Tavanı satılmışın altına çekmek serbesttir ve engellenmez — '
      + 'malzeme biter, satışı kapatmak istersiniz; ekran bunu "fazla satılmış" '
      + 'diye yazar, kalanı negatife düşürmez.'),
    hintBox('YENİDEN YAYINLAMAK SAYAÇLARI SIFIRLAMAZ. Günü taslağa çekip tekrar '
      + 'yayınlamak satılmış porsiyonları geri getirmez; yazdığınız tavan aynı '
      + 'satılmış sayısının üstüne oturur.'),
    hintBox('Tavan tablosu TAM LİSTEDİR: kaydettiğinizde tablodaki her kalemin '
      + 'tavanı yazılır. Bu yüzden yazma iki adımlıdır — önce kuru prova, sonra '
      + 'onay. Arada satış değişirse onay reddedilir ve tablo yeniden hesaplanır.'),
  );
  if (state.preview) nodes.previewBox.append(previewPanel(state.preview));
}

/**
 * Kalem satırının kalan/kapasite ölçeri.
 *
 * TAVANSIZ SATIRDA ÇUBUK ÇİZİLMEZ: dolu bir çubuk "sınırsız"ı "doldu" gibi
 * gösterirdi, boş bir çubuk da "hiç satılmamış" gibi. Sınırsız bir satırın
 * doğru gösterimi bir çubuk değil, bir cümledir.
 */
function itemMeter(row) {
  const box = h('div', 'bm-itemmeter');
  const line = h('div', 'bm-itemmarks');
  line.append(h('span', 'bm-meter-value', capacityText(row.capacity, row.remaining)));
  if (row.oversold) line.append(badge('Fazla satılmış', 'bad'));
  else if (row.full) line.append(badge('Doldu', 'warn'));
  if (row.sold_out) line.append(badge('Mutfak tükendi dedi', 'bad'));
  box.append(line);

  if (row.capacity !== null && row.capacity !== undefined && row.capacity > 0) {
    const bar = progress([]);
    const filled = Math.min(100, Math.round((row.sold / row.capacity) * 100));
    bar.percent(filled, `%${filled} dolu`);
    box.append(bar.node);
  }
  return box;
}

function stockMeter(label, line) {
  const box = h('div', 'bm-meter');
  const head = h('div', 'bm-meter-head');
  head.append(h('span', 'bm-meter-label', label));
  head.append(h('span', 'bm-meter-value',
    `${num(line.sold)} rezerve · ${capacityText(line.capacity, line.remaining)} kalan`));
  box.append(head);

  const bar = progress([]);
  if (line.capacity === null || line.capacity === undefined) {
    bar.percent(0, 'Tavan konmamış — sınırsız satış.');
  } else if (line.capacity === 0) {
    bar.percent(100, 'Tavan sıfır: bu gün satılmıyor.');
  } else {
    const filled = Math.min(100, Math.round((line.sold / line.capacity) * 100));
    bar.percent(filled, line.oversold
      ? `Fazla satılmış: ${num(line.sold)} rezerve, tavan ${num(line.capacity)}.`
      : `%${filled} dolu · ${num(line.sold_orders)} sipariş + `
        + `${num(line.sold_subscriptions)} abonelik`);
  }
  box.append(bar.node);
  return box;
}

/** Sayı kutusu — boş bırakmak `null` (sınırsız) demektir, sıfır demek DEĞİL. */
function numberInput(value, onChange) {
  const input = h('input', 'kit-input bm-cap');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.placeholder = 'sınırsız';
  input.value = value === null || value === undefined ? '' : String(value);
  input.setAttribute('aria-label', 'Porsiyon tavanı; boş bırakmak sınırsız demektir');
  input.addEventListener('input', () => {
    const raw = input.value.trim();
    if (raw === '') { onChange(null); input.classList.remove('bad'); return; }
    const parsed = Number(raw.replace(',', '.'));
    const valid = Number.isInteger(parsed) && parsed >= 0;
    input.classList.toggle('bad', !valid);
    if (valid) onChange(parsed);
  });
  return input;
}

/**
 * Kuru prova — ONAY PENCERESİ YOK ve olmamalı.
 *
 * Eskiden buraya da gerekçe soruluyordu: hiçbir şey yazmayan bir hesap için on
 * karakterlik metin istemek, gerekçeyi tam da anlamsızlaştıran şeydi. Yazma
 * kapısı bir sonraki adımda (jetonlu `PUT stock`) duruyor.
 */
async function previewStock(draft) {
  const items = Object.entries(draft.items)
    .map(([id, capacity]) => ({ item_id: Number(id), capacity }));
  const result = await write(`${BASE}/days/${state.selected}/stock/preview`,
    { method: 'POST', payload: { capacity_total: draft.capacity_total, items } },
    { expectDryRun: true });
  if (!result) return;
  state.preview = { ...result };
  nodes.previewBox?.replaceChildren(previewPanel(state.preview));
}

function previewPanel(preview) {
  const box = h('div', 'bm-preview');
  box.append(h('h4', 'bm-preview-title', 'Kuru prova sonucu'));
  const warnings = preview.warnings || [];
  if (warnings.length) {
    for (const warning of warnings) {
      box.append(alertBox(`${warning.label}: kalem #${warning.item_id} için tavan `
        + `${num(warning.capacity ?? 0)}, rezerve ${num(warning.sold)}. `
        + 'Satış bu kalemde kapanır; girmiş siparişler etkilenmez.', 'warn'));
    }
  } else {
    box.append(alertBox('Uyarı yok: hiçbir tavan satılmışın altına düşmüyor.', 'good'));
  }
  box.append(h('p', 'bm-preview-note',
    `Bu tablo ${preview.expires_in_minutes} dakika geçerli. Onaylamadan önce satış `
    + 'değişirse uygulama reddedilir ve yeniden hesaplarsınız. "Tavanları uygula" '
    + 'başka bir onay sormaz: gördüğünüz tablo DOĞRUDAN yazılır.'));

  const actions = h('div', 'bm-formactions');
  actions.append(button('Tavanları uygula', {
    variant: 'primary',
    onClick: () => applyStock(preview),
  }));
  actions.append(button('Vazgeç', {
    onClick: () => { state.preview = null; nodes.previewBox?.replaceChildren(); },
  }));
  box.append(actions);
  return box;
}

/**
 * Tavanları yazar. GEREKÇE SORULMAZ (politika) ve İKİNCİ BİR ONAY DA SORULMAZ:
 * onay zaten bu akışın kendisidir — kullanıcı önce kuru provayı koşturdu,
 * uyarıları okudu ve elindeki jeton tam olarak gördüğü tabloyu temsil ediyor.
 * Üstüne bir "emin misiniz" penceresi koymak, okunan bir onayın üstüne
 * okunmayan bir onay eklemekti.
 */
async function applyStock(preview) {
  await write(`${BASE}/days/${state.selected}/stock`,
    { method: 'PUT', payload: { token: preview.token } },
    { success: 'Tavanlar yazıldı.',
      after: async () => {
        state.preview = null;
        dropStock();                      // tavan değişti, sayılar yeniden okunmalı
        await reloadDay({ quiet: true });
        await reloadCalendar({ quiet: true });
      } });
}

// ================================================================ eylemler

/**
 * NE YAYINLANDIĞININ ÖZETİ — gerekçe kutusunun hemen üstünde durur.
 *
 * Gerekçe artık yalnız burada isteniyorsa, kullanıcının gerekçeyi NEYE
 * yazdığını görmesi gerekir. Dört sayı yeterli ve dördü de yayınla düğmesine
 * basmadan önce kontrol edilmesi gereken şeyler: kaç kalem gidiyor, paket kaça
 * satılıyor, günün tavanı ne, sipariş hangi saatte kapanıyor.
 *
 * Satır sonları `panel.css` içindeki `white-space: pre-line` ile görünür;
 * `confirmWithReason` açıklamayı düz metin olarak yazıyor (`innerHTML` yok).
 */
function publishSummary(day) {
  const capacity = day.capacity_total;
  return [
    longDate(state.selected),
    `· ${num(day.item_count)} kalem`,
    `· Paket fiyatı: ${day.package_price_kurus === null
      ? 'yok — yalnız kalemler tek tek satılır' : money(day.package_price_kurus)}`,
    `· Gün tavanı: ${capacity === null || capacity === undefined ? 'sınırsız'
      : (capacity === 0 ? '0 — bu gün satılmıyor' : `${num(capacity)} porsiyon`)}`,
    `· Kesim saati: ${day.cutoff_time ? `${day.cutoff_time} (güne özel)`
      : `${globalCutoff()} (genel ayar)`}`,
  ].join('\n');
}

async function doPublish() {
  const day = state.day;
  const reason = await askReason({
    title: 'Günü yayınla — müşteriye açılıyor',
    description: `${publishSummary(day)}\n\nYayınlandığı anda müşteri bu güne `
      + 'sipariş verebilir. Yeniden yayınlamak satılmış porsiyonları SIFIRLAMAZ.',
    confirmLabel: 'Yayınla',
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}/publish`,
    { method: 'POST', payload: { reason } },
    { success: 'Gün yayınlandı.',
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
}

/**
 * Günü satışa açar — `POST days/{date}/open-sale`.
 *
 * İKİ ADIM TEK ÇAĞRIDA: eksik "zorunlu kalem" işaretleri kurulur (yoksa BLD
 * paketi hiç satmıyor, sitede paket kartı çizilmiyor) ve gün taslaktaysa
 * yayınlanır. Ayrıntı ve gerekçe kararı `MenuService.open_sale` başlığındadır.
 *
 * `auto` KENDİLİĞİNDEN ÇAĞRILDIĞINI söyler ve YALNIZ DİLİ değiştirir: elle
 * basılan düğme sessiz kalamaz ("bir şey oldu mu?"), kendiliğinden koşan akış
 * ise yalnız GERÇEKTEN bir şey yaptıysa konuşmalı — her fiyat kaydından sonra
 * "gün zaten yayında" diye bilgi vermek, okunmayı bırakılan bir bildirim
 * üretirdi.
 */
async function openSale({ auto = false } = {}) {
  const result = await write(`${BASE}/days/${state.selected}/open-sale`,
    { method: 'POST', payload: {} },
    { after: async () => {
      await reloadDay({ quiet: true });
      await reloadCalendar({ quiet: true });
    } });
  if (!result) return null;

  const parts = [];
  if (result.required_items) parts.push(`${num(result.required_items)} kalem zorunlu yapıldı`);
  if (result.published) parts.push('gün yayına alındı');

  if (parts.length) {
    // Cümle NE OLDUĞUNU sayar, "başarılı" demez: yönetici kaç kaleme
    // dokunulduğunu bilmeden ekranı kapatmamalı.
    toast(`Satışa açıldı — ${parts.join(', ')}.`, 'good');
  } else if (result.note) {
    // Yarım kalan adım HER ZAMAN söylenir, otomatik yolda bile: "paket fiyatı
    // girdim ama satılmıyor" sorusunun tekrar doğmasının tek yolu bu cümleyi
    // yutmaktır.
    toast(result.note, 'warn');
  } else if (!auto) {
    toast(result.already ? 'Gün zaten yayında ve paket satılabilir durumda.'
      : 'Değişen bir şey yok.', 'warn');
  }
  return result;
}

/** Toplu açmanın aralığı: bugünden ileriye, sözleşmenin izin verdiği kadar. */
function forwardRange() {
  const span = Math.max(1, Math.min(92, state.contract?.limits?.calendar_max_days || 92));
  const start = new Date(`${todayIso()}T00:00:00`);
  const end = new Date(start);
  end.setDate(start.getDate() + span - 1);
  return { from: todayIso(), to: isoOf(end) };
}

/**
 * BUGÜNDEN İLERİYE bütün uygun günleri satışa açar — `POST open-sale`.
 *
 * İKİ AŞAMALI ve bu bilinçli: önce kuru prova koşuyor, kullanıcı KAÇ GÜNE
 * dokunulacağını ve hangilerinin atlanacağını görüyor, sonra onaylıyor. Toplu
 * ve müşteriye görünür bir işlemde "ne olacağını görmeden onayla" demek, onayı
 * biçimsel bir tıklamaya indirirdi.
 */
async function openSaleRange() {
  const range = forwardRange();
  const body = { date_from: range.from, date_to: range.to };

  const prova = await write(`${BASE}/open-sale`,
    { method: 'POST', payload: { ...body, dryRun: true } },
    { expectDryRun: true });
  if (!prova) return;

  const t = prova.totals || {};
  if (!t.candidates) {
    toast('Bugünden ileriye paket fiyatı girilmiş bir gün yok.', 'warn');
    return;
  }
  if (!t.published && !t.required_items) {
    toast(`${num(t.candidates)} günün hepsi zaten satışta.`, 'good');
    return;
  }

  // Hangi günler DEĞİŞECEK — sayı tek başına "hangi gün" sorusunu
  // cevaplamıyor ve toplu işlemde asıl merak edilen o.
  const degisen = (prova.days || []).filter((row) => row.published || row.required_items);
  const liste = degisen.slice(0, 12).map((row) => `· ${row.date}`).join('\n');
  const fazlasi = degisen.length > 12 ? `\n· … ve ${num(degisen.length - 12)} gün daha` : '';

  const onay = await confirmSimple(nodes.root, {
    title: 'Menüleri satışa aç',
    description: `${range.from} — ${range.to} arası ${num(t.candidates)} günde paket `
      + `fiyatı girilmiş.\n\n${num(t.published)} gün yayına alınacak, `
      + `${num(t.required_items)} kalem "zorunlu" yapılacak.`
      + (t.skipped ? ` ${num(t.skipped)} gün atlanacak (kalemsiz ya da kesim saati geçmiş).`
        : '')
      + `\n\n${liste}${fazlasi}\n\nYayınlanan günler müşteriye ANINDA görünür. `
      + 'Geri almak gün gün "Yayından çek" ile yapılır.',
    confirmLabel: 'Satışa aç',
  });
  if (!onay) return;

  const sonuc = await write(`${BASE}/open-sale`,
    { method: 'POST', payload: body },
    { after: async () => {
      await reloadCalendar({ quiet: true });
      await reloadDay({ quiet: true });
    } });
  if (!sonuc) return;

  const bitti = sonuc.totals || {};
  const parcalar = [`${num(bitti.published)} gün yayına alındı`];
  if (bitti.required_items) parcalar.push(`${num(bitti.required_items)} kalem zorunlu yapıldı`);
  if (bitti.already) parcalar.push(`${num(bitti.already)} gün zaten satıştaydı`);
  if (bitti.skipped) parcalar.push(`${num(bitti.skipped)} gün atlandı`);
  toast(parcalar.join(', ') + '.', bitti.skipped ? 'warn' : 'good');
}

async function doUnpublish() {
  const reason = await askReason({
    title: 'Günü yayından çek — müşteriden kalkıyor',
    description: `${publishSummary(state.day)}\n\nBu gün satış kanalından düşecek ve `
      + 'taslağa dönecek. Kalemler ve tavanlar YERİNDE KALIR; yeniden yayınlamak tek '
      + 'tıktır. O güne sipariş girmişse sunucu bunu engeller — satılmış bir günü '
      + 'müşteriden gizlemek doğru olmazdı.',
    confirmLabel: 'Yayından çek',
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}/unpublish`,
    { method: 'POST', payload: { reason } },
    { success: 'Gün taslağa çekildi.',
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
}

async function doDeleteDay() {
  const day = state.day;
  const reason = await askReason({
    title: 'Menü gününü sil',
    description: `${longDate(state.selected)} ve ${num(day.item_count)} kalemi GERİ `
      + 'ALINAMAZ biçimde silinecek: fiyat geçersiz kılmaları, tavanlar ve etiketler '
      + 'birlikte gider. Yalnız taslak günler silinebilir; siparişi olan gün sunucuda '
      + 'ayrıca engellenir.',
    confirmLabel: 'Günü sil',
    danger: true,
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}`,
    { method: 'DELETE', payload: { reason } },
    { success: 'Menü günü silindi.',
      after: async () => {
        state.day = null;
        state.exists = false;
        clearJustAdded();
        await reloadCalendar({ quiet: true });
        paintDay();
      } });
}

// -------------------------------------------------------------- gün kurma

function openDayCreator() {
  const form = formGrid({
    fields: [
      { key: 'title', label: 'Başlık', type: 'text', maxLength: 120, wide: true,
        placeholder: 'Ev Yemeği Menüsü' },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500,
        wide: true, hint: 'Müşteriye görünür.' },
      { key: 'internal_note', label: 'İç not', type: 'textarea', maxLength: 255,
        wide: true, hint: 'Müşteriye GİTMEZ.' },
      { key: 'package_price_kurus', label: 'Paket fiyatı', type: 'money',
        hint: 'Boş bırakılırsa paket satılmaz. Sıfır kabul edilmez.' },
      { key: 'components_sellable', label: 'Kalemler tek tek satılabilir',
        type: 'checkbox' },
      { key: 'cutoff_time', label: 'Güne özel kesim saati', type: 'text',
        maxLength: 5, placeholder: 'ss:dd',
        hint: `Boş bırakılırsa genel ayar geçerli: ${globalCutoff()}.` },
      { key: 'capacity_total', label: 'Gün toplam tavanı (porsiyon)', type: 'number',
        min: 0, hint: 'Boş = sınırsız.' },
    ],
    value: { components_sellable: true },
  });

  const createButton = button('Günü kur (taslak)', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      const payload = {
        title: draft.title || null,
        description: draft.description || null,
        internal_note: draft.internal_note || null,
        package_price_kurus: draft.package_price_kurus ?? null,
        components_sellable: Boolean(draft.components_sellable),
        cutoff_time: draft.cutoff_time || null,
        capacity_total: draft.capacity_total ?? null,
        items: [],
      };
      // GEREKÇE SORULMAZ (politika): taslak kurmak bir taahhüt değil. Gün
      // `draft` doğuyor, müşteri onu görmüyor; taahhüt yayın anındadır.
      handle.close();
      await write(`${BASE}/days`,
        { method: 'POST', payload: { date: state.selected, ...payload } },
        { success: 'Gün kuruldu (taslak). Kalem kutusu açıldı: yazın, Enter\'a basın.',
          after: async () => {
            clearJustAdded();
            await reloadCalendar({ quiet: true });
            // Kalem sekmesi ÖNCEDEN seçilir: `reloadDay` sağ panoyu baştan
            // çiziyor ve sekmeyi sonradan değiştirmek ikinci bir çizim demekti.
            state.tab = 'items';
            await reloadDay({ quiet: true });
            // Yeni günün ilk işi kalem girmek; odak oraya verilir.
            resetQuickSearch();
          } });
    },
  });

  const handle = drawer(nodes.root, {
    title: 'Menü günü kur',
    subtitle: longDate(state.selected),
    actions: [createButton],
    onClose: () => form.destroy(),
  });
  handle.body.append(
    hintBox('Gün her zaman TASLAK doğar. Yarım girilmiş bir gün kaydedildiği anda '
      + 'müşteriye görünmemeli; yayın ayrı bir eylemdir.'),
    form.node,
    hintBox('Kalemleri gün kurulduktan sonra ekleyeceksiniz. Yayınlamak için en az '
      + 'bir kalem gerekli.'),
  );
  closers.push(handle.close);
}

// ------------------------------------------------------------- kopyalama

/**
 * `toHere: true` → BU güne başka bir günden kopyala (kaynak seçilir).
 * `toHere: false` → BU günü başka bir güne kopyala (hedef seçilir).
 *
 * İki yön de gerekiyor: takvimi dolduran yönetici bazen boş bir güne bakıp
 * "geçen salıyı buraya getir" diyor, bazen dolu bir güne bakıp "bunu önümüzdeki
 * salıya da koy" diyor. Tek yön bırakmak, ikinci hâlde önce hedefe gidip sonra
 * kaynağı hatırlamayı gerektirirdi.
 */
function openDuplicate({ toHere }) {
  const other = dateField({
    value: toHere ? todayIso(-7) : todayIso(7),
    label: toHere ? 'Kaynak gün' : 'Hedef gün',
    onChange: () => { hint.textContent = describe(); },
  });

  const overwrite = h('input', 'kit-check');
  overwrite.type = 'checkbox';
  const overwriteLabel = h('label', 'bm-check');
  overwriteLabel.append(overwrite,
    h('span', undefined, 'Hedefte taslak bir gün varsa üzerine yaz'));

  const hint = h('p', 'bm-preview-note');
  const describe = () => {
    const source = toHere ? other.get() : state.selected;
    const target = toHere ? state.selected : other.get();
    return `${longDate(source)} → ${longDate(target)}`;
  };
  hint.textContent = describe();

  const copyButton = button('Kopyala', {
    variant: 'primary',
    onClick: async () => {
      const source = toHere ? other.get() : state.selected;
      const target = toHere ? state.selected : other.get();
      if (!source || !target) { toast('Gün seçilmedi.', 'warn'); return; }
      const reason = await askReason({
        title: 'Günü kopyala',
        description: `${describe()} · hedef gün TASLAK doğar, kaynak yayında olsa `
          + 'bile. Görsel ve iç not kopyalanmaz: güne özgüdürler ve kopyalamak '
          + 'yanlış fotoğrafı yayınlatırdı.',
        confirmLabel: 'Kopyala',
      });
      if (!reason) return;
      handle.close();
      await write(`${BASE}/days/${source}/duplicate`,
        { method: 'POST',
          payload: { reason, target_date: target, overwrite: overwrite.checked } },
        { success: 'Menü kopyalandı (taslak).',
          after: async () => {
            state.selected = target;
            state.month = monthOf(target);
            state.draft = [];        // taslak kaynak güne aitti
            clearJustAdded();
            nodes.calendar.setMonthFrom(target);
            await reloadCalendar({ quiet: true });
            await reloadDay({ quiet: true });
          } });
    },
  });

  // `dateField` GLOBAL DİNLEYİCİ TUTUYOR ve çekmece kapanınca bırakılmalı;
  // panelin `cleanup`'ına ertelemek, çekmece her açılışta bir dinleyici daha
  // biriktirmek demekti.
  const handle = drawer(nodes.root, {
    title: 'Günü kopyala',
    subtitle: longDate(state.selected),
    actions: [copyButton],
    onClose: () => other.destroy(),
  });
  handle.body.append(
    hintBox('Haftalık menü kurarken en büyük zaman kazancı budur. Kopyalananlar: '
      + 'başlık, açıklama, paket fiyatı, bileşen satışı, kesim saati, gün tavanı ve '
      + 'TÜM kalemler (kalem tavanları dâhil).'),
    other.node,
    overwriteLabel,
    hint,
    hintBox('Üzerine yazma yalnız TASLAK bir hedefte çalışır; yayınlanmış bir günün '
      + 'üzerine kopyalamayı sunucu reddeder.'),
  );
  closers.push(handle.close);
}

// ==================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  // `justAdded` DİZİSİ TAZE ÜRETİLİR: `{ ...EMPTY_STATE }` sığ kopya yapıyor
  // ve şablondaki diziyi paylaşmak, kapatılıp yeniden açılan panelde önceki
  // oturumun "az önce eklendi" izini geri getirirdi.
  state = { ...EMPTY_STATE, justAdded: [], draft: [],
    month: monthOf(todayIso()), selected: todayIso() };

  const view = h('div', 'kit-panel bm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.status = statusLine();
  nodes.summary = h('div', 'bm-summary');
  nodes.refreshHint = h('span', 'bm-refreshhint');

  nodes.calendar = monthCalendar({
    month: state.month,
    selected: state.selected,
    weekend: true,
    renderBadge: badgeFor,
    dayTitle: dayTitleFor,
    // AÇIKLAMA SÖZLEŞMENİN PARÇASI (kit kuralı 7): rozeti çizen, ne demek
    // olduğunu yazan listeyi de vermek zorunda.
    legend: [
      { text: 'Yayın = müşteriye görünüyor', tone: 'good' },
      { text: 'Taslak = girildi, yayınlanmadı', tone: 'warn' },
      { text: 'Tükendi = tavan doldu', tone: 'bad' },
      { text: 'rozet yok = menü girilmemiş' },
    ],
    onPick: (iso) => {
      // KAYDEDİLMEMİŞ KALEM VARSA UYAR. Taslak seçili GÜNE aittir; sessizce
      // taşımak başka günün menüsüne yabancı kalem koyardı, sessizce atmak da
      // kullanıcının yazdığını kaybettirirdi.
      if (state.draft.length && iso !== state.selected) {
        const kalan = state.draft.length;
        state.draft = [];
        toast(`${num(kalan)} kaydedilmemiş kalem düşürüldü — gün değişti.`, 'warn');
      }
      state.selected = iso;
      // SEKME KORUNUR. Eskiden her gün seçiminde "Gün bilgileri"ne dönülüyordu:
      // takvimi dolduran yönetici gün → sekme → gün → sekme diye gidiyor,
      // her gün için fazladan bir tık atıyordu. Menü girmek arka arkaya
      // yapılan bir iştir; ekran kullanıcının nerede çalıştığını hatırlamalı.
      //
      // "Stok" sekmesi de korunur ve stok o gün için bir kez okunur — gün
      // açılışında değil (bkz. `ensureStock`).
      state.preview = null;
      clearJustAdded();          // iz seçili güne aitti, yenisine taşınmaz
      reloadDay();
    },
    onMonth: (monthKey) => {
      state.month = monthKey;
      reloadCalendar();
    },
    // TATİL İŞARETLEME YOK: kapalı günler `settings/closed-days` alanında
    // yönetiliyor ve bu ekranın ucu değil. Sağ tık menüsünü bağlamak,
    // çalışmayan bir kısayol bırakmak olurdu.
  });

  const left = h('div', 'bm-left');
  const leftHead = h('div', 'bm-lefthead');
  leftHead.append(
    button('Yenile', { onClick: () => reloadCalendar() }),
    // TAKVİMİN YANINDA, gün düzenleyicisinde değil: işlem TEK GÜNE ait değil,
    // bugünden ileriye bütün takvime ait.
    button('Tümünü satışa aç', {
      title: 'Bugünden ileriye paket fiyatı girilmiş bütün günleri satışa açar. '
        + 'Önce ne olacağını gösterir.',
      onClick: () => openSaleRange(),
    }),
    nodes.refreshHint,
  );
  left.append(
    nodes.summary,
    card('Menü takvimi', nodes.calendar.node,
      'Bir güne tıklayın; sağda o günün düzenleyicisi açılır.'),
    leftHead,
    hintBox('HAFTA SONU GRİ BOYANIR AMA SATIŞ KANALI AÇIKTIR: Cumartesi girilen bir '
      + 'sipariş Pazartesi\'ye yazılabilir. Gri "menü yok" demektir, "sipariş '
      + 'alınmaz" demez.'),
    hintBox(`Her servis günü kendi sabah kesim saatinde kapanır. Genel ayar: `
      + `${globalCutoff()}; güne özel saat onu ezer.`),
  );

  nodes.right = h('div', 'bm-right');
  const split = splitView(left, nodes.right, 'minmax(0, 380px) minmax(0, 1fr)');

  const bar = h('div', 'bm-topbar');
  bar.append(h('h1', 'bm-title', 'Menü Yönetimi'));
  view.append(bar, nodes.status.node, split);

  root.replaceChildren(view);

  // İlk yükleme: önce takvim (küresel kesim saati ve rozetler oradan gelir),
  // sonra seçili gün. Sıra ters olsaydı gün düzenleyicisi "genel ayar: —"
  // yazardı ve yönetici boş bırakılan kesim saatinin ne olduğunu göremezdi.
  //
  // YOKLAMA İLK YANITTAN SONRA KURULUR: aralık modül ayarından geliyor ve
  // burada hemen kurulsaydı sözleşme henüz elimizde olmadığı için her zaman
  // yedek değere düşerdi — ayarı değiştiren kurulumda hiçbir etkisi olmazdı.
  alive = true;
  reloadCalendar().then(() => {
    if (!alive) return;
    reloadDay();
    poll = pollLoop({
      // Sekme gizliyken durur: dört BLD paneli aynı anda yoklarsa arka planda
      // duran bir pencere paylaşılan 3000/saat bütçesini boşuna yakar.
      every: (state.contract?.refresh_seconds || 0) * 1000 || FALLBACK_REFRESH_MS,
      run: () => reloadCalendar({ quiet: true }),
    });
  });

  return () => {
    alive = false;
    poll?.stop();
    poll = null;
    nodes.calendar?.destroy();
    nodes.dayForm?.destroy();       // tarih alanları global dinleyici tutuyor
    nodes.dayForm = null;
    // Hızlı ekleme kutusunun düğümleri: `createPicker` global dinleyici
    // tutmuyor (Enter dinleyicisi kendi düğümünde) ama ölü düğümlere işaret
    // eden `nodes` alanları bırakmak, sonraki mount'ta sessiz bir arıza olurdu.
    dropItemNodes();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE, justAdded: [] };
    busy = false;
  };
}
