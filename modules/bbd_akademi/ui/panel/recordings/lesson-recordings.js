// BBD Akademi — Ders kayıtları (F-DK, akıllı tahta): tahtalar ve derslikler, kayıtlar, depolama, ayarlar.
//
// KULLANICI (2026-10-16): "akıllı tahtamız var derslerin işlendiği … kayıt bitince otomatik o derse programda seçmişti
// ya atanmış öğrenciler erişebilecek". Kural: her yönetici özelliği KM'den de yönetilebilir; KM en yetkili merkezdir.
// Bu ekran web / mobil yönetici ekranının (tasarım `DkYonetimTahtalar|Kayitlar|Depolama|Ayarlar`) KM karşılığıdır;
// uçlar AYNIDIR (`x-surface: both`, Akademi README §14 madde 110, `docs/integrations/ders-kaydi.md`):
//
//   Tahtalar ve derslikler  admin_listBoards · createBoard (→ tek seferlik eşleme kodu) · updateBoard · deleteBoard ·
//                           createBoardPairingCode (yeniden eşle) · cancelBoardPairingCode · revokeBoard ·
//                           getBoardDiagnostics · requestBoardDiagnostics · listRooms · createRoom · updateRoom · deleteRoom
//   Kayıtlar                admin_listRecordings (süzgeç, arama: ad / ders / konu / öğretmen) · updateRecording (gizle /
//                           göster, "Adını değiştir", "Ders ve konu" — README §14 madde 127) · getRecordingCurriculum ·
//                           deleteRecording ·
//                           listRecordingViews (kim izledi) · list/add/removeRecordingShare (ek öğrenci / grup)
//   Depolama                admin_getRecordingQuota (800 GB çubuğu, %80 / %90 işaretli, tahmini dolma günü)
//   Ayarlar                 admin_get/putRecordingSettings (saklama ≤ 120 gün, tavan ≤ 800 GB, kalite, kamera izni)
//
// Bayrak `lesson_recordings` kapalıyken ekran menüde yoktur (`sections.js` → `feature`). KM izni
// `bbd_akademi.recordings`; ayarları YAZMAK kurum ayarıdır (`bbd_akademi.settings`, `permission_map.py`).
// KM panel anahtarı bu izinler eklenmeden üretildiyse Akademi `403 forbidden` döner: ekran ne yapılacağını yazar.
// Video baytı KM'den geçmez; KM yalnız meta veriyi yönetir (oynatıcı web / mobil uygulamadadır).

import { ago, button, copyText, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow, emptyState, kpiRow, tabBar } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import {
  actionButton, confirmAction, dateInput, empty, fmtDate, fmtDateTime, fmtTime, formDialog, kv, loader, multiChips,
  openDrawer, pagedList, personName, runAction, searchBox, sectionHead, selectBox, studentSelect, toolbar,
} from '../common.js';
import {
  DIAG_TRIGGER, FLAGS, KEY_HINT, QUALITY, QUOTA_MAX_GB, QUOTA_MIN_GB, REC_FILTERS, REC_SORTS,
  RETENTION_MAX, TABS, TAB_READS, boardActions, boardBody, boardState, diagnosticsRows, durationText, etaText,
  gbText, hideBody, keyLacksPermission, liveText, micEventsText, micText, pairingBreaksLink, quotaProblem,
  quotaView, rangeProblem, recordingExtra, recordingQuery, recordingState, recordingTitle, retentionProblem, roomBody,
  roomSub, settingsBody, settingsDraft, settingsWarning, shareBody, shareIntro, shareLabel, sizeText, viewState,
  viewsText, renameBody, curriculumBody, curriculumOptions, scheduleLine, topicNames, TITLE_MAX,
} from './lesson-recordings-logic.js';

