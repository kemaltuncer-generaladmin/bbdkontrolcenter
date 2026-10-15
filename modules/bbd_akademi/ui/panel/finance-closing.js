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
// Akademi README §14 madde 87 (ozellik/finans-belge):
//   · ayrılmada OKUNAN SÜRE hesabı (`method: pro_rata`): öneri = ödenen − katılım
//     süresi oranında hizmet bedeli − kurum kesintisi (Ayarlar → Kurum finansı);
//     yönetici iade tutarını düzeltebilir, gerekçe zorunlu;
//   · "Kaydı yeniden başlat" (`admin_resumeFrozenAccount`): dondurulan taksitler
//     aynı tutarla yeni vadelere kayar;
//   · iade makbuzu (`admin_getRefundReceipt`) ve ödeme planı belgesi
//     (`admin_getPlanAgreement`, kayıt sözleşmesi eki) — kurum logolu A4.
// İş kuralı Akademi'dedir; panel niyeti gönderir, önizleme `dryRun` iledir.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { dateRange } from '../../ui-kit/datefield.js';
import { AkademiError } from './api.js';
import { runDocument } from './documents.js';
import { paymentPlanDoc, refundReceiptDoc } from './documents-logic.js';
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
export const WITHDRAWAL_METHODS = { pro_rata: 'Okunan süre hesabı (önerilen)',
  schedule: 'Sözleşme takvimi (kalan taksitler iptal)' };
const METHOD_SHORT = { pro_rata: 'Okunan süre', schedule: 'Takvim' };
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

/**
 * Ayrılma / dondurma isteği (`FinanceWithdrawalCreate`). Dondurma her zaman takvimle yapılır;
 * ayrılmada varsayılan okunan süre hesabıdır. Düzeltme yalnız okunan süre hesabında gider.
 */
export function withdrawalBody(draft, dryRun, refund = null) {
  const kind = draft.kind || 'withdrawal';
  const method = kind === 'freeze' ? 'schedule' : (draft.method || 'pro_rata');
  const override = method === 'pro_rata' && draft.refundOverrideMinor !== undefined
    && draft.refundOverrideMinor !== null && draft.refundOverrideMinor !== '' ? Number(draft.refundOverrideMinor) : null;
  return {
    kind,
    effectiveDate: draft.effectiveDate,
    reason: String(draft.reason || '').trim(),
    method,
    serviceStartDate: method === 'pro_rata' ? draft.serviceStartDate || null : null,
    serviceEndDate: method === 'pro_rata' ? draft.serviceEndDate || null : null,
    refundOverrideMinor: override,
    overrideReason: override !== null ? String(draft.overrideReason || '').trim() || null : null,
    refund,
    dryRun,
  };
}

/** Taslak denetimi (Akademi aynı kuralları 422 ile de uygular). */
export function withdrawalProblem(draft) {
  if (!draft.effectiveDate) return 'Etkin tarihi seçin.';
  if (String(draft.reason || '').trim().length < 3) return 'Gerekçe en az 3 karakter.';
  const body = withdrawalBody(draft, true);
  if (body.refundOverrideMinor !== null && String(body.overrideReason || '').length < 3) {
    return 'İade tutarını düzelttiniz: düzeltme gerekçesini yazın (en az 3 karakter).';
  }
  if (body.serviceStartDate && body.serviceEndDate && body.serviceEndDate <= body.serviceStartDate) {
    return 'Hizmet döneminin bitişi başlangıcından sonra olmalı.';
  }
  return null;
}

