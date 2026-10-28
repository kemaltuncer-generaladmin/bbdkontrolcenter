// BBD Akademi — Denemeler › Optik formlar (Akademi README §14 madde 141, iş 82).
//
// BBD Optik uygulaması (yalnız yönetici, telefonla seri okuma) formları
// Akademi'ye gönderir. Öğrencisi ya da kitapçığı belirlenemeyen form HATA
// DEĞİLDİR: `pending` olarak kayıtlıdır, sonucu yazılmamıştır (kullanıcı:
// "boş gönderir km de düzenleriz"). Bu sekme:
//
//   · Eşleşmemiş optikler — bekleyen formlar (sayı rozeti sekme başlığında);
//     satır açılınca solda form görüntüsü (yakınlaştırılabilir), sağda öğrenci
//     (adaylar + ad araması `admin_listOmrStudents`), kitapçık, ALAN (yalnız
//     AYT) ve isteğe bağlı cevap düzeltmesi → "Kaydet" (`admin_updateOmrSheet`).
//   · Kaydedilenler — sonucu yazılmış bütün optik formlar, görüntüleriyle.
//
// Sonuçlar sekmesindeki "Optik" rozeti de buradaki görüntü penceresini açar
// (`omrSheetIndex` + `openOmrSheet`).
//
// ANLIK: Akademi'nin `exams.omr` SSE olayı kişiye bağlıdır ve KM vekili
// (entegrasyon anahtarı) SSE açamaz (65-deneme-km, 80-km kararları). Bunun
// yerine deneme ayrıntısı açıkken küçük bir imza yoklaması koşar
// (`omrSignature`, exams.js; gizli sekmede durur) ve değişiklikte bu liste +
// sonuçlar yenilenir; kendi kaydımız yanıtla anında çizilir.
//
// Görüntü bağlantısı imzalı ve kısa ömürlüdür: KM köprüsü (`fetchFile`) ile
// indirilip yerel blob olarak gösterilir; süresi geçmişse liste yeniden okunur.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, tabBar } from '../../../ui-kit/layout.js';
import { newKey, problem } from '../api.js';
import {
  actionButton, confirmAction, fmtDateTime, gradeText, openDrawer, pagedList, searchBox, selectBox, toolbar,
} from '../common.js';
import { netText } from './exams-shared.js';

const sheetsPath = (examId) => `/v1/admin/exams/${examId}/omr/sheets`;

export const OMR_REASONS = {
  no_student: ['Öğrenci yok', 'warn', 'Kimlik okunamadı ya da hiçbir öğrenciyle eşleşmedi.'],
  ambiguous_student: ['Belirsiz öğrenci', 'warn', 'Okunan numara birden çok öğrenciye çıktı (kardeşler); adaylardan seçin.'],
  unknown_student: ['Öğrenci bulunamadı', 'bad', 'Seçilen öğrenci yok ya da pasif.'],
  no_booklet: ['Kitapçık boş', 'warn', 'Formda kitapçık türü işaretlenmemiş.'],
  unknown_booklet: ['Kitapçık denemede yok', 'bad', 'Okunan kitapçık bu denemede tanımlı değil.'],
  no_answer_key: ['Cevap anahtarı yok', 'bad', 'Bu kitapçığın cevap anahtarı girilmemiş; girince "Kaydet" ile yeniden deneyin.'],
  no_alan_exam: ['ALAN denemesi yok', 'bad', 'Formdaki ALAN için aynı gün yapılmış AYT denemesi yok.'],
};

const ID_KIND = { phone: 'Cep tel.', tc: 'T.C. kimlik' };
const MATCHED_BY = { student: 'öğrencinin numarası', guardian: 'veli numarası', tc: 'T.C. kimlik no' };
const ALAN = { SAY: 'Sayısal', EA: 'Eşit ağırlık', SOZ: 'Sözel', DIL: 'Dil' };
const SHEET_STATUS = { pending: ['Bekliyor', 'warn'], saved: ['Kaydedildi', 'good'] };

