// Yerleşim bileşenleri — kart, sekme, KPI, rozet, çip, çekmece, boş durum.
//
// Tek kopya (ADR 0011). Hepsi düğüm döndürür; hiçbiri kendi verisini çekmez
// ve hiçbiri panelin durumunu bilmez.

import { button, h } from './kit.js';

/** Başlıklı içerik kutusu. */
export function card(title, content, hint) {
  const box = h('section', 'kit-card');
  if (title) {
    const head = h('div', 'kit-card-head');
    head.append(h('h3', 'kit-card-title', title));
    if (hint) head.append(h('span', 'kit-card-hint', hint));
    box.append(head);
  }
  if (content) box.append(content);
  return box;
}

/**
 * Sekme şeridi.
 *
 *   const tabs = tabBar([{key:'list', label:'Liste'}, {key:'set', label:'Ayarlar'}],
 *                       'list', (key) => render(key));
 *   view.append(tabs.node);
 *   tabs.select('set');            // programla değiştir
 *   tabs.badge('list', 7);         // sayaç rozeti
 */
export function tabBar(items, active, onChange) {
  const node = h('div', 'kit-tabs');
  node.setAttribute('role', 'tablist');
  const buttons = new Map();
  let current = active ?? items[0]?.key;

  const paint = () => {
    for (const [key, entry] of buttons) {
      const on = key === current;
      entry.node.classList.toggle('on', on);
      entry.node.setAttribute('aria-selected', on ? 'true' : 'false');
      entry.node.tabIndex = on ? 0 : -1;
    }
  };

  const select = (key, notify = true) => {
    if (!buttons.has(key) || key === current) return;
    current = key;
    paint();
    if (notify) onChange?.(key);
  };

  const keys = items.map((item) => item.key);
  for (const item of items) {
    const tab = h('button', 'kit-tab', item.label);
    tab.type = 'button';
    tab.setAttribute('role', 'tab');
    tab.addEventListener('click', () => select(item.key));
    // Sekmeler arasında ok tuşuyla gezinme — WAI-ARIA tablist deseni.
    tab.addEventListener('keydown', (event) => {
      const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const index = (keys.indexOf(current) + step + keys.length) % keys.length;
      select(keys[index]);
      buttons.get(keys[index])?.node.focus();
    });
    buttons.set(item.key, { node: tab, badge: null });
    node.append(tab);
  }
  paint();

  return {
    node,
    select,
    get active() { return current; },
    /** Sekme başlığına sayaç rozeti koyar; 0/undefined kaldırır. */
    badge(key, count) {
      const entry = buttons.get(key);
      if (!entry) return;
      entry.badge?.remove();
      entry.badge = null;
      if (!count) return;
      entry.badge = h('span', 'kit-tab-badge', String(count));
      entry.node.append(entry.badge);
    },
  };
}

/**
 * KPI şeridi. Her kutu: {label, value, tone?, delta?, title?}
 * `delta`: {percent, title} — yüzde farkı, yönü oktan okunur.
 *
 * Renk tek başına anlam taşımaz: değer her zaman yazıyla da orada.
 */
export function kpiRow(tiles) {
  const node = h('div', 'kit-kpi');
  for (const tile of tiles) {
    const box = h('div', 'kit-kpi-tile');
    box.append(h('span', 'kit-kpi-label', tile.label));
    box.append(h('b', `kit-kpi-value${tile.tone ? ` ${tile.tone}` : ''}`, String(tile.value)));
    if (tile.delta && tile.delta.percent !== null && tile.delta.percent !== undefined) {
      const up = Number(tile.delta.percent) >= 0;
      const chip = h('span', `kit-delta ${up ? 'up' : 'down'}`,
        `${up ? '▲' : '▼'} %${Math.abs(Number(tile.delta.percent)).toLocaleString('tr-TR')}`);
      if (tile.delta.title) chip.title = tile.delta.title;
      box.append(chip);
    }
    if (tile.title) box.title = tile.title;
    node.append(box);
  }
  return node;
}

/** Rozet. `tone`: '' | 'good' | 'bad' | 'warn' | 'info' | 'dim'. */
export function badge(text, tone = '') {
  return h('span', `kit-badge${tone ? ` ${tone}` : ''}`, text);
}

/**
 * Süzgeç çipi şeridi. Her çip: {key, label, count?}
 * Tek seçim; aynı çipe tekrar basmak seçimi kaldırır (`null` gönderir).
 */
export function chipRow(items, active, onChange) {
  const node = h('div', 'kit-chips');
  const buttons = new Map();
  let current = active ?? null;

  const paint = () => {
    for (const [key, chip] of buttons) chip.classList.toggle('on', key === current);
  };

  for (const item of items) {
    const chip = h('button', 'kit-chip', item.label);
    chip.type = 'button';
    if (item.count !== undefined && item.count !== null) {
      chip.append(h('span', 'kit-chip-count', String(item.count)));
    }
    chip.addEventListener('click', () => {
      current = current === item.key ? null : item.key;
      paint();
      onChange?.(current);
    });
    buttons.set(item.key, chip);
    node.append(chip);
  }
  paint();

  return {
    node,
    get active() { return current; },
    set(key) { current = key; paint(); },
    /** Sayaçları veriden sonra günceller. */
    counts(map) {
      for (const [key, chip] of buttons) {
        const slot = chip.querySelector('.kit-chip-count');
        const value = map?.[key];
        if (slot) slot.textContent = value === undefined || value === null ? '' : String(value);
      }
    },
  };
}

