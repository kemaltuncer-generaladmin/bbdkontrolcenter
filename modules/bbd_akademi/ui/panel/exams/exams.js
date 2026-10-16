// BBD Akademi — Denemeler (F-A10, F-E1…F-E8).
//
// Deneme tanımı (tür, yayın, kitapçık, bölümler + soru sayıları, net kuralı),
// sonuç tablosu ve tekil giriş, TOPLU YÜKLEME sihirbazı (XLSX/CSV → sütun
// eşleme → önizleme → öğrenci eşleşmesi → kısmi kayıt → geri alma), cevap
// anahtarı + soru→konu eşlemesi, deneme analizi, dışa aktarım ve çoklu deneme
// analizi (sınıf/kurum trendi, öğrenci karşılaştırma).
//
// GEÇMİŞ DENEME (F-E4) ayrı bir ekran değildir: geçmiş tarihli bir deneme
// "geçmiş kayıt" işaretiyle tanımlanır, sonuçları toplu yüklenir ya da tek tek
// girilir. Geçmiş kayıtta bildirim GİTMEZ; trend ve analizlere katılır.
// İşareti koymak `exams.import` izni ister (Akademi 403
// `historical_requires_import_permission`).
//
// Net, sıra ve puan Akademi'de hesaplanır; ekrandaki net tahmini yalnız yol
// gösterir. Parçalar: exams-results.js (sonuç + dışa aktarım), exams-import.js
// (sihirbaz), exams-keys.js (anahtar + konu), exams-analytics.js (analiz).

import { blockedButton, button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, tabBar } from '../../../ui-kit/layout.js';
import { AkademiError } from '../api.js';
import { programLabel, sortPrograms } from '../shared/curriculum-picker.js';
import {
  actionButton, dateInput, fmtDate, fmtDateTime, formDialog, kv, loader, multiChips, num, optionsOf,
  pagedList, sectionHead, selectBox, statusBadge, studentSelect, todayIst, toolbar,
} from '../common.js';
import {
  EXAM_STATUS, EXAM_TYPES, FILL_STATUS, SCORING_RULES, examFamily, examTemplate, examTitleCell,
  scoringText, sectionLabel, sortedSections, totalQuestions,
} from './exams-shared.js';
import { exportTab, resultsTab } from './exams-results.js';
import { importTab } from './exams-import.js';
import { keysTab } from './exams-keys.js';
import { analysisTab, analyticsPanel } from './exams-analytics.js';
import { liveTab } from './exams-live.js';
import { aliasesPanel, matchingTab, publisherWizard } from './exams-publisher.js';
import {
  APP_STATE, appDraftFromExam, appFacts, defaultAppDraft, deliveryPart, validateApp,
} from './exams-app-logic.js';
import { appSettings, deliveryChoice } from './exams-app-form.js';

const BOOKLET_LETTERS = ['A', 'B', 'C', 'D', 'E', 'F'];
const DETAIL_TABS = ['live', 'results', 'import', 'keys', 'matching', 'analysis', 'export'];

