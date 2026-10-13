// Öğrenci Yönetimi — CANLI KANTİN VERİSİ, BBD Akademi içinde.
//
// TAŞINDI (13.10.2026, kullanıcı isteği): "Öğrenci yönetimini tamamıyla BBD
// Akademi kısmına taşıyalım." Öğrenci listesi, toplu işlemler ve gruplar artık
// BBD Akademi → Öğrenciler'de çizilir; öğrencinin tek kartı Akademi'dedir ve
// bu modülün kaydı orada "Kayıt ve veliler" sekmesidir. Bağ ARAYÜZ
// YETENEKLERİYLEDİR (K3 — modül modülü import etmez):
//
//   bbd_students.manager  (root, {payload, capability, openStudent}) → temizlik
//                         liste + toplu işlemler + gruplar; satıra tıklayınca
//                         `openStudent(öğrenci)` (Akademi öğrenci kartı)
//   bbd_students.record   (root, {kantinId, capability, toast, onChanged, …})
//                         → {reload, destroy}; tek öğrencinin kaydı (record.js)
//   bbd_students.list     öğrenci seçiciler için özet liste (değişmedi)
//
// VERİ SAHİPLİĞİ DEĞİŞMEDİ: kayıt bu modüldedir, kantin otoritedir; bütün
// yazmalar bu modülün uçlarından geçer (önce KM, sonra kantin, outbox olayı).
//
// BU EKRAN (menüdeki "Öğrenci Yönetimi"): kullanıcı BBD Akademi'yi
// görebiliyorsa (Akademi paneli `bbd_akademi.students` yeteneğini veriyor)
// oraya YÖNLENDİRİR — eski menü öğesi ve derin bağlantılar kırılmaz. Akademi'yi
// göremeyen roller (ör. muhasebe) yönetimi burada, aynı bileşenlerle yapar.
//
// ÇAKIŞMAMA KURALI: yalnızca DEĞİŞTİRİLEN alanlar gönderilir. Kasa tableti
// aynı anda başka bir alanı düzenliyorsa onun yazdığı korunur.

import { classSortKey, formatBalance, formatPhone, fullName, normalizeClass } from './student.js';
// Yol kabuğun KOPYALANMIŞ konumuna göredir: shell/panels/bbd_students/ →
// shell/ui-kit/. Kaynakta (`modules/bbd_students/ui/panel/`) çözülmez —
// normaldir (bkz. `tools/build-ui-registry.py`).
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';
import { button, confirmSimple, dialogA11y } from '../../ui-kit/kit.js';
import { mountGroups } from './groups.js';
import { askDeactivate, watchEvents } from './lifecycle.js';
import { mountRecord } from './record.js';
import {
  EXAM_CHOICES, GRADE_CHOICES, TRACK_CHOICES, fillFromSuggestion, parseChoice, parseGrade,
} from './curriculum.js';
import { cleanPhone } from './guardian-model.js';

const BASE = '/api/bbd_students';

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

/** Düğme sırası (`h()` üçüncü argümanı METİNDİR; düğüm verilmez). */
function actionsRow(...items) {
  const row = h('div', 'st-bulk-actions');
  row.append(...items.filter(Boolean));
  return row;
}

function loadCss() {
  const href = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${href}"]`)) {
    const style = h('link');
    style.rel = 'stylesheet';
    style.href = href;
    document.head.append(style);
  }
}

/** Kuruşu şeritte okunur kılar: 1234567 → "12.345,67 ₺". */
function money(kurus) {
  if (kurus === null || kurus === undefined) return '—';
  return `${(Number(kurus) / 100).toLocaleString('tr-TR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })} ₺`;
}

function sortStudents(list) {
  return [...list].sort((a, b) => {
    const [an, al] = classSortKey(a.className);
    const [bn, bl] = classSortKey(b.className);
    if (an !== bn) return an - bn;
    if (al !== bl) return String(al).localeCompare(String(bl), 'tr');
    return fullName(a).localeCompare(fullName(b), 'tr');
  });
}

async function loadPermissions(api) {
  try {
    const me = await api('/auth/me');
    return new Set(Array.isArray(me?.permissions) ? me.permissions : []);
  } catch {
    return null;
  }
}

// ================================================================ yönetici

/**
 * Öğrenci yönetimi: liste + toplu işlemler + gruplar.
 *
 * `options.openStudent(öğrenci)` verilirse (BBD Akademi içinde) satıra
 * tıklamak öğrencinin TEK KARTINI açar ve liste tam genişlik çizilir; verilmezse
 * (bu modülün kendi ekranı) kayıt sağda, aynı bileşenle (`record.js`) çizilir.
 */
