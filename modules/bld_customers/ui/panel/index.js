// Müşteriler paneli — BLD müşteri kayıtlarının Kontrol Merkezi'nden yönetimi.
//
// NE YAPAR: ad/telefon/e-posta/kurum araması ve sayfalı liste; müşteri
// çekmecesinde bilgiler + adres defteri + sipariş geçmişi + abonelikler + SMS
// gönderim kaydı; iletişim bilgilerinin ve serbest metin kurum etiketlerinin
// düzenlenmesi; hesabın kapatılması ve yeniden açılması; bu ekranın kendi
// KVKK erişim izinin ve yazma denemesi izinin okunması.
//
// ================== BU EKRAN KİŞİSEL VERİ EKRANIDIR ==================
//
// Sözleşme (`docs/control/customers.md` + `00-genel.md` §9) bu alanı ötekilerden
// ayırıyor ve panelin de ayırması gerekiyor. Üç somut sonuç:
//
//  1. YOKLAMA YOK. Bu dosyada `pollLoop` ve `setInterval` HİÇ GEÇMEZ ve
//     geçmeyecek. Her okuma hem BLD'de hem yerelde bir denetim satırı yazar;
//     15 saniyede bir yoklayan bir ekran, izi günde binlerce anlamsız satırla
//     doldurup içindeki gerçek erişimi görünmez kılardı. Tazeleme yalnız
//     yöneticinin bastığı düğmeyle olur.
//  2. AÇILIŞTA SAYAÇ YOK. `GET /overview` BLD'ye hiç gitmez; süzgeç sözleşmesi
//     ve uyarı metinleri döner. Sayılar ilk aramanın `meta.total` alanından
//     gelir — yani yöneticinin bilinçli bir eyleminden.
//  3. SEKMELER TEMBELDİR. Çekmecedeki her sekme İLK AÇILDIĞINDA yüklenir.
//     Hepsini birden çekmek, yöneticinin bakmadığı dört ekran için dört
//     denetim satırı yazmak olurdu. Adres defteri bu yüzden "Bilgiler"in
//     içine gömülmedi, kendi sekmesinde durur.
//
// KULLANICI BUNU BİLİR. Ekranın tepesinde kalıcı bir `hintBox` var ve
// metni SUNUCUDAN gelir (`overview.kvkk_notice`) — aynı cümlenin iki kopyası
// zamanla ayrışır ve biri güncellenmez.
//
// NE YAPMAZ:
//  · MASKELEMEZ. Liste ve kart telefonu, e-postayı olduğu gibi gösterir.
//    Sözleşme bunu açıkça reddediyor: yönetici müşteriyi telefonundan tanır ve
//    maskeli bir listede doğru kaydı seçemez, hepsini tek tek açmak zorunda
//    kalır — yani her arama için bir düzine denetim satırı doğar. Maskeleme
//    ekranda gizliliği artırmaz, izi bozar. (Maske yalnız DENETİM İZİNDE var
//    ve orayı sunucu ile servis yazıyor.)
//  · PAROLA GÖSTERMEZ, YAZMAZ, SIFIRLAMAZ. Hiçbir uçta geçmiyor.
//  · E-POSTA YAZMAZ. Giriş kimliğidir; değiştirmek hesabı devretmektir.
//  · SİLMEZ. Silme ucu yok ve olmayacak; hesap yalnız kapatılır ve kayıt durur.
//  · ADRES YAZMAZ. Adres siparişe kopyalanıyor, bağlanmıyor: defteri buradan
//    düzeltmek geçmiş siparişlerin adresini değiştirmez ve yönetici
//    değiştirdiğini sanır. Harita da çizilmez — dış bir harita servisine istek
//    atmak, müşterinin ev adresini üçüncü bir tarafa göndermek olurdu.
//  · SİPARİŞ VE ABONELİK DEĞİŞTİRMEZ. Revizyon `bld_orders`ın, abonelik
//    `bld_subscriptions`ın işidir ve buradan oraya kısayol da konmaz: bir iş
//    eylemi tek ekranda durur, yoksa denetim izinde "hangi ekrandan yapıldı"
//    sorusu cevapsız kalır.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · TELEFON ALANI `type: 'phone'` DEĞİL. Kitin telefon doğrulayıcısı 5 ile
//    başlayan 10 haneli cebi şart koşuyor; sözleşme 10-15 hane kabul ediyor ve
//    kurum telefonu sabit hat olabiliyor (312…). İstemci denetimi sunucudan
//    KATI olursa, kullanıcı sunucunun kabul edeceği bir numarayı gönderemez.
//  · YAZMA GEREKÇE İSTER (>=10). Kuru prova şalteri yoktur: buradan yapılan
//    her yazma gerçektir. Yanıttaki `dry_run` yine okunur — bir kurulum provayı
//    ayardan açarsa ekran "kaydedildi" DEMEMELİ.
//  · HESAP KAPATMA AYRI YETKİ İSTER (`bld_customers.disable`) ve gerekçeli
//    onaydan geçer. Hesabı AÇMAK aynı anahtarı istemez: kapatmak yıkıcı, açmak
//    onarıcıdır.
//  · AKTİF ABONELİK ENGEL DEĞİL, UYARIDIR. Abonelik üretimi hesap kapanınca
//    durmaz (kural hesaba değil aboneliğe bağlıdır) ve yönetici bunu bilmeli.
//  · SMS SEKMESİNİN UCU BAŞKA ALANDA (`control/sms/log`). Sunucu o okuma için
//    `customer.read` satırı yazmaz; yerel iz tek kayıttır ve `sms.read` adını
//    taşır. Erişim izi sekmesinde ayrımı görürsünüz.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_customers/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_customers/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, confirmWithReason, copyText, h,
  loadStyles, money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid, formatPhone } from '../../ui-kit/form.js';

const BASE = '/api/bld_customers';

/** Gerekçe alt sınırı — sunucu ve servis de denetliyor; bu erken geri bildirim. */
const REASON_MIN = 10;

/** Gerekçe üst sınırı (sözleşme §3, müşteri alanı). */
const REASON_MAX = 500;

/** Aramanın alt sınırı; kısası isteğe hiç konmaz (sunucu 422 verirdi). */
const QUERY_MIN = 2;

// ---------------------------------------------------------------- sözlükler

/**
 * Hesap durumu rozetleri. RENK TEK BAŞINA ANLAM TAŞIMAZ: her rozetin içinde
 * yazı var ve tabloda ayrıca bir de metin sütunu bulunuyor.
 */
const ACCOUNT_TONE = { true: 'good', false: 'bad' };

/**
 * Sipariş durumu tonları — `bld_kds` panelindeki sıranın aynısı. Etiketi
 * SUNUCU veriyor (`status_label`); burada yalnız ton var, çünkü etiket
 * sözlüğünü iki yerde tutmak ikisinin ayrışması demekti.
 */
const ORDER_TONE = {
  yeni: 'info', onaylandi: 'info', hazirlaniyor: 'warn', hazir: 'good',
  yolda: 'good', teslim_edildi: 'dim', iptal: 'bad',
};

/** Abonelik durumu tonları. */
const SUBSCRIPTION_TONE = {
  pending: 'warn', active: 'good', paused: 'warn', cancelled: 'bad', expired: 'dim',
};

/** Yerel yazma izinin sonuç tonları. */
const RESULT_TONE = {
  denendi: 'warn', ok: 'good', dry_run: 'info', engellendi: 'dim', hata: 'bad',
};

/**
 * Yerel izlerdeki `result` için okunur karşılık. "denendi" ayrı bir tonda
 * durur çünkü o satır BİLİNMEYEN bir denemedir: istek gidip gitmediği belli
 * değil ve sunucunun kendi defteri onu hiç bilmiyor olabilir.
 */
