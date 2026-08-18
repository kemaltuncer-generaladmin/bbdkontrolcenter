// Kantin Raporları paneli — öğrenci ve ürün bazlı derin analiz.
//
// Kaynak kantinin işlem geçmişidir; kırılımlar çekirdekte hesaplanır. Çekilen
// her gün önbelleğe yazıldığı için ikinci açılış anında gelir ve kantin
// erişilemese bile geçmiş raporlar okunur.
//
// Grafikler satır içi SVG'dir: dış kitaplık yok, tema uyumlu, yazdırılabilir.

import { dateField } from './datefield.js';
import { barChart, hourStrip, lineChart, paretoChart } from './charts.js';
import {
  button, foldText, h, loadStyles, longDate, money, stamp, toaster, todayIso,
} from './kit.js';
// BASKI CİHAZDA YAPILIR, SUNUCUDA DEĞİL — gerekçe `ui-kit/printing.js`
// başlığında. Bu panelin kendi `kit.js` kopyası var ama yazdırmanın ikinci bir
// kopyası OLMAZ: kâğıdın çıkacağı yeri tek yer bilir.
//
// YOL ÇALIŞMA ANINA GÖREDİR: panel `shell/panels/bbd_canteen_reports/` altına
// kopyalanıyor (`tools/build-ui-registry.py`), oradan `../../ui-kit/` doğru
// yeri gösterir. Kaynak ağacında bu göreli yol bir dosyaya denk gelmez.
import { printDocument, printerReady } from '../../ui-kit/printing.js';

loadStyles(import.meta.url);

let api = null;
let capability = null;
let toast = null;

let data = null;
let classMap = new Map();
let range = { start: todayIso(-6), end: todayIso() };
let tab = 'overview';
let studentQuery = '';
let studentSort = 'total';
let productSort = 'total';
let openStudent = null;
let busy = false;

const nodes = {};

// ------------------------------------------------------------------- veri

async function loadClasses() {
  const provider = capability?.('bbd_students.list');
  if (!provider) return;
  try {
    const list = await provider();
    classMap = new Map(list.map((student) => [student.id, student.className || '']));
  } catch (error) {
    console.warn('sınıf bilgisi alınamadı', error);
  }
}

/**
 * Raporu yükler.
 *
 * ÖNBELLEK YOK: her çağrıda veri kantinden taze çekilir. Eskiden çekilen
 * günler saklanıp tekrar okunuyordu; ama iptal edilen bir satış ya da nakit
 * tahsilat GEÇMİŞ günün rakamını değiştiriyor — saklanan kopya sessizce
 * yanlış rapor üretiyordu.
 */
async function load() {
  if (busy) return;
  busy = true;
  setStatus('Kantinden çekiliyor…');
  try {
    data = await api('/api/bbd_canteen_reports/report', {
      method: 'POST',
      body: {
        start: range.start,
        end: range.end,
        classes: Object.fromEntries(classMap),
        compare: true,
      },
    });
    const meta = data.meta || {};
    const snapshotBad = Boolean(meta.snapshotErrors?.length);
    // Eksik veri SESSİZ KALMAZ: aralık 400 günde kırpıldıysa ya da bir gün
    // kantinin satır sınırını doldurduysa `meta.warnings` bunu söyler ve
    // şeride yazılır. Eskiden ikisi de görünmeden oluyordu.
    const warnings = meta.warnings || [];
    setStatus(
      `${meta.days} gün · tamamı kantinden canlı okundu`
      + (meta.errors?.length ? ` · ${meta.errors.length} gün okunamadı` : '')
      + (snapshotBad ? ' · öğrenci/ürün listesi okunamadı, bazı kırılımlar eksik' : '')
      + (warnings.length ? ` · ${warnings.join(' · ')}` : ''),
      Boolean(meta.errors?.length) || snapshotBad || warnings.length > 0);
  } catch (error) {
    data = null;
    setStatus(`Rapor alınamadı: ${error.message}`, true);
  } finally {
    busy = false;
  }
  render();
}

