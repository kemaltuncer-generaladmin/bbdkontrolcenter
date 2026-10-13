// Küçük DOM — panel JS'ini Node'da çalıştırıp erişilebilirlik denetimi yapmak için.
//
// NEDEN. Depoda tarayıcı ya da jsdom yok ve bağımlılık eklenmiyor (K11; makine
// kuralı: SDK/paket kurulmaz). Paneller DOM'u yalnız `document.createElement`,
// `append`, `setAttribute`, `classList`, `addEventListener` gibi temel
// yüzeyle kuruyor; bu dosya o yüzeyi gerçeğe yakın taklit eder. Yerleşim
// (layout) ve CSS hesaplanmaz: denetim YAPIYA bakar (ad, rol, etiket, bağ,
// odaklanabilirlik). Renk karşıtlığı CSS jetonlarından ayrıca hesaplanır
// (`audit.mjs` → `contrast`).
//
// Kullanım: `import { install } from './dom.mjs'; install();` → globalThis'e
// `document`, `window`, `HTMLElement`, `Option`, `Event`, `KeyboardEvent`…

/* eslint-disable max-classes-per-file */

const VOID = new Set(['input', 'br', 'hr', 'img', 'meta', 'link', 'source', 'col', 'wbr']);

// Özellik ↔ öznitelik yansıtması (yalnız panellerin kullandıkları).
const REFLECT = {
  id: 'id', title: 'title', type: 'type', name: 'name', placeholder: 'placeholder', href: 'href',
  src: 'src', alt: 'alt', lang: 'lang', dir: 'dir', role: 'role', htmlFor: 'for', target: 'target',
  rel: 'rel', download: 'download', inputMode: 'inputmode', autocomplete: 'autocomplete',
  accept: 'accept', min: 'min', max: 'max', step: 'step', pattern: 'pattern', width: 'width',
  height: 'height', viewBox: 'viewBox',
};
const BOOL = {
  hidden: 'hidden', disabled: 'disabled', readOnly: 'readonly', required: 'required',
  multiple: 'multiple', open: 'open', autofocus: 'autofocus', noValidate: 'novalidate',
};
const NUM = { maxLength: 'maxlength', minLength: 'minlength', rows: 'rows', cols: 'cols', size: 'size',
  colSpan: 'colspan', rowSpan: 'rowspan' };

export class Event {
  constructor(type, init = {}) {
    this.type = type;
    this.bubbles = Boolean(init.bubbles);
    this.cancelable = Boolean(init.cancelable);
    this.defaultPrevented = false;
    this.target = null;
    this.currentTarget = null;
    this._stop = false;
    this.detail = init.detail;
    Object.assign(this, Object.fromEntries(Object.entries(init).filter(([key]) => !['bubbles', 'cancelable'].includes(key))));
  }

  preventDefault() { if (this.cancelable !== false) this.defaultPrevented = true; }

  stopPropagation() { this._stop = true; }

  stopImmediatePropagation() { this._stop = true; this._immediate = true; }
}

export class KeyboardEvent extends Event {
  constructor(type, init = {}) {
    super(type, { bubbles: true, cancelable: true, ...init });
    this.key = init.key || '';
    this.code = init.code || '';
    for (const flag of ['ctrlKey', 'altKey', 'metaKey', 'shiftKey']) this[flag] = Boolean(init[flag]);
  }
}

export class MouseEvent extends Event {
  constructor(type, init = {}) {
    super(type, { bubbles: true, cancelable: true, ...init });
    this.button = init.button || 0;
    for (const flag of ['ctrlKey', 'altKey', 'metaKey', 'shiftKey']) this[flag] = Boolean(init[flag]);
  }
}

export class FocusEvent extends Event {}
export class InputEvent extends Event {}
export class CustomEvent extends Event {}

class ClassList {
  constructor(el) { this.el = el; }

  _get() { return (this.el.getAttribute('class') || '').split(/\s+/).filter(Boolean); }

  _set(list) { this.el.setAttribute('class', [...new Set(list)].join(' ')); }

  add(...names) { this._set([...this._get(), ...names.filter(Boolean)]); }

  remove(...names) { this._set(this._get().filter((name) => !names.includes(name))); }

  contains(name) { return this._get().includes(name); }

  toggle(name, force) {
    const has = this.contains(name);
    const want = force === undefined ? !has : Boolean(force);
    if (want && !has) this.add(name);
    if (!want && has) this.remove(name);
    return want;
  }

  replace(a, b) { if (this.contains(a)) { this.remove(a); this.add(b); } }

  get length() { return this._get().length; }

  [Symbol.iterator]() { return this._get()[Symbol.iterator](); }
}

