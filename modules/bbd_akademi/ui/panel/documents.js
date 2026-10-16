// BBD Akademi — belge önizleme ve yazdırma (kurum logolu A4 PDF).
//
// Kantin raporlarıyla AYNI zincir (`ui-kit/report.js`, `ui-kit/printing.js`):
// sunucu PDF'i üretir (`POST /api/bbd_akademi/km/documents/preview`), önizleme o
// PDF'in sayfa görüntüleridir, "Yazdır" PDF'in KENDİSİNİ bu cihazın yazıcısına
// verir (yazıcı seçme penceresi açılmaz; hedef Sistem Ayarları'ndan ya da
// sistemin varsayılanından). Masaüstü uygulaması dışında (web) yazıcıya
// erişilemez; o zaman "PDF kaydet" kalır.
//
// Ekranlar yalnız `documentButtons(app, {build})` çağırır: `build` veriyi okuyup
// `documents-logic.js` ile `{kind, document}` modelini döndürür. Uzun işler
// (toplu karne) `build(progress)` ile ilerlemeyi yazar ve iptal edilebilir.
//
// ERİŞİLEBİLİRLİK: önizleme penceresi `role=dialog`, `aria-modal`; açılınca
// odak "Yazdır"a gelir, Tab pencere içinde döner, Esc kapatır ve odak pencereyi
// açan düğmeye geri döner. Sayfa görüntülerinin `alt` metni sayfa numarasıdır.
//
// ÖNİZLEME PENCERESİ ORTAKTIR (63-finans-pdf): `ui-kit/report.js` →
// `pdfViewer` — sayfalar, yakınlaştırma, sayfa sayısı, kaydırdıkça kalan
// sayfalar (`POST /km/documents/pages`), "PDF kaydet" (cihazın kaydet
// penceresi). Finansal raporlar `financeToolbar` ile tek tip araç çubuğu
// alır: "Önizle" · "Yazdır" (yazıcı, kopya, sayfa aralığı, dikey/yatay) ·
// "PDF kaydet". Diğer belgelerin "Yazdır"ı eskisi gibi doğrudan basar.

import { button, h } from '../../ui-kit/kit.js';
import { canPrintLocally, printDocument, printerReady } from '../../ui-kit/printing.js';
import { closePdfViewer, pdfViewer, reportToolbar } from '../../ui-kit/report.js';
import { problem } from './api.js';

const TIMEOUT = 300;

function styles() {
  // Panel CSS'i mount içinde yüklenir; bu küçük ek kural seti `ak-doc-` önekli.
  if (document.getElementById('ak-doc-style')) return;
  const style = document.createElement('style');
  style.id = 'ak-doc-style';
  style.textContent = `
.ak-doc-actions { display: inline-flex; gap: 6px; align-items: center; }
.ak-doc-progress { display: grid; gap: 10px; min-width: min(380px, 90vw); padding: 22px; border-radius: 14px;
  background: #fff; border: 1px solid var(--line); box-shadow: 0 30px 70px -28px rgba(0,0,0,.6); }
.ak-doc-progress-bar { height: 6px; border-radius: 3px; background: var(--tint); overflow: hidden; }
.ak-doc-progress-bar > span { display: block; height: 100%; width: 0; background: var(--accent, #3f6fd8);
  transition: width .2s ease; }
.ak-doc-meta { font-size: 11.5px; color: var(--soft); }
.ak-doc-frame:focus { outline: none; }
.ak-doc-page-label { font-size: 11px; color: var(--soft); align-self: center; }`;
  document.head.append(style);
}

/** Odak tuzağı + Esc: pencere kapanınca odak `opener`a döner. */
function trapFocus(container, close, opener) {
  const onKey = (event) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== 'Tab') return;
    const items = [...container.querySelectorAll('button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])')]
      .filter((node) => node.offsetParent !== null || node === document.activeElement);
    if (!items.length) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  };
  document.addEventListener('keydown', onKey, true);
  return () => {
    document.removeEventListener('keydown', onKey, true);
    if (opener?.isConnected) opener.focus();
  };
}

async function produce0(app, model, { preview }) {
  return app.api.km('/documents/preview', { method: 'POST', timeoutSeconds: TIMEOUT,
    body: { kind: model.kind, document: model.document, preview } });
}

