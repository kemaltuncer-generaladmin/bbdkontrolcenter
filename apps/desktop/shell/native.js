// native.js — masaüstü uygulaması hissi (ADR 0032).
//
// Bu dosya kabuğun "tarayıcı gibi" davranan her köşesini işletim sisteminin
// alışılmış davranışına çevirir:
//
//  · KOMUTLAR VE KISAYOLLAR — menü çubuğundaki her satır bir komut kimliğidir
//    (`km:print`, `km:back`…). Aynı komuta üç yoldan varılır: yerel menü
//    (Rust → `window.__kmNative.menu(id)`), klavye (buradaki tablo) ve bağlam
//    menüsü. Windows'ta menü hızlandırıcısı ile sayfanın tuş olayı ikisi
//    birden gelebilir; aynı komut 350 ms içinde FARKLI yoldan ikinci kez
//    gelirse yok sayılır.
//  · TERCİHLER — tema (sistemi izle / açık / koyu), yoğunluk, kalıcı
//    yakınlaştırma, bildirimler, açılışta son ekran. Makineye özeldir
//    (`localStorage`), oturumla silinmez.
//  · TARAYICI İZLERİ — sağ tıkta "İncele/Geri/Yenile" yerine bağlama uygun
//    yerel menü; bağlantı/görsel sürüklenmez; dosya pencereye bırakılınca
//    sayfa o dosyaya gitmez; Ctrl+tekerlek yakınlaştırmaz; Backspace ve fare
//    yan tuşları sayfa geçmişinde değil, ekran geçmişinde gezer; F5/Ctrl+R
//    oturumu düşüren sayfa yenilemesi değil, ekranın verisini tazeler;
//    `<a download>` tarayıcının indirme balonu yerine yerel "Kaydet" açar.
//
// K1: burada modül adı geçmez. Bağlam menüsüne panel kendi satırını
// `km-contextmenu` olayıyla ekler (aşağıda).

import { saveFile } from './ui-kit/native.js';

const tauri = () => window.__TAURI__?.core?.invoke || null;

export function isNative() {
  return Boolean(tauri());
}

export async function invoke(command, args) {
  const fn = tauri();
  if (!fn) throw new Error('masaüstü kabuğu yok');
  return fn(command, args);
}

/** `mac` · `win` · `linux` — `boot.js` ilk boyadan önce yazar. */
export function platform() {
  return document.documentElement.dataset.platform || 'linux';
}

export const isMac = () => platform() === 'mac';

/** Kısayol gösterimi: macOS'ta ⌘, ötekilerde Ctrl. */
export function keyLabel(combo) {
  const mac = isMac();
  return combo
    .replace('Mod+', mac ? '⌘' : 'Ctrl+')
    .replace('Shift+', mac ? '⇧' : 'Shift+')
    .replace('Alt+', mac ? '⌥' : 'Alt+');
}

// ================================================================ tercihler

const PREF = 'km.pref.';
const DEFAULTS = {
  theme: 'system',          // system · light · dark
  density: 'comfortable',   // comfortable · compact
  zoom: '1',                // 0.8 … 1.5
  notify: 'on',             // on · off
  restoreLast: 'on',        // on · off — girişte son açık ekran
  sidebar: 'shown',         // shown · hidden
};
const prefListeners = new Set();

export const prefs = {
  get(key) {
    try {
      return localStorage.getItem(PREF + key) ?? DEFAULTS[key];
    } catch {
      return DEFAULTS[key];
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(PREF + key, String(value));
    } catch {
      /* depolama kapalı: tercih bu oturumla sınırlı kalır */
    }
    applyPref(key);
    for (const fn of prefListeners) {
      try { fn(key, String(value)); } catch (error) { console.error('[tercih] dinleyici:', error); }
    }
  },
  onChange(fn) {
    prefListeners.add(fn);
    return () => prefListeners.delete(fn);
  },
};

const ZOOM_STEPS = [0.8, 0.9, 1, 1.1, 1.25, 1.4, 1.5];
const darkQuery = typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)') : null;

function resolvedTheme() {
  const pref = prefs.get('theme');
  if (pref === 'light' || pref === 'dark') return pref;
  return darkQuery?.matches ? 'dark' : 'light';
}

