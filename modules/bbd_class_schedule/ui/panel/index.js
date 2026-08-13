// Ders Takvimi paneli.
//
// Ekranın sorusu: "hafta içi hangi saatlerde ders var?" Cevap gruplara göre
// değişebilir, o yüzden plan bir GRUBA aittir. Grup opsiyoneldir: tek plan
// yetiyorsa kullanıcı "Genel"in içinde kalır ve grup diye bir şey görmez.
//
// Kabuk sözleşmesi: `mount(root, ctx)` → temizleyici fonksiyon.

import {
  cleanName, DAYS, DEFAULT_DURATION, duration, emptyWeek, formatDuration,
  makeBlock, NAME_MAX, newId, overlappingIds, sortBlocks, totalMinutes, usedNames,
} from './schedule.js';
import { COLORS, error as storeError, flush, init, load, save } from './store.js';

let state = null;
let activeGroupId = null;
let undoSnapshot = null;
let ctxRef = null;
const nodes = {};

// ------------------------------------------------------------------ yardım

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function activeGroup() {
  return state.groups.find((group) => group.id === activeGroupId) || state.groups[0];
}

function commit({ snapshot = false } = {}) {
  if (snapshot) undoSnapshot = JSON.parse(JSON.stringify(state));
  save(state);
}

/** Grubun haftalık toplamı: kaç ders, kaç saat. */
function weekStats(group) {
  const blocks = DAYS.flatMap((day) => group.week[day.key]);
  return { count: blocks.length, minutes: totalMinutes(blocks) };
}

// ------------------------------------------------------------------- çizim

function renderGroupBar() {
  const bar = nodes.groupBar;
  bar.replaceChildren();

  for (const group of state.groups) {
    const chip = h('button', 'cs-chip');
    chip.type = 'button';
    chip.style.setProperty('--chip', group.color);
    chip.classList.toggle('active', group.id === activeGroup().id);

    chip.append(h('i', 'cs-chip-dot'), h('span', null, group.name));

    const stats = weekStats(group);
    chip.append(h('em', 'cs-chip-count', stats.count ? String(stats.count) : '—'));

    chip.addEventListener('click', () => {
      activeGroupId = group.id;
      renderAll();
    });
    bar.append(chip);
  }

  const add = h('button', 'cs-chip cs-chip-add', '+ Yeni grup');
  add.type = 'button';
  add.addEventListener('click', addGroup);
  bar.append(add);
}

function renderHeadline() {
  const group = activeGroup();
  const stats = weekStats(group);

  nodes.groupName.value = group.name;
  nodes.groupName.style.setProperty('--chip', group.color);

  nodes.headStats.textContent = stats.count === 0
    ? 'Henüz ders girilmedi'
    : `${stats.count} ders · haftada ${formatDuration(stats.minutes)}`;

  nodes.students.textContent = group.students.length === 0
    ? 'Öğrenci seçilmedi'
    : `${group.students.length} öğrenci`;

  nodes.removeGroup.disabled = state.groups.length === 1;
}

function renderDay(dayKey) {
  const group = activeGroup();
  const day = DAYS.find((entry) => entry.key === dayKey);
  const blocks = sortBlocks(group.week[dayKey]);
  const clashing = overlappingIds(blocks);
  const column = nodes.days[dayKey];

  column.count.textContent = blocks.length === 0
    ? '—'
    : `${blocks.length} ders · ${formatDuration(totalMinutes(blocks))}`;

  column.list.replaceChildren();
  column.column.classList.toggle('empty', blocks.length === 0);

  blocks.forEach((block, index) => {
    const item = h('li', 'cs-block');
    if (clashing.has(block.id)) item.classList.add('clash');

    item.append(h('span', 'cs-block-no', String(index + 1)));

    // Ad yerinde düzenlenir: saatleri hızlıca girip adları sonra doldurmak
    // isteyen kullanıcı bloğu silip yeniden eklemek zorunda kalmasın.
    const name = h('input', 'cs-block-name');
    name.type = 'text';
    name.value = block.name || '';
    name.placeholder = 'Ders adı';
    name.maxLength = NAME_MAX;
    name.setAttribute('list', 'cs-names');
    name.setAttribute('aria-label', `${day.name} ${block.start} dersinin adı`);
    name.addEventListener('input', () => {
      block.name = cleanName(name.value);
      commit();
    });
    name.addEventListener('change', () => renderNames());
    item.append(name);

    const foot = h('span', 'cs-block-foot');
    const time = h('span', 'cs-block-time');
    time.append(h('b', null, block.start), h('i', null, '–'), h('b', null, block.end));
    foot.append(time, h('span', 'cs-block-len', formatDuration(duration(block))));
    item.append(foot);

    const remove = h('button', 'cs-block-remove', '×');
    remove.type = 'button';
    remove.title = 'Dersi kaldır';
    remove.setAttribute('aria-label', `${day.name} ${block.start} dersini kaldır`);
    remove.addEventListener('click', () => {
      group.week[dayKey] = group.week[dayKey].filter((entry) => entry.id !== block.id);
      commit();
      renderDay(dayKey);
      renderGroupBar();
      renderHeadline();
    });
    item.append(remove);

    if (clashing.has(block.id)) item.title = 'Bu ders bir öncekiyle çakışıyor.';
    column.list.append(item);
  });
}

