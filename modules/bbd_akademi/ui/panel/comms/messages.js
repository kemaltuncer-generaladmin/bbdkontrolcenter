// BBD Akademi — Mesaj denetimi (F-K15 moderasyon, F-K21 KM mesaj denetimi).
//
// Koç ↔ öğrenci/veli mesajlaşması (`coach_messaging`) canlıda açık; öğrenci ve
// veli bir mesajı "Şikâyet et" ile bildirebiliyor. Bu ekran o şikâyetlerin
// işlendiği TEK yerdir:
//
//   · Şikâyetler — kuyruk (bekleyen / işlem yapılan / reddedilen), ayrıntıda
//     şikâyet edilen mesajın öncesi ve sonrası (en çok 10 + 10), karar
//     (reddet · göndereni uyar · mesajı gizle · konuşmayı kilitle) ve konuşma
//     kilidi / kilit açma. Uçlar: `admin_listMessageReports`,
//     `admin_getMessageReport`, `admin_decideMessageReport`,
//     `admin_lockMessageThread`.
//   · Denetim araması — gerekçeli (en az 10 karakter) arama: öğrenci, personel,
//     tarih (en çok 1 yıl), konuşma türü, metin, yalnız işaretliler. Konuşmayı
//     denetimde açar (silinen ve düzenlenen özgün içerik dahil) ve PDF olarak
//     dışa aktarır. Uçlar: `admin_searchMessages`, `admin_openAuditThread`,
//     `admin_exportThread`.
//   · Saklama süresi — başlıktaki düğme: yazışmaların ilişki / öğrenim
//     bitişinden kaç yıl sonra silineceği (1–10, vars. 2; Akademi gece işi
//     `messaging_retention`). Uçlar: `admin_getCoachingSettings`,
//     `admin_putCoachingSettings`.
//
// GİZLİLİK (PLATFORM §5 kural 6): yönetici yazışmayı yalnız şikâyet üzerinden
// ya da gerekçeli denetimle görür; her erişim Akademi denetim kaydına yazılır.
// Gerekçe URL'ye konmaz (POST gövdesi). İzin: `messages.moderate` /
// `messages.audit` → KM `bbd_akademi.settings`.
//
// Saf mantık `messages-logic.js`'tedir (Node ile sınanır).

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow, emptyState, hintBox, skeletonRows, tabBar } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { problem } from '../api.js';
import {
  actionButton, dateInput, errorBox, exportJob, fmtDateTime, formDialog, kv, openDrawer, optionsOf,
  pagedList, sectionHead, selectBox, statusBadge, studentPicker, toolbar,
} from '../common.js';
import {
  AUDIT_REASON_MIN, DECISIONS, DECISION_DONE, FLAGS, LOCKED_TEXT, REPORT_FILTERS, REPORT_REASONS,
  REPORT_STATUS, RETENTION_DESCRIPTION, THREAD_KINDS, THREAD_STATUS, attachmentText, auditBody, auditReason,
  decisionBody, isReported, lockBody, messagePreview, participantText, participantsText, reasonProblem,
  retentionOptions, retentionPutBody, retentionText, searchBody, searchProblem,
} from './messages-logic.js';

const REPORTS = '/v1/admin/messages/reports';

const studentCell = (student) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', student?.displayName || '—'), h('small', '', student?.classGroup?.name || ''));
  return cell;
};

function flagBadges(flags) {
  const row = h('span', 'ak-msg-flags');
  for (const flag of flags || []) row.append(statusBadge(FLAGS, flag));
  return row;
}