/** "9 Aralık 2026" (ISO gün). */
function longDay(iso) {
  if (!iso) return null;
  const date = new Date(`${String(iso).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric' });
}

function stack(...parts) {
  const cell = h('div', 'ak-cell-stack');
  for (const part of parts.filter((item) => item !== null && item !== undefined && item !== '')) {
    cell.append(part instanceof Node ? part : h('span', '', String(part)));
  }
  return cell;
}

function small(text) {
  return h('small', 'ak-muted', text);
}

export function section(app) {
  loadStyles(import.meta.url, 'lesson-recordings.css');
  const node = h('div', 'ak-stack ak-rec');
  const live = h('div', 'ak-rec-live');
  live.hidden = true;
  node.append(sectionHead('Ders kayıtları', {
    subtitle: 'Akıllı tahtalar, kayıtlar, paylaşım, depolama ve saklama ayarları',
  }), live);
  const notice = h('div');
  node.append(notice);

  const tabs = TABS.filter((tab) => app.can(TAB_READS[tab.key]));
  if (!tabs.length) {
    node.append(emptyState({ title: 'Ders kayıtları için yetkiniz yok',
      text: 'Bu ekran ders kaydı yetkisi ister (yöneticinizden izin isteyin).' }));
    return { node };
  }

  /** Okuma hatası Akademi'nin 403'üyse (anahtar izni eksik) üstte ne yapılacağını yaz. */
  const ctx = {
    guard: (promise) => promise.catch((failure) => {
      if (keyLacksPermission(failure)) notice.replaceChildren(alertBox(KEY_HINT, 'warn'));
      throw failure;
    }),
    clearNotice: () => notice.replaceChildren(),
    setLive: (devices) => {
      const text = liveText(devices);
      live.hidden = !text;
      live.replaceChildren();
      if (text) live.append(h('span', 'ak-rec-dot'), h('span', '', text));
    },
  };

  const panes = new Map();
  const body = h('div', 'ak-rec-body');
  const factories = { boards: boardsPane, recordings: recordingsPane, storage: storagePane, settings: settingsPane };
  const open = (key, payload = null) => {
    if (!panes.has(key)) {
      const view = factories[key](app, ctx);
      panes.set(key, view);
      body.append(view.node);
    }
    for (const [name, view] of panes) view.node.hidden = name !== key;
    panes.get(key).shown?.(payload);
  };
  const bar = tabBar(tabs, tabs[0].key, (key) => open(key));
  node.append(bar.node, body);

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.recTab && tabs.some((tab) => tab.key === payload.recTab) ? payload.recTab
        : (payload?.recordingId && tabs.some((tab) => tab.key === 'recordings') ? 'recordings' : null);
      if (wanted) { started = true; bar.select(wanted, false); open(wanted, payload); return; }
      if (!started) { started = true; open(tabs[0].key); }
    },
    destroy() { for (const view of panes.values()) view.destroy?.(); },
  };
}

// ============================================================ tahtalar ve derslikler

function boardsPane(app, ctx) {
  const node = h('div', 'ak-stack');
  let rooms = [];
  let devices = [];
  const view = loader(async () => {
    const [boardList, roomList] = await ctx.guard(Promise.all([
      app.api.get('/v1/admin/boards'),
      app.can('admin_listRooms') ? app.api.get('/v1/admin/rooms', { includeInactive: true }) : { items: [] },
    ]));
    ctx.clearNotice();
    return { boards: boardList?.items || [], rooms: roomList?.items || [] };
  }, (data) => {
    devices = data.boards;
    rooms = data.rooms;
    ctx.setLive(devices);
    const cards = h('div', 'ak-stack');
    cards.append(boardsCard());
    if (app.can('admin_listRooms')) cards.append(roomsCard());
    return cards;
  }, { rows: 4, columns: 6 });
  node.append(view.node);

  const roomOptions = () => [{ value: '', label: 'Dersliksiz' },
    ...rooms.filter((room) => room.active !== false).map((room) => ({ value: room.id, label: room.name }))];

  // ---------------------------------------------------------------- tahtalar
  function boardsCard() {
    const add = actionButton(app, 'admin_createBoard', 'Tahta ekle', { variant: 'primary', onClick: () => addBoard() });
    const box = h('section', 'kit-card');
    const head = h('div', 'kit-card-head ak-rec-cardhead');
    head.append(h('h3', 'kit-card-title', 'Tahtalar'), h('span', 'kit-spacer'), add);
    box.append(head);
    box.append(h('p', 'ak-muted ak-rec-note', 'Tahta uygulaması kurulduktan sonra eşleme kodu bir kez girilir. '
      + 'Tahta her 30 saniyede bir nabız gönderir; son 2 dakikada nabız gelmediyse çevrimdışı görünür.'));
    const table = dataTable({
      rows: devices,
      columns: [
        { key: 'name', label: 'Tahta', width: 'minmax(0, 1.2fr)',
          cell: (row) => stack(h('b', '', row.name || '—'), row.osVersion ? small(row.osVersion) : null) },
        { key: 'room', label: 'Derslik', width: 'minmax(0, 0.8fr)', cell: (row) => row.room?.name || '—' },
        { key: 'status', label: 'Durum', width: '130px', cell: (row) => {
          const state = boardState(row);
          return badge(state.label, state.tone);
        } },
        { key: 'mic', label: 'Mikrofon', width: 'minmax(0, 1.3fr)', cell: (row) => {
          const mic = micText(row);
          const text = h('span', mic.warn ? 'ak-rec-warn' : '', mic.text);
          return stack(text, mic.warn && mic.hint ? small(mic.hint) : null);
        } },
        { key: 'lastSeenAt', label: 'Son görülme', width: '130px', cell: (row) => {
          if (!row.lastSeenAt) return '—';
          const text = h('span', '', ago(row.lastSeenAt));
          text.title = fmtDateTime(row.lastSeenAt);
          return text;
        } },
        { key: 'appVersion', label: 'Sürüm', width: '80px', cell: (row) => row.appVersion || '—' },
        { key: 'actions', label: 'İşlemler', width: '300px', cell: (row) => boardButtons(row) },
      ],
      empty: emptyState({ title: 'Henüz tahta yok',
        text: '"Tahta ekle" ile tahtayı ve dersliğini girin; çıkan eşleme kodunu tahta uygulamasına yazın.' }),
    });
    box.append(table.node);
    return box;
  }

  function boardButtons(device) {
    const wrap = h('div', 'ak-inline ak-rec-actions');
    const can = boardActions(device);
    wrap.append(actionButton(app, 'admin_createBoardPairingCode', can.pairLabel, { variant: 'ghost',
      onClick: () => pair(device) }));
    if (can.revoke) {
      wrap.append(actionButton(app, 'admin_revokeBoard', 'İptal et', { variant: 'ghost', onClick: () => revoke(device) }));
    }
    const more = button('Ayrıntı', { variant: 'ghost', onClick: () => boardDrawer(device) });
    more.setAttribute('aria-label', `${device.name || 'Tahta'} ayrıntısı`);
    wrap.append(more);
    return wrap;
  }

  async function addBoard() {
    const result = await formDialog(app, {
      title: 'Tahta ekle',
      description: 'Tahtayı adlandırın ve dersliğini seçin. Kaydedince tek seferlik eşleme kodu çıkar; kodu tahta '
        + 'uygulamasının "Bu tahtayı kuruma bağlayın" ekranına yazın.',
      fields: boardFields(),
      value: { name: '', roomId: '' },
      submitLabel: 'Tahta ekle',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/boards', boardBody(draft), { key }),
    });
    if (!result) return;
    view.reload();
    if (result.pairing) showCode(app, result.device?.name || 'Yeni tahta', result.pairing);
  }

  function boardFields() {
    return [
      { key: 'name', label: 'Tahta adı', type: 'text', required: true, maxLength: 60, placeholder: 'Ör. Derslik 3 tahtası' },
      { key: 'roomId', label: 'Derslik', type: 'select', options: roomOptions(),
        hint: 'Tahta bu dersliğin derslerini listeler. Dersliksiz tahtada öğretmen yalnız kendi derslerini görür.' },
    ];
  }

  async function editBoard(device, onDone) {
    const result = await formDialog(app, {
      title: `${device.name} — düzenle`,
      fields: boardFields(),
      value: { name: device.name || '', roomId: device.room?.id || '' },
      onSubmit: (draft) => app.api.patch(`/v1/admin/boards/${device.id}`, boardBody(draft)),
    });
    if (!result) return;
    app.toast('Tahta kaydedildi.', 'good');
    view.reload();
    onDone?.(result);
  }

  async function pair(device) {
    if (pairingBreaksLink(device)) {
      const ok = await confirmAction(app, {
        title: `${device.name} için yeni eşleme kodu`,
        description: 'Yeni kod eski eşlemeyi geçersiz kılar: tahtanın bağlantısı hemen kesilir ve kod tahtaya '
          + 'yeniden girilene dek kayıt alınamaz. Süren kayıt varsa yüklenmiş bölümleri kalır.',
        confirmLabel: 'Kodu üret',
        danger: true,
      });
      if (!ok) return;
    }
    const code = await runAction(app, () => app.api.post(`/v1/admin/boards/${device.id}/pairing-code`, undefined,
      { key: `board-pair-${device.id}-${Date.now()}` }));
    if (!code) return;
    view.reload();
    showCode(app, device.name, code);
  }

  async function revoke(device) {
    const ok = await confirmAction(app, {
      title: `${device.name} iptal edilsin mi?`,
      description: 'Tahtanın bağlantısı hemen kesilir, tahtadaki öğretmen oturumu kapanır ve süren kayıt durur '
        + '(yüklenmiş bölümleri kalır). Yeniden kullanmak için "Yeniden eşle" ile yeni kod üretin.',
      confirmLabel: 'İptal et',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.post(`/v1/admin/boards/${device.id}/revoke`, undefined,
      { key: `board-revoke-${device.id}` }), { success: `${device.name} iptal edildi · tahtadaki oturum kapandı` });
    if (done) view.reload();
  }

  async function cancelCode(device, onDone) {
    const ok = await confirmAction(app, {
      title: 'Eşleme kodu iptal edilsin mi?',
      description: 'Açık kod artık kullanılamaz. Tahta zaten eşliyse bağlantısı etkilenmez.',
      confirmLabel: 'Kodu iptal et',
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/boards/${device.id}/pairing-code`),
      { success: 'Eşleme kodu iptal edildi.' });
    if (done !== undefined) { view.reload(); onDone?.(); }
  }

  async function removeBoard(device, onDone) {
    const ok = await confirmAction(app, {
      title: `${device.name} silinsin mi?`,
      description: 'Tahta listeden kalkar ve bağlantısı kesilir. Bu tahtayla alınmış ders kayıtları silinmez.',
      confirmLabel: 'Tahtayı sil',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/boards/${device.id}`),
      { success: `${device.name} silindi.` });
    if (done !== undefined) { view.reload(); onDone?.(); }
  }

  function boardDrawer(device) {
    const panel = openDrawer(app, { title: device.name || 'Tahta', subtitle: 'Akıllı tahta' });
    const paint = () => {
      const state = boardState(device);
      const mic = micText(device);
      const can = boardActions(device);
      const nodes = [];
      if (mic.warn && device.status === 'active') nodes.push(alertBox(mic.hint || 'Harici mikrofonu tak', 'warn'));
      nodes.push(card('', kv([
        ['Durum', badge(state.label, state.tone)],
        ['Derslik', device.room?.name || 'Dersliksiz'],
        ['Mikrofon', mic.text],
        ['Kamera', device.cameraAvailable ? 'Takılı' : 'Yok'],
        ['Yüklenmeyi bekleyen bölüm', String(device.queuedSegments ?? 0)],
        ['Boş disk', device.freeDiskBytes === null || device.freeDiskBytes === undefined ? '—' : gbText(device.freeDiskBytes)],
        ['Son görülme', device.lastSeenAt ? `${fmtDateTime(device.lastSeenAt)} (${ago(device.lastSeenAt)})` : '—'],
        ['Uygulama sürümü', device.appVersion || '—'],
        ['Windows', device.osVersion || '—'],
        ['Eşlendi', device.pairedAt ? fmtDateTime(device.pairedAt) : '—'],
        ['Açık eşleme kodu', device.pairingExpiresAt ? `${fmtDateTime(device.pairingExpiresAt)}'e kadar geçerli` : 'Yok'],
        ['Son tanılama', device.lastDiagnosticsAt ? fmtDateTime(device.lastDiagnosticsAt) : '—'],
      ])));
      const tools = h('div', 'ak-inline');
      tools.append(
        actionButton(app, 'admin_updateBoard', 'Düzenle', { onClick: () => editBoard(device, (next) => {
          Object.assign(device, next);
          panel.setTitle(device.name);
          paint();
        }) }),
        actionButton(app, 'admin_createBoardPairingCode', can.pairLabel, { variant: 'ghost', onClick: () => pair(device) }),
      );
      if (can.cancelCode) {
        tools.append(actionButton(app, 'admin_cancelBoardPairingCode', 'Kodu iptal et', { variant: 'ghost',
          onClick: () => cancelCode(device, () => { device.pairingExpiresAt = null; paint(); }) }));
      }
      if (can.revoke) {
        tools.append(actionButton(app, 'admin_revokeBoard', 'İptal et', { variant: 'ghost', onClick: () => revoke(device) }));
      }
      tools.append(actionButton(app, 'admin_deleteBoard', 'Sil', { variant: 'danger',
        onClick: () => removeBoard(device, () => panel.close()) }));
      nodes.push(tools);
      if (app.can('admin_getBoardDiagnostics')) nodes.push(diagnosticsCard(device));
      panel.body.replaceChildren(...nodes);
    };
    paint();
    return panel;
  }

  function diagnosticsCard(device) {
    const box = h('section', 'kit-card');
    const head = h('div', 'kit-card-head ak-rec-cardhead');
    head.append(h('h3', 'kit-card-title', 'Tanılama'), h('span', 'kit-spacer'),
      actionButton(app, 'admin_requestBoardDiagnostics', 'Tanılama iste', { variant: 'ghost', onClick: async () => {
        const done = await runAction(app, () => app.api.post(`/v1/admin/boards/${device.id}/diagnostics/request`,
          undefined, { key: `board-diag-${device.id}-${Date.now()}` }),
        { success: 'İstek gönderildi · tahta bir sonraki nabızda (≤ 30 sn) raporunu yollar.' });
        if (done !== undefined) setTimeout(() => diag.reload(), 35_000);
      } }));
    box.append(head);
    const diag = loader(async () => {
      try {
        return await app.api.get(`/v1/admin/boards/${device.id}/diagnostics`);
      } catch (failure) {
        if (failure?.code === 'board_diagnostics_not_found' || failure?.status === 404) return null;
        throw failure;
      }
    }, (report) => {
      if (!report?.report) {
        return empty('Henüz tanılama raporu yok', 'Tahta ilk eşlemede, her güncellemeden sonra, kayıt '
          + 'başlatılamadığında ve "Tanılama iste" ile rapor gönderir.');
      }
      const trigger = DIAG_TRIGGER[report.report.trigger] || '';
      const parts = [small(`Alındı: ${fmtDateTime(report.receivedAt)}${trigger ? ` · ${trigger}` : ''}`)];
      if (report.report.test && !report.report.test.ok) {
        parts.push(alertBox('Deneme kaydı başarısız. Kodlayıcı, ekran ya da mikrofon sorununu aşağıdaki satırlardan '
          + 'okuyun.', 'bad'));
      }
      parts.push(kv(diagnosticsRows(report)));
      return parts;
    }, { rows: 4, columns: 2 });
    const inner = h('div', 'kit-card-body');
    inner.append(diag.node);
    box.append(inner);
    diag.reload();
    return box;
  }

  // ---------------------------------------------------------------- derslikler
  function roomsCard() {
    const box = h('section', 'kit-card');
    const head = h('div', 'kit-card-head ak-rec-cardhead');
    head.append(h('h3', 'kit-card-title', 'Derslikler'), h('span', 'kit-spacer'),
      actionButton(app, 'admin_createRoom', 'Derslik ekle', { variant: 'ghost', onClick: () => roomForm(null) }));
    box.append(head);
    box.append(h('p', 'ak-muted ak-rec-note', 'Derslik adı ders programındaki "Derslik" alanıyla aynı yazılmalı '
      + '(büyük / küçük harf fark etmez); tahta o dersliğin derslerini bu adla bulur.'));
    const table = dataTable({
      rows: rooms,
      columns: [
        { key: 'name', label: 'Derslik', width: 'minmax(0, 1.4fr)',
          cell: (row) => stack(h('b', '', row.name || '—'), small(roomSub(row, devices))) },
        { key: 'capacity', label: 'Kapasite', width: '100px', align: 'num',
          cell: (row) => (row.capacity ? String(row.capacity) : '—') },
        { key: 'active', label: 'Durum', width: '100px',
          cell: (row) => (row.active === false ? badge('Pasif', 'dim') : badge('Etkin', 'good')) },
        { key: 'actions', label: 'İşlemler', width: '200px', cell: (row) => {
          const wrap = h('div', 'ak-inline ak-rec-actions');
          const edit = actionButton(app, 'admin_updateRoom', 'Düzenle', { variant: 'ghost', onClick: () => roomForm(row) });
          edit.setAttribute('aria-label', `${row.name} dersliğini düzenle`);
          const drop = actionButton(app, 'admin_deleteRoom', 'Sil', { variant: 'ghost', onClick: () => removeRoom(row) });
          drop.setAttribute('aria-label', `${row.name} dersliğini sil`);
          wrap.append(edit, drop);
          return wrap;
        } },
      ],
      empty: emptyState({ title: 'Henüz derslik yok', text: '"Derslik ekle" ile dersliklerinizi girin.' }),
    });
    box.append(table.node);
    return box;
  }

  async function roomForm(room) {
    const result = await formDialog(app, {
      title: room ? `${room.name} — düzenle` : 'Derslik ekle',
      description: room ? 'Ad değişirse ders programındaki derslik metnini de aynı yazın (program kendiliğinden değişmez).' : '',
      fields: [
        { key: 'name', label: 'Derslik adı', type: 'text', required: true, maxLength: 40, placeholder: 'Ör. Derslik 3' },
        { key: 'capacity', label: 'Kapasite', type: 'number', hint: 'İsteğe bağlı (1–500).' },
        { key: 'active', label: 'Etkin', type: 'checkbox' },
      ],
      value: { name: room?.name || '', capacity: room?.capacity ?? '', active: room ? room.active !== false : true },
      submitLabel: room ? 'Kaydet' : 'Derslik ekle',
      onSubmit: (draft, { key }) => (room
        ? app.api.put(`/v1/admin/rooms/${room.id}`, roomBody(draft))
        : app.api.post('/v1/admin/rooms', roomBody(draft), { key })),
    });
    if (!result) return;
    app.toast(room ? 'Derslik kaydedildi.' : `${result.name || 'Derslik'} eklendi`, 'good');
    view.reload();
  }

  async function removeRoom(room) {
    const ok = await confirmAction(app, {
      title: `${room.name} silinsin mi?`,
      description: 'Bu dersliğe bağlı tahtalar dersliksiz kalır. Ders programındaki derslik metinleri ve kayıtlar değişmez.',
      confirmLabel: 'Dersliği sil',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/rooms/${room.id}`),
      { success: `${room.name} silindi.` });
    if (done !== undefined) view.reload();
  }

  let started = false;
  return {
    node,
    shown() { if (!started) { started = true; view.reload(); } },
  };
}

/** Eşleme kodu penceresi: kod YALNIZ bu yanıtta görünür (sunucu özetini saklar). */
export function showCode(app, name, pairing) {
  const overlay = h('div', 'kit-overlay');
  const box = h('div', 'kit-dialog ak-dialog');
  box.setAttribute('role', 'dialog');
  box.setAttribute('aria-modal', 'true');
  box.append(h('h3', 'kit-dialog-title', `${name} için eşleme kodu`));
  const code = h('div', 'ak-rec-code', pairing?.code || '—');
  code.setAttribute('aria-label', `Eşleme kodu ${String(pairing?.code || '').split('').join(' ')}`);
  box.append(code,
    h('p', 'kit-dialog-text', `${fmtDateTime(pairing?.expiresAt)}'e kadar geçerli · tek kullanımlık. Kod bir daha gösterilmez; `
      + 'kaybolursa yeni kod üretin.'),
    h('p', 'kit-dialog-text', 'Tahtada ilk kurulumdan sonra "Bu tahtayı kuruma bağlayın" ekranına yazın. '
      + 'Yeni kod eski eşlemeyi geçersiz kılar.'));
  let release = () => {};
  const close = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    release();
  };
  const onKey = (event) => { if (event.key === 'Escape') close(); };
  const actions = h('div', 'kit-dialog-actions');
  const done = button('Tamam', { variant: 'primary', onClick: close });
  actions.append(button('Kopyala', { onClick: async () => {
    const copied = await copyText(pairing?.code || '');
    app.toast(copied ? 'Kod kopyalandı.' : 'Kopyalanamadı; kodu elle yazın.', copied ? 'good' : 'bad');
  } }), done);
  box.append(actions);
  overlay.append(box);
  document.addEventListener('keydown', onKey);
  app.root.append(overlay);
  release = dialogA11y(box);
  done.focus();
  return { close };
}