/** Okuma bayrağı → Türkçe (serbest metin; bilinmeyen olduğu gibi). */
export function flagText(flag) {
  const parts = String(flag || '').split(':');
  if (parts[0] === 'double_mark' && parts.length >= 3) return `Çift işaret: ${parts[1]}. sütun ${parts[2]}. satır`;
  if (parts[0] === 'double_mark') return 'Çift işaret';
  if (parts[0] === 'low_confidence') {
    const where = { id: 'kimlik', booklet: 'kitapçık', alan: 'ALAN', answers: 'cevaplar' }[parts[1]] || parts[1] || '';
    return `Düşük güven${where ? `: ${where}` : ''}`;
  }
  if (parts[0] === 'blank_id') return 'Kimlik boş';
  if (parts[0] === 'blank_booklet') return 'Kitapçık boş';
  return String(flag || '');
}

export function reasonBadges(reasons) {
  const node = h('div', 'ak-omr-reasons');
  for (const reason of reasons || []) {
    const [label, tone, hint] = OMR_REASONS[reason] || [reason, 'dim', ''];
    const chip = badge(label, tone);
    if (hint) chip.title = hint;
    node.append(chip);
  }
  return node;
}

function idText(sheet) {
  if (!sheet?.idMasked) return 'Okunamadı';
  return `${ID_KIND[sheet.idKind] || 'Kimlik'} ${sheet.idMasked}`;
}

function studentLine(student) {
  return [student?.className, student?.gradeLevel ? gradeText(student.gradeLevel) : '']
    .filter((part) => part && part !== '—').join(' · ');
}

function omrStudentCell(student) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', student?.displayName || '—'), h('small', '', studentLine(student)));
  return node;
}

function readTime(sheet) {
  return fmtDateTime(sheet?.readAt || sheet?.createdAt);
}

// ------------------------------------------------------------------ sekme

/**
 * "Optik formlar" sekmesi. `onCounts({pending, saved})` sekme başlığının
 * rozetini besler; `onChanged()` sonuç yazıldığında (sonuçlar + başlık yenilenir).
 */
