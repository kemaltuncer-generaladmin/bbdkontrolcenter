// BBD Akademi — Koçluk → Check-in (F-K16; bayrak `self_review`).
//
// · Kurum soru seti (`admin_getCheckinQuestionSet` / `admin_putCheckinQuestionSet`):
//   pazar öz değerlendirmesine eklenen en çok 5 koç sorusu (ölçek, kısa yanıt,
//   tek seçim). Öğrenciye özel 0–3 ek soru öğrencinin koçluk kartındadır.
// · Haftanın check-in'leri (`admin_listCheckins`): dolduran / doldurmayan; satır
//   öğrencinin o haftaki öz değerlendirmesini açar (`students_getSelfReview`,
//   koç sorularının yanıtları dahil) ve koç yorumu yazdırır (`admin_commentCheckin`,
//   öğrenciye bildirim; öğrenci ekranında "Koçunun yorumu").

import { button, dialogA11y, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, hintBox, skeletonRows } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import {
  actionButton, errorBox, fmtDateTime, kv, loader, openDrawer, pagedList, runAction, selectBox, todayIst,
  toolbar, weekStart,
} from '../common.js';
import { CHECKIN_KINDS, addDaysIso, checkinQuestion, weekLabel } from './coaching-logic.js';
import { coachOptions, studentCell } from './coaching-shared.js';
import { templateTools, textareaForm } from '../shared/text-templates.js';

const SET = '/v1/admin/coaching/checkin-questions';

export function tab(app) {
  const node = h('div', 'ak-stack');
  if (app.can('admin_getCheckinQuestionSet')) node.append(questionSetCard(app));
  if (app.can('admin_listCheckins')) node.append(weekList(app));
  return { node, shown() {} };
}

// ------------------------------------------------------------- soru seti

function questionSetCard(app) {
  let current = null;
  const view = loader(() => app.api.getCached(SET), (data) => {
    current = data;
    const list = h('ol', 'ak-coach-qlist');
    for (const question of data?.questions || []) list.append(questionLine(question));
    const body = h('div', 'ak-stack');
    body.append(data?.questions?.length ? list : h('p', 'ak-muted', 'Kurum sorusu yok. Öğrenciler yalnız standart öz '
      + 'değerlendirmeyi doldurur.'));
    if (data?.updatedAt) body.append(h('small', 'ak-muted', `Son değişiklik ${fmtDateTime(data.updatedAt)}`));
    body.append(toolbar(actionButton(app, 'admin_putCheckinQuestionSet', 'Soruları düzenle', { variant: 'ghost',
      onClick: () => edit() })));
    return body;
  });
  async function edit() {
    const saved = await questionsDialog(app, { title: 'Kurum check-in soruları', max: 5,
      questions: current?.questions || [],
      description: 'Pazar öz değerlendirmesinin sonuna eklenir (en çok 5 soru). Yanıtları koç görür.',
      save: (questions) => app.api.put(SET, { questions, updatedAt: current?.updatedAt ?? null }) });
    if (saved) {
      app.api.invalidate(SET);
      app.toast('Soru seti kaydedildi.', 'good');
      view.reload();
    }
  }
  const box = card('Kurum soru seti', view.node, 'pazar öz değerlendirmesi');
  view.reload();
  return box;
}

function questionLine(question) {
  const line = h('li', 'ak-coach-q');
  const meta = [CHECKIN_KINDS[question.kind] || question.kind];
  if (question.kind === 'scale') meta.push(`1–${question.scaleMax || 5}`);
  if (question.kind === 'single_choice') meta.push((question.options || []).join(' / '));
  line.append(h('span', '', question.prompt), h('small', 'ak-muted', meta.join(' · ')));
  return line;
}

/**
 * Soru düzenleyici penceresi (kurum seti ve öğrenciye özel sorular ortak).
 * `save(questions)` → kayıt; dönüş kayıt sonucu ya da vazgeçildiyse null.
 */
