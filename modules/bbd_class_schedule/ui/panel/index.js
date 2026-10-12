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
    'Saatler ve saat setleri Zil Sistemi ekranından girilir; günlerin kipi (tatil, özel '
    + 'gün) Akademi kurum takviminden gelir. Buraya kendiliğinden yansır.'));
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

const MODE_LABEL = { normal: 'Normal', off: 'Tatil — zil yok', profile: 'Saat seti' };
const SOURCE_LABEL = { akademi: 'Akademi planı', km: 'KM acil kapatma', none: 'haftalık saatler' };

/** Önümüzdeki 7 gün: Akademi kurum takviminden gelen gün kipi. */
function renderDays(data) {
  const card = h('section', 'cs-card');
  card.append(h('h4', null, 'Önümüzdeki 7 gün'));
  const days = data.days || [];
  if (!days.length) {
    card.append(h('p', 'cs-empty', 'Zil planı bilgisi yok — zil haftalık saatlerle çalar.'));
    return card;
  }
  const names = Object.fromEntries((data.profiles || []).map((item) => [item.id, item.name]));
  const list = h('div', 'cs-days');
  for (const day of days) {
    const [y, m, d] = String(day.date).split('-').map(Number);
    const weekday = DAYS[(new Date(y, m - 1, d).getDay() + 6) % 7];
    const row = h('div', `cs-plan-day cs-mode-${day.override ? 'off' : day.mode}`);
    row.append(h('b', null, `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')} ${weekday.short}`));
    const mode = day.override ? 'Acil kapatıldı — zil yok'
      : day.mode === 'profile' ? `Saat seti: ${names[day.profileId] || day.profileId}`
        : (MODE_LABEL[day.mode] || day.mode);
    row.append(h('span', null, mode));
    row.append(h('span', 'cs-hint', [day.reason, SOURCE_LABEL[day.source] || day.source]
      .filter(Boolean).join(' · ')));
    list.append(row);
  }
  card.append(list);
  return card;
}

/** Saat setleri — özel günlerde haftalık saatlerin yerine çalan adlı listeler. */
function renderProfiles(data) {
  const card = h('section', 'cs-card');
  card.append(h('h4', null, 'Saat setleri'));
  const profiles = data.profiles || [];
  if (!profiles.length) {
    card.append(h('p', 'cs-empty', 'Saat seti yok. Zil Sistemi › Takvim’den eklenir.'));
    return card;
  }
  for (const profile of profiles) {
    const block = h('div', 'cs-profile');
    block.append(h('b', null, profile.name));
    const slots = h('div', 'cs-profile-times');
    for (const entry of profile.times || []) {
      const slot = h('span', `cs-kind ${entry.kind === 'teneffus' ? 'break' : 'lesson'}`,
        `${entry.time} ${entry.kind === 'teneffus' ? 'teneffüs' : 'ders'}`);
      slots.append(slot);
    }
    block.append(slots);
    card.append(block);
  }
  return card;
}

function renderGroups(data) {
  const card = h('section', 'cs-card');
  card.append(h('h4', null, 'Gruplar'));

  if (!(data.groups || []).length) {
    card.append(h('p', 'cs-empty',
      'Gruplar Öğrenci Yönetimi’nde (Öğrenci Yönetimi › Gruplar) yönetilir.'));
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
    body.append(renderNotice(ctx), renderDays(data), renderWeek(data), renderProfiles(data),
      renderGroups(data));
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
