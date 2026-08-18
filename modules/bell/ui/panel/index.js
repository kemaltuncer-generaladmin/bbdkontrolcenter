// Zil Sistemi paneli.
//
// EKRANIN ANLATTIĞI ÜÇ ŞEY, BU SIRAYLA:
//
//   1. ÇALIYOR MU?  Ajan bağlı mı, sıradaki zil ne zaman, eksik ses var mı.
//      En üstte durur ve arıza varsa kırmızıdır. Çalmayan zil, fark
//      edilmeyen arızadır — ekran bunu saklarsa hata haftalarca yaşar.
//   2. ŞİMDİ NE YAPABİLİRİM?  Grup çağır, zil çal. Elle yapılan iş budur.
//   3. NASIL KURULUYOR?  Haftalık saatler, ses ve metinler.
//
// SESİ OLMAYAN DÜĞME ÇİZİLİR AMA KAPALIDIR ve nedeni yazar. Basılabilen ama
// sessizlikle biten bir "Çağır", hiç olmayandan kötüdür.
//
// Ortak bileşenler kabuktan gelir (ADR 0011). Yol, panelin KOPYALANMIŞ
// konumuna göredir: shell/panels/bell/ → shell/ui-kit/. Depoda
// '../../ui-kit/' çözülmez; normaldir.

import {
  ago, blockedButton, button, confirmSimple, h, loadStyles, toaster,
} from '../../ui-kit/kit.js';
import * as store from './store.js';

const DAYS = [
  { key: 'mon', name: 'Pazartesi', short: 'Pzt' },
  { key: 'tue', name: 'Salı', short: 'Sal' },
  { key: 'wed', name: 'Çarşamba', short: 'Çar' },
  { key: 'thu', name: 'Perşembe', short: 'Per' },
  { key: 'fri', name: 'Cuma', short: 'Cum' },
  { key: 'sat', name: 'Cumartesi', short: 'Cmt' },
  { key: 'sun', name: 'Pazar', short: 'Paz' },
];

/** Ajan bu süre görünmezse bağlı sayılmaz. Sorgulama 3 sn; 90 sn bolca pay. */
const AGENT_STALE_MS = 90_000;

/** Ekran kendini bu sıklıkta tazeler: ajan durumu ve ses kuyruğu canlı kalsın. */
const REFRESH_MS = 10_000;

let ctx = null;
let data = null;
let activeDay = DAYS[new Date().getDay() === 0 ? 6 : new Date().getDay() - 1].key;
let notify = () => {};
let timer = null;
let disposed = false;
const nodes = {};

/**
 * Kullanıcının yazdığını saate çevirir. Klavyeden hızlı girişi hedefler:
 *   "9" → 09:00 · "930" → 09:30 · "9:3" → 09:30 · "0930" → 09:30
 * Anlamlandıramazsa null döner ve ekran bunu söyler.
 */
