// Kantin Cihazları paneli — kiosk eşlemesinin yönetimi.
//
// NE YAPAR: kantindeki kiosk cihazlarını listeler, yeni kiosk kaydı açar,
// TEK KULLANIMLIK eşleme kodu üretir (istenirse kâğıda basar) ve eşlemeyi
// iptal eder.
//
// NE YAPMAZ — VE BU EKRANIN EN ÖNEMLİ SINIRIDIR:
//  · SAHADAKİ KASA TABLETİNE DOKUNMAZ. O cihaz kantinde `devices` tablosunda,
//    paylaşılan kayıt sırrıyla çalışıyor ve o akış aynen sürüyor. Bu ekranın
//    kullandığı bütün uçlar `/api/bbd_canteen_devices/*` altındadır ve kantinde
//    yalnız `kiosks` tablosuna bakar. Buradan yapılan hiçbir işlem çalışan
//    tableti etkilemez.
//  · KAYIT SİLMEZ. İptal edilen kiosk listede kalır (`revokedAt` dolar);
//    hangi cihazın ne zaman ve neden düştüğü denetimin parçasıdır.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · KOD BİR KEZ GÖRÜNÜR. Kantin kodu düz saklamıyor, yalnız sha256'sını
//    tutuyor; liste ucu kodu HİÇ döndürmez. Bu yüzden kod üretildiği anda açılan
//    pencerede gösterilir ve pencere kapanınca BİR DAHA GÖRÜLEMEZ — yenisi
//    üretilir. Ekran bunu üretim anında yazar; sonradan "kod neredeydi" diye
//    aranmasın.
//  · "ÇEVRİMDIŞI" İLE "EŞLEŞME BEKLİYOR" AYRI ŞEYLER. İlki sahaya gitmeyi,
//    ikincisi kodu götürmeyi gerektirir. Hiç eşlenmemiş kiosk arızalı değildir.
//  · ÇEVRİMİÇİ KARARI BU MAKİNENİN SAATİNE BAĞLI. Kantin "çevrimiçi mi"
//    alanı döndürmüyor, yalnız son görülme damgası veriyor; eşik Kontrol
//    Merkezi'nde hesaplanıyor. Bu yüzden rozetin yanında damganın kendisi de
//    yazılır — saat kayarsa yönetici farkı görebilsin.
//  · İPTAL İKİ PENCERE AÇAR: önce gerekçe, sonra PIN. İkisi ayrı şeydir —
//    gerekçe hesap verebilirlik (denetim kaydına yazılır), PIN kimlik (açık
//    bırakılmış bir oturumda gerekçe yazmak kimseyi durdurmaz).
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bbd_canteen_devices/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bbd_canteen_devices/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, button, confirmWithPin, confirmWithReason, copyText, h, loadStyles,
  num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, statusLine, tabBar,
} from '../../ui-kit/layout.js';

const BASE = '/api/bbd_canteen_devices';

//: Gerekçenin en az uzunluğu. Sunucu da 10 istiyor (K9 — çift kapı); buradaki
//: sınır yalnız erken geri bildirim içindir.
const MIN_REASON = 10;

const EMPTY_STATE = {
  items: [],
  summary: {},
  audit: [],
  link: { connected: true, error: '' },
  contract: { printer_available: false, pairing_ttl_minutes: 10, online_after_minutes: 5 },
  tab: 'kiosks',
};

let api = null;
let toast = null;
let busy = false;
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
    throw new Error(String(result.error));
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * Kantin düştüğünde okuma ucu `{ok:true, connected:false, error:"…"}` döner.
 * Bu bir HATA DEĞİL, bir DURUMDUR: kiosk gerçekten yok değil, ŞU AN
 * okunamıyor. Boş liste çizip susmak, "hiç kiosk yok" ile "kantine
 * ulaşılamıyor"u aynı gösterirdi.
 */
function linkOf(payload) {
  if (payload && payload.connected === false) {
    state.link = { connected: false, error: payload.error || 'Kantine ulaşılamıyor.' };
    return false;
  }
  if (payload && payload.connected === true) state.link = { connected: true, error: '' };
  return true;
}

