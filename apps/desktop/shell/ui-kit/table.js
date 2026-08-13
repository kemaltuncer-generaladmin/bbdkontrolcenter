// Tablo ve sayfalama.
//
// Tek kopya (ADR 0011). `<table>` KULLANILMAZ: sütun genişliklerini CSS grid
// ile vermek hem sticky başlıkla hem de değişken sütun setleriyle sorunsuz
// çalışıyor; mevcut panellerin hepsi bu deseni kullanıyor.
//
// Bileşen VERİ ÇEKMEZ. Satırları alır, çizer, seçim ve sıralama durumunu
// tutar. Sunucu tarafı sayfalama gerekiyorsa `onSort`/`pager.onChange`
// çağrıları paneli tetikler, panel yeni satırları `update()` ile verir.

import { h } from './kit.js';

/**
 * @param {object} spec
 * @param {Array<{key,label,width?,align?,sortable?,cell?,className?,title?}>} spec.columns
 *        `width`: CSS grid parçası ('120px', 'minmax(0,2fr)'). Verilmezse 1fr.
 *        `align`: 'num' sağa yaslar ve tabular rakam kullanır.
 *        `cell(row)`: düğüm ya da metin döndürür. Yoksa `row[key]` yazılır.
 * @param {Array<object>} spec.rows
 * @param {boolean} [spec.selectable]  başa onay kutusu sütunu ekler
 * @param {boolean} [spec.dense]       UDİT gibi yoğun listeler için
 * @param {{key:string,dir:'asc'|'desc'}} [spec.sort]
 * @param {(key:string,dir:string)=>void} [spec.onSort]  verilmezse istemci sıralar
 * @param {(row:object)=>void} [spec.onRow]  satır tıklaması
 * @param {(row:object)=>string} [spec.rowKey]  seçim kimliği (varsayılan row.id)
 * @param {Node} [spec.empty]  boş durum düğümü
 */
export function dataTable(spec) {
  const {
    columns, selectable = false, dense = false, onSort, onRow,
    rowKey = (row) => String(row?.id ?? ''), empty,
  } = spec;

  const node = h('div', `kit-table${dense ? ' dense' : ''}`);
  node.setAttribute('role', 'table');

  const selected = new Set();
  let rows = spec.rows || [];
  let sort = spec.sort || null;
  let emptyNode = empty || null;

  const template = [
    ...(selectable ? ['28px'] : []),
    ...columns.map((column) => column.width || 'minmax(0, 1fr)'),
  ].join(' ');

  // ------------------------------------------------------------- başlık

  const headRow = h('div', 'kit-row kit-head');
  headRow.style.gridTemplateColumns = template;
  headRow.setAttribute('role', 'row');

  let headCheck = null;
  if (selectable) {
    headCheck = h('input', 'kit-check');
    headCheck.type = 'checkbox';
    headCheck.title = 'Görünen kayıtların tümünü seç';
    headCheck.addEventListener('change', () => {
      if (headCheck.checked) rows.forEach((row) => selected.add(rowKey(row)));
      else rows.forEach((row) => selected.delete(rowKey(row)));
      paint();
      spec.onSelect?.(selection());
    });
    headRow.append(headCheck);
  }

  const headCells = new Map();
  for (const column of columns) {
    const cell = h('span', `kit-cell${column.align === 'num' ? ' num' : ''}`);
    cell.textContent = column.label;
    if (column.title) cell.title = column.title;
    if (column.sortable) {
      cell.classList.add('sortable');
      cell.tabIndex = 0;
      cell.setAttribute('role', 'columnheader');
      const toggle = () => {
        const dir = sort?.key === column.key && sort.dir === 'desc' ? 'asc' : 'desc';
        sort = { key: column.key, dir };
        if (onSort) onSort(column.key, dir);
        else paint();
      };
      cell.addEventListener('click', toggle);
      cell.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
      });
    }
    headCells.set(column.key, cell);
    headRow.append(cell);
  }

  const bodyNode = h('div');
  node.append(headRow, bodyNode);

  // -------------------------------------------------------------- çizim

  function sorted() {
    // `onSort` verilmişse sıralama sunucudadır; satırlar zaten sıralı gelir.
    if (!sort || onSort) return rows;
    const column = columns.find((item) => item.key === sort.key);
    const pick = column?.sortValue || ((row) => row[sort.key]);
    const factor = sort.dir === 'asc' ? 1 : -1;
    return [...rows].sort((a, b) => {
      const left = pick(a);
      const right = pick(b);
      if (typeof left === 'number' || typeof right === 'number') {
        return ((Number(left) || 0) - (Number(right) || 0)) * factor;
      }
      return String(left ?? '').localeCompare(String(right ?? ''), 'tr') * factor;
    });
  }

  function paintHead() {
    for (const [key, cell] of headCells) {
      cell.querySelector('.kit-sort-arrow')?.remove();
      if (sort?.key === key) {
        cell.append(h('span', 'kit-sort-arrow', sort.dir === 'asc' ? '▲' : '▼'));
      }
    }
    if (headCheck) {
      const total = rows.length;
      const chosen = rows.filter((row) => selected.has(rowKey(row))).length;
      headCheck.checked = total > 0 && chosen === total;
      headCheck.indeterminate = chosen > 0 && chosen < total;
    }
  }

  function paint() {
    paintHead();
    bodyNode.replaceChildren();

    const list = sorted();
    if (list.length === 0) {
      bodyNode.append(emptyNode || h('div', 'kit-empty', 'Kayıt yok.'));
      return;
    }

    for (const row of list) {
      const id = rowKey(row);
      // Seçim kutusu varken satır <button> OLAMAZ: iç içe etkileşimli öğe
      // ekran okuyucuda ve klavyede bozuk davranır. Bu durumda satır div'dir
      // ve klavye desteği elle verilir.
      const interactive = Boolean(onRow);
      const line = h(interactive && !selectable ? 'button' : 'div', 'kit-row');
      line.style.gridTemplateColumns = template;
      line.setAttribute('role', 'row');
      if (line.tagName === 'BUTTON') line.type = 'button';

      if (interactive) {
        line.addEventListener('click', () => onRow(row));
        if (line.tagName !== 'BUTTON') {
          line.tabIndex = 0;
          line.style.cursor = 'pointer';
          line.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') { event.preventDefault(); onRow(row); }
          });
        }
      }
      if (selected.has(id)) line.classList.add('on');

      if (selectable) {
        const check = h('input', 'kit-check');
        check.type = 'checkbox';
        check.checked = selected.has(id);
        check.setAttribute('aria-label', 'Kaydı seç');
        // Satır tıklamasını yeme.
        check.addEventListener('click', (event) => event.stopPropagation());
        check.addEventListener('change', () => {
          if (check.checked) selected.add(id);
          else selected.delete(id);
          line.classList.toggle('on', check.checked);
          paintHead();
          spec.onSelect?.(selection());
        });
        line.append(check);
      }

      for (const column of columns) {
        const classes = ['kit-cell'];
        if (column.align === 'num') classes.push('num');
        if (column.className) classes.push(column.className);
        const cell = h('span', classes.join(' '));
        const value = column.cell ? column.cell(row) : row[column.key];
        if (value instanceof Node) cell.append(value);
        else cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
        line.append(cell);
      }
      bodyNode.append(line);
    }
  }

  function selection() {
    return [...selected];
  }

  paint();

  return {
    node,
    /** Yeni satır kümesi ve/veya sıralama. Ölü seçimler temizlenir. */
    update({ rows: next, sort: nextSort, empty: nextEmpty } = {}) {
      if (next !== undefined) {
        rows = next;
        const alive = new Set(rows.map(rowKey));
        for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);
      }
      if (nextSort !== undefined) sort = nextSort;
      if (nextEmpty !== undefined) emptyNode = nextEmpty;
      paint();
    },
    selection,
    selectedRows() {
      return rows.filter((row) => selected.has(rowKey(row)));
    },
    clearSelection() { selected.clear(); paint(); },
    get sort() { return sort; },
  };
}

