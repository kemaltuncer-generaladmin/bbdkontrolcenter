// BBD Akademi — Ayarlar › Tema ve logo (F-A16, ARCHITECTURE §3.6).
//
// Akış Akademi'dedir: logo yüklenir → sunucu tohum rengi, tonal paletleri,
// açık/koyu şemayı ve kurum tokenlarını üretir, WCAG AA'ya göre düzeltir →
// sonuç TASLAKTIR → elle ince ayar (isteğe bağlı) → yayımla (sürüm artar).
// Panel renk HESAPLAMAZ; yalnız sunucunun döndürdüğü paleti çizer.
//
// CANLI ÖNİZLEME: ince ayar alanı değişince (500 ms bekleyip) yan etkisiz
// `admin_previewBranding` çağrılır; telefon görünümü dönen paletle çizilir.
// "Taslağa uygula" aynı gövdeyi `admin_patchBrandingOverrides` ile kaydeder.
// Gövdeye yalnız temel temadan FARKLI alanlar girer.
//
// Renkler `element.style` ile verilir; `innerHTML` yok. Logo, imzalı bağlantıdan
// köprüyle (fetchFile) indirilip nesne URL'siyle gösterilir; URL'ler sekme
// kapanınca bırakılır. Yükleme tek dosyadır (açık zemin logosu): köprü tek
// dosya taşır, koyu zemin logosu yoksa Akademi açık logoyu kullanır.

import { button, debounce, h, num } from '../../ui-kit/kit.js';
import { alertBox, badge, card } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  actionButton, confirmAction, empty, fileDrop, fmtDateTime, loader, problem, runAction,
  uploadProgress,
} from './common.js';

const HEX = /^#[0-9A-Fa-f]{6}$/;
const SOURCES = { logo: 'logodan üretildi', manual: 'elle ayarlandı', default: 'varsayılan tema' };
const DENSITY = { compact: 'Sıkı', standard: 'Standart', comfortable: 'Ferah' };
const MODES = { light: 'Açık zemin', dark: 'Koyu zemin' };
const TOKEN_LABELS = {
  bg: 'Zemin', surface: 'Yüzey', surface2: 'Yüzey 2', line: 'Çizgi', ink: 'Metin', muted: 'Soluk metin',
  primary: 'Birincil', onPrimary: 'Birincil üstü', deep: 'Derin zemin', onDeep: 'Derin üstü',
  deepMuted: 'Derin soluk', accent: 'Vurgu', accentSoft: 'Vurgu açık', accentInk: 'Vurgu metni',
  success: 'Başarı', successSoft: 'Başarı açık', warn: 'Uyarı', warnSoft: 'Uyarı açık',
  danger: 'Tehlike', dangerSoft: 'Tehlike açık', student: 'Öğrenci', parent: 'Veli', admin: 'Yönetici',
  wave: 'Dalga',
};
/** Elle ayarlanabilen tokenlar (her iki şemada). */
const EDITABLE = ['primary', 'accent', 'deep'];

