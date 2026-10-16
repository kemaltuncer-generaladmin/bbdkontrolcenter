// BBD Akademi — Müfredat (F-A8 müfredat kısmı, F-C1, F-C2).
//
// SEVİYE KM'DEDİR (kullanıcı kararı). Öğrencinin seviyesi (5–12, mezun), alanı
// (SAY/EA/SÖZ/DİL) ve hedef sınavı (LGS/YKS) KM Öğrenciler ekranında
// tanımlanır; Akademi okul programlarını (5–12) ve sınav konu listelerini
// (LGS, TYT, AYT) bunlardan atama kurallarıyla çözer. Bu ekran kataloğu
// gösterir, kuruma özel eklemeleri ve program istisnalarını yönetir. Çözüm
// kuralı Akademi'dedir; "Program çözümle" yalnız önizlemedir.
//
// KÜRESEL KAYIT SALT OKUNURDUR: küresel program / ünite / konu düzenlenmez ve
// kaldırılmaz (Akademi 409 `global_read_only`), bu yüzden düğmesi de çizilmez.
// Kurum küresel programa kendi ünitesini, küresel üniteye kendi konusunu
// ekleyebilir; yalnız kendi eklediğini (`scope: tenant` / `custom`) düzenler.
// "Kaldır" silmez: kayıt `retired` olur, öğrenci ilerlemesi korunur.
//
// ÖNBELLEK: katalog seyrek değişir → `getCached`; her yazmadan sonra
// `/v1/admin/curricula/programs` öneki (liste + ayrıntı) düşürülür. Program
// listesi sözleşmede sayfasızdır (`{items}`), istisna listesi de öyle.

import { blockedButton, button, foldText, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow, skeletonRows, tabBar } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import {
  actionButton, confirmAction, CURRICULUM_EXAMS, empty, errorBox, EXAM_TARGETS, fmtDate,
  fmtDateTime, formDialog, GRADE_LEVELS, gradeText, kv, loader, num, openDrawer, optionsOf,
  runAction, searchBox, sectionHead, selectBox, staticList, statusBadge, studentPicker, toolbar,
  TRACKS,
} from '../common.js';
import { bulkTargetField } from '../shared/bulk-target.js';
import { loadTargets } from '../resources/resources-common.js';

const PROGRAMS = '/v1/admin/curricula/programs';
const VERSIONS = '/v1/admin/curricula/versions';

const KINDS = { school: 'Okul programı', exam: 'Sınav programı', custom: 'Kuruma özel' };
const SCOPE = { global: ['Küresel', 'dim'], tenant: ['Kuruma özel', 'info'] };
const SCOPE_FILTER = { all: 'Küresel + kuruma özel', global: 'Yalnız küresel', tenant: 'Yalnız kuruma özel' };
const OVERRIDE_ACTION = { add: ['Program eklendi', 'good'], remove: ['Program çıkarıldı', 'warn'] };
const TARGET_KIND = { student: 'Öğrenci', class_group: 'Sınıf' };
const SOURCE = { rule: ['Atama kuralı', 'info'], class_override: ['Sınıf istisnası', 'warn'],
  student_override: ['Öğrenci istisnası', 'warn'] };

// ---------------------------------------------------------------- yardımcılar

const gradeOptions = (emptyLabel) => [{ value: '', label: emptyLabel },
  ...GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }))];

/** Seçim kutusundaki seviye → sözleşme değeri (5..12 tam sayı | 'graduate' | null). */
function gradeValue(raw) {
  if (raw === '' || raw === null || raw === undefined) return null;
  return raw === 'graduate' ? 'graduate' : Number(raw);
}

function orderValue(raw) {
  return raw === '' || raw === null || raw === undefined ? null : Math.round(Number(raw));
}

function hasValue(value) {
  return value !== null && value !== undefined && value !== '';
}

/** "TYT · 12. sınıf · Sayısal" — programın seviye / sınav / alan özeti. */
function programLevel(program) {
  const parts = [];
  if (program?.exam) parts.push(program.exam);
  if (hasValue(program?.grade)) parts.push(gradeText(program.grade));
  if (program?.tracks?.length) parts.push(program.tracks.map((track) => TRACKS[track] || track).join(' / '));
  return parts.join(' · ') || '—';
}

function programCell(program) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('b', '', program?.title || '—'),
    h('small', '', [program?.subject?.name, programLevel(program)].filter((part) => part && part !== '—')
      .join(' · ')));
  return cell;
}

function stack(main, sub) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', main || '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
}

/** Yetkisi olmayana satır içi düğme çizilmez (ağaçta onlarca kapalı düğme gürültüdür). */
function rowAction(app, operationId, label, onClick) {
  if (!app.can(operationId)) return null;
  return button(label, { variant: 'ghost', onClick });
}

/** DELETE (204) — başarıda true. `runAction` boş gövdede `undefined` döndürebilir. */
function removeCall(app, path, success) {
  return runAction(app, async () => { await app.api.del(path); return true; }, { success })
    .then((done) => done === true);
}

function subjectOptions(app, emptyLabel) {
  return app.lookups.subjects().then((items) => [{ value: '', label: emptyLabel },
    ...items.map((item) => ({ value: item.id,
      label: `${item.name}${item.custom ? ' (kuruma özel)' : ''}` }))]);
}

function loadVersions(app) {
  return app.api.getCached(VERSIONS, undefined, { ttlMs: 10 * 60_000 });
}

function removableChip(text, onRemove, title = 'Kaldır') {
  const chip = h('button', 'kit-chip on', `${text} ✕`);
  chip.type = 'button';
  chip.title = title;
  chip.addEventListener('click', onRemove);
  return chip;
}

// ------------------------------------------------------------------ bölüm

