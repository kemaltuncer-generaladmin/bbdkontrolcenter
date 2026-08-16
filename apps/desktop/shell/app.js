// Kontrol Merkezi — kabuk davranışı.
//
// Kabuk hiçbir modülün adını bilmez (K1): menü, çekirdeğin verdiği kayıttan
// kurulur. Giriş de çekirdeğe aittir — PIN sidecar'daki `POST /auth/login`
// ucunda Argon2id ile doğrulanır, kabuk yalnızca sorar ve belirteci taşır.

import { iconSvg } from './icons.js';
import {
  loadRegistry, login, mountPanel, navTree, setNavigator, waitForCore,
} from './ui-kernel.js';

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
  panels: [],
  activeId: null,
  cleanup: null,
  user: null,
};

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
    ? shell.panels.filter((panel) => fold(panel.title).includes(needle)
      || fold(panel.group).includes(needle))
    : shell.panels;

  el.nav.replaceChildren();

  if (matches.length === 0) {
    el.nav.append(message(shell.panels.length ? 'Eşleşen ekran yok.' : 'Görünür ekran yok.'));
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
async function select(id, { scroll = false, payload = null, force = false } = {}) {
  const panel = shell.panels.find((candidate) => candidate.id === id);
  if (!panel) return;
  if (id === shell.activeId && !force) return;

  try {
    shell.cleanup?.();
  } catch (error) {
    console.error('[kabuk] panel temizliği hata verdi:', error);
  }
  shell.cleanup = null;
  shell.activeId = id;

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

  const cleanup = await mountPanel(panel, body, shell.registry, payload);
  if (shell.activeId === id) shell.cleanup = cleanup;
  else cleanup?.();
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
  shell.registry = await loadRegistry();
  shell.panels = shell.registry.panels;

  el.foot.replaceChildren();
  const who = document.createElement('span');
  who.className = 'sidebar-user';
  who.textContent = user.fullName;
  who.title = `Roller: ${user.roles.join(', ') || '—'}`;
  el.foot.append(who);

  // Paneller birbirine `ctx.open(id, payload)` ile gezinir. Kabuk yalnız
  // taşıyıcıdır: hangi panelin hangisine gittiğini bilmez (K1).
  setNavigator((id, payload) => select(id, { payload, force: true, scroll: true }));

  renderNav();
  if (shell.panels.length > 0) await select(shell.panels[0].id);

  el.login.hidden = true;
  el.workspace.hidden = false;
  el.filter.focus();
}

// ================================================================== GİRİŞ

let pin = '';
let busy = false;

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

function failLogin(text = 'Giriş yapılamadı.') {
  // Tek tip mesaj: sebep ayırt edilmez (yanlış PIN mi, kilitli mi, yok mu).
  el.error.textContent = text;
  el.error.classList.add('show');
  el.slots.classList.remove('shake');
  void el.slots.offsetWidth;
  el.slots.classList.add('shake');
  pin = '';
  renderSlots();
}

function setHint(html) {
  el.loginHint.innerHTML = html;
}

async function submitPin() {
  if (busy || pin.length < PIN_MIN) return;

  busy = true;
  clearError();
  setHint('Doğrulanıyor…');
  el.loginHint.classList.add('busy');

  try {
    const user = await login(pin);
    pin = '';
    renderSlots();
    el.login.classList.add('leaving');
    await openWorkspace(user);
    el.login.classList.remove('leaving');
  } catch (error) {
    failLogin(error.status === 401 ? 'Giriş yapılamadı.' : 'Çekirdeğe ulaşılamadı.');
  } finally {
    busy = false;
    el.loginHint.classList.remove('busy');
    setHint("PIN'inizi yazın, <kbd>Enter</kbd> ile girin");
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

  if (event.key === '/') {
    event.preventDefault();
    el.filter.focus();
    el.filter.select();
  }
});

window.addEventListener('contextmenu', (event) => event.preventDefault());

// ================================================================ AÇILIŞ

async function start() {
  el.login.hidden = false;
  renderSlots();
  setHint('Çekirdek başlatılıyor…');

  const ready = await waitForCore();
  if (!ready.ok) {
    setHint(`Çekirdeğe ulaşılamadı — <code>${ready.error}</code>`);
    return;
  }
  setHint("PIN'inizi yazın, <kbd>Enter</kbd> ile girin");
}

start();
