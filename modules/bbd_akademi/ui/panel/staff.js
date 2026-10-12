// BBD Akademi — Personel: mobil yönetici erişimi, Akademi personeli, roller.
//
// KM OTORİTEDİR (PLATFORM §1.1). "Mobil yönetici erişimi" sekmesi Akademi'nin
// değil MODÜLÜN KENDİ tablosudur (`/api/bbd_akademi/km/staff`): kimin Akademi
// mobil uygulamasına yönetici/öğretmen olarak gireceğini KM tutar ve listeyi
// Akademi'ye TAM itilir (değişiklikte, zamanlanmış görevde ve "Şimdi
// eşitle"de — backend/staff.py). Panel `PUT /v1/admin/staff/sync` ucuna
// HİÇ gitmez. Bu uçlar kabuk kanalından geçer: hata `AkademiError` değil,
// yalnız `message`/`status` taşır.
//
// İYİMSER KİLİT: düzenleme satırın `revision`'ıyla gider; araya başka bir
// değişiklik girdiyse 409 döner ve liste tazelenir.
//
// ANONS ADI ("Hüseyin hoca"): zil sisteminin oturum başı çağrısında öğretmenin
// okunacağı ad. KM tek kaynaktır (sözleşme km-ogrenci-gruplari-ve-zil.md §1);
// kural `bell-plan.js` ve backend/staff.py'de (kırpılır, en çok 60 karakter).
// Sözleşmeye 2d91b96 ile girdi (`announceNameSync` açık); şalter kapatılırsa
// KM'de saklanır ama itilmez (backend/contract_features.py) — ekran bunu söyler.
//
// "Akademi personeli" Akademi'nin bildiği herkestir. `source=km` satırları
// salt okunurdur ("KM'den itilir"); yerel personel yalnız KM'nin personel
// itmediği kurumlarda eklenir (Akademi `staff_managed_by_km` ile de korur).
// Yerel personelin telefonu maskeli gelir; düzenlemede numara yeniden yazılır.
//
// "Roller": izin kataloğu alana göre gruplanır; hazır roller salt okunurdur.
// Yetki her yerde izinden gelir, rol adına göre dal açılmaz (K10).

import { blockedButton, button, foldText, h, loadStyles } from '../../ui-kit/kit.js';
import { alertBox, badge, card, hintBox, tabBar } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { formatPhone, normalizePhone } from '../../ui-kit/form.js';
import { AkademiError } from './api.js';
import { ANNOUNCE_NAME_MAX, announceNameError, cleanAnnounceName } from './bell-plan.js';
import {
  actionButton, confirmAction, empty, fmtDateTime, formDialog, kv, loader, multiChips, num, openDrawer,
  problem, runAction, searchBox, sectionHead, selectBox, staticList, toolbar,
} from './common.js';

const KM_ROLES = [{ key: 'admin', label: 'Yönetici' }, { key: 'teacher', label: 'Öğretmen' }];
const SYNC_STATE = { never: ['Henüz eşitlenmedi', 'warn'], ok: ['Eşitlendi', 'good'],
  partial: ['Kısmen eşitlendi', 'warn'], failed: ['Eşitlenemedi', 'bad'] };
const SYNC_SOURCE = { change: 'değişiklik sonrası', schedule: 'zamanlanmış görev', manual: 'elle eşitleme' };
const ROW_ERRORS = { invalid_phone: 'Akademi telefonu geçersiz buldu', unknown_role: 'Rol Akademi\'de tanımlı değil',
  phone_conflict: 'Telefon Akademi\'de başka bir kişide' };

