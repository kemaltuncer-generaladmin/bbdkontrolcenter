// CMS paneli — vitrin sayfaları, yasal metinler, SSS ve 301 yönlendirmeleri.
//
// NE YAPAR: solda dallara ayrılmış sayfa ağacı, sağda düzenleyici (künye ·
// içerik · SEO · sürümler). Arama BAŞLIK, SLUG ve İÇERİK METNİNDE çalışır;
// eşleşen yerden bir alıntı gösterilir. SEO kartı arama sonucunu taklit eder
// ve karakter sayacı sınırları söyler. Her kaydetmeden önce sunucu eski hâli
// sürüm tablosuna yazar; "Bu sürüme dön" geri alır.
//
// NE YAPMAZ:
//  · Sayfa SİLMEZ. Silinen sayfanın adresi 404 verir ve arama motorunda,
//    e-postalarda, faturalarda duran bağlantılar kırılır (ADR 0012).
//  · Zengin metin düzenleyici (WYSIWYG) DEĞİLDİR. Düzenleme kaynak üzerinden
//    yapılır, araç çubuğu seçimi etiketle sarar. Sahte bir WYSIWYG'in
//    ürettiği `<div style=…>` çorbası, beyaz listeden geçerken sessizce
//    biçimini kaybederdi.
//  · Blok DÜZENLEMEZ (salt okunur) ve MENÜ yönetmez — uçları yok, sekmeler
//    bunu yazar.
//
// GÜVENLİK KARARI — ÖNİZLEME `iframe`/`srcdoc` KULLANMAZ.
// Kabuğun CSP'sinde `frame-src` yok; `default-src 'self'` devreye girer ve
// `srcdoc` davranışı WebKitGTK'da öngörülemez (kimi sürümde boş, kimi sürümde
// engellenmiş çerçeve). Bunun yerine içerik `DOMParser` ile ayrıştırılır ve
// BEYAZ LİSTELİ etiketler (p h1-h4 ul ol li a img strong em br table td th)
// KLONLANARAK çizilir; `script`/`style`/`iframe` ve tüm `on*` öznitelikleri
// atılır. `innerHTML` hiçbir yerde kullanılmaz. Aynı beyaz liste sunucuda da
// uygulanır (backend/content.py) — iki kapı, tek liste.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/store_cms/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/store_cms/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  button, clip, confirmWithReason, csvBlob, debounce, h, loadStyles, num, toaster,
} from '../../ui-kit/kit.js';
import { dataTable, pager } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { applyChoiceFilter } from '../../ui-kit/choice.js';
import {
  alertBox, badge, card, chipRow, emptyState, hintBox, kpiRow, skeletonRows, splitView,
  statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { renderHtml, richText } from '../../ui-kit/richtext.js';
import { reportChain } from '../../ui-kit/report.js';

const BASE = '/api/store_cms';

// `count: 0` ZORUNLU: kit sayaç yuvasını yalnız başlangıçta `count` verilen
// çipe kuruyor, sonradan `counts()` çağırmak yuvası olmayan çipe yazamaz.
const CHIPS = [
  { key: 'legal', label: 'Yasal metinler', count: 0 },
  { key: 'seo_missing', label: 'SEO eksik', count: 0 },
  { key: 'broken_links', label: 'Kırık iç bağlantı', count: 0 },
  { key: 'empty', label: 'İçeriği zayıf', count: 0 },
];

const EMPTY_STATE = {
  tree: [], items: [], counts: {}, total: 0, connected: false, error: '', chip: null,
  selectedId: 0, detail: null, loaded: false, reference: { channels: [], siteUrl: '' },
  seoSection: 'rewrites',
};

let api = null;
let toast = null;
let report = null;
let busy = false;
let state = { ...EMPTY_STATE };

const nodes = {};

// =================================================== beyaz listeli çizim
//
// BEYAZ LİSTE ARTIK BURADA DEĞİL. Bu dosyada `SAFE_TAGS`/`cloneSafe` adıyla
// ikinci bir kopya duruyordu ve sunucudakinden çoktan ayrışmıştı: `img`
// genişlik/yükseklik burada yoktu, `frameset` eksikti, `u`/`span` hiç yoktu.
// İki listeyi elle eşit tutmak işlemedi. Tek kopya kitte (`ui-kit/richtext.js`)
// ve zengin metin düzenleyicisi de aynı listeyi kullanıyor — böylece "yazarken
// gördüğüm biçim kaydedince kayboldu" durumu yapısal olarak imkânsız.

/**
 * İçeriği çizer. `innerHTML` YOK — kit düğümleri tek tek klonlar.
 *
 * Kitin çıktısına iki panel dokunuşu eklenir: görsel tembel yüklenir ve
 * açılmayan görsel sessiz kalmaz. Boş kutu "sayfa bozuk mu" sorusunu
 * doğuruyordu; CSP engellediğinde neden görünmediği yazılı olmalı.
 */
function renderSafe(html) {
  const box = renderHtml(html, 'cm-doc');
  for (const image of box.querySelectorAll('img')) {
    image.loading = 'lazy';
    image.addEventListener('error', () => {
      image.replaceWith(h('span', 'cm-noimg',
        `görsel açılmadı: ${image.getAttribute('src') || '—'}`));
    });
  }
  if (!box.childNodes.length) box.append(h('p', 'cm-dim', 'İçerik boş.'));
  return box;
}

// ------------------------------------------------------------------ araçlar

/** Sunucu `{ok:false, error}` de dönebilir; ikisi de tek yerde okunur. */
async function call(path, options) {
  const result = await api(path, options);
  if (result && result.ok === false && result.error) throw new Error(result.error);
  return result;
}

async function withBusy(label, work) {
  if (busy) return null;
  busy = true;
  nodes.status?.set(label);
  try {
    return await work();
  } catch (error) {
    toast(error.message || 'İşlem başarısız.', 'bad');
    nodes.status?.set(error.message || 'İşlem başarısız.', true);
    return null;
  } finally {
    busy = false;
  }
}

/** Yazan her işlem buradan geçer: gerekçe backend'e gider ve kayda yazılır. */
function askReason({ title, description, confirmLabel }) {
  return confirmWithReason(nodes.root, {
    title,
    description,
    confirmLabel,
    minLength: 10,
    placeholder: 'Gerekçe (en az 10 karakter) — denetim kaydına yazılır',
  });
}

/** Sunucudaki `slugify` ile aynı kural: Türkçe harfler önce eşlenir. */
function slugify(value) {
  const map = { ı: 'i', İ: 'i', ş: 's', Ş: 's', ğ: 'g', Ğ: 'g', ü: 'u', Ü: 'u', ö: 'o', Ö: 'o', ç: 'c', Ç: 'c' };
  return String(value || '')
    .replace(/[ıİşŞğĞüÜöÖçÇ]/g, (letter) => map[letter])
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .replace(/-{2,}/g, '-')
    .toLowerCase();
}

function statusText() {
  if (!state.connected) return `Mağazaya ulaşılamadı — ${state.error}`;
  return `Bağlı · ${num(state.total)} sayfa listeleniyor`;
}

// ==================================================================== veri

function filters() {
  const values = nodes.filters ? nodes.filters.values() : {};
  return { q: values.q || '', channel: values.channel || '' };
}

async function refreshPages() {
  nodes.treeBox?.replaceChildren(skeletonRows(8, 1));
  nodes.status?.set('Sayfalar alınıyor…');
  const { q, channel } = filters();
  const query = new URLSearchParams({ q, channel, chip: state.chip || '', size: '200' });
  let payload;
  try {
    payload = await api(`${BASE}/pages?${query.toString()}`);
  } catch (error) {
    state = { ...state, connected: false, error: error.message, tree: [], items: [], total: 0 };
    renderTree();
    nodes.status?.set(statusText(), true);
    return;
  }
  state = {
    ...state,
    tree: payload.tree || [],
    items: payload.items || [],
    counts: payload.counts || {},
    total: payload.total || 0,
    connected: Boolean(payload.connected),
    error: payload.error || '',
    loaded: true,
  };
  renderTree();
  nodes.chips?.counts?.(state.counts);
  nodes.status?.set(statusText(), !state.connected);
}

async function loadLegal() {
  if (!nodes.legal) return;
  let payload;
  try {
    payload = await api(`${BASE}/legal`);
  } catch {
    nodes.legal.replaceChildren();          // yasal şerit isteğe bağlı (K7)
    return;
  }
  const strip = h('div', 'cm-legal');
  for (const item of payload.items || []) {
    const chip = h('button', 'cm-legal-chip');
    chip.type = 'button';
    const tone = !item.found ? 'bad' : (item.empty ? 'warn' : 'good');
    chip.append(h('span', 'cm-legal-label', item.label));
    // Renk tek başına anlam taşımaz: rozetin içinde her zaman yazı var.
    chip.append(badge(!item.found ? 'yok' : (item.empty ? 'çok kısa' : `${num(item.chars)} krk`), tone));
    chip.disabled = !item.pageId;
    chip.title = item.found ? `/${item.slug} · güncelleme ${item.updatedAt || '—'}`
      : `Bu adreste sayfa yok: /${item.slug || '—'}`;
    chip.addEventListener('click', () => openPage(item.pageId));
    strip.append(chip);
  }
  const missing = payload.missing || 0;
  nodes.legal.replaceChildren(card(
    'Yasal metinler', strip,
    missing ? `${missing} metin eksik` : 'dördü de yerinde',
  ));
  if (missing) {
    nodes.legal.append(alertBox(
      `${missing} yasal metin mağazada bulunamadı. Mesafeli satış, iade, gizlilik ve çerez `
      + 'metinleri vitrinde bulunmak zorundadır; adresleri modül ayarındaki `legal_slugs` '
      + 'ile eşleşmiyorsa ayarı düzeltin.', 'warn',
    ));
  }
}

async function loadReference() {
  try {
    const payload = await api(`${BASE}/reference`);
    state.reference = { channels: payload.channels || [], siteUrl: payload.siteUrl || '' };
    // TEK KANALLI MAĞAZADA KUTU ÇİZİLMEZ. Karar veriden çıkar: ikinci kanal
    // açılırsa süzgeç kendiliğinden geri gelir (`choice.js`). Sert kodlama yok.
    applyChoiceFilter(nodes.filters, 'channel', state.reference.channels,
                      { allLabel: 'Tümü — kanal' });
  } catch {
    // Kanal listesi gelmezse kutu gizli kalır, ekran çalışır (K7).
  }
}

// =================================================================== ağaç

function renderTree() {
  const box = nodes.treeBox;
  if (!box) return;
  box.replaceChildren();

  if (!state.connected) {
    box.append(emptyState({
      title: 'Mağazaya ulaşılamadı',
      text: state.error || 'Bağlantı kurulamadı.',
      actions: [button('Tekrar dene', { variant: 'primary', onClick: () => refreshPages() })],
    }));
    return;
  }
  if (!state.tree.length) {
    box.append(emptyState({
      title: 'Bu aramaya uyan sayfa yok',
      text: 'Arama başlık, adres ve içerik metninde çalışır. Süzgeci gevşetin.',
      actions: [button('Aramayı temizle', { onClick: () => nodes.filters.reset() })],
    }));
    return;
  }

  for (const branch of state.tree) {
    const group = h('div', 'cm-branch');
    const head = h('div', 'cm-branch-head');
    head.append(h('b', undefined, branch.label), badge(String(branch.count), 'dim'));
    group.append(head);

    for (const item of branch.items) {
      const row = h('button', 'cm-node');
      row.type = 'button';
      if (item.id === state.selectedId) row.classList.add('on');
      const line = h('span', 'cm-node-main');
      line.append(clip(h('b'), item.title, 42));
      line.append(h('code', 'cm-slug', `/${item.slug || '—'}`));
      row.append(line);

      const marks = h('span', 'cm-node-marks');
      if (!item.seoComplete) marks.append(badge('SEO eksik', 'warn'));
      if (item.brokenLinks) marks.append(badge(`${num(item.brokenLinks)} kırık bağlantı`, 'bad'));
      if (item.chars < 200) marks.append(badge(`${num(item.chars)} krk`, 'dim'));
      if (marks.childNodes.length) row.append(marks);
      if (item.hit) row.append(h('span', 'cm-hit', item.hit));

      row.addEventListener('click', () => openPage(item.id));
      group.append(row);
    }
    box.append(group);
  }
}

// ============================================================= düzenleyici

function dropEditor() {
  nodes.editorForms?.forEach((form) => form.destroy());
  nodes.editorForms = [];
  nodes.previewSoon?.cancel();
  nodes.previewSoon = null;
  // Zengin metin düzenleyicisi de bekleyen bir debounce tutuyor; sayfa
  // değişince tetiklenirse artık DOM'da olmayan önizlemeyi boyar.
  nodes.contentEditor?.destroy();
  nodes.contentEditor = null;
}

async function openPage(pageId) {
  if (!pageId) return;
  state.selectedId = pageId;
  renderTree();
  dropEditor();
  nodes.editor.replaceChildren(skeletonRows(6, 2));

  let payload;
  try {
    payload = await call(`${BASE}/pages/${pageId}`);
  } catch (error) {
    nodes.editor.replaceChildren(emptyState({
      title: 'Sayfa okunamadı', text: error.message,
      actions: [button('Tekrar dene', { onClick: () => openPage(pageId) })],
    }));
    return;
  }
  state.detail = payload;
  paintEditor();
}

function paintEditor() {
  const payload = state.detail;
  if (!payload) return;
  const page = payload.page;
  dropEditor();
  nodes.editorForms = [];

  const box = nodes.editor;
  box.replaceChildren();

  const head = h('div', 'cm-head');
  head.append(h('b', 'cm-title', page.title));
  head.append(h('code', 'cm-slug', `/${page.slug}`));
  if (page.status === null) {
    head.append(badge('durum alanı yok', 'dim'));
  } else {
    head.append(badge(page.status ? 'Yayında' : 'Kapalı', page.status ? 'good' : 'dim'));
  }
  head.append(badge(`${num(page.chars)} karakter`, 'info'));
  box.append(head);

  if (payload.sanitized) {
    box.append(alertBox(
      'Mağazadaki içerik izin verilmeyen etiketler taşıyor (ör. script/style/iframe). '
      + 'Aşağıda temizlenmiş hâli görüyorsunuz; KAYDEDERSENİZ temizlenmiş hâli yazılır.',
      'warn',
    ));
  }
  if (payload.brokenLinks?.length) {
    box.append(alertBox(
      `Kırık iç bağlantı: ${payload.brokenLinks.map((item) => item.href).join(' · ')}. `
      + 'Adres değiştiyse Arama & SEO sekmesinden 301 ekleyin.', 'warn',
    ));
  }

  const pane = h('div', 'cm-pane');
  const tabs = tabBar([
    { key: 'general', label: 'Künye' },
    { key: 'content', label: 'İçerik' },
    { key: 'seo', label: 'SEO' },
    { key: 'versions', label: 'Sürümler' },
  ], 'general', (key) => paint(key));
  tabs.badge('versions', payload.versions?.length || undefined);
  box.append(tabs.node, pane);

  function paint(key) {
    pane.replaceChildren();
    ({
      general: paintGeneral, content: paintContent, seo: paintSeo, versions: paintVersions,
    })[key](pane, payload);
  }
  paint('general');
}

/** Künye: başlık + adres. Adres başlıktan türetilir, elle yazılınca kilitlenir. */
function paintGeneral(pane, payload) {
  const page = payload.page;
  let slugLocked = Boolean(page.slug);
  let writing = false;

  const form = formGrid({
    fields: [
      { key: 'title', label: 'Sayfa başlığı', type: 'text', required: true, maxLength: 180, wide: true },
      {
        key: 'slug',
        label: 'Adres (url_key)',
        type: 'text',
        maxLength: 180,
        hint: 'Başlıktan türetilir; elle düzelttiğinizde kilitlenir. Değişirse eski adres '
          + '404 verir — kaydettikten sonra 301 yönlendirmesi önerilir.',
      },
    ],
    value: { title: page.title, slug: page.slug },
    onChange: (draft) => {
      if (writing) return;
      const derived = slugify(draft.title);
      if (!slugLocked && derived && derived !== draft.slug) {
        writing = true;
        form.set('slug', derived);
        writing = false;
      }
      paintNotice(draft.slug);
    },
  });
  nodes.editorForms.push(form);

  // Kullanıcı adres alanına dokunduğu anda türetme durur. Alanın kendisine
  // bağlanır: olayın hedefinden etiket metnine bakmak, kit alan düzenini
  // değiştirdiği gün sessizce bozulurdu.
  const slugInput = form.node.querySelectorAll('input.kit-input')[1];
  slugInput?.addEventListener('input', () => { slugLocked = true; });

  const notice = h('div', 'cm-notice');
  function paintNotice(slug) {
    notice.replaceChildren();
    if (slug && slug !== page.slug) {
      notice.append(alertBox(
        `/${page.slug} adresi artık çalışmayacak. Kaydettikten sonra Arama & SEO `
        + `sekmesindeki URL yeniden yazma bölümünden /${page.slug} → /${slug} (301) ekleyin.`,
        'warn',
      ));
    }
  }

  const actions = h('div', 'cm-actions');
  actions.append(
    button('Kaydet', {
      variant: 'primary',
      onClick: () => savePatch(form.patch(), form, 'Künye'),
    }),
    button('Adres kilidini aç', {
      variant: 'ghost',
      title: 'Başlıktan yeniden türetilsin',
      onClick: () => { slugLocked = false; form.set('slug', slugify(form.draft().title)); },
    }),
  );

  pane.append(form.node, notice, actions, hintBox(
    'Kaydetme OKU-DEĞİŞTİR-YAZ yapar: sayfa taze okunur, dokunmadığınız alanlar mağazadaki '
    + 'güncel değeriyle geri gönderilir. Yazmadan önce sunucu eski hâli sürüm tablosuna '
    + 'kaydeder; sürüm alınamazsa yazma hiç yapılmaz.',
  ));
}

/**
 * İçerik: zengin metin düzenleyici + beyaz listeli canlı önizleme.
 *
 * ESKİDEN NE VARDI: bir HTML kaynak kutusu ve seçimin etrafına etiket saran
 * düğmeler. "Kalın" düğmesi metnin başına `<strong>` yazıyordu — yani
 * kullanıcı yazdığı şeyi değil, işaretlemesini görüyordu. Renk hiç yoktu.
 * Şimdi biçim doğrudan metnin üzerinde uygulanıyor; HTML'i görmek isteyen
 * düzenleyicideki "Kaynak" düğmesine basıyor.
 */
function paintContent(pane, payload) {
  let value = payload.content || '';

  const preview = h('div', 'cm-preview');
  const repaint = () => preview.replaceChildren(renderSafe(value));

  const previewSoon = debounce(repaint, 250);
  nodes.previewSoon = previewSoon;

  const editor = richText({
    value,
    placeholder: 'Sayfa metnini yazın. Biçim yukarıdaki araç çubuğundan verilir.',
    onChange: (html) => { value = html; previewSoon(); },
  });
  nodes.contentEditor = editor;
  repaint();

  const actions = h('div', 'cm-actions');
  actions.append(
    button('İçeriği kaydet', {
      variant: 'primary',
      onClick: () => {
        if (value === payload.content) { toast('İçerik değişmedi.', 'warn'); return; }
        savePatch({ content: value }, null, 'İçerik');
      },
    }),
    button('Değişikliği geri al', {
      variant: 'ghost',
      title: 'Ekrandaki düzenlemeyi bırakır; mağazadaki metne döner',
      onClick: () => {
        value = payload.content || '';
        editor.set(value);
        repaint();
      },
    }),
  );

  pane.append(
    card('İçerik', editor.node,
      'Biçim araç çubuğundan uygulanır; HTML yazmak gerekmez. İzin verilen '
      + 'etiketler: p h1-h4 ul ol li a img strong em u span br table td th'),
    card('Önizleme', preview, 'iframe kullanılmaz — etiketler beyaz listeden geçirilerek çizilir'),
    actions,
  );
}

/** SEO: meta alanları + arama sonucu önizlemesi + karakter sayaçları. */
function paintSeo(pane, payload) {
  const page = payload.page;
  const seo = payload.seo;

  const gaugeBox = h('div', 'cm-gauges');
  const previewBox = h('div', 'cm-serp');

  const form = formGrid({
    fields: [
      {
        key: 'metaTitle', label: 'Meta başlık', type: 'text', maxLength: 120, wide: true,
        hint: `Arama sonucunda görünen başlık. ${seo.title.min}-${seo.title.max} karakter arası `
          + 'kesilmeden görünür; boşsa arama motoru sayfa başlığını kullanır.',
      },
      {
        key: 'metaDescription', label: 'Meta açıklama', type: 'textarea', maxLength: 400, wide: true,
        hint: `${seo.description.min}-${seo.description.max} karakter arası kesilmeden görünür.`,
      },
      { key: 'metaKeywords', label: 'Anahtar kelimeler', type: 'text', maxLength: 300, wide: true,
        hint: 'Arama motorları uzun süredir kullanmıyor; mağaza içi arama için doldurulur.' },
    ],
    value: {
      metaTitle: page.metaTitle,
      metaDescription: page.metaDescription,
      metaKeywords: page.metaKeywords,
    },
    onChange: (draft) => paintPreview(draft),
  });
  nodes.editorForms.push(form);

  function gauge(label, length, min, max) {
    const tone = length === 0 ? 'bad' : (length < min || length > max ? 'warn' : 'good');
    const note = length === 0 ? 'boş' : (length < min ? 'kısa' : (length > max ? 'uzun' : 'uygun'));
    const box = h('div', 'cm-gauge');
    box.append(h('span', 'cm-gauge-label', label));
    box.append(h('b', undefined, `${num(length)} / ${min}–${max}`));
    box.append(badge(note, tone));
    return box;
  }

  function paintPreview(draft) {
    gaugeBox.replaceChildren(
      gauge('Meta başlık', String(draft.metaTitle || '').length, seo.title.min, seo.title.max),
      gauge('Meta açıklama', String(draft.metaDescription || '').length,
        seo.description.min, seo.description.max),
    );
    previewBox.replaceChildren();
    const shown = String(draft.metaTitle || '') || page.title;
    previewBox.append(h('div', 'cm-serp-url', `${state.reference.siteUrl || ''}/${page.slug}`));
    previewBox.append(h('div', 'cm-serp-title', shown.slice(0, seo.title.max + 10)
      + (shown.length > seo.title.max + 10 ? '…' : '')));
    const description = String(draft.metaDescription || '');
    previewBox.append(h('div', 'cm-serp-desc',
      description ? description.slice(0, seo.description.max + 20)
        + (description.length > seo.description.max + 20 ? '…' : '')
        : 'Açıklama boş — arama motoru sayfadan kendi seçtiği bir cümleyi gösterir.'));
    if (!String(draft.metaTitle || '')) {
      previewBox.append(h('div', 'cm-dim', 'Meta başlık boş; önizlemede sayfa başlığı kullanıldı.'));
    }
  }
  paintPreview(form.draft());

  const actions = h('div', 'cm-actions');
  actions.append(button('SEO alanlarını kaydet', {
    variant: 'primary',
    onClick: () => savePatch(form.patch(), form, 'SEO'),
  }));

  pane.append(
    card('Arama sonucu önizlemesi', previewBox, 'gerçek görünüm arama motoruna göre değişir'),
    gaugeBox,
    form.node,
    actions,
  );
}

function paintVersions(pane, payload) {
  const rows = payload.versions || [];
  if (!rows.length) {
    pane.append(emptyState({
      title: 'Henüz sürüm yok',
      text: 'Sürümler bu ekrandan yapılan ilk kaydetmede oluşmaya başlar; mağaza tarafında '
        + 'yapılan düzenlemelerin eski hâli tutulamaz.',
    }));
    return;
  }

  const table = dataTable({
    columns: [
      { key: 'createdAt', label: 'Alındı', width: '150px',
        cell: (row) => (row.createdAt || '').replace('T', ' ').slice(0, 16) },
      { key: 'actor', label: 'Kim', width: 'minmax(0, 1fr)' },
      { key: 'action', label: 'İşlem', width: '92px',
        cell: (row) => badge(row.action === 'restore' ? 'geri alma' : 'kaydetme', 'dim') },
      { key: 'chars', label: 'Karakter', width: '92px', align: 'num',
        cell: (row) => num(row.chars) },
      { key: 'reason', label: 'Gerekçe', width: 'minmax(0, 1.6fr)',
        cell: (row) => clip(h('span'), row.reason || '—', 40) },
      {
        key: 'id',
        label: '',
        width: '190px',
        cell: (row) => {
          const wrap = h('span', 'cm-row-actions');
          wrap.append(
            button('Göster', { variant: 'ghost', onClick: () => showVersion(row) }),
            button('Bu sürüme dön', { variant: 'danger', onClick: () => restoreVersion(row) }),
          );
          return wrap;
        },
      },
    ],
    rows,
  });

  pane.append(table.node, hintBox(
    'Sürümler YAZMADAN ÖNCE alınır ve hiç silinmez. Geri alma da yeni bir sürüm bırakır: '
    + 'yanlış sürüme dönerseniz onu da geri alabilirsiniz.',
  ));
}

async function showVersion(row) {
  const payload = await withBusy('Sürüm okunuyor…', () => call(`${BASE}/versions/${row.id}`));
  if (!payload) return;
  const version = payload.version;
  const box = h('div', 'cm-version');
  box.append(h('div', 'cm-dim', `${version.createdAt} · ${version.actor || '—'}`));
  box.append(h('b', undefined, version.title));
  box.append(h('code', 'cm-slug', `/${version.slug}`));
  box.append(renderSafe(version.content));
  nodes.editor.querySelector('.cm-pane')?.replaceChildren(
    card(`Sürüm #${version.id}`, box, 'salt gösterim'),
    button('Sürüm listesine dön', { onClick: () => paintEditor() }),
  );
}

async function restoreVersion(row) {
  const reason = await askReason({
    title: 'Bu sürüme dön',
    description: `${row.createdAt} tarihli sürüm yürürlükteki metnin ÜSTÜNE yazılır. `
      + 'Şu anki hâl silinmez: geri almadan önce o da yeni bir sürüm olarak kaydedilir.',
    confirmLabel: 'Geri al',
  });
  if (!reason) return;
  await withBusy('Geri alınıyor…', async () => {
    const result = await call(`${BASE}/pages/${state.selectedId}/restore`, {
      method: 'POST', body: { versionId: row.id, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Sayfa eski sürüme döndü.',
      result.dryRun ? 'warn' : 'good');
    if (result.notice) toast(result.notice, 'warn');
    await openPage(state.selectedId);
  });
}

/** Tek kaydetme yolu: kirli alanlar + gerekçe → sunucu sürüm alır, sonra yazar. */
async function savePatch(patch, form, label) {
  if (!patch || !Object.keys(patch).length) {
    toast('Değişen alan yok.', 'warn');
    return;
  }
  if (form && !form.valid()) {
    form.showErrors();
    toast('Alanları düzeltin.', 'bad');
    return;
  }
  const reason = await askReason({
    title: `${label} kaydet`,
    description: `${Object.keys(patch).join(', ')} alanı değişiyor. Gerekçe denetim kaydına `
      + 'yazılır ve mağazaya başlıkla gider.',
    confirmLabel: 'Kaydet',
  });
  if (!reason) return;
  await withBusy('Kaydediliyor…', async () => {
    const result = await call(`${BASE}/pages/${state.selectedId}`, {
      method: 'PUT', body: { patch, reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Kaydedildi.',
      result.dryRun ? 'warn' : 'good');
    if (result.slugNotice) toast(result.slugNotice, 'warn');
    await openPage(state.selectedId);
    refreshPages();
  });
}

async function createPage() {
  const title = window.prompt('Yeni sayfanın başlığı');
  if (!title || !title.trim()) return;
  const reason = await askReason({
    title: 'Sayfa aç',
    description: `“${title.trim()}” başlığıyla boş bir sayfa açılır; adres başlıktan `
      + 'türetilir ve içerik düzenleyicide doldurulur.',
    confirmLabel: 'Aç',
  });
  if (!reason) return;
  await withBusy('Sayfa açılıyor…', async () => {
    const result = await call(`${BASE}/pages`, {
      method: 'POST',
      body: { title: title.trim(), slug: slugify(title), reason, dryRun: false },
    });
    toast(result.dryRun ? 'Kuru prova: sayfa açılmadı.' : `Sayfa açıldı: /${result.slug}`,
      result.dryRun ? 'warn' : 'good');
    await refreshPages();
    if (result.id) openPage(result.id);
  });
}

// ================================================================== SSS

async function renderFaq(host) {
  host.replaceChildren(skeletonRows(5, 2));
  let payload;
  try {
    payload = await call(`${BASE}/faq`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();

  if (!payload.available) {
    host.append(emptyState({
      title: 'SSS sayfası hazır değil',
      text: payload.reason || 'SSS içeriğini taşıyan sayfa bulunamadı.',
      actions: [button('Sayfalar sekmesine git', { onClick: () => nodes.tabs.select('pages') })],
    }));
    return;
  }
  if (!payload.structured) {
    host.append(alertBox(
      'Bu sayfa soru-cevap düzeninde yazılmamış (her soru bir <h3> olmalı). İçerik burada '
      + 'YENİDEN YAZILMAZ; sayfayı Sayfalar sekmesindeki kaynak düzenleyiciden düzenleyin.',
      'warn',
    ));
  }

  let pairs = (payload.pairs || []).map((item) => ({ ...item }));
  const list = h('div', 'cm-faq');

  function paint() {
    list.replaceChildren();
    if (!pairs.length) {
      list.append(hintBox('Henüz soru yok. “Soru ekle” ile başlayın.'));
    }
    pairs.forEach((pair, index) => {
      const row = h('div', 'cm-faq-row');
      const question = h('input', 'kit-input');
      question.type = 'text';
      question.value = pair.question;
      question.placeholder = 'Soru';
      question.setAttribute('aria-label', `Soru ${index + 1}`);
      question.addEventListener('input', () => { pair.question = question.value; });

      const answer = h('textarea', 'kit-textarea');
      answer.value = pair.answer;
      answer.placeholder = 'Cevap (basit HTML kullanılabilir)';
      answer.setAttribute('aria-label', `Cevap ${index + 1}`);
      answer.addEventListener('input', () => { pair.answer = answer.value; });

      const tools = h('div', 'cm-faq-tools');
      tools.append(
        button('▲', {
          title: 'Yukarı taşı',
          disabled: index === 0,
          onClick: () => { pairs.splice(index - 1, 0, pairs.splice(index, 1)[0]); paint(); },
        }),
        button('▼', {
          title: 'Aşağı taşı',
          disabled: index === pairs.length - 1,
          onClick: () => { pairs.splice(index + 1, 0, pairs.splice(index, 1)[0]); paint(); },
        }),
        button('Çıkar', {
          variant: 'danger',
          title: 'Bu soruyu listeden çıkarır; kaydedince sayfadan da silinir',
          onClick: () => { pairs.splice(index, 1); paint(); },
        }),
      );
      row.append(h('span', 'cm-faq-no', String(index + 1)), question, answer, tools);
      list.append(row);
    });
  }
  paint();

  const actions = h('div', 'cm-actions');
  actions.append(
    button('Soru ekle', { onClick: () => { pairs.push({ question: '', answer: '' }); paint(); } }),
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        const clean = pairs.filter((pair) => pair.question.trim());
        if (!clean.length) { toast('En az bir soru gerekir.', 'bad'); return; }
        const reason = await askReason({
          title: 'SSS sayfasını kaydet',
          description: `${clean.length} soru sayfanın TAMAMINI yeniden kurar; listeden `
            + 'çıkardığınız sorular sayfadan da silinir. Eski hâl sürüm olarak saklanır.',
          confirmLabel: 'Kaydet',
        });
        if (!reason) return;
        await withBusy('SSS kaydediliyor…', async () => {
          const result = await call(`${BASE}/faq`, {
            method: 'POST',
            body: {
              pairs: clean.map((pair) => ({ question: pair.question.trim(), answer: pair.answer })),
              reason,
              dryRun: false,
            },
          });
          toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.'
            : `${num(result.count)} soru kaydedildi.`, result.dryRun ? 'warn' : 'good');
        });
      },
    }),
  );

  host.append(
    card(`SSS · /${payload.slug}`, list,
      'her soru sayfada bir <h3>, cevabı sonraki başlığa kadarki metindir'),
    actions,
  );
}

// ============================================================= arama & SEO
//
// DÖRT ALT BÖLÜM tek sekmede: URL yeniden yazma · arama terimleri · eş
// anlamlılar · site haritası. Dördü de aynı soruya bakıyor: "müşteri
// aradığını bulabiliyor mu, bulduğu adres çalışıyor mu".
//
// URL YENİDEN YAZMA ESKİDEN AYRI SEKMEYDİ, buraya taşındı. Aynı kaydı iki
// ekrandan yönetmek, iki ekranın birbirinin üstüne yazması demektir; sahibi
// tek olsun.

const SEO_SECTIONS = [
  { key: 'rewrites', label: 'URL yeniden yazma' },
  { key: 'terms', label: 'Arama terimleri' },
  { key: 'synonyms', label: 'Eş anlamlılar' },
  { key: 'sitemaps', label: 'Site haritası' },
];

/** Bölümler arası köprü: sonuçsuz bir arama, eş anlamlı formuna taşınır. */
let seoBridge = '';

function dropSeoForms() {
  nodes.seoForms?.forEach((form) => form.destroy());
  nodes.seoForms = [];
}

function renderSeo(host) {
  dropSeoForms();
  const body = h('div', 'cm-seo');
  const tabs = tabBar(SEO_SECTIONS, state.seoSection, (key) => {
    state.seoSection = key;
    paintSection(key);
  });
  nodes.seoTabs = tabs;
  host.replaceChildren(tabs.node, body);

  function paintSection(key) {
    dropSeoForms();
    body.replaceChildren(skeletonRows(6, 3));
    ({
      rewrites: renderRewrites, terms: renderTerms,
      synonyms: renderSynonyms, sitemaps: renderSitemaps,
    })[key](body);
  }
  nodes.seoPaint = paintSection;
  paintSection(state.seoSection);
}

/** Bir alt bölüme geç ve gerekiyorsa formu doldur. */
function gotoSeo(key) {
  state.seoSection = key;
  nodes.seoTabs?.select(key, false);
  nodes.seoPaint?.(key);
}

// ------------------------------------------------------ URL yeniden yazma

async function renderRewrites(host) {
  dropSeoForms();          // yeniden çizim eskisinin formunu bırakmasın
  let payload;
  try {
    payload = await call(`${BASE}/redirects`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.connected) {
    host.append(alertBox(`Yönlendirmeler okunamadı — ${payload.error}`, 'bad'));
    return;
  }

  let editing = 0;                    // 0 = yeni kayıt, >0 = düzenlenen kayıt
  const mode = h('div', 'cm-dim');

  const form = formGrid({
    fields: [
      { key: 'source', label: 'Eski adres', type: 'text', required: true, maxLength: 300,
        placeholder: '/eski-sayfa', hint: 'Yalnız yol yazın; alan adı gerekmez.' },
      { key: 'target', label: 'Yeni adres', type: 'text', required: true, maxLength: 300,
        placeholder: '/yeni-sayfa' },
      { key: 'type', label: 'Tür', type: 'select', options: [
        { value: '301', label: '301 — kalıcı (arama motoru adresi günceller)' },
        { value: '302', label: '302 — geçici (eski adres korunur)' },
      ] },
      { key: 'entity', label: 'Kayıt türü', type: 'select',
        options: (payload.entities || []).map((item) => ({ value: item.value, label: item.label })),
        hint: 'Mağaza bu alanı zorunlu istiyor. Sayfa adresi değiştiyse CMS sayfası seçin.' },
    ],
    value: { source: '', target: '', type: '301', entity: 'cms_page' },
  });
  nodes.seoForms.push(form);

  function setMode(row) {
    editing = row ? row.id : 0;
    mode.replaceChildren();
    if (!row) return;
    mode.append(h('span', undefined, `#${row.id} kaydı düzenleniyor — `));
    mode.append(button('yeni kayda dön', {
      variant: 'ghost',
      onClick: () => { form.reset({ source: '', target: '', type: '301', entity: 'cms_page' }); setMode(null); },
    }));
  }

  const save = button('Yönlendirmeyi kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      if (!form.valid()) { form.showErrors(); toast('Alanları doldurun.', 'bad'); return; }
      const reason = await askReason({
        title: editing ? `#${editing} yönlendirmesini güncelle` : 'Yönlendirme ekle',
        description: `${draft.source} adresine gelen ziyaretçi ${draft.target} adresine `
          + `gönderilecek (${draft.type}). Yanlış kayıt vitrini döngüye sokabilir; sunucu `
          + 'döngü ve çakışan kaynak denetimi yapar.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Yönlendirme yazılıyor…', async () => {
        const result = await call(`${BASE}/redirects`, {
          method: 'POST',
          body: {
            source: draft.source, target: draft.target, type: Number(draft.type) || 301,
            rewriteId: editing, entity: draft.entity || 'cms_page', reason, dryRun: false,
          },
        });
        toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.'
          : `/${result.source} → ${result.target}`, result.dryRun ? 'warn' : 'good');
        renderRewrites(host);
      });
    },
  });

  async function removeRow(row) {
    const reason = await askReason({
      title: 'Yönlendirmeyi sil',
      description: `/${row.source} → ${row.target} kaydı kalkacak. Bu adrese gelen eski `
        + 'bağlantılar (arama motoru, e-posta, fatura) bundan sonra 404 verir.',
      confirmLabel: 'Sil',
    });
    if (!reason) return;
    await withBusy('Yönlendirme siliniyor…', async () => {
      const result = await call(`${BASE}/redirects/${row.id}/delete`, {
        method: 'POST', body: { reason, dryRun: false },
      });
      toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Yönlendirme silindi.',
        result.dryRun ? 'warn' : 'good');
      if (result.notice) toast(result.notice, 'warn');
      renderRewrites(host);
    });
  }

  const table = dataTable({
    columns: [
      { key: 'source', label: 'Eski adres', width: 'minmax(0, 1.3fr)',
        cell: (row) => {
          const box = h('span', 'cm-cell');
          box.append(h('code', 'cm-slug', `/${row.source}`));
          // Renk tek başına anlam taşımaz: rozetin içinde yazı var.
          if (row.conflict) box.append(badge('çakışma', 'bad'));
          return box;
        } },
      { key: 'target', label: 'Yeni adres', width: 'minmax(0, 1.3fr)' },
      { key: 'type', label: 'Tür', width: '104px',
        cell: (row) => badge(row.type === 301 ? '301 kalıcı' : `${row.type} geçici`,
          row.type === 301 ? 'info' : 'dim') },
      { key: 'entityLabel', label: 'Kayıt', width: '110px',
        cell: (row) => badge(row.entityLabel || '—', 'dim') },
      { key: 'updatedAt', label: 'Güncelleme', width: '140px',
        cell: (row) => (row.updatedAt || '—').replace('T', ' ').slice(0, 16) },
      {
        key: 'id',
        label: '',
        width: '150px',
        cell: (row) => {
          const wrap = h('span', 'cm-row-actions');
          wrap.append(
            button('Düzenle', {
              variant: 'ghost',
              onClick: () => {
                form.reset({
                  source: `/${row.source}`, target: row.target,
                  type: String(row.type), entity: row.entityType || 'cms_page',
                });
                setMode(row);
              },
            }),
            button('Sil', { variant: 'danger', onClick: () => removeRow(row) }),
          );
          return wrap;
        },
      },
    ],
    rows: payload.items || [],
    empty: emptyState({
      title: 'Yönlendirme yok',
      text: 'Sayfa ya da ürün adı değiştiğinde eski adresi buraya ekleyin; yoksa '
        + 'paylaşılmış bağlantılar 404 verir.',
    }),
  });

  if (payload.conflicts) {
    host.append(alertBox(
      `${num(payload.conflicts)} kayıt aynı kaynak adresi yönlendiriyor. Aynı adres için iki `
      + 'kural varken hangisinin uygulanacağı belli değildir; fazlasını silin.', 'warn',
    ));
  }

  host.append(
    card('Yönlendirme kuralı', h('div', undefined, form.node, mode, save)),
    card(`Kayıtlı yönlendirmeler (${num(payload.total || 0)})`, table.node),
    hintBox('301 kalıcıdır: arama motoru eski adresi bırakır ve yenisini indeksler. '
      + 'Geçici bir taşımada 302 kullanın.'),
  );
}

// -------------------------------------------------------- arama terimleri

async function renderTerms(host) {
  let onlyZero = false;
  let page = 1;
  let size = 25;

  async function load() {
    let payload;
    try {
      payload = await call(`${BASE}/search-terms?${new URLSearchParams({
        onlyZero: String(onlyZero), page: String(page), size: String(size),
      })}`);
    } catch (error) {
      host.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    host.replaceChildren();
    if (!payload.connected) {
      host.append(alertBox(`Arama terimleri okunamadı — ${payload.error}`, 'bad'));
      return;
    }

    const summary = payload.summary || {};
    host.append(kpiRow([
      { label: 'Terim', value: num(summary.total || 0) },
      { label: 'Sonuçsuz terim', value: num(summary.zero || 0),
        tone: summary.zero ? 'bad' : 'good',
        title: 'Müşteri aradı, mağaza hiçbir şey gösteremedi' },
      { label: 'Boşa giden arama', value: num(summary.zeroUses || 0),
        title: 'Sonuçsuz terimlerin toplam aranma sayısı' },
      { label: 'Toplam arama', value: num(summary.uses || 0) },
    ]));

    const chips = chipRow([{ key: 'zero', label: 'Sonuçsuz aramalar', count: summary.zero || 0 }],
      onlyZero ? 'zero' : null, (key) => { onlyZero = key === 'zero'; page = 1; load(); });
    host.append(chips.node);

    if (payload.capped) {
      host.append(alertBox('Terim listesi tavana dayandı; en eski kayıtlar gösterilmiyor.', 'warn'));
    }

    const table = dataTable({
      columns: [
        { key: 'term', label: 'Aranan', width: 'minmax(0, 1.6fr)',
          cell: (row) => {
            const box = h('span', 'cm-cell');
            box.append(clip(h('b'), row.term || '—', 46));
            if (row.zeroResults) box.append(badge('sonuç yok', 'bad'));
            if (row.redirectUrl) box.append(badge('yönlendirilmiş', 'info'));
            return box;
          } },
        { key: 'results', label: 'Sonuç', width: '92px', align: 'num',
          cell: (row) => num(row.results) },
        { key: 'uses', label: 'Arama', width: '92px', align: 'num',
          cell: (row) => num(row.uses) },
        { key: 'locale', label: 'Dil', width: '70px', cell: (row) => row.locale || '—' },
        { key: 'updatedAt', label: 'Son arama', width: '140px',
          cell: (row) => (row.updatedAt || '—').replace('T', ' ').slice(0, 16) },
        {
          key: 'id',
          label: '',
          width: '140px',
          cell: (row) => (row.zeroResults ? button('Eş anlamlı kur', {
            variant: 'ghost',
            title: 'Bu kelimeyi bir eş anlamlı grubuna ekleyerek aramayı kurtarın',
            onClick: () => { seoBridge = row.term; gotoSeo('synonyms'); },
          }) : h('span', 'cm-dim', '—')),
        },
      ],
      rows: payload.items || [],
      dense: true,
      empty: emptyState({
        title: onlyZero ? 'Sonuçsuz arama yok' : 'Arama terimi yok',
        text: onlyZero ? 'Aranan her kelime en az bir sonuç döndürmüş.'
          : 'Vitrinde henüz arama yapılmamış ya da kayıtlar temizlenmiş.',
      }),
    });

    host.append(
      card('Aranan kelimeler', table.node, 'sonuçsuzlar en üstte, çok arananlar önce'),
      pager({
        total: payload.total || 0, page: payload.page || 1, size: payload.size || size,
        sizes: [25, 50],
        onChange: (next) => { page = next.page; size = next.size; load(); },
      }).node,
      hintBox('SONUÇSUZ ARAMALAR bu ekrandaki en değerli bilgidir: müşteri o ürünü '
        + 'istiyor ama katalog karşılık vermiyor. Ya ürün eksiktir, ya da müşteri başka '
        + 'bir kelime kullanıyordur — ikincisi eş anlamlı grubuyla çözülür.'),
    );
  }

  await load();
}

// ---------------------------------------------------------- eş anlamlılar

async function renderSynonyms(host) {
  dropSeoForms();          // yeniden çizim eskisinin formunu bırakmasın
  let payload;
  try {
    payload = await call(`${BASE}/synonyms`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.connected) {
    host.append(alertBox(`Eş anlamlılar okunamadı — ${payload.error}`, 'bad'));
    return;
  }

  let editing = 0;
  const mode = h('div', 'cm-dim');

  const form = formGrid({
    fields: [
      { key: 'name', label: 'Grup adı', type: 'text', required: true, maxLength: 120,
        placeholder: 'Kalem', hint: 'Yalnız sizin göreceğiniz ad; müşteriye gösterilmez.' },
      { key: 'terms', label: 'Kelimeler', type: 'text', required: true, maxLength: 800, wide: true,
        placeholder: 'kalem, tükenmez, pen',
        hint: 'Virgülle ayırın. Gruptaki kelimelerden biri arandığında diğerleri de sonuç '
          + 'verir; en az iki kelime gerekir.' },
    ],
    value: { name: '', terms: seoBridge ? `${seoBridge}, ` : '' },
  });
  nodes.seoForms.push(form);
  if (seoBridge) {
    host.append(hintBox(`“${seoBridge}” sonuçsuz aramadan geldi. Yanına mağazanın bildiği `
      + 'kelimeyi yazın (ör. katalogdaki adı) ve grubu kaydedin.'));
    seoBridge = '';
  }

  function setMode(row) {
    editing = row ? row.id : 0;
    mode.replaceChildren();
    if (!row) return;
    mode.append(h('span', undefined, `#${row.id} grubu düzenleniyor — `));
    mode.append(button('yeni gruba dön', {
      variant: 'ghost',
      onClick: () => { form.reset({ name: '', terms: '' }); setMode(null); },
    }));
  }

  const save = button('Grubu kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      if (!form.valid()) { form.showErrors(); toast('Alanları doldurun.', 'bad'); return; }
      const reason = await askReason({
        title: editing ? `#${editing} grubunu güncelle` : 'Eş anlamlı grubu ekle',
        description: `“${draft.terms}” kelimeleri aynı sonucu verecek. Arama sonucunu `
          + 'doğrudan etkiler; sunucu aynı kelimenin başka grupta olup olmadığını denetler.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Grup yazılıyor…', async () => {
        const result = await call(`${BASE}/synonyms`, {
          method: 'POST',
          body: { name: draft.name, terms: draft.terms, synonymId: editing, reason, dryRun: false },
        });
        toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.'
          : `“${result.name}” kaydedildi.`, result.dryRun ? 'warn' : 'good');
        renderSynonyms(host);
      });
    },
  });

  async function removeRow(row) {
    const reason = await askReason({
      title: 'Grubu sil',
      description: `“${row.name}” grubu kalkacak; ${row.terms.join(', ')} kelimeleri artık `
        + 'birbirinin yerine geçmeyecek.',
      confirmLabel: 'Sil',
    });
    if (!reason) return;
    await withBusy('Grup siliniyor…', async () => {
      const result = await call(`${BASE}/synonyms/${row.id}/delete`, {
        method: 'POST', body: { reason, dryRun: false },
      });
      toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : 'Grup silindi.',
        result.dryRun ? 'warn' : 'good');
      renderSynonyms(host);
    });
  }

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Grup', width: 'minmax(0, 1fr)' },
      { key: 'terms', label: 'Kelimeler', width: 'minmax(0, 2.2fr)',
        cell: (row) => {
          const box = h('span', 'cm-cell');
          for (const word of row.terms || []) box.append(badge(word, 'dim'));
          return box;
        } },
      { key: 'count', label: 'Kelime', width: '86px', align: 'num',
        cell: (row) => num(row.count) },
      {
        key: 'id',
        label: '',
        width: '150px',
        cell: (row) => {
          const wrap = h('span', 'cm-row-actions');
          wrap.append(
            button('Düzenle', {
              variant: 'ghost',
              onClick: () => { form.reset({ name: row.name, terms: row.terms.join(', ') }); setMode(row); },
            }),
            button('Sil', { variant: 'danger', onClick: () => removeRow(row) }),
          );
          return wrap;
        },
      },
    ],
    rows: payload.items || [],
    empty: emptyState({
      title: 'Eş anlamlı grubu yok',
      text: 'Müşteri “tükenmez” arayıp katalogdaki “kalem” ürününü bulamıyorsa buraya bir '
        + 'grup ekleyin.',
    }),
  });

  host.append(
    card('Eş anlamlı grubu', h('div', undefined, form.node, mode, save)),
    card(`Kayıtlı gruplar (${num(payload.total || 0)})`, table.node),
    hintBox('Eş anlamlı, arama sonucunu düzeltir; ürünü yeniden adlandırmaz. Aynı kelimeyi '
      + 'iki gruba koymayın — hangi grubun genişleteceği belli olmaz.'),
  );
}

