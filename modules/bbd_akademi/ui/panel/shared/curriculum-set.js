// BBD Akademi — müfredat SETİ (F-C7, README §14 madde 68): öğrenci başına + sınıf başına.
//
// KULLANICI (14.10.2026): "her öğrenciye ayrı müfredat istediğimiz gibi atalım, varsayılan olsun ama
// biz değiştirebilelim" · "11. sınıf TYT'ye de girer, hem 11. sınıf hem TYT müfredatlarını
// atayabilelim" · "hüseyin hocanın TYT sınıfına bağımsız müfredat atayabilmeliyiz (TYT mat)".
//
//   ETKİN MÜFREDAT = öğrencinin kendi seti (varsayılan ± elle) ∪ üyesi olduğu sınıfların müfredatı.
//   Varsayılan öğrencinin KM seviye / alan / hedef sınavından türer; elle eklenen / çıkarılan seviye
//   değişince korunur. Sınıf müfredatı sınıfın üyelerine EKLENİR (yerine geçmez).
//   Set DÖNEME bağlıdır (akademik yıl, 1 Eylül'de döner); geçmiş dönem salt okunurdur.
//
// Kaynak etiketleri: "Otomatik" (Akademi README §14 madde 102; eskiden "Varsayılan"), "Elle", "Sınıftan: 8-A".
// Otomatik müfredatın dayanağı KM seviyesi; KM öğrencide seviye yoksa ana sınıfın seviyesi ("sınıftan: 11-SAY").
// Yalnız sınıftan gelen öğe öğrencide
// çıkarılamaz (`removable: false`) — sınıf müfredatından ya da sınıftan çıkarılır.
//
// Hız (A14): set tek istekle okunur; program kataloğu önbellekli (`fetchPrograms`, 10 dk);
// her yazma yanıtı güncel seti döndürür — ayrıca okunmaz.

import { h } from '../../../ui-kit/kit.js';
import { alertBox, badge } from '../../../ui-kit/layout.js';
import { actionButton, confirmAction, empty, fmtDateTime, gradeText, loader, problem, selectBox, TRACKS } from '../common.js';
import { programGroups, programLabel, programMultiPicker } from './curriculum-picker.js';

const MAX_CHANGE = 60;                // CurriculumSetChange.add / remove üst sınırı

/** Gün → akademik yıl ("2026-2027"); 1 Eylül ve sonrası yeni dönem (Europe/Istanbul). */
export function academicYearOf(iso) {
  const [year, month] = String(iso || '').slice(0, 10).split('-').map(Number);
  if (!year || !month) return '';
  return month >= 9 ? `${year}-${year + 1}` : `${year - 1}-${year}`;
}

/** Bugünün dönemi + önceki iki dönem (geçmiş salt okunur). */
export function yearChoices(today) {
  const current = academicYearOf(today);
  if (!current) return [];
  const start = Number(current.slice(0, 4));
  return [0, 1, 2].map((back) => {
    const value = `${start - back}-${start - back + 1}`;
    return { value, label: back ? `${value} (geçmiş, salt okunur)` : `${value} (güncel)` };
  });
}

/** Kaynak etiketi: "Otomatik", "Elle", "Sınıftan: 8-A" (+ "önceki dönemden"). */
export function sourceText(source) {
  const base = source?.kind === 'default' ? 'Otomatik'
    : source?.kind === 'manual' ? 'Elle'
      : source?.kind === 'class' ? `Sınıftan: ${source.classGroup?.name || '—'}` : String(source?.kind || '');
  return source?.carriedFromYear ? `${base} · ${source.carriedFromYear} döneminden` : base;
}

const SOURCE_TONE = { default: 'dim', manual: 'info', class: 'good' };

/** Seviye "tümü" → o seviyedeki program kimlikleri (en çok 60; zaten sette olanlar atlanır). */
export function levelProgramIds(programs, levelKey, have = new Set()) {
  const group = programGroups(programs).find((item) => item.key === levelKey);
  return (group?.items || []).map((program) => program.id).filter((id) => !have.has(id)).slice(0, MAX_CHANGE);
}

/** "11. sınıf · Sayısal · YKS", sınıftan / gruptan geliyorsa "… (gruptan: MEZUN GRUBU · addan tahmin)"; seviye yoksa
 *  uyarı. Web / mobil aynı. */
