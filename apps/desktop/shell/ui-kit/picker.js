// Çoklu seçici — gruplanmış, aranabilir liste.
//
// Tek kopya (ADR 0011). Kantin sürümü öğrenciye özeldi (sınıf, veli, bakiye);
// bu sürüm herhangi bir kayıt kümesiyle çalışır: ürün, müşteri, sipariş,
// kategori. Alan adları `{id, name, group, meta}` sözleşmesine indirgenmiştir;
// çağıran taraf kendi verisini bu şekle çevirir.
//
// İKİ VERİ KİPİ (13.10.2026 — "toplu işlemlerde öğrenci seçilemiyor"):
//   · YEREL  — `items` / `setItems()`: liste bellekte; arama ve grup süzgeci
//              burada uygulanır (KM Öğrenci Yönetimi → Toplu işlemler).
//   · UZAK   — `source({query, group, cursor})` → `{items, next}`: arama ve
//              grup süzgeci SUNUCUDA, sayfa sayfa (BBD Akademi toplu ödev,
//              atama paketi, toplu kitap … "Kime?" seçicisi). Seçim sayfalar
//              arasında korunur (`selectedItems()` ad bilgisini de verir).
// Her iki kipte AYNI görünüm: arama, grup süzgeci, "Görünenleri seç",
// "Seçimi temizle", "Yalnız seçilenler", seçili sayısı, "Daha fazla göster",
// yükleniyor / hata (Tekrar dene) / boş durumları ve klavye (aşağıda).
//
// KLAVYE. Satırlar düğmedir (`aria-pressed`): Tab listeye TEK durakla girer
// (gezici tabindex), ↑/↓ satır değiştirir, Home/End uçlara gider, Boşluk /
// Enter seçer. Arama kutusunda ↓ ilk satıra iner; satırda yazmaya başlamak
// aramaya döner. Seçili sayısı `role=status` ile duyurulur.

import { button, foldText, h } from './kit.js';

/**
 * @param {object} spec
 * @param {Array<{id, name, group?, meta?}>} [spec.items]
 * @param {string} [spec.groupLabel='Grup']  — grup süzgecinin etiketi
 * @param {string} [spec.placeholder]
 * @param {string} [spec.label]  — listenin erişilebilir adı (ör. 'Öğrenciler')
 * @param {boolean} [spec.single=false] — tek seçim kipi (radyo gibi)
 * @param {(ids:string[])=>void} [spec.onChange]
 * @param {number} [spec.limit=400] — tek seferde çizilecek satır; üstü "Daha fazla göster"
 * @param {Function} [spec.source] — UZAK kip: `({query, group, cursor}) => Promise<{items, next}>`
 * @param {Array<{value, label}>} [spec.groups] — UZAK kipte grup süzgecinin seçenekleri
 */
//: Arama kutusundaki yazmanın çizime dönüşme gecikmesi (ms). Bkz. `search`
//: dinleyicisi: çizim ucuz değil, tuş vuruşu sık.
const SEARCH_DELAY = 90;
//: Uzak kipte aramanın isteğe dönüşme gecikmesi (A14: tuş başına istek yok).
const REMOTE_DELAY = 250;