function camelToData(key) { return `data-${key.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)}`; }
function dataToCamel(attr) { return attr.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()); }

export class Node {
  constructor(doc) {
    this.ownerDocument = doc;
    this.parentNode = null;
    this.childNodes = [];
    this._listeners = new Map();
  }

  get parentElement() { return this.parentNode instanceof Element ? this.parentNode : null; }

  get firstChild() { return this.childNodes[0] || null; }

  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }

  get nextSibling() {
    if (!this.parentNode) return null;
    const list = this.parentNode.childNodes;
    return list[list.indexOf(this) + 1] || null;
  }

  get previousSibling() {
    if (!this.parentNode) return null;
    const list = this.parentNode.childNodes;
    return list[list.indexOf(this) - 1] || null;
  }

  get isConnected() {
    let node = this;
    while (node.parentNode) node = node.parentNode;
    return node === this.ownerDocument;
  }

  _adopt(child) {
    if (child == null) return [];
    if (typeof child === 'string' || typeof child === 'number') {
      return [this.ownerDocument.createTextNode(String(child))];
    }
    if (child instanceof DocumentFragment) {
      const list = [...child.childNodes];
      child.childNodes = [];
      for (const item of list) item.parentNode = null;
      return list;
    }
    if (child.parentNode) child.parentNode.removeChild(child);
    return [child];
  }

  appendChild(child) { this.append(child); return child; }

  append(...children) {
    for (const raw of children) {
      for (const child of this._adopt(raw)) {
        child.parentNode = this;
        this.childNodes.push(child);
      }
    }
  }

  prepend(...children) {
    const adopted = children.flatMap((raw) => this._adopt(raw));
    for (const child of adopted) child.parentNode = this;
    this.childNodes.unshift(...adopted);
  }

  insertBefore(child, ref) {
    if (!ref) return this.appendChild(child);
    const adopted = this._adopt(child);
    const index = this.childNodes.indexOf(ref);
    for (const item of adopted) item.parentNode = this;
    this.childNodes.splice(index < 0 ? this.childNodes.length : index, 0, ...adopted);
    return child;
  }

  removeChild(child) {
    const index = this.childNodes.indexOf(child);
    if (index >= 0) this.childNodes.splice(index, 1);
    child.parentNode = null;
    if (this.ownerDocument && this.ownerDocument._active && child.contains?.(this.ownerDocument._active)) {
      this.ownerDocument._active = null;
    }
    return child;
  }

  replaceChild(next, old) { this.insertBefore(next, old); this.removeChild(old); return old; }

  remove() { this.parentNode?.removeChild(this); }

  replaceChildren(...children) {
    for (const child of [...this.childNodes]) this.removeChild(child);
    this.append(...children);
  }

  replaceWith(...nodes) {
    const parent = this.parentNode;
    if (!parent) return;
    const next = this.nextSibling;
    parent.removeChild(this);
    for (const node of nodes) parent.insertBefore(typeof node === 'string' ? this.ownerDocument.createTextNode(node) : node, next);
  }

  before(...nodes) { for (const node of nodes) this.parentNode?.insertBefore(node, this); }

  after(...nodes) {
    const next = this.nextSibling;
    for (const node of nodes) this.parentNode?.insertBefore(node, next);
  }

  contains(other) {
    for (let node = other; node; node = node.parentNode) if (node === this) return true;
    return false;
  }

  get textContent() { return this.childNodes.map((child) => child.textContent).join(''); }

  set textContent(value) {
    this.replaceChildren();
    if (value !== null && value !== undefined && String(value) !== '') {
      this.append(this.ownerDocument.createTextNode(String(value)));
    }
  }

  addEventListener(type, fn, options) {
    if (!fn) return;
    if (!this._listeners.has(type)) this._listeners.set(type, []);
    const once = typeof options === 'object' && options?.once;
    const capture = options === true || (typeof options === 'object' && options?.capture);
    this._listeners.get(type).push({ fn, once, capture });
  }

  removeEventListener(type, fn) {
    const list = this._listeners.get(type);
    if (!list) return;
    this._listeners.set(type, list.filter((entry) => entry.fn !== fn));
  }

  hasListener(type) { return (this._listeners.get(type) || []).length > 0; }

  dispatchEvent(event) {
    event.target = event.target || this;
    const path = [];
    for (let node = this; node; node = node.parentNode) path.push(node);
    if (this.ownerDocument && path[path.length - 1] === this.ownerDocument) path.push(this.ownerDocument.defaultView);
    const fire = (node, capture) => {
      const list = node?._listeners?.get(event.type);
      if (!list) return;
      for (const entry of [...list]) {
        if (Boolean(entry.capture) !== capture && node !== event.target) continue;
        event.currentTarget = node;
        if (entry.once) node.removeEventListener(event.type, entry.fn);
        try {
          if (typeof entry.fn === 'function') entry.fn.call(node, event);
          else entry.fn.handleEvent(event);
        } catch (error) {
          (globalThis.__domErrors ||= []).push(error);
        }
        if (event._immediate) return;
      }
    };
    for (const node of [...path].reverse()) {
      if (node === event.target) break;
      fire(node, true);
      if (event._stop) return !event.defaultPrevented;
    }
    fire(event.target, false);
    if (event.bubbles) {
      for (const node of path.slice(1)) {
        if (event._stop) break;
        fire(node, false);
      }
    }
    return !event.defaultPrevented;
  }
}

