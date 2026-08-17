// Elle Sipariş paneli — personel MÜŞTERİYLE TELEFONDAYKEN kullanır.
//
// EKRANIN TASARIM ÖLÇÜSÜ TEK: her fazladan tık, hattaki müşteriyi bekletir.
// Bu yüzden burada SEKME YOKTUR, sihirbaz YOKTUR, ayrı müşteri ekranı YOKTUR.
// Tek sayfa, dört blok, aşağıda duran bir özet şeridi: müşteri → gün ve
// teslimat → kalemler → kaydet.
//
// NE YAPAR: telefonla müşteri arar; bulamazsa AYNI EKRANDA ad+telefonla açar;
// servis gününü seçer (kendi takvimimiz); ürünleri çoklu seçiciden tık tık
// biriktirip tek düğmeyle sepete atar; adet, not, teslimat türü, adres, ödeme
// yöntemi ve müşteri notunu alır; tahmini toplamı canlı gösterir; siparişi
// açar ve SİPARİŞ NUMARASINI söyler.
//
// NE YAPMAZ:
//  · SİPARİŞ PENCERESİNİ YENİDEN UYGULAMAZ. Kesim saati, ileri görüş sınırı,
//    sipariş alım şalteri, asgari sepet tutarı ve menü üyeliği sunucuda
//    `adminContext: true` ile BİLEREK atlanıyor: personel müşteriyle telefonda
//    anlaşmış, istisnayı insan vermiştir. Bu bir onay akışı değil, KAYIT
//    akışıdır. Ekran atlanan iki şeyi YAZIYLA söyler ve HİÇBİRİNİ ENGELLEMEZ:
//      (1) o günün sipariş alımı kesimde kapandıysa,
//      (2) bir kalem gün ya da ürün tavanını aşıyorsa.
//    Engelleyen bir uyarı, sunucunun verdiği kararı ekranda iptal etmek olurdu.
//  · FİYAT HESAPLAMAZ. Gösterilen toplam TAHMİNDİR ve öyle yazar: gerçek
//    tutarı `OrderFactory` çözer (paket çözümlemesi, seçenek farkları,
//    kampanya). Tahmini "toplam" diye sunmak, personelin telefonda müşteriye
//    YANLIŞ tutar söylemesi demekti.
//  · DURUM DEĞİŞTİRMEZ, İPTAL ETMEZ, REVİZYON YAZMAZ. Onlar Sipariş Yönetimi
//    ekranının işi; bu ekranın tek yazması sipariş AÇMAKTIR.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · MÜŞTERİ YA KAYITLI YA YENİ. İkisi birden gönderilirse sunucu `customer`
//    nesnesini SESSİZCE yok sayar ve sipariş BAŞKA hesaba yazılır; ekran bu
//    yüzden seçim yapılınca yeni müşteri kutularını kapatır.
//  · AYNI TELEFONLA İKİNCİ SİPARİŞ İKİNCİ MÜŞTERİ YARATMAZ. Yer tutucu
//    e-posta ulusal numaradan türüyor; kayıt varsa döndürülür. Sonuç
//    `customer.created` ile yazılır.
//  · SEÇENEK KİMLİĞİ KORUNUR. Sunucudan gelen `option_value_ids` bu turda
//    DÜZENLENMEZ ama kalemde taşınır — düşerse "ekstra peynir" silinirdi.
//  · `<input type="date">` YASAK: masaüstü kabuğu WebKitGTK ile çalışıyor ve
//    yerleşik tarih seçici orada kapanmıyor. Kitin `dateField`i kullanılır.
//  · ÖDEME YÖNTEMİ YALNIZ `online` ve `cash`. `account` cari hesapla birlikte
//    kalktı; gönderilse `422` alırdı ve ekranda hiç çizilmez.
//  · PARA HER YERDE KURUŞ. Gösterimde `money()`; hiçbir yerde bölme yok.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_manual_order/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_manual_order/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.
//
// `innerHTML` HİÇ KULLANILMAZ: her düğüm `h()` ile kurulur ve metin
// `textContent` ile yazılır. Müşteri adı ve adres notu KULLANICI VERİSİDİR;
// dizeyle HTML kurmak, tek bir `<` karakterinin ekranı bozması demekti.

import {
  button, debounce, h, loadStyles, money, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dateField } from '../../ui-kit/datefield.js';
import { createPicker } from '../../ui-kit/picker.js';
import { formatPhone, normalizePhone } from '../../ui-kit/form.js';
import { alertBox, card, hintBox, statusLine } from '../../ui-kit/layout.js';

const BASE = '/api/bld_manual_order';

/** Müşteri araması ve stok denetimi gecikmesi (ms). */
const SEARCH_DELAY = 320;
const STOCK_DELAY = 500;

/** Adet sınırları — sunucu da denetliyor (`items.*.quantity`). */
const MIN_QTY = 1;
const MAX_QTY = 999;

// ------------------------------------------------------------------ durum

const EMPTY_STATE = {
  // BAĞLANTI: `ok:true` ile gelen `connected:false` (K7). Ayrı tutulur çünkü
  // "kayıt yok" ile "sunucuya ulaşılamıyor" aynı ekranda aynı görünmemeli.
  link: { connected: true, error: '' },
  contract: null,
  limits: null,

  // Müşteri: ya `selected` doludur ya `newCustomer`. İKİSİ BİRDEN OLMAZ.
  query: '',
  found: [],
  searchNote: '',
  selected: null,
  newCustomer: null,       // {name, phone}

  serviceDate: '',
  deliveryType: 'pickup',
  paymentMethod: 'cash',
  address: { line1: '', district: '', city: '', note: '' },
  customerNote: '',
  reason: '',

  products: [],
  productsNote: '',
  items: [],               // {menu_id, name, price_kurus, quantity, note, option_value_ids}

  day: null,               // /service-day yanıtı
  stockWarnings: [],
  saving: false,
  result: null,
};

