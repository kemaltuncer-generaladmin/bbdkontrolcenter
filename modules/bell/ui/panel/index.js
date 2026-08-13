// Zil Sistemi paneli.
//
// Kurgu: zil kendi başına bir takvim TUTMAZ. Ders saatleri Ders Takvimi
// modülünündür; zil onları `bbd_class_schedule.week` yeteneğinden okur ve
// yalnızca "hangi grup, hangi derste, hangi sesi, hangi düzeyde" kararını
// saklar (K3 — modül modülü import etmez, K5 — kimse başkasının verisini
// kopyalamaz).
//
// Üç kademeli karar:
//   1. Ana şalter   — tüm zilleri tek yerden sustur (tören, deneme sınavı)
//   2. Grup ayarı   — sesi, düzeyi, ders başı/sonu kuralı
//   3. Ders istisnası — tek bir dersi sustur veya ona başka ses ver

import { BELLS, bellById, play, release, stop } from './sounds.js';
import { DEFAULT_SOUND, flush, init, load, overrideKey, save, settingsFor } from './store.js';

const DAYS = [
  { key: 'mon', name: 'Pazartesi' },
  { key: 'tue', name: 'Salı' },
  { key: 'wed', name: 'Çarşamba' },
  { key: 'thu', name: 'Perşembe' },
  { key: 'fri', name: 'Cuma' },
];

const VOLUME_PRESETS = [
  { label: 'Düşük', value: 40 },
  { label: 'Orta', value: 70 },
  { label: 'Yüksek', value: 95 },
];

let state = null;
let groups = [];        // ders takviminden gelen gruplar (salt okunur)
let activeGroupId = null;
const nodes = {};

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function activeGroup() {
  return groups.find((group) => group.id === activeGroupId) || groups[0] || null;
}

function activeSettings() {
  const group = activeGroup();
  return group ? settingsFor(state, group.id) : null;
}

function commit() {
  save(state);
}

/** Bir grubun haftada kaç kez zil çalacağı — ana şalter ve istisnalar dahil. */
function bellCount(group) {
  const settings = settingsFor(state, group.id);
  if (!state.enabled || !settings.enabled) return 0;

  let count = 0;
  for (const day of DAYS) {
    for (const block of group.week[day.key] || []) {
      const override = settings.overrides[overrideKey(day.key, block.start)] || {};
      if (settings.ringStart && override.start !== false) count += 1;
      if (settings.ringEnd && override.end !== false) count += 1;
    }
  }
  return count;
}

function effectiveSound(settings, override) {
  return bellById(override?.soundId || settings.soundId || DEFAULT_SOUND);
}

// ------------------------------------------------------------------- çizim

function renderGroups() {
  nodes.groups.replaceChildren();

  for (const group of groups) {
    const settings = settingsFor(state, group.id);
    const chip = h('button', 'bl-chip');
    chip.type = 'button';
    chip.style.setProperty('--chip', group.color || '#5b8cff');
    chip.classList.toggle('active', group.id === activeGroupId);
    chip.classList.toggle('muted', !settings.enabled || !state.enabled);

    chip.append(h('i', 'bl-chip-dot'), h('span', null, group.name));
    chip.append(h('em', 'bl-chip-count', `${bellCount(group)} zil`));

    chip.addEventListener('click', () => {
      activeGroupId = group.id;
      renderAll();
    });
    nodes.groups.append(chip);
  }
}

function renderSettings() {
  const group = activeGroup();
  const settings = activeSettings();
  if (!group) return;

  const off = !state.enabled || !settings.enabled;
  nodes.body.classList.toggle('off', off);

  nodes.groupName.textContent = group.name;
  nodes.groupSwitch.checked = settings.enabled;
  nodes.groupNote.textContent = !state.enabled
    ? 'Ana şalter kapalı — hiçbir grup çalmıyor.'
    : settings.enabled
      ? `Haftada ${bellCount(group)} zil`
      : 'Bu grup susturuldu.';

  for (const card of nodes.soundCards) {
    card.input.checked = card.id === settings.soundId;
    card.root.classList.toggle('active', card.id === settings.soundId);
  }

  nodes.volume.value = String(settings.volume);
  nodes.volumeValue.textContent = `%${settings.volume}`;
  for (const preset of nodes.volumePresets) {
    preset.button.classList.toggle('active', settings.volume === preset.value);
  }

  nodes.ringStart.checked = settings.ringStart;
  nodes.ringEnd.checked = settings.ringEnd;
}

