// Raporlar paneli — 7 dallı hiyerarşik ağaç, 35 rapor, tek PDF'te rapor paketi.
//
// NE YAPAR: solda aranabilir ve KLAVYEYLE gezilen rapor ağacı (↑↓ gez · → aç ·
// ← kapat · Enter seç · Space pakete ekle); sağda yaprağa göre değişen
// parametre şeridi → KPI → grafik → tablo → eylem çubuğu. Birden çok yaprak
// seçilince hepsi TEK PDF'te birleşir: kapak + içindekiler + her rapor kendi
// sayfasında.
//
// NE YAPMAZ:
//  · Rakamı istemcide HESAPLAMAZ. Ekranda görülen sayı ile PDF'teki sayı aynı
//    yerden (sunucudaki tek üreteçten) gelir; iki ayrı hesap zamanla ayrışır
//    ve rapor ekranında bu, güvenin tamamen kaybolması demektir.
//  · Ağacın tamamını önden ÇALIŞTIRMAZ. 35 raporu birden üretmek dakikada 60
//    istek sınırını tek açılışta tüketirdi; yalnız seçilen yaprak koşar.
//  · Rapor SİLMEZ. Kaydedilmiş rapor arşivlenir, satır durur.
//
// HATA YAPRAK BAZLIDIR: bir rapor patlarsa yalnız sağ taraf hata kartı
// gösterir; ağaç ayakta kalır ve başka bir rapor seçilebilir (K7).
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_reports/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_reports/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, confirmWithReason, foldText, h, loadStyles, num, todayIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { choiceFilter } from '../../ui-kit/choice.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, skeletonRows, splitView,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import {
  barChart, groupedBar, hourStrip, lineChart, paretoChart, stackedBar,
} from '../../ui-kit/charts.js';
import { reportChain } from '../../ui-kit/report.js';
// BASKI CİHAZDA YAPILIR — gerekçe `ui-kit/printing.js` başlığında.
import { printDocument } from '../../ui-kit/printing.js';

const BASE = '/api/store_reports';

const WEEKDAYS = ['Pazartesi', 'Salı', 'Çarşamba', 'Perşembe', 'Cuma', 'Cumartesi', 'Pazar'];

const EMPTY = {
  branches: [],
  search: [],
  defaults: null,
  reference: { channels: [], customerGroups: [], categories: [], carriers: [] },
  open: new Set(),        // açık dallar
  package: new Set(),     // pakete işaretlenmiş yapraklar
  active: '',             // seçili yaprak
  preset: null,           // kaydedilmiş rapordan gelen parametreler (bir kez uygulanır)
  result: null,
  packageLimit: 12,
  connected: false,
  error: '',
  loaded: false,
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY, open: new Set(), package: new Set() };

// Açık diyalogların kapatıcıları. Panel kapanırken diyalog açıksa `document`
// üzerindeki `keydown` dinleyicisi ORADA KALIR ve her açılışta bir tane daha
// birikir; sözü verilen Promise de hiç çözülmezdi (kit kuralı 4).
const dialogs = new Set();

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

function leafOf(key) {
  for (const branch of state.branches) {
    const found = (branch.children || []).find((item) => item.key === key);
    if (found) return { ...found, branch: branch.label };
  }
  return null;
}

function statusText() {
  if (!state.loaded) return 'Rapor ağacı alınıyor…';
  const leaves = state.branches.reduce((total, branch) => total + branch.count, 0);
  const chosen = state.package.size ? ` · pakette ${state.package.size} rapor` : '';
  if (!state.connected) return `Mağaza geçidi okunamadı — ${state.error}${chosen}`;
  return `${state.branches.length} dal · ${leaves} rapor${chosen}`;
}

// ------------------------------------------------------------------- veri

async function loadTree() {
  let payload;
  try {
    payload = await api(`${BASE}/tree`);
  } catch (error) {
    state.error = error.message;
    nodes.tree.replaceChildren(alertBox(
      `Rapor ağacı alınamadı — ${error.message}`, 'bad'));
    nodes.status.set(error.message, true);
    return;
  }
  state.branches = payload.branches || [];
  state.search = payload.search || [];
  state.defaults = payload.defaults || null;
  state.packageLimit = payload.packageLimit || 12;
  state.connected = Boolean(payload.connected);
  state.error = payload.error || '';
  state.loaded = true;
  // Açılışta yalnız ilk dal açık: yedi dal birden açık, ekranı 35 satırla
  // doldurup aramayı zorunlu kılıyordu.
  if (!state.open.size && state.branches.length) state.open.add(state.branches[0].key);
  renderTree();
  nodes.status.set(statusText(), !state.connected);
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
    channels: payload.channels || [],
    customerGroups: payload.customerGroups || [],
    categories: payload.categories || [],
    carriers: payload.carriers || [],
  };
  if (state.active) buildParamBar(leafOf(state.active));
}

// -------------------------------------------------------------------- ağaç

