// BBD Akademi — Kitap satışı (F-A27): kurum yöneticisi öğrencilere kitap satar.
//
// KULLANICI: "karışık olmasın". Satış üç adımdır:
//
//   1. Kitaplar   bbdstore kataloğunda ara (ad / ISBN / yayınevi), sepete ekle
//                 (en çok 20 kitap, öğrenci başına 1–5 adet; stokta olmayan eklenmez)
//   2. Öğrenciler sınıf / grup (satış anındaki üyeler, en çok 20) + tek tek öğrenci (en çok 200)
//   3. Ödeme      kurum carisine yaz · nakit · kart (POS) · veliye ödeme bağlantısı;
//                 özet ve "Satışı tamamla"
//
// Her öğrenci bir satış SATIRIDIR ve bbdstore'da ayrı kurum siparişi olur
// (Akademi arka plan işi; sabit Idempotency-Key — yeniden deneme çift sipariş
// açmaz). Para YALNIZ kurum carisindedir (A12); kantin bakiyesine dokunulmaz.
// Satış ayrıntısında satır işlemleri: siparişi yeniden dene, mağaza durumunu
// yenile, ödeme bağlantısını yeniden gönder, sipariş PDF'i, gerekçeli iptal,
// teslim (seçilenler ya da siparişi verilmiş hepsi).
//
// Öğrencinin kartında (Cari, kantin ve kitap → Kitaplar) öğrencinin aldığı
// kitaplar `students_listBookPurchases` ile görünür.

