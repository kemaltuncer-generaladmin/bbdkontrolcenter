// Promosyonlar paneli — sepet kuralı, katalog kuralı, kupon ve kampanya performansı.
//
// NE YAPAR: dört sekme. Kurallar listesinde kullanım çubuğu ve takvim durumu;
// satırdan tam yükseklik çekmecede TEK SAYFA kural düzenleyici (künye ·
// koşullar · eylem · kısıtlar · takvim · simülasyon · kuponlar · geçmiş);
// kupon üreteci (önek + adet + uzunluk → CSV); performans sekmesinde kupon
// başına ciro ve indirim maliyeti.
//
// NEDEN TEK SAYFA DÜZENLEYİCİ: kuralın altı parçası birbirine bağlı — koşulu
// değiştirince eylemin ne yapacağını görmek gerekiyor. Sekmelere bölmek her
// denemede üç tıklama ekliyordu.
//
// NE YAPMAZ:
//  · Kural SİLMEZ. Durdurulur; silinirse geçmiş siparişlerin hangi kampanyayla
//    indirildiği kaybolur (ADR 0012).
//  · Kupon kodu ÜRETMEZ — üreteci mağaza çalıştırır. Buradaki "örnek kodlar"
//    yalnız biçim önizlemesidir; hiçbir yere yazılmaz.
//  · Kuralı kendiliğinden yayına almaz. Yeni kural taslak açılır; yayına alma
//    ayrı yetkidir (`store_promotions.activate`).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · `status=1` "yayında" demek değildir; takvimi geçmiş kural ÖLÜDÜR. Durum
//    rozeti takvimi de okur.
//  · Yüzde ile tutar aynı alanda taşınır; ekran ikisini AYRI girdi tipiyle
//    sorar (yüzde sayı, tutar kuruş).
//  · Mağazadan gelen ama bu ekranın tanımadığı koşullar korunur ve sayısı
//    yazılır — "kaydet" onları yok etmez.
//  · Üst sınır alanı her mağaza sürümünde yok; yoksa alan kapalı gelir.
//  · Simülasyon MAĞAZANIN MOTORU DEĞİLDİR; kutunun altında böyle yazar.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_promotions/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_promotions/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmWithReason, csvBlob, debounce, h, loadStyles, money, moneyInput,
  num, parseMoney, percent, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter, resolveChoice } from '../../ui-kit/choice.js';
import {
  alertBox, badge, card, chipRow, drawer, emptyState, hintBox, kpiRow,
  skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { createPicker } from '../../ui-kit/picker.js';
import { barChart } from '../../ui-kit/charts.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_promotions';

const STATUS_TONES = {
  active: 'good', scheduled: 'info', paused: 'dim', expired: 'bad', draft: 'dim',
};

// `count: 0` ZORUNLU: kit sayaç yuvasını yalnız `count` verilmişse kuruyor,
// sonradan `counts()` çağırmak boş çipte hiçbir şey yazmıyordu.
const CHIPS = [
  { key: 'active', label: 'Şu an aktif', count: 0 },
  { key: 'expiring', label: 'Bu hafta doluyor', count: 0 },
  { key: 'neverUsed', label: 'Hiç kullanılmamış', count: 0 },
  { key: 'exhausted', label: 'Limiti dolmuş', count: 0 },
];

// FONKSİYON, sabit değil: iç içe nesneler paylaşılan bir sabitten kopyalanırsa
// `state.coupons.ruleId = …` gibi bir atama SABİTİ değiştirir ve panel yeniden
// açıldığında önceki oturumun seçimi geri gelir.
const blank = () => ({
  rules: [], total: 0, page: 1, size: 50, pages: 0, counts: {},
  connected: false, error: '', chip: null,
  scopeFilterable: true, scopeNoted: false,
  reference: {
    channels: [], customerGroups: [], categories: [], paymentMethods: [],
    actionTypes: [], operators: [], conditionKinds: [], limits: {},
  },
  catalog: { items: [], connected: false, error: '' },
  coupons: { ruleId: 0, items: [], total: 0, page: 1, pages: 0 },
  performance: null,
  loaded: {},
});

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = blank();
let view = 'rules';

const nodes = {};
const closers = [];          // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

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

/** Yıkıcı ve parayı etkileyen her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function select(options, value) {
  const node = h('select', 'kit-select');
  for (const option of options) {
    const item = h('option', undefined, option.label);
    item.value = String(option.value);
    if (option.disabled) item.disabled = true;
    node.append(item);
  }
  if (value !== undefined && value !== null) node.value = String(value);
  return node;
}

function labelled(label, control, hint) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
  if (hint) wrap.append(h('span', 'kit-field-hint', hint));
  return wrap;
}

function moneyBox(value, placeholder = '0,00') {
  const input = h('input', 'kit-input pm-money');
  input.type = 'text';
  input.inputMode = 'decimal';
  input.placeholder = placeholder;
  if (value !== null && value !== undefined && value !== '') input.value = moneyInput(value);
  return input;
}

function numberBox(value, { min = 0, max = 999999 } = {}) {
  const input = h('input', 'kit-input pm-num');
  input.type = 'number';
  input.min = String(min);
  input.max = String(max);
  input.value = String(value ?? 0);
  return input;
}

/** Kullanım çubuğu. Renk tek başına anlam taşımaz: yanında her zaman sayı var. */
function usageBar(usage) {
  const box = h('span', 'pm-usage');
  const rail = h('span', 'pm-bar');
  const fill = h('span', 'pm-bar-fill');
  if (usage.ratio === null || usage.ratio === undefined) {
    rail.classList.add('open');
    fill.style.width = '0%';
  } else {
    fill.style.width = `${Math.max(2, usage.ratio)}%`;
    if (usage.ratio >= 100) fill.classList.add('full');
    else if (usage.ratio >= 80) fill.classList.add('near');
  }
  rail.append(fill);
  box.append(rail, h('span', 'pm-usage-text', usage.label));
  if (usage.exhausted) box.append(badge('doldu', 'bad'));
  return box;
}

function statusText() {
  if (view === 'performance') {
    const data = state.performance;
    if (!data) return 'Performans için tarih aralığı seçin.';
    if (!data.connected) return `Mağazaya ulaşılamadı — ${data.error}`;
    return `${data.start} – ${data.end} · ${num(data.orders)} sipariş tarandı`
      + (data.truncated ? ' · TAVANA DAYANDI' : '');
  }
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  const counts = state.counts || {};
  return `Bağlı · ${num(state.total)} kural · ${num(counts.active || 0)} aktif`
    + ` · sayfa ${state.page}/${Math.max(1, state.pages)}`;
}

// ==================================================================== veri

function ruleQuery(extra = {}) {
  const values = nodes.filters ? nodes.filters.values() : {};
  const params = new URLSearchParams();
  const all = {
    q: values.q || '',
    status: values.status || '',
    kind: values.kind || '',
    channelId: values.channel || 0,
    groupId: values.group || 0,
    chip: state.chip || '',
    ...extra,
  };
  for (const [key, value] of Object.entries(all)) {
    if (value === null || value === undefined || value === '' || value === 0) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refreshRules({ page = state.page, size = state.size } = {}) {
  nodes.tableWrap?.replaceChildren(skeletonRows(8, 7));
  nodes.status?.set('Kampanyalar alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/rules?${ruleQuery({ page, size })}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, rules: [], total: 0 };
    renderRuleTable();
    nodes.status?.set(statusText(), true);
    return;
  }
  state = {
    ...state,
    rules: payload.items || [],
    total: payload.total || 0,
    page: payload.page || page,
    size: payload.size || size,
    pages: payload.pages || 0,
    counts: payload.counts || {},
    connected: Boolean(payload.connected),
    error: payload.error || '',
    scopeFilterable: payload.scopeFilterable !== false,
  };
  // Mağazanın kural LİSTESİ kanal ve müşteri grubunu vermiyor (yalnız tekil
  // kayıtta dolu). Süzgeci açık bırakmak, seçim yapan kullanıcıya hiçbir şey
  // değişmeyen bir düğme vermek olurdu; seçenekler kaldırılır ve nedeni yazar.
  if (state.connected && !state.scopeFilterable && !state.scopeNoted) {
    state.scopeNoted = true;
    // Kanal kutusu zaten çizilmiyorsa (tek kanal) burada da açılmaz; yalnız
    // çok kanallı bir mağazada "liste bu bilgiyi vermiyor" demek gerekir.
    nodes.filters.options('channel', [{ value: '', label: 'Kanal — liste bu bilgiyi vermiyor' }]);
    nodes.filters.options('group', [{ value: '', label: 'Grup — liste bu bilgiyi vermiyor' }]);
  }
  renderRuleTable();
  renderRuleKpi();
  nodes.pager.update({ total: state.total, page: state.page, size: state.size });
  nodes.chips.counts?.(state.counts);
  nodes.status?.set(statusText(), !state.connected);
}

/**
 * Yeni kuralın kanalı — mağazada TEK kanal varsa onun kimliği, yoksa boş.
 *
 * Tek seçenekli alanı ekrandan kaldırmanın yazma tarafındaki karşılığı budur:
 * kutu sorulmaz ama değer gönderilir (`choice.js` başlığı). Çok kanallı bir
 * mağazada boş döner — orada seçim gerçek bir sorudur ve uydurulmaz.
 */
function autoChannelIds() {
  const choice = resolveChoice(state.reference.channels);
  return choice.mode === 'single' ? [Number(choice.value)] : [];
}

async function loadReference() {
  let payload;
  try {
    payload = await api(`${BASE}/reference`);
  } catch {
    // Referans gelmezse süzgeçler boş kalır ama ekran çalışır (K7).
    return;
  }
  state.reference = { ...state.reference, ...payload };
  // TEK KANALLI MAĞAZADA KUTU ÇİZİLMEZ (`choice.js`); ikinci kanal açılırsa
  // süzgeç kendiliğinden geri gelir. MÜŞTERİ GRUBU AYNI KURALA GİRMEZ: orada
  // üç gerçek seçenek var (Toptan/Genel/Misafir) ve seçim listeyi değiştirir.
  applyChoiceFilter(nodes.filters, 'channel', payload.channels,
                    { allLabel: 'Tümü — kanal' });
  nodes.filters.options('group', [
    { value: '', label: 'Tümü — müşteri grubu' },
    ...(payload.customerGroups || []).map((item) => ({ value: item.id, label: item.name })),
  ]);
  nodes.filters.options('kind', [
    { value: '', label: 'Tümü — indirim tipi' },
    ...(payload.actionTypes || []).map((item) => ({ value: item.value, label: item.label })),
  ]);
  if ((payload.warnings || []).length) {
    toast(`Bazı referans listeler gelmedi: ${payload.warnings.join(' · ')}`, 'warn');
  }
}

// =================================================================== çizim

function renderRuleKpi() {
  if (!nodes.kpi) return;
  const counts = state.counts || {};
  nodes.kpi.replaceChildren(kpiRow([
    { label: 'Kural', value: num(counts.all || 0) },
    { label: 'Aktif', value: num(counts.active || 0), tone: 'good' },
    { label: 'Zamanlanmış', value: num(counts.scheduled || 0), tone: 'muted' },
    { label: 'Duraklatıldı', value: num(counts.paused || 0), tone: 'muted' },
    { label: 'Bu hafta doluyor', value: num(counts.expiring || 0), tone: 'warn' },
    { label: 'Hiç kullanılmamış', value: num(counts.neverUsed || 0), tone: 'warn' },
  ]));
}

const RULE_COLUMNS = [
  {
    key: 'name',
    label: 'Kampanya',
    width: 'minmax(0, 2.4fr)',
    sortable: true,
    cell: (row) => {
      const box = h('span', 'pm-name');
      box.append(clip(h('b'), row.name, 44));
      box.append(h('span', 'pm-sub', row.description || row.actionLabel));
      return box;
    },
  },
  {
    key: 'code',
    label: 'Kod',
    width: '132px',
    cell: (row) => {
      if (row.couponType !== 'specific') return h('span', 'pm-sub', 'kodsuz');
      if (row.autoGenerated && !row.code) return badge('otomatik kupon', 'info');
      return h('code', 'pm-code', row.code || '—');
    },
  },
  { key: 'actionLabel', label: 'Tür', width: '150px' },
  { key: 'valueLabel', label: 'Değer', width: '104px', align: 'num' },
  {
    key: 'endsTill',
    label: 'Takvim',
    width: '176px',
    cell: (row) => {
      const box = h('span', 'pm-dates');
      box.append(h('span', undefined, `${row.startsFrom || '—'} → ${row.endsTill || 'süresiz'}`));
      if (row.expiringSoon) box.append(badge('bu hafta doluyor', 'warn'));
      return box;
    },
  },
  { key: 'usage', label: 'Kullanım', width: '186px', cell: (row) => usageBar(row.usage) },
  { key: 'priority', label: 'Öncelik', width: '78px', align: 'num' },
  {
    key: 'status',
    label: 'Durum',
    width: '120px',
    cell: (row) => badge(row.statusLabel, STATUS_TONES[row.status] || ''),
  },
];

function renderRuleTable() {
  const wrap = nodes.tableWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  let empty;
  if (!state.connected) {
    empty = emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshRules() })],
    });
  } else if (state.chip) {
    empty = emptyState({
      title: 'Bu başlıkta kampanya yok',
      text: 'Seçili çipe uyan kural bulunamadı.',
      actions: [button('Çipi kaldır', {
        onClick: () => { nodes.chips.set(null); state.chip = null; refreshRules({ page: 1 }); },
      })],
    });
  } else {
    empty = emptyState({
      title: 'Kampanya yok',
      text: 'Sepet kuralı = "şu koşulda şu indirimi yap". Bir tane açtığınızda '
        + 'burada listelenir ve kullanımını buradan izlersiniz.',
      actions: [button('Yeni kural', { variant: 'primary', onClick: () => openRule(0) })],
    });
  }

  nodes.table = dataTable({
    columns: RULE_COLUMNS,
    rows: state.rules,
    empty,
    onRow: (row) => openRule(row.id),
  });
  wrap.append(nodes.table.node);
}