function renderLessons() {
  const group = activeGroup();
  const settings = activeSettings();
  nodes.lessons.replaceChildren();
  if (!group) return;

  const total = DAYS.reduce((sum, day) => sum + (group.week[day.key] || []).length, 0);
  if (total === 0) {
    nodes.lessons.append(
      h('p', 'bl-empty', 'Bu grupta ders saati yok. Saatler Ders Takvimi ekranından girilir.'),
    );
    return;
  }

  const table = h('table', 'bl-table');
  const head = h('thead');
  const headRow = h('tr');
  for (const label of ['Gün', 'Ders', 'Saat', 'Başı', 'Sonu', 'Ses', '']) {
    headRow.append(h('th', null, label));
  }
  head.append(headRow);
  table.append(head);

  const body = h('tbody');
  for (const day of DAYS) {
    const blocks = group.week[day.key] || [];
    blocks.forEach((block, index) => {
      const key = overrideKey(day.key, block.start);
      const override = settings.overrides[key] || {};
      const row = h('tr');

      row.append(h('td', 'bl-day', index === 0 ? day.name : ''));
      row.append(h('td', 'bl-lesson', block.name || `${index + 1}. ders`));
      row.append(h('td', 'bl-time', `${block.start} – ${block.end}`));

      row.append(ringCell(key, override, 'start', settings));
      row.append(ringCell(key, override, 'end', settings));

      // Ses istisnası: boş bırakılırsa grubun sesi çalar.
      const soundCell = h('td');
      const select = h('select', 'bl-select');
      const inherit = document.createElement('option');
      inherit.value = '';
      inherit.textContent = `Grubun sesi (${bellById(settings.soundId).name})`;
      select.append(inherit);
      for (const bell of BELLS) {
        const option = document.createElement('option');
        option.value = bell.id;
        option.textContent = bell.name;
        select.append(option);
      }
      select.value = override.soundId || '';
      select.addEventListener('change', () => {
        const next = { ...override, soundId: select.value || null };
        setOverride(key, next);
        renderLessons();
      });
      soundCell.append(select);
      row.append(soundCell);

      const playCell = h('td');
      const listen = h('button', 'bl-row-play', '▶');
      listen.type = 'button';
      listen.title = 'Bu dersin zilini dinle';
      listen.addEventListener('click', () => {
        play(effectiveSound(settings, override).id, settings.volume);
      });
      playCell.append(listen);
      row.append(playCell);

      if (index === 0) row.classList.add('bl-day-first');
      body.append(row);
    });
  }

  table.append(body);
  nodes.lessons.append(table);
}

function ringCell(key, override, edge, settings) {
  const cell = h('td', 'bl-tick');
  const box = h('input');
  box.type = 'checkbox';
  const groupRule = edge === 'start' ? settings.ringStart : settings.ringEnd;
  box.checked = groupRule && override[edge] !== false;
  box.disabled = !groupRule;
  box.title = groupRule
    ? 'Bu ders için zili aç/kapat'
    : `Grup ayarında "ders ${edge === 'start' ? 'başı' : 'sonu'}" kapalı.`;
  box.addEventListener('change', () => {
    setOverride(key, { ...override, [edge]: box.checked ? undefined : false });
    renderGroups();
    renderSettings();
  });
  cell.append(box);
  return cell;
}

/** İstisna yazar; hepsi ön tanımlıysa kaydı siler — çöp birikmesin. */
function setOverride(key, next) {
  const settings = activeSettings();
  const clean = {};
  if (next.start === false) clean.start = false;
  if (next.end === false) clean.end = false;
  if (next.soundId) clean.soundId = next.soundId;

  if (Object.keys(clean).length === 0) delete settings.overrides[key];
  else settings.overrides[key] = clean;

  commit();
}

function renderAll() {
  renderGroups();
  renderSettings();
  renderLessons();
}

