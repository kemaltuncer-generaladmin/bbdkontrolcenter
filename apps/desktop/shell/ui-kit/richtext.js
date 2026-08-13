// Zengin metin düzenleyici — kalın, italik, renk, hizalama; HTML yazmadan.
//
// NEDEN VAR: mağazadaki ürün açıklaması ve CMS sayfası HTML tutuyor. Bu
// dosyadan önce personel `<strong>` ve `<em>` etiketlerini ELLE yazıyordu;
// renk ise hiç yoktu, çünkü tek yolu `style` özniteliğiydi ve o da beyaz
// listede kapalı. Sonuç: "koyu yaz" gibi en sıradan istek kod bilgisi
// istiyordu. Buradaki düzenleyici o etiketleri kullanıcı adına üretir.
//
// GÜVENLİK — TEK LİSTE, İKİ KAPI (K9). Buradaki beyaz liste `store_cms`
// backend'indeki `sanitize_html` ile AYNI olmak zorundadır. Arayüz kapısı
// kolaylık içindir; asıl kapı sunucudadır. Biri genişletilirse öteki de
// genişletilir — yoksa kullanıcı ekranda gördüğü biçimi kaydedince sessizce
// kaybeder.
//
// `style` ÖZNİTELİĞİ NEDEN ARTIK AÇIK (ve neden hâlâ güvenli): eskiden tümden
// yasaktı, gerekçe "sayfayı kaplayan görünmez katman kurulabilir" idi. O saldırı
// `position`, `width/height`, `opacity`, `z-index` ister. Burada style ham
// geçmez: ÜÇ ÖZELLİĞE indirgenir — `color`, `background-color`, `text-align` —
// ve değerleri de biçim denetiminden geçer. Kaplama kurmaya yetecek hiçbir
// özellik listede yok, dolayısıyla gerekçe ortadan kalkıyor ama koruma kalıyor.
//
// `execCommand` NEDEN: resmen "deprecated" ama contenteditable üzerinde
// çalışan, tarayıcıya gömülü tek seçim/geri-al motoru odur. Yerine geçen bir
// standart YOK; elle Range işlemek geri-al yığınını (Ctrl+Z) bozardı. Çıktısı
// düzensizdir (`<b>`, `<font>`, `<div>`) — bu yüzden okunan her değer
// `sanitizeHtml`'den geçer ve etiketler normal biçimlerine eşlenir.

import { h, button, debounce } from './kit.js';

export const RICHTEXT_VERSION = '1.1.0';

// ------------------------------------------------------------- beyaz liste

/** Çizilmesine izin verilen etiketler — sunucudaki `ALLOWED_TAGS` ile aynı. */
const ALLOWED_TAGS = new Set([
  'p', 'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'li', 'a', 'img', 'strong', 'em',
  'u', 'span', 'br', 'table', 'thead', 'tbody', 'tr', 'td', 'th',
]);

/** İçeriğiyle birlikte atılanlar. Diğer tanınmayan etiket açılır, metni kalır. */
const DROP_TAGS = new Set([
  'script', 'style', 'iframe', 'frame', 'frameset', 'object', 'embed',
  'applet', 'form', 'input', 'button', 'select', 'textarea', 'svg',
  'math', 'noscript', 'template', 'link', 'meta', 'base',
]);

const VOID_TAGS = new Set(['br', 'img']);

/** Etiket başına izin verilen öznitelikler (`style` ayrı ele alınır). */
const ALLOWED_ATTRS = {
  a: ['href', 'title'],
  img: ['src', 'alt', 'title', 'width', 'height'],
  td: ['colspan', 'rowspan'],
  th: ['colspan', 'rowspan', 'scope'],
};

/**
 * `execCommand` ve dış yapıştırmanın ürettiği eş anlamlıları tek biçime çeker.
 * Anahtar sola, beyaz listedeki karşılığı sağa.
 */
const TAG_ALIASES = {
  b: 'strong', i: 'em', ins: 'u', div: 'p', section: 'p', article: 'p',
  h5: 'h4', h6: 'h4', font: 'span', mark: 'span', small: 'span', big: 'span',
};