let api = null;
let toast = null;
let state = { ...EMPTY_STATE };

const nodes = {};

// ------------------------------------------------------------------ araçlar

/**
 * Sunucu iki türlü hata döndürebilir: HTTP durumu (kabuk `api()` fırlatır) ve
 * gövdedeki `{ok:false, error}`. İkincisi burada tek yerde okunur.
 */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) {
    const error = new Error(String(result.error));
    error.code = result.code || '';
    throw error;
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Geçit ya da BLD sunucusu düştüğünde okuma uçları
 * `{ok:true, connected:false, error:"…"}` döndürür. Bu bir HATA DEĞİL, bir
 * DURUMDUR: veri gerçekten yok değil, ŞU AN OKUNAMIYOR. Sessizce boş liste
 * çizmek, personele "bu müşteri kayıtlı değilmiş" dedirtir ve aynı müşteri
 * ikinci kez açılırdı.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = { connected: false, error: String(payload.error || '') };
    return false;
  }
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

function paintStatus() {
  if (!nodes.status) return;
  if (!state.link.connected) {
    nodes.status.set(`BLD'ye ulaşılamıyor — ${state.link.error}`, true);
    return;
  }
  const parts = [];
  parts.push(state.selected
    ? `Müşteri: ${state.selected.name}`
    : state.newCustomer ? `Yeni müşteri: ${state.newCustomer.name}` : 'Müşteri seçilmedi');
  parts.push(`${state.items.length} kalem`);
  parts.push(`${totalQuantity()} porsiyon`);
  nodes.status.set(parts.join(' · '), false);
}

function totalQuantity() {
  return state.items.reduce((sum, item) => sum + Number(item.quantity || 0), 0);
}

/**
 * TAHMİNİ toplam. Katalog fiyatı × adet (+ teslimat ücreti).
 *
 * GERÇEK TUTAR SUNUCUDA HESAPLANIR ve buradaki sayı ondan farklı çıkabilir:
 * günün menüsü bir paket satırı + sıfır fiyatlı bileşenler olarak yazılıyor,
 * seçenekler fiyat ekliyor ve kampanya kuralları burada bilinmiyor. Ekran bu
 * yüzden sayının yanına "tahmini" yazar — yazmasaydı personel telefonda
 * müşteriye kesin bir tutar söyler ve fatura başka çıkardı.
 */
function estimatedTotal() {
  const items = state.items.reduce(
    (sum, item) => sum + Number(item.price_kurus || 0) * Number(item.quantity || 0), 0);
  const fee = state.deliveryType === 'delivery'
    ? Number(state.day?.delivery_fee_kurus || 0)
    : 0;
  return items + fee;
}

// ------------------------------------------------------------------ müşteri

function customerCard() {
  const box = h('div', 'bmo-stack');

  const search = h('input', 'kit-input bmo-search');
  search.type = 'search';
  search.placeholder = 'Telefon ya da ad — en az 3 karakter';
  search.setAttribute('aria-label', 'Müşteri ara');
  nodes.search = search;

  // GECİKMELİ ARAMA: her müşteri okuması BLD'de bir KVKK denetim satırı açıyor
  // (`customers.md`). Her tuşta istek atmak, izi okunamaz bir gürültüye
  // çevirirdi. `cancel()` panel kapanırken çağrılır.
  nodes.searchDebounced = debounce(() => runSearch(), SEARCH_DELAY);
  search.addEventListener('input', () => {
    state.query = search.value;
    nodes.searchDebounced();
  });
  search.addEventListener('keydown', (event) => {
    // Enter beklemeyi kısaltır: personel numarayı yazıp Enter'a basar.
    if (event.key !== 'Enter') return;
    event.preventDefault();
    // KUTUDAN OKUNUR, `state.query`den DEĞİL. Yapıştırma, otomatik doldurma ve
    // sanal klavye `input` olayını atlayabiliyor; o hâlde `state.query` bir tur
    // geride kalır ve Enter, kullanıcının EKRANDA GÖRDÜĞÜ metinden başka bir
    // şeyi aratırdı (ya da hiçbir şeyi).
    state.query = search.value;
    nodes.searchDebounced.cancel();
    runSearch();
  });

  nodes.searchNote = h('div', 'bmo-dim');
  nodes.found = h('div', 'bmo-found');
  nodes.picked = h('div', 'bmo-picked');
  nodes.newBox = h('div', 'bmo-newbox');

  box.append(search, nodes.searchNote, nodes.found, nodes.picked, nodes.newBox);
  paintCustomer();

  return card('1 · Müşteri', box,
    'Telefonla arayın. Kayıt yoksa AYNI EKRANDA ad ve telefonla açılır — '
    + 'ayrı bir müşteri ekranına gitmek telefondaki akışı keser.');
}

