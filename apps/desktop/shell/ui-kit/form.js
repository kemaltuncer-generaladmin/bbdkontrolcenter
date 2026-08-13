// Form üreteci — alan tipleri, doğrulama, kirli alan takibi.
//
// Tek kopya (ADR 0011). Deseni `bbd_students` panelinden geliyor ve iki
// fikrini koruyor:
//
//  1. DOM BİR KEZ kurulur, sonra yalnız değer yazılır. Her tuşta yeniden
//     çizmek imleci ve seçimi kaybettiriyor.
//  2. KİRLİ ALAN takibi: sunucuya yalnız DEĞİŞEN alanlar gönderilir.
//     Bagisto'da tam gövde göndermek, göndermediğiniz alanları boşaltabiliyor;
//     kısmi güncelleme hem güvenli hem denetim kaydında okunur.

import { h, money, moneyInput, parseMoney } from './kit.js';
import { dateField } from './datefield.js';

const TR_PHONE = /^5\d{9}$/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

/** Serbest yazımı 5XXXXXXXXX biçimine indirger; çözülemezse olduğu gibi döner. */
export function normalizePhone(raw) {
  const digits = String(raw ?? '').replace(/\D/g, '');
  if (digits.length === 12 && digits.startsWith('90')) return digits.slice(2);
  if (digits.length === 11 && digits.startsWith('0')) return digits.slice(1);
  return digits;
}

/** 5321234567 → "0532 123 45 67". */
export function formatPhone(value) {
  const digits = normalizePhone(value);
  if (!TR_PHONE.test(digits)) return String(value ?? '');
  return `0${digits.slice(0, 3)} ${digits.slice(3, 6)} ${digits.slice(6, 8)} ${digits.slice(8)}`;
}

/**
 * @param {object} spec
 * @param {Array} spec.fields — her biri:
 *   {key, label, type, required?, hint?, placeholder?, maxLength?, options?,
 *    wide?, readOnly?, validate?(value, draft) -> string|null}
 *   type: 'text' | 'textarea' | 'number' | 'money' | 'select' | 'checkbox'
 *       | 'phone' | 'email' | 'date' | 'static'
 * @param {object} [spec.value] — başlangıç kaydı
 * @param {(draft:object, dirty:string[])=>void} [spec.onChange]
 */