export function basisText(basis) {
  if (!basis) return '';
  const parts = [basis.gradeLevel !== null && basis.gradeLevel !== undefined ? gradeText(basis.gradeLevel) : null,
    TRACKS[basis.track] || null, basis.examTarget || null].filter(Boolean);
  if (!parts.length) return 'seviye girilmemiş — otomatik müfredat yok (Öğrenciler → Sınıflar\'da sınıfına seviye girin)';
  const kind = basis.source === 'class' ? 'sınıftan' : basis.source === 'group' ? 'gruptan' : '';
  const from = kind && basis.classGroup?.name
    ? ` (${kind}: ${basis.classGroup.name}${basis.guessed ? ' · addan tahmin' : ''})` : '';
  return `${parts.join(' · ')}${from}`;
}

function todayIso() {
  return new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Istanbul' });
}

/**
 * Ekleme satırı (iş 30): ÇOKLU seçici — seviye grupları, arama, "Seviyenin tümü", seçilenler çip.
 * Seçilenler TEK istekte eklenir: `onAdd(ids, control)`.
 */
function addRow(app, { operationId, have, onAdd }) {
  const node = h('div', 'ak-stack ak-curset-add');
  const count = h('span', 'ak-muted', '0 seçili');
  const picker = programMultiPicker(app, { label: 'Eklenecek müfredatlar', max: MAX_CHANGE, have,
    onChange: (ids) => { count.textContent = `${ids.length} seçili`; } });
  const add = actionButton(app, operationId, 'Seçilenleri ekle', { variant: 'primary', onClick: async () => {
    const ids = picker.selection().filter((id) => !have().has(id));
    if (!ids.length) { app.toast('Önce müfredat seçin.', 'warn'); return; }
    await onAdd(ids, add);
  } });
  const line = h('div', 'ak-inline ak-curset-line');
  line.append(add, count);
  node.append(picker.node, line);
  picker.load();
  return { node, destroy: () => picker.destroy() };
}

/** Program satırı: ad + kaynak rozetleri + isteğe bağlı düğme. */
function programRow(program, badges = [], tools = []) {
  const row = h('li', 'ak-curset-row');
  const main = h('div', 'ak-curset-main');
  main.append(h('b', '', programLabel(program)));
  const tags = h('span', 'ak-curset-tags');
  for (const [text, tone] of badges) tags.append(badge(text, tone));
  main.append(tags);
  row.append(main, ...tools.filter(Boolean));
  return row;
}

function changeError(failure) {
  if (failure?.code === 'curriculum_from_class') {
    return 'Bu müfredat öğrencinin sınıfından geliyor; sınıfın müfredatından ya da sınıftan çıkarılır.';
  }
  if (failure?.code === 'class_group_archived') return 'Sınıf arşivde; müfredatı değiştirilemez.';
  return problem(failure, 'Müfredat değiştirilemedi.');
}

// ------------------------------------------------------------------ öğrenci

/**
 * Öğrencinin müfredat seti (çekmece → Müfredat sekmesinin üstü).
 * @returns {{node, reload, destroy}}
 */
