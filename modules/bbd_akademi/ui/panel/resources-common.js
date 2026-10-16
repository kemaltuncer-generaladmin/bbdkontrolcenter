// BBD Akademi — Kaynaklar: ortak etiketler, hücreler ve seçiciler.
//
// Kaynaklar bölümü birkaç dosyaya bölündü (resources.js, -toc, -packages,
// -assign); hepsinin paylaştığı etiket haritaları, kaynak hücresi, kaynak
// seçici ve hedef (sınıf / grup / öğrenci) seçici burada durur. Uç adresi
// yalnız seçicilerde ve okuma amaçlıdır (`/v1/catalog/library`).

import { button, h } from '../../ui-kit/kit.js';
import { badge } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { bookPicker } from './book-picker.js';
import {
  CURRICULUM_EXAMS, EXAM_TARGETS, GRADE_LEVELS, TRACKS, empty, gradeText, multiChips, num, problem,
  searchBox, studentSelect,
} from './common.js';

export const KINDS = { topic_book: 'Konu anlatımlı', question_bank: 'Soru bankası',
  fascicle: 'Fasikül', practice_exam: 'Deneme seti', other: 'Diğer' };
export const SECTION_KINDS = { test: 'Test', chapter: 'Konu anlatımı', unit_test: 'Ünite testi',
  review: 'Tekrar', other: 'Diğer' };
export const SCOPES = { global: ['Hazır kütüphane', 'info'], tenant: ['Kurum kaynağı', 'good'] };
export const TARGET_KINDS = { class_group: 'Sınıf', student_group: 'Çalışma grubu',
  student: 'Öğrenci', package_rule: 'Paket kuralı' };
export { CURRICULUM_EXAMS, EXAM_TARGETS, TRACKS };

/** Seviye seçenekleri (çip / açılır kutu): değer metindir, gövdeye `gradeValue` ile çevrilir. */
export const GRADE_OPTIONS = GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }));

/** '11' → 11, 'graduate' → 'graduate', '' → null (sözleşme: integer 5..12 | 'graduate'). */
export function gradeValue(raw) {
  if (raw === null || raw === undefined || raw === '') return null;
  return raw === 'graduate' ? 'graduate' : Number(raw);
}

/** Boş değerleri sorgudan atar (vekile `x=` gitmesin). */
export function clean(query) {
  const out = {};
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '' || value === false) continue;
    out[key] = value;
  }
  return out;
}

/** [9,10,11,12,'graduate'] → '9–12. sınıf · Mezun'. Ardışık sınıflar aralığa katlanır. */
export function gradesText(levels) {
  const list = Array.isArray(levels) ? levels : [];
  const numbers = list.map(Number).filter((value) => Number.isInteger(value)).sort((a, b) => a - b);
  const parts = [];
  for (let index = 0; index < numbers.length; index += 1) {
    let end = index;
    while (end + 1 < numbers.length && numbers[end + 1] === numbers[end] + 1) end += 1;
    parts.push(end > index ? `${numbers[index]}–${numbers[end]}` : String(numbers[index]));
    index = end;
  }
  const out = [];
  if (parts.length) out.push(`${parts.join(', ')}. sınıf`);
  if (list.includes('graduate')) out.push('Mezun');
  return out.join(' · ');
}

/** Seviye · sınav · alan tek satırda. */
export function levelText(resource) {
  if (!resource) return '—';
  return [
    gradesText(resource.gradeLevels),
    (resource.exams || []).join('/'),
    (resource.tracks || []).map((track) => TRACKS[track] || track).join(', '),
  ].filter(Boolean).join(' · ') || '—';
}

export function subjectsText(resource) {
  return (resource?.subjects || []).map((subject) => subject.shortName || subject.name).join(', ') || '—';
}

export function contentText(resource) {
  if (!resource) return '—';
  return `${num(resource.sectionCount || 0)} bölüm · ${num(resource.questionCount || 0)} soru`;
}

export function scopeBadge(scope) {
  const [text, tone] = SCOPES[scope] || [scope || '—', 'dim'];
  return badge(text, tone);
}

/**
 * Kaynak hücresi: ad (tıklanabilir) + yayınevi · yıl · ISBN.
 * `onOpen` verilirse ad bağlantı düğmesidir (satır düğme DEĞİL — aynı satırda
 * eylem düğmeleri var, iç içe düğme olmaz).
 */
export function resourceCell(resource, onOpen) {
  const cell = h('div', 'ak-cell-stack');
  if (!resource) {
    cell.append(h('span', 'ak-muted', 'Kaldırılmış kaynak'));
    return cell;
  }
  let title;
  if (onOpen) {
    title = h('button', 'ak-res-link', resource.title || '—');
    title.type = 'button';
    title.addEventListener('click', () => onOpen(resource));
  } else {
    title = h('b', '', resource.title || '—');
  }
  cell.append(title, h('small', '', [resource.publisher, resource.year, resource.isbn ? `ISBN ${resource.isbn}` : '']
    .filter(Boolean).join(' · ')));
  return cell;
}

