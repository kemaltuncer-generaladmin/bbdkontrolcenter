// BBD Akademi — Denemeler › Yayınevi Excel'i (kazanım tablosu + cevap anahtarı) ve otomatik eşleştirme
// (F-E10; Akademi README §14 madde 120).
//
// Kullanıcı (16.10.2026): "cevap anahtarı ve konu analiz excelini yükleyeceğiz … yalnız yayınevlerine göre kazanım
// isimlendirmesi değişiyor. otomatik eşleştirme yapmalı … örnek exceli atıyorum bunu yükleyecez ona göre otomatik
// dolduracak."
//
// Sihirbaz: dosya bırak → önizleme (yayınevi, deneme adı, tür, bölümler / soru sayıları, kitapçıklar, anahtar özeti,
// satır hataları; yeni deneme mi var olan mı) → EŞLEŞTİRME TABLOSU (her benzersiz (ders, yayınevi kazanım adı) →
// önerilen müfredat konusu, güven rozeti, soru numaraları, aramalı konu seçici, "yalnız kontrol gerekenler",
// "Önerilenleri onayla") → uygula. Eşleştirmeyi SUNUCU yapar (kurum sözlüğü → başka yayınevinin onayı → birebir →
// eş anlamlı → benzer); yöneticinin kararları sözlüğe yazılır, bir sonraki yüklemede aynı adlar kendiliğinden gelir.
// Uzantı .xls olsa da içerik sezilir (Akademi); burada yalnız boyut / tür ön denetimi.
//
// Ayrıca: denemenin "Kazanım eşleştirmesi" sekmesi (sonradan düzelt → analiz yeniden hesaplanır) ve kurum "Eşleme
// sözlüğü" listesi. Eski CSV / XLSX anahtar + soru → konu yolu (`exams-keys.js`) olduğu gibi kalır.

import { button, h, num } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, hintBox } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { problem } from '../api.js';
import { wizard } from '../shared/wizard.js';
import {
  actionButton, confirmAction, dateInput, empty, errorBox, fileDrop, fmtDateTime, formDialog, kv, loader, optionsOf,
  pagedList, searchBox, selectBox, statusBadge, todayIst, toolbar, uploadProgress,
} from '../common.js';
import {
  CONFIDENCE, CONFIDENCE_ORDER, acceptSuggestions, decisionsBody, examFromPreview, familyOf, filterMatches,
  matchCounts, previewFacts, questionsText, rowState, searchTopics,
} from './exams-app-logic.js';
import { EXAM_TYPES, uploadFile } from './exams-shared.js';

export const PUBLISHER_TABLES = '/v1/admin/exam-publisher-tables';
export const TOPIC_ALIASES = '/v1/admin/exam-topic-aliases';
const PROGRAMS = '/v1/admin/curricula/programs';

const percentText = (ratio) => `%${num(Math.round((Number(ratio) || 0) * 100))}`;

// ================================================================ konu seçici

/**
 * Aramalı müfredat konu seçici (penceresi). Kapsam: denemenin sınav ailesi (TYT / AYT / LGS) + satırın dersi; ders
 * değiştirilebilir (yayınevinin "Ders" sütunu yanlış derse düşmüş olabilir). Öneriler (alternatifler) en üstte.
 * Ünite başlığı da seçilebilir (ünite düzeyi eşleme). @returns {Promise<object|null>} TopicMatchTarget
 */
