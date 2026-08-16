// Dahili AI paneli — İKİ İŞ BİR EKRANDA.
//
// NE YAPAR:
//  1. KATALOG SAĞLIĞI — kural tabanlı denetim. YAPAY ZEKÂ YOKTUR, para
//     harcamaz. Görselsiz/açıklaması yetersiz/barkodsuz/kategorisiz ürün,
//     fiyat anormallikleri, boş kategori, kırık SEO ve çift url_key. Bu yarı,
//     mağazadaki BBD/AI uçları hiç yayınlanmasa bile bugün çalışır.
//  2. ARAÇLAR — MagicAI üstünden ÖNERİ üretir.
//
// EKRANIN KİMLİĞİ OLAN KURAL: AI HİÇBİR ŞEYİ DOĞRUDAN YAZMAZ. Her sonuç
// öneridir; fark tablosu satır satır gösterilir, kullanıcı tek tek ya da
// toplu onaylar. Onaysız uygulama yoktur ve uygulama fark tablosunu yeniden
// hesaplamaz — onayladığınız metnin aynısı gider.
//
// NE YAPMAZ:
//  · Tanımadığı aracı çalıştırmaz. Çıktının hangi alana yazılacağı
//    bilinmeden fark tablosu çizilemez; "onaysız uygulama yok" kuralı boşa
//    düşerdi.
//  · Bulgu SİLMEZ. "Bu bilerek böyle" demek için susturma vardır ve susturma
//    da gerekçe ister; geri alma satır ekleyerek yapılır.
//  · Ölçemediği parayı harcamaz. Bütçe sayacı okunamıyorsa ve tahmine izin
//    verilmemişse çalıştırma durur.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Katalog taraması 1.419 üründe ~29 istek eder. Sonuç bellekte taze
//    tutulur ve ekran TARAMA ZAMANINI yazar — bayat rakama bakıp bakmadığınızı
//    bilmelisiniz.
//  · Barkod alanı bu katalogda tanımlı olmayabilir; o zaman kural kapanır ve
//    ekran nedenini söyler (1.419 satırlık anlamsız kırmızı duvar yerine).
//  · "Önce" sütunu ALANIN HAM DEĞERİdir (HTML etiketleri dâhil): uygulama bu
//    metnin yerine yazacak, gerçekte neyin gideceğini görmelisiniz.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_ai/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_ai/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmSimple, confirmWithReason, csvBlob, h, loadStyles,
  money, num, percent, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { applyFilters, filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, progress, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { barChart, lineChart } from '../../ui-kit/charts.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_ai';

// YAYINDA DEĞİL — bilinçli kapalı, bozuk değil.
//
// SEBEP "UÇ YOK" DEĞİL, AKIŞ MODELİ UYUŞMAZLIĞI. Bir dönem burada
// "ai/* uçlarının hiçbiri yayında değil" deniyordu; artık böyle değil.
// Canlıda ölçüldü (2026-08-16, salt okuma):
//    GET  ai/drafts                     → 200 (taslak ailesi YAYINDA)
//    POST ai/drafts | .../apply | .../discard → route:list'te VAR
//    GET  ai/tools · ai/usage           → 404
//    POST ai/tools/{tool}/run           → route:list'te YOK
//
// Ekranın iki yarısı var:
//  · ARAÇLAR: bu panel "aracı çalıştır → sonucu al" varsayıyor; yayındaki
//    ControlApi ise "taslağı KM üretir → mağaza saklar → onaylanınca
//    uygular" akışı sunuyor. Uçların varlığı bunu kapatmıyor: isim
//    eşlemesiyle de kapanmaz, ekranın onay/fark tablosu mantığı taslak
//    modeline göre yeniden kurulmalı ve metin üretimi KM'de kalmalı.
//  · KATALOG SAĞLIĞI: kural motoru burada çalışıyor ve doğru sonuç üretiyor
//    (canlıda 81 görselsiz ürün — 2026-08-16'da bbd/catalog/health ile
//    doğrulandı, sayı değişmemiş), ama sonucun tek çıkışı Araçlar sekmesinin
//    onay akışı. Yarım ekran açık bırakmak, "düzelt" düğmesi hiçbir şey
//    yapmayan bir liste demek olurdu.
//
// Bu yüzden ekran "geliştiriliyor" der ve durur. Aşağıdaki kodun tamamı
// yerinde; model uzlaştırıldığında bu bayrak `true` olur, başka hiçbir yer
// değişmez. Menü girdisi kalır — kaybolan menü, kullanıcıya "bir şey bozuldu"
// dedirtir; kapalı olduğunu SÖYLEYEN ekran demez.
const YAYINDA = false;

const SEVERITY = {
  high: { label: 'Ağır', tone: 'bad' },
  medium: { label: 'Orta', tone: 'warn' },
  low: { label: 'Hafif', tone: 'dim' },
};

const BUDGET_TONE = { ok: 'good', warn: 'warn', block: 'bad' };

const EMPTY = {
  tab: 'tools',
  tools: [], toolsConnected: false, toolsError: '', endpointReady: true, maxTargets: 25,
  budget: null,
  catalog: null, findings: [], catalogPage: 1, catalogSize: 50, selection: [],
  runs: [], runsPage: 1, runsSize: 50, runsTotal: 0, runsConnected: false,
  usage: null,
  settings: null,
  loaded: {},
};

let api = null;
let open = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY };

const nodes = {};
const closers = [];        // cleanup'ta çağrılacak gerçek kaynak bırakıcılar

// ------------------------------------------------------------------ araçlar

/** Sunucu `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
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

/** Para harcayan ve mağazaya yazan her işlem buradan geçer. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function trTime(value) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '—';
}

// ------------------------------------------------------------- bütçe şeridi

/**
 * Bütçe her sekmede görünür. Para harcayan tek ekran burası; kullanıcı hangi
 * sekmede olursa olsun kalanı görmeden düğmeye basmamalı.
 */
function renderBudget() {
  const host = nodes.budget;
  if (!host) return;
  host.replaceChildren();
  const budget = state.budget;
  if (!budget) return;

  const bar = h('div', 'ai-budget');
  const tone = BUDGET_TONE[budget.level] || '';
  // Renk tek başına anlam taşımaz: rozetin yanında her zaman rakam durur.
  bar.append(badge(budget.allowed ? 'Çalıştırma açık' : 'Çalıştırma durduruldu', tone));
  if (budget.budget > 0) {
    const meter = progress([]);
    meter.percent(Math.min(100, budget.percent), `${money(budget.spent)} / `
      + `${money(budget.budget)} · kalan ${money(budget.remaining)} (%${budget.percent})`);
    meter.node.classList.add('ai-meter');
    bar.append(meter.node);
  } else {
    bar.append(h('span', 'ai-sub', `Bu ay harcanan: ${money(budget.spent)} · aylık sınır yok`));
  }
  if (!budget.measured) bar.append(badge('sayaç tahmini', 'warn'));
  host.append(bar);
  if (budget.level !== 'ok') host.append(alertBox(budget.message, tone === 'bad' ? 'bad' : 'warn'));
}