/** Ders adı önerileri — grupta daha önce yazılanlar. */
function renderNames() {
  nodes.names.replaceChildren(
    ...usedNames(activeGroup().week).map((name) => {
      const option = document.createElement('option');
      option.value = name;
      return option;
    }),
  );
}

function renderAll() {
  renderGroupBar();
  renderHeadline();
  for (const day of DAYS) renderDay(day.key);
  renderNames();
}

// ------------------------------------------------------------------ eylem

function addBlock(dayKey, fields) {
  const group = activeGroup();
  const blocks = group.week[dayKey];
  const last = sortBlocks(blocks).at(-1);

  const result = makeBlock(fields.start.value, fields.end.value, {
    fallbackDuration: last ? duration(last) : DEFAULT_DURATION,
    name: fields.name.value,
  });

  if (result.error) {
    fields.error.textContent = result.error;
    fields.start.focus();
    return;
  }

  fields.error.textContent = '';
  blocks.push(result.block);
  commit();
  renderDay(dayKey);
  renderGroupBar();
  renderHeadline();
  renderNames();

  fields.name.value = '';
  fields.start.value = '';
  fields.end.value = '';
  fields.name.focus();
}

/** Bir günün planını diğer hafta içi günlere kopyalar. */
function copyDayToRest(dayKey) {
  const group = activeGroup();
  const source = sortBlocks(group.week[dayKey]);
  if (source.length === 0) return;

  commit({ snapshot: true });
  for (const day of DAYS) {
    if (day.key === dayKey) continue;
    group.week[day.key] = source.map((block) => ({ ...block, id: newId() }));
  }
  commit();
  renderAll();
  toast(`${DAYS.find((d) => d.key === dayKey).name} planı diğer günlere uygulandı.`);
}

function clearDay(dayKey) {
  const group = activeGroup();
  if (group.week[dayKey].length === 0) return;

  commit({ snapshot: true });
  group.week[dayKey] = [];
  commit();
  renderAll();
  toast(`${DAYS.find((d) => d.key === dayKey).name} temizlendi.`);
}

function addGroup() {
  const group = {
    id: newId(),
    name: `Grup ${state.groups.length + 1}`,
    color: COLORS[state.groups.length % COLORS.length],
    students: [],
    week: emptyWeek(),
  };
  state.groups.push(group);
  activeGroupId = group.id;
  commit();
  renderAll();
  nodes.groupName.focus();
  nodes.groupName.select();
}

function removeGroup() {
  if (state.groups.length === 1) return;
  const group = activeGroup();
  const stats = weekStats(group);
  const warning = stats.count > 0 ? `\n\n${stats.count} ders kaydı da silinecek.` : '';
  if (!window.confirm(`"${group.name}" grubu silinsin mi?${warning}`)) return;

  state.groups = state.groups.filter((entry) => entry.id !== group.id);
  activeGroupId = state.groups[0].id;
  commit();
  renderAll();
}

