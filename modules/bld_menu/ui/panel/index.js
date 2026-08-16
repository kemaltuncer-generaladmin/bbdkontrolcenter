// Menü Yönetimi paneli — günlük menü takviminin Kontrol Merkezi'nden yönetimi.
//
// NE YAPAR: ay ızgarasında hangi güne ne girildiğini gösterir (yayında ·
// taslak · menü yok · kapalı gün · tükendi); seçilen günün başlığını,
// açıklamasını, iç notunu, paket fiyatını, kesim saatini ve bileşen satışını
// düzenler; kalem ekler, sırasını ve fiyat geçersiz kılmasını değiştirir,
// zorunlu/tek satılır işaretlerini kurar; günü yayınlar ve taslağa çeker;
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
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_menu/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_menu/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, confirmWithReason, h, isoOf, loadStyles, longDate, money, num,
  pollLoop, toaster, todayIso,
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
  products: [],
  productsError: '',
  tab: 'day',
  preview: null,
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

/** Gerekçeli onay — her yazma buradan geçer (`00-genel.md` §3, ADR 0012). */
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
      ? { limits: result.limits, refresh_seconds: result.refresh_seconds }
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

async function reloadDay({ quiet = false } = {}) {
  if (!state.selected) return;
  const iso = state.selected;
  if (!quiet) nodes.right.replaceChildren(skeletonRows(6, 3));
  try {
    const [dayResult, stockResult] = await Promise.all([
      call(`${BASE}/days/${iso}`),
      call(`${BASE}/days/${iso}/stock`).catch(() => null),
    ]);
    if (state.selected !== iso) return;      // kullanıcı aradan gün değiştirdi
    state.exists = Boolean(dayResult.exists);
    // BOŞ SÖZLÜK "veri var" DEĞİLDİR. Servis bağlanamadığında ya da gün
    // olmadığında `{}` döndürüyor ve `payload || null` yazmak onu doğru
    // sanardı; ekran da `day.title` okumaya çalışıp çökerdi.
    state.day = state.exists ? (dayResult.day || null) : null;
    state.stock = stockResult?.stock?.day ? stockResult.stock : null;
    state.preview = null;
  } catch (failure) {
    if (state.selected !== iso) return;
    state.exists = false;
    state.day = null;
    state.stock = null;
    toast(failure.message, 'bad');
  }
  paintDay();
}

async function ensureProducts() {
  if (state.products.length || state.productsError) return;
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

function paintDay() {
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
      actions.append(button('Yayından çek', { onClick: () => doUnpublish() }));
    } else {
      actions.append(button('Yayınla', { variant: 'primary', onClick: () => doPublish() }));
    }
    actions.append(button('Kopyala', { onClick: () => openDuplicate({ toHere: false }) }));
    actions.append(button('Sil', { variant: 'danger', onClick: () => doDeleteDay() }));
  }
  head.append(left, actions);
  return head;
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
  const reason = await askReason({
    title: 'Gün bilgilerini kaydet',
    description: `${longDate(state.selected)} · değişen alanlar: `
      + `${Object.keys(payload).join(', ')}`,
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}`,
    { method: 'PATCH', payload: { reason, ...payload } },
    { success: 'Gün bilgileri kaydedildi.',
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
}

// -------------------------------------------------------------- kalemler

function paintItems() {
  const items = state.day.items || [];
  const table = dataTable({
    columns: [
      { key: 'sort_order', label: 'Sıra', width: '70px', align: 'num' },
      { key: 'name', label: 'Kalem', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('div', 'bm-itemname');
          box.append(h('b', undefined, row.name || `#${row.menu_id}`));
          const marks = h('div', 'bm-itemmarks');
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
      { key: 'actions', label: '', width: '150px',
        cell: (row) => {
          const box = h('div', 'bm-rowactions');
          box.append(button('Düzenle', { variant: 'ghost', onClick: () => openItemEditor(row) }));
          box.append(button('Sil', { variant: 'ghost', onClick: () => doDeleteItem(row) }));
          return box;
        } },
    ],
    rows: items,
    empty: emptyState({
      title: 'Bu günde kalem yok',
      text: 'Yayınlamak için en az bir kalem gerekli. Kalemsiz bir gün müşteriye '
        + 'boş bir menü kartı gösterirdi.',
    }),
  });

  const actions = h('div', 'bm-formactions');
  actions.append(button('Kalem ekle', { variant: 'primary',
    onClick: () => openItemPicker() }));

  nodes.tabBody.append(
    card('Kalemler', table.node,
      'Sıra sunucudan gelir: çorba → ana yemek → pilav → tatlı.'),
    actions,
    hintBox('Kalemin ürünü değiştirilemez. Ürünü değiştirmek kalemi silip yenisini '
      + 'eklemektir ve denetim izinde iki ayrı satır olarak görünmelidir.'),
  );
}