// ========================================================== koşul satırları

/**
 * Tek koşul satırı: tür + operatör + değer. `read()` ekran modelini döndürür.
 *
 * Desteklenmeyen tür (ör. "yalnız ilk sipariş") listede KALIR ama seçilemez ve
 * nedeni yazar; gizlemek kullanıcıyı aramaya iter.
 */
function conditionRow(value, onRemove) {
  const kinds = state.reference.conditionKinds || [];
  const wrap = h('div', 'pm-cond');
  const kindSelect = select(
    kinds.filter((item) => item.value !== 'customerGroup')
      .map((item) => ({
        value: item.value,
        label: item.available ? item.label : `${item.label} (yakında)`,
        disabled: !item.available,
      })),
    value.kind || 'subtotal',
  );
  const operatorHost = h('span', 'pm-cond-op');
  const valueHost = h('span', 'pm-cond-value');
  const note = h('span', 'pm-sub');
  let operatorSelect = null;
  let read = () => ({});

  const OPERATORS = {
    subtotal: ['>=', '<=', '>', '<', '=='],
    itemsQty: ['>=', '<=', '=='],
    product: ['()', '!()'],
    category: ['{}', '!{}'],
    payment: ['==', '!='],
  };

  function paint() {
    const kind = kindSelect.value;
    const labels = Object.fromEntries(
      (state.reference.operators || []).map((item) => [item.value, item.label]),
    );
    operatorSelect = select(
      (OPERATORS[kind] || ['==']).map((item) => ({ value: item, label: labels[item] || item })),
      value.operator,
    );
    operatorHost.replaceChildren(operatorSelect);
    valueHost.replaceChildren();
    note.textContent = '';

    if (kind === 'subtotal') {
      const input = moneyBox(value.kind === kind ? value.value : null, 'örn. 500,00');
      valueHost.append(input);
      read = () => ({ kind, operator: operatorSelect.value, value: parseMoney(input.value) ?? 0 });
    } else if (kind === 'itemsQty') {
      const input = numberBox(value.kind === kind ? value.value : 1, { min: 1, max: 999 });
      valueHost.append(input);
      read = () => ({ kind, operator: operatorSelect.value, value: Number(input.value) });
    } else if (kind === 'payment') {
      const methods = state.reference.paymentMethods || [];
      let control;
      if (methods.length) {
        control = select([{ value: '', label: 'Seçin' },
          ...methods.map((item) => ({ value: item.value, label: item.label }))],
        value.kind === kind ? value.value : '');
      } else {
        // Ödeme yöntemleri okunamadı: boş bir açılır göstermek yerine kodu
        // elle yazdırırız ve nedenini söyleriz. Gerekçeyi sunucu yazar
        // (hangi uç okunamadı onu o biliyor); metin yoksa genel cümle kalır.
        control = h('input', 'kit-input');
        control.placeholder = 'ödeme yöntemi kodu';
        control.value = value.kind === kind ? (value.value || '') : '';
        const spec = kinds.find((item) => item.value === 'payment');
        note.textContent = spec?.note
          || 'Ödeme yöntemleri mağazadan okunamadı; kodu elle yazın.';
      }
      valueHost.append(control);
      read = () => ({ kind, operator: operatorSelect.value, value: control.value });
    } else if (kind === 'category') {
      const picker = createPicker({
        items: (state.reference.categories || []).map((item) => ({
          id: item.id, name: item.label, group: item.depth === 0 ? 'Ana' : 'Alt',
        })),
        groupLabel: 'Düzey',
        placeholder: 'Kategori ara',
      });
      if (value.kind === kind) picker.select(value.value || []);
      picker.node.classList.add('pm-cond-picker');
      valueHost.append(picker.node);
      read = () => ({
        kind, operator: operatorSelect.value, value: picker.selection().map(Number),
      });
    } else {
      // Ürün: 1.419 ürünlük katalog seçiciye sığmaz; sunucuda aranır.
      const chosen = new Map();
      for (const id of (value.kind === kind ? value.value : []) || []) {
        chosen.set(Number(id), `#${id}`);
      }
      const chips = h('div', 'pm-chips');
      const search = h('input', 'kit-input');
      search.type = 'search';
      search.placeholder = 'Ürün adı ara (en az 2 harf)';
      const results = h('div', 'pm-results');

      const paintChips = () => {
        chips.replaceChildren();
        for (const [id, label] of chosen) {
          const chip = h('span', 'pm-chip', label);
          chip.append(button('×', {
            variant: 'ghost',
            title: 'Çıkar',
            onClick: () => { chosen.delete(id); paintChips(); },
          }));
          chips.append(chip);
        }
        if (!chosen.size) chips.append(h('span', 'pm-sub', 'Ürün seçilmedi.'));
      };
      paintChips();

      const lookup = debounce(async () => {
        if (search.value.trim().length < 2) { results.replaceChildren(); return; }
        try {
          const found = await call(`${BASE}/products?q=${encodeURIComponent(search.value)}`);
          results.replaceChildren();
          for (const item of found.items || []) {
            results.append(button(`${item.name} · ${item.sku}`, {
              onClick: () => { chosen.set(item.id, item.name); paintChips(); },
            }));
          }
          if (!(found.items || []).length) {
            results.append(h('span', 'pm-sub', 'Eşleşen ürün yok.'));
          }
        } catch (error) {
          results.replaceChildren(h('span', 'pm-sub', error.message));
        }
      }, 400);
      closers.push(() => lookup.cancel());
      search.addEventListener('input', lookup);

      valueHost.append(chips, search, results);
      read = () => ({ kind, operator: operatorSelect.value, value: [...chosen.keys()] });
    }
  }

  kindSelect.addEventListener('change', paint);
  paint();

  wrap.append(kindSelect, operatorHost, valueHost,
    button('Kaldır', { variant: 'ghost', onClick: () => { wrap.remove(); onRemove?.(); } }));
  wrap.append(note);
  return { node: wrap, read: () => read(), alive: () => wrap.isConnected };
}