export async function chooseTopic(app, { row, examType }) {
  let subjects = [];
  try { subjects = await app.lookups.subjects(); } catch { subjects = []; }
  const family = familyOf(examType);
  let chosen = null;
  return formDialog(app, {
    title: 'Müfredat konusu seç',
    description: `Yayınevi kazanımı: “${row.name}”${row.code ? ` (kod ${row.code})` : ''} · ${row.subjectRaw || ''}`,
    wide: true,
    fields: [{
      key: 'target', label: 'Konu', type: 'custom', required: true,
      render: (draft, set) => {
        const node = h('div', 'ak-stack ak-ex-topic-pick');
        let subjectId = row.subjectId || subjects[0]?.id || '';
        let programs = [];
        let token = 0;
        const subject = selectBox([{ value: '', label: 'Ders seçin' },
          ...subjects.map((item) => ({ value: item.id, label: item.name }))], subjectId, (value) => {
          subjectId = value; loadPrograms();
        }, { label: 'Ders' });
        const search = searchBox({ placeholder: 'Konu ya da ünite ara (ör. sözcükte anlam)', label: 'Konu ara',
          delay: 150, value: '', onSearch: () => paint() });
        const list = h('div', 'ak-ex-topic-results');
        list.setAttribute('role', 'listbox');
        list.setAttribute('aria-label', 'Konular');
        node.append(toolbar(subject.node, search.node), list);

        function option(target, note = '') {
          const item = h('button', `ak-ex-topic-option${chosen && same(chosen, target) ? ' on' : ''}`);
          item.type = 'button';
          item.setAttribute('role', 'option');
          item.setAttribute('aria-selected', chosen && same(chosen, target) ? 'true' : 'false');
          const line = h('span', 'ak-inline');
          line.append(h('b', '', target.title), target.level === 'unit' ? badge('Ünite', 'dim') : '');
          if (note) line.append(badge(note, 'info'));
          item.append(line, h('small', '', target.path || ''));
          item.addEventListener('click', () => { chosen = target; set(target); paint(); });
          return item;
        }

        function paint() {
          list.replaceChildren();
          const suggestions = [row.target, ...(row.alternatives || [])].filter(Boolean);
          if (suggestions.length && !search.value) {
            list.append(h('div', 'ak-ex-topic-group', 'Öneriler'));
            const seen = new Set();
            for (const target of suggestions) {
              const id = `${target.level}:${target.topicId}:${target.unitId}`;
              if (seen.has(id)) continue;
              seen.add(id);
              list.append(option(target, target === row.target ? 'İlk öneri' : ''));
            }
          }
          if (!programs.length) {
            list.append(h('div', 'ak-muted', subjectId ? 'Bu derste müfredat programı bulunamadı.' : 'Ders seçin.'));
            return;
          }
          const hits = searchTopics(programs, search.value, { limit: 60 });
          list.append(h('div', 'ak-ex-topic-group', search.value ? `Arama sonuçları (${num(hits.length)})` : 'Müfredat'));
          if (!hits.length) list.append(h('div', 'ak-muted', 'Aramaya uyan konu yok. Başka bir sözcük deneyin.'));
          for (const target of hits) list.append(option(target));
        }

        async function loadPrograms() {
          const mine = ++token;
          programs = [];
          list.replaceChildren(h('div', 'ak-muted', 'Konular yükleniyor…'));
          if (!subjectId) { paint(); return; }
          try {
            const query = { subject: subjectId };
            if (family) query.exam = family;
            const data = await app.api.getCached(PROGRAMS, query, { ttlMs: 10 * 60_000 });
            const items = (data?.items || []).filter((item) => !family || item.exam === family || !item.exam).slice(0, 4);
            const details = await Promise.all(items.map((item) => app.api.getCached(`${PROGRAMS}/${item.id}`, undefined,
              { ttlMs: 10 * 60_000 })));
            if (mine !== token) return;
            programs = details.filter(Boolean).map((program) => ({ ...program,
              units: [...(program.units || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0)) }));
            paint();
          } catch (failure) {
            if (mine !== token) return;
            list.replaceChildren(errorBox(failure, () => loadPrograms()));
          }
        }
        loadPrograms();
        return { node, update() {}, destroy: () => search.destroy() };
      },
    }],
    submitLabel: 'Bu konuyu seç',
    onSubmit: (draft) => draft.target,
  });
}

function same(a, b) {
  return a.level === b.level && (a.topicId || null) === (b.topicId || null) && (a.unitId || null) === (b.unitId || null);
}

// ============================================================ eşleştirme tablosu

/**
 * Eşleştirme tablosu. `decisions` (Map matchKey → {target} | {skip:true}) dışarıda tutulur; her değişimde
 * `onChange()`. Sayaçlar, süzgeç, "Önerilenleri onayla".
 */
