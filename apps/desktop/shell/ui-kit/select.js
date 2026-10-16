// Açılır liste — klavyeyle aranabilen, platform gibi görünen seçim kutusu
// (1.6.0, ADR 0032).
//
// NEDEN. `<select>` açıldığında WebView2'de tarayıcının çizdiği liste,
// WebKitGTK'da kapanmayan bir pencere açılıyordu; hiçbirinde liste içinde
// arama yoktu (40 sınıflı bir kurumda "12-B"yi bulmak kaydırma demekti).
//
// İKİ YOL, TEK GÖRÜNÜM:
//  1. `enhanceSelects()` — kabuk açılışta BİR KEZ kurar. Sayfadaki HER tekli
//     `<select>` tıklanınca/Enter/Boşluk/F4/Alt+↓ ile bu listeyi açar.
//     `<select>` DOM'da kalır: panelin `.value` okuması, `change` dinleyicisi,
//     form doğrulaması değişmez — paneller tek satır değiştirmeden kazanır.
//     Kapalı kutudaki ok tuşları ve harfle atlama yerel davranıştır.
//     İstisna: `multiple`, `size > 1`, `disabled` ya da `data-native` taşıyan
//     kutu dokunulmadan kalır.
//  2. `kitSelect({...})` — yeni kod için: içinde gerçek bir `<select>` olan
//     hazır bileşen (etiket + kutu), aynı listeyi kullanır.
//
// ERİŞİLEBİLİRLİK: WAI-ARIA listbox. Liste açılınca odak arama kutusuna (ya da
// listeye) geçer; ↑/↓/Home/End/PageUp/PageDown gezer, Enter seçer, Esc ve
// Tab kapatır, odak kutuya döner. Etkin satır `aria-activedescendant` ile.

const SEARCH_FROM = 8;       // bu sayıdan fazla seçenekte arama kutusu çıkar
const MAX_HEIGHT = 340;

function fold(text) {
  return String(text ?? '')
    .toLocaleLowerCase('tr')
    .replaceAll('ı', 'i').replaceAll('ş', 's').replaceAll('ğ', 'g')
    .replaceAll('ü', 'u').replaceAll('ö', 'o').replaceAll('ç', 'c')
    .replaceAll('i̇', 'i');
}

let open = null;   // { close(), anchor }
let seq = 0;

/** Açık listeyi kapatır (kabuk ekran değişince çağırır). */
export function closeListbox() {
  open?.close(false);
}

/** `<select>` → liste satırları. */
function itemsOf(select) {
  const items = [];
  for (const child of select.children) {
    if (child.tagName === 'OPTGROUP') {
      for (const option of child.children) {
        if (option.tagName !== 'OPTION' || option.hidden) continue;
        items.push({ value: option.value, label: option.textContent, group: child.label || '',
          disabled: option.disabled || child.disabled, index: option.index });
      }
    } else if (child.tagName === 'OPTION' && !child.hidden) {
      items.push({ value: child.value, label: child.textContent, group: '',
        disabled: child.disabled, index: child.index });
    }
  }
  return items;
}

/**
 * Liste penceresini `anchor`ın altına (yer yoksa üstüne) açar.
 *
 * @param {HTMLElement} anchor
 * @param {{items: {value, label, group?, disabled?}[], value: string,
 *          onPick: (item) => void, label?: string}} o
 */
