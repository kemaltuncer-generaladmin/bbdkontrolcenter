// BBD Akademi — "Deneme Kulübü" ekranı (Finans ve Kantin → Deneme Kulübü; Akademi README §14 madde 141, iş 81-km-kulup).
//
// KULLANICI (28.10.2026): "deneme kulübü diye sınıf açacaz biz … sanki kurum öğrencisiymiş gibi olacak" +
// "nakit kurumda pos bbdstore üzerinden ödeme havale/EFT kurum hesabına … tek kullanımlık form göndeririz link ile
// sms ile kullanıcı doldurunca aboneliği otomatik başlar".
//
// Sekmeler:
//   · Davetler (`admin_listClubInvites`): durum çipleri, kaynak, arama; satırda öğrenci / sözleşme bağlantısı;
//     "Yeniden gönder" (yeni bağlantı), "İptal", başarısızda "Yeniden dene" ve "KM'den eşleştir".
//     "Yeni kulüp kaydı" (kurumda: ad, telefon, Nakit / POS / Havale-EFT, tutar, ödendi → SMS formu).
//   · Kulüp ayarları (`/v1/admin/settings` → `club.*`): sınıf adı, paket, sınav türü, deneme sayısı, sezon, ücret,
//     davet geçerliliği, SMS metni, BBDSTORE'dan otomatik davet, ön bilgilendirme künyesi.
// Üstte BBDSTORE izleyicisinin şeridi (`GET /api/bbd_akademi/km/club/store-watch`): durum, davet açılamayan
// siparişler (sipariş no + neden), "Şimdi tara".
//
// TEK KAYNAK AKADEMİ (KM kopya tutmaz). KM'de SSE yok → ekran görünürken 20 sn'de bir sessiz yoklama (yalnız iz
// değişince yeniden çizilir).

import { button, copyText, h, loadStyles, pollLoop } from '../../../ui-kit/kit.js';
import { alertBox, badge, tabBar } from '../../../ui-kit/layout.js';
import {
  actionButton, confirmAction, empty, fmtDate, fmtDateTime, formDialog, money, pagedList, reasonDialog, runAction,
  searchBox, sectionHead, selectBox, statusBadge, studentPicker, toolbar,
} from '../common.js';
import {
  BRANCH_METHODS, EXAM_TYPE, INVITE_SOURCE, INVITE_STATUS, INVITE_STEP, PAYMENT_METHOD, STATUS_CHIPS,
  branchInviteBody, branchInviteErrors, clubSettings, inviteActions, inviteFingerprint, inviteQuery, settingsErrors,
  settingsPatch, watchTone,
} from './club-logic.js';

const LIST = '/v1/admin/club/invites';
const SETTINGS = '/v1/admin/settings';
const POLL_MS = 20_000;

const TABS = [
  { key: 'invites', label: 'Davetler', read: 'admin_listClubInvites' },
  { key: 'settings', label: 'Kulüp ayarları', read: 'admin_getSettings' },
];

/** Öğrenci kartı → Sözleşmeler sekmesi (80-km-sozlesme). */
function openStudent(app, studentId, contractId = null) {
  if (!studentId) return;
  app.go('students', { studentId, tab: contractId ? 'contracts' : undefined, contractId });
}