// ============================================================ kayıtlar

function recordingsPane(app, ctx) {
  const node = h('div', 'ak-stack');
  const state = { filter: 'all', q: '', from: '', to: '', teacherId: '', groupId: '', sort: 'recent' };
  const rangeNote = h('div', 'ak-rec-error');
  rangeNote.setAttribute('role', 'status');

  const chips = chipRow(REC_FILTERS, state.filter, (key) => { state.filter = key || 'all'; chips.set(state.filter); reload(); });
  const from = dateInput('', (iso) => { state.from = iso || ''; reload(); }, 'Başlangıç');
  const to = dateInput('', (iso) => { state.to = iso || ''; reload(); }, 'Bitiş');
  const teacher = selectBox([{ value: '', label: 'Bütün öğretmenler' }], '', (value) => { state.teacherId = value; reload(); },
    { label: 'Öğretmen' });
  const group = selectBox([{ value: '', label: 'Bütün sınıflar' }], '', (value) => { state.groupId = value; reload(); },
    { label: 'Sınıf' });
  const sort = selectBox(REC_SORTS, state.sort, (value) => { state.sort = value; reload(); }, { label: 'Sıralama' });
  // Sunucu araması (`admin_listRecordings?q=`: kayıt adı, ders, konu, öğretmen, derslik; Türkçe harf duyarsız; ≥ 2).
  const search = searchBox({ placeholder: 'Kayıt ara', label: 'Kayıt ara (ad, ders, konu, öğretmen, derslik)', delay: 300,
    onSearch: (text) => {
      const next = text.length >= 2 ? text : '';
      if (next === state.q) return;
      state.q = next;
      reload();
    } });
  node.append(chips.node, toolbar(search.node, from.node, to.node, teacher.node, group.node, sort.node), rangeNote);
  app.lookups?.options?.('teachers', { empty: 'Bütün öğretmenler' }).then((list) => teacher.options(list)).catch(() => {});
  app.lookups?.options?.('classGroups', { empty: 'Bütün sınıflar' }).then((list) => group.options(list)).catch(() => {});

  const listApp = { ...app, api: { ...app.api, pages: (path, query, options) => {
    const iterator = app.api.pages(path, query, options);
    return {
      get done() { return iterator.done; },
      next: () => ctx.guard(iterator.next()),
    };
  } } };

  const list = pagedList({
    app: listApp,
    path: '/v1/admin/recordings',
    query: () => recordingQuery(state.filter, state),
    onLoaded: () => ctx.clearNotice(),
    onRow: (row) => recordingDrawer(app, row, {
      onChange: (next) => list.replace((item) => item.id === next.id, next),
      onRemove: (id) => list.remove((item) => item.id === id),
    }),
    columns: [
      { key: 'title', label: 'Ders', width: 'minmax(0, 1.6fr)',
        cell: (row) => stack(h('b', '', recordingTitle(row)), scheduleLine(row) ? small(scheduleLine(row)) : null,
          recordingExtra(row) ? small(recordingExtra(row)) : null) },
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1fr)', cell: (row) => personName(row.teacher) },
      { key: 'date', label: 'Tarih', width: '130px',
        cell: (row) => stack(fmtDate(row.date), row.startsAt ? small(fmtTime(row.startsAt)) : null) },
      { key: 'size', label: 'Süre · boyut', width: '150px', cell: (row) => sizeText(row) },
      { key: 'views', label: 'İzleyen', width: '90px', cell: (row) => viewsText(row.viewStats) },
      { key: 'status', label: 'Durum', width: '120px', cell: (row) => {
        const [text, tone] = recordingState(row);
        return badge(text, tone);
      } },
      { key: 'expiresAt', label: 'Silinir', width: '110px', cell: (row) => (row.expiresAt ? fmtDate(row.expiresAt) : '—') },
    ],
    empty: { title: 'Henüz ders kaydı yok',
      text: 'Öğretmen tahtada "Derse Başla" deyince kayıt burada görünür; ders bitince derse atanmış öğrenciler izler.' },
  });
  node.append(list.node);

  function reload() {
    const problemText = rangeProblem(state.from, state.to);
    rangeNote.textContent = problemText || '';
    if (problemText) return;
    list.reload();
  }

  let started = false;
  return {
    node,
    shown(payload) {
      if (!started) { started = true; reload(); }
      if (payload?.recordingId) {
        recordingDrawer(app, { id: payload.recordingId }, {
          onChange: (next) => list.replace((item) => item.id === next.id, next),
          onRemove: (id) => list.remove((item) => item.id === id),
        });
      }
    },
    destroy() { list.destroy(); search.destroy(); from.destroy?.(); to.destroy?.(); },
  };
}

