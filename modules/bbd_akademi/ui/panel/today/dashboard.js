// BBD Akademi — "Bugün" (F-A1 panel) ve kurum içi anlık işler (F-A3 öğrenci
// çağır, F-A4 tabletler).
//
// TEK İSTEK: ekranın bütün rakamları ve "Sizi bekleyenler" kartı
// `admin_getDashboard` yükünden gelir; kutu başına ayrı istek atılmaz. Tek
// istisna açık mesaj şikâyeti sayısıdır: özet yükünde yok, yalnız şikâyet
// kuyruğunu görme yetkisi (`admin_listMessageReports`) ve koçluk açıkken
// kuyruğun ilk sayfası (en çok 100) AYNI anda okunur; okunamazsa satır çizilmez,
// ekran yine açılır. Tablet ve öğrenci çağırma bölümü katlı gelir, yalnız
// açılınca yüklenir. Ekran açıkken 60 sn'de bir tazelenir ve sekme gizliyken
// durur (`pollLoop`).
//
// UYGULAMAYLA AYNI (V3): erken uyarı (`earlyWarnings`: açık + önem kırılımı) ve
// koçluk (`coaching`: cevapsız hoca sorusu, kontrol bekleyen teslim) kutuları
// React/Flutter yönetici panosuyla aynı yükten; kurumda kapalıysa ya da yetki
// yoksa Akademi `null` döner ve kutu çizilmez.
//
// YALNIZ AÇIK OLAN: kurumda kapalı özelliğin bölümü Akademi'den `null` gelir
// (kantin, tablet, kitap, kurum cari) ve kartı çizilmez.
//
// İKİ PARA AYRI (A12): kurum carisi (mavi) ile kantin (turuncu) ayrı kartlarda
// durur, hiçbir yerde toplanmaz.
//
// Tablet ve anons KM'nin kendi modülleridir; buradaki düğmeler Akademi'nin
// aynı işlemleri (mobil yöneticinin kullandığı uçlar) üzerinden geçer ki
// denetim kaydı tek yerde kalsın. Ayrıntılı tablet yönetimi Öğrenci Tablet
// Yönetimi ekranındadır.

import { button, h, loadStyles, pollLoop } from '../../../ui-kit/kit.js';
import { badge, card, kpiRow, tabBar } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import {
  actionButton, empty, errorBox, fmtDateTime, fmtTime, formDialog, loader, money, num,
  pagedList, personName, reasonDialog, runAction, sectionHead, selectBox, statusBadge,
  studentPicker, todayIst, toolbar,
} from '../common.js';

const LESSON_STATUS = { scheduled: ['Planlı', 'info'], cancelled: ['İptal', 'dim'],
  changed: ['Değişti', 'warn'] };
const KIND = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik', break: 'Teneffüs' };
const TABLET_STATE = { free: ['Boş', 'good'], in_use: ['Kullanımda', 'info'], offline: ['Çevrimdışı', 'warn'],
  locked: ['Kilitli', 'bad'], pending: ['Eşleme bekliyor', 'dim'], revoked: ['Eşlemesi iptal', 'dim'] };
const PA_STATUS = { queued: ['Sırada', 'info'], played: ['Okundu', 'good'], failed: ['Okunamadı', 'bad'] };
/** Şikâyet sayısı için okunan en çok kayıt (tek sayfa; fazlası "100+"). */
const REPORT_PEEK = 100;

/** Erken uyarı kırılımı: "2 yüksek · 3 orta" (React `earlyWarningsBreakdown` ile aynı). */
export function warningBreakdown(warnings) {
  if (!warnings) return '';
  return [[warnings.high, 'yüksek'], [warnings.medium, 'orta'], [warnings.low, 'düşük']]
    .filter(([count]) => Number(count) > 0).map(([count, label]) => `${num(count)} ${label}`).join(' · ');
}

const duration = (seconds) => {
  const minutes = Math.round((Number(seconds) || 0) / 60);
  return minutes >= 60 ? `${Math.floor(minutes / 60)} sa ${minutes % 60} dk` : `${minutes} dk`;
};