export function section(app) {
  loadStyles(import.meta.url, 'club-screen.css');
  const node = h('div', 'ak-stack ak-club');
  const visible = TABS.filter((tab) => app.can(tab.read));
  let invitesTab = null;
  const create = actionButton(app, 'admin_createClubInvite', 'Yeni kulüp kaydı', { variant: 'primary',
    title: 'Kurumda ödeme alındı: veliye tek kullanımlık kayıt formunu SMS ile gönder.',
    onClick: () => newBranchInvite(app, { opener: create, done: () => invitesTab?.reload() }) });
  node.append(sectionHead('Deneme Kulübü', {
    subtitle: 'Kulüp üyesi KM\'de "Deneme Kulübü" sınıfının normal öğrencisidir: denemeler, sonuçlar ve karne kurum '
      + 'öğrencisi gibi. Ödeme alınınca veliye SMS ile tek kullanımlık form gider; form dolunca öğrenci, kulüp '
      + 'sözleşmesi ve tahsilat kendiliğinden açılır.',
    actions: [create],
  }));
  if (!visible.length) {
    node.append(empty('Deneme Kulübü\'nü görme yetkiniz yok', 'Kulüp izni gerekir (yöneticinizden isteyin).'));
    return { node, shown() {}, destroy() {} };
  }

  const watch = app.has('club') ? watchBand(app, () => invitesTab?.reload()) : null;
  if (watch) node.append(watch.node);

  const bodies = {};
  let active = null;
  const builders = {
    invites: () => { invitesTab = invitesList(app); return invitesTab; },
    settings: () => settingsTab(app),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => showTab(key));
  const slot = h('div', 'ak-club-slot');
  node.append(tabs.node, slot);

  function showTab(key) {
    active = key;
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload?.();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== active;
  }

  const poll = pollLoop({ every: POLL_MS, run: async () => {
    if (!node.isConnected || node.offsetParent === null || app.root.querySelector('.kit-overlay, .kit-drawer')) return;
    await Promise.all([bodies[active]?.poll?.(), watch?.reload()]);
  } });

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && visible.some((tab) => tab.key === payload.tab) ? payload.tab : null;
      if (!started) {
        started = true;
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
        watch?.reload();
        return;
      }
      if (wanted && wanted !== active) {
        tabs.select(wanted, false);
        showTab(wanted);
      } else {
        bodies[active]?.poll?.();
        watch?.reload();
      }
    },
    destroy() {
      poll.stop();
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ------------------------------------------------------------------ BBDSTORE izleyici şeridi

function watchBand(app, onInvited) {
  const node = h('div', 'ak-club-watch');
  let last = null;

  async function reload() {
    try {
      last = await app.api.km('/club/store-watch');
    } catch (failure) {
      last = { state: 'error', message: `İzleyici durumu okunamadı: ${failure?.message || failure}`, counts: {},
        problems: [] };
    }
    paint();
  }

  async function runNow(control) {
    const reply = await runAction(app, () => app.api.km('/club/store-watch/run', { method: 'POST', body: {} }),
      { button: control });
    if (!reply) return;
    last = reply.status;
    paint();
    const invited = reply.result?.invited || 0;
    app.toast(invited ? `${invited} BBDSTORE siparişi için davet açıldı.` : 'BBDSTORE tarandı; yeni sipariş yok.',
      invited ? 'good' : 'info');
    if (invited) onInvited?.();
  }

  function paint() {
    node.replaceChildren();
    if (!last) return;
    const tone = watchTone(last);
    const head = h('div', `ak-club-watch-head tone-${tone}`);
    const text = h('div', 'ak-club-watch-text');
    text.append(h('b', '', 'BBDSTORE siparişleri'), h('span', '', last.message || ''));
    const meta = [];
    if (last.counts?.invited) meta.push(`${last.counts.invited} sipariş davete çevrildi`);
    if (last.lastRunAt) meta.push(`son tarama ${fmtDateTime(last.lastRunAt)}`);
    if (meta.length) text.append(h('small', '', meta.join(' · ')));
    const scan = button('Şimdi tara', { variant: 'ghost',
      title: 'BBDSTORE\'daki ödenmiş kulüp siparişlerini hemen kontrol et (normalde 2 dk\'da bir).',
      onClick: () => runNow(scan) });
    head.append(text, h('span', 'kit-spacer'), scan);
    node.append(head);
    const problems = last.problems || [];
    if (problems.length) {
      const list = h('ul', 'ak-club-watch-problems');
      for (const item of problems) {
        const row = h('li');
        row.append(h('b', '', `Sipariş ${item.orderNumber}`), h('span', '', money(item.amountMinor || 0)),
          h('span', 'ak-muted', item.error || 'Davet açılamadı.'));
        list.append(row);
      }
      node.append(alertBox('Davet açılamayan BBDSTORE siparişleri — telefon geçersizse "Yeni kulüp kaydı" ile elle '
        + 'açın (kaynak kurum, ödeme BBDSTORE değil; tutar 0 ve "ödendi" kapalı bırakılabilir); diğerleri 2 dk\'da '
        + 'bir kendiliğinden yeniden denenir.', 'warn'), list);
    }
  }

  return { node, reload };
}

// ------------------------------------------------------------------ davetler

function inviteCell(row) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', row.name || 'Adsız davet'), h('small', '', row.phoneMasked || ''));
  return node;
}