export function omrTab(app, exam, { onChanged, onCounts } = {}) {
  const node = h('div', 'ak-stack');
  const state = { status: 'pending', includeReplaced: false };
  let counts = null;

  const inner = tabBar([
    { key: 'pending', label: 'Eşleşmemiş optikler' },
    { key: 'saved', label: 'Kaydedilenler' },
  ], state.status, (key) => { state.status = key; paintTools(); swap(); });

  const replaced = h('label', 'ak-inline ak-ex-toggle');
  const replacedCheck = h('input', 'kit-check');
  replacedCheck.type = 'checkbox';
  replacedCheck.addEventListener('change', () => { state.includeReplaced = replacedCheck.checked; lists.saved.reload(); });
  replaced.append(replacedCheck, h('span', '', 'Yerine yenisi okunanları da göster'));
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => reload() });
  const intro = h('div');

  function paintTools() {
    replaced.hidden = state.status !== 'saved';
    intro.replaceChildren(state.status === 'pending'
      ? alertBox('BBD Optik uygulamasıyla okunup öğrencisi ya da kitapçığı belirlenemeyen formlar burada bekler; '
        + 'sonuçları henüz yazılmadı. Satırı açın, form görüntüsüne bakarak öğrenciyi ve kitapçığı seçip '
        + '"Kaydet"e basın — sonuç hemen yazılır.', 'info')
      : alertBox('Optik uygulamasıyla okunup sonucu yazılmış formlar. Satırı açınca form görüntüsü, okunan cevaplar '
        + 've düzeltme açılır; düzeltme sonucu yeniden hesaplar.', 'info'));
  }

  const open = (row) => openOmrSheet(app, exam, row, { onSaved: (sheet) => saved(sheet) });
  const editButton = (row, label) => {
    const control = actionButton(app, 'admin_updateOmrSheet', label, {
      variant: row.status === 'pending' ? 'primary' : 'ghost', onClick: () => open(row) });
    control.addEventListener('click', (event) => event.stopPropagation());
    return control;
  };

  const pendingColumns = [
    { key: 'id', label: 'Okunan kimlik', width: 'minmax(150px, 1fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('b', '', idText(row)), h('small', '', row.candidates?.length
        ? `${row.candidates.length} aday: ${row.candidates.slice(0, 3).map((item) => item.displayName).join(', ')}`
        : 'Aday yok'));
      return cell;
    } },
    { key: 'reasons', label: 'Neden', width: 'minmax(180px, 1.4fr)', cell: (row) => reasonBadges(row.reasons) },
    { key: 'booklet', label: 'Kit.', width: '48px', cell: (row) => row.booklet || '—' },
    { key: 'flags', label: 'Okuma notu', width: 'minmax(120px, 1fr)', cell: (row) => flagsCell(row.flags) },
    { key: 'readAt', label: 'Okunma', width: '132px', cell: (row) => h('small', '', readTime(row)) },
    { key: 'actions', label: '', width: '112px', cell: (row) => editButton(row, 'Düzelt') },
  ];
  const savedColumns = [
    { key: 'student', label: 'Öğrenci', width: 'minmax(170px, 1.5fr)', cell: (row) => omrStudentCell(row.student) },
    { key: 'booklet', label: 'Kit.', width: '48px', cell: (row) => row.booklet || '—' },
    { key: 'net', label: 'Toplam net', align: 'num', width: '96px', cell: (row) => (row.total
      ? h('b', '', netText(row.total.net)) : '—') },
    { key: 'dyb', label: 'D / Y / B', align: 'num', width: '96px', cell: (row) => (row.total
      ? h('small', '', `${row.total.correct}/${row.total.wrong}/${row.total.blank}`) : '—') },
    { key: 'id', label: 'Okunan kimlik', width: 'minmax(130px, 1fr)', cell: (row) => h('small', 'ak-muted', idText(row)) },
    { key: 'readAt', label: 'Okunma', width: '132px', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('small', '', readTime(row)));
      if (row.replacedAt) cell.append(badge('Yerine yenisi okundu', 'dim'));
      return cell;
    } },
    { key: 'actions', label: '', width: '112px', cell: (row) => editButton(row, 'Aç') },
  ];

  const makeList = (status, columns, empty) => pagedList({
    app,
    path: sheetsPath(exam.id),
    query: () => ({ status, includeReplaced: status === 'saved' && state.includeReplaced ? true : undefined }),
    columns,
    rowKey: (row) => row.sheetId,
    dense: true,
    onRow: (row) => open(row),
    empty,
  });
  const lists = {
    pending: makeList('pending', pendingColumns, { title: 'Eşleşmemiş optik yok',
      text: 'Optik uygulamasından gelen bütün formların öğrencisi ve kitapçığı belli; sonuçları yazıldı.' }),
    saved: makeList('saved', savedColumns, { title: 'Henüz optikle kaydedilen form yok',
      text: 'BBD Optik uygulamasında bu denemeyi seçip formları okutun; gönderilenler burada görünür.' }),
  };
  const slot = h('div');
  const loaded = { pending: false, saved: false };

  function swap() {
    slot.replaceChildren(lists[state.status].node);
    if (!loaded[state.status]) { loaded[state.status] = true; lists[state.status].reload(); }
  }

  node.append(toolbar(inner.node, h('span', 'kit-spacer'), replaced, refresh), intro, slot);
  paintTools();

  async function loadCounts() {
    try {
      const data = await app.api.get(sheetsPath(exam.id), { status: 'pending', limit: 1 });
      setCounts(data?.counts || null);
    } catch { /* rozet eski kalır */ }
  }

  function setCounts(next) {
    if (!next) return;
    counts = next;
    inner.badge('pending', counts.pending || 0);
    inner.badge('saved', counts.saved || 0);
    onCounts?.(counts);
  }

  function reload() {
    loaded.pending = false;
    loaded.saved = false;
    swap();
    return loadCounts();
  }

  /** Kaydetme yanıtıyla anında çizim: satır bekleyenlerden düşer, sayaçlar ve sonuçlar yenilenir. */
  function saved(sheet) {
    if (sheet.status === 'saved') {
      lists.pending.remove((row) => row.sheetId === sheet.sheetId);
      loaded.saved = false;
      onChanged?.();
    } else {
      lists.pending.replace((row) => row.sheetId === sheet.sheetId, sheet);
    }
    if (state.status === 'saved') swap();
    loadCounts();
  }

  return {
    node,
    reload,
    /** Yoklamadan gelen sayaçlar (ek istek atmadan rozet). */
    counts: (next) => setCounts(next),
    destroy() { lists.pending.destroy(); lists.saved.destroy(); },
  };
}

function flagsCell(flags) {
  if (!flags?.length) return h('small', 'ak-muted', '—');
  const cell = h('small', '', flags.slice(0, 2).map(flagText).join(' · ') + (flags.length > 2 ? ` +${flags.length - 2}` : ''));
  cell.title = flags.map(flagText).join('\n');
  return cell;
}