// ------------------------------------------------------------------ çizim

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function render() {
  renderKpi();
  renderTabs();
  nodes.body.replaceChildren();
  if (!data) {
    nodes.body.append(h('div', 'cr-hint', 'Rapor yok. Bir aralık seçip “Getir” deyin.'));
    return;
  }
  ({
    overview: renderOverview,
    students: renderStudents,
    products: renderProducts,
    classes: renderClasses,
  }[tab] || renderOverview)();
}

function renderKpi() {
  nodes.kpi.replaceChildren();
  if (!data) return;
  const overview = data.overview;
  const compare = data.compare || {};

  const tile = (label, value, key) => {
    const box = h('div', 'cr-kpi-tile');
    box.append(h('span', 'cr-kpi-label', label), h('b', 'cr-kpi-value', value));
    const delta = compare[key];
    if (delta && delta.percent !== null && delta.percent !== undefined) {
      const up = delta.percent >= 0;
      const chip = h('span', `cr-delta ${up ? 'up' : 'down'}`,
        `${up ? '▲' : '▼'} %${Math.abs(delta.percent).toLocaleString('tr-TR')}`);
      chip.title = `Önceki dönem (${compare.range.start} – ${compare.range.end}): `
        + (key === 'total' || key === 'average' ? money(delta.previous) : delta.previous);
      box.append(chip);
    }
    return box;
  };

  nodes.kpi.append(
    tile('Toplam ciro', money(overview.total), 'total'),
    tile('İşlem', String(overview.count), 'count'),
    tile('Alışveriş yapan', String(overview.students), 'students'),
    tile('Satılan adet', String(overview.units), 'units'),
    tile('Ortalama sepet', money(overview.average), 'average'),
  );

  const extra = h('div', 'cr-kpi-tile');
  const receivable = data.openReceivables;
  extra.append(h('span', 'cr-kpi-label', 'Açık alacak'),
    h('b', `cr-kpi-value${receivable === null ? ' muted' : ' debt'}`,
      receivable === null ? '—' : money(receivable)));
  if (receivable === null) extra.title = 'Öğrenci listesi okunamadı; bakiye toplamı bilinmiyor.';
  nodes.kpi.append(extra);

  if (overview.reversedCount > 0) {
    const undone = h('div', 'cr-kpi-tile');
    undone.append(h('span', 'cr-kpi-label', 'İptal edilen'),
      h('b', 'cr-kpi-value muted', `${overview.reversedCount} · ${money(overview.reversedTotal)}`));
    undone.title = 'İptal edilen satışlar hiçbir hesaba dahil değildir.';
    nodes.kpi.append(undone);
  }
}

function renderTabs() {
  for (const [key, node] of Object.entries(nodes.tabs)) node.classList.toggle('on', key === tab);
}

function card(title, content, hint) {
  const box = h('section', 'cr-card');
  const head = h('div', 'cr-card-head');
  head.append(h('h3', 'cr-card-title', title));
  if (hint) head.append(h('span', 'cr-card-hint', hint));
  box.append(head, content);
  return box;
}

// --------------------------------------------------------------- sekmeler

