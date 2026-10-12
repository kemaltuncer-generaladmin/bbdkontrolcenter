// Profiller sekmesi — liste + düzenleyici.
//
// Profil, tablette hangi uygulamanın ne kadar açılacağını, hangi saatlerde
// kullanılabileceğini ve tabletin sistem kısıtlamalarını belirler. Her kayıt
// sunucuda yeni bir revizyon açar; tabletler onu bir sonraki bağlantıda alır.
//
// Düzenleyici yoklamada yeniden KURULMAZ. Kaydedilmemiş değişiklik varken
// sunucudaki sürüm değişse bile taslağa dokunulmaz; değişiklik yoksa
// düzenleyici yeni sürümle sessizce tazelenir.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/profiles.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, confirmSimple, confirmWithPin, h, num } from '../../ui-kit/kit.js';
import { createPicker } from '../../ui-kit/picker.js';
import { emptyState, splitView } from '../../ui-kit/layout.js';
import {
  DAYS, DEFAULT_TZ, OWN_PACKAGE, busy, clockInput, formDialog, matchesText, muted, nameCell,
  parseClock, permButton, problem, when, windowsSummary,
} from './common.js';

const NEW = '__new__';
const UNASSIGN = '__unassign__';
// Uygulama kaynağı: tabletin envanteri yerine panelden dağıtılan uygulamalar.
const MANAGED = '__managed__';
const enc = encodeURIComponent;

// Sunucunun kabul ettiği Android SystemPolicyControl.storageKey() alt kümesi.
// Seçilmeyen kontrol tabletteki mevcut varsayılan davranışı korur.
const SYSTEM_CONTROLS = [
  ['usb_file_transfer', 'USB dosya aktarımı', 'Tabletin USB üzerinden depolama olarak kullanılmasını engeller.'],
  ['unknown_source_installs', 'Bilinmeyen kaynaklardan kurulum', 'Mağaza dışı uygulama kurulumunu engeller.'],
  ['app_stores_and_installers', 'Uygulama mağazaları ve yükleyiciler', 'Uygulama kurulumunu ve tablet uygulamasının güvenli güncellemesini de engelleyebilir.'],
  ['account_modification', 'Hesap değişiklikleri', 'Cihazda hesap ekleme ve kaldırmayı engeller.'],
  ['vpn_configuration', 'VPN yapılandırması', 'VPN ayarlarının değiştirilmesini engeller.'],
  ['network_reset', 'Ağ ayarlarını sıfırlama', 'Ağ ayarlarını sıfırlamayı engeller.'],
  ['tethering', 'İnternet paylaşımı', 'Hotspot ve bağlantı paylaşımı ayarlarını engeller.'],
  ['wifi_configuration', 'Wi-Fi yapılandırması', 'Ağ seçimini kısıtlar; yeni bir Wi-Fi ağı veya şifresi tanımlamaz. Cihaz üreticisine göre sonuç değişebilir.'],
  ['private_dns', 'Özel DNS', 'Özel DNS ayarlarının değiştirilmesini engeller.'],
  ['date_and_time', 'Tarih ve saat', 'Sistem saati ve saat dilimi değişikliklerini engeller.'],
  ['safe_boot', 'Güvenli mod', 'Cihazın güvenli modda başlatılmasını engeller.'],
  ['factory_reset', 'Fabrika ayarlarına sıfırlama', 'Ayarlar üzerinden cihazı sıfırlamayı engeller.'],
  ['user_and_profile_creation', 'Kullanıcı ve profil oluşturma', 'Yeni kullanıcı/profil oluşturmayı ve kullanıcı değiştirmeyi engeller.'],
  ['app_uninstall', 'Uygulama kaldırma', 'Uygulamaların kaldırılmasını engeller.'],
  ['app_control_settings', 'Uygulama kontrol ayarları', 'Uygulamaları durdurma ve verilerini silme ayarlarını engeller.'],
];

const CONTROL_CHOICES = [['', 'Tablet varsayılanı'], ['block', 'Engelle'], ['allow', 'İzin ver']];
const MODES = [['blocked', 'Yasak'], ['timed', 'Süreli'], ['unlimited', 'Sınırsız']];
const MAX_WINDOWS = 21;
const PACKAGE_NAME = /^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$/;
const FALLBACK_ZONES = ['Europe/London', 'Europe/Berlin', 'Europe/Moscow', 'Asia/Baku', 'Asia/Dubai', 'UTC'];

function zoneList(current) {
  let zones = [];
  try {
    zones = typeof Intl.supportedValuesOf === 'function' ? Intl.supportedValuesOf('timeZone') : [];
  } catch {
    zones = [];
  }
  if (!zones.length) zones = FALLBACK_ZONES;
  const list = [DEFAULT_TZ, ...zones.filter((zone) => zone !== DEFAULT_TZ)];
  if (current && !list.includes(current)) list.splice(1, 0, current);
  return list;
}

