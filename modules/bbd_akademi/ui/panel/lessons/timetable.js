// BBD Akademi — Ders programı (F-A5, F-A6; hafta takvimi Akademi README §14 madde 125).
//
// KULLANICI (17.10.2026): "ders programı girme kısmı çok karışık … takvim olacak ekranda. bu hafta önceki hafta
// sonraki hafta diye ok tuşları olacak yanında. sınıf seçip program girecez şak şak".
//
// TEK EKRAN: açılışta yalnız HAFTA TAKVİMİ (timetable-calendar.js) görünür — ‹ Önceki · Bu hafta · Sonraki ›,
// sınıf seçici, gün × zil dilimi takvimi. Sekme şeridi YOK. Ara sıra gereken işler takvimin "Diğer" menüsündedir
// ve takvimin yerine açılır ("‹ Takvime dön" ile geri):
//   · Etüt ve tek seferlik dersler (timetable-events.js; talep kuyruğu Talepler bölümünde)
//   · Zil saatleri — KM aynası ya da yerel düzenleyici + zil planı durumu kartı (timetable-setup.js,
//     timetable-bell-status.js; kart eskiden takvimin üstündeydi — teknik ayrıntı, buraya alındı)
//   · Dönemler (timetable-setup.js)
//   · Program şablonları (timetable-templates.js; takvimde "Şablondan doldur" bunları kullanır)
//
// ESKİ "Haftalık program" düzenleyicisi (geçerlilik başlangıcı / bitişi, sürüm, taslak + Kaydet şeridi,
// timetable-week.js) KALDIRILDI: kavramları (geçerlilik aralığı, sürüm) kullanıcıya gösteriyordu. Aynı iş artık
// hücre hücre ve "yalnız bu hafta / sonrakiler" sorusuyla yapılır; satır bölme Akademi'dedir.
//
// Tek tek derslerin iptali, telafisi ve yerine öğretmen "Ders oturumları" bölümündedir (sessions.js).
//
// Derin bağlantılar: `{classGroupId, week?}` o sınıfın takvimini (haftasını) açar, `{lessonEventId}` etüt
// çekmecesini, `{tab: 'week'|'events'|'bells'|'terms'|'templates'}` görünümü açar.

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { sectionHead } from '../common.js';
import { calendarTab } from './timetable-calendar.js';
import { eventsTab, openLessonEvent } from './timetable-events.js';
import { bellSlotsTab, termsTab } from './timetable-setup.js';
import { bellPlanCard } from './timetable-bell-status.js';
import { templatesTab } from './timetable-templates.js';

const VIEWS = {
  week: 'Haftalık takvim',
  events: 'Etüt ve tek seferlik dersler',
  bells: 'Zil saatleri',
  terms: 'Dönemler',
  templates: 'Program şablonları',
};

export function section(app) {
  loadStyles(import.meta.url, 'timetable.css');
  const node = h('div', 'ak-stack ak-tt');
  // Etüt / zil / dönem / şablon ekranda göze batmaz: takvimin "Diğer" menüsündedir (lider mesajı 2).
  node.append(sectionHead('Ders programı', {
    subtitle: 'Sınıf seçin, haftayı okla değiştirin, hücreye tıklayıp dersi girin. Her hafta kendi programıdır.' }));

  const back = h('div', 'ak-tt-back');
  const backButton = button('‹ Takvime dön', { variant: 'ghost', onClick: () => go('week') });
  const backTitle = h('b', 'ak-tt-back-title');
  back.append(backButton, backTitle);
  back.hidden = true;

  const bodies = {};
  const builders = {
    week: () => calendarTab(app, { openBells: () => go('bells'), openView: (key) => go(key) }),
    events: () => eventsTab(app),
    bells: () => {
      // Zil saatleri + zil planının KM'ye ulaşıp ulaşmadığı (eskiden takvimin üstündeydi).
      const status = bellPlanCard(app);
      const inner = bellSlotsTab(app, { onChanged: () => bodies.week?.bellsChanged() });
      const wrap = h('div', 'ak-stack');
      wrap.append(status.node, inner.node);
      return { node: wrap, reload: () => { status.load(); return inner.reload(); }, destroy: () => inner.destroy?.() };
    },
    terms: () => termsTab(app),
    templates: () => templatesTab(app),
  };
  const slot = h('div', 'ak-tt-tabbody');
  node.append(back, slot);

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    back.hidden = key === 'week';
    backTitle.textContent = key === 'week' ? '' : VIEWS[key];
    return bodies[key];
  }

  function go(key) {
    return show(builders[key] ? key : 'week');
  }

  /** Etüt çekmecesi (derin bağlantı); değişince açık liste tazelenir. */
  function openEvent(id) {
    openLessonEvent(app, id, {
      onChange: () => bodies.events?.reload(),
      onRemove: () => bodies.events?.reload(),
    });
  }

  /** Takvimi yüklemeden kur (derin bağlantı sınıfı seçsin diye). */
  function ensureWeek() {
    if (!bodies.week) {
      bodies.week = builders.week();
      bodies.week.node.hidden = true;
      slot.append(bodies.week.node);
    }
    return bodies.week;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const tab = payload?.tab && builders[payload.tab] ? payload.tab : null;
      if (payload?.classGroupId) {
        const week = ensureWeek();
        week.open(payload.classGroupId, payload.week || null);
        go('week');
        week.reload();
      } else if (payload?.lessonEventId) {
        go(tab || 'events');
        if (bodies.events) bodies.events.open(payload.lessonEventId);
        else openEvent(payload.lessonEventId);
      } else if (tab) {
        go(tab);
      } else if (!started) {
        go('week');
      }
      started = true;
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}