export class Text extends Node {
  constructor(doc, data) { super(doc); this.data = String(data); this.nodeType = 3; this.nodeName = '#text'; }

  get textContent() { return this.data; }

  set textContent(value) { this.data = String(value ?? ''); }

  get nodeValue() { return this.data; }

  cloneNode() { return new Text(this.ownerDocument, this.data); }
}

export class Comment extends Node {
  constructor(doc, data) { super(doc); this.data = String(data); this.nodeType = 8; }

  get textContent() { return ''; }

  cloneNode() { return new Comment(this.ownerDocument, this.data); }
}

export class DocumentFragment extends Node {
  constructor(doc) { super(doc); this.nodeType = 11; }

  get children() { return this.childNodes.filter((node) => node instanceof Element); }

  querySelector(selector) { return query(this, selector, true)[0] || null; }

  querySelectorAll(selector) { return query(this, selector, false); }

  cloneNode(deep) {
    const copy = new DocumentFragment(this.ownerDocument);
    if (deep) copy.append(...this.childNodes.map((node) => node.cloneNode(true)));
    return copy;
  }
}

export class Element extends Node {
  constructor(doc, tag, ns = null) {
    super(doc);
    this.nodeType = 1;
    this.namespaceURI = ns;
    this.localName = ns ? tag : tag.toLowerCase();
    this.tagName = ns ? tag : tag.toUpperCase();
    this.nodeName = this.tagName;
    this._attrs = new Map();
    this.classList = new ClassList(this);
    this.style = makeStyle();
    this.dataset = new Proxy({}, {
      get: (_, key) => (typeof key === 'string' ? this.getAttribute(camelToData(key)) ?? undefined : undefined),
      set: (_, key, value) => { this.setAttribute(camelToData(key), String(value)); return true; },
      deleteProperty: (_, key) => { this.removeAttribute(camelToData(key)); return true; },
      has: (_, key) => this.hasAttribute(camelToData(key)),
      ownKeys: () => [...this._attrs.keys()].filter((name) => name.startsWith('data-')).map(dataToCamel),
      getOwnPropertyDescriptor: (_, key) => (this.hasAttribute(camelToData(key))
        ? { enumerable: true, configurable: true, value: this.getAttribute(camelToData(key)) } : undefined),
    });
    this.scrollTop = 0;
    this.scrollLeft = 0;
  }

  get children() { return this.childNodes.filter((node) => node instanceof Element); }

  get childElementCount() { return this.children.length; }

  get firstElementChild() { return this.children[0] || null; }

  get lastElementChild() { const list = this.children; return list[list.length - 1] || null; }

  get nextElementSibling() {
    let node = this.nextSibling;
    while (node && !(node instanceof Element)) node = node.nextSibling;
    return node;
  }

  get previousElementSibling() {
    let node = this.previousSibling;
    while (node && !(node instanceof Element)) node = node.previousSibling;
    return node;
  }

  get attributes() { return [...this._attrs].map(([name, value]) => ({ name, value })); }

  getAttribute(name) { const value = this._attrs.get(String(name)); return value === undefined ? null : value; }

  setAttribute(name, value) { this._attrs.set(String(name), String(value)); }

  removeAttribute(name) { this._attrs.delete(String(name)); }

  hasAttribute(name) { return this._attrs.has(String(name)); }

  toggleAttribute(name, force) {
    const want = force === undefined ? !this.hasAttribute(name) : Boolean(force);
    if (want) this.setAttribute(name, ''); else this.removeAttribute(name);
    return want;
  }

  getAttributeNames() { return [...this._attrs.keys()]; }

  get className() { return this.getAttribute('class') || ''; }

  set className(value) { this.setAttribute('class', String(value ?? '')); }

  get tabIndex() {
    const raw = this.getAttribute('tabindex');
    if (raw !== null) return Number(raw);
    return isNativeFocusable(this) ? 0 : -1;
  }