export function section(app) {
  loadStyles(import.meta.url, 'curriculum.css');
  const node = h('div', 'ak-stack ak-cur');
  let started = false;

  const addProgram = actionButton(app, 'admin_createProgram', 'Kuruma özel program', {
    variant: 'primary',
    onClick: () => editProgram(app, null, (saved) => {
      bodies.catalog?.reload();
      if (saved?.id) openProgram(saved.id);
    }),
  });
  node.append(sectionHead('Müfredat', {
    subtitle: 'Okul programları (5–12) ve LGS, TYT, AYT konu listeleri; kuruma özel eklemeler ve '
      + 'program istisnaları. Öğrencinin seviyesi, alanı ve hedef sınavı KM Öğrenciler ekranında tanımlanır.',
    actions: [addProgram],
  }));

  const bodies = {};
  const tabs = tabBar([
    { key: 'catalog', label: 'Katalog' },
    { key: 'overrides', label: 'Program istisnaları' },
    { key: 'resolve', label: 'Program çözümle' },
    { key: 'versions', label: 'Sürümler ve kurallar' },
  ], 'catalog', (key) => showTab(key));
  const tabSlot = h('div');
  node.append(tabs.node, tabSlot);

  const ctx = { openProgram, showResolve, addOverride };
  const builders = {
    catalog: () => catalogTab(app, ctx),
    overrides: (init) => overridesTab(app, ctx, init),
    resolve: (init) => resolveTab(app, ctx, init),
    versions: () => versionsTab(app, ctx),
  };

  function showTab(key, init = null) {
    if (!bodies[key]) {
      bodies[key] = builders[key](init);
      tabSlot.append(bodies[key].node);
      bodies[key].reload();
    } else if (init) {
      bodies[key].apply?.(init);
    }
    tabs.select(key, false);
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  function openProgram(programId) {
    programDrawer(app, programId, {
      addOverride,
      onChange: () => {
        bodies.catalog?.reload();
        bodies.overrides?.reload();
      },
    });
  }

  function showResolve(params) { showTab('resolve', params); }

  function addOverride(prefill = {}, after) {
    overrideDialog(app, prefill, (saved) => {
      bodies.overrides?.reload();
      after?.(saved);
    });
  }

  return {
    node,
    shown(payload) {
      const studentInit = payload?.studentId
        ? { studentId: payload.studentId, studentLabel: payload.studentName || '' } : null;
      const wanted = studentInit ? 'overrides'
        : (payload?.tab && Object.hasOwn(builders, payload.tab) ? payload.tab : null);
      if (!started) {
        started = true;
        showTab(wanted || 'catalog', studentInit);
      } else if (wanted) {
        showTab(wanted, studentInit);
      }
      if (payload?.programId) openProgram(payload.programId);
    },
    destroy() {
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// ---------------------------------------------------------------- katalog

function catalogTab(app, { openProgram }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', grade: '', exam: '', subject: '', track: '', kind: '', version: '', scope: 'all' };
  const versionTitles = new Map();
  let lastRows = [];
  const set = (key) => (value) => { filters[key] = value; list.reload(); };

  const search = searchBox({ placeholder: 'Program ara (ad, ders)', onSearch: set('q') });
  const gradeSelect = selectBox(gradeOptions('Tüm seviyeler'), '', set('grade'), { label: 'Seviye' });
  const examSelect = selectBox(optionsOf(CURRICULUM_EXAMS, { empty: 'Tüm sınavlar' }), '', set('exam'),
    { label: 'Sınav' });
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', set('subject'), { label: 'Ders' });
  const trackSelect = selectBox(optionsOf(TRACKS, { empty: 'Tüm alanlar' }), '', set('track'), { label: 'Alan' });
  const kindSelect = selectBox(optionsOf(KINDS, { empty: 'Tüm türler' }), '', set('kind'), { label: 'Tür' });
  const versionSelect = selectBox([{ value: '', label: 'Tüm sürümler' }], '', set('version'),
    { label: 'Müfredat sürümü' });
  const scopeSelect = selectBox(optionsOf(SCOPE_FILTER), 'all', set('scope'), { label: 'Kapsam' });
  subjectOptions(app, 'Tüm dersler').then((options) => subjectSelect.options(options)).catch(() => {});
  loadVersions(app).then((data) => {
    const versions = data?.versions || [];
    for (const version of versions) versionTitles.set(version.id, version.title);
    versionSelect.options([{ value: '', label: 'Tüm sürümler' },
      ...versions.map((version) => ({ value: version.id, label: version.title }))]);
    if (lastRows.length) list.table.update({ rows: lastRows });
  }).catch(() => {});

  const count = h('span', 'ak-muted');
  node.append(toolbar(search.node, gradeSelect.node, examSelect.node, subjectSelect.node, trackSelect.node,
    kindSelect.node, versionSelect.node, scopeSelect.node, h('span', 'kit-spacer'), count));

  const query = () => {
    const out = {};
    for (const [key, value] of Object.entries(filters)) {
      if (value && !(key === 'scope' && value === 'all')) out[key] = value;
    }
    return out;
  };

  const list = staticList({
    load: () => app.api.getCached(PROGRAMS, query()),
    columns: [
      { key: 'title', label: 'Program', width: 'minmax(0, 2.4fr)', cell: (row) => programCell(row) },
      { key: 'kind', label: 'Tür', width: 'minmax(0, 1fr)', cell: (row) => KINDS[row.kind] || row.kind },
      { key: 'version', label: 'Sürüm', width: 'minmax(0, 1.2fr)',
        cell: (row) => versionTitles.get(row.curriculumVersion) || row.curriculumVersion || '—' },
      { key: 'size', label: 'Ünite / konu', align: 'num',
        cell: (row) => `${num(row.unitCount ?? 0)} / ${num(row.topicCount ?? 0)}` },
      { key: 'questions', label: 'Soru', align: 'num',
        cell: (row) => (hasValue(row.questionCount) ? num(row.questionCount) : '—') },
      { key: 'scope', label: 'Kapsam', cell: (row) => statusBadge(SCOPE, row.scope) },
    ],
    onRow: (row) => openProgram(row.id),
    onLoaded: (rows) => {
      lastRows = rows;
      count.textContent = rows.length ? `${num(rows.length)} program` : '';
    },
    empty: { title: 'Bu süzgece uyan program yok',
      text: 'Süzgeçleri gevşetin ya da kuruma özel program ekleyin.' },
  });
  node.append(list.node);

  return { node, reload: () => list.reload(), destroy() { search.destroy(); } };
}

// --------------------------------------------------------- program ayrıntısı

function programDrawer(app, programId, { onChange, addOverride } = {}) {
  const expanded = new Set();
  let includeRetired = false;
  let needle = '';
  let program = null;

  const search = searchBox({ placeholder: 'Ünite / konu ara', onSearch: (text) => {
    needle = foldText(text);
    paintTree();
  } });
  const panel = openDrawer(app, { title: 'Program', onClose: () => search.destroy() });
  panel.node.classList.add('ak-cur-drawer');

  const retiredBox = h('label', 'ak-inline ak-cur-check');
  const retiredInput = h('input', 'kit-check');
  retiredInput.type = 'checkbox';
  retiredInput.addEventListener('change', () => { includeRetired = retiredInput.checked; view.reload(); });
  retiredBox.append(retiredInput, h('span', '', 'Kaldırılanları göster'));
  const treeSlot = h('div', 'ak-cur-tree');
  const treeTools = toolbar(search.node,
    button('Tümünü aç', { variant: 'ghost', onClick: () => {
      for (const unit of program?.units || []) expanded.add(unit.id);
      paintTree();
    } }),
    button('Tümünü kapat', { variant: 'ghost', onClick: () => { expanded.clear(); paintTree(); } }),
    h('span', 'kit-spacer'), retiredBox);

  const view = loader(() => app.api.getCached(`/v1/admin/curricula/programs/${programId}`,
    includeRetired ? { includeRetired: true } : undefined), (data) => {
    program = data;
    panel.setTitle(data.title || 'Program');
    return render(data);
  }, { rows: 8, columns: 3 });
  panel.body.append(view.node);
  view.reload();

  function changed() {
    app.api.invalidate(PROGRAMS);
    view.reload();
    onChange?.();
  }

  function render(data) {
    const tenant = data.scope === 'tenant';
    const nodes = [];
    const tools = h('div', 'ak-inline');
    if (tenant) {
      tools.append(
        actionButton(app, 'admin_updateProgram', 'Programı düzenle', {
          onClick: () => editProgram(app, data, () => changed()) }),
        actionButton(app, 'admin_retireProgram', 'Programı kaldır', { variant: 'ghost',
          onClick: () => retireProgram(data) }),
      );
    }
    tools.append(
      actionButton(app, 'admin_createUnit', 'Ünite ekle', { variant: tenant ? 'primary' : '',
        onClick: () => editUnit(app, data, null, changed) }),
      actionButton(app, 'admin_createProgramOverride', 'İstisna ekle', {
        onClick: () => addOverride?.({ program: data }) }),
    );
    nodes.push(tools);
    if (!tenant) {
      nodes.push(alertBox('Küresel program salt okunurdur; ünite ve konuları düzenlenemez. Kurum bu '
        + 'programa kendi ünitesini, küresel ünitelere kendi konusunu ekleyebilir.', 'info'));
    }

    const units = data.units || [];
    const topics = units.flatMap((unit) => unit.topics || []);
    nodes.push(kpiRow([
      { label: 'Ünite', value: num(units.filter((unit) => !unit.retired).length) },
      { label: 'Konu', value: num(topics.filter((topic) => !topic.retired).length) },
      { label: 'Kuruma özel konu', value: num(topics.filter((topic) => topic.custom && !topic.retired).length) },
      { label: 'Soru sayısı', value: hasValue(data.questionCount) ? num(data.questionCount) : '—',
        title: 'Sınav bölümündeki soru sayısı (yalnız sınav programlarında).' },
    ]));

    const sources = data.sources || [];
    const sourceList = h('div', 'ak-cell-stack');
    for (const source of sources) {
      const line = h('span', '', [source.title || source.url, source.accessed ? `erişim ${fmtDate(source.accessed)}` : '']
        .filter(Boolean).join(' · '));
      if (source.url) line.title = source.url;
      sourceList.append(line);
    }
    nodes.push(card('', kv([
      ['Ders', data.subject?.name],
      ['Tür', KINDS[data.kind] || data.kind],
      ['Seviye / sınav', programLevel(data)],
      ['Müfredat sürümü', data.curriculumVersion],
      ['Kapsam', statusBadge(SCOPE, data.scope)],
      ['Program kimliği', h('code', 'ak-cur-pattern', data.id)],
      ['Açıklama', data.description || '—'],
      sources.length ? ['Kaynaklar', sourceList] : null,
    ])));

    const treeCard = h('div', 'ak-stack ak-card-pad');
    treeCard.append(treeTools, treeSlot);
    nodes.push(card('Üniteler ve konular', treeCard,
      tenant ? 'Kuruma özel program' : 'Küresel üniteler salt okunur'));
    paintTree();
    return nodes;
  }

  function paintTree() {
    if (!program) return;
    treeSlot.replaceChildren();
    const titles = new Map();
    for (const unit of program.units || []) {
      for (const topic of unit.topics || []) titles.set(topic.id, topic.title);
    }
    const units = [...(program.units || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    let shown = 0;
    for (const unit of units) {
      const topics = [...(unit.topics || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
      const unitHit = needle && foldText(unit.title).includes(needle);
      const matches = needle && !unitHit ? topics.filter((topic) => foldText(topic.title).includes(needle))
        : topics;
      if (needle && !unitHit && !matches.length) continue;
      shown += 1;
      treeSlot.append(unitBlock(unit, matches, needle ? true : expanded.has(unit.id), titles));
    }
    if (!shown) {
      treeSlot.append(needle ? empty('Aramaya uyan ünite ya da konu yok', 'Başka bir sözcük deneyin.')
        : empty('Bu programda ünite yok', 'Kuruma özel ünite ekleyebilirsiniz.'));
    }
  }

  function unitBlock(unit, topics, open, titles) {
    const box = h('div', `ak-cur-unit${unit.retired ? ' ak-cur-retired' : ''}`);
    const head = h('div', 'ak-cur-unit-head');
    const toggle = h('button', 'ak-cur-toggle');
    toggle.type = 'button';
    const caret = h('span', 'ak-cur-caret', open ? '▾' : '▸');
    toggle.append(caret, h('span', 'ak-cur-order', `${unit.order ?? ''}.`), h('b', 'ak-cur-title', unit.title));
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');

    const meta = h('span', 'ak-cur-meta');
    if (unit.custom) meta.append(badge('Kuruma özel', 'info'));
    if (unit.retired) meta.append(badge('Kaldırıldı', 'dim'));
    const liveCount = (unit.topics || []).filter((topic) => !topic.retired).length;
    meta.append(h('span', 'ak-muted', `${num(liveCount)} konu`));

    const actions = h('div', 'ak-row-actions');
    if (!unit.retired) {
      actions.append(...[
        rowAction(app, 'admin_createTopic', 'Konu ekle', () => editTopic(app, unit, null, changed)),
        unit.custom ? rowAction(app, 'admin_updateUnit', 'Düzenle', () => editUnit(app, program, unit, changed)) : null,
        unit.custom ? rowAction(app, 'admin_retireUnit', 'Kaldır', () => retireUnit(unit)) : null,
      ].filter(Boolean));
    }
    head.append(toggle, meta, h('span', 'kit-spacer'), actions);

    const body = h('div', 'ak-cur-topics');
    body.hidden = !open;
    for (const topic of topics) body.append(topicRow(unit, topic, titles));
    if (!topics.length) body.append(h('div', 'ak-muted ak-cur-none', 'Bu ünitede konu yok.'));
    toggle.addEventListener('click', () => {
      const next = body.hidden;
      body.hidden = !next;
      caret.textContent = next ? '▾' : '▸';
      toggle.setAttribute('aria-expanded', next ? 'true' : 'false');
      if (next) expanded.add(unit.id); else expanded.delete(unit.id);
    });
    box.append(head, body);
    return box;
  }

  function topicRow(unit, topic, titles) {
    const row = h('div', `ak-cur-topic${topic.retired ? ' ak-cur-retired' : ''}`);
    row.append(h('span', 'ak-cur-order', `${unit.order ?? ''}.${topic.order ?? ''}`));
    const text = h('div', 'ak-cell-stack');
    text.append(h('span', 'ak-cur-title', topic.title));
    const notes = [];
    if (hasValue(topic.weight)) notes.push(`ağırlık ${num(topic.weight, 2)}`);
    if (topic.retired && topic.replacedBy?.length) {
      notes.push(`yerine: ${topic.replacedBy.map((id) => titles.get(id) || id).join(', ')}`);
    }
    if (notes.length) text.append(h('small', '', notes.join(' · ')));
    const meta = h('span', 'ak-cur-meta');
    if (topic.custom) meta.append(badge('Kuruma özel', 'info'));
    if (topic.retired) meta.append(badge('Kaldırıldı', 'dim'));
    const actions = h('div', 'ak-row-actions');
    if (topic.custom && !topic.retired) {
      actions.append(...[
        rowAction(app, 'admin_updateTopic', 'Düzenle', () => editTopic(app, unit, topic, changed)),
        rowAction(app, 'admin_retireTopic', 'Kaldır', () => retireTopic(topic)),
      ].filter(Boolean));
    }
    row.append(text, meta, actions);
    return row;
  }

  async function retireProgram(data) {
    const yes = await confirmAction(app, { title: `“${data.title}” kaldırılsın mı?`,
      description: 'Program katalogdan ve atamalardan kalkar. Öğrencilerin bu programdaki ilerlemesi '
        + 'silinmez; kayıt “kaldırıldı” olarak saklanır.',
      confirmLabel: 'Programı kaldır', danger: true });
    if (!yes) return;
    if (await removeCall(app, `/v1/admin/curricula/programs/${data.id}`, 'Program kaldırıldı.')) {
      app.api.invalidate(PROGRAMS);
      panel.close();
      onChange?.();
    }
  }

  async function retireUnit(unit) {
    const yes = await confirmAction(app, { title: `“${unit.title}” ünitesi kaldırılsın mı?`,
      description: 'Ünite ve içindeki kuruma özel konular listeden kalkar; öğrenci ilerlemesi silinmez.',
      confirmLabel: 'Üniteyi kaldır', danger: true });
    if (!yes) return;
    if (await removeCall(app, `/v1/admin/curricula/units/${unit.id}`, 'Ünite kaldırıldı.')) changed();
  }

  async function retireTopic(topic) {
    const yes = await confirmAction(app, { title: `“${topic.title}” konusu kaldırılsın mı?`,
      description: 'Konu listeden kalkar; bu konuya bağlı öğrenci ilerlemesi silinmez.',
      confirmLabel: 'Konuyu kaldır', danger: true });
    if (!yes) return;
    if (await removeCall(app, `/v1/admin/curricula/topics/${topic.id}`, 'Konu kaldırıldı.')) changed();
  }

  return panel;
}

// ---------------------------------------------------------- formlar

async function editProgram(app, current, onSaved) {
  const subjects = await app.lookups.subjects().catch(() => []);
  const subjectChoices = [{ value: '', label: 'Ders seçin' },
    ...subjects.map((item) => ({ value: item.id, label: `${item.name}${item.custom ? ' (kuruma özel)' : ''}` }))];
  if (current?.subject?.id && !subjects.some((item) => item.id === current.subject.id)) {
    subjectChoices.push({ value: current.subject.id, label: current.subject.name });
  }
  const saved = await formDialog(app, {
    title: current ? 'Programı düzenle' : 'Kuruma özel program',
    description: current ? ''
      : 'Kurumun kendi programı (ör. “LGS kamp programı”, “Hızlı okuma”). Ünite ve konular sonra eklenir.',
    value: current ? {
      title: current.title, subjectId: current.subject?.id || '', description: current.description || '',
      grade: hasValue(current.grade) ? String(current.grade) : '', exam: current.exam || '',
    } : { subjectId: '', grade: '', exam: '' },
    fields: [
      { key: 'title', label: 'Program adı', required: true, maxLength: 80, wide: true },
      { key: 'subjectId', label: 'Ders', type: 'select', required: true, options: subjectChoices },
      { key: 'grade', label: 'Seviye', type: 'select', options: gradeOptions('Seviyesiz') },
      { key: 'exam', label: 'Sınav', type: 'select', options: optionsOf(CURRICULUM_EXAMS, { empty: 'Sınavsız' }) },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500, wide: true },
    ],
    submitLabel: current ? 'Kaydet' : 'Programı oluştur',
    onSubmit: (draft, { key }) => {
      const body = {
        subjectId: draft.subjectId,
        title: String(draft.title || '').trim(),
        description: String(draft.description || '').trim() || null,
        grade: gradeValue(draft.grade),
        exam: draft.exam || null,
      };
      return current ? app.api.patch(`/v1/admin/curricula/programs/${current.id}`, body)
        : app.api.post(PROGRAMS, body, { key });
    },
  });
  if (!saved) return;
  app.api.invalidate(PROGRAMS);
  app.toast(current ? 'Program güncellendi.' : 'Program oluşturuldu. Şimdi ünite ekleyebilirsiniz.', 'good');
  onSaved?.(saved);
}

async function editUnit(app, program, unit, onSaved) {
  const saved = await formDialog(app, {
    title: unit ? 'Üniteyi düzenle' : `Ünite ekle — ${program.title}`,
    description: !unit && program.scope === 'global'
      ? 'Küresel programa kuruma özel ünite eklenir; yalnız sizin kurumunuzda görünür.' : '',
    value: unit ? { title: unit.title, order: unit.order } : {},
    fields: [
      { key: 'title', label: 'Ünite adı', required: true, maxLength: 120, wide: true },
      { key: 'order', label: 'Sıra', type: 'number', min: 1, max: 999,
        hint: 'Boş bırakılırsa sona eklenir.' },
    ],
    submitLabel: unit ? 'Kaydet' : 'Üniteyi ekle',
    onSubmit: (draft, { key }) => {
      const body = { title: String(draft.title || '').trim(), order: orderValue(draft.order) };
      return unit ? app.api.patch(`/v1/admin/curricula/units/${unit.id}`, body)
        : app.api.post(`/v1/admin/curricula/programs/${program.id}/units`, body, { key });
    },
  });
  if (!saved) return;
  app.toast(unit ? 'Ünite güncellendi.' : 'Ünite eklendi.', 'good');
  onSaved?.(saved);
}

async function editTopic(app, unit, topic, onSaved) {
  const saved = await formDialog(app, {
    title: topic ? 'Konuyu düzenle' : `Konu ekle — ${unit.title}`,
    description: !topic && !unit.custom
      ? 'Küresel üniteye kuruma özel konu eklenir; yalnız sizin kurumunuzda görünür.' : '',
    value: topic ? { title: topic.title, order: topic.order, weight: topic.weight } : {},
    fields: [
      { key: 'title', label: 'Konu adı', required: true, maxLength: 160, wide: true },
      { key: 'order', label: 'Sıra', type: 'number', min: 1, max: 999,
        hint: 'Boş bırakılırsa sona eklenir.' },
      { key: 'weight', label: 'Ağırlık (isteğe bağlı)', type: 'number', min: 0,
        hint: 'Sınavdaki göreli ağırlık; bilinmiyorsa boş bırakın.' },
    ],
    submitLabel: topic ? 'Kaydet' : 'Konuyu ekle',
    onSubmit: (draft, { key }) => {
      const body = {
        title: String(draft.title || '').trim(),
        order: orderValue(draft.order),
        weight: hasValue(draft.weight) ? Number(draft.weight) : null,
      };
      return topic ? app.api.patch(`/v1/admin/curricula/topics/${topic.id}`, body)
        : app.api.post(`/v1/admin/curricula/units/${unit.id}/topics`, body, { key });
    },
  });
  if (!saved) return;
  app.toast(topic ? 'Konu güncellendi.' : 'Konu eklendi.', 'good');
  onSaved?.(saved);
}

// ---------------------------------------------------------- istisnalar

function overridesTab(app, { openProgram, showResolve, addOverride }, init) {
  const node = h('div', 'ak-stack');
  const filters = { studentId: '', studentLabel: '', classGroupId: '' };
  Object.assign(filters, pickFilter(init));

  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value;
    list.reload();
  }, { label: 'Sınıf' });
  app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).then((options) => {
    classSelect.options(options);
    classSelect.set(filters.classGroupId);
  }).catch(() => {});

  const studentSlot = h('span', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => {
    filters.studentId = student.id;
    filters.studentLabel = student.displayName || '';
    paintStudent();
    list.reload();
  } });
  function paintStudent() {
    studentSlot.replaceChildren();
    if (!filters.studentId) return;
    studentSlot.append(
      removableChip(filters.studentLabel || 'Seçili öğrenci', () => {
        filters.studentId = '';
        filters.studentLabel = '';
        paintStudent();
        list.reload();
      }, 'Öğrenci süzgecini kaldır'),
      button('Programlarını gör', { variant: 'ghost', onClick: () => showResolve({
        studentId: filters.studentId, studentLabel: filters.studentLabel }) }),
    );
  }

  const add = actionButton(app, 'admin_createProgramOverride', 'İstisna ekle', { variant: 'primary',
    onClick: () => addOverride(filters.studentId
      ? { student: { id: filters.studentId, displayName: filters.studentLabel || 'Seçili öğrenci' } }
      : (filters.classGroupId ? { classGroupId: filters.classGroupId } : {})) });
  // Toplu (0.5.4, F-A26): sınıflara / gruplara / süzgece uyan herkese tek işlemde ekle · çıkar · kurala döndür.
  const bulk = actionButton(app, 'admin_bulkProgramOverrides', 'Toplu program', {
    onClick: () => bulkOverrideDialog(app, () => list.reload()) });
  node.append(
    h('div', 'ak-muted', 'İstisna, atama kuralının sonucunu bir öğrenci ya da sınıf için değiştirir: '
      + 'kuralın vermediği bir programı ekler ya da verdiği bir programı çıkarır.'),
    toolbar(classSelect.node, picker.node, studentSlot, h('span', 'kit-spacer'), bulk, add),
  );

  const list = staticList({
    load: () => app.api.get('/v1/admin/curricula/overrides', {
      studentId: filters.studentId || undefined, classGroupId: filters.classGroupId || undefined }),
    columns: [
      { key: 'target', label: 'Hedef', width: 'minmax(0, 1.4fr)',
        cell: (row) => stack(row.target?.label, TARGET_KIND[row.target?.kind] || row.target?.kind) },
      { key: 'program', label: 'Program', width: 'minmax(0, 2fr)', cell: (row) => programCell(row.program) },
      { key: 'action', label: 'İşlem', cell: (row) => statusBadge(OVERRIDE_ACTION, row.action) },
      { key: 'note', label: 'Not', width: 'minmax(0, 1.4fr)', cell: (row) => row.note || '—' },
      { key: 'by', label: 'Ekleyen', width: 'minmax(0, 1.2fr)',
        cell: (row) => stack(row.createdBy, fmtDateTime(row.createdAt)) },
      { key: 'actions', label: '', width: '230px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button('Program', { variant: 'ghost', onClick: () => openProgram(row.program.id) }));
        if (row.target?.kind === 'student') {
          actions.append(button('Çözümle', { variant: 'ghost', onClick: () => showResolve({
            studentId: row.target.id, studentLabel: row.target.label }) }));
        }
        actions.append(actionButton(app, 'admin_deleteProgramOverride', 'Kaldır', { variant: 'ghost',
          onClick: () => remove(row) }));
        return actions;
      } },
    ],
    onLoaded: (rows) => {
      if (filters.studentId && !filters.studentLabel) {
        const hit = rows.find((row) => row.target?.kind === 'student' && row.target.id === filters.studentId);
        if (hit) { filters.studentLabel = hit.target.label; paintStudent(); }
      }
    },
    empty: { title: 'İstisna yok', text: 'Bu süzgece uyan öğrenci ya da sınıf istisnası bulunmuyor.' },
  });
  node.append(list.node);
  paintStudent();

  async function remove(row) {
    const yes = await confirmAction(app, { title: 'İstisna kaldırılsın mı?',
      description: `${row.target?.label || ''}: “${row.program?.title || ''}” ${row.action === 'add'
        ? 'eklemesi' : 'çıkarması'} kaldırılır; atama kuralının sonucu yeniden geçerli olur.`,
      confirmLabel: 'İstisnayı kaldır', danger: true });
    if (!yes) return;
    if (await removeCall(app, `/v1/admin/curricula/overrides/${row.id}`, 'İstisna kaldırıldı.')) list.reload();
  }

  return {
    node,
    reload: () => list.reload(),
    apply(next) {
      Object.assign(filters, { studentId: '', studentLabel: '', classGroupId: '' }, pickFilter(next));
      classSelect.set(filters.classGroupId);
      paintStudent();
      list.reload();
    },
    destroy() { picker.destroy(); },
  };
}

function pickFilter(init) {
  if (!init) return {};
  return { studentId: init.studentId || '', studentLabel: init.studentLabel || '',
    classGroupId: init.classGroupId || '' };
}

/**
 * Program arama alanı (formDialog özel alanı): ad / ders / seviye ile arar,
 * seçileni gösterir. İstisna penceresi ve toplu program penceresi ortak.
 * `state.program` seçili programdır (çağıran okur).
 */
function programSearchField(programs, disposers, state, { placeholder } = {}) {
  let program = state.program || null;
  return (draft, set) => {
    const wrap = h('div', 'ak-stack');
    const chosen = h('div', 'ak-inline');
    const results = h('div', 'ak-cur-pick');
    results.hidden = true;
    const box = searchBox({ placeholder: placeholder || 'Program ara: ad, ders, “TYT”, “9. sınıf”…',
      onSearch: (text) => paintResults(text) });
    disposers.push(() => box.destroy());
    const paintChosen = () => {
      chosen.replaceChildren();
      if (!program) {
        chosen.append(h('span', 'ak-muted', programs === null ? 'Program listesi alınamadı.'
          : `Program seçilmedi · ${num(programs.length)} program içinde arayın.`));
        box.node.hidden = programs === null;
        return;
      }
      chosen.append(programCell(program), button('Değiştir', { variant: 'ghost', onClick: () => {
        program = null;
        state.program = null;
        set('');
        paintChosen();
        box.node.focus();
      } }));
      box.node.hidden = true;
      results.hidden = true;
    };
    function paintResults(text) {
      const tokens = foldText(text).split(/\s+/).filter(Boolean);
      results.replaceChildren();
      if (!tokens.length || !programs) { results.hidden = true; return; }
      const hits = programs.filter((item) => {
        const hay = foldText(`${item.title} ${item.subject?.name || ''} ${programLevel(item)} ${item.id}`);
        return tokens.every((token) => hay.includes(token));
      });
      for (const item of hits.slice(0, 30)) {
        const row = h('button', 'ak-cur-pick-row');
        row.type = 'button';
        row.append(programCell(item), statusBadge(SCOPE, item.scope));
        row.addEventListener('click', () => {
          program = item;
          state.program = item;
          set(item.id);
          paintChosen();
        });
        results.append(row);
      }
      if (!hits.length) results.append(h('div', 'ak-muted ak-cur-none', 'Eşleşen program yok.'));
      else if (hits.length > 30) results.append(h('div', 'ak-muted ak-cur-none',
        `${num(hits.length)} sonuçtan ilk 30'u gösteriliyor; aramayı daraltın.`));
      results.hidden = false;
    }
    set(program?.id || '');
    paintChosen();
    wrap.append(chosen, box.node, results);
    return wrap;
  };

}

const BULK_ACTIONS = {
  add: 'Programı ekle (kuralın vermediği programı ver)',
  remove: 'Programı çıkar (kuralın verdiği programı kaldır)',
  clear: 'Bu programdaki istisnaları sil (kurala dön)',
};

/**
 * Toplu program istisnası (`admin_bulkProgramOverrides`, tek işlem, en çok
 * 2.000 öğrenci): seçilen sınıf / grup / öğrencilere ya da süzgece uyan
 * herkese programı ekler, çıkarır ya da istisnayı silip kurala döndürür.
 * "Önce say" aynı isteği `dryRun` ile gönderir (yazma yok).
 */
async function bulkOverrideDialog(app, onDone) {
  const [[classes, groups], programs] = await Promise.all([
    loadTargets(app),
    app.api.getCached(PROGRAMS).then((data) => data?.items || []).catch(() => null),
  ]);
  const disposers = [];
  const main = { program: null };
  const replaced = { program: null };
  let target = null;
  const previewSlot = h('div', 'ak-stack');
  previewSlot.setAttribute('aria-live', 'polite');
  previewSlot.append(h('div', 'ak-muted', '“Önce say” hiçbir şey yazmadan kaç öğrencinin etkileneceğini gösterir.'));

  const body = (draft, dryRun) => ({
    programId: main.program?.id,
    action: draft.action || 'add',
    replacesProgramId: draft.action === 'add' ? replaced.program?.id || null : null,
    target: target.value({ bundle: true }),
    note: String(draft.note || '').trim() || null,
    dryRun,
  });
  const missing = () => (!main.program ? 'Program seçin.' : target?.problem() || null);

  const result = await formDialog(app, {
    title: 'Toplu program',
    description: 'Seçtiğiniz öğrencilerin müfredat programını tek işlemde değiştirir; programları hemen yeniden '
      + 'hesaplanır (öğrenci uygulamasına anında yansır).',
    wide: true,
    value: { action: 'add' },
    fields: [
      { key: 'action', label: 'İşlem', type: 'select', options: optionsOf(BULK_ACTIONS) },
      { key: 'programId', label: 'Program', type: 'custom', render: programSearchField(programs, disposers, main),
        validate: (raw) => (raw ? null : 'Program seçin.') },
      { key: 'replaces', label: 'Yerine geçtiği program (isteğe bağlı)', type: 'custom', render: (draft, set) => {
        const wrap = h('div', 'ak-stack');
        wrap.append(h('div', 'ak-muted', 'Ör. TYT Matematik yerine AYT Matematik: eskisi çıkar, yenisi eklenir.'),
          programSearchField(programs, disposers, replaced, { placeholder: 'Çıkacak programı ara (isteğe bağlı)' })(
            draft, set));
        const paint = (next) => { wrap.hidden = (next.action || 'add') !== 'add'; };
        paint(draft);
        return { node: wrap, update: paint };
      } },
      { key: 'target', label: 'Kime', type: 'custom', render: () => {
        target = bulkTargetField(app, { classes, groups, modes: ['pick', 'filter'], onChange: () => {} });
        disposers.push(() => target.destroy());
        return target.node;
      }, validate: () => target?.problem() || null },
      { key: 'note', label: 'Not (isteğe bağlı)', maxLength: 200, placeholder: 'Ör. Dönem başı AYT geçişi' },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önce say', { onClick: async () => {
          const reason = missing();
          if (reason) { previewSlot.replaceChildren(alertBox(reason, 'warn')); return; }
          go.disabled = true;
          try {
            const counts = await app.api.post('/v1/admin/curricula/overrides/bulk', body(draft, true));
            previewSlot.replaceChildren(h('div', 'ak-muted', 'Önizleme — henüz bir şey yazılmadı:'), kpiRow([
              { label: 'Öğrenci', value: num(counts.studentCount) },
              { label: 'Eklenecek istisna', value: num(counts.created) },
              { label: 'Kalkacak istisna', value: num(counts.removed) },
              { label: 'Değişmeyen', value: num(counts.unchanged) },
            ]));
          } catch (failure) {
            previewSlot.replaceChildren(errorBox(failure));
          } finally {
            go.disabled = false;
          }
        } });
        wrap.append(go, previewSlot);
        return wrap;
      } },
    ],
    submitLabel: 'Uygula',
    onSubmit: (draft, { key }) => app.api.post('/v1/admin/curricula/overrides/bulk', body(draft, false), { key }),
  });
  for (const fn of disposers) fn();
  if (!result || result === true) return;
  app.toast(`${num(result.studentCount)} öğrenci · ${num(result.created)} istisna eklendi, ${num(result.removed)} `
    + `kaldırıldı, ${num(result.unchanged)} değişmedi.`, 'good');
  onDone?.(result);
}

