// Bildirimler paneli — şablon yaz, kural kur, kime gittiğini gör.
//
// NE YAPAR: gönderim geçmişi (sunucu tarafı sayfalama); değişken paletli
// şablon düzenleyici + örnek veriyle önizleme + SMS karakter/parça/kredi
// sayacı + [Kendime test gönder]; olay → koşul → şablon/kanal/gecikme
// kuralları; kanal ayarları (SMTP ve SMS sırları MASKELİ, sessiz saatler,
// günlük limit, mobil uygulama); bülten abonelikleri; toplu bildirim.
//
// MÜŞTERİ SMS'İ SEKMESİ: üç aşama (sipariş alındı · kargoya verildi · teslim
// edildi), aşama başına Açık/Kapalı, örnek veriyle önizleme ve TEK SEGMENT
// zorlayan segment sayacı, gönderilmeyenleri de nedeniyle gösteren gönderim
// izi. Bu sekmeden ELLE SMS GÖNDERİLMEZ — tetikleyiciler Siparişler ekranında.
//
// NE YAPMAZ:
//  · Kendi SMTP'sini KURMAZ ve toplu bildirimi kendi göndermez. Onlar mağazadan
//    geçer (K4); mağazanın gönderim kaydı tektir, ekran ikinci bir gerçeklik
//    üretmez. TEK İSTİSNA aşama SMS'idir: o Kontrol Merkezi'nin `notify`
//    katmanından çıkar, çünkü üç katmanlı fren, beyaz liste, tek segment
//    zorlaması ve tekrar engeli BURADA var, mağaza tarafında yok.
//  · SIR YAZMAZ. Parola ve API anahtarı kasada durur (K8); ekran yalnız
//    maskeli değeri gösterir ve nereden değiştirileceğini söyler.
//  · Toplu bildirimi TEK TIKLA GÖNDERMEZ. Önce kuru prova: kaç kişiye
//    gideceğini ve tahmini maliyeti mağazadan sorar, jeton üretir; gerçek
//    gönderim o jetonla ve gerekçeyle gelir.
//  · Şablon SİLMEZ, pasifleştirir — geçmiş gönderimler hangi şablonla
//    gittiğini göstermeye devam etmeli (ADR 0012).
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · Bilinmeyen değişken BOŞA ÇEVRİLMEZ, yerinde kalır ve önizlemede kırmızı
//    listelenir: "Sayın , siparişiniz kargolandı" 1.800 kişiye gitmesin.
//  · Türkçe harf SMS'i pahalılaştırır; sayaç hangi karakterin ne kadara mal
//    olduğunu söyler ve sadeleştirmeyi ÖNERİR — otomatik uygulamaz.
//  · Sessiz saatler açıkken toplu gönderim durur; kuru prova yine çalışır.
//  · Şablon kimliği kaynağını taşır (`store:12` · `local:3`): mağazadaki
//    e-posta şablonu ile yereldeki SMS şablonu aynı listede yan yana durur.
//  · Maliyet birim fiyat girilmemişse "—" gösterilir; sıfır yazmak "bedava"
//    demek olurdu.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_notifications/ →
// shell/ui-kit/. Bu dosyanın KAYNAĞI modules/store_notifications/ui/panel/
// altındadır; orada '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  blockedButton, button, clip, confirmSimple, confirmWithReason, csvBlob, debounce, h,
  loadStyles, money, num, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  splitView, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_notifications';

const STATUS_TONES = {
  queued: 'info', sent: '', delivered: 'good', opened: 'good',
  failed: 'bad', rejected: 'bad',
};

// Alıcı kitleleri PANELDE TUTULMAZ, `/catalog` ucundan gelir. Burada bir kopya
// durduğu sürece backend'in kabul ettiği kümeyle ayrışabilirdi: ekran kitleyi
// seçtirir, kuru prova "bilinmeyen alıcı kitlesi" der. Katalog hiç gelmezse
// (K7) tek güvenli seçenek kalır — abone listesi, en dar kitle.
const AUDIENCE_FALLBACK = [{ value: 'subscribers', label: 'Bülten aboneleri' }];

const EMPTY = {
  catalog: { events: [], channels: [], statuses: [], variables: [], audiences: [], sample: {} },
  history: { items: [], total: 0, page: 1, size: 100, pages: 0, connected: false, error: '',
    summary: {} },
  templates: { items: [], connected: false, error: '' },
  rules: { items: [], connected: false, error: '' },
  channels: null,
  audit: { items: [], error: '' },
  subscribers: { items: [], total: 0, page: 1, size: 100, pages: 0, connected: false, error: '' },
  stages: { items: [], sms: {}, error: '' },
  stageLog: { items: [], summary: {}, error: '' },
  editing: null,
  tab: 'history',
  loaded: {},
};

let api = null;
let toast = null;
let report = null;
let busy = false;
// `structuredClone` yerine JSON kopyası: kit'in geri kalanı da bunu kullanıyor
// ve WebKitGTK sürümleri arasında davranışı değişmiyor.
const fresh = () => JSON.parse(JSON.stringify(EMPTY));
let state = fresh();

// Bırakılacak gerçek kaynaklar: iki `filterBar`, üç `formGrid` listesi ve açık
// düzenleyicinin bekleyen önizleme debounce'u. Hepsinin sahibi `nodes`; ayrı bir
// "kapanışlar" listesi tutulmuyor — üretecisi olmayan boş bir liste, cleanup'ın
// bir şey bıraktığı yanılgısını verirdi.
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

/** Yıkıcı ve para harcayan her işlem buradan geçer: gerekçe backend'e gider. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

function label(list, value, fallback = '—') {
  return list.find((item) => item.key === value)?.label || value || fallback;
}

function drop(list) {
  list.forEach((item) => { try { item.destroy(); } catch { /* kapanışta yutulur */ } });
  list.length = 0;
}

// ==================================================================== veri

async function loadCatalog() {
  try {
    state.catalog = await call(`${BASE}/catalog`);
  } catch {
    // Sabitler gelmezse süzgeç açılırları boş kalır ama ekran çalışır (K7).
  }
}

// ------------------------------------------------------- gönderim geçmişi

function historyQuery(extra = {}) {
  const values = nodes.historyFilters ? nodes.historyFilters.values() : {};
  const cost = values.cost || {};
  const range = values.range || {};
  const query = {
    q: values.q || '',
    channel: values.channel || '',
    status: values.status || '',
    event: values.event || '',
    dateFrom: range.start || '',
    dateTo: range.end || '',
    costMin: cost.min ?? null,
    costMax: cost.max ?? null,
    failedOnly: values.failedOnly ? '1' : '',
    ...extra,
  };
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === null || value === undefined || value === '' || value === 0) continue;
    params.set(key, String(value));
  }
  return params.toString();
}

async function refreshHistory({ page = state.history.page, size = state.history.size } = {}) {
  nodes.historyTable?.replaceChildren(skeletonRows(8, 8));
  nodes.status.set('Gönderim geçmişi alınıyor…');
  let payload;
  try {
    payload = await api(`${BASE}/history?${historyQuery({ page, size })}`);
  } catch (error) {
    state.history = { ...state.history, connected: false, error: error.message, items: [] };
    renderHistory();
    return;
  }
  state.history = { ...state.history, ...payload };
  state.loaded.history = true;
  renderHistory();
  nodes.historyPager.update({ total: state.history.total, page: state.history.page,
    size: state.history.size });
}

// -------------------------------------------------------------- şablonlar

async function refreshTemplates() {
  nodes.templateList?.replaceChildren(skeletonRows(6, 3));
  try {
    state.templates = await api(`${BASE}/templates`);
  } catch (error) {
    state.templates = { items: [], connected: false, error: error.message };
  }
  state.loaded.templates = true;
  renderTemplateList();
}

async function refreshRules() {
  nodes.ruleWrap?.replaceChildren(skeletonRows(6, 6));
  try {
    state.rules = await api(`${BASE}/rules`);
  } catch (error) {
    state.rules = { items: [], connected: false, error: error.message };
  }
  state.loaded.rules = true;
  renderRules();
}

async function refreshChannels() {
  nodes.channelWrap?.replaceChildren(skeletonRows(5, 2));
  try {
    state.channels = await api(`${BASE}/channels`);
  } catch (error) {
    state.channels = { ok: false, error: error.message };
  }
  // Denetim izi YERELDİR ve ayrı istekle alınır: mağaza düşse de okunabilmeli
  // ve okunamaması kanal ayarlarını düşürmemeli (K7).
  try {
    state.audit = await api(`${BASE}/audit?limit=50`);
  } catch (error) {
    state.audit = { items: [], error: error.message };
  }
  state.loaded.channels = true;
  renderChannels();
}

async function refreshSubscribers({ page = 1, size = state.subscribers.size } = {}) {
  nodes.subsTable?.replaceChildren(skeletonRows(8, 5));
  const values = nodes.subsFilters ? nodes.subsFilters.values() : {};
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (values.q) params.set('q', values.q);
  if (values.subscribed) params.set('subscribed', values.subscribed);
  try {
    state.subscribers = await api(`${BASE}/subscribers?${params.toString()}`);
  } catch (error) {
    state.subscribers = { ...state.subscribers, connected: false, error: error.message,
      items: [] };
  }
  state.loaded.subscribers = true;
  renderSubscribers();
  nodes.subsPager.update({ total: state.subscribers.total, page: state.subscribers.page,
    size: state.subscribers.size });
}

