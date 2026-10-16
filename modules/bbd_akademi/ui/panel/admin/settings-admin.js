// BBD Akademi — Ayarlar › Entegrasyon anahtarları, Dizin eşitleme, Denetim kaydı.
//
// ENTEGRASYON ANAHTARI: tam anahtar (`aksk_<önek>_<sır>`) YALNIZ oluşturma
// yanıtında bir kez gelir. Panel onu tek bir pencerede gösterir (kopyala
// düğmesiyle) ve pencere kapanınca alanı boşaltır; hiçbir yere yazmaz,
// bildirimde göstermez. KM'nin kendisi de Akademi'ye bu anahtarlardan biriyle
// bağlanır — hangisi olduğunu panel bilemez, bu yüzden iptal penceresi genel
// uyarı verir (Akademi isteği yapan anahtarın kendini iptal etmesini zaten
// `cannot_revoke_self` ile reddeder).
//
// DİZİN EŞİTLEME: "Şimdi eşitle" işi sıraya koyar (202); durum, iş sürerken
// birkaç saniyede bir yoklanır. Sekme gizlenince ya da ekran kapanınca
// yoklama durur.
//
// DENETİM KAYDI: imleçli liste; satırdaki önce/sonra değerleri metin olarak
// (alan → önce → sonra) çizilir, HTML olarak değil. Dışa aktarım ortak
// `exportJob` ile (202 iş → yokla → indir).

import { button, copyText, h, menuButton } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { dateRange } from '../../../ui-kit/datefield.js';
import { AkademiError } from '../api.js';
import {
  actionButton, confirmAction, empty, exportJob, fmtDateTime, fmtTime, formDialog, kv, loader, num,
  openDrawer, optionsOf, pagedList, runAction, searchBox, selectBox, statusBadge, studentPicker,
  todayIst, toolbar,
} from '../common.js';
import { permissionChecklist } from './staff.js';

// ------------------------------------------------------- entegrasyon anahtarı

const PRESETS = { km_panel: 'KM paneli (KM için tanımlı tam küme)', custom: 'Özel izinler' };
const CIDR = /^(\d{1,3}(\.\d{1,3}){3}\/\d{1,2}|[0-9A-Fa-f:]+\/\d{1,3})$/;

function parseCidrs(text) {
  return String(text || '').split(/[\s,;]+/).map((item) => item.trim()).filter(Boolean);
}

