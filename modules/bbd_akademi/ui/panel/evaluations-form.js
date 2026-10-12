// BBD Akademi — Değerlendirme formu (F-A21: değerlendirme yapma, paylaşım).
//
// Çekmecede iki sütun: solda form (kriter puanları + kriter notu, genel
// yorum, güçlü yönler, gelişim alanları, paylaşım), sağda OTOMATİK VERİLER
// (plan uyumu, soru, doğruluk, deneme trendi, devam, ödev, okuma, hata
// defteri, öz değerlendirme). Otomatik veriler değerlendirme yükünde gelir
// (`autoMetrics`); koç başka bir aralık seçerse yalnız o zaman
// `admin_getEvaluationMetrics` çağrılır (en çok 180 gün).
//
// Akademi kuralları (sözleşme): dönemde öğrenci başına TEK değerlendirme
// (409 `evaluation_exists` → mevcut olan açılır); kayıt iyimser kilitlidir
// (`version`; mobil ve KM aynı anda düzenleyebilir → 409 `version_conflict`);
// yayım için bütün kriterler puanlı ve genel yorum en az 40 karakter olmalı
// (ön denetim burada UX'tir, karar Akademi'de). Paylaşım seçilmezse
// değerlendirme kurum içinde kalır. Yayından kaldırmak okundu bilgisini silmez.

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../ui-kit/layout.js';
import { sparkline } from '../../ui-kit/charts.js';
import {
  actionButton, addDays, confirmAction, dateInput, fmtDate, fmtDateTime, formDialog, gradeText, kv, net, num,
  openDrawer, percent, problem, runAction, statusBadge, studentPicker, todayIst,
} from './common.js';
import {
  CHANGE_MARK, EVALUATION_STATUS, MISTAKE_REASONS, READ_KIND, averageText, checkLine, detailOf, exportReports,
  iconButton, shareText,
} from './evaluations-shared.js';

const MIN_COMMENT = 40;
const MAX_METRIC_DAYS = 180;

/** Tam değerlendirme → liste satırı (listeyi baştan çekmeden yerinde güncellemek için). */
export function summaryOf(evaluation) {
  return {
    id: evaluation.id, student: evaluation.student, period: evaluation.period, title: evaluation.title,
    status: evaluation.status, average: evaluation.average, evaluator: evaluation.evaluator,
    publishedAt: evaluation.publishedAt, updatedAt: evaluation.updatedAt || evaluation.createdAt,
    readCount: Array.isArray(evaluation.reads) ? evaluation.reads.length : 0,
  };
}

// ------------------------------------------------------------- yeni kayıt

/**
 * Öğrenci için değerlendirme taslağı açar ve formu gösterir.
 * Öğrenci + dönem biliniyorsa pencere açılmaz (dönem satırındaki
 * "Değerlendir"); bilinmiyorsa öğrenci / dönem / şablon sorulur.
 */
