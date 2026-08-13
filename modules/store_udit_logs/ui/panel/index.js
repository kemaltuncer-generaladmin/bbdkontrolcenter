// UDİT İşlem Kayıtları paneli — kim, neyi, neden değiştirdi.
//
// NE YAPAR: mağazanın denetim kayıtları ile geçidin yerel izini TEK ZAMAN
// ÇİZGİSİNDE gösterir; satırdan açılan çekmecede ALAN ALAN fark tablosunu
// (öncesi → sonrası) verir; kayıttan ilgili panele geçirir; aralığın dökümünü
// PDF/CSV üretir.
//
// NE YAPMAZ — VE ASLA YAPMAYACAK:
//  · Kayıt DÜZENLEMEZ, SİLMEZ. Bu ekran salt okunurdur ve bu bir görünüm
//    tercihi değil, ekranın varlık sebebidir: denetim kaydı değiştirilebiliyorsa
//    denetim kaydı değildir. Panelde kalıcı bir not olarak yazar.
//  · Tarih aralığı olmadan sorgu ATMAZ. Açık uçlu denetim sorgusu mağazayı
//    dakikalarca meşgul eder ve ekranı da doldurmaz.
//  · Sayfa numarası göstermez. Kayıt sayısı sınırsız büyür; "sayfa 412/9.318"
//    kimseye bir şey anlatmaz. Sayfalama SUNUCU TARAFINDA ve İMLEÇLİDİR —
//    20 ekran içinde bunu yapan tek ekran budur.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · İki kaynağın saat ekseni farklı (geçit UTC, mağaza yerel). Birleştirme
//    backend'de `records.normalize_stamp` ile yapılır; ekran hazır damga alır.
//  · Başarılı yazma iki kaynakta da var; geçit satırı yalnız GEREKÇE kaynağı
//    olarak kullanılır, listede tekrar etmez.
//  · Mağaza süzgeci uygulamamış olabilir (Laravel tanımadığı parametreyi
//    sessizce yok sayar). Backend bunu yakalar, ekran "sayfa içinde süzüldü"
//    uyarısını GÖSTERİR — süzülmemiş listeyi süzülmüş gibi göstermez.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_udit_logs/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_udit_logs/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import { button, clip, csvBlob, h, loadStyles, num, toaster } from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows, statusLine,
} from '../../ui-kit/layout.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_udit_logs';

/**
 * Yeteneği dışa vurur: `store.audit.for`.
 *
 * 19 panelin çekmecesindeki "İşlem geçmişi" sekmesi budur. İMZA BİLEREK DAR:
 * varlık adı + kayıt numarası + (isteğe bağlı) kaç satır ve kaç gün. Süzgeç,
 * sayfalama ve imleç bu yüzeyde YOKTUR — çağıran panel bir kaydın geçmişini
 * gösterir; daha fazlası gerekiyorsa `ctx.open('store_udit_logs', {entity,
 * entityId})` ile bu ekrana geçer ve iş burada devam eder.
 *
 * Yayınlanmış imza geriye dönük uyumsuz DEĞİŞTİRİLMEZ: 19 panel buna bağlanır.
 * Yeni davranış yeni bir alan olarak `options` içinde gelir.
 *
 * K3: yalnızca `ctx.api` kullanılır — bu fonksiyon panel açılmadan çağrılır.
 */
export function capabilities(ctx) {
  return {
    'store.audit.for': (entity, entityId, options = {}) => {
      const params = new URLSearchParams({
        entity: String(entity || ''),
        entityId: String(Number(entityId) || 0),
      });
      if (options.limit) params.set('limit', String(options.limit));
      if (options.days) params.set('days', String(options.days));
      return ctx.api(`${BASE}/history?${params.toString()}`);
    },
  };
}