async function withBusy(message, work) {
  if (busy) return;
  busy = true;
  nodes.status?.set(message);
  try {
    await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
  } finally {
    busy = false;
    nodes.status?.set(statusText(), !state.link.connected);
  }
}

function statusText() {
  if (!state.link.connected) return `Kantine ulaşılamıyor — ${state.link.error}`;
  const summary = state.summary || {};
  return `${num(summary.total || 0)} kiosk · ${num(summary.online || 0)} çevrimiçi`
    + ` · ${num(summary.awaiting || 0)} eşleşme bekliyor`;
}

// -------------------------------------------------------------------- veri

async function loadKiosks() {
  const payload = await api(`${BASE}/kiosks`);
  linkOf(payload);
  state.items = Array.isArray(payload?.items) ? payload.items : [];
  state.summary = payload?.summary || {};
  state.contract = {
    printer_available: Boolean(payload?.printer_available),
    pairing_ttl_minutes: Number(payload?.pairing_ttl_minutes || 10),
    online_after_minutes: Number(payload?.online_after_minutes || 5),
  };
}

async function loadAudit() {
  const payload = await api(`${BASE}/audit`);
  state.audit = Array.isArray(payload?.items) ? payload.items : [];
}

async function refresh() {
  await withBusy('Kantinden okunuyor…', async () => {
    await loadKiosks();
    if (state.tab === 'audit') await loadAudit();
    paint();
  });
}

// ------------------------------------------------------------------- çizim

function paint() {
  // Yazıcı yoksa baskı kutusu HİÇ AÇILMAZ (K7): çalışmayan bir düğme bırakmak
  // yerine yokluğu görünür olur. Karar sunucudan gelen bayrakla verilir, bu
  // yüzden çizimde — mount anında bayrak henüz okunmamıştı.
  nodes.printBox.hidden = !state.contract.printer_available || state.tab !== 'kiosks';
  if (state.tab === 'audit') paintAudit();
  else paintKiosks();
  nodes.status?.set(statusText(), !state.link.connected);
}

function paintKiosks() {
  nodes.body.replaceChildren();

  if (!state.link.connected) {
    nodes.body.append(alertBox(
      `Kantine ulaşılamıyor: ${state.link.error} — aşağıdaki liste ŞU AN okunamıyor, `
      + 'kiosk yok anlamına gelmez.', 'bad'));
  }

  const summary = state.summary || {};
  nodes.body.append(kpiRow([
    { label: 'Kiosk', value: num(summary.total || 0) },
    { label: 'Çevrimiçi', value: num(summary.online || 0), tone: 'good' },
    { label: 'Çevrimdışı', value: num(summary.offline || 0),
      tone: summary.offline ? 'warn' : '' },
    { label: 'Eşleşme bekliyor', value: num(summary.awaiting || 0),
      title: 'Kaydı açılmış ama henüz kod girmemiş cihazlar. Arıza değildir.' },
    { label: 'Canlı kod', value: num(summary.usable_codes || 0),
      title: `Üretilmiş ve henüz kullanılmamış kodlar. Her biri `
        + `${state.contract.pairing_ttl_minutes} dakika yaşar.` },
    { label: 'İptal edilmiş', value: num(summary.revoked || 0), tone: 'dim' },
  ]));

  nodes.body.append(hintBox(
    `“Çevrimiçi” kararı BU BİLGİSAYARIN saatine göre veriliyor: kantin böyle bir `
    + `alan döndürmüyor, yalnız son görülme damgası veriyor. Eşik `
    + `${state.contract.online_after_minutes} dakika; damganın kendisi her satırda yazılı.`));

  nodes.table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    empty: emptyState({
      title: 'Kayıtlı kiosk yok',
      text: 'Yeni kiosk açtığınızda ilk eşleme kodu birlikte üretilir ve bir kez gösterilir.',
      actions: [button('Yeni kiosk', { variant: 'primary', onClick: createKiosk })],
    }),
  });
  nodes.body.append(card('Kiosk cihazları', nodes.table.node,
    'Kod listede GÖSTERİLMEZ: kantin kodu düz saklamıyor. Kod yalnız üretildiği '
    + 'anda, bir kez görünür.'));
}