const RESULT_LABEL = {
  denendi: 'Denendi (sonuç bilinmiyor)',
  ok: 'Uygulandı',
  dry_run: 'Kuru prova',
  engellendi: 'Engellendi',
  hata: 'Hata',
};

/** Ödeme durumu etiketleri (`orders.md`). */
const PAYMENT_LABEL = {
  pending: 'Bekliyor', paid: 'Ödendi', failed: 'Başarısız', refunded: 'İade',
};

// -------------------------------------------------------------------- durum

const EMPTY_STATE = {
  tab: 'list',
  // Geçidin BLD'ye ulaşıp ulaşamadığı (K7). Okuma yanıtlarından toplanır; her
  // sekmenin uyarısı ve her yazma düğmesinin kapısı buradan beslenir.
  link: { connected: true, error: '' },
  // Süzgeç sözleşmesi ve ekran tercihi `GET /overview`ten gelir; uyarı
  // METİNLERİ durumda tutulmaz, doğrudan DOM'a yazılır (tek okuyucusu var).
  spec: null,
  prefs: null,
  // Liste
  rows: [],
  meta: { page: 1, per_page: 25, total: 0, last_page: 1 },
  listLoaded: false,
  listError: '',
  filters: {},
  // Çekmece — TEK müşteri, tembel sekmeler
  open: null,          // {id, tab, customer, loaded:{}, data:{}, error:{}}
  // Yerel defterler
  access: [], accessLoaded: false, accessError: '',
  audit: [], auditLoaded: false, auditError: '',
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];   // cleanup'ta çağrılacak GERÇEK kaynak bırakıcılar

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
    error.code = typeof raw === 'string' ? (result.code || '') : (raw.code || '');
    error.blocked = Boolean(result.blocked);
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
 * çizmek yanlış olurdu — yönetici "müşteri yok" ile "sunucuya ulaşılamıyor"u
 * ayırt EDEMEZ.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
    };
    return false;
  }
  // Anahtar HİÇ YOKSA durum değişmez: `overview` bilerek `connected`
  // taşımıyor (BLD'ye hiç gitmiyor) ve bilinen bir kopukluğu "düzeldi"
  // saymamalı.
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

/** Durum satırının metni — bağlantı durumu her zaman içinde. */
function statusText(extra = '') {
  const link = state.link.connected ? 'BLD bağlı' : `BLD YOK — ${state.link.error}`;
  return extra ? `${extra} · ${link}` : link;
}

/** Boş/eksik değeri tire ile yazar; `0` ve `false` gerçek değerdir. */
function orDash(value) {
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

/**
 * Sayı "bilinmiyor" olabilir: servis eksik istatistiği `-1` ile işaretliyor.
 * Sıfır yazmak, "hiç sipariş vermemiş" ile "sayı gelmedi"yi aynı gösterirdi.
 */
function orUnknown(value, render = num) {
  return Number(value) < 0 ? 'bilinmiyor' : render(value);
}

/** ISO damgasını okunur yazar; boşsa tire. */
function when(iso) {
  return iso ? stampIso(iso) : '—';
}

/** Telefonu okunur biçime çevirir; çevrilemiyorsa HAM hâli yazılır. */
function phoneText(value) {
  if (!value) return '—';
  const pretty = formatPhone(value);
  return pretty || String(value);
}

/** Panelin içindeki bir düğmeyi işlem boyunca kilitler (çift tıklama). */
async function guard(fn) {
  if (busy) return null;
  busy = true;
  try {
    return await fn();
  } finally {
    busy = false;
  }
}

/**
 * Gerekçeli onay — YAZMANIN TEK KAPISI.
 *
 * Kitin `confirmWithReason`u varsayılan olarak 3 karakter istiyor; sözleşme 10
 * istiyor ve sunucu da denetliyor. Sınırı burada yükseltmek, kullanıcıyı
 * sunucudan dönecek bir hataya göndermekten iyidir.
 */
function askReason({ title, description, confirmLabel, danger = true }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (en az ${REASON_MIN}, en çok ${REASON_MAX} karakter)`,
  });
}

/** Yazma yanıtını kullanıcıya duyurur; kuru prova YANITTAN okunur. */
function announce(result, done) {
  if (result?.dry_run) {
    // Bu ekranda kuru prova şalteri YOK. Yanıtta `dry_run: true` görülüyorsa
    // kurulum provayı ayardan açmış demektir ve ekran "yapıldı" DEMEMELİ.
    toast('KURU PROVA: istek sunucuya gitti ama hiçbir şey yazılmadı. '
      + 'Kurulumdaki kuru prova ayarı açık.', 'warn');
    return;
  }
  toast(done, 'good');
}

// =============================================================== liste (ana)

function paintList() {
  const wrap = nodes.body;
  wrap.replaceChildren();

  wrap.append(nodes.notice);
  wrap.append(nodes.filters.node);

  if (!state.listLoaded) {
    wrap.append(skeletonRows(8, 7));
    return;
  }

  if (state.listError) {
    wrap.append(alertBox(state.listError, 'bad'));
  }

  const empty = state.link.connected
    ? emptyState({
      title: 'Bu aramaya uyan müşteri yok',
      text: 'Ad, soyad, telefon, e-posta ya da kurum adının en az iki harfini yazın.',
      actions: [button('Süzgeci temizle', { onClick: () => resetFilters() })],
    })
    : emptyState({
      title: 'BLD sunucusuna ulaşılamıyor',
      text: state.link.error,
      actions: [button('Yeniden dene', { onClick: () => loadList() })],
    });

  nodes.table = dataTable({
    columns: [
      {
        key: 'full_name', label: 'Ad Soyad', width: 'minmax(0, 1.6fr)',
        cell: (row) => {
          const box = h('div', 'bc-name');
          box.append(h('b', undefined, row.full_name || '(adsız kayıt)'));
          if (row.org_name) box.append(h('span', 'bc-sub', row.org_name));
          return box;
        },
      },
      {
        key: 'telephone', label: 'Telefon', width: '150px',
        // MASKELENMEZ — sözleşme. Yönetici müşteriyi telefonundan tanır.
        cell: (row) => phoneText(row.telephone),
      },
      { key: 'email', label: 'E-posta', width: 'minmax(0, 1.4fr)',
        cell: (row) => orDash(row.email) },
      { key: 'account_type_label', label: 'Tür', width: '96px' },
      { key: 'order_count', label: 'Sipariş', width: '90px', align: 'num',
        cell: (row) => num(row.order_count) },
      {
        key: 'last_order_at', label: 'Son sipariş', width: '150px',
        cell: (row) => (row.last_order_at ? ago(row.last_order_at) : '—'),
        title: 'Müşterinin en son sipariş verdiği an',
      },
      {
        key: 'subscription_count', label: 'Abonelik', width: '96px', align: 'num',
        cell: (row) => (row.subscription_count
          ? badge(`${num(row.subscription_count)} adet`, 'info')
          : h('span', 'bc-dim', '—')),
      },
      {
        key: 'status', label: 'Hesap', width: '110px',
        cell: (row) => badge(row.status ? 'Açık' : 'Kapalı', ACCOUNT_TONE[row.status]),
      },
    ],
    rows: state.rows,
    onRow: (row) => openCustomer(row.customer_id),
    rowKey: (row) => String(row.customer_id),
    empty,
  });
  wrap.append(nodes.table.node);

  nodes.pager = pager({
    total: state.meta.total,
    page: state.meta.page,
    size: state.meta.per_page,
    onChange: ({ page, size }) => loadList({ page, per_page: size }),
  });
  wrap.append(nodes.pager.node);

  nodes.status.set(
    statusText(`${num(state.meta.total)} müşteri · sayfa ${state.meta.page}/${state.meta.last_page}`),
    Boolean(state.listError) || !state.link.connected,
  );
}

function resetFilters() {
  // `reset()` süzgeç şeridinin kendi `onChange`ini tetikliyor ve o da listeyi
  // çekiyor. BURADA İKİNCİ BİR ÇAĞRI YOK: her çağrı bir denetim satırı yazar
  // ve "temizle"ye basmak tek bir eylemdir, iki değil.
  nodes.filters.reset();
}

/**
 * Listeyi çeker. HER ÇAĞRI BİR DENETİM SATIRI YAZAR — bu yüzden yalnız
 * kullanıcı eylemiyle çağrılır (açılış, arama, sayfa, "Yenile").
 */
async function loadList(overrides = {}) {
  const values = state.filters || {};
  const query = new URLSearchParams();
  const text = String(values.q || '').trim();
  // Kısa arama İSTEĞE KONMAZ: sunucu 422 verirdi ve kullanıcı yazmaya devam
  // ederken hata görürdü. Servis de aynı kapıyı taşıyor (çift kapı).
  if (text.length >= QUERY_MIN) query.set('q', text);
  if (values.status) query.set('status', values.status);
  // ÜÇ DEĞERLİ: '' süzgeç yok, 'true'/'false' gerçek süzgeç. Boş değeri
  // göndermek, "aboneliği olmayanlar" demek olurdu.
  if (values.subscription) query.set('has_subscription', values.subscription);
  if (values.sort) query.set('sort', values.sort);
  if (values.direction) query.set('direction', values.direction);
  query.set('page', String(overrides.page || 1));
  if (overrides.per_page) query.set('per_page', String(overrides.per_page));

  nodes.status.set('Müşteriler aranıyor…');
  try {
    const payload = await call(`${BASE}/customers?${query.toString()}`);
    linkOk(payload);
    state.rows = payload.items || [];
    state.meta = payload.meta || state.meta;
    // KOPUKLUK BURADA HATA SAYILMAZ: `linkOk` onu `state.link`e yazdı ve
    // ekranda boş durum kutusu olarak çıkacak. Aynı cümleyi bir de uyarı
    // kutusunda tekrarlamak, tek bir sorunu iki ayrı sorun gibi gösterirdi.
    state.listError = '';
  } catch (error) {
    state.rows = [];
    state.listError = error.message;
    state.link = { connected: false, error: error.message };
  }
  state.listLoaded = true;
  if (state.tab === 'list') paintList();
}

// ============================================================ müşteri kartı

/**
 * Çekmeceyi açar ve YALNIZCA "Bilgiler" sekmesini yükler.
 *
 * Öteki sekmeler ilk açıldıklarında yüklenir. Hepsini birden çekmek,
 * yöneticinin bakmadığı dört ekran için dört denetim satırı yazmak olurdu.
 */
async function openCustomer(customerId) {
  // AÇIK BİR ÇEKMECE VARSA ÖNCE KAPANIR. Erişim izi tablosundaki bir müşteri
  // kimliğine basmak, kart açıkken de olabiliyor; ikinci bir katman açmak
  // birincisinin formunu asılı bırakır ve `Escape` yalnız üsttekini kapatır.
  state.open?.view?.close?.();

  state.open = {
    id: customerId, tab: 'info', customer: null,
    loaded: {}, data: {}, error: {}, form: null,
  };

  const view = drawer(nodes.root, {
    title: 'Müşteri kartı',
    subtitle: `#${customerId} · bu kartın açılışı denetim izine yazıldı`,
    onClose: () => {
      state.open?.form?.destroy?.();
      state.open = null;
    },
  });
  state.open.view = view;

  state.open.tabs = tabBar([
    { key: 'info', label: 'Bilgiler' },
    { key: 'addresses', label: 'Adresler' },
    { key: 'orders', label: 'Siparişler' },
    { key: 'subscriptions', label: 'Abonelik' },
    { key: 'sms', label: 'SMS geçmişi' },
  ], 'info', (key) => showCustomerTab(key));

  state.open.panel = h('div', 'bc-drawer-body');
  view.body.append(state.open.tabs.node, state.open.panel);

  await showCustomerTab('info');
}

