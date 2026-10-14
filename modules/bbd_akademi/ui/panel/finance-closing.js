// BBD Akademi — Kurum finansı genişletmesi (uçtan uca denetim U18; Akademi
// sözleşmesi README §14 madde 78).
//
// KANTİNDEN AYRI (A12). Bu dosya Kurum finansı ekranına şunları ekler:
//   · indirim / burs — plan oluştururken (yüzde ya da tutar, gerekçe, onaylayan)
//     ve sonradan etkin plana (`admin_applyPlanDiscount`, açık kalemlere oranla);
//   · ayrılma / kayıt dondurma (`admin_withdrawFinanceAccount`): etkin tarih ve
//     sonrası taksitler iptal, iade edilebilir tutar hesaplanır (önizleme);
//   · iade kaydı (`admin_recordRefund`) ve iptali (`admin_voidRefund`, PIN) —
//     PARA HAREKETİ OTOMATİK DEĞİL: kayıt, kurumun elle yaptığı geri ödemeyi belgeler;
//   · aylık tahsilat özeti (`admin_getFinanceMonthlyReport`, ay × yöntem);
//   · muhasebeciye dışa aktarım (`admin_exportFinance`, Excel uyumlu CSV / XLSX).
// İş kuralı Akademi'dedir; panel niyeti gönderir, önizleme `dryRun` iledir.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { AkademiError } from './api.js';
import {
  actionButton, empty, errorBox, exportJob, fmtDate, fmtDateTime, formDialog, loader, money, num,
  optionsOf, personName, pinStepUp, reasonDialog, runAction, statusBadge, todayIst, toolbar,
} from './common.js';

export const STATEMENT_KINDS = { charge: 'Borç', charge_cancelled: 'Borç iptali', payment: 'Tahsilat',
  payment_voided: 'Tahsilat iptali', credit_applied: 'Alacak mahsubu', discount: 'İndirim / burs',
  refund: 'İade', refund_voided: 'İade iptali', withdrawal: 'Ayrılma / dondurma' };
export const DISCOUNT_REASONS = { scholarship: 'Burs', sibling: 'Kardeş indirimi',
  early_registration: 'Erken kayıt', staff: 'Personel yakını', other: 'Diğer' };
const DISCOUNT_KINDS = { percent: 'Yüzde', amount: 'Tutar' };
const REFUND_METHODS = { cash: 'Nakit', transfer: 'Havale / EFT', card: 'Kart (POS)', other: 'Diğer' };
const WITHDRAWAL_KINDS = { withdrawal: 'Ayrılma', freeze: 'Kayıt dondurma' };
const REFUND_STATUS = { completed: ['Geçerli', 'good'], voided: ['İptal edildi', 'dim'] };
const MONTH_METHODS = { cash: 'Nakit', transfer: 'Havale / EFT', card: 'Kart (POS)',
  online_card: 'Uygulamadan kart', other: 'Diğer' };
export const EXPORT_REPORTS = {
  payments: 'Tahsilatlar (makbuz, yöntem, iptaller)',
  accounts: 'Cari bakiyeler (anlık; borç, alacak, toplamlar)',
  overdue: 'Geciken kalemler (anlık)',
  monthly: 'Aylık tahsilat özeti (ay × yöntem)',
  refunds: 'İadeler',
  discounts: 'İndirim ve burslar',
  withdrawals: 'Ayrılma ve kayıt dondurma',
};
const MONTHS = ['Ocak', 'Şubat', 'Mart', 'Nisan', 'Mayıs', 'Haziran', 'Temmuz', 'Ağustos', 'Eylül', 'Ekim',
  'Kasım', 'Aralık'];

/** `2026-10` → `Ekim 2026`. */
export function monthLabel(key) {
  const [year, month] = String(key || '').split('-').map(Number);
  return MONTHS[month - 1] ? `${MONTHS[month - 1]} ${year}` : String(key || '—');
}