/** Aramaya uyan yaprak anahtarları; arama boşsa `null` (hepsi görünür). */
function searchHits() {
  const needle = foldText(nodes.search?.value || '');
  if (!needle) return null;
  return new Set(state.search
    .filter((item) => foldText(item.text).includes(needle))
    .map((item) => item.key));
}

/** Ekranda O AN görünen düğümler — klavye gezintisi bu sırayı izler. */
function visibleNodes() {
  const hits = searchHits();
  const out = [];
  for (const branch of state.branches) {
    const children = (branch.children || [])
      .filter((leaf) => !hits || hits.has(leaf.key));
    if (hits && children.length === 0) continue;
    out.push({ kind: 'branch', key: branch.key, label: branch.label, count: children.length });
    // Arama varken dal KENDİLİĞİNDEN açılır: eşleşen raporu görmek için
    // kullanıcının ayrıca dalı açması gerekseydi arama işe yaramazdı.
    if (!hits && !state.open.has(branch.key)) continue;
    for (const leaf of children) out.push({ kind: 'leaf', ...leaf, branch: branch.label });
  }
  return out;
}

function renderTree() {
  const rows = visibleNodes();
  const host = nodes.tree;
  host.replaceChildren();
  host.setAttribute('role', 'tree');
  host.setAttribute('aria-label', 'Rapor ağacı');

  if (!rows.length) {
    host.append(emptyState({
      title: 'Aramaya uyan rapor yok',
      text: 'Başka bir sözcük deneyin ya da aramayı temizleyin.',
      actions: [button('Aramayı temizle', {
        onClick: () => { nodes.search.value = ''; renderTree(); },
      })],
    }));
    return;
  }

  for (const row of rows) {
    if (row.kind === 'branch') {
      const open = state.open.has(row.key) || Boolean(searchHits());
      const node = h('div', `rp-branch${open ? ' on' : ''}`);
      node.setAttribute('role', 'treeitem');
      node.setAttribute('aria-expanded', open ? 'true' : 'false');
      node.dataset.key = row.key;
      node.dataset.kind = 'branch';
      node.tabIndex = -1;
      node.append(h('span', 'rp-caret', open ? '▾' : '▸'));
      node.append(h('span', 'rp-branch-label', row.label));
      node.append(h('span', 'rp-count', String(row.count)));
      node.addEventListener('click', () => toggleBranch(row.key));
      host.append(node);
      continue;
    }

    const node = h('div', `rp-leaf${state.active === row.key ? ' on' : ''}`
      + `${row.available ? '' : ' pending'}`);
    node.setAttribute('role', 'treeitem');
    node.setAttribute('aria-selected', state.active === row.key ? 'true' : 'false');
    node.dataset.key = row.key;
    node.dataset.kind = 'leaf';
    node.tabIndex = -1;
    node.title = row.available ? row.hint : row.reason;

    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = state.package.has(row.key);
    check.disabled = !row.available;
    check.title = 'Rapor paketine ekle';
    check.addEventListener('click', (event) => {
      event.stopPropagation();
      togglePackage(row.key);
    });
    node.append(check, h('span', 'rp-leaf-label', row.label));
    // Ucu olmayan rapor GİZLENMEZ: durumu yazıyla söylenir, renkle değil.
    if (!row.available) node.append(badge('uç bekleniyor', 'dim'));
    node.addEventListener('click', () => selectLeaf(row.key));
    host.append(node);
  }

  const focusable = host.querySelector('[data-key][data-kind]');
  if (focusable) focusable.tabIndex = 0;
  const current = host.querySelector('.rp-leaf.on');
  if (current) {
    if (focusable) focusable.tabIndex = -1;
    current.tabIndex = 0;
  }
  nodes.packageBar.classList.toggle('on', state.package.size > 0);
  nodes.packageCount.textContent = `${state.package.size} rapor seçildi`;
  nodes.status?.set(statusText(), !state.connected);
}

function toggleBranch(key) {
  if (state.open.has(key)) state.open.delete(key);
  else state.open.add(key);
  renderTree();
}

function togglePackage(key) {
  if (state.package.has(key)) {
    state.package.delete(key);
  } else if (!leafOf(key)?.available) {
    // FARE ile eklenemiyordu (kutu `disabled`), KLAVYE ile eklenebiliyordu:
    // Space ucu olmayan yaprağı pakete sokuyor, kutu devre dışıyken işaretli
    // görünüyor ve paket PDF'i "üretilemedi" notuyla çıkıyordu. İki yol da
    // aynı cevabı vermeli ve nedenini SÖYLEMELİ.
    toast(leafOf(key)?.reason || 'Bu rapor henüz çalıştırılamıyor.', 'warn');
    return;
  } else if (state.package.size >= state.packageLimit) {
    toast(`Tek pakette en çok ${state.packageLimit} rapor birleştirilir.`, 'warn');
    return;
  } else {
    state.package.add(key);
  }
  renderTree();
}

