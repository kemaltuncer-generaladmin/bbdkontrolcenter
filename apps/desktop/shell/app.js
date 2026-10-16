// Kontrol Merkezi — kabuk davranışı.
//
// Kabuk hiçbir modülün adını bilmez (K1): menü, çekirdeğin verdiği kayıttan
// kurulur. Giriş de çekirdeğe aittir — PIN sidecar'daki `POST /api/auth/login`
// ucunda Argon2id ile doğrulanır, kabuk yalnızca sorar ve belirteci taşır.
//
// ADR 0016 (kişiye özel şifreyle giriş) REDDEDİLDİ: giriş yine 6 haneli PIN'dir
// ve ekran tuş takımıdır. Uçtaki alan adı (`password`) ve `users.set_password`
// izni, göç zaten koştuğu için olduğu gibi bırakıldı — ad şifre der, kural
// PIN'dir (docs/adr/0016-giris-sifre-ile.md — Neden reddedildi).

import { iconSvg } from './icons.js';
import {
  api, bypassCacheOnce, clearResponseCache, connection, loadRegistry, login, logout, menuPanels, mountPanel, navTree,
  onConnectionRestored, preloadPanel, resetProviders, searchProviders, setNavigator, waitForCore,
} from './ui-kernel.js';
import { createGlobalSearch } from './global-search.js';
import {
  applyAllPrefs, closeDomMenu, defineCommand, exportTable, installNative, invoke, isMac, isNative,
  keyLabel, prefs, runCommand, shellToast, syncMenu, zoomStep,
} from './native.js';
import { createFindBar } from './findbar.js';
import { closeDialog, openAbout, openPerf, openPreferences, openShortcuts } from './dialogs.js';
import { perf } from './perf.js';
import { createUpdater } from './update.js';
import { notify } from './ui-kit/native.js';
import { closeListbox, enhanceSelects } from './ui-kit/select.js';

// İlk boyadan önce uygulanmış tercihler (boot.js) burada tamamlanır: Rust
// tarafı (başlık çubuğu teması, kalıcı yakınlaştırma) da eşitlenir.
applyAllPrefs();

const PIN_MIN = 6;
const PIN_MAX = 12; // yerleşim sınırı; sözleşmede üst sınır yok

const el = {
  login: document.getElementById('login'),
  slots: document.getElementById('slots'),
  error: document.getElementById('error'),
  loginHint: document.getElementById('login-hint'),
  workspace: document.getElementById('workspace'),
  nav: document.getElementById('nav'),
  filter: document.getElementById('filter'),
  crumb: document.getElementById('crumb'),
  title: document.getElementById('panel-title'),
  foot: document.querySelector('.sidebar-foot'),
};

function fold(text) {
  return text
    .toLocaleLowerCase('tr')
    .replaceAll('ı', 'i').replaceAll('ş', 's').replaceAll('ğ', 'g')
    .replaceAll('ü', 'u').replaceAll('ö', 'o').replaceAll('ç', 'c');
}

const shell = {
  registry: { groups: [], panels: [] },
  // `panels`: kayıttaki BÜTÜN ekranlar (açılabilir — `ctx.open`, derin bağlantı);
  // `menu`: menüde ÇİZİLENLER (işi başka ekrana taşınanlar düşer — ADR 0031).
  panels: [],
  menu: [],
  activeId: null,
  activePayload: null,
  cleanup: null,
  user: null,
  // ADR 0032 — gezinme geçmişi (Geri/İleri), açılmış ekranlar (geri dönüş
  // ölçüsü), kaydırma hafızası, açılış sayacı.
  history: { stack: [], index: -1 },
  visited: new Set(),
  scrolls: new Map(),
  mountSeq: 0,
  interacted: false,
};

// ======================================================== ADR 0032 yardımcıları

/** Sık kullanılan ekranlar — kullanıcıya özel sayaç (Ctrl/⌘+1…9, Git menüsü). */
function freqKey() {
  const who = shell.user?.id ?? shell.user?.fullName ?? 'anon';
  return `km.freq.${who}`;
}