/** İçinde bulunulan ay dahil son 12 ayın ilk günü (`YYYY-MM-01`). */
export function twelveMonthsStart(today) {
  const [year, month] = String(today).split('-').map(Number);
  const index = year * 12 + (month - 1) - 11;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}-01`;
}

// ------------------------------------------------------------ indirim / burs

/** Plan formuna eklenen indirim alanları (`FinanceDiscountInput`). */
export function discountFields({ required = false } = {}) {
  return [
    { key: 'discountKind', label: 'İndirim / burs', type: 'select', required,
      options: required ? optionsOf(DISCOUNT_KINDS) : optionsOf(DISCOUNT_KINDS, { empty: 'İndirim yok' }) },
    { key: 'discountPercent', label: 'İndirim yüzdesi (1–99)', type: 'number', min: 1, max: 99,
      hint: 'Türü “Yüzde” ise.' },
    { key: 'discountAmountMinor', label: 'İndirim tutarı', type: 'money', min: 1, hint: 'Türü “Tutar” ise.' },
    { key: 'discountReason', label: 'Gerekçe', type: 'select', options: optionsOf(DISCOUNT_REASONS) },
    { key: 'discountApprovedBy', label: 'Onaylayan', maxLength: 120, placeholder: 'Boşsa işlemi yapan' },
    { key: 'discountNote', label: 'Açıklama', maxLength: 200, placeholder: 'Örn. kardeşi 10. sınıfta' },
  ];
}

/** Taslaktan `FinanceDiscountInput`; indirim seçilmediyse `null`. Tür ↔ alan uyuşmazlığı Türkçe hatadır. */
export function discountBody(draft) {
  const kind = draft.discountKind || '';
  if (!kind) return null;
  const body = { kind, reason: draft.discountReason || 'other',
    note: String(draft.discountNote || '').trim() || null,
    approvedBy: String(draft.discountApprovedBy || '').trim() || null };
  if (kind === 'percent') {
    const percent = Number(draft.discountPercent);
    if (!Number.isInteger(percent) || percent < 1 || percent > 99) {
      throw new AkademiError({ message: 'İndirim yüzdesi 1 ile 99 arasında tam sayı olmalı.' });
    }
    body.percent = percent;
  } else {
    const amount = Number(draft.discountAmountMinor);
    if (!amount || amount < 1) throw new AkademiError({ message: 'İndirim tutarını yazın.' });
    body.amountMinor = amount;
  }
  return body;
}

/** Plan kartındaki indirim satırı. */
export function discountLine(plan) {
  const d = plan?.discount;
  if (!d) return null;
  const what = d.kind === 'percent' && d.percent ? `%${d.percent}` : money(d.amountMinor);
  const parts = [`${DISCOUNT_REASONS[d.reason] || 'İndirim'} ${what}`,
    `liste ${money(d.listPriceMinor)} → ${money(plan.totalMinor)} (−${money(d.amountMinor)})`];
  if (d.approvedBy) parts.push(`onaylayan ${d.approvedBy}`);
  if (d.afterCreation) parts.push(`sonradan · ${fmtDate(d.appliedAt)}`);
  if (d.note) parts.push(d.note);
  return h('div', 'ak-muted ak-fin-discount', parts.join(' · '));
}

/** Etkin plana sonradan indirim / burs: önizle (dryRun) → uygula. */
export async function applyPlanDiscount(app, plan, changed) {
  const preview = h('div', 'ak-fin-preview');
  preview.append(h('div', 'ak-muted', 'Yüzde, planın ödenmemiş tutarı üzerinden hesaplanır; indirim açık '
    + 'taksitlere kalanları oranında dağıtılır.'));
  const result = await formDialog(app, {
    title: `“${plan.title}” planına indirim / burs`,
    description: 'Ödenmiş taksitler değişmez. Planda yalnız bir indirim olabilir.',
    wide: true,
    value: { discountKind: 'percent', discountReason: 'scholarship' },
    fields: [
      ...discountFields({ required: true }),
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önizle', { onClick: async () => {
          go.disabled = true;
          try {
            const next = await app.api.post(`/v1/admin/finance/installment-plans/${plan.id}/discount`,
              { ...discountBody(draft), dryRun: true }, { key: `km-disc-preview-${Date.now()}` });
            preview.replaceChildren(discountPreview(plan, next));
          } catch (failure) {
            preview.replaceChildren(errorBox(failure));
          } finally { go.disabled = false; }
        } });
        wrap.append(go, preview);
        return wrap;
      } },
    ],
    submitLabel: 'İndirimi uygula',
    onSubmit: (draft, { key }) => app.api.post(`/v1/admin/finance/installment-plans/${plan.id}/discount`,
      { ...discountBody(draft), dryRun: false }, { key }),
  });
  if (result) {
    app.toast(`İndirim uygulandı: ${money(result.discount?.amountMinor || 0)} düşüldü.`, 'good');
    changed();
  }
}

function discountPreview(before, after) {
  const old = new Map((before.charges || []).map((row) => [row.id, row]));
  const rows = (after.charges || []).filter((row) => row.discountMinor
    || (old.get(row.id)?.amountMinor ?? row.amountMinor) !== row.amountMinor);
  const table = dataTable({
    columns: [
      { key: 'dueDate', label: 'Vade', cell: (row) => fmtDate(row.dueDate) },
      { key: 'description', label: 'Kalem', width: 'minmax(0, 2fr)' },
      { key: 'before', label: 'Önce', align: 'num', cell: (row) => money(old.get(row.id)?.amountMinor ?? row.amountMinor) },
      { key: 'after', label: 'Sonra', align: 'num', cell: (row) => money(row.amountMinor) },
    ],
    rows,
    dense: true,
    empty: empty('Değişen taksit yok'),
  });
  const wrap = h('div', 'ak-stack');
  wrap.append(table.node, h('div', 'ak-muted',
    `İndirim ${money(after.discount?.amountMinor || 0)} · plan ${money(before.totalMinor)} → ${money(after.totalMinor)}`));
  return wrap;
}

// ------------------------------------------------------ ayrılma / dondurma

/** Ayrılma / kayıt dondurma: önizle (dryRun) → kalan taksitleri kapat (+ isteğe bağlı iade kaydı). */
export async function withdrawAccount(app, account, changed) {
  const studentId = account.student.id;
  const preview = h('div', 'ak-fin-preview');
  preview.append(h('div', 'ak-muted', 'Etkin tarih ve sonrası vadeli taksitler iptal edilir; önceki vadeler '
    + 've kitap/materyal kalemleri borç olarak kalır. “Önizle” iade edilebilir tutarı gösterir.'));
  const bodyOf = (draft, dryRun, refund = null) => ({
    kind: draft.kind || 'withdrawal',
    effectiveDate: draft.effectiveDate,
    reason: String(draft.reason || '').trim(),
    refund,
    dryRun,
  });
  const check = (draft) => {
    if (!draft.effectiveDate) throw new AkademiError({ message: 'Etkin tarihi seçin.' });
    if (String(draft.reason || '').trim().length < 3) throw new AkademiError({ message: 'Gerekçe en az 3 karakter.' });
  };
  const result = await formDialog(app, {
    title: `Ayrılma / kayıt dondurma — ${account.student?.displayName || ''}`,
    description: 'Para hareketi otomatik yapılmaz: iade edilecek tutarı elle ödeyip burada iade kaydı olarak '
      + 'yazarsınız (aynı işlemde ya da sonra “İade kaydı” ile).',
    wide: true,
    value: { kind: 'withdrawal', effectiveDate: todayIst(), refundNow: '', refundMethod: 'transfer' },
    fields: [
      { key: 'kind', label: 'Tür', type: 'select', options: optionsOf(WITHDRAWAL_KINDS) },
      { key: 'effectiveDate', label: 'Etkin tarih', type: 'date', required: true,
        hint: 'Bu gün ve sonrası vadeli taksitler iptal edilir.' },
      { key: 'reason', label: 'Gerekçe', required: true, maxLength: 200, wide: true,
        placeholder: 'Örn. şehir değişikliği' },
      { key: 'refundNow', label: 'İade kaydı', type: 'select', options: [
        { value: '', label: 'Şimdi yazma (sonra)' }, { value: 'yes', label: 'Aynı işlemde yaz' }] },
      { key: 'refundAmountMinor', label: 'İade tutarı', type: 'money', min: 1,
        hint: 'Boşsa iade edilebilir tutarın tamamı.' },
      { key: 'refundMethod', label: 'İade yöntemi', type: 'select', options: optionsOf(REFUND_METHODS) },
      { key: 'refundReference', label: 'Dekont / referans', maxLength: 80 },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önizle', { onClick: async () => {
          go.disabled = true;
          try {
            check(draft);
            const res = await app.api.post(`/v1/admin/finance/accounts/${studentId}/withdrawal`,
              bodyOf(draft, true), { key: `km-wd-preview-${Date.now()}` });
            preview.replaceChildren(withdrawalPreview(res));
          } catch (failure) {
            preview.replaceChildren(errorBox(failure));
          } finally { go.disabled = false; }
        } });
        wrap.append(go, preview);
        return wrap;
      } },
    ],
    submitLabel: 'Kalan taksitleri kapat',
    danger: true,
    onSubmit: async (draft, { key }) => {
      check(draft);
      let refund = null;
      if (draft.refundNow === 'yes') {
        let amount = Number(draft.refundAmountMinor) || 0;
        if (!amount) {
          const dry = await app.api.post(`/v1/admin/finance/accounts/${studentId}/withdrawal`,
            bodyOf(draft, true), { key: `${key}-dry` });
          amount = dry?.withdrawal?.refundableMinor || 0;
        }
        if (amount > 0) {
          refund = { amountMinor: amount, method: draft.refundMethod || 'transfer',
            reference: String(draft.refundReference || '').trim() || null, note: 'Ayrılma iadesi' };
        }
      }
      return app.api.post(`/v1/admin/finance/accounts/${studentId}/withdrawal`, bodyOf(draft, false, refund), { key });
    },
  });
  if (!result) return;
  const w = result.withdrawal || {};
  const refundText = result.refund ? ` · iade ${result.refund.refundNo} ${money(result.refund.amountMinor)}`
    : (w.refundableMinor ? ` · iade edilebilir ${money(w.refundableMinor)}` : '');
  app.toast(`${WITHDRAWAL_KINDS[w.kind] || 'Kapanış'}: ${num(w.cancelledCount || 0)} taksit iptal${refundText}`, 'good');
  changed();
}

function withdrawalPreview(res) {
  const w = res?.withdrawal || {};
  const wrap = h('div', 'ak-stack');
  wrap.append(kpiRow([
    { label: 'İptal edilecek', value: `${num(w.cancelledCount || 0)} kalem · ${money(w.cancelledMinor || 0)}` },
    { label: 'Alacağa geçecek (ödenmiş)', value: money(w.releasedMinor || 0) },
    { label: 'Kalan borç', value: money(w.remainingDebtMinor || 0), tone: w.remainingDebtMinor ? 'bad' : '' },
    { label: 'İade edilebilir', value: money(w.refundableMinor || 0), tone: w.refundableMinor ? 'good' : '' },
  ]));
  const table = dataTable({
    columns: [
      { key: 'dueDate', label: 'Vade', cell: (row) => fmtDate(row.dueDate) },
      { key: 'description', label: 'İptal edilecek kalem', width: 'minmax(0, 2fr)' },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    ],
    rows: res?.cancelledCharges || [],
    dense: true,
    empty: empty('İptal edilecek taksit yok'),
  });
  wrap.append(table.node);
  return wrap;
}

// ------------------------------------------------------------------ iade

/** İade kaydı: hesaptaki alacağın aileye elle geri verildiğini belgeler. */
export async function recordRefund(app, account, changed) {
  const credit = account.summary?.creditMinor || 0;
  const result = await formDialog(app, {
    title: `İade kaydı — ${account.student?.displayName || ''}`,
    description: `Hesaptaki alacak ${money(credit)}. Para hareketini (havale, nakit) siz yaparsınız; bu kayıt `
      + 'alacaktan düşer ve ekstrede “İade” satırı olur.',
    value: { amountMinor: credit || undefined, method: 'transfer', refundedDate: todayIst() },
    fields: [
      { key: 'amountMinor', label: 'İade tutarı', type: 'money', required: true, min: 1 },
      { key: 'method', label: 'Yöntem', type: 'select', options: optionsOf(REFUND_METHODS) },
      { key: 'refundedDate', label: 'İade günü', type: 'date' },
      { key: 'reference', label: 'Dekont / referans', maxLength: 80 },
      { key: 'note', label: 'Not', maxLength: 200, wide: true },
    ],
    submitLabel: 'İadeyi kaydet',
    onSubmit: (draft, { key }) => {
      const today = draft.refundedDate === todayIst();
      return app.api.post(`/v1/admin/finance/accounts/${account.student.id}/refunds`, {
        amountMinor: Number(draft.amountMinor),
        method: draft.method || 'transfer',
        refundedAt: !draft.refundedDate || today ? null : `${draft.refundedDate}T12:00:00+03:00`,
        reference: String(draft.reference || '').trim() || null,
        note: String(draft.note || '').trim() || null,
      }, { key });
    },
  });
  if (result) {
    app.toast(`İade kaydedildi: ${result.refundNo} · ${money(result.amountMinor)}`, 'good');
    changed();
  }
}

async function voidRefund(app, refund, changed) {
  const reason = await reasonDialog(app, {
    title: `${refund.refundNo} iade kaydını iptal et`,
    description: `${money(refund.amountMinor)} hesaba alacak olarak döner ve açık kalemlere mahsup edilir.`,
    confirmLabel: 'Devam et',
    presets: ['Havale geri döndü', 'Tutar hatalı girildi', 'Mükerrer kayıt'],
  });
  if (!reason) return;
  const token = await pinStepUp(app, 'admin_voidRefund', {
    title: 'İade iptali — PIN teyidi',
    description: `${refund.refundNo} · ${money(refund.amountMinor)}. Devam etmek için kendi PIN'inizi girin.`,
    confirmLabel: 'İadeyi iptal et',
  });
  if (!token) return;
  const done = await runAction(app, () => app.api.post(`/v1/admin/finance/refunds/${refund.id}/void`,
    { reason }, { key: `refund-void-${refund.id}`.slice(0, 120), stepUpToken: token }),
  { success: 'İade kaydı iptal edildi.' });
  if (done) changed();
}

