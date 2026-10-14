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
//   · Program şablonları — oluştur / düzenle / sil / sınıflara uygula (timetable-templates.js;
//                          yalnız KM yüzeyi, KM-3)
//
// Tek tek derslerin iptali, telafisi ve yerine öğretmen "Ders oturumları" bölümündedir (sessions.js).
//
// İŞ KURALI AKADEMİ'DE: öğretmen/derslik çakışması, sürüm kilidi, zil saati
// değişince eşleşmeyen satırlar sunucuda belirlenir; panel sonucu gösterir.
//
// Sekmelerin üstünde "Zil planı durumu" kartı (timetable-bell-status.js):
// Akademi'nin ürettiği 14 günlük zil planının KM Zil Sistemi'ne ulaşıp
// ulaşmadığı. Ekran ilk açıldığında TEK istekle okunur.
//
// Derin bağlantılar: `{classGroupId}` o sınıfın ızgarasını, `{lessonEventId}`
// etüt çekmecesini, `{tab: 'week'|'events'|'bells'|'terms'|'templates'}` sekmeyi açar.

import { h, loadStyles } from '../../ui-kit/kit.js';
import { tabBar } from '../../ui-kit/layout.js';
import { sectionHead } from './common.js';
import { weekTab } from './timetable-week.js';
import { eventsTab, openLessonEvent } from './timetable-events.js';
import { bellSlotsTab, termsTab } from './timetable-setup.js';
import { bellPlanCard } from './timetable-bell-status.js';
import { templatesTab } from './timetable-templates.js';

const TABS = [
  { key: 'week', label: 'Haftalık program' },
  { key: 'events', label: 'Etüt ve tek seferlik dersler' },
  { key: 'bells', label: 'Zil dilimleri' },
  { key: 'terms', label: 'Dönemler' },
  { key: 'templates', label: 'Program şablonları' },
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
    templates: () => templatesTab(app),
  };
  const bellStatus = bellPlanCard(app);
  const tabs = tabBar(TABS, 'week', (key) => show(key));
  const slot = h('div', 'ak-tt-tabbody');
  node.append(bellStatus.node, tabs.node, slot);

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
      bellStatus.load();
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
