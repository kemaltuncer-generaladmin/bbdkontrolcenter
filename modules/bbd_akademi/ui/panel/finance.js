// BBD Akademi — Kurum finansı (F-A11, F-P5).
//
// KANTİNDEN TAMAMEN AYRI (A12). Burada yalnız KURUM CARİSİ vardır: taksit,
// kitap, materyal ve diğer kalemler; nakit / havale / kart tahsilatı. Kantin
// borcu, kantin tahsilatı ve kantin kart ödemesi bu ekranda HİÇ görünmez ve
// hiçbir toplam kantinle toplanmaz. Renk dili de ayrıdır (mavi çizgi).
//
// İŞ KURALI AKADEMİ'DE: FIFO dağıtım, artan alacak, makbuz numarası, taksit
// takvimi, indirim dağıtımı, ayrılma / iade hesabı sunucuda yapılır (indirim,
// ayrılma, iade, aylık özet ve dışa aktarım: `finance-closing.js`). Panel yalnız niyeti gönderir ve sunucunun
// sonucunu gösterir; ekrandaki "önizleme" yalnız yol gösterir.
//
// PARA İŞLEMLERİ İDEMPOTENTTİR: tahsilat, iptal, plan ve kalem pencereleri
// açıldığı anda tek bir `Idempotency-Key` alır (formDialog); çift tıklama ya da
// ağ tekrarı ikinci tahsilat açmaz. Tahsilat iptali ayrıca PIN teyidi ister
// (step-up): PIN Akademi'ye gitmez, KM doğrular.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { AkademiError } from './api.js';
import {
  actionButton, addDays, empty, errorBox, fmtDate, fmtDateTime, formDialog, kv, loader, money,
  moneyText, num, openDrawer, optionsOf, pagedList, parseMoney, personName, pinStepUp,
  reasonDialog, runAction, searchBox, sectionHead, selectBox, statusBadge, studentPicker,
  todayIst, toolbar,
} from './common.js';
import { documentButtons, previewAkademiPdf } from './documents.js';
import { receiptDoc, statementDoc } from './documents-logic.js';
import {
  STATEMENT_KINDS, applyPlanDiscount, closingCards, closingNote, discountBody, discountFields, discountLine,
  exportButton, monthlyTab, printPlanAgreement, recordRefund, withdrawAccount,
} from './finance-closing.js';

export const METHODS = { cash: 'Nakit', transfer: 'Havale / EFT', card: 'Kart (POS)', other: 'Diğer',
  online_card: 'Uygulamadan kart' };
const CHARGE_KINDS = { installment: 'Taksit', down_payment: 'Peşinat', book: 'Kitap',
  material: 'Materyal', other: 'Diğer' };
const CHARGE_STATUS = { open: ['Açık', 'info'], partially_paid: ['Kısmi ödendi', 'warn'],
  paid: ['Ödendi', 'good'], overdue: ['Gecikmiş', 'bad'], cancelled: ['İptal', 'dim'] };
const PAYMENT_STATUS = { completed: ['Tamamlandı', 'good'], voided: ['İptal edildi', 'dim'] };
const PLAN_STATUS = { active: ['Aktif', 'info'], completed: ['Tamamlandı', 'good'],
  cancelled: ['İptal', 'dim'] };
const INTERVALS = { monthly: 'Aylık', biweekly: 'İki haftada bir', weekly: 'Haftalık' };
const ACCOUNT_STATES = { overdue: 'Gecikmesi olan', open: 'Açık borcu olan', clear: 'Borcu yok',
  credit: 'Alacaklı' };
const VIA = { app: 'Mobil yönetici', km_panel: 'Kontrol Merkezi', online: 'Uygulamadan ödeme' };

const studentCell = (student) => {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', student?.displayName || '—'),
    h('small', '', [student?.externalRef, student?.classGroup?.name].filter(Boolean).join(' · ')));
  return node;
};