const color = (value, fallback) => (HEX.test(String(value || '')) ? value : fallback);
const fontStack = (family) => {
  const clean = String(family || '').replace(/["';{}]/g, '').trim();
  return clean ? `"${clean}", system-ui, sans-serif` : 'system-ui, sans-serif';
};

export function brandingTab(app) {
  const node = h('div', 'ak-stack ak-set-theme');
  const logos = new Map();            // imzalı URL → Promise<nesne URL | null>
  let state = null;                   // {published, draft}
  let previewTheme = null;            // son önizleme (kaydedilmedi)
  let source = 'draft';               // draft | published | preview
  let mode = 'light';
  let form = null;
  let previewToken = 0;

  const view = loader(() => app.api.getCached('/v1/admin/branding'), (data) => {
    state = data || {};
    previewTheme = null;
    source = state.draft ? 'draft' : 'published';
    return render();
  }, { rows: 6, columns: 4 });
  node.append(view.node);

  const base = () => state?.draft || state?.published || null;
  const shownTheme = () => (source === 'preview' && previewTheme) || (source === 'draft' && state?.draft)
    || state?.published || null;

  // --------------------------------------------------------------- çizim
  const previewSlot = h('div', 'ak-set-th-preview');
  const contrastSlot = h('div');
  const tokensSlot = h('div');
  const segSlot = h('div', 'ak-inline');

  function render() {
    form?.destroy();
    const head = statusStrip();
    const left = h('div', 'ak-stack');
    left.append(logoCard());
    form = overridesForm();
    left.append(form.node);
    const right = h('div', 'ak-stack');
    right.append(card('Önizleme', previewBody(), 'Uygulamanın kurum temasıyla görünümü'),
      contrastSlot, tokensSlot);
    const grid = h('div', 'ak-grid-2 ak-set-th-grid');
    grid.append(left, right);
    paintPreview();
    return [head, grid];
  }

  function statusStrip() {
    const strip = h('div', 'kit-card ak-set-th-status');
    const published = state?.published;
    const draft = state?.draft;
    const info = h('div', 'ak-cell-stack');
    const line = h('div', 'ak-inline');
    line.append(h('b', '', published ? `Yayımlanan tema: sürüm ${published.version}` : 'Yayımlanmış tema yok'));
    if (published) {
      line.append(h('span', 'ak-muted', `${SOURCES[published.source] || published.source} · `
        + `${fmtDateTime(published.generatedAt)}`));
    }
    info.append(line);
    const second = h('div', 'ak-inline');
    if (draft) {
      second.append(badge('Taslak var', 'warn'), h('span', 'ak-muted',
        `${SOURCES[draft.source] || draft.source} · ${fmtDateTime(draft.generatedAt)} · yayımlanınca `
        + `sürüm ${(published?.version || 0) + 1} olur`));
    } else {
      second.append(h('span', 'ak-muted', 'Taslak yok. Logo yükleyin ya da ince ayar yapın; sonuç önce taslak olur.'));
    }
    info.append(second);
    const publish = actionButton(app, 'admin_publishBranding', 'Taslağı yayımla', { variant: 'primary',
      onClick: () => publishDraft(publish) });
    if (!draft && publish.dataset.blocked !== '1') {
      publish.disabled = true;
      publish.title = 'Yayımlanacak taslak yok.';
    }
    strip.append(info, h('span', 'kit-spacer'), publish);
    return strip;
  }

  function logoCard() {
    const body = h('div', 'ak-stack ak-card-pad');
    const progressSlot = h('div');
    const canUpload = app.can('admin_uploadLogo');
    const drop = fileDrop({ accept: ['png', 'svg', 'jpg', 'jpeg'], maxMb: 2,
      label: 'Logo seçin ya da buraya bırakın', onFile: (file) => upload(file, drop, progressSlot) });
    if (!canUpload) {
      body.append(alertBox('Logo yükleme yetkiniz yok (yöneticinizden izin isteyin).', 'warn'));
    } else {
      body.append(drop.node, progressSlot);
    }
    body.append(h('div', 'ak-muted', 'Şeffaf zeminli PNG ya da SVG en iyi sonucu verir. Renkler logodan '
      + 'çıkarılır, kontrast AA\'ya göre düzeltilir; sonuç taslaktır, yayımlamadan uygulamalara gitmez.'));
    const current = base();
    if (current?.logo?.url) {
      const shown = h('div', 'ak-set-th-logos');
      shown.append(logoTile(current.logo.url, 'Açık zemin', current.light?.bg),
        logoTile(current.logo.darkUrl || current.logo.url, 'Koyu zemin', current.dark?.bg));
      body.append(shown);
    }
    return card('Logo', body, state?.draft ? 'Taslaktaki logo' : 'Yayımlanan logo');
  }

  function logoTile(url, label, background) {
    const tile = h('div', 'ak-set-th-logo-tile');
    const frame = h('div', 'ak-set-th-logo-frame');
    frame.style.background = color(background, '#FFFFFF');
    const note = h('small', 'ak-muted', 'Yükleniyor…');
    frame.append(logoImage(url, 'ak-set-th-logo-img'), note);
    logos.get(url)?.then((src) => {
      if (src) note.remove();
      else note.textContent = 'Önizlenemedi';
    });
    tile.append(frame, h('small', 'ak-muted', label));
    return tile;
  }

  /** Logo görseli: imzalı bağlantı köprüden indirilir; olmazsa yazı kalır. */
  function logoImage(url, className) {
    const img = h('img', className);
    img.alt = 'Kurum logosu';
    img.hidden = true;
    if (!url) return img;
    if (!logos.has(url)) {
      logos.set(url, app.api.fetchFile(url, 'logo')
        .then(({ blob }) => URL.createObjectURL(blob)).catch(() => null));
    }
    logos.get(url).then((src) => {
      if (src) { img.src = src; img.hidden = false; }
    });
    return img;
  }

  async function upload(file, drop, slot) {
    const bar = uploadProgress();
    slot.replaceChildren(bar.node);
    try {
      const result = await app.api.call('PUT', '/v1/admin/branding/logo', {
        upload: { file, field: 'file', onProgress: (value) => {
          bar.reading(value);
          if (value >= 100) bar.sending();
        } },
      });
      bar.done('Tema taslağı üretildi');
      app.api.invalidate('/v1/admin/branding');
      state = result.data || state;
      previewTheme = null;
      source = 'draft';
      app.toast('Logodan tema taslağı üretildi. Önizleyip yayımlayın.', 'good');
      view.node.replaceChildren(...render());
    } catch (failure) {
      slot.replaceChildren(alertBox(problem(failure, 'Logo yüklenemedi.'), 'bad'));
      drop.clear();
    }
  }

  // ------------------------------------------------------------ ince ayar
  function overridesForm() {
    const theme = base();
    const wrap = h('div', 'ak-stack ak-card-pad');
    const values = {
      seedColor: theme?.seedColor || '',
      light: Object.fromEntries(EDITABLE.map((token) => [token, theme?.light?.[token] || ''])),
      dark: Object.fromEntries(EDITABLE.map((token) => [token, theme?.dark?.[token] || ''])),
      density: theme?.density || 'standard',
      autoFixContrast: true,
    };
    const rows = [];
    // Önizleme yan etkisizdir ama ayrı bir işlemdir; yetki yoksa yalnız kaydedilir.
    const canPreview = app.can('admin_previewBranding');
    const runPreview = debounce(() => preview(), 500);
    const changed = () => { paintCount(); runPreview(); };

    const seedRow = colorRow('Tohum renk', theme?.seedColor, (value) => { values.seedColor = value; changed(); },
      'Değişirse paletin tamamı bu renkten yeniden türetilir.');
    rows.push(seedRow);
    wrap.append(seedRow.node);
    for (const mode of ['light', 'dark']) {
      const group = h('div', 'ak-set-th-group');
      group.append(h('div', 'ak-set-th-group-title', MODES[mode]));
      for (const token of EDITABLE) {
        const row = colorRow(TOKEN_LABELS[token], theme?.[mode]?.[token], (value) => {
          values[mode][token] = value;
          changed();
        });
        rows.push(row);
        group.append(row.node);
      }
      wrap.append(group);
    }

    const densityRow = h('label', 'ak-set-th-row ak-set-th-row-select');
    const densitySelect = h('select', 'kit-select');
    for (const [value, label] of Object.entries(DENSITY)) {
      const option = h('option', '', label);
      option.value = value;
      densitySelect.append(option);
    }
    densitySelect.value = values.density;
    densitySelect.addEventListener('change', () => { values.density = densitySelect.value; changed(); });
    densityRow.append(h('span', 'ak-set-th-row-label', 'Yoğunluk'), densitySelect);

    const fixRow = h('label', 'ak-set-th-row ak-set-th-row-check');
    const fix = h('input', 'kit-check');
    fix.type = 'checkbox';
    fix.checked = true;
    fix.addEventListener('change', () => { values.autoFixContrast = fix.checked; changed(); });
    fixRow.append(fix, h('span', '', 'Kontrastı kendiliğinden düzelt (WCAG AA)'));
    wrap.append(densityRow, fixRow);

    const count = h('span', 'ak-muted');
    const reset = button('Geri al', { variant: 'ghost',
      onClick: () => { previewTheme = null; view.node.replaceChildren(...render()); } });
    const apply = actionButton(app, 'admin_patchBrandingOverrides', 'Taslağa uygula', { variant: 'primary',
      onClick: () => save(apply) });
    const actions = h('div', 'ak-inline');
    actions.append(count, h('span', 'kit-spacer'), reset, apply);
    wrap.append(actions);

    function body() {
      const out = {};
      let changes = 0;
      if (theme && values.seedColor && values.seedColor !== theme.seedColor) {
        out.seedColor = values.seedColor;
        changes += 1;
      }
      for (const mode of ['light', 'dark']) {
        for (const token of EDITABLE) {
          const value = values[mode][token];
          if (value && value !== theme?.[mode]?.[token]) {
            out[mode] = { ...(out[mode] || {}), [token]: value };
            changes += 1;
          }
        }
      }
      if (values.density !== (theme?.density || 'standard')) {
        out.density = values.density;
        changes += 1;
      }
      // Otomatik düzeltme varsayılan açıktır; yalnız kapatılınca gönderilir.
      if (!values.autoFixContrast) {
        out.autoFixContrast = false;
        changes += 1;
      }
      return { out, changes };
    }
    const valid = () => rows.every((row) => row.valid());
    function paintCount() {
      const { changes } = body();
      count.textContent = !valid() ? 'Renkleri #RRGGBB biçiminde yazın.'
        : changes ? `${changes} değişiklik${canPreview ? ' · önizlemede' : ''}` : 'Değişiklik yok';
      if (apply.dataset.blocked !== '1') apply.disabled = !changes || !valid();
      if (reset.dataset.blocked !== '1') reset.disabled = !changes;
    }

    async function preview() {
      const mine = ++previewToken;
      const { out, changes } = body();
      if (!valid() || !canPreview) return;
      if (!changes) {
        previewTheme = null;
        if (source === 'preview') source = state?.draft ? 'draft' : 'published';
        paintPreview();
        return;
      }
      try {
        const result = await app.api.post('/v1/admin/branding/preview', out);
        if (mine !== previewToken) return;
        previewTheme = result;
        source = 'preview';
        paintPreview();
      } catch (failure) {
        if (mine !== previewToken) return;
        previewSlot.replaceChildren(alertBox(problem(failure, 'Önizleme alınamadı.'), 'bad'));
      }
    }

    async function save(control) {
      const { out, changes } = body();
      if (!changes || !valid()) return;
      const next = await runAction(app, () => app.api.patch('/v1/admin/branding/overrides', out),
        { success: 'İnce ayar taslağa uygulandı.', button: control });
      if (!next) return;
      app.api.invalidate('/v1/admin/branding');
      state = next;
      previewTheme = null;
      source = 'draft';
      view.node.replaceChildren(...render());
    }

    paintCount();
    const box = card('İnce ayar', wrap, state?.draft ? 'Taslağın üstüne' : 'Yayımlanan temadan yeni taslak');
    return { node: box, destroy: () => runPreview.cancel() };
  }

  // ------------------------------------------------------------- önizleme
  function previewBody() {
    const wrap = h('div', 'ak-stack ak-card-pad');
    wrap.append(segSlot, previewSlot);
    return wrap;
  }

  function paintSegments() {
    const options = [];
    if (previewTheme) options.push(['preview', 'Önizleme (kaydedilmedi)']);
    if (state?.draft) options.push(['draft', 'Taslak']);
    if (state?.published) options.push(['published', `Yayımlanan (s. ${state.published.version})`]);
    if (options.length && !options.some(([key]) => key === source)) [[source]] = options;
    segSlot.replaceChildren(
      segmented(options, source, (value) => { source = value; paintPreview(); }),
      segmented(Object.entries(MODES), mode, (value) => { mode = value; paintPreview(); }),
    );
  }

  function paintPreview() {
    paintSegments();
    const theme = shownTheme();
    if (!theme) {
      previewSlot.replaceChildren(empty('Tema yok', 'Logo yükleyince tema taslağı üretilir.'));
      contrastSlot.replaceChildren();
      tokensSlot.replaceChildren();
      return;
    }
    const logoUrl = mode === 'dark' ? (theme.logo?.darkUrl || theme.logo?.url) : theme.logo?.url;
    previewSlot.replaceChildren(phoneMock(theme, mode, logoImage(logoUrl, 'ak-set-th-phone-logo')));
    contrastSlot.replaceChildren(contrastCard(theme));
    tokensSlot.replaceChildren(tokensCard(theme, mode));
  }

  async function publishDraft(control) {
    const draft = state?.draft;
    if (!draft) return;
    const failures = draft.contrast?.failures?.length || 0;
    const ok = await confirmAction(app, {
      title: 'Tema yayımlansın mı?',
      description: `Taslak, sürüm ${(state.published?.version || 0) + 1} olarak yayımlanır; öğrenci, veli ve `
        + 'yönetici uygulamaları yeni temayı bir sonraki açılışta alır.'
        + `${failures ? ` Dikkat: ${failures} renk çifti AA eşiğinin altında; Akademi yayımlamayı reddedebilir.` : ''}`,
      confirmLabel: 'Yayımla',
    });
    if (!ok) return;
    const result = await runAction(app, () => app.api.post('/v1/admin/branding/publish', undefined,
      { key: `branding-publish-${draft.version}-${draft.generatedAt}`.slice(0, 120) }),
    { button: control });
    if (!result) return;
    app.toast(`Tema yayımlandı (sürüm ${result.version}).`, 'good');
    app.api.invalidate('/v1/admin/branding');
    view.reload();
  }

  return {
    node,
    reload: () => view.reload(),
    destroy() {
      form?.destroy();
      for (const pending of logos.values()) {
        pending.then((src) => { if (src) URL.revokeObjectURL(src); });
      }
      logos.clear();
    },
  };
}

// ------------------------------------------------------------- parçalar

/** Renk satırı: etiket + canlı renk kutusu + `#RRGGBB` alanı. */
function colorRow(label, initial, onChange, hint = '') {
  const node = h('label', 'ak-set-th-row');
  const swatch = h('span', 'ak-set-th-swatch');
  const input = h('input', 'kit-input ak-set-th-hex');
  input.type = 'text';
  input.maxLength = 7;
  input.placeholder = '#RRGGBB';
  input.value = initial || '';
  input.spellcheck = false;
  const paint = () => {
    const value = input.value.trim();
    const ok = !value || HEX.test(value);
    input.classList.toggle('bad', !ok);
    swatch.style.background = HEX.test(value) ? value : 'transparent';
  };
  input.addEventListener('input', () => {
    paint();
    const value = input.value.trim();
    if (!value || HEX.test(value)) onChange(value ? value.toUpperCase() : '');
    else onChange(value);
  });
  input.addEventListener('blur', () => {
    if (HEX.test(input.value.trim())) input.value = input.value.trim().toUpperCase();
  });
  const text = h('span', 'ak-set-th-row-label', label);
  node.append(text, swatch, input);
  if (hint) node.title = hint;
  paint();
  return { node, valid: () => !input.value.trim() || HEX.test(input.value.trim()) };
}

/** Tek seçimli düğme şeridi. */
function segmented(options, value, onChange) {
  const node = h('div', 'ak-set-th-seg');
  node.setAttribute('role', 'group');
  for (const [key, label] of options) {
    const item = h('button', `ak-set-th-seg-item${key === value ? ' on' : ''}`, label);
    item.type = 'button';
    item.setAttribute('aria-pressed', key === value ? 'true' : 'false');
    item.addEventListener('click', () => { if (key !== value) onChange(key); });
    node.append(item);
  }
  return node;
}

/** Telefon görünümü: dönen paletle örnek ekran (renkler `style` ile). */
function phoneMock(theme, mode, logo) {
  const t = theme?.[mode] || {};
  const radius = theme?.radius || {};
  const pad = { compact: 8, standard: 12, comfortable: 16 }[theme?.density] || 12;
  const display = fontStack(theme?.fonts?.display?.family);
  const px = (value, fallback) => `${Number.isFinite(Number(value)) ? Number(value) : fallback}px`;

  const frame = h('div', 'ak-set-th-phone');
  frame.style.background = color(t.bg, '#FFFFFF');
  frame.style.color = color(t.ink, '#111722');
  frame.style.fontFamily = fontStack(theme?.fonts?.body?.family);

  const bar = h('div', 'ak-set-th-appbar');
  bar.style.background = color(t.deep, '#1F2A44');
  bar.style.color = color(t.onDeep, '#FFFFFF');
  bar.style.padding = `${pad + 4}px ${pad + 2}px ${pad}px`;
  const titles = h('div', 'ak-cell-stack');
  const hello = h('b', 'ak-set-th-hello', 'Günaydın, Ayşe');
  hello.style.fontFamily = display;
  const sub = h('small', '', 'Pazartesi · bugün 4 ders');
  sub.style.color = color(t.deepMuted, '#C9D2E3');
  titles.append(hello, sub);
  bar.append(logo, titles);
  const wave = h('div', 'ak-set-th-wave');
  wave.style.background = color(t.wave, color(t.primary, '#5B8CFF'));

  const screen = h('div', 'ak-set-th-screen');
  screen.style.padding = `${pad}px`;
  screen.style.gap = `${pad}px`;

  const lesson = h('div', 'ak-set-th-card');
  lesson.style.background = color(t.surface, '#FFFFFF');
  lesson.style.border = `1px solid ${color(t.line, '#E5E7EB')}`;
  lesson.style.borderRadius = px(radius.lg, 14);
  lesson.style.padding = `${pad}px`;
  const lessonTitle = h('div', 'ak-set-th-card-title', 'Matematik · Türev');
  lessonTitle.style.fontFamily = display;
  const lessonMeta = h('div', 'ak-set-th-small', '10:40 – 11:20 · 12-A · Derslik 3');
  lessonMeta.style.color = color(t.muted, '#667487');
  const chip = h('span', 'ak-set-th-chip', 'Ödev teslimi yarın');
  chip.style.background = color(t.accentSoft, '#EEF2FF');
  chip.style.color = color(t.accentInk, '#1D3F9E');
  chip.style.borderRadius = px(radius.pill, 999);
  lesson.append(lessonTitle, lessonMeta, chip);

  const goal = h('div', 'ak-set-th-card');
  goal.style.background = color(t.surface2, '#F7F8FB');
  goal.style.borderRadius = px(radius.lg, 14);
  goal.style.padding = `${pad}px`;
  const goalHead = h('div', 'ak-set-th-row-between');
  const goalTitle = h('span', '', 'Haftalık hedef');
  const goalValue = h('b', '', '%64');
  goalValue.style.color = color(t.primary, '#5B8CFF');
  goalHead.append(goalTitle, goalValue);
  const track = h('div', 'ak-set-th-track');
  track.style.background = color(t.line, '#E5E7EB');
  track.style.borderRadius = px(radius.pill, 999);
  const fill = h('div', 'ak-set-th-fill');
  fill.style.background = color(t.primary, '#5B8CFF');
  fill.style.borderRadius = px(radius.pill, 999);
  fill.style.width = '64%';
  track.append(fill);
  goal.append(goalHead, track);

  const buttons = h('div', 'ak-set-th-buttons');
  const primary = h('span', 'ak-set-th-btn', 'Devam et');
  primary.style.background = color(t.primary, '#5B8CFF');
  primary.style.color = color(t.onPrimary, '#FFFFFF');
  primary.style.borderRadius = px(radius.md, 10);
  const outline = h('span', 'ak-set-th-btn', 'Ayrıntı');
  outline.style.border = `1.5px solid ${color(t.primary, '#5B8CFF')}`;
  outline.style.color = color(t.primary, '#5B8CFF');
  outline.style.borderRadius = px(radius.md, 10);
  buttons.append(primary, outline);

  const states = h('div', 'ak-set-th-buttons');
  for (const [text, strong, soft] of [['Geldi', t.success, t.successSoft], ['Geç', t.warn, t.warnSoft],
    ['Gelmedi', t.danger, t.dangerSoft]]) {
    const pill = h('span', 'ak-set-th-chip', text);
    pill.style.color = color(strong, '#15803D');
    pill.style.background = color(soft, '#ECFDF3');
    pill.style.borderRadius = px(radius.pill, 999);
    states.append(pill);
  }

  const roles = h('div', 'ak-set-th-buttons');
  for (const [token, text] of [['student', 'Öğrenci'], ['parent', 'Veli'], ['admin', 'Yönetici']]) {
    const item = h('span', 'ak-set-th-role');
    const dot = h('span', 'ak-set-th-dot');
    dot.style.background = color(t[token], '#999999');
    const label = h('span', 'ak-set-th-small', text);
    label.style.color = color(t.muted, '#667487');
    item.append(dot, label);
    roles.append(item);
  }

  const heat = h('div', 'ak-set-th-heat');
  for (const value of Array.isArray(t.heat) ? t.heat : []) {
    const cell = h('span', 'ak-set-th-heat-cell');
    cell.style.background = color(value, 'transparent');
    cell.style.borderRadius = px(radius.sm, 4);
    heat.append(cell);
  }

  screen.append(lesson, goal, buttons, states, roles, heat);
  frame.append(bar, wave, screen);
  const shell = h('div', 'ak-set-th-device');
  shell.append(frame);
  return shell;
}

function swatch(value) {
  const node = h('span', 'ak-set-th-swatch');
  node.style.background = color(value, 'transparent');
  return node;
}

function contrastCard(theme) {
  const report = theme?.contrast;
  if (!report) return card('Kontrast denetimi', h('div', 'ak-muted ak-card-pad', 'Bu tema için kontrast bilgisi yok.'));
  const body = h('div', 'ak-stack ak-card-pad');
  const failures = report.failures || [];
  body.append(alertBox(report.passed
    ? `Bütün metin/zemin çiftleri ${report.standard} denetiminden geçti (en az ${num(report.minRatio, 1)}:1).`
    : `${num(failures.length)} çift ${report.standard} eşiğinin (${num(report.minRatio, 1)}:1) altında; `
      + 'yayımlama reddedilebilir. Rengi elle düzeltin ya da otomatik düzeltmeyi açın.',
  report.passed ? 'good' : 'bad'));
  const ratio = (value) => `${num(value, 2)}:1`;
  if ((report.adjustments || []).length) {
    body.append(h('b', '', 'Otomatik düzeltilenler'), dataTable({
      dense: true,
      rows: report.adjustments,
      rowKey: (row) => `${row.mode}-${row.token}-${row.against}`,
      columns: [
        { key: 'token', label: 'Token', width: 'minmax(0, 1.4fr)', cell: (row) =>
          `${TOKEN_LABELS[row.token] || row.token} · ${row.mode === 'dark' ? 'koyu' : 'açık'}` },
        { key: 'against', label: 'Zemin', cell: (row) => TOKEN_LABELS[row.against] || row.against || '—' },
        { key: 'change', label: 'Renk', width: '150px', cell: (row) => {
          const cell = h('span', 'ak-inline');
          cell.append(swatch(row.from), h('span', 'ak-muted', '→'), swatch(row.to), h('code', '', row.to));
          return cell;
        } },
        { key: 'ratio', label: 'Oran', align: 'num', cell: (row) => `${ratio(row.ratioBefore)} → ${ratio(row.ratioAfter)}` },
      ],
    }).node);
  }
  if (failures.length) {
    body.append(h('b', '', 'Eşiğin altında kalanlar'), dataTable({
      dense: true,
      rows: failures,
      rowKey: (row) => `${row.mode}-${row.token}-${row.against}`,
      columns: [
        { key: 'token', label: 'Token', width: 'minmax(0, 1.4fr)', cell: (row) =>
          `${TOKEN_LABELS[row.token] || row.token} · ${row.mode === 'dark' ? 'koyu' : 'açık'}` },
        { key: 'against', label: 'Zemin', cell: (row) => TOKEN_LABELS[row.against] || row.against },
        { key: 'ratio', label: 'Oran', align: 'num', cell: (row) => ratio(row.ratio) },
      ],
    }).node);
  }
  return card('Kontrast denetimi', body);
}

function tokensCard(theme, mode) {
  const palette = theme?.[mode] || {};
  const grid = h('div', 'ak-set-th-tokens ak-card-pad');
  for (const [token, label] of Object.entries(TOKEN_LABELS)) {
    if (!palette[token]) continue;
    const item = h('div', 'ak-set-th-token');
    const text = h('div', 'ak-cell-stack');
    text.append(h('span', '', label), h('code', 'ak-muted', palette[token]));
    item.append(swatch(palette[token]), text);
    grid.append(item);
  }
  const fonts = theme?.fonts;
  const meta = h('div', 'ak-muted ak-set-th-meta', [
    `Tohum ${theme?.seedColor || '—'}`,
    fonts ? `Yazı: ${fonts.display?.family || '—'} / ${fonts.body?.family || '—'}` : '',
    `Yoğunluk: ${DENSITY[theme?.density] || theme?.density || '—'}`,
  ].filter(Boolean).join(' · '));
  const box = h('div');
  box.append(grid, meta);
  return card(`Renkler — ${MODES[mode].toLocaleLowerCase('tr')}`, box);
}