function createManager(root, options) {
  loadCss();
  const api = options.api;
  const embedded = typeof options.openStudent === 'function';
  let permissions = null;
  let students = [];
  let connected = false;
  let selectedId = options.payload?.kantinId || options.payload?.externalRef || null;
  let record = null;
  let recordFor = null;
  let groupsView = null;
  let bulkPicker = null;
  let stopWatch = null;
  let refreshTimer = null;
  let toastTimer = null;
  let disposed = false;
  // Durum süzgecinin varsayılanı "Etkin" (km-merkezi-senkron §5.2): pasifler ayrı.
  const filters = { text: '', className: '', flag: '', status: 'active' };
  const nodes = {};

  const can = (permission) => permissions === null
    || permissions.has(permission) || permissions.has(`${permission}:*`);

  const view = h('div', `st${embedded ? ' st-embedded' : ''}`);
  nodes.root = view;
  // Kantin rakamları şeridi yalnız kendi ekranında; BBD Akademi'de kantin
  // rakamları "Bugün" ve "Kantin ve Finans"tadır (sade görünüm).
  nodes.kpi = h('div', 'st-kpi');
  if (!embedded) view.append(nodes.kpi);

  // ------------------------------------------------------------ sekmeler
  const modeBar = h('div', 'st-mode-bar');
  const modes = [['list', 'Öğrenciler'], ['bulk', 'Toplu işlemler'], ['groups', 'Gruplar']];
  const modeButtons = {};
  for (const [key, label] of modes) {
    const item = h('button', 'st-mode-btn', label);
    item.type = 'button';
    item.addEventListener('click', () => setMode(key));
    modeButtons[key] = item;
    modeBar.append(item);
  }
  // Akademi kendi sekme şeridini çizer (`hideModes`) — iki şerit üst üste olmasın.
  if (!options.hideModes) view.append(modeBar);

  // -------------------------------------------------------------- şerit
  const bar = h('header', 'st-bar');
  nodes.search = h('input', 'st-search');
  nodes.search.type = 'search';
  nodes.search.placeholder = 'Ad, sınıf, no, veli telefonu ara';
  nodes.search.setAttribute('aria-label', 'Öğrenci listesinde ara: ad, sınıf, no, veli telefonu');
  nodes.search.addEventListener('input', () => { filters.text = nodes.search.value.trim(); renderList(); });
  nodes.statusFilter = h('select', 'st-input st-select');
  nodes.statusFilter.setAttribute('aria-label', 'Durum');
  for (const [value, label] of [['active', 'Etkin'], ['inactive', 'Pasif'], ['', 'Etkin + pasif']]) {
    nodes.statusFilter.append(new Option(label, value));
  }
  nodes.statusFilter.addEventListener('change', () => { filters.status = nodes.statusFilter.value; renderList(); });
  nodes.classFilter = h('select', 'st-input st-select');
  nodes.classFilter.setAttribute('aria-label', 'Sınıf');
  nodes.classFilter.addEventListener('change', () => { filters.className = nodes.classFilter.value; renderList(); });
  nodes.flagFilter = h('select', 'st-input st-select');
  nodes.flagFilter.setAttribute('aria-label', 'Süzgeç');
  for (const [value, label] of [['', 'Tümü'], ['debt', 'Borçlu'], ['blocked', 'Engelli'],
    ['unclassed', 'Sınıfsız'], ['noguardian', 'Velisi girilmemiş'], ['pending', 'Kantine iletilemeyen'],
    ['draft', 'Kasadan eklenen']]) {
    nodes.flagFilter.append(new Option(label, value));
  }
  nodes.flagFilter.addEventListener('change', () => { filters.flag = nodes.flagFilter.value; renderList(); });
  nodes.count = h('span', 'st-count');
  nodes.add = button('+ Yeni öğrenci', { variant: 'primary', onClick: () => createStudent() });
  bar.append(nodes.search, nodes.statusFilter, nodes.classFilter, nodes.flagFilter, nodes.count,
    h('div', 'st-spacer'), button('Yenile', { onClick: () => refresh() }), nodes.add);
  view.append(bar);
  nodes.bar = bar;

  nodes.status = h('p', 'st-status', 'Bağlanıyor…');
  view.append(nodes.status);

  // TOPLU DÜZENLEME LİSTEDE (0.5.3): satırları işaretle → alan seç → değer →
  // Uygula. Ayrı "Toplu işlemler" sekmesine gitmeden çoklu seçim + alan.
  const chosen = new Set();
  nodes.bulkBar = buildListBulkBar();
  view.append(nodes.bulkBar.node);

  const split = h('div', `st-split${embedded ? ' st-split-full' : ''}`);
  nodes.list = h('nav', 'st-list');
  nodes.list.setAttribute('aria-label', 'Öğrenciler');
  split.append(nodes.list);
  nodes.detail = h('section', 'st-detail');
  if (!embedded) split.append(nodes.detail);
  view.append(split);
  nodes.split = split;

  nodes.bulk = buildBulkView();
  view.append(nodes.bulk);
  nodes.groups = h('div', 'st-groups');
  nodes.groups.hidden = true;
  view.append(nodes.groups);

  nodes.toast = h('div', 'st-toast');
  nodes.toast.setAttribute('role', 'status');
  nodes.toast.setAttribute('aria-live', 'polite');
  view.append(nodes.toast);
  root.replaceChildren(view);

  function toast(text) {
    nodes.toast.textContent = text;
    nodes.toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => nodes.toast.classList.remove('show'), 3000);
  }

  let mode = 'list';
  function setMode(next) {
    mode = next;
    nodes.bulkBar?.paint();
    split.hidden = mode !== 'list';
    nodes.bar.hidden = mode !== 'list';
    nodes.status.hidden = mode === 'groups';
    nodes.bulk.hidden = mode !== 'bulk';
    nodes.groups.hidden = mode !== 'groups';
    for (const [key, item] of Object.entries(modeButtons)) item.classList.toggle('st-mode-active', key === mode);
    if (mode === 'bulk') {
      syncBulkPicker();
      nodes.bulk.prepare?.();   // sınıf önerileri ve grup listesi tazelenir
    }
    if (mode === 'groups') {
      if (groupsView === null) {
        groupsView = mountGroups(nodes.groups, { api, can, students: () => students, dialogRoot: view, toast });
      } else {
        groupsView.reload();
      }
    }
  }

  // --------------------------------------------------------------- veri

  function renderSummary(summary) {
    nodes.kpi.replaceChildren();
    if (!summary) return;
    const tiles = [
      { label: 'Toplam öğrenci', value: String(summary.totalStudents ?? '—') },
      { label: 'Kantin açık alacak', value: money(summary.openReceivables), tone: 'debt' },
      { label: 'Borçlu öğrenci', value: String(summary.debtorCount ?? '—') },
      { label: 'Bugünkü kantin satışı', value: money(summary.todaySales) },
      { label: 'Bekleyen ödeme talebi', value: String(summary.pendingRequests ?? '—') },
      { label: 'Engelli öğrenci', value: String(summary.blockedStudents ?? '—'), tone: summary.blockedStudents ? 'warn' : '' },
    ];
    for (const tile of tiles) {
      const box = h('div', 'st-kpi-tile');
      box.append(h('span', 'st-kpi-label', tile.label),
        h('b', `st-kpi-value${tile.tone ? ` ${tile.tone}` : ''}`, tile.value));
      nodes.kpi.append(box);
    }
    nodes.kpi.title = summary.source === 'kasa panosu'
      ? 'Kantin rakamları kasa panosundan okundu — tabletle aynı kaynak.'
      : 'Kasa panosu okunamadı; rakamlar öğrenci bakiyelerinden hesaplandı.';
  }

  async function refresh() {
    nodes.status.textContent = 'Kantinden okunuyor…';
    try {
      const payload = await api(`${BASE}/students`);
      if (disposed) return;
      students = payload.students || [];
      connected = payload.connected;
      renderSummary(payload.summary);
      nodes.status.textContent = connected
        ? `Kantine bağlı · ${students.length} öğrenci`
        : `Kantine ulaşılamadı — ${payload.error || 'bilinmeyen hata'}`;
      nodes.status.classList.toggle('bad', !connected);
    } catch (error) {
      if (disposed) return;
      connected = false;
      students = [];
      nodes.status.textContent = `Öğrenci listesi okunamadı: ${error.message}`;
      nodes.status.classList.add('bad');
    }
    if (!embedded && (!selectedId || !students.some((student) => student.kantinId === selectedId))) {
      selectedId = students.find(matches)?.kantinId ?? null;
    }
    renderList();
    if (!embedded) showRecord();
    syncBulkPicker();
  }

  /** Canlı yenileme: olaylar 300 ms birikip TEK yenilemeye iner. */
  function scheduleRefresh(items) {
    const mine = items.some((item) => item.subject?.id === selectedId);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(async () => {
      await refresh();
      // Düzenleme sürerken kayıt EZİLMEZ; değişiklik yoksa tazelenir.
      if (mine && record && !record.dirty()) record.reload();
    }, 300);
  }

  function matches(student) {
    if (filters.status && (student.status || 'active') !== filters.status) return false;
    if (filters.className && student.className !== filters.className) return false;
    if (filters.flag === 'pending' && student.canteenSync !== 'pending') return false;
    if (filters.flag === 'draft' && student.source !== 'canteen') return false;
    if (filters.flag === 'blocked' && !student.isBlocked) return false;
    if (filters.flag === 'debt' && Number(student.balance) <= 0) return false;
    if (filters.flag === 'unclassed' && student.className) return false;
    if (filters.flag === 'noguardian' && (student.guardians || []).some((row) => cleanPhone(row.phone))) return false;
    if (filters.text) {
      const needle = filters.text.toLocaleLowerCase('tr');
      const phones = (student.guardians || []).map((row) => row.phone).join(' ');
      const hay = [student.displayName, student.className, student.schoolNo,
        student.parentName, student.parentPhone, phones, student.kantinId]
        .join(' ').toLocaleLowerCase('tr');
      if (!hay.includes(needle) && !hay.includes(needle.replace(/\D/g, '') || '\u0000')) return false;
    }
    return true;
  }

  function classes() {
    return [...new Set(students.map((student) => student.className).filter(Boolean))]
      .sort((a, b) => {
        const [an, al] = classSortKey(a);
        const [bn, bl] = classSortKey(b);
        return an - bn || String(al).localeCompare(String(bl), 'tr');
      });
  }

  function renderList() {
    const all = sortStudents(students);
    const shown = all.filter(matches);
    nodes.count.textContent = all.length === shown.length ? `${all.length} öğrenci` : `${shown.length} / ${all.length}`;
    const known = classes();
    const previous = nodes.classFilter.value;
    nodes.classFilter.replaceChildren(new Option('Tüm sınıflar', ''));
    for (const name of known) nodes.classFilter.append(new Option(name, name));
    nodes.classFilter.value = known.includes(previous) ? previous : '';
    nodes.add.hidden = !can('bbd_students.manage');

    nodes.list.replaceChildren();
    if (shown.length === 0) {
      const box = h('div', 'st-empty');
      box.append(h('p', null, !connected ? 'Kantine bağlanılamadı; liste boş.'
        : students.length ? 'Bu süzgece uyan öğrenci yok.' : 'Henüz öğrenci yok.'));
      if (connected && !students.length && can('bbd_students.manage')) {
        box.append(button('Öğrenci ekle', { variant: 'primary', onClick: () => createStudent() }));
      }
      nodes.list.append(box);
      return;
    }

    // Tümünü seç (görünenler) — çoklu seçimin tek tık kısayolu.
    if (can('bbd_students.manage')) {
      const allBox = h('label', 'st-select-all');
      const all = h('input', 'st-check');
      all.type = 'checkbox';
      const ids = shown.map((student) => student.kantinId);
      all.checked = ids.length > 0 && ids.every((id) => chosen.has(id));
      all.indeterminate = !all.checked && ids.some((id) => chosen.has(id));
      all.addEventListener('change', () => {
        for (const id of ids) {
          if (all.checked) chosen.add(id);
          else chosen.delete(id);
        }
        renderList();
      });
      allBox.append(all, h('span', null, `Görünen ${ids.length} öğrenciyi seç`));
      nodes.list.append(allBox);
    }
    nodes.bulkBar.paint();

    let lastClass = null;
    for (const student of shown) {
      if (student.className !== lastClass) {
        lastClass = student.className;
        nodes.list.append(h('p', 'st-list-group', student.className || 'Sınıf girilmemiş'));
      }
      const line = h('div', 'st-row');
      if (can('bbd_students.manage')) {
        const check = h('input', 'st-check st-row-check');
        check.type = 'checkbox';
        check.checked = chosen.has(student.kantinId);
        check.setAttribute('aria-label', `${student.displayName || 'Adsız'} seç`);
        check.addEventListener('change', () => {
          if (check.checked) chosen.add(student.kantinId);
          else chosen.delete(student.kantinId);
          nodes.bulkBar.paint();
        });
        line.append(check);
      }
      const item = h('button', 'st-item');
      item.type = 'button';
      item.classList.toggle('active', !embedded && student.kantinId === selectedId);
      const main = h('span', 'st-item-main');
      main.append(h('b', null, student.displayName || 'Adsız'));
      const primary = (student.guardians || []).find((row) => row.primary);
      main.append(h('span', 'st-item-meta',
        [student.schoolNo && `No ${student.schoolNo}`, primary?.name, formatPhone(student.parentPhone)]
          .filter(Boolean).join(' · ') || 'Veli girilmemiş'));
      item.append(main);
      const balance = h('span', 'st-item-balance', formatBalance(student.balance));
      balance.title = 'Kantin bakiyesi';
      balance.classList.toggle('debt', Number(student.balance) > 0);
      balance.classList.toggle('credit', Number(student.balance) < 0);
      item.append(balance);
      if (student.isBlocked) item.append(h('i', 'st-badge', 'engelli'));
      if (student.status === 'inactive') {
        item.classList.add('st-item-inactive');
        item.append(h('i', 'st-badge st-badge-inactive', 'pasif'));
      } else if (student.scheduledDeactivation) {
        item.append(h('i', 'st-badge st-badge-warn', `${student.scheduledDeactivation} pasif`));
      }
      if (student.canteenSync === 'pending') {
        const badge = h('i', `st-badge ${student.canteenStale ? 'st-badge-bad' : 'st-badge-warn'}`,
          student.canteenStale ? 'kantine iletilemedi (24 sa+)' : 'kantine iletilemedi');
        badge.title = 'Kantine iletilemedi — otomatik yeniden denenecek.';
        item.append(badge);
      }
      if (student.source === 'canteen') {
        const badge = h('i', 'st-badge st-badge-draft', 'kasadan eklendi');
        badge.title = 'Kasadan eklendi — bilgileri tamamla.';
        item.append(badge);
      }
      item.addEventListener('click', () => select(student));
      line.append(item);
      nodes.list.append(line);
    }
  }

  /**
   * Listenin üstündeki toplu düzenleme şeridi: N seçili · Alan · Değer · Uygula.
   * Alanlar mevcut toplu uçlara gider (öğrenci başına olay, kantin/Akademi
   * senkronu aynı): ana sınıf, seviye, alan, hedef sınav, gruba ekle, pasifleştir.
   */
  function buildListBulkBar() {
    const node = h('section', 'st-listbulk');
    node.setAttribute('aria-label', 'Seçilen öğrencilere toplu düzenleme');
    node.hidden = true;
    const count = h('b', 'st-listbulk-count');
    const fieldSelect = h('select', 'st-input st-select');
    fieldSelect.setAttribute('aria-label', 'Değiştirilecek alan');
    for (const [value, label] of [['className', 'Ana sınıf'], ['gradeLevel', 'Seviye'], ['track', 'Alan'],
      ['examTarget', 'Hedef sınav'], ['group', 'Gruba ekle'], ['deactivate', 'Pasifleştir']]) {
      fieldSelect.append(new Option(label, value));
    }
    const valueSlot = h('span', 'st-listbulk-value');
    const status = h('span', 'st-listbulk-status');
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    let control = null;
    let groupsCache = null;

    async function paintValue() {
      const field = fieldSelect.value;
      valueSlot.replaceChildren();
      control = null;
      if (field === 'deactivate') return;
      if (field === 'className') {
        control = h('input', 'st-input');
        control.placeholder = '9-A';
        control.maxLength = 20;
        control.setAttribute('list', 'st-listbulk-classes');
        const datalist = h('datalist');
        datalist.id = 'st-listbulk-classes';
        datalist.append(...classes().map((name) => new Option(name, name)));
        valueSlot.append(control, datalist);
      } else if (field === 'group') {
        control = h('select', 'st-input st-select');
        control.append(new Option('Grup seçin', ''));
        valueSlot.append(control);
        try {
          groupsCache = groupsCache || (await api(`${BASE}/groups?archived=live`)).groups || [];
          control.append(...groupsCache.map((group) => new Option(group.name, group.id)));
        } catch (failure) {
          status.textContent = `Gruplar okunamadı: ${failure.message}`;
        }
      } else {
        control = h('select', 'st-input st-select');
        const choices = { gradeLevel: GRADE_CHOICES, track: TRACK_CHOICES, examTarget: EXAM_CHOICES }[field];
        for (const [value, label] of choices) control.append(new Option(label, value));
        valueSlot.append(control);
      }
      control.setAttribute('aria-label', `Yeni ${fieldSelect.selectedOptions[0]?.textContent || 'değer'}`);
      control.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); apply(); } });
    }
    fieldSelect.addEventListener('change', () => paintValue());

    async function apply() {
      const ids = [...chosen];
      if (!ids.length) return;
      const field = fieldSelect.value;
      go.disabled = true;
      status.textContent = 'Uygulanıyor…';
      try {
        let done = ids.length;
        let failed = 0;
        if (field === 'className') {
          const result = await api(`${BASE}/students/class`, { method: 'POST',
            body: { students: ids, className: normalizeClass(control.value) } });
          done = result.updated?.length ?? ids.length;
          failed = result.failed?.length || 0;
        } else if (field === 'group') {
          if (!control.value) { status.textContent = 'Grup seçin.'; return; }
          const result = await api(`${BASE}/groups/${encodeURIComponent(control.value)}/members`,
            { method: 'POST', body: { studentIds: ids } });
          failed = result?.skipped?.length || 0;
          done = ids.length - failed;
        } else if (field === 'deactivate') {
          const answer = await askDeactivate(view, { students: students.filter((row) => chosen.has(row.kantinId)) });
          if (!answer) { status.textContent = ''; return; }
          const result = await api(`${BASE}/students/deactivate`, { method: 'POST', body: { students: ids, ...answer } });
          done = (result.deactivated?.length || 0) + (result.scheduled?.length || 0);
          failed = result.failed?.length || 0;
        } else {
          const value = field === 'gradeLevel' ? parseGrade(control.value) : parseChoice(control.value);
          const result = await api(`${BASE}/students/curriculum`, { method: 'POST',
            body: { students: ids, [field]: value } });
          done = result.updated?.length ?? ids.length;
          failed = result.failed?.length || 0;
        }
        status.textContent = `${done} öğrenci güncellendi${failed ? `, ${failed} atlandı` : ''}.`;
        chosen.clear();
        await refresh();
      } catch (failure) {
        status.textContent = `Uygulanamadı: ${failure.message}`;
      } finally {
        go.disabled = false;
      }
    }
    const go = button('Uygula', { variant: 'primary', onClick: () => apply() });
    const clear = button('Seçimi temizle', { onClick: () => { chosen.clear(); renderList(); } });
    node.append(count, h('span', 'st-label', 'Alan'), fieldSelect, valueSlot, go, clear, status);
    paintValue();
    return {
      node,
      paint() {
        // Silinen / artık listede olmayan seçimler düşer.
        const known = new Set(students.map((row) => row.kantinId));
        for (const id of [...chosen]) if (!known.has(id)) chosen.delete(id);
        node.hidden = chosen.size === 0 || mode !== 'list';
        count.textContent = `${chosen.size} öğrenci seçili`;
      },
    };
  }

  async function select(student) {
    if (embedded) {
      options.openStudent(student);
      return;
    }
    if (record?.dirty() && !await confirmSimple(view, {
      title: 'Kaydedilmemiş değişiklikler',
      description: 'Değişiklikler kaydedilmedi. Başka öğrenciye geçilsin mi?',
      confirmLabel: 'Değişiklikleri bırak',
      danger: true,
    })) return;
    selectedId = student.kantinId;
    renderList();
    showRecord();
  }

  function showRecord() {
    if (embedded) return;
    if (!selectedId) {
      record?.destroy();
      record = null;
      recordFor = null;
      nodes.detail.replaceChildren(h('p', 'st-empty', connected ? 'Soldan bir öğrenci seçin.' : 'Kantine bağlanılamadı.'));
      return;
    }
    if (recordFor === selectedId && record) return;
    record?.destroy();
    recordFor = selectedId;
    const host = h('div', 'st-detail-body');
    nodes.detail.replaceChildren(host);
    record = mountRecord(host, {
      api, kantinId: selectedId, can, capability: options.capability, dialogRoot: view, toast,
      classes, onChanged: () => refresh(),
      onRemoved: () => { selectedId = null; recordFor = null; refresh(); },
    });
  }

  // --------------------------------------------------------- yeni öğrenci

  /** Sade pencere: ad, soyad, sınıf, birincil veli telefonu (kalanı kartta). */
  function createStudent() {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog st-lc-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', 'Yeni öğrenci'),
      h('p', 'kit-dialog-text', 'Öğrenci önce Kontrol Merkezi\'ne, sonra kantine yazılır ve BBD Akademi\'ye '
        + 'kendiliğinden geçer. Diğer bilgileri öğrenci kartından tamamlayın.'));
    const input = (label, placeholder = '', max = 60) => {
      const wrap = h('label', 'st-field');
      const node = h('input', 'kit-input st-input');
      node.type = 'text';
      node.placeholder = placeholder;
      node.maxLength = max;
      wrap.append(h('span', 'st-label', label), node);
      return { wrap, node };
    };
    const first = input('Ad');
    const last = input('Soyad');
    const klass = input('Sınıf', '9-A', 20);
    const phone = input('Veli telefonu (birincil veli)', '+90 5xx xxx xx xx', 24);
    const grid = h('div', 'st-lc-grid');
    grid.append(first.wrap, last.wrap, klass.wrap, phone.wrap);
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(grid, error);
    let release = () => {};
    const close = () => { document.removeEventListener('keydown', onKey); overlay.remove(); release(); };
    const submit = async () => {
      const body = { firstName: first.node.value.trim(), lastName: last.node.value.trim() };
      if (!body.firstName || !body.lastName) { error.textContent = 'Ad ve soyad zorunlu.'; return; }
      if (klass.node.value.trim()) body.className = normalizeClass(klass.node.value);
      if (phone.node.value.trim()) {
        const number = cleanPhone(phone.node.value);
        if (!number) { error.textContent = 'Veli telefonu cep numarası olmalı (+90 5xx xxx xx xx).'; return; }
        body.parentPhone = number;
      }
      go.disabled = true;
      try {
        const result = await api(`${BASE}/students`, { method: 'POST', body });
        close();
        toast(result?.warning || 'Öğrenci eklendi.');
        selectedId = result.kantinId;
        await refresh();
        const added = students.find((student) => student.kantinId === result.kantinId);
        if (added) select(added);
      } catch (failure) {
        error.textContent = `Eklenemedi: ${failure.message}`;
        go.disabled = false;
      }
    };
    const onKey = (event) => { if (event.key === 'Escape') close(); };
    const go = button('Ekle', { variant: 'primary', onClick: submit });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: close }), go);
    card.append(actions);
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
    view.append(overlay);
    release = dialogA11y(card);
    first.node.focus();
  }

  // ------------------------------------------------------ toplu işlemler

  function syncBulkPicker() {
    if (!bulkPicker) return;
    bulkPicker.setItems(sortStudents(students).map((student) => ({
      id: student.kantinId,
      name: student.displayName || 'Adsız',
      group: student.className || 'Sınıf girilmemiş',
      meta: [student.schoolNo || '—', student.status === 'inactive' ? 'pasif' : '',
        student.accessCode ? 'kod var' : 'kod yok'].filter(Boolean).join(' · '),
    })));
  }

  function selection(status) {
    const ids = bulkPicker?.selection() ?? [];
    if (ids.length === 0) status.textContent = 'Önce soldan en az bir öğrenci seçin.';
    return ids;
  }

  function section(title, hint) {
    const box = h('div', 'st-bulk-plan');
    box.append(h('p', 'st-subhead', title));
    if (hint) box.append(h('p', 'st-hint', hint));
    return box;
  }

  function select_(label, choices) {
    const wrap = h('label', 'st-field');
    wrap.append(h('span', 'st-label', label));
    const input = h('select', 'st-input st-select');
    for (const [value, text] of choices) input.append(new Option(text, value));
    wrap.append(input);
    return { wrap, input };
  }

  /** Sınıfa ata — çoklu seçim → ana sınıf (öğrenci başına olay). */
  function bulkClass() {
    const box = section('Sınıfa ata', 'Seçilenlerin ANA sınıfı değişir. Bir öğrenciyi ek bir topluluğa '
      + '(etüt, kurs) almak için aşağıdaki “Gruba ekle”yi kullanın.');
    const wrap = h('label', 'st-field');
    const input = h('input', 'st-input');
    input.placeholder = '9-A';
    input.maxLength = 20;
    const listId = 'st-bulk-classes';
    input.setAttribute('list', listId);
    const datalist = h('datalist');
    datalist.id = listId;
    wrap.append(h('span', 'st-label', 'Sınıf'), input, datalist);
    const status = h('p', 'st-bulk-status', '');
    const apply = button('Seçilenleri bu sınıfa ata', { variant: 'primary', onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      const name = normalizeClass(input.value);
      if (!await confirmSimple(view, { title: name ? `${ids.length} öğrenci “${name}” sınıfına atansın mı?`
        : `${ids.length} öğrencinin sınıfı temizlensin mi?`,
      description: 'Basılan kantin kartındaki sınıf ve BBD Akademi sınıfı da güncellenir.', confirmLabel: 'Ata' })) return;
      try {
        const result = await api(`${BASE}/students/class`, { method: 'POST', body: { students: ids, className: name } });
        status.textContent = `${result.updated.length} öğrenci ${name ? `“${name}” sınıfına atandı` : 'sınıfsız bırakıldı'}.`
          + (result.failed.length ? ` ${result.failed.length} atlandı.` : '');
        await refresh();
      } catch (failure) {
        status.textContent = `Atanamadı: ${failure.message}`;
      }
    } });
    apply.disabled = !can('bbd_students.manage');
    box.append(wrap, actionsRow(apply), status);
    box.refreshClasses = () => {
      datalist.replaceChildren(...classes().map((name) => new Option(name, name)));
    };
    return box;
  }

  /** Gruba ekle / çıkar / taşı — çoklu seçim, tarihli üyelik (bugünden). */
  function bulkGroups() {
    const box = section('Gruba ekle, çıkar ya da taşı', 'Öğrenci aynı anda birden çok grupta olabilir. '
      + 'Ekleme bugünden başlar; çıkarma bugünü son gün yapar; taşıma eski grubu dünle kapatır.');
    const action = select_('İşlem', [['add', 'Gruba ekle'], ['end', 'Gruptan çıkar'], ['move', 'Gruptan gruba taşı']]);
    const from = select_('Grup', [['', 'Grup seçin']]);
    const to = select_('Hedef grup', [['', 'Grup seçin']]);
    to.wrap.hidden = true;
    action.input.addEventListener('change', () => {
      to.wrap.hidden = action.input.value !== 'move';
      from.wrap.querySelector('.st-label').textContent = action.input.value === 'move' ? 'Kaynak grup' : 'Grup';
    });
    const status = h('p', 'st-bulk-status', '');
    let groups = [];
    async function loadGroups() {
      try {
        const payload = await api(`${BASE}/groups?archived=live`);
        groups = payload.groups || [];
        for (const select of [from.input, to.input]) {
          const value = select.value;
          select.replaceChildren(new Option('Grup seçin', ''), ...groups.map((group) => new Option(group.name, group.id)));
          select.value = value;
        }
        if (!groups.length) status.textContent = 'Henüz grup yok — “Gruplar” sekmesinden açın.';
      } catch (failure) {
        status.textContent = `Gruplar okunamadı: ${failure.message}`;
      }
    }
    const apply = button('Uygula', { variant: 'primary', onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      if (!from.input.value || (action.input.value === 'move' && !to.input.value)) {
        status.textContent = 'Grup seçin.';
        return;
      }
      try {
        let result;
        if (action.input.value === 'add') {
          result = await api(`${BASE}/groups/${encodeURIComponent(from.input.value)}/members`,
            { method: 'POST', body: { studentIds: ids } });
        } else if (action.input.value === 'end') {
          result = await api(`${BASE}/groups/${encodeURIComponent(from.input.value)}/members/end`,
            { method: 'POST', body: { studentIds: ids } });
        } else {
          result = await api(`${BASE}/groups/move`, { method: 'POST',
            body: { studentIds: ids, fromGroupId: from.input.value, toGroupId: to.input.value } });
        }
        const skipped = result?.skipped?.length || 0;
        status.textContent = `${ids.length - skipped} öğrenci için yapıldı.${skipped ? ` ${skipped} öğrenci zaten uygundu, atlandı.` : ''}`;
        groupsView?.reload();
      } catch (failure) {
        status.textContent = `Yapılamadı: ${failure.message}`;
      }
    } });
    apply.disabled = !can('bbd_students.groups');
    box.append(action.wrap, from.wrap, to.wrap, actionsRow(apply), status);
    box.loadGroups = loadGroups;
    return box;
  }

  /** Toplu seviye/alan/hedef sınav. "değiştirme" seçili alan gönderilmez. */
  function bulkCurriculum() {
    const box = section('Seviye / alan ata');
    const keep = ['__keep', 'değiştirme'];
    const grade = select_('Seviye', [keep, ...GRADE_CHOICES]);
    const track = select_('Alan', [keep, ...TRACK_CHOICES]);
    const exam = select_('Hedef sınav', [keep, ...EXAM_CHOICES]);
    const status = h('p', 'st-bulk-status', '');
    const send = (ids, body) => api(`${BASE}/students/curriculum`, { method: 'POST', body: { students: ids, ...body } });
    const apply = button('Seçilenlere uygula', { variant: 'primary', onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      const body = {};
      if (grade.input.value !== '__keep') body.gradeLevel = parseGrade(grade.input.value);
      if (track.input.value !== '__keep') body.track = parseChoice(track.input.value);
      if (exam.input.value !== '__keep') body.examTarget = parseChoice(exam.input.value);
      if (Object.keys(body).length === 0) { status.textContent = 'Değiştirilecek alan seçin.'; return; }
      try {
        const result = await send(ids, body);
        status.textContent = `${result.updated.length} öğrenci güncellendi.`
          + (result.failed.length ? ` ${result.failed.length} öğrenci atlandı: ${result.failed[0].error}` : '');
        await refresh();
      } catch (failure) {
        status.textContent = `Uygulanamadı: ${failure.message}`;
      }
    } });
    const suggest = button('Sınıf adından öneriyi uygula', { onClick: async () => {
      const ids = new Set(selection(status));
      if (!ids.size) return;
      const groups = new Map();
      for (const student of students.filter((row) => ids.has(row.kantinId))) {
        const changes = fillFromSuggestion(student);
        if (Object.keys(changes).length === 0) continue;
        const key = JSON.stringify(changes);
        if (!groups.has(key)) groups.set(key, { changes, ids: [] });
        groups.get(key).ids.push(student.kantinId);
      }
      if (groups.size === 0) { status.textContent = 'Seçilenlerde öneriyle doldurulacak boş alan yok.'; return; }
      const total = [...groups.values()].reduce((sum, group) => sum + group.ids.length, 0);
      if (!await confirmSimple(view, { title: 'Öneri uygulansın mı?',
        description: `${total} öğrencinin boş seviye/alan/hedef sınav alanı sınıf adından gelen öneriyle `
          + 'doldurulacak. Dolu alanlara dokunulmaz.', confirmLabel: 'Uygula' })) return;
      let updated = 0;
      let failed = 0;
      for (const group of groups.values()) {
        try {
          const result = await send(group.ids, group.changes);
          updated += result.updated.length;
          failed += result.failed.length;
        } catch {
          failed += group.ids.length;
        }
      }
      status.textContent = `${updated} öğrenci güncellendi${failed ? `, ${failed} atlandı` : ''}.`;
      await refresh();
    } });
    suggest.title = 'Seçilenlerin YALNIZ boş alanlarını sınıf adından gelen öneriyle doldurur.';
    apply.disabled = !can('bbd_students.manage');
    suggest.disabled = !can('bbd_students.manage');
    box.append(grade.wrap, track.wrap, exam.wrap, actionsRow(apply, suggest), status);
    return box;
  }

  /** Giriş kodları — listele (değiştirmez) ya da sıfırla (teyitli). */
  function bulkCodes(chain) {
    const box = section('Kantin giriş kodları', 'Kodları kantin üretir; elle şifre girilmez — iki öğrenciye '
      + 'aynı kodun verilmesi bu yüzden mümkün değildir.');
    const status = h('p', 'st-bulk-status', '');
    const list = button('Mevcut kodları listele ve yazdır', { onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      const result = await chain.run('access-code-list', { students: ids });
      if (!result) return;
      status.textContent = `${result.count} kod listelendi.`
        + (result.missing?.length ? ` ${result.missing.length} öğrencinin kodu yoktu.` : '');
    } });
    list.title = 'Hiçbir kodu değiştirmez; kantinde hâlâ geçerli olan kodları gösterir.';
    const reset = button('Seçilenlerin kodunu sıfırla ve yazdır', { variant: 'danger', onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      if (!await confirmSimple(view, { title: 'Toplu giriş kodu üret',
        description: `${ids.length} öğrenci için yeni giriş kodu üretilecek. Mevcut kodları geçersiz olacak.`,
        confirmLabel: 'Kodları üret', danger: true })) return;
      const result = await chain.run('access-code-reset', { students: ids });
      if (!result) return;
      status.textContent = `${result.count} yeni kod üretildi.`
        + (result.failed?.length ? ` ${result.failed.length} öğrenci sıfırlanamadı — tekrar deneyin.` : '');
      await refresh();
    } });
    list.hidden = !can('bbd_students.access_code');
    reset.hidden = !can('bbd_students.access_code');
    box.append(actionsRow(list, reset), status);
    box.hidden = !can('bbd_students.access_code');
    return box;
  }

  function bulkLifecycle() {
    const box = section('Pasifleştir', 'Yıl sonu mezunlar gibi toplu pasifleştirme; her öğrenci ayrı kaydedilir.');
    const status = h('p', 'st-bulk-status', '');
    const off = button('Seçilenleri pasifleştir', { variant: 'danger', onClick: async () => {
      const ids = selection(status);
      if (!ids.length) return;
      const chosen = students.filter((student) => ids.includes(student.kantinId));
      const answer = await askDeactivate(view, { students: chosen });
      if (!answer) return;
      try {
        const result = await api(`${BASE}/students/deactivate`, { method: 'POST', body: { students: ids, ...answer } });
        const parts = [`${result.deactivated?.length || 0} pasifleştirildi`];
        if (result.scheduled?.length) parts.push(`${result.scheduled.length} planlandı`);
        if (result.failed?.length) parts.push(`${result.failed.length} atlandı`);
        status.textContent = `${parts.join(' · ')}.`;
        await refresh();
      } catch (failure) {
        status.textContent = `Pasifleştirilemedi: ${failure.message}`;
      }
    } });
    box.append(actionsRow(off), status);
    box.hidden = !can('bbd_students.lifecycle');
    return box;
  }

  function buildBulkView() {
    const bulk = h('div', 'st-bulk');
    bulk.hidden = true;
    const layout = h('div', 'st-bulk-layout');
    nodes.bulkCount = h('p', 'st-bulk-count', 'Seçim yok');
    bulkPicker = createPicker({
      groupLabel: 'Sınıf',
      placeholder: 'Ad, sınıf, no ara',
      onChange: (ids) => { nodes.bulkCount.textContent = ids.length === 0 ? 'Seçim yok' : `${ids.length} öğrenci seçili`; },
    });
    layout.append(bulkPicker.node);
    const side = h('div', 'st-bulk-side');
    const chain = reportChain({ api, root: view, toast, base: BASE });
    const classBox = bulkClass();
    const groupBox = bulkGroups();
    side.append(nodes.bulkCount, classBox, groupBox, bulkCurriculum(), bulkCodes(chain), bulkLifecycle());
    layout.append(side);
    bulk.append(layout);
    bulk.prepare = () => { classBox.refreshClasses(); groupBox.loadGroups(); };
    return bulk;
  }

  // ------------------------------------------------------------- açılış
  setMode(options.payload?.view === 'groups' || options.payload?.tab === 'groups' ? 'groups'
    : options.payload?.view === 'bulk' ? 'bulk' : 'list');
  loadPermissions(api).then((set) => {
    permissions = set;
    return refresh();
  });
  // Canlı yenileme: KM'de, kasada ya da BBD Akademi'de olan değişiklik bu ekrana
  // 1 sn içinde düşer (km-merkezi-senkron §8). "Yenile" düğmesi yedek olarak kalır.
  stopWatch = watchEvents({
    api,
    types: 'student.*,guardian.*,canteen.*,group.changed,tablet.policy_changed',
    onEvents: (items, { reset }) => scheduleRefresh(reset ? [] : items),
  });

  const cleanup = () => {
    disposed = true;
    stopWatch?.();
    clearTimeout(refreshTimer);
    clearTimeout(toastTimer);
    bulkPicker?.destroy();
    record?.destroy();
    groupsView?.destroy();
    root.replaceChildren();
  };
  /** Gömülüyken sekmeyi Akademi seçer: `cleanup.setMode('list'|'bulk'|'groups')`. */
  cleanup.setMode = (next) => { if (modeButtons[next]) setMode(next); };
  return cleanup;
}

