// findbar.js — Sayfada bul (Ctrl/⌘+F), ADR 0032.
//
// Web görünümünün kendi "bul" çubuğu yok (WebView2'de tarayıcınınki açılıyor,
// WKWebView'da hiç yok). Bu çubuk etkin ekranın gövdesinde (`#panel-body`)
// arar: Türkçe harf katlamalı ("ogrenci" → "Öğrenci"), Enter sonraki,
// Shift+Enter önceki, Esc kapatır. Eşleşmeler CSS Highlight API ile
// boyanır (yoksa geçerli eşleşme seçimle gösterilir); ekran değişirse arama
// kendiliğinden yenilenir.
//
// DOM'u DEĞİŞTİRMEZ: metin `<mark>` ile sarılmaz — panelin düğümlerine
// dokunmak, panelin kendi güncellemesini bozardı.

const HIGHLIGHT_ALL = 'km-find';
const HIGHLIGHT_NOW = 'km-find-now';

/** Tek karakteri katlar — uzunluk korunur (eşleşme konumu kaymasın). */
function foldChar(ch) {
  const lower = ch.toLocaleLowerCase('tr');
  const first = lower[0] || ch;
  return ({ ı: 'i', ş: 's', ğ: 'g', ü: 'u', ö: 'o', ç: 'c', â: 'a', î: 'i', û: 'u' })[first] || first;
}

export function foldKeep(text) {
  let out = '';
  for (const ch of String(text)) out += foldChar(ch).length === 1 ? foldChar(ch) : ch;
  return out;
}

/** Görünür metin düğümleri (gizli öğelerin içi aranmaz). */
const SKIP = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SELECT', 'OPTION']);

function textNodes(root) {
  const out = [];
  const visit = (node) => {
    for (const child of node.childNodes || []) {
      if (child.nodeType === 3) {
        if (child.nodeValue && child.nodeValue.trim()) out.push(child);
        continue;
      }
      if (child.nodeType !== 1 || SKIP.has(child.tagName)) continue;
      if (child.hidden || child.getAttribute?.('aria-hidden') === 'true') continue;
      // Görünmeyen (display:none) alt ağaç aranmaz. Gerçek tarayıcıda
      // `offsetParent` bunu söyler; yerleşimi olmayan sınama DOM'unda alan yok.
      if (typeof child.offsetWidth === 'number' && child.offsetParent === null
        && child.getClientRects().length === 0) continue;
      visit(child);
    }
  };
  visit(root);
  return out;
}

/** Kök içindeki eşleşmeler: `[{node, start, end}]`. Saf; sınanır. */
export function findMatches(root, query) {
  const needle = foldKeep(String(query || '').trim());
  if (!needle) return [];
  const out = [];
  for (const node of textNodes(root)) {
    const hay = foldKeep(node.nodeValue);
    let from = 0;
    for (;;) {
      const at = hay.indexOf(needle, from);
      if (at === -1) break;
      out.push({ node, start: at, end: at + needle.length });
      from = at + Math.max(1, needle.length);
      if (out.length >= 2000) return out;
    }
  }
  return out;
}

/**
 * @param {{host: HTMLElement, scope: () => HTMLElement}} o
 *   host  — çubuğun ekleneceği yer (`.panel`)
 *   scope — aranacak kök (her aramada yeniden sorulur: ekran değişebilir)
 */
