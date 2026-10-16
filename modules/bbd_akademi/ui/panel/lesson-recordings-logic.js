// BBD Akademi — Ders kayıtları (F-DK, akıllı tahta): saf mantık (DOM'suz, import'suz; Node ile sınanır).
//
// Sözleşme: Akademi README §14 madde 110, `docs/integrations/ders-kaydi.md` (bağlayıcı), uçlar `/v1/admin/boards*`,
// `/v1/admin/rooms*`, `/v1/admin/recordings*` (`x-surface: both` — web / mobil yönetici ile AYNI uçlar).
// Metinler tasarım panolarıyla (`docs/design/prototype/DkYonetimTahtalar|Kayitlar|Depolama|Ayarlar`) ve web / mobil
// yönetici ekranlarıyla aynıdır: "Tahtalar ve derslikler", "Kayıtlar", "Depolama", "Ayarlar", "Eşleme kodu",
// "Yayında", "Gizli", "Kurtarılmış", "Kayıtta", "Kim izledi", "Paylaş", "Kaydı sil" …
//
// Birimler: depolama sözleşmede ONDALIK bayttır (800 GB = 800 000 000 000); ekranda GB 1e9 ile hesaplanır.

export const GB = 1_000_000_000;
export const RETENTION_MAX = 120;
export const QUOTA_MIN_GB = 10;
export const QUOTA_MAX_GB = 800;
export const SHARE_MAX_STUDENTS = 100;
export const SHARE_MAX_GROUPS = 20;

/** Ekran sekmeleri (tasarım panosu sırası; web ile aynı). */
export const TABS = [
  { key: 'boards', label: 'Tahtalar ve derslikler' },
  { key: 'recordings', label: 'Kayıtlar' },
  { key: 'storage', label: 'Depolama' },
  { key: 'settings', label: 'Ayarlar' },
];

/** Sekme → onu açan OKUMA işlemi (izin / bayrak yoksa sekme çizilmez). */
export const TAB_READS = {
  boards: 'admin_listBoards',
  recordings: 'admin_listRecordings',
  storage: 'admin_getRecordingQuota',
  settings: 'admin_getRecordingSettings',
};

/** `MicrophoneKind` → kısa metin. */
export const MIC_KIND = {
  usb: 'USB',
  bluetooth_hfp: 'Bluetooth HFP',
  bluetooth_other: 'Bluetooth',
  builtin: 'Yerleşik',
};

/** `RecordingQuality` → [başlık, alt metin] (tasarım panosu). */
export const QUALITY = {
  '1080p25': ['1080p · 25 kare', '~0,9 GB/saat · önerilen'],
  '720p25': ['720p · 25 kare', '~0,5 GB/saat · yavaş internet'],
};

/** `BoardScene` → metin (tahta uygulamasıyla aynı: "Tahta", "Tahta + Ben", "Sadece Ben"). */
export const SCENE = { board: 'tahta', board_cam: 'tahta + kamera', cam: 'kamera' };

/** `LessonRecordingFlag` → kısa uyarı (öğrenci / veli ekranlarıyla aynı metin). */
export const FLAGS = {
  recovered: 'Kurtarılmış kayıt',
  fallback_mic: 'Ses kalitesi düşük olabilir',
  mic_gaps: 'Bazı bölümlerde ses yok',
  incomplete: 'Bazı bölümler eksik',
};

/** Kayıt listesi süzgeç çipleri (tasarım panosu) → sorgu. */
export const REC_FILTERS = [
  { key: 'all', label: 'Tümü' },
  { key: 'ready', label: 'Yayında' },
  { key: 'hidden', label: 'Gizli' },
  { key: 'recovered', label: 'Kurtarılmış' },
  { key: 'recording', label: 'Kayıtta' },
  { key: 'failed', label: 'Başarısız' },
];

export const REC_SORTS = [
  { value: 'recent', label: 'En yeni' },
  { value: 'oldest', label: 'En eski' },
  { value: 'largest', label: 'En büyük' },
];