export function studentCurriculumSet(app, studentId, { onChanged } = {}) {
  const node = h('div', 'ak-stack ak-curset');
  const years = yearChoices(todayIso());
  let year = years[0]?.value || '';
  let data = null;
  let adder = null;
  const yearSelect = selectBox(years, year, (value) => { year = value; view.reload(); }, { label: 'Dönem' });
  const path = `/v1/admin/students/${studentId}/curricula`;

  const view = loader(() => app.api.get(path, { academicYear: year || undefined }), (set) => {
    data = set;
    return render(set);
  }, { rows: 4, columns: 3 });

  async function change(body, control, success) {
    try {
      if (control) control.disabled = true;
      data = await app.api.post(path, body);
      app.toast(success, 'good');
      view.node.replaceChildren(...render(data));
      onChanged?.(data);
    } catch (failure) {
      app.toast(changeError(failure), 'bad');
    } finally {
      if (control) control.disabled = false;
    }
  }

  function render(set) {
    adder?.destroy();
    adder = null;
    const writable = Boolean(set?.current);
    const head = h('div', 'ak-inline ak-curset-head');
    head.append(h('span', 'ak-muted', `Otomatik müfredatın dayanağı: ${basisText(set?.basis)}`));
    if (set?.customized) head.append(badge('Elle değiştirildi', 'info'));
    const out = [head];
    if (!writable) out.push(alertBox('Geçmiş dönem — salt okunur. Konu ilerlemesi silinmez.', 'info'));

    const items = set?.items || [];
    const list = h('ul', 'ak-curset-list');
    for (const item of items) {
      const kinds = new Set((item.sources || []).map((source) => source.kind));
      const removable = writable && item.removable;
      const remove = removable ? actionButton(app, 'admin_changeStudentCurricula', 'Çıkar', { variant: 'ghost',
        onClick: async () => {
          const fromDefault = kinds.has('default');
          if (!await confirmAction(app, { title: `“${programLabel(item.program)}” setten çıkarılsın mı?`,
            description: fromDefault ? 'Varsayılan bir öğe; “Varsayılana döndür” ile geri gelir. Konu ilerlemesi silinmez.'
              : 'Konu ilerlemesi silinmez; yeni öneri ve seçicilerde görünmez.', confirmLabel: 'Çıkar' })) return;
          await change({ remove: [item.program.id] }, remove, 'Müfredat çıkarıldı.');
        } }) : null;
      list.append(programRow(item.program,
        (item.sources || []).map((source) => [sourceText(source), SOURCE_TONE[source.kind] || '']), [remove]));
    }
    out.push(items.length ? list : empty('Etkin müfredat yok',
      'Öğrencinin seviyesi KM\'de girilmemiş olabilir; aşağıdan müfredat ekleyin.'));

    if ((set?.removedDefaults || []).length) {
      const removed = h('ul', 'ak-curset-list');
      for (const program of set.removedDefaults) {
        const back = writable ? actionButton(app, 'admin_changeStudentCurricula', 'Geri ekle', { variant: 'ghost',
          onClick: () => change({ add: [program.id] }, back, 'Müfredat geri eklendi.') }) : null;
        removed.append(programRow(program, [['Varsayılandan çıkarıldı', 'warn']], [back]));
      }
      out.push(h('p', 'ak-curset-sub', 'Varsayılandan çıkarılanlar'), removed);
    }

    if (writable && (set?.suggestions || []).length) {
      const suggested = h('ul', 'ak-curset-list');
      for (const suggestion of set.suggestions) {
        const take = actionButton(app, 'admin_changeStudentCurricula', 'Bu döneme ekle', { variant: 'ghost',
          onClick: () => change({ add: [suggestion.program.id] }, take, 'Müfredat bu döneme eklendi.') });
        suggested.append(programRow(suggestion.program, [[`${suggestion.fromYear} döneminde elle eklenmişti`, 'info']],
          [take]));
      }
      out.push(h('p', 'ak-curset-sub', 'Önceki dönemden öneriler'), suggested);
    }

    if (writable) {
      const have = () => new Set((data?.items || []).map((item) => item.program.id));
      adder = addRow(app, { operationId: 'admin_changeStudentCurricula', have,
        onAdd: (ids, control) => change({ add: ids }, control,
          ids.length > 1 ? `${ids.length} müfredat eklendi.` : 'Müfredat eklendi.') });
      const reset = set?.customized ? actionButton(app, 'admin_resetStudentCurricula', 'Varsayılana döndür', {
        variant: 'ghost', onClick: async () => {
          if (!await confirmAction(app, { title: 'Müfredat seti varsayılana dönsün mü?',
            description: 'Bu dönemde elle eklenenler kalkar, çıkarılan varsayılanlar geri gelir. Sınıftan gelenler '
              + 'sürer; konu ilerlemesi silinmez.', confirmLabel: 'Varsayılana döndür' })) return;
          try {
            reset.disabled = true;
            data = await app.api.post(`${path}/reset`, {});
            app.toast('Müfredat seti varsayılana döndü.', 'good');
            view.node.replaceChildren(...render(data));
            onChanged?.(data);
          } catch (failure) {
            app.toast(changeError(failure), 'bad');
          } finally {
            reset.disabled = false;
          }
        } }) : null;
      out.push(h('p', 'ak-curset-sub', 'Müfredat ekle'), adder.node);
      if (reset) {
        const line = h('div', 'ak-inline');
        line.append(reset);
        out.push(line);
      }
    }
    return out;
  }

  const top = h('div', 'ak-inline ak-curset-top');
  top.append(h('span', 'ak-muted', 'Etkin müfredat = öğrencinin seti ∪ sınıflarının müfredatı. Konu önerileri, '
    + 'çalışma planı ve seçiciler bunu kullanır.'), h('span', 'kit-spacer'), yearSelect.node);
  node.append(top, view.node);
  return { node, reload: () => view.reload(), destroy: () => adder?.destroy() };
}

// -------------------------------------------------------------------- sınıf

/**
 * Sınıf müfredatı (Sınıflar → sınıf çekmecesi): sınıfa atanan programlar üyelerin etkin setine eklenir.
 * @returns {{node, reload, destroy}}
 */