/** İki satırlı küçük hücre: üstte değer, altta açıklama. */
export function stackCell(top, bottom) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(top instanceof Node ? top : h('span', '', top ?? '—'));
  if (bottom) cell.append(h('small', '', bottom));
  return cell;
}

/**
 * Kaynak seçici: yazdıkça (250 ms) `catalog_searchLibrary` arar.
 * `scope`: 'all' | 'global' | 'tenant'. `onPick(resource)` liste yükündeki özet.
 */
export function resourcePicker(app, { onPick, scope = 'all', placeholder = 'Kaynak ara (ad, yayınevi, ISBN)' } = {}) {
  const node = h('div', 'ak-picker ak-res-picker');
  const results = h('div', 'ak-picker-list');
  results.hidden = true;
  let token = 0;
  const box = searchBox({ placeholder, onSearch: async (text) => {
    const mine = ++token;
    if (text.length < 2) { results.hidden = true; return; }
    const digits = text.replace(/[\s-]/g, '');
    const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
    try {
      const data = await app.api.get('/v1/catalog/library', { ...query, scope, limit: 12 });
      if (mine !== token) return;
      results.replaceChildren();
      const items = data?.items || [];
      if (!items.length) results.append(h('div', 'ak-picker-empty', 'Eşleşen kaynak yok.'));
      for (const item of items) {
        const row = h('button', 'ak-picker-row');
        row.type = 'button';
        row.append(h('b', '', item.title),
          h('span', '', [KINDS[item.kind], item.publisher, levelText(item),
            SCOPES[item.scope]?.[0]].filter((part) => part && part !== '—').join(' · ')));
        row.addEventListener('click', () => {
          results.hidden = true;
          onPick?.(item);
        });
        results.append(row);
      }
      results.hidden = false;
    } catch (failure) {
      if (mine !== token) return;
      results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
      results.hidden = false;
    }
  } });
  node.append(box.node, results);
  return { node, destroy: box.destroy, focus: () => box.node.focus() };
}

/**
 * Seçilmiş kaynak listesi (paket ve atama pencereleri): arama + kaldırılabilir satırlar.
 * `onChange(resources)` her değişiklikte tam listeyi verir.
 */
export function resourceListField(app, { initial = [], scope = 'all', onChange } = {}) {
  const node = h('div', 'ak-stack');
  const chosen = new Map(initial.map((item) => [item.id, item]));
  const list = h('div', 'ak-res-chosen');
  const paint = () => {
    list.replaceChildren();
    if (!chosen.size) list.append(h('div', 'ak-muted', 'Henüz kaynak seçilmedi.'));
    let index = 0;
    for (const item of chosen.values()) {
      index += 1;
      const row = h('div', 'ak-res-chosen-row');
      const remove = button('Çıkar', { variant: 'ghost', onClick: () => {
        chosen.delete(item.id);
        paint();
        onChange?.([...chosen.values()]);
      } });
      row.append(h('span', 'ak-res-chosen-no', `${index}.`), resourceCell(item),
        h('span', 'ak-muted', KINDS[item.kind] || ''), scopeBadge(item.scope), remove);
      list.append(row);
    }
  };
  const picker = resourcePicker(app, { scope, placeholder: 'Eklemek için kaynak ara', onPick: (item) => {
    if (chosen.has(item.id)) { app.toast('Bu kaynak zaten listede.', 'warn'); return; }
    chosen.set(item.id, item);
    paint();
    onChange?.([...chosen.values()]);
  } });
  // Ortak kitap seçicisinden çoklu seçim (README §14 madde 98 / 116): süzgeçli katalogda "şak şak seç".
  const many = button('Katalogdan çoklu seç', { variant: 'ghost', onClick: async () => {
    const items = await bookPicker(app, { multiple: true, title: 'Kitapları seç', initial: [...chosen.values()] });
    if (!items) return;
    for (const item of items) if (!chosen.has(item.id)) chosen.set(item.id, item);
    paint();
    onChange?.([...chosen.values()]);
  } });
  paint();
  const row = h('div', 'ak-inline');
  row.append(picker.node, many);
  node.append(row, list);
  return { node, value: () => [...chosen.values()], destroy: picker.destroy };
}

/**
 * Hedef seçici: sınıflar + çalışma grupları + öğrenciler (birlikte seçilebilir).
 * `kinds` çizilecek blokları daraltır (paket uygulamada kapsam tek türdür).
 * `onChange({classGroupIds, studentGroupIds, studentIds})`.
 */