export async function startEvaluation(ctx, { studentId = '', periodId = '', student = null } = {}) {
  const { app } = ctx;
  if (!app.can('admin_createEvaluation')) {
    app.toast('Değerlendirme açma yetkiniz yok.', 'bad');
    return null;
  }
  if (studentId && periodId) {
    try {
      const created = await app.api.post('/v1/admin/evaluations', { studentId, periodId },
        { key: `km-ev-${studentId}-${periodId}` });
      return opened(created);
    } catch (failure) {
      return existingOr(failure);
    }
  }

  // Öğrenci adı: bağlantıdan yalnız kimlik geldiyse tek okuma (öğrenci detayı).
  let chosen = student;
  if (studentId && !chosen && app.can('admin_getStudent')) {
    chosen = await app.api.get(`/v1/admin/students/${studentId}`).then((data) => data?.student || null)
      .catch(() => null);
  }
  const [periodOptions, templates] = await Promise.all([
    ctx.periodOptions({ empty: 'Dönemsiz (serbest değerlendirme)', onlyOpen: true }),
    ctx.templates().catch(() => []),
  ]);
  const firstOpen = periodOptions.find((option) => option.value)?.value || '';
  const result = await formDialog(app, {
    title: 'Yeni değerlendirme',
    description: 'Taslak açılır; puanlar, yorum ve paylaşım formda doldurulur. Dönemde öğrenci başına tek '
      + 'değerlendirme olur — varsa mevcut olan açılır.',
    value: { studentId: chosen?.id || studentId || '', periodId: periodId || firstOpen,
      templateId: templates[0]?.id || '' },
    fields: [
      { key: 'studentId', label: 'Öğrenci', type: 'custom', required: true,
        render: (draft, set) => studentChoice(ctx, chosen, (picked) => { chosen = picked; set(picked?.id || ''); }) },
      { key: 'periodId', label: 'Dönem', type: 'select', options: periodOptions,
        hint: periodOptions.length > 1 ? 'Yalnız açık dönemler listelenir.'
          : 'Açık dönem yok; dönemsiz değerlendirme için şablon seçin.' },
      { key: 'templateId', label: 'Şablon (dönemsiz değerlendirmede)', type: 'select',
        options: [{ value: '', label: templates.length ? 'Şablon seçin' : 'Etkin şablon yok' },
          ...templates.map((item) => ({ value: item.id, label: `${item.name} · v${item.version}` }))],
        hint: 'Dönem seçiliyse dönemin şablonu kullanılır.',
        validate: (value, draft) => (!draft.periodId && !value ? 'Dönemsiz değerlendirme için şablon seçin.' : null) },
    ],
    submitLabel: 'Taslağı aç',
    onSubmit: async (draft, { key }) => {
      try {
        return { created: await app.api.post('/v1/admin/evaluations', {
          studentId: draft.studentId,
          periodId: draft.periodId || null,
          templateId: draft.periodId ? null : (draft.templateId || null),
        }, { key }) };
      } catch (failure) {
        const existing = failure?.code === 'evaluation_exists' ? detailOf(failure, 'evaluationId') : null;
        if (existing) return { existing };
        throw failure;
      }
    },
  });
  if (!result) return null;
  if (result.existing) {
    app.toast('Bu öğrencinin bu dönemde değerlendirmesi zaten var; o açılıyor.', 'warn');
    return openEvaluation(ctx, result.existing);
  }
  return opened(result.created);

  function opened(created) {
    app.toast('Değerlendirme taslağı açıldı.', 'good');
    ctx.emit({ type: 'evaluation', kind: 'created', evaluation: created });
    return openEvaluation(ctx, created.id, { initial: created });
  }

  function existingOr(failure) {
    const existing = failure?.code === 'evaluation_exists' ? detailOf(failure, 'evaluationId') : null;
    if (existing) {
      app.toast('Bu öğrencinin bu dönemde değerlendirmesi zaten var; o açılıyor.', 'warn');
      return openEvaluation(ctx, existing);
    }
    app.toast(problem(failure, 'Değerlendirme açılamadı.'), 'bad');
    return null;
  }
}

/** Pencerede öğrenci seçimi: seçili ad + "Değiştir" ya da arama kutusu. */
function studentChoice(ctx, initial, onPick) {
  const { app } = ctx;
  const node = h('div', 'ak-ev-student-choice');
  let picker = null;
  const paint = (student) => {
    picker?.destroy?.();
    picker = null;
    node.replaceChildren();
    if (student) {
      const line = h('div', 'ak-inline');
      line.append(h('b', '', student.displayName || '—'),
        h('span', 'ak-muted', [student.classGroup?.name, student.gradeLevel ? gradeText(student.gradeLevel) : '']
          .filter((part) => part && part !== '—').join(' · ')),
        button('Değiştir', { variant: 'ghost', onClick: () => { onPick(null); paint(null); } }));
      node.append(line);
      return;
    }
    // Arama `admin_listStudents` ister; yetki yoksa kutu çizilmez, neden yazılır.
    if (!app.can('admin_listStudents')) {
      node.append(h('span', 'ak-muted', 'Öğrenci aramak için öğrenci listesi yetkisi gerekiyor.'));
      return;
    }
    picker = studentPicker(app, { onPick: (picked) => { onPick(picked); paint(picked); } });
    node.append(picker.node);
    setTimeout(() => picker?.focus(), 0);
  };
  paint(initial);
  return node;
}

// ------------------------------------------------------------------ form

/**
 * Değerlendirme çekmecesi. `initial` verilirse (yeni açılan / yayımlanan
 * kayıt) ilk çizim ağa çıkmaz.
 */