/**
 * Sağdan açılan detay çekmecesi.
 *
 * Overlay panelin KÖKÜNE eklenir, `document.body`'ye değil: panel
 * değiştiğinde kabuk `root.replaceChildren()` yapıyor ve body'deki bir
 * overlay orada asılı kalırdı.
 */
export function drawer(root, { title, subtitle, actions = [], onClose } = {}) {
  const overlay = h('div', 'kit-overlay');
  const panel = h('aside', 'kit-drawer');
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-modal', 'true');
  if (title) panel.setAttribute('aria-label', title);

  const head = h('div', 'kit-drawer-head');
  const heading = h('div');
  heading.append(h('div', 'kit-drawer-title', title || ''));
  if (subtitle) heading.append(h('div', 'kit-drawer-sub', subtitle));
  head.append(heading, h('span', 'kit-spacer'), ...actions);

  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    onClose?.();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };

  head.append(button('Kapat', { variant: 'ghost', onClick: close }));
  const body = h('div', 'kit-drawer-body');
  panel.append(head, body);
  overlay.append(panel);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  document.addEventListener('keydown', onKey);
  root.append(overlay);
  panel.focus?.();

  return { node: panel, body, close, setTitle: (text) => { heading.firstChild.textContent = text; } };
}

/**
 * İki sütunlu çalışma alanı: liste + düzenleyici.
 * `sizes` grid şablonudur; varsayılan solda esnek, sağda sabit 400px.
 */
export function splitView(left, right, sizes = 'minmax(0, 1fr) 400px') {
  const node = h('div', 'kit-split');
  node.style.gridTemplateColumns = sizes;
  node.append(left, right);
  return node;
}

/**
 * Boş durum. NE OLDUĞUNU değil SONUCUNU söyler ve çıkış yolu verir.
 * "Kayıt bulunamadı" değil: "Bu filtreye uyan sipariş yok — [Filtreyi temizle]".
 */
export function emptyState({ title, text, actions = [] } = {}) {
  const node = h('div', 'kit-empty');
  if (title) node.append(h('div', 'kit-empty-title', title));
  if (text) node.append(h('div', 'kit-empty-text', text));
  if (actions.length) {
    const row = h('div', 'kit-empty-actions');
    row.append(...actions);
    node.append(row);
  }
  return node;
}

/** Uyarı/bilgi kutusu. `tone`: 'bad' | 'warn' | 'info' | 'good'. */
export function alertBox(text, tone = 'info') {
  return h('div', `kit-alert ${tone}`, text);
}

/** Açıklama kutusu — kesikli çerçeveli, ekranın ne yaptığını anlatır. */
export function hintBox(text) {
  return h('div', 'kit-hint', text);
}

/**
 * Adımlı ilerleme. Belirsiz bekleme yerine nerede olduğumuzu söyler.
 *
 *   const bar = progress(['Dışa aktarılıyor', 'Sıkıştırılıyor', 'Doğrulanıyor']);
 *   bar.step(1);            // ikinci adım
 *   bar.done('Yazıldı');
 */
export function progress(steps = []) {
  const node = h('div');
  const track = h('div', 'kit-progress');
  const fill = h('div', 'kit-progress-fill');
  fill.style.width = '0%';
  track.append(fill);
  const label = h('div', 'kit-progress-label', steps[0] || '');
  node.append(track, label);

  return {
    node,
    step(index) {
      const total = Math.max(1, steps.length);
      fill.style.width = `${Math.round(((index + 1) / total) * 100)}%`;
      label.textContent = steps[index] || '';
    },
    /** Yüzdeyi doğrudan ver (adım listesi yoksa). */
    percent(value, text) {
      fill.style.width = `${Math.max(0, Math.min(100, Number(value) || 0))}%`;
      if (text !== undefined) label.textContent = text;
    },
    done(text = 'Tamamlandı') {
      fill.style.width = '100%';
      label.textContent = text;
    },
  };
}

/**
 * Yükleniyor iskeleti. Boş beyaz alan yerine tablonun ŞEKLİNİ gösterir —
 * kullanıcı ne geleceğini bilir ve ekran bozuk sanılmaz.
 */
export function skeletonRows(count = 8, columns = 5) {
  const node = h('div', 'kit-table');
  for (let row = 0; row < count; row += 1) {
    const line = h('div', 'kit-row');
    line.style.gridTemplateColumns = `repeat(${columns}, minmax(0, 1fr))`;
    for (let cell = 0; cell < columns; cell += 1) {
      const bar = h('span', 'kit-skel');
      // Farklı genişlikler gerçek veriye benzesin diye; sabit desen titremez.
      bar.style.width = `${[92, 68, 80, 55, 74][(row + cell) % 5]}%`;
      line.append(bar);
    }
    node.append(line);
  }
  return node;
}

/** Durum satırı — "Bağlı · 1.419 kayıt" / hata mesajı. */
export function statusLine() {
  const node = h('div', 'kit-status');
  return {
    node,
    set(text, bad = false) {
      node.textContent = text;
      node.classList.toggle('bad', Boolean(bad));
    },
  };
}