// ----------------------------------------------------------------- kurulum

function buildSoundCard(bell) {
  const root = h('label', 'bl-sound');
  const input = h('input');
  input.type = 'radio';
  input.name = 'bl-sound';
  input.value = bell.id;
  input.addEventListener('change', () => {
    const settings = activeSettings();
    settings.soundId = bell.id;
    commit();
    renderAll();
    play(bell.id, settings.volume);
  });

  const text = h('div', 'bl-sound-text');
  text.append(h('b', null, bell.name), h('span', null, bell.note));

  const listen = h('button', 'bl-play', '▶');
  listen.type = 'button';
  listen.title = 'Dinle';
  listen.addEventListener('click', (event) => {
    event.preventDefault();
    play(bell.id, activeSettings()?.volume ?? 85);
  });

  root.append(input, text, listen);
  nodes.soundCards.push({ id: bell.id, root, input });
  return root;
}

/** Zili gerçekten çalar — tarayıcı önizlemesi değil, hoparlör. */
async function ringForReal(ctx) {
  const settings = settingsFor(state, activeGroupId);
  say('Çalınıyor…');
  try {
    const result = await ctx.api('/api/bell/ring', {
      method: 'POST',
      body: { groupId: activeGroupId || '', sound: settings.soundId, volume: settings.volume },
    });
    say(result.ok
      ? `Çaldı: ${result.sound} · ${result.detail}`
      : `ÇALAMADI — ${result.detail}`, !result.ok);
  } catch (error) {
    say(`Çalamadı: ${error.message}`, true);
  }
}

/** Üstteki canlı durum satırı. */
function say(text, bad = false) {
  if (!nodes.status) return;
  nodes.status.textContent = text;
  nodes.status.classList.toggle('bad', bad);
}

/**
 * Paneli kurar.
 *
 * SIRA ÖNEMLİ: önce veri, sonra çizim. Eskiden tersiydi — ekran `groups`
 * henüz boşken çiziliyor, "önce ders takvimi" boş durumuna girip `return`
 * ediyordu; veri sonradan gelse bile ekran bir daha çizilmiyordu. Üstelik o
 * dal, çok aşağıda ayrı bir kapsamda tanımlı `readWeek`'e bakıyordu:
 * ReferenceError → kabuk paneli boşaltıyordu. İkisi de bu düzenle bitti.
 */