export function section(app) {
  loadStyles(import.meta.url, 'finance.css');
  const node = h('div', 'ak-stack ak-fin');
  const disposers = [];
  let started = false;

  // ---------------------------------------------------------- başlık + özet
  const range = dateRange({ start: todayIst(-29), end: todayIst(), label: 'Dönem',
    onChange: () => loadSummary() });
  disposers.push(() => range.destroy?.());
  const kpiSlot = h('div', 'ak-money-finance ak-fin-kpi');
  node.append(sectionHead('Kurum finansı', {
    subtitle: 'Kurum cari hesapları: taksit, kalem ve tahsilat. Kantin hesabı bu ekranda yer almaz.',
    actions: [range.node, exportButton(app, range)],
  }), kpiSlot);

  async function loadSummary() {
    const { start, end } = range.get();
    try {
      const summary = await app.api.get('/v1/admin/finance/summary', { from: start, to: end });
      const byMethod = (summary.byMethod || []).filter((row) => row.count)
        .map((row) => `${METHODS[row.method] || row.method}: ${money(row.amountMinor)} (${num(row.count)})`)
        .join(' · ');
      kpiSlot.replaceChildren(kpiRow([
        { label: 'Açık bakiye', value: money(summary.openMinor) },
        { label: 'Gecikmiş', value: money(summary.overdueMinor), tone: summary.overdueMinor ? 'bad' : '' },
        { label: 'Alacak (öğrenci lehine)', value: money(summary.creditMinor || 0) },
        { label: 'Dönemde tahsil edilen', value: money(summary.collectedMinor),
          title: byMethod || 'Bu dönemde tahsilat yok.' },
      ]));
      if (byMethod) kpiSlot.append(h('div', 'ak-muted ak-fin-methods', byMethod));
    } catch (failure) {
      kpiSlot.replaceChildren(errorBox(failure, loadSummary));
    }
  }

  // ------------------------------------------------------------- sekmeler
  const bodies = {};
  const tabs = tabBar([
    { key: 'accounts', label: 'Cari hesaplar' },
    { key: 'payments', label: 'Tahsilatlar' },
    { key: 'overdue', label: 'Gecikenler' },
    { key: 'monthly', label: 'Aylık rapor' },
    { key: 'templates', label: 'Plan şablonları' },
  ], 'accounts', (key) => showTab(key));
  const tabSlot = h('div');
  node.append(tabs.node, tabSlot);

  const builders = {
    accounts: () => accountsTab(app, { openAccount }),
    payments: () => paymentsTab(app, { openAccount }),
    overdue: () => overdueTab(app, { openAccount }),
    monthly: () => monthlyTab(app),
    templates: () => templatesTab(app),
  };

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      tabSlot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  function openAccount(studentId) {
    accountDrawer(app, studentId, {
      onChange: () => {
        loadSummary();
        for (const body of Object.values(bodies)) body.stale = true;
        bodies.accounts?.reload();
      },
    });
  }

  return {
    node,
    shown(payload) {
      if (!started) {
        started = true;
        loadSummary();
        showTab('accounts');
      }
      if (payload?.tab && builders[payload.tab]) tabs.select(payload.tab);
      if (payload?.studentId) openAccount(payload.studentId);
    },
    destroy() {
      for (const fn of disposers) fn();
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// --------------------------------------------------------------- hesaplar

function accountsTab(app, { openAccount }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', classGroupId: '', state: '' };
  const search = searchBox({ placeholder: 'Öğrenci ara', onSearch: (q) => { filters.q = q; reload(); } });
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  }, { label: 'Sınıf' });
  const stateSelect = selectBox(optionsOf(ACCOUNT_STATES, { empty: 'Tüm hesaplar' }), '', (value) => {
    filters.state = value; reload();
  }, { label: 'Durum' });
  app.lookups.options('classGroups', { empty: 'Tüm sınıflar' })
    .then((options) => classSelect.options(options)).catch(() => {});

  const picker = studentPicker(app, { placeholder: 'Hesabı aç: öğrenci ara',
    onPick: (student) => openAccount(student.id) });
  node.append(toolbar(search.node, classSelect.node, stateSelect.node, h('span', 'kit-spacer'),
    picker.node));

  const list = pagedList({
    app,
    path: '/v1/admin/finance/accounts',
    query: () => filters,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => studentCell(row.student) },
      { key: 'open', label: 'Açık bakiye', align: 'num', cell: (row) => money(row.openBalanceMinor) },
      { key: 'overdue', label: 'Gecikmiş', align: 'num', cell: (row) => (row.overdueMinor
        ? badge(money(row.overdueMinor), 'bad') : '—') },
      { key: 'credit', label: 'Alacak', align: 'num', cell: (row) => (row.creditMinor ? money(row.creditMinor) : '—') },
      { key: 'next', label: 'Sonraki vade', cell: (row) => fmtDate(row.nextDueDate) },
    ],
    rowKey: (row) => row.student?.id,
    onRow: (row) => openAccount(row.student.id),
    empty: { title: 'Bu süzgece uyan cari hesap yok', text: 'Süzgeçleri temizleyin ya da öğrenci arayın.' },
  });
  node.append(list.node);
  function reload() { return list.reload(); }
  return { node, reload, destroy() { search.destroy(); picker.destroy(); list.destroy(); } };
}

// ------------------------------------------------------------ tahsilatlar

function paymentsTab(app, { openAccount }) {
  const node = h('div', 'ak-stack');
  const filters = { from: todayIst(-29), to: todayIst(), method: '', status: '' };
  const range = dateRange({ start: filters.from, end: filters.to, label: 'Tahsilat tarihi',
    onChange: (value) => {
      filters.from = value.start || filters.from;
      filters.to = value.end || filters.to;
      reload();
    } });
  const methodSelect = selectBox(optionsOf(METHODS, { empty: 'Tüm yöntemler' }), '', (value) => {
    filters.method = value; reload();
  });
  const statusSelect = selectBox([{ value: '', label: 'Tümü' }, { value: 'completed', label: 'Tamamlanan' },
    { value: 'voided', label: 'İptal edilen' }], '', (value) => { filters.status = value; reload(); });
  node.append(toolbar(range.node, methodSelect.node, statusSelect.node));

  let list;
  // Tahsilat satırı öğrenciyi taşımaz; öğrenci makbuzda görünür (satır başına
  // ek istek atılmaz). Hesaba geçiş makbuz çekmecesindeki düğmeden.
  const columns = paymentColumns(app, {
    onReceipt: (row) => receiptDrawer(app, row.id, { openAccount }),
    onVoid: (row) => voidPayment(app, row, (next) => list.replace((item) => item.id === next.id, next)),
  });
  list = pagedList({
    app,
    path: '/v1/admin/finance/payments',
    query: () => filters,
    columns,
    empty: { title: 'Bu dönemde tahsilat yok', text: 'Tarih aralığını genişletin.' },
  });
  node.append(list.node);
  function reload() { return list.reload(); }
  return { node, reload, destroy() { range.destroy?.(); list.destroy(); } };
}

function paymentColumns(app, { onReceipt, onVoid }) {
  const columns = [
    { key: 'receiptNo', label: 'Makbuz', width: '120px' },
    { key: 'paidAt', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.paidAt) },
  ];
  columns.push(
    { key: 'method', label: 'Yöntem', cell: (row) => METHODS[row.method] || row.method },
    { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    { key: 'credit', label: 'Alacağa', align: 'num', cell: (row) => (row.creditAddedMinor ? money(row.creditAddedMinor) : '—') },
    { key: 'status', label: 'Durum', cell: (row) => statusBadge(PAYMENT_STATUS, row.status) },
    { key: 'actions', label: '', width: '170px', cell: (row) => {
      const actions = h('div', 'ak-row-actions');
      actions.append(button('Makbuz', { variant: 'ghost', onClick: () => onReceipt(row) }));
      if (row.status === 'completed') {
        actions.append(actionButton(app, 'admin_voidPayment', 'İptal', { variant: 'ghost',
          onClick: () => onVoid(row) }));
      }
      return actions;
    } },
  );
  return columns;
}

