// Çoklu seçici — gruplanmış, aranabilir liste.
//
// Tek kopya (ADR 0011). Kantin sürümü öğrenciye özeldi (sınıf, veli, bakiye);
// bu sürüm herhangi bir kayıt kümesiyle çalışır: ürün, müşteri, sipariş,
// kategori. Alan adları `{id, name, group, meta}` sözleşmesine indirgenmiştir;
// çağıran taraf kendi verisini bu şekle çevirir.

import { button, foldText, h } from './kit.js';

/**
 * @param {object} spec
 * @param {Array<{id, name, group?, meta?}>} [spec.items]
 * @param {string} [spec.groupLabel='Grup']  — grup süzgecinin etiketi
 * @param {string} [spec.placeholder]
 * @param {boolean} [spec.single=false] — tek seçim kipi (radyo gibi)
 * @param {(ids:string[])=>void} [spec.onChange]
 * @param {number} [spec.limit=400] — çizilecek en çok satır; üstü uyarı ile kırpılır
 */
export function createPicker({ items = [], groupLabel = 'Grup', placeholder = 'Ara',
  single = false, onChange, limit = 400 } = {}) {
  const node = h('div', 'pk-box');
  const selected = new Set();
  let all = items;
  let query = '';
  let group = '';

  const fire = () => onChange?.([...selected]);

  // --------------------------------------------------------------- şerit
  const bar = h('div', 'pk-bar');
  const search = h('input', 'kit-input pk-search');
  search.type = 'search';
  search.placeholder = placeholder;
  search.setAttribute('aria-label', placeholder);
  search.addEventListener('input', () => { query = search.value; paint(); });

  const groupSelect = h('select', 'kit-select pk-select');
  groupSelect.setAttribute('aria-label', `${groupLabel} süzgeci`);
  groupSelect.addEventListener('change', () => { group = groupSelect.value; paint(); });

  bar.append(search, groupSelect);

  // ------------------------------------------------------------ eylemler
  const actions = h('div', 'pk-actions');
  if (!single) {
    actions.append(
      button('Görünenleri seç', {
        variant: 'ghost',
        onClick: () => { visible().forEach((item) => selected.add(String(item.id))); paint(); fire(); },
      }),
      button('Seçimi temizle', {
        variant: 'ghost',
        onClick: () => { selected.clear(); paint(); fire(); },
      }),
    );
  }

  const count = h('div', 'pk-count');
  const list = h('div', 'pk-list');
  node.append(bar, ...(single ? [] : [actions]), count, list);

  // --------------------------------------------------------------- çizim
  function visible() {
    const needle = foldText(query);
    return all.filter((item) => {
      if (group && String(item.group ?? '') !== group) return false;
      if (!needle) return true;
      return foldText(`${item.name} ${item.meta ?? ''}`).includes(needle);
    });
  }

  function paintGroups() {
    const groups = [...new Set(all.map((item) => String(item.group ?? '')).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, 'tr'));
    groupSelect.replaceChildren();
    const any = h('option', undefined, `Tüm ${groupLabel.toLocaleLowerCase('tr')}lar`);
    any.value = '';
    groupSelect.append(any);
    for (const name of groups) {
      const option = h('option', undefined, name);
      option.value = name;
      groupSelect.append(option);
    }
    groupSelect.value = group;
    groupSelect.hidden = groups.length === 0;
  }

  function paint() {
    const rows = visible();
    count.textContent = single
      ? `${rows.length} kayıt`
      : `${rows.length} kayıt görünüyor · ${selected.size} seçili`;

    list.replaceChildren();
    if (rows.length === 0) {
      list.append(h('div', 'pk-empty', all.length === 0
        ? 'Liste boş.'
        : 'Aramaya uyan kayıt yok.'));
      return;
    }

    const shown = rows.slice(0, limit);
    let lastGroup = null;

    for (const item of shown) {
      const name = String(item.group ?? '');
      if (name && name !== lastGroup) {
        const header = h('div', 'pk-group');
        header.append(
          h('span', 'pk-group-name', name),
          h('span', 'pk-group-count',
            String(rows.filter((row) => String(row.group ?? '') === name).length)),
        );
        list.append(header);
        lastGroup = name;
      }

      const id = String(item.id);
      const row = h('button', `pk-row${selected.has(id) ? ' on' : ''}`);
      row.type = 'button';
      row.setAttribute('aria-pressed', selected.has(id) ? 'true' : 'false');
      row.append(h('span', 'pk-name', item.name));
      if (item.meta) row.append(h('span', 'pk-meta', String(item.meta)));
      row.addEventListener('click', () => {
        if (single) {
          selected.clear();
          selected.add(id);
        } else if (selected.has(id)) {
          selected.delete(id);
        } else {
          selected.add(id);
        }
        paint();
        fire();
      });
      list.append(row);
    }

    if (rows.length > shown.length) {
      list.append(h('div', 'pk-empty',
        `${rows.length - shown.length} kayıt daha var — aramayı daraltın.`));
    }
  }

  paintGroups();
  paint();

  return {
    node,
    setItems(next) {
      all = next || [];
      const alive = new Set(all.map((item) => String(item.id)));
      for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);
      paintGroups();
      paint();
    },
    selection: () => [...selected],
    selectedItems() {
      return all.filter((item) => selected.has(String(item.id)));
    },
    select(ids) {
      selected.clear();
      for (const id of ids || []) selected.add(String(id));
      paint();
    },
    add(ids) {
      for (const id of ids || []) selected.add(String(id));
      paint();
    },
    clear() { selected.clear(); paint(); },
    size: () => selected.size,
    focus() { search.focus(); },
  };
}
