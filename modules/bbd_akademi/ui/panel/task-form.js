// BBD Akademi — görev formu (koç görevi + kurum plan ataması + şablon): tek bileşen.
//
// Kullanıcı (15.10.2026): "koç öğrenciye soru vs atarken sıkıntı çekiyor; bir sürü
// alan var, gerekli alanlar belli olmuyor, tipler kayıyor." Eski koç formu 19 alanı
// türden bağımsız alt alta diziyordu (ders/konu seçicisi notun ALTINDA kalıyordu),
// plan düzenleyicisi tür değişince blokları gizleyip açıyordu (düzen zıplıyordu).
//
// DÜZEN (yukarıdan aşağı, SABİT):
//   1. Hızlı seçim (yalnız koç, yeni görev): zayıf konu · tekrar zamanı · raftaki sıradaki bölüm —
//      tek tıkla tür + ders/konu ya da kaynak/bölüm dolar.
//   2. Tür kartları (büyük, radyo grubu; ok tuşlarıyla gezilir): Soru çözümü · Konu çalışma ·
//      Kaynak · Video · Tekrar · Deneme; altında "Diğer" çipleri.
//   3. Gerekli: Gün * · Süre * — her türde aynı yerde.
//   4. Türün alanları: SABİT YÜKSEKLİKLİ alan (`ak-tf-area`, en az 2 satır); tür değişince içerik
//      yumuşakça değişir, altındaki bölümler kımıldamaz. Alan örnekleri türler arasında
//      PAYLAŞILIR (ders/konu seçicisi beş türde aynı nesne) → girilen değerler korunur.
//   5. "Daha fazla seçenek" (katlı; doluysa sayısı yazar): başlık (boşsa otomatik başlık
//      ipucunda), saat aralığı, talimat, öncelik, kanıt, başarı ölçütü, ekler, not.
//
// DOĞRULAMA: `task-form-logic.js validate` — hata alanın ALTINDA (Türkçe), alan değişince
// yeniden; sunucunun `fields_required_for_type` / `validation_error` alanları da aynı kutulara.
//
// KAYNAK SEÇİMİ TEK NOKTADAN: `pickResourceField` (bkz. aşağı). Ortak kitap seçicisi
// (`book-picker.js`, iş `kitap-senkron`, Akademi README §14 madde 98) buraya bağlıdır:
// "Katalogdan kitap ekle" → sınıf/ders/yayınevi süzgeçli kapaklı seçici, öğrencinin kitapları önce.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge } from '../../ui-kit/layout.js';
import { dateField } from '../../ui-kit/datefield.js';
import { problem } from './api.js';
import { bookPicker } from './book-picker.js';
import { T as BOOKS } from './book-picker-logic.js';
import { DAY_LABEL, WEEKDAYS, clockInput, fileDrop, fmtDate, num, todayIst } from './common.js';
import { programLabel, sortPrograms } from './curriculum-picker.js';
import { PRIORITY, PROOF_PHOTO, clockText, pct } from './coaching-logic.js';
import { videoCard, videoDialog } from './coaching-video.js';
import {
  CLOCK, FIELD_ORDER, TYPE_HINTS, TYPE_LABELS, autoTitle, filledOptional, intOf, minutesForVideo, rulesFor,
  suggestQuestions, typesFor, validate,
} from './task-form-logic.js';

const MINUTE_CHIPS = [20, 30, 40, 60, 90];
const SHORT_DAY = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));
const SUBJECT_TYPES = new Set(['question_solving', 'topic_study', 'video', 'review', 'mistake_review']);
let seq = 0;

/** Türkçe aramaya uygun sadeleştirme ("Üslü" ~ "uslu"). */
export function fold(text) {
  return String(text || '').toLocaleLowerCase('tr-TR')
    .replace(/[ıİ]/g, 'i').replace(/ş/g, 's').replace(/ğ/g, 'g').replace(/ü/g, 'u').replace(/ö/g, 'o')
    .replace(/ç/g, 'c').replace(/â/g, 'a').replace(/î/g, 'i').replace(/û/g, 'u').trim();
}

function numberBox(value, label, { placeholder = '', max = 4 } = {}) {
  const input = h('input', 'kit-input ak-tf-num');
  input.type = 'text';
  input.inputMode = 'numeric';
  input.maxLength = max;
  input.value = value === null || value === undefined ? '' : String(value);
  if (placeholder) input.placeholder = placeholder;
  input.setAttribute('aria-label', label);
  return input;
}

// ------------------------------------------------------------------ ana bileşen

/**
 * Görev formu gövdesi. Dialog/satır içi sarmalayıcılar (`coachTaskDialog`, plan
 * düzenleyicisi) bunu kullanır.
 *
 * @param {object} app
 * @param {object} options
 *   mode: 'coach' | 'date' | 'offset' | 'patch'
 *   form: başlangıç taslağı (`emptyForm` biçimi)
 *   studentId: koç kipinde öğrenci (müfredat, raf, bölümler)
 *   week: koç kipinde `CoachWeek` (gün çipleri)
 *   builder: koç kipinde `TaskBuilderContext` (raf, zayıf konular, son kullanılanlar)
 *   duration: şablon süresi (gün no üst sınırı)
 *   isNew: yeni görev mi (hızlı seçim yalnız yenide)
 *   onSubmit(again): Enter → false, Shift+Enter → true
 * @returns {{node, value, check, showErrors, setForm, focusFirst, destroy}}
 */
