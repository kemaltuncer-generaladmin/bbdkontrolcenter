// Zil Sistemi paneli.
//
// EKRANIN ANLATTIĞI ÜÇ ŞEY, BU SIRAYLA:
//
//   1. ÇALIYOR MU?  Üst bant: sıradaki zil (saat, tür, kaç dakika sonra), zil
//      bilgisayarı bağlı mı, son komutu çaldı mı. Arıza varsa kırmızıdır.
//      Çalmayan zil fark edilmeyen arızadır — ekran bunu saklarsa hata
//      haftalarca yaşar.
//   2. PROGRAM.  Haftalık zil programı tek bakışta (gün × saat), önümüzdeki
//      7 gün (Akademi tatil/özel gün) ve son çalmalar — zil bilgisayarının
//      cevabıyla. Sekmeler: Program · Çağrı · Ayarlar (en çok üç).
//   3. AYARLAR.  Ses, metinler, özel gün saatleri, bakım — günlük işin dışı.
//
// 0.5.x YENİDEN TASARIM (13.10.2026, kullanıcı: "görünüm, program, zil
// saatlerinin düzeltilmesi — bilgisayara dönüşü yok, saçma sapan bir şeye
// dönmüş"). Eski ekranın sorunları: sıradaki zil iç gönderim tetiğinden
// okunuyordu ("… · gönderim", bir dakika erken); günlük UTC saat
// gösteriyordu (16:50 zili "13:50"); zil bilgisayarının cevabı hiç
// görünmüyordu; saatler gün sekmelerinin arkasında ve her küçük değişiklik
// ayrı kayıttı; "bu bilgisayardan da çal" sunucuda hiçbir yerde çalmıyordu.
//
// Ortak bileşenler kabuktan gelir (ADR 0011). Yol, panelin KOPYALANMIŞ
// konumuna göredir: shell/panels/bell/ → shell/ui-kit/. Depoda
// '../../ui-kit/' çözülmez; normaldir.

import {
  ago, button, confirmSimple, confirmWithPin, h, loadStyles, menuButton, pollLoop, toaster,
} from '../../ui-kit/kit.js';
import { tabBar } from '../../ui-kit/layout.js';
import { renderCalls } from './calls.js';
import { KIND_LABEL, localClock, parseTime, renderProgram, whenText } from './program.js';
import { renderSettings } from './settings.js';
import * as store from './store.js';

export { parseTime };

/** Zil bilgisayarı bu süre görünmezse bağlı sayılmaz. Sorgulama 3 sn; 90 sn bolca pay. */
const AGENT_STALE_MS = 90_000;

/** Ekran kendini bu sıklıkta tazeler: ajan durumu, son çalma ve ses kuyruğu canlı kalsın. */
const REFRESH_MS = 10_000;

const TABS = [
  { key: 'program', label: 'Program' },
  { key: 'cagri', label: 'Çağrı' },
  { key: 'ayarlar', label: 'Ayarlar' },
];

let ctx = null;
let data = null;
let notify = () => {};
let loop = null;
let disposed = false;
let view = 'program';
const nodes = {};

// --------------------------------------------------------------- yardımcı

async function run(action, { success = '', reload = true } = {}) {
  try {
    const result = await action();
    if (result && result.ok === false) {
      notify(result.detail || 'İşlem yapılamadı.', 'bad');
    } else if (success) {
      notify(typeof success === 'function' ? success(result) : success);
    }
    if (reload) await refresh();
    return result;
  } catch (error) {
    notify(error.message || String(error), 'bad');
    return null;
  }
}

async function refresh() {
  const next = await store.state();
  if (disposed) return;
  data = next;
  render();
}

/** Panel alt dosyalarının ihtiyaç duyduğu tek yüzey. */
function app() {
  return {
    data,
    run,
    notify: (...args) => notify(...args),
    root: nodes.view,
    store,
    rerender: render,
    open: typeof ctx?.open === 'function' ? ctx.open : null,
  };
}