async function voidPayment(app, payment, onDone) {
  const extra = payment.method === 'online_card'
    ? ' Uygulamadan kartla yapılan ödemenin iptali kartı İADE ETMEZ; iade ayrıca yapılır.' : '';
  const reason = await reasonDialog(app, {
    title: `${payment.receiptNo} makbuzlu tahsilatı iptal et`,
    description: `${money(payment.amountMinor)} tutarındaki tahsilatın dağıtımları geri alınır ve kalemler `
      + `yeniden açılır.${extra}`,
    confirmLabel: 'Devam et',
    presets: ['Yanlış öğrenciye kaydedildi', 'Tutar hatalı girildi', 'Mükerrer kayıt'],
  });
  if (!reason) return;
  const token = await pinStepUp(app, 'admin_voidPayment', {
    title: 'Tahsilat iptali — PIN teyidi',
    description: `${payment.receiptNo} · ${money(payment.amountMinor)}. Devam etmek için kendi PIN'inizi girin.`,
    confirmLabel: 'Tahsilatı iptal et',
  });
  if (!token) return;
  const key = `void-${payment.id}`.slice(0, 120);
  const result = await runAction(app, () => app.api.post(
    `/v1/admin/finance/payments/${payment.id}/void`, { reason }, { key, stepUpToken: token }),
  { success: 'Tahsilat iptal edildi.' });
  if (result) onDone?.(result);
}

// --------------------------------------------------------------- gecikenler

function overdueTab(app, { openAccount }) {
  const node = h('div', 'ak-stack');
  const filters = { classGroupId: '', minDaysOverdue: 1, asOf: todayIst() };
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; reload();
  });
  app.lookups.options('classGroups', { empty: 'Tüm sınıflar' })
    .then((options) => classSelect.options(options)).catch(() => {});
  const daySelect = selectBox([1, 7, 15, 30, 60].map((value) => ({ value, label: `${value}+ gün` })),
    1, (value) => { filters.minDaysOverdue = Number(value); reload(); });
  let list;
  const remindSelected = actionButton(app, 'admin_sendFinanceReminders', 'Seçilenlere hatırlat', {
    onClick: () => remind(list.table.selection()) });
  const remindAll = actionButton(app, 'admin_sendFinanceReminders', 'Tümüne hatırlat', {
    variant: 'primary', onClick: () => remind([]) });
  node.append(toolbar(classSelect.node, daySelect.node, h('span', 'kit-spacer'), remindSelected, remindAll));

  list = pagedList({
    app,
    path: '/v1/admin/finance/overdue',
    query: () => filters,
    selectable: true,
    rowKey: (row) => row.student?.id,
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = studentCell(row.student);
        cell.style.cursor = 'pointer';
        cell.addEventListener('click', () => openAccount(row.student.id));
        return cell;
      } },
      { key: 'guardians', label: 'Veli', width: 'minmax(0, 1.4fr)', cell: (row) => (row.guardians || [])
        .map((guardian) => personName(guardian.guardian || guardian)).join(', ') || '—' },
      { key: 'amount', label: 'Gecikmiş', align: 'num', cell: (row) => badge(money(row.overdueMinor), 'bad') },
      { key: 'oldest', label: 'En eski vade', cell: (row) => fmtDate(row.oldestDueDate) },
      { key: 'days', label: 'Gün', align: 'num', cell: (row) => num(row.daysOverdue) },
      { key: 'count', label: 'Kalem', align: 'num', cell: (row) => num(row.chargeCount) },
      { key: 'reminded', label: 'Son hatırlatma', cell: (row) => fmtDateTime(row.lastReminderAt) },
    ],
    empty: { title: 'Geciken ödeme yok', text: 'Bu süzgece uyan gecikmiş kurum borcu bulunmuyor.' },
  });
  node.append(list.node);

  async function remind(studentIds) {
    const count = studentIds.length;
    const result = await formDialog(app, {
      title: count ? `${num(count)} öğrencinin velisine hatırlatma` : 'Bütün gecikenlere hatırlatma',
      description: count ? 'Seçilen öğrencilerin velilerine vade hatırlatması gönderilir.'
        : `${filters.minDaysOverdue}+ gün gecikmesi olan bütün öğrencilerin velilerine gönderilir.`,
      fields: [{ key: 'message', label: 'Ek not (isteğe bağlı)', type: 'textarea', maxLength: 300, wide: true }],
      submitLabel: 'Gönder',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/finance/reminders', {
        studentIds: count ? studentIds : undefined,
        minDaysOverdue: filters.minDaysOverdue,
        message: draft.message || null,
      }, { key }),
    });
    if (result) {
      app.toast(`${num(result.notifiedCount ?? 0)} veliye hatırlatma gönderildi.`, 'good');
      list.reload();
    }
  }

  function reload() { return list.reload(); }
  return { node, reload, destroy() { list.destroy(); } };
}

// --------------------------------------------------------- plan şablonları

