// BBD Akademi — Değerlendirme bölümünün ortak etiketleri, hücreleri ve bağlamı.
//
// Bölüm üç sekmedir (evaluations.js): Değerlendirmeler · Dönemler · Şablonlar.
// Sekmeler aynı bağlamı paylaşır: sınıf / grup adları (önbellekli
// `app.lookups`), seçimlerde kullanılan dönem listesi, etkin şablonlar
// (ETag önbelleği) ve küçük bir olay kanalı. Değerlendirme çekmecesinde
// yapılan bir kayıt, yayın ya da silme bu kanaldan duyurulur; liste satırı
// yerinde güncellenir, dönem tamamlama sayısı tek istekle tazelenir (satır
// başına istek yok).

import { h } from '../../../ui-kit/kit.js';
import { badge } from '../../../ui-kit/layout.js';
import { exportJob, formDialog, num, percent } from '../common.js';

export const EVALUATION_STATUS = { draft: ['Taslak', 'warn'], published: ['Yayımlandı', 'good'] };
export const PERIOD_STATUS = { scheduled: ['Planlandı', 'info'], open: ['Açık', 'good'], closed: ['Kapandı', 'dim'] };
export const PROGRESS_STATUS = { not_started: ['Başlanmadı', 'dim'], draft: ['Taslak', 'warn'],
  published: ['Yayımlandı', 'good'] };
export const TEMPLATE_STATUS = { active: ['Etkin', 'good'], archived: ['Arşivde', 'dim'] };
export const SCALE_KINDS = { numeric_5: '1–5 puan', descriptive_4: 'Dört düzey (Geliştirilmeli → Çok iyi)' };
/** Şablonun kriterine bağlanabilecek otomatik veri (sözleşme `autoMetric`). */
export const AUTO_METRICS = { plan_adherence: 'Plan uyumu', questions: 'Çözülen soru', accuracy: 'Doğruluk',
  exam_trend: 'Deneme trendi', attendance: 'Devam', homework_rate: 'Ödev tamamlama', reading: 'Okuma',
  mistakes: 'Hata defteri', self_review: 'Öz değerlendirme' };
export const MISTAKE_REASONS = { knowledge_gap: 'Bilgi eksiği', misconception: 'Kavram yanılgısı',
  calculation_error: 'İşlem hatası', careless: 'Dikkatsizlik', misread_question: 'Soruyu yanlış okuma',
  time_shortage: 'Süre yetmedi', guess: 'Tahmin' };
export const READ_KIND = { student: 'Öğrenci', guardian: 'Veli' };
/** Önceki döneme göre kriter değişimi: [işaret, ton, açıklama]. */
export const CHANGE_MARK = { up: ['▲', 'good', 'Önceki değerlendirmeye göre yükseldi'],
  down: ['▼', 'bad', 'Önceki değerlendirmeye göre düştü'], same: ['=', 'dim', 'Önceki değerlendirmeyle aynı'] };
export const REPORT_LAYOUTS = { combined: 'Tek PDF (bütün öğrenciler art arda)',
  one_per_student: 'Öğrenci başına ayrı PDF' };

/** Ortalama → "3,75" (ölçek puanı; yoksa —). */
export function averageText(value) {
  return value === null || value === undefined ? '—' : num(value, 2);
}

/** "Öğrenci ve veli" / "Yalnız öğrenci" / "Kurum içinde". */
export function shareText(share) {
  if (share?.student && share?.guardians) return 'Öğrenci ve veliler';
  if (share?.student) return 'Yalnız öğrenci';
  if (share?.guardians) return 'Yalnız veliler';
  return 'Kurum içinde (paylaşılmadı)';
}

/** Ölçek özeti: "1–5 puan" ya da düzey adları. */
export function scaleText(scale) {
  if (!scale) return '—';
  if (scale.kind === 'descriptive_4' && Array.isArray(scale.labels) && scale.labels.length) {
    return scale.labels.map((item) => item.label).join(' · ');
  }
  return SCALE_KINDS[scale.kind] || scale.kind || '—';
}