async function runSearch() {
  const needle = String(state.query || '').trim();
  if (!nodes.found) return;

  if (!needle) {
    state.found = [];
    state.searchNote = '';
    paintCustomer();
    return;
  }

  try {
    // Numara yazıldıysa ULUSAL BİÇİMDE aranır: kayıt "5321234567" diye
    // duruyor ve "0532..." diye aranınca hiç bulunmazdı.
    const digits = normalizePhone(needle);
    const query = digits.length >= 7 ? digits : needle;
    const payload = await call(`${BASE}/customers?q=${encodeURIComponent(query)}`);
    if (!linkOk(payload)) {
      state.found = [];
      state.searchNote = 'Müşteri araması şu an yapılamıyor; yeni müşteri olarak açabilirsiniz.';
    } else if (payload.too_short) {
      state.found = [];
      state.searchNote = `Aramak için en az ${payload.min_chars} karakter yazın.`;
    } else {
      state.found = payload.items || [];
      state.searchNote = state.found.length
        ? `${state.found.length} kayıt bulundu.`
        : 'Kayıt bulunamadı — aşağıdan yeni müşteri açabilirsiniz.';
    }
  } catch (failure) {
    state.found = [];
    state.searchNote = String(failure.message || failure);
  }
  paintCustomer();
  paintStatus();
}

function paintCustomer() {
  if (!nodes.found) return;

  nodes.searchNote.textContent = state.searchNote;

  // --- bulunanlar ---------------------------------------------------------
  nodes.found.replaceChildren();
  if (!state.selected && state.found.length) {
    for (const row of state.found) {
      const line = h('button', 'bmo-hit');
      line.type = 'button';
      line.append(h('span', 'bmo-hit-name', row.name || '(adsız kayıt)'));
      // TELEFON MASKELENMEZ: personel müşteriyi numarasından tanır ve maskeli
      // bir listede doğru kaydı seçemez (`customers.md`).
      line.append(h('span', 'bmo-hit-meta', formatPhone(row.phone) || '—'));
      if (row.org_name) line.append(h('span', 'bmo-hit-meta', row.org_name));
      line.addEventListener('click', () => {
        state.selected = row;
        // İKİSİ BİRDEN OLMAZ: seçim yapıldığı an yeni müşteri taslağı düşer.
        state.newCustomer = null;
        state.found = [];
        state.searchNote = '';
        paintCustomer();
        paintSummary();
        paintStatus();
      });
      nodes.found.append(line);
    }
  }

  // --- seçili müşteri -----------------------------------------------------
  nodes.picked.replaceChildren();
  if (state.selected) {
    const chip = h('div', 'bmo-chip');
    chip.append(h('span', 'bmo-chip-name', state.selected.name || '(adsız kayıt)'));
    chip.append(h('span', 'bmo-chip-meta', formatPhone(state.selected.phone) || '—'));
    chip.append(button('Değiştir', {
      variant: 'ghost',
      onClick: () => {
        state.selected = null;
        paintCustomer();
        paintSummary();
        paintStatus();
        nodes.search?.focus();
      },
    }));
    nodes.picked.append(chip);
  }

  // --- yeni müşteri -------------------------------------------------------
  nodes.newBox.replaceChildren();
  if (state.selected) return;

  if (!state.newCustomer) {
    nodes.newBox.append(button('Yeni müşteri aç', {
      onClick: () => {
        // Kutular ARAMADAKİ yazıyla dolar: personel numarayı zaten yazdı,
        // ikinci kez yazdırmak bir tık daha bekletmekti.
        const digits = normalizePhone(state.query);
        state.newCustomer = {
          name: digits ? '' : String(state.query || '').trim(),
          phone: digits || '',
        };
        paintCustomer();
        paintSummary();
      },
    }));
    return;
  }

  const grid = h('div', 'bmo-grid2');
  grid.append(
    labelled('Ad / unvan', textInput(state.newCustomer.name, (value) => {
      state.newCustomer.name = value;
      paintSummary();
      paintStatus();
    }, { placeholder: 'Acme Gıda', maxLength: 120 })),
    labelled('Telefon', textInput(state.newCustomer.phone, (value) => {
      state.newCustomer.phone = value;
      paintSummary();
    }, { placeholder: '0532 123 45 67', maxLength: 24 })),
  );

  const note = hintBox(
    'Kayıt kurumsal etiketle ve YER TUTUCU e-postayla açılır '
    + '(tel-<numara>@bld.invalid). Aynı telefonla ikinci sipariş İKİNCİ MÜŞTERİ '
    + 'YARATMAZ — var olan kayıt bulunup kullanılır.');

  const cancel = button('Vazgeç', {
    variant: 'ghost',
    onClick: () => {
      state.newCustomer = null;
      paintCustomer();
      paintSummary();
      paintStatus();
    },
  });

  nodes.newBox.append(grid, note, cancel);
}

// ---------------------------------------------------------- gün ve teslimat

