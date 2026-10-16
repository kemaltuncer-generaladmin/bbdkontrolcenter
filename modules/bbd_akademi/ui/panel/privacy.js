// BBD Akademi — KVKK talepleri (F-00.7; sözleşme README §14 madde 41–44).
//
// Uygulama kullanıcıları (veli, öğrenci, personel) hesap silme ve veri
// indirme talebini uygulamadan açar; kurum burada görür ve en geç 30 gün
// içinde sonuçlandırır (KVKK m.13). Kurum karar vermezse silme talebi 30. gün
// sonunda Akademi'de kendiliğinden işlenir; 23. günden sonra yöneticilere
// hatırlatma gider. İş kuralı Akademi'dedir; bu ekran yalnız kuyruğu, kararı
// ve "erişimi yeniden aç"ı gösterir.
//
// Yazmalar (`admin_decideDataRequest`, `admin_reopenErasedAccess`) vekilden
// `X-Akademi-On-Behalf-Of: km:user:<id>:<ad>` ile gider; talebin "sonuçlandıran"
// alanında KM kullanıcısının adı görünür. Telefon hiç dönmez (A6).
//
// İzin: KM `bbd_akademi.privacy` (yalnız admin) ↔ Akademi `privacy.requests`.
// KM panel anahtarı bu izinden ÖNCE üretildiyse Akademi 403 döner: anahtar
// `km_panel` kümesiyle yeniden üretilmelidir (README → Kurulum).

import { h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, fmtDate, fmtDateTime, kv, loader, openDrawer, pagedList, pinStepUp,
  reasonDialog, runAction, sectionHead, selectBox, statusBadge, toolbar,
} from './common.js';
import {
  KIND, REJECT_MIN, STATUS, SUBJECT, actionsFor, cleanNote, dueBadge, keyLacksPermission,
} from './privacy-logic.js';


/** Son gün hücresi: tarih + rozet (`dueBadge`). */
function dueCell(row) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', fmtDate(row.dueAt)));
  const due = dueBadge(row);
  if (due?.tone) cell.append(badge(due.text, due.tone));
  else if (due) cell.append(h('small', '', due.text));
  return cell;
}

function subjectCell(row) {
  const cell = h('div', 'ak-cell-stack');
  const subject = row.subject || {};
  cell.append(h('span', '', subject.displayName || '—'));
  cell.append(h('small', '', [SUBJECT[subject.kind] || '', subject.erased ? 'veri silindi' : '']
    .filter(Boolean).join(' · ')));
  return cell;
}

const KEY_HINT = 'Akademi bu isteği reddetti: KM panel anahtarı KVKK talep iznini (privacy.requests) '
  + 'taşımıyor. Anahtar bu izin eklenmeden önce üretilmiş. Ayarlar → Entegrasyon anahtarları\'ndan '
  + '"KM paneli" kümesiyle yeni anahtar oluşturup KM sır kasasına (bbd_akademi.api_key) yazın, '
  + 'sonra eski anahtarı iptal edin.';