function minutesOf(text) {
  const value = Number(String(text ?? '').trim().replace(',', '.'));
  return Number.isInteger(value) ? value : NaN;
}

/** Sunucu profili → düzenlenebilir taslak (derin kopya). */
function toDraft(profile) {
  const controls = profile?.systemControls || {};
  return {
    name: profile?.name || '',
    timezone: profile?.timezone || DEFAULT_TZ,
    windows: (profile?.usageWindows || []).map((item) => ({
      days: [...(item.days || [])], start: item.start || '', end: item.end || '',
    })),
    apps: (profile?.apps || []).map((item) => ({
      packageName: item.packageName,
      appName: item.appName || '',
      mode: !item.allowed ? 'blocked' : item.unlimited ? 'unlimited' : 'timed',
      minutes: String(item.allowed && !item.unlimited
        ? Math.max(1, Math.round((item.dailyLimitSeconds || 0) / 60)) : 60),
    })),
    controls: Object.fromEntries(SYSTEM_CONTROLS.map(([key]) => [key,
      Object.hasOwn(controls, key) ? (controls[key] ? 'block' : 'allow') : ''])),
  };
}

/** Taslak → sunucu gövdesi. Kirli karşılaştırması da bunun üzerinden yapılır. */
function toBody(draft) {
  return {
    name: draft.name.trim(),
    timezone: draft.timezone,
    usageWindows: draft.windows.map((item) => ({
      days: [...new Set(item.days)].sort((a, b) => a - b),
      start: parseClock(item.start) || item.start,
      end: parseClock(item.end) || item.end,
    })),
    apps: draft.apps.map((item) => ({
      packageName: item.packageName,
      appName: String(item.appName || '').trim() || item.packageName,
      allowed: item.mode !== 'blocked',
      unlimited: item.mode === 'unlimited',
      dailyLimitSeconds: item.mode === 'timed' ? minutesOf(item.minutes) * 60 : 0,
    })),
    systemControls: Object.fromEntries(Object.entries(draft.controls)
      .filter(([, value]) => value).map(([key, value]) => [key, value === 'block'])),
  };
}

/** İlk sorun ve nerede olduğu; sorun yoksa null. */
function findProblem(draft) {
  const name = draft.name.trim();
  if (name.length < 2 || name.length > 100) {
    return { message: 'Profil adı 2–100 karakter olmalı.', field: 'name' };
  }
  if (draft.windows.length > MAX_WINDOWS) {
    return { message: `En fazla ${MAX_WINDOWS} saat aralığı tanımlanabilir.`, window: draft.windows[MAX_WINDOWS] };
  }
  for (const [index, item] of draft.windows.entries()) {
    if (!item.days.length) return { message: `${index + 1}. saat aralığında en az bir gün seçin.`, window: item };
    const start = parseClock(item.start);
    const end = parseClock(item.end);
    if (!start || !end) return { message: 'Saatleri 08:30 biçiminde yazın.', window: item };
    if (start >= end) {
      return { message: 'Başlangıç saati bitişten önce olmalı. Gece yarısını aşan aralığı ikiye bölün.', window: item };
    }
  }
  for (const item of draft.apps) {
    if (item.mode !== 'timed') continue;
    const minutes = minutesOf(item.minutes);
    if (!Number.isInteger(minutes) || minutes < 1 || minutes > 1440) {
      return { message: `${item.appName || item.packageName}: günlük süre 1 ile 1440 dakika arasında olmalı.`, app: item };
    }
  }
  return null;
}

function section(title, ...nodes) {
  const box = h('section', 'tb-section');
  box.append(h('h4', 'tb-section-title', title), ...nodes.filter(Boolean));
  return box;
}

function field(label, input) {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), input);
  return wrap;
}