/** Ağaç klavyesi: ↑↓ gez · → aç · ← kapat · Enter seç · Space pakete ekle. */
function onTreeKey(event) {
  const rows = [...nodes.tree.querySelectorAll('[data-key][data-kind]')];
  if (!rows.length) return;
  const index = rows.indexOf(document.activeElement);
  const focus = (target) => {
    if (!target) return;
    rows.forEach((node) => { node.tabIndex = -1; });
    target.tabIndex = 0;
    target.focus();
  };

  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      focus(rows[Math.min(rows.length - 1, index + 1)] || rows[0]);
      break;
    case 'ArrowUp':
      event.preventDefault();
      focus(rows[Math.max(0, index - 1)] || rows[0]);
      break;
    case 'Home':
      event.preventDefault();
      focus(rows[0]);
      break;
    case 'End':
      event.preventDefault();
      focus(rows[rows.length - 1]);
      break;
    case 'ArrowRight': {
      const node = rows[index];
      if (!node || node.dataset.kind !== 'branch') return;
      event.preventDefault();
      if (!state.open.has(node.dataset.key)) {
        state.open.add(node.dataset.key);
        renderTree();
        // Çizim sonrası aynı dal yeniden bulunur: düğüm nesnesi değişti.
        focusKey(node.dataset.key);
      }
      break;
    }
    case 'ArrowLeft': {
      const node = rows[index];
      if (!node) return;
      event.preventDefault();
      if (node.dataset.kind === 'branch') {
        state.open.delete(node.dataset.key);
        renderTree();
        focusKey(node.dataset.key);
      } else {
        // Yapraktan sola basmak dalına çıkarır — ağaç deseninin gereği.
        const parent = [...nodes.tree.querySelectorAll('[data-kind="branch"]')]
          .filter((item) => rows.indexOf(item) < index).pop();
        focus(parent);
      }
      break;
    }
    case 'Enter': {
      const node = rows[index];
      if (!node) return;
      event.preventDefault();
      if (node.dataset.kind === 'branch') toggleBranch(node.dataset.key);
      else selectLeaf(node.dataset.key);
      focusKey(node.dataset.key);
      break;
    }
    case ' ': {
      const node = rows[index];
      if (!node || node.dataset.kind !== 'leaf') return;
      event.preventDefault();
      togglePackage(node.dataset.key);
      focusKey(node.dataset.key);
      break;
    }
    default:
      break;
  }
}

function focusKey(key) {
  const target = nodes.tree.querySelector(`[data-key="${key}"]`);
  if (target) {
    target.tabIndex = 0;
    target.focus();
  }
}

// ------------------------------------------------------- parametre şeridi

/** Yaprağın İSTEDİĞİ alanlar çizilir. Kargo raporunda "kategori" sormak
 *  kullanıcıyı raporun onu dinlediğine inandırırdı. */
function buildParamBar(leaf) {
  const wanted = new Set(leaf?.params || ['range']);
  const current = nodes.params ? nodes.params.values() : {};
  // Kaydedilmiş rapor AÇILDIĞINDA kendi parametreleriyle açılmalı. Şeridi o
  // anki değerlerle kurmak, kullanıcıya "Haftalık satış"ı açtırıp başka bir
  // tarih aralığının rakamlarını göstermek olurdu — ve hiçbir yerde yazmazdı.
  const saved = state.preset;
  const previous = saved
    ? { ...current, range: { start: saved.start || '', end: saved.end || '' },
        granularity: saved.granularity, compare: saved.compare, channel: saved.channel,
        customerGroup: saved.customerGroup, category: saved.category, carrier: saved.carrier }
    : current;
  state.preset = null;
  const defaults = state.defaults || {};
  nodes.params?.destroy();

  const fields = [];
  if (wanted.has('range')) {
    fields.push({
      kind: 'dateRange',
      key: 'range',
      label: 'Aralık',
      start: previous.range?.start || defaults.start || todayIso(-29),
      end: previous.range?.end || defaults.end || todayIso(),
    });
  }
  if (wanted.has('granularity')) {
    fields.push({
      kind: 'select', key: 'granularity', label: 'Kırılım',
      value: previous.granularity || 'day',
      options: [{ value: 'day', label: 'Gün' }, { value: 'week', label: 'Hafta' },
        { value: 'month', label: 'Ay' }],
    });
  }
  if (wanted.has('channel')) {
    // TEK KANALLI MAĞAZADA KUTU ÇİZİLMEZ. `choiceFilter` seçenek sayısına
    // bakar (`choice.js`): birden çoksa kutu geri gelir, tek/sıfırsa `null`
    // döner ve şeride hiç eklenmez. Rapor parametresi olarak da GÖNDERİLMEZ —
    // tek kanallı mağazada hiçbir satır elemeyen bir süzgeç, mağazanın kanal
    // parametresini yanlış yorumlaması hâlinde raporu sessizce boşaltabilir.
    const field = choiceFilter({
      key: 'channel', label: 'Kanal', allLabel: 'Tümü — kanal',
      value: previous.channel || '', options: state.reference.channels,
    });
    if (field) fields.push(field);
  }
  if (wanted.has('customerGroup')) {
    fields.push({
      kind: 'select', key: 'customerGroup', label: 'Müşteri grubu',
      value: previous.customerGroup || '',
      options: [{ value: '', label: 'Tümü — grup' },
        ...state.reference.customerGroups.map((item) => ({ value: item, label: item }))],
    });
  }
  if (wanted.has('category')) {
    fields.push({
      kind: 'select', key: 'category', label: 'Kategori', value: previous.category || '',
      options: [{ value: '', label: 'Tümü — kategori' },
        ...state.reference.categories.map((item) => ({ value: item, label: item }))],
    });
  }
  if (wanted.has('carrier')) {
    fields.push({
      kind: 'select', key: 'carrier', label: 'Taşıyıcı', value: previous.carrier || '',
      options: [{ value: '', label: 'Tümü — taşıyıcı' },
        ...state.reference.carriers.map((item) => ({ value: item, label: item }))],
    });
  }
  if (wanted.has('compare')) {
    fields.push({
      kind: 'select', key: 'compare', label: 'Karşılaştırma', value: previous.compare || '',
      options: [{ value: '', label: 'Yok' }, { value: 'previous', label: 'Önceki dönem' },
        { value: 'year', label: 'Geçen yıl aynı dönem' }],
    });
  }

  nodes.params = filterBar({
    fields,
    // Parametre değişince rapor KENDİLİĞİNDEN koşmaz: 35 raporun her biri
    // mağazaya birkaç istek demek. Kullanıcı [Ekranda göster] ile ister.
    onChange: () => nodes.status.set('Parametre değişti — [Ekranda göster] ile yenileyin.'),
    actions: [button('Ekranda göster', { variant: 'primary', onClick: runActive })],
  });
  nodes.paramHost.replaceChildren(nodes.params.node);
}

