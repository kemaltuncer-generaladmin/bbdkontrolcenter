// Antivirüs paneli — iki düğme, bir ilerleme, bir sonuç, bir imza durumu.
//
// EKRAN BİLEREK KÜÇÜK. İstenen şey "çok basit bir ekran"dı: Tam Tarama ve
// Hızlı Tarama düğmeleri, ilerleme, son tarama sonucu ve imza durumu.
// Geçmiş tablosu, karantina listesi ve ayar formu BURADA YOK — tarama takvimi
// ve yollar Sistem Ayarları'ndaki kendi sekmesindedir (ADR 0018), bir iş
// eyleminin tek evi olur.
//
// ÜÇ DURUM, ÜÇ AYRI CÜMLE. "ClamAV kurulu değil", "imzalar hazırlanıyor" ve
// "tarama yapılamıyor" birbirinden ayrı yazılır; kullanıcının yapacağı iş
// üçünde de farklıdır. İlk kurulumda freshclam ~300 MB indirir ve bu bitmeden
// clamd başlamaz — bu bir arıza değildir, ekran onu "hazırlanıyor" olarak
// gösterir (ADR 0009).
//
// "TEMİZ" DAR BİR SÖZDÜR. Atlanan yol varken tarama temiz gösterilmez; ekran
// bunu saklamaz, tam olarak neyin atlandığını yazar (ADR 0009 §4 — bağlayıcı).
//
// Ortak bileşenler kabuktan gelir (ADR 0011). Import yolu panelin KOPYALANMIŞ
// konumuna göredir: shell/panels/antivirus/ → shell/ui-kit/. Kaynak dosya
// modules/antivirus/ui/panel/ altındayken bu yol dosya sisteminde ÇÖZÜLMEZ;
// `tools/build-ui-registry.py` paneli kopyaladıktan sonra çözülür.

import { blockedButton, button, h, loadStyles, num, pollLoop, stampIso, toaster }
  from '../../ui-kit/kit.js';
import { alertBox, badge, card, emptyState, kpiRow } from '../../ui-kit/layout.js';

/** Tarama sürerken sık, boştayken seyrek yoklanır. */
const POLL_ACTIVE = 2000;
const POLL_IDLE = 10000;

const VERDICTS = {
  clean: { label: 'Temiz', tone: 'good' },
  incomplete: { label: 'Eksik tarama', tone: 'warn' },
  infected: { label: 'Bulaşma var', tone: 'bad' },
  failed: { label: 'Başarısız', tone: 'bad' },
};

const ENGINE_STATES = {
  ready: { label: 'Hazır', tone: 'good' },
  preparing: { label: 'Hazırlanıyor', tone: 'warn' },
  missing: { label: 'Kurulu değil', tone: 'bad' },
  unavailable: { label: 'Çalışmıyor', tone: 'bad' },
};

/** Saniye → "4 dk 12 sn". Tarama süreleri saatler sürebilir. */
function duration(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} sn`;
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} dk ${total % 60} sn`;
  return `${Math.floor(minutes / 60)} sa ${minutes % 60} dk`;
}

function ageText(signatures) {
  if (!signatures.known) return 'bilinmiyor';
  const hours = Number(signatures.ageHours) || 0;
  if (hours < 1) return 'bir saatten yeni';
  if (hours < 48) return `${Math.round(hours)} saat`;
  return `${Math.round(hours / 24)} gün`;
}

// ------------------------------------------------------------------- motor

function renderEngine(data) {
  const engine = data.engine || {};
  const meta = ENGINE_STATES[engine.state] || ENGINE_STATES.unavailable;

  const head = h('div', 'av-line');
  head.append(badge(meta.label, meta.tone));
  if (engine.engine) head.append(h('span', 'av-dim', `Motor: ${engine.engine}`));
  if (engine.state === 'ready' && !engine.daemon) {
    head.append(h('span', 'av-dim', 'clamd kapalı — yedek yol'));
  }

  const body = h('div', 'av-stack');
  body.append(head);
  if (engine.note) {
    body.append(alertBox(engine.note, engine.state === 'preparing' ? 'info' : 'bad'));
  }
  return card('Motor', body);
}

// ------------------------------------------------------------------ eylem