// Varlık → panel eşlemesi. Hedefi KABUK bilmez, çağıran panel bilir (K1);
// burada durması doğrudur. Eşlemesi olmayan varlıkta düğme pasif olur ve
// nedenini söyler — sessizce hiçbir şey yapmayan düğme bırakılmaz.
const TARGETS = {
  order: ['store_orders', 'orderId'],
  invoice: ['store_invoices', 'invoiceId'],
  refund: ['store_refunds', 'refundId'],
  shipment: ['store_shipping', 'shipmentId'],
  transaction: ['store_payment_gateway', 'transactionId'],
  product: ['store_products', 'productId'],
  category: ['store_products', 'categoryId'],
  customer: ['store_customers', 'customerId'],
  customer_group: ['store_customers', 'groupId'],
  review: ['store_customers', 'reviewId'],
  cms_page: ['store_cms', 'pageId'],
  cart_rule: ['store_promotions', 'ruleId'],
  catalog_rule: ['store_promotions', 'ruleId'],
  tax_rate: ['store_tax', 'rateId'],
  tax_category: ['store_tax', 'categoryId'],
  bundle: ['store_bundles', 'bundleId'],
  carousel: ['store_home_media', 'slotId'],
  notification: ['store_notifications', 'notificationId'],
  return_request: ['store_requests', 'requestId'],
  trial_exam: ['store_trial_club', 'examId'],
  backup: ['store_backups', 'backupId'],
};

const EMPTY = {
  items: [], connected: false, error: '', cursor: '', hasMore: false, remoteTotal: 0,
  serverFiltered: true, warnings: [], range: {}, guard: false, loaded: false,
};

let api = null;
let open = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY };
// İmleç geriye gitmez: ÖNCEKİ sayfaları getiren imleçler burada yığılır.
//
// TUZAK: yığına konması gereken, o an çizili sayfayı GETİREN imleçtir
// (`pageCursor`), sayfanın SONRAKİNİ gösteren `state.cursor` değil. İkisini
// karıştırmak "‹ Önceki" düğmesini aynı sayfayı yeniden yükleyen bir düğmeye
// çevirir ve kimse fark etmez: sayfa değişmiş gibi görünür, satırlar aynıdır.
let trail = [];
let pageCursor = '';
const nodes = {};

// ------------------------------------------------------------------ araçlar

async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

/** `2026-08-13T10:22:31` → `13.08.2026 10:22:31`. Kaynak saniye hassasiyetli. */
function stampText(iso) {
  const raw = String(iso || '');
  if (raw.length < 19) return raw || '—';
  return `${raw.slice(8, 10)}.${raw.slice(5, 7)}.${raw.slice(0, 4)} ${raw.slice(11, 19)}`;
}

/** `HH:MM` doğrular; boş ya da bozuksa varsayılana düşer. */
function clockValue(input, fallback) {
  const raw = String(input.value || '').trim();
  const ok = /^([01]\d|2[0-3]):[0-5]\d$/.test(raw);
  input.classList.toggle('bad', Boolean(raw) && !ok);
  return ok ? raw : fallback;
}

function filters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  const range = values.range || {};
  return {
    // Tarih ZORUNLU: kit `dateRange` varsayılan olarak son 7 günü verir, bu
    // yüzden ekran hiçbir zaman aralıksız açılmaz. Kullanıcı alanı elle
    // boşaltırsa sunucu kuralı söyler ve liste çizilmez.
    start: range.start ? `${range.start}T${clockValue(nodes.fromClock, '00:00')}:00` : '',
    end: range.end ? `${range.end}T${clockValue(nodes.toClock, '23:59')}:59` : '',
    q: values.q || '',
    user: values.user || '',
    action: values.action || '',
    entity: values.entity || '',
    entityId: Number(values.entityId) || 0,
    ip: values.ip || '',
    result: values.result || '',
    source: values.source || '',
    destructive: Boolean(values.destructive),
    reasoned: Boolean(values.reasoned),
  };
}