/** Süzgeç çipi + ek süzgeçler → `admin_listRecordings` sorgusu (boş değer gönderilmez). */
export function recordingQuery(filter = 'all', extra = {}) {
  const query = {};
  if (filter === 'ready') { query.status = 'ready'; query.hidden = false; }
  else if (filter === 'hidden') query.hidden = true;
  else if (filter === 'recovered') query.recovered = true;
  else if (filter === 'recording') query.status = 'recording';
  else if (filter === 'failed') query.status = 'failed';
  for (const name of ['from', 'to', 'teacherId', 'groupId', 'subjectId']) {
    const value = extra[name];
    if (value !== undefined && value !== null && String(value).trim() !== '') query[name] = String(value).trim();
  }
  query.sort = REC_SORTS.some((item) => item.value === extra.sort) ? extra.sort : 'recent';
  return query;
}

/** Tarih aralığı denetimi ('YYYY-MM-DD'). */
export function rangeProblem(from, to) {
  if (from && to && String(to) < String(from)) return 'Bitiş tarihi başlangıçtan önce olamaz.';
  return null;
}

// ------------------------------------------------------------------ tahta

/**
 * Tahtanın durumu → {key, label, tone}. Sıra: iptal > eşleme bekliyor > kayıtta > çevrimiçi > çevrimdışı.
 * `online` sunucudan gelir (son nabız < 2 dk).
 */
export function boardState(device) {
  if (!device) return { key: 'unknown', label: '—', tone: 'dim' };
  if (device.status === 'revoked') return { key: 'revoked', label: 'İptal edildi', tone: 'dim' };
  if (device.status === 'pending_pairing') return { key: 'pending', label: 'Eşleme bekliyor', tone: 'info' };
  if (device.activeRecordingId && device.online) return { key: 'recording', label: 'Kayıtta', tone: 'bad' };
  if (device.online) return { key: 'online', label: 'Çevrimiçi', tone: 'good' };
  return { key: 'offline', label: 'Çevrimdışı', tone: 'dim' };
}

/**
 * Mikrofon hücresi → {text, warn, hint}. Harici mikrofon ZORUNLU: takılı değil, yerleşik ya da yedeğe düşmüş →
 * uyarı; Bluetooth eller serbest (HFP) profili kayıt başlatır ama ses kalitesi düşük olabilir.
 */
export function micText(device) {
  if (!device || !device.microphoneName) {
    return { text: 'Takılı değil', warn: device?.status === 'active', hint: 'Harici mikrofonu tak' };
  }
  const kind = MIC_KIND[device.microphoneKind] || '';
  const text = kind ? `${device.microphoneName} · ${kind}` : device.microphoneName;
  if (device.microphoneFallback) return { text, warn: true, hint: 'Mikrofon bağlantısı koptu' };
  if (device.microphoneKind === 'builtin') return { text, warn: true, hint: 'Harici mikrofonu tak' };
  if (device.microphoneKind === 'bluetooth_hfp') {
    return { text, warn: true, hint: 'Ses kalitesi düşük olabilir (Bluetooth eller serbest)' };
  }
  return { text, warn: false, hint: '' };
}

/** Tahta satırının eylemleri (yetki ayrıca `app.can` ile). */
export function boardActions(device) {
  const revoked = device?.status === 'revoked';
  return {
    pairLabel: revoked ? 'Yeniden eşle' : (device?.status === 'pending_pairing' ? 'Yeni kod' : 'Eşleme kodu'),
    revoke: device?.status === 'active',
    cancelCode: Boolean(device?.pairingExpiresAt) && device?.status !== 'revoked',
  };
}

/** Eşleme kodu yeniden üretilirken uyarı gerekir mi (eşli tahta bağlantısını keser). */
export function pairingBreaksLink(device) {
  return device?.status === 'active';
}

/** `SmartboardDeviceWrite` gövdesi. */
export function boardBody(draft = {}) {
  const name = String(draft.name ?? '').trim();
  if (!name) throw new Error('Tahta adı zorunlu.');
  if (name.length > 60) throw new Error('Tahta adı en çok 60 karakter olabilir.');
  const roomId = String(draft.roomId ?? '').trim();
  return { name, roomId: roomId || null };
}

