// Durum Monitörü paneli — "bir şey çalışmıyor" şikâyeti geldiğinde bakılacak
// ilk ekran.
//
// NE YAPAR: dört bileşen için sağlık kutusu (mobil uygulama · web sitesi ·
// mutfak kasaları · sunucu); her araştırma için ayrı bir durum satırı;
// sunucudaki hata olaylarının süzülen listesi ve gerekçeli çözüm; kasa sağlık
// tablosu; gözlem ve yazmaları TEK akışta birleştiren olay geçmişi; düzeltme
// defteri ve defterden komut gönderme; bu ekranın kendi yerel kaydı.
//
// NE YAPMAZ:
//  · SAĞLIK HÜKMÜNÜ YENİDEN HESAPLAMAZ. `ok` / `degraded` / `down` SUNUCUNUN
//    tek cümlelik hükmüdür (`monitor.md` → `GET /summary`). Üç ayrı ekranın
//    (izleme · gösterge paneli · KDS yönetimi) aynı duruma bakıp farklı renk
//    göstermesi, hangisine inanılacağını belirsiz kılardı.
//  · KASA YÖNETMEZ. Eşleme, ayar ve cihaz düzenleme `bld_kds` ekranındadır.
//    Buradan yalnız DÜZELTME komutları gider ve hepsi defterde tanımlıdır.
//  · KABUK KOMUTU ÇALIŞTIRMAZ. Her komut `bld.api` geçidinden geçer (K4);
//    kabuk erişimi gerektiren adımlar deftere yazılabilir ama ÇALIŞTIRILAMAZ
//    ve düğmesi kapalı çizilir, nedeni üstünde yazar.
//  · KAYIT SİLMEZ. Bir hata kaydını silmek, o hatanın hiç olmadığını iddia
//    etmektir; çözülen olay işaretlenir ve varsayılan listeden düşer.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · "BİLİNMİYOR" İLE "DURDU" AYRI ŞEYDİR. İlki "soramadım", ikincisi
//    "sordum, kötü". Aynı kutuda göstermek, kopmuş bir ağı çökmüş bir sisteme
//    çevirirdi — ekipler boşuna sahaya gider.
//  · UÇ HENÜZ YAYINDA OLMAYABİLİR. Sunucu tarafı paralel yazılıyor; geçit
//    `control_endpoint_missing` verdiğinde ekran ZARİFÇE bozulur: kutular
//    "uç henüz yayında değil" der, yerel kayıt ve defter çalışmaya devam eder.
//    Bu beklenen bir durumdur, hata değil.
//  · `info` VARSAYILAN SÜZGEÇTE GİZLİDİR. Bilgi seviyesindeki olaylar sayıca
//    en kalabalık olanlardır ve listeyi doldurup gerçek hataları görünmez
//    kılarlar. Gizlendiği ekranda YAZAR.
//  · ÜÇ DURUMLU ALANLAR. `printer_ok` / `sound_ok` / `alarm_muted` `null`
//    olabilir ve `null` "bilinmiyor" demektir, `false` değil. Sağlık
//    bildirmemiş bir kasa arızalı gösterilmez.
//  · TEKRAR SAYISI GÖRÜNÜR. 47 kez tekrarlanmış bir hata ile bir kez görülmüş
//    bir hata aynı satır yüksekliğinde ama aynı aciliyette değil.
//  · RENK TEK BAŞINA ANLAM TAŞIMAZ (kit kuralı 7). Her rozetin yanında yazı
//    var; ekran siyah-beyaz yazdırıldığında da okunur.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_status_monitor/ →
// shell/ui-kit/. Bu dosyanın KAYNAĞI modules/bld_status_monitor/ui/panel/
// altındadır; orada '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, blockedButton, button, confirmWithReason, h, loadStyles, num, pollLoop,
  stampIso, toaster, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, card, drawer, emptyState, hintBox, kpiRow, skeletonRows,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { timeline } from '../../ui-kit/flow.js';

const BASE = '/api/bld_status_monitor';

/** Gerekçe sınırları — sunucu da denetliyor (sözleşme §3), bu erken geri bildirim. */
const REASON_MIN = 10;
const REASON_MAX = 160;

/** `since` süzgecinin hazır aralıkları. `<input type="date">` YASAK (kit kuralı 1). */
const SINCE_PRESETS = [
  { value: '', label: 'Sunucu varsayılanı (7 gün)' },
  { value: '0', label: 'Bugün' },
  { value: '1', label: 'Son 2 gün' },
  { value: '7', label: 'Son 7 gün' },
  { value: '30', label: 'Son 30 gün' },
];

// ------------------------------------------------------------------ durum

const EMPTY_STATE = {
  tab: 'health',
  // BAĞLANTI: `ok:true` ile gelen `connected:false` (K7). Ayrı tutulur çünkü
  // "hata yok" ile "sunucuya ulaşılamıyor" aynı ekranda aynı görünmemeli — bu
  // ekranda o karışıklık, izleme ekranının söyleyebileceği en kötü yalan.
  link: { connected: true, error: '', missing: false },
  contract: null,
  prefs: null,
  limits: null,
  summary: null,
  tiles: [],
  events: [],
  eventMeta: {},
  eventFilters: {},
  page: 1,
  size: 25,
  devices: [],
  deviceMeta: {},
  history: [],
  local: [],
  localFilters: {},
  runbook: [],
  audit: [],
  loaded: false,
  stale: false,
  error: '',
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
    const error = new Error(String(result.error));
    error.code = result.code || '';
    throw error;
  }
  return result;
}

/**
 * BAĞLANTI DURUMU — `ok:true` ile gelen `connected:false` (K7).
 *
 * `endpoint_missing` AYRI TUTULUR: "uç henüz dağıtılmadı" bir arıza değil,
 * beklenen bir geçiş hâlidir ve kırmızı gösterilmez.
 *
 * @returns {boolean} veri güvenilir mi
 */
function linkOk(payload) {
  if (payload && payload.connected === false) {
    state.link = {
      connected: false,
      error: payload.error || 'BLD sunucusuna ulaşılamıyor.',
      missing: Boolean(payload.endpoint_missing),
    };
    return false;
  }
  // `connected` HİÇ YOKSA (ör. ağa çıkmayan `/overview`) durum DEĞİŞMEZ:
  // taşımayan bir yanıt, bilinen bir kopukluğu "düzeldi" saymamalı.
  if (payload && payload.connected === true) {
    state.link = { connected: true, error: '', missing: false };
  }
  return true;
}