// =============================================== SEKME 1 — gönderim geçmişi

function statusBadge(row) {
  // Renk tek başına anlam taşımaz: rozetin içinde her zaman yazı durur.
  const box = h('span', 'nt-status');
  box.append(badge(row.statusLabel, STATUS_TONES[row.status] ?? ''));
  if (row.attempts > 1) box.append(h('span', 'nt-sub', `${num(row.attempts)}. deneme`));
  if (row.error) box.title = row.error;
  return box;
}

const HISTORY_COLUMNS = [
  { key: 'createdAt', label: 'Zaman', width: '148px',
    cell: (row) => (row.createdAt ? stampIso(row.createdAt) : '—') },
  { key: 'channelLabel', label: 'Kanal', width: '90px' },
  { key: 'template', label: 'Şablon', width: 'minmax(0, 1.4fr)' },
  { key: 'recipient', label: 'Alıcı', width: 'minmax(0, 1.4fr)', className: 'mono' },
  { key: 'summary', label: 'Konu / özet', width: 'minmax(0, 2fr)',
    cell: (row) => clip(h('span'), row.summary, 60) },
  { key: 'status', label: 'Durum', width: '150px', cell: statusBadge },
  { key: 'parts', label: 'Parça', width: '70px', align: 'num',
    cell: (row) => (row.parts ? num(row.parts) : '—') },
  { key: 'cost', label: 'Maliyet', width: '110px', align: 'num',
    // Bilinmeyen maliyet SIFIR gösterilmez: sıfır "bedava" demektir.
    cell: (row) => (row.cost === null || row.cost === undefined ? '—' : money(row.cost)) },
  { key: 'id', label: '', width: '124px',
    cell: (row) => (row.failed
      ? button('Yeniden gönder', { title: 'Yeni bir gönderimdir: para harcar.',
        onClick: () => resend(row) })
      : h('span', 'nt-sub', '—')) },
];

function renderHistory() {
  const wrap = nodes.historyTable;
  if (!wrap) return;
  wrap.replaceChildren();

  const summary = state.history.summary || {};
  nodes.historyKpi.replaceChildren(kpiRow([
    { label: 'Sayfadaki gönderim', value: num(summary.total ?? 0) },
    { label: 'İletildi', value: num(summary.delivered ?? 0), tone: 'good' },
    { label: 'Başarısız', value: num(summary.failed ?? 0), tone: 'bad' },
    { label: 'Kuyrukta', value: num(summary.queued ?? 0), tone: 'warn' },
    { label: 'SMS parçası', value: num(summary.parts ?? 0) },
    { label: 'Maliyet',
      value: summary.cost === null || summary.cost === undefined ? '—' : money(summary.cost),
      title: summary.costUnknown
        ? `${summary.costUnknown} satırın maliyeti mağazadan gelmedi; toplama girmedi.`
        : 'Yalnız bu sayfadaki satırlar.' },
  ]));

  if (!state.history.connected) {
    wrap.append(alertBox(
      `Gönderim geçmişi okunamadı — ${state.history.error}. Mağaza tarafındaki BBD paketi `
      + 'yayınlanınca bu liste kendiliğinden dolacak; ekranın geri kalanı çalışmaya devam eder.',
      'warn'));
  }

  nodes.history = dataTable({
    columns: HISTORY_COLUMNS,
    rows: state.history.items,
    dense: true,
    empty: state.history.connected
      ? emptyState({
        title: 'Bu süzgeçte gönderim yok',
        text: 'Tarih aralığını genişletin ya da süzgeçleri temizleyin.',
        actions: [button('Filtreyi temizle', { onClick: () => nodes.historyFilters.reset() })],
      })
      : emptyState({
        title: 'Geçmiş alınamadı',
        text: state.history.error || 'Mağazaya ulaşılamadı.',
        actions: [button('Tekrar dene', { variant: 'primary',
          onClick: () => refreshHistory() })],
      }),
  });
  wrap.append(nodes.history.node);
  nodes.status.set(state.history.connected
    ? `Bağlı · ${num(state.history.total)} gönderim · sayfa ${state.history.page}/`
      + `${Math.max(1, state.history.pages)}`
    : `Mağazaya ulaşılamadı — ${state.history.error}`, !state.history.connected);
}

async function resend(row) {
  const reason = await askReason({
    title: 'Yeniden gönder',
    description: `${row.recipient} adresine ${row.channelLabel} yeniden gidecek. Bu YENİ bir `
      + 'gönderimdir: para harcar ve müşteriye ikinci kez ulaşır.',
    confirmLabel: 'Gönder',
  });
  if (!reason) return;
  await withBusy('Yeniden gönderiliyor…', async () => {
    await call(`${BASE}/history/${row.id}/resend`, {
      method: 'POST', body: { reason, dryRun: false },
    });
    toast('Yeniden gönderildi.', 'good');
    refreshHistory();
  });
}

function exportVisibleHistory() {
  const headers = ['Zaman', 'Kanal', 'Şablon', 'Alıcı', 'Özet', 'Durum', 'Deneme', 'Parça',
    'Maliyet'];
  const rows = state.history.items.map((row) => [
    row.createdAt, row.channelLabel, row.template, row.recipient, row.summary,
    row.statusLabel, row.attempts, row.parts,
    row.cost === null || row.cost === undefined ? '' : money(row.cost),
  ]);
  csvBlob(headers, rows, `bildirim-gonderim-sayfa-${state.history.page}`);
  toast(`${num(rows.length)} satır indirildi.`, 'good');
}

async function exportAllHistory() {
  const ok = await confirmSimple(nodes.root, {
    title: 'Tüm kayıtları dışa aktar',
    description: 'Gönderim geçmişi mağazadan sayfa sayfa çekilir ve rapor klasörüne CSV '
      + 'olarak yazılır. Geçmiş büyükse birkaç dakika sürebilir.',
    confirmLabel: 'Başlat',
  });
  if (!ok) return;
  const values = nodes.historyFilters.values();
  const range = values.range || {};
  await withBusy('Geçmiş taranıyor…', async () => {
    const result = await call(`${BASE}/export`, {
      method: 'POST',
      body: { dateFrom: range.start || '', dateTo: range.end || '',
        channel: values.channel || '', status: values.status || '' },
    });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
    if (result.truncated) toast('Geçmiş tavana dayandı; dosya eksik olabilir.', 'warn');
  });
}

function reportParams() {
  const values = nodes.historyFilters ? nodes.historyFilters.values() : {};
  const range = values.range || {};
  return { dateFrom: range.start || '', dateTo: range.end || '', channel: values.channel || '' };
}

// ==================================================== SEKME 2 — şablonlar

function renderTemplateList() {
  const wrap = nodes.templateList;
  if (!wrap) return;
  wrap.replaceChildren();

  if (state.templates.error) {
    wrap.append(alertBox(
      `E-posta şablonları mağazadan okunamadı — ${state.templates.error}. SMS ve Push `
      + 'şablonları yereldedir ve düzenlenmeye devam edebilir.', 'warn'));
  }

  nodes.templateTable = dataTable({
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2fr)',
        cell: (row) => {
          const box = h('span', 'nt-name');
          box.append(clip(h('b'), row.name, 34));
          box.append(h('span', 'nt-sub',
            row.source === 'store' ? 'mağazada' : 'Kontrol Merkezi’nde'));
          return box;
        } },
      { key: 'channelLabel', label: 'Kanal', width: '86px' },
      { key: 'active', label: 'Durum', width: '84px',
        cell: (row) => badge(row.active ? 'Aktif' : 'Pasif', row.active ? 'good' : 'dim') },
    ],
    rows: state.templates.items,
    dense: true,
    rowKey: (row) => row.id,
    onRow: (row) => openTemplate(row),
    empty: emptyState({
      title: 'Şablon yok',
      text: 'Yeni bir SMS ya da Push şablonu açın; e-posta şablonları mağazadan gelir.',
      actions: [button('Yeni şablon', { variant: 'primary', onClick: () => openTemplate(null) })],
    }),
  });
  wrap.append(nodes.templateTable.node);
}