// ------------------------------------------------- sonuçlar: "Optik" rozeti

/**
 * Denemenin KAYITLI optik formları → öğrenci kimliği → form. Sonuçlar
 * sekmesindeki "Optik" rozeti bunu kullanır (tek liste okuması, satır başına
 * istek yok). Yetki yoksa boş.
 */
export async function omrSheetIndex(app, examId) {
  if (!app.can('admin_listOmrSheets')) return new Map();
  const rows = await app.api.all(sheetsPath(examId), { status: 'saved' }, { limit: 50, max: 3000 });
  const index = new Map();
  for (const row of rows) if (row.studentId && !row.replacedAt) index.set(row.studentId, row);
  return index;
}

/** Sonuç satırındaki küçük "Optik" rozeti (tıklanınca form görüntüsü). */
export function omrBadge(app, exam, sheet, { onSaved } = {}) {
  const control = h('button', 'ak-omr-badge');
  control.type = 'button';
  control.textContent = 'Optik';
  control.title = sheet ? `Optik formla okundu (${readTime(sheet)}) — form görüntüsünü aç` : 'Optik formla okundu';
  control.addEventListener('click', (event) => {
    event.stopPropagation();
    if (sheet) openOmrSheet(app, exam, sheet, { onSaved });
  });
  if (!sheet) control.disabled = true;
  return control;
}

// --------------------------------------------------- görüntü + düzeltme

/** İmzalı bağlantının süresi geçmek üzereyse formu listeden yeniden okur. */
async function freshSheet(app, exam, sheet) {
  const expires = Date.parse(sheet?.image?.expiresAt || '');
  if (!sheet?.image?.url || !Number.isFinite(expires) || expires - Date.now() > 30_000) return sheet;
  const iterator = app.api.pages(sheetsPath(exam.id), { status: sheet.status,
    includeReplaced: sheet.replacedAt ? true : undefined }, { limit: 50 });
  for (let page = 0; page < 40 && !iterator.done; page += 1) {
    const items = await iterator.next(); // eslint-disable-line no-await-in-loop
    const hit = items.find((item) => item.sheetId === sheet.sheetId);
    if (hit) return hit;
  }
  return sheet;
}

/** Yakınlaştırılabilir form görüntüsü (düğmeler, Ctrl + tekerlek, çift tık, sürükleyerek kaydırma). */
function imageViewer(app) {
  const node = h('div', 'ak-omr-viewer');
  const frame = h('div', 'ak-omr-frame');
  const img = h('img');
  img.alt = 'Okunan optik form';
  img.draggable = false;
  frame.append(h('div', 'ak-muted ak-omr-frame-note', 'Görüntü yükleniyor…'));
  let zoom = 1;
  let blobUrl = '';

  const label = h('span', 'ak-muted ak-omr-zoom', '%100');
  function apply(next, anchor = null) {
    const before = zoom;
    zoom = Math.min(5, Math.max(1, Math.round(next * 100) / 100));
    img.style.width = `${zoom * 100}%`;
    label.textContent = `%${Math.round(zoom * 100)}`;
    if (anchor && before !== zoom) {
      // İmlecin altındaki nokta yerinde kalsın.
      const ratio = zoom / before;
      frame.scrollLeft = (frame.scrollLeft + anchor.x) * ratio - anchor.x;
      frame.scrollTop = (frame.scrollTop + anchor.y) * ratio - anchor.y;
    }
  }
  const tools = h('div', 'ak-omr-viewer-tools');
  const zoomOut = button('−', { variant: 'ghost', title: 'Uzaklaştır', onClick: () => apply(zoom / 1.25) });
  const zoomIn = button('+', { variant: 'ghost', title: 'Yakınlaştır', onClick: () => apply(zoom * 1.25) });
  const fit = button('Sığdır', { variant: 'ghost', onClick: () => apply(1) });
  const big = button('Yeni pencerede aç', { variant: 'ghost', onClick: () => { if (blobUrl) window.open(blobUrl, '_blank'); } });
  big.disabled = true;
  tools.append(zoomOut, label, zoomIn, fit, h('span', 'kit-spacer'), big);
  node.append(tools, frame);

  frame.addEventListener('wheel', (event) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    const box = frame.getBoundingClientRect();
    apply(zoom * (event.deltaY < 0 ? 1.15 : 1 / 1.15), { x: event.clientX - box.left, y: event.clientY - box.top });
  }, { passive: false });
  img.addEventListener('dblclick', (event) => {
    const box = frame.getBoundingClientRect();
    apply(zoom > 1 ? 1 : 2.5, { x: event.clientX - box.left, y: event.clientY - box.top });
  });
  let drag = null;
  frame.addEventListener('pointerdown', (event) => {
    if (zoom <= 1 || event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, left: frame.scrollLeft, top: frame.scrollTop };
    frame.setPointerCapture?.(event.pointerId);
    frame.classList.add('dragging');
  });
  frame.addEventListener('pointermove', (event) => {
    if (!drag) return;
    frame.scrollLeft = drag.left - (event.clientX - drag.x);
    frame.scrollTop = drag.top - (event.clientY - drag.y);
  });
  const stop = () => { drag = null; frame.classList.remove('dragging'); };
  frame.addEventListener('pointerup', stop);
  frame.addEventListener('pointercancel', stop);

  async function load(current) {
    if (!current?.image?.url) {
      frame.replaceChildren(h('div', 'ak-muted ak-omr-frame-note',
        'Form görüntüsü henüz gelmedi (uygulama görüntüyü formdan sonra da yükleyebilir).'));
      return;
    }
    try {
      const file = await app.api.fetchFile(current.image.url, `optik-${current.sheetId}.jpg`);
      if (blobUrl) URL.revokeObjectURL(blobUrl);
      blobUrl = URL.createObjectURL(file.blob);
      img.src = blobUrl;
      frame.replaceChildren(img);
      big.disabled = false;
      apply(1);
    } catch (failure) {
      frame.replaceChildren(h('div', 'ak-muted ak-omr-frame-note', `Görüntü açılamadı: ${problem(failure)}`));
    }
  }
  return { node, load, destroy() { if (blobUrl) URL.revokeObjectURL(blobUrl); blobUrl = ''; } };
}

