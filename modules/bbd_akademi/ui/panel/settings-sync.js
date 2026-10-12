// BBD Akademi — Ayarlar › Senkron (km-merkezi-senkron §8).
//
// KM tek merkezdir: öğrenci, veli, kantin kontrolü, tablet ve zil değişiklikleri
// KM'nin kalıcı olay kuyruğuna (outbox) yazılır, geçit (`mobile_gateway`) bunları
// imzalı webhook ile Akademi'ye iter. Bu kart iki ucu YAN YANA gösterir:
//
//   · KM → Akademi (geçit): `GET /api/bbd_akademi/km/events` — geçidin
//     `mobile_gateway.events.status()` + ölü mektuplar. Kapısı geçidin kendi
//     iznidir (`mobile_gateway.events.view`; yeniden gönderme `.manage`).
//     Geçit bu KM'de yoksa ya da eski sürümse kart bunu söyler.
//   · Akademi alıcısı: `admin_getDirectorySyncStatus` yanıtının `events` bloğu
//     (vekil, `bbd_akademi.settings`).
//
// RENK: iki uç arası `seq` farkı > 100 ya da gecikme > 60 sn → sarı; iletim
// durduysa (`stopped`) ya da Akademi'ye 6 saattir ulaşılamıyorsa → kırmızı.
// İletim ayardan kapalıysa (varsayılan) gri: Akademi 2 dk yoklamayla çalışır,
// hiçbir şey kırılmaz, yalnız gecikme artar.
//
// HIZ: sekme görünürken 20 sn'de bir iki okuma (biri KM'nin kendisi); sekme
// gizlenince ya da pencere arka plandayken durur.

import { blockedButton, button, h } from '../../ui-kit/kit.js';
import { alertBox, card, kpiRow } from '../../ui-kit/layout.js';
import { dataTable } from '../../ui-kit/table.js';
import { confirmAction, empty, fmtDateTime, kv, loader, num, problem, statusBadge, toolbar } from './common.js';
import {
  AKADEMI_MODE, GATEWAY_STATE, SEQ_GAP_WARN, errorText, lagText, seqGap, syncVerdict,
} from './sync-logic.js';

const REFRESH_MS = 20_000;

