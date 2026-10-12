// Uygulamalar sekmesi — APK yükleme, dağıtılan uygulamalar, indirme sırası.
//
// YÜKLEME PARÇALIDIR: dosya 8 MB'lık parçalar hâlinde (base64, JSON) gider;
// Cloudflare 100 MB'tan büyük gövdeyi reddeder ve kabuğun köprüsü yalnız JSON
// taşır. Parça hata alırsa birkaç kez artan beklemeyle yeniden denenir. Aynı
// dosya (ad + boyut + değiştirilme zamanı) yeniden seçilirse sunucu yarım
// yüklemeyi bulur ve kaldığı yerden sürer. Bitince sunucu paketi arka planda
// açar; ekran "işleniyor" durumunu yoklar ve hazır olunca onay penceresini açar.
//
// SUNUCUYU YORMAZ: sekme açıkken `GET /apps` indiren tablet varsa 15, yoksa 30
// saniyede bir okunur; sekme gizliyken ya da panel kapanınca durur.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_tablet/apps.js  →  shell/ui-kit/
// Kaynak modules/bbd_tablet/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, bytes, foldText, h, num, pollLoop } from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import { badge, card, drawer, emptyState, progress } from '../../ui-kit/layout.js';
import {
  busy, facts, formDialog, inline, matchesText, modal, muted, nameCell, permButton, problem,
  since, when,
} from './common.js';

const enc = encodeURIComponent;
const FAST_MS = 15_000;
const SLOW_MS = 30_000;
const CHUNK_TIMEOUT_SECONDS = 300;
const RETRY_DELAYS_MS = [2_000, 5_000, 10_000, 20_000, 30_000];
const PROCESS_POLL_MS = 2_000;
const ACCEPT = '.apk,.apks,.xapk,.apkm';

const STATUS = {
  installed: ['Kuruldu', 'good'],
  downloading: ['İndiriliyor', 'info'],
  waiting: ['Sırada', 'dim'],
  failed: ['Hata', 'bad'],
  unknown: ['Haber yok', 'dim'],
};

const DRAFT_STATE = {
  uploading: ['Yarım yükleme', 'warn'],
  processing: ['İşleniyor', 'info'],
  ready: ['Dağıtıma hazır', 'good'],
  failed: ['İşlenemedi', 'bad'],
};

/** Tabletin makine koduyla bildirdiği hata → kısa Türkçe cümle. */
export function installError(code) {
  const text = String(code || '');
  if (!text) return '';
  if (text === 'insufficient_storage') return 'Tablette yer yok';
  if (text === 'download_failed') return 'İndirme yarıda kaldı';
  if (text === 'hash_mismatch') return 'İnen dosya bozuk çıktı';
  if (text === 'incompatible_signature') return 'İmza uyuşmuyor: tabletteki uygulama başka bir imzayla kurulmuş';
  if (text === 'superseded') return 'Daha yeni sürüm geldi';
  if (text.startsWith('install_failed')) {
    // install_failed:<durum>:<ayrıntı> — Android'in kendi kodu, ham hâliyle ipucunda da durur.
    const [, status = '', ...rest] = text.split(':');
    const detail = [status, rest.join(':')].map((part) => part.trim()).filter(Boolean).join(' · ');
    return detail ? `Kurulum başarısız: ${detail}` : 'Kurulum başarısız';
  }
  return 'Kurulamadı';
}

function versionText(item) {
  if (!item) return '—';
  const name = item.versionName ? `${item.versionName} ` : '';
  return `${name}(${num(item.versionCode)})`;
}

function readBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || '');
      resolve(text.slice(text.indexOf(',') + 1));   // "data:...;base64," öneki atılır
    };
    reader.onerror = () => reject(reader.error || new Error('Dosya okunamadı.'));
    reader.readAsDataURL(blob);
  });
}

const sleep = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });

/** Sayaçlar: "Kuruldu 12/40 · İndiriliyor 2 · Sırada 5 · Hata 1 · Haber yok 20". */
function countBadges(counts) {
  const c = counts || {};
  const parts = [badge(`Kuruldu ${num(c.installed || 0)}/${num(c.total || 0)}`,
    c.total && c.installed === c.total ? 'good' : '')];
  if (c.downloading) parts.push(badge(`İndiriliyor ${num(c.downloading)}`, 'info'));
  if (c.waiting) parts.push(badge(`Sırada ${num(c.waiting)}`, 'dim'));
  if (c.failed) parts.push(badge(`Hata ${num(c.failed)}`, 'bad'));
  if (c.unknown) parts.push(badge(`Haber yok ${num(c.unknown)}`, 'dim'));
  return inline(...parts);
}