// ---------------------------------------------------------- site haritası

async function renderSitemaps(host) {
  dropSeoForms();          // yeniden çizim eskisinin formunu bırakmasın
  let payload;
  try {
    payload = await call(`${BASE}/sitemaps`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.connected) {
    host.append(alertBox(`Site haritaları okunamadı — ${payload.error}`, 'bad'));
    return;
  }

  const summary = payload.summary || {};
  host.append(kpiRow([
    { label: 'Tanım', value: num(summary.total || 0) },
    { label: 'Hiç üretilmemiş', value: num(summary.never || 0),
      tone: summary.never ? 'warn' : 'good' },
    { label: 'Son üretim',
      value: (summary.lastGeneratedAt || '—').replace('T', ' ').slice(0, 16),
      title: 'Bu tarihten sonra eklenen ürünler site haritasında yok' },
  ]));

  const form = formGrid({
    fields: [
      { key: 'fileName', label: 'Dosya adı', type: 'text', required: true, maxLength: 120,
        placeholder: 'sitemap.xml', hint: '`.xml` ile bitmeli; klasör içeremez.' },
      { key: 'path', label: 'Yol', type: 'text', required: true, maxLength: 200,
        placeholder: '/', hint: 'Dosyanın yazılacağı klasör. Kök için `/` yazın.' },
    ],
    value: { fileName: 'sitemap.xml', path: '/' },
  });
  nodes.seoForms.push(form);

  const save = button('Tanımı kaydet', {
    variant: 'primary',
    onClick: async () => {
      const draft = form.draft();
      if (!form.valid()) { form.showErrors(); toast('Alanları doldurun.', 'bad'); return; }
      const reason = await askReason({
        title: 'Site haritası tanımla',
        description: `${draft.path}${draft.fileName} tanımlanacak. TANIM DOSYAYI ÜRETMEZ; `
          + 'üretim listedeki “Üret” düğmesiyle yapılır.',
        confirmLabel: 'Kaydet',
      });
      if (!reason) return;
      await withBusy('Tanım yazılıyor…', async () => {
        const result = await call(`${BASE}/sitemaps`, {
          method: 'POST',
          body: { fileName: draft.fileName, path: draft.path, sitemapId: 0, reason, dryRun: false },
        });
        toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.' : `${result.fileName} tanımlandı.`,
          result.dryRun ? 'warn' : 'good');
        if (result.notice) toast(result.notice, 'warn');
        renderSitemaps(host);
      });
    },
  });

  async function generate(row) {
    const reason = await askReason({
      title: 'Site haritasını üret',
      description: `${row.fileName} yeniden yazılacak. AĞIR İŞTİR: yayındaki her kategori, `
        + 'ürün ve sayfa dolaşılır (1.419 ürün) ve istek dakikalar sürebilir.',
      confirmLabel: 'Üret',
    });
    if (!reason) return;
    await withBusy('Site haritası üretiliyor — bu iş uzun sürebilir…', async () => {
      const result = await call(`${BASE}/sitemaps/${row.id}/generate`, {
        method: 'POST', body: { reason, dryRun: false },
      });
      toast(result.dryRun ? 'Kuru prova: istek gönderilmedi.'
        : `Üretildi${result.files ? ` · ${num(result.files)} dosya` : ''}.`,
        result.dryRun ? 'warn' : 'good');
      renderSitemaps(host);
    });
  }

  const table = dataTable({
    columns: [
      { key: 'fileName', label: 'Dosya', width: 'minmax(0, 1.2fr)',
        cell: (row) => {
          const box = h('span', 'cm-cell');
          box.append(h('code', 'cm-slug', row.fileName));
          if (!row.generatedAt) box.append(badge('hiç üretilmedi', 'warn'));
          return box;
        } },
      { key: 'path', label: 'Yol', width: 'minmax(0, 1fr)' },
      { key: 'url', label: 'Adres', width: 'minmax(0, 1.6fr)',
        cell: (row) => clip(h('span'), row.url || '—', 46) },
      { key: 'generatedAt', label: 'Son üretim', width: '150px',
        cell: (row) => (row.generatedAt || 'hiç').replace('T', ' ').slice(0, 16) },
      {
        key: 'id',
        label: '',
        width: '96px',
        cell: (row) => button('Üret', { variant: 'primary', onClick: () => generate(row) }),
      },
    ],
    rows: payload.items || [],
    empty: emptyState({
      title: 'Site haritası tanımı yok',
      text: 'Arama motoru ürünleri kendi bulur ama site haritası bulmayı hızlandırır. '
        + 'Yukarıdaki formdan sitemap.xml tanımlayın, sonra üretin.',
    }),
  });

  host.append(
    card('Yeni tanım', h('div', undefined, form.node, save)),
    card(`Tanımlar (${num((payload.items || []).length)})`, table.node),
    hintBox('Tanım dosyayı üretmez. Katalog değiştikçe site haritası ESKİR; ürün ekleyip '
      + 'çıkardıktan sonra yeniden üretin.'),
  );
}