/** Ayrılma / kayıt dondurma: önizle (dryRun) → kalan taksitleri kapat (+ isteğe bağlı iade kaydı). */
export async function withdrawAccount(app, account, changed) {
  const studentId = account.student.id;
  const preview = h('div', 'ak-fin-preview');
  preview.append(h('div', 'ak-muted', 'Ayrılmada “Okunan süre hesabı” iade önerisini çıkarır: ödenen − katılım süresi '
    + 'oranında hizmet bedeli − kurum kesintisi. Dondurmada taksitler askıya alınır; öğrenci dönünce “Kaydı yeniden '
    + 'başlat” ile yeni vadelerle açılır. “Önizle” sonucu gösterir, hiçbir şey kaydetmez.'));
  const check = (draft) => {
    const problem = withdrawalProblem(draft);
    if (problem) throw new AkademiError({ message: problem });
  };
  const result = await formDialog(app, {
    title: `Ayrılma / kayıt dondurma — ${account.student?.displayName || ''}`,
    description: 'Para hareketi otomatik yapılmaz: iade edilecek tutarı elle ödeyip burada iade kaydı olarak '
      + 'yazarsınız (aynı işlemde ya da sonra “İade kaydı” ile). Veliye bildirim gider.',
    wide: true,
    value: { kind: 'withdrawal', method: 'pro_rata', effectiveDate: todayIst(), refundNow: '',
      refundMethod: 'transfer' },
    fields: [
      { key: 'kind', label: 'Tür', type: 'select', options: optionsOf(WITHDRAWAL_KINDS) },
      { key: 'effectiveDate', label: 'Etkin tarih', type: 'date', required: true,
        hint: 'Öğrencinin derse gelmediği ilk gün. Bu gün ve sonrası vadeli taksitler kapanır.' },
      { key: 'reason', label: 'Gerekçe', required: true, maxLength: 200, wide: true,
        placeholder: 'Örn. şehir değişikliği' },
      { key: 'method', label: 'İade hesabı', type: 'select', options: optionsOf(WITHDRAWAL_METHODS), wide: true,
        hint: 'Yalnız ayrılmada. Kayıt dondurma her zaman sözleşme takvimiyle yapılır.' },
      { key: 'serviceStartDate', label: 'Hizmet başlangıcı', type: 'date',
        hint: 'Boşsa plan takviminden (ilk taksit vadesi).' },
      { key: 'serviceEndDate', label: 'Hizmet bitişi', type: 'date',
        hint: 'Boşsa son taksitten bir dönem sonrası.' },
      { key: 'refundOverrideMinor', label: 'İade tutarını düzelt', type: 'money', min: 0,
        hint: 'Boşsa önerilen tutar uygulanır. Düzeltirseniz gerekçe zorunlu (denetim kaydına yazılır).' },
      { key: 'overrideReason', label: 'Düzeltme gerekçesi', maxLength: 200,
        placeholder: 'Örn. sözleşmedeki kamp günleri düşüldü' },
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
              withdrawalBody(draft, true), { key: `km-wd-preview-${Date.now()}` });
            preview.replaceChildren(withdrawalPreview(res));
          } catch (failure) {
            preview.replaceChildren(errorBox(failure));
          } finally { go.disabled = false; }
        } });
        wrap.append(go, preview);
        return wrap;
      } },
    ],
    submitLabel: 'Kaydı kapat',
    danger: true,
    onSubmit: async (draft, { key }) => {
      check(draft);
      let refund = null;
      if (draft.refundNow === 'yes') {
        let amount = Number(draft.refundAmountMinor) || 0;
        if (!amount) {
          const dry = await app.api.post(`/v1/admin/finance/accounts/${studentId}/withdrawal`,
            withdrawalBody(draft, true), { key: `${key}-dry` });
          amount = dry?.withdrawal?.refundableMinor || 0;
        }
        if (amount > 0) {
          refund = { amountMinor: amount, method: draft.refundMethod || 'transfer',
            reference: String(draft.refundReference || '').trim() || null,
            note: draft.kind === 'freeze' ? 'Dondurma iadesi' : 'Ayrılma iadesi' };
        }
      }
      return app.api.post(`/v1/admin/finance/accounts/${studentId}/withdrawal`,
        withdrawalBody(draft, false, refund), { key });
    },
  });
  if (!result) return;
  const w = result.withdrawal || {};
  const refundText = result.refund ? ` · iade ${result.refund.refundNo} ${money(result.refund.amountMinor)}`
    : (w.refundableMinor ? ` · iade edilebilir ${money(w.refundableMinor)}` : '');
  app.toast(`${WITHDRAWAL_KINDS[w.kind] || 'Kapanış'}: ${num(w.cancelledCount || 0)} taksit `
    + `${w.kind === 'freeze' ? 'askıya alındı' : 'iptal'}${refundText}`, 'good');
  changed();
}

/** Okunan süre dökümü (öneri ya da uygulanan). */
export function settlementRows(settlement) {
  return (settlement?.plans || []).map((plan) => ({
    id: plan.planId,
    title: plan.title,
    period: `${fmtDate(plan.serviceStartDate)} – ${fmtDate(plan.serviceEndDate)}`,
    days: `${num(plan.attendedDays)} / ${num(plan.totalDays)} gün`,
    contract: plan.contractMinor,
    paid: plan.paidMinor,
    fee: plan.serviceFeeMinor,
    deduction: plan.deductionMinor,
  }));
}