/**
 * Kayıt ayrıntısı. Listeden açılınca satır yükü kullanılır (ek istek yok); derin bağlantıda (`{recordingId}`)
 * satır yükü yoktur — kim izledi ve paylaşım yine çalışır, başlık sonradan dolar.
 */
export function recordingDrawer(app, row, { onChange, onRemove } = {}) {
  let recording = { ...row };
  const panel = openDrawer(app, { title: row.subject ? recordingTitle(row) : 'Ders kaydı' });

  function paint() {
    const nodes = [];
    const [stateText, tone] = recordingState(recording);
    for (const flag of recording.flags || []) {
      if (FLAGS[flag]) nodes.push(alertBox(FLAGS[flag], flag === 'recovered' || flag === 'incomplete' ? 'info' : 'warn'));
    }
    if (recording.hidden) nodes.push(alertBox('Bu kayıt gizli: öğrenci ve veliler görmez.', 'info'));
    if (recording.subject || recording.date) {
      nodes.push(card('', kv([
        ['Ad', recording.title || 'Ders programındaki ad'],
        recording.title && recording.scheduleTitle ? ['Ders programı adı', recording.scheduleTitle] : null,
        ['Ders', recording.subject?.name || '—'],
        topicNames(recording) ? ['Konular', `${topicNames(recording)}${recording.curriculumAssigned ? '' : ' (ders programından)'}`] : null,
        ['Öğretmen', personName(recording.teacher)],
        ['Sınıf / grup', (recording.groups || []).map((group) => group.name).join(', ') || '—'],
        ['Derslik', recording.room || '—'],
        ['Ders saati', `${fmtDate(recording.date)} ${recording.startsAt ? fmtTime(recording.startsAt) : ''}`
          + `${recording.endsAt ? `–${fmtTime(recording.endsAt)}` : ''}`],
        ['Kayıt', `${recording.startedAt ? fmtDateTime(recording.startedAt) : '—'}`
          + `${recording.endedAt ? ` → ${fmtTime(recording.endedAt)}` : ''}${Number(recording.part || 1) > 1 ? ` · ${recording.part}. bölüm` : ''}`],
        ['Süre · boyut', sizeText(recording)],
        ['Kalite', QUALITY[recording.quality]?.[0] || recording.quality || '—'],
        ['Kamera', recording.hasCamera ? 'Öğretmen kamerası var' : 'Yalnız tahta ekranı'],
        ['Mikrofon', micEventsText(recording.microphoneEvents)],
        ['Durum', badge(stateText, tone)],
        ['İzleyen', recording.viewStats ? `${viewsText(recording.viewStats)} öğrenci · ${recording.viewStats.completedCount || 0} `
          + `tamamladı · ${recording.viewStats.downloadCount || 0} indirdi` : '—'],
        ['Silinir', recording.expiresAt ? `${fmtDate(recording.expiresAt)} (saklama süresi; kota temizliği daha erken silebilir)` : '—'],
      ])));
    }
    const tools = h('div', 'ak-inline');
    if (recording.status !== 'deleted') {
      tools.append(actionButton(app, 'admin_updateRecording', 'Adını değiştir', { variant: 'ghost', onClick: () => rename() }));
      if (recording.title) {
        tools.append(actionButton(app, 'admin_updateRecording', 'Varsayılana dön', { variant: 'ghost', onClick: () => saveTitle(null) }));
      }
      if (app.can('admin_getRecordingCurriculum')) {
        tools.append(actionButton(app, 'admin_updateRecording', 'Ders ve konu', { variant: 'ghost', onClick: () => curriculum() }));
      }
      tools.append(actionButton(app, 'admin_updateRecording', recording.hidden ? 'Göster' : 'Gizle',
        { variant: 'ghost', onClick: () => toggleHidden() }));
      tools.append(actionButton(app, 'admin_deleteRecording', 'Kaydı sil', { variant: 'danger', onClick: () => remove() }));
    }
    nodes.push(tools);
    info.replaceChildren(...nodes);
  }

  // Bilgi kısmı yeniden çizilir; paylaşım ve "kim izledi" kartları BİR KEZ kurulur (tazelemede yeniden istek yok).
  const info = h('div', 'ak-stack');
  panel.body.append(info);
  const shares = app.can('admin_listRecordingShares') ? sharesCard(app, () => recording) : null;
  if (shares) panel.body.append(shares);
  if (app.can('admin_listRecordingViews')) panel.body.append(viewsCard(app, recording));

  async function toggleHidden() {
    const next = await runAction(app, () => app.api.patch(`/v1/admin/recordings/${recording.id}`, hideBody(recording)),
      { success: recording.hidden ? 'Kayıt yeniden görünür · öğrenci ve veliler izleyebilir' : 'Kayıt gizlendi · öğrenci ve veliler görmez' });
    if (!next) return;
    recording = { ...recording, ...next };
    onChange?.(recording);
    paint();
  }

  function applied(next) {
    if (!next) return;
    recording = { ...recording, ...next };
    panel.setTitle(recordingTitle(recording));
    onChange?.(recording);
    paint();
  }

  async function saveTitle(title) {
    const next = await runAction(app, () => app.api.patch(`/v1/admin/recordings/${recording.id}`, { title }),
      { success: title ? 'Kaydın adı değişti' : 'Ders programındaki ada dönüldü' });
    applied(next);
  }

  // "Adını değiştir": boş bırakılırsa ders programındaki ad (web / mobil / tahta ile aynı metinler).
  async function rename() {
    const next = await formDialog(app, {
      title: 'Kaydı adlandır',
      description: 'Boş bırakırsan ders programındaki ad kullanılır.',
      fields: [{ key: 'title', label: 'Kaydın adı', maxLength: 200, wide: true, placeholder: recording.scheduleTitle || '',
        hint: `En çok ${TITLE_MAX} karakter` }],
      value: { title: recording.title || '' },
      onSubmit: (draft, { key }) => app.api.patch(`/v1/admin/recordings/${recording.id}`, renameBody(draft.title),
        { key }),
    });
    if (next && next !== true) {
      app.toast(next.title ? 'Kaydın adı değişti' : 'Ders programındaki ada dönüldü', 'good');
      applied(next);
    }
  }

  // "Ders ve konu": merkezi müfredat kapsamı (TYT / AYT / sınıf) → ders → bir ya da birden çok konu.
  async function curriculum() {
    const base = await runAction(app, () => app.api.get(`/v1/admin/recordings/${recording.id}/curriculum`));
    if (!base) return;
    const cache = { '': base };
    const next = await formDialog(app, {
      title: 'Ders ve konu',
      description: 'Müfredattan ders ve bir ya da birden çok konu seç. Öğrenciler kaydı bu konularla bulur.',
      wide: true,
      value: { scope: base.defaultScope || base.scopes?.[0]?.key || '', subjectId: recording.subjectId || recording.subject?.id || '',
        topicIds: (recording.topics?.length ? recording.topics : (recording.topic ? [recording.topic] : [])).map((topic) => topic.id) },
      fields: [{ key: 'pick', label: 'Ders ve konu', type: 'custom', wide: true,
        render: (draft) => {
          const node = h('div', 'ak-stack');
          const scope = selectBox((base.scopes || []).map((item) => ({ value: item.key, label: item.label })), draft.scope,
            (value) => { draft.scope = value; load(); }, { label: 'Kapsam' });
          const subject = selectBox([], draft.subjectId, (value) => {
            if (value !== draft.subjectId) draft.topicIds = [];
            draft.subjectId = value;
            fill();
          }, { label: 'Ders' });
          const topicsBox = h('div', 'ak-stack');
          node.append(toolbar(scope.node, subject.node), topicsBox);
          const current = () => {
            const meta = (base.scopes || []).find((item) => item.key === draft.scope);
            return meta && !meta.loaded ? cache[draft.scope] : base;
          };
          function fill() {
            const { subjects, topics } = curriculumOptions(current(), draft.scope, draft.subjectId);
            subject.options([{ value: '', label: 'Ders seçin' }, ...subjects]);
            subject.set(draft.subjectId);
            const chips = multiChips(topics.map((topic) => ({ value: topic.value, label: topic.label })), draft.topicIds,
              (list) => { draft.topicIds = list; });
            topicsBox.replaceChildren(topics.length ? chips.node : small(draft.subjectId ? 'Bu derste konu yok' : 'Önce ders seçin'));
          }
          async function load() {
            const meta = (base.scopes || []).find((item) => item.key === draft.scope);
            if (meta && !meta.loaded && !cache[draft.scope]) {
              topicsBox.replaceChildren(small('Müfredat alınıyor…'));
              cache[draft.scope] = await app.api.get(`/v1/admin/recordings/${recording.id}/curriculum`, { scope: draft.scope })
                .catch(() => ({ programs: [] }));
            }
            fill();
          }
          fill();
          return node;
        },
        validate: (_raw, draft) => {
          try { curriculumBody(draft.subjectId, draft.topicIds); return ''; } catch (error) { return error.message; }
        } }],
      onSubmit: (draft, { key }) => app.api.patch(`/v1/admin/recordings/${recording.id}`,
        curriculumBody(draft.subjectId, draft.topicIds), { key }),
    });
    if (next && next !== true) {
      app.toast('Ders ve konu kaydedildi', 'good');
      applied(next);
    }
  }

  async function remove() {
    const title = recording.subject ? recordingTitle(recording) : 'Ders';
    const ok = await confirmAction(app, {
      title: `${title} kaydı silinsin mi?`,
      description: 'Kayıt herkesten kalkar ve depodan silinir; öğrencilerin indirdiği kopyalar da uygulama bir sonraki '
        + 'açılışında silinir. Geri alınamaz.',
      confirmLabel: 'Kaydı sil',
      danger: true,
    });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/recordings/${recording.id}`),
      { success: `${title} kaydı silindi · indirilmiş kopyalar da kaldırılır` });
    if (done === undefined) return;
    onRemove?.(recording.id);
    panel.close();
  }

  paint();
  // Ders bilgi satırları güncel kayıttan (`admin_getRecording`): bildirimden / derin bağlantıdan yalnız kimlikle
  // açılınca satır yükü yoktur; listeden açılınca da sayılar (izleyen, durum) tazelenir.
  if (app.can('admin_getRecording')) {
    app.api.get(`/v1/admin/recordings/${row.id}`).then((fresh) => {
      if (!fresh || !panel.node.isConnected) return;
      recording = { ...recording, ...fresh };
      panel.setTitle(recordingTitle(recording));
      if (row.subject) onChange?.(recording);
      paint();
      shares?.refreshIntro();
    }).catch((failure) => {
      if (!row.subject && panel.node.isConnected) {
        info.prepend(alertBox(failure?.code === 'recording_not_found'
          ? 'Kayıt bulunamadı (silinmiş olabilir).' : 'Kayıt bilgileri alınamadı.', 'bad'));
      }
    });
  }
  return panel;
}

/** `current()` güncel kaydı verir (ayrıntı `admin_getRecording` ile tazelenince açıklama da güncellenir). */
function sharesCard(app, current) {
  const recording = { id: current().id };
  const box = h('section', 'kit-card');
  const head = h('div', 'kit-card-head ak-rec-cardhead');
  head.append(h('h3', 'kit-card-title', 'Paylaşım'), h('span', 'kit-spacer'),
    actionButton(app, 'admin_addRecordingShares', 'Paylaş', { variant: 'ghost', onClick: () => addShares() }));
  const intro = h('p', 'ak-muted ak-rec-note', shareIntro(current()));
  box.append(head, intro);
  box.refreshIntro = () => { intro.textContent = shareIntro(current()); };
  const view = loader(() => app.api.get(`/v1/admin/recordings/${recording.id}/shares`), (data) => {
    const items = data?.items || [];
    if (!items.length) return small('Ek paylaşım yok.');
    const listNode = h('ul', 'ak-rec-shares');
    for (const share of items) {
      const item = h('li', 'ak-rec-share');
      const label = shareLabel(share);
      const removeButton = actionButton(app, 'admin_removeRecordingShare', 'Kaldır', { variant: 'ghost',
        onClick: async () => {
          const done = await runAction(app, () => app.api.del(`/v1/admin/recordings/${recording.id}/shares/${share.id}`),
            { success: `${label} paylaşımı kaldırıldı.` });
          if (done !== undefined) view.reload();
        } });
      removeButton.setAttribute('aria-label', `${label} paylaşımını kaldır`);
      item.append(stack(h('span', '', label), small(`${fmtDateTime(share.createdAt)}${share.createdBy ? ` · ${personName(share.createdBy)}` : ''}`)),
        removeButton);
      listNode.append(item);
    }
    return listNode;
  }, { rows: 2, columns: 2 });
  const inner = h('div', 'kit-card-body');
  inner.append(view.node);
  box.append(inner);
  view.reload();

  async function addShares() {
    const [classes, groups] = await Promise.all([
      app.lookups?.classGroups?.().catch(() => []) ?? [],
      app.lookups?.studentGroups?.().catch(() => []) ?? [],
    ]);
    const groupOptions = [...classes, ...groups].map((item) => ({ value: item.id, label: item.name }));
    let picker = null;
    const result = await formDialog(app, {
      title: 'Kaydı paylaş',
      description: shareIntro(current()),
      wide: true,
      fields: [
        { key: 'studentIds', label: 'Öğrenciler', type: 'custom', render: (draft, set) => {
          picker = studentSelect(app, { classes, onChange: (list) => set(list.map((item) => item.id)) });
          return picker;
        } },
        groupOptions.length ? { key: 'groupIds', label: 'Gruplar', type: 'custom',
          hint: 'Grubun bugünkü üyeleri (sonradan katılanlar dahil) izler.',
          render: (draft, set) => multiChips(groupOptions, [], (list) => set(list)) } : null,
        { key: 'notify', label: 'Yeni erişenlere "Ders kaydı hazır" bildirimi gitsin', type: 'checkbox' },
      ],
      value: { studentIds: [], groupIds: [], notify: true },
      submitLabel: 'Paylaş',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/recordings/${recording.id}/shares`,
        shareBody(draft), { key }),
    });
    picker?.destroy?.();
    if (!result) return;
    app.toast('Paylaşıldı · seçilenler bu kaydı görebilir', 'good');
    view.reload();
  }

  return box;
}

