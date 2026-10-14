// Tabletler sekmesi — liste, toplu eylemler, tablet ekleme ve tablet çekmecesi.
//
// Çekmece yoklamada YENİDEN KURULMAZ: her bölümün bir imzası vardır ve yalnız
// imzası değişen bölüm boyanır. Odak bir bölümün içindeyse (açılır kutu
// seçiliyken) o bölüm atlanır; kullanıcının elindeki seçim kaybolmaz.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/devices.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import {
  button, bytes, confirmSimple, confirmWithPin, copyText, foldText, h, menuButton, num, pollLoop,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { badge, card, drawer, emptyState } from '../../ui-kit/layout.js';
import {
  COMMAND_LABELS, COMMAND_STATUS, DEFAULT_TZ, END_REASONS, OWN_PACKAGE, POLICY_HEALTH, REASONS,
  appVersionText, batteryText, busy, clockOf, commandResult, dur, facts, formDialog, inline,
  loginLockBadge, loginLockMinutes, matchesText, modal, msOf, muted, nameCell, permButton, problem,
  selfUpdateState, since, statusBadge, syncBadge, todayIn, usageList, when, windowsSummary,
} from './common.js';

const NARROW = '(max-width: 900px)';
const enc = encodeURIComponent;

const STATUS_FILTERS = [
  { value: '', label: 'Kullanımdakiler' },
  { value: 'online', label: 'Çevrimiçi' },
  { value: 'offline', label: 'Çevrimdışı' },
  { value: 'session', label: 'Öğrenci kullanıyor' },
  { value: 'pending', label: 'Eşleme bekliyor' },
  { value: 'locked', label: 'Kilitli' },
  { value: 'login_locked', label: 'Giriş kilitli' },
  { value: 'behind', label: 'Profili geride' },
  { value: 'revoked', label: 'Eşlemesi iptal edilen' },
];

const STATUS_ORDER = { online: 0, offline: 1, pending: 2, revoked: 3 };

const OFFLINE_MODES = [
  ['local', 'Tablet ayarına bırak'],
  ['allowed', 'İzin ver'],
  ['denied', 'Kapat'],
];

const LOCK_PRESETS = ['Ders saati — tabletler kilitli.', 'Tabletleri teslim edin.'];

const SELF_UPDATE_WARNING = 'Tablet, Uygulamalar sekmesinde yayında olan TabletKontrol sürümünü '
  + 'bekleme süresi olmadan indirir ve kurar. Açık öğrenci oturumu varsa kullanımı gönderilip '
  + 'kapatılır; öğrenci yeniden giriş yapar. Tablet zaten güncelse bir şey olmaz.';

function deviceLine(device) {
  const model = [device.manufacturer, device.model].filter(Boolean).join(' ');
  const android = device.android_version ? `Android ${device.android_version}` : '';
  return [model, android].filter(Boolean).join(' · ');
}

function subtitleOf(device) {
  switch (device.status) {
    case 'online':
      return device.active_student_name
        ? `Çevrimiçi · ${device.active_student_name} kullanıyor` : 'Çevrimiçi · boşta';
    case 'offline':
      return device.last_seen_at ? `Çevrimdışı · son görülme ${since(device.last_seen_at)}` : 'Çevrimdışı';
    case 'pending':
      return 'Eşleme bekliyor';
    case 'revoked':
      return 'Eşlemesi iptal edildi';
    default:
      return '';
  }
}

function matchesStatus(device, status, now) {
  switch (status) {
    case '': return device.status !== 'revoked';
    case 'session': return Boolean(device.active_session_id);
    case 'locked': return Boolean(device.locked) && device.status !== 'revoked';
    case 'login_locked': return loginLockMinutes(device, now) > 0;
    case 'behind': return device.sync_state === 'behind';
    default: return device.status === status;
  }
}

/** Kartın gövdesi — kit `card()` çocuklarına iç boşluk vermez. */
function body(...nodes) {
  const box = h('div', 'kit-card-body tb-stack');
  box.append(...nodes.filter(Boolean));
  return box;
}

/** Etiket · değer · eylemler satırı (çekmecedeki uzaktan yönetim). */
function line(label, value, ...actions) {
  const row = h('div', 'tb-line');
  row.append(h('span', 'tb-line-label', label));
  const valueBox = h('div', 'tb-line-value');
  if (value instanceof Node) valueBox.append(value);
  else valueBox.textContent = value;
  row.append(valueBox);
  const tools = h('div', 'tb-line-actions');
  tools.append(...actions.filter(Boolean));
  row.append(tools);
  return row;
}

function signature(device, keys) {
  return JSON.stringify(keys.map((key) => device[key] ?? null));
}