export function agentOnline(state) {
  const agent = state.agent || {};
  if (!agent.online) return false;
  const seen = Date.parse(agent.lastSeen || '');
  return !seen || Number.isNaN(seen) || Date.now() - seen < AGENT_STALE_MS;
}

/**
 * Zil neden çalmaz (kırmızı) ve neye dikkat (sarı)?
 * @returns {{blockers: string[], warnings: string[]}}
 */
export function problems(state) {
  const blockers = [];
  const warnings = [];
  const settings = state.settings || {};
  if (!settings.enabled) blockers.push('Otomatik zil kapalı');
  if (!Object.values(state.times || {}).some((items) => items.length)) {
    blockers.push('Hiç zil saati yok');
  }
  const local = Boolean(settings.playLocally && state.localAvailable);
  if (!agentOnline(state) && !local) {
    blockers.push(state.agent?.error ? `Zil bilgisayarına ulaşılamıyor (${state.agent.error})`
      : 'Zil bilgisayarı görünmüyor');
  }
  const missing = (state.missingVoices || []).length;
  if (missing) warnings.push(`${missing} anons sesi hazırlanıyor`);
  const library = state.library;
  if (library && agentOnline(state) && library.present < library.wanted) {
    warnings.push(`Zil bilgisayarına ${library.wanted - library.present} ses iniyor`);
  }
  return { blockers, warnings };
}

// ---------------------------------------------------------------- üst bant