export function section(app) {
  loadStyles(import.meta.url, 'dashboard.css');
  const node = h('div', 'ak-stack ak-dash');
  const stamp = h('span', 'ak-muted');
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => load() });
  node.append(sectionHead('Bugün', { subtitle: 'Kurumda bugün olan biten ve sizi bekleyen işler.',
    actions: [stamp, refresh] }));

  // Kurum içi anlık işler (tablet, öğrenci çağırma) — kurumda açık olanlar.
  const liveTabs = [
    app.can('admin_getTabletBoard') ? { key: 'tablets', label: 'Tabletler' } : null,
    app.can('admin_listPaCalls') ? { key: 'pa', label: 'Anons' } : null,
  ].filter(Boolean);

  const quick = toolbar(
    button('Yoklama al', { onClick: () => app.go('attendance') }),
    button('Ödev ver', { onClick: () => app.go('homework') }),
    app.has('finance') && app.enabled('finance')
      ? button('Tahsilat al', { onClick: () => app.go('finance') }) : null,
    app.has('announcements') ? button('Duyuru yaz', { onClick: () => app.go('announcements') }) : null,
    app.has('campus') && liveTabs.some((item) => item.key === 'pa')
      ? button('Anons', { onClick: () => openLive('pa') }) : null,
    button('Öğrenci bul', { onClick: () => app.go('students') }),
  );
  const top = h('div', 'ak-stack');
  node.append(quick, top);

  // TEK ÖZET ÇAĞRISI: ekranın bütün rakamları `admin_getDashboard` yükünden.
  const canReports = () => app.has('settings') && app.enabled('coaching') && app.can('admin_listMessageReports');
  const view = loader(async () => {
    const [data, reports] = await Promise.all([
      app.api.get('/v1/admin/dashboard'),
      canReports()
        ? app.api.get('/v1/admin/messages/reports', { status: 'open', limit: REPORT_PEEK }).catch(() => null)
        : Promise.resolve(null),
    ]);
    return { ...data, openReports: reports ? { count: (reports.items || []).length, more: Boolean(reports.nextCursor) }
      : null };
  }, (data) => {
    stamp.textContent = `Son güncelleme ${new Date().toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' })}`;
    return render(data);
  }, { rows: 4, columns: 4 });
  top.append(view.node);

  function render(data) {
    const nodes = [];
    const attendance = data.attendance || {};
    const pending = data.pendingRequests || {};
    const pendingTotal = (pending.leave || 0) + (pending.meeting || 0) + (pending.etut || 0);
    const books = data.pendingBookRequests;
    const warnings = data.earlyWarnings || null;
    const coaching = data.coaching || null;
    nodes.push(kpiRow([
      { label: 'Bugünkü ders', value: `${num(data.lessons?.ongoing || 0)} sürüyor / ${num(data.lessons?.scheduled || 0)}` },
      { label: 'Yoklama', value: `${num(attendance.sessionsTaken || 0)}/${num(attendance.sessionsTotal || 0)}`,
        tone: attendance.sessionsTotal && attendance.sessionsTaken < attendance.sessionsTotal ? 'warn' : '',
        title: `${num(attendance.absentCount || 0)} öğrenci gelmedi.` },
      { label: 'Kurumda', value: `${num(data.presence?.inInstitution || 0)} / ${num(data.presence?.activeStudents || 0)}`,
        title: 'Bugün kantin harcaması ya da tablet oturumu olan öğrenciler.' },
      { label: 'Bekleyen talep', value: num(pendingTotal), tone: pendingTotal ? 'warn' : '',
        title: `İzin ${pending.leave || 0} · Görüşme ${pending.meeting || 0} · Etüt ${pending.etut || 0}` },
      // Kitap talepleri kurumda kapalıysa Akademi `null` döner: kutu çizilmez.
      books === null || books === undefined ? null : { label: 'Kitap talebi', value: num(books) },
      // Erken uyarı / koçluk kurumda kapalıysa ya da yetki yoksa `null`: kutu çizilmez.
      warnings ? { label: 'Erken uyarı', value: `${num(warnings.open || 0)}${warnings.high
        ? ` (${num(warnings.high)} yüksek)` : ''}`,
      tone: warnings.high ? 'bad' : (warnings.open ? 'warn' : ''),
      title: warnings.open ? `Açık uyarı: ${warningBreakdown(warnings)}` : 'Açık uyarı yok' } : null,
      coaching ? { label: 'Koçluk', value: `${num(coaching.openQuestions || 0)} soru · ${num(coaching.pendingReviews || 0)} kontrol`,
        tone: coaching.openQuestions || coaching.pendingReviews ? 'warn' : '',
        title: 'Cevapsız hoca sorusu · koçun kontrolünü bekleyen teslim' } : null,
    ].filter(Boolean)));

    nodes.push(waitingCard(pending, books, { warnings, coaching, reports: data.openReports }));

    const money2 = h('div', 'ak-grid-2');
    if (app.has('finance') && data.institutionFinance) {
      const finance = data.institutionFinance;
      const box = card('Kurum carisi', kpiRow([
        { label: 'Gecikmiş', value: money(finance.overdueMinor || 0), tone: finance.overdueMinor ? 'bad' : '',
          title: `${num(finance.overdueStudentCount || 0)} öğrenci` },
        { label: 'Bugün tahsil', value: money(finance.collectedTodayMinor || 0) },
      ]), `${num(finance.overdueStudentCount || 0)} öğrencide gecikme`);
      box.classList.add('ak-money-finance');
      box.append(toolbar(button('Gecikenler', { variant: 'ghost', onClick: () => app.go('finance', { tab: 'overdue' }) })));
      money2.append(box);
    }
    // Kantin kurumda kapalıysa `canteen` null gelir: kart çizilmez (A12: kurum
    // carisiyle hiçbir yerde toplanmaz).
    if (data.canteen) {
      const canteen = data.canteen;
      const canteenBox = card('Kantin', kpiRow([
        { label: 'Bugün harcanan', value: money(canteen.spentTodayMinor || 0) },
        { label: 'Toplam kantin borcu', value: money(canteen.totalDebtMinor || 0) },
        { label: 'Bekleyen ödeme talebi', value: num(canteen.pendingPaymentRequestCount || 0) },
      ]), 'Kurum carisinden ayrıdır');
      canteenBox.classList.add('ak-money-canteen');
      money2.append(canteenBox);
    }
    if (money2.childElementCount) nodes.push(money2);

    if (data.tablets) {
      const tablets = data.tablets;
      nodes.push(h('div', 'ak-muted',
        `Tabletler: ${num(tablets.free || 0)} boş · ${num(tablets.inUse || 0)} kullanımda · `
        + `${num(tablets.offline || 0)} çevrimdışı · ${num(tablets.locked || 0)} kilitli`));
    }

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
      empty: empty('Bugün ders yok', 'Ders programı boş ya da bugün tatil.',
        [button('Ders programını aç', { variant: 'ghost', onClick: () => app.go('timetable') })]),
    });
    nodes.push(card('Bugünkü dersler', lessons.node, attendance.pendingSessionIds?.length
      ? `${num(attendance.pendingSessionIds.length)} dersin yoklaması bekliyor` : ''));
    return nodes;
  }

  /**
   * "Sizi bekleyenler" — özet yükündeki sayılarla (+ şikâyet kuyruğunun ilk
   * sayfası). Kurumda kapalı / yetkisiz bölümün satırı hiç çizilmez; "Aç"
   * yalnız ekran açılabiliyorsa.
   */
  function waitingCard(pending, books, { warnings, coaching, reports }) {
    const rows = h('div', 'ak-dash-waiting');
    let waiting = 0;
    const line = (label, count, action, display = null) => {
      const row = h('div', 'ak-dash-wait');
      row.append(h('span', '', label), h('b', '', display || num(count || 0)));
      if (action && count) row.append(action);
      rows.append(row);
      waiting += Number(count) || 0;
    };
    const open = (label, key, payload = null) => button('Aç', { variant: 'ghost', onClick: () => app.go(key, payload),
      title: label });
    const toRequests = (kind) => (app.has('requests') ? open('Talepler', 'requests', { tab: kind }) : null);
    line('İzin talebi', pending.leave, toRequests('leave'));
    line('Görüşme talebi', pending.meeting, toRequests('meeting'));
    line('Etüt talebi', pending.etut, toRequests('etut'));
    if (books !== null && books !== undefined) {
      line('Kitap talebi', books, open('Kitap talepleri', 'books'));
    }
    if (coaching) {
      line('Cevapsız hoca sorusu', coaching.openQuestions,
        app.has('learning') && app.enabled('coach_questions') ? open('Hoca soruları', 'questions') : null);
      line('Kontrol bekleyen koç görevi', coaching.pendingReviews,
        app.enabled('coaching') ? open('Koçluk kontrol kuyruğu', 'coaching', { coachingTab: 'queue' }) : null);
    }
    if (reports) {
      line('Açık mesaj şikâyeti', reports.count, open('Mesaj denetimi', 'messages', { messagesTab: 'reports' }),
        reports.more ? `${num(reports.count)}+` : null);
    }
    if (warnings && warnings.high) {
      line('Yüksek önemli erken uyarı', warnings.high,
        app.enabled('early_warning') ? open('Erken uyarı', 'insights', { tab: 'warnings' }) : null);
    }
    return card('Sizi bekleyenler', waiting ? rows
      : empty('Bekleyen iş yok', 'Yeni izin, görüşme, etüt, kitap talebi, hoca sorusu ya da mesaj şikâyeti '
        + 'geldiğinde burada görünür.'));
  }

  // ------------------------------------------------- kurum içi anlık işler
  // Katlı gelir ve AÇILINCA yüklenir: "Bugün" ilk açılışta tek özet çağrısı yapar.
  const live = h('details', 'ak-fold');
  const liveBody = h('div', 'ak-fold-body ak-stack');
  live.append(h('summary', '', 'Kurum içi: tabletler ve öğrenci çağırma'), liveBody);
  const liveBodies = {};
  const liveSlot = h('div');
  let tabs = null;
  if (liveTabs.length) {
    tabs = tabBar(liveTabs, liveTabs[0].key, (key) => showLive(key));
    if (liveTabs.length > 1) liveBody.append(tabs.node);
    liveBody.append(liveSlot);
    live.querySelector('summary').textContent = liveTabs.length > 1 ? 'Kurum içi: tabletler ve öğrenci çağırma'
      : `Kurum içi: ${liveTabs[0].label.toLocaleLowerCase('tr-TR')}`;
    live.addEventListener('toggle', () => { if (live.open) showLive(tabs.active); });
    node.append(live);
  }

  function openLive(key) {
    if (!tabs) return;
    tabs.select(key, false);
    if (live.open) showLive(key);
    else live.open = true;
    queueMicrotask(() => live.scrollIntoView?.({ block: 'nearest' }));
  }

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

