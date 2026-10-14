// SINIFLAR — tek liste (SINIF = GRUP, iş 23, 2026-10-14) ve öğrenci kartındaki
// "Sınıfları" bölümü.
//
// Kullanıcı: "sınıf=grup bak öğrenci yönetiminde burası çok karışıyor." Eski
// "Gruplar" ve "Sınıflar" sekmeleri TEK "Sınıflar" ekranıdır; bir öğrenci birden
// çok sınıfa EŞİT üye olabilir (ana sınıf yok). Öğrencinin eski `className`'i
// birleştirmeyle sınıf kaydına dönüştü (göç 009). Sınıfın TEK SAHİBİ BU
// MODÜLDÜR (sözleşme km-ogrenci-gruplari-ve-zil.md §2, §2.6): BBD Akademi
// buradan çeker, zil elle çağrı listesini buradan okur. Üyelik TARİHLİDİR
// (başlangıç ve son gün dahil). Sınıfın seviyesi (isteğe bağlı) ŞABLONDUR:
// öğrenci eklenince ve "Seviyeyi üyelere uygula" ile öğrencinin kendi
// seviyesine yazılır. Kural backend'dedir (`backend/groups.py`); ekran yalnız
// gösterir. Kod/yol adları ("groups") kaldı; arayüzde "grup" kelimesi yoktur.
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
//   POST  /api/bbd_students/groups/{id}/apply-level            bbd_students.groups
//   GET   /api/bbd_students/students/{id}/groups               bbd_students.view
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { createPicker } from '../../ui-kit/picker.js';
import { confirmSimple, dialogA11y, todayIso } from '../../ui-kit/kit.js';
import {
  EXAM_CHOICES, GRADE_CHOICES, TRACK_CHOICES, gradeLabel, parseChoice, parseGrade,
  suggestedExam, trackAllowed,
} from './curriculum.js';

const BASE = '/api/bbd_students/groups';

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const KIND_LABEL = { group: 'Sınıf', individual: 'Özel ders' };

/** Sınıfın seviyesi kısa yazı: "11. sınıf · SAY · YKS"; seviyesizse ''. */
export function levelText(level) {
  if (!level || level.gradeLevel === null || level.gradeLevel === undefined) return '';
  return [gradeLabel(level.gradeLevel), level.track === 'SOZ' ? 'SÖZ' : level.track === 'DIL' ? 'DİL'
    : level.track, level.examTarget].filter(Boolean).join(' · ');
}