function buildView(root, ctx, readWeek) {
  const styleHref = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${styleHref}"]`)) {
    const style = h('link');
    style.rel = 'stylesheet';
    style.href = styleHref;
    document.head.append(style);
  }

  const view = h('div', 'bl');
  nodes.soundCards = [];
  nodes.volumePresets = [];

  // --- ana şalter -----------------------------------------------------
  const top = h('header', 'bl-top');
  const master = h('label', 'bl-switch');
  const masterBox = h('input');
  masterBox.type = 'checkbox';
  masterBox.checked = state.enabled;
  masterBox.addEventListener('change', () => {
    state.enabled = masterBox.checked;
    commit();
    renderAll();
  });
  master.append(masterBox, h('i', 'bl-switch-track'), h('span', null, 'Zil sistemi'));
  top.append(master);

  // Çalma durumu CANLI: zil gerçekten çalıyor mu, çalmıyorsa neden.
  // Ekranın bunu saklaması yanlış olurdu — çalmayan zil, fark edilmeyen arızadır.
  nodes.status = h('p', 'bl-status', 'Durum okunuyor…');
  top.append(nodes.status);

  // GERÇEK çalma: hoparlörden, çekirdeğin ses yeteneğiyle. Yandaki ▶ düğmeleri
  // yalnız tarayıcıda önizleme yapar — ikisi farklı şeydir, ayrı düğme gerekir.
  nodes.ringNow = h('button', 'bl-ring-now', 'Hoparlörden çal');
  nodes.ringNow.type = 'button';
  nodes.ringNow.title = 'Zili gerçekten çalar (çekirdeğin ses aygıtından) ve günlüğe yazar.';
  nodes.ringNow.addEventListener('click', () => ringForReal(ctx));
  top.append(nodes.ringNow);

  view.append(top);

  // --- gruplar --------------------------------------------------------
  nodes.groups = h('div', 'bl-groups');
  view.append(nodes.groups);

  // --- grup yoksa -----------------------------------------------------
  if (groups.length === 0) {
    const empty = h('div', 'bl-empty-state');
    empty.append(h('h3', null, 'Önce ders takvimi'));
    empty.append(h('p', null,
      readWeek
        ? 'Ders Takvimi ekranında henüz grup yok. Zil, ders saatlerini oradan '
          + 'okur; grup ve ders saati girildiğinde bu ekran kendiliğinden dolar.'
        : 'Ders Takvimi modülü çözümlenemedi. Zil, ders saatlerini o modülden '
          + 'okur; modül gelmeden zil kuralı girilemez.'));
    view.append(empty);
    root.replaceChildren(view);
    return;
  }

  // --- grup ayarları ---------------------------------------------------
  const body = h('div', 'bl-body');
  nodes.body = body;

  const headline = h('div', 'bl-headline');
  nodes.groupName = h('h3');
  nodes.groupNote = h('span', 'bl-note');

  const groupSwitch = h('label', 'bl-switch bl-switch-sm');
  nodes.groupSwitch = h('input');
  nodes.groupSwitch.type = 'checkbox';
  nodes.groupSwitch.addEventListener('change', () => {
    activeSettings().enabled = nodes.groupSwitch.checked;
    commit();
    renderAll();
  });
  groupSwitch.append(nodes.groupSwitch, h('i', 'bl-switch-track'), h('span', null, 'Bu grup çalsın'));

  headline.append(nodes.groupName, nodes.groupNote, h('div', 'bl-spacer'), groupSwitch);
  body.append(headline);

  const columns = h('div', 'bl-columns');

  // ses seçimi
  const sounds = h('section', 'bl-card');
  sounds.append(h('h4', null, 'Zil sesi'));
  const soundList = h('div', 'bl-sounds');
  for (const bell of BELLS) soundList.append(buildSoundCard(bell));
  sounds.append(soundList);
  columns.append(sounds);

  // düzey ve kurallar
  const rules = h('section', 'bl-card');
  rules.append(h('h4', null, 'Ses düzeyi'));

  const volumeRow = h('div', 'bl-volume');
  nodes.volume = h('input', 'bl-range');
  nodes.volume.type = 'range';
  nodes.volume.min = '0';
  nodes.volume.max = '100';
  nodes.volume.step = '5';
  nodes.volume.setAttribute('aria-label', 'Ses düzeyi');
  nodes.volume.addEventListener('input', () => {
    activeSettings().volume = Number(nodes.volume.value);
    commit();
    renderSettings();
  });
  nodes.volumeValue = h('b', 'bl-volume-value');
  volumeRow.append(nodes.volume, nodes.volumeValue);
  rules.append(volumeRow);

  const presets = h('div', 'bl-presets');
  for (const preset of VOLUME_PRESETS) {
    const button = h('button', 'bl-preset', preset.label);
    button.type = 'button';
    button.addEventListener('click', () => {
      const settings = activeSettings();
      settings.volume = preset.value;
      commit();
      renderSettings();
      play(settings.soundId, settings.volume);
    });
    presets.append(button);
    nodes.volumePresets.push({ value: preset.value, button });
  }

  const test = h('button', 'bl-preset bl-test', 'Dinle');
  test.type = 'button';
  test.addEventListener('click', () => {
    const settings = activeSettings();
    play(settings.soundId, settings.volume);
  });
  presets.append(test);
  rules.append(presets);

  rules.append(h('p', 'bl-hint',
    'Düzey bu grubun zilleri içindir. Cihazın kendi ses ayarı ayrıca geçerlidir.'));

  rules.append(h('h4', 'bl-h4-gap', 'Ne zaman çalsın'));
  const when = h('div', 'bl-checks');

  const startLabel = h('label', 'bl-check');
  nodes.ringStart = h('input');
  nodes.ringStart.type = 'checkbox';
  nodes.ringStart.addEventListener('change', () => {
    activeSettings().ringStart = nodes.ringStart.checked;
    commit();
    renderAll();
  });
  startLabel.append(nodes.ringStart, h('span', null, 'Ders başında'));

  const endLabel = h('label', 'bl-check');
  nodes.ringEnd = h('input');
  nodes.ringEnd.type = 'checkbox';
  nodes.ringEnd.addEventListener('change', () => {
    activeSettings().ringEnd = nodes.ringEnd.checked;
    commit();
    renderAll();
  });
  endLabel.append(nodes.ringEnd, h('span', null, 'Ders bitiminde'));

  when.append(startLabel, endLabel);
  rules.append(when);
  rules.append(h('p', 'bl-hint',
    'Tek bir dersi susturmak ya da ona başka ses vermek için alttaki listeyi kullanın.'));

  columns.append(rules);
  body.append(columns);

  // --- ders listesi ----------------------------------------------------
  const lessons = h('section', 'bl-card bl-card-wide');
  lessons.append(h('h4', null, 'Dersler'));
  nodes.lessons = h('div');
  lessons.append(nodes.lessons);
  body.append(lessons);

  view.append(body);
  root.replaceChildren(view);
  renderAll();
}