  set tabIndex(value) { this.setAttribute('tabindex', String(value)); }

  get innerHTML() { return this.textContent; }

  set innerHTML(value) {
    // Paneller innerHTML'i yalnız boşaltmak ya da düz metin için kullanır;
    // etiketli metin burada AYRIŞTIRILMAZ (denetim bunu ayrıca yakalar).
    this.replaceChildren();
    const text = String(value ?? '');
    if (text) {
      if (/<[a-z]/i.test(text)) (globalThis.__domInnerHtml ||= []).push(text.slice(0, 80));
      this.append(this.ownerDocument.createTextNode(text.replace(/<[^>]*>/g, '')));
    }
  }

  get outerHTML() { return `<${this.localName}>${this.textContent}</${this.localName}>`; }

  insertAdjacentElement(where, el) {
    if (where === 'beforeend') this.append(el);
    else if (where === 'afterbegin') this.prepend(el);
    else if (where === 'beforebegin') this.before(el);
    else this.after(el);
    return el;
  }

  insertAdjacentText(where, text) { this.insertAdjacentElement(where, this.ownerDocument.createTextNode(text)); }

  querySelector(selector) { return query(this, selector, true)[0] || null; }

  querySelectorAll(selector) { return query(this, selector, false); }

  getElementsByTagName(tag) { return query(this, tag, false); }

  getElementsByClassName(name) { return query(this, `.${name}`, false); }

  matches(selector) { return parseList(selector).some((chain) => matchChain(this, chain)); }

  closest(selector) {
    for (let node = this; node instanceof Element; node = node.parentNode) if (node.matches(selector)) return node;
    return null;
  }

  cloneNode(deep) {
    const copy = this.ownerDocument.createElement(this.localName);
    for (const [name, value] of this._attrs) copy.setAttribute(name, value);
    if (deep) copy.append(...this.childNodes.map((node) => node.cloneNode(true)));
    return copy;
  }

  focus() {
    if (this.disabled) return;
    const doc = this.ownerDocument;
    const before = doc._active;
    if (before === this) return;
    doc._active = this;
    if (before) before.dispatchEvent(new FocusEvent('blur', { relatedTarget: this }));
    if (before) before.dispatchEvent(new FocusEvent('focusout', { bubbles: true, relatedTarget: this }));
    this.dispatchEvent(new FocusEvent('focus', { relatedTarget: before }));
    this.dispatchEvent(new FocusEvent('focusin', { bubbles: true, relatedTarget: before }));
  }

  blur() {
    const doc = this.ownerDocument;
    if (doc._active !== this) return;
    doc._active = null;
    this.dispatchEvent(new FocusEvent('blur', {}));
    this.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
  }

  click() {
    if (this.disabled) return;
    if (this.localName === 'input' && ['checkbox', 'radio'].includes(this.type)) {
      this.checked = this.type === 'radio' ? true : !this.checked;
    }
    const proceed = this.dispatchEvent(new MouseEvent('click'));
    if (proceed && this.localName === 'input' && ['checkbox', 'radio'].includes(this.type)) {
      this.dispatchEvent(new Event('input', { bubbles: true }));
      this.dispatchEvent(new Event('change', { bubbles: true }));
    }
    if (proceed && this.localName === 'summary' && this.parentNode?.localName === 'details') {
      this.parentNode.open = !this.parentNode.open;
      this.parentNode.dispatchEvent(new Event('toggle'));
    }
  }