export function matchingTable(app, { rows: given, decisions, examType, sectionTitle = (key) => key, onChange }) {
  const node = h('div', 'ak-stack ak-ex-match');
  let rows = given || [];
  const filters = { onlyReview: false, confidence: '', subjectId: '', q: '' };
  const summary = h('div', 'ak-ex-match-summary');
  summary.setAttribute('aria-live', 'polite');

  const onlyReview = h('input', 'kit-check');
  onlyReview.type = 'checkbox';
  onlyReview.addEventListener('change', () => { filters.onlyReview = onlyReview.checked; paint(); });
  const reviewLabel = h('label', 'ak-inline ak-ex-toggle');
  const reviewText = h('span', '', 'Yalnız kontrol gerekenler');
  reviewLabel.append(onlyReview, reviewText);
  const confidence = selectBox([{ value: '', label: 'Tüm güven düzeyleri' },
    ...CONFIDENCE_ORDER.map((key) => ({ value: key, label: CONFIDENCE[key][0] }))], '', (value) => {
    filters.confidence = value; paint();
  }, { label: 'Güven' });
  const subject = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => { filters.subjectId = value; paint(); },
    { label: 'Ders' });
  const search = searchBox({ placeholder: 'Kazanım ya da konu ara', label: 'Kazanım ara', delay: 150,
    onSearch: (text) => { filters.q = text; paint(); } });
  const accept = button('Önerilenleri onayla', { onClick: () => {
    const added = acceptSuggestions(rows, decisions);
    app.toast(added ? `${num(added)} benzer eşleşme önerisi onaylandı.` : 'Onay bekleyen öneri yok.', added ? 'good' : 'info');
    changed();
  } });
  accept.title = '“Benzer — kontrol et” satırlarına ilk öneriyi uygular; “Eşleşmedi” satırlar elle seçilir.';

  const table = dataTable({
    columns: [
      { key: 'subject', label: 'Ders', width: 'minmax(90px, 0.7fr)', cell: (row) => row.subjectRaw || '—' },
      { key: 'name', label: 'Yayınevi kazanımı', width: 'minmax(180px, 2fr)', cell: (row) => nameCell(row) },
      { key: 'questions', label: 'Sorular', width: 'minmax(110px, 1fr)', cell: (row) => questionsCell(row) },
      { key: 'target', label: 'Müfredat konusu', width: 'minmax(180px, 2fr)', cell: (row) => targetCell(row) },
      { key: 'confidence', label: 'Güven', width: '150px', cell: (row) => confidenceCell(row) },
      { key: 'actions', label: '', width: '150px', cell: (row) => actionsCell(row) },
    ],
    rows: [],
    dense: true,
    rowKey: (row) => row.matchKey,
    empty: empty('Bu süzgece uyan kazanım yok', 'Süzgeci temizleyin.'),
  });

  node.append(summary, toolbar(reviewLabel, confidence.node, subject.node, search.node, h('span', 'kit-spacer'), accept),
    table.node);

  function nameCell(row) {
    const wrap = h('div', 'ak-cell-stack');
    wrap.append(h('span', 'ak-ex-match-name', row.name));
    if (row.code) wrap.append(h('small', '', `kod ${row.code}`));
    wrap.title = row.name;
    return wrap;
  }

  function questionsCell(row) {
    const text = questionsText(row.questions, sectionTitle);
    const span = h('span', 'ak-ex-match-q', `${num((row.questions || []).length)} soru · ${text}`);
    span.title = text;
    return span;
  }

  function targetCell(row) {
    const { state, target } = rowState(row, decisions.get(row.matchKey));
    const wrap = h('div', 'ak-cell-stack');
    if (state === 'skipped') {
      wrap.append(h('span', 'ak-muted', 'Eşlenmeyecek'), h('small', '', 'Sorular puanlanır; konu analizinde “eşleşmemiş”.'));
      return wrap;
    }
    if (!target) {
      wrap.append(h('span', 'ak-ex-match-missing', 'Konu seçilmedi'));
      return wrap;
    }
    const line = h('span', 'ak-inline');
    line.append(h('b', '', target.title));
    if (target.level === 'unit') line.append(badge('Ünite', 'dim'));
    wrap.append(line, h('small', '', target.path || ''));
    wrap.title = target.path || target.title;
    return wrap;
  }

  function confidenceCell(row) {
    const { state } = rowState(row, decisions.get(row.matchKey));
    const wrap = h('div', 'ak-cell-stack');
    if (state === 'decided') wrap.append(badge('Siz seçtiniz', 'good'));
    else if (state === 'skipped') wrap.append(badge('Atlandı', 'dim'));
    else wrap.append(statusBadge(CONFIDENCE, row.confidence));
    if (state !== 'decided' && row.score !== undefined && row.confidence !== 'none') {
      wrap.append(h('small', '', `benzerlik ${percentText(row.score)}`));
    }
    return wrap;
  }

  function actionsCell(row) {
    const wrap = h('div', 'ak-row-actions');
    const decision = decisions.get(row.matchKey);
    wrap.append(button(rowState(row, decision).target ? 'Değiştir' : 'Konu seç', { onClick: async () => {
      const target = await chooseTopic(app, { row, examType });
      if (!target || target === true) return;
      decisions.set(row.matchKey, { target });
      changed();
    } }));
    if (decision) {
      wrap.append(button('Geri al', { variant: 'ghost', title: 'Önerilen eşleşmeye dön', onClick: () => {
        decisions.delete(row.matchKey); changed();
      } }));
    } else {
      wrap.append(button('Eşleme', { variant: 'ghost', title: 'Bu kazanımı eşlemeden bırak', onClick: () => {
        decisions.set(row.matchKey, { skip: true }); changed();
      } }));
    }
    return wrap;
  }

  function paintSummary() {
    const counts = matchCounts(rows, decisions);
    const parts = [
      `${num(counts.questions)} soru · ${num(counts.rows)} benzersiz kazanım`,
      `${percentText(counts.rate)} eşlendi (${num(counts.mappedQuestions)} soru)`,
    ];
    summary.replaceChildren();
    const line = h('div', 'ak-inline');
    line.append(h('b', '', parts.join(' · ')));
    for (const key of CONFIDENCE_ORDER.slice().reverse()) {
      if (counts.byConfidence[key]) line.append(badge(`${CONFIDENCE[key][0]} ${num(counts.byConfidence[key])}`, CONFIDENCE[key][1]));
    }
    summary.append(line);
    summary.append(counts.review
      ? alertBox(`${num(counts.review)} kazanım (${num(counts.reviewQuestions)} soru) kontrol bekliyor. Seçmezseniz sorular `
        + 'yine puanlanır, konu analizinde “eşleşmemiş” görünür; sonradan “Kazanım eşleştirmesi” sekmesinden düzeltilebilir.', 'warn')
      : alertBox('Bütün kazanımların kararı verildi.', 'good'));
    reviewText.textContent = `Yalnız kontrol gerekenler (${num(counts.review)})`;
  }

  function paint() {
    table.update({ rows: filterMatches(rows, decisions, filters) });
    paintSummary();
  }

  function changed() {
    paint();
    onChange?.();
  }

  function setRows(next) {
    rows = next || [];
    const subjects = new Map();
    for (const row of rows) {
      const id = row.subjectId || row.subjectRaw;
      if (id && !subjects.has(id)) subjects.set(id, row.subjectRaw || id);
    }
    subject.options([{ value: '', label: 'Tüm dersler' }, ...[...subjects].map(([value, label]) => ({ value, label }))]);
    // Kontrol bekleyen varsa tablo onlarla açılır (en çok işi olan görünüm önce).
    const counts = matchCounts(rows, decisions);
    filters.onlyReview = counts.review > 0 && counts.review < counts.rows;
    onlyReview.checked = filters.onlyReview;
    paint();
  }
  setRows(rows);

  return { node, setRows, refresh: paint, destroy: () => search.destroy() };
}

