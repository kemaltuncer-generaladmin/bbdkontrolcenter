// Erişilebilirlik denetimi — axe-core benzeri kurallar, Node'da (WCAG 2.2 AA).
//
// `dom.mjs` ile kurulan ağaçta paneli çizdikten sonra `audit(kök)` ihlalleri
// döner. Kurallar axe-core'un en sık yakaladıklarının karşılığıdır; yerleşim
// gerektirenler (gerçek piksel boyutu, odak sırası görsel konumu) burada
// YAPIDAN denetlenir, gerisi elle kontrol listesindedir (README).
//
//   button-name        düğme / bağlantı / [role=button] erişilebilir ad taşır
//   label              form denetimi etiketlidir (label, aria-label(ledby), title)
//   aria-valid-ref     aria-labelledby/-describedby/-controls/-activedescendant kimliği var
//   duplicate-id       aynı kimlik iki kez yok
//   dialog-name        role=dialog|alertdialog adlı ve aria-modal
//   tabindex           pozitif tabindex yok (mantıklı sekme sırası)
//   click-events       tıklanan ama etkileşimsiz öğe (div, span…) rol + tabindex taşır
//   svg-img-alt        süs olmayan svg / img metin alternatifi taşır
//   aria-hidden-focus  aria-hidden içinde odaklanabilir öğe yok
//   aria-roles         rol değeri geçerli
//   aria-values        aria-expanded/-selected/-pressed/-checked/-invalid değeri geçerli
//   invalid-describedby aria-invalid=true denetim hatasına aria-describedby ile bağlı
//   live-region        bildirim/toast öğesi role=status|alert ya da aria-live taşır
//   tab-pattern        role=tab öğesi tablist içinde ve aria-selected taşır
//   label-orphan       <label for> hedefi var
//   name-not-placeholder yalnız placeholder ile etiketlenmiş denetim yok
//
// Ayrıca `contrast(ön, arka)` (WCAG göreli parlaklık) ve CSS denetimleri
// (`cssAudit`): hareket azaltma, odak çizgisi kaldırılmışsa yerine
// :focus-visible, dokunma hedefi.

const ROLES = new Set(['alert', 'alertdialog', 'application', 'article', 'banner', 'button', 'cell',
  'checkbox', 'columnheader', 'combobox', 'complementary', 'contentinfo', 'definition', 'dialog',
  'directory', 'document', 'feed', 'figure', 'form', 'grid', 'gridcell', 'group', 'heading', 'img',
  'link', 'list', 'listbox', 'listitem', 'log', 'main', 'marquee', 'math', 'menu', 'menubar',
  'menuitem', 'menuitemcheckbox', 'menuitemradio', 'meter', 'navigation', 'none', 'note', 'option',
  'presentation', 'progressbar', 'radio', 'radiogroup', 'region', 'row', 'rowgroup', 'rowheader',
  'scrollbar', 'search', 'searchbox', 'separator', 'slider', 'spinbutton', 'status', 'switch', 'tab',
  'table', 'tablist', 'tabpanel', 'term', 'textbox', 'timer', 'toolbar', 'tooltip', 'tree', 'treegrid',
  'treeitem']);

const INTERACTIVE_TAGS = new Set(['a', 'button', 'input', 'select', 'textarea', 'summary', 'label',
  'option', 'details', 'dialog']);
const INTERACTIVE_ROLES = new Set(['button', 'link', 'checkbox', 'radio', 'tab', 'menuitem', 'option',
  'switch', 'menuitemcheckbox', 'menuitemradio', 'treeitem', 'gridcell', 'row', 'combobox', 'textbox',
  'slider', 'spinbutton', 'searchbox']);
const BOOL_ARIA = { 'aria-expanded': ['true', 'false', 'undefined'], 'aria-selected': ['true', 'false', 'undefined'],
  'aria-pressed': ['true', 'false', 'mixed', 'undefined'], 'aria-checked': ['true', 'false', 'mixed', 'undefined'],
  'aria-invalid': ['true', 'false', 'grammar', 'spelling'], 'aria-hidden': ['true', 'false', 'undefined'],
  'aria-modal': ['true', 'false'], 'aria-disabled': ['true', 'false'], 'aria-busy': ['true', 'false'],
  'aria-current': ['page', 'step', 'location', 'date', 'time', 'true', 'false'],
  'aria-live': ['off', 'polite', 'assertive'] };