function renderHero() {
  const hero = h('header', 'bl-hero');
  const schedule = data.schedule || { next: [], days: [] };
  const { blockers, warnings } = problems(data);
  const next = (schedule.next || [])[0];

  // --- 1) sıradaki zil ya da neden çalmaz ---
  const main = h('div', `bl-next${blockers.length ? ' bad' : ''}`);
  if (blockers.length) {
    main.append(h('span', 'bl-next-label', 'Zil çalmaz'));
    main.append(h('b', 'bl-next-why', blockers.join(' · ')));
    if (!data.settings?.enabled) {
      main.append(h('span', 'bl-hint', 'Elle zil bu şaltere bakmaz; düğme çalışsa da otomatik zil çalmaz.'));
    }
  } else if (next) {
    main.append(h('span', 'bl-next-label', 'Sıradaki zil'));
    const line = h('div', 'bl-next-line');
    line.append(h('b', 'bl-next-time', next.time));
    line.append(h('span', `bl-pill k-${next.kind}`, KIND_LABEL[next.kind] || 'Ders'));
    line.append(h('span', 'bl-next-when', whenText(next, schedule.today)));
    main.append(line);
    if (next.label) main.append(h('span', 'bl-hint', next.label));
  } else {
    main.append(h('span', 'bl-next-label', 'Sıradaki zil'));
    main.append(h('b', 'bl-next-why', 'Önümüzdeki 7 günde zil yok'));
  }

  // --- 2) zil bilgisayarı ---
  const facts = h('div', 'bl-facts');
  const online = agentOnline(data);
  const agent = h('div', `bl-fact${online ? ' good' : ' bad'}`);
  agent.append(h('i', 'bl-dot'));
  agent.append(h('span', null, online ? 'Zil bilgisayarı bağlı' : 'Zil bilgisayarı görünmüyor'));
  if (data.agent?.lastSeen) {
    agent.append(h('em', null, ago(data.agent.lastSeen)));
    agent.title = `Son görülme: ${localClock(data.agent.lastSeen, { withDate: true })}`;
  }
  if (data.agent?.error) agent.title = data.agent.error;
  facts.append(agent);

  const ack = data.agentAck;
  if (ack) {
    // Zil bilgisayarının KENDİ cevabı: "çaldı" ya da neden çalmadığı.
    const fact = h('div', `bl-fact${ack.ok ? ' good' : ' warn'}`);
    fact.append(h('span', null, ack.ok ? 'Son komut çaldı'
      : `Son komut çalmadı${ack.detail ? `: ${ack.detail}` : ''}`));
    if (ack.at) fact.append(h('em', null, localClock(ack.at)));
    fact.title = ack.detail || '';
    facts.append(fact);
  }
  if (data.library && online) {
    const full = data.library.present >= data.library.wanted;
    const fact = h('div', `bl-fact${full ? '' : ' warn'}`);
    fact.append(h('span', null, `Sesler ${data.library.present}/${data.library.wanted}`));
    fact.title = 'Zil ve anons sesleri zil bilgisayarında önceden durur; çalma anında indirme beklenmez.';
    facts.append(fact);
  }
  for (const text of warnings) facts.append(h('div', 'bl-fact warn', text));

  // --- 3) eylemler ---
  const actions = h('div', 'bl-actions');
  const master = h('label', 'bl-switch');
  const box = h('input');
  box.type = 'checkbox';
  box.checked = Boolean(data.settings.enabled);
  box.addEventListener('change', async () => {
    if (!box.checked) {
      const yes = await confirmSimple(nodes.view, {
        title: 'Otomatik zil kapatılsın mı?',
        description: 'Siz yeniden açana dek hiçbir gün zil saatleri çalmaz. Yalnız bugün için '
          + '“Bugün zili kapat”ı kullanın.',
        confirmLabel: 'Kapat',
        danger: true,
      });
      if (!yes) { box.checked = true; return; }
    }
    run(() => store.saveSettings({ ...data.settings, enabled: box.checked }),
      { success: box.checked ? 'Otomatik zil açıldı.' : 'Otomatik zil kapatıldı.' });
  });
  master.append(box, h('i', 'bl-switch-track'), h('span', null, 'Otomatik zil'));
  actions.append(master);

  actions.append(button('Şimdi zil çal', {
    variant: 'primary',
    title: 'Zili okulun hoparlöründen hemen çalar. Anons çalmaz.',
    onClick: () => run(store.ring, { success: 'Zil komutu zil bilgisayarına gönderildi.' }),
  }));

  const today = (schedule.days || [])[0];
  const more = [
    today && !today.override && {
      label: 'Bugün zili kapat…',
      title: 'Acil durum: bugün gün sonuna dek zil, anons ve çağrı çalmaz.',
      onClick: async () => {
        const pin = await confirmWithPin(nodes.view, {
          title: 'Bugünün zili kapatılsın mı?',
          description: 'Gün sonuna dek otomatik zil, zil sonrası anons ve oturum çağrıları ÇALMAZ. '
            + 'Yarın program yine geçerlidir. Elle zil ve çağrı çalışmaya devam eder.',
          confirmLabel: 'Bugün zili kapat',
        });
        if (pin) {
          await run(() => store.overrideToday(today.date, pin),
            { success: 'Bugünün zili kapatıldı.' });
        }
      },
    },
    today && today.override && {
      label: 'Bugünün zilini yeniden aç',
      onClick: () => run(() => store.clearOverride(today.date),
        { success: 'Bugünün zili yeniden açıldı.' }),
    },
  ].filter(Boolean);
  if (more.length) actions.append(menuButton('Diğer', more));

  hero.append(main, facts, actions);
  return hero;
}

function overrideBand() {
  const today = (data.schedule?.days || [])[0];
  if (!today?.override) return null;
  const band = h('div', 'bl-alert');
  band.append(h('b', null, 'Bugün zil kapalı.'),
    h('span', null, ' Gün sonuna dek otomatik zil çalmaz; yarın program yine geçerli.'));
  band.append(button('Yeniden aç', {
    onClick: () => run(() => store.clearOverride(today.date),
      { success: 'Bugünün zili yeniden açıldı.' }),
  }));
  return band;
}

// -------------------------------------------------------------------- çizim

function render() {
  if (!nodes.body || !data) return;
  // Yeniden çizimde kaydırma konumu korunur: 10 saniyede bir tazelenen bir
  // ekranda listenin başa fırlaması, kullanıcıyı okuduğu yerden koparır.
  const scroll = nodes.body.scrollTop;
  const band = overrideBand();
  nodes.hero.replaceChildren(renderHero(), ...(band ? [band] : []));

  const surface = app();
  if (view === 'cagri') nodes.body.replaceChildren(renderCalls(surface, nodes));
  else if (view === 'ayarlar') nodes.body.replaceChildren(renderSettings(surface));
  else nodes.body.replaceChildren(renderProgram(surface));
  nodes.body.scrollTop = scroll;
}

