// Veri yardımcıları — toplulaştırma ve gruplama.
//
// Tek kopya (ADR 0011). Buradaki hiçbir şey DOM bilmez; saf fonksiyonlardır ve
// ayrı test edilebilirler. 20 ekranın hepsi aynı işleri yapıyor: listeyi
// gruplama, toplam alma, en çok N'i çıkarma, iki dönemi karşılaştırma.

/** `groupBy(rows, r => r.status)` → Map<anahtar, satırlar>. Sıra korunur. */
export function groupBy(rows, key) {
  const pick = typeof key === 'function' ? key : (row) => row[key];
  const map = new Map();
  for (const row of rows || []) {
    const name = pick(row);
    if (!map.has(name)) map.set(name, []);
    map.get(name).push(row);
  }
  return map;
}

/** Sayısal toplam. Eksik/bozuk değer 0 sayılır — tablo patlamaz. */
export function sum(rows, key) {
  const pick = typeof key === 'function' ? key : (row) => row[key];
  return (rows || []).reduce((total, row) => total + (Number(pick(row)) || 0), 0);
}

/** Ortalama; liste boşsa 0 (NaN döndürmek ekranda "—" bile göstermez). */
export function average(rows, key) {
  const list = rows || [];
  return list.length === 0 ? 0 : sum(list, key) / list.length;
}

/** Türkçe sıralama. `dir`: 'asc' | 'desc'. Sayılar sayısal karşılaştırılır. */
export function sortBy(rows, key, dir = 'asc') {
  const pick = typeof key === 'function' ? key : (row) => row[key];
  const factor = dir === 'asc' ? 1 : -1;
  return [...(rows || [])].sort((a, b) => {
    const left = pick(a);
    const right = pick(b);
    if (typeof left === 'number' || typeof right === 'number') {
      return ((Number(left) || 0) - (Number(right) || 0)) * factor;
    }
    return String(left ?? '').localeCompare(String(right ?? ''), 'tr') * factor;
  });
}

/** İlk görüleni tutarak tekilleştirir. */
export function uniqueBy(rows, key) {
  const pick = typeof key === 'function' ? key : (row) => row[key];
  const seen = new Set();
  const out = [];
  for (const row of rows || []) {
    const id = pick(row);
    if (seen.has(id)) continue;
    seen.add(id);
    out.push(row);
  }
  return out;
}

/** En büyük N kayıt (grafiklerde "ilk 10"). */
export function topN(rows, key, count = 10) {
  return sortBy(rows, key, 'desc').slice(0, count);
}

/**
 * İki dönem karşılaştırması → {percent, direction, delta}.
 *
 * Önceki dönem 0 iken yüzde TANIMSIZDIR ve `null` döner: "%∞ artış" yazmak
 * bilgi değil gürültüdür. Ekran o durumda yalnız yeni değeri gösterir.
 */
export function compare(current, previous) {
  const now = Number(current) || 0;
  const before = Number(previous) || 0;
  const delta = now - before;
  if (before === 0) return { percent: null, direction: delta > 0 ? 'up' : 'flat', delta };
  const percent = Math.round((delta / before) * 1000) / 10;
  return { percent, direction: delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat', delta };
}

/**
 * Zaman serisini gün gün doldurur — VERİSİ OLMAYAN GÜNLER 0 OLARAK GİRER.
 *
 * Eksik günü atlamak grafiği yalan söyletir: 3 günlük boşluk düz bir çizgi
 * gibi görünür, oysa satış yoktur. Düşük hacimli bir mağazada bu fark önemli.
 *
 * @param {Array<{date:string, value:number}>} rows — `date` ISO gün
 */
export function fillDays(rows, startIso, endIso) {
  const map = new Map((rows || []).map((row) => [String(row.date).slice(0, 10), Number(row.value) || 0]));
  const out = [];
  const cursor = new Date(`${startIso}T00:00:00`);
  const last = new Date(`${endIso}T00:00:00`);
  const pad = (value) => String(value).padStart(2, '0');

  while (cursor <= last) {
    const iso = `${cursor.getFullYear()}-${pad(cursor.getMonth() + 1)}-${pad(cursor.getDate())}`;
    out.push({
      date: iso,
      label: `${pad(cursor.getDate())}.${pad(cursor.getMonth() + 1)}`,
      value: map.get(iso) || 0,
    });
    cursor.setDate(cursor.getDate() + 1);
    if (out.length > 800) break;   // güvenlik: bozuk aralık sonsuz döngü yapmasın
  }
  return out;
}

/**
 * Pareto (ABC) sınıflandırması: kümülatif payı %80'e kadar A, %95'e kadar B,
 * gerisi C. Ürün analizinde "hangi 20 ürün cironun %80'ini yapıyor" sorusu.
 */
export function abcClassify(rows, key) {
  const pick = typeof key === 'function' ? key : (row) => row[key];
  const sorted = sortBy(rows, pick, 'desc');
  const total = sum(sorted, pick);
  let cumulative = 0;
  return sorted.map((row) => {
    const value = Number(pick(row)) || 0;
    cumulative += value;
    const share = total === 0 ? 0 : (value / total) * 100;
    const cumulativeShare = total === 0 ? 0 : (cumulative / total) * 100;
    return {
      ...row,
      share: Math.round(share * 10) / 10,
      cumulativeShare: Math.round(cumulativeShare * 10) / 10,
      abc: cumulativeShare <= 80 ? 'A' : cumulativeShare <= 95 ? 'B' : 'C',
    };
  });
}
