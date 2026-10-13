// Zil Sistemi — "Program" sekmesi: haftalık zil programı tek bakışta, gün
// düzenleyici, önümüzdeki 7 gün ve son çalmalar.
//
// KULLANICI (13.10.2026): "Görünüm, program, zil saatlerinin düzeltilmesi —
// bilgisayara dönüşü yok. Saçma sapan bir şeye dönmüş." Eski ekranda saatler
// gün sekmelerinin arkasındaydı, her değişiklik (tür, etiket, sil) ANINDA ayrı
// ayrı kaydediliyor ve ekran her 10 saniyede yeniden çiziliyordu; Akademi'nin
// tatil günleri ayrı bir sekmede, günlük saatleri UTC gösteriyordu.
//
// ŞİMDİ: hafta yedi sütun hâlinde görünür; bir gün "Düzenle" ile çekmecede
// açılır, değişiklikler TOPLU kaydedilir (ekle, sil, kaydır, başka günlere
// kopyala). Kaydedilen her saat zil bilgisayarına aynı kuralla gider — ekranda
// görünen "sıradaki zil" tetik tablosunun kendisinden gelir (backend
// `schedule_view`).
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).

import { button, confirmSimple, h } from '../../ui-kit/kit.js';
import { drawer } from '../../ui-kit/layout.js';

export const DAYS = [
  { key: 'mon', name: 'Pazartesi', short: 'Pzt' },
  { key: 'tue', name: 'Salı', short: 'Sal' },
  { key: 'wed', name: 'Çarşamba', short: 'Çar' },
  { key: 'thu', name: 'Perşembe', short: 'Per' },
  { key: 'fri', name: 'Cuma', short: 'Cum' },
  { key: 'sat', name: 'Cumartesi', short: 'Cmt' },
  { key: 'sun', name: 'Pazar', short: 'Paz' },
];

export const KIND_LABEL = { ders: 'Ders', teneffus: 'Teneffüs' };

const ZONE = 'Europe/Istanbul';

