// Manuel Yedekleme paneli — mağaza verisinin yedeği: al, doğrula, indir, geri yükle.
//
// NE YAPAR: en üstte KALICI uyarı bandı ve durum bandı (son yedek ne zaman,
// ne kadar, doğrulandı mı — metin + renk); "Yeni yedek" kartı kapsam seçimi
// ve notla, adımlı ilerlemeyle; disk göstergesi ve "kalan alanla yaklaşık kaç
// yedek daha alınır" cümlesi; envanter tablosu ve satır eylemleri.
//
// NE YAPMAZ:
//  · Yedek dosyasını tarayıcıya AKITMAZ. Panelin `api()` katmanı JSON taşıyor;
//    yüz megabaytlık gövdeyi belleğe alıp base64'e çevirmek hem sidecar'ı hem
//    kabuğu düşürürdü. Dosya sunucudan alınıp yerel diske 0600 ile yazılır ve
//    ekran YOLU söyler. Tavanın üstündeki dosyada indirme hiç başlamaz.
//  · Yedek SİLMEZ — mağazada ADA GÖRE silme ucu hâlâ yayında değil
//    (2026-08-16'da sunucudaki rota listesiyle doğrulandı; `POST
//    backups/prune` toplu budamadır, "şunu sil" değildir). Düğme kapalı
//    durur ve nedenini yazar; basılınca sessizce patlamaz.
//    Listeleme, indirme, alma ve doğrulama uçları AYNI TARİHTE CANLIDA
//    ÇALIŞIYOR — bu kapalılık onları kapsamaz.
//  · Zamanlanmış yedek rotasyonuna karışmaz; buradan alınan yedek manueldir.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · "Doğrulanmadı" ile "doğrulandı" AYRI şeydir. Hiç doğrulanmamış yedek
//    yeşil gösterilseydi, felaket gününde açılmadığında anlaşılırdı.
//  · Adımlı ilerlemede "Dışa aktarılıyor" ve "Sıkıştırılıyor" mağaza tarafında
//    TEK istek içinde geçer; aradaki geçişi gözleyemeyiz. Sahte bir sayaçla
//    ilerletmek yerine adım atlanır — uydurma ilerleme, ilerleme değildir.
//  · Geri yükleme İKİ kapıdan geçer: önce ne olacağını anlatan onay, sonra
//    gerekçeli onay. Sunucu ayrıca önce güvenlik yedeği alır ve alamazsa
//    geri yüklemeyi hiç başlatmaz.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_backups/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_backups/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, bytes as sizeText, button, clip, confirmSimple, confirmWithReason,
  copyText, csvBlob, h, loadStyles, num, percent, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, progress, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';

const BASE = '/api/store_backups';

const VERIFY_TONES = { ok: 'good', bad: 'bad', unknown: 'warn' };
const BAND_TONES = { good: 'good', warn: 'warn', bad: 'bad' };

//: Adımlar mağazadaki gerçek aşamalardır; ilk ikisi tek istek içinde geçer.
const STEPS = ['Dışa aktarılıyor', 'Sıkıştırılıyor', 'Doğrulanıyor', 'Yazıldı'];

//: Ucun hâli üç değerlidir (servis `endpointState` gönderir). "Uç yok" ile
//: "uç kapalı" ayrı başlıklarla anlatılır: ilkinde yapılacak bir şey yok,
//: ikincisinde mağaza sunucusunda açılacak bir anahtar var.
const PENDING_TITLES = {
  missing: 'Yedek ucu henüz yayında değil',
  disabled: "Mağazadaki Kontrol Merkezi API'si kapalı",
};
const pendingTitle = () => PENDING_TITLES[state.endpointState] || 'Yedek ucu çalışmıyor';

const EMPTY_STATE = {
  items: [], connected: false, error: '', loaded: false, endpointPending: false,
  endpointState: 'live',
  summary: { has: false, known: true, tone: 'bad', text: '' },
  disk: { known: false }, diskNote: '',
  policy: { scopes: [], defaultScope: [], deleteAvailable: false, deleteNote: '',
    createAvailable: true, endpointPending: false, endpointState: 'live',
    endpointNote: '', warning: '' },
  features: {},
};

