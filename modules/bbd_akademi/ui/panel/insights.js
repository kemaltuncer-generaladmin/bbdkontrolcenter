// BBD Akademi — Erken uyarı (F-A22).
//
// Üç sekme: kurallı RİSK LİSTESİ (uyarılar, her uyarıdan not / görüşme /
// veliye bildirim / çözüldü), sınıf bazında KONU VE HATA ANALİZİ
// (insights-analysis.js) ve kural EŞİKLERİ (insights-settings.js, yalnız KM).
//
// KURAL AKADEMİ'DE: uyarılar Akademi'de saatlik hesaplanır; panel yalnız
// listeyi okur ve eylemi gönderir. Eylem uçları güncel uyarıyı döndürür —
// çekmece ve liste satırı o yanıtla yerinde tazelenir, liste baştan çekilmez.
//
// SAYILAR İKİNCİ İSTEK DEĞİL: listenin ilk sayfası ile süzgeç sayıları
// (`counts`) aynı yanıttadır. Liste ve sayaç aynı sorguyu aynı anda ister;
// istemci uçuştaki aynı GET'i paylaştığı için ağa tek istek çıkar. Yazmadan
// sonra sayaçlar `limit=1` ile (tek satırlık yanıt) tazelenir.
//
// BAYRAK: `early_warning` kiracı bayrağıdır; kapalıysa uyarı uçları
// `feature_disabled` döner ve sekme sebebini söyler (Ayarlar → Özellikler).
// Eşikler bayrak kapalıyken de düzenlenebilir.
//
// DERİN BAĞLANTI (`shown(payload)`): `{earlyWarningId}` çekmeceyi açar,
// `{studentId, studentName?}` listeyi o öğrenciye süzer, `{tab}` sekme seçer
// (warnings · analysis · settings).