function settlementView(settlement) {
  const s = settlement;
  const wrap = h('div', 'ak-stack');
  const net = s.suggestedRefundMinor
    ? { label: 'Önerilen iade', value: money(s.suggestedRefundMinor), tone: 'good' }
    : { label: 'Aile borcu', value: money(s.owedMinor || 0), tone: s.owedMinor ? 'bad' : '' };
  wrap.append(h('b', '', s.applied ? 'Okunan süre hesabı (uygulanacak)' : 'Okunan süre hesabı (bilgi)'), kpiRow([
    { label: 'Plana ödenen', value: money(s.paidMinor) },
    { label: 'Hizmet bedeli (okunan süre)', value: money(s.serviceFeeMinor) },
    { label: `Kurum kesintisi %${num(s.deductionPercent)}`, value: money(s.deductionMinor) },
    net,
  ]));
  wrap.append(dataTable({
    columns: [
      { key: 'title', label: 'Plan', width: 'minmax(0, 1.5fr)' },
      { key: 'period', label: 'Hizmet dönemi' },
      { key: 'days', label: 'Okunan' },
      { key: 'contract', label: 'Sözleşme', align: 'num', cell: (row) => money(row.contract) },
      { key: 'paid', label: 'Ödenen', align: 'num', cell: (row) => money(row.paid) },
      { key: 'fee', label: 'Bedel', align: 'num', cell: (row) => money(row.fee) },
      { key: 'deduction', label: 'Kesinti', align: 'num', cell: (row) => money(row.deduction) },
    ],
    rows: settlementRows(s),
    dense: true,
    empty: empty('Etkin tarihte süren plan yok'),
  }).node);
  if (s.applied && s.overrideReason) {
    wrap.append(alertBox(`Yönetici düzeltmesi: aileye ${money(s.appliedRefundMinor)} iade · ${s.overrideReason}`,
      'warn'));
  } else if (!s.applied) {
    wrap.append(h('div', 'ak-muted', 'Sözleşme takvimi seçili: bu öneri yalnız bilgi içindir. Uygulamak için '
      + '“İade hesabı”nı “Okunan süre hesabı” yapın.'));
  }
  return wrap;
}

