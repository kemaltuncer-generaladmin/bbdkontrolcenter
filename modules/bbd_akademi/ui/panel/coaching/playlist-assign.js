// BBD Akademi — oynatma listesini öğrencilere ata (Akademi README §14 madde 124; F-K6, F-K9).
//
// Üç adım (ortak sihirbaz, `shared/wizard.js`):
//   1. Videolar  — Tüm liste · Seçili videolar (onay kutusu, "Tümünü seç" / "Hiçbiri", aralık "3–7", toplam süre) ·
//                  Güne yay ("Her gün N video" ya da "Her gün ~X dk", günler, tatil atlanır) · "Sırayla izle".
//   2. Kime?     — toplu hedef seçici (madde 116: sınıf, çalışma grubu, öğrenci).
//   3. Ne zaman? — başlangıç günü, son tarih, başlık, talimat; önizleme (`admin_previewPlaylistAssignment`, yazmaz):
//                  öğrenci / video / gün dağılımı, atlanan videolar, "3 öğrencide 5 video zaten atanmış" +
//                  "Zaten atanmışları atla" (`skipDuplicates`), uyarılar. "Ata" tek istek (`admin_createPlaylistAssignment`,
//                  sihirbaz boyunca sabit Idempotency-Key).
//
// Kaynak: kütüphane öğesi (`libraryItemId`), havuz öğesi (`videoPoolItemId`) ya da yalnız önizlenmiş liste (`playlistId`).

import { button, debounce, h } from '../../../ui-kit/kit.js';
import { alertBox, badge } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import { WEEKDAYS, addDays, dateInput, multiChips, num, todayIst } from '../common.js';
import { loadTargets } from '../resources/resources-common.js';
import { bulkTargetField } from '../shared/bulk-target.js';
import { wizard } from '../shared/wizard.js';
import { playlistHead, playlistStyles, selectionBar, videoRows } from './playlist.js';
import {
  ASSIGN_MODES, ASSIGN_MODE_HINTS, ASSIGN_WARNINGS, DAYS, SPREAD_KINDS, assignBody, assignableVideos, dayLine,
  duplicateText, previewHeadline, selectionOf, selectionText, skippedText, spreadOf,
} from './playlist-logic.js';

const ASSIGNMENTS = '/v1/admin/playlist-assignments';

function radioGroup(name, label, options, value, onChange) {
  const node = h('div', 'ak-bt-modes');
  node.setAttribute('role', 'radiogroup');
  node.setAttribute('aria-label', label);
  const inputs = [];
  for (const [key, text] of Object.entries(options)) {
    const option = h('label', 'ak-bt-mode');
    const radio = h('input');
    radio.type = 'radio';
    radio.name = name;
    radio.value = key;
    radio.checked = key === value;
    radio.addEventListener('change', () => { if (radio.checked) onChange(key); });
    inputs.push(radio);
    option.append(radio, h('span', '', text));
    node.append(option);
  }
  return { node, set(next) { for (const radio of inputs) radio.checked = radio.value === next; } };
}

function checkLine(text, checked, onChange) {
  const label = h('label', 'ak-inline');
  const box = h('input', 'kit-check');
  box.type = 'checkbox';
  box.checked = checked;
  box.addEventListener('change', () => onChange(box.checked));
  label.append(box, h('span', '', text));
  return label;
}

function numberInput(value, label, { min, max }) {
  const input = h('input', 'kit-input ak-pl-num');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = 3;
  input.value = String(value);
  input.setAttribute('aria-label', label);
  input.title = `${min}–${max}`;
  return input;
}

/**
 * @param {object} app
 * @param {object} options
 *   source: {libraryItemId} | {videoPoolItemId} | {playlistId}
 *   playlist: `VideoPlaylistDetail` (videolar dahil) — yoksa `admin_getVideoPlaylist` ile (kaynak `playlistId` ise
 *     önizleme ucuyla) okunur
 *   playlistId: liste kimliği (playlist verilmezse)
 *   target: başlangıç hedefi {studentIds, students:[{id, displayName}]} (ör. öğrenci kartından)
 * @returns {Promise<object|null>} oluşturulan `PlaylistAssignment` ya da null
 */