function applyPref(key) {
  const root = document.documentElement;
  if (key === 'theme') {
    root.dataset.theme = resolvedTheme();
    root.dataset.themePref = prefs.get('theme');
    // Başlık çubuğu ve yerel menüler de aynı temaya geçer.
    if (isNative()) invoke('window_set_theme', { theme: prefs.get('theme') }).catch(() => {});
  } else if (key === 'density') {
    root.dataset.density = prefs.get('density') === 'compact' ? 'compact' : 'comfortable';
  } else if (key === 'zoom') {
    const factor = clampZoom(Number(prefs.get('zoom')) || 1);
    if (isNative()) {
      invoke('window_set_zoom', { factor }).catch(() => {
        root.style.zoom = String(factor);
      });
    } else {
      root.style.zoom = factor === 1 ? '' : String(factor);
    }
  } else if (key === 'sidebar') {
    root.classList.toggle('km-sidebar-hidden', prefs.get('sidebar') === 'hidden');
  }
}

function clampZoom(value) {
  return Math.min(1.5, Math.max(0.8, Math.round(value * 100) / 100));
}

export function zoomStep(direction) {
  const current = clampZoom(Number(prefs.get('zoom')) || 1);
  if (direction === 0) {
    prefs.set('zoom', '1');
    return 1;
  }
  const next = direction > 0
    ? ZOOM_STEPS.find((step) => step > current + 0.001) ?? ZOOM_STEPS.at(-1)
    : [...ZOOM_STEPS].reverse().find((step) => step < current - 0.001) ?? ZOOM_STEPS[0];
  prefs.set('zoom', String(next));
  return next;
}

/** Bütün tercihleri uygular; sistem teması değişince de yeniden uygular. */
export function applyAllPrefs() {
  for (const key of Object.keys(DEFAULTS)) applyPref(key);
  darkQuery?.addEventListener?.('change', () => {
    if (prefs.get('theme') === 'system') applyPref('theme');
  });
}

// ================================================================== komutlar

const commands = new Map();
let lastRun = { id: '', source: '', at: 0 };

/**
 * Komut tanımlar. `run(source)`; `when()` false dönerse komut çalışmaz
 * (ör. oturum kapalıyken "Yazdır").
 */
export function defineCommand(id, run, { when = null } = {}) {
  commands.set(id, { run, when });
}

/**
 * Komutu çalıştırır. `source`: `menu` · `key` · `context` · `ui`.
 * @returns {boolean} çalıştı mı
 */
export function runCommand(id, source = 'ui', arg = undefined) {
  const now = performance.now();
  // AYNI KOMUT, FARKLI YOL, 350 ms: Windows'ta menü hızlandırıcısı ve sayfanın
  // tuş olayı birlikte gelebilir — ikincisi yok sayılır. Aynı yoldan tekrar
  // (tuşa iki kez basmak) yok sayılmaz.
  if (lastRun.id === id && lastRun.source !== source && now - lastRun.at < 350) return false;
  lastRun = { id, source, at: now };
  const command = commands.get(id);
  if (!command) return false;
  if (command.when && !command.when()) return false;
  try {
    const out = command.run(source, arg);
    if (out && typeof out.catch === 'function') {
      out.catch((error) => console.error(`[komut] ${id}:`, error));
    }
  } catch (error) {
    console.error(`[komut] ${id}:`, error);
  }
  return true;
}

/**
 * KLAVYE TABLOSU. `Mod` = macOS'ta ⌘, ötekilerde Ctrl. Fiziksel tuş kodu
 * (`event.code`) kullanılır: menü hızlandırıcısı da fiziksel tuşa bakar ve
 * Türkçe Q klavyede `[`/`]` ayrı tuş değildir (Ctrl+Ğ / Ctrl+Ü olur).
 * Yakınlaştırma ise BASILAN KARAKTERE bakar: Türkçe klavyede `-` başka bir
 * fiziksel tuştadır.
 */
