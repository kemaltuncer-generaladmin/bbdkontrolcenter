// Filtre şeridi — 20 ekranda aynı dil.
//
// Tek kopya (ADR 0011). Amaç tutarlılık: arama kutusu her ekranda solda,
// açılır süzgeçler ortada, eylemler sağda; Türkçe arama her yerde aksansız
// (`foldText`) eşleşir; "Temizle" her ekranda aynı yerde durur.
//
// Bileşen VERİ SÜZMEZ, yalnız DEĞER TAŞIR. Süzme kararını panel verir:
// küçük listelerde `applyFilters()` ile istemcide, büyük listelerde
// (1.419 ürün, denetim kayıtları) değerleri sunucuya göndererek.

import { button, debounce, foldText, h, parseMoney } from './kit.js';
import { dateRange } from './datefield.js';

/**
 * @param {object} spec
 * @param {Array} spec.fields — her biri:
 *   {kind:'search',    key, placeholder?, width?}
 *   {kind:'select',    key, label?, options:[{value,label}], value?}
 *   {kind:'dateRange', key, label?, start?, end?, presets?}
 *   {kind:'numRange',  key, label?, money?}   // money:true → kuruşa çevirir
 *   {kind:'toggle',    key, label}            // anahtar süzgeç
 * @param {(values:object)=>void} spec.onChange
 * @param {number} [spec.debounceMs=260] — yalnız metin alanları için
 * @param {Array<Node>} [spec.actions] — sağa yaslanan düğmeler
 */
export function filterBar({ fields = [], onChange, debounceMs = 260, actions = [] } = {}) {
  const node = h('div', 'kit-filters');
  node.setAttribute('role', 'search');

  const values = {};
  const controls = new Map();
  const disposers = [];

  const fire = () => onChange?.(snapshot());
  const fireSoon = debounce(fire, debounceMs);
  disposers.push(() => fireSoon.cancel());

  const snapshot = () => JSON.parse(JSON.stringify(values));

  for (const field of fields) {
    // YANLIŞ (null/undefined) ALAN ATLANIR — ve atlanan alan `values` içine de
    // GİRMEZ. Tek seçenekli süzgeç kutusu çizilmez (`choice.js`); değeri boş
    // bırakmak yerine hiç anahtar üretmemek bilinçlidir: tek kanallı mağazada
    // `channel=default` göndermek listeyi sessizce boşaltabiliyor (ölçüldü,
    // bkz. store_api `_drop_channel`). Yani süzgeç yalnız görünmez olmaz,
    // ISTEKTEN DE ÇIKAR.
    if (!field) continue;
    switch (field.kind) {
      case 'search': {
        values[field.key] = field.value || '';
        const input = h('input', 'kit-input');
        input.type = 'search';
        input.placeholder = field.placeholder || 'Ara';
        input.value = values[field.key];
        input.setAttribute('aria-label', field.placeholder || 'Ara');
        if (field.width) input.style.width = field.width;
        input.addEventListener('input', () => {
          values[field.key] = input.value;
          fireSoon();
        });
        controls.set(field.key, { set: (value) => { input.value = value ?? ''; } });
        node.append(input);
        break;
      }

      case 'select': {
        values[field.key] = field.value ?? '';
        const caption = field.label ? h('span', 'kit-filter-label', field.label) : null;
        if (caption) node.append(caption);
        const select = h('select', 'kit-select');
        select.setAttribute('aria-label', field.label || field.key);
        const fill = (options) => {
          select.replaceChildren();
          for (const option of options) {
            const item = h('option', undefined, option.label);
            item.value = String(option.value);
            select.append(item);
          }
          select.value = String(values[field.key] ?? '');
        };
        fill(field.options || []);
        select.addEventListener('change', () => {
          values[field.key] = select.value;
          fire();
        });
        /**
         * Kutuyu ekrandan kaldırır/geri getirir.
         *
         * NEDEN GİZLENEN KUTUNUN DEĞERİ DE SIFIRLANIR: seçenekler VERİDEN
         * SONRA geliyor ve tek seçenekli olduğu ancak o zaman anlaşılıyor.
         * Kutu gizlenip değer kalsaydı, kullanıcının göremediği bir süzgeç
         * listeyi süzmeye devam ederdi — "neden 3 kayıt görüyorum" sorusunun
         * ekranda hiçbir cevabı olmazdı.
         */
        const visible = (on) => {
          select.hidden = !on;
          if (caption) caption.hidden = !on;
          if (!on && values[field.key] !== '') {
            values[field.key] = '';
            select.value = '';
          }
        };
        if (field.hidden) visible(false);
        controls.set(field.key, {
          set: (value) => { select.value = String(value ?? ''); },
          // Seçenekler veriden sonra gelir (kanallar, müşteri grupları…).
          options: (options) => fill(options),
          visible,
        });
        node.append(select);
        break;
      }

      case 'dateRange': {
        const range = dateRange({
          start: field.start,
          end: field.end,
          label: field.label || 'Aralık',
          presets: field.presets,
          onChange: (next) => { values[field.key] = next; fire(); },
        });
        values[field.key] = range.get();
        controls.set(field.key, { set: (value) => range.set(value || {}) });
        disposers.push(() => range.destroy());
        node.append(range.node);
        break;
      }

      case 'numRange': {
        values[field.key] = { min: null, max: null };
        if (field.label) node.append(h('span', 'kit-filter-label', field.label));
        const row = h('div', 'kit-field-row');
        const make = (which, placeholder) => {
          const input = h('input', 'kit-input');
          input.type = 'text';
          input.inputMode = 'decimal';
          input.placeholder = placeholder;
          input.style.width = '92px';
          input.setAttribute('aria-label', `${field.label || field.key} ${placeholder}`);
          input.addEventListener('input', () => {
            const raw = input.value.trim();
            if (raw === '') {
              values[field.key][which] = null;
              input.classList.remove('bad');
            } else {
              const parsed = field.money ? parseMoney(raw) : Number(raw.replace(',', '.'));
              const ok = parsed !== null && !Number.isNaN(parsed);
              values[field.key][which] = ok ? parsed : null;
              input.classList.toggle('bad', !ok);
            }
            fireSoon();
          });
          return input;
        };
        const min = make('min', 'en az');
        const max = make('max', 'en çok');
        row.append(min, h('span', undefined, '–'), max);
        controls.set(field.key, {
          set: (value) => {
            min.value = value?.min ?? '';
            max.value = value?.max ?? '';
          },
        });
        node.append(row);
        break;
      }

      case 'toggle': {
        values[field.key] = Boolean(field.value);
        const wrap = h('label', 'kit-field-row');
        const check = h('input', 'kit-check');
        check.type = 'checkbox';
        check.checked = values[field.key];
        check.addEventListener('change', () => {
          values[field.key] = check.checked;
          fire();
        });
        wrap.append(check, h('span', 'kit-filter-label', field.label));
        controls.set(field.key, { set: (value) => { check.checked = Boolean(value); } });
        node.append(wrap);
        break;
      }

      default:
        break;
    }
  }

  const initial = snapshot();

  node.append(h('span', 'kit-spacer'));
  const clear = button('Temizle', {
    variant: 'ghost',
    title: 'Tüm süzgeçleri başlangıç durumuna al',
    onClick: () => reset(),
  });
  node.append(clear, ...actions);

  function reset() {
    for (const [key, value] of Object.entries(initial)) {
      values[key] = JSON.parse(JSON.stringify(value));
      controls.get(key)?.set(values[key]);
    }
    fire();
  }

  return {
    node,
    values: snapshot,
    /** Tek bir süzgecin değerini programla ayarlar (olay tetiklemez). */
    set(key, value) {
      values[key] = value;
      controls.get(key)?.set(value);
    },
    /** Açılır listenin seçeneklerini veriden sonra doldurur. */
    options(key, options) {
      controls.get(key)?.options?.(options);
    },
    /**
     * Açılır kutuyu gösterir/gizler — tek seçenekli süzgeç çizilmez.
     *
     * Karar `choice.js` içindedir (`applyChoiceFilter`); burası yalnız onu
     * uygular. Süzgeç şeridi veriden sonra kurulamıyor (panel açılır açılmaz
     * çizilmeli), o yüzden kutu önce çizilip sonra kaldırılır.
     */
    visible(key, on) {
      controls.get(key)?.visible?.(Boolean(on));
    },
    reset,
    /** Panel cleanup'ında ÇAĞRILMALI: tarih alanları global dinleyici tutuyor. */
    destroy() { disposers.forEach((fn) => fn()); },
  };
}