export function keysTab(app) {
  const node = h('div', 'ak-stack');
  let rows = [];
  let showRevoked = false;

  node.append(alertBox('Kontrol Merkezi Akademi\'ye bu anahtarlardan biriyle bağlanır (sır kasasında '
    + '“bbd_akademi.api_key”). O anahtar iptal edilirse Akademi ekranları ve personel eşitlemesi durur. '
    + 'Anahtar yenilerken önce yenisini oluşturup sır kasasına yazın, sonra eskisini iptal edin.', 'warn'));

  const revokedToggle = h('label', 'ak-inline ak-set-toggle');
  const revokedCheck = h('input', 'kit-check');
  revokedCheck.type = 'checkbox';
  revokedCheck.addEventListener('change', () => { showRevoked = revokedCheck.checked; paint(); });
  revokedToggle.append(revokedCheck, h('span', '', 'İptal edilenleri de göster'));
  const add = actionButton(app, 'admin_createIntegrationKey', 'Yeni anahtar', { variant: 'primary',
    onClick: () => create() });
  node.append(toolbar(revokedToggle, h('span', 'kit-spacer'), add));

  const table = dataTable({
    columns: [
      { key: 'name', label: 'Anahtar', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.name), h('code', 'ak-set-mono', `aksk_${row.prefix}_…`));
        return cell;
      } },
      { key: 'permissions', label: 'İzin', align: 'num', cell: (row) => {
        const text = h('span', '', `${num((row.permissions || []).length)} izin`);
        text.title = (row.permissions || []).join('\n');
        return text;
      } },
      { key: 'cidrs', label: 'IP aralığı', width: 'minmax(0, 1.2fr)', cell: (row) => ((row.allowedCidrs || []).length
        ? row.allowedCidrs.join(', ') : 'Her adresten') },
      { key: 'created', label: 'Oluşturma', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'used', label: 'Son kullanım', width: '130px', cell: (row) => (row.lastUsedAt
        ? fmtDateTime(row.lastUsedAt) : 'Kullanılmadı') },
      { key: 'status', label: 'Durum', cell: (row) => (row.revokedAt
        ? badge(`İptal · ${fmtDateTime(row.revokedAt).slice(0, 10)}`, 'dim') : badge('Etkin', 'good')) },
      { key: 'actions', label: '', width: '170px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        actions.append(button('Ayrıntı', { variant: 'ghost', onClick: () => keyDrawer(app, row) }));
        if (!row.revokedAt) {
          const revokeButton = actionButton(app, 'admin_revokeIntegrationKey', 'İptal et', { variant: 'ghost',
            onClick: () => revoke(row, revokeButton) });
          actions.append(revokeButton);
        }
        return actions;
      } },
    ],
    rows: [],
    empty: empty('Anahtar yok', 'İlk anahtar Akademi kurulumunda komut satırıyla üretilir.'),
  });
  const view = loader(() => app.api.get('/v1/admin/integration-keys'), (data) => {
    rows = data?.items || [];
    paint();
    return table.node;
  });
  node.append(view.node);

  function paint() {
    table.update({ rows: showRevoked ? rows : rows.filter((row) => !row.revokedAt) });
  }

  async function create() {
    let catalog = null;
    try {
      catalog = (await app.api.getCached('/v1/admin/permissions', undefined, { ttlMs: 10 * 60_000 }))?.items
        || null;
    } catch {
      catalog = null;           // katalog okunamazsa izinler elle yazılır
    }
    const result = await formDialog(app, {
      title: 'Yeni entegrasyon anahtarı',
      description: 'Anahtar yalnız oluşturulduğu anda bir kez gösterilir. Bir anahtar kendi izinlerinden geniş '
        + 'bir anahtar üretemez.',
      wide: true,
      // Özel alan (izinler) `value`ya KONMAZ: formDialog onu formGrid'e de verir ve
      // her alan değişikliğinde seçimi ilk değerle ezer; değer render içinde tohumlanır.
      value: { name: '', preset: 'km_panel', cidrs: '' },
      fields: [
        { key: 'name', label: 'Ad', required: true, maxLength: 60, wide: true,
          placeholder: 'KM paneli — 2026 güz' },
        { key: 'preset', label: 'İzin kümesi', type: 'select', options: optionsOf(PRESETS) },
        { key: 'cidrs', label: 'İzinli IP aralıkları', type: 'textarea', maxLength: 600,
          placeholder: '203.0.113.0/24',
          hint: 'Her satıra bir CIDR. Boş bırakılırsa anahtar her adresten kullanılabilir.',
          validate: (text) => {
            const bad = parseCidrs(text).filter((item) => !CIDR.test(item));
            return bad.length ? `Geçersiz aralık: ${bad.slice(0, 3).join(', ')}` : null;
          } },
        { key: 'permissions', label: 'İzinler', type: 'custom', wide: true,
          render: (draft, set) => permissionsField(catalog, draft, set) },
      ],
      submitLabel: 'Anahtarı oluştur',
      onSubmit: (draft, { key }) => {
        const custom = draft.preset === 'custom';
        const permissions = custom ? (draft.permissions || []) : undefined;
        if (custom && !permissions.length) {
          throw new AkademiError({ message: 'Özel kümede en az bir izin seçin.' });
        }
        return app.api.post('/v1/admin/integration-keys', {
          name: String(draft.name || '').trim(),
          preset: draft.preset || 'km_panel',
          permissions,
          allowedCidrs: parseCidrs(draft.cidrs),
        }, { key });
      },
    });
    if (!result || typeof result !== 'object') return;
    await secretDialog(app, result);
    result.secret = '';
    view.reload();
  }

  async function revoke(row, control) {
    const ok = await confirmAction(app, {
      title: `“${row.name}” anahtarı iptal edilsin mi?`,
      description: `aksk_${row.prefix}_… ile başlayan anahtar hemen geçersiz olur ve bu işlem geri alınamaz. `
        + 'Bu anahtarı kullanan sistem Akademi\'ye bağlanamaz. Kontrol Merkezi\'nin kullandığı anahtarsa '
        + 'Akademi ekranları ve personel eşitlemesi durur.',
      confirmLabel: 'Anahtarı iptal et',
      danger: true,
    });
    if (!ok) return;
    const next = await runAction(app, () => app.api.post(`/v1/admin/integration-keys/${row.id}/revoke`,
      undefined, { key: `revoke-key-${row.id}` }), { success: 'Anahtar iptal edildi.', button: control });
    if (next) {
      rows = rows.map((item) => (item.id === row.id ? { ...item, ...next } : item));
      paint();
    }
  }

  return { node, reload: () => view.reload() };
}