export function createFindBar({ host, scope }) {
  const doc = host.ownerDocument;
  const bar = doc.createElement('div');
  bar.className = 'km-find';
  bar.setAttribute('role', 'search');
  bar.hidden = true;

  const input = doc.createElement('input');
  input.type = 'text';
  input.className = 'km-find-input';
  input.placeholder = 'Sayfada bul';
  input.setAttribute('aria-label', 'Sayfada bul');
  input.autocomplete = 'off';
  input.spellcheck = false;

  const count = doc.createElement('span');
  count.className = 'km-find-count';
  count.setAttribute('role', 'status');
  count.setAttribute('aria-live', 'polite');

  const mk = (label, title, text) => {
    const button = doc.createElement('button');
    button.type = 'button';
    button.className = 'km-find-btn';
    button.setAttribute('aria-label', title);
    button.title = title;
    button.textContent = text;
    return button;
  };
  const prev = mk('prev', 'Önceki (Shift+Enter)', '↑');
  const next = mk('next', 'Sonraki (Enter)', '↓');
  const close = mk('close', 'Kapat (Esc)', '✕');
  bar.append(input, count, prev, next, close);
  host.append(bar);

  let matches = [];
  let index = -1;
  let observer = null;
  let rerun = null;

  const highlights = globalThis.CSS?.highlights && typeof globalThis.Highlight === 'function';

  function rangeOf(match) {
    const range = doc.createRange();
    range.setStart(match.node, match.start);
    range.setEnd(match.node, match.end);
    return range;
  }

  function paint() {
    const live = matches.filter((match) => match.node.isConnected);
    if (highlights) {
      CSS.highlights.set(HIGHLIGHT_ALL, new Highlight(...live.map(rangeOf)));
      const now = matches[index];
      if (now?.node.isConnected) CSS.highlights.set(HIGHLIGHT_NOW, new Highlight(rangeOf(now)));
      else CSS.highlights.delete(HIGHLIGHT_NOW);
    }
    const now = matches[index];
    if (now?.node.isConnected) {
      const range = rangeOf(now);
      if (!highlights) {
        const selection = doc.getSelection?.();
        selection?.removeAllRanges();
        selection?.addRange(range);
      }
      const box = range.getBoundingClientRect?.();
      const element = now.node.parentNode;
      if (box && element) element.scrollIntoView?.({ block: 'center', inline: 'nearest' });
    }
    count.textContent = input.value.trim()
      ? (matches.length ? `${index + 1}/${matches.length}` : 'Bulunamadı')
      : '';
    bar.classList.toggle('none', Boolean(input.value.trim()) && matches.length === 0);
  }

  function search({ keepIndex = false } = {}) {
    const root = scope();
    const previous = matches[index];
    matches = root ? findMatches(root, input.value) : [];
    if (keepIndex && previous) {
      const same = matches.findIndex((m) => m.node === previous.node && m.start === previous.start);
      index = same >= 0 ? same : Math.min(index, matches.length - 1);
    } else {
      index = matches.length ? 0 : -1;
    }
    paint();
  }

  function step(delta) {
    if (!bar.hidden && !input.value.trim()) return;
    if (!matches.length) search();
    if (!matches.length) return;
    index = (index + delta + matches.length) % matches.length;
    paint();
  }

  function clearPaint() {
    if (highlights) {
      CSS.highlights.delete(HIGHLIGHT_ALL);
      CSS.highlights.delete(HIGHLIGHT_NOW);
    }
  }

  function watch() {
    observer?.disconnect();
    const root = scope();
    if (!root || typeof MutationObserver !== 'function') return;
    observer = new MutationObserver((records) => {
      if (records.every((record) => bar.contains(record.target))) return;
      clearTimeout(rerun);
      rerun = setTimeout(() => search({ keepIndex: true }), 200);
    });
    observer.observe(root, { childList: true, subtree: true, characterData: true });
  }

  input.addEventListener('input', () => search());
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); step(event.shiftKey ? -1 : 1); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); api.close(); }
  });
  prev.addEventListener('click', () => step(-1));
  next.addEventListener('click', () => step(1));
  close.addEventListener('click', () => api.close());

  const api = {
    get open() { return !bar.hidden; },
    show(text) {
      bar.hidden = false;
      // Seçili metin varsa onunla başlar (yerel uygulamalar gibi).
      const picked = text ?? String(doc.getSelection?.() || '').trim();
      if (picked && picked.length < 80) input.value = picked;
      input.focus();
      input.select();
      watch();
      search();
    },
    close() {
      if (bar.hidden) return;
      bar.hidden = true;
      observer?.disconnect();
      observer = null;
      clearTimeout(rerun);
      clearPaint();
      matches = [];
      index = -1;
      scope()?.focus?.({ preventScroll: true });
    },
    next: () => (bar.hidden ? api.show() : step(1)),
    prev: () => (bar.hidden ? api.show() : step(-1)),
    /** Ekran değişti: açıksa yeni ekranda yeniden ara. */
    refresh() {
      if (bar.hidden) return;
      watch();
      search();
    },
    node: bar,
  };
  return api;
}