export function formGrid({ fields = [], value = {}, onChange } = {}) {
  const node = h('div', 'kit-form-grid');
  const controls = new Map();
  const disposers = [];

  let original = { ...value };
  let draft = { ...value };

  const keys = fields.map((field) => field.key);

  const dirty = () => keys.filter((key) => {
    const before = original[key] ?? '';
    const after = draft[key] ?? '';
    return String(before) !== String(after);
  });

  const touch = () => {
    paintErrors();
    onChange?.({ ...draft }, dirty());
  };

  function paintErrors(only = null) {
    for (const field of fields) {
      if (only && field.key !== only) continue;
      const control = controls.get(field.key);
      if (!control?.error) continue;
      const message = validateField(field, draft[field.key], draft);
      control.error.textContent = message || '';
      control.input?.classList?.toggle('bad', Boolean(message));
    }
  }

  for (const field of fields) {
    const wrap = h('label', `kit-field${field.wide ? ' kit-field-wide' : ''}`);
    const label = h('span', 'kit-field-label', field.label);
    if (field.required) label.append(h('span', 'req', '*'));
    wrap.append(label);

    let input = null;
    const write = (raw) => {
      draft[field.key] = raw;
      touch();
    };

    switch (field.type) {
      case 'textarea': {
        input = h('textarea', 'kit-textarea');
        if (field.maxLength) input.maxLength = field.maxLength;
        if (field.placeholder) input.placeholder = field.placeholder;
        input.addEventListener('input', () => write(input.value));
        break;
      }
      case 'select': {
        input = h('select', 'kit-select');
        for (const option of field.options || []) {
          const item = h('option', undefined, option.label);
          item.value = String(option.value);
          input.append(item);
        }
        input.addEventListener('change', () => write(input.value));
        break;
      }
      case 'checkbox': {
        input = h('input', 'kit-check');
        input.type = 'checkbox';
        input.addEventListener('change', () => write(input.checked));
        break;
      }
      case 'date': {
        const picker = dateField({
          value: draft[field.key] || '',
          label: field.label,
          onChange: (iso) => write(iso),
        });
        disposers.push(() => picker.destroy());
        controls.set(field.key, {
          node: picker.node,
          set: (raw) => picker.set(raw || ''),
          error: null,
        });
        wrap.append(picker.node);
        break;
      }
      case 'static': {
        input = h('div', 'kit-field-hint');
        break;
      }
      default: {
        input = h('input', 'kit-input');
        input.type = 'text';
        if (field.maxLength) input.maxLength = field.maxLength;
        if (field.placeholder) input.placeholder = field.placeholder;
        if (field.type === 'number' || field.type === 'money') input.inputMode = 'decimal';
        if (field.type === 'phone') input.inputMode = 'tel';
        input.addEventListener('input', () => {
          if (field.type === 'money') {
            const parsed = parseMoney(input.value);
            draft[field.key] = parsed;
            // Ham metni de sakla ki kullanıcı yazarken alan sıfırlanmasın.
            draft[`${field.key}Text`] = input.value;
            touch();
          } else if (field.type === 'number') {
            const raw = input.value.trim().replace(',', '.');
            draft[field.key] = raw === '' ? null : Number(raw);
            touch();
          } else {
            write(input.value);
          }
        });
        if (field.type === 'phone') {
          // Alan terk edilince okunur biçime geçer; saklanan değer ham kalır.
          input.addEventListener('blur', () => {
            const normal = normalizePhone(input.value);
            draft[field.key] = normal;
            input.value = formatPhone(normal);
            touch();
          });
        }
        break;
      }
    }

    if (input) {
      if (field.readOnly && 'readOnly' in input) input.readOnly = true;
      if (field.readOnly && input.tagName === 'SELECT') input.disabled = true;
      wrap.append(input);
      const error = h('span', 'kit-field-error');
      const setter = (raw) => {
        if (field.type === 'checkbox') input.checked = Boolean(raw);
        else if (field.type === 'money') input.value = raw === null || raw === undefined ? '' : moneyInput(raw);
        else if (field.type === 'phone') input.value = formatPhone(raw);
        else if (field.type === 'static') input.textContent = raw === null || raw === undefined || raw === '' ? '—' : String(raw);
        else input.value = raw === null || raw === undefined ? '' : String(raw);
      };
      controls.set(field.key, { node: input, input, set: setter, error });
      if (field.hint) wrap.append(h('span', 'kit-field-hint', field.hint));
      wrap.append(error);
    } else if (field.hint) {
      wrap.append(h('span', 'kit-field-hint', field.hint));
    }

    node.append(wrap);
  }

  const applyValues = () => {
    for (const field of fields) controls.get(field.key)?.set(draft[field.key]);
  };
  applyValues();

  return {
    node,
    draft: () => ({ ...draft }),
    dirty,
    /** Yalnız değişen alanlardan oluşan gövde — kısmi güncelleme için. */
    patch() {
      const body = {};
      for (const key of dirty()) body[key] = draft[key];
      return body;
    },
    errors() {
      const list = [];
      for (const field of fields) {
        const message = validateField(field, draft[field.key], draft);
        if (message) list.push({ key: field.key, message });
      }
      return list;
    },
    valid() { return this.errors().length === 0; },
    /** Sunucudan gelen kayıtla tazeler; kirli durum sıfırlanır. */
    reset(next = {}) {
      original = { ...next };
      draft = { ...next };
      applyValues();
      paintErrors();
    },
    set(key, raw) {
      draft[key] = raw;
      controls.get(key)?.set(raw);
      touch();
    },
    focus(key) { controls.get(key)?.input?.focus?.(); },
    showErrors() { paintErrors(); },
    /** Panel cleanup'ında ÇAĞRILMALI: tarih alanları global dinleyici tutuyor. */
    destroy() { disposers.forEach((fn) => fn()); },
  };
}

function validateField(field, value, draft) {
  const empty = value === null || value === undefined || String(value).trim() === '';
  if (field.required && field.type !== 'checkbox' && empty) {
    return `${field.label} zorunlu.`;
  }
  if (!empty) {
    if (field.type === 'phone' && !TR_PHONE.test(normalizePhone(value))) {
      return 'Cep numarası 10 haneli olmalı ve 5 ile başlamalı.';
    }
    if (field.type === 'email' && !EMAIL.test(String(value).trim())) {
      return 'Geçerli bir e-posta adresi yazın.';
    }
    if (field.type === 'money') {
      const amount = Number(value);
      if (Number.isNaN(amount)) return 'Tutarı 1.250,00 gibi yazın.';
      if (field.min !== undefined && amount < field.min) {
        return `En az ${money(field.min)} olmalı.`;
      }
      if (field.max !== undefined && amount > field.max) {
        return `En çok ${money(field.max)} olabilir.`;
      }
    }
    if (field.type === 'number') {
      const amount = Number(value);
      if (Number.isNaN(amount)) return 'Sayı yazın.';
      if (field.min !== undefined && amount < field.min) return `En az ${field.min} olmalı.`;
      if (field.max !== undefined && amount > field.max) return `En çok ${field.max} olabilir.`;
    }
  }
  return field.validate ? field.validate(value, draft) : null;
}
