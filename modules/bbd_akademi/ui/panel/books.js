// BBD Akademi — Kitap talepleri (F-A12, F-S12, F-P6).
//
// Akış Akademi'dedir (ARCHITECTURE §3.8): öğrenci ister → veli onaylar (kart
// ya da cari) → bbdstore siparişi → teslim. Buradan kuyruk izlenir, düşen
// sipariş yeniden denenir, teslim işaretlenir ve sipariş PDF'i yazdırılır.
// "Cari hesaba ekle" onayı kurum carisine kalem yazar (kantinle ilgisi yok).

import { h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow } from '../../ui-kit/layout.js';
import { timeline } from '../../ui-kit/flow.js';
import { openBlob } from './api.js';
import {
  actionButton, fmtDateTime, formDialog, kv, loader, money, num, openDrawer, pagedList,
  personName, runAction, sectionHead, statusBadge, todayIst,
} from './common.js';

export const BOOK_STATUS = {
  requested: ['Veli onayı bekliyor', 'info'],
  awaiting_payment: ['Ödeme bekleniyor', 'warn'],
  approved: ['Onaylandı', 'info'],
  ordering: ['Sipariş veriliyor', 'info'],
  ordered: ['Sipariş verildi', 'good'],
  order_failed: ['Sipariş başarısız', 'bad'],
  delivered: ['Teslim edildi', 'good'],
  rejected: ['Reddedildi', 'dim'],
  cancelled: ['İptal', 'dim'],
  expired: ['Süresi doldu', 'dim'],
};
const PAYMENT_CHOICE = { card: 'Kartla ödendi', account: 'Kurum carisine eklendi' };

export function section(app) {
  loadStyles(import.meta.url, 'books.css');
  const node = h('div', 'ak-stack');
  let status = 'order_failed';
  node.append(sectionHead('Kitap talepleri', {
    subtitle: 'Öğrencilerin bbdstore kitap talepleri: kuyruk, sipariş, teslim.' }));

  const chips = chipRow([
    { key: 'order_failed', label: 'Siparişi düşen' },
    { key: 'requested', label: 'Veli onayı bekleyen' },
    { key: 'awaiting_payment', label: 'Ödeme bekleyen' },
    { key: 'approved', label: 'Onaylanan' },
    { key: 'ordered', label: 'Siparişi verilen' },
    { key: 'delivered', label: 'Teslim edilen' },
  ], status, (key) => { status = key || ''; list.reload(); });
  node.append(chips.node);

  const list = pagedList({
    app,
    path: '/v1/admin/book-requests',
    query: () => ({ status: status || undefined }),
    columns: [
      { key: 'ref', label: 'Talep', width: '120px' },
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.3fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.student?.displayName || '—'), h('small', '', row.student?.classGroup?.name || ''));
        return cell;
      } },
      { key: 'product', label: 'Kitap', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.product?.title || '—'),
          h('small', '', [row.product?.publisher, row.product?.isbn].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'qty', label: 'Adet', align: 'num', cell: (row) => num(row.quantity) },
      { key: 'total', label: 'Tutar', align: 'num', cell: (row) => money(row.totalMinor ?? row.product?.priceMinor) },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(BOOK_STATUS, row.status) },
      { key: 'at', label: 'Talep', width: '130px', cell: (row) => fmtDateTime(row.requestedAt) },
    ],
    onRow: (row) => openRequest(app, row.id, (next) => list.replace((item) => item.id === next.id, next)),
    empty: { title: 'Bu durumda talep yok', text: 'Başka bir durum seçin.' },
  });
  node.append(list.node);

  let started = false;
  return {
    node,
    shown(payload) {
      if (!started) { started = true; list.reload(); }
      if (payload?.bookRequestId) openRequest(app, payload.bookRequestId, () => list.reload());
    },
    destroy() { list.destroy(); },
  };
}