function dayCard() {
  const box = h('div', 'bmo-stack');

  // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
  // kitin kendi takvimi kullanılır ve `destroy()` cleanup'ta çağrılır.
  nodes.date = dateField({
    value: state.serviceDate,
    label: 'Servis günü',
    onChange: (iso) => {
      state.serviceDate = iso;
      loadDay();
    },
  });

  nodes.deliveryChips = choiceStrip(
    [{ key: 'pickup', label: 'Gel-al' }, { key: 'delivery', label: 'Adrese teslim' }],
    state.deliveryType,
    (key) => {
      state.deliveryType = key;
      paintAddress();
      paintSummary();
    });

  nodes.paymentChips = choiceStrip(
    [{ key: 'cash', label: 'Nakit' }, { key: 'online', label: 'Online' }],
    state.paymentMethod,
    (key) => { state.paymentMethod = key; paintSummary(); });

  nodes.addressBox = h('div', 'bmo-addr');
  nodes.dayNote = h('div', 'bmo-stack');

  const noteField = h('textarea', 'kit-textarea');
  noteField.placeholder = 'Müşteri notu (mutfağa ve fişe düşer) — isteğe bağlı';
  noteField.maxLength = 500;
  noteField.setAttribute('aria-label', 'Müşteri notu');
  noteField.addEventListener('input', () => { state.customerNote = noteField.value; });
  nodes.customerNote = noteField;

  box.append(
    labelled('Servis günü', nodes.date.node),
    labelled('Teslimat', nodes.deliveryChips.node),
    labelled('Ödeme', nodes.paymentChips.node),
    nodes.addressBox,
    nodes.dayNote,
    noteField,
  );
  paintAddress();

  return card('2 · Gün, teslimat ve ödeme', box,
    'Servis günü ZORUNLUDUR ve türetilmez — sessizce bugüne düşmesi, yarına '
    + 'alınan siparişin bugün pişmesi demekti. Saat alanı YOKTUR: sunucu '
    + 'bugünse "şimdi", ileriyse 12:00 yazar.');
}

function paintAddress() {
  if (!nodes.addressBox) return;
  nodes.addressBox.replaceChildren();
  // GEL-AL SİPARİŞTE ADRES HİÇ SORULMAZ ve gönderilmez: doldurulmuş bir adres,
  // listede teslimat sanılan bir kayıt üretirdi.
  if (state.deliveryType !== 'delivery') return;

  const grid = h('div', 'bmo-grid2');
  grid.append(
    labelled('Adres', textInput(state.address.line1, (value) => {
      state.address.line1 = value;
      paintSummary();
    }, { placeholder: 'Örnek Mah. 12. Sk No:3', maxLength: 255, wide: true })),
    labelled('İlçe', textInput(state.address.district, (value) => {
      state.address.district = value;
      paintSummary();
    }, { placeholder: 'Selçuklu', maxLength: 96 })),
    labelled('İl', textInput(state.address.city, (value) => {
      state.address.city = value;
      paintSummary();
    }, { placeholder: 'Konya', maxLength: 96 })),
    labelled('Adres notu', textInput(state.address.note, (value) => {
      state.address.note = value;
    }, { placeholder: 'Zili çalmayın', maxLength: 255, wide: true })),
  );
  nodes.addressBox.append(grid);
}

async function loadDay() {
  if (!state.serviceDate) return;
  try {
    const payload = await call(`${BASE}/service-day?date=${encodeURIComponent(state.serviceDate)}`);
    linkOk(payload);
    state.day = payload;

    // SUNUCUNUN ÖDEME LİSTESİ KAZANIR. Ekranın gömülü listesi yalnız açılış
    // içindir; vitrinde kapatılmış bir yöntemi çizmeye devam etmek, sunucunun
    // `422` ile reddedeceği bir siparişi personele yazdırmak olurdu.
    const allowed = Array.isArray(payload.payment_methods) ? payload.payment_methods : [];
    if (allowed.length) {
      nodes.paymentChips.limit(allowed);
      if (!allowed.includes(state.paymentMethod)) {
        state.paymentMethod = allowed[0];
        nodes.paymentChips.set(state.paymentMethod);
      }
    }
  } catch (failure) {
    state.day = null;
    toast(String(failure.message || failure), 'warn');
  }
  paintDayNote();
  checkStock();
  paintSummary();
}

function paintDayNote() {
  if (!nodes.dayNote) return;
  nodes.dayNote.replaceChildren();
  if (!state.day) return;

  // (1) KESİM UYARISI — GÖSTERİLİR, ENGELLEMEZ. Sunucu pencereyi
  // `adminContext: true` ile bilerek atlıyor; ekranın işi personelin bunu
  // BİLEREK yapmasını sağlamak.
  const cutoff = state.day.cutoff || {};
  if (cutoff.closed && cutoff.label) {
    nodes.dayNote.append(alertBox(cutoff.label, 'warn'));
  } else if (cutoff.cutoff) {
    nodes.dayNote.append(h('div', 'bmo-dim',
      `Bu günün kesim saati ${cutoff.cutoff}`
      + `${cutoff.source === 'day' ? ' (güne özel).' : '.'}`
      + (state.serviceDate > cutoff.today
        ? ' İleri tarihli sipariş, kesim anında mutfağa düşer.'
        : ' Sipariş anında mutfağa düşer.')));
  }

  if (state.day.ordering_enabled === false) {
    nodes.dayNote.append(alertBox(
      'Vitrinde sipariş alımı DURDURULMUŞ durumda'
      + (state.day.pause_reason ? ` (${state.day.pause_reason})` : '')
      + '. Panelden giriş bundan etkilenmez.', 'info'));
  }

  if (state.day.menu_missing) {
    nodes.dayNote.append(h('div', 'bmo-dim',
      'Bu güne tanımlı bir günlük menü yok. Sipariş yine açılabilir — menü '
      + 'üyeliği panel girişinde aranmıyor.'));
  }
}

// ------------------------------------------------------------------ kalemler