export function taskEditor(app, { mode = 'coach', form: initial, studentId = '', week = null, builder = null,
  duration = null, isNew = true, originalDate = '', onSubmit } = {}) {
  loadStyles(import.meta.url, 'task-form.css');
  const coach = mode === 'coach';
  const patch = mode === 'patch';
  const ruleMode = patch ? 'date' : mode;
  const form = { ...initial };
  const disposers = [];
  const fields = {};
  const touched = new Set();
  let submitted = false;
  const id = `ak-tf-${(seq += 1)}`;

  const node = h('div', `ak-tf ak-tf-${mode}`);

  // --- alan kabı (etiket + denetim + ipucu + hata)
  function field(key, label, control, { hint = '', wide = false, cls = '' } = {}) {
    const wrap = h('div', `kit-field ak-tf-field${wide ? ' kit-field-wide' : ''}${cls ? ` ${cls}` : ''}`);
    wrap.dataset.field = key;
    const caption = h('span', 'kit-field-label ak-tf-label', label);
    const star = h('span', 'req', '*');
    star.setAttribute('aria-hidden', 'true');
    star.hidden = true;
    caption.append(star);
    const hintNode = h('span', 'kit-field-hint ak-tf-hint', hint);
    hintNode.hidden = !hint;
    const error = h('span', 'kit-field-error ak-tf-error');
    error.id = `${id}-${key}-err`;
    error.setAttribute('aria-live', 'polite');
    const target = control.matches?.('input, select, textarea') ? control
      : control.querySelector?.('input, select, textarea');
    if (target) target.setAttribute('aria-describedby', error.id);
    wrap.append(caption, control, hintNode, error);
    fields[key] = { wrap, star, error, hint: hintNode, control, target, label };
    return fields[key];
  }
  const need = (key, on) => {
    const slot = fields[key];
    if (!slot) return;
    slot.star.hidden = !on;
    slot.target?.setAttribute('aria-required', on ? 'true' : 'false');
  };
  const setHint = (key, text) => {
    const slot = fields[key];
    if (!slot) return;
    slot.hint.textContent = text || '';
    slot.hint.hidden = !text;
  };

  // ------------------------------------------------------------ 1. hızlı seçim
  const quick = h('div', 'ak-tf-quick');
  if (coach && isNew && builder) {
    const items = [];
    for (const entry of (builder.shelf || []).filter((item) => item.nextSection?.id).slice(0, 3)) {
      items.push({ label: `Sıradaki: ${entry.title} › ${entry.nextSection.title}`, tone: 'res', apply: () => {
        setType('resource_section');
        resource.pick({ resourceId: entry.resourceId, title: entry.title, subject: entry.subject },
          [entry.nextSection.id]);
      } });
    }
    for (const item of (builder.weakTopics || []).slice(0, 3)) {
      items.push({ label: `Zayıf: ${item.topic.name}${item.accuracyPercent !== null && item.accuracyPercent !== undefined
        ? ` (${pct(item.accuracyPercent)})` : ''}`, tone: 'weak', apply: () => {
        setType('question_solving');
        subject.pick({ subjectId: item.topic.subjectId, topicId: item.topic.id, topicName: item.topic.name });
      } });
    }
    for (const topic of (builder.dueReviews || []).slice(0, 3)) {
      items.push({ label: `Tekrar zamanı: ${topic.name}`, tone: 'review', apply: () => {
        setType('review');
        subject.pick({ subjectId: topic.subjectId, topicId: topic.id, topicName: topic.name });
      } });
    }
    if (items.length) {
      quick.append(h('span', 'ak-tf-quick-title', 'Hızlı seçim'));
      const row = h('div', 'ak-tf-quick-row');
      for (const item of items) {
        const chip = h('button', `kit-chip ak-tf-quick-chip ${item.tone}`, item.label);
        chip.type = 'button';
        chip.addEventListener('click', () => { item.apply(); changed(); });
        row.append(chip);
      }
      quick.append(row);
    }
  }
  if (quick.childElementCount) node.append(quick);

  // ------------------------------------------------------------ 2. tür kartları
  const { primary, other } = typesFor(ruleMode);
  if (!primary.includes(form.type) && !other.includes(form.type)) form.type = primary[0];
  const typeBox = h('div', 'ak-tf-types');
  const typeButtons = new Map();
  if (patch) {
    typeBox.append(h('div', 'ak-tf-static', `${TYPE_LABELS[form.type] || form.type} · ${form.title || ''}`),
      h('small', 'ak-muted', 'Tür ve içerik değişmez; gerekirse atamayı geri alıp yenisini açın.'));
  } else {
    const cards = h('div', 'ak-tf-cards');
    cards.setAttribute('role', 'radiogroup');
    cards.setAttribute('aria-label', 'Görev türü');
    const others = h('div', 'ak-tf-others');
    others.setAttribute('role', 'radiogroup');
    others.setAttribute('aria-label', 'Diğer görev türleri');
    const make = (key, big) => {
      const card = h('button', big ? 'ak-tf-card' : 'kit-chip ak-tf-other');
      card.type = 'button';
      card.setAttribute('role', 'radio');
      card.dataset.type = key;
      if (big) card.append(h('b', '', TYPE_LABELS[key]), h('small', '', TYPE_HINTS[key]));
      else card.textContent = TYPE_LABELS[key];
      card.addEventListener('click', () => { setType(key); changed(); });
      typeButtons.set(key, card);
      return card;
    };
    for (const key of primary) cards.append(make(key, true));
    others.append(h('span', 'ak-tf-others-title', 'Diğer:'));
    for (const key of other) others.append(make(key, false));
    const order = [...primary, ...other];
    const onArrows = (event) => {
      if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : -1;
      const next = order[(order.indexOf(form.type) + step + order.length) % order.length];
      setType(next);
      changed();
      typeButtons.get(next)?.focus();
    };
    cards.addEventListener('keydown', onArrows);
    others.addEventListener('keydown', onArrows);
    typeBox.append(cards, others);
  }
  node.append(typeBox);

  // ------------------------------------------------------------ 3. gerekli: gün + süre
  const basics = h('div', 'ak-tf-basics');
  let dateCtl = null;
  let dayControl;
  if (coach && week) {
    dayControl = h('div', 'ak-tf-days');
    dayControl.setAttribute('role', 'radiogroup');
    dayControl.setAttribute('aria-label', 'Gün');
    const paintDays = () => {
      dayControl.replaceChildren();
      for (const day of week.days || []) {
        const chip = h('button', `kit-chip ak-tf-day${day.holiday ? ' holiday' : ''}${day.overCapacity ? ' over' : ''}`);
        chip.type = 'button';
        chip.setAttribute('role', 'radio');
        chip.setAttribute('aria-checked', form.date === day.date ? 'true' : 'false');
        chip.classList.toggle('on', form.date === day.date);
        chip.append(h('b', '', SHORT_DAY[day.dayOfWeek] || ''), h('span', '', fmtDate(day.date).slice(0, 5)));
        chip.setAttribute('aria-label', `${DAY_LABEL[day.dayOfWeek] || ''} ${fmtDate(day.date)}${day.holiday ? ' (tatil)' : ''}`);
        chip.title = [day.holiday ? `Tatil: ${day.holiday}` : '', day.overCapacity ? 'Kapasite aşıldı' : '',
          day.plannedMinutes ? `Planlı ${day.plannedMinutes} dk` : ''].filter(Boolean).join(' · ');
        chip.addEventListener('click', () => { form.date = day.date; touched.add('date'); paintDays(); changed(); });
        dayControl.append(chip);
      }
    };
    paintDays();
  } else if (mode === 'offset') {
    dayControl = numberBox(form.dayOffset || 1, 'Gün no', { max: 2 });
    dayControl.addEventListener('input', () => { form.dayOffset = dayControl.value; touched.add('date'); changed(); });
  } else {
    dateCtl = dateField({ value: form.date || '', label: 'Gün', onChange: (iso) => { form.date = iso; touched.add('date'); changed(); } });
    disposers.push(() => dateCtl.destroy());
    dayControl = dateCtl.node;
  }
  field('date', mode === 'offset' ? 'Gün no' : 'Gün', dayControl, { wide: Boolean(coach && week),
    hint: mode === 'offset' ? (duration ? `1–${duration} (1 = başlangıç günü)` : '1 = başlangıç günü') : '' });
  need('date', true);

  const minuteRow = h('div', 'ak-tf-minutes');
  const minuteChips = h('div', 'ak-tf-minute-chips');
  minuteChips.setAttribute('role', 'group');
  minuteChips.setAttribute('aria-label', 'Hazır süreler');
  const minuteInput = numberBox(form.estimatedMinutes, 'Süre (dakika)', { max: 3 });
  const paintMinutes = () => {
    minuteChips.replaceChildren();
    for (const value of MINUTE_CHIPS) {
      const chip = h('button', `kit-chip${Number(form.estimatedMinutes) === value ? ' on' : ''}`, `${value} dk`);
      chip.type = 'button';
      chip.setAttribute('aria-pressed', Number(form.estimatedMinutes) === value ? 'true' : 'false');
      chip.addEventListener('click', () => {
        form.estimatedMinutes = value;
        form.minutesTouched = true;
        minuteInput.value = String(value);
        touched.add('estimatedMinutes');
        paintMinutes();
        changed();
      });
      minuteChips.append(chip);
    }
  };
  minuteInput.addEventListener('input', () => {
    form.estimatedMinutes = minuteInput.value;
    form.minutesTouched = true;
    touched.add('estimatedMinutes');
    paintMinutes();
    changed();
  });
  paintMinutes();
  minuteRow.append(minuteChips, minuteInput, h('span', 'ak-muted', 'dk'));
  field('estimatedMinutes', 'Süre', minuteRow, { wide: Boolean(coach && week) });
  need('estimatedMinutes', true);
  basics.append(fields.date.wrap, fields.estimatedMinutes.wrap);
  node.append(basics);

  // ------------------------------------------------------------ 4. türün alanları
  const area = h('div', 'ak-tf-area');
  area.setAttribute('aria-live', 'off');
  const left = h('div', 'ak-tf-col ak-tf-main');
  const right = h('div', 'ak-tf-col ak-tf-side');
  const pair = h('div', 'ak-tf-pair');
  area.append(left, right);
  if (!patch) node.append(area);

  // Başlık (serbest görevde zorunlu → alana çıkar; diğerlerinde "Daha fazla"da).
  const titleInput = h('input', 'kit-input');
  titleInput.type = 'text';
  titleInput.maxLength = 120;
  titleInput.value = form.title || '';
  titleInput.setAttribute('aria-label', 'Başlık');
  titleInput.addEventListener('input', () => { form.title = titleInput.value; touched.add('title'); changed(); });
  field('title', 'Başlık', titleInput, { wide: true });

  // Ders + konu (paylaşılan).
  const subject = subjectTopicField(app, { studentId: coach ? studentId : '', form, onChange: () => { touched.add('subject'); changed(); } });
  disposers.push(() => subject.destroy());
  field('subject', 'Ders ve konu', subject.node, { wide: true });

  // Hedef soru (+ öneri).
  const questionsInput = numberBox(form.targetQuestions, 'Hedef soru sayısı');
  const suggestion = h('button', 'kit-chip ak-tf-suggest');
  suggestion.type = 'button';
  suggestion.hidden = true;
  suggestion.addEventListener('click', () => {
    const value = suggestQuestions(form.estimatedMinutes, form.sectionQuestions);
    if (!value) return;
    form.targetQuestions = String(value);
    form.questionsTouched = true;
    questionsInput.value = form.targetQuestions;
    touched.add('questions');
    changed();
  });
  questionsInput.addEventListener('input', () => {
    form.targetQuestions = questionsInput.value;
    form.questionsTouched = true;
    touched.add('questions');
    changed();
  });
  const questionsRow = h('div', 'ak-inline');
  questionsRow.append(questionsInput, h('span', 'ak-muted', 'soru'), suggestion);
  field('questions', 'Hedef soru sayısı', questionsRow);

  // Kaynak + bölümler (TEK bağlanış noktası).
  const resource = pickResourceField(app, { mode: ruleMode, studentId: coach ? studentId : '', builder, form,
    onChange: () => { touched.add('resource'); syncFromSections(); changed(); } });
  disposers.push(() => resource.destroy());
  field('resource', 'Kaynak ve bölüm', resource.node, { wide: true });

  // Sayfa aralığı.
  const pageFrom = numberBox(form.pageFrom, 'İlk sayfa', { placeholder: 'ilk' });
  const pageTo = numberBox(form.pageTo, 'Son sayfa', { placeholder: 'son' });
  for (const [input, key] of [[pageFrom, 'pageFrom'], [pageTo, 'pageTo']]) {
    input.addEventListener('input', () => { form[key] = input.value; touched.add(key); changed(); });
  }
  const pages = h('div', 'ak-inline');
  pages.append(pageFrom, h('span', 'ak-muted', '–'), pageTo);
  field('pageFrom', 'Sayfa aralığı', pages);
  fields.pageTo = { ...fields.pageFrom, target: pageTo };   // hata aynı kutunun altına

  // Soru aralığı (koç, kaynak).
  const questionFrom = numberBox(form.questionFrom, 'İlk soru', { placeholder: 'ilk' });
  const questionTo = numberBox(form.questionTo, 'Son soru', { placeholder: 'son' });
  for (const [input, key] of [[questionFrom, 'questionFrom'], [questionTo, 'questionTo']]) {
    input.addEventListener('input', () => { form[key] = input.value; touched.add(key); changed(); });
  }
  const qRange = h('div', 'ak-inline');
  qRange.append(questionFrom, h('span', 'ak-muted', '–'), questionTo);
  field('questionFrom', 'Soru aralığı', qRange);
  fields.questionTo = { ...fields.questionFrom, target: questionTo };

  // Video.
  const video = coach ? videoField(app, { form, onChange: (added) => {
    touched.add('video');
    const first = form.videos[0]?.video;
    if (added && first?.durationSeconds && !form.minutesTouched) {
      const minutes = minutesForVideo(first.durationSeconds);
      if (minutes) { form.estimatedMinutes = minutes; minuteInput.value = String(minutes); paintMinutes(); }
    }
    changed();
  } }) : null;
  if (video) field('video', 'Video', video.node, { wide: true });

  // Deneme / ödev (ilk gerektiğinde yüklenir).
  const exam = remotePick(app, { label: 'Deneme', empty: 'Deneme seçin', missing: 'Kayıtlı deneme', value: form.examId,
    load: () => app.api.getCached('/v1/admin/exams', { from: todayIst(-7), limit: 100 }).then((data) => data?.items || []),
    describe: (item) => `${item.name} · ${fmtDate(item.date)}`,
    onChange: (value, item) => { form.examId = value; form.examName = item?.name || ''; touched.add('exam'); changed(); } });
  field('exam', 'Deneme', exam.node, { hint: 'Son bir hafta ve sonrasındaki denemeler.', wide: true });
  const homework = mode === 'date' ? remotePick(app, { label: 'Ödev', empty: 'Ödev seçin', missing: 'Kayıtlı ödev',
    value: form.homeworkId,
    load: () => app.api.getCached('/v1/admin/homework', { state: 'active', limit: 100 }).then((data) => data?.items || []),
    describe: (item) => [item.title, item.subject?.shortName || item.subject?.name, item.dueAt ? `son ${fmtDate(item.dueAt)}` : '']
      .filter(Boolean).join(' · '),
    onChange: (value, item) => { form.homeworkId = value; form.homeworkTitle = item?.title || ''; touched.add('homework'); changed(); } })
    : null;
  if (homework) field('homework', 'Ödev', homework.node, { hint: 'Süresi dolmamış ödevler.', wide: true });

  // Hata tekrarı, okuma.
  const mistakeInput = numberBox(form.mistakeCount, 'Hata sayısı', { max: 2 });
  mistakeInput.addEventListener('input', () => { form.mistakeCount = mistakeInput.value; touched.add('mistakeCount'); changed(); });
  field('mistakeCount', 'Tekrar edilecek hata sayısı', mistakeInput, { hint: 'Hata defterindeki sıradaki hatalar (1–50).' });
  const readInput = numberBox(form.targetPages, 'Hedef sayfa');
  readInput.addEventListener('input', () => { form.targetPages = readInput.value; touched.add('readPages'); changed(); });
  field('readPages', 'Hedef sayfa', readInput, { hint: '1–2.000' });

  function syncFromSections() {
    const info = resource.info();
    form.sectionQuestions = info.questionCount || null;
    const range = info.pageFrom ? `Bölümden: s. ${info.pageFrom}${info.pageTo && info.pageTo !== info.pageFrom ? `–${info.pageTo}` : ''}` : '';
    setHint('pageFrom', range ? `${range} (boş bırakılırsa sunucu bölümden doldurur)` : '');
    pageFrom.placeholder = info.pageFrom ? String(info.pageFrom) : 'ilk';
    pageTo.placeholder = info.pageTo ? String(info.pageTo) : 'son';
  }

  // Türe göre alanı doldur: SOL = seçici, SAĞ = sayılar. Alan nesneleri yeniden kullanılır.
  function paintArea() {
    if (patch) return;
    const { show, need: needs } = rulesFor(form.type, ruleMode);
    const has = (key) => show.includes(key);
    const leftKeys = [];
    const rightKeys = [];
    if (has('title')) leftKeys.push('title');
    if (has('resource')) leftKeys.push('resource');
    if (has('video') && video) leftKeys.push('video');
    if (has('exam')) leftKeys.push('exam');
    if (has('homework') && homework) leftKeys.push('homework');
    if (has('subject')) leftKeys.push('subject');
    if (has('questions')) rightKeys.push('questions');
    if (has('pages')) rightKeys.push('pageFrom');
    if (has('questionRange')) rightKeys.push('questionFrom');
    if (has('mistakeCount')) rightKeys.push('mistakeCount');
    if (has('readPages')) rightKeys.push('readPages');
    if (form.type === 'mistake_review' || form.type === 'video') {
      // ders/konu bu türlerde isteğe bağlı: seçicinin altına
      const index = leftKeys.indexOf('subject');
      if (index > 0) { leftKeys.splice(index, 1); leftKeys.push('subject'); }
    }
    left.replaceChildren(...leftKeys.map((key) => fields[key].wrap));
    // Sayfa ve soru aralığı yan yana (kaynak türünde alan yüksekliği diğer türlerle aynı kalsın).
    const pairKeys = rightKeys.filter((key) => key === 'pageFrom' || key === 'questionFrom');
    const sideNodes = rightKeys.filter((key) => !pairKeys.includes(key)).map((key) => fields[key].wrap);
    if (pairKeys.length) {
      pair.replaceChildren(...pairKeys.map((key) => fields[key].wrap));
      sideNodes.push(pair);
    }
    right.replaceChildren(...sideNodes);
    right.hidden = !rightKeys.length;
    area.classList.toggle('one', !rightKeys.length);
    area.dataset.type = form.type;
    // Zorunlu işaretleri.
    need('subject', needs.includes('subject') || needs.includes('topic'));
    fields.subject.wrap.querySelector('.ak-tf-label').firstChild.textContent = needs.includes('topic') ? 'Ders ve konu (konu zorunlu)'
      : (SUBJECT_TYPES.has(form.type) && !needs.includes('subject') ? 'Ders ve konu (isteğe bağlı)' : 'Ders ve konu');
    need('questions', needs.includes('questions'));
    need('resource', needs.includes('resource'));
    need('video', needs.includes('video'));
    need('exam', needs.includes('exam'));
    need('homework', needs.includes('homework'));
    need('mistakeCount', needs.includes('mistakeCount'));
    need('readPages', needs.includes('readPages'));
    need('title', needs.includes('title'));
    need('pageFrom', false);
    setHint('resource', needs.includes('sections') ? 'Bölüm seçin ya da sayfa aralığı yazın (biri zorunlu).' : '');
    if (has('exam')) exam.ensure();
    if (has('homework')) homework?.ensure();
    // Başlık: serbest görevde alanda, diğerlerinde "Daha fazla"da.
    if (form.type === 'free') {
      titleInput.placeholder = 'Ör. Okul ödevini bitir';
    } else {
      moreGrid.prepend(fields.title.wrap);
    }
    paintTypes();
  }

  function paintTypes() {
    for (const [key, card] of typeButtons) {
      const on = key === form.type;
      card.setAttribute('aria-checked', on ? 'true' : 'false');
      card.tabIndex = on ? 0 : -1;
      card.classList.toggle('on', on);
    }
  }

  function setType(next) {
    if (!next || next === form.type) return;
    form.type = next;
    touched.add('type');
    paintArea();
    area.classList.remove('swap');
    // Yumuşak geçiş (yalnız opaklık — düzen değişmez).
    void area.offsetWidth;
    area.classList.add('swap');
  }

  // ------------------------------------------------------------ 5. daha fazla seçenek
  const more = h('div', 'ak-tf-more');
  const moreToggle = h('button', 'ak-tf-more-toggle');
  moreToggle.type = 'button';
  const moreGrid = h('div', 'ak-tf-more-grid');
  moreGrid.id = `${id}-more`;
  moreToggle.setAttribute('aria-controls', moreGrid.id);
  let moreOpen = patch;
  const paintMore = () => {
    const count = filledOptional(form, ruleMode);
    moreToggle.textContent = `${moreOpen ? '▾' : '▸'} Daha fazla seçenek${count ? ` (${count} dolu)` : ''}`;
    moreToggle.setAttribute('aria-expanded', moreOpen ? 'true' : 'false');
    moreGrid.hidden = !moreOpen;
  };
  moreToggle.addEventListener('click', () => { moreOpen = !moreOpen; paintMore(); });

  // saat aralığı
  const start = clockInput(form.startTime || '', (value) => { form.startTime = value; touched.add('startTime'); changed(); }, 'Başlangıç saati');
  const end = clockInput(form.endTime || '', (value) => { form.endTime = value; touched.add('endTime'); changed(); }, 'Bitiş saati');
  for (const [ctl, key] of [[start, 'startTime'], [end, 'endTime']]) {
    ctl.node.addEventListener('input', () => { form[key] = ctl.node.value.trim(); if (!form[key] || CLOCK.test(form[key])) changed(); });
  }
  const clocks = h('div', 'ak-inline');
  clocks.append(start.node, h('span', 'ak-muted', '–'), end.node);
  field('startTime', 'Saat aralığı', clocks, { hint: 'İsteğe bağlı; ör. 17:00 – 17:40.' });
  fields.endTime = { ...fields.startTime, target: end.node };

  let instructions = null;
  if (coach) {
    instructions = h('textarea', 'kit-textarea');
    instructions.maxLength = 2000;
    instructions.rows = 2;
    instructions.value = form.instructions || '';
    instructions.setAttribute('aria-label', 'Talimat (öğrenci görür)');
    instructions.addEventListener('input', () => { form.instructions = instructions.value; paintMore(); });
    field('instructions', 'Talimat (öğrenci görür)', instructions, { wide: true });

    const segmented = (key, options, label) => {
      const row = h('div', 'ak-chips');
      row.setAttribute('role', 'radiogroup');
      row.setAttribute('aria-label', label);
      const paint = () => {
        row.replaceChildren();
        for (const [value, text] of Object.entries(options)) {
          const chip = h('button', `kit-chip${form[key] === value ? ' on' : ''}`, text);
          chip.type = 'button';
          chip.setAttribute('role', 'radio');
          chip.setAttribute('aria-checked', form[key] === value ? 'true' : 'false');
          chip.addEventListener('click', () => { form[key] = value; paint(); paintMore(); });
          row.append(chip);
        }
      };
      paint();
      return row;
    };
    field('priority', 'Öncelik', segmented('priority', PRIORITY, 'Öncelik'));
    field('proofPhoto', 'Fotoğraf kanıtı', segmented('proofPhoto', PROOF_PHOTO, 'Fotoğraf kanıtı'));
    const answers = h('label', 'ak-inline ak-tf-check');
    const answersBox = h('input', 'kit-check');
    answersBox.type = 'checkbox';
    answersBox.checked = form.answersRequired !== false;
    answersBox.addEventListener('change', () => { form.answersRequired = answersBox.checked; paintMore(); });
    answers.append(answersBox, h('span', '', 'Doğru / yanlış / boş zorunlu'));
    field('answersRequired', 'Teslim', answers);
    const accuracy = numberBox(form.minAccuracyPercent, 'Başarı ölçütü (doğruluk %)', { max: 3, placeholder: 'ör. 70' });
    accuracy.addEventListener('input', () => { form.minAccuracyPercent = accuracy.value; touched.add('minAccuracyPercent'); changed(); });
    field('minAccuracyPercent', 'Başarı ölçütü: doğruluk %', accuracy, { hint: 'Altında kalırsa tekrar önerilir.' });
    field('attachments', 'Ekler (foto / PDF, en çok 3)', filesField(app, form, () => paintMore()), { wide: true });
  }
  const note = h('textarea', 'kit-textarea');
  note.maxLength = 500;
  note.rows = 2;
  note.value = form.note || '';
  note.setAttribute('aria-label', 'Not');
  note.addEventListener('input', () => { form.note = note.value; paintMore(); });
  field('note', 'Not', note, { wide: true });

  if (patch) {
    // Düzenlemede yalnız bunlar değişir: gün, saat, süre, başlık, hedef soru, not.
    moreGrid.append(fields.title.wrap, fields.questions.wrap);
    suggestion.remove();
  }
  moreGrid.append(...['startTime', 'instructions', 'priority', 'proofPhoto', 'answersRequired', 'minAccuracyPercent',
    'attachments', 'note'].filter((key) => fields[key]).map((key) => fields[key].wrap));
  more.append(moreToggle, moreGrid);
  node.append(more);

  // ------------------------------------------------------------ canlı güncelleme
  function paintAuto() {
    // Otomatik başlık ipucu.
    const auto = autoTitle(form);
    if (form.type !== 'free') {
      titleInput.placeholder = auto ? `Boşsa: ${auto}` : 'Boş bırakılırsa seçimden oluşturulur';
    }
    // Hedef soru önerisi: dokunulmadıysa öneri yazılır (tek tıkla kaydedilebilsin).
    const suggested = suggestQuestions(form.estimatedMinutes, form.sectionQuestions);
    const wantsQuestions = rulesFor(form.type, ruleMode).show.includes('questions');
    // Soru çözümünde süreden, kaynakta yalnız seçili bölümlerin soru sayısından.
    const autoFill = form.type === 'question_solving' || (form.type === 'resource_section' && form.sectionQuestions);
    if (!patch && wantsQuestions && !form.questionsTouched && suggested && autoFill) {
      form.targetQuestions = String(suggested);
      questionsInput.value = form.targetQuestions;
      questionsInput.classList.add('auto');
    } else if (!patch && !form.questionsTouched && wantsQuestions && !autoFill && form.targetQuestions) {
      form.targetQuestions = '';                       // başka türden kalan otomatik öneri taşınmaz
      questionsInput.value = '';
    } else if (form.questionsTouched) {
      questionsInput.classList.remove('auto');
    }
    suggestion.hidden = !suggested || String(suggested) === String(form.targetQuestions).trim() || patch;
    suggestion.textContent = suggested ? `Öneri: ${suggested}` : '';
    suggestion.title = form.sectionQuestions ? 'Seçili bölümlerin soru sayısı' : 'Süreye göre (≈1,5 dk/soru)';
    setHint('questions', form.questionsTouched || !suggested || !autoFill ? ''
      : form.sectionQuestions && form.type === 'resource_section' ? 'Seçili bölümlerin soru sayısı; değiştirebilirsiniz.'
        : 'Süreye göre önerildi (≈1,5 dk/soru); değiştirebilirsiniz.');
    paintMore();
  }

  function showErrors(map, { all = false } = {}) {
    for (const [key, slot] of Object.entries(fields)) {
      if (!slot?.error) continue;
      const shown = all || submitted || touched.has(key) || slot.error.textContent;
      const text = shown ? map[key] || '' : '';
      if (slot.error.textContent !== text) slot.error.textContent = text;
      slot.wrap.classList.toggle('bad', Boolean(map[key] && shown));
      slot.target?.setAttribute('aria-invalid', map[key] && shown ? 'true' : 'false');
    }
    // aynı kutuyu paylaşan ikinci denetimin (son sayfa, son soru, bitiş saati) hatası
    for (const [twin, main] of [['pageTo', 'pageFrom'], ['questionTo', 'questionFrom'], ['endTime', 'startTime']]) {
      if (map[twin] && (all || submitted || touched.has(twin)) && !map[main]) {
        fields[main].error.textContent = map[twin];
        fields[main].wrap.classList.add('bad');
      }
    }
    if ((map.startTime || map.endTime || map.minAccuracyPercent) && (all || submitted)) { moreOpen = true; paintMore(); }
  }

  function check() {
    const result = validate(form, { mode, today: todayIst(0), duration, originalDate });
    submitted = true;
    showErrors(result.errors, { all: true });
    return { ok: !result.first, errors: result.errors, first: result.first };
  }

  function changed() {
    paintAuto();
    if (submitted || touched.size) {
      const result = validate(form, { mode, today: todayIst(0), duration, originalDate });
      showErrors(result.errors);
    }
  }

  function focusField(key) {
    const slot = fields[key];
    const target = slot?.wrap.querySelector('.on[role="radio"], input:not([type="hidden"]), select, textarea, button');
    target?.focus?.();
  }

  // Klavye: Enter = kaydet, Shift+Enter = kaydet ve yeni ekle, Ctrl/Cmd+Enter her yerde kaydet.
  node.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.defaultPrevented) return;
    const tag = event.target?.localName;
    const inTextarea = tag === 'textarea';
    if (inTextarea && !(event.ctrlKey || event.metaKey)) return;
    if (tag === 'button' && !(event.ctrlKey || event.metaKey)) return;
    if (event.target?.closest?.('.ak-tf-search')) return;   // arama kendi Enter'ını işler
    event.preventDefault();
    onSubmit?.(Boolean(event.shiftKey));
  });

  paintTypes();
  paintArea();
  paintAuto();
  paintMore();
  syncFromSections();

  return {
    node,
    value: () => ({ ...form }),
    check,
    /** Sunucu hataları (alan → metin); `_form` dışarıda gösterilir. */
    showErrors(map) {
      submitted = true;
      showErrors(map, { all: true });
      const first = FIELD_ORDER.find((key) => map[key]);
      if (first) focusField(first === 'pageTo' ? 'pageFrom' : first);
    },
    focusFirst(key = null) {
      if (key) { focusField(key === 'pageTo' ? 'pageFrom' : key === 'questionTo' ? 'questionFrom' : key === 'endTime' ? 'startTime' : key); return; }
      const { need: needs } = rulesFor(form.type, ruleMode);
      const firstEmpty = needs.find((item) => {
        if (item === 'questions') return !String(form.targetQuestions || '').trim();
        if (item === 'subject' || item === 'topic') return !form.subjectId;
        return false;
      });
      if (firstEmpty === 'subject' || firstEmpty === 'topic') { subject.focus(); return; }
      if (firstEmpty === 'questions') { questionsInput.focus(); return; }
      (typeButtons.get(form.type) || fields.date.target || minuteInput).focus?.();
    },
    destroy() { disposers.forEach((fn) => fn()); },
  };
}

