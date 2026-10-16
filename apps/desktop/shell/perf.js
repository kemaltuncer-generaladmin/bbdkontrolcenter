// perf.js — hız günlüğü (ADR 0032 §Hız): soğuk açılış, ekran değişimi, geri dönüş.
//
// Ölçüler `performance.now()` ile alınır, konsola `[hız]` önekiyle yazılır ve
// Yardım → Hız ölçümleri penceresinde görünür (`window.__kmPerf` da aynısı).
// Ağa hiçbir şey gönderilmez.
//
//  · `boot`     — betik başlangıcı (boot.js), giriş ekranı boyandı, çekirdek
//                 hazır, çalışma alanı açıldı.
//  · `panel`    — ekran seçildi → ekran OTURDU (mount döndü + açılış
//                 istekleri bitti + bir boyama). `return: true` daha önce
//                 açılmış bir ekrana dönüştür; `cache` açılışta önbellekten
//                 cevaplanan istek sayısıdır.

const MAX = 200;
const entries = [];
const marks = new Map();

function log(entry) {
  entries.push(entry);
  if (entries.length > MAX) entries.shift();
  const detail = Object.entries(entry)
    .filter(([key]) => !['kind', 'at', 'ms'].includes(key))
    .map(([key, value]) => `${key}=${value}`)
    .join(' ');
  console.info(`[hız] ${entry.kind} ${Math.round(entry.ms)} ms ${detail}`.trim());
}

export const perf = {
  /** Açılıştan bu yana geçen süreyle bir işaret koyar. */
  mark(name) {
    const ms = performance.now();
    marks.set(name, ms);
    log({ kind: 'boot', step: name, ms, at: Date.now() });
    return ms;
  },
  markTime(name) {
    return marks.get(name);
  },
  record(kind, ms, data = {}) {
    log({ kind, ms, ...data, at: Date.now() });
  },
  entries() {
    return entries.slice();
  },
  /** Ekran ölçümlerinin özeti: ilk açılış ve geri dönüş ortancası. */
  summary() {
    const panels = entries.filter((entry) => entry.kind === 'panel');
    const median = (list) => {
      if (!list.length) return null;
      const sorted = list.map((entry) => entry.ms).sort((a, b) => a - b);
      return Math.round(sorted[Math.floor(sorted.length / 2)]);
    };
    return {
      firstOpen: median(panels.filter((entry) => !entry.return)),
      returnVisit: median(panels.filter((entry) => entry.return)),
      count: panels.length,
      boot: Object.fromEntries([...marks.entries()].map(([key, value]) => [key, Math.round(value)])),
    };
  },
};

if (typeof window !== 'undefined') window.__kmPerf = perf;