function renderOverview() {
  const overview = data.overview;

  const series = overview.byDay.map((row) => ({
    label: row.day.slice(8) + '.' + row.day.slice(5, 7),
    value: row.total,
  }));
  nodes.body.append(card('Günlük ciro', lineChart(series),
    `${overview.byDay.length} gün · toplam ${money(overview.total)}`));

  nodes.body.append(card('Saat dağılımı', hourStrip(overview.byHour),
    'Hangi saatte kaç işlem yapıldı — teneffüs yoğunluğunu gösterir.'));

  const topProducts = data.products.slice(0, 10).map((row) => ({
    label: row.name, value: row.total, display: money(row.total),
  }));
  nodes.body.append(card('En çok ciro yapan ürünler', barChart(topProducts),
    'İlk 10 ürün'));

  const topStudents = data.students.slice(0, 10).map((row) => ({
    label: row.name, value: row.total, display: money(row.total),
  }));
  nodes.body.append(card('En çok harcayan öğrenciler', barChart(topStudents),
    'İlk 10 öğrenci'));

  const collections = data.collections || {};
  if (collections.totalCollected !== undefined) {
    const grid = h('div', 'cr-mini');
    const mini = (label, value) => {
      const box = h('div', 'cr-mini-tile');
      box.append(h('span', 'cr-mini-label', label), h('b', 'cr-mini-value', value));
      return box;
    };
    grid.append(
      mini('Dönemde tahsil edilen', money(collections.totalCollected)),
      mini('Nakit', money(collections.totalCash ?? 0)),
      mini('Tahsilat işlemi', String(collections.count ?? 0)),
      mini('Açık alacak', money(data.openReceivables)),
    );
    nodes.body.append(card('Tahsilat', grid,
      'Kaynak: kantin tahsilat raporu (ödeme linki + nakit).'));
  }
}