async function sendToPrinter(app, result) {
  return printDocument((path, options) => app.coreApi(path, options), result.path, { copies: 1 });
}

/** Hazırlanıyor penceresi (veri okuma + PDF üretimi). İptal edilebilir. */
function progressDialog(app, title, opener) {
  styles();
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'ak-doc-progress');
  box.setAttribute('role', 'alertdialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', `${title} hazırlanıyor`);
  const text = h('div', '', `${title} hazırlanıyor…`);
  text.setAttribute('aria-live', 'polite');
  const bar = h('div', 'ak-doc-progress-bar');
  const fill = h('span');
  bar.append(fill);
  const state = { cancelled: false };
  const cancel = button('Vazgeç', { variant: 'ghost', onClick: () => { state.cancelled = true; close(); } });
  box.append(text, bar, cancel);
  overlay.append(box);
  app.root.append(overlay);
  const release = trapFocus(box, () => { state.cancelled = true; close(); }, opener);
  cancel.focus();
  function close() {
    release();
    overlay.remove();
  }
  return {
    state,
    step(done, total, label = '') {
      if (total) fill.style.width = `${Math.round((done / total) * 100)}%`;
      text.textContent = label || `${title} hazırlanıyor… ${done} / ${total}`;
    },
    close,
  };
}

/** Önizlemenin kalan sayfaları (kaydırdıkça). */
function morePages(app) {
  return async (result, first, last) => {
    const data = await app.api.km('/documents/pages', { method: 'POST', timeoutSeconds: 120,
      body: { path: result.path, first, last } });
    if (!data?.ok) throw new Error(data?.error || 'Sayfalar alınamadı.');
    return data.pages || [];
  };
}

/**
 * Önizleme penceresi (ortak `pdfViewer`): sayfalar + yakınlaştırma + Yazdır +
 * PDF kaydet + Kapat. `regenerate` verilirse yazdırma penceresinde yön seçilir.
 * `direct` (varsayılan): "Yazdır" eskisi gibi pencere açmadan basar.
 */
export function showPreview(app, result, { opener = null, regenerate = null, direct = !regenerate,
  orientation = 'portrait' } = {}) {
  return pdfViewer({ root: app.root, api: (path, options) => app.coreApi(path, options), result,
    toast: app.toast, opener, morePages: morePages(app), regenerate, direct, orientation });
}

/**
 * Belgeyi hazırlar ve önizler (`print=false`) ya da doğrudan basar (`print=true`).
 * Yazıcı yoksa doğrudan baskı önizlemeye düşer ve sebebi orada yazar.
 */
export async function runDocument(app, build, { print = false, title = 'Belge', opener = null } = {}) {
  const busy = progressDialog(app, title, opener);
  let model;
  try {
    model = await build((done, total, label) => busy.step(done, total, label), busy.state);
  } catch (error) {
    busy.close();
    app.toast?.(problem(error, `${title} için veri okunamadı.`), 'bad');
    return null;
  }
  if (busy.state.cancelled) return null;
  if (!model?.document) {
    busy.close();
    app.toast?.(`${title} için yazdırılacak kayıt yok.`, 'warn');
    return null;
  }
  const direct = print && canPrintLocally() && (await printerReady()).ready;
  busy.step(1, 1, direct ? 'Belge yazıcıya hazırlanıyor…' : 'Önizleme hazırlanıyor…');
  let result;
  try {
    result = await produce0(app, model, { preview: !direct });
  } catch (error) {
    busy.close();
    app.toast?.(problem(error, 'Belge üretilemedi.'), 'bad');
    return null;
  }
  if (busy.state.cancelled) return null;
  busy.close();
  if (!result?.ok) {
    app.toast?.(result?.error || 'Belge üretilemedi.', 'bad');
    return null;
  }
  if (!direct) {
    showPreview(app, result, { opener, orientation: model.document.orientation || 'portrait',
      regenerate: async ({ orientation }) => produce0(app,
        { kind: model.kind, document: { ...model.document, orientation } }, { preview: true }) });
    return result;
  }
  try {
    const printer = await sendToPrinter(app, result);
    app.toast?.(`${result.name} · ${printer} yazıcısına gönderildi.`, 'good');
  } catch (error) {
    app.toast?.(problem(error, 'Yazdırılamadı.'), 'bad');
    showPreview(app, { ...result, pages: [], previewError: problem(error, 'Yazdırılamadı.') }, { opener });
  }
  return result;
}

