// BBD Akademi — Ders programı › "Zil planı durumu" kartı.
//
// Sözleşme km-ogrenci-gruplari-ve-zil.md §3.2–§3.5: Akademi ders programından
// ve kurum takviminden önümüzdeki 14 günün zil planını üretip KM'ye iter; KM
// Zil Sistemi planı yerelde tutar ve çalar. Bu kart yöneticiye planın KM'ye
// ulaşıp ulaşmadığını gösterir (günlerin kaynağı, bugünün kipi, çağrı ve
// bekleyen ses sayısı, zil ajanı, oturum çağrıları şalteri, acil kapatma).
//
// KAYNAK SEÇİMİ (ekran açılışında TEK istek; "Yenile" ile tekrar):
//   1. Akademi `admin_getBellPlanStatus` izin listesinde ve kullanıcıya açıksa
//      o (yolu `/km/operations` listesinden alınır). Akademi işlemi henüz
//      sunmuyorsa (404) ya da kiracıda `bell_plan_sync` kapalıysa
//      (`feature_disabled`) bir kez KM'ye düşülür (`shouldFallBack`).
//   2. KM Zil Sistemi'nin kendi ucu `GET /api/bell/plan/status?from&to`
//      (izin `bell.view`). Uç bu sürümde yoksa (404) ya da izin yoksa (403)
//      kart ham hata değil sebebini yazar.
//
// "Planı şimdi it" Akademi'nin `admin_pushBellPlan` işlemidir (202, kirli
// günler arka planda itilir); birkaç saniye sonra durum yeniden okunur.
//
// Özet hesabı `bell-plan.js`'tedir (Node ile birim testli).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, hintBox } from '../../ui-kit/layout.js';
import { actionButton, fmtDate, fmtDateTime, num, runAction, todayIst } from './common.js';
import {
  PUSH_RESULT_TEXT, planStatusProblem, shouldFallBack, summarizePlanStatus, todayModeText,
} from './bell-plan.js';

const AKADEMI_STATUS = 'admin_getBellPlanStatus';
const WINDOW_DAYS = 14;
const SOURCE_LABEL = { akademi: 'Akademi', km: 'KM Zil Sistemi' };

function akademiPath(app) {
  const operation = app.api.operation(AKADEMI_STATUS);
  return operation && operation.method === 'GET' && operation.allowed ? operation.path : null;
}