function viewsCard(app, recording) {
  const box = h('section', 'kit-card');
  const head = h('div', 'kit-card-head');
  head.append(h('h3', 'kit-card-title', 'Kim izledi'));
  box.append(head);
  const view = loader(() => app.api.get(`/v1/admin/recordings/${recording.id}/views`), (data) => {
    const stats = data?.stats || {};
    const items = data?.items || [];
    const parts = [kpiRow([
      { label: 'Erişebilen', value: String(stats.eligibleCount ?? 0) },
      { label: 'İzleyen', value: String(stats.watchedCount ?? 0) },
      { label: 'Tamamlayan', value: String(stats.completedCount ?? 0) },
      { label: 'İndiren', value: String(stats.downloadCount ?? 0) },
    ])];
    const table = dataTable({
      rows: items,
      rowKey: (item) => String(item?.student?.id || ''),
      dense: true,
      columns: [
        { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.4fr)', cell: (item) => stack(
          h('span', '', item.student?.displayName || '—'),
          small(`${item.student?.classGroup?.name || ''}${item.source === 'shared' ? ' · paylaşım' : ''}`)) },
        { key: 'state', label: 'Durum', width: '120px', cell: (item) => {
          const [text, tone] = viewState(item);
          return badge(text, tone);
        } },
        { key: 'watched', label: 'İzlenen', width: '90px', cell: (item) => (item.watchedSeconds ? durationText(item.watchedSeconds) : '—') },
        { key: 'last', label: 'Son izleme', width: '140px', cell: (item) => (item.lastWatchedAt ? fmtDateTime(item.lastWatchedAt) : '—') },
        { key: 'download', label: 'İndirdi', width: '90px', cell: (item) => (item.downloaded ? 'Evet' : '—') },
      ],
      empty: emptyState({ title: 'Bu kayda erişen öğrenci yok' }),
    });
    parts.push(table.node);
    return parts;
  }, { rows: 4, columns: 4 });
  const inner = h('div', 'kit-card-body');
  inner.append(view.node);
  box.append(inner);
  view.reload();
  return box;
}