async function showCustomerTab(key) {
  if (!state.open) return;
  state.open.tab = key;
  const panel = state.open.panel;
  panel.replaceChildren(skeletonRows(5, 4));

  const loaders = {
    info: loadInfo,
    addresses: () => loadSub('addresses', `${BASE}/customers/${state.open.id}/addresses`),
    orders: () => loadSub('orders', `${BASE}/customers/${state.open.id}/orders`),
    subscriptions: () => loadSub('subscriptions',
      `${BASE}/customers/${state.open.id}/subscriptions`),
    sms: () => loadSub('sms', `${BASE}/customers/${state.open.id}/sms`),
  };
  // TEMBEL: yalnız ilk açılışta yüklenir. Sekmeye her dönüşte yeniden çekmek,
  // her dönüş için bir denetim satırı yazmak olurdu; "Yenile" düğmesi var.
  if (!state.open.loaded[key]) await loaders[key]();
  if (!state.open || state.open.tab !== key) return;

  ({
    info: paintInfo,
    addresses: paintAddresses,
    orders: paintOrders,
    subscriptions: paintSubscriptions,
    sms: paintSms,
  }[key])();
}

async function loadInfo() {
  try {
    const payload = await call(`${BASE}/customers/${state.open.id}`);
    linkOk(payload);
    if (!state.open) return;
    state.open.customer = payload.customer || null;
    state.open.error.info = payload.connected === false ? payload.error : '';
  } catch (error) {
    if (!state.open) return;
    state.open.customer = null;
    state.open.error.info = error.message;
  }
  if (state.open) state.open.loaded.info = true;
}

async function loadSub(key, path) {
  try {
    const payload = await call(path);
    linkOk(payload);
    if (!state.open) return;
    state.open.data[key] = payload;
    state.open.error[key] = payload.connected === false ? payload.error : '';
  } catch (error) {
    if (!state.open) return;
    state.open.data[key] = { items: [] };
    state.open.error[key] = error.message;
  }
  if (state.open) state.open.loaded[key] = true;
}

/** Sekmeyi elle tazeler — yeni bir denetim satırı yazar, kullanıcı bilir. */
function refreshTab(key) {
  if (!state.open) return;
  state.open.loaded[key] = false;
  showCustomerTab(key);
}

// ---------------------------------------------------------- bilgiler sekmesi