export function section(app) {
  loadStyles(import.meta.url, 'staff.css');
  const node = h('div', 'ak-stack ak-stf');
  node.append(sectionHead('Personel', {
    subtitle: 'Akademi mobil uygulamasına kimin gireceği (KM belirler), Akademi personel listesi ve roller.' }));

  const tabsSpec = [
    { key: 'mobile', label: 'Mobil yönetici erişimi', visible: app.has('staff') },
    { key: 'akademi', label: 'Akademi personeli', visible: app.can('admin_listStaff') },
    { key: 'roles', label: 'Roller', visible: app.can('admin_listRoles') },
  ].filter((tab) => tab.visible);
  if (!tabsSpec.length) {
    node.append(empty('Görüntülenecek sekme yok', 'Personel sekmeleri için yetkiniz yok.'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  let active = null;
  const staleAkademi = () => {
    app.api.invalidate('/v1/admin/staff');
    if (bodies.akademi) bodies.akademi.stale = true;
  };
  const goTab = (key) => {
    if (!tabsSpec.some((tab) => tab.key === key)) return;
    tabs.select(key, false);
    showTab(key);
  };
  const builders = {
    mobile: () => mobileTab(app, { onChanged: staleAkademi }),
    akademi: () => akademiTab(app, { goTab }),
    roles: () => rolesTab(app, { onChanged: staleAkademi }),
  };
  const tabs = tabBar(tabsSpec.map(({ key, label }) => ({ key, label })), tabsSpec[0].key,
    (key) => showTab(key));
  const slot = h('div');
  node.append(tabs.node, slot);

  function showTab(key) {
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    } else if (bodies[key].stale) {
      bodies[key].stale = false;
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    active = key;
  }

  let started = false;
  return {
    node,
    shown(payload) {
      const wanted = payload?.tab && tabsSpec.some((tab) => tab.key === payload.tab) ? payload.tab : null;
      if (!started) {
        started = true;
        const first = wanted || tabsSpec[0].key;
        tabs.select(first, false);
        showTab(first);
      } else if (wanted && wanted !== active) {
        goTab(wanted);
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ------------------------------------------------------- mobil yönetici erişimi

function mobileTab(app, { onChanged }) {
  const node = h('div', 'ak-stack');
  let rows = [];
  let roles = KM_ROLES;
  let sync = null;
  let announceSync = false;
  let query = '';
  let showInactive = false;

  const banner = h('div');
  const search = searchBox({ placeholder: 'Ad ya da telefon ara', onSearch: (text) => {
    query = foldText(text);
    paint();
  } });
  const inactiveToggle = h('label', 'ak-inline ak-stf-toggle');
  const inactiveCheck = h('input', 'kit-check');
  inactiveCheck.type = 'checkbox';
  inactiveCheck.addEventListener('change', () => { showInactive = inactiveCheck.checked; paint(); });
  inactiveToggle.append(inactiveCheck, h('span', '', 'Erişimi kapalı olanları da göster'));
  const syncButton = button('Şimdi eşitle', { onClick: () => syncNow() });
  const add = button('Erişim ver', { variant: 'primary', onClick: () => edit(null) });
  node.append(hintBox('Akademi mobil uygulamasına yönetici ya da öğretmen olarak kimin gireceğini Kontrol '
    + 'Merkezi belirler. Liste her değişiklikte, zamanlanmış görevde ve “Şimdi eşitle” ile Akademi\'ye tam '
    + 'olarak itilir; Akademi bu kayıtları değiştiremez.'), banner,
  toolbar(search.node, inactiveToggle, h('span', 'kit-spacer'), syncButton, add));

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Kişi', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.displayName));
        if (row.announceName) cell.append(h('small', 'ak-stf-announce', `Anons: ${row.announceName}`));
        if (row.note) cell.append(h('small', '', row.note));
        return cell;
      } },
      { key: 'phone', label: 'Telefon', width: '140px', cell: (row) => formatPhone(row.phone) },
      { key: 'roles', label: 'Akademi rolleri', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        for (const role of row.roles || []) cell.append(badge(roleLabel(roles, role), 'info'));
        return cell;
      } },
      { key: 'status', label: 'Durum', width: 'minmax(0, 1.2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(badge(row.active ? 'Erişim açık' : 'Erişim kapalı', row.active ? 'good' : 'dim'));
        if (row.syncError) {
          const error = h('small', 'ak-stf-row-error',
            `${ROW_ERRORS[row.syncError.code] || row.syncError.code}${row.syncError.message
              ? `: ${row.syncError.message}` : ''}`);
          cell.append(error);
        }
        return cell;
      } },
      { key: 'updated', label: 'Son değişiklik', width: '150px', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', fmtDateTime(row.updatedAt)), h('small', '', row.updatedBy || ''));
        return cell;
      } },
      { key: 'actions', label: '', width: '190px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button('Düzenle', { variant: 'ghost', onClick: () => edit(row) }));
        const toggle = button(row.active ? 'Erişimi kapat' : 'Erişimi aç', { variant: 'ghost',
          onClick: () => setActive(row, !row.active, toggle) });
        actions.append(toggle);
        return actions;
      } },
    ],
    rows: [],
    empty: empty('Mobil yönetici erişimi olan kimse yok',
      'Akademi mobil uygulamasına girecek yönetici ve öğretmenleri ekleyin.'),
  });

  const view = loader(() => app.api.km('/staff'), (data) => {
    rows = data?.items || [];
    roles = data?.roles?.length ? data.roles : KM_ROLES;
    sync = data?.sync || null;
    announceSync = Boolean(data?.announceNameSync);
    paintBanner();
    paint();
    return table.node;
  });
  node.append(view.node);

  function paint() {
    const digits = normalizePhone(query);
    const visible = rows.filter((row) => (showInactive || row.active)
      && (!query || foldText(`${row.displayName} ${row.announceName || ''} ${row.phone} ${row.note || ''}`)
        .includes(query)
        || (digits.length >= 3 && row.phone.includes(digits))));
    table.update({ rows: visible });
  }

  function paintBanner() {
    banner.replaceChildren();
    if (!sync) return;
    const [label, tone] = SYNC_STATE[sync.status] || [sync.status, 'dim'];
    const box = h('div', `kit-card ak-stf-sync ${tone}`);
    const head = h('div', 'ak-inline');
    head.append(badge(label, tone), h('span', 'ak-muted', [
      sync.attemptedAt ? `Son deneme ${fmtDateTime(sync.attemptedAt)}${sync.source
        ? ` (${SYNC_SOURCE[sync.source] || sync.source})` : ''}` : 'Akademi\'ye henüz itilmedi',
      sync.succeededAt ? `son başarılı ${fmtDateTime(sync.succeededAt)}` : '',
    ].filter(Boolean).join(' · ')));
    box.append(head);
    const result = sync.result;
    if (result && typeof result === 'object') {
      const errors = (result.errors || []).length;
      box.append(h('div', 'ak-muted', [`${num(result.created ?? 0)} yeni`, `${num(result.updated ?? 0)} güncellenen`,
        `${num(result.deactivated ?? 0)} kapatılan`, `${num(result.unchanged ?? 0)} değişmeyen`,
        errors ? `${num(errors)} satır reddedildi (satırda yazılı)` : ''].filter(Boolean).join(' · ')));
    }
    if (sync.status === 'failed' && sync.error) {
      box.append(h('div', 'ak-stf-sync-error', `${sync.error} — kayıtlar KM'de duruyor; sonraki turda tekrar denenir.`));
    }
    banner.append(box);
  }

  /** Yazma sonrası: satır yerinde, eşitleme şeridi yeni durumla. */
  function applyResult(result, verb) {
    if (result?.item) {
      const exists = rows.some((row) => row.id === result.item.id);
      rows = exists ? rows.map((row) => (row.id === result.item.id ? result.item : row))
        : [result.item, ...rows];
    }
    if (result?.sync) sync = result.sync;
    paintBanner();
    paint();
    onChanged?.();
    if (result?.sync?.status === 'failed') {
      app.toast(`${verb}; Akademi'ye itilemedi (${result.sync.error || 'bağlantı yok'}). Sonraki turda tekrar denenecek.`, 'warn');
    } else if (result?.item?.syncError) {
      app.toast(`${verb}; Akademi bu satırı reddetti: ${ROW_ERRORS[result.item.syncError.code]
        || result.item.syncError.message}.`, 'warn');
    } else {
      app.toast(`${verb} ve Akademi'ye eşitlendi.`, 'good');
    }
  }

  async function edit(row) {
    // Roller özel alandır: `value`ya konmaz (formDialog onu formGrid'e de verir ve
    // her alan değişikliğinde seçimi ilk değerle ezerdi); render içinde tohumlanır.
    const initialRoles = row ? [...(row.roles || [])] : ['admin'];
    const value = row
      ? { displayName: row.displayName, phone: row.phone, kmUserId: row.kmUserId || '', note: row.note || '',
        announceName: row.announceName || '', active: row.active }
      : { displayName: '', phone: '', kmUserId: '', note: '', announceName: '', active: true };
    const result = await formDialog(app, {
      title: row ? `${row.displayName} — düzenle` : 'Mobil yönetici erişimi ver',
      description: row ? 'Kaydedince liste Akademi\'ye hemen itilir.'
        : 'Kişi bu cep telefonuyla Akademi mobil uygulamasına seçilen rollerle girer.',
      value,
      fields: [
        { key: 'displayName', label: 'Ad soyad', required: true, maxLength: 80, wide: true },
        { key: 'phone', label: 'Cep telefonu', type: 'phone', required: true,
          hint: 'Uygulamaya bu numaraya gelen kodla girilir.' },
        { key: 'kmUserId', label: 'KM kullanıcı kimliği', maxLength: 80,
          hint: 'İsteğe bağlı; kişiyi KM kullanıcısıyla eşler.' },
        { key: 'announceName', label: 'Anons adı (ör. Hüseyin hoca)', maxLength: ANNOUNCE_NAME_MAX,
          placeholder: 'Hüseyin hoca', validate: (raw) => announceNameError(raw),
          hint: announceSync
            ? 'Zil çağrısında öğretmen bu adla okunur: “TYT grubu, Hüseyin hoca ile dersiniz başlıyor.” Boşsa genel çağrı okunur.'
            : 'Zil çağrısında öğretmen bu adla okunacak. KM\'de saklanır; Akademi bu alanı henüz desteklemiyor, '
              + 'destek gelince eşitlemeyle kendiliğinden gider.' },
        { key: 'roles', label: 'Akademi rolleri', type: 'custom', required: true,
          render: (_draft, set) => {
            set(initialRoles);
            return multiChips(roles.map((role) => ({ value: role.key, label: role.label })),
              initialRoles, (list) => set(list)).node;
          } },
        { key: 'note', label: 'Not', type: 'textarea', maxLength: 200, wide: true,
          placeholder: 'Ör. 12. sınıflar koordinatörü' },
        { key: 'active', label: 'Erişim açık', type: 'checkbox' },
      ],
      submitLabel: row ? 'Kaydet' : 'Erişim ver',
      onSubmit: (draft) => {
        const order = roles.map((role) => role.key);
        const clean = {
          displayName: String(draft.displayName || '').trim().replace(/\s+/g, ' '),
          phone: normalizePhone(draft.phone),
          roles: [...(draft.roles || [])].sort((a, b) => order.indexOf(a) - order.indexOf(b)),
          kmUserId: String(draft.kmUserId || '').trim(),
          note: String(draft.note || '').trim(),
          announceName: cleanAnnounceName(draft.announceName),
          active: Boolean(draft.active),
        };
        if (!row) {
          return app.api.km('/staff', { method: 'POST',
            body: { ...clean, kmUserId: clean.kmUserId || null } });
        }
        const changed = {};
        if (clean.displayName !== row.displayName) changed.displayName = clean.displayName;
        if (clean.phone !== row.phone) changed.phone = clean.phone;
        if (clean.roles.join(',') !== (row.roles || []).join(',')) changed.roles = clean.roles;
        // Boş dize kimliği temizler (sunucu `null`u "değişmedi" sayar).
        if (clean.kmUserId !== (row.kmUserId || '')) changed.kmUserId = clean.kmUserId;
        if (clean.note !== (row.note || '')) changed.note = clean.note;
        // Boş dize anons adını temizler.
        if (clean.announceName !== (row.announceName || '')) changed.announceName = clean.announceName;
        if (clean.active !== row.active) changed.active = clean.active;
        if (!Object.keys(changed).length) return { unchanged: true };
        return app.api.km(`/staff/${row.id}`, { method: 'PATCH',
          body: { expectedRevision: row.revision, ...changed } }).catch((failure) => {
          // 409: kayıt araya giren bir değişiklikle eskidi ya da telefon çakıştı —
          // liste arkada tazelenir ki yeni deneme güncel sürümle gitsin.
          if (failure?.status === 409) view.reload();
          throw failure;
        });
      },
    });
    if (!result || result === true) return;
    if (result.unchanged) { app.toast('Değişiklik yok.', 'warn'); return; }
    applyResult(result, row ? 'Kaydedildi' : 'Erişim verildi');
  }

  async function setActive(row, next, control) {
    if (!next) {
      const ok = await confirmAction(app, {
        title: `${row.displayName} — mobil erişim kapatılsın mı?`,
        description: 'Kişi Akademi mobil uygulamasına yönetici/öğretmen olarak giremez; Akademi açık oturumlarını '
          + 'kapatır. Kayıt silinmez, erişim yeniden açılabilir.',
        confirmLabel: 'Erişimi kapat',
        danger: true,
      });
      if (!ok) return;
    }
    control.disabled = true;
    try {
      const result = await app.api.km(`/staff/${row.id}`, { method: 'PATCH',
        body: { expectedRevision: row.revision, active: next } });
      applyResult(result, next ? 'Erişim açıldı' : 'Erişim kapatıldı');
    } catch (failure) {
      app.toast(problem(failure), 'bad');
      if (failure?.status === 409) view.reload();
    } finally {
      control.disabled = false;
    }
  }

  async function syncNow() {
    syncButton.disabled = true;
    syncButton.textContent = 'Eşitleniyor…';
    try {
      const result = await app.api.km('/staff/sync', { method: 'POST' });
      rows = result?.items || rows;
      sync = result?.sync || sync;
      paintBanner();
      paint();
      onChanged?.();
      const [label, tone] = SYNC_STATE[sync?.status] || ['Eşitleme bitti', 'good'];
      app.toast(sync?.status === 'failed' ? `${label}: ${sync.error || 'Akademi\'ye ulaşılamadı.'}` : `${label}.`,
        tone === 'bad' ? 'bad' : tone === 'warn' ? 'warn' : 'good');
    } catch (failure) {
      app.toast(problem(failure), 'bad');
    } finally {
      syncButton.disabled = false;
      syncButton.textContent = 'Şimdi eşitle';
    }
  }

  return { node, reload: () => view.reload(), destroy() { search.destroy(); } };
}