/** Bağlantı kopukken çizilen uyarı; bağlantı varken `null`. */
function linkAlert({ what = 'Veri' } = {}) {
  if (state.link.connected) return null;
  if (state.link.missing) {
    return alertBox(
      `${what} okunamadı: izleme uçları sunucuya HENÜZ DAĞITILMADI. `
      + 'Bu beklenen bir durum — sunucu eklentisi güncellenince ekran '
      + 'kendiliğinden dolacak. Bu ekranın YEREL kaydı ve düzeltme defteri '
      + 'çalışmaya devam ediyor.', 'warn');
  }
  return alertBox(
    `BLD sunucusuna ULAŞILAMIYOR — ${state.link.error} ${what} okunamadı. `
    + 'Bu kopukluk bu ekranın YEREL kaydına yazıldı; "Geçmiş" sekmesinde '
    + 'duruyor ve sunucu tarafında karşılığı YOK. Bağlantı geri geldiğinde '
    + 'ekran kendiliğinden düzelir.', 'bad');
}

/** Yazma düğmesinin kapalı olma nedeni; yazılabiliyorsa boş dize. */
function linkBlock() {
  if (state.link.connected) return '';
  return `BLD sunucusuna ulaşılamıyor (${state.link.error}) — ulaşılamayan bir `
    + 'sunucuya gönderilen komut yöneticiye "gitti" hissi verirdi. Bağlantı '
    + 'gelince düğme kendiliğinden açılır.';
}

/**
 * Yazma düğmesi. Bağlantı yoksa ya da `blocked` doluysa KAPALI çizilir ve
 * nedenini söyler — sessizce çalışmayan düğme bırakılmaz (kit `blockedButton`).
 */
function writeButton(label, { variant = '', title = '', onClick, blocked = '' } = {}) {
  const why = blocked || linkBlock();
  if (why) return blockedButton(label, why, { variant });
  return button(label, { variant, title, onClick });
}

/** Aynı anda tek yazma. İki kez tıklanan bir "yeniden başlat", iki kesinti olurdu. */
async function withBusy(message, work) {
  if (busy) return;
  busy = true;
  nodes.status?.set(message);
  try {
    await work();
  } catch (failure) {
    toast(failure.message || 'İşlem başarısız.', 'bad');
    nodes.status?.set(failure.message || 'İşlem başarısız.', true);
  } finally {
    busy = false;
  }
}

/**
 * Gerekçeli onay. Kitin kutusu 255 karaktere kadar yazdırıyor; sunucu sınırı
 * 160. Farkı burada yakalamak, kullanıcıya 422 yerine kendi cümlesini
 * göstermek demektir.
 */
async function askReason({ title, description, confirmLabel, danger = true }) {
  const reason = await confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    danger,
    minLength: REASON_MIN,
    placeholder: `Gerekçe (${REASON_MIN}-${REASON_MAX} karakter, denetim kaydına yazılır)`,
  });
  if (!reason) return null;
  if (reason.length > REASON_MAX) {
    toast(`Gerekçe en çok ${REASON_MAX} karakter olabilir; ${reason.length} yazıldı.`, 'bad');
    return null;
  }
  return reason;
}

/** ISO damgasını "16 Ağu 09:00" + "3 saat önce" ikilisiyle yazar. */
function whenCell(value) {
  const box = h('span', 'sm-cell');
  if (!value) {
    box.append(h('b', undefined, '—'));
    return box;
  }
  box.append(h('b', undefined, stampIso(value)));
  box.append(h('span', 'sm-dim', ago(value)));
  return box;
}

/** İki satırlı hücre: üstte ana bilgi, altta tamamlayıcı. */
function twoLine(main, sub) {
  const box = h('span', 'sm-cell');
  box.append(h('b', undefined, String(main ?? '—')));
  if (sub) box.append(h('span', 'sm-dim', String(sub)));
  return box;
}

// =================================================================== veri

async function loadOverview() {
  try {
    const payload = await call(`${BASE}/overview`);
    linkOk(payload);
    state.contract = payload.contract;
    state.prefs = payload.prefs;
    state.limits = payload.limits;
    state.size = payload.prefs?.page_size || payload.limits?.page_size || 25;
  } catch (failure) {
    // Sözleşme ucu ağa çıkmıyor; buraya düşmek çekirdeğin kendisiyle ilgili
    // bir sorundur ve ekranın geri kalanı yine de çizilmeli.
    state.error = failure.message;
  }
}

async function loadSummary() {
  try {
    const payload = await call(`${BASE}/summary`);
    state.summary = payload.summary || null;
    state.tiles = payload.tiles || [];
    if (!linkOk(payload)) {
      // Kutular yine çizilir ve dördü de "bilinmiyor" der: boş bir ekran,
      // kopukluğu "her şey yolunda" gibi gösterirdi.
      state.error = payload.error || '';
      return;
    }
    state.error = '';
    state.loaded = true;
  } catch (failure) {
    state.error = failure.message;
  }
}

function eventQuery() {
  const values = state.eventFilters || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.source) params.set('source', values.source);
  if (values.level) params.set('level', values.level);
  if (values.resolved) params.set('resolved', values.resolved);
  // Gün sayısı ISO GÜNE çevrilir; `todayIso()` kullanılır, `toISOString()`
  // DEĞİL — ikincisi UTC'ye kayar ve "bugün" dün olabilir (kit kuralı 6).
  if (values.since) params.set('since', todayIso(-Number(values.since)));
  params.set('page', String(state.page));
  params.set('per_page', String(state.size));
  return params.toString();
}

async function loadEvents() {
  try {
    const payload = await call(`${BASE}/events?${eventQuery()}`);
    if (!linkOk(payload)) {
      // Eski satırlar EKRANDA KALIR ve "bayat" diye işaretlenir.
      state.stale = state.events.length > 0;
      state.error = payload.error || '';
      return;
    }
    state.events = payload.items || [];
    state.eventMeta = payload.meta || {};
    state.stale = false;
    state.error = '';
  } catch (failure) {
    state.error = failure.message;
    state.stale = state.events.length > 0;
  }
}

async function loadDevices() {
  try {
    const payload = await call(`${BASE}/devices`);
    if (!linkOk(payload)) {
      state.stale = state.devices.length > 0;
      return;
    }
    state.devices = payload.items || [];
    state.deviceMeta = payload.meta || {};
    state.stale = false;
  } catch (failure) {
    state.error = failure.message;
  }
}

async function loadHistory() {
  const payload = await call(`${BASE}/history`);
  state.history = payload.items || [];
}