// =================================================================== ARAÇLAR

//: Araç listesi iki yerden istenebilir (sekme açılışı ve başka panelden gelen
//: hedefler). Uçuştaki isteğin sözü saklanır: aynı listeyi iki kez çekmek,
//: ikinci yanıt geldiğinde açık çekmecenin altındaki kartları yeniden çizerdi.
let toolsPromise = null;

function ensureTools() {
  if (!toolsPromise) toolsPromise = loadTools().finally(() => { toolsPromise = null; });
  return toolsPromise;
}

async function loadTools() {
  nodes.toolsHost.replaceChildren(skeletonRows(3, 3));
  let payload;
  try {
    payload = await api(`${BASE}/tools`);
  } catch (error) {
    nodes.toolsHost.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.tools = payload.tools || [];
  state.toolsConnected = Boolean(payload.connected);
  state.toolsError = payload.error || '';
  state.endpointReady = payload.endpointReady !== false;
  state.maxTargets = payload.maxTargets || 25;
  state.budget = payload.budget || state.budget;
  state.loaded.tools = true;
  renderBudget();
  renderTools();
}

function renderTools() {
  const host = nodes.toolsHost;
  host.replaceChildren();

  if (!state.toolsConnected) {
    host.append(alertBox(
      state.endpointReady
        ? `Mağazaya ulaşılamadı — ${state.toolsError}. Katalog Sağlığı sekmesi kural `
          + 'tabanlıdır ve çalışmaya devam eder.'
        // Uç adı TEK TEK yazılır: "ai/* yayında değil" demek yanlıştı —
        // taslak uçları yayında, eksik olan yalnız araç/kullanım uçları.
        // Kullanıcı hangi parçanın beklediğini bilmeden destek çağıramaz.
        : 'Mağazadaki AI araç uçları (ai/tools, ai/tools/{tool}/run, ai/usage) yayında '
          + 'değil; taslak uçları (ai/drafts) yayında. Araçlar eksik uçlar gelince '
          + 'kendiliğinden açılacak. Katalog Sağlığı sekmesi AI kullanmaz ve şimdi çalışır.',
      state.endpointReady ? 'bad' : 'warn'));
  }

  const grid = h('div', 'ai-cards');
  for (const tool of state.tools) {
    const box = h('article', `ai-card${tool.runnable ? '' : ' off'}`);
    const head = h('div', 'ai-card-head');
    head.append(h('b', undefined, tool.label));
    head.append(tool.readOnly
      ? badge('yazmaz', 'info')
      : badge(`${tool.writes.length} alan yazar`, 'warn'));
    if (!tool.known) head.append(badge('tanınmıyor', 'dim'));
    box.append(head);
    box.append(h('p', 'ai-card-text', tool.summary || '—'));

    if (tool.writes.length) {
      box.append(h('div', 'ai-sub',
        `Önerdiği alanlar: ${tool.writes.map((item) => item.label).join(', ')}`));
    }
    if (tool.note) box.append(h('div', 'ai-sub', tool.note));
    if (tool.blockReason) box.append(alertBox(tool.blockReason, 'warn'));

    const actions = h('div', 'ai-card-actions');
    actions.append(button('Çalıştır', {
      variant: 'primary',
      disabled: !tool.runnable || !(state.budget?.allowed ?? true),
      title: state.budget?.allowed === false ? state.budget.message : '',
      onClick: () => openRunDrawer(tool),
    }));
    box.append(actions);
    grid.append(box);
  }
  host.append(grid);

  host.append(hintBox(
    'AI hiçbir şeyi doğrudan yazmaz. Her çalıştırma ÖNERİ üretir; fark tablosunu görüp '
    + 'satır satır onaylarsınız. Onaysız hiçbir şey mağazaya gitmez ve uygulama, '
    + 'onayladığınız metni yeniden üretmez.'));
}

/** Çalıştırma çekmecesi: hedefler + kuru prova + gerçek çalıştırma. */
function openRunDrawer(tool, prefill = []) {
  const forms = [];
  const dropForms = () => { forms.forEach((form) => form.destroy()); forms.length = 0; };
  const box = drawerFor(tool.label, tool.readOnly ? 'Mağazaya yazmaz' : 'Öneri üretir',
    dropForms);

  const isProduct = tool.target === 'product';
  const form = formGrid({
    fields: isProduct
      ? [{
        key: 'targets', label: 'Hedef ürün numaraları', type: 'textarea', wide: true,
        hint: `Virgül ya da satırla ayırın. Tek çalıştırmada en çok ${state.maxTargets} ürün: `
          + 'her hedefin mevcut değeri ayrı bir istekle okunur ve mağaza dakikada 60 kabul eder.',
      }]
      : [{
        key: 'days', label: 'Kaç günlük veri', type: 'number', min: 1, max: 180,
        hint: 'Bu araç ürün seçmez; dönemi seçersiniz.',
      }],
    value: isProduct ? { targets: prefill.join(', ') } : { days: 30 },
  });
  forms.push(form);
  box.body.append(card('Girdi', form.node, tool.note || ''));

  const result = h('div', 'ai-run-result');
  box.body.append(result);

  const readTargets = () => String(form.draft().targets || '')
    .split(/[\s,;]+/).map((item) => Number(item)).filter((item) => Number.isInteger(item) && item > 0);

  async function fire(dryRun) {
    const targetIds = isProduct ? readTargets() : [];
    if (isProduct && !targetIds.length) { toast('Hedef ürün numarası girin.', 'bad'); return; }
    const reason = await askReason({
      title: dryRun ? `${tool.label} — kuru prova` : tool.label,
      description: dryRun
        ? 'Kuru prova model çağrısı YAPMAZ ve jeton harcamaz; yalnız neyin gönderileceğini '
          + 'söyler. Gerekçe yine denetim kaydına yazılır.'
        : `${isProduct ? `${targetIds.length} ürün için ` : ''}öneri üretilecek. BU İŞLEM `
          + 'JETON HARCAR ve aylık bütçeye işlenir. Mağazaya hiçbir şey yazılmaz.',
      confirmLabel: dryRun ? 'Kuru provayı çalıştır' : 'Çalıştır (jeton harcar)',
    });
    if (!reason) return;

    result.replaceChildren(skeletonRows(4, 4));
    await withBusy('Öneri üretiliyor…', async () => {
      const run = await call(`${BASE}/tools/${tool.key}/run`, {
        method: 'POST',
        body: {
          targetIds,
          params: isProduct ? {} : { days: Number(form.draft().days) || 30 },
          reason,
          dryRun,
        },
      });
      state.budget = run.budget || state.budget;
      renderBudget();
      paintRun(result, run, { onApplied: () => { box.close(); loadRuns(); } });
      toast(run.dryRun ? 'Kuru prova tamam: jeton harcanmadı.' : 'Öneriler hazır.',
        run.dryRun ? 'warn' : 'good');
      state.loaded.runs = false;
    });
  }

  const actions = h('div', 'ai-actions');
  actions.append(
    button('Maliyet provası (jeton harcamaz)', { onClick: () => fire(true) }),
    button('Çalıştır (jeton harcar)', { variant: 'danger', onClick: () => fire(false) }),
  );
  box.body.append(actions);
}

/**
 * Çekmece açar ve TEMİZLEYİCİSİNİ kaydeder.
 *
 * Çekmecenin kendi kaynakları (formGrid → tarih alanı global dinleyici tutar)
 * çekmece kapanınca bırakılır; panel cleanup'ına bırakılırsa her açılışta bir
 * tane daha birikir. Panel kapanırken çekmece hâlâ açıksa `closers` yine
 * çalıştırır — iki kez çağrılması zararsızdır.
 *
 * Kapanan çekmecenin kaydı `closers` dizisinden ÇIKARILIR: çıkarılmasaydı
 * dizi oturum boyunca her bulgu tıklamasında bir eleman büyür ve panel
 * kapanırken yüzlerce ölü temizleyici çağrılırdı.
 */
function drawerFor(title, subtitle, onClose) {
  const release = () => {
    const at = closers.indexOf(release);
    if (at >= 0) closers.splice(at, 1);
    onClose?.();
  };
  closers.push(release);
  return drawer(nodes.root, { title, subtitle, onClose: release });
}

// ------------------------------------------------------------ fark tablosu

/**
 * FARK TABLOSU — ekranın kimliği. Her satır ayrı onaylanabilir; hiçbir şey
 * varsayılan olarak seçili DEĞİLDİR ve "hepsini seç" bilinçli bir tıklamadır.
 */
function paintRun(host, run, { onApplied } = {}) {
  host.replaceChildren();

  const summary = run.summary || {};
  host.append(kpiRow([
    { label: 'Öneri', value: num(summary.total || 0) },
    { label: 'Uygulanabilir', value: num(summary.applicable || 0), tone: 'good' },
    { label: 'Değişmeyen', value: num(summary.unchanged || 0), tone: 'muted' },
    { label: 'Atlanan', value: num(summary.skipped || 0), tone: 'warn' },
    { label: 'Jeton', value: num(run.tokens || 0) },
    { label: 'Maliyet', value: money(run.cost || 0) },
  ]));

  if (run.text) host.append(card('Özet', h('p', 'ai-summary', run.text)));

  if (!(run.rows || []).length) {
    // Salt okunur araçta (yorum duygu özeti) fark tablosu OLMAMASI doğrudur;
    // "öneri üretilmedi" demek kullanıcıyı hataya inandırırdı.
    host.append(run.readOnly
      ? hintBox(run.note || 'Bu araç mağazaya yazmaz; çıktısı yukarıdaki özettir.')
      : emptyState({
        title: 'Öneri üretilmedi',
        text: run.note || 'Model bu girdi için sonuç döndürmedi.',
      }));
    return;
  }

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kayıt', width: 'minmax(0, 1.4fr)',
        cell: (row) => {
          const cell = h('span', 'ai-name');
          cell.append(clip(h('b'), row.name, 34),
            h('span', 'ai-sub', row.sku || `#${row.targetId}`));
          return cell;
        } },
      { key: 'fieldLabel', label: 'Alan', width: '120px' },
      { key: 'before', label: 'Önce', width: 'minmax(0, 1.6fr)',
        cell: (row) => textCell(row.beforeKnown ? row.before : '', !row.beforeKnown) },
      { key: 'after', label: 'Sonra (öneri)', width: 'minmax(0, 1.6fr)',
        cell: (row) => textCell(row.after, false, 'ai-after') },
      { key: 'confidence', label: 'Güven', width: '80px', align: 'num',
        cell: (row) => (row.confidence === null || row.confidence === undefined
          ? '—' : percent(row.confidence, 0)) },
      { key: 'state', label: 'Durum', width: '112px',
        cell: (row) => {
          if (row.state === 'applied') return badge('Uygulandı', 'good');
          if (row.skipped) {
            const chip = badge('Atlandı', 'dim');
            chip.title = row.skipReason || '';
            return chip;
          }
          if (!row.changed) return badge('Değişiklik yok', 'muted');
          return badge('Onay bekliyor', 'warn');
        } },
    ],
    rows: run.rows,
    selectable: true,
    dense: true,
    rowKey: (row) => String(row.id),
    onSelect: () => paintApplyBar(),
  });
  host.append(table.node);

  const bar = h('div', 'ai-apply');
  host.append(bar);

  function paintApplyBar() {
    bar.replaceChildren();
    const chosen = table.selectedRows()
      .filter((row) => !row.skipped && row.changed && row.state !== 'applied');
    bar.append(h('b', undefined, `${num(chosen.length)} öneri onaylandı`));
    bar.append(h('span', 'kit-spacer'));
    bar.append(button('Fark tablosunu CSV al', {
      onClick: () => {
        csvBlob(['Kayıt', 'SKU', 'Alan', 'Önce', 'Sonra', 'Güven', 'Not'],
          run.rows.map((row) => [row.name, row.sku, row.fieldLabel,
            row.beforeKnown ? row.before : '(bilinmiyor)', row.after,
            row.confidence ?? '', row.skipReason || row.note || '']),
          `ai-oneri-${run.tool || 'fark'}`);
        toast('Fark tablosu indirildi.', 'good');
      },
    }));
    bar.append(button(`Onaylananları uygula (${num(chosen.length)})`, {
      variant: 'danger',
      disabled: !run.applicable || chosen.length === 0,
      title: run.applicable ? '' : (run.note || 'Uygulama şu an açık değil.'),
      onClick: () => applySelection(run, chosen, onApplied),
    }));
    if (!run.applicable) bar.append(h('span', 'ai-sub', run.note || ''));
  }
  paintApplyBar();

  if (run.note) host.append(hintBox(run.note));
}