let api = null;
let toast = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};

// ------------------------------------------------------------------ araçlar

/** Sunucu yanıtı `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status?.set(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    nodes.status?.set(error.message || 'İşlem başarısız.', true);
    return null;
  } finally {
    busy = false;
  }
}

/** Yıkıcı her işlem buradan geçer: gerekçe backend'e gider ve denetime yazılır. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function durationText(seconds) {
  const value = Number(seconds || 0);
  if (!value) return '—';
  if (value < 60) return `${num(Math.round(value))} sn`;
  return `${num(Math.floor(value / 60))} dk ${num(Math.round(value % 60))} sn`;
}

function statusText() {
  // "Uç yayında değil" ile "mağaza çökmüş" ayrı cümlelerdir: ilkinde sunucuya
  // bakacak bir şey yok, ikincisinde var. Aynı metni yazmak personeli boşuna
  // sunucu odasına gönderir. Üçüncü hâl (uç KAPALI) da ayrı başlık taşır:
  // orada yapılacak şey var ama sunucu odasında değil, bir ayar anahtarında.
  if (state.endpointPending) return `${pendingTitle()} — ${state.error}`;
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const total = state.items.length;
  const shown = visibleRows().length;
  return shown === total
    ? `Bağlı · ${num(total)} yedek`
    : `Bağlı · ${num(shown)} / ${num(total)} yedek (süzülü)`;
}

// -------------------------------------------------------------------- veri

async function refresh() {
  nodes.tableWrap?.replaceChildren(skeletonRows(6, 7));
  nodes.status?.set('Yedek envanteri alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/backups`);
  } catch (error) {
    // Sidecar'a bile ulaşılamadı. ESKİ DURUM BANDI BIRAKILMAZ: ekranda kalan
    // "Son yedek 3 saat önce alındı ve doğrulandı" satırı artık doğrulanmamış
    // bir iddiadır ve tam da güvenilmemesi gereken anda güven verir.
    state = {
      ...state, connected: false, endpointPending: false, endpointState: 'live',
      error: error.message, items: [], loaded: true,
      summary: { has: false, known: false, tone: 'warn',
        text: 'Yedek envanteri okunamadı; son yedeğin durumu BİLİNMİYOR. '
          + 'Bu "yedek yok" demek değildir — liste alınamadı.' },
      disk: { known: false }, diskNote: '',
    };
    renderAll();
    return;
  }
  state = {
    ...state,
    items: (payload.items || []).map((row) => ({
      ...row,
      // Süzgeç MB ile çalışıyor: kullanıcı "200 MB üstü" diye düşünür, bayt diye değil.
      sizeMb: Math.round((row.bytes || 0) / (1024 * 1024)),
    })),
    connected: Boolean(payload.connected),
    endpointPending: Boolean(payload.endpointPending),
    endpointState: payload.endpointState || 'live',
    error: payload.error || '',
    summary: payload.summary || EMPTY_STATE.summary,
    disk: payload.disk || EMPTY_STATE.disk,
    diskNote: payload.diskNote || '',
    policy: payload.policy || state.policy,
    // Kapalı işlerin nedeni SUNUCUDAN gelir; iki tarafta iki ayrı cümle
    // tutmak, biri değiştiğinde ötekinin sessizce eskimesi demektir.
    features: payload.features || state.features,
    loaded: true,
  };
  renderAll();
}

// ------------------------------------------------------------------- çizim

function renderAll() {
  // Kart yeniden kurulur: ayarlar sonradan geldiyse (varsayılan kapsam), uç
  // açılıp kapandıysa ya da ucun HÂLİ değiştiyse. Sonuncusu atlanırsa "uç
  // hazır değil" ile "API kapalı" arasında geçişte etiket ve gerekçe kutusu
  // eski hâlin cümlesini göstermeye devam ederdi.
  if (!nodes.cardFromPolicy || nodes.cardCreatable !== canCreate()
      || nodes.cardState !== state.endpointState) renderNewCard();
  renderBand();
  renderDisk();
  renderTable();
  nodes.status?.set(statusText(), !state.connected);
}

/** Durum bandı — renk TEK BAŞINA anlam taşımaz, aynı bilgi yazıyla da durur. */
function renderBand() {
  const host = nodes.band;
  if (!host) return;
  host.replaceChildren();
  if (!state.connected && !state.loaded) return;

  const summary = state.summary || {};
  const tone = BAND_TONES[summary.tone] || 'warn';
  const box = h('div', `bu-band ${tone}`);

  const head = h('div', 'bu-band-head');
  // ÜÇ hâl vardır, iki değil: son yedek var · yedek yok · BİLİNMİYOR.
  // Okunamamış listeye "Yedek yok" demek, yedeği alınmış bir mağazayı
  // yedeksiz göstermek olurdu.
  const known = summary.known !== false;
  head.append(h('b', undefined,
    !known ? 'Son yedek bilinmiyor' : summary.has ? 'Son yedek' : 'Yedek yok'));
  head.append(badge(
    !known ? 'Bilinmiyor' : { good: 'Durum iyi', warn: 'Dikkat', bad: 'Sorun var' }[tone],
    tone === 'good' ? 'good' : tone,
  ));
  box.append(head);
  box.append(h('div', 'bu-band-text', summary.text || ''));

  if (summary.has) {
    box.append(kpiRow([
      { label: 'Alındı', value: stampIso(summary.createdAt),
        title: `${ago(summary.createdAt)} · ${summary.createdAt}` },
      { label: 'Yaş', value: summary.ageHours === null || summary.ageHours === undefined
        ? 'bilinmiyor' : `${num(Math.round(summary.ageHours))} saat` },
      { label: 'Boyut', value: sizeText(summary.bytes) },
      { label: 'Kapsam', value: summary.scopeLabel || '—' },
      { label: 'Doğrulama',
        value: { ok: 'Doğrulandı', bad: 'BOZUK', unknown: 'Doğrulanmadı' }[summary.verifyState]
          || '—',
        tone: VERIFY_TONES[summary.verifyState] || '' },
    ]));
  }
  host.append(box);
}