function readFreq() {
  try {
    const raw = JSON.parse(localStorage.getItem(freqKey()) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

function countVisit(id) {
  const freq = readFreq();
  const entry = freq[id] || { n: 0, last: 0 };
  freq[id] = { n: entry.n + 1, last: Date.now() };
  try {
    localStorage.setItem(freqKey(), JSON.stringify(freq));
    localStorage.setItem(`${freqKey()}.last`, id);
  } catch { /* hatırlamaz, çalışır */ }
}

/** Menüde görünen ekranlardan en sık açılan 9'u (eşitlikte en son açılan). */
function frequentScreens() {
  const freq = readFreq();
  const visible = new Set(shell.menu.map((panel) => panel.id));
  return Object.entries(freq)
    .filter(([id]) => visible.has(id))
    .sort((a, b) => b[1].n - a[1].n || b[1].last - a[1].last)
    .slice(0, 9)
    .map(([id]) => shell.menu.find((panel) => panel.id === id));
}

function lastScreen() {
  try {
    return localStorage.getItem(`${freqKey()}.last`);
  } catch {
    return null;
  }
}

function updateMenu() {
  syncMenu({
    signedIn: Boolean(shell.user),
    screens: shell.user ? frequentScreens().map((panel) => panel.title) : [],
    canBack: shell.history.index > 0,
    canForward: shell.history.index < shell.history.stack.length - 1,
  });
}

function pushHistory(id, payload) {
  const h = shell.history;
  const top = h.stack[h.index];
  if (top && top.id === id && top.payload === payload) return;
  h.stack = h.stack.slice(0, h.index + 1);
  h.stack.push({ id, payload });
  if (h.stack.length > 60) h.stack.shift();
  h.index = h.stack.length - 1;
}

function goHistory(delta) {
  const h = shell.history;
  const next = h.index + delta;
  if (next < 0 || next >= h.stack.length) return;
  h.index = next;
  const entry = h.stack[next];
  select(entry.id, { payload: entry.payload, force: true, viaHistory: true, scroll: true });
}

/** Ekrandaki kaydırılmış alanlar: gövdeden çocuk sırasıyla yol + konum. */
function rememberScroll(id) {
  const body = document.getElementById('panel-body');
  if (!id || !body) return;
  const out = [];
  const pathOf = (node) => {
    const path = [];
    for (let cur = node; cur && cur !== body; cur = cur.parentElement) {
      path.unshift([...cur.parentElement.children].indexOf(cur));
    }
    return path;
  };
  if (body.scrollTop > 0) out.push({ path: [], top: body.scrollTop });
  for (const node of body.querySelectorAll('*')) {
    if (node.scrollTop > 0) out.push({ path: pathOf(node), top: node.scrollTop });
    if (out.length > 8) break;
  }
  shell.scrolls.set(id, out);
}

function restoreScroll(id) {
  const body = document.getElementById('panel-body');
  const saved = shell.scrolls.get(id);
  if (!body || !saved?.length) return;
  for (const { path, top } of saved) {
    let node = body;
    for (const index of path) node = node?.children?.[index];
    if (node) node.scrollTop = top;
  }
}

/** Panelde yazdırma eylemi: açık pencere/çekmece önce, sonra ekranın kendisi. */
function printActive() {
  const body = document.getElementById('panel-body');
  const visible = (node) => node && !node.disabled && node.getClientRects().length > 0;
  const layers = [...body.querySelectorAll('.kit-overlay, .kit-dialog, .kit-drawer, [role="dialog"]')]
    .filter(visible).reverse();
  for (const scope of [...layers, body]) {
    const explicit = [...scope.querySelectorAll('[data-command="print"]')].find(visible);
    if (explicit) { explicit.click(); return; }
    const byLabel = [...scope.querySelectorAll('button, [role="button"]')].find((node) => visible(node)
      && /(^|\s)yazdır/i.test(`${node.textContent} ${node.getAttribute('aria-label') || ''}`.toLocaleLowerCase('tr')));
    if (byLabel) { byLabel.click(); return; }
  }
  shellToast('Bu ekranda yazdırılacak bir belge yok — rapor ve belge ekranlarında “Yazdır” düğmesi bu kısayola bağlıdır.');
}

function exportActive() {
  const body = document.getElementById('panel-body');
  const table = [...body.querySelectorAll('[role="table"], table')].find((node) => node.getClientRects().length > 0);
  if (!table) {
    shellToast('Bu ekranda dışa aktarılacak bir tablo yok.');
    return;
  }
  exportTable(table);
}

/** Ekranı yeniden kurar. `hard`: önbellek atılır (Ctrl/⌘+R). */
function refreshActive({ hard = false } = {}) {
  if (!shell.activeId) return;
  if (hard) bypassCacheOnce();
  rememberScroll(shell.activeId);
  select(shell.activeId, { payload: shell.activePayload, force: true, refresh: true });
}

/** Açılışta son bilinen veriyle çizilen ekranın verisi arkada değişti. */
function staleData() {
  // TEK SESSİZ YENİLEME: yanıtında her seferinde değişen bir alan (sunucu
  // saati gibi) taşıyan ekran, her yeniden çizimde yine "değişti" görünür ve
  // ekran kendini durmadan yeniden kurardı. Kendiliğinden yapılan yenilemenin
  // ardından gelen değişiklik yok sayılır (veri zaten saniyeler önce alındı).
  if (shell.autoRefreshed === shell.activeId) return;
  // Kullanıcı ekrana henüz dokunmadıysa sessizce yeniden çizilir (veri artık
  // önbellekte güncel, yeniden çizim anlık). Dokunduysa yazdığı/seçtiği şey
  // silinmez: başlıkta "Yeni veriler var" düğmesi çıkar.
  if (!shell.interacted) {
    shell.autoRefreshed = shell.activeId;
    refreshActive();
    return;
  }
  const head = document.querySelector('.panel-head-text');
  if (!head || head.querySelector('.km-stale')) return;
  const chip = document.createElement('button');
  chip.type = 'button';
  chip.className = 'km-stale';
  chip.textContent = 'Yeni veriler var · Yenile';
  chip.title = `Ekranı yenile (${keyLabel('Mod+R')})`;
  chip.addEventListener('click', () => refreshActive());
  head.append(chip);
}

/** Açık geçici katmanlar: seçim listesi, bağlam menüsü, kabuk pencereleri. */
function closeTransient() {
  closeListbox();
  closeDomMenu();
  document.querySelector('.km-stale')?.remove();
}

// ================================================================== KABUK

/** Açık bölümler kullanıcıya özeldir ve uygulama kapanınca kaybolmaz. */
const OPEN_KEY = 'km.nav.open';

function openSet() {
  try {
    const raw = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]');
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function saveOpen(set) {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...set]));
  } catch {
    /* depolama kapalıysa menü yine çalışır, yalnız hatırlamaz */
  }
}

function navButton(panel) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'nav-item';
  item.dataset.id = panel.id;
  item.setAttribute('aria-current', panel.id === shell.activeId ? 'page' : 'false');
  item.classList.toggle('active', panel.id === shell.activeId);

  const label = document.createElement('span');
  label.textContent = panel.title;
  item.append(iconSvg(panel.icon), label);

  // Yüklenmemiş modül menüde durur ama durumu belli olur.
  if (panel.state && panel.state !== 'loaded') {
    item.classList.add('idle');
    item.title = panel.reason || 'Modül yüklenmedi.';
  }

  item.addEventListener('click', () => select(panel.id));
  return item;
}

/**
 * Kenar çubuğu — iki seviyeli ve katlanabilir.
 *
 * NEDEN KATLANIYOR. Elli bir ekran düz çizildiğinde şerit ~1800px oluyor ve
 * kenar çubuğu ~800px görüyor; kullanıcı her gidişte kaydırıyordu. Katlanınca
 * aynı anda on satır görünür.
 *
 * ARAMA KATLAMAYI EZER: bir bölümde eşleşme varsa o bölüm geçici olarak
 * açılır. Aksi hâlde aranan ekran kapalı bir başlığın içinde kalır ve arama
 * "sonuç yok" der gibi görünürdü.
 *
 * ETKİN EKRANIN BÖLÜMÜ HER ZAMAN AÇIKTIR: kullanıcının nerede olduğunu
 * gizlemek, menüyü bir bilmeceye çevirir.
 */