/** Öğrenci seçimi — kaynak Öğrenci Yönetimi modülü (K3: registry üzerinden). */
function openStudents() {
  const group = activeGroup();
  const provider = ctxRef?.capability?.('bbd_students.list');

  const dialog = h('div', 'cs-modal');
  const card = h('div', 'cs-modal-card');
  card.append(h('h3', null, `${group.name} — Öğrenciler`));

  if (!provider) {
    const note = h('p', 'cs-modal-note');
    note.textContent =
      'Öğrenci listesi Öğrenci Yönetimi modülünden gelir. O modül yayına '
      + 'alınınca öğrenciler burada listelenecek ve gruba seçilebilecek. '
      + 'Ders saatleri şimdiden girilebilir; grup, öğrenciler geldiğinde dolar.';
    card.append(note);
  }

  const close = h('button', 'cs-btn', 'Kapat');
  close.type = 'button';
  close.addEventListener('click', () => dialog.remove());

  const foot = h('div', 'cs-modal-foot');
  foot.append(close);
  card.append(foot);

  dialog.append(card);
  dialog.addEventListener('click', (event) => { if (event.target === dialog) dialog.remove(); });

  // Panelin İÇİNE eklenir: dışarıda kalsaydı `.cs` renk değişkenlerini
  // göremez, koyu gövde rengini miras alıp beyaz kartta okunmaz olurdu.
  nodes.view.append(dialog);
  close.focus();
}

function toast(text) {
  nodes.toast.replaceChildren(h('span', null, text));

  const undo = h('button', 'cs-toast-undo', 'Geri al');
  undo.type = 'button';
  undo.addEventListener('click', () => {
    if (!undoSnapshot) return;
    state = undoSnapshot;
    undoSnapshot = null;
    if (!state.groups.some((group) => group.id === activeGroupId)) activeGroupId = state.groups[0].id;
    commit();
    renderAll();
    nodes.toast.classList.remove('show');
  });
  nodes.toast.append(undo);

  nodes.toast.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => nodes.toast.classList.remove('show'), 6000);
}

// ------------------------------------------------------------------- kurulum

function buildDayColumn(day) {
  const column = h('section', 'cs-day');

  const head = h('header', 'cs-day-head');
  head.append(h('h3', null, day.name));
  const count = h('span', 'cs-day-count');
  head.append(count);

  const tools = h('div', 'cs-day-tools');
  const copy = h('button', 'cs-tool', '⧉');
  copy.type = 'button';
  copy.title = 'Bu günün planını diğer günlere uygula';
  copy.addEventListener('click', () => copyDayToRest(day.key));

  const clear = h('button', 'cs-tool', '×');
  clear.type = 'button';
  clear.title = 'Günü temizle';
  clear.addEventListener('click', () => clearDay(day.key));

  tools.append(copy, clear);
  head.append(tools);
  column.append(head);

  const list = h('ul', 'cs-blocks');
  column.append(list);

  const form = h('form', 'cs-add');

  const name = h('input', 'cs-add-name');
  name.type = 'text';
  name.placeholder = 'Ders adı';
  name.maxLength = NAME_MAX;
  name.autocomplete = 'off';
  name.setAttribute('list', 'cs-names');
  name.setAttribute('aria-label', `${day.name} için ders adı`);

  const row = h('div', 'cs-add-row');

  const start = h('input', 'cs-time');
  start.type = 'text';
  start.inputMode = 'numeric';
  start.placeholder = '09:00';
  start.setAttribute('aria-label', `${day.name} ders başlangıcı`);

  const end = h('input', 'cs-time');
  end.type = 'text';
  end.inputMode = 'numeric';
  end.placeholder = '09:40';
  end.setAttribute('aria-label', `${day.name} ders bitişi`);

  const submit = h('button', 'cs-add-go', '+');
  submit.type = 'submit';
  submit.title = 'Ders ekle (Enter)';

  row.append(start, h('i', 'cs-dash', '–'), end, submit);
  form.append(name, row);

  const error = h('p', 'cs-add-error');
  column.append(form, error);

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    addBlock(day.key, { name, start, end, error });
  });

  nodes.days[day.key] = { column, list, count };
  return column;
}

/**
 * İlan edilen yetenek (module.yaml → provides).
 *
 * Salt okunur KOPYA döner: tüketen modül elindekini değiştirse bile takvim
 * bozulmaz. Panel açık olmasa da çalışır — kabuk dosyayı gerektiğinde yükler.
 */