export function questionsDialog(app, { title, description = '', max = 5, questions = [], save }) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog ak-dialog wide');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', title);
    box.append(h('h3', 'kit-dialog-title', title));
    if (description) box.append(h('p', 'kit-dialog-text', description));
    const rows = questions.map((question) => ({ id: question.id || null, kind: question.kind || 'scale',
      prompt: question.prompt || '', scaleMax: question.scaleMax || 5, options: (question.options || []).join('\n') }));
    const list = h('div', 'ak-stack');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    const add = button('Soru ekle', { variant: 'ghost', onClick: () => {
      rows.push({ id: null, kind: 'scale', prompt: '', scaleMax: 5, options: '' });
      paint();
    } });
    const paint = () => {
      list.replaceChildren();
      if (!rows.length) list.append(h('p', 'ak-muted', 'Soru yok.'));
      rows.forEach((row, index) => {
        const item = h('div', 'ak-coach-qedit');
        const prompt = h('input', 'kit-input');
        prompt.type = 'text';
        prompt.maxLength = 200;
        prompt.value = row.prompt;
        prompt.placeholder = 'Soru (ör. Bu hafta en çok hangi derste zorlandın?)';
        prompt.setAttribute('aria-label', `${index + 1}. soru`);
        prompt.addEventListener('input', () => { row.prompt = prompt.value; });
        const kind = selectBox(Object.entries(CHECKIN_KINDS).map(([value, label]) => ({ value, label })), row.kind,
          (value) => { row.kind = value; paint(); }, { label: `${index + 1}. soru türü` });
        const extra = h('div', 'ak-coach-qextra');
        if (row.kind === 'scale') {
          const scale = h('input', 'kit-input ak-coach-num');
          scale.type = 'text';
          scale.inputMode = 'numeric';
          scale.value = String(row.scaleMax || 5);
          scale.setAttribute('aria-label', 'Ölçeğin üst değeri (3–10)');
          scale.addEventListener('input', () => { row.scaleMax = Number(scale.value); });
          extra.append(h('span', 'ak-muted', '1 –'), scale);
        } else if (row.kind === 'single_choice') {
          const options = h('textarea', 'kit-textarea');
          options.value = row.options;
          options.placeholder = 'Her satıra bir seçenek (2–5)';
          options.setAttribute('aria-label', 'Seçenekler');
          options.addEventListener('input', () => { row.options = options.value; });
          extra.append(options);
        }
        const remove = button('Kaldır', { variant: 'ghost', onClick: () => { rows.splice(index, 1); paint(); } });
        item.append(h('b', '', `${index + 1}.`), prompt, kind.node, extra, remove);
        list.append(item);
      });
      add.disabled = rows.length >= max;
    };
    paint();
    let release = () => {};
    const close = (result) => { document.removeEventListener('keydown', onKey); overlay.remove(); release(); resolve(result); };
    const onKey = (event) => { if (event.key === 'Escape') close(null); };
    const go = button('Kaydet', { variant: 'primary', onClick: async () => {
      error.textContent = '';
      const built = [];
      for (const row of rows) {
        const result = checkinQuestion(row);
        if (result.error) { error.textContent = result.error; return; }
        built.push(result.question);
      }
      go.disabled = true;
      try {
        close(await save(built));
      } catch (failure) {
        go.disabled = false;
        error.textContent = problem(failure);
      }
    } });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(add, h('span', 'kit-spacer'), button('Vazgeç', { onClick: () => close(null) }), go);
    box.append(list, error, actions);
    overlay.append(box);
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(box);
    box.querySelector('input')?.focus?.();
  });
}

// --------------------------------------------------------- haftanın listesi

function weekList(app) {
  const node = h('div', 'ak-stack');
  let week = weekStart(todayIst(-1));
  let status = '';
  let coachId = '';
  const label = h('b', 'ak-coach-weeklabel', weekLabel(week));
  const prev = button('‹ Önceki hafta', { variant: 'ghost', onClick: () => move(-7) });
  const next = button('Sonraki hafta ›', { variant: 'ghost', onClick: () => move(7) });
  const chips = chipRow([{ key: 'submitted', label: 'Dolduranlar' }, { key: 'missing', label: 'Doldurmayanlar' }], null,
    (key) => { status = key || ''; list.reload(); });
  const coachSlot = h('div');
  coachOptions(app, { empty: 'Bütün koçlar' }).then((options) => {
    coachSlot.append(selectBox(options, '', (value) => { coachId = value; list.reload(); }, { label: 'Koç' }).node);
  });
  node.append(h('h3', 'ak-coach-h', 'Haftanın check-in\'leri'), toolbar(prev, label, next, chips.node, coachSlot));
  const move = (days) => {
    week = addDaysIso(week, days);
    label.textContent = weekLabel(week);
    list.reload();
  };
  const list = pagedList({
    app,
    path: '/v1/admin/coaching/checkins',
    query: () => ({ weekStart: week, status: status || undefined, coachId: coachId || undefined }),
    limit: 50,
    onRow: (row) => openCheckin(app, row, () => list.reload()),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (row) => studentCell(row.student) },
      { key: 'submitted', label: 'Durum', width: '160px', cell: (row) => (row.submitted ? badge('Doldurdu', 'good')
        : badge('Doldurmadı', 'warn')) },
      { key: 'submittedAt', label: 'Gönderim', width: '150px', cell: (row) => (row.submittedAt ? fmtDateTime(row.submittedAt) : '—') },
      { key: 'comment', label: 'Koç yorumu', width: '150px', cell: (row) => (row.coachCommentAt ? fmtDateTime(row.coachCommentAt)
        : (row.submitted ? badge('Yorum bekliyor', 'info') : '—')) },
    ],
    empty: { title: 'Bu hafta kayıt yok', text: 'Koçlu öğrencilerin check-in durumu burada görünür.' },
  });
  node.append(list.node);
  list.reload();
  return node;
}

