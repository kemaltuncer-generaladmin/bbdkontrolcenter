// BBD Akademi — Denemeler › Toplu yükleme sihirbazı (F-E2, F-E3, F-E4).
//
// AKIŞ (Akademi'de, asenkron): dosya yüklenir (XLSX/CSV, en çok 10 MB /
// 5.000 satır) → Akademi sütunları algılar ve eşleme önerir (seçilen şablon ya
// da yayına kayıtlı şablon) → kullanıcı sütunları eşler (isterse yayın şablonu
// olarak saklar) → önizleme: satır hataları, belirsiz / eşleşmeyen öğrenciler
// (okul no → telefon → ad soyad) → belirsizler elle çözülür ya da atlanır →
// kayıt (kısmi olabilir) → gerekirse geri alma.
//
// Panel durumu UYDURMAZ: hangi adımda olunduğu işin `status` alanından
// çıkar; işleniyorken (`uploaded`/`analyzing`/`committing`) iş artan aralıkla
// yoklanır. Aynı dosya aynı denemeye ikinci kez yüklenirse Akademi yeni iş
// açmaz, mevcut işi döndürür (`duplicateOf`). Kayıt ve geri alma
// idempotenttir: anahtar işe (ve seçeneklere) bağlıdır, çift tıklama ikinci
// kez yazmaz. Kayıt bildirim göndermez; bildirim yayımlamada gider.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, hintBox, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { stepper } from '../../ui-kit/flow.js';
import { newKey } from './api.js';
import {
  actionButton, confirmAction, empty, errorBox, fileDrop, fmtDateTime, formDialog, loader, num,
  openDrawer, optionsOf, pagedList, reasonDialog, runAction, selectBox, staticList, statusBadge,
  studentPicker, toolbar, uploadProgress,
} from './common.js';
import {
  EXAM_TYPES, IMPORT_KINDS, IMPORT_STATUS, columnLetter, netText, sectionLabel, sortedSections,
  studentCell, uploadFile, wait,
} from './exams-shared.js';

const BUSY = ['uploaded', 'analyzing', 'committing'];
const MATCH_BY = { school_no: 'okul no', phone: 'telefon', full_name: 'ad soyad', manual: 'elle seçildi' };
const PREVIEW_STATUS = {
  ok: ['Hazır', 'good'], error: ['Hatalı', 'bad'], ambiguous: ['Belirsiz', 'warn'],
  unmatched: ['Eşleşmedi', 'warn'], skipped: ['Atlanacak', 'dim'],
};
const STEPS = ['Dosya', 'Sütun eşleme', 'Önizleme ve eşleşme', 'Kaydet'];

/** Kayıtlı sütun şablonları (seyrek değişir → ETag önbelleği). */
function loadTemplates(app, exam) {
  return app.api.getCached('/v1/admin/exam-import-templates', undefined, { ttlMs: 5 * 60_000 })
    .then((data) => [...(data?.items || [])].sort((a, b) => {
      const mine = (item) => Number(Boolean(exam?.publisher) && item.publisher === exam.publisher);
      return mine(b) - mine(a) || String(a.name).localeCompare(String(b.name), 'tr');
    }));
}

function templateLabel(template) {
  return [template.name, template.publisher, EXAM_TYPES[template.examType]].filter(Boolean).join(' · ');
}

function statsLine(stats) {
  if (!stats) return '—';
  const pending = (stats.ambiguous || 0) + (stats.unmatched || 0);
  return [`${num(stats.rows)} satır`, stats.committed ? `${num(stats.committed)} kaydedildi` : '',
    stats.error ? `${num(stats.error)} hatalı` : '', pending ? `${num(pending)} eşleşme bekliyor` : '',
    stats.skipped ? `${num(stats.skipped)} atlandı` : ''].filter(Boolean).join(' · ');
}

// ------------------------------------------------------------------ sekme

export function importTab(app, exam, { onChanged, onShowResults, onPublish } = {}) {
  const node = h('div', 'ak-stack');
  const historyView = h('div', 'ak-stack');
  const wizardSlot = h('div', 'ak-stack');
  node.append(historyView, wizardSlot);
  let wizard = null;

  historyView.append(hintBox('Yayının sonuç dosyasını (XLSX ya da CSV) yükleyin. Sütunları bir kez eşleyip '
    + 'şablon olarak saklarsanız aynı yayının sonraki dosyaları kendiliğinden eşlenir. Öğrenciler okul no → '
    + 'telefon → ad soyad sırasıyla bulunur; belirsiz olanlar onayınıza düşer. Aynı dosya iki kez yüklenirse '
    + 'yeni kayıt oluşmaz.'));
  if (exam.isHistorical) {
    historyView.append(alertBox('Geçmiş kayıt: yüklenen sonuçlar trend ve analizlere katılır, bildirim gitmez.', 'info'));
  }
  historyView.append(toolbar(h('b', '', 'İçe aktarma geçmişi'), h('span', 'kit-spacer'),
    button('Sütun şablonları', { onClick: () => templatesDrawer(app, exam) }),
    actionButton(app, 'admin_uploadExamImport', 'Yeni dosya yükle', { variant: 'primary',
      onClick: () => openWizard(null) })));

  const history = staticList({
    load: () => app.api.get(`/v1/admin/exams/${exam.id}/imports`),
    map: (items) => [...items].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt))),
    columns: [
      { key: 'createdAt', label: 'Yüklenme', width: '150px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', fmtDateTime(row.createdAt)), h('small', '', row.createdBy || ''));
        return cell;
      } },
      { key: 'file', label: 'Dosya', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', 'ak-ex-file', row.fileName),
          h('small', '', row.kind === 'answers' ? 'Cevap dizisi' : 'Sonuç dosyası'));
        return cell;
      } },
      { key: 'status', label: 'Durum', width: '170px', cell: (row) => statusBadge(IMPORT_STATUS, row.status) },
      { key: 'stats', label: 'Satırlar', width: 'minmax(0, 1.6fr)', cell: (row) => statsLine(row.stats) },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button(['committed', 'rolled_back'].includes(row.status) ? 'Ayrıntı' : 'Devam et',
          { variant: 'ghost', onClick: () => openWizard(row.id) }));
        if (row.status === 'committed') {
          actions.append(actionButton(app, 'admin_rollbackExamImport', 'Geri al', { variant: 'ghost',
            onClick: async () => {
              const done = await rollbackImport(app, row);
              if (done) { history.reload(); onChanged?.(); }
            } }));
        }
        return actions;
      } },
    ],
    empty: { title: 'Bu deneme için yükleme yok', text: '“Yeni dosya yükle” ile yayının sonuç dosyasını yükleyin.' },
  });
  historyView.append(history.node);

  function openWizard(importId) {
    wizard?.destroy();
    wizard = importWizard(app, exam, {
      importId,
      onClose: () => closeWizard(),
      onNew: () => openWizard(null),
      onChanged: () => onChanged?.(),
      onShowResults: () => { closeWizard(); onShowResults?.(); },
      onPublish: () => onPublish?.(),
    });
    wizardSlot.replaceChildren(wizard.node);
    historyView.hidden = true;
  }

  function closeWizard() {
    wizard?.destroy();
    wizard = null;
    wizardSlot.replaceChildren();
    historyView.hidden = false;
    history.reload();
  }

  return {
    node,
    reload: () => history.reload(),
    openImport: (importId) => openWizard(importId),
    destroy() { wizard?.destroy(); },
  };
}

