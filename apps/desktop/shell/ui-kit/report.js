// Rapor zinciri — üret → önizle → yazdır.
//
// Tek kopya (ADR 0011). Akış üç uca dayanır ve her rapor üreten modül bu üçünü
// aynı adlarla açar:
//
//   POST <base>/preview  {kind, ...params}
//        → {ok, path, name, bytes, pages: ["data:image/png;base64,…"]}
//   POST <base>/print    {path, copies}
//        → {ok, printer} | {ok:false, error}
//   GET  <base>/printer
//        → {ready, error, target:{name}, device:{tonerPercent}}
//
// NEDEN SUNUCU ÜRETİYOR: kalıcı çıktı masaüstündeki
// `Kontrol Merkezi/Raporlar/…` klasörüne 0600 izinle yazılmalı ve CUPS'a
// gönderilmeli. Tauri'de dosya sistemi ve yazdırma eklentisi yok; tarayıcı
// tarafı bunu yapamaz. Panel yalnız yolu gösterir ve sayfaları önizler.
//
// ÖNİZLEME GERÇEK PDF'İN KENDİSİDİR: sunucu `pdftoppm` ile sayfaları
// görüntüye çevirir. Ekranda gördüğünüz ile yazıcıdan çıkan aynıdır.

import { button, bytes as formatBytes, h } from './kit.js';
// BASKI CİHAZDA YAPILIR — gerekçe `printing.js` başlığında.
import { canPrintLocally, localPrinters, printDocument } from './printing.js';

/**
 * @param {object} spec
 * @param {(path:string, options?:object)=>Promise<any>} spec.api
 * @param {HTMLElement} spec.root — overlay'in ekleneceği PANEL KÖKÜ
 * @param {(message:string, tone?:string)=>void} spec.toast
 * @param {string} spec.base — modülün uç öneki, ör. '/api/store_reports'
 */
//: Rapor üretiminin zaman aşımı (saniye). Mağazanın tam taraması + PDF
//: üretimi birkaç dakikayı bulabiliyor; etkileşimli ekranların 60 saniyesi
//: buraya uymuyor.
const REPORT_TIMEOUT = 600;