export function openListbox(anchor, { items, value, onPick, label = '' }) {
  closeListbox();
  const doc = anchor.ownerDocument;
  const win = doc.defaultView || globalThis;
  const id = `kit-lb-${(seq += 1)}`;

  const pop = doc.createElement('div');
  pop.className = 'kit-lb';
  const searchable = items.length > SEARCH_FROM;
  let search = null;
  if (searchable) {
    search = doc.createElement('input');
    search.type = 'text';
    search.className = 'kit-lb-search';
    search.placeholder = 'Ara…';
    search.autocomplete = 'off';
    search.spellcheck = false;
    search.setAttribute('role', 'combobox');
    search.setAttribute('aria-expanded', 'true');
    search.setAttribute('aria-controls', id);
    search.setAttribute('aria-autocomplete', 'list');
    search.setAttribute('aria-label', label ? `${label} içinde ara` : 'Seçeneklerde ara');
    pop.append(search);
  }
  const list = doc.createElement('ul');
  list.className = 'kit-lb-list';
  list.id = id;
  list.setAttribute('role', 'listbox');
  list.tabIndex = -1;
  if (label) list.setAttribute('aria-label', label);
  const empty = doc.createElement('p');
  empty.className = 'kit-lb-empty';
  empty.textContent = 'Eşleşen seçenek yok.';
  empty.hidden = true;
  pop.append(list, empty);

  let visible = [];
  let active = -1;
  const focusTarget = search || list;

  function render(query = '') {
    const needle = fold(query.trim());
    visible = needle ? items.filter((item) => fold(item.label).includes(needle)) : items.slice();
    list.replaceChildren();
    let group = null;
    visible.forEach((item, index) => {
      if (item.group && item.group !== group) {
        group = item.group;
        const head = doc.createElement('li');
        head.className = 'kit-lb-group';
        head.setAttribute('role', 'presentation');
        head.textContent = group;
        list.append(head);
      }
      const row = doc.createElement('li');
      row.id = `${id}-o${index}`;
      row.className = 'kit-lb-opt';
      row.setAttribute('role', 'option');
      row.dataset.index = String(index);
      row.textContent = item.label || ' ';
      const selected = String(item.value) === String(value);
      row.setAttribute('aria-selected', selected ? 'true' : 'false');
      if (selected) row.classList.add('selected');
      if (item.disabled) row.setAttribute('aria-disabled', 'true');
      list.append(row);
    });
    empty.hidden = visible.length > 0;
    const current = visible.findIndex((item) => String(item.value) === String(value) && !item.disabled);
    setActive(current >= 0 ? current : firstEnabled(0, 1));
  }

  function firstEnabled(from, step) {
    for (let i = from; i >= 0 && i < visible.length; i += step) {
      if (!visible[i].disabled) return i;
    }
    return -1;
  }

  function setActive(index) {
    active = index;
    for (const row of list.querySelectorAll('.kit-lb-opt')) {
      const on = Number(row.dataset.index) === index;
      row.classList.toggle('active', on);
      if (on) row.scrollIntoView?.({ block: 'nearest' });
    }
    if (index >= 0) focusTarget.setAttribute('aria-activedescendant', `${id}-o${index}`);
    else focusTarget.removeAttribute('aria-activedescendant');
  }

  function move(step, edge = false) {
    if (!visible.length) return;
    if (edge) {
      setActive(step > 0 ? firstEnabled(visible.length - 1, -1) : firstEnabled(0, 1));
      return;
    }
    let next = active;
    for (let i = 0; i < visible.length; i += 1) {
      next = Math.min(visible.length - 1, Math.max(0, next + step));
      if (!visible[next]?.disabled) break;
      if (next === 0 || next === visible.length - 1) break;
    }
    if (!visible[next]?.disabled) setActive(next);
  }

  function place() {
    const box = anchor.getBoundingClientRect?.() || { left: 0, top: 0, bottom: 0, width: 200 };
    const viewH = win.innerHeight || 800;
    const viewW = win.innerWidth || 1200;
    const below = viewH - box.bottom - 8;
    const above = box.top - 8;
    const height = Math.min(MAX_HEIGHT, Math.max(below, above));
    pop.style.maxHeight = `${Math.max(120, height)}px`;
    pop.style.minWidth = `${Math.max(180, box.width)}px`;
    const left = Math.min(box.left, Math.max(8, viewW - Math.max(180, box.width) - 8));
    pop.style.left = `${Math.max(8, left)}px`;
    if (below >= Math.min(MAX_HEIGHT, 200) || below >= above) {
      pop.style.top = `${box.bottom + 4}px`;
      pop.style.bottom = '';
    } else {
      pop.style.top = '';
      pop.style.bottom = `${viewH - box.top + 4}px`;
    }
  }

  function pick(index) {
    const item = visible[index];
    if (!item || item.disabled) return;
    close(true);
    onPick(item);
  }

  function onKey(event) {
    const key = event.key;
    if (key === 'ArrowDown') { event.preventDefault(); move(1); }
    else if (key === 'ArrowUp') { event.preventDefault(); move(-1); }
    else if (key === 'PageDown') { event.preventDefault(); move(8); }
    else if (key === 'PageUp') { event.preventDefault(); move(-8); }
    else if (key === 'Home' && !search) { event.preventDefault(); move(-1, true); }
    else if (key === 'End' && !search) { event.preventDefault(); move(1, true); }
    else if (key === 'Enter' || (key === ' ' && !search)) { event.preventDefault(); pick(active); }
    else if (key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(true); }
    else if (key === 'Tab') { close(true); }
    else if (!search && key.length === 1 && !event.ctrlKey && !event.metaKey) {
      // Aramasız kısa listede harfle atla (yerel liste gibi).
      const needle = fold(key);
      const from = active + 1;
      const order = [...visible.keys()].map((i) => (i + from) % visible.length);
      const hit = order.find((i) => !visible[i].disabled && fold(visible[i].label).startsWith(needle));
      if (hit !== undefined) setActive(hit);
    }
  }

  function onOutside(event) {
    if (pop.contains(event.target) || event.target === anchor || anchor.contains?.(event.target)) return;
    close(false);
  }

  function onScroll(event) {
    if (pop.contains(event.target)) return;
    close(false);
  }

  function close(returnFocus) {
    if (!pop.isConnected && open?.pop !== pop) return;
    pop.remove();
    doc.removeEventListener('pointerdown', onOutside, true);
    win.removeEventListener('resize', onResize);
    doc.removeEventListener('scroll', onScroll, true);
    anchor.setAttribute('aria-expanded', 'false');
    if (open?.pop === pop) open = null;
    if (returnFocus) anchor.focus?.();
  }

  const onResize = () => close(false);

  list.addEventListener('pointerdown', (event) => event.preventDefault());
  list.addEventListener('click', (event) => {
    const row = event.target.closest?.('.kit-lb-opt');
    if (row) pick(Number(row.dataset.index));
  });
  list.addEventListener('pointermove', (event) => {
    const row = event.target.closest?.('.kit-lb-opt');
    if (row && !visible[Number(row.dataset.index)]?.disabled) setActive(Number(row.dataset.index));
  });
  pop.addEventListener('keydown', onKey);
  search?.addEventListener('input', () => render(search.value));

  render('');
  doc.body.append(pop);
  place();
  anchor.setAttribute('aria-expanded', 'true');
  doc.addEventListener('pointerdown', onOutside, true);
  win.addEventListener('resize', onResize);
  doc.addEventListener('scroll', onScroll, true);
  focusTarget.focus();
  open = { close, pop, anchor };
  return { close: () => close(false), node: pop };
}