// ============================================================ depolama

function storagePane(app, ctx) {
  const node = h('div', 'ak-stack');
  const view = loader(async () => {
    const quota = await ctx.guard(app.api.get('/v1/admin/recordings/quota'));
    ctx.clearNotice();
    return quota;
  }, (quota) => {
    const shown = quotaView(quota);
    const box = h('section', 'kit-card ak-rec-quota');
    const top = h('div', 'ak-rec-quota-top');
    top.append(h('b', 'ak-rec-quota-used', shown.usedText),
      h('span', 'ak-muted', ` / ${shown.totalText} kullanılıyor · %${shown.pctText}`),
      h('span', 'kit-spacer'), badge(shown.stateText, shown.tone));
    const bar = h('div', `ak-rec-bar ${shown.tone}`);
    bar.setAttribute('role', 'meter');
    bar.setAttribute('aria-label', 'Depolama kullanımı');
    bar.setAttribute('aria-valuemin', '0');
    bar.setAttribute('aria-valuemax', '100');
    bar.setAttribute('aria-valuenow', String(Math.round(shown.barPct)));
    bar.setAttribute('aria-valuetext', shown.summary);
    const fill = h('span', 'ak-rec-bar-fill');
    fill.style.width = `${shown.barPct}%`;
    bar.append(fill);
    for (const mark of [shown.low, shown.high]) {
      const tick = h('span', 'ak-rec-bar-mark');
      tick.style.left = `${mark}%`;
      tick.setAttribute('aria-hidden', 'true');
      const text = h('span', 'ak-rec-bar-label', `%${mark}`);
      text.style.left = `${mark}%`;
      text.setAttribute('aria-hidden', 'true');
      bar.append(tick, text);
    }
    box.append(top, bar,
      h('p', 'ak-rec-eta', etaText(quota, longDay(quota?.estimatedFullDate))));
    const tiles = kpiRow([
      { label: 'Kayıt', value: String(quota?.recordingCount ?? 0) },
      { label: 'Günlük ortalama', value: gbText(quota?.avgDailyBytes), title: 'Son 30 günün ortalaması' },
      { label: 'En eski kayıt', value: quota?.oldestRecordingAt ? fmtDate(String(quota.oldestRecordingAt).slice(0, 10)) : '—' },
      { label: 'Son temizlik', value: quota?.lastCleanupAt ? fmtDate(String(quota.lastCleanupAt).slice(0, 10)) : '—',
        title: quota?.lastCleanupAt ? `${quota.lastCleanupDeletedCount ?? 0} kayıt silindi` : '' },
    ]);
    const foot = small(`Saklama: ders tarihinden itibaren en çok ${quota?.retentionDays ?? RETENTION_MAX} gün · `
      + `son temizlikte ${quota?.lastCleanupDeletedCount ?? 0} kayıt silindi · hesaplandı ${quota?.computedAt ? fmtDateTime(quota.computedAt) : '—'}`);
    return [box, tiles, foot];
  }, { rows: 3, columns: 3 });
  node.append(view.node);
  let started = false;
  return { node, shown() { if (!started) { started = true; view.reload(); } } };
}

