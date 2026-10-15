// DÖNEM GEÇİŞİ sihirbazı — Öğrenciler › Sınıflar › "Dönem geçişi" (iş 37 / İ4).
//
// Yıl sonunun parça parça işi (dönem ata, toplu seviye, toplu pasifleştir, program
// kopyala) tek ekranda: kaynak dönemin canlı sınıfları listelenir, her sınıfa bir
// işlem seçilir, önizleme yazmadan ne olacağını gösterir, "Geçişi uygula" PIN'le tek
// seferde yapar, "Geri al" son geçişi geri çevirir. Kurallar backend'dedir
// (`backend/academic_year.py` → `YearRollover`); ekran yalnız gösterir ve seçtirir.
//
//   GET  /api/bbd_students/groups/rollover                 bbd_students.view
//   POST /api/bbd_students/groups/rollover/preview         bbd_students.groups
//   POST /api/bbd_students/groups/rollover        (PIN)    bbd_students.groups
//                                                          (+ lifecycle: mezun varsa)
//   POST /api/bbd_students/groups/rollover/{id}/undo (PIN) bbd_students.groups
//
// Ders programı ve sınıf müfredatı BBD Akademi'dedir: geçişten sonra Akademi → Ders
// programı → "Dönem geçişi kopyası" eski sınıfın programını/müfredatını yeni sınıfa
// aktarır (eşleme `latest.pairs`).
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { confirmWithPin } from '../../ui-kit/kit.js';
import { describe } from './curriculum.js';

const BASE = '/api/bbd_students/groups/rollover';

export const ACTION_LABEL = {
  promote: 'Üst seviyeye taşı',
  copy: 'Aynen kopyala',
  graduate: 'Mezun et',
  archive: 'Yalnız arşivle',
  keep: 'Dokunma',
};

const ACTION_HINT = {
  promote: 'Yeni dönemde bir üst seviyede sınıf açılır, öğrenciler taşınır; eski sınıf arşivlenir.',
  copy: 'Yeni dönemde aynı ad ve seviyeyle sınıf açılır, öğrenciler taşınır; eski sınıf arşivlenir.',
  graduate: 'Sınıf arşivlenir; öğrenciler “mezun” gerekçesiyle pasifleşir (kantin, tablet, uygulama girişi kapanır).',
  archive: 'Sınıf arşivlenir; öğrenciler etkin kalır (diğer sınıfları sürer).',
  keep: 'Sınıfa dokunulmaz.',
};

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

