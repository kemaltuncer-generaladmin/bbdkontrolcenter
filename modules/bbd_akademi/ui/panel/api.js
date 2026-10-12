// BBD Akademi — panelin Akademi istemcisi.
//
// NEDEN KÖPRÜ. Kabuğun istek kanalı (`ctx.api`) yalnız JSON metin taşır, özel
// başlık geçiremez ve hata yanıtında yalnız `message` bırakır. Akademi'nin
// hata kodu (`timetable_conflict`, `validation_error` alanları …), dosya
// yükleme/indirme ve `Idempotency-Key` bu yüzden modülün köprüsünden geçer:
// `POST /api/bbd_akademi/km/call` → `{ok, status, data, error, headers, file?}`.
// Kapılar (izin listesi + KM izni) köprüde de AYNIDIR; karar backend'dedir (K9).
//
// HIZ (Akademi PERFORMANCE.md):
//   · seyrek değişen okumalar (katalog, şablon, ayar) ETag ile önbelleğe alınır;
//     `getCached` önce bellekteki değeri verir, arkadan `If-None-Match` sorar
//   · aynı GET aynı anda iki kez istenirse tek istek gider (uçuştaki istek paylaşılır)
//   · liste uçları imleçlidir (`pages()`), satır başına ek istek atılmaz
//
// Hata: `AkademiError` — {code, message, details, status, traceId}. `message`
// Akademi'den Türkçe ve kullanıcıya gösterilebilir gelir; bilinen kodlar için
// `problem()` daha açıklayıcı bir cümle kurar.

const BASE = '/api/bbd_akademi';

export class AkademiError extends Error {
  constructor({ code = 'error', message = 'İstek başarısız.', details = null, status = 0,
    traceId = null } = {}) {
    super(message);
    this.name = 'AkademiError';
    this.code = code;
    this.details = details;
    this.status = status;
    this.traceId = traceId;
  }
}

/** Kod → kullanıcı cümlesi. Bilinmeyen kodda Akademi'nin kendi mesajı kalır. */
const CODE_TEXT = {
  akademi_unavailable: 'Akademi sunucusuna şu an ulaşılamıyor. Biraz sonra tekrar deneyin.',
  akademi_not_configured: 'Akademi bağlantısı kurulmamış: entegrasyon anahtarı sır kasasında yok.',
  akademi_disabled: 'Akademi bağlantısı ayarlardan kapatılmış.',
  operation_not_allowed: 'Bu işlem Kontrol Merkezi üzerinden açık değil.',
  forbidden: 'Bu işlem için yetkiniz yok.',
  feature_disabled: 'Bu özellik kurum için kapalı (Ayarlar → Özellikler).',
  step_up_required: 'Bu işlem için PIN teyidi gerekiyor.',
  rate_limited: 'Çok sık istek gönderildi; birkaç saniye bekleyip tekrar deneyin.',
  idempotency_conflict: 'Aynı işlem farklı bilgilerle tekrar gönderildi. Formu kapatıp yeniden açın.',
  version_conflict: 'Kayıt siz düzenlerken değişti. Ekranı yenileyip tekrar deneyin.',
  payload_too_large: 'Dosya izin verilen boyutu aşıyor.',
  invalid_integration_key: 'Akademi entegrasyon anahtarını reddetti (iptal edilmiş olabilir).',
};

export function problem(error, fallback = 'İşlem tamamlanamadı.') {
  if (!error) return fallback;
  if (error instanceof AkademiError) {
    if (error.code === 'validation_error' && Array.isArray(error.details) && error.details.length) {
      return error.details.map((item) => item?.message || item?.field).filter(Boolean).join(' · ')
        || error.message;
    }
    return CODE_TEXT[error.code] && !['forbidden'].includes(error.code)
      ? CODE_TEXT[error.code]
      : (error.message || CODE_TEXT[error.code] || fallback);
  }
  return error.message || fallback;
}

