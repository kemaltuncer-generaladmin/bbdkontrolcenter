// BBD Akademi — müfredat program / konu seçicisi (ödev, plan görevi, deste, deneme, kaynak).
//
// SORUN (13.10.2026, kullanıcı): "Konu bulunamadı diyor. Lise grubu seçince program olarak
// 5. sınıftan başlatıyor." Kök nedenler: (1) program listesi kimlik sırasıyla geliyordu
// (`ortaokul-5-*` en üstte) ve seçici hedefin seviyesini bilmiyordu; (2) plan seçicisi
// "programın / ünitenin tamamı" için program ya da ünite kimliğini `topicId` olarak
// gönderiyordu (Akademi yalnız konu kimliği arıyordu → "Konu bulunamadı").
//
// ÇÖZÜM. `admin_listPrograms` artık seviye alanları (`levelKey`, `levelLabel`, `levelOrder`,
// `stage`, `examTarget`) taşır ve seviye sırasıyla döner; hedef (sınıf / grup / öğrenci)
// verilince hedefe atanan programlar `recommended`, hedefin seviyeleri `target` olur. Seviye
// SINIFTAN gelir (KM → Öğrenciler → Sınıflar); seviyesi bilinmeyen hedefte yönetici seviyeyi
// seçer ("Mezun" → TYT / AYT).
//
//   sortPrograms(items)         → seviye sırası (eski Akademi kimlik sırasıyla verse de)
//   programGroups(items)        → [{key, label, items}] seviye sırasıyla
//   programLabel(program)       → "11. sınıf · Matematik", "TYT · Matematik"
//   fillProgramSelect(select, items, {placeholder}) → optgroup'lu <select>
//   programPicker(app, {...})   → seviye çipleri + arama + gruplu seçim + hedef özeti
//   topicTree(app, {...})       → program → ünite → konu çipleri (aramalı), boş durum yol gösterir
//   targetQuery(target)         → `admin_listPrograms` hedef süzgeci

import { foldText, h } from '../../ui-kit/kit.js';
import { errorBox, multiChips, problem, searchBox, TRACKS } from './common.js';

export const PROGRAMS = '/v1/admin/curricula/programs';
const TTL = 10 * 60_000;
const MAX_GROUPS = 50;
const MAX_STUDENTS = 200;

/** Sunucu seviye alanı yoksa (eski Akademi) sınav / sınıftan türetilir. */
function levelOf(program) {
  if (program?.levelKey) {
    return { key: program.levelKey, label: program.levelLabel || program.levelKey,
      order: Number.isFinite(program.levelOrder) ? program.levelOrder : 200 };
  }
  const exam = program?.exam;
  if (exam) return { key: exam.toLowerCase(), label: exam, order: { LGS: 45, TYT: 90, AYT: 100 }[exam] ?? 150 };
  const grade = program?.grade;
  if (grade === 'graduate') return { key: 'mezun', label: 'Mezun', order: 110 };
  if (Number.isInteger(grade)) {
    return grade <= 8 ? { key: `ortaokul-${grade}`, label: `${grade}. sınıf`, order: (grade - 4) * 10 }
      : { key: `lise-${grade}`, label: `${grade}. sınıf`, order: grade * 10 - 40 };
  }
  return { key: 'kurum', label: 'Kurum programı', order: 200 };
}

/** "11. sınıf · Matematik", "AYT · Matematik (SAY / EA)", kurum programına "kurum". */
export function programLabel(program) {
  const level = levelOf(program);
  const tracks = program?.tracks?.length ? ` (${program.tracks.map((t) => TRACKS[t] || t).join(' / ')})` : '';
  const scope = program?.scope === 'tenant' ? ' · kurum' : '';
  return `${level.label} · ${program?.title || program?.id || ''}${tracks}${scope}`;
}

/** Seviye sırası (5→8, LGS, 9→12, TYT, AYT, mezun, kurum); seviye içinde gelen sıra korunur (kararlı). */
export function sortPrograms(items) {
  return [...(items || [])].sort((a, b) => levelOf(a).order - levelOf(b).order);
}

/** Programları seviye gruplarına ayırır (5→8, LGS, 9→12, TYT, AYT, mezun, kurum). */
export function programGroups(items) {
  const groups = new Map();
  for (const program of items || []) {
    const level = levelOf(program);
    if (!groups.has(level.key)) groups.set(level.key, { ...level, items: [] });
    groups.get(level.key).items.push(program);
  }
  return [...groups.values()].sort((a, b) => a.order - b.order);
}

function placeholderOption(text) {
  const option = h('option', '', text);
  option.value = '';
  return option;
}