function paintInfo() {
  const panel = state.open.panel;
  panel.replaceChildren();

  if (state.open.error.info) {
    panel.append(alertBox(state.open.error.info, 'bad'));
  }
  const person = state.open.customer;
  if (!person) {
    panel.append(emptyState({
      title: 'Müşteri kaydı okunamadı',
      text: state.open.error.info || 'BLD sunucusuna ulaşılamıyor.',
      actions: [button('Yeniden dene', { onClick: () => refreshTab('info') })],
    }));
    return;
  }

  state.open.view.setTitle(`${person.full_name || '(adsız kayıt)'} · #${person.customer_id}`);

  // --------------------------------------------------------------- özet
  const stats = person.stats;
  panel.append(kpiRow([
    { label: 'Sipariş', value: orUnknown(stats.order_count),
      title: 'Toplam sipariş sayısı' },
    { label: 'İptal', value: orUnknown(stats.cancelled_order_count),
      tone: Number(stats.cancelled_order_count) > 0 ? 'warn' : '' },
    { label: 'Toplam harcama', value: orUnknown(stats.total_spent_kurus, money) },
    { label: 'Etkin abonelik', value: orUnknown(stats.active_subscription_count) },
    {
      label: 'Ödenmemiş dönem borcu',
      value: orUnknown(stats.unpaid_total_kurus, money),
      tone: Number(stats.unpaid_total_kurus) > 0 ? 'bad' : '',
      // Cari hesap kaldırıldı; başka bir borç kaynağı YOK.
      title: 'Abonelik dönem ödemelerinden gelir (cari hesap kaldırıldı).',
    },
    { label: 'Kayıtlı adres', value: orUnknown(stats.address_count) },
  ]));

  // ------------------------------------------------- salt okunur künye
  const facts = h('div', 'bc-facts');
  const fact = (label, value, hint) => {
    const box = h('div', 'bc-fact');
    box.append(h('span', 'bc-fact-label', label));
    const line = h('div', 'bc-fact-value');
    line.append(typeof value === 'string' ? h('span', undefined, value) : value);
    box.append(line);
    if (hint) box.append(h('span', 'bc-fact-hint', hint));
    return box;
  };

  const mail = h('div', 'bc-copy');
  mail.append(h('span', undefined, orDash(person.email)));
  if (person.email) {
    mail.append(button('Kopyala', {
      variant: 'ghost',
      onClick: () => copyText(person.email).then(() => toast('E-posta kopyalandı.')),
    }));
  }

  facts.append(
    fact('E-posta', mail,
      'DEĞİŞTİRİLEMEZ: giriş kimliğidir ve değiştirmek hesabı devretmek '
      + 'anlamına gelir. Müşteri kendi hesap ekranından ya da destekten düzeltir.'),
    fact('Parola', h('span', 'bc-dim', 'hiçbir yerde görünmez'),
      'Okunmaz, yazılmaz, sıfırlanmaz. Parola sıfırlama müşterinin kendi akışıdır.'),
    fact('Hesap türü', person.account_type_label,
      'Okunur, yazılmaz: kurumsal sipariş kapısı kaldırıldı, alan artık yalnız '
      + 'geçmiş kayıtların etiketi.'),
    fact('Hesap durumu',
      badge(person.status ? 'Açık' : 'Kapalı', ACCOUNT_TONE[person.status]),
      person.status
        ? 'Müşteri giriş yapabilir ve sipariş verebilir.'
        : 'Müşteri giriş YAPAMAZ ve sipariş VEREMEZ. Kayıt silinmedi.'),
    fact('Hesap doğrulaması',
      badge(person.is_activated ? 'Doğrulanmış' : 'Doğrulanmamış',
        person.is_activated ? 'good' : 'warn'),
      'Müşterinin kendi doğrulama akışından gelir; buradan değiştirilemez.'),
    fact('Kayıt tarihi', when(person.created_at)),
    fact('Son giriş', when(person.last_login)),
    fact('İlk sipariş', when(stats.first_order_at)),
  );
  panel.append(card('Değiştirilemeyen bilgiler', facts,
    'Bu bölümdeki hiçbir alanın yazma yolu sözleşmede tanımlı değil.'));

  // ------------------------------------------------------ düzenlenebilir
  state.open.form?.destroy?.();
  const form = formGrid({
    fields: [
      { key: 'first_name', label: 'Ad', type: 'text', required: true, maxLength: 128 },
      { key: 'last_name', label: 'Soyad', type: 'text', required: true, maxLength: 128 },
      {
        key: 'telephone', label: 'Telefon', type: 'text', maxLength: 32,
        hint: 'Rakam, boşluk ve + ( ) - kullanılabilir; temizlendiğinde 10-15 hane.',
        // `type: 'phone'` KULLANILMIYOR: kitin doğrulayıcısı 5 ile başlayan 10
        // haneli cebi şart koşuyor, sözleşme ise 10-15 hane kabul ediyor.
        // İstemci denetimi sunucudan KATI olursa kullanıcı, sunucunun kabul
        // edeceği bir numarayı gönderemez.
        validate: phoneRule,
      },
      { key: 'org_name', label: 'Kurum adı', type: 'text', maxLength: 255, wide: true },
      { key: 'tax_office', label: 'Vergi dairesi', type: 'text', maxLength: 255 },
      {
        key: 'tax_no', label: 'Vergi / TC no', type: 'text', maxLength: 11,
        hint: 'Vergi numarası 10, TC kimlik numarası 11 hane.',
        validate: taxRule,
      },
      { key: 'contact_person', label: 'Yetkili kişi', type: 'text', maxLength: 255 },
      {
        key: 'org_phone', label: 'Kurum telefonu', type: 'text', maxLength: 32,
        hint: 'Sabit hat olabilir; aynı kural geçerli.',
        validate: phoneRule,
      },
    ],
    value: {
      first_name: person.first_name,
      last_name: person.last_name,
      telephone: person.telephone,
      org_name: person.org_name,
      tax_office: person.tax_office,
      tax_no: person.tax_no,
      contact_person: person.contact_person,
      org_phone: person.org_phone,
    },
    onChange: () => paintSaveBar(),
  });
  // `closers`a EKLENMEZ: bu fonksiyon her tazelemede yeniden çalışıyor ve her
  // seferinde bir kapatıcı biriktirmek, panel kapanana kadar ölü formlara
  // tutunmak olurdu. Eski form yukarıda yok edildi; sonuncusunu çekmecenin
  // `onClose`u ve `mount` temizliği yok eder.
  state.open.form = form;

  const saveBar = h('div', 'bc-savebar');
  state.open.saveBar = saveBar;
  panel.append(card('İletişim bilgileri ve kurum etiketleri',
    (() => { const box = h('div'); box.append(form.node, saveBar); return box; })(),
    'Kurum alanları SERBEST METİN etikettir; bir yetki ya da fiyat belirlemez.'));
  paintSaveBar();

  // ------------------------------------------------------- hesap eylemi
  const actions = h('div', 'bc-actions');
  if (!state.link.connected) {
    actions.append(blockedButton(
      person.status ? 'Hesabı kapat' : 'Hesabı aç',
      'BLD sunucusuna ulaşılamıyor; yazma denenmez.'));
  } else if (person.status) {
    actions.append(button('Hesabı kapat', {
      variant: 'danger',
      onClick: () => setAccount(false),
    }));
  } else {
    actions.append(button('Hesabı yeniden aç', {
      variant: 'primary',
      onClick: () => setAccount(true),
    }));
  }
  actions.append(h('span', 'kit-spacer'));
  actions.append(button('Kartı tazele', { variant: 'ghost',
    onClick: () => refreshTab('info') }));

  const warn = h('div');
  warn.append(actions);
  if (person.status && Number(stats.active_subscription_count) > 0) {
    warn.append(alertBox(
      `Bu müşterinin ${num(stats.active_subscription_count)} etkin aboneliği var. `
      + 'Hesabı kapatmak abonelik üretimini DURDURMAZ — kural hesaba değil '
      + 'aboneliğe bağlıdır; abonelikten üretilen siparişler oluşmaya devam eder. '
      + 'Üretimi durdurmak için Abonelikler ekranından aboneliği duraklatın.', 'warn'));
  }
  warn.append(hintBox(
    'Müşteri SİLİNMEZ ve silme düğmesi yoktur: geçmiş siparişlerin müşterisi '
    + 'olmayan kayıtlara dönüşmesi muhasebe ve denetim açısından geri alınamaz '
    + 'bir kayıptır. Hesap kapatmak yalnız girişi ve sipariş vermeyi engeller.'));
  panel.append(card('Hesap', warn));

  // --------------------------------------------------- bu kaydın yerel izi
  const trail = h('div', 'bc-inline-audit');
  panel.append(card('Bu kayıtta yapılan yazma denemeleri', trail,
    'Yerel defter — ağa çıkmaz. "Denendi"de kalmış bir satır, isteğin gidip '
    + 'gitmediği bilinmeyen bir denemedir.'));
  loadCustomerAudit(trail);
}

function phoneRule(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return null;                      // boş → `null` (sözleşme)
  if (!/^[0-9+()\-\s]+$/.test(raw)) {
    return 'Yalnız rakam, boşluk ve + ( ) - kullanılabilir.';
  }
  const only = raw.replace(/\D/g, '');
  if (only.length < 10 || only.length > 15) {
    return `Temizlendiğinde 10-15 hane olmalı; şu an ${only.length} hane.`;
  }
  return null;
}