/** `RoomWrite` gövdesi. */
export function roomBody(draft = {}) {
  const name = String(draft.name ?? '').trim().replace(/\s+/g, ' ');
  if (!name) throw new Error('Derslik adı zorunlu.');
  if (name.length > 40) throw new Error('Derslik adı en çok 40 karakter olabilir.');
  const rawCapacity = draft.capacity;
  let capacity = null;
  if (rawCapacity !== undefined && rawCapacity !== null && String(rawCapacity).trim() !== '') {
    capacity = Number(rawCapacity);
    if (!Number.isInteger(capacity) || capacity < 1 || capacity > 500) {
      throw new Error('Kapasite 1–500 arasında tam sayı olmalı.');
    }
  }
  return { name, capacity, active: draft.active === undefined ? true : Boolean(draft.active) };
}

/** Derslik satırının alt metni: "Tahta 1 bağlı" / "2 tahta bağlı" / "Tahta yok". */
export function roomSub(room, devices = []) {
  const linked = devices.filter((device) => device?.room?.id === room?.id && device.status !== 'revoked');
  if (linked.length === 1) return `${linked[0].name} bağlı`;
  if (linked.length > 1) return `${linked.length} tahta bağlı`;
  const count = Number(room?.boardCount || 0);
  return count ? `${count} tahta bağlı` : 'Tahta yok';
}

/** Üst bant: kayıtta olan tahtalar → "Derslik 3 · Tahta 1" listesi (yoksa null). */
export function liveText(devices = []) {
  const live = devices.filter((device) => device?.online && device.activeRecordingId);
  if (!live.length) return null;
  const names = live.map((device) => device.room?.name || device.name);
  return `Kayıt sürüyor: ${names.join(', ')}`;
}

/** Tanılama raporunun özet satırları [[etiket, değer], …] (kişisel veri yok — A6). */
export function diagnosticsRows(report) {
  const data = report?.report || {};
  const test = data.test || null;
  const micList = (data.microphones || []).map((mic) => `${mic.name}${MIC_KIND[mic.kind] ? ` · ${MIC_KIND[mic.kind]}` : ''}`);
  const rows = [
    ['Uygulama sürümü', data.appVersion || '—'],
    ['İşletim sistemi', data.os || '—'],
    ['İşlemci', data.cpu || '—'],
    ['Bellek', data.ramMb ? `${Math.round(data.ramMb / 1024)} GB` : '—'],
    ['Ekran kartı', (data.gpus || []).join(', ') || '—'],
    ['Kodlayıcılar', (data.encoders || []).join(', ') || '—'],
    ['Seçilen kodlayıcı', data.selectedEncoder || '—'],
    ['Ekranlar', (data.displays || []).map((d) => `${d.name} ${d.width}×${d.height}${d.primary ? ' (ana)' : ''}`).join(', ') || '—'],
    ['Mikrofonlar', micList.join(', ') || '—'],
    ['Seçili mikrofon', data.selectedMicrophone ? `${data.selectedMicrophone.name}${MIC_KIND[data.selectedMicrophone.kind] ? ` · ${MIC_KIND[data.selectedMicrophone.kind]}` : ''}` : '—'],
    ['Kameralar', (data.cameras || []).join(', ') || '—'],
    ['Boş disk', data.freeDiskBytes === null || data.freeDiskBytes === undefined ? '—' : gbText(data.freeDiskBytes)],
    ['ffmpeg', data.ffmpegVersion || '—'],
  ];
  if (test) {
    rows.push(['Deneme kaydı', test.ok ? `Başarılı · ${test.durationSeconds} sn` : `Başarısız${test.error ? ` · ${test.error}` : ''}`]);
    if (test.avgVideoKbps) rows.push(['Ortalama bit hızı', `${test.avgVideoKbps} kbps`]);
    if (test.cpuPercent !== null && test.cpuPercent !== undefined) rows.push(['İşlemci kullanımı', `%${Math.round(test.cpuPercent)}`]);
    if (test.gpuPercent !== null && test.gpuPercent !== undefined) rows.push(['Ekran kartı kullanımı', `%${Math.round(test.gpuPercent)}`]);
    if (test.droppedFrames !== null && test.droppedFrames !== undefined) rows.push(['Düşen kare', String(test.droppedFrames)]);
    if (test.bytesPerHourEstimate) rows.push(['Saatlik boyut (tahmin)', `${gbText(test.bytesPerHourEstimate)}/saat`]);
    if (test.uploadMbps !== null && test.uploadMbps !== undefined) rows.push(['Yükleme hızı', `${decimal(test.uploadMbps, 1)} Mbps`]);
    if (test.micPeakDb !== null && test.micPeakDb !== undefined) rows.push(['Mikrofon tepe seviyesi', `${decimal(test.micPeakDb, 1)} dB`]);
  }
  return rows;
}