/**
 * Sayfalama şeridi.
 *
 * 1.419 ürünü tek seferde çekmek hem 60 istek/dk sınırını hem de kullanıcının
 * sabrını zorluyor; ürün ve denetim ekranlarında sayfalama SUNUCU tarafındadır
 * ve bu şerit yalnız sayfayı seçtirir.
 */
export function pager({ total = 0, page = 1, size = 50, onChange, sizes = [25, 50, 100] } = {}) {
  const node = h('div', 'kit-pager');
  let state = { total, page, size };

  const rebuild = () => {
    node.replaceChildren();
    const pages = Math.max(1, Math.ceil(state.total / state.size));
    const current = Math.min(Math.max(1, state.page), pages);

    const first = state.total === 0 ? 0 : (current - 1) * state.size + 1;
    const last = Math.min(state.total, current * state.size);
    node.append(h('span', undefined,
      state.total === 0 ? 'Kayıt yok' : `${first}–${last} / ${state.total.toLocaleString('tr-TR')}`));

    node.append(h('span', 'kit-spacer'));

    const go = (target) => {
      if (target < 1 || target > pages || target === current) return;
      state.page = target;
      rebuild();
      onChange?.({ page: target, size: state.size });
    };

    const step = (label, target, disabled, title) => {
      const btn = h('button', 'kit-pager-btn', label);
      btn.type = 'button';
      btn.disabled = disabled;
      if (title) btn.title = title;
      btn.addEventListener('click', () => go(target));
      return btn;
    };

    node.append(step('‹', current - 1, current <= 1, 'Önceki sayfa'));

    // Kalabalık olmasın: ilk, son ve mevcudun iki komşusu gösterilir.
    const wanted = new Set([1, pages, current, current - 1, current + 1]);
    const visible = [...wanted].filter((value) => value >= 1 && value <= pages).sort((a, b) => a - b);
    let previous = 0;
    for (const value of visible) {
      if (value - previous > 1) node.append(h('span', undefined, '…'));
      const btn = step(String(value), value, false);
      if (value === current) {
        btn.classList.add('on');
        btn.setAttribute('aria-current', 'page');
      }
      node.append(btn);
      previous = value;
    }

    node.append(step('›', current + 1, current >= pages, 'Sonraki sayfa'));

    if (sizes.length > 1) {
      const select = h('select', 'kit-select');
      select.title = 'Sayfa başına kayıt';
      for (const value of sizes) {
        const option = h('option', undefined, String(value));
        option.value = String(value);
        select.append(option);
      }
      select.value = String(state.size);
      select.addEventListener('change', () => {
        state.size = Number(select.value);
        state.page = 1;
        rebuild();
        onChange?.({ page: 1, size: state.size });
      });
      node.append(select);
    }
  };

  rebuild();

  return {
    node,
    update(next) { state = { ...state, ...next }; rebuild(); },
    get state() { return { ...state }; },
  };
}