function taxRule(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) return 'Yalnız rakam yazın.';
  if (raw.length !== 10 && raw.length !== 11) {
    return 'Vergi numarası 10, TC kimlik numarası 11 hane olmalı.';
  }
  return null;
}

/**
 * Kaydet şeridi. YALNIZ DEĞİŞEN ALANLAR gönderilir (`patch()`); tam gövde
 * göndermek, dokunulmamış alanları da denetim izine "değişti" diye yazardı.
 */
function paintSaveBar() {
  const bar = state.open?.saveBar;
  const form = state.open?.form;
  if (!bar || !form) return;
  bar.replaceChildren();

  const dirty = form.dirty();
  const errors = form.errors();

  if (!dirty.length) {
    bar.append(h('span', 'bc-dim', 'Değişiklik yok.'));
    return;
  }
  bar.append(h('span', undefined, `${dirty.length} alan değişti.`));
  bar.append(h('span', 'kit-spacer'));

  if (errors.length) {
    bar.append(blockedButton('Kaydet', errors[0].message));
    return;
  }
  if (!state.link.connected) {
    bar.append(blockedButton('Kaydet', 'BLD sunucusuna ulaşılamıyor; yazma denenmez.'));
    return;
  }
  bar.append(button('Vazgeç', {
    variant: 'ghost',
    // FORMU YENİDEN ÇİZ, `reset()` ÇAĞIRMA: `reset(next)` verilen kaydı YENİ
    // ASIL sayar ve `form.draft()` geçilseydi değişiklikler "asıl" olurdu —
    // yani vazgeçme, değişiklikleri kalıcı kirlilikten çıkarıp gizlerdi.
    // Elimizde zaten sunucudan gelen kayıt var; kartı ondan yeniden kuruyoruz
    // ve BLD'ye yeni bir istek de atılmıyor (denetim izine satır düşmez).
    onClick: () => paintInfo(),
  }));
  bar.append(button('Kaydet', { variant: 'primary', onClick: () => saveInfo() }));
}

async function saveInfo() {
  const form = state.open?.form;
  if (!form) return;
  const fields = form.patch();
  if (!Object.keys(fields).length) return;

  const names = Object.keys(fields).join(', ');
  const reason = await askReason({
    title: 'Müşteri bilgilerini güncelle',
    description: `Değişen alanlar: ${names}. Gerekçe denetim izine yazılır ve `
      + 'silinemez; telefon numaraları izde MASKELENİR.',
    confirmLabel: 'Kaydet',
    danger: false,
  });
  if (!reason) return;

  await guard(async () => {
    try {
      const result = await call(`${BASE}/customers/${state.open.id}`, {
        method: 'PATCH',
        // `dryRun` GÖNDERİLMEZ: bu ekranda kuru prova şalteri yok ve
        // varsayılan kapalı. Yanıttaki `dry_run` yine okunuyor (bkz. announce).
        body: { reason, fields },
      });
      announce(result, `Kaydedildi: ${(result.changed || []).join(', ') || names}.`);
      // Kart TAZELENİR (yeni bir okuma, bir denetim satırı) — yönetici zaten
      // o karta bakıyor ve kaydettiği değerin gerçekten yazıldığını görmeli.
      refreshTab('info');
      // LİSTE TAZELENMEZ, YERİNDE GÜNCELLENİR. Yeni bir arama isteği atmak,
      // kimsenin sormadığı bir okuma için denetim izine ikinci bir satır
      // yazmak olurdu; elimizde zaten yazılan değerler var.
      patchListRow(state.open.id, fields);
    } catch (error) {
      // `blocked` = istek geçide HİÇ GİTMEDİ (yasak alan, kısa gerekçe, izin
      // yok). Tekrar denemek anlamsızdır ve ton bunu ayırt ettirmeli:
      // "sunucu reddetti" ile "biz göndermedik" farklı cümlelerdir.
      toast(error.message, error.blocked ? 'warn' : 'bad');
    }
  });
}

/**
 * Liste satırını YENİDEN OKUMADAN günceller.
 *
 * Bu ekranda her okuma bir denetim satırı yazıyor; yazma sonrası listeyi
 * yeniden çekmek, yöneticinin bakmadığı bir sayfa için deftere satır eklemek
 * olurdu. Yazılan değerleri zaten biliyoruz.
 */
function patchListRow(customerId, changes) {
  const row = state.rows.find((item) => item.customer_id === customerId);
  if (!row) return;
  Object.assign(row, changes);
  if (typeof changes.first_name === 'string' || typeof changes.last_name === 'string') {
    row.full_name = `${row.first_name || ''} ${row.last_name || ''}`.trim();
  }
  if (state.tab === 'list') paintList();
}

/**
 * Hesabı kapatır ya da açar.
 *
 * KAPATMA YIKICIDIR ve ayrı izin ister (`bld_customers.disable`); izin
 * BURADA DENETLENMEZ (K9 — görünürlük sunucuda süzülür), uç 403 dönerse ekran
 * bunu söyler. Açmak onarıcıdır ve `manage` de yeter.
 */
async function setAccount(enable) {
  const person = state.open?.customer;
  if (!person) return;

  const subs = Number(person.stats.active_subscription_count);
  const reason = await askReason({
    title: enable ? 'Hesabı yeniden aç' : 'Hesabı kapat',
    description: enable
      ? `${person.full_name} yeniden giriş yapabilecek ve sipariş verebilecek.`
      : `${person.full_name} giriş YAPAMAYACAK ve sipariş VEREMEYECEK. Kayıt `
        + 'silinmez, geçmiş siparişler durur.'
        + (subs > 0
          ? ` DİKKAT: ${subs} etkin abonelik var ve abonelik üretimi DURMAZ.`
          : ''),
    confirmLabel: enable ? 'Hesabı aç' : 'Hesabı kapat',
    danger: !enable,
  });
  if (!reason) return;

  await guard(async () => {
    try {
      const result = await call(
        `${BASE}/customers/${state.open.id}/${enable ? 'enable' : 'disable'}`,
        { method: 'POST', body: { reason } },
      );
      if (result.already) {
        // Sözleşme `409` vermiyor; sunucu `ok: true` döndü. Ekran "kapatıldı"
        // derse yönetici az önce bir şey değiştirdiğini sanır.
        toast(enable ? 'Hesap zaten açıktı.' : 'Hesap zaten kapalıydı.', 'info');
      } else {
        announce(result, enable ? 'Hesap açıldı.' : 'Hesap kapatıldı.');
      }
      for (const item of result.warnings || []) {
        if (item.code === 'active_subscriptions') {
          toast(`Uyarı: ${(item.subscription_ids || []).length} etkin abonelik `
            + 'üretim yapmaya devam edecek.', 'warn');
        } else {
          toast(`Uyarı: ${item.code}`, 'warn');
        }
      }
      refreshTab('info');
      // Aynı gerekçe: liste yerinde güncellenir, yeniden okunmaz. Kuru provada
      // BLD'de bir şey değişmediği için satıra da dokunulmaz.
      if (!result.dry_run) patchListRow(state.open.id, { status: enable });
    } catch (error) {
      toast(error.message, error.blocked ? 'warn' : 'bad');
    }
  });
}

async function loadCustomerAudit(host) {
  if (!host) return;
  try {
    const payload = await call(`${BASE}/audit?customer_id=${state.open.id}&limit=25`);
    const rows = payload.items || [];
    if (!rows.length) {
      host.replaceChildren(h('span', 'bc-dim', 'Bu kayıtta yazma denemesi yok.'));
      return;
    }
    host.replaceChildren(auditTable(rows, { compact: true }).node);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
  }
}