// --------------------------------------------------------------- yetenek

/**
 * Arayüz yetenekleri (K3). `bbd_students.list` öğrenci seçen paneller içindir
 * (her öğrenci bugün üyesi olduğu grupları taşır); `manager` ve `record`
 * öğrenci yönetiminin BBD Akademi paneline taşınmış hâlidir.
 */
export function capabilities(ctx) {
  /**
   * Öğrencinin TEK KARTINI açar — her ekrandan aynı yer (0.5.3, ADR 0031).
   * Kart BBD Akademi → Öğrenciler'dedir; Akademi yoksa bu modülün ekranı.
   * Yetenek çağrı anında çözülür (kayıt sırası önemsiz).
   */
  const openStudent = (kantinId, { tab = 'record' } = {}) => {
    if (!kantinId || typeof ctx.open !== 'function') return;
    const akademi = typeof ctx.capability === 'function' ? ctx.capability('bbd_akademi.students') : null;
    if (akademi) {
      ctx.open('bbd_akademi', { section: 'students', kmStudentId: kantinId, studentTab: tab });
    } else {
      ctx.open('bbd_students', { kantinId, standalone: true, tab });
    }
  };
  const MATCH = { name: '', schoolNo: 'okul no', card: 'kantin kartı', guardianPhone: 'veli telefonu',
    studentPhone: 'öğrenci telefonu', className: 'sınıf' };
  return {
    'bbd_students.open': openStudent,
    /**
     * Küresel arama sağlayıcısı (kabuğun üst çubuğu, `/`). Sonuç tek tıkla
     * öğrencinin kartını "Kayıt ve veliler" sekmesinde açar.
     */
    'bbd_students.search': {
      label: 'Öğrenciler',
      async search(query, { limit = 8 } = {}) {
        const params = new URLSearchParams({ q: query, limit: String(limit) });
        const payload = await ctx.api(`${BASE}/search?${params}`);
        return (payload?.items || []).map((item) => ({
          id: item.id,
          title: item.displayName,
          detail: [item.className || 'sınıf yok', item.schoolNo && `No ${item.schoolNo}`,
            item.guardian?.name, item.guardian?.phone, MATCH[item.match] && `eşleşen: ${MATCH[item.match]}`]
            .filter(Boolean).join(' · '),
          badge: item.status === 'inactive' ? 'Pasif' : 'Öğrenci',
          open: () => openStudent(item.id),
        }));
      },
    },
    'bbd_students.list': async () => {
      const [payload, memberships] = await Promise.all([
        ctx.api(`${BASE}/students`),
        ctx.api(`${BASE}/groups/memberships`).catch(() => ({ items: [] })),
      ]);
      const byStudent = new Map();
      for (const item of memberships.items || []) {
        if (!byStudent.has(item.studentId)) byStudent.set(item.studentId, []);
        byStudent.get(item.studentId).push({ id: item.groupId, name: item.groupName, kind: item.kind });
      }
      return (payload.students || []).map((student) => ({
        id: student.kantinId,
        name: student.displayName,
        className: student.className,
        groups: byStudent.get(student.kantinId) || [],
      }));
    },
    /**
     * `(root, {payload, capability, openStudent, hideModes})` → temizlik işlevi;
     * işlevin `setMode('list'|'bulk'|'groups')` özelliği sekmeyi değiştirir.
     */
    'bbd_students.manager': (root, options = {}) => createManager(root, {
      api: ctx.api, payload: options.payload || null, capability: options.capability || null,
      openStudent: options.openStudent, hideModes: Boolean(options.hideModes),
    }),
    /** `(root, {kantinId, capability, toast, dialogRoot, onChanged, onRemoved})` → {reload, destroy, dirty}. */
    'bbd_students.record': (root, options = {}) => {
      let permissions = null;
      const can = (permission) => permissions === null
        || permissions.has(permission) || permissions.has(`${permission}:*`);
      const host = h('div');
      root.replaceChildren(host);
      let instance = null;
      let alive = true;
      loadPermissions(ctx.api).then((set) => {
        permissions = set;
        if (alive) instance = mountRecord(host, { ...options, api: ctx.api, can });
      });
      return {
        reload: () => instance?.reload(),
        dirty: () => Boolean(instance?.dirty()),
        destroy() { alive = false; instance?.destroy(); root.replaceChildren(); },
      };
    },
  };
}