import { blockedButton, button, copyText, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, card, chipRow, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { timeline } from '../../ui-kit/flow.js';
import { documentButtons, previewAkademiPdf } from './documents.js';
import { bookSaleDoc } from './documents-logic.js';
import {
  actionButton, dateInput, empty, errorBox, fmtDate, fmtDateTime, formDialog, kv, loader, money, num,
  openDrawer, pagedList, personName, reasonDialog, runAction, searchBox, sectionHead, statusBadge,
  studentPicker, todayIst, toolbar,
} from './common.js';
import { removableChip, stack } from './homework-content.js';
import { loadTargets, targetsField } from './resources-common.js';
import { wizard } from './shared/wizard.js';

export const LINE_STATUS = {
  awaiting_payment: ['Ödeme bekleniyor', 'warn'],
  approved: ['Sipariş sırada', 'info'],
  ordering: ['Sipariş veriliyor', 'info'],
  ordered: ['Sipariş verildi', 'good'],
  order_failed: ['Sipariş düştü', 'bad'],
  delivered: ['Teslim edildi', 'good'],
  cancelled: ['İptal edildi', 'dim'],
  expired: ['Ödeme süresi doldu', 'dim'],
};
export const PAYMENT = {
  cari: 'Kurum carisine yaz',
  cash: 'Nakit tahsil edildi',
  card: 'Kartla (POS) tahsil edildi',
  pay_link: 'Veliye ödeme bağlantısı gönder',
};
const PAYMENT_SHORT = { cari: 'Cari', cash: 'Nakit', card: 'Kart (POS)', pay_link: 'Ödeme bağlantısı' };
const PAYMENT_NOTE = {
  cari: 'Her öğrencinin kurum carisine kitap kalemi yazılır; sipariş hemen verilir.',
  cash: 'Her öğrenci için makbuz numaralı nakit tahsilat yazılır, velilere bildirim gider; sipariş hemen verilir.',
  card: 'Her öğrenci için makbuz numaralı kart tahsilatı yazılır (POS slip no makbuza geçer); sipariş hemen verilir.',
  pay_link: 'Velilere kartla ödeme bağlantısı gider; ödeme gelince sipariş verilir. Süresinde ödenmeyen satır düşer.',
};
const COUNTS = [
  ['awaitingPayment', 'ödeme bekliyor', 'warn'], ['processing', 'siparişte', 'info'], ['ordered', 'sipariş verildi', 'good'],
  ['orderFailed', 'sipariş düştü', 'bad'], ['delivered', 'teslim', 'good'], ['cancelled', 'iptal', 'dim'],
];
const MAX_BOOKS = 20;
const MAX_STUDENTS = 200;
const MAX_GROUPS = 20;

function countsCell(counts) {
  const cell = h('div', 'ak-bs-counts');
  for (const [key, label, tone] of COUNTS) {
    if (!counts?.[key]) continue;
    const chip = h('span', `ak-bs-count ${tone}`, `${num(counts[key])} ${label}`);
    cell.append(chip);
  }
  if (!cell.childElementCount) cell.append(h('span', 'ak-muted', '—'));
  return cell;
}

function booksText(items, max = 2) {
  const list = (items || []).map((item) => `${item.title}${item.quantity > 1 ? ` ×${item.quantity}` : ''}`);
  return list.length > max ? `${list.slice(0, max).join(', ')} +${num(list.length - max)}` : list.join(', ');
}

// ================================================================ bölüm

export function section(app) {
  loadStyles(import.meta.url, 'books.css');
  const node = h('div', 'ak-stack ak-bs');
  const filters = { status: 'open', studentId: '', studentLabel: '' };
  const sell = actionButton(app, 'admin_createBookSale', 'Kitap sat', { variant: 'primary',
    onClick: async () => {
      const sale = await sellWizard(app);
      if (sale) { list.reload(); openSale(app, sale.id, { onChange: () => list.reload() }); }
    } });
  node.append(sectionHead('Kitap satışı', {
    subtitle: 'Öğrencilere kurumdan kitap satışı: üç adım (kitaplar → öğrenciler → ödeme), her öğrenciye ayrı '
      + 'bbdstore siparişi, teslim. Para yalnız kurum carisindedir.',
    actions: [sell],
  }));

  const chips = chipRow([
    { key: 'open', label: 'Açık (teslim / iptal edilmemiş)' },
    { key: 'all', label: 'Tümü' },
  ], filters.status, (key) => { filters.status = key || 'all'; list.reload(); });
  const studentSlot = h('span', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => {
    filters.studentId = student.id;
    filters.studentLabel = student.displayName || '';
    paintStudent();
    list.reload();
  } });
  function paintStudent() {
    studentSlot.replaceChildren();
    if (filters.studentId) {
      studentSlot.append(removableChip(filters.studentLabel || 'Seçili öğrenci', () => {
        filters.studentId = '';
        filters.studentLabel = '';
        paintStudent();
        list.reload();
      }, 'Öğrenci süzgecini kaldır'));
    }
  }
  node.append(toolbar(chips.node, h('span', 'kit-spacer'), picker.node, studentSlot));

  const list = pagedList({
    app,
    path: '/v1/admin/book-sales',
    query: () => ({ status: filters.status === 'open' ? 'open' : undefined, studentId: filters.studentId || undefined }),
    limit: 30,
    columns: [
      { key: 'ref', label: 'Satış', width: '150px', cell: (row) => stack(row.ref, fmtDateTime(row.createdAt)) },
      { key: 'books', label: 'Kitaplar', width: 'minmax(0, 2fr)', cell: (row) => booksText(row.items) || '—' },
      { key: 'students', label: 'Öğrenci', width: '80px', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'payment', label: 'Ödeme', width: '130px', cell: (row) => PAYMENT_SHORT[row.paymentMethod]
        || row.paymentMethod || '—' },
      { key: 'total', label: 'Tutar', width: '110px', align: 'num', cell: (row) => money(row.totalMinor) },
      { key: 'state', label: 'Durum', width: 'minmax(0, 1.8fr)', cell: (row) => countsCell(row.counts) },
      { key: 'by', label: 'Satan', width: 'minmax(0, 1fr)', cell: (row) => personName(row.createdBy) },
    ],
    onRow: (row) => openSale(app, row.id, { onChange: (next) => (next?.id
      ? list.replace((sale) => sale.id === next.id, summaryOf(next)) : list.reload()) }),
    empty: { title: 'Bu süzgece uyan satış yok', text: 'Sınıfa ya da seçtiğiniz öğrencilere tek seferde kitap '
      + 'satabilirsiniz.', actions: [actionButton(app, 'admin_createBookSale', 'Kitap sat', { variant: 'primary',
      onClick: () => sell.click() })] },
  });
  node.append(list.node);

  /** Ayrıntı yükü → liste satırı (satırlar listede taşınmaz). */
  function summaryOf(sale) {
    const { lines, ...rest } = sale;   // eslint-disable-line no-unused-vars
    return rest;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.studentId) {
        filters.studentId = payload.studentId;
        filters.studentLabel = payload.studentName || '';
        filters.status = 'all';
        chips.set('all');
        paintStudent();
        if (started) list.reload();
      }
      if (!started) { started = true; list.reload(); }
      if (payload?.saleId) openSale(app, payload.saleId, { onChange: () => list.reload() });
    },
    destroy() { picker.destroy(); list.destroy(); },
  };
}

// ============================================================ satış ayrıntısı