function localQuery() {
  const values = state.localFilters || {};
  const params = new URLSearchParams();
  if (values.q) params.set('q', values.q);
  if (values.source) params.set('source', values.source);
  if (values.result) params.set('result', values.result);
  if (values.kind) params.set('kind', values.kind);
  return params.toString();
}

async function loadLocal() {
  const payload = await call(`${BASE}/log?${localQuery()}`);
  state.local = payload.items || [];
}

async function loadRunbook() {
  const payload = await call(`${BASE}/runbook`);
  state.runbook = payload.items || [];
}

async function loadAudit() {
  const payload = await call(`${BASE}/audit?limit=100`);
  state.audit = payload.items || [];
}

// =================================================================== çizim

function paintStatus() {
  if (!state.link.connected) {
    nodes.status.set(state.link.missing
      ? 'İzleme uçları sunucuya henüz dağıtılmadı — yerel kayıt çalışıyor.'
      : `Bağlantı yok · ${state.link.error}`, !state.link.missing);
    return;
  }
  const health = state.summary?.health;
  const open = state.summary?.events?.open_total ?? 0;
  nodes.status.set(health
    ? `Sunucunun hükmü: ${health.label} · ${num(open)} açık olay`
    : 'Bağlı', health ? health.status === 'down' : false);
}

/** Sağlık sekmesi — dört kutu, hüküm şeridi ve araştırma satırları. */
function showHealth() {
  const body = h('div', 'sm-stack');

  const warn = linkAlert({ what: 'Sağlık özeti' });
  if (warn) body.append(warn);

  if (!state.tiles.length) {
    body.append(skeletonRows(2, 4));
    nodes.body.replaceChildren(body);
    return;
  }

  // DÖRT KUTU. `value` bir SAYI DEĞİL, DURUM YAZISIDIR: renk tek başına anlam
  // taşımaz ve "Aksıyor" yazan bir kutu, sarı bir kutudan daha anlaşılırdır.
  body.append(kpiRow(state.tiles.map((tile) => ({
    label: tile.label,
    value: tile.status_label,
    tone: tile.tone,
    title: `${tile.hint} · ${tile.open_events} açık olay`,
  }))));

  const health = state.summary?.health;
  if (health) {
    const box = h('div', 'sm-verdict');
    box.append(h('span', 'sm-verdict-label', 'Sunucunun hükmü'));
    box.append(badge(health.label, health.tone));
    // Sebepler MAKİNE OKUNUR gelir ve Türkçesini panel yazar (sözleşme).
    for (const label of health.reason_labels || []) box.append(badge(label, 'warn'));
    if (!(health.reason_labels || []).length) {
      box.append(h('span', 'sm-dim', 'Bildirilen bir sebep yok.'));
    }
    body.append(card('Sistemin bütünü', box,
      'Hüküm sunucuda verilir; bu ekran onu yeniden hesaplamaz.'));
  }

  // HER ARAŞTIRMA İÇİN AYRI SATIR. Kutu "ne durumda" der, satır "neden"
  // der: ikisini tek kutuya sıkıştırmak, sebebi görünmez yapardı.
  const probes = h('div', 'sm-probes');
  for (const tile of state.tiles) {
    const line = statusLine();
    const notes = (tile.notes || []).join(' · ');
    line.set(`${tile.label}: ${tile.status_label}${notes ? ` — ${notes}` : ''}`,
      tile.status === 'down');
    probes.append(line.node);
  }
  body.append(card('Araştırmalar', probes,
    'Her satır bir bileşenin son yoklamasıdır ve yerel kayda işlenir.'));

  const counters = state.summary?.events;
  if (counters) {
    const row = h('div', 'sm-badges');
    for (const level of state.contract?.levels || []) {
      const value = counters.open?.[level.code] ?? 0;
      row.append(badge(`${level.label}: ${num(value)}`, value > 0 ? level.tone : 'dim'));
    }
    const oldest = counters.oldest_open_at;
    if (oldest) {
      row.append(badge(`En eski açık olay: ${stampIso(oldest)} (${ago(oldest)})`, 'dim'));
    }
    body.append(card('Açık olaylar', row,
      'Sayılar süzgeçten bağımsızdır; listedeki süzgeç bunları değiştirmez.'));
  }

  const devices = state.summary?.devices;
  if (devices) {
    const row = h('div', 'sm-badges');
    row.append(badge(`Kasa: ${num(devices.total)}`, 'dim'));
    row.append(badge(`Çevrimiçi: ${num(devices.online)}`,
      devices.online < devices.total ? 'warn' : 'good'));
    // `printer_fault` YALNIZ `printer_ok === false` olanları sayar; sağlık
    // bildirmemiş kasa arıza sayılmaz (sözleşme).
    row.append(badge(`Yazıcı arızası: ${num(devices.printer_fault)}`,
      devices.printer_fault > 0 ? 'bad' : 'good'));
    row.append(badge(`Bekleyen baskı: ${num(devices.queue_pending)}`, 'dim'));
    row.append(badge(`Başarısız baskı: ${num(devices.queue_failed)}`,
      devices.queue_failed > 0 ? 'bad' : 'good'));
    if (devices.queue_oldest_age_minutes > 0) {
      // EN ÇOK İŞE YARAYAN ALAN: "kuyrukta 4 iş var" normaldir, "en eskisi 41
      // dakikadır bekliyor" kuyruğun akmadığı anlamına gelir.
      row.append(badge(
        `En eski iş: ${num(devices.queue_oldest_age_minutes)} dk bekliyor`,
        devices.queue_oldest_age_minutes > 15 ? 'bad' : 'warn'));
    }
    body.append(card('Mutfak kasaları', row,
      'Sayaçlar sunucuda hesaplanır; panelin saati kaymış olabilir.'));
  }

  nodes.body.replaceChildren(body);
}

