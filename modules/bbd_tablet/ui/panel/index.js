// Öğrenci Tablet Yönetimi — Kontrol Merkezi'nden yönetilen paylaşımlı okul
// tabletleri.
//
// NE YAPAR: tabletleri canlı izler ve uzaktan yönetir (kilit, duyuru, komut,
// profil, oturum kapatma), profilleri düzenler (uygulama kuralları, kullanım
// saatleri, sistem kısıtlamaları), öğrenciye ek süre verir ya da tablet
// kullanımını kısıtlar ve günlük kullanım raporunu gösterir. Uygulamalar
// sekmesinden APK yüklenir ve tabletlere sırayla dağıtılır (TabletKontrol'ün
// kendi güncellemesi dahil).
//
// SUNUCUYU YORMAZ: Kontrol Merkezi bbdstore ile aynı sunucuda koşar. Ekran
// 30 saniyede bir TEK bir özet isteği (`/overview`) atar ve sekme gizliyken
// durur (`pollLoop`). Kantin öğrenci listesi yalnız Öğrenciler sekmesi
// açıldığında ve en çok 5 dakikada bir okunur; tabletin uygulama listesi 5
// dakika bellekte tutulur. Uygulamalar sekmesi yalnız açıkken kendi listesini
// okur (indiren tablet varsa 15, yoksa 30 saniyede bir). Çekmece ve formlar
// yoklamada yeniden kurulmaz, yalnız değişen parçası boyanır — yazılan metin
// kaybolmaz.
//
// YETKİ: düğmeler `/auth/me` izinlerine göre çizilir; yetki yoksa gerekçeli
// kapalı düğme (`blockedButton`). Asıl kapı backend'dedir (K9). İzinler
// okunamazsa düğmeler açık çizilir ve karar backend'e kalır.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/index.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, h, loadStyles, num, pollLoop, toaster } from '../../ui-kit/kit.js';
import { emptyState, kpiRow, skeletonRows, tabBar } from '../../ui-kit/layout.js';
import { appsView } from './apps.js';
import { closeAllDialogs, problem } from './common.js';
import { devicesView } from './devices.js';
import { profilesView } from './profiles.js';
import { studentsView } from './students.js';

