// Öğrenciler sekmesi — öğrenci listesi ve detayı (kullanım, ek süre,
// kısıtlama, oturum geçmişi) ile günlük kullanım raporu.
//
// Öğrenci kimliği kantinde durur; liste kantinden okunur ve sunucuda 5 dakika
// bellekte tutulur. Kantine ulaşılamazsa liste yalnız şu an tablette olan
// öğrencilerden kurulur ve tek satırlık uyarı çıkar.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/students.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import {
  blockedButton, button, confirmSimple, confirmWithPin, csvBlob, debounce, h, num, todayIso,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { badge, card, emptyState, splitView } from '../../ui-kit/layout.js';
import { barChart } from '../../ui-kit/charts.js';
import { dateField, trOf } from '../../ui-kit/datefield.js';
import {
  DEFAULT_TZ, END_REASONS, busy, clockInput, clockOf, dur, inline, matchesText, msOf, muted,
  nameCell, parseClock, permButton, problem, todayIn, usageList, when,
} from './common.js';

const enc = encodeURIComponent;
const LIST_LIMIT = 200;
const GRANT_CHIPS = [5, 10, 15, 30];

const SHOW_OPTIONS = [
  ['', 'Tüm öğrenciler'],
  ['session', 'Şu an tablette'],
  ['blocked', 'Kısıtlı'],
];

/** Bölümlü seçici (aria-pressed düğmeler). `set()` olay tetiklemez. */
function segmented(items, active, onChange, label) {
  const box = h('div', 'tb-seg');
  box.setAttribute('role', 'group');
  box.setAttribute('aria-label', label);
  const buttons = new Map();
  const paint = (key) => {
    for (const [name, node] of buttons) {
      node.classList.toggle('on', name === key);
      node.setAttribute('aria-pressed', String(name === key));
    }
  };
  for (const [key, text] of items) {
    const node = h('button', 'tb-seg-btn', text);
    node.type = 'button';
    node.addEventListener('click', () => {
      paint(key);
      onChange(key);
    });
    buttons.set(key, node);
    box.append(node);
  }
  paint(active);
  return { box, set: paint };
}

function pad(value) {
  return String(value).padStart(2, '0');
}