/** "Özel izinler" seçilince izin listesi; katalog okunamazsa elle yazım. */
function permissionsField(catalog, draft, set) {
  const wrap = h('div', 'ak-stack');
  set([]);
  let shown = null;
  let checklist = null;
  const update = (current) => {
    const custom = current.preset === 'custom';
    if (custom === shown) return;
    shown = custom;
    checklist?.destroy();
    checklist = null;
    wrap.replaceChildren();
    if (!custom) {
      wrap.append(h('span', 'ak-muted', 'KM paneli kümesi, Akademi\'nin Kontrol Merkezi için tanımladığı '
        + 'izinlerin tamamıdır; ayrıca izin seçilmez.'));
      return;
    }
    // Katalog okunamadıysa (Roller yetkisi yok) liste elle yazıma düşer.
    checklist = permissionChecklist({ catalog: catalog || [], value: current.permissions || [],
      onChange: (list) => set(list) });
    wrap.append(checklist.node);
  };
  update(draft);
  return { node: wrap, update };
}

function keyDrawer(app, row) {
  const panel = openDrawer(app, { title: row.name, subtitle: `aksk_${row.prefix}_…` });
  panel.body.append(card('', kv([
    ['Durum', row.revokedAt ? `İptal edildi · ${fmtDateTime(row.revokedAt)}` : 'Etkin'],
    ['Oluşturma', fmtDateTime(row.createdAt)],
    ['Son kullanım', row.lastUsedAt ? fmtDateTime(row.lastUsedAt) : 'Kullanılmadı'],
    ['IP aralığı', (row.allowedCidrs || []).length ? row.allowedCidrs.join(', ') : 'Her adresten'],
  ])));
  const list = h('div', 'ak-chips ak-card-pad');
  for (const permission of row.permissions || []) list.append(h('code', 'ak-set-perm', permission));
  if (!(row.permissions || []).length) list.append(h('span', 'ak-muted', 'İzin yok.'));
  panel.body.append(card(`İzinler (${num((row.permissions || []).length)})`, list));
}

/**
 * Tam anahtarı BİR KEZ gösteren pencere. Dışarı tıklama ve Esc kapatmaz
 * (yanlışlıkla kaybolmasın); kapanınca alan boşaltılır.
 */