export function section(app) {
  loadStyles(import.meta.url, 'privacy.css');
  const node = h('div', 'ak-stack');
  node.append(sectionHead('KVKK talepleri', {
    subtitle: 'Uygulama kullanıcılarının hesap silme ve veri indirme talepleri. Yasal süre talepten '
      + 'itibaren 30 gündür; karar verilmezse silme talebi 30. gün sonunda kendiliğinden işlenir.',
  }));

  const status = selectBox([
    { value: 'open', label: 'Açık (yeni + işleniyor)' },
    { value: 'received', label: 'Yeni' },
    { value: 'processing', label: 'İşleniyor' },
    { value: 'completed', label: 'Tamamlanan' },
    { value: 'rejected', label: 'Reddedilen' },
    { value: 'cancelled', label: 'Vazgeçilen' },
  ], 'open', () => list.reload(), { label: 'Durum' });
  const kind = selectBox([
    { value: '', label: 'Tüm türler' },
    { value: 'account_deletion', label: KIND.account_deletion },
    { value: 'data_export', label: KIND.data_export },
  ], '', () => list.reload(), { label: 'Tür' });
  const notice = h('div');
  node.append(toolbar(status.node, kind.node), notice);

  // Liste sayfası Akademi'nin 403'üyle düşerse (anahtar izni eksik) hata
  // kutusunun üstüne ne yapılacağını yaz. Ek istek yok: sayfa çağrısının
  // kendi hatası okunur.
  const listApp = { ...app, api: { ...app.api, pages: (path, query, options) => {
    const iterator = app.api.pages(path, query, options);
    return {
      get done() { return iterator.done; },
      next: () => iterator.next().catch((failure) => {
        if (keyLacksPermission(failure)) notice.replaceChildren(alertBox(KEY_HINT, 'warn'));
        throw failure;
      }),
    };
  } } };

  const list = pagedList({
    app: listApp,
    path: '/v1/admin/data-requests',
    query: () => ({ status: status.value || 'open', kind: kind.value || undefined }),
    onLoaded: (rows) => {
      notice.replaceChildren();
      const late = rows.filter((row) => row.overdue).length;
      if (late) notice.append(alertBox(`${late} açık talebin yasal süresi geçti. En kısa sürede karar verin.`, 'bad'));
    },
    onRow: (row) => openRequest(app, row.id, (next) => list.replace((item) => item.id === next.id, next)),
    columns: [
      { key: 'subject', label: 'Talep sahibi', width: 'minmax(0, 1.4fr)', cell: subjectCell },
      { key: 'kind', label: 'Tür', width: '130px', cell: (row) => KIND[row.kind] || row.kind },
      { key: 'createdAt', label: 'Talep', width: '150px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'dueAt', label: 'Son gün', width: '140px', cell: dueCell },
      { key: 'reason', label: 'Neden', width: 'minmax(0, 1.4fr)', cell: (row) => row.reason || '—' },
      { key: 'status', label: 'Durum', width: '150px', cell: (row) => statusBadge(STATUS, row.status) },
      { key: 'next', label: '', width: '190px', cell: nextStep },
    ],
    empty: { title: 'Bu süzgeçte KVKK talebi yok',
      text: 'Talepler uygulamada "Hesabımı sil" ve "Verilerimi indir" ile açılır.' },
  });
  node.append(list.node);

  let started = false;
  return {
    node,
    shown(payload) {
      if (!started) { started = true; list.reload(); }
      if (payload?.requestId) openRequest(app, payload.requestId, () => list.reload());
    },
    destroy() { list.destroy(); },
  };
}

/**
 * Satırın son sütunu: sıradaki iş. Satır tıklanınca çekmece açılır; karar
 * düğmeleri yalnız çekmecededir (satır kendisi düğmedir — iç içe düğme olmaz,
 * karar da talebin tamamı okunarak verilir).
 */
function nextStep(row) {
  if (actionsFor(row).decide) return h('span', 'ak-privacy-next', 'Karar bekliyor ›');
  if (row.accessReopenable) return h('small', 'ak-muted', 'Erişim yeniden açılabilir ›');
  return h('small', 'ak-muted', row.note || '');
}

const DECIDE = 'admin_decideDataRequest';

async function approve(app, row) {
  const name = row.subject?.displayName || 'Talep sahibi';
  const deletion = row.kind === 'account_deletion';
  const note = await reasonDialog(app, {
    title: deletion ? `${name}: hesabı silmeyi onayla` : `${name}: veri indirmeyi onayla`,
    description: deletion
      ? 'Onaydan sonra Akademi kişinin uygulama hesabını, oturumlarını ve cihazlarını, çalışma, soru, plan, '
        + 'hata defteri, ödev fotoğrafı, not, ajanda, okuma, kart, hedef ve bildirim verilerini GERİ DÖNÜŞSÜZ '
        + 'siler. Kurum cari (ödeme, makbuz) ve yoklama kayıtları yasal süre boyunca anonim kalır. '
        + 'KM\'deki öğrenci kaydı silinmez; kişi dizin senkronunda yeniden oluşmaz. İsteğe bağlı not talep '
        + 'sahibine gösterilir.'
      : 'Akademi kişinin verilerini bir dosyada hazırlar; hazır olunca talep sahibine bildirim gider ve '
        + 'dosya 7 gün indirilebilir. Dosya kuruma verilmez. İsteğe bağlı not talep sahibine gösterilir.',
    confirmLabel: deletion ? 'Silmeyi onayla' : 'Onayla',
    minLength: 0,
    danger: deletion,
  });
  if (note === null) return null;
  // KVKK kararı KM'de PIN teyidi ister (iş 63, KM-07) — onay geri dönüşsüz siler.
  const token = await pinStepUp(app, DECIDE, { title: 'KVKK kararı — PIN teyidi' });
  if (!token) return null;
  return runAction(app, () => app.api.post(`/v1/admin/data-requests/${row.id}/decision`,
    { action: 'approve', note: cleanNote(note) }, { key: `kvkk-approve-${row.id}`, stepUpToken: token }),
  { success: deletion ? 'Silme onaylandı; Akademi işliyor.' : 'Onaylandı; dosya hazırlanıyor.' });
}

async function reject(app, row) {
  const note = await reasonDialog(app, {
    title: `${row.subject?.displayName || 'Talep'}: ${KIND[row.kind] || 'talebi'} reddet`,
    description: 'Gerekçe talep sahibine bildirimle gider (KVKK m.13: ret gerekçeli olmalıdır).',
    confirmLabel: 'Reddet',
    minLength: REJECT_MIN,
  });
  if (!cleanNote(note)) return null;
  const token = await pinStepUp(app, DECIDE, {
    title: 'KVKK kararı — PIN teyidi',
    description: 'Talebi reddetmek için kendi PIN\'inizi girin.',
  });
  if (!token) return null;
  return runAction(app, () => app.api.post(`/v1/admin/data-requests/${row.id}/decision`,
    { action: 'reject', note: cleanNote(note) }, { key: `kvkk-reject-${row.id}`, stepUpToken: token }),
  { success: 'Talep reddedildi; talep sahibine bildirildi.' });
}

async function reopen(app, row) {
  const ok = await confirmAction(app, {
    title: 'Silinmiş hesabın erişimini yeniden aç',
    description: 'Silme kaydı kaldırılır: kişi KM\'de hâlâ kayıtlıysa sıradaki dizin senkronunda BOŞ bir '
      + 'hesapla geri gelir ve yeniden giriş yapabilir. Silinen veri geri gelmez.',
    confirmLabel: 'Erişimi aç',
  });
  if (!ok) return null;
  return runAction(app, () => app.api.post(`/v1/admin/data-requests/${row.id}/reopen-access`, undefined,
    { key: `kvkk-reopen-${row.id}` }), { success: 'Erişim yeniden açıldı; kişi sıradaki senkronda gelir.' });
}

/** Ayrıntı çekmecesi (`admin_getDataRequest`). */
export function openRequest(app, id, onChange) {
  const panel = openDrawer(app, { title: 'KVKK talebi' });
  const view = loader(() => app.api.get(`/v1/admin/data-requests/${id}`), (request) => {
    panel.setTitle(`${KIND[request.kind] || 'KVKK talebi'} — ${request.subject?.displayName || ''}`);
    const nodes = [];
    if (request.overdue) nodes.push(alertBox('Yasal süre (30 gün) geçti.', 'bad'));
    if (request.status === 'processing' && request.kind === 'account_deletion') {
      nodes.push(alertBox('Silme onaylandı; Akademi arka planda işliyor. İnceleme demo kurumunda '
        + 'silme işlenmez, talep bu durumda kalır.', 'info'));
    }
    nodes.push(card('', kv([
      ['Talep sahibi', request.subject?.displayName],
      ['Kişi türü', SUBJECT[request.subject?.kind] || request.subject?.kind],
      ['Kişisel veri', request.subject?.erased ? 'Silindi — hesap erişimi kapalı' : 'Duruyor'],
      ['Tür', KIND[request.kind] || request.kind],
      ['Durum', statusBadge(STATUS, request.status)],
      ['Neden', request.reason || '—'],
      ['Talep tarihi', fmtDateTime(request.createdAt)],
      ['Son gün', dueCell(request)],
      ['Sonuçlandıran', request.resolvedBy || '—'],
      ['Sonuç tarihi', request.completedAt ? fmtDateTime(request.completedAt) : '—'],
      ['Kurumun notu', request.note || '—'],
      request.kind === 'data_export'
        ? ['Dosyanın silineceği an', request.downloadExpiresAt ? fmtDateTime(request.downloadExpiresAt) : '—']
        : null,
    ])));
    const tools = h('div', 'ak-inline');
    const refresh = (next) => { if (next) { onChange?.(next); view.reload(); } };
    const can = actionsFor(request);
    if (can.decide) {
      tools.append(
        actionButton(app, 'admin_decideDataRequest', 'Onayla', { variant: 'primary',
          onClick: () => approve(app, request).then(refresh) }),
        actionButton(app, 'admin_decideDataRequest', 'Reddet', { variant: 'ghost',
          onClick: () => reject(app, request).then(refresh) }),
      );
    }
    if (can.reopen) {
      tools.append(actionButton(app, 'admin_reopenErasedAccess', 'Erişimi yeniden aç',
        { onClick: () => reopen(app, request).then(refresh) }));
    }
    if (request.subject?.kind === 'student' && request.subject?.studentId && !request.subject?.erased) {
      tools.append(actionButton(app, 'admin_getStudent', 'Öğrenci kartı', { variant: 'ghost',
        onClick: () => { panel.close(); app.go('students', { studentId: request.subject.studentId }); } }));
    }
    if (tools.childElementCount) nodes.push(tools);
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();
  return panel;
}

