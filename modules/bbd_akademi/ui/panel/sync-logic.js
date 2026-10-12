// BBD Akademi — Senkron kartının saf mantığı (import'suz, DOM'suz).
//
// `settings-sync.js` ekranı çizer; hüküm (yeşil / sarı / kırmızı / gri) ve
// metinler burada üretilir ki Node ile test edilebilsin
// (tests/test_akademi_sync_card.py). Eşikler km-merkezi-senkron §8:
// iki uç arası `seq` farkı > 100 ya da gecikme > 60 sn → sarı; iletim durdu ya
// da Akademi'ye 6 saattir ulaşılamıyor → kırmızı; iletim kapalı → gri.

const num = (value) => Number(value || 0).toLocaleString('tr-TR');

export const GATEWAY_STATE = { live: ['Canlı', 'good'], retrying: ['Yeniden deneniyor', 'warn'],
  stopped: ['Durdu', 'bad'], off: ['Kapalı', 'dim'] };
export const AKADEMI_MODE = { live: ['Canlı', 'good'], degraded: ['Gecikmeli', 'warn'],
  polling: ['Yoklama (2 dk)', 'warn'], disabled: ['Kapalı (webhook sırrı yok)', 'dim'] };
export const SEQ_GAP_WARN = 100;
export const LAG_WARN_SECONDS = 60;

const ERRORS = {
  events_disabled: 'iletim ayardan kapalı',
  invalid_signature: 'imza reddedildi (webhook sırrı iki tarafta aynı mı?)',
  invalid_integration_key: 'Akademi kiracıyı tanımadı',
  tenant_not_found: 'Akademi kiracıyı tanımadı',
  unreachable: 'Akademi\'ye ulaşılamıyor',
  timeout: 'Akademi zamanında yanıt vermedi',
  events_expired: '14 günü aşan gecikme — tam dizin eşitlemesi istendi',
};
export const errorText = (code) => (code ? `${ERRORS[code] || code}${ERRORS[code] ? ` (${code})` : ''}` : '—');

/** Gecikme metni: 0.4 → "1 sn altı", 75 → "1 dk 15 sn". */
export function lagText(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const value = Number(seconds);
  if (!Number.isFinite(value)) return '—';
  if (value < 1) return '1 sn altı';
  if (value < 60) return `${Math.round(value)} sn`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes} dk ${Math.round(value % 60)} sn`;
  return `${Math.floor(minutes / 60)} sa ${minutes % 60} dk`;
}

/**
 * İki ucun durumundan tek bir hüküm: {tone, title, reasons[]}.
 * Dışa açık — kural testle bağlanabilsin diye saf işlevdir.
 */
export function syncVerdict(gateway, akademi) {
  const reasons = [];
  let tone = 'good';
  const raise = (next, text) => {
    const rank = { good: 0, dim: 1, warn: 2, bad: 3 };
    if (rank[next] > rank[tone]) tone = next;
    reasons.push({ tone: next, text });
  };
  const gw = gateway?.status || null;
  const ev = akademi?.events || null;

  if (gateway && !gateway.supported) {
    raise('dim', 'Bu KM\'deki geçit sürümü olay iletimini desteklemiyor; Akademi 2 dk yoklamayla eşitlenir.');
  } else if (gw && !gw.enabled) {
    raise('dim', 'Olay iletimi kapalı (mobile_gateway → events_enabled). Akademi 2 dk yoklamayla eşitlenir; '
      + 'webhook sırrı Akademi\'ye girildikten sonra açın.');
  } else if (gw) {
    if (gw.state === 'stopped') raise('bad', `İletim durdu: ${errorText(gw.lastError)}.`);
    if (gw.unreachable) raise('bad', 'Akademi\'ye 6 saattir olay iletilemiyor.');
    if (gw.state === 'retrying') raise('warn', `İletim yeniden deneniyor: ${errorText(gw.lastError)}.`);
    if (Number(gw.lagSeconds) > LAG_WARN_SECONDS) {
      raise('warn', `İletilmeyi bekleyen en eski olay ${lagText(gw.lagSeconds)} önce yazıldı.`);
    }
    if (Number(gw.deadCount) > 0) {
      raise('warn', `${num(gw.deadCount)} olay Akademi tarafından reddedildi (ölü mektup); aşağıdan yeniden gönderin.`);
    }
  }
  if (ev) {
    if (ev.mode === 'degraded') raise('warn', 'Akademi aldığı olayları geç uyguluyor (60 sn\'den eski bekleyen var).');
    if (ev.mode === 'polling' && gw?.enabled) {
      raise('warn', 'Akademi olay almıyor, 2 dk yoklamaya düştü.');
    }
    if (ev.mode === 'disabled' && gw?.enabled) {
      raise('warn', 'Akademi\'de webhook sırrı tanımlı değil; KM\'nin gönderdiği olaylar kabul edilmez.');
    }
    if (Number(ev.lagSecondsP95) > LAG_WARN_SECONDS) {
      raise('warn', `Akademi'de son 1 saatin uygulama gecikmesi (p95) ${lagText(ev.lagSecondsP95)}.`);
    }
    const gap = seqGap(gw, ev);
    if (gap !== null && gap > SEQ_GAP_WARN && gw?.enabled) {
      raise('warn', `KM'nin son olayı ile Akademi'nin aldığı son olay arasında ${num(gap)} olay fark var.`);
    }
  }
  const title = { good: 'Senkron canlı', dim: 'Senkron yoklamayla çalışıyor', warn: 'Senkron gecikiyor',
    bad: 'Senkron durdu' }[tone];
  return { tone, title, reasons };
}

export function seqGap(gw, ev) {
  if (!gw || ev?.lastSeq === null || ev?.lastSeq === undefined) return null;
  return Math.max(0, Number(gw.lastSeq || 0) - Number(ev.lastSeq || 0));
}