function renderStudents() {
  const tools = h('div', 'cr-tools');
  const search = h('input', 'cr-input');
  search.type = 'search';
  search.placeholder = 'Öğrenci ara';
  search.value = studentQuery;
  search.addEventListener('input', () => { studentQuery = search.value; renderStudents(); });

  const sort = h('select', 'cr-select');
  for (const [value, label] of [
    ['total', 'Tutara göre'], ['count', 'İşlem sayısına göre'],
    ['units', 'Adete göre'], ['balance', 'Bakiyeye göre'], ['name', 'Ada göre'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    sort.append(option);
  }
  sort.value = studentSort;
  sort.addEventListener('change', () => { studentSort = sort.value; renderStudents(); });

  tools.append(search, sort, h('span', 'cr-spacer'),
    button('CSV indir', { onClick: () => exportFile('students_csv') }));

  const needle = foldText(studentQuery);
  const rows = data.students
    .filter((row) => !needle || foldText(`${row.name} ${row.className}`).includes(needle))
    .sort((a, b) => (studentSort === 'name'
      ? String(a.name).localeCompare(String(b.name), 'tr')
      : Number(b[studentSort] || 0) - Number(a[studentSort] || 0)));

  const table = h('div', 'cr-table');
  table.append(headRow(['Öğrenci', 'Sınıf', 'İşlem', 'Adet', 'Tutar', 'Ortalama',
    'Bakiye', 'Favori ürün', 'Son işlem'], 'cr-row-students'));

  for (const row of rows) {
    const line = h('button', 'cr-row cr-row-students');
    line.type = 'button';
    line.append(
      h('span', 'cr-cell strong', row.name),
      h('span', 'cr-cell', row.className || '—'),
      h('span', 'cr-cell num', String(row.count)),
      h('span', 'cr-cell num', String(row.units)),
      h('span', 'cr-cell num', money(row.total)),
      h('span', 'cr-cell num', money(row.average)),
      h('span', `cr-cell num${row.balance > 0 ? ' debt' : ''}`, money(row.balance)),
      h('span', 'cr-cell', `${row.favourite} (${row.favouriteQty})`),
      h('span', 'cr-cell', stamp(row.last)),
    );
    line.addEventListener('click', () => showStudent(row));
    table.append(line);
  }

  if (rows.length === 0) table.append(h('div', 'cr-empty', 'Bu aralıkta işlem yok.'));

  nodes.body.append(card(`Öğrenci bazlı (${rows.length})`,
    wrap(tools, table),
    'Satıra tıklayın: o öğrencinin dökümü ve karne PDF\'i açılır.'));
}

function renderProducts() {
  const tools = h('div', 'cr-tools');
  const sort = h('select', 'cr-select');
  for (const [value, label] of [
    ['total', 'Ciroya göre'], ['qty', 'Adete göre'],
    ['buyers', 'Alıcı sayısına göre'], ['daysLeft', 'Tükenmeye yakın'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    sort.append(option);
  }
  sort.value = productSort;
  sort.addEventListener('change', () => { productSort = sort.value; renderProducts(); });

  tools.append(sort, h('span', 'cr-spacer'),
    button('CSV indir', { onClick: () => exportFile('products_csv') }),
    button('Ürün PDF', { variant: 'primary', onClick: () => exportFile('products_pdf') }));

  const rows = [...data.products].sort((a, b) => {
    if (productSort === 'daysLeft') {
      const left = a.daysLeft ?? Infinity;
      const right = b.daysLeft ?? Infinity;
      return left - right;
    }
    return Number(b[productSort] || 0) - Number(a[productSort] || 0);
  });

  nodes.body.append(card('Pareto — ciro yoğunlaşması', paretoChart(data.products),
    'Çubuk: ürün cirosu · kesikli çizgi: kümülatif pay. Cironun %80\'ini üreten '
    + 'ürünler A sınıfıdır.'));

  const table = h('div', 'cr-table');
  table.append(headRow(['Ürün', 'Adet', 'Ciro', 'Pay', 'ABC', 'Alıcı', 'Stok',
    'Günlük', 'Kalan gün'], 'cr-row-products'));

  for (const row of rows) {
    const line = h('div', 'cr-row cr-row-products');
    const left = row.daysLeft;
    line.append(
      h('span', 'cr-cell strong', row.name),
      h('span', 'cr-cell num', String(row.qty)),
      h('span', 'cr-cell num', money(row.total)),
      h('span', 'cr-cell num', `%${String(row.share).replace('.', ',')}`),
      h('span', `cr-cell abc abc-${row.abc}`, row.abc),
      h('span', 'cr-cell num', String(row.buyers)),
      h('span', 'cr-cell num', row.stock === null ? '—' : String(row.stock)),
      h('span', 'cr-cell num', String(row.perDay).replace('.', ',')),
      h('span', `cr-cell num${left !== null && left <= 7 ? ' warn' : ''}`,
        left === null ? '—' : `${String(left).replace('.', ',')} gün`),
    );
    if (left !== null && left <= 7) line.title = 'Bu hızla giderse stok bir hafta içinde biter.';
    table.append(line);
  }
  nodes.body.append(card(`Ürün bazlı (${rows.length})`, wrap(tools, table)));

  if ((data.deadStock || []).length > 0) {
    const dead = h('div', 'cr-table');
    dead.append(headRow(['Ürün', 'Stok', 'Birim fiyat', 'Bağlı tutar'], 'cr-row-dead'));
    for (const row of data.deadStock.slice(0, 40)) {
      const line = h('div', 'cr-row cr-row-dead');
      line.append(
        h('span', 'cr-cell strong', row.name),
        h('span', 'cr-cell num', String(row.stock)),
        h('span', 'cr-cell num', money(row.price)),
        h('span', 'cr-cell num', money(row.value)),
      );
      dead.append(line);
    }
    const total = data.deadStock.reduce((sum, row) => sum + row.value, 0);
    nodes.body.append(card('Ölü stok', dead,
      `Dönemde hiç satılmayan aktif ürünler · bağlı tutar ${money(total)}`));
  }
}

function renderClasses() {
  const rows = data.classes || [];
  const onlyUnknown = rows.length === 1 && rows[0].className === '—';

  if (rows.length === 0 || onlyUnknown) {
    nodes.body.append(card('Sınıf bazlı',
      h('div', 'cr-hint',
        'Sınıf bilgisi bulunamadı. Sınıflar Öğrenci Yönetimi ekranında girilir; '
        + 'girildiğinde bu sekme kendiliğinden dolar.')));
    return;
  }

  nodes.body.append(card('Sınıf cirosu', barChart(rows.map((row) => ({
    label: row.className, value: row.total, display: money(row.total),
  })), { max: 20 })));

  nodes.body.append(card('Öğrenci başına harcama', barChart(
    [...rows].sort((a, b) => b.perStudent - a.perStudent).map((row) => ({
      label: row.className, value: row.perStudent, display: money(row.perStudent),
    })), { max: 20 }),
    'Sınıf mevcudu farklıysa toplam ciro yanıltır; bu kırılım onu düzeltir.'));

  const table = h('div', 'cr-table');
  table.append(headRow(['Sınıf', 'Öğrenci', 'İşlem', 'Adet', 'Tutar', 'Öğrenci başına'],
    'cr-row-classes'));
  for (const row of rows) {
    const line = h('div', 'cr-row cr-row-classes');
    line.append(
      h('span', 'cr-cell strong', row.className),
      h('span', 'cr-cell num', String(row.students)),
      h('span', 'cr-cell num', String(row.count)),
      h('span', 'cr-cell num', String(row.units)),
      h('span', 'cr-cell num', money(row.total)),
      h('span', 'cr-cell num', money(row.perStudent)),
    );
    table.append(line);
  }
  nodes.body.append(card('Sınıf tablosu', table));
}

function headRow(labels, extraClass) {
  const row = h('div', `cr-row cr-head ${extraClass}`);
  for (const label of labels) row.append(h('span', 'cr-cell', label));
  return row;
}

function wrap(...children) {
  const box = h('div', 'cr-stack');
  box.append(...children);
  return box;
}

// ------------------------------------------------------- öğrenci dökümü

async function showStudent(row) {
  openStudent = row;
  const overlay = h('div', 'kit-overlay');
  const card_ = h('div', 'cr-drawer');
  card_.append(h('div', 'cr-drawer-head'));
  const head = card_.firstChild;
  head.append(
    h('div', 'cr-drawer-title', row.name),
    h('div', 'cr-drawer-sub',
      `${row.className || 'sınıfsız'} · ${longDate(range.start)} – ${longDate(range.end)}`),
    h('span', 'cr-spacer'),
    button('Karne PDF', {
      variant: 'primary',
      onClick: () => exportFile('student_pdf', { studentId: row.studentId }),
    }),
    button('Kapat', { onClick: () => overlay.remove() }),
  );

  const body = h('div', 'cr-drawer-body', 'Yükleniyor…');
  card_.append(body);
  overlay.append(card_);
  overlay.addEventListener('mousedown', (event) => {
    if (event.target === overlay) overlay.remove();
  });
  nodes.root.append(overlay);

  try {
    const detail = await api(
      `/api/bbd_canteen_reports/students/${encodeURIComponent(row.studentId)}`
      + `?start=${range.start}&end=${range.end}`);
    body.replaceChildren();

    const tiles = h('div', 'cr-mini');
    const mini = (label, value, tone = '') => {
      const box = h('div', 'cr-mini-tile');
      box.append(h('span', 'cr-mini-label', label), h('b', `cr-mini-value${tone ? ` ${tone}` : ''}`, value));
      return box;
    };
    tiles.append(
      mini('Toplam harcama', money(row.total)),
      mini('İşlem', String(row.count)),
      mini('Ortalama sepet', money(row.average)),
      mini('Güncel bakiye', money(row.balance), row.balance > 0 ? 'debt' : ''),
    );
    body.append(tiles);

    body.append(card('Günlük harcama', lineChart(
      (detail.byDay || []).map((item) => ({
        label: item.day.slice(8) + '.' + item.day.slice(5, 7), value: item.total,
      })))));

    body.append(card('Aldığı ürünler', barChart(
      (detail.products || []).map((item) => ({
        label: item.name, value: item.total, display: `${item.qty} adet · ${money(item.total)}`,
      })), { max: 15 })));

    // Eksik veri uyarısı çekmecede de görünsün: bu döküm "tüm işlemler" diye
    // okunuyor, eksikse bunu söylemek zorundayız.
    for (const warning of (detail.meta || {}).warnings || []) {
      body.append(h('div', 'cr-alert bad', warning));
    }

    const entries = detail.transactions || [];
    const counts = detail.counts || {};
    const table = h('div', 'cr-table');
    table.append(headRow(['Tarih', 'Tür', 'Ürünler / açıklama', 'Tutar'], 'cr-row-tx'));

    const parts = [`${entries.length} satır`];
    if (counts.sale) parts.push(`${counts.sale} satış`);
    if (counts.reversed) parts.push(`${counts.reversed} iptal`);
    if (counts.collection) parts.push(`${counts.collection} tahsilat`);
    body.append(card(`İşlem dökümü (${parts.join(' · ')})`, table,
      'Aralıktaki TÜM hareketler: satış, iptal ve tahsilat. Üstteki özet ile ürün '
      + 'kırılımı yalnız geçerli satışlardan hesaplanır.'));
    // Tablo DOM'a girdikten SONRA doldurulur: parçalı çizim "hâlâ ekranda mı"
    // sorusunu `isConnected` ile soruyor, bağlanmadan önce başlarsa ilk
    // dilimden sonra durur.
    fillEntries(table, entries);
  } catch (error) {
    body.textContent = `Döküm alınamadı: ${error.message}`;
  }
}

const ENTRY_LABELS = { sale: 'Satış', reversed: 'İptal', collection: 'Tahsilat' };
const COLLECTION_METHODS = {
  cash: 'Nakit tahsilat', link: 'Ödeme linki', online: 'Ödeme linki', card: 'Kart',
};

/** Döküm satırının açıklama sütunu — türüne göre değişir. */
function entryText(item) {
  if (item.kind === 'collection') {
    return COLLECTION_METHODS[String(item.method || '').toLowerCase()] || 'Tahsilat';
  }
  const lines = (item.items || []).map((entry) => `${entry.qty}× ${entry.name}`).join(', ');
  if (item.kind !== 'reversed') return lines;
  return item.reversedReason ? `${lines} — iptal gerekçesi: ${item.reversedReason}` : lines;
}

/**
 * Dökümü tabloya PARÇA PARÇA çizer.
 *
 * SATIR SINIRI YOK: eskiden yalnız ilk 200 satır çiziliyordu ama başlık
 * toplam sayıyı yazdığı için kullanıcı gerisini boşuna arıyordu. Bir yıllık
 * aralıkta binlerce satır tek turda eklenince arayüz donuyor; bu yüzden
 * ilk dilim hemen, kalanı sonraki karelerde eklenir — liste eksiksizdir,
 * pencere yine de anında açılır.
 */
function fillEntries(table, entries, chunk = 150) {
  let index = 0;
  const step = () => {
    const slice = entries.slice(index, index + chunk);
    const fragment = document.createDocumentFragment();
    for (const item of slice) {
      const line = h('div', `cr-row cr-row-tx tx-${item.kind || 'sale'}`);
      line.append(
        h('span', 'cr-cell', stamp(item.createdAt)),
        h('span', `cr-cell cr-kind kind-${item.kind || 'sale'}`,
          ENTRY_LABELS[item.kind] || 'Satış'),
        h('span', 'cr-cell', entryText(item)),
        h('span', 'cr-cell num', money(item.total)),
      );
      fragment.append(line);
    }
    table.append(fragment);
    index += chunk;
    // Tablo DOM'dan koparılmışsa (çekmece kapandı) çizmeye devam etme.
    if (index < entries.length && table.isConnected) requestAnimationFrame(step);
  };
  step();
  if (entries.length === 0) table.append(h('div', 'cr-empty', 'Bu aralıkta hareket yok.'));
}

// ------------------------------------------------------------- çıktı

/**
 * Rapor üretir ve ÖNİZLEME penceresinde açar.
 *
 * Önizleme, basılacak PDF'in birebir kendisidir: sunucu dosyayı üretip
 * sayfalarını görüntüye çevirir. Ekranda görülen ile kâğıttan çıkan aynıdır.
 */
async function exportFile(kind, extra = {}) {
  setStatus('Rapor üretiliyor…');
  let result;
  try {
    result = await api('/api/bbd_canteen_reports/preview', {
      method: 'POST',
      body: { kind, start: range.start, end: range.end,
        classes: Object.fromEntries(classMap), ...extra },
    });
  } catch (error) {
    toast(error.message, 'bad');
    setStatus(error.message, true);
    return;
  }

  if (!result.ok) {
    toast(result.error || 'Üretilemedi.', 'bad');
    setStatus(result.error, true);
    return;
  }

  toast(`${result.name} hazır (${Math.round(result.bytes / 1024)} KB).`, 'good');
  setStatus(`Dosya: ${result.path}`);
  nodes.lastFile.replaceChildren(
    h('span', undefined, 'Son üretilen: '),
    h('code', 'cr-path', result.path),
  );
  openPreview(result);
}

/** Bu CİHAZIN yazıcısını okur; basılamıyorsa sebebini döner.
 *
 *  Sunucunun `/printer` ucu SORULMAZ: o sunucudaki kuyrukları anlatıyor ve
 *  sunucuda yazıcı yok. Kâğıt kullanıcının masasında çıkıyor. */
async function printerState() {
  const status = await printerReady();
  return status.ready
    ? { ready: true, target: { name: status.name } }
    : { ready: false, error: status.error };
}

/** Üretilen raporun önizleme penceresi + yazdır düğmesi. */
function openPreview(result) {
  const overlay = h('div', 'cr-overlay');
  const card = h('div', 'cr-preview');
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');

  const close = () => {
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  // --- başlık şeridi ---
  const bar = h('div', 'cr-preview-bar');
  bar.append(
    h('b', 'cr-preview-title', result.name),
    h('span', 'cr-preview-sub',
      result.pageCount
        ? `${result.pageCount} sayfa${result.shown < result.pageCount
          ? ` · ilk ${result.shown} sayfa gösteriliyor` : ''}`
        : ''),
    h('span', 'cr-spacer'),
  );

  const printBtn = button('Yazdır', {
    variant: 'primary',
    title: 'Raporu USB\'ye bağlı yazıcıya doğrudan gönderir.',
  });
  const note = h('span', 'cr-preview-note');
  bar.append(note, printBtn, button('Kapat', { onClick: close }));

  printBtn.addEventListener('click', async () => {
    printBtn.disabled = true;
    note.textContent = 'Yazıcıya gönderiliyor…';
    note.classList.remove('bad');
    try {
      // Eskiden `/api/bbd_canteen_reports/print` çağrılıyordu: o uç SUNUCUDAKİ
      // CUPS'a gidiyor, sunucuda CUPS kurulu değil ve düğme her tıklamada
      // "sistemde CUPS (lp/lpstat) bulunamadı" diyordu.
      const printer = await printDocument(api, result.path, { copies: 1 });
      note.textContent = `${printer} yazıcısına gönderildi.`;
      toast(`Yazdırılıyor: ${printer}`, 'good');
    } catch (error) {
      note.textContent = error.message;
      note.classList.add('bad');
      toast(error.message, 'bad');
    } finally {
      printBtn.disabled = false;
    }
  });

  // --- sayfalar ---
  const pages = h('div', 'cr-preview-pages');
  if (result.previewError) {
    pages.append(h('div', 'cr-alert bad', result.previewError));
  } else if (!(result.pages || []).length) {
    pages.append(h('div', 'cr-hint', 'Bu çıktı için önizleme üretilmedi.'));
  } else {
    for (const [index, src] of result.pages.entries()) {
      const page = h('figure', 'cr-preview-page');
      const image = h('img');
      image.src = src;
      image.alt = `Sayfa ${index + 1}`;
      image.loading = 'lazy';
      page.append(image, h('figcaption', undefined, `Sayfa ${index + 1}`));
      pages.append(page);
    }
  }

  card.append(bar, pages);
  overlay.append(card);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey, true);
  nodes.root.append(overlay);

  // Yazıcı bağlı değilse düğme bunu ÖNCEDEN söylesin; kullanıcı tıklayıp
  // hata almak yerine durumu baştan görsün.
  printerState().then((status) => {
    if (!status.ready) {
      note.textContent = status.error || 'Yazıcı bağlantısı hatası.';
      note.classList.add('bad');
      printBtn.disabled = true;
      return;
    }
    // TONER VE CİHAZ UYARISI ARTIK OKUNMUYOR: onları sunucudaki `ipp-usb`
    // sorgusu üretiyordu ve sunucu yazıcıyı görmüyor. Yazıcının kendi durumu
    // yerel kuyruktan sorulabilir, ama bu ayrı bir iştir; ekran şimdi yalnız
    // bildiğini söylüyor — uydurulmuş bir "hazır" rozetinden iyidir.
    note.textContent = status.target?.name || 'Yazıcı';
  });
}

// ------------------------------------------------------------------ mount

function preset(days) {
  return () => {
    range = { start: todayIso(-(days - 1)), end: todayIso() };
    nodes.start.set(range.start);
    nodes.end.set(range.end);
    load();
  };
}

export function mount(root, ctx) {
  api = ctx.api;
  capability = ctx.capability;

  const view = h('div', 'cr');
  nodes.root = view;
  toast = toaster(view);

  // --- şerit ---
  const bar = h('div', 'cr-bar');
  // Yerli `<input type=date>` WebKitGTK'da açılır takvimi kapatmıyordu; alan
  // takılı kalıyordu. Kendi takvimimiz kullanılıyor.
  nodes.start = dateField({
    value: range.start, label: 'Başlangıç tarihi',
    onChange: (iso) => { range.start = iso; },
  });
  nodes.end = dateField({
    value: range.end, label: 'Bitiş tarihi',
    onChange: (iso) => { range.end = iso; },
  });

  bar.append(
    h('span', 'cr-field-label', 'Aralık'),
    nodes.start.node, h('span', 'cr-dash', '–'), nodes.end.node,
    button('Getir', { variant: 'primary', onClick: () => load() }),
    h('span', 'cr-presets'),
  );
  const presets = bar.lastChild;
  for (const [label, days] of [['Bugün', 1], ['7 gün', 7], ['30 gün', 30], ['90 gün', 90]]) {
    const chip = h('button', 'cr-chip', label);
    chip.type = 'button';
    chip.addEventListener('click', preset(days));
    presets.append(chip);
  }
  bar.append(h('span', 'cr-spacer'),
    h('span', 'cr-live', 'veri kantinden canlı'),
    button('Özet raporu', {
      variant: 'primary',
      title: 'Raporu üretir, önizlemede açar; oradan yazdırabilirsiniz.',
      onClick: () => exportFile('summary_pdf'),
    }));

  nodes.status = h('div', 'cr-status');
  nodes.lastFile = h('div', 'cr-lastfile');
  nodes.kpi = h('div', 'cr-kpi');

  const tabBar = h('div', 'cr-tabs');
  nodes.tabs = {};
  for (const [key, label] of [
    ['overview', 'Genel bakış'], ['students', 'Öğrenci bazlı'],
    ['products', 'Ürün bazlı'], ['classes', 'Sınıf bazlı'],
  ]) {
    const node = h('button', 'cr-tab', label);
    node.type = 'button';
    node.addEventListener('click', () => { tab = key; render(); });
    nodes.tabs[key] = node;
    tabBar.append(node);
  }

  nodes.body = h('div', 'cr-body');
  view.append(bar, nodes.status, nodes.kpi, tabBar, nodes.body, nodes.lastFile);
  root.replaceChildren(view);

  render();
  (async () => {
    await loadClasses();
    await load();
  })();

  return () => {
    nodes.start?.destroy?.();
    nodes.end?.destroy?.();
    root.replaceChildren();
    data = null;
    openStudent = null;
    busy = false;
  };
}