function itemsCard() {
  const box = h('div', 'bmo-stack');

  // ÇOKLU KİP: seçici AÇIK KALIR, ürünlere tık tık tık basılır, seçim birikir
  // ve tek "Ekle (N)" hepsini sepete atar. Her üründen sonra kapanan bir
  // seçici, on kalemlik bir siparişte on kez açtırırdı.
  //
  // ARAMA DARALTINCA ÖNCEKİ SEÇİMLER DÜŞMEZ: seçim `createPicker` içinde
  // görünür listeden AYRI bir kümede duruyor ve süzgeç yalnız çizimi
  // etkiliyor. Bu yüzden arama değiştikçe `setItems()` ÇAĞRILMAZ — o çağrı
  // listede olmayan seçimleri temizler ve personelin biriktirdiği sepeti
  // sessizce boşaltırdı.
  nodes.picker = createPicker({
    items: [],
    groupLabel: 'Kategori',
    placeholder: 'Ürün ara — seçimler birikir',
    single: false,
    onChange: () => paintAddButton(),
  });

  nodes.addButton = button('Ekle', { variant: 'primary', onClick: () => addSelected() });
  nodes.productsNote = h('div', 'bmo-dim');

  const bar = h('div', 'bmo-actions');
  bar.append(nodes.addButton, nodes.productsNote);

  nodes.itemRows = h('div', 'bmo-items');

  box.append(nodes.picker.node, bar, nodes.itemRows);
  paintItems();
  paintAddButton();

  return card('3 · Kalemler', box,
    'Seçici açık kalır: ürünlere sırayla basın, seçim birikir, tek düğmeyle '
    + 'hepsi eklenir. Adet satırın üstünde değiştirilir.');
}

function paintAddButton() {
  if (!nodes.addButton) return;
  const count = nodes.picker ? nodes.picker.size() : 0;
  nodes.addButton.textContent = count ? `Ekle (${count})` : 'Ekle';
  nodes.addButton.disabled = count === 0;
}

function addSelected() {
  if (!nodes.picker) return;
  const chosen = nodes.picker.selectedItems();
  if (!chosen.length) return;

  for (const entry of chosen) {
    const menuId = Number(entry.id);
    const product = state.products.find((row) => Number(row.menu_id) === menuId);
    // AYNI ÜRÜN İKİNCİ KEZ EKLENİRSE ADEDİ ARTAR, ikinci satır açılmaz:
    // personel yanlışlıkla iki kez bastığında sepette iki ayrı "Tavuk Sote"
    // görmek, hangisinin doğru olduğunu sormak demekti. Notu farklı bir satır
    // isteyen personel, satırı ekledikten sonra "Ayır" ile ikizini açar.
    const existing = state.items.find((item) => item.menu_id === menuId && !item.note);
    if (existing) {
      existing.quantity = Math.min(MAX_QTY, existing.quantity + 1);
      continue;
    }
    state.items.push({
      menu_id: menuId,
      name: product ? product.name : entry.name,
      price_kurus: product ? Number(product.price_kurus || 0) : 0,
      quantity: 1,
      note: '',
    });
  }

  nodes.picker.clear();
  paintAddButton();
  paintItems();
  paintSummary();
  paintStatus();
  checkStock();
}

function paintItems() {
  if (!nodes.itemRows) return;
  nodes.itemRows.replaceChildren();

  if (!state.items.length) {
    nodes.itemRows.append(h('div', 'bmo-dim',
      'Sepet boş. Yukarıdaki seçiciden ürün seçip "Ekle"ye basın.'));
    return;
  }

  for (const [index, item] of state.items.entries()) {
    const row = h('div', 'bmo-item');

    const name = h('div', 'bmo-item-name');
    name.append(h('span', undefined, item.name || `#${item.menu_id}`));
    name.append(h('span', 'bmo-dim', money(item.price_kurus)));
    row.append(name);

    // ADET SATIRIN ÜSTÜNDE. Ayrı bir düzenleme penceresi açmak, telefonda
    // "iki değil üç olsun" diyen müşteri için iki tık daha demekti.
    const qty = h('input', 'kit-input bmo-qty');
    qty.type = 'number';
    qty.min = String(MIN_QTY);
    qty.max = String(MAX_QTY);
    qty.value = String(item.quantity);
    qty.setAttribute('aria-label', `${item.name} adedi`);
    qty.addEventListener('input', () => {
      const value = Math.max(MIN_QTY, Math.min(MAX_QTY, Number(qty.value) || MIN_QTY));
      item.quantity = value;
      // SATIRLAR YENİDEN ÇİZİLMEZ: her tuşta yeniden çizmek imleci kutudan
      // atardı. Yalnız toplam, uyarılar ve durum satırı tazelenir.
      paintSummary();
      paintStatus();
      queueStockCheck();
    });
    qty.addEventListener('blur', () => { qty.value = String(item.quantity); });
    row.append(qty);

    const note = h('input', 'kit-input bmo-item-note');
    note.type = 'text';
    note.maxLength = 255;
    note.placeholder = 'Kalem notu (acısız, ayrı paket…)';
    note.value = item.note || '';
    note.setAttribute('aria-label', `${item.name} notu`);
    note.addEventListener('input', () => { item.note = note.value; });
    row.append(note);

    row.append(button('Çıkar', {
      variant: 'ghost',
      onClick: () => {
        state.items.splice(index, 1);
        paintItems();
        paintSummary();
        paintStatus();
        checkStock();
      },
    }));

    nodes.itemRows.append(row);
  }
}