export function appsView(app) {
  const node = h('div', 'tb-pane');
  node.hidden = true;
  const st = {
    data: null, error: '', at: 0, loading: null, query: '',
    upload: null,          // süren yükleme
    current: null,         // açık çekmece
    disposed: false,
  };

  // ------------------------------------------------------------- şerit
  const picker = h('input');
  picker.type = 'file';
  picker.accept = ACCEPT;
  picker.hidden = true;
  picker.addEventListener('change', () => {
    const file = picker.files?.[0];
    picker.value = '';
    if (file) startUpload(file);
  });
  const uploadButton = permButton(app, 'APK yükle', 'manage', {
    variant: 'primary',
    title: 'APK ya da bölünmüş paket (.apks, .xapk, .apkm) seçin',
    onClick: () => {
      if (st.upload) {
        app.toast('Önce süren yükleme bitsin.', 'warn');
        return;
      }
      picker.click();
    },
  });
  const bar = filterBar({
    compact: true,
    fields: [{ kind: 'search', key: 'q', placeholder: 'Uygulama ya da paket adı ara', primary: true }],
    onChange: (values) => {
      st.query = values.q || '';
      paintList();
    },
    actions: [uploadButton],
  });

  const slotLine = h('p', 'tb-muted');
  const uploadSlot = h('div', 'tb-slot');
  const selfSlot = h('div', 'tb-slot');
  const draftSlot = h('div', 'tb-slot');
  const tableSlot = h('div', 'tb-table');
  const removedSlot = h('div', 'tb-slot');
  const scroller = h('div', 'kit-body');
  scroller.append(picker, slotLine, uploadSlot, selfSlot, draftSlot,
    card('Dağıtılan uygulamalar', tableSlot), removedSlot);
  node.append(bar.node, scroller);

  // -------------------------------------------------------------- veri
  async function load() {
    if (st.loading) return st.loading;
    st.loading = (async () => {
      try {
        st.data = await app.call('/apps');
        st.error = '';
        st.at = Date.now();
      } catch (error) {
        st.error = problem(error, 'Uygulama listesi alınamadı.');
      } finally {
        st.loading = null;
      }
      if (!st.disposed) paint();
    })();
    return st.loading;
  }

  function downloading() {
    return Boolean(st.data?.slots?.used)
      || (st.data?.apps || []).some((item) => item.counts?.downloading);
  }

  // Sekme görünürken 15 sn'de bir tik; indiren yoksa okuma 30 sn'de bir.
  const poll = pollLoop({
    every: FAST_MS,
    run: async () => {
      if (node.hidden || st.disposed) return;
      if (!downloading() && Date.now() - st.at < SLOW_MS - 1_000) return;
      await load();
      if (st.current) await loadDevices(st.current);
    },
  });

  // ------------------------------------------------------------- çizim
  function paint() {
    paintSlots();
    paintSelf();
    paintDrafts();
    paintList();
    paintRemoved();
    paintDrawer();
  }

  function paintSlots() {
    const slots = st.data?.slots;
    if (st.error && !st.data) {
      slotLine.textContent = st.error;
      return;
    }
    if (!slots) {
      slotLine.textContent = 'Yükleniyor…';
      return;
    }
    slotLine.textContent = slots.used
      ? `Şu an ${num(slots.used)}/${num(slots.total)} tablet indiriyor. Diğerleri sırada bekler.`
      : `Şu an indiren tablet yok. Aynı anda en fazla ${num(slots.total)} tablet indirir.`;
    if (st.data && st.data.storageReady === false) {
      slotLine.textContent += ' Sunucuda depolama yeri tanımlı değil; yükleme yapılamaz.';
    }
  }

  function selfApp() {
    return (st.data?.apps || []).find((item) => item.kind === 'self') || null;
  }

  function paintSelf() {
    if (!st.data) {
      selfSlot.replaceChildren();
      return;
    }
    const own = selfApp();
    const body = h('div', 'kit-card-body tb-stack');
    if (!own) {
      body.append(muted('TabletKontrol’ün yeni sürümünü (com.veykemtu.tabletkontrol) “APK yükle” ile '
        + 'yükleyip dağıtınca tabletler kendini sırayla günceller. Güncelleme her zaman sıranın önündedir.'));
    } else {
      body.append(facts([
        ['Sürüm', versionText(own)],
        ['Boyut', bytes(own.totalBytes)],
        ['Yayın', own.publishedAt ? `${when(own.publishedAt)} (${since(own.publishedAt)})` : ''],
        ['Tabletler', countBadges(own.counts)],
      ]));
      const tools = h('div', 'tb-actions');
      tools.append(button('Tablet durumları', { onClick: () => open(own.packageName) }));
      body.append(tools);
    }
    selfSlot.replaceChildren(card('Tablet uygulaması sürümü', body));
  }

  function paintDrafts() {
    const drafts = st.data?.drafts || [];
    if (!drafts.length) {
      draftSlot.replaceChildren();
      return;
    }
    const list = h('div', 'tb-cmds');
    for (const draft of drafts) {
      const row = h('div', 'tb-cmd');
      const release = draft.release;
      const [label, tone] = DRAFT_STATE[draft.state] || [draft.state, 'dim'];
      const title = release ? `${release.appName} · ${versionText(release)}` : draft.fileName;
      let detail = `${draft.fileName} · ${bytes(draft.sizeBytes)}`;
      if (draft.state === 'uploading') {
        detail += ` · ${bytes(draft.receivedBytes)} gönderildi — aynı dosyayı yeniden seçince kaldığı yerden sürer`;
      }
      if (draft.state === 'failed' && draft.error) detail = `${draft.fileName} · ${draft.error}`;
      row.append(nameCell(title, detail));
      const tools = h('div', 'tb-cmd-tools');
      tools.append(badge(label, tone));
      if (draft.state === 'ready' && app.can('manage')) {
        tools.append(button('Dağıt', { variant: 'primary', onClick: () => confirmPublish(draft) }));
      }
      if (draft.state !== 'processing' && app.can('manage')) {
        tools.append(button('At', {
          variant: 'ghost',
          title: 'Taslağı ya da yarım yüklemeyi sil',
          onClick: (event) => busy(event.currentTarget, () => discard(draft)),
        }));
      }
      row.append(tools);
      list.append(row);
    }
    const body = h('div', 'kit-card-body');
    body.append(list);
    draftSlot.replaceChildren(card('Taslaklar ve yarım yüklemeler', body,
      'İki günden eski taslaklar kendiliğinden silinir'));
  }

  function paintRemoved() {
    const removed = st.data?.removed || [];
    if (!removed.length) {
      removedSlot.replaceChildren();
      return;
    }
    removedSlot.replaceChildren(muted(`Tabletlerden kaldırılanlar: ${removed.join(', ')}. `
      + 'Yeniden dağıtılınca listeden çıkar.'));
  }

  // ------------------------------------------------------------- tablo
  const table = dataTable({
    columns: [
      {
        key: 'appName', label: 'Uygulama', width: 'minmax(0, 1.4fr)', sortable: true,
        sortValue: (row) => foldText(row.appName),
        cell: (row) => nameCell(row.appName || row.packageName, row.packageName),
      },
      { key: 'versionCode', label: 'Sürüm', width: '140px', cell: (row) => versionText(row) },
      {
        key: 'totalBytes', label: 'Boyut', width: '100px', sortable: true,
        sortValue: (row) => Number(row.totalBytes || 0), cell: (row) => bytes(row.totalBytes),
      },
      { key: 'counts', label: 'Tabletler', width: 'minmax(0, 2fr)', cell: (row) => countBadges(row.counts) },
    ],
    rows: [],
    onRow: (row) => open(row.packageName),
    rowKey: (row) => row.packageName,
    sort: { key: 'appName', dir: 'asc' },
    empty: emptyState({ title: 'Henüz dağıtılan uygulama yok' }),
  });
  tableSlot.append(table.node);

  function paintList() {
    const rows = (st.data?.apps || [])
      .filter((item) => item.kind !== 'self')
      .filter((item) => matchesText(st.query, item.appName, item.packageName));
    const empty = st.data?.apps?.some((item) => item.kind !== 'self')
      ? emptyState({ title: 'Bu aramaya uyan uygulama yok', actions: [button('Aramayı temizle', { onClick: () => bar.reset() })] })
      : emptyState({
        title: st.error && !st.data ? 'Uygulama listesi alınamadı' : 'Henüz dağıtılan uygulama yok',
        text: st.error && !st.data ? st.error
          : 'APK yükleyip dağıtın; tabletler sırayla indirip kendiliğinden kurar. Öğrencinin görmesi için '
            + 'uygulamanın profilde de izinli olması gerekir.',
      });
    table.update({ rows, empty });
  }

  // ---------------------------------------------------------- yükleme
  function paintUpload() {
    const up = st.upload;
    if (!up) {
      uploadSlot.replaceChildren();
      return;
    }
    if (!up.node) {
      up.bar = progress([]);
      up.text = h('span', 'tb-sub');
      up.text.setAttribute('aria-live', 'polite');
      up.cancel = button('İptal et', {
        variant: 'ghost',
        title: 'Yüklemeyi durdur ve sunucudaki yarım dosyayı sil',
        onClick: () => cancelUpload(),
      });
      const tools = h('div', 'tb-actions');
      tools.append(up.text, h('span', 'kit-spacer'), up.cancel);
      const body = h('div', 'kit-card-body tb-stack');
      body.append(up.bar.node, tools);
      up.node = card(`Yükleniyor: ${up.file.name}`, body);
      uploadSlot.replaceChildren(up.node);
    }
    const percent = up.size ? Math.floor((up.sent / up.size) * 100) : 0;
    if (up.phase === 'processing') {
      up.bar.percent(100, 'Sunucu paketi açıyor ve doğruluyor…');
      up.text.textContent = 'Büyük paketlerde birkaç dakika sürebilir; bu ekranı kapatsanız da iş sürer.';
      up.cancel.hidden = true;
      return;
    }
    const speed = up.speed ? ` · ${bytes(up.speed)}/sn` : '';
    up.bar.percent(percent, `${bytes(up.sent)} / ${bytes(up.size)} · %${num(percent)}${speed}`);
    up.text.textContent = up.note || '';
  }

  function measure(up) {
    const now = Date.now();
    up.samples.push([now, up.sent]);
    while (up.samples.length > 2 && now - up.samples[0][0] > 10_000) up.samples.shift();
    const [firstAt, firstSent] = up.samples[0];
    up.speed = now > firstAt ? ((up.sent - firstSent) * 1000) / (now - firstAt) : 0;
  }

  async function sendChunk(up, offset, data) {
    for (let attempt = 0; ; attempt += 1) {
      if (up.cancelled) return null;
      try {
        return await app.call(`/apps/uploads/${enc(up.id)}/chunk`, {
          method: 'PUT', body: { offset, data }, timeoutSeconds: CHUNK_TIMEOUT_SECONDS,
        });
      } catch (error) {
        if (error?.status === 409) {
          // Sunucu başka bir yerden sürmek istiyor (ör. önceki parça aslında
          // yazılmıştı); doğru konumu okuyup oradan devam edilir.
          return app.call(`/apps/uploads/${enc(up.id)}`);
        }
        const status = Number(error?.status) || 0;
        const retryable = !status || status >= 500 || status === 408 || status === 429;
        if (!retryable || attempt >= RETRY_DELAYS_MS.length) throw error;
        up.note = `Bağlantı sorunu; ${Math.round(RETRY_DELAYS_MS[attempt] / 1000)} sn sonra yeniden denenecek…`;
        paintUpload();
        await sleep(RETRY_DELAYS_MS[attempt]);
        up.note = '';
      }
    }
  }

  async function startUpload(file) {
    if (!file.size) {
      app.toast('Dosya boş.', 'bad');
      return;
    }
    const limit = Number(st.data?.maxUploadBytes || 0);
    if (limit && file.size > limit) {
      app.toast(`Dosya çok büyük; en fazla ${bytes(limit)} yüklenebilir.`, 'bad');
      return;
    }
    const up = {
      file, id: null, size: file.size, sent: 0, samples: [], speed: 0, note: '',
      phase: 'uploading', cancelled: false, node: null,
    };
    st.upload = up;
    paintUpload();
    try {
      const started = await app.call('/apps/uploads', {
        method: 'POST',
        body: { fileName: file.name, sizeBytes: file.size, fingerprint: String(file.lastModified || '') },
      });
      up.id = started.uploadId;
      up.sent = Number(started.receivedBytes || 0);
      if (started.resumed && up.sent) app.toast(`Yarım yükleme bulundu; ${bytes(up.sent)} sonrasından sürüyor.`, 'info');
      const chunk = Number(started.chunkBytes) || 8 * 1024 * 1024;
      measure(up);
      while (!up.cancelled && up.sent < up.size) {
        const end = Math.min(up.size, up.sent + chunk);
        const data = await readBase64(file.slice(up.sent, end));
        const result = await sendChunk(up, up.sent, data);
        if (!result) break;
        if (result.state && result.state !== 'uploading') break;
        up.sent = Number(result.receivedBytes || 0);
        measure(up);
        paintUpload();
      }
      if (up.cancelled) return;
      up.phase = 'processing';
      paintUpload();
      let status = await app.call(`/apps/uploads/${enc(up.id)}/finish`, { method: 'POST' });
      while (status?.state === 'processing' && !st.disposed) {
        await sleep(PROCESS_POLL_MS);
        status = await app.call(`/apps/uploads/${enc(up.id)}`);
      }
      if (st.disposed) return;
      st.upload = null;
      paintUpload();
      await load();
      if (status?.state === 'ready') confirmPublish(status);
      else if (status?.state === 'failed') app.toast(status.error || 'Paket işlenemedi.', 'bad');
    } catch (error) {
      if (st.upload === up) st.upload = null;
      paintUpload();
      app.toast(`${problem(error, 'Yükleme yapılamadı.')} Aynı dosyayı yeniden seçince kaldığı yerden sürer.`, 'bad');
      load();
    }
  }

  async function cancelUpload() {
    const up = st.upload;
    if (!up || up.phase !== 'uploading') return;
    up.cancelled = true;
    st.upload = null;
    paintUpload();
    if (up.id) {
      try {
        await app.call(`/apps/releases/${enc(up.id)}`, { method: 'DELETE' });
      } catch {
        /* yarım dosya iki gün sonra kendiliğinden silinir */
      }
    }
    app.toast('Yükleme iptal edildi.', 'info');
    load();
  }

  async function discard(draft) {
    try {
      await app.call(`/apps/releases/${enc(draft.releaseId)}`, { method: 'DELETE' });
      app.toast('Taslak silindi.', 'good');
    } catch (error) {
      app.toast(problem(error, 'Taslak silinemedi.'), 'bad');
    }
    await load();
  }

  // ---------------------------------------------------- dağıtım onayı
  function confirmPublish(status) {
    const release = status.release;
    if (!release) return;
    const { card: box, close } = modal(app.root, { title: 'Uygulama dağıtılsın mı?', closeOnOverlay: true });
    const parts = release.files?.length || 0;
    const current = release.current;
    box.append(facts([
      ['Paket', release.packageName],
      ['Sürüm', versionText(release)],
      ['Boyut', bytes(release.totalBytes)],
      ['Dosya', parts > 1 ? `Bölünmüş paket · ${num(parts)} parça` : 'Tek APK'],
      ['Mevcut sürüm', current ? `${current.appName} · ${versionText(current)}` : 'Yok (ilk dağıtım)'],
    ]));
    if (release.kind === 'self') {
      box.append(muted('Bu, tablet uygulamasının (TabletKontrol) kendisi. Tabletler bu güncellemeyi '
        + 'sıranın önüne alır ve kurulumdan sonra yeni sürümle yeniden başlar.'));
    }
    const field = h('label', 'kit-field');
    field.append(h('span', 'kit-field-label', 'Uygulama adı (panelde ve tablette görünür)'));
    const name = h('input', 'kit-input');
    name.type = 'text';
    name.maxLength = 120;
    name.value = release.appName || '';
    field.append(name);
    const fields = h('div', 'tb-dialog-fields');
    fields.append(field);
    box.append(fields);
    box.append(muted('Tabletler paketi sırayla indirip kullanıcıya sormadan kurar. Öğrencinin '
      + 'açabilmesi için uygulamanın profilde de izinli olması gerekir.'));
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    box.append(error);
    const actions = h('div', 'kit-dialog-actions');
    const confirm = button('Dağıt', {
      variant: 'primary',
      onClick: (event) => busy(event.currentTarget, async () => {
        error.textContent = '';
        try {
          await app.call(`/apps/releases/${enc(release.releaseId)}/publish`, {
            method: 'POST', body: { appName: name.value.trim() },
          });
          close(true);
          app.toast(`${name.value.trim() || release.appName} dağıtıma çıktı.`, 'good');
          await load();
          app.forgetManagedApps?.();
        } catch (failure) {
          error.textContent = problem(failure, 'Dağıtılamadı. Tekrar deneyin.');
        }
      }),
    });
    actions.append(button('Vazgeç', { onClick: () => close(null) }), confirm);
    box.append(actions);
    name.focus();
  }

  // ----------------------------------------------------------- çekmece
  function open(packageName) {
    st.current?.drawer.close();
    const item = (st.data?.apps || []).find((entry) => entry.packageName === packageName);
    if (!item) return;
    const ctl = {
      packageName,
      data: null,
      error: '',
      head: h('div', 'tb-slot'),
      list: h('div', 'tb-table'),
      foot: h('div', 'tb-slot'),
    };
    ctl.drawer = drawer(app.root, {
      title: item.appName || packageName,
      subtitle: `${packageName} · ${versionText(item)}`,
      onClose: () => { if (st.current === ctl) st.current = null; },
    });
    ctl.drawer.node.classList.add('tb-drawer');
    ctl.subtitle = ctl.drawer.node.querySelector('.kit-drawer-sub');
    ctl.table = dataTable({
      columns: [
        { key: 'deviceName', label: 'Tablet', width: 'minmax(0, 1.2fr)', sortable: true,
          sortValue: (row) => foldText(row.deviceName), cell: (row) => row.deviceName },
        { key: 'status', label: 'Durum', width: '130px', sortable: true,
          sortValue: (row) => Object.keys(STATUS).indexOf(row.status),
          cell: (row) => {
            const [text, tone] = STATUS[row.status] || ['Bilinmiyor', 'dim'];
            return inline(badge(text, tone), row.priority && row.status !== 'installed' ? badge('Öne alındı', 'info') : null);
          } },
        { key: 'position', label: 'Sıra', width: '60px', sortable: true,
          sortValue: (row) => row.position ?? 9999, cell: (row) => (row.position ? `${num(row.position)}.` : '—') },
        { key: 'progressPercent', label: 'İlerleme', width: '80px',
          cell: (row) => (row.progressPercent === null || row.progressPercent === undefined ? '—' : `%${num(row.progressPercent)}`) },
        { key: 'lastError', label: 'Son hata', width: 'minmax(0, 1.4fr)',
          cell: (row) => {
            if (!row.lastError || row.status === 'installed') return '—';
            const text = h('span', null, installError(row.lastError));
            text.title = `${row.lastError}${row.errorAt ? ` · ${when(row.errorAt)}` : ''}`;
            return text;
          } },
        { key: 'actions', label: '', width: '90px',
          cell: (row) => (['waiting', 'failed', 'unknown'].includes(row.status) && app.can('manage')
            ? button('Öne al', {
              variant: 'ghost',
              title: 'Bu tableti bu uygulama için sıranın önüne al',
              onClick: (event) => {
                event.stopPropagation();
                busy(event.currentTarget, () => prioritize(ctl, row));
              },
            }) : '') },
      ],
      rows: [],
      rowKey: (row) => row.deviceId,
      sort: { key: 'deviceName', dir: 'asc' },
      empty: emptyState({ title: 'Eşli tablet yok' }),
    });
    ctl.list.append(ctl.table.node);
    ctl.drawer.body.append(ctl.head, card('Tabletler', ctl.list), ctl.foot);
    st.current = ctl;
    paintDrawer();
    loadDevices(ctl);
  }

  async function loadDevices(ctl) {
    try {
      ctl.data = await app.call(`/apps/${enc(ctl.packageName)}/devices`);
      ctl.error = '';
    } catch (error) {
      ctl.error = error?.status === 404 ? 'Bu uygulamanın dağıtımı bitmiş.'
        : problem(error, 'Tablet durumları alınamadı.');
    }
    if (st.current === ctl) paintDrawer();
  }

  function paintDrawer() {
    const ctl = st.current;
    if (!ctl) return;
    const item = (st.data?.apps || []).find((entry) => entry.packageName === ctl.packageName);
    if (!item) {
      ctl.drawer.close();
      return;
    }
    ctl.drawer.setTitle(item.appName || item.packageName);
    if (ctl.subtitle) ctl.subtitle.textContent = `${item.packageName} · ${versionText(item)}`;
    const body = h('div', 'kit-card-body tb-stack');
    body.append(facts([
      ['Paket', item.packageName],
      ['Sürüm', versionText(item)],
      ['Boyut', `${bytes(item.totalBytes)}${item.files?.length > 1 ? ` · ${num(item.files.length)} parça` : ''}`],
      ['Yayın', item.publishedAt ? when(item.publishedAt) : ''],
      ['Tabletler', countBadges(item.counts)],
      ['İndirme yuvası', ctl.data?.slots ? `${num(ctl.data.slots.used)}/${num(ctl.data.slots.total)} dolu` : ''],
    ]));
    if (ctl.error) body.append(muted(ctl.error));
    ctl.head.replaceChildren(card('Yayın', body));
    ctl.table.update({ rows: ctl.data?.devices || [] });

    const danger = h('div', 'kit-card-body tb-danger');
    if (item.kind === 'self') {
      danger.append(muted('Tablet uygulamasının dağıtımı bitirilemez; yalnız daha yeni bir sürümü yüklenebilir.'));
    } else {
      danger.append(muted('Dağıtımı bitirince tabletler bu uygulamayı artık indirmez. İsterseniz '
        + 'tabletlerden de sessizce kaldırılır.'),
      permButton(app, 'Dağıtımı bitir', 'manage', {
        variant: 'danger', onClick: (event) => busy(event.currentTarget, () => finish(ctl, item)),
      }));
    }
    ctl.foot.replaceChildren(card(null, danger));
  }

  async function prioritize(ctl, row) {
    try {
      await app.call(`/apps/${enc(ctl.packageName)}/devices/${enc(row.deviceId)}/prioritize`, { method: 'POST' });
      app.toast(`${row.deviceName} sıranın önüne alındı; tablet bir sonraki bağlantıda indirmeye başlar.`, 'good');
    } catch (error) {
      app.toast(problem(error, 'Sıra değiştirilemedi.'), 'bad');
    }
    await load();
    await loadDevices(ctl);
  }

  async function finish(ctl, item) {
    const answer = await formDialog(app.root, {
      title: `“${item.appName}” dağıtımı bitirilsin mi?`,
      description: 'Tabletler bu uygulamayı artık indirmez; sunucudaki dosyası silinir. '
        + 'Onaylamak için PIN’inizi girin.',
      fields: [
        { key: 'uninstall', label: 'Tabletlerdeki kurulu uygulama', type: 'select', value: 'keep',
          options: [{ value: 'keep', label: 'Tabletlerde kalsın' },
            { value: 'remove', label: 'Tabletlerden de kaldırılsın' }] },
        { key: 'pin', label: 'PIN', type: 'pin', required: true, minLength: 4 },
      ],
      confirmLabel: 'Dağıtımı bitir',
      danger: true,
    });
    if (!answer) return;
    try {
      await app.call(`/apps/${enc(item.packageName)}`, {
        method: 'DELETE', body: { pin: answer.pin, uninstall: answer.uninstall === 'remove' },
      });
      ctl.drawer.close();
      app.toast(answer.uninstall === 'remove'
        ? 'Dağıtım bitti; tabletler uygulamayı bir sonraki bağlantıda kaldırır.'
        : 'Dağıtım bitti.', 'good');
      app.forgetManagedApps?.();
    } catch (error) {
      app.toast(problem(error, 'Dağıtım bitirilemedi. Tekrar deneyin.'), 'bad');
    }
    await load();
  }

  // ------------------------------------------------------------- dış yüz
  return {
    node,
    shown() {
      if (!st.data || Date.now() - st.at > FAST_MS) load();
      else paint();
    },
    update() {
      // Özet yoklaması bu sekmenin verisini taşımaz; kendi yoklaması var.
    },
    destroy() {
      st.disposed = true;
      poll.stop();
      bar.destroy();
      st.current?.drawer.close();
      if (st.upload) st.upload.cancelled = true;
    },
  };
}
