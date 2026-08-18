// Ders Takvimi paneli — SALT OKUNUR AYNA.
//
// Bu ekranda hiçbir şey değiştirilemez. Haftalık zil saatlerinin ve grupların
// sahibi Zil Sistemi'dir; burası onun görünümüdür.
//
// TASARIM KARARI: değiştirilemezlik SAKLANMAZ, SÖYLENİR. Kapalı bir giriş
// alanı çizip kullanıcının tıklayıp tıklayıp anlamasını beklemek yerine, üstte
// tek cümleyle nereden düzenleneceği yazar ve oraya götüren bir düğme durur.
// 0.1'de bu ekran verinin sahibiydi; alışkanlığı olan kullanıcı önce burayı
// arayacak.
//
// Ortak bileşenler kabuktan gelir (ADR 0011). Yol panelin KOPYALANMIŞ konumuna
// göredir: shell/panels/bbd_class_schedule/ → shell/ui-kit/.

import { button, h, loadStyles } from '../../ui-kit/kit.js';

const DAYS = [
  { key: 'mon', name: 'Pazartesi', short: 'Pzt' },
  { key: 'tue', name: 'Salı', short: 'Sal' },
  { key: 'wed', name: 'Çarşamba', short: 'Çar' },
  { key: 'thu', name: 'Perşembe', short: 'Per' },
  { key: 'fri', name: 'Cuma', short: 'Cum' },
  { key: 'sat', name: 'Cumartesi', short: 'Cmt' },
  { key: 'sun', name: 'Pazar', short: 'Paz' },
];

/** Bugünün sütunu vurgulanır: kullanıcı çoğunlukla bugüne bakar. */
function todayKey() {
  const index = new Date().getDay();
  return DAYS[index === 0 ? 6 : index - 1].key;
}

function renderNotice(ctx) {
  const box = h('div', 'cs-notice');
  box.append(h('span', 'cs-lock', '🔒'));
  const text = h('div', 'cs-notice-text');
  text.append(h('b', null, 'Bu ekran salt okunurdur.'));
  text.append(h('span', null,
    'Saatler ve gruplar Zil Sistemi ekranından girilir; buraya kendiliğinden yansır.'));
  box.append(text);
  box.append(h('div', 'kit-spacer'));
  box.append(button('Zil Sistemi’ne git', {
    variant: 'primary',
    onClick: () => ctx.open('bell'),
  }));
  return box;
}

function renderWeek(data) {
  const card = h('section', 'cs-card');
  card.append(h('h4', null, 'Haftalık zil saatleri'));

  const total = DAYS.reduce((sum, day) => sum + (data.times[day.key] || []).length, 0);
  if (total === 0) {
    card.append(h('p', 'cs-empty',
      'Henüz zil saati girilmemiş. Zil Sistemi ekranından eklenebilir.'));
    return card;
  }

  const today = todayKey();
  const grid = h('div', 'cs-week');
  for (const day of DAYS) {
    const entries = data.times[day.key] || [];
    const column = h('div', `cs-day${day.key === today ? ' today' : ''}`);

    const head = h('div', 'cs-day-head');
    head.append(h('b', null, day.name));
    head.append(h('span', 'cs-day-count', entries.length ? `${entries.length} zil` : '—'));
    column.append(head);

    if (!entries.length) {
      column.append(h('p', 'cs-none', 'zil yok'));
    } else {
      for (const entry of entries) {
        const row = h('div', 'cs-slot');
        row.append(h('b', 'cs-clock', entry.time));
        // TÜR ETİKETTEN AYRIDIR: hangi anonsun çalacağını tür belirler,
        // etiket yalnız insan içindir. Aynada ikisini karıştırmamak gerekir.
        row.append(h('span', `cs-kind ${entry.kind === 'teneffus' ? 'break' : 'lesson'}`,
          entry.kind === 'teneffus' ? 'teneffüs' : 'ders'));
        if (entry.label) row.append(h('span', 'cs-label', entry.label));
        column.append(row);
      }
    }
    grid.append(column);
  }
  card.append(grid);
  return card;
}

function renderGroups(data) {
  const card = h('section', 'cs-card');
  card.append(h('h4', null, 'Gruplar'));

  if (!(data.groups || []).length) {
    card.append(h('p', 'cs-empty', 'Henüz grup yok.'));
    return card;
  }

  const list = h('div', 'cs-groups');
  for (const group of data.groups) list.append(h('span', 'cs-chip', group.name));
  card.append(list);
  card.append(h('p', 'cs-hint',
    'Gruplar Zil Sistemi ekranından açılır ve oradan elle çağrılır.'));
  return card;
}

function renderUnavailable(reason, ctx) {
  const box = h('div', 'cs-empty-state');
  box.append(h('h3', null, 'Takvim okunamadı'));
  box.append(h('p', null, reason || 'Kaynak modüle ulaşılamadı.'));
  box.append(button('Zil Sistemi’ne git', {
    variant: 'primary',
    onClick: () => ctx.open('bell'),
  }));
  return box;
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);
  let disposed = false;

  const view = h('div', 'cs kit-panel');
  const body = h('div', 'cs-body kit-body');
  view.append(body);

  const loading = h('div', 'cs-loading');
  loading.append(h('span', 'cs-spinner'), h('p', null, 'Zil saatleri okunuyor…'));
  root.replaceChildren(loading);

  (async () => {
    const data = await ctx.api('/api/bbd_class_schedule/week');
    if (disposed) return;
    root.replaceChildren(view);

    if (!data.available) {
      body.append(renderUnavailable(data.reason, ctx));
      return;
    }
    body.append(renderNotice(ctx), renderWeek(data), renderGroups(data));
  })().catch((error) => {
    if (disposed) return;
    const box = h('div', 'cs-empty-state');
    box.append(h('h3', null, 'Ders Takvimi açılamadı'));
    box.append(h('p', null, error?.message || String(error)));
    root.replaceChildren(box);
  });

  return () => {
    disposed = true;
    root.replaceChildren();
  };
}