/** Şablon düzenleyici — değişken paleti, önizleme, SMS sayacı, test gönderimi. */
function openTemplate(row) {
  drop(nodes.templateForms);
  // ÖNCEKİ DÜZENLEYİCİNİN BEKLEYEN ÖNİZLEMESİ İPTAL EDİLİR (kit kuralı 4).
  // Eskiden her `openTemplate` yeni bir debounce'u bir listeye EKLİYOR ama
  // eskisini iptal etmiyordu: başka şablona geçildiğinde bekleyen çağrı yine
  // ateşleniyor, artık DOM'da olmayan düğümlere yazıyor ve gereksiz bir POST
  // atıyordu; hata dönerse ekranda alakasız bir toast beliriyordu.
  nodes.templatePreview?.cancel();
  const host = nodes.templateEditor;
  host.replaceChildren();
  state.editing = row;

  const isNew = !row;
  const channels = state.catalog.channels.length ? state.catalog.channels
    : [{ key: 'email', label: 'E-posta' }, { key: 'sms', label: 'SMS' }];

  const head = formGrid({
    fields: [
      { key: 'name', label: 'Şablon adı', type: 'text', required: true, maxLength: 120 },
      { key: 'channel', label: 'Kanal', type: 'select', readOnly: !isNew,
        options: channels.map((item) => ({ value: item.key, label: item.label })),
        hint: isNew ? 'E-posta şablonu mağazaya, SMS/Push şablonu Kontrol Merkezi’ne yazılır.'
          : 'Kanal sonradan değiştirilmez: iki kaynak birbirine çevrilemez.' },
      { key: 'event', label: 'Olay (isteğe bağlı)', type: 'select',
        options: [{ value: '', label: '— olaya bağlı değil' },
          ...state.catalog.events.map((item) => ({ value: item.key, label: item.label }))],
        hint: 'Seçilirse değişken paleti o olayda dolan alanları öne alır.' },
      // KONU YALNIZ PUSH VE WHATSAPP'TA VARDIR. Mağazanın e-posta şablonu
      // kaynağında `subject` alanı YOKTUR (canlı şemada doğrulandı: name ·
      // status · content); yazılan konu Laravel tarafından sessizce yok
      // sayılırdı. Backend de reddeder — burada erkenden söylenir.
      { key: 'subject', label: 'Konu', type: 'text', maxLength: 255,
        hint: 'Konu yalnız Push ve WhatsApp şablonlarında vardır. Mağazanın e-posta '
          + 'şablonunda konu alanı yoktur (konu gövdenin kendi başlığından gelir), '
          + 'SMS’te de yoktur.',
        validate: (value, draft) => {
          if (!value || (draft.channel !== 'email' && draft.channel !== 'sms')) return null;
          return draft.channel === 'email'
            ? 'E-posta şablonunda konu alanı yoktur; konuyu gövdeye taşıyın.'
            : 'SMS şablonunda konu alanı yoktur; boş bırakın.';
        } },
      { key: 'active', label: 'Aktif', type: 'checkbox' },
    ],
    value: row
      ? { name: row.name, channel: row.channel, event: row.event, subject: row.subject,
        active: row.active }
      : { name: '', channel: 'sms', event: '', subject: '', active: true },
    onChange: () => schedulePreview(),
  });
  nodes.templateForms.push(head);

  const area = h('textarea', 'kit-textarea nt-body');
  area.rows = 10;
  area.value = row ? row.body : '';
  area.placeholder = 'Mesaj gövdesi. Değişkenleri paletten tıklayarak ekleyin.';
  area.addEventListener('input', () => schedulePreview());

  const palette = h('div', 'nt-palette');
  const paintPalette = () => {
    palette.replaceChildren();
    const event = head.draft().event || '';
    const items = (state.catalog.variables || []).map((item) => ({
      ...item,
      relevant: !event
        || (state.catalog.events.find((entry) => entry.key === event)?.variables || [])
          .includes(item.key),
    }));
    items.sort((a, b) => Number(b.relevant) - Number(a.relevant));
    for (const item of items) {
      const chip = h('button', `nt-token${item.relevant ? '' : ' off'}`, item.token);
      chip.type = 'button';
      chip.title = item.relevant
        ? `${item.label} — örnek: ${item.sample}`
        : `${item.label} — bu olayda DOLMAZ; yine de ekleyebilirsiniz.`;
      chip.addEventListener('click', () => insertToken(area, item.token));
      palette.append(chip);
    }
  };

  const preview = h('div', 'nt-preview');
  const counter = h('div', 'nt-counter');
  const problems = h('div', 'nt-problems');

  const actions = h('div', 'nt-actions');
  const testBtn = button('Kendime test gönder', {
    title: 'Tek alıcıya gerçek gönderim yapar; para harcar.',
    onClick: () => openTestDialog(head.draft(), area.value),
  });
  const broadcastBtn = button('Toplu gönderim', {
    variant: 'danger',
    title: 'Önce kuru prova: kaç kişiye gideceğini ve maliyeti gösterir.',
    onClick: () => openBroadcast(head.draft(), area.value),
  });
  actions.append(
    button('Kaydet', { variant: 'primary', onClick: () => saveTemplate(head, area) }),
    testBtn,
    broadcastBtn,
  );
  if (!isNew) {
    actions.append(button('Pasifleştir', {
      variant: 'ghost',
      title: 'Şablon silinmez; geçmiş gönderimler hangi şablonla gittiğini göstermeye devam eder.',
      onClick: () => deactivateTemplate(row),
    }));
  }

  host.append(
    card(isNew ? 'Yeni şablon' : row.name, head.node,
      isNew ? '' : `Kimlik: ${row.id}`),
    card('Gövde', (() => {
      const box = h('div');
      box.append(palette, area, hintBox(
        'Bilinmeyen değişken BOŞA ÇEVRİLMEZ, yerinde kalır: "Sayın , siparişiniz kargolandı" '
        + 'gibi bir metin 1.800 kişiye gitmesin diye önizleme onu ayrıca listeler.'));
      return box;
    })()),
    card('Önizleme (örnek veriyle)', (() => {
      const box = h('div');
      box.append(problems, preview, counter);
      return box;
    })()),
    actions,
  );

  paintPalette();

  const runPreview = debounce(async () => {
    const draft = head.draft();
    paintPalette();
    let payload;
    try {
      payload = await api(`${BASE}/templates/preview`, {
        method: 'POST',
        body: { channel: draft.channel || 'sms', subject: draft.subject || '',
          body: area.value, recipients: 1 },
      });
    } catch (error) {
      preview.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    paintPreview(payload, draft);
  }, 250);
  // Tek kayıt yeter: bir sonraki `openTemplate` ve cleanup bunu iptal eder.
  // Listeye eklemek, 50 şablon açıldığında 50 ölü kapanış tutmak olurdu.
  nodes.templatePreview = runPreview;

  function schedulePreview() { runPreview(); }

  function paintPreview(payload, draft) {
    problems.replaceChildren();
    if (!payload.ok) {
      problems.append(alertBox(payload.error, 'bad'));
      return;
    }
    if (payload.missing.length) {
      problems.append(alertBox(
        `Örnek veriyle dolmayan değişken: ${payload.missing.join(', ')} — gönderimde de boş `
        + 'kalabilir.', 'warn'));
    }
    if (payload.unknown.length) {
      problems.append(alertBox(
        `Palette olmayan değişken: ${payload.unknown.join(', ')}. Bu alanlar hiçbir zaman `
        + 'dolmaz; metinde olduğu gibi görünür.', 'bad'));
    }

    preview.replaceChildren();
    if (draft.channel === 'email' && payload.subject) {
      preview.append(h('div', 'nt-preview-subject', payload.subject));
    }
    preview.append(h('pre', 'nt-preview-body', payload.body));

    counter.replaceChildren();
    if (!payload.plan) return;
    const plan = payload.plan;
    counter.append(kpiRow([
      { label: 'Karakter', value: `${num(plan.units)} / ${num(plan.capacity)}` },
      { label: 'SMS parçası', value: num(plan.parts),
        tone: plan.parts > 1 ? 'warn' : 'good' },
      { label: 'Sonraki parçaya', value: `${num(plan.remaining)} karakter` },
      { label: 'Kodlama', value: plan.unicode ? 'UCS-2 (70)' : (plan.encoding || 'GSM-7') },
    ]));
    if (plan.offending.length) {
      counter.append(alertBox(
        `Şu ${plan.offending.length} karakter mesajı pahalılaştırıyor: `
        + `${plan.offending.join(' ')}`, 'warn'));
    }
    if (plan.simplified && plan.simplifiedParts < plan.parts) {
      const row = h('div', 'nt-suggest');
      row.append(h('span', undefined,
        `Türkçe harfler sadeleştirilirse ${plan.parts} yerine ${plan.simplifiedParts} parça:`));
      row.append(button('Sadeleştirilmişi kullan', {
        onClick: () => { area.value = plan.simplified; schedulePreview(); },
      }));
      counter.append(row);
    }
  }

  schedulePreview();
}

function insertToken(area, token) {
  const start = area.selectionStart ?? area.value.length;
  const end = area.selectionEnd ?? start;
  area.value = `${area.value.slice(0, start)}${token}${area.value.slice(end)}`;
  const caret = start + token.length;
  area.focus();
  area.setSelectionRange(caret, caret);
  area.dispatchEvent(new Event('input'));
}

async function saveTemplate(form, area) {
  if (!form.valid()) {
    form.showErrors();
    toast('Eksik alan var.', 'warn');
    return;
  }
  const draft = form.draft();
  const reason = await askReason({
    title: 'Şablonu kaydet',
    description: draft.channel === 'email'
      ? 'E-posta şablonu MAĞAZAYA yazılır; müşteriye giden postayı doğrudan etkiler.'
      : 'SMS/Push şablonu Kontrol Merkezi’nde saklanır.',
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;
  await withBusy('Şablon kaydediliyor…', async () => {
    await call(`${BASE}/templates`, {
      method: 'POST',
      body: {
        template: state.editing ? state.editing.id : '',
        name: draft.name, channel: draft.channel, subject: draft.subject || '',
        body: area.value, event: draft.event || '', active: Boolean(draft.active),
        reason, dryRun: false,
      },
    });
    toast('Şablon kaydedildi.', 'good');
    await refreshTemplates();
  });
}

async function deactivateTemplate(row) {
  const reason = await askReason({
    title: 'Şablonu pasifleştir',
    description: 'Şablon SİLİNMEZ. Geçmiş gönderimler hangi şablonla gittiğini göstermeye '
      + 'devam eder; kurallar bu şablonu artık kullanamaz.',
    confirmLabel: 'Pasifleştir',
  });
  if (!reason) return;
  await withBusy('Pasifleştiriliyor…', async () => {
    await call(`${BASE}/templates/deactivate`, {
      method: 'POST', body: { template: row.id, reason, dryRun: false },
    });
    toast('Şablon pasifleştirildi.', 'good');
    await refreshTemplates();
  });
}

/** [Kendime test gönder] — alıcı kullanıcının kendisidir, tek mesaj gider. */
function openTestDialog(draft, body) {
  const box = drawer(nodes.root, {
    title: 'Kendime test gönder',
    subtitle: `${label(state.catalog.channels, draft.channel)} · tek alıcı`,
  });
  const form = formGrid({
    fields: [{
      key: 'to',
      label: draft.channel === 'sms' ? 'Cep numaram' : 'Kendi adresim',
      type: draft.channel === 'sms' ? 'phone' : 'email',
      required: true,
      hint: 'Gerçek gönderimdir ve para harcar. Yalnız buraya gider.',
    }],
    value: { to: '' },
  });
  nodes.templateForms.push(form);

  const actions = h('div', 'nt-actions');
  actions.append(
    button('Vazgeç', { onClick: box.close }),
    button('Gönder', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); return; }
        await withBusy('Test gönderiliyor…', async () => {
          const result = await call(`${BASE}/templates/test`, {
            method: 'POST',
            body: { channel: draft.channel, to: form.draft().to,
              subject: draft.subject || '', body },
          });
          toast(`Test gönderildi: ${result.to}`, 'good');
          if (result.missing?.length) {
            toast(`Dolmayan değişken: ${result.missing.join(', ')}`, 'warn');
          }
          box.close();
        });
      },
    }),
  );
  box.body.append(form.node, actions);
}