/** İstisna ekle: hedef (öğrenci / sınıf) + program + ekle/çıkar + not. */
async function overrideDialog(app, prefill = {}, onDone) {
  const [classes, programs] = await Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.api.getCached(PROGRAMS).then((data) => data?.items || []).catch(() => null),
  ]);
  const disposers = [];
  let student = prefill.student || null;
  const state = { program: prefill.program || null };

  const targetField = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    let shownKind = null;
    let picker = null;
    const paint = () => {
      const kind = draft.targetKind || 'student';
      if (kind === shownKind) return;
      shownKind = kind;
      picker?.destroy();
      picker = null;
      wrap.replaceChildren();
      if (kind === 'class_group') {
        const select = selectBox([{ value: '', label: 'Sınıf seçin' },
          ...classes.map((item) => ({ value: item.id, label: item.name }))], draft.classGroupId || '',
        (value) => { draft.classGroupId = value; set(value); }, { label: 'Sınıf' });
        set(draft.classGroupId || '');
        wrap.append(classes.length ? select.node : h('span', 'ak-muted', 'Sınıf bulunamadı.'));
        return;
      }
      const picked = h('div', 'ak-inline');
      const paintPicked = () => {
        picked.replaceChildren(student
          ? removableChip(student.displayName || 'Öğrenci', () => { student = null; set(''); paintPicked(); })
          : h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
      };
      picker = studentPicker(app, { onPick: (item) => {
        student = { id: item.id, displayName: item.displayName };
        set(item.id);
        paintPicked();
      } });
      set(student?.id || '');
      paintPicked();
      wrap.append(picker.node, picked);
    };
    disposers.push(() => picker?.destroy());
    paint();
    return { node: wrap, update: paint };
  };

  const programField = programSearchField(programs, disposers, state);

  const noteField = (draft, set) => {
    const area = h('textarea', 'kit-textarea');
    area.maxLength = 200;
    area.placeholder = 'Ör. “Veli talebiyle AYT Fizik eklendi.”';
    area.addEventListener('input', () => set(area.value));
    return area;
  };

  const saved = await formDialog(app, {
    title: 'Program istisnası',
    description: 'Atama kuralı öğrencinin KM’deki seviye, alan ve hedef sınavından program verir. '
      + 'İstisna bu sonucu yalnız seçilen öğrenci ya da sınıf için değiştirir.',
    wide: true,
    value: { targetKind: prefill.classGroupId ? 'class_group' : 'student',
      classGroupId: prefill.classGroupId || '', action: 'add' },
    fields: [
      { key: 'targetKind', label: 'Hedef türü', type: 'select', options: optionsOf(TARGET_KIND) },
      { key: 'action', label: 'İşlem', type: 'select', options: [
        { value: 'add', label: 'Programı ekle (kuralın vermediği programı ver)' },
        { value: 'remove', label: 'Programı çıkar (kuralın verdiği programı kaldır)' }] },
      { key: 'targetId', label: 'Hedef', type: 'custom', render: targetField,
        validate: (raw, draft) => (raw ? null
          : (draft.targetKind === 'class_group' ? 'Sınıf seçin.' : 'Öğrenci seçin.')) },
      { key: 'programId', label: 'Program', type: 'custom', render: programField,
        validate: (raw) => (raw ? null : 'Program seçin.') },
      { key: 'note', label: 'Not (isteğe bağlı)', type: 'custom', render: noteField },
    ],
    submitLabel: 'İstisnayı ekle',
    onSubmit: (draft, { key }) => app.api.post('/v1/admin/curricula/overrides', {
      targetKind: draft.targetKind || 'student',
      targetId: draft.targetId,
      programId: draft.programId,
      action: draft.action || 'add',
      note: String(draft.note || '').trim() || null,
    }, { key }),
  });
  for (const fn of disposers) fn();
  if (!saved) return;
  app.toast('İstisna eklendi.', 'good');
  onDone?.(saved);
}