export const DIAG_TRIGGER = {
  enroll: 'İlk eşleme',
  update: 'Sürüm güncellemesi',
  admin_request: 'Yönetici isteği',
  start_failed: 'Kayıt başlatılamadı',
  manual: 'Tahtadan gönderildi',
};

// ------------------------------------------------------------------ kayıt

/** Kaydın durumu → [metin, ton]. Gizli kayıt (öğrenci / veli görmez) "Gizli" öne çıkar. */
export function recordingState(recording) {
  if (!recording) return ['—', 'dim'];
  if (recording.status === 'recording') return ['Kayıtta', 'bad'];
  if (recording.status === 'finalizing') return ['Yükleniyor', 'info'];
  if (recording.status === 'failed') return ['Başarısız', 'bad'];
  if (recording.status === 'deleted') return ['Silindi', 'dim'];
  if (recording.hidden) return ['Gizli', 'dim'];
  if (recording.recovered) return ['Kurtarılmış', 'warn'];
  return ['Yayında', 'good'];
}

/** Ders başlığı: "Matematik · 8-A, 8-B" (+ parça). */
export function recordingTitle(recording) {
  const subject = recording?.subject?.name || 'Ders';
  const groups = (recording?.groups || []).map((group) => group.name).filter(Boolean).join(', ');
  const part = Number(recording?.part || 1) > 1 ? ` · ${recording.part}. bölüm` : '';
  return `${subject}${groups ? ` · ${groups}` : ''}${part}`;
}

/** Başlık altı: sahne + derslik + etiketler ("tahta + kamera · Derslik 3 · Kurtarılmış kayıt"). */
export function recordingExtra(recording) {
  const parts = [];
  const scene = recording?.scene ? SCENE[recording.scene] : (recording?.hasCamera ? SCENE.board_cam : null);
  if (scene) parts.push(scene);
  if (recording?.room) parts.push(recording.room);
  for (const flag of recording?.flags || []) if (FLAGS[flag]) parts.push(FLAGS[flag]);
  return parts.join(' · ');
}