export function syncTab(app) {
  const node = h('div', 'ak-stack');
  let timer = null;
  let visible = false;
  let busy = false;

  const refresh = button('Yenile', { onClick: () => view.reload() });
  node.append(toolbar(h('span', 'ak-muted', 'KM\'deki öğrenci, veli, kantin, tablet ve zil değişiklikleri '
    + 'olay olarak Akademi\'ye iletilir; mobil uygulama 1–3 sn içinde günceller.'), h('span', 'kit-spacer'), refresh));

  const view = loader(load, render, { rows: 5, columns: 4 });
  node.append(view.node);

  async function load() {
    const canAkademi = app.can('admin_getDirectorySyncStatus');
    const [gateway, akademi] = await Promise.all([
      app.api.km('/events').catch((failure) => ({ failed: problem(failure) })),
      canAkademi
        ? app.api.get('/v1/admin/directory/sync-status').catch((failure) => ({ failed: problem(failure) }))
        : Promise.resolve({ forbidden: true }),
    ]);
    return { gateway, akademi };
  }

  function render({ gateway, akademi }) {
    const gwOk = gateway && !gateway.failed ? gateway : null;
    const akOk = akademi && !akademi.failed && !akademi.forbidden ? akademi : null;
    const verdict = syncVerdict(gwOk, akOk);
    const nodes = [];
    const tone = verdict.tone === 'dim' ? 'info' : verdict.tone;
    const summary = h('div', 'ak-stack');
    summary.append(h('b', '', verdict.title));
    for (const reason of verdict.reasons) summary.append(h('span', '', reason.text));
    const box = alertBox('', tone);
    box.replaceChildren(summary);
    nodes.push(box);

    const gw = gwOk?.status || null;
    const ev = akOk?.events || null;
    const gap = seqGap(gw, ev);
    nodes.push(kpiRow([
      { label: 'KM son olay', value: gw ? num(gw.lastSeq) : '—' },
      { label: 'Akademi\'ye iletilen', value: gw ? num(gw.deliveredSeq) : '—' },
      { label: 'Akademi\'nin aldığı', value: ev?.lastSeq !== null && ev?.lastSeq !== undefined ? num(ev.lastSeq) : '—' },
      { label: 'Fark', value: gap === null ? '—' : num(gap),
        tone: gap !== null && gap > SEQ_GAP_WARN && gw?.enabled ? 'warn' : '',
        title: 'KM\'nin son olay sırası ile Akademi\'nin aldığı son sıra arasındaki fark.' },
      { label: 'Ölü mektup', value: gw ? num(gw.deadCount) : '—', tone: gw?.deadCount ? 'warn' : '' },
    ]));

    const grid = h('div', 'ak-set-sync');
    grid.append(gatewayCard(gateway), akademiCard(akademi));
    nodes.push(grid);
    if (gwOk?.supported && gwOk.canView) nodes.push(deadCard(gwOk));
    schedule();
    return nodes;
  }

  function gatewayCard(gateway) {
    const title = 'KM → Akademi (geçit gönderici)';
    if (gateway?.failed) return card(title, alertBox(`Durum okunamadı: ${gateway.failed}`, 'bad'));
    if (!gateway?.supported) {
      return card(title, empty('Geçit sürümü olay iletimini desteklemiyor',
        'Bu KM\'de “Mobil geçit” modülü yok ya da olay iletiminden eski bir sürüm. Akademi dizini 2 dk '
        + 'yoklamayla ve gece tam eşitlemeyle güncel kalır.'));
    }
    if (!gateway.canView) {
      return card(title, empty('Görüntüleme yetkiniz yok',
        'Geçidin iletim durumunu görmek için “mobile_gateway.events.view” izni gerekir.'));
    }
    const gw = gateway.status || {};
    const state = gw.enabled ? gw.state : 'off';
    const pending = Math.max(0, Number(gw.lastSeq || 0) - Number(gw.deliveredSeq || 0));
    return card(title, kv([
      ['Durum', statusBadge(GATEWAY_STATE, state)],
      ['Son olay (KM)', num(gw.lastSeq ?? 0)],
      ['İletilen son olay', num(gw.deliveredSeq ?? 0)],
      ['İletilmeyi bekleyen', num(pending)],
      ['En eski bekleyenin yaşı', pending ? lagText(gw.lagSeconds) : '—'],
      ['Son iletim gecikmesi', gw.deliveryLagMs === null || gw.deliveryLagMs === undefined
        ? '—' : `${num(gw.deliveryLagMs)} ms`],
      ['Son başarılı iletim', fmtDateTime(gw.lastSuccessAt)],
      ['Son hata', errorText(gw.enabled ? gw.lastError : null)],
      ['Akademi kiracısı', gw.tenant || '—'],
    ]), gw.unreachable ? '6 saattir ulaşılamıyor' : '');
  }

  function akademiCard(akademi) {
    const title = 'Akademi alıcısı';
    if (akademi?.forbidden) {
      return card(title, empty('Görüntüleme yetkiniz yok',
        'Akademi tarafını görmek için Akademi ayarları yetkisi (bbd_akademi.settings) gerekir.'));
    }
    if (akademi?.failed) return card(title, alertBox(`Akademi durumu okunamadı: ${akademi.failed}`, 'bad'));
    const ev = akademi?.events;
    if (!ev) {
      return card(title, empty('Akademi olay kanalı durumunu bildirmiyor',
        'Akademi sunucusu olay alıcısından (POST /v1/webhooks/km) eski bir sürümde olabilir.'));
    }
    const counts = akademi.counts || {};
    return card(title, kv([
      ['Mod', statusBadge(AKADEMI_MODE, ev.mode)],
      ['Alınan son olay', ev.lastSeq === null || ev.lastSeq === undefined ? '—' : num(ev.lastSeq)],
      ['Son alma', fmtDateTime(ev.lastReceivedAt)],
      ['Son uygulama', fmtDateTime(ev.lastAppliedAt)],
      ['Uygulanmayı bekleyen', num(ev.pending ?? 0)],
      ['Uygulama gecikmesi (p95, 1 sa)', lagText(ev.lagSecondsP95)],
      ['Son hata', errorText(ev.lastErrorCode)],
      ['Etkin / pasif öğrenci', `${num(counts.active ?? 0)} / ${num(counts.inactive ?? 0)}`],
    ]));
  }

  function deadCard(gateway) {
    const rows = (gateway.dead || []).map((row) => ({ ...row, id: String(row.seq) }));
    const table = dataTable({
      columns: [
        { key: 'seq', label: 'Sıra', width: '90px', cell: (row) => num(row.seq) },
        { key: 'type', label: 'Olay', width: 'minmax(0, 1.4fr)', cell: (row) => row.type },
        { key: 'error', label: 'Akademi yanıtı', width: 'minmax(0, 1.4fr)', cell: (row) => errorText(row.errorCode) },
        { key: 'attempts', label: 'Deneme', width: '80px', cell: (row) => num(row.attempts) },
        { key: 'last', label: 'Son deneme', width: '140px', cell: (row) => fmtDateTime(row.lastAttemptAt) },
      ],
      rows,
      dense: true,
      selectable: gateway.canManage && rows.length > 0,
      empty: empty('Ölü mektup yok', 'Akademi\'nin reddettiği olay yok.'),
    });
    const actions = [];
    if (rows.length) {
      if (gateway.canManage) {
        actions.push(button('Seçilenleri yeniden gönder', { onClick: (event) => retry(
          table.selection().map(Number), event.currentTarget) }));
        actions.push(button('Tümünü yeniden gönder', { variant: 'primary', onClick: (event) => retry(
          rows.map((row) => row.seq), event.currentTarget, true) }));
      } else {
        actions.push(blockedButton('Yeniden gönder',
          'Yeniden göndermek için “mobile_gateway.events.manage” izni gerekir.'));
      }
    }
    const body = h('div', 'ak-stack');
    if (actions.length) body.append(toolbar(h('span', 'ak-muted', 'Reddedilen olay tek tek yeniden gönderilir; '
      + 'Akademi aynı olayı (eventId) iki kez uygulamaz.'), h('span', 'kit-spacer'), ...actions));
    body.append(table.node);
    const more = Number(gateway.status?.deadCount || 0) - rows.length;
    if (more > 0) body.append(h('span', 'ak-muted', `İlk ${num(rows.length)} kayıt gösteriliyor (toplam ${num(gateway.status.deadCount)}).`));
    return card('Ölü mektuplar', body, rows.length ? `${num(gateway.status?.deadCount || rows.length)} olay` : '');
  }

  async function retry(seqs, control, all = false) {
    if (!seqs.length) {
      app.toast('Önce yeniden gönderilecek olayları seçin.', 'warn');
      return;
    }
    if (all) {
      const ok = await confirmAction(app, { title: 'Bütün ölü mektuplar yeniden gönderilsin mi?',
        description: `${num(seqs.length)} olay Akademi'ye tek tek yeniden gönderilir.`, confirmLabel: 'Gönder' });
      if (!ok) return;
    }
    if (busy) return;
    busy = true;
    if (control) control.disabled = true;
    try {
      const reply = await app.api.km('/events/retry', { method: 'POST', body: { seqs } });
      const result = reply?.result || {};
      app.toast(`${num(result.delivered || 0)} olay iletildi${result.failed ? `, ${num(result.failed)} yine reddedildi` : ''}.`,
        result.failed ? 'warn' : 'good');
      view.reload();
    } catch (failure) {
      app.toast(problem(failure), 'bad');
    } finally {
      busy = false;
      if (control) control.disabled = false;
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = null;
    if (!visible) return;
    timer = setTimeout(() => {
      timer = null;
      if (document.hidden || busy) { schedule(); return; }
      view.reload();
    }, REFRESH_MS);
  }

  return {
    node,
    reload: () => view.reload(),
    shown() { visible = true; if (!timer) schedule(); },
    hidden() { visible = false; clearTimeout(timer); timer = null; },
    destroy() { visible = false; clearTimeout(timer); timer = null; },
  };
}