export function capabilities(ctx) {
  return {
    // Zil sistemi bunu tüketir. Panel hiç açılmamış olabilir; bu yüzden veriyi
    // önbellekten değil, doğrudan çekirdekten okuruz.
    'bbd_class_schedule.week': async () => {
      const payload = await ctx.api('/api/bbd_class_schedule/groups');
      const groups = payload?.document?.groups || [];
      return groups.map((group) => ({
        id: group.id,
        name: group.name,
        color: group.color,
        week: Object.fromEntries(
          DAYS.map((day) => [day.key, sortBlocks(group.week?.[day.key] || []).map((b) => ({ ...b }))]),
        ),
      }));
    },
    'bbd_class_schedule.week.cached': () => load().groups.map((group) => ({
      id: group.id,
      name: group.name,
      color: group.color,
      week: Object.fromEntries(
        DAYS.map((day) => [
          day.key,
          sortBlocks(group.week[day.key]).map((block) => ({ ...block })),
        ]),
      ),
    })),
  };
}

export function mount(root, ctx) {
  ctxRef = ctx;
  // Önbellek boş: önce varsayılanla çiz, çekirdek yanıt verince tazele.
  state = load();
  activeGroupId = state.groups[0].id;
  nodes.days = {};

  // Panel kendi stilini getirir; kabuk modülün stilini bilmez.
  const styleHref = new URL('./panel.css', import.meta.url).href;
  let style = document.querySelector(`link[href="${styleHref}"]`);
  if (!style) {
    style = h('link');
    style.rel = 'stylesheet';
    style.href = styleHref;
    document.head.append(style);
  }

  const view = h('div', 'cs');
  nodes.view = view;

  // Ders adı önerileri, tüm sütunlardaki alanlar bunu paylaşır.
  nodes.names = h('datalist');
  nodes.names.id = 'cs-names';
  view.append(nodes.names);

  // grup şeridi
  nodes.groupBar = h('div', 'cs-groups');
  view.append(nodes.groupBar);

  // seçili grubun künyesi
  const headline = h('header', 'cs-head');
  nodes.groupName = h('input', 'cs-name');
  nodes.groupName.type = 'text';
  nodes.groupName.maxLength = 60;
  nodes.groupName.setAttribute('aria-label', 'Grup adı');
  nodes.groupName.addEventListener('input', () => {
    activeGroup().name = nodes.groupName.value.trim() || 'Grup';
    commit();
    renderGroupBar();
  });

  nodes.headStats = h('span', 'cs-head-stats');

  nodes.students = h('button', 'cs-btn cs-btn-quiet');
  nodes.students.type = 'button';
  nodes.students.addEventListener('click', openStudents);

  nodes.removeGroup = h('button', 'cs-btn cs-btn-quiet', 'Grubu sil');
  nodes.removeGroup.type = 'button';
  nodes.removeGroup.addEventListener('click', removeGroup);

  headline.append(nodes.groupName, nodes.headStats, h('div', 'cs-spacer'), nodes.students, nodes.removeGroup);
  view.append(headline);

  // hafta
  const week = h('div', 'cs-week');
  for (const day of DAYS) week.append(buildDayColumn(day));
  view.append(week);

  nodes.toast = h('div', 'cs-toast');
  view.append(nodes.toast);

  root.replaceChildren(view);
  renderAll();

  // Kalıcı veriyi çekirdekten al; tarayıcı belleğindeki eski takvim varsa bir kez
  // içeri taşınır. Ekran bu arada varsayılanla açık durur, boş kalmaz.
  (async () => {
    const result = await init(ctx.api);
    state = load();
    if (!state.groups.some((group) => group.id === activeGroupId)) {
      activeGroupId = state.groups[0].id;
    }
    renderAll();
    if (result.migrated) {
      toast("Takvim tarayıcı belleğinden kalıcı depoya taşındı.");
    } else if (result.error) {
      toast(`Takvim okunamadı — yerel kopya gösteriliyor. ${result.error}`);
    }
  })();

  return () => {
    clearTimeout(toast.timer);
    document.querySelector('.cs-modal')?.remove();
    // Bekleyen yazma varsa panel kapanmadan gönderilsin.
    flush();
    root.replaceChildren();
  };
}
