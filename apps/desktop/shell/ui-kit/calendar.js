// Ay takvimi — gün ızgarası ve gün başına rozet.
//
// TEK KOPYA (ADR 0011). Kaynak `modules/bbd_lunch/ui/panel/calendar.js`:
// şekli doğruydu (Pazartesi-ilk, sabit 42 hücre, ISO anahtarlı gün sözlüğü,
// sağ tıkla tatil) ama kendi `kit.js` KOPYASINDAN import ediyordu ve rozeti
// yemek kaydına göre çiziyordu. Burası TERFİ ETMİŞ ve GENELLEŞTİRİLMİŞ
// hâlidir; `bbd_lunch` kendi dosyasıyla çalışmaya devam eder (BBD panelleri
// kite taşınmadı — README "Mevcut BBD panelleri").
//
// GENELLEŞTİRME NEREDE: rozeti bu dosya çizmez. Çağıran `renderBadge(iso,
// info)` verir; menü ekranı "yayınlandı/taslak/yok", stok ekranı "kalan
// porsiyon", yemek ekranı "işlenen porsiyon" gösterir. Takvimin bileceği tek
// şey hangi günde bir şey OLDUĞUdur, ne olduğu değil.
//
// RENK TEK BAŞINA ANLAM TAŞIMAZ (kit kuralı 7). Bu yüzden `legend` bir
// SEÇENEK değil, sözleşmenin parçasıdır: rozet çizen çağıran, o rozetin ne
// demek olduğunu yazan açıklamayı da verir. Takvimin KENDİ boyadığı gri
// hücrelerin (hafta sonu / tatil) açıklaması buraya gömülüdür ve her zaman
// yazılır.
//
// AY DEĞİŞİMİ AYRI GERİ ÇAĞRIDIR. Kaynakta ay okları `onPick(null, month)`
// çağırıyordu ve çağıran her seçim işlemine `if (day)` diye başlamak
// zorundaydı — "seçim yok" bilgisini seçim geri çağrısıyla taşımak, iki ayrı
// olayı tek kapıdan geçirmekti. Burada ay gezinmesi `onMonth(monthKey)`,
// gün seçimi `onPick(iso, monthKey)`.

import { dayNames, h, isoOf, longDate, monthName, todayIso } from './kit.js';

export const CALENDAR_VERSION = '1.0.0';

const pad = (value) => String(value).padStart(2, '0');

/** Varsayılan rozet: sayı ya da kısa metin. Nesne gelirse çağıran çizmeli. */
function defaultBadge(_iso, info) {
  if (info === null || info === undefined || info === '') return null;
  if (typeof info === 'number' || typeof info === 'string') return String(info);
  return null;
}

/**
 * Ay takvimi kurar.
 *
 * @param {object} spec
 * @param {string}  [spec.month]      — "YYYY-MM"; boşsa içinde bulunulan ay
 * @param {string}  [spec.selected]   — ISO gün
 * @param {boolean} [spec.weekend]    — Cmt/Paz gri boyansın mı (varsayılan evet)
 * @param {(iso:string, monthKey:string)=>void} [spec.onPick]
 * @param {(monthKey:string)=>void}    [spec.onMonth]
 * @param {(iso:string, isHoliday:boolean)=>void} [spec.onToggleHoliday]
 *   — verilmezse sağ tık menüsü hiç bağlanmaz
 * @param {(iso:string, info:any)=>(Node|string|number|null)} [spec.renderBadge]
 * @param {(iso:string, info:any)=>string} [spec.dayTitle] — hücre ipucu
 * @param {{text:string, tone?:string}[]} [spec.legend] — rozetlerin açıklaması
 * @returns {{node, month, selected, setMonthFrom, update, destroy}}
 */