const SAFE_SCHEMES = ['http:', 'https:', 'mailto:', 'tel:'];

/** Yalnız bu üç özellik geçer. Değerleri de ayrıca denetlenir. */
const STYLE_PROPS = new Set(['color', 'background-color', 'text-align']);
const ALIGN_VALUES = new Set(['left', 'center', 'right', 'justify']);

/** Renk paleti — düzenleyicideki seçenekler. Serbest renk girişi yok. */
export const TEXT_COLORS = [
  { value: '', label: 'Varsayılan' },
  { value: '#111827', label: 'Siyah' },
  { value: '#6b7280', label: 'Gri' },
  { value: '#b91c1c', label: 'Kırmızı' },
  { value: '#c2410c', label: 'Turuncu' },
  { value: '#a16207', label: 'Hardal' },
  { value: '#15803d', label: 'Yeşil' },
  { value: '#0e7490', label: 'Turkuaz' },
  { value: '#1d4ed8', label: 'Mavi' },
  { value: '#6d28d9', label: 'Mor' },
  { value: '#be185d', label: 'Pembe' },
];

export const HIGHLIGHT_COLORS = [
  { value: '', label: 'Vurgu yok' },
  { value: '#fef08a', label: 'Sarı' },
  { value: '#bbf7d0', label: 'Yeşil' },
  { value: '#bfdbfe', label: 'Mavi' },
  { value: '#fecaca', label: 'Kırmızı' },
  { value: '#e9d5ff', label: 'Mor' },
  { value: '#e5e7eb', label: 'Gri' },
];

// ---------------------------------------------------------------- temizlik

/** `rgb(17, 24, 39)` → `#111827`. Zaten hex ise küçük harfe indirir. */
function normalizeColor(raw) {
  const value = String(raw ?? '').trim().toLowerCase();
  if (!value) return '';
  if (/^#[0-9a-f]{3}$/.test(value)) {
    return `#${value[1]}${value[1]}${value[2]}${value[2]}${value[3]}${value[3]}`;
  }
  if (/^#[0-9a-f]{6}$/.test(value)) return value;
  const rgb = value.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+\s*)?\)$/);
  if (!rgb) return '';
  const parts = [rgb[1], rgb[2], rgb[3]].map((part) => {
    const number = Math.min(255, Math.max(0, Number(part)));
    return number.toString(16).padStart(2, '0');
  });
  return `#${parts.join('')}`;
}

/**
 * `style` değerini üç özelliğe indirger. Hiçbiri kalmazsa boş dizge döner ve
 * öznitelik hiç yazılmaz.
 */
export function filterStyle(raw) {
  const out = [];
  for (const chunk of String(raw ?? '').split(';')) {
    const at = chunk.indexOf(':');
    if (at < 0) continue;
    const prop = chunk.slice(0, at).trim().toLowerCase();
    const value = chunk.slice(at + 1).trim();
    if (!STYLE_PROPS.has(prop)) continue;
    if (prop === 'text-align') {
      if (ALIGN_VALUES.has(value.toLowerCase())) out.push(`text-align:${value.toLowerCase()}`);
      continue;
    }
    const color = normalizeColor(value);
    if (color) out.push(`${prop}:${color}`);
  }
  return out.join(';');
}

/**
 * Şema denetimi. `javascript:` ve `data:` reddedilir.
 *
 * Boşluk ve denetim karakterleri ÖNCE atılır: satır sonu serpiştirilmiş
 * `java&#10;script:` yazımı bazı tarayıcılarda hâlâ çalışır ve ham dizge
 * karşılaştırmasını atlatır.
 */
export function safeUrl(raw) {
  const value = String(raw ?? '').replace(/[^\x20-\x7e\u00a0-\uffff]|\s/g, '');
  if (!value) return '';
  if (value.startsWith('/') || value.startsWith('#')) return value;
  try {
    const parsed = new URL(value, 'https://bbdstore.com.tr');
    return SAFE_SCHEMES.includes(parsed.protocol) ? value : '';
  } catch {
    return '';
  }
}