function textCell(value, unknown, extra = '') {
  const cell = h('span', `ai-text${extra ? ` ${extra}` : ''}`);
  if (unknown) {
    cell.textContent = '—';
    cell.title = 'Mevcut değer okunamadı; uydurma eski değer yazılmaz.';
    return cell;
  }
  const shown = String(value || '');
  cell.textContent = shown.length > 90 ? `${shown.slice(0, 89)}…` : (shown || '—');
  if (shown) cell.title = shown;
  return cell;
}

async function applySelection(run, chosen, onApplied) {
  const reason = await askReason({
    title: 'Onaylanan önerileri uygula',
    description: `${chosen.length} öneri mağazaya yazılacak. Uygulama fark tablosunu YENİDEN `
      + 'HESAPLAMAZ: onayladığınız metnin aynısı gider. Eski değerler mağazada geri '
      + 'alınamaz — kuşkulu satırları seçimden çıkarın.',
    confirmLabel: 'Uygula',
  });
  if (!reason) return;
  await withBusy('Uygulanıyor…', async () => {
    const result = await call(`${BASE}/runs/${run.token}/apply`, {
      method: 'POST',
      body: { selections: chosen.map((row) => row.id), reason, dryRun: false },
    });
    toast(`${num(result.applied)} öneri uygulandı.`, result.applied ? 'good' : 'warn');
    if (result.notice) toast(result.notice, 'warn');
    state.loaded.catalog = false;
    onApplied?.();
  });
}