// --------------------------------------------------------- adresler sekmesi

function paintAddresses() {
  const panel = state.open.panel;
  panel.replaceChildren();
  const payload = state.open.data.addresses || { items: [] };

  if (state.open.error.addresses) panel.append(alertBox(state.open.error.addresses, 'bad'));

  panel.append(hintBox(
    'SALT OKUNUR. Adres siparişe kopyalanıyor, bağlanmıyor: buradan düzeltmek '
    + 'geçmiş siparişlerin adresini DEĞİŞTİRMEZ ve yönetici değiştirdiğini sanır. '
    + 'Adresi müşteri kendi uygulamasından yönetir. Harita da çizilmiyor — dış bir '
    + 'harita servisine istek atmak, müşterinin adresini üçüncü bir tarafa '
    + 'göndermek olurdu.'));

  if (!payload.items.length) {
    panel.append(emptyState({
      title: 'Kayıtlı adres yok',
      text: 'Müşteri henüz bir teslimat adresi eklememiş.',
      actions: [button('Yenile', { onClick: () => refreshTab('addresses') })],
    }));
    return;
  }

  for (const row of payload.items) {
    const box = h('div', 'bc-address');
    const head = h('div', 'bc-address-head');
    head.append(h('b', undefined, row.label || 'Adres'));
    if (row.is_default) head.append(badge('Varsayılan', 'info'));
    box.append(head);
    box.append(h('div', undefined, row.line_1));
    if (row.line_2) box.append(h('div', undefined, row.line_2));
    box.append(h('div', 'bc-sub',
      [row.neighbourhood, row.district, row.city, row.postcode]
        .filter(Boolean).join(' · ')));
    panel.append(card(`#${row.address_id}`, box));
  }
  panel.append(refreshRow('addresses'));
}

// -------------------------------------------------------- siparişler sekmesi

function paintOrders() {
  const panel = state.open.panel;
  panel.replaceChildren();
  const payload = state.open.data.orders || { items: [], meta: {} };

  if (state.open.error.orders) panel.append(alertBox(state.open.error.orders, 'bad'));

  panel.append(hintBox(
    'SALT OKUNUR. Sipariş revizyonu, durum ilerletme ve iptal Siparişler '
    + 'ekranının işidir; buradan oraya kısayol da yok — bir iş eylemi tek '
    + 'ekranda durur, yoksa denetim izinde "hangi ekrandan yapıldı" sorusu '
    + 'cevapsız kalır.'));

  const table = dataTable({
    columns: [
      { key: 'order_number', label: 'Sipariş', width: '130px' },
      {
        key: 'status', label: 'Durum', width: '130px',
        cell: (row) => badge(row.status_label, ORDER_TONE[row.status] || ''),
      },
      { key: 'service_date', label: 'Servis günü', width: '120px' },
      { key: 'item_count', label: 'Kalem', width: '80px', align: 'num' },
      {
        key: 'total_kurus', label: 'Tutar', width: '120px', align: 'num',
        // PARA HER ZAMAN KURUŞ; `money()` tek geçit.
        cell: (row) => money(row.total_kurus),
      },
      {
        key: 'payment_status', label: 'Ödeme', width: '120px',
        cell: (row) => h('span', undefined,
          `${PAYMENT_LABEL[row.payment_status] || orDash(row.payment_status)}`
          + `${row.payment_method ? ` (${row.payment_method === 'cash' ? 'nakit' : 'online'})` : ''}`),
      },
      {
        key: 'is_subscription', label: 'Kaynak', width: '110px',
        cell: (row) => (row.is_subscription
          ? badge(`Abonelik #${row.subscription_id ?? '?'}`, 'info')
          : h('span', 'bc-dim', 'Elle')),
      },
      { key: 'created_at', label: 'Girildi', width: '150px',
        cell: (row) => when(row.created_at) },
    ],
    rows: payload.items,
    dense: true,
    empty: emptyState({
      title: 'Sipariş yok',
      text: 'Bu müşterinin kayıtlı siparişi bulunmuyor.',
    }),
  });
  panel.append(table.node);

  const meta = payload.meta || {};
  if (meta.total) {
    panel.append(pager({
      total: meta.total, page: meta.page, size: meta.per_page,
      onChange: ({ page, size }) => loadOrdersPage(page, size),
    }).node);
  }
  panel.append(refreshRow('orders'));
}

async function loadOrdersPage(page, size) {
  const id = state.open?.id;
  if (!id) return;
  await loadSub('orders', `${BASE}/customers/${id}/orders?page=${page}&per_page=${size}`);
  if (state.open?.tab === 'orders') paintOrders();
}

// --------------------------------------------------------- abonelik sekmesi

function paintSubscriptions() {
  const panel = state.open.panel;
  panel.replaceChildren();
  const payload = state.open.data.subscriptions || { items: [] };

  if (state.open.error.subscriptions) {
    panel.append(alertBox(state.open.error.subscriptions, 'bad'));
  }

  panel.append(hintBox(
    'SALT OKUNUR. Abonelik etkinleştirme, duraklatma, sipariş üretme ve sözleşme '
    + 'işlemleri Abonelikler ekranındadır. Burada yalnız "aboneliği var mı, borcu '
    + 'var mı" sorusu cevaplanır — hesabı kapatmadan önce sorulması gereken soru.'));

  if (!payload.items.length) {
    panel.append(emptyState({
      title: 'Abonelik yok',
      text: 'Bu müşterinin kayıtlı aboneliği bulunmuyor.',
      actions: [button('Yenile', { onClick: () => refreshTab('subscriptions') })],
    }));
    return;
  }

  const dayNames = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz'];
  for (const row of payload.items) {
    const box = h('div', 'bc-sub-card');
    const head = h('div', 'bc-address-head');
    head.append(h('b', undefined, `Abonelik #${row.id}`));
    head.append(badge(row.status_label, SUBSCRIPTION_TONE[row.status] || ''));
    if (row.unpaid_periods > 0) {
      head.append(badge(`${num(row.unpaid_periods)} ödenmemiş dönem`, 'bad'));
    }
    box.append(head);

    const grid = h('div', 'bc-facts');
    const line = (label, value) => {
      const item = h('div', 'bc-fact');
      item.append(h('span', 'bc-fact-label', label));
      item.append(h('div', 'bc-fact-value', String(value)));
      return item;
    };
    grid.append(
      line('Başlangıç', row.start_date || '—'),
      line('Bitiş', row.end_date || 'süresiz'),
      line('Servis günleri',
        (row.service_days || []).map((day) => dayNames[day - 1] || day).join(', ') || '—'),
      line('Günlük adet', num(row.default_quantity)),
      line('Birim fiyat',
        Number(row.agreed_unit_price_kurus) < 0 ? 'girilmemiş'
          : money(row.agreed_unit_price_kurus)),
      line('Sonraki servis', row.next_service_date || '—'),
      line('Sözleşme', orDash(row.contract_status)),
      line('Ödenmemiş toplam', money(row.unpaid_total_kurus)),
    );
    box.append(grid);
    panel.append(card('', box));
  }
  panel.append(refreshRow('subscriptions'));
}

// -------------------------------------------------------------- SMS sekmesi

