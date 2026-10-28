// BBD Akademi — tek sözleşmenin ayrıntısı (öğrencinin Sözleşmeler sekmesinde; F-SZ, iş 80).
//
// A7 tek okumadır (toplamlar, taksitler, satırlar, sürümler, ekler, maskeli taraf, eksikler hepsi içinde; satır başına
// istek yok). Kimlik değişti bandı için A4 bir kez, ARKADAN okunur (çizimi bekletmez).
//
// Eylemler (izin yoksa gerekçeli kapalı, kurumda kapalıysa hiç çizilmez — `actionButton`):
//   İmzalandı (A10) · Tarama ekle (A12) · Maddeleri düzenle (A8) · Güncel bilgilerle yenile (A8 refresh) ·
//   Velinin gördüğü · Belgeler: tür başına Aç (A14) + seçili türler tek PDF önizle / yazdır (A17) · Belgeleri üret (A9) ·
//   İmzayı geri al (A11, gerekçe) · Ek aç (A15) / sil (A13, gerekçe) · İptal et (A16, gerekçe).
// Her yazmadan sonra yerel `contract.changed` olayı yayılır (açık başka ekranlar da tazelenir).

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { problem } from '../api.js';
import {
  actionButton, empty, fmtDate, fmtDateTime, money, num, reasonDialog, runAction, statusBadge, toolbar,
} from '../common.js';
import {
  attachDialog, clausesDialog, guardianDialog, openAttachment, promissoryDialog, signDialog,
} from './contract-dialogs.js';
import { promissoryText } from './wizard-logic.js';
import {
  buildDocuments, contractSettings, editInStudentsPanel, emitContractEvent, openContractDocument, paths,
} from './shared-contract.js';
import {
  CONTRACT_STATUSES, DOCUMENT_KINDS, INSTALLMENT_METHODS, MEB_FEE_CATEGORIES, SIGNATURE_SOURCES,
  latestRevision, missingText, needsRender, partyChanged, readyKinds, signatureChip,
} from './shared-contract-logic.js';

const CHARGE_STATUS = { open: ['Açık', 'info'], partially_paid: ['Kısmi ödendi', 'warn'], paid: ['Ödendi', 'good'],
  overdue: ['Gecikmiş', 'bad'], cancelled: ['İptal', 'dim'] };
const LIVE_KINDS = ['payment_plan', 'legal_notice'];

/**
 * @param {object} app — öğrenci kartının uygulama görünümü (pasif öğrencide yazmalar kapalı)
 * @param {string} contractId
 * @param {{studentId: string, kmId?: () => string|null, name?: () => string, onChanged?: (detail) => void}} options
 * @returns {{node, reload(), refreshParty(), id, destroy()}}
 */