function renderNav(query = '') {
  const needle = fold(query.trim());
  const matches = needle
    ? shell.menu.filter((panel) => fold(panel.title).includes(needle)
      || fold(panel.group).includes(needle))
    : shell.menu;

  el.nav.replaceChildren();

  if (matches.length === 0) {
    el.nav.append(message(shell.menu.length ? 'Eşleşen ekran yok.' : 'Görünür ekran yok.'));
    return;
  }

  const open = openSet();
  const aramaVar = Boolean(needle);

  for (const top of navTree(shell.registry, matches)) {
    const bolumler = top.sections;
    // Alt bölümü olmayan üst başlık ESKİSİ GİBİ düz çizilir: tek ekranlık bir
    // grubu katlanabilir yapmak, tıklanacak bir şey olmadan tık istemektir.
    if (bolumler.length === 0) {
      const heading = document.createElement('p');
      heading.className = 'nav-group';
      heading.textContent = top.title;
      el.nav.append(heading);
      for (const panel of top.panels) el.nav.append(navButton(panel));
      continue;
    }

    const heading = document.createElement('p');
    heading.className = 'nav-group';
    heading.textContent = top.title;
    el.nav.append(heading);

    // Üst başlığın doğrudan ekranları bölümlerin ÜSTÜNDE durur.
    for (const panel of top.panels) el.nav.append(navButton(panel));

    for (const section of bolumler) {
      const key = `${top.title}${' / '}${section.title}`;
      const etkin = section.panels.some((panel) => panel.id === shell.activeId);
      const acik = aramaVar || etkin || open.has(key);

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = `nav-section${acik ? ' open' : ''}`;
      toggle.setAttribute('aria-expanded', acik ? 'true' : 'false');

      const caret = document.createElement('span');
      caret.className = 'nav-caret';
      caret.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = section.title;
      const count = document.createElement('span');
      count.className = 'nav-count';
      count.textContent = String(section.panels.length);
      toggle.append(caret, label, count);

      toggle.addEventListener('click', () => {
        const now = openSet();
        if (now.has(key)) now.delete(key);
        else now.add(key);
        saveOpen(now);
        renderNav(el.filter?.value || '');
      });
      el.nav.append(toggle);

      if (!acik) continue;
      for (const panel of section.panels) {
        const item = navButton(panel);
        item.classList.add('nav-nested');
        el.nav.append(item);
      }
    }
  }
}

function message(text) {
  const node = document.createElement('p');
  node.className = 'nav-empty';
  node.textContent = text;
  return node;
}

/**
 * Ekran değiştirir.
 *
 * `payload`: başka panelin `ctx.open()` ile gönderdiği veri.
 * `force`: aynı panele veriyle yeniden girmek için — "bu siparişi aç"
 *   denildiğinde zaten Siparişler ekranındaysak da yeniden bağlanmalı.
 */
async function select(id, {
  scroll = false, payload = null, force = false, viaHistory = false, refresh = false,
} = {}) {
  const panel = shell.panels.find((candidate) => candidate.id === id);
  if (!panel) return;
  if (id === shell.activeId && !force) return;

  closeTransient();
  if (!refresh) rememberScroll(shell.activeId);
  try {
    shell.cleanup?.();
  } catch (error) {
    console.error('[kabuk] panel temizliği hata verdi:', error);
  }
  shell.cleanup = null;
  shell.activeId = id;
  shell.activePayload = payload;
  if (!refresh) {
    if (!viaHistory) pushHistory(id, payload);
    countVisit(id);
    shell.autoRefreshed = null;
  }

  for (const item of el.nav.querySelectorAll('.nav-item')) {
    const active = item.dataset.id === id;
    item.classList.toggle('active', active);
    item.setAttribute('aria-current', active ? 'page' : 'false');
    if (active && scroll) item.scrollIntoView({ block: 'nearest' });
  }

  el.crumb.textContent = panel.group;
  el.title.textContent = panel.title;
  document.title = `${panel.title} — Kontrol Merkezi`;

  const body = document.getElementById('panel-body');
  body.replaceChildren();

  // İSKELET YALNIZ 150 ms'DEN UZUN YÜKLEMEDE (ADR 0032). İlk 150 ms gövdedeki
  // iskelet/yükleniyor öğeleri görünmez (`.km-fresh`): veri o arada gelirse
  // ekran iskelet yanıp sönmeden doğrudan dolu çizilir.
  body.classList.add('km-fresh');
  clearTimeout(shell.freshTimer);
  shell.freshTimer = setTimeout(() => body.classList.remove('km-fresh'), 150);

  const started = performance.now();
  const returning = shell.visited.has(id);
  shell.visited.add(id);
  const seq = (shell.mountSeq += 1);
  shell.interacted = false;

  const cleanup = await mountPanel(panel, body, shell.registry, payload, {
    onSettled: ({ fromCache }) => {
      if (seq !== shell.mountSeq) return;
      perf.record('panel', performance.now() - started,
        { id, title: panel.title, return: returning, cache: fromCache, refresh });
      if (returning || refresh) restoreScroll(id);
      find?.refresh();
    },
    onStale: () => {
      if (seq === shell.mountSeq) staleData();
    },
  });
  if (shell.activeId === id && seq === shell.mountSeq) shell.cleanup = cleanup;
  else cleanup?.();
  updateMenu();
}

function step(delta) {
  const visible = [...el.nav.querySelectorAll('.nav-item')].map((item) => item.dataset.id);
  if (visible.length === 0) return;
  const index = visible.indexOf(shell.activeId);
  const next = index === -1
    ? (delta > 0 ? 0 : visible.length - 1)
    : (index + delta + visible.length) % visible.length;
  select(visible[next], { scroll: true });
}