function queryString(extra = {}) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries({ ...filters(), ...extra })) {
    if (value === null || value === undefined || value === '' || value === 0
      || value === false) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

// -------------------------------------------------------------------- veri

async function load({ cursor = '', push = false } = {}) {
  if (busy) return;
  busy = true;
  nodes.tableWrap.replaceChildren(skeletonRows(10, 9));
  nodes.status.set('Denetim kayıtları alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/entries?${queryString({ cursor })}`);
  } catch (error) {
    state = { ...state, ...EMPTY, error: error.message, loaded: true };
    busy = false;
    render();
    return;
  }
  if (push) trail.push(pageCursor);
  pageCursor = cursor;
  state = {
    ...state,
    items: payload.items || [],
    connected: Boolean(payload.connected),
    error: payload.error || '',
    cursor: payload.cursor || '',
    hasMore: Boolean(payload.hasMore),
    remoteTotal: payload.remoteTotal || 0,
    serverFiltered: payload.serverFiltered !== false,
    warnings: payload.warnings || [],
    range: payload.range || {},
    guard: Boolean(payload.guard),
    loaded: true,
  };
  busy = false;
  render();
}

function reload() {
  trail = [];
  pageCursor = '';
  state.cursor = '';
  load({ cursor: '' });
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    return;   // Süzgeçler serbest metne düşer; ekran çalışmaya devam eder (K7).
  }
  nodes.filters.options('user', [
    { value: '', label: 'Tümü — kullanıcı' },
    ...(payload.users || []).map((item) => ({ value: item.value, label: item.label })),
  ]);
  nodes.filters.options('entity', [
    { value: '', label: 'Tümü — varlık' },
    ...(payload.entities || []).map((item) => ({ value: item.value, label: item.label })),
  ]);
  nodes.filters.options('action', [
    { value: '', label: 'Tümü — işlem' },
    ...(payload.actions || []).map((item) => ({ value: item.value, label: item.label })),
  ]);
  nodes.filters.options('result', [
    { value: '', label: 'Tümü — sonuç' },
    ...(payload.results || []).map((item) => ({ value: item.value, label: item.label })),
  ]);
  if (payload.maxRangeDays) {
    nodes.rangeHint.textContent = `En geniş aralık ${payload.maxRangeDays} gün · `
      + `sayfa başına ${payload.pageSize} kayıt`;
  }
}

// ------------------------------------------------------------------- çizim

function statusText() {
  if (state.guard) return state.error;
  const window = state.range?.start
    ? `${stampText(state.range.start)} – ${stampText(state.range.end)}`
    : '';
  if (!state.connected && state.error) {
    return `Mağazaya ulaşılamadı — ${state.error}. Geçidin yerel izi gösteriliyor.`;
  }
  const total = state.remoteTotal ? ` · mağazada ${num(state.remoteTotal)} kayıt` : '';
  return `${window} · ekranda ${num(state.items.length)} kayıt${total}`;
}

function renderKpi() {
  const rows = state.items;
  const destructive = rows.filter((row) => row.destructive).length;
  const reasoned = rows.filter((row) => row.reason).length;
  const gateway = rows.filter((row) => row.source === 'gateway').length;
  const users = new Set(rows.map((row) => row.user).filter((name) => name && name !== '—'));
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Bu sayfada', value: num(rows.length) },
    { label: 'Yıkıcı', value: num(destructive), tone: destructive ? 'bad' : '' },
    { label: 'Gerekçeli', value: num(reasoned) },
    { label: 'Kullanıcı', value: num(users.size) },
    {
      label: 'Gönderilemeyen',
      value: num(gateway),
      tone: gateway ? 'warn' : '',
      title: 'Geçitten çıkmayan ya da sonucu bilinmeyen istekler — mağazada izi yoktur.',
    },
  ]));
}

function renderNotices() {
  nodes.notices.replaceChildren();
  if (state.guard) {
    nodes.notices.append(alertBox(state.error, 'warn'));
    return;
  }
  if (!state.connected && state.error) {
    nodes.notices.append(alertBox(
      `Mağaza denetim kaydı okunamadı — ${state.error} · Aşağıdaki satırlar geçidin `
      + 'yerel izinden gelir: ne yapmaya çalıştığımız burada durur.', 'bad'));
  }
  for (const warning of state.warnings) nodes.notices.append(alertBox(warning, 'warn'));
}

function actionCell(row) {
  const box = h('span', 'ud-action');
  box.append(h('b', undefined, row.actionLabel));
  if (row.destructive) box.append(badge('yıkıcı', 'bad'));
  if (row.dryRun) box.append(badge('kuru prova', 'info'));
  return box;
}

function entityCell(row) {
  const box = h('span', 'ud-entity');
  box.append(h('span', undefined, row.entityLabel));
  box.append(h('span', 'ud-sub', row.sourceLabel));
  return box;
}

function textCell(value, max) {
  const node = h('span', 'ud-clip');
  clip(node, value || '', max);
  return node;
}