export function openEvaluation(ctx, evaluationId, { initial = null } = {}) {
  const { app } = ctx;
  let evaluation = null;
  let local = null;
  let dirty = false;
  let busy = false;
  let closed = false;
  let metrics = null;
  let conflict = false;
  const canSave = app.can('admin_updateEvaluation');

  const panel = openDrawer(app, { title: 'Değerlendirme', onClose: () => onClosed() });
  panel.node.classList.add('ak-ev-drawer');
  const layout = h('div', 'ak-ev-layout');
  const main = h('div', 'ak-ev-main');
  const side = h('aside', 'ak-ev-side');
  layout.append(main, side);
  panel.body.append(layout);
  main.append(skeleton());

  async function load(given = null) {
    try {
      const data = given || await app.api.get(`/v1/admin/evaluations/${evaluationId}`);
      if (closed) return;
      apply(data);
    } catch (failure) {
      if (closed) return;
      main.replaceChildren(alertBox(problem(failure, 'Değerlendirme açılamadı.'), 'bad'),
        button('Tekrar dene', { variant: 'ghost', onClick: () => { main.replaceChildren(skeleton()); load(); } }));
    }
  }

  function apply(next) {
    evaluation = next;
    conflict = false;
    local = {
      scores: new Map(allCriteria().map((criterion) => [criterion.criterionId,
        { value: criterion.value ?? null, note: criterion.note || '' }])),
      comment: next.comment || '',
      strengths: [...(next.strengths || [])],
      improvements: [...(next.improvements || [])],
      share: { student: Boolean(next.sharedWith?.student), guardians: Boolean(next.sharedWith?.guardians) },
      notifyUpdate: true,
    };
    dirty = false;
    panel.setTitle(`${next.student?.displayName || 'Öğrenci'} — ${next.title || 'Değerlendirme'}`);
    paint();
    if (!metrics) {
      metrics = metricsPanel(app, next.student?.id, next.autoMetrics);
      side.replaceChildren(metrics.node);
    }
  }

  function allCriteria() {
    return (evaluation?.sections || []).flatMap((item) => item.criteria || []);
  }

  function markDirty() {
    dirty = true;
    paintState();
  }

  // ------------------------------------------------------------- çizim
  const averageLine = h('span', 'ak-ev-average');
  let actionsBar = null;
  let rowsById = new Map();
  let commentInput = null;

  function paint() {
    const scroll = panel.body.scrollTop;
    const nodes = [];
    const published = evaluation.status === 'published';

    const head = h('div', 'ak-ev-head');
    const badges = h('div', 'ak-inline');
    badges.append(statusBadge(EVALUATION_STATUS, evaluation.status),
      badge(shareText(evaluation.sharedWith), evaluation.sharedWith?.student || evaluation.sharedWith?.guardians
        ? 'info' : 'dim'));
    head.append(badges, kv([
      ['Öğrenci', [evaluation.student?.displayName, evaluation.student?.externalRef,
        evaluation.student?.classGroup?.name].filter(Boolean).join(' · ')],
      ['Dönem', evaluation.period?.name || '—'],
      ['Ölçek', (evaluation.scale?.labels || []).map((item) => `${item.value} ${item.label}`).join(' · ') || '—'],
      ['Değerlendiren', evaluation.evaluator?.displayName || '—'],
      ['Yayım', published ? `${fmtDateTime(evaluation.publishedAt)}${evaluation.lastNotifiedAt
        ? ` · son bildirim ${fmtDateTime(evaluation.lastNotifiedAt)}` : ''}` : 'Taslak — öğrenci ve veli görmez'],
      ['Son kayıt', `${fmtDateTime(evaluation.updatedAt || evaluation.createdAt)} · sürüm ${evaluation.version}`],
    ]));
    nodes.push(head);

    if (conflict) {
      const box = h('div', 'ak-ev-conflict');
      box.append(alertBox('Bu değerlendirme siz düzenlerken başka bir yerde (ör. mobil uygulama) kaydedildi. '
        + 'Son hâlini yükleyip değişikliklerinizi yeniden girin.', 'bad'),
      button('Son hâlini yükle', { variant: 'primary', onClick: () => {
        dirty = false; main.replaceChildren(skeleton()); load();
      } }));
      nodes.push(box);
    }
    if (published && canSave) {
      nodes.push(alertBox('Yayımlanmış değerlendirmeyi düzeltiyorsunuz. Kaydettiğinizde öğrenci ve veli '
        + 'yeni hâlini görür.', 'info'));
    }

    // --- kriterler
    rowsById = new Map();
    for (const sectionItem of evaluation.sections || []) {
      const box = h('div', 'ak-ev-crit-group');
      for (const criterion of sectionItem.criteria || []) {
        const row = criterionRow(criterion);
        rowsById.set(criterion.criterionId, row);
        box.append(row);
      }
      nodes.push(card(sectionItem.title, box, `${num((sectionItem.criteria || []).length)} kriter`));
    }
    const summary = h('div', 'ak-ev-summary');
    summary.append(averageLine);
    nodes.push(summary);

    // --- serbest metinler
    const textCard = h('div', 'ak-ev-texts');
    textCard.append(commentField());
    textCard.append(itemsField('Güçlü yönler', local.strengths, 'ör. Matematikte işlem hızı belirgin arttı'));
    textCard.append(itemsField('Gelişim alanları', local.improvements, 'ör. Paragraf sorularında süre yönetimi'));
    nodes.push(card('Yorum', textCard));

    // --- paylaşım
    const shareBox = h('div', 'ak-ev-share');
    shareBox.append(
      checkLine('Öğrenciyle paylaş', local.share.student, (value) => { local.share.student = value; markDirty(); },
        { disabled: !canSave, hint: 'Öğrenci uygulamasında “Değerlendirmeler”de görünür.' }).node,
      checkLine('Velilerle paylaş', local.share.guardians, (value) => { local.share.guardians = value; markDirty(); },
        { disabled: !canSave, hint: 'Veli uygulamasında görünür; veli buradan görüşme talep edebilir.' }).node,
    );
    if (published) {
      shareBox.append(checkLine('Kaydedince paylaşılanlara “güncellendi” bildirimi gönder', local.notifyUpdate,
        (value) => { local.notifyUpdate = value; }, { disabled: !canSave }).node);
    }
    nodes.push(card('Paylaşım', shareBox, published ? '' : 'Yayımlayınca geçerli olur'));

    // --- okundu bilgisi
    const reads = evaluation.reads || [];
    if (published || reads.length) {
      const list = h('div', 'ak-ev-reads');
      if (!reads.length) list.append(h('span', 'ak-muted', 'Henüz okunmadı.'));
      for (const read of [...reads].sort((a, b) => String(a.readAt).localeCompare(String(b.readAt)))) {
        const line = h('div', 'ak-ev-read');
        line.append(badge(READ_KIND[read.kind] || read.kind, read.kind === 'guardian' ? 'info' : ''),
          h('span', '', read.person?.displayName || '—'), h('span', 'ak-muted', fmtDateTime(read.readAt)));
        list.append(line);
      }
      nodes.push(card('Okundu bilgisi', list, reads.length ? `${num(reads.length)} okuma` : ''));
    }

    actionsBar = h('div', 'ak-ev-actions');
    nodes.push(actionsBar);
    main.replaceChildren(...nodes);
    paintState();
    panel.body.scrollTop = scroll;
  }

  function criterionRow(criterion) {
    const row = h('div', 'ak-ev-crit');
    const entry = local.scores.get(criterion.criterionId);
    const info = h('div', 'ak-ev-crit-info');
    info.append(h('b', '', criterion.title));
    if (criterion.description) info.append(h('small', 'ak-muted', criterion.description));
    if (criterion.previousValue !== null && criterion.previousValue !== undefined) {
      const previous = h('small', 'ak-ev-prev', `Önceki: ${labelFor(criterion.previousValue)}`);
      const mark = CHANGE_MARK[criterion.change];
      if (mark && evaluation.status === 'published') {
        const sign = h('span', `ak-ev-change ${mark[1]}`, mark[0]);
        sign.title = mark[2];
        previous.append(sign);
      }
      info.append(previous);
    }

    const scale = h('div', 'ak-ev-scale');
    scale.setAttribute('role', 'radiogroup');
    scale.setAttribute('aria-label', criterion.title);
    const buttons = [];
    for (const option of evaluation.scale?.labels || []) {
      const choice = h('button', 'ak-ev-score');
      choice.type = 'button';
      choice.disabled = !canSave;
      choice.setAttribute('role', 'radio');
      choice.append(h('b', '', String(option.value)), h('small', '', option.label));
      choice.title = `${option.value} — ${option.label}`;
      choice.addEventListener('click', () => {
        entry.value = entry.value === option.value ? null : option.value;
        row.classList.remove('missing');
        paintChoice();
        markDirty();
      });
      buttons.push([option.value, choice]);
      scale.append(choice);
    }
    function paintChoice() {
      for (const [value, choice] of buttons) {
        const on = entry.value === value;
        choice.classList.toggle('on', on);
        choice.setAttribute('aria-checked', on ? 'true' : 'false');
      }
    }
    paintChoice();

    const note = h('input', 'kit-input ak-ev-note');
    note.type = 'text';
    note.maxLength = 300;
    note.placeholder = 'Kriter notu (isteğe bağlı, en çok 300 karakter)';
    note.value = entry.note;
    note.readOnly = !canSave;
    note.addEventListener('input', () => { entry.note = note.value; markDirty(); });
    const noteToggle = button(entry.note ? 'Notu gizle' : '+ Not', { variant: 'ghost', onClick: () => {
      note.hidden = !note.hidden;
      noteToggle.textContent = note.hidden ? '+ Not' : 'Notu gizle';
      if (!note.hidden) note.focus();
    } });
    note.hidden = !entry.note;
    if (!canSave && !entry.note) noteToggle.hidden = true;
    noteToggle.classList.add('ak-ev-note-toggle');

    const control = h('div', 'ak-ev-crit-control');
    control.append(scale, noteToggle);
    row.append(info, control, note);
    return row;
  }

  function labelFor(value) {
    const item = (evaluation.scale?.labels || []).find((option) => option.value === value);
    return item ? `${value} · ${item.label}` : String(value);
  }

  function commentField() {
    const wrap = h('label', 'kit-field kit-field-wide');
    const label = h('span', 'kit-field-label', 'Genel yorum');
    const input = h('textarea', 'kit-textarea ak-ev-comment');
    input.maxLength = 3000;
    input.placeholder = 'Öğrencinin dönemine dair genel değerlendirme. Yayım için en az 40 karakter.';
    input.value = local.comment;
    input.readOnly = !canSave;
    const counter = h('span', 'kit-field-hint');
    const count = () => {
      const length = input.value.trim().length;
      counter.textContent = `${num(length)}/3.000 karakter${length < MIN_COMMENT
        ? ` · yayım için en az ${MIN_COMMENT}` : ''}`;
      counter.classList.toggle('ak-ev-short', length > 0 && length < MIN_COMMENT);
    };
    input.addEventListener('input', () => { local.comment = input.value; count(); markDirty(); });
    count();
    commentInput = input;
    wrap.append(label, input, counter);
    return wrap;
  }

  /** Madde listesi (güçlü yön / gelişim alanı): her biri 2–200 karakter. */
  function itemsField(title, items, placeholder) {
    const wrap = h('div', 'kit-field kit-field-wide ak-ev-items');
    wrap.append(h('span', 'kit-field-label', title));
    const list = h('div', 'ak-ev-item-list');
    const paintItems = () => {
      list.replaceChildren();
      items.forEach((text, index) => {
        const row = h('div', 'ak-ev-item');
        const input = h('input', 'kit-input');
        input.type = 'text';
        input.maxLength = 200;
        input.value = text;
        input.readOnly = !canSave;
        input.addEventListener('input', () => { items[index] = input.value; markDirty(); });
        row.append(h('span', 'ak-ev-bullet', '•'), input);
        if (canSave) {
          row.append(iconButton('✕', 'Maddeyi kaldır', () => { items.splice(index, 1); paintItems(); markDirty(); }));
        }
        list.append(row);
      });
      if (!items.length && !canSave) list.append(h('span', 'ak-muted', 'Yazılmamış.'));
    };
    paintItems();
    wrap.append(list);
    if (canSave) {
      const adder = h('div', 'ak-ev-item ak-ev-adder');
      const input = h('input', 'kit-input');
      input.type = 'text';
      input.maxLength = 200;
      input.placeholder = placeholder;
      const add = () => {
        const text = input.value.trim();
        if (text.length < 2) { input.focus(); return; }
        items.push(text);
        input.value = '';
        paintItems();
        markDirty();
        input.focus();
      };
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') { event.preventDefault(); add(); }
      });
      adder.append(h('span', 'ak-ev-bullet', '+'), input, button('Ekle', { variant: 'ghost', onClick: add }));
      wrap.append(adder);
    }
    return wrap;
  }

  /** Puanlanan kriter, canlı ortalama, kaydedilmemiş değişiklik ve düğmeler. */
  function paintState() {
    if (!evaluation) return;
    const criteria = allCriteria();
    const values = criteria.map((criterion) => local.scores.get(criterion.criterionId)?.value)
      .filter((value) => value !== null && value !== undefined);
    const mean = values.length ? values.reduce((total, value) => total + value, 0) / values.length : null;
    averageLine.replaceChildren(
      h('span', '', `Puanlanan ${num(values.length)}/${num(criteria.length)} kriter`),
      h('b', '', `Ortalama ${dirty ? averageText(mean) : averageText(evaluation.average ?? mean)}`),
      h('span', 'ak-muted', evaluation.previousAverage !== null && evaluation.previousAverage !== undefined
        ? `önceki ${averageText(evaluation.previousAverage)}` : 'önceki değerlendirme yok'),
    );
    if (dirty) averageLine.append(badge('Kaydedilmemiş değişiklik', 'warn'));
    if (!actionsBar) return;
    actionsBar.replaceChildren();
    const published = evaluation.status === 'published';
    const save = actionButton(app, 'admin_updateEvaluation', published ? 'Düzeltmeyi kaydet' : 'Taslağı kaydet',
      { variant: published ? 'primary' : '', onClick: () => saveNow() });
    if (!dirty || busy) save.disabled = true;
    actionsBar.append(save);
    if (!published) {
      const publishButton = actionButton(app, 'admin_publishEvaluation', 'Yayımla…', { variant: 'primary',
        onClick: () => publish() });
      if (busy || conflict) publishButton.disabled = true;
      actionsBar.append(publishButton);
    } else {
      actionsBar.append(actionButton(app, 'admin_unpublishEvaluation', 'Yayından kaldır', {
        onClick: (event) => unpublish(event.currentTarget) }));
      actionsBar.append(actionButton(app, 'admin_exportEvaluationReports', 'Gelişim karnesi (PDF)', {
        onClick: () => exportReports(ctx, { periodId: evaluation.period?.id || '', periodName: evaluation.period?.name,
          studentIds: [evaluation.student?.id], studentName: evaluation.student?.displayName }) }));
    }
    actionsBar.append(h('span', 'kit-spacer'));
    if (evaluation.student?.id) {
      actionsBar.append(button('Öğrenci kartı', { variant: 'ghost', onClick: () => {
        panel.close();
        app.go('students', { studentId: evaluation.student.id });
      } }));
    }
    if (!published) {
      actionsBar.append(actionButton(app, 'admin_deleteEvaluation', 'Taslağı sil', { variant: 'danger',
        onClick: (event) => remove(event.currentTarget) }));
    }
  }

  // ----------------------------------------------------------- yazma
  function itemsProblem() {
    for (const [title, items] of [['Güçlü yönler', local.strengths], ['Gelişim alanları', local.improvements]]) {
      if (items.some((text) => text.trim().length > 0 && text.trim().length < 2)) {
        return `${title}: her madde en az 2 karakter olmalı.`;
      }
    }
    return null;
  }

  function body() {
    const payload = {
      version: evaluation.version,
      scores: allCriteria().map((criterion) => {
        const entry = local.scores.get(criterion.criterionId) || {};
        return { criterionId: criterion.criterionId, value: entry.value ?? null,
          note: String(entry.note || '').trim() || null };
      }),
      comment: local.comment.trim() || null,
      strengths: local.strengths.map((text) => text.trim()).filter(Boolean),
      improvements: local.improvements.map((text) => text.trim()).filter(Boolean),
      share: { ...local.share },
    };
    if (evaluation.status === 'published') payload.notifyUpdate = Boolean(local.notifyUpdate);
    return payload;
  }

  /** Kaydeder; başarıda true. Çekmece kapandıktan sonra da çalışır (kapanışta kaydet). */
  async function saveNow({ quiet = false } = {}) {
    const issue = itemsProblem();
    if (issue) { app.toast(issue, 'bad'); return false; }
    busy = true;
    paintState();
    try {
      const next = await app.api.put(`/v1/admin/evaluations/${evaluation.id}`, body());
      if (!quiet) app.toast(next.status === 'published' ? 'Düzeltme kaydedildi.' : 'Taslak kaydedildi.', 'good');
      ctx.emit({ type: 'evaluation', kind: 'saved', evaluation: next });
      busy = false;
      if (closed) { dirty = false; return true; }
      apply(next);
      return true;
    } catch (failure) {
      busy = false;
      if (failure?.code === 'version_conflict') {
        conflict = true;
        if (!closed) paint();
        app.toast('Kayıt başka bir yerde değişti; değişiklikleriniz kaydedilmedi.', 'bad');
      } else {
        app.toast(problem(failure, 'Değerlendirme kaydedilemedi.'), 'bad');
        paintState();
      }
      return false;
    }
  }

  async function publish() {
    const missing = allCriteria().filter((criterion) => {
      const value = local.scores.get(criterion.criterionId)?.value;
      return value === null || value === undefined;
    });
    for (const criterion of missing) rowsById.get(criterion.criterionId)?.classList.add('missing');
    if (missing.length) {
      app.toast(`Yayım için bütün kriterler puanlanmalı: ${num(missing.length)} kriter puansız.`, 'bad');
      rowsById.get(missing[0].criterionId)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    if (local.comment.trim().length < MIN_COMMENT) {
      app.toast(`Genel yorum en az ${MIN_COMMENT} karakter olmalı.`, 'bad');
      commentInput?.focus();
      return;
    }
    if (dirty && !await saveNow({ quiet: true })) return;
    const result = await formDialog(app, {
      title: 'Değerlendirmeyi yayımla',
      description: 'Seçtiğiniz kişiler değerlendirmeyi uygulamada görür. Paylaşım seçilmezse değerlendirme '
        + 'yayımlanır ama kurum içinde kalır.',
      value: { student: local.share.student, guardians: local.share.guardians, notify: true },
      fields: [
        { key: 'student', label: 'Öğrenciyle paylaş', type: 'checkbox', wide: true },
        { key: 'guardians', label: 'Velilerle paylaş', type: 'checkbox', wide: true },
        { key: 'notify', label: 'Paylaşılanlara bildirim gönder', type: 'checkbox', wide: true },
      ],
      submitLabel: 'Yayımla',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/evaluations/${evaluation.id}/publish`, {
        share: { student: Boolean(draft.student), guardians: Boolean(draft.guardians) },
        notify: Boolean(draft.notify),
      }, { key }),
    });
    if (!result || closed) return;
    app.toast(result.sharedWith?.student || result.sharedWith?.guardians
      ? 'Değerlendirme yayımlandı ve paylaşıldı.' : 'Değerlendirme yayımlandı (kurum içinde).', 'good');
    ctx.emit({ type: 'evaluation', kind: 'published', evaluation: result });
    apply(result);
  }

  async function unpublish(control) {
    const ok = await confirmAction(app, { title: 'Yayından kaldırılsın mı?',
      description: 'Değerlendirme öğrenci ve veli görünümünden kalkar ve taslağa döner. Okundu bilgisi saklanır.',
      confirmLabel: 'Yayından kaldır', danger: true });
    if (!ok) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/evaluations/${evaluation.id}/unpublish`),
      { success: 'Değerlendirme taslağa döndü.', button: control });
    if (!next) return;
    ctx.emit({ type: 'evaluation', kind: 'unpublished', evaluation: next });
    if (!closed) apply(next);
  }

  async function remove(control) {
    const ok = await confirmAction(app, { title: 'Taslak silinsin mi?',
      description: `${evaluation.student?.displayName || 'Öğrenci'} için “${evaluation.title}” taslağı silinir. `
        + 'Yayımlanmış değerlendirme silinemez.', confirmLabel: 'Sil', danger: true });
    if (!ok) return;
    const done = await runAction(app, async () => {
      await app.api.del(`/v1/admin/evaluations/${evaluation.id}`);
      return true;
    }, { success: 'Taslak silindi.', button: control });
    if (!done) return;
    ctx.emit({ type: 'evaluation', kind: 'deleted', removedId: evaluation.id, evaluation });
    dirty = false;
    panel.close();
  }

  async function onClosed() {
    closed = true;
    metrics?.destroy();
    if (!dirty || !canSave || !evaluation || conflict) return;
    const ok = await confirmAction(app, { title: 'Kaydedilmemiş değişiklikler var',
      description: `${evaluation.student?.displayName || 'Öğrenci'} değerlendirmesindeki değişiklikler kaydedilsin mi?`,
      confirmLabel: 'Kaydet' });
    if (ok) await saveNow();
    else app.toast('Değişiklikler kaydedilmedi.', 'warn');
  }

  // Yukarıdaki bütün durum değişkenleri tanımlandıktan SONRA: `initial`
  // verildiğinde ilk çizim eşzamanlı koşar.
  load(initial);
  return panel;
}

