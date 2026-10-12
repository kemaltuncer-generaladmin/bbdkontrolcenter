// BBD Akademi — Ders programı (F-A5, F-A6).
//
// Dört sekme, her biri ilk açıldığında kurulur ve veri çeker:
//   · Haftalık program   — sınıf × gün × zil dilimi ızgarası (timetable-week.js),
//                          haftayı kopyala (timetable-copy.js)
//   · Etüt ve tek seferlik dersler — liste, ayrıntı, oluştur/değiştir/iptal/sil
//                          (timetable-events.js); talep kuyruğu Talepler bölümünde
//   · Zil dilimleri      — KM aynası (salt okunur + "şimdi çek") ya da yerel
//                          düzenleyici (timetable-setup.js)
//   · Dönemler           — ad + tarih aralığı (timetable-setup.js)
//
// İŞ KURALI AKADEMİ'DE: öğretmen/derslik çakışması, sürüm kilidi, zil saati
// değişince eşleşmeyen satırlar sunucuda belirlenir; panel sonucu gösterir.
//
// Derin bağlantılar: `{classGroupId}` o sınıfın ızgarasını, `{lessonEventId}`
// etüt çekmecesini, `{tab: 'week'|'events'|'bells'|'terms'}` sekmeyi açar.

import { h, loadStyles } from '../../ui-kit/kit.js';
import { tabBar } from '../../ui-kit/layout.js';
import { sectionHead } from './common.js';
import { weekTab } from './timetable-week.js';
import { eventsTab, openLessonEvent } from './timetable-events.js';
import { bellSlotsTab, termsTab } from './timetable-setup.js';

const TABS = [
  { key: 'week', label: 'Haftalık program' },
  { key: 'events', label: 'Etüt ve tek seferlik dersler' },
  { key: 'bells', label: 'Zil dilimleri' },
  { key: 'terms', label: 'Dönemler' },
];

export function section(app) {
  loadStyles(import.meta.url, 'timetable.css');
  const node = h('div', 'ak-stack ak-tt');
  node.append(sectionHead('Ders programı', {
    subtitle: 'Sınıfların haftalık programı, etüt ve tek seferlik dersler, zil dilimleri ve dönemler.' }));

  const bodies = {};
  const builders = {
    week: () => weekTab(app, { openEvent: (id) => openEvent(id), openBells: () => go('bells') }),
    events: () => eventsTab(app),
    bells: () => bellSlotsTab(app, { onChanged: () => bodies.week?.bellsChanged() }),
    terms: () => termsTab(app),
  };
  const tabs = tabBar(TABS, 'week', (key) => show(key));
  const slot = h('div', 'ak-tt-tabbody');
  node.append(tabs.node, slot);

  function show(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    return bodies[key];
  }

  /** Sekmeyi programla aç (şerit + gövde birlikte). */
  function go(key) {
    tabs.select(key, false);
    return show(key);
  }

  /** Etüt çekmecesi (çakışma kutusu, derin bağlantı); değişince açık liste tazelenir. */
  function openEvent(id) {
    openLessonEvent(app, id, {
      onChange: () => bodies.events?.reload(),
      onRemove: () => bodies.events?.reload(),
    });
  }

  /** Izgara gövdesini yüklemeden kur (derin bağlantı sınıfı seçsin diye). */
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
        // Izgara ilk kez açılıyorsa ilk yükleme doğrudan bu sınıfla başlar.
        ensureWeek().open(payload.classGroupId);
        go('week');
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