function currentParams() {
  const values = nodes.params ? nodes.params.values() : {};
  return {
    start: values.range?.start || '',
    end: values.range?.end || '',
    granularity: values.granularity || 'day',
    compare: values.compare || '',
    channel: values.channel || '',
    customerGroup: values.customerGroup || '',
    category: values.category || '',
    carrier: values.carrier || '',
  };
}

// ------------------------------------------------------------ rapor koşma

function selectLeaf(key) {
  const leaf = leafOf(key);
  if (!leaf) return;
  state.active = key;
  state.result = null;
  renderTree();
  buildParamBar(leaf);
  renderWorkspace();
  if (leaf.available) runActive();
}

async function runActive() {
  if (!state.active) return;
  const leaf = leafOf(state.active);
  if (!leaf) return;
  if (!leaf.available) {
    state.result = { ok: false, error: leaf.reason, pending: true };
    renderWorkspace();
    return;
  }
  nodes.output.replaceChildren(skeletonRows(8, 5));
  await withBusy(`${leaf.label} hesaplanıyor…`, async () => {
    const payload = await api(`${BASE}/run`, {
      method: 'POST', body: { leaf: state.active, ...currentParams() },
    });
    state.result = payload;
    renderWorkspace();
    nodes.status.set(payload.ok
      ? `${leaf.branch} · ${leaf.label} hazır.`
      : payload.error, !payload.ok);
    return payload;
  });
}

// ------------------------------------------------------------------ çizim

function chartNode(chart) {
  if (!chart || !chart.kind || !(chart.data || []).length) return null;
  const data = chart.data;
  switch (chart.kind) {
    case 'line': return lineChart(data);
    case 'bar': return barChart(data);
    case 'hours': return hourStrip(data);
    case 'pareto': return paretoChart(data);
    case 'stacked': return stackedBar(data);
    case 'grouped': return groupedBar(data, { series: chart.series || [] });
    default: return null;
  }
}

function tableNode(table) {
  if (!table || !(table.headers || []).length) return null;
  const align = table.align || '';
  const columns = table.headers.map((label, index) => ({
    key: `c${index}`,
    label,
    align: align[index] === 'R' ? 'num' : '',
    width: index === 1 ? 'minmax(0, 2.4fr)' : 'minmax(0, 1fr)',
  }));
  const rows = (table.rows || []).map((cells, index) => {
    const row = { id: `r${index}` };
    cells.forEach((cell, position) => { row[`c${position}`] = cell; });
    return row;
  });
  return dataTable({
    columns,
    rows,
    dense: rows.length > 40,
    empty: emptyState({ title: 'Bu aralıkta veri yok', text: 'Tarih aralığını genişletin.' }),
  }).node;
}

