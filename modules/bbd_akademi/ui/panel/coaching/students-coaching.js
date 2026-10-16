// BBD Akademi — öğrenci detayı: Koçluk sekmesi (F-A21 koç notları, F-S21, F-A22).
//
// Beş iç sekme:
//  · Koç — öğrencinin koçu ve eşleme geçmişi; koç ata / değiştir / eşlemeyi bitir
//    (`admin_listStudentCoachHistory`, `admin_createCoachAssignment`, `admin_changeCoach`,
//    `admin_endCoachAssignment`). ÖĞRETMEN = KOÇ: seçici `admin_listCoaches`'tır ve
//    "Öğretmen / Koç" rolündeki bütün etkin personeli listeler (yönetici + öğretmen olan
//    da); ayrı koç tanımı / koç profili adımı yoktur (profil ilk düzenlemede oluşur).
//  · Koç notları — kurumun görüşme notları (özel ya da paylaşılan); ekle,
//    düzenle, sil (`/v1/admin/students/{id}/coach-notes`, `/v1/admin/coach-notes/{id}`).
//  · Öğrencinin gördüğü notlar — öğrenci/veli uygulamasındaki paylaşılan
//    notlar, olduğu gibi (`/v1/students/{id}/coach-notes`, salt okunur).
//  · Değerlendirmeler — öğrenci/velinin gördüğü yayımlanmış değerlendirmeler,
//    ayrıntı ve gelişim grafiği (salt okunur). Değerlendirme YAZMAK
//    Değerlendirmeler bölümündedir ("Değerlendir" → `app.go('evaluations')`).
//  · Öz değerlendirme — haftalık öz değerlendirmeler (salt okunur; bayrak
//    `self_review` kapalıysa sebebi söylenir).
// Üstte öğrencinin etkin erken uyarıları (en çok 5; tek istek) — her biri
// Erken uyarı bölümünde açılır.
//
// TEMBEL: çekmeceye gömülür (`coachingPanel(app, studentId)`); ağ ilk
// `shown()` ile başlar, her iç sekme ilk açıldığında kendi çağrılarını paralel
// atar. Listeler imleçlidir; satır başına ek istek atılmaz (öz değerlendirme
// ayrıntısı liste yükünden çizilir).
//
// DERİN BAĞLANTI: `shown({coachingTab})` — coach · notes · shared · evaluations · selfReviews.

import { button, clip, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, hintBox, kpiRow, tabBar } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { groupedBar, lineChart, sparkline } from '../../../ui-kit/charts.js';
import {
  actionButton, addDays, confirmAction, empty, fmtDate, fmtDateTime, formDialog, kv, loader, num, openDrawer,
  optionsOf, pagedList, runAction, selectBox, statusBadge, todayIst, toolbar, weekStart,
} from '../common.js';
import { featureNotice, ruleName, ruleValue, severityBadge, statusBadgeOf } from '../insights-shared.js';

const SUBTABS = [
  { key: 'coach', label: 'Koç', read: 'admin_listStudentCoachHistory' },
  { key: 'notes', label: 'Koç notları', read: 'admin_listCoachNotes' },
  { key: 'shared', label: 'Öğrencinin gördüğü notlar', read: 'students_listSharedCoachNotes' },
  { key: 'evaluations', label: 'Değerlendirmeler', read: 'students_listEvaluations' },
  { key: 'selfReviews', label: 'Öz değerlendirme', read: 'students_listSelfReviews' },
];

const VISIBILITY = { private: ['Özel (yalnız kurum)', 'dim'], student: ['Öğrenci görür', 'info'],
  guardians: ['Veliler görür', 'info'], all: ['Öğrenci ve veliler', 'good'] };
const CHANGE = { up: ['▲ yükseldi', 'good'], down: ['▼ düştü', 'bad'], same: ['= aynı', 'dim'] };

const scoreText = (value, digits = 2) => (value === null || value === undefined ? '—' : num(value, digits));
const scoreTone = (value) => (value === null || value === undefined ? 'dim'
  : value <= 2 ? 'bad' : value < 4 ? 'warn' : 'good');

/**
 * Öğrenci detayına gömülen "Koçluk" sekmesi.
 * @returns {{node: HTMLElement, shown: (payload?: {coachingTab?: string}) => void, destroy: () => void}}
 */