/** Süre (sn) → "38 dk" / "1 sa 5 dk" / "45 sn". */
export function durationText(seconds) {
  const total = Math.max(0, Math.round(Number(seconds) || 0));
  if (total < 60) return `${total} sn`;
  const minutes = Math.round(total / 60);
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${minutes} dk`;
  return `${hours} sa${rest ? ` ${rest} dk` : ''}`;
}

/** Ondalık sayı → Türkçe virgüllü metin. */
export function decimal(value, digits = 1) {
  return Number(value || 0).toFixed(digits).replace('.', ',');
}

/** Bayt → "0,6 GB" / "412 GB" / "350 MB" (ondalık birim — sözleşmeyle aynı). */
export function gbText(bytes) {
  const value = Number(bytes) || 0;
  if (value >= 100 * GB) return `${Math.round(value / GB)} GB`;
  if (value >= 0.1 * GB) return `${decimal(value / GB, 1)} GB`;
  if (value >= 1_000_000) return `${Math.round(value / 1_000_000)} MB`;
  if (value > 0) return `${Math.max(1, Math.round(value / 1000))} KB`;
  return '0 GB';
}

/** "Süre · boyut" hücresi. */
export function sizeText(recording) {
  return `${durationText(recording?.durationSeconds)} · ${gbText(recording?.sizeBytes)}`;
}

/** "İzleyen" hücresi: "9 / 28" (izleyen / erişebilen). */
export function viewsText(stats) {
  if (!stats) return '—';
  return `${Number(stats.watchedCount || 0)} / ${Number(stats.eligibleCount || 0)}`;
}

/** Mikrofon olayları → kısa metin (yalnız yönetici / öğretmen görür). */
export function micEventsText(events) {
  if (!events) return '—';
  const parts = [];
  if (events.disconnects) parts.push(`${events.disconnects} kez koptu`);
  if (events.reconnects) parts.push(`${events.reconnects} kez geri geldi`);
  if (events.silences) parts.push(`${events.silences} uzun sessizlik`);
  if (events.fallbackSeconds) parts.push(`${durationText(events.fallbackSeconds)} yedek mikrofonla`);
  return parts.length ? parts.join(' · ') : 'Sorun yok';
}

/** Gizle / göster gövdesi (`LessonRecordingUpdate`). */
export function hideBody(recording) {
  return { hidden: !recording?.hidden };
}

/** Paylaşım gövdesi (`RecordingShareCreate`); boş ya da sınır aşımı hata. */
export function shareBody({ studentIds = [], groupIds = [], notify = true } = {}) {
  const students = [...new Set((studentIds || []).map(String).filter(Boolean))];
  const groups = [...new Set((groupIds || []).map(String).filter(Boolean))];
  if (!students.length && !groups.length) throw new Error('En az bir öğrenci ya da grup seçin.');
  if (students.length > SHARE_MAX_STUDENTS) throw new Error(`Tek seferde en çok ${SHARE_MAX_STUDENTS} öğrenci eklenebilir.`);
  if (groups.length > SHARE_MAX_GROUPS) throw new Error(`Tek seferde en çok ${SHARE_MAX_GROUPS} grup eklenebilir.`);
  const body = { notify: notify !== false };
  if (students.length) body.studentIds = students;
  if (groups.length) body.groupIds = groups;
  return body;
}

/** Paylaşım satırı metni: "Can Yılmaz (8-B)" / "8-B grubu". */
export function shareLabel(share) {
  if (!share) return '—';
  if (share.kind === 'group') return `${share.group?.name || 'Grup'} grubu`;
  const student = share.student || {};
  const cls = student.classGroup?.name;
  return `${student.displayName || 'Öğrenci'}${cls ? ` (${cls})` : ''}`;
}

/** Paylaşım panelinin açıklaması: derse atanmış gruplar zaten görür. */
export function shareIntro(recording) {
  const groups = (recording?.groups || []).map((group) => group.name).filter(Boolean);
  const eligible = recording?.viewStats?.eligibleCount;
  const head = groups.length
    ? `Derse atanmış gruplar (${groups.join(', ')}${eligible ? `, ${eligible} öğrenci` : ''}) ve velileri zaten görür.`
    : 'Derse atanmış öğrenciler ve velileri zaten görür.';
  return `${head} Buraya ek öğrenci ya da grup ekleyin.`;
}

/** "Kim izledi" satırı → durum metni. */
export function viewState(view) {
  if (!view) return ['—', 'dim'];
  if (view.completed) return ['Tamamladı', 'good'];
  if (view.watched) return ['İzledi', 'info'];
  if (view.guardianWatched) return ['Velisi açtı', 'info'];
  return ['İzlemedi', 'dim'];
}

// ---------------------------------------------------------------- depolama

/** Kota paneli görünümü (`RecordingQuota`). */
export function quotaView(quota) {
  const used = Number(quota?.usedBytes || 0);
  const total = Number(quota?.quotaBytes || 0) || QUOTA_MAX_GB * GB;
  const pct = quota?.usedPercent !== undefined && quota?.usedPercent !== null
    ? Number(quota.usedPercent) : (used / total) * 100;
  const high = Number(quota?.highWatermarkPercent || 90);
  const low = Number(quota?.lowWatermarkPercent || 80);
  const state = quota?.state || (pct >= high ? 'cleanup' : pct >= low ? 'warning' : 'ok');
  const tone = state === 'cleanup' ? 'bad' : state === 'warning' ? 'warn' : 'good';
  const stateText = state === 'cleanup' ? `%${high} aşıldı · gece temizlik`
    : state === 'warning' ? `%${low} aşıldı` : 'Yeterli alan var';
  return {
    usedText: gbText(used),
    totalText: gbText(total),
    pct: Math.max(0, pct),
    pctText: decimal(pct, 1),
    barPct: Math.min(100, Math.max(0, pct)),
    low,
    high,
    state,
    tone,
    stateText,
    summary: `${gbText(used)} / ${gbText(total)} kullanılıyor · %${decimal(pct, 1)}`,
  };
}

/** Tahmini dolma cümlesi (tasarım panosu). `fullDateText` biçimlenmiş tarih ya da null. */
export function etaText(quota, fullDateText) {
  const days = Number(quota?.retentionDays || RETENTION_MAX);
  const tail = `ayrıca her kayıt en çok ${days} gün saklanır.`;
  if (!fullDateText) {
    return `Bu hızla %${quota?.highWatermarkPercent || 90}'a ulaşılmıyor; ${tail}`;
  }
  return `Bu hızla ${fullDateText} %${quota?.highWatermarkPercent || 90}'a ulaşılır. O zaman en eski kayıtlar `
    + `%${quota?.lowWatermarkPercent || 80}'e inene dek kendiliğinden silinir; ${tail}`;
}

