// BBD Akademi — toplu işlemlerin "Kime?" seçicisi (BulkTarget / BundleTarget).
//
// Toplu ödev, atama paketi uygula / kaldır, toplu müfredat programı ve toplu
// kaynak ataması kaldırma AYNI seçiciyi kullanır (sade: kullanıcı her yerde
// aynı üç yolu görür):
//
//   · "Seçerek"     — sınıflar + çalışma grupları + tek tek öğrenciler (birleşim)
//   · "Süzgeçle"    — kurumun bütün etkin öğrencileri, seviye / alan / hedef
//                     sınava göre süzülmüş (yalnız BundleTarget)
//   · "Paketin kuralına uyanlar" — atama paketinin kuralları (yalnız paket)
//
// Her yolda "hariç tut" öğrenci listesi vardır (en son düşülür). Seçim
// sözleşme gövdesine `value()` ile çevrilir; sayıyı Akademi önizlemesi verir
// (burada öğrenci sayılmaz — üyelik işlem günündekidir).

import { h } from '../../../ui-kit/kit.js';
import {
  EXAM_TARGETS, GRADE_LEVELS, TRACKS, gradeText, multiChips, num, studentSelect,
} from '../common.js';
import { targetsField } from '../resources-common.js';

const MODES = {
  pick: 'Sınıf, grup ya da öğrenci seçerek',
  filter: 'Süzgeçle (seviye, alan, hedef sınav)',
  rules: 'Paketin kuralına uyan herkes',
};

let seq = 0;

/** '11' → 11, 'graduate' → 'graduate'. */
function grade(raw) {
  return raw === 'graduate' ? 'graduate' : Number(raw);
}

/**
 * @param {object} app
 * @param {object} options
 *   classes, groups: önbellekli listeler (`loadTargets`)
 *   modes: ['pick'] (BulkTarget) ya da ['pick', 'filter', 'rules'] (BundleTarget)
 *   rulesText: paket kurallarının okunur metni ('rules' kipinde gösterilir)
 *   value: başlangıç {classGroupIds, studentGroupIds, studentIds, students:[{id, displayName}]}
 *   onChange(): her değişiklikte
 */