function roleLabel(roles, key) {
  return roles.find((role) => role.key === key)?.label || key;
}

// ----------------------------------------------------------- Akademi personeli

const SOURCE = { km: ['KM\'den', 'info'], local: ['Yerel', 'dim'] };

function akademiTab(app, { goTab }) {
  const node = h('div', 'ak-stack');
  const filters = { q: '', role: '', active: 'true' };
  let roleOptions = KM_ROLES.map((role) => ({ value: role.key, label: role.label }));
  let rows = [];

  const search = searchBox({ placeholder: 'Ad ara', onSearch: (text) => { filters.q = text; list.reload(); } });
  const roleSelect = selectBox([{ value: '', label: 'Bütün roller' }, ...roleOptions], '', (value) => {
    filters.role = value;
    list.reload();
  }, { label: 'Rol' });
  const activeSelect = selectBox([{ value: 'true', label: 'Etkin' }, { value: 'false', label: 'Pasif' },
    { value: '', label: 'Tümü' }], 'true', (value) => { filters.active = value; list.reload(); },
  { label: 'Durum' });
  const toolsSlot = h('div', 'ak-inline');
  node.append(toolbar(search.node, roleSelect.node, activeSelect.node, h('span', 'kit-spacer'), toolsSlot));

  if (app.can('admin_listRoles')) {
    app.api.getCached('/v1/admin/roles').then((data) => {
      roleOptions = (data?.items || []).map((role) => ({ value: role.key, label: role.name }));
      if (roleOptions.length) roleSelect.options([{ value: '', label: 'Bütün roller' }, ...roleOptions]);
    }).catch(() => {});
  }

  const list = staticList({
    load: () => app.api.get('/v1/admin/staff', {
      q: filters.q || undefined,
      role: filters.role || undefined,
      active: filters.active === '' ? undefined : filters.active === 'true',
    }),
    onLoaded: (items) => { rows = items; paintTools(); },
    columns: [
      { key: 'name', label: 'Kişi', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.displayName), h('small', '', row.phoneMasked || ''));
        return cell;
      } },
      { key: 'roles', label: 'Roller', width: 'minmax(0, 1.2fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        for (const role of row.roles || []) cell.append(badge(role.name || role.key, 'info'));
        return cell;
      } },
      { key: 'source', label: 'Kaynak', cell: (row) => {
        const [text, tone] = SOURCE[row.source] || [row.source || '—', 'dim'];
        return badge(text, tone);
      } },
      { key: 'app', label: 'Uygulama', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.hasAppAccount ? 'Giriş yaptı' : 'Henüz girmedi'));
        if (row.lastSeenAt) cell.append(h('small', '', `son görülme ${fmtDateTime(row.lastSeenAt)}`));
        return cell;
      } },
      { key: 'active', label: 'Durum', cell: (row) => badge(row.active ? 'Etkin' : 'Pasif', row.active ? 'good' : 'dim') },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.source === 'local') {
          actions.append(actionButton(app, 'admin_updateStaff', 'Düzenle', { variant: 'ghost',
            onClick: () => editLocal(row) }));
          if (row.active) {
            actions.append(actionButton(app, 'admin_deactivateStaff', 'Pasifleştir', { variant: 'ghost',
              onClick: () => deactivate(row) }));
          }
        } else {
          const note = h('span', 'ak-muted', 'KM\'den itilir');
          note.title = 'Bu kişi Kontrol Merkezi\'nin mobil yönetici listesinden gelir; değişiklik “Mobil '
            + 'yönetici erişimi” sekmesinden yapılır.';
          actions.append(note);
        }
        return actions;
      } },
    ],
    empty: { title: 'Bu süzgece uyan personel yok', text: 'Süzgeçleri değiştirin.' },
  });
  node.append(list.node);

  /** KM personel itiyorsa yeni yerel personel eklenmez; kişi KM listesine girer. */
  function kmManages() {
    const state = app.status?.staffSync?.status;
    return (state && state !== 'never') || rows.some((row) => row.source === 'km');
  }

  function paintTools() {
    toolsSlot.replaceChildren();
    if (!kmManages()) {
      toolsSlot.append(actionButton(app, 'admin_createStaff', 'Yerel personel ekle', { variant: 'primary',
        onClick: () => editLocal(null) }));
      return;
    }
    toolsSlot.append(h('span', 'ak-muted', 'Personel KM\'den yönetiliyor.'));
    if (app.has('staff')) {
      toolsSlot.append(button('Mobil yönetici erişimine git', { onClick: () => goTab('mobile') }));
    }
  }

  async function editLocal(row) {
    const saved = await formDialog(app, {
      title: row ? `${row.displayName} — düzenle` : 'Yerel personel ekle',
      description: row ? `Güvenlik için telefon maskeli gelir (${row.phoneMasked || '—'}); kaydetmek için `
        + 'numarayı yeniden yazın.' : 'Yalnız personeli KM\'den itilmeyen kurumlar için.',
      value: { displayName: row?.displayName || '', phone: '', active: row ? row.active : true },
      fields: [
        { key: 'displayName', label: 'Ad soyad', required: true, maxLength: 80, wide: true },
        { key: 'phone', label: 'Cep telefonu', type: 'phone', required: true },
        { key: 'roleKeys', label: 'Roller', type: 'custom', required: true,
          render: (_draft, set) => {
            const initial = (row?.roles || []).map((role) => role.key);
            set(initial);
            return multiChips(roleOptions, initial, (selected) => set(selected)).node;
          } },
        { key: 'active', label: 'Etkin', type: 'checkbox' },
      ],
      submitLabel: row ? 'Kaydet' : 'Ekle',
      onSubmit: (draft, { key }) => {
        const body = {
          displayName: String(draft.displayName || '').trim(),
          phone: normalizePhone(draft.phone),
          roleKeys: draft.roleKeys || [],
          active: Boolean(draft.active),
        };
        return row ? app.api.patch(`/v1/admin/staff/${row.id}`, body)
          : app.api.post('/v1/admin/staff', body, { key });
      },
    });
    if (saved) {
      app.toast(row ? 'Personel kaydedildi.' : 'Personel eklendi.', 'good');
      app.api.invalidate('/v1/admin/staff');
      list.reload();
    }
  }

  async function deactivate(row) {
    const ok = await confirmAction(app, { title: `${row.displayName} pasifleştirilsin mi?`,
      description: 'Kişi Akademi\'ye giremez ve açık oturumları kapanır. Kayıt ve geçmişi silinmez.',
      confirmLabel: 'Pasifleştir', danger: true });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/staff/${row.id}`),
      { success: 'Personel pasifleştirildi.' });
    if (done !== undefined) {
      app.api.invalidate('/v1/admin/staff');
      list.reload();
    }
  }

  return { node, reload: () => list.reload(), destroy() { search.destroy(); } };
}

// ---------------------------------------------------------------------- roller

const ROLE_KEY = /^[a-z][a-z0-9_]{1,39}$/;

function rolesTab(app, { onChanged }) {
  const node = h('div', 'ak-stack');
  let catalog = [];
  const add = actionButton(app, 'admin_createRole', 'Yeni rol', { variant: 'primary', onClick: () => edit(null) });
  node.append(toolbar(h('span', 'ak-muted', 'Yetki her zaman izinlerden gelir; rol, izinleri bir arada '
    + 'vermenin yoludur. Hazır roller değiştirilemez.'), h('span', 'kit-spacer'), add));

  const list = staticList({
    load: async () => {
      const [roles, permissions] = await Promise.all([
        app.api.getCached('/v1/admin/roles'),
        app.can('admin_listPermissions')
          ? app.api.getCached('/v1/admin/permissions', undefined, { ttlMs: 10 * 60_000 }).catch(() => null)
          : null,
      ]);
      catalog = permissions?.items || [];
      return roles;
    },
    columns: [
      { key: 'name', label: 'Rol', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const title = h('span', 'ak-inline');
        title.append(h('b', '', row.name));
        if (row.builtIn) title.append(badge('Hazır', 'info'));
        cell.append(title, h('code', 'ak-stf-mono', row.key));
        return cell;
      } },
      { key: 'permissions', label: 'İzin', align: 'num', cell: (row) => {
        const text = h('span', '', `${num((row.permissions || []).length)} izin`);
        text.title = (row.permissions || []).join('\n');
        return text;
      } },
      { key: 'members', label: 'Kişi', align: 'num', cell: (row) => num(row.memberCount ?? 0) },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.builtIn) {
          actions.append(button('İncele', { variant: 'ghost', onClick: () => inspect(row) }));
          return actions;
        }
        actions.append(actionButton(app, 'admin_updateRole', 'Düzenle', { variant: 'ghost',
          onClick: () => edit(row) }));
        actions.append(row.memberCount
          ? blockedButton('Sil', `Bu rol ${num(row.memberCount)} kişide kullanılıyor; önce kişilerden kaldırın.`,
            { variant: 'ghost' })
          : actionButton(app, 'admin_deleteRole', 'Sil', { variant: 'ghost', onClick: () => remove(row) }));
        return actions;
      } },
    ],
    empty: { title: 'Rol yok' },
  });
  node.append(list.node);

  async function edit(role) {
    const disposers = [];
    const saved = await formDialog(app, {
      title: role ? `“${role.name}” rolünü düzenle` : 'Yeni rol',
      description: role ? 'Değişiklik, rolü taşıyan kişilerin yetkisine bir sonraki oturum yenilemesinde yansır.'
        : 'Rolü oluşturduktan sonra kişilere Akademi personeli ya da KM mobil erişim listesinden verilir.',
      wide: true,
      value: { name: role?.name || '', key: role?.key || '' },
      fields: [
        { key: 'name', label: 'Rol adı', required: true, maxLength: 60, placeholder: 'Rehber öğretmen' },
        { key: 'key', label: 'Anahtar', required: true, maxLength: 40, readOnly: Boolean(role),
          placeholder: 'rehber_ogretmen',
          hint: role ? 'Anahtar değiştirilemez.' : 'Küçük harfle başlar; yalnız a–z, 0–9 ve alt çizgi.',
          validate: (value) => (ROLE_KEY.test(String(value || '')) ? null
            : 'Küçük harfle başlayan 2–40 karakter: a–z, 0–9, _') },
        { key: 'permissions', label: 'İzinler', type: 'custom', required: true, wide: true,
          render: (_draft, set) => {
            const initial = [...(role?.permissions || [])];
            set(initial);
            const checklist = permissionChecklist({ catalog, value: initial,
              onChange: (selected) => set(selected) });
            disposers.push(checklist.destroy);
            return checklist.node;
          } },
      ],
      submitLabel: role ? 'Kaydet' : 'Rolü oluştur',
      onSubmit: (draft, { key }) => {
        const body = { key: role ? role.key : String(draft.key || '').trim(),
          name: String(draft.name || '').trim(), permissions: draft.permissions || [] };
        if (!body.permissions.length) throw new AkademiError({ message: 'En az bir izin seçin.' });
        return role ? app.api.put(`/v1/admin/roles/${role.key}`, body)
          : app.api.post('/v1/admin/roles', body, { key });
      },
    });
    disposers.forEach((fn) => fn());
    if (saved) {
      app.toast(role ? 'Rol kaydedildi.' : 'Rol oluşturuldu.', 'good');
      app.api.invalidate('/v1/admin/roles');
      onChanged?.();
      list.reload();
    }
  }

  async function remove(role) {
    const ok = await confirmAction(app, { title: `“${role.name}” rolü silinsin mi?`,
      description: 'Rol kalıcı olarak silinir. Kişide kullanılan rol silinemez.', confirmLabel: 'Sil', danger: true });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/roles/${role.key}`), { success: 'Rol silindi.' });
    if (done !== undefined) {
      app.api.invalidate('/v1/admin/roles');
      onChanged?.();
      list.reload();
    }
  }

  function inspect(role) {
    const panel = openDrawer(app, { title: role.name, subtitle: 'Hazır rol — değiştirilemez' });
    panel.body.append(card('', kv([
      ['Anahtar', h('code', 'ak-stf-mono', role.key)],
      ['Kişi', num(role.memberCount ?? 0)],
      ['İzin', num((role.permissions || []).length)],
    ])));
    const checklist = permissionChecklist({ catalog, value: role.permissions || [], readOnly: true });
    const box = h('div', 'ak-card-pad');
    box.append(checklist.node);
    panel.body.append(card('İzinler', box));
  }

  return { node, reload: () => list.reload() };
}

