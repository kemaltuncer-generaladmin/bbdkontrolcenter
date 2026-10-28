// BBD Akademi — Sözleşmeler (F-SZ, iş 80): KM ekranlarının ORTAK parçaları.
// Bu dosyayı 80-km-sozlesme yazar; 80-km-belge ("Sözleşmeler" ekranı, toplu belge) de kullanır:
//
//   openContractFile(app, {kind, url, name, opener})  Akademi PDF'i → KM önizleme (sayfa görüntüleri) → Yazdır / PDF kaydet
//   printContractFile(app, {kind, url, name})          PDF'in KENDİSİ bu cihazın yazıcısına (yazıcı yoksa önizleme)
//   buildDocuments(app, body, {print, opener})         A17 (+ A18 iş yoklaması) → önizleme / yazdır; `skipped` döner
//   contractSettings(app)                              `/v1/admin/settings` → contracts.* (okunamazsa varsayılan)
//   onContractEvent(fn) / emitContractEvent(event)     ANLIK yerel veri yolu (`contract.changed`, `contract.party_changed`)
//   watchStudentContracts(app, studentId, onChange)    açık ekranın yoklaması (Akademi akışı kişiye bağlı — aşağıda)
//   requestWizard(studentId, planId) / wizardButton   "Kayıt ve ödeme planı" sihirbazını açtırır (olay; sihirbaz 80-km-sihirbaz'ın)
//   openStudentContracts(app, studentId)               öğrenci kartı → Cari, kantin ve kitap → Sözleşmeler
//   editInStudentsPanel(app, {kmId, name, onClosed})   "Öğrenciler panelinde düzenle" (bbd_students kaydı)
//
// YAZDIRMA ZİNCİRİ (karne deseni): Akademi PDF'i kısa ömürlü imzalı bağlantıyla verir (`ContractFile {url, expiresAt,
// fileName}`); KM bağlantıyı köprüden indirir (`/km/documents/akademi`, tür anahtarı aşağıda), sayfa görüntüleriyle
// önizler, "Yazdır" PDF'in kendisini yazıcıya verir (`ui-kit/printing.js` → `/api/outputs/document`). KM arka ucu bu
// türü henüz tanımıyorsa (`AKADEMI_PDFS`, 80-km-belge ekler) bağlantı `fetchFile` ile indirilip sistemin PDF
// uygulamasında açılır — iş yarıda kalmaz.
//
// ANLIK: Akademi'nin SSE akışı kişiye bağlıdır (`/v1/me/events`), vekil entegrasyon anahtarıyla konuşur. Bu yüzden
// (Canlı sınav ekranıyla aynı karar) KM'de: (1) KM'deki her sözleşme yazması yerel olay yayar → açık sekme ve
// sihirbaz HEMEN tazelenir; (2) KM'de kimlik değişince (`bbd-students:updated`, 80-km-kimlik) taraf yeniden okunur;
// (3) açık sözleşme sekmesi 15 sn'de bir tek liste okumasıyla (A3) başka cihazdaki değişikliği yakalar (sekme gizliyken durur).

import { button, h, pollLoop } from '../../../ui-kit/kit.js';
import { canPrintLocally, printDocument, printerReady } from '../../../ui-kit/printing.js';
import { AkademiError, openBlob, problem } from '../api.js';
import { openDrawer } from '../common.js';
import { showPreview } from '../documents.js';
import { contractSettingsOf, listFingerprint } from './shared-contract-logic.js';

/** KM arka ucunun Akademi PDF türleri (`documents/service.py` → `AKADEMI_PDFS`; 80-km-belge ekler). */
export const KM_PDF_KINDS = {
  document: 'contract_document',       // A14 admin_getContractDocument
  preview: 'contract_preview',         // A5  admin_previewContract (?pdf=…)
  documents: 'contract_documents',     // A17 admin_buildContractDocuments / A18 admin_getContractDocumentsJob
  attachment: 'contract_attachment',   // A15 admin_getContractAttachmentFile
};

// Pencere olay adları 80-km-belge ile ORTAK (`devir/raporlar/80-km-belge.ortak.md` §2): ayrıntı `{contractId, studentId, change}`.
const EVENTS = { 'contract.changed': 'bbd-akademi:contract-changed', 'contract.party_changed': 'bbd-akademi:contract-party-changed' };