const COLUMNS = [
  { key: 'at', label: 'Zaman', width: '150px', cell: (row) => stampText(row.at) },
  { key: 'user', label: 'Kullanıcı', width: '130px', cell: (row) => textCell(row.user, 18) },
  { key: 'action', label: 'İşlem', width: '150px', cell: actionCell },
  { key: 'entity', label: 'Varlık', width: '110px', cell: entityCell },
  {
    key: 'entityId',
    label: 'Kayıt',
    width: '78px',
    align: 'num',
    cell: (row) => (row.entityId ? `#${row.entityId}` : '—'),
  },
  {
    key: 'summary',
    label: 'Özet (ne değişti)',
    width: 'minmax(0, 2fr)',
    cell: (row) => textCell(row.summary || (row.fieldCount ? `${row.fieldCount} alan` : ''), 44),
  },
  {
    key: 'reason',
    label: 'Gerekçe',
    width: 'minmax(0, 1.6fr)',
    cell: (row) => textCell(row.reason, 40),
  },
  { key: 'ip', label: 'IP', width: '116px', cell: (row) => textCell(row.ip, 15) },
  {
    key: 'result',
    label: 'Sonuç',
    width: '104px',
    // Renk TEK BAŞINA anlam taşımaz: rozet her zaman metin taşır.
    cell: (row) => badge(row.resultLabel, row.resultTone),
  },
];

function render() {
  renderNotices();
  renderKpi();

  if (state.guard) {
    nodes.tableWrap.replaceChildren(emptyState({
      title: 'Tarih aralığı gerekiyor',
      text: state.error,
    }));
  } else if (!state.items.length) {
    // "Daha var ama bu sayfada eşleşen yok" ile "hiç yok" AYRI şeylerdir:
    // mağaza süzgeci uygulamadığında sayfa içinde eleme yapılır ve sayfa boş
    // kalabilir. İkisine aynı cümleyi yazmak kullanıcıyı yanlış yönlendirir.
    // `state.cursor` şart: imleçsiz "Sonraki sayfa" düğmesi ilk sayfayı
    // yeniden yükler — hareket ediyormuş gibi görünen ölü düğme.
    nodes.tableWrap.replaceChildren(state.hasMore && state.cursor ? emptyState({
      title: 'Bu sayfada eşleşen kayıt yok',
      text: 'Süzgece uyan kayıt sonraki sayfalarda olabilir; kayıtlar sayfa içinde '
        + 'süzülüyor.',
      actions: [button('Sonraki sayfa', {
        variant: 'primary',
        onClick: () => load({ cursor: state.cursor, push: true }),
      })],
    }) : emptyState({
      title: 'Bu aralıkta kayıt yok',
      text: 'Seçilen aralık ve süzgeçlerde hiçbir işlem kaydedilmemiş. Aralığı genişletin '
        + 'ya da süzgeçleri temizleyin.',
      actions: [button('Süzgeçleri temizle', { onClick: () => nodes.filters.reset() })],
    }));
  } else {
    nodes.table = dataTable({
      columns: COLUMNS,
      rows: state.items,
      dense: true,                       // yoğun kip: 100 satır tek ekranda okunur
      rowKey: (row) => row.key,
      onRow: (row) => openEntry(row),
    });
    nodes.tableWrap.replaceChildren(nodes.table.node);
  }

  nodes.prev.disabled = trail.length === 0;
  nodes.next.disabled = !state.hasMore || !state.cursor;
  nodes.cursorText.textContent = trail.length
    ? `Sayfa ${trail.length + 1} · imleçli`
    : 'İlk sayfa · imleçli';
  nodes.status.set(statusText(), Boolean(state.error) && !state.connected);
}

// ---------------------------------------------------------------- çekmece

// Fark tablosu KİTİN tablosudur (ADR 0011): elle çizilen bir ızgara, kitin
// sütun şablonunu, yoğun kipini ve '—' davranışını tekrar yazmak demekti.
// Satır düzeyinde vurgu kitte yok; değişen alan adı KOYU + renkli çizilir ve
// son sütunda ayrıca YAZIYLA işaretlenir (renk tek başına anlam taşımaz).
const DIFF_COLUMNS = [
  {
    key: 'field',
    label: 'Alan',
    width: 'minmax(0, 1.2fr)',
    cell: (item) => {
      const node = h(item.changed ? 'b' : 'span', `ud-clip${item.changed ? ' on' : ''}`);
      return clip(node, item.field, 34);
    },
  },
  // '—' burada BİLEREK var: boş metin "alan yoktu" demek değildir, "değer
  // boştu" demektir; ikisi de fark tablosunda okunur bir hücre ister.
  {
    key: 'before',
    label: 'Öncesi',
    width: 'minmax(0, 2fr)',
    cell: (item) => textCell(item.before || '—', 60),
  },
  {
    key: 'after',
    label: 'Sonrası',
    width: 'minmax(0, 2fr)',
    cell: (item) => textCell(item.after || '—', 60),
  },
  {
    key: 'changed',
    label: 'Durum',
    width: '82px',
    cell: (item) => badge(item.changed ? 'değişti' : 'aynı', item.changed ? 'info' : 'dim'),
  },
];

