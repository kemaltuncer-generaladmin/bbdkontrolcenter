// Rapor zinciri — üret → önizle → yazdır → PDF kaydet.
//
// Tek kopya (ADR 0011). Akış üç uca dayanır ve her rapor üreten modül bu üçünü
// aynı adlarla açar:
//
//   POST <base>/preview  {kind, ...params}
//        → {ok, path, name, bytes, pages: ["data:image/png;base64,…"], pageCount}
//   POST <base>/print    {path, copies}
//        → {ok, printer} | {ok:false, error}
//   GET  <base>/printer
//        → {ready, error, target:{name}, device:{tonerPercent}}
//
// NEDEN SUNUCU ÜRETİYOR: kalıcı çıktı `Kontrol Merkezi/Raporlar/…` klasörüne
// 0600 izinle yazılmalı ve Çıktı Merkezi'ne kaydolmalı. Panel yalnız yolu
// gösterir ve sayfaları önizler.
//
// ÖNİZLEME GERÇEK PDF'İN KENDİSİDİR: sunucu `pdftoppm` ile sayfaları
// görüntüye çevirir. Ekranda gördüğünüz ile yazıcıdan çıkan aynıdır.
//
// FİNANSAL RAPORLARIN TEK TİP ARAÇ ÇUBUĞU (63-finans-pdf, 16.10.2026 —
// kullanıcı: "finansal raporlarda pdf önizleme ve yazdırma olsun"):
//
//   reportToolbar({...})  → "Önizle" · "Yazdır" · "PDF kaydet"
//   pdfViewer({...})      → uygulama içi önizleme: sayfalar, yakınlaştırma,
//                           sayfa sayısı, kalan sayfalar kaydırdıkça gelir
//   printDialog({...})    → yazıcı seçimi, kopya, sayfa aralığı, dikey/yatay
//
// Bütün finans ekranları (Akademi kurum carisi, kantin raporları, ödeme
// talepleri, öğle yemeği) bu üçünü kullanır; her ekran kendi önizlemesini
// yazmaz. Ekrandaki süzgeçler PDF'e aynen gider — onu üreten `produce`
// çağıranın elindedir.

import { button, bytes as formatBytes, h, loadStyles } from './kit.js';
// BASKI CİHAZDA YAPILIR — gerekçe `printing.js` başlığında.
import {
  canPrintLocally, localPrinters, pageRangeError, printDocument, printerReady, savePdf,
} from './printing.js';

//: Rapor üretiminin zaman aşımı (saniye). Mağazanın tam taraması + PDF
//: üretimi birkaç dakikayı bulabiliyor; etkileşimli ekranların 60 saniyesi
//: buraya uymuyor.
const REPORT_TIMEOUT = 600;

const ZOOMS = [50, 75, 100, 125, 150, 200];
//: Kaydırınca bir seferde istenen sayfa sayısı.
const MORE_CHUNK = 6;

//: AÇIK ÖNİZLEME TEKTİR (bütün ekranlar için). İkinci önizleme açılırken
//: öncekinin dinleyicisi de düğmesi de kapanır — üst üste biriken canlı
//: "Yazdır" düğmeleri "yazıcı aynı raporu basıp duruyor" diye görünüyordu.
let openViewer = null;

function styles() {
  loadStyles(import.meta.url, 'report.css');
}