/**
 * Bir DOM ağacını beyaz listeye indirgeyip yeni ağaç üretir.
 * `innerHTML` ile yazma YOK — düğümler tek tek klonlanır.
 */
function cleanInto(source, target, doc) {
  for (const child of Array.from(source.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) {
      target.append(doc.createTextNode(child.nodeValue));
      continue;
    }
    if (child.nodeType !== Node.ELEMENT_NODE) continue;

    const raw = child.tagName.toLowerCase();
    if (DROP_TAGS.has(raw)) continue;

    const tag = TAG_ALIASES[raw] || raw;

    // Tanınmayan etiket AÇILIR: metni ve çocukları korunur, kabuğu atılır.
    if (!ALLOWED_TAGS.has(tag)) {
      cleanInto(child, target, doc);
      continue;
    }

    const node = doc.createElement(tag);

    for (const name of ALLOWED_ATTRS[tag] || []) {
      if (!child.hasAttribute(name)) continue;
      let value = child.getAttribute(name);
      if (name === 'href' || name === 'src') {
        value = safeUrl(value);
        if (!value) continue;
      }
      node.setAttribute(name, value);
    }

    // `font color="…"` eski biçimdir; stile çevrilir.
    const legacyColor = raw === 'font' ? normalizeColor(child.getAttribute('color')) : '';
    const style = filterStyle(
      legacyColor ? `color:${legacyColor};${child.getAttribute('style') || ''}`
        : child.getAttribute('style'),
    );
    if (style) node.setAttribute('style', style);

    if (tag === 'a' && node.getAttribute('href')?.startsWith('http')) {
      node.setAttribute('target', '_blank');
      node.setAttribute('rel', 'noopener noreferrer');
    }

    if (!VOID_TAGS.has(tag)) cleanInto(child, node, doc);

    // Biçim taşımayan boş `span` gereksiz gürültüdür; içeriği yukarı taşınır.
    if (tag === 'span' && !node.hasAttribute('style')) {
      while (node.firstChild) target.append(node.firstChild);
      continue;
    }
    target.append(node);
  }
}

/** Serbest HTML'i beyaz listeye indirger ve dizge döndürür. */
export function sanitizeHtml(html) {
  const doc = new DOMParser().parseFromString(String(html ?? ''), 'text/html');
  const box = doc.createElement('div');
  cleanInto(doc.body, box, doc);
  return box.innerHTML.trim();
}

/** Etiketleri atıp düz metin bırakır — karakter sayacı bunu sayar. */
export function htmlToText(html) {
  const doc = new DOMParser().parseFromString(String(html ?? ''), 'text/html');
  for (const node of doc.querySelectorAll([...DROP_TAGS].join(','))) node.remove();
  return (doc.body.textContent || '').replace(/\s+/g, ' ').trim();
}

/** Beyaz listeden geçirilmiş içeriği çizen düğüm — önizleme için. */
export function renderHtml(html, className = 'kit-rt-doc') {
  const box = h('div', className);
  const doc = new DOMParser().parseFromString(String(html ?? ''), 'text/html');
  cleanInto(doc.body, box, document);
  return box;
}

// -------------------------------------------------------------- düzenleyici

const BLOCKS = [
  { value: 'p', label: 'Paragraf' },
  { value: 'h2', label: 'Başlık' },
  { value: 'h3', label: 'Alt başlık' },
  { value: 'h4', label: 'Küçük başlık' },
];

/** Seçimi kaybetmeden çalışan araç düğmesi. */
function tool(label, { title, onRun, className = '' }) {
  const node = button(label, { title });
  node.className = `kit-rt-tool${className ? ` ${className}` : ''}`;
  // `mousedown` engellenmezse odak araç çubuğuna geçer ve seçim silinir;
  // komut o zaman "hiçbir şeye" uygulanır.
  node.addEventListener('mousedown', (event) => event.preventDefault());
  node.addEventListener('click', onRun);
  return node;
}