function renderDisk() {
  const host = nodes.disk;
  if (!host) return;
  const disk = state.disk || {};
  const box = h('div', 'bu-disk');

  if (disk.known) {
    // ÇUBUK YALNIZ ORAN GERÇEKTEN BİLİNİYORSA çizilir. Toplam gelmediğinde
    // %0'da duran boş bir çubuk ve "Kullanılan 0 B / 0 B" satırı, diskin
    // bomboş olduğunu söyler — elimizde o bilgi yokken.
    const ratio = Number.isFinite(disk.usedPercent) ? Math.max(0, Math.min(100, disk.usedPercent))
      : null;
    if (ratio !== null) {
      const bar = h('div', 'bu-meter');
      const fill = h('div', 'bu-meter-fill');
      fill.style.width = `${ratio}%`;
      if (ratio >= 90) fill.classList.add('bad');
      else if (ratio >= 75) fill.classList.add('warn');
      bar.append(fill);
      box.append(bar);
      box.append(h('div', 'bu-sub',
        `Kullanılan ${sizeText(disk.usedBytes)} / ${sizeText(disk.totalBytes)}`
        + ` (${percent(disk.usedPercent)}) · boş ${sizeText(disk.freeBytes)}`));
    } else {
      box.append(h('div', 'bu-sub', disk.freeBytes
        ? `Boş ${sizeText(disk.freeBytes)} · toplam alan mağazadan gelmedi, doluluk oranı yok`
        : `Toplam ${sizeText(disk.totalBytes)} · boş alan mağazadan gelmedi`));
    }
  } else {
    box.append(alertBox('Disk durumu mağazadan gelmedi.', 'warn'));
  }
  box.append(h('div', 'bu-estimate', state.diskNote || ''));
  host.replaceChildren(card('Disk', box));
}

function verifyCell(row) {
  const box = h('span', 'bu-verify');
  box.append(badge(row.verifyLabel, VERIFY_TONES[row.verifyState] || ''));
  if (row.verifiedAt) box.title = `Doğrulandığı an: ${stampIso(row.verifiedAt)}`;
  else if (row.verifyState === 'unknown') box.title = 'Bu yedek hiç doğrulanmadı.';
  return box;
}

