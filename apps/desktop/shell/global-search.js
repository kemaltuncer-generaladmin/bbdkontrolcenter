// Küresel arama — üst çubuktaki "Ara" kutusu (ADR 0031).
//
// KULLANICI İSTEĞİ (13.10.2026): "Adam bir öğrenciyi güncellemek için
// saatlerce uğraşıp durmasın." Her ekrandan `/` → yaz → Enter: öğrencinin tek
// kartı açılır. Aynı kutu ekran adlarını da bulur.
//
// KABUK KİMİN ARADIĞINI BİLMEZ (K1). Sonuçları, adı `.search` ile biten arayüz
// yetenekleri verir (`ui-kernel.js` → `searchProviders`):
//
//     { label, search: async (metin, {limit}) => [{id, title, detail?, badge?, open()}] }
//
// ERİŞİLEBİLİRLİK (WCAG 2.2 AA): WAI-ARIA "combobox + listbox" deseni. Odak
// kutuda kalır; ↑/↓ etkin seçeneği `aria-activedescendant` ile taşır, Enter
// açar, Esc önce listeyi kapatır, ikinci Esc kutuyu boşaltıp bırakır. Sonuç
// sayısı `role=status` ile ekran okuyucuya söylenir. Fareyle tıklamak odağı
// kutudan almaz (`mousedown` → `preventDefault`).
//
// HIZ: yazım 120 ms bekler; eski aramanın geç gelen yanıtı yenisini ezmez
// (sıra numarası). Sağlayıcılar aynı anda sorulur; biri patlarsa ötekiler
// yine gelir (K7).

const DEBOUNCE_MS = 120;
const SCREEN_LIMIT = 5;
const RESULT_LIMIT = 8;

/**
 * @param {object} o
 *   input, popup, list, status — DOM düğümleri (index.html)
 *   fold(metin) → katlanmış metin
 *   screens() → menüdeki ekranlar [{id, title, group}]
 *   providers() → Promise<[{name, label, search}]>
 *   openScreen(id)
 * @returns {{focus, enable, reset, run(metin) → Promise<void>, options() → dizi}}
 */