function button(label, variant, onClick) {
  const node = h('button', `st-btn${variant ? ` st-btn-${variant}` : ''}`, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

/** 2027-09-01 → 01.09.2027 */
export function trDay(iso) {
  const [y, m, d] = String(iso || '').slice(0, 10).split('-');
  return d && m && y ? `${d}.${m}.${y}` : '—';
}

/** Seviye kısa yazı ("8. sınıf · LGS"); yoksa "seviye yok". */
export function levelText(level) {
  return describe(level) || 'seviye yok';
}

/** Eşleme satırı: "7-A → 8-A (2027-2028)"; var olana katıldıysa belirtilir. */
export function pairText(pair) {
  return `${pair.fromName} → ${pair.toName} (${pair.toYear})${pair.joined ? ' · var olan sınıfa eklendi' : ''}`;
}

/** Ekranın gönderdiği plan gövdesi — yalnız elle değişen ad gider (yoksa öneri). */
export function planBody(state) {
  return {
    fromYear: state.fromYear || undefined,
    effectiveFrom: state.effectiveFrom || undefined,
    bumpOthers: state.bumpOthers,
    keepStudentIds: [...state.keep],
    items: [...state.items.entries()].map(([groupId, item]) => ({
      groupId, action: item.action, ...(item.nameEdited && item.name ? { name: item.name } : {}),
    })),
  };
}

/**
 * @param {HTMLElement} host
 * @param {{api, can:(p:string)=>boolean, students:()=>Array, dialogRoot, toast}} ctx
 * @param {{onClose?: () => void}} [options]
 * @returns {{reload, destroy}}
 */
export function mountYearRollover(host, ctx, { onClose } = {}) {
  const canEdit = ctx.can('bbd_students.groups');
  const canGraduate = ctx.can('bbd_students.lifecycle');
  const state = {
    overview: null, preview: null, fromYear: '', effectiveFrom: '', bumpOthers: true,
    items: new Map(), keep: new Set(), busy: false, token: 0,
  };
  let nameTimer = null;
  let alive = true;

  const view = h('div', 'gr yr');
  const head = h('header', 'st-bar yr-head');
  const title = h('h3', 'yr-title', 'Dönem geçişi');
  head.append(title, h('div', 'st-spacer'));
  if (onClose) head.append(button('← Sınıflara dön', '', () => onClose()));
  view.append(head);
  const body = h('div', 'st-detail-body yr-body');
  view.append(body);
  host.replaceChildren(view);

  body.append(h('p', 'st-hint',
    'Yıl sonunda sınıfları yeni döneme tek seferde geçirin: her sınıf için işlemi seçin, önizlemeyi '
    + 'okuyun, “Geçişi uygula” ile PIN’inizi girin. Uygulanan son geçiş “Geri al” ile geri çevrilir. '
    + 'Önizleme hiçbir şey yazmaz.'));
  const lastSlot = h('div');
  const settings = h('section', 'st-card');
  const classesCard = h('section', 'st-card');
  const summary = h('section', 'st-card');
  const graduatesCard = h('section', 'st-card');
  const levelsCard = h('section', 'st-card');
  const actions = h('div', 'gr-tools yr-actions');
  const error = h('p', 'st-lc-warn bad');
  error.setAttribute('role', 'alert');
  error.hidden = true;
  body.append(lastSlot, settings, classesCard, summary, graduatesCard, levelsCard, error, actions);

  const studentName = (id) => {
    const row = ctx.students().find((item) => item.kantinId === id);
    return row?.displayName || id;
  };

  // ------------------------------------------------------------ ayarlar
  const fromSelect = h('select', 'st-input st-select');
  fromSelect.setAttribute('aria-label', 'Kaynak dönem');
  fromSelect.addEventListener('change', () => {
    state.fromYear = fromSelect.value;
    state.items.clear();
    state.keep.clear();
    refresh();
  });
  const toText = h('b', 'yr-to', '');
  const dayInput = h('input', 'st-input gr-day');
  dayInput.type = 'date';
  dayInput.setAttribute('aria-label', 'Geçiş günü');
  dayInput.addEventListener('change', () => { state.effectiveFrom = dayInput.value; refresh(); });
  const bump = h('input', 'st-check');
  bump.type = 'checkbox';
  bump.id = `yr-bump-${Math.random().toString(36).slice(2, 8)}`;
  bump.checked = true;
  bump.addEventListener('change', () => { state.bumpOthers = bump.checked; refresh(); });
  const bumpLabel = h('label', 'st-check',
    'Seviyesi olmayan sınıflardaki öğrencilerin kendi seviyesini de bir artır');
  bumpLabel.htmlFor = bump.id;
  const bumpRow = h('div', 'st-check-row');
  bumpRow.append(bump, bumpLabel);
  const field = (label, input) => {
    const wrap = h('label', 'st-field');
    wrap.append(h('span', 'st-label', label), input);
    return wrap;
  };
  const toWrap = h('div', 'st-field');
  toWrap.append(h('span', 'st-label', 'Hedef dönem'), toText);
  const windowHint = h('p', 'st-hint');
  const grid = h('div', 'st-grid');
  grid.append(field('Kaynak dönem', fromSelect), toWrap, field('Geçiş günü (yeni üyeliklerin ilk günü)', dayInput));
  settings.append(h('h4', null, 'Dönemler'), grid, windowHint, bumpRow);

  // ------------------------------------------------------------ eylemler
  const refreshButton = button('Önizlemeyi yenile', '', () => refresh());
  const applyButton = button('Geçişi uygula', 'primary', () => apply());
  actions.append(refreshButton, h('div', 'st-spacer'), applyButton);
  if (!canEdit) {
    applyButton.disabled = true;
    refreshButton.disabled = true;
  }

  function showError(text) {
    error.textContent = text || '';
    error.hidden = !text;
  }

  // ------------------------------------------------------------ veri
  async function load() {
    showError('');
    try {
      state.overview = await ctx.api(BASE);
    } catch (failure) {
      if (!alive) return;
      lastSlot.replaceChildren(h('p', 'st-empty', `Dönem geçişi okunamadı: ${failure.message}`));
      return;
    }
    if (!alive) return;
    const data = state.overview;
    state.fromYear = data.fromYear;
    state.effectiveFrom = data.effectiveFrom;
    state.items.clear();
    state.keep.clear();
    const years = new Set([data.fromYear, ...(data.years || []).map((item) => item.academicYear)]);
    fromSelect.replaceChildren(...[...years].sort().reverse().map((year) => {
      const count = (data.years || []).find((item) => item.academicYear === year)?.classes;
      return new Option(count ? `${year} · ${count} canlı sınıf` : year, year);
    }));
    fromSelect.value = state.fromYear;
    dayInput.value = state.effectiveFrom;
    dayInput.min = data.window?.earliest || '';
    dayInput.max = data.window?.latest || '';
    renderLast();
    if (canEdit) {
      await refresh();
    } else {
      classesCard.replaceChildren(h('p', 'st-hint',
        'Dönem geçişi için sınıf yönetme yetkisi gerekir (bbd_students.groups).'));
    }
  }

  async function refresh() {
    if (!canEdit) return;
    const token = (state.token += 1);
    refreshButton.disabled = true;
    try {
      const preview = await ctx.api(`${BASE}/preview`, { method: 'POST', body: planBody(state) });
      if (!alive || token !== state.token) return;
      state.preview = preview;
      for (const entry of preview.classes || []) {
        const current = state.items.get(entry.groupId);
        state.items.set(entry.groupId, {
          action: entry.action,
          name: current?.nameEdited ? current.name : (entry.newName || entry.nameSuggested || ''),
          nameEdited: Boolean(current?.nameEdited),
        });
      }
      showError('');
      render();
    } catch (failure) {
      if (alive && token === state.token) showError(`Önizleme yapılamadı: ${failure.message}`);
    } finally {
      if (token === state.token) refreshButton.disabled = false;
    }
  }

  // ------------------------------------------------------------ çizim
  function renderLast() {
    const latest = state.overview?.latest;
    lastSlot.replaceChildren();
    if (!latest) return;
    const card = h('section', 'st-card yr-last');
    card.append(h('h4', null, 'Son dönem geçişi'));
    const counts = latest.counts || {};
    card.append(h('p', 'yr-line', [
      `${latest.fromYear} → ${latest.toYear}`,
      `geçiş günü ${trDay(latest.effectiveFrom)}`,
      `${counts.created ?? 0} yeni sınıf`,
      `${counts.moved ?? 0} öğrenci taşındı`,
      latest.graduation ? `${latest.graduation.deactivated + latest.graduation.scheduled} mezun` : null,
      `${latest.createdBy || '—'} · ${(latest.createdAt || '').replace('T', ' ').slice(0, 16)}`,
    ].filter(Boolean).join(' · ')));
    if ((latest.pairs || []).length) {
      const list = h('ul', 'yr-pairs');
      for (const pair of latest.pairs) list.append(h('li', null, pairText(pair)));
      card.append(list);
    }
    card.append(h('p', 'st-hint',
      'Ders programı ve sınıf müfredatı BBD Akademi’dedir: Akademi → Ders programı → '
      + '“Dönem geçişi kopyası” eski sınıfların programını ve müfredatını yeni sınıflara aktarır.'));
    if (latest.canUndo && canEdit) {
      card.append(button('Bu geçişi geri al', 'danger', () => undo(latest)));
    }
    lastSlot.append(card);
  }

  function render() {
    const plan = state.preview;
    toText.textContent = plan ? plan.toYear : '—';
    windowHint.textContent = plan
      ? `Eski üyelikler ${trDay(plan.closeDay)} günü kapanır, yeni üyelikler ${trDay(plan.effectiveFrom)} `
        + 'günü başlar. Eski sınıflar uygulandığı an arşivlenir; eski dönemin dersleri bittikten sonra uygulayın.'
      : '';
    renderClasses(plan);
    renderSummary(plan);
    renderGraduates(plan);
    renderLevels(plan);
    applyButton.disabled = !canEdit || !plan || Boolean(plan.blocked) || state.busy;
    applyButton.title = plan?.blocked || '';
  }

  function renderClasses(plan) {
    classesCard.replaceChildren(h('h4', null, `${plan?.fromYear || ''} sınıfları (${plan?.classes?.length || 0})`));
    if (!plan || !(plan.classes || []).length) {
      classesCard.append(h('p', 'st-hint', 'Bu dönemde canlı sınıf yok.'));
      return;
    }
    classesCard.append(h('p', 'st-hint',
      'Varsayılan: seviyeli sınıflar üst seviyeye, 8., 12. sınıf ve mezun sınıfları “Mezun et”, '
      + 'seviyesiz ve özel ders sınıfları “Yalnız arşivle”. Yeni ad yeni dönemde zaten açılmış bir '
      + 'sınıfa denk gelirse öğrenciler o sınıfa eklenir.'));
    const problems = new Map();
    for (const item of plan.problems || []) {
      if (item.groupId) problems.set(item.groupId, [...(problems.get(item.groupId) || []), item.message]);
    }
    const table = h('div', 'yr-table');
    const header = h('div', 'yr-row yr-row-head');
    for (const text of ['Sınıf', 'İşlem', 'Yeni dönemde', 'Sonuç']) header.append(h('span', null, text));
    table.append(header);
    for (const entry of plan.classes) {
      const item = state.items.get(entry.groupId) || { action: entry.action, name: entry.newName || '' };
      const row = h('div', 'yr-row');
      const name = h('div', 'yr-cell');
      name.append(h('b', null, entry.name), h('span', 'st-item-meta',
        [levelText(entry.classLevel), entry.kind === 'individual' ? 'özel ders' : null,
          `${entry.memberCount} öğrenci`].filter(Boolean).join(' · ')));
      const select = h('select', 'st-input st-select');
      select.setAttribute('aria-label', `${entry.name} için işlem`);
      for (const [value, label] of Object.entries(ACTION_LABEL)) {
        const option = new Option(label, value);
        if (value === 'promote' && !entry.classLevel) option.disabled = true;
        select.append(option);
      }
      select.value = item.action;
      select.title = ACTION_HINT[item.action] || '';
      select.addEventListener('change', () => {
        state.items.set(entry.groupId, { action: select.value, name: '', nameEdited: false });
        refresh();
      });
      const target = h('div', 'yr-cell');
      if (entry.action === 'promote' || entry.action === 'copy') {
        const input = h('input', 'st-input');
        input.maxLength = 60;
        input.value = item.name || entry.newName || '';
        input.setAttribute('aria-label', `${entry.name} için yeni sınıf adı`);
        input.addEventListener('input', () => {
          state.items.set(entry.groupId, { action: select.value, name: input.value.trim(), nameEdited: true });
          clearTimeout(nameTimer);
          nameTimer = setTimeout(refresh, 400);
        });
        target.append(input, h('span', 'st-item-meta', [
          levelText(entry.newLevel),
          entry.target === 'join' ? 'var olan sınıfa eklenir' : 'yeni sınıf',
        ].join(' · ')));
      } else {
        target.append(h('span', 'st-item-meta', '—'));
      }
      const result = h('div', 'yr-cell');
      const outcome = {
        promote: `${entry.moving} öğrenci taşınır`,
        copy: `${entry.moving} öğrenci taşınır`,
        graduate: `${entry.leaving} öğrenci pasifleşir`,
        archive: 'arşivlenir',
        keep: 'değişmez',
      }[entry.action];
      result.append(h('span', null, outcome));
      if (entry.leaving && entry.action !== 'graduate') {
        result.append(h('span', 'st-item-meta', `${entry.leaving} mezun taşınmaz`));
      }
      for (const message of problems.get(entry.groupId) || []) result.append(h('span', 'yr-bad', message));
      row.append(name, select, target, result);
      table.append(row);
    }
    classesCard.append(table);
  }

  function renderSummary(plan) {
    summary.replaceChildren(h('h4', null, 'Önizleme'));
    if (!plan) return;
    const counts = plan.counts || {};
    const tiles = h('div', 'st-kpi yr-kpi');
    for (const [label, value] of [
      ['Yeni sınıf', counts.created], ['Var olana eklenen', counts.joined],
      ['Arşivlenecek', counts.archived], ['Taşınan öğrenci', counts.moved],
      ['Mezun (pasifleşir)', counts.graduates], ['Seviyesi değişen', counts.levelChanges],
    ]) {
      const tile = h('div', 'st-kpi-tile');
      tile.append(h('span', 'st-kpi-label', label), h('span', 'st-kpi-value', String(value ?? 0)));
      tiles.append(tile);
    }
    summary.append(tiles);
    for (const item of (plan.problems || []).filter((problem) => !problem.groupId)) {
      summary.append(h('p', 'st-lc-warn bad', item.message));
    }
    if (plan.blocked) summary.append(h('p', 'st-lc-warn', plan.blocked));
    for (const note of plan.notes || []) summary.append(h('p', 'st-hint', note));
    if (counts.graduates && !canGraduate) {
      summary.append(h('p', 'st-lc-warn bad', 'Mezunları pasifleştirmek için öğrenci pasifleştirme '
        + 'yetkisi gerekir; o sınıfları “Yalnız arşivle”ye alın.'));
    }
  }

  function renderGraduates(plan) {
    const list = [...(plan?.graduates || []), ...(plan?.keptGraduates || [])]
      .sort((a, b) => studentName(a.studentId).localeCompare(studentName(b.studentId), 'tr'));
    graduatesCard.hidden = list.length === 0;
    graduatesCard.replaceChildren(h('h4', null, `Mezunlar (${plan?.graduates?.length || 0} pasifleşir)`));
    if (!list.length) return;
    graduatesCard.append(h('p', 'st-hint',
      'İşaretli öğrenciler “mezun” gerekçesiyle pasifleşir: kantin, tablet ve BBD Akademi girişi kapanır '
      + '(geçiş günü ileride ise o gün). Kalacak öğrencinin (ör. yeniden hazırlanacak mezun) işaretini kaldırın; '
      + 'sınıfı yine arşivlenir, öğrenci etkin kalır.'));
    const box = h('div', 'gr-members');
    for (const item of list) {
      const row = h('label', 'gr-member');
      const check = h('input', 'st-check');
      check.type = 'checkbox';
      check.checked = !state.keep.has(item.studentId);
      check.setAttribute('aria-label', `${studentName(item.studentId)} pasifleşsin`);
      check.addEventListener('change', () => {
        if (check.checked) state.keep.delete(item.studentId); else state.keep.add(item.studentId);
        refresh();
      });
      row.append(check, h('b', null, studentName(item.studentId)),
        h('span', 'st-item-meta', (item.classes || []).join(', ')));
      box.append(row);
    }
    graduatesCard.append(box);
  }

  function renderLevels(plan) {
    const changes = plan?.levelChanges || [];
    levelsCard.hidden = changes.length === 0;
    levelsCard.replaceChildren(h('h4', null, `Seviyesi değişecek öğrenciler (${changes.length})`));
    if (!changes.length) return;
    levelsCard.append(h('p', 'st-hint',
      'BBD Akademi varsayılan müfredatı yeni seviye, alan ve hedef sınavdan türetir. “Alanını ve hedef '
      + 'sınavını koru” işaretli öğrencide yalnız seviye değişir.'));
    const box = h('div', 'gr-members');
    for (const change of changes.slice(0, 40)) {
      const row = h('div', 'gr-history-row');
      row.append(h('b', null, studentName(change.studentId)),
        h('span', null, `${levelText(change.before)} → ${levelText(change.after)}`),
        h('span', 'st-item-meta', change.source === 'class' ? 'yeni sınıftan' : 'kendi seviyesi +1'));
      box.append(row);
    }
    levelsCard.append(box);
    if (changes.length > 40) levelsCard.append(h('p', 'st-hint', `… ve ${changes.length - 40} öğrenci daha.`));
  }

  // ------------------------------------------------------------ yazma
  async function apply() {
    const plan = state.preview;
    if (!plan || plan.blocked || state.busy) return;
    const counts = plan.counts || {};
    const pin = await confirmWithPin(ctx.dialogRoot, {
      title: `${plan.fromYear} → ${plan.toYear} geçişi uygulansın mı?`,
      description: [
        `${counts.created} yeni sınıf açılır${counts.joined ? `, ${counts.joined} sınıfa öğrenci eklenir` : ''}; `
          + `${counts.archived} sınıf arşivlenir.`,
        `${counts.moved} öğrenci ${trDay(plan.effectiveFrom)} gününden yeni sınıfında.`,
        counts.graduates ? `${counts.graduates} mezun pasifleşir.` : '',
        counts.levelChanges ? `${counts.levelChanges} öğrencinin seviyesi değişir.` : '',
        'Son geçiş “Geri al” ile geri çevrilebilir. Devam etmek için PIN’inizi girin.',
      ].filter(Boolean).join('\n'),
      confirmLabel: 'Geçişi uygula',
    });
    if (!pin) return;
    state.busy = true;
    applyButton.disabled = true;
    try {
      const result = await ctx.api(BASE, { method: 'POST',
        body: { ...planBody(state), expectedRevision: plan.revision, pin } });
      const graduates = result.graduation
        ? result.graduation.deactivated + result.graduation.scheduled : 0;
      ctx.toast(`Dönem geçişi uygulandı: ${result.counts?.created ?? 0} yeni sınıf`
        + `${graduates ? ` · ${graduates} mezun` : ''}. Ders programları için Akademi → Ders programı → `
        + '“Dönem geçişi kopyası”.');
      ctx.onChanged?.();
      await load();
    } catch (failure) {
      showError(`Uygulanamadı: ${failure.message}`);
    } finally {
      state.busy = false;
      render();
    }
  }

  async function undo(latest) {
    const pin = await confirmWithPin(ctx.dialogRoot, {
      title: `${latest.fromYear} → ${latest.toYear} geçişi geri alınsın mı?`,
      description: 'Geçişin açtığı sınıflar kalıcı silinir (sonradan eklenen üyelikleri ve Akademi’de '
        + 'kopyalanan programları dahil), eski sınıflar ve üyelikler geri açılır, seviyeler eski haline '
        + 'döner, geçişin pasifleştirdiği mezunlar yeniden etkinleşir. Devam etmek için PIN’inizi girin.',
      confirmLabel: 'Geri al',
    });
    if (!pin) return;
    try {
      const result = await ctx.api(`${BASE}/${encodeURIComponent(latest.id)}/undo`,
        { method: 'POST', body: { pin } });
      const done = result.undo || {};
      ctx.toast([`Geçiş geri alındı: ${done.deletedClasses ?? 0} sınıf silindi, `
        + `${done.restoredClasses ?? 0} sınıf geri açıldı.`, ...(done.notes || [])].join(' '));
      ctx.onChanged?.();
      await load();
    } catch (failure) {
      showError(`Geri alınamadı: ${failure.message}`);
    }
  }

  load();
  return {
    reload: load,
    destroy() {
      alive = false;
      clearTimeout(nameTimer);
      host.replaceChildren();
    },
  };
}