/** Tarayıcıda tekil anahtar. `crypto.randomUUID` yoksa (eski WebKit) zaman+rastgele. */
export function newKey(prefix = 'km') {
  const random = globalThis.crypto?.randomUUID
    ? globalThis.crypto.randomUUID()
    : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}-${random}`.slice(0, 120);
}

function queryKey(path, query) {
  const entries = Object.entries(query || {})
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .sort(([a], [b]) => a.localeCompare(b));
  return `${path}?${JSON.stringify(entries)}`;
}

/**
 * @param {object} ctx — panelin `mount(root, ctx)` bağlamı
 */
export function createClient(ctx) {
  const etags = new Map();      // anahtar → {etag, data, at}
  const inflight = new Map();   // anahtar → Promise
  let operations = null;        // operationId → {allowed, stepUp, …}

  async function raw(payload) {
    let envelope;
    try {
      envelope = await ctx.api(`${BASE}/km/call`, { method: 'POST', body: payload,
        timeoutSeconds: payload.upload ? 300 : undefined });
    } catch (failure) {
      // Köprünün KENDİSİNE ulaşılamadı (KM oturumu düştü, ağ yok) ya da taban
      // izin yok: kabuk zarfı yalnız `message` taşır.
      throw new AkademiError({
        code: failure?.status === 401 ? 'session_expired'
          : failure?.status === 403 ? 'forbidden' : 'km_unavailable',
        message: failure?.message || 'Kontrol Merkezi sunucusuna ulaşılamadı.',
        status: failure?.status || 0,
      });
    }
    if (!envelope || typeof envelope !== 'object') {
      throw new AkademiError({ message: 'Beklenmeyen yanıt.' });
    }
    if (!envelope.ok) throw new AkademiError({ ...(envelope.error || {}), status: envelope.status });
    return envelope;
  }

  /**
   * Akademi işlemi. `options`:
   *   query       {k: v | v[]}
   *   body        JSON gövde (GET'te gönderilmez)
   *   key         Idempotency-Key — verilmezse POST'ta kendiliğinden üretilir.
   *               Form/pencere açıkken AYNI anahtar tekrar kullanılmalı (çift
   *               tıklama ve yeniden deneme ikinci kayıt açmasın).
   *   stepUpToken PIN teyidi belirteci (`stepUp()`)
   *   upload      {file: File, field?, fields?, onProgress?}
   * Dönüş: {data, headers, file?, status}
   */
  async function call(method, path, options = {}) {
    const payload = { method, path };
    if (options.query) payload.query = options.query;
    if (options.body !== undefined && method !== 'GET') payload.body = options.body;
    const key = options.key || (method === 'POST' ? newKey() : null);
    if (key) payload.idempotencyKey = key;
    if (options.stepUpToken) payload.stepUpToken = options.stepUpToken;
    if (options.ifNoneMatch) payload.ifNoneMatch = options.ifNoneMatch;
    if (options.upload) {
      const { file, field = 'file', fields = {}, onProgress } = options.upload;
      payload.upload = {
        field,
        filename: file.name || 'dosya',
        contentType: file.type || guessType(file.name),
        dataBase64: await readBase64(file, onProgress),
        fields,
      };
    }
    const envelope = await raw(payload);
    return { data: envelope.data, headers: envelope.headers || {}, file: envelope.file || null,
      status: envelope.status };
  }

  async function get(path, query) {
    const id = queryKey(path, query);
    if (inflight.has(id)) return inflight.get(id);
    const run = call('GET', path, { query }).then((result) => result.data)
      .finally(() => inflight.delete(id));
    inflight.set(id, run);
    return run;
  }

  /**
   * ETag'li okuma. Taze kayıt (`ttlMs` içinde) ağa hiç çıkmadan döner; eskiyse
   * `If-None-Match` ile sorulur, 304'te bellekteki değer kullanılır.
   */
  async function getCached(path, query, { ttlMs = 60_000 } = {}) {
    const id = queryKey(path, query);
    const cached = etags.get(id);
    if (cached && Date.now() - cached.at < ttlMs) return cached.data;
    if (inflight.has(id)) return inflight.get(id);
    const run = (async () => {
      const result = await call('GET', path, { query, ifNoneMatch: cached?.etag });
      if (result.status === 304 && cached) {
        cached.at = Date.now();
        return cached.data;
      }
      etags.set(id, { etag: result.headers.etag || null, data: result.data, at: Date.now() });
      return result.data;
    })().finally(() => inflight.delete(id));
    inflight.set(id, run);
    return run;
  }

  /** Yazmadan sonra ilgili önbelleği düşür (`prefix` ile başlayan her yol). */
  function invalidate(prefix) {
    for (const id of [...etags.keys()]) if (id.startsWith(prefix)) etags.delete(id);
  }

  /**
   * İmleçli liste: `{items, nextCursor}`. Her `next()` bir sayfa getirir;
   * `done` son sayfadan sonra true olur.
   */
  function pages(path, query = {}, { limit = 50 } = {}) {
    let cursor = null;
    let done = false;
    return {
      get done() { return done; },
      async next() {
        if (done) return [];
        const data = await get(path, { ...query, limit, cursor: cursor || undefined });
        cursor = data?.nextCursor || null;
        done = !cursor;
        return Array.isArray(data?.items) ? data.items : [];
      },
    };
  }

  /** Tüm sayfalar (küçük, sınırlı kümeler için; ör. sınıf listesi). */
  async function all(path, query = {}, { limit = 100, max = 2000 } = {}) {
    const iterator = pages(path, query, { limit });
    const rows = [];
    while (!iterator.done && rows.length < max) {
      rows.push(...await iterator.next());   // eslint-disable-line no-await-in-loop
    }
    return rows;
  }

  /** PIN teyidi: tek kullanımlık belirteç döner (yanlış PIN → hata). */
  async function stepUp(operationId, pin) {
    try {
      const result = await ctx.api(`${BASE}/km/step-up`, { method: 'POST',
        body: { pin, operationId } });
      return result.stepUpToken;
    } catch (failure) {
      throw new AkademiError({ code: failure?.status === 403 ? 'pin_rejected' : 'step_up_failed',
        message: failure?.message || 'PIN doğrulanamadı.', status: failure?.status || 0 });
    }
  }

  /** İmzalı bağlantıdaki dosyayı (PDF/XLSX) indirir: {filename, blob}. */
  async function fetchFile(url, fallbackName = 'akademi-dosya') {
    let envelope;
    try {
      envelope = await ctx.api(`${BASE}/km/fetch`, { method: 'POST', body: { url },
        timeoutSeconds: 300 });
    } catch (failure) {
      throw new AkademiError({ message: failure?.message || 'Dosya indirilemedi.' });
    }
    if (!envelope?.ok || !envelope.file) {
      throw new AkademiError({ ...(envelope?.error || { message: 'Dosya indirilemedi.' }) });
    }
    return fileOf(envelope.file, fallbackName);
  }

  /** Bu kullanıcı işlemi yapabilir mi? İzinler okunamadıysa karar backend'e kalır. */
  async function loadOperations() {
    try {
      const result = await ctx.api(`${BASE}/km/operations`);
      operations = new Map((result?.items || []).map((item) => [item.operationId, item]));
    } catch {
      operations = null;
    }
    return operations;
  }

  function can(operationId) {
    if (!operations) return true;
    return Boolean(operations.get(operationId)?.allowed);
  }

  function operation(operationId) {
    return operations?.get(operationId) || null;
  }

  return {
    call,
    get,
    getCached,
    invalidate,
    pages,
    all,
    post: (path, body, options = {}) => call('POST', path, { ...options, body }).then((r) => r.data),
    put: (path, body, options = {}) => call('PUT', path, { ...options, body }).then((r) => r.data),
    patch: (path, body, options = {}) => call('PATCH', path, { ...options, body }).then((r) => r.data),
    del: (path, options = {}) => call('DELETE', path, options).then((r) => r.data),
    stepUp,
    fetchFile,
    loadOperations,
    can,
    operation,
    /** Modülün kendi uçları (`/api/bbd_akademi/km/...`) — kabuk kanalıyla. */
    km: (path, options) => ctx.api(`${BASE}/km${path}`, options),
  };
}

// ----------------------------------------------------------------- dosya

const TYPES = {
  csv: 'text/csv', xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xls: 'application/vnd.ms-excel', png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
  svg: 'image/svg+xml', webp: 'image/webp', pdf: 'application/pdf', txt: 'text/plain',
};

export function guessType(name) {
  const ext = String(name || '').split('.').pop().toLowerCase();
  return TYPES[ext] || 'application/octet-stream';
}

/** Dosyayı base64'e çevirir; `onProgress(0..100)` okuma ilerlemesini verir. */
export function readBase64(file, onProgress) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100));
    };
    reader.onerror = () => reject(new AkademiError({ code: 'upload_invalid',
      message: 'Dosya okunamadı.' }));
    reader.onload = () => {
      onProgress?.(100);
      const text = String(reader.result || '');
      resolve(text.includes(',') ? text.split(',')[1] : text);
    };
    reader.readAsDataURL(file);
  });
}

/** Köprüden gelen `{filename, contentType, dataBase64}` → {filename, blob}. */
export function fileOf(file, fallbackName = 'akademi-dosya') {
  const binary = atob(file.dataBase64 || '');
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return { filename: file.filename || fallbackName,
    blob: new Blob([bytes], { type: file.contentType || 'application/octet-stream' }) };
}

/** Blob'u kullanıcının diskine indirir (`<a download>`). */
export function saveBlob({ filename, blob }) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

/** PDF'i yeni pencerede açar (yazdırma için); açılamazsa indirir. */
export function openBlob({ filename, blob }) {
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, '_blank');
  if (!opened) saveBlob({ filename, blob });
  setTimeout(() => URL.revokeObjectURL(url), 120_000);
}