export function bulkTargetField(app, { classes = [], groups = [], modes = ['pick'], rulesText = '',
  value = {}, onChange } = {}) {
  seq += 1;
  const node = h('div', 'ak-stack ak-bt');
  let mode = modes.includes(value.mode) ? value.mode : modes[0];
  let picked = { classGroupIds: value.classGroupIds || [], studentGroupIds: value.studentGroupIds || [],
    studentIds: (value.students || []).map((item) => item.id) };
  const filter = { gradeLevels: [], tracks: [], examTargets: [] };
  const excluded = new Map();
  const fire = () => onChange?.();

  // --------------------------------------------------------------- kip
  const radios = h('div', 'ak-bt-modes');
  radios.setAttribute('role', 'radiogroup');
  radios.setAttribute('aria-label', 'Hedef türü');
  const panes = {};
  if (modes.length > 1) {
    for (const key of modes) {
      const label = h('label', 'ak-bt-mode');
      const input = h('input');
      input.type = 'radio';
      input.name = `ak-bt-${seq}`;
      input.value = key;
      input.checked = key === mode;
      input.addEventListener('change', () => {
        if (!input.checked) return;
        mode = key;
        paint();
        fire();
      });
      label.append(input, h('span', '', MODES[key]));
      radios.append(label);
    }
    node.append(radios);
  }

  // ------------------------------------------------------- seçerek
  const pick = targetsField(app, { classes, groups, value: { ...value, students: value.students || [] },
    onChange: (next) => { picked = next; fire(); } });
  panes.pick = pick.node;

  // ------------------------------------------------------- süzgeçle
  const filterPane = h('div', 'ak-stack');
  const block = (title, control) => {
    const wrap = h('div', 'ak-res-target-block');
    const head = h('div', 'ak-res-target-title', title);
    wrap.append(head, control.node);
    control.node.setAttribute('role', 'group');
    control.node.setAttribute('aria-label', title);
    return wrap;
  };
  filterPane.append(
    h('div', 'ak-muted', 'Kurumun bütün etkin öğrencileri; seçtiğiniz koşullar birlikte aranır (boş = koşul yok).'),
    block('Sınıf seviyesi', multiChips(GRADE_LEVELS.map((level) => ({ value: String(level),
      label: gradeText(level) })), [], (list) => { filter.gradeLevels = list.map(grade); fire(); })),
    block('Alan', multiChips(Object.entries(TRACKS).map(([key, label]) => ({ value: key, label })), [],
      (list) => { filter.tracks = list; fire(); })),
    block('Hedef sınav', multiChips(Object.entries(EXAM_TARGETS).map(([key, label]) => ({ value: key, label })), [],
      (list) => { filter.examTargets = list; fire(); })),
  );
  panes.filter = filterPane;

  // ----------------------------------------------------- paket kuralı
  const rulesPane = h('div', 'ak-muted', rulesText
    ? `Kurallar: ${rulesText}. KM'deki seviye / alan / hedef bilgisi uyan bütün etkin öğrenciler.`
    : 'Pakette kural yok.');
  panes.rules = rulesPane;

  for (const key of modes) node.append(panes[key]);

  // ------------------------------------------------------- hariç tut
  // Hariç tutulacaklar da AYNI ortak seçiciyle seçilir (`studentSelect`); seçici
  // bölüm ilk açıldığında kurulur — açılmayan bölüm Akademi'ye istek atmaz (A14).
  const exclusion = h('details', 'ak-bt-exclude');
  const summary = h('summary', '', 'Hariç tutulacak öğrenciler (isteğe bağlı)');
  const paintExcluded = () => {
    summary.textContent = excluded.size
      ? `Hariç tutulan öğrenciler (${num(excluded.size)})` : 'Hariç tutulacak öğrenciler (isteğe bağlı)';
  };
  let excludePicker = null;
  exclusion.addEventListener('toggle', () => {
    if (!exclusion.open || excludePicker) return;
    excludePicker = studentSelect(app, { classes, label: 'Hariç tutulacak öğrenciler',
      placeholder: 'Hariç tutulacak öğrenciyi ara', onChange: (list) => {
        excluded.clear();
        for (const item of list) excluded.set(item.id, item.displayName);
        paintExcluded();
        fire();
      } });
    excludePicker.node.classList.add('ak-bt-exclude-pick');
    exclusion.append(excludePicker.node);
  });
  exclusion.append(summary);
  paintExcluded();
  node.append(exclusion);

  function paint() {
    for (const key of modes) panes[key].hidden = key !== mode;
  }
  paint();

  const hasFilter = () => filter.gradeLevels.length || filter.tracks.length || filter.examTargets.length;

  return {
    node,
    get mode() { return mode; },
    /** Sözleşme gövdesi. `bundle` true: BundleTarget (matchRules / filter alanlarıyla). */
    value({ bundle = modes.length > 1 } = {}) {
      const body = { excludeStudentIds: [...excluded.keys()] };
      if (mode === 'rules') return { ...body, matchRules: true };
      if (mode === 'filter') {
        return { ...body, matchRules: false, classGroupIds: [], studentGroupIds: [], studentIds: [],
          filter: { ...filter } };
      }
      const out = { ...body, classGroupIds: picked.classGroupIds || [], studentGroupIds: picked.studentGroupIds || [],
        studentIds: picked.studentIds || [] };
      if (bundle) { out.matchRules = false; out.filter = null; }
      return out;
    },
    /** Boş hedef → sebep (Akademi'nin `422 empty_target`ını beklemeden). */
    problem() {
      if (mode === 'rules') return rulesText ? null : 'Pakette kural yok; sınıf ya da öğrenci seçin.';
      if (mode === 'filter') return hasFilter() ? null : 'En az bir koşul seçin (seviye, alan ya da hedef sınav).';
      const count = (picked.classGroupIds || []).length + (picked.studentGroupIds || []).length
        + (picked.studentIds || []).length;
      return count ? null : 'En az bir sınıf, çalışma grubu ya da öğrenci seçin.';
    },
    /** "2 sınıf, 1 grup, 3 öğrenci · 1 hariç" — özet satırı. */
    summary() {
      const parts = [];
      if (mode === 'rules') parts.push('paketin kuralına uyanlar');
      else if (mode === 'filter') {
        const bits = [
          filter.gradeLevels.map((level) => gradeText(level)).join(', '),
          filter.tracks.map((key) => TRACKS[key] || key).join(', '),
          filter.examTargets.map((key) => EXAM_TARGETS[key] || key).join(', '),
        ].filter(Boolean);
        parts.push(`süzgeç: ${bits.join(' · ') || '—'}`);
      } else {
        const names = new Map([...classes, ...groups].map((item) => [item.id, item.name]));
        const chosen = [...(picked.classGroupIds || []), ...(picked.studentGroupIds || [])]
          .map((id) => names.get(id)).filter(Boolean);
        if (chosen.length) parts.push(chosen.length <= 4 ? chosen.join(', ') : `${num(chosen.length)} sınıf/grup`);
        if ((picked.studentIds || []).length) parts.push(`${num(picked.studentIds.length)} öğrenci`);
      }
      if (excluded.size) parts.push(`${num(excluded.size)} hariç`);
      return parts.join(' · ') || '—';
    },
    destroy() { pick.destroy?.(); excludePicker?.destroy(); },
  };
}