// ======================================================= toplu gönderim

/** Toplu bildirim: ÖNCE kuru prova (alıcı + maliyet), SONRA gerekçeli onay. */
function openBroadcast(draft, body) {
  const box = drawer(nodes.root, {
    title: 'Toplu bildirim',
    subtitle: 'Canlıda 1.800+ kişiye gider · geri alınamaz',
  });
  const audiences = (state.catalog.audiences || []).length
    ? state.catalog.audiences.map((item) => ({ value: item.key, label: item.label }))
    : AUDIENCE_FALLBACK;
  const form = formGrid({
    fields: [
      { key: 'audience', label: 'Kime', type: 'select', options: audiences,
        hint: 'Kitle backend’de de doğrulanır: tanınmayan bir ad mağaza tarafından '
          + 'sessizce yok sayılır ve gönderim süzgeçsiz kalırdı.' },
      { key: 'channel', label: 'Kanal', type: 'static' },
    ],
    value: { audience: audiences[0].value,
      channel: label(state.catalog.channels, draft.channel) },
  });
  nodes.templateForms.push(form);

  const result = h('div', 'nt-broadcast');
  const actions = h('div', 'nt-actions');
  let preview = null;

  const paintActions = () => {
    actions.replaceChildren();
    actions.append(button('Vazgeç', { onClick: box.close }));
    actions.append(button('Kuru prova al', {
      onClick: async () => {
        await withBusy('Alıcı sayısı soruluyor…', async () => {
          preview = await call(`${BASE}/broadcast/preview`, {
            method: 'POST',
            body: { channel: draft.channel, audience: form.draft().audience,
              subject: draft.subject || '', body, filters: {} },
          });
          paintPreview();
          paintActions();
        });
      },
    }));
    if (preview) {
      actions.append(button(`${num(preview.recipients)} kişiye gönder`, {
        variant: 'danger',
        disabled: Boolean(preview.blocked),
        title: preview.blocked ? preview.note : 'Gerekçe istenir; gönderim geri alınamaz.',
        onClick: () => send(),
      }));
    }
  };

  const paintPreview = () => {
    result.replaceChildren();
    if (!preview) return;
    result.append(kpiRow([
      { label: 'Alıcı', value: num(preview.recipients) },
      { label: 'SMS kredisi', value: preview.plan ? num(preview.plan.credits) : '—' },
      { label: 'Tahmini maliyet',
        value: preview.plan && preview.plan.cost !== null && preview.plan.cost !== undefined
          ? money(preview.plan.cost) : '—',
        title: 'Birim fiyat girilmemişse maliyet uydurulmaz.' },
      { label: 'Günlük kalan',
        value: preview.limit?.limited ? num(preview.limit.remaining) : 'sınırsız' },
    ]));
    if (preview.missing?.length) {
      result.append(alertBox(
        `Dolmayan değişken: ${preview.missing.join(', ')}. Bu alanlar alıcıya olduğu gibi `
        + 'görünecek.', 'warn'));
    }
    if (preview.note) result.append(alertBox(preview.note, 'bad'));
    result.append(h('pre', 'nt-preview-body', preview.body));
  };

  async function send() {
    const reason = await askReason({
      title: 'Toplu bildirimi gönder',
      description: `${num(preview.recipients)} kişiye ${label(state.catalog.channels,
        draft.channel)} gidecek. GERİ ALINAMAZ ve gönderilen SMS’in parası harcanır. `
        + 'Önizlediğiniz iş neyse o gönderilir; yeniden hesaplanmaz.',
      confirmLabel: 'Gönder',
    });
    if (!reason) return;
    await withBusy('Gönderiliyor…', async () => {
      const sent = await call(`${BASE}/broadcast/send`, {
        method: 'POST', body: { token: preview.token, reason, dryRun: false },
      });
      toast(`${num(sent.recipients)} alıcıya gönderildi.`, 'good');
      box.close();
      if (state.tab === 'history') refreshHistory();
    });
  }

  box.body.append(
    alertBox('Kuru prova alınmadan gönderim açılmaz: kaç kişiye gideceğini ve tahmini '
      + 'maliyeti görmeden bu düğmeye basılmaz.', 'info'),
    form.node, result, actions);
  paintActions();
}

// ====================================================== SEKME 3 — kurallar

/**
 * Kural YAZMA neden kapalı — kapalı değilse boş dize.
 *
 * ÜÇ AYRI NEDEN, ÜÇ AYRI CÜMLE ve sıra önemli: önce "hiç yazılamaz", sonra
 * "şu an okunamadı". Nedeni okunup kapatılmış bir düğme, her tıklamada aynı
 * cümleyi gösteren bir düğmeden iyidir.
 *
 * BİR DÖNEM "yazma ucu okuma ucuyla aynı pakette geliyor; liste 404 dönüyorsa
 * yazma da dönecektir" deniyordu. Artık böyle değil: liste ucu 2026-08-16
 * itibarıyla YAYINDA (canlıda gerçek kural listesi geliyor), yazma ucu ise
 * gecikmedi — mağaza onu bilerek yazmadı. Eski varsayım bırakılsaydı liste
 * geldiği için düğme AÇIK görünür, kullanıcı formu doldurup gerekçe
 * yazdıktan SONRA reddedilirdi.
 *
 * `writeReason` backend'den gelir; alan hiç gelmezse (uç bir gün yazılabilir
 * olursa) aşağıdaki iki dal olduğu gibi çalışmaya devam eder.
 */
function ruleWriteReason() {
  if (state.rules?.writeReason) return state.rules.writeReason;
  if (state.rules?.connected) return '';
  return state.rules?.endpointPending
    ? 'Kural ucu mağazada henüz yayında değil; paket yayınlanınca bu düğme açılacak.'
    : `Kural listesi okunamadı (${state.rules?.error || 'neden bilinmiyor'}); okunamayan `
      + 'listenin üstüne kural yazmak var olan bir kuralı sessizce ezebilirdi.';
}

function newRuleButton() {
  const reason = ruleWriteReason();
  return reason
    ? blockedButton('Yeni kural', reason, { variant: 'primary' })
    : button('Yeni kural', { variant: 'primary', onClick: () => openRule(null) });
}

