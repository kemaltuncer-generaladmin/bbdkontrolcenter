// Talepler paneli — iade/değişim taleplerinin (RMA) tek ekranda yürütülmesi.
//
// NE YAPAR: Liste ⇄ Pano (durum sütunları) iki görünüm; SLA kalan süre sütunu
// (saat SAYISI + yazı + renk); çekmecede sipariş özeti, iade edilecek kalem
// seçimi, müşteri yazışması / iç not ayrımı, karar (Onayla → İadeler'e devret /
// Reddet); RMA formu PDF (müşteriye, kutuya konur) ve SLA raporu.
//
// NE YAPMAZ:
//  · PARA İADE ETMEZ. "Onayla" talebi onaylar ve kalemleri İadeler ekranına
//    devreder. Parayı iade etmek ayrı ekranın, ayrı iznin ve ayrı onayın işi;
//    buradan iade başlatmak, iade izni olmayan personele para iade ettirirdi.
//  · İÇ NOTU MAĞAZAYA GÖNDERMEZ. İç not yalnız Kontrol Merkezi'nde durur;
//    "internal" bayrağının müşteri portalında yanlış yorumlanması geri
//    alınamaz bir sızıntı olurdu. Yanıt ve not AYRI düğmelerdir, aynı
//    kutunun iki kipi değil.
//  · Talep SİLMEZ. Kapatma vardır; kapanan talep zincirle birlikte kalır.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Uzak RMA ucu (`/api/admin/bbd/return-requests`) henüz yayında olmayabilir;
//    ekran çökmez, "uç hazır olunca açılacak" der ve yerel notları gösterir.
//  · "Müşteri bekleniyor" durumunda SLA sayacı durur — yanıt bizde değil.
//  · SLA ve "yanıt bizde" süzgeçleri türetilmiştir; uzak uç uygulamadıysa
//    sayfa yerelde daraltılır ve ekran bunu SÖYLER.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_requests/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_requests/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, csvBlob, h, loadStyles, money, num,
  toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_requests';

const SLA_TEXT = {
  overdue: 'Gecikti',
  today: 'Bugün doluyor',
  soon: 'Yaklaşıyor',
  ok: 'Zamanında',
  paused: 'Sayaç durdu',
  done: 'Kapandı',
  none: 'Süre yok',
};

const PRIORITY_TONES = { urgent: 'bad', high: 'warn', normal: '', low: 'dim' };
const STATUS_TONES = {
  new: 'info', reviewing: 'warn', waiting_customer: 'info',
  approved: 'good', rejected: 'bad', closed: 'dim',
};

const CHIPS = [
  { key: 'overdue', label: 'Süresi geçen' },
  { key: 'today', label: 'Bugün doluyor' },
  { key: 'awaiting', label: 'Yanıt bizde bekliyor' },
];

const EMPTY_STATE = {
  items: [], total: 0, page: 1, size: 50, pages: 0,
  connected: false, error: '', narrowed: false, summary: {},
  columns: [], view: 'list', chip: null, selection: [], loaded: false,
  reference: { types: [], statuses: [], priorities: [], channels: [], assignees: [], templates: [] },
};

let api = null;
let capability = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

// ------------------------------------------------------------------ araçlar

/** Sunucu yanıtı `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

/**
 * Kaynak bırakıcıyı panel cleanup'ına takar; dönen işlev LİSTEDEN ÇIKARIR.
 *
 * Çıkarma şart: çekmece ve toplu işlem penceresi bir oturumda onlarca kez
 * açılıyor. Yalnız `push` edilseydi liste büyür, kapanmış çekmecelerin ölü
 * bırakıcıları panel kapanana kadar bellekte kalırdı.
 */