// ------------------------------------------------------------------ ders + konu

/**
 * Ders seçimi + aranabilir konu. Koç kipinde (öğrenci belli) öğrencinin müfredatı
 * (`students_getCurriculum`) tek istekle gelir: önce ÖĞRENCİNİN dersleri, konu araması
 * bütün programlarında ("Türev" yazınca "11. sınıf Matematik › Türev › Türev kuralları").
 * Öğrenci yoksa (kurum ataması): ders seçilince dersin programları okunur, arama onlarda.
 * Ünite ya da programın tamamı da seçilebilir (sözleşme: `topicId` program/ünite/konu).
 */
export function subjectTopicField(app, { studentId = '', form, onChange }) {
  const node = h('div', 'ak-tf-subject');
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', 'Ders');
  const search = h('div', 'ak-tf-search');
  const input = h('input', 'kit-input');
  input.type = 'search';
  input.placeholder = 'Konu ara (ör. türev, üslü sayılar)';
  input.setAttribute('aria-label', 'Konu ara');
  input.setAttribute('autocomplete', 'off');
  const list = h('div', 'ak-tf-results');
  list.setAttribute('role', 'listbox');
  list.setAttribute('aria-label', 'Konu sonuçları');
  list.hidden = true;
  list.id = `ak-tf-topics-${(seq += 1)}`;
  input.setAttribute('aria-controls', list.id);
  search.append(input, list);
  const chosen = h('div', 'ak-tf-chosen');
  const row = h('div', 'ak-tf-subject-row');
  row.append(select, search);
  node.append(row, chosen);

  const state = { subjects: [], studentSubjects: [], index: [], indexFor: null, loading: null, active: -1, results: [] };

  const option = (parent, value, label) => {
    const item = h('option', '', label);
    item.value = value;
    parent.append(item);
  };

  function paintSelect() {
    select.replaceChildren();
    option(select, '', state.subjects.length ? 'Ders seçin' : 'Dersler yükleniyor…');
    const known = new Set();
    if (state.studentSubjects.length) {
      const group = h('optgroup');
      group.label = 'Öğrencinin dersleri';
      for (const item of state.studentSubjects) { option(group, item.id, item.name); known.add(item.id); }
      select.append(group);
    }
    const rest = state.subjects.filter((item) => !known.has(item.id));
    if (rest.length) {
      const group = h('optgroup');
      group.label = state.studentSubjects.length ? 'Diğer dersler' : 'Dersler';
      for (const item of rest) { option(group, item.id, item.name); known.add(item.id); }
      select.append(group);
    }
    if (form.subjectId && !known.has(form.subjectId)) option(select, form.subjectId, form.subjectName || form.subjectId);
    select.value = form.subjectId || '';
  }

  function paintChosen() {
    chosen.replaceChildren();
    if (form.topicId) {
      const chip = h('button', 'kit-chip on ak-tf-topic-chip', `Konu: ${form.topicName || form.topicId} ✕`);
      chip.type = 'button';
      chip.title = 'Konuyu kaldır (dersin tamamı)';
      chip.addEventListener('click', () => { form.topicId = ''; form.topicName = ''; paintChosen(); onChange?.(); });
      chosen.append(chip);
    } else {
      chosen.append(h('small', 'ak-muted', form.subjectId ? 'Konu seçilmedi — dersin tamamı.' : 'Önce ders seçin ya da konuyu arayın.'));
    }
  }

  // Arama dizini: [{id, name, path, subjectId, subjectName, kind}]
  function addProgram(program, units, subjectRef) {
    const subjectId = subjectRef?.id || program.subject?.id || '';
    const subjectName = subjectRef?.name || program.subject?.name || '';
    const label = programLabel(program) || program.title || '';
    state.index.push({ id: program.id, name: `${label} (programın tamamı)`, path: '', subjectId, subjectName, kind: 'program' });
    for (const unit of [...(units || [])].filter((item) => !item.retired).sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
      state.index.push({ id: unit.id, name: `${unit.title} (ünitenin tamamı)`, path: label, subjectId, subjectName, kind: 'unit' });
      for (const topic of [...(unit.topics || [])].filter((item) => !item.retired).sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
        state.index.push({ id: topic.topicId || topic.id, name: topic.title, path: `${label} › ${unit.title}`, subjectId, subjectName,
          kind: 'topic', accuracy: topic.accuracyPercent ?? null });
      }
    }
  }

  async function ensureIndex() {
    const key = studentId ? `s:${studentId}` : `p:${form.subjectId}`;
    if (state.indexFor === key) return;
    if (state.loading?.key === key) { await state.loading.promise; return; }
    const run = async () => {
      state.index = [];
      if (studentId) {
        const data = await app.api.getCached(`/v1/students/${studentId}/curriculum`, { includeTopics: true },
          { ttlMs: 5 * 60_000 });
        for (const item of data?.programs || []) addProgram(item.program, item.units, item.program?.subject);
      } else if (form.subjectId) {
        const data = await app.api.getCached('/v1/admin/curricula/programs', { subject: form.subjectId }, { ttlMs: 10 * 60_000 });
        const programs = sortPrograms(data?.items || []).slice(0, 12);
        const details = await Promise.all(programs.map((program) => app.api.getCached(`/v1/admin/curricula/programs/${program.id}`,
          undefined, { ttlMs: 10 * 60_000 }).catch(() => null)));
        programs.forEach((program, i) => addProgram(program, details[i]?.units || [], program.subject));
      }
      state.indexFor = key;
    };
    const promise = run();
    state.loading = { key, promise };
    try { await promise; } finally { if (state.loading?.key === key) state.loading = null; }
  }

  function paintResults(text) {
    const needle = fold(text);
    list.replaceChildren();
    state.active = -1;
    if (!needle) { list.hidden = true; return; }
    const pool = state.index.filter((item) => !form.subjectId || !studentId || item.subjectId === form.subjectId
      || !state.studentSubjects.some((subjectItem) => subjectItem.id === form.subjectId));
    const words = needle.split(/\s+/).filter(Boolean);
    state.results = pool.filter((item) => {
      const hay = fold(`${item.name} ${item.path} ${item.subjectName}`);
      return words.every((word) => hay.includes(word));
    }).sort((a, b) => (a.kind === 'topic' ? 0 : 1) - (b.kind === 'topic' ? 0 : 1)).slice(0, 8);
    if (!state.results.length) {
      list.append(h('div', 'ak-picker-empty', studentId || form.subjectId ? 'Eşleşen konu yok.' : 'Önce ders seçin.'));
    }
    state.results.forEach((item, index) => {
      const rowNode = h('button', 'ak-picker-row ak-tf-result');
      rowNode.type = 'button';
      rowNode.setAttribute('role', 'option');
      rowNode.dataset.index = String(index);
      rowNode.append(h('b', '', item.name), h('span', '', [item.subjectName, item.path].filter(Boolean).join(' · ')
        + (item.accuracy !== null && item.accuracy !== undefined ? ` · ${pct(item.accuracy)}` : '')));
      rowNode.addEventListener('mousedown', (event) => event.preventDefault());
      rowNode.addEventListener('click', () => choose(item));
      list.append(rowNode);
    });
    list.hidden = false;
  }

  function choose(item) {
    form.subjectId = item.subjectId || form.subjectId;
    form.subjectName = item.subjectName || form.subjectName;
    form.topicId = item.id;
    form.topicName = item.name.replace(/ \((programın|ünitenin) tamamı\)$/, '');
    input.value = '';
    list.hidden = true;
    paintSelect();
    paintChosen();
    onChange?.();
  }

  let timer = null;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    const text = input.value;
    timer = setTimeout(async () => {
      try {
        await ensureIndex();
      } catch (failure) {
        list.replaceChildren(h('div', 'ak-picker-empty', problem(failure, 'Konular alınamadı.')));
        list.hidden = false;
        return;
      }
      paintResults(text);
    }, 120);
  });
  input.addEventListener('focus', () => { ensureIndex().catch(() => {}); });
  input.addEventListener('blur', () => { setTimeout(() => { list.hidden = true; }, 150); });
  input.addEventListener('keydown', (event) => {
    const rows = list.querySelectorAll('.ak-tf-result');
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!rows.length) return;
      event.preventDefault();
      state.active = (state.active + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
      rows.forEach((rowNode, index) => rowNode.classList.toggle('active', index === state.active));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      event.stopPropagation();
      const item = state.results[state.active >= 0 ? state.active : 0];
      if (item && !list.hidden) choose(item);
    } else if (event.key === 'Escape' && !list.hidden) {
      event.preventDefault();
      event.stopPropagation();
      list.hidden = true;
    }
  });
  select.addEventListener('change', () => {
    const changedSubject = select.value !== form.subjectId;
    form.subjectId = select.value;
    form.subjectName = select.value ? select.selectedOptions?.[0]?.textContent || '' : '';
    if (changedSubject) { form.topicId = ''; form.topicName = ''; }
    paintChosen();
    onChange?.();
  });

  (async () => {
    state.subjects = await app.lookups.subjects().catch(() => []);
    if (studentId && app.can?.('students_getCurriculum') !== false) {
      try {
        await ensureIndex();
        const seen = new Map();
        for (const item of state.index) if (item.subjectId && !seen.has(item.subjectId)) seen.set(item.subjectId, item.subjectName);
        state.studentSubjects = [...seen].map(([idValue, name]) => ({ id: idValue, name }));
      } catch {
        state.studentSubjects = [];
      }
    }
    if (form.subjectId && !form.subjectName) form.subjectName = state.subjects.find((item) => item.id === form.subjectId)?.name || '';
    paintSelect();
  })();

  paintSelect();
  paintChosen();
  return {
    node,
    /** Hızlı seçimden: ders + konu. */
    pick({ subjectId, topicId, topicName }) {
      form.subjectId = subjectId || form.subjectId;
      form.subjectName = state.subjects.find((item) => item.id === form.subjectId)?.name
        || state.studentSubjects.find((item) => item.id === form.subjectId)?.name || form.subjectName;
      form.topicId = topicId || '';
      form.topicName = topicName || '';
      paintSelect();
      paintChosen();
    },
    focus: () => (form.subjectId ? input : select).focus(),
    destroy: () => clearTimeout(timer),
  };
}

