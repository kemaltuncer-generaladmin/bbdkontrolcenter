// Öğrenci grupları — "Gruplar" sekmesi ve öğrenci kartındaki "Gruplar" bölümü.
//
// GRUBUN TEK SAHİBİ BU MODÜLDÜR (sözleşme km-ogrenci-gruplari-ve-zil.md §2).
// BBD Akademi grubu buradan çeker; zil sistemi elle çağrı listesini buradan
// okur. Grup = ad + öğrenci seçimi; üyelik TARİHLİDİR (başlangıç ve son gün
// dahil). Kural backend'dedir (`backend/groups.py`); ekran yalnız gösterir.
//
//   GET   /api/bbd_students/groups?archived=&kind=&q=          bbd_students.view
//   GET   /api/bbd_students/groups/{id}?date=                  bbd_students.view
//   GET   /api/bbd_students/groups/{id}/history                bbd_students.view
//   POST  /api/bbd_students/groups                             bbd_students.groups
//   POST  /api/bbd_students/groups/from-class                  bbd_students.groups
//   PUT   /api/bbd_students/groups/{id}                        bbd_students.groups
//   POST  /api/bbd_students/groups/{id}/archive | /restore     bbd_students.groups
//   POST  /api/bbd_students/groups/{id}/members                bbd_students.groups
//   POST  /api/bbd_students/groups/{id}/members/end            bbd_students.groups
//   POST  /api/bbd_students/groups/move                        bbd_students.groups
//   PATCH /api/bbd_students/groups/{id}/members/{memberId}     bbd_students.groups
//   GET   /api/bbd_students/students/{id}/groups               bbd_students.view
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { createPicker } from '../../ui-kit/picker.js';
import { confirmSimple, dialogA11y, todayIso } from '../../ui-kit/kit.js';

const BASE = '/api/bbd_students/groups';

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const KIND_LABEL = { group: 'Toplu ders', individual: 'Özel ders' };

/** 2026-10-12 → 12.10.2026; boş → "açık". */
export function trDay(iso, open = 'açık') {
  if (!iso) return open;
  const [y, m, d] = String(iso).split('-');
  return d && m && y ? `${d}.${m}.${y}` : String(iso);
}

/** Bir üyelik satırı o gün geçerli mi? Uçlar dahil. */
export function validOn(member, iso) {
  return member.validFrom <= iso && (!member.validTo || iso <= member.validTo);
}

/** Tarih kutusu: `<input type="date">` — değer YYYY-AA-GG. */
function dayInput(value, label = 'Gün') {
  const input = h('input', 'st-input gr-day');
  input.type = 'date';
  input.setAttribute('aria-label', label);
  input.value = value || todayIso();
  return input;
}