export function section(app) {
  loadStyles(import.meta.url, 'messages.css');
  const node = h('div', 'ak-stack');
  const retentionButton = app.can('admin_getCoachingSettings')
    ? actionButton(app, 'admin_putCoachingSettings', 'Saklama süresi',
      { variant: 'ghost', onClick: () => retentionDialog(app) })
    : null;
  node.append(sectionHead('Mesaj denetimi', {
    subtitle: 'Öğrenci ve velilerin şikâyet ettiği mesajlar ve gerekçeli mesaj denetimi. Her erişim ve karar '
      + 'Akademi denetim kaydına yazılır.',
    actions: retentionButton ? [retentionButton] : [],
  }));

  const tabs = [
    app.can('admin_listMessageReports') && { key: 'reports', label: 'Şikâyetler' },
    app.can('admin_searchMessages') && { key: 'audit', label: 'Denetim araması' },
  ].filter(Boolean);
  if (!tabs.length) {
    node.append(emptyState({ title: 'Mesaj denetimi için yetkiniz yok',
      text: 'Bu ekran kurum ayarları yetkisi ister (yöneticinizden izin isteyin).' }));
    return { node };
  }

  const panes = new Map();
  const body = h('div', 'ak-msg-body');
  let reportsView = null;
  let auditView = null;
  const open = (key, payload = null) => {
    if (!panes.has(key)) {
      const view = key === 'reports'
        ? (reportsView = reportsPane(app, { onAudit: (threadId) => { bar.select('audit'); auditView?.focusThread(threadId); } }))
        : (auditView = auditPane(app));
      panes.set(key, view);
      body.append(view.node);
    }
    for (const [name, view] of panes) view.node.hidden = name !== key;
    panes.get(key).shown?.(payload);
  };
  const bar = tabBar(tabs, tabs[0].key, (key) => open(key));
  node.append(bar.node, body);

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.messagesTab && tabs.some((tab) => tab.key === payload.messagesTab)
        ? payload.messagesTab : null;
      if (wanted) { bar.select(wanted, false); open(wanted, payload); return; }
      if (!started) { started = true; open(tabs[0].key); }
    },
    destroy() { reportsView?.destroy?.(); auditView?.destroy?.(); },
  };
}

// ------------------------------------------------------------- şikâyetler

function reportsPane(app, { onAudit }) {
  const node = h('div', 'ak-stack');
  let status = 'open';
  const chips = chipRow(REPORT_FILTERS, status, (key) => {
    status = key || 'open';
    if (!key) chips.set('open');
    list.reload();
  });
  node.append(toolbar(chips.node),
    hintBox('Öğrenci ya da veli bir mesajı "Şikâyet et" ile bildirdiğinde burada belirir. Kararı verdiğinizde '
      + 'şikâyet eden kişiye ayrıntı gösterilmez; karar ve notunuz denetim kaydına yazılır.'));

  const list = pagedList({
    app,
    path: REPORTS,
    query: () => ({ status }),
    onRow: (row) => openReport(app, row, {
      onChange: (next) => list.replace((item) => item.id === next.id, next),
      onAudit,
    }),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.1fr)', cell: (row) => studentCell(row.student) },
      { key: 'reason', label: 'Neden', width: '170px', cell: (row) => REPORT_REASONS[row.reason] || row.reason },
      { key: 'reporter', label: 'Şikâyet eden', width: 'minmax(0, 1fr)', cell: (row) => participantText(row.reporter) },
      { key: 'message', label: 'Mesaj', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', messagePreview(row.message, 90)),
          h('small', '', [THREAD_KINDS[row.threadKind], row.message?.sender ? participantText(row.message.sender) : '']
            .filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'createdAt', label: 'Şikâyet', width: '140px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'status', label: 'Durum', width: '150px', cell: (row) => (row.decision
        ? badge(DECISION_DONE[row.decision] || row.decision, row.decision === 'dismiss' ? 'dim' : 'good')
        : statusBadge(REPORT_STATUS, row.status)) },
    ],
    empty: { title: 'Bu süzgeçte şikâyet yok',
      text: 'Şikâyet edilen mesajlar burada sıraya girer.' },
  });
  node.append(list.node);
  let started = false;
  return {
    node,
    shown() { if (!started) { started = true; list.reload(); } },
    destroy() { list.destroy(); },
  };
}