// ------------------------------------------------------------- izin listesi

/** İzin alanı → Türkçe grup adı. Bilinmeyen alan kendi adıyla görünür. */
const AREAS = {
  students: 'Öğrenciler', guardians: 'Veliler', academic: 'Akademik yapı', curriculum: 'Müfredat',
  resources: 'Kaynaklar', timetable: 'Ders programı', schedule: 'Program', attendance: 'Yoklama',
  homework: 'Ödevler', exams: 'Denemeler', progress: 'İlerleme', study: 'Çalışma', planning: 'Plan',
  finance: 'Kurum finansı', canteen: 'Kantin', books: 'Kitap', announcements: 'Duyurular',
  requests: 'Talepler', pa: 'Anons', tablet: 'Tablet', staff: 'Personel', roles: 'Roller',
  audit: 'Denetim', settings: 'Ayarlar', branding: 'Tema', directory: 'Dizin',
  integrations: 'Entegrasyon', dashboard: 'Panel', teacher: 'Öğretmen', notifications: 'Bildirimler',
  agenda: 'Ajanda', evaluations: 'Değerlendirme', learning: 'Öğrenme araçları',
};
const SCOPES = { tenant: 'Kurum', children: 'Kendi çocukları', self: 'Kendisi' };

const defaultScope = (permission) => {
  const scopes = permission?.scopes || [];
  if (!scopes.length) return '';
  return scopes.includes('tenant') ? 'tenant' : scopes[0];
};

