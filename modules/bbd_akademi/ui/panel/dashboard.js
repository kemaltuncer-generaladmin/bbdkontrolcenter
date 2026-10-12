// BBD Akademi — Panel (F-A1) ve kurum içi anlık işler (F-A3 öğrenci çağır, F-A4 tabletler).
//
// TEK İSTEK: pano rakamlarının hepsi `admin_getDashboard` yükünden gelir;
// kutu başına ayrı istek atılmaz. Ekran açıkken 60 sn'de bir tazelenir ve
// sekme gizliyken durur (`pollLoop`).
//
// İKİ PARA AYRI (A12): kurum carisi (mavi) ile kantin (turuncu) ayrı kartlarda
// durur, hiçbir yerde toplanmaz.
//
// Tablet ve anons KM'nin kendi modülleridir; buradaki düğmeler Akademi'nin
// aynı işlemleri (mobil yöneticinin kullandığı uçlar) üzerinden geçer ki
// denetim kaydı tek yerde kalsın. Ayrıntılı tablet yönetimi Öğrenci Tablet
// Yönetimi ekranındadır.

import { button, h, loadStyles, pollLoop } from '../../ui-kit/kit.js';
import { badge, card, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  actionButton, empty, errorBox, fmtDateTime, fmtTime, formDialog, loader, money, num,
  pagedList, personName, reasonDialog, runAction, sectionHead, selectBox, statusBadge,
  studentPicker, todayIst, toolbar,
} from './common.js';

const LESSON_STATUS = { scheduled: ['Planlı', 'info'], cancelled: ['İptal', 'dim'],
  changed: ['Değişti', 'warn'] };
const KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik', break: 'Teneffüs' };
const TABLET_STATE = { free: ['Boş', 'good'], in_use: ['Kullanımda', 'info'], offline: ['Çevrimdışı', 'warn'],
  locked: ['Kilitli', 'bad'], pending: ['Eşleme bekliyor', 'dim'], revoked: ['Eşlemesi iptal', 'dim'] };
const PA_STATUS = { queued: ['Sırada', 'info'], played: ['Okundu', 'good'], failed: ['Okunamadı', 'bad'] };