function messageCard(message, { highlight = false } = {}) {
  const card = h('div', `ak-msg-item${highlight ? ' on' : ''}`);
  const head = h('div', 'ak-msg-item-head');
  head.append(h('strong', '', participantText(message.sender)), h('span', 'ak-muted', fmtDateTime(message.createdAt)));
  if (message.editedAt) head.append(badge('Düzenlendi', 'dim'));
  if (highlight) head.append(badge('Şikâyet edilen mesaj', 'bad'));
  if (message.flags?.length) head.append(flagBadges(message.flags));
  card.append(head);
  const deleted = message.deletedAt || message.body === null;
  card.append(h('p', `ak-msg-text${deleted ? ' gone' : ''}`, deleted ? 'Mesaj silindi' : (message.body || '')));
  if (message.attachments?.length) {
    card.append(h('small', 'ak-muted', message.attachments.map(attachmentText).join(' · ')));
  }
  if (message.card?.title) card.append(h('small', 'ak-muted', `Kart: ${message.card.title}`));
  return card;
}

async function openReport(app, row, { onChange, onAudit }) {
  const view = openDrawer(app, {
    title: `Şikâyet — ${row.student?.displayName || 'Öğrenci'}`,
    subtitle: `${REPORT_REASONS[row.reason] || row.reason} · ${THREAD_KINDS[row.threadKind] || ''}`,
  });
  view.body.replaceChildren(skeletonRows(6, 3));
  let report = row;

  const render = () => {
    const parts = [];
    parts.push(kv([
      ['Öğrenci', report.student?.displayName || '—'],
      ['Konuşma', THREAD_KINDS[report.threadKind] || '—'],
      ['Şikâyet eden', participantText(report.reporter)],
      ['Neden', REPORT_REASONS[report.reason] || report.reason],
      ['Açıklama', report.note || '—'],
      ['Şikâyet zamanı', fmtDateTime(report.createdAt)],
      ['Durum', report.decision ? `${DECISION_DONE[report.decision] || report.decision} — ${report.decidedBy?.displayName
        || ''} · ${fmtDateTime(report.decidedAt)}` : (REPORT_STATUS[report.status]?.[0] || report.status)],
    ]));
    const context = Array.isArray(report.context) ? report.context : null;
    parts.push(h('h4', 'ak-msg-h', 'Konuşmadan kesit'));
    if (context && context.length) {
      const stream = h('div', 'ak-msg-stream');
      for (const message of context) stream.append(messageCard(message, { highlight: isReported(message, report) }));
      parts.push(stream);
    } else if (report.message) {
      parts.push(messageCard(report.message, { highlight: true }));
    } else {
      parts.push(h('p', 'ak-muted', 'Şikâyet edilen mesaj artık yok (silinmiş).'));
    }
    const tools = h('div', 'ak-row-actions');
    if (report.status === 'open') {
      tools.append(actionButton(app, 'admin_decideMessageReport', 'Karar ver', { variant: 'primary',
        onClick: () => decide() }));
    }
    tools.append(actionButton(app, 'admin_lockMessageThread', 'Kilitle / kilidi aç', { variant: 'ghost',
      onClick: () => lockDialog(app, report.threadId).then((thread) => {
        if (thread) app.toast(thread.status === 'locked' ? LOCKED_TEXT : 'Konuşmanın kilidi açıldı.', 'good');
      }) }));
    if (app.can('admin_openAuditThread')) {
      tools.append(button('Konuşmanın tamamını denetle', { variant: 'ghost', onClick: () => {
        view.close();
        onAudit?.(report.threadId);
      } }));
    }
    parts.push(tools);
    view.body.replaceChildren(...parts);
  };

  async function decide() {
    const result = await formDialog(app, {
      title: 'Şikâyete karar ver',
      description: 'Karar şikâyet edilen mesaja uygulanır ve denetim kaydına notunuzla yazılır.',
      fields: [
        { key: 'decision', label: 'Karar', type: 'select', required: true, wide: true,
          options: Object.entries(DECISIONS).map(([value, [label]]) => ({ value, label })) },
        { key: 'note', label: 'Not (denetim kaydına yazılır)', type: 'textarea', required: true, wide: true,
          maxLength: 1000, validate: (value) => (String(value || '').trim().length < 3 ? 'En az 3 karakter.' : null) },
      ],
      value: { decision: 'dismiss', note: '' },
      submitLabel: 'Kararı kaydet',
      onSubmit: (draft, { key }) => app.api.post(`${REPORTS}/${report.id}/decision`,
        decisionBody(draft.decision, draft.note), { key }),
    });
    if (!result) return;
    report = { ...report, ...result };
    onChange?.(report);
    app.toast(DECISION_DONE[result.decision] || 'Karar kaydedildi.', 'good');
    render();
  }

  try {
    const detail = await app.api.get(`/v1/admin/messages/reports/${row.id}`);
    report = { ...row, ...detail };
    render();
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openReport(app, row, { onChange, onAudit })));
  }
}