export function studentsView(app) {
  const node = h('div', 'tb-pane');
  const disposers = [];
  let mode = 'list';

  // ------------------------------------------------------------ üst satır
  const subnav = h('div', 'tb-subnav');
  const modes = segmented([['list', 'Öğrenciler'], ['report', 'Günlük rapor']], mode,
    (key) => showMode(key), 'Görünüm');
  subnav.append(modes.box);
  const warn = h('div', 'tb-warnline');
  warn.setAttribute('role', 'status');
  warn.hidden = true;

  const listView = h('div', 'tb-pane');
  const reportView = h('div', 'tb-pane');
  reportView.hidden = true;
  node.append(subnav, warn, listView, reportView);

  function paintWarn() {
    const error = app.state.studentsError;
    warn.hidden = mode !== 'list' || !error;
    if (warn.hidden) return;
    warn.replaceChildren(h('span', null, `${error} Yalnız şu an tablette olan öğrenciler görünüyor.`),
      button('Tekrar dene', { variant: 'ghost', onClick: (event) => busy(event.currentTarget, app.loadStudents) }));
  }

  function showMode(key) {
    mode = key;
    listView.hidden = key !== 'list';
    reportView.hidden = key !== 'report';
    paintWarn();
    if (key === 'report' && (!report.loadedAt || Date.now() - report.loadedAt > 60_000)) loadReport();
  }

  // ---------------------------------------------------------------- liste
  const side = h('div', 'tb-side');
  const tools = h('div', 'tb-side-head tb-side-tools');
  const search = h('input', 'kit-input');
  search.type = 'search';
  search.placeholder = 'Öğrenci ara';
  search.setAttribute('aria-label', 'Öğrenci ara');
  const show = h('select', 'kit-select');
  show.setAttribute('aria-label', 'Gösterilen öğrenciler');
  for (const [value, label] of SHOW_OPTIONS) {
    const option = h('option', null, label);
    option.value = value;
    show.append(option);
  }
  const repaintSoon = debounce(() => paintList(), 120);
  disposers.push(() => repaintSoon.cancel());
  search.addEventListener('input', repaintSoon);
  show.addEventListener('change', () => paintList());
  tools.append(search, show);
  const list = h('div', 'tb-list');
  side.append(tools, list);
  const detail = h('div', 'tb-detail');
  detail.append(emptyState({ title: 'Öğrenci seçin', text: 'Kullanımını, ek sürelerini ve kısıtlamasını görmek için listeden bir öğrenci seçin.' }));
  const split = splitView(side, detail, 'minmax(220px, 320px) minmax(0, 1fr)');
  split.classList.add('tb-split');
  listView.append(split);

  function blockedActive(until) {
    const ms = msOf(until);
    return !Number.isNaN(ms) && ms > app.now();
  }

  /** Kantin listesi + şu an tablette olan öğrenciler (kantin düşse de görünsünler). */
  function rows() {
    const sessions = new Map(app.state.sessions.map((item) => [String(item.student_id), item]));
    const seen = new Set();
    const result = [];
    for (const student of app.state.students || []) {
      const id = String(student.id);
      seen.add(id);
      result.push({ id, name: student.name || 'Adsız öğrenci',
        blockedUntil: blockedActive(student.blockedUntil) ? student.blockedUntil : null,
        session: sessions.get(id) || null });
    }
    for (const [id, session] of sessions) {
      if (!seen.has(id)) {
        result.push({ id, name: session.student_name || 'Öğrenci', blockedUntil: null, session });
      }
    }
    return result.sort((a, b) => Number(Boolean(b.session)) - Number(Boolean(a.session))
      || a.name.localeCompare(b.name, 'tr'));
  }

  function paintList() {
    const all = rows();
    const filtered = all
      .filter((row) => (show.value === 'session' ? row.session : show.value === 'blocked' ? row.blockedUntil : true))
      .filter((row) => matchesText(search.value, row.name));
    list.replaceChildren();
    if (app.state.students === null && !app.state.studentsError && !all.length) {
      list.append(muted('Öğrenciler yükleniyor…'));
      return;
    }
    if (!filtered.length) {
      list.append(muted(all.length ? 'Bu aramaya uyan öğrenci yok.' : 'Gösterilecek öğrenci yok.'));
      return;
    }
    for (const row of filtered.slice(0, LIST_LIMIT)) {
      const item = h('button', 'tb-item');
      item.type = 'button';
      if (current?.id === row.id) {
        item.classList.add('on');
        item.setAttribute('aria-current', 'true');
      }
      item.append(h('b', null, row.name));
      if (row.session) {
        item.append(inline(badge('Tablette', 'good'), row.session.device_name || ''));
      }
      if (row.blockedUntil) {
        item.append(inline(badge('Kısıtlı', 'bad'), `bitiş ${when(row.blockedUntil)}`));
      }
      item.addEventListener('click', () => open(row.id));
      list.append(item);
    }
    if (filtered.length > LIST_LIMIT) {
      list.append(muted(`${num(filtered.length - LIST_LIMIT)} öğrenci daha var — aramayı daraltın.`));
    }
  }

  // ---------------------------------------------------------------- detay
  let current = null;

  function timezoneOf(studentId) {
    const session = app.sessionOf(studentId);
    return app.profile(session?.profile_id)?.timezone || DEFAULT_TZ;
  }

  function open(id) {
    for (const fn of current?.disposers || []) fn();
    const key = String(id);
    const row = rows().find((item) => item.id === key);
    const today = todayIn(timezoneOf(key));
    const st = {
      id: key,
      name: row?.name || app.sessionOf(key)?.student_name || 'Öğrenci',
      today,
      date: today,
      usage: null,
      blocks: [],
      usageAt: 0,
      disposers: [],
    };
    current = st;
    buildDetail(st);
    paintList();
  }

  function buildDetail(st) {
    detail.replaceChildren();
    detail.scrollTop = 0;

    // Başlık
    st.head = h('div', 'tb-detail-head');
    detail.append(st.head);
    paintHead(st);

    // Gün seçimi
    const dateRow = h('div', 'tb-actions');
    const todayButton = button('Bugün', {
      variant: 'ghost',
      onClick: () => {
        picker.set(st.today);
        changeDate(st.today);
      },
    });
    const picker = dateField({ value: st.date, label: 'Gün', onChange: (iso) => changeDate(iso) });
    st.disposers.push(() => picker.destroy());
    const changeDate = (iso) => {
      if (!iso || iso === st.date) return;
      st.date = iso;
      todayButton.hidden = iso === st.today;
      loadUsage(st);
    };
    todayButton.hidden = true;
    dateRow.append(h('span', 'tb-sub', 'Gün'), picker.node, todayButton);
    detail.append(dateRow);

    // Kullanım
    st.usageInfo = h('div', 'tb-sub');
    st.usageBox = h('div');
    detail.append(card('Kullanım', wrap(st.usageInfo, st.usageBox)));

    // Ek süre
    st.grantBox = h('div', 'tb-grant');
    detail.append(card('Ek süre ver', wrap(st.grantBox)));
    buildGrantForm(st);
    st.grantsBox = h('div', 'tb-grants');
    detail.append(card('Verilen ek süreler', wrap(st.grantsBox)));

    // Kısıtlama
    st.blockState = h('div', 'tb-block-state');
    st.blockHistory = h('div', 'tb-block-history');
    const blockForm = buildBlockForm(st);
    detail.append(card('Kısıtlama', wrap(st.blockState, blockForm, st.blockHistory)));

    // Oturum geçmişi
    st.historyBox = h('div');
    st.historyBox.append(muted('Yükleniyor…'));
    detail.append(card('Oturum geçmişi', st.historyBox));

    // Tablet verilerini silme — tehlikeli, en altta
    st.dataBox = h('div', 'tb-danger');
    detail.append(card('Tablet verileri', wrap(st.dataBox)));
    paintData(st);

    loadUsage(st);
    loadBlocks(st);
    loadHistory(st);
  }

  function wrap(...nodes) {
    const box = h('div', 'kit-card-body tb-stack');
    box.append(...nodes.filter(Boolean));
    return box;
  }

  function paintHead(st) {
    const session = app.sessionOf(st.id);
    const titles = h('div', 'tb-editor-titles');
    titles.append(h('h3', 'tb-editor-title', st.name),
      session
        ? inline(badge('Tablette', 'good'),
          `${session.device_name || 'Tablet'} · giriş ${clockOf(session.login_at)}`)
        : h('span', 'tb-sub', 'Şu an tablette değil'));
    const actions = h('div', 'tb-actions');
    if (session) {
      actions.append(
        permButton(app, 'Oturumu kapat', 'manage', {
          variant: 'danger',
          onClick: (event) => busy(event.currentTarget, () => endSession(st, session)),
        }),
        button('Tableti aç', { variant: 'ghost', onClick: () => app.openDevice(session.device_id) }),
      );
    }
    st.head.replaceChildren(titles, h('span', 'kit-spacer'), actions);
    st.sessionId = session?.id || '';
  }

  /** Silme düğmesi: açık oturumda pasif ve nedeni yazılı. */
  function paintData(st) {
    const session = app.sessionOf(st.id);
    const label = 'Tablet verilerini sil';
    let action;
    if (!app.can('manage')) {
      action = permButton(app, label, 'manage', { variant: 'danger' });
    } else if (session) {
      action = blockedButton(label, 'Öğrenci şu an tablette. Önce oturumu kapatın.', { variant: 'danger' });
    } else {
      action = button(label, {
        variant: 'danger',
        onClick: (event) => busy(event.currentTarget, () => eraseData(st)),
      });
    }
    st.dataBox.replaceChildren(
      muted(session
        ? 'Öğrenci şu an tablette; veriler oturum kapanınca silinebilir.'
        : 'Oturum geçmişi, kullanım kayıtları, ek süreler ve kısıtlamalar silinir. Kantin hesabı etkilenmez.'),
      action,
    );
  }

  async function eraseData(st) {
    const pin = await confirmWithPin(app.root, {
      title: `${st.name} için tablet verileri silinsin mi?`,
      description: 'Oturum geçmişi, kullanım kayıtları, ek süreler ve kısıtlamalar kalıcı olarak '
        + 'silinir; süren bir kısıtlama da kalkar. Kantin hesabı etkilenmez. '
        + 'Onaylamak için PIN’inizi girin.',
      confirmLabel: 'Verileri sil',
    });
    if (!pin) return;
    try {
      const result = await app.call(`/students/${enc(st.id)}/history`, { method: 'DELETE', body: { pin } });
      const sessions = Number(result?.sessions || 0);
      app.toast(sessions ? `${st.name} için tablet verileri silindi (${num(sessions)} oturum).`
        : `${st.name} için tablet verileri silindi.`, 'good');
      if (current !== st) return;
      loadUsage(st, { quiet: true });
      loadHistory(st);
      await afterBlockChange(st);
    } catch (error) {
      app.toast(problem(error, 'Tablet verileri silinemedi. Tekrar deneyin.'), 'bad');
    }
  }

  async function loadUsage(st, { quiet = false } = {}) {
    st.usageAt = Date.now();
    const date = st.date;
    if (!quiet) st.usageBox.replaceChildren(muted('Yükleniyor…'));
    try {
      const result = await app.call(`/students/${enc(st.id)}/usage?localDate=${date}`);
      if (current !== st || st.date !== date) return;
      st.usage = result || {};
      paintUsage(st);
      paintGrantOptions(st);
      paintGrants(st);
    } catch (error) {
      if (current !== st || st.date !== date) return;
      st.usageBox.replaceChildren(muted(problem(error, 'Kullanım bilgisi alınamadı.')));
    }
  }

  function paintUsage(st) {
    const usage = st.usage?.usage || [];
    const total = usage.reduce((sum, item) => sum + (Number(item.usedSeconds) || 0), 0);
    const profile = st.usage?.profile;
    st.usageInfo.textContent = [
      `${trOf(st.date)}${st.date === st.today ? ' (bugün)' : ''}`,
      profile ? `Profil: ${profile.name}` : '',
      total ? `Toplam ${dur(total)}` : '',
    ].filter(Boolean).join(' · ');
    st.usageBox.replaceChildren(usageList(usage, {
      emptyText: profile ? 'Bu gün için kullanım yok.' : 'Öğrenci henüz hiç tablete giriş yapmadı.',
      onExtra: app.can('grant') && st.date >= st.today ? (item) => prefillGrant(st, item.packageName) : null,
    }));
  }

  // ------------------------------------------------------------- ek süre
  function buildGrantForm(st) {
    const appSelect = h('select', 'kit-select');
    appSelect.setAttribute('aria-label', 'Ek süre verilecek uygulama');
    const minutes = h('input', 'kit-input tb-minutes-input');
    minutes.type = 'text';
    minutes.inputMode = 'numeric';
    minutes.maxLength = 4;
    minutes.value = '10';
    minutes.setAttribute('aria-label', 'Ek süre (dakika)');
    const chips = h('div', 'tb-chips');
    const paintChips = () => {
      for (const chip of chips.children) chip.classList.toggle('on', chip.dataset.minutes === minutes.value.trim());
    };
    for (const value of GRANT_CHIPS) {
      const chip = h('button', 'kit-chip', `+${value} dk`);
      chip.type = 'button';
      chip.dataset.minutes = String(value);
      chip.addEventListener('click', () => {
        minutes.value = String(value);
        minutes.classList.remove('bad');
        paintChips();
      });
      chips.append(chip);
    }
    minutes.addEventListener('input', () => {
      minutes.classList.remove('bad');
      paintChips();
    });
    paintChips();
    const reason = h('input', 'kit-input');
    reason.type = 'text';
    reason.maxLength = 300;
    reason.placeholder = 'Gerekçe (isteğe bağlı)';
    reason.setAttribute('aria-label', 'Ek süre gerekçesi');
    const action = h('div', 'tb-actions');
    const minutesBox = h('label', 'tb-minutes');
    minutesBox.append(minutes, h('span', 'tb-sub', 'dk'));
    const amountRow = h('div', 'tb-actions');
    amountRow.append(chips, minutesBox);
    st.grantBox.append(appSelect, amountRow, reason, action);
    st.grant = { appSelect, minutes, reason, action };
    // Öğrencinin profili kullanım gelince bilinir; o ana kadar bütün
    // profillerin süreli uygulamaları listelenir.
    paintGrantOptions(st);
  }

  function paintGrantAction(st) {
    const { action } = st.grant;
    action.replaceChildren();
    if (!app.can('grant')) {
      action.append(permButton(app, 'Ek süre ver', 'grant', { variant: 'primary' }));
    } else if (st.date < st.today) {
      action.append(blockedButton('Ek süre ver', 'Geçmiş bir güne ek süre verilemez. Bugünü seçin.',
        { variant: 'primary' }));
    } else if (!st.grant.appSelect.options.length) {
      action.append(blockedButton('Ek süre ver', 'Hiçbir profilde süreli uygulama yok.', { variant: 'primary' }));
    } else {
      action.append(button('Ek süre ver', {
        variant: 'primary',
        onClick: (event) => busy(event.currentTarget, () => giveGrant(st)),
      }));
    }
  }

  /**
   * Seçenekler: önce öğrencinin son profilindeki süreli uygulamalar, sonra
   * diğer profillerdekiler. Sunucu, uygulama en az bir profilde izinliyse ek
   * süreyi kabul eder; sınırsız uygulamaya ek süre anlamsız olduğu için
   * listede yoktur.
   */
  function paintGrantOptions(st) {
    const { appSelect } = st.grant;
    const keep = appSelect.value;
    const ownProfile = app.profile(st.usage?.profile?.id);
    const timed = (profile) => (profile?.apps || []).filter((item) => item.allowed && !item.unlimited);
    const own = new Map(timed(ownProfile).map((item) => [item.packageName, item.appName || item.packageName]));
    const others = new Map();
    for (const profile of app.state.profiles) {
      if (profile.id === ownProfile?.id) continue;
      for (const item of timed(profile)) {
        if (!own.has(item.packageName) && !others.has(item.packageName)) {
          others.set(item.packageName, item.appName || item.packageName);
        }
      }
    }
    const sorted = (map) => [...map].sort((a, b) => a[1].localeCompare(b[1], 'tr'));
    const group = (label, entries) => {
      const node = h('optgroup');
      node.label = label;
      for (const [value, text] of entries) {
        const option = h('option', null, text);
        option.value = value;
        node.append(option);
      }
      return node;
    };
    appSelect.replaceChildren();
    if (own.size && others.size) {
      appSelect.append(group('Öğrencinin profili', sorted(own)), group('Diğer profiller', sorted(others)));
    } else {
      for (const [value, text] of sorted(own.size ? own : others)) {
        const option = h('option', null, text);
        option.value = value;
        appSelect.append(option);
      }
    }
    if ([...appSelect.options].some((option) => option.value === keep)) appSelect.value = keep;
    paintGrantAction(st);
  }

  function prefillGrant(st, packageName) {
    const { appSelect, minutes } = st.grant;
    if ([...appSelect.options].some((option) => option.value === packageName)) appSelect.value = packageName;
    st.grantBox.scrollIntoView({ block: 'center', behavior: 'smooth' });
    minutes.focus();
    minutes.select();
  }

  async function giveGrant(st) {
    const { appSelect, minutes, reason } = st.grant;
    const value = Number(minutes.value.trim());
    if (!Number.isInteger(value) || value < 1 || value > 1440) {
      minutes.classList.add('bad');
      minutes.focus();
      app.toast('Ek süre 1 ile 1440 dakika arasında olmalı.', 'bad');
      return;
    }
    const packageName = appSelect.value;
    if (!packageName) return;
    const appName = appSelect.selectedOptions[0]?.textContent || packageName;
    try {
      await app.call(`/students/${enc(st.id)}/apps/${enc(packageName)}/grants`, {
        method: 'POST',
        body: { extraSeconds: value * 60, localDate: st.date, reason: reason.value.trim() },
      });
      reason.value = '';
      app.toast(`${st.name}: ${appName} için ${dur(value * 60)} ek süre verildi.`, 'good');
      await loadUsage(st, { quiet: true });
    } catch (error) {
      app.toast(problem(error, 'Ek süre verilemedi. Tekrar deneyin.'), 'bad');
    }
  }

  function paintGrants(st) {
    const grants = st.usage?.grants || [];
    st.grantsBox.replaceChildren();
    if (!grants.length) {
      st.grantsBox.append(muted('Bu gün için ek süre verilmedi.'));
      return;
    }
    for (const grant of grants) {
      const row = h('div', 'tb-cmd');
      row.append(
        nameCell(`${grant.appName || grant.packageName} · +${dur(grant.extraSeconds)}`,
          [grant.reason, grant.createdBy, when(grant.createdAt)].filter(Boolean).join(' · ')),
        permButton(app, 'Geri al', 'grant', {
          variant: 'ghost',
          onClick: (event) => busy(event.currentTarget, () => revokeGrant(st, grant)),
        }),
      );
      st.grantsBox.append(row);
    }
  }

  async function revokeGrant(st, grant) {
    const yes = await confirmSimple(app.root, {
      title: 'Ek süre geri alınsın mı?',
      description: `${grant.appName || grant.packageName} için verilen ${dur(grant.extraSeconds)} geri alınır. `
        + 'Tablet bir sonraki bağlantıda süreyi düşer.',
      confirmLabel: 'Geri al',
      danger: true,
    });
    if (!yes) return;
    try {
      const result = await app.call(`/students/${enc(st.id)}/grants/${enc(grant.id)}/revoke`, { method: 'POST' });
      app.toast(result?.alreadyRevoked ? 'Bu ek süre zaten geri alınmıştı.' : 'Ek süre geri alındı.', 'good');
      await loadUsage(st, { quiet: true });
    } catch (error) {
      app.toast(problem(error, 'Ek süre geri alınamadı. Tekrar deneyin.'), 'bad');
    }
  }

  // ----------------------------------------------------------- kısıtlama
  function buildBlockForm(st) {
    const form = h('div', 'tb-block-form');
    const dateInput = dateField({ value: todayIso(1), label: 'Kısıtlamanın bitiş günü' });
    st.disposers.push(() => dateInput.destroy());
    let clock = '08:00';
    const clockField = clockInput(clock, 'Kısıtlamanın bitiş saati', (value) => { clock = value; });
    const setUntil = (iso, time) => {
      dateInput.set(iso);
      clock = time;
      clockField.value = time;
      clockField.classList.remove('bad');
    };
    const nowClock = () => {
      const now = new Date();
      return `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    };
    const presets = h('div', 'tb-chips');
    for (const [label, apply] of [
      ['Bugün sonuna kadar', () => setUntil(todayIso(), '23:59')],
      ['1 gün', () => setUntil(todayIso(1), nowClock())],
      ['1 hafta', () => setUntil(todayIso(7), nowClock())],
    ]) {
      const chip = h('button', 'kit-chip', label);
      chip.type = 'button';
      chip.addEventListener('click', apply);
      presets.append(chip);
    }
    const reason = h('input', 'kit-input');
    reason.type = 'text';
    reason.maxLength = 300;
    reason.placeholder = 'Gerekçe (isteğe bağlı)';
    reason.setAttribute('aria-label', 'Kısıtlama gerekçesi');
    const untilRow = h('div', 'tb-actions');
    untilRow.append(h('span', 'tb-sub', 'Bitiş'), dateInput.node, clockField);
    const action = h('div', 'tb-actions');
    action.append(permButton(app, 'Kısıtla', 'grant', {
      variant: 'danger',
      onClick: (event) => busy(event.currentTarget, () => blockStudent(st, dateInput.get(), clock, reason)),
    }));
    form.append(presets, untilRow, reason, action);
    return form;
  }

  async function blockStudent(st, date, clock, reason) {
    const time = parseClock(clock);
    if (!date || !time) {
      app.toast('Bitiş gününü ve saatini seçin.', 'bad');
      return;
    }
    // Saat dilimsiz gönderilir: sunucu bunu okulun saat diliminde okur.
    const until = `${date}T${time}:00`;
    if (new Date(until).getTime() <= Date.now()) {
      app.toast('Bitiş zamanı ileride olmalı.', 'bad');
      return;
    }
    const yes = await confirmSimple(app.root, {
      title: `${st.name} kısıtlansın mı?`,
      description: `Bitiş: ${trOf(date)} ${time}. O zamana kadar tablete giriş yapamaz; açık oturumu hemen kapanır.`,
      confirmLabel: 'Kısıtla',
      danger: true,
    });
    if (!yes) return;
    try {
      const result = await app.call(`/students/${enc(st.id)}/block`, {
        method: 'POST', body: { until, reason: reason.value.trim() },
      });
      reason.value = '';
      app.toast(result?.endedSessions ? 'Öğrenci kısıtlandı; açık oturumu kapatıldı.' : 'Öğrenci kısıtlandı.', 'good');
      await afterBlockChange(st);
    } catch (error) {
      app.toast(problem(error, 'Öğrenci kısıtlanamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function unblockStudent(st) {
    try {
      const result = await app.call(`/students/${enc(st.id)}/unblock`, { method: 'POST' });
      app.toast(result?.lifted ? 'Kısıtlama kaldırıldı.' : 'Kaldırılacak kısıtlama yoktu.', 'good');
      await afterBlockChange(st);
    } catch (error) {
      app.toast(problem(error, 'Kısıtlama kaldırılamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function afterBlockChange(st) {
    await loadBlocks(st);
    await app.refresh();
    await app.loadStudents();
  }

  async function loadBlocks(st) {
    try {
      const rows = await app.call(`/students/${enc(st.id)}/blocks`);
      if (current !== st) return;
      st.blocks = Array.isArray(rows) ? rows : [];
      paintBlocks(st);
    } catch (error) {
      if (current !== st) return;
      st.blockState.replaceChildren(muted(problem(error, 'Kısıtlama bilgisi alınamadı.')));
    }
  }

  function paintBlocks(st) {
    const active = st.blocks.find((item) => item.active && blockedActive(item.until));
    st.blockState.replaceChildren();
    if (active) {
      const line = h('div', 'tb-actions');
      line.append(inline(badge('Kısıtlı', 'bad'), `bitiş ${when(active.until)}`,
        active.reason ? `· ${active.reason}` : ''), h('span', 'kit-spacer'),
      permButton(app, 'Kısıtlamayı kaldır', 'grant', {
        onClick: (event) => busy(event.currentTarget, () => unblockStudent(st)),
      }));
      st.blockState.append(line);
    } else {
      st.blockState.append(muted('Kısıtlı değil. Kısıtlanan öğrenci bitişe kadar hiçbir tablete giriş yapamaz.'));
    }
    st.blockHistory.replaceChildren();
    const past = st.blocks.filter((item) => item !== active).slice(0, 5);
    if (!past.length) return;
    st.blockHistory.append(h('span', 'tb-sub', 'Önceki kısıtlamalar'));
    for (const item of past) {
      const state = item.liftedAt ? badge('Kaldırıldı', 'dim') : badge('Bitti', 'dim');
      const row = h('div', 'tb-cmd');
      row.append(nameCell(`Bitiş ${when(item.until)}`,
        [item.reason, item.createdBy, when(item.createdAt)].filter(Boolean).join(' · ')), state);
      st.blockHistory.append(row);
    }
  }

  // --------------------------------------------------------------- oturum
  async function endSession(st, session) {
    const yes = await confirmSimple(app.root, {
      title: 'Oturum kapatılsın mı?',
      description: `${st.name} ${session.device_name || 'tabletten'} çıkarılır; o ana kadarki kullanımı kaydedilir.`,
      confirmLabel: 'Oturumu kapat',
      danger: true,
    });
    if (!yes) return;
    try {
      const result = await app.call(`/sessions/${enc(session.id)}/end`, { method: 'POST', body: { message: '' } });
      app.toast(result?.alreadyEnded ? 'Oturum zaten kapanmıştı.' : 'Oturum kapatıldı.', 'good');
      await app.refresh();
      loadHistory(st);
    } catch (error) {
      app.toast(problem(error, 'Oturum kapatılamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function loadHistory(st) {
    try {
      const rows = await app.call(`/sessions?limit=20&studentId=${enc(st.id)}`);
      if (current !== st) return;
      const history = dataTable({
        columns: [
          { key: 'deviceName', label: 'Tablet', width: 'minmax(0, 1.2fr)',
            cell: (row) => (row.deviceDeleted ? h('span', 'tb-sub', row.deviceName) : row.deviceName) },
          { key: 'loginAt', label: 'Giriş', width: '130px', cell: (row) => when(row.loginAt) },
          { key: 'endedAt', label: 'Bitiş', width: '130px',
            cell: (row) => (row.state === 'active' ? badge('Açık', 'good') : when(row.endedAt)) },
          { key: 'endReason', label: 'Neden', width: 'minmax(0, 1.2fr)',
            cell: (row) => (row.state === 'active' ? '—' : END_REASONS[row.endReason] || '—') },
        ],
        rows: Array.isArray(rows) ? rows : [],
        dense: true,
        empty: emptyState({ title: 'Henüz tablete giriş yapmadı' }),
      });
      st.historyBox.replaceChildren(history.node);
    } catch (error) {
      if (current !== st) return;
      st.historyBox.replaceChildren(muted(problem(error, 'Oturum geçmişi alınamadı.')));
    }
  }

  // ---------------------------------------------------------------- rapor
  const report = { date: todayIn(DEFAULT_TZ), data: null, loadedAt: 0, query: '' };
  const reportBody = h('div', 'kit-body');
  const reportHead = h('div', 'tb-actions tb-report-head');
  const reportDate = dateField({ value: report.date, label: 'Rapor günü', onChange: (iso) => {
    if (!iso || iso === report.date) return;
    report.date = iso;
    loadReport();
  } });
  disposers.push(() => reportDate.destroy());
  const reportSummary = h('span', 'tb-sub');
  const csvButton = button('CSV indir', {
    title: 'Öğrenci ve uygulama tablosunu Excel’de açılabilir dosya olarak indirir',
    onClick: () => exportCsv(),
  });
  csvButton.disabled = true;
  reportHead.append(h('span', 'tb-sub', 'Gün'), reportDate.node, reportSummary, h('span', 'kit-spacer'), csvButton);
  const chartBox = h('div', 'tb-chart');
  const reportSearch = h('input', 'kit-input');
  reportSearch.type = 'search';
  reportSearch.placeholder = 'Öğrenci ya da uygulama ara';
  reportSearch.setAttribute('aria-label', 'Raporda ara');
  reportSearch.addEventListener('input', () => {
    report.query = reportSearch.value;
    paintReportTable();
  });
  const reportTable = dataTable({
    columns: [
      { key: 'studentName', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', sortable: true,
        cell: (row) => row.studentName || 'Adı bilinmiyor' },
      { key: 'appName', label: 'Uygulama', width: 'minmax(0, 1.4fr)', sortable: true,
        cell: (row) => row.appName || row.packageName },
      { key: 'usedSeconds', label: 'Süre', width: '120px', align: 'num', sortable: true,
        sortValue: (row) => Number(row.usedSeconds) || 0, cell: (row) => dur(row.usedSeconds) },
    ],
    rows: [],
    sort: { key: 'usedSeconds', dir: 'desc' },
    empty: emptyState({ title: 'Bu gün için kullanım yok' }),
  });
  const tableTools = h('div', 'tb-card-tools');
  tableTools.append(reportSearch);
  const tableBox = h('div');
  tableBox.append(tableTools, reportTable.node);
  reportBody.append(reportHead, card('Uygulamalara göre', chartBox), card('Öğrenci ve uygulama', tableBox));
  reportView.append(reportBody);

  async function loadReport() {
    const date = report.date;
    reportSummary.textContent = 'Yükleniyor…';
    try {
      const data = await app.call(`/reports/daily?localDate=${date}`);
      if (report.date !== date) return;
      report.data = data || {};
      report.loadedAt = Date.now();
      paintReport();
    } catch (error) {
      if (report.date !== date) return;
      reportSummary.textContent = problem(error, 'Rapor alınamadı. Tekrar deneyin.');
    }
  }

  function paintReport() {
    const data = report.data || {};
    const apps = Array.isArray(data.apps) ? data.apps : [];
    const students = Array.isArray(data.students) ? data.students : [];
    reportSummary.textContent = data.totalSeconds
      ? `Toplam ${dur(data.totalSeconds)} · ${num(students.length)} öğrenci · ${num(apps.length)} uygulama`
      : 'Bu gün için kullanım yok.';
    chartBox.replaceChildren(apps.length
      ? barChart(apps.map((item) => ({
        label: item.appName || item.packageName,
        value: Number(item.usedSeconds) || 0,
        display: dur(item.usedSeconds),
      })), { max: 12 })
      : muted('Bu gün için kullanım yok.'));
    csvButton.disabled = !(data.rows || []).length;
    paintReportTable();
  }

  function paintReportTable() {
    const rows = (report.data?.rows || [])
      .filter((row) => matchesText(report.query, row.studentName, row.appName, row.packageName));
    reportTable.update({ rows });
  }

  function exportCsv() {
    const rows = report.data?.rows || [];
    if (!rows.length) return;
    csvBlob(
      ['Öğrenci', 'Uygulama', 'Paket', 'Süre (dk)'],
      rows.map((row) => [row.studentName || '', row.appName || '', row.packageName,
        Math.round((Number(row.usedSeconds) || 0) / 60)]),
      `tablet-kullanim-${report.date}`,
    );
    app.toast(`${num(rows.length)} satır indirildi.`, 'good');
  }

  // ------------------------------------------------------------- dış yüz
  return {
    node,
    open(id) {
      if (mode !== 'list') {
        modes.set('list');
        showMode('list');
      }
      if (!app.state.studentsWanted) app.loadStudents();
      open(id);
    },
    shown() {
      if (!app.state.studentsWanted) app.loadStudents();
      paintList();
    },
    update() {
      paintWarn();
      paintList();
      const st = current;
      if (!st) return;
      const session = app.sessionOf(st.id);
      if ((session?.id || '') !== st.sessionId) {
        paintHead(st);
        paintData(st);
        loadHistory(st);
      }
      // Tabletteyken bugünün kullanımı yoklamayla tazelenir (yazılan form
      // alanlarına dokunulmaz); geçmiş gün değişmez, okunmaz.
      if (session && st.date === st.today && Date.now() - st.usageAt > 25_000) loadUsage(st, { quiet: true });
    },
    destroy() {
      for (const fn of current?.disposers || []) fn();
      for (const fn of disposers) fn();
    },
  };
}