export async function assignPlaylistWizard(app, { source, playlist = null, playlistId = '', target: initial = null } = {}) {
  playlistStyles();
  let detail = playlist;
  if (!detail?.videos) {
    const id = playlistId || playlist?.playlistId || source?.playlistId;
    try {
      // Kaydedilmemiş (yalnız önizlenen) liste görünür bir öğeye bağlı değildir → önizleme ucu (yazmaz).
      detail = source?.playlistId
        ? await app.api.post('/v1/admin/video-playlists/preview', { playlistId: id })
        : await app.api.get(`/v1/admin/video-playlists/${encodeURIComponent(id)}`);
    } catch (failure) {
      app.toast(problem(failure, 'Liste okunamadı.'), 'bad');
      return null;
    }
  }
  const [classes, groups] = await loadTargets(app);
  const videos = detail.videos || [];
  const state = {
    source,
    mode: 'all',
    picked: new Set(assignableVideos(videos).map((video) => video.videoId)),
    spreadKind: 'perDay',
    perDay: 2,
    minutesPerDay: 40,
    daysOfWeek: ['mon', 'tue', 'wed', 'thu', 'fri'],
    skipHolidays: true,
    requireOrder: false,
    startDate: todayIst(),
    dueDate: '',
    title: '',
    instructions: '',
    skipDuplicates: true,
    notify: true,
  };
  let target = null;
  const body = () => assignBody({
    ...state,
    picked: state.mode === 'all' ? [] : selectionOf(videos, [...state.picked]).ids,
    target: target && !target.problem() ? target.value({ bundle: false }) : {},
  });

  // ======================================================= 1. Videolar
  const stepVideos = (ctl) => {
    const node = h('div', 'ak-stack');
    const hint = h('small', 'ak-muted', ASSIGN_MODE_HINTS[state.mode]);
    const modes = radioGroup('ak-pl-assign-mode', 'Atama biçimi', ASSIGN_MODES, state.mode, (key) => {
      state.mode = key;
      paint();
    });
    // Güne yay ayarları
    const spread = h('div', 'ak-stack ak-pl-spread');
    const perDay = numberInput(state.perDay, 'Her gün video sayısı', { min: 1, max: 20 });
    perDay.addEventListener('input', () => { state.perDay = perDay.value; ctl.clearError(); });
    const minutes = numberInput(state.minutesPerDay, 'Her gün dakika', { min: 5, max: 600 });
    minutes.addEventListener('input', () => { state.minutesPerDay = minutes.value; ctl.clearError(); });
    const perDayRow = h('span', 'ak-inline');
    perDayRow.append(h('span', '', 'Her gün'), perDay, h('span', '', 'video'));
    const minutesRow = h('span', 'ak-inline');
    minutesRow.append(h('span', '', 'Her gün ~'), minutes, h('span', '', 'dk'));
    const kind = radioGroup('ak-pl-spread-kind', 'Güne yay ölçüsü', SPREAD_KINDS, state.spreadKind, (key) => {
      state.spreadKind = key;
      paintSpread();
    });
    const days = multiChips(WEEKDAYS.map(([key, , long]) => ({ value: key, label: long })), state.daysOfWeek,
      (list) => { state.daysOfWeek = DAYS.filter((day) => list.includes(day)); });
    days.node.setAttribute('role', 'group');
    days.node.setAttribute('aria-label', 'Görev verilecek günler');
    spread.append(kind.node, perDayRow, minutesRow, h('span', 'ak-muted', 'Görev verilecek günler'), days.node,
      checkLine('Kurum tatillerini atla', state.skipHolidays, (on) => { state.skipHolidays = on; }),
      h('small', 'ak-muted', 'Seçili videolar liste sırasıyla günlere dağıtılır; video bölünmez, süresi bilinmeyen video '
        + '10 dk sayılır.'));
    const paintSpread = () => {
      perDayRow.hidden = state.spreadKind !== 'perDay';
      minutesRow.hidden = state.spreadKind !== 'minutesPerDay';
    };

    // Seçim
    const listTitle = h('h4', 'ak-coach-sub', 'Videolar');
    let bar = null;
    const rows = videoRows({ videos, picked: state.picked, readonly: () => state.mode === 'all',
      onToggle: () => { bar.paint(); note(); } });
    bar = selectionBar({ videos, picked: state.picked, onChange: () => { rows.paint(); bar.paint(); note(); ctl.clearError(); } });
    const allSummary = h('small', 'ak-muted', selectionText(selectionOf(videos,
      assignableVideos(videos).map((video) => video.videoId))));
    const order = checkLine('Sırayla izle (önceki video tamamlanmadan sonraki açılmaz)', state.requireOrder,
      (on) => { state.requireOrder = on; });

    node.append(playlistHead(detail), modes.node, hint, spread, order, listTitle, allSummary, bar.node, rows.node);
    function note() {
      const ids = state.mode === 'all' ? assignableVideos(videos).map((video) => video.videoId) : [...state.picked];
      ctl.note(`${ASSIGN_MODES[state.mode]} · ${selectionText(selectionOf(videos, ids))}`);
    }
    function paint() {
      hint.textContent = ASSIGN_MODE_HINTS[state.mode];
      spread.hidden = state.mode !== 'spread';
      const choosing = state.mode !== 'all';
      bar.node.hidden = !choosing;
      allSummary.hidden = choosing;
      rows.node.classList.toggle('ak-pl-readonly', !choosing);
      rows.paint();
      paintSpread();
      note();
    }
    paint();
    return { node, enter: note };
  };

  // ======================================================= 2. Kime?
  const stepWho = (ctl) => {
    target = bulkTargetField(app, { classes, groups, modes: ['pick'], exclude: false,
      value: initial ? { studentIds: initial.studentIds || [], students: initial.students || [] } : {},
      onChange: () => { ctl.clearError(); ctl.note(`Hedef: ${target.summary()}`); } });
    const node = h('div', 'ak-stack');
    node.append(h('div', 'ak-muted', 'Seçilenler birleştirilir; aynı öğrenciye liste bir kez düşer. Koç yalnız kendi '
      + 'öğrencilerine atayabilir (en çok 200 öğrenci).'), target.node);
    return { node, enter: () => ctl.note(`Hedef: ${target.summary()}`), destroy: () => target.destroy() };
  };

  // ======================================================= 3. Ne zaman?
  const stepWhen = (ctl) => {
    const node = h('div', 'ak-stack');
    const dates = h('div', 'ak-inline ak-pl-dates');
    const start = dateInput(state.startDate, (iso) => { state.startDate = iso || ''; changed(); }, 'Başlangıç günü');
    const due = dateInput(state.dueDate, (iso) => { state.dueDate = iso || ''; changed(); }, 'Son tarih');
    dates.append(h('span', 'ak-muted', 'Başlangıç günü'), start.node, h('span', 'ak-muted', 'Son tarih (isteğe bağlı)'), due.node);
    const quick = h('div', 'ak-row-actions');
    for (const [label, days] of [['1 hafta', 6], ['2 hafta', 13], ['1 ay', 29]]) {
      quick.append(button(label, { variant: 'ghost', onClick: () => {
        state.dueDate = addDays(state.startDate || todayIst(), days);
        due.set?.(state.dueDate);
        changed();
      } }));
    }
    const title = h('input', 'kit-input');
    title.maxLength = 120;
    title.placeholder = detail.title || 'Liste adı';
    title.setAttribute('aria-label', 'Görev başlığı (boşsa liste adı)');
    title.addEventListener('input', () => { state.title = title.value; });
    const instructions = h('textarea', 'kit-textarea');
    instructions.maxLength = 2000;
    instructions.rows = 2;
    instructions.placeholder = 'Talimat (isteğe bağlı) — ör. her videodan sonra 3 soru çöz';
    instructions.setAttribute('aria-label', 'Talimat');
    instructions.addEventListener('input', () => { state.instructions = instructions.value; });
    const skip = checkLine('Zaten atanmışları atla', state.skipDuplicates, (on) => { state.skipDuplicates = on; changed(); });
    const notify = checkLine('Öğrencilere tek bildirim gönder', state.notify, (on) => { state.notify = on; });

    const preview = h('div', 'ak-hwb-preview');
    const previewBody = h('div', 'ak-stack');
    previewBody.setAttribute('aria-live', 'polite');
    preview.append(h('div', 'ak-hwb-preview-title', 'Önizleme'), previewBody);
    node.append(dates, quick, title, instructions, skip, notify, preview);

    let token = 0;
    const run = async () => {
      const mine = ++token;
      const built = body();
      if (built.error) { previewBody.replaceChildren(h('div', 'ak-muted', built.error)); ctl.note(''); return; }
      if (!app.can('admin_previewPlaylistAssignment')) {
        previewBody.replaceChildren(h('div', 'ak-muted', 'Önizleme bu hesaba açık değil; “Ata” yine çalışır.'));
        return;
      }
      previewBody.replaceChildren(h('div', 'ak-muted', 'Hesaplanıyor…'));
      try {
        const result = await app.api.post(`${ASSIGNMENTS}/preview`, built.body);
        if (mine !== token) return;
        previewBody.replaceChildren(...previewNodes(result));
        ctl.note(previewHeadline(result));
      } catch (failure) {
        if (mine !== token) return;
        previewBody.replaceChildren(alertBox(problem(failure), 'bad'));
        ctl.note('');
      }
    };
    const changed = debounce(run, 450);
    return {
      node,
      enter: () => run(),
      destroy: () => { changed.cancel?.(); start.destroy?.(); due.destroy?.(); },
    };
  };

  return wizard(app, {
    title: `Listeyi ata — ${detail.title || 'Oynatma listesi'}`,
    steps: [
      { key: 'videos', label: 'Videolar', render: stepVideos, validate: () => {
        if (state.mode !== 'all' && !selectionOf(videos, [...state.picked]).count) {
          return 'En az bir video seçin (onay kutusu ya da "3–7" aralığı).';
        }
        return state.mode === 'spread' ? spreadOf(state).error || null : null;
      } },
      { key: 'who', label: 'Kime?', render: stepWho, validate: () => target?.problem() || null },
      { key: 'when', label: 'Ne zaman?', render: stepWhen, validate: () => body().error || null },
    ],
    finishLabel: 'Ata',
    onFinish: async ({ key }) => {
      const built = body();
      if (built.error) throw new Error(built.error);
      const created = await app.api.post(ASSIGNMENTS, built.body, { key });
      app.toast(`Liste atandı: ${num(created.studentCount)} öğrenci · ${num(created.videoCount)} video`
        + `${created.taskCount > 1 ? ` · ${num(created.taskCount)} gün` : ''}`
        + `${created.skippedDuplicateCount ? ` · ${num(created.skippedDuplicateCount)} yinelenen atlandı` : ''}.`, 'good');
      return created;
    },
  });
}