function track(fn) {
  closers.push(fn);
  return () => {
    const at = closers.indexOf(fn);
    if (at >= 0) closers.splice(at, 1);
  };
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

/** Yıkıcı ve müşteriye ulaşan her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const pages = Math.max(1, state.pages);
  const chip = state.chip ? ` · ${CHIPS.find((item) => item.key === state.chip)?.label}` : '';
  if (state.view === 'board') return `Bağlı · pano görünümü${chip}`;
  return `Bağlı · ${num(state.total)} talep · sayfa ${state.page}/${pages}${chip}`;
}

// -------------------------------------------------------------------- veri

function currentFilters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  return {
    q: values.q || '',
    type: values.type || '',
    status: values.status || '',
    priority: values.priority || '',
    assignee: values.assignee || '',
    channel: values.channel || '',
    dateField: values.dateField || 'created',
    start: range.start || '',
    end: range.end || '',
    product: values.product || '',
    sla: state.chip === 'awaiting' ? '' : (state.chip || ''),
    awaiting: state.chip === 'awaiting',
  };
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...currentFilters(), ...extra })) {
    if (value === null || value === undefined || value === '' || value === false) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refresh({ page = state.page, size = state.size } = {}) {
  if (state.view === 'board') return refreshBoard();
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 7));
  nodes.status?.set('Talepler alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/requests?${queryString({ page, size })}`);
  } catch (error) {
    // Sayaçlar ve sayfalama da SIFIRLANIR: "Mağazaya ulaşılamadı" yazarken
    // yanında bir önceki çekimden kalma "12 açık talep" durması, kullanıcıya
    // hâlâ taze veri gördüğünü söyleyen bir yalandır.
    state = {
      ...state, connected: false, error: error.message, items: [], total: 0,
      pages: 0, summary: {}, selection: [], narrowed: false,
    };
    renderKpi();
    renderList();
    nodes.pager.update({ total: 0, page: 1, size: state.size });
    nodes.status?.set(statusText(), true);
    return null;
  }
  state = {
    ...state,
    items: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    narrowed: Boolean(payload.narrowed),
    summary: payload.summary || {},
    selection: [],
    loaded: true,
  };
  renderKpi();
  renderList();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.status?.set(statusText(), !state.connected);
  return payload;
}

async function refreshBoard() {
  nodes.boardWrap?.replaceChildren(skeletonRows(6, 6));
  nodes.status?.set('Pano alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/board?${queryString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, columns: [], summary: {} };
    renderKpi();
    renderBoard();
    nodes.status?.set(statusText(), true);
    return null;
  }
  state = {
    ...state,
    columns: payload.columns || [],
    connected: Boolean(payload.connected),
    error: payload.error || '',
    summary: payload.summary || {},
    loaded: true,
  };
  renderKpi();
  renderBoard();
  if (payload.warnings && payload.warnings.length) {
    toast(`Bazı sütunlar okunamadı: ${payload.warnings.join(' · ')}`, 'warn');
  }
  nodes.status?.set(statusText(), !state.connected);
  return payload;
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    // Referans listeler gelmezse süzgeçler boş kalır ama ekran çalışır (K7).
    return;
  }
  state.reference = {
    types: payload.types || [],
    statuses: payload.statuses || [],
    priorities: payload.priorities || [],
    channels: payload.channels || [],
    assignees: payload.assignees || [],
    templates: payload.templates || [],
  };
  const options = (label, items, map) => [{ value: '', label }, ...items.map(map)];
  nodes.filters.options('type', options('Tümü — tür', state.reference.types,
    (item) => ({ value: item.value, label: item.label })));
  nodes.filters.options('status', options('Tümü — durum', state.reference.statuses,
    (item) => ({ value: item.value, label: item.label })));
  nodes.filters.options('priority', options('Tümü — öncelik', state.reference.priorities,
    (item) => ({ value: item.value, label: item.label })));
  nodes.filters.options('channel', options('Tümü — kanal', state.reference.channels,
    (item) => ({ value: item.value, label: item.label })));
  nodes.filters.options('assignee', options('Tümü — atanan', state.reference.assignees,
    (item) => ({ value: item.name, label: item.name })));
  if (!payload.connected) {
    toast('Atanan kişi listesi mağazadan alınamadı; atama açılırı boş.', 'warn');
  }
}

// ------------------------------------------------------------------- çizim

function renderKpi() {
  if (!nodes.kpi) return;
  const summary = state.summary || {};
  const value = (key) => (summary[key] === undefined ? '—' : num(summary[key]));
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Görünen talep', value: value('total') },
    { label: 'Açık', value: value('open') },
    { label: 'Süresi geçen', value: value('overdue'), tone: 'bad' },
    { label: 'Bugün doluyor', value: value('today'), tone: 'warn' },
    { label: 'Yanıt bizde', value: value('unanswered'), tone: 'warn' },
    { label: 'Müşteri bekleniyor', value: value('paused'), tone: 'muted' },
  ]));
}

/** SLA hücresi: SAYI + YAZI + renk. Renk tek başına anlam taşımaz. */
function slaCell(row) {
  const sla = row.sla || {};
  const box = h('span', 'rq-sla');
  const label = SLA_TEXT[sla.state] || '—';
  if (sla.hoursLeft === null || sla.hoursLeft === undefined) {
    box.append(h('b', 'rq-dim', '—'), badge(label, 'dim'));
  } else {
    box.append(h('b', sla.state === 'overdue' ? 'rq-bad' : '', `${num(sla.hoursLeft, 1)} sa`));
    box.append(badge(label, sla.tone || ''));
  }
  if (sla.label) box.title = `${sla.label}${sla.dueAt ? ` · bitiş ${sla.dueAt}` : ''}`;
  return box;
}

const COLUMNS = [
  { key: 'code', label: 'Talep no', width: '110px', className: 'mono', sortable: true },
  {
    key: 'createdAt',
    label: 'Açılış',
    width: '128px',
    sortable: true,
    cell: (row) => row.createdAt || '—',
  },
  { key: 'typeLabel', label: 'Tür', width: '92px' },
  {
    key: 'subject',
    label: 'Konu',
    width: 'minmax(0, 2.4fr)',
    cell: (row) => {
      const box = h('span', 'rq-subject');
      box.append(clip(h('b'), row.subject, 52));
      const sub = h('span', 'rq-sub', row.channelLabel || '—');
      if (row.awaitingUs === true) sub.append(badge('yanıt bizde', 'warn'));
      box.append(sub);
      return box;
    },
  },
  { key: 'customerName', label: 'Müşteri', width: 'minmax(0, 1.4fr)' },
  {
    key: 'orderNumber',
    label: 'Sipariş',
    width: '110px',
    className: 'mono',
    cell: (row) => row.orderNumber || (row.orderId ? `#${row.orderId}` : '—'),
  },
  {
    key: 'priority',
    label: 'Öncelik',
    width: '92px',
    cell: (row) => badge(row.priorityLabel, PRIORITY_TONES[row.priority] ?? ''),
  },
  { key: 'assignee', label: 'Atanan', width: '128px', cell: (row) => row.assignee || '—' },
  { key: 'updatedAt', label: 'Son hareket', width: '128px', cell: (row) => row.updatedAt || '—' },
  {
    key: 'sla',
    label: 'SLA kalan',
    width: '166px',
    cell: slaCell,
    title: 'Kalan saat + durum. “Müşteri bekleniyor” durumunda sayaç durur.',
  },
  {
    key: 'status',
    label: 'Durum',
    width: '128px',
    cell: (row) => badge(row.statusLabel, STATUS_TONES[row.status] ?? ''),
  },
];

function emptyNode() {
  if (!state.connected) {
    return emptyState({
      title: 'Talepler okunamadı',
      text: `${state.error || 'Mağazaya ulaşılamadı.'} — Talep ucu (bbd/return-requests) `
        + 'mağazada yayınlanınca liste kendiliğinden dolacak. İç notlarınız yerelde duruyor.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refresh() })],
    });
  }
  if (state.chip) {
    return emptyState({
      title: 'Bu başlıkta talep yok',
      text: 'Seçili çipe uyan talep bulunamadı — bu başlıkta biriken iş yok.',
      actions: [button('Çipi kaldır', {
        onClick: () => { nodes.chips.set(null); applyChip(null); },
      })],
    });
  }
  return emptyState({
    title: 'Bu filtreye uyan talep yok',
    text: `${num(state.total)} kayıt döndü. Süzgeçleri gevşetin ya da temizleyin.`,
    actions: [button('Filtreyi temizle', { onClick: () => nodes.filters.reset() })],
  });
}

function renderList() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.narrowed) {
    wrap.append(alertBox(
      'SLA / “yanıt bizde” süzgeci mağaza tarafında uygulanmadı; bu SAYFA Kontrol '
      + 'Merkezi’nde daraltıldı. Sayfa sayısı ve toplam, daraltmadan önceki listeye aittir.',
      'warn',
    ));
  }

  nodes.table = dataTable({
    columns: COLUMNS,
    rows: state.items,
    selectable: true,
    empty: emptyNode(),
    onRow: (row) => openRequest(row.id),
    onSelect: (ids) => { state.selection = ids; renderSelectionBar(); },
  });
  wrap.append(nodes.table.node);
  renderSelectionBar();
}