/** Konuşma kilidi penceresi: kilitle ya da kilidi aç + gerekçe. Dönüş: güncel konuşma | null. */
export function lockDialog(app, threadId, { locked = null } = {}) {
  return formDialog(app, {
    title: 'Konuşma kilidi',
    description: `Kilitli konuşmaya yeni mesaj yazılamaz; katılımcılar "${LOCKED_TEXT}" notunu görür.`,
    fields: [
      { key: 'locked', label: 'İşlem', type: 'select', required: true, wide: true,
        options: [{ value: 'yes', label: 'Konuşmayı kilitle' }, { value: 'no', label: 'Kilidi aç' }] },
      { key: 'reason', label: 'Gerekçe (denetim kaydına yazılır)', type: 'textarea', required: true, wide: true,
        maxLength: 500, validate: (value) => (String(value || '').trim().length < 3 ? 'En az 3 karakter.' : null) },
    ],
    value: { locked: locked === false ? 'no' : 'yes', reason: '' },
    submitLabel: 'Kaydet',
    onSubmit: (draft, { key }) => app.api.post(`/v1/admin/messages/threads/${threadId}/lock`,
      lockBody(draft.locked === 'yes', draft.reason), { key }),
  });
}

// --------------------------------------------------------- denetim araması

function auditPane(app) {
  const node = h('div', 'ak-stack');
  const filters = { studentId: '', studentName: '', staffId: '', kind: '', from: '', to: '', text: '',
    flaggedOnly: false };
  const reason = { reason: '', caseRef: '' };

  // Gerekçe — her arama, konuşma açma ve dışa aktarma bu gerekçeyle denetim kaydına yazılır.
  const reasonBox = h('div', 'ak-msg-reason');
  const reasonInput = h('textarea', 'kit-textarea ak-msg-reason-input');
  reasonInput.maxLength = 500;
  reasonInput.placeholder = `Neden inceliyorsunuz? (en az ${AUDIT_REASON_MIN} karakter — ör. "Veli şikâyeti, 14.10 görüşmesi")`;
  reasonInput.setAttribute('aria-label', 'Denetim gerekçesi');
  reasonInput.addEventListener('input', () => { reason.reason = reasonInput.value; reasonError.textContent = ''; });
  const caseInput = h('input', 'kit-input');
  caseInput.type = 'text';
  caseInput.maxLength = 64;
  caseInput.placeholder = 'Vaka / şikâyet no (isteğe bağlı)';
  caseInput.setAttribute('aria-label', 'Vaka numarası');
  caseInput.addEventListener('input', () => { reason.caseRef = caseInput.value; });
  const reasonError = h('div', 'ak-msg-error');
  reasonError.setAttribute('role', 'alert');
  reasonBox.append(h('strong', '', 'Denetim gerekçesi'), reasonInput, caseInput, reasonError);
  node.append(alertBox('Yazışmalar özeldir. Denetim yalnız somut bir gerekçeyle yapılır; arama, açılan her '
    + 'konuşma ve her dışa aktarım gerekçenizle birlikte denetim kaydına yazılır.', 'warn'), reasonBox);

  // Süzgeçler.
  const studentSlot = h('div', 'ak-msg-student');
  const studentChosen = h('span', 'ak-muted', 'Bütün öğrenciler');
  const clearStudent = button('Temizle', { variant: 'ghost', onClick: () => {
    filters.studentId = ''; filters.studentName = ''; studentChosen.textContent = 'Bütün öğrenciler';
    clearStudent.hidden = true;
  } });
  clearStudent.hidden = true;
  const picker = studentPicker(app, { onPick: (student) => {
    filters.studentId = student.id;
    filters.studentName = student.displayName || student.id;
    studentChosen.textContent = filters.studentName;
    clearStudent.hidden = false;
  } });
  studentSlot.append(picker.node, studentChosen, clearStudent);
  const staffSlot = h('div');
  app.lookups.options('staff', { empty: 'Bütün personel' }).then((options) => {
    staffSlot.append(selectBox(options, '', (value) => { filters.staffId = value; }, { label: 'Personel' }).node);
  }).catch(() => {});
  const kind = selectBox(optionsOf(THREAD_KINDS, { empty: 'Bütün konuşmalar' }), '', (value) => { filters.kind = value; },
    { label: 'Konuşma türü' });
  const from = dateInput('', (value) => { filters.from = value || ''; }, 'Başlangıç');
  const to = dateInput('', (value) => { filters.to = value || ''; }, 'Bitiş');
  const text = h('input', 'kit-input');
  text.type = 'search';
  text.maxLength = 100;
  text.placeholder = 'Metinde ara (en az 3 karakter)';
  text.setAttribute('aria-label', 'Metinde ara');
  text.addEventListener('input', () => { filters.text = text.value; });
  const flagged = h('label', 'ak-inline');
  const flaggedBox = h('input', 'kit-check');
  flaggedBox.type = 'checkbox';
  flaggedBox.addEventListener('change', () => { filters.flaggedOnly = flaggedBox.checked; });
  flagged.append(flaggedBox, h('span', '', 'Yalnız işaretli (iletişim bilgisi / şikâyet)'));
  const go = actionButton(app, 'admin_searchMessages', 'Ara', { variant: 'primary', onClick: () => search() });
  const filterError = h('div', 'ak-msg-error');
  filterError.setAttribute('role', 'alert');
  const filterBox = h('div', 'ak-msg-filters');
  filterBox.append(studentSlot, toolbar(staffSlot, kind.node, from.node, to.node),
    toolbar(text, flagged, go), filterError);
  node.append(filterBox);

  // Sonuçlar (gövdede imleç — POST'tur).
  const resultSlot = h('div');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadMore() });
  more.hidden = true;
  const count = h('span', 'ak-list-count');
  const foot = h('div', 'ak-list-foot');
  foot.append(count, h('span', 'kit-spacer'), more);
  node.append(resultSlot, foot);
  resultSlot.append(emptyState({ title: 'Arama yapılmadı',
    text: 'Gerekçeyi yazıp süzgeçleri seçin, "Ara"ya basın.' }));

  let rows = [];
  let cursor = null;
  let token = 0;
  let snapshot = null;          // son aramanın süzgeç + gerekçesi (sayfalar aynı aramadır)
  const table = dataTable({
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1fr)', cell: (row) => studentCell(row.student) },
      { key: 'kind', label: 'Konuşma', width: '130px', cell: (row) => THREAD_KINDS[row.threadKind] || row.threadKind },
      { key: 'who', label: 'Katılımcılar', width: 'minmax(0, 1.2fr)', cell: (row) => participantsText(row.participants) },
      { key: 'excerpt', label: 'Mesaj', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.excerpt || (row.messageId ? '—' : 'Konuşma')),
          h('small', '', [row.sender ? participantText(row.sender) : '', row.deleted ? 'silindi' : '',
            row.edited ? 'düzenlendi' : ''].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'flags', label: 'İşaret', width: '150px', cell: (row) => flagBadges(row.flags) },
      { key: 'createdAt', label: 'Tarih', width: '140px', cell: (row) => fmtDateTime(row.createdAt) },
    ],
    rows: [],
    rowKey: (row) => `${row.threadId}:${row.messageId || ''}`,
    onRow: (row) => openAuditThread(app, row.threadId, snapshot?.reason || reason),
    empty: emptyState({ title: 'Eşleşen mesaj yok', text: 'Süzgeçleri genişletip tekrar arayın.' }),
  });

  function checkReason() {
    const message = reasonProblem(reason.reason);
    reasonError.textContent = message || '';
    if (message) reasonInput.focus();
    return !message;
  }

  async function search() {
    filterError.textContent = '';
    if (!checkReason()) return;
    const wrong = searchProblem(filters);
    if (wrong) { filterError.textContent = wrong; return; }
    snapshot = { filters: { ...filters }, reason: { ...reason } };
    rows = [];
    cursor = null;
    token += 1;
    resultSlot.replaceChildren(skeletonRows(6, 5));
    await loadMore(true);
  }

  async function loadMore(first = false) {
    if (!snapshot || (!first && !cursor)) return;
    const mine = token;
    more.disabled = true;
    try {
      const page = await app.api.post('/v1/admin/messages/search',
        searchBody(snapshot.filters, snapshot.reason, { cursor }));
      if (mine !== token) return;
      rows = rows.concat(page?.items || []);
      cursor = page?.nextCursor || null;
      table.update({ rows });
      resultSlot.replaceChildren(table.node);
      count.textContent = rows.length ? `${rows.length} sonuç${cursor ? '+' : ''}` : '';
      more.hidden = !cursor;
    } catch (failure) {
      if (mine !== token) return;
      resultSlot.replaceChildren(alertBox(problem(failure, 'Arama yapılamadı.'), 'bad'));
    } finally {
      more.disabled = false;
    }
  }

  return {
    node,
    /** Şikâyetten gelinince: konuşmayı (gerekçe yazılınca) denetimde aç. */
    focusThread(threadId) {
      if (!threadId) return;
      if (!checkReason()) {
        reasonError.textContent = `Konuşmayı açmak için gerekçe yazın (en az ${AUDIT_REASON_MIN} karakter); `
          + 'sonra "Konuşmayı aç"a basın.';
        const openButton = button('Konuşmayı aç', { variant: 'primary', onClick: () => {
          if (checkReason()) { openButton.remove(); openAuditThread(app, threadId, { ...reason }); }
        } });
        reasonBox.append(openButton);
        return;
      }
      openAuditThread(app, threadId, { ...reason });
    },
    destroy() { picker.destroy?.(); from.destroy?.(); to.destroy?.(); token += 1; },
  };
}