function renderRules() {
  const wrap = nodes.ruleWrap;
  if (!wrap) return;
  wrap.replaceChildren();

  // İKİ AYRI DURUM, İKİ AYRI CÜMLE. "Uç yayında değil" beklenecek bir şeydir;
  // "okunamadı" tekrar denenecek bir arıza. Aynı metne sığdırmak, personeli
  // olmayan bir arızayı kovalarken bırakırdı.
  if (!state.rules.connected) {
    wrap.append(alertBox(state.rules.endpointPending
      ? `Kural ucu mağazada henüz yayında değil — ${state.rules.error} Kurallar MAĞAZADA `
        + 'çalışır (olay orada olur); paket yayına girince bu liste dolacak ve düzenleme '
        + 'kendiliğinden açılacak.'
      : `Kurallar okunamadı — ${state.rules.error} Yazma da kapalıdır: okunamayan bir `
        + 'listenin üstüne kural yazmak, var olan bir kuralı sessizce ezebilirdi.',
    state.rules.endpointPending ? 'info' : 'warn'));
  } else if (state.rules.writeReason) {
    // Liste GELİYOR ama düzenlenemiyor. Bu, üstteki iki durumdan da farklıdır
    // ve söylenmezse ekran "bozuk" görünür: satırlar dolu, düğmeler kapalı ve
    // ortada bir açıklama yok. Cümle backend'den gelir — nedeni bilen taraf o.
    wrap.append(alertBox(`Kurallar salt okunur. ${state.rules.writeReason}`, 'info'));
  }

  const table = dataTable({
    columns: [
      { key: 'eventLabel', label: 'Olay', width: 'minmax(0, 1.4fr)',
        cell: (row) => {
          const box = h('span', 'nt-name');
          box.append(h('b', undefined, row.eventLabel));
          if (!row.known) box.append(badge('tanınmayan olay', 'warn'));
          return box;
        } },
      { key: 'condition', label: 'Koşul', width: 'minmax(0, 1.2fr)',
        cell: (row) => clip(h('span'), row.condition || 'koşulsuz', 40) },
      { key: 'templateName', label: 'Şablon', width: 'minmax(0, 1.2fr)',
        cell: (row) => row.templateName || row.templateId || '—' },
      { key: 'channelLabel', label: 'Kanal', width: '90px' },
      { key: 'delayMinutes', label: 'Gecikme', width: '96px', align: 'num',
        cell: (row) => (row.delayMinutes ? `${num(row.delayMinutes)} dk` : 'hemen') },
      { key: 'lastFiredAt', label: 'Son tetiklenme', width: '150px',
        cell: (row) => (row.lastFiredAt ? stampIso(row.lastFiredAt) : 'hiç') },
      { key: 'active', label: 'Durum', width: '84px',
        cell: (row) => badge(row.active ? 'Açık' : 'Kapalı', row.active ? 'good' : 'dim') },
      { key: 'id', label: '', width: '96px',
        cell: (row) => (ruleWriteReason()
          ? blockedButton(row.active ? 'Kapat' : 'Aç', ruleWriteReason())
          : button(row.active ? 'Kapat' : 'Aç', { onClick: () => toggleRule(row) })) },
    ],
    rows: state.rules.items,
    dense: true,
    onRow: (row) => openRule(row),
    empty: emptyState({
      title: 'Kural yok',
      text: 'Bir olay gerçekleştiğinde hangi şablonun hangi kanaldan gideceğini burada '
        + 'tanımlarsınız.',
      actions: [newRuleButton()],
    }),
  });
  wrap.append(table.node);
}

/**
 * Kural DETAYI — yazma kapalıyken açılan salt okunur çekmece.
 *
 * Satıra tıklamak düzenleme formu açardı; yazma kapalıyken o form, doldurulup
 * ancak kaydederken reddedilen bir tuzaktır. Satır yine açılır — kuralın ne
 * yaptığını okumak yazma iznine bağlı değildir — ama düzenlenebilir alan
 * göstermez ve neden kapalı olduğunu söyler.
 */
function openRuleDetail(row, reason) {
  const box = drawer(nodes.root, {
    title: row.eventLabel || 'Kural',
    subtitle: 'Salt okunur',
  });
  const facts = h('div');
  const line = (label, value) => {
    const node = h('div', 'nt-sub');
    node.append(h('b', undefined, `${label}: `), document.createTextNode(value || '—'));
    return node;
  };
  facts.append(
    line('Kimlik', row.id),
    line('Olay', row.event),
    line('Kanal', row.channelLabel),
    line('Durum', row.active ? 'Açık' : 'Kapalı'),
  );
  if (row.description) facts.append(h('p', 'nt-sub', row.description));

  const actions = h('div', 'nt-actions');
  actions.append(button('Kapat', { onClick: box.close }));
  box.body.append(facts, alertBox(reason, 'info'), actions);
}

function openRule(row) {
  const blocked = ruleWriteReason();
  if (blocked) {
    if (row) openRuleDetail(row, blocked);
    return;
  }
  const box = drawer(nodes.root, {
    title: row ? 'Kuralı düzenle' : 'Yeni kural',
    subtitle: 'Olay → koşul → şablon + kanal + gecikme',
  });
  const templates = state.templates.items.filter((item) => item.active);
  const form = formGrid({
    fields: [
      { key: 'event', label: 'Olay', type: 'select', required: true,
        options: state.catalog.events.map((item) => ({ value: item.key, label: item.label })) },
      { key: 'condition', label: 'Koşul (isteğe bağlı)', type: 'text', maxLength: 500,
        hint: 'Örn. tutar > 500 · kanal = default. Boşsa olay her gerçekleştiğinde çalışır.' },
      { key: 'template', label: 'Şablon', type: 'select', required: true,
        options: [{ value: '', label: '— seçin' },
          ...templates.map((item) => ({ value: item.id,
            label: `${item.name} (${item.channelLabel})` }))] },
      { key: 'channel', label: 'Kanal', type: 'select', required: true,
        options: state.catalog.channels.map((item) => ({ value: item.key, label: item.label })) },
      { key: 'delayMinutes', label: 'Gecikme (dakika)', type: 'number', min: 0, max: 10080,
        hint: '0 = hemen. En çok 7 gün (10.080 dk).' },
      { key: 'active', label: 'Açık', type: 'checkbox' },
    ],
    value: row
      ? { event: row.event, condition: row.condition, template: row.templateId,
        channel: row.channel, delayMinutes: row.delayMinutes, active: row.active }
      : { event: state.catalog.events[0]?.key || '', condition: '', template: '',
        channel: 'email', delayMinutes: 0, active: true },
  });
  nodes.ruleForms.push(form);

  const actions = h('div', 'nt-actions');
  actions.append(
    button('Vazgeç', { onClick: box.close }),
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        if (!form.valid()) { form.showErrors(); return; }
        const draft = form.draft();
        const reason = await askReason({
          title: 'Kuralı kaydet',
          description: 'Kural mağazada çalışır: bu olay her gerçekleştiğinde müşteriye '
            + 'bildirim gider.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        await withBusy('Kural kaydediliyor…', async () => {
          await call(`${BASE}/rules`, {
            method: 'POST',
            body: { ruleId: row ? row.id : null, event: draft.event, channel: draft.channel,
              template: draft.template, condition: draft.condition || '',
              delayMinutes: Number(draft.delayMinutes) || 0, active: Boolean(draft.active),
              reason, dryRun: false },
          });
          toast('Kural kaydedildi.', 'good');
          box.close();
          refreshRules();
        });
      },
    }),
  );
  box.body.append(form.node, actions);
}

async function toggleRule(row) {
  const reason = await askReason({
    title: row.active ? 'Kuralı kapat' : 'Kuralı aç',
    description: row.active
      ? 'Kapalı kural tetiklenmez. SİLİNMEZ: neyin neden durdurulduğu kayıtta kalsın.'
      : 'Açılan kural, olay gerçekleştiğinde müşteriye bildirim göndermeye başlar.',
    confirmLabel: row.active ? 'Kapat' : 'Aç',
  });
  if (!reason) return;
  await withBusy('Kural güncelleniyor…', async () => {
    await call(`${BASE}/rules/${row.id}/toggle`, {
      method: 'POST', body: { active: !row.active, reason, dryRun: false },
    });
    toast('Kural güncellendi.', 'good');
    refreshRules();
  });
}

// ====================================================== SEKME 4 — kanallar

function secretRow(title, entry) {
  const line = h('div', 'nt-secret');
  line.append(h('span', 'nt-secret-label', title));
  if (!entry || !entry.found) {
    line.append(badge('anahtar bulunamadı', 'warn'));
    return line;
  }
  const value = entry.secret ? entry.masked : String(entry.value ?? '—');
  line.append(h('code', 'nt-secret-value', value || '—'));
  if (entry.secret) line.append(badge('kasada', 'dim'));
  return line;
}

/** Bu ekrandan yapılan yazmaların YEREL izi — gerekçeleriyle (ADR 0012). */
function auditCard() {
  const box = h('div');
  if (state.audit?.error) {
    box.append(alertBox(`Denetim izi okunamadı — ${state.audit.error}`, 'warn'));
  }
  box.append(dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '150px',
        cell: (row) => (row.createdAt ? stampIso(row.createdAt) : '—') },
      { key: 'action', label: 'İşlem', width: 'minmax(0, 1fr)' },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      { key: 'result', label: 'Sonuç', width: '110px' },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)',
        cell: (row) => clip(h('span'), row.reason, 70) },
    ],
    rows: state.audit?.items || [],
    dense: true,
    empty: emptyState({
      title: 'Bu ekrandan henüz yazma yapılmadı',
      text: 'Şablon, kural, ayar ve gönderim işlemleri gerekçeleriyle burada listelenir.',
    }),
  }).node);
  return card('Denetim izi', box, 'Yerel kayıt · son 50 işlem');
}