export function openRequest(app, id, onChange) {
  const panel = openDrawer(app, { title: 'Kitap talebi' });
  const view = loader(() => app.api.get(`/v1/admin/book-requests/${id}`), (request) => {
    panel.setTitle(`${request.ref} — ${request.product?.title || ''}`);
    const nodes = [];
    if (request.status === 'order_failed') {
      nodes.push(alertBox(`bbdstore siparişi oluşturulamadı${request.order?.failureCode
        ? ` (${request.order.failureCode})` : ''}. Stok ya da mağaza erişimi düzelince yeniden deneyin.`, 'bad'));
    }
    nodes.push(card('', kv([
      ['Öğrenci', `${request.student?.displayName || ''} · ${request.student?.classGroup?.name || ''}`],
      ['Kitap', request.product?.title],
      ['Yayınevi / ISBN', [request.product?.publisher, request.product?.isbn].filter(Boolean).join(' · ') || '—'],
      ['Adet × fiyat', `${num(request.quantity)} × ${money(request.product?.priceMinor)}`],
      ['Toplam', money(request.totalMinor ?? (request.product?.priceMinor || 0) * (request.quantity || 1))],
      ['Durum', statusBadge(BOOK_STATUS, request.status)],
      ['Not', request.note || '—'],
      ['İsteyen', `${personName(request.requestedBy)} · ${fmtDateTime(request.requestedAt)}`],
      ['Veli kararı', request.decision?.decidedAt ? `${personName(request.decision.by)} · `
        + `${PAYMENT_CHOICE[request.decision.paymentChoice] || request.decision.paymentChoice || ''}`
        + `${request.decision.rejectReason ? ` · ${request.decision.rejectReason}` : ''}` : '—'],
      ['Ödeme', request.paymentRef || (request.chargeId ? 'Kurum carisine kalem yazıldı' : '—')],
      ['Sipariş', request.order?.incrementId ? `#${request.order.incrementId} · ${request.order.status || ''} · `
        + `${fmtDateTime(request.order.placedAt)}` : '—'],
      ['Teslim', fmtDateTime(request.deliveredAt)],
    ])));
    const tools = h('div', 'ak-inline');
    if (request.status === 'order_failed') {
      tools.append(actionButton(app, 'admin_retryBookOrder', 'Siparişi yeniden dene', { variant: 'primary',
        onClick: async () => {
          const next = await runAction(app, () => app.api.post(`/v1/admin/book-requests/${id}/retry-order`,
            undefined, { key: `retry-${id}-${Date.now()}` }), { success: 'Sipariş yeniden denendi.' });
          if (next) { onChange?.(next); view.reload(); }
        } }));
    }
    if (request.status === 'ordered') {
      tools.append(actionButton(app, 'admin_deliverBook', 'Teslim edildi', { variant: 'primary',
        onClick: () => deliver(request) }));
    }
    if (['ordered', 'delivered', 'ordering'].includes(request.status) || request.order?.orderId) {
      tools.append(actionButton(app, 'admin_getBookOrderPdf', 'Sipariş PDF', { onClick: async () => {
        await runAction(app, async () => {
          const signed = await app.api.get(`/v1/admin/book-requests/${id}/order-pdf`);
          openBlob(await app.api.fetchFile(signed.url, `${request.ref}.pdf`));
        });
      } }));
    }
    if (tools.childElementCount) nodes.push(tools);
    // Akış eskiden yeniye okunur (kitin kuralı): en son durum en altta.
    const events = [...(request.history || [])]
      .sort((a, b) => String(a.at).localeCompare(String(b.at)))
      .map((item) => ({
        title: BOOK_STATUS[item.status]?.[0] || item.status,
        at: fmtDateTime(item.at),
        detail: item.actorLabel || '',
        tone: BOOK_STATUS[item.status]?.[1] || '',
      }));
    nodes.push(card('Geçmiş', timeline(events, { emptyText: 'Henüz hareket yok.' })));
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();

  async function deliver(request) {
    const next = await formDialog(app, {
      title: `${request.ref}: teslim`,
      description: 'Öğrenciye/veliye teslim edildi olarak işaretlenir; veliye bildirim gider.',
      value: { day: todayIst() },
      fields: [
        { key: 'day', label: 'Teslim günü', type: 'date' },
        { key: 'note', label: 'Not', maxLength: 200 },
      ],
      submitLabel: 'Teslimi kaydet',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/book-requests/${id}/deliver`, {
        deliveredAt: draft.day && draft.day !== todayIst() ? `${draft.day}T12:00:00+03:00` : null,
        note: draft.note || null }, { key }),
    });
    if (next) { app.toast('Teslim kaydedildi.', 'good'); onChange?.(next); view.reload(); }
  }
  return panel;
}