/**
 * İstemci tarafı süzme.
 *
 * `spec` her anahtarın satırdan hangi değeri okuyacağını söyler:
 *   {
 *     q:      {kind:'search', fields:['name','sku']},
 *     status: {kind:'equals', field:'status'},
 *     range:  {kind:'dateRange', field:'createdAt'},   // ISO gün ya da epoch-ms
 *     amount: {kind:'numRange', field:'total'},
 *     noImage:{kind:'toggle', test:(row)=>!row.imageUrl},
 *   }
 *
 * Boş/seçilmemiş süzgeç HİÇBİR ŞEYİ elemez — "Tümü" varsayılanı budur.
 */
export function applyFilters(rows, values, spec) {
  const tests = [];

  for (const [key, rule] of Object.entries(spec || {})) {
    const value = values?.[key];

    if (rule.kind === 'search') {
      const needle = foldText(value || '');
      if (!needle) continue;
      const fields = rule.fields || [];
      tests.push((row) => fields.some((name) => foldText(row?.[name]).includes(needle)));
    } else if (rule.kind === 'equals') {
      if (value === '' || value === null || value === undefined) continue;
      tests.push((row) => String(row?.[rule.field] ?? '') === String(value));
    } else if (rule.kind === 'dateRange') {
      const { start, end } = value || {};
      if (!start && !end) continue;
      tests.push((row) => {
        const raw = row?.[rule.field];
        if (!raw) return false;
        const iso = typeof raw === 'number'
          ? new Date(raw).toISOString().slice(0, 10)
          : String(raw).slice(0, 10);
        if (start && iso < start) return false;
        if (end && iso > end) return false;
        return true;
      });
    } else if (rule.kind === 'numRange') {
      const { min, max } = value || {};
      if (min === null && max === null) continue;
      tests.push((row) => {
        const amount = Number(row?.[rule.field] ?? 0);
        if (min !== null && min !== undefined && amount < min) return false;
        if (max !== null && max !== undefined && amount > max) return false;
        return true;
      });
    } else if (rule.kind === 'toggle') {
      if (!value) continue;
      tests.push(rule.test);
    }
  }

  if (tests.length === 0) return rows;
  return rows.filter((row) => tests.every((test) => test(row)));
}