async function openWorkspace(user) {
  shell.user = user;
  // Kullanıcı ÇEKİRDEK EKRANLARININ görünürlüğü için verilir: modül ekranlarını
  // sidecar süzer, çekirdek ekranlarının listesi kabukta durur (ADR 0017 §1) ve
  // izin süzgeci de burada uygulanır. Her ikisinin de arkasında backend'in
  // kendi denetimi vardır (K9).
  shell.registry = await loadRegistry(user);
  shell.panels = shell.registry.panels;
  shell.menu = menuPanels(shell.registry);

  el.foot.replaceChildren();
  const who = document.createElement('span');
  who.className = 'sidebar-user';
  who.textContent = user.fullName;
  who.title = `Roller: ${user.roles.join(', ') || '—'}`;

  const cikis = document.createElement('button');
  cikis.type = 'button';
  cikis.className = 'sidebar-signout';
  cikis.textContent = 'Çıkış Yap';
  cikis.title = 'Oturumu kapat';
  cikis.addEventListener('click', signOut);

  el.foot.append(who, cikis);

  // Paneller birbirine `ctx.open(id, payload)` ile gezinir. Kabuk yalnız
  // taşıyıcıdır: hangi panelin hangisine gittiğini bilmez (K1).
  setNavigator((id, payload) => select(id, { payload, force: true, scroll: true }));

  renderNav();
  search.enable();
  // SON GÖRÜNÜM: kullanıcı en son hangi ekrandaysa onunla başlar (Tercihler'den
  // kapatılabilir). Ekran artık menüde değilse ilk ekran.
  const last = prefs.get('restoreLast') !== 'off' ? lastScreen() : null;
  const first = shell.menu.find((panel) => panel.id === last) || shell.menu[0];
  if (first) await select(first.id);

  el.login.hidden = true;
  el.workspace.hidden = false;
  el.filter.focus();
  perf.mark('calisma-alani');
  updateMenu();
  preloadFrequent();
  updater?.start();
}

/**
 * MODÜL ÖNYÜKLEME: boşta kalınca en sık açılan ekranların dosyaları indirilip
 * ayrıştırılır (bağlanmaz). İlk tıklamada `import()` beklemesi kalkar.
 */
function preloadFrequent() {
  const idle = window.requestIdleCallback || ((fn) => setTimeout(fn, 1200));
  const queue = frequentScreens().filter((panel) => panel && panel.id !== shell.activeId).slice(0, 5);
  const next = () => {
    const panel = queue.shift();
    if (!panel || !shell.user) return;
    preloadPanel(panel).finally(() => idle(next));
  };
  idle(next);
}

/**
 * Oturumu kapatır ve giriş ekranına döner.
 *
 * EKRAN ÖNCE SÖKÜLÜR, sonra belirteç düşer. Etkin panelin `cleanup`'ı
 * çalışmazsa açık kalan zamanlayıcıları ve dinleyicileri, artık yetkisi
 * olmayan bir oturumun ardından istek atmaya devam ederdi.
 *
 * MENÜ VE KAYIT DA BOŞALTILIR. Yalnız giriş kartını göstermek, bir sonraki
 * kişinin ekranında bir öncekinin ekran listesini bir an için bırakırdı —
 * ekran adları da yetki bilgisidir.
 */
async function signOut() {
  try {
    shell.cleanup?.();
  } catch (error) {
    console.error('[kabuk] panel temizliği hata verdi:', error);
  }
  shell.cleanup = null;
  shell.activeId = null;
  shell.activePayload = null;
  shell.user = null;
  shell.registry = { groups: [], panels: [] };
  shell.panels = [];
  shell.menu = [];
  shell.history = { stack: [], index: -1 };
  shell.visited.clear();
  shell.scrolls.clear();
  closeTransient();
  closeDialog();
  find?.close();
  search.reset();
  resetProviders();

  await logout();
  // Önceki kişinin yanıtları bellekte kalmaz (setToken da boşaltır).
  clearResponseCache();
  updateMenu();

  document.getElementById('panel-body').replaceChildren();
  el.nav.replaceChildren();
  el.foot.replaceChildren();
  el.filter.value = '';
  el.crumb.textContent = '';
  el.title.textContent = '';
  document.title = 'Kontrol Merkezi';

  el.workspace.hidden = true;
  el.login.hidden = false;
  clearError();
  resetPin();
  setHint(LOGIN_HINT);
}

// ================================================================== GİRİŞ
//
// TUŞ TAKIMI. Kullanıcı adı sorulmaz — 6 haneli PIN hem kimliği hem girişi
// belirler (docs/identity-model.md — Giriş akışı). Metin alanı YOKTUR: rakamlar
// pencere tuş dinleyicisinden toplanır, ekranda yalnız hane SAYISI görünür.
//
// EKRANIN TEK KİPİ VAR: PIN yazılır, oturum açılır ya da tek tip redde varılır.
//
// ESKİDEN İKİ KİP DAHA VARDI ('set' / 'repeat'): reddedilmiş ADR 0016'nın "önce
// kendine sır belirle" adımı. O akış 17.08.2026'da gerçek bir kurulumu kilitledi
// — kullanıcı orijinal PIN'iyle girdi, akış yerine YENİ bir sır yazdı ve eski
// PIN o günden sonra reddedildi. Kalıntı tümüyle kaldırıldı; kullanıcı kendi
// PIN'ini yalnız İSTEYEREK değiştirir (mevcut PIN'ini girerek).

/** İpucu satırının parçaları: [metin, tuş?, kuyruk?]. */
const LOGIN_HINT = ["PIN'inizi yazın, ", 'Enter', ' ile girin'];

let pin = '';
let busy = false;

/**
 * GİRİŞ REDDİNİN TEK CÜMLESİ.
 *
 * Sebep AYIRT EDİLMEZ: PIN yanlış mı, hesap kilitli mi, o PIN kimseye ait değil
 * mi — üçü de aynı cümleyi verir. Farklı cümle yazmak, deneme yoluyla "bu PIN
 * birine ait" bilgisini sızdırırdı; backend de aynı nedenle tek tip 401
 * döndürüyor (`km_core/http/users.py`).
 */
const LOGIN_REJECTED = 'Giriş yapılamadı.';