function pathCell(row) {
  const box = h('span', 'bu-path');
  if (!row.path) {
    box.textContent = '—';
    return box;
  }
  const copy = button('Yolu kopyala', {
    variant: 'ghost',
    title: row.path,
    onClick: async (event) => {
      event.stopPropagation();
      const ok = await copyText(row.path);
      toast(ok ? 'Sunucudaki yol panoya kopyalandı.' : 'Kopyalanamadı; yol satırın ipucunda.',
        ok ? 'good' : 'warn');
    },
  });
  box.append(copy);
  return box;
}

//: Dosya adı beyaz listeden geçmeyen satırda servis HER eylemi reddeder
//: (`inventory.safe_name`, yol güvenliği). Düğmeyi açık bırakmak, basana ancak
//: hata balonuyla cevap veren ölü bir düğme demekti; sebep baştan yazılır.
//: Tablonun tepesindeki uyarı kutusu satırın hangisi olduğunu söylemiyordu.
const UNSAFE_NOTE = 'Bu yedeğin dosya adı beklenmeyen karakter içeriyor; yol güvenliği '
  + 'gereği doğrulama, indirme ve geri yükleme reddedilir.';

function actionCell(row) {
  const box = h('span', 'bu-actions');
  const unsafe = row.nameSafe === false;
  box.append(button('Doğrula', {
    disabled: unsafe,
    title: unsafe ? UNSAFE_NOTE
      : 'Sağlama toplamını (sha256) mağazaya denetletir; veriyi değiştirmez',
    onClick: (event) => { event.stopPropagation(); verifyBackup(row); },
  }));
  box.append(button('İndir', {
    disabled: unsafe,
    title: unsafe ? UNSAFE_NOTE : 'Yedeği bu makineye, rapor klasörüne indirir',
    onClick: (event) => { event.stopPropagation(); downloadBackup(row); },
  }));
  // GERİ YÜKLEME BU EKRANDAN BAŞLATILMIYOR — canlı veriyi ezer.
  //
  // Düğme daha önce iki onay kapısından geçiriyor, gerçek bir güvenlik yedeği
  // aldırıyor ve SONRA geçitten "bu uç bilerek yok" cevabını alıyordu: yani
  // yapılmayacak bir iş için disk doluyor, mağaza meşgul ediliyor ve kullanıcı
  // sonunda ham bir hata metni görüyordu. Şimdi kapalı çiziliyor ve nedeni
  // hem düğmede hem uyarı bandında yazıyor.
  box.append(blockedButton('Geri yükle', restoreReason(), { variant: 'danger' }));
  // Silme ucu yayında değilse düğme KAPALI ve nedeni ipucunda: basınca
  // sessizce patlayan bir düğme, olmayan düğmeden kötüdür.
  box.append(button('Sil', {
    variant: 'ghost',
    disabled: !state.policy.deleteAvailable || unsafe,
    title: unsafe ? UNSAFE_NOTE : state.policy.deleteAvailable
      ? `Yalnız ${state.policy.deleteMinAgeDays} günden eski yedekler silinebilir`
      : state.policy.deleteNote,
    onClick: (event) => { event.stopPropagation(); deleteBackup(row); },
  }));
  return box;
}

const COLUMNS = [
  { key: 'createdAt', label: 'Tarih', width: '150px', sortable: true,
    cell: (row) => {
      const box = h('span', 'bu-when');
      box.append(h('b', undefined, stampIso(row.createdAt)));
      box.append(h('span', 'bu-sub', ago(row.createdAt)));
      return box;
    } },
  { key: 'kindLabel', label: 'Tür', width: '92px' },
  { key: 'scopeLabel', label: 'Kapsam', width: 'minmax(0, 1.1fr)' },
  { key: 'bytes', label: 'Boyut', width: '104px', align: 'num', sortable: true,
    cell: (row) => sizeText(row.bytes) },
  { key: 'durationSeconds', label: 'Süre', width: '96px', align: 'num',
    cell: (row) => durationText(row.durationSeconds) },
  { key: 'actor', label: 'Alan kişi', width: '118px' },
  { key: 'note', label: 'Not', width: 'minmax(0, 1.3fr)',
    cell: (row) => (row.note ? clip(h('span'), row.note, 42) : '—') },
  { key: 'verifyState', label: 'Doğrulama', width: '128px', cell: verifyCell },
  { key: 'path', label: 'Yol', width: '118px', cell: pathCell },
  { key: 'actions', label: '', width: '270px', cell: actionCell },
];