function renderChannels() {
  const wrap = nodes.channelWrap;
  if (!wrap) return;
  drop(nodes.channelForms);
  wrap.replaceChildren();

  const payload = state.channels;
  if (!payload || payload.ok === false) {
    // Kanal ayarları okunamasa bile yerel denetim izi gösterilir: "ne yapmaya
    // çalıştık" kaydı mağazadan bağımsızdır.
    wrap.append(alertBox(payload?.error || 'Kanal ayarları okunamadı.', 'bad'), auditCard());
    return;
  }

  // ------------------------------------------------------------- SMTP
  const smtp = h('div');
  if (!payload.storeAvailable) {
    smtp.append(alertBox(`Mağaza ayarları okunamadı — ${payload.error}`, 'warn'));
  } else {
    const store = payload.store || {};
    // Anahtar adları CANLIDA DOĞRULANDI: bağlantı bilgisi
    // `emails.configure.smtp.*`, gönderen kimliği
    // `emails.configure.email_settings.sender_*` altındadır.
    smtp.append(
      secretRow('Sunucu', store.host), secretRow('Port', store.port),
      secretRow('Kullanıcı', store.username), secretRow('Parola', store.password),
      secretRow('Şifreleme', store.encryption), secretRow('Gönderen adres', store.sender_email),
      secretRow('Gönderen ad', store.sender_name),
    );
  }
  smtp.append(hintBox(
    'Sırlar bu ekrandan YAZILMAZ: parola ve anahtar kasada durur (config/local.yaml → '
    + 'secrets). Burada yalnız son dört hane görünür. Değiştirmek için kasa güncellenir.'));
  const smtpTest = h('div', 'nt-actions');
  const smtpNote = h('span', 'nt-sub', '');
  smtpTest.append(button('Bağlantıyı sına', {
    onClick: () => testConnection('store', smtpNote),
  }), smtpNote);
  smtp.append(smtpTest);

  // -------------------------------------------------------------- SMS
  const sms = h('div');
  const smsState = payload.sms || {};
  if (!smsState.available) {
    sms.append(alertBox(
      'SMS katmanı bu kurulumda yok (notify yeteneği kapalı). SMS şablonları yazılabilir '
      + 'ama gönderim mağaza tarafından yapılır.', 'info'));
  } else {
    sms.append(
      secretRow('Sağlayıcı', { found: true, value: smsState.provider }),
      secretRow('Gönderici başlığı', { found: true, value: smsState.header || '—' }),
      secretRow('Durum', { found: true,
        value: smsState.configured ? 'kimlik bilgisi girilmiş' : 'kurulmamış' }),
    );
    if (smsState.dryRun) {
      sms.append(alertBox('KURU PROVA AÇIK: bu katmandan gerçek SMS gitmez '
        + '(platform.notify.sms.dry_run).', 'warn'));
    }
    if (smsState.error) sms.append(alertBox(smsState.error, 'warn'));
  }
  const smsTest = h('div', 'nt-actions');
  const smsNote = h('span', 'nt-sub', '');
  smsTest.append(button('Bağlantıyı sına', { onClick: () => testConnection('sms', smsNote) }),
    smsNote);
  sms.append(smsTest);

  // ------------------------------------------- sessiz saatler + limit
  const local = payload.local || {};
  const discipline = formGrid({
    fields: [
      { key: 'quietEnabled', label: 'Sessiz saatler açık', type: 'checkbox',
        hint: 'Kapalıyken gece de toplu bildirim gidebilir.' },
      { key: 'quietStart', label: 'Başlangıç (SS:DD)', type: 'text', maxLength: 5,
        placeholder: '22:00' },
      { key: 'quietEnd', label: 'Bitiş (SS:DD)', type: 'text', maxLength: 5,
        placeholder: '08:00',
        hint: 'Gece yarısını aşabilir. Başlangıçla bitiş AYNI olursa pencere uygulanmaz.' },
      { key: 'dailyLimit', label: 'Günlük alıcı limiti', type: 'number', min: 0, max: 1000000,
        hint: '0 = sınır yok. Sayaç Kontrol Merkezi’nde tutulur; mağaza düşse de korur.' },
    ],
    value: { quietEnabled: local.quietEnabled, quietStart: local.quietStart,
      quietEnd: local.quietEnd, dailyLimit: local.dailyLimit },
  });
  nodes.channelForms.push(discipline);

  const disciplineBox = h('div');
  disciplineBox.append(discipline.node);
  if (payload.quiet?.error) disciplineBox.append(alertBox(payload.quiet.error, 'warn'));
  if (payload.quiet?.active) {
    disciplineBox.append(alertBox(
      `Şu an sessiz saat içindeyiz (${payload.quiet.window}); toplu gönderim durur.`, 'info'));
  }
  disciplineBox.append(h('div', 'nt-sub',
    `Bugün gönderilen: ${num(local.sentToday ?? 0)} alıcı`));

  // ------------------------------------------------ mobil uygulama
  const mobileBox = h('div');
  let mobileForm = null;
  if (!payload.mobileAvailable) {
    mobileBox.append(alertBox(
      `Mobil uygulama ayarları okunamadı — ${payload.mobileError || 'uç yayında değil'}. `
      + 'Mağaza tarafındaki uç yayına girince bu bölüm açılacak.', 'warn'));
  } else {
    const mobile = payload.mobile || {};
    mobileForm = formGrid({
      fields: [
        { key: 'pushEnabled', label: 'Push bildirimleri açık', type: 'checkbox' },
        { key: 'androidVersion', label: 'Android sürümü', type: 'text', maxLength: 32 },
        { key: 'iosVersion', label: 'iOS sürümü', type: 'text', maxLength: 32 },
        { key: 'minSupported', label: 'En düşük desteklenen sürüm', type: 'text', maxLength: 32 },
        { key: 'forceUpdate', label: 'Zorunlu güncelleme', type: 'checkbox',
          hint: 'Açıkken eski sürüm kullanıcıları uygulamayı kullanamaz.' },
        { key: 'maintenance', label: 'Bakım modu', type: 'checkbox' },
        { key: 'maintenanceText', label: 'Bakım metni', type: 'textarea', maxLength: 500,
          wide: true },
      ],
      value: mobile,
    });
    nodes.channelForms.push(mobileForm);
    mobileBox.append(mobileForm.node);
    if (mobile.pushKeyMasked) {
      mobileBox.append(secretRow('Push sağlayıcı anahtarı',
        { found: true, secret: true, masked: mobile.pushKeyMasked }));
    }
  }

  const save = button('Ayarları kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = discipline.draft();
      const reason = await askReason({
        title: 'Kanal ayarlarını kaydet',
        description: 'Sessiz saatler ve günlük limit bütün bildirim yollarını etkiler; '
          + 'mobil ayarlar mağazaya yazılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Ayarlar kaydediliyor…', async () => {
        const body = {
          quietStart: draft.quietStart, quietEnd: draft.quietEnd,
          quietEnabled: Boolean(draft.quietEnabled),
          dailyLimit: Number(draft.dailyLimit) || 0,
          reason, dryRun: false,
        };
        if (mobileForm) body.mobile = mobileForm.patch();
        const result = await call(`${BASE}/channels`, { method: 'POST', body });
        toast(`Kaydedildi: ${result.changed.join(', ') || 'değişiklik yok'}`, 'good');
        if (result.mobileError) toast(`Mobil ayarlar yazılamadı: ${result.mobileError}`, 'warn');
        refreshChannels();
      });
    },
  });

  const actions = h('div', 'nt-actions');
  actions.append(save);

  wrap.append(
    card('E-posta (SMTP)', smtp, 'Mağaza ayarı · sırlar maskeli'),
    card('SMS sağlayıcı', sms, 'Kontrol Merkezi katmanı'),
    card('Gönderim disiplini', disciplineBox, 'Yalnız Kontrol Merkezi’ni etkiler'),
    card('Mobil uygulama', mobileBox, 'Mağaza ayarı'),
    actions,
    auditCard(),
    hintBox('Ayrı bir “Mağaza Ayarları” ekranı yoktur: her ayar onu kullanan ekranda durur. '
      + 'Bildirim ve mobil ayarlar burada, vergi store_tax’ta, kargo ücreti store_shipping’de.'),
  );
}

async function testConnection(kind, note) {
  note.textContent = 'Sınanıyor…';
  note.classList.remove('bad');
  try {
    const result = await api(`${BASE}/channels/test?kind=${kind}`);
    note.textContent = result.message || (result.ready ? 'Bağlantı var.' : 'Bağlantı yok.');
    note.classList.toggle('bad', !result.ready);
  } catch (error) {
    note.textContent = error.message;
    note.classList.add('bad');
  }
}

// =================================================== SEKME 5 — abonelikler

function renderSubscribers() {
  const wrap = nodes.subsTable;
  if (!wrap) return;
  wrap.replaceChildren();

  if (!state.subscribers.connected) {
    wrap.append(alertBox(`Abone listesi okunamadı — ${state.subscribers.error}`, 'warn'));
  }
  const table = dataTable({
    columns: [
      { key: 'email', label: 'E-posta', width: 'minmax(0, 2fr)', className: 'mono' },
      { key: 'customerName', label: 'Müşteri', width: 'minmax(0, 1.4fr)' },
      { key: 'channel', label: 'Kanal', width: '120px' },
      { key: 'subscribed', label: 'Abonelik', width: '120px',
        cell: (row) => badge(row.subscribed ? 'Abone' : 'Ayrıldı',
          row.subscribed ? 'good' : 'dim') },
      { key: 'createdAt', label: 'Kayıt', width: '148px',
        cell: (row) => (row.createdAt ? stampIso(row.createdAt) : '—') },
    ],
    rows: state.subscribers.items,
    dense: true,
    empty: emptyState({
      title: 'Abone bulunamadı',
      text: 'Arama kutusunu temizleyin ya da "Abonelik" süzgecini gevşetin.',
      actions: [button('Filtreyi temizle', { onClick: () => nodes.subsFilters.reset() })],
    }),
  });
  wrap.append(table.node);
  wrap.append(hintBox(
    'Abonelikten çıkmış müşteriye toplu bildirim GÖNDERİLMEZ; alıcı sayısını mağaza '
    + 'hesaplar ve kuru prova onu gösterir.'));
}