function renderWorkspace() {
  const host = nodes.output;
  host.replaceChildren();

  if (!state.active) {
    host.append(emptyState({
      title: 'Soldan bir rapor seçin',
      text: 'Ağaçta ↑↓ ile gezin, → ile dalı açın, Enter ile raporu çalıştırın. '
        + 'Birden çok raporu Space ile işaretleyip tek PDF alabilirsiniz.',
    }));
    return;
  }

  const leaf = leafOf(state.active);
  const head = h('div', 'rp-head');
  head.append(h('h3', 'rp-title', `${leaf.branch} · ${leaf.label}`));
  head.append(h('span', 'rp-hint', leaf.hint));
  host.append(head);

  if (!leaf.available) {
    host.append(alertBox(leaf.reason, 'warn'));
    host.append(hintBox('Bu rapor ağaçta kalır: uç yayınlandığında ekstra bir '
      + 'ayar yapmadan çalışmaya başlar.'));
    return;
  }

  const result = state.result;
  if (!result) {
    host.append(skeletonRows(6, 5));
    return;
  }
  if (!result.ok) {
    host.append(card('Rapor üretilemedi',
      h('div', 'rp-error', result.error || 'Bilinmeyen hata.'),
      'Ağaçtaki diğer raporlar çalışmaya devam eder'));
    host.append(button('Tekrar dene', { variant: 'primary', onClick: runActive }));
    return;
  }

  if ((result.kpis || []).length) host.append(kpiRow(result.kpis));

  const chart = chartNode(result.chart);
  if (chart) host.append(card(result.chart.title || 'Grafik', chart));

  const table = tableNode(result.table);
  if (table) {
    const hint = result.table.truncated
      ? `İlk ${result.table.rows.length} satır gösteriliyor` : '';
    host.append(card(result.table.title || 'Ayrıntı', table, hint));
  }

  if ((result.notes || []).length) {
    const notes = h('ul', 'rp-notes');
    for (const note of result.notes) notes.append(h('li', undefined, note));
    host.append(card('Yöntem', notes, 'Rakamın nereden geldiği'));
  }
}

// --------------------------------------------------------------- eylemler

function reportParams(extra = {}) {
  return { ...currentParams(), ...extra };
}

async function exportCsv() {
  if (!state.active) return;
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST', body: { leaf: state.active, ...currentParams() },
    });
    toast(`${result.name} yazıldı (${num(result.rows)} satır).`, 'good');
    nodes.lastFile.set(result.path);
    return result;
  });
}

/** [Yazdır]: üret → BU CİHAZIN yazıcısına bas. Önizleme açılmaz; önizlemeli
 *  akış [PDF] düğmesindedir.
 *
 *  BASKI SUNUCUDA DEĞİL BURADA YAPILIR. Eskiden `${BASE}/print` çağrılıyordu:
 *  o uç sunucudaki CUPS'a gidiyor, sunucu imajında CUPS yok ("sunucuda yazıcı
 *  ve hoparlör yok" — `deploy/server/Dockerfile`) ve düğme her tıklamada
 *  "sistemde CUPS (lp/lpstat) bulunamadı" diyordu. Önizleme penceresindeki
 *  Yazdır (`ui-kit/report.js`) çoktan yerel yola geçmişti; bu düğme geride
 *  kalmıştı ve aynı raporun iki yazdırma yolundan biri çalışıyordu. */
async function printDirect() {
  if (!state.active) return;
  const pending = leafOf(state.active);
  if (pending && !pending.available) { toast(pending.reason, 'warn'); return; }
  await withBusy('Rapor hazırlanıp yazıcıya gönderiliyor…', async () => {
    const status = await report.printerState();
    if (!status?.ready) {
      toast(status?.error || 'Yazıcıya ulaşılamıyor.', 'bad');
      return null;
    }
    const produced = await call(`${BASE}/preview`, {
      method: 'POST', body: { kind: state.active, ...reportParams() },
    });
    nodes.lastFile.set(produced.path);
    const printer = await printDocument(api, produced.path, { copies: 1 });
    toast(`${printer} yazıcısına gönderildi.`, 'good');
    return { printer };
  });
}

async function savePreset() {
  const keys = state.package.size ? [...state.package] : (state.active ? [state.active] : []);
  if (!keys.length) {
    toast('Önce bir rapor seçin.', 'warn');
    return;
  }
  const name = await askText({
    title: 'Raporu kaydet',
    description: `${keys.length} rapor ve şu anki parametreler bir adla saklanır. `
      + 'Mağazaya hiçbir şey yazılmaz.',
    placeholder: 'Rapor adı (ör. Haftalık satış paketi)',
    confirmLabel: 'Kaydet',
  });
  if (!name) return;
  await withBusy('Kaydediliyor…', async () => {
    await call(`${BASE}/saved`, {
      method: 'POST', body: { name, leaves: keys, ...currentParams() },
    });
    toast(`“${name}” kaydedildi.`, 'good');
    if (nodes.tabs.active === 'saved') renderSaved();
    return true;
  });
}

async function schedulePreset() {
  const keys = state.package.size ? [...state.package] : (state.active ? [state.active] : []);
  if (!keys.length) {
    toast('Önce bir rapor seçin.', 'warn');
    return;
  }
  const plan = await askSchedule(keys.length);
  if (!plan) return;
  await withBusy('Zamanlanıyor…', async () => {
    const result = await call(`${BASE}/schedules`, {
      method: 'POST',
      body: { ...plan, leaves: keys, ...currentParams() },
    });
    toast(result.note || 'Zamanlandı.', 'good');
    if (nodes.tabs.active === 'schedules') renderSchedules();
    return result;
  });
}