// ================================================================= çekmece

async function openRule(ruleId) {
  const forms = [];
  const rows = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: ruleId ? 'Kural yükleniyor…' : 'Yeni kampanya',
    subtitle: ruleId ? `#${ruleId}` : 'taslak olarak açılır',
    onClose: dropForms,
  });
  closers.push(dropForms);

  let rule = null;
  if (ruleId) {
    box.body.append(skeletonRows(6, 3));
    let payload;
    try {
      payload = await call(`${BASE}/rules/${ruleId}`);
    } catch (error) {
      box.body.replaceChildren(emptyState({
        title: 'Kural okunamadı',
        text: error.message,
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }
    rule = payload.rule;
    box.setTitle(rule.name);
    if ((payload.warnings || []).length) {
      toast(`Bazı parçalar okunamadı: ${payload.warnings.join(' · ')}`, 'warn');
    }
  }

  box.body.replaceChildren();
  const head = h('div', 'pm-drawer-head');
  if (rule) {
    head.append(
      badge(rule.statusLabel, STATUS_TONES[rule.status] || ''),
      badge(rule.actionLabel, 'info'),
      usageBar(rule.usage),
    );
  } else {
    head.append(badge('Taslak', 'dim'),
      h('span', 'pm-sub', 'Kaydedildiğinde vitrinde indirim YAPMAZ; yayına almak ayrı yetki.'));
  }
  box.body.append(head);

  // ------------------------------------------------------------- künye
  const identity = formGrid({
    fields: [
      { key: 'name', label: 'Kampanya adı', type: 'text', required: true, maxLength: 120,
        wide: true },
      { key: 'description', label: 'Açıklama', type: 'text', maxLength: 240, wide: true,
        hint: 'Personel için not; müşteri görmez.' },
      { key: 'couponType', label: 'Kupon', type: 'select', options: [
        { value: 'none', label: 'Kodsuz — koşulu tutan herkese uygulanır' },
        { value: 'specific', label: 'Kupon kodu gerekli' },
      ] },
      { key: 'couponCode', label: 'Kupon kodu', type: 'text', maxLength: 64,
        hint: 'Toplu kupon üretecekseniz boş bırakın; kodları Kuponlar sekmesi üretir.' },
      { key: 'startsFrom', label: 'Başlangıç', type: 'date' },
      { key: 'endsTill', label: 'Bitiş', type: 'date',
        hint: 'Boş bırakılırsa kampanya süresizdir.' },
    ],
    value: {
      name: rule?.name || '',
      description: rule?.description || '',
      couponType: rule?.couponType || 'none',
      couponCode: rule?.code || '',
      startsFrom: rule?.startsFrom || todayIso(),
      endsTill: rule?.endsTill || '',
    },
  });
  forms.push(identity);

  // ---------------------------------------------------------- koşullar
  const joiner = select([
    { value: 'all', label: 'TÜMÜ tutmalı (VE)' },
    { value: 'any', label: 'BİRİ yeter (VEYA)' },
  ], rule?.conditionType || 'all');
  const conditionHost = h('div', 'pm-conds');
  const addCondition = (value = { kind: 'subtotal', operator: '>=', value: 0 }) => {
    const row = conditionRow(value);
    rows.push(row);
    conditionHost.append(row.node);
  };
  for (const item of rule?.conditions || []) addCondition(item);
  if (!(rule?.conditions || []).length) {
    conditionHost.append(hintBox('Koşul yoksa kural HER sepete uygulanır. Bunu bilerek '
      + 'yapıyorsanız sorun yok; genelde en az bir alt sınır konur.'));
  }

  const groupPicker = createPicker({
    items: (state.reference.customerGroups || []).map((item) => ({
      id: item.id, name: item.name,
    })),
    placeholder: 'Müşteri grubu ara',
  });
  groupPicker.select(rule?.customerGroups || []);

  const conditionBox = h('div', 'pm-block');
  conditionBox.append(
    labelled('Koşullar nasıl birleşsin', joiner),
    conditionHost,
    button('+ Koşul ekle', { onClick: () => addCondition() }),
    labelled('Kimler (müşteri grubu)', groupPicker.node,
      'Boş bırakılırsa tüm gruplara uygulanır.'),
  );
  const unavailable = (state.reference.conditionKinds || []).filter((item) => !item.available);
  if (unavailable.length) {
    conditionBox.append(alertBox(
      unavailable.map((item) => `${item.label}: ${item.note}`).join(' · '), 'info'));
  }
  if (rule?.unknownConditions) {
    conditionBox.append(alertBox(
      `Bu kuralda bu ekranın düzenlemediği ${rule.unknownConditions} koşul var (mağaza `
      + 'yönetiminden eklenmiş). Kaydettiğinizde SİLİNMEZ, olduğu gibi korunur.', 'warn'));
  }

  // ------------------------------------------------------------- eylem
  const actionKind = select(
    (state.reference.actionTypes || []).map((item) => ({ value: item.value, label: item.label })),
    rule?.action?.kind || 'by_percent',
  );
  const percentBox = numberBox(rule?.action?.kind === 'by_percent' ? rule.action.value : 10,
    { min: 0, max: 100 });
  const amountBox = moneyBox(rule?.action && rule.action.kind !== 'by_percent'
    ? rule.action.value : null);
  const capBox = moneyBox(rule?.action?.maxDiscount ?? null, 'sınırsız');
  const stepBox = numberBox(rule?.action?.step ?? 2, { min: 0, max: 99 });
  const qtyBox = numberBox(rule?.action?.quantity ?? 1, { min: 0, max: 99 });
  const freeShip = h('input', 'kit-check');
  freeShip.type = 'checkbox';
  freeShip.checked = Boolean(rule?.action?.freeShipping);

  // Üst sınır alanı mağaza sürümüne bağlı. YENİ kuralda henüz kayıt yok, yani
  // "desteklemiyor" demek yanlış olurdu: bilmiyoruz, kaydettikten sonra
  // görülecek. İki durum ayrı yazılır.
  const capSupported = rule ? rule.action.maxDiscountSupported : false;
  if (!capSupported) {
    capBox.disabled = true;
    capBox.value = '';
    capBox.placeholder = rule ? 'mağaza sürümü desteklemiyor' : 'kayıttan sonra görülecek';
  }

  const actionBox = h('div', 'pm-block');
  const actionFields = h('div', 'pm-row');
  const percentField = labelled('Yüzde (%)', percentBox);
  const amountField = labelled('Tutar', amountBox);
  const stepField = labelled('Kaç adet alınca', stepBox);
  const qtyField = labelled('Kaç adet bedava', qtyBox);
  const capField = labelled('İndirim üst sınırı', capBox,
    capSupported ? 'Yüzde indirimde tavanı tutar.'
      : (rule ? 'Bu alan mağazanın kural kaydında yok; yazılsa bile uygulanmaz.'
        : 'Kural kaydedildikten sonra, mağaza kaydı bu alanı taşıyorsa açılır.'));
  actionFields.append(labelled('İndirim tipi', actionKind), percentField, amountField,
    stepField, qtyField, capField,
    labelled('Ücretsiz kargo', freeShip, 'İndirimle birlikte kargo bedeli düşer.'));

  function paintAction() {
    const kind = actionKind.value;
    percentField.hidden = kind !== 'by_percent';
    amountField.hidden = kind === 'by_percent' || kind === 'buy_x_get_y';
    stepField.hidden = kind !== 'buy_x_get_y';
    qtyField.hidden = kind !== 'buy_x_get_y';
    capField.hidden = kind !== 'by_percent';
  }
  actionKind.addEventListener('change', paintAction);
  paintAction();
  actionBox.append(actionFields);

  // ---------------------------------------------------------- kısıtlar
  const limits = formGrid({
    fields: [
      { key: 'perCoupon', label: 'Toplam kullanım limiti', type: 'number', min: 0, max: 999999,
        hint: '0 = sınırsız.' },
      { key: 'perCustomer', label: 'Müşteri başına limit', type: 'number', min: 0, max: 9999,
        hint: '0 = sınırsız.' },
      { key: 'priority', label: 'Öncelik', type: 'number', min: 0, max: 9999,
        hint: 'Küçük sayı önce uygulanır.' },
      { key: 'endOtherRules', label: 'Diğer kuralları durdur', type: 'checkbox',
        hint: 'Bu kural uygulanınca daha düşük öncelikli kampanyalar devreye girmez.' },
    ],
    value: {
      perCoupon: rule?.limits?.perCoupon ?? 0,
      perCustomer: rule?.limits?.perCustomer ?? 0,
      priority: rule?.limits?.priority ?? 0,
      endOtherRules: Boolean(rule?.limits?.endOtherRules),
    },
  });
  forms.push(limits);

  // ------------------------------------------------------------ toplama
  function draft() {
    const values = identity.draft();
    const limitValues = limits.draft();
    const kind = actionKind.value;
    return {
      id: rule?.id || 0,
      name: values.name,
      description: values.description,
      couponType: values.couponType,
      couponCode: values.couponCode,
      startsFrom: values.startsFrom,
      endsTill: values.endsTill,
      conditionType: joiner.value,
      conditions: rows.filter((row) => row.alive()).map((row) => row.read()),
      customerGroups: groupPicker.selection().map(Number),
      // KANAL SORULMAZ, KENDİLİĞİNDEN DOLAR — ama YALNIZ tek kanallıyken.
      //
      // Kural bir kanala bağlı olmak zorunda: boş `channels` ile kaydedilen
      // kural vitrinde HİÇ görünmez. Mevcut kuralın kanalı varsa o korunur.
      //
      // ÇOK KANALLI HÂL BURADA KARARA BAĞLANMAZ: "hepsini seç" de bir karardır
      // ve kampanyanın hangi vitrinlerde çalışacağını sessizce belirler. O gün
      // buraya gerçek bir kanal seçici gelir; bugün olmayan bir seçim
      // uydurulmaz.
      channels: (rule?.channels || []).length
        ? rule.channels
        : autoChannelIds(),
      action: {
        kind,
        value: kind === 'by_percent' ? Number(percentBox.value)
          : (parseMoney(amountBox.value) ?? 0),
        maxDiscount: capSupported ? (parseMoney(capBox.value) ?? 0) : 0,
        freeShipping: freeShip.checked,
        step: Number(stepBox.value),
        quantity: Number(qtyBox.value),
      },
      limits: {
        perCoupon: Number(limitValues.perCoupon || 0),
        perCustomer: Number(limitValues.perCustomer || 0),
        priority: Number(limitValues.priority || 0),
        endOtherRules: Boolean(limitValues.endOtherRules),
      },
    };
  }

  // -------------------------------------------------------- simülasyon
  const simulation = simulationBox(() => draft());

  // ------------------------------------------------------------ eylemler
  const actions = h('div', 'pm-actions');
  actions.append(button(rule ? 'Kaydet' : 'Taslağı oluştur', {
    variant: 'primary',
    onClick: async () => {
      if (!identity.valid()) { identity.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const body = draft();
      const reason = await askReason({
        title: rule ? 'Kampanyayı güncelle' : 'Yeni kampanya',
        description: rule
          ? `${rule.name} güncellenecek. Durum bu düğmeyle DEĞİŞMEZ; yayına alma ayrı yetkidir.`
          : 'Kural TASLAK açılır ve vitrinde indirim yapmaz. Gerekçe denetim kaydına yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const saved = await withBusy('Kaydediliyor…', async () => call(
        rule ? `${BASE}/rules/${rule.id}` : `${BASE}/rules`,
        { method: rule ? 'PUT' : 'POST', body: { patch: body, reason, dryRun: false } },
      ));
      if (!saved) return;
      toast(rule ? 'Kampanya kaydedildi.' : 'Taslak oluşturuldu.', 'good');
      if (saved.notice) toast(saved.notice, 'warn');
      if (saved.maxDiscountSkipped) {
        toast('Üst sınır YAZILMADI: mağaza kaydında böyle bir alan yok.', 'warn');
      }
      box.close();
      refreshRules();
    },
  }));

  if (rule) {
    const live = rule.status === 'active';
    actions.append(button(live ? 'Durdur' : 'Yayına al', {
      variant: live ? 'danger' : 'primary',
      title: live ? 'Kampanya sepetlere uygulanmayı bırakır'
        : 'Kampanya bundan sonraki siparişlerde indirim yapar',
      onClick: () => toggleRule(rule, !live, box),
    }));
    actions.append(button('Kopyala', {
      onClick: async () => {
        const reason = await askReason({
          title: 'Kampanyayı kopyala',
          description: `${rule.name} kopyalanır. Kopya PASİF açılır; kupon kodları kopyalanmaz.`,
          confirmLabel: 'Kopyala',
        });
        if (!reason) return;
        await withBusy('Kopyalanıyor…', async () => {
          await call(`${BASE}/rules/${rule.id}/copy`, {
            method: 'POST', body: { reason, dryRun: false },
          });
          toast('Kopya oluşturuldu.', 'good');
          box.close();
          refreshRules();
        });
      },
    }));
    if (rule.couponType === 'specific') {
      actions.append(button('Kuponlara git', {
        onClick: () => { box.close(); openCoupons(rule.id); },
      }));
    }
  }

  box.body.append(
    card('Künye', identity.node),
    card('Koşullar', conditionBox, 'Hangi sepete uygulanacak'),
    card('Eylem', actionBox, 'Ne kadar indirim'),
    card('Kısıtlar', limits.node, 'Kaç kez, hangi sırayla'),
    card('Simülasyon', simulation.node, 'Örnek sepet gir, indirimi gör'),
    actions,
  );

  if (rule) {
    const history = h('div');
    box.body.append(card('İşlem geçmişi', history, 'Bu ekrandan yapılan yazmalar'));
    paintHistory(history, rule.id);
  }
  box.body.append(hintBox('Kaydetme OKU-DEĞİŞTİR-YAZ yapar: kural taze okunur, '
    + 'dokunmadığınız alanlar (bu ekranın tanımadığı koşullar dahil) mağazadaki güncel '
    + 'değeriyle geri gönderilir.'));
}

async function toggleRule(rule, active, box) {
  const reason = await askReason({
    title: active ? 'Kampanyayı yayına al' : 'Kampanyayı durdur',
    description: active
      ? `${rule.name} bundan sonraki her uygun siparişte indirim yapacak. Takvim ve `
        + 'kullanım limiti geçerlidir.'
      : `${rule.name} artık indirim yapmayacak. Kural SİLİNMEZ: geçmiş siparişlerin hangi `
        + 'kampanyayla indirildiği kayıtlı kalır.',
    confirmLabel: active ? 'Yayına al' : 'Durdur',
  });
  if (!reason) return;
  await withBusy(active ? 'Yayına alınıyor…' : 'Durduruluyor…', async () => {
    const result = await call(`${BASE}/rules/${rule.id}/status`, {
      method: 'POST', body: { active, scope: 'cart', reason, dryRun: false },
    });
    toast(active ? 'Kampanya yayında.' : 'Kampanya durduruldu.', 'good');
    if (result.notice) toast(result.notice, 'warn');
    box?.close();
    refreshRules();
  });
}

async function paintHistory(host, ruleId) {
  host.append(skeletonRows(3, 3));
  let result;
  try {
    result = await call(`${BASE}/audit?ruleId=${ruleId}&limit=50`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!result.items.length) {
    host.append(h('span', 'pm-sub', 'Bu kurala bu ekrandan dokunulmadı.'));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'action', label: 'İşlem', width: '150px' },
      { key: 'actor', label: 'Kim', width: '120px' },
      { key: 'result', label: 'Sonuç', width: '90px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
    ],
    rows: result.items,
    dense: true,
    rowKey: (row) => `${row.createdAt}-${row.action}`,
  });
  host.append(table.node);
}

// ============================================================== simülasyon

/**
 * "Örnek sepet gir → indirim ne olur" kutusu.
 *
 * Taslağı da hesaba katar: kaydetmeden önce kuralın işe yarayıp yaramadığı
 * burada görülür. Sonuç kutusu UYGULANMAYAN kuralları da NEDENİYLE listeler —
 * "niye indirim düşmedi" sorusunun cevabı ekranın yarısıdır.
 */
function simulationBox(draftReader) {
  const wrap = h('div', 'pm-sim');
  const lines = h('div', 'pm-sim-lines');
  const output = h('div', 'pm-sim-out');
  const items = [];

  function addItem(value = { name: 'Örnek ürün', price: 10000, qty: 1 }) {
    const row = h('div', 'pm-sim-line');
    const name = h('input', 'kit-input');
    name.value = value.name;
    name.placeholder = 'kalem adı';
    const price = moneyBox(value.price);
    const qty = numberBox(value.qty, { min: 1, max: 999 });
    const category = select([{ value: '', label: 'kategorisiz' },
      ...(state.reference.categories || []).map((item) => ({
        value: item.id, label: item.label.replace(/^(— )+/, ''),
      }))]);
    const productId = numberBox(0, { min: 0, max: 9999999 });
    row.append(name, price, qty, category, productId,
      button('×', { variant: 'ghost', title: 'Kalemi çıkar', onClick: () => row.remove() }));
    lines.append(row);
    items.push({
      node: row,
      read: () => ({
        name: name.value,
        price: parseMoney(price.value) ?? 0,
        qty: Number(qty.value),
        categoryIds: category.value ? [Number(category.value)] : [],
        productId: Number(productId.value) || 0,
      }),
    });
  }
  addItem();

  const shipping = moneyBox(3000);
  const coupon = h('input', 'kit-input');
  coupon.placeholder = 'kupon kodu (varsa)';
  const payment = select([{ value: '', label: 'ödeme yöntemi farketmez' },
    ...(state.reference.paymentMethods || []).map((item) => ({
      value: item.value, label: item.label,
    }))]);
  const group = select([{ value: '', label: 'grup farketmez' },
    ...(state.reference.customerGroups || []).map((item) => ({
      value: item.id, label: item.name,
    }))]);

  const controls = h('div', 'pm-row');
  controls.append(labelled('Kargo', shipping), labelled('Kupon', coupon),
    labelled('Ödeme', payment), labelled('Müşteri grubu', group));

  const head = h('div', 'pm-sim-head');
  head.append(h('span', 'pm-sub', 'Kalem · fiyat · adet · kategori · ürün no'));

  const run = button('İndirimi hesapla', {
    variant: 'primary',
    onClick: async () => {
      const body = {
        items: items.filter((item) => item.node.isConnected).map((item) => item.read()),
        shipping: parseMoney(shipping.value) ?? 0,
        paymentMethod: payment.value,
        customerGroupId: Number(group.value) || 0,
        coupon: coupon.value.trim(),
        draft: draftReader ? draftReader() : null,
      };
      if (!body.items.length) { toast('En az bir kalem girin.', 'warn'); return; }
      output.replaceChildren(skeletonRows(3, 3));
      let payload;
      try {
        payload = await call(`${BASE}/simulate`, { method: 'POST', body });
      } catch (error) {
        output.replaceChildren(alertBox(error.message, 'bad'));
        return;
      }
      paintSimulation(output, payload);
    },
  });

  wrap.append(head, lines,
    button('+ Kalem ekle', { onClick: () => addItem() }),
    controls, run, output);
  return { node: wrap };
}

function paintSimulation(host, payload) {
  const result = payload.result;
  host.replaceChildren();
  if (!payload.connected) {
    host.append(alertBox(`Kayıtlı kurallar okunamadı (${payload.error}); hesap yalnız `
      + 'ekrandaki taslakla yapıldı.', 'warn'));
  }
  host.append(kpiRow([
    { label: 'Ara toplam', value: money(result.subtotal) },
    { label: 'İndirim', value: money(result.discount), tone: result.discount ? 'good' : 'muted' },
    { label: 'Kargo', value: result.freeShipping ? 'ücretsiz' : money(result.shipping) },
    { label: 'Ödenecek', value: money(result.payable) },
  ]));

  const applied = h('div', 'pm-sim-list');
  applied.append(h('b', undefined, `Uygulanan (${result.applied.length})`));
  if (!result.applied.length) {
    applied.append(h('span', 'pm-sub', 'Hiçbir kural uygulanmadı.'));
  }
  for (const row of result.applied) {
    const line = h('div', 'pm-sim-item');
    line.append(badge('uygulandı', 'good'), h('b', undefined, row.name),
      h('span', 'pm-sub', row.note), h('span', 'kit-spacer'),
      h('b', 'pm-good', `− ${money(row.amount)}`));
    applied.append(line);
  }

  const skipped = h('div', 'pm-sim-list');
  skipped.append(h('b', undefined, `Uygulanmayan (${result.skipped.length})`));
  for (const row of result.skipped) {
    const line = h('div', 'pm-sim-item');
    line.append(badge('atlandı', 'dim'), h('b', undefined, row.name),
      h('span', 'pm-sub', row.reason));
    skipped.append(line);
  }

  host.append(applied, skipped);
  if (result.chainStopped) {
    host.append(alertBox(`"${result.chainStopped}" kuralı zinciri durdurdu; daha düşük `
      + 'öncelikli kampanyalar hiç denenmedi.', 'info'));
  }
  host.append(hintBox(payload.notice));
}

// ========================================================= katalog kuralları

async function refreshCatalog() {
  nodes.catalogWrap.replaceChildren(skeletonRows(6, 5));
  let payload;
  try {
    payload = await api(`${BASE}/catalog-rules`);
  } catch (error) {
    nodes.catalogWrap.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.catalog = payload;
  nodes.catalogWrap.replaceChildren();
  if (!payload.connected) {
    nodes.catalogWrap.append(alertBox(`Mağazaya ulaşılamadı — ${payload.error}`, 'bad'));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kural', width: 'minmax(0, 2.4fr)' },
      { key: 'actionLabel', label: 'Tür', width: '160px' },
      { key: 'valueLabel', label: 'Değer', width: '110px', align: 'num' },
      { key: 'startsFrom', label: 'Başlangıç', width: '120px' },
      { key: 'endsTill', label: 'Bitiş', width: '120px', cell: (row) => row.endsTill || 'süresiz' },
      { key: 'priority', label: 'Öncelik', width: '84px', align: 'num' },
      { key: 'status', label: 'Durum', width: '120px',
        cell: (row) => badge(row.statusLabel, STATUS_TONES[row.status] || '') },
    ],
    rows: payload.items,
    empty: emptyState({
      title: 'Katalog kuralı yok',
      text: 'Katalog kuralı sepete değil VİTRİN FİYATINA uygulanır: ürün listesinde de '
        + 'indirimli fiyat görünür. Sepette kupon isteyen kampanyalar diğer sekmededir.',
    }),
    onRow: (row) => openCatalogRule(row.id),
  });
  nodes.catalogWrap.append(table.node, hintBox(
    'Katalog kuralı vitrin fiyatını değiştirir ve yansıması indeksleme sonrası görünür. '
    + 'Sepet kuralıyla birlikte çalışır: önce katalog indirimi fiyata iner, sonra sepet '
    + 'kuralı o fiyat üzerinden hesaplanır.'));
}

async function openCatalogRule(ruleId) {
  const forms = [];
  const rows = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawer(nodes.root, {
    title: ruleId ? 'Katalog kuralı' : 'Yeni katalog kuralı',
    subtitle: ruleId ? `#${ruleId}` : 'taslak',
    onClose: dropForms,
  });
  closers.push(dropForms);

  let rule = null;
  if (ruleId) {
    box.body.append(skeletonRows(5, 3));
    try {
      rule = (await call(`${BASE}/catalog-rules/${ruleId}`)).rule;
    } catch (error) {
      box.body.replaceChildren(emptyState({
        title: 'Kural okunamadı', text: error.message,
        actions: [button('Kapat', { onClick: box.close })],
      }));
      return;
    }
  }
  box.body.replaceChildren();

  const identity = formGrid({
    fields: [
      { key: 'name', label: 'Kural adı', type: 'text', required: true, maxLength: 120,
        wide: true },
      { key: 'description', label: 'Açıklama', type: 'text', maxLength: 240, wide: true },
      { key: 'startsFrom', label: 'Başlangıç', type: 'date' },
      { key: 'endsTill', label: 'Bitiş', type: 'date' },
      { key: 'priority', label: 'Öncelik', type: 'number', min: 0, max: 9999 },
    ],
    value: {
      name: rule?.name || '',
      description: rule?.description || '',
      startsFrom: rule?.startsFrom || todayIso(),
      endsTill: rule?.endsTill || '',
      priority: rule?.limits?.priority ?? 0,
    },
  });
  forms.push(identity);

  const kind = select([
    { value: 'by_percent', label: 'Yüzde indirim' },
    { value: 'by_fixed', label: 'Sabit tutar indirimi' },
  ], rule?.action?.kind || 'by_percent');
  const percentBox = numberBox(rule?.action?.kind === 'by_percent' ? rule.action.value : 10,
    { min: 0, max: 100 });
  const amountBox = moneyBox(rule?.action?.kind === 'by_fixed' ? rule.action.value : null);
  const percentField = labelled('Yüzde (%)', percentBox);
  const amountField = labelled('Tutar', amountBox);
  const paint = () => {
    percentField.hidden = kind.value !== 'by_percent';
    amountField.hidden = kind.value === 'by_percent';
  };
  kind.addEventListener('change', paint);
  paint();

  const conditionBox = h('div', 'pm-block');
  const conditionHost = h('div', 'pm-conds');
  const addCondition = (value = { kind: 'category', operator: '{}', value: [] }) => {
    const row = conditionRow(value);
    rows.push(row);
    conditionHost.append(row.node);
  };
  for (const item of rule?.conditions || []) addCondition(item);
  conditionBox.append(conditionHost,
    button('+ Koşul ekle', { onClick: () => addCondition() }));
  if (rule?.unknownConditions) {
    conditionBox.append(alertBox(
      `Bu kuralda bu ekranın düzenlemediği ${rule.unknownConditions} koşul var; kaydettiğinizde `
      + 'SİLİNMEZ, olduğu gibi korunur.', 'warn'));
  }

  const actionRow = h('div', 'pm-row');
  actionRow.append(labelled('İndirim tipi', kind), percentField, amountField);

  const actions = h('div', 'pm-actions');
  actions.append(button(rule ? 'Kaydet' : 'Taslağı oluştur', {
    variant: 'primary',
    onClick: async () => {
      if (!identity.valid()) { identity.showErrors(); toast('Alanları düzeltin.', 'bad'); return; }
      const values = identity.draft();
      const body = {
        name: values.name,
        description: values.description,
        startsFrom: values.startsFrom,
        endsTill: values.endsTill,
        conditionType: 'all',
        conditions: rows.filter((row) => row.alive()).map((row) => row.read()),
        action: {
          kind: kind.value,
          value: kind.value === 'by_percent' ? Number(percentBox.value)
            : (parseMoney(amountBox.value) ?? 0),
        },
        limits: { priority: Number(values.priority || 0) },
      };
      const reason = await askReason({
        title: 'Katalog kuralını kaydet',
        description: 'Katalog kuralı VİTRİN FİYATINI değiştirir; ürün listesinde de indirimli '
          + 'fiyat görünür. Durum bu düğmeyle değişmez.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      const saved = await withBusy('Kaydediliyor…', async () => call(
        rule ? `${BASE}/catalog-rules/${rule.id}` : `${BASE}/catalog-rules`,
        { method: rule ? 'PUT' : 'POST', body: { patch: body, reason, dryRun: false } },
      ));
      if (!saved) return;
      toast('Katalog kuralı kaydedildi.', 'good');
      if (saved.notice) toast(saved.notice, 'warn');
      box.close();
      refreshCatalog();
    },
  }));
  if (rule) {
    const live = rule.status === 'active';
    actions.append(button(live ? 'Durdur' : 'Yayına al', {
      variant: live ? 'danger' : 'primary',
      onClick: async () => {
        const reason = await askReason({
          title: live ? 'Katalog kuralını durdur' : 'Katalog kuralını yayına al',
          description: `${rule.name} vitrin fiyatlarını etkiler. Yansıması indeksleme `
            + 'sonrası görünür.',
          confirmLabel: live ? 'Durdur' : 'Yayına al',
        });
        if (!reason) return;
        await withBusy('Uygulanıyor…', async () => {
          await call(`${BASE}/rules/${rule.id}/status`, {
            method: 'POST', body: { active: !live, scope: 'catalog', reason, dryRun: false },
          });
          toast(live ? 'Kural durduruldu.' : 'Kural yayında.', 'good');
          box.close();
          refreshCatalog();
        });
      },
    }));
  }

  box.body.append(
    card('Künye', identity.node),
    card('Hangi ürünlere', conditionBox, 'Koşul yoksa TÜM katalog indirime girer'),
    card('İndirim', actionRow),
    actions,
  );
}

// ================================================================= kuponlar

function openCoupons(ruleId) {
  if (ruleId) state.coupons.ruleId = ruleId;
  // `tabBar.select` aynı sekmedeyken haber VERMEZ; o durumda görünümü elle
  // tazeleriz, yoksa "Kuponlara git" ikinci basışta hiçbir şey yapmazdı.
  if (nodes.tabs.active === 'coupons') showView('coupons');
  else nodes.tabs.select('coupons');
}

function couponRuleOptions() {
  const rules = state.rules.filter((row) => row.couponType === 'specific');
  return [{ value: 0, label: rules.length ? 'Kural seçin' : 'Kupon isteyen kural yok' },
    ...rules.map((row) => ({ value: row.id, label: `${row.name} · ${row.statusLabel}` }))];
}

async function refreshCoupons() {
  const ruleId = Number(nodes.couponRule.value) || 0;
  state.coupons.ruleId = ruleId;
  nodes.couponWrap.replaceChildren();
  if (!ruleId) {
    nodes.couponWrap.append(emptyState({
      title: 'Kural seçilmedi',
      text: 'Kuponlar bir kampanyaya bağlıdır: önce "Kupon kodu gerekli" olarak açılmış bir '
        + 'kampanya seçin. Kodsuz kampanyaların kuponu olmaz.',
    }));
    return;
  }
  nodes.couponWrap.append(skeletonRows(6, 4));
  let payload;
  try {
    payload = await api(`${BASE}/rules/${ruleId}/coupons`);
  } catch (error) {
    nodes.couponWrap.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.coupons = { ...state.coupons, ...payload };
  nodes.couponWrap.replaceChildren();
  if (!payload.connected) {
    nodes.couponWrap.append(alertBox(`Kuponlar okunamadı — ${payload.error}`, 'bad'));
    return;
  }

  const selection = new Set();
  const table = dataTable({
    columns: [
      { key: 'code', label: 'Kod', width: 'minmax(0, 1.4fr)', className: 'mono' },
      { key: 'usage', label: 'Kullanım', width: '180px', cell: (row) => usageBar(row.usage) },
      { key: 'perCustomer', label: 'Müşteri başına', width: '120px', align: 'num',
        cell: (row) => (row.perCustomer ? num(row.perCustomer) : 'sınırsız') },
      { key: 'expiresAt', label: 'Son kullanma', width: '130px',
        cell: (row) => row.expiresAt || 'süresiz' },
      { key: 'state', label: 'Durum', width: '130px',
        cell: (row) => badge(row.stateLabel,
          { usable: 'good', exhausted: 'dim', expired: 'bad' }[row.state]) },
    ],
    rows: payload.items,
    selectable: true,
    empty: emptyState({
      title: 'Bu kampanyada kupon yok',
      text: 'Aşağıdaki üreteçle önek ve adet verip kod üretebilirsiniz; kodlar CSV olarak '
        + 'iner.',
    }),
    onSelect: (ids) => {
      selection.clear();
      ids.forEach((id) => selection.add(Number(id)));
      nodes.couponBar.replaceChildren();
      if (!selection.size) return;
      nodes.couponBar.append(
        h('b', undefined, `${num(selection.size)} kod seçildi`),
        button('Seçilenleri kaldır', {
          variant: 'danger',
          title: 'Yalnız hiç kullanılmamış kodlar kaldırılabilir',
          onClick: () => removeCoupons(ruleId, [...selection]),
        }),
        button('Seçilenleri CSV indir', {
          onClick: () => {
            const rows = payload.items.filter((row) => selection.has(row.id));
            csvBlob(['Kod', 'Kullanım', 'Durum'],
              rows.map((row) => [row.code, row.usage.label, row.stateLabel]), 'kupon-secimi');
            toast(`${num(rows.length)} kod indirildi.`, 'good');
          },
        }),
      );
    },
  });
  nodes.couponWrap.append(table.node);
}

async function removeCoupons(ruleId, couponIds) {
  const reason = await askReason({
    title: 'Kupon kodlarını kaldır',
    description: `${couponIds.length} kod kaldırılacak. KULLANILMIŞ kod kaldırılmaz: kodun izi `
      + 'siparişte duruyor ve performans raporu onu oradan okuyor.',
    confirmLabel: 'Kaldır',
  });
  if (!reason) return;
  await withBusy('Kaldırılıyor…', async () => {
    const result = await call(`${BASE}/rules/${ruleId}/coupons/remove`, {
      method: 'POST', body: { couponIds, reason, dryRun: false },
    });
    toast(`${num(result.count)} kod kaldırıldı.`, 'good');
    refreshCoupons();
  });
}

function generatorCard() {
  const prefix = h('input', 'kit-input');
  prefix.placeholder = 'BBD-';
  prefix.maxLength = 16;
  const count = numberBox(50, { min: 1, max: 50000 });
  const length = numberBox(8, { min: 4, max: 64 });
  const format = select([
    { value: 'alphanumeric', label: 'Harf + rakam' },
    { value: 'alphabetical', label: 'Yalnız harf' },
    { value: 'numeric', label: 'Yalnız rakam' },
  ]);
  const sample = h('div', 'pm-sample');
  const output = h('div', 'pm-batch');

  const refreshSample = debounce(async () => {
    try {
      const result = await call(`${BASE}/coupon-preview?prefix=${encodeURIComponent(prefix.value)}`
        + `&length=${Number(length.value) || 8}&fmt=${format.value}`);
      sample.replaceChildren(
        h('span', 'pm-sub', 'Örnek kodlar: '),
        h('code', undefined, (result.samples || []).join('  ')),
        h('span', 'pm-sub', ` — ${result.notice}`),
      );
    } catch (error) {
      sample.replaceChildren(h('span', 'pm-sub', error.message));
    }
  }, 350);
  closers.push(() => refreshSample.cancel());
  prefix.addEventListener('input', refreshSample);
  length.addEventListener('input', refreshSample);
  format.addEventListener('change', refreshSample);
  refreshSample();

  const row = h('div', 'pm-row');
  row.append(labelled('Önek', prefix, 'Kodun başına eklenir; boş bırakılabilir.'),
    labelled('Adet', count), labelled('Uzunluk', length, 'Önek hariç.'),
    labelled('Biçim', format));

  const run = button('Kodları üret', {
    variant: 'primary',
    onClick: async () => {
      const ruleId = Number(nodes.couponRule.value) || 0;
      if (!ruleId) { toast('Önce kampanya seçin.', 'warn'); return; }
      const reason = await askReason({
        title: 'Kupon üret',
        description: `${count.value} adet kod üretilecek ve mağazaya yazılacak. Kodları MAĞAZA `
          + 'üretir (benzersizliği o garanti eder); üretim sonrası liste CSV olarak rapor '
          + 'klasörüne de yazılır.',
        confirmLabel: 'Üret',
      });
      if (!reason) return;
      await withBusy('Kuponlar üretiliyor…', async () => {
        const result = await call(`${BASE}/rules/${ruleId}/coupons/generate`, {
          method: 'POST',
          body: {
            prefix: prefix.value,
            count: Number(count.value),
            length: Number(length.value),
            fmt: format.value,
            reason,
            dryRun: false,
          },
        });
        paintBatch(output, result);
        toast(`${num(result.produced || 0)} kod üretildi.`,
          result.produced ? 'good' : 'warn');
        if (result.notice) toast(result.notice, 'warn');
        if (result.fileError) toast(result.fileError, 'warn');
        refreshCoupons();
      });
    },
  });

  const box = h('div', 'pm-block');
  // SON KULLANMA ALANI BİLEREK YOK. Mağazanın toplu üretim ucu kuponun
  // süresini KURALIN bitiş tarihinden alıyor; buraya bir tarih alanı koyup
  // göndermek Laravel tarafında sessizce yok sayılırdı ve kullanıcı "son
  // kullanma verdim" sanırdı. Doğru yer kuralın takvimi; ekran oraya yollar.
  box.append(row, sample,
    hintBox('Kuponun son kullanma tarihi KURALIN bitiş tarihidir; ayrı bir alan yok. '
      + 'Kodların bir tarihte geçersizleşmesini istiyorsanız kampanyanın bitişini '
      + 'düzenleyicide ayarlayın.'),
    run, output);
  return card('Kupon üreteci', box, 'Önek + adet + uzunluk → CSV');
}

function paintBatch(host, result) {
  host.replaceChildren();
  if (!result.codes || !result.codes.length) {
    host.append(alertBox(result.notice || 'Kod üretilmedi.', 'warn'));
    return;
  }
  host.append(kpiRow([
    { label: 'İstenen', value: num(result.requested || 0) },
    { label: 'Üretilen', value: num(result.produced || 0),
      tone: result.produced === result.requested ? 'good' : 'warn' },
  ]));
  host.append(h('div', 'pm-codes', result.codes.slice(0, 40).join('  ')));
  if (result.codes.length > 40) {
    host.append(h('span', 'pm-sub', `… ve ${num(result.codes.length - 40)} kod daha.`));
  }
  const tools = h('div', 'pm-actions');
  tools.append(button('⤓ CSV indir', {
    variant: 'primary',
    onClick: () => {
      csvBlob(['Kupon kodu'], result.codes.map((code) => [code]), 'kupon-kodlari');
      toast(`${num(result.codes.length)} kod indirildi.`, 'good');
    },
  }));
  if (result.path) {
    tools.append(h('span', 'pm-sub', `Rapor klasörüne de yazıldı: ${result.path}`));
  }
  host.append(tools);
}

async function paintBatches(host) {
  host.replaceChildren(skeletonRows(3, 4));
  let payload;
  try {
    payload = await call(`${BASE}/batches?limit=20`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.items.length) {
    host.append(h('span', 'pm-sub', 'Henüz kupon partisi üretilmedi.'));
    return;
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px' },
      { key: 'prefix', label: 'Önek', width: '110px' },
      { key: 'count', label: 'Adet', width: '80px', align: 'num' },
      { key: 'actor', label: 'Kim', width: '120px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)', className: 'wrap' },
      { key: 'path', label: '', width: '120px',
        cell: (row) => button('Kodlar', {
          onClick: async () => {
            const result = await call(`${BASE}/batches/${row.token}`);
            csvBlob(['Kupon kodu'], (result.codes || []).map((code) => [code]),
              `kupon-${row.prefix || 'parti'}`);
            toast(`${num((result.codes || []).length)} kod indirildi.`, 'good');
          },
        }) },
    ],
    rows: payload.items,
    dense: true,
    rowKey: (row) => row.token,
  });
  host.append(table.node, hintBox('Parti kaydı YERELDİR: hangi önekle, kaç adet, kim ve '
    + 'hangi gerekçeyle ürettiğini mağaza tutmuyor. CSV kaybolursa kodlar buradan yeniden '
    + 'indirilir.'));
}

// =============================================================== performans

async function refreshPerformance() {
  const values = nodes.perfFilters.values();
  const range = values.range || {};
  nodes.perfWrap.replaceChildren(skeletonRows(6, 5));
  nodes.status?.set('Siparişler taranıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/performance?start=${range.start || ''}&end=${range.end || ''}`);
  } catch (error) {
    nodes.perfWrap.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.performance = payload;
  nodes.perfWrap.replaceChildren();
  nodes.status?.set(statusText(), !payload.connected);

  if (!payload.connected) {
    nodes.perfWrap.append(emptyState({
      title: 'Siparişler okunamadı',
      text: payload.error,
      actions: [button('Tekrar dene', { variant: 'primary', onClick: refreshPerformance })],
    }));
    return;
  }

  const totals = payload.totals || {};
  // İndirim tutarı sipariş LİSTESİNDE yok; servis kuponlu siparişleri tek tek
  // okuyor. Okunamayan varsa rakam eksiktir ve "—" yazılır: eksik toplamı tam
  // gibi göstermek kampanyayı olduğundan ucuz gösterirdi.
  const costKnown = payload.discountComplete !== false;
  nodes.perfWrap.append(kpiRow([
    { label: 'Kuponlu sipariş', value: num(totals.couponOrders || 0) },
    { label: 'Kupon cirosu', value: money(totals.couponRevenue || 0) },
    { label: 'İndirim maliyeti',
      value: costKnown ? money(totals.couponDiscount || 0) : '—',
      tone: 'warn',
      title: costKnown ? '' : 'Bazı siparişlerin indirim tutarı okunamadı.' },
    { label: 'Ortalama sepet (kuponlu)', value: money(totals.average || 0) },
    { label: 'Ortalama sepet (kuponsuz)', value: money(totals.plainAverage || 0) },
    { label: 'İndirim / ciro',
      value: totals.costRatio === null || totals.costRatio === undefined
        ? '—' : percent(totals.costRatio) },
  ]));
  if (!costKnown) {
    nodes.perfWrap.append(alertBox('Bazı siparişlerin indirim tutarı okunamadı; indirim '
      + 'maliyeti ve oranı "—" gösteriliyor. Ciro ve sipariş sayıları doğrudur.', 'warn'));
  }

  if (!payload.rows.length) {
    nodes.perfWrap.append(emptyState({
      title: 'Bu aralıkta kupon kullanılmamış',
      text: `${num(payload.orders)} sipariş tarandı, hiçbirinde kupon kodu yok. Aralığı `
        + 'genişletin ya da kampanyanın duyurulup duyurulmadığını kontrol edin.',
    }));
  } else {
    nodes.perfWrap.append(card('Kupon başına ciro',
      barChart(payload.rows.slice(0, 10).map((row) => ({
        label: row.code, value: row.revenue, display: money(row.revenue),
      })))));

    const table = dataTable({
      columns: [
        { key: 'code', label: 'Kod', width: 'minmax(0, 1.2fr)', className: 'mono' },
        { key: 'rule', label: 'Kampanya', width: 'minmax(0, 1.6fr)',
          cell: (row) => row.rule || '—' },
        { key: 'orders', label: 'Sipariş', width: '90px', align: 'num',
          cell: (row) => num(row.orders) },
        { key: 'revenue', label: 'Ciro', width: '130px', align: 'num',
          cell: (row) => money(row.revenue) },
        { key: 'discount', label: 'İndirim', width: '130px', align: 'num',
          cell: (row) => (row.discountKnown === false ? '—' : money(row.discount)) },
        { key: 'average', label: 'Ortalama', width: '130px', align: 'num',
          cell: (row) => money(row.average) },
        { key: 'costRatio', label: 'İndirim/ciro', width: '110px', align: 'num',
          cell: (row) => (row.costRatio === null ? '—' : percent(row.costRatio)) },
        { key: 'canceled', label: 'İptal', width: '80px', align: 'num',
          cell: (row) => num(row.canceled) },
      ],
      rows: payload.rows,
      sort: { key: 'revenue', dir: 'desc' },
    });
    nodes.perfWrap.append(table.node);
  }

  const usage = (payload.usage || []).filter((row) => row.used);
  if (usage.length) {
    nodes.perfWrap.append(card('Kural kullanımı', usageList(usage),
      'Kaç kez kullanıldı / limit'));
  }
  if (payload.truncated) {
    nodes.perfWrap.append(alertBox('Sipariş taraması tavana dayandı; rakamlar EKSİK olabilir. '
      + 'Aralığı daraltın ya da modül ayarındaki tavanı yükseltin.', 'warn'));
  }
  nodes.perfWrap.append(hintBox('Rakamlar sipariş listesinden hesaplanır: kupon kodu sipariş '
    + 'satırında durur, mağazada kampanya kırılımı veren bir rapor ucu yok. İPTAL edilen '
    + 'siparişler ciroya girmez, ayrı sütunda sayılır.'));
}

function usageList(rows) {
  const box = h('div', 'pm-usage-list');
  for (const row of rows) {
    const line = h('div', 'pm-usage-row');
    line.append(h('b', undefined, row.name), h('span', 'kit-spacer'),
      usageBar({ ...row, exhausted: row.limit > 0 && row.used >= row.limit }));
    box.append(line);
  }
  return box;
}

// ==================================================================== mount

function showView(key) {
  view = key;
  const isRules = key === 'rules';
  nodes.filters.node.hidden = !isRules;
  nodes.chips.node.hidden = !isRules;
  nodes.perfFilters.node.hidden = key !== 'performance';
  nodes.body.replaceChildren();

  if (isRules) {
    nodes.body.append(nodes.kpi, nodes.tableWrap, nodes.pager.node);
    if (!state.loaded.rules) { state.loaded.rules = true; refreshRules(); }
  } else if (key === 'catalog') {
    nodes.body.append(nodes.catalogHead, nodes.catalogWrap);
    refreshCatalog();
  } else if (key === 'coupons') {
    nodes.couponRule.replaceChildren();
    for (const option of couponRuleOptions()) {
      const item = h('option', undefined, option.label);
      item.value = String(option.value);
      nodes.couponRule.append(item);
    }
    if (state.coupons.ruleId) nodes.couponRule.value = String(state.coupons.ruleId);
    nodes.body.append(nodes.couponHead, nodes.couponBar, nodes.couponWrap,
      nodes.generator, nodes.batchCard);
    refreshCoupons();
    paintBatches(nodes.batchHost);
  } else {
    nodes.body.append(nodes.perfWrap);
    if (!state.performance) refreshPerformance();
  }
  nodes.status?.set(statusText(), false);
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const panel = h('div', 'kit-panel pm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = panel;
  toast = toaster(panel);
  report = reportChain({ api, root: panel, toast, base: BASE });

  nodes.tabs = tabBar([
    { key: 'rules', label: 'Sepet kuralları' },
    { key: 'catalog', label: 'Katalog kuralları' },
    { key: 'coupons', label: 'Kuponlar' },
    { key: 'performance', label: 'Performans' },
  ], 'rules', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Kampanya adı veya kupon kodu', width: '240px' },
      { kind: 'select', key: 'status', label: 'Durum', options: [
        { value: '', label: 'Tümü' },
        { value: 'active', label: 'Aktif' },
        { value: 'scheduled', label: 'Zamanlanmış' },
        { value: 'paused', label: 'Duraklatıldı' },
        { value: 'expired', label: 'Süresi doldu' },
      ] },
      { kind: 'select', key: 'kind', label: 'Tip',
        options: [{ value: '', label: 'Tümü — indirim tipi' }] },
      // Başlangıçta GİZLİ: kanal listesi `reference` isteğiyle geliyor.
      { kind: 'select', key: 'channel', label: 'Kanal', hidden: true, options: [] },
      { kind: 'select', key: 'group', label: 'Grup',
        options: [{ value: '', label: 'Tümü — müşteri grubu' }] },
    ],
    onChange: () => refreshRules({ page: 1 }),
    actions: [
      button('Yeni kural', { variant: 'primary', onClick: () => openRule(0) }),
      button('Yenile', { onClick: () => refreshRules() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm kuralları rapor klasörüne yaz', onClick: exportAll }),
      button('Kampanya listesi PDF', { onClick: () => report.run('rules', {}) }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => { state.chip = key; refreshRules({ page: 1 }); });
  nodes.status = statusLine();
  nodes.kpi = h('div', 'pm-kpi');
  nodes.tableWrap = h('div', 'pm-table');
  nodes.pager = pager({
    total: 0,
    page: 1,
    size: 50,
    onChange: ({ page, size }) => refreshRules({ page, size }),
  });

  // ---- katalog sekmesi
  nodes.catalogWrap = h('div', 'pm-table');
  nodes.catalogHead = h('div', 'pm-head');
  nodes.catalogHead.append(
    h('span', 'pm-sub', 'Katalog kuralı sepete değil vitrin fiyatına uygulanır.'),
    h('span', 'kit-spacer'),
    button('Yeni katalog kuralı', { variant: 'primary', onClick: () => openCatalogRule(0) }),
    button('Yenile', { onClick: refreshCatalog }),
  );

  // ---- kupon sekmesi
  nodes.couponRule = select([{ value: 0, label: 'Kural seçin' }]);
  nodes.couponRule.addEventListener('change', () => refreshCoupons());
  nodes.couponHead = h('div', 'pm-head');
  nodes.couponHead.append(labelled('Kampanya', nodes.couponRule), h('span', 'kit-spacer'),
    button('Kupon listesini dışa aktar', {
      onClick: async () => {
        const ruleId = Number(nodes.couponRule.value) || 0;
        if (!ruleId) { toast('Önce kampanya seçin.', 'warn'); return; }
        await withBusy('Kupon listesi yazılıyor…', async () => {
          const result = await call(`${BASE}/export`, {
            method: 'POST', body: { kind: 'coupons', ruleId },
          });
          toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
          if (result.notice) toast(result.notice, 'warn');
          nodes.status.set(`Dosya: ${result.path}`, Boolean(result.truncated));
        });
      },
    }));
  nodes.couponBar = h('div', 'pm-selbar');
  nodes.couponWrap = h('div', 'pm-table');
  nodes.generator = generatorCard();
  nodes.batchHost = h('div');
  nodes.batchCard = card('Üretilen partiler', nodes.batchHost, 'Yerel kayıt');

  // ---- performans sekmesi
  nodes.perfFilters = filterBar({
    fields: [{ kind: 'dateRange', key: 'range', label: 'Aralık' }],
    onChange: () => refreshPerformance(),
    actions: [
      button('Performans raporu PDF', {
        onClick: () => {
          const range = nodes.perfFilters.values().range || {};
          report.run('performance', { start: range.start || '', end: range.end || '' });
        },
      }),
    ],
  });
  nodes.perfWrap = h('div', 'pm-table');
  nodes.perfFilters.node.hidden = true;

  nodes.body = h('div', 'pm-body');
  panel.append(nodes.tabs.node, nodes.filters.node, nodes.perfFilters.node, nodes.chips.node,
    nodes.status.node, nodes.body);

  root.replaceChildren(panel);
  nodes.status.set('Kampanyalar alınıyor…');
  // Referans listeler ÖNCE gelir: koşul düzenleyicisi ve süzgeçler onlarsız
  // boş açılıyordu.
  loadReference().then(() => showView('rules'));

  return () => {
    nodes.filters?.destroy();
    nodes.perfFilters?.destroy();
    closers.forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    root.replaceChildren();
    state = blank();
    view = 'rules';
    busy = false;
  };
}

// ------------------------------------------------------------------- CSV

function exportVisible() {
  const headers = ['Ad', 'Kod', 'Tür', 'Değer', 'Başlangıç', 'Bitiş', 'Kullanım', 'Durum'];
  const rows = state.rules.map((row) => [
    row.name, row.code || '', row.actionLabel, row.valueLabel,
    row.startsFrom, row.endsTill, row.usage.label, row.statusLabel,
  ]);
  const written = csvBlob(headers, rows, `kampanyalar-sayfa-${state.page}`);
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Kural listesi yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: { kind: 'rules' } });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}