// ------------------------------------------------------------------ kaynak

/**
 * KAYNAK SEÇİMİNİN TEK BAĞLANIŞ NOKTASI. Ortak kitap seçicisi (iş `kitap-senkron`:
 * arama + sınıf/ders/yayınevi/seri süzgeci, kapaklı ızgara, "bu öğrencinin kitapları"
 * önce) hazır olduğunda yalnız `searchCatalog` ve `resourceRow` buradan ona bağlanır.
 *
 * Koç kipinde: öğrencinin rafı (ilerleme %) + son kullanılanlar (`recentCombos`) çip olarak,
 * "Başka kaynak ara" katalogda arar; bölümler öğrencinin ilerlemesiyle
 * (`admin_getStudentResourceSections`), rafta değilse "rafa da ekle".
 * Kurum kipinde: katalog araması + içindekiler (`catalog_getLibraryResource`).
 * `info()` → seçili bölümlerin sayfa aralığı ve soru sayısı (öneri için).
 */
export function pickResourceField(app, { mode = 'coach', studentId = '', builder = null, form, onChange }) {
  const coach = mode === 'coach' && studentId;
  const node = h('div', 'ak-tf-resource');
  const current = h('div', 'ak-tf-res-current');
  const shelfRow = h('div', 'ak-tf-res-shelf');
  const searchWrap = h('div', 'ak-tf-search ak-picker');
  const input = h('input', 'kit-input');
  input.type = 'search';
  input.placeholder = 'Kaynak ara (ad, yayınevi, ISBN)';
  input.setAttribute('aria-label', 'Kaynak ara');
  input.setAttribute('autocomplete', 'off');
  const results = h('div', 'ak-picker-list ak-tf-results');
  results.hidden = true;
  searchWrap.append(input, results);
  const sections = h('div', 'ak-tf-sections');
  const shelfCheck = h('label', 'ak-inline ak-tf-check');
  const shelfBox = h('input', 'kit-check');
  shelfBox.type = 'checkbox';
  shelfBox.checked = Boolean(form.addToShelf);
  shelfBox.addEventListener('change', () => { form.addToShelf = shelfBox.checked; onChange?.(); });
  shelfCheck.append(shelfBox, h('span', '', 'Kaynak öğrencinin rafında değil — rafa da ekle'));
  shelfCheck.hidden = true;
  // Ortak kitap seçici: bütün katalog (hazır kütüphane + kurum + bbdstore), süzgeçli, hedefin kitapları önce.
  const catalogButton = button(BOOKS.chooseFromCatalog, { variant: 'ghost', onClick: async () => {
    const chosen = await bookPicker(app, { target: coach ? { studentIds: [studentId] } : {}, subject: form.subjectId || '' });
    const book = chosen?.[0];
    if (!book) return;
    pick({ resourceId: book.id, title: book.title, subject: book.subjects?.[0] }, []);
    onChange?.();
  } });
  searchWrap.append(catalogButton);
  node.append(shelfRow, current, searchWrap, shelfCheck, sections);
  const meta = new Map();   // bölüm kimliği → {title, pageFrom, pageTo, questionCount}
  let token = 0;

  const shelf = coach ? (builder?.shelf || []) : [];
  const recent = coach ? (builder?.recentCombos || []) : [];

  function paintShelf() {
    shelfRow.replaceChildren();
    const chips = [];
    const seen = new Set();
    for (const item of recent.slice(0, 3)) {
      if (seen.has(item.resourceId)) continue;
      seen.add(item.resourceId);
      chips.push([item.resourceId, `↺ ${item.title}${item.sectionTitle ? ` › ${item.sectionTitle}` : ''}`, item.title, 'Son kullanılan']);
    }
    for (const item of shelf.slice(0, 8)) {
      if (seen.has(item.resourceId)) continue;
      seen.add(item.resourceId);
      chips.push([item.resourceId, `${item.title} · ${pct(item.progressPercent)}`, item.title, 'Öğrencinin rafında']);
    }
    if (!chips.length) return;
    shelfRow.append(h('span', 'ak-tf-mini', shelf.length ? 'Raf ve son kullanılanlar' : 'Son kullanılanlar'));
    const row = h('div', 'ak-chips');
    for (const [resourceId, label, title, hint] of chips) {
      const chip = h('button', `kit-chip${form.resourceId === resourceId ? ' on' : ''}`, label);
      chip.type = 'button';
      chip.title = hint;
      chip.setAttribute('aria-pressed', form.resourceId === resourceId ? 'true' : 'false');
      chip.addEventListener('click', () => {
        const next = shelf.find((item) => item.resourceId === resourceId)?.nextSection;
        pick({ resourceId, title }, next?.id ? [next.id] : []);
        onChange?.();
      });
      row.append(chip);
    }
    shelfRow.append(row);
  }

  function paintCurrent() {
    current.replaceChildren();
    searchWrap.hidden = Boolean(form.resourceId);
    if (!form.resourceId) { sections.replaceChildren(); shelfCheck.hidden = true; return; }
    current.append(h('b', '', form.resourceTitle || 'Seçili kaynak'),
      button('Değiştir', { variant: 'ghost', onClick: () => {
        form.resourceId = '';
        form.resourceTitle = '';
        form.sectionIds = [];
        form.sectionTitles = [];
        form.addToShelf = false;
        meta.clear();
        paintShelf();
        paintCurrent();
        onChange?.();
        input.focus();
      } }));
  }

  async function searchCatalog(text) {
    const digits = text.replace(/[\s-]/g, '');
    const query = /^\d{10}(\d{3})?$/.test(digits) ? { isbn: digits } : { q: text };
    const data = await app.api.get('/v1/catalog/library', { ...query, limit: 12 });
    return data?.items || [];
  }

  let searchTimer = null;
  input.addEventListener('input', () => {
    clearTimeout(searchTimer);
    const text = input.value.trim();
    if (text.length < 2) { results.hidden = true; return; }
    searchTimer = setTimeout(async () => {
      const mine = ++token;
      try {
        const items = await searchCatalog(text);
        if (mine !== token) return;
        results.replaceChildren();
        if (!items.length) results.append(h('div', 'ak-picker-empty', 'Eşleşen kaynak yok.'));
        for (const item of items) {
          const row = h('button', 'ak-picker-row ak-tf-result');
          row.type = 'button';
          row.append(h('b', '', item.title), h('span', '', [item.publisher, item.sectionCount ? `${num(item.sectionCount)} bölüm` : '',
            item.scope === 'tenant' ? 'kurum kaynağı' : ''].filter(Boolean).join(' · ')));
          row.addEventListener('mousedown', (event) => event.preventDefault());
          row.addEventListener('click', () => { results.hidden = true; input.value = ''; pick({ resourceId: item.id, title: item.title }, []); onChange?.(); });
          results.append(row);
        }
        results.hidden = false;
      } catch (failure) {
        results.replaceChildren(h('div', 'ak-picker-empty', problem(failure, 'Kaynaklar aranamadı.')));
        results.hidden = false;
      }
    }, 250);
  });
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); results.querySelector('.ak-tf-result')?.click(); }
    if (event.key === 'Escape' && !results.hidden) { event.preventDefault(); event.stopPropagation(); results.hidden = true; }
  });
  input.addEventListener('blur', () => setTimeout(() => { results.hidden = true; }, 150));

  function sectionChips(items) {
    const box = h('div', 'ak-tf-sec-box');
    const chips = h('div', 'ak-chips');
    for (const section of items) {
      meta.set(section.id, section);
      const label = `${section.done ? '✓ ' : ''}${section.title}${section.pageFrom ? ` (s. ${section.pageFrom}${section.pageTo
        && section.pageTo !== section.pageFrom ? `–${section.pageTo}` : ''})` : ''}${section.questionCount ? ` · ${section.questionCount} soru` : ''}`;
      const chip = h('button', 'kit-chip', label);
      chip.type = 'button';
      const sync = () => {
        const on = form.sectionIds.includes(section.id);
        chip.classList.toggle('on', on);
        chip.setAttribute('aria-pressed', on ? 'true' : 'false');
      };
      chip.addEventListener('click', () => {
        if (form.sectionIds.includes(section.id)) form.sectionIds = form.sectionIds.filter((item) => item !== section.id);
        else if (form.sectionIds.length < 20) form.sectionIds = [...form.sectionIds, section.id];
        form.sectionTitles = form.sectionIds.map((item) => meta.get(item)?.title).filter(Boolean);
        sync();
        onChange?.();
      });
      sync();
      chips.append(chip);
    }
    box.append(chips);
    return box;
  }

  async function loadSections(preselect) {
    const resourceId = form.resourceId;
    const mine = ++token;
    sections.replaceChildren(h('small', 'ak-muted', 'Bölümler yükleniyor…'));
    try {
      let items = [];
      if (coach && app.can('admin_getStudentResourceSections')) {
        const data = await app.api.get(`/v1/admin/students/${studentId}/resources/${resourceId}/sections`);
        if (mine !== token || resourceId !== form.resourceId) return;
        items = (data?.sections || []).filter((item) => item.kind !== 'unit');
        shelfCheck.hidden = Boolean(data?.onShelf);
        if (!data?.onShelf && form.addToShelf === false && !shelf.some((item) => item.resourceId === resourceId)) {
          form.addToShelf = true;
          shelfBox.checked = true;
        }
        if (!form.resourceTitle && data?.title) { form.resourceTitle = data.title; paintCurrent(); }
      } else {
        const data = await app.api.getCached(`/v1/catalog/library/${resourceId}`, undefined, { ttlMs: 10 * 60_000 });
        if (mine !== token || resourceId !== form.resourceId) return;
        if (!form.resourceTitle && data?.title) { form.resourceTitle = data.title; paintCurrent(); }
        for (const unit of [...(data?.toc || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
          for (const section of [...(unit.sections || [])].sort((a, b) => (a.order ?? 0) - (b.order ?? 0))) {
            items.push({ ...section, title: `${unit.title} › ${section.title}` });
          }
        }
      }
      sections.replaceChildren();
      if (preselect) form.sectionIds = preselect.filter((idValue) => items.some((item) => item.id === idValue));
      else form.sectionIds = form.sectionIds.filter((idValue) => items.some((item) => item.id === idValue));
      if (!items.length) {
        sections.append(h('small', 'ak-muted', 'Bu kaynağın içindekiler listesi yok; sayfa aralığı yazın.'));
      } else {
        sections.append(h('span', 'ak-tf-mini', 'Bölümler (isteğe bağlı, en çok 20)'), sectionChips(items));
      }
      form.sectionTitles = form.sectionIds.map((item) => meta.get(item)?.title).filter(Boolean);
      onChange?.();
    } catch (failure) {
      if (mine !== token) return;
      sections.replaceChildren(h('small', 'ak-coach-error', problem(failure, 'Bölümler okunamadı.')));
    }
  }

  function pick(resource, preselect = []) {
    form.resourceId = resource.resourceId;
    form.resourceTitle = resource.title || '';
    form.sectionIds = [];
    form.sectionTitles = [];
    form.addToShelf = false;
    shelfBox.checked = false;
    meta.clear();
    if (resource.subject?.id && !form.subjectId) { form.subjectId = resource.subject.id; form.subjectName = resource.subject.name || ''; }
    paintShelf();
    paintCurrent();
    loadSections(preselect);
  }

  paintShelf();
  paintCurrent();
  if (form.resourceId) loadSections(null);
  return {
    node,
    pick,
    info() {
      const chosen = form.sectionIds.map((item) => meta.get(item)).filter(Boolean);
      const froms = chosen.map((item) => item.pageFrom).filter(Boolean);
      const tos = chosen.map((item) => item.pageTo || item.pageFrom).filter(Boolean);
      const questions = chosen.reduce((total, item) => total + (item.questionCount || 0), 0);
      return { pageFrom: froms.length ? Math.min(...froms) : null, pageTo: tos.length ? Math.max(...tos) : null,
        questionCount: questions || null };
    },
    destroy: () => clearTimeout(searchTimer),
  };
}

// ------------------------------------------------------------------ video

/** Bağlantı yapıştır → sunucuda çözülür (başlık, kanal, süre); kütüphane / kesit için ayrıntılı pencere. */
function videoField(app, { form, onChange }) {
  const node = h('div', 'ak-tf-video');
  const row = h('div', 'ak-tf-video-row');
  const input = h('input', 'kit-input');
  input.type = 'url';
  input.placeholder = 'YouTube bağlantısını yapıştırın';
  input.setAttribute('aria-label', 'YouTube bağlantısı');
  const status = h('small', 'ak-muted');
  status.setAttribute('aria-live', 'polite');
  const list = h('div', 'ak-tf-video-list');
  const canResolve = app.can('admin_resolveVideoLink');
  const more = button('Kütüphane / kesitli ekle', { variant: 'ghost', onClick: async () => {
    const picked = await videoDialog(app, { max: 10 - form.videos.length });
    if (picked?.length) { form.videos.push(...picked); paint(); onChange?.(true); }
  } });
  more.disabled = !(canResolve || app.can('admin_listCoachLibrary'));
  input.disabled = !canResolve;
  if (!canResolve) input.placeholder = 'Video bağlantısı çözme yetkiniz yok';

  let busy = false;
  async function resolve() {
    const url = input.value.trim();
    if (!url || busy) return;
    if (!/youtu\.?be|youtube\.com/i.test(url)) { status.textContent = 'YouTube bağlantısı değil gibi görünüyor.'; return; }
    if (form.videos.length >= 10) { status.textContent = 'En çok 10 video eklenir.'; return; }
    busy = true;
    status.textContent = 'Video bilgisi alınıyor…';
    try {
      const data = await app.api.post('/v1/admin/videos/resolve', { url });
      if (form.videos.some((item) => !item.libraryItemId && item.videoId === data.video.videoId)) {
        status.textContent = 'Bu video zaten ekli.';
      } else {
        form.videos.push({ videoId: data.video.videoId, video: data.video, startSeconds: data.startSeconds ?? null,
          endSeconds: null, chapters: [], checkpoints: [], playlistId: data.playlistId || null, order: form.videos.length,
          warnings: data.warnings || [] });
        status.textContent = `Eklendi: ${data.video.title}${data.video.durationSeconds ? ` (${clockText(data.video.durationSeconds)})` : ''}.`;
        input.value = '';
        paint();
        onChange?.(true);
      }
    } catch (failure) {
      status.textContent = problem(failure, 'Bağlantı çözülemedi.');
    } finally {
      busy = false;
    }
  }
  input.addEventListener('paste', () => setTimeout(resolve, 0));
  input.addEventListener('change', resolve);
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); resolve(); }
  });

  function paint() {
    list.replaceChildren();
    form.videos.forEach((item, index) => {
      const line = h('div', 'ak-coach-action');
      line.append(videoCard(item.video || { title: item.videoId || 'Kütüphane öğesi' }, { warnings: item.warnings || [] }),
        h('small', 'ak-muted', [item.startSeconds || item.endSeconds ? 'kesit' : '',
          item.checkpoints?.length ? `${item.checkpoints.length} durak sorusu` : ''].filter(Boolean).join(' · ')),
        button('Çıkar', { variant: 'ghost', onClick: () => { form.videos.splice(index, 1); paint(); onChange?.(false); } }));
      list.append(line);
    });
  }
  row.append(input, more);
  node.append(row, status, list);
  paint();
  return { node };
}