function renderSlots() {
  const count = Math.max(PIN_MIN, pin.length);
  if (el.slots.childElementCount !== count) {
    el.slots.replaceChildren(
      ...Array.from({ length: count }, (_, i) => {
        const dot = document.createElement('span');
        dot.className = i < PIN_MIN ? 'slot' : 'slot extra';
        return dot;
      }),
    );
  }
  for (const [i, dot] of [...el.slots.children].entries()) {
    dot.classList.toggle('filled', i < pin.length);
  }
  el.slots.setAttribute('aria-label', `Girilen hane sayısı: ${pin.length}`);
}

function clearError() {
  el.error.classList.remove('show');
  el.error.textContent = '';
}

function failLogin(text = LOGIN_REJECTED) {
  el.error.textContent = text;
  el.error.classList.add('show');
  el.slots.classList.remove('shake');
  void el.slots.offsetWidth;
  el.slots.classList.add('shake');
  pin = '';
  renderSlots();
}

/**
 * İpucu satırı.
 *
 * METİN DÜĞÜM OLARAK YAZILIR, `innerHTML` İLE DEĞİL. Buradan ÇEKİRDEKTEN GELEN
 * veri de geçiyor (sunucunun "PIN belirleyin" cümlesi, son bağlantı hatası);
 * `innerHTML` olsaydı o veri HTML olarak yorumlanırdı. Tuş rozeti ihtiyacı bu
 * yüzden ayrı bir parça olarak alınır.
 */
function setHint([text = '', key = '', tail = '']) {
  el.loginHint.replaceChildren(document.createTextNode(text));
  if (!key) return;
  const rozet = document.createElement('kbd');
  rozet.textContent = key;
  el.loginHint.append(rozet, document.createTextNode(tail));
}

function resetPin() {
  pin = '';
  renderSlots();
}

async function enterWorkspace(user) {
  resetPin();
  el.login.classList.add('leaving');
  try {
    await openWorkspace(user);
  } finally {
    // `leaving` SINIFI HER DURUMDA KALKAR. Kart `opacity: 0` ve
    // `pointer-events: none` alıyor; menü kurulamadığında (ör. `/modules`
    // patlarsa) orada bırakmak, kullanıcıyı görünmez ve tıklanamaz bir giriş
    // ekranına kilitlerdi — hata mesajı yazılır ama kimse okuyamazdı.
    el.login.classList.remove('leaving');
  }
}

async function submitPin() {
  if (busy || pin.length < PIN_MIN) return;

  const entered = pin;
  busy = true;
  clearError();
  setHint(['Doğrulanıyor…']);
  el.loginHint.classList.add('busy');

  try {
    const result = await login(entered);
    await enterWorkspace(result.user);
  } catch (error) {
    if (error.status >= 400 && error.status < 500) {
      // 401 TEK TİPTİR (`LOGIN_REJECTED`) — sebep ayırt edilmez. Diğer 4xx
      // çekirdeğin kendi cümlesiyle gösterilir; o cümleler sır hakkında değil,
      // isteğin biçimi hakkındadır ve hiçbir şey ele vermez.
      failLogin(error.status === 401 ? LOGIN_REJECTED : error.message);
    } else {
      failLogin('Çekirdeğe ulaşılamadı.');
    }
  } finally {
    busy = false;
    el.loginHint.classList.remove('busy');
    // Giriş ekranı kapandıysa dokunulmaz: kullanıcı artık menüdedir.
    if (!el.login.hidden) setHint(LOGIN_HINT);
  }
}

function onLoginKey(event) {
  if (event.key >= '0' && event.key <= '9') {
    if (busy || pin.length >= PIN_MAX) return;
    clearError();
    pin += event.key;
    renderSlots();
  } else if (event.key === 'Backspace') {
    if (busy || pin.length === 0) return;
    clearError();
    pin = pin.slice(0, -1);
    renderSlots();
  } else if (event.key === 'Escape') {
    if (busy) return;
    clearError();
    pin = '';
    renderSlots();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    submitPin();
  }
}

// ================================================================= OLAYLAR

el.filter.addEventListener('input', () => renderNav(el.filter.value));

el.filter.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    el.filter.value = '';
    renderNav();
    el.filter.blur();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    el.filter.blur();
  }
});

function isTyping(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable
      || target.tagName === 'INPUT'
      || target.tagName === 'TEXTAREA'
      || target.tagName === 'SELECT');
}

window.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.altKey || event.metaKey) return;

  if (!el.login.hidden) {
    onLoginKey(event);
    return;
  }

  const typing = isTyping(event.target);

  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (typing && event.target !== el.filter) return;
    event.preventDefault();
    step(event.key === 'ArrowDown' ? 1 : -1);
    return;
  }

  if (typing) return;

  // `/` KÜRESEL ARAMAYI açar (öğrenci, telefon, kart, ekran — ADR 0031).
  // Menü süzgecine ulaşmak için Sekme ya da tıklama yeter.
  if (event.key === '/') {
    event.preventDefault();
    search.focus();
  }
});

// ========================================================= KÜRESEL ARAMA
//
// Üst çubuktaki "Ara" kutusu (ADR 0031): her ekrandan, tek kutudan öğrenci
// (ad, okul no, veli telefonu, kantin kartı) ve ekran bulunur; sonuç tek
// tıkla (ya da Enter ile) açılır. Kabuk kimin aradığını bilmez — adı
// `.search` ile biten arayüz yetenekleri sonuç verir (K1).
const search = createGlobalSearch({
  input: document.getElementById('gsearch-input'),
  popup: document.getElementById('gsearch-pop'),
  list: document.getElementById('gsearch-list'),
  status: document.getElementById('gsearch-status'),
  fold,
  screens: () => shell.menu,
  providers: () => searchProviders(shell.registry),
  openScreen: (id) => select(id, { scroll: true }),
});

// Sağ tık artık bağlama uygun yerel menü açar (native.js → installNative).