function renderBoard() {
  const wrap = nodes.boardWrap;
  if (!wrap) return;
  wrap.replaceChildren();
  if (!state.connected) {
    wrap.append(emptyNode());
    return;
  }
  const board = h('div', 'rq-board');
  for (const column of state.columns) {
    const box = h('div', 'rq-col');
    const head = h('div', 'rq-col-head');
    head.append(h('b', undefined, column.label));
    // Başlıktaki sayı GERÇEK toplamdır; sütunda o kadar kart olmayabilir.
    head.append(badge(num(column.total), column.overdue ? 'warn' : 'dim'));
    if (column.overdue) head.append(badge(`${num(column.overdue)} gecikmiş`, 'bad'));
    box.append(head);

    if (!column.cards.length) {
      box.append(h('div', 'rq-col-empty', 'Bu sütunda talep yok.'));
    }
    for (const row of column.cards) {
      const item = h('button', 'rq-card');
      item.type = 'button';
      item.addEventListener('click', () => openRequest(row.id));
      const top = h('div', 'rq-card-top');
      top.append(h('code', 'rq-code', row.code),
        badge(row.priorityLabel, PRIORITY_TONES[row.priority] ?? ''));
      item.append(top);
      item.append(clip(h('div', 'rq-card-subject'), row.subject, 64));
      const foot = h('div', 'rq-card-foot');
      foot.append(h('span', 'rq-sub', row.customerName), slaCell(row));
      item.append(foot);
      box.append(item);
    }
    if (column.total > column.shown) {
      box.append(h('div', 'rq-col-more',
        `…ve ${num(column.total - column.shown)} talep daha. Tümü için Liste görünümü.`));
    }
    board.append(box);
  }
  wrap.append(board);
}

function renderSelectionBar() {
  const bar = nodes.selbar;
  if (!bar) return;
  bar.replaceChildren();
  const count = state.selection.length;
  if (!count) {
    bar.classList.remove('on');
    return;
  }
  bar.classList.add('on');
  bar.append(h('b', undefined, `${num(count)} talep seçildi`));
  bar.append(h('span', 'kit-spacer'));
  bar.append(
    button('Durum değiştir', { onClick: () => bulkDialog('status') }),
    button('Ata', { onClick: () => bulkDialog('assign') }),
    button('Kapat', { variant: 'danger', onClick: () => bulkDialog('close') }),
    button('Seçimi bırak', {
      variant: 'ghost',
      onClick: () => {
        nodes.table?.clearSelection();
        state.selection = [];
        renderSelectionBar();
      },
    }),
  );
}