function sourceCell(row) {
  const node = h('div', 'ak-cell-stack');
  node.append(badge(INVITE_SOURCE[row.source] || row.source || '—', row.source === 'store' ? 'info' : 'dim'));
  if (row.storeOrderRef) node.append(h('small', '', `Sipariş ${row.storeOrderRef}`));
  return node;
}

function amountCell(row) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('span', '', money(row.amountMinor ?? 0)));
  node.append(h('small', '', [PAYMENT_METHOD[row.paymentMethod] || row.paymentMethod,
    row.paid ? 'ödendi' : 'ödenmedi'].filter(Boolean).join(' · ')));
  return node;
}

function statusCell(row) {
  const node = h('div', 'ak-cell-stack');
  node.append(statusBadge(INVITE_STATUS, row.status));
  if (row.status === 'failed' && row.failureReason) node.append(h('small', 'ak-club-reason', row.failureReason));
  else if (row.status === 'processing' && row.step) node.append(h('small', '', INVITE_STEP[row.step] || row.step));
  else if (row.status === 'cancelled' && row.cancelReason) node.append(h('small', '', row.cancelReason));
  else if (['sent', 'opened'].includes(row.status) && row.expiresAt) {
    node.append(h('small', '', `Son gün ${fmtDate(row.expiresAt)}`));
  }
  return node;
}

function studentCell(app, row) {
  const node = h('div', 'ak-cell-stack');
  if (!row.studentId) {
    node.append(h('span', 'ak-muted', '—'));
    return node;
  }
  const open = h('button', 'ak-link', row.studentName || 'Öğrenci kartı');
  open.type = 'button';
  open.title = 'Öğrenci kartını aç';
  open.addEventListener('click', (event) => { event.stopPropagation(); openStudent(app, row.studentId); });
  node.append(open);
  if (row.contractId) {
    const contract = h('button', 'ak-link', row.contractNumber ? `Sözleşme ${row.contractNumber}` : 'Kulüp sözleşmesi');
    contract.type = 'button';
    contract.title = 'Öğrencinin Sözleşmeler sekmesinde aç';
    contract.addEventListener('click', (event) => {
      event.stopPropagation();
      openStudent(app, row.studentId, row.contractId);
    });
    node.append(contract);
  }
  if (row.matchedExisting) node.append(h('small', '', 'Mevcut öğrenciyle eşleşti'));
  return node;
}

/** Form bağlantısını göster + kopyala (yalnız oluşturma / yeniden gönderme yanıtında gelir). */
async function showFormLink(app, invite, { sent }) {
  if (!invite?.formUrl) return;
  await formDialog(app, {
    title: sent ? 'Kayıt formu SMS ile gönderildi' : 'Kayıt formu hazır',
    description: sent
      ? 'Veli bağlantıyı SMS ile aldı. İsterseniz kopyalayıp başka kanaldan da iletebilirsiniz; bağlantı tek kullanımlıktır.'
      : 'SMS gönderilmedi. Bağlantıyı kopyalayıp veliye iletin; bağlantı tek kullanımlıktır.',
    submitLabel: 'Kapat',
    fields: [{ key: 'link', label: 'Form bağlantısı', type: 'custom', render: () => {
      const wrap = h('div', 'ak-club-link');
      const input = h('input', 'kit-input');
      input.readOnly = true;
      input.value = invite.formUrl;
      const copy = button('Kopyala', { onClick: async () => {
        app.toast(await copyText(invite.formUrl) ? 'Bağlantı kopyalandı.' : 'Kopyalanamadı; bağlantıyı seçip kopyalayın.',
          'info');
      } });
      wrap.append(input, copy);
      return wrap;
    } }],
    onSubmit: async () => true,
  });
}