// ------------------------------------------------------------------ küçükler

/** Seçenekleri İLK gerektiğinde yüklenen açılır kutu (deneme, ödev). */
function remotePick(app, { load, value = '', empty, label, describe, missing, onChange }) {
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', label);
  const items = new Map();
  let started = false;
  const fill = (options) => {
    select.replaceChildren();
    for (const option of options) {
      const item = h('option', '', option.label);
      item.value = option.value;
      select.append(item);
    }
  };
  fill([{ value: value || '', label: value ? missing : empty }]);
  select.value = value || '';
  select.addEventListener('change', () => onChange?.(select.value, items.get(select.value) || null));
  async function ensure() {
    if (started) return;
    started = true;
    const keep = select.value;
    fill([{ value: keep, label: 'Yükleniyor…' }]);
    select.disabled = true;
    try {
      const list = await load();
      for (const item of list) items.set(item.id, item);
      const options = [{ value: '', label: list.length ? empty : `${empty} (liste boş)` },
        ...list.map((item) => ({ value: item.id, label: describe(item) }))];
      if (keep && !items.has(keep)) options.push({ value: keep, label: missing });
      fill(options);
      select.value = keep;
    } catch (failure) {
      started = false;
      fill([{ value: keep, label: problem(failure, 'Liste alınamadı.') }]);
    } finally {
      select.disabled = false;
    }
  }
  return { node: select, ensure };
}