function runPackage() {
  if (state.package.size < 2) {
    toast('Pakete en az iki rapor işaretleyin (Space).', 'warn');
    return;
  }
  report.run('package', {
    ...reportParams(),
    leaves: [...state.package],
    title: 'Mağaza rapor paketi',
  }).then((result) => {
    if (!result) return;
    nodes.lastFile.set(result.path);
    if ((result.failed || []).length) {
      toast(`${result.failed.length} rapor üretilemedi; PDF'te not olarak duruyor.`, 'warn');
    }
  });
}

// ------------------------------------------------------------- diyaloglar

/** Basit metin sorusu. `confirmWithReason` gerekçe içindir; ad sorarken
 *  "gerekçe" demek kullanıcıyı yanıltıyordu. */
function askText({ title, description, placeholder, confirmLabel }) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.append(h('h3', 'kit-dialog-title', title));
    if (description) box.append(h('p', 'kit-dialog-text', description));

    const input = h('input', 'kit-input');
    input.type = 'text';
    input.placeholder = placeholder || '';
    input.maxLength = 80;
    const error = h('div', 'kit-dialog-error');
    box.append(input, error);

    const close = (value) => {
      dialogs.delete(close);
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    dialogs.add(close);
    const submit = () => {
      const value = input.value.trim();
      if (value.length < 3) {
        error.textContent = 'En az 3 karakter yazın.';
        input.focus();
        return;
      }
      close(value);
    };
    const onKey = (event) => {
      if (event.key === 'Escape') close(null);
      if (event.key === 'Enter') submit();
    };

    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }),
      button(confirmLabel || 'Tamam', { variant: 'primary', onClick: submit }));
    box.append(actions);
    overlay.append(box);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    nodes.root.append(overlay);
    input.focus();
  });
}

function askSchedule(count) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog rp-dialog');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.append(h('h3', 'kit-dialog-title', 'Raporu zamanla'));
    box.append(h('p', 'kit-dialog-text',
      `${count} rapor belirlenen sıklıkta üretilip rapor klasörüne PDF olarak `
      + 'yazılır. E-posta gönderilmez.'));

    const name = h('input', 'kit-input');
    name.type = 'text';
    name.placeholder = 'Zamanlanmış raporun adı';
    name.maxLength = 80;

    const period = h('select', 'kit-select');
    for (const [value, label] of [['weekly', 'Her hafta'], ['monthly', 'Her ay']]) {
      const option = h('option', undefined, label);
      option.value = value;
      period.append(option);
    }

    const weekday = h('select', 'kit-select');
    WEEKDAYS.forEach((label, index) => {
      const option = h('option', undefined, label);
      option.value = String(index);
      weekday.append(option);
    });

    const dayOfMonth = h('select', 'kit-select');
    // 28'e kadar: 29-31 her ayda yok ve o aylarda rapor sessizce atlanırdı.
    for (let day = 1; day <= 28; day += 1) {
      const option = h('option', undefined, `Ayın ${day}. günü`);
      option.value = String(day);
      dayOfMonth.append(option);
    }
    dayOfMonth.hidden = true;

    const time = h('input', 'kit-input');
    time.type = 'text';
    time.value = '07:00';
    time.maxLength = 5;
    time.style.width = '80px';
    time.setAttribute('aria-label', 'Saat');

    period.addEventListener('change', () => {
      weekday.hidden = period.value !== 'weekly';
      dayOfMonth.hidden = period.value !== 'monthly';
    });

    const row = h('div', 'rp-dialog-row');
    row.append(period, weekday, dayOfMonth, h('span', 'kit-filter-label', 'Saat'), time);
    const error = h('div', 'kit-dialog-error');
    box.append(name, row, error);

    const close = (value) => {
      dialogs.delete(close);
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      resolve(value);
    };
    dialogs.add(close);
    const submit = () => {
      if (name.value.trim().length < 3) {
        error.textContent = 'Ad en az 3 karakter olmalı.';
        return;
      }
      if (!/^\d{2}:\d{2}$/.test(time.value.trim())) {
        error.textContent = 'Saat SS:DD biçiminde olmalı (ör. 07:00).';
        return;
      }
      close({
        name: name.value.trim(),
        period: period.value,
        weekday: Number(weekday.value),
        dayOfMonth: Number(dayOfMonth.value),
        atTime: time.value.trim(),
      });
    };
    const onKey = (event) => { if (event.key === 'Escape') close(null); };

    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }),
      button('Zamanla', { variant: 'primary', onClick: submit }));
    box.append(actions);
    overlay.append(box);
    overlay.addEventListener('mousedown', (event) => {
      if (event.target === overlay) close(null);
    });
    document.addEventListener('keydown', onKey);
    nodes.root.append(overlay);
    name.focus();
  });
}