// ---------------------------------------------------------- çözümleme

function resolveTab(app, { openProgram, addOverride }, init) {
  const node = h('div', 'ak-stack');
  const state = { gradeLevel: '', track: '', examTarget: '', studentId: '', studentLabel: '' };
  let token = 0;

  const gradeSelect = selectBox(gradeOptions('Seviye seçin'), '', (value) => { state.gradeLevel = value; run(); },
    { label: 'Seviye' });
  const trackSelect = selectBox(optionsOf(TRACKS, { empty: 'Alan yok' }), '', (value) => {
    state.track = value; run();
  }, { label: 'Alan' });
  const targetSelect = selectBox(optionsOf(EXAM_TARGETS, { empty: 'Hedef sınav yok' }), '', (value) => {
    state.examTarget = value; run();
  }, { label: 'Hedef sınav' });
  const studentSlot = h('span', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'ya da öğrenci seçin', onPick: (student) => {
    state.studentId = student.id;
    state.studentLabel = student.displayName || '';
    paintStudent();
    run();
  } });
  const slot = h('div', 'ak-stack');
  node.append(
    h('div', 'ak-muted', 'Bir profilin hangi programları alacağını gösterir. Öğrenci seçilirse KM’deki '
      + 'seviyesi, alanı, hedef sınavı ve istisnaları kullanılır. Bu yalnız önizlemedir; değerler KM '
      + 'Öğrenciler ekranında değiştirilir.'),
    toolbar(gradeSelect.node, trackSelect.node, targetSelect.node, picker.node, studentSlot),
    slot,
  );

  function paintStudent() {
    studentSlot.replaceChildren();
    const locked = Boolean(state.studentId);
    for (const select of [gradeSelect, trackSelect, targetSelect]) select.node.disabled = locked;
    if (!locked) return;
    studentSlot.append(removableChip(state.studentLabel || 'Seçili öğrenci', () => {
      state.studentId = '';
      state.studentLabel = '';
      paintStudent();
      run();
    }, 'Öğrenci seçimini kaldır'));
  }

  function profileText() {
    if (state.studentId) return state.studentLabel || 'Seçili öğrenci';
    return [gradeText(state.gradeLevel), TRACKS[state.track], state.examTarget ? `hedef ${state.examTarget}` : '']
      .filter((part) => part && part !== '—').join(' · ');
  }

  async function run() {
    const mine = ++token;
    if (!state.studentId && !state.gradeLevel) {
      slot.replaceChildren(empty('Seviye ya da öğrenci seçin',
        'Seviye, alan ve hedef sınav seçince hangi programların atanacağı burada görünür.'));
      return;
    }
    const query = state.studentId ? { studentId: state.studentId } : { gradeLevel: state.gradeLevel,
      track: state.track || undefined, examTarget: state.examTarget || undefined };
    slot.replaceChildren(skeletonRows(5, 5));
    try {
      const data = await app.api.get('/v1/admin/curricula/resolve', query);
      if (mine !== token) return;
      slot.replaceChildren(...render(data?.programs || []));
    } catch (failure) {
      if (mine !== token) return;
      slot.replaceChildren(errorBox(failure, run));
    }
  }

  function render(items) {
    const fromRules = items.filter((item) => item.source === 'rule').length;
    const head = h('div', 'ak-inline');
    head.append(h('b', '', profileText()),
      h('span', 'ak-muted', `${num(items.length)} program · ${num(fromRules)} atama kuralından · `
        + `${num(items.length - fromRules)} istisnadan`), h('span', 'kit-spacer'));
    if (state.studentId) {
      head.append(actionButton(app, 'admin_createProgramOverride', 'Bu öğrenciye istisna ekle', {
        onClick: () => addOverride({ student: { id: state.studentId,
          displayName: state.studentLabel || 'Seçili öğrenci' } }, () => run()) }));
    }
    const table = dataTable({
      columns: [
        { key: 'program', label: 'Program', width: 'minmax(0, 2.4fr)', cell: (row) => programCell(row.program) },
        { key: 'kind', label: 'Tür', cell: (row) => KINDS[row.program?.kind] || row.program?.kind },
        { key: 'source', label: 'Kaynak', width: 'minmax(0, 1.3fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(statusBadge(SOURCE, row.source));
          if (hasValue(row.matchedRule)) cell.append(h('small', '', `Kural ${num(row.matchedRule + 1)}`));
          return cell;
        } },
        { key: 'size', label: 'Ünite / konu', align: 'num',
          cell: (row) => `${num(row.program?.unitCount ?? 0)} / ${num(row.program?.topicCount ?? 0)}` },
        { key: 'scope', label: 'Kapsam', cell: (row) => statusBadge(SCOPE, row.program?.scope) },
      ],
      rows: items,
      rowKey: (row) => row.program?.id,
      onRow: (row) => openProgram(row.program.id),
      empty: empty('Bu profile program düşmüyor',
        'Atama kurallarına bakın (Sürümler ve kurallar) ya da istisna ekleyin.'),
    });
    return [head, table.node];
  }

  function apply(next) {
    if (!next) return;
    state.studentId = next.studentId || '';
    state.studentLabel = next.studentLabel || '';
    if (!state.studentId) {
      state.gradeLevel = hasValue(next.gradeLevel) ? String(next.gradeLevel) : '';
      state.track = next.track || '';
      state.examTarget = next.examTarget || '';
      gradeSelect.set(state.gradeLevel);
      trackSelect.set(state.track);
      targetSelect.set(state.examTarget);
    }
    paintStudent();
  }

  apply(init);
  return {
    node,
    reload: run,
    apply(next) { apply(next); run(); },
    destroy() { picker.destroy(); },
  };
}