export function section(app) {
  loadStyles(import.meta.url, 'exams.css');
  const node = h('div', 'ak-stack ak-ex');
  const main = h('div', 'ak-stack');
  const detailSlot = h('div', 'ak-stack');
  detailSlot.hidden = true;
  node.append(main, detailSlot);
  let started = false;
  let detail = null;

  // ---------------------------------------------------------------- başlık
  const add = actionButton(app, 'admin_createExam', 'Deneme ekle', { variant: 'primary',
    onClick: () => createExam(false) });
  // Geçmiş kayıt işareti `exams.import` ister; izin listesinde bunu taşıyan
  // işlem toplu yüklemedir (rol adı sorulmaz, işlem sorulur).
  const addHistorical = app.can('admin_createExam') && !app.can('admin_uploadExamImport')
    ? blockedButton('Geçmiş deneme ekle',
      'Geçmiş deneme girişi toplu yükleme yetkisi ister (yöneticinizden izin isteyin).')
    : actionButton(app, 'admin_createExam', 'Geçmiş deneme ekle', { onClick: () => createExam(true) });
  // Yayınevi kazanım tablosu (Akademi README §14 madde 120): dosyadan yeni deneme + anahtar + soru → konu.
  const addFromTable = actionButton(app, 'admin_previewExamPublisherTable', "Yayınevi Excel'i yükle",
    { onClick: () => importPublisherTable() });
  main.append(sectionHead('Denemeler', {
    subtitle: 'Deneme tanımı (kâğıt ya da uygulamada), sonuç girişi, toplu yükleme, yayınevi kazanım tablosu, '
      + 'cevap anahtarı, canlı sınav, analiz ve dışa aktarım.',
    actions: [addHistorical, addFromTable, add],
  }));

  const views = {};
  const tabs = tabBar([
    { key: 'exams', label: 'Denemeler' },
    { key: 'analytics', label: 'Analiz ve karşılaştırma' },
    app.can('admin_listExamTopicAliases') ? { key: 'aliases', label: 'Eşleme sözlüğü' } : null,
  ].filter(Boolean), 'exams', (key) => showView(key));
  const viewSlot = h('div', 'ak-stack');
  main.append(tabs.node, viewSlot);

  // ------------------------------------------------------------- liste
  const filters = { type: '', status: '', historical: '', from: '', to: '' };
  const listView = h('div', 'ak-stack');
  const typeSelect = selectBox(optionsOf(EXAM_TYPES, { empty: 'Tüm türler' }), '', (value) => {
    filters.type = value; list.reload();
  }, { label: 'Tür' });
  const statusSelect = selectBox(optionsOf(EXAM_STATUS, { empty: 'Tüm durumlar' }), '', (value) => {
    filters.status = value; list.reload();
  }, { label: 'Durum' });
  const historicalSelect = selectBox([{ value: '', label: 'Güncel ve geçmiş' },
    { value: 'false', label: 'Yalnız güncel' }, { value: 'true', label: 'Yalnız geçmiş kayıtlar' }], '',
  (value) => { filters.historical = value; list.reload(); }, { label: 'Kayıt türü' });
  const fromField = dateInput('', (iso) => { filters.from = iso || ''; list.reload(); }, 'Başlangıç');
  const toField = dateInput('', (iso) => { filters.to = iso || ''; list.reload(); }, 'Bitiş');
  const clearDates = button('Tarihi temizle', { variant: 'ghost', onClick: () => {
    filters.from = ''; filters.to = '';
    fromField.set(''); toField.set('');
    list.reload();
  } });
  const dates = h('div', 'kit-field-row');
  dates.append(h('span', 'kit-filter-label', 'Tarih'), fromField.node, h('span', '', '–'), toField.node,
    clearDates);
  listView.append(toolbar(typeSelect.node, statusSelect.node, historicalSelect.node, dates));

  const list = pagedList({
    app,
    path: '/v1/admin/exams',
    query: () => ({
      type: filters.type || undefined,
      status: filters.status || undefined,
      isHistorical: filters.historical === '' ? undefined : filters.historical === 'true',
      from: filters.from || undefined,
      to: filters.to || undefined,
    }),
    columns: [
      { key: 'date', label: 'Tarih', width: '96px', cell: (row) => fmtDate(row.date) },
      { key: 'name', label: 'Deneme', width: 'minmax(0, 2.2fr)', cell: (row) => examTitleCell(row) },
      { key: 'sections', label: 'Bölüm / soru', cell: (row) => `${num((row.sections || []).length)} bölüm · `
        + `${num(totalQuestions(row))} soru` },
      { key: 'results', label: 'Sonuç', align: 'num', width: '80px', cell: (row) => num(row.resultCount) },
      { key: 'keys', label: 'Anahtar / konu', cell: (row) => {
        const cell = h('div', 'ak-inline ak-ex-fill');
        cell.append(statusBadge(FILL_STATUS, row.answerKeyStatus), statusBadge(FILL_STATUS, row.questionTopicStatus));
        cell.title = `Cevap anahtarı: ${FILL_STATUS[row.answerKeyStatus]?.[0] || '—'} · Soru→konu: `
          + `${FILL_STATUS[row.questionTopicStatus]?.[0] || '—'}`;
        return cell;
      } },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(EXAM_STATUS, row.status) },
    ],
    onRow: (row) => openExam(row.id),
    empty: { title: 'Henüz deneme yok ya da süzgece uyan yok',
      text: 'Süzgeçleri temizleyin ya da yeni deneme tanımlayın; sonuçları toplu yükleyebilirsiniz.',
      actions: [actionButton(app, 'admin_createExam', 'Deneme ekle', { variant: 'primary',
        onClick: () => add.click() })] },
  });
  listView.append(list.node);
  views.exams = { node: listView, reload: () => list.reload(), destroy: () => {
    list.destroy(); fromField.destroy(); toField.destroy();
  } };

  function showView(key) {
    if (!views[key]) {
      views[key] = key === 'aliases' ? aliasesPanel(app)
        : analyticsPanel(app, { openExam: (examId) => openExam(examId) });
      viewSlot.append(views[key].node);
      views[key].reload();
    }
    if (key === 'exams' && !views.exams.loaded) {
      views.exams.loaded = true;
      views.exams.reload();
    }
    for (const [name, view] of Object.entries(views)) view.node.hidden = name !== key;
  }
  viewSlot.append(listView);

  // ------------------------------------------------------- tanım / düzenleme
  async function createExam(historical) {
    const saved = await examDialog(app, null, { historical });
    if (!saved) return;
    app.toast(historical ? 'Geçmiş deneme tanımlandı. Şimdi sonuç dosyasını yükleyin.'
      : 'Deneme tanımlandı.', 'good');
    list.reload();
    // Uygulamada deneme canlı ekranla açılır (pencere açılmadıysa da: hedef kitle ve sayaçlar orada).
    openExam(saved.id, { tab: historical && app.can('admin_uploadExamImport') ? 'import'
      : (saved.deliveryMode === 'app' ? null : 'results'), exam: saved });
  }

  async function importPublisherTable() {
    const result = await publisherWizard(app);
    if (!result?.exam) return;
    list.reload();
    openExam(result.exam.id, { tab: 'matching', exam: result.exam });
  }

  async function editExam(exam) {
    const saved = await examDialog(app, exam);
    if (!saved) return;
    app.toast('Deneme güncellendi.', 'good');
    list.replace((row) => row.id === saved.id, saved);
    if (detail?.examId === saved.id) openExam(saved.id, { tab: detail.tab(), exam: saved });
  }

  // ------------------------------------------------------------- detay
  function openExam(examId, { tab = null, importId = null, exam = null } = {}) {
    detail?.destroy();
    detail = examDetail(app, examId, {
      tab, importId, exam,
      onBack: () => closeDetail(),
      onEdit: (current) => editExam(current),
      onImportTable: async (current) => {
        const result = await publisherWizard(app, { exam: current });
        if (result?.exam) {
          list.replace((row) => row.id === result.exam.id, result.exam);
          openExam(result.exam.id, { tab: 'matching', exam: result.exam });
        }
      },
      onChanged: (next) => { if (next) list.replace((row) => row.id === next.id, next); },
      onDeleted: (id) => { list.remove((row) => row.id === id); closeDetail(); },
    });
    detailSlot.replaceChildren(detail.node);
    detailSlot.hidden = false;
    main.hidden = true;
  }

  function closeDetail() {
    detail?.destroy();
    detail = null;
    detailSlot.replaceChildren();
    detailSlot.hidden = true;
    main.hidden = false;
  }

  async function openImportLink(importId) {
    try {
      const job = await app.api.get(`/v1/admin/exam-imports/${importId}`);
      openExam(job.examId, { tab: 'import', importId });
    } catch (failure) {
      app.toast(`İçe aktarma işi açılamadı: ${errorText(failure)}`, 'bad');
    }
  }

  return {
    node,
    shown(payload) {
      if (!started) {
        started = true;
        showView(payload?.tab === 'analytics' ? 'analytics' : 'exams');
        if (payload?.tab === 'analytics') tabs.select('analytics', false);
      }
      if (payload?.examId) {
        openExam(payload.examId, { tab: DETAIL_TABS.includes(payload.tab) ? payload.tab : null,
          importId: payload.importId || null });
      } else if (payload?.importId) {
        openImportLink(payload.importId);
      } else if (payload?.tab === 'aliases' && app.can('admin_listExamTopicAliases')) {
        closeDetail();
        tabs.select('aliases');
        showView('aliases');
      } else if (payload?.tab === 'analytics') {
        closeDetail();
        tabs.select('analytics');
        showView('analytics');
      }
    },
    destroy() {
      detail?.destroy();
      for (const view of Object.values(views)) view.destroy?.();
    },
  };
}

