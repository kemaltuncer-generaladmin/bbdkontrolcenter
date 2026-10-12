// BBD Akademi — Ders programı › Zil dilimleri ve Dönemler.
//
// ZİL DİLİMLERİNİN SAHİBİ. BBD'de zil saatleri KM Zil Sistemi'ndedir
// (`source: km`): burada salt okunur görünür, "KM'den şimdi çek" eşitlemeyi
// sıraya alır (202) ve saatler birkaç saniye içinde yenilenir. Yalnız
// `source: local` kurumda zil dilimleri buradan yazılır (aksi hâlde Akademi
// `409 bell_slots_managed_by_km` der). Saat değişince eşleşmeyen program
// satırları `orphaned` olur — kaydetmeden önce kullanıcı uyarılır.
//
// Zil dilimleri önbellekli okunur (`app.lookups.bellSlots`); her yazmadan ve
// eşitlemeden sonra `/v1/admin/bell-slots` önbelleği düşürülür ve Haftalık
// program sekmesine haber verilir.
//
// DÖNEMLER: ad + başlangıç + bitiş. "Geçerli dönem" Akademi'de tarihten
// hesaplanır (`current`), elle işaretlenmez; dönemler çakışamaz
// (`409 overlapping_term`).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, hintBox, tabBar } from '../../ui-kit/layout.js';
import { AkademiError } from './api.js';
import {
  DAY_LABEL, WEEKDAYS, actionButton, addDays, clockInput, confirmAction, empty, fmtDate,
  fmtDateTime, formDialog, loader, multiChips, num, problem, runAction, selectBox, staticList,
  todayIst, toolbar,
} from './common.js';
import { CLOCK, DAY_KEYS, clockOf, minutesOf, slotLayout, slotTime } from './timetable-shared.js';

// ------------------------------------------------------------ zil dilimleri

export function bellSlotsTab(app, { onChanged } = {}) {
  const node = h('div', 'ak-stack');
  const timers = new Set();
  let editor = null;
  const view = loader(() => app.lookups.bellSlots(), (data) => render(data), { rows: 8, columns: 6 });
  node.append(view.node);

  function refresh() {
    app.api.invalidate('/v1/admin/bell-slots');
    view.reload();
  }

  function render(data) {
    editor = null;
    const local = data?.source === 'local';
    const slots = data?.slots || [];
    const lessons = slots.filter((slot) => slot.kind === 'lesson').length;
    const canWrite = local && app.can('admin_putBellSlots');
    const syncButton = local ? null : actionButton(app, 'admin_syncBellSlots', 'KM\'den şimdi çek', {
      variant: 'primary', onClick: () => sync(syncButton, data?.syncedAt || null) });
    const nodes = [toolbar(
      badge(local ? 'Yerel — Akademi\'de tanımlı' : 'KM aynası', local ? 'info' : 'good'),
      h('span', 'ak-muted', local ? `${num(lessons)} ders dilimi`
        : `${num(lessons)} ders dilimi · son eşitleme ${data?.syncedAt ? fmtDateTime(data.syncedAt) : 'yok'}`),
      h('span', 'kit-spacer'),
      local ? null : button('Zil Sistemi\'ni aç', { title: 'KM Zil Sistemi ekranı',
        onClick: () => app.openPanel('bell') }),
      syncButton,
      button('Yenile', { variant: 'ghost', onClick: () => refresh() }),
    )];
    if (!local) {
      nodes.push(hintBox('BBD\'de zil saatleri KM Zil Sistemi\'nde tutulur ve burada salt okunur görünür. '
        + 'Saati değiştirmek için Zil Sistemi ekranını kullanın, sonra “KM\'den şimdi çek” ile Akademi\'ye '
        + 'aktarın. Saat değişince eşleşmeyen ders programı satırları “eşleşmiyor” olarak işaretlenir.'));
      if (data?.available === false) {
        nodes.push(alertBox('KM Zil Sistemi\'ne şu an ulaşılamıyor; son eşitlenen saatler gösteriliyor.', 'warn'));
      }
      nodes.push(weekView(slots));
      return nodes;
    }
    if (!canWrite) {
      nodes.push(hintBox('Zil dilimleri bu kurumda Akademi\'de tanımlıdır. Değiştirme yetkiniz yok.'),
        weekView(slots));
      return nodes;
    }
    editor = slotEditor(app, slots, {
      onSaved: () => {
        app.api.invalidate('/v1/admin/bell-slots');
        onChanged?.();
        view.reload();
      },
    });
    nodes.push(editor.node);
    return nodes;
  }

  async function sync(control, before) {
    const job = await runAction(app, () => app.api.post('/v1/admin/bell-slots/sync', undefined),
      { button: control });
    if (!job) return;
    app.toast('Eşitleme sıraya alındı; zil saatleri birkaç saniye içinde yenilenir.', 'good');
    const delays = [2000, 4000, 7000, 12000];
    let attempt = 0;
    const poll = () => {
      const timer = setTimeout(async () => {
        timers.delete(timer);
        app.api.invalidate('/v1/admin/bell-slots');
        let data = null;
        try { data = await app.lookups.bellSlots(); } catch { data = null; }
        attempt += 1;
        const updated = data && (data.syncedAt || null) !== before;
        if (updated || attempt >= delays.length) {
          view.reload();
          onChanged?.();
          app.toast(updated ? 'Zil saatleri KM\'den güncellendi.'
            : 'Eşitleme henüz bitmedi; “Yenile” ile tekrar bakın.', updated ? 'good' : 'warn');
          return;
        }
        poll();
      }, delays[attempt]);
      timers.add(timer);
    };
    poll();
  }

  return {
    node,
    reload: () => view.reload(),
    dirty: () => Boolean(editor?.dirty()),
    destroy() {
      for (const timer of timers) clearTimeout(timer);
      timers.clear();
    },
  };
}