export const SHORTCUTS = [
  { keys: 'Mod+K', code: 'KeyK', id: 'search', label: 'Genel arama (öğrenci, telefon, kart, ekran)' },
  { keys: 'Mod+F', code: 'KeyF', id: 'find', label: 'Sayfada bul' },
  { keys: 'Mod+G', code: 'KeyG', id: 'find-next', label: 'Sonrakini bul' },
  { keys: 'Mod+Shift+G', code: 'KeyG', shift: true, id: 'find-prev', label: 'Öncekini bul' },
  { keys: 'Mod+P', code: 'KeyP', id: 'print', label: 'Yazdır (ekranın kendi yazdır eylemi)' },
  { keys: 'Mod+Shift+E', code: 'KeyE', shift: true, id: 'export', label: 'Görünen listeyi dışa aktar' },
  { keys: 'Mod+[', code: 'BracketLeft', id: 'back', label: 'Geri (önceki ekran)' },
  { keys: 'Mod+]', code: 'BracketRight', id: 'forward', label: 'İleri' },
  { keys: 'Mod+1…9', code: 'Digit', id: 'go', label: 'Sık kullanılan 1.–9. ekran' },
  { keys: 'Mod+R', code: 'KeyR', id: 'refresh', label: 'Ekranı yenile (veriyi tazele)' },
  { keys: 'F5', code: 'F5', bare: true, id: 'refresh', label: 'Ekranı yenile', hidden: true },
  { keys: 'F11', code: 'F11', bare: true, id: 'fullscreen', label: 'Tam ekran', notMac: true },
  { keys: 'Mod+Ctrl+F', code: 'KeyF', ctrl: true, id: 'fullscreen', label: 'Tam ekran', onlyMac: true },
  { keys: 'Mod+,', code: 'Comma', id: 'prefs', label: 'Tercihler' },
  { keys: 'Mod++', char: ['+', '='], id: 'zoom-in', label: 'Yakınlaştır' },
  { keys: 'Mod+-', char: ['-', '_'], id: 'zoom-out', label: 'Uzaklaştır' },
  { keys: 'Mod+0', char: ['0'], code: 'Digit0', id: 'zoom-reset', label: 'Gerçek boyut' },
  { keys: 'Mod+\\', code: 'Backslash', id: 'sidebar', label: 'Kenar çubuğunu gizle/göster' },
  { keys: 'Mod+L', code: 'KeyL', id: 'go-filter', label: 'Ekran listesinde ara' },
  { keys: 'Mod+/', code: 'Slash', id: 'shortcuts', label: 'Klavye kısayolları' },
  { keys: 'Mod+Shift+L', code: 'KeyL', shift: true, id: 'signout', label: 'Oturumu kapat' },
  { keys: 'Alt+←', code: 'ArrowLeft', alt: true, id: 'back', label: 'Geri', notMac: true },
  { keys: 'Alt+→', code: 'ArrowRight', alt: true, id: 'forward', label: 'İleri', notMac: true },
];

/** Tuş olayı → komut kimliği (yoksa null). Saf; sınanır. */
export function matchShortcut(event, mac = isMac()) {
  const mod = mac ? event.metaKey : event.ctrlKey;
  const otherMod = mac ? event.ctrlKey : event.metaKey;
  for (const entry of SHORTCUTS) {
    if (entry.notMac && mac) continue;
    if (entry.onlyMac && !mac) continue;
    if (entry.bare) {
      if (event.code === entry.code && !event.ctrlKey && !event.metaKey && !event.altKey) return entry.id;
      continue;
    }
    if (entry.alt) {
      if (event.altKey && !event.ctrlKey && !event.metaKey && event.code === entry.code) return entry.id;
      continue;
    }
    if (!mod || event.altKey) continue;
    if (entry.ctrl ? !event.ctrlKey : otherMod) continue;
    if (entry.char) {
      if (entry.char.includes(event.key)) return entry.id;
      if (entry.code && event.code === entry.code && !event.shiftKey) return entry.id;
      continue;
    }
    if (Boolean(entry.shift) !== Boolean(event.shiftKey)) continue;
    if (entry.code === 'Digit') {
      const match = /^Digit([1-9])$/.exec(event.code || '');
      if (match) return `go:${match[1]}`;
      continue;
    }
    if (event.code === entry.code) return entry.id;
  }
  return null;
}

export function isTyping(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable
      || target.tagName === 'INPUT'
      || target.tagName === 'TEXTAREA'
      || target.tagName === 'SELECT');
}

// =============================================================== bağlam menüsü

let contextItems = [];
let domMenu = null;

/**
 * Bağlam menüsü satırları — hedefe göre (saf olmayan ama DOM'dan başka
 * bağımlılığı olmayan işlev; sınanır).
 *
 * Satır: `{id, label, run, enabled?, accelerator?}` ya da `{separator: true}`.
 */