function diffTable(row) {
  const rows = row.diff || [];
  if (!rows.length) return h('div', 'ud-sub', 'Bu kayıtta alan farkı taşınmamış.');
  return dataTable({ columns: DIFF_COLUMNS, rows, dense: true, rowKey: (i) => i.field }).node;
}

function metaGrid(row) {
  const box = h('div', 'ud-meta');
  const pairs = [
    ['Kaynak', row.sourceLabel === 'Mağaza'
      ? 'Mağaza denetim kaydı (admin_api_audits)'
      : 'Geçit izi (istek gönderilmeden yazıldı)'],
    ['Zaman', stampText(row.at)],
    ['Kullanıcı', row.user],
    ['Sonuç', `${row.resultLabel}${row.status ? ` · HTTP ${row.status}` : ''}`],
    ['Yöntem/yol', `${row.method || '—'} ${row.path || ''}`.trim()],
    ['IP', row.ip || '—'],
    ['İstek kimliği', row.requestId || '—'],
  ];
  for (const [label, value] of pairs) {
    box.append(h('span', 'ud-sub', label), h('span', undefined, String(value || '—')));
  }
  return box;
}

async function openEntry(row) {
  const target = TARGETS[row.entity];
  const goto = button('Kayda git', {
    variant: 'primary',
    title: target
      ? `${row.entityLabel} kaydını ilgili ekranda aç`
      : 'Bu varlık için ekran eşlemesi yok; kayıt numarasını kopyalayıp arayabilirsiniz.',
    disabled: !target || !row.entityId,
    onClick: () => {
      const [panel, key] = target;
      box.close();
      open(panel, { [key]: row.entityId });
    },
  });

  const history = button('Bu kayıt için tüm geçmiş', {
    title: 'Bu kaydın aralıktaki tüm işlemlerini PDF olarak üretir',
    disabled: !row.entityId,
    onClick: () => report.run('record', {
      ...filters(), entity: row.entity, entityId: row.entityId,
    }),
  });

  const box = drawer(nodes.root, {
    title: `${row.entityLabel}${row.entityId ? ` #${row.entityId}` : ''} · ${row.actionLabel}`,
    subtitle: `${stampText(row.at)} · ${row.user} · ${row.sourceLabel}`,
    actions: [goto, history],
    onClose: () => { nodes.drawer = null; },
  });
  nodes.drawer = box;

  const body = box.body;
  body.append(hintBox('Bu kayıt salt okunurdur: düzenlenemez, silinemez. '
    + 'Aşağıdaki fark tablosu kaydın kendisinden üretilir.'));

  if (row.result !== 'ok') {
    body.append(alertBox(
      row.result === 'unknown'
        ? 'Bu isteğin sonucu BİLİNMİYOR: istek geçitten çıktı ama yanıt alınamadı. '
          + 'Uzakta uygulanmış olabilir; mağaza kaydında karşılığını arayın.'
        : `İstek sonucu: ${row.resultLabel}. Mağaza tarafında kalıcı bir değişiklik `
          + 'oluşmamış olabilir.',
      row.result === 'blocked' ? 'warn' : 'bad'));
  }

  body.append(metaGrid(row));
  body.append(card('Gerekçe', h('div', 'ud-reason',
    row.reason || 'Gerekçe kaydedilmemiş. (Mağaza kendi denetim kaydında gerekçe tutmaz; '
    + 'gerekçe yalnız Kontrol Merkezi üzerinden yapılan yazmalarda vardır.)')));
  body.append(card('Alan farkı', diffTable(row),
    `${(row.changed || []).length} alan değişti · ${row.fieldCount} alan taşındı`));

  // Ham JSON KATLANIR: normalde kimse okumaz, gerektiğinde her şey oradadır.
  const fold = h('details', 'ud-raw');
  fold.append(h('summary', undefined, 'Ham kayıt (JSON)'));
  const pre = h('pre', 'ud-json');
  pre.textContent = row.raw || '{}';
  fold.append(pre);
  body.append(fold);

  // Listedeki fark KIRPILMIŞTIR (80 alan / 4 KB). Çekmece açılınca kaydın
  // tamamı tazelenir; başarısız olursa kırpılmış hâli ekranda kalır.
  try {
    const fresh = await call(`${BASE}/entry?key=${encodeURIComponent(row.key)}`);
    if (!fresh?.item && fresh?.error && nodes.drawer === box) {
      // Tazeleme yapılamadı ama elimizdeki kırpılmış hâl DOĞRU: sebebini
      // söyler, ekranı boşaltmayız.
      body.append(alertBox(fresh.error, 'warn'));
    }
    if (fresh?.item && nodes.drawer === box) {
      const full = fresh.item;
      body.replaceChildren();
      body.append(hintBox('Bu kayıt salt okunurdur: düzenlenemez, silinemez.'));
      body.append(metaGrid(full));
      body.append(card('Gerekçe', h('div', 'ud-reason',
        full.reason || 'Gerekçe kaydedilmemiş.')));
      body.append(card('Alan farkı', diffTable(full),
        `${(full.changed || []).length} alan değişti · ${full.fieldCount} alan`));
      const deep = h('details', 'ud-raw');
      deep.append(h('summary', undefined, 'Ham kayıt (JSON)'));
      const text = h('pre', 'ud-json');
      text.textContent = full.raw || '{}';
      deep.append(text);
      body.append(deep);
    }
  } catch (error) {
    if (nodes.drawer === box) {
      body.append(alertBox(`Kaydın tamamı tazelenemedi: ${error.message}`, 'warn'));
    }
  }
}