function skeleton() {
  const node = h('div', 'ak-ev-skeleton');
  for (let index = 0; index < 5; index += 1) node.append(h('span', 'kit-skel'));
  return node;
}

// -------------------------------------------------------- otomatik veriler

/**
 * Yan panel: değerlendirmeyle gelen özet; aralık değişirse
 * `GET /v1/admin/students/{id}/evaluation-metrics` (en çok 180 gün).
 */
function metricsPanel(app, studentId, initial) {
  const node = h('div', 'ak-ev-metrics');
  const head = h('div', 'ak-ev-metrics-head');
  head.append(h('b', '', 'Otomatik veriler'),
    h('small', 'ak-muted', 'Akademi kayıtlarından; puanı siz verirsiniz.'));
  const content = h('div', 'ak-ev-metrics-body');
  let from = initial?.from || todayIst(-29);
  let to = initial?.to || todayIst();
  const fromField = dateInput(from, (iso) => { from = iso || ''; }, 'Başlangıç');
  const toField = dateInput(to, (iso) => { to = iso || ''; }, 'Bitiş');
  const error = h('small', 'ak-ev-range-error');
  const canRead = app.can('admin_getEvaluationMetrics');
  const show = button('Göster', { variant: 'ghost', onClick: () => reload(), disabled: !canRead || !studentId });
  if (!canRead) show.title = 'Otomatik verileri okuma yetkiniz yok.';
  const range = h('div', 'ak-ev-range');
  range.append(fromField.node, h('span', 'ak-muted', '–'), toField.node, show);
  node.append(head, range, error, content);
  let token = 0;

  async function reload() {
    error.textContent = '';
    if (!from || !to) { error.textContent = 'Başlangıç ve bitiş seçin.'; return; }
    if (to < from) { error.textContent = 'Bitiş başlangıçtan önce olamaz.'; return; }
    if (addDays(from, MAX_METRIC_DAYS - 1) < to) { error.textContent = `Aralık en çok ${MAX_METRIC_DAYS} gün olabilir.`; return; }
    const mine = ++token;
    content.replaceChildren(skeleton());
    try {
      const data = await app.api.get(`/v1/admin/students/${studentId}/evaluation-metrics`, { from, to });
      if (mine === token) paint(data);
    } catch (failure) {
      if (mine === token) content.replaceChildren(alertBox(problem(failure, 'Otomatik veriler alınamadı.'), 'bad'));
    }
  }

  function tile(label, value, sub = '', extra = null) {
    const box = h('div', 'ak-ev-metric');
    box.append(h('span', 'ak-ev-metric-label', label), h('b', 'ak-ev-metric-value', value));
    if (sub) box.append(h('small', 'ak-muted', sub));
    if (extra) box.append(extra);
    return box;
  }

  function signed(value, digits = 0) {
    if (value === null || value === undefined) return '';
    const text = num(Math.abs(value), digits);
    return value > 0 ? `+${text}` : value < 0 ? `−${text}` : '±0';
  }

  function paint(data) {
    if (!data) {
      content.replaceChildren(h('span', 'ak-muted', 'Bu aralık için özet yok.'));
      return;
    }
    const pct = (value) => (value === null || value === undefined ? '—' : percent(value, 0));
    const tiles = [];
    tiles.push(tile('Plan uyumu', pct(data.planAdherencePercent)));
    const questions = data.questions || {};
    const questionDelta = (questions.count ?? 0) - (questions.previousCount ?? 0);
    tiles.push(tile('Çözülen soru', num(questions.count ?? 0),
      `önceki dönem ${num(questions.previousCount ?? 0)} (${signed(questionDelta)})`));
    tiles.push(tile('Doğruluk', pct(data.accuracyPercent)));
    const trend = data.examTrend || {};
    const nets = (trend.nets || []).filter((item) => item.totalNet !== null && item.totalNet !== undefined)
      .sort((a, b) => String(a.date).localeCompare(String(b.date)));
    let chart = null;
    if (nets.length > 1) {
      chart = h('span', 'ak-ev-spark');
      chart.append(sparkline(nets.map((item) => Number(item.totalNet)), { width: 140, height: 26 }));
      chart.title = nets.map((item) => `${fmtDate(item.date)} ${item.name}: ${net(item.totalNet)}`).join('\n');
    }
    tiles.push(tile('Son deneme neti', net(trend.lastNet), trend.deltaFromPrevious !== null
      && trend.deltaFromPrevious !== undefined ? `öncekine göre ${signed(trend.deltaFromPrevious, 2)}`
      : `${num((trend.nets || []).length)} deneme`, chart));
    const attendance = data.attendance || {};
    tiles.push(tile('Devam', pct(attendance.attendanceRatePercent),
      `${num(attendance.present ?? 0)} geldi · ${num(attendance.absent ?? 0)} gelmedi · `
      + `${num(attendance.late ?? 0)} geç · ${num(attendance.excused ?? 0)} izinli`));
    const homework = data.homework || {};
    tiles.push(tile('Ödev tamamlama', pct(homework.completionRatePercent),
      `${num(homework.completed ?? 0)}/${num(homework.assigned ?? 0)} ödev`));
    const reading = data.reading || {};
    tiles.push(tile('Okuma', `${num(reading.pages ?? 0)} sayfa`, `${num(reading.books ?? 0)} kitap`));
    const mistakes = data.mistakes || {};
    tiles.push(tile('Hata defteri', `${num(mistakes.added ?? 0)} eklendi`,
      [`${num(mistakes.learned ?? 0)} öğrenildi`, mistakes.dominantReason
        ? `en sık: ${MISTAKE_REASONS[mistakes.dominantReason] || mistakes.dominantReason}` : '']
        .filter(Boolean).join(' · ')));
    const review = data.selfReview || {};
    tiles.push(tile('Öz değerlendirme', `${num(review.count ?? 0)} kayıt`,
      review.count ? `motivasyon ${averageText(review.averageMotivation)} · odak ${averageText(review.averageFocus)}`
        : 'Bu aralıkta yok'));
    const grid = h('div', 'ak-ev-metric-grid');
    grid.append(...tiles);
    content.replaceChildren(grid, h('small', 'ak-muted ak-ev-computed',
      `${fmtDate(data.from)} – ${fmtDate(data.to)} · özet ${fmtDateTime(data.computedAt)} itibarıyla`));
  }

  if (initial) paint(initial);
  else if (canRead && studentId) reload();
  else content.replaceChildren(h('span', 'ak-muted', 'Otomatik veri yok.'));

  return { node, reload, destroy() { token += 1; fromField.destroy(); toField.destroy(); } };
}