export function profilesView(app) {
  const node = h('div', 'tb-pane');
  const side = h('div', 'tb-side');
  const sideHead = h('div', 'tb-side-head');
  sideHead.append(h('h3', 'tb-side-title', 'Profiller'), h('span', 'kit-spacer'),
    permButton(app, 'Yeni profil', 'manage', { onClick: () => guard(() => select(NEW)) }));
  const list = h('div', 'tb-list');
  side.append(sideHead, list);
  const editorWrap = h('div', 'tb-editor-wrap');
  const split = splitView(side, editorWrap, 'minmax(220px, 300px) minmax(0, 1fr)');
  split.classList.add('tb-split');
  node.append(split);

  let selectedKey = null;
  let editor = null;

  // ------------------------------------------------------------- liste
  function paintList() {
    list.replaceChildren();
    if (selectedKey === NEW) {
      const item = h('div', 'tb-item on');
      item.setAttribute('aria-current', 'true');
      item.append(h('b', null, editor?.draft.name.trim() || 'Yeni profil'), h('span', 'tb-sub', 'Henüz kaydedilmedi'));
      list.append(item);
    }
    if (!app.state.profiles.length && selectedKey !== NEW) {
      list.append(muted('Henüz profil yok. Profil, tablette hangi uygulamanın ne kadar açılacağını belirler.'));
    }
    for (const profile of app.state.profiles) {
      const item = h('button', 'tb-item');
      item.type = 'button';
      if (profile.id === selectedKey) {
        item.classList.add('on');
        item.setAttribute('aria-current', 'true');
      }
      const tablets = profile.deviceCount ? `${num(profile.deviceCount)} tablet` : 'Tablete atanmamış';
      item.append(h('b', null, profile.name),
        h('span', 'tb-sub', `${num(profile.apps?.length || 0)} uygulama · ${tablets}`),
        h('span', 'tb-sub', windowsSummary(profile.usageWindows)));
      item.addEventListener('click', () => {
        if (profile.id !== selectedKey) guard(() => select(profile.id));
      });
      list.append(item);
    }
  }

  async function guard(next) {
    if (editor?.dirty()) {
      const discard = await confirmSimple(app.root, {
        title: 'Kaydedilmemiş değişiklikler var',
        description: 'Bu profilde yaptığınız değişiklikler kaydedilmedi. Devam ederseniz kaybolur.',
        confirmLabel: 'Değişiklikleri bırak',
        danger: true,
      });
      if (!discard) return;
    }
    await next();
  }

  function select(key) {
    const profile = key === NEW ? null : app.profile(key);
    if (key !== NEW && !profile) {
      autoselect();
      return;
    }
    selectedKey = key;
    buildEditor(profile);
    paintList();
  }

  function autoselect() {
    const first = app.state.profiles[0];
    if (first) {
      select(first.id);
      return;
    }
    if (app.can('manage')) {
      select(NEW);
      return;
    }
    selectedKey = null;
    editor?.destroy();
    editor = null;
    editorWrap.replaceChildren(emptyState({
      title: 'Henüz profil yok',
      text: 'Profil oluşturmak için tablet yönetme yetkisi gerekiyor.',
    }));
    paintList();
  }

  // -------------------------------------------------------- düzenleyici
  function buildEditor(profile, { keepScroll = false } = {}) {
    const scroll = keepScroll ? editorWrap.scrollTop : 0;
    editor?.destroy();
    editor = createEditor(profile);
    editorWrap.replaceChildren(editor.node);
    editorWrap.scrollTop = scroll;
  }

  function createEditor(profile) {
    const ed = {
      id: profile?.id || null,
      revision: profile?.revision || 0,
      draft: toDraft(profile),
      baseline: '',
      inventory: null,
      ruleRows: new Map(),
      windowRows: new Map(),
    };
    ed.dirty = () => JSON.stringify(toBody(ed.draft)) !== ed.baseline;
    const canManage = app.can('manage');
    const box = h('div', 'tb-editor');

    // ------------------------------------------------------------ başlık
    const head = h('div', 'tb-editor-head');
    const titles = h('div', 'tb-editor-titles');
    const title = h('h3', 'tb-editor-title', profile ? profile.name : 'Yeni profil');
    const meta = h('span', 'tb-sub');
    titles.append(title, meta);
    const dirtyNote = h('span', 'kit-dirty', 'Kaydedilmemiş değişiklik var');
    const revert = button('Vazgeç', {
      variant: 'ghost',
      title: 'Değişiklikleri bırak, kayıtlı hâline dön',
      onClick: () => buildEditor(ed.id ? app.profile(ed.id) : null, { keepScroll: true }),
    });
    revert.hidden = true;
    const save = permButton(app, 'Kaydet', 'manage', {
      variant: 'primary',
      onClick: (event) => busy(event.currentTarget, saveProfile),
    });
    head.append(titles, h('span', 'kit-spacer'), dirtyNote, revert, save);

    dirtyNote.setAttribute('aria-hidden', 'true');
    const changed = () => {
      const dirty = ed.dirty();
      dirtyNote.classList.toggle('show', dirty);
      // Soluk gizlenen not ekran okuyucuda da susmalı.
      dirtyNote.setAttribute('aria-hidden', String(!dirty));
      revert.hidden = !dirty;
      if (!ed.id) {
        title.textContent = ed.draft.name.trim() || 'Yeni profil';
        paintList();
      }
    };

    // Yetki yoksa bütün alanlar tek yerden kapanır: `fieldset[disabled]`
    // içindeki her girdi ve düğme doğal olarak pasifleşir.
    const form = h('fieldset', 'tb-fieldset');
    if (!canManage) {
      form.disabled = true;
      form.title = 'Profil düzenlemek için tablet yönetme yetkisi gerekiyor.';
    }

    // ------------------------------------------------------------- genel
    const nameInput = h('input', 'kit-input');
    nameInput.type = 'text';
    nameInput.maxLength = 100;
    nameInput.placeholder = 'Örn. 5. sınıflar';
    nameInput.value = ed.draft.name;
    nameInput.addEventListener('input', () => {
      ed.draft.name = nameInput.value;
      nameInput.classList.remove('bad');
      changed();
    });
    const zoneSelect = h('select', 'kit-select');
    for (const zone of zoneList(ed.draft.timezone)) {
      const option = h('option', null, zone === DEFAULT_TZ ? 'Türkiye (İstanbul)' : zone);
      option.value = zone;
      zoneSelect.append(option);
    }
    zoneSelect.value = ed.draft.timezone;
    zoneSelect.addEventListener('change', () => {
      ed.draft.timezone = zoneSelect.value;
      changed();
    });
    const basics = h('div', 'tb-grid');
    basics.append(field('Profil adı', nameInput), field('Saat dilimi', zoneSelect));

    // ---------------------------------------------------- kullanım saatleri
    const windowsList = h('div', 'tb-windows');
    const addWindow = button('Saat aralığı ekle', {
      onClick: () => {
        if (ed.draft.windows.length >= MAX_WINDOWS) {
          app.toast(`En fazla ${MAX_WINDOWS} saat aralığı tanımlanabilir.`, 'warn');
          return;
        }
        ed.draft.windows.push({ days: [1, 2, 3, 4, 5], start: '08:30', end: '16:00' });
        paintWindows();
        changed();
        ed.windowRows.get(ed.draft.windows[ed.draft.windows.length - 1])?.querySelector('input')?.focus();
      },
    });

    function windowRow(item, index) {
      const row = h('div', 'tb-window');
      row.setAttribute('role', 'group');
      row.setAttribute('aria-label', `${index + 1}. saat aralığı`);
      const days = h('div', 'tb-days');
      for (const [number, short, long] of DAYS) {
        const chip = h('button', 'tb-day', short);
        chip.type = 'button';
        chip.title = long;
        chip.setAttribute('aria-label', long);
        const paint = () => {
          const on = item.days.includes(number);
          chip.classList.toggle('on', on);
          chip.setAttribute('aria-pressed', String(on));
        };
        paint();
        chip.addEventListener('click', () => {
          item.days = item.days.includes(number)
            ? item.days.filter((day) => day !== number) : [...item.days, number];
          row.classList.remove('tb-bad-row');
          paint();
          changed();
        });
        days.append(chip);
      }
      const times = h('div', 'tb-times');
      times.append(
        clockInput(item.start, `${index + 1}. aralık başlangıç saati`, (value) => {
          item.start = value;
          row.classList.remove('tb-bad-row');
          changed();
        }),
        h('span', 'tb-sub', '–'),
        clockInput(item.end, `${index + 1}. aralık bitiş saati`, (value) => {
          item.end = value;
          row.classList.remove('tb-bad-row');
          changed();
        }),
      );
      row.append(days, times, button('Kaldır', {
        variant: 'ghost',
        title: 'Bu saat aralığını kaldır',
        onClick: () => {
          ed.draft.windows.splice(ed.draft.windows.indexOf(item), 1);
          paintWindows();
          changed();
        },
      }));
      ed.windowRows.set(item, row);
      return row;
    }

    function paintWindows() {
      ed.windowRows.clear();
      windowsList.replaceChildren();
      if (!ed.draft.windows.length) {
        windowsList.append(muted('Her zaman açık. Saat aralığı eklerseniz tablet yalnız o saatlerde kullanılabilir.'));
        return;
      }
      ed.draft.windows.forEach((item, index) => windowsList.append(windowRow(item, index)));
    }

    // ----------------------------------------------------- uygulama kuralları
    const counts = h('span', 'tb-sub');
    const ruleSearch = h('input', 'kit-input tb-rule-search');
    ruleSearch.type = 'search';
    ruleSearch.placeholder = 'Kurallarda ara';
    ruleSearch.setAttribute('aria-label', 'Uygulama kurallarında ara');
    ruleSearch.addEventListener('input', () => filterRules());
    const rulesHead = h('div', 'tb-rules-head');
    rulesHead.append(counts, h('span', 'kit-spacer'), ruleSearch);
    const rulesList = h('div', 'tb-rules');
    rulesList.setAttribute('role', 'list');

    function paintCounts() {
      const apps = ed.draft.apps;
      const tally = (mode) => apps.filter((item) => item.mode === mode).length;
      counts.textContent = apps.length
        ? `${num(apps.length)} uygulama · ${num(tally('timed'))} süreli · `
          + `${num(tally('unlimited'))} sınırsız · ${num(tally('blocked'))} yasak`
        : '';
      ruleSearch.hidden = apps.length < 8;
    }

    function filterRules() {
      for (const [rule, entry] of ed.ruleRows) {
        entry.row.hidden = !matchesText(ruleSearch.value, rule.appName, rule.packageName);
      }
    }

    function ruleRow(rule) {
      const label = rule.appName || rule.packageName;
      const row = h('div', 'tb-rule');
      row.setAttribute('role', 'listitem');
      const seg = h('div', 'tb-seg');
      seg.setAttribute('role', 'group');
      seg.setAttribute('aria-label', `${label} için kural`);
      const minutes = h('input', 'kit-input tb-minutes-input');
      minutes.type = 'text';
      minutes.inputMode = 'numeric';
      minutes.maxLength = 4;
      minutes.value = rule.minutes;
      minutes.setAttribute('aria-label', `${label} için günlük dakika`);
      minutes.addEventListener('input', () => {
        rule.minutes = minutes.value;
        minutes.classList.remove('bad');
        changed();
      });
      const minutesBox = h('label', 'tb-minutes');
      minutesBox.append(minutes, h('span', 'tb-sub', 'dk / gün'));
      const choices = MODES.map(([mode, text]) => {
        const choice = h('button', 'tb-seg-btn', text);
        choice.type = 'button';
        choice.addEventListener('click', () => {
          rule.mode = mode;
          paintMode();
          paintCounts();
          changed();
          if (mode === 'timed') minutes.focus();
        });
        return choice;
      });
      const paintMode = () => {
        MODES.forEach(([mode], index) => {
          const on = rule.mode === mode;
          choices[index].classList.toggle('on', on);
          choices[index].setAttribute('aria-pressed', String(on));
        });
        // Görünmez ama yerini korur: satırlar alt alta hizalı kalsın.
        minutesBox.classList.toggle('tb-off', rule.mode !== 'timed');
      };
      seg.append(...choices);
      paintMode();
      row.append(nameCell(label, label !== rule.packageName ? rule.packageName : ''), seg, minutesBox,
        button('Kaldır', {
          variant: 'ghost',
          title: `${label} kuralını kaldır`,
          onClick: () => {
            ed.draft.apps.splice(ed.draft.apps.indexOf(rule), 1);
            ed.ruleRows.delete(rule);
            row.remove();
            if (!ed.draft.apps.length) paintRules();
            paintCounts();
            refreshPicker();
            changed();
          },
        }));
      ed.ruleRows.set(rule, { row, minutes });
      return row;
    }

    function paintRules() {
      ed.ruleRows.clear();
      rulesList.replaceChildren();
      if (!ed.draft.apps.length) {
        rulesList.append(muted('Henüz uygulama yok. Öğrenciler yalnız burada izin verilen uygulamaları açabilir.'));
      }
      for (const rule of ed.draft.apps) rulesList.append(ruleRow(rule));
      paintCounts();
      filterRules();
    }

    function addRules(items) {
      const taken = new Set(ed.draft.apps.map((item) => item.packageName));
      let added = 0;
      for (const item of items) {
        if (taken.has(item.packageName)) continue;
        taken.add(item.packageName);
        ed.draft.apps.push({
          packageName: item.packageName,
          appName: String(item.appName || '').slice(0, 120),
          mode: 'timed',
          minutes: '60',
        });
        added += 1;
      }
      if (added) {
        paintRules();
        refreshPicker();
        changed();
      }
      return added;
    }

    // ------------------------------------------------------ uygulama ekle
    const adder = h('div', 'tb-adder');
    adder.hidden = true;
    const toggleAdder = button('Uygulama ekle', {
      onClick: () => {
        adder.hidden = !adder.hidden;
        toggleAdder.setAttribute('aria-expanded', String(!adder.hidden));
        if (!adder.hidden) prepareSources();
      },
    });
    toggleAdder.setAttribute('aria-expanded', 'false');

    const source = h('select', 'kit-select');
    source.setAttribute('aria-label', 'Uygulamaları alınacak tablet');
    source.addEventListener('change', () => loadSource());
    const pickerNote = h('span', 'tb-sub');
    const addChosen = button('Seçilenleri ekle', {
      onClick: () => {
        const chosen = picker.selectedItems();
        const added = addRules(chosen.map((item) => ({ packageName: String(item.id), appName: item.name })));
        picker.clear();
        paintAddChosen();
        if (added) app.toast(`${num(added)} uygulama eklendi; süresini ayarlayıp kaydedin.`, 'good');
      },
    });
    const paintAddChosen = () => {
      const count = picker.size();
      addChosen.textContent = count ? `Seçilenleri ekle (${num(count)})` : 'Seçilenleri ekle';
      addChosen.disabled = !count;
    };
    const picker = createPicker({
      items: [],
      groupLabel: 'Uygulama',
      placeholder: 'Uygulama ara',
      onChange: () => paintAddChosen(),
    });
    const pickerBox = h('div', 'tb-picker');
    pickerBox.hidden = true;
    pickerBox.append(picker.node, addChosen);
    paintAddChosen();

    function prepareSources() {
      const devices = app.state.devices
        .filter((device) => device.paired && device.status !== 'revoked')
        .sort((a, b) => Number(b.status === 'online') - Number(a.status === 'online')
          || String(a.name).localeCompare(String(b.name), 'tr'));
      const keep = source.value;
      source.replaceChildren();
      const placeholder = h('option', null, devices.length ? 'Tablet seçin' : 'Eşli tablet yok');
      placeholder.value = '';
      source.append(placeholder);
      // Henüz hiçbir tablete kurulmamış dağıtılan uygulama da profile eklenebilir.
      const distributed = h('option', null, 'Dağıtılan uygulamalar');
      distributed.value = MANAGED;
      source.append(distributed);
      for (const device of devices) {
        const option = h('option', null, device.status === 'online' ? device.name : `${device.name} (çevrimdışı)`);
        option.value = device.id;
        source.append(option);
      }
      source.value = keep === MANAGED || devices.some((device) => device.id === keep) ? keep : '';
      if (!source.value) {
        pickerBox.hidden = true;
        pickerNote.textContent = devices.length ? '' : 'Paket adını aşağıdan elle ekleyebilirsiniz.';
      }
    }

    async function loadSource() {
      const id = source.value;
      ed.inventory = null;
      pickerBox.hidden = true;
      if (!id) {
        pickerNote.textContent = '';
        return;
      }
      pickerNote.textContent = 'Uygulamalar yükleniyor…';
      try {
        const rows = id === MANAGED
          ? (await app.managedApps()).filter((item) => item.kind !== 'self').map((item) => ({
            packageName: item.packageName, appName: item.appName, isLaunchable: true,
            isSystemApp: false, managed: true,
          }))
          : await app.inventory(id);
        if (source.value !== id) return;
        ed.inventory = rows;
        refreshPicker();
        pickerBox.hidden = false;
      } catch (error) {
        pickerNote.textContent = problem(error, 'Uygulama listesi alınamadı.');
      }
    }

    function refreshPicker() {
      if (!ed.inventory) return;
      const taken = new Set(ed.draft.apps.map((item) => item.packageName));
      const items = ed.inventory
        .filter((item) => item.isLaunchable && item.packageName !== OWN_PACKAGE && !taken.has(item.packageName))
        .map((item) => ({
          id: item.packageName,
          name: item.appName || item.packageName,
          meta: item.packageName,
          group: item.managed ? 'Dağıtılan uygulama' : (item.isSystemApp ? 'Sistem' : 'Yüklenen'),
        }));
      picker.setItems(items);
      paintAddChosen();
      const fromManaged = source.value === MANAGED;
      pickerNote.textContent = items.length ? ''
        : (ed.inventory.length
          ? (fromManaged ? 'Dağıtılan uygulamaların hepsi profilde.' : 'Bu tabletteki uygulamaların hepsi profilde.')
          : (fromManaged ? 'Henüz dağıtılan uygulama yok. Uygulamalar sekmesinden APK yükleyin.'
            : 'Tablet henüz uygulama listesini göndermedi.'));
    }

    const manualPackage = h('input', 'kit-input');
    manualPackage.type = 'text';
    manualPackage.placeholder = 'Paket adı, örn. org.khanacademy.android';
    manualPackage.maxLength = 255;
    manualPackage.setAttribute('aria-label', 'Paket adı');
    const manualName = h('input', 'kit-input');
    manualName.type = 'text';
    manualName.placeholder = 'Uygulama adı (isteğe bağlı)';
    manualName.maxLength = 120;
    manualName.setAttribute('aria-label', 'Uygulama adı');
    const manualError = h('span', 'tb-sub tb-bad');
    manualError.setAttribute('role', 'alert');
    const addManual = () => {
      const packageName = manualPackage.value.trim();
      if (!PACKAGE_NAME.test(packageName)) {
        manualError.textContent = 'Paket adını com.ornek.uygulama biçiminde yazın.';
        manualPackage.focus();
        return;
      }
      if (ed.draft.apps.some((item) => item.packageName === packageName)) {
        manualError.textContent = 'Bu uygulama profilde zaten var.';
        return;
      }
      addRules([{ packageName, appName: manualName.value.trim() || packageName }]);
      manualPackage.value = '';
      manualName.value = '';
      manualError.textContent = '';
    };
    manualPackage.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        addManual();
      }
    });
    const manualRow = h('div', 'tb-manual');
    manualRow.append(manualPackage, manualName, button('Ekle', { onClick: addManual }));

    const sourceRow = h('div', 'tb-actions');
    sourceRow.append(h('span', 'tb-sub', 'Tabletten seç:'), source, pickerNote);
    adder.append(sourceRow, pickerBox, h('span', 'tb-sub', 'Ya da elle ekleyin:'), manualRow, manualError);

    // ---------------------------------------------------------- gelişmiş
    const advanced = h('details', 'tb-advanced');
    const summary = h('summary');
    const paintSummary = () => {
      const set = Object.values(ed.draft.controls).filter(Boolean).length;
      summary.textContent = `Gelişmiş · sistem kısıtlamaları${set ? ` (${num(set)} ayarlı)` : ''}`;
    };
    const controls = h('div', 'tb-controls');
    for (const [key, label, description] of SYSTEM_CONTROLS) {
      const row = h('div', 'tb-control');
      const select = h('select', 'kit-select');
      select.setAttribute('aria-label', label);
      for (const [value, text] of CONTROL_CHOICES) {
        const option = h('option', null, text);
        option.value = value;
        select.append(option);
      }
      select.value = ed.draft.controls[key] || '';
      select.addEventListener('change', () => {
        ed.draft.controls[key] = select.value;
        paintSummary();
        changed();
      });
      row.append(nameCell(label, description), select);
      controls.append(row);
    }
    advanced.append(summary, muted('Tablet desteklemediği kısıtlamayı uygulamayabilir. USB hata ayıklama '
      + 'buradan kapatılamaz; kurtarma erişimi korunur.'), controls);
    paintSummary();

    // ------------------------------------------------------------- alt
    const foot = h('div', 'tb-editor-foot');
    ed.paintMeta = (fresh) => {
      const current = fresh || (ed.id ? app.profile(ed.id) : null);
      meta.textContent = current
        ? `${current.deviceCount ? `${num(current.deviceCount)} tablette kullanılıyor` : 'Tablete atanmamış'}`
          + ` · son değişiklik ${when(current.updatedAt)}`
        : 'Kaydedince tabletlere atanabilir.';
      foot.replaceChildren();
      if (!current) return;
      const remove = permButton(app, 'Sil', 'manage', {
        variant: 'danger',
        onClick: (event) => busy(event.currentTarget, removeProfile),
      });
      foot.append(
        permButton(app, 'Kopyala', 'manage', {
          title: 'Kayıtlı hâlinin kopyasını oluşturur',
          onClick: (event) => busy(event.currentTarget, duplicateProfile),
        }),
        remove,
      );
    };

    const windowActions = h('div', 'tb-actions');
    windowActions.append(addWindow);
    const ruleActions = h('div', 'tb-actions');
    ruleActions.append(toggleAdder);
    form.append(
      section('Genel', basics),
      section('Kullanım saatleri', windowsList, windowActions),
      section('Uygulamalar', rulesHead, rulesList, ruleActions, adder),
      advanced,
    );
    box.append(head, form, foot);

    paintWindows();
    paintRules();
    ed.paintMeta(profile);
    ed.baseline = JSON.stringify(toBody(ed.draft));

    // ------------------------------------------------------------ kayıt
    function showProblem(found) {
      app.toast(found.message, 'bad');
      if (found.field === 'name') {
        nameInput.classList.add('bad');
        nameInput.focus();
      } else if (found.window) {
        const row = ed.windowRows.get(found.window);
        row?.classList.add('tb-bad-row');
        row?.scrollIntoView({ block: 'center' });
      } else if (found.app) {
        const entry = ed.ruleRows.get(found.app);
        if (entry) {
          entry.row.hidden = false;
          entry.minutes.classList.add('bad');
          entry.minutes.focus();
        }
      }
    }

    async function saveProfile() {
      const found = findProblem(ed.draft);
      if (found) {
        showProblem(found);
        return;
      }
      const payload = toBody(ed.draft);
      try {
        const saved = await app.call(ed.id ? `/profiles/${enc(ed.id)}` : '/profiles', {
          method: ed.id ? 'PUT' : 'POST', body: payload,
        });
        const created = !ed.id;
        // Taslak artık kayıtlı hâl: yoklama düzenleyiciyi "kirli" sanmasın.
        ed.id = saved.id;
        ed.revision = saved.revision;
        ed.baseline = JSON.stringify(payload);
        selectedKey = saved.id;
        app.toast(created ? 'Profil oluşturuldu. Tabletler sekmesinden atayabilirsiniz.'
          : 'Profil kaydedildi. Tabletler bir sonraki bağlantıda alır.', 'good');
        await app.refresh();
        const latest = app.profile(saved.id);
        buildEditor(latest && latest.revision >= saved.revision ? latest
          : { ...saved, deviceCount: latest?.deviceCount || 0 }, { keepScroll: true });
        paintList();
      } catch (error) {
        app.toast(problem(error, 'Profil kaydedilemedi. Tekrar deneyin.'), 'bad');
      }
    }

    async function duplicateProfile() {
      await guard(async () => {
        try {
          const copy = await app.call(`/profiles/${enc(ed.id)}/duplicate`, { method: 'POST', body: {} });
          app.toast(`“${copy.name}” oluşturuldu.`, 'good');
          await app.refresh();
          select(copy.id);
        } catch (error) {
          app.toast(problem(error, 'Profil kopyalanamadı. Tekrar deneyin.'), 'bad');
        }
      });
    }

    /**
     * Profil silme. Tabletlere atanmışsa pencere o tabletleri sayar ve ne
     * olacaklarını sorar: başka profile taşınır ya da profilsiz kalır.
     */
    async function removeProfile() {
      const current = app.profile(ed.id);
      if (!current) return;
      const tablets = app.state.devices
        .filter((device) => device.profile_id === current.id && device.status !== 'revoked');
      const others = app.state.profiles.filter((item) => item.id !== current.id);
      let body;
      if (!tablets.length) {
        const pin = await confirmWithPin(app.root, {
          title: `“${current.name}” profili silinsin mi?`,
          description: 'Profil ve uygulama kuralları kalıcı olarak silinir. Onaylamak için PIN’inizi girin.',
          confirmLabel: 'Profili sil',
        });
        if (!pin) return;
        body = { pin };
      } else {
        const names = tablets.slice(0, 6).map((device) => device.name).join(', ')
          + (tablets.length > 6 ? ` ve ${num(tablets.length - 6)} tablet daha` : '');
        const answer = await formDialog(app.root, {
          title: `“${current.name}” profili silinsin mi?`,
          description: `Bu profil ${num(tablets.length)} tablette kullanılıyor: ${names}. `
            + 'Profil ve uygulama kuralları kalıcı olarak silinir.',
          fields: [
            {
              key: 'target', label: 'Bu tabletler', type: 'select', value: others[0]?.id || UNASSIGN,
              options: [
                ...others.map((item) => ({ value: item.id, label: `“${item.name}” profiline taşı` })),
                { value: UNASSIGN, label: 'Profilsiz bırak — öğrenci giriş yapamaz' },
              ],
            },
            { key: 'pin', label: 'PIN', type: 'pin', required: true, minLength: 4, maxLength: 32,
              hint: 'Onaylamak için PIN’inizi girin.' },
          ],
          confirmLabel: 'Profili sil',
          danger: true,
        });
        if (!answer) return;
        body = answer.target === UNASSIGN
          ? { pin: answer.pin, unassign: true }
          : { pin: answer.pin, replacementProfileId: answer.target };
      }
      try {
        const result = await app.call(`/profiles/${enc(ed.id)}`, { method: 'DELETE', body });
        const moved = Number(result?.movedDevices || 0);
        const left = Number(result?.unassignedDevices || 0);
        const target = app.profile(body.replacementProfileId)?.name;
        app.toast(moved ? `Profil silindi; ${num(moved)} tablet “${target}” profiline taşındı.`
          : left ? `Profil silindi; ${num(left)} tablet profilsiz kaldı.` : 'Profil silindi.', 'good');
        editor?.destroy();
        editor = null;
        selectedKey = null;
        await app.refresh();
      } catch (error) {
        if (error?.status === 409) {
          app.toast('Profil bu arada bir tablete atanmış. Liste yenilendi; tekrar deneyin.', 'warn');
          await app.refresh();
          return;
        }
        app.toast(problem(error, 'Profil silinemedi. Tekrar deneyin.'), 'bad');
      }
    }

    ed.node = box;
    ed.destroy = () => picker.destroy();
    return ed;
  }

  // ------------------------------------------------------------- dış yüz
  return {
    node,
    open(id) {
      if (id === selectedKey) return;
      guard(() => select(id));
    },
    update() {
      if (!editor) {
        if (selectedKey === null) autoselect();
        else paintList();
        return;
      }
      if (editor.id) {
        const fresh = app.profile(editor.id);
        if (!fresh) {
          // Başka bir yerde silindi. Taslak kirliyse kullanıcı kaydetmeyi
          // deneyip sebebi görür; değilse ilk profile geçilir.
          if (!editor.dirty()) {
            app.toast('Açık olan profil silinmiş.', 'warn');
            editor.destroy();
            editor = null;
            selectedKey = null;
            autoselect();
            return;
          }
        } else if (fresh.revision > editor.revision && !editor.dirty()) {
          buildEditor(fresh, { keepScroll: true });
        } else {
          editor.paintMeta(fresh);
        }
      }
      paintList();
    },
    destroy() {
      editor?.destroy();
    },
  };
}