export function buildContextItems(target, { selection = '', command = runCommand } = {}) {
  const items = [];
  const sep = () => {
    if (items.length && !items.at(-1).separator) items.push({ separator: true });
  };
  const exec = (name) => () => {
    try { document.execCommand(name); } catch { /* desteklenmiyor */ }
  };
  const editable = target?.closest?.('input, textarea, [contenteditable=""], [contenteditable="true"]');

  if (editable && !(editable.tagName === 'INPUT'
    && ['checkbox', 'radio', 'button', 'submit', 'range', 'color', 'file'].includes(editable.type))) {
    const readOnly = editable.readOnly || editable.disabled;
    items.push(
      { id: 'undo', label: 'Geri al', run: exec('undo'), enabled: !readOnly, accelerator: keyLabel('Mod+Z') },
      { id: 'redo', label: 'Yinele', run: exec('redo'), enabled: !readOnly, accelerator: keyLabel(isMac() ? 'Mod+Shift+Z' : 'Mod+Y') },
      { separator: true },
      { id: 'cut', label: 'Kes', run: exec('cut'), enabled: !readOnly && Boolean(selection), accelerator: keyLabel('Mod+X') },
      { id: 'copy', label: 'Kopyala', run: exec('copy'), enabled: Boolean(selection), accelerator: keyLabel('Mod+C') },
      { id: 'paste', label: 'Yapıştır', run: () => pasteInto(editable), enabled: !readOnly, accelerator: keyLabel('Mod+V') },
      { separator: true },
      { id: 'select-all', label: 'Tümünü seç', run: () => { editable.focus(); editable.select?.() ?? exec('selectAll')(); }, accelerator: keyLabel('Mod+A') },
    );
    return items;
  }

  if (selection) {
    const short = selection.length > 28 ? `${selection.slice(0, 27)}…` : selection;
    items.push(
      { id: 'copy', label: 'Kopyala', run: () => copy(selection), accelerator: keyLabel('Mod+C') },
      { id: 'search-selection', label: `“${short}” için genel arama`, run: () => command('search-text', 'context', selection) },
    );
  }

  const link = target?.closest?.('a[href]');
  if (link && /^https?:|^mailto:|^tel:/.test(link.getAttribute('href') || '')) {
    sep();
    items.push({ id: 'copy-link', label: 'Bağlantı adresini kopyala', run: () => copy(link.href) });
  }

  const image = target?.closest?.('img');
  if (image?.src) {
    sep();
    items.push(
      { id: 'save-image', label: 'Görseli farklı kaydet…', run: () => saveImage(image) },
      { id: 'copy-image-url', label: 'Görsel adresini kopyala', run: () => copy(image.src), enabled: /^https?:/.test(image.src) },
    );
  }

  const row = target?.closest?.('[role="row"]:not(.kit-head), tbody tr');
  if (row) {
    const table = row.closest('[role="table"], table');
    const cell = target.closest('[role="cell"], [role="gridcell"], .kit-cell, td');
    sep();
    items.push(
      { id: 'copy-row', label: 'Satırı kopyala', run: () => copy(rowText(row)) },
      { id: 'copy-cell', label: 'Hücreyi kopyala', run: () => copy(cleanText(cell?.textContent)), enabled: Boolean(cell && cleanText(cell.textContent)) },
      { id: 'open-row', label: 'Ayrıntıyı aç', run: () => openRow(row) },
    );
    if (table) {
      items.push({ id: 'export-table', label: 'Tabloyu dışa aktar (CSV)…', run: () => exportTable(table) });
    }
  }

  // Panel kendi satırlarını ekleyebilir (K1: kabuk panelin işini bilmez).
  //   el.addEventListener('km-contextmenu', (e) => e.detail.add({ label: 'Yoklamayı aç', run: () => … }));
  const extra = [];
  try {
    const event = new CustomEvent('km-contextmenu', {
      bubbles: true,
      detail: { add: (entry) => { if (entry?.label && typeof entry.run === 'function') extra.push(entry); } },
    });
    target?.dispatchEvent?.(event);
  } catch { /* olay kurulamadıysa yalnız kabuk satırları */ }
  if (extra.length) {
    sep();
    extra.forEach((entry, index) => items.push({ id: `panel-${index}`, enabled: true, ...entry }));
  }

  sep();
  items.push(
    { id: 'back', label: 'Geri', run: () => command('back', 'context'), accelerator: keyLabel('Mod+[') },
    { id: 'forward', label: 'İleri', run: () => command('forward', 'context'), accelerator: keyLabel('Mod+]') },
    { id: 'refresh', label: 'Yenile', run: () => command('refresh', 'context'), accelerator: keyLabel('Mod+R') },
    { separator: true },
    { id: 'find', label: 'Sayfada bul…', run: () => command('find', 'context'), accelerator: keyLabel('Mod+F') },
    { id: 'print', label: 'Yazdır…', run: () => command('print', 'context'), accelerator: keyLabel('Mod+P') },
  );
  return items;
}