// ============================================ SEKME 6 — müşteri aşama SMS'i
//
// ÜÇ AŞAMA: sipariş alındı · kargoya verildi · teslim edildi. Metin buradan
// düzenlenir, aşama tek tek açılıp kapatılır; TETİKLEYİCİLER Siparişler
// ekranındadır. Bu sekmeden ELLE SMS GÖNDERİLMEZ — "1.800 kişiye aşama SMS'i"
// diye bir düğme olmasın diye.
//
// SEGMENT SAYACI ÖNİZLEMENİN YANINDA DURUR ve ölçüm BİLEREK UZUN örnek veriyle
// yapılır: "Ayse Yilmaz" ile tek parçaya sığan metin "Mehmet Emin
// Karaosmanoglu" ile taşar ve her siparişte iki kredi yakar.

const STAGE_TONES = {
  sent: 'good', dry_run: 'info', no_phone: 'warn', bad_phone: 'warn',
  missing: 'warn', error: 'bad',
};

async function refreshStages() {
  state.loaded.stages = true;
  try {
    state.stages = await api(`${BASE}/stages`);
  } catch (error) {
    state.stages = { items: [], sms: {}, error: error.message };
  }
  try {
    state.stageLog = await api(`${BASE}/stages/log?limit=100`);
  } catch (error) {
    state.stageLog = { items: [], summary: {}, error: error.message };
  }
  renderStages();
}

function renderStages() {
  const wrap = nodes.stageWrap;
  if (!wrap) return;
  dropStagePreviews();
  drop(nodes.stageForms);
  wrap.replaceChildren();

  const payload = state.stages || {};
  if (payload.error) wrap.append(alertBox(payload.error, 'bad'));

  wrap.append(card('SMS freni', stageBrakeBox(payload.sms || {}),
    'Gerçek SMS yalnız üç katmanın üçü de kapalıysa çıkar'));

  for (const item of payload.items || []) wrap.append(stageCard(item));

  wrap.append(card('Gönderim izi', stageLogBox(),
    'Gönderilmeyenler de burada — nedeniyle'));
  wrap.append(hintBox(
    'Bu ekrandan elle SMS gönderilmez. Tetikleyiciler Siparişler ekranındadır: yeni '
    + 'sipariş ve teslim edilen gönderi taramayla, “kargoya verildi” ise kargoya verme '
    + 'eyleminin kendisiyle. Aynı siparişe aynı aşama için ikinci SMS gitmez.'));
}

/** Üç katmanın HER BİRİ ayrı yazılır: "gönderilmiyor" demek hangi anahtarın
 *  açılacağını söylemez. */
function stageBrakeBox(sms) {
  const box = h('div');
  if (!sms.available) {
    box.append(alertBox(sms.error || 'SMS katmanı (notify) bu kurulumda yok; aşama SMS\'i '
      + 'gönderilemez. Metinler yine yazılabilir.', 'warn'));
    return box;
  }
  box.append(
    secretRow('Sağlayıcı', { found: true, value: sms.provider || '—' }),
    secretRow('Gönderici başlığı', { found: true, value: sms.header || '—' }),
    secretRow('Kimlik bilgisi', { found: true,
      value: sms.configured ? 'girilmiş' : 'kurulmamış' }),
  );
  const brakes = [
    ['Platform freni', sms.platformDryRun, 'platform.notify.sms.dry_run'],
    ['Modül freni', sms.moduleDryRun, 'modules.store_notifications.lifecycle_sms_dry_run'],
  ];
  for (const [label, on, key] of brakes) {
    box.append(h('div', 'nt-sub',
      `${label}: ${on ? 'AÇIK — gerçek SMS gitmez' : 'kapalı'} (${key})`));
  }
  box.append(h('div', 'nt-sub',
    'Üçüncü katman tetikleyicidedir: modules.store_orders.stage_sms_dry_run'));
  if ((sms.allowlist || []).length) {
    box.append(alertBox(
      `Beyaz liste dolu (${sms.allowlist.length} numara): yalnız bu numaralara gerçek SMS `
      + 'gider, diğerleri kuru provaya düşer ve nedeni ize yazılır.', 'info'));
  }
  if (sms.error) box.append(alertBox(sms.error, 'warn'));
  return box;
}

function stageCard(item) {
  const box = h('div');

  const form = formGrid({
    fields: [
      { key: 'enabled', label: 'Bu aşama açık', type: 'checkbox',
        hint: 'Kapalıyken bu aşama için hiçbir SMS gönderilmez ve iz de yazılmaz.' },
    ],
    value: { enabled: item.enabled },
  });
  nodes.stageForms.push(form);

  const area = h('textarea', 'kit-textarea nt-body');
  area.rows = 4;
  area.value = item.body || '';
  area.maxLength = 1000;

  const palette = h('div', 'nt-palette');
  for (const variable of item.variables || []) {
    const chip = h('button', `nt-token${variable.required ? ' nt-token-must' : ''}`,
      variable.token);
    chip.type = 'button';
    chip.title = variable.required
      ? `${variable.label} — BU AŞAMADA ZORUNLU. Örnek: ${variable.sample}`
      : `${variable.label} — örnek: ${variable.sample}`;
    chip.addEventListener('click', () => insertToken(area, variable.token));
    palette.append(chip);
  }

  const preview = h('div', 'nt-preview');
  const counter = h('div', 'nt-counter');
  const problems = h('div', 'nt-problems');
  const saveBtn = button('Kaydet', { variant: 'primary',
    onClick: () => saveStage(item.stage, form, area) });

  const paint = (view) => {
    problems.replaceChildren();
    preview.replaceChildren();
    counter.replaceChildren();
    if (!view || view.ok === false) {
      problems.append(alertBox(view?.error || 'Önizleme alınamadı.', 'bad'));
      return;
    }
    // TEK SEGMENTİ AŞAN ŞABLON KAYDEDİLMEZ: düğme tıklanmadan kapanır ve
    // NEDENİ yazılır. Kapı ayrıca backend'dedir (K9) — burada gizlemek
    // yetkilendirme değil, nezakettir.
    if (view.problem) problems.append(alertBox(view.problem, 'bad'));
    preview.append(h('pre', 'nt-preview-body', view.preview || ''));
    const plan = view.plan || {};
    counter.append(kpiRow([
      { label: 'Karakter', value: `${num(plan.units)} / ${num(plan.capacity)}` },
      { label: 'SMS parçası', value: num(plan.parts), tone: plan.parts > 1 ? 'bad' : 'good' },
      { label: 'Kalan yer', value: `${num(plan.remaining)} karakter` },
      { label: 'Kodlama', value: plan.unicode ? 'UCS-2 (70)' : (plan.encoding || 'GSM-7') },
    ]));
    if ((plan.offending || []).length) {
      counter.append(alertBox(
        `Şu karakterler mesajı pahalılaştırıyor: ${plan.offending.join(' ')}`, 'warn'));
    }
    if (plan.simplified && plan.simplifiedParts < plan.parts) {
      const row = h('div', 'nt-suggest');
      row.append(h('span', undefined,
        `Türkçe harfler sadeleştirilirse ${plan.parts} yerine ${plan.simplifiedParts} parça:`));
      row.append(button('Sadeleştirilmişi kullan', {
        onClick: () => { area.value = plan.simplified; run(); },
      }));
      counter.append(row);
    }
    saveBtn.disabled = Boolean(view.problem);
  };

  const run = debounce(async () => {
    try {
      paint(await api(`${BASE}/stages/preview`, {
        method: 'POST', body: { stage: item.stage, body: area.value },
      }));
    } catch (error) {
      paint({ ok: false, error: error.message });
    }
  }, 250);
  nodes.stagePreviews.push(run);
  area.addEventListener('input', () => run());

  const actions = h('div', 'nt-actions');
  actions.append(saveBtn, button('Varsayılana dön', {
    variant: 'ghost',
    title: 'Fabrika metnini geri getirir. Kaydetmeden önce yine ölçülür.',
    onClick: () => { area.value = item.defaultBody || ''; run(); },
  }));

  box.append(
    h('div', 'nt-sub', `Tetikleyici: ${item.trigger}`),
    form.node, palette, area, problems, preview, counter, actions,
  );
  if (item.default) {
    box.append(h('div', 'nt-sub', 'Metin fabrika ayarında.'));
  } else if (item.updatedAt) {
    box.append(h('div', 'nt-sub',
      `Son değişiklik: ${stampIso(item.updatedAt)} · ${item.actor || '—'}`));
  }

  paint(item);          // ilk çizim sunucunun verdiği ölçümle
  return card(item.label, box, item.enabled ? 'Açık' : 'KAPALI');
}