/** İstemci tarafı süzme: liste onlarca satır, sunucuya gitmeye değmez. */
function visibleRows() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const rows = applyFilters(state.items, values, {
    q: { kind: 'search', fields: ['name', 'note', 'actor'] },
    verify: { kind: 'equals', field: 'verifyState' },
    kind: { kind: 'equals', field: 'kind' },
    created: { kind: 'dateRange', field: 'createdAt' },
    size: { kind: 'numRange', field: 'sizeMb' },
  });
  // Kapsam satırda LİSTEDİR; `applyFilters` eşitlik ve aralık bilir, kümeyi
  // bilmez. Tek kural için kite alan eklemek yerine burada süzülür.
  const scope = values.scope || '';
  return scope ? rows.filter((row) => (row.scope || []).includes(scope)) : rows;
}

function emptyNode() {
  if (state.endpointPending) {
    // "Hiç yedek yok" DEMEZ: liste hiç okunamadı. İkisini karıştırmak,
    // yedeği alınmış bir mağazayı yedeksiz göstermek olurdu. Başlık ucun
    // hâline göre değişir: "yok" ile "kapalı" farklı işler gerektirir.
    return emptyState({
      title: pendingTitle(),
      text: state.error,
      actions: [button('Tekrar dene', { onClick: () => refresh() })],
    });
  }
  if (!state.connected) {
    return emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı. Yedek envanteri okunamıyor.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.items.length) {
    return emptyState({
      title: 'Bu filtreye uyan yedek yok',
      text: `${num(state.items.length)} yedek var ama hiçbiri süzgece uymuyor.`,
      actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
    });
  }
  return emptyState({
    title: 'Hiç yedek yok',
    text: 'Mağazanın yedeği alınmamış. Canlı veri yedeksiz duruyor — yukarıdaki '
      + 'karttan ilk yedeği alın.',
  });
}

function renderTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();
  const rows = visibleRows();
  const broken = rows.filter((row) => row.nameSafe === false);
  if (broken.length) {
    wrap.append(alertBox(
      `${num(broken.length)} yedeğin dosya adı beklenmeyen karakter içeriyor; bu satırlarda `
      + 'doğrulama, indirme ve geri yükleme reddedilir (yol güvenliği).', 'warn'));
  }
  nodes.table = dataTable({
    columns: COLUMNS,
    rows,
    empty: emptyNode(),
    rowKey: (row) => row.name || row.createdAt,
  });
  wrap.append(nodes.table.node);
}

// --------------------------------------------------------------- yeni yedek

function scopeSelection() {
  return Object.entries(nodes.scopeBoxes || {})
    .filter(([, box]) => box.checked)
    .map(([key]) => key);
}

/** Yedek alma ucu yayında mı. `false` ise düğme KAPALI ve nedeni ekranda. */
function canCreate() {
  return state.policy.createAvailable !== false;
}

