// dialogs.js — kabuğun kendi pencereleri: Tercihler (Ctrl/⌘+,), Klavye
// kısayolları (Ctrl/⌘+/), Hız ölçümleri, Hakkında. ADR 0032.
//
// Tek desen: `.km-modal` (role=dialog, aria-modal), Esc kapatır, odak içeride
// döner, kapanınca önceki öğeye geri verilir.

import { SHORTCUTS, invoke, isMac, isNative, keyLabel, prefs } from './native.js';
import { perf } from './perf.js';

let current = null;

export function closeDialog() {
  current?.close();
}

function modal(titleText, build, { wide = false } = {}) {
  closeDialog();
  const previous = document.activeElement;
  const backdrop = document.createElement('div');
  backdrop.className = 'km-modal-backdrop';
  const card = document.createElement('div');
  card.className = `km-modal${wide ? ' wide' : ''}`;
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.tabIndex = -1;
  const id = `km-dlg-${Date.now()}`;
  card.setAttribute('aria-labelledby', id);
  const head = document.createElement('div');
  head.className = 'km-modal-head';
  const title = document.createElement('h2');
  title.id = id;
  title.textContent = titleText;
  const x = document.createElement('button');
  x.type = 'button';
  x.className = 'km-modal-x';
  x.setAttribute('aria-label', 'Kapat');
  x.textContent = '✕';
  head.append(title, x);
  const body = document.createElement('div');
  body.className = 'km-modal-body';
  card.append(head, body);
  backdrop.append(card);
  document.body.append(backdrop);

  const focusables = () => [...card.querySelectorAll('button, input, select, [tabindex="0"]')]
    .filter((node) => !node.disabled && !node.hidden);
  const onKey = (event) => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    else if (event.key === 'Tab') {
      const list = focusables();
      if (!list.length) return;
      const index = list.indexOf(document.activeElement);
      const next = event.shiftKey ? (index <= 0 ? list.length - 1 : index - 1) : (index + 1) % list.length;
      event.preventDefault();
      list[next].focus();
    }
  };
  function close() {
    backdrop.remove();
    document.removeEventListener('keydown', onKey, true);
    if (current?.backdrop === backdrop) current = null;
    try { previous?.focus?.(); } catch { /* yok */ }
  }
  x.addEventListener('click', close);
  backdrop.addEventListener('mousedown', (event) => { if (event.target === backdrop) close(); });
  document.addEventListener('keydown', onKey, true);
  current = { close, backdrop };
  build(body, close);
  (focusables()[1] || card).focus();
  return { close, body };
}

function row(label, control, hint = '') {
  const wrap = document.createElement('div');
  wrap.className = 'km-pref';
  const text = document.createElement('div');
  text.className = 'km-pref-text';
  const name = document.createElement('span');
  name.className = 'km-pref-name';
  name.textContent = label;
  text.append(name);
  if (hint) {
    const small = document.createElement('span');
    small.className = 'km-pref-hint';
    small.textContent = hint;
    text.append(small);
  }
  wrap.append(text, control);
  return wrap;
}

/** Bölümlü düğme grubu (radyo) — tema, yoğunluk, yakınlaştırma. */
function segmented(name, options, value, onChange) {
  const group = document.createElement('div');
  group.className = 'km-seg';
  group.setAttribute('role', 'radiogroup');
  group.setAttribute('aria-label', name);
  const buttons = options.map(([key, label]) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'km-seg-btn';
    button.setAttribute('role', 'radio');
    button.textContent = label;
    button.dataset.value = key;
    const paint = (active) => {
      button.setAttribute('aria-checked', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
    };
    paint(key === value);
    button.addEventListener('click', () => {
      buttons.forEach((other) => other.setAttribute('aria-checked', other === button ? 'true' : 'false'));
      buttons.forEach((other) => { other.tabIndex = other === button ? 0 : -1; });
      onChange(key);
    });
    return button;
  });
  group.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const index = buttons.indexOf(document.activeElement);
    const next = buttons[(index + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
    next.focus();
    next.click();
  });
  group.append(...buttons);
  return group;
}

function toggle(label, on, onChange) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'km-switch';
  button.setAttribute('role', 'switch');
  button.setAttribute('aria-label', label);
  button.setAttribute('aria-checked', on ? 'true' : 'false');
  button.addEventListener('click', () => {
    const next = button.getAttribute('aria-checked') !== 'true';
    button.setAttribute('aria-checked', next ? 'true' : 'false');
    onChange(next);
  });
  return button;
}

