// Öğrenci kartındaki "Tablet" hızlı işlemi (0.5.3 — "öğrenci tek yerde").
//
// `bbd_tablet.student_quick` ARAYÜZ yeteneği: öğrencinin tek kartı (BBD Akademi
// → Öğrenciler → "Kayıt ve veliler") tablet kısıtını buradan tek tıkla görür
// ve değiştirir: "Bugün sonuna kadar kısıtla" / "Kısıtlamayı kaldır". Ayrıntı
// (bitiş saati, gerekçe, ek süre) bu modülün kendi ekranında kalır.
//
//   GET  /api/bbd_tablet/students/{id}/blocks     bbd_tablet.view
//   POST /api/bbd_tablet/students/{id}/block      bbd_tablet.grant  {until, reason}
//   POST /api/bbd_tablet/students/{id}/unblock    bbd_tablet.grant
//
// Yazma olayı (`tablet.policy_changed`) uçtan gider; Akademi aynı yoldan alır.
// İzin yoksa (403) düğme gerekçesiyle kapanır — asıl kapı backend'dedir (K9).
// Import yolu kabuğun KOPYALANMIŞ konumuna göredir (index.js ile aynı).

import { button, confirmSimple, h, loadStyles } from '../../ui-kit/kit.js';

const BASE = '/api/bbd_tablet/students';
const enc = encodeURIComponent;

function pad(value) { return String(value).padStart(2, '0'); }

/** Bugünün yerel 23:59'u — saat dilimsiz (sunucu okulun saat diliminde okur). */
export function endOfToday(now = new Date()) {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}T23:59:00`;
}

/** Süren kısıt (bitişi ileride olan, etkin) ya da null. */
export function activeBlock(rows, now = Date.now()) {
  return (rows || []).find((row) => row && row.active !== false && row.until
    && new Date(row.until).getTime() > now) || null;
}

function when(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso || '');
  return date.toLocaleString('tr-TR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
}

/**
 * @param {HTMLElement} root
 * @param {{api, studentId, displayName?, dialogRoot?, toast?(metin)}} options
 * @returns {{reload, destroy}}
 */
export function mountStudentQuick(root, options) {
  const { api, studentId } = options;
  const toast = options.toast || (() => {});
  // Başka panelin içinde çizilir: bu modülün stili kendiliğinden yüklenmemiş olabilir.
  loadStyles(import.meta.url);
  let destroyed = false;
  const box = h('div', 'tb-quick');
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', 'Tablet');
  root.replaceChildren(box);

  function paint(rows, error = '') {
    box.replaceChildren(h('span', 'tb-quick-label', 'Tablet:'));
    if (error) {
      box.append(h('span', 'tb-quick-state', error));
      return;
    }
    const active = activeBlock(rows);
    box.append(h('span', `tb-quick-state${active ? ' bad' : ''}`,
      active ? `kısıtlı (bitiş ${when(active.until)})` : 'serbest'));
    const action = active
      ? button('Kısıtlamayı kaldır', { onClick: () => lift() })
      : button('Bugün sonuna kadar kısıtla', { variant: 'danger', onClick: () => block() });
    box.append(action);
  }

  async function reload() {
    try {
      const rows = await api(`${BASE}/${enc(studentId)}/blocks`);
      if (!destroyed) paint(Array.isArray(rows) ? rows : []);
    } catch (failure) {
      if (!destroyed) paint([], failure?.status === 403 ? 'görüntüleme yetkiniz yok' : 'okunamadı');
    }
  }

  async function block() {
    const yes = await confirmSimple(options.dialogRoot || root, {
      title: `${options.displayName || 'Öğrenci'} tablette bugün sonuna kadar kısıtlansın mı?`,
      description: 'Bugün 23:59\'a kadar tablete giriş yapamaz; açık oturumu hemen kapanır. Farklı bitiş '
        + 'saati ve gerekçe için Öğrenci Tablet Yönetimi ekranını kullanın.',
      confirmLabel: 'Kısıtla', danger: true });
    if (!yes) return;
    try {
      const result = await api(`${BASE}/${enc(studentId)}/block`, { method: 'POST',
        body: { until: endOfToday(), reason: 'Öğrenci kartından (bugün)' } });
      toast(result?.endedSessions ? 'Tablet kısıtlandı; açık oturum kapatıldı.' : 'Tablet kısıtlandı.');
    } catch (failure) {
      toast(failure?.status === 403 ? 'Tablet kısıtlama yetkiniz yok.' : `Kısıtlanamadı: ${failure.message}`);
    }
    reload();
  }

  async function lift() {
    try {
      const result = await api(`${BASE}/${enc(studentId)}/unblock`, { method: 'POST' });
      toast(result?.lifted ? 'Tablet kısıtlaması kaldırıldı.' : 'Kaldırılacak kısıtlama yoktu.');
    } catch (failure) {
      toast(failure?.status === 403 ? 'Tablet kısıtlama yetkiniz yok.' : `Kaldırılamadı: ${failure.message}`);
    }
    reload();
  }

  box.append(h('span', 'tb-quick-label', 'Tablet: okunuyor…'));
  reload();
  return {
    reload,
    destroy() { destroyed = true; root.replaceChildren(); },
  };
}