async function openAuditThread(app, threadId, reason) {
  const wrong = reasonProblem(reason?.reason);
  if (wrong) { app.toast(wrong, 'bad'); return; }
  const view = openDrawer(app, { title: 'Konuşma denetimi', subtitle: 'Açılış denetim kaydına yazıldı.' });
  view.body.replaceChildren(skeletonRows(8, 3));
  let thread = null;
  let items = [];
  let cursor = null;

  const render = () => {
    const parts = [];
    if (thread) {
      view.setTitle(`Konuşma — ${thread.student?.displayName || ''}`);
      const status = THREAD_STATUS[thread.status] || [thread.status, 'dim'];
      parts.push(kv([
        ['Öğrenci', thread.student?.displayName || '—'],
        ['Tür', THREAD_KINDS[thread.kind] || thread.kind],
        ['Katılımcılar', participantsText(thread.participants)],
        ['Durum', badge(status[0], status[1])],
        ['Mesaj sayısı', String(thread.messageCount ?? items.length)],
      ]));
      if (thread.status === 'locked') parts.push(alertBox(LOCKED_TEXT, 'warn'));
    }
    const tools = h('div', 'ak-row-actions');
    tools.append(
      actionButton(app, 'admin_exportThread', 'PDF olarak dışa aktar', { variant: 'ghost',
        onClick: () => exportJob(app, () => app.api.post(`/v1/admin/messages/threads/${threadId}/export`,
          { ...auditReason(reason), format: 'pdf' }), { name: 'mesaj-denetimi', label: 'Konuşma PDF\'i hazırlanıyor…' }) }),
      actionButton(app, 'admin_lockMessageThread', thread?.status === 'locked' ? 'Kilidi aç' : 'Konuşmayı kilitle',
        { variant: 'ghost', onClick: async () => {
          const next = await lockDialog(app, threadId, { locked: thread?.status !== 'locked' });
          if (next) { thread = { ...thread, ...next }; render(); }
        } }),
    );
    parts.push(tools);
    const stream = h('div', 'ak-msg-stream');
    if (!items.length) stream.append(h('p', 'ak-muted', 'Bu konuşmada mesaj yok.'));
    for (const message of items) stream.append(auditCard(message));
    parts.push(stream);
    if (cursor) {
      parts.push(button('Daha fazla mesaj', { variant: 'ghost', onClick: (event) => load(event.currentTarget) }));
    }
    view.body.replaceChildren(...parts);
  };

  async function load(control = null) {
    if (control) control.disabled = true;
    try {
      const page = await app.api.post(`/v1/admin/messages/threads/${threadId}/audit-view`,
        { ...auditReason(reason), pageSize: 100, ...(cursor ? { cursor } : {}) });
      thread = page?.thread || thread;
      items = items.concat(page?.items || []);
      cursor = page?.nextCursor || null;
      render();
    } catch (failure) {
      if (control) { control.disabled = false; app.toast(problem(failure), 'bad'); return; }
      view.body.replaceChildren(errorBox(failure, () => load()));
    }
  }
  await load();
}