export function createGlobalSearch(o) {
  const { input, popup, list, status, fold } = o;
  let options = [];          // [{id, label, run}] — görünür seçenekler, sırayla
  let active = -1;
  let seq = 0;
  let timer = null;
  let enabled = false;

  const doc = input.ownerDocument;

  function setExpanded(open) {
    popup.hidden = !open;
    input.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (!open) {
      active = -1;
      input.removeAttribute('aria-activedescendant');
    }
  }

  function paintActive() {
    list.querySelectorAll('[role="option"]').forEach((node, index) => {
      const on = index === active;
      node.setAttribute('aria-selected', on ? 'true' : 'false');
      node.classList.toggle('on', on);
      if (on) {
        input.setAttribute('aria-activedescendant', node.id);
        node.scrollIntoView?.({ block: 'nearest' });
      }
    });
    if (active < 0) input.removeAttribute('aria-activedescendant');
  }

  function option(item, index) {
    const li = doc.createElement('li');
    li.id = `gsearch-opt-${index}`;
    li.setAttribute('role', 'option');
    li.setAttribute('aria-selected', 'false');
    li.className = 'gsearch-opt';
    const title = doc.createElement('span');
    title.className = 'gsearch-title';
    title.textContent = item.title;
    li.append(title);
    if (item.detail) {
      const detail = doc.createElement('span');
      detail.className = 'gsearch-detail';
      detail.textContent = item.detail;
      li.append(detail);
    }
    if (item.badge) {
      const badge = doc.createElement('span');
      badge.className = 'gsearch-badge';
      badge.textContent = item.badge;
      li.append(badge);
    }
    li.addEventListener('mousedown', (event) => event.preventDefault());
    li.addEventListener('click', () => choose(index));
    return li;
  }

  function group(label, items) {
    const head = doc.createElement('li');
    head.setAttribute('role', 'presentation');
    head.className = 'gsearch-group';
    head.textContent = label;
    list.append(head);
    for (const item of items) {
      const index = options.length;
      options.push(item);
      list.append(option(item, index));
    }
  }

  function paint(groups, note) {
    list.replaceChildren();
    options = [];
    for (const [label, items] of groups) if (items.length) group(label, items);
    const total = options.length;
    status.textContent = note || (total === 0 ? 'Sonuç yok.'
      : `${total} sonuç. Seçmek için aşağı ok, açmak için Enter.`);
    active = total ? 0 : -1;
    setExpanded(true);
    paintActive();
  }

  async function run(text) {
    const query = String(text || '').trim();
    const mine = ++seq;
    if (!query) {
      list.replaceChildren();
      options = [];
      status.textContent = '';
      setExpanded(false);
      return;
    }
    const needle = fold(query);
    const screens = (o.screens() || [])
      .filter((panel) => fold(panel.title).includes(needle))
      .slice(0, SCREEN_LIMIT)
      .map((panel) => ({ id: `screen:${panel.id}`, title: panel.title, detail: panel.group,
        badge: 'Ekran', open: () => o.openScreen(panel.id) }));
    // Ekranlar hemen görünür; öğrenciler gelince liste tamamlanır.
    paint([['Ekranlar', screens]], 'Aranıyor…');
    let providers = [];
    try {
      providers = await o.providers();
    } catch {
      providers = [];
    }
    const answers = await Promise.all(providers.map(async (provider) => {
      try {
        const items = await provider.search(query, { limit: RESULT_LIMIT });
        return [provider.label, Array.isArray(items) ? items.slice(0, RESULT_LIMIT) : []];
      } catch (failure) {
        return [provider.label, [], failure];
      }
    }));
    if (mine !== seq) return;           // daha yeni bir arama var
    const failed = answers.filter((answer) => answer[2]).map((answer) => answer[0]);
    const groups = [...answers.map(([label, items]) => [label, items.map((item) => ({ ...item,
      id: `${label}:${item.id}` }))]), ['Ekranlar', screens]];
    const total = groups.reduce((sum, [, items]) => sum + items.length, 0);
    paint(groups, failed.length && total === 0
      ? `${failed.join(', ')} şu an aranamadı.` : '');
  }

  function choose(index) {
    const item = options[index];
    if (!item) return;
    input.value = '';
    list.replaceChildren();
    options = [];
    status.textContent = '';
    setExpanded(false);
    input.blur();
    try {
      item.open?.();
    } catch (failure) {
      console.error('[arama] sonuç açılamadı:', failure);
    }
  }

  input.addEventListener('input', () => {
    clearTimeout(timer);
    timer = setTimeout(() => run(input.value), DEBOUNCE_MS);
  });

  input.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      if (popup.hidden && input.value.trim()) {
        run(input.value);
        return;
      }
      if (!options.length) return;
      const delta = event.key === 'ArrowDown' ? 1 : -1;
      active = (active + delta + options.length) % options.length;
      paintActive();
    } else if (event.key === 'Enter') {
      event.preventDefault();
      clearTimeout(timer);
      if (options.length && active >= 0) choose(active);
      else if (input.value.trim()) run(input.value);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (!popup.hidden) {
        setExpanded(false);
      } else {
        input.value = '';
        input.blur();
      }
    } else if (event.key === 'Home' && options.length && !popup.hidden && event.ctrlKey) {
      active = 0;
      paintActive();
    }
  });

  input.addEventListener('blur', () => {
    // Tıklama `mousedown`ı önlediği için odak sonuçtayken kaybolmaz; başka
    // yere gidilince liste kapanır.
    setTimeout(() => {
      if (doc.activeElement !== input) setExpanded(false);
    }, 0);
  });

  input.addEventListener('focus', () => {
    if (input.value.trim() && options.length) setExpanded(true);
  });

  setExpanded(false);
  input.disabled = true;

  return {
    /** Kutuya odaklanır; `text` verilirse onu arar (bağlam menüsü: "… için ara"). */
    focus(text) {
      if (!enabled) return;
      input.focus();
      if (typeof text === 'string' && text.trim()) {
        input.value = text.trim();
        run(input.value);
      } else {
        input.select?.();
      }
    },
    enable() {
      enabled = true;
      input.disabled = false;
    },
    reset() {
      enabled = false;
      clearTimeout(timer);
      seq += 1;
      input.value = '';
      input.disabled = true;
      list.replaceChildren();
      options = [];
      status.textContent = '';
      setExpanded(false);
    },
    run,
    options: () => options.map((item) => ({ id: item.id, title: item.title })),
  };
}
