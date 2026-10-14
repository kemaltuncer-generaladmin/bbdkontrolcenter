// BBD Akademi — öğrenci detayının "Öğrenme" sekmesi (F-A24; öğrenci tarafı F-S16–F-S20).
//
// SALT OKUNUR. Öğrencinin çalışma planı, hata defteri (görseller dahil), soru
// takibi, okuma ve ajandası öğrenci uçlarından (`/v1/students/{id}/*`, KM
// `bbd_akademi.view`) okunur; kurum burada yalnız izler. Plan görevi atama ve
// erteleme onayı plan yönetimindedir (F-A18), bu sekmede yazma işlemi yoktur.
//
// TEMBEL: modül çekmeceye gömülür (`learningPanel(app, studentId)`); ağ ilk
// `shown()` ile başlar, her iç sekme ilk açıldığında kendi çağrılarını paralel
// atar. Listeler imleçlidir (`pagedList`); satır başına ek istek atılmaz.
//
// GİZLİLİK (FEATURES §3d): kişisel ajanda etkinlikleri ve yapılacaklar yalnız
// öğrencinindir — Akademi süzer, burada istenmez de. Öğrencinin veli paylaşım
// tercihleri (hata görseli, öz değerlendirme) yalnız VELİ görünürlüğünü
// belirler; kurum personeli hata görsellerini görür.
//
// GÖRSEL: hata/kapak görseli gömülü imzalı bağlantıdan, süresi dolmuşsa taze
// bağlantıyla (`students_getFile`) köprüden iner ve nesne URL'si olarak çizilir;
// çekmece kapanınca bırakılır.
//
// KAPALI ÖZELLİK: sınıf kıyası (`class_comparison`) ve okuma ligi
// (`reading_league`) kiracı bayrağıdır; kapalıysa uç `feature_disabled` döner
// ve ekran sebebini söyler.
//
// Soru takibi ve okuma sekmeleri ile ortak yardımcılar (görsel indirme,
// dönem aralığı, hücre biçimleri) `students-learning-stats.js` dosyasındadır.
//
// Hata defteri PDF'i burada yok: işi başlatan uç (`students_exportMistakes`)
// entegrasyon anahtarına açık değil, yoklanacak bir iş olmaz.