async function loadProducts() {
  try {
    const payload = await call(`${BASE}/products`);
    if (!linkOk(payload)) {
      state.products = [];
      state.productsNote = 'Ürün kataloğu okunamadı; bağlantı gelince yeniden deneyin.';
    } else {
      state.products = payload.items || [];
      state.productsNote = payload.truncated
        // `truncated` SÖYLENMELİ: eksik bir seçici, aranan ürünün hiç olmadığını
        // düşündürür ve personel telefonda "böyle bir şeyimiz yok" der.
        ? `${state.products.length} ürün — liste KESİLDİ, sunucuda daha çok kayıt var.`
        : `${state.products.length} ürün.`;
    }
  } catch (failure) {
    state.products = [];
    state.productsNote = String(failure.message || failure);
  }

  // `setItems` YALNIZ BURADA çağrılır (katalog bir kez gelir): arama sırasında
  // çağrılsaydı biriken seçimler temizlenirdi.
  nodes.picker?.setItems(state.products.map((row) => ({
    id: row.menu_id,
    name: row.name,
    group: row.category || '',
    meta: money(row.price_kurus),
  })));
  if (nodes.productsNote) nodes.productsNote.textContent = state.productsNote;
  paintAddButton();
}

// ---------------------------------------------------------------- stok uyarı

function queueStockCheck() {
  nodes.stockDebounced?.();
}

async function checkStock() {
  if (!state.serviceDate || !state.items.length) {
    state.stockWarnings = [];
    paintSummary();
    return;
  }
  try {
    const payload = await call(`${BASE}/stock-check`, {
      method: 'POST',
      body: {
        service_date: state.serviceDate,
        items: state.items.map((item) => ({ menu_id: item.menu_id, quantity: item.quantity })),
      },
    });
    if (!linkOk(payload)) {
      // Stok okunamıyorsa UYARI YOKTUR ama "aşım yok" da denmez: boş liste
      // sessizce güven verirdi. Durum satırı bağlantıyı zaten söylüyor.
      state.stockWarnings = [];
    } else {
      state.stockWarnings = payload.warnings || [];
    }
  } catch {
    state.stockWarnings = [];
  }
  paintSummary();
}

// -------------------------------------------------------------------- özet

function summaryBar() {
  const box = h('div', 'bmo-summary');

  nodes.warnBox = h('div', 'bmo-stack');
  nodes.totalLine = h('div', 'bmo-total');
  nodes.blockLine = h('div', 'bmo-dim');

  const reason = h('input', 'kit-input bmo-reason');
  reason.type = 'text';
  reason.maxLength = 500;
  reason.placeholder = 'Gerekçe (isteğe bağlı) — denetim izine yazılır';
  reason.setAttribute('aria-label', 'Gerekçe');
  reason.addEventListener('input', () => { state.reason = reason.value; });
  nodes.reason = reason;

  nodes.dryButton = button('Önce prova et', { onClick: () => save(true) });
  nodes.saveButton = button('Siparişi kaydet', { variant: 'primary', onClick: () => save(false) });

  const actions = h('div', 'bmo-actions');
  actions.append(reason, nodes.dryButton, nodes.saveButton);

  nodes.resultBox = h('div', 'bmo-stack');

  box.append(nodes.warnBox, nodes.totalLine, nodes.blockLine, actions, nodes.resultBox);
  paintSummary();
  return box;
}

/** Kaydetmeye engel olan (GERÇEK engel) durumlar. Kesim ve stok BURADA YOK. */
function blockers() {
  const list = [];
  if (!state.selected && !state.newCustomer) list.push('müşteri seçilmedi');
  if (state.newCustomer && String(state.newCustomer.name || '').trim().length < 2) {
    list.push('yeni müşteri adı eksik');
  }
  if (state.newCustomer && !normalizePhone(state.newCustomer.phone)) {
    list.push('yeni müşteri telefonu eksik');
  }
  if (!state.serviceDate) list.push('servis günü seçilmedi');
  if (!state.items.length) list.push('kalem eklenmedi');
  if (state.deliveryType === 'delivery') {
    const { line1, district, city } = state.address;
    if (!String(line1).trim() || !String(district).trim() || !String(city).trim()) {
      list.push('teslimat adresi eksik');
    }
  }
  return list;
}

function paintSummary() {
  if (!nodes.totalLine) return;

  // İKİ UYARI DA BURADA TOPLANIR VE HİÇBİRİ ENGELLEMEZ (sunucu bunlara
  // bilerek izin veriyor): (1) kesim geçmiş gün, (2) tavan aşımı.
  nodes.warnBox.replaceChildren();
  const cutoff = state.day?.cutoff || {};
  if (cutoff.closed && cutoff.label) nodes.warnBox.append(alertBox(cutoff.label, 'warn'));
  for (const line of state.stockWarnings) nodes.warnBox.append(alertBox(line, 'warn'));

  nodes.totalLine.replaceChildren();
  nodes.totalLine.append(h('span', 'bmo-total-label', 'Tahmini toplam'));
  nodes.totalLine.append(h('span', 'bmo-total-value', money(estimatedTotal())));
  nodes.totalLine.append(h('span', 'bmo-dim',
    `${totalQuantity()} porsiyon · katalog fiyatından hesaplandı, `
    + 'kesin tutarı sunucu belirler'
    + (state.deliveryType === 'delivery' && state.day?.delivery_fee_kurus
      ? ` · teslimat ${money(state.day.delivery_fee_kurus)} dâhil`
      : '')));

  const missing = blockers();
  nodes.blockLine.textContent = missing.length
    ? `Kaydetmek için eksik: ${missing.join(', ')}.`
    : '';

  const busy = state.saving;
  nodes.saveButton.disabled = busy || missing.length > 0;
  nodes.dryButton.disabled = busy || missing.length > 0;
  nodes.saveButton.textContent = busy ? 'Kaydediliyor…' : 'Siparişi kaydet';
}

// ------------------------------------------------------------------ kaydet