function all(root) {
  const out = [];
  const visit = (node) => {
    for (const child of node.childNodes || []) {
      if (child.nodeType === 1) { out.push(child); visit(child); }
    }
  };
  if (root.nodeType === 1) out.push(root);
  visit(root);
  return out;
}

/** Gizli mi (hidden, aria-hidden zinciri ya da display:none satır içi stili). */
export function isHidden(el) {
  for (let node = el; node && node.nodeType === 1; node = node.parentNode) {
    if (node.hidden || node.getAttribute('aria-hidden') === 'true') return true;
    if (String(node.style?.display || '') === 'none') return true;
    if (node.localName === 'template') return true;
  }
  return false;
}

function byId(doc, root, id) {
  return doc.getElementById(id) || all(root).find((el) => el.id === id) || null;
}

function text(el) { return String(el.textContent || '').replace(/\s+/g, ' ').trim(); }

/** Erişilebilir ad (accname'in sadeleştirilmiş hâli). */
export function accessibleName(el, root = el.ownerDocument) {
  const doc = el.ownerDocument;
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const name = labelledby.split(/\s+/).map((id) => byId(doc, root, id)).filter(Boolean).map(text).join(' ').trim();
    if (name) return name;
  }
  const aria = (el.getAttribute('aria-label') || '').trim();
  if (aria) return aria;
  const tag = el.localName;
  if (['input', 'select', 'textarea'].includes(tag)) {
    if (tag === 'input' && ['button', 'submit', 'reset'].includes(el.type)) return el.value || '';
    const labels = el.id ? all(root.ownerDocument === root ? root : root).filter((node) => node.localName === 'label'
      && node.getAttribute('for') === el.id) : [];
    const docLabels = el.id ? doc.querySelectorAll('label').filter((node) => node.getAttribute('for') === el.id) : [];
    const wrap = el.closest('label');
    const name = [...new Set([...labels, ...docLabels, wrap].filter(Boolean))].map(text).join(' ').trim();
    if (name) return name;
    return (el.getAttribute('title') || '').trim();
  }
  if (tag === 'img') return (el.getAttribute('alt') || '').trim();
  if (tag === 'svg') {
    const title = el.querySelector('title');
    return title ? text(title) : '';
  }
  const own = text(el);
  if (own) return own;
  // Ad içerikteki svg/img'den de gelebilir.
  for (const child of all(el).slice(1)) {
    if (child.localName === 'img' && child.getAttribute('alt')) return child.getAttribute('alt');
    if (child.localName === 'svg' && child.getAttribute('aria-label')) return child.getAttribute('aria-label');
  }
  return (el.getAttribute('title') || '').trim();
}

function describe(el) {
  const cls = el.getAttribute('class');
  const id = el.id ? `#${el.id}` : '';
  const name = text(el).slice(0, 40);
  return `<${el.localName}${id}${cls ? `.${cls.split(/\s+/).join('.')}` : ''}>${name ? ` “${name}”` : ''}`;
}

function focusable(el) {
  if (el.disabled) return false;
  const raw = el.getAttribute('tabindex');
  if (raw !== null) return Number(raw) >= 0;
  const tag = el.localName;
  if (tag === 'a') return el.hasAttribute('href');
  if (tag === 'input') return el.type !== 'hidden';
  return ['button', 'select', 'textarea', 'summary'].includes(tag);
}

/**
 * @param {Element} root
 * @param {{ignore?: string[]}} options  — kapsam dışı kural kimlikleri
 * @returns {Array<{rule, node, message}>}
 */