// ------------------------------------------------------------------ yollar

export const paths = {
  studentContracts: (studentId) => `/v1/admin/students/${studentId}/contracts`,
  party: (studentId) => `/v1/admin/students/${studentId}/contract-party`,
  preview: (studentId) => `/v1/admin/students/${studentId}/contracts:preview`,
  contract: (contractId) => `/v1/admin/contracts/${contractId}`,
};

// ------------------------------------------------------------------ anlık

/**
 * Yerel olay: `{type: 'contract.changed', contractId, studentId, change}` (change: created | revised | signed | unsigned |
 * attachment | cancelled) ya da `{type: 'contract.party_changed', studentId, kmId?}`.
 */
export function emitContractEvent({ type = 'contract.changed', ...detail } = {}) {
  try {
    window.dispatchEvent(new CustomEvent(EVENTS[type] || EVENTS['contract.changed'], { detail }));
  } catch { /* olay yayını işi bozmaz */ }
}

/** Abone ol (iki olay da `{type, ...ayrıntı}` olarak gelir); dönen işlev aboneliği bırakır. */
export function onContractEvent(fn) {
  const handlers = Object.entries(EVENTS).map(([type, name]) => {
    const handler = (event) => fn({ ...(event.detail || {}), type });
    window.addEventListener(name, handler);
    return [name, handler];
  });
  return () => handlers.forEach(([name, handler]) => window.removeEventListener(name, handler));
}

/**
 * Açık öğrenci sözleşme ekranının yoklaması: A3'ün parmak izi değişirse `onChange('remote')`. KM'de kimlik
 * değişince (`bbd-students:updated`) 2 ve 5 sn sonra `onChange('party')` (Akademi aynası 1–3 sn'de uygular).
 */
export function watchStudentContracts(app, studentId, onChange, { every = 15_000 } = {}) {
  let last = null;
  const loop = pollLoop({ every, run: async () => {
    const data = await app.api.get(paths.studentContracts(studentId));
    const print = listFingerprint(Array.isArray(data) ? data : data?.items || []);
    if (last !== null && print !== last) onChange('remote');
    last = print;
  } });
  const timers = [];
  const onStudents = () => {
    timers.push(setTimeout(() => onChange('party'), 2000), setTimeout(() => onChange('party'), 5000));
  };
  window.addEventListener('bbd-students:updated', onStudents);
  return {
    /** Son okunan listeyi bildir (ilk çizim): gereksiz "değişti" sayılmasın. */
    seen(items) { last = listFingerprint(items); },
    stop() {
      loop.stop();
      timers.forEach(clearTimeout);
      window.removeEventListener('bbd-students:updated', onStudents);
    },
  };
}

// ------------------------------------------------------------------ ayarlar

let settingsCache = null;
/** Sözleşme ayarları; izin yoksa / okunamazsa varsayılanlar (kayıt seti, nüsha, numara önizlemesi). */
export async function contractSettings(app, { fresh = false } = {}) {
  if (settingsCache && !fresh) return settingsCache;
  let raw = null;
  try {
    if (app.api.can('admin_getSettings')) raw = await app.api.getCached('/v1/admin/settings');
  } catch { raw = null; }
  settingsCache = contractSettingsOf(raw);
  return settingsCache;
}

// ------------------------------------------------------------------ belge

/** Bağlantıyı sistemin PDF uygulamasında açar (KM türü tanımıyorsa yedek yol). */
async function openDirect(app, url, name) {
  const file = await app.api.fetchFile(url, name || 'sozlesme.pdf');
  openBlob(file);
  return { ok: true, direct: true };
}

/** Akademi PDF'ini KM'ye alır (`preview` true → ilk sayfa görüntüleri). */
async function importPdf(app, { kind, url, name, preview }) {
  return app.api.km('/documents/akademi', { method: 'POST', timeoutSeconds: 300,
    body: { kind, url, name: name || 'sozlesme', preview } });
}

const unknownKind = (result) => /Bilinmeyen Akademi belgesi/i.test(String(result?.error || ''));

/**
 * Akademi'nin sözleşme PDF'ini önizleme penceresinde açar (sayfalar, yakınlaştırma, Yazdır, PDF kaydet).
 * @param {{kind?: string, url: string, name?: string, opener?: Element}} spec — `kind`: KM_PDF_KINDS değeri
 */