/** Hata olayları sekmesi — süzgeç + tablo + sayfalayıcı. */
function showEvents() {
  const body = h('div', 'sm-stack');

  const warn = linkAlert({ what: 'Hata listesi' });
  if (warn) body.append(warn);
  if (state.stale && state.events.length) {
    body.append(alertBox(
      'Aşağıdaki satırlar son başarılı okumadan kalma ve BAYAT: yeni hatalar '
      + 'burada görünmüyor olabilir.', 'warn'));
  }

  body.append(nodes.eventFilters.node);

  const hidden = !(state.eventFilters.level || '').includes('info');
  if (hidden) {
    // GİZLENDİĞİ EKRANDA YAZAR: sessizce süzülen bir seviye, "hata yok"
    // sanılmasına yol açardı.
    body.append(hintBox(
      'Bilgi seviyesindeki olaylar varsayılan süzgeçte GİZLİ: sayıca en '
      + 'kalabalık olanlar onlar ve listeyi doldurup gerçek hataları görünmez '
      + 'kılıyorlar. Görmek için seviye süzgecinden "Bilgi" seçin.'));
  }

  nodes.eventTable.update({
    rows: state.events,
    empty: emptyState({
      title: state.link.connected ? 'Bu süzgece uyan hata yok' : 'Liste okunamadı',
      text: state.link.connected
        ? 'Varsayılan süzgeç yalnız AÇIK olayları ve `info` dışındaki '
          + 'seviyeleri gösterir. Çözülenleri görmek için süzgeci değiştirin.'
        : state.link.error,
      actions: [button('Süzgeci temizle', { onClick: () => resetEventFilters() })],
    }),
  });
  body.append(nodes.eventTable.node);

  nodes.pager.update({
    total: Number(state.eventMeta.total || 0),
    page: Number(state.eventMeta.page || state.page),
    size: state.size,
  });
  body.append(nodes.pager.node);

  nodes.body.replaceChildren(body);
}

/** Kasa sağlık tablosu. */
function showDevices() {
  const body = h('div', 'sm-stack');
  const warn = linkAlert({ what: 'Kasa listesi' });
  if (warn) body.append(warn);

  body.append(hintBox(
    'Bu tablo kasaları YÖNETMEZ; eşleme, ayar ve cihaz düzenleme KDS Yönetimi '
    + 'ekranındadır. `printer_ok` / `sound_ok` / `alarm_muted` alanları ÜÇ '
    + 'DURUMLUDUR: "bildirilmedi" ile "arızalı" ayrı şeylerdir ve sağlık '
    + 'bildirmemiş bir kasa arızalı sayılmaz.'));

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kasa', width: 'minmax(0, 1.4fr)',
        cell: (row) => twoLine(row.name || `#${row.device_id}`,
          row.app_version ? `sürüm ${row.app_version}` : 'sürüm bildirilmedi') },
      { key: 'state', label: 'Durum', width: '170px',
        cell: (row) => badge(row.state_label, row.state_tone) },
      { key: 'printer', label: 'Donanım', width: 'minmax(0, 1.2fr)',
        cell: (row) => twoLine(row.printer_label, row.sound_label) },
      { key: 'queue', label: 'Kuyruk', width: '150px', align: 'num',
        cell: (row) => twoLine(
          `${num(row.queue_pending || 0)} bekliyor`,
          `${num(row.queue_failed || 0)} başarısız`) },
      { key: 'oldest', label: 'En eski iş', width: '130px', align: 'num',
        cell: (row) => (row.queue_oldest_age_minutes
          ? `${num(row.queue_oldest_age_minutes)} dk`
          : '—') },
      { key: 'last_seen_at', label: 'Son görülme', width: '170px',
        cell: (row) => whenCell(row.last_seen_at) },
      { key: 'open_event_count', label: 'Açık olay', width: '110px', align: 'num' },
    ],
    rows: state.devices,
    rowKey: (row) => String(row.device_id ?? ''),
    empty: emptyState({
      title: state.link.connected ? 'Kayıtlı kasa yok' : 'Liste okunamadı',
      text: state.link.connected
        ? 'Mutfak kasaları KDS Yönetimi ekranından eklenir.'
        : state.link.error,
    }),
  });
  body.append(table.node);
  nodes.body.replaceChildren(body);
}

/** Geçmiş sekmesi — zaman çizelgesi + yerel kayıt tablosu. */
function showHistory() {
  const body = h('div', 'sm-stack');

  body.append(hintBox(
    'Bu sekmedeki her şey YERELDİR ve sunucuda karşılığı YOKTUR. Geçidin '
    + 'kopması, imzanın reddedilmesi ve ucun henüz dağıtılmamış olması '
    + 'sunucuya hiç ulaşmaz; tam olarak bu yüzden burada tutuluyor. Aynı '
    + 'gözlem ikinci satır açmaz, tekrar sayısı artar.'));

  // ESKİDEN YENİYE: yolculuk yukarıdan aşağı okunur ve son satır "nereye
  // kadar geldik" sorusunu cevaplar (kit `timeline`).
  body.append(card('Olay geçmişi',
    timeline(state.history.map((item) => ({
      title: item.title,
      detail: item.detail,
      at: item.at ? `${stampIso(item.at)} · ${ago(item.at)}` : '',
      tone: item.tone,
    })), { emptyText: 'Henüz kayıtlı bir gözlem yok.' }),
    'Gözlemler ve bu ekrandan yapılan yazmalar tek akışta.'));

  body.append(nodes.localFilters.node);

  nodes.localTable.update({
    rows: state.local,
    empty: emptyState({
      title: 'Bu süzgece uyan yerel kayıt yok',
      text: 'Yerel kayıt her yoklamada dolar; ekran birkaç dakikadır açıksa '
        + 'satırlar burada belirir.',
    }),
  });
  body.append(card('Yerel kayıt', nodes.localTable.node,
    'Araştırma sonuçları ve ulaşılamayan istekler — satır silinmez.'));

  nodes.body.replaceChildren(body);
}

/** Düzeltme defteri sekmesi. */
function showRunbook() {
  const body = h('div', 'sm-stack');

  body.append(hintBox(
    'Defter "bu hata çıkınca ne yapıyoruz" sorusunun yazılı cevabıdır. '
    + 'Komutlar `bld.api` geçidinden geçer; kabuk erişimi gerektiren adımlar '
    + 'yazılabilir ama Kontrol Merkezi\'nden ÇALIŞTIRILAMAZ. Kayıt silinmez, '
    + 'pasifleştirilir.'));

  const table = dataTable({
    columns: [
      { key: 'title', label: 'Adım', width: 'minmax(0, 1.6fr)',
        cell: (row) => twoLine(row.title, row.description || row.key) },
      { key: 'channel', label: 'Kanal', width: '130px',
        cell: (row) => badge(row.channel_label, row.runnable ? 'info' : 'dim') },
      { key: 'action', label: 'Eylem', width: 'minmax(0, 1.2fr)',
        cell: (row) => twoLine(row.action_label,
          row.device_id ? `kasa #${row.device_id}` : '') },
      { key: 'enabled', label: 'Durum', width: '120px',
        cell: (row) => badge(row.enabled ? 'Etkin' : 'Pasif',
          row.enabled ? 'good' : 'dim') },
      { key: 'run', label: '', width: '250px',
        cell: (row) => runbookActions(row) },
    ],
    rows: state.runbook,
    rowKey: (row) => String(row.key ?? ''),
    empty: emptyState({
      title: 'Defter boş',
      text: 'İlk adımı aşağıdaki formdan ekleyin: "yazıcı arızasında test '
        + 'fişi bas" gibi tekrarlanan bir düzeltme, yazılı olduğunda '
        + 'nöbetteki herkes tarafından uygulanabilir.',
    }),
  });
  body.append(card('Düzeltme adımları', table.node));
  body.append(card('Yeni adım / güncelleme', runbookForm(),
    'Var olan bir anahtar yazılırsa kayıt güncellenir.'));

  nodes.body.replaceChildren(body);
}

