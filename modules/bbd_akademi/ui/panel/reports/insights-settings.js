// BBD Akademi — Erken uyarı › Eşikler (F-A22, yalnız KM).
//
// Yedi kuralın her biri: açık/kapalı, önem, eşik ve kurala göre pencere
// (gün) ya da art arda sayısı. Ayar seyrek değişir: ETag'li önbellekle
// okunur (`getCached`), kayıttan sonra önbellek düşürülür (`invalidate`)
// ve ekran sunucunun döndürdüğü kayıtla yeniden çizilir.
//
// Bayrak (`early_warning`) kapalıyken de eşikler kaydedilebilir; ekran
// bunu söyler. Yeni eşikler bir sonraki saatlik hesaplamada uygulanır.
//
// Sunucu bir kuralı hiç döndürmediyse satır "tanımsız" çizilir ve yalnız
// açılırsa kayda eklenir (ilk değerler sözleşmedeki varsayılanlardır).

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../../ui-kit/layout.js';
import {
  actionButton, fmtDateTime, loader, optionsOf, runAction, selectBox,
} from '../common.js';
import { RULES, SEVERITY, SEVERITY_ORDER } from './insights-shared.js';

/** Kurala göre ek alanlar ve sınırlar. */
const META = {
  no_activity: { cutoff: 3, min: 1, max: 60 },
  streak_broken: { cutoff: 7, min: 1, max: 365 },
  low_plan_adherence: { cutoff: 50, min: 0, max: 100, consecutive: 2, consecutiveUnit: 'hafta' },
  exam_net_drop: { cutoff: 0, min: 0, max: 200, step: 0.25, consecutive: 2, consecutiveUnit: 'deneme' },
  absence: { cutoff: 3, min: 1, max: 120, windowDays: 30 },
  homework_overdue: { cutoff: 3, min: 1, max: 100 },
  low_motivation: { cutoff: 2, min: 1, max: 5 },
};

const SEVERITY_OPTIONS = optionsOf(Object.fromEntries(SEVERITY_ORDER.map((key) => [key, SEVERITY[key]])));

export function settingsTab(app) {
  const node = h('div', 'ak-stack');
  const view = loader(() => app.api.getCached('/v1/admin/early-warning-settings', undefined,
    { ttlMs: 5 * 60_000 }), (data) => paint(data), { rows: 7, columns: 5 });
  node.append(view.node);

  function paint(data) {
    const editable = app.can('admin_putEarlyWarningSettings');
    const given = new Map((data.rules || []).map((rule) => [rule.rule, rule]));
    const rows = Object.keys(RULES).map((key) => ruleRow(key, given.get(key), editable));

    const nodes = [];
    if (!data.enabled) {
      const box = h('div', 'ak-stack');
      box.append(alertBox('Erken uyarı özelliği kurumda kapalı. Eşikler kaydedilebilir; uyarılar özellik '
        + 'açılınca bu eşiklerle üretilir.', 'warn'));
      if (app.can('admin_listFeatures')) {
        box.append(button('Özelliklere git', { variant: 'ghost',
          onClick: () => app.go('settings', { tab: 'features' }) }));
      }
      nodes.push(box);
    }

    const grid = h('div', 'ak-in-rules');
    const head = h('div', 'ak-in-rule ak-in-rule-head');
    for (const label of ['Kural', 'Açık', 'Önem', 'Eşik', 'Pencere', 'Art arda']) head.append(h('span', '', label));
    grid.append(head);
    for (const row of rows) grid.append(row.node);

    const notify = h('input', 'kit-check');
    notify.type = 'checkbox';
    notify.checked = Boolean(data.notifyStaff);
    notify.disabled = !editable;
    const notifyRow = h('label', 'ak-inline ak-in-notify');
    notifyRow.append(notify, h('span', '', 'Yeni YÜKSEK önemli uyarıda, uyarıları görme yetkisi olan personele '
      + 'uygulama içi bildirim gönder'));

    const error = h('div', 'kit-field-error');
    const save = actionButton(app, 'admin_putEarlyWarningSettings', 'Eşikleri kaydet', { variant: 'primary',
      onClick: async () => {
        error.textContent = '';
        const invalid = rows.map((row) => row.validate()).some(Boolean);
        if (invalid) { error.textContent = 'İşaretli eşikleri düzeltin.'; return; }
        const body = {
          rules: rows.filter((row) => row.included()).map((row) => row.value()),
          notifyStaff: notify.checked,
        };
        if (!body.rules.length) { error.textContent = 'En az bir kural tanımlı olmalı.'; return; }
        const result = await runAction(app, () => app.api.put('/v1/admin/early-warning-settings', body),
          { success: 'Eşikler kaydedildi; bir sonraki saatlik hesaplamada uygulanır.', button: save });
        if (!result) return;
        app.api.invalidate('/v1/admin/early-warning-settings');
        view.node.replaceChildren(...paint(result));
      } });
    const undo = button('Değişiklikleri geri al', { variant: 'ghost',
      onClick: () => view.node.replaceChildren(...paint(data)) });

    const body = h('div', 'ak-card-pad ak-stack');
    body.append(grid, notifyRow, h('div', 'ak-muted ak-in-note', 'Yeni eşikler bir sonraki saatlik hesaplamada '
      + 'uygulanır. Artık tetiklenmeyen açık uyarılar kendiliğinden çözülmez; listeden kapatın.'));
    const actions = h('div', 'ak-inline');
    if (editable) actions.append(save, undo, error);
    else actions.append(save, h('span', 'ak-muted', 'Eşikleri yalnız görüntüleyebilirsiniz.'));
    body.append(actions);
    nodes.push(card('Kural eşikleri', body, data.updatedAt ? `Son değişiklik ${fmtDateTime(data.updatedAt)}` : ''));
    return nodes;
  }

  return { node, reload: view.reload, destroy() {} };
}