function secretDialog(app, result) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog ak-dialog ak-set-secret');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.append(h('h3', 'kit-dialog-title', 'Anahtar oluşturuldu'));
    box.append(alertBox('Bu anahtar YALNIZ ŞİMDİ gösterilir; Akademi onu saklamaz, bir daha göremezsiniz. '
      + 'Kopyalayıp KM sır kasasına ya da kullanacak sistemin güvenli ayarına yazın. Kaybederseniz anahtarı '
      + 'iptal edip yenisini oluşturmanız gerekir.', 'warn'));
    const key = result.key || {};
    box.append(kv([
      ['Ad', key.name],
      ['Önek', key.prefix ? `aksk_${key.prefix}_…` : '—'],
      ['İzin', `${num((key.permissions || []).length)} izin`],
      ['IP aralığı', (key.allowedCidrs || []).length ? key.allowedCidrs.join(', ') : 'Her adresten'],
    ]));
    const field = h('input', 'kit-input ak-set-secret-value');
    field.type = 'text';
    field.readOnly = true;
    field.spellcheck = false;
    field.autocomplete = 'off';
    field.value = result.secret || '';
    field.setAttribute('aria-label', 'Tam entegrasyon anahtarı');
    field.addEventListener('focus', () => field.select());
    let copied = false;
    const copy = button('Kopyala', { onClick: async () => {
      if (await copyText(field.value)) {
        copied = true;
        copy.textContent = 'Kopyalandı';
        app.toast('Anahtar panoya kopyalandı.', 'good');
      } else {
        field.focus();
        field.select();
        app.toast('Panoya kopyalanamadı; seçili metni Ctrl+C ile kopyalayın.', 'warn');
      }
    } });
    const row = h('div', 'ak-set-secret-row');
    row.append(field, copy);
    box.append(row);
    const close = async () => {
      if (!copied) {
        const sure = await confirmAction(app, { title: 'Anahtarı kopyalamadınız',
          description: 'Pencere kapanınca anahtar bir daha gösterilemez. Yine de kapatılsın mı?',
          confirmLabel: 'Kapat', danger: true });
        if (!sure) return;
      }
      field.value = '';
      overlay.remove();
      resolve();
    };
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Kaydettim, kapat', { variant: 'primary', onClick: () => close() }));
    box.append(actions);
    overlay.append(box);
    app.root.append(overlay);
    field.focus();
  });
}

// ------------------------------------------------------------- dizin eşitleme

const DIRECTORY_STATUS = { idle: ['Boşta', 'dim'], running: ['Eşitleniyor', 'info'],
  failed: ['Son eşitleme başarısız', 'bad'] };
const POLL_MS = 4000;
const POLL_MAX = 45;            // ~3 dk