// ================================================================ EŞLEME
//
// ADR 0021 §4 — CİHAZ EŞLEMESİ. Eşleşmemiş bir kurulum giriş ekranını hiç
// görmez: önce "bu makine bizim" demesi gerekir. Yönetici merkezde tek
// kullanıcılık, süreli bir kod üretir; kod buraya girilir ve kurulum karşılığında
// kendi token'ını alır.
//
// EŞLEME TOKEN'I OTURUM DEĞİLDİR. Kod girildikten sonra kullanıcı yine kendi
// PIN'ini yazar; aşağıdaki tuş takımına ve giriş akışına DOKUNULMAZ. Bu bölüm
// yalnızca onun ÖNÜNE eklenir ve iş bitince kendini tümüyle söker.
//
// YENİ CSS YOK: ekran, giriş kartının sınıflarını (`login`, `login-card`,
// `slots`) yeniden kullanır. Kod da PIN gibi rakamdır ve tuş takımıyla girilir —
// metin alanı açmak, ekranın kendi diline yabancı bir kutu koymak olurdu.

const PAIR_LENGTH = 8;

// Eşleme etkinken DOM düğümlerini ve durumu taşır; kapanınca null olur.
let pairing = null;

/**
 * Çekirdeğe eşleme durumunu sorar.
 *
 * HATA "EŞLEME GEREKMİYOR" SAYILIR. Uç yoksa, yetenek kapalıysa ya da istek
 * düşerse kurulum bugünkü gibi tek makinede açılmalıdır (ADR 0021 — Sonuçlar):
 * merkez yüzünden giriş ekranını göstermemek, tam da o kararın yasakladığı
 * gerilemedir.
 */
async function pairingRequired() {
  try {
    const state = await api('/api/pairing/state');
    return Boolean(state?.pairingRequired);
  } catch {
    return false;
  }
}

function pairSlots() {
  const dots = [...pairing.slots.children];
  for (const [i, dot] of dots.entries()) {
    dot.classList.toggle('filled', i < pairing.code.length);
  }
  pairing.slots.setAttribute('aria-label', `Girilen hane sayısı: ${pairing.code.length}`);
}

function pairFail(text) {
  pairing.error.textContent = text;
  pairing.error.classList.add('show');
  pairing.slots.classList.remove('shake');
  void pairing.slots.offsetWidth;
  pairing.slots.classList.add('shake');
  pairing.code = '';
  pairSlots();
}

function pairHint(text) {
  // Metin DÜĞÜM olarak yazılır, `innerHTML` ile değil: buradan çekirdeğin ve
  // merkezin cümleleri geçiyor (giriş ekranındaki `setHint` ile aynı gerekçe).
  pairing.hint.replaceChildren(document.createTextNode(text));
}

async function submitPairCode() {
  if (pairing.busy || pairing.code.length !== PAIR_LENGTH) return;

  const code = pairing.code;
  pairing.busy = true;
  pairing.error.classList.remove('show');
  pairing.error.textContent = '';
  pairHint('Eşleniyor…');

  try {
    await api('/api/pairing/pair', { method: 'POST', body: { code } });

    // EŞLEMEDEN SONRA UYGULAMA YENİDEN BAŞLAR — ve bu bir kolaylık değil,
    // zorunluluk.
    //
    // Modüller çekirdek açılırken yükleniyor; eşlemeyle inen geçit ayarları
    // (BLD, kantin, mağaza adresleri) ise eşleme ANINDA geliyor. Yani ilk
    // eşlemede modüller o ayarları göremeden yüklenmiş oluyor: kantin geçidi
    // "adres yok" diyerek hiç yüklenmiyor, BLD geçidi boş adresle ve salt
    // okunur açılıyor. Ölçüldü — yeniden başlatınca üçü de doğru bağlanıyor
    // ve 49/49 modül sorunsuz yükleniyor.
    //
    // KULLANICIYA "yeniden başlatın" DEMİYORUZ: okumasına bağlı kalırdı,
    // okumazsa ekranlar boş gelir ve sebebi hiçbir yerde görünmezdi. Eşleme
    // girişten önce, kimse çalışmaya başlamadan yapılıyor — kaybolacak iş yok.
    //
    // TARAYICIDA (Tauri dışında) komut yoktur: orada ekran yalnız kapanır ve
    // bugünkü davranış sürer.
    pairHint('Kurulum tamamlandı — uygulama yeniden başlatılıyor…');
    const invoke = window.__TAURI__?.core?.invoke;
    if (invoke) {
      try {
        await invoke('restart_app');
        return;
      } catch (error) {
        console.error('[kabuk] yeniden başlatılamadı:', error);
      }
    }
    pairing.done();
  } catch (error) {
    // MERKEZİN CÜMLESİ OLDUĞU GİBİ GÖSTERİLİR: kod geçersiz mi, merkeze mi
    // ulaşılamıyor, deneme sınırına mı takıldı — üçü ayrı ekran davranışı
    // ister ve kendi cümlemizle değiştirmek durumu gizlerdi.
    pairFail(error.message || 'Eşleme yapılamadı.');
    pairing.busy = false;
    pairHint(`Merkezden aldığınız ${PAIR_LENGTH} haneli kodu yazın.`);
  }
}

function onPairKey(event) {
  if (event.ctrlKey || event.altKey || event.metaKey) return;

  if (event.key >= '0' && event.key <= '9') {
    if (pairing.busy || pairing.code.length >= PAIR_LENGTH) return;
    pairing.error.classList.remove('show');
    pairing.code += event.key;
    pairSlots();
  } else if (event.key === 'Backspace') {
    if (pairing.busy || pairing.code.length === 0) return;
    pairing.code = pairing.code.slice(0, -1);
    pairSlots();
  } else if (event.key === 'Escape') {
    if (pairing.busy) return;
    pairing.code = '';
    pairSlots();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    submitPairCode();
  } else {
    return;
  }
  // OLAY AŞAĞI GEÇMEZ. Dinleyici YAKALAMA evresindedir; giriş ekranının kendi
  // tuş dinleyicisi bu sırada hiç çalışmaz ve ona tek satır dokunulmamış olur.
  event.stopPropagation();
}