async function newBranchInvite(app, { opener = null, done } = {}) {
  let defaults = clubSettings(null);
  try {
    if (app.can('admin_getSettings')) defaults = clubSettings(await app.api.getCached(SETTINGS));
  } catch { /* ayar okunamazsa boş tutarla açılır */ }
  const pkg = [defaults.packageName, EXAM_TYPE[defaults.examType], defaults.examCount ? `${defaults.examCount} deneme` : '',
    defaults.seasonStart && defaults.seasonEnd ? `${fmtDate(defaults.seasonStart)} – ${fmtDate(defaults.seasonEnd)}` : '']
    .filter(Boolean).join(' · ');
  const result = await formDialog(app, {
    title: 'Yeni kulüp kaydı (kurumda)',
    description: `Veliye tek kullanımlık kayıt formu SMS ile gider; form dolunca öğrenci "${defaults.className}" sınıfına `
      + `eklenir, kulüp sözleşmesi ve tahsilat kendiliğinden yazılır.${pkg ? ` Paket: ${pkg}.` : ''}`,
    submitLabel: 'Kaydı başlat',
    value: { name: '', phone: '', paymentMethod: 'cash', amountMinor: defaults.priceMinor ?? null,
      paid: true, send: true },
    fields: [
      { key: 'name', label: 'Veli / öğrenci adı', type: 'text', maxLength: 120,
        hint: 'SMS\'te ve listede görünür; asıl bilgiler formdan gelir.' },
      { key: 'phone', label: 'Cep telefonu', type: 'phone', required: true, hint: 'Form bağlantısı bu numaraya gider.' },
      { key: 'paymentMethod', label: 'Ödeme yöntemi', type: 'select', required: true,
        options: BRANCH_METHODS.map((value) => ({ value, label: PAYMENT_METHOD[value] })) },
      { key: 'amountMinor', label: 'Tutar', type: 'money', required: true, min: 0,
        hint: 'Kulüp sözleşmesine ve tahsilata yazılır. Ayarlardaki kulüp ücreti önerilir.' },
      { key: 'paid', label: 'Ödeme alındı (tahsilat kaydedilsin)', type: 'checkbox' },
      { key: 'send', label: 'Formu SMS ile gönder', type: 'checkbox',
        hint: 'Kapalıysa bağlantı yalnız ekranda gösterilir (kopyalayıp iletirsiniz).' },
    ],
    onSubmit: async (draft) => {
      const errors = branchInviteErrors(draft);
      const first = Object.values(errors)[0];
      if (first) throw new Error(first);
      return app.api.post(LIST, branchInviteBody(draft));
    },
  });
  opener?.focus?.();
  if (!result || result === true) return;
  app.toast(result.sendCount ? 'Kayıt formu SMS ile gönderildi.' : 'Davet açıldı.', 'good');
  done?.();
  if (result.formUrl) await showFormLink(app, result, { sent: Boolean(result.sendCount) });
}