function requestBody(dryRun) {
  const body = {
    service_date: state.serviceDate,
    delivery_type: state.deliveryType,
    payment_method: state.paymentMethod,
    // KALEMLER OLDUĞU GİBİ: `option_value_ids` taşınıyorsa düşürülmez.
    items: state.items.map((item) => {
      const line = { menu_id: item.menu_id, quantity: item.quantity };
      if (item.note) line.note = item.note;
      if (item.option_value_ids?.length) line.option_value_ids = item.option_value_ids;
      return line;
    }),
    // TEK AD: `dryRun`. `dry_run` yazılsaydı sunucu alanı reddeder (422) —
    // sessizce düşen bir ad, prova sanılan bir isteği GERÇEK SİPARİŞ yapardı.
    dryRun,
  };
  if (state.selected) body.customer_id = Number(state.selected.id);
  else body.customer = {
    name: String(state.newCustomer.name).trim(),
    phone: String(state.newCustomer.phone).trim(),
  };
  if (state.deliveryType === 'delivery') {
    body.address = {
      line1: String(state.address.line1).trim(),
      district: String(state.address.district).trim(),
      city: String(state.address.city).trim(),
      note: String(state.address.note || '').trim(),
    };
  }
  if (state.customerNote.trim()) body.customer_note = state.customerNote.trim();
  if (state.reason.trim()) body.reason = state.reason.trim();
  return body;
}

async function save(dryRun) {
  if (state.saving) return;
  if (blockers().length) return;

  state.saving = true;
  paintSummary();

  try {
    const result = await call(`${BASE}/orders`, { method: 'POST', body: requestBody(dryRun) });
    state.result = result;
    paintResult(result);
    if (!result.dry_run) {
      toast(`Sipariş açıldı: ${result.order?.order_number || '—'}`, 'good');
      resetDraft();
    } else {
      toast('Prova tamam — sipariş AÇILMADI.', 'warn');
    }
  } catch (failure) {
    state.result = null;
    paintResult({ error: String(failure.message || failure) });
    toast(String(failure.message || failure), 'bad');
  } finally {
    state.saving = false;
    paintSummary();
  }
}

function paintResult(result) {
  if (!nodes.resultBox) return;
  nodes.resultBox.replaceChildren();

  if (result?.error) {
    nodes.resultBox.append(alertBox(result.error, 'bad'));
    return;
  }

  if (result?.dry_run) {
    const would = result.would || {};
    nodes.resultBox.append(alertBox(
      `Prova: ${would.item_count ?? state.items.length} kalem, `
      + `${would.service_date || state.serviceDate}, `
      + `${would.payment_method || state.paymentMethod}`
      + `${would.would_create_customer ? ' · YENİ müşteri açılacak' : ''}. `
      + 'Sipariş AÇILMADI.', 'info'));
    // PROVANIN SINIRI YAZILIR: gövdeyi ve ödeme yöntemini denetler; kalem
    // geçerliliğini, fiyatı ve stoğu DENETLEMEZ. "Prova geçti" cümlesi
    // "sipariş geçecek" anlamına gelmiyor.
    if (result.note) nodes.resultBox.append(h('div', 'bmo-dim', result.note));
    return;
  }

  const order = result?.order || {};
  const line = h('div', 'bmo-ok');
  // SİPARİŞ NUMARASI BAŞARI MESAJININ KENDİSİDİR: personel telefondaki
  // müşteriye onu okur.
  line.append(h('span', 'bmo-ok-no', order.order_number || `#${order.id || '—'}`));
  line.append(h('span', undefined,
    `${order.service_date || ''} · ${order.item_count || 0} kalem · ${money(order.total_kurus)}`));
  if (result?.customer?.created) {
    line.append(h('span', 'bmo-dim', 'Yeni müşteri kaydı açıldı.'));
  }
  nodes.resultBox.append(line);

  for (const warning of result?.warnings || []) {
    // Sunucu uyarısı bugün tek bir hâlde doluyor: sipariş yazıldı ama
    // "onaylandı" geçişi patladı. Sipariş VARDIR; "olmadı" demek personelin
    // onu ikinci kez girmesine ve müşteriye iki kere yemek çıkmasına yol açardı.
    nodes.resultBox.append(alertBox(warning, 'warn'));
  }
}

function resetDraft() {
  // SİPARİŞ AÇILDIKTAN SONRA SEPET BOŞALIR ama MÜŞTERİ VE GÜN KALIR: aynı
  // müşteri telefonda "bir de yarına verelim" diyor ve her seferinde baştan
  // aramak, akışın en sık tekrarlanan adımını en pahalı hâle getirirdi.
  state.items = [];
  state.customerNote = '';
  state.reason = '';
  state.stockWarnings = [];
  if (nodes.customerNote) nodes.customerNote.value = '';
  if (nodes.reason) nodes.reason.value = '';
  nodes.picker?.clear();
  paintAddButton();
  paintItems();
  paintSummary();
  paintStatus();
}

// ------------------------------------------------------------- küçük parçalar

/** Sözleşmeden gelen sınırları ekrana uygular. */
function applyLimits() {
  const min = Number(state.limits?.min_search_chars || 0);
  if (min && nodes.search) {
    nodes.search.placeholder = `Telefon ya da ad — en az ${min} karakter`;
  }
  const limits = state.contract?.limits;
  if (!limits) return;
  // Uzunluk sınırları sunucu sütunlarıdır; tarayıcı kutusu onları AŞMASIN diye
  // yazılır. Kırpma DEĞİL, engelleme: sessizce kırpılan bir adres notu,
  // kuryenin okuyamadığı yarım cümle demekti.
  if (nodes.customerNote) nodes.customerNote.maxLength = Number(limits.customer_note);
  if (nodes.reason) nodes.reason.maxLength = Number(limits.reason);
}