export function studentCell(student) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('b', '', student?.displayName || '—'),
    h('small', '', [student?.externalRef, student?.classGroup?.name].filter(Boolean).join(' · ')));
  return cell;
}

export function stackCell(main, sub) {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', main || '—'));
  if (sub) cell.append(h('small', '', sub));
  return cell;
}

/** Dönem tamamlama: yayımlanan / öğrenci + taslak rozeti + şerit. */
export function progressCell(progress) {
  const total = progress?.studentCount || 0;
  const published = progress?.published || 0;
  const share = total ? Math.min(1, published / total) : 0;
  const cell = h('div', 'ak-ev-progress');
  const line = h('div', 'ak-inline ak-ev-progress-line');
  line.append(h('span', 'ak-num', total ? `${num(published)}/${num(total)}` : '—'));
  if (total) line.append(h('span', 'ak-muted', percent(progress?.percent ?? share * 100, 0)));
  if (progress?.draft) line.append(badge(`${num(progress.draft)} taslak`, 'warn'));
  const bar = h('span', 'ak-ev-bar');
  const fill = h('span', 'ak-ev-fill');
  fill.style.width = `${Math.round(share * 100)}%`;
  bar.append(fill);
  cell.append(line, bar);
  cell.title = total ? `${num(published)} yayımlandı · ${num(progress?.draft || 0)} taslak · `
    + `${num(progress?.notStarted || 0)} başlanmadı` : 'Hedefte öğrenci yok';
  return cell;
}

/**
 * Hedef özeti. Sınıf / grup adları önbellekli listelerden; öğrenci adı yükte
 * yoktur, sayısı yazılır. Hedef boşsa Akademi kurumun bütün aktif öğrencilerini alır.
 */
export function targetText(target, names, { full = false } = {}) {
  const classes = target?.classGroupIds || [];
  const groups = target?.studentGroupIds || [];
  const students = target?.studentIds || [];
  const parts = [];
  if (classes.length) {
    parts.push(full || classes.length <= 3
      ? classes.map((id) => names.classes.get(id) || 'Sınıf').join(', ') : `${num(classes.length)} sınıf`);
  }
  if (groups.length) {
    parts.push(full || groups.length <= 2
      ? groups.map((id) => names.groups.get(id) || 'Grup').join(', ') : `${num(groups.length)} grup`);
  }
  if (students.length) parts.push(`${num(students.length)} öğrenci`);
  return parts.join(' · ') || 'Bütün aktif öğrenciler';
}

/** Küçük simge düğmesi (↑ ↓ ✕). */
export function iconButton(text, title, onClick, disabled = false) {
  const node = h('button', 'kit-btn kit-btn-ghost ak-ev-icon', text);
  node.type = 'button';
  node.title = title;
  node.setAttribute('aria-label', title);
  node.disabled = Boolean(disabled);
  node.addEventListener('click', onClick);
  return node;
}

export function removableChip(text, onRemove, title = 'Kaldır') {
  const chip = h('button', 'kit-chip on', `${text} ✕`);
  chip.type = 'button';
  chip.title = title;
  chip.addEventListener('click', onRemove);
  return chip;
}

/** Onay kutusu + etiket (+ isteğe bağlı açıklama). */
export function checkLine(text, checked, onChange, { hint = '', disabled = false } = {}) {
  const node = h('label', 'ak-ev-check');
  const input = h('input', 'kit-check');
  input.type = 'checkbox';
  input.checked = Boolean(checked);
  input.disabled = Boolean(disabled);
  input.addEventListener('change', () => onChange?.(input.checked));
  const text_ = h('span', 'ak-ev-check-text');
  text_.append(h('span', '', text));
  if (hint) text_.append(h('small', 'ak-muted', hint));
  node.append(input, text_);
  return { node, get value() { return input.checked; }, set(value) { input.checked = Boolean(value); } };
}

/**
 * Tablo satırındaki düğme(ler): tıklama satırın `onRow`una taşmasın diye
 * sarmalayıcı yayılımı durdurur (düğmenin kendi işi önce çalışır).
 */