// ------------------------------------------------- kaydedilmiş / zamanlı

async function renderSaved() {
  const host = nodes.body;
  host.replaceChildren(skeletonRows(4, 4));
  let payload;
  try {
    payload = await call(`${BASE}/saved`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.items.length) {
    host.append(emptyState({
      title: 'Kaydedilmiş rapor yok',
      text: 'Bir rapor seçip [Kaydet] dediğinizde adı ve parametreleri burada durur.',
    }));
    return;
  }
  for (const item of payload.items) {
    const body = h('div', 'rp-saved');
    body.append(h('div', 'rp-saved-leaves', item.labels.join(' · ')));
    body.append(h('div', 'rp-sub',
      `${item.params.start} — ${item.params.end} · ${item.actor} · ${item.createdAt}`));
    const tools = h('div', 'rp-actions');
    tools.append(button('Aç', {
      onClick: () => {
        state.package = new Set(item.leaves);
        state.active = item.leaves[0];
        state.preset = item.params || null;   // kaydedilen aralık/kırılım geri gelir
        nodes.tabs.select('report');
        selectLeaf(item.leaves[0]);
      },
    }));
    tools.append(button('PDF', {
      onClick: () => report.run(item.leaves.length > 1 ? 'package' : item.leaves[0], {
        ...item.params, leaves: item.leaves, title: item.name,
      }),
    }));
    tools.append(button('Arşivle', {
      variant: 'danger',
      onClick: async () => {
        const reason = await confirmWithReason(nodes.root, {
          title: `“${item.name}” arşivlensin mi?`,
          description: 'Kayıt SİLİNMEZ, listeden kalkar. Gerekçe denetim kaydına yazılır.',
          confirmLabel: 'Arşivle',
          minLength: 10,
          placeholder: 'Gerekçe (en az 10 karakter)',
        });
        if (!reason) return;
        await withBusy('Arşivleniyor…', async () => {
          await call(`${BASE}/saved/${item.id}/archive`, {
            method: 'POST', body: { reason },
          });
          toast('Arşivlendi.', 'good');
          renderSaved();
          return true;
        });
      },
    }));
    body.append(tools);
    host.append(card(item.name, body));
  }
}

async function renderSchedules() {
  const host = nodes.body;
  host.replaceChildren(skeletonRows(4, 4));
  let payload;
  try {
    payload = await call(`${BASE}/schedules`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  host.append(hintBox('Zamanlanmış rapor klasöre PDF yazar. Saat geçtiyse iş bir '
    + 'sonraki turda üretilir; aynı gün ikinci kez basılmaz.'));
  if (!payload.items.length) {
    host.append(emptyState({
      title: 'Zamanlanmış rapor yok',
      text: 'Bir rapor seçip [Zamanla] dediğinizde haftalık ya da aylık üretilir.',
    }));
    return;
  }
  for (const item of payload.items) {
    const body = h('div', 'rp-saved');
    const when = item.period === 'weekly'
      ? `Her ${WEEKDAYS[item.weekday] || '—'} ${item.atTime}`
      : `Ayın ${item.dayOfMonth}. günü ${item.atTime}`;
    body.append(h('div', 'rp-saved-leaves', item.labels.join(' · ')));
    body.append(h('div', 'rp-sub',
      `${when} · ${item.active ? 'Etkin' : 'Duraklatıldı'}`
      + `${item.lastRunAt ? ` · son: ${item.lastRunAt}` : ''}`));
    if (item.lastResult) body.append(h('div', 'rp-sub', item.lastResult));
    const tools = h('div', 'rp-actions');
    tools.append(badge(item.active ? 'Etkin' : 'Duraklatıldı', item.active ? 'good' : 'dim'));
    tools.append(button(item.active ? 'Duraklat' : 'Etkinleştir', {
      onClick: () => withBusy('Güncelleniyor…', async () => {
        await call(`${BASE}/schedules/${item.id}/toggle`, {
          method: 'POST', body: { active: !item.active },
        });
        renderSchedules();
        return true;
      }),
    }));
    body.append(tools);
    host.append(card(item.name, body));
  }
}

async function renderRuns() {
  const host = nodes.body;
  host.replaceChildren(skeletonRows(6, 5));
  let payload;
  try {
    payload = await call(`${BASE}/runs?limit=100`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.items.length) {
    host.append(emptyState({ title: 'Henüz rapor üretilmedi', text: 'Bir rapor alın.' }));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '170px' },
      { key: 'kindLabel', label: 'Rapor' },
      // Üreten SUNUCUDAN gelir; zamanlanmış üretim "Zamanlanmış: <ad>" yazar.
      // Kolon yokken elle alınan raporla zamanlayıcının bastığı ayırt
      // edilemiyordu — sütun boş değil, HİÇ YOKTU.
      { key: 'actor', label: 'Üreten', width: '190px' },
      { key: 'name', label: 'Dosya' },
      { key: 'state', label: 'Sonuç', width: '160px' },
    ],
    rows: payload.items.map((item, index) => ({
      id: `run${index}`,
      createdAt: item.createdAt,
      // Ham yaprak anahtarı (`sales_summary`) yerine ağacın Türkçe etiketi.
      kindLabel: item.kind === 'package'
        ? `Paket (${item.leaves.length})`
        : (item.labels || [])[0] || (item.kind === 'csv' ? 'CSV' : item.kind),
      actor: item.actor || '—',
      name: item.name || '—',
      state: item.ok ? 'Üretildi' : `Hata: ${item.error}`,
    })),
    dense: true,
  });
  host.append(card('Üretim geçmişi', table.node,
    'Ne üretmeye çalıştık — ağ koparsa kayıt yalnız burada kalır'));
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel rp');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });
  nodes.lastFile = report.lastFileLine();

  // ------------------------------------------------------------ sol sütun
  nodes.search = h('input', 'kit-input rp-search');
  nodes.search.type = 'search';
  nodes.search.placeholder = 'Raporlarda ara';
  nodes.search.setAttribute('aria-label', 'Raporlarda ara');
  nodes.search.addEventListener('input', () => renderTree());

  nodes.tree = h('div', 'rp-tree');
  nodes.tree.addEventListener('keydown', onTreeKey);

  nodes.packageCount = h('span', 'rp-package-count', '0 rapor seçildi');
  nodes.packageBar = h('div', 'rp-package');
  nodes.packageBar.append(
    nodes.packageCount,
    button('Paket PDF', { variant: 'primary', onClick: runPackage }),
    button('Temizle', {
      variant: 'ghost',
      onClick: () => { state.package.clear(); renderTree(); },
    }),
  );

  const left = h('div', 'rp-side');
  left.append(nodes.search, nodes.tree, nodes.packageBar);

  // ------------------------------------------------------------ sağ sütun
  nodes.paramHost = h('div', 'rp-params');
  nodes.output = h('div', 'rp-output');

  const actions = h('div', 'rp-toolbar');
  actions.append(
    button('PDF', { title: 'Önizle ve yazdır', onClick: () => {
      if (!state.active) { toast('Önce bir rapor seçin.', 'warn'); return; }
      // Ucu olmayan yaprakta üretim, içinde yalnız "üretilemedi" notu bulunan
      // boş bir PDF çıkarıyordu. Neden çalışmadığı ekranda YAZAR; kâğıda
      // dökülmez.
      const leaf = leafOf(state.active);
      if (leaf && !leaf.available) { toast(leaf.reason, 'warn'); return; }
      report.run(state.active, reportParams()).then((result) => {
        if (result) nodes.lastFile.set(result.path);
      });
    } }),
    button('CSV', { title: 'Tabloyu rapor klasörüne yaz', onClick: exportCsv }),
    button('Yazdır', { title: 'Üret ve doğrudan yazıcıya gönder', onClick: printDirect }),
    button('Kaydet', { title: 'Adıyla sakla', onClick: savePreset }),
    button('Zamanla', { title: 'Haftalık/aylık üret', onClick: schedulePreset }),
  );

  const right = h('div', 'rp-work');
  right.append(nodes.paramHost, actions, nodes.output, nodes.lastFile.node);

  nodes.split = splitView(left, right, '320px minmax(0, 1fr)');
  nodes.split.classList.add('rp-split');

  nodes.status = statusLine();
  nodes.body = h('div', 'rp-body');
  nodes.body.append(nodes.split);

  nodes.tabs = tabBar([
    { key: 'report', label: 'Rapor' },
    { key: 'saved', label: 'Kaydedilmiş' },
    { key: 'schedules', label: 'Zamanlanmış' },
    { key: 'runs', label: 'Geçmiş' },
  ], 'report', (key) => showView(key));

  view.append(nodes.tabs.node, nodes.status.node, nodes.body);

  function showView(key) {
    if (key === 'report') {
      nodes.body.replaceChildren(nodes.split);
      nodes.status.set(statusText(), !state.connected);
      return;
    }
    nodes.body.replaceChildren();
    if (key === 'saved') renderSaved();
    else if (key === 'schedules') renderSchedules();
    else renderRuns();
  }

  buildParamBar(null);
  renderWorkspace();
  root.replaceChildren(view);
  nodes.status.set('Rapor ağacı alınıyor…');
  loadTree().then(() => loadReference());

  return () => {
    // `filterBar` tarih alanı taşıyor ve o global dinleyici tutuyor; panel
    // kapanırken bırakılmazsa her açılışta bir dinleyici daha birikir.
    nodes.params?.destroy();
    nodes.params = null;
    // Açık diyalog varsa kapatılır: `document` dinleyicisi bırakılır ve
    // bekleyen söz `null` ile çözülür (çağıran "vazgeçildi" görür).
    for (const close of [...dialogs]) close(null);
    dialogs.clear();
    nodes.tree.removeEventListener('keydown', onTreeKey);
    root.replaceChildren();
    state = { ...EMPTY, open: new Set(), package: new Set() };
    busy = false;
  };
}