function withdrawalPreview(res) {
  const w = res?.withdrawal || {};
  const wrap = h('div', 'ak-stack');
  if (w.kind === 'freeze') {
    wrap.append(alertBox('Kayıt dondurma: taksitler askıya alınır, ödenmiş kısmı hesapta alacak olarak kalır. '
      + 'Öğrenci dönünce cari kartındaki “Kaydı yeniden başlat” taksitleri aynı tutarlarla yeni vadelerle açar.',
    'info'));
  }
  wrap.append(kpiRow([
    { label: 'İptal edilecek', value: `${num(w.cancelledCount || 0)} kalem · ${money(w.cancelledMinor || 0)}` },
    { label: 'Alacağa geçecek (ödenmiş)', value: money(w.releasedMinor || 0) },
    { label: 'Kalan borç', value: money(w.remainingDebtMinor || 0), tone: w.remainingDebtMinor ? 'bad' : '' },
    { label: 'İade edilebilir', value: money(w.refundableMinor || 0), tone: w.refundableMinor ? 'good' : '' },
  ]));
  if (w.settlement) wrap.append(settlementView(w.settlement));
  if ((res?.settlementCharges || []).length) {
    wrap.append(dataTable({
      columns: [
        { key: 'description', label: 'Yazılacak kalem', width: 'minmax(0, 3fr)' },
        { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
      ],
      rows: res.settlementCharges,
      dense: true,
    }).node);
  }
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

// ------------------------------------------------------- dondurmadan dönüş

/** "Kaydı yeniden başlat": dondurulan taksitler aynı tutarla yeni vadelerle açılır (önizlemeli). */
export async function resumeFrozen(app, withdrawal, changed) {
  const url = `/v1/admin/finance/withdrawals/${withdrawal.id}/resume`;
  const bodyOf = (draft, dryRun) => ({ firstDueDate: draft.firstDueDate || null,
    note: String(draft.note || '').trim() || null, dryRun });
  const preview = h('div', 'ak-fin-preview');
  preview.append(h('div', 'ak-muted', '“Önizle” eski ve yeni vadeleri gösterir.'));
  const result = await formDialog(app, {
    title: `Kaydı yeniden başlat — dondurma ${fmtDate(withdrawal.effectiveDate)}`,
    description: 'Dondurulan taksitler aynı tutarlarla yeni vadelerle açılır; plan yeniden etkinleşir, indirim / '
      + 'burs korunur, hesaptaki alacak yeni taksitlere mahsup edilir. Veliye bildirim gider. Bir kez yapılır.',
    wide: true,
    value: { firstDueDate: '' },
    fields: [
      { key: 'firstDueDate', label: 'İlk yeni vade', type: 'date',
        hint: 'Boşsa: eski ilk vade ileride ise o; değilse bugünden sonraki ilk aynı gün. Aylık planda diğer '
          + 'taksitler ay ay kayar.' },
      { key: 'note', label: 'Not', maxLength: 200, placeholder: 'Örn. tedavi bitti, derslere döndü' },
      { key: 'preview', label: 'Önizleme', type: 'custom', render: (draft) => {
        const wrap = h('div', 'ak-stack');
        const go = button('Önizle', { onClick: async () => {
          go.disabled = true;
          try {
            const res = await app.api.post(url, bodyOf(draft, true), { key: `km-resume-preview-${Date.now()}` });
            preview.replaceChildren(resumePreview(res));
          } catch (failure) {
            preview.replaceChildren(errorBox(failure));
          } finally { go.disabled = false; }
        } });
        wrap.append(go, preview);
        return wrap;
      } },
    ],
    submitLabel: 'Taksitleri yeni vadelerle aç',
    onSubmit: (draft, { key }) => app.api.post(url, bodyOf(draft, false), { key }),
  });
  if (!result || result === true) return;
  const first = result.withdrawal?.resumedFirstDueDate;
  app.toast(`Kayıt yeniden başlatıldı: ${num((result.charges || []).length)} taksit yeni vadelerle açıldı`
    + `${first ? ` · ilk vade ${fmtDate(first)}` : ''}.`, 'good');
  changed();
}

function resumePreview(res) {
  const wrap = h('div', 'ak-stack');
  const total = (res?.shifts || []).reduce((sum, row) => sum + Number(row.amountMinor || 0), 0);
  wrap.append(kpiRow([
    { label: 'Açılacak taksit', value: num((res?.shifts || []).length) },
    { label: 'Toplam', value: money(total) },
    { label: 'Yeniden etkinleşen plan', value: num((res?.reopenedPlanIds || []).length) },
  ]));
  wrap.append(dataTable({
    columns: [
      { key: 'description', label: 'Taksit', width: 'minmax(0, 2fr)' },
      { key: 'old', label: 'Eski vade', cell: (row) => fmtDate(row.oldDueDate) },
      { key: 'new', label: 'Yeni vade', cell: (row) => h('b', '', fmtDate(row.newDueDate)) },
      { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
    ],
    rows: res?.shifts || [],
    dense: true,
    empty: empty('Açılacak taksit yok'),
  }).node);
  return wrap;
}

// ------------------------------------------------------------- belgeler

/** İade makbuzu: Akademi'den veri → kurum logolu A4 (önizle / yazdır). */
export function printRefundReceipt(app, refund, opener = null) {
  return runDocument(app, async () => refundReceiptDoc(
    await app.api.get(`/v1/admin/finance/refunds/${refund.id}/receipt`)), { title: 'İade makbuzu', opener });
}

/** Ödeme planı belgesi (kayıt sözleşmesi eki; veli + kurum imza alanı). */
export function printPlanAgreement(app, plan, opener = null) {
  return runDocument(app, async () => paymentPlanDoc(
    await app.api.get(`/v1/admin/finance/installment-plans/${plan.id}/agreement`)), { title: 'Ödeme planı', opener });
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
        { key: 'method', label: 'Hesap', cell: (row) => (row.kind === 'freeze' ? '—'
          : METHOD_SHORT[row.method] || METHOD_SHORT.schedule) },
        { key: 'by', label: 'Kaydeden', cell: (row) => personName(row.createdBy) },
        { key: 'resume', label: '', width: '170px', cell: (row) => {
          if (row.resumedAt) {
            return h('span', 'ak-muted', `Yeniden başlatıldı ${fmtDate(row.resumedAt)} · ${num(row.resumedCount || 0)} taksit`);
          }
          return row.resumable ? actionButton(app, 'admin_resumeFrozenAccount', 'Kaydı yeniden başlat', {
            variant: 'ghost', onClick: () => resumeFrozen(app, row, changed) }) : '';
        } },
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
        { key: 'actions', label: '', width: '170px', cell: (row) => {
          const actions = h('div', 'ak-row-actions');
          const receipt = actionButton(app, 'admin_getRefundReceipt', 'Makbuz', { variant: 'ghost',
            title: 'Veliye imzalatılacak iade makbuzu (önizle / yazdır).',
            onClick: (event) => printRefundReceipt(app, row, event?.currentTarget || null) });
          actions.append(receipt);
          if (row.status === 'completed') {
            actions.append(actionButton(app, 'admin_voidRefund', 'İptal', { variant: 'ghost',
              onClick: () => voidRefund(app, row, changed) }));
          }
          return actions;
        } },
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