/** Odak tuzağı + Esc: kapanınca odak `opener`a (yoksa önceki odağa) döner. */
function trap(container, close, opener) {
  const previous = opener || document.activeElement;
  const onKey = (event) => {
    if (!container.isConnected) return;
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = [...container.querySelectorAll(
      'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])')]
      .filter((node) => !node.closest('[hidden]'));
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  document.addEventListener('keydown', onKey, true);
  return () => {
    document.removeEventListener('keydown', onKey, true);
    if (previous?.isConnected && typeof previous.focus === 'function') previous.focus();
  };
}

/** `<option>` — `new Option` her DOM'da (test DOM'u) bulunmuyor. */
function option(label, value) {
  const node = h('option', '', label);
  node.value = value;
  return node;
}

function message(error, fallback) {
  return error?.message || (typeof error === 'string' ? error : '') || fallback;
}

/**
 * Bir sonucu "kaydedildi" cümlesine çevirir.
 * @returns {Promise<string|null>} kaydedilen yol ('' = tarayıcı indirdi), vazgeçildiyse null
 */
async function saveResult(api, result, toast) {
  const saved = await savePdf(api, result.path, result.name);
  if (saved.cancelled) return null;
  toast?.(saved.path ? `PDF kaydedildi: ${saved.path}` : `${saved.name} indirildi.`, 'good');
  return saved.path;
}

// ------------------------------------------------------------------ yazdır

/**
 * Yazdırma penceresi: yazıcı, kopya, sayfa aralığı, dikey/yatay.
 *
 * `result` verilirse o PDF basılır; `produce({orientation})` verilirse PDF
 * "Yazdır"a basıldığında üretilir (araç çubuğundan doğrudan yazdırma).
 * `regenerate({orientation})` yön değişince PDF'i yeniden üretir; verilmezse
 * yön seçimi gösterilmez (rapor tek yönlüdür).
 *
 * @returns {Promise<{printer:string, result:object}|null>}
 */
export function printDialog({ root, api, toast, result = null, produce = null, regenerate = null,
  orientation = 'portrait', opener = null, title = 'Yazdır' } = {}) {
  styles();
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay kit-rv-print-overlay');
    const card = h('div', 'kit-dialog kit-rv-print');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.tabIndex = -1;
    const heading = h('h3', 'kit-dialog-title', title);
    heading.id = `kit-rv-print-${Date.now()}`;
    card.setAttribute('aria-labelledby', heading.id);
    card.append(heading);

    const field = (label, control, hint = '') => {
      const wrap = h('label', 'kit-field kit-rv-field');
      wrap.append(h('span', 'kit-field-label', label), control);
      if (hint) wrap.append(h('span', 'kit-field-hint', hint));
      return wrap;
    };

    const printerSelect = h('select', 'kit-select');
    printerSelect.disabled = true;
    printerSelect.append(option('Yazıcılar okunuyor…', ''));
    const copies = h('input', 'kit-input');
    copies.type = 'number';
    copies.min = '1';
    copies.max = '20';
    copies.value = '1';
    const pages = h('input', 'kit-input');
    pages.type = 'text';
    pages.placeholder = 'Örn. 1-3, 5';
    pages.autocomplete = 'off';
    const total = Number(result?.pageCount || 0);
    const orientationSelect = h('select', 'kit-select');
    orientationSelect.append(option('Dikey (A4)', 'portrait'), option('Yatay (A4)', 'landscape'));
    orientationSelect.value = orientation === 'landscape' ? 'landscape' : 'portrait';

    const grid = h('div', 'kit-rv-print-grid');
    grid.append(
      field('Yazıcı', printerSelect),
      field('Kopya', copies, '1–20'),
      field('Sayfalar', pages, total ? `Boş = bütün sayfalar (${total} sayfa)` : 'Boş = bütün sayfalar'),
    );
    if (regenerate || produce) grid.append(field('Yön', orientationSelect));
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    const ok = button('Yazdır', { variant: 'primary' });
    ok.disabled = true;
    const cancel = button('Vazgeç');
    const actions = h('div', 'kit-dialog-actions');
    actions.append(cancel, ok);
    card.append(grid, error, actions);
    overlay.append(card);
    root.append(overlay);

    let release = () => {};
    let done = false;
    const close = (value) => {
      if (done) return;
      done = true;
      release();
      overlay.remove();
      resolve(value);
    };
    release = trap(card, () => close(null), opener);
    cancel.addEventListener('click', () => close(null));
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    card.focus();

    if (!canPrintLocally()) {
      printerSelect.replaceChildren(option('Yazıcı yok', ''));
      error.textContent = 'Yazdırma yalnız masaüstü uygulamasında çalışır. “PDF kaydet” ile dosyayı alıp '
        + 'tarayıcıdan yazdırabilirsiniz.';
    } else {
      localPrinters().then((list) => {
        if (done) return;
        printerSelect.replaceChildren();
        for (const name of list.printers) {
          const label = name === list.systemDefault ? `${name} (sistem varsayılanı)` : name;
          printerSelect.append(option(label, name));
        }
        if (!list.printers.length) {
          printerSelect.append(option('Kurulu yazıcı yok', ''));
          error.textContent = list.blocked || list.error || 'Bu cihazda yazıcı bulunamadı.';
          return;
        }
        printerSelect.value = list.effective || list.printers[0];
        printerSelect.disabled = false;
        ok.disabled = false;
        ok.focus();
      }).catch((failure) => {
        if (done) return;
        error.textContent = message(failure, 'Yazıcı listesi alınamadı.');
      });
    }

    ok.addEventListener('click', async () => {
      const range = pages.value.trim();
      const problem = pageRangeError(range);
      if (problem) {
        error.textContent = problem;
        pages.focus();
        return;
      }
      const count = Math.round(Number(copies.value) || 1);
      if (count < 1 || count > 20) {
        error.textContent = 'Kopya sayısı 1 ile 20 arasında olmalı.';
        copies.focus();
        return;
      }
      ok.disabled = true;
      cancel.disabled = true;
      error.textContent = '';
      const chosen = orientationSelect.value;
      try {
        let target = result;
        if (!target || (regenerate && chosen !== (target.orientation || orientation))) {
          ok.textContent = 'Hazırlanıyor…';
          target = target && regenerate
            ? await regenerate({ orientation: chosen })
            : await produce({ orientation: chosen });
          if (!target?.ok) throw new Error(target?.error || 'Rapor üretilemedi.');
        }
        ok.textContent = 'Gönderiliyor…';
        const printer = await printDocument(api, target.path, {
          copies: count, printer: printerSelect.value, pages: range });
        toast?.(`${target.name} · ${printer} yazıcısına gönderildi${count > 1 ? ` (${count} kopya)` : ''}.`, 'good');
        close({ printer, result: target });
      } catch (failure) {
        error.textContent = message(failure, 'Yazdırılamadı.');
        ok.textContent = 'Yazdır';
        ok.disabled = false;
        cancel.disabled = false;
      }
    });
  });
}