/** Eşleme ekranını kurar ve kod girilene kadar bekleyen bir söz döndürür. */
function runPairing() {
  const section = document.createElement('section');
  section.className = 'login';

  const aurora = document.createElement('div');
  aurora.className = 'aurora';
  aurora.setAttribute('aria-hidden', 'true');

  const card = document.createElement('div');
  card.className = 'login-card';

  // Marka işareti giriş kartından KOPYALANIR: SVG'yi elle kurmak aynı çizimi
  // ikinci kez yazmak olurdu.
  const mark = document.querySelector('#login .mark');
  if (mark) card.append(mark.cloneNode(true));

  const heading = document.createElement('h1');
  heading.textContent = 'Kontrol Merkezi';
  const org = document.createElement('p');
  org.className = 'org';
  org.textContent = 'Bu kurulum henüz eşlenmedi';

  const slots = document.createElement('div');
  slots.className = 'slots';
  slots.setAttribute('role', 'img');
  slots.append(...Array.from({ length: PAIR_LENGTH }, () => {
    const dot = document.createElement('span');
    dot.className = 'slot';
    return dot;
  }));

  const error = document.createElement('p');
  error.className = 'error';
  error.setAttribute('role', 'alert');
  error.setAttribute('aria-live', 'assertive');

  const hint = document.createElement('p');
  hint.className = 'login-hint';

  card.append(heading, org, slots, error, hint);
  section.append(aurora, card);
  document.body.append(section);

  // Giriş kartı bu sırada GİZLENİR ve sonunda geri açılır; kendisine
  // dokunulmaz.
  el.login.hidden = true;

  return new Promise((resolve) => {
    pairing = {
      section, slots, error, hint, code: '', busy: false,
      done: () => {
        window.removeEventListener('keydown', onPairKey, true);
        section.remove();
        pairing = null;
        el.login.hidden = false;
        resolve();
      },
    };
    pairSlots();
    pairHint(`Merkezden aldığınız ${PAIR_LENGTH} haneli kodu yazın.`);
    window.addEventListener('keydown', onPairKey, true);
  });
}

// ====================================================== NATİVE HİS (ADR 0032)

const workspaceOpen = () => !el.workspace.hidden && Boolean(shell.user);

/** Sayfada bul — etkin ekranın gövdesinde. */
const find = createFindBar({
  host: document.querySelector('.panel'),
  scope: () => document.getElementById('panel-body'),
});

/** Arka planda güncelleme (yalnız masaüstü kabuğunda). */
const updater = isNative()
  ? createUpdater({ invoke, notify, toast: shellToast })
  : null;

function setZoom(direction) {
  const factor = zoomStep(direction);
  shellToast(`Yakınlaştırma %${Math.round(factor * 100)}`);
}

async function toggleFullscreen() {
  if (isNative()) {
    await invoke('window_toggle_fullscreen');
  } else if (document.fullscreenElement) {
    await document.exitFullscreen();
  } else {
    await document.documentElement.requestFullscreen?.();
  }
}

function editCommand(name) {
  return () => {
    const target = document.activeElement;
    if (name === 'selectAll' && target?.select) {
      target.select();
      return;
    }
    try { document.execCommand(name); } catch { /* desteklenmiyor */ }
  };
}

// Komut kimlikleri menü çubuğuyla (src-tauri/src/native.rs) AYNIDIR.
defineCommand('search', () => search.focus(), { when: workspaceOpen });
defineCommand('search-text', (_source, text) => search.focus(String(text || '')), { when: workspaceOpen });
defineCommand('find', () => find.show(), { when: workspaceOpen });
defineCommand('find-next', () => find.next(), { when: workspaceOpen });
defineCommand('find-prev', () => find.prev(), { when: workspaceOpen });
defineCommand('print', printActive, { when: workspaceOpen });
defineCommand('export', exportActive, { when: workspaceOpen });
defineCommand('back', () => goHistory(-1), { when: workspaceOpen });
defineCommand('forward', () => goHistory(1), { when: workspaceOpen });
for (let n = 1; n <= 9; n += 1) {
  defineCommand(`go:${n}`, () => {
    const panel = frequentScreens()[n - 1];
    if (panel) select(panel.id, { scroll: true });
    else shellToast(`${n}. sık ekran henüz yok — ekranlar açıldıkça buraya yerleşir.`);
  }, { when: workspaceOpen });
}
defineCommand('refresh', () => refreshActive({ hard: true }), { when: workspaceOpen });
defineCommand('fullscreen', () => toggleFullscreen());
defineCommand('prefs', () => openPreferences({ onChange: () => updateMenu() }));
defineCommand('zoom-in', () => setZoom(1));
defineCommand('zoom-out', () => setZoom(-1));
defineCommand('zoom-reset', () => setZoom(0));
for (const theme of ['system', 'light', 'dark']) {
  defineCommand(`theme:${theme}`, () => { prefs.set('theme', theme); updateMenu(); });
}
for (const density of ['comfortable', 'compact']) {
  defineCommand(`density:${density}`, () => { prefs.set('density', density); updateMenu(); });
}
defineCommand('sidebar', () => prefs.set('sidebar', prefs.get('sidebar') === 'hidden' ? 'shown' : 'hidden'),
  { when: workspaceOpen });
defineCommand('go-filter', () => {
  if (prefs.get('sidebar') === 'hidden') prefs.set('sidebar', 'shown');
  el.filter.focus();
  el.filter.select();
}, { when: workspaceOpen });
defineCommand('shortcuts', () => openShortcuts());
defineCommand('perf', () => openPerf());
defineCommand('about', () => openAbout());
defineCommand('update', () => (updater ? updater.check({ manual: true })
  : shellToast('Güncelleme yalnız masaüstü uygulamasında çalışır.')));
defineCommand('signout', () => signOut(), { when: workspaceOpen });
defineCommand('edit:undo', editCommand('undo'));
defineCommand('edit:redo', editCommand('redo'));
defineCommand('edit:cut', editCommand('cut'));
defineCommand('edit:copy', editCommand('copy'));
defineCommand('edit:paste', editCommand('paste'));
defineCommand('edit:selectall', editCommand('selectAll'));