function errorText(failure) {
  return failure?.message || 'İşlem tamamlanamadı.';
}

/** Hedef kitle: sınıflar + tek tek öğrenciler; ikisi de boşsa bütün kurum. */
function targetText(exam) {
  const classes = (exam.classGroupIds || []).length;
  const students = (exam.studentIds || []).length;
  if (!classes && !students) return 'Bütün kurum';
  return [classes ? `${num(classes)} sınıf` : '', students ? `${num(students)} öğrenci (ayrıca)` : '']
    .filter(Boolean).join(' + ');
}

// ---------------------------------------------------------------- detay

/**
 * Deneme detayı: başlık + özet + sekmeler. Sekmeler ilk açılışta kurulur ve
 * açık kalır; deneme düzenlenirse detay baştan kurulur (bölümler değişebilir).
 */
function examDetail(app, examId, { tab, importId, exam: given, onBack, onEdit, onImportTable, onChanged, onDeleted }) {
  const node = h('div', 'ak-stack');
  const headSlot = h('div', 'ak-stack');
  const tabSlot = h('div');
  const bodySlot = h('div', 'ak-stack');
  node.append(headSlot, tabSlot, bodySlot);
  let exam = given || null;
  let tabs = null;
  let current = tab;
  const bodies = {};
  let disposed = false;

  const view = loader(() => (exam ? Promise.resolve(exam) : app.api.get(`/v1/admin/exams/${examId}`)),
    (data) => { exam = data; build(); return []; }, { rows: 4, columns: 4 });
  headSlot.append(view.node);
  view.reload();

  const isApp = () => exam?.deliveryMode === 'app';

  function available() {
    return [
      isApp() && app.can('admin_getExamLive') ? { key: 'live', label: 'Canlı sınav' } : null,
      { key: 'results', label: 'Sonuçlar' },
      app.can('admin_listExamImports') || app.can('admin_uploadExamImport')
        ? { key: 'import', label: 'Toplu yükleme' } : null,
      app.can('admin_getAnswerKeys') || app.can('admin_getQuestionTopics')
        ? { key: 'keys', label: 'Cevap anahtarı ve konular' } : null,
      app.can('admin_getExamTopicMatching') ? { key: 'matching', label: 'Kazanım eşleştirmesi' } : null,
      { key: 'analysis', label: 'Analiz' },
      app.can('admin_exportExam') ? { key: 'export', label: 'Dışa aktar' } : null,
    ].filter(Boolean);
  }

  function build() {
    if (disposed) return;
    paintHead();
    const items = available();
    // Uygulamada deneme sürerken (ya da açılmak üzereyken) detay canlı ekranla açılır.
    if (!current) current = isApp() && ['scheduled', 'open', 'paused'].includes(exam.app?.state) ? 'live' : 'results';
    if (!items.some((item) => item.key === current)) current = 'results';
    tabs = tabBar(items, current, (key) => show(key));
    tabSlot.replaceChildren(tabs.node);
    show(current);
    if (importId && bodies.import) bodies.import.openImport(importId);
  }

  async function refresh() {
    try {
      exam = await app.api.get(`/v1/admin/exams/${examId}`);
      if (disposed) return;
      paintHead();
      onChanged?.(exam);
    } catch { /* başlık eski kalır; sekmeler kendi hatasını gösterir */ }
  }

  function show(key) {
    current = key;
    if (!bodies[key]) {
      const hooks = {
        onChanged: () => { refresh(); for (const [name, body] of Object.entries(bodies)) if (name !== key) body.stale = true; },
        onShowResults: () => { tabs.select('results'); },
        onPublish: () => publish(),
        onImport: () => onImportTable?.(exam),
      };
      const builders = {
        results: () => resultsTab(app, exam, hooks),
        import: () => importTab(app, exam, hooks),
        live: () => liveTab(app, exam, hooks),
        keys: () => keysTab(app, exam, hooks),
        matching: () => matchingTab(app, exam, hooks),
        analysis: () => analysisTab(app, exam),
        export: () => exportTab(app, exam),
      };
      bodies[key] = builders[key]();
      bodySlot.append(bodies[key].node);
      bodies[key].reload();
    } else if (bodies[key].stale) {
      bodies[key].stale = false;
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  function paintHead() {
    const back = button('← Denemeler', { variant: 'ghost', onClick: () => onBack?.() });
    const actions = [];
    actions.push(actionButton(app, 'admin_updateExam', 'Düzenle', { onClick: () => onEdit?.(exam) }));
    if (exam.status === 'published') {
      actions.push(blockedButton('Yayımlandı', `Sonuçlar ${fmtDateTime(exam.publishedAt)} tarihinde yayımlandı.`));
    } else if (!exam.resultCount && app.can('admin_publishExam')) {
      actions.push(blockedButton(exam.isHistorical ? 'Yayımla' : 'Yayımla ve bildir',
        'Yayımlamak için önce sonuç girin ya da toplu yükleyin.', { variant: 'primary' }));
    } else {
      actions.push(actionButton(app, 'admin_publishExam',
        exam.isHistorical ? 'Yayımla' : 'Yayımla ve bildir', { variant: 'primary', onClick: () => publish() }));
    }
    if (exam.status === 'published' && app.can('admin_deleteExam')) {
      actions.push(blockedButton('Sil', 'Yayımlanmış deneme silinemez (Akademi kuralı).', { variant: 'ghost' }));
    } else {
      actions.push(actionButton(app, 'admin_deleteExam', 'Sil', { variant: 'ghost', onClick: () => remove() }));
    }

    const title = h('div', 'ak-ex-detail-title');
    title.append(h('h2', 'ak-title', exam.name), statusBadge(EXAM_STATUS, exam.status));
    if (exam.isHistorical) title.append(badge('Geçmiş kayıt', 'dim'));
    if (isApp()) title.append(badge('Uygulamada', 'info'), statusBadge(APP_STATE, exam.app?.state));
    const head = h('div', 'ak-head');
    const text = h('div', 'ak-head-text');
    text.append(back, title, h('div', 'ak-sub', [EXAM_TYPES[exam.type] || exam.type, exam.publisher,
      fmtDate(exam.date)].filter(Boolean).join(' · ')));
    const tools = h('div', 'ak-tools');
    tools.append(...actions);
    head.append(text, tools);

    const nodes = [head];
    if (exam.isHistorical) {
      nodes.push(alertBox('Geçmiş kayıt: yayımlandığında öğrenci ve velilere bildirim gitmez; sonuçlar '
        + 'trend ve analizlere katılır.', 'info'));
    }
    const sections = sortedSections(exam).map((item) => `${sectionLabel(item)} ${item.questionCount}`).join(' · ');
    const summary = card('', kv([
      ['Bölümler', `${sections || '—'} (toplam ${num(totalQuestions(exam))} soru)`],
      ['Kitapçıklar', (exam.bookletTypes || []).join(', ') || '—'],
      ['Net kuralı', scoringText(exam)],
      ['Sonuç', `${num(exam.resultCount)} öğrenci`],
      ['Cevap anahtarı', statusBadge(FILL_STATUS, exam.answerKeyStatus)],
      ['Soru → konu', statusBadge(FILL_STATUS, exam.questionTopicStatus)],
      ['Yayım', exam.publishedAt ? fmtDateTime(exam.publishedAt) : 'Yayımlanmadı (yalnız yöneticiler görür)'],
      ...(isApp() ? [...appFacts(exam.app, fmtDateTime), ['Soru kitapçığı', bookletCell()]] : []),
      ['Hedef', targetText(exam)],
    ]));
    summary.classList.add('ak-ex-summary');
    nodes.push(summary);
    headSlot.replaceChildren(...nodes);
  }

  /** İsteğe bağlı soru kitapçığı PDF'i: öğrenci oturumu başlayınca uygulamada görür (tablette form ile yan yana). */
  function bookletCell() {
    const cell = h('div', 'ak-inline');
    const booklet = exam.app?.questionBooklet;
    cell.append(h('span', booklet ? '' : 'ak-muted', booklet
      ? `${booklet.fileName} (${num(Math.ceil((booklet.sizeBytes || 0) / 1024))} KB)` : 'Yok (isteğe bağlı)'));
    const input = h('input');
    input.type = 'file';
    input.accept = '.pdf,application/pdf';
    input.hidden = true;
    input.addEventListener('change', async () => {
      const file = input.files?.[0];
      input.value = '';
      if (!file) return;
      if (!/\.pdf$/i.test(file.name)) { app.toast('Yalnız PDF yüklenebilir.', 'warn'); return; }
      if (file.size > 25 * 1024 * 1024) { app.toast("PDF 25 MB'tan büyük olamaz.", 'warn'); return; }
      try {
        const result = await app.api.call('PUT', `/v1/admin/exams/${exam.id}/question-booklet`,
          { upload: { file, field: 'file' } });
        exam = result.data || exam;
        paintHead();
        onChanged?.(exam);
        app.toast('Soru kitapçığı yüklendi; öğrenciler oturumu başlatınca görür.', 'good');
      } catch (failure) {
        app.toast(errorText(failure), 'bad');
      }
    });
    cell.append(input, actionButton(app, 'admin_uploadExamQuestionBooklet', booklet ? 'Değiştir' : 'PDF yükle',
      { variant: 'ghost', onClick: () => input.click() }));
    if (booklet) {
      cell.append(actionButton(app, 'admin_deleteExamQuestionBooklet', 'Kaldır', { variant: 'ghost', onClick: async () => {
        try {
          await app.api.del(`/v1/admin/exams/${exam.id}/question-booklet`);
          await refresh();
          app.toast('Soru kitapçığı kaldırıldı.', 'good');
        } catch (failure) {
          app.toast(errorText(failure), 'bad');
        }
      } }));
    }
    return cell;
  }

  async function publish() {
    const done = await publishExam(app, exam);
    if (done) { exam = done; paintHead(); onChanged?.(done); }
  }

  async function remove() {
    const done = await deleteExam(app, exam);
    if (done) onDeleted?.(exam.id);
  }

  return {
    node,
    examId,
    tab: () => current,
    destroy() {
      disposed = true;
      for (const body of Object.values(bodies)) body.destroy?.();
    },
  };
}

// --------------------------------------------------------------- pencereler

/** Yayımla (ve bildir). Geçmiş kayıtta bildirim seçeneği yoktur. */
async function publishExam(app, exam) {
  const historical = exam.isHistorical;
  const result = await formDialog(app, {
    title: `“${exam.name}” sonuçlarını yayımla`,
    description: historical
      ? `${num(exam.resultCount)} öğrencinin sonucu açılır. Geçmiş kayıt olduğu için bildirim gönderilmez.`
      : `${num(exam.resultCount)} öğrencinin sonucu öğrenci ve velilere açılır. Zayıf konular öğrencilerin `
        + 'tekrar listesine öne alınır. Yayımdan sonra deneme silinemez.',
    value: { notify: !historical },
    fields: [historical ? null
      : { key: 'notify', label: 'Öğrenci ve velilere bildirim gönder', type: 'checkbox', wide: true }],
    submitLabel: historical ? 'Yayımla' : 'Yayımla ve bildir',
    onSubmit: (draft, { key }) => app.api.post(`/v1/admin/exams/${exam.id}/publish`,
      { notify: historical ? false : draft.notify !== false }, { key }),
  });
  if (!result || result === true) return null;
  app.toast(historical ? 'Sonuçlar yayımlandı.' : 'Sonuçlar yayımlandı ve bildirim gönderildi.', 'good');
  return result;
}

/**
 * Denemeyi sonuçlarıyla siler. Sözleşmede DELETE gövde taşımaz (gerekçe
 * gönderilemez); yanlışlıkla silmeye karşı deneme adını yazmak istenir.
 */
async function deleteExam(app, exam) {
  const done = await formDialog(app, {
    title: `“${exam.name}” denemesini sil`,
    description: `Deneme tanımı, ${num(exam.resultCount)} öğrenci sonucu, cevap anahtarı ve içe aktarma `
      + 'kayıtları kalıcı olarak silinir. Bu işlem geri alınamaz.',
    danger: true,
    submitLabel: 'Denemeyi ve sonuçlarını sil',
    fields: [{ key: 'confirmName', label: 'Onay için deneme adını aynen yazın', required: true, wide: true,
      placeholder: exam.name,
      validate: (value) => (String(value || '').trim() === String(exam.name).trim() ? null
        : 'Yazılan ad deneme adıyla aynı değil.') }],
    onSubmit: async () => { await app.api.del(`/v1/admin/exams/${exam.id}`); return true; },
  });
  if (done) app.toast('Deneme silindi.', 'good');
  return Boolean(done);
}

/**
 * Deneme tanımı (oluştur / düzenle). `historical` ön ayarı geçmiş kayıt
 * işaretini açar ve tarihi geçmişe çeker.
 */
async function examDialog(app, exam, { historical = false } = {}) {
  let subjects = [];
  let classGroups = [];
  try {
    [subjects, classGroups] = await Promise.all([app.lookups.subjects(), app.lookups.classGroups()]);
  } catch (failure) {
    app.toast(`Ders ve sınıf listesi alınamadı: ${errorText(failure)}`, 'bad');
    return null;
  }
  const locked = Boolean(exam?.resultCount);
  // Özel alanların (kitapçık, sınıf, bölüm) başlangıç değeri `value`a KONMAZ:
  // formDialog, form ızgarasındaki her değişiklikte ızgaranın `value` kopyasını
  // taslağa yazıyor ve özel alanın düzenlenmiş değerini başlangıç değeriyle
  // eziyordu. Başlangıç değerleri kapanışta tutulur, `set` ile taslağa geçer.
  const initial = exam ? {
    bookletTypes: exam.bookletTypes?.length ? [...exam.bookletTypes] : ['A'],
    classGroupIds: [...(exam.classGroupIds || [])],
    sections: sortedSections(exam).map((item) => ({ id: item.id, subjectId: item.subject?.id || '',
      title: item.title, questionCount: item.questionCount, programId: item.programId || '' })),
  } : { bookletTypes: ['A'], classGroupIds: [], sections: [] };
  const value = exam ? {
    name: exam.name, type: exam.type, publisher: exam.publisher || '', date: exam.date,
    scoringRule: exam.scoring?.rule || '', wrongsPerCorrect: exam.scoring?.wrongsPerCorrect ?? null,
    isHistorical: Boolean(exam.isHistorical),
  } : {
    name: '', type: 'tyt', publisher: '', date: historical ? todayIst(-7) : todayIst(),
    scoringRule: '', wrongsPerCorrect: null, isHistorical: historical,
  };

  const canHistorical = app.can('admin_uploadExamImport');

  // Nasıl çözülecek? (Akademi README §14 madde 120). Seçim, sözleşmesi bilinen Akademi'de (canlı sınav işlemi izin
  // listesinde) görünür; kurum bayrağı kapalıysa "Uygulamada" sebebiyle kapalıdır.
  const canApp = app.can('admin_getExamLive') || exam?.deliveryMode === 'app';
  const appFlagOff = !app.enabled('exams.app_delivery');
  const lockedApp = exam?.deliveryMode === 'app' && exam.app && exam.app.state !== 'scheduled' ? exam.app : null;
  const formSections = () => initial.sections;
  initial.deliveryMode = exam?.deliveryMode === 'app' ? 'app' : 'paper';
  initial.app = exam?.deliveryMode === 'app' ? appDraftFromExam(exam, sortedSections(exam))
    : defaultAppDraft(value.type, value.date);
  initial.studentIds = [...(exam?.studentIds || [])];
  let appCtl = null;
  const showApp = (mode) => showAppField(appCtl?.node, mode);
  return formDialog(app, {
    title: exam ? 'Denemeyi düzenle' : (historical ? 'Geçmiş deneme ekle' : 'Yeni deneme'),
    description: historical
      ? 'Geçmiş tarihli bir denemeyi tanımlayın; ardından sonuç dosyasını “Toplu yükleme” sekmesinden '
        + 'yükleyin ya da sonuçları tek tek girin. Bildirim gitmez, trend ve analizlere katılır.'
      : (locked ? 'Sonuç girildiği için bölümler ve soru sayıları değiştirilemez.' : ''),
    wide: true,
    value,
    fields: [
      { key: 'name', label: 'Deneme adı', required: true, maxLength: 120, placeholder: '3. TYT Kurum Denemesi',
        wide: true },
      { key: 'type', label: 'Tür', type: 'select', options: optionsOf(EXAM_TYPES),
        hint: 'Net kuralı türden gelir: LGS 3 yanlış, YKS 4 yanlış 1 doğruyu götürür.' },
      { key: 'publisher', label: 'Yayın', maxLength: 80, placeholder: 'Örnek Yayınları',
        hint: 'Toplu yüklemede bu yayının kayıtlı sütun şablonu önerilir.' },
      { key: 'date', label: 'Sınav tarihi', type: 'date', required: true },
      { key: 'scoringRule', label: 'Net kuralı', type: 'select', options: [
        { value: '', label: 'Türün kuralı' }, ...optionsOf(SCORING_RULES)] },
      { key: 'wrongsPerCorrect', label: 'Kaç yanlış 1 doğruyu götürür', type: 'number', min: 1, max: 10,
        hint: 'Yalnız “Özel kural” seçiliyse.',
        validate: (raw, draft) => (draft.scoringRule === 'custom' && !raw ? 'Özel kuralda bu sayı zorunlu.' : null) },
      // İşaretsiz kullanıcıda alan çizilmez; mevcut değer taslakta korunur.
      canHistorical ? { key: 'isHistorical', label: 'Geçmiş kayıt (bildirim gitmez)', type: 'checkbox' } : null,
      canApp ? { key: 'deliveryMode', label: 'Nasıl çözülecek?', type: 'custom',
        render: (draft, set) => {
          set(initial.deliveryMode);
          return deliveryChoice({ value: initial.deliveryMode, locked: Boolean(lockedApp),
            disabledReason: appFlagOff ? 'Kurumda kapalı: Ayarlar → Özellikler → “Uygulamada deneme”.' : '',
            onChange: (mode) => { set(mode); showApp(mode); } });
        },
        hint: lockedApp ? 'Pencere açıldığı için çözülme biçimi değiştirilemez.' : '' } : null,
      { key: 'bookletTypes', label: 'Kitapçık türleri', type: 'custom', required: true,
        render: (draft, set) => {
          set(initial.bookletTypes);
          return multiChips(BOOKLET_LETTERS.map((letter) => ({ value: letter, label: letter })),
            initial.bookletTypes, (list) => { set([...list].sort()); appCtl?.sectionsChanged(); });
        },
        hint: 'Cevap anahtarı kitapçık başına girilir.' },
      { key: 'classGroupIds', label: 'Katılan sınıflar', type: 'custom',
        render: (draft, set) => {
          set(initial.classGroupIds);
          return classGroups.length
            ? multiChips(classGroups.map((item) => ({ value: item.id, label: item.name })), initial.classGroupIds, set)
            : h('span', 'ak-muted', 'Sınıf tanımlı değil.');
        },
        hint: 'Sınıf da öğrenci de seçilmezse deneme bütün kuruma açıktır.' },
      canApp ? { key: 'studentIds', label: 'Ayrıca tek tek öğrenciler', type: 'custom',
        render: (draft, set) => studentTargets(app, { ids: initial.studentIds, classGroups, set }),
        hint: 'Sınıf dışından katılacak öğrenciler (hedef = seçili sınıflar + bu öğrenciler).' } : null,
      { key: 'sections', label: 'Bölümler ve soru sayıları', type: 'custom', required: true,
        render: (draft, set) => sectionsEditor(app, { draft, set: (rows) => {
          set(rows);
          initial.sections = rows;
          appCtl?.sectionsChanged();
        }, subjects, locked: locked || Boolean(lockedApp), rows: initial.sections }),
        validate: (rows) => validateSections(rows) },
      canApp ? { key: 'app', label: 'Uygulamada deneme ayarları', type: 'custom',
        render: (draft, set) => {
          set(initial.app);
          appCtl = appSettings({ draft: initial.app, sections: formSections, type: () => draft.type || value.type,
            bookletCount: () => (draft.bookletTypes || initial.bookletTypes).length, lockedApp, set });
          queueMicrotask(() => showApp(draft.deliveryMode || initial.deliveryMode));
          return { node: appCtl.node, update: (next) => appCtl.typeChanged(next.type) };
        },
        validate: (raw, draft) => (draft.deliveryMode === 'app' && !lockedApp
          ? validateApp(raw, (draft.sections || []).length) : null) } : null,
    ],
    submitLabel: exam ? 'Kaydet' : 'Denemeyi tanımla',
    onSubmit: (draft, { key }) => {
      if (draft.isHistorical && draft.date > todayIst()) {
        throw new AkademiError({ message: 'Geçmiş kayıt ileri tarihli olamaz; tarihi ya da işareti düzeltin.' });
      }
      const body = examBody(draft);
      // Pencere açıldıysa ayarlar gönderilmez (Akademi mevcut değeri korur); kâğıt denemede yeni alanlar yalnız
      // sunucu tanıyorsa yazılır (eski Akademi ile de kayıt kırılmaz).
      if (canApp && !lockedApp) Object.assign(body, deliveryPart(draft, exam));
      if (canApp && (draft.studentIds?.length || (exam && 'studentIds' in exam))) body.studentIds = draft.studentIds || [];
      if (draft.deliveryMode === 'app' && !lockedApp) {
        const booklets = body.bookletTypes || [];
        if (body.app.bookletMode !== 'single' && booklets.length < 2) {
          throw new AkademiError({ message: 'Bu kitapçık kuralı için en az iki kitapçık türü seçin ya da “Tek kitapçık” seçin.' });
        }
      }
      return exam ? app.api.put(`/v1/admin/exams/${exam.id}`, body)
        : app.api.post('/v1/admin/exams', body, { key });
    },
  });
}

/** Uygulamada ayarları alanını çözülme biçimine göre gösterir / gizler. */
function showAppField(node, mode) {
  const wrap = node?.closest?.('.kit-field');
  if (wrap) wrap.hidden = mode !== 'app';
}

/**
 * Tek tek hedeflenen öğrenciler. Kayıtlı kimliklerin adı yükte gelmez (sözleşme yalnız kimlik verir): sayı yazılır,
 * "Yeniden seç" ile ortak öğrenci seçicisi açılır; boş bırakılan seçim kayıtlıyı korur, "Temizle" kaldırır.
 */
function studentTargets(app, { ids, classGroups, set }) {
  const node = h('div', 'ak-stack');
  let current = [...ids];
  set(current);
  const line = h('div', 'ak-inline');
  const text = h('span', 'ak-muted');
  const slot = h('div');
  let picker = null;
  const paint = () => {
    text.textContent = current.length ? `${num(current.length)} öğrenci ayrıca hedefte.` : 'Tek tek öğrenci yok.';
    clear.hidden = !current.length;
  };
  const choose = button(ids.length ? 'Yeniden seç' : 'Öğrenci seç', { onClick: () => {
    if (picker) return;
    picker = studentSelect(app, { classes: classGroups.map((item) => ({ id: item.id, name: item.name })),
      onChange: (students) => {
        current = students.length ? students.map((item) => item.id) : [...ids];
        set(current);
        paint();
      } });
    slot.replaceChildren(picker.node);
  } });
  const clear = button('Temizle', { variant: 'ghost', onClick: () => {
    current = [];
    set(current);
    picker?.destroy();
    picker = null;
    slot.replaceChildren();
    paint();
  } });
  line.append(text, choose, clear);
  node.append(line, slot);
  paint();
  return node;
}

function validateSections(rows) {
  if (!Array.isArray(rows) || !rows.length) return 'En az bir bölüm ekleyin.';
  for (const [index, row] of rows.entries()) {
    if (!row.subjectId) return `${index + 1}. bölümün dersini seçin.`;
    if (!String(row.title || '').trim()) return `${index + 1}. bölümün adını yazın.`;
    const count = Number(row.questionCount);
    if (!Number.isInteger(count) || count < 1 || count > 200) return `${index + 1}. bölümün soru sayısı 1–200 olmalı.`;
  }
  return null;
}

function examBody(draft) {
  const rule = draft.scoringRule || '';
  return {
    name: String(draft.name || '').trim(),
    type: draft.type,
    publisher: String(draft.publisher || '').trim() || null,
    date: draft.date,
    bookletTypes: draft.bookletTypes?.length ? draft.bookletTypes : ['A'],
    sections: (draft.sections || []).map((row, index) => ({
      id: row.id || null,
      subjectId: row.subjectId,
      title: String(row.title || '').trim(),
      questionCount: Number(row.questionCount),
      order: index + 1,
      programId: row.programId || null,
    })),
    // Boş bırakılırsa kural türden gelir (Akademi).
    scoring: rule ? { rule, wrongsPerCorrect: rule === 'custom' ? Number(draft.wrongsPerCorrect) : null } : undefined,
    classGroupIds: draft.classGroupIds || [],
    isHistorical: Boolean(draft.isHistorical),
  };
}

/**
 * Bölüm düzenleyici: ders, ad, soru sayısı ve (konu analizi için) müfredat
 * programı. "Türe göre doldur" yayımlanmış soru dağılımını getirir.
 */
function sectionsEditor(app, { draft, set, subjects, locked, rows: given = [] }) {
  const node = h('div', 'ak-ex-sections');
  let rows = given.map((row) => ({ ...row }));
  const subjectName = Object.fromEntries(subjects.map((item) => [item.id, item.name]));
  const commit = () => set(rows.map((row) => ({ ...row })));

  if (locked) {
    const table = h('div', 'ak-ex-sec-locked');
    for (const row of rows) {
      table.append(h('span', '', subjectName[row.subjectId] || row.subjectId),
        h('span', '', row.title), h('span', 'ak-num', `${row.questionCount} soru`));
    }
    node.append(table, h('span', 'kit-field-hint',
      'Sonuç girildiği için bölümler kilitli. Değiştirmek için önce sonuçları silin ya da içe aktarmayı geri alın.'));
    commit();
    return node;
  }

  const listNode = h('div', 'ak-ex-sec-list');
  const foot = h('div', 'ak-inline');
  const total = h('span', 'ak-muted');
  foot.append(
    button('Bölüm ekle', { onClick: () => { rows.push({ subjectId: '', title: '', questionCount: 20, programId: '' }); paint(); } }),
    button('Türe göre doldur', { onClick: () => { preset(); } }),
    h('span', 'kit-spacer'), total);
  node.append(listNode, foot);

  const subjectOptions = [{ value: '', label: 'Ders seçin' },
    ...subjects.map((item) => ({ value: item.id, label: item.name }))];

  // Şablon Akademi'den (`catalog_listExamTemplates`; elle kopya yok — README §14 madde 75).
  async function preset() {
    let template;
    try {
      template = await examTemplate(app, draft.type);
    } catch (failure) {
      app.toast(`Deneme şablonu alınamadı: ${errorText(failure)}`, 'bad');
      return;
    }
    const layout = template?.sections || [];
    if (!layout.length) { app.toast('Bu tür için hazır düzen yok; bölümleri elle ekleyin.', 'warn'); return; }
    const known = new Set(subjects.map((item) => item.id));
    const missing = layout.filter((item) => !known.has(item.subjectId)).map((item) => item.title);
    rows = layout.filter((item) => known.has(item.subjectId))
      .map((item) => ({ subjectId: item.subjectId, title: item.title, questionCount: item.questionCount, programId: '' }));
    if (missing.length) app.toast(`Kurumda tanımlı olmayan dersler atlandı: ${missing.join(', ')}`, 'warn');
    paint();
  }

  function paint() {
    listNode.replaceChildren();
    const head = h('div', 'ak-ex-sec-row ak-ex-sec-head');
    head.append(h('span', '', 'Ders'), h('span', '', 'Bölüm adı'), h('span', '', 'Soru'),
      h('span', '', 'Müfredat programı (konu analizi)'), h('span', ''));
    listNode.append(head);
    rows.forEach((row, index) => listNode.append(rowNode(row, index)));
    if (!rows.length) listNode.append(h('div', 'ak-muted', 'Henüz bölüm yok. “Türe göre doldur” ya da “Bölüm ekle”.'));
    total.textContent = `${num(rows.length)} bölüm · ${num(rows.reduce((sum, row) => sum + (Number(row.questionCount) || 0), 0))} soru`;
    commit();
  }

  function rowNode(row, index) {
    const line = h('div', 'ak-ex-sec-row');
    const subject = selectBox(subjectOptions, row.subjectId, (value) => {
      row.subjectId = value;
      if (!row.title || Object.values(subjectName).includes(row.title)) row.title = subjectName[value] || '';
      row.programId = '';
      title.value = row.title;
      loadPrograms();
      commit();
    }, { label: 'Ders' });
    const title = h('input', 'kit-input');
    title.value = row.title || '';
    title.maxLength = 80;
    title.setAttribute('aria-label', 'Bölüm adı');
    title.addEventListener('input', () => { row.title = title.value; commit(); });
    const count = h('input', 'kit-input ak-ex-count');
    count.inputMode = 'numeric';
    count.value = row.questionCount ?? '';
    count.setAttribute('aria-label', 'Soru sayısı');
    count.addEventListener('input', () => {
      row.questionCount = count.value.trim() === '' ? null : Number(count.value);
      total.textContent = `${num(rows.length)} bölüm · ${num(rows.reduce((sum, item) => sum + (Number(item.questionCount) || 0), 0))} soru`;
      commit();
    });
    const program = selectBox([{ value: '', label: row.subjectId ? 'Program yükleniyor…' : '—' }], row.programId,
      (value) => { row.programId = value; commit(); }, { label: 'Müfredat programı' });

    async function loadPrograms() {
      if (!row.subjectId) { program.options([{ value: '', label: '—' }]); return; }
      try {
        const data = await app.api.getCached('/v1/admin/curricula/programs', { subject: row.subjectId },
          { ttlMs: 10 * 60_000 });
        const family = examFamily(draft.type);
        const items = sortPrograms(data?.items).sort((a, b) => Number(b.exam === family) - Number(a.exam === family));
        program.options([{ value: '', label: 'Program yok' },
          ...items.map((item) => ({ value: item.id, label: programLabel(item) }))]);
        // Türle aynı sınav programı tekse kendiliğinden seçilir.
        const matching = items.filter((item) => item.kind === 'exam' && item.exam && item.exam === family
          && (!item.subject?.id || item.subject.id === row.subjectId));
        if (!row.programId && matching.length === 1) { row.programId = matching[0].id; commit(); }
        program.set(row.programId || '');
      } catch {
        program.options([{ value: '', label: 'Program listesi alınamadı' }]);
      }
    }
    loadPrograms();

    const tools = h('div', 'ak-row-actions');
    tools.append(
      button('↑', { variant: 'ghost', title: 'Yukarı', disabled: index === 0, onClick: () => {
        [rows[index - 1], rows[index]] = [rows[index], rows[index - 1]]; paint();
      } }),
      button('↓', { variant: 'ghost', title: 'Aşağı', disabled: index === rows.length - 1, onClick: () => {
        [rows[index + 1], rows[index]] = [rows[index], rows[index + 1]]; paint();
      } }),
      button('Kaldır', { variant: 'ghost', onClick: () => { rows.splice(index, 1); paint(); } }),
    );
    line.append(subject.node, title, count, program.node, tools);
    return line;
  }

  paint();
  return node;
}