export async function openContractFile(app, { kind = KM_PDF_KINDS.document, url, name = '', opener = null } = {}) {
  if (!url) {
    app.toast('Belge bağlantısı gelmedi.', 'bad');
    return null;
  }
  let result;
  try {
    result = await importPdf(app, { kind, url, name, preview: true });
  } catch (failure) {
    app.toast(problem(failure, 'Belge alınamadı.'), 'bad');
    return null;
  }
  if (!result?.ok) {
    if (unknownKind(result)) {
      try { return await openDirect(app, url, name); } catch (failure) {
        app.toast(problem(failure, 'Belge açılamadı.'), 'bad');
        return null;
      }
    }
    app.toast(result?.error || 'Belge alınamadı.', 'bad');
    return null;
  }
  showPreview(app, result, { opener });
  return result;
}

/** PDF'in kendisini yazıcıya gönderir; yerel yazıcı yoksa önizleme açılır (sebebi orada yazar). */
export async function printContractFile(app, { kind = KM_PDF_KINDS.documents, url, name = '', opener = null } = {}) {
  const direct = canPrintLocally() && (await printerReady().catch(() => ({ ready: false }))).ready;
  if (!direct) return openContractFile(app, { kind, url, name, opener });
  let result;
  try {
    result = await importPdf(app, { kind, url, name, preview: false });
  } catch (failure) {
    app.toast(problem(failure, 'Belge alınamadı.'), 'bad');
    return null;
  }
  if (!result?.ok) {
    if (unknownKind(result)) return openDirect(app, url, name).catch(() => null);
    app.toast(result?.error || 'Belge alınamadı.', 'bad');
    return null;
  }
  try {
    const printer = await printDocument((path, options) => app.coreApi(path, options), result.path, { copies: 1 });
    app.toast(`${result.name} · ${printer} yazıcısına gönderildi.`, 'good');
  } catch (failure) {
    showPreview(app, { ...result, pages: [], previewError: problem(failure, 'Yazdırılamadı.') }, { opener });
  }
  return result;
}

/** Tek sözleşme belgesi (A14) → önizleme. `revision` verilmezse son sürüm. */
export async function openContractDocument(app, contract, kind, { revision = null, opener = null } = {}) {
  try {
    const file = await app.api.get(`/v1/admin/contracts/${contract.id}/documents/${kind}`,
      revision ? { revision } : undefined);
    return openContractFile(app, { kind: KM_PDF_KINDS.document, url: file?.url,
      name: file?.fileName || `${contract.number || 'sozlesme'}-${kind}`, opener });
  } catch (failure) {
    app.toast(problem(failure, 'Belge alınamadı.'), 'bad');
    return null;
  }
}

/**
 * Toplu / seçili türler TEK PDF (A17; büyük iş A18 ile yoklanır). `print` true → yazıcıya, değilse önizleme.
 * Dönüş: Akademi yanıtı (`skipped` dahil) ya da hata/vazgeçişte null. `body` taslaktaki A17 gövdesidir.
 */
export async function buildDocuments(app, body, { print = false, opener = null, key = undefined,
  name = 'sozlesme-belgeleri' } = {}) {
  let job;
  try {
    job = await app.api.post('/v1/admin/contracts/documents', body, { key });
    const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
    let delay = 800;
    const deadline = Date.now() + 180_000;
    while (job?.status === 'queued' && job.jobId && Date.now() < deadline) {
      await wait(delay); // eslint-disable-line no-await-in-loop
      delay = Math.min(delay * 1.5, 4000);
      job = await app.api.get(`/v1/admin/contracts/documents/${job.jobId}`); // eslint-disable-line no-await-in-loop
    }
  } catch (failure) {
    app.toast(problem(failure, 'Belgeler hazırlanamadı.'), 'bad');
    return null;
  }
  if (job?.status !== 'ready' || !job.file?.url) {
    const skipped = job?.skipped?.length ? ` ${job.skipped.length} öğrenci eksik bilgi yüzünden atlandı.` : '';
    app.toast(job?.status === 'queued' ? 'Belgeler hâlâ hazırlanıyor; biraz sonra tekrar deneyin.'
      : `Yazdırılacak belge üretilemedi.${skipped}`, 'bad');
    return job || null;
  }
  const spec = { kind: KM_PDF_KINDS.documents, url: job.file.url, name: job.file.fileName || name, opener };
  if (print) await printContractFile(app, spec); else await openContractFile(app, spec);
  return job;
}