async function openItemPicker() {
  await ensureProducts();
  const used = new Set((state.day.items || []).map((item) => item.menu_id));
  const rows = state.products
    .filter((row) => !used.has(row.menu_id))
    .map((row) => ({
      id: row.menu_id,
      name: row.name,
      group: row.category || 'Diğer',
      meta: `${money(row.price_kurus)}${row.sold_out ? ' · mutfak tükendi dedi' : ''}`,
    }));

  const body = h('div');
  if (state.productsError) {
    body.append(alertBox(`Ürün listesi alınamadı: ${state.productsError} `
      + 'Ürün kimliğini elle girebilirsiniz.', 'warn'));
  }
  body.append(hintBox('Listede yalnız SATIŞTAKİ ürünler var. Satıştan kalkmış bir '
    + 'ürünü içeren gün yayınlanamıyor; burada göstermek, kurduğunuz menünün '
    + 'yayınlanmayacağını size ancak yayın düğmesinde söylemek olurdu. Menüde '
    + 'zaten olan ürünler de listede yok.'));

  const picker = createPicker({
    items: rows,
    groupLabel: 'Kategori',
    placeholder: 'Ürün ara',
    single: true,
    onChange: (ids) => { chosen = ids[0] || ''; addButton.disabled = !chosen; },
  });
  let chosen = '';
  body.append(picker.node);

  const addButton = button('Seçilen ürünü ekle', {
    variant: 'primary',
    disabled: true,
    onClick: async () => {
      const menuId = Number(chosen);
      if (!menuId) return;
      const product = state.products.find((row) => row.menu_id === menuId);
      const reason = await askReason({
        title: 'Menüye kalem ekle',
        description: `${longDate(state.selected)} · ${product?.name || `#${menuId}`}`,
        confirmLabel: 'Ekle',
      });
      if (!reason) return;
      handle.close();
      await write(`${BASE}/days/${state.selected}/items`,
        { method: 'POST', payload: { reason, menu_id: menuId, quantity: 1,
          is_required: false, sellable_alone: true } },
        { success: 'Kalem eklendi.',
          after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
    },
  });

  const handle = drawer(nodes.root, {
    title: 'Kalem ekle',
    subtitle: longDate(state.selected),
    actions: [addButton],
  });
  handle.body.append(body);
  closers.push(handle.close);
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
      const reason = await askReason({
        title: 'Kalemi güncelle',
        description: `${longDate(state.selected)} · ${row.name} · `
          + `değişen alanlar: ${Object.keys(payload).join(', ')}`,
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      handle.close();
      await write(`${BASE}/days/${state.selected}/items/${row.id}`,
        { method: 'PATCH', payload: { reason, ...payload } },
        { success: 'Kalem güncellendi.',
          after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
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

async function doDeleteItem(row) {
  const reason = await askReason({
    title: `"${row.name}" kalemini sil`,
    description: 'GERİ ALINAMAZ: kalemin etiketi, fiyat geçersiz kılması ve tavanı '
      + 'birlikte gider. Bugünün siparişlerinde kullanılmış bir kalemi sunucu ayrıca '
      + 'engeller — geçmiş bozulmaz, ama mutfağın bugün pişirdiği bir kalemi listeden '
      + 'düşürmek istemezsiniz.',
    confirmLabel: 'Kalemi sil',
    danger: true,
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}/items/${row.id}`,
    { method: 'DELETE', payload: { reason } },
    { success: 'Kalem silindi.',
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
}

// ------------------------------------------------------------------ stok

function paintStock() {
  const stock = state.stock;
  if (!stock) {
    nodes.tabBody.append(alertBox('Stok durumu okunamadı. Tavanları yazmadan önce '
      + 'satılmış porsiyonların görünmesi gerekiyor; ekranı tazeleyin.', 'warn'));
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

async function previewStock(draft) {
  const reason = await askReason({
    title: 'Tavanları hesapla',
    description: 'Kuru prova koşulacak: sunucu tavanların kaç siparişin altında '
      + 'kaldığını hesaplayacak. Hiçbir şey yazılmayacak.',
    confirmLabel: 'Hesapla',
  });
  if (!reason) return;
  const items = Object.entries(draft.items)
    .map(([id, capacity]) => ({ item_id: Number(id), capacity }));
  const result = await write(`${BASE}/days/${state.selected}/stock/preview`,
    { method: 'POST', payload: { reason, capacity_total: draft.capacity_total, items } },
    { expectDryRun: true });
  if (!result) return;
  state.preview = { ...result, reason };
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
    + 'değişirse uygulama reddedilir ve yeniden hesaplarsınız.'));

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

async function applyStock(preview) {
  const reason = await askReason({
    title: 'Tavanları uygula',
    description: 'Onayladığınız tablo yazılacak. Tablo TAM LİSTEDİR: her kalemin '
      + 'tavanı gördüğünüz hâliyle geçerli olur.',
    confirmLabel: 'Uygula',
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}/stock`,
    { method: 'PUT', payload: { reason, token: preview.token } },
    { success: 'Tavanlar yazıldı.',
      after: async () => {
        state.preview = null;
        await reloadDay({ quiet: true });
        await reloadCalendar({ quiet: true });
      } });
}

// ================================================================ eylemler

async function doPublish() {
  const day = state.day;
  const reason = await askReason({
    title: 'Günü yayınla',
    description: `${longDate(state.selected)} · ${num(day.item_count)} kalem · `
      + `${day.package_price_kurus === null ? 'paket satılmıyor'
        : money(day.package_price_kurus)}. Yayınlandığı anda müşteri bu güne sipariş `
      + 'verebilir. Yeniden yayınlamak satılmış porsiyonları SIFIRLAMAZ.',
    confirmLabel: 'Yayınla',
  });
  if (!reason) return;
  await write(`${BASE}/days/${state.selected}/publish`,
    { method: 'POST', payload: { reason } },
    { success: 'Gün yayınlandı.',
      after: async () => { await reloadDay({ quiet: true }); await reloadCalendar({ quiet: true }); } });
}

async function doUnpublish() {
  const reason = await askReason({
    title: 'Günü yayından çek',
    description: `${longDate(state.selected)} satış kanalından düşecek ve taslağa `
      + 'dönecek. Kalemler ve tavanlar YERİNDE KALIR; yeniden yayınlamak tek tıktır. '
      + 'O güne sipariş girmişse sunucu bunu engeller — satılmış bir günü müşteriden '
      + 'gizlemek doğru olmazdı.',
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
      const reason = await askReason({
        title: 'Menü günü kur',
        description: `${longDate(state.selected)} · gün TASLAK doğar, yayın ayrı bir `
          + 'eylemdir ve ayrı bir denetim satırı bırakır.',
        confirmLabel: 'Kur',
      });
      if (!reason) return;
      handle.close();
      await write(`${BASE}/days`,
        { method: 'POST', payload: { reason, date: state.selected, ...payload } },
        { success: 'Gün kuruldu (taslak). Şimdi kalem ekleyin.',
          after: async () => {
            await reloadCalendar({ quiet: true });
            await reloadDay({ quiet: true });
            state.tab = 'items';
            paintTab();
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
  closers.push(() => other.destroy());

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
            nodes.calendar.setMonthFrom(target);
            await reloadCalendar({ quiet: true });
            await reloadDay({ quiet: true });
          } });
    },
  });

  const handle = drawer(nodes.root, {
    title: 'Günü kopyala',
    subtitle: longDate(state.selected),
    actions: [copyButton],
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
  state = { ...EMPTY_STATE, month: monthOf(todayIso()), selected: todayIso() };

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
      state.selected = iso;
      state.tab = 'day';
      state.preview = null;
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
  leftHead.append(button('Yenile', { onClick: () => reloadCalendar() }), nodes.refreshHint);
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
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