function labelled(label, control) {
  const row = h('label', 'bmo-field');
  row.append(h('span', 'bmo-field-label', label));
  row.append(control);
  return row;
}

function textInput(value, onInput, { placeholder = '', maxLength = 255, wide = false } = {}) {
  const node = h('input', `kit-input${wide ? ' bmo-wide' : ''}`);
  node.type = 'text';
  node.value = value || '';
  node.placeholder = placeholder;
  node.maxLength = maxLength;
  node.addEventListener('input', () => onInput(node.value));
  return node;
}

/**
 * Tek seçimli çip şeridi — ZORUNLU alan için.
 *
 * Kitin `chipRow`u aynı çipe ikinci kez basınca seçimi KALDIRIYOR (`null`) ve
 * bu bir süzgeç için doğru, ZORUNLU bir alan için yanlış: teslimat türü boş
 * kalamaz. Kit ortak maldır ve bu turda ona dokunulmuyor; sarmalayıcı seçimi
 * geri koyar. Bağımlılık burada yazılı dursun ki kit bir gün `required`
 * seçeneği verdiğinde buranın sadeleşeceği bilinsin.
 */
function choiceStrip(items, active, onChange) {
  const node = h('div', 'kit-chips');
  const buttons = new Map();
  let current = active;
  let allowed = items.map((item) => item.key);

  const paint = () => {
    for (const [key, chip] of buttons) {
      chip.classList.toggle('on', key === current);
      chip.hidden = !allowed.includes(key);
      chip.setAttribute('aria-pressed', key === current ? 'true' : 'false');
    }
  };

  for (const item of items) {
    const chip = h('button', 'kit-chip', item.label);
    chip.type = 'button';
    chip.addEventListener('click', () => {
      if (current === item.key) return;   // seçimi KALDIRMAZ
      current = item.key;
      paint();
      onChange(current);
    });
    buttons.set(item.key, chip);
    node.append(chip);
  }
  paint();

  return {
    node,
    set(key) { current = key; paint(); },
    /** Sunucunun izin verdiği değerlerle daraltır. */
    limit(keys) { allowed = keys.slice(); paint(); },
  };
}

// -------------------------------------------------------------------- mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = {
    ...EMPTY_STATE,
    address: { line1: '', district: '', city: '', note: '' },
    items: [],
    // VARSAYILAN BUGÜN: telefon siparişlerinin ezici çoğunluğu bugüne veriliyor.
    serviceDate: todayIso(),
  };

  const view = h('div', 'kit-panel bmo');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.status = statusLine();
  nodes.stockDebounced = debounce(() => checkStock(), STOCK_DELAY);

  const body = h('div', 'bmo-body');
  body.append(
    hintBox(
      'Telefonla alınan sipariş buradan girilir. Sipariş "onaylandı" doğar; '
      + 'servis günü bugünse ANINDA mutfak ekranına düşer, ileri tarihliyse o '
      + 'günün kesim anında. Kesim saati ve stok tavanı burada UYARI'
      + 'dır, engel değil — sunucu panel girişinde ikisini de bilerek atlar.'),
    customerCard(),
    dayCard(),
    itemsCard(),
    summaryBar(),
  );

  // KATMANLAR KÖKE EKLENİR: `toaster` ve kitin overlay'leri `view`e göre
  // konumlanıyor; gövdeye eklenen bir katman kaydırılan alanla birlikte kayar.
  view.append(nodes.status.node, body);
  root.replaceChildren(view);

  paintStatus();

  // Açılış sırası: sözleşme (ağa çıkmaz) → katalog → gün. Gün en sonda çünkü
  // ödeme çipleri sunucunun listesine göre daralıyor ve çipler o zamana kadar
  // çizilmiş olmalı.
  (async () => {
    try {
      const payload = await call(`${BASE}/overview`);
      state.contract = payload.contract || null;
      state.limits = payload.limits || null;
      // Alan sınırları ve arama eşiği SUNUCUDAN gelir: ekrana gömülü bir sayı,
      // modül ayarı değiştiğinde sessizce yalan söylerdi.
      applyLimits();
      if (payload.dry_run_default) {
        // AYAR AÇIKSA SÖYLENİR: "kaydet" düğmesinin sessizce provaya dönmesi,
        // ekranın yalan söylemesi olurdu.
        toast('Uyarı: modül ayarında kuru prova AÇIK — kaydetme yazmaz.', 'warn');
      }
    } catch (failure) {
      toast(String(failure.message || failure), 'bad');
    }
    await loadProducts();
    await loadDay();
    nodes.search?.focus();
  })();

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR (kit kuralı 4): `dateField` `document`
  // üzerinde `mousedown`/`keydown` dinliyor ve iki `debounce` bekleyen
  // zamanlayıcı tutuyor. Bırakılmazsa panel her açılışta bir tane daha birikir
  // ve KAPALI bir ekran müşteri araması yapmaya devam eder — her biri BLD'de
  // bir KVKK denetim satırı açar.
  return () => {
    nodes.date?.destroy();
    nodes.searchDebounced?.cancel();
    nodes.stockDebounced?.cancel();
    nodes.picker = null;
    nodes.date = null;
    nodes.searchDebounced = null;
    nodes.stockDebounced = null;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
  };
}