// ========================================================== KATALOG SAĞLIĞI

async function loadCatalog(refresh = false) {
  nodes.catalogTable.replaceChildren(skeletonRows(8, 6));
  nodes.status.set(refresh ? 'Katalog yeniden taranıyor…' : 'Katalog sağlığı okunuyor…');
  let payload;
  try {
    payload = await api(`${BASE}/catalog?refresh=${refresh ? 'true' : 'false'}`);
  } catch (error) {
    nodes.catalogTable.replaceChildren(alertBox(error.message, 'bad'));
    nodes.status.set(error.message, true);
    return;
  }
  state.catalog = payload;
  state.findings = payload.findings || [];
  state.catalogPage = 1;
  state.selection = [];
  state.loaded.catalog = true;
  renderCatalog();
  nodes.status.set(catalogStatus(), !payload.connected);
}

function catalogStatus() {
  const payload = state.catalog;
  if (!payload) return '';
  if (!payload.connected) return `Mağazaya ulaşılamadı — ${payload.error}`;
  const summary = payload.summary;
  return `Kural tabanlı denetim · ${num(summary.scanned)} ürün tarandı · `
    + `${num(summary.total)} bulgu · tarama: ${trTime(payload.scannedAt)}`;
}

function visibleFindings() {
  const values = nodes.catalogFilters ? nodes.catalogFilters.values() : {};
  return applyFilters(state.findings, values, {
    q: { kind: 'search', fields: ['name', 'sku', 'detail', 'kindLabel'] },
    kind: { kind: 'equals', field: 'kind' },
    severity: { kind: 'equals', field: 'severity' },
    targetType: { kind: 'equals', field: 'targetType' },
  });
}

function renderCatalog() {
  const payload = state.catalog;
  nodes.catalogKpi.replaceChildren();
  nodes.catalogTable.replaceChildren();
  nodes.catalogNotes.replaceChildren();

  if (!payload) return;
  if (!payload.connected) {
    nodes.catalogTable.append(emptyState({
      title: 'Katalog okunamadı',
      text: payload.error || 'Mağazaya ulaşılamadı.',
      actions: [button('Yeniden tara', { variant: 'primary', onClick: () => loadCatalog(true) })],
    }));
    return;
  }

  const summary = payload.summary;
  nodes.catalogKpi.append(kpiRow([
    { label: 'Taranan ürün', value: num(summary.scanned) },
    { label: 'Bulgu', value: num(summary.total) },
    { label: 'Etkilenen kayıt', value: num(summary.affected), tone: 'warn' },
    { label: 'Temiz ürün', value: num(summary.clean), tone: 'good' },
    { label: 'Ağır', value: num(summary.bySeverity.high), tone: 'bad' },
    { label: 'Susturulan', value: num(summary.muted || 0), tone: 'muted' },
  ]));

  // Bulgu türü açılırı veriden doldurulur: sıfır bulgulu türü seçenek olarak
  // sunmak, kullanıcıyı boş listeye yollar.
  nodes.catalogFilters.options('kind', [
    { value: '', label: 'Tümü — bulgu türü' },
    ...summary.byKind.filter((item) => item.count > 0)
      .map((item) => ({ value: item.kind, label: `${item.label} (${item.count})` })),
  ]);

  for (const warning of payload.warnings || []) {
    nodes.catalogNotes.append(alertBox(warning, 'warn'));
  }

  const rows = visibleFindings();
  const start = (state.catalogPage - 1) * state.catalogSize;
  const table = dataTable({
    columns: [
      { key: 'kindLabel', label: 'Bulgu', width: 'minmax(0, 1fr)' },
      { key: 'severity', label: 'Önem', width: '86px',
        cell: (row) => badge(SEVERITY[row.severity]?.label || row.severity,
          SEVERITY[row.severity]?.tone || '') },
      { key: 'name', label: 'Kayıt', width: 'minmax(0, 1.6fr)',
        cell: (row) => {
          const cell = h('span', 'ai-name');
          cell.append(clip(h('b'), row.name, 40), h('span', 'ai-sub',
            row.targetType === 'category' ? `Kategori #${row.targetId}`
              : (row.sku || `Ürün #${row.targetId}`)));
          return cell;
        } },
      { key: 'detail', label: 'Neden', width: 'minmax(0, 2.4fr)', className: 'wrap' },
    ],
    rows: rows.slice(start, start + state.catalogSize),
    selectable: true,
    dense: true,
    rowKey: (row) => row.id,
    empty: emptyState({
      title: state.findings.length ? 'Bu süzgeçte bulgu yok' : 'Katalog bu kurallarda temiz',
      text: state.findings.length
        ? 'Süzgeçleri gevşetin ya da temizleyin.'
        : `${num(summary.scanned)} ürün tarandı ve hiçbir kural takılmadı.`,
      actions: state.findings.length
        ? [button('Filtreyi temizle', { onClick: () => nodes.catalogFilters.reset() })] : [],
    }),
    onRow: (row) => openFinding(row),
    onSelect: () => paintFindingBar(table),
  });
  nodes.catalogTable.append(table.node);
  nodes.catalogPager.update({ total: rows.length, page: state.catalogPage,
    size: state.catalogSize });
  paintFindingBar(table);

  if (summary.disabledRules?.length) {
    nodes.catalogNotes.append(hintBox(
      'Kapalı denetimler: ' + summary.disabledRules.join(', ')
      + '. Bir kural veri olmadığı için kapandıysa ekran kırmızı duvar üretmek yerine '
      + 'susar; nedeni yukarıdaki uyarıda yazar.'));
  }
}