function templatesTab(app) {
  const node = h('div', 'ak-stack');
  const add = actionButton(app, 'admin_createPlanTemplate', 'Yeni şablon', { variant: 'primary',
    onClick: () => edit(null) });
  node.append(toolbar(h('span', 'ak-muted', 'Sık kullanılan taksit düzenleri; plan oluştururken seçilir.'),
    h('span', 'kit-spacer'), add));
  const table = dataTable({
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 2fr)' },
      { key: 'total', label: 'Toplam', align: 'num', cell: (row) => money(row.totalMinor) },
      { key: 'down', label: 'Peşinat', align: 'num', cell: (row) => (row.downPaymentMinor ? money(row.downPaymentMinor) : '—') },
      { key: 'count', label: 'Taksit', align: 'num', cell: (row) => num(row.installmentCount) },
      { key: 'interval', label: 'Aralık', cell: (row) => INTERVALS[row.interval] || row.interval },
      { key: 'day', label: 'Vade günü', align: 'num', cell: (row) => (row.firstDueDay ? `Ayın ${row.firstDueDay}'i` : '—') },
      { key: 'actions', label: '', width: '150px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(
          actionButton(app, 'admin_updatePlanTemplate', 'Düzenle', { variant: 'ghost', onClick: () => edit(row) }),
          actionButton(app, 'admin_deletePlanTemplate', 'Sil', { variant: 'ghost', onClick: () => remove(row) }),
        );
        return actions;
      } },
    ],
    rows: [],
    empty: empty('Şablon yok', 'Örneğin "Yıllık 10 taksit" şablonu ekleyin.'),
  });
  const view = loader(() => app.api.get('/v1/admin/finance/plan-templates'), (data) => {
    table.update({ rows: data?.items || [] });
    return table.node;
  });
  node.append(view.node);

  async function edit(row) {
    const saved = await formDialog(app, {
      title: row ? 'Şablonu düzenle' : 'Yeni taksit şablonu',
      fields: templateFields(),
      value: row ? { ...row, interval: row.interval || 'monthly' } : { interval: 'monthly', downPaymentMinor: 0 },
      onSubmit: (draft, { key }) => {
        const body = templateBody(draft);
        return row ? app.api.put(`/v1/admin/finance/plan-templates/${row.id}`, body)
          : app.api.post('/v1/admin/finance/plan-templates', body, { key });
      },
    });
    if (saved) {
      app.toast('Şablon kaydedildi.', 'good');
      app.api.invalidate('/v1/admin/finance/plan-templates');
      view.reload();
    }
  }

  async function remove(row) {
    const reason = await reasonDialog(app, { title: `“${row.name}” şablonunu sil`,
      description: 'Bu şablonla oluşturulmuş planlar etkilenmez.', confirmLabel: 'Sil', minLength: 0 });
    if (reason === null) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/finance/plan-templates/${row.id}`),
      { success: 'Şablon silindi.' });
    if (done !== undefined) { app.api.invalidate('/v1/admin/finance/plan-templates'); view.reload(); }
  }

  return { node, reload: () => view.reload() };
}

function templateFields() {
  return [
    { key: 'name', label: 'Şablon adı', required: true, maxLength: 80, wide: true },
    { key: 'totalMinor', label: 'Toplam tutar', type: 'money', required: true, min: 100 },
    { key: 'downPaymentMinor', label: 'Peşinat', type: 'money', min: 0 },
    { key: 'installmentCount', label: 'Taksit sayısı', type: 'number', required: true, min: 1, max: 36 },
    { key: 'interval', label: 'Aralık', type: 'select', options: optionsOf(INTERVALS) },
    { key: 'firstDueDay', label: 'Vade günü (1–28)', type: 'number', min: 1, max: 28,
      hint: 'Boş bırakılırsa ilk vade plan oluştururken seçilir.' },
  ];
}

function templateBody(draft) {
  return {
    name: String(draft.name || '').trim(),
    totalMinor: Number(draft.totalMinor),
    downPaymentMinor: Number(draft.downPaymentMinor || 0),
    installmentCount: Number(draft.installmentCount),
    interval: draft.interval || 'monthly',
    firstDueDay: draft.firstDueDay ? Number(draft.firstDueDay) : null,
  };
}

// ------------------------------------------------------------ hesap detayı

function accountDrawer(app, studentId, { onChange } = {}) {
  const panel = openDrawer(app, { title: 'Cari hesap', subtitle: 'Yükleniyor…' });
  panel.node.classList.add('ak-fin-drawer');
  const body = h('div', 'ak-stack');
  panel.body.append(body);
  let account = null;

  const view = loader(() => app.api.get(`/v1/admin/finance/accounts/${studentId}`), (data) => {
    account = data;
    const student = data.student || {};
    panel.setTitle(`${student.displayName || 'Öğrenci'} — kurum cari`);
    return render(data);
  }, { rows: 6, columns: 5 });
  body.append(view.node);
  view.reload();

  function changed() {
    view.reload();
    payments.reload();
    onChange?.();
  }

  // Tahsilat geçmişi öğrenci ucundan imleçli gelir (satır başına istek yok).
  const payments = pagedList({
    app,
    path: `/v1/students/${studentId}/finance/payments`,
    columns: paymentColumns(app, {
      onReceipt: (row) => receiptDrawer(app, row.id),
      onVoid: (row) => voidPayment(app, row, () => changed()),
    }),
    limit: 20,
    empty: { title: 'Henüz tahsilat yok' },
  });

  function render(data) {
    const summary = data.summary || {};
    const student = data.student || {};
    const head = card('', kv([
      ['Öğrenci', `${student.displayName || '—'} · ${student.externalRef || ''}`],
      ['Sınıf', student.classGroup?.name || '—'],
      ['Sonraki vade', summary.nextDue?.dueDate
        ? `${fmtDate(summary.nextDue.dueDate)} · ${summary.nextDue.description} · ${money(summary.nextDue.remainingMinor)}`
        : '—'],
      ['Son tahsilat', summary.lastPayment?.paidAt
        ? `${fmtDateTime(summary.lastPayment.paidAt)} · ${money(summary.lastPayment.amountMinor)} · `
          + `${METHODS[summary.lastPayment.method] || ''}` : '—'],
      ['Uygulamadan kartla ödeme', summary.cardPaymentEnabled ? 'Açık' : 'Kapalı'],
    ]));
    head.classList.add('ak-money-finance');

    const actions = toolbar(
      actionButton(app, 'admin_recordPayment', 'Tahsilat al', { variant: 'primary',
        onClick: () => recordPayment(app, data, changed) }),
      actionButton(app, 'admin_createInstallmentPlan', 'Taksit planı', {
        onClick: () => createPlan(app, data, changed) }),
      actionButton(app, 'admin_createCharge', 'Borç kalemi', { onClick: () => editCharge(app, data, null, changed) }),
      button('Ekstre', { onClick: () => statementDrawer(app, studentId, student.displayName) }),
      h('span', 'kit-spacer'),
      actionButton(app, 'admin_recordRefund', 'İade kaydı', { onClick: () => recordRefund(app, data, changed),
        title: 'Hesaptaki alacağın aileye elle geri verildiğini kaydeder.' }),
      actionButton(app, 'admin_withdrawFinanceAccount', 'Ayrılma / dondurma', {
        onClick: () => withdrawAccount(app, data, changed),
        title: 'Ayrılma: okunan süre hesabıyla iade önerisi. Dondurma: taksitler askıya alınır, sonra “Kaydı yeniden başlat”.' }),
    );

    const kpis = kpiRow([
      { label: 'Açık bakiye', value: money(summary.openBalanceMinor) },
      { label: 'Gecikmiş', value: money(summary.overdueMinor), tone: summary.overdueMinor ? 'bad' : '' },
      { label: '30 gün içinde', value: money(summary.dueNext30DaysMinor) },
      { label: 'Alacak', value: money(summary.creditMinor),
        title: 'Fazla ödeme; sonraki kalemlere kendiliğinden mahsup edilir (yalnız kurum carisinde).' },
    ]);

    const charges = dataTable({
      columns: chargeColumns(app, data, changed),
      rows: data.openCharges || [],
      empty: empty('Açık kalem yok', 'Bu öğrencinin ödenmemiş kurum borcu bulunmuyor.'),
    });

    const plans = h('div', 'ak-stack');
    for (const plan of data.plans || []) plans.append(planCard(app, plan, data, changed));
    if (!(data.plans || []).length) plans.append(h('div', 'ak-muted', 'Taksit planı yok.'));

    return [actions, kpis, head, card('Açık kalemler', charges.node),
      card('Taksit planları', plans), ...closingCards(app, data, changed), card('Tahsilat geçmişi', payments.node)];
  }

  // Tahsilat listesi çekmece açılınca ilk sayfayı getirir.
  payments.reload();
  return { close: panel.close, account: () => account };
}

function chargeColumns(app, account, changed) {
  return [
    { key: 'dueDate', label: 'Vade', width: '96px', cell: (row) => fmtDate(row.dueDate) },
    { key: 'description', label: 'Açıklama', width: 'minmax(0, 2fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', row.description),
        h('small', '', [CHARGE_KINDS[row.kind], row.installmentNo
          ? `${row.installmentNo}/${row.installmentCount}` : ''].filter(Boolean).join(' · ')));
      return cell;
    } },
    { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    { key: 'remaining', label: 'Kalan', align: 'num', cell: (row) => money(row.remainingMinor) },
    { key: 'status', label: 'Durum', cell: (row) => statusBadge(CHARGE_STATUS, row.status) },
    { key: 'actions', label: '', width: '150px', cell: (row) => {
      const actions = h('div', 'ak-row-actions');
      if (row.status !== 'cancelled' && row.status !== 'paid') {
        actions.append(
          actionButton(app, 'admin_updateCharge', 'Düzenle', { variant: 'ghost',
            onClick: () => editCharge(app, account, row, changed) }),
          actionButton(app, 'admin_cancelCharge', 'İptal', { variant: 'ghost',
            onClick: () => cancelCharge(app, row, changed) }),
        );
      }
      return actions;
    } },
  ];
}

function planCard(app, plan, account, changed) {
  const box = h('div', 'ak-fin-plan');
  const head = h('div', 'ak-inline');
  head.append(h('b', '', plan.title), statusBadge(PLAN_STATUS, plan.status),
    h('span', 'ak-muted', `${money(plan.totalMinor)} · ${num(plan.installmentCount)} taksit · `
      + `${INTERVALS[plan.interval] || plan.interval}${plan.downPaymentMinor ? ` · peşinat ${money(plan.downPaymentMinor)}` : ''}`),
    h('span', 'kit-spacer'));
  // Kayıt sözleşmesi eki: öğrenci, veli, liste fiyatı, indirim, taksit tablosu, koşullar, imza alanı.
  head.append(actionButton(app, 'admin_getPlanAgreement', 'Ödeme planı belgesi', { variant: 'ghost',
    title: 'İmzaya hazır ödeme planı (kayıt sözleşmesi eki) — önizle / yazdır.',
    onClick: (event) => printPlanAgreement(app, plan, event?.currentTarget || null) }));
  if (plan.status === 'active' && !plan.discount) {
    head.append(actionButton(app, 'admin_applyPlanDiscount', 'İndirim / burs', { variant: 'ghost',
      onClick: () => applyPlanDiscount(app, plan, changed) }));
  }
  if (plan.status === 'active') {
    head.append(actionButton(app, 'admin_cancelInstallmentPlan', 'Planı iptal et', { variant: 'ghost',
      onClick: async () => {
        const reason = await reasonDialog(app, { title: `“${plan.title}” planını iptal et`,
          description: 'Planın açık (ödenmemiş) kalemleri iptal edilir; ödenmiş kalemler ve tahsilatlar korunur.',
          confirmLabel: 'Planı iptal et' });
        if (!reason) return;
        const done = await runAction(app, () => app.api.post(
          `/v1/admin/finance/installment-plans/${plan.id}/cancel`, { reason },
          { key: `plan-cancel-${plan.id}` }), { success: 'Plan iptal edildi.' });
        if (done) changed();
      } }));
  }
  const table = dataTable({ columns: chargeColumns(app, account, changed), rows: plan.charges || [], dense: true });
  box.append(head);
  const discount = discountLine(plan);
  if (discount) box.append(discount);
  if (plan.status === 'cancelled' && plan.cancelReason) box.append(h('div', 'ak-muted', plan.cancelReason));
  box.append(table.node);
  return box;
}

// ------------------------------------------------------------- pencereler

/** Tahsilat: seçili kalemlere ya da serbest tutar (FIFO, artan alacak). */
async function recordPayment(app, account, changed) {
  const charges = (account.openCharges || []).filter((row) => row.remainingMinor > 0
    && row.status !== 'cancelled');
  const picked = new Map();          // chargeId → kuruş

  const chargePicker = (draft, set) => {
    const wrap = h('div', 'ak-fin-pick');
    if (!charges.length) {
      wrap.append(h('div', 'ak-muted', 'Açık kalem yok; tutar alacak olarak yazılır.'));
      return wrap;
    }
    const total = h('div', 'ak-fin-pick-total');
    const paint = () => {
      const sum = [...picked.values()].reduce((acc, value) => acc + value, 0);
      total.textContent = `Seçilen: ${money(sum)}`;
      set([...picked.entries()].map(([chargeId, amountMinor]) => ({ chargeId, amountMinor })));
    };
    for (const charge of charges) {
      const row = h('label', 'ak-fin-pick-row');
      const check = h('input', 'kit-check');
      check.type = 'checkbox';
      const amount = h('input', 'kit-input ak-fin-amount');
      amount.inputMode = 'decimal';
      amount.value = moneyText(charge.remainingMinor);
      amount.disabled = true;
      const sync = () => {
        if (!check.checked) { picked.delete(charge.id); }
        else {
          const value = parseMoney(amount.value);
          picked.set(charge.id, Math.min(value ?? charge.remainingMinor, charge.remainingMinor));
        }
        amount.disabled = !check.checked;
        paint();
      };
      check.addEventListener('change', sync);
      amount.addEventListener('input', sync);
      row.append(check, h('span', 'ak-fin-pick-desc', `${fmtDate(charge.dueDate)} · ${charge.description}`),
        h('span', 'ak-muted', `kalan ${money(charge.remainingMinor)}`), amount);
      wrap.append(row);
    }
    wrap.append(total);
    paint();
    return wrap;
  };

  const result = await formDialog(app, {
    title: `Tahsilat — ${account.student?.displayName || ''}`,
    description: `Açık bakiye ${money(account.summary?.openBalanceMinor)} · alacak ${money(account.summary?.creditMinor)}. `
      + 'Serbest tutar en eski vadeden başlayarak dağıtılır; artan kısım alacak olarak kalır.',
    wide: true,
    value: { method: 'cash', mode: charges.length ? 'selected_charges' : 'free_amount', paidDate: todayIst() },
    fields: [
      { key: 'method', label: 'Yöntem', type: 'select', options: optionsOf({ cash: METHODS.cash,
        transfer: METHODS.transfer, card: METHODS.card, other: METHODS.other }) },
      { key: 'mode', label: 'Dağıtım', type: 'select', options: [
        { value: 'selected_charges', label: 'Seçili kalemlere' },
        { value: 'free_amount', label: 'Serbest tutar (en eski vadeden)' }] },
      { key: 'amountMinor', label: 'Tahsil edilen tutar', type: 'money', min: 1,
        hint: 'Seçili kalemlerde boş bırakılırsa seçilenlerin toplamı alınır.' },
      { key: 'paidDate', label: 'Tahsilat günü', type: 'date' },
      { key: 'reference', label: 'Dekont / referans', maxLength: 80 },
      { key: 'note', label: 'Not', maxLength: 200 },
      { key: 'allocations', label: 'Kalemler (seçili kalemlere dağıtımda)', type: 'custom',
        render: chargePicker, wide: true },
    ],
    submitLabel: 'Tahsilatı kaydet',
    onSubmit: (draft, { key }) => {
      const allocations = draft.mode === 'selected_charges' ? (draft.allocations || []) : [];
      const sum = allocations.reduce((acc, item) => acc + item.amountMinor, 0);
      const amount = Number(draft.amountMinor) || sum;
      if (!amount) throw new AkademiError({ message: 'Tutar yazın ya da kalem seçin.' });
      if (draft.mode === 'selected_charges' && !allocations.length) {
        throw new AkademiError({ message: 'En az bir kalem seçin ya da serbest tutara geçin.' });
      }
      if (sum > amount) {
        throw new AkademiError({ message: `Seçilen kalemler (${money(sum)}) tahsil edilen tutarı aşıyor.` });
      }
      const today = draft.paidDate === todayIst();
      return app.api.post(`/v1/admin/finance/accounts/${account.student.id}/payments`, {
        method: draft.method,
        amountMinor: amount,
        mode: draft.mode,
        allocations: draft.mode === 'selected_charges' ? allocations : undefined,
        // Bugün ise saat Akademi'nin; geçmiş gün için gün ortası (Europe/Istanbul).
        paidAt: !draft.paidDate || today ? null : `${draft.paidDate}T12:00:00+03:00`,
        reference: draft.reference || null,
        note: draft.note || null,
      }, { key });
    },
  });
  if (!result) return;
  const credit = result.creditAddedMinor ? ` · ${money(result.creditAddedMinor)} alacağa yazıldı` : '';
  app.toast(`Tahsilat kaydedildi: ${result.receiptNo} · ${money(result.amountMinor)}${credit}`, 'good');
  changed();
  receiptDrawer(app, result.id);
}

/** Taksit planı: şablondan doldur → önizle (dryRun) → oluştur. */
async function createPlan(app, account, changed) {
  let templates = [];
  try {
    templates = (await app.api.getCached('/v1/admin/finance/plan-templates'))?.items || [];
  } catch { templates = []; }
  const preview = h('div', 'ak-fin-preview');
  preview.append(h('div', 'ak-muted', 'Bilgileri doldurup “Önizle” ile taksit takvimini görün.'));

  let lastPreviewKey = '';
  const result = await formDialog(app, {
    title: `Taksit planı — ${account.student?.displayName || ''}`,
    wide: true,
    value: { interval: 'monthly', downPaymentMinor: 0, firstDueDate: addDays(todayIst(), 30),
      installmentCount: 10, roundingMinor: '100', discountKind: '', discountReason: 'scholarship' },
    fields: [
      templates.length ? { key: 'templateId', label: 'Şablon', type: 'select', options: [
        { value: '', label: 'Şablonsuz' }, ...templates.map((item) => ({ value: item.id, label: item.name }))],
      hint: 'Şablon seçince alanlar dolar; değiştirebilirsiniz.' } : null,
      { key: 'title', label: 'Plan adı', required: true, maxLength: 80, placeholder: '2026–2027 eğitim ücreti' },
      { key: 'totalMinor', label: 'Toplam tutar (peşinat dahil; indirim varsa liste fiyatı)', type: 'money',
        required: true, min: 100 },
      { key: 'downPaymentMinor', label: 'Peşinat', type: 'money', min: 0 },
      { key: 'downPaymentDueDate', label: 'Peşinat vadesi', type: 'date' },
      { key: 'installmentCount', label: 'Taksit sayısı', type: 'number', required: true, min: 1, max: 36 },
      { key: 'firstDueDate', label: 'İlk taksit vadesi', type: 'date', required: true },
      { key: 'interval', label: 'Aralık', type: 'select', options: optionsOf(INTERVALS) },
      { key: 'roundingMinor', label: 'Yuvarlama', type: 'select', options: [
        { value: '1', label: 'Kuruşa' }, { value: '100', label: '1 ₺' }, { value: '1000', label: '10 ₺' }] },
      // İndirim / burs: toplam tutar LİSTE fiyatıdır; indirim düşülüp kalan taksitlere bölünür.
      ...discountFields(),
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önizle', { onClick: async () => {
          go.disabled = true;
          try {
            const plan = await app.api.post(
              `/v1/admin/finance/accounts/${account.student.id}/installment-plans`,
              { ...planBody(draft, templates), dryRun: true });
            lastPreviewKey = JSON.stringify(planBody(draft, templates));
            preview.replaceChildren(previewTable(plan));
          } catch (failure) {
            preview.replaceChildren(errorBox(failure));
          } finally { go.disabled = false; }
        } });
        wrap.append(go, preview);
        return wrap;
      } },
    ],
    submitLabel: 'Planı oluştur',
    onSubmit: (draft, { key }) => {
      const body = planBody(draft, templates);
      if (lastPreviewKey && lastPreviewKey !== JSON.stringify(body)) preview.prepend(
        alertBox('Bilgiler önizlemeden sonra değişti; oluşturulan plan yeni bilgilere göre hesaplanır.', 'info'));
      return app.api.post(`/v1/admin/finance/accounts/${account.student.id}/installment-plans`,
        { ...body, dryRun: false }, { key });
    },
  });
  if (result) {
    app.toast(`“${result.title}” planı oluşturuldu (${num((result.charges || []).length)} kalem).`, 'good');
    changed();
  }
}

function planBody(draft, templates) {
  const template = templates.find((item) => item.id === draft.templateId) || null;
  const pickNumber = (value, fallback) => (value === undefined || value === null || value === ''
    ? fallback : Number(value));
  return {
    title: String(draft.title || template?.name || '').trim(),
    totalMinor: pickNumber(draft.totalMinor, template?.totalMinor),
    downPaymentMinor: pickNumber(draft.downPaymentMinor, template?.downPaymentMinor || 0),
    downPaymentDueDate: draft.downPaymentDueDate || null,
    installmentCount: pickNumber(draft.installmentCount, template?.installmentCount),
    firstDueDate: draft.firstDueDate,
    interval: draft.interval || template?.interval || 'monthly',
    roundingMinor: Number(draft.roundingMinor || 100),
    templateId: template?.id || null,
    discount: discountBody(draft),
  };
}

function previewTable(plan) {
  const table = dataTable({
    columns: [
      { key: 'dueDate', label: 'Vade', cell: (row) => fmtDate(row.dueDate) },
      { key: 'description', label: 'Kalem', width: 'minmax(0, 2fr)' },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    ],
    rows: plan.charges || [],
    dense: true,
  });
  const wrap = h('div', 'ak-stack');
  const sum = (plan.charges || []).reduce((acc, row) => acc + row.amountMinor, 0);
  const discount = plan.discount ? ` · indirim ${money(plan.discount.amountMinor)} (liste ${money(plan.discount.listPriceMinor)})` : '';
  wrap.append(table.node, h('div', 'ak-muted', `Toplam ${money(sum)} · ${num((plan.charges || []).length)} kalem${discount}`));
  return wrap;
}

async function editCharge(app, account, charge, changed) {
  const result = await formDialog(app, {
    title: charge ? 'Borç kalemini düzenle' : `Borç kalemi — ${account.student?.displayName || ''}`,
    description: charge && charge.paidMinor ? `Bu kalemden ${money(charge.paidMinor)} ödenmiş; tutar bunun altına inemez.` : '',
    value: charge ? { ...charge } : { kind: 'other', dueDate: todayIst() },
    fields: [
      charge ? null : { key: 'kind', label: 'Tür', type: 'select', options: optionsOf(CHARGE_KINDS) },
      { key: 'description', label: 'Açıklama', required: true, maxLength: 120, placeholder: 'Deneme seti ücreti', wide: true },
      { key: 'amountMinor', label: 'Tutar', type: 'money', required: true, min: 1 },
      { key: 'dueDate', label: 'Vade', type: 'date', required: true },
    ],
    onSubmit: (draft, { key }) => (charge
      ? app.api.patch(`/v1/admin/finance/charges/${charge.id}`, {
        description: draft.description, dueDate: draft.dueDate, amountMinor: Number(draft.amountMinor) })
      : app.api.post(`/v1/admin/finance/accounts/${account.student.id}/charges`, {
        kind: draft.kind || 'other', description: draft.description,
        amountMinor: Number(draft.amountMinor), dueDate: draft.dueDate }, { key })),
  });
  if (result) { app.toast('Kalem kaydedildi.', 'good'); changed(); }
}

async function cancelCharge(app, charge, changed) {
  const reason = await reasonDialog(app, { title: `“${charge.description}” kalemini iptal et`,
    description: charge.paidMinor ? `Bu kaleme ${money(charge.paidMinor)} ödeme dağıtılmış. İptal Akademi kurallarına göre işlenir.`
      : 'Kalem iptal edilir, bakiyeden düşer. Kayıt silinmez.',
    confirmLabel: 'Kalemi iptal et' });
  if (!reason) return;
  const done = await runAction(app, () => app.api.post(`/v1/admin/finance/charges/${charge.id}/cancel`,
    { reason }, { key: `charge-cancel-${charge.id}` }), { success: 'Kalem iptal edildi.' });
  if (done) changed();
}

// ------------------------------------------------------- makbuz ve ekstre

export function receiptDrawer(app, paymentId, { openAccount } = {}) {
  const panel = openDrawer(app, { title: 'Makbuz', subtitle: 'Kurum cari tahsilatı' });
  const view = loader(() => app.api.get(`/v1/admin/finance/payments/${paymentId}/receipt`), (receipt) => {
    const payment = receipt.payment || {};
    panel.setTitle(`Makbuz ${receipt.receiptNo}`);
    const nodes = [];
    if (payment.status === 'voided') {
      nodes.push(alertBox(`Bu tahsilat ${fmtDateTime(payment.voidedAt)} tarihinde iptal edildi: ${payment.voidReason || ''}`, 'warn'));
    }
    nodes.push(card('', kv([
      ['Kurum', receipt.institution?.name],
      ['Öğrenci', `${receipt.student?.displayName || ''} · ${receipt.student?.externalRef || ''}`],
      ['Tarih', fmtDateTime(payment.paidAt)],
      ['Tutar', money(payment.amountMinor)],
      ['Yöntem', METHODS[payment.method] || payment.method],
      ['Referans', payment.reference || payment.ref || '—'],
      ['Kaydeden', `${personName(payment.recordedBy)}${payment.recordedVia ? ` · ${VIA[payment.recordedVia] || payment.recordedVia}` : ''}`],
      ['Not', payment.note || '—'],
      ['İşlem sonrası bakiye', money(receipt.balanceAfterMinor)],
      ['İşlem sonrası alacak', money(receipt.creditAfterMinor)],
    ])));
    const allocations = dataTable({
      columns: [
        { key: 'description', label: 'Kapanan kalem', width: 'minmax(0, 2fr)' },
        { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      ],
      rows: payment.allocations || [],
      empty: empty('Kaleme dağıtılmadı', 'Tutarın tamamı alacak olarak yazıldı.'),
    });
    nodes.push(card('Dağıtım', allocations.node));
    if (payment.creditAddedMinor) nodes.push(h('div', 'ak-muted', `Alacağa yazılan: ${money(payment.creditAddedMinor)}`));
    // Makbuz: kurum logolu belge (önizle / yazdır); Akademi'nin kendi PDF'i
    // varsa o da aynı önizleme penceresinde açılır ve bu cihazdan basılır.
    const tools = [documentButtons(app, { title: 'Tahsilat makbuzu', label: 'Makbuz',
      build: async () => receiptDoc(receipt) })];
    if (receipt.pdfUrl) {
      tools.push(button('Akademi PDF', { title: 'Akademi\'nin ürettiği makbuz PDF\'ini önizler.',
        onClick: (event) => previewAkademiPdf(app, { kind: 'finance_receipt', url: receipt.pdfUrl,
          name: receipt.receiptNo, opener: event.currentTarget }) }));
    }
    if (openAccount && receipt.student?.id) {
      tools.push(button('Cari hesabı aç', { onClick: () => { panel.close(); openAccount(receipt.student.id); } }));
    }
    if (tools.length) nodes.push(toolbar(...tools));
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
}

function statementDrawer(app, studentId, name) {
  let range = null;
  const panel = openDrawer(app, { title: `Hesap ekstresi — ${name || ''}`,
    onClose: () => range?.destroy() });
  const state = { from: todayIst(-365), to: todayIst() };
  range = dateRange({ start: state.from, end: state.to, label: 'Dönem', onChange: (value) => {
    state.from = value.start || state.from;
    state.to = value.end || state.to;
    view.reload();
  } });
  let pdfUrl = null;
  // Akademi'nin kendi ekstre PDF'i: önizleme penceresinde açılır, bu cihazdan basılır.
  const pdf = button('Akademi PDF', { title: 'Akademi\'nin ürettiği ekstre PDF\'ini önizler.',
    onClick: async (event) => {
      const opener = event.currentTarget;
      const url = pdfUrl || await runAction(app, async () => {
        const data = await app.api.get(`/v1/admin/finance/accounts/${studentId}/statement`,
          { from: state.from, to: state.to, format: 'pdf' });
        if (!data?.pdfUrl) throw new AkademiError({ message: 'Akademi bu ekstre için PDF üretmedi.' });
        return data.pdfUrl;
      });
      if (url) previewAkademiPdf(app, { kind: 'finance_statement', url, name: `${name || ''} ${state.from} ${state.to}`, opener });
    } });
  // Kurum logolu ekstre (ekrandaki dönem).
  const branded = documentButtons(app, { title: 'Hesap ekstresi', label: 'Ekstre',
    build: async () => {
      const [detail, statement] = await Promise.all([app.api.get(`/v1/admin/students/${studentId}`),
        app.api.get(`/v1/admin/finance/accounts/${studentId}/statement`, { from: state.from, to: state.to })]);
      return statementDoc(detail?.student || { displayName: name }, statement);
    } });
  const KINDS = STATEMENT_KINDS;
  const view = loader(() => app.api.get(`/v1/admin/finance/accounts/${studentId}/statement`,
    { from: state.from, to: state.to }), (statement) => {
    pdfUrl = statement.pdfUrl || null;
    const table = dataTable({
      columns: [
        { key: 'date', label: 'Tarih', width: '96px', cell: (row) => fmtDate(row.date) },
        { key: 'kind', label: 'Hareket', cell: (row) => KINDS[row.kind] || row.kind },
        { key: 'description', label: 'Açıklama', width: 'minmax(0, 2fr)' },
        { key: 'charge', label: 'Borç', align: 'num', cell: (row) => (row.chargeMinor ? money(row.chargeMinor) : '') },
        { key: 'payment', label: 'Ödeme', align: 'num', cell: (row) => (row.paymentMinor ? money(row.paymentMinor) : '') },
        { key: 'balance', label: 'Bakiye', align: 'num', cell: (row) => money(row.balanceMinor) },
      ],
      rows: statement.lines || [],
      dense: true,
      empty: empty('Bu dönemde hareket yok'),
    });
    return [kpiRow([
      { label: 'Dönem başı', value: money(statement.openingBalanceMinor) },
      { label: 'Dönem sonu', value: money(statement.closingBalanceMinor) },
      { label: 'Alacak', value: money(statement.creditMinor) },
    ]), closingNote(statement), table.node].filter(Boolean);
  });
  panel.body.append(toolbar(range.node, h('span', 'kit-spacer'), branded, pdf), view.node);
  view.reload();
}