const COLUMNS = [
  { key: 'name', label: 'Kiosk', width: 'minmax(0, 2fr)',
    cell: (row) => {
      const box = h('div', 'cd-name');
      box.append(h('b', undefined, row.name || '—'));
      const alt = [row.platform, row.app_version].filter(Boolean).join(' · ');
      box.append(h('span', 'cd-sub', alt || 'sürüm bildirmedi'));
      return box;
    } },
  { key: 'state', label: 'Durum', width: '150px',
    cell: (row) => {
      // Renk TEK BAŞINA anlam taşımaz (kit kuralı 7): rozetin metni durumu yazar.
      if (row.awaiting_pairing) return badge('Eşleşme bekliyor', 'info');
      return badge(row.state_label, row.tone);
    } },
  { key: 'last_seen_at', label: 'Son görülme', width: '190px',
    cell: (row) => {
      if (!row.last_seen_at) return 'hiç bağlanmadı';
      const box = h('div', 'cd-seen');
      box.append(h('span', undefined, ago(row.last_seen_at)));
      // Damganın kendisi de yazılır: "5 dk önce" kararı bu makinenin saatinden
      // çıkıyor ve saat kayarsa fark ancak burada görülür.
      box.append(h('span', 'cd-sub', stampIso(row.last_seen_at)));
      return box;
    } },
  { key: 'pairing', label: 'Bekleyen kod', width: '150px',
    cell: (row) => (row.pairing?.usable
      ? badge(`${row.pairing.expires_in_minutes} dk kaldı`, 'warn')
      : (row.paired ? 'yok' : 'üretilmedi')) },
  { key: 'actions', label: '', width: '300px',
    cell: (row) => {
      const box = h('div', 'cd-actions');
      if (row.revoked) {
        // İptal edilmiş kioska kod üretilmez; düğmeyi çalışıyormuş gibi
        // bırakmak yerine nedeni yazılır.
        box.append(h('span', 'cd-sub',
          row.revoked_reason ? `İptal: ${row.revoked_reason}` : 'İptal edildi'));
        return box;
      }
      box.append(
        button('Kod üret', { onClick: () => makeCode(row) }),
        button('Ad değiştir', { variant: 'ghost', onClick: () => renameKiosk(row) }),
        button('Eşlemeyi iptal et', { variant: 'danger', onClick: () => revokeKiosk(row) }),
      );
      return box;
    } },
];