function renderActions(data, actions) {
  const engine = data.engine || {};
  const active = data.active;
  const row = h('div', 'av-actions');

  if (active) {
    row.append(button('Durdur', {
      variant: 'danger',
      disabled: Boolean(active.stopping),
      onClick: actions.cancel,
    }));
    row.append(h('span', 'av-dim',
      active.kind === 'full' ? 'Tam tarama sürüyor.' : 'Hızlı tarama sürüyor.'));
    return row;
  }

  if (!engine.ready) {
    // Kapalı düğme NEDENİNİ söyler; ham bir 503 metni kimseye bir şey
    // anlatmaz (ui-kit kuralı).
    const reason = engine.note || 'Antivirüs motoru hazır değil.';
    row.append(blockedButton('Tam Tarama', reason, { variant: 'primary' }));
    row.append(blockedButton('Hızlı Tarama', reason));
    return row;
  }

  row.append(button('Tam Tarama', { variant: 'primary', onClick: () => actions.scan('full') }));
  row.append(button('Hızlı Tarama', { onClick: () => actions.scan('quick') }));
  return row;
}

/**
 * İLERLEME ÇUBUĞU YÜZDE GÖSTERMEZ. Toplam dosya sayısı taramadan önce
 * bilinemez; uydurulmuş bir yüzde, bittiğini sanan kullanıcı demektir.
 * Gösterilen şey gerçek: kaç dosya tarandı ve ne kadar süredir sürüyor.
 */
function renderProgress(active) {
  const box = h('div', 'av-progress');
  const track = h('div', 'av-bar');
  track.append(h('div', 'av-bar-run'));
  box.append(track);
  box.append(h('div', 'av-progress-text',
    `${num(active.files)} dosya tarandı · ${duration(active.seconds)}`));
  box.append(h('div', 'av-dim', `Yollar: ${(active.paths || []).join(', ')}`));
  return card('Tarama sürüyor', box);
}

// ------------------------------------------------------------------ sonuç

function renderSkipped(last) {
  const box = h('div', 'av-stack');
  box.append(alertBox(
    `Bu tarama "temiz" sayılamaz: ${num(last.skippedCount)} yol taranamadı. `
    + 'Erişilemeyen bir yolun içinde ne olduğu bilinmiyor.', 'warn'));

  const list = h('ul', 'av-list');
  for (const entry of (last.skipped || []).filter((item) => item.blocking)) {
    const row = h('li');
    row.append(h('code', null, entry.path));
    row.append(h('span', 'av-dim', entry.reason));
    list.append(row);
  }
  if (list.childElementCount) box.append(list);
  if (last.skippedCount > (last.skipped || []).length) {
    box.append(h('p', 'av-dim', 'Liste kısaltıldı; sayaç tamdır.'));
  }
  return box;
}

function renderThreats(last) {
  const box = h('div', 'av-stack');
  box.append(alertBox(`${num(last.threatCount)} bulaşmış dosya bulundu.`, 'bad'));
  const list = h('ul', 'av-list');
  for (const threat of last.threats || []) {
    const row = h('li');
    row.append(h('code', null, threat.path));
    row.append(h('span', 'av-bad', threat.name));
    list.append(row);
  }
  if (list.childElementCount) box.append(list);
  box.append(h('p', 'av-dim',
    'Karantina ve kalıcı silme bu sürümde yoktur; dosyalar yerinde bırakıldı.'));
  return box;
}