function paintFindingBar(table) {
  const bar = nodes.catalogBar;
  bar.replaceChildren();
  const chosen = table.selectedRows();
  if (!chosen.length) { bar.classList.remove('on'); return; }
  bar.classList.add('on');

  const fixable = chosen.filter((row) => row.tool);
  bar.append(h('b', undefined, `${num(chosen.length)} bulgu seçildi`));
  bar.append(h('span', 'kit-spacer'));

  if (fixable.length) {
    // İKİ YARIYI BİRLEŞTİREN DÜĞME: kural tabanlı denetim neyin eksik
    // olduğunu bulur, AI aracı öneriyi üretir, onay yine kullanıcıda kalır.
    //
    // TEK ARAÇ: seçimde birden çok bulgu türü varsa ilkinin aracı açılır ve
    // düğme YALNIZ o araca giden hedefleri sayar. İki farklı aracı tek
    // çalıştırmada birleştirmek, iki ayrı fark tablosunu tek listeye karıştırıp
    // onayı okunmaz kılardı.
    const first = fixable[0].tool;
    const tool = state.tools.find((item) => item.key === first);
    const targets = fixable
      .filter((row) => row.tool === first && row.targetType === 'product')
      .map((row) => row.targetId)
      .slice(0, state.maxTargets);
    bar.append(button(`${tool?.label || 'AI'} ile gider (${num(targets.length)})`, {
      variant: 'primary',
      disabled: !tool?.runnable || targets.length === 0,
      title: tool?.blockReason
        || (fixable.length > targets.length
          ? 'Seçimde birden çok bulgu türü var; yalnız ilkinin aracı açılır.'
          : 'Seçili bulguları giderecek aracı hedeflerle açar'),
      onClick: () => {
        nodes.tabs.select('tools');
        openRunDrawer(tool, targets);
      },
    }));
  }

  bar.append(button('Sustur (gerekçeli)', {
    onClick: () => muteFindings(chosen),
  }));
  bar.append(button('Seçimi bırak', {
    variant: 'ghost', onClick: () => { table.clearSelection(); paintFindingBar(table); },
  }));
}

async function muteFindings(rows) {
  const reason = await askReason({
    title: 'Bulguları sustur',
    description: `${rows.length} bulgu listeden kalkar. SİLME DEĞİLDİR: kayıt kalır, `
      + 'Ayarlar sekmesinden geri alınabilir. Gerekçe denetim kaydına yazılır.',
    confirmLabel: 'Sustur',
  });
  if (!reason) return;
  await withBusy('Susturuluyor…', async () => {
    for (const row of rows) {
      await call(`${BASE}/catalog/mute`, {
        method: 'POST',
        body: { findingKey: row.id, kind: row.kind, targetId: row.targetId, reason },
      });
    }
    toast(`${num(rows.length)} bulgu susturuldu.`, 'good');
    await loadCatalog();
  });
}

function openFinding(row) {
  const box = drawerFor(row.kindLabel, row.sku || `#${row.targetId}`, () => {});
  box.body.append(card('Bulgu', h('p', 'ai-summary', row.detail),
    SEVERITY[row.severity]?.label || ''));
  const actions = h('div', 'ai-actions');
  if (row.targetType === 'product') {
    actions.append(button('Ürünler ekranında aç', {
      variant: 'primary',
      onClick: () => { box.close(); open?.('store_products', { productId: row.targetId }); },
    }));
  }
  if (row.tool) {
    const tool = state.tools.find((item) => item.key === row.tool);
    actions.append(button('AI ile gider', {
      disabled: !tool?.runnable,
      title: tool?.blockReason || '',
      onClick: () => { box.close(); nodes.tabs.select('tools'); openRunDrawer(tool, [row.targetId]); },
    }));
  }
  actions.append(button('Sustur', { onClick: () => { box.close(); muteFindings([row]); } }));
  box.body.append(actions);
  box.body.append(hintBox(
    'Bu bulgu KURAL TABANLIDIR: yapay zekâ kullanılmamıştır. Eşikleri Ayarlar '
    + 'sekmesinden değiştirebilirsiniz; değiştirdiğinizde tarama kendiliğinden yenilenir.'));
}

// ========================================================== ÇALIŞMA GEÇMİŞİ

async function loadRuns(page = 1) {
  nodes.runsTable.replaceChildren(skeletonRows(6, 6));
  let payload;
  try {
    payload = await api(`${BASE}/runs?page=${page}&size=${state.runsSize}`);
  } catch (error) {
    nodes.runsTable.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.runs = payload.items || [];
  state.runsPage = payload.page || page;
  state.runsTotal = payload.total || 0;
  state.runsConnected = Boolean(payload.connected);
  state.loaded.runs = true;
  renderRuns();
}

function renderRuns() {
  nodes.runsTable.replaceChildren();
  if (!state.runsConnected) {
    nodes.runsTable.append(alertBox(
      'Mağazanın AI geçmişi okunamadı; aşağıda Kontrol Merkezi\'nin YEREL defteri var. '
      + 'Yerel defter gerekçeyi taşır ama başka araçlardan yapılan çalıştırmaları görmez.',
      'warn'));
  }
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px', cell: (row) => trTime(row.createdAt) },
      { key: 'toolLabel', label: 'Araç', width: 'minmax(0, 1.3fr)' },
      { key: 'actor', label: 'Kim', width: '130px' },
      { key: 'status', label: 'Sonuç', width: '120px',
        cell: (row) => {
          const map = { preview: ['Öneri hazır', 'info'], applied: ['Uygulandı', 'good'],
            failed: ['Hata', 'bad'], dry_run: ['Kuru prova', 'warn'],
            readonly: ['Özet', 'info'] };
          const [label, tone] = map[row.status] || [row.status || '—', ''];
          const chip = badge(label, tone);
          if (row.error) chip.title = row.error;
          return chip;
        } },
      { key: 'targets', label: 'Hedef', width: '78px', align: 'num',
        cell: (row) => num(row.targets || 0) },
      { key: 'applied', label: 'Uygulanan', width: '92px', align: 'num',
        cell: (row) => (row.applied ? num(row.applied) : '—') },
      { key: 'tokens', label: 'Jeton', width: '90px', align: 'num',
        cell: (row) => num(row.tokens || 0) },
      { key: 'cost', label: 'Maliyet', width: '110px', align: 'num',
        cell: (row) => money(row.cost || 0) },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)', className: 'wrap',
        cell: (row) => (row.local ? (row.reason || '—')
          : textCell('', true)) },
    ],
    rows: state.runs,
    dense: true,
    rowKey: (row) => row.token || `uzak-${row.runId}`,
    empty: emptyState({
      title: 'Henüz çalıştırma yok',
      text: 'Araçlar sekmesinden bir araç çalıştırdığınızda her çalışma gerekçesi, jetonu '
        + 've maliyetiyle burada listelenir.',
    }),
    onRow: (row) => (row.token ? openRunHistory(row) : toast(
      'Bu çalışma mağaza tarafında yapılmış; fark tablosu Kontrol Merkezi\'nde yok.', 'warn')),
  });
  nodes.runsTable.append(table.node);
  nodes.runsPager.update({ total: state.runsTotal, page: state.runsPage,
    size: state.runsSize });
}