/** Tek kural satırı: girişler + doğrulama + gövde. */
function ruleRow(key, given, editable) {
  const meta = META[key] || {};
  const [name, unit, description] = RULES[key];
  const present = Boolean(given);
  const start = given || { rule: key, enabled: false, severity: 'medium', cutoff: meta.cutoff ?? null,
    windowDays: meta.windowDays ?? null, consecutive: meta.consecutive ?? null };

  const node = h('div', `ak-in-rule${present ? '' : ' missing'}`);
  const text = h('div', 'ak-cell-stack');
  text.append(h('b', '', name), h('small', '', description));
  if (!present) text.append(badge('Tanımsız — açarsanız eklenir', 'dim'));
  const error = h('small', 'ak-in-err');
  text.append(error);

  const enabled = h('input', 'kit-check');
  enabled.type = 'checkbox';
  enabled.checked = Boolean(start.enabled);
  enabled.setAttribute('aria-label', `${name} açık`);
  const severity = selectBox(SEVERITY_OPTIONS, start.severity, null, { label: `${name} önemi` });

  const numberInput = (value, label, { min, max, step = 1 } = {}) => {
    const input = h('input', 'kit-input ak-in-num');
    input.type = 'number';
    if (min !== undefined) input.min = String(min);
    if (max !== undefined) input.max = String(max);
    input.step = String(step);
    input.value = value === null || value === undefined ? '' : String(value);
    input.setAttribute('aria-label', label);
    return input;
  };
  const withUnit = (input, suffix) => {
    const wrap = h('span', 'ak-in-unit');
    if (suffix === '%') wrap.append(h('span', '', '%'), input);
    else wrap.append(input, h('span', '', suffix));
    return wrap;
  };

  const cutoff = numberInput(start.cutoff, `${name} eşiği`, meta);
  const hasWindow = meta.windowDays !== undefined || (start.windowDays !== null && start.windowDays !== undefined);
  const hasConsecutive = meta.consecutive !== undefined
    || (start.consecutive !== null && start.consecutive !== undefined);
  const windowDays = hasWindow ? numberInput(start.windowDays, `${name} penceresi`, { min: 1, max: 120 }) : null;
  const consecutive = hasConsecutive
    ? numberInput(start.consecutive, `${name} art arda`, { min: 1, max: 10 }) : null;

  for (const control of [enabled, severity.node, cutoff, windowDays, consecutive]) {
    if (control) control.disabled = !editable;
  }

  node.append(text, enabled, severity.node, withUnit(cutoff, unit),
    windowDays ? withUnit(windowDays, 'gün') : h('span', 'ak-muted', '—'),
    consecutive ? withUnit(consecutive, meta.consecutiveUnit || 'kez') : h('span', 'ak-muted', '—'));

  const read = (input) => {
    if (!input) return null;
    const raw = String(input.value || '').trim().replace(',', '.');
    return raw === '' ? null : Number(raw);
  };

  return {
    node,
    included: () => present || enabled.checked,
    validate() {
      error.textContent = '';
      const bad = (message) => { error.textContent = message; return true; };
      const value = read(cutoff);
      if (enabled.checked && value === null) return bad('Açık kuralın eşiği boş olamaz.');
      if (value !== null && (Number.isNaN(value) || (meta.min !== undefined && value < meta.min)
        || (meta.max !== undefined && value > meta.max))) {
        return bad(`Eşik ${meta.min ?? 0}–${meta.max ?? '∞'} arasında olmalı.`);
      }
      const span = read(windowDays);
      if (span !== null && (!Number.isInteger(span) || span < 1 || span > 120)) {
        return bad('Pencere 1–120 gün arasında tam sayı olmalı.');
      }
      const times = read(consecutive);
      if (times !== null && (!Number.isInteger(times) || times < 1 || times > 10)) {
        return bad('Art arda sayısı 1–10 arasında tam sayı olmalı.');
      }
      return false;
    },
    value: () => ({
      rule: key,
      enabled: enabled.checked,
      severity: severity.value || 'medium',
      cutoff: read(cutoff),
      windowDays: windowDays ? read(windowDays) : (start.windowDays ?? null),
      consecutive: consecutive ? read(consecutive) : (start.consecutive ?? null),
    }),
  };
}