installNative({ isLoginOpen: () => !el.login.hidden || Boolean(pairing) });
// Sayfadaki bütün tekli `<select>`lar klavyeyle aranabilen listeyi açar.
enhanceSelects(document);

// Kullanıcı ekrana dokundu mu (arkadaki tazeleme yazdığını silmesin).
for (const type of ['pointerdown', 'keydown', 'input']) {
  document.getElementById('panel-body').addEventListener(type, () => { shell.interacted = true; }, true);
}

// BAĞLANTI GERİ GELDİ: etkin ekran ağdan tazelenir.
onConnectionRestored(() => {
  if (workspaceOpen()) refreshActive({ hard: true });
});
connection.subscribe((online) => {
  document.documentElement.classList.toggle('km-offline', !online);
});

// Kısayol ipuçları platformun diliyle (macOS: ⌘K).
document.getElementById('gsearch-key').textContent = isMac() ? '⌘K' : 'Ctrl K';
document.getElementById('gsearch-key').setAttribute('aria-label', `Kısayol: ${keyLabel('Mod+K')} ya da eğik çizgi`);
document.querySelector('.sidebar-foot')?.replaceChildren(
  ...[['↑', '↓', ' gez · '], [keyLabel('Mod+K'), ' ara · '], [keyLabel('Mod+/'), ' kısayollar']]
    .flatMap((parts) => parts.map((part, index) => {
      if (index === parts.length - 1) return document.createTextNode(part);
      const key = document.createElement('kbd');
      key.textContent = part;
      return key;
    })),
);

// Yazdırma komutunu Ctrl/⌘+P'ye bağlayan menü öğesi de dahil, menü çubuğu
// giriş öncesi durumuyla eşitlenir.
updateMenu();

// Genel `/` kısayolu ve Ctrl/⌘+K aynı kutuyu açar; kabuk dışı tarayıcıda
// `runCommand` görünür kalsın (sınama ve tasarım denemesi).
window.__kmRunCommand = runCommand;

// ================================================================ AÇILIŞ

/**
 * Kabuğun hangi adrese baktığı. Kabuk dışında (tarayıcı denemesi) `null`.
 *
 * Adres RUST TARAFINDA belirlenir ve tek yerdedir (`server_base`); arayüz onu
 * kendi başına hesaplasaydı iki taraf ayrışabilir ve ekran bir adresi
 * yazarken istek başka bir adrese giderdi.
 */
async function serverInfo() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return null;
  try {
    return await invoke('server_info');
  } catch {
    return null;
  }
}

async function start() {
  el.login.hidden = false;
  renderSlots();
  // Pencerenin kendi özellikleri (macOS bütünleşik başlık çubuğu, Windows 11
  // Mica) — CSS buna göre yer açar.
  if (isNative()) {
    invoke('native_info').then((info) => {
      const root = document.documentElement;
      if (info?.overlayTitleBar) root.dataset.titlebar = 'overlay';
      root.classList.toggle('km-mica', Boolean(info?.mica));
    }).catch(() => {});
  }
  // İLK BOYA: giriş kartı çizildi → pencere görünür (Rust onu boyanana dek
  // gizli tutuyor; beyaz/siyah kare yanıp sönmez).
  requestAnimationFrame(() => requestAnimationFrame(() => {
    perf.mark('giris-boyandi');
    if (isNative()) invoke('window_ready').catch(() => {});
  }));

  // KİPİ ÖNCE SÖYLE. Sunucu kipinde "çekirdek başlatılıyor" cümlesi yanlıştır:
  // burada başlatılan bir şey yok, uzak bir sunucuya bağlanılıyor (ADR 0026).
  // Yanlış cümle, arıza anında kullanıcıyı yanlış yere baktırır — makinede
  // olmayan bir süreci aramaya.
  const server = await serverInfo();
  const remote = server && !server.local;
  setHint([remote ? `${server.base} adresine bağlanılıyor…` : 'Çekirdek başlatılıyor…']);

  // MERKEZ GELENE KADAR BEKLENİR — süresiz (ADR 0032). Eskiden 20 sn sonra
  // "ulaşılamadı" yazıp DURUYORDU; internet bir dakika sonra gelse bile
  // kullanıcı uygulamayı kapatıp açmak zorundaydı. Şimdi ipucu satırı neyin
  // beklendiğini ve kaçıncı deneme olduğunu söyler; bağlantı gelince giriş
  // kendiliğinden açılır.
  const ready = await waitForCore(0, {
    onWait: (attempt, error) => {
      if (attempt < 3) return;
      setHint([remote
        ? `Merkeze ulaşılamıyor — yeniden deneniyor (${attempt}). İnternet bağlantınızı denetleyin. Sebep: ${error}`
        : `Çekirdeğe ulaşılamadı — yeniden deneniyor (${attempt}). Sebep: ${error}`]);
    },
  });
  perf.mark('merkez-hazir');
  if (!ready.ok) {
    // SON HATA OLDUĞU GİBİ YAZILIR ama METİN olarak: içeriği çekirdekten
    // geliyor ve `innerHTML` ile basılırsa yorumlanırdı.
    //
    // SUNUCU KİPİNDE NE YAPILACAĞI DA YAZILIR. "Ulaşılamadı" tek başına
    // kullanıcıya hiçbir şey yaptırmaz; internet mi, sunucu mu, adres mi
    // sorusunun ilk adımı söylenir.
    setHint(remote
      ? [
        'Merkeze ulaşılamıyor — uygulama veriyi merkezden alır.',
        `Adres: ${server.base}`,
        `Sebep: ${ready.error}`,
        'İnternet bağlantınızı denetleyin; sorun sürerse yöneticinize bildirin.',
      ]
      : [`Çekirdeğe ulaşılamadı — ${ready.error}`]);
    return;
  }

  // EŞLEME GİRİŞTEN ÖNCE GELİR (ADR 0021 §4). Gerekmiyorsa bu satır hiçbir şey
  // yapmaz ve ekran bugünkü gibi doğrudan PIN tuş takımıyla açılır.
  if (await pairingRequired()) await runPairing();

  setHint(LOGIN_HINT);
}

start();