import { bytes, button, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, hintBox, kpiRow, skeletonRows, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { barChart } from '../../ui-kit/charts.js';
import {
  DAY_LABEL, WEEKDAYS, addDays, empty, errorBox, fmtDate, fmtDateTime, fmtTime, kv, loader,
  multiChips, num, openDrawer, optionsOf, pagedList, percent, selectBox, statusBadge, todayIst,
  toolbar, weekStart,
} from './common.js';
import {
  accuracyTone, box, dayMonth, imageBag, minutes, periodRange, questionsTab, readingTab, stack,
} from './students-learning-stats.js';
import { documentButtons } from './documents.js';
import { studyPlanDoc } from './documents-logic.js';

/** İç sekmeler: anahtar → {etiket, okuma işlemi}. Okuma yetkisi yoksa çizilmez. */
const SUBTABS = [
  { key: 'plan', label: 'Çalışma planı', read: 'students_getPlanWeek' },
  { key: 'mistakes', label: 'Hata defteri', read: 'students_listMistakes' },
  { key: 'questions', label: 'Soru takibi', read: 'students_getQuestionStats' },
  { key: 'reading', label: 'Okuma', read: 'students_listReadingShelf' },
  { key: 'agenda', label: 'Ajanda', read: 'students_getAgenda' },
  { key: 'privacy', label: 'Gizlilik ve depolama', read: 'students_getLearningPrivacy' },
];

const DAY_SHORT = Object.fromEntries(WEEKDAYS.map(([key, short]) => [key, short]));

const TASK_TYPES = { topic_study: 'Konu çalışması', question_solving: 'Soru çözümü',
  resource_section: 'Kaynak bölümü', review: 'Tekrar', mistake_review: 'Hata tekrarı', reading: 'Okuma',
  exam: 'Deneme', homework: 'Ödev', free: 'Serbest görev' };
const TASK_STATUS = { planned: ['Planlandı', 'info'], done: ['Yapıldı', 'good'], partial: ['Kısmen', 'warn'],
  skipped: ['Atlandı', 'bad'], moved: ['Taşındı', 'dim'] };
const TASK_SOURCE = { self: 'Öğrencinin kendisi', coach: 'Koç görevi (kurum ataması)', suggestion: 'Plan önerisi' };
const POSTPONE_STATUS = { requested: ['Erteleme talebi bekliyor', 'warn'], approved: ['Erteleme onaylandı', 'good'],
  declined: ['Erteleme reddedildi', 'bad'], cancelled: ['Talep geri çekildi', 'dim'] };
const SUGGESTION_STATUS = { draft: ['Öğrencinin onayını bekliyor', 'info'], accepted: ['Onaylandı', 'good'],
  discarded: ['Vazgeçildi', 'dim'], expired: ['Süresi doldu', 'dim'] };
const SUGGESTION_REASONS = { weak_topic: 'Zayıf konu', review_due: 'Tekrar zamanı', mistake_review_due: 'Hata tekrarı',
  homework_due: 'Ödev', next_section: 'Sıradaki bölüm', exam_soon: 'Sınav yaklaşıyor', reading_goal: 'Okuma hedefi',
  balance: 'Denge' };
const TREND = { up: '▲ yükseliyor', down: '▼ düşüyor', flat: '→ yatay' };

// `unspecified`: öğrenci nedeni seçmedi (hızlı kayıt — Akademi README §14 madde 59; süzgeç ve kırılımda da geçerli).
const REASONS = { knowledge_gap: 'Bilgi eksiği', misconception: 'Kavram yanılgısı', calculation_error: 'İşlem hatası',
  careless: 'Dikkatsizlik', misread_question: 'Soruyu yanlış okuma', time_shortage: 'Süre yetmedi', guess: 'Tahmin',
  unspecified: 'Belirtilmemiş' };
const reasonText = (reason) => REASONS[reason || 'unspecified'] || reason;
const MISTAKE_STATUS = { active: ['Etkin', 'info'], learned: ['Öğrenildi', 'good'] };
const ORIGINS = { manual: 'Öğrenci ekledi', exam_suggestion: 'Deneme önerisinden' };
const IMAGE_ROLES = { question: 'Soru', solution: 'Çözüm', note: 'Not' };
const SOURCE_KIND = { resource: 'Kaynak', exam: 'Deneme', external_exam: 'Dış deneme', other: 'Diğer' };
const REVIEW_RESULT = { solved: 'Çözdü', failed: 'Yine yapamadı' };

/** Kurumun görebildiği ajanda türleri (kişisel etkinlik ve yapılacaklar öğrencinindir). */
const AGENDA_TYPES = { lesson: 'Ders', etut: 'Etüt', one_off: 'Tek seferlik ders', homework: 'Ödev',
  exam: 'Deneme', plan_task: 'Plan görevi', meeting: 'Görüşme', leave: 'İzin', institution_event: 'Kurum takvimi' };
const EVENT_KINDS = { holiday: 'Tatil', event: 'Etkinlik', parent_meeting: 'Veli toplantısı', exam_day: 'Deneme günü',
  registration_period: 'Kayıt dönemi', other: 'Diğer' };
const IMAGE_SHARE = { none: 'Paylaşmıyor — veli yalnız sayısal özeti görür', all: 'Bütün hata görselleri',
  selected: 'Yalnız paylaşmak için işaretlediği hatalar' };
const FILE_KINDS = { mistake_image: 'Hata defteri görselleri', homework_image: 'Ödev görselleri',
  flashcard_image: 'Bilgi kartı görselleri', book_cover: 'Kitap kapakları' };

const weekdayOf = (iso) => {
  const [year, month, day] = String(iso).split('-').map(Number);
  return WEEKDAYS[(new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7][0];
};

/**
 * Öğrenci detayına gömülen "Öğrenme" sekmesi.
 * @returns {{node: HTMLElement, shown: (payload?: {learningTab?: string}) => void, destroy: () => void}}
 */
export function learningPanel(app, studentId) {
  loadStyles(import.meta.url, 'students-learning.css');
  const node = h('div', 'ak-stack ak-sl');
  const visible = SUBTABS.filter((tab) => app.can(tab.read));
  if (!visible.length) {
    node.append(empty('Öğrenme verisine erişim yok', 'Öğrencinin öğrenme ekranlarından hiçbirine yetkiniz yok.'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  const builders = {
    plan: () => planTab(app, studentId),
    mistakes: () => mistakesTab(app, studentId),
    questions: () => questionsTab(app, studentId),
    reading: () => readingTab(app, studentId),
    agenda: () => agendaTab(app, studentId),
    privacy: () => privacyTab(app, studentId),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => showTab(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.learningTab && visible.some((tab) => tab.key === payload.learningTab)
        ? payload.learningTab : null;
      if (!started) {
        started = true;
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
      } else if (wanted) {
        tabs.select(wanted, false);
        showTab(wanted);
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ------------------------------------------------------------- çalışma planı

function planTab(app, studentId) {
  const node = h('div', 'ak-stack');
  let week = weekStart(todayIst());
  const label = h('b', 'ak-sl-week-label');
  const paintLabel = () => { label.textContent = `${fmtDate(week)} – ${fmtDate(addDays(week, 6))}`; };
  const go = (next) => { week = next; paintLabel(); weekView.reload(); };
  node.append(toolbar(
    button('‹ Önceki hafta', { variant: 'ghost', onClick: () => go(addDays(week, -7)) }),
    button('Bu hafta', { variant: 'ghost', onClick: () => go(weekStart(todayIst())) }),
    button('Sonraki hafta ›', { variant: 'ghost', onClick: () => go(addDays(week, 7)) }),
    label,
    h('span', 'kit-spacer'),
    documentButtons(app, { title: 'Haftalık çalışma planı', label: 'Plan',
      build: async () => {
        const [detail, data] = await Promise.all([app.api.get(`/v1/admin/students/${studentId}`),
          app.api.get(`/v1/students/${studentId}/plan`, { week })]);
        return studyPlanDoc(detail?.student, data);
      } }),
  ));

  const weekView = loader(() => app.api.get(`/v1/students/${studentId}/plan`, { week }),
    (data) => renderWeek(data), { rows: 5, columns: 7 });
  const thisWeek = weekStart(todayIst());
  const adherence = loader(() => app.api.get(`/v1/students/${studentId}/plan/adherence`,
    { from: addDays(thisWeek, -49), to: addDays(thisWeek, 6) }), (data) => renderAdherence(data), { rows: 4, columns: 3 });
  const prefs = loader(() => app.api.getCached(`/v1/students/${studentId}/plan/preferences`, undefined,
    { ttlMs: 60_000 }), (data) => renderPreferences(data), { rows: 5, columns: 2 });
  const notes = pagedList({
    app,
    path: `/v1/students/${studentId}/plan/encouragements`,
    limit: 10,
    dense: true,
    columns: [
      { key: 'at', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'from', label: 'Veli', width: 'minmax(0, 1fr)', cell: (row) => row.from?.displayName || '—' },
      { key: 'message', label: 'Not', width: 'minmax(0, 2.4fr)', cell: (row) => row.message || '—' },
      { key: 'week', label: 'Hafta', width: '100px', cell: (row) => fmtDate(row.weekStart) },
    ],
    empty: { title: 'Teşvik notu yok', text: 'Veliler uygulamadan plan için teşvik notu bırakabilir.' },
  });

  const lower = h('div', 'ak-grid-2');
  lower.append(card('Plan uyumu — son 8 hafta', adherence.node), card('Çalışma saatleri ve bildirimler', prefs.node));
  node.append(weekView.node, lower, card('Veli teşvik notları', notes.node));

  function renderWeek(data) {
    const summary = data.summary || {};
    const nodes = [kpiRow([
      { label: 'Planlanan görev', value: num(summary.plannedTasks),
        title: summary.movedTasks ? `${num(summary.movedTasks)} görev başka güne taşındı (sayılmaz).` : '' },
      { label: 'Yapılan', value: `${num(summary.doneTasks)}${summary.partialTasks ? ` + ${num(summary.partialTasks)} kısmi` : ''}` },
      { label: 'Atlanan', value: num(summary.skippedTasks), tone: summary.skippedTasks ? 'bad' : '' },
      { label: 'Süre (yapılan / planlanan)', value: `${num(summary.doneMinutes)} / ${minutes(summary.plannedMinutes)}` },
      { label: 'Plan uyumu', value: summary.adherencePercent === null || summary.adherencePercent === undefined
        ? 'Plan yok' : percent(summary.adherencePercent, 0),
      tone: summary.adherencePercent !== null && summary.adherencePercent < 50 ? 'bad' : '' },
    ])];
    if (data.pendingSuggestionId) {
      const line = h('div', 'ak-inline');
      line.append(alertBox('Öğrencinin bu hafta için onay bekleyen bir “Planımı oluştur” önerisi var.', 'info'),
        button('Öneriyi incele', { variant: 'ghost',
          onClick: () => suggestionDrawer(app, studentId, data.pendingSuggestionId) }));
      nodes.push(line);
    }
    const grid = h('div', 'ak-sl-week');
    const today = todayIst();
    for (const day of data.days || []) {
      const column = h('div', `ak-sl-day${day.date === today ? ' today' : ''}${day.holiday ? ' holiday' : ''}`);
      const head = h('div', 'ak-sl-day-head');
      head.append(h('b', '', `${DAY_SHORT[day.dayOfWeek] || ''} ${dayMonth(day.date)}`),
        h('small', '', `${num(day.doneMinutes)}/${minutes(day.plannedMinutes)}`));
      column.append(head);
      if (day.holiday) column.append(badge(day.holiday.title || 'Tatil', 'warn'));
      column.append(h('small', 'ak-muted', `Boş süre ${minutes(day.availableMinutes)}`));
      const tasks = [...(day.tasks || [])].sort((a, b) => String(a.startTime || '99').localeCompare(String(b.startTime || '99'))
        || (a.order || 0) - (b.order || 0));
      if (!tasks.length) column.append(h('div', 'ak-sl-day-empty', 'Görev yok'));
      for (const task of tasks) column.append(taskChip(task));
      grid.append(column);
    }
    nodes.push(grid);
    return nodes;
  }

  function taskChip(task) {
    const chip = h('button', `ak-sl-task ${task.status || ''}`);
    chip.type = 'button';
    const top = h('div', 'ak-sl-task-top');
    top.append(h('small', '', task.startTime ? `${task.startTime}${task.endTime ? `–${task.endTime}` : ''}`
      : minutes(task.estimatedMinutes)), statusBadge(TASK_STATUS, task.status));
    chip.append(top, h('span', 'ak-sl-task-title', task.title || TASK_TYPES[task.type] || 'Görev'));
    const meta = h('div', 'ak-sl-task-meta');
    meta.append(h('small', '', [task.subject?.shortName || task.subject?.name, TASK_TYPES[task.type]].filter(Boolean).join(' · ')));
    if (task.locked || task.source === 'coach') meta.append(badge('Koç', 'info'));
    if (task.postponeRequest?.status === 'requested') meta.append(badge('Erteleme talebi', 'warn'));
    chip.append(meta);
    chip.addEventListener('click', () => taskDrawer(app, studentId, task.id));
    return chip;
  }

  function renderAdherence(data) {
    const weeks = data.weeks || [];
    if (!weeks.length) return empty('Plan uyumu verisi yok', 'Öğrenci bu aralıkta plan oluşturmamış.');
    const head = h('div', 'ak-muted ak-sl-note',
      `Ortalama ${percent(data.averagePercent, 0)} · eğilim ${TREND[data.trend] || '—'} · özet ${fmtDateTime(data.computedAt)}`);
    const chart = barChart(weeks.map((item) => ({
      label: `${dayMonth(item.weekStart)} haftası`,
      value: item.adherencePercent ?? 0,
      display: item.adherencePercent === null || item.adherencePercent === undefined
        ? 'plan yok' : `${percent(item.adherencePercent, 0)} · ${num(item.doneMinutes)}/${minutes(item.plannedMinutes)}`,
    })), { max: 26 });
    const subjects = new Map();
    for (const item of weeks) {
      for (const row of item.bySubject || []) {
        const key = row.subject?.id || row.subject?.name;
        const entry = subjects.get(key) || { name: row.subject?.name || '—', planned: 0, done: 0 };
        entry.planned += row.plannedMinutes || 0;
        entry.done += row.doneMinutes || 0;
        subjects.set(key, entry);
      }
    }
    const table = dataTable({
      columns: [
        { key: 'name', label: 'Ders', width: 'minmax(0, 1.6fr)' },
        { key: 'planned', label: 'Planlanan', align: 'num', cell: (row) => minutes(row.planned) },
        { key: 'done', label: 'Yapılan', align: 'num', cell: (row) => minutes(row.done) },
        { key: 'rate', label: 'Uyum', align: 'num', cell: (row) => (row.planned
          ? badge(percent((row.done / row.planned) * 100, 0), accuracyTone((row.done / row.planned) * 100)) : '—') },
      ],
      rows: [...subjects.values()].sort((a, b) => b.planned - a.planned),
      dense: true,
      rowKey: (row) => row.name,
      empty: empty('Ders kırılımı yok'),
    });
    return [head, chart, table.node];
  }

  function renderPreferences(data) {
    const notify = data.notifications || {};
    const ranges = (data.availability || []).filter((day) => (day.ranges || []).length)
      .map((day) => `${DAY_SHORT[day.dayOfWeek] || day.dayOfWeek} ${day.ranges.map((range) => `${range.start}–${range.end}`).join(', ')}`);
    return kv([
      ['Çalışma saatleri', ranges.length ? ranges.join(' · ') : 'Seçilmemiş'],
      ['Günlük en çok', minutes(data.maxDailyMinutes)],
      ['Oturum / mola', `${minutes(data.sessionMinutes)} / ${minutes(data.breakMinutes)}`],
      ['Dinlenme günleri', (data.restDays || []).map((day) => DAY_LABEL[day] || day).join(', ') || 'Yok'],
      ['Kaçan görev', data.autoMoveMissed ? 'Gece sonraki boş güne kendiliğinden taşınır' : 'Kendiliğinden taşınmaz'],
      ['Günün planı bildirimi', notify.dailyPlanAt || 'Kapalı'],
      ['Görev hatırlatma', notify.taskReminderMinutes === null || notify.taskReminderMinutes === undefined
        ? 'Kapalı' : `${num(notify.taskReminderMinutes)} dk önce`],
      ['Kaçırılan görev özeti', notify.missedSummaryAt || 'Kapalı'],
      ['Son değişiklik', fmtDateTime(data.updatedAt)],
    ]);
  }

  paintLabel();
  return {
    node,
    reload: () => Promise.all([weekView.reload(), adherence.reload(), prefs.reload(), notes.reload()]),
    destroy() { notes.destroy(); },
  };
}

function taskDrawer(app, studentId, taskId) {
  const panel = openDrawer(app, { title: 'Plan görevi', subtitle: 'Salt okunur' });
  const view = loader(() => app.api.get(`/v1/students/${studentId}/plan/tasks/${taskId}`), (task) => {
    panel.setTitle(task.title || TASK_TYPES[task.type] || 'Plan görevi');
    const nodes = [];
    if (task.locked) {
      nodes.push(alertBox('Koç görevi: öğrenci silemez ve gününü değiştiremez; yalnız erteleme talep edebilir.', 'info'));
    }
    const sections = (task.sections || []).map((item) => item.title).join(', ');
    nodes.push(card('', kv([
      ['Tür', TASK_TYPES[task.type] || task.type],
      ['Durum', statusBadge(TASK_STATUS, task.status)],
      ['Gün', `${fmtDate(task.date)}${task.startTime ? ` · ${task.startTime}${task.endTime ? `–${task.endTime}` : ''}` : ''}`],
      ['Tahmini süre', minutes(task.estimatedMinutes)],
      ['Kaynağı', TASK_SOURCE[task.source] || task.source],
      ['Ders / konu', [task.subject?.name, task.topic?.name].filter(Boolean).join(' · ') || '—'],
      task.resource ? ['Kaynak kitap', [task.resource.title, sections].filter(Boolean).join(' · ')] : null,
      task.pageFrom || task.pageTo ? ['Sayfa aralığı', `${task.pageFrom ?? '?'}–${task.pageTo ?? '?'}`] : null,
      task.targetQuestions ? ['Hedef soru', num(task.targetQuestions)] : null,
      task.targetPages ? ['Hedef sayfa', num(task.targetPages)] : null,
      task.mistakeReview ? ['Hata tekrarı', `${num(task.mistakeReview.count)} hata`] : null,
      ['Not', task.note || '—'],
      task.assignment ? ['Atayan', [task.assignment.assignedBy?.displayName, fmtDateTime(task.assignment.assignedAt),
        task.assignment.templateName ? `şablon: ${task.assignment.templateName}` : ''].filter(Boolean).join(' · ')] : null,
      ['Oluşturuldu', fmtDateTime(task.createdAt)],
      ['Güncellendi', fmtDateTime(task.updatedAt)],
    ])));
    const done = task.completion;
    if (done) {
      nodes.push(card('Tamamlama', kv([
        ['Durum', statusBadge(TASK_STATUS, done.status)],
        ['Zaman', fmtDateTime(done.completedAt)],
        ['Süre', minutes(done.durationMinutes)],
        ['Doğru / yanlış / boş', `${num(done.correct)} / ${num(done.wrong)} / ${num(done.blank)}`],
        done.pagesRead !== null && done.pagesRead !== undefined ? ['Okunan sayfa', num(done.pagesRead)] : null,
        ['Not', done.note || '—'],
      ])));
    }
    const postpone = task.postponeRequest;
    if (postpone) {
      nodes.push(card('Erteleme talebi', kv([
        ['Durum', statusBadge(POSTPONE_STATUS, postpone.status)],
        ['İstenen gün', `${fmtDate(postpone.requestedDate)}${postpone.requestedStartTime ? ` · ${postpone.requestedStartTime}` : ''}`],
        ['Gerekçe', postpone.reason || '—'],
        ['Talep', fmtDateTime(postpone.createdAt)],
        postpone.decidedAt ? ['Karar', [postpone.decidedBy?.displayName, fmtDateTime(postpone.decidedAt)].filter(Boolean).join(' · ')] : null,
        postpone.approvedDate ? ['Onaylanan gün', fmtDate(postpone.approvedDate)] : null,
        postpone.decisionNote ? ['Karar notu', postpone.decisionNote] : null,
      ])));
    }
    const links = [];
    if (task.movedFromTaskId) {
      links.push(button('Taşındığı önceki görev', { variant: 'ghost',
        onClick: () => taskDrawer(app, studentId, task.movedFromTaskId) }));
    }
    if (task.movedToTaskId) {
      links.push(button('Taşındığı yeni görev', { variant: 'ghost',
        onClick: () => taskDrawer(app, studentId, task.movedToTaskId) }));
    }
    if (links.length) nodes.push(toolbar(...links));
    return nodes;
  }, { rows: 6, columns: 2 });
  panel.body.append(view.node);
  view.reload();
}

function suggestionDrawer(app, studentId, suggestionId) {
  const panel = openDrawer(app, { title: 'Plan önerisi', subtitle: 'Öneriyi yalnız öğrenci onaylar; kurum buradan izler.' });
  panel.node.classList.add('ak-sl-drawer');
  const view = loader(() => app.api.get(`/v1/students/${studentId}/plan/suggestions/${suggestionId}`), (suggestion) => {
    const inputs = suggestion.inputs || {};
    const info = card('', kv([
      ['Durum', statusBadge(SUGGESTION_STATUS, suggestion.status)],
      ['Hafta', `${fmtDate(suggestion.weekStart)} – ${fmtDate(addDays(suggestion.weekStart, 6))}`],
      ['Üretildi', fmtDateTime(suggestion.generatedAt)],
      ['Geçerlilik', fmtDateTime(suggestion.expiresAt)],
      ['Hedef sınav', inputs.examTarget ? `${inputs.examTarget}${inputs.examDate ? ` · ${fmtDate(inputs.examDate)}` : ''}`
        + `${inputs.daysToExam !== null && inputs.daysToExam !== undefined ? ` · ${num(inputs.daysToExam)} gün` : ''}` : '—'],
      ['Boş saat dilimi', num((inputs.freeSlots || []).length)],
      ['Zayıf konu / tekrar / hata tekrarı', `${num(inputs.weakTopicCount)} / ${num(inputs.reviewDueCount)} / `
        + `${num(inputs.mistakeReviewDueCount)}`],
      ['Bekleyen ödev', num(inputs.pendingHomeworkCount)],
      ['Tatil günleri', (inputs.holidays || []).map(fmtDate).join(', ') || 'Yok'],
    ]));
    const table = dataTable({
      columns: [
        { key: 'date', label: 'Gün', width: '96px', cell: (row) => `${DAY_SHORT[weekdayOf(row.date)]} ${dayMonth(row.date)}` },
        { key: 'time', label: 'Saat', width: '96px', cell: (row) => (row.startTime
          ? `${row.startTime}${row.endTime ? `–${row.endTime}` : ''}` : minutes(row.estimatedMinutes)) },
        { key: 'title', label: 'Görev', width: 'minmax(0, 2fr)', cell: (row) => stack(row.title,
          [TASK_TYPES[row.type], row.subject?.name, row.topic?.name, row.resource?.title].filter(Boolean).join(' · ')) },
        { key: 'reasons', label: 'Gerekçe', width: 'minmax(0, 1.4fr)', cell: (row) => (row.reasons || [])
          .map((reason) => SUGGESTION_REASONS[reason] || reason).join(', ') || '—' },
      ],
      rows: suggestion.tasks || [],
      rowKey: (row) => row.key,
      dense: true,
      empty: empty('Öneride görev yok'),
    });
    return [info, card(`Önerilen görevler (${num((suggestion.tasks || []).length)})`, table.node)];
  }, { rows: 6, columns: 3 });
  panel.body.append(view.node);
  view.reload();
}

// --------------------------------------------------------------- hata defteri

function sourceText(source) {
  if (!source) return '—';
  if (source.kind === 'resource') {
    return [source.resourceTitle, source.sectionTitle, source.page ? `s. ${source.page}` : '',
      source.questionNo ? `soru ${source.questionNo}` : ''].filter(Boolean).join(' · ') || SOURCE_KIND.resource;
  }
  if (source.kind === 'exam' || source.kind === 'external_exam') {
    return [source.examName || SOURCE_KIND[source.kind], source.examQuestionNo ? `soru ${source.examQuestionNo}` : '']
      .filter(Boolean).join(' · ');
  }
  return source.description || SOURCE_KIND[source.kind] || '—';
}

function mistakeColumns() {
  return [
    { key: 'subject', label: 'Ders / konu', width: 'minmax(0, 1.6fr)', cell: (row) => stack(row.subject?.name, row.topic?.name) },
    { key: 'source', label: 'Kaynak', width: 'minmax(0, 1.5fr)', cell: (row) => sourceText(row.source) },
    { key: 'reason', label: 'Neden', width: 'minmax(0, 1fr)', cell: (row) => reasonText(row.reason) },
    { key: 'status', label: 'Durum', width: '150px', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(statusBadge(MISTAKE_STATUS, row.status));
      if (row.review && row.status === 'active') cell.append(h('small', '', `Aşama ${row.review.stage}/5 · ${row.review.intervalDays} gün`));
      return cell;
    } },
    { key: 'due', label: 'Sonraki tekrar', width: '110px', cell: (row) => fmtDate(row.review?.dueDate) },
    { key: 'images', label: 'Görsel', width: '80px', cell: (row) => ((row.images || []).length
      ? num(row.images.length) : row.imagesHidden ? 'Gizli' : '—') },
    { key: 'at', label: 'Eklendi', width: '96px', cell: (row) => (row.actedBy
      ? stack(fmtDate(row.createdAt), row.actedBy) : fmtDate(row.createdAt)) },
  ];
}

function mistakesTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const open = (row) => mistakeDrawer(app, studentId, row.id);

  const filters = { status: 'active', reason: '', subject: '', origin: '' };
  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => {
    filters.subject = value; list.reload();
  }, { label: 'Ders' });
  const summary = loader(() => app.api.get(`/v1/students/${studentId}/mistakes/summary`),
    (data) => renderSummary(data), { rows: 3, columns: 5 });

  let scope = 'due';
  const scopes = chipRow([{ key: 'due', label: 'Zamanı gelenler' }, { key: 'upcoming', label: 'Yaklaşanlar' }], scope,
    (key) => { scope = key || 'due'; scopes.set(scope); queue.reload(); });
  const queue = pagedList({
    app,
    path: `/v1/students/${studentId}/mistakes/review-queue`,
    query: () => ({ scope }),
    limit: 10,
    dense: true,
    columns: mistakeColumns().filter((column) => ['subject', 'reason', 'status', 'due'].includes(column.key)),
    onRow: open,
    empty: { title: 'Tekrar zamanı gelen hata yok', text: 'Aralıklı tekrar (1 → 3 → 7 → 14 → 30 gün) takvimine göre.' },
  });

  const statusSelect = selectBox([{ value: 'active', label: 'Etkin hatalar' }, { value: 'learned', label: 'Öğrenilenler' },
    { value: 'all', label: 'Tümü' }, { value: 'expiring', label: 'Yakında silinecekler' }], 'active',
  (value) => { filters.status = value; list.reload(); }, { label: 'Durum' });
  const reasonSelect = selectBox(optionsOf(REASONS, { empty: 'Tüm nedenler' }), '', (value) => {
    filters.reason = value; list.reload();
  }, { label: 'Neden' });
  const originSelect = selectBox(optionsOf(ORIGINS, { empty: 'Tüm kayıtlar' }), '', (value) => {
    filters.origin = value; list.reload();
  }, { label: 'Kayıt' });
  const list = pagedList({
    app,
    path: `/v1/students/${studentId}/mistakes`,
    // "Yakında silinecekler": saklama uyarısı gönderilmiş eski hatalar (Akademi README §14 madde 64)
    query: () => ({ status: filters.status === 'expiring' ? 'all' : filters.status,
      expiring: filters.status === 'expiring' ? true : undefined, reason: filters.reason || undefined,
      subject: filters.subject || undefined, origin: filters.origin || undefined }),
    limit: 20,
    columns: mistakeColumns(),
    onRow: open,
    empty: { title: 'Bu süzgece uyan hata yok', text: 'Süzgeçleri değiştirin.' },
  });

  node.append(summary.node,
    card('Tekrar kuyruğu', box(scopes.node, queue.node)),
    card('Hata defteri', box(toolbar(statusSelect.node, subjectSelect.node, reasonSelect.node, originSelect.node), list.node)));

  function renderSummary(data) {
    subjectSelect.options([{ value: '', label: 'Tüm dersler' },
      ...(data.bySubject || []).map((row) => ({ value: row.subject?.id, label: `${row.subject?.name} (${num(row.count)})` }))]);
    const nodes = [kpiRow([
      { label: 'Etkin hata', value: num(data.activeCount) },
      { label: 'Öğrenildi', value: num(data.learnedCount), tone: data.learnedCount ? 'good' : '' },
      { label: 'Dönemde eklenen', value: num(data.addedCount) },
      { label: 'Dönemde öğrenilen', value: num(data.newlyLearnedCount) },
      { label: 'Tekrar zamanı gelen', value: num(data.dueCount), tone: data.dueCount ? 'warn' : '' },
    ])];
    const dominant = data.dominantReason?.reason
      ? ` · hataların ${percent(data.dominantReason.percent, 0)}'i “${REASONS[data.dominantReason.reason] || data.dominantReason.reason}”`
      : '';
    // saklama süresiyle temizlenenler toplamda sayılır; uyarısı gönderilenler yakında silinecek
    const cleaned = data.expiredCount ? ` · ${num(data.expiredCount)} eski hata temizlendi` : '';
    const expiring = data.expiringCount ? ` · ${num(data.expiringCount)} hata yakında silinecek` : '';
    nodes.push(h('div', 'ak-muted ak-sl-note',
      `${fmtDate(data.from)} – ${fmtDate(data.to)} · toplam ${num(data.totalCount)} hata${dominant}${cleaned}${expiring} · özet ${fmtDateTime(data.computedAt)}`));
    const reasons = barChart((data.byReason || []).map((row) => ({ label: reasonText(row.reason),
      value: row.count, display: `${num(row.count)} · ${percent(row.percent, 0)}` })), { max: 7 });
    const topics = dataTable({
      columns: [
        { key: 'topic', label: 'Konu', width: 'minmax(0, 2fr)', cell: (row) => row.topic?.name || '—' },
        { key: 'count', label: 'Toplam', align: 'num', cell: (row) => num(row.count) },
        { key: 'active', label: 'Etkin', align: 'num', cell: (row) => num(row.active) },
        { key: 'prio', label: '', width: '130px', cell: (row) => (row.prioritized ? badge('Tekrarda öncelikli', 'warn') : h('span')) },
      ],
      rows: data.topTopics || [],
      rowKey: (row) => row.topic?.id,
      dense: true,
      empty: empty('Konu kırılımı yok'),
    });
    const grid = h('div', 'ak-grid-2');
    grid.append(card('Hata nedenleri', reasons), card('En çok hata yapılan konular', topics.node));
    nodes.push(grid);
    return nodes;
  }

  return {
    node,
    reload: () => Promise.all([summary.reload(), queue.reload(), list.reload()]),
    destroy() { queue.destroy(); list.destroy(); },
  };
}

function mistakeDrawer(app, studentId, mistakeId) {
  const images = imageBag(app, studentId);
  const panel = openDrawer(app, { title: 'Hata', subtitle: 'Hata defteri · salt okunur', onClose: () => images.close() });
  panel.node.classList.add('ak-sl-drawer');
  const view = loader(() => app.api.get(`/v1/students/${studentId}/mistakes/${mistakeId}`), (mistake) => {
    panel.setTitle([mistake.subject?.name, mistake.topic?.name].filter(Boolean).join(' — ') || 'Hata');
    const nodes = [];
    const gallery = h('div', 'ak-sl-gallery');
    for (const image of mistake.images || []) {
      gallery.append(images.figure(image.file, IMAGE_ROLES[image.role] || image.role));
    }
    if ((mistake.images || []).length) nodes.push(gallery);
    else if (mistake.imagesHidden) nodes.push(alertBox('Bu hatanın görselleri gösterilemiyor.', 'info'));
    else if (mistake.imagesExpiredAt) {
      nodes.push(alertBox(`Fotoğraf saklama süresi doldu (${fmtDate(mistake.imagesExpiredAt)}); metin ve konu duruyor.`, 'info'));
    }
    const review = mistake.review || {};
    nodes.push(card('', kv([
      ['Ders / konu', [mistake.subject?.name, mistake.topic?.name].filter(Boolean).join(' · ') || '—'],
      ['Kaynak', sourceText(mistake.source)],
      ['Doğru cevap', mistake.correctAnswer || '—'],
      ['Öğrencinin cevabı', mistake.studentAnswer || '—'],
      ['Çözüm notu', mistake.solutionNote || '—'],
      ['Etiketler', (mistake.tags || []).join(', ') || '—'],
      ['Hata nedeni', reasonText(mistake.reason)],
      ['Kayıt', ORIGINS[mistake.origin] || mistake.origin],
      ['Durum', statusBadge(MISTAKE_STATUS, mistake.status)],
      ['Tekrar aşaması', review.stage ? `${review.stage}/5 · ${review.intervalDays} günlük aralık${review.checkup ? ' · kontrol tekrarı' : ''}` : '—'],
      ['Sonraki tekrar', fmtDate(review.dueDate)],
      ['Üst üste doğru', `${num(review.consecutiveSolved)} / 3`],
      ['Tekrar sayısı', num(review.reviewCount)],
      ['Son tekrar', review.lastReviewedAt ? `${fmtDateTime(review.lastReviewedAt)} · ${REVIEW_RESULT[review.lastResult] || '—'}` : '—'],
      ['Veliyle paylaşım', mistake.sharedWithGuardian ? 'Öğrenci paylaşmak için işaretledi' : 'İşaretlenmedi'],
      mistake.learnedAt ? ['Öğrenildi', fmtDateTime(mistake.learnedAt)] : null,
      ['Saklama', mistake.keep ? 'Öğrenci “Sakla” dedi — kendiliğinden silinmez'
        : mistake.scheduledDeletionOn ? `${fmtDate(mistake.scheduledDeletionOn)} tarihinde kendiliğinden silinecek` : '—'],
      ['Eklendi', fmtDateTime(mistake.createdAt)],
      mistake.actedBy ? ['Giren', `${mistake.actedBy} (öğrenci adına)`] : null,
    ])));
    return nodes;
  }, { rows: 6, columns: 2 });
  panel.body.append(view.node);
  view.reload();
}

// --------------------------------------------------------------------- ajanda

function agendaTab(app, studentId) {
  const node = h('div', 'ak-stack');
  const all = Object.keys(AGENDA_TYPES);
  const state = { mode: 'week', anchor: todayIst(), types: [...all] };

  const countdown = loader(() => app.api.getCached(`/v1/students/${studentId}/agenda/countdown`, undefined,
    { ttlMs: 5 * 60_000 }), (data) => renderCountdown(data), { rows: 2, columns: 3 });

  const label = h('b', 'ak-sl-week-label');
  const modeSelect = selectBox([{ value: 'week', label: 'Hafta' }, { value: 'two_weeks', label: '2 hafta' },
    { value: 'month', label: 'Ay' }], state.mode, (value) => { state.mode = value; reload(); }, { label: 'Görünüm' });
  const step = (direction) => {
    if (state.mode === 'month') {
      const [year, month] = state.anchor.split('-').map(Number);
      state.anchor = new Date(Date.UTC(year, month - 1 + direction, 1)).toISOString().slice(0, 10);
    } else {
      state.anchor = addDays(state.anchor, direction * (state.mode === 'week' ? 7 : 14));
    }
    reload();
  };
  const typeChips = multiChips(all.map((value) => ({ value, label: AGENDA_TYPES[value] })), state.types,
    (list) => { state.types = list; reload(); });

  const listSlot = h('div', 'ak-stack');
  const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadMore() });
  more.hidden = true;
  node.append(
    card('Sınav geri sayımı', countdown.node),
    toolbar(modeSelect.node,
      button('‹', { variant: 'ghost', title: 'Önceki', onClick: () => step(-1) }),
      button('Bugün', { variant: 'ghost', onClick: () => { state.anchor = todayIst(); reload(); } }),
      button('›', { variant: 'ghost', title: 'Sonraki', onClick: () => step(1) }), label),
    typeChips.node,
    hintBox('Öğrencinin kişisel etkinlikleri ve yapılacakları yalnız kendisinindir; Akademi bunları kuruma göstermez.'),
    listSlot,
    toolbar(more),
  );

  let items = [];
  let holidays = new Set();
  let cursor = null;
  let query = null;
  let token = 0;
  let busy = false;

  function range() {
    if (state.mode === 'month') {
      const { from, to } = periodRange('month', state.anchor);
      return { from, to };
    }
    const from = weekStart(state.anchor);
    return { from, to: addDays(from, state.mode === 'week' ? 6 : 13) };
  }

  function reload() {
    token += 1;
    busy = false;
    items = [];
    holidays = new Set();
    cursor = null;
    const { from, to } = range();
    label.textContent = `${fmtDate(from)} – ${fmtDate(to)}`;
    if (!state.types.length) {
      more.hidden = true;
      listSlot.replaceChildren(empty('Tür seçilmedi', 'Takvimde görmek istediğiniz en az bir türü seçin.'));
      return Promise.resolve();
    }
    query = { from, to, limit: 200, types: state.types.length < all.length ? state.types : undefined };
    listSlot.replaceChildren(skeletonRows(5, 4));
    return loadMore(true);
  }

  async function loadMore(first = false) {
    if (busy || (!first && !cursor)) return;
    busy = true;
    more.disabled = true;
    const mine = token;
    try {
      const data = await app.api.get(`/v1/students/${studentId}/agenda`, { ...query, cursor: cursor || undefined });
      if (mine !== token) return;
      items = items.concat(data?.items || []);
      for (const day of data?.holidays || []) holidays.add(day);
      cursor = data?.nextCursor || null;
      paint();
    } catch (failure) {
      if (mine !== token) return;
      listSlot.replaceChildren(errorBox(failure, () => reload()));
      cursor = null;
    } finally {
      if (mine === token) {
        busy = false;
        more.disabled = false;
        more.hidden = !cursor;
      }
    }
  }

  function paint() {
    listSlot.replaceChildren();
    if (!items.length) {
      listSlot.append(empty('Bu aralıkta takvim öğesi yok', 'Aralığı ya da türleri değiştirin.'));
      return;
    }
    const days = new Map();
    for (const item of items) {
      if (!days.has(item.date)) days.set(item.date, []);
      days.get(item.date).push(item);
    }
    for (const date of [...days.keys()].sort()) {
      const group = h('div', 'ak-sl-agenda-day');
      const head = h('div', 'ak-sl-agenda-head');
      head.append(h('b', '', `${DAY_LABEL[weekdayOf(date)]} ${fmtDate(date)}`));
      if (holidays.has(date)) head.append(badge('Tatil', 'warn'));
      if (date === todayIst()) head.append(badge('Bugün', 'info'));
      group.append(head);
      const rows = days.get(date).sort((a, b) => (a.allDay === b.allDay ? String(a.startsAt || '').localeCompare(String(b.startsAt || ''))
        : a.allDay ? -1 : 1));
      for (const item of rows) group.append(agendaRow(item));
      listSlot.append(group);
    }
  }

  function agendaRow(item) {
    const row = h('div', `ak-sl-agenda-row${item.status === 'cancelled' ? ' cancelled' : ''}`);
    const time = item.allDay ? 'Tüm gün' : `${fmtTime(item.startsAt)}${item.endsAt ? `–${fmtTime(item.endsAt)}` : ''}`;
    row.append(h('span', 'ak-sl-agenda-time', time));
    const kind = item.type === 'institution_event' && item.institutionEventKind
      ? EVENT_KINDS[item.institutionEventKind] || AGENDA_TYPES[item.type] : AGENDA_TYPES[item.type] || item.type;
    row.append(badge(kind, item.type === 'exam' ? 'warn' : item.type === 'institution_event' ? 'info' : ''));
    row.append(stack(item.title, [item.subtitle, item.subject?.name, item.location,
      item.endDate && item.endDate !== item.date ? `${fmtDate(item.endDate)} tarihine kadar` : ''].filter(Boolean).join(' · ')));
    row.append(h('small', 'ak-muted', item.status ? agendaStatus(item) : ''));
    return row;
  }

  function agendaStatus(item) {
    if (item.type === 'plan_task') return TASK_STATUS[item.status]?.[0] || item.status;
    return { scheduled: 'Planlı', cancelled: 'İptal', changed: 'Değişti', pending: 'Bekliyor', completed: 'Teslim edildi',
      overdue: 'Gecikti', approved: 'Onaylı' }[item.status] || item.status;
  }

  function renderCountdown(data) {
    if (!data?.examTarget && !data?.nextInstitutionExam) {
      return h('div', 'ak-muted ak-card-pad', 'Öğrencinin hedef sınavı KM\'de tanımlı değil ve planlı kurum denemesi yok.');
    }
    const official = data.official;
    const next = data.nextInstitutionExam;
    const tiles = [];
    if (data.examTarget) {
      tiles.push({ label: `${data.examTarget} geri sayımı`, value: data.daysLeft === null || data.daysLeft === undefined
        ? 'Tarih yok' : `${num(data.daysLeft)} gün`, tone: data.daysLeft !== null && data.daysLeft <= 30 ? 'warn' : '' });
      tiles.push({ label: 'Resmî tarih', value: official ? fmtDate(official.date) : 'Açıklanmadı',
        title: official && !official.confirmed ? 'Tarih henüz kesinleşmedi.' : '' });
    }
    tiles.push({ label: 'Sonraki kurum denemesi', value: next ? `${num(next.daysLeft)} gün` : 'Planlı deneme yok',
      title: next ? `${next.exam?.name} · ${fmtDate(next.exam?.date)}` : '' });
    const nodes = [kpiRow(tiles)];
    const details = [];
    if (official) {
      details.push(['Resmî sınav', `${official.name}${official.confirmed ? '' : ' (tarih kesinleşmedi)'}`]);
      if ((official.sessions || []).length) {
        details.push(['Oturumlar', official.sessions.map((item) => `${item.name} · ${fmtDateTime(item.startsAt)}`).join(' / ')]);
      }
    }
    if (next) details.push(['Kurum denemesi', `${next.exam?.name || '—'} · ${fmtDate(next.exam?.date)}`]);
    if (details.length) nodes.push(kv(details));
    return nodes;
  }

  return {
    node,
    reload: () => Promise.all([countdown.reload(), reload()]),
    destroy() { token += 1; },
  };
}

// ------------------------------------------------------- gizlilik ve depolama

function privacyTab(app, studentId) {
  const node = h('div', 'ak-grid-2');
  const privacy = loader(() => app.api.get(`/v1/students/${studentId}/learning-privacy`), (data) => {
    const nodes = [];
    if (!data.tenantAllowsGuardianMistakeImages) {
      nodes.push(alertBox('Kurum ayarı (guardian_mistake_images) kapalı: öğrenci paylaşsa da hata görselleri veliye gitmez.', 'info'));
    }
    nodes.push(kv([
      ['Hata görselleri veliye', IMAGE_SHARE[data.guardianMistakeImages] || data.guardianMistakeImages],
      ['Kurum izni', data.tenantAllowsGuardianMistakeImages ? 'Açık' : 'Kapalı'],
      ['Öz değerlendirmeler veliye', data.guardianSelfReviews ? 'Açık — veli görür' : 'Kapalı — yalnız koç görür'],
      ['Son değişiklik', fmtDateTime(data.updatedAt)],
    ]));
    nodes.push(hintBox('Bu tercihler öğrencinindir ve yalnız VELİ görünürlüğünü belirler; panelden değiştirilmez. '
      + 'Kurum personeli hata görsellerini görür. Kişisel ajanda etkinlikleri ve yapılacaklar kuruma hiç gösterilmez. '
      + 'Süzme Akademi sunucusunda yapılır.'));
    return box(...nodes);
  }, { rows: 4, columns: 2 });

  const canStorage = app.can('students_getStorageUsage');
  const storage = loader(() => app.api.get(`/v1/students/${studentId}/storage`), (data) => {
    const ratio = data.quotaBytes ? (data.usedBytes / data.quotaBytes) * 100 : 0;
    const bar = h('div', 'ak-sl-quota');
    const fill = h('div', `ak-sl-quota-fill${ratio >= 90 ? ' bad' : ratio >= 75 ? ' warn' : ''}`);
    fill.style.width = `${Math.min(100, Math.max(0, ratio))}%`;
    bar.append(fill);
    const table = dataTable({
      columns: [
        { key: 'kind', label: 'Tür', width: 'minmax(0, 2fr)', cell: (row) => FILE_KINDS[row.kind] || row.kind },
        { key: 'count', label: 'Dosya', align: 'num', cell: (row) => num(row.fileCount) },
        { key: 'size', label: 'Boyut', align: 'num', cell: (row) => bytes(row.usedBytes) },
      ],
      rows: data.byKind || [],
      rowKey: (row) => row.kind,
      dense: true,
      empty: empty('Dosya yok'),
    });
    return box(
      h('div', 'ak-sl-note', `${bytes(data.usedBytes)} / ${bytes(data.quotaBytes)} · ${percent(ratio, 0)} dolu · ${num(data.fileCount)} dosya`),
      bar, table.node);
  }, { rows: 4, columns: 3 });

  node.append(...[card('Veliyle paylaşım tercihleri', privacy.node),
    canStorage ? card('Dosya kotası', storage.node) : null].filter(Boolean));
  return {
    node,
    reload: () => Promise.all([privacy.reload(), canStorage ? storage.reload() : null]),
  };
}