function applyChip(key) {
  state.chip = key;
  refresh({ page: 1 });
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['Talep no', 'Açılış', 'Tür', 'Konu', 'Müşteri', 'Sipariş', 'Öncelik',
    'Atanan', 'Son hareket', 'SLA kalan (saat)', 'Durum'];
  const rows = state.items.map((row) => [
    row.code, row.createdAt, row.typeLabel, row.subject, row.customerName,
    row.orderNumber || (row.orderId ? `#${row.orderId}` : ''), row.priorityLabel,
    row.assignee || '', row.updatedAt,
    row.sla && row.sla.hoursLeft !== null && row.sla.hoursLeft !== undefined
      ? num(row.sla.hoursLeft, 1) : '',
    row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `talepler-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm kayıtları dışa aktar',
    description: `${num(state.total)} talep mağazadan sayfa sayfa çekilir ve rapor klasörüne `
      + 'CSV olarak yazılır. Birkaç dakika sürebilir.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  await withBusy('Talepler taranıyor…', async () => {
    const filters = currentFilters();
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: {
        q: filters.q, type: filters.type, status: filters.status, priority: filters.priority,
        assignee: filters.assignee, channel: filters.channel,
        start: filters.start, end: filters.end,
      },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Liste tavana dayandı; dosya eksik olabilir.', 'warn');
  });
}

// ================================================================= çekmece

async function openRequest(requestId) {
  // Çekmecenin kendi kaynakları (formGrid → tarih alanları global dinleyici
  // tutar) çekmece kapanınca bırakılır; panel cleanup'ına bırakılırsa her
  // açılışta bir tane daha birikir.
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const untrack = track(dropForms);
  const box = drawer(nodes.root, {
    title: 'Talep yükleniyor…',
    subtitle: `#${requestId}`,
    onClose: () => { dropForms(); untrack(); },
  });
  box.body.append(skeletonRows(6, 3));

  let payload;
  try {
    payload = await api(`${BASE}/requests/${requestId}`);
  } catch (error) {
    box.body.replaceChildren(emptyState({
      title: 'Talep okunamadı',
      text: error.message,
      actions: [button('Kapat', { onClick: box.close })],
    }));
    return;
  }

  if (payload.ok === false) {
    // Uzak kayıt gelmedi ama YEREL notlar duruyor: personel en azından bu
    // talebe ne yaptığımızı görür.
    box.body.replaceChildren(alertBox(
      `${payload.error} — Talep ucu mağazada yayınlanınca künye burada açılacak.`, 'warn'));
    if ((payload.notes || []).length) {
      const list = h('div', 'rq-thread');
      for (const note of payload.notes) list.append(messageNode({ ...note, side: 'internal' }));
      box.body.append(card('Yerel iç notlar', list));
    }
    box.body.append(button('Kapat', { onClick: box.close }));
    return;
  }

  const request = payload.request;
  box.setTitle(`${request.code} · ${request.subject}`);
  box.body.replaceChildren();

  const head = h('div', 'rq-drawer-head');
  head.append(
    badge(request.statusLabel, STATUS_TONES[request.status] ?? ''),
    badge(request.typeLabel, 'info'),
    badge(request.priorityLabel, PRIORITY_TONES[request.priority] ?? ''),
    slaCell(request),
    h('span', 'kit-spacer'),
    button('RMA formu', {
      title: 'Müşteriye verilecek/kutuya konacak form — PDF üretir ve yazdırır',
      onClick: () => report.run('rma', { requestId: request.id }),
    }),
  );

  const tabs = tabBar([
    { key: 'summary', label: 'Özet' },
    { key: 'items', label: 'İade kalemleri' },
    { key: 'thread', label: 'Yazışma' },
    { key: 'history', label: 'İşlem geçmişi' },
  ], 'summary', (key) => paint(key));
  tabs.badge('thread', payload.thread.length || undefined);
  tabs.badge('items', payload.items.filter((item) => item.qty > 0).length || undefined);

  const pane = h('div', 'rq-pane');
  box.body.append(head, tabs.node, pane);
  if (payload.warnings && payload.warnings.length) {
    box.body.insertBefore(
      alertBox(`Bazı parçalar okunamadı — ${payload.warnings.join(' · ')}`, 'warn'), pane);
  }

  const reload = async () => {
    box.close();
    await refresh();
    openRequest(requestId);
  };

  function paint(key) {
    dropForms();
    pane.replaceChildren();
    ({
      summary: paintSummary, items: paintItems, thread: paintThread, history: paintHistory,
    })[key]?.(pane, payload, forms, reload);
  }
  paint('summary');
}

function messageNode(message) {
  const box = h('div', `rq-msg ${message.side}`);
  const head = h('div', 'rq-msg-head');
  const who = {
    customer: 'Müşteri mesajı', internal: 'İÇ NOT — müşteri görmez', staff: 'Bizim yanıtımız',
  }[message.side] || 'Mesaj';
  head.append(h('b', undefined, message.author || '—'), badge(who,
    { customer: 'info', internal: 'warn', staff: 'good' }[message.side] || ''));
  if (message.local) head.append(badge('yerel', 'dim'));
  head.append(h('span', 'kit-spacer'), h('span', 'rq-sub', message.createdAt || '—'));
  box.append(head, h('div', 'rq-msg-body', message.body || ''));
  for (const link of message.attachments || []) {
    const anchor = h('a', 'rq-attach', 'Ek görsel');
    anchor.href = link;
    anchor.target = '_blank';
    anchor.rel = 'noreferrer';
    box.append(anchor);
  }
  return box;
}

// ------------------------------------------------------------------- özet