async function openCheckin(app, row, onChange) {
  const studentId = row.student?.id;
  const view = openDrawer(app, { title: row.student?.displayName || 'Check-in', subtitle: `Hafta ${weekLabel(row.weekStart)}` });
  if (!row.submitted) {
    view.body.replaceChildren(alertBox('Öğrenci bu haftanın check-in\'ini doldurmadı.', 'info'),
      hintBox('Öğrenciye hatırlatma koçluk kuyruğundan ("Check-in doldurulmadı") ya da mesajla yapılır.'));
    return;
  }
  view.body.replaceChildren(skeletonRows(6, 2));
  try {
    const data = await app.api.get(`/v1/students/${studentId}/self-reviews/${row.weekStart}`);
    const review = data?.review;
    if (!review) { view.body.replaceChildren(alertBox('Öz değerlendirme bulunamadı.', 'info')); return; }
    const parts = [kv([
      ['Motivasyon', `${review.motivation} / 5`],
      ['Odak', `${review.focus} / 5`],
      ['İyi giden', review.wentWell || '—'],
      ['Zorlandığı dersler', (review.struggledSubjects || []).map((item) => item.name).join(', ') || '—'],
      ['Zorlandığı yer', review.struggledNote || '—'],
      ['Gelecek hafta hedefi', review.nextWeekGoal || '—'],
      ['Gönderim', fmtDateTime(review.submittedAt)],
    ])];
    if (review.coachAnswers?.length) {
      parts.push(h('h4', 'ak-coach-sub', 'Koç sorularına yanıtlar'));
      const questions = new Map((data.coachQuestions || []).map((question) => [question.id, question]));
      const answers = h('dl', 'ak-kv');
      for (const answer of review.coachAnswers) {
        const question = questions.get(answer.questionId);
        answers.append(h('dt', '', answer.prompt || question?.prompt || 'Soru'));
        const value = answer.text ?? (answer.scale !== null && answer.scale !== undefined
          ? `${answer.scale}${question?.scaleMax ? ` / ${question.scaleMax}` : ''}`
          : (answer.choiceIndex !== null && answer.choiceIndex !== undefined
            ? (question?.options?.[answer.choiceIndex] || `${answer.choiceIndex + 1}. seçenek`) : '—'));
        answers.append(h('dd', '', String(value)));
      }
      parts.push(answers);
    }
    parts.push(h('h4', 'ak-coach-sub', 'Koç yorumu'));
    if (review.coachComment) {
      parts.push(h('p', 'ak-coach-text', review.coachComment), h('small', 'ak-muted', fmtDateTime(review.coachCommentedAt)));
    }
    const input = h('textarea', 'kit-textarea');
    input.maxLength = 1000;
    input.placeholder = 'Öğrenciye yorum (öğrenci "Koçunun yorumu" olarak görür)';
    input.setAttribute('aria-label', 'Koç yorumu');
    const send = actionButton(app, 'admin_commentCheckin', review.coachComment ? 'Yorumu güncelle' : 'Yorumu gönder',
      { variant: 'primary', onClick: async (event) => {
        const text = input.value.trim();
        if (!text) { app.toast('Yorum yazın.', 'bad'); return; }
        const saved = await runAction(app, () => app.api.post(`/v1/admin/students/${studentId}/checkins/${row.weekStart}/comment`,
          { comment: text.slice(0, 1000) }), { success: 'Yorum gönderildi; öğrenciye bildirim gitti.', button: event.currentTarget });
        if (saved) { view.close(); onChange?.(); }
      } });
    // Akademi README §14 madde 115: hazır yorum (öğrencinin adı ve hafta doldurulur) / yazılanı şablon yap.
    const tools = templateTools(app, { kind: 'checkin_comment', form: textareaForm(input), maxLength: 1000,
      context: () => ({ studentId, date: row.weekStart }) });
    if (tools) parts.push(tools);
    parts.push(input, toolbar(send));
    view.body.replaceChildren(...parts);
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openCheckin(app, row, onChange)));
  }
}