export function bellPlanCard(app) {
  const node = h('section', 'kit-card ak-tt-bell');
  const head = h('div', 'kit-card-head');
  const hint = h('span', 'kit-card-hint', '');
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => load(true) });
  const push = actionButton(app, 'admin_pushBellPlan', 'Planı şimdi it', { variant: 'ghost',
    title: 'Akademi kirli günlerin zil planını KM\'ye hemen gönderir', onClick: () => pushNow() });
  head.append(h('h3', 'kit-card-title', 'Zil planı durumu'), hint, h('span', 'kit-spacer'), push,
    button('Zil Sistemi\'ni aç', { variant: 'ghost', title: 'KM Zil Sistemi ekranı',
      onClick: () => app.openPanel('bell') }), refresh);
  const body = h('div', 'ak-tt-bell-body');
  node.append(head, body);
  let token = 0;
  let started = false;

  async function read(from, to) {
    const path = akademiPath(app);
    let note = null;
    if (path) {
      try {
        return { kind: 'akademi', data: await app.api.get(path, { from, to }), note };
      } catch (failure) {
        if (!shouldFallBack(failure)) throw Object.assign(failure, { source: 'akademi' });
        note = planStatusProblem(failure, 'akademi').text;
      }
    }
    try {
      return { kind: 'km', data: await app.kmGet(`/api/bell/plan/status?from=${from}&to=${to}`), note };
    } catch (failure) {
      throw Object.assign(failure, { source: 'km', note });
    }
  }

  async function load(force = false) {
    if (started && !force) return;
    started = true;
    const mine = ++token;
    const from = todayIst();
    const to = todayIst(WINDOW_DAYS - 1);
    hint.textContent = `Önümüzdeki ${WINDOW_DAYS} gün`;
    body.replaceChildren(h('span', 'ak-muted', 'Zil planı durumu okunuyor…'));
    refresh.disabled = true;
    try {
      const { kind, data, note } = await read(from, to);
      if (mine !== token) return;
      hint.textContent = `Önümüzdeki ${WINDOW_DAYS} gün · kaynak: ${SOURCE_LABEL[kind]}`;
      body.replaceChildren(...[note ? h('small', 'ak-muted', note) : null,
        ...render(summarizePlanStatus(data, from))].filter(Boolean));
    } catch (failure) {
      if (mine !== token) return;
      const { tone, text } = planStatusProblem(failure, failure?.source || 'km');
      body.replaceChildren(...[failure?.note ? h('small', 'ak-muted', failure.note) : null,
        alertBox(text, tone)].filter(Boolean));
    } finally {
      if (mine === token) refresh.disabled = false;
    }
  }

  async function pushNow() {
    const done = await runAction(app, () => app.api.post('/v1/admin/bell-plan/push', undefined),
      { success: 'Zil planı KM\'ye gönderilmek üzere sıraya alındı.', button: push });
    if (done !== undefined) setTimeout(() => load(true), 4000);
  }

  function render(summary) {
    const nodes = [];
    if (summary.stale) {
      nodes.push(alertBox('KM Zil Sistemi\'ne şu an ulaşılamıyor; son bilinen durum gösteriliyor.', 'warn'));
    }
    const flags = h('div', 'ak-inline ak-tt-bell-flags');
    if (summary.bellEnabled !== null) {
      flags.append(badge(summary.bellEnabled ? 'Zil açık' : 'Zil kapalı', summary.bellEnabled ? 'good' : 'bad'));
    }
    if (summary.agentOnline !== null) {
      const agent = badge(summary.agentOnline ? 'Zil ajanı çevrimiçi' : 'Zil ajanı çevrimdışı',
        summary.agentOnline ? 'good' : 'warn');
      if (summary.agentLastSeenAt) agent.title = `Son görülme ${fmtDateTime(summary.agentLastSeenAt)}`;
      flags.append(agent);
    }
    if (summary.sessionCalls !== null) {
      flags.append(badge(summary.sessionCalls ? 'Oturum çağrıları açık' : 'Oturum çağrıları kapalı',
        summary.sessionCalls ? 'info' : 'dim'));
    }
    if (flags.childElementCount) nodes.push(flags);

    const today = summary.today;
    const todayRow = h('div', 'ak-tt-bell-today');
    todayRow.append(h('b', '', 'Bugün:'), h('span', '', todayModeText(today)));
    if (today) {
      todayRow.append(h('span', 'ak-muted', [
        `${num(today.calls)} çağrı`,
        today.voicesPending ? `${num(today.voicesPending)} ses hazırlanıyor` : '',
        today.source === 'none' ? 'Akademi planı yok' : today.source === 'km' ? 'KM acil kapatması' : 'Akademi planı',
        today.lastDispatchAt ? `son gönderim ${fmtDateTime(today.lastDispatchAt)}` : '',
      ].filter(Boolean).join(' · ')));
    }
    nodes.push(todayRow);

    const { counts, total } = summary;
    nodes.push(h('div', 'ak-muted', [
      `${num(total)} gün: ${num(counts.akademi)} Akademi planı`,
      `${num(counts.none)} plan yok`,
      counts.km ? `${num(counts.km)} KM acil kapatması` : '',
      summary.outOfSync ? `${num(summary.outOfSync)} gün KM ile eşit değil` : '',
      summary.voicesPending ? `toplam ${num(summary.voicesPending)} ses hazırlanıyor` : 'bütün çağrı sesleri hazır',
    ].filter(Boolean).join(' · ')));
    if (summary.lastPushAt || summary.lastPushResult) {
      nodes.push(h('div', 'ak-muted', `Son itme: ${summary.lastPushAt ? fmtDateTime(summary.lastPushAt) : '—'}${
        summary.lastPushResult ? ` · ${PUSH_RESULT_TEXT[summary.lastPushResult] || summary.lastPushResult}` : ''}`));
    }

    for (const item of summary.overrides) {
      nodes.push(alertBox(`Acil kapatma: ${fmtDate(item.date)} günü zil ve çağrı KM'den kapatıldı${
        item.text ? ` (${item.text})` : ''}. Akademi takvimi değişmez; ertesi gün Akademi planı geçerlidir.`, 'warn'));
    }
    if (total && counts.none === total) {
      nodes.push(hintBox('Akademi bu günler için henüz zil planı iletmedi: zil haftalık saatlerle çalar, '
        + 'oturum çağrısı yapılmaz.'));
    } else if (!total) {
      nodes.push(hintBox('Bu aralık için zil planı bilgisi yok.'));
    }
    return nodes;
  }

  return { node, load };
}