function paintSummary(pane, payload, forms, reload) {
  const request = payload.request;
  const facts = h('div', 'rq-facts');
  const line = (label, value) => {
    const row = h('div', 'rq-fact');
    row.append(h('span', 'rq-sub', label), h('b', undefined, value || '—'));
    facts.append(row);
  };
  line('Talep no', request.code);
  line('Açılış', request.createdAt);
  line('Son hareket', request.updatedAt);
  line('Kanal', request.channelLabel);
  line('Müşteri', `${request.customerName}${request.customerEmail ? ` · ${request.customerEmail}` : ''}`);
  line('Kargo geri gönderim kodu', request.returnCode);
  line('SLA', request.sla?.label);

  pane.append(card('Künye', facts));
  pane.append(orderCard(payload));
  customerCard(pane, request);

  // ------------------------------------------------------------ düzenleme
  const form = formGrid({
    fields: [
      {
        key: 'status',
        label: 'Durum',
        type: 'select',
        options: state.reference.statuses.map((item) => ({ value: item.value, label: item.label })),
      },
      {
        key: 'priority',
        label: 'Öncelik',
        type: 'select',
        options: state.reference.priorities.map((item) => ({ value: item.value, label: item.label })),
        hint: 'SLA süresi önceliğe göre hesaplanır.',
      },
      {
        key: 'assignee',
        label: 'Atanan',
        type: 'select',
        // Talebin ATANDIĞI kişi listede yoksa (mağazada silinmiş yönetici,
        // ya da atanan listesi hiç gelmemiş) seçenek elle eklenir. Aksi hâlde
        // açılır "(atanmamış)" gösterir; talep aslında atanmışken ekran
        // atanmamış diye YALAN söyler ve kayıt farkında olmadan sıfırlanır.
        options: assigneeOptions(request.assignee),
      },
    ],
    value: {
      status: request.status, priority: request.priority, assignee: request.assignee || '',
    },
  });
  forms.push(form);

  const actions = h('div', 'rq-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      const patch = form.patch();
      if (!Object.keys(patch).length) { toast('Değişen alan yok.', 'warn'); return; }
      const reason = await askReason({
        title: 'Talebi güncelle',
        description: `${request.code} · ${Object.keys(patch).join(', ')} değişecek. Gerekçe `
          + 'denetim kaydına yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Kaydediliyor…', async () => {
        await call(`${BASE}/requests/${request.id}/update`, {
          method: 'POST', body: { ...patch, reason, dryRun: false },
        });
        toast('Talep güncellendi.', 'good');
        reload();
      });
    },
  }));
  pane.append(card('Durum · öncelik · atama', form.node), actions);

  // ---------------------------------------------------------------- karar
  const decision = h('div', 'rq-actions');
  decision.append(
    button('Onayla → İadeler’e devret', {
      variant: 'primary',
      onClick: () => decide(payload, true, reload),
    }),
    button('Reddet', { variant: 'danger', onClick: () => decide(payload, false, reload) }),
  );
  const handed = payload.handoff || [];
  pane.append(card('Karar', decision,
    handed.length ? `Devredildi: ${handed[0].createdAt} · ${money(handed[0].amount)}` : ''));
  if (handed.length) {
    pane.append(alertBox(
      `Bu talep ${handed[0].createdAt} tarihinde ${money(handed[0].amount)} tutarıyla İadeler `
      + `ekranına devredildi (${handed[0].actor}). Paranın iadesi orada, ayrı izinle yapılır.`,
      'good'));
  }
  pane.append(hintBox('“Onayla” PARA İADE ETMEZ: talebi onaylar ve seçili kalemleri İadeler '
    + 'ekranına devreder. Parayı iade etmek ayrı ekranın, ayrı iznin ve ayrı onayın işidir.'));
}

/** Atanan açılırının seçenekleri; `current` listede yoksa başa eklenir. */
function assigneeOptions(current) {
  const known = state.reference.assignees.map((item) => ({ value: item.name, label: item.name }));
  const options = [{ value: '', label: '(atanmamış)' }, ...known];
  if (current && !known.some((item) => item.value === current)) {
    options.splice(1, 0, { value: current, label: `${current} (listede yok)` });
  }
  return options;
}

function orderCard(payload) {
  const order = payload.order || {};
  const request = payload.request;
  if (!request.orderId) {
    return card('Sipariş', h('div', 'rq-sub', 'Bu talep bir siparişe bağlı değil.'));
  }
  const box = h('div', 'rq-facts');
  const line = (label, value) => {
    const row = h('div', 'rq-fact');
    row.append(h('span', 'rq-sub', label), h('b', undefined, value || '—'));
    box.append(row);
  };
  line('Sipariş', order.number || `#${request.orderId}`);
  line('Tarih', order.createdAt);
  line('Durum', order.statusLabel || order.status);
  line('Tutar', order.total === null || order.total === undefined ? '—' : money(order.total));
  line('Kargo', order.shipping);

  // `store.order.card` yeteneği varsa özet oradan TAZELENİR; yoksa yukarıdaki
  // alanlar (geçitten gelen sipariş kaydı) gösterilmeye devam eder.
  const provider = capability ? capability('store.order.card') : null;
  if (provider) {
    provider(request.orderId).then((extra) => {
      const summary = extra?.order || extra;
      if (summary && summary.statusLabel) line('Siparişler ekranı', summary.statusLabel);
    }).catch(() => { /* yetenek patlarsa özet olduğu gibi kalır (K7) */ });
  }
  return card('Sipariş özeti', box);
}

function customerCard(pane, request) {
  const provider = capability ? capability('store.customer.card') : null;
  if (!provider || !request.customerId) return;   // yetenek yoksa bölüm hiç çizilmez
  const box = h('div', 'rq-facts');
  const host = card('Müşteri künyesi', box, 'Müşteriler ekranından');
  pane.append(host);
  provider(request.customerId).then((payload) => {
    const customer = payload?.customer || payload || {};
    for (const [label, value] of [
      ['Grup', customer.groupName], ['Telefon', customer.phone],
      ['Sipariş', customer.orderCount], ['Toplam harcama',
        customer.totalSpent === undefined ? undefined : money(customer.totalSpent)],
    ]) {
      if (value === undefined || value === null || value === '') continue;
      const row = h('div', 'rq-fact');
      row.append(h('span', 'rq-sub', label), h('b', undefined, String(value)));
      box.append(row);
    }
    if (!box.childElementCount) host.remove();
  }).catch(() => host.remove());
}

