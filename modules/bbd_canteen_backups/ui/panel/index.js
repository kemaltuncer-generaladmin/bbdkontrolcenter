// Kantin Yedekleri paneli.
//
// Sunucudaki yedekleri görür, elle yedek aldırır ve yedeği BU MAKİNEYE indirip
// sha256 ile doğrular. Sunucu tümden kaybolsa bile elde doğrulanmış bir kopya
// kalsın diye.
//
// GERİ YÜKLEME YOKTUR. Tek tıkla canlı veritabanının üzerine yazmak kabul
// edilemez risk; geri yükleme sunucuda elle yapılır. Ekran bunu açıkça söyler.

import { button, h, loadStyles, stampIso, toaster } from './kit.js';

loadStyles(import.meta.url);

let api = null;
let toast = null;
let state = null;
let busy = false;

const nodes = {};

const mb = (bytes) => `${(Number(bytes || 0) / 1048576).toFixed(1)} MB`;

function age(minutes) {
  if (minutes === null || minutes === undefined) return 'bilinmiyor';
  if (minutes < 60) return `${minutes} dakika önce`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} saat ${minutes % 60} dakika önce`;
  return `${Math.floor(hours / 24)} gün ${hours % 24} saat önce`;
}

// ------------------------------------------------------------------- veri

async function refresh() {
  setStatus('Sunucudan okunuyor…');
  try {
    state = await api('/api/bbd_canteen_backups/overview');
    setStatus(state.connected
      ? `Sunucuda ${state.summary.serverCount} yedek (${mb(state.summary.serverBytes)}) · `
        + `burada ${state.summary.localCount} doğrulanmış kopya (${mb(state.summary.localBytes)})`
      : `Kantine ulaşılamadı — ${state.error || 'bilinmeyen hata'}`, !state.connected);
  } catch (error) {
    state = null;
    setStatus(`Çekirdek hatası: ${error.message}`, true);
  }
  render();
}

// ------------------------------------------------------------------ çizim

function setStatus(text, bad = false) {
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

function render() {
  nodes.body.replaceChildren();
  if (!state) {
    nodes.body.append(h('div', 'bk-hint', 'Veri alınamadı.'));
    return;
  }

  if (state.supported === false) {
    nodes.body.append(h('div', 'bk-alert bad',
      'Sunucu SQLite kullanmıyor; bu ekranın yedek listesi çalışmaz. '
      + 'Yedekler sağlayıcının kendi planına göre alınır.'));
    return;
  }

  renderFreshness();
  renderServer();
  renderLocal();
}

/** Tazelik kartı — "yedek alınıyor mu" sorusunun tek bakışta cevabı. */
function renderFreshness() {
  const fresh = state.freshness || {};
  const card = h('section', `bk-fresh${fresh.stale ? ' stale' : ''}`);

  const left = h('div', 'bk-fresh-main');
  left.append(
    h('div', 'bk-fresh-label', 'Son yedek'),
    h('div', 'bk-fresh-value', age(fresh.ageMinutes)),
    h('div', 'bk-fresh-when', fresh.lastBackupAt
      ? new Date(fresh.lastBackupAt).toLocaleString('tr-TR')
      : 'Hiç yedek bulunamadı'),
  );

  const note = h('div', 'bk-fresh-note');
  note.textContent = fresh.stale
    ? `Yedek ${fresh.staleAfter} dakikadan eski. Sunucudaki zamanlayıcı durmuş `
      + 'ya da disk dolmuş olabilir — elle yedek alıp durumu denetleyin.'
    : `Sunucu düzenli yedek alıyor (eşik ${fresh.staleAfter} dakika).`;

  card.append(left, note, h('span', 'bk-spacer'));
  card.append(button('Şimdi yedek al', {
    variant: 'primary',
    title: 'Sunucuda anında tutarlı bir yedek oluşturur.',
    onClick: createBackup,
  }));
  nodes.body.append(card);

  nodes.body.append(h('div', 'bk-note',
    'GERİ YÜKLEME bu ekrandan yapılamaz. Tek tıkla canlı veritabanının üzerine '
    + 'yazmak kabul edilemez bir risk olduğu için böyle bir uç hiç eklenmedi; '
    + 'geri yükleme sunucuda elle yapılır.'));
}

function renderServer() {
  const rows = state.backups || [];
  const card = h('section', 'bk-card');
  const serverHead = h('div', 'bk-card-head');
  serverHead.append(
    h('h3', 'bk-card-title', `Sunucudaki yedekler (${rows.length})`),
    h('span', 'bk-card-hint', 'En yeni başta · sunucu 5 dakikada bir otomatik alır'),
  );
  card.append(serverHead);

  if (rows.length === 0) {
    card.append(h('div', 'bk-hint', 'Sunucuda yedek dosyası yok.'));
    nodes.body.append(card);
    return;
  }

  const table = h('div', 'bk-table');
  const head = h('div', 'bk-row bk-head');
  for (const label of ['Dosya', 'Boyut', 'Oluşturma', 'Özet (sha256)', 'Burada', '']) {
    head.append(h('span', 'bk-cell', label));
  }
  table.append(head);

  for (const item of rows) {
    const row = h('div', 'bk-row');
    const local = item.local;
    row.append(
      h('span', 'bk-cell mono', item.name),
      h('span', 'bk-cell num', mb(item.size)),
      h('span', 'bk-cell', new Date(item.createdAt).toLocaleString('tr-TR')),
      h('span', 'bk-cell mono dim', String(item.sha256 || '').slice(0, 16) + '…'),
    );

    const localCell = h('span', 'bk-cell');
    if (local && local.exists) {
      localCell.append(h('span', `bk-badge ${local.verified ? 'good' : 'bad'}`,
        local.verified ? 'doğrulandı' : 'ÖZET TUTMUYOR'));
    } else if (local) {
      localCell.append(h('span', 'bk-badge warn', 'dosya silinmiş'));
    } else {
      localCell.append(h('span', 'bk-badge dim', '—'));
    }
    row.append(localCell);

    const actions = h('span', 'bk-cell bk-actions');
    const get = h('button', 'bk-mini', local?.exists ? 'yeniden indir' : 'buraya indir');
    get.type = 'button';
    get.title = 'Dosyayı bu makineye indirir ve sha256 ile doğrular.';
    get.addEventListener('click', () => download(item.name));
    actions.append(get);
    row.append(actions);

    table.append(row);
  }
  card.append(table);
  nodes.body.append(card);
}

/** Doğrulama rozetini taşıyan hücre. */
function stateCell(verified) {
  const cell = h('span', 'bk-cell');
  cell.append(h('span', `bk-badge ${verified ? 'good' : 'bad'}`,
    verified ? 'doğrulandı' : 'ÖZET TUTMUYOR'));
  return cell;
}

function renderLocal() {
  const rows = (state.local || []).filter((row) => row.exists);
  const card = h('section', 'bk-card');
  const head = h('div', 'bk-card-head');
  head.append(
    h('h3', 'bk-card-title', `Bu makinedeki kopyalar (${rows.length})`),
    h('span', 'bk-card-hint', state.localDir),
    h('span', 'bk-spacer'),
    button('Kopyaları doğrula', {
      title: 'Yereldeki dosyaları yeniden özetleyip sessiz bozulma var mı bakar.',
      onClick: verify,
    }),
  );
  card.append(head);

  if (rows.length === 0) {
    card.append(h('div', 'bk-hint',
      'Bu makinede yedek kopyası yok. Yukarıdaki listeden “buraya indir” diyerek '
      + 'sunucudan bağımsız bir kopya tutabilirsiniz — sunucu tümden kaybolsa bile '
      + 'veriniz elinizde kalır.'));
    nodes.body.append(card);
    return;
  }

  const table = h('div', 'bk-table');
  const header = h('div', 'bk-row bk-head bk-row-local');
  for (const label of ['Dosya', 'Boyut', 'İndirme', 'Durum', 'Kullanıcı']) {
    header.append(h('span', 'bk-cell', label));
  }
  table.append(header);

  for (const row of rows) {
    const line = h('div', 'bk-row bk-row-local');
    line.append(
      h('span', 'bk-cell mono', row.name),
      h('span', 'bk-cell num', mb(row.size)),
      h('span', 'bk-cell', stampIso(row.downloaded_at)),
      stateCell(row.verified),
      h('span', 'bk-cell dim', row.downloaded_by || ''),
    );
    table.append(line);
  }
  card.append(table);
  card.append(h('div', 'bk-note',
    `En çok ${state.summary.keepLocal} kopya tutulur; fazlası indirme sırasına göre silinir.`));
  nodes.body.append(card);
}

// ------------------------------------------------------------- eylemler

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  setStatus(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    setStatus(error.message, true);
    return null;
  } finally {
    busy = false;
  }
}

async function createBackup() {
  await withBusy('Sunucuda yedek alınıyor…', async () => {
    const result = await api('/api/bbd_canteen_backups/create', { method: 'POST' });
    if (!result.ok) { toast(result.error || 'Yedek alınamadı.', 'bad'); return; }
    toast(`Yedek alındı: ${result.name} (${mb(result.size)})`, 'good');
    await refresh();
  });
}

async function download(name) {
  await withBusy(`${name} indiriliyor…`, async () => {
    const result = await api('/api/bbd_canteen_backups/download', {
      method: 'POST', body: { name },
    });
    if (!result.ok) { toast(result.error || 'İndirilemedi.', 'bad'); return; }
    toast(result.verified
      ? `İndirildi ve doğrulandı: ${name} (${mb(result.size)})`
      : `İndirildi ama sunucu özeti alınamadı: ${name}`,
      result.verified ? 'good' : 'warn');
    if ((result.pruned || []).length) {
      toast(`Eski kopya silindi: ${result.pruned.join(', ')}`, 'warn');
    }
    await refresh();
  });
}

async function verify() {
  await withBusy('Kopyalar doğrulanıyor…', async () => {
    const result = await api('/api/bbd_canteen_backups/verify', { method: 'POST' });
    const bad = result.corrupt + result.missing;
    toast(bad === 0
      ? `${result.valid} kopyanın hepsi sağlam.`
      : `${result.valid} sağlam, ${result.corrupt} bozuk, ${result.missing} eksik.`,
      bad === 0 ? 'good' : 'bad');
    await refresh();
  });
}

// ------------------------------------------------------------------ mount

export function mount(root, ctx) {
  api = ctx.api;

  const view = h('div', 'bk');
  toast = toaster(view);

  const bar = h('div', 'bk-bar');
  bar.append(
    h('span', 'bk-brand', 'Kantin Yedekleri'),
    h('span', 'bk-brand-note', 'sunucudaki yedekler ve buradaki doğrulanmış kopyalar'),
    h('span', 'bk-spacer'),
    button('Yenile', { onClick: refresh }),
  );
  nodes.status = h('div', 'bk-status');
  nodes.body = h('div', 'bk-body');

  view.append(bar, nodes.status, nodes.body);
  root.replaceChildren(view);

  refresh();

  return () => {
    root.replaceChildren();
    state = null;
    busy = false;
  };
}