export function openSale(app, saleId, { onChange } = {}) {
  loadStyles(import.meta.url, 'books.css');
  const panel = openDrawer(app, { title: 'Kitap satışı' });
  panel.node.classList.add('ak-bs-drawer');
  let table = null;
  let current = null;
  const view = loader(() => app.api.get(`/v1/admin/book-sales/${saleId}`), (sale) => {
    current = sale;
    panel.setTitle(`${sale.ref} — ${num(sale.studentCount)} öğrenci`);
    const lines = sale.lines || [];
    const ordered = lines.filter((line) => line.status === 'ordered');
    const tools = h('div', 'ak-inline');
    tools.append(
      ordered.length
        ? actionButton(app, 'admin_deliverBookSale', `Siparişi verilenleri teslim et (${num(ordered.length)})`,
          { variant: 'primary', onClick: () => deliver([]) })
        : blockedButton('Siparişi verilenleri teslim et', 'Siparişi verilmiş (teslim bekleyen) öğrenci yok.',
          { variant: 'primary' }),
      actionButton(app, 'admin_deliverBookSale', 'Seçilenleri teslim et', { onClick: () => {
        const ids = (table?.selection() || []).filter((id) => ordered.some((line) => line.id === id));
        if (!ids.length) { app.toast('Önce listeden “Sipariş verildi” durumundaki öğrencileri seçin.', 'warn'); return; }
        deliver(ids);
      } }),
      h('span', 'kit-spacer'),
      // Kurum logolu A4 satış belgesi: kitaplar + öğrenci satırları + teslim alan imza sütunu.
      documentButtons(app, { title: 'Kitap satış belgesi', label: 'Satış belgesi',
        build: async () => bookSaleDoc(await app.api.get(`/v1/admin/book-sales/${saleId}`)) }),
    );
    const failed = lines.filter((line) => line.status === 'order_failed').length;
    const nodes = [tools];
    if (failed) {
      nodes.push(alertBox(`${num(failed)} öğrencinin bbdstore siparişi oluşturulamadı. Satırı açıp “Siparişi `
        + 'yeniden dene” ile tekrar deneyin (çift sipariş açılmaz).', 'bad'));
    }
    nodes.push(kpiRow([
      { label: 'Öğrenci', value: num(sale.studentCount) },
      { label: 'Tutar', value: money(sale.totalMinor) },
      { label: 'Ödeme bekliyor', value: num(sale.counts?.awaitingPayment), tone: sale.counts?.awaitingPayment ? 'warn' : '' },
      { label: 'Sipariş verildi', value: num(sale.counts?.ordered) },
      { label: 'Teslim', value: num(sale.counts?.delivered), tone: sale.counts?.delivered ? 'good' : '' },
    ]));
    nodes.push(card('', kv([
      ['Kitaplar', (sale.items || []).map((book) => `${book.title} — ${num(book.quantity)} × `
        + `${money(book.unitPriceMinor)}`).join(' · ') || '—'],
      ['Ödeme', PAYMENT[sale.paymentMethod] || sale.paymentMethod],
      ['Not', sale.note || '—'],
      ['Satan', `${personName(sale.createdBy)} · ${fmtDateTime(sale.createdAt)}`],
    ])));
    table = dataTable({
      columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.8fr)', cell: (line) => stack(line.student?.displayName,
          [line.student?.classGroup?.name, line.ref].filter(Boolean).join(' · ')) },
        { key: 'status', label: 'Durum', width: '150px', cell: (line) => statusBadge(LINE_STATUS, line.status) },
        { key: 'total', label: 'Tutar', width: '100px', align: 'num', cell: (line) => money(line.totalMinor) },
        { key: 'pay', label: 'Ödeme', width: 'minmax(0, 1.2fr)', cell: (line) => paymentText(line) },
        { key: 'order', label: 'Sipariş', width: '120px', cell: (line) => (line.order?.incrementId
          ? `#${line.order.incrementId}` : '—') },
      ],
      rows: lines,
      selectable: true,
      onRow: (line) => openLine(app, saleId, line.id, { onChange: () => { view.reload(); onChange?.(); } }),
      empty: empty('Satırı yok'),
    });
    nodes.push(card('Öğrenciler', table.node, 'Satıra tıklayınca sipariş, ödeme ve geçmiş açılır.'));
    return nodes;
  }, { rows: 6, columns: 5 });
  panel.body.append(view.node);
  view.reload();

  async function deliver(lineIds) {
    const result = await formDialog(app, {
      title: lineIds.length ? `${num(lineIds.length)} öğrenciye teslim` : 'Siparişi verilenlerin hepsine teslim',
      description: 'Satırlar “Teslim edildi” olur; öğrenciye bildirim gider.',
      fields: [{ key: 'note', label: 'Teslim notu (isteğe bağlı)', maxLength: 200 }],
      submitLabel: 'Teslimi kaydet',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/book-sales/${saleId}/deliver`,
        { lineIds: lineIds.length ? lineIds : undefined, note: String(draft.note || '').trim() || null }, { key }),
    });
    if (!result || result === true) return;
    app.toast('Teslim kaydedildi.', 'good');
    view.reload();
    onChange?.(result);
  }

  return { panel, sale: () => current };
}

function paymentText(line) {
  if (line.payment) {
    return `${line.payment.receiptNo}${line.payment.status === 'voided' ? ' (iptal)' : ''}`;
  }
  if (line.payLink) return line.payLink.url ? `Bağlantı · ${fmtDateTime(line.payLink.expiresAt)}'e dek` : 'Bağlantı süresi doldu';
  if (line.chargeId) return 'Cari kalemi';
  return '—';
}

// ============================================================== satır

function openLine(app, saleId, lineId, { onChange } = {}) {
  const path = `/v1/admin/book-sales/${saleId}/lines/${lineId}`;
  const panel = openDrawer(app, { title: 'Satış satırı' });
  const view = loader(() => app.api.get(path), (line) => {
    panel.setTitle(`${line.student?.displayName || 'Öğrenci'} — ${line.ref}`);
    const nodes = [];
    const tools = h('div', 'ak-inline');
    const after = (next, text) => { if (next) { app.toast(text, 'good'); view.reload(); onChange?.(); } };
    if (['order_failed', 'approved'].includes(line.status)) {
      tools.append(actionButton(app, 'admin_retryBookSaleOrder', 'Siparişi yeniden dene', { variant: 'primary',
        onClick: async () => after(await runAction(app, () => app.api.post(`${path}/retry-order`)),
          'Sipariş yeniden denendi.') }));
    }
    if (line.status === 'awaiting_payment') {
      tools.append(actionButton(app, 'admin_sendBookSalePayLink', 'Ödeme bağlantısını yeniden gönder', {
        variant: 'primary', onClick: async () => after(await runAction(app, () => app.api.post(`${path}/pay-link`)),
          'Velilere yeni ödeme bağlantısı gönderildi.') }));
    }
    if (line.status === 'ordered') {
      tools.append(actionButton(app, 'admin_deliverBookSale', 'Teslim edildi', { variant: 'primary',
        onClick: async () => after(await runAction(app, () => app.api.post(`/v1/admin/book-sales/${saleId}/deliver`,
          { lineIds: [lineId] })), 'Teslim kaydedildi.') }));
    }
    if (line.order?.orderId) {
      tools.append(
        actionButton(app, 'admin_refreshBookSaleOrder', 'Mağaza durumunu yenile', { onClick: async () => after(
          await runAction(app, () => app.api.post(`${path}/refresh-order`)), 'Mağaza durumu okundu.') }),
        actionButton(app, 'admin_getBookSaleOrderPdf', 'Sipariş / fatura PDF', { onClick: (event) => {
          const opener = event?.currentTarget || null;
          return runAction(app, async () => {
            // bbdstore'un kendi belgesi; aynı önizle / yazdır penceresinde açılır.
            const signed = await app.api.get(`${path}/order-pdf`);
            await previewAkademiPdf(app, { kind: 'book_sale_order', url: signed.url, name: line.ref, opener });
          });
        } }),
      );
    }
    if (!['cancelled', 'expired'].includes(line.status)) {
      tools.append(h('span', 'kit-spacer'), actionButton(app, 'admin_cancelBookSaleLine', 'İptal / iade',
        { variant: 'ghost', onClick: () => cancel(line) }));
    }
    if (tools.childElementCount) nodes.push(tools);
    if (line.status === 'order_failed') {
      nodes.push(alertBox(`bbdstore siparişi oluşturulamadı${line.order?.failureCode ? ` (${line.order.failureCode})` : ''}. `
        + 'Stok ya da mağaza erişimi düzelince yeniden deneyin.', 'bad'));
    }
    if (line.payLink?.url) {
      const linkRow = h('div', 'ak-inline');
      const field = h('input', 'kit-input ak-bs-link');
      field.readOnly = true;
      field.value = line.payLink.url;
      field.setAttribute('aria-label', 'Ödeme bağlantısı');
      linkRow.append(field, button('Kopyala', { onClick: async () => {
        await copyText(line.payLink.url);
        app.toast('Bağlantı kopyalandı.', 'good');
      } }));
      nodes.push(card('Veliye giden ödeme bağlantısı', linkRow,
        `${fmtDateTime(line.payLink.expiresAt)} tarihine dek geçerli; süre uzamaz.`));
    }
    nodes.push(card('', kv([
      ['Öğrenci', [line.student?.displayName, line.student?.classGroup?.name].filter(Boolean).join(' · ')],
      ['Kitaplar', (line.items || []).map((book) => `${book.title} × ${num(book.quantity)}`).join(' · ') || '—'],
      ['Tutar', money(line.totalMinor)],
      ['Durum', statusBadge(LINE_STATUS, line.status)],
      ['Ödeme şekli', PAYMENT[line.paymentMethod] || line.paymentMethod || '—'],
      ['Tahsilat', line.payment ? `${line.payment.receiptNo} · ${money(line.payment.amountMinor)}`
        + `${line.payment.status === 'voided' ? ' · iptal edildi' : ''}` : (line.chargeId ? 'Kurum carisine kalem yazıldı' : '—')],
      ['Ödeme son günü', line.payDeadline ? fmtDateTime(line.payDeadline) : '—'],
      ['bbdstore siparişi', line.order ? `#${line.order.incrementId} · ${line.order.status}`
        + `${line.order.checkedAt ? ` · son okuma ${fmtDateTime(line.order.checkedAt)}` : ''}` : '—'],
      ['Teslim', fmtDateTime(line.deliveredAt)],
      ['İptal', line.cancelledAt ? `${fmtDateTime(line.cancelledAt)} · ${line.cancelReason || ''}` : '—'],
    ])));
    const events = [...(line.history || [])].sort((a, b) => String(a.at).localeCompare(String(b.at)))
      .map((event) => ({ title: LINE_STATUS[event.status]?.[0] || event.status, at: fmtDateTime(event.at),
        detail: [event.actorLabel, event.note].filter(Boolean).join(' · '), tone: LINE_STATUS[event.status]?.[1] || '' }));
    nodes.push(card('Geçmiş', timeline(events, { emptyText: 'Henüz hareket yok.' })));
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();

  async function cancel(line) {
    const what = {
      awaiting_payment: 'Açık ödeme bağlantısı kapanır (sonradan gelen ödeme öğrencinin carisine alacak yazılır).',
      approved: 'Kurum cari kalemi iptal edilir; tahsil edilmiş tutar öğrencinin carisine ALACAK yazılır.',
      order_failed: 'Kurum cari kalemi iptal edilir; tahsil edilmiş tutar öğrencinin carisine ALACAK yazılır.',
    }[line.status] || 'Sipariş bbdstore\'da iptal edilmediyse işlem reddedilir: önce mağaza panelinden siparişi '
      + 'iptal edin.';
    const reason = await reasonDialog(app, {
      title: `${line.student?.displayName || 'Öğrenci'} — iptal / iade`,
      description: `${what} Nakit / kart iadesi gerekiyorsa tahsilat Kurum finansı'ndan ayrıca iptal edilir.`,
      confirmLabel: 'Satırı iptal et',
      presets: ['Veli vazgeçti', 'Yanlış kitap', 'Öğrenci ayrıldı'],
    });
    if (!reason) return;
    const next = await runAction(app, () => app.api.post(`${path}/cancel`, { reason }), { success: 'Satır iptal edildi.' });
    if (next) { view.reload(); onChange?.(); }
  }
  return panel;
}

// ============================================================ satış sihirbazı

/**
 * Kitap sat: kitaplar → öğrenciler → ödeme.
 * @returns {Promise<object|null>} oluşan satış (`BookSale`) ya da null
 */
export async function sellWizard(app, { students = [] } = {}) {
  loadStyles(import.meta.url, 'books.css');
  const [classes, groups] = await loadTargets(app);
  const cart = new Map();     // productId → {book, quantity}
  let target = { classGroupIds: [], studentGroupIds: [], studentIds: students.map((item) => item.id) };
  const pay = { method: '', dueDate: '', reference: '', note: '' };
  const perStudent = () => [...cart.values()].reduce((sum, row) => sum + row.book.priceMinor * row.quantity, 0);
  const groupIds = () => [...target.classGroupIds, ...target.studentGroupIds];
  const methodOff = (method) => (method === 'pay_link' ? !app.enabled('finance') || !app.enabled('finance.card')
    : !app.enabled('finance'));

  // ======================================================= 1. kitaplar
  const stepBooks = (ctl) => {
    const node = h('div', 'ak-stack');
    const results = h('div', 'ak-bs-results');
    const basket = h('div', 'ak-bs-cart');
    basket.setAttribute('aria-live', 'polite');
    let token = 0;
    const find = async (text) => {
      const mine = ++token;
      if (text.length < 2) { results.replaceChildren(h('div', 'ak-muted', 'Aramak için en az 2 harf ya da ISBN yazın.')); return; }
      results.replaceChildren(h('div', 'ak-muted', 'Aranıyor…'));
      const digits = text.replace(/[\s-]/g, '');
      const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
      try {
        const data = await app.api.get('/v1/catalog/books', { ...query, page: 1, limit: 12 });
        if (mine !== token) return;
        results.replaceChildren();
        const items = data?.items || [];
        if (!items.length) results.append(h('div', 'ak-muted', 'Eşleşen kitap yok.'));
        for (const book of items) {
          const row = h('div', 'ak-bs-book');
          const info = stack(book.title, [book.author, book.publisher, book.isbn ? `ISBN ${book.isbn}` : '']
            .filter(Boolean).join(' · '));
          const price = h('span', 'ak-num', money(book.priceMinor));
          let add;
          if (book.inStock === false) {
            add = h('span', 'ak-muted', 'Stokta yok');
          } else {
            add = button(cart.has(book.productId) ? 'Sepette' : 'Ekle', { onClick: () => {
              if (cart.has(book.productId)) return;
              if (cart.size >= MAX_BOOKS) { app.toast(`En çok ${MAX_BOOKS} kitap eklenebilir.`, 'warn'); return; }
              cart.set(book.productId, { book, quantity: 1 });
              add.textContent = 'Sepette';
              add.setAttribute('aria-label', `${book.title} — sepette`);
              paintCart();
              ctl.clearError();
            } });
            add.setAttribute('aria-label', `${book.title} — sepete ekle`);
          }
          row.append(info, price, add);
          results.append(row);
        }
        if (data?.hasMore) results.append(h('div', 'ak-muted', 'Daha fazla sonuç var; aramayı daraltın.'));
      } catch (failure) {
        if (mine !== token) return;
        results.replaceChildren(errorBox(failure, () => find(text)));
      }
    };
    const search = searchBox({ placeholder: 'Kitap ara: ad, yazar, yayınevi ya da ISBN', label: 'Kitap ara',
      onSearch: (text) => find(text) });
    const paintCart = () => {
      basket.replaceChildren();
      if (!cart.size) {
        basket.append(h('div', 'ak-muted', 'Sepet boş.'));
        ctl.note('');
        return;
      }
      for (const [productId, row] of cart) {
        const line = h('div', 'ak-bs-cart-row');
        const qty = h('select', 'kit-select ak-bs-qty');
        qty.setAttribute('aria-label', `${row.book.title} — öğrenci başına adet`);
        for (let count = 1; count <= 5; count += 1) {
          const option = h('option', '', `${count} adet`);
          option.value = String(count);
          qty.append(option);
        }
        qty.value = String(row.quantity);
        qty.addEventListener('change', () => { row.quantity = Number(qty.value); paintCart(); });
        const remove = button('Çıkar', { variant: 'ghost', onClick: () => { cart.delete(productId); paintCart(); } });
        remove.setAttribute('aria-label', `${row.book.title} — sepetten çıkar`);
        line.append(stack(row.book.title, money(row.book.priceMinor)), qty,
          h('span', 'ak-num', money(row.book.priceMinor * row.quantity)), remove);
        basket.append(line);
      }
      ctl.note(`Öğrenci başına ${money(perStudent())} · ${num(cart.size)} kitap`);
    };
    node.append(search.node, results, h('h5', 'ak-bs-cart-title', 'Sepet (öğrenci başına)'), basket);
    results.append(h('div', 'ak-muted', 'bbdstore kataloğunda arayın; fiyat satış anında kilitlenir.'));
    paintCart();
    return { node, enter: paintCart, destroy: () => search.destroy() };
  };

  // ======================================================= 2. öğrenciler
  const stepStudents = (ctl) => {
    const field = targetsField(app, { classes, groups, value: { students }, onChange: (next) => {
      target = next;
      ctl.clearError();
      paint();
    } });
    const paint = () => {
      const parts = [];
      if (groupIds().length) parts.push(`${num(groupIds().length)} sınıf/grup (satış anındaki üyeler)`);
      if (target.studentIds.length) parts.push(`${num(target.studentIds.length)} öğrenci`);
      ctl.note(parts.join(' + '));
    };
    const node = h('div', 'ak-stack');
    node.append(h('div', 'ak-muted', 'Her öğrenciye ayrı satış satırı ve ayrı bbdstore siparişi açılır; aynı öğrenci '
      + 'iki kez sayılmaz.'), field.node);
    return { node, enter: paint, destroy: () => field.destroy() };
  };

  // ======================================================= 3. ödeme
  const stepPay = (ctl) => {
    const node = h('div', 'ak-stack');
    const radios = h('div', 'ak-bt-modes ak-bs-methods');
    radios.setAttribute('role', 'radiogroup');
    radios.setAttribute('aria-label', 'Ödeme şekli');
    const note = h('div', 'ak-muted');
    const extras = { cari: h('div', 'ak-inline'), card: h('div', 'ak-inline') };
    const due = dateInput('', (iso) => { pay.dueDate = iso || ''; }, 'Cari kaleminin vadesi');
    extras.cari.append(h('span', 'ak-muted', 'Vade (boşsa kurum ayarındaki gün):'), due.node);
    const reference = h('input', 'kit-input');
    reference.maxLength = 80;
    reference.placeholder = 'POS slip / provizyon no';
    reference.setAttribute('aria-label', 'POS slip ya da provizyon numarası');
    reference.addEventListener('input', () => { pay.reference = reference.value; });
    extras.card.append(h('span', 'ak-muted', 'Makbuza yazılır:'), reference);
    for (const [key, label] of Object.entries(PAYMENT)) {
      const wrap = h('label', 'ak-bt-mode');
      const input = h('input');
      input.type = 'radio';
      input.name = 'ak-bs-method';
      input.value = key;
      input.disabled = methodOff(key);
      input.addEventListener('change', () => { if (input.checked) { pay.method = key; ctl.clearError(); paint(); } });
      wrap.append(input, h('span', '', methodOff(key) ? `${label} (kurumda kapalı)` : label));
      radios.append(wrap);
    }
    const memo = h('textarea', 'kit-textarea');
    memo.maxLength = 300;
    memo.placeholder = 'Kurum içi not (bbdstore sipariş notuna da eklenir)';
    memo.setAttribute('aria-label', 'Not');
    memo.addEventListener('input', () => { pay.note = memo.value; });
    const summary = h('div', 'ak-bs-summary');
    node.append(radios, note, extras.cari, extras.card, memo, summary,
      h('div', 'ak-muted', 'Para yalnız kurum carisindedir; kantin bakiyesine dokunulmaz.'));
    function paint() {
      note.textContent = PAYMENT_NOTE[pay.method] || 'Ödeme şeklini seçin.';
      extras.cari.hidden = pay.method !== 'cari';
      extras.card.hidden = pay.method !== 'card';
      const known = target.studentIds.length;
      const rows = [
        ['Kitaplar', [...cart.values()].map((row) => `${row.book.title} × ${num(row.quantity)}`).join(' · ') || '—'],
        ['Öğrenci başına', money(perStudent())],
        ['Öğrenciler', [groupIds().length ? `${num(groupIds().length)} sınıf/grup` : '',
          known ? `${num(known)} öğrenci` : ''].filter(Boolean).join(' + ') || '—'],
      ];
      if (!groupIds().length && known) rows.push(['Toplam', money(perStudent() * known)]);
      else rows.push(['Toplam', `öğrenci başına ${money(perStudent())} × satış anındaki öğrenci sayısı`]);
      rows.push(['Ödeme', PAYMENT[pay.method] || '—']);
      summary.replaceChildren(card('Özet', kv(rows)));
    }
    paint();
    return { node, enter: paint, destroy: () => due.destroy() };
  };

  return wizard(app, {
    title: 'Kitap sat',
    finishLabel: 'Satışı tamamla',
    steps: [
      { key: 'books', label: 'Kitaplar', render: stepBooks,
        validate: () => (cart.size ? null : 'Sepete en az bir kitap ekleyin.') },
      { key: 'students', label: 'Öğrenciler', render: stepStudents, validate: () => {
        if (!groupIds().length && !target.studentIds.length) return 'En az bir sınıf, grup ya da öğrenci seçin.';
        if (groupIds().length > MAX_GROUPS) return `En çok ${MAX_GROUPS} sınıf/grup seçilebilir.`;
        if (target.studentIds.length > MAX_STUDENTS) return `En çok ${MAX_STUDENTS} öğrenci seçilebilir.`;
        return null;
      } },
      { key: 'pay', label: 'Ödeme', render: stepPay, validate: () => {
        if (!pay.method) return 'Ödeme şeklini seçin.';
        if (methodOff(pay.method)) return 'Bu ödeme şekli kurumda kapalı.';
        if (pay.method === 'cari' && pay.dueDate && pay.dueDate < todayIst()) return 'Vade geçmişte olamaz.';
        return null;
      } },
    ],
    onFinish: async ({ key }) => {
      const sale = await app.api.post('/v1/admin/book-sales', {
        items: [...cart.values()].map((row) => ({ productId: row.book.productId, quantity: row.quantity })),
        studentIds: target.studentIds,
        groupIds: groupIds(),
        paymentMethod: pay.method,
        dueDate: pay.method === 'cari' ? pay.dueDate || null : null,
        reference: pay.method === 'card' ? pay.reference.trim() || null : null,
        note: pay.note.trim() || null,
      }, { key });
      app.toast(`Satış ${sale.ref}: ${num(sale.studentCount)} öğrenci · ${money(sale.totalMinor)}. Siparişler bbdstore'a `
        + 'gönderiliyor.', 'good');
      return sale;
    },
  });
}

// ===================================================== öğrenci kartı: Kitaplar

/** Öğrenci kartı → Cari, kantin ve kitap → Kitaplar (`students_listBookPurchases`). */
export function studentBooksPart(ctx) {
  const { app, id } = ctx;
  const node = h('div', 'ak-stack');
  const list = pagedList({
    app,
    path: `/v1/students/${id}/book-purchases`,
    limit: 20,
    columns: [
      { key: 'books', label: 'Kitaplar', width: 'minmax(0, 2fr)', cell: (row) => stack(booksText(row.items, 3),
        `${row.saleRef} · ${fmtDate(String(row.purchasedAt || '').slice(0, 10))}`) },
      { key: 'total', label: 'Tutar', width: '100px', align: 'num', cell: (row) => money(row.totalMinor) },
      { key: 'payment', label: 'Ödeme', width: '130px', cell: (row) => PAYMENT_SHORT[row.paymentMethod] || '—' },
      { key: 'status', label: 'Durum', width: '150px', cell: (row) => statusBadge(LINE_STATUS, row.status) },
      { key: 'order', label: 'Sipariş', width: '110px', cell: (row) => (row.order?.incrementId
        ? `#${row.order.incrementId}` : '—') },
    ],
    onRow: (row) => showPurchase(row.id),
    empty: { title: 'Kurumdan alınmış kitap yok' },
  });
  ctx.dispose?.(() => list.destroy());
  const sell = actionButton(app, 'admin_createBookSale', 'Bu öğrenciye kitap sat', { variant: 'ghost',
    onClick: async () => {
      const sale = await sellWizard(app, { students: [{ id, displayName: ctx.student?.student?.displayName
        || 'Öğrenci' }] });
      if (sale) list.reload();
    } });
  node.append(toolbar(h('span', 'ak-muted', 'Kitap talepleri ayrıdır (Finans ve Kantin → Kitap talepleri).'),
    h('span', 'kit-spacer'), sell), list.node);
  list.reload();

  async function showPurchase(purchaseId) {
    const purchase = await runAction(app, () => app.api.get(`/v1/students/${id}/book-purchases/${purchaseId}`));
    if (!purchase) return;
    const panel = openDrawer(app, { title: `${purchase.ref} — kitap alımı` });
    panel.body.append(card('', kv([
      ['Kitaplar', (purchase.items || []).map((book) => `${book.title} × ${num(book.quantity)}`).join(' · ') || '—'],
      ['Tutar', money(purchase.totalMinor)],
      ['Durum', statusBadge(LINE_STATUS, purchase.status)],
      ['Ödeme', PAYMENT[purchase.paymentMethod] || '—'],
      ['Ödeme son günü', purchase.payDeadline ? fmtDateTime(purchase.payDeadline) : '—'],
      ['Sipariş', purchase.order ? `#${purchase.order.incrementId} · ${purchase.order.status}` : '—'],
      ['Alım', fmtDateTime(purchase.purchasedAt)],
      ['Teslim', fmtDateTime(purchase.deliveredAt)],
      ['İptal', fmtDateTime(purchase.cancelledAt)],
    ])), h('div', 'ak-muted', `Satış: ${purchase.saleRef}. Satır işlemleri Finans ve Kantin → Kitap satışı'ndadır.`));
  }
  return { node };
}