// ------------------------------------------------------------ sürümler

function versionsTab(app, { showResolve }) {
  const view = loader(() => loadVersions(app), (data) => {
    const versions = data?.versions || [];
    const rules = (data?.assignmentRules || []).map((rule, index) => ({ ...rule, index }));
    const versionTable = dataTable({
      columns: [
        { key: 'title', label: 'Sürüm', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title, row.id) },
        { key: 'grades', label: `${data?.academicYear || 'Bu'} öğretim yılında izleyen sınıflar`,
          width: 'minmax(0, 2fr)', cell: (row) => (row.grades2026 || []).map((grade) => gradeText(grade))
            .join(', ') || '—' },
      ],
      rows: versions,
      empty: empty('Sürüm bilgisi yok'),
    });
    const ruleTable = dataTable({
      columns: [
        { key: 'no', label: '#', width: '48px', align: 'num', cell: (row) => num(row.index + 1) },
        { key: 'grade', label: 'Seviye', cell: (row) => (hasValue(row.when?.grade) ? gradeText(row.when.grade)
          : 'Her seviye') },
        { key: 'track', label: 'Alan', cell: (row) => (row.when?.track ? TRACKS[row.when.track] || row.when.track
          : 'Her alan') },
        { key: 'target', label: 'Hedef sınav', cell: (row) => row.when?.examTarget || 'Her hedef' },
        { key: 'programs', label: 'Programlar', width: 'minmax(0, 3fr)', cell: (row) => {
          const cell = h('div', 'ak-cur-patterns');
          for (const pattern of row.programs || []) cell.append(h('code', 'ak-cur-pattern', pattern));
          return cell;
        } },
        { key: 'actions', label: '', width: '100px', cell: (row) => (hasValue(row.when?.grade)
          ? button('Önizle', { variant: 'ghost', onClick: () => showResolve({ gradeLevel: String(row.when.grade),
            track: row.when.track || '', examTarget: row.when.examTarget || '' }) })
          : blockedButton('Önizle', 'Seviyesi olmayan kural tek başına önizlenemez.', { variant: 'ghost' })) },
      ],
      rows: rules,
      rowKey: (row) => String(row.index),
      empty: empty('Atama kuralı yok'),
    });
    const ruleBody = h('div', 'ak-stack');
    ruleBody.append(h('div', 'ak-muted ak-card-pad', 'Bir öğrenciye uyan BÜTÜN kurallar birleşir (ilk eşleşen '
      + 'değil). Program kimliğinin sonundaki * önek eşleşmesidir: “lgs-*” bütün LGS programları demektir. '
      + 'Kurallar Akademi’nin müfredat tanımından gelir; kurum farkları istisnayla yapılır.'), ruleTable.node);
    return [
      kpiRow([
        { label: 'Öğretim yılı', value: data?.academicYear || '—' },
        { label: 'Müfredat sürümü', value: num(versions.length) },
        { label: 'Atama kuralı', value: num(rules.length) },
      ]),
      card('Müfredat sürümleri', versionTable.node, 'Hangi sınıf hangi öğretim programını izliyor'),
      card('Atama kuralları', ruleBody, 'Seviye × alan × hedef sınav → programlar'),
    ];
  }, { rows: 6, columns: 4 });
  const node = h('div', 'ak-stack');
  node.append(view.node);
  return { node, reload: () => view.reload() };
}
