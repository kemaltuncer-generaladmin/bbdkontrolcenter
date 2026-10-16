// BBD Akademi — Kurum finansı raporlarının PDF araç çubuğu (63-finans-pdf).
//
// Kullanıcı (16.10.2026): "finansal raporlarda pdf önizleme ve yazdırma olsun".
// Her finans listesi (cari hesaplar, tahsilatlar, gecikenler / borç
// yaşlandırma, aylık rapor) tek tip araç çubuğu alır: "Önizle" · "Yazdır" ·
// "PDF kaydet" (`documents.js` → `financeToolbar`, ortak `ui-kit/report.js`).
//
// EKRANDAKİ SÜZGEÇ = KÂĞITTAKİ SÜZGEÇ. Rapor ekranda görünen 50 satırı değil,
// süzgece uyan BÜTÜN kayıtları basar (imleçli liste sonuna kadar okunur,
// sayfa başına 100); süzgeçler belgenin başlığında yazar. Liste çok uzunsa
// (`MAX_ROWS`) üretim durur ve süzgeci daraltmak istenir — sunucu boğulmaz (A14).
//
// KANTİNDEN AYRI (A12): burada yalnız kurum carisi vardır.

import { financeToolbar } from './documents.js';
import {
  financeAccountsDoc, financeMonthlyDoc, financeOverdueDoc, financePaymentsDoc,
} from './documents-logic.js';
import { fmtDate } from './common.js';

//: Tek raporda okunacak en çok kayıt (belge sınırı 40.000 tablo satırı).
export const MAX_ROWS = 20_000;
const PAGE = 100;

/** İmleçli listenin süzgece uyan bütün kayıtları. */
export async function fetchAll(app, path, query, { max = MAX_ROWS } = {}) {
  const iterator = app.api.pages(path, query, { limit: PAGE });
  const rows = [];
  while (!iterator.done) {
    // Sayfalar sırayla okunur: imleç bir önceki sayfadan gelir.
    rows.push(...await iterator.next());  // eslint-disable-line no-await-in-loop
    if (rows.length > max) {
      throw new Error(`Liste çok uzun (${max.toLocaleString('tr-TR')} kayıttan fazla). `
        + 'Süzgeci daraltıp yeniden deneyin.');
    }
  }
  return rows;
}

/** Seçim kutusunun ekranda görünen etiketi ("Tüm sınıflar" → boş = Tümü). */
export function chosenLabel(box) {
  const select = box?.node;
  if (!select || !select.value) return '';
  return select.options?.[select.selectedIndex]?.textContent || select.value;
}

const period = (from, to) => (from || to ? `${fmtDate(from)} – ${fmtDate(to)}` : '');

/** Cari hesaplar sekmesi. `state()` → {filters, classBox, stateBox}. */
export function accountsReport(app, state) {
  return financeToolbar(app, { title: 'Cari hesaplar', build: async () => {
    const { filters, classBox, stateBox } = state();
    const items = await fetchAll(app, '/v1/admin/finance/accounts', filters);
    return financeAccountsDoc(items, { filters: [
      ['Arama', filters.q || null], ['Sınıf', chosenLabel(classBox)], ['Durum', chosenLabel(stateBox)],
    ] });
  } });
}

/** Tahsilatlar sekmesi. */
export function paymentsReport(app, state) {
  return financeToolbar(app, { title: 'Tahsilatlar', build: async () => {
    const { filters, methodBox, statusBox } = state();
    const items = await fetchAll(app, '/v1/admin/finance/payments', filters);
    return financePaymentsDoc(items, { filters: [
      ['Tahsilat tarihi', period(filters.from, filters.to)], ['Yöntem', chosenLabel(methodBox)],
      ['Durum', chosenLabel(statusBox)],
    ] });
  } });
}

/** Gecikenler sekmesi — borç yaşlandırma (1–30 / 31–60 / 61–90 / 90+ gün). */
export function overdueReport(app, state) {
  return financeToolbar(app, { title: 'Gecikenler — borç yaşlandırma', build: async () => {
    const { filters, classBox } = state();
    const items = await fetchAll(app, '/v1/admin/finance/overdue', filters);
    return financeOverdueDoc(items, { filters: [
      ['Sınıf', chosenLabel(classBox)], ['Gecikme', `${filters.minDaysOverdue}+ gün`],
      ['Tarih itibarıyla', fmtDate(filters.asOf)],
    ] });
  } });
}

/** Aylık rapor sekmesi (ay × yöntem). Varsayılan yön yatay. */
export function monthlyReport(app, range) {
  return financeToolbar(app, { title: 'Aylık tahsilat raporu', orientation: 'landscape', build: async () => {
    const { start, end } = range.get();
    const report = await app.api.get('/v1/admin/finance/reports/monthly', { from: start, to: end });
    return financeMonthlyDoc(report, { filters: [['Dönem', period(start, end)]] });
  } });
}