/** Renk seçici — `select` kullanılır: paletten çıkılamaz, klavyeyle gezilir. */
function colorPicker(options, { title, ariaLabel, onPick }) {
  const node = h('select', 'kit-rt-color');
  node.title = title;
  node.setAttribute('aria-label', ariaLabel);
  for (const option of options) {
    const item = h('option', undefined, option.label);
    item.value = option.value;
    if (option.value) item.style.color = option.value;
    node.append(item);
  }
  node.addEventListener('mousedown', () => { node.dataset.armed = '1'; });
  node.addEventListener('change', () => {
    onPick(node.value);
    node.selectedIndex = 0;   // seçenek "uygulanır", seçili kalmaz
  });
  return node;
}

/**
 * Zengin metin düzenleyici.
 *
 * @param {object} spec
 * @param {string}   [spec.value]        — başlangıç HTML'i
 * @param {number}   [spec.maxLength]    — DÜZ METİN karakter sınırı (etiketler sayılmaz)
 * @param {string}   [spec.placeholder]
 * @param {boolean}  [spec.allowSource]  — "Kaynak" sekmesi (varsayılan açık)
 * @param {(html:string)=>void} [spec.onChange]
 * @returns {{node:HTMLElement, get:()=>string, set:(html:string)=>void,
 *            text:()=>string, focus:()=>void, destroy:()=>void}}
 */