export function monthCalendar({
  month = '',
  selected = null,
  weekend = true,
  onPick,
  onMonth,
  onToggleHoliday,
  renderBadge = defaultBadge,
  dayTitle,
  legend = [],
} = {}) {
  const startFrom = /^\d{4}-\d{2}$/.test(month)
    ? new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1)
    : new Date();

  let cursor = new Date(startFrom.getFullYear(), startFrom.getMonth(), 1);
  let current = selected;        // seçili ISO gün
  let days = {};                 // ISO → çağıranın rozet bilgisi
  let holidays = {};             // ISO → etiket

  const root = h('div', 'kit-cal');
  const head = h('div', 'kit-cal-head');
  const title = h('div', 'kit-cal-title');
  const nav = h('div', 'kit-cal-nav');

  const monthKey = () => `${cursor.getFullYear()}-${pad(cursor.getMonth() + 1)}`;

  const navButton = (label, hint, step) => {
    const node = h('button', 'kit-cal-nav-btn', label);
    node.type = 'button';
    node.title = hint;
    node.setAttribute('aria-label', hint);
    node.addEventListener('click', () => {
      cursor = new Date(cursor.getFullYear(), cursor.getMonth() + step, 1);
      render();
      onMonth?.(monthKey());
    });
    return node;
  };

  const todayButton = h('button', 'kit-cal-nav-btn', 'Bugün');
  todayButton.type = 'button';
  todayButton.title = 'Bu aya ve bugüne dön';
  todayButton.addEventListener('click', () => {
    const iso = todayIso();
    const before = monthKey();
    const now = new Date();
    cursor = new Date(now.getFullYear(), now.getMonth(), 1);
    current = iso;
    render();
    // Ay DEĞİŞTİYSE önce veri istenir, sonra seçim bildirilir: sıra ters
    // olsaydı çağıran, elindeki eski ayın verisiyle yeni günü çizmeye
    // çalışırdı.
    if (before !== monthKey()) onMonth?.(monthKey());
    onPick?.(iso, monthKey());
  });

  nav.append(navButton('‹', 'Önceki ay', -1), todayButton, navButton('›', 'Sonraki ay', 1));
  head.append(title, nav);

  const weekRow = h('div', 'kit-cal-week');
  for (const name of dayNames()) weekRow.append(h('div', 'kit-cal-week-cell', name));

  const grid = h('div', 'kit-cal-grid');

  const legendRow = h('div', 'kit-cal-legend');
  for (const item of legend) {
    legendRow.append(h('span', `kit-cal-legend-item${item.tone ? ` ${item.tone}` : ''}`, item.text));
  }
  // Takvimin KENDİ boyadığı gri hücrelerin karşılığı: rozeti çağıran çizse de
  // bu açıklama her zaman burada durur, çünkü griyi çizen bu dosyadır.
  legendRow.append(h('span', 'kit-cal-legend-item muted',
    weekend ? 'gri = tatil / hafta sonu' : 'gri = tatil'));

  root.append(head, weekRow, grid, legendRow);

  function render() {
    title.textContent = `${monthName(cursor.getMonth())} ${cursor.getFullYear()}`;
    grid.replaceChildren();

    const first = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
    // Hafta pazartesi başlar: JS'te 0=Pazar olduğu için kaydırıyoruz.
    const lead = (first.getDay() + 6) % 7;
    const start = new Date(first);
    start.setDate(1 - lead);

    const now = todayIso();

    // SABİT 42 HÜCRE: ay 4, 5 ya da 6 satıra düşebiliyor ve ızgara her ay
    // boy değiştirirse altındaki içerik zıplıyor.
    for (let index = 0; index < 42; index += 1) {
      const date = new Date(start);
      date.setDate(start.getDate() + index);
      const iso = isoOf(date);
      const outside = date.getMonth() !== cursor.getMonth();
      const isWeekend = weekend && (date.getDay() === 0 || date.getDay() === 6);
      const holiday = Object.hasOwn(holidays, iso);
      const info = days[iso];

      const cell = h('button', 'kit-cal-cell');
      cell.type = 'button';
      if (outside) cell.classList.add('out');
      if (isWeekend || holiday) cell.classList.add('off');
      if (iso === now) cell.classList.add('today');
      if (iso === current) cell.classList.add('on');
      cell.dataset.day = iso;
      // Ekran okuyucu "14" değil "14 Ağustos 2026 Cuma" okur; ızgarada sayı
      // tek başına hangi gün olduğunu söylemiyor.
      cell.setAttribute('aria-label', longDate(iso));
      cell.setAttribute('aria-pressed', iso === current ? 'true' : 'false');
      if (iso === now) cell.setAttribute('aria-current', 'date');

      cell.append(h('span', 'kit-cal-day', String(date.getDate())));

      const drawn = renderBadge?.(iso, info);
      if (drawn instanceof Node) {
        cell.append(drawn);
      } else if (drawn !== null && drawn !== undefined && drawn !== '') {
        cell.append(h('span', 'kit-cal-badge', String(drawn)));
      }

      const hint = dayTitle?.(iso, info);
      if (hint) cell.title = hint;
      if (holiday) {
        // Tatil etiketi ipucunun yerini ALMAZ, önüne geçer: ikisi de varsa
        // kullanıcı önce "neden kapalı" sorusunun cevabını görmeli.
        cell.title = holidays[iso] || 'Tatil';
        cell.append(h('span', 'kit-cal-off-dot', ''));
      }

      cell.addEventListener('click', () => {
        current = iso;
        render();
        onPick?.(iso, monthKey());
      });
      if (onToggleHoliday) {
        // Sağ tık: tatil işaretle / kaldır — akışı kesmeden.
        cell.addEventListener('contextmenu', (event) => {
          event.preventDefault();
          onToggleHoliday(iso, holiday);
        });
      }

      grid.append(cell);
    }
  }

  render();

  return {
    node: root,
    /** Görüntülenen ay — "YYYY-MM". Veri isteği bu anahtarla yapılır. */
    month: monthKey,
    selected: () => current,
    /** Verilen günün ayına atlar. Geri çağrı TETİKLENMEZ: bu programlı bir hamledir. */
    setMonthFrom(iso) {
      const date = new Date(`${iso}T00:00:00`);
      if (Number.isNaN(date.getTime())) return;
      cursor = new Date(date.getFullYear(), date.getMonth(), 1);
      render();
    },
    /** Veri geldiğinde çağrılır; verilmeyen alan olduğu gibi kalır. */
    update({ days: nextDays, holidays: nextHolidays, selected: nextSelected } = {}) {
      if (nextDays) days = nextDays;
      if (nextHolidays) holidays = nextHolidays;
      if (nextSelected !== undefined) current = nextSelected;
      render();
    },
    /**
     * Takvim GLOBAL dinleyici tutmaz — bütün olaylar kendi hücrelerinde.
     * Yine de kitin temizlik sözleşmesine uysun diye var: paneller
     * `destroy()` çağırmayı alışkanlık hâline getirsin, hangi bileşenin
     * dinleyici tuttuğunu ezberlemek zorunda kalmasın.
     */
    destroy() {
      grid.replaceChildren();
      days = {};
      holidays = {};
    },
  };
}