function renderNewCard() {
  const host = nodes.newCard;
  if (!host) return;
  const body = h('div', 'bu-new');
  const creatable = canCreate();

  // Uç yayında değilken kart görünür kalır (kullanıcı ekranın ne yapacağını
  // görsün) ama düğme kapalıdır ve nedeni ipucunda değil, GÖZLE GÖRÜNÜR bir
  // kutuda yazar: `title` dokunmatikte ve klavyeyle hiç görünmez.
  if (!creatable) body.append(alertBox(state.policy.endpointNote || state.error, 'warn'));

  const scopes = h('div', 'bu-scopes');
  nodes.scopeBoxes = {};
  const defaults = state.policy.defaultScope || [];
  const catalog = state.policy.scopes?.length
    ? state.policy.scopes
    : [{ key: 'database', label: 'Veritabanı' }, { key: 'uploads', label: 'Yüklenen dosyalar' },
      { key: 'config', label: 'Ayarlar' }];
  for (const item of catalog) {
    const wrap = h('label', 'bu-scope');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = defaults.includes(item.key);
    check.disabled = !creatable;
    nodes.scopeBoxes[item.key] = check;
    wrap.append(check, h('span', undefined, item.label));
    scopes.append(wrap);
  }

  const note = h('input', 'kit-input');
  note.type = 'text';
  note.maxLength = 255;
  note.disabled = !creatable;
  note.placeholder = 'Not — bu yedek neden alınıyor? (envanterde görünür)';
  nodes.note = note;

  // Kapalı düğmenin ETİKETİ de nedeni söyler: "uç hazır değil" ile "API kapalı"
  // farklı işler gerektirir ve düğme kapalıyken okunan tek şey etikettir.
  const closedLabel = state.endpointState === 'disabled'
    ? 'Yedeği başlat (mağazada API kapalı)'
    : 'Yedeği başlat (uç hazır değil)';
  const start = button(creatable ? 'Yedeği başlat' : closedLabel, {
    variant: creatable ? 'primary' : 'ghost',
    disabled: !creatable,
    title: creatable ? '' : state.policy.endpointNote,
    onClick: () => startBackup(),
  });

  const row = h('div', 'bu-new-row');
  row.append(note, start);
  body.append(scopes, row);

  host.replaceChildren(card('Yeni yedek', body,
    'Zamanlanmış yedek rotasyonuna karışmaz'));
  // Kart kurulduğunda ayarlar gelmiş miydi: gelmediyse bir dahaki veri
  // turunda yeniden kurulur, yoksa varsayılan kapsam hiç işaretlenmez.
  nodes.cardFromPolicy = Boolean(state.policy.scopes?.length);
  nodes.cardCreatable = creatable;
  nodes.cardState = state.endpointState;
}

async function startBackup() {
  const scope = scopeSelection();
  if (!scope.length) {
    toast('En az bir kapsam seçin.', 'bad');
    return;
  }
  const labels = (state.policy.scopes || [])
    .filter((item) => scope.includes(item.key)).map((item) => item.label).join(' · ');
  const reason = await askReason({
    title: 'Yeni yedek al',
    description: `Kapsam: ${labels || scope.join(', ')}. Yedek mağaza sunucusunda üretilir; `
      + 'bu işlem birkaç dakika sürebilir ve sunucuda yer kaplar.',
    confirmLabel: 'Yedeği başlat',
  });
  if (!reason) return;

  const bar = nodes.progress;
  await withBusy('Yedek alınıyor…', async () => {
    // ÇUBUK İŞ BAŞLAYINCA AÇILIR. `withBusy` dışında açılırsa, başka bir işlem
    // sürerken basılan düğme hiçbir şey yapmadan çubuğu "Dışa aktarılıyor"da
    // asılı bırakıyordu: olmayan bir işin ilerlemesi. Bu ekranın sahte
    // ilerleme yasağı (README) tam olarak bunu yasaklıyor.
    bar.node.hidden = false;
    bar.step(0);
    const created = await call(`${BASE}/backups`, {
      method: 'POST',
      body: { scope, note: nodes.note.value.trim(), reason, dryRun: false },
    });
    // "Sıkıştırılıyor" adımı mağaza tarafında aynı istek içinde geçti;
    // gözleyemediğimiz bir adımda sahte sayaç döndürmek yerine atlanır.
    bar.step(2);
    toast(created.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Yedek alındı.',
      created.dryRun ? 'warn' : 'good');

    if (created.name && !created.dryRun) {
      try {
        const verdict = await call(`${BASE}/backups/verify`, {
          method: 'POST', body: { name: created.name },
        });
        bar.done(`Yazıldı · ${verdict.label}`);
        toast(verdict.message, verdict.state === 'ok' ? 'good' : 'bad');
      } catch (error) {
        // Yedek ALINDI; yalnız doğrulama yapılamadı. İkisini aynı hata gibi
        // göstermek, alınmış bir yedeği yok saydırırdı.
        bar.done('Yazıldı · doğrulanamadı');
        toast(`Yedek alındı ama doğrulanamadı: ${error.message}`, 'warn');
      }
    } else {
      bar.done('Yazıldı');
    }
    nodes.note.value = '';
    await refresh();
  });
}