export function directoryTab(app) {
  const node = h('div', 'ak-stack');
  let timer = null;
  let polls = 0;
  let pollWanted = false;
  let visible = false;
  let lastRun = null;
  let lastRunAtStart = null;
  let sawRunning = false;

  const syncNow = actionButton(app, 'admin_triggerDirectorySync', 'Şimdi eşitle', { variant: 'primary',
    onClick: () => trigger(false, syncNow) });
  const syncFull = actionButton(app, 'admin_triggerDirectorySync', 'Tam eşitleme', {
    onClick: () => trigger(true, syncFull) });
  node.append(toolbar(h('span', 'ak-muted', 'Öğrenci ve veli dizini KM\'den Akademi\'ye eşitlenir; '
    + 'zamanlanmış görev bunu kendiliğinden yapar.'), h('span', 'kit-spacer'), syncFull, syncNow));

  const view = loader(() => app.api.get('/v1/admin/directory/sync-status'), (status) => {
    lastRun = status?.lastRunAt || null;
    return render(status || {});
  }, { rows: 4, columns: 4 });
  node.append(view.node);

  function render(status) {
    const counts = status.counts || {};
    const nodes = [];
    if (status.status === 'failed') {
      nodes.push(alertBox(`Son eşitleme başarısız${status.lastErrorCode ? ` (${status.lastErrorCode})` : ''}. `
        + 'Sebep giderilince “Şimdi eşitle” ile tekrar deneyin.', 'bad'));
    }
    if (status.status === 'running') nodes.push(alertBox('Eşitleme sürüyor; durum birkaç saniyede bir tazelenir.', 'info'));
    if (status.complete === false) {
      nodes.push(alertBox('Son eşitleme eksik tamamlandı; güvenlik için hiçbir kayıt “eksik” işaretlenmedi '
        + 'ya da kapatılmadı.', 'warn'));
    }
    nodes.push(kpiRow([
      { label: 'Etkin öğrenci', value: num(counts.active ?? 0) },
      { label: 'Pasif öğrenci', value: num(counts.inactive ?? 0),
        title: 'KM\'de pasifleştirilen öğrenciler (ayrıldı, mezun, durduruldu); Öğrenciler bölümünde “Pasif” süzgeciyle salt okunur görünür.' },
      { label: 'Dizinde görünmeyen', value: num(counts.missing ?? 0), tone: counts.missing ? 'warn' : '',
        title: 'KM dizininde art arda iki tam eşitlemede görünmeyen öğrenciler.' },
      { label: 'Veli', value: num(counts.guardians ?? 0) },
      { label: 'Telefon sorunu', value: num(counts.phoneIssues ?? 0), tone: counts.phoneIssues ? 'bad' : '',
        title: 'Geçersiz ya da çakışan veli telefonu; KM Öğrenciler ekranından düzeltilir.' },
    ]));
    nodes.push(card('Eşitleme durumu', kv([
      ['Durum', statusBadge(DIRECTORY_STATUS, status.status)],
      ['Son çalışma', fmtDateTime(status.lastRunAt)],
      ['Son başarılı', fmtDateTime(status.lastSuccessAt)],
      ['Son tam eşitleme', fmtDateTime(status.lastFullAt)],
      ['Eksiksiz', status.complete === null || status.complete === undefined ? '—'
        : status.complete ? 'Evet' : 'Hayır — silme / eksik işaretleme yapılmadı'],
      ['Son hata', status.lastErrorCode || '—'],
      ['Sonraki çalışma', fmtDateTime(status.nextRunAt)],
    ])));
    // Yoklama: iş sürerken sürer. "Bitti" ancak işin koştuğu görüldüyse ya da
    // yeni bir çalışma kaydı düştüyse denir; süre dolarsa sessizce durur.
    if (pollWanted) {
      if (status.status === 'running') sawRunning = true;
      const newRun = Boolean(status.lastRunAt) && status.lastRunAt !== lastRunAtStart;
      const finished = status.status !== 'running' && (sawRunning || newRun);
      if (finished) {
        pollWanted = false;
        app.toast(status.status === 'failed' ? 'Dizin eşitlemesi başarısız oldu.' : 'Dizin eşitlemesi tamamlandı.',
          status.status === 'failed' ? 'bad' : 'good');
      } else if (polls >= POLL_MAX) {
        pollWanted = false;
      } else {
        schedule();
      }
    }
    return nodes;
  }

  function schedule() {
    clearTimeout(timer);
    if (!pollWanted || !visible) return;
    timer = setTimeout(() => {
      timer = null;
      if (document.hidden) { schedule(); return; }
      polls += 1;
      view.reload();
    }, POLL_MS);
  }

  async function trigger(full, control) {
    if (full) {
      const ok = await confirmAction(app, { title: 'Tam eşitleme başlatılsın mı?',
        description: 'Bütün dizin baştan karşılaştırılır; birkaç dakika sürebilir. KM\'de art arda iki tam '
          + 'eşitlemede görünmeyen öğrenciler “eksik” işaretlenir.', confirmLabel: 'Başlat' });
      if (!ok) return;
    }
    const job = await runAction(app, () => app.api.post('/v1/admin/directory/sync', { full }),
      { button: control });
    if (!job) return;
    app.toast(`Eşitleme sıraya alındı (${fmtTime(job.queuedAt)}).`, 'good');
    lastRunAtStart = lastRun;
    sawRunning = false;
    polls = 0;
    pollWanted = true;
    schedule();
  }

  return {
    node,
    reload: () => view.reload(),
    shown() { visible = true; if (pollWanted && !timer) schedule(); },
    hidden() { visible = false; clearTimeout(timer); timer = null; },
    destroy() { pollWanted = false; clearTimeout(timer); timer = null; },
  };
}

