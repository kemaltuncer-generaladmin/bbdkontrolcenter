// Ay takvimi — her gün hücresinde o günün yemek kaydı rozeti.
//
// Rozet tek başına renk değil, SAYI taşır: renk körlüğünde de okunur.
//   · yeşil nokta + sayı  → işlenmiş porsiyon
//   · kırmızı üçgen       → başarısız satır var
//   · üstü çizili         → tamamı geri alınmış
//   · gri zemin           → tatil / hafta sonu

import { dayNames, h, isoOf, monthName } from './kit.js';

export function createCalendar({ onPick, onToggleHoliday }) {
  let cursor = new Date();       // görüntülenen ay
  let selected = null;           // ISO gün
  let days = {};                 // ISO → {total, ok, failed, reversed}
  let holidays = {};             // ISO → etiket

  const root = h('div', 'cal');
  const head = h('div', 'cal-head');
  const title = h('div', 'cal-title');
  const nav = h('div', 'cal-nav');

  const navButton = (label, hint, step) => {
    const node = h('button', 'cal-nav-btn', label);
    node.type = 'button';
    node.title = hint;
    node.addEventListener('click', () => {
      cursor = new Date(cursor.getFullYear(), cursor.getMonth() + step, 1);
      onPick(null, monthKey());   // ay değişti: veriyi yenile, seçimi koru
    });
    return node;
  };

  const today = h('button', 'cal-nav-btn', 'Bugün');
  today.type = 'button';
  today.title = 'Bu aya ve bugüne dön';
  today.addEventListener('click', () => {
    cursor = new Date();
    onPick(isoOf(new Date()), monthKey());
  });

  nav.append(navButton('‹', 'Önceki ay', -1), today, navButton('›', 'Sonraki ay', 1));
  head.append(title, nav);

  const weekRow = h('div', 'cal-week');
  for (const name of dayNames()) weekRow.append(h('div', 'cal-week-cell', name));

  const grid = h('div', 'cal-grid');
  const legend = h('div', 'cal-legend');
  legend.append(
    h('span', 'cal-legend-item', 'sayı = işlenen porsiyon'),
    h('span', 'cal-legend-item warn', 'kırmızı = hatalı satır'),
    h('span', 'cal-legend-item muted', 'gri = tatil / hafta sonu'),
  );

  root.append(head, weekRow, grid, legend);

  function monthKey() {
    return `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}`;
  }

  function render() {
    title.textContent = `${monthName(cursor.getMonth())} ${cursor.getFullYear()}`;
    grid.replaceChildren();

    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    // Hafta pazartesi başlar: JS'te 0=Pazar olduğu için kaydırıyoruz.
    const lead = (first.getDay() + 6) % 7;
    const start = new Date(first);
    start.setDate(1 - lead);

    const todayIso = isoOf(new Date());

    for (let index = 0; index < 42; index += 1) {
      const date = new Date(start);
      date.setDate(start.getDate() + index);
      const iso = isoOf(date);
      const outside = date.getMonth() !== cursor.getMonth();
      const weekend = date.getDay() === 0 || date.getDay() === 6;
      const holiday = Object.hasOwn(holidays, iso);
      const info = days[iso];

      const cell = h('button', 'cal-cell');
      cell.type = 'button';
      if (outside) cell.classList.add('out');
      if (weekend || holiday) cell.classList.add('off');
      if (iso === todayIso) cell.classList.add('today');
      if (iso === selected) cell.classList.add('on');
      cell.dataset.day = iso;

      cell.append(h('span', 'cal-day', String(date.getDate())));

      if (info && info.total > 0) {
        const allReversed = info.reversed >= info.total;
        // Tamamı geri alınmışsa "0" değil, ÜSTÜ ÇİZİLİ toplam gösterilir:
        // "o gün hiç kayıt olmadı" ile "kayıt vardı, iptal edildi" farklı şeyler.
        const badge = h('span', `cal-badge${allReversed ? ' undone' : ''}`,
          String(allReversed ? info.total : (info.ok || 0)));
        badge.title = allReversed
          ? `${info.total} kayıt — tamamı geri alınmış`
          : `${info.ok} işlenmiş${info.failed ? `, ${info.failed} hatalı` : ''}${info.reversed ? `, ${info.reversed} geri alınmış` : ''}`;
        cell.append(badge);
        if (info.failed > 0) cell.append(h('span', 'cal-warn', '!'));
      }

      if (holiday) {
        cell.title = holidays[iso] || 'Tatil';
        cell.append(h('span', 'cal-off-dot', ''));
      }

      cell.addEventListener('click', () => onPick(iso, monthKey()));
      // Sağ tık: tatil işaretle / kaldır — akışı kesmeden.
      cell.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        onToggleHoliday(iso, holiday);
      });

      grid.append(cell);
    }
  }

  return {
    node: root,
    month: monthKey,
    selected: () => selected,
    setMonthFrom(iso) {
      const date = new Date(`${iso}T00:00:00`);
      cursor = new Date(date.getFullYear(), date.getMonth(), 1);
    },
    update({ days: nextDays, holidays: nextHolidays, selected: nextSelected }) {
      if (nextDays) days = nextDays;
      if (nextHolidays) holidays = nextHolidays;
      if (nextSelected !== undefined) selected = nextSelected;
      render();
    },
  };
}