// ---------------------------------------------------------------- eylemler

async function verifyBackup(row) {
  await withBusy(`${row.name} doğrulanıyor…`, async () => {
    const verdict = await call(`${BASE}/backups/verify`, {
      method: 'POST', body: { name: row.name },
    });
    toast(verdict.message, verdict.state === 'ok' ? 'good' : 'bad');
    await refresh();
  });
}

async function downloadBackup(row) {
  const limit = state.policy.downloadLimitBytes || 0;
  if (limit && row.bytes > limit) {
    toast(`Dosya ${sizeText(row.bytes)}; buradan indirme sınırı ${sizeText(limit)}. `
      + 'Sunucudaki yolu kullanın.', 'warn');
    return;
  }
  const ok = await confirmSimple(nodes.root, {
    title: 'Yedeği indir',
    description: `${row.name} (${sizeText(row.bytes)}) bu makineye, rapor klasörüne `
      + 'yazılır. Dosya yalnız size okunur izinle (0600) kaydedilir.',
    confirmLabel: 'İndir',
  });
  if (!ok) return;
  await withBusy('Yedek indiriliyor…', async () => {
    const result = await call(`${BASE}/backups/download`, {
      method: 'POST', body: { name: row.name },
    });
    toast(`İndirildi: ${sizeText(result.bytes)}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

/**
 * Geri yüklemenin neden kapalı olduğunu söyleyen cümle.
 *
 * Sunucu söylüyorsa ONUN cümlesi kullanılır; iki tarafta iki ayrı gerekçe
 * yazmak, biri değiştiğinde ötekinin sessizce eskimesi demektir.
 */
function restoreReason() {
  return state.features?.restore?.reason
    || 'Geri yükleme Kontrol Merkezi\'nden başlatılmıyor: CANLI VERİYİ EZER. İşlem '
      + 'sunucuda, hazırlanmış geri yükleme yordamıyla yapılır.';
}

async function deleteBackup(row) {
  const reason = await askReason({
    title: 'Yedeği sil',
    description: `${row.name} sunucudan silinecek. Yalnız `
      + `${state.policy.deleteMinAgeDays || 30} günden eski yedekler silinebilir ve silme `
      + 'geri alınamaz.',
    confirmLabel: 'Sil',
  });
  if (!reason) return;
  await withBusy('Siliniyor…', async () => {
    await call(`${BASE}/backups/delete`, { method: 'POST', body: { name: row.name, reason } });
    await refresh();
  });
}

// ------------------------------------------------------------------- çıktı

function exportVisible() {
  const rows = visibleRows();
  const headers = ['Dosya', 'Tarih', 'Tür', 'Kapsam', 'Boyut (bayt)', 'Süre (sn)', 'Alan kişi',
    'Not', 'Doğrulama', 'SHA-256', 'Yol'];
  const written = csvBlob(headers, rows.map((row) => [
    row.name, row.createdAt, row.kindLabel, row.scopeLabel, row.bytes, row.durationSeconds,
    row.actor, row.note, row.verifyLabel, row.sha256, row.path,
  ]), 'yedek-envanteri');
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Envanter yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST' });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// =============================================================== işlem geçmişi

async function renderHistory(host) {
  host.replaceChildren(skeletonRows(6, 5));
  let result;
  try {
    result = await call(`${BASE}/audit?limit=200`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!result.items.length) {
    host.append(emptyState({
      title: 'Bu ekrandan henüz işlem yapılmadı',
      text: 'Yedek alma, doğrulama, indirme ve geri yükleme gerekçesiyle burada listelenir.',
    }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '160px',
        cell: (row) => stampIso(row.createdAt) },
      { key: 'action', label: 'İşlem', width: '110px' },
      { key: 'name', label: 'Yedek', width: 'minmax(0, 1.4fr)', className: 'mono' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'result', label: 'Sonuç', width: '96px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}-${row.name}`,
  });
  host.append(table.node, hintBox(
    'Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydında gerekçe alanı yok; '
    + 'ağ koparsa "ne yapmaya çalıştık" bilgisi yalnız burada kalır. "denendi" satırından '
    + 'sonra "ok" ya da "hata" gelmemişse işlem ortada kesilmiştir.'));
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel bu');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  // KALICI UYARI BANDI: kapatılamaz, kaydırmayla kaybolmaz. Bu ekranın tek
  // cümlelik özeti odur ve her açılışta yeniden okunmalıdır.
  const warning = h('div', 'bu-warning');
  warning.append(h('b', undefined, 'Canlı mağaza verisi.'),
    h('span', undefined, ' Yedek almak, doğrulamak ve indirmek buradan yapılır; '
      + 'GERİ YÜKLEME yapılmaz — o adım canlı veriyi ezer ve sunucuda yürütülür.'));

  const tabs = tabBar([
    { key: 'list', label: 'Yedekler' },
    { key: 'history', label: 'İşlem geçmişi' },
  ], 'list', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Dosya adı, not veya kişi ara', width: '240px' },
      { kind: 'select', key: 'scope', label: 'Kapsam', options: [
        { value: '', label: 'Tümü' },
        { value: 'database', label: 'Veritabanı' },
        { value: 'uploads', label: 'Yüklenen dosyalar' },
        { value: 'config', label: 'Ayarlar' },
      ] },
      { kind: 'select', key: 'kind', label: 'Tür', options: [
        { value: '', label: 'Tümü' },
        { value: 'manual', label: 'Manuel' },
        { value: 'scheduled', label: 'Zamanlanmış' },
        { value: 'safety', label: 'Güvenlik' },
      ] },
      { kind: 'select', key: 'verify', label: 'Doğrulama', options: [
        { value: '', label: 'Tümü' },
        { value: 'ok', label: 'Doğrulandı' },
        { value: 'bad', label: 'Bozuk' },
        { value: 'unknown', label: 'Doğrulanmadı' },
      ] },
      // Boş aralık: varsayılan "son 7 gün" olsaydı eski yedekler ilk açılışta
      // gizlenir ve "yedeklerim kayboldu" sanılırdı.
      { kind: 'dateRange', key: 'created', label: 'Tarih', start: '', end: '' },
      { kind: 'numRange', key: 'size', label: 'Boyut (MB)' },
    ],
    onChange: () => { renderTable(); nodes.status.set(statusText(), !state.connected); },
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Süzülmüş listeyi CSV indir', onClick: exportVisible }),
      button('⤓ Envanter', { title: 'Tüm envanteri rapor klasörüne yaz', onClick: exportAll }),
    ],
  });

  nodes.band = h('div', 'bu-band-host');
  nodes.newCard = h('div', 'bu-card-host');
  nodes.disk = h('div', 'bu-card-host');
  nodes.status = statusLine();
  nodes.tableWrap = h('div', 'bu-table');
  nodes.body = h('div', 'bu-body');

  // TEK ilerleme çubuğu, gövdenin tepesinde: hem yedek alma hem geri yükleme
  // onu kullanır. Kartın içine gömseydik geri yükleme sırasında "Yeni yedek"
  // kartında ilerleyen bir çubuk görünürdü — yanlış işi anlatan bir gösterge.
  nodes.progress = progress(STEPS);
  nodes.progress.node.hidden = true;
  nodes.live = h('div', 'bu-live');
  nodes.live.append(nodes.progress.node);

  renderNewCard();
  const listView = [nodes.live, nodes.band, nodes.newCard, nodes.disk, nodes.filters.node,
    nodes.status.node, nodes.tableWrap];
  nodes.body.append(...listView);

  view.append(warning, tabs.node, nodes.body);

  function showView(key) {
    nodes.body.replaceChildren();
    if (key === 'history') {
      renderHistory(nodes.body);
      return;
    }
    nodes.body.append(...listView);
    if (!state.loaded) refresh();
  }

  root.replaceChildren(view);
  refresh();

  return () => {
    nodes.filters?.destroy();          // arama alanı ve takvimler dinleyici tutar
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