function renderLast(data) {
  const last = data.last;
  if (!last) {
    return card('Son tarama', emptyState({
      title: 'Henüz tarama yapılmadı',
      text: 'Yukarıdaki düğmelerden biriyle ilk taramayı başlatabilirsiniz.',
    }));
  }

  const meta = VERDICTS[last.verdict] || VERDICTS.failed;
  const body = h('div', 'av-stack');

  const head = h('div', 'av-line');
  head.append(badge(meta.label, meta.tone));
  head.append(h('span', 'av-dim',
    last.kind === 'full' ? 'Tam tarama' : 'Hızlı tarama'));
  head.append(h('span', 'av-dim', stampIso(last.finishedAt || last.startedAt)));
  if (last.actor) head.append(h('span', 'av-dim', last.actor));
  body.append(head);

  body.append(kpiRow([
    { label: 'Taranan dosya', value: num(last.files) },
    { label: 'Tehdit', value: num(last.threatCount), tone: last.threatCount ? 'bad' : '' },
    { label: 'Atlanan yol', value: num(last.skippedCount), tone: last.skippedCount ? 'warn' : '' },
    { label: 'Süre', value: duration(last.seconds) },
  ]));

  if (last.error) body.append(alertBox(last.error, 'bad'));
  if (last.threatCount) body.append(renderThreats(last));
  if (last.skippedCount) body.append(renderSkipped(last));
  if (last.excludedCount) {
    body.append(h('p', 'av-dim',
      `${num(last.excludedCount)} yol ayardaki hariç tutma listesi yüzünden taranmadı. `
      + 'Bu bir eksiklik değil, ilan edilmiş bir karardır.'));
  }
  return card('Son tarama', body);
}

// ------------------------------------------------------------------- imza

function renderSignatures(data) {
  const signatures = data.signatures || {};
  const body = h('div', 'av-stack');

  const head = h('div', 'av-line');
  if (!signatures.known) head.append(badge('Bilinmiyor', 'dim'));
  else head.append(badge(signatures.stale ? 'Eski' : 'Güncel', signatures.stale ? 'warn' : 'good'));
  head.append(h('span', 'av-dim', `Yaş: ${ageText(signatures)}`));
  head.append(h('span', 'av-dim', `Eşik: ${num(signatures.thresholdHours)} saat`));
  body.append(head);

  if (signatures.updatedAt) {
    body.append(h('p', 'av-dim', `Son güncelleme: ${stampIso(signatures.updatedAt)}`));
  }
  if (signatures.reason) body.append(h('p', 'av-dim', signatures.reason));
  body.append(h('p', 'av-dim',
    'İmzaları clamav-freshclam servisi günceller; bu ekrandan elle güncelleme '
    + 'yapılmaz (kilit çakışması yaratır).'));
  return card('İmzalar', body);
}

// ------------------------------------------------------------------- mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);
  let disposed = false;
  let loop = null;
  let period = 0;

  const view = h('div', 'kit-panel av');
  const body = h('div', 'av-body kit-body');
  view.append(body);
  root.replaceChildren(view);
  const toast = toaster(view);

  const draw = (data) => {
    const actions = {
      scan: (kind) => start(kind),
      cancel: () => stop(),
    };
    const blocks = [renderEngine(data), renderActions(data, actions)];
    if (data.active) blocks.push(renderProgress(data.active));
    blocks.push(renderLast(data), renderSignatures(data));
    body.replaceChildren(...blocks);
  };

  const refresh = async () => {
    const data = await ctx.api('/api/antivirus/state');
    if (disposed) return;
    draw(data);
    retune(data.active ? POLL_ACTIVE : POLL_IDLE);
  };

  /** Aralık değiştiyse döngüyü yeniden kurar — sürerken sık, boştayken seyrek. */
  function retune(next) {
    if (disposed || next === period) return;
    period = next;
    loop?.stop();
    loop = pollLoop({ every: next, run: refresh });
  }

  async function start(kind) {
    try {
      await ctx.api('/api/antivirus/scan', { method: 'POST', body: { kind } });
      toast(kind === 'full' ? 'Tam tarama başladı.' : 'Hızlı tarama başladı.');
    } catch (error) {
      toast(error?.message || 'Tarama başlatılamadı.', 'bad');
    }
    await refresh().catch(() => {});
  }

  async function stop() {
    try {
      const result = await ctx.api('/api/antivirus/scan/cancel', { method: 'POST' });
      toast(result?.detail || 'Tarama durduruluyor.');
    } catch (error) {
      toast(error?.message || 'Tarama durdurulamadı.', 'bad');
    }
    await refresh().catch(() => {});
  }

  body.replaceChildren(h('p', 'av-dim', 'Antivirüs durumu okunuyor…'));
  refresh().catch((error) => {
    if (disposed) return;
    body.replaceChildren(emptyState({
      title: 'Antivirüs açılamadı',
      text: error?.message || String(error),
    }));
    retune(POLL_IDLE);
  });

  return () => {
    disposed = true;
    loop?.stop();
    root.replaceChildren();
  };
}