/** Salt okunur haftalık zil tablosu (ızgara ile aynı hizalama). */
function weekView(slots) {
  const layout = slotLayout(slots);
  if (layout.empty) {
    return empty('Zil dilimi yok', 'Haftalık zil saati tanımlanmamış ya da henüz eşitlenmedi.');
  }
  const grid = h('div', 'ak-tt-grid ak-tt-bells');
  grid.style.gridTemplateColumns = `112px repeat(${layout.days.length}, minmax(96px, 1fr))`;
  grid.append(h('div', 'ak-tt-corner'));
  for (const day of layout.days) grid.append(h('div', 'ak-tt-dayhead', DAY_LABEL[day] || day));
  for (const row of layout.rows) {
    const head = h('div', `ak-tt-rowhead${row.isBreak ? ' ak-tt-is-break' : ''}`);
    head.append(h('b', '', row.label), h('small', '', slotTime(row.ref)));
    grid.append(head);
    for (const day of layout.days) {
      const slot = row.cells[day];
      if (!slot) {
        grid.append(h('div', 'ak-tt-none'));
        continue;
      }
      const cell = h('div', `ak-tt-bell${slot.kind === 'break' ? ' ak-tt-is-break' : ''}`);
      cell.append(h('b', '', slotTime(slot)));
      if (slot.label) cell.append(h('small', '', slot.label));
      grid.append(cell);
    }
  }
  const wrap = h('div', 'ak-tt-gridwrap');
  wrap.append(grid);
  return wrap;
}