export function audit(root, { ignore = [] } = {}) {
  const doc = root.ownerDocument;
  const out = [];
  const add = (rule, el, message) => { if (!ignore.includes(rule)) out.push({ rule, node: describe(el), message }); };
  const nodes = all(root);
  const seen = new Map();

  for (const el of nodes) {
    const tag = el.localName;
    const role = el.getAttribute('role');
    const hidden = isHidden(el);

    if (el.id) {
      if (seen.has(el.id)) add('duplicate-id', el, `kimlik “${el.id}” iki kez kullanılmış`);
      seen.set(el.id, el);
    }

    if (role && !role.split(/\s+/).every((item) => ROLES.has(item))) add('aria-roles', el, `geçersiz rol “${role}”`);

    for (const [attr, allowed] of Object.entries(BOOL_ARIA)) {
      if (el.hasAttribute(attr) && !allowed.includes(el.getAttribute(attr))) {
        add('aria-values', el, `${attr}="${el.getAttribute(attr)}" geçersiz`);
      }
    }

    for (const attr of ['aria-labelledby', 'aria-describedby', 'aria-controls', 'aria-activedescendant', 'aria-owns',
      'aria-errormessage']) {
      const value = el.getAttribute(attr);
      if (!value) continue;
      for (const id of value.split(/\s+/).filter(Boolean)) {
        if (!byId(doc, root, id)) add('aria-valid-ref', el, `${attr} → “${id}” bulunamadı`);
      }
    }

    const tabindex = el.getAttribute('tabindex');
    if (tabindex !== null && Number(tabindex) > 0) add('tabindex', el, `tabindex=${tabindex} sekme sırasını bozar`);

    if (hidden) continue;

    if (tag === 'button' || role === 'button' || (tag === 'a' && el.hasAttribute('href')) || role === 'link'
      || role === 'tab' || role === 'menuitem' || role === 'option' || role === 'switch') {
      if (!accessibleName(el, root)) add('button-name', el, 'düğmenin/bağlantının erişilebilir adı yok');
    }

    if ((tag === 'input' && !['hidden', 'button', 'submit', 'reset'].includes(el.type)) || tag === 'select'
      || tag === 'textarea' || role === 'textbox' || role === 'combobox' || role === 'searchbox'
      || role === 'checkbox' || role === 'radio' || role === 'slider' || role === 'spinbutton') {
      const name = accessibleName(el, root);
      if (!name) {
        if (el.getAttribute('placeholder')) add('name-not-placeholder', el, 'denetim yalnız placeholder ile etiketli');
        else add('label', el, 'form denetiminin etiketi yok');
      }
    }

    if (tag === 'label' && el.getAttribute('for') && !byId(doc, root, el.getAttribute('for'))) {
      add('label-orphan', el, `label for="${el.getAttribute('for')}" hedefi yok`);
    }

    if (role === 'dialog' || role === 'alertdialog' || tag === 'dialog') {
      if (!accessibleName(el, root) || accessibleName(el, root) === text(el)) {
        if (!el.getAttribute('aria-labelledby') && !el.getAttribute('aria-label')) {
          add('dialog-name', el, 'diyalog aria-labelledby/aria-label taşımıyor');
        }
      }
      if (tag !== 'dialog' && el.getAttribute('aria-modal') !== 'true') add('dialog-name', el, 'diyalog aria-modal="true" değil');
    }

    if (el.hasListener?.('click') && !INTERACTIVE_TAGS.has(tag) && !(role && INTERACTIVE_ROLES.has(role))) {
      // Kapsayıcıya bağlanmış olay yetkisi (içinde gerçek düğme olan) serbest.
      const delegated = all(el).slice(1).some((child) => focusable(child));
      if (!delegated) add('click-events', el, 'tıklanan öğe klavyeyle erişilemez (rol + tabindex ya da <button>)');
    }
    if (role && INTERACTIVE_ROLES.has(role) && !INTERACTIVE_TAGS.has(tag) && el.getAttribute('tabindex') === null) {
      // `option` / `row` / `gridcell` odaklanmadan aria-activedescendant ile yönetilebilir.
      const managed = ['option', 'row', 'gridcell', 'treeitem'].includes(role)
        || (role === 'tab' && el.closest('[role="tablist"]'));
      if (!managed) add('click-events', el, `role=${role} öğesi odaklanamıyor (tabindex yok)`);
    }

    if (tag === 'img' && !el.hasAttribute('alt') && role !== 'presentation' && role !== 'none') {
      add('svg-img-alt', el, 'img alt taşımıyor');
    }
    if (tag === 'svg' && el.getAttribute('aria-hidden') !== 'true' && role !== 'presentation' && role !== 'none') {
      const parentNamed = el.parentNode && (el.parentNode.getAttribute?.('aria-label') || text(el.parentNode));
      if (!accessibleName(el, root) && !(role === 'img' && el.getAttribute('aria-label')) && !parentNamed) {
        add('svg-img-alt', el, 'svg ne süs (aria-hidden) ne adlı');
      }
    }

    if (el.getAttribute('aria-invalid') === 'true') {
      const ids = (el.getAttribute('aria-describedby') || '') + ' ' + (el.getAttribute('aria-errormessage') || '');
      const linked = ids.split(/\s+/).filter(Boolean).map((id) => byId(doc, root, id)).filter((node) => node && text(node));
      if (!linked.length) add('invalid-describedby', el, 'hatalı alan hata metnine aria-describedby ile bağlı değil');
    }

    const cls = el.getAttribute('class') || '';
    if (/(^|\s|-)toast(\s|$)/.test(cls) && !role && !el.getAttribute('aria-live')) {
      add('live-region', el, 'bildirim ekran okuyucuya duyurulmuyor (role=status / aria-live)');
    }

    if (role === 'tab') {
      if (!el.closest('[role="tablist"]')) add('tab-pattern', el, 'role=tab tablist içinde değil');
      if (!el.hasAttribute('aria-selected')) add('tab-pattern', el, 'role=tab aria-selected taşımıyor');
    }
  }

  for (const el of nodes) {
    if (el.getAttribute('aria-hidden') === 'true') {
      for (const child of all(el)) {
        if (focusable(child) && !child.hidden) add('aria-hidden-focus', child, 'aria-hidden içinde odaklanabilir öğe');
      }
    }
  }
  return out;
}