async function openRunHistory(row) {
  const box = drawerFor(row.toolLabel || row.tool, trTime(row.createdAt), () => {});
  box.body.append(skeletonRows(4, 4));
  let detail;
  try {
    detail = await call(`${BASE}/runs/${row.token}`);
  } catch (error) {
    box.body.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  box.body.replaceChildren();
  box.body.append(card('Gerekçe', h('p', 'ai-summary', detail.reason || '—'),
    `${detail.actor || '—'} · ${trTime(detail.createdAt)}`));
  const host = h('div', 'ai-run-result');
  box.body.append(host);
  paintRun(host, { ...detail, note: detail.applicable ? '' : 'Bu çalışma uygulanmış ya da '
    + 'uygulanabilir durumda değil.' }, { onApplied: () => { box.close(); loadRuns(); } });
}

// ======================================================= KULLANIM & MALİYET

async function loadUsage() {
  nodes.usageHost.replaceChildren(skeletonRows(4, 3));
  let payload;
  try {
    payload = await api(`${BASE}/usage?days=30`);
  } catch (error) {
    nodes.usageHost.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.usage = payload;
  state.budget = payload.budget || state.budget;
  state.loaded.usage = true;
  renderBudget();
  renderUsage();
}

function renderUsage() {
  const host = nodes.usageHost;
  const usage = state.usage;
  host.replaceChildren();
  if (!usage) return;

  if (!usage.measured) {
    host.append(alertBox(
      'Mağaza sayacı okunamadı; aşağıdaki rakamlar Kontrol Merkezi\'nin YEREL defterinden '
      + 'hesaplandı ve başka araçlardan yapılan çalıştırmaları içermez.', 'warn'));
  }

  const budget = usage.budget || {};
  host.append(kpiRow([
    { label: 'Bu ay harcanan', value: money(usage.spent) },
    { label: 'Aylık bütçe', value: budget.budget ? money(budget.budget) : 'sınırsız' },
    { label: 'Kalan', value: budget.budget ? money(budget.remaining) : '—',
      tone: budget.level === 'block' ? 'bad' : (budget.level === 'warn' ? 'warn' : 'good') },
    { label: 'Jeton', value: num(usage.tokens || 0) },
    { label: 'Dönem', value: usage.month || '—' },
  ]));

  host.append(card('Günlük maliyet',
    lineChart((usage.days || []).map((point) => ({ label: point.label, value: point.value })),
      { valueFormat: (value) => money(Math.round(value)) }),
    usage.measured ? 'mağaza sayacı' : 'yerel defter (tahmini)'));

  host.append(card('Araç bazlı dağılım',
    barChart((usage.byTool || []).map((row) => ({
      label: row.label, value: row.cost,
      display: `${money(row.cost)} · ${num(row.runs)} çalışma`,
    })))));

  // Maliyet raporu (`build_report('usage')`) backend'de vardı ama hiçbir düğme
  // onu çağırmıyordu — ekrandan ulaşılamayan bir rapor, olmayan rapordur.
  const actions = h('div', 'ai-actions');
  actions.append(button('Maliyet raporu', {
    variant: 'primary',
    title: 'Dönem icmalini PDF olarak rapor klasörüne yazar ve önizler',
    onClick: () => report.run('usage', { days: 30 }),
  }));
  host.append(actions);

  host.append(hintBox(
    'Bütçe kapısı her çalıştırmadan ÖNCE sorulur. Sınıra gelince ne olacağını (durdur / '
    + 'uyar) Ayarlar sekmesinden seçersiniz; varsayılan durdurmaktır çünkü para harcayan '
    + 'işte güvenli taraf odur.'));
}

// ================================================================= AYARLAR

let settingsForms = [];

async function loadSettings() {
  settingsForms.forEach((form) => form.destroy());
  settingsForms = [];
  nodes.settingsHost.replaceChildren(skeletonRows(5, 2));
  let payload;
  try {
    payload = await call(`${BASE}/settings`);
  } catch (error) {
    nodes.settingsHost.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  state.settings = payload;
  state.budget = payload.budget || state.budget;
  state.loaded.settings = true;
  renderBudget();
  renderSettings();
}

function renderSettings() {
  const host = nodes.settingsHost;
  const payload = state.settings;
  host.replaceChildren();
  const values = payload.values;

  const source = (key) => (payload.sources[key] === 'ekran' ? 'ekrandan değiştirildi'
    : 'varsayılan');

  const budgetForm = formGrid({
    fields: [
      // ZORUNLU. Boş bırakılan ya da çözülemeyen tutarda `parseMoney` null
      // döner; alan zorunlu değilken bu null doğrulamadan geçiyor ve kaydetme
      // gövdesi sessizce ESKİ bütçeyi geri gönderiyordu — ekran "kaydedildi"
      // der, sınır değişmezdi. Sınırsız istemek için 0 yazılır.
      { key: 'monthlyBudget', label: 'Aylık bütçe', type: 'money', required: true,
        hint: `0 = sınır yok. ${source('monthly_budget')}.` },
      { key: 'budgetBehavior', label: 'Sınıra gelince', type: 'select',
        options: [{ value: 'block', label: 'Durdur — yeni çalıştırma reddedilir' },
          { value: 'warn', label: 'Uyar — çalıştırma sürer' }],
        hint: 'Varsayılan durdurmaktır: para harcayan işte güvenli taraf odur.' },
      { key: 'budgetEstimateWhenOffline', label: 'Sayaç okunamazsa yerel defterden tahmin et',
        type: 'checkbox',
        hint: 'Kapalıyken sayaç okunamıyorsa çalıştırma durur — ölçemediğimiz parayı harcamayız.' },
    ],
    value: {
      monthlyBudget: values.monthly_budget,
      budgetBehavior: values.budget_behavior || 'block',
      budgetEstimateWhenOffline: Boolean(values.budget_estimate_when_offline),
    },
  });
  settingsForms.push(budgetForm);

  const ruleForm = formGrid({
    fields: [
      { key: 'includePassive', label: 'Pasif ürünleri de denetle', type: 'checkbox',
        hint: 'Pasif ürün vitrinde değildir; SEO eksiği bulgu sayılmaz. Açarsanız sayılır.' },
      // Eşiklerin hepsi ZORUNLU: boş bırakılan sayı alanı `null` verir,
      // `Number(null)` sıfırdır ve sunucunun alt sınırına (`metaTitleMax >= 10`)
      // takılıp anlamsız bir 422 döndürürdü. Boş bırakmak "varsayılana dön"
      // demek değildir; kullanıcı ne istediğini yazmalı.
      { key: 'minDescriptionChars', label: 'En az açıklama uzunluğu', type: 'number',
        min: 0, max: 5000, required: true, hint: 'HTML etiketleri sayılmadan ölçülür.' },
      { key: 'metaTitleMax', label: 'Meta başlık üst sınırı', type: 'number', min: 10, max: 200,
        required: true },
      { key: 'metaDescriptionMin', label: 'Meta açıklama alt sınırı', type: 'number',
        min: 0, max: 500, required: true },
      { key: 'metaDescriptionMax', label: 'Meta açıklama üst sınırı', type: 'number',
        min: 10, max: 500, required: true },
      { key: 'priceOutlierFactor', label: 'Fiyat anormallik katsayısı', type: 'number',
        min: 2, max: 100, required: true,
        hint: 'Kategori MEDYANININ bu katı üstü/altı anormal sayılır. Ortalama değil medyan: '
          + 'tek pahalı ürün denetimi köreltmesin.' },
    ],
    value: {
      includePassive: Boolean(values.include_passive),
      minDescriptionChars: values.min_description_chars,
      metaTitleMax: values.meta_title_max,
      metaDescriptionMin: values.meta_description_min,
      metaDescriptionMax: values.meta_description_max,
      priceOutlierFactor: values.price_outlier_factor,
    },
  });
  settingsForms.push(ruleForm);

  // Araç anahtarları formGrid'e sığmıyor (dinamik liste); kendi kutusu var.
  const toolBox = h('div', 'ai-toggles');
  const disabled = new Set(values.disabled_tools || []);
  const checks = new Map();
  for (const tool of payload.tools || []) {
    const row = h('label', 'ai-toggle');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = !disabled.has(tool.key);
    checks.set(tool.key, check);
    row.append(check, h('span', undefined, tool.label),
      h('span', 'ai-sub', tool.readOnly ? 'mağazaya yazmaz'
        : `${tool.writes.length} alan önerir`));
    toolBox.append(row);
  }

  const actions = h('div', 'ai-actions');
  actions.append(button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (!budgetForm.valid() || !ruleForm.valid()) {
        budgetForm.showErrors();
        ruleForm.showErrors();
        toast('Alanları düzeltin.', 'bad');
        return;
      }
      const budgetDraft = budgetForm.draft();
      const ruleDraft = ruleForm.draft();
      const body = {
        // `?? values.monthly_budget` KALDIRILDI: çözülemeyen tutarda eski
        // değeri geri göndermek, kullanıcıya değişmeyen bir sınırı
        // "kaydedildi" diye göstermekti. Artık alan zorunlu, buraya null gelmez.
        monthlyBudget: Number(budgetDraft.monthlyBudget) || 0,
        budgetBehavior: budgetDraft.budgetBehavior,
        budgetEstimateWhenOffline: Boolean(budgetDraft.budgetEstimateWhenOffline),
        includePassive: Boolean(ruleDraft.includePassive),
        minDescriptionChars: Number(ruleDraft.minDescriptionChars),
        metaTitleMax: Number(ruleDraft.metaTitleMax),
        metaDescriptionMin: Number(ruleDraft.metaDescriptionMin),
        metaDescriptionMax: Number(ruleDraft.metaDescriptionMax),
        priceOutlierFactor: Number(ruleDraft.priceOutlierFactor),
        disabledTools: [...checks.entries()].filter(([, box]) => !box.checked)
          .map(([key]) => key),
      };
      const reason = await askReason({
        title: 'AI ayarlarını kaydet',
        description: 'Bütçe para politikasıdır; denetim eşikleri hangi ürünün "bulgulu" '
          + 'sayılacağını belirler. Eşik değişince tarama kendiliğinden yenilenir.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const result = await call(`${BASE}/settings`, {
          method: 'POST', body: { ...body, reason },
        });
        toast(`Kaydedildi: ${result.changed.length} ayar.`, 'good');
        if (result.skipped.length) {
          toast(`Tanınmayan ayar yazılmadı: ${result.skipped.join(', ')}`, 'warn');
        }
        state.budget = result.budget || state.budget;
        state.loaded.catalog = false;      // eşikler değişti, bulgular bayat
        renderBudget();
        await loadSettings();
      });
    },
  }));

  host.append(
    card('Aylık bütçe', budgetForm.node, 'Para harcayan tek ekran burasıdır'),
    card('Denetim eşikleri', ruleForm.node, 'Katalog sağlığı kurallarını ayarlar'),
    card('Açık araçlar', toolBox, 'Kapatılan araç çalıştırılamaz'),
    actions,
    mutedBox(),
    hintBox('Ayrı bir "Mağaza Ayarları" ekranı yoktur: her ayar onu kullanan ekranda '
      + 'durur. AI bütçesi ve denetim eşikleri burada, stok store_products\'ta, vergi '
      + 'store_tax\'ta.'),
  );
}