export function createPicker({ items = [], groupLabel = 'Grup', placeholder = 'Ara', label = '',
  single = false, onChange, limit = 400, source = null, groups: remoteGroups = null } = {}) {
  const remote = typeof source === 'function';
  const node = h('div', `pk-box${remote ? ' pk-remote' : ''}`);
  const selected = new Map();          // id → kayıt (uzak kipte sayfalar arası ad bilgisi)
  let all = items || [];
  let query = '';
  let group = '';
  let onlySelected = false;
  let shown = limit;                   // çizilen satır sınırı (yerel kip)
  let focusId = null;                  // gezici tabindex: tabla gelinen satır
  let state = { loading: false, error: null, retry: null };
  let next = null;                     // uzak kip: sonraki sayfa imleci
  let token = 0;                       // uzak kip: eskiyen yanıtı at

  const fire = () => onChange?.([...selected.keys()]);
  const listId = `pk-list-${Math.random().toString(36).slice(2, 9)}`;

  // --------------------------------------------------------------- şerit
  const bar = h('div', 'pk-bar');
  const search = h('input', 'kit-input pk-search');
  search.type = 'search';
  search.placeholder = placeholder;
  search.setAttribute('aria-label', placeholder);
  search.setAttribute('aria-controls', listId);

  // ARAMA ÇİZİMİ GECİKTİRİLİR. `paint()` listeyi baştan kuruyor: her satır bir
  // düğme ve iki span, ayrıca grup başlıkları. Yüzlerce kayıtlı bir katalogda
  // bu, HER TUŞ VURUŞUNDA binlerce DOM düğümü demekti ve yazarken gözle
  // görülür bir takılma üretiyordu (WebKitGTK'da daha da belirgin).
  //
  // 90 ms insanın "anında" saydığı sınırın altındadır ama arka arkaya gelen
  // tuşları tek çizimde toplar: "mercimek" yazmak 8 çizim yerine 1 çizim eder.
  // Uzak kipte gecikme 250 ms'dir ve çizim yerine İSTEK toplanır.
  //
  // Bekleyen zamanlayıcı `destroy()` ile bırakılır; çağrılmasa da zarar
  // vermez (kopmuş bir düğümü çizer), ama sızıntı bırakmamak temiz olan.
  let searchTimer = null;
  search.addEventListener('input', () => {
    query = search.value;
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      searchTimer = null;
      shown = limit;
      if (remote) load(); else paint();
    }, remote ? REMOTE_DELAY : SEARCH_DELAY);
  });
  search.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      const first = rowNodes()[0];
      if (first) { event.preventDefault(); first.focus(); }
    }
  });

  const groupSelect = h('select', 'kit-select pk-select');
  groupSelect.setAttribute('aria-label', `${groupLabel} süzgeci`);
  groupSelect.addEventListener('change', () => {
    group = groupSelect.value;
    shown = limit;
    if (remote) load(); else paint();
  });

  bar.append(search, groupSelect);

  // ------------------------------------------------------------ eylemler
  const actions = h('div', 'pk-actions');
  const pickVisible = button('Görünenleri seç', {
    variant: 'ghost',
    onClick: () => {
      for (const item of visible()) selected.set(String(item.id), item);
      paint();
      fire();
    },
  });
  const clearAll = button('Seçimi temizle', {
    variant: 'ghost',
    onClick: () => { selected.clear(); onlySelected = false; paint(); fire(); },
  });
  const onlyToggle = button('Yalnız seçilenler', {
    variant: 'ghost',
    onClick: () => { onlySelected = !onlySelected; shown = limit; paint(); },
  });
  onlyToggle.setAttribute('aria-pressed', 'false');
  if (!single) actions.append(pickVisible, clearAll, onlyToggle);

  const count = h('div', 'pk-count');
  count.setAttribute('role', 'status');
  count.setAttribute('aria-live', 'polite');
  const list = h('div', 'pk-list');
  list.id = listId;
  list.setAttribute('role', 'group');
  list.setAttribute('aria-label', label || placeholder);
  node.append(bar, ...(single ? [] : [actions]), count, list);

  // ------------------------------------------------------------- klavye
  function rowNodes() {
    return list.querySelectorAll('.pk-row');
  }

  list.addEventListener('keydown', (event) => {
    const rows = [...rowNodes()];
    const index = rows.indexOf(event.target);
    if (index < 0) return;
    let target = null;
    if (event.key === 'ArrowDown') target = rows[Math.min(index + 1, rows.length - 1)];
    else if (event.key === 'ArrowUp') {
      if (index === 0) { event.preventDefault(); search.focus(); return; }
      target = rows[index - 1];
    } else if (event.key === 'Home') target = rows[0];
    else if (event.key === 'End') target = rows[rows.length - 1];
    else if (event.key && event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey
      && !event.altKey) {
      // Satırda harf yazmak aramaya döner (yazı kaybolmaz).
      search.focus();
      return;
    }
    if (!target) return;
    event.preventDefault();
    for (const row of rows) row.tabIndex = row === target ? 0 : -1;
    focusId = target.dataset.id;
    target.focus();
    target.scrollIntoView?.({ block: 'nearest' });
  });

  // --------------------------------------------------------------- çizim
  function matches(item) {
    if (remote) return true;           // uzak kipte süzgeç sunucuda
    if (group && String(item.group ?? '') !== group) return false;
    const needle = foldText(query.trim());
    if (!needle) return true;
    return foldText(`${item.name} ${item.meta ?? ''} ${item.group ?? ''}`).includes(needle);
  }

  function visible() {
    if (onlySelected) return [...selected.values()];
    return all.filter(matches);
  }

  function paintGroups() {
    const names = remote
      ? (remoteGroups || []).map((item) => ({ value: String(item.value), label: item.label }))
      : [...new Set(all.map((item) => String(item.group ?? '')).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, 'tr')).map((name) => ({ value: name, label: name }));
    groupSelect.replaceChildren();
    const any = h('option', undefined, `Tüm ${groupLabel.toLocaleLowerCase('tr')}lar`);
    any.value = '';
    groupSelect.append(any);
    for (const item of names) {
      const option = h('option', undefined, item.label);
      option.value = item.value;
      groupSelect.append(option);
    }
    if (group && !names.some((item) => item.value === group)) group = '';
    groupSelect.value = group;
    groupSelect.hidden = names.length === 0;
  }

  function stateBox(text, extra) {
    const box = h('div', 'pk-empty');
    box.append(h('span', '', text));
    if (extra) box.append(extra);
    return box;
  }

  function paint() {
    const rows = visible();
    const restore = list.contains(document.activeElement) ? focusId : null;
    const total = remote && !onlySelected ? `${rows.length}${next ? '+' : ''}` : String(rows.length);
    count.textContent = single
      ? `${total} kayıt`
      : `${total} kayıt görünüyor · ${selected.size} seçili`;
    onlyToggle.setAttribute('aria-pressed', onlySelected ? 'true' : 'false');
    onlyToggle.classList.toggle('on', onlySelected);
    onlyToggle.disabled = !onlySelected && selected.size === 0;
    pickVisible.disabled = onlySelected || rows.length === 0;
    clearAll.disabled = selected.size === 0;
    list.setAttribute('aria-busy', state.loading ? 'true' : 'false');

    list.replaceChildren();
    if (state.error && !onlySelected) {
      const retry = state.retry ? button('Tekrar dene', { onClick: () => state.retry() }) : null;
      const box = stateBox(state.error, retry);
      box.classList.add('pk-error');
      box.setAttribute('role', 'alert');
      list.append(box);
      return;
    }
    if (rows.length === 0) {
      if (state.loading) { list.append(stateBox('Yükleniyor…')); return; }
      const filtered = Boolean(query.trim() || group);
      const reset = filtered && !onlySelected ? button('Aramayı temizle', { variant: 'ghost', onClick: () => {
        query = '';
        group = '';
        search.value = '';
        groupSelect.value = '';
        if (remote) load(); else paint();
        search.focus();
      } }) : null;
      list.append(stateBox(onlySelected ? 'Seçili kayıt yok.'
        : filtered ? 'Aramaya uyan kayıt yok.' : 'Liste boş.', reset));
      return;
    }

    const drawn = remote && !onlySelected ? rows : rows.slice(0, shown);
    const counts = new Map();
    for (const row of rows) {
      const name = String(row.group ?? '');
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    let lastGroup = null;
    let tabbable = null;

    for (const item of drawn) {
      const name = String(item.group ?? '');
      if (name && name !== lastGroup) {
        const header = h('div', 'pk-group');
        header.append(h('span', 'pk-group-name', name), h('span', 'pk-group-count', String(counts.get(name))));
        list.append(header);
        lastGroup = name;
      }

      const id = String(item.id);
      const on = selected.has(id);
      const row = h('button', `pk-row${on ? ' on' : ''}`);
      row.type = 'button';
      row.dataset.id = id;
      row.tabIndex = -1;
      row.setAttribute('aria-pressed', on ? 'true' : 'false');
      const check = h('span', `pk-check${single ? ' pk-radio' : ''}`);
      check.setAttribute('aria-hidden', 'true');
      row.append(check, h('span', 'pk-name', item.name));
      if (item.meta) row.append(h('span', 'pk-meta', String(item.meta)));
      row.addEventListener('click', () => {
        focusId = id;
        if (single) {
          selected.clear();
          selected.set(id, item);
        } else if (selected.has(id)) {
          selected.delete(id);
        } else {
          selected.set(id, item);
        }
        paint();
        fire();
      });
      if (id === focusId) tabbable = row;
      list.append(row);
    }
    const rowsNow = [...rowNodes()];
    (tabbable || rowsNow[0]).tabIndex = 0;
    if (restore && tabbable) tabbable.focus();

    const remaining = rows.length - drawn.length;
    if (remaining > 0) {
      list.append(h('div', 'pk-more'));
      list.lastChild.append(button(`Daha fazla göster (${remaining} kayıt daha)`, { variant: 'ghost', onClick: () => {
        shown += limit;
        paint();
      } }));
    } else if (remote && next && !onlySelected) {
      const more = button(state.loading ? 'Yükleniyor…' : 'Daha fazla yükle', { variant: 'ghost',
        onClick: () => load({ append: true }) });
      more.disabled = state.loading;
      list.append(h('div', 'pk-more'));
      list.lastChild.append(more);
    }
  }

  // ------------------------------------------------------------ uzak veri
  async function load({ append = false } = {}) {
    if (!remote) return;
    const mine = ++token;
    state = { loading: true, error: null, retry: null };
    if (!append) { all = []; next = null; }
    paint();
    try {
      const page = await source({ query: query.trim(), group, cursor: append ? next : null });
      if (mine !== token) return;
      const fresh = Array.isArray(page?.items) ? page.items : [];
      all = append ? [...all, ...fresh] : fresh;
      next = page?.next || null;
      state = { loading: false, error: null, retry: null };
    } catch (failure) {
      if (mine !== token) return;
      state = { loading: false, error: failure?.message ? `Liste okunamadı: ${failure.message}`
        : 'Liste okunamadı.', retry: () => load({ append }) };
    }
    // Seçili kayıtların ad bilgisi en tazesiyle değişir.
    for (const item of all) if (selected.has(String(item.id))) selected.set(String(item.id), item);
    paint();
  }

  paintGroups();
  paint();
  if (remote) load();

  return {
    node,
    setItems(nextItems) {
      all = nextItems || [];
      state = { loading: false, error: null, retry: null };
      const alive = new Map(all.map((item) => [String(item.id), item]));
      for (const id of [...selected.keys()]) {
        if (alive.has(id)) selected.set(id, alive.get(id));
        else selected.delete(id);
      }
      paintGroups();
      paint();
    },
    /** Uzak kip: grup süzgecinin seçenekleri (ör. sınıflar sonradan gelirse). */
    setGroups(options) { remoteGroups = options || []; paintGroups(); },
    /** Yükleniyor durumu (yerel kipte veri çağıranda okunurken). */
    setLoading(on = true) { state = { ...state, loading: Boolean(on), error: on ? null : state.error }; paint(); },
    /** Hata durumu: `message` boşsa temizlenir; `retry` verilirse "Tekrar dene". */
    setError(message, retry = null) {
      state = { loading: false, error: message || null, retry: message ? retry : null };
      paint();
    },
    /** Uzak kip: listeyi baştan yükler. */
    reload() { return load(); },
    selection: () => [...selected.keys()],
    /** Yerel kipte listedeki sırayla (eski davranış); uzak kipte seçiliş sırasıyla. */
    selectedItems() {
      if (remote) return [...selected.values()];
      return all.filter((item) => selected.has(String(item.id)));
    },
    /** `ids` dize ya da `{id, name}` kaydı olabilir (uzak kipte ad bilgisiyle). */
    select(ids) {
      selected.clear();
      for (const value of ids || []) add(value);
      paint();
    },
    add(ids) {
      for (const value of ids || []) add(value);
      paint();
    },
    remove(id) { selected.delete(String(id)); paint(); fire(); },
    clear() { selected.clear(); paint(); },
    size: () => selected.size,
    focus() { search.focus(); },
    /** Bekleyen arama çizimini iptal eder. Panel kapanışında çağrılır. */
    destroy() {
      if (searchTimer) { clearTimeout(searchTimer); searchTimer = null; }
      token += 1;
    },
  };

  function add(value) {
    if (value && typeof value === 'object') {
      selected.set(String(value.id), { ...value, name: value.name ?? String(value.id) });
      return;
    }
    const id = String(value);
    selected.set(id, all.find((item) => String(item.id) === id) || { id, name: id });
  }
}