function auditCard(message) {
  const shown = auditBody(message);
  const card = h('div', `ak-msg-item${shown.deleted ? ' gone' : ''}`);
  const head = h('div', 'ak-msg-item-head');
  head.append(h('strong', '', participantText(message.sender)), h('span', 'ak-muted', fmtDateTime(message.createdAt)));
  if (shown.deleted) head.append(badge(`Silindi ${fmtDateTime(message.deletedAt)}`, 'bad'));
  if (message.editedAt) head.append(badge('Düzenlendi', 'dim'));
  if (message.deliverAt && !message.delivered) head.append(badge(`İletilecek: ${fmtDateTime(message.deliverAt)}`, 'info'));
  if (message.flags?.length) head.append(flagBadges(message.flags));
  card.append(head, h('p', 'ak-msg-text', shown.text || '—'));
  if (message.attachments?.length) card.append(h('small', 'ak-muted', message.attachments.map(attachmentText).join(' · ')));
  if (message.card?.title) card.append(h('small', 'ak-muted', `Kart: ${message.card.title}`));
  if (shown.edits.length) {
    const history = h('details', 'ak-msg-edits');
    history.append(h('summary', '', `Önceki sürümler (${shown.edits.length})`));
    for (const edit of shown.edits) {
      const line = h('div', 'ak-msg-edit');
      line.append(h('small', 'ak-muted', fmtDateTime(edit.editedAt)), h('p', 'ak-msg-text', edit.previousBody));
      history.append(line);
    }
    card.append(history);
  }
  return card;
}

// ---------------------------------------------------------------- saklama süresi

async function retentionDialog(app) {
  let current;
  let body = null;
  try {
    current = await app.api.get('/v1/admin/coaching/settings');
  } catch (failure) {
    app.toast(problem(failure, 'Ayarlar okunamadı.'), 'bad');
    return;
  }
  const saved = await formDialog(app, {
    title: 'Yazışma saklama süresi',
    description: RETENTION_DESCRIPTION,
    value: { years: String(current.messageRetentionYears ?? 2) },
    fields: [
      { key: 'years', label: 'Bitişten sonra saklama', type: 'select', required: true, options: retentionOptions(),
        hint: `Şu an: ${retentionText(current)}. En az 1 yıl.` },
    ],
    onSubmit: (draft, { key }) => {
      body = retentionPutBody(current, draft.years);
      return app.api.put('/v1/admin/coaching/settings', body, { key });
    },
  });
  if (saved) {
    app.api.invalidate?.('/v1/admin/coaching/settings');
    app.toast(`Yazışma saklama süresi ${retentionText(body)} olarak kaydedildi.`, 'good');
  }
}