/**
 * Alana göre gruplanmış izin seçimi. Değer `<alan>.<eylem>[:<kapsam>]` dizeleri.
 * Katalogda olmayan mevcut izinler KORUNUR (sessizce düşmez) ve ayrıca gösterilir.
 * Katalog boşsa (okunamadıysa) izinler elle, satır satır yazılır.
 *
 * @returns {{node, value: () => string[], destroy}}
 */
export function permissionChecklist({ catalog = [], value = [], readOnly = false, onChange } = {}) {
  loadStyles(import.meta.url, 'staff.css');
  const node = h('div', 'ak-stf-perms');

  if (!catalog.length) {
    if (readOnly) {
      const chips = h('div', 'ak-chips');
      for (const item of value) chips.append(h('code', 'ak-stf-perm-chip', item));
      if (!value.length) chips.append(h('span', 'ak-muted', 'İzin yok.'));
      node.append(chips);
      return { node, value: () => [...value], destroy() {} };
    }
    const area = h('textarea', 'kit-textarea ak-stf-perm-text');
    area.placeholder = 'Her satıra bir izin (ör. students.view:tenant)';
    area.value = value.join('\n');
    const read = () => area.value.split(/\s+/).map((item) => item.trim()).filter(Boolean);
    area.addEventListener('input', () => onChange?.(read()));
    node.append(alertBox('İzin kataloğu okunamadı; izinleri elle yazın.', 'info'), area);
    return { node, value: read, destroy() {} };
  }

  const known = new Map(catalog.map((permission) => [permission.key, permission]));
  const chosen = new Map();              // anahtar → kapsam ('' = kapsamsız)
  const unknown = [];
  for (const raw of value || []) {
    const [key, scope = ''] = String(raw).split(':');
    if (known.has(key)) chosen.set(key, scope || defaultScope(known.get(key)));
    else unknown.push(String(raw));
  }
  let query = '';
  const current = () => [...[...chosen].map(([key, scope]) => (scope ? `${key}:${scope}` : key)), ...unknown];
  const emit = () => { count.textContent = `${num(chosen.size + unknown.length)} izin seçili`; onChange?.(current()); };

  const count = h('span', 'ak-muted');
  const groupsNode = h('div', 'ak-stf-perm-groups');
  let search = null;
  if (!readOnly) {
    search = searchBox({ placeholder: 'İzin ara (ad ya da açıklama)', onSearch: (text) => {
      query = foldText(text);
      paint();
    } });
    node.append(toolbar(search.node, h('span', 'kit-spacer'), count));
  }
  node.append(groupsNode);

  const groups = new Map();
  for (const permission of catalog) {
    const area = String(permission.key).split('.')[0];
    if (!groups.has(area)) groups.set(area, []);
    groups.get(area).push(permission);
  }
  const ordered = [...groups.entries()].sort(([a], [b]) => (AREAS[a] || a).localeCompare(AREAS[b] || b, 'tr'));

  function paint() {
    groupsNode.replaceChildren();
    for (const [area, permissions] of ordered) {
      const visible = permissions.filter((permission) => (readOnly ? chosen.has(permission.key) : true)
        && (!query || foldText(`${permission.key} ${permission.description}`).includes(query)));
      if (!visible.length) continue;
      const group = h('div', 'ak-stf-perm-group');
      const head = h('div', 'ak-stf-perm-head');
      const picked = permissions.filter((permission) => chosen.has(permission.key)).length;
      head.append(h('b', '', AREAS[area] || area), h('span', 'ak-muted', `${picked}/${permissions.length}`));
      if (!readOnly) {
        const all = visible.every((permission) => chosen.has(permission.key));
        head.append(h('span', 'kit-spacer'), button(all ? 'Hiçbirini seçme' : 'Tümünü seç', { variant: 'ghost',
          onClick: () => {
            for (const permission of visible) {
              if (all) chosen.delete(permission.key);
              else if (!chosen.has(permission.key)) chosen.set(permission.key, defaultScope(permission));
            }
            paint();
            emit();
          } }));
      }
      group.append(head);
      for (const permission of visible) group.append(permissionRow(permission));
      groupsNode.append(group);
    }
    if (unknown.length) {
      const group = h('div', 'ak-stf-perm-group');
      group.append(h('div', 'ak-stf-perm-head', 'Katalogda olmayan izinler (korunur)'));
      const chips = h('div', 'ak-chips');
      for (const item of unknown) chips.append(h('code', 'ak-stf-perm-chip', item));
      group.append(chips);
      groupsNode.append(group);
    }
    if (!groupsNode.childElementCount) {
      groupsNode.append(h('div', 'ak-muted', readOnly ? 'İzin yok.' : 'Aramaya uyan izin yok.'));
    }
  }

  function permissionRow(permission) {
    const row = h('label', 'ak-stf-perm');
    const check = h('input', 'kit-check');
    check.type = 'checkbox';
    check.checked = chosen.has(permission.key);
    check.disabled = readOnly;
    const text = h('div', 'ak-cell-stack');
    const title = h('span', 'ak-inline');
    title.append(h('span', '', permission.description || permission.key));
    if (permission.kmOnly) title.append(badge('Yalnız KM', 'warn'));
    text.append(title, h('code', 'ak-stf-mono', permission.key));
    row.append(check, text);
    const scopes = permission.scopes || [];
    let scopeSelect = null;
    if (scopes.length > 1) {
      scopeSelect = h('select', 'kit-select ak-stf-scope');
      scopeSelect.setAttribute('aria-label', `${permission.key} kapsamı`);
      for (const scope of scopes) {
        const option = h('option', '', SCOPES[scope] || scope);
        option.value = scope;
        scopeSelect.append(option);
      }
      scopeSelect.value = chosen.get(permission.key) || defaultScope(permission);
      scopeSelect.disabled = readOnly || !check.checked;
      scopeSelect.addEventListener('change', () => {
        chosen.set(permission.key, scopeSelect.value);
        emit();
      });
      row.append(scopeSelect);
    } else if (scopes.length === 1) {
      row.append(h('span', 'ak-muted ak-stf-scope-fixed', SCOPES[scopes[0]] || scopes[0]));
    }
    check.addEventListener('change', () => {
      if (check.checked) chosen.set(permission.key, scopeSelect?.value || defaultScope(permission));
      else chosen.delete(permission.key);
      if (scopeSelect) scopeSelect.disabled = !check.checked;
      emit();
    });
    return row;
  }

  paint();
  count.textContent = `${num(chosen.size + unknown.length)} izin seçili`;
  return { node, value: current, destroy() { search?.destroy(); } };
}