// ================================================================ bloklar

async function renderBlocks(host) {
  host.replaceChildren(skeletonRows(4, 2));
  let payload;
  try {
    payload = await call(`${BASE}/blocks`);
  } catch (error) {
    host.replaceChildren(alertBox(error.message, 'bad'));
    return;
  }
  host.replaceChildren();
  if (!payload.connected) {
    host.append(alertBox(`Bloklar okunamadı — ${payload.error}`, 'warn'));
    return;
  }
  host.append(hintBox(payload.notice));
  if (!(payload.items || []).length) {
    host.append(emptyState({ title: 'Blok yok', text: 'Bu kanalda tanımlı statik blok bulunamadı.' }));
    return;
  }
  for (const block of payload.items) {
    const head = h('div', 'cm-block-head');
    head.append(h('b', undefined, block.name));
    head.append(badge(block.type || 'tür yok', 'dim'));
    head.append(badge(block.status ? 'açık' : 'kapalı', block.status ? 'good' : 'dim'));
    head.append(badge(`${num(block.chars)} karakter`, 'info'));
    const body = h('div', undefined, head, renderSafe(block.html));
    host.append(card(`#${block.id}`, body, (block.channels || []).join(', ')));
  }
}

// ================================================================= menüler

function renderMenus(host) {
  host.replaceChildren();
  host.append(emptyState({
    title: 'Menü yönetimi henüz açık değil',
    text: 'Vitrin menüsü için mağaza tarafında uç yok. Geçitteki tek menü ucu YÖNETİCİ '
      + 'PANELİNİN menüsüdür; onu buradan düzenlemek personelin Bagisto arayüzünü '
      + 'bozmasına kapı açardı. `bbd_cms_menus` ve `bbd_save_cms_menu` uçları yayınlanınca '
      + 'bu sekme kendiliğinden çalışacak. O zamana kadar menü Bagisto yönetici '
      + 'arayüzünden düzenlenir.',
  }));
}

