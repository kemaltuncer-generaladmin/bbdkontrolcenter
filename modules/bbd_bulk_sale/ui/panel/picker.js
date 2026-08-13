// Öğrenci çoklu seçici — sınıfa göre gruplu.
//
// Sınıf bilgisi kantinde YOKTUR; Öğrenci Yönetimi modülünün kendi verisidir ve
// buraya `bbd_students.list` YETENEĞİ üzerinden gelir (K3 — modül modülü
// import etmez, tablosunu okumaz). Yetenek yoksa liste sınıfsız çalışır.

import { foldText, h, money } from './kit.js';

const UNKNOWN_CLASS = 'Sınıfsız';

/**
 * @param {object} options
 * @param {() => void} options.onChange  seçim değiştiğinde çağrılır
 */
export function createPicker({ onChange }) {
  let students = [];              // {kantinId, displayName, balance, spendingLimit, isBlocked}
  let classOf = new Map();        // kantinId → sınıf adı
  let selected = new Set();
  let query = '';
  let classFilter = '';
  let flagFilter = '';

  const nodes = {};
  const root = h('div', 'pk');

  // ---------------------------------------------------------------- şerit

  const bar = h('div', 'pk-bar');

  nodes.search = h('input', 'pk-search');
  nodes.search.type = 'search';
  nodes.search.placeholder = 'Öğrenci ara — ad veya sınıf';
  nodes.search.addEventListener('input', () => {
    query = nodes.search.value;
    renderList();
  });

  nodes.classFilter = h('select', 'pk-select');
  nodes.classFilter.addEventListener('change', () => {
    classFilter = nodes.classFilter.value;
    renderList();
  });

  nodes.flagFilter = h('select', 'pk-select');
  for (const [value, label] of [
    ['', 'Tümü'],
    ['debt', 'Borçlu'],
    ['blocked', 'Engelli'],
    ['ok', 'Engelsiz'],
  ]) {
    const option = h('option', undefined, label);
    option.value = value;
    nodes.flagFilter.append(option);
  }
  nodes.flagFilter.addEventListener('change', () => {
    flagFilter = nodes.flagFilter.value;
    renderList();
  });

  bar.append(nodes.search, nodes.classFilter, nodes.flagFilter);

  const actions = h('div', 'pk-actions');
  const quick = (label, title, handler) => {
    const node = h('button', 'pk-chip', label);
    node.type = 'button';
    node.title = title;
    node.addEventListener('click', handler);
    return node;
  };
  actions.append(
    quick('Görünenleri seç', 'Filtreye uyan tüm öğrencileri seçer', () => {
      for (const student of visible()) selected.add(student.kantinId);
      changed();
    }),
    quick('Seçimi kaldır', 'Tüm seçimi temizler', () => { selected.clear(); changed(); }),
    quick('Tersine çevir', 'Görünenlerde seçili olanı bırakır, olmayanı seçer', () => {
      for (const student of visible()) {
        if (selected.has(student.kantinId)) selected.delete(student.kantinId);
        else selected.add(student.kantinId);
      }
      changed();
    }),
  );
  nodes.actions = actions;

  nodes.list = h('div', 'pk-list');
  nodes.count = h('div', 'pk-count');

  root.append(bar, actions, nodes.count, nodes.list);

  // ----------------------------------------------------------------- veri

  function visible() {
    const needle = foldText(query);
    return students.filter((student) => {
      const className = classOf.get(student.kantinId) || '';
      if (classFilter && className !== classFilter) return false;
      if (flagFilter === 'blocked' && !student.isBlocked) return false;
      if (flagFilter === 'ok' && student.isBlocked) return false;
      if (flagFilter === 'debt' && Number(student.balance || 0) <= 0) return false;
      if (!needle) return true;
      return foldText(`${student.displayName} ${className}`).includes(needle);
    });
  }

  function grouped(list) {
    const groups = new Map();
    for (const student of list) {
      const key = classOf.get(student.kantinId) || UNKNOWN_CLASS;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(student);
    }
    // Sınıfsızlar en sona; gerisi doğal sırada (9-A, 9-B, 10-A …).
    return [...groups.entries()].sort((a, b) => {
      if (a[0] === UNKNOWN_CLASS) return 1;
      if (b[0] === UNKNOWN_CLASS) return -1;
      return a[0].localeCompare(b[0], 'tr', { numeric: true });
    });
  }

  function changed() {
    renderList();
    onChange?.();
  }

  // --------------------------------------------------------------- çizim

  function renderClassOptions() {
    const names = [...new Set(students.map((s) => classOf.get(s.kantinId)).filter(Boolean))]
      .sort((a, b) => a.localeCompare(b, 'tr', { numeric: true }));
    nodes.classFilter.replaceChildren();
    const all = h('option', undefined, 'Tüm sınıflar');
    all.value = '';
    nodes.classFilter.append(all);
    for (const name of names) {
      const option = h('option', undefined, name);
      option.value = name;
      nodes.classFilter.append(option);
    }
    nodes.classFilter.value = classFilter;
    nodes.classFilter.disabled = names.length === 0;
    nodes.classFilter.title = names.length
      ? 'Sınıf bilgisi Öğrenci Yönetimi modülünden gelir.'
      : 'Sınıf bilgisi yok — Öğrenci Yönetimi ekranından sınıf girilebilir.';
  }

  function renderList() {
    const list = visible();
    nodes.list.replaceChildren();

    if (list.length === 0) {
      nodes.list.append(h('div', 'pk-empty', 'Filtreye uyan öğrenci yok.'));
      nodes.count.textContent = `${selected.size} seçili`;
      return;
    }

    for (const [className, members] of grouped(list)) {
      const chosen = members.filter((student) => selected.has(student.kantinId)).length;

      const head = h('div', 'pk-group');
      const box = h('input', 'pk-box');
      box.type = 'checkbox';
      box.checked = chosen === members.length;
      box.indeterminate = chosen > 0 && chosen < members.length;
      box.title = `${className} sınıfının tamamını seç / bırak`;
      box.addEventListener('change', () => {
        for (const student of members) {
          if (box.checked) selected.add(student.kantinId);
          else selected.delete(student.kantinId);
        }
        changed();
      });
      head.append(box, h('span', 'pk-group-name', className),
        h('span', 'pk-group-count', `${chosen}/${members.length}`));
      nodes.list.append(head);

      for (const student of members) {
        const row = h('label', `pk-row${selected.has(student.kantinId) ? ' on' : ''}`);
        const check = h('input', 'pk-box');
        check.type = 'checkbox';
        check.checked = selected.has(student.kantinId);
        check.addEventListener('change', () => {
          if (check.checked) selected.add(student.kantinId);
          else selected.delete(student.kantinId);
          changed();
        });

        const name = h('span', 'pk-name', student.displayName || '—');
        const meta = h('span', 'pk-meta');
        if (student.isBlocked) meta.append(h('span', 'pk-tag blocked', 'engelli'));
        if (Number(student.balance || 0) > 0) {
          meta.append(h('span', 'pk-tag debt', money(student.balance)));
        }
        if (student.spendingLimit !== null && student.spendingLimit !== undefined) {
          meta.append(h('span', 'pk-tag limit', `limit ${money(student.spendingLimit)}`));
        }

        row.append(check, name, meta);
        nodes.list.append(row);
      }
    }

    nodes.count.textContent = `${selected.size} seçili · ${list.length} görünen · ${students.length} toplam`;
  }

  // ----------------------------------------------------------------- API

  return {
    node: root,
    setStudents(list, classes) {
      students = list || [];
      classOf = classes || new Map();
      // Listeden düşen öğrenciyi seçili bırakma.
      const alive = new Set(students.map((student) => student.kantinId));
      for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);
      renderClassOptions();
      renderList();
    },
    setClasses(classes) {
      classOf = classes || new Map();
      renderClassOptions();
      renderList();
    },
    selection: () => [...selected],
    classMap: () => Object.fromEntries([...selected].map((id) => [id, classOf.get(id) || ''])),
    select(ids) {
      selected = new Set(ids || []);
      changed();
    },
    add(ids) {
      for (const id of ids || []) selected.add(id);
      changed();
    },
    clear() { selected.clear(); changed(); },
    size: () => selected.size,
  };
}