function invitesList(app) {
  const node = h('div', 'ak-stack');
  const filters = { status: '', source: '', q: '' };

  const chips = h('div', 'ak-chips');
  chips.setAttribute('role', 'group');
  chips.setAttribute('aria-label', 'Davet durumu');
  const chipButtons = new Map();
  for (const chip of STATUS_CHIPS) {
    const item = h('button', 'kit-chip', chip.label);
    item.type = 'button';
    item.addEventListener('click', () => { filters.status = chip.key; paintChips(); list.reload(); });
    chipButtons.set(chip.key, item);
    chips.append(item);
  }
  function paintChips() {
    for (const [key, item] of chipButtons) {
      item.classList.toggle('on', key === filters.status);
      item.setAttribute('aria-pressed', key === filters.status ? 'true' : 'false');
    }
  }
  paintChips();
  const source = selectBox([{ value: '', label: 'Bütün kaynaklar' },
    ...Object.entries(INVITE_SOURCE).map(([value, label]) => ({ value, label }))], '', (value) => {
    filters.source = value;
    list.reload();
  }, { label: 'Kaynak' });
  const search = searchBox({ placeholder: 'Ara: ad, telefonun son haneleri, sipariş no', label: 'Davet ara',
    onSearch: (text) => { filters.q = text; list.reload(); } });
  node.append(toolbar(chips), toolbar(source.node, search.node));

  const can = {
    resend: app.can('admin_resendClubInvite'),
    cancel: app.can('admin_cancelClubInvite'),
    retry: app.can('admin_retryClubInvite'),
  };

  function actionsCell(row) {
    const allowed = inviteActions(row);
    const cell = h('div', 'ak-club-actions');
    const stop = (fn) => (event) => { event?.stopPropagation?.(); fn(); };
    if (allowed.retry && can.retry) {
      const retry = button('Yeniden dene', { variant: 'primary', title: 'İşlemi kaldığı adımdan yeniden başlat.',
        onClick: stop(() => retryInvite(row, retry)) });
      const match = button('KM\'den eşleştir', { variant: 'ghost',
        title: 'Belirsiz eşleşme: mevcut öğrenciyi seçin; yeni öğrenci açılmaz, kulüp sınıfı ek olarak eklenir.',
        onClick: stop(() => matchInvite(row)) });
      cell.append(retry, match);
    }
    if (allowed.resend && can.resend) {
      const resend = button('Yeniden gönder', { variant: 'ghost',
        title: 'Yeni bağlantı üretip SMS\'i tekrar gönderir (eski bağlantı geçersiz olur; saatte en çok 3).',
        onClick: stop(() => resendInvite(row, resend)) });
      cell.append(resend);
    }
    if (allowed.cancel && can.cancel) {
      cell.append(button('İptal', { variant: 'ghost', title: 'Daveti iptal et (bağlantı kapanır).',
        onClick: stop(() => cancelInvite(row)) }));
    }
    return cell;
  }

  const list = pagedList({
    app,
    path: LIST,
    limit: 50,
    query: () => inviteQuery(filters),
    rowKey: (row) => String(row.id),
    columns: [
      { key: 'who', label: 'Davet', width: 'minmax(0, 1.2fr)', cell: inviteCell },
      { key: 'source', label: 'Kaynak', width: '120px', cell: sourceCell },
      { key: 'amount', label: 'Tutar', width: '150px', align: 'num', cell: amountCell },
      { key: 'status', label: 'Durum', width: 'minmax(0, 1fr)', cell: statusCell },
      { key: 'student', label: 'Öğrenci / sözleşme', width: 'minmax(0, 1fr)', cell: (row) => studentCell(app, row) },
      { key: 'date', label: 'Gönderim', width: '110px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', fmtDate(row.lastSentAt || row.createdAt)));
        if ((row.sendCount || 0) > 1) cell.append(h('small', '', `${row.sendCount}. gönderim`));
        return cell;
      } },
      { key: 'actions', label: '', width: '230px', cell: actionsCell },
    ],
    empty: { title: 'Kulüp daveti yok',
      text: 'BBDSTORE\'dan ödenen kulüp siparişleri buraya kendiliğinden düşer; kurumda alınan ödemeler için '
        + '"Yeni kulüp kaydı".' },
  });
  node.append(list.node);

  /** Tek daveti Akademi'den tazele (`admin_getClubInvite`) — listeyi baştan çekmeden satırı günceller. */
  async function refreshRow(id) {
    if (!app.can('admin_getClubInvite')) { await list.reload(); return; }
    try {
      const fresh = await app.api.get(`${LIST}/${encodeURIComponent(id)}`);
      if (fresh?.id) list.replace((item) => String(item.id) === String(id), fresh);
    } catch {
      await list.reload();
    }
  }

  async function retryInvite(row, control, studentId = null) {
    const done = await runAction(app, () => app.api.post(`${LIST}/${encodeURIComponent(row.id)}:retry`,
      studentId ? { studentId } : {}), { success: 'Kayıt yeniden işleniyor.', button: control });
    if (done !== undefined) await refreshRow(row.id);
  }

  async function matchInvite(row) {
    let picked = null;
    const ok = await formDialog(app, {
      title: 'KM\'den eşleştir',
      description: `${row.name || 'Bu davet'}: formdaki öğrenci mevcut bir öğrenciyle belirsiz eşleşti. Doğru öğrenciyi `
        + 'seçin — yeni öğrenci açılmaz; öğrenci "Deneme Kulübü" sınıfına EK olarak eklenir, kulüp sözleşmesi ona bağlanır.',
      submitLabel: 'Eşleştir ve yeniden dene',
      fields: [{ key: 'student', label: 'Öğrenci', type: 'custom', required: true, render: (_draft, set) => {
        const wrap = h('div', 'ak-stack');
        const chosen = h('div', 'ak-muted', 'Henüz seçilmedi.');
        const picker = studentPicker(app, { onPick: (student) => {
          picked = student;
          set(student.id);
          chosen.textContent = `Seçilen: ${student.displayName || student.fullName || student.id}`;
        } });
        wrap.append(picker.node, chosen);
        return wrap;
      } }],
      onSubmit: async () => true,
    });
    if (ok && picked) await retryInvite(row, null, picked.id);
  }

  async function resendInvite(row, control) {
    const sure = await confirmAction(app, { title: 'Formu yeniden gönder',
      description: 'Yeni bir bağlantı üretilir ve SMS tekrar gider; eski bağlantı çalışmaz. Geçerlilik süresi uzar.',
      confirmLabel: 'Gönder' });
    if (!sure) return;
    const reply = await runAction(app, () => app.api.post(`${LIST}/${encodeURIComponent(row.id)}:resend`, {}),
      { success: 'Form yeniden gönderildi.', button: control });
    if (reply === undefined) return;
    await refreshRow(row.id);
    if (reply?.formUrl) await showFormLink(app, reply, { sent: true });
  }

  async function cancelInvite(row) {
    const reason = await reasonDialog(app, { title: 'Daveti iptal et',
      description: 'Form bağlantısı kapanır; veli formu dolduramaz. İşlenmiş kayıt iptal edilemez.',
      confirmLabel: 'İptal et', presets: ['Veli vazgeçti', 'Yanlış numara', 'Ödeme iade edildi'] });
    if (!reason) return;
    const done = await runAction(app, () => app.api.post(`${LIST}/${encodeURIComponent(row.id)}:cancel`, { reason }),
      { success: 'Davet iptal edildi.' });
    if (done !== undefined) await refreshRow(row.id);
  }

  async function poll() {
    const rows = list.rows();
    if (!rows.length && !list.node.querySelector('.kit-table, table')) return;
    const fresh = await app.api.get(LIST, { ...inviteQuery(filters), limit: 50 });
    if (inviteFingerprint(fresh?.items) !== inviteFingerprint(rows.slice(0, 50))) await list.reload();
  }

  return {
    node,
    reload: () => list.reload(),
    poll,
    destroy() { list.destroy(); search.destroy(); },
  };
}