// ------------------------------------------------------------------ tarama yükleme

/**
 * Tarama dosyasını Akademi'ye yükler → dosya kimliği. KM'nin dosya yolu köprüdür (base64, `POST /v1/admin/files`,
 * `kind=contract_scan`, `contractId`); Akademi tek PDF'e birleştirir (A10 `scanFileIds` / A12 `fileIds`).
 */
export async function uploadScan(app, file, contractId, { onProgress } = {}) {
  const result = await app.api.call('POST', '/v1/admin/files', {
    upload: { file, field: 'file', fields: { kind: 'contract_scan', contractId }, onProgress } });
  const id = result.data?.id || result.data?.fileId;
  if (!id) throw new AkademiError({ code: 'contract_scan_invalid', message: `${file.name}: yüklenemedi.` });
  return id;
}

// ------------------------------------------------------------------ girişler

/**
 * "Kayıt ve ödeme planı" sihirbazını açtırır. Sihirbaz 80-km-sihirbaz'ındır (`wizard-*.js`) ve bu olayı dinler:
 * `bbd-akademi:open-contract-wizard` {studentId, planId}. (Modüller arası değil, panel içi gevşek bağ.)
 */
export function requestWizard(studentId, planId = null) {
  window.dispatchEvent(new CustomEvent('bbd-akademi:open-contract-wizard', { detail: { studentId, planId } }));
}

/** Öğrencinin kartını Sözleşmeler bölümünde açar (liste satırı → öğrenci sekmesi; `contractId` o sözleşmeyi seçer). */
export function openStudentContracts(app, studentId, contractId = null) {
  app.go('students', { studentId, tab: 'contracts', ...(contractId ? { contractId } : {}) });
}

/**
 * "Öğrenciler panelinde düzenle": KM ana kaydı (`bbd_students.record`) çekmecede — eksik kimlik alanları orada
 * tamamlanır (K2). Kapanınca `onClosed()` (çağıran tarafı yeniden okur; Akademi aynası 1–3 sn).
 */
export function editInStudentsPanel(app, { kmId, name = '', onClosed } = {}) {
  const drawRecord = app.capability?.('bbd_students.record');
  if (typeof drawRecord !== 'function' || !kmId) {
    app.toast('Öğrenciler paneli bu kullanıcı için açık değil; kimlik bilgilerini yetkili kişi tamamlamalı.', 'bad');
    return null;
  }
  let instance = null;
  const panel = openDrawer(app, { title: name || 'Öğrenci', subtitle: 'Kimlik, adres ve veliler (sözleşme için)',
    onClose: () => { instance?.destroy(); onClosed?.(); } });
  panel.node.classList.add('ak-stu-drawer');
  const slot = h('div', 'ak-stu-record');
  panel.body.append(slot);
  instance = drawRecord(slot, { kantinId: kmId, capability: app.capability, dialogRoot: app.root,
    toast: (text) => app.toast(text, 'good'), onRemoved: () => panel.close(),
    onChanged: () => emitContractEvent({ type: 'contract.party_changed', studentId: null, kmId }) });
  return panel;
}

/** "Kayıt ve ödeme planı" düğmesi (sözleşme oluşturma izni yoksa gerekçeli kapalı; kurumda kapalıysa gizli). */
export function wizardButton(app, studentId, { label = 'Kayıt ve ödeme planı', variant = '', planId = null } = {}) {
  if (app.api.featureOff?.('admin_createContract')) {
    const hidden = button(label, { variant, disabled: true });
    hidden.hidden = true;
    return hidden;
  }
  if (!app.api.can('admin_createContract')) {
    return button(label, { variant, disabled: true,
      title: 'Sözleşme oluşturma yetkiniz yok (yöneticinizden izin isteyin).' });
  }
  return button(label, { variant,
    title: 'Öğrenci ve veli bilgileri, hizmetler, ödeme planı ve sözleşme — tek ekranda, tek onay.',
    onClick: () => requestWizard(studentId, planId) });
}
