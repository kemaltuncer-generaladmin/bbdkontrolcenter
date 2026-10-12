// BBD Akademi — Değerlendirmeler (F-A21).
//
// Üç sekme: Değerlendirmeler (liste + süzgeç) · Dönemler (dönem aç, tamamlama
// durumu, öğrenci satırından "Değerlendir") · Şablonlar (bölüm + kriter +
// ölçek düzenleyicisi). Değerlendirme formu çekmecededir ve sağında otomatik
// veriler durur (evaluations-form.js). PDF gelişim karnesi 202 işidir;
// `exportJob` yoklar ve indirir.
//
// Parçalar: evaluations-shared.js (etiket, hücre, bağlam + olay kanalı),
// evaluations-list.js, evaluations-periods.js, evaluations-templates.js,
// evaluations-form.js. Sekmeler ilk açıldıklarında kurulur ve veri çeker.
//
// Bağlantılar (`app.go('evaluations', payload)`):
//   {evaluationId}            → o değerlendirme açılır
//   {studentId, periodId?}    → o öğrenci için yeni değerlendirme (öğrenci kartı)
//   {periodId}                → dönem ayrıntısı
//   {tab: 'evaluations'|'periods'|'templates'}

import { h, loadStyles } from '../../ui-kit/kit.js';
import { emptyState, tabBar } from '../../ui-kit/layout.js';
import { actionButton, sectionHead } from './common.js';
import { createContext, exportReports } from './evaluations-shared.js';
import { evaluationsTab } from './evaluations-list.js';
import { periodsTab } from './evaluations-periods.js';
import { templatesTab } from './evaluations-templates.js';
import { openEvaluation, startEvaluation } from './evaluations-form.js';

const TABS = [
  { key: 'evaluations', label: 'Değerlendirmeler', op: 'admin_listEvaluations', build: evaluationsTab },
  { key: 'periods', label: 'Dönemler', op: 'admin_listEvaluationPeriods', build: periodsTab },
  { key: 'templates', label: 'Şablonlar', op: 'admin_listEvaluationTemplates', build: templatesTab },
];

export function section(app) {
  loadStyles(import.meta.url, 'evaluations.css');
  const node = h('div', 'ak-stack ak-ev');
  const ctx = createContext(app);
  const views = new Map();
  const visible = TABS.filter((tab) => app.can(tab.op));

  ctx.openEvaluation = (evaluationId) => openEvaluation(ctx, evaluationId);
  ctx.createEvaluation = (options = {}) => startEvaluation(ctx, options);
  ctx.goTab = (key) => select(key);
  ctx.openPeriod = (periodId) => {
    select('periods');
    views.get('periods')?.open(periodId);
  };

  node.append(sectionHead('Değerlendirmeler', {
    subtitle: 'Dönemsel öğrenci değerlendirmesi: şablonlar, dönemler, form, paylaşım ve gelişim karnesi.',
    actions: [
      actionButton(app, 'admin_exportEvaluationReports', 'Gelişim karnesi (PDF)', {
        onClick: () => exportReports(ctx) }),
      actionButton(app, 'admin_createEvaluation', 'Yeni değerlendirme', { variant: 'primary',
        onClick: () => startEvaluation(ctx) }),
    ],
  }));

  if (!visible.length) {
    node.append(emptyState({ title: 'Değerlendirme ekranlarını görme yetkiniz yok',
      text: 'Değerlendirme, dönem ya da şablon listesi için yöneticinizden izin isteyin.' }));
    return { node, shown() {}, destroy() {} };
  }

  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => show(key));
  const slot = h('div', 'ak-stack');
  node.append(tabs.node, slot);

  function view(key) {
    if (!views.has(key)) {
      const tab = visible.find((item) => item.key === key);
      if (!tab) return null;
      const instance = tab.build(ctx);
      views.set(key, instance);
      slot.append(instance.node);
    }
    return views.get(key);
  }

  function show(key) {
    const current = view(key);
    if (!current) return;
    for (const [name, instance] of views) instance.node.hidden = name !== key;
    current.show();
  }

  function select(key) {
    if (!visible.some((tab) => tab.key === key)) return false;
    tabs.select(key, false);
    show(key);
    return true;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && visible.some((tab) => tab.key === payload.tab) ? payload.tab
        : payload?.periodId && !payload?.studentId ? 'periods' : null;
      if (!started) {
        started = true;
        select(wanted && visible.some((tab) => tab.key === wanted) ? wanted : visible[0].key);
      } else if (wanted) {
        select(wanted);
      }
      if (!payload) return;
      if (payload.evaluationId) {
        openEvaluation(ctx, payload.evaluationId);
      } else if (payload.studentId) {
        startEvaluation(ctx, { studentId: payload.studentId, periodId: payload.periodId || '' });
      } else if (payload.periodId) {
        if (select('periods')) views.get('periods')?.open(payload.periodId);
      }
    },
    destroy() {
      for (const instance of views.values()) {
        try { instance.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
      views.clear();
    },
  };
}