function cleanText(text) {
  return String(text ?? '').replace(/\s+/g, ' ').trim();
}

function cellsOf(row) {
  const cells = [...row.querySelectorAll('[role="cell"], [role="gridcell"], [role="columnheader"], .kit-cell, td, th')]
    .filter((cell) => cell.closest('[role="row"], tr') === row);
  return cells.length ? cells : [...row.children];
}

export function rowText(row) {
  return cellsOf(row).map((cell) => cleanText(cell.textContent)).filter(Boolean).join('\t');
}

function openRow(row) {
  // Satırın kendi "aç" davranışı: tıklama (dataTable `onRow`) ya da içindeki
  // ilk bağlantı/düğme.
  const action = row.querySelector('a[href], button:not([disabled])');
  if (row.onclick || row.hasAttribute('tabindex') || !action) row.click();
  else action.click();
}

/** Görünen tabloyu CSV'ye döker — sunucuya gitmez. Saf; sınanır. */
export function tableCsv(table) {
  const rows = [...table.querySelectorAll('[role="row"], tr')]
    .filter((row) => row.closest('[role="table"], table') === table);
  const escape = (cell) => `"${String(cell ?? '').replace(/"/g, '""')}"`;
  return rows
    .map((row) => cellsOf(row).map((cell) => escape(cleanText(cell.textContent))).join(';'))
    .filter((line) => line.replace(/[";]/g, '').trim())
    .join('\r\n');
}

export async function exportTable(table) {
  const title = cleanText(document.getElementById('panel-title')?.textContent) || 'tablo';
  const day = new Date();
  const stamp = `${day.getFullYear()}-${String(day.getMonth() + 1).padStart(2, '0')}-${String(day.getDate()).padStart(2, '0')}`;
  const name = `${title.toLocaleLowerCase('tr').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '')}-${stamp}.csv`;
  await saveFile({ name, data: `﻿${tableCsv(table)}`, filters: [{ name: 'CSV', extensions: ['csv'] }] });
}

async function saveImage(image) {
  try {
    const response = await fetch(image.src);
    const blob = await response.blob();
    const ext = (blob.type.split('/')[1] || 'png').replace('jpeg', 'jpg').replace('svg+xml', 'svg');
    const base = (image.alt || 'gorsel').toLocaleLowerCase('tr').replace(/[^\p{L}\p{N}]+/gu, '-').slice(0, 40) || 'gorsel';
    await saveFile({ name: `${base}.${ext}`, data: blob });
  } catch (error) {
    console.error('[kabuk] görsel kaydedilemedi:', error);
  }
}

async function copy(text) {
  try {
    await navigator.clipboard.writeText(String(text ?? ''));
  } catch {
    // Pano izni yoksa eski yol: geçici metin alanı.
    const area = document.createElement('textarea');
    area.value = String(text ?? '');
    area.style.cssText = 'position:fixed;opacity:0;pointer-events:none';
    document.body.append(area);
    area.select();
    try { document.execCommand('copy'); } catch { /* yok */ }
    area.remove();
  }
}

async function pasteInto(field) {
  field.focus();
  let ok = false;
  try { ok = document.execCommand('paste'); } catch { ok = false; }
  if (ok) return;
  try {
    const text = await navigator.clipboard.readText();
    if (!document.execCommand('insertText', false, text) && 'value' in field) {
      const start = field.selectionStart ?? field.value.length;
      const end = field.selectionEnd ?? field.value.length;
      field.value = field.value.slice(0, start) + text + field.value.slice(end);
      field.dispatchEvent(new Event('input', { bubbles: true }));
    }
  } catch {
    /* pano okunamadı — kullanıcı Ctrl+V ile yapıştırabilir */
  }
}

/** Bağlam menüsünü gösterir: masaüstünde işletim sisteminin kendi menüsü. */
export async function showContextMenu(items, { x = 0, y = 0 } = {}) {
  contextItems = items;
  if (isNative()) {
    try {
      await invoke('context_menu', {
        items: items.map((item) => (item.separator
          ? { separator: true }
          : { id: item.id, label: item.label, enabled: item.enabled !== false, accelerator: item.accelerator || null })),
      });
      return;
    } catch (error) {
      console.warn('[kabuk] yerel bağlam menüsü açılamadı, sayfa içi menüye düşülüyor:', error);
    }
  }
  showDomMenu(items, x, y);
}

function runContextItem(id) {
  const item = contextItems.find((entry) => entry.id === id);
  if (item && item.enabled !== false) {
    try {
      const out = item.run();
      out?.catch?.((error) => console.error('[bağlam] satır hata verdi:', error));
    } catch (error) {
      console.error('[bağlam] satır hata verdi:', error);
    }
  }
}

/** Tarayıcıda (ve yerel menü açılamazsa) sayfa içi menü — klavyeyle de gezilir. */
function showDomMenu(items, x, y) {
  closeDomMenu();
  const menu = document.createElement('div');
  menu.className = 'km-ctx';
  menu.setAttribute('role', 'menu');
  menu.tabIndex = -1;
  for (const item of items) {
    if (item.separator) {
      const line = document.createElement('div');
      line.className = 'km-ctx-sep';
      line.setAttribute('role', 'separator');
      menu.append(line);
      continue;
    }
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'km-ctx-item';
    row.setAttribute('role', 'menuitem');
    row.disabled = item.enabled === false;
    const label = document.createElement('span');
    label.textContent = item.label;
    row.append(label);
    if (item.accelerator) {
      const keys = document.createElement('kbd');
      keys.textContent = item.accelerator;
      row.append(keys);
    }
    row.addEventListener('click', () => {
      closeDomMenu();
      runContextItem(item.id);
    });
    menu.append(row);
  }
  document.body.append(menu);
  const width = menu.offsetWidth || 220;
  const height = menu.offsetHeight || 200;
  menu.style.left = `${Math.max(4, Math.min(x, window.innerWidth - width - 4))}px`;
  menu.style.top = `${Math.max(4, Math.min(y, window.innerHeight - height - 4))}px`;
  const rows = () => [...menu.querySelectorAll('.km-ctx-item:not([disabled])')];
  const onKey = (event) => {
    const list = rows();
    const index = list.indexOf(document.activeElement);
    if (event.key === 'Escape') { event.preventDefault(); closeDomMenu(); }
    else if (event.key === 'ArrowDown') { event.preventDefault(); list[(index + 1) % list.length]?.focus(); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); list[(index - 1 + list.length) % list.length]?.focus(); }
  };
  const onOutside = (event) => { if (!menu.contains(event.target)) closeDomMenu(); };
  menu.addEventListener('keydown', onKey);
  document.addEventListener('pointerdown', onOutside, true);
  window.addEventListener('blur', closeDomMenu, { once: true });
  domMenu = { menu, onOutside };
  (rows()[0] || menu).focus();
}

export function closeDomMenu() {
  if (!domMenu) return;
  domMenu.menu.remove();
  document.removeEventListener('pointerdown', domMenu.onOutside, true);
  domMenu = null;
}

// ============================================================ kurulum (bir kez)

/**
 * Bütün genel dinleyicileri kurar. `app.js` açılışta bir kez çağırır.
 *
 * @param {object} o
 *   onMenu(id)     — `km:*` menü kimliği (önek atılmış) → komut
 *   isLoginOpen()  — giriş/eşleme ekranı açık mı (kısayollar orada kapalı)
 */
export function installNative({ isLoginOpen = () => false } = {}) {
  // Yerel menüden gelen olay (Rust → eval).
  window.__kmNative = {
    menu(id) {
      const text = String(id || '');
      if (text.startsWith('ctx:')) runContextItem(text.slice(4));
      else if (text.startsWith('km:')) runCommand(text.slice(3), 'menu');
    },
  };

  // ---- kısayollar (yakalama evresi: panelin kendi dinleyicisinden önce)
  window.addEventListener('keydown', (event) => {
    if (event.defaultPrevented || event.isComposing) return;
    const id = matchShortcut(event);
    if (id) {
      // Giriş ekranında yalnız uygulama düzeyindeki kısayollar.
      if (isLoginOpen() && !['prefs', 'zoom-in', 'zoom-out', 'zoom-reset', 'fullscreen', 'shortcuts'].includes(id)) {
        event.preventDefault();
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      runCommand(id, 'key');
      return;
    }
    // Backspace: yazı alanı dışında sayfa geçmişine gitmesin.
    if (event.key === 'Backspace' && !isTyping(event.target) && !isLoginOpen()) {
      event.preventDefault();
    }
  }, true);

  // ---- fare yan tuşları (geri/ileri): sayfa geçmişi değil, ekran geçmişi.
  window.addEventListener('mouseup', (event) => {
    if (event.button === 3 || event.button === 4) {
      event.preventDefault();
      if (!isLoginOpen()) runCommand(event.button === 3 ? 'back' : 'forward', 'mouse');
    }
  });
  window.addEventListener('auxclick', (event) => {
    if (event.button === 3 || event.button === 4) event.preventDefault();
  });

  // ---- Ctrl+tekerlek / kıstırma: rastgele yakınlaştırma yok.
  window.addEventListener('wheel', (event) => {
    if (event.ctrlKey || event.metaKey) event.preventDefault();
  }, { passive: false });
  for (const name of ['gesturestart', 'gesturechange']) {
    window.addEventListener(name, (event) => event.preventDefault());
  }

  // ---- bağlantı/görsel sürüklenmez; açıkça sürüklenebilir işaretli olan sürüklenir.
  document.addEventListener('dragstart', (event) => {
    const target = event.target;
    if (target?.closest?.('[draggable="true"]')) return;
    if (target?.nodeType === 3 || target?.closest?.('a, img')) event.preventDefault();
  });

  // ---- pencereye dosya bırakmak sayfayı O DOSYAYA götürmesin. Bırakma alanı
  // olan bileşen (görsel alanı) kendi `drop`unda `preventDefault` çağırır;
  // burada yalnız kimsenin üstlenmediği bırakma iptal edilir.
  window.addEventListener('dragover', (event) => {
    if (!event.defaultPrevented) {
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'none';
    }
  });
  window.addEventListener('drop', (event) => {
    if (!event.defaultPrevented) event.preventDefault();
  });

  // ---- bağlam menüsü
  window.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    if (isLoginOpen()) return;
    const selection = String(window.getSelection?.() || '').trim();
    const target = event.target instanceof Element ? event.target : event.target?.parentElement;
    const items = buildContextItems(target, { selection });
    showContextMenu(items, { x: event.clientX, y: event.clientY });
  });

  // ---- `<a download>` → yerel "Farklı kaydet". Paneller indirmeyi çoğu
  // zaman belgeye EKLENMEMİŞ bir bağlantıyla yapıyor (`link.click()`); olay
  // belgeye hiç ulaşmadığı için yakalama ancak burada, `click` üzerinde olur.
  if (isNative()) {
    const original = HTMLAnchorElement.prototype.click;
    HTMLAnchorElement.prototype.click = function click() {
      if (this.download && !this.__kmNativeDownload && /^(blob:|data:)/.test(this.href)) {
        nativeDownload(this.href, this.download);
        return;
      }
      original.call(this);
    };
    document.addEventListener('click', (event) => {
      const link = event.target?.closest?.('a[download]');
      if (!link || link.__kmNativeDownload || !/^(blob:|data:)/.test(link.href)) return;
      event.preventDefault();
      nativeDownload(link.href, link.download);
    }, true);
    // Dış bağlantılar web görünümünün İÇİNDE açılmasın: uygulama kendi
    // penceresini kaybederdi. Sistem tarayıcısına gider (Rust tarafında
    // `on_navigation` / `on_new_window` da aynı kapıyı tutar).
    document.addEventListener('click', (event) => {
      if (event.defaultPrevented) return;
      const link = event.target?.closest?.('a[href]');
      if (!link || link.download) return;
      const href = link.getAttribute('href') || '';
      if (/^(https?:|mailto:|tel:)/i.test(href)) {
        event.preventDefault();
        openExternal(href);
      }
    });
    // `window.open`: dış adres → sistem tarayıcısı; bellekteki dosya
    // (`blob:`/`data:` — ör. PDF) → varsayılan uygulama (Önizleme, Acrobat).
    // WebView2'de tarayıcı benzeri açılır pencere, WKWebView'da hiçbir şey
    // olmuyordu. Çağırana boş olmayan bir tutamaç döner: "açılamadı, indir"
    // yedek yolları gereksiz yere çalışmasın.
    const nativeWindowOpen = window.open?.bind(window);
    window.open = function open(url, target, features) {
      const href = String(url ?? '');
      if (/^(https?:|mailto:|tel:)/i.test(href)) {
        openExternal(href);
        return handleStub();
      }
      if (/^(blob:|data:)/i.test(href)) {
        openInDefaultApp(href);
        return handleStub();
      }
      return nativeWindowOpen ? nativeWindowOpen(url, target, features) : null;
    };
  }

  // ---- macOS bütünleşik başlık çubuğu: sürükleme bölgesi.
  if (isNative() && isMac()) {
    document.addEventListener('mousedown', (event) => {
      if (event.button !== 0) return;
      const zone = event.target?.closest?.('[data-km-drag]');
      if (!zone || event.target.closest('button, a, input, select, textarea, [role="button"], [contenteditable]')) return;
      if (event.detail === 2) {
        invoke('window_toggle_maximize').catch(() => {});
      } else {
        invoke('window_start_drag').catch(() => {});
      }
    });
  }
}

function handleStub() {
  return { closed: false, close() {}, focus() {}, blur() {}, postMessage() {}, document: null };
}

export function openExternal(url) {
  invoke('native_open_url', { url: String(url) })
    .catch((error) => shellToast(`Bağlantı açılamadı: ${error}`, 'bad'));
}

async function openInDefaultApp(href) {
  try {
    const blob = await (await fetch(href)).blob();
    const ext = (blob.type.split('/')[1] || 'bin').replace('jpeg', 'jpg').replace('svg+xml', 'svg')
      .replace('vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx');
    const title = (document.getElementById('panel-title')?.textContent || 'belge').trim();
    const name = `${title.toLocaleLowerCase('tr').replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '') || 'belge'}-${Date.now() % 100000}.${ext}`;
    const bytes = new Uint8Array(await blob.arrayBuffer());
    let text = '';
    for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    await invoke('native_open_temp_file', { name, base64: btoa(text) });
  } catch (error) {
    console.error('[kabuk] dosya açılamadı:', error);
    shellToast('Dosya açılamadı.', 'bad');
  }
}

async function nativeDownload(href, name) {
  try {
    const blob = await (await fetch(href)).blob();
    await saveFile({ name: name || 'dosya', data: blob });
  } catch (error) {
    console.error('[kabuk] dosya kaydedilemedi:', error);
  }
}

// ==================================================================== menü

/** Menü çubuğunu arayüz durumuyla eşitler (sık ekranlar, tema, geri/ileri). */
let menuTimer = null;
export function syncMenu(state) {
  if (!isNative()) return;
  clearTimeout(menuTimer);
  menuTimer = setTimeout(() => {
    invoke('menu_sync', {
      state: {
        signedIn: Boolean(state.signedIn),
        screens: (state.screens || []).slice(0, 9),
        theme: prefs.get('theme'),
        density: prefs.get('density'),
        canBack: Boolean(state.canBack),
        canForward: Boolean(state.canForward),
      },
    }).catch((error) => console.warn('[kabuk] menü eşitlenemedi:', error));
  }, 60);
}

// ============================================================== kabuk bildirimi

let toastTimer = null;
/** Kabuğun kısa bildirimi (panel dışı komutların geri bildirimi). */
export function shellToast(message, tone = '') {
  let node = document.getElementById('km-toast');
  if (!node) {
    node = document.createElement('div');
    node.id = 'km-toast';
    node.className = 'km-toast';
    node.setAttribute('role', 'status');
    node.setAttribute('aria-live', 'polite');
    document.body.append(node);
  }
  clearTimeout(toastTimer);
  node.textContent = message;
  node.className = `km-toast show${tone ? ` ${tone}` : ''}`;
  toastTimer = setTimeout(() => { node.className = 'km-toast'; }, 3800);
}
