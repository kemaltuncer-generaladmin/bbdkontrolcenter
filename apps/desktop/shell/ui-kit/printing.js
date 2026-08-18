// Yerel yazdırma — kabuğun Rust tarafına açılan ince kapı.
//
// BASKI KULLANICININ MAKİNESİNDE YAPILIR. Çekirdek sunucuda koşuyor (ADR 0026)
// ve sunucu imajı CUPS'ı bilerek kurmuyor ("sunucuda yazıcı ve hoparlör yok" —
// `deploy/server/Dockerfile`); yazıcılar ise kullanıcının masasında. Sunucuya
// "bas" demek, kâğıdın çıkmayacağı bir yere komut göndermekti.
//
// NEDEN `ui-kit` İÇİNDE: hem rapor zinciri (`report.js`) hem Sistem Ayarları
// ekranı kullanıyor. Ayar ekranı `ui-kit` dışından import EDEMEZ (ADR 0011,
// `test_ui_kit_disina_import_yok`), bu yüzden ortak yer burasıdır.
//
// `api` DIŞARIDAN VERİLİR, import edilmez: kit çekirdek istek katmanına
// bağlanmaz — `reportChain` de aynı nedenle onu parametre olarak alıyor.

/** Kabuk yerel yazdırma yapabiliyor mu (Tauri içinde miyiz)? */
export function canPrintLocally() {
  return Boolean(window.__TAURI__?.core?.invoke);
}

function invoker() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) throw new Error('Yazdırma yalnız masaüstü uygulamasında çalışır.');
  return invoke;
}

/** Bu cihazda kurulu yazıcılar + seçili olan. */
export async function localPrinters() {
  if (!canPrintLocally()) {
    return { printers: [], systemDefault: '', selected: '',
      error: 'Yazıcı listesi yalnız masaüstü uygulamasında görünür.' };
  }
  const result = await invoker()('printers');
  return {
    printers: result.printers || [],
    systemDefault: result.system_default || '',
    selected: result.selected || '',
    error: result.error || '',
  };
}

/** Yazıcıyı BU CİHAZ için seçer. Merkezî ayara yazılmaz. */
export async function selectLocalPrinter(name) {
  return invoker()('printer_select', { name: name || '' });
}

/**
 * Sunucuda üretilmiş PDF'i indirip bu cihazın seçili yazıcısına basar.
 *
 * İki adım tek yerde: baytları al, yerel kuyruğa ver. Her rapor ekranının bu
 * sırayı yeniden yazması, birinin adımı atlaması demekti.
 */
export async function printDocument(api, path, { copies = 1 } = {}) {
  const invoke = invoker();
  const doc = await api('/api/outputs/document', { method: 'POST', body: { path } });
  if (!doc?.data) throw new Error('Belge alınamadı.');
  return invoke('printer_print', { data: doc.data, name: doc.name || 'belge.pdf', copies });
}