/**
 * Kullanıcının yazdığını saate çevirir. Klavyeden hızlı girişi hedefler:
 *   "9" → 09:00 · "930" → 09:30 · "9:3" → 09:30 · "0930" → 09:30 · "9.30" → 09:30
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

/** "08:40" + 5 → "08:45". Gün sınırını aşarsa null (zil bir gün öteye kaymaz). */
export function shiftTime(value, minutes) {
  const parsed = parseTime(value);
  if (parsed === null) return null;
  const [hour, minute] = parsed.split(':').map(Number);
  const total = hour * 60 + minute + Number(minutes || 0);
  if (total < 0 || total >= 24 * 60) return null;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

/**
 * Düzenleyicideki satırlar → kaydedilecek liste. Boş satır atlanır; anlaşılmayan
 * saat ve TEKRAR eden saat hatadır (sessizce düşmez — backend düşürürdü ve
 * kullanıcı kaydettiğini sanırdı).
 * @returns {{times: Array, errors: Array<{index:number, message:string}>}}
 */
export function cleanTimes(rows) {
  const times = [];
  const errors = [];
  const seen = new Map();
  rows.forEach((row, index) => {
    const raw = String(row.time ?? '').trim();
    if (!raw && !String(row.label ?? '').trim()) return;
    const time = parseTime(raw);
    if (time === null) {
      errors.push({ index, message: `“${raw || '(boş)'}” saat olarak anlaşılmadı.` });
      return;
    }
    if (seen.has(time)) {
      errors.push({ index, message: `${time} iki kez yazılmış.` });
      return;
    }
    seen.set(time, index);
    times.push({ time, kind: row.kind === 'teneffus' ? 'teneffus' : 'ders',
      label: String(row.label ?? '').trim().slice(0, 40) });
  });
  times.sort((a, b) => a.time.localeCompare(b.time));
  return { times, errors };
}

function istanbulParts(moment) {
  return Object.fromEntries(new Intl.DateTimeFormat('tr-TR', {
    timeZone: ZONE, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(moment).map((part) => [part.type, part.value]));
}

/** ISO damga → İstanbul saatiyle "16:50" (bugün değilse "12.10 16:50"). */
export function localClock(iso, { withDate = 'auto' } = {}) {
  const ms = Date.parse(String(iso || ''));
  if (Number.isNaN(ms)) return '';
  const parts = istanbulParts(new Date(ms));
  const time = `${parts.hour}:${parts.minute}`;
  if (withDate === false) return time;
  const now = istanbulParts(new Date());
  const day = `${parts.day}.${parts.month}`;
  return withDate === true || day !== `${now.day}.${now.month}` ? `${day} ${time}` : time;
}

/** "2026-10-14" → "14.10 Çar". */
export function shortDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  const weekday = DAYS[(new Date(y, m - 1, d).getDay() + 6) % 7];
  return `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')} ${weekday ? weekday.short : ''}`;
}

/** Sıradaki zil için kısa zaman: "47 dk sonra" · "2 sa 5 dk sonra" · "yarın" · "Çar". */
export function whenText(next, today) {
  if (!next) return '';
  if (next.date !== today) {
    const tomorrow = new Date(`${today}T12:00:00`);
    tomorrow.setDate(tomorrow.getDate() + 1);
    const iso = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, '0')}-`
      + `${String(tomorrow.getDate()).padStart(2, '0')}`;
    return next.date === iso ? 'yarın' : shortDate(next.date);
  }
  const minutes = Number(next.inMinutes || 0);
  if (minutes < 1) return 'şimdi';
  if (minutes < 60) return `${minutes} dk sonra`;
  const rest = minutes % 60;
  return `${Math.floor(minutes / 60)} sa${rest ? ` ${rest} dk` : ''} sonra`;
}

/** Bir günün (bu haftaki tarihinin) kısa açıklaması. */
export function dayNote(day) {
  if (!day) return null;
  if (day.override) return { tone: 'bad', text: 'Bugün kapatıldı' };
  if (day.mode === 'off') return { tone: 'bad', text: day.reason ? `Tatil · ${day.reason}` : 'Tatil — zil yok' };
  if (day.mode === 'profile') {
    return { tone: 'info', text: `Özel saat: ${day.profileName || 'saat seti'}` };
  }
  return null;
}

// ================================================================== sekme

/**
 * @param {object} app — {data, run, notify, root, store, open}
 */
export function renderProgram(app) {
  const wrap = h('div', 'bl-program');
  wrap.append(weekCard(app), upcomingCard(app), recentCard(app));
  return wrap;
}

function weekCard(app) {
  const { data } = app;
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Haftalık zil programı'));
  head.append(h('span', 'bl-hint', 'Her hafta tekrar eder. Bir günü düzenlemek için üstüne tıklayın.'));
  card.append(head);

  const schedule = data.schedule || { days: [], next: [] };
  const thisWeek = Object.fromEntries((schedule.days || []).map((day) => [day.weekday, day]));
  const today = schedule.today || '';
  const nowClock = String(schedule.now || '').slice(11, 16);
  const next = (schedule.next || [])[0];

  const grid = h('div', 'bl-week');
  for (const day of DAYS) {
    const planned = thisWeek[day.key];
    const isToday = planned && planned.date === today;
    const entries = (data.times || {})[day.key] || [];
    const column = h('section', `bl-day${isToday ? ' today' : ''}${entries.length ? '' : ' empty'}`);

    const top = h('button', 'bl-day-head');
    top.type = 'button';
    top.title = `${day.name} zil saatlerini düzenle`;
    top.append(h('b', null, day.name));
    const meta = h('span', 'bl-day-meta');
    if (planned) meta.append(h('span', null, shortDate(planned.date).slice(0, 5)));
    if (isToday) meta.append(h('em', 'bl-today', 'Bugün'));
    top.append(meta);
    top.addEventListener('click', () => openDayEditor(app, day.key));
    column.append(top);

    const note = dayNote(planned);
    if (note) column.append(h('div', `bl-day-note ${note.tone}`, note.text));

    const list = h('ol', 'bl-bells');
    for (const entry of entries) {
      const past = isToday && entry.time <= nowClock;
      const isNext = Boolean(next && planned && next.date === planned.date
        && next.time === entry.time && planned.mode === 'normal');
      const item = h('li', `bl-bell k-${entry.kind || 'ders'}${past ? ' past' : ''}`
        + `${isNext ? ' next' : ''}`);
      item.append(h('b', 'bl-bell-time', entry.time));
      item.append(h('span', 'bl-bell-kind', KIND_LABEL[entry.kind] || 'Ders'));
      if (entry.label) item.append(h('span', 'bl-bell-label', entry.label));
      if (isNext) item.title = 'Sıradaki zil';
      list.append(item);
    }
    column.append(list);
    if (!entries.length) column.append(h('p', 'bl-day-empty', 'Zil yok'));

    const foot = h('div', 'bl-day-foot');
    foot.append(button(entries.length ? 'Düzenle' : '+ Saat ekle', {
      variant: 'ghost', onClick: () => openDayEditor(app, day.key),
    }));
    if (entries.length) foot.append(h('span', 'bl-hint', `${entries.length} zil`));
    column.append(foot);
    grid.append(column);
  }
  card.append(grid);

  const legend = h('div', 'bl-legend');
  legend.append(h('span', 'bl-dotkey k-ders', 'Ders — arkasından “iyi dersler” anonsu'),
    h('span', 'bl-dotkey k-teneffus', 'Teneffüs — arkasından “iyi teneffüsler” anonsu'));
  if (schedule.now) {
    legend.append(h('span', 'bl-hint', `Saatler Türkiye saatine göre · şu an ${nowClock}`));
  }
  card.append(legend);
  return card;
}

function upcomingCard(app) {
  const { data } = app;
  const schedule = data.schedule || { days: [] };
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Önümüzdeki 7 gün'));
  head.append(h('span', 'bl-hint', 'Tatil ve özel günler BBD Akademi kurum takviminden gelir; '
    + 'zil kendiliğinden uyar.'));
  if (typeof app.open === 'function') {
    head.append(h('div', 'kit-spacer'), button('Tatil / özel gün ekle', {
      variant: 'ghost',
      title: 'BBD Akademi › Kurum takvimi açılır.',
      onClick: () => app.open('bbd_akademi', { section: 'calendar' }),
    }));
  }
  card.append(head);

  const strip = h('div', 'bl-days7');
  for (const day of schedule.days || []) {
    const note = dayNote(day);
    const tile = h('div', `bl-d7${day.date === schedule.today ? ' today' : ''}`
      + `${note ? ` ${note.tone}` : ''}`);
    tile.append(h('b', null, shortDate(day.date)));
    const bells = day.bells || [];
    if (note) {
      tile.append(h('span', 'bl-d7-mode', note.text));
    } else {
      tile.append(h('span', 'bl-d7-mode', bells.length ? 'Normal' : 'Zil yok'));
    }
    tile.append(h('span', 'bl-hint', bells.length
      ? `${bells.length} zil · ${bells[0].time}–${bells[bells.length - 1].time}` : ' '));
    if (day.source === 'akademi' && !note) tile.title = 'Akademi takvimine göre normal gün';
    strip.append(tile);
  }
  card.append(strip);
  return card;
}

// ------------------------------------------------------------ son çalmalar

const RECENT_ROWS = 12;
let showAll = false;

/** Günlük satırı → {what, km, agent:{tone,text}}. */
export function describeLog(row) {
  const kind = String(row.kind || '');
  const edge = String(row.edge || '');
  const name = String(row.group_name || '');
  let what = name || '—';
  if (kind === 'zil' && edge === 'plan') what = 'Otomatik zil';
  else if (kind === 'zil' && edge === 'manuel') what = 'Elle zil';
  else if (kind === 'zil' && edge === 'acil') what = 'Acil kapatma';
  else if (kind === 'cagri' && edge === 'manuel') what = `Grup çağrısı · ${name}`;
  else if (kind === 'cagri' && edge === 'sayfa') what = `Anons · ${name}`;
  else if (kind === 'cagri-plan' && name === 'zil planı') what = 'Akademi zil planı alındı';
  else if (kind === 'cagri-plan') what = 'Oturum çağrıları';
  else if (kind === 'anons') what = `Anons hazır değil · ${name}`;

  let agent = { tone: 'dim', text: '—' };
  const commandId = Number(row.command_id || 0);
  const agentOk = Number(row.agent_ok ?? -1);
  if (commandId > 0) {
    if (agentOk === 1) agent = { tone: 'good', text: 'Çaldı' };
    else if (agentOk === 0) agent = { tone: 'bad', text: row.agent_detail || 'Çalmadı' };
    else {
      const age = (Date.now() - Date.parse(row.at || '')) / 1000;
      agent = age < 180 ? { tone: 'wait', text: 'Bekleniyor…' } : { tone: 'dim', text: 'Cevap gelmedi' };
    }
  } else if (!Number(row.ok)) {
    agent = { tone: 'bad', text: String(row.detail || 'Gönderilemedi').replace(/^ajan · /, '') };
  }
  return { what, agent };
}

function recentCard(app) {
  const { data } = app;
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Son çalmalar'));
  head.append(h('span', 'bl-hint', 'Zil bilgisayarının cevabıyla birlikte.'));
  card.append(head);

  const rows = data.log || [];
  if (!rows.length) {
    card.append(h('p', 'bl-empty', 'Henüz çalma kaydı yok.'));
    return card;
  }
  const table = h('table', 'bl-table');
  const headRow = h('tr');
  for (const title of ['Saat', 'Ne', 'Zil bilgisayarı']) headRow.append(h('th', null, title));
  table.append(h('thead'), h('tbody'));
  table.tHead.append(headRow);
  for (const row of rows.slice(0, showAll ? rows.length : RECENT_ROWS)) {
    const { what, agent } = describeLog(row);
    const line = h('tr', agent.tone === 'bad' ? 'bad' : '');
    line.append(h('td', 'bl-time-cell', localClock(row.at)));
    const whatCell = h('td', null, what);
    if (row.detail) whatCell.title = row.detail;
    line.append(whatCell);
    line.append(h('td', `bl-agent-cell ${agent.tone}`, agent.text));
    table.tBodies[0].append(line);
  }
  card.append(table);
  if (rows.length > RECENT_ROWS) {
    const more = button(showAll ? 'Daha az göster' : `Tümünü göster (${rows.length})`, {
      variant: 'ghost',
      onClick: () => { showAll = !showAll; app.rerender(); },
    });
    const foot = h('div', 'bl-card-foot');
    foot.append(more);
    card.append(foot);
  }
  return card;
}

// ============================================================ düzenleyici

/**
 * Zil saatleri listesi düzenleyicisi — gün ve saat seti için ORTAK.
 * Satır: saat · Ders/Teneffüs · etiket · sil. Değişiklik bellekte birikir;
 * çağıran "Kaydet"te `read()` ile alır.
 */
export function timeListEditor(entries) {
  const rows = (entries || []).map((item) => ({ time: item.time, kind: item.kind || 'ders',
    label: item.label || '' }));
  const node = h('div', 'bl-editor');
  const list = h('div', 'bl-editor-rows');
  const error = h('p', 'bl-editor-error');
  error.hidden = true;

  const paint = (focusIndex = -1) => {
    list.replaceChildren();
    if (!rows.length) list.append(h('p', 'bl-empty', 'Bu gün zil çalmaz. “+ Saat ekle” ile başlayın.'));
    rows.forEach((row, index) => {
      const line = h('div', 'bl-editor-row');
      const time = h('input', 'kit-input bl-editor-time');
      time.value = row.time;
      time.placeholder = '09:30';
      time.maxLength = 5;
      time.setAttribute('aria-label', 'Saat');
      time.addEventListener('input', () => { row.time = time.value; });
      time.addEventListener('blur', () => {
        const parsed = parseTime(time.value);
        if (parsed) { row.time = parsed; time.value = parsed; }
        time.classList.toggle('bad', Boolean(time.value.trim()) && !parsed);
      });
      time.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); add(); }
      });

      const kind = h('div', 'bl-seg');
      for (const [key, label] of Object.entries(KIND_LABEL)) {
        const option = h('button', `bl-seg-btn k-${key}${row.kind === key ? ' on' : ''}`, label);
        option.type = 'button';
        option.addEventListener('click', () => {
          row.kind = key;
          for (const other of kind.children) other.classList.toggle('on', other === option);
        });
        kind.append(option);
      }

      const label = h('input', 'kit-input bl-editor-label');
      label.value = row.label;
      label.maxLength = 40;
      label.placeholder = 'Etiket (isteğe bağlı)';
      label.setAttribute('aria-label', 'Etiket (isteğe bağlı)');
      label.addEventListener('input', () => { row.label = label.value; });

      const drop = h('button', 'bl-icon-btn', '✕');
      drop.type = 'button';
      drop.title = 'Bu zili kaldır';
      drop.addEventListener('click', () => { rows.splice(index, 1); paint(); });

      line.append(time, kind, label, drop);
      list.append(line);
      if (index === focusIndex) window.setTimeout(() => time.focus(), 0);
    });
  };

  /** Yeni satır: son zilden 40 dk sonrası (ilk satırsa 09:00), tür sırayla değişir. */
  const add = () => {
    const last = rows.map((row) => parseTime(row.time)).filter(Boolean).sort().pop();
    const lastRow = rows[rows.length - 1];
    const step = lastRow && lastRow.kind === 'teneffus' ? 10 : 40;
    rows.push({ time: last ? (shiftTime(last, step) || '') : '09:00',
      kind: lastRow ? (lastRow.kind === 'ders' ? 'teneffus' : 'ders') : 'ders', label: '' });
    paint(rows.length - 1);
  };

  const shift = (minutes) => {
    const moved = rows.map((row) => shiftTime(row.time, minutes));
    if (moved.some((value, index) => value === null && parseTime(rows[index].time) !== null)) {
      error.hidden = false;
      error.textContent = 'Kaydırınca bir zil gün dışına taşıyor.';
      return;
    }
    rows.forEach((row, index) => { if (moved[index]) row.time = moved[index]; });
    error.hidden = true;
    paint();
  };

  const tools = h('div', 'bl-editor-tools');
  tools.append(button('+ Saat ekle', { onClick: add }));
  tools.append(h('span', 'kit-spacer'));
  tools.append(h('span', 'bl-hint', 'Hepsini kaydır'));
  for (const minutes of [-10, -5, 5, 10]) {
    tools.append(button(`${minutes > 0 ? '+' : '−'}${Math.abs(minutes)} dk`, {
      variant: 'ghost', title: `Bütün zilleri ${Math.abs(minutes)} dakika `
        + `${minutes > 0 ? 'ileri' : 'geri'} al`, onClick: () => shift(minutes),
    }));
  }

  node.append(list, tools, error);
  paint();
  return {
    node,
    /** {times, errors} — hata varsa ekranda gösterilir. */
    read() {
      const result = cleanTimes(rows);
      error.hidden = !result.errors.length;
      error.textContent = result.errors.map((item) => item.message).join(' ');
      for (const [index, line] of [...list.querySelectorAll('.bl-editor-row')].entries()) {
        line.classList.toggle('bad', result.errors.some((item) => item.index === index));
      }
      return result;
    },
    replace(next) {
      rows.splice(0, rows.length, ...(next || []).map((item) => ({ ...item })));
      paint();
    },
  };
}

/** Haftalık programın bir gününü çekmecede düzenler; kaydette TÜM haftayı yollar. */
export function openDayEditor(app, dayKey) {
  const day = DAYS.find((item) => item.key === dayKey);
  const week = app.data.times || {};
  const panel = drawer(app.root, {
    title: `${day.name} — zil saatleri`,
    subtitle: 'Her hafta tekrar eder. Değişiklikler “Kaydet”e basınca zil bilgisayarına gider.',
  });
  const editor = timeListEditor(week[dayKey] || []);

  // --- başka günlere kopyala ---
  const copy = h('div', 'bl-copy');
  copy.append(h('span', 'bl-label', 'Bu saatleri şu günlere de uygula'));
  const picks = new Map();
  const chips = h('div', 'bl-copy-days');
  for (const other of DAYS.filter((item) => item.key !== dayKey)) {
    const chip = h('button', 'bl-chip', other.short);
    chip.type = 'button';
    const count = (week[other.key] || []).length;
    chip.title = count ? `${other.name}: şu an ${count} zil — üzerine yazılır` : `${other.name}: zil yok`;
    chip.addEventListener('click', () => {
      picks.set(other.key, !picks.get(other.key));
      chip.classList.toggle('on', Boolean(picks.get(other.key)));
    });
    chips.append(chip);
  }
  const weekdays = button('Hafta içi', {
    variant: 'ghost',
    onClick: () => {
      for (const [index, other] of DAYS.filter((item) => item.key !== dayKey).entries()) {
        const on = ['mon', 'tue', 'wed', 'thu', 'fri'].includes(other.key);
        picks.set(other.key, on);
        chips.children[index].classList.toggle('on', on);
      }
    },
  });
  chips.append(weekdays);
  copy.append(chips);

  // --- başka günden al ---
  const from = h('div', 'bl-copy');
  from.append(h('span', 'bl-label', 'Başka günden al'));
  const fromRow = h('div', 'bl-copy-days');
  for (const other of DAYS.filter((item) => item.key !== dayKey && (week[item.key] || []).length)) {
    const chip = h('button', 'bl-chip', other.short);
    chip.type = 'button';
    chip.title = `${other.name} saatlerini buraya getirir (kaydetmeden önce görürsünüz)`;
    chip.addEventListener('click', () => editor.replace(week[other.key]));
    fromRow.append(chip);
  }
  for (const profile of app.data.profiles || []) {
    const chip = h('button', 'bl-chip set', profile.name);
    chip.type = 'button';
    chip.title = 'Saat setinin saatlerini buraya getirir';
    chip.addEventListener('click', () => editor.replace(profile.times));
    fromRow.append(chip);
  }
  if (fromRow.childElementCount) from.append(fromRow);

  const save = async () => {
    const { times, errors } = editor.read();
    if (errors.length) return;
    const targets = [...picks].filter(([, on]) => on).map(([key]) => key);
    const overwritten = targets.filter((key) => (week[key] || []).length);
    if (overwritten.length) {
      const yes = await confirmSimple(app.root, {
        title: 'Diğer günlerin saatleri değişsin mi?',
        description: `${overwritten.map((key) => DAYS.find((d) => d.key === key).name).join(', ')} `
          + 'gününün mevcut saatleri bu listeyle değiştirilecek.',
        confirmLabel: 'Uygula',
      });
      if (!yes) return;
    }
    const next = {};
    for (const item of DAYS) next[item.key] = (week[item.key] || []).map((entry) => ({ ...entry }));
    next[dayKey] = times;
    for (const key of targets) next[key] = times.map((entry) => ({ ...entry }));
    const result = await app.run(() => app.store.saveTimes(next), {
      success: () => `${day.name}${targets.length ? ` ve ${targets.length} gün` : ''} kaydedildi — `
        + 'zil bilgisayarı yeni saatlere göre çalacak.',
    });
    if (result && result.ok !== false) panel.close();
  };

  const foot = h('div', 'bl-drawer-foot');
  foot.append(h('span', 'kit-spacer'), button('Vazgeç', { onClick: () => panel.close() }),
    button('Kaydet', { variant: 'primary', onClick: save }));
  panel.body.append(editor.node, copy);
  if (from.childElementCount > 1) panel.body.append(from);
  panel.body.append(foot);
  panel.body.querySelector('.bl-editor-time')?.focus();
}