// ------------------------------------------------------------------ önizleme

/**
 * Uygulama içi PDF önizleme penceresi.
 *
 * @param {object} spec
 * @param {HTMLElement} spec.root — overlay'in ekleneceği PANEL KÖKÜ
 * @param {(path:string, options?:object)=>Promise<any>} spec.api — çekirdek istemcisi (`/api/outputs/document`)
 * @param {object} spec.result — üretim sonucu {path, name, bytes, pages, pageCount, shown, previewError, number?, parts?}
 * @param {(result:object, first:number, last:number)=>Promise<string[]>} [spec.morePages] — kalan sayfalar
 * @param {(options:{orientation:string})=>Promise<object>} [spec.regenerate] — yönü değiştirip yeniden üretir
 * @param {boolean} [spec.direct] — "Yazdır" pencere açmadan basar (eski davranış; finans dışı belgeler)
 */
export function pdfViewer({ root, api, result, toast, opener = null, morePages = null,
  regenerate = null, orientation = 'portrait', direct = false }) {
  styles();
  openViewer?.close();
  let current = { orientation, ...result };

  const overlay = h('div', 'kit-overlay kit-preview');
  const frame = h('div', 'kit-preview-frame kit-rv-frame');
  frame.setAttribute('role', 'dialog');
  frame.setAttribute('aria-modal', 'true');
  frame.tabIndex = -1;
  const name = h('span', 'kit-preview-name');
  name.id = `kit-rv-title-${Date.now()}`;
  frame.setAttribute('aria-labelledby', name.id);

  const note = h('span', 'kit-preview-note', 'Yazıcı durumu okunuyor…');
  note.setAttribute('aria-live', 'polite');
  const printBtn = button('Yazdır', { variant: 'primary',
    title: direct ? 'Bu cihazın yazıcısına gönderir.' : 'Yazıcı, kopya, sayfa aralığı ve yön seçilir.' });
  printBtn.disabled = true;
  const saveBtn = button('PDF kaydet', { title: 'PDF dosyasını bu cihaza kaydeder.' });
  const closeBtn = button('Kapat', { variant: 'ghost' });

  // Yakınlaştırma: −, yüzde, +, sığdır. Klavyede + / − / 0.
  let zoom = 100;
  let fit = false;
  const zoomOut = button('−', { variant: 'ghost', title: 'Uzaklaştır' });
  zoomOut.setAttribute('aria-label', 'Uzaklaştır');
  const zoomIn = button('+', { variant: 'ghost', title: 'Yakınlaştır' });
  zoomIn.setAttribute('aria-label', 'Yakınlaştır');
  const zoomLabel = h('span', 'kit-rv-zoom-label', '%100');
  zoomLabel.setAttribute('aria-live', 'polite');
  const fitBtn = button('Sığdır', { variant: 'ghost', title: 'Sayfayı pencere genişliğine sığdırır.' });
  const zoomGroup = h('span', 'kit-rv-zoom');
  zoomGroup.setAttribute('role', 'group');
  zoomGroup.setAttribute('aria-label', 'Yakınlaştırma');
  zoomGroup.append(zoomOut, zoomLabel, zoomIn, fitBtn);

  const head = h('div', 'kit-preview-head kit-rv-head');
  head.append(name, h('span', 'kit-spacer'), zoomGroup, note, printBtn, saveBtn, closeBtn);
  const body = h('div', 'kit-preview-body kit-rv-body');
  const foot = h('div', 'kit-preview-foot kit-rv-foot');
  frame.append(head, body, foot);
  overlay.append(frame);
  root.append(overlay);

  let release = () => {};
  let observer = null;
  function close() {
    observer?.disconnect();
    release();
    overlay.remove();
    if (openViewer?.overlay === overlay) openViewer = null;
  }
  release = trap(frame, close, opener);
  closeBtn.addEventListener('click', close);
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(); });
  openViewer = { overlay, close };

  function applyZoom() {
    zoomLabel.textContent = fit ? 'Sığdır' : `%${zoom}`;
    for (const image of body.querySelectorAll('img.kit-preview-page')) {
      image.style.width = fit ? '100%' : `${Math.round(7.6 * zoom)}px`;
      image.style.maxWidth = fit || zoom <= 100 ? '100%' : 'none';
    }
    zoomOut.disabled = !fit && zoom === ZOOMS[0];
    zoomIn.disabled = !fit && zoom === ZOOMS[ZOOMS.length - 1];
  }
  const step = (direction) => {
    const index = ZOOMS.indexOf(zoom);
    fit = false;
    zoom = ZOOMS[Math.max(0, Math.min(ZOOMS.length - 1, index + direction))];
    applyZoom();
  };
  zoomOut.addEventListener('click', () => step(-1));
  zoomIn.addEventListener('click', () => step(1));
  fitBtn.addEventListener('click', () => { fit = !fit; applyZoom(); });
  frame.addEventListener('keydown', (event) => {
    if (event.target?.tagName === 'INPUT' || event.metaKey || event.ctrlKey) return;
    if (event.key === '+' || event.key === '=') { event.preventDefault(); step(1); }
    else if (event.key === '-') { event.preventDefault(); step(-1); }
    else if (event.key === '0') { event.preventDefault(); fit = false; zoom = 100; applyZoom(); }
  });

  function addPages(images, startIndex) {
    images.forEach((src, offset) => {
      const image = h('img', 'kit-preview-page');
      image.src = src;
      image.alt = `Sayfa ${startIndex + offset}`;
      image.loading = startIndex + offset > 2 ? 'lazy' : 'eager';
      body.insertBefore(image, more.isConnected ? more : null);
    });
    applyZoom();
  }

  // Kalan sayfalar: kaydırınca (IntersectionObserver) ya da düğmeyle istenir.
  const more = button('Sonraki sayfaları göster', { variant: 'ghost' });
  more.classList.add('kit-rv-more');
  let loading = false;
  async function loadMore() {
    const shown = body.querySelectorAll('img.kit-preview-page').length;
    const totalPages = Number(current.pageCount || 0);
    if (loading || !morePages || shown >= totalPages) return;
    loading = true;
    more.disabled = true;
    more.textContent = 'Sayfalar hazırlanıyor…';
    try {
      const first = shown + 1;
      const last = Math.min(totalPages, shown + MORE_CHUNK);
      const images = await morePages(current, first, last);
      if (!overlay.isConnected) return;
      addPages(images || [], first);
      if (!(images || []).length) throw new Error('Sayfa görüntüsü gelmedi.');
    } catch (failure) {
      more.textContent = `Sayfalar alınamadı — tekrar dene (${message(failure, 'hata')})`;
      more.disabled = false;
      loading = false;
      return;
    }
    loading = false;
    updateFoot();
  }
  more.addEventListener('click', loadMore);

  function updateFoot() {
    const shown = body.querySelectorAll('img.kit-preview-page').length;
    const totalPages = Number(current.pageCount || shown || 0);
    const rest = Math.max(0, totalPages - shown);
    if (rest > 0 && morePages) {
      more.disabled = false;
      more.textContent = `Sonraki sayfaları göster (${rest} sayfa kaldı)`;
      if (!more.isConnected) body.append(more);
    } else if (more.isConnected) {
      more.remove();
    }
    foot.replaceChildren();
    const bits = [
      current.number ? `Belge no: ${current.number}` : '',
      totalPages ? `${totalPages} sayfa${shown && shown < totalPages ? ` · ilk ${shown} sayfa gösteriliyor` : ''}` : '',
      current.parts > 1 ? `${current.parts} belge tek iş` : '',
      current.bytes ? formatBytes(current.bytes) : '',
      current.orientation === 'landscape' ? 'Yatay' : '',
    ].filter(Boolean);
    foot.append(h('span', '', bits.join(' · ')));
    if (current.path) {
      foot.append(h('span', '', ' · Dosya: '), h('code', 'kit-preview-path', current.path));
    }
  }

  function render() {
    name.textContent = current.name || 'Belge';
    body.replaceChildren();
    const pages = current.pages || [];
    if (!pages.length) {
      body.append(h('div', 'kit-hint', current.previewError
        || 'Önizleme üretilemedi. Belge yine de hazırlandı; yazdırabilir ya da PDF olarak kaydedebilirsiniz.'));
    }
    addPages(pages, Number(current.first || 1));
    updateFoot();
    if (morePages && typeof IntersectionObserver === 'function') {
      observer?.disconnect();
      observer = new IntersectionObserver((entries) => {
        if (entries.some((entry) => entry.isIntersecting)) loadMore();
      }, { root: body, rootMargin: '400px' });
      observer.observe(more);
    }
  }
  render();
  frame.focus();

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    try {
      await saveResult(api, current, toast);
    } catch (failure) {
      toast?.(message(failure, 'PDF kaydedilemedi.'), 'bad');
    } finally {
      saveBtn.disabled = false;
    }
  });

  printBtn.addEventListener('click', async () => {
    if (direct) {
      printBtn.disabled = true;
      note.classList.remove('bad');
      note.textContent = 'Yazıcıya gönderiliyor…';
      try {
        const printer = await printDocument(api, current.path, { copies: 1 });
        note.textContent = `${printer} yazıcısına gönderildi.`;
        toast?.(`${printer} yazıcısına gönderildi.`, 'good');
      } catch (failure) {
        note.textContent = message(failure, 'Yazdırılamadı.');
        note.classList.add('bad');
      } finally {
        printBtn.disabled = false;
      }
      return;
    }
    const outcome = await printDialog({ root, api, toast, result: current, opener: printBtn,
      orientation: current.orientation || orientation,
      regenerate: regenerate ? async (options) => {
        const next = await regenerate(options);
        if (next?.ok) {
          current = { ...next, orientation: options.orientation };
          if (overlay.isConnected) render();
        }
        return next;
      } : null });
    if (outcome) {
      note.classList.remove('bad');
      note.textContent = `${outcome.printer} yazıcısına gönderildi.`;
    }
  });

  // Yazıcı durumu ÖNCEDEN okunur: kullanıcı tıklayıp hata almasın.
  printerReady().then((status) => {
    if (!overlay.isConnected) return;
    if (!status.ready) {
      note.textContent = status.error || 'Yazıcıya ulaşılamıyor.';
      note.classList.add('bad');
      printBtn.disabled = true;
      saveBtn.focus();
      return;
    }
    note.textContent = status.name || 'Yazıcı hazır';
    printBtn.disabled = false;
    printBtn.focus();
  });

  return { close, get result() { return current; } };
}