import { blockedButton, button, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { timeline } from '../../ui-kit/flow.js';
import {
  actionButton, confirmAction, empty, errorBox, fmtDate, fmtDateTime, formDialog, istanbulStamp, kv,
  loader, num, openDrawer, optionsOf, pagedList, runAction, sectionHead, selectBox, studentPicker,
  todayIst, toolbar,
} from './common.js';
import {
  ACTION_TYPES, RESOLVE_REASONS, RULES, SEVERITY, SEVERITY_ORDER, featureNotice, ruleName, ruleValue,
  severityBadge, statusBadgeOf, studentCell,
} from './insights-shared.js';
import { analysisTab } from './insights-analysis.js';
import { settingsTab } from './insights-settings.js';

const STATUS_FILTER = [
  { value: 'active', label: 'Etkin (açık + işlemde)' },
  { value: 'open', label: 'Açık' },
  { value: 'in_progress', label: 'İşlemde' },
  { value: 'resolved', label: 'Çözüldü' },
];
const MEETING_MODES = { in_person: 'Yüz yüze', phone: 'Telefon', online: 'Çevrimiçi' };

export function section(app) {
  loadStyles(import.meta.url, 'insights.css');
  const node = h('div', 'ak-stack ak-in');
  node.append(sectionHead('Erken uyarı', {
    subtitle: 'Kurallı risk listesi ve uyarı eylemleri, sınıf bazında en çok hata yapılan konular, '
      + 'kural eşikleri.',
  }));

  const specs = [
    { key: 'warnings', label: 'Uyarılar', reads: ['admin_listEarlyWarnings'] },
    { key: 'analysis', label: 'Konu ve hata analizi',
      reads: ['admin_getTopicAccuracyInsights', 'admin_getMistakeInsights'] },
    { key: 'settings', label: 'Eşikler', reads: ['admin_getEarlyWarningSettings'] },
  ];
  const visible = specs.filter((spec) => spec.reads.some((op) => app.can(op)));
  if (!visible.length) {
    node.append(empty('Erken uyarı ekranlarına erişim yok',
      'Uyarı listesi, konu analizi ve eşiklerden hiçbirine yetkiniz yok.'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  let pendingStudent = null;
  const builders = {
    warnings: () => warningsTab(app, { student: pendingStudent, open: (id) => openWarning(id) }),
    analysis: () => analysisTab(app),
    settings: () => settingsTab(app),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key,
    (key) => showTab(key));
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

  function openWarning(earlyWarningId) {
    warningDrawer(app, earlyWarningId, { onChange: (item) => bodies.warnings?.changed(item) });
  }

  const has = (key) => visible.some((spec) => spec.key === key);
  let started = false;
  return {
    node,
    shown(payload) {
      const student = payload?.studentId
        ? { id: payload.studentId, displayName: payload.studentName || payload.displayName || '' } : null;
      const wanted = student && has('warnings') ? 'warnings'
        : (payload?.tab && has(payload.tab) ? payload.tab : null);
      if (!started) {
        started = true;
        pendingStudent = student;
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
      } else {
        if (student && !bodies.warnings) pendingStudent = student;
        if (wanted) { tabs.select(wanted, false); showTab(wanted); }
        if (student) bodies.warnings?.setStudent(student);
      }
      if (payload?.earlyWarningId) openWarning(payload.earlyWarningId);
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ================================================================== liste

function warningsTab(app, { student: initialStudent = null, open }) {
  const node = h('div', 'ak-stack');
  const content = h('div', 'ak-stack');
  const noticeSlot = h('div');
  node.append(noticeSlot, content);

  const filters = { status: 'active', severity: '', rule: '', classGroupId: '' };
  let student = initialStudent;
  let countToken = 0;
  let lookupsLoaded = false;

  const kpiSlot = h('div', 'ak-in-kpi');
  const computed = h('div', 'ak-muted ak-in-note');

  const statusSelect = selectBox(STATUS_FILTER, 'active', (value) => { filters.status = value; reload(); },
    { label: 'Durum' });
  const severitySelect = selectBox(optionsOf(Object.fromEntries(SEVERITY_ORDER.map((key) => [key, SEVERITY[key]])),
    { empty: 'Tüm önem düzeyleri' }), '', (value) => { filters.severity = value; reload(); },
  { label: 'Önem' });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '',
    (value) => { filters.classGroupId = value; reload(); }, { label: 'Sınıf' });
  const ruleChips = chipRow(Object.keys(RULES).map((key) => ({ key, label: ruleName(key), count: '' })), null,
    (key) => { filters.rule = key || ''; reload(); });

  const picker = studentPicker(app, { placeholder: 'Öğrenci ara (ad, okul no)',
    onPick: (picked) => setStudent({ id: picked.id, displayName: picked.displayName || picked.fullName || '' }) });
  const studentChip = h('div', 'ak-in-student');

  function paintStudentChip() {
    studentChip.replaceChildren();
    studentChip.hidden = !student;
    if (!student) return;
    studentChip.append(h('span', '', `Öğrenci: ${student.displayName || 'seçili öğrenci'}`),
      button('Süzgeci kaldır', { variant: 'ghost', onClick: () => setStudent(null) }));
  }

  const query = () => ({
    status: filters.status,
    severity: filters.severity || undefined,
    rule: filters.rule || undefined,
    classGroupId: filters.classGroupId || undefined,
    studentId: student?.id || undefined,
  });

  const columns = [
    { key: 'severity', label: 'Önem', width: '86px', cell: (row) => severityBadge(row.severity) },
    { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.1fr)', cell: (row) => studentCell(row.student) },
    { key: 'title', label: 'Uyarı', width: 'minmax(0, 1.6fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', row.title), h('small', '', [ruleName(row.rule),
        row.context?.value !== null && row.context?.value !== undefined
          ? `${ruleValue(row.rule, row.context.value)} (eşik ${ruleValue(row.rule, row.context.cutoff)})` : '']
        .filter(Boolean).join(' · ')));
      return cell;
    } },
    { key: 'status', label: 'Durum', width: '96px', cell: (row) => statusBadgeOf(row.status) },
    { key: 'lastAction', label: 'Son eylem', width: 'minmax(0, 1fr)', cell: (row) => {
      const last = latestAction(row.actions);
      if (!last) return h('span', 'ak-muted', 'Henüz eylem yok');
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', last.summary || ACTION_TYPES[last.type]?.[0] || last.type),
        h('small', '', fmtDateTime(last.at)));
      return cell;
    } },
    { key: 'lastTriggeredAt', label: 'Son tetiklenme', width: '128px',
      cell: (row) => fmtDateTime(row.lastTriggeredAt) },
  ];

  const list = pagedList({
    app,
    path: '/v1/admin/early-warnings',
    query,
    columns,
    limit: 50,
    onRow: (row) => open(row.id),
    onLoaded: (rows) => {
      // Derin bağlantı adsız geldiyse öğrenci adı ilk satırdan okunur.
      if (student && !student.displayName && rows[0]?.student?.id === student.id) {
        student.displayName = rows[0].student.displayName;
        paintStudentChip();
      }
    },
    empty: { title: 'Bu süzgece uyan uyarı yok',
      text: 'Kurallar saatte bir yeniden hesaplanır; risk görülen öğrenci burada belirir.' },
  });

  content.append(kpiSlot, computed,
    toolbar(statusSelect.node, severitySelect.node, classSelect.node, picker.node, studentChip),
    ruleChips.node, list.node);
  paintStudentChip();

  function paintCounts(data) {
    const counts = data?.counts || {};
    const bySeverity = Object.fromEntries((counts.bySeverity || []).map((item) => [item.severity, item.count]));
    kpiSlot.replaceChildren(kpiRow([
      { label: 'Etkin uyarı', value: num(counts.open ?? 0), tone: counts.open ? 'bad' : '',
        title: 'Süzgeçteki açık ve işlemdeki uyarılar' },
      ...SEVERITY_ORDER.map((key) => ({ label: `${SEVERITY[key][0]} önem`, value: num(bySeverity[key] || 0),
        tone: key === 'high' && bySeverity[key] ? 'bad' : '' })),
    ]));
    const byRule = Object.fromEntries(Object.keys(RULES).map((key) => [key, 0]));
    for (const item of counts.byRule || []) byRule[item.rule] = item.count;
    ruleChips.counts(byRule);
    computed.textContent = data?.computedAt
      ? `Kurallar son hesaplandı: ${fmtDateTime(data.computedAt)} · saatte bir yeniden hesaplanır.` : '';
  }

  async function loadCounts(limit) {
    const mine = ++countToken;
    try {
      const data = await app.api.get('/v1/admin/early-warnings', { ...query(), limit });
      if (mine !== countToken) return;
      paintCounts(data);
    } catch (failure) {
      if (mine !== countToken) return;
      const notice = featureNotice(app, failure);
      if (notice) {
        content.hidden = true;
        noticeSlot.replaceChildren(notice, button('Tekrar dene', { variant: 'ghost', onClick: () => reload() }));
        return;
      }
      kpiSlot.replaceChildren(errorBox(failure, () => loadCounts(1)));
    }
  }

  function loadLookups() {
    if (lookupsLoaded) return;
    lookupsLoaded = true;
    app.lookups.options('classGroups', { empty: 'Tüm sınıflar' })
      .then((options) => classSelect.options(options)).catch(() => { lookupsLoaded = false; });
  }

  function reload() {
    loadLookups();
    content.hidden = false;
    noticeSlot.replaceChildren();
    // Aynı sorgu, aynı an: liste ve sayaç tek istekte buluşur (bkz. başlık).
    list.reload(query());
    loadCounts(50);
  }

  function setStudent(next) {
    if ((student?.id || null) === (next?.id || null)) {
      if (next?.displayName && student) { student.displayName = next.displayName; paintStudentChip(); }
      return;
    }
    student = next ? { ...next } : null;
    paintStudentChip();
    reload();
  }

  const matches = (item) => (filters.status === 'active' ? item.status !== 'resolved'
    : item.status === filters.status);

  return {
    node,
    reload,
    setStudent,
    /** Çekmecedeki eylemden dönen güncel uyarı → satır yerinde; süzgeçten çıktıysa listeden. */
    changed(item) {
      if (!item?.id) return;
      if (matches(item)) list.replace((row) => row.id === item.id, item);
      else list.remove((row) => row.id === item.id);
      loadCounts(1);
    },
    destroy() {
      countToken += 1;
      list.destroy();
      picker.destroy();
    },
  };
}

function latestAction(actions) {
  let last = null;
  for (const action of actions || []) {
    if (!last || String(action.at) > String(last.at)) last = action;
  }
  return last;
}

// ================================================================ çekmece

/** Uyarı ayrıntısı + eylem geçmişi + eylemler. `onChange(item)` her yazmadan sonra. */
export function warningDrawer(app, earlyWarningId, { onChange } = {}) {
  const panel = openDrawer(app, { title: 'Erken uyarı', subtitle: 'Yükleniyor…' });
  panel.node.classList.add('ak-in-drawer');
  const subtitle = panel.node.querySelector('.kit-drawer-sub');

  const view = loader(() => app.api.get(`/v1/admin/early-warnings/${earlyWarningId}`), (data) => paint(data),
    { rows: 5, columns: 3 });
  panel.body.append(view.node);
  view.reload();

  function apply(next, message) {
    if (!next || typeof next !== 'object') return;
    view.node.replaceChildren(...paint(next));
    if (message) app.toast(message, 'good');
    onChange?.(next);
  }

  function go(key, payload) {
    panel.close();
    app.go(key, payload);
  }

  function paint(item) {
    panel.setTitle(item.title || 'Erken uyarı');
    if (subtitle) {
      subtitle.textContent = [item.student?.displayName, item.student?.classGroup?.name, ruleName(item.rule)]
        .filter(Boolean).join(' · ');
    }
    const resolved = item.status === 'resolved';
    const bar = h('div', 'ak-inline ak-in-actions');
    if (!resolved) {
      bar.append(
        actionButton(app, 'admin_resolveEarlyWarning', 'Çözüldü olarak işaretle', { variant: 'primary',
          onClick: () => resolve(item) }),
        actionButton(app, 'admin_addEarlyWarningNote', 'Not ekle', { onClick: () => addNote() }),
        actionButton(app, 'admin_scheduleEarlyWarningMeeting', 'Görüşme planla',
          { onClick: () => scheduleMeeting(item) }),
        item.guardianCount > 0 || !app.can('admin_notifyEarlyWarningGuardians')
          ? actionButton(app, 'admin_notifyEarlyWarningGuardians', 'Veliye bildir',
            { onClick: () => notifyGuardians(item) })
          : blockedButton('Veliye bildir', 'Öğrenciye bağlı veli yok; bildirim gidecek kimse yok.'),
      );
    } else {
      bar.append(actionButton(app, 'admin_reopenEarlyWarning', 'Yeniden aç', { onClick: (event) =>
        reopen(event?.currentTarget) }));
    }
    bar.append(h('span', 'kit-spacer'),
      button('Öğrenci kartı', { variant: 'ghost', onClick: () => go('students', { studentId: item.student?.id }) }),
      button('Koçluk notları', { variant: 'ghost',
        onClick: () => go('students', { studentId: item.student?.id, tab: 'coaching' }) }));

    const nodes = [bar];
    if (resolved) {
      nodes.push(alertBox(`Çözüldü (${fmtDateTime(item.resolvedAt)}): `
        + `${RESOLVE_REASONS[item.resolution?.reason] || item.resolution?.reason || '—'}`
        + `${item.resolution?.note ? ` — ${item.resolution.note}` : ''}. Kural yeniden tetiklenirse uyarı `
        + 'kendiliğinden tekrar açılır.', 'good'));
    }

    const studentLink = h('button', 'ak-link', item.student?.displayName || '—');
    studentLink.type = 'button';
    studentLink.addEventListener('click', () => go('students', { studentId: item.student?.id }));
    const context = item.context || {};
    nodes.push(card('Uyarı', kv([
      ['Öğrenci', studentLink],
      ['Sınıf', item.student?.classGroup?.name || '—'],
      ['Kural', ruleName(item.rule)],
      ['Önem', severityBadge(item.severity)],
      ['Durum', statusBadgeOf(item.status)],
      ['Ölçülen değer', ruleValue(item.rule, context.value)],
      ['Kurum eşiği', ruleValue(item.rule, context.cutoff)],
      context.since ? ['Başlangıç', fmtDate(context.since)] : null,
      ['Açıldı', fmtDateTime(item.openedAt)],
      ['Son tetiklenme', fmtDateTime(item.lastTriggeredAt)],
      ['Bildirim gidebilecek veli', `${num(item.guardianCount)} veli`],
    ])));

    if ((context.details || []).length) {
      const list = h('ul', 'ak-in-details');
      for (const line of context.details) list.append(h('li', '', line));
      nodes.push(card('Bağlam', list));
    }

    const history = [{ title: 'Uyarı açıldı', at: fmtDateTime(item.openedAt), tone: 'bad',
      detail: item.title }];
    const ordered = [...(item.actions || [])].sort((a, b) => String(a.at).localeCompare(String(b.at)));
    for (const action of ordered) {
      const [label, tone] = ACTION_TYPES[action.type] || [action.type, ''];
      history.push({ title: label, at: fmtDateTime(action.at), tone,
        detail: [action.summary, byText(action.by),
          action.notifiedCount !== null && action.notifiedCount !== undefined
            ? `${num(action.notifiedCount)} veliye gitti` : '']
          .filter(Boolean).join(' · ') });
    }
    const historyBox = h('div', 'ak-stack');
    historyBox.append(timeline(history));
    if (ordered.some((action) => action.meetingRequestId) && app.has('requests')) {
      historyBox.append(h('div', 'ak-card-pad'));
      historyBox.lastChild.append(button('Görüşme taleplerini aç', { variant: 'ghost',
        onClick: () => go('requests', { tab: 'meeting' }) }));
    }
    nodes.push(card(`Eylem geçmişi (${num(ordered.length)})`, historyBox));
    return nodes;
  }

  // ------------------------------------------------------------- eylemler

  async function addNote() {
    const result = await formDialog(app, {
      title: 'Uyarıya not ekle',
      description: 'Not öğrencinin koç notlarına ÖZEL (yalnız kurum) olarak eklenir; öğrenci ve veli görmez.',
      fields: [{ key: 'note', label: 'Not', type: 'textarea', required: true, maxLength: 3000, wide: true,
        placeholder: 'Ör. Öğrenciyle konuşuldu; bu hafta matematik planını birlikte düzenledik.',
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter yazın.' : null) }],
      submitLabel: 'Notu ekle',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/early-warnings/${earlyWarningId}/notes`,
        { note: String(draft.note).trim() }, { key }),
    });
    apply(result, 'Not eklendi.');
  }

  async function scheduleMeeting(item) {
    const staff = await app.lookups.options('staff', { empty: 'Belirtilmedi' })
      .catch(() => [{ value: '', label: 'Belirtilmedi' }]);
    const result = await formDialog(app, {
      title: 'Görüşme planla',
      description: `${item.student?.displayName || 'Öğrencinin'} velilerine görüşme daveti gider; görüşme `
        + 'Talepler → Görüşme listesine ve ajandaya düşer.',
      value: { date: todayIst(1), start: '16:00', end: '16:30', mode: 'in_person', location: '', staffId: '',
        note: '' },
      fields: [
        { key: 'date', label: 'Tarih', type: 'date', required: true },
        { key: 'start', label: 'Başlangıç (SS:DD)', type: 'text', required: true, maxLength: 5,
          validate: (value) => (clockOf(value) ? null : 'Saat SS:DD biçiminde olmalı.') },
        { key: 'end', label: 'Bitiş (SS:DD)', type: 'text', required: true, maxLength: 5,
          validate: (value, draft) => {
            const end = clockOf(value);
            if (!end) return 'Saat SS:DD biçiminde olmalı.';
            const start = clockOf(draft.start);
            return start && end <= start ? 'Bitiş başlangıçtan sonra olmalı.' : null;
          } },
        { key: 'mode', label: 'Görüşme biçimi', type: 'select', options: optionsOf(MEETING_MODES) },
        { key: 'location', label: 'Yer / bağlantı', type: 'text', maxLength: 80,
          placeholder: 'Ör. Rehberlik odası' },
        { key: 'staffId', label: 'Görüşecek personel', type: 'select', options: staff },
        { key: 'note', label: 'Davet notu', type: 'textarea', maxLength: 300, wide: true,
          hint: 'Velilere giden davet metnine eklenir.' },
      ],
      submitLabel: 'Görüşmeyi planla',
      onSubmit: (draft, { key }) => {
        const start = clockOf(draft.start);
        const end = clockOf(draft.end);
        if (!draft.date || !start || !end) throw new Error('Tarih ve saatleri eksiksiz yazın.');
        return app.api.post(`/v1/admin/early-warnings/${earlyWarningId}/meeting`, {
          startsAt: istanbulStamp(draft.date, start),
          endsAt: istanbulStamp(draft.date, end),
          mode: draft.mode || 'in_person',
          location: String(draft.location || '').trim() || null,
          staffId: draft.staffId || null,
          note: String(draft.note || '').trim() || null,
        }, { key });
      },
    });
    apply(result, 'Görüşme planlandı; velilere davet gönderildi.');
  }

  async function notifyGuardians(item) {
    const result = await formDialog(app, {
      title: 'Veliye bildirim gönder',
      description: `${num(item.guardianCount)} veliye uygulama içi bildirim gider. Bildirim öğrencinin plan `
        + 'haftasını açar; veli uyarının kendisini görmez. Uyarı başına günde en çok bir bildirim gönderilebilir.',
      fields: [{ key: 'message', label: 'Mesaj', type: 'textarea', required: true, maxLength: 500, wide: true,
        placeholder: 'Ör. Son üç gündür çalışma kaydı görünmüyor; bu akşam birlikte planına bakabilir misiniz?',
        validate: (value) => (String(value || '').trim().length < 10 ? 'En az 10 karakter yazın.' : null) }],
      submitLabel: 'Gönder',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/early-warnings/${earlyWarningId}/notify-guardians`,
        { message: String(draft.message).trim() }, { key }),
    });
    apply(result, 'Bildirim gönderildi.');
  }

  async function resolve(item) {
    const result = await formDialog(app, {
      title: 'Çözüldü olarak işaretle',
      description: `“${item.title}” kapatılır. Kural yeniden tetiklenirse uyarı kendiliğinden tekrar açılır.`,
      value: { reason: 'contacted', note: '' },
      fields: [
        { key: 'reason', label: 'Sebep', type: 'select', required: true, options: optionsOf(RESOLVE_REASONS) },
        { key: 'note', label: 'Açıklama', type: 'textarea', maxLength: 300, wide: true },
      ],
      submitLabel: 'Çözüldü',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/early-warnings/${earlyWarningId}/resolve`,
        { reason: draft.reason, note: String(draft.note || '').trim() || null }, { key }),
    });
    apply(result, 'Uyarı çözüldü olarak işaretlendi.');
  }

  async function reopen(control) {
    const yes = await confirmAction(app, { title: 'Uyarı yeniden açılsın mı?',
      description: 'Uyarı tekrar etkin listeye döner ve eylem geçmişine “yeniden açıldı” yazılır.',
      confirmLabel: 'Yeniden aç' });
    if (!yes) return;
    const result = await runAction(app, () => app.api.post(`/v1/admin/early-warnings/${earlyWarningId}/reopen`), { button: control });
    apply(result, 'Uyarı yeniden açıldı.');
  }

  return panel;
}

/** 'km:user:…' / 'system' / ad → okunur yazar. */
function byText(by) {
  if (!by) return '';
  if (by === 'system') return 'Sistem';
  if (String(by).startsWith('km:')) return 'Kontrol Merkezi';
  return by;
}

/** Serbest saat yazımı ('9:30', '0930', '9.30') → 'HH:MM' ya da null. */
function clockOf(value) {
  const match = String(value || '').trim().replace('.', ':').match(/^(\d{1,2}):?(\d{2})$/);
  if (!match || Number(match[1]) > 23 || Number(match[2]) > 59) return null;
  return `${match[1].padStart(2, '0')}:${match[2]}`;
}