export function contractDetail(app, contractId, { studentId, kmId = () => null, name = () => '', onChanged } = {}) {
  const node = h('div', 'ak-ct-detail');
  node.setAttribute('aria-live', 'polite');
  let detail = null;
  let token = 0;
  let identityUpdatedAt = null;
  const band = h('div', 'ak-ct-band');

  async function reload() {
    const mine = ++token;
    if (!detail) node.replaceChildren(h('div', 'ak-muted', 'Sözleşme yükleniyor…'));
    try {
      const data = await app.api.get(paths.contract(contractId));
      if (mine !== token) return;
      detail = data;
      node.replaceChildren(...render(data));
      refreshParty();
    } catch (failure) {
      if (mine !== token) return;
      node.replaceChildren(alertBox(problem(failure, 'Sözleşme okunamadı.'), 'bad'),
        button('Tekrar dene', { variant: 'ghost', onClick: () => reload() }));
    }
  }

  /**
   * "Kimlik bilgileri değişti": Akademi `ContractDetail.partyChanged` verirse o esastır (KM'de kimlik düzenlenince ayrıntı
   * yeniden okunur); vermezse A4 `identityUpdatedAt` son sürümün tarihiyle karşılaştırılır.
   */
  async function refreshParty({ reread = false } = {}) {
    if (!detail || detail.status !== 'active') { band.replaceChildren(); return; }
    if (typeof detail.partyChanged === 'boolean') {
      if (reread) await reload();
      else paintBand();
      return;
    }
    try {
      const party = await app.api.get(paths.party(studentId));
      identityUpdatedAt = party?.identityUpdatedAt || null;
    } catch { identityUpdatedAt = null; }
    paintBand();
  }

  function paintBand() {
    band.replaceChildren();
    const changedParty = typeof detail?.partyChanged === 'boolean' ? detail.partyChanged : partyChanged(detail || {}, identityUpdatedAt);
    if (!detail || !changedParty) return;
    const box = h('div', 'ak-ct-alert');
    box.append(alertBox('Kimlik bilgileri değişti — belgeler eski bilgilerle. "Güncel bilgilerle yenile" yeni sürüm '
      + 'oluşturur (imzalıysa yeniden imza gerekir).', 'warn'), refreshButton());
    band.append(box);
  }

  async function changed(next, change) {
    if (next && typeof next === 'object' && next.id) detail = next;
    emitContractEvent({ type: 'contract.changed', contractId, studentId, change });
    onChanged?.(detail);
    await reload();
  }

  const write = (operationId, label, onClick, { variant = 'ghost', title = '' } = {}) =>
    actionButton(app, operationId, label, { variant, title, onClick });

  function refreshButton() {
    return write('admin_reviseContract', 'Güncel bilgilerle yenile', async () => {
      const reason = await reasonDialog(app, { title: 'Güncel bilgilerle yenile', danger: false,
        description: 'Öğrenci / veli kimlik bilgileri ve ödeme planı güncel haliyle yeni sürüme alınır; belgeler yeniden '
          + 'üretilir. İmzalı sözleşmede yeni sürüm yeniden imza bekler.',
        confirmLabel: 'Yenile', presets: ['Kimlik bilgileri güncellendi', 'Ödeme planı değişti'] });
      if (!reason) return;
      const next = await runAction(app, () => app.api.post(`/v1/admin/contracts/${contractId}/revisions`,
        { reason, refresh: true }, { key: `km-ct-refresh-${contractId}-${Date.now().toString(36)}` }),
      { success: 'Yeni sürüm oluşturuldu.' });
      if (next) changed(next, 'revised');
    }, { title: 'Kimlik ve plan bilgilerini yeni sürüme alır.' });
  }

  function render(data) {
    const active = data.status === 'active';
    const latest = latestRevision(data);
    const [signText, signTone] = signatureChip(data);
    const head = h('div', 'ak-ct-head');
    const title = h('div', 'ak-ct-title');
    title.append(h('b', '', `Sözleşme ${data.number || '—'}`), statusBadge(CONTRACT_STATUSES, data.status),
      badge(signText, signTone));
    const meta = [data.academicYear, `sözleşme ${fmtDate(data.contractDate)}`, `kayıt ${fmtDate(data.registeredOn)}`,
      data.payerGuardian?.name ? `ödeme sorumlusu ${data.payerGuardian.name}` : '',
      latest ? `sürüm ${latest.no}` : ''].filter(Boolean).join(' · ');
    head.append(title, h('div', 'ak-ct-meta', meta));

    const out = [head, band];
    if ((data.missingFields || []).length && active) {
      const box = h('div', 'ak-ct-alert');
      box.append(alertBox(`Belge için eksik bilgi: ${data.missingFields.map(missingText).join(' · ')}. `
        + 'Eksik bilgiyle o belge üretilmez (senet: ödeme sorumlusu velinin ad soyad ve TC\'si şart).', 'bad'));
      const km = kmId();
      if (km) {
        box.append(button('Öğrenciler panelinde düzenle', { onClick: () => editInStudentsPanel(app,
          { kmId: km, name: name(), onClosed: () => setTimeout(() => refreshParty({ reread: true }), 1500) }) }));
      }
      out.push(box);
    }
    if (data.signature?.state === 'resign_required') {
      out.push(alertBox(`Son sürüm henüz imzalanmadı. Veli uygulamada imzalı sürüm ${data.signature.signedRevisionNo}'i görür.`, 'info'));
    }

    if (active) {
      const primary = data.signature?.state !== 'signed';
      out.push(toolbar(
        write('admin_signContract', 'İmzalandı', async () => {
          const next = await signDialog(app, data, { revisionNo: latest?.no });
          if (next) { app.toast('İmza kaydedildi; veli imzalı belgeleri uygulamada görür.', 'good'); changed(next, 'signed'); }
        }, { variant: primary ? 'primary' : 'ghost', title: 'Islak imzalı nüshayı işaretle (taranmış sayfalarla ya da tek başına).' }),
        write('admin_addContractAttachment', 'Tarama ekle', async () => {
          const next = await attachDialog(app, data);
          if (next) { app.toast('Ek eklendi.', 'good'); changed(null, 'attachment'); }
        }),
        write('admin_reviseContract', 'Maddeleri düzenle', async () => {
          const next = await clausesDialog(app, data);
          if (next) { app.toast('Yeni sürüm oluşturuldu; belgeler yeni metinle üretildi.', 'good'); changed(next, 'revised'); }
        }),
        // Tek senet / her taksit (taslak "EK — TEK SENET"); peşin sözleşmede senet yok.
        data.payment?.mode === 'installment' && (data.payment.installments || []).length
          ? write('admin_reviseContract', 'Senet türünü değiştir', async () => {
            const next = await promissoryDialog(app, data);
            if (next) { app.toast('Yeni sürüm oluşturuldu; senetler yeniden üretildi.', 'good'); changed(next, 'revised'); }
          }, { title: 'Her taksit için ayrı senet ya da tek senet (toplam tutar).' })
          : null,
        refreshButton(),
        button('Velinin gördüğü', { variant: 'ghost', onClick: () => guardianDialog(app, data) }),
        h('span', 'kit-spacer'),
        write('admin_cancelContract', 'İptal et', async () => {
          const reason = await reasonDialog(app, { title: `Sözleşme ${data.number} iptal edilsin mi?`,
            description: 'Sözleşme "İptal" olur ve veliden kalkar. Taksit planı İPTAL EDİLMEZ (ayrı finans işlemi: Kurum '
              + 'finansı → Taksit planları).', confirmLabel: 'Sözleşmeyi iptal et' });
          if (!reason) return;
          const next = await runAction(app, () => app.api.post(`/v1/admin/contracts/${contractId}:cancel`, { reason },
            { key: `km-ct-cancel-${contractId}` }), { success: 'Sözleşme iptal edildi.' });
          if (next) changed(next, 'cancelled');
        }),
      ));
    } else {
      out.push(toolbar(button('Velinin gördüğü', { variant: 'ghost', onClick: () => guardianDialog(app, data) })));
    }

    const totals = data.totals || {};
    out.push(kpiRow([
      { label: 'Sözleşme tutarı', value: money(totals.contractMinor ?? data.totalMinor) },
      { label: 'Ödenen', value: money(totals.paidMinor) },
      { label: 'Kalan', value: money(totals.remainingMinor) },
      { label: 'Gecikmiş', value: money(totals.overdueMinor), tone: totals.overdueMinor ? 'bad' : '' },
    ]));
    out.push(documentsCard(data, latest), paymentCard(data), linesCard(data), revisionsCard(data), attachmentsCard(data));
    return out;
  }

  // ---------------------------------------------------------------- belgeler

  function documentsCard(data, latest) {
    const box = h('div', 'ak-ct-docs');
    const ready = new Set(readyKinds(latest || {}));
    const kinds = Object.keys(DOCUMENT_KINDS).filter((kind) => LIVE_KINDS.includes(kind)
      || (latest?.documents || []).some((item) => item.kind === kind));
    const chosen = new Set(kinds.filter((kind) => ready.has(kind) || LIVE_KINDS.includes(kind))
      .filter((kind) => kind !== 'legal_notice'));
    for (const kind of kinds) {
      const available = ready.has(kind) || LIVE_KINDS.includes(kind);
      const row = h('label', 'ak-ct-doc');
      const check = h('input', 'kit-check');
      check.type = 'checkbox';
      check.checked = chosen.has(kind);
      check.disabled = !available;
      check.setAttribute('aria-label', `${DOCUMENT_KINDS[kind]} — tek PDF'e kat`);
      check.addEventListener('change', () => { if (check.checked) chosen.add(kind); else chosen.delete(kind); });
      const state = LIVE_KINDS.includes(kind) ? badge('Güncel', 'info')
        : available ? badge('Hazır', 'good') : badge('Üretilmedi', 'warn');
      const open = button('Aç', { variant: 'ghost', disabled: !available,
        onClick: (event) => openContractDocument(app, data, kind, { opener: event?.currentTarget || null }) });
      open.setAttribute('aria-label', `${DOCUMENT_KINDS[kind]} — aç`);
      row.append(check, h('span', 'ak-ct-doc-name', DOCUMENT_KINDS[kind]), state, h('span', 'kit-spacer'), open);
      box.append(row);
    }
    const settingsCopies = async () => (await contractSettings(app)).defaultCopies;
    const run = async (print, control) => {
      const selected = kinds.filter((kind) => chosen.has(kind));
      if (!selected.length) { app.toast('En az bir belge seçin.', 'bad'); return; }
      control.disabled = true;
      try {
        const defaults = await settingsCopies();
        const copies = Object.fromEntries(selected.map((kind) => [kind, defaults[kind] || 1]));
        const job = await buildDocuments(app, { contractIds: [contractId], kinds: selected,
          options: { copies, promissoryScope: 'all', revision: 'latest' } }, { print, opener: control,
          name: `sozlesme-${data.number || ''}` });
        if (job?.skipped?.length) {
          app.toast(`Eksik bilgi yüzünden üretilemeyen belge var: ${job.skipped.flatMap((item) => item.missingFields || [])
            .map(missingText).join(' · ') || 'kimlik bilgileri'}.`, 'bad');
        }
      } finally { control.disabled = false; }
    };
    const actions = h('div', 'ak-ct-doc-actions');
    const preview = actionButton(app, 'admin_buildContractDocuments', 'Seçilenler · tek PDF önizle',
      { onClick: (event) => run(false, event?.currentTarget || preview) });
    const print = actionButton(app, 'admin_buildContractDocuments', 'Yazdır', { variant: 'primary',
      onClick: (event) => run(true, event?.currentTarget || print) });
    print.setAttribute('aria-label', 'Seçili belgeleri yazdır');
    actions.append(preview, print);
    if (latest && needsRender(latest) && data.status === 'active') {
      actions.prepend(renderButton(latest.no));
    }
    box.append(actions);
    return card('Belgeler', box, latest ? `sürüm ${latest.no}` : '');
  }

  function renderButton(revisionNo) {
    return write('admin_renderContractRevision', 'Belgeleri üret', async () => {
      const next = await runAction(app, () => app.api.post(
        `/v1/admin/contracts/${contractId}/revisions/${revisionNo}:render`, {},
        { key: `km-ct-render-${contractId}-${revisionNo}-${Date.now().toString(36)}` }),
      { success: 'Belgeler güncel kimlik bilgileriyle üretildi.' });
      if (next) changed(next, 'revised');
    }, { variant: 'primary', title: 'Eksik bilgi yüzünden üretilemeyen belgeleri şimdi üretir (yeni sürüm gerekmez).' });
  }

  // ---------------------------------------------------------------- ödeme planı

  function paymentCard(data) {
    const payment = data.payment || {};
    const rows = [];
    if (payment.downPayment && payment.downPayment.amountMinor) {
      rows.push({ no: 'Peşinat', ...payment.downPayment, status: payment.downPayment.status || null });
    }
    rows.push(...(payment.installments || []));
    const body = h('div', 'ak-stack');
    // "Senet: Tek senet (vade 05.06.2027)" / "Her taksit için ayrı" / "Senet yok (peşin)".
    body.append(h('div', 'ak-ct-meta', `Senet: ${promissoryText(data)}`));
    const table = dataTable({
      dense: true,
      columns: [
        { key: 'no', label: 'No', width: '70px', cell: (row) => String(row.no) },
        { key: 'dueOn', label: 'Vade', width: '100px', cell: (row) => fmtDate(row.dueOn) },
        { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
        { key: 'method', label: 'Tür', cell: (row) => INSTALLMENT_METHODS[row.method] || row.method || '—' },
        { key: 'paid', label: 'Ödenen', align: 'num', cell: (row) => (row.paidMinor === undefined ? '—' : money(row.paidMinor)) },
        { key: 'status', label: 'Durum', cell: (row) => (row.status ? statusBadge(CHARGE_STATUS, row.status) : '—') },
      ],
      rows,
      empty: empty('Taksit yok', payment.mode === 'cash' ? 'Peşin ödeme.' : ''),
    });
    body.append(table.node);
    return card('Ödeme planı', body, payment.mode === 'cash' ? 'peşin' : `${num((payment.installments || []).length)} taksit`);
  }

  function linesCard(data) {
    const table = dataTable({
      dense: true,
      columns: [
        { key: 'category', label: 'Ürün türü', cell: (row) => MEB_FEE_CATEGORIES[row.category] || row.category || '—' },
        { key: 'name', label: 'Ürün', width: 'minmax(0, 2fr)' },
        { key: 'unit', label: 'Birim fiyatı', align: 'num', cell: (row) => money(row.unitPriceMinor) },
        { key: 'quantity', label: 'Miktar', align: 'num', cell: (row) => num(row.quantity, Number.isInteger(row.quantity) ? 0 : 2) },
        { key: 'amount', label: 'Tutar', align: 'num', cell: (row) => money(row.amountMinor) },
        { key: 'discount', label: 'İndirim', align: 'num', cell: (row) => money(row.discountMinor) },
        { key: 'total', label: 'Toplam', align: 'num', cell: (row) => money(row.totalMinor) },
      ],
      rows: data.lines || [],
      empty: empty('Hizmet satırı yok'),
    });
    const reasons = (data.discountReasons || []).length ? `indirim: ${data.discountReasons.join(', ')}` : '';
    return card('Hizmetler', table.node, reasons);
  }

  // ---------------------------------------------------------------- sürümler

  function revisionsCard(data) {
    const list = h('ol', 'ak-ct-timeline');
    for (const revision of [...(data.revisions || [])].sort((a, b) => b.no - a.no)) {
      const item = h('li', 'ak-ct-rev');
      const top = h('div', 'ak-ct-rev-top');
      top.append(h('b', '', `Sürüm ${revision.no}`), h('span', 'ak-muted', `${fmtDateTime(revision.createdAt)} · `
        + `${revision.createdByLabel || '—'}`));
      if (revision.signature) top.append(badge('İmzalı', 'good'));
      item.append(top);
      if (revision.reason) item.append(h('div', 'ak-ct-rev-reason', revision.reason));
      const docs = h('div', 'ak-ct-rev-docs');
      for (const doc of revision.documents || []) {
        docs.append(badge(DOCUMENT_KINDS[doc.kind] || doc.kind, doc.ready ? 'info' : 'warn'));
      }
      item.append(docs);
      if (revision.signature) {
        const sig = revision.signature;
        const line = h('div', 'ak-ct-rev-sign');
        line.append(h('span', '', `İmza ${fmtDate(sig.signedOn)} · ${SIGNATURE_SOURCES[sig.source] || sig.source || ''} · `
          + `${(sig.kinds || []).map((kind) => DOCUMENT_KINDS[kind] || kind).join(', ')}`
          + `${sig.byLabel ? ` · işaretleyen ${sig.byLabel}` : ''}${sig.note ? ` · ${sig.note}` : ''}`));
        if (data.status === 'active') {
          line.append(write('admin_unsignContract', 'İmzayı geri al', async () => {
            const reason = await reasonDialog(app, { title: `Sürüm ${revision.no} imzası geri alınsın mı?`,
              description: 'Yanlış işaretlemeyi düzeltir (denetim kaydına yazılır). İmzalı belgeler veliden kalkar.',
              confirmLabel: 'İmzayı geri al', presets: ['Yanlış sürüm işaretlendi', 'Yanlış öğrenci'] });
            if (!reason) return;
            const next = await runAction(app, () => app.api.del(
              `/v1/admin/contracts/${contractId}/signatures/${revision.no}`, { query: { reason } }),
            { success: 'İmza geri alındı.' });
            if (next !== undefined) changed(next, 'unsigned');
          }));
        }
        item.append(line);
      } else if (data.status === 'active' && needsRender(revision)) {
        item.append(renderButton(revision.no));
      }
      list.append(item);
    }
    if (!list.childElementCount) list.append(h('li', 'ak-muted', 'Sürüm yok.'));
    return card('Sürümler', list);
  }

  // ---------------------------------------------------------------- ekler

  function attachmentsCard(data) {
    const table = dataTable({
      dense: true,
      columns: [
        { key: 'label', label: 'Ek', width: 'minmax(0, 2fr)', cell: (row) => row.label || 'Taranmış nüsha' },
        { key: 'revision', label: 'Sürüm', width: '70px', cell: (row) => String(row.revisionNo) },
        { key: 'pages', label: 'Sayfa', align: 'num', width: '70px', cell: (row) => num(row.pageCount || 0) },
        { key: 'uploaded', label: 'Yükleyen', cell: (row) => `${row.uploadedByLabel || '—'} · ${fmtDateTime(row.uploadedAt)}` },
        { key: 'actions', label: '', width: '130px', cell: (row) => {
          const actions = h('div', 'ak-row-actions');
          const open = button('Aç', { variant: 'ghost', onClick: (event) => openAttachment(app, data, row, event?.currentTarget) });
          open.setAttribute('aria-label', `${row.label || 'Taranmış nüsha'} — aç`);
          actions.append(open);
          if (data.status === 'active') {
            actions.append(write('admin_deleteContractAttachment', 'Sil', async () => {
              const reason = await reasonDialog(app, { title: 'Ek silinsin mi?',
                description: 'Taranmış dosya sözleşmeden kaldırılır (denetim kaydına yazılır).', confirmLabel: 'Sil' });
              if (!reason) return;
              const done = await runAction(app, () => app.api.del(
                `/v1/admin/contracts/${contractId}/attachments/${row.id}`, { query: { reason } }), { success: 'Ek silindi.' });
              if (done !== undefined) changed(null, 'attachment');
            }));
          }
          return actions;
        } },
      ],
      rows: data.attachments || [],
      empty: empty('Ek yok', 'İmzalı nüsha taramaları "İmzalandı" ile, diğer ekler "Tarama ekle" ile gelir.'),
    });
    return card('Taranmış nüshalar ve ekler', table.node);
  }

  reload();
  return {
    node,
    id: contractId,
    reload,
    refreshParty,
    get detail() { return detail; },
    destroy() { token += 1; },
  };
}