export function rowActions(...nodes) {
  const wrap = h('div', 'ak-row-actions');
  wrap.append(...nodes.filter(Boolean));
  wrap.addEventListener('click', (event) => event.stopPropagation());
  return wrap;
}

/** Hata ayrıntısından bir alanı okur (`details` nesne ya da dizi olabilir). */
export function detailOf(failure, key) {
  const details = failure?.details;
  if (!details) return null;
  if (Array.isArray(details)) return details.find((item) => item && item[key])?.[key] || null;
  return details[key] || null;
}

/** Dosya adı için sade metin: "Ekim değerlendirmesi" → "ekim-degerlendirmesi". */
export function slug(text) {
  const map = { ç: 'c', ğ: 'g', ı: 'i', i: 'i', ö: 'o', ş: 's', ü: 'u' };
  return String(text || '').toLocaleLowerCase('tr-TR').replace(/[çğıöşü]/g, (ch) => map[ch] || ch)
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 60) || 'karne';
}

/**
 * Sekmelerin paylaştığı bağlam. `openEvaluation` / `createEvaluation` /
 * `openPeriod` / `goTab` bölüm dosyasında bağlanır.
 */
export function createContext(app) {
  const listeners = new Set();
  let periodItems = null;
  let periodLoad = null;
  let periodSeq = 0;
  const ctx = {
    app,
    names: { classes: new Map(), groups: new Map() },
    namesReady: null,
    on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
    /** {type: 'evaluation'|'period'|'template'|'names', kind, evaluation?, period?, removedId?} */
    emit(event) {
      for (const fn of [...listeners]) {
        try { fn(event); } catch (failure) { console.error('[bbd_akademi] değerlendirme olayı', failure); }
      }
    },
    /** Sınıf ve çalışma grubu adları — bir kez, önbellekten. */
    loadNames() {
      if (!ctx.namesReady) {
        ctx.namesReady = Promise.all([
          app.lookups.classGroups().catch(() => []),
          app.lookups.studentGroups().catch(() => []),
        ]).then(([classes, groups]) => {
          for (const item of classes) ctx.names.classes.set(item.id, item.name);
          for (const item of groups) ctx.names.groups.set(item.id, item.name);
          ctx.classList = classes;
          ctx.groupList = groups;
          ctx.emit({ type: 'names' });
          return ctx.names;
        });
      }
      return ctx.namesReady;
    },
    classList: [],
    groupList: [],
    /**
     * Seçimlerde kullanılan dönemler (bütün durumlar). Küçük, sınırlı küme;
     * dönem yazıldıktan sonra `force` ile tazelenir. Liste izni yoksa boş.
     */
    async periods(force = false) {
      if (!app.can('admin_listEvaluationPeriods')) return [];
      if (force) { periodItems = null; periodLoad = null; }
      if (periodItems) return periodItems;
      if (!periodLoad) {
        const mine = ++periodSeq;
        const run = app.api.all('/v1/admin/evaluation-periods', { status: 'all' }, { limit: 100, max: 500 })
          .then((items) => { if (mine === periodSeq) periodItems = items; return items; })
          .finally(() => { if (periodLoad === run) periodLoad = null; });
        periodLoad = run;
      }
      return periodLoad;
    },
    /** Dönem seçenekleri: açıklar önce, sonra planlanan, sonra kapananlar. */
    async periodOptions({ empty, onlyOpen = false } = {}) {
      const order = { open: 0, scheduled: 1, closed: 2 };
      const items = (await ctx.periods().catch(() => []))
        .filter((item) => !onlyOpen || item.status === 'open')
        .sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3)
          || String(b.startDate).localeCompare(String(a.startDate)));
      const list = items.map((item) => ({ value: item.id,
        label: `${item.name}${item.status === 'open' ? '' : ` (${PERIOD_STATUS[item.status]?.[0] || item.status})`}` }));
      return empty !== undefined ? [{ value: '', label: empty }, ...list] : list;
    },
    /**
     * Etkin şablonlar — seyrek değişir: ETag önbelleği (5 dk taze). Şablon
     * yazıldıktan sonra `invalidateTemplates()`.
     */
    async templates() {
      if (!app.can('admin_listEvaluationTemplates')) return [];
      const data = await app.api.getCached('/v1/admin/evaluation-templates', { status: 'active', limit: 100 },
        { ttlMs: 5 * 60_000 });
      return Array.isArray(data?.items) ? data.items : [];
    },
    invalidateTemplates() { app.api.invalidate('/v1/admin/evaluation-templates'); },
  };
  return ctx;
}