/** Yerel zil dilimi düzenleyici: gün sekmesi + satırlar + günü kopyala. */
function slotEditor(app, slots, { onSaved }) {
  const node = h('div', 'ak-stack');
  const days = new Map(DAY_KEYS.map((key) => [key, []]));
  for (const slot of slots || []) {
    days.get(slot.dayOfWeek)?.push({ kind: slot.kind || 'lesson', startTime: slot.startTime,
      endTime: slot.endTime, label: slot.label || '' });
  }
  const byStart = (a, b) => String(a.startTime).localeCompare(String(b.startTime));
  for (const list of days.values()) list.sort(byStart);
  const snapshot = () => JSON.stringify(DAY_KEYS.map((key) => days.get(key)));
  let original = snapshot();
  let current = DAY_KEYS.find((key) => days.get(key).length) || 'mon';

  const tabs = tabBar(WEEKDAYS.map(([key, , long]) => ({ key, label: long })), current, (key) => {
    current = key;
    paint();
  });
  const table = h('div', 'ak-tt-slots');
  const errors = h('div', 'ak-stack');
  const copyTargets = multiChips(WEEKDAYS.map(([key, short]) => ({ value: key, label: short })), [], null);
  const dirtyText = h('span', 'ak-tt-dirty');
  const revert = button('Vazgeç', { variant: 'ghost', onClick: () => {
    const saved = JSON.parse(original);
    DAY_KEYS.forEach((key, index) => days.set(key, saved[index]));
    errors.replaceChildren();
    paint();
  } });
  const save = actionButton(app, 'admin_putBellSlots', 'Zil dilimlerini kaydet', { variant: 'primary',
    onClick: () => submit() });

  const adders = toolbar(
    button('+ Ders ekle', { onClick: () => addRow('lesson') }),
    button('+ Teneffüs ekle', { onClick: () => addRow('break') }),
    h('span', 'kit-spacer'),
    h('span', 'ak-muted', 'Bu günü şu günlere kopyala:'),
    copyTargets.node,
    button('Kopyala', { onClick: () => copyDay() }),
  );
  const bar = h('div', 'ak-tt-bar');
  bar.append(h('span', 'ak-muted', 'Saat değişince eşleşmeyen ders programı satırları “eşleşmiyor” olur.'),
    h('span', 'kit-spacer'), dirtyText, revert, save);
  node.append(tabs.node, table, adders, errors, bar);

  function changed() {
    paintBar();
  }

  function paintBar() {
    const dirty = snapshot() !== original;
    dirtyText.textContent = dirty ? 'Kaydedilmemiş değişiklik var' : 'Değişiklik yok';
    dirtyText.classList.toggle('on', dirty);
    revert.hidden = !dirty;
    if (app.can('admin_putBellSlots')) save.disabled = !dirty;
    for (const [key] of WEEKDAYS) tabs.badge(key, days.get(key).filter((row) => row.kind === 'lesson').length);
  }

  function duration(row) {
    if (!CLOCK.test(row.startTime) || !CLOCK.test(row.endTime)) return '—';
    const minutes = minutesOf(row.endTime) - minutesOf(row.startTime);
    return minutes > 0 ? `${num(minutes)} dk` : 'geçersiz';
  }

  function paint() {
    const rows = days.get(current);
    table.replaceChildren();
    const head = h('div', 'ak-tt-slot ak-tt-slot-head');
    for (const text of ['Tür', 'Başlangıç', 'Bitiş', 'Süre', 'Etiket', '']) head.append(h('span', '', text));
    table.append(head);
    if (!rows.length) {
      table.append(h('div', 'ak-tt-slot-empty', `${DAY_LABEL[current]} için zil dilimi yok. `
        + '“Ders ekle” ile başlayın ya da başka bir günü buraya kopyalayın.'));
    }
    let lessonNo = 0;
    rows.forEach((row, index) => {
      if (row.kind === 'lesson') lessonNo += 1;
      const line = h('div', `ak-tt-slot${row.kind === 'break' ? ' ak-tt-is-break' : ''}`);
      const kind = selectBox([{ value: 'lesson', label: 'Ders' }, { value: 'break', label: 'Teneffüs' }],
        row.kind, (value) => { row.kind = value; changed(); paint(); }, { label: 'Tür' });
      const length = h('span', 'ak-muted', duration(row));
      const start = clockInput(row.startTime, (value) => { row.startTime = value; length.textContent = duration(row); changed(); }, 'Başlangıç');
      const end = clockInput(row.endTime, (value) => { row.endTime = value; length.textContent = duration(row); changed(); }, 'Bitiş');
      const label = h('input', 'kit-input');
      label.type = 'text';
      label.maxLength = 40;
      label.value = row.label;
      label.placeholder = row.kind === 'break' ? 'ör. Öğle arası' : `${lessonNo}. ders`;
      label.setAttribute('aria-label', 'Etiket');
      label.addEventListener('input', () => { row.label = label.value; changed(); });
      const actions = h('span', 'ak-row-actions');
      const move = (step) => {
        const target = index + step;
        if (target < 0 || target >= rows.length) return;
        [rows[index], rows[target]] = [rows[target], rows[index]];
        changed();
        paint();
      };
      const up = button('↑', { variant: 'ghost', title: 'Yukarı taşı', onClick: () => move(-1) });
      const down = button('↓', { variant: 'ghost', title: 'Aşağı taşı', onClick: () => move(1) });
      up.disabled = index === 0;
      down.disabled = index === rows.length - 1;
      actions.append(up, down, button('Sil', { variant: 'ghost', onClick: () => {
        rows.splice(index, 1);
        changed();
        paint();
      } }));
      line.append(kind.node, start.node, end.node, length, label, actions);
      table.append(line);
    });
    paintBar();
  }

  function addRow(kind) {
    const rows = days.get(current);
    const last = [...rows].reverse().find((row) => CLOCK.test(row.endTime));
    const sameKind = [...rows].reverse().find((row) => row.kind === kind && CLOCK.test(row.startTime)
      && CLOCK.test(row.endTime) && row.endTime > row.startTime);
    const length = sameKind ? minutesOf(sameKind.endTime) - minutesOf(sameKind.startTime)
      : (kind === 'break' ? 10 : 40);
    const startAt = last ? minutesOf(last.endTime) : 8 * 60 + 30;
    rows.push({ kind, startTime: clockOf(startAt), endTime: clockOf(startAt + length), label: '' });
    changed();
    paint();
    table.querySelector('.ak-tt-slot:last-child input')?.focus();
  }

  function copyDay() {
    const targets = copyTargets.value.filter((key) => key !== current);
    if (!targets.length) {
      app.toast('Kopyalanacak günleri seçin.', 'warn');
      return;
    }
    for (const key of targets) days.set(key, days.get(current).map((row) => ({ ...row })));
    copyTargets.set([]);
    changed();
    paint();
    app.toast(`${DAY_LABEL[current]} saatleri ${targets.map((key) => DAY_LABEL[key]).join(', ')} `
      + 'günlerine kopyalandı (kaydedilmedi).', 'good');
  }

  function problems() {
    const list = [];
    for (const [key, , long] of WEEKDAYS) {
      const rows = days.get(key);
      rows.forEach((row, index) => {
        if (!CLOCK.test(row.startTime) || !CLOCK.test(row.endTime)) {
          list.push(`${long} ${index + 1}. satır: saati 08:30 gibi yazın.`);
        } else if (row.startTime >= row.endTime) {
          list.push(`${long} ${index + 1}. satır: bitiş başlangıçtan sonra olmalı.`);
        }
      });
      const valid = rows.filter((row) => CLOCK.test(row.startTime) && CLOCK.test(row.endTime)
        && row.startTime < row.endTime).sort(byStart);
      for (let index = 1; index < valid.length; index += 1) {
        if (valid[index].startTime < valid[index - 1].endTime) {
          list.push(`${long}: ${slotTime(valid[index - 1])} ile ${slotTime(valid[index])} üst üste biniyor.`);
        }
      }
    }
    return list;
  }

  async function submit() {
    errors.replaceChildren();
    const issues = problems();
    if (issues.length) {
      errors.append(alertBox(`${issues.slice(0, 6).join(' ')}${issues.length > 6 ? ` (+${issues.length - 6})` : ''}`, 'bad'));
      return;
    }
    const ok = await confirmAction(app, {
      title: 'Zil dilimlerini kaydet',
      description: 'Saatler değişince Akademi eşleşmeyen ders programı satırlarını “eşleşmiyor” olarak '
        + 'işaretler; bunların Haftalık program sekmesinde yeniden yerleştirilmesi gerekir.',
      confirmLabel: 'Kaydet',
    });
    if (!ok) return;
    const body = { slots: DAY_KEYS.flatMap((key) => [...days.get(key)].sort(byStart).map((row) => ({
      dayOfWeek: key,
      startTime: row.startTime,
      endTime: row.endTime,
      label: String(row.label || '').trim() || null,
      kind: row.kind,
    }))) };
    const next = await runAction(app, () => app.api.put('/v1/admin/bell-slots', body),
      { success: 'Zil dilimleri kaydedildi.', button: save });
    if (next) {
      original = snapshot();
      onSaved?.(next);
    } else {
      paintBar();
    }
  }

  paint();
  return { node, dirty: () => snapshot() !== original };
}