/**
 * Susturulmuş bulgular AYARLARDAN gelir, taramadan DEĞİL.
 *
 * Önceki sürüm listeyi `state.catalog.muted` içinden okuyordu ve iki şey
 * birden bozuktu: Katalog Sağlığı sekmesi hiç açılmadıysa liste boş
 * görünüyor, "Geri al" sonrası ise yalnız ayarlar tazelendiği için satır
 * ekranda KALIYORDU — düğme çalışmıyor sanılıyordu. Liste yerel tablodan
 * gelir; okumak tarama gerektirmez.
 */
function mutedBox() {
  const muted = state.settings?.muted || [];
  const box = h('div', 'ai-muted');
  if (!muted.length) {
    box.append(h('div', 'ai-sub', 'Susturulmuş bulgu yok.'));
    return card('Susturulmuş bulgular', box, 'silme yok, geri alınabilir');
  }
  for (const item of muted) {
    const row = h('div', 'ai-muted-row');
    row.append(h('code', 'ai-key', item.key),
      h('span', 'ai-sub', `${item.actor || '—'} · ${trTime(item.createdAt)}`),
      h('span', undefined, item.reason || '—'),
      button('Geri al', {
        variant: 'ghost',
        onClick: async () => {
          const reason = await askReason({
            title: 'Susturmayı kaldır',
            description: `${item.key} bulgusu listede yeniden görünecek.`,
            confirmLabel: 'Geri al',
          });
          if (!reason) return;
          await withBusy('Geri alınıyor…', async () => {
            await call(`${BASE}/catalog/unmute`, {
              method: 'POST', body: { findingKey: item.key, reason },
            });
            toast('Susturma kaldırıldı.', 'good');
            state.loaded.catalog = false;
            await loadSettings();
          });
        },
      }));
    box.append(row);
  }
  return card('Susturulmuş bulgular', box, 'silme yok, geri alınabilir');
}

// ================================================================== dışa aktarma