/**
 * Ekranlara eklenen düğme çifti: "Önizle" + "Yazdır".
 * @param {object} app
 * @param {{build: Function, title?: string, label?: string, disabled?: string}} spec
 *   `build(progress, state)` → `{kind, document}`; `disabled` doluysa düğmeler kapalı ve sebep başlıkta.
 */
export function documentButtons(app, { build, title = 'Belge', label = '', disabled = '' } = {}) {
  styles();
  const node = h('span', 'ak-doc-actions');
  const previewBtn = button(label ? `${label} · önizle` : 'Önizle', {
    title: disabled || `${title}: kurum logolu belgeyi önizler (PDF kaydet / yazdır).` });
  const printBtn = button('Yazdır', { title: disabled || `${title}: önizlemeden bu cihazın yazıcısına gönderir.` });
  // Bir ekranda birden çok "Yazdır" olabilir: ekran okuyucu hangisi olduğunu duysun.
  printBtn.setAttribute('aria-label', `${label || title} — yazdır`);
  printBtn.dataset.command = 'print';   // Ctrl/⌘+P bu düğmeye basar (KM kabuğu, ADR 0032)
  for (const item of [previewBtn, printBtn]) item.disabled = Boolean(disabled);
  let running = false;
  const run = async (print, opener) => {
    if (running) return;
    running = true;
    previewBtn.disabled = true;
    printBtn.disabled = true;
    try {
      await runDocument(app, build, { print, title, opener });
    } finally {
      running = false;
      previewBtn.disabled = Boolean(disabled);
      printBtn.disabled = Boolean(disabled);
    }
  };
  previewBtn.addEventListener('click', () => run(false, previewBtn));
  printBtn.addEventListener('click', () => run(true, printBtn));
  node.append(previewBtn, printBtn);
  return node;
}

/**
 * Finansal raporların tek tip araç çubuğu: "Önizle" · "Yazdır" · "PDF kaydet"
 * (`ui-kit/report.js` → `reportToolbar`). `build(progress, state)` ekrandaki
 * SÜZGEÇLERLE veriyi okuyup `{kind, document}` döndürür; yön (dikey/yatay)
 * yazdırma penceresinden gelir ve modele yazılır.
 *
 * @param {object} app
 * @param {{build: Function, title?: string, label?: string, orientation?: string, blocked?: () => string}} spec
 */
export function financeToolbar(app, { build, title = 'Rapor', label = '', orientation = 'portrait',
  blocked = null } = {}) {
  const produce = async ({ preview, orientation: chosen }) => {
    const model = await build(() => {}, { cancelled: false });
    if (!model?.document) return { ok: false, error: `${title} için yazdırılacak kayıt yok.` };
    const document = { ...model.document, orientation: chosen || model.document.orientation || orientation };
    try {
      return await produce0(app, { kind: model.kind, document }, { preview });
    } catch (error) {
      return { ok: false, error: problem(error, 'Belge üretilemedi.') };
    }
  };
  const bar = reportToolbar({ root: app.root, api: (path, options) => app.coreApi(path, options),
    toast: app.toast, produce, morePages: morePages(app), title, label, orientation, blocked });
  return bar.node;
}

/** Akademi'nin kendi PDF'ini (makbuz, ekstre, sipariş belgesi) önizleme penceresinde açar. */
export async function previewAkademiPdf(app, { kind, url, name = '', opener = null }) {
  const busy = progressDialog(app, 'Belge', opener);
  let result;
  try {
    result = await app.api.km('/documents/akademi', { method: 'POST', timeoutSeconds: TIMEOUT,
      body: { kind, url, name } });
  } catch (error) {
    busy.close();
    app.toast?.(problem(error, 'Belge alınamadı.'), 'bad');
    return null;
  }
  busy.close();
  if (busy.state.cancelled) return null;
  if (!result?.ok) {
    app.toast?.(result?.error || 'Belge alınamadı.', 'bad');
    return null;
  }
  showPreview(app, result, { opener });
  return result;
}

/** Ekran kapanınca açık pencere kalmasın. */
export function closeDocumentPreview() {
  closePdfViewer();
}