function paintAudit() {
  nodes.body.replaceChildren();
  const table = dataTable({
    columns: [
      { key: 'created_at', label: 'Zaman', width: '180px',
        cell: (row) => stampIso(row.created_at) },
      { key: 'actor', label: 'Kim', width: '160px' },
      { key: 'action', label: 'İşlem', width: '160px',
        cell: (row) => ACTION_LABELS[row.action] || row.action },
      { key: 'result', label: 'Sonuç', width: '130px',
        cell: (row) => badge(RESULT_LABELS[row.result] || row.result,
          RESULT_TONES[row.result] || '') },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows: state.audit,
    dense: true,
    empty: emptyState({ title: 'Henüz işlem yok',
      text: 'Bu ekrandan yapılan her deneme buraya yazılır ve silinmez.' }),
  });
  nodes.body.append(card('Yerel işlem izi', table.node,
    'Kantin yalnız SONUCU tutar. Yarıda kalan bir deneme — ağ koptuğunda, istek '
    + 'düştüğünde — yalnız burada görünür. Eşleme kodu buraya YAZILMAZ.'));
}

const ACTION_LABELS = {
  create_kiosk: 'Kiosk açıldı',
  rename_kiosk: 'Ad değiştirildi',
  pairing_code: 'Eşleme kodu',
  revoke_kiosk: 'Eşleme iptali',
};

const RESULT_LABELS = {
  denendi: 'Denendi', ok: 'Tamam', engellendi: 'Engellendi', hata: 'Hata',
};

const RESULT_TONES = {
  denendi: 'dim', ok: 'good', engellendi: 'warn', hata: 'bad',
};

// ------------------------------------------------------------------ eylem

async function createKiosk() {
  const name = window.prompt('Yeni kiosk adı (kantinde görünecek):', '');
  if (name === null) return;
  const trimmed = name.trim();
  if (trimmed.length < 2) {
    toast('Kiosk adı en az 2 karakter olmalı.', 'bad');
    return;
  }
  const reason = await confirmWithReason(nodes.root, {
    title: `“${trimmed}” kaydı açılsın mı?`,
    description: 'Kayıtla birlikte İLK eşleme kodu üretilir ve bir kez gösterilir. '
      + 'Gerekçe denetim kaydına yazılır.',
    confirmLabel: 'Kiosku aç',
    danger: false,
    minLength: MIN_REASON,
  });
  if (!reason) return;

  await withBusy('Kiosk açılıyor…', async () => {
    const result = await call(`${BASE}/kiosks`, {
      method: 'POST', body: { name: trimmed, reason },
    });
    await loadKiosks();
    paint();
    showCode(result?.kiosk || { name: trimmed }, result?.pairing);
  });
}

async function renameKiosk(row) {
  const name = window.prompt('Yeni ad:', row.name || '');
  if (name === null) return;
  const trimmed = name.trim();
  if (trimmed === (row.name || '')) return;

  const reason = await confirmWithReason(nodes.root, {
    title: `“${row.name}” → “${trimmed}”`,
    description: 'Yalnız ad değişir; eşleme durumu ve token etkilenmez.',
    confirmLabel: 'Adı değiştir',
    danger: false,
    minLength: MIN_REASON,
  });
  if (!reason) return;

  await withBusy('Ad değiştiriliyor…', async () => {
    await call(`${BASE}/kiosks/${row.id}`, {
      method: 'PATCH', body: { name: trimmed, reason },
    });
    toast('Ad değiştirildi.', 'good');
    await loadKiosks();
    paint();
  });
}

async function makeCode(row) {
  const reason = await confirmWithReason(nodes.root, {
    title: `“${row.name}” için yeni eşleme kodu`,
    description: `Kod ${state.contract.pairing_ttl_minutes} dakika yaşar, TEK KULLANIMLIKTIR `
      + 've bu kioskun bekleyen eski kodunu geçersiz kılar. Ekranda bir kez görünür.',
    confirmLabel: 'Kod üret',
    danger: false,
    minLength: MIN_REASON,
  });
  if (!reason) return;

  await withBusy('Kod üretiliyor…', async () => {
    const result = await call(`${BASE}/kiosks/${row.id}/pairing-code`, {
      method: 'POST',
      // Baskı, kodun düz göründüğü TEK ANDA yapılabilir: kod hiçbir yere
      // yazılmıyor, "sonra bas" diye bir uç yok.
      body: { reason, print: state.contract.printer_available && nodes.printWanted.checked },
    });
    await loadKiosks();
    paint();
    showCode(row, result?.pairing, result?.print);
  });
}

async function revokeKiosk(row) {
  // İKİ KAPI, İKİ PENCERE. Gerekçe hesap verebilirliktir ve denetim kaydına
  // yazılır; PIN kimliktir ve açık bırakılmış bir oturumu durdurur. Tek
  // pencerede toplamak, ikisini tek bir "tamam"a indirgerdi.
  const reason = await confirmWithReason(nodes.root, {
    title: `“${row.name}” eşlemesi iptal edilsin mi?`,
    description: 'Cihazın token’ı SİLİNİR ve bekleyen kodu ölür: kiosk eşleme ekranına '
      + 'düşer ve sahada yeni kod girilene kadar HİÇ çalışmaz. İptal edilen kiosk yeni '
      + 'kod da alamaz — geri dönüş yoktur, kayıt silinmez.',
    confirmLabel: 'Devam et',
    minLength: MIN_REASON,
  });
  if (!reason) return;

  const pin = await confirmWithPin(nodes.root, {
    title: 'PIN ile onaylayın',
    description: `“${row.name}” eşlemesi iptal edilecek. Yetkiniz yeterli; bu adım `
      + 'klavyenin başındaki kişinin siz olduğunuzu doğrular.',
    confirmLabel: 'Eşlemeyi iptal et',
  });
  if (!pin) return;

  await withBusy('Eşleme iptal ediliyor…', async () => {
    await call(`${BASE}/kiosks/${row.id}/revoke`, {
      method: 'POST', body: { reason, pin },
    });
    toast(`“${row.name}” eşlemesi iptal edildi.`, 'good');
    await loadKiosks();
    paint();
  });
}

/**
 * Üretilen kodu BİR KEZ gösterir.
 *
 * Pencere kapandıktan sonra kod hiçbir yerden okunamaz: kantin yalnız sha256'sını
 * sakladı, denetim izine de yazılmadı. Bu yüzden pencerede "kapatınca kaybolur"
 * cümlesi yazılı ve kopyalama düğmesi var — yönetici kodu telefona yazarken
 * pencereyi kapatmasın.
 */
function showCode(row, pairing, printed) {
  const code = pairing?.code;
  if (!code) {
    toast('Kod üretildi ama yanıtta görünmedi; listeden yeniden üretin.', 'warn');
    return;
  }

  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog cd-code-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');

  box.append(h('h3', 'kit-dialog-title', `${row.name || 'Kiosk'} — eşleme kodu`));
  // 4+4 gösterim: sekiz hane tek blok hâlinde telefonda yanlış okunuyor.
  box.append(h('div', 'cd-code', `${code.slice(0, 4)} ${code.slice(4)}`));
  box.append(h('p', 'kit-dialog-text',
    `Kod ${state.contract.pairing_ttl_minutes} dakika geçerli ve TEK KULLANIMLIK. `
    + 'Bu pencere kapandığında kod bir daha görüntülenemez — gerekirse yenisi üretilir.'));
  if (pairing.expires_at) {
    box.append(h('p', 'cd-sub', `Son geçerlilik: ${stampIso(pairing.expires_at)}`));
  }
  if (printed) {
    box.append(printed.printed
      ? alertBox('Eşleme fişi yazıcıya gönderildi.', 'good')
      : alertBox(`Fiş basılamadı: ${printed.error || 'bilinmeyen hata'} — kodu ekrandan okuyun.`,
        'warn'));
  }

  const actions = h('div', 'kit-dialog-actions');
  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  actions.append(
    button('Kodu kopyala', {
      onClick: async () => {
        toast(await copyText(code) ? 'Kod panoya kopyalandı.' : 'Kopyalanamadı.',
          'info');
      },
    }),
    button('Kapat', { variant: 'primary', onClick: close }),
  );
  box.append(actions);
  overlay.append(box);
  // Overlay panel köküne eklenir, `document.body`ye DEĞİL (kit kuralı 3).
  nodes.root.append(overlay);
  document.addEventListener('keydown', onKey);
  // Yanlışlıkla dışarı tıklayıp kodu kaybetmeyi zorlaştırmak için overlay
  // tıklaması kapatmaz: kod bir daha görünmeyecek.
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel cd');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'kiosks', label: 'Kiosklar' },
    { key: 'audit', label: 'İşlem izi' },
  ], 'kiosks', (key) => showTab(key));

  nodes.printWanted = h('input', 'kit-check');
  nodes.printWanted.type = 'checkbox';
  nodes.printWanted.id = 'cd-print-wanted';
  nodes.printBox = h('label', 'cd-print');
  nodes.printBox.htmlFor = nodes.printWanted.id;
  nodes.printBox.hidden = true;   // bayrak okunana kadar kapalı
  nodes.printBox.append(nodes.printWanted,
    h('span', undefined, 'Kodu kâğıda da bas'));

  const bar = h('div', 'cd-topbar');
  bar.append(nodes.tabs.node, h('span', 'cd-spacer'), nodes.printBox,
    button('Yenile', { onClick: refresh }),
    button('Yeni kiosk', { variant: 'primary', onClick: createKiosk }));

  nodes.status = statusLine();
  nodes.body = h('div', 'cd-body');
  view.append(bar, nodes.status.node, nodes.body);

  root.replaceChildren(view);
  showTab('kiosks');

  return () => {
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}

function showTab(key) {
  state.tab = key;
  refresh();
}