export function targetsField(app, { classes = [], groups = [], value = {}, onChange,
  kinds = ['classes', 'groups', 'students'] } = {}) {
  const node = h('div', 'ak-res-targets');
  const state = {
    classGroupIds: [...(value.classGroupIds || [])],
    studentGroupIds: [...(value.studentGroupIds || [])],
    studentIds: [...(value.studentIds || [])],
  };
  const students = new Map((value.students || []).map((item) => [item.id, item.displayName || item.id]));
  const fire = () => onChange?.({ ...state, studentIds: [...students.keys()] });

  const block = (title, content) => {
    const wrap = h('div', 'ak-res-target-block');
    wrap.append(h('div', 'ak-res-target-title', title), content);
    return wrap;
  };

  const classChips = classes.length
    ? multiChips(classes.map((item) => ({ value: item.id, label: item.name })), state.classGroupIds,
      (list) => { state.classGroupIds = list; fire(); }).node
    : h('span', 'ak-muted', 'Sınıf tanımlı değil.');
  const groupChips = groups.length
    ? multiChips(groups.map((item) => ({ value: item.id, label: item.name })), state.studentGroupIds,
      (list) => { state.studentGroupIds = list; fire(); }).node
    : h('span', 'ak-muted', 'Çalışma grubu yok.');

  // Öğrenciler: ortak toplu seçici (liste + sınıf süzgeci + tümünü seç + sayı; `studentSelect`).
  // Kurulumu yalnız "Öğrenciler" bloğu istenince yapılır (istek ekonomisi, A14).
  const picker = kinds.includes('students') ? studentSelect(app, { classes, value: [...students]
    .map(([id, displayName]) => ({ id, displayName })), onChange: (list) => {
    students.clear();
    for (const item of list) students.set(item.id, item.displayName);
    fire();
  } }) : null;
  const studentBox = h('div', 'ak-stack');
  if (picker) studentBox.append(picker.node);

  if (kinds.includes('classes')) node.append(block('Sınıflar', classChips));
  if (kinds.includes('groups')) node.append(block('Çalışma grupları', groupChips));
  if (kinds.includes('students')) node.append(block('Öğrenciler', studentBox));
  return {
    node,
    value: () => ({ ...state, studentIds: [...students.keys()] }),
    count: () => state.classGroupIds.length + state.studentGroupIds.length + students.size,
    /** Seçili öğrenciler `{id, displayName}` (özet / belge için). */
    students: () => [...students].map(([id, displayName]) => ({ id, displayName })),
    destroy: () => picker?.destroy(),
  };
}

/** Sınıf ve çalışma grubu listeleri (önbellekli); okunamazsa boş liste. */
export function loadTargets(app) {
  return Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]);
}

/** Kaynak paketleri (seyrek değişir → ETag önbelleği; yazmadan sonra `invalidate`). */
export function loadPackages(app) {
  return app.api.getCached('/v1/admin/resource-packages').then((data) => data?.items || []);
}

/** Otomatik atama kuralı → "11. sınıf · Sayısal · YKS". */
export function ruleText(rule) {
  if (!rule) return '—';
  return [
    rule.gradeLevel !== null && rule.gradeLevel !== undefined && rule.gradeLevel !== '' ? gradeText(rule.gradeLevel) : '',
    rule.track ? TRACKS[rule.track] || rule.track : '',
    rule.examTarget ? `${EXAM_TARGETS[rule.examTarget] || rule.examTarget} hedefi` : '',
  ].filter(Boolean).join(' · ') || 'Bütün öğrenciler';
}

/** İçindekiler önizlemesi: ünite başlığı + bölüm tablosu (konu adları kapsamdan). */
export function tocPreview(resource) {
  const wrap = h('div', 'ak-res-preview');
  const units = [...(resource.toc || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  if (!units.length) {
    wrap.append(empty('İçindekiler girilmemiş',
      resource.scope === 'tenant' ? '“İçindekileri düzenle” ile ünite ve bölümleri girin.' : ''));
    return wrap;
  }
  const names = new Map((resource.topicCoverage || []).map((row) => [row.topic.id, row.topic.name]));
  for (const [index, unit] of units.entries()) {
    const sections = [...(unit.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    const questions = sections.reduce((acc, row) => acc + (row.questionCount || 0), 0);
    const head = h('div', 'ak-res-preview-unit');
    head.append(h('b', '', `${index + 1}. ${unit.title}`),
      h('span', 'ak-muted', `${num(sections.length)} bölüm · ${num(questions)} soru`));
    const table = dataTable({
      dense: true,
      columns: [
        { key: 'title', label: 'Bölüm', width: 'minmax(0, 2fr)' },
        { key: 'kind', label: 'Tür', width: '110px', cell: (row) => SECTION_KINDS[row.kind] || row.kind },
        { key: 'pages', label: 'Sayfa', width: '90px', cell: (row) => pageText(row) },
        { key: 'questions', label: 'Soru', width: '60px', align: 'num', cell: (row) => num(row.questionCount) },
        { key: 'topics', label: 'Konular', width: 'minmax(0, 1.6fr)', cell: (row) => (row.topicIds || [])
          .map((id) => names.get(id) || id).join(', ') || '—' },
      ],
      rows: sections,
      empty: empty('Bu ünitede bölüm yok'),
    });
    wrap.append(head, table.node);
  }
  return wrap;
}

export function pageText(row) {
  if (!row.pageFrom && !row.pageTo) return '—';
  if (row.pageFrom && row.pageTo && row.pageFrom !== row.pageTo) return `${row.pageFrom}–${row.pageTo}`;
  return String(row.pageFrom || row.pageTo);
}