// ---------------------------------------------------------------- kurulum

export function mount(root, ctx) {
  // Öğrenci yönetimi BBD Akademi'de: Akademi'yi görebilen kullanıcı oraya
  // yönlendirilir (derin bağlantı verisiyle). `standalone: true` yönlendirmeyi
  // atlar (Akademi yokken `bbd_students.open` bu ekranı böyle açar). 0.5.3'ten
  // beri bu ekran menüde çizilmez (`ui.nav.replacedBy`, ADR 0031); buraya yalnız
  // eski derin bağlantılar düşer. Akademi modülü kapalıysa menüye geri döner.
  const akademi = typeof ctx.capability === 'function' ? ctx.capability('bbd_akademi.students') : null;
  if (akademi && !ctx.payload?.standalone && typeof ctx.open === 'function') {
    loadCss();
    const note = h('div', 'st st-moved');
    note.append(h('h3', null, 'Öğrenci yönetimi BBD Akademi’de'),
      h('p', 'st-hint', 'Öğrenci ekleme, sınıf ve grup atama, veliler ve kantin ayarları artık '
        + 'BBD Akademi → Öğrenciler bölümünde, öğrencinin tek kartında.'),
      button('BBD Akademi → Öğrenciler', { variant: 'primary', onClick: () => go() }));
    root.replaceChildren(note);
    const go = () => ctx.open('bbd_akademi', { section: 'students', kmStudentId: ctx.payload?.kantinId
      || ctx.payload?.externalRef || null, studentTab: ctx.payload?.tab || null });
    go();
    return () => root.replaceChildren();
  }
  return createManager(root, { api: ctx.api, payload: ctx.payload || null,
    capability: typeof ctx.capability === 'function' ? ctx.capability : null });
}