/** Açık önizleme varsa kapatır (ekran kapanırken). */
export function closePdfViewer() {
  openViewer?.close();
}

// ------------------------------------------------------------------ araç çubuğu

/**
 * Finansal raporların tek tip araç çubuğu: "Önizle" · "Yazdır" · "PDF kaydet".
 *
 * @param {object} spec
 * @param {HTMLElement} spec.root — panel kökü (pencereler buraya açılır)
 * @param {(path:string, options?:object)=>Promise<any>} spec.api — çekirdek istemcisi
 * @param {(options:{preview:boolean, orientation:string})=>Promise<object>} spec.produce
 *   PDF'i EKRANDAKİ SÜZGEÇLERLE üretir; `{ok, path, name, pages, pageCount, …}` döner.
 * @param {(result:object, first:number, last:number)=>Promise<string[]>} [spec.morePages]
 * @param {string} [spec.title] — ekran okuyucu ve ipuçları için rapor adı
 * @param {string} [spec.orientation] — varsayılan yön ('portrait' | 'landscape')
 * @param {() => string} [spec.blocked] — doluysa düğmeler kapalı ve sebep ipucunda
 */
export function reportToolbar({ root, api, toast, produce, morePages = null, title = 'Rapor',
  orientation = 'portrait', blocked = null }) {
  styles();
  const node = h('span', 'kit-rv-toolbar');
  node.setAttribute('role', 'group');
  node.setAttribute('aria-label', `${title} — PDF`);
  const previewBtn = button('Önizle', { title: `${title}: PDF önizlemesi (sayfalar, yakınlaştırma).` });
  const printBtn = button('Yazdır', { title: `${title}: yazıcı, kopya, sayfa aralığı ve yön seçilir.` });
  const saveBtn = button('PDF kaydet', { title: `${title}: PDF dosyasını bu cihaza kaydeder.` });
  previewBtn.setAttribute('aria-label', `${title} — önizle`);
  printBtn.setAttribute('aria-label', `${title} — yazdır`);
  saveBtn.setAttribute('aria-label', `${title} — PDF kaydet`);
  node.append(previewBtn, printBtn, saveBtn);
  const all = [previewBtn, printBtn, saveBtn];
  let running = false;

  function refresh() {
    const reason = blocked?.() || '';
    for (const item of all) {
      item.disabled = running || Boolean(reason);
      if (reason) item.title = reason;
    }
  }

  async function produceChecked(options) {
    const result = await produce(options);
    if (!result?.ok) throw new Error(result?.error || 'Rapor üretilemedi.');
    return { orientation: options.orientation, ...result };
  }

  async function busy(target, label, work) {
    if (running) return;
    running = true;
    const text = target.textContent;
    target.textContent = label;
    target.setAttribute('aria-busy', 'true');
    refresh();
    try {
      await work();
    } catch (failure) {
      toast?.(message(failure, 'Rapor üretilemedi.'), 'bad');
    } finally {
      running = false;
      target.textContent = text;
      target.removeAttribute('aria-busy');
      refresh();
    }
  }

  previewBtn.addEventListener('click', () => busy(previewBtn, 'Hazırlanıyor…', async () => {
    const result = await produceChecked({ preview: true, orientation });
    pdfViewer({ root, api, toast, result, opener: previewBtn, morePages, orientation,
      regenerate: (options) => produceChecked({ preview: true, ...options }) });
  }));
  printBtn.addEventListener('click', () => busy(printBtn, 'Yazdır…', async () => {
    await printDialog({ root, api, toast, opener: printBtn, orientation, title: `${title} — yazdır`,
      produce: (options) => produceChecked({ preview: false, ...options }) });
  }));
  saveBtn.addEventListener('click', () => busy(saveBtn, 'Hazırlanıyor…', async () => {
    const result = await produceChecked({ preview: false, orientation });
    await saveResult(api, result, toast);
  }));
  refresh();
  return { node, refresh };
}

// ------------------------------------------------------------------ eski zincir

/**
 * @param {object} spec
 * @param {(path:string, options?:object)=>Promise<any>} spec.api
 * @param {HTMLElement} spec.root — overlay'in ekleneceği PANEL KÖKÜ
 * @param {(message:string, tone?:string)=>void} spec.toast
 * @param {string} spec.base — modülün uç öneki, ör. '/api/store_reports'
 */
export function reportChain({ api, root, toast, base }) {
  let lastPath = '';

  // Sunucunun yazıcısı SORULMAZ: kâğıt orada çıkmıyor. Tek doğru soru "bu
  // cihazda hangi yazıcıya basacağız" ve onu `printing.js` yanıtlıyor.
  async function printerState() {
    const status = await printerReady();
    return status.ready
      ? { ready: true, target: { name: status.name } }
      : { ready: false, error: status.error };
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
      // kesiyordu; süre burada açıkça istenir.
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
    pdfViewer({ root, api, toast, result });
    return result;
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