// ---------------------------------------------------------------- ayarlar

export function retentionProblem(value) {
  const days = Number(value);
  if (!Number.isInteger(days) || days < 1 || days > RETENTION_MAX) return `Saklama süresi 1–${RETENTION_MAX} gün olmalı.`;
  return null;
}

export function quotaProblem(valueGb) {
  const gb = Number(valueGb);
  if (!Number.isInteger(gb) || gb < QUOTA_MIN_GB || gb > QUOTA_MAX_GB) {
    return `Depolama tavanı ${QUOTA_MIN_GB}–${QUOTA_MAX_GB} GB olmalı.`;
  }
  return null;
}

/** Taslak (gün, GB, kalite, kamera) → `RecordingSettingsUpdate`. Hatalı alan hata fırlatır. */
export function settingsBody(draft = {}) {
  const problem = retentionProblem(draft.retentionDays) || quotaProblem(draft.quotaGb);
  if (problem) throw new Error(problem);
  if (!QUALITY[draft.quality]) throw new Error('Kalite seçin.');
  return {
    retentionDays: Number(draft.retentionDays),
    quotaBytes: Number(draft.quotaGb) * GB,
    quality: draft.quality,
    allowCameraScenes: draft.allowCameraScenes !== false,
  };
}

/** `RecordingSettings` → form taslağı. */
export function settingsDraft(settings = {}) {
  return {
    retentionDays: Number(settings?.retentionDays || RETENTION_MAX),
    quotaGb: Math.round(Number(settings?.quotaBytes || QUOTA_MAX_GB * GB) / GB),
    quality: QUALITY[settings?.quality] ? settings.quality : '1080p25',
    allowCameraScenes: settings?.allowCameraScenes !== false,
  };
}

/** Kaydetmeden önce uyarı: süre kısaldıysa / tavan düştüyse eski kayıtlar erken silinebilir. */
export function settingsWarning(before = {}, after = {}) {
  const notes = [];
  if (Number(after.retentionDays) < Number(before.retentionDays)) {
    notes.push(`Saklama süresi ${before.retentionDays} günden ${after.retentionDays} güne iniyor: var olan kayıtların `
      + 'silinme tarihi yeniden hesaplanır, süresi dolanlar gece temizliğinde silinir.');
  }
  if (Number(after.quotaGb) < Number(before.quotaGb)) {
    notes.push(`Depolama tavanı ${before.quotaGb} GB'tan ${after.quotaGb} GB'a iniyor: kullanım yeni tavanın %90'ını `
      + 'aşarsa en eski kayıtlar %80\'e inene dek silinir.');
  }
  return notes.length ? notes.join(' ') : null;
}

// ---------------------------------------------------------------- hata

/**
 * Akademi `403 forbidden` (vekilin değil): KM panel anahtarı bu izinleri (boards.manage, recordings.manage)
 * taşımıyor — anahtar izinleri oluşturulduğu an dondurulur (modül README → Kurulum).
 */
export function keyLacksPermission(error) {
  return Boolean(error && error.code === 'forbidden' && !error.details?.operationId);
}

export const KEY_HINT = 'Akademi bu isteği reddetti: KM panel anahtarı ders kaydı izinlerini (boards.manage, '
  + 'recordings.manage) taşımıyor. Anahtar bu izinler eklenmeden önce üretilmiş. Ayarlar → Entegrasyon '
  + 'anahtarları\'ndan "KM paneli" kümesiyle yeni anahtar oluşturup KM sır kasasına (bbd_akademi.api_key) '
  + 'yazın, sonra eski anahtarı iptal edin.';