/** Öğrenci satırı → seçici kaydı: başlık ilk sınıf, süzgeç BÜTÜN sınıfları (çoklu üyelik). */
export function pickerItem(student) {
  const classes = Array.isArray(student.classNames) && student.classNames.length
    ? student.classNames : (student.className ? [student.className] : []);
  return {
    id: student.kantinId,
    name: student.displayName || 'Adsız',
    group: classes[0] || 'Sınıfsız',
    groups: classes.length ? classes : ['Sınıfsız'],
    meta: [classes.length > 1 && classes.join(', '),
      student.gradeLevel && gradeLabel(student.gradeLevel), student.track,
      student.schoolNo && `No ${student.schoolNo}`].filter(Boolean).join(' · '),
    gradeLevel: student.gradeLevel ?? null,
    track: student.track ?? null,
  };
}

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
 * "Sınıflar" sekmesi (tek liste).
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
  search.placeholder = 'Sınıf ya da anons adı ara';
  search.setAttribute('aria-label', 'Sınıf ya da anons adı ara');
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.query = search.value.trim(); load(); }, 200);
  });
  const kindFilter = h('select', 'st-input st-select');
  kindFilter.setAttribute('aria-label', 'Sınıf türü');
  for (const [value, label] of [['', 'Tüm sınıflar'], ['group', 'Toplu sınıflar'], ['individual', 'Özel ders']]) {
    kindFilter.append(new Option(label, value));
  }
  kindFilter.addEventListener('change', () => { state.kind = kindFilter.value; load(); });
  const archFilter = h('select', 'st-input st-select');
  archFilter.setAttribute('aria-label', 'Arşiv durumu');
  for (const [value, label] of [['live', 'Canlı sınıflar'], ['archived', 'Arşiv'], ['all', 'Tümü']]) {
    archFilter.append(new Option(label, value));
  }
  archFilter.addEventListener('change', () => { state.archived = archFilter.value; load(); });
  const count = h('span', 'st-count');
  bar.append(search, kindFilter, archFilter, count, h('div', 'st-spacer'));
  if (canEdit) {
    bar.append(button('+ Yeni sınıf', 'primary', () => openEditor(null)));
  }
  view.append(bar);

  view.append(h('p', 'st-hint',
    'Sınıflar tek listedir: 11-A da, “TYT Matematik” de bir sınıftır; bir öğrenci birden çok '
    + 'sınıfa üye olabilir. BBD Akademi ders programı, yoklama ve sınıf müfredatı, zil sistemi '
    + 'çağrısı buradan okur. Üyelik tarihlidir (son gün dahil). Silme yoktur, arşiv vardır — '
    + 'geçmiş yoklama korunur.'));

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
      list.replaceChildren(h('p', 'st-empty', `Sınıflar okunamadı: ${error.message}`));
      return;
    }
    renderList();
    if (state.selected) await select(state.selected);
  }

  function renderList() {
    count.textContent = `${state.groups.length} sınıf`;
    list.replaceChildren();
    if (state.groups.length === 0) {
      list.append(h('p', 'st-empty', 'Sınıf yok. “+ Yeni sınıf” ile ad ve öğrenci seçerek açın.'));
      return;
    }
    for (const group of state.groups) {
      const item = h('button', 'st-item');
      item.type = 'button';
      item.classList.toggle('active', group.id === state.selected);
      const main = h('span', 'st-item-main');
      main.append(h('b', null, group.name));
      main.append(h('span', 'st-item-meta',
        [levelText(group.classLevel), group.callName && `anons: ${group.callName}`,
          `${group.memberCount} öğrenci bugün`].filter(Boolean).join(' · ')));
      item.append(main);
      if (group.kind === 'individual') {
        item.append(h('i', 'st-badge gr-kind gr-kind-individual', KIND_LABEL.individual));
      }
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
      detail.replaceChildren(h('p', 'st-empty', `Sınıf okunamadı: ${error.message}`));
      return;
    }
    renderDetail();
  }

  function studentName(id) {
    const student = ctx.students().find((row) => row.kantinId === id);
    return student ? (student.displayName || id) : id;
  }

  /** Öğrencinin BU SINIF DIŞINDAKİ sınıfları (çoklu üyelik bilgisi). */
  function studentClass(id, except = '') {
    const row = ctx.students().find((item) => item.kantinId === id);
    const names = row?.classNames || (row?.className ? [row.className] : []);
    return names.filter((name) => name !== except).join(', ');
  }

  function renderDetail() {
    picker?.destroy?.();
    picker = null;
    const group = state.detail;
    detail.replaceChildren();
    if (!group) {
      detail.append(h('p', 'st-empty', 'Soldan bir sınıf seçin.'));
      return;
    }

    const head = h('header', 'st-detail-head');
    head.append(h('h3', null, group.name));
    head.append(h('span', 'st-subtitle', [
      levelText(group.classLevel) || 'seviye yok',
      group.kind === 'individual' ? 'özel ders (tek öğrenci)' : null,
      group.callName ? `anons adı: ${group.callName}` : 'anons adı yok (ad okunur)',
      group.archived ? `arşiv · ${(group.archivedAt || '').slice(0, 10)}` : 'canlı',
    ].filter(Boolean).join(' · ')));
    head.append(h('div', 'st-spacer'));
    if (canEdit) {
      head.append(button('Düzenle', '', () => openEditor(group)));
      if (group.classLevel && !group.archived && group.current.length) {
        head.append(button('Seviyeyi üyelere uygula', '', applyLevel));
      }
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
        const others = studentClass(member.studentId, group.name);
        row.append(check, h('b', null, studentName(member.studentId)),
          h('span', 'st-item-meta', [others && `diğer: ${others}`,
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
      target.setAttribute('aria-label', 'Taşınacak sınıf');
      target.append(new Option('Taşınacak sınıf…', ''));
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
          if (ids.length === 0 || !target.value) { ctx.toast('Öğrenci ve hedef sınıf seçin.'); return; }
          await call('/move', {
            studentIds: ids, fromGroupId: group.id, toGroupId: target.value,
            effectiveFrom: endDay.value, reason: reason.value,
          }, `${ids.length} öğrenci taşındı: önceki sınıfta son gün ${trDay(dayBefore(endDay.value))}.`);
        }));
      current.append(tools);
      current.append(h('p', 'st-hint',
        '“Çıkar”: seçilen gün öğrencinin bu sınıftaki SON günüdür (dahil); diğer sınıfları sürer. '
        + '“Taşı”: bu sınıftaki son gün bir önceki gün olur, yeni sınıfta seçilen gün başlar.'));
    }
    body.append(current);

    // --------------------------------------------------------- üye ekle
    if (canEdit && !group.archived) {
      const add = h('section', 'st-card');
      add.append(h('h4', null, 'Öğrenci ekle'));
      if (group.kind === 'individual') {
        add.append(h('p', 'st-hint', 'Özel ders sınıfında aynı gün yalnız bir öğrenci olur.'));
      }
      // Çoklu seçici: sınıf (öğrencinin BÜTÜN sınıfları) / seviye / alan / ara.
      const pool = ctx.students()
        .filter((student) => student.status !== 'inactive')
        .filter((student) => !group.current.some((m) => m.studentId === student.kantinId))
        .map(pickerItem);
      const extra = h('div', 'gr-tools gr-filters');
      const gradeFilter = h('select', 'st-input st-select');
      gradeFilter.setAttribute('aria-label', 'Seviye süzgeci');
      gradeFilter.append(new Option('Tüm seviyeler', ''));
      for (const [value, label] of GRADE_CHOICES) if (value) gradeFilter.append(new Option(label, value));
      const trackFilter = h('select', 'st-input st-select');
      trackFilter.setAttribute('aria-label', 'Alan süzgeci');
      trackFilter.append(new Option('Tüm alanlar', ''));
      for (const [value, label] of TRACK_CHOICES) if (value) trackFilter.append(new Option(label, value));
      extra.append(gradeFilter, trackFilter);
      picker = createPicker({
        groupLabel: 'Sınıf',
        placeholder: 'Ad, sınıf, no ara',
        label: 'Eklenecek öğrenciler',
        single: group.kind === 'individual',
        items: pool,
      });
      const refilter = () => picker.setItems(pool.filter((item) =>
        (!gradeFilter.value || String(item.gradeLevel ?? '') === gradeFilter.value)
        && (!trackFilter.value || item.track === trackFilter.value)));
      gradeFilter.addEventListener('change', refilter);
      trackFilter.addEventListener('change', refilter);
      add.append(extra, picker.node);
      const from = dayInput(todayIso(), 'Başlangıç günü');
      const reason = h('input', 'st-input');
      reason.placeholder = 'Gerekçe (geçmiş tarihte zorunlu)';
      reason.setAttribute('aria-label', 'Gerekçe (geçmiş tarihte zorunlu)');
      reason.maxLength = 200;
      const addTools = h('div', 'gr-tools');
      add.append(addTools);
      let applyBox = null;
      if (group.classLevel) {
        applyBox = h('input', 'st-check');
        applyBox.type = 'checkbox';
        applyBox.checked = true;
        applyBox.id = `gr-apply-${Math.random().toString(36).slice(2, 8)}`;
        const applyLabel = h('label', 'st-check',
          `Sınıfın seviyesini (${levelText(group.classLevel)}) eklenenlere yaz`);
        applyLabel.htmlFor = applyBox.id;
        const applyRow = h('div', 'st-check-row');
        applyRow.append(applyBox, applyLabel);
        add.append(applyRow);
      }
      addTools.append(h('span', 'st-label', 'Başlangıç günü'), from, reason,
        button('Seçilenleri ekle', 'primary', async () => {
          const ids = picker.selection();
          if (ids.length === 0) { ctx.toast('Önce öğrenci seçin.'); return; }
          const result = await post(`${encodeURIComponent(group.id)}/members`,
            { studentIds: ids, validFrom: from.value, reason: reason.value,
              applyLevel: applyBox ? applyBox.checked : true }, null);
          if (result) {
            ctx.toast([result.skipped?.length
              ? `${result.added} eklendi · ${result.skipped.length} zaten üyeydi.`
              : `${result.added} öğrenci eklendi.`,
            result.levelUpdated ? `${result.levelUpdated} öğrencinin seviyesi güncellendi.` : '']
              .filter(Boolean).join(' '));
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
      title: `“${group.name}” sınıfı arşivlensin mi?`,
      description: 'Açık üyelikler bugün kapanır; henüz başlamamış üyelikler kaldırılır. '
        + 'Geçmiş yoklama ve raporlar korunur. Arşivden geri alınabilir.',
      confirmLabel: 'Arşivle',
      danger: true,
    })) return;
    await post(`${encodeURIComponent(group.id)}/archive`, {}, 'Sınıf arşivlendi.');
  }

  async function applyLevel() {
    const group = state.detail;
    if (!await confirmSimple(ctx.dialogRoot, {
      title: `Seviye ${group.current.length} öğrenciye yazılsın mı?`,
      description: `“${group.name}” sınıfının seviyesi (${levelText(group.classLevel)}) bugünkü `
        + 'üyelerin kendi seviyesine yazılır; Akademi varsayılan müfredatı buna göre güncellenir. '
        + '“Alanını ve hedef sınavını koru” işaretli öğrencide yalnız seviye değişir.',
      confirmLabel: 'Uygula',
    })) return;
    const result = await call(`/${encodeURIComponent(group.id)}/apply-level`, {}, null);
    if (result) ctx.toast(`${result.updatedStudents} öğrencinin seviyesi güncellendi.`);
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
    card.append(h('h3', 'kit-dialog-title', group ? 'Sınıfı düzenle' : 'Yeni sınıf'));
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
    const kind = h('input', 'st-check');
    kind.type = 'checkbox';
    kind.id = `gr-kind-${Math.random().toString(36).slice(2, 8)}`;
    kind.checked = group?.kind === 'individual';
    const kindLabel = h('label', 'st-check', 'Özel ders — tek öğrenci (anons tekil hitap eder)');
    kindLabel.htmlFor = kind.id;
    const kindRow = h('div', 'st-check-row');
    kindRow.append(kind, kindLabel);
    const note = h('textarea', 'st-input st-textarea');
    note.maxLength = 500;
    note.value = group?.note || '';
    // Seviye (isteğe bağlı şablon): öğrenci eklenince ona da yazılır.
    const level = group?.classLevel || null;
    const choose = (choices, value, labelText) => {
      const select = h('select', 'st-input st-select');
      select.setAttribute('aria-label', labelText);
      for (const [v, text] of choices) select.append(new Option(text, v));
      select.value = value === null || value === undefined ? '' : String(value);
      return select;
    };
    const grade = choose(GRADE_CHOICES, level?.gradeLevel, 'Seviye');
    const track = choose(TRACK_CHOICES, level?.track, 'Alan');
    const exam = choose(EXAM_CHOICES, level?.examTarget, 'Hedef sınav');
    const syncTrack = () => {
      const allowed = trackAllowed(parseGrade(grade.value));
      track.disabled = !allowed || !grade.value;
      if (track.disabled) track.value = '';
      exam.disabled = !grade.value;
      if (!grade.value) exam.value = '';
    };
    grade.addEventListener('change', () => {
      if (!exam.value && suggestedExam(parseGrade(grade.value))) exam.value = suggestedExam(parseGrade(grade.value));
      syncTrack();
    });
    syncTrack();
    const levelGrid = h('div', 'st-grid');
    levelGrid.append(field('Seviye (isteğe bağlı)', grade), field('Alan', track), field('Hedef sınav', exam));
    const applyBox = h('input', 'st-check');
    applyBox.type = 'checkbox';
    applyBox.checked = true;
    applyBox.id = `gr-lvl-${Math.random().toString(36).slice(2, 8)}`;
    const applyLabel = h('label', 'st-check', 'Seviye değişirse bugünkü üyelere de yaz');
    applyLabel.htmlFor = applyBox.id;
    const applyRow = h('div', 'st-check-row');
    applyRow.append(applyBox, applyLabel);
    card.append(field('Sınıf adı', name), field('Anons adı (TTS için)', callName), kindRow,
      levelGrid, ...(group ? [applyRow] : []), field('Not', note));
    if (!group) {
      card.append(h('p', 'st-hint',
        'Öğrencileri sınıfı açtıktan sonra “Öğrenci ekle” bölümünden seçin.'));
    }
    const error = h('p', 'kit-dialog-error');
    card.append(error);
    error.setAttribute('role', 'alert');
    let release = () => {};
    const close = () => { overlay.remove(); release(); };
    const save = button(group ? 'Kaydet' : 'Sınıfı aç', 'primary', async () => {
      const body = { name: name.value, callName: callName.value,
        kind: kind.checked ? 'individual' : 'group', note: note.value };
      const nextLevel = grade.value ? { gradeLevel: parseGrade(grade.value),
        track: parseChoice(track.value), examTarget: parseChoice(exam.value) } : null;
      if (group || nextLevel) body.level = nextLevel;
      if (group) body.applyLevel = applyBox.checked;
      try {
        const result = group
          ? await ctx.api(`${BASE}/${encodeURIComponent(group.id)}`, { method: 'PUT', body })
          : await ctx.api(BASE, { method: 'POST', body });
        close();
        state.selected = result.id;
        ctx.toast(group ? 'Sınıf kaydedildi.' : 'Sınıf açıldı.');
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
    create: 'sınıf açıldı', import: 'zil sisteminden taşındı', from_class: 'başka sınıftan kopyalandı',
    update: 'düzenlendi', archive: 'arşivlendi', restore: 'arşivden alındı',
    members_add: 'öğrenci eklendi', members_end: 'üyelik bitirildi', move_out: 'başka sınıfa taşındı',
    move_in: 'başka sınıftan taşındı', member_fix: 'üyelik düzeltildi',
    student_deactivated: 'öğrenci pasifleşti', class_merge: 'eski sınıf adlarından birleştirildi',
    class_merge_rollback: 'birleştirme geri alındı',
  }[action] || action;
}

/**
 * Öğrenci kartındaki "Sınıfları" bölümü — geçmişle.
 * @returns {{destroy}}
 */
export function mountStudentGroups(host, { api, studentId }) {
  let alive = true;
  host.replaceChildren(h('p', 'st-hint', 'Sınıflar okunuyor…'));
  api(`/api/bbd_students/students/${encodeURIComponent(studentId)}/groups`).then((payload) => {
    if (!alive) return;
    const items = payload.items || [];
    if (items.length === 0) {
      host.replaceChildren(h('p', 'st-hint', 'Öğrenci hiçbir sınıfta değil. Sınıflar sekmesinden eklenir.'));
      return;
    }
    const today = todayIso();
    const rows = items.map((item) => {
      const row = h('div', 'gr-history-row');
      row.classList.toggle('gr-past', !validOn(item, today));
      row.append(h('b', null, item.group.name),
        ...(item.group.kind === 'individual'
          ? [h('i', 'st-badge gr-kind gr-kind-individual', KIND_LABEL.individual)] : []),
        h('span', null, `${trDay(item.validFrom)} → ${trDay(item.validTo)}`),
        h('span', 'st-item-meta', [item.group.archived && 'arşiv', item.reason].filter(Boolean).join(' · ')));
      return row;
    });
    host.replaceChildren(...rows);
  }).catch((error) => {
    if (alive) host.replaceChildren(h('p', 'st-hint', `Sınıflar okunamadı: ${error.message}`));
  });
  return { destroy() { alive = false; } };
}