// ------------------------------------------------------- yerel <select> ları

function eligible(select) {
  return select && select.tagName === 'SELECT' && !select.multiple && !(select.size > 1)
    && !select.disabled && !select.hasAttribute('data-native') && select.isConnected;
}

function openForSelect(select) {
  const items = itemsOf(select);
  const label = select.getAttribute('aria-label')
    || (select.id && select.ownerDocument.querySelector(`label[for="${select.id}"]`)?.textContent)
    || select.closest('label')?.textContent || '';
  openListbox(select, {
    items,
    value: select.value,
    label: String(label).trim(),
    onPick: (item) => {
      if (String(select.value) === String(item.value) && select.selectedIndex === item.index) return;
      select.selectedIndex = item.index;
      select.dispatchEvent(new Event('input', { bubbles: true }));
      select.dispatchEvent(new Event('change', { bubbles: true }));
    },
  });
}

let installed = null;

/**
 * Sayfadaki tekli `<select>`ları bu listeye bağlar (olay yetkisiyle — DOM'a
 * sonradan eklenen kutular da kapsanır). İkinci çağrı bir şey yapmaz.
 * @returns {() => void} kaldırıcı
 */
export function enhanceSelects(doc = globalThis.document) {
  if (!doc) return () => {};
  if (installed?.doc === doc) return installed.remove;
  const isMac = /mac/i.test(doc.documentElement?.dataset?.platform || globalThis.navigator?.platform || '');

  const onPointer = (event) => {
    if (event.button !== 0) return;
    const select = event.target?.closest?.('select');
    if (!eligible(select)) return;
    event.preventDefault();
    if (open?.anchor === select) { closeListbox(); return; }
    select.focus();
    openForSelect(select);
  };
  const onKey = (event) => {
    const select = event.target;
    if (!eligible(select) || open?.anchor === select) return;
    const key = event.key;
    const opens = key === 'Enter' || key === ' ' || key === 'F4'
      || (event.altKey && (key === 'ArrowDown' || key === 'ArrowUp'))
      // macOS'ta oklar yerel listeyi AÇAR; aynı tuş bizim listeyi açsın.
      || (isMac && (key === 'ArrowDown' || key === 'ArrowUp'));
    if (!opens) return;
    event.preventDefault();
    openForSelect(select);
  };
  doc.addEventListener('mousedown', onPointer, true);
  doc.addEventListener('keydown', onKey, true);
  const remove = () => {
    doc.removeEventListener('mousedown', onPointer, true);
    doc.removeEventListener('keydown', onKey, true);
    installed = null;
  };
  installed = { doc, remove };
  return remove;
}