function paintSms() {
  const panel = state.open.panel;
  panel.replaceChildren();
  const payload = state.open.data.sms || { items: [], meta: {} };

  if (state.open.error.sms) panel.append(alertBox(state.open.error.sms, 'bad'));

  panel.append(hintBox(
    'Telefon SUNUCUDA maskelenir ve gövde 120 karakterde kırpılır: gönderim '
    + 'kaydı bir iletişim defterine dönüşmemeli. Bu sekmenin ucu SMS alanındadır, '
    + 'müşteri alanında değil — sunucu bu okuma için müşteri okuma satırı yazmaz, '
    + 'yerel iz tek kayıttır ve "sms.read" adını taşır.'));

  if (Number(payload.segment_total) >= 0) {
    panel.append(kpiRow([
      { label: 'Kayıt', value: num(payload.meta?.total || 0) },
      { label: 'Segment (süzülen küme)', value: num(payload.segment_total),
        title: 'Maliyet sorusunun cevabı: gönderilen SMS segmenti toplamı.' },
    ]));
  }

  const table = dataTable({
    columns: [
      { key: 'sent_at', label: 'Gönderildi', width: '150px',
        cell: (row) => when(row.sent_at) },
      { key: 'template_key', label: 'Şablon', width: '150px',
        cell: (row) => orDash(row.template_key) },
      { key: 'phone', label: 'Numara', width: '120px' },
      { key: 'body', label: 'Metin', width: 'minmax(0, 2fr)' },
      { key: 'segments', label: 'Segment', width: '90px', align: 'num' },
      {
        key: 'status', label: 'Sonuç', width: '110px',
        cell: (row) => badge(row.status === 'sent' ? 'Gitti' : 'Başarısız',
          row.status === 'sent' ? 'good' : 'bad'),
      },
      { key: 'context', label: 'Bağlam', width: '100px',
        cell: (row) => orDash(row.context) },
    ],
    rows: payload.items,
    dense: true,
    empty: emptyState({
      title: 'SMS kaydı yok',
      text: 'Bu müşteriye gönderilmiş bir SMS bulunmuyor.',
    }),
  });
  panel.append(table.node);

  const meta = payload.meta || {};
  if (meta.total) {
    panel.append(pager({
      total: meta.total, page: meta.page, size: meta.per_page,
      onChange: ({ page, size }) => loadSmsPage(page, size),
    }).node);
  }
  panel.append(refreshRow('sms'));
}

async function loadSmsPage(page, size) {
  const id = state.open?.id;
  if (!id) return;
  await loadSub('sms', `${BASE}/customers/${id}/sms?page=${page}&per_page=${size}`);
  if (state.open?.tab === 'sms') paintSms();
}

/**
 * "Yenile" satırı. TAZELEME KULLANICI EYLEMİDİR ve yeni bir denetim satırı
 * yazar; bu yüzden düğmenin yanında ne olacağı yazılı.
 */
function refreshRow(key) {
  const row = h('div', 'bc-refresh');
  row.append(h('span', 'bc-dim', 'Yenilemek yeni bir erişim kaydı oluşturur.'));
  row.append(h('span', 'kit-spacer'));
  row.append(button('Yenile', { variant: 'ghost', onClick: () => refreshTab(key) }));
  return row;
}

// ========================================================== yerel defterler

/** Yazma denemesi tablosu — hem çekmecede hem kendi sekmesinde kullanılır. */
function auditTable(rows, { compact = false } = {}) {
  return dataTable({
    columns: [
      { key: 'created_at', label: 'Zaman', width: '150px',
        cell: (row) => when(row.created_at) },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      { key: 'action', label: 'İşlem', width: '150px' },
      ...(compact ? [] : [{
        key: 'customer_id', label: 'Müşteri', width: '100px', align: 'num',
        cell: (row) => (row.customer_id
          ? button(`#${row.customer_id}`, {
            variant: 'ghost', onClick: () => openCustomer(row.customer_id) })
          : h('span', 'bc-dim', '—')),
      }]),
      {
        key: 'result', label: 'Sonuç', width: '190px',
        cell: (row) => badge(RESULT_LABEL[row.result] || row.result,
          RESULT_TONE[row.result] || ''),
      },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)' },
      {
        key: 'detail', label: 'Değişiklik', width: 'minmax(0, 1.4fr)',
        // Telefonlar burada MASKELİ gelir; servis öyle yazıyor.
        cell: (row) => changeText(row.detail),
      },
    ],
    rows,
    dense: true,
    empty: emptyState({ title: 'Yazma denemesi yok',
      text: 'Bu ekrandan henüz bir değişiklik yapılmamış.' }),
  });
}

function changeText(detail) {
  const changes = detail?.changes;
  if (Array.isArray(changes) && changes.length) {
    return changes.map((item) => `${item.field}: ${item.from || '—'} → ${item.to || '—'}`)
      .join(' · ');
  }
  if (detail?.error) return `hata: ${detail.error}`;
  if (detail?.name) return detail.name;
  return '—';
}

function paintAccess() {
  const wrap = nodes.body;
  wrap.replaceChildren();

  wrap.append(hintBox(
    'YEREL DEFTER — ağa çıkmaz, müşteri verisi taşımaz. Sunucuda da bir kayıt '
    + 'tutuluyor ama o yalnız SUNUCUYA ULAŞAN okumayı bilir; ağ koparsa, imza '
    + 'reddedilirse ya da geçit patlarsa "kim kimin kaydını açmak istedi" '
    + 'sorusunun cevabı yalnız burada kalır. Satırlar yalnız kimin, ne zaman, '
    + 'hangi ekranı ve hangi süzgeci kullandığını taşır; dönen kayıtlar ASLA '
    + 'yazılmaz. Kayıt silinemez.'));

  if (!state.accessLoaded) {
    wrap.append(skeletonRows(8, 6));
    return;
  }
  if (state.accessError) wrap.append(alertBox(state.accessError, 'bad'));

  wrap.append(dataTable({
    columns: [
      { key: 'created_at', label: 'Zaman', width: '150px',
        cell: (row) => when(row.created_at) },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      {
        key: 'scope', label: 'Ne açıldı', width: '160px',
        cell: (row) => (state.spec?.read_scopes || [])
          .find((item) => item.value === row.scope)?.label || row.scope,
      },
      {
        key: 'action', label: 'Denetim adı', width: '140px',
        // `sms.read` ile `customer.read` AYRI: SMS ucu müşteri alanında değil
        // ve sunucu onun için müşteri okuma satırı yazmıyor.
        cell: (row) => h('code', undefined, row.action),
        title: 'Sunucudaki karşılığıyla aynı ad; sms.read yalnız yereldedir.',
      },
      {
        key: 'customer_id', label: 'Müşteri', width: '100px', align: 'num',
        cell: (row) => (row.customer_id
          ? button(`#${row.customer_id}`, {
            variant: 'ghost', onClick: () => openCustomer(row.customer_id) })
          : h('span', 'bc-dim', 'liste')),
      },
      {
        key: 'result', label: 'Sonuç', width: '110px',
        cell: (row) => badge(row.result === 'okundu' ? 'Okundu' : 'Hata',
          row.result === 'okundu' ? 'good' : 'bad'),
      },
      {
        key: 'filters', label: 'Süzgeç', width: 'minmax(0, 1.4fr)',
        cell: (row) => filterText(row.filters),
      },
    ],
    rows: state.access,
    dense: true,
    empty: emptyState({ title: 'Erişim kaydı yok',
      text: 'Bu bilgisayardan henüz müşteri kaydı açılmamış.' }),
  }).node);

  const bar = h('div', 'bc-refresh');
  bar.append(h('span', 'kit-spacer'));
  bar.append(button('Yenile', { variant: 'ghost', onClick: () => loadAccess(true) }));
  wrap.append(bar);

  nodes.status.set(statusText(`${num(state.access.length)} erişim kaydı`),
    Boolean(state.accessError));
}

function filterText(filters) {
  const entries = Object.entries(filters || {})
    .filter(([, value]) => value !== null && value !== undefined && value !== '');
  if (!entries.length) return 'süzgeçsiz';
  return entries.map(([key, value]) => `${key}=${value}`).join(' · ');
}