// -------------------------------------------------------------------- CSV

function exportVisible() {
  if (!state.items.length) {
    toast('Dışa aktarılacak kayıt yok.', 'warn');
    return;
  }
  const headers = ['Zaman', 'Kaynak', 'Kullanıcı', 'İşlem', 'Varlık', 'Kayıt', 'Özet',
    'Gerekçe', 'IP', 'Sonuç', 'Yıkıcı'];
  const rows = state.items.map((row) => [
    stampText(row.at), row.sourceLabel, row.user, row.actionLabel, row.entityLabel,
    row.entityId || '', row.summary, row.reason, row.ip, row.resultLabel,
    row.destructive ? 'evet' : 'hayır',
  ]);
  const day = String(state.range?.start || '').slice(0, 10) || 'aralik';
  const count = csvBlob(headers, rows, `denetim-gorunen-${day}`);
  toast(`${count} satır indirildi.`, 'good');
}

async function exportAll() {
  if (busy) return;
  busy = true;
  nodes.status.set('Aralığın tamamı yazılıyor…');
  try {
    const result = await call(`${BASE}/export`, { method: 'POST', body: filters() });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    if (result.truncated) {
      toast('Tavana dayanıldı; döküm eksik olabilir. Aralığı daraltın.', 'warn');
    }
    nodes.lastFile.set(result.path);
  } catch (error) {
    toast(error.message || 'Döküm alınamadı.', 'bad');
    nodes.status.set(error.message, true);
  } finally {
    busy = false;
    nodes.status.set(statusText(), !state.connected);
  }
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  open = ctx.open;
  trail = [];
  pageCursor = '';
  state = { ...EMPTY };

  const view = h('div', 'kit-panel ud');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Gerekçe, kullanıcı, işlem, yol ara', width: '250px' },
      { kind: 'dateRange', key: 'range', label: 'Aralık' },
      { kind: 'select', key: 'user', label: 'Kullanıcı', options: [{ value: '', label: 'Tümü — kullanıcı' }] },
      { kind: 'select', key: 'action', label: 'İşlem', options: [{ value: '', label: 'Tümü — işlem' }] },
      { kind: 'select', key: 'entity', label: 'Varlık', options: [{ value: '', label: 'Tümü — varlık' }] },
      { kind: 'search', key: 'entityId', placeholder: 'Kayıt no', width: '96px' },
      { kind: 'select', key: 'result', label: 'Sonuç', options: [{ value: '', label: 'Tümü — sonuç' }] },
      { kind: 'select', key: 'source', label: 'Kaynak', options: [
        { value: '', label: 'Tümü — kaynak' },
        { value: 'store', label: 'Mağaza kaydı' },
        { value: 'gateway', label: 'Geçit izi' },
      ] },
      { kind: 'search', key: 'ip', placeholder: 'IP', width: '120px' },
      { kind: 'toggle', key: 'destructive', label: 'Yıkıcı işlemler' },
      { kind: 'toggle', key: 'reasoned', label: 'Gerekçeli işlemler' },
    ],
    onChange: () => reload(),
    actions: [
      button('Yenile', { onClick: () => reload() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Aralığın tümü', { title: 'Rapor klasörüne yaz (iz bırakır)', onClick: exportAll }),
      button('Denetim dökümü', { onClick: () => report.run('dump', filters()) }),
    ],
  });

  // SAAT HASSASİYETİ. Kit'in tarih alanı GÜN verir; denetimde "13 Ağustos
  // 14:00'ten sonra ne oldu" sorusu sık sorulur. `<input type="time">`
  // WebKitGTK'da tarih alanıyla aynı derdi taşıdığı için düz metin kullanılır
  // ve `HH:MM` elle doğrulanır.
  nodes.fromClock = h('input', 'kit-input ud-clock');
  nodes.toClock = h('input', 'kit-input ud-clock');
  for (const [input, value, label] of [[nodes.fromClock, '00:00', 'Başlangıç saati'],
    [nodes.toClock, '23:59', 'Bitiş saati']]) {
    input.type = 'text';
    input.value = value;
    input.placeholder = value;
    input.setAttribute('aria-label', label);
    input.addEventListener('change', () => reload());
  }
  nodes.rangeHint = h('span', 'ud-sub');
  const clocks = h('div', 'ud-clocks');
  clocks.append(h('span', 'kit-filter-label', 'Saat'), nodes.fromClock,
    h('span', undefined, '–'), nodes.toClock, h('span', 'kit-spacer'), nodes.rangeHint);

  nodes.status = statusLine();
  nodes.notices = h('div', 'ud-notices');
  nodes.kpi = h('div', 'ud-kpi');
  nodes.tableWrap = h('div', 'ud-table');
  nodes.lastFile = report.lastFileLine();

  nodes.prev = button('‹ Önceki', {
    title: 'Bir önceki imleç sayfası',
    onClick: () => {
      if (!trail.length) return;
      load({ cursor: trail.pop() });
    },
  });
  nodes.next = button('Sonraki ›', {
    title: 'Bir sonraki imleç sayfası',
    onClick: () => load({ cursor: state.cursor, push: true }),
  });
  nodes.cursorText = h('span', 'ud-sub', 'İlk sayfa · imleçli');
  const cursorBar = h('div', 'ud-cursor');
  cursorBar.append(nodes.prev, nodes.next, nodes.cursorText, h('span', 'kit-spacer'),
    nodes.lastFile.node);

  nodes.body = h('div', 'ud-body');
  nodes.body.append(nodes.notices, nodes.kpi, nodes.tableWrap, cursorBar);

  view.append(
    // KALICI NOT — ekranın kimliği. Kaydırılıp gözden kaybolmasın diye
    // gövdenin İÇİNDE değil, üstünde sabit durur.
    hintBox('Bu ekran salt okunurdur. Denetim kaydı silinemez, düzenlenemez; '
      + 'buradan yapılan tek yazma işlemi rapor dosyası üretmektir.'),
    nodes.filters.node, clocks, nodes.status.node, nodes.body,
  );

  root.replaceChildren(view);

  // Başka panelden gelindiyse (ör. Siparişler → "İşlem geçmişi") süzgeç
  // hazır gelir; kullanıcı aynı sorguyu elle kurmak zorunda kalmaz.
  if (ctx.payload?.entity) {
    nodes.filters.set('entity', String(ctx.payload.entity));
    if (ctx.payload.entityId) nodes.filters.set('entityId', String(ctx.payload.entityId));
  }

  loadReference().then(() => load({ cursor: '' }));

  return () => {
    nodes.filters?.destroy();      // tarih alanları global dinleyici tutuyor
    nodes.drawer?.close();         // açık çekmece kalırsa artık DOM'da asılı kalır
    nodes.drawer = null;
    root.replaceChildren();
    state = { ...EMPTY };
    trail = [];
    pageCursor = '';
    busy = false;
  };
}