// --------------------------------------------------------------- denetim kaydı

const ACTOR_KINDS = { person: 'Personel / veli', student: 'Öğrenci', integration: 'Entegrasyon (KM)',
  system: 'Sistem' };

export function auditTab(app) {
  const node = h('div', 'ak-stack');
  const filters = { from: todayIst(-6), to: todayIst(), actorKind: '', action: '', entityType: '',
    studentId: '' };

  const range = dateRange({ start: filters.from, end: filters.to, label: 'Tarih', onChange: (value) => {
    filters.from = value.start || filters.from;
    filters.to = value.end || filters.to;
    list.reload();
  } });
  const actor = selectBox(optionsOf(ACTOR_KINDS, { empty: 'Herkes' }), '', (value) => {
    filters.actorKind = value;
    list.reload();
  }, { label: 'Yapan' });
  const action = searchBox({ placeholder: 'İşlem (ör. finance.)', onSearch: (value) => {
    filters.action = value;
    list.reload();
  } });
  const entity = searchBox({ placeholder: 'Kayıt türü (ör. student)', onSearch: (value) => {
    filters.entityType = value;
    list.reload();
  } });
  const studentSlot = h('div', 'ak-inline');
  const picker = studentPicker(app, { placeholder: 'Öğrenciye göre süz', onPick: (student) => {
    filters.studentId = student.id;
    paintStudent(student);
    list.reload();
  } });
  function paintStudent(student) {
    studentSlot.replaceChildren();
    if (!student) { studentSlot.append(picker.node); return; }
    const chip = h('button', 'kit-chip on', `${student.displayName || 'Öğrenci'} ✕`);
    chip.type = 'button';
    chip.title = 'Öğrenci süzgecini kaldır';
    chip.addEventListener('click', () => {
      filters.studentId = '';
      paintStudent(null);
      list.reload();
    });
    studentSlot.append(chip);
  }
  paintStudent(null);

  const exporter = app.can('admin_exportAuditLog')
    ? menuButton('Dışa aktar', [
      { label: 'Excel (XLSX)', onClick: () => exportLog('xlsx') },
      { label: 'CSV', onClick: () => exportLog('csv') },
    ], { title: 'Dosyaya tarih aralığı ve “Yapan” süzgeci uygulanır.' })
    : actionButton(app, 'admin_exportAuditLog', 'Dışa aktar');

  node.append(toolbar(range.node), toolbar(actor.node, action.node, entity.node, studentSlot,
    h('span', 'kit-spacer'), exporter));

  const list = pagedList({
    app,
    path: '/v1/admin/audit-log',
    limit: 50,
    query: () => ({
      from: filters.from,
      to: filters.to,
      actorKind: filters.actorKind || undefined,
      action: filters.action || undefined,
      entityType: filters.entityType || undefined,
      studentId: filters.studentId || undefined,
    }),
    columns: [
      { key: 'at', label: 'Zaman', width: '130px', cell: (row) => fmtDateTime(row.at) },
      { key: 'actor', label: 'Yapan', width: 'minmax(0, 1.2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.actor?.label || '—'),
          h('small', '', ACTOR_KINDS[row.actor?.kind] || row.actor?.kind || ''));
        return cell;
      } },
      { key: 'action', label: 'İşlem', width: 'minmax(0, 1.1fr)', cell: (row) => h('code', 'ak-set-mono', row.action) },
      { key: 'summary', label: 'Özet', width: 'minmax(0, 2.4fr)' },
      { key: 'entity', label: 'Kayıt', width: 'minmax(0, 1fr)', cell: (row) => [row.entityType,
        row.entityId ? String(row.entityId).slice(0, 8) : ''].filter(Boolean).join(' · ') || '—' },
    ],
    onRow: (row) => auditDrawer(app, row),
    empty: { title: 'Bu süzgece uyan kayıt yok', text: 'Tarih aralığını genişletin ya da süzgeçleri temizleyin.' },
  });
  node.append(list.node);

  async function exportLog(format) {
    await exportJob(app, () => app.api.post('/v1/admin/audit-log/exports', {
      from: filters.from, to: filters.to, format, actorKind: filters.actorKind || undefined,
    }), { name: `denetim-kaydi-${filters.from}-${filters.to}`, label: 'Denetim kaydı hazırlanıyor…' });
  }

  return {
    node,
    reload: () => list.reload(),
    destroy() {
      range.destroy();
      action.destroy();
      entity.destroy();
      picker.destroy();
      list.destroy();
    },
  };
}