function runbookActions(row) {
  const box = h('div', 'sm-actions');
  if (!row.enabled) {
    box.append(blockedButton('Çalıştır',
      'Kayıt pasifleştirilmiş. Çalıştırmadan önce formdan yeniden etkinleştirin.'));
  } else if (!row.runnable) {
    box.append(blockedButton('Çalıştır',
      `Bu adım ${row.channel_label.toLowerCase()}: kabuk erişimi gerektiren `
      + 'düzeltmeler için `ssh` platform yeteneği henüz yazılmadı. Adım '
      + 'burada yazılı duruyor ve sahada elle uygulanır.'));
  } else {
    box.append(writeButton('Prova', {
      title: 'Sunucunun ön denetimini çalıştırır; kasaya hiçbir şey gitmez.',
      onClick: () => runEntry(row, true),
    }));
    box.append(writeButton('Çalıştır', {
      variant: row.destructive ? 'danger' : 'primary',
      title: row.warning || 'Komutu kasaya gönderir.',
      onClick: () => runEntry(row, false),
    }));
  }
  return box;
}

function runbookForm() {
  const form = h('div', 'sm-form');
  const key = textField('Anahtar', 'ornek: yazici.test');
  const title = textField('Başlık', 'Yazıcı arızasında test fişi bas');
  const description = textField('Açıklama', 'Ne zaman ve neden uygulanır');

  const channel = selectField('Kanal',
    (state.contract?.channels || []).map((item) => ({ value: item.code, label: item.label })));
  const action = selectField('Eylem', [
    { value: state.contract?.manual_action || 'manual.note', label: 'Elle yapılır' },
    ...(state.contract?.actions || []).map((item) => ({
      value: item.code,
      label: item.destructive ? `${item.label} (yıkıcı)` : item.label,
    })),
  ]);
  const device = numberField('Kasa kimliği', 0, 0, 99999);
  const enabled = toggleField('Etkin', true);

  const note = h('div', 'sm-dim');
  const paint = () => {
    const spec = (state.contract?.actions || []).find((item) => item.code === action.value);
    note.textContent = spec?.warning
      ? `Yıkıcı: ${spec.warning}`
      : (channel.value === 'manual'
        ? 'Elle yapılan adım kaydedilir ama Kontrol Merkezi\'nden çalıştırılamaz.'
        : 'Komut geçitten gider ve denetim kaydına yazılır.');
  };
  channel.onChange(paint);
  action.onChange(paint);
  paint();

  form.append(key.node, title.node, description.node, channel.node, action.node,
    device.node, enabled.node, note);

  const actions = h('div', 'sm-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: () => saveEntry({
      key: key.value, title: title.value, description: description.value,
      channel: channel.value, action: action.value,
      device_id: device.value, enabled: enabled.value,
    }),
  }));
  form.append(actions);
  return form;
}

/** Ayar sekmesi — yoklama tercihi ve bu ekranın yerel yazma izi. */
function showSettings() {
  const body = h('div', 'sm-stack');

  const form = h('div', 'sm-form');
  const poll = numberField('Yoklama aralığı (saniye)',
    state.prefs?.poll_seconds || 60, 15, 600);
  const size = numberField('Sayfa boyutu', state.prefs?.page_size || 25, 5, 100);
  const auto = toggleField('Otomatik tazeleme', state.prefs?.auto_refresh !== false);
  form.append(poll.node, size.node, auto.node);
  form.append(h('div', 'sm-dim',
    'Yoklama aralığını kısaltmak paylaşılan sunucu bütçesini (3000 istek/saat) '
    + 'hızlandırılmış biçimde yakar ve ikinci bir yöneticinin ekranını 429 '
    + 'hatasına düşürür. Sekme gizliyken yoklama zaten durur.'));

  const actions = h('div', 'sm-actions');
  actions.append(button('Kaydet', {
    variant: 'primary',
    onClick: () => savePrefs({
      poll_seconds: poll.value, page_size: size.value, auto_refresh: auto.value,
    }),
  }));
  form.append(actions);
  body.append(card('Ekran ayarı', form, 'Bu ayarlar BLD\'yi etkilemez.'));

  const table = dataTable({
    columns: [
      { key: 'created_at', label: 'Ne zaman', width: '180px',
        cell: (row) => whenCell(row.created_at) },
      { key: 'action', label: 'İşlem', width: 'minmax(0, 1fr)',
        cell: (row) => twoLine(row.action, row.target_id) },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      { key: 'result', label: 'Sonuç', width: '140px',
        cell: (row) => badge(row.result, auditTone(row.result)) },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
    ],
    rows: state.audit,
    dense: true,
    empty: emptyState({
      title: 'Bu ekrandan henüz yazma yapılmadı',
      text: 'Olay çözme, defter kaydı ve komut gönderme burada iz bırakır.',
    }),
  });
  body.append(card('Yerel yazma izi', table.node,
    'Sunucuya ulaşmayan denemeler de burada durur — satır silinmez.'));

  nodes.body.replaceChildren(body);
}

function auditTone(result) {
  return ({ ok: 'good', dry_run: 'info', denendi: 'warn', engellendi: 'dim',
    hata: 'bad' })[result] || 'dim';
}

// ================================================================ çekmece