export function classCurriculumSet(app, classGroupId, { archived = false, onChanged } = {}) {
  const node = h('div', 'ak-stack ak-curset');
  const years = yearChoices(todayIso());
  let year = years[0]?.value || '';
  let data = null;
  let adder = null;
  const path = `/v1/admin/class-groups/${classGroupId}/curricula`;
  const yearSelect = selectBox(years, year, (value) => { year = value; view.reload(); }, { label: 'Dönem' });
  const view = loader(() => app.api.get(path, { academicYear: year || undefined }), (set) => {
    data = set;
    return render(set);
  }, { rows: 3, columns: 3 });

  async function change(body, control, success) {
    try {
      if (control) control.disabled = true;
      data = await app.api.post(path, body);
      app.toast(success, 'good');
      view.node.replaceChildren(...render(data));
      app.api.invalidate('/v1/admin/class-groups');
      onChanged?.(data);
    } catch (failure) {
      app.toast(changeError(failure), 'bad');
    } finally {
      if (control) control.disabled = false;
    }
  }

  function render(set) {
    adder?.destroy();
    adder = null;
    const writable = Boolean(set?.current) && !archived;
    const out = [];
    if (!set?.current) out.push(alertBox('Geçmiş dönem — salt okunur.', 'info'));
    else if (archived) out.push(alertBox('Sınıf arşivde; müfredatı değiştirilemez.', 'info'));
    const items = set?.items || [];
    const list = h('ul', 'ak-curset-list');
    for (const item of items) {
      const remove = writable ? actionButton(app, 'admin_changeClassGroupCurricula', 'Kaldır', { variant: 'ghost',
        onClick: async () => {
          if (!await confirmAction(app, { title: `“${programLabel(item.program)}” sınıftan kaldırılsın mı?`,
            description: 'Üyelerin etkin setinden düşer (kendi setlerinde ya da başka sınıfta yoksa). Konu '
              + 'ilerlemesi silinmez.', confirmLabel: 'Kaldır' })) return;
          await change({ remove: [item.program.id] }, remove, 'Sınıf müfredatı kaldırıldı.');
        } }) : null;
      const meta = [item.assignedBy ? `${item.assignedBy} · ${fmtDateTime(item.assignedAt)}` : fmtDateTime(item.assignedAt)];
      if (item.carriedFromYear) meta.push(`${item.carriedFromYear} döneminden`);
      list.append(programRow(item.program, meta.filter(Boolean).map((text) => [text, 'dim']), [remove]));
    }
    const count = Number.isFinite(set?.memberCount) ? ` Bugün ${set.memberCount} öğrenci etkilenir.` : '';
    out.push(h('p', 'ak-muted', `Sınıf müfredatı üyelerin kendi setine EKLENİR, yerine geçmez.${count}`));
    out.push(items.length ? list : empty('Sınıf müfredatı yok', 'Ör. “TYT Matematik” atayın; sınıfın bütün '
      + 'öğrencilerinin etkin setine eklenir.'));
    if (writable && (set?.suggestions || []).length) {
      const suggested = h('ul', 'ak-curset-list');
      for (const suggestion of set.suggestions) {
        const take = actionButton(app, 'admin_changeClassGroupCurricula', 'Bu döneme ekle', { variant: 'ghost',
          onClick: () => change({ add: [suggestion.program.id] }, take, 'Sınıf müfredatı eklendi.') });
        suggested.append(programRow(suggestion.program, [[`${suggestion.fromYear} döneminde atanmıştı`, 'info']], [take]));
      }
      out.push(h('p', 'ak-curset-sub', 'Önceki dönemden öneriler'), suggested);
    }
    if (writable) {
      const have = () => new Set((data?.items || []).map((item) => item.program.id));
      adder = addRow(app, { operationId: 'admin_changeClassGroupCurricula', have,
        onAdd: (ids, control) => change({ add: ids }, control,
          ids.length > 1 ? `${ids.length} müfredat sınıfa atandı.` : 'Müfredat sınıfa atandı.') });
      out.push(h('p', 'ak-curset-sub', 'Sınıfa müfredat ata'), adder.node);
    }
    return out;
  }

  const top = h('div', 'ak-inline ak-curset-top');
  top.append(h('span', 'kit-spacer'), yearSelect.node);
  node.append(top, view.node);
  return { node, reload: () => view.reload(), destroy: () => adder?.destroy() };
}

/** Liste çipleri: sınıfın güncel müfredatı (`ClassGroup.curricula`). */
export function curriculaChips(programs) {
  const list = programs || [];
  if (!list.length) return '—';
  const node = h('span', 'ak-curset-tags');
  for (const program of list.slice(0, 3)) node.append(badge(program.title || programLabel(program), 'info'));
  if (list.length > 3) node.append(h('span', 'ak-muted', `+${list.length - 3}`));
  return node;
}