// ------------------------------------------------------------------ kulüp ayarları

function settingsTab(app) {
  const node = h('div', 'ak-stack');
  const canSave = app.can('admin_patchSettings');
  let original = null;

  async function reload() {
    node.replaceChildren(h('div', 'ak-muted', 'Ayarlar yükleniyor…'));
    try {
      const raw = await app.api.getCached(SETTINGS);
      original = raw?.club && typeof raw.club === 'object' ? raw.club : {};
      render(clubSettings(raw));
    } catch (failure) {
      node.replaceChildren(alertBox(`Kulüp ayarları okunamadı: ${failure?.message || failure}`, 'bad'),
        button('Tekrar dene', { variant: 'ghost', onClick: () => reload() }));
    }
  }

  function render(values) {
    const draft = { ...values };
    const form = h('div', 'kit-form-grid ak-set-grid');
    const errorSlots = new Map();
    const field = (key, label, control, hint = '', wide = false) => {
      const wrap = h('label', `kit-field${wide ? ' kit-field-wide' : ''}`);
      wrap.append(h('span', 'kit-field-label', label), control);
      if (hint) wrap.append(h('span', 'kit-field-hint', hint));
      const slot = h('span', 'kit-field-error');
      errorSlots.set(key, slot);
      wrap.append(slot);
      return wrap;
    };
    const text = (key, { maxLength = 120, placeholder = '', type = 'text' } = {}) => {
      const input = h('input', 'kit-input');
      input.type = type;
      input.maxLength = maxLength;
      input.value = draft[key] ?? '';
      if (placeholder) input.placeholder = placeholder;
      input.disabled = !canSave;
      input.addEventListener('input', () => { draft[key] = input.value; paint(); });
      return input;
    };
    const number = (key, { min = 0, max = 999 } = {}) => {
      const input = h('input', 'kit-input');
      input.type = 'number';
      input.min = String(min);
      input.max = String(max);
      input.value = draft[key] ?? '';
      input.disabled = !canSave;
      input.addEventListener('input', () => { draft[key] = input.value === '' ? null : Number(input.value); paint(); });
      return input;
    };
    const examSelect = h('select', 'kit-select');
    for (const [value, label] of Object.entries(EXAM_TYPE)) {
      const option = h('option', '', label);
      option.value = value;
      examSelect.append(option);
    }
    examSelect.value = draft.examType || 'TYT';
    examSelect.disabled = !canSave;
    examSelect.addEventListener('change', () => { draft.examType = examSelect.value; paint(); });
    const price = h('input', 'kit-input');
    price.inputMode = 'decimal';
    price.value = draft.priceMinor === null || draft.priceMinor === undefined ? '' : String(draft.priceMinor / 100)
      .replace('.', ',');
    price.placeholder = 'ör. 20000';
    price.disabled = !canSave;
    price.addEventListener('input', () => {
      const raw = price.value.trim().replace(/\./g, '').replace(',', '.');
      draft.priceMinor = raw === '' || Number.isNaN(Number(raw)) ? null : Math.round(Number(raw) * 100);
      paint();
    });
    const sms = h('textarea', 'kit-input');
    sms.rows = 3;
    sms.maxLength = 160;
    sms.value = draft.smsText || '';
    sms.disabled = !canSave;
    const smsCount = h('span', 'kit-field-hint');
    sms.addEventListener('input', () => { draft.smsText = sms.value; paint(); });
    const auto = h('input');
    auto.type = 'checkbox';
    auto.checked = draft.autoInviteFromStore !== false;
    auto.disabled = !canSave;
    auto.addEventListener('change', () => { draft.autoInviteFromStore = auto.checked; paint(); });
    const autoWrap = h('label', 'kit-check');
    autoWrap.append(auto, h('span', '', 'BBDSTORE\'da ödenen kulüp siparişlerine davet kendiliğinden gitsin'));

    form.append(
      field('className', 'KM sınıf adı', text('className', { maxLength: 20 }),
        'Kulüp üyeleri bu sınıfa eklenir (yoksa açılır).'),
      field('packageName', 'Paket adı', text('packageName'), 'Sözleşmede "DENEME KULÜBÜ — {paket}".'),
      field('examType', 'Sınav türü', examSelect),
      field('examCount', 'Deneme sayısı', number('examCount', { min: 1, max: 200 })),
      field('seasonStart', 'Sezon başlangıcı', text('seasonStart', { type: 'date' })),
      field('seasonEnd', 'Sezon bitişi', text('seasonEnd', { type: 'date' })),
      field('priceMinor', 'Kulüp ücreti (₺)', price, 'Kurumda kayıtta önerilen tutar.'),
      field('inviteValidDays', 'Form geçerliliği (gün)', number('inviteValidDays', { min: 1, max: 60 })),
      field('smsText', 'SMS metni', sms, '{link} form bağlantısıyla değişir; en çok 160 karakter.', true),
      field('autoInviteFromStore', 'BBDSTORE', autoWrap, '', true),
      field('mersisNo', 'MERSİS no', text('mersisNo', { maxLength: 20 }), 'Ön bilgilendirme formunda (boşsa basılmaz).'),
      field('taxOffice', 'Vergi dairesi', text('taxOffice', { maxLength: 80 })),
      field('taxNo', 'Vergi no', text('taxNo', { maxLength: 20 })),
      field('email', 'Kurum e-postası', text('email', { maxLength: 120, type: 'email' })),
    );
    smsCount.textContent = '';
    errorSlots.get('smsText')?.before(smsCount);

    const status = h('span', 'ak-muted');
    const save = actionButton(app, 'admin_patchSettings', 'Kaydet', { variant: 'primary', onClick: () => submit() });
    const bar = h('div', 'ak-club-save');
    bar.append(status, h('span', 'kit-spacer'), save);

    function paint(showErrors = false) {
      const errors = settingsErrors(draft);
      for (const [key, slot] of errorSlots) slot.textContent = showErrors || key === 'smsText' ? errors[key] || '' : '';
      smsCount.textContent = `${String(draft.smsText || '').length} / 160`;
      const patch = settingsPatch(original, draft);
      const count = patch ? Object.keys(patch.club).length : 0;
      status.textContent = count ? `${count} ayar değişti` : 'Değişiklik yok';
      if ('disabled' in save) save.disabled = !count;
    }

    async function submit() {
      const errors = settingsErrors(draft);
      if (Object.keys(errors).length) { paint(true); app.toast('İşaretli alanları düzeltin.', 'bad'); return; }
      const patch = settingsPatch(original, draft);
      if (!patch) return;
      const reply = await runAction(app, () => app.api.patch(SETTINGS, patch),
        { success: 'Kulüp ayarları kaydedildi.', button: save });
      if (reply === undefined) return;
      app.api.invalidate(SETTINGS);
      original = { ...(original || {}), ...patch.club };
      paint();
    }

    node.replaceChildren(
      alertBox('Bu ayarlar yeni davetlere ve kulüp sözleşmelerine uygulanır. Banka hesapları Sözleşmeler → Sözleşme '
        + 'ayarları\'ndadır (havale/EFT ödemesi).', 'info'),
      form, bar);
    paint();
  }

  return { node, reload, poll: async () => {} };
}