// ============================================================ ayarlar

function settingsPane(app, ctx) {
  const node = h('div', 'ak-stack');
  const view = loader(async () => {
    const settings = await ctx.guard(app.api.get('/v1/admin/recordings/settings'));
    ctx.clearNotice();
    return settings;
  }, (settings) => form(settings), { rows: 4, columns: 2 });
  node.append(view.node);

  function form(settings) {
    const original = settingsDraft(settings);
    const draft = { ...original };
    const writable = app.can('admin_putRecordingSettings');
    const box = h('section', 'kit-card ak-rec-settings');
    const error = h('div', 'ak-rec-error');
    error.setAttribute('role', 'alert');

    // Saklama süresi: − [gün] +
    const retention = h('div', 'ak-rec-field');
    const retentionId = 'ak-rec-retention';
    const retentionLabel = h('label', 'kit-field-label', 'Saklama süresi');
    retentionLabel.htmlFor = retentionId;
    const days = h('input', 'kit-input ak-rec-num');
    days.id = retentionId;
    days.type = 'number';
    days.min = '1';
    days.max = String(RETENTION_MAX);
    days.value = String(draft.retentionDays);
    days.disabled = !writable;
    const setDays = (value) => {
      draft.retentionDays = Math.max(1, Math.min(RETENTION_MAX, Number(value) || 1));
      days.value = String(draft.retentionDays);
    };
    days.addEventListener('change', () => setDays(days.value));
    const minus = button('−', { onClick: () => setDays(draft.retentionDays - 1), disabled: !writable });
    minus.setAttribute('aria-label', 'Saklama süresini bir gün azalt');
    const plus = button('+', { onClick: () => setDays(draft.retentionDays + 1), disabled: !writable });
    plus.setAttribute('aria-label', 'Saklama süresini bir gün artır');
    const daysRow = h('div', 'ak-inline');
    daysRow.append(minus, days, plus, small(`gün · en çok ${RETENTION_MAX} (KVKK aydınlatma metniyle aynı)`));
    retention.append(retentionLabel, daysRow);

    // Depolama tavanı (GB)
    const quota = h('div', 'ak-rec-field');
    const quotaLabel = h('label', 'kit-field-label', 'Depolama tavanı');
    quotaLabel.htmlFor = 'ak-rec-quota-gb';
    const gb = h('input', 'kit-input ak-rec-num');
    gb.id = 'ak-rec-quota-gb';
    gb.type = 'number';
    gb.min = String(QUOTA_MIN_GB);
    gb.max = String(QUOTA_MAX_GB);
    gb.value = String(draft.quotaGb);
    gb.disabled = !writable;
    gb.addEventListener('change', () => { draft.quotaGb = Number(gb.value); });
    const gbRow = h('div', 'ak-inline');
    gbRow.append(gb, small(`GB · en çok ${QUOTA_MAX_GB}. %90 aşılınca en eski kayıtlar %80'e inene dek gece silinir; `
      + 'yeni kayıt engellenmez.'));
    quota.append(quotaLabel, gbRow);

    // Kalite
    const quality = h('div', 'ak-rec-field');
    const qualityTitle = h('span', 'kit-field-label', 'Kalite');
    qualityTitle.id = 'ak-rec-quality-title';
    const options = h('div', 'ak-rec-quality');
    options.setAttribute('role', 'radiogroup');
    options.setAttribute('aria-labelledby', qualityTitle.id);
    const paintQuality = () => {
      for (const item of options.querySelectorAll('.ak-rec-choice')) {
        const on = item.dataset.value === draft.quality;
        item.classList.toggle('on', on);
        item.setAttribute('aria-checked', on ? 'true' : 'false');
      }
    };
    for (const [value, [label, sub]] of Object.entries(QUALITY)) {
      const choice = h('button', 'ak-rec-choice');
      choice.type = 'button';
      choice.dataset.value = value;
      choice.setAttribute('role', 'radio');
      choice.disabled = !writable;
      choice.append(h('b', '', label), h('small', '', sub));
      choice.addEventListener('click', () => { draft.quality = value; paintQuality(); });
      options.append(choice);
    }
    paintQuality();
    quality.append(qualityTitle, options, small('Tahtalara bir sonraki nabızda gider; süren kayıt etkilenmez.'));

    // Kamera sahneleri
    const camera = h('label', 'ak-rec-toggle');
    const cam = h('input', 'kit-check');
    cam.type = 'checkbox';
    cam.checked = draft.allowCameraScenes;
    cam.disabled = !writable;
    cam.addEventListener('change', () => { draft.allowCameraScenes = cam.checked; });
    const camText = h('span', 'ak-rec-toggle-text');
    camText.append(h('b', '', 'Kamera sahnelerine izin ver'),
      small('Öğretmen açık rıza verdiyse "Tahta + Ben" ve "Sadece Ben" kullanılabilir. Kapalıyken yalnız tahta ekranı kaydedilir.'));
    camera.append(cam, camText);

    const save = actionButton(app, 'admin_putRecordingSettings', 'Kaydet', { variant: 'primary', onClick: async () => {
      error.textContent = '';
      draft.retentionDays = Number(days.value);
      draft.quotaGb = Number(gb.value);
      const problemText = retentionProblem(draft.retentionDays) || quotaProblem(draft.quotaGb);
      if (problemText) { error.textContent = problemText; return; }
      const warning = settingsWarning(original, draft);
      if (warning) {
        const ok = await confirmAction(app, { title: 'Ayarlar kaydedilsin mi?', description: warning,
          confirmLabel: 'Kaydet', danger: true });
        if (!ok) return;
      }
      let bodyValue;
      try { bodyValue = settingsBody(draft); } catch (failure) { error.textContent = failure.message; return; }
      const next = await runAction(app, () => app.api.put('/v1/admin/recordings/settings', bodyValue),
        { success: 'Ayarlar kaydedildi · web ve mobil yönetimde de görünür', button: save });
      if (next) view.reload();
    } });

    box.append(retention, quota, quality, camera, error);
    const foot = h('div', 'ak-inline');
    foot.append(save, small(settings?.updatedAt ? `Son değişiklik: ${fmtDateTime(settings.updatedAt)}` : 'Varsayılan ayarlar'));
    box.append(foot);
    const parts = [box];
    if (!writable) {
      parts.unshift(alertBox('Ayarları değiştirmek kurum ayarı yetkisi ister (yöneticinizden izin isteyin).', 'info'));
    }
    return parts;
  }

  let started = false;
  return { node, shown() { if (!started) { started = true; view.reload(); } } };
}