/** `<select>`i optgroup'larla doldurur; önerilenler en üstte ayrı grupta. Seçimi korur. */
export function fillProgramSelect(select, items, { placeholder = 'Program seçin', recommendedFirst = true } = {}) {
  const keep = select.value;
  select.replaceChildren();
  select.append(placeholderOption(placeholder));
  const recommended = recommendedFirst ? (items || []).filter((program) => program.recommended) : [];
  const rest = (items || []).filter((program) => !recommended.includes(program));
  const add = (label, list) => {
    if (!list.length) return;
    const group = h('optgroup');
    group.label = label;
    for (const program of list) {
      const option = h('option', '', programLabel(program));
      option.value = program.id;
      group.append(option);
    }
    select.append(group);
  };
  add('Önerilen (hedefin seviyesi)', recommended);
  for (const group of programGroups(rest)) add(group.label, group.items);
  if ([...select.querySelectorAll('option')].some((option) => option.value === keep)) select.value = keep;
  return select;
}

/** Hedef → sorgu (sınırlar sunucuyla aynı; aşan hedefte öneri istenmez). */
export function targetQuery(target) {
  if (!target) return {};
  const classGroupIds = [...(target.classGroupIds || [])];
  const studentGroupIds = [...(target.studentGroupIds || [])];
  const studentIds = [...(target.studentIds || [])];
  if (classGroupIds.length + studentGroupIds.length > MAX_GROUPS || studentIds.length > MAX_STUDENTS) return {};
  const query = {};
  if (classGroupIds.length) query.classGroupIds = classGroupIds.join(',');
  if (studentGroupIds.length) query.studentGroupIds = studentGroupIds.join(',');
  if (studentIds.length) query.studentIds = studentIds.join(',');
  return query;
}

/** "Hedef: 11. sınıf · SAY (24) · 3 öğrencinin sınıfına seviye girilmemiş". */
export function targetSummary(target) {
  if (!target) return '';
  const parts = (target.levels || []).map((level) => (level.studentCount
    ? `${level.label} (${level.studentCount})` : level.label));
  const head = parts.length ? `Hedefin seviyesi: ${parts.join(', ')}` : 'Hedefin seviyesi bilinmiyor';
  const unknown = target.unknownLevelCount
    ? ` · ${target.unknownLevelCount} öğrencinin sınıfına seviye girilmemiş (Öğrenciler → Sınıflar)` : '';
  return head + unknown;
}

/** Programlar (önbellekli). Hedef verilirse öneri ve hedef özeti de gelir. */
export async function fetchPrograms(app, { subjectId = '', target = null, level = '' } = {}) {
  const query = { subject: subjectId || undefined, level: level || undefined, ...targetQuery(target) };
  const data = await app.api.getCached(PROGRAMS, query, { ttlMs: TTL });
  return { items: data?.items || [], target: data?.target || null };
}

/**
 * Program seçici: seviye çipleri (hedef seviyesi bilinmiyorsa yönetici seçer) + arama +
 * gruplu `<select>` + hedef özeti. `load({subjectId, target})` her ders / hedef değişiminde.
 * Önerilen tek seviyedeyse ilk önerilen program kendiliğinden seçilir.
 */
export function programPicker(app, { onChange, label = 'Program', placeholder = 'Program seçin (seviye / sınav)',
  emptyText = 'Bu derste program yok' } = {}) {
  const node = h('div', 'ak-stack ak-prog-picker');
  const levels = h('div', 'ak-chips ak-prog-levels');
  levels.setAttribute('role', 'group');
  levels.setAttribute('aria-label', 'Seviye');
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', label);
  const info = h('div', 'ak-muted ak-prog-target');
  let all = [];
  let level = '';
  let needle = '';
  let token = 0;
  const search = searchBox({ placeholder: 'Program ara (ör. TYT, 11, fizik)', label: 'Program ara',
    delay: 150, onSearch: (text) => { needle = foldText(text); paint(); } });
  const row = h('div', 'ak-inline');
  row.append(select, search.node);
  node.append(levels, row, info);
  select.addEventListener('change', () => onChange?.(select.value, current()));

  const current = () => all.find((program) => program.id === select.value) || null;

  function paint() {
    const visible = all.filter((program) => (!level || levelOf(program).key === level)
      && (!needle || foldText(`${programLabel(program)} ${program.subject?.name || ''}`).includes(needle)));
    fillProgramSelect(select, visible, { placeholder: all.length ? placeholder : emptyText });
    select.disabled = !all.length;
    search.node.hidden = all.length < 6;
  }

  function paintLevels() {
    levels.replaceChildren();
    const groups = programGroups(all);
    levels.hidden = groups.length < 2;
    const chip = (key, text) => {
      const item = h('button', `kit-chip${level === key ? ' on' : ''}`, text);
      item.type = 'button';
      item.setAttribute('aria-pressed', level === key ? 'true' : 'false');
      item.addEventListener('click', () => {
        level = level === key ? '' : key;
        paintLevels();
        paint();
        const only = all.filter((program) => !level || levelOf(program).key === level);
        if (level && only.length === 1) { select.value = only[0].id; onChange?.(select.value, current()); }
      });
      levels.append(item);
    };
    chip('', 'Tüm seviyeler');
    for (const group of groups) chip(group.key, group.label);
  }

  async function load({ subjectId = '', target = null } = {}) {
    const mine = ++token;
    select.replaceChildren(placeholderOption('Programlar yükleniyor…'));
    select.disabled = true;
    info.textContent = '';
    try {
      const data = await fetchPrograms(app, { subjectId, target });
      if (mine !== token) return null;
      all = data.items;
      const recommended = all.filter((program) => program.recommended);
      // Varsayılan seviye: hedefin tek seviyesi (önerilenlerin seviyesi) — yoksa hepsi.
      const keys = [...new Set(recommended.map((program) => levelOf(program).key))];
      level = keys.length === 1 ? keys[0] : '';
      paintLevels();
      paint();
      info.textContent = targetSummary(data.target);
      if (data.target && !recommended.length && all.length) {
        info.textContent += ' — yukarıdan seviyeyi seçin (mezunlar için TYT / AYT).';
      }
      const previous = select.value;
      if (!previous && recommended.length) {
        select.value = recommended[0].id;
        onChange?.(select.value, current());
      }
      return data;
    } catch (failure) {
      if (mine !== token) return null;
      all = [];
      select.replaceChildren(placeholderOption(problem(failure, 'Programlar alınamadı.')));
      return null;
    }
  }

  return {
    node,
    load,
    get value() { return select.value; },
    program: current,
    set(id) { select.value = id || ''; },
    destroy: () => search.destroy(),
  };
}