function filesField(app, form, onChange) {
  const box = h('div', 'ak-stack');
  const status = h('small', 'ak-muted', form.attachmentFileIds.length ? `${form.attachmentFileIds.length} ek` : '');
  if (!app.can('admin_uploadCoachingFile')) {
    box.append(h('small', 'ak-muted', 'Ek yükleme yetkiniz yok.'));
    return box;
  }
  const drop = fileDrop({ accept: ['pdf', 'png', 'jpg', 'jpeg', 'webp'], maxMb: 10, label: 'Foto ya da PDF ekleyin',
    onFile: async (file) => {
      if (form.attachmentFileIds.length >= 3) { status.textContent = 'En çok 3 ek eklenir.'; return; }
      status.textContent = 'Yükleniyor…';
      try {
        const result = await app.api.call('POST', '/v1/admin/coaching/files', {
          upload: { file, field: 'file', fields: { kind: 'coach_attachment' } } });
        if (result.data?.id) form.attachmentFileIds.push(result.data.id);
        status.textContent = `${form.attachmentFileIds.length} ek yüklendi.`;
        drop.clear();
        onChange?.();
      } catch (failure) {
        status.textContent = problem(failure, 'Dosya yüklenemedi.');
      }
    } });
  box.append(drop.node, status);
  return box;
}

/** Kısa durum satırı (kaydet ve yeni ekle sonrası). */
export function savedLine(text) {
  const line = h('div', 'ak-tf-saved');
  line.setAttribute('role', 'status');
  line.append(badge('Eklendi', 'good'), h('span', '', text));
  return line;
}