function openEvent(row) {
  const box = drawer(nodes.root, {
    title: `#${row.id} · ${row.code || 'kodsuz'}`,
    subtitle: `${row.source_label} · ${row.level_label}`,
  });

  const stack = h('div', 'sm-stack');
  stack.append(h('p', 'sm-message', row.message || '—'));

  const meta = h('div', 'sm-badges');
  meta.append(badge(row.level_label, row.level_tone));
  meta.append(badge(`${num(row.occurrence_count)} kez`, 'dim'));
  if (row.device_name) meta.append(badge(row.device_name, 'dim'));
  if (row.app_version) meta.append(badge(`sürüm ${row.app_version}`, 'dim'));
  stack.append(meta);

  const times = h('div', 'sm-grid');
  times.append(labelled('İlk görülme', whenCell(row.first_seen_at)));
  times.append(labelled('Son görülme', whenCell(row.last_seen_at)));
  stack.append(times);

  box.body.append(stack);
  box.body.append(h('div', 'sm-dim', 'Ayrıntı yükleniyor…'));

  (async () => {
    let detail = row;
    try {
      const payload = await call(`${BASE}/events/${row.id}`);
      detail = payload.event || row;
    } catch (failure) {
      box.body.append(alertBox(`Ayrıntı okunamadı: ${failure.message}`, 'bad'));
      return;
    }
    box.body.replaceChildren(stack);

    // `related` OLAYIN DEĞİL CİHAZIN ŞU ANKİ hâlidir: olay 05:12'de
    // kaydedildi, yönetici 09:00'da bakıyor ve asıl sorusu "hâlâ bozuk mu".
    const related = detail.related;
    if (related && typeof related === 'object') {
      const row2 = h('div', 'sm-badges');
      row2.append(badge(related.device_online ? 'Kasa çevrimiçi' : 'Kasa çevrimdışı',
        related.device_online ? 'good' : 'bad'));
      row2.append(badge(related.device_printer_ok ? 'Yazıcı çalışıyor' : 'Yazıcı arızalı',
        related.device_printer_ok ? 'good' : 'bad'));
      row2.append(badge(`Bekleyen: ${num(related.queue_pending || 0)}`, 'dim'));
      row2.append(badge(`Başarısız: ${num(related.queue_failed || 0)}`,
        related.queue_failed > 0 ? 'bad' : 'dim'));
      box.body.append(card('Cihazın ŞU ANKİ durumu', row2,
        'Olay kaydedildiği anın değil, şu anın fotoğrafı.'));
    }

    const context = detail.context;
    if (context && typeof context === 'object') {
      const list = h('div', 'sm-kv');
      for (const [key, value] of Object.entries(context)) {
        list.append(h('span', 'sm-kv-key', key));
        // `innerHTML` ASLA (kit kuralı 11): değer metin olarak yazılır.
        list.append(h('span', 'sm-kv-value',
          typeof value === 'object' ? JSON.stringify(value) : String(value)));
      }
      box.body.append(card('Bağlam', list,
        'Kişisel veri sunucuda kayıt anında maskelenir.'));
    }

    if (detail.resolved) {
      box.body.append(alertBox(
        `Bu olay ${detail.resolved_by_actor || 'bilinmeyen kişi'} tarafından `
        + `${stampIso(detail.resolved_at)} tarihinde çözüldü işaretlenmiş. `
        + `Not: ${detail.resolve_note || '—'}`, 'good'));
      box.body.append(hintBox(
        'Olay YENİDEN GELİRSE otomatik yeniden açılır ve bu not silinmez: '
        + '"geçen sefer ne yapılmıştı" bilgisi, aynı hatanın ikinci kez '
        + 'teşhisinde en kısa yoldur.'));
      return;
    }

    const actions = h('div', 'sm-actions');
    actions.append(writeButton('Çözüldü işaretle', {
      variant: 'primary',
      title: 'Gerekçe ister; kayıt SİLİNMEZ, yalnız açık listeden düşer.',
      onClick: () => resolveEvent(detail, box),
    }));
    box.body.append(actions);
  })();
}

function labelled(label, node) {
  const box = h('div', 'sm-field');
  box.append(h('span', 'sm-label', label));
  box.append(node);
  return box;
}

// ================================================================== yazma

async function resolveEvent(row, box) {
  const reason = await askReason({
    title: `#${row.id} çözüldü işaretlensin mi?`,
    description: 'Kayıt SİLİNMEZ: bir hata kaydını silmek, o hatanın hiç '
      + 'olmadığını iddia etmektir. Olay yalnız açık listeden düşer ve aynı '
      + 'hata tekrarlarsa kendiliğinden yeniden açılır. Gerekçe metni çözüm '
      + 'notu olarak sunucuya yazılır.',
    confirmLabel: 'Çözüldü işaretle',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Olay çözüldü işaretleniyor…', async () => {
    const result = await call(`${BASE}/events/${row.id}/resolve`, {
      method: 'POST',
      body: { reason, dryRun: false },
    });
    toast('Olay çözüldü işaretlendi.', 'good');
    box?.close();
    await Promise.all([loadEvents(), loadSummary()]);
    paintStatus();
    if (state.tab === 'events') showEvents();
    if (result?.dry_run) toast('Kuru prova: sunucuda hiçbir şey değişmedi.', 'info');
  });
}

async function runEntry(row, dryRun) {
  const uyari = row.warning ? `\n\nYIKICI: ${row.warning}` : '';
  const reason = await askReason({
    title: dryRun ? `${row.title} — prova` : row.title,
    description: dryRun
      ? 'Kuru prova sunucunun ön denetimlerini çalıştırır; kasaya hiçbir komut '
        + 'gitmez. Denetim kaydına yine bir satır yazılır.'
      : `"${row.action_label}" komutu kasa #${row.device_id} için kuyruğa `
        + `atılacak.${uyari}`,
    confirmLabel: dryRun ? 'Provayı çalıştır' : 'Komutu gönder',
    danger: !dryRun && row.destructive,
  });
  if (!reason) return;

  await withBusy(dryRun ? 'Prova çalıştırılıyor…' : 'Komut gönderiliyor…', async () => {
    const result = await call(`${BASE}/runbook/${encodeURIComponent(row.key)}/run`, {
      method: 'POST',
      body: { reason, dryRun },
    });
    toast(result?.dry_run
      ? 'Kuru prova geçti: kasaya hiçbir şey gönderilmedi.'
      : 'Komut kuyruğa atıldı; kasa bir sonraki yoklamasında alacak.',
    result?.dry_run ? 'info' : 'good');
    await loadAudit();
    nodes.status?.set(result?.dry_run ? 'Prova tamamlandı.' : 'Komut gönderildi.');
  });
}