// --------------------------------------------------------------- karşıtlık

function channel(value) {
  const c = value / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

/** `#rgb`, `#rrggbb`, `rgb(a)` → [r, g, b, a]. */
export function parseColor(text) {
  const raw = String(text).trim().toLowerCase();
  let m = /^#([0-9a-f]{3})$/.exec(raw);
  if (m) return [...m[1]].map((ch) => parseInt(ch + ch, 16)).concat(1);
  m = /^#([0-9a-f]{6})$/.exec(raw);
  if (m) return [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)).concat(1);
  m = /^rgba?\(([^)]+)\)$/.exec(raw);
  if (m) {
    const parts = m[1].split(/[\s,/]+/).filter(Boolean).map(Number);
    return [parts[0], parts[1], parts[2], parts[3] ?? 1];
  }
  if (raw === 'white' || raw === '#fff') return [255, 255, 255, 1];
  if (raw === 'black') return [0, 0, 0, 1];
  throw new Error(`renk çözülemedi: ${text}`);
}

/** Yarı saydam ön rengi arka plana karıştırır. */
export function blend(front, back) {
  const [r, g, b, a] = front;
  return [r * a + back[0] * (1 - a), g * a + back[1] * (1 - a), b * a + back[2] * (1 - a), 1];
}

export function luminance([r, g, b]) {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** WCAG karşıtlık oranı (1–21). */
export function contrast(fore, back) {
  const bg = parseColor(back);
  const fg = blend(parseColor(fore), bg);
  const [l1, l2] = [luminance(fg), luminance(bg)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

// -------------------------------------------------------------- CSS denetimi

/**
 * CSS metninde: (1) animasyon/geçiş varsa `prefers-reduced-motion` bloğu var mı
 * (ya da kabuk genel kuralı yeterli sayılır → `shellCoversMotion`); (2) odak
 * çizgisi kaldırılan seçicinin yanında `:focus-visible` karşılığı var mı.
 */
export function cssAudit(css, { name = 'css', shellCoversMotion = false } = {}) {
  const out = [];
  const body = String(css).replace(/\/\*[\s\S]*?\*\//g, '');
  const moves = /(animation\s*:|transition\s*:)/.test(body);
  if (moves && !shellCoversMotion && !/prefers-reduced-motion/.test(body)) {
    out.push({ rule: 'reduced-motion', node: name, message: 'animasyon/geçiş var, prefers-reduced-motion yok' });
  }
  const rules = body.split('}');
  for (const rule of rules) {
    const [selector, decl = ''] = rule.split('{');
    if (!selector || !/:focus(?!-visible|-within)/.test(selector)) continue;
    if (/outline\s*:\s*(none|0)\b/.test(decl) && !/(border|box-shadow|background)/.test(decl)) {
      out.push({ rule: 'focus-visible', node: `${name}: ${selector.trim()}`,
        message: 'odak çizgisi kaldırılmış, yerine görünür bir odak işareti yok' });
    }
  }
  return out;
}

/** CSS'teki `--ad: değer` jetonlarını okur (ilk tanım). */
export function tokens(css, scope = null) {
  const body = String(css).replace(/\/\*[\s\S]*?\*\//g, '');
  const out = {};
  const blocks = scope ? body.split('}').filter((block) => block.split('{')[0].includes(scope)) : [body];
  for (const block of blocks) {
    for (const m of block.matchAll(/(--[\w-]+)\s*:\s*([^;}]+)/g)) {
      if (!(m[1] in out)) out[m[1]] = m[2].trim();
    }
  }
  return out;
}

/**
 * Metin rengi karşıtlığı — CSS kurallarından (yerleşimsiz yaklaşık denetim).
 * Her `color:` bildirimi, aynı kuralın `background(-color)`ı varsa ona, yoksa
 * `defaultBg`ye (panel kâğıdı) karşı ölçülür. `var(--x)` jetonları aynı
 * dosyadan ve `extra` sözlüğünden çözülür; çözülemeyen / `currentColor` /
 * `inherit` / gradyan atlanır. Yer tutucu (`::placeholder`) da metindir.
 * `skip`: bilinçli istisna seçicileri (ör. devre dışı düğme — WCAG 1.4.3 muaf).
 */
export function cssContrast(css, { defaultBg = '#ffffff', extra = {}, min = 4.5, skip = [] } = {}) {
  const body = String(css).replace(/\/\*[\s\S]*?\*\//g, '');
  const vars = { ...tokens(body), ...extra };
  const resolve = (value, depth = 0) => {
    const text = String(value || '').trim().replace(/\s*!important$/, '');
    const m = /^var\((--[\w-]+)(?:\s*,\s*([^)]+))?\)$/.exec(text);
    if (m) {
      if (depth > 5) return null;
      return resolve(vars[m[1]] ?? m[2], depth + 1);
    }
    if (/^(#[0-9a-f]{3}|#[0-9a-f]{6}|rgba?\([^)]*\)|white|black)$/i.test(text)) return text;
    return null;
  };
  const out = [];
  const flat = body.replace(/@media[^{]*\{([\s\S]*?\})\s*\}/g, (all, inner) => (/prefers-color-scheme:\s*dark/.test(all) ? '' : inner));
  for (const rule of flat.split('}')) {
    const [selector = '', decl = ''] = rule.split('{');
    const sel = selector.trim();
    if (!sel || sel.startsWith('@') || skip.some((item) => sel.includes(item))) continue;
    const color = /(?:^|;|\s)color\s*:\s*([^;]+)/.exec(decl);
    if (!color) continue;
    const fg = resolve(color[1]);
    if (!fg) continue;
    const bgMatch = /background(?:-color)?\s*:\s*([^;]+)/.exec(decl);
    const bg = bgMatch ? resolve(bgMatch[1]) : defaultBg;
    if (!bg) continue;
    let bgSolid;
    try { bgSolid = blend(parseColor(bg), parseColor(defaultBg)); } catch { continue; }
    const bgHex = `rgb(${bgSolid.slice(0, 3).map((v) => Math.round(v)).join(',')})`;
    let ratio;
    try { ratio = contrast(fg, bgHex); } catch { continue; }
    if (ratio < min) out.push({ selector: sel.replace(/\s+/g, ' '), color: fg, background: bg, ratio: Math.round(ratio * 100) / 100 });
  }
  return out;
}