function button(label, variant, onClick) {
  const node = h('button', `st-btn${variant ? ` st-btn-${variant}` : ''}`, label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

/**
 * "Gruplar" sekmesi.
 * @param {HTMLElement} host
 * @param {{api, can:(p:string)=>boolean, students:()=>Array, dialogRoot, toast}} ctx
 */
export function mountGroups(host, ctx) {
  const state = {
    archived: 'live', kind: '', query: '', groups: [], selected: null, detail: null,
  };
  const canEdit = ctx.can('bbd_students.groups');
  let picker = null;
  let searchTimer = null;

  const view = h('div', 'gr');
  const bar = h('header', 'st-bar');
  const search = h('input', 'st-search');
  search.type = 'search';
  search.placeholder = 'Grup ya da anons adı ara';
  search.setAttribute('aria-label', 'Grup ya da anons adı ara');
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = search.value.trim(); load(); }, 200);
  });
  const kindFilter = h('select', 'st-input st-select');
  kindFilter.setAttribute('aria-label', 'Grup türü');
  for (const [value, label] of [['', 'Tüm türler'], ['group', 'Toplu ders'], ['individual', 'Özel ders']]) {
    kindFilter.append(new Option(label, value));
  }
  kindFilter.addEventListener('change', () => { state.kind = kindFilter.value; load(); });
  const archFilter = h('select', 'st-input st-select');
  archFilter.setAttribute('aria-label', 'Arşiv durumu');
  for (const [value, label] of [['live', 'Canlı gruplar'], ['archived', 'Arşiv'], ['all', 'Tümü']]) {
    archFilter.append(new Option(label, value));
  }
  archFilter.addEventListener('change', () => { state.archived = archFilter.value; load(); });
  const count = h('span', 'st-count');
  bar.append(search, kindFilter, archFilter, count, h('div', 'st-spacer'));
  if (canEdit) {
    bar.append(button('Sınıftan grup oluştur', '', fromClass));
    bar.append(button('+ Yeni grup', 'primary', () => openEditor(null)));
  }
  view.append(bar);

  view.append(h('p', 'st-hint',
    'Gruplar Kontrol Merkezi’nde yönetilir; BBD Akademi ders programı ve yoklama, zil '
    + 'sistemi grup çağrısı buradan okur. Üyelik tarihlidir: son gün dahildir. '
    + 'Silme yoktur, arşiv vardır — geçmiş yoklama korunur.'));

  const split = h('div', 'st-split');
  const list = h('nav', 'st-list gr-list');
  const detail = h('section', 'st-detail gr-detail');
  split.append(list, detail);
  view.append(split);
  host.replaceChildren(view);

  async function load() {
    const params = new URLSearchParams({ archived: state.archived });
    if (state.kind) params.set('kind', state.kind);
    if (state.query) params.set('q', state.query);
    try {
      const payload = await ctx.api(`${BASE}?${params}`);
      state.groups = payload.groups || [];
    } catch (error) {
      list.replaceChildren(h('p', 'st-empty', `Gruplar okunamadı: ${error.message}`));
      return;
    }
    renderList();
    if (state.selected) await select(state.selected);
  }

  function renderList() {
    count.textContent = `${state.groups.length} grup`;
    list.replaceChildren();
    if (state.groups.length === 0) {
      list.append(h('p', 'st-empty', 'Grup yok. “+ Yeni grup” ile ad ve öğrenci seçerek açın.'));
      return;
    }
    for (const group of state.groups) {
      const item = h('button', 'st-item');
      item.type = 'button';
      item.classList.toggle('active', group.id === state.selected);
      const main = h('span', 'st-item-main');
      main.append(h('b', null, group.name));
      main.append(h('span', 'st-item-meta',
        [group.callName && `anons: ${group.callName}`, `${group.memberCount} üye bugün`]
          .filter(Boolean).join(' · ')));
      item.append(main);
      item.append(h('i', `st-badge gr-kind gr-kind-${group.kind}`, KIND_LABEL[group.kind] || group.kind));
      if (group.archived) item.append(h('i', 'st-badge', 'arşiv'));
      item.addEventListener('click', () => select(group.id));
      list.append(item);
    }
  }

  async function select(id) {
    state.selected = id;
    for (const node of list.querySelectorAll('.st-item')) node.classList.remove('active');
    renderList();
    try {
      state.detail = await ctx.api(`${BASE}/${encodeURIComponent(id)}`);
    } catch (error) {
      detail.replaceChildren(h('p', 'st-empty', `Grup okunamadı: ${error.message}`));
      return;
    }
    renderDetail();
  }

  function studentName(id) {
    const student = ctx.students().find((row) => row.kantinId === id);
    return student ? (student.displayName || id) : id;
  }

  function studentClass(id) {
    return ctx.students().find((row) => row.kantinId === id)?.className || '';
  }

  function renderDetail() {
    picker?.destroy?.();
    picker = null;
    const group = state.detail;
    detail.replaceChildren();
    if (!group) {
      detail.append(h('p', 'st-empty', 'Soldan bir grup seçin.'));
      return;
    }

    const head = h('header', 'st-detail-head');
    head.append(h('h3', null, group.name));
    head.append(h('span', 'st-subtitle', [
      KIND_LABEL[group.kind] || group.kind,
      group.callName ? `anons adı: ${group.callName}` : 'anons adı yok (ad okunur)',
      group.archived ? `arşiv · ${(group.archivedAt || '').slice(0, 10)}` : 'canlı',
    ].join(' · ')));
    head.append(h('div', 'st-spacer'));
    if (canEdit) {
      head.append(button('Düzenle', '', () => openEditor(group)));
      head.append(group.archived
        ? button('Arşivden al', '', () => post(`${encodeURIComponent(group.id)}/restore`, {}, 'Arşivden alındı.'))
        : button('Arşivle', 'danger', archive));
    }
    detail.append(head);

    const body = h('div', 'st-detail-body');
    detail.append(body);

    // ---------------------------------------------------- bugünkü üyeler
    const current = h('section', 'st-card');
    current.append(h('h4', null, `Bugünkü üyeler (${group.current.length})`));
    const checks = new Map();
    if (group.current.length === 0) {
      current.append(h('p', 'st-hint', 'Bugün geçerli üye yok.'));
    } else {
      const table = h('div', 'gr-members');
      for (const member of group.current) {
        const row = h('label', 'gr-member');
        const check = h('input', 'st-check');
        check.type = 'checkbox';
        check.setAttribute('aria-label', `${studentName(member.studentId)} seç`);
        check.disabled = !canEdit;
        checks.set(member.studentId, check);
        row.append(check, h('b', null, studentName(member.studentId)),
          h('span', 'st-item-meta', [studentClass(member.studentId),
            `${trDay(member.validFrom)} → ${trDay(member.validTo)}`].filter(Boolean).join(' · ')));
        table.append(row);
      }
      current.append(table);
    }
    if (canEdit && !group.archived && group.current.length > 0) {
      const tools = h('div', 'gr-tools');
      const endDay = dayInput(todayIso(), 'Seçilenler için gün');
      const reason = h('input', 'st-input');
      reason.placeholder = 'Gerekçe (geçmiş tarihte zorunlu)';
      reason.setAttribute('aria-label', 'Gerekçe (geçmiş tarihte zorunlu)');
      reason.maxLength = 200;
      const target = h('select', 'st-input st-select');
      target.setAttribute('aria-label', 'Taşınacak grup');
      target.append(new Option('Taşınacak grup…', ''));
      for (const other of state.groups) {
        if (other.id !== group.id && !other.archived) target.append(new Option(other.name, other.id));
      }
      const chosen = () => [...checks].filter(([, box]) => box.checked).map(([id]) => id);
      tools.append(h('span', 'st-label', 'Seçilenler için gün'), endDay, reason,
        button('Çıkar (son gün)', '', async () => {
          const ids = chosen();
          if (ids.length === 0) { ctx.toast('Önce öğrenci seçin.'); return; }
          await post(`${encodeURIComponent(group.id)}/members/end`,
            { studentIds: ids, validTo: endDay.value, reason: reason.value },
            `${ids.length} öğrencinin son günü ${trDay(endDay.value)}.`);
        }),
        target,
        button('Taşı (bu günden)', '', async () => {
          const ids = chosen();
          if (ids.length === 0 || !target.value) { ctx.toast('Öğrenci ve hedef grup seçin.'); return; }
          await call('/move', {
            studentIds: ids, fromGroupId: group.id, toGroupId: target.value,
            effectiveFrom: endDay.value, reason: reason.value,
          }, `${ids.length} öğrenci taşındı: önceki grupta son gün ${trDay(dayBefore(endDay.value))}.`);
        }));
      current.append(tools);
      current.append(h('p', 'st-hint',
        '“Çıkar”: seçilen gün öğrencinin bu gruptaki SON günüdür (dahil). '
        + '“Taşı”: bu gruptaki son gün bir önceki gün olur, yeni grupta seçilen gün başlar.'));
    }
    body.append(current);

    // --------------------------------------------------------- üye ekle
    if (canEdit && !group.archived) {
      const add = h('section', 'st-card');
      add.append(h('h4', null, 'Öğrenci ekle'));
      if (group.kind === 'individual') {
        add.append(h('p', 'st-hint', 'Özel ders grubunda aynı gün yalnız bir öğrenci olur.'));
      }
      picker = createPicker({
        groupLabel: 'Sınıf',
        placeholder: 'Ad, sınıf, no ara',
        single: group.kind === 'individual',
        items: ctx.students()
          .filter((student) => !group.current.some((m) => m.studentId === student.kantinId))
          .map((student) => ({
            id: student.kantinId,
            name: student.displayName || 'Adsız',
            group: student.className || 'Sınıf girilmemiş',
            meta: [student.gradeLevel && `${student.gradeLevel}. sınıf`, student.track,
              student.schoolNo && `No ${student.schoolNo}`].filter(Boolean).join(' · '),
          })),
      });
      add.append(picker.node);
      const from = dayInput(todayIso(), 'Başlangıç günü');
      const reason = h('input', 'st-input');
      reason.placeholder = 'Gerekçe (geçmiş tarihte zorunlu)';
      reason.setAttribute('aria-label', 'Gerekçe (geçmiş tarihte zorunlu)');
      reason.maxLength = 200;
      const addTools = h('div', 'gr-tools');
      add.append(addTools);
      addTools.append(h('span', 'st-label', 'Başlangıç günü'), from, reason,
        button('Seçilenleri ekle', 'primary', async () => {
          const ids = picker.selection();
          if (ids.length === 0) { ctx.toast('Önce öğrenci seçin.'); return; }
          const result = await post(`${encodeURIComponent(group.id)}/members`,
            { studentIds: ids, validFrom: from.value, reason: reason.value }, null);
          if (result) {
            ctx.toast(result.skipped?.length
              ? `${result.added} eklendi · ${result.skipped.length} zaten üyeydi.`
              : `${result.added} öğrenci eklendi.`);
          }
        }));
      body.append(add);
    }

    // ------------------------------------------------- üyelik geçmişi
    const past = h('section', 'st-card');
    past.append(h('h4', null, `Üyelik geçmişi (${group.members.length} satır)`));
    past.append(h('p', 'st-hint',
      'Bütün satırlar, geçmiş dahil. Yanlış girilmiş başlangıç ya da son gün düzeltilebilir; '
      + 'geçmişe dokunan düzeltme gerekçe ister. Akademi’de donmuş yoklama listeleri değişmez.'));
    const rows = [...group.members].sort((a, b) => (b.validFrom || '').localeCompare(a.validFrom || ''));
    for (const member of rows) {
      const line = h('div', 'gr-history-row');
      line.append(h('b', null, studentName(member.studentId)),
        h('span', null, `${trDay(member.validFrom)} → ${trDay(member.validTo)}`),
        h('span', 'st-item-meta', member.reason || ''));
      if (canEdit) line.append(button('Düzelt', 'ghost', () => fixMember(group, member)));
      past.append(line);
    }
    body.append(past);

    // ------------------------------------------------------ denetim izi
    const audit = h('section', 'st-card');
    audit.append(h('h4', null, 'Değişiklik kaydı'));
    const auditList = h('div', 'gr-audit');
    audit.append(auditList);
    body.append(audit);
    ctx.api(`${BASE}/${encodeURIComponent(group.id)}/history`).then((payload) => {
      auditList.replaceChildren(...(payload.items || []).slice(0, 30).map((item) => h('p', 'st-hint',
        `${(item.at || '').replace('T', ' ').slice(0, 16)} · ${item.actor || '—'} · ${actionLabel(item.action)}`
        + `${item.detail?.reason ? ` · ${item.detail.reason}` : ''}`)));
    }).catch(() => auditList.replaceChildren(h('p', 'st-hint', 'Kayıt okunamadı.')));
  }

  function dayBefore(iso) {
    const date = new Date(`${iso}T12:00:00`);
    date.setDate(date.getDate() - 1);
    return date.toISOString().slice(0, 10);
  }

  async function call(path, body, message, method = 'POST') {
    try {
      const result = await ctx.api(`${BASE}${path}`, { method, body });
      if (message) ctx.toast(message);
      await load();
      return result;
    } catch (error) {
      ctx.toast(`Olmadı: ${error.message}`);
      return null;
    }
  }

  const post = (path, body, message) => call(`/${path}`, body, message);

  async function archive() {
    const group = state.detail;
    if (!await confirmSimple(ctx.dialogRoot, {
      title: `“${group.name}” arşivlensin mi?`,
      description: 'Açık üyelikler bugün kapanır; henüz başlamamış üyelikler kaldırılır. '
        + 'Geçmiş yoklama ve raporlar korunur. Arşivden geri alınabilir.',
      confirmLabel: 'Arşivle',
      danger: true,
    })) return;
    await post(`${encodeURIComponent(group.id)}/archive`, {}, 'Grup arşivlendi.');
  }

  async function fixMember(group, member) {
    const from = window.prompt('Başlangıç günü (YYYY-AA-GG):', member.validFrom);
    if (from === null) return;
    const to = window.prompt('Son gün (YYYY-AA-GG; boş = açık uç):', member.validTo || '');
    if (to === null) return;
    const reason = window.prompt('Gerekçe (geçmişe dokunan düzeltmede zorunlu):', '') || '';
    await call(`/${encodeURIComponent(group.id)}/members/${encodeURIComponent(member.id)}`,
      { validFrom: from.trim(), validTo: to.trim(), reason }, 'Üyelik düzeltildi.', 'PATCH');
  }

  function openEditor(group) {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog gr-editor');
    card.setAttribute('role', 'dialog');
    card.append(h('h3', 'kit-dialog-title', group ? 'Grubu düzenle' : 'Yeni grup'));
    const field = (label, input) => {
      const wrap = h('label', 'st-field');
      wrap.append(h('span', 'st-label', label), input);
      return wrap;
    };
    const name = h('input', 'st-input');
    name.maxLength = 60;
    name.value = group?.name || '';
    const callName = h('input', 'st-input');
    callName.maxLength = 80;
    callName.placeholder = 'ör. On iki sayısal grubu — boşsa ad okunur';
    callName.value = group?.callName || '';
    const kind = h('select', 'st-input st-select');
    kind.append(new Option('Toplu ders (çoğul hitap)', 'group'),
      new Option('Özel ders — tek öğrenci (tekil hitap)', 'individual'));
    kind.value = group?.kind || 'group';
    const note = h('textarea', 'st-input st-textarea');
    note.maxLength = 500;
    note.value = group?.note || '';
    card.append(field('Grup adı', name), field('Anons adı (TTS için)', callName),
      field('Tür', kind), field('Not', note));
    if (!group) {
      card.append(h('p', 'st-hint',
        'Öğrencileri grubu açtıktan sonra “Öğrenci ekle” bölümünden seçin.'));
    }
    const error = h('p', 'kit-dialog-error');
    card.append(error);
    error.setAttribute('role', 'alert');
    let release = () => {};
    const close = () => { overlay.remove(); release(); };
    const save = button(group ? 'Kaydet' : 'Grubu aç', 'primary', async () => {
      const body = { name: name.value, callName: callName.value, kind: kind.value, note: note.value };
      try {
        const result = group
          ? await ctx.api(`${BASE}/${encodeURIComponent(group.id)}`, { method: 'PUT', body })
          : await ctx.api(BASE, { method: 'POST', body });
        close();
        state.selected = result.id;
        ctx.toast(group ? 'Grup kaydedildi.' : 'Grup açıldı.');
        await load();
      } catch (failure) {
        error.textContent = failure.message;
      }
    });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', '', close), save);
    card.append(actions);
    overlay.append(card);
    ctx.dialogRoot.append(overlay);
    release = dialogA11y(card);
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { event.stopPropagation(); close(); }
    });
    name.focus();
  }

  async function fromClass() {
    const classes = [...new Set(ctx.students().map((s) => s.className).filter(Boolean))].sort();
    const className = window.prompt(`Sınıf adı (${classes.slice(0, 12).join(', ')}${classes.length > 12 ? '…' : ''}):`);
    if (!className) return;
    const name = window.prompt('Grup adı:', className.trim());
    if (!name) return;
    const result = await call('/from-class', { className: className.trim(), name: name.trim() },
      'Sınıfın bugünkü öğrencileri yeni gruba eklendi (kopya; sonra bağımsızdır).');
    if (result) { state.selected = result.id; await load(); }
  }

  load();
  return {
    reload: load,
    destroy() {
      clearTimeout(searchTimer);
      picker?.destroy?.();
      host.replaceChildren();
    },
  };
}