// ============================================================== sihirbaz

/**
 * Yayınevi Excel'i sihirbazı. `exam` verilirse o denemeye uygulanır (adım 2'de hedef seçimi yok).
 * @returns {Promise<object|null>} PublisherTableApplyResult
 */
export async function publisherWizard(app, { exam = null } = {}) {
  let file = null;
  let preview = null;
  let previewFor = null;          // hangi dosya/tür/yayınevi için alındı
  const decisions = new Map();
  const options = { examType: exam?.type || '', publisher: '' };
  const target = { mode: exam ? 'existing' : 'new', examId: exam?.id || '', name: '', date: todayIst(), type: '',
    subjects: {}, applyAnswerKeys: true };
  let subjects = [];
  try { subjects = await app.lookups.subjects(); } catch { subjects = []; }
  let table = null;

  const sectionTitle = (key) => preview?.sections?.find((item) => item.key === key)?.title || key;

  async function ensurePreview() {
    const signature = `${file?.name}|${file?.size}|${file?.lastModified}|${options.examType}|${options.publisher}`;
    if (preview && previewFor === signature) return null;
    const progress = uploadProgress();
    progressSlot.replaceChildren(progress.node);
    try {
      const response = await uploadFile(app, `${PUBLISHER_TABLES}/preview`, file,
        { fields: { examType: options.examType || undefined, publisher: options.publisher || undefined }, progress });
      preview = response.data;
      previewFor = signature;
      decisions.clear();
      target.name = preview?.examName || target.name || '';
      target.type = preview?.examType || options.examType || target.type || '';
      return null;
    } catch (failure) {
      progressSlot.replaceChildren();
      return problem(failure, 'Dosya okunamadı.');
    }
  }

  const progressSlot = h('div');

  function fileStep() {
    const node = h('div', 'ak-stack');
    const drop = fileDrop({ accept: ['xlsx', 'xls', 'csv'], maxMb: 5,
      label: 'Yayınevinin kazanım tablosunu (cevap anahtarlı) seçin ya da buraya bırakın',
      onFile: (picked) => { file = picked; } });
    const type = selectBox([{ value: '', label: 'Dosyadan algıla' }, ...optionsOf(EXAM_TYPES)], options.examType,
      (value) => { options.examType = value; }, { label: 'Deneme türü' });
    const publisher = h('input', 'kit-input');
    publisher.maxLength = 80;
    publisher.placeholder = 'Dosyadaki adı kullan';
    publisher.setAttribute('aria-label', 'Yayınevi adı');
    publisher.addEventListener('input', () => { options.publisher = publisher.value.trim(); });
    const typeField = h('label', 'kit-field');
    typeField.append(h('span', 'kit-field-label', 'Deneme türü'), type.node);
    const publisherField = h('label', 'kit-field');
    publisherField.append(h('span', 'kit-field-label', 'Yayınevi (isteğe bağlı)'), publisher);
    const grid = h('div', 'kit-form-grid');
    grid.append(typeField, publisherField);
    node.append(
      hintBox('Yayınevinin “kazanım tablosu + cevap anahtarı” dosyası (ör. ÇAP TYT 0). Başlık satırı: Kitapçık · Test · '
        + 'Ders · A Soru · B Soru · Cevap · Kazanım Kodu · Kazanım-1…; B kitapçığının anahtarı “B Soru” sütunundan çıkarılır. '
        + 'Uzantı .xls olsa da içerik tanınır.'),
      drop.node, grid, progressSlot,
    );
    if (exam) node.append(alertBox(`Tablo “${exam.name}” denemesine uygulanacak (cevap anahtarı ve soru → konu).`, 'info'));
    return node;
  }

  function previewStep() {
    const node = h('div', 'ak-stack');
    const slot = h('div', 'ak-stack');
    node.append(slot);
    return {
      node,
      enter() { slot.replaceChildren(...previewNodes()); },
    };
  }

  function previewNodes() {
    if (!preview) return [h('div', 'ak-muted', 'Önizleme yok.')];
    const nodes = [];
    const facts = card('Dosyada bulunanlar', kv(previewFacts(preview, (type) => EXAM_TYPES[type] || type)),
      preview.fileName);
    facts.classList.add('ak-ex-summary');
    nodes.push(facts);
    if (preview.appliedExamId) {
      nodes.push(alertBox(`Bu dosya ${fmtDateTime(preview.appliedAt)} tarihinde bir denemeye uygulanmıştı; aynı denemeye `
        + 'yeniden uygulamak yeni kayıt açmaz, yalnız eşleştirme kararlarınızı işler.', 'info'));
    }
    const errors = preview.errors || [];
    if (errors.length) {
      nodes.push(alertBox(`${num(errors.length)} satır okunamadı; bu sorular atlanır. Dosyayı düzeltip yeniden yükleyebilirsiniz.`, 'warn'));
      nodes.push(dataTable({
        columns: [
          { key: 'rowNo', label: 'Satır', width: '64px', align: 'num' },
          { key: 'column', label: 'Sütun', width: '120px', cell: (row) => row.column || '—' },
          { key: 'message', label: 'Sorun', width: 'minmax(0, 2fr)' },
        ],
        rows: errors.slice(0, 50),
        dense: true,
      }).node);
    } else {
      nodes.push(alertBox('Dosya hatasız okundu.', 'good'));
    }
    nodes.push(targetNode());
    return nodes;
  }

  function targetNode() {
    const box = h('div', 'ak-stack ak-ex-target');
    const keys = h('input', 'kit-check');
    keys.type = 'checkbox';
    keys.checked = target.applyAnswerKeys;
    keys.addEventListener('change', () => { target.applyAnswerKeys = keys.checked; });
    const keysLabel = h('label', 'ak-inline ak-ex-toggle');
    keysLabel.append(keys, h('span', '', 'Cevap anahtarını da yaz (kitapçık başına)'));
    if (exam) {
      box.append(h('b', '', `Uygulanacak deneme: ${exam.name}`), keysLabel);
      return box;
    }
    const mode = selectBox([{ value: 'new', label: 'Yeni deneme oluştur' }, { value: 'existing', label: 'Var olan denemeye uygula' }],
      target.mode, (value) => { target.mode = value; paintMode(); }, { label: 'Nereye uygulanacak' });
    const modeField = h('label', 'kit-field');
    modeField.append(h('span', 'kit-field-label', 'Nereye uygulanacak'), mode.node);
    const slot = h('div', 'ak-stack');
    box.append(h('b', '', 'Deneme'), modeField, slot, keysLabel);

    function paintMode() {
      slot.replaceChildren();
      if (target.mode === 'existing') {
        const select = selectBox([{ value: '', label: 'Denemeler yükleniyor…' }], target.examId, (value) => {
          target.examId = value;
        }, { label: 'Deneme' });
        const field = h('label', 'kit-field kit-field-wide');
        field.append(h('span', 'kit-field-label', 'Deneme'), select.node,
          h('span', 'kit-field-hint', 'Bölüm adları ve soru sayıları tabloyla aynı olmalı; sonuç girilmiş denemenin yapısı değişmez.'));
        slot.append(field);
        app.api.get('/v1/admin/exams', { type: target.type || undefined, limit: 50 }).then((data) => {
          select.options([{ value: '', label: 'Deneme seçin' }, ...(data?.items || []).map((item) => ({ value: item.id,
            label: `${item.name} · ${item.date || ''}` }))]);
          select.set(target.examId);
        }).catch(() => select.options([{ value: '', label: 'Deneme listesi alınamadı' }]));
        return;
      }
      const grid = h('div', 'kit-form-grid');
      const name = h('input', 'kit-input');
      name.maxLength = 120;
      name.value = target.name;
      name.setAttribute('aria-label', 'Deneme adı');
      name.addEventListener('input', () => { target.name = name.value; });
      const nameField = h('label', 'kit-field kit-field-wide');
      nameField.append(h('span', 'kit-field-label', 'Deneme adı'), name);
      const date = dateInput(target.date, (iso) => { target.date = iso || ''; }, 'Sınav tarihi');
      const dateField = h('div', 'kit-field');
      dateField.append(h('span', 'kit-field-label', 'Sınav tarihi'), date.node);
      const type = selectBox([{ value: '', label: 'Tür seçin' }, ...optionsOf(EXAM_TYPES)], target.type, (value) => {
        target.type = value;
      }, { label: 'Tür' });
      const typeField = h('label', 'kit-field');
      typeField.append(h('span', 'kit-field-label', 'Tür'), type.node);
      grid.append(nameField, dateField, typeField);
      // Katalog dersi bulunamayan bölüm için ders seçimi.
      for (const section of preview?.sections || []) {
        if (section.subjectId) continue;
        const pick = selectBox([{ value: '', label: 'Ders seçin' }, ...subjects.map((item) => ({ value: item.id, label: item.name }))],
          target.subjects[section.key] || '', (value) => { target.subjects[section.key] = value; },
          { label: `${section.title} dersi` });
        const field = h('label', 'kit-field');
        field.append(h('span', 'kit-field-label', `${section.title} — ders`), pick.node);
        grid.append(field);
      }
      slot.append(grid, h('span', 'kit-field-hint', 'Bölümler tablodan gelir (test sırası, soru sayısı). Uygulamada çözdürmek '
        + 'için denemeyi açıp “Düzenle → Nasıl çözülecek? Uygulamada” seçin.'));
    }
    paintMode();
    return box;
  }

  function matchStep() {
    const node = h('div', 'ak-stack');
    const slot = h('div', 'ak-stack');
    const rematch = button('Sözlükle yeniden eşleştir', { variant: 'ghost', onClick: async () => {
      if (!preview?.id) return;
      rematch.disabled = true;
      try {
        preview = await app.api.get(`/v1/admin/exam-publisher-tables/${preview.id}`);
        table?.setRows(preview.matches || []);
      } catch (failure) {
        app.toast(problem(failure), 'bad');
      } finally {
        rematch.disabled = false;
      }
    } });
    rematch.title = 'Sözlüğe bu arada eklenen onayları da kullanarak eşleştirmeyi yeniden hesaplatır.';
    node.append(slot);
    return {
      node,
      enter() {
        if (!table) {
          table = matchingTable(app, { rows: preview?.matches || [], decisions, sectionTitle,
            examType: target.type || exam?.type || preview?.examType || '' });
          slot.replaceChildren(toolbar(h('span', 'ak-muted', 'Kararlarınız kurum sözlüğüne yazılır; bir sonraki yüklemede '
            + 'aynı adlar kendiliğinden eşleşir.'), h('span', 'kit-spacer'), rematch), table.node);
        } else {
          table.setRows(preview?.matches || []);
        }
      },
      destroy: () => table?.destroy(),
    };
  }

  return wizard(app, {
    title: exam ? `Yayınevi Excel'i — ${exam.name}` : "Yayınevi Excel'i yükle",
    finishLabel: 'Uygula',
    steps: [
      { key: 'file', label: 'Dosya', render: () => fileStep(),
        validate: async () => (file ? ensurePreview() : 'Önce dosyayı seçin.') },
      { key: 'preview', label: 'Önizleme', render: () => previewStep(),
        validate: () => {
          if (exam) return null;
          if (target.mode === 'existing') return target.examId ? null : 'Uygulanacak denemeyi seçin.';
          const made = examFromPreview(preview, { name: target.name, date: target.date, type: target.type,
            publisher: options.publisher, subjects: target.subjects });
          if (made.error) return made.error;
          return target.date ? null : 'Sınav tarihini seçin.';
        } },
      { key: 'match', label: 'Kazanım eşleştirmesi', render: () => matchStep() },
    ],
    onFinish: async ({ progress }) => {
      if (!app.can('admin_applyExamPublisherTable')) {
        throw new Error('Tabloyu denemeye uygulama yetkiniz yok (yöneticinizden izin isteyin).');
      }
      progress('Uygulanıyor…');
      const body = { decisions: decisionsBody(decisions), acceptSuggestions: false,
        applyAnswerKeys: target.applyAnswerKeys };
      if (exam || target.mode === 'existing') body.examId = exam?.id || target.examId;
      else {
        body.exam = examFromPreview(preview, { name: target.name, date: target.date, type: target.type,
          publisher: options.publisher, subjects: target.subjects }).exam;
      }
      const result = await app.api.post(`/v1/admin/exam-publisher-tables/${preview.id}/apply`, body);
      app.api.invalidate?.('/v1/admin/exams');
      app.toast(applyText(result), result?.unmappedQuestions ? 'warn' : 'good');
      return result;
    },
  });
}