export function parseTime(input) {
  const raw = String(input ?? '').trim();
  if (raw === '') return null;
  const digits = raw.replace(/\D/g, '');
  if (digits === '' || digits.length > 4) return null;

  let hours;
  let minutes;
  if (raw.includes(':') || raw.includes('.')) {
    const [left, right = ''] = raw.split(/[:.]/);
    hours = Number(left.replace(/\D/g, ''));
    minutes = right === '' ? 0 : Number(right.replace(/\D/g, '').padEnd(2, '0'));
  } else if (digits.length <= 2) {
    hours = Number(digits);
    minutes = 0;
  } else {
    hours = Number(digits.slice(0, digits.length - 2));
    minutes = Number(digits.slice(-2));
  }
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null;
  if (hours > 23 || minutes > 59) return null;
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`;
}

// --------------------------------------------------------------- yardımcı

async function run(action, { success = '', reload = true } = {}) {
  try {
    const result = await action();
    if (result && result.ok === false) {
      notify(result.detail || 'İşlem yapılamadı.', 'bad');
    } else if (success) {
      notify(typeof success === 'function' ? success(result) : success);
    }
    if (reload) await refresh();
    return result;
  } catch (error) {
    notify(error.message || String(error), 'bad');
    return null;
  }
}

async function refresh() {
  const next = await store.state();
  if (disposed) return;
  data = next;
  render();
}

function agentOnline() {
  const agent = data.agent || {};
  if (!agent.online) return false;
  const seen = Date.parse(agent.lastSeen || '');
  return !seen || Number.isNaN(seen) || Date.now() - seen < AGENT_STALE_MS;
}

/** Zil neden çalmaz? Boş dizi dönerse her şey yolunda. */
function blockers() {
  const list = [];
  const settings = data.settings || {};
  if (!settings.enabled) list.push('ana şalter kapalı');
  if (!Object.values(data.times || {}).some((items) => items.length)) {
    list.push('hiç zil saati girilmemiş');
  }
  if (!agentOnline() && !settings.playLocally) {
    list.push(data.agent?.error || 'zil ajanı görünmüyor');
  }
  if ((data.missingVoices || []).length) {
    list.push(`${data.missingVoices.length} grubun sesi hazır değil`);
  }
  return list;
}

// ------------------------------------------------------------------- şerit

function renderHeader() {
  const bar = h('header', 'bl-top');

  const master = h('label', 'bl-switch');
  const box = h('input');
  box.type = 'checkbox';
  box.checked = Boolean(data.settings.enabled);
  box.addEventListener('change', () => run(
    () => store.saveSettings({ ...data.settings, enabled: box.checked }),
    { success: box.checked ? 'Zil sistemi açıldı.' : 'Zil sistemi kapatıldı.' },
  ));
  master.append(box, h('i', 'bl-switch-track'), h('span', null, 'Zil sistemi'));

  const online = agentOnline();
  const agent = h('div', `bl-agent${online ? '' : ' off'}`);
  agent.append(h('i', 'bl-dot'));
  const seen = data.agent?.lastSeen ? ago(data.agent.lastSeen) : '';
  agent.append(h('span', null, online
    ? `Zil ajanı bağlı${seen ? ` · ${seen}` : ''}`
    : 'Zil ajanı görünmüyor'));
  if (data.agent?.error) agent.title = data.agent.error;

  // Ajanın yerelinde bulunması gereken dosyalar: teneffüs zili + her grup
  // için bir anons. Zilden sonra anons çalınmıyor, o ses de tutulmuyor.
  const wanted = 1 + (data.groups || []).length;
  const present = (data.agent?.sounds || []).length;
  const library = h('span', `bl-library${present >= wanted ? ' ok' : ''}`,
    online ? `Ajanda ${present}/${wanted} ses` : '');
  library.title = 'Sesler ajanın yerelinde durur; komut gelince indirme '
    + 'beklenmez. Eksik varsa arka planda iniyor demektir.';

  const ring = button('Şimdi zil çal', {
    variant: 'primary',
    title: 'Zili okulun hoparlöründen çalar. Anons çalmaz.',
    onClick: () => run(store.ring, { success: (r) => `Zil çaldı — ${r.detail}` }),
  });

  bar.append(master, agent, library, h('div', 'kit-spacer'), ring);
  return bar;
}

function renderStatus() {
  const problems = blockers();
  if (!problems.length) {
    const next = data.scheduler?.next?.[0];
    return h('p', 'bl-status',
      next ? `Sıradaki zil: ${next.label} · ${next.at.slice(11)}`
        : 'Zil etkin. Bugün için sırada zil yok.');
  }
  const line = h('p', 'bl-status bad');
  line.append(h('b', null, 'ZİL ÇALMAZ — '), h('span', null, `${problems.join(', ')}.`));
  return line;
}

// ------------------------------------------------------------------ gruplar

function renderGroups() {
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Grup çağır'));
  head.append(h('span', 'bl-hint',
    'Seçilen gruba anons geçer. Zil çalmaz.'));
  card.append(head);

  const grid = h('div', 'bl-groups');
  for (const group of data.groups || []) grid.append(groupCard(group));
  grid.append(newGroupCard());
  card.append(grid);

  if (!(data.groups || []).length) {
    card.append(h('p', 'bl-empty',
      'Henüz grup yok. Bir grup açtığında anons sesi bir kez üretilir; '
      + 'sonraki çağrılarda buluta çıkılmaz.'));
  }
  return card;
}

function groupCard(group) {
  const card = h('div', `bl-group bl-voice-${group.voice.state}`);

  const name = h('div', 'bl-group-name', group.name);
  name.title = group.text;
  card.append(name);

  const meta = h('div', 'bl-group-meta');
  meta.append(h('span', `bl-kind bl-kind-${group.kind}`,
    group.kind === 'ozel' ? 'özel ders' : 'grup'));
  meta.append(voiceBadge(group.voice));
  card.append(meta);

  const actions = h('div', 'bl-group-actions');
  if (group.voice.state === 'ready') {
    actions.append(button('Çağır', {
      variant: 'primary',
      title: group.text,
      onClick: () => run(() => store.callGroup(group.id),
        { success: `Anons geçildi: ${group.name}` }),
    }));
  } else {
    actions.append(blockedButton('Çağır', voiceReason(group.voice)));
  }

  const menu = h('div', 'bl-group-menu');
  const rename = h('button', 'bl-icon-btn', '✎');
  rename.type = 'button';
  rename.title = 'Adını değiştir';
  rename.addEventListener('click', () => startRename(name, group));

  const drop = h('button', 'bl-icon-btn', '✕');
  drop.type = 'button';
  drop.title = 'Grubu kaldır';
  drop.addEventListener('click', async () => {
    const yes = await confirmSimple(nodes.view, {
      title: `“${group.name}” kaldırılsın mı?`,
      description: 'Grup listeden çıkar. Kaydı ve üretilmiş sesi silinmez; '
        + 'aynı adla yeniden açarsan ses tekrar üretilmez.',
      confirmLabel: 'Kaldır',
      danger: true,
    });
    if (yes) await run(() => store.removeGroup(group.id), { success: 'Grup kaldırıldı.' });
  });
  menu.append(rename, drop);
  card.append(menu, actions);
  return card;
}

function voiceBadge(voice) {
  const map = {
    ready: ['✓ ses hazır', 'ok'],
    pending: ['⏳ ses üretiliyor', 'wait'],
    error: ['✕ ses üretilemedi', 'bad'],
    missing: ['⏳ sıraya alınacak', 'wait'],
  };
  const [text, tone] = map[voice.state] || map.missing;
  const badge = h('span', `bl-badge ${tone}`, text);
  if (voice.state === 'error') badge.title = voice.detail;
  return badge;
}

function voiceReason(voice) {
  if (voice.state === 'error') {
    return `Bu grubun anons sesi üretilemedi: ${voice.detail} `
      + 'Aşağıdaki “Sesleri yeniden üret” düğmesiyle yeniden denenebilir.';
  }
  return 'Bu grubun anons sesi henüz üretilmedi. Birkaç saniye içinde hazır olur.';
}

/**
 * Ad alanını yerinde düzenlemeye açar.
 *
 * Kartın tamamı değil YALNIZCA ad değiştirilir; rozet ve düğmeler yerinde
 * kalır. Bitince her hâlükârda `render()` çalışır — kaydedilsin ya da
 * vazgeçilsin, ekran tek doğru kaynaktan yeniden çizilir.
 */
function startRename(nameNode, group) {
  const field = h('input', 'kit-input');
  field.value = group.name;
  field.maxLength = 60;

  let closed = false;
  const finish = async (save) => {
    if (closed) return;           // blur + Enter birlikte tetiklenebilir
    closed = true;
    const next = field.value.trim();
    if (save && next && next !== group.name) {
      await run(() => store.renameGroup(group.id, next),
        { success: 'Grup adı değişti. Anons sesi yeniden üretiliyor.' });
    } else {
      render();
    }
  };

  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') finish(true);
    if (event.key === 'Escape') finish(false);
  });
  field.addEventListener('blur', () => finish(true));

  nameNode.replaceWith(field);
  field.focus();
  field.select();
}

function newGroupCard() {
  const card = h('div', 'bl-group bl-group-new');
  const field = h('input', 'kit-input');
  field.placeholder = 'Ad';
  field.maxLength = 60;

  // TÜR SEÇİMİ ZORUNLU GÖRÜNÜR, gizli bir varsayılan değil: cümle buna göre
  // kuruluyor ve yanlış seçim, tek öğrenciye "dersiniz başlıyor" diyen bir
  // anons üretir. Kullanıcı sesi duyana kadar bunu fark etmez.
  const kind = h('select', 'kit-select');
  for (const [value, label] of [['grup', 'Grup dersi'], ['ozel', 'Özel ders']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    kind.append(option);
  }
  kind.title = 'Grup dersi çoğul hitap eder ("dersiniz başlıyor"), '
    + 'özel ders tekil ("özel dersin başlıyor").';

  const submit = async () => {
    const name = field.value.trim();
    if (!name) return;
    field.value = '';
    await run(() => store.addGroup(name, kind.value),
      { success: `“${name}” eklendi. Anons sesi üretiliyor.` });
  };
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') submit();
  });

  card.append(h('div', 'bl-group-name', '+ Yeni'), field, kind,
    button('Ekle', { onClick: submit }));
  return card;
}

// -------------------------------------------------------------- zil saatleri

function renderTimes() {
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Zil saatleri'));
  head.append(h('span', 'bl-hint', 'Her hafta aynı saatlerde tekrar eder.'));
  card.append(head);

  const tabs = h('div', 'bl-tabs');
  for (const day of DAYS) {
    const count = (data.times[day.key] || []).length;
    const tab = h('button', `bl-tab${day.key === activeDay ? ' active' : ''}`);
    tab.type = 'button';
    tab.append(h('span', null, day.short));
    if (count) tab.append(h('em', 'bl-tab-count', String(count)));
    tab.addEventListener('click', () => { activeDay = day.key; render(); });
    tabs.append(tab);
  }
  card.append(tabs);

  const list = h('div', 'bl-times');
  const entries = data.times[activeDay] || [];
  for (const entry of entries) list.append(timeRow(entry));
  if (!entries.length) {
    list.append(h('p', 'bl-empty',
      `${DAYS.find((day) => day.key === activeDay).name} için zil saati yok.`));
  }
  card.append(list, timeForm());

  const copy = h('div', 'bl-copy');
  copy.append(h('span', 'bl-hint', 'Bu günü kopyala:'));
  for (const day of DAYS.filter((item) => item.key !== activeDay)) {
    const link = h('button', 'bl-link', day.short);
    link.type = 'button';
    link.title = `${DAYS.find((d) => d.key === activeDay).name} saatlerini `
      + `${day.name} gününe kopyalar (o günün mevcut saatlerinin üstüne yazar).`;
    link.addEventListener('click', () => copyDay(day.key));
    copy.append(link);
  }
  if (entries.length) card.append(copy);
  return card;
}

function timeRow(entry) {
  const row = h('div', 'bl-time');
  row.append(h('b', 'bl-time-clock', entry.time));

  const label = h('input', 'kit-input bl-time-label');
  label.value = entry.label || '';
  label.placeholder = 'teneffüs';
  label.maxLength = 40;
  label.addEventListener('change', () => {
    const next = cloneTimes();
    const found = next[activeDay].find((item) => item.time === entry.time);
    if (found) found.label = label.value.trim();
    run(() => store.saveTimes(next), { success: '' });
  });
  row.append(label);

  const drop = h('button', 'bl-icon-btn', '✕');
  drop.type = 'button';
  drop.title = 'Bu zili kaldır';
  drop.addEventListener('click', () => {
    const next = cloneTimes();
    next[activeDay] = next[activeDay].filter((item) => item.time !== entry.time);
    run(() => store.saveTimes(next), { success: `${entry.time} zili kaldırıldı.` });
  });
  row.append(drop);
  return row;
}

function timeForm() {
  const form = h('div', 'bl-time-form');
  const clock = h('input', 'kit-input');
  clock.placeholder = 'Saat (930 → 09:30)';
  const label = h('input', 'kit-input');
  label.placeholder = 'Etiket (isteğe bağlı)';
  label.maxLength = 40;

  const submit = () => {
    const parsed = parseTime(clock.value);
    if (parsed === null) {
      notify('Saat anlaşılmadı. Örnek: 930, 9:30, 0930.', 'bad');
      clock.focus();
      return;
    }
    const next = cloneTimes();
    if (next[activeDay].some((item) => item.time === parsed)) {
      notify(`${parsed} zaten var.`, 'bad');
      return;
    }
    next[activeDay].push({ time: parsed, label: label.value.trim() });
    clock.value = '';
    label.value = '';
    run(() => store.saveTimes(next), { success: `${parsed} zili eklendi.` });
    clock.focus();
  };
  clock.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });
  label.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });

  form.append(clock, label, button('Saat ekle', { onClick: submit }));
  return form;
}

function cloneTimes() {
  const next = {};
  for (const day of DAYS) {
    next[day.key] = (data.times[day.key] || []).map((item) => ({ ...item }));
  }
  return next;
}

async function copyDay(target) {
  const source = DAYS.find((day) => day.key === activeDay).name;
  const name = DAYS.find((day) => day.key === target).name;
  const yes = await confirmSimple(nodes.view, {
    title: `${source} saatleri ${name} gününe kopyalansın mı?`,
    description: `${name} gününde şu an ${(data.times[target] || []).length} zil var; `
      + 'hepsi bu listeyle değiştirilecek.',
    confirmLabel: 'Kopyala',
  });
  if (!yes) return;
  const next = cloneTimes();
  next[target] = next[activeDay].map((item) => ({ ...item }));
  await run(() => store.saveTimes(next), { success: `${name} gününe kopyalandı.` });
}

// ---------------------------------------------------------- ses ve metinler

function renderSound() {
  const card = h('section', 'bl-card bl-card-wide');
  card.append(h('h4', null, 'Ses ve metin'));

  // --- teneffüs zili ---
  const row = h('div', 'bl-row');
  row.append(h('label', 'bl-label', 'Teneffüs zili'));

  const select = h('select', 'kit-select');
  for (const item of data.sounds || []) {
    const option = document.createElement('option');
    option.value = item.stem;
    option.textContent = item.name;
    select.append(option);
  }
  select.value = data.settings.bellSound;
  select.addEventListener('change', () => run(
    () => store.saveSettings({ ...data.settings, bellSound: select.value }),
    { success: 'Zil sesi değişti. Ajana gönderiliyor.' },
  ));
  row.append(select);

  row.append(button('Dinle', {
    title: 'Yalnız bu bilgisayardan çalar; okula duyulmaz.',
    // SESİ KABUK ÇALAR (ADR 0026). Çekirdek sunucuda koşuyor ve orada
    // hoparlör yok; uç sesin kendisini base64 veri URI'si olarak veriyor.
    // `Audio` üç platformda da aynı şekilde çalışır.
    onClick: () => run(async () => {
      const result = await store.preview(select.value);
      if (!result?.ok || !result.dataUri) {
        return result || { ok: false, detail: 'Ses alınamadı.' };
      }
      try {
        await new Audio(result.dataUri).play();
      } catch (error) {
        return { ok: false, detail: `Ses çalınamadı: ${error?.message || error}` };
      }
      return { ok: true, detail: `${result.name} çalınıyor` };
    }, { reload: false }),
  }));

  const upload = h('input');
  upload.type = 'file';
  upload.accept = '.wav,.ogg,.mp3,.flac,audio/*';
  upload.hidden = true;
  upload.addEventListener('change', async () => {
    const file = upload.files?.[0];
    upload.value = '';
    if (!file) return;
    await run(() => store.uploadSound(file),
      { success: (r) => `“${r.name}” yüklendi. Zil sesi olarak seçebilirsin.` });
  });
  row.append(button('Dosya yükle', { onClick: () => upload.click() }), upload);
  card.append(row, volumeRow());

  if (!(data.sounds || []).length) {
    card.append(h('p', 'bl-empty',
      'Hiç zil sesi dosyası yok. “Dosya yükle” ile bir wav ekle.'));
  }

  card.append(h('hr', 'bl-rule'));

  // --- metinler ---
  // ZİLDEN SONRA ANONS YOK. Bir zamanlar burada "Zilden sonra" metin alanı
  // duruyordu; kaldırıldı (bkz. backend/service.py başlığı). Aşağıdaki iki
  // metin YALNIZ elle basılan çağrıda çalar, zil saatinde değil.
  const grup = (data.groups || []).filter((g) => g.kind !== 'ozel').length;
  const ozel = (data.groups || []).filter((g) => g.kind === 'ozel').length;

  card.append(textRow({
    key: 'call',
    label: 'Grup dersi çağrısı',
    hint: 'Çoğul hitap. {grup} yerine grubun adı yazılır. '
      + `Değiştirirsen ${grup} grubun sesi yeniden üretilir.`,
  }));
  card.append(textRow({
    key: 'solo',
    label: 'Özel ders çağrısı',
    hint: 'Tekil hitap — tek öğrenciyle yapılan ders. '
      + `Değiştirirsen ${ozel} öğrencinin sesi yeniden üretilir.`,
  }));

  const tools = h('div', 'bl-tools');
  tools.append(h('span', 'bl-hint', `Kuyrukta ${data.queue?.queued || 0} ses.`));
  tools.append(button('Sesleri yeniden üret', {
    title: 'Eksik ya da hata almış sesleri yeniden üretim sırasına alır.',
    onClick: () => run(store.rebuildVoices,
      { success: (r) => (r.queued ? `${r.queued} ses sıraya alındı.`
        : 'Eksik ses yok.') }),
  }));
  tools.append(button('Ajanı eşitle', {
    title: 'Ajanın yerelindeki ses kitaplığını hemen tazeler.',
    onClick: () => run(store.syncAgent,
      { success: (r) => (r.ok ? `${r.uploaded} ses gönderildi.` : r.detail) }),
  }));

  const local = h('label', 'bl-check');
  const box = h('input');
  box.type = 'checkbox';
  box.checked = Boolean(data.settings.playLocally);
  box.addEventListener('change', () => run(
    () => store.saveSettings({ ...data.settings, playLocally: box.checked }),
    { success: box.checked
      ? 'Bu bilgisayardan da çalacak.'
      : 'Yalnız okulun hoparlöründen çalacak.' },
  ));
  local.append(box, h('span', null, 'Bu bilgisayardan da çal'));
  local.title = 'Asıl hoparlör zil ajanındadır. Bu seçenek yedek ve deneme içindir.';

  tools.append(h('div', 'kit-spacer'), local);
  card.append(tools);
  return card;
}

function volumeRow() {
  const row = h('div', 'bl-row');
  row.append(h('label', 'bl-label', 'Ses düzeyi'));

  const range = h('input', 'bl-range');
  range.type = 'range';
  range.min = '0';
  range.max = '100';
  range.step = '5';
  range.value = String(data.settings.volume);
  range.setAttribute('aria-label', 'Ses düzeyi');

  const value = h('b', 'bl-volume-value', `%${data.settings.volume}`);
  range.addEventListener('input', () => { value.textContent = `%${range.value}`; });
  range.addEventListener('change', () => run(
    () => store.saveSettings({ ...data.settings, volume: Number(range.value) }),
    { success: '' },
  ));

  row.append(range, value);
  row.append(h('span', 'bl-hint',
    'Cihazın kendi ses ayarı ayrıca geçerlidir.'));
  return row;
}

function textRow({ key, label, hint }) {
  const wrap = h('div', 'bl-text');
  const head = h('div', 'bl-text-head');
  head.append(h('label', 'bl-label', label));
  wrap.append(head);

  const field = h('textarea', 'kit-textarea');
  field.value = data.settings.texts[key] || '';
  field.rows = 2;
  field.maxLength = 300;

  const save = button('Kaydet', {
    variant: 'primary',
    onClick: () => {
      const text = field.value.trim();
      if (text === (data.settings.texts[key] || '')) {
        notify('Metin değişmedi.');
        return;
      }
      run(() => store.saveSettings({
        ...data.settings,
        texts: { ...data.settings.texts, [key]: text },
      }), { success: 'Metin kaydedildi. Sesler yeniden üretiliyor.' });
    },
  });

  wrap.append(field, h('p', 'bl-hint', hint), save);
  return wrap;
}

// ------------------------------------------------------------------ günlük

function renderLog() {
  const card = h('section', 'bl-card bl-card-wide');
  card.append(h('h4', null, 'Günlük'));

  const rows = data.log || [];
  if (!rows.length) {
    card.append(h('p', 'bl-empty', 'Henüz çalma kaydı yok.'));
    return card;
  }

  const table = h('table', 'bl-table');
  const head = h('thead');
  const headRow = h('tr');
  for (const title of ['Zaman', 'Tür', 'Ne', 'Sonuç', 'Ayrıntı']) {
    headRow.append(h('th', null, title));
  }
  head.append(headRow);
  table.append(head);

  const body = h('tbody');
  const kinds = { zil: 'Zil', anons: 'Anons', cagri: 'Çağrı' };
  for (const row of rows.slice(0, 40)) {
    const line = h('tr', row.ok ? '' : 'bad');
    line.append(h('td', 'bl-time-cell', String(row.at || '').slice(11, 19)));
    line.append(h('td', null, kinds[row.kind] || row.kind || '—'));
    line.append(h('td', null, row.group_name || '—'));
    line.append(h('td', null, row.ok ? '✓' : '✕'));
    line.append(h('td', 'bl-detail', row.detail || ''));
    body.append(line);
  }
  table.append(body);
  card.append(table);
  return card;
}

// -------------------------------------------------------------------- çizim

function render() {
  if (!nodes.body) return;
  // Yeniden çizimde kaydırma konumu korunur: 10 saniyede bir tazelenen bir
  // ekranda listenin başa fırlaması, kullanıcıyı okuduğu yerden koparır.
  const scroll = nodes.body.scrollTop;

  const columns = h('div', 'bl-columns');
  columns.append(renderGroups(), renderTimes());

  nodes.head.replaceChildren(renderHeader(), renderStatus());
  nodes.body.replaceChildren(columns, renderSound(), renderLog());
  nodes.body.scrollTop = scroll;
}

export function mount(root, context) {
  ctx = context;
  disposed = false;
  data = null;
  loadStyles(import.meta.url);
  store.connect(ctx.api);

  // `kit-panel` paylaşılan renk jetonlarını taşır ve bildirim şeridinin
  // konumlandığı köktür; olmazsa uyarılar tüm pencereye taşar (ADR 0011).
  nodes.view = h('div', 'bl kit-panel');
  nodes.head = h('div', 'bl-head');
  nodes.body = h('div', 'bl-body kit-body');
  nodes.view.append(nodes.head, nodes.body);
  notify = toaster(nodes.view);

  const loading = h('div', 'bl-loading');
  loading.append(h('span', 'bl-spinner'), h('p', null, 'Zil sistemi okunuyor…'));
  root.replaceChildren(loading);

  (async () => {
    const first = await store.state();
    if (disposed) return;
    data = first;
    root.replaceChildren(nodes.view);
    render();
    // Ajan durumu ve ses kuyruğu canlı: ekran açıkken kendini tazeler.
    timer = window.setInterval(() => {
      refresh().catch(() => { /* geçici ağ hatası ekranı boşaltmasın */ });
    }, REFRESH_MS);
  })().catch((error) => {
    if (disposed) return;
    const box = h('div', 'bl-empty-state');
    box.append(h('h3', null, 'Zil sistemi açılamadı'));
    box.append(h('p', null, error?.message || String(error)));
    root.replaceChildren(box);
  });

  return () => {
    disposed = true;
    window.clearInterval(timer);
    root.replaceChildren();
  };
}

// ARAYÜZ TARAFINDA YETENEK SUNULMUYOR — bilerek.
//
// Manifestteki `provides: bell.week` BACKEND yeteneğidir: Ders Takvimi
// modülünün backend'i onu registry'den çözer ve kendi ucundan servis eder.
// Zincir Python içinde kapanır, ağa hiç çıkmaz.
//
// Burada bir `capabilities()` de sunulabilirdi ama tek yolu `/api/bell/state`
// çağırmaktı; o uç köprüye HTTP isteği atıyor (ajan durumu için). Ders
// takvimini görmek isteyen bir ekranın bbdstore'a gitmesi için hiçbir sebep
// yok. Kullanılmayan ama pahalı bir yol bırakmak, birinin onu bulup
// kullanmasını beklemekten ibarettir.