export function reportChain({ api, root, toast, base }) {
  let lastPath = '';
  //: AÇIK ÖNİZLEME TEKTİR.
  //:
  //: Eskiden her `run()` köke YENİ bir katman ekliyor, öncekini kaldırmıyordu.
  //: "Üret ve yazdır"a üç kez basan kullanıcının ekranında üst üste üç katman
  //: birikiyordu ve her birinin kendi "Yazdır" düğmesi CANLIYDI. Üsttekinden
  //: basıp kapatınca altından aynı raporun bir katmanı daha çıkıyor, o da
  //: basılıyordu: kullanıcının gördüğü şey "yazıcı aynı raporu basıp
  //: duruyor"du. Katman tek tutulunca o dizi kökten biter.
  let openOverlay = null;

  async function printerState() {
    // Sunucunun yazıcısı SORULMAZ: kâğıt orada çıkmıyor. Tek doğru soru
    // "bu cihazda hangi yazıcı seçili".
    if (!canPrintLocally()) {
      return { ready: false,
        error: 'Yazdırma yalnız masaüstü uygulamasında çalışır.' };
    }
    try {
      const local = await localPrinters();
      if (local.error) return { ready: false, error: local.error };
      if (!local.selected) {
        return { ready: false,
          error: 'Bu cihazda yazıcı seçilmemiş — Sistem Ayarları → Yazıcı.' };
      }
      return { ready: true, target: { name: local.selected } };
    } catch (error) {
      return { ready: false, error: error.message };
    }
  }

  /**
   * Raporu üretir ve önizlemeyi açar.
   * @returns {Promise<object|null>} üretim sonucu, başarısızsa null
   */
  async function run(kind, params = {}) {
    let result;
    try {
      // RAPOR UZUN SÜRER ve bu normaldir: sunucu mağazayı sayfa sayfa gezip
      // PDF üretiyor. Kabuğun 60 saniyelik varsayılanı bu isteği ortasında
      // kesiyordu ve hata "çekirdeğe bağlanılamadı" diye görünüyordu — rapor
      // aslında üretilmeye devam ediyordu. Süre burada açıkça istenir.
      result = await api(`${base}/preview`, {
        method: 'POST', body: { kind, ...params }, timeoutSeconds: REPORT_TIMEOUT,
      });
    } catch (error) {
      toast?.(error.message || 'Rapor üretilemedi.', 'bad');
      return null;
    }

    if (!result?.ok) {
      toast?.(result?.error || 'Rapor üretilemedi.', 'bad');
      return null;
    }

    lastPath = result.path || '';
    toast?.(`${result.name} hazır (${formatBytes(result.bytes)}).`, 'good');
    openPreview(result);
    return result;
  }

  function openPreview(result) {
    const overlay = h('div', 'kit-overlay kit-preview');
    const frame = h('div', 'kit-preview-frame');
    frame.setAttribute('role', 'dialog');
    frame.setAttribute('aria-modal', 'true');
    frame.setAttribute('aria-label', `${result.name} önizlemesi`);

    const head = h('div', 'kit-preview-head');
    head.append(h('span', 'kit-preview-name', result.name));
    const note = h('span', 'kit-preview-note', 'Yazıcı durumu okunuyor…');

    const printBtn = button('Yazdır', { variant: 'primary' });
    printBtn.disabled = true;

    const close = () => {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      if (openOverlay === overlay) openOverlay = null;
    };
    const onKey = (event) => { if (event.key === 'Escape') close(); };

    // Önceki katman KAPATILIR: dinleyicisi de gider, düğmesi de. Yalnız
    // `remove()` demek dinleyiciyi bırakırdı.
    if (openOverlay) openOverlay.close();

    head.append(h('span', 'kit-spacer'), note, printBtn,
      button('Kapat', { variant: 'ghost', onClick: close }));

    const body = h('div', 'kit-preview-body');
    for (const page of result.pages || []) {
      const image = h('img', 'kit-preview-page');
      image.src = page;
      image.alt = '';
      body.append(image);
    }
    if (!(result.pages || []).length) {
      body.append(h('div', 'kit-hint',
        'Önizleme üretilemedi (poppler-utils kurulu olmayabilir). '
        + 'Dosya yine de yazıldı ve yazdırılabilir.'));
    }

    const foot = h('div', 'kit-preview-foot');
    const pathNode = h('code', 'kit-preview-path', result.path || '');
    foot.append(h('span', undefined, 'Dosya: '), pathNode);

    frame.append(head, body, foot);
    overlay.append(frame);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
    root.append(overlay);
    overlay.close = close;
    openOverlay = overlay;

    // Yazıcı durumu ÖNCEDEN okunur: kullanıcı tıklayıp hata almasın.
    printerState().then((status) => {
      if (!status?.ready) {
        note.textContent = status?.error || 'Yazıcıya ulaşılamıyor.';
        note.classList.add('bad');
        printBtn.disabled = true;
        return;
      }
      const parts = [status.target?.name || 'Yazıcı'];
      if (typeof status.device?.tonerPercent === 'number') {
        parts.push(`toner %${status.device.tonerPercent}`);
      }
      note.textContent = parts.join(' · ');
      note.classList.remove('bad');
      printBtn.disabled = false;
    });

    printBtn.addEventListener('click', async () => {
      printBtn.disabled = true;
      note.textContent = 'Yazıcıya gönderiliyor…';
      note.classList.remove('bad');
      try {
        // `printLocally` iki adımı birlikte yapar: PDF'i sunucudan indirir,
        // bu cihazın seçili yazıcısına basar. Yazıcı seçme penceresi AÇILMAZ —
        // hedef bir kez ayarlardan seçildi.
        const printer = await printDocument(api, result.path, { copies: 1 });
        const sent = { ok: true, printer };
        if (!sent?.ok) {
          note.textContent = sent?.error || 'Yazdırılamadı.';
          note.classList.add('bad');
          toast?.(sent?.error || 'Yazdırılamadı.', 'bad');
        } else {
          note.textContent = `${sent.printer} yazıcısına gönderildi.`;
          toast?.(`${sent.printer} yazıcısına gönderildi.`, 'good');
        }
      } catch (error) {
        note.textContent = error.message;
        note.classList.add('bad');
        toast?.(error.message, 'bad');
      } finally {
        printBtn.disabled = false;
      }
    });
  }

  return {
    run,
    printerState,
    get lastPath() { return lastPath; },
    /** "Son üretilen: /yol/dosya.pdf" satırı — panelin altında durur. */
    lastFileLine() {
      const node = h('div', 'kit-count');
      return {
        node,
        set(path) {
          node.replaceChildren();
          if (!path) return;
          node.append(h('span', undefined, 'Son üretilen: '), h('code', 'kit-preview-path', path));
        },
      };
    },
  };
}