export function mount(root, ctx) {
  groups = [];
  activeGroupId = null;
  state = load();

  // Veri gelene kadar ekran boş kalmasın; "boş ekran" ile "yükleniyor" farkı
  // kullanıcı için hatanın var olup olmadığı farkıdır.
  root.replaceChildren(loading());

  let disposed = false;

  (async () => {
    const result = await init(ctx.api);

    // `bbd_class_schedule.week` ASENKRON: takvim de kalıcı depoda, yetenek
    // çağrısı ağ üzerinden dönüyor.
    const readWeek = ctx.capability('bbd_class_schedule.week');
    if (readWeek) {
      try {
        groups = await readWeek();
      } catch (error) {
        console.warn('ders takvimi okunamadı', error);
        groups = [];
      }
    }

    // Panel bu arada kapatılmış olabilir; kapalı ekrana çizmeyiz.
    if (disposed) return;

    activeGroupId = groups[0]?.id ?? null;
    state = load();
    buildView(root, ctx, readWeek);

    if (result.migrated) {
      say('Zil ayarı tarayıcı belleğinden kalıcı depoya taşındı.');
    } else if (result.error) {
      say(`Zil ayarı okunamadı: ${result.error}`, true);
    }

    // Ses aygıtı ya da zamanlayıcı yoksa zil ÇALMAZ; ekran bunu saklamamalı.
    const status = result.state;
    if (status?.ready) {
      const next = status.scheduler?.next?.[0];
      say(`Zil etkin · ${status.sounds?.length || 0} ses · `
        + (next ? `sıradaki: ${next.label} (${next.at.slice(11)})` : 'sırada zil yok'));
    }
    if (status && !status.ready) {
      const missing = [];
      if (!status.audio?.ready) missing.push('ses aygıtı bulunamadı');
      if (!status.scheduler?.running) missing.push('zamanlayıcı çalışmıyor');
      if (!(status.groups || []).length) missing.push('ders takvimi boş');
      if (!status.settings?.enabled) missing.push('ana şalter kapalı');
      if (missing.length) say(`ZİL ÇALMAZ — ${missing.join(', ')}.`, true);
    }
  })().catch((error) => {
    // Buraya düşmek beklenmez; düşerse ekran yine boş kalmamalı.
    console.error('zil paneli kurulamadı', error);
    if (!disposed) root.replaceChildren(failure(error));
  });

  return () => {
    disposed = true;
    stop();
    release();
    flush();
    root.replaceChildren();
  };
}

/** Veri beklenirken görünen ara ekran. */
function loading() {
  const box = h('div', 'bl-loading');
  box.append(h('span', 'bl-spinner'), h('p', null, 'Zil ayarı ve ders saatleri okunuyor…'));
  return box;
}

/** Kurulum tümden başarısızsa görünen kart — boş ekran yerine. */
function failure(error) {
  const box = h('div', 'bl-empty-state');
  box.append(h('h3', null, 'Zil sistemi açılamadı'));
  box.append(h('p', null, error?.message || String(error)));
  return box;
}