export function richText({
  value = '',
  maxLength = 0,
  placeholder = 'Yazmaya başlayın…',
  allowSource = true,
  onChange,
} = {}) {
  const node = h('div', 'kit-rt');
  const bar = h('div', 'kit-rt-bar');
  const area = h('div', 'kit-rt-area');
  const source = h('textarea', 'kit-rt-source');
  const footer = h('div', 'kit-rt-foot');
  const counter = h('span', 'kit-rt-count');

  let html = sanitizeHtml(value);
  let sourceMode = false;

  area.contentEditable = 'true';
  area.spellcheck = true;
  area.dataset.placeholder = placeholder;
  area.setAttribute('role', 'textbox');
  area.setAttribute('aria-multiline', 'true');
  source.spellcheck = false;
  source.setAttribute('aria-label', 'HTML kaynağı');
  source.hidden = true;

  const paint = () => { area.replaceChildren(...renderHtml(html).childNodes); };
  paint();

  const updateCounter = () => {
    const length = htmlToText(html).length;
    if (!maxLength) { counter.textContent = `${length} karakter`; return; }
    counter.textContent = `${length} / ${maxLength} karakter`;
    counter.classList.toggle('over', length > maxLength);
  };

  const emit = () => { updateCounter(); onChange?.(html); };
  const emitSoon = debounce(emit, 220);

  /** Düzenleme alanından okunan her değer beyaz listeden geçer. */
  const readArea = () => { html = sanitizeHtml(area.innerHTML); };

  area.addEventListener('input', () => { readArea(); emitSoon(); });
  area.addEventListener('blur', () => { readArea(); emit(); });

  // Word/tarayıcı yapıştırması sayfa dolusu `style` ve `<o:p>` taşır.
  // Ham bırakılırsa alan bozulur; temizlenmiş HTML yerleştirilir.
  area.addEventListener('paste', (event) => {
    const data = event.clipboardData;
    if (!data) return;
    event.preventDefault();
    const pasted = data.getData('text/html') || data.getData('text/plain');
    const clean = data.getData('text/html')
      ? sanitizeHtml(pasted)
      : String(pasted).split(/\n{2,}/).map((block) => {
        const escaped = document.createElement('p');
        escaped.textContent = block.trim();
        return escaped.outerHTML;
      }).join('');
    document.execCommand('insertHTML', false, clean);
    readArea();
    emit();
  });

  const run = (command, argument = null) => {
    area.focus();
    // `styleWithCSS` açıkken renk `<span style>` üretir; kapalıyken `<font>`.
    // İkisi de temizlikten geçiyor ama span'i doğrudan üretmek daha az iş.
    document.execCommand('styleWithCSS', false, true);
    document.execCommand(command, false, argument);
    readArea();
    emit();
  };

  // --- araç çubuğu

  const blockSelect = h('select', 'kit-rt-block');
  blockSelect.title = 'Metin biçimi';
  blockSelect.setAttribute('aria-label', 'Metin biçimi');
  for (const block of BLOCKS) {
    const item = h('option', undefined, block.label);
    item.value = block.value;
    blockSelect.append(item);
  }
  blockSelect.addEventListener('mousedown', (event) => event.stopPropagation());
  blockSelect.addEventListener('change', () => run('formatBlock', `<${blockSelect.value}>`));

  const group = (...children) => {
    const box = h('div', 'kit-rt-group');
    box.append(...children);
    return box;
  };

  bar.append(
    group(blockSelect),
    group(
      tool('K', { title: 'Kalın (Ctrl+B)', onRun: () => run('bold'), className: 'is-bold' }),
      tool('İ', { title: 'İtalik (Ctrl+I)', onRun: () => run('italic'), className: 'is-italic' }),
      tool('A', { title: 'Altı çizili (Ctrl+U)', onRun: () => run('underline'), className: 'is-underline' }),
    ),
    group(
      colorPicker(TEXT_COLORS, {
        title: 'Yazı rengi',
        ariaLabel: 'Yazı rengi',
        onPick: (color) => run('foreColor', color || '#111827'),
      }),
      colorPicker(HIGHLIGHT_COLORS, {
        title: 'Vurgu rengi',
        ariaLabel: 'Vurgu rengi',
        onPick: (color) => run('hiliteColor', color || 'transparent'),
      }),
    ),
    group(
      tool('• Liste', { title: 'Madde işaretli liste', onRun: () => run('insertUnorderedList') }),
      tool('1. Liste', { title: 'Numaralı liste', onRun: () => run('insertOrderedList') }),
    ),
    group(
      tool('⇤', { title: 'Sola hizala', onRun: () => run('justifyLeft') }),
      tool('⇔', { title: 'Ortala', onRun: () => run('justifyCenter') }),
      tool('⇥', { title: 'Sağa hizala', onRun: () => run('justifyRight') }),
    ),
    group(
      tool('Bağlantı', {
        title: 'Seçili metni bağlantıya çevirir',
        onRun: () => {
          const href = window.prompt('Bağlantı adresi (ör. /iade-ve-cayma-hakki)', '/');
          if (!href) return;
          const clean = safeUrl(href);
          if (!clean) { window.alert('Adres kabul edilmedi. http, https, mailto, tel ya da / ile başlamalı.'); return; }
          run('createLink', clean);
        },
      }),
      tool('Bağlantıyı kaldır', { title: 'Seçimdeki bağlantıyı çözer', onRun: () => run('unlink') }),
    ),
    group(
      tool('Biçimi temizle', {
        title: 'Seçimdeki kalın/italik/renk biçimlerini kaldırır',
        onRun: () => run('removeFormat'),
      }),
    ),
  );

  if (allowSource) {
    const toggle = tool('Kaynak', {
      title: 'HTML kaynağını göster/gizle',
      onRun: () => {
        sourceMode = !sourceMode;
        if (sourceMode) {
          source.value = html;
          source.hidden = false;
          area.hidden = true;
          toggle.classList.add('on');
        } else {
          html = sanitizeHtml(source.value);
          paint();
          source.hidden = true;
          area.hidden = false;
          toggle.classList.remove('on');
          emit();
        }
      },
    });
    toggle.classList.add('kit-rt-sourcetoggle');
    bar.append(group(toggle));
  }

  source.addEventListener('input', () => {
    html = sanitizeHtml(source.value);
    emitSoon();
  });

  footer.append(counter);
  node.append(bar, area, source, footer);
  updateCounter();

  return {
    node,
    get: () => html,
    text: () => htmlToText(html),
    set(next) {
      html = sanitizeHtml(next);
      paint();
      if (sourceMode) source.value = html;
      updateCounter();
    },
    focus() { area.focus(); },
    destroy() { emitSoon.cancel(); },
  };
}
