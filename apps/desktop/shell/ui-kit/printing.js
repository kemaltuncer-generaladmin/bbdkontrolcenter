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

/** Bu cihazda kurulu yazıcılar + seçili olan + baskının gideceği kuyruk. */
export async function localPrinters() {
  if (!canPrintLocally()) {
    return { printers: [], systemDefault: '', selected: '', effective: '',
      blocked: 'Yazdırma yalnız masaüstü uygulamasında çalışır.',
      error: 'Yazıcı listesi yalnız masaüstü uygulamasında görünür.' };
  }
  const result = await invoker()('printers');
  return {
    printers: result.printers || [],
    systemDefault: result.system_default || '',
    selected: result.selected || '',
    // Kâğıdın çıkacağı yer: seçim varsa o, yoksa sistemin varsayılanı.
    effective: result.effective || '',
    blocked: result.blocked || '',
    error: result.error || '',
  };
}

/**
 * "Şimdi bassak çıkar mı?" — BÜTÜN yazdır düğmelerinin sorduğu tek soru.
 *
 * Her ekran bunu kendi kurmasın: sıra (masaüstünde miyiz → yazıcı var mı →
 * hangisi) bir yerde dursun ki bir ekran adımı atlayıp "gönderildi" derken
 * kâğıt çıkmamasın. Hata FIRLATMAZ, anlatır — düğme kapatılacak ve yanına
 * sebep yazılacak.
 *
 * @returns {Promise<{ready:boolean, name:string, error:string}>}
 */
export async function printerReady() {
  if (!canPrintLocally()) {
    return { ready: false, name: '',
      error: 'Yazdırma yalnız masaüstü uygulamasında çalışır.' };
  }
  let local;
  try {
    local = await localPrinters();
  } catch (error) {
    return { ready: false, name: '', error: error.message };
  }
  if (local.effective) return { ready: true, name: local.effective, error: '' };
  // `blocked` neden basılamadığını söyler (seçim yok + varsayılan yok, ayardaki
  // yazıcı silinmiş…). `error` yalnız liste hiç alınamadığında doludur.
  return { ready: false, name: '',
    error: local.blocked || local.error || 'Bu cihazda yazıcı bulunamadı.' };
}

/** Yazıcıyı BU CİHAZ için seçer. Merkezî ayara yazılmaz. */
export async function selectLocalPrinter(name) {
  return invoker()('printer_select', { name: name || '' });
}

/**
 * Sunucuda üretilmiş PDF'i indirip bu cihazın yazıcısına basar.
 * Basılan yazıcının adını döndürür.
 *
 * İki adım tek yerde: baytları al, yerel kuyruğa ver. Her rapor ekranının bu
 * sırayı yeniden yazması, birinin adımı atlaması demekti.
 *
 * HEDEFİ KENDİSİ ÇÖZER: ayardan seçilen yazıcı varsa oraya, yoksa sistemin
 * varsayılanına basar (`printing.rs` → `resolve`). Çağıranın yazıcı adı
 * vermesine gerek yoktur ve YAZICI SEÇME PENCERESİ AÇILMAZ. Basılabilecek
 * yazıcı yoksa sebebini söyleyen bir hata fırlar; `printerReady` ile önceden
 * sorup düğmeyi kapatmak, kullanıcıyı tıklayıp hata almaktan kurtarır.
 */
export async function printDocument(api, path, { copies = 1 } = {}) {
  const invoke = invoker();
  if (!path) throw new Error('Basılacak dosya belirtilmedi.');
  const doc = await api('/api/outputs/document', { method: 'POST', body: { path } });
  if (!doc?.data) throw new Error('Belge alınamadı.');
  return invoke('printer_print', { data: doc.data, name: doc.name || 'belge.pdf', copies });
}