async function saveStage(stage, form, area) {
  const reason = await askReason({
    title: 'Aşama SMS\'ini kaydet',
    description: 'Metin ve Açık/Kapalı durumu müşteriye giden mesajı doğrudan belirler. '
      + 'Tek segmenti aşan metin kaydedilmez.',
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;
  await withBusy('Aşama kaydediliyor…', async () => {
    await call(`${BASE}/stages`, {
      method: 'POST',
      body: { stage, body: area.value, enabled: Boolean(form.draft().enabled), reason },
    });
    toast('Aşama kaydedildi.', 'good');
    await refreshStages();
  });
}

function stageLogBox() {
  const box = h('div');
  const payload = state.stageLog || { items: [] };
  if (payload.error) box.append(alertBox(payload.error, 'warn'));
  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '148px',
        cell: (row) => (row.createdAt ? stampIso(row.createdAt) : '—') },
      { key: 'stageLabel', label: 'Aşama', width: '140px' },
      { key: 'orderNo', label: 'Sipariş', width: '130px', className: 'mono' },
      { key: 'customer', label: 'Müşteri', width: 'minmax(0, 1.2fr)' },
      { key: 'phone', label: 'Numara', width: '120px', className: 'mono' },
      { key: 'resultLabel', label: 'Sonuç', width: '190px',
        cell: (row) => badge(row.resultLabel, STAGE_TONES[row.result] || '') },
      { key: 'note', label: 'Açıklama', width: 'minmax(0, 1.6fr)' },
    ],
    rows: payload.items || [],
    dense: true,
    empty: emptyState({
      title: 'Henüz aşama SMS\'i denenmedi',
      text: 'Aşamalar açıldığında ve tetikleyici çalıştığında gönderilenler de '
        + 'gönderilemeyenler de burada listelenir.',
    }),
  });
  box.append(table.node);
  box.append(hintBox(
    'Numarası olmayan müşteri SESSİZCE GEÇİLMEZ: satır “gönderilemedi: numara yok” '
    + 'diye burada durur. Numara düzeltilince aynı aşama yeniden denenir; gerçekten '
    + 'gitmiş bir mesaj ise ikinci kez gönderilmez.'));
  return box;
}

function dropStagePreviews() {
  for (const item of nodes.stagePreviews || []) item.cancel?.();
  nodes.stagePreviews = [];
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel nt');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  nodes.templateForms = [];
  nodes.ruleForms = [];
  nodes.channelForms = [];
  nodes.stageForms = [];
  nodes.stagePreviews = [];
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });

  const tabs = tabBar([
    { key: 'history', label: 'Gönderim geçmişi' },
    { key: 'templates', label: 'Şablonlar' },
    { key: 'stages', label: 'Müşteri SMS’i' },
    { key: 'rules', label: 'Kurallar' },
    { key: 'channels', label: 'Kanallar' },
    { key: 'subscribers', label: 'Abonelikler' },
  ], 'history', (key) => showTab(key));

  nodes.status = statusLine();

  // ----------------------------------------------------- geçmiş sekmesi
  nodes.historyFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Alıcı, konu, içerik, sipariş no', width: '250px' },
      { kind: 'select', key: 'channel', label: 'Kanal', options: [{ value: '', label: 'Tümü' }] },
      { kind: 'select', key: 'status', label: 'Durum', options: [{ value: '', label: 'Tümü' }] },
      { kind: 'select', key: 'event', label: 'Olay', options: [{ value: '', label: 'Tümü' }] },
      { kind: 'dateRange', key: 'range', label: 'Tarih' },
      { kind: 'numRange', key: 'cost', label: 'Maliyet', money: true },
      { kind: 'toggle', key: 'failedOnly', label: 'Başarısızlar' },
    ],
    onChange: () => refreshHistory({ page: 1 }),
    actions: [
      button('Yenile', { onClick: () => refreshHistory() }),
      button('⤓ Görünen', { title: 'Ekrandaki sayfayı CSV indir', onClick: exportVisibleHistory }),
      button('⤓ Tümü', { title: 'Tüm kayıtları rapor klasörüne yaz', onClick: exportAllHistory }),
      button('Gönderim raporu', { onClick: () => report.run('delivery', reportParams()) }),
      button('SMS maliyet icmali', { onClick: () => report.run('sms_cost', reportParams()) }),
    ],
  });
  nodes.historyKpi = h('div', 'nt-kpi');
  nodes.historyTable = h('div', 'nt-table');
  nodes.historyPager = pager({
    total: 0, page: 1, size: 100, sizes: [50, 100, 200],
    onChange: ({ page, size }) => refreshHistory({ page, size }),
  });
  const historyView = h('div');
  historyView.append(nodes.historyFilters.node, nodes.historyKpi, nodes.historyTable,
    nodes.historyPager.node);

  // --------------------------------------------------- şablon sekmesi
  nodes.templateList = h('div', 'nt-tlist');
  nodes.templateEditor = h('div', 'nt-editor');
  const templateLeft = h('div');
  const newBtn = h('div', 'nt-actions');
  newBtn.append(
    button('Yeni şablon', { variant: 'primary', onClick: () => openTemplate(null) }),
    button('Yenile', { onClick: () => refreshTemplates() }),
  );
  templateLeft.append(newBtn, nodes.templateList);
  const templateView = splitView(templateLeft, nodes.templateEditor,
    'minmax(0, 1fr) minmax(0, 1.4fr)');

  // --------------------------------------------------- kural sekmesi
  nodes.ruleWrap = h('div', 'nt-table');
  const ruleView = h('div');
  const ruleActions = h('div', 'nt-actions');
  ruleActions.append(
    newRuleButton(),
    button('Yenile', { onClick: () => refreshRules() }),
  );
  ruleView.append(ruleActions, nodes.ruleWrap, hintBox(
    'Kural MAĞAZADA çalışır: tetikleyen olay orada olur. Kontrol Merkezi kapalıyken de '
    + 'bildirim gitmeye devam eder.'));

  // -------------------------------------------------- kanal sekmesi
  nodes.channelWrap = h('div', 'nt-channels');

  // ------------------------------------------------ abonelik sekmesi
  nodes.subsFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'E-posta ara', width: '250px' },
      { kind: 'select', key: 'subscribed', label: 'Abonelik', options: [
        { value: '', label: 'Tümü' }, { value: '1', label: 'Abone' },
        { value: '0', label: 'Ayrılmış' },
      ] },
    ],
    onChange: () => refreshSubscribers({ page: 1 }),
    actions: [button('Yenile', { onClick: () => refreshSubscribers() })],
  });
  nodes.subsTable = h('div', 'nt-table');
  nodes.subsPager = pager({
    total: 0, page: 1, size: 100, sizes: [50, 100, 200],
    onChange: ({ page, size }) => refreshSubscribers({ page, size }),
  });
  const subsView = h('div');
  subsView.append(nodes.subsFilters.node, nodes.subsTable, nodes.subsPager.node);

  // ------------------------------------------ müşteri aşama SMS'i sekmesi
  nodes.stageWrap = h('div', 'nt-channels');
  const stageView = h('div');
  const stageActions = h('div', 'nt-actions');
  stageActions.append(button('Yenile', { onClick: () => refreshStages() }));
  stageView.append(stageActions, nodes.stageWrap);

  nodes.body = h('div', 'nt-body');
  const views = { history: historyView, templates: templateView, stages: stageView,
    rules: ruleView, channels: nodes.channelWrap, subscribers: subsView };
  const loaders = { history: refreshHistory, templates: refreshTemplates,
    stages: refreshStages, rules: refreshRules, channels: refreshChannels,
    subscribers: refreshSubscribers };

  function showTab(key) {
    state.tab = key;
    nodes.body.replaceChildren(views[key]);
    if (!state.loaded[key]) loaders[key]();
    // Kurallar sekmesi şablon listesine bağımlı: seçici boş açılmasın.
    if (key === 'rules' && !state.loaded.templates) refreshTemplates();
  }

  view.append(tabs.node, nodes.status.node, nodes.body);
  root.replaceChildren(view);
  nodes.body.replaceChildren(historyView);
  nodes.status.set('Bildirimler yükleniyor…');

  // Sabitler ÖNCE gelir: süzgeç açılırları dolmadan liste çekmek, kullanıcının
  // seçtiği kanalı/olayı kaybettiriyordu.
  loadCatalog().then(() => {
    nodes.historyFilters.options('channel', [{ value: '', label: 'Tümü — kanal' },
      ...state.catalog.channels.map((item) => ({ value: item.key, label: item.label }))]);
    nodes.historyFilters.options('status', [{ value: '', label: 'Tümü — durum' },
      ...state.catalog.statuses.map((item) => ({ value: item.key, label: item.label }))]);
    nodes.historyFilters.options('event', [{ value: '', label: 'Tümü — olay' },
      ...state.catalog.events.map((item) => ({ value: item.key, label: item.label }))]);
    refreshHistory();
  });

  return () => {
    nodes.historyFilters?.destroy();     // arama alanı bekleyen debounce tutar
    nodes.subsFilters?.destroy();
    nodes.templatePreview?.cancel();     // açık düzenleyicinin bekleyen önizlemesi
    nodes.templatePreview = null;
    dropStagePreviews();                 // üç aşamanın bekleyen önizlemeleri
    drop(nodes.templateForms);
    drop(nodes.ruleForms);
    drop(nodes.channelForms);
    drop(nodes.stageForms);
    root.replaceChildren();
    state = fresh();
    busy = false;
  };
}