export function applyText(result) {
  if (!result) return '';
  const parts = [result.alreadyApplied ? 'Tablo bu denemeye zaten uygulanmıştı; kararlarınız işlendi'
    : `${num(result.appliedQuestions)} soru uygulandı`];
  parts.push(`${num(result.mappedQuestions)} soru konuya eşlendi`);
  if (result.unmappedQuestions) parts.push(`${num(result.unmappedQuestions)} soru eşleşmeden kaldı`);
  if (result.aliasesSaved) parts.push(`${num(result.aliasesSaved)} eşleme sözlüğe yazıldı`);
  if (result.resultsRecomputed) parts.push('sonuçlar yeniden hesaplanıyor');
  return `${parts.join(' · ')}.`;
}

// ===================================================== deneme sekmesi: eşleştirme

/** Denemenin "Kazanım eşleştirmesi" sekmesi: sonradan düzelt (+ sözlüğe yaz) → analiz yeniden hesaplanır. */
export function matchingTab(app, exam, { onChanged, onImport } = {}) {
  const node = h('div', 'ak-stack');
  const decisions = new Map();
  let table = null;
  let data = null;
  const error = h('div', 'kit-field-error');
  const save = actionButton(app, 'admin_putExamTopicMatching', 'Değişiklikleri kaydet', { variant: 'primary',
    onClick: () => submit() });
  const importButton = actionButton(app, 'admin_previewExamPublisherTable', "Yayınevi Excel'i yükle",
    { onClick: () => onImport?.() });

  const view = loader(() => app.api.get(`/v1/admin/exams/${exam.id}/topic-matching`), (payload) => {
    data = payload;
    decisions.clear();
    table?.destroy();
    table = null;
    if (!(payload?.matches || []).length) {
      return empty('Bu deneme yayınevi tablosuyla kurulmadı',
        'Yayınevinin kazanım tablosunu yükleyin; kazanımlar müfredat konularına kendiliğinden eşlenir. Soru → konu '
        + 'eşlemesi elle de “Cevap anahtarı ve konular” sekmesinden girilebilir.', [importButton]);
    }
    const sections = new Map((exam.sections || []).map((item) => [item.id, item.title]));
    table = matchingTable(app, { rows: payload.matches, decisions, examType: exam.type,
      sectionTitle: (key) => sections.get(key) || key, onChange: () => paintSave() });
    const wrap = h('div', 'ak-stack');
    wrap.append(toolbar(h('span', 'ak-muted', [payload.publisher ? `Yayınevi: ${payload.publisher}` : '',
      'Kaydedince denemenin konu analizi yeniden hesaplanır; kararlar kurum sözlüğüne yazılır.'].filter(Boolean).join(' · ')),
    h('span', 'kit-spacer'), importButton), table.node, error, toolbar(h('span', 'kit-spacer'), save));
    paintSave();
    return wrap;
  });
  node.append(card('Kazanım eşleştirmesi', view.node, 'yayınevi kazanımı → müfredat konusu'));

  function paintSave() {
    save.disabled = !decisions.size || !app.can('admin_putExamTopicMatching');
    save.textContent = decisions.size ? `Değişiklikleri kaydet (${num(decisions.size)})` : 'Değişiklikleri kaydet';
  }

  async function submit() {
    if (!decisions.size) return;
    error.textContent = '';
    save.disabled = true;
    try {
      const next = await app.api.put(`/v1/admin/exams/${exam.id}/topic-matching`, { decisions: decisionsBody(decisions) });
      app.toast(`${num(decisions.size)} kazanımın eşlemesi kaydedildi; konu analizi yeniden hesaplanıyor.`, 'good');
      decisions.clear();
      data = next;
      table?.setRows(next?.matches || data?.matches || []);
      paintSave();
      onChanged?.();
    } catch (failure) {
      error.textContent = problem(failure);
      paintSave();
    }
  }

  return {
    node,
    reload: () => { if (!decisions.size) view.reload(); },
    destroy: () => table?.destroy(),
  };
}