/** Yazarken, diyalog ya da çekmece açıkken tazeleme ekranı çizmez. */
function busy() {
  const active = document.activeElement;
  if (active && nodes.view.contains(active)
      && ['INPUT', 'TEXTAREA', 'SELECT'].includes(active.tagName)) return true;
  return Boolean(nodes.view.querySelector('.kit-overlay, .kit-menu-list:not([hidden])'));
}

/**
 * Arayüz yetenekleri (ADR 0031). `bell.status` — Zil Sistemi'nin durumunu
 * başka modüllerin paneli SALT OKUR (ADR 0033 K12: BBD Akademi kurum takvimi
 * ve ders programı eskiden `/api/bell/...` uçlarını doğrudan çağırıyordu).
 * Adres bu modülde kalır; istek çağıranın oturumuyla gider, izin `bell.view`.
 *   state() → GET /api/bell/state   ·   planStatus({from, to}) → GET /api/bell/plan/status
 */
export function capabilities(shell) {
  return {
    'bell.status': {
      state: () => shell.api('/api/bell/state'),
      planStatus: ({ from, to }) => shell.api(`/api/bell/plan/status?from=${from}&to=${to}`),
    },
  };
}

export function mount(root, context) {
  ctx = context;
  disposed = false;
  data = null;
  nodes.pageForm = null;
  loadStyles(import.meta.url);
  store.connect(ctx.api);

  // `kit-panel` paylaşılan renk jetonlarını taşır ve bildirim şeridinin
  // konumlandığı köktür; olmazsa uyarılar tüm pencereye taşar (ADR 0011).
  nodes.view = h('div', 'bl kit-panel');
  nodes.head = h('div', 'bl-head');
  nodes.hero = h('div', 'bl-hero-wrap');
  const tabs = tabBar(TABS, view, (key) => { view = key; render(); });
  nodes.head.append(nodes.hero, tabs.node);
  nodes.body = h('div', 'bl-body kit-body');
  nodes.view.append(nodes.head, nodes.body);
  notify = toaster(nodes.view);

  const loading = h('div', 'bl-loading');
  loading.append(h('span', 'bl-spinner'), h('p', null, 'Zil sistemi okunuyor…'));
  root.replaceChildren(loading);

  (async () => {
    const first = await store.state();
    if (disposed) return;
    data = first;
    root.replaceChildren(nodes.view);
    render();
    // Ajan durumu, son çalma ve ses kuyruğu canlı: ekran açıkken kendini tazeler.
    loop = pollLoop({
      every: REFRESH_MS,
      run: async () => {
        if (busy()) return;
        await refresh();
      },
    });
  })().catch((error) => {
    if (disposed) return;
    const box = h('div', 'bl-empty-state');
    box.append(h('h3', null, 'Zil sistemi açılamadı'));
    box.append(h('p', null, error?.message || String(error)));
    root.replaceChildren(box);
  });

  return () => {
    disposed = true;
    loop?.stop();
    loop = null;
    root.replaceChildren();
  };
}

// HAFTALIK SAATLER İÇİN ARAYÜZ YETENEĞİ YOK — bilerek.
//
// Manifestteki `provides: bell.week` BACKEND yeteneğidir: Ders Takvimi
// modülünün backend'i onu registry'den çözer ve kendi ucundan servis eder.
// `/api/bell/state` köprüye HTTP isteği atıyor (ajan durumu için); ders
// takvimini görmek isteyen bir ekranın bbdstore'a gitmesi için sebep yok.
// Yukarıdaki `bell.status` yalnız bu durumu GERÇEKTEN isteyen ekran içindir
// (Akademi takvim formundaki saat setleri, zil planı kartı) ve tembeldir:
// kabuk yeteneği kaydederken hiçbir istek atılmaz.