async function exportFindings() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Bulguları dışa aktar',
    description: 'Tüm katalog sağlığı bulguları rapor klasörüne CSV olarak yazılır.',
    confirmLabel: 'Yaz',
  });
  if (!ok) return;
  await withBusy('CSV yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ==================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  open = ctx.open;

  // Kapalıysa hiçbir istek atılmaz: kapalı ekranın veri çekmesi hem boşuna
  // yük, hem de denetim kaydını gerçekte kimsenin bakmadığı çağrılarla şişirir.
  if (!YAYINDA) {
    const view = h('div', 'kit-panel ai');
    view.append(emptyState({
      title: 'Bu ekran geliştiriliyor',
      // "Uç yok" DEMİYORUZ — yanlış olurdu (taslak uçları yayında) ve
      // kullanıcıyı mağazada olmayan bir arızayı aramaya gönderirdi.
      // Söylenen şey gerçek engel: onay akışı iki tarafta aynı değil.
      text: 'Mağazadaki AI taslak uçları yayında, ama onay akışı iki tarafta '
          + 'henüz aynı değil; yarım bir onay ekranı açmak yerine bekletiliyor. '
          + 'Hazır olduğunda burada katalog sağlığı denetimi ve onaya bağlı metin '
          + 'önerileri olacak. Diğer mağaza ekranları bundan etkilenmez.',
    }));
    root.append(view);
    return () => { view.remove(); };   // kabuk sökerken çağırır (K7)
  }

  const view = h('div', 'kit-panel ai');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  nodes.tabs = tabBar([
    { key: 'tools', label: 'Araçlar' },
    { key: 'catalog', label: 'Katalog Sağlığı' },
    { key: 'runs', label: 'Çalışma geçmişi' },
    { key: 'usage', label: 'Kullanım & maliyet' },
    { key: 'settings', label: 'Ayarlar' },
  ], 'tools', (key) => showTab(key));

  nodes.budget = h('div', 'ai-budget-host');
  nodes.status = statusLine();
  nodes.body = h('div', 'ai-body');

  // ---- Araçlar
  nodes.toolsHost = h('div', 'ai-pane');

  // ---- Katalog sağlığı
  nodes.catalogKpi = h('div', 'ai-kpi');
  nodes.catalogNotes = h('div', 'ai-notes');
  nodes.catalogBar = h('div', 'ai-selbar');
  nodes.catalogTable = h('div', 'ai-table');
  nodes.catalogFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ürün adı, SKU, bulgu ya da neden ara',
        width: '280px' },
      { kind: 'select', key: 'kind', label: 'Bulgu',
        options: [{ value: '', label: 'Tümü — bulgu türü' }] },
      { kind: 'select', key: 'severity', label: 'Önem', options: [
        { value: '', label: 'Tümü — önem' }, { value: 'high', label: 'Ağır' },
        { value: 'medium', label: 'Orta' }, { value: 'low', label: 'Hafif' },
      ] },
      { kind: 'select', key: 'targetType', label: 'Kayıt', options: [
        { value: '', label: 'Tümü — kayıt türü' }, { value: 'product', label: 'Ürün' },
        { value: 'category', label: 'Kategori' },
      ] },
    ],
    onChange: () => { state.catalogPage = 1; renderCatalog(); },
    actions: [
      button('Yeniden tara', { title: 'Tam katalog taraması ~29 istek eder',
        onClick: () => loadCatalog(true) }),
      button('⤓ CSV', { title: 'Tüm bulguları rapor klasörüne yaz', onClick: exportFindings }),
      button('Sağlık raporu', { onClick: () => report.run('health', {}) }),
    ],
  });
  nodes.catalogPager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => {
      state.catalogPage = page;
      state.catalogSize = size;
      renderCatalog();
    },
  });
  nodes.catalogPane = h('div', 'ai-pane');
  nodes.catalogPane.append(nodes.catalogFilters.node, nodes.catalogKpi, nodes.catalogNotes,
    nodes.catalogBar, nodes.catalogTable, nodes.catalogPager.node);

  // ---- Çalışma geçmişi
  nodes.runsTable = h('div', 'ai-table');
  nodes.runsPager = pager({
    total: 0, page: 1, size: 50,
    onChange: ({ page, size }) => { state.runsSize = size; loadRuns(page); },
  });
  nodes.runsPane = h('div', 'ai-pane');
  nodes.runsPane.append(nodes.runsTable, nodes.runsPager.node);

  // ---- Kullanım & maliyet
  nodes.usageHost = h('div', 'ai-pane');

  // ---- Ayarlar
  nodes.settingsHost = h('div', 'ai-pane');

  const panes = {
    tools: nodes.toolsHost,
    catalog: nodes.catalogPane,
    runs: nodes.runsPane,
    usage: nodes.usageHost,
    settings: nodes.settingsHost,
  };
  const loaders = {
    tools: ensureTools,
    catalog: () => loadCatalog(false),
    runs: () => loadRuns(1),
    usage: loadUsage,
    settings: loadSettings,
  };
  const waiting = {
    tools: 'Araçlar okunuyor…',
    catalog: 'Katalog sağlığı okunuyor…',
    runs: 'Çalışma geçmişi okunuyor…',
    usage: 'Kullanım sayacı okunuyor…',
    settings: 'Ayarlar okunuyor…',
  };

  function showTab(key) {
    state.tab = key;
    // Tek gövde kabı; sekme değişince İÇERİĞİ değişir. Katalog taraması
    // TEMBELdir: ekran açılır açılmaz 29 istek atmak, yalnız araç listesine
    // bakmak isteyen kullanıcıyı da bekletirdi.
    nodes.body.replaceChildren(panes[key]);
    if (state.loaded[key]) {
      // Sekmeye geri dönünce durum satırı ÖNCEKİ sekmenin cümlesinde kalmasın.
      nodes.status.set(key === 'catalog' ? catalogStatus() : '');
      return;
    }
    nodes.status.set(waiting[key]);
    loaders[key]();
  }

  view.append(nodes.tabs.node, nodes.budget, nodes.status.node, nodes.body);
  root.replaceChildren(view);
  showTab('tools');

  // Başka panelden gelen hedefler (ör. Ürünler ekranından "AI ile açıkla").
  // `ensureTools()` uçuştaki isteği paylaşır; liste iki kez çekilmez.
  if (Array.isArray(ctx.payload?.productIds) && ctx.payload.productIds.length) {
    queueMicrotask(async () => {
      await ensureTools();
      const tool = state.tools.find((item) => item.key
        === (ctx.payload.tool || 'product_description'));
      if (tool) openRunDrawer(tool, ctx.payload.productIds.slice(0, state.maxTargets));
    });
  }

  return () => {
    nodes.catalogFilters?.destroy();     // arama alanı bekleyen debounce tutar
    settingsForms.forEach((form) => form.destroy());
    settingsForms = [];
    // KOPYA ÜZERİNDE gezilir: her temizleyici kendini `closers` içinden
    // çıkarıyor; canlı dizide gezmek splice yüzünden bir sonrakini atlar ve o
    // çekmecenin dinleyicisi asılı kalırdı.
    [...closers].forEach((fn) => { try { fn(); } catch { /* kapanışta hata yutulur */ } });
    closers.length = 0;
    toolsPromise = null;
    root.replaceChildren();
    state = { ...EMPTY, loaded: {} };
    busy = false;
  };
}
