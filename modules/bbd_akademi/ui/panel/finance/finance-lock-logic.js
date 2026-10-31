// Kurum finansı kilidi — SAF mantık (DOM yok; Node'da sınanır). Metinler web/mobil ile AYNI (83-ortak).

export const TEXT = {
  lockedTitle: 'Kurum finansı kilitli',
  lockedText: 'Finans işlemleri için onay kodu gerekir. Kod, finans yöneticisinin telefonuna SMS ile gönderilir.',
  managerMissing: 'Finans yöneticisi tanımlı değil — KM → Personel\'den atayın.',
  request: 'Onay kodu iste',
  resend: 'Kodu yeniden gönder',
  codeLabel: 'Onay kodu (6 hane)',
  verify: 'Doğrula',
  lock: 'Finansı kilitle',
  unlock: 'Finansın kilidini aç',
  locked: 'Kurum finansı için onay kodu gerekli.',
  kpiLocked: 'Kilitli — açmak için Finans\'a gir',
  opened: 'Kurum finansı açıldı.',
  closed: 'Kurum finansı kilitlendi.',
};

/** Kalan tam dakika (yukarı yuvarlanır); süre geçtiyse 0. */
export function minutesLeft(untilIso, now = Date.now()) {
  if (!untilIso) return 0;
  const at = Date.parse(untilIso);
  if (Number.isNaN(at)) return 0;
  return Math.max(0, Math.ceil((at - now) / 60_000));
}

/** Rozet metni: "Finans açık · 28 dk". */
export function badgeText(untilIso, now = Date.now()) {
  const left = minutesLeft(untilIso, now);
  return left > 0 ? `Finans açık · ${left} dk` : TEXT.lockedTitle;
}

/** Kod gönderildi cümlesi: kime gittiği (ad + maskeli telefon). */
export function sentText(approvers = []) {
  const list = (approvers || []).filter((item) => item && (item.name || item.maskedPhone))
    .map((item) => [item.name, item.maskedPhone ? `(${item.maskedPhone})` : ''].filter(Boolean).join(' '));
  return list.length
    ? `Kod ${list.join(', ')} numarasına gönderildi. Kodu ondan alıp girin.`
    : 'Kod finans yöneticisinin telefonuna gönderildi.';
}

/** Yapıştırılan / yazılan metinden 6 haneli kod. */
export function cleanCode(text) {
  return String(text || '').replace(/\D/g, '').slice(0, 6);
}

/** Hata kilit hatası mı (vekil `403 finance_access_required`)? */
export function isFinanceLocked(error) {
  return Boolean(error && error.code === 'finance_access_required');
}

/** Kod hata kodları → kullanıcı cümlesi. */
export function codeProblem(error) {
  const code = error?.code;
  if (code === 'otp_invalid') return 'Kod hatalı. Tekrar deneyin.';
  if (code === 'otp_expired') return 'Kodun süresi doldu. Yeni kod isteyin.';
  if (code === 'otp_attempts_exceeded') return 'Çok fazla hatalı deneme. Yeni kod isteyin.';
  if (code === 'finance_manager_missing') return TEXT.managerMissing;
  if (code === 'rate_limited') return 'Çok sık kod istendi; birkaç dakika sonra tekrar deneyin.';
  return error?.message || 'İşlem tamamlanamadı.';
}