// --------------------------------------------------------------- bileşen

/**
 * Hazır seçim kutusu. İçinde GERÇEK bir `<select>` vardır; liste
 * `enhanceSelects` ile açılır (kabuk kurmadıysa burada kurulur).
 *
 * @param {{options: ({value, label, group?, disabled?}|string)[], value?: string,
 *          label?: string, placeholder?: string, onChange?: (value) => void,
 *          name?: string}} o
 * @returns {{node, select, value, setOptions(options, value?), destroy()}}
 */
export function kitSelect({ options = [], value = '', label = '', placeholder = '', onChange,
  name = '' } = {}) {
  const doc = globalThis.document;
  enhanceSelects(doc);
  const wrap = doc.createElement('label');
  wrap.className = 'kit-field kit-select-field';
  const select = doc.createElement('select');
  select.className = 'kit-select';
  if (name) select.name = name;
  if (label) {
    const text = doc.createElement('span');
    text.className = 'kit-field-label';
    text.textContent = label;
    wrap.append(text);
  }
  wrap.append(select);

  function setOptions(list, next = select.value) {
    select.replaceChildren();
    if (placeholder) {
      const blank = doc.createElement('option');
      blank.value = '';
      blank.textContent = placeholder;
      select.append(blank);
    }
    const groups = new Map();
    for (const raw of list) {
      const item = typeof raw === 'object' ? raw : { value: raw, label: raw };
      const option = doc.createElement('option');
      option.value = String(item.value);
      option.textContent = String(item.label ?? item.value);
      option.disabled = Boolean(item.disabled);
      if (item.group) {
        let group = groups.get(item.group);
        if (!group) {
          group = doc.createElement('optgroup');
          group.label = item.group;
          groups.set(item.group, group);
          select.append(group);
        }
        group.append(option);
      } else {
        select.append(option);
      }
    }
    select.value = String(next ?? '');
  }

  setOptions(options, value);
  const onChangeEvent = () => onChange?.(select.value);
  select.addEventListener('change', onChangeEvent);

  return {
    node: wrap,
    select,
    get value() { return select.value; },
    set value(next) { select.value = String(next ?? ''); },
    setOptions,
    destroy() {
      select.removeEventListener('change', onChangeEvent);
      if (open?.anchor === select) closeListbox();
    },
  };
}