/** Cari hesap çekmecesindeki ayrılma ve iade kartları (yoksa boş). */
export function closingCards(app, account, changed) {
  const nodes = [];
  const withdrawals = account.withdrawals || [];
  if (withdrawals.length) {
    const table = dataTable({
      columns: [
        { key: 'createdAt', label: 'Kayıt', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
        { key: 'kind', label: 'Tür', cell: (row) => WITHDRAWAL_KINDS[row.kind] || row.kind },
        { key: 'effectiveDate', label: 'Etkin tarih', cell: (row) => fmtDate(row.effectiveDate) },
        { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 2fr)' },
        { key: 'cancelled', label: 'İptal', align: 'num',
          cell: (row) => `${num(row.cancelledCount)} · ${money(row.cancelledMinor)}` },
        { key: 'refundable', label: 'İade edilebilir', align: 'num', cell: (row) => money(row.refundableMinor) },
        { key: 'by', label: 'Kaydeden', cell: (row) => personName(row.createdBy) },
      ],
      rows: withdrawals,
      dense: true,
    });
    nodes.push(card('Ayrılma / kayıt dondurma', table.node));
  }
  const refunds = account.refunds || [];
  if (refunds.length) {
    const table = dataTable({
      columns: [
        { key: 'refundNo', label: 'İade no', width: '170px' },
        { key: 'refundedAt', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.refundedAt) },
        { key: 'method', label: 'Yöntem', cell: (row) => REFUND_METHODS[row.method] || row.method },
        { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
        { key: 'reference', label: 'Referans', cell: (row) => row.reference || '—' },
        { key: 'status', label: 'Durum', cell: (row) => statusBadge(REFUND_STATUS, row.status) },
        { key: 'actions', label: '', width: '90px', cell: (row) => (row.status === 'completed'
          ? actionButton(app, 'admin_voidRefund', 'İptal', { variant: 'ghost',
            onClick: () => voidRefund(app, row, changed) }) : '') },
      ],
      rows: refunds,
      dense: true,
    });
    nodes.push(card('İadeler', table.node));
  }
  return nodes;
}

// ------------------------------------------------------------ aylık özet

/** "Aylık rapor" sekmesi: ay × yöntem tahsilat, iptal, iade, net, tahakkuk, indirim. */
export function monthlyTab(app) {
  const node = h('div', 'ak-stack');
  const range = dateRange({ start: twelveMonthsStart(todayIst()), end: todayIst(), label: 'Dönem',
    onChange: () => view.reload() });
  const toExcel = actionButton(app, 'admin_exportFinance', 'Excel’e aktar', {
    onClick: () => {
      const { start, end } = range.get();
      return runExport(app, { report: 'monthly', from: start, to: end, format: 'xlsx' });
    } });
  node.append(toolbar(range.node, h('span', 'ak-muted',
    'Tahakkuk: vadesi o ay olan iptal edilmemiş kalemler. Net: tahsilat − iade.'), h('span', 'kit-spacer'),
  toExcel));
  const view = loader(() => {
    const { start, end } = range.get();
    return app.api.get('/v1/admin/finance/reports/monthly', { from: start, to: end });
  }, (report) => monthlyTable(report), { rows: 12, columns: 9 });
  node.append(view.node);
  return { node, reload: () => view.reload(), destroy() { range.destroy?.(); } };
}

export function monthlyRows(report) {
  const methodOf = (row, method) => (row.byMethod || []).find((item) => item.method === method)?.amountMinor || 0;
  const line = (label, row) => ({ label, ...Object.fromEntries(Object.keys(MONTH_METHODS)
    .map((method) => [method, methodOf(row, method)])),
  collected: row.collectedMinor || 0, voided: row.voidedMinor || 0, refunded: row.refundedMinor || 0,
  net: row.netMinor || 0, charged: row.chargedMinor || 0, discount: row.discountMinor || 0 });
  const rows = (report?.months || []).map((row) => line(monthLabel(row.month), row));
  if (report?.totals) rows.push({ ...line('Toplam', report.totals), total: true });
  return rows;
}

function monthlyTable(report) {
  const moneyCell = (key) => (row) => (row[key] ? money(row[key]) : '—');
  const table = dataTable({
    columns: [
      { key: 'label', label: 'Ay', width: '120px', cell: (row) => (row.total ? h('b', '', row.label) : row.label) },
      ...Object.entries(MONTH_METHODS).map(([key, label]) => ({ key, label, align: 'num', cell: moneyCell(key) })),
      { key: 'collected', label: 'Tahsilat', align: 'num', cell: (row) => h('b', '', money(row.collected)) },
      { key: 'voided', label: 'İptal edilen', align: 'num', cell: moneyCell('voided') },
      { key: 'refunded', label: 'İade', align: 'num', cell: moneyCell('refunded') },
      { key: 'net', label: 'Net', align: 'num', cell: (row) => money(row.net) },
      { key: 'charged', label: 'Tahakkuk', align: 'num', cell: moneyCell('charged') },
      { key: 'discount', label: 'İndirim', align: 'num', cell: moneyCell('discount') },
    ],
    rows: monthlyRows(report),
    dense: true,
    empty: empty('Bu dönemde hareket yok'),
  });
  return table.node;
}

// -------------------------------------------------------------- dışa aktarım

async function runExport(app, body) {
  const stem = { payments: 'tahsilatlar', accounts: 'cari-bakiyeler', overdue: 'geciken-kalemler',
    monthly: 'aylik-tahsilat', refunds: 'iadeler', discounts: 'indirim-burs',
    withdrawals: 'ayrilma-dondurma' }[body.report] || 'kurum-carisi';
  return exportJob(app, async () => {
    const file = await app.api.post('/v1/admin/finance/exports', body);
    return { status: 'done', url: file.url, format: file.format };
  }, { name: `${stem}-${body.from || todayIst()}`, label: 'Dosya hazırlanıyor…' });
}

/** Başlıktaki "Dışa aktar…" düğmesi: rapor, dönem ve biçim seçilir. */
export function exportButton(app, range) {
  return actionButton(app, 'admin_exportFinance', 'Dışa aktar…', { onClick: async () => {
    const { start, end } = range.get();
    const draft = await formDialog(app, {
      title: 'Kurum carisi dışa aktarımı',
      description: 'Muhasebeci / mali müşavir için Excel uyumlu dosya. Cari bakiyeler ve geciken kalemler '
        + 'bugünkü durumdur (dönem yok sayılır); aylık özet ay ay listelenir. Dışa aktarım denetim kaydına yazılır.',
      value: { report: 'payments', format: 'csv', from: start, to: end },
      fields: [
        { key: 'report', label: 'Rapor', type: 'select', options: optionsOf(EXPORT_REPORTS), wide: true },
        { key: 'from', label: 'Başlangıç', type: 'date' },
        { key: 'to', label: 'Bitiş', type: 'date' },
        { key: 'format', label: 'Biçim', type: 'select', options: [
          { value: 'csv', label: 'CSV (Excel, Türkçe)' }, { value: 'xlsx', label: 'Excel (.xlsx)' }] },
      ],
      submitLabel: 'Dışa aktar',
      onSubmit: (value) => value,
    });
    if (!draft || draft === true) return;
    const snapshot = draft.report === 'accounts' || draft.report === 'overdue';
    await runExport(app, { report: draft.report, format: draft.format || 'csv',
      from: snapshot ? null : draft.from || null, to: snapshot ? null : draft.to || null });
  } });
}

/** Ekstre çekmecesinin başlığı için küçük uyarı (iade / ayrılma satırları). */
export function closingNote(statement) {
  const kinds = new Set((statement?.lines || []).map((line) => line.kind));
  if (!kinds.has('refund') && !kinds.has('withdrawal')) return null;
  return alertBox('Bu dönemde ayrılma / iade kaydı var: iade satırı borç sütununda görünür (alacağı kapatır).',
    'info');
}