// ------------------------------------------------------------ anons (öğrenci / sınıf çağır, duyuru)
//
// Akademi README §14 madde 106 — mobil / web "Anons" ekranıyla aynı akış ve metinler: tür (Öğrenci · Sınıf ·
// Duyuru), hedef, hazır / kurum şablonu ya da özel metin, "Dinle" (önizleme; hoparlöre gitmez, ses saklanır),
// onay → anons. Ses bir kez üretilir; aynı metin tekrar çalınırken yeniden üretilmez. "Şablon olarak kaydet".

const PA_MODES = [
  { value: 'student', label: 'Öğrenci' },
  { value: 'class', label: 'Sınıf' },
  { value: 'free', label: 'Duyuru' },
];
const PA_HOLES = {
  student: '{öğrenci} {ad} {soyad} {sınıf} {öğretmen} {saat}',
  class: '{sınıf} {öğretmen} {saat} {derslik} {ders}',
  free: '{saat}',
};
const PA_KIND = { student: 'Öğrenci', class: 'Sınıf', free: 'Duyuru' };

function paTarget(row) {
  if (row.kind === 'class') return `${row.group?.name || ''} · Sınıf`;
  if (row.kind === 'free') return 'Duyuru';
  return row.student?.displayName || '—';
}

function paView(app) {
  const node = h('div', 'ak-stack');
  let mode = 'student';
  let targets = null;
  let templates = [];
  let chosen = null;
  const modeSelect = selectBox(PA_MODES, 'student', (value) => {
    mode = value;
    templateSelect.set('');
    text.value = '';
    paint();
    loadTemplates();
  }, { label: 'Anons türü' });
  const who = h('div', 'ak-dash-pa-who ak-muted', 'Öğrenci seçin.');
  const picker = studentPicker(app, { placeholder: 'Çağrılacak öğrenci', onPick: (student) => {
    chosen = student;
    who.textContent = `${student.displayName} · ${student.classGroup?.name || ''}`;
    who.classList.remove('ak-muted');
  } });
  const classSelect = selectBox([{ value: '', label: 'Sınıf seçin' }], '', () => paint(), { label: 'Sınıf' });
  const teacherSelect = selectBox([{ value: '', label: 'Dersin öğretmeni / ben' }], '', null, { label: 'Öğretmen' });
  const templateSelect = selectBox([{ value: '', label: 'Özel metin' }], '', () => paint(), { label: 'Şablon' });
  const text = h('input', 'kit-input ak-dash-pa-text');
  text.maxLength = 160;
  text.placeholder = 'Özel metin (en çok 160 karakter)';
  const holes = h('div', 'ak-muted', '');
  const hint = h('div', 'ak-muted', '');
  const player = h('audio');
  player.controls = true;
  player.hidden = true;
  const listenButton = actionButton(app, 'admin_previewPa', 'Dinle', { onClick: () => listen() });
  const saveButton = actionButton(app, 'admin_createPaTemplate', 'Şablon olarak kaydet', { onClick: () => saveTemplate() });
  const buttons = {
    student: actionButton(app, 'admin_callStudent', 'Anons et', { variant: 'primary', onClick: () => call() }),
    class: actionButton(app, 'admin_callClass', 'Sınıfı çağır', { variant: 'primary', onClick: () => call() }),
    free: actionButton(app, 'admin_announcePa', 'Duyur', { variant: 'primary', onClick: () => call() }),
  };
  const studentRow = toolbar(picker.node, who);
  const classRow = toolbar(classSelect.node, teacherSelect.node);
  const note = h('div', 'ak-muted', 'Aynı anons 60 sn içinde tekrar yapılamaz. Ses bir kez üretilir ve saklanır; '
    + '“Dinle” hoparlöre gitmez.');
  node.append(toolbar(modeSelect.node), studentRow, classRow,
    toolbar(templateSelect.node, text, listenButton, buttons.student, buttons.class, buttons.free),
    holes, hint, player, toolbar(saveButton), note);

  function paint() {
    studentRow.hidden = mode !== 'student';
    classRow.hidden = mode !== 'class';
    for (const [key, control] of Object.entries(buttons)) control.hidden = key !== mode;
    const template = templates.find((item) => item.id === templateSelect.value);
    text.disabled = Boolean(template);
    if (template) text.value = template.text;
    holes.textContent = `Yer tutucular: ${PA_HOLES[mode]}`;
    const klass = targets?.classes?.find((item) => item.id === classSelect.value);
    hint.textContent = mode === 'class' && klass?.session
      ? `Şimdiki ders: ${fmtDateTime(klass.session.startsAt)} · ${klass.session.teacherName || '—'}`
        + `${klass.session.room ? ` · ${klass.session.room}` : ''}`
      : '';
    saveButton.hidden = Boolean(template) || !text.value.trim();
    player.hidden = true;
  }
  text.addEventListener('input', () => { saveButton.hidden = !text.value.trim(); player.hidden = true; });

  const history = pagedList({
    app,
    path: '/v1/admin/pa/calls',
    query: () => ({ from: todayIst(-6), to: todayIst(), kind: 'all' }),
    columns: [
      { key: 'at', label: 'Zaman', width: '130px', cell: (row) => fmtDateTime(row.at) },
      { key: 'target', label: 'Kime', cell: (row) => paTarget(row) },
      { key: 'text', label: 'Metin', width: 'minmax(0, 2fr)' },
      { key: 'by', label: 'Çağıran', cell: (row) => row.calledBy || '—' },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(PA_STATUS, row.status) },
    ],
    dense: true,
    empty: { title: 'Son 7 günde anons yok' },
  });
  node.append(h('b', '', 'Son anonslar'), history.node);

  async function loadTargets() {
    try {
      targets = await app.api.getCached('/v1/admin/pa/targets');
      classSelect.options([{ value: '', label: 'Sınıf seçin' },
        ...(targets?.classes || []).map((item) => ({ value: item.id, label: item.name }))]);
      teacherSelect.options([{ value: '', label: 'Dersin öğretmeni / ben' },
        ...(targets?.teachers || []).map((item) => ({ value: item.id, label: item.callName }))]);
      modeSelect.node.querySelector('option[value="free"]').hidden = !targets?.freeText;
    } catch (failure) {
      node.prepend(errorBox(failure));
    }
    paint();
  }

  async function loadTemplates() {
    try {
      templates = (await app.api.getCached('/v1/admin/pa/templates', { kind: mode }))?.items || [];
      templateSelect.options([{ value: '', label: 'Özel metin' },
        ...templates.map((item) => ({ value: item.id, label: item.title }))]);
    } catch (failure) {
      node.prepend(errorBox(failure));
    }
    paint();
  }

  function body() {
    const template = templates.find((item) => item.id === templateSelect.value) || null;
    const message = template ? null : text.value.trim();
    return { templateId: template?.id || null, text: message || null, display: template ? template.text : message };
  }

  function target() {
    if (mode === 'student') return chosen ? { studentId: chosen.id, label: chosen.displayName } : null;
    if (mode === 'class') {
      const klass = targets?.classes?.find((item) => item.id === classSelect.value);
      return klass ? { groupId: klass.id, label: klass.name } : null;
    }
    return { label: 'Duyuru' };
  }

  async function listen() {
    const t = target();
    const b = body();
    if (!t) { app.toast(mode === 'class' ? 'Önce sınıfı seçin.' : 'Önce öğrenciyi seçin.', 'bad'); return; }
    if (!b.display) { app.toast('Metin yazın ya da şablon seçin.', 'bad'); return; }
    const result = await runAction(app, () => app.api.post('/v1/admin/pa/preview', {
      kind: mode, studentId: t.studentId || null, groupId: t.groupId || null,
      teacherId: teacherSelect.value || null, templateId: b.templateId, text: b.text,
    }), { button: listenButton });
    if (result?.audio) {
      player.src = result.audio;
      player.hidden = false;
      hint.textContent = `“${result.text}”`;
      player.play().catch(() => {});
    }
  }

  async function call() {
    const t = target();
    const b = body();
    if (!t) { app.toast(mode === 'class' ? 'Önce sınıfı seçin.' : 'Önce öğrenciyi seçin.', 'bad'); return; }
    if (!b.display) { app.toast('Metin yazın ya da şablon seçin.', 'bad'); return; }
    const path = mode === 'student' ? '/v1/admin/pa/calls'
      : mode === 'class' ? '/v1/admin/pa/class-calls' : '/v1/admin/pa/announcements';
    const result = await formDialog(app, {
      title: 'Anonsu onaylayın',
      description: `“${b.display}” — ${t.label}`,
      fields: [],
      submitLabel: mode === 'free' ? 'Duyur' : 'Anons et',
      onSubmit: (_draft, { key }) => app.api.post(path, {
        ...(t.studentId ? { studentId: t.studentId } : {}),
        ...(t.groupId ? { groupId: t.groupId } : {}),
        ...(mode !== 'free' ? { teacherId: teacherSelect.value || null } : {}),
        templateId: b.templateId, text: b.text, confirm: true }, { key }),
    });
    if (result) {
      app.toast(result.etaSeconds ? `Anons sıraya alındı (~${result.etaSeconds} sn).` : 'Anons gönderildi.', 'good');
      history.reload();
    }
  }

  async function saveTemplate() {
    const message = text.value.trim();
    if (!message) return;
    const result = await formDialog(app, {
      title: 'Şablon olarak kaydet',
      description: `“${message}” (${PA_KIND[mode]} şablonu)`,
      fields: [{ key: 'title', label: 'Şablon adı', required: true, maxLength: 60 }],
      submitLabel: 'Kaydet',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/pa/templates',
        { title: draft.title, text: message, kind: mode }, { key }),
    });
    if (result) {
      app.api.invalidate('/v1/admin/pa/templates');
      app.toast('Şablon kaydedildi.', 'good');
      await loadTemplates();
      templateSelect.set(result.id);
      paint();
    }
  }

  paint();
  return {
    node,
    reload() { loadTargets(); loadTemplates(); history.reload(); },
    destroy() { picker.destroy(); history.destroy(); },
  };
}