async function saveEntry(values) {
  if (!values.key || !values.title) {
    toast('Anahtar ve başlık zorunlu.', 'bad');
    return;
  }
  const reason = await askReason({
    title: 'Defter kaydı yazılsın mı?',
    description: 'Bu tablo neyin ÇALIŞTIRILABİLECEĞİNİ tanımlıyor; kaydı kimin '
      + 'eklediği, komutu kimin çalıştırdığı kadar önemlidir. Var olan bir '
      + 'anahtar güncellenir, hiçbir satır silinmez.',
    confirmLabel: 'Kaydet',
    danger: false,
  });
  if (!reason) return;

  await withBusy('Defter kaydı yazılıyor…', async () => {
    // Gövde ALAN ALAN kurulur: uç `extra="forbid"` ile korunuyor ve `key`
    // yoldan geliyor. Formun tamamını yaymak, gövdeye ikinci bir `key`
    // koyup isteği 422'ye düşürürdü.
    await call(`${BASE}/runbook/${encodeURIComponent(values.key)}`, {
      method: 'PUT',
      body: {
        title: values.title,
        description: values.description,
        channel: values.channel,
        action: values.action,
        device_id: values.device_id,
        enabled: values.enabled,
        reason,
      },
    });
    toast('Defter kaydı yazıldı.', 'good');
    await Promise.all([loadRunbook(), loadAudit()]);
    if (state.tab === 'runbook') showRunbook();
  });
}

async function savePrefs(values) {
  await withBusy('Tercih kaydediliyor…', async () => {
    const result = await call(`${BASE}/prefs`, { method: 'PUT', body: values });
    state.prefs = result.prefs || state.prefs;
    state.size = state.prefs.page_size;
    toast('Ekran ayarı kaydedildi.', 'good');
    // Yoklama döngüsü YENİDEN KURULUR: aralık değiştiği hâlde eski döngü
    // dönmeye devam etseydi, ayar kaydedildiğini söyleyip hiçbir şey
    // değiştirmemiş olurdu.
    nodes.restartLive?.();
  });
}

// ============================================================ küçük alanlar

function textField(label, placeholder) {
  const box = h('div', 'sm-field');
  box.append(h('span', 'sm-label', label));
  const input = h('input', 'kit-input');
  input.type = 'text';
  input.placeholder = placeholder || '';
  box.append(input);
  return { node: box, get value() { return input.value.trim(); } };
}

function numberField(label, initial, min, max) {
  const box = h('div', 'sm-field');
  box.append(h('span', 'sm-label', label));
  const input = h('input', 'kit-input');
  input.type = 'number';
  input.min = String(min);
  input.max = String(max);
  input.value = String(initial ?? min);
  box.append(input);
  return {
    node: box,
    get value() {
      return Math.max(min, Math.min(max, Number(input.value) || min));
    },
  };
}

function selectField(label, options) {
  const box = h('div', 'sm-field');
  box.append(h('span', 'sm-label', label));
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', label);
  for (const option of options) {
    const item = h('option', undefined, option.label);
    item.value = String(option.value);
    select.append(item);
  }
  box.append(select);
  return {
    node: box,
    get value() { return select.value; },
    onChange(handler) { select.addEventListener('change', handler); },
  };
}

function toggleField(label, initial) {
  const box = h('label', 'sm-toggle');
  const input = h('input', 'kit-check');
  input.type = 'checkbox';
  input.checked = Boolean(initial);
  box.append(input, h('span', undefined, label));
  return { node: box, get value() { return input.checked; } };
}

// =================================================================== akış

function resetEventFilters() {
  // `reset()` kendi `onChange`ını tetikliyor ve sayfayı başa alan kod orada
  // duruyor; burada ikinci kez çağırmak aynı isteği iki kez atardı.
  nodes.eventFilters.reset?.();
}

async function syncEvents() {
  await loadEvents();
  paintStatus();
  if (state.tab === 'events') showEvents();
}