function paintAudit() {
  const wrap = nodes.body;
  wrap.replaceChildren();

  wrap.append(hintBox(
    'YEREL DEFTER — bu ekrandan yapılan YAZMA DENEMELERİ. "Denendi"de kalmış '
    + 'bir satır, isteğin gidip gitmediği bilinmeyen bir denemedir; sunucunun '
    + 'kendi defteri o satırı hiç bilmez. Telefon numaraları burada MASKELİDİR. '
    + 'Kayıt silinemez.'));

  if (!state.auditLoaded) {
    wrap.append(skeletonRows(8, 6));
    return;
  }
  if (state.auditError) wrap.append(alertBox(state.auditError, 'bad'));

  wrap.append(auditTable(state.audit).node);

  const bar = h('div', 'bc-refresh');
  bar.append(h('span', 'kit-spacer'));
  bar.append(button('Yenile', { variant: 'ghost', onClick: () => loadAudit(true) }));
  wrap.append(bar);

  nodes.status.set(statusText(`${num(state.audit.length)} yazma denemesi`),
    Boolean(state.auditError));
}

async function loadAccess(force = false) {
  if (state.accessLoaded && !force) return;
  try {
    const payload = await call(`${BASE}/access-log`);
    state.access = payload.items || [];
    state.accessError = payload.connected === false ? payload.error : '';
  } catch (error) {
    state.access = [];
    state.accessError = error.message;
  }
  state.accessLoaded = true;
  if (state.tab === 'access') paintAccess();
}

async function loadAudit(force = false) {
  if (state.auditLoaded && !force) return;
  try {
    const payload = await call(`${BASE}/audit`);
    state.audit = payload.items || [];
    state.auditError = payload.connected === false ? payload.error : '';
  } catch (error) {
    state.audit = [];
    state.auditError = error.message;
  }
  state.auditLoaded = true;
  if (state.tab === 'audit') paintAudit();
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE, link: { connected: true, error: '' } };

  const view = h('div', 'kit-panel bc');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  // KVKK uyarısı EKRANIN TEPESİNDE VE KALICI. Metin sunucudan gelene kadar
  // burada bir yedek durur: uyarısız açılan bir saniye bile olmamalı.
  nodes.notice = hintBox(
    'Bu ekrandaki her arama ve açılan her müşteri kartı denetim izine yazılır.');

  nodes.tabs = tabBar([
    { key: 'list', label: 'Müşteriler' },
    { key: 'access', label: 'Erişim izi (KVKK)' },
    { key: 'audit', label: 'Yazma izi' },
  ], 'list', (key) => showTab(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '280px',
        placeholder: 'Ad, soyad, telefon, e-posta, kurum (en az 2 harf)' },
      // BAŞLANGIÇ DEĞERİ AÇIKÇA VERİLİR. Verilmezse `filterBar` değeri boş
      // dizeyle kurar ama tarayıcı kutuda İLK SEÇENEĞİ gösterir; ekranda
      // "Hepsi" yazarken istekte hiçbir şey gitmez ve ikisi ayrışır. Boş dize
      // burada yalnız "Fark etmez"in gerçek değeridir.
      { kind: 'select', key: 'status', label: 'Hesap', value: 'all',
        options: [{ value: 'all', label: 'Hepsi' },
          { value: 'active', label: 'Açık hesaplar' },
          { value: 'disabled', label: 'Kapalı hesaplar' }] },
      // ÜÇ DEĞERLİ: boş = süzgeç yok. `toggle` kullanılsaydı üçüncü hâl
      // kaybolur ve ekran "hepsi" diyemezdi.
      { kind: 'select', key: 'subscription', label: 'Abonelik', value: '',
        options: [{ value: '', label: 'Fark etmez' },
          { value: 'true', label: 'Aboneliği olanlar' },
          { value: 'false', label: 'Aboneliği olmayanlar' }] },
      { kind: 'select', key: 'sort', label: 'Sırala', value: 'name',
        options: [{ value: 'name', label: 'Ada göre' },
          { value: 'created', label: 'Kayıt tarihine göre' },
          { value: 'last_order', label: 'Son siparişe göre' }] },
      { kind: 'select', key: 'direction', label: 'Yön', value: 'asc',
        options: [{ value: 'asc', label: 'Artan' },
          { value: 'desc', label: 'Azalan' }] },
    ],
    onChange: (values) => {
      state.filters = values;
      // ARAMA HER TUŞTA SUNUCUYA GİTMEZ demek yetmez: `filterBar` metni zaten
      // geciktiriyor (260 ms) ama burada her tetikleme BİR DENETİM SATIRI
      // yazıyor. Kısa metin isteğe hiç konmaz ve süzgeç sıfırlanınca da tek
      // istek atılır.
      loadList({ page: 1 });
    },
    actions: [button('Yenile', { onClick: () => loadList({ page: state.meta.page }) })],
  });
  closers.push(() => nodes.filters.destroy());
  // ŞERİDİN KENDİ DEĞERLERİYLE BAŞLA. `state.filters` boş bırakılsaydı ilk
  // istek hiçbir süzgeç taşımaz, ekranda ise "Hepsi / Ada göre / Artan"
  // yazardı — kutuların gösterdiği ile isteğe giden ilk gün ayrışırdı.
  state.filters = nodes.filters.values();

  nodes.status = statusLine();
  nodes.body = h('div', 'bc-body');

  const bar = h('div', 'bc-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    if (key === 'access') { loadAccess(); paintAccess(); return; }
    if (key === 'audit') { loadAudit(); paintAudit(); return; }
    paintList();
  }

  root.replaceChildren(view);
  paintList();
  boot();

  return () => {
    // ÇEKMECE KAPANMADAN PANEL DEĞİŞEBİLİR: form global dinleyici tutuyor.
    state.open?.form?.destroy?.();
    state.open?.view?.close?.();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}

/**
 * Açılış. İKİ İSTEK ATILIR VE İKİSİ DE BİLİNÇLİDİR:
 *
 *  1. `GET /overview` — BLD'ye HİÇ GİTMEZ, denetim izine satır YAZMAZ. Süzgeç
 *     sözleşmesini, ekran tercihini ve uyarı metinlerini getirir.
 *  2. `GET /customers` — ilk sayfa. Bu BİR denetim satırı yazar ve yazması
 *     doğrudur: yönetici gezinme çubuğundan "Müşteriler"e bastı, yani müşteri
 *     listesine bakmayı seçti. Listeyi boş açıp "Ara"ya basmasını beklemek
 *     satır sayısını değiştirmez, yalnız bir tık ekler.
 */
async function boot() {
  try {
    const payload = await call(`${BASE}/overview`);
    state.spec = payload.filters || null;
    state.prefs = payload.prefs || null;
    if (payload.kvkk_notice) {
      nodes.notice.textContent = payload.kvkk_notice;
      if (payload.readonly_notice) {
        nodes.notice.append(h('div', 'bc-notice-sub', payload.readonly_notice));
      }
    }
    // Tercih varsa süzgeç kutuları ona göre açılır; yoksa varsayılan durur.
    // `set()` OLAY TETİKLEMEZ (kitin sözleşmesi) — tetikleseydi açılışta iki
    // liste isteği ve iki denetim satırı doğardı.
    if (state.prefs) {
      nodes.filters.set('status', state.prefs.status_filter);
      nodes.filters.set('sort', state.prefs.sort);
      nodes.filters.set('direction', state.prefs.direction);
      state.filters = {
        ...state.filters,
        status: state.prefs.status_filter,
        sort: state.prefs.sort,
        direction: state.prefs.direction,
      };
    }
  } catch (error) {
    // Açılış künyesi alınamadıysa ekran YİNE ÇALIŞIR: süzgeç kutuları kendi
    // varsayılanlarıyla duruyor ve liste ayrı bir uçtan geliyor (K7).
    nodes.status.set(`Açılış künyesi okunamadı: ${error.message}`, true);
  }
  await loadList({ page: 1, per_page: state.prefs?.page_size });
}