async function rollbackImport(app, job) {
  const reason = await reasonDialog(app, {
    title: `“${job.fileName}” içe aktarmasını geri al`,
    description: `Bu işin yazdığı ${num(job.stats?.committed || 0)} sonuç silinir. Sonradan elle düzeltilen `
      + 'sonuçlar korunur ve raporlanır. Gerekçe Akademi denetim kaydına yazılır.',
    confirmLabel: 'İçe aktarmayı geri al',
    presets: ['Yanlış dosya yüklendi', 'Sütunlar yanlış eşlendi', 'Yanlış denemeye yüklendi'],
  });
  if (!reason) return null;
  return runAction(app, () => app.api.post(`/v1/admin/exam-imports/${job.id}/rollback`, { reason },
    { key: `exam-import-rollback-${job.id}` }), { success: 'İçe aktarma geri alındı.' });
}

// --------------------------------------------------------------- sihirbaz

function importWizard(app, exam, { importId, onClose, onNew, onChanged, onShowResults, onPublish }) {
  const node = h('div', 'ak-stack ak-ex-wizard');
  const statusSlot = h('span');
  node.append(toolbar(button('← İçe aktarma geçmişi', { variant: 'ghost', onClick: () => onClose?.() }),
    h('b', '', 'Toplu sonuç yükleme'), h('span', 'kit-spacer'), statusSlot));
  const stepSlot = h('div', 'ak-ex-stepper');
  const body = h('div', 'ak-stack');
  node.append(stepSlot, body);

  let job = null;
  let token = 0;
  let disposed = false;
  let cleanup = [];
  const sections = sortedSections(exam);

  function dispose() {
    for (const fn of cleanup.splice(0)) { try { fn(); } catch { /* temizlik yarıda kalmaz */ } }
  }

  function viewFor(status) {
    if (!status) return 'upload';
    if (status === 'needs_mapping') return 'mapping';
    if (status === 'needs_review' || status === 'ready') return 'preview';
    if (status === 'committed') return 'done';
    if (status === 'rolled_back') return 'rolled_back';
    if (status === 'failed') return 'failed';
    return 'wait';
  }

  function paintStepper(view) {
    const steps = STEPS.map((label) => ({ label }));
    let index = -1;
    if (view === 'wait' && job?.status !== 'committing') { index = 0; steps[1].state = 'dosya çözümleniyor…'; }
    if (view === 'mapping') index = 0;
    if (view === 'preview') index = 1;
    if (view === 'wait' && job?.status === 'committing') { index = 2; steps[3].state = 'kaydediliyor…'; }
    if (view === 'done') index = 3;
    if (view === 'rolled_back') { index = 3; steps[3].state = 'geri alındı'; steps[3].tone = 'dim'; }
    if (view === 'failed') {
      index = job?.mapping ? 1 : 0;
      const failing = steps[Math.min(index + 1, steps.length - 1)];
      failing.state = 'başarısız';
      failing.tone = 'bad';
    }
    stepSlot.replaceChildren(stepper(steps, index));
    if (view === 'loading') statusSlot.replaceChildren();
    else statusSlot.replaceChildren(job ? statusBadge(IMPORT_STATUS, job.status) : badge('Yeni yükleme', 'info'));
  }

  function render(forced = null) {
    if (disposed) return;
    token += 1;
    dispose();
    const view = forced || viewFor(job?.status);
    paintStepper(view);
    body.replaceChildren();
    const steps = {
      upload: uploadStep, wait: waitStep, mapping: mappingStep, preview: previewStep,
      done: doneStep, rolled_back: doneStep, failed: failedStep,
    };
    steps[view]();
    if (view === 'wait') poll();
  }

  async function poll() {
    const mine = token;
    let delay = 800;
    const deadline = Date.now() + 180_000;
    while (!disposed && mine === token && Date.now() < deadline) {
      await wait(delay); // eslint-disable-line no-await-in-loop
      if (disposed || mine !== token) return;
      try {
        job = await app.api.get(`/v1/admin/exam-imports/${job.id}`); // eslint-disable-line no-await-in-loop
      } catch (failure) {
        if (mine === token) body.replaceChildren(errorBox(failure, () => render()));
        return;
      }
      if (disposed || mine !== token) return;
      if (!BUSY.includes(job.status)) {
        if (job.status === 'committed') onChanged?.();
        render();
        return;
      }
      delay = Math.min(delay * 1.4, 4000);
    }
    if (!disposed && mine === token) {
      body.replaceChildren(alertBox('Dosya hâlâ işleniyor. Biraz sonra “Yenile” ile durumu tekrar sorun.', 'warn'),
        toolbar(button('Yenile', { onClick: () => refresh() })));
    }
  }

  async function refresh() {
    try {
      job = await app.api.get(`/v1/admin/exam-imports/${job.id}`);
      render();
    } catch (failure) {
      body.replaceChildren(errorBox(failure, () => refresh()));
    }
  }

  // ------------------------------------------------------------- 1. dosya
  function uploadStep() {
    const state = { file: null, kind: 'results', templateId: '', key: newKey('exam-import') };
    const progress = uploadProgress();
    const progressSlot = h('div');
    const go = actionButton(app, 'admin_uploadExamImport', 'Yükle ve çözümle', { variant: 'primary',
      onClick: () => upload() });
    go.disabled = true;
    const drop = fileDrop({ accept: ['xlsx', 'xls', 'csv'], maxMb: 10,
      label: 'Sonuç dosyasını seçin ya da buraya bırakın',
      onFile: (file) => {
        state.file = file;
        state.key = newKey('exam-import');     // yeni dosya = yeni iş
        if (!go.dataset.blocked) go.disabled = false;
      } });
    const kindSelect = selectBox(optionsOf(IMPORT_KINDS), state.kind, (value) => { state.kind = value; paintKind(); },
      { label: 'Dosya türü' });
    const kindNote = h('div', 'ak-muted');
    const paintKind = () => {
      kindNote.textContent = state.kind === 'answers'
        ? (exam.answerKeyStatus === 'complete'
          ? 'Her öğrencinin cevap dizisi cevap anahtarıyla karşılaştırılır; D/Y/B Akademi\'de hesaplanır.'
          : 'Cevap dizisi için önce “Cevap anahtarı ve konular” sekmesinden anahtarı eksiksiz girin.')
        : 'Dosyada her ders için doğru / yanlış (ve varsa boş) sütunları bulunmalı.';
    };
    paintKind();
    const templateSelect = selectBox([{ value: '', label: 'Otomatik (yayına kayıtlı şablon varsa)' }], '',
      (value) => { state.templateId = value; }, { label: 'Sütun şablonu' });
    loadTemplates(app, exam).then((items) => templateSelect.options([
      { value: '', label: 'Otomatik (yayına kayıtlı şablon varsa)' },
      ...items.map((item) => ({ value: item.id, label: templateLabel(item) }))])).catch(() => {});

    async function upload() {
      if (!state.file) return;
      go.disabled = true;
      progressSlot.replaceChildren(progress.node);
      try {
        const result = await uploadFile(app, `/v1/admin/exams/${exam.id}/imports`, state.file, {
          fields: { kind: state.kind, templateId: state.templateId || undefined },
          progress,
          key: state.key,
        });
        job = result.data;
        if (job?.duplicateOf && job.duplicateOf !== job.id) {
          job = await app.api.get(`/v1/admin/exam-imports/${job.duplicateOf}`);
        }
        if (result.status === 200 || job?.duplicateOf) {
          app.toast('Bu dosya bu deneme için daha önce yüklenmiş; yeni kayıt açılmadı, mevcut iş açıldı.', 'warn');
        }
        render();
      } catch (failure) {
        progressSlot.replaceChildren(errorBox(failure));
        go.disabled = false;
      }
    }

    const form = h('div', 'ak-stack ak-card-pad');
    form.append(drop.node, h('div', 'ak-grid-2 ak-ex-upload-opts'));
    const opts = form.lastChild;
    const kindWrap = h('label', 'kit-field');
    kindWrap.append(h('span', 'kit-field-label', 'Dosya türü'), kindSelect.node, kindNote);
    const templateWrap = h('label', 'kit-field');
    templateWrap.append(h('span', 'kit-field-label', 'Sütun şablonu'), templateSelect.node,
      h('span', 'kit-field-hint', 'Seçilmezse yayına kayıtlı şablon ya da başlıklardan tahmin kullanılır.'));
    opts.append(kindWrap, templateWrap);
    form.append(progressSlot, toolbar(h('span', 'ak-muted', 'En çok 10 MB · 5.000 satır.'), h('span', 'kit-spacer'), go));
    body.append(card('1. Dosya', form));
  }

  // -------------------------------------------------------- işleniyor
  function waitStep() {
    const committing = job?.status === 'committing';
    const box = h('div', 'ak-stack ak-card-pad');
    box.append(h('b', '', committing ? 'Sonuçlar kaydediliyor…' : 'Dosya çözümleniyor…'),
      h('div', 'ak-muted', committing
        ? 'Satırlar öğrenci sonuçlarına yazılıyor. Bu ekranı kapatabilirsiniz; iş arka planda sürer.'
        : 'Sütunlar algılanıyor ve eşleme öneriliyor. Büyük dosyalarda birkaç saniye sürebilir.'),
      h('div', 'ak-muted', job?.fileName || ''),
      toolbar(button('Yenile', { variant: 'ghost', onClick: () => refresh() })));
    body.append(card('', box));
  }

  // ---------------------------------------------------------- 2. eşleme
  function emptyMapping() {
    return {
      sheetName: job?.sheetNames?.[0] || null, headerRow: 0, firstDataRow: 1,
      studentMatch: { schoolNo: null, phone: null, fullName: null, firstName: null, lastName: null },
      bookletType: null, absent: null, publisherNationalRank: null,
      sections: sections.map((item) => ({ sectionId: item.id, correct: null, wrong: null, blank: null, net: null,
        answers: null })),
    };
  }

  /** Eşlemeyi bu denemenin bölümlerine oturtur (eksik bölüm boş, fazlası atılır). */
  function normalize(raw, { positional = false } = {}) {
    const base = emptyMapping();
    if (!raw) return base;
    const valid = new Set((job?.detectedColumns || []).map((column) => column.index));
    const keep = (value) => (value !== null && value !== undefined && valid.has(Number(value)) ? Number(value) : null);
    const given = raw.sections || [];
    return {
      sheetName: raw.sheetName ?? base.sheetName,
      headerRow: Number.isInteger(raw.headerRow) ? raw.headerRow : base.headerRow,
      firstDataRow: Number.isInteger(raw.firstDataRow) ? raw.firstDataRow : base.firstDataRow,
      studentMatch: Object.fromEntries(Object.keys(base.studentMatch)
        .map((field) => [field, keep(raw.studentMatch?.[field])])),
      bookletType: keep(raw.bookletType),
      absent: keep(raw.absent),
      publisherNationalRank: keep(raw.publisherNationalRank),
      sections: sections.map((item, index) => {
        // Şablon başka bir denemeden gelir: bölüm kimliği tutmazsa SIRAYLA eşlenir.
        const same = given.find((value) => value.sectionId === item.id) || (positional ? given[index] : null) || {};
        return { sectionId: item.id, correct: keep(same.correct), wrong: keep(same.wrong), blank: keep(same.blank),
          net: keep(same.net), answers: keep(same.answers) };
      }),
    };
  }

  function mappingStep(initial = null, note = '') {
    const columns = job?.detectedColumns || [];
    const mapping = initial || normalize(job?.mapping || job?.suggestedMapping);
    const answersKind = job?.kind === 'answers';
    const options = [{ value: '', label: '— yok —' },
      ...columns.map((column) => ({ value: String(column.index),
        label: `${columnLetter(column.index)} · ${column.header || '(başlıksız)'}` }))];
    const warnings = h('div', 'ak-stack');
    const selects = [];

    const columnSelect = (get, setValue, label) => {
      const box = selectBox(options, get() === null || get() === undefined ? '' : String(get()), (value) => {
        setValue(value === '' ? null : Number(value));
        paintWarnings();
      }, { label });
      selects.push({ label, get });
      return box.node;
    };
    const field = (label, control, hint = '') => {
      const wrap = h('label', 'kit-field');
      wrap.append(h('span', 'kit-field-label', label), control);
      if (hint) wrap.append(h('span', 'kit-field-hint', hint));
      return wrap;
    };

    // -- üst bilgi + şablon araçları
    const info = note || (job?.templateId ? 'Eşleme kayıtlı şablondan önerildi. Kontrol edip kaydedin.'
      : job?.suggestedMapping ? 'Sütunlar başlıklardan tahmin edildi. Kontrol edip kaydedin.'
        : 'Dosyadaki sütunları aşağıdaki alanlarla eşleyin.');
    body.append(alertBox(info, 'info'));
    let templates = [];
    const templateSelect = selectBox([{ value: '', label: 'Şablon uygula…' }], '', (value) => {
      if (!value) return;
      const template = templates.find((item) => item.id === value);
      if (template) mappingRender(normalize(template.mapping, { positional: true }),
        `“${template.name}” şablonu uygulandı. Bölümler kimlikle, tutmayanlar sırayla eşlendi; kontrol edin.`);
    }, { label: 'Şablon uygula' });
    loadTemplates(app, exam).then((items) => {
      templates = items;
      templateSelect.options([{ value: '', label: items.length ? 'Şablon uygula…' : 'Kayıtlı şablon yok' },
        ...items.map((item) => ({ value: item.id, label: templateLabel(item) }))]);
    }).catch(() => {});
    body.append(toolbar(templateSelect.node,
      job?.suggestedMapping ? button('Önerilen eşlemeye dön', { variant: 'ghost',
        onClick: () => mappingRender(normalize(job.suggestedMapping), 'Önerilen eşleme geri yüklendi.') }) : null,
      button('Şablonları yönet', { variant: 'ghost', onClick: () => templatesDrawer(app, exam, () => {
        loadTemplates(app, exam).then((items) => {
          templates = items;
          templateSelect.options([{ value: '', label: items.length ? 'Şablon uygula…' : 'Kayıtlı şablon yok' },
            ...items.map((item) => ({ value: item.id, label: templateLabel(item) }))]);
        }).catch(() => {});
      }) })));

    // -- algılanan sütunlar
    const detected = dataTable({
      columns: [
        { key: 'index', label: 'Sütun', width: '56px', cell: (row) => columnLetter(row.index) },
        { key: 'header', label: 'Başlık', width: 'minmax(0, 1.2fr)', cell: (row) => row.header || '(başlıksız)' },
        { key: 'samples', label: 'Örnek değerler', width: 'minmax(0, 2fr)',
          cell: (row) => (row.samples || []).slice(0, 4).join(' · ') || '—' },
      ],
      rows: columns,
      dense: true,
      empty: empty('Sütun algılanmadı', 'Dosya boş olabilir ya da başlık satırı farklı bir satırda.'),
    });
    const detectedCard = card(`Algılanan sütunlar (${num(columns.length)})`, detected.node);
    detectedCard.classList.add('ak-ex-detected');

    // -- dosya yapısı
    const structure = h('div', 'ak-grid-3 ak-card-pad');
    if ((job?.sheetNames || []).length > 1) {
      const sheet = selectBox(job.sheetNames.map((name) => ({ value: name, label: name })), mapping.sheetName || '',
        (value) => { mapping.sheetName = value; }, { label: 'Sayfa' });
      structure.append(field('Sayfa', sheet.node, 'Sayfa değişirse sütunlar kaydettikten sonra yeniden okunur.'));
    }
    const rowInput = (key, label, hint) => {
      const input = h('input', 'kit-input ak-ex-count');
      input.inputMode = 'numeric';
      input.value = String((mapping[key] ?? 0) + 1);
      input.setAttribute('aria-label', label);
      input.addEventListener('input', () => {
        const value = Number(input.value);
        if (Number.isInteger(value) && value >= 1) mapping[key] = value - 1;
        paintWarnings();
      });
      return field(label, input, hint);
    };
    structure.append(rowInput('headerRow', 'Başlık satırı', 'Dosyadaki satır numarası (1\'den başlar).'),
      rowInput('firstDataRow', 'İlk öğrenci satırı', 'Başlıktan sonraki ilk veri satırı.'));

    // -- öğrenci eşleme
    const match = h('div', 'ak-grid-3 ak-card-pad');
    const matchFields = [['schoolNo', 'Okul no'], ['phone', 'Telefon'], ['fullName', 'Ad soyad (tek sütun)'],
      ['firstName', 'Ad'], ['lastName', 'Soyad']];
    for (const [key, label] of matchFields) {
      match.append(field(label, columnSelect(() => mapping.studentMatch[key],
        (value) => { mapping.studentMatch[key] = value; }, label)));
    }
    const extra = h('div', 'ak-grid-3 ak-card-pad');
    extra.append(
      field('Kitapçık türü', columnSelect(() => mapping.bookletType, (value) => { mapping.bookletType = value; },
        'Kitapçık türü'), (exam.bookletTypes || []).length > 1 ? 'Birden çok kitapçık var; eşlemeniz önerilir.' : ''),
      field('Girmedi işareti', columnSelect(() => mapping.absent, (value) => { mapping.absent = value; },
        'Girmedi işareti')),
      field('Yayının Türkiye sırası', columnSelect(() => mapping.publisherNationalRank,
        (value) => { mapping.publisherNationalRank = value; }, 'Yayının Türkiye sırası')),
    );

    // -- bölümler
    const grid = h('div', `ak-ex-map-grid${answersKind ? ' ak-ex-mode-answers' : ''}`);
    const headRow = h('div', 'ak-ex-map-row ak-ex-row-head');
    headRow.append(h('span', '', 'Bölüm'));
    const sectionFields = answersKind ? [['answers', 'Cevap dizisi']]
      : [['correct', 'Doğru'], ['wrong', 'Yanlış'], ['blank', 'Boş'], ['net', 'Net (denetim)']];
    for (const [, label] of sectionFields) headRow.append(h('span', '', label));
    grid.append(headRow);
    sections.forEach((item, index) => {
      const target = mapping.sections[index];
      const row = h('div', 'ak-ex-map-row');
      const label = h('span', 'ak-cell-stack');
      label.append(h('span', '', sectionLabel(item)), h('small', '', `${item.questionCount} soru`));
      row.append(label);
      for (const [key, text] of sectionFields) {
        row.append(columnSelect(() => target[key], (value) => { target[key] = value; }, `${sectionLabel(item)} ${text}`));
      }
      grid.append(row);
    });

    // -- şablon olarak sakla + kaydet
    const saveTemplate = h('input', 'kit-check');
    saveTemplate.type = 'checkbox';
    const templateName = h('input', 'kit-input');
    templateName.maxLength = 80;
    templateName.value = [exam.publisher, EXAM_TYPES[exam.type]].filter(Boolean).join(' ') || 'Sonuç şablonu';
    templateName.setAttribute('aria-label', 'Şablon adı');
    const templatePublisher = h('input', 'kit-input');
    templatePublisher.maxLength = 80;
    templatePublisher.value = exam.publisher || '';
    templatePublisher.placeholder = 'Yayın';
    templatePublisher.setAttribute('aria-label', 'Şablonun yayını');
    const paintTemplateInputs = () => {
      templateName.disabled = !saveTemplate.checked;
      templatePublisher.disabled = !saveTemplate.checked;
    };
    saveTemplate.addEventListener('change', paintTemplateInputs);
    paintTemplateInputs();
    const templateRow = h('div', 'ak-inline ak-ex-save-template');
    const templateToggle = h('label', 'ak-inline');
    templateToggle.append(saveTemplate, h('span', '', 'Bu eşlemeyi yayın şablonu olarak sakla'));
    templateRow.append(templateToggle, templateName, templatePublisher);

    const saveError = h('div', 'kit-field-error');
    const save = actionButton(app, 'admin_putExamImportMapping', 'Eşlemeyi kaydet ve önizle', { variant: 'primary',
      onClick: () => submit() });

    function problems() {
      const list = [];
      if (!Object.values(mapping.studentMatch).some((value) => value !== null)) {
        list.push('Öğrenciyi bulmak için en az bir sütun eşleyin (okul no, telefon ya da ad soyad).');
      }
      if (mapping.firstDataRow <= mapping.headerRow) list.push('İlk öğrenci satırı başlık satırından sonra olmalı.');
      sections.forEach((item, index) => {
        const target = mapping.sections[index];
        if (answersKind && target.answers === null) list.push(`${sectionLabel(item)}: cevap dizisi sütununu seçin.`);
        if (!answersKind && (target.correct === null || target.wrong === null)) {
          list.push(`${sectionLabel(item)}: doğru ve yanlış sütunlarını seçin.`);
        }
      });
      return list;
    }

    function paintWarnings() {
      warnings.replaceChildren();
      const used = new Map();
      for (const item of selects) {
        const value = item.get();
        if (value === null || value === undefined) continue;
        if (!used.has(value)) used.set(value, []);
        used.get(value).push(item.label);
      }
      const duplicates = [...used.entries()].filter(([, labels]) => labels.length > 1);
      if (duplicates.length) {
        warnings.append(alertBox(`Aynı sütun birden çok alana eşlendi: ${duplicates
          .map(([index, labels]) => `${columnLetter(index)} (${labels.join(', ')})`).join(' · ')}`, 'warn'));
      }
    }

    async function submit() {
      saveError.textContent = '';
      const list = problems();
      if (list.length) { saveError.textContent = list.join(' '); return; }
      const payload = { mapping: JSON.parse(JSON.stringify(mapping)) };
      if (saveTemplate.checked) {
        const name = templateName.value.trim();
        if (!name) { saveError.textContent = 'Şablon adı yazın.'; return; }
        payload.saveAsTemplate = { name, publisher: templatePublisher.value.trim() || null };
      }
      save.disabled = true;
      try {
        job = await app.api.put(`/v1/admin/exam-imports/${job.id}/mapping`, payload);
        if (payload.saveAsTemplate) {
          app.api.invalidate('/v1/admin/exam-import-templates');
          app.toast(`“${payload.saveAsTemplate.name}” şablonu saklandı.`, 'good');
        }
        render();
      } catch (failure) {
        save.disabled = false;
        saveError.textContent = errorText(failure);
      }
    }

    const left = h('div', 'ak-stack');
    left.append(card('Dosya yapısı', structure), card('Öğrenci eşleme', match),
      h('div', 'ak-muted ak-ex-note', 'Eşleme sırası: okul no → telefon → ad soyad. Tutmayan ya da birden çok '
        + 'öğrenciye uyan satırlar önizlemede onayınıza düşer.'),
      card('Diğer alanlar', extra), card('Bölümler', grid));
    const layout = h('div', 'ak-ex-map-layout');
    layout.append(left, detectedCard);
    const foot = h('div', 'ak-stack ak-card-pad');
    foot.append(templateRow, warnings, saveError, toolbar(h('span', 'kit-spacer'), save));
    body.append(layout, card('2. Kaydet', foot));
    paintWarnings();
  }

  function mappingRender(mapping, note) {
    token += 1;
    dispose();
    body.replaceChildren();
    paintStepper('mapping');
    mappingStep(mapping, note);
  }

  // ------------------------------------------------- 3–4. önizleme ve kayıt
  function previewStep() {
    const stats = job.stats || {};
    const pending = new Map();               // rowNo → öğrenci | null (atla)
    let resolveKey = newKey('exam-match');
    const canResolve = app.can('admin_resolveExamImportMatches');
    const unresolved = (stats.ambiguous || 0) + (stats.unmatched || 0);
    let filter = stats.ambiguous ? 'ambiguous' : stats.unmatched ? 'unmatched' : stats.error ? 'error' : 'all';

    const chips = chipRow([
      { key: 'all', label: 'Tümü', count: stats.rows ?? 0 },
      { key: 'ok', label: 'Hazır', count: stats.ok ?? 0 },
      { key: 'error', label: 'Hatalı', count: stats.error ?? 0 },
      { key: 'ambiguous', label: 'Belirsiz', count: stats.ambiguous ?? 0 },
      { key: 'unmatched', label: 'Eşleşmedi', count: stats.unmatched ?? 0 },
      { key: 'skipped', label: 'Atlanacak', count: stats.skipped ?? 0 },
    ], filter, (key) => {
      filter = key || 'all';
      if (!key) chips.set('all');
      list.reload();
    });
    chips.node.classList.add('ak-ex-chips');

    const pendingNote = h('span', 'ak-muted');
    const saveChoices = actionButton(app, 'admin_resolveExamImportMatches', 'Seçimleri kaydet', {
      variant: 'primary', onClick: () => saveResolutions() });
    const paintPending = () => {
      pendingNote.textContent = pending.size ? `${num(pending.size)} seçim kaydedilmedi` : '';
      if (!saveChoices.dataset.blocked) saveChoices.disabled = !pending.size;
    };

    body.append(
      unresolved ? alertBox(`${num(unresolved)} satırın öğrencisi kesin değil. Doğru öğrenciyi seçin ya da satırı `
        + 'atlayın; çözülmeyen satırlar kaydedilmez.', 'warn') : alertBox('Bütün satırların öğrencisi belli.', 'good'),
      toolbar(chips.node, h('span', 'kit-spacer'), pendingNote,
        button('Eşlemeyi değiştir', { variant: 'ghost', onClick: () => render('mapping') }), saveChoices),
    );

    const list = pagedList({
      app,
      path: `/v1/admin/exam-imports/${job.id}/preview`,
      query: () => ({ filter }),
      dense: true,
      rowKey: (row) => String(row.rowNo),
      columns: [
        { key: 'rowNo', label: 'Satır', width: '58px', align: 'num', cell: (row) => num(row.rowNo) },
        { key: 'rawName', label: 'Dosyadaki ad', width: 'minmax(0, 1.1fr)', cell: (row) => row.rawName || '—' },
        { key: 'student', label: 'Eşleşen öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => matchCell(row) },
        { key: 'status', label: 'Durum', width: '104px', cell: (row) => statusBadge(PREVIEW_STATUS, row.status) },
        { key: 'net', label: 'Toplam net', width: '92px', align: 'num', cell: (row) => valuesCell(row) },
        { key: 'errors', label: 'Hata', width: 'minmax(0, 1.4fr)', cell: (row) => errorsCell(row) },
        { key: 'actions', label: '', width: 'minmax(220px, 1.6fr)', cell: (row) => resolveCell(row) },
      ],
      limit: 50,
      empty: { title: 'Bu süzgece uyan satır yok', text: 'Başka bir süzgeç seçin.' },
    });
    body.append(list.node);
    cleanup.push(() => list.destroy());
    list.reload();

    function matchCell(row) {
      const choice = pending.get(row.rowNo);
      if (choice !== undefined) {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', choice ? choice.displayName : 'Atlanacak'), h('small', '', 'kaydedilmemiş seçim'));
        return cell;
      }
      const student = row.match?.student;
      if (!student) return h('span', 'ak-muted', row.status === 'ambiguous' ? `${num((row.candidates || []).length)} aday` : '—');
      const cell = studentCell(student);
      if (row.match?.by) cell.append(h('small', '', `eşleşme: ${MATCH_BY[row.match.by] || row.match.by}`));
      return cell;
    }

    function valuesCell(row) {
      const values = row.values || {};
      if (values.absent) return badge('Girmedi', 'dim');
      const text = h('span', '', netText(values.totalNet));
      text.title = (values.sections || []).map((item) => {
        const section = sections.find((value) => value.id === item.sectionId);
        return `${sectionLabel(section)}: ${item.correct}D ${item.wrong}Y ${item.blank}B · net ${netText(item.net)}`;
      }).join('\n');
      return text;
    }

    function errorsCell(row) {
      const errors = row.errors || [];
      if (!errors.length) return '—';
      const text = errors.map((item) => `${item.column ? `${item.column}: ` : ''}${item.message}`).join(' · ');
      const cell = h('small', 'ak-ex-row-error', text);
      cell.title = text;
      return cell;
    }

    function resolveCell(row) {
      const box = h('div', 'ak-row-actions ak-ex-resolve');
      const choice = pending.get(row.rowNo);
      if (choice !== undefined) {
        box.append(button('Vazgeç', { variant: 'ghost', onClick: () => { pending.delete(row.rowNo); repaint(row); } }));
        return box;
      }
      if (!canResolve) return box;
      if (row.status === 'ambiguous') {
        for (const candidate of (row.candidates || []).slice(0, 3)) {
          const student = candidate.student;
          box.append(button([student.displayName, student.classGroup?.name].filter(Boolean).join(' · '), {
            variant: 'ghost', title: `Aday (${MATCH_BY[candidate.matchedBy] || candidate.matchedBy} ile)`,
            onClick: () => choose(row, student) }));
        }
      }
      box.append(button(row.match?.student ? 'Değiştir…' : 'Öğrenci seç…', { variant: 'ghost',
        onClick: async () => { const picked = await pickStudent(app, row); if (picked) choose(row, picked); } }));
      if (row.status !== 'skipped') box.append(button('Atla', { variant: 'ghost', onClick: () => choose(row, null) }));
      return box;
    }

    function choose(row, student) {
      pending.set(row.rowNo, student);
      repaint(row);
    }

    function repaint(row) {
      list.replace((item) => item.rowNo === row.rowNo, { ...row });
      paintPending();
    }

    async function saveResolutions() {
      if (!pending.size) return false;
      const resolutions = [...pending.entries()].map(([rowNo, student]) => ({ rowNo, studentId: student ? student.id : null }));
      const next = await runAction(app, () => app.api.post(`/v1/admin/exam-imports/${job.id}/matches`,
        { resolutions }, { key: resolveKey }), { success: `${num(resolutions.length)} satır güncellendi.`,
        button: saveChoices });
      if (!next) return false;
      resolveKey = newKey('exam-match');
      job = next;
      pending.clear();
      render();
      return true;
    }

    // -- 4. kaydet
    const skip = h('input', 'kit-check');
    skip.type = 'checkbox';
    skip.checked = true;
    const overwrite = h('input', 'kit-check');
    overwrite.type = 'checkbox';
    const option = (input, text, hint) => {
      const wrap = h('label', 'ak-ex-option');
      const textNode = h('span', 'ak-cell-stack');
      textNode.append(h('span', '', text), h('small', '', hint));
      wrap.append(input, textNode);
      return wrap;
    };
    const commitError = h('div', 'kit-field-error');
    const commit = actionButton(app, 'admin_commitExamImport', 'Sonuçları kaydet', { variant: 'primary',
      onClick: () => runCommit() });
    const commitBox = h('div', 'ak-stack ak-card-pad');
    commitBox.append(
      kpiRow([
        { label: 'Kaydedilecek', value: num(stats.ok ?? 0) },
        { label: 'Hatalı', value: num(stats.error ?? 0), tone: stats.error ? 'bad' : '' },
        { label: 'Öğrencisi belirsiz', value: num(unresolved), tone: unresolved ? 'warn' : '' },
        { label: 'Atlanacak', value: num(stats.skipped ?? 0) },
      ]),
      option(skip, 'Hatalı ve öğrencisi belirsiz satırları atla, gerisini kaydet (kısmi kayıt)',
        'Kapalıysa tek bir hatalı satır bütün kaydı durdurur.'),
      option(overwrite, 'Bu denemede sonucu zaten olan öğrencilerin sonucunun üzerine yaz',
        'Kapalıysa önceden girilmiş sonuçlar korunur, o satırlar atlanır.'),
      h('div', 'ak-muted', exam.isHistorical ? 'Geçmiş kayıt: bildirim gitmez.'
        : 'Kayıt bildirim göndermez; öğrenciler sonucu deneme yayımlanınca görür.'),
      commitError,
      toolbar(h('span', 'kit-spacer'), commit),
    );
    body.append(card('4. Kaydet', commitBox));

    async function runCommit() {
      commitError.textContent = '';
      const options = { skipErrorRows: skip.checked, overwriteExisting: overwrite.checked };
      if (pending.size) {
        const ok = await confirmAction(app, { title: 'Kaydedilmemiş seçimler var',
          description: `${num(pending.size)} satır için yaptığınız öğrenci seçimi henüz kaydedilmedi. Önce seçimler `
            + 'kaydedilsin, sonra sonuçlar yazılsın mı?', confirmLabel: 'Seçimleri kaydet ve devam et' });
        if (!ok) return;
        if (!await saveResolutions()) return;
      }
      commit.disabled = true;
      try {
        job = await app.api.post(`/v1/admin/exam-imports/${job.id}/commit`, options,
          { key: `exam-import-commit-${job.id}-${options.skipErrorRows ? 1 : 0}${options.overwriteExisting ? 1 : 0}` });
        if (job.status === 'committed') onChanged?.();
        render();
      } catch (failure) {
        commit.disabled = false;
        commitError.textContent = errorText(failure);
        // Seçimler kaydedilip ekran yenilendiyse bu kutu artık görünmüyor.
        if (!commitError.isConnected) app.toast(errorText(failure), 'bad');
      }
    }

    paintPending();
  }

  // --------------------------------------------------------------- sonuç
  function doneStep() {
    const stats = job.stats || {};
    const rolledBack = job.status === 'rolled_back';
    const box = h('div', 'ak-stack ak-card-pad');
    if (rolledBack) {
      box.append(alertBox(`Bu içe aktarma ${fmtDateTime(job.rolledBackAt)} tarihinde geri alındı. Yazdığı sonuçlar `
        + 'silindi; sonradan elle düzeltilen sonuçlar korundu.', 'info'));
    } else {
      box.append(alertBox(`${num(stats.committed ?? 0)} öğrencinin sonucu kaydedildi (${fmtDateTime(job.committedAt)}).`,
        'good'));
    }
    box.append(kpiRow([
      { label: 'Dosyadaki satır', value: num(stats.rows ?? 0) },
      { label: 'Kaydedilen', value: num(stats.committed ?? 0) },
      { label: 'Atlanan', value: num(stats.skipped ?? 0) },
      { label: 'Hatalı', value: num(stats.error ?? 0), tone: stats.error ? 'bad' : '' },
    ]), kvLine('Dosya', job.fileName), kvLine('Yükleyen', job.createdBy || '—'));
    const actions = [];
    if (!rolledBack) {
      actions.push(button('Sonuçları gör', { variant: 'primary', onClick: () => onShowResults?.() }));
      if (exam.status !== 'published') {
        actions.push(actionButton(app, 'admin_publishExam', exam.isHistorical ? 'Yayımla' : 'Yayımla ve bildir',
          { onClick: () => onPublish?.() }));
      }
      actions.push(actionButton(app, 'admin_rollbackExamImport', 'Geri al', { variant: 'ghost', onClick: async () => {
        const next = await rollbackImport(app, job);
        if (next) { job = next; onChanged?.(); render(); }
      } }));
    }
    actions.push(actionButton(app, 'admin_uploadExamImport', 'Yeni dosya yükle', { variant: 'ghost',
      onClick: () => onNew?.() }));
    box.append(toolbar(...actions));
    body.append(card(rolledBack ? 'Geri alındı' : 'Kaydedildi', box));
  }

  function failedStep() {
    const box = h('div', 'ak-stack ak-card-pad');
    box.append(alertBox(job.error || 'Dosya işlenemedi.', 'bad'),
      h('div', 'ak-muted', 'Dosya biçimini (XLSX/CSV), başlık satırını ve sütun eşlemesini kontrol edip yeniden deneyin.'));
    const actions = [];
    if ((job.detectedColumns || []).length) {
      actions.push(button('Eşlemeyi düzelt', { variant: 'primary', onClick: () => render('mapping') }));
    }
    actions.push(actionButton(app, 'admin_uploadExamImport', 'Yeni dosya yükle', { onClick: () => onNew?.() }));
    box.append(toolbar(...actions));
    body.append(card('İşlenemedi', box));
  }

  // ----------------------------------------------------------------- açılış
  if (importId) {
    // Var olan işi aç: hangi adımda olunduğu işin durumundan çıkar.
    paintStepper('loading');
    const view = loader(() => app.api.get(`/v1/admin/exam-imports/${importId}`), (data) => {
      job = data;
      render();
      return [];
    }, { rows: 4, columns: 4 });
    body.append(view.node);
    view.reload();
  } else {
    render('upload');
  }

  return {
    node,
    destroy() {
      disposed = true;
      token += 1;
      dispose();
    },
  };
}

function kvLine(label, value) {
  const line = h('div', 'ak-inline');
  line.append(h('span', 'ak-muted', `${label}:`), h('span', '', value || '—'));
  return line;
}

function errorText(failure) {
  if (failure?.code === 'validation_error' && Array.isArray(failure.details) && failure.details.length) {
    return failure.details.map((item) => item?.message || item?.field).filter(Boolean).join(' · ');
  }
  return failure?.message || 'İşlem tamamlanamadı.';
}

/** Önizleme satırı için öğrenci seçimi (adaylar dışında birini seçmek). */
function pickStudent(app, row) {
  return formDialog(app, {
    title: `Satır ${row.rowNo}: öğrenciyi seçin`,
    description: row.rawName ? `Dosyadaki ad: “${row.rawName}”. Doğru öğrenciyi arayıp seçin.` : 'Doğru öğrenciyi arayıp seçin.',
    fields: [{ key: 'student', label: 'Öğrenci', type: 'custom', required: true, render: (draft, set) => {
      const wrap = h('div', 'ak-stack');
      const chosen = h('div', 'ak-muted', 'Henüz seçilmedi.');
      const picker = studentPicker(app, { onPick: (student) => { set(student); chosen.replaceChildren(studentCell(student)); } });
      wrap.append(picker.node, chosen);
      return wrap;
    } }],
    submitLabel: 'Bu öğrenciyi seç',
    onSubmit: (draft) => draft.student,
  });
}

/** Kayıtlı sütun şablonları: liste + silme. */
function templatesDrawer(app, exam, onChange) {
  const panel = openDrawer(app, { title: 'Sütun eşleme şablonları',
    subtitle: 'Yayın bazında saklanan eşlemeler; eşleme adımında “şablon olarak sakla” ile eklenir.' });
  const table = dataTable({
    columns: [
      { key: 'name', label: 'Şablon', width: 'minmax(0, 1.6fr)' },
      { key: 'publisher', label: 'Yayın', cell: (row) => row.publisher || '—' },
      { key: 'examType', label: 'Tür', cell: (row) => EXAM_TYPES[row.examType] || '—' },
      { key: 'createdAt', label: 'Oluşturma', width: '140px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'actions', label: '', width: '90px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(actionButton(app, 'admin_deleteExamImportTemplate', 'Sil', { variant: 'ghost',
          onClick: () => remove(row) }));
        return actions;
      } },
    ],
    rows: [],
    empty: empty('Kayıtlı şablon yok', 'Eşleme adımında “Bu eşlemeyi yayın şablonu olarak sakla” ile ekleyin.'),
  });
  const view = loader(() => loadTemplates(app, exam), (items) => { table.update({ rows: items }); return table.node; });
  panel.body.append(view.node);
  view.reload();

  async function remove(row) {
    const ok = await confirmAction(app, { title: `“${row.name}” şablonunu sil`,
      description: 'Bu şablonla daha önce yapılmış içe aktarmalar etkilenmez.', confirmLabel: 'Şablonu sil', danger: true });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/exam-import-templates/${row.id}`),
      { success: 'Şablon silindi.' });
    if (done === undefined) return;
    app.api.invalidate('/v1/admin/exam-import-templates');
    view.reload();
    onChange?.();
  }
}