// ============================================================ eşleme sözlüğü

/** Kurum eşleme sözlüğü: yayınevi kazanım adı → müfredat konusu (onaylananlar). */
export function aliasesPanel(app) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', publisher: '' };
  const search = searchBox({ placeholder: 'Kazanım ya da konu ara', label: 'Sözlükte ara',
    onSearch: (text) => { filters.q = text; list.reload(); } });
  const publisher = searchBox({ placeholder: 'Yayınevi', label: 'Yayınevi süzgeci',
    onSearch: (text) => { filters.publisher = text; list.reload(); } });
  const list = pagedList({
    app,
    path: TOPIC_ALIASES,
    query: () => ({ q: filters.q || undefined, publisher: filters.publisher || undefined }),
    dense: true,
    columns: [
      { key: 'publisher', label: 'Yayınevi', width: '110px', cell: (row) => row.publisher || 'Hepsi' },
      { key: 'name', label: 'Yayınevi kazanımı', width: 'minmax(180px, 2fr)', cell: (row) => {
        const wrap = h('div', 'ak-cell-stack');
        wrap.append(h('span', '', row.name), h('small', '', [row.subjectId, row.code ? `kod ${row.code}` : ''].filter(Boolean).join(' · ')));
        return wrap;
      } },
      { key: 'target', label: 'Müfredat konusu', width: 'minmax(180px, 2fr)', cell: (row) => {
        const wrap = h('div', 'ak-cell-stack');
        wrap.append(h('b', '', row.target?.title || '—'), h('small', '', row.target?.path || ''));
        return wrap;
      } },
      { key: 'usage', label: 'Kullanım', width: '80px', align: 'num', cell: (row) => num(row.usageCount) },
      { key: 'by', label: 'Onaylayan', width: '130px', cell: (row) => row.approvedBy || '—' },
      { key: 'at', label: 'Güncelleme', width: '130px', cell: (row) => fmtDateTime(row.updatedAt) },
      { key: 'actions', label: '', width: '70px', cell: (row) => actionButton(app, 'admin_deleteExamTopicAlias', 'Sil',
        { variant: 'ghost', onClick: () => remove(row) }) },
    ],
    empty: { title: 'Sözlük boş', text: 'Yayınevi tablosu yükleyip eşleştirmeyi onayladıkça kurumun sözlüğü dolar; '
      + 'sonraki yüklemelerde aynı adlar kendiliğinden eşleşir.' },
  });
  node.append(hintBox('Onayladığınız eşlemeler burada durur. Yanlış bir eşlemeyi silerseniz sonraki yüklemede o ad yeniden '
    + 'önerilir; var olan denemelerin eşlemesi değişmez (deneme içinden “Kazanım eşleştirmesi” ile düzeltilir).'),
  toolbar(search.node, publisher.node), list.node);

  async function remove(row) {
    const ok = await confirmAction(app, { title: 'Sözlük satırını sil',
      description: `“${row.name}” → “${row.target?.title || '—'}” eşlemesi sözlükten çıkar.`, confirmLabel: 'Sil', danger: true });
    if (!ok) return;
    try {
      await app.api.del(`${TOPIC_ALIASES}/${row.id}`);
      list.remove((item) => item.id === row.id);
      app.toast('Sözlük satırı silindi.', 'good');
    } catch (failure) {
      app.toast(problem(failure), 'bad');
    }
  }

  return {
    node,
    reload: () => list.reload(),
    destroy: () => { list.destroy(); search.destroy(); publisher.destroy(); },
  };
}