/**
 * Program → ünite → konu çipleri. `selected` (Map kimlik → ad) dışarıda tutulur; her değişimde
 * `onChange()`. Çok konulu programda arama. Yalnız KONU kimliği seçilir (ünite / program değil).
 */
export function topicTree(app, { selected, onChange }) {
  const node = h('div', 'ak-stack ak-hw-topics');
  let units = [];
  let needle = '';
  let token = 0;
  let chips = [];
  const search = searchBox({ placeholder: 'Konu ya da ünite ara', label: 'Konu ara', delay: 150,
    onSearch: (text) => { needle = foldText(text); paint(); } });
  const list = h('div', 'ak-stack');
  node.append(search.node, list);
  search.node.hidden = true;

  function paint() {
    list.replaceChildren();
    chips = [];
    let shown = 0;
    for (const unit of units) {
      const unitHit = needle && foldText(unit.title).includes(needle);
      const live = unit.topics.filter((topic) => !needle || unitHit || foldText(topic.title).includes(needle));
      if (!live.length) continue;
      shown += live.length;
      const block = h('div', 'ak-hw-unit');
      const group = multiChips(live.map((topic) => ({ value: topic.id, label: topic.title })),
        live.filter((topic) => selected.has(topic.id)).map((topic) => topic.id), (ids) => {
          const chosen = new Set(ids);
          for (const topic of live) {
            if (chosen.has(topic.id)) selected.set(topic.id, topic.title); else selected.delete(topic.id);
          }
          onChange?.();
        });
      chips.push({ group, live });
      block.append(h('div', 'ak-hw-unit-title', unit.title), group.node);
      list.append(block);
    }
    if (!shown) {
      list.append(h('span', 'ak-muted', units.length
        ? 'Aramaya uyan konu yok. Başka bir sözcük deneyin.'
        : 'Bu programda konu yok. Kuruma özel konu Müfredat ekranından eklenebilir; ya da başka seviyeden program seçin.'));
    }
  }

  return {
    node,
    async load(programId) {
      const mine = ++token;
      units = [];
      list.replaceChildren();
      search.node.hidden = true;
      if (!programId) {
        list.append(h('span', 'ak-muted', 'Konu seçmek için program seçin.'));
        return;
      }
      list.append(h('span', 'ak-muted', 'Konular yükleniyor…'));
      try {
        const program = await app.api.getCached(`${PROGRAMS}/${programId}`, undefined, { ttlMs: TTL });
        if (mine !== token) return;
        units = [...(program?.units || [])].filter((unit) => !unit.retired)
          .sort((a, b) => (a.order ?? 0) - (b.order ?? 0))
          .map((unit) => ({ ...unit, topics: [...(unit.topics || [])].filter((topic) => !topic.retired)
            .sort((a, b) => (a.order ?? 0) - (b.order ?? 0)) }));
        search.node.hidden = units.reduce((sum, unit) => sum + unit.topics.length, 0) < 20;
        paint();
      } catch (failure) {
        if (mine !== token) return;
        list.replaceChildren(errorBox(failure));
      }
    },
    refresh() {
      for (const { group, live } of chips) group.set(live.filter((topic) => selected.has(topic.id)).map((topic) => topic.id));
    },
    destroy: () => search.destroy(),
  };
}