/** Tercihler (Ctrl/⌘+,). Değişiklik ANINDA uygulanır; "Kaydet" yok. */
export function openPreferences({ onChange = () => {} } = {}) {
  return modal('Tercihler', (body) => {
    const set = (key, value) => { prefs.set(key, value); onChange(key, value); };
    body.append(
      row('Tema', segmented('Tema', [['system', 'Sistemi izle'], ['light', 'Açık'], ['dark', 'Koyu']],
        prefs.get('theme'), (value) => set('theme', value)),
      'Sistemi izle: işletim sistemi koyuya geçince uygulama da geçer.'),
      row('Yoğunluk', segmented('Yoğunluk', [['comfortable', 'Rahat'], ['compact', 'Sıkı']],
        prefs.get('density'), (value) => set('density', value)),
      'Sıkı: satır ve düğmeler daha alçak, ekrana daha çok kayıt sığar.'),
      row('Yakınlaştırma', segmented('Yakınlaştırma',
        [['0.9', '%90'], ['1', '%100'], ['1.1', '%110'], ['1.25', '%125'], ['1.4', '%140']],
        String(Number(prefs.get('zoom')) || 1), (value) => set('zoom', value)),
      `Kalıcıdır. Kısayol: ${keyLabel('Mod++')} / ${keyLabel('Mod+-')} / ${keyLabel('Mod+0')}`),
      row('Sistem bildirimleri', toggle('Sistem bildirimleri', prefs.get('notify') !== 'off',
        (on) => set('notify', on ? 'on' : 'off')),
      'Güncelleme hazır olduğunda ve uzun işler bittiğinde (pencere arkadayken).'),
      row('Açılışta son ekran', toggle('Açılışta son ekran', prefs.get('restoreLast') !== 'off',
        (on) => set('restoreLast', on ? 'on' : 'off')),
      'Girişten sonra en son açık olan ekranla başlar.'),
    );

    // Windows 11 Mica — pencere kurulurken okunur, bir sonraki açılışta geçerli.
    if (isNative()) {
      invoke('native_info').then(async (info) => {
        if (!info?.micaSupported) return;
        const stored = await invoke('native_get_prefs').catch(() => ({ mica: false }));
        body.append(row('Mica arka planı (Windows 11)', toggle('Mica arka planı', Boolean(stored?.mica),
          (on) => invoke('native_set_prefs', { mica: on }).catch(() => {})),
        'Kenar çubuğu masaüstü renginden ton alır. Uygulama yeniden açılınca geçerli olur.'));
      }).catch(() => {});
    }
  });
}

/** Klavye kısayolları (Ctrl/⌘+/). */
export function openShortcuts() {
  return modal('Klavye kısayolları', (body) => {
    const table = document.createElement('table');
    table.className = 'km-keys';
    const mac = isMac();
    const seen = new Set();
    for (const entry of SHORTCUTS) {
      if (entry.hidden || (entry.notMac && mac) || (entry.onlyMac && !mac)) continue;
      const key = `${entry.id}:${entry.keys}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const tr = document.createElement('tr');
      const keys = document.createElement('td');
      const kbd = document.createElement('kbd');
      kbd.textContent = entry.id === 'fullscreen' && mac ? '⌃⌘F' : keyLabel(entry.keys);
      keys.append(kbd);
      const what = document.createElement('td');
      what.textContent = entry.label;
      tr.append(keys, what);
      table.append(tr);
    }
    const extra = [
      ['/', 'Genel arama (yazı alanı dışında)'],
      ['↑ / ↓', 'Menüde önceki/sonraki ekran (yazı alanı dışında)'],
      ['Esc', 'Açık pencereyi, listeyi ya da aramayı kapatır'],
      [keyLabel('Mod+C'), 'Seçili metni kopyalar — tablolarda sağ tık: satırı/hücreyi kopyala, CSV'],
    ];
    for (const [keysText, label] of extra) {
      const tr = document.createElement('tr');
      const keys = document.createElement('td');
      const kbd = document.createElement('kbd');
      kbd.textContent = keysText;
      keys.append(kbd);
      const what = document.createElement('td');
      what.textContent = label;
      tr.append(keys, what);
      table.append(tr);
    }
    body.append(table);
  }, { wide: true });
}

/** Hız ölçümleri (Yardım menüsü). */
export function openPerf() {
  return modal('Hız ölçümleri', (body) => {
    const summary = perf.summary();
    const p = document.createElement('p');
    p.className = 'km-pref-hint';
    p.textContent = 'Bu oturumdaki ölçümler (ms). "Oturdu": ekran çizildi ve açılış istekleri bitti. '
      + 'Geri dönüşte açılış istekleri son bilinen yanıtla cevaplanır, gerçeği arkadan gelir.';
    const list = document.createElement('dl');
    list.className = 'km-perf';
    const add = (name, value) => {
      const dt = document.createElement('dt');
      dt.textContent = name;
      const dd = document.createElement('dd');
      dd.textContent = value == null ? '—' : `${value} ms`;
      list.append(dt, dd);
    };
    add('Giriş ekranı boyandı', summary.boot['giris-boyandi']);
    add('Merkez hazır', summary.boot['merkez-hazir']);
    add('Çalışma alanı açıldı', summary.boot['calisma-alani']);
    add('Ekran ilk açılış (ortanca)', summary.firstOpen);
    add('Ekrana geri dönüş (ortanca)', summary.returnVisit);
    body.append(p, list);

    const recent = perf.entries().filter((entry) => entry.kind === 'panel').slice(-12).reverse();
    if (recent.length) {
      const table = document.createElement('table');
      table.className = 'km-keys';
      for (const entry of recent) {
        const tr = document.createElement('tr');
        const a = document.createElement('td');
        a.textContent = entry.title || entry.id;
        const b = document.createElement('td');
        b.textContent = `${Math.round(entry.ms)} ms${entry.return ? ' · geri dönüş' : ''}${entry.cache ? ` · ${entry.cache} istek önbellekten` : ''}`;
        tr.append(a, b);
        table.append(tr);
      }
      body.append(table);
    }
  }, { wide: true });
}

/** Hakkında (Windows/Linux Yardım menüsü; macOS'ta sistemin kendi penceresi). */
export async function openAbout() {
  const info = isNative() ? await invoke('native_info').catch(() => null) : null;
  const server = isNative() ? await invoke('server_info').catch(() => null) : null;
  return modal('Kontrol Merkezi', (body) => {
    const lines = [
      `Sürüm ${info?.version || '—'}`,
      server?.base ? `Merkez: ${server.base}` : '',
      '© Benim Dünyam Şirketler Grubu',
    ].filter(Boolean);
    for (const text of lines) {
      const p = document.createElement('p');
      p.textContent = text;
      body.append(p);
    }
  });
}