const duration = (seconds) => {
  const minutes = Math.round((Number(seconds) || 0) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} sa ${minutes % 60} dk` : `${minutes} dk`;
};

export function section(app) {
  loadStyles(import.meta.url, 'dashboard.css');
  const node = h('div', 'ak-stack ak-dash');
  const stamp = h('span', 'ak-muted');
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => load() });
  node.append(sectionHead('Panel', { subtitle: 'Bugün kurumda olan biten.', actions: [stamp, refresh] }));

  const quick = toolbar(
    button('Yoklama al', { onClick: () => app.go('attendance') }),
    app.has('finance') ? button('Tahsilat al', { onClick: () => app.go('finance') }) : null,
    app.has('announcements') ? button('Duyuru yaz', { onClick: () => app.go('announcements') }) : null,
    app.has('campus') ? button('Öğrenci çağır', { onClick: () => { tabs.select('pa'); } }) : null,
    button('Öğrenci bul', { onClick: () => app.go('students') }),
  );
  const top = h('div', 'ak-stack');
  node.append(quick, top);

  const view = loader(() => app.api.get('/v1/admin/dashboard'), (data) => {
    stamp.textContent = `Son güncelleme ${new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}`;
    return render(data);
  }, { rows: 4, columns: 4 });
  top.append(view.node);

  function render(data) {
    const nodes = [];
    const attendance = data.attendance || {};
    const pending = data.pendingRequests || {};
    const pendingTotal = (pending.leave || 0) + (pending.meeting || 0) + (pending.etut || 0);
    nodes.push(kpiRow([
      { label: 'Bugünkü ders', value: `${num(data.lessons?.ongoing || 0)} sürüyor / ${num(data.lessons?.scheduled || 0)}` },
      { label: 'Yoklama', value: `${num(attendance.sessionsTaken || 0)}/${num(attendance.sessionsTotal || 0)}`,
        tone: attendance.sessionsTotal && attendance.sessionsTaken < attendance.sessionsTotal ? 'warn' : '',
        title: `${num(attendance.absentCount || 0)} öğrenci gelmedi.` },
      { label: 'Kurumda', value: `${num(data.presence?.inInstitution || 0)} / ${num(data.presence?.activeStudents || 0)}`,
        title: 'Bugün kantin harcaması ya da tablet oturumu olan öğrenciler.' },
      { label: 'Bekleyen talep', value: num(pendingTotal), tone: pendingTotal ? 'warn' : '',
        title: `İzin ${pending.leave || 0} · Görüşme ${pending.meeting || 0} · Etüt ${pending.etut || 0}` },
      { label: 'Kitap talebi', value: data.pendingBookRequests === null || data.pendingBookRequests === undefined
        ? '—' : num(data.pendingBookRequests) },
    ]));

    const money2 = h('div', 'ak-grid-2');
    if (app.has('finance')) {
      const finance = data.institutionFinance || {};
      const box = card('Kurum carisi', kpiRow([
        { label: 'Gecikmiş', value: money(finance.overdueMinor || 0), tone: finance.overdueMinor ? 'bad' : '',
          title: `${num(finance.overdueStudentCount || 0)} öğrenci` },
        { label: 'Bugün tahsil', value: money(finance.collectedTodayMinor || 0) },
      ]), `${num(finance.overdueStudentCount || 0)} öğrencide gecikme`);
      box.classList.add('ak-money-finance');
      box.append(toolbar(button('Gecikenler', { variant: 'ghost', onClick: () => app.go('finance', { tab: 'overdue' }) })));
      money2.append(box);
    }
    const canteen = data.canteen || {};
    const canteenBox = card('Kantin', kpiRow([
      { label: 'Bugün harcanan', value: money(canteen.spentTodayMinor || 0) },
      { label: 'Toplam kantin borcu', value: money(canteen.totalDebtMinor || 0) },
      { label: 'Bekleyen ödeme talebi', value: num(canteen.pendingPaymentRequestCount || 0) },
    ]), 'Kurum carisinden ayrıdır');
    canteenBox.classList.add('ak-money-canteen');
    money2.append(canteenBox);
    nodes.push(money2);

    const tablets = data.tablets || {};
    nodes.push(h('div', 'ak-muted',
      `Tabletler: ${num(tablets.free || 0)} boş · ${num(tablets.inUse || 0)} kullanımda · `
      + `${num(tablets.offline || 0)} çevrimdışı · ${num(tablets.locked || 0)} kilitli`));

    const lessons = dataTable({
      columns: [
        { key: 'time', label: 'Saat', width: '96px', cell: (row) => `${fmtTime(row.startsAt)}–${fmtTime(row.endsAt)}` },
        { key: 'class', label: 'Sınıf', cell: (row) => row.classGroup?.name || (row.kind === 'etut' ? 'Etüt' : '—') },
        { key: 'subject', label: 'Ders', width: 'minmax(0, 1.4fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', '', row.subject?.name || '—'));
          if (row.topic?.name || row.changeNote) cell.append(h('small', '', row.changeNote || row.topic?.name));
          return cell;
        } },
        { key: 'teacher', label: 'Öğretmen', cell: (row) => personName(row.teacher) },
        { key: 'room', label: 'Derslik', cell: (row) => row.room || '—' },
        { key: 'status', label: 'Durum', cell: (row) => statusBadge(LESSON_STATUS, row.status) },
        { key: 'kind', label: 'Tür', cell: (row) => KIND[row.kind] || row.kind },
      ],
      rows: data.lessons?.items || [],
      onRow: () => app.go('attendance', { date: data.date }),
      empty: empty('Bugün ders yok', 'Ders programı boş ya da bugün tatil.'),
    });
    nodes.push(card('Bugünkü dersler', lessons.node, attendance.pendingSessionIds?.length
      ? `${num(attendance.pendingSessionIds.length)} dersin yoklaması bekliyor` : ''));
    return nodes;
  }

  // ------------------------------------------------- kurum içi anlık işler
  const live = h('div', 'ak-stack');
  const tabs = tabBar([
    { key: 'tablets', label: 'Tabletler' },
    { key: 'pa', label: 'Öğrenci çağır' },
  ], 'tablets', (key) => showLive(key));
  live.append(tabs.node);
  const liveBodies = {};
  const liveSlot = h('div');
  live.append(liveSlot);
  if (app.can('admin_getTabletBoard') || app.can('admin_listPaCalls')) node.append(card('Kurum içi', live));

  function showLive(key) {
    if (!liveBodies[key]) {
      liveBodies[key] = key === 'tablets' ? tabletsView(app) : paView(app);
      liveSlot.append(liveBodies[key].node);
      liveBodies[key].reload();
    }
    for (const [name, body] of Object.entries(liveBodies)) body.node.hidden = name !== key;
  }

  let poll = null;
  let started = false;
  function load() { return view.reload(); }

  return {
    node,
    shown() {
      if (!started) {
        started = true;
        load();
        showLive(tabs.active);
        poll = pollLoop({ every: 60_000, run: () => (node.isConnected && !node.closest('[hidden]') ? load() : null) });
      }
    },
    destroy() {
      poll?.stop();
      for (const body of Object.values(liveBodies)) body.destroy?.();
    },
  };
}

// ----------------------------------------------------------------- tabletler

function tabletsView(app) {
  const node = h('div', 'ak-stack');
  const board = loader(() => app.api.get('/v1/admin/tablets'), (data) => {
    const devices = dataTable({
      columns: [
        { key: 'name', label: 'Tablet', width: 'minmax(0, 1.2fr)' },
        { key: 'state', label: 'Durum', cell: (row) => statusBadge(TABLET_STATE, row.state) },
        { key: 'battery', label: 'Pil', align: 'num', cell: (row) => (row.battery === null || row.battery === undefined
          ? '—' : `%${row.battery}${row.charging ? ' ⚡' : ''}`) },
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => (row.activeSession
          ? `${personName(row.activeSession.student)} · ${fmtTime(row.activeSession.loginAt)}` : '—') },
        { key: 'seen', label: 'Son görülme', cell: (row) => fmtDateTime(row.lastSeenAt) },
        { key: 'actions', label: '', width: '140px', cell: (row) => {
          if (!row.activeSession) return '';
          const actions = h('div', 'ak-row-actions');
          actions.append(actionButton(app, 'admin_endTabletSession', 'Oturumu bitir', { variant: 'ghost',
            onClick: () => endSession(row) }));
          return actions;
        } },
      ],
      rows: data.devices || [],
      dense: true,
      empty: empty('Tablet yok', 'Tabletler Öğrenci Tablet Yönetimi ekranından eşlenir.'),
    });
    const summary = data.summary || {};
    return [h('div', 'ak-muted', `${num(summary.free || 0)} boş · ${num(summary.inUse || 0)} kullanımda · `
      + `${num(summary.offline || 0)} çevrimdışı · ${num(summary.locked || 0)} kilitli · ${fmtDateTime(data.fetchedAt)}`),
    devices.node];
  }, { rows: 4, columns: 5 });

  const usageDate = todayIst();
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', () => usage.reload());
  app.lookups.options('classGroups', { empty: 'Tüm sınıflar' }).then((list) => classSelect.options(list)).catch(() => {});
  const usage = pagedList({
    app,
    path: '/v1/admin/tablets/usage',
    query: () => ({ date: usageDate, classGroupId: classSelect.value }),
    rowKey: (row) => row.student?.id,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.6fr)', cell: (row) => row.student?.displayName || '—' },
      { key: 'class', label: 'Sınıf', cell: (row) => row.student?.classGroup?.name || '—' },
      { key: 'total', label: 'Bugün', align: 'num', cell: (row) => duration(row.totalSeconds) },
      { key: 'active', label: 'Açık oturum', cell: (row) => (row.activeSession ? badge('Var', 'info') : '—') },
      { key: 'block', label: 'Kısıtlama', cell: (row) => (row.block?.blocked
        ? badge(`${row.block.by === 'guardian' ? 'Veli' : 'Kurum'}${row.block.blockedUntil ? ` · ${fmtDateTime(row.block.blockedUntil)}` : ''}`, 'bad')
        : '—') },
      { key: 'actions', label: '', width: '230px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_grantTabletTime', 'Ek süre', { variant: 'ghost',
          onClick: () => grant(row) }));
        if (row.block?.blocked) {
          actions.append(actionButton(app, 'admin_unblockTablet', 'Engeli kaldır', { variant: 'ghost',
            onClick: () => unblock(row) }));
        } else {
          actions.append(actionButton(app, 'admin_blockTablet', 'Engelle', { variant: 'ghost',
            onClick: () => block(row) }));
        }
        return actions;
      } },
    ],
    empty: { title: 'Bugün tablet kullanan öğrenci yok' },
  });
  node.append(board.node, toolbar(h('b', '', 'Öğrenci kullanımı (bugün)'), h('span', 'kit-spacer'), classSelect.node),
    usage.node);

  async function endSession(device) {
    const reason = await reasonDialog(app, { title: `${device.name}: oturumu bitir`,
      description: `${personName(device.activeSession?.student)} tabletten çıkarılır.`, confirmLabel: 'Bitir',
      presets: ['Ders başladı', 'Kullanım kuralı ihlali'] });
    if (!reason) return;
    const done = await runAction(app, () => app.api.post(
      `/v1/admin/tablets/sessions/${encodeURIComponent(device.activeSession.id)}/end`, { reason }),
    { success: 'Oturum bitirildi.' });
    if (done) { board.reload(); usage.reload(); }
  }

  async function grant(row) {
    const result = await formDialog(app, {
      title: `${row.student?.displayName}: ek süre`,
      value: { minutes: 30 },
      fields: [
        { key: 'minutes', label: 'Ek süre (dk)', type: 'number', required: true, min: 1, max: 240 },
        { key: 'packageName', label: 'Yalnız bu uygulama için (paket adı, isteğe bağlı)', maxLength: 255 },
        { key: 'reason', label: 'Gerekçe', required: true, maxLength: 200 },
      ],
      submitLabel: 'Ek süre ver',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/tablets/students/${row.student.id}/grant`, {
        extraSeconds: Math.round(Number(draft.minutes) * 60), packageName: draft.packageName || null,
        reason: draft.reason }, { key }),
    });
    if (result) app.toast('Ek süre verildi.', 'good');
  }

  async function block(row) {
    const result = await formDialog(app, {
      title: `${row.student?.displayName}: tableti kısıtla`,
      value: { hours: 24 },
      fields: [
        { key: 'hours', label: 'Süre (saat; boş = süresiz)', type: 'number', min: 1, max: 720 },
        { key: 'reason', label: 'Gerekçe', required: true, maxLength: 200 },
      ],
      submitLabel: 'Kısıtla',
      danger: true,
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/tablets/students/${row.student.id}/block`, {
        until: draft.hours ? new Date(Date.now() + Number(draft.hours) * 3_600_000).toISOString() : null,
        reason: draft.reason }, { key }),
    });
    if (result) { app.toast('Tablet kullanımı kısıtlandı.', 'good'); usage.reload(); }
  }

  async function unblock(row) {
    if (row.block?.by === 'guardian') {
      app.toast('Bu kısıtlamayı veli koydu; kurum veli engelini kaldıramaz.', 'bad');
      return;
    }
    const reason = await reasonDialog(app, { title: `${row.student?.displayName}: kısıtlamayı kaldır`,
      confirmLabel: 'Kaldır', danger: false });
    if (!reason) return;
    const done = await runAction(app, () => app.api.post(`/v1/admin/tablets/students/${row.student.id}/unblock`,
      { reason }), { success: 'Kısıtlama kaldırıldı.' });
    if (done) usage.reload();
  }

  return { node, reload() { board.reload(); usage.reload(); }, destroy() { usage.destroy(); } };
}

// ------------------------------------------------------------ öğrenci çağır

function paView(app) {
  const node = h('div', 'ak-stack');
  let templates = [];
  let chosen = null;
  const who = h('div', 'ak-dash-pa-who ak-muted', 'Öğrenci seçin.');
  const picker = studentPicker(app, { placeholder: 'Çağrılacak öğrenci', onPick: (student) => {
    chosen = student;
    who.textContent = `${student.displayName} · ${student.classGroup?.name || ''}`;
    who.classList.remove('ak-muted');
  } });
  const templateSelect = selectBox([{ value: '', label: 'Özel metin' }], '', () => paint());
  const text = h('input', 'kit-input ak-dash-pa-text');
  text.maxLength = 160;
  text.placeholder = 'Özel metin (en çok 160 karakter)';
  const callButton = actionButton(app, 'admin_callStudent', 'Anons et', { variant: 'primary', onClick: () => call() });
  const note = h('div', 'ak-muted', 'Aynı öğrenci 60 sn içinde tekrar çağrılamaz. Anons kurumun zil/anons sisteminden okunur.');
  node.append(toolbar(picker.node, who), toolbar(templateSelect.node, text, callButton), note);

  function paint() {
    const template = templates.find((item) => item.id === templateSelect.value);
    text.disabled = Boolean(template);
    if (template) text.value = template.text;
  }

  const history = pagedList({
    app,
    path: '/v1/admin/pa/calls',
    query: () => ({ from: todayIst(-6), to: todayIst() }),
    columns: [
      { key: 'at', label: 'Zaman', width: '130px', cell: (row) => fmtDateTime(row.at) },
      { key: 'student', label: 'Öğrenci', cell: (row) => row.student?.displayName || '—' },
      { key: 'text', label: 'Metin', width: 'minmax(0, 2fr)' },
      { key: 'by', label: 'Çağıran', cell: (row) => row.calledBy || '—' },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(PA_STATUS, row.status) },
    ],
    dense: true,
    empty: { title: 'Son 7 günde çağrı yok' },
  });
  node.append(h('b', '', 'Son çağrılar'), history.node);

  async function loadTemplates() {
    try {
      templates = (await app.api.getCached('/v1/admin/pa/templates'))?.items || [];
      templateSelect.options([{ value: '', label: 'Özel metin' },
        ...templates.map((item) => ({ value: item.id, label: item.title }))]);
    } catch (failure) {
      node.prepend(errorBox(failure));
    }
  }

  async function call() {
    if (!chosen) { app.toast('Önce öğrenciyi seçin.', 'bad'); return; }
    const template = templates.find((item) => item.id === templateSelect.value) || null;
    const message = template ? template.text : text.value.trim();
    if (!message) { app.toast('Metin yazın ya da şablon seçin.', 'bad'); return; }
    const result = await formDialog(app, {
      title: 'Anonsu onaylayın',
      description: `“${message}” — ${chosen.displayName}`,
      fields: [],
      submitLabel: 'Anons et',
      onSubmit: (_draft, { key }) => app.api.post('/v1/admin/pa/calls', {
        studentId: chosen.id, templateId: template?.id || null, text: template ? null : message,
        confirm: true }, { key }),
    });
    if (result) {
      app.toast(result.etaSeconds ? `Anons sıraya alındı (~${result.etaSeconds} sn).` : 'Anons gönderildi.', 'good');
      history.reload();
    }
  }

  return {
    node,
    reload() { loadTemplates(); history.reload(); },
    destroy() { picker.destroy(); history.destroy(); },
  };
}
