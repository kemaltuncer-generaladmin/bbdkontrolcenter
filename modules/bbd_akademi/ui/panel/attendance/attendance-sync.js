// BBD Akademi — çevrimdışı yoklama kuyruğu (`admin_syncAttendance`, F-Y7).
//
// KM paneli sunucudaki Akademi'ye vekil üzerinden yazar. Yoklama kaydedilirken KM sunucusuna
// ULAŞILAMAZSA (`km_unavailable`: ağ koptu, sunucu yeniden başlıyor) işaretler kaybolmasın diye bu
// bilgisayarda bekletilir ve bağlantı gelince TEK istekte (en çok 50 oturum) gönderilir — öğretmen
// uygulamasının çevrimdışı senkronuyla AYNI uç ve aynı kurallar:
//   · her öğe `clientUuid` ile tekildir (iki kez gönderilse de bir kez yazılır → `duplicate`);
//   · `markedAt` işaretin konduğu andır; düzeltme penceresi buna göre değerlendirilir, pencere
//     geçtiyse öğe `queued_for_review` olur ve Yoklama → Düzeltme kuyruğu'na düşer;
//   · `baseVersion` o an görülen liste sürümüdür; arada başkası değiştirdiyse `conflict` döner,
//     hiçbir şey yazılmaz ve liste açılıp kontrol edilmelidir.
// Kuyruk tarayıcı deposundadır (yalnız bu bilgisayar); depo kapalıysa kuyruk bellekte kalır.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox } from '../../../ui-kit/layout.js';
import { num, problem } from '../common.js';

const STORE_KEY = 'bbd_akademi.attendance.offline.v1';
export const SYNC_LIMIT = 50;
let memory = [];

function uuid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const hex = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  return `${hex(8)}-${hex(4)}-4${hex(3)}-a${hex(3)}-${hex(12)}`;
}

export function readQueue() {
  try {
    const raw = globalThis.localStorage?.getItem(STORE_KEY);
    if (raw) return JSON.parse(raw) || [];
  } catch { /* depo kapalı: bellek */ }
  return [...memory];
}

function writeQueue(list) {
  memory = [...list];
  try {
    if (list.length) globalThis.localStorage?.setItem(STORE_KEY, JSON.stringify(list));
    else globalThis.localStorage?.removeItem(STORE_KEY);
  } catch { /* depo kapalı: bellek yeter */ }
}

/**
 * Kaydedilemeyen yoklamayı kuyruğa koyar. Aynı oturumun eski bekleyeni yenisiyle değişir
 * (son işaret geçerlidir). `label`: ekranda gösterilecek ad ("Matematik · 8-A, 14.10 09:00").
 */
export function enqueue({ sessionId, baseVersion = 0, rows, label = '' }, now = new Date()) {
  const list = readQueue().filter((item) => item.sessionId !== sessionId);
  list.push({ clientUuid: uuid(), sessionId, baseVersion: Number(baseVersion) || 0, markedAt: now.toISOString(),
    rows, label });
  writeQueue(list);
  return list.length;
}

/** `AttendanceSyncRequest` gövdesi (en çok 50 öğe). */
export function syncBody(list) {
  return { items: list.slice(0, SYNC_LIMIT).map((item) => ({ clientUuid: item.clientUuid, sessionId: item.sessionId,
    baseVersion: item.baseVersion, markedAt: item.markedAt, markAllPresent: false, rows: item.rows })) };
}

/**
 * Sonuçları kuyruğa uygular: gönderilen her öğe kuyruktan çıkar (yanıtta olmayan kalır).
 * @returns {{applied, review, conflict, rejected, left, messages}}
 */
export function applyResults(list, results) {
  const byId = new Map((results || []).map((item) => [item.clientUuid, item]));
  const summary = { applied: 0, review: 0, conflict: 0, rejected: 0, left: 0, messages: [] };
  const rest = [];
  for (const item of list) {
    const result = byId.get(item.clientUuid);
    if (!result) { rest.push(item); continue; }
    if (result.result === 'applied' || result.result === 'duplicate') summary.applied += 1;
    else if (result.result === 'queued_for_review') {
      summary.review += 1;
      summary.messages.push(`${item.label || 'Yoklama'}: düzeltme penceresi geçmiş, onay kuyruğuna düştü.`);
    } else if (result.result === 'conflict') {
      summary.conflict += 1;
      summary.messages.push(`${item.label || 'Yoklama'}: bu arada başka biri değiştirmiş; listeyi açıp kontrol edin.`);
    } else {
      summary.rejected += 1;
      summary.messages.push(`${item.label || 'Yoklama'}: kaydedilemedi${result.errorCode ? ` (${result.errorCode})` : ''}.`);
    }
  }
  summary.left = rest.length;
  return { rest, summary };
}

/** Bekleyenleri gönderir. Ağ yine yoksa kuyruk aynen kalır ve hata fırlar. */
export async function flush(app) {
  const list = readQueue();
  if (!list.length) return null;
  const sent = list.slice(0, SYNC_LIMIT);
  const result = await app.api.post('/v1/admin/attendance/sync', syncBody(sent));
  const { rest, summary } = applyResults(sent, result?.results);
  writeQueue([...rest, ...list.slice(SYNC_LIMIT)]);
  summary.left = readQueue().length;
  return summary;
}

/**
 * Günlük yoklama ekranının üstündeki şerit: "N yoklama gönderilmeyi bekliyor · Şimdi gönder".
 * `refresh()` ekran açılınca ve kayıttan sonra çağrılır; bekleyen varsa kendiliğinden bir kez dener.
 */
export function syncBanner(app, { onSynced } = {}) {
  const node = h('div', 'ak-att-offline');
  node.hidden = true;
  let busy = false;

  function paint(extra = []) {
    const count = readQueue().length;
    node.replaceChildren();
    node.hidden = !count && !extra.length;
    if (count) {
      const row = h('div', 'ak-inline ak-att-offline-row');
      row.append(h('b', '', `${num(count)} yoklama bu bilgisayarda gönderilmeyi bekliyor.`),
        h('span', 'ak-muted', 'Kontrol Merkezi sunucusuna ulaşılamadığında kaydedildi.'));
      if (app.can('admin_syncAttendance')) {
        row.append(button(busy ? 'Gönderiliyor…' : 'Şimdi gönder', { variant: 'primary', disabled: busy,
          onClick: () => send() }));
      }
      node.append(row);
    }
    for (const message of extra) node.append(alertBox(message, 'warn'));
  }

  async function send({ quiet = false } = {}) {
    if (busy || !readQueue().length || !app.can('admin_syncAttendance')) return;
    busy = true;
    paint();
    try {
      const summary = await flush(app);
      busy = false;
      if (!summary) { paint(); return; }
      if (summary.applied) app.toast(`${num(summary.applied)} bekleyen yoklama gönderildi.`, 'good');
      paint(summary.messages);
      if (summary.applied || summary.review || summary.conflict) onSynced?.();
    } catch (failure) {
      busy = false;
      paint();
      if (!quiet) app.toast(problem(failure, 'Bekleyen yoklamalar gönderilemedi; bağlantı gelince tekrar deneyin.'), 'bad');
    }
  }

  return {
    node,
    refresh() { paint(); send({ quiet: true }); },
    send,
  };
}