function actionLabel(action) {
  return {
    create: 'grup açıldı', import: 'zil grubundan taşındı', from_class: 'sınıftan oluşturuldu',
    update: 'düzenlendi', archive: 'arşivlendi', restore: 'arşivden alındı',
    members_add: 'üye eklendi', members_end: 'üyelik bitirildi', move_out: 'başka gruba taşındı',
    move_in: 'başka gruptan taşındı', member_fix: 'üyelik düzeltildi',
  }[action] || action;
}

/**
 * Öğrenci kartındaki "Gruplar" bölümü — geçmişle.
 * @returns {{destroy}}
 */
export function mountStudentGroups(host, { api, studentId }) {
  let alive = true;
  host.replaceChildren(h('p', 'st-hint', 'Gruplar okunuyor…'));
  api(`/api/bbd_students/students/${encodeURIComponent(studentId)}/groups`).then((payload) => {
    if (!alive) return;
    const items = payload.items || [];
    if (items.length === 0) {
      host.replaceChildren(h('p', 'st-hint', 'Öğrenci hiçbir grupta değil. Gruplar sekmesinden eklenir.'));
      return;
    }
    const today = todayIso();
    const rows = items.map((item) => {
      const row = h('div', 'gr-history-row');
      row.classList.toggle('gr-past', !validOn(item, today));
      row.append(h('b', null, item.group.name),
        h('i', `st-badge gr-kind gr-kind-${item.group.kind}`, KIND_LABEL[item.group.kind] || item.group.kind),
        h('span', null, `${trDay(item.validFrom)} → ${trDay(item.validTo)}`),
        h('span', 'st-item-meta', [item.group.archived && 'arşiv', item.reason].filter(Boolean).join(' · ')));
      return row;
    });
    host.replaceChildren(...rows);
  }).catch((error) => {
    if (alive) host.replaceChildren(h('p', 'st-hint', `Gruplar okunamadı: ${error.message}`));
  });
  return { destroy() { alive = false; } };
}