/**
 * PDF gelişim karnesi (F-A21, yalnız yayımlanmış değerlendirmeler). Önce
 * kapsam penceresi, sonra 202 işi `exportJob` ile yoklanıp indirilir.
 * `fixed`: {periodId?, periodName?, classGroupId?, studentIds?, studentName?}
 */
export async function exportReports(ctx, fixed = {}) {
  const { app } = ctx;
  await ctx.loadNames();
  const periodOptions = fixed.periodId ? [] : await ctx.periodOptions({ empty: 'Dönem seçilmedi' });
  const classOptions = [{ value: '', label: 'Bütün sınıflar' },
    ...ctx.classList.map((item) => ({ value: item.id, label: item.name }))];
  const single = Array.isArray(fixed.studentIds) && fixed.studentIds.length > 0;
  const picked = await formDialog(app, {
    title: 'Gelişim karnesi (PDF)',
    description: 'Karneye yalnız YAYIMLANMIŞ değerlendirmeler girer. Dosya arka planda hazırlanır; '
      + 'hazır olunca açılır.',
    value: { periodId: fixed.periodId || '', classGroupId: fixed.classGroupId || '', layout: 'combined',
      includeAutoMetrics: true, includeCoachNotes: false,
      scope: [fixed.periodName && `Dönem: ${fixed.periodName}`, fixed.studentName && `Öğrenci: ${fixed.studentName}`]
        .filter(Boolean).join(' · ') },
    fields: [
      (fixed.periodName || fixed.studentName) && { key: 'scope', label: 'Kapsam', type: 'static', wide: true },
      !fixed.periodId && { key: 'periodId', label: 'Dönem', type: 'select', options: periodOptions,
        hint: 'Dönem seçilmezse öğrencinin en son yayımlanan değerlendirmesi kullanılır.' },
      !single && { key: 'classGroupId', label: 'Sınıf', type: 'select', options: classOptions },
      !single && { key: 'layout', label: 'Dosya düzeni', type: 'select',
        options: Object.entries(REPORT_LAYOUTS).map(([value, label]) => ({ value, label })) },
      { key: 'includeAutoMetrics', label: 'Otomatik verileri ekle (plan uyumu, soru, deneme, devam, ödev)',
        type: 'checkbox', wide: true },
      { key: 'includeCoachNotes', label: 'Koç görüşme notlarını ekle', type: 'checkbox', wide: true },
    ],
    submitLabel: 'Karneyi hazırla',
    onSubmit: (draft, { key }) => {
      if (!single && !draft.periodId && !draft.classGroupId) {
        throw new Error('Bir dönem ya da sınıf seçin (bütün kurumun karnesi tek seferde üretilmez).');
      }
      return { draft, key };
    },
  });
  if (!picked) return null;
  const { draft, key } = picked;
  const body = {
    periodId: draft.periodId || fixed.periodId || null,
    classGroupId: single ? null : (draft.classGroupId || null),
    includeAutoMetrics: Boolean(draft.includeAutoMetrics),
    includeCoachNotes: Boolean(draft.includeCoachNotes),
    layout: single ? 'combined' : (draft.layout || 'combined'),
  };
  if (single) body.studentIds = fixed.studentIds;
  const nameParts = ['gelisim-karnesi', fixed.periodName || '', fixed.studentName || '',
    !single && draft.classGroupId ? ctx.names.classes.get(draft.classGroupId) || '' : ''];
  return exportJob(app, () => app.api.post('/v1/admin/evaluation-reports', body, { key }),
    { name: slug(nameParts.filter(Boolean).join(' ')), label: 'Gelişim karnesi hazırlanıyor…' });
}