export function coachingPanel(app, studentId) {
  loadStyles(import.meta.url, 'students-coaching.css');
  const node = h('div', 'ak-stack ak-sco');
  const visible = SUBTABS.filter((tab) => app.can(tab.read));

  const evaluate = app.can('admin_createEvaluation')
    ? actionButton(app, 'admin_createEvaluation', 'Değerlendir', { variant: 'primary',
      onClick: () => app.go('evaluations', { studentId }) })
    : null;
  const warnings = app.can('admin_listEarlyWarnings') ? warningsStrip(app, studentId) : null;
  const top = h('div', 'ak-sco-top');
  if (warnings) top.append(warnings.node);
  if (evaluate) top.append(h('div', 'ak-sco-evaluate'));
  if (evaluate) top.lastChild.append(evaluate);
  if (top.childElementCount) node.append(top);

  if (!visible.length) {
    node.append(empty('Koçluk verisine erişim yok', 'Koç notları, değerlendirmeler ve öz değerlendirmelerden '
      + 'hiçbirine yetkiniz yok.'));
    let shownOnce = false;
    return { node, shown() { if (!shownOnce) { shownOnce = true; warnings?.reload(); } }, destroy() {} };
  }

  const bodies = {};
  const builders = {
    coach: () => coachTab(app, studentId),
    notes: () => notesTab(app, studentId),
    shared: () => sharedTab(app, studentId),
    evaluations: () => evaluationsTab(app, studentId),
    selfReviews: () => selfReviewsTab(app, studentId),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => showTab(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.coachingTab && visible.some((tab) => tab.key === payload.coachingTab)
        ? payload.coachingTab : null;
      if (!started) {
        started = true;
        warnings?.reload();
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
      } else if (wanted) {
        tabs.select(wanted, false);
        showTab(wanted);
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ============================================================ koç (eşleme)

const ASSIGNMENT_STATUS = { active: ['Etkin', 'good'], scheduled: ['Başlayacak', 'info'], ended: ['Bitti', 'dim'] };

/** Koç seçeneği: "Ad · 12/20 öğrenci" (kapasite kurum ya da koç sınırı). */
function coachOption(row) {
  const load = row.maxStudents ? `${row.studentCount}/${row.maxStudents}` : String(row.studentCount);
  return { value: row.coach.id, label: `${row.coach.displayName} · ${load} öğrenci` };
}

/** `km:user:<id>:<ad>` (vekil etiketi) → "Ad (KM)"; kişi adı olduğu gibi. */
function assignerText(by) {
  if (!by) return '—';
  const parts = String(by).split(':');
  if (parts[0] !== 'km') return by;
  if (parts.length < 4) return 'Kontrol Merkezi';
  try {
    return `${decodeURIComponent(parts.slice(3).join(':'))} (KM)`;
  } catch {
    return 'Kontrol Merkezi';
  }
}

function coachTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const canPick = app.can('admin_listCoaches');

  const view = loader(async () => {
    const [history, coaches] = await Promise.all([
      app.api.get(`/v1/admin/students/${studentId}/coach-assignments`),
      canPick ? app.api.get('/v1/admin/coaching/coaches', { sort: 'name' }) : Promise.resolve(null),
    ]);
    return { items: history?.items || [], coaches: coaches?.items || null };
  }, ({ items, coaches }) => {
    // Açık eşleme: bitişi olmayan (etkin ya da ileri tarihli) — Akademi'de en çok bir tane.
    const open = items.find((row) => row.status !== 'ended' && !row.endsOn) || null;
    const nodes = [hintBox('Öğretmen = koç: "Öğretmen / Koç" rolündeki her etkin personel (yönetici + öğretmen '
      + 'olanlar dahil) ayrıca tanımlanmadan koç seçilebilir. Rol, Personel → Mobil yönetici erişimi\'nden verilir.')];
    const actions = h('div', 'ak-inline');
    const options = (coaches || []).map(coachOption);
    if (!open) {
      actions.append(actionButton(app, 'admin_createCoachAssignment', 'Koç ata', { variant: 'primary',
        onClick: () => assign(options) }));
    } else {
      actions.append(
        actionButton(app, 'admin_changeCoach', 'Koçu değiştir', { variant: 'primary',
          onClick: () => change(open, options) }),
        actionButton(app, 'admin_endCoachAssignment', 'Eşlemeyi bitir', { variant: 'ghost',
          onClick: () => end(open) }),
      );
    }
    const body = h('div', 'ak-card-pad ak-stack');
    body.append(open
      ? kv([
        ['Koç', open.coach?.displayName || '—'],
        ['Durum', statusBadge(ASSIGNMENT_STATUS, open.status)],
        ['Başlangıç', fmtDate(open.startsOn)],
        ['Atayan', assignerText(open.assignedBy)],
      ])
      : h('span', 'ak-muted', 'Öğrencinin koçu yok. "Koç ata" ile Öğretmen / Koç rolündeki birini seçin.'),
    actions);
    nodes.push(card(open ? 'Koçu' : 'Koç atanmamış', body));
    if (coaches && !coaches.length) {
      nodes.push(alertBox('Koç seçilecek kimse yok: Personel → Mobil yönetici erişimi\'nde birine '
        + '"Öğretmen / Koç" rolü verin.', 'warn'));
    }
    const table = dataTable({
      columns: [
        { key: 'coach', label: 'Koç', width: 'minmax(0, 1.4fr)', cell: (row) => row.coach?.displayName || '—' },
        { key: 'status', label: 'Durum', width: '120px', cell: (row) => statusBadge(ASSIGNMENT_STATUS, row.status) },
        { key: 'from', label: 'Başlangıç', width: '120px', cell: (row) => fmtDate(row.startsOn) },
        { key: 'to', label: 'Bitiş', width: '120px', cell: (row) => (row.endsOn ? fmtDate(row.endsOn) : '—') },
        { key: 'reason', label: 'Bitiş gerekçesi', width: 'minmax(0, 1.4fr)', cell: (row) => row.endReason || '—' },
      ],
      rows: items,
      empty: empty('Eşleme geçmişi yok', 'Öğrenciye henüz koç atanmadı.'),
    });
    nodes.push(card('Eşleme geçmişi', table.node));
    return nodes;
  });
  node.append(view.node);

  const coachField = (options, label = 'Koç') => ({ key: 'coachId', label, type: 'select', required: true,
    options: [{ value: '', label: options.length ? 'Öğretmen / Koç seçin' : 'Seçilecek öğretmen / koç yok' }, ...options],
    hint: 'Öğretmen / Koç rolündeki etkin personel; yanında üstlendiği öğrenci sayısı.' });
  const reasonCheck = (value) => (String(value || '').trim().length < 3 ? 'En az 3 karakter yazın.' : null);
  const capacityToast = (capacity) => {
    if (capacity?.exceeded) {
      app.toast(`${capacity.coach?.displayName || 'Koç'} kapasiteyi aştı (${capacity.studentCount}/`
        + `${capacity.maxStudents}); atama yine yapıldı.`, 'warn');
    }
  };

  async function assign(options) {
    const result = await formDialog(app, {
      title: 'Koç ata',
      description: 'Öğrenci ve veliye "koçunuz" bildirimi gider; koç bu öğrencinin koçluk ekranlarını görür.',
      value: { coachId: '', startsOn: todayIst() },
      fields: [coachField(options), { key: 'startsOn', label: 'Başlangıç', type: 'date', required: true }],
      submitLabel: 'Ata',
      onSubmit: (draft, { key }) => {
        if (!draft.coachId) throw new Error('Koç seçin.');
        return app.api.post('/v1/admin/coach-assignments',
          { studentId, coachId: draft.coachId, startsOn: draft.startsOn || undefined }, { key });
      },
    });
    if (!result) return;
    app.toast(`${result.assignment?.coach?.displayName || 'Koç'} atandı.`, 'good');
    capacityToast(result.capacity);
    view.reload();
  }

  async function change(current, options) {
    const result = await formDialog(app, {
      title: 'Koçu değiştir',
      description: `Şu anki koç: ${current.coach?.displayName || '—'}. Açık koç görevleri ve yazışmalar yeni koça `
        + 'devredilir; devir notu yeni koça görünür.',
      value: { coachId: '', effectiveOn: todayIst(), reason: '', handoverNote: '' },
      fields: [
        coachField(options.filter((option) => option.value !== current.coach?.id), 'Yeni koç'),
        { key: 'effectiveOn', label: 'Geçerlilik', type: 'date', required: true },
        { key: 'reason', label: 'Gerekçe', type: 'text', required: true, maxLength: 300, wide: true,
          validate: reasonCheck },
        { key: 'handoverNote', label: 'Devir notu', type: 'textarea', required: true, maxLength: 2000, wide: true,
          validate: reasonCheck },
      ],
      submitLabel: 'Değiştir',
      onSubmit: (draft, { key }) => {
        if (!draft.coachId) throw new Error('Yeni koçu seçin.');
        return app.api.post(`/v1/admin/students/${studentId}/coach-change`, {
          newCoachId: draft.coachId, effectiveOn: draft.effectiveOn || undefined,
          reason: String(draft.reason).trim(), handoverNote: String(draft.handoverNote).trim(),
        }, { key });
      },
    });
    if (!result) return;
    app.toast(`Koç değişti: ${result.created?.coach?.displayName || ''}.`, 'good');
    capacityToast(result.capacity);
    view.reload();
  }

  async function end(current) {
    const result = await formDialog(app, {
      title: 'Eşlemeyi bitir',
      description: `${current.coach?.displayName || 'Koç'} ile eşleme biter; öğrenci koçsuz kalır.`,
      value: { endsOn: todayIst(), reason: '' },
      fields: [
        { key: 'endsOn', label: 'Bitiş', type: 'date', required: true },
        { key: 'reason', label: 'Gerekçe', type: 'text', required: true, maxLength: 300, wide: true,
          validate: reasonCheck },
      ],
      submitLabel: 'Bitir',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/coach-assignments/${current.id}/end`,
        { endsOn: draft.endsOn || undefined, reason: String(draft.reason).trim() }, { key }),
    });
    if (!result) return;
    app.toast('Eşleme bitti.', 'good');
    view.reload();
  }

  return { node, reload: () => view.reload(), destroy() {} };
}

// ============================================================ erken uyarı

function warningsStrip(app, studentId) {
  const view = loader(async () => {
    try {
      return await app.api.get('/v1/admin/early-warnings', { studentId, status: 'active', limit: 5 });
    } catch (failure) {
      if (failure?.code === 'feature_disabled') return { disabled: true };
      throw failure;
    }
  }, (data) => {
    const box = h('div', 'ak-sco-warnings');
    const head = h('div', 'ak-inline');
    head.append(h('b', '', 'Erken uyarılar'));
    if (data.disabled) {
      head.append(h('span', 'ak-muted', 'Erken uyarı özelliği kurumda kapalı.'));
      box.append(head);
      return box;
    }
    const items = data.items || [];
    const total = data.counts?.open ?? items.length;
    head.append(total ? badge(`${num(total)} etkin`, 'bad') : badge('Etkin uyarı yok', 'good'));
    if (total) {
      const all = h('button', 'ak-link', 'Tümünü Erken uyarı bölümünde gör');
      all.type = 'button';
      all.addEventListener('click', () => app.go('insights', { studentId }));
      head.append(h('span', 'kit-spacer'), all);
    }
    box.append(head);
    for (const item of items) {
      const row = h('button', 'ak-sco-warning');
      row.type = 'button';
      const text = h('div', 'ak-cell-stack');
      text.append(h('span', '', item.title), h('small', '', [ruleName(item.rule),
        item.context?.value !== null && item.context?.value !== undefined
          ? `${ruleValue(item.rule, item.context.value)} (eşik ${ruleValue(item.rule, item.context.cutoff)})` : '',
        `son ${fmtDateTime(item.lastTriggeredAt)}`].filter(Boolean).join(' · ')));
      const meta = h('div', 'ak-inline');
      meta.append(severityBadge(item.severity), statusBadgeOf(item.status));
      row.append(text, meta);
      row.addEventListener('click', () => app.go('insights', { earlyWarningId: item.id }));
      box.append(row);
    }
    return box;
  }, { rows: 2, columns: 3 });
  return view;
}

// ============================================================== koç notları

function noteColumns(app, { onWarning } = {}) {
  return [
    { key: 'date', label: 'Tarih', width: '96px', cell: (row) => fmtDate(row.date) },
    { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('b', '', row.topic), clip(h('small', 'ak-sco-preview'), row.note, 140));
      return cell;
    } },
    { key: 'visibility', label: 'Görünürlük', width: '150px', cell: (row) => statusBadge(VISIBILITY, row.visibility) },
    { key: 'author', label: 'Yazan', width: 'minmax(0, 0.8fr)', cell: (row) => row.author || '—' },
    onWarning ? { key: 'source', label: '', width: '110px', cell: (row) => {
      if (!row.earlyWarningId) return '';
      const link = h('button', 'ak-link', 'Erken uyarı');
      link.type = 'button';
      link.title = 'Bu not bir erken uyarıdan eklendi';
      link.addEventListener('click', (event) => { event.stopPropagation(); onWarning(row.earlyWarningId); });
      return link;
    } } : null,
  ].filter(Boolean);
}

function notesTab(app, studentId) {
  const node = h('div', 'ak-stack');
  let visibility = '';
  const goWarning = (earlyWarningId) => app.go('insights', { earlyWarningId });

  const filter = selectBox([{ value: '', label: 'Tüm notlar' }, ...optionsOf(VISIBILITY)], '',
    (value) => { visibility = value; list.reload(); }, { label: 'Görünürlük' });
  const add = actionButton(app, 'admin_createCoachNote', 'Not ekle', { variant: 'primary',
    onClick: async () => {
      const created = await noteDialog(app, studentId, null);
      if (created) list.reload();
    } });

  const list = pagedList({
    app,
    path: `/v1/admin/students/${studentId}/coach-notes`,
    query: () => ({ visibility: visibility || undefined }),
    columns: noteColumns(app, { onWarning: goWarning }),
    limit: 30,
    onRow: (row) => openNote(row),
    empty: { title: 'Koç notu yok', text: 'Görüşme notlarını buraya ekleyin; paylaşılan notları öğrenci ve veli de görür.' },
  });

  node.append(toolbar(filter.node, h('span', 'kit-spacer'), add), list.node);

  function openNote(row) {
    const panel = openDrawer(app, { title: row.topic || 'Koç notu', subtitle: [fmtDate(row.date), row.author]
      .filter(Boolean).join(' · ') });
    panel.node.classList.add('ak-sco-drawer');
    let current = row;
    const paint = () => {
      panel.setTitle(current.topic || 'Koç notu');
      const bar = h('div', 'ak-inline');
      bar.append(
        actionButton(app, 'admin_updateCoachNote', 'Düzenle', { onClick: async () => {
          const updated = await noteDialog(app, studentId, current);
          if (!updated) return;
          current = updated;
          list.replace((item) => item.id === updated.id, updated);
          paint();
        } }),
        actionButton(app, 'admin_deleteCoachNote', 'Sil', { variant: 'danger', onClick: async (event) => {
          const yes = await confirmAction(app, { title: 'Not silinsin mi?', danger: true, confirmLabel: 'Sil',
            description: current.visibility === 'private' ? 'Özel not kalıcı olarak silinir.'
              : 'Not silinir ve öğrenci/veli görünümünden de kalkar.' });
          if (!yes) return;
          const done = await runAction(app, async () => {
            await app.api.del(`/v1/admin/coach-notes/${current.id}`);
            return true;
          }, { success: 'Not silindi.', button: event?.currentTarget });
          if (done !== true) return;
          list.remove((item) => item.id === current.id);
          panel.close();
        } }),
      );
      if (current.earlyWarningId && app.can('admin_getEarlyWarning')) {
        bar.append(button('Erken uyarıyı aç', { variant: 'ghost', onClick: () => {
          panel.close();
          goWarning(current.earlyWarningId);
        } }));
      }
      panel.body.replaceChildren(bar, noteBody(current));
    };
    paint();
  }

  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

function noteBody(note) {
  const wrap = h('div', 'ak-stack');
  wrap.append(card('', kv([
    ['Tarih', fmtDate(note.date)],
    ['Görünürlük', statusBadge(VISIBILITY, note.visibility)],
    ['Yazan', note.author || '—'],
    ['Eklendi', fmtDateTime(note.createdAt)],
    note.updatedAt ? ['Güncellendi', fmtDateTime(note.updatedAt)] : null,
  ])), card('Not', h('div', 'ak-card-pad ak-sco-text', note.note)));
  return wrap;
}

/** Not ekle (note=null) ya da düzenle. Düzenlemede yalnız değişen alanlar gider. */
function noteDialog(app, studentId, note) {
  const editing = Boolean(note);
  return formDialog(app, {
    title: editing ? 'Koç notunu düzenle' : 'Koç notu ekle',
    description: editing
      ? 'Özel not paylaşılana çevrilirse görenlere bildirim gider; paylaşılan not özele çevrilirse öğrenci ve '
        + 'veli görünümünden kalkar.'
      : 'Özel not yalnız kurumda görünür. Paylaşılan not öğrencinin ve/veya velinin uygulamasında görünür.',
    value: editing
      ? { date: note.date, topic: note.topic, note: note.note, visibility: note.visibility }
      : { date: todayIst(), topic: '', note: '', visibility: 'private', notify: true },
    fields: [
      { key: 'date', label: 'Görüşme tarihi', type: 'date', required: true },
      { key: 'visibility', label: 'Kim görür', type: 'select', options: optionsOf(VISIBILITY) },
      { key: 'topic', label: 'Konu', type: 'text', required: true, maxLength: 120, wide: true,
        placeholder: 'Ör. Deneme sonrası değerlendirme',
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter yazın.' : null) },
      { key: 'note', label: 'Not', type: 'textarea', required: true, maxLength: 3000, wide: true,
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter yazın.' : null) },
      editing ? null : { key: 'notify', label: 'Paylaşılan notu görenlere bildirim gönder', type: 'checkbox',
        hint: 'Özel notta bildirim gitmez.' },
    ].filter(Boolean),
    submitLabel: editing ? 'Kaydet' : 'Notu ekle',
    onSubmit: async (draft, { key }) => {
      if (!draft.date) throw new Error('Görüşme tarihini seçin.');
      const body = { date: draft.date, topic: String(draft.topic).trim(), note: String(draft.note).trim(),
        visibility: draft.visibility || 'private' };
      if (!editing) {
        return app.api.post(`/v1/admin/students/${studentId}/coach-notes`,
          { ...body, notify: body.visibility !== 'private' && Boolean(draft.notify) }, { key });
      }
      const changed = Object.fromEntries(Object.entries(body).filter(([field, value]) => note[field] !== value));
      if (!Object.keys(changed).length) return note;
      const updated = await app.api.patch(`/v1/admin/coach-notes/${note.id}`, changed);
      app.toast('Not güncellendi.', 'good');
      return updated;
    },
  }).then((result) => {
    if (result && !editing) app.toast('Not eklendi.', 'good');
    return result;
  });
}

// ===================================================== öğrencinin gördüğü notlar

function sharedTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/coach-notes`,
    columns: noteColumns(app),
    limit: 30,
    onRow: (row) => {
      const panel = openDrawer(app, { title: row.topic || 'Koç notu',
        subtitle: 'Öğrenci / veli uygulamasında görünen not' });
      panel.node.classList.add('ak-sco-drawer');
      panel.body.append(noteBody(row));
    },
    empty: { title: 'Paylaşılan not yok', text: 'Öğrenci ve veli şu an hiçbir koç notu görmüyor; özel notlar burada yer almaz.' },
  });
  node.append(hintBox('Öğrencinin ve velinin uygulamada gördüğü notlar, gördükleri gibi. Özel notlar burada yoktur.'),
    list.node);
  return { node, reload: () => list.reload(), destroy: () => list.destroy() };
}

// ========================================================== değerlendirmeler

function evaluationsTab(app, studentId) {
  const node = h('div', 'ak-stack');

  const growth = loader(() => app.api.get(`/v1/students/${studentId}/evaluations/growth`, { limit: 12 }),
    (data) => growthView(data), { rows: 3, columns: 4 });

  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/evaluations`,
    limit: 20,
    columns: [
      { key: 'title', label: 'Değerlendirme', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.title), h('small', '', row.period?.name || ''));
        return cell;
      } },
      { key: 'average', label: 'Ortalama', width: '150px', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(badge(scoreText(row.average), scoreTone(row.average)));
        if (row.average !== null && row.previousAverage !== null && row.previousAverage !== undefined) {
          const diff = row.average - row.previousAverage;
          cell.append(h('small', 'ak-muted', diff > 0 ? `▲ ${num(diff, 2)}` : diff < 0 ? `▼ ${num(-diff, 2)}` : '='));
        }
        return cell;
      } },
      { key: 'evaluator', label: 'Değerlendiren', width: 'minmax(0, 1fr)', cell: (row) => row.evaluator?.displayName || '—' },
      { key: 'publishedAt', label: 'Yayımlandı', width: '130px', cell: (row) => fmtDateTime(row.publishedAt) },
    ],
    onRow: (row) => openEvaluation(app, studentId, row.id),
    empty: { title: 'Paylaşılan değerlendirme yok',
      text: 'Öğrenciye ya da veliye yayımlanmış değerlendirme yok. Taslaklar Değerlendirmeler bölümündedir.' },
  });

  node.append(growth.node, card('Öğrenci / velinin gördüğü değerlendirmeler', list.node));
  return { node, reload: () => { growth.reload(); list.reload(); }, destroy: () => list.destroy() };
}

function growthView(data) {
  const points = (data.average || []).filter((point) => point.value !== null && point.value !== undefined)
    .map((point) => ({ label: fmtDate(point.publishedAt).slice(0, 5), value: point.value }));
  if (points.length < 2) {
    return h('div', 'ak-muted ak-sco-note', 'Gelişim grafiği için en az iki yayımlanmış değerlendirme gerekir.');
  }
  const rows = (data.criteria || []).map((criterion) => {
    const values = (criterion.points || []).filter((point) => point.value !== null && point.value !== undefined);
    return { id: criterion.criterionId, title: criterion.title, values,
      first: values[0]?.value ?? null, last: values[values.length - 1]?.value ?? null };
  });
  const table = dataTable({
    columns: [
      { key: 'title', label: 'Kriter', width: 'minmax(0, 1.6fr)' },
      { key: 'trend', label: 'Eğilim', width: '140px', cell: (row) => {
        const wrap = h('span', 'ak-sco-spark');
        wrap.append(sparkline(row.values.map((point) => point.value)));
        wrap.title = row.values.map((point) => `${point.title}: ${num(point.value)}`).join('\n');
        return wrap;
      } },
      { key: 'first', label: 'İlk', align: 'num', width: '70px', cell: (row) => scoreText(row.first, 0) },
      { key: 'last', label: 'Son', align: 'num', width: '70px', cell: (row) => badge(scoreText(row.last, 0),
        scoreTone(row.last)) },
    ],
    rows,
    dense: true,
    empty: empty('Kriter puanı yok'),
  });
  const grid = h('div', 'ak-grid-2');
  grid.append(
    card('Gelişim — ortalama puan', lineChart(points, { height: 170, valueFormat: (value) => num(value, 1) }),
      `son ${num(points.length)} değerlendirme`),
    card('Kriterler zamanla', table.node, `hesaplama ${fmtDateTime(data.computedAt)}`),
  );
  return grid;
}

function openEvaluation(app, studentId, evaluationId) {
  const panel = openDrawer(app, { title: 'Değerlendirme', subtitle: 'Yükleniyor…' });
  panel.node.classList.add('ak-sco-drawer', 'ak-sco-wide');
  const subtitle = panel.node.querySelector('.kit-drawer-sub');
  const view = loader(() => app.api.get(`/v1/students/${studentId}/evaluations/${evaluationId}`), (item) => {
    panel.setTitle(item.title || 'Değerlendirme');
    if (subtitle) {
      subtitle.textContent = [item.period?.name, item.evaluator?.displayName].filter(Boolean).join(' · ');
    }
    const nodes = [];
    if (app.can('admin_getEvaluation')) {
      const bar = h('div', 'ak-inline');
      bar.append(button('Değerlendirmeler bölümünde aç', { variant: 'ghost', onClick: () => {
        panel.close();
        app.go('evaluations', { studentId, evaluationId });
      } }));
      nodes.push(bar);
    }
    nodes.push(kpiRow([
      { label: 'Ortalama', value: scoreText(item.average) },
      { label: 'Önceki dönem', value: scoreText(item.previousAverage) },
      { label: 'Ölçek', value: item.scale?.kind === 'descriptive_4' ? 'Dört düzey' : `${num(item.scale?.min)}–${num(item.scale?.max)} puan` },
      { label: 'Paylaşım', value: shareText(item.sharedWith) },
    ]));
    nodes.push(h('div', 'ak-muted ak-sco-note', `Yayımlandı ${fmtDateTime(item.publishedAt)}`
      + `${item.updatedAt ? ` · güncellendi ${fmtDateTime(item.updatedAt)}` : ''}`));
    const labels = new Map((item.scale?.labels || []).map((entry) => [entry.value, entry.label]));
    for (const sectionItem of item.sections || []) {
      const table = dataTable({
        columns: [
          { key: 'title', label: 'Kriter', width: 'minmax(0, 1.5fr)', cell: (row) => {
            const cell = h('div', 'ak-cell-stack');
            cell.append(h('span', '', row.title));
            if (row.description) cell.append(h('small', '', row.description));
            if (row.note) cell.append(h('small', 'ak-sco-crit-note', `Not: ${row.note}`));
            return cell;
          } },
          { key: 'value', label: 'Puan', width: '150px', cell: (row) => (row.value === null || row.value === undefined
            ? h('span', 'ak-muted', 'Puanlanmadı')
            : badge(row.label || labels.get(row.value) || num(row.value), scoreTone(row.value))) },
          { key: 'previousValue', label: 'Önceki', width: '120px', cell: (row) => (row.previousValue === null
            || row.previousValue === undefined ? '—' : labels.get(row.previousValue) || num(row.previousValue)) },
          { key: 'change', label: 'Değişim', width: '110px', cell: (row) => (CHANGE[row.change]
            ? statusBadge(CHANGE, row.change) : '—') },
        ],
        rows: sectionItem.criteria || [],
        rowKey: (row) => row.criterionId,
        dense: true,
        empty: empty('Kriter yok'),
      });
      nodes.push(card(sectionItem.title, table.node));
    }
    if (item.comment) nodes.push(card('Genel yorum', h('div', 'ak-card-pad ak-sco-text', item.comment)));
    const lists = h('div', 'ak-grid-2');
    lists.append(listCard('Güçlü yönler', item.strengths), listCard('Gelişim alanları', item.improvements));
    nodes.push(lists);
    return nodes;
  }, { rows: 6, columns: 4 });
  panel.body.append(view.node);
  view.reload();
}

function listCard(title, items) {
  const list = h('ul', 'ak-sco-list');
  for (const text of items || []) list.append(h('li', '', text));
  if (!(items || []).length) list.append(h('li', 'ak-muted', 'Yazılmadı.'));
  return card(title, list);
}

function shareText(share) {
  if (share?.student && share?.guardians) return 'Öğrenci ve veliler';
  if (share?.student) return 'Yalnız öğrenci';
  if (share?.guardians) return 'Yalnız veliler';
  return 'Paylaşılmadı';
}

// ========================================================== öz değerlendirme

function selfReviewsTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const noticeSlot = h('div');
  const content = h('div', 'ak-stack');
  node.append(noticeSlot, content);
  const thisWeek = weekStart(todayIst());

  const disabled = (failure) => {
    const notice = featureNotice(app, failure, { title: 'Öz değerlendirme kurumda kapalı',
      text: 'Haftalık öz değerlendirme özelliği açılınca öğrencinin girdiği değerlendirmeler burada görünür.' });
    if (!notice) return false;
    content.hidden = true;
    noticeSlot.replaceChildren(notice);
    return true;
  };

  const week = loader(async () => {
    try {
      return await app.api.get(`/v1/students/${studentId}/self-reviews/${thisWeek}`);
    } catch (failure) {
      if (disabled(failure)) return null;
      throw failure;
    }
  }, (slot) => {
    if (!slot) return null;
    const review = slot.review;
    const box = h('div', 'ak-sco-week');
    box.append(h('b', '', `Bu hafta (${fmtDate(thisWeek)} – ${fmtDate(addDays(thisWeek, 6))})`));
    if (review) {
      box.append(badge('Gönderildi', 'good'), h('span', '', `motivasyon ${num(review.motivation)}/5 · `
        + `odak ${num(review.focus)}/5 · ${fmtDateTime(review.submittedAt)}`));
    } else {
      box.append(badge('Henüz doldurulmadı', 'warn'),
        h('span', 'ak-muted', `hatırlatma ${fmtDateTime(slot.promptAt)}`));
    }
    return box;
  }, { rows: 1, columns: 3 });

  const chartSlot = h('div');
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/self-reviews`,
    limit: 20,
    columns: [
      { key: 'weekStart', label: 'Hafta', width: '170px',
        cell: (row) => `${fmtDate(row.weekStart)} – ${fmtDate(addDays(row.weekStart, 6))}` },
      { key: 'motivation', label: 'Motivasyon', width: '100px',
        cell: (row) => badge(`${num(row.motivation)}/5`, scoreTone(row.motivation)) },
      { key: 'focus', label: 'Odak', width: '80px', cell: (row) => badge(`${num(row.focus)}/5`, scoreTone(row.focus)) },
      { key: 'struggled', label: 'Zorlandığı dersler', width: 'minmax(0, 1fr)',
        cell: (row) => (row.struggledSubjects || []).map((subject) => subject.shortName || subject.name).join(', ') || '—' },
      { key: 'nextWeekGoal', label: 'Gelecek hafta hedefi', width: 'minmax(0, 1.4fr)',
        cell: (row) => h('span', 'ak-sco-preview', row.nextWeekGoal || '—') },
    ],
    onRow: (row) => openReview(row),
    onLoaded: (rows) => paintChart(rows),
    empty: { title: 'Öz değerlendirme yok', text: 'Öğrenci henüz haftalık öz değerlendirme göndermemiş.' },
  });

  function paintChart(rows) {
    const recent = rows.slice(0, 12).reverse();
    chartSlot.replaceChildren();
    if (recent.length < 2) return;
    chartSlot.append(card('Motivasyon ve odak (haftalık, 1–5)', groupedBar(recent.map((row) => ({
      label: fmtDate(row.weekStart).slice(0, 5), values: [row.motivation, row.focus] })),
    { series: ['Motivasyon', 'Odak'], height: 170, format: (value) => num(value, 0) }),
    `son ${num(recent.length)} hafta`));
  }

  function openReview(row) {
    const panel = openDrawer(app, { title: 'Öz değerlendirme',
      subtitle: `${fmtDate(row.weekStart)} – ${fmtDate(addDays(row.weekStart, 6))}` });
    panel.node.classList.add('ak-sco-drawer');
    panel.body.append(card('', kv([
      ['Motivasyon', badge(`${num(row.motivation)}/5`, scoreTone(row.motivation))],
      ['Odak', badge(`${num(row.focus)}/5`, scoreTone(row.focus))],
      ['Zorlandığı dersler', (row.struggledSubjects || []).map((subject) => subject.name).join(', ') || '—'],
      ['Gönderildi', fmtDateTime(row.submittedAt)],
      row.updatedAt ? ['Düzeltildi', fmtDateTime(row.updatedAt)] : null,
    ])));
    for (const [title, text] of [['Bu hafta iyi giden', row.wentWell], ['Zorlandığı konu', row.struggledNote],
      ['Gelecek hafta hedefi', row.nextWeekGoal]]) {
      panel.body.append(card(title, h('div', `ak-card-pad ak-sco-text${text ? '' : ' ak-muted'}`, text || 'Yazılmadı.')));
    }
  }

  content.append(hintBox('Öz değerlendirme koça görünür; veliye yalnız öğrenci izin verirse gösterilir.'),
    week.node, chartSlot, list.node);

  return {
    node,
    reload() {
      content.hidden = false;
      noticeSlot.replaceChildren();
      week.reload();
      list.reload();
    },
    destroy: () => list.destroy(),
  };
}