async function syncHealth() {
  await loadSummary();
  paintStatus();
  if (state.tab === 'health') showHealth();
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;
  state = { ...EMPTY_STATE };

  const view = h('div', 'kit-panel sm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  nodes.tabs = tabBar([
    { key: 'health', label: 'Durum' },
    { key: 'events', label: 'Hatalar' },
    { key: 'devices', label: 'Kasalar' },
    { key: 'history', label: 'Geçmiş' },
    { key: 'runbook', label: 'Düzeltme defteri' },
    { key: 'settings', label: 'Ayar ve iz' },
  ], 'health', (key) => showTab(key));

  // Süzgeç şeritleri sekmeyle birlikte YOK EDİLMEZ: `filterBar` global
  // dinleyici tutuyor ve her sekme geçişinde yenisini kurmak onları
  // biriktirirdi.
  nodes.eventFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '260px',
        placeholder: 'Mesaj ya da hata kodu' },
      { kind: 'select', key: 'source', label: 'Kaynak',
        options: [{ value: '', label: 'Hepsi' }] },
      { kind: 'select', key: 'level', label: 'Seviye',
        options: [{ value: '', label: 'Varsayılan (bilgi hariç)' }] },
      { kind: 'select', key: 'resolved', label: 'Çözüm',
        options: [{ value: '', label: 'Açık olanlar' }] },
      // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
      // `since` bir AN olduğu için hazır aralık kutusu kullanılıyor ve gün
      // `todayIso()` ile hesaplanıyor.
      { kind: 'select', key: 'since', label: 'Aralık', options: SINCE_PRESETS },
    ],
    onChange: (values) => {
      state.eventFilters = values;
      // Sunucuya giden bir süzgeç değiştiyse sayfa BAŞA döner: aksi hâlde
      // 6. sayfada duran kullanıcı boş bir tablo görür ve "hata yok" sanır.
      state.page = 1;
      syncEvents();
    },
    actions: [button('Yenile', { onClick: () => syncEvents() })],
  });
  state.eventFilters = nodes.eventFilters.values();

  nodes.localFilters = filterBar({
    fields: [
      { kind: 'search', key: 'q', width: '240px', placeholder: 'Mesaj ya da kod' },
      { kind: 'select', key: 'source', label: 'Kaynak',
        options: [{ value: '', label: 'Hepsi' }] },
      { kind: 'select', key: 'result', label: 'Sonuç',
        options: [{ value: '', label: 'Hepsi' }] },
      { kind: 'select', key: 'kind', label: 'Tür',
        options: [{ value: '', label: 'Hepsi' },
          { value: 'probe', label: 'Araştırma' },
          { value: 'fault', label: 'Ulaşılamadı' }] },
    ],
    onChange: async (values) => {
      state.localFilters = values;
      await loadLocal();
      if (state.tab === 'history') showHistory();
    },
    actions: [],
  });
  state.localFilters = nodes.localFilters.values();

  nodes.eventTable = dataTable({
    columns: [
      { key: 'level', label: 'Seviye', width: '110px',
        cell: (row) => badge(row.level_label, row.level_tone) },
      { key: 'source', label: 'Kaynak', width: '140px',
        cell: (row) => twoLine(row.source_label, row.device_name) },
      { key: 'message', label: 'Hata', width: 'minmax(0, 2.4fr)',
        cell: (row) => twoLine(row.message, row.code) },
      // TEKRAR SAYISI GÖRÜNÜR: 47 kez tekrarlanmış bir hata ile bir kez
      // görülmüş bir hata aynı aciliyette değildir.
      { key: 'occurrence_count', label: 'Tekrar', width: '90px', align: 'num',
        cell: (row) => num(row.occurrence_count) },
      { key: 'first_seen_at', label: 'İlk görülme', width: '170px',
        cell: (row) => whenCell(row.first_seen_at) },
      { key: 'last_seen_at', label: 'Son görülme', width: '170px',
        cell: (row) => whenCell(row.last_seen_at) },
    ],
    rows: [],
    onRow: (row) => openEvent(row),
  });

  nodes.localTable = dataTable({
    columns: [
      { key: 'kind', label: 'Tür', width: '120px',
        cell: (row) => badge(row.kind_label, row.kind === 'fault' ? 'warn' : 'dim') },
      { key: 'source', label: 'Kaynak', width: '150px', cell: (row) => row.source_label },
      { key: 'result', label: 'Görülen', width: '130px',
        cell: (row) => badge(row.result_label, row.result_tone) },
      { key: 'message', label: 'Not', width: 'minmax(0, 2fr)',
        cell: (row) => twoLine(row.message, row.code) },
      { key: 'occurrence_count', label: 'Tekrar', width: '90px', align: 'num',
        cell: (row) => num(row.occurrence_count) },
      { key: 'first_seen_at', label: 'İlk görülme', width: '170px',
        cell: (row) => whenCell(row.first_seen_at) },
      { key: 'last_seen_at', label: 'Son görülme', width: '170px',
        cell: (row) => whenCell(row.last_seen_at) },
    ],
    rows: [],
    dense: true,
  });

  nodes.pager = pager({
    total: 0, page: 1, size: state.size,
    onChange: ({ page, size }) => {
      state.page = page;
      state.size = size;
      syncEvents();
    },
  });

  nodes.status = statusLine();
  nodes.body = h('div', 'sm-body');

  const bar = h('div', 'sm-topbar');
  bar.append(nodes.tabs.node);
  view.append(bar, nodes.status.node, nodes.body);

  function showTab(key) {
    state.tab = key;
    // CANLI VERİ YALNIZ DURUM SEKMESİNDE: kapalı sekme için istek üretmek,
    // paylaşılan 3000/saat kovasını boşuna yakar.
    if (key === 'health') startLive(); else stopLive();
    ({
      health: showHealth,
      events: () => { showEvents(); syncEvents(); },
      devices: () => { showDevices(); loadDevices().then(showDevices); },
      history: () => {
        showHistory();
        Promise.all([loadHistory(), loadLocal()]).then(() => {
          if (state.tab === 'history') showHistory();
        });
      },
      runbook: () => {
        showRunbook();
        loadRunbook().then(() => { if (state.tab === 'runbook') showRunbook(); });
      },
      settings: () => {
        showSettings();
        loadAudit().then(() => { if (state.tab === 'settings') showSettings(); });
      },
    }[key] || showHealth)();
  }

  function startLive() {
    if (nodes.live) return;
    // TERCİH GELMEDEN DÖNGÜ KURULMAZ. Sekme açılışı sözleşme okumasından önce
    // gerçekleşiyor; burada varsayılanla başlamak, "otomatik tazeleme kapalı"
    // diyen bir kullanıcının ekranını yine de yoklatırdı.
    if (!state.prefs) return;
    if (state.prefs.auto_refresh === false) return;
    const every = Math.max(15, Number(state.prefs?.poll_seconds || 60)) * 1000;
    // `pollLoop` sekme gizliyken durur ve üst üste binmez.
    nodes.live = pollLoop({ every, run: () => syncHealth() });
  }

  function stopLive() {
    nodes.live?.stop();
    nodes.live = null;
  }

  nodes.restartLive = () => {
    if (state.tab !== 'health') return;
    stopLive();
    startLive();
  };

  root.replaceChildren(view);
  showTab('health');

  // Açılış: önce sözleşme (ağa çıkmaz), sonra özet. Sıra önemli — süzgeç
  // kutuları sözleşmeden dolar ve sözleşme gelmeden çizilen bir kutu,
  // sunucudaki kod listesinden ayrışmış olurdu.
  (async () => {
    await loadOverview();
    fillFilterOptions();
    nodes.pager.update({ size: state.size, page: 1, total: 0 });
    startLive();
    await syncHealth();
  })();

  function fillFilterOptions() {
    const contract = state.contract;
    if (!contract) return;
    nodes.eventFilters.options?.('source', [
      { value: '', label: 'Hepsi' },
      ...contract.sources.map((item) => ({ value: item.code, label: item.label })),
    ]);
    nodes.eventFilters.options?.('level', [
      { value: '', label: 'Varsayılan (bilgi hariç)' },
      ...contract.levels.map((item) => ({ value: item.code, label: item.label })),
    ]);
    nodes.eventFilters.options?.('resolved', contract.resolved_filters.map(
      (item) => ({ value: item.code === 'false' ? '' : item.code, label: item.label })));
    nodes.localFilters.options?.('source', [
      { value: '', label: 'Hepsi' },
      ...contract.sources.map((item) => ({ value: item.code, label: item.label })),
    ]);
    nodes.localFilters.options?.('result', [
      { value: '', label: 'Hepsi' },
      ...contract.health.map((item) => ({ value: item.code, label: item.label })),
    ]);
  }

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR (kit kuralı 4): `pollLoop` hem
  // zamanlayıcıyı hem `visibilitychange` dinleyicisini, iki `filterBar` ise
  // arama için tuttukları global dinleyicileri bırakır. Bırakılmazsa panel her
  // açılışta bir tane daha birikir ve kapalı bir ekran yoklamaya devam eder.
  return () => {
    stopLive();
    nodes.eventFilters?.destroy();
    nodes.localFilters?.destroy();
    nodes.restartLive = null;
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    busy = false;
  };
}