/** Önizleme gövdesi: başlık satırı, gün dağılımı, atlananlar, yinelenenler, uyarılar. */
export function previewNodes(result) {
  const out = [h('b', '', previewHeadline(result))];
  if (result.students?.length) {
    const names = result.students.map((student) => student.displayName).join(', ');
    out.push(h('small', 'ak-muted', result.studentCount > result.students.length
      ? `${names} ve ${num(result.studentCount - result.students.length)} öğrenci daha` : names));
  }
  if ((result.days || []).length > 1) {
    const list = h('div', 'ak-pl-days');
    for (const day of result.days.slice(0, 14)) list.append(h('span', 'ak-pl-day', dayLine(day)));
    if (result.days.length > 14) list.append(h('span', 'ak-muted', `+${num(result.days.length - 14)} gün`));
    out.push(list);
  }
  const skipped = skippedText(result.skippedVideos);
  if (skipped) out.push(h('small', 'ak-pl-warn', skipped));
  const duplicates = duplicateText(result);
  if (duplicates) {
    const box = h('div', 'ak-pl-dup');
    box.append(badge(duplicates, 'warn'));
    for (const row of (result.duplicates || []).slice(0, 5)) {
      box.append(h('small', 'ak-muted', `${row.student?.displayName || 'Öğrenci'}: ${num(row.videoIds.length)} video`));
    }
    out.push(box);
  }
  for (const warning of result.warnings || []) out.push(alertBox(ASSIGN_WARNINGS[warning] || warning, 'warn'));
  return out;
}
