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
 *
 * YAZDIRMA PENCERESİ (finansal raporlar — `report.js` → `printDialog`):
 * `printer` bu cihazda kurulu bir yazıcıyı seçer (boşsa yukarıdaki kural),
 * `pages` sayfa aralığıdır ("1-3, 5"; boş = bütün sayfalar), `copies` 1–20.
 * Aralık kabukta yeniden doğrulanır; komuta serbest metin gitmez.
 */
export async function printDocument(api, path, { copies = 1, printer = '', pages = '' } = {}) {
  const invoke = invoker();
  if (!path) throw new Error('Basılacak dosya belirtilmedi.');
  const problem = pageRangeError(pages);
  if (problem) throw new Error(problem);
  const doc = await api('/api/outputs/document', { method: 'POST', body: { path } });
  if (!doc?.data) throw new Error('Belge alınamadı.');
  return invoke('printer_print', {
    data: doc.data, name: doc.name || 'belge.pdf',
    copies: Math.max(1, Math.min(20, Number(copies) || 1)),
    printer: printer || null, pages: String(pages || '').trim() || null,
  });
}

/**
 * Sayfa aralığı metni geçerli mi? Geçerliyse boş metin, değilse sebep.
 * Kural `printing.rs` → `page_ranges` ile aynı: "1-3, 5"; 1 ≤ a ≤ b ≤ 9999.
 */
export function pageRangeError(text) {
  const compact = String(text || '').replace(/\s+/g, '');
  if (!compact) return '';
  const message = `Sayfa aralığı anlaşılamadı: “${String(text).trim()}”. Örnek: 1-3, 5 (boş bırakılırsa bütün sayfalar).`;
  const parts = compact.split(',').filter(Boolean);
  if (!parts.length || parts.length > 50) return message;
  for (const part of parts) {
    const match = /^(\d{1,4})(?:-(\d{1,4}))?$/.exec(part);
    if (!match) return message;
    const start = Number(match[1]);
    const end = match[2] === undefined ? start : Number(match[2]);
    if (start < 1 || end < start) return message;
  }
  return '';
}

/** Önerilen dosya adı: yol ayracı atılır, `.pdf` garanti edilir, Türkçe harf kalır. */
export function pdfFileName(name) {
  let base = String(name || '').replace(/[\\/:*?"<>|\u0000-\u001f]/g, '-').trim().replace(/^\.+|\.+$/g, '');
  if (!base) base = 'rapor';
  base = base.slice(0, 120);
  return /\.pdf$/i.test(base) ? base : `${base}.pdf`;
}

/**
 * Sunucuda üretilmiş PDF'i BU CİHAZA kaydeder.
 *
 * Masaüstünde işletim sisteminin kaydet penceresi açılır (`printing.rs` →
 * `pdf_save`); vazgeçilirse `{cancelled: true}`. Tarayıcıda (web) dosya
 * indirilir. Dönen `path` kaydedilen yerdir — kullanıcıya söylenir ki
 * dosyayı aramak zorunda kalmasın.
 *
 * @returns {Promise<{path:string, cancelled:boolean, name:string}>}
 */
export async function savePdf(api, path, name = '') {
  if (!path) throw new Error('Kaydedilecek dosya belirtilmedi.');
  const doc = await api('/api/outputs/document', { method: 'POST', body: { path } });
  if (!doc?.data) throw new Error('Belge alınamadı.');
  const fileName = pdfFileName(name || doc.name || 'rapor.pdf');
  if (canPrintLocally()) {
    const saved = await invoker()('pdf_save', { data: doc.data, name: fileName });
    return { path: saved || '', cancelled: !saved, name: fileName };
  }
  const binary = atob(doc.data);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  const url = URL.createObjectURL(new Blob([bytes], { type: 'application/pdf' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
  return { path: '', cancelled: false, name: fileName };
}