function auditDrawer(app, row) {
  const panel = openDrawer(app, { title: row.summary || row.action, subtitle: fmtDateTime(row.at) });
  panel.node.classList.add('ak-set-drawer');
  panel.body.append(card('', kv([
    ['Zaman', fmtDateTime(row.at)],
    ['Yapan', `${row.actor?.label || '—'} · ${ACTOR_KINDS[row.actor?.kind] || row.actor?.kind || ''}`],
    ['İşlem', h('code', 'ak-set-mono', row.action || '—')],
    ['Kayıt', `${row.entityType || '—'}${row.entityId ? ` · ${row.entityId}` : ''}`],
    ['Öğrenci', row.studentId || '—'],
    ['İstek kimliği', row.requestId ? h('code', 'ak-set-mono', row.requestId) : '—'],
    ['Özet', row.summary || '—'],
  ])));
  const before = row.changes?.before ?? null;
  const after = row.changes?.after ?? null;
  const diff = diffRows(before, after);
  if (!diff.length) {
    panel.body.append(h('div', 'ak-muted ak-card-pad', 'Bu kayıtta alan değişikliği yok.'));
    return;
  }
  const caption = before === null ? 'Oluşturulan değerler' : after === null ? 'Silinen / kapatılan değerler'
    : 'Değişen alanlar';
  panel.body.append(card(caption, dataTable({
    dense: true,
    rows: diff,
    rowKey: (item) => item.key,
    columns: [
      { key: 'key', label: 'Alan', width: 'minmax(0, 1.2fr)', cell: (item) => h('code', 'ak-set-mono', item.key) },
      { key: 'before', label: 'Önce', width: 'minmax(0, 1.4fr)', cell: (item) => valueCell(item.before, 'before') },
      { key: 'after', label: 'Sonra', width: 'minmax(0, 1.4fr)', cell: (item) => valueCell(item.after, 'after') },
    ],
  }).node));
}

function valueCell(value, side) {
  const cell = h('span', `ak-set-diff ak-set-diff-${side}`, value === undefined ? '—' : value);
  return cell;
}

/** {a: {b: 1}} → [['a.b', '1']] — iç içe nesneyi düz alan listesine indirir. */
function flatPairs(value, prefix = '', out = new Map(), depth = 0) {
  if (value === null || value === undefined) {
    if (prefix) out.set(prefix, '—');
    return out;
  }
  if (typeof value === 'object' && !Array.isArray(value) && depth < 5) {
    const entries = Object.entries(value);
    if (!entries.length && prefix) out.set(prefix, '{}');
    for (const [key, item] of entries) flatPairs(item, prefix ? `${prefix}.${key}` : key, out, depth + 1);
    return out;
  }
  if (Array.isArray(value)) {
    out.set(prefix, value.every((item) => item === null || typeof item !== 'object')
      ? value.map((item) => String(item)).join(', ') || '[]' : JSON.stringify(value));
    return out;
  }
  out.set(prefix, typeof value === 'object' ? JSON.stringify(value) : String(value));
  return out;
}

function diffRows(before, after) {
  const left = flatPairs(before);
  const right = flatPairs(after);
  const keys = [...new Set([...left.keys(), ...right.keys()])].filter(Boolean);
  return keys
    .map((key) => ({ key, before: left.get(key), after: right.get(key) }))
    .filter((item) => item.before !== item.after);
}