// ------------------------------------------------------------------ dönemler

function termState(row, today) {
  if (row.current) return badge('Geçerli dönem', 'good');
  if (String(row.startsOn) > today) return badge('Gelecek', 'info');
  return badge('Geçmiş', 'dim');
}

function termWeeks(row) {
  const start = Date.parse(`${row.startsOn}T00:00:00Z`);
  const end = Date.parse(`${row.endsOn}T00:00:00Z`);
  if (Number.isNaN(start) || Number.isNaN(end)) return '—';
  return `${num(Math.max(1, Math.round(((end - start) / 86_400_000 + 1) / 7)))} hafta`;
}

export function termsTab(app) {
  const node = h('div', 'ak-stack');
  const add = actionButton(app, 'admin_createTerm', 'Yeni dönem', { variant: 'primary', onClick: () => edit(null) });
  node.append(toolbar(h('span', 'ak-muted', 'Öğretim dönemleri. Geçerli dönem tarihe göre Akademi\'de '
    + 'belirlenir; dönemler birbiriyle çakışamaz.'), h('span', 'kit-spacer'), add));
  const today = todayIst();
  const list = staticList({
    load: () => app.lookups.terms(),
    map: (items) => [...items].sort((a, b) => String(b.startsOn).localeCompare(String(a.startsOn))),
    columns: [
      { key: 'name', label: 'Dönem', width: 'minmax(0, 1.6fr)', cell: (row) => h('b', '', row.name) },
      { key: 'startsOn', label: 'Başlangıç', width: '120px', cell: (row) => fmtDate(row.startsOn) },
      { key: 'endsOn', label: 'Bitiş', width: '120px', cell: (row) => fmtDate(row.endsOn) },
      { key: 'length', label: 'Süre', width: '90px', align: 'num', cell: (row) => termWeeks(row) },
      { key: 'state', label: 'Durum', width: '130px', cell: (row) => termState(row, today) },
      { key: 'actions', label: '', width: '110px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_updateTerm', 'Düzenle', { variant: 'ghost', onClick: () => edit(row) }));
        return actions;
      } },
    ],
    empty: { title: 'Dönem tanımlı değil', text: 'Örneğin “2026–2027 Güz” dönemini ekleyin.' },
  });
  node.append(list.node);

  async function edit(row) {
    const start = todayIst();
    const saved = await formDialog(app, {
      title: row ? `“${row.name}” dönemini düzenle` : 'Yeni dönem',
      description: 'Dönem tarihleri başka bir dönemle çakışamaz. Sınıf grupları ve raporlar dönemlere göre süzülür.',
      value: row ? { name: row.name, startsOn: row.startsOn, endsOn: row.endsOn }
        : { name: '', startsOn: start, endsOn: addDays(start, 139) },
      fields: [
        { key: 'name', label: 'Dönem adı', required: true, maxLength: 60, placeholder: '2026–2027 Güz', wide: true },
        { key: 'startsOn', label: 'Başlangıç', type: 'date', required: true },
        { key: 'endsOn', label: 'Bitiş', type: 'date', required: true },
      ],
      submitLabel: row ? 'Kaydet' : 'Dönemi oluştur',
      onSubmit: async (draft, { key }) => {
        if (draft.endsOn <= draft.startsOn) {
          throw new AkademiError({ code: 'invalid_range', message: 'Bitiş tarihi başlangıçtan sonra olmalı.' });
        }
        const body = { name: String(draft.name || '').trim(), startsOn: draft.startsOn, endsOn: draft.endsOn };
        try {
          return row ? await app.api.patch(`/v1/admin/terms/${row.id}`, body)
            : await app.api.post('/v1/admin/terms', body, { key });
        } catch (failure) {
          if (failure?.code === 'overlapping_term') {
            throw new AkademiError({ code: failure.code,
              message: `${problem(failure, 'Bu tarihler başka bir dönemle çakışıyor.')} `
                + 'Tarihleri diğer dönemlerle çakışmayacak şekilde değiştirin.' });
          }
          throw failure;
        }
      },
    });
    if (!saved) return;
    app.toast(row ? 'Dönem güncellendi.' : 'Dönem oluşturuldu.', 'good');
    app.api.invalidate('/v1/admin/terms');
    list.reload();
  }

  return { node, reload: () => list.reload() };
}
