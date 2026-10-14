// Öğrenci Tablet Yönetimi — üç sekmenin paylaştığı biçim, etiket ve pencere
// yardımcıları. Burada uç adresi ve veri durumu YOKTUR; onlar index.js'teki
// `app` nesnesindedir.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/common.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { ago, blockedButton, button, foldText, h, stampIso } from '../../ui-kit/kit.js';
import { badge, progress } from '../../ui-kit/layout.js';

export const DEFAULT_TZ = 'Europe/Istanbul';
export const OWN_PACKAGE = 'com.veykemtu.tabletkontrol';

const pad = (value) => String(value).padStart(2, '0');

// ------------------------------------------------------------------ süre

/** Saniye → "1 sa 25 dk" / "25 dk". Sıfırdan büyük ama bir dakikadan az süre yuvarlanıp yutulmaz. */
export function dur(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total > 0 && total < 60) return "1 dk'dan az";
  const minutes = Math.floor(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${minutes} dk`;
  return rest ? `${hours} sa ${rest} dk` : `${hours} sa`;
}

// ------------------------------------------------------------------ zaman

/**
 * Sunucu zaman damgası → epoch-ms.
 *
 * Sunucu `isoformat()` mikro saniyeyle (6 hane) yazıyor; WebKit'in tarih
 * çözücüsü 3 haneden fazlasını her sürümde kabul etmiyor. Fazlası atılır.
 */
export function msOf(text) {
  if (!text) return NaN;
  return Date.parse(String(text).replace(/(\.\d{3})\d+/, '$1'));
}

/** Zaman damgası → "12.10.2026 14:05". */
export function when(text) {
  if (!text) return '—';
  return stampIso(String(text).replace(/(\.\d{3})\d+/, '$1'));
}

/** Zaman damgası → "5 dakika önce". */
export function since(text) {
  const ms = msOf(text);
  return Number.isNaN(ms) ? '—' : ago(ms);
}

/** Zaman damgası → yalnız saat: "14:05". */
export function clockOf(text) {
  const ms = msOf(text);
  if (Number.isNaN(ms)) return '—';
  return new Date(ms).toLocaleTimeString('tr-TR', { hour: '2-digit', minute: '2-digit' });
}

/** Saat dilimindeki bugün (YYYY-MM-DD). Profilin günü, bilgisayarın günü değildir. */
export function todayIn(timezone) {
  try {
    return new Date().toLocaleDateString('sv-SE', { timeZone: timezone || DEFAULT_TZ });
  } catch {
    return new Date().toLocaleDateString('sv-SE');
  }
}

/** "8", "830", "8.30", "08:30" → "08:30". Geçersizse null. */
export function parseClock(text) {
  const raw = String(text ?? '').trim().replace('.', ':');
  let match = raw.match(/^(\d{1,2}):(\d{2})$/);
  if (!match) match = raw.match(/^(\d{1,2})()$/);
  if (!match) match = raw.match(/^(\d{1,2})(\d{2})$/);
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2] || 0);
  if (hours > 23 || minutes > 59) return null;
  return `${pad(hours)}:${pad(minutes)}`;
}

/** Saat alanı: elle yazılır, alan terk edilince "08:30" biçimine oturur. */
export function clockInput(value, label, onInput) {
  const input = h('input', 'kit-input tb-clock');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = 5;
  input.placeholder = 'SS:DD';
  input.value = value || '';
  input.setAttribute('aria-label', label);
  input.addEventListener('input', () => {
    input.classList.remove('bad');
    onInput?.(input.value.trim());
  });
  input.addEventListener('blur', () => {
    const clock = parseClock(input.value);
    if (clock) {
      input.value = clock;
      onInput?.(clock);
    }
    input.classList.toggle('bad', !clock);
  });
  return input;
}

// ------------------------------------------------------------- günler

export const DAYS = [
  [1, 'Pzt', 'Pazartesi'], [2, 'Sal', 'Salı'], [3, 'Çar', 'Çarşamba'],
  [4, 'Per', 'Perşembe'], [5, 'Cum', 'Cuma'], [6, 'Cmt', 'Cumartesi'], [7, 'Paz', 'Pazar'],
];
const SHORT_DAY = new Map(DAYS.map(([number, short]) => [number, short]));

/** [1,2,3,4,5] → "Hafta içi"; [1,3,5] → "Pzt, Çar, Cum". */
export function daysText(days) {
  const list = [...new Set(days || [])].sort((a, b) => a - b);
  if (list.length === 7) return 'Her gün';
  if (list.join() === '1,2,3,4,5') return 'Hafta içi';
  if (list.join() === '6,7') return 'Hafta sonu';
  const contiguous = list.length >= 3 && list.every((day, index) => index === 0 || day === list[index - 1] + 1);
  if (contiguous) return `${SHORT_DAY.get(list[0])}–${SHORT_DAY.get(list[list.length - 1])}`;
  return list.map((day) => SHORT_DAY.get(day)).join(', ');
}

/** Profilin kullanım saatlerinin tek satırlık özeti. */
export function windowsSummary(windows) {
  const list = windows || [];
  if (!list.length) return 'Her zaman açık';
  if (list.length === 1) return `${daysText(list[0].days)} ${list[0].start}–${list[0].end}`;
  return `${list.length} saat aralığı`;
}

// ------------------------------------------------------------ etiketler

export const DEVICE_STATUS = {
  online: ['Çevrimiçi', 'good'],
  offline: ['Çevrimdışı', 'warn'],
  pending: ['Eşleme bekliyor', 'info'],
  revoked: ['Eşlemesi iptal', 'dim'],
};

export function statusBadge(device) {
  const [text, tone] = DEVICE_STATUS[device?.status] || ['Bilinmiyor', 'dim'];
  return badge(text, tone);
}

/** Profilin tablette güncel olup olmadığı. Bilinmiyorsa rozet yok. */
export function syncBadge(device) {
  if (device?.sync_state === 'current') return badge('Güncel', 'good');
  if (device?.sync_state === 'behind') {
    const node = badge('Geride', 'warn');
    node.title = 'Tablet profilin son hâlini henüz almadı; bağlanınca kendiliğinden alır.';
    return node;
  }
  return null;
}

export const POLICY_HEALTH = {
  ok: ['Kısıtlamalar uygulanıyor', 'good'],
  pending: ['Yeni ayarlar uygulanıyor', 'info'],
  degraded: ['Kısıtlamalar tam uygulanamıyor', 'warn'],
  not_device_owner: ['Tam yönetim kurulmamış', 'bad'],
};

export const END_REASONS = {
  admin: 'Panelden kapatıldı',
  expired: 'Uzun süre bağlantı yok',
  logout: 'Öğrenci çıkış yaptı',
  revoked: 'Eşleme kaldırıldı',
  blocked: 'Öğrenci kısıtlandı',
  outside_hours: 'Kullanım saati bitti',
};

export const COMMAND_LABELS = {
  message: 'Mesaj',
  refresh_inventory: 'Uygulama listesini yenile',
  flush_usage: 'Kullanımı şimdi gönder',
  reboot: 'Yeniden başlat',
  self_update: 'Şimdi güncelle',
  diagnostics: 'Tanılama iste',
};

export const COMMAND_STATUS = {
  pending: ['Bekliyor', 'dim'],
  delivered: ['İletildi', 'info'],
  done: ['Tamamlandı', 'good'],
  failed: ['Başarısız', 'bad'],
  expired: ['Süresi doldu', 'warn'],
  cancelled: ['İptal edildi', 'dim'],
};

/** Tabletin bildirdiği komut sonucu kullanıcı diline çevrilir; ham metin gösterilmez. */
export function commandResult(command) {
  if (command?.status !== 'failed') return '';
  if (command.resultMessage === 'unsupported') return 'Tabletteki uygulama bu komutu tanımıyor; uygulamayı güncelleyin.';
  if (command.resultMessage === 'empty') return 'Mesaj boş olduğu için gösterilmedi.';
  if (command.type === 'self_update' && command.resultMessage === 'no_update') {
    return 'Tablete sunulan güncelleme yok: tablet zaten güncel ya da yayında daha yeni sürüm yok.';
  }
  if (command.type === 'diagnostics') return 'Tablet tanı raporunu gönderemedi.';
  return 'Tablet komutu uygulayamadı.';
}

// ------------------------------------------------- kendini güncelleme

// Tabletin kendini güncellemede bildirdiği makine kodları (sözleşme v2.2).
const SELF_UPDATE_ERRORS = {
  size_mismatch: 'İnen dosyanın boyutu beklenenden farklı',
  hash_mismatch: 'İnen dosya bozuk çıktı (SHA-256 tutmuyor)',
  unparseable_apk: 'İnen dosya geçerli bir APK değil',
  package_mismatch: 'Yüklenen APK TabletKontrol’e ait değil (paket adı farklı)',
  version_mismatch: 'APK’nın sürümü sunulan sürümle uyuşmuyor ya da kurulu sürümden büyük değil',
  incompatible_signature: 'İmza uyuşmuyor: yeni APK tabletteki sürümle aynı anahtarla imzalanmamış',
  min_sdk: 'Bu sürüm tabletin Android sürümünü desteklemiyor',
  insufficient_storage: 'Tablette yer yok',
  not_device_owner: 'Tablet tam yönetimde değil (Device Owner yok); kendini güncelleyemez',
  install_no_result: 'Android kurulumdan sonuç döndürmedi; yarım kurulum atıldı',
  install_lost: 'Kurulum yarıda kaldı (tablet yeniden başladı ya da kurulum kayboldu)',
  release_gone: 'Bu sürüm sunucuda artık yok (yenisi yüklendi)',
};

// Yeni yayın ya da "Şimdi güncelle" gelene kadar yeniden denenmeyen hatalar.
const PERMANENT_ERRORS = new Set(['incompatible_signature', 'package_mismatch', 'version_mismatch', 'min_sdk']);

/** Kendini güncelleme hata kodu → kısa Türkçe cümle; tanınmayan kod genel cümleye düşer. */
export function selfUpdateError(code) {
  const text = String(code || '');
  if (!text) return '';
  if (SELF_UPDATE_ERRORS[text]) return SELF_UPDATE_ERRORS[text];
  if (text.startsWith('download_failed')) {
    const reason = text.split(':').slice(1).join(':').trim();
    if (/^\d{3}$/.test(reason)) return `İndirme başarısız (sunucu yanıtı ${reason})`;
    return 'İndirme yarıda kaldı (bağlantı sorunu)';
  }
  if (text.startsWith('install_failed')) {
    // install_failed:<durum>:<ileti> — Android'in kendi kodu; ham hâli ipucunda durur.
    const [, status = '', ...rest] = text.split(':');
    const detail = [status, rest.join(':')].map((part) => part.trim()).filter(Boolean).join(' · ');
    return detail ? `Android kurulumu reddetti: ${detail}` : 'Android kurulumu reddetti';
  }
  return 'Güncellenemedi';
}

/** Tabletin sürümü: "0.7.2 (22)". */
export function appVersionText(device) {
  const name = device?.app_version || '';
  const code = device?.app_version_code;
  if (code === null || code === undefined) return name || null;
  return `${name ? `${name} ` : ''}(${code})`;
}

/** Zaman damgası bugünse saat, değilse tarih + saat. */
function soonText(text) {
  const ms = msOf(text);
  if (Number.isNaN(ms)) return '';
  return new Date(ms).toDateString() === new Date().toDateString() ? clockOf(text) : when(text);
}

/**
 * Tabletin son `selfUpdate` bildirimi → [Türkçe durum, rozet tonu, ipucu].
 * Bildirim yoksa (v2.2 öncesi tablet) null.
 */
export function selfUpdateState(report, totalBytes = 0) {
  if (!report || typeof report !== 'object') return null;
  const hint = [report.error, report.detail].filter(Boolean).join(' · ');
  switch (report.state) {
    case 'idle':
      return ['Güncel', 'good', ''];
    case 'installed':
      return ['Kuruldu', 'good', ''];
    case 'downloading': {
      const received = Number(report.receivedBytes || 0);
      const share = totalBytes && received ? Math.min(100, Math.floor((received * 100) / totalBytes)) : null;
      return [share === null ? 'İndiriliyor' : `İndiriliyor %${share}`, 'info', ''];
    }
    case 'ready':
      return ['Kurulmayı bekliyor (öğrenci oturumu bitince)', 'info', ''];
    case 'installing':
      return ['Kuruluyor', 'info', hint];
    case 'failed': {
      const reason = selfUpdateError(report.error);
      const retry = report.nextRetryAt ? ` — sonraki deneme ${soonText(report.nextRetryAt)}`
        : (PERMANENT_ERRORS.has(report.error)
          ? ' — yeni sürüm yüklenene ya da “Şimdi güncelle” denene kadar denenmez' : '');
      return [`Hata: ${reason}${retry}`, 'bad', hint];
    }
    default:
      return null;
  }
}

/**
 * Hatalı şifreler yüzünden kilitli girişin kalan dakikası; kilit yoksa 0.
 * `now` sunucu saatine göre şimdidir (`app.now()`).
 */
export function loginLockMinutes(device, now) {
  const until = msOf(device?.login_locked_until);
  if (Number.isNaN(until) || until <= now) return 0;
  return Math.max(1, Math.ceil((until - now) / 60_000));
}

/** "Giriş kilitli · 3 dk" rozeti; kilit yoksa null. */
export function loginLockBadge(device, now) {
  const minutes = loginLockMinutes(device, now);
  if (!minutes) return null;
  const node = badge(`Giriş kilitli · ${minutes} dk`, 'warn');
  node.title = 'Bu tablette art arda hatalı şifre girildi. Süre bitince giriş kendiliğinden açılır.';
  return node;
}

/** Pil: "%81 · şarjda". */
export function batteryText(device) {
  const value = device?.battery_percent;
  if (value === null || value === undefined) return '—';
  return `%${value}${device.charging ? ' · şarjda' : ''}`;
}

// ---------------------------------------------------------------- hata

/**
 * Hata → kısa Türkçe cümle: ne oldu + ne yapılabilir.
 *
 * Sunucunun düz metin `detail` yanıtları zaten kısa ve Türkçe, olduğu gibi
 * gösterilir. Doğrulama hataları (422) bir nesne listesi döndürür ve kabuk
 * bunu "[object Object]" diye metne çevirir; o durumda ve sunucu çöktüğünde
 * çağıranın verdiği cümle kullanılır.
 */
export function problem(error, fallback) {
  const status = Number(error?.status) || 0;
  const text = typeof error?.message === 'string' ? error.message.trim() : '';
  if (status === 403) {
    return text === 'PIN doğrulanamadı.' ? 'PIN yanlış. Tekrar deneyin.' : 'Bu işlem için yetkiniz yok.';
  }
  if (!status) return 'Sunucuya ulaşılamadı. Bağlantıyı kontrol edip tekrar deneyin.';
  if (status === 422 || (status >= 500 && status !== 503)) return fallback;
  if (!text || text.startsWith('[object') || text.startsWith('İstek başarısız')) return fallback;
  return text;
}

// --------------------------------------------------------------- düğme

export const REASONS = {
  manage: 'Bu işlem için tablet yönetme yetkiniz yok.',
  grant: 'Bu işlem için ek süre ve kısıtlama yetkiniz yok.',
};

/** Yetkiye bağlı düğme: yetki yoksa gerekçeli kapalı düğme (kit `blockedButton`). */
export function permButton(app, label, permission, { variant = '', onClick, title = '' } = {}) {
  if (!app.can(permission)) return blockedButton(label, REASONS[permission], { variant });
  return button(label, { variant, onClick, title });
}

/** İş sürerken düğme kapalı kalır; çift tıklama ikinci isteği göndermez. */
export async function busy(node, work) {
  if (node?.disabled) return undefined;
  if (node) {
    node.disabled = true;
    node.setAttribute('aria-busy', 'true');
  }
  try {
    return await work();
  } finally {
    if (node) {
      node.disabled = false;
      node.removeAttribute('aria-busy');
    }
  }
}

// ---------------------------------------------------------- bilgi listesi

/** Etiket–değer listesi. Değer metin ya da düğüm; boş değerli satır çizilmez. */
export function facts(rows) {
  const list = h('dl', 'tb-facts');
  for (const row of rows) {
    if (!row) continue;
    const [label, value, title] = row;
    if (value === null || value === undefined || value === '') continue;
    const term = h('dt', null, label);
    const detail = h('dd');
    if (value instanceof Node) detail.append(value);
    else detail.textContent = String(value);
    if (title) detail.title = title;
    list.append(term, detail);
  }
  return list;
}

/** Yan yana birden fazla rozet/metin. */
export function inline(...parts) {
  const box = h('span', 'tb-inline');
  for (const part of parts) {
    if (part === null || part === undefined || part === '') continue;
    box.append(part instanceof Node ? part : document.createTextNode(String(part)));
  }
  return box;
}

/** İki satırlı ad hücresi: kalın başlık + soluk alt satır. */
export function nameCell(title, subtitle) {
  const box = h('div', 'tb-name');
  box.append(h('b', null, title));
  if (subtitle) box.append(h('span', 'tb-sub', subtitle));
  return box;
}

export function muted(text) {
  return h('p', 'tb-muted', text);
}

/** Bir arama kutusunun aksansız eşleşmesi. */
export function matchesText(needle, ...values) {
  const folded = foldText(needle).trim();
  if (!folded) return true;
  return foldText(values.filter(Boolean).join(' ')).includes(folded);
}

// ----------------------------------------------------------- kullanım

function usageText(item) {
  if (item.unlimited) return `Sınırsız · ${dur(item.usedSeconds)} kullandı`;
  if (!item.allowed) return `Profilde izinli değil · ${dur(item.usedSeconds)}`;
  const extra = item.extraSeconds ? ` (+${dur(item.extraSeconds)} ek süre)` : '';
  return `${dur(item.usedSeconds)} / ${dur(item.limitSeconds)}${extra}`;
}

/**
 * Bir günün uygulama kullanımı: ad, kullanılan/limit ve ilerleme çubuğu.
 * `onExtra(item)` verilirse süreli uygulamada "Ek süre" düğmesi çizilir.
 */
export function usageList(items, { onExtra, emptyText = 'Bu gün için kullanım yok.' } = {}) {
  const box = h('div', 'tb-usage');
  if (!items?.length) {
    box.append(muted(emptyText));
    return box;
  }
  for (const item of items) {
    const row = h('div', 'tb-usage-row');
    const head = h('div', 'tb-usage-head');
    head.append(h('b', null, item.appName || item.packageName), h('span', 'tb-sub', usageText(item)));
    const timed = item.allowed && !item.unlimited;
    if (onExtra && timed) {
      head.append(button('Ek süre', {
        variant: 'ghost',
        title: `${item.appName || item.packageName} için ek süre ver`,
        onClick: () => onExtra(item),
      }));
    }
    row.append(head);
    if (timed && item.limitSeconds) {
      const total = item.limitSeconds + (item.extraSeconds || 0);
      const share = total ? Math.min(100, Math.round((item.usedSeconds / total) * 100)) : 100;
      const left = item.remainingSeconds ?? Math.max(0, total - item.usedSeconds);
      const bar = progress([]);
      bar.percent(share, left > 0 ? `Kalan ${dur(left)}` : 'Süre doldu');
      bar.node.classList.add('tb-meter');
      if (left <= 0) bar.node.classList.add('tb-meter-full');
      bar.node.setAttribute('role', 'progressbar');
      bar.node.setAttribute('aria-valuemin', '0');
      bar.node.setAttribute('aria-valuemax', '100');
      bar.node.setAttribute('aria-valuenow', String(share));
      bar.node.setAttribute('aria-label', `${item.appName || item.packageName} kullanımı`);
      row.append(bar.node);
    }
    box.append(row);
  }
  return box;
}

// -------------------------------------------------------------- pencere

// Açık pencerelerin kapatıcıları — panel kapanırken belge dinleyicisi asılı
// kalmasın diye (kit kuralı 4).
const openDialogs = new Set();

export function closeAllDialogs() {
  for (const close of [...openDialogs]) close();
}

/**
 * Panel köküne modal pencere kurar; kapatıcı döndürür.
 *
 * Esc yakalama aşamasında tutulur ve yayılmaz: pencere çekmecenin üstünde
 * açıldığında Esc yalnız pencereyi kapatmalı, alttaki çekmeceyi değil.
 */
export function modal(root, { title, className = '', onClose, closeOnOverlay = false } = {}) {
  const overlay = h('div', 'kit-overlay');
  const card = h('div', `kit-dialog tb-dialog${className ? ` ${className}` : ''}`);
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  const heading = h('h3', 'kit-dialog-title', title);
  heading.id = `tb-dialog-${Math.random().toString(36).slice(2, 9)}`;
  card.setAttribute('aria-labelledby', heading.id);
  card.append(heading);
  overlay.append(card);

  let closed = false;
  const close = (value) => {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey, true);
    openDialogs.delete(close);
    overlay.remove();
    onClose?.(value);
  };
  const onKey = (event) => {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    close(null);
  };
  document.addEventListener('keydown', onKey, true);
  if (closeOnOverlay) {
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
  }
  openDialogs.add(close);
  root.append(overlay);
  return { card, close };
}

/**
 * Kısa form penceresi: metin, çok satırlı metin, seçim ya da PIN alanları.
 *
 * `type: 'pin'` alanı maskelidir (kit `confirmWithPin` gibi) ve pencere
 * kapanınca boşaltılır: DOM'dan kopan düğümde PIN kalmaz.
 *
 * `confirmWithReason` burada kullanılmaz: onun alanı "gerekçe" diye
 * etiketlenir ve hata cümlesi gerekçe der; kilit notu ya da tablet adı
 * gerekçe değildir.
 *
 * @returns {Promise<object|null>} alan anahtarı → kırpılmış değer; vazgeçilirse null
 */
export function formDialog(root, { title, description = '', fields = [], confirmLabel = 'Kaydet',
  danger = false, validate } = {}) {
  return new Promise((resolve) => {
    const inputs = new Map();
    const { card, close } = modal(root, {
      title,
      closeOnOverlay: true,
      onClose: (value) => {
        for (const field of fields) {
          if (field.type === 'pin') inputs.get(field.key).value = '';
        }
        resolve(value);
      },
    });
    if (description) card.append(h('p', 'kit-dialog-text', description));

    const box = h('div', 'tb-dialog-fields');
    for (const field of fields) {
      const wrap = h('label', 'kit-field');
      wrap.append(h('span', 'kit-field-label', field.label));
      let input;
      if (field.type === 'select') {
        input = h('select', 'kit-select');
        for (const option of field.options || []) {
          const item = h('option', null, option.label);
          item.value = String(option.value);
          input.append(item);
        }
      } else if (field.type === 'textarea') {
        input = h('textarea', 'kit-textarea');
        input.rows = 3;
      } else if (field.type === 'pin') {
        input = h('input', 'kit-input');
        input.type = 'password';
        input.inputMode = 'numeric';
        input.autocomplete = 'off';
      } else {
        input = h('input', 'kit-input');
        input.type = 'text';
      }
      if (field.placeholder) input.placeholder = field.placeholder;
      if (field.maxLength) input.maxLength = field.maxLength;
      input.value = field.value === undefined || field.value === null ? '' : String(field.value);
      wrap.append(input);
      if (field.hint) wrap.append(h('span', 'kit-field-hint', field.hint));
      box.append(wrap);
      if (field.presets?.length) {
        const chips = h('div', 'kit-dialog-presets');
        for (const text of field.presets) {
          const chip = h('button', 'kit-chip', text);
          chip.type = 'button';
          chip.addEventListener('click', () => { input.value = text; input.focus(); });
          chips.append(chip);
        }
        box.append(chips);
      }
      inputs.set(field.key, input);
    }
    card.append(box);

    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    card.append(error);

    const values = () => Object.fromEntries(
      [...inputs].map(([key, input]) => [key, String(input.value).trim()]),
    );
    const submit = () => {
      const current = values();
      for (const field of fields) {
        const value = current[field.key];
        const message = field.required && !value ? `${field.label} boş bırakılamaz.`
          : field.minLength && value && value.length < field.minLength
            ? `${field.label} en az ${field.minLength} karakter olmalı.` : '';
        if (message) {
          error.textContent = message;
          inputs.get(field.key)?.focus();
          return;
        }
      }
      const message = validate?.(current);
      if (message) {
        error.textContent = message;
        return;
      }
      close(current);
    };
    // Tek satırlık alanda Enter onaylar; çok satırlı alanda satır başı kalır.
    card.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && event.target?.tagName === 'INPUT') {
        event.preventDefault();
        submit();
      }
    });

    const actions = h('div', 'kit-dialog-actions');
    actions.append(
      button('Vazgeç', { onClick: () => close(null) }),
      button(confirmLabel, { variant: danger ? 'danger' : 'primary', onClick: submit }),
    );
    card.append(actions);
    (inputs.values().next().value || actions.lastChild)?.focus();
  });
}