async function decide(payload, approve, reload) {
  const request = payload.request;
  const chosen = payload.items.filter((item) => item.qty > 0);
  const total = chosen.reduce((sum, item) => sum + item.qty * item.unitPrice, 0);
  const reason = await askReason({
    title: approve ? 'Talebi onayla' : 'Talebi reddet',
    description: approve
      ? `${request.code} onaylanacak ve ${num(chosen.length)} kalem (${money(total)}) İadeler `
        + 'ekranına devredilecek. PARA BURADAN İADE EDİLMEZ; iade orada ayrı izinle yapılır. '
        + 'Gerekçe müşteriye görünen karar metnine ve denetim kaydına yazılır.'
      : `${request.code} reddedilecek. Gerekçe müşteriye görünen karar metnine ve denetim `
        + 'kaydına yazılır; kısa yazmayın.',
    confirmLabel: approve ? 'Onayla ve devret' : 'Reddet',
  });
  if (!reason) return;
  await withBusy(approve ? 'Onaylanıyor…' : 'Reddediliyor…', async () => {
    const result = await call(`${BASE}/requests/${request.id}/${approve ? 'approve' : 'reject'}`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast(approve
      ? `Onaylandı${result.handedOff ? ` · ${money(result.estimate.amount)} İadeler’e devredildi` : ''}`
      : 'Talep reddedildi.', 'good');
    if (approve && !result.handedOff) {
      toast('Devredilecek kalem yoktu; İadeler’e kayıt açılmadı.', 'warn');
    }
    reload();
  });
}

// --------------------------------------------------------------- kalemler

function paintItems(pane, payload, forms, reload) {
  const request = payload.request;
  if (!payload.items.length) {
    pane.append(emptyState({
      title: 'Kalem listesi yok',
      text: request.orderId
        ? 'Siparişin kalemleri okunamadı; seçim yapılamaz. Sipariş kaydı gelince liste dolar.'
        : 'Bu talep bir siparişe bağlı değil; iade edilecek kalem seçilemez.',
    }));
    return;
  }

  const selection = {};
  for (const item of payload.items) selection[item.itemId] = item.qty || 0;
  const totalNode = h('div', 'rq-total');

  const paintTotal = () => {
    let amount = 0;
    let count = 0;
    for (const item of payload.items) {
      const qty = selection[item.itemId] || 0;
      amount += qty * item.unitPrice;
      count += qty;
    }
    totalNode.replaceChildren(
      h('span', 'rq-sub', 'Seçilen: '),
      h('b', undefined, `${num(count)} adet · ${money(amount)}`),
      h('span', 'rq-sub', ' — kargo, kupon payı ve KDV düzeltmesi İadeler ekranında hesaplanır.'),
    );
  };

  const table = dataTable({
    columns: [
      { key: 'sku', label: 'SKU', width: 'minmax(0, 1fr)', className: 'mono' },
      { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
      { key: 'qtyOrdered', label: 'Sipariş', width: '80px', align: 'num' },
      {
        key: 'qtyReturned',
        label: 'İade edilmiş',
        width: '110px',
        align: 'num',
        title: 'Daha önce iade/para iadesi yapılmış adet; iade edilebilir adetten düşülür.',
      },
      {
        key: 'unitPrice', label: 'Birim', width: '110px', align: 'num',
        cell: (row) => money(row.unitPrice),
      },
      {
        key: 'qty',
        label: 'İade edilecek',
        width: '130px',
        align: 'num',
        cell: (row) => {
          if (!row.maxQty) return badge('iade edilemez', 'dim');
          const input = h('input', 'kit-input rq-qty');
          input.type = 'number';
          input.min = '0';
          input.max = String(row.maxQty);
          input.value = String(selection[row.itemId] || 0);
          input.title = `En çok ${row.maxQty} adet`;
          input.addEventListener('input', () => {
            const wanted = Math.max(0, Math.min(row.maxQty, Number(input.value) || 0));
            selection[row.itemId] = wanted;
            input.classList.toggle('bad', Number(input.value) > row.maxQty);
            paintTotal();
          });
          return input;
        },
      },
    ],
    rows: payload.items,
    dense: true,
    rowKey: (row) => String(row.itemId),
  });
  paintTotal();

  const actions = h('div', 'rq-actions');
  actions.append(button('Kalemleri kaydet', {
    variant: 'primary',
    onClick: async () => {
      const chosen = {};
      for (const [key, value] of Object.entries(selection)) if (value > 0) chosen[key] = value;
      if (!Object.keys(chosen).length) { toast('Kalem seçilmedi.', 'warn'); return; }
      const reason = await askReason({
        title: 'İade edilecek kalemleri kaydet',
        description: `${request.code} · ${Object.keys(chosen).length} kalem. Adet MUTLAKTIR ve `
          + 'sipariş adedine karşı doğrulanır; daha önce iade edilmiş adet düşülür.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Kalemler yazılıyor…', async () => {
        const result = await call(`${BASE}/requests/${request.id}/items`, {
          method: 'POST', body: { selection: chosen, reason, dryRun: false },
        });
        toast(`Kaydedildi · tahmini ${money(result.estimate.amount)}`, 'good');
        reload();
      });
    },
  }));

  pane.append(table.node, totalNode, actions,
    hintBox('Adet sipariş adedini AŞAMAZ ve daha önce iade edilen düşülür: iki kez iade, '
      + 'mağazaya iki kez para iade ettirir ve stoğu şişirir.'));
}

// --------------------------------------------------------------- yazışma

function paintThread(pane, payload, forms, reload) {
  const request = payload.request;
  const list = h('div', 'rq-thread');
  if (!payload.thread.length) {
    list.append(h('div', 'rq-sub', 'Henüz mesaj yok.'));
  }
  for (const message of payload.thread) list.append(messageNode(message));
  pane.append(card('Zincir', list));

  // ------------------------------------------------------- müşteriye yanıt
  const reply = h('textarea', 'kit-textarea rq-editor');
  reply.placeholder = 'Müşteriye gidecek yanıt…';
  reply.maxLength = 4000;

  const templates = h('select', 'kit-select');
  const first = h('option', undefined, 'Şablon seç…');
  first.value = '';
  templates.append(first);
  for (const [index, item] of (payload.templates || []).entries()) {
    const option = h('option', undefined, item.title);
    option.value = String(index);
    templates.append(option);
  }
  templates.addEventListener('change', () => {
    const item = (payload.templates || [])[Number(templates.value)];
    if (!item) return;
    // Şablon METNİ EZMEZ, sonuna eklenir: yazılmış metni sessizce silmek
    // kullanıcının emeğini yok eder.
    reply.value = reply.value ? `${reply.value.trimEnd()}\n\n${item.body}` : item.body;
    templates.value = '';
  });

  const closeAfter = h('input', 'kit-check');
  closeAfter.type = 'checkbox';
  const closeLabel = h('label', 'rq-inline');
  closeLabel.append(closeAfter, h('span', undefined, 'Yanıttan sonra “Müşteri bekleniyor” yap'));

  const replyActions = h('div', 'rq-actions');
  replyActions.append(templates, closeLabel, h('span', 'kit-spacer'), button('Müşteriye gönder', {
    variant: 'primary',
    onClick: async () => {
      const body = reply.value.trim();
      if (body.length < 2) { toast('Yanıt boş olamaz.', 'bad'); return; }
      const reason = await askReason({
        title: 'Müşteriye yanıt gönder',
        description: `${request.code} · bu metin MÜŞTERİYE gider ve geri alınamaz. Gerekçe `
          + 'denetim kaydına yazılır.',
        confirmLabel: 'Gönder',
      });
      if (!reason) return;
      await withBusy('Yanıt gönderiliyor…', async () => {
        await call(`${BASE}/requests/${request.id}/reply`, {
          method: 'POST',
          body: {
            body, reason, dryRun: false,
            status: closeAfter.checked ? 'waiting_customer' : '',
          },
        });
        toast('Yanıt gönderildi.', 'good');
        reload();
      });
    },
  }));
  const replyBox = h('div', 'rq-editor-box');
  replyBox.append(reply, replyActions);
  pane.append(card('Müşteriye yanıt', replyBox, 'Mağazaya gider · geri alınamaz'));

  // ------------------------------------------------------------- iç not
  const note = h('textarea', 'kit-textarea rq-editor');
  note.placeholder = 'Yalnız personelin göreceği not…';
  note.maxLength = 4000;
  const noteActions = h('div', 'rq-actions');
  noteActions.append(button('İç not ekle', {
    onClick: async () => {
      const body = note.value.trim();
      if (body.length < 2) { toast('Not boş olamaz.', 'bad'); return; }
      await withBusy('Not yazılıyor…', async () => {
        await call(`${BASE}/requests/${request.id}/note`, { method: 'POST', body: { body } });
        toast('İç not eklendi (yalnız Kontrol Merkezi’nde).', 'good');
        reload();
      });
    },
  }));
  const noteBox = h('div', 'rq-editor-box');
  noteBox.append(note, noteActions);
  pane.append(card('İç not', noteBox, 'Mağazaya GİTMEZ · müşteri görmez'));
  pane.append(hintBox('Yanıt ve iç not AYRI uçlardır, aynı kutunun iki kipi değil: tek '
    + 'bayrak hatası personel yazışmasını müşteriye gösterebilirdi. İç not Kontrol '
    + 'Merkezi’nde kalır ve mağazaya hiç gönderilmez.'));
}

// ------------------------------------------------------------- işlem geçmişi

async function paintHistory(pane, payload) {
  pane.append(skeletonRows(4, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?requestId=${payload.request.id}&limit=50`);
  } catch (error) {
    pane.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  pane.replaceChildren();
  if (!result.items.length) {
    pane.append(emptyState({
      title: 'Bu talebe bu ekrandan dokunulmadı',
      text: 'Kontrol Merkezi üzerinden yapılan her yazma gerekçesiyle burada listelenir.',
    }));
  } else {
    pane.append(dataTable({
      columns: [
        { key: 'createdAt', label: 'Zaman', width: '150px' },
        { key: 'action', label: 'İşlem', width: '140px' },
        { key: 'actor', label: 'Kim', width: '130px' },
        { key: 'result', label: 'Sonuç', width: '90px' },
        { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
      ],
      rows: result.items,
      dense: true,
      rowKey: (row) => `${row.createdAt}-${row.action}`,
    }).node);
  }

  // Mağaza tarafındaki denetim kaydı ayrı bir modülün yeteneğidir; yoksa
  // bölüm hiç çizilmez ve ekran çalışmaya devam eder (K7).
  const provider = capability ? capability('store.audit.for') : null;
  if (provider) {
    const slot = h('div');
    slot.append(skeletonRows(3, 3));
    const host = card('Mağaza denetim kaydı', slot, 'UDİT ekranından');
    pane.append(host);
    provider('return_request', payload.request.id).then((remote) => {
      const rows = remote?.items || [];
      slot.replaceChildren(rows.length ? dataTable({
        columns: [
          { key: 'createdAt', label: 'Zaman', width: '150px' },
          { key: 'action', label: 'İşlem', width: '160px' },
          { key: 'actor', label: 'Kim', width: '140px' },
        ],
        rows,
        dense: true,
        rowKey: (row) => String(row.id ?? row.createdAt),
      }).node : h('div', 'rq-sub', 'Mağaza tarafında kayıt yok.'));
    }).catch(() => host.remove());
  }

  pane.append(hintBox('Bu iz YERELDİR ve gerekçeyi tutar. Mağazanın kendi denetim kaydı '
    + 'gerekçe alanı taşımıyor; ağ koparsa “ne yapmaya çalıştık” bilgisi yalnız burada kalır.'));
}

// ============================================================= toplu işlem

function bulkDialog(action) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog rq-bulk');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');

  const titles = {
    status: 'Toplu durum değiştirme',
    assign: 'Toplu atama',
    close: 'Toplu kapatma',
  };
  box.append(h('h3', 'kit-dialog-title', titles[action]));
  box.append(h('p', 'kit-dialog-text',
    `${num(state.selection.length)} talep seçili. Mağazada TOPLU UÇ YOK: her talep ayrı `
    + 'istekle yazılır ve yarıda kalırsa yazılanlar kalır, kalanlar eski durumunda durur.'));

  const onKey = (event) => { if (event.key === 'Escape') close(); };
  const drop = () => document.removeEventListener('keydown', onKey);
  const untrack = track(drop);
  const close = () => {
    drop();
    untrack();
    overlay.remove();
  };
  document.addEventListener('keydown', onKey);

  let picker = null;
  if (action !== 'close') {
    picker = h('select', 'kit-select');
    const items = action === 'status'
      ? state.reference.statuses.map((item) => ({ value: item.value, label: item.label }))
      : state.reference.assignees.map((item) => ({ value: item.name, label: item.name }));
    for (const item of items) {
      const option = h('option', undefined, item.label);
      option.value = String(item.value);
      picker.append(option);
    }
    const wrap = h('label', 'kit-field');
    wrap.append(h('span', 'kit-field-label', action === 'status' ? 'Yeni durum' : 'Atanan kişi'),
      picker);
    box.append(wrap);
  }

  const actions = h('div', 'kit-dialog-actions');
  actions.append(button('Vazgeç', { onClick: close }), button('Uygula', {
    variant: 'danger',
    onClick: async () => {
      const value = picker ? picker.value : '';
      if (action !== 'close' && !value) { toast('Değer seçilmedi.', 'bad'); return; }
      const reason = await askReason({
        title: titles[action],
        description: `${num(state.selection.length)} talep etkilenecek. Gerekçe her talebin `
          + 'denetim kaydına ayrı ayrı yazılır.',
        confirmLabel: 'Uygula',
      });
      if (!reason) return;
      close();
      await withBusy('Uygulanıyor…', async () => {
        const result = await api(`${BASE}/bulk`, {
          method: 'POST',
          body: {
            requestIds: state.selection.map(Number), action, value, reason, dryRun: false,
          },
        });
        if (result.ok === false && !result.applied) throw new Error(result.error);
        toast(`${num(result.applied)} talep güncellendi`
          + (result.failed?.length ? ` · ${num(result.failed.length)} başarısız` : ''),
        result.failed?.length ? 'warn' : 'good');
        nodes.table?.clearSelection();
        state.selection = [];
        refresh();
      });
    },
  }));
  box.append(actions);
  overlay.append(box);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  nodes.root.append(overlay);
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'kit-panel rq');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar([
    { key: 'list', label: 'Liste' },
    { key: 'board', label: 'Pano' },
  ], 'list', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Talep no, sipariş, müşteri, konu, mesaj', width: '280px' },
      { kind: 'select', key: 'type', label: 'Tür', options: [{ value: '', label: 'Tümü — tür' }] },
      { kind: 'select', key: 'status', label: 'Durum', options: [{ value: '', label: 'Tümü — durum' }] },
      { kind: 'select', key: 'priority', label: 'Öncelik', options: [{ value: '', label: 'Tümü — öncelik' }] },
      { kind: 'select', key: 'assignee', label: 'Atanan', options: [{ value: '', label: 'Tümü — atanan' }] },
      { kind: 'select', key: 'channel', label: 'Kanal', options: [{ value: '', label: 'Tümü — kanal' }] },
      {
        kind: 'select',
        key: 'dateField',
        label: 'Tarih alanı',
        value: 'created',
        options: [{ value: 'created', label: 'Açılış' }, { value: 'updated', label: 'Son hareket' }],
      },
      // `<input type="date">` KULLANILMAZ (WebKitGTK'da açılır takvim kapanmıyor).
      // Aralık BOŞ başlar. Kitin varsayılanı "son 7 gün"dür ve bu ekranda
      // yalan söylerdi: bir hafta önce açılmış GECİKMİŞ talep listeden düşer,
      // "Süresi geçen" çipi 0 gösterir ve tam da bakılması gereken iş görünmez.
      { kind: 'dateRange', key: 'range', label: 'Aralık', start: '', end: '' },
    ],
    onChange: () => refresh({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => refresh() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAll }),
      button('SLA raporu', {
        onClick: () => {
          const filters = currentFilters();
          report.run('sla', {
            type: filters.type, status: filters.status, assignee: filters.assignee,
            start: filters.start, end: filters.end,
          });
        },
      }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => applyChip(key));
  nodes.status = statusLine();
  nodes.kpi = h('div', 'rq-kpi');
  nodes.selbar = h('div', 'rq-selbar');
  nodes.tableWrap = h('div', 'rq-table');
  nodes.boardWrap = h('div', 'rq-board-wrap');
  nodes.pager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => refresh({ page, size }),
  });
  // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. İki ayrı kap tutup
  // `replaceChild` yapmak, kaydırma konumunu kaybettiriyor.
  nodes.body = h('div', 'rq-body');
  const listView = [nodes.kpi, nodes.selbar, nodes.tableWrap, nodes.pager.node];
  const boardView = [nodes.kpi, nodes.boardWrap];
  nodes.body.append(...listView);

  view.append(tabs.node, nodes.filters.node, nodes.chips.node, nodes.status.node, nodes.body);

  function showView(key) {
    state.view = key;
    // Durum süzgeci PANODA anlamsız: her sütun zaten bir durum.
    nodes.filters.set('status', '');
    if (key === 'board') {
      nodes.body.replaceChildren(...boardView);
      refreshBoard();
    } else {
      nodes.body.replaceChildren(...listView);
      refresh({ page: 1 });
    }
  }

  root.replaceChildren(view);
  nodes.status.set('Talepler alınıyor…');
  // Referans listeler ÖNCE gelir: süzgeç açılırları dolmadan liste çekmek,
  // kullanıcının seçtiği durumu kaybettiriyordu.
  loadReference().then(() => refresh());

  return () => {
    nodes.filters?.destroy();          // arama alanı ve tarih alanı dinleyici tutar
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