const BASE = '/api/bbd_tablet';
const POLL_MS = 30_000;
const STUDENTS_TTL_MS = 5 * 60_000;
const INVENTORY_TTL_MS = 5 * 60_000;
const MANAGED_TTL_MS = 60_000;

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA

  const view = h('div', 'kit-panel tb');   // 'kit-panel' ZORUNLU + kendi önekimiz
  const toast = toaster(view);
  const disposers = [];
  let disposed = false;
  let permissions = null;               // null = okunamadı, karar backend'de
  let inflight = null;

  const state = {
    summary: {}, profiles: [], devices: [], sessions: [],
    students: null, studentsError: '', studentsAt: 0, studentsWanted: false,
    loadedAt: 0, loadError: '', skew: 0,
  };
  const inventory = new Map();          // deviceId → {at, promise}
  let managed = null;                   // {at, promise} — dağıtılan uygulamalar

  const app = {
    root: view,
    state,
    toast,
    call: (path, options) => ctx.api(`${BASE}${path}`, options),
    can(permission) {
      if (permissions === null) return true;
      const key = `bbd_tablet.${permission}`;
      return permissions.has(key) || permissions.has(`${key}:*`);
    },
    device: (id) => state.devices.find((item) => item.id === id) || null,
    profile: (id) => state.profiles.find((item) => item.id === id) || null,
    sessionOf: (studentId) => state.sessions.find((item) => String(item.student_id) === String(studentId)) || null,
    /** Sunucu saatine göre şimdi — geri sayım bilgisayarın saati kaysa da doğru kalır. */
    now: () => Date.now() + state.skew,
    refresh: (options) => refresh(options),
    loadStudents: () => loadStudents(),
    /** Tabletin yüklü uygulamaları; 5 dakika bellekte. */
    inventory(deviceId, { fresh = false } = {}) {
      const cached = inventory.get(deviceId);
      if (!fresh && cached && Date.now() - cached.at < INVENTORY_TTL_MS) return cached.promise;
      const promise = app.call(`/devices/${encodeURIComponent(deviceId)}/apps`)
        .then((rows) => (Array.isArray(rows) ? rows : []));
      promise.catch(() => inventory.delete(deviceId));
      inventory.set(deviceId, { at: Date.now(), promise });
      return promise;
    },
    forgetInventory: (deviceId) => inventory.delete(deviceId),
    /** Dağıtılan uygulamalar (profil düzenleyicisi için); 1 dakika bellekte. */
    managedApps() {
      if (managed && Date.now() - managed.at < MANAGED_TTL_MS) return managed.promise;
      const promise = app.call('/apps').then((data) => (Array.isArray(data?.apps) ? data.apps : []));
      promise.catch(() => { managed = null; });
      managed = { at: Date.now(), promise };
      return promise;
    },
    forgetManagedApps: () => { managed = null; },
    openDevice: (id) => { tabs.select('devices'); views.devices?.open(id); },
    openProfile: (id) => { tabs.select('profiles'); views.profiles?.open(id); },
    showStudent: (id) => { tabs.select('students'); views.students?.open(id); },
    onDispose: (fn) => disposers.push(fn),
  };

  // ------------------------------------------------------------- iskelet
  const head = h('div', 'tb-head');
  const kpiSlot = h('div', 'tb-kpi');
  const tools = h('div', 'tb-tools');
  const updated = h('span', 'tb-updated');
  updated.setAttribute('aria-live', 'polite');
  const refreshButton = button('Yenile', {
    variant: 'ghost',
    title: 'Tabletlerin son durumunu şimdi oku',
    onClick: () => refresh({ manual: true }),
  });
  tools.append(updated, refreshButton);
  head.append(kpiSlot, tools);

  const tabs = tabBar([
    { key: 'devices', label: 'Tabletler' },
    { key: 'profiles', label: 'Profiller' },
    { key: 'students', label: 'Öğrenciler' },
    { key: 'apps', label: 'Uygulamalar' },
  ], 'devices', (key) => show(key));

  const main = h('div', 'tb-main');
  main.append(skeletonRows(6, 6));
  view.append(head, tabs.node, main);
  root.replaceChildren(view);

  const views = {};

  function show(key) {
    if (!views.devices) return;
    for (const [name, item] of Object.entries(views)) item.node.hidden = name !== key;
    views[key]?.shown?.();
  }

  // --------------------------------------------------------------- veri
  async function loadPermissions() {
    try {
      const me = await ctx.api('/auth/me');
      permissions = new Set(Array.isArray(me?.permissions) ? me.permissions : []);
    } catch {
      permissions = null;
    }
  }

  async function loadStudents() {
    state.studentsWanted = true;
    try {
      const rows = await app.call('/students');
      state.students = Array.isArray(rows) ? rows : [];
      state.studentsError = '';
    } catch (error) {
      state.studentsError = error?.status === 503
        ? 'Kantin öğrenci listesine şu an ulaşılamıyor.'
        : problem(error, 'Öğrenci listesi alınamadı.');
    }
    state.studentsAt = Date.now();
    if (!disposed) views.students?.update();
  }

  async function fetchOverview() {
    try {
      const data = await app.call('/overview');
      state.summary = data?.summary || {};
      state.profiles = Array.isArray(data?.profiles) ? data.profiles : [];
      state.devices = Array.isArray(data?.devices) ? data.devices : [];
      state.sessions = Array.isArray(data?.sessions) ? data.sessions : [];
      const server = Date.parse(String(data?.serverTime || '').replace(/(\.\d{3})\d+/, '$1'));
      state.skew = Number.isNaN(server) ? 0 : server - Date.now();
      state.loadedAt = Date.now();
      state.loadError = '';
    } catch (error) {
      state.loadError = problem(error, 'Tablet bilgileri alınamadı.');
    }
  }

  /**
   * Özeti okur ve ekranı boyar.
   *
   * Yoklama (`poll`) süren bir okumaya ortak olur, ikinci istek atmaz. Bir
   * değişiklikten sonra çağrılan tazeleme ise süren okumayı BEKLER ve yenisini
   * başlatır: değişiklikten önce yola çıkmış bir yanıt, az önce kaydedilen
   * profili eski hâliyle geri çizerdi.
   */
  async function refresh({ manual = false, poll = false } = {}) {
    if (inflight && poll) {
      await inflight;
    } else {
      if (inflight) await inflight;
      const run = fetchOverview().finally(() => { if (inflight === run) inflight = null; });
      inflight = run;
      await run;
    }
    if (disposed) return;
    const studentsDue = state.studentsWanted
      && (manual || Date.now() - state.studentsAt > STUDENTS_TTL_MS);
    if (studentsDue) await loadStudents();
    if (disposed) return;
    paint();
    if (manual && state.loadError) toast(state.loadError, 'bad');
  }

  // ------------------------------------------------------------- çizim
  function paintHead() {
    const summary = state.summary || {};
    const behind = Number(summary.behindPolicy || 0);
    kpiSlot.replaceChildren(kpiRow([
      { label: 'Çevrimiçi tablet', value: `${num(summary.online || 0)}/${num(summary.tabletsTotal || 0)}` },
      { label: 'Açık oturum', value: num(summary.activeSessions || 0) },
      { label: 'Eşleme bekleyen', value: num(summary.pendingPairing || 0) },
      { label: 'Profili güncel olmayan', value: num(behind), tone: behind ? 'warn' : '',
        title: 'Profilin son hâlini henüz almamış tablet. Tablet bağlanınca kendiliğinden alır.' },
    ]));
    const stamp = state.loadedAt
      ? new Date(state.loadedAt).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })
      : '';
    updated.classList.toggle('bad', Boolean(state.loadError));
    updated.textContent = state.loadError
      ? (stamp ? `Bağlantı sorunu · ${stamp} bilgileri gösteriliyor` : state.loadError)
      : (stamp ? `Son güncelleme ${stamp}` : '');
  }

  function paint() {
    paintHead();
    if (!views.devices) {
      // İlk okuma hiç başarılı olmadıysa boş sekmeler yerine sebep yazılır;
      // yoklama sürer ve bağlantı gelince ekran kendiliğinden kurulur.
      if (!state.loadedAt) {
        failed();
        return;
      }
      build();
    }
    views.devices.update();
    views.profiles.update();
    views.students.update();
    views.apps.update();
  }

  function failed() {
    main.replaceChildren(emptyState({
      title: 'Tablet bilgileri alınamadı',
      text: `${state.loadError} Bağlantı gelince ekran kendiliğinden dolar.`,
      actions: [button('Tekrar dene', { onClick: () => refresh({ manual: true }) })],
    }));
  }

  function build() {
    views.devices = devicesView(app);
    views.profiles = profilesView(app);
    views.students = studentsView(app);
    views.apps = appsView(app);
    main.replaceChildren(views.devices.node, views.profiles.node, views.students.node,
      views.apps.node);
    show(tabs.active);
  }

  const poll = pollLoop({ every: POLL_MS, run: () => refresh({ poll: true }) });
  loadPermissions().then(() => refresh());

  return () => {
    disposed = true;
    poll.stop();
    closeAllDialogs();
    for (const item of Object.values(views)) item.destroy?.();
    for (const fn of disposers.splice(0)) {
      try { fn(); } catch { /* temizlik bir hatayla yarıda kalmaz */ }
    }
    root.replaceChildren();
  };
}