export function devicesView(app) {
  const node = h('div', 'tb-pane');
  const query = window.matchMedia(NARROW);
  let narrow = query.matches;
  let values = { q: '', status: '' };
  let table = null;
  let current = null;      // açık çekmece
  let codeView = null;     // açık eşleme kodu penceresi

  // ------------------------------------------------------------- şerit
  const bar = filterBar({
    compact: true,
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Tablet, öğrenci ya da uygulama ara', primary: true },
      { kind: 'select', key: 'status', label: 'Durum', primary: true, options: STATUS_FILTERS },
    ],
    onChange: (next) => {
      values = next;
      paintTable();
    },
    actions: [permButton(app, 'Tablet ekle', 'manage', {
      variant: 'primary',
      onClick: (event) => busy(event.currentTarget, addTablet),
    })],
  });

  const selbar = h('div', 'tb-selbar');
  selbar.hidden = true;
  const tableSlot = h('div', 'tb-table');
  const scroller = h('div', 'kit-body');
  scroller.append(selbar, card(null, tableSlot));
  node.append(bar.node, scroller);

  // ------------------------------------------------------------- tablo
  function columns() {
    const name = {
      key: 'name', label: 'Tablet', width: 'minmax(0, 1.5fr)', sortable: true,
      sortValue: (row) => foldText(row.name),
      cell: (row) => nameCell(row.name || 'Adsız tablet',
        deviceLine(row) || (row.status === 'pending' ? 'Henüz bağlanmadı' : '')),
    };
    const status = {
      key: 'status', label: 'Durum', width: '170px', sortable: true,
      sortValue: (row) => STATUS_ORDER[row.status] ?? 9,
      cell: (row) => inline(statusBadge(row),
        row.locked && row.status !== 'revoked' ? badge('Kilitli', 'bad') : null,
        loginLockBadge(row, app.now())),
    };
    const student = {
      key: 'active_student_name', label: 'Öğrenci', width: 'minmax(0, 1.2fr)', sortable: true,
      sortValue: (row) => foldText(row.active_student_name || ''),
      cell: (row) => row.active_student_name || '—',
    };
    const foreground = {
      key: 'foreground_app_name', label: 'Açık uygulama', width: 'minmax(0, 1.2fr)',
      cell: (row) => (row.active_session_id && row.foreground_app_name) || '—',
    };
    const profile = {
      key: 'profile_name', label: 'Profil', width: 'minmax(0, 1.3fr)', sortable: true,
      sortValue: (row) => foldText(row.profile_name || ''),
      cell: (row) => (row.status === 'revoked' ? '—'
        : inline(row.profile_name || 'Atanmamış', syncBadge(row))),
    };
    const battery = {
      key: 'battery_percent', label: 'Pil', width: '100px', sortable: true,
      sortValue: (row) => row.battery_percent ?? -1,
      cell: (row) => {
        if (row.battery_percent === null || row.battery_percent === undefined) return '—';
        const low = row.battery_percent < 20 && !row.charging;
        return low ? badge(batteryText(row), 'warn') : batteryText(row);
      },
    };
    const seen = {
      key: 'last_seen_at', label: 'Son görülme', width: '130px', sortable: true,
      sortValue: (row) => msOf(row.last_seen_at) || 0,
      cell: (row) => {
        if (row.status === 'pending') return row.enrollment_code_expired ? 'Kodun süresi doldu' : 'Kod bekleniyor';
        if (!row.last_seen_at) return '—';
        const text = h('span', null, since(row.last_seen_at));
        text.title = when(row.last_seen_at);
        return text;
      },
    };
    return narrow ? [name, status, student, seen]
      : [name, status, student, foreground, profile, battery, seen];
  }

  function visibleRows() {
    const now = app.now();
    return app.state.devices
      .filter((device) => matchesStatus(device, values.status || '', now))
      .filter((device) => matchesText(values.q, device.name, device.active_student_name,
        device.foreground_app_name, device.profile_name, device.model, device.manufacturer));
  }

  function emptyNode() {
    if (!app.state.devices.length) {
      return emptyState({
        title: 'Henüz tablet yok',
        text: 'Tablet ekleyin; verilen kodu tablette girince tablet burada görünür.',
      });
    }
    return emptyState({
      title: 'Bu filtreye uyan tablet yok',
      actions: [button('Filtreyi temizle', { onClick: () => bar.reset() })],
    });
  }

  function buildTable() {
    table = dataTable({
      columns: columns(),
      rows: visibleRows(),
      // Toplu eylemlerin hepsi yönetme yetkisi ister; yetki yoksa seçim kutusu
      // hiç çizilmez — seçip hiçbir şey yapamamak kafa karıştırır.
      selectable: app.can('manage'),
      onRow: (row) => open(row.id),
      onSelect: () => paintSelbar(),
      rowKey: (row) => String(row.id),
      sort: { key: 'name', dir: 'asc' },
      empty: emptyNode(),
    });
    tableSlot.replaceChildren(table.node);
    paintSelbar();
  }

  function paintTable() {
    table.update({ rows: visibleRows(), empty: emptyNode() });
    paintSelbar();
  }

  const onNarrow = () => {
    narrow = query.matches;
    buildTable();
  };
  if (query.addEventListener) query.addEventListener('change', onNarrow);
  else query.addListener(onNarrow);
  app.onDispose(() => {
    if (query.removeEventListener) query.removeEventListener('change', onNarrow);
    else query.removeListener(onNarrow);
  });

  // ----------------------------------------------------- toplu eylemler
  function paintSelbar() {
    const chosen = table ? table.selectedRows() : [];
    selbar.hidden = chosen.length === 0;
    if (!chosen.length) {
      selbar.replaceChildren();
      return;
    }
    selbar.replaceChildren(
      h('span', 'tb-selcount', `${num(chosen.length)} tablet seçili`),
      button('Kilitle', { onClick: (event) => busy(event.currentTarget, bulkLock) }),
      button('Kilidi aç', {
        onClick: (event) => busy(event.currentTarget,
          () => runBulk('unlock', {}, 'tabletin kilidi açıldı.')),
      }),
      button('Mesaj gönder', { onClick: (event) => busy(event.currentTarget, bulkMessage) }),
      menuButton('Diğer', [
        { label: 'Duyuru yayınla', onClick: bulkAnnounce },
        { label: 'Duyuruyu kaldır',
          onClick: () => runBulk('announce', { announcement: '' }, 'tabletten duyuru kaldırıldı.') },
        { label: 'Profil ata', onClick: bulkAssign },
        { label: 'Tablet uygulamasını şimdi güncelle', onClick: bulkSelfUpdate },
        { label: 'Tanılama iste',
          onClick: () => runBulk('command', { command: { type: 'diagnostics', payload: {} } },
            'tabletten tanı raporu istendi.') },
        // Tekli kaldırma gibi ek süre yetkisi (grant) ister.
        ...(app.can('grant') ? [{ label: 'Giriş kilidini kaldır',
          onClick: () => runBulk('reset_login_lock', {}, 'tabletin giriş kilidi kaldırıldı.') }] : []),
        { label: 'Sil', onClick: bulkDelete },
      ]),
      h('span', 'kit-spacer'),
      button('Seçimi kaldır', {
        variant: 'ghost',
        onClick: () => {
          table.clearSelection();
          paintSelbar();
        },
      }),
    );
  }

  async function runBulk(action, extra, verb) {
    const ids = table.selection();
    if (!ids.length) return;
    try {
      const result = await app.call('/devices/bulk', {
        method: 'POST', body: { deviceIds: ids, action, ...extra },
      });
      const done = Number(result?.okCount || 0);
      const missed = Number(result?.failedCount || 0);
      if (!missed) {
        app.toast(`${num(done)} ${verb}`, 'good');
      } else {
        const first = (result?.results || []).find((item) => !item.ok)?.message;
        app.toast(`${num(done)} tablette yapıldı, ${num(missed)} tablette yapılamadı`
          + `${first ? `: ${first}` : '.'}`, 'warn');
      }
      table.clearSelection();
      paintSelbar();
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Toplu işlem yapılamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function bulkLock() {
    const count = table.selection().length;
    const answer = await formDialog(app.root, {
      title: `${num(count)} tablet kilitlensin mi?`,
      description: 'Açık oturumlar kapanır; tabletlerde kilit ekranı görünür.',
      fields: [lockField()],
      confirmLabel: 'Kilitle',
    });
    if (answer) await runBulk('lock', { lockMessage: answer.message }, 'tablet kilitlendi.');
  }

  async function bulkSelfUpdate() {
    const count = table.selection().length;
    const yes = await confirmSimple(app.root, {
      title: `${num(count)} tablet şimdi güncellensin mi?`,
      description: SELF_UPDATE_WARNING,
      confirmLabel: 'Şimdi güncelle',
      danger: true,
    });
    if (!yes) return;
    await runBulk('command', { command: { type: 'self_update', payload: {} } },
      'tablete güncelleme komutu gönderildi.');
  }

  async function bulkMessage() {
    const answer = await formDialog(app.root, {
      title: 'Seçili tabletlere mesaj',
      description: 'Mesaj tablet ekranında bir kez görünür; öğrenci kapatabilir.',
      fields: [messageField()],
      confirmLabel: 'Gönder',
    });
    if (!answer) return;
    await runBulk('command', { command: { type: 'message', payload: { text: answer.text } } },
      'tablete mesaj gönderildi.');
  }

  async function bulkAnnounce() {
    const answer = await formDialog(app.root, {
      title: 'Seçili tabletlere duyuru',
      description: 'Duyuru, kaldırılana kadar tablet ekranında görünür.',
      fields: [announcementField('')],
      confirmLabel: 'Yayınla',
    });
    if (answer) await runBulk('announce', { announcement: answer.text }, 'tablete duyuru gönderildi.');
  }

  async function bulkAssign() {
    if (!app.state.profiles.length) {
      app.toast('Önce Profiller sekmesinden bir profil oluşturun.', 'warn');
      return;
    }
    const answer = await formDialog(app.root, {
      title: 'Seçili tabletlere profil ata',
      description: 'Tabletler yeni profili bir sonraki bağlantıda alır.',
      fields: [profileField('')],
      confirmLabel: 'Profili ata',
    });
    if (answer) await runBulk('assign_profile', { profileId: answer.profileId }, 'tablete profil atandı.');
  }

  async function bulkDelete() {
    const ids = table.selection();
    if (!ids.length) return;
    const paired = ids.some((id) => {
      const device = app.device(id);
      return device?.paired && device.status !== 'revoked';
    });
    const pin = await confirmWithPin(app.root, {
      title: `${num(ids.length)} tablet silinsin mi?`,
      description: `${paired
        ? 'Tabletler Kontrol Merkezi’nden çıkarılır, açık oturumlar kapanır; kullanım geçmişi kalır.'
        : 'Tablet kayıtları listeden kalkar; kullanım geçmişi kalır.'} `
        + 'Onaylamak için PIN’inizi girin.',
      confirmLabel: 'Sil',
    });
    if (!pin) return;
    for (const id of ids) app.forgetInventory(id);
    if (current && ids.includes(current.id)) current.drawer.close();
    await runBulk('delete', { pin }, 'tablet silindi.');
  }

  // ------------------------------------------------------ pencere alanları
  function lockField(value = '') {
    return {
      key: 'message', label: 'Kilit ekranındaki not (isteğe bağlı)', type: 'textarea',
      maxLength: 200, value, placeholder: 'Örn. Ders saati — tabletler kilitli.',
      presets: LOCK_PRESETS,
    };
  }

  function messageField() {
    return {
      key: 'text', label: 'Mesaj', type: 'textarea', maxLength: 500, required: true,
      placeholder: 'Örn. 5 dakika sonra tabletleri teslim edin.',
    };
  }

  function announcementField(value) {
    return {
      key: 'text', label: 'Duyuru', type: 'textarea', maxLength: 500, required: true, value,
      placeholder: 'Örn. Yarın tabletler 10.00’da açılacak.',
    };
  }

  function profileField(value) {
    return {
      key: 'profileId', label: 'Profil', type: 'select', value: value || app.state.profiles[0]?.id,
      options: app.state.profiles.map((profile) => ({ value: profile.id, label: profile.name })),
    };
  }

  // -------------------------------------------------------- tablet ekle
  function suggestName() {
    const names = new Set(app.state.devices.map((device) => device.name));
    let index = app.state.devices.length + 1;
    while (names.has(`Tablet ${index}`)) index += 1;
    return `Tablet ${index}`;
  }

  async function addTablet() {
    const profiles = app.state.profiles;
    const answer = await formDialog(app.root, {
      title: 'Tablet ekle',
      description: 'Kayıt açılınca tablete girilecek tek kullanımlık kod gösterilir.',
      fields: [
        { key: 'name', label: 'Tablet adı', required: true, minLength: 2, maxLength: 100,
          value: suggestName(), placeholder: 'Örn. 5-A Tablet 3' },
        profiles.length ? {
          key: 'profileId', label: 'Profil', type: 'select', value: '',
          options: [{ value: '', label: 'Sonra seçerim' },
            ...profiles.map((profile) => ({ value: profile.id, label: profile.name }))],
        } : null,
      ].filter(Boolean),
      confirmLabel: 'Kodu oluştur',
    });
    if (!answer) return;
    try {
      const result = await app.call('/devices', {
        method: 'POST',
        body: { name: answer.name, ...(answer.profileId ? { profileId: answer.profileId } : {}) },
      });
      await app.refresh();
      showCode(result);
    } catch (error) {
      app.toast(problem(error, 'Tablet eklenemedi. Tekrar deneyin.'), 'bad');
    }
  }

  /**
   * Eşleme kodu penceresi. Kod sunucuda yalnız özetiyle durur; pencere
   * kapanınca bir daha görüntülenemez, gerekirse yenisi alınır. Bu yüzden
   * dışarı tıklamak pencereyi kapatmaz.
   *
   * Pencere açıkken ekran 8 saniyede bir yoklar: tablet eşlenince pencere
   * "Tablet bağlandı" der, yönetici sonucu beklemeden görür.
   */
  function showCode(result) {
    codeView?.close(null);
    const view = {
      id: result.id,
      code: String(result.enrollmentCode || ''),
      display: String(result.enrollmentCodeDisplay || ''),
      expires: msOf(result.expiresAt),
      paired: false,
      timer: null,
      fast: null,
    };
    const { card: box, close } = modal(app.root, {
      title: `${result.name || 'Tablet'} için eşleme kodu`,
      className: 'tb-code-dialog',
      onClose: () => {
        window.clearInterval(view.timer);
        view.fast?.stop();
        if (codeView === view) codeView = null;
      },
    });
    view.close = close;

    const codeNode = h('div', 'tb-code');
    const timer = h('div', 'tb-timer');
    timer.setAttribute('role', 'timer');
    const renew = permButton(app, 'Yeni kod al', 'manage', {
      onClick: (event) => busy(event.currentTarget, () => renewCode(view)),
    });
    const tools = h('div', 'tb-code-tools');
    tools.append(
      button('Kodu kopyala', {
        onClick: async () => {
          const copied = await copyText(view.code);
          app.toast(copied ? 'Kod panoya kopyalandı.' : 'Kopyalanamadı; kodu elle yazın.',
            copied ? 'good' : 'warn');
        },
      }),
      timer,
      renew,
    );
    const steps = h('ol', 'tb-steps');
    for (const text of [
      'Tablette TabletKontrol uygulamasını açıp “Kontrol Merkezi koduyla eşleştir”e dokunun.',
      'Kontrol Merkezi adresini ve bu kodu yazın.',
      '“Tableti Eşleştir”e dokunun; tablet birkaç saniye içinde bu listede görünür.',
    ]) steps.append(h('li', null, text));
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Tamam', { variant: 'primary', onClick: () => close(null) }));
    box.append(codeNode, tools, steps, actions);

    view.paint = () => {
      const shown = view.display || `${view.code.slice(0, 4)}-${view.code.slice(4)}`;
      codeNode.textContent = shown;
      codeNode.setAttribute('aria-label', `Eşleme kodu: ${view.code.split('').join(' ')}`);
    };
    view.tick = () => {
      if (view.paired) {
        timer.replaceChildren(badge('Tablet bağlandı', 'good'));
        codeNode.classList.add('tb-code-done');
        renew.hidden = true;
        window.clearInterval(view.timer);
        view.fast?.stop();
        return;
      }
      const left = Math.floor((view.expires - app.now()) / 1000);
      const expired = Number.isNaN(left) || left <= 0;
      codeNode.classList.toggle('tb-code-expired', expired);
      renew.hidden = !expired;
      timer.textContent = expired ? 'Kodun süresi doldu.'
        : `Kalan süre ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
    };
    view.paint();
    view.tick();
    view.timer = window.setInterval(view.tick, 1000);
    view.fast = pollLoop({ every: 8000, run: () => app.refresh({ poll: true }) });
    codeView = view;
    actions.lastChild.focus();
  }

  async function renewCode(view) {
    try {
      const result = await app.call(`/devices/${enc(view.id)}/enrollment-code`, { method: 'POST', body: {} });
      view.code = String(result.enrollmentCode || '');
      view.display = String(result.enrollmentCodeDisplay || '');
      view.expires = msOf(result.expiresAt);
      view.paint();
      view.tick();
      app.toast('Yeni kod oluşturuldu; önceki kod artık geçersiz.', 'good');
    } catch (error) {
      app.toast(problem(error, 'Yeni kod alınamadı. Tekrar deneyin.'), 'bad');
    }
  }

  // ------------------------------------------------------------ çekmece
  function open(id) {
    const device = app.device(id);
    if (!device) return;
    current?.drawer.close();
    const ctl = {
      id,
      sigs: {},
      commands: [],
      apps: null,
      usageAt: 0,
      profileTouched: false,
      slots: {},
      usageBox: h('div'),
      commandList: h('div', 'tb-cmds'),
      diagnostics: null,
      diagList: h('div', 'tb-cmds'),
      doneDiagnostics: null,
      historyBox: h('div'),
      appsList: h('div', 'tb-apps'),
      appsCount: h('span', 'tb-sub'),
      appsFilter: { q: '', all: false },
    };
    ctl.drawer = drawer(app.root, {
      title: device.name || 'Tablet',
      subtitle: subtitleOf(device) || ' ',
      onClose: () => { if (current === ctl) current = null; },
    });
    ctl.drawer.node.classList.add('tb-drawer');
    ctl.subtitle = ctl.drawer.node.querySelector('.kit-drawer-sub');
    for (const key of ['status', 'session', 'control', 'profile', 'software', 'commands', 'apps',
      'history', 'record']) {
      ctl.slots[key] = h('div', 'tb-slot');
      ctl.drawer.body.append(ctl.slots[key]);
    }
    buildAppsTools(ctl);
    ctl.slots.history.append(card('Oturum geçmişi', ctl.historyBox));
    ctl.historyBox.append(muted('Yükleniyor…'));
    current = ctl;
    paintDrawer({ force: true });
    loadHistory(ctl);
  }

  function paintDrawer({ force = false } = {}) {
    const ctl = current;
    if (!ctl) return;
    const device = app.device(ctl.id);
    if (!device) {
      ctl.drawer.close();
      app.toast('Bu tablet kaydı artık yok.', 'warn');
      return;
    }
    ctl.drawer.setTitle(device.name || 'Tablet');
    if (ctl.subtitle) ctl.subtitle.textContent = subtitleOf(device);
    const focused = document.activeElement;
    const editing = focused && /^(SELECT|INPUT|TEXTAREA)$/.test(focused.tagName);
    const step = (key, sig, painter) => {
      if (!force && ctl.sigs[key] === sig) return;
      // Kullanıcı bölümdeki bir alanı kullanıyorsa (açılır kutu, arama) bölüm
      // yeniden kurulmaz; imza güncellenmediği için odak çıkınca boyanır.
      if (!force && editing && ctl.slots[key].contains(focused)) return;
      ctl.sigs[key] = sig;
      painter(ctl, device);
    };
    // Dakika tıkı "5 dakika önce" yazısını tazeler.
    const minute = Math.floor(Date.now() / 60_000);
    step('status', signature(device, [
      'status', 'locked', 'last_seen_at', 'active_student_name', 'foreground_app_name',
      'battery_percent', 'charging', 'policy_health', 'pending_usage_batches', 'pending_commands',
      'manufacturer', 'model', 'android_version', 'enrollment_expires_at',
      'enrollment_code_expired', 'revoked_at', 'policy_detail',
    ]) + minute, paintStatus);
    step('session', String(device.active_session_id || ''), paintSession);
    // Giriş kilidi sürerken kalan dakika her dakika tazelenir.
    const loginLock = loginLockMinutes(device, app.now());
    step('control', signature(device,
      ['status', 'locked', 'lock_message', 'announcement', 'offline_mode', 'login_locked_until'])
      + (loginLock ? `:${loginLock}` : ''), paintControl);
    if (!ctl.profileTouched) {
      const profiles = JSON.stringify(app.state.profiles.map((item) => [item.id, item.name, item.revision]));
      step('profile', signature(device, ['status', 'paired', 'profile_id', 'sync_state']) + profiles,
        paintProfile);
    }
    step('software', signature(device, ['status', 'paired', 'app_version', 'app_version_code',
      'self_update']) + JSON.stringify(app.state.selfRelease) + minute, paintSoftware);
    step('commands', signature(device, ['status', 'paired']), paintCommands);
    step('apps', signature(device, ['status', 'paired', 'profile_id', 'profile_revision']), paintApps);
    step('record', signature(device, ['status', 'paired', 'name']), paintRecord);
    if (!force) {
      // Yoklamada yalnız gerektiği kadar okunur: bekleyen komut varsa durumu,
      // oturum açıksa bugünkü kullanım.
      if (ctl.commands.some((item) => item.status === 'pending' || item.status === 'delivered')) {
        loadCommands(ctl);
      }
      if (device.active_session_id && Date.now() - ctl.usageAt > 25_000) loadUsage(ctl);
    }
  }

  function codeState(device) {
    if (device.enrollment_code_expired) return 'Süresi doldu — yeni kod alın';
    const left = Math.ceil((msOf(device.enrollment_expires_at) - app.now()) / 60_000);
    return Number.isNaN(left) ? null : `${Math.max(1, left)} dk daha geçerli`;
  }

  function paintStatus(ctl, device) {
    const policy = POLICY_HEALTH[device.policy_health];
    const live = device.status !== 'revoked';
    const rows = [
      ['Bağlantı', inline(statusBadge(device), device.locked && live ? badge('Kilitli', 'bad') : null)],
      ['Son görülme', device.last_seen_at
        ? `${since(device.last_seen_at)} · ${when(device.last_seen_at)}`
        : (device.status === 'pending' ? 'Henüz bağlanmadı' : null)],
      device.status === 'pending' ? ['Eşleme kodu', codeState(device)] : null,
      ['Öğrenci', device.active_student_name || (device.paired && live ? 'Oturum yok' : null)],
      ['Açık uygulama', device.active_session_id ? device.foreground_app_name : null],
      ['Pil', device.battery_percent === null || device.battery_percent === undefined ? null : batteryText(device)],
      ['Kısıtlamalar', policy && live ? badge(policy[0], policy[1]) : null],
      // Tabletin son politika uygulamasındaki hatanın özeti (v2.2); teknik
      // metindir, destek için olduğu gibi gösterilir.
      ['Kısıtlama ayrıntısı', live && device.policy_health !== 'ok' && device.policy_detail
        ? device.policy_detail : null],
      ['Gönderilmemiş kullanım', device.pending_usage_batches
        ? `${num(device.pending_usage_batches)} kayıt tabletten gelmeyi bekliyor` : null],
      ['Bekleyen komut', device.pending_commands ? num(device.pending_commands) : null],
      ['Cihaz', deviceLine(device) || null],
      ['Eklendi', device.created_at ? when(device.created_at) : null],
      ['Eşleme iptali', device.revoked_at ? when(device.revoked_at) : null],
    ];
    ctl.slots.status.replaceChildren(card('Durum', body(facts(rows))));
  }

  function paintSession(ctl, device) {
    const slot = ctl.slots.session;
    ctl.usageAt = 0;
    if (!device.active_session_id) {
      slot.replaceChildren();
      ctl.usageBox.replaceChildren();
      if (ctl.historyLoaded) loadHistory(ctl);
      return;
    }
    const session = app.state.sessions.find((item) => item.id === device.active_session_id);
    const who = nameCell(device.active_student_name || 'Öğrenci',
      session ? `Giriş ${clockOf(session.login_at)} · ${since(session.login_at)}` : '');
    const actions = h('div', 'tb-actions');
    actions.append(
      permButton(app, 'Oturumu kapat', 'manage', {
        variant: 'danger',
        onClick: (event) => busy(event.currentTarget, () => endSession(device)),
      }),
      button('Öğrenci sayfasını aç', {
        variant: 'ghost',
        onClick: () => {
          ctl.drawer.close();
          app.showStudent(device.active_student_id);
        },
      }),
    );
    ctl.usageBox.replaceChildren(muted('Bugünkü kullanım yükleniyor…'));
    slot.replaceChildren(card('Şu an kullanan öğrenci', body(who, ctl.usageBox, actions)));
    loadUsage(ctl);
    if (ctl.historyLoaded) loadHistory(ctl);
  }

  async function loadUsage(ctl) {
    const device = app.device(ctl.id);
    if (!device?.active_student_id) return;
    ctl.usageAt = Date.now();
    const date = todayIn(app.profile(device.profile_id)?.timezone || DEFAULT_TZ);
    try {
      const result = await app.call(`/students/${enc(device.active_student_id)}/usage?localDate=${date}`);
      if (current !== ctl) return;
      ctl.usageBox.replaceChildren(usageList(result?.usage || [], { emptyText: 'Bugün henüz kullanım yok.' }));
    } catch (error) {
      if (current !== ctl) return;
      ctl.usageBox.replaceChildren(muted(problem(error, 'Bugünkü kullanım alınamadı.')));
    }
  }

  function paintControl(ctl, device) {
    const slot = ctl.slots.control;
    if (device.status === 'revoked') {
      slot.replaceChildren();
      return;
    }
    const lines = h('div', 'tb-lines');
    lines.append(line('Kilit',
      device.locked ? `Kilitli${device.lock_message ? ` · “${device.lock_message}”` : ''}` : 'Açık',
      device.locked
        ? permButton(app, 'Kilidi aç', 'manage', {
          onClick: (event) => busy(event.currentTarget, () => patchDevice(device.id, { locked: false },
            'Tabletin kilidi açıldı.', 'Kilit açılamadı. Tekrar deneyin.')),
        })
        : permButton(app, 'Kilitle', 'manage', {
          onClick: (event) => busy(event.currentTarget, () => lockDevice(device)),
        })));
    const loginLock = loginLockMinutes(device, app.now());
    if (loginLock) {
      const locked = line('Öğrenci girişi', `Kilitli · ${loginLock} dk daha`,
        permButton(app, 'Giriş kilidini kaldır', 'grant', {
          onClick: (event) => busy(event.currentTarget, () => resetLoginLock(device)),
        }));
      locked.title = 'Bu tablette art arda hatalı şifre girildi. Süre bitince giriş kendiliğinden açılır.';
      lines.append(locked);
    }
    lines.append(line('Duyuru', device.announcement || 'Yok',
      permButton(app, device.announcement ? 'Değiştir' : 'Duyuru yaz', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => announce(device)),
      }),
      device.announcement ? permButton(app, 'Kaldır', 'manage', {
        variant: 'ghost',
        onClick: (event) => busy(event.currentTarget, () => patchDevice(device.id, { announcement: '' },
          'Duyuru kaldırıldı.', 'Duyuru kaldırılamadı. Tekrar deneyin.')),
      }) : null));

    const select = h('select', 'kit-select');
    select.setAttribute('aria-label', 'Çevrimdışı kullanım');
    for (const [value, label] of OFFLINE_MODES) {
      const option = h('option', null, label);
      option.value = value;
      select.append(option);
    }
    select.value = device.offline_mode || 'local';
    if (!app.can('manage')) {
      select.disabled = true;
      select.title = REASONS.manage;
    }
    select.addEventListener('change', async () => {
      select.disabled = true;
      const saved = await patchDevice(device.id, { offlineMode: select.value },
        'Çevrimdışı kullanım ayarı kaydedildi.', 'Ayar kaydedilemedi. Tekrar deneyin.');
      select.disabled = false;
      if (!saved) select.value = device.offline_mode || 'local';
    });
    const offline = line('Çevrimdışı kullanım', select);
    offline.title = 'İnternet kesildiğinde açık oturumun sürüp sürmeyeceği. “Tablet ayarına bırak” '
      + 'seçilirse tablet yöneticisinin tabletteki ayarı geçerlidir.';
    lines.append(offline);
    slot.replaceChildren(card('Uzaktan yönetim', body(lines)));
  }

  function paintProfile(ctl, device) {
    const slot = ctl.slots.profile;
    if (device.status === 'revoked') {
      slot.replaceChildren();
      return;
    }
    const profile = app.profile(device.profile_id);
    const select = h('select', 'kit-select');
    select.setAttribute('aria-label', 'Atanacak profil');
    if (!profile) {
      const none = h('option', null, 'Profil seçin');
      none.value = '';
      select.append(none);
    }
    for (const item of app.state.profiles) {
      const option = h('option', null, item.name);
      option.value = item.id;
      select.append(option);
    }
    select.value = device.profile_id || '';
    const assign = permButton(app, 'Profili ata', 'manage', {
      onClick: (event) => busy(event.currentTarget, () => assignProfile(ctl, select.value)),
    });
    if (app.can('manage')) {
      assign.disabled = true;
      select.addEventListener('change', () => {
        ctl.profileTouched = select.value !== (device.profile_id || '');
        assign.disabled = !ctl.profileTouched || !select.value;
      });
    } else {
      select.disabled = true;
      select.title = REASONS.manage;
    }
    const picker = h('div', 'tb-actions');
    picker.append(select, assign);
    if (profile) {
      picker.append(button('Profili düzenle', {
        variant: 'ghost',
        onClick: () => {
          ctl.drawer.close();
          app.openProfile(profile.id);
        },
      }));
    }
    const rows = facts([
      ['Şu anki profil', profile ? inline(profile.name, syncBadge(device))
        : 'Atanmamış — öğrenci bu tablete giriş yapamaz.'],
      ['Kullanım saatleri', profile ? windowsSummary(profile.usageWindows) : null],
    ]);
    const parts = [rows, picker];
    if (!profile && device.paired && app.can('manage')) {
      parts.push(button('Bu tabletin uygulamalarından profil oluştur', {
        onClick: (event) => busy(event.currentTarget, () => profileFromTablet(device)),
      }));
    }
    slot.replaceChildren(card('Profil', body(...parts)));
  }

  /**
   * Tablet uygulaması: sürüm, kendini güncelleme durumu (v2.2), "Şimdi
   * güncelle", "Tanılama iste" ve tanı raporları. Eski tablet kendini
   * güncelleme durumunu bildirmez; onun güncellemesi Uygulamalar sekmesindeki
   * dağıtım sırasından izlenir.
   */
  function paintSoftware(ctl, device) {
    const slot = ctl.slots.software;
    if (!device.paired || device.status === 'revoked') {
      slot.replaceChildren();
      return;
    }
    const own = app.state.selfRelease;
    const report = device.self_update;
    const state = selfUpdateState(report, own?.totalBytes || 0);
    let stateNode = null;
    if (state) {
      stateNode = badge(state[0], state[1]);
      if (state[2]) stateNode.title = state[2];
    }
    const parts = [facts([
      ['Uygulama sürümü', appVersionText(device)],
      ['Yayındaki sürüm', own ? `${own.versionName ? `${own.versionName} ` : ''}(${num(own.versionCode)})` : null],
      ['Güncelleme', stateNode],
    ])];
    if (!report) {
      parts.push(muted('Tabletteki uygulama sürümü güncelleme durumunu bildirmiyor ve tanı raporu '
        + 'gönderemez. Yeni sürümü Uygulamalar sekmesinden dağıtınca tablet onu sırayla kurar.'));
      slot.replaceChildren(card('Tablet uygulaması', body(...parts)));
      return;
    }
    const actions = h('div', 'tb-actions');
    actions.append(
      permButton(app, 'Şimdi güncelle', 'manage', {
        title: 'Bekleme süresini atla; gerekirse açık oturumu kapatıp hemen kur',
        onClick: (event) => busy(event.currentTarget, () => selfUpdateNow(ctl)),
      }),
      permButton(app, 'Tanılama iste', 'manage', {
        title: 'Tablet tanı raporunu toplayıp sunucuya gönderir',
        onClick: (event) => busy(event.currentTarget, () => sendCommand(ctl, 'diagnostics')),
      }),
    );
    const head = h('div', 'tb-actions');
    head.append(h('b', null, 'Tanılama raporları'), h('span', 'kit-spacer'), button('Yenile', {
      variant: 'ghost',
      onClick: (event) => busy(event.currentTarget, () => loadDiagnostics(ctl)),
    }));
    parts.push(actions, head, ctl.diagList);
    slot.replaceChildren(card('Tablet uygulaması', body(...parts)));
    if (ctl.diagnostics === null) {
      ctl.diagnostics = [];
      ctl.diagList.replaceChildren(muted('Yükleniyor…'));
      loadDiagnostics(ctl);
    }
  }

  function paintDiagnostics(ctl) {
    const list = ctl.diagList;
    list.replaceChildren();
    if (!ctl.diagnostics.length) {
      list.append(muted('Henüz tanı raporu yok. “Tanılama iste” ile isteyebilirsiniz.'));
      return;
    }
    for (const item of ctl.diagnostics) {
      const row = h('div', 'tb-cmd');
      const version = [item.appVersion, item.appVersionCode !== null && item.appVersionCode !== undefined
        ? `(${item.appVersionCode})` : ''].filter(Boolean).join(' ');
      row.append(nameCell(`${when(item.createdAt)} · ${since(item.createdAt)}`,
        [version ? `Sürüm ${version}` : '', bytes(item.sizeBytes)].filter(Boolean).join(' · ')));
      const tools = h('div', 'tb-cmd-tools');
      tools.append(permButton(app, 'Görüntüle', 'manage', {
        variant: 'ghost',
        onClick: (event) => busy(event.currentTarget, () => showDiagnostics(ctl, item)),
      }));
      row.append(tools);
      list.append(row);
    }
  }

  async function loadDiagnostics(ctl) {
    try {
      const rows = await app.call(`/devices/${enc(ctl.id)}/diagnostics`);
      if (current !== ctl) return;
      ctl.diagnostics = Array.isArray(rows) ? rows : [];
      paintDiagnostics(ctl);
    } catch (error) {
      if (current !== ctl) return;
      ctl.diagList.replaceChildren(muted(problem(error, 'Tanı raporları alınamadı.')));
    }
  }

  async function showDiagnostics(ctl, item) {
    let report;
    try {
      report = await app.call(`/devices/${enc(ctl.id)}/diagnostics/${enc(item.id)}`);
    } catch (error) {
      app.toast(error?.status === 404 ? 'Bu rapor artık yok; daha yenileri geldiği için silinmiş olabilir.'
        : problem(error, 'Tanı raporu açılamadı. Tekrar deneyin.'), 'bad');
      if (error?.status === 404) loadDiagnostics(ctl);
      return;
    }
    const text = String(report?.text || '');
    const { card: box, close } = modal(app.root, {
      title: `Tanı raporu · ${when(item.createdAt)}`, className: 'tb-diag-dialog', closeOnOverlay: true,
    });
    const pre = h('pre', 'tb-diag-text', text || '(boş rapor)');
    pre.tabIndex = 0;
    const actions = h('div', 'kit-dialog-actions');
    actions.append(
      button('Kopyala', {
        onClick: async () => {
          const copied = await copyText(text);
          app.toast(copied ? 'Rapor panoya kopyalandı.' : 'Kopyalanamadı; metni seçip elle kopyalayın.',
            copied ? 'good' : 'warn');
        },
      }),
      button('Kapat', { variant: 'primary', onClick: () => close(null) }),
    );
    box.append(h('p', 'kit-dialog-text', [item.appVersion ? `Sürüm ${item.appVersion}` : '',
      bytes(item.sizeBytes)].filter(Boolean).join(' · ')), pre, actions);
    actions.lastChild?.focus();
  }

  async function selfUpdateNow(ctl) {
    const yes = await confirmSimple(app.root, {
      title: 'Tablet uygulaması şimdi güncellensin mi?',
      description: SELF_UPDATE_WARNING,
      confirmLabel: 'Şimdi güncelle',
      danger: true,
    });
    if (yes) await sendCommand(ctl, 'self_update');
  }

  function paintCommands(ctl, device) {
    const slot = ctl.slots.commands;
    if (!device.paired || device.status === 'revoked') {
      slot.replaceChildren();
      return;
    }
    const actions = h('div', 'tb-actions');
    actions.append(
      permButton(app, 'Mesaj gönder', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => sendMessage(ctl)),
      }),
      permButton(app, 'Uygulama listesini yenile', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => sendCommand(ctl, 'refresh_inventory')),
      }),
      permButton(app, 'Kullanımı şimdi gönder', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => sendCommand(ctl, 'flush_usage')),
      }),
      permButton(app, 'Yeniden başlat', 'manage', {
        variant: 'danger',
        onClick: (event) => busy(event.currentTarget, () => reboot(ctl)),
      }),
    );
    slot.replaceChildren(card('Komutlar', body(actions, ctl.commandList)));
    if (!ctl.commandsLoaded) {
      ctl.commandList.replaceChildren(muted('Yükleniyor…'));
      loadCommands(ctl);
    }
  }

  function paintCommandList(ctl) {
    const list = ctl.commandList;
    list.replaceChildren();
    if (!ctl.commands.length) {
      list.append(muted('Henüz komut gönderilmedi.'));
      return;
    }
    for (const command of ctl.commands) {
      const [text, tone] = COMMAND_STATUS[command.status] || ['Bilinmiyor', 'dim'];
      const message = command.type === 'message' ? String(command.payload?.text || '') : '';
      const title = message
        ? `Mesaj: “${message.length > 60 ? `${message.slice(0, 59)}…` : message}”`
        : (COMMAND_LABELS[command.type] || 'Komut');
      const info = nameCell(title, [since(command.createdAt), command.createdBy].filter(Boolean).join(' · '));
      const result = commandResult(command);
      if (result) info.append(h('span', 'tb-sub tb-bad', result));
      const tools = h('div', 'tb-cmd-tools');
      if (command.status === 'pending' || command.status === 'delivered') {
        tools.append(permButton(app, 'İptal et', 'manage', {
          variant: 'ghost',
          title: command.status === 'pending'
            ? 'Komut tablete hiç gitmez.'
            : 'Komut bir daha gönderilmez. Tablet çalıştırdıysa sonucu yine görünür.',
          onClick: (event) => busy(event.currentTarget, () => cancelCommand(ctl, command)),
        }));
      }
      tools.append(badge(text, tone));
      const row = h('div', 'tb-cmd');
      row.append(info, tools);
      list.append(row);
    }
  }

  async function loadCommands(ctl) {
    ctl.commandsLoaded = true;
    try {
      const rows = await app.call(`/devices/${enc(ctl.id)}/commands?limit=10`);
      if (current !== ctl) return;
      ctl.commands = Array.isArray(rows) ? rows : [];
      paintCommandList(ctl);
      // Tanılama komutu tamamlanınca rapor gelmiştir: liste yeniden okunur.
      const done = ctl.commands.filter((item) => item.type === 'diagnostics' && item.status === 'done')
        .map((item) => item.id).sort().join();
      if (ctl.doneDiagnostics !== null && done !== ctl.doneDiagnostics && ctl.diagnostics !== null) {
        loadDiagnostics(ctl);
      }
      ctl.doneDiagnostics = done;
    } catch (error) {
      if (current !== ctl) return;
      ctl.commandList.replaceChildren(muted(problem(error, 'Komut listesi alınamadı.')));
    }
  }

  function buildAppsTools(ctl) {
    const tools = h('div', 'tb-apps-tools');
    const search = h('input', 'kit-input');
    search.type = 'search';
    search.placeholder = 'Uygulama ara';
    search.setAttribute('aria-label', 'Yüklü uygulamalarda ara');
    search.addEventListener('input', () => {
      ctl.appsFilter.q = search.value;
      paintAppsList(ctl);
    });
    const toggle = h('label', 'kit-field-row');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.addEventListener('change', () => {
      ctl.appsFilter.all = check.checked;
      paintAppsList(ctl);
    });
    toggle.append(check, h('span', 'tb-sub', 'Arka plan uygulamalarını da göster'));
    tools.append(search, toggle, h('span', 'kit-spacer'), ctl.appsCount);
    ctl.appsTools = tools;
  }

  function paintApps(ctl, device) {
    const slot = ctl.slots.apps;
    if (!device.paired || device.status === 'revoked') {
      slot.replaceChildren();
      return;
    }
    slot.replaceChildren(card('Yüklü uygulamalar', body(ctl.appsTools, ctl.appsList)));
    if (ctl.apps === null) {
      ctl.appsList.replaceChildren(muted('Yükleniyor…'));
      loadApps(ctl);
    } else {
      paintAppsList(ctl);
    }
  }

  async function loadApps(ctl) {
    try {
      const rows = await app.inventory(ctl.id);
      if (current !== ctl) return;
      ctl.apps = rows;
      paintAppsList(ctl);
    } catch (error) {
      if (current !== ctl) return;
      ctl.appsList.replaceChildren(muted(problem(error, 'Uygulama listesi alınamadı.')));
    }
  }

  function ruleBadge(rule) {
    if (!rule) return badge('Profilde yok', 'dim');
    if (!rule.allowed) return badge('Yasak', 'bad');
    if (rule.unlimited) return badge('Sınırsız', 'good');
    return badge(`Günde ${dur(rule.dailyLimitSeconds)}`, 'info');
  }

  function paintAppsList(ctl) {
    const list = ctl.appsList;
    const device = app.device(ctl.id);
    const rows = ctl.apps || [];
    if (!rows.length) {
      ctl.appsCount.textContent = '';
      list.replaceChildren(muted('Tablet henüz uygulama listesini göndermedi. '
        + '“Uygulama listesini yenile” komutuyla isteyebilirsiniz.'));
      return;
    }
    const rules = new Map((app.profile(device?.profile_id)?.apps || [])
      .map((rule) => [rule.packageName, rule]));
    const shown = rows
      .filter((item) => ctl.appsFilter.all || item.isLaunchable)
      .filter((item) => matchesText(ctl.appsFilter.q, item.appName, item.packageName));
    ctl.appsCount.textContent = `${num(shown.length)} uygulama`;
    list.replaceChildren();
    if (!shown.length) {
      list.append(muted('Aramaya uyan uygulama yok.'));
      return;
    }
    for (const item of shown.slice(0, 300)) {
      const row = h('div', 'tb-app');
      row.append(nameCell(item.appName || item.packageName, item.packageName),
        inline(item.isSystemApp ? badge('Sistem', 'dim') : null,
          device?.profile_id ? ruleBadge(rules.get(item.packageName)) : null));
      list.append(row);
    }
    if (shown.length > 300) list.append(muted(`${num(shown.length - 300)} uygulama daha var — aramayı daraltın.`));
  }

  async function loadHistory(ctl) {
    ctl.historyLoaded = true;
    try {
      const rows = await app.call(`/sessions?limit=15&deviceId=${enc(ctl.id)}`);
      if (current !== ctl) return;
      const history = dataTable({
        columns: [
          { key: 'studentName', label: 'Öğrenci', width: 'minmax(0, 1.4fr)' },
          { key: 'loginAt', label: 'Giriş', width: '130px', cell: (row) => when(row.loginAt) },
          { key: 'endedAt', label: 'Bitiş', width: '130px',
            cell: (row) => (row.state === 'active' ? badge('Açık', 'good') : when(row.endedAt)) },
          { key: 'endReason', label: 'Neden', width: 'minmax(0, 1.2fr)',
            cell: (row) => (row.state === 'active' ? '—' : END_REASONS[row.endReason] || '—') },
        ],
        rows: Array.isArray(rows) ? rows : [],
        dense: true,
        empty: emptyState({ title: 'Bu tablette henüz oturum açılmadı' }),
      });
      ctl.historyBox.replaceChildren(history.node);
    } catch (error) {
      if (current !== ctl) return;
      ctl.historyBox.replaceChildren(muted(problem(error, 'Oturum geçmişi alınamadı.')));
    }
  }

  /**
   * Kayıt: ad, eşleme ve silme. "Eşlemeyi iptal et" kaydı tutar (yeni kodla
   * yeniden bağlanır); "Tableti sil" kaydı tamamen kaldırır. İkisi
   * karışmasın diye silme ayrı satırda, en altta ve farkı yazılı durur.
   */
  function paintRecord(ctl, device) {
    const unpaired = device.status === 'pending' || device.status === 'revoked';
    const actions = h('div', 'tb-actions');
    actions.append(permButton(app, 'Yeniden adlandır', 'manage', {
      onClick: (event) => busy(event.currentTarget, () => rename(device)),
    }));
    if (unpaired) {
      actions.append(permButton(app, 'Yeni eşleme kodu', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => newCode(device)),
      }));
    } else {
      actions.append(permButton(app, 'Eşlemeyi iptal et', 'manage', {
        onClick: (event) => busy(event.currentTarget, () => revoke(device)),
      }));
    }
    const danger = h('div', 'tb-danger');
    danger.append(
      muted(unpaired
        ? 'Tableti silmek kaydı tamamen kaldırır; kullanım geçmişi kalır.'
        : 'Eşlemeyi iptal edince kayıt kalır, tablet yeni kodla yeniden bağlanabilir. '
          + 'Tableti silmek kaydı tamamen kaldırır; kullanım geçmişi kalır.'),
      permButton(app, 'Tableti sil', 'manage', {
        variant: 'danger',
        onClick: (event) => busy(event.currentTarget, () => removeDevice(ctl, device)),
      }),
    );
    ctl.slots.record.replaceChildren(card('Kayıt', body(actions, danger)));
  }

  // ------------------------------------------------------ tekil eylemler
  async function patchDevice(id, changes, done, fallback) {
    try {
      const result = await app.call(`/devices/${enc(id)}`, { method: 'PATCH', body: changes });
      app.toast(typeof done === 'function' ? done(result) : done, 'good');
      await app.refresh();
      return result;
    } catch (error) {
      app.toast(problem(error, fallback), 'bad');
      return null;
    }
  }

  async function lockDevice(device) {
    const answer = await formDialog(app.root, {
      title: `“${device.name}” kilitlensin mi?`,
      description: 'Açık oturum kapanır; tablette kilit ekranı görünür.',
      fields: [lockField(device.lock_message || '')],
      confirmLabel: 'Kilitle',
    });
    if (!answer) return;
    await patchDevice(device.id, { locked: true, lockMessage: answer.message },
      (result) => (result?.endedSessions ? 'Tablet kilitlendi; açık oturum kapatıldı.' : 'Tablet kilitlendi.'),
      'Tablet kilitlenemedi. Tekrar deneyin.');
  }

  async function announce(device) {
    const answer = await formDialog(app.root, {
      title: 'Duyuru',
      description: 'Duyuru, kaldırılana kadar tablet ekranında görünür.',
      fields: [announcementField(device.announcement || '')],
      confirmLabel: 'Yayınla',
    });
    if (!answer) return;
    await patchDevice(device.id, { announcement: answer.text }, 'Duyuru yayınlandı.',
      'Duyuru yayınlanamadı. Tekrar deneyin.');
  }

  async function rename(device) {
    const answer = await formDialog(app.root, {
      title: 'Tableti yeniden adlandır',
      fields: [{ key: 'name', label: 'Tablet adı', required: true, minLength: 2, maxLength: 100,
        value: device.name }],
      confirmLabel: 'Kaydet',
    });
    if (!answer || answer.name === device.name) return;
    await patchDevice(device.id, { name: answer.name }, 'Tablet adı değişti.',
      'Ad değiştirilemedi. Tekrar deneyin.');
  }

  async function assignProfile(ctl, profileId) {
    if (!profileId) return;
    try {
      await app.call(`/devices/${enc(ctl.id)}/assign-profile`, { method: 'POST', body: { profileId } });
      ctl.profileTouched = false;
      app.toast('Profil atandı. Tablet bir sonraki bağlantıda alır.', 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Profil atanamadı. Tekrar deneyin.'), 'bad');
    }
  }

  /**
   * Profili olmayan tablet için başlangıç profili: tabletteki açılabilir
   * uygulamalar eklenir; yüklenen uygulamalar günde 1 saat, sistem
   * uygulamaları (Ayarlar, Kamera…) yasak başlar. Yönetici sonra düzenler.
   */
  async function profileFromTablet(device) {
    let rows;
    try {
      rows = await app.inventory(device.id);
    } catch (error) {
      app.toast(problem(error, 'Uygulama listesi alınamadı.'), 'bad');
      return;
    }
    const launchable = rows.filter((item) => item.isLaunchable && item.packageName !== OWN_PACKAGE);
    if (!launchable.length) {
      app.toast('Tablet henüz uygulama listesini göndermedi. “Uygulama listesini yenile” komutunu deneyin.', 'warn');
      return;
    }
    const answer = await formDialog(app.root, {
      title: 'Tabletin uygulamalarından profil',
      description: `${num(launchable.length)} uygulama eklenir: yüklenen uygulamalar günde 1 saat, `
        + 'sistem uygulamaları yasak. Sonra Profiller sekmesinden düzenleyebilirsiniz.',
      fields: [{ key: 'name', label: 'Profil adı', required: true, minLength: 2, maxLength: 100,
        value: `${device.name} profili`.slice(0, 100) }],
      confirmLabel: 'Oluştur ve ata',
    });
    if (!answer) return;
    try {
      const profile = await app.call('/profiles', {
        method: 'POST',
        body: {
          name: answer.name,
          timezone: DEFAULT_TZ,
          usageWindows: [],
          apps: launchable.slice(0, 1000).map((item) => ({
            packageName: item.packageName,
            appName: String(item.appName || item.packageName).slice(0, 120),
            allowed: !item.isSystemApp,
            unlimited: false,
            dailyLimitSeconds: item.isSystemApp ? 0 : 3600,
          })),
        },
      });
      await app.call(`/devices/${enc(device.id)}/assign-profile`, {
        method: 'POST', body: { profileId: profile.id },
      });
      app.toast(`“${profile.name}” oluşturuldu ve tablete atandı.`, 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Profil oluşturulamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function endSession(device) {
    const answer = await formDialog(app.root, {
      title: 'Oturum kapatılsın mı?',
      description: `${device.active_student_name || 'Öğrenci'} tabletten çıkarılır; o ana kadarki kullanımı kaydedilir.`,
      fields: [{ key: 'message', label: 'Öğrenciye not (isteğe bağlı)', maxLength: 200,
        placeholder: 'Örn. Ders bitti.' }],
      confirmLabel: 'Oturumu kapat',
      danger: true,
    });
    if (!answer) return;
    try {
      const result = await app.call(`/sessions/${enc(device.active_session_id)}/end`, {
        method: 'POST', body: { message: answer.message },
      });
      app.toast(result?.alreadyEnded ? 'Oturum zaten kapanmıştı.' : 'Oturum kapatıldı.', 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Oturum kapatılamadı. Tekrar deneyin.'), 'bad');
    }
  }

  const COMMAND_DONE = {
    message: 'Mesaj gönderildi. Tablet bağlanınca gösterir.',
    refresh_inventory: 'İstek gönderildi. Uygulama listesi tablet bağlanınca yenilenir.',
    flush_usage: 'İstek gönderildi. Kullanım tablet bağlanınca gelir.',
    reboot: 'Yeniden başlatma isteği gönderildi.',
    self_update: 'Güncelleme komutu gönderildi. Tablet bağlanınca güncellemeyi hemen kurar.',
    diagnostics: 'İstek gönderildi. Tanı raporu tablet bağlanınca aşağıdaki listeye gelir.',
  };

  async function sendCommand(ctl, type, payload = {}) {
    try {
      await app.call(`/devices/${enc(ctl.id)}/commands`, { method: 'POST', body: { type, payload } });
      if (type === 'refresh_inventory') {
        app.forgetInventory(ctl.id);
        ctl.apps = null;
      }
      app.toast(COMMAND_DONE[type], 'good');
      await loadCommands(ctl);
    } catch (error) {
      app.toast(problem(error, 'Komut gönderilemedi. Tekrar deneyin.'), 'bad');
    }
  }

  async function sendMessage(ctl) {
    const answer = await formDialog(app.root, {
      title: 'Tablete mesaj',
      description: 'Mesaj tablet ekranında bir kez görünür; öğrenci kapatabilir.',
      fields: [messageField()],
      confirmLabel: 'Gönder',
    });
    if (answer) await sendCommand(ctl, 'message', { text: answer.text });
  }

  async function reboot(ctl) {
    const yes = await confirmSimple(app.root, {
      title: 'Tablet yeniden başlatılsın mı?',
      description: 'Açık oturum kapanır, kullanım gönderilir ve tablet yeniden başlar.',
      confirmLabel: 'Yeniden başlat',
      danger: true,
    });
    if (yes) await sendCommand(ctl, 'reboot');
  }

  async function newCode(device) {
    const offerProfile = !device.profile_id && app.state.profiles.length > 0;
    const answer = await formDialog(app.root, {
      title: 'Yeni eşleme kodu',
      description: device.status === 'revoked'
        ? 'Tablet yeniden bağlanabilsin diye yeni kod oluşturulur.'
        : 'Yeni kod oluşunca önceki kod geçersiz olur.',
      fields: offerProfile ? [{
        key: 'profileId', label: 'Profil', type: 'select', value: '',
        options: [{ value: '', label: 'Sonra seçerim' },
          ...app.state.profiles.map((profile) => ({ value: profile.id, label: profile.name }))],
      }] : [],
      confirmLabel: 'Kodu oluştur',
    });
    if (!answer) return;
    try {
      const result = await app.call(`/devices/${enc(device.id)}/enrollment-code`, {
        method: 'POST', body: answer.profileId ? { profileId: answer.profileId } : {},
      });
      await app.refresh();
      showCode(result);
    } catch (error) {
      app.toast(problem(error, 'Kod oluşturulamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function revoke(device) {
    const pin = await confirmWithPin(app.root, {
      title: `“${device.name}” eşlemesi iptal edilsin mi?`,
      description: 'Tablet Kontrol Merkezi’nden ayrılır, açık oturum kapanır ve gönderilmemiş '
        + 'komutlar silinir. Kayıt listede kalır; yeniden bağlamak için yeni eşleme kodu verilir. '
        + 'Onaylamak için PIN’inizi girin.',
      confirmLabel: 'Eşlemeyi iptal et',
    });
    if (!pin) return;
    try {
      const result = await app.call(`/devices/${enc(device.id)}/revoke`, { method: 'POST', body: { pin } });
      app.forgetInventory(device.id);
      app.toast(result?.alreadyRevoked ? 'Eşleme zaten iptal edilmişti.' : 'Eşleme iptal edildi.', 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Eşleme iptal edilemedi. Tekrar deneyin.'), 'bad');
    }
  }

  async function removeDevice(ctl, device) {
    const paired = device.paired && device.status !== 'revoked';
    const pin = await confirmWithPin(app.root, {
      title: `“${device.name}” silinsin mi?`,
      description: `${paired
        ? 'Tablet Kontrol Merkezi’nden çıkarılır, açık oturum kapanır; kullanım geçmişi kalır.'
        : 'Tablet kaydı listeden kalkar; kullanım geçmişi kalır.'} `
        + 'Tableti yeniden kullanmak için baştan eklemek gerekir. Onaylamak için PIN’inizi girin.',
      confirmLabel: 'Tableti sil',
    });
    if (!pin) return;
    try {
      await app.call(`/devices/${enc(device.id)}`, { method: 'DELETE', body: { pin } });
      app.forgetInventory(device.id);
      ctl.drawer.close();
      app.toast('Tablet silindi.', 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Tablet silinemedi. Tekrar deneyin.'), 'bad');
    }
  }

  async function resetLoginLock(device) {
    try {
      const result = await app.call(`/devices/${enc(device.id)}/login-lock/reset`, { method: 'POST' });
      app.toast(result?.cleared ? 'Giriş kilidi kaldırıldı; tablet kısa süre içinde açılır.'
        : 'Giriş kilidi zaten kalkmıştı.', 'good');
      await app.refresh();
    } catch (error) {
      app.toast(problem(error, 'Giriş kilidi kaldırılamadı. Tekrar deneyin.'), 'bad');
    }
  }

  async function cancelCommand(ctl, command) {
    try {
      const result = await app.call(`/devices/${enc(ctl.id)}/commands/${enc(command.id)}`,
        { method: 'DELETE' });
      app.toast(result?.alreadyCancelled ? 'Komut zaten iptal edilmişti.' : 'Komut iptal edildi.', 'good');
    } catch (error) {
      app.toast(problem(error, 'Komut iptal edilemedi. Tekrar deneyin.'), 'bad');
    }
    await loadCommands(ctl);
    await app.refresh();
  }

  // ------------------------------------------------------------- dış yüz
  buildTable();

  return {
    node,
    open,
    update() {
      paintTable();
      paintDrawer();
      if (codeView) {
        const device = app.device(codeView.id);
        if (device?.paired && !codeView.paired) {
          codeView.paired = true;
          codeView.tick();
        }
      }
    },
    destroy() {
      bar.destroy();
      current?.drawer.close();
    },
  };
}
