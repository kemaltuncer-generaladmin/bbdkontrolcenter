// net.js — API yanıt önbelleği ve bağlantı hatası sınıflaması (ADR 0032).
//
// DOM'A DOKUNMAZ; saf mantıktır ve Node'da sınanır
// (`tests/integration/test_kabuk_native_his.py`).
//
// ÖNBELLEK NE İÇİN VAR, NE İÇİN YOK.
//  · VAR: gezinmede "geri dönünce anında" — bir ekrana dönüldüğünde açılış
//    istekleri son bilinen yanıtla HEMEN cevaplanır, arkada tazelenir
//    (stale-while-revalidate). Bağlantı koptuğunda okumalar son bilinen
//    yanıttan gelir; uygulama durmaz.
//  · YOK: canlı veri. Önbellek yalnız bir ekranın AÇILIŞ penceresindeki
//    okumalara cevap verir (`ui-kernel.js` → mount kapsamı). Yoklama
//    (`pollLoop`), "Yenile" düğmesi ve açılıştan sonraki her okuma ağa gider;
//    merkezdeki değişikliğin 1–3 sn'de ekrana ulaşma sözü bozulmaz.
//  · YAZMA (GET dışı her istek) önbelleğin TAMAMINI boşaltır. "Hangi liste
//    bu yazmadan etkilendi" sorusunu kabuk bilemez (K1 — modül adı yok);
//    tamamını atmak ucuz ve her zaman doğrudur.
//
// ÖNBELLEK BELLEKTEDİR, diske yazılmaz ve oturum kapanınca boşaltılır:
// öğrenci/veli verisi makinede iz bırakmaz, sonraki kişi öncekinin verisini
// görmez.

/** Açılış penceresinde son bilinen yanıtın en fazla yaşı. Daha eskisi beklenir. */
export const SWR_MAX_AGE_MS = 10 * 60 * 1000;

/** Bir ekranın "açılış penceresi" — bu süre içindeki okumalar önbellekten cevaplanabilir. */
export const MOUNT_WINDOW_MS = 2000;

/**
 * ÖNBELLEĞE GİRMEYEN yollar. Kimlik/eşleme uçları oturumun kendisidir;
 * `/health` bağlantı yoklamasıdır (önbellekten cevap, kopmuş bağlantıyı
 * sağlam gösterirdi); uzun işler ve dosya üreten uçlar her seferinde
 * yeniden üretilmelidir (ADR 0011 adlandırma sözleşmesi).
 */
const NEVER_CACHE = [
  /^\/health(?:[/?]|$)/,
  /^\/modules(?:[/?]|$)/,
  /^\/api\/auth\//,
  /^\/api\/pairing\//,
  /\/(?:export|preview|print|printer|run|download|stream|events)(?:[/?]|$)/,
];

/**
 * Yazmada yeniden deneme penceresi ÇIKMAYAN yollar: giriş ve eşleme ekranı
 * kendi hata cümlesini kurar; çıkış ağ yokken de yerelde tamamlanır.
 */
const OWN_ERROR_UI = [/^\/api\/auth\//, /^\/api\/pairing\//];

export function cacheable(method, path) {
  if (String(method || 'GET').toUpperCase() !== 'GET') return false;
  const clean = String(path || '');
  return !NEVER_CACHE.some((rule) => rule.test(clean));
}

export function ownsErrorUi(path) {
  return OWN_ERROR_UI.some((rule) => rule.test(String(path || '')));
}

/**
 * Bağlantı hatasının türü ya da `null` (bağlantı hatası değil).
 *
 * Rust köprüsü (`core_request`) hatayı `[connect]` / `[timeout]` / `[network]`
 * önekiyle döndürür. Tarayıcı yolunda `fetch` ağ hatasında `TypeError`,
 * zaman aşımında `AbortError` atar.
 *
 *  · `connect` — istek sunucuya HİÇ ULAŞMADI; yazma güvenle yeniden denenir.
 *  · `timeout` / `network` — istek gitmiş olabilir; yazma KÖR yeniden
 *    denenmez (para, yoklama: çift kayıt).
 */
export function failureKind(error) {
  if (!error) return null;
  const text = String(error.message || error || '');
  const tagged = /^\[(connect|timeout|network)\]/.exec(text);
  if (tagged) return tagged[1];
  if (error.name === 'AbortError') return 'timeout';
  if (error.name === 'TypeError' && /fetch|network|load failed/i.test(text)) return 'connect';
  return null;
}

/**
 * Vekil (Traefik/Cloudflare) "arka uca ulaşılamadı" yanıtları. Okumada
 * bağlantı kopukluğu gibi davranılır (son bilinen yanıt); yazmada belirsizdir
 * (504'te iş sunucuda bitmiş olabilir) ve kör yeniden denenmez.
 */
const GATEWAY = new Set([502, 503, 504, 520, 521, 522, 523, 524]);

export function isGateway(status) {
  return GATEWAY.has(Number(status));
}

/** Kullanıcıya gösterilecek, ne olduğunu ve ne YAPILACAĞINI söyleyen cümleler. */
export const MESSAGES = {
  readOffline: 'Bağlantı yok — bu ekranın verisi daha önce yüklenmediği için gösterilemiyor. '
    + 'Bağlantı gelince kendiliğinden tazelenir.',
  writeNotSent: 'Bağlantı yok — işlem YAPILMADI. Bağlantı gelince yeniden deneyin.',
  writeUnknown: 'Sunucudan yanıt alınamadı — işlem kaydedilmiş OLABİLİR. Yeniden denemeden önce '
    + 'ekranı yenileyip (Ctrl+R) kaydın oluşup oluşmadığına bakın.',
};

/** Ağ hatasını panelin göstereceği açık bir hataya çevirir. */
export function offlineError(kind, mode) {
  const text = mode === 'read' ? MESSAGES.readOffline
    : mode === 'write-not-sent' ? MESSAGES.writeNotSent
      : MESSAGES.writeUnknown;
  const error = new Error(text);
  error.offline = true;
  error.kind = kind;
  error.status = 0;
  return error;
}

/**
 * Bellekteki yanıt önbelleği (LRU). Değer HAM METİNDİR: her okuyucu kendi
 * `JSON.parse` kopyasını alır, bir panelin nesneyi değiştirmesi ötekine
 * sızmaz.
 */
export function createResponseCache({ maxEntries = 400, maxChars = 16_000_000, now = () => Date.now() } = {}) {
  const map = new Map();
  let chars = 0;

  function drop(key) {
    const entry = map.get(key);
    if (!entry) return;
    chars -= entry.text.length;
    map.delete(key);
  }

  return {
    get(key) {
      const entry = map.get(key);
      if (!entry) return null;
      // LRU: okunan sona taşınır.
      map.delete(key);
      map.set(key, entry);
      return { text: entry.text, age: now() - entry.at };
    },
    set(key, text) {
      const value = String(text ?? '');
      // Tek başına bütçenin dörtte birini aşan yanıt saklanmaz: bir rapor
      // dökümü bütün önbelleği silip atardı.
      if (value.length > maxChars / 4) {
        drop(key);
        return;
      }
      drop(key);
      map.set(key, { text: value, at: now() });
      chars += value.length;
      while (map.size > maxEntries || chars > maxChars) {
        const oldest = map.keys().next().value;
        if (oldest === undefined) break;
        drop(oldest);
      }
    },
    clear() {
      map.clear();
      chars = 0;
    },
    get size() { return map.size; },
    get chars() { return chars; },
  };
}