// ==================================================================== CSV

function exportVisible() {
  const headers = ['Başlık', 'Adres', 'Dal', 'Karakter', 'SEO', 'Kırık bağlantı'];
  const rows = state.items.map((row) => [
    row.title, `/${row.slug}`, row.groupLabel, row.chars,
    row.seoComplete ? 'tam' : 'eksik', row.brokenLinks,
  ]);
  const written = csvBlob(headers, rows, 'cms-sayfalari');
  toast(`${num(written)} satır indirildi.`, 'good');
}

async function exportAll() {
  await withBusy('Liste yazılıyor…', async () => {
    const result = await call(`${BASE}/export`, { method: 'POST', body: {} });
    toast(`${num(result.rows)} satır yazıldı: ${result.name}`, 'good');
    nodes.status.set(`Dosya: ${result.path}`);
  });
}

// ================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  api = ctx.api;

  const view = h('div', 'kit-panel cm');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);
  report = reportChain({ api, root: view, toast, base: BASE });
  nodes.editorForms = [];
  nodes.seoForms = [];

  nodes.tabs = tabBar([
    { key: 'pages', label: 'Sayfalar' },
    { key: 'menus', label: 'Menüler' },
    { key: 'blocks', label: 'Bloklar' },
    { key: 'faq', label: 'SSS' },
    { key: 'seo', label: 'Arama & SEO' },
  ], 'pages', (key) => showView(key));

  nodes.filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Başlık, adres veya İÇERİK METNİ ara', width: '300px' },
      // `hidden: true` BAŞLANGIÇ HÂLİ: kanal listesi `reference` isteğinden
      // sonra geliyor ve kutunun çizilip çizilmeyeceğine ancak o zaman karar
      // verilebiliyor. Önce gizli çizip sonra açmak, bir an görünüp kaybolan
      // kutudan iyidir.
      { kind: 'select', key: 'channel', label: 'Kanal', hidden: true, options: [] },
    ],
    // 500 ms: her tuşta iki istek (sayfalar + yönlendirmeler) gidiyor ve
    // geçidin hız kovası dakikada 55 istekte tutuluyor; 260 ms'lik varsayılan
    // hızlı yazan personelde payı gereksiz yere tüketiyordu.
    debounceMs: 500,
    onChange: () => refreshPages(),
    actions: [
      button('Yeni sayfa', { variant: 'primary', onClick: createPage }),
      button('Yenile', { onClick: () => { refreshPages(); loadLegal(); } }),
      button('⤓ Görünen', { title: 'Listeyi CSV indir', onClick: exportVisible }),
      button('⤓ Tümü', { title: 'Tüm sayfaları rapor klasörüne yaz', onClick: exportAll }),
      button('İçerik envanteri', { onClick: () => report.run('inventory', {}) }),
      button('Yasal metinler', {
        title: 'Yasal metinlerin bugünkü tam hâli — arşivlik PDF',
        onClick: () => report.run('legal', {}),
      }),
    ],
  });

  nodes.chips = chipRow(CHIPS, null, (key) => { state.chip = key; refreshPages(); });
  nodes.status = statusLine();
  nodes.legal = h('div', 'cm-legal-wrap');
  nodes.treeBox = h('div', 'cm-tree');
  nodes.editor = h('div', 'cm-editor-host');
  nodes.editor.append(emptyState({
    title: 'Soldan bir sayfa seçin',
    text: 'Yasal metinler en üstteki dalda durur. Arama içerik metninde de çalışır.',
  }));

  const pagesView = h('div', 'cm-view');
  pagesView.append(
    nodes.legal,
    nodes.filters.node,
    nodes.chips.node,
    nodes.status.node,
    splitView(card('Sayfalar', nodes.treeBox), nodes.editor, 'minmax(0, 360px) minmax(0, 1fr)'),
  );

  nodes.body = h('div', 'cm-body');
  nodes.body.append(pagesView);
  view.append(nodes.tabs.node, nodes.body);

  function showView(key) {
    dropEditor();
    dropSeoForms();
    if (key === 'pages') {
      nodes.body.replaceChildren(pagesView);
      if (!state.loaded) refreshPages();
      return;
    }
    const host = h('div', 'cm-view');
    nodes.body.replaceChildren(host);
    if (key === 'faq') renderFaq(host);
    else if (key === 'seo') renderSeo(host);
    else if (key === 'blocks') renderBlocks(host);
    else renderMenus(host);
  }

  root.replaceChildren(view);
  nodes.status.set('Sayfalar alınıyor…');
  loadReference().then(() => refreshPages());
  loadLegal();

  return () => {
    nodes.filters?.destroy();          // arama alanı bekleyen debounce tutar
    dropSeoForms();                    // Arama & SEO bölümlerinin formları
    nodes.seoTabs = null;
    nodes.seoPaint = null;
    dropEditor();
    root.replaceChildren();
    state = { ...EMPTY_STATE };
    seoBridge = '';
    busy = false;
  };
}