/**
 * Form çekmecesi: solda görüntü, sağda öğrenci / kitapçık / ALAN / cevaplar.
 * Bekleyen formda "Kaydet" sonucu yazar; kayıtlı formda düzeltir.
 * `onSaved(sheet, replaced)` — yanıttaki güncel form.
 */
export function openOmrSheet(app, exam, given, { onSaved } = {}) {
  let sheet = given;
  const booklets = exam.bookletTypes || [];
  const draft = { studentId: null, student: null, booklet: null, alan: undefined, columns: null };
  let viewer = null;
  let searchToken = 0;
  let search = null;

  const panel = openDrawer(app, {
    title: sheet.student?.displayName || 'Eşleşmemiş optik',
    subtitle: `${exam.name} · okunma ${readTime(sheet)}`,
    onClose: () => { viewer?.destroy(); search?.destroy(); },
  });
  panel.node.classList.add('ak-omr-drawer');
  const left = h('div', 'ak-omr-left');
  const right = h('div', 'ak-omr-right ak-stack');
  const grid = h('div', 'ak-omr-grid');
  grid.append(left, right);
  panel.body.append(grid);

  viewer = imageViewer(app);
  left.append(viewer.node);
  freshSheet(app, exam, sheet).then((fresh) => { sheet = { ...sheet, image: fresh.image }; viewer.load(sheet); })
    .catch(() => viewer.load(sheet));

  // Düzen (sütun → bölüm): cevap düzeltmesinde bölüm adlarını ve ALAN'ı verir.
  let layout = null;
  const layoutReady = app.api.get(`/v1/admin/exams/${sheet.examId || exam.id}/omr/layout`)
    .then((data) => { layout = data; }).catch(() => { layout = null; });

  const statusSlot = h('div', 'ak-stack');
  const studentSlot = h('div', 'ak-stack');
  const bookletSlot = h('div', 'ak-stack');
  const alanSlot = h('div', 'ak-stack');
  const answersSlot = h('div', 'ak-stack');
  const error = h('div', 'kit-field-error');
  const save = actionButton(app, 'admin_updateOmrSheet', 'Kaydet', { variant: 'primary', onClick: () => submit() });
  right.append(statusSlot, studentSlot, bookletSlot, alanSlot, answersSlot, error, toolbar(h('span', 'kit-spacer'), save));

  function paintStatus() {
    const head = h('div', 'ak-omr-status');
    head.append(badge(...(SHEET_STATUS[sheet.status] || [sheet.status, 'dim'])));
    if (sheet.replacedAt) head.append(badge('Yerine yenisi okundu', 'dim'));
    if (sheet.total) head.append(h('span', '', `Net ${netText(sheet.total.net)} · ${sheet.total.correct} D / ${sheet.total.wrong} Y / ${sheet.total.blank} B`));
    const facts = h('div', 'ak-omr-facts');
    facts.append(h('span', 'ak-muted', `Okunan kimlik: ${idText(sheet)}`));
    if (sheet.flags?.length) facts.append(h('span', 'ak-muted', `Okuma notları: ${sheet.flags.map(flagText).join(' · ')}`));
    statusSlot.replaceChildren(head, reasonBadges(sheet.reasons), facts);
    if (sheet.replacedAt) {
      statusSlot.append(alertBox(`Bu öğrenci için ${fmtDateTime(sheet.replacedAt)} tarihinde başka bir form okundu; `
        + 'geçerli sonuç o formdan gelir.', 'warn'));
    }
  }

  // ------------------------------------------------------------ öğrenci
  function chosenStudent() {
    return draft.student || sheet.student || null;
  }

  function paintStudent() {
    studentSlot.replaceChildren(h('div', 'ak-omr-label', 'Öğrenci'));
    const current = chosenStudent();
    const chosen = h('div', 'ak-omr-chosen');
    if (current) {
      chosen.append(omrStudentCell(current));
      if (draft.studentId && draft.studentId !== sheet.studentId) chosen.append(badge('Yeni seçim', 'info'));
    } else {
      chosen.append(h('span', 'ak-muted', 'Seçilmedi — adaylardan ya da aramayla seçin.'));
    }
    studentSlot.append(chosen);

    const candidates = sheet.candidates || [];
    if (candidates.length) {
      const list = h('div', 'ak-omr-options');
      for (const item of candidates) {
        list.append(optionButton(item, {
          meta: [studentLine(item), `${MATCHED_BY[item.matchedBy] || item.matchedBy} eşleşti`].filter(Boolean).join(' · '),
          warn: [item.inExamAudience === false ? 'Denemenin sınıflarında değil' : '',
            item.existingResult ? 'Sonucu var' : ''].filter(Boolean),
          existing: Boolean(item.existingResult),
        }));
      }
      studentSlot.append(h('small', 'ak-muted', 'Okunan numarayla eşleşen adaylar'), list);
    }

    const results = h('div', 'ak-omr-options');
    search?.destroy();
    search = searchBox({ placeholder: 'Öğrenci ara (ad, okul no)', label: 'Öğrenci ara', onSearch: async (text) => {
      const mine = ++searchToken;
      if (text.length < 2) { results.replaceChildren(); return; }
      try {
        const data = await app.api.get(`/v1/admin/exams/${exam.id}/omr/students`, { q: text, limit: 12 });
        if (mine !== searchToken) return;
        const items = data?.items || [];
        results.replaceChildren(...(items.length ? items.map((item) => optionButton(item, {
          meta: studentLine(item), warn: item.hasResult ? ['Sonucu var'] : [], existing: item.hasResult,
        })) : [h('div', 'ak-muted', 'Denemenin öğrencileri arasında eşleşen yok.')]));
      } catch (failure) {
        if (mine === searchToken) results.replaceChildren(h('div', 'ak-muted', problem(failure)));
      }
    } });
    studentSlot.append(search.node, results);
  }

  function optionButton(item, { meta = '', warn = [], existing = false }) {
    const row = h('button', 'ak-omr-option');
    row.type = 'button';
    const on = (draft.studentId || sheet.studentId) === item.studentId;
    row.classList.toggle('on', on);
    row.append(h('b', '', item.displayName || '—'), h('span', 'ak-muted', meta));
    for (const text of warn) row.append(badge(text, 'warn'));
    row.addEventListener('click', () => {
      draft.studentId = item.studentId;
      draft.student = { studentId: item.studentId, displayName: item.displayName, className: item.className,
        gradeLevel: item.gradeLevel };
      draft.existing = existing && item.studentId !== sheet.studentId;
      paintStudent();
    });
    return row;
  }

  // ----------------------------------------------------------- kitapçık
  function paintBooklet() {
    bookletSlot.replaceChildren();
    if (booklets.length <= 1) {
      if (sheet.reasons?.includes('unknown_booklet') && booklets.length === 1) draft.booklet = booklets[0];
      if (sheet.reasons?.includes('unknown_booklet')) {
        bookletSlot.append(h('div', 'ak-omr-label', 'Kitapçık'),
          h('span', 'ak-muted', `Denemede tek kitapçık var (${booklets[0] || '—'}); kayıtta o kullanılır.`));
      }
      return;
    }
    const current = draft.booklet || sheet.booklet;
    const row = h('div', 'ak-omr-segment');
    row.setAttribute('role', 'radiogroup');
    row.setAttribute('aria-label', 'Kitapçık');
    for (const letter of booklets) {
      const option = h('button', `kit-chip${current === letter ? ' on' : ''}`, `${letter} kitapçığı`);
      option.type = 'button';
      option.setAttribute('role', 'radio');
      option.setAttribute('aria-checked', current === letter ? 'true' : 'false');
      option.addEventListener('click', () => { draft.booklet = letter; paintBooklet(); });
      row.append(option);
    }
    bookletSlot.append(h('div', 'ak-omr-label', 'Kitapçık'), row);
    if (!current) bookletSlot.append(h('small', 'ak-muted', 'Formda kitapçık boş: görüntüye bakıp seçin.'));
  }

  // --------------------------------------------------------------- ALAN
  function paintAlan() {
    alanSlot.replaceChildren();
    if (!layout?.readsAlan) return;
    const current = draft.alan !== undefined ? draft.alan : sheet.alan;
    const box = selectBox([{ value: '', label: 'Okunmadı (bu deneme)' },
      ...Object.entries(ALAN).map(([value, label]) => ({ value, label }))],
    current || '', (value) => { draft.alan = value || null; }, { label: 'ALAN' });
    alanSlot.append(box.node);
    if (layout.alanExams?.length) {
      alanSlot.append(h('small', 'ak-muted', `ALAN başka bir AYT denemesini gösteriyorsa sonuç o denemeye yazılır: ${
        layout.alanExams.map((item) => `${ALAN[item.alan] || item.alan} → ${item.examName}`).join(' · ')}`));
    }
  }

  // ------------------------------------------------------------ cevaplar
  function columnsNow() {
    return draft.columns || [...(sheet.columns || [])];
  }

  function paintAnswers() {
    answersSlot.replaceChildren();
    const details = h('details', 'ak-omr-answers');
    const summary = h('summary', '', 'Cevapları düzelt (isteğe bağlı)');
    details.append(summary);
    const body = h('div', 'ak-stack');
    details.append(body);
    const columns = columnsNow();
    while (columns.length < 4) columns.push('');
    const write = (index, value) => {
      const next = columnsNow();
      while (next.length < 4) next.push('');
      next[index] = value;
      draft.columns = next;
    };
    const clean = (text) => text.toUpperCase().replace(/[^A-E *-]/g, '');
    const rows = layout?.sections?.length ? layout.sections : null;
    body.append(h('small', 'ak-muted', 'A–E işaret, “-” ya da boşluk boş, “*” çift işaret (boş sayılır). '
      + 'Değiştirdiğiniz bölüm kayıtta yeniden puanlanır.'));
    if (rows) {
      for (const section of rows) {
        const start = section.startRow - 1;
        const index = section.column - 1;
        const current = (columns[index] || '').padEnd(start + section.count, ' ').slice(start, start + section.count);
        const input = h('input', 'kit-input ak-ex-answers');
        input.value = current;
        input.maxLength = section.count;
        input.spellcheck = false;
        input.setAttribute('aria-label', `${section.sectionName} cevapları`);
        const counter = h('small', 'ak-muted', `${section.count} soru · ${section.column}. sütun, satır ${section.startRow}–${section.startRow + section.count - 1}`);
        input.addEventListener('input', () => {
          input.value = clean(input.value);
          const column = (columnsNow()[index] || '').padEnd(start + section.count, ' ');
          const value = input.value.padEnd(section.count, ' ');
          write(index, (column.slice(0, start) + value + column.slice(start + section.count)).replace(/\s+$/, ''));
        });
        const row = h('div', 'ak-omr-answer-row');
        const label = h('span', 'ak-cell-stack');
        label.append(h('b', '', section.sectionName), counter);
        row.append(label, input);
        body.append(row);
      }
    } else {
      body.append(alertBox('Deneme bölümleri forma yerleştirilemedi; ham sütunlar gösteriliyor.', 'warn'));
      const sizes = (layout?.formColumns || []).map((item) => item.rows);
      columns.slice(0, 4).forEach((value, index) => {
        const input = h('input', 'kit-input ak-ex-answers');
        input.value = value || '';
        input.maxLength = sizes[index] || 46;
        input.spellcheck = false;
        input.setAttribute('aria-label', `${index + 1}. sütun`);
        input.addEventListener('input', () => { input.value = clean(input.value); write(index, input.value.replace(/\s+$/, '')); });
        const row = h('div', 'ak-omr-answer-row');
        row.append(h('b', '', `${index + 1}. sütun`), input);
        body.append(row);
      });
    }
    answersSlot.append(details);
  }

  function paintAll() {
    panel.setTitle(chosenStudent()?.displayName || (sheet.status === 'pending' ? 'Eşleşmemiş optik' : 'Optik form'));
    paintStatus();
    paintStudent();
    paintBooklet();
    paintAlan();
    paintAnswers();
    save.textContent = sheet.status === 'pending' ? 'Kaydet' : 'Düzeltmeyi kaydet';
  }
  paintAll();
  layoutReady.then(() => { paintAlan(); paintAnswers(); });

  async function submit() {
    error.textContent = '';
    const body = {};
    if (draft.studentId && draft.studentId !== sheet.studentId) body.studentId = draft.studentId;
    if (draft.booklet && draft.booklet !== sheet.booklet) body.booklet = draft.booklet;
    if (draft.alan !== undefined && draft.alan !== (sheet.alan || null)) body.alan = draft.alan;
    if (draft.columns && JSON.stringify(draft.columns) !== JSON.stringify(sheet.columns || [])) body.columns = draft.columns;
    if (!chosenStudent()) { error.textContent = 'Önce öğrenciyi seçin.'; return; }
    if (booklets.length > 1 && !(draft.booklet || sheet.booklet)) { error.textContent = 'Kitapçığı seçin (A / B).'; return; }
    if (sheet.status === 'saved' && !Object.keys(body).length) { error.textContent = 'Değişiklik yok.'; return; }
    if (draft.existing) {
      const ok = await confirmAction(app, {
        title: `${draft.student?.displayName || 'Öğrenci'}: sonucu değiştirilsin mi?`,
        description: 'Bu öğrencinin denemede zaten sonucu var. Kaydederseniz sonucu bu formdan yeniden hesaplanır.',
        confirmLabel: 'Değiştir',
      });
      if (!ok) return;
    }
    save.disabled = true;
    try {
      const result = await app.api.patch(`/v1/admin/exams/${sheet.examId || exam.id}/omr/sheets/${sheet.sheetId}`, body,
        { key: newKey('omr') });
      const next = result?.sheet || sheet;
      const replaced = Boolean(result?.replaced);
      sheet = { ...next, image: next.image || sheet.image };
      draft.studentId = null; draft.student = null; draft.booklet = null; draft.alan = undefined; draft.columns = null;
      draft.existing = false;
      onSaved?.(sheet, replaced);
      if (sheet.status === 'saved') {
        app.toast(`${sheet.student?.displayName || 'Öğrenci'}: sonuç yazıldı${sheet.total ? ` (net ${netText(sheet.total.net)})` : ''}`
          + `${replaced ? '; önceki sonucun yerine geçti' : ''}.`, 'good');
        panel.close();
        return;
      }
      app.toast('Form kaydedildi ama hâlâ eksik: nedenlere bakın.', 'warn');
      paintAll();
    } catch (failure) {
      error.textContent = problem(failure);
    } finally {
      save.disabled = false;
    }
  }

  return panel;
}

/** Deneme ayrıntısının yoklama imzası: sayaçlar + en yeni formun değişimi (tek küçük okuma). */
export async function omrSignature(app, examId) {
  const data = await app.api.get(sheetsPath(examId), { status: 'all', includeReplaced: true, limit: 1 });
  const top = data?.items?.[0];
  return {
    counts: data?.counts || { pending: 0, saved: 0 },
    key: [data?.counts?.pending ?? 0, data?.counts?.saved ?? 0, top?.sheetId || '', top?.updatedAt || ''].join('|'),
  };
}