  getBoundingClientRect() { return { x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; }

  getClientRects() { return []; }

  scrollIntoView() {}

  scrollTo() {}

  scrollBy() {}

  showModal() { this.open = true; }

  close() { this.open = false; }

  showPicker() {}

  select() {}

  setSelectionRange() {}

  setCustomValidity(message) { this._validity = message; }

  reportValidity() { return !this._validity; }

  checkValidity() { return !this._validity; }

  animate() { return { finished: Promise.resolve(), cancel() {}, onfinish: null }; }

  get offsetWidth() { return 0; }

  get offsetHeight() { return 0; }

  get clientWidth() { return 0; }

  get clientHeight() { return 0; }

  get scrollHeight() { return 0; }

  get scrollWidth() { return 0; }
}

for (const [prop, attr] of Object.entries(REFLECT)) {
  Object.defineProperty(Element.prototype, prop, {
    get() { return this.getAttribute(attr) ?? ''; },
    set(value) { this.setAttribute(attr, String(value ?? '')); },
    configurable: true,
  });
}
for (const [prop, attr] of Object.entries(BOOL)) {
  Object.defineProperty(Element.prototype, prop, {
    get() { return this.hasAttribute(attr); },
    set(value) { this.toggleAttribute(attr, Boolean(value)); },
    configurable: true,
  });
}
for (const [prop, attr] of Object.entries(NUM)) {
  Object.defineProperty(Element.prototype, prop, {
    get() { const raw = this.getAttribute(attr); return raw === null ? -1 : Number(raw); },
    set(value) { this.setAttribute(attr, String(value)); },
    configurable: true,
  });
}

// Form denetimlerinin değer/işaret durumu (öznitelikten ayrı yaşar).
Object.defineProperty(Element.prototype, 'value', {
  get() {
    if (this.localName === 'select') {
      const chosen = this.options.find((option) => option.selected) || this.options[0];
      return chosen ? chosen.value : '';
    }
    if (this.localName === 'option') return this.hasAttribute('value') ? this.getAttribute('value') : this.textContent;
    if (this.localName === 'textarea') return this._value ?? this.textContent;
    if (this._value !== undefined) return this._value;
    return this.getAttribute('value') ?? (this.localName === 'input' && ['checkbox', 'radio'].includes(this.type) ? 'on' : '');
  },
  set(value) {
    const text = value === null || value === undefined ? '' : String(value);
    if (this.localName === 'select') {
      let found = false;
      for (const option of this.options) {
        option._selected = !found && option.value === text;
        if (option._selected) found = true;
      }
      return;
    }
    if (this.localName === 'option') { this.setAttribute('value', text); return; }
    this._value = text;
  },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'checked', {
  get() { return this._checked ?? this.hasAttribute('checked'); },
  set(value) {
    this._checked = Boolean(value);
    if (this._checked && this.type === 'radio' && this.name) {
      const scope = this.closest('form') || this.ownerDocument;
      for (const other of scope.querySelectorAll(`input[type="radio"]`)) {
        if (other !== this && other.name === this.name) other._checked = false;
      }
    }
  },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'indeterminate', {
  get() { return Boolean(this._indeterminate); },
  set(value) { this._indeterminate = Boolean(value); },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'selected', {
  get() {
    if (this._selected !== undefined) return this._selected;
    const select = this.closest('select');
    if (!select) return this.hasAttribute('selected');
    const explicit = select.options.some((option) => option._selected !== undefined);
    if (explicit) return false;
    const marked = select.options.find((option) => option.hasAttribute('selected'));
    return marked ? marked === this : select.options[0] === this;
  },
  set(value) {
    const select = this.closest('select');
    if (select && value && !select.multiple) for (const option of select.options) option._selected = option === this;
    else this._selected = Boolean(value);
  },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'options', {
  get() { return this.localName === 'select' ? query(this, 'option', false) : undefined; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'selectedIndex', {
  get() { return this.options ? this.options.findIndex((option) => option.selected) : -1; },
  set(index) { if (this.options?.[index]) this.options[index].selected = true; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'selectedOptions', {
  get() { return this.options ? this.options.filter((option) => option.selected) : []; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'text', {
  get() { return this.textContent; },
  set(value) { this.textContent = value; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'files', {
  get() { return this._files || []; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'labels', {
  get() {
    const out = [];
    const id = this.id;
    if (id) out.push(...this.ownerDocument.querySelectorAll('label').filter((label) => label.htmlFor === id));
    const wrap = this.closest('label');
    if (wrap && !out.includes(wrap)) out.push(wrap);
    return out;
  },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'isContentEditable', {
  get() { return this.getAttribute('contenteditable') === 'true' || this.getAttribute('contenteditable') === ''; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'contentEditable', {
  get() { return this.getAttribute('contenteditable') ?? 'inherit'; },
  set(value) { this.setAttribute('contenteditable', String(value)); },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'form', {
  get() { return this.closest('form'); },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'validity', {
  get() { return { valid: !this._validity, customError: Boolean(this._validity) }; },
  configurable: true,
});
Object.defineProperty(Element.prototype, 'valueAsNumber', {
  get() { return this.value === '' ? Number.NaN : Number(this.value); },
  set(value) { this.value = String(value); },
  configurable: true,
});

function makeStyle() {
  const store = {};
  return new Proxy(store, {
    get(target, key) {
      if (key === 'setProperty') return (name, value) => { target[name] = String(value); };
      if (key === 'removeProperty') return (name) => { delete target[name]; };
      if (key === 'getPropertyValue') return (name) => target[name] ?? '';
      if (key === 'cssText') return Object.entries(target).map(([k, v]) => `${k}: ${v}`).join('; ');
      return target[key] ?? '';
    },
    set(target, key, value) {
      if (key === 'cssText') {
        for (const part of String(value).split(';')) {
          const cut = part.indexOf(':');
          if (cut > 0) target[part.slice(0, cut).trim()] = part.slice(cut + 1).trim();
        }
        return true;
      }
      target[key] = value;
      return true;
    },
  });
}

export function isNativeFocusable(el) {
  if (!(el instanceof Element) || el.disabled) return false;
  const tag = el.localName;
  if (tag === 'a') return el.hasAttribute('href');
  if (tag === 'input') return el.type !== 'hidden';
  if (['button', 'select', 'textarea', 'iframe'].includes(tag)) return true;
  if (tag === 'summary') return el.parentNode?.localName === 'details';
  return el.isContentEditable;
}

// ------------------------------------------------------------ seçici motoru

function parseCompound(text) {
  const parts = { tag: null, id: null, classes: [], attrs: [], pseudos: [] };
  const re = /(^[a-zA-Z*][\w-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]|:(not|is|where)\(([^()]*(?:\([^()]*\)[^()]*)*)\)|:([\w-]+)(?:\(([^)]*)\))?/g;
  let match;
  let consumed = 0;
  while ((match = re.exec(text)) !== null) {
    if (match.index !== consumed) throw new Error(`seçici çözülemedi: ${text}`);
    consumed = re.lastIndex;
    if (match[1]) parts.tag = match[1] === '*' ? null : match[1].toLowerCase();
    else if (match[2]) parts.id = match[2];
    else if (match[3]) parts.classes.push(match[3]);
    else if (match[4]) parts.attrs.push({ name: match[4], op: match[5], value: match[6] ?? match[7] ?? match[8]?.trim() });
    else if (match[9]) parts.pseudos.push({ name: match[9], arg: match[10] });
    else if (match[11]) parts.pseudos.push({ name: match[11], arg: match[12] });
  }
  if (consumed !== text.length) throw new Error(`seçici çözülemedi: ${text}`);
  return parts;
}

function splitTop(text, sep) {
  const out = [];
  let depth = 0;
  let quote = null;
  let current = '';
  for (const char of text) {
    if (quote) { current += char; if (char === quote) quote = null; continue; }
    if (char === '"' || char === "'") { quote = char; current += char; continue; }
    if (char === '(' || char === '[') depth += 1;
    if (char === ')' || char === ']') depth -= 1;
    if (depth === 0 && sep.test(char)) { out.push(current); current = ''; continue; }
    current += char;
  }
  out.push(current);
  return out;
}

function parseList(selector) {
  return splitTop(String(selector), /,/).map((part) => part.trim()).filter(Boolean).map((part) => {
    const tokens = [];
    const normalized = part.replace(/\s*([>+~])\s*/g, ' $1 ').trim();
    const pieces = splitTop(normalized, /\s/).filter(Boolean);
    let combinator = ' ';
    for (const piece of pieces) {
      if (['>', '+', '~'].includes(piece)) { combinator = piece; continue; }
      tokens.push({ combinator, compound: parseCompound(piece) });
      combinator = ' ';
    }
    return tokens;
  });
}

function matchCompound(el, c) {
  if (!(el instanceof Element)) return false;
  if (c.tag && el.localName !== c.tag) return false;
  if (c.id && el.id !== c.id) return false;
  for (const name of c.classes) if (!el.classList.contains(name)) return false;
  for (const attr of c.attrs) {
    if (!el.hasAttribute(attr.name)) return false;
    const actual = el.getAttribute(attr.name);
    if (attr.op === '=' && actual !== attr.value) return false;
    if (attr.op === '~=' && !actual.split(/\s+/).includes(attr.value)) return false;
    if (attr.op === '^=' && !actual.startsWith(attr.value)) return false;
    if (attr.op === '$=' && !actual.endsWith(attr.value)) return false;
    if (attr.op === '*=' && !actual.includes(attr.value)) return false;
  }
  for (const pseudo of c.pseudos) {
    if (pseudo.name === 'not' && el.matches(pseudo.arg)) return false;
    if ((pseudo.name === 'is' || pseudo.name === 'where') && !el.matches(pseudo.arg)) return false;
    if (pseudo.name === 'checked' && !el.checked) return false;
    if (pseudo.name === 'disabled' && !el.disabled) return false;
    if (pseudo.name === 'first-child' && el.parentNode?.children?.[0] !== el) return false;
    if (pseudo.name === 'last-child') { const list = el.parentNode?.children || []; if (list[list.length - 1] !== el) return false; }
    if (pseudo.name === 'focus' && el.ownerDocument._active !== el) return false;
    if (pseudo.name === 'scope') { /* kök — sorgu bağlamında geçer */ }
  }
  return true;
}

function matchChain(el, chain, index = chain.length - 1) {
  const { compound, combinator } = chain[index];
  if (!matchCompound(el, compound)) return false;
  if (index === 0) return true;
  if (combinator === '>') return matchChain(el.parentNode, chain, index - 1);
  if (combinator === '+') return el.previousElementSibling ? matchChain(el.previousElementSibling, chain, index - 1) : false;
  if (combinator === '~') {
    for (let node = el.previousElementSibling; node; node = node.previousElementSibling) if (matchChain(node, chain, index - 1)) return true;
    return false;
  }
  for (let node = el.parentNode; node instanceof Element; node = node.parentNode) if (matchChain(node, chain, index - 1)) return true;
  return false;
}

function walk(root, visit) {
  for (const child of root.childNodes) {
    if (child instanceof Element) {
      if (visit(child) === false) return false;
      if (walk(child, visit) === false) return false;
    }
  }
  return true;
}

function query(root, selector, first) {
  const scoped = /^\s*:scope\s*>\s*/.exec(selector);
  if (scoped) {
    const rest = parseList(selector.slice(scoped[0].length));
    const hits = (root.children || []).filter((el) => rest.some((chain) => matchChain(el, chain)));
    return first ? hits.slice(0, 1) : hits;
  }
  const chains = parseList(selector);
  const out = [];
  walk(root, (el) => {
    if (chains.some((chain) => matchChain(el, chain))) {
      out.push(el);
      if (first) return false;
    }
    return true;
  });
  return out;
}

// ---------------------------------------------------------------- belge

export class Document extends Node {
  constructor() {
    super(null);
    this.ownerDocument = this;
    this.nodeType = 9;
    this._active = null;
    this.documentElement = this.createElement('html');
    this.head = this.createElement('head');
    this.body = this.createElement('body');
    this.documentElement.append(this.head, this.body);
    this.append(this.documentElement);
    this.title = '';
    this.readyState = 'complete';
    this.visibilityState = 'visible';
    this.hidden = false;
    this.cookie = '';
  }

  createElement(tag) {
    const el = new Element(this, String(tag));
    if (String(tag).toLowerCase() === 'details') el.open = false;
    return el;
  }

  createElementNS(ns, tag) { return new Element(this, String(tag), ns); }

  createTextNode(text) { return new Text(this, text); }

  createComment(text) { return new Comment(this, text); }

  createDocumentFragment() { return new DocumentFragment(this); }

  get activeElement() { return this._active || this.body; }

  getElementById(id) { return query(this, `#${CSS_escape(id)}`, true)[0] || null; }

  querySelector(selector) { return query(this, selector, true)[0] || null; }

  querySelectorAll(selector) { return query(this, selector, false); }

  getElementsByTagName(tag) { return query(this, tag, false); }

  get children() { return [this.documentElement]; }

  execCommand() { return false; }

  getSelection() { return { rangeCount: 0, removeAllRanges() {}, addRange() {} }; }

  createRange() { return { selectNodeContents() {}, setStart() {}, setEnd() {}, collapse() {} }; }

  hasFocus() { return true; }
}

function CSS_escape(value) { return String(value).replace(/([^\w-])/g, '\\$1'); }

/** Global yüzeyi kurar (bir kez). Dönen `document` her çağrıda yenidir. */
export function install() {
  const doc = new Document();
  const win = globalThis;
  const listeners = new Node(doc);
  win.document = doc;
  win.window = win;
  win.self = win;
  doc.defaultView = win;
  win.Node = Node;
  win.Element = Element;
  win.HTMLElement = Element;
  win.SVGElement = Element;
  for (const name of ['HTMLInputElement', 'HTMLSelectElement', 'HTMLTextAreaElement', 'HTMLButtonElement',
    'HTMLAnchorElement', 'HTMLDivElement', 'HTMLDetailsElement', 'HTMLDialogElement', 'HTMLImageElement',
    'HTMLCanvasElement', 'HTMLTableElement', 'HTMLFormElement', 'HTMLLabelElement', 'HTMLOptionElement']) {
    win[name] = Element;
  }
  win.Text = Text;
  win.DocumentFragment = DocumentFragment;
  win.Event = Event;
  win.KeyboardEvent = KeyboardEvent;
  win.MouseEvent = MouseEvent;
  win.PointerEvent = MouseEvent;
  win.FocusEvent = FocusEvent;
  win.InputEvent = InputEvent;
  win.CustomEvent = CustomEvent;
  win.Option = function Option(text = '', value, defaultSelected = false, selected = false) {
    const option = doc.createElement('option');
    option.textContent = String(text);
    if (value !== undefined) option.setAttribute('value', String(value));
    if (defaultSelected) option.setAttribute('selected', '');
    if (selected) option._selected = true;
    return option;
  };
  win.addEventListener = (...args) => listeners.addEventListener(...args);
  win.removeEventListener = (...args) => listeners.removeEventListener(...args);
  win.dispatchEvent = (event) => listeners.dispatchEvent(event);
  win._listenersNode = listeners;
  win.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {} });
  win.getComputedStyle = (el) => el.style;
  win.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
  win.cancelAnimationFrame = (id) => clearTimeout(id);
  win.requestIdleCallback = (fn) => setTimeout(() => fn({ timeRemaining: () => 10 }), 0);
  win.cancelIdleCallback = (id) => clearTimeout(id);
  const storage = () => {
    const map = new Map();
    return { getItem: (key) => (map.has(key) ? map.get(key) : null), setItem: (key, value) => map.set(key, String(value)),
      removeItem: (key) => map.delete(key), clear: () => map.clear(), key: (i) => [...map.keys()][i] ?? null,
      get length() { return map.size; } };
  };
  win.localStorage = storage();
  win.sessionStorage = storage();
  win.location = { href: 'http://localhost/', origin: 'http://localhost', hash: '', search: '', reload() {} };
  win.history = { pushState() {}, replaceState() {}, back() {} };
  win.scrollTo = () => {};
  win.innerWidth = 1280;
  win.innerHeight = 800;
  win.devicePixelRatio = 1;
  win.print = () => {};
  win.open = () => null;
  win.alert = () => {};
  win.confirm = () => true;
  win.prompt = () => null;
  win.CSS = { escape: CSS_escape, supports: () => true };
  win.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  win.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
  win.MutationObserver = class { observe() {} disconnect() {} takeRecords() { return []; } };
  win.DOMParser = class { parseFromString() { return new Document(); } };
  win.Image = function Image() { return doc.createElement('img'); };
  win.FileReader = class { readAsDataURL() {} readAsText() {} readAsArrayBuffer() {} };
  if (!win.navigator) win.navigator = {};
  try { Object.defineProperty(win, 'navigator', { value: { ...win.navigator, clipboard: { writeText: async () => {} }, language: 'tr-TR', userAgent: 'node' }, configurable: true }); } catch { /* yoksa olduğu gibi */ }
  return doc;
}

/**
 * Küçük HTML ayrıştırıcı — yalnız depodaki elle yazılmış, düzgün HTML için
 * (`apps/desktop/shell/index.html`). Yorumlar atlanır, `<script>` içeriği
 * okunmaz, boş öğeler (`input`, `meta`…) kapanış beklemez.
 */
export function parseHTML(doc, html, into = doc.body) {
  const source = String(html).replace(/<!--[\s\S]*?-->/g, '').replace(/<!doctype[^>]*>/i, '');
  const stack = [into];
  const re = /<\/([a-zA-Z][\w-]*)\s*>|<([a-zA-Z][\w-]*)((?:\s+[^\s=>\/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>|([^<]+)/g;
  let match;
  let skip = null;
  while ((match = re.exec(source)) !== null) {
    const top = stack[stack.length - 1];
    if (skip) {
      if (match[1] && match[1].toLowerCase() === skip) { skip = null; stack.pop(); }
      continue;
    }
    if (match[1]) {
      const tag = match[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i -= 1) {
        if (stack[i].localName === tag) { stack.length = i; break; }
      }
    } else if (match[2]) {
      const tag = match[2].toLowerCase();
      if (['html', 'head', 'body'].includes(tag)) {
        const target = tag === 'html' ? doc.documentElement : doc[tag];
        for (const m of (match[3] || '').matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
          target.setAttribute(m[1], m[2] ?? m[3] ?? m[4] ?? '');
        }
        if (tag !== 'html') { stack.length = 1; stack[0] = target; }
        continue;
      }
      const el = /^(svg|circle|path|g|rect|line|polyline|polygon|title|use|defs|ellipse)$/.test(tag)
        && (top.namespaceURI || tag === 'svg') ? doc.createElementNS('http://www.w3.org/2000/svg', tag) : doc.createElement(tag);
      for (const m of (match[3] || '').matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g)) {
        el.setAttribute(m[1], m[2] ?? m[3] ?? m[4] ?? '');
      }
      top.append(el);
      if (tag === 'script' || tag === 'style') { stack.push(el); skip = tag; continue; }
      if (!VOID.has(tag) && !match[4]) stack.push(el);
    } else if (match[5]) {
      const value = match[5].replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      if (value.trim()) top.append(doc.createTextNode(value));
    }
  }
  return into;
}
