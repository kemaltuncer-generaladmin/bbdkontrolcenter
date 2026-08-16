// Akış bileşenleri — zaman çizelgesi, aşama şeridi, ölçü karşılaştırma.
//
// Tek kopya (ADR 0011). Hepsi düğüm döndürür; hiçbiri kendi verisini çekmez
// ve hiçbiri panelin durumunu bilmez.
//
// ADLAR ALAN ADI TAŞIMAZ. Burada "gönderi", "sipariş", "kargo" geçmez:
// kabuk modül adı bilmez (K1) ve aynı üç bileşen kargo hareketinde de,
// ödeme talebinde de, baskı işinde de aynı işi görür. Alan diline çeviri
// panelin işidir — `timeline` olay alır, gönderi almaz.
//
// TASARIM KURALI (charts.js ile aynı): renk tek başına anlam taşımaz. Her
// öğenin yazısı vardır; rengi kaldırdığınızda bilgi kaybolmaz. Ekran
// görüntüsü siyah-beyaz yazdırıldığında da okunur.

import { h } from './kit.js';

/** Ton adları kit genelinde ortaktır: good · warn · bad · info · dim. */
const TONES = new Set(['good', 'warn', 'bad', 'info', 'dim']);

const tone = (value) => (TONES.has(value) ? value : '');

/**
 * Dikey olay akışı — bir şeyin başına sırayla ne geldiği.
 *
 *   timeline([
 *     {title: 'Kargoya verildi', at: '14 Ağu 09:12', detail: 'Konya Aktarma'},
 *     {title: 'Dağıtımda',       at: '15 Ağu 07:40', tone: 'info'},
 *     {title: 'Teslim edildi',   at: '15 Ağu 16:05', tone: 'good'},
 *   ])
 *
 * SIRA ESKİDEN YENİYE. Yolculuk yukarıdan aşağı okunur; en yeni hareket en
 * altta ve VURGULU durur. Tersine dizmek (en yeni üstte) bir liste için
 * doğrudur ama bir yolculuk için değildir: okuyan kişi "nereye kadar geldi"
 * sorusunu son satıra bakarak yanıtlar.
 *
 * @param {Array<{title: string, detail?: string, at?: string, tone?: string,
 *                pending?: boolean}>} events
 * @param {{emptyText?: string}} [options]
 */
export function timeline(events, { emptyText = 'Henüz hareket yok.' } = {}) {
  const list = Array.isArray(events) ? events.filter(Boolean) : [];
  const node = h('ol', 'kit-timeline');

  if (list.length === 0) {
    const bos = h('div', 'kit-timeline-empty', emptyText);
    return bos;
  }

  list.forEach((event, index) => {
    const son = index === list.length - 1;
    // BEKLEYEN ADIM AYRI GÖSTERİLİR: gerçekleşmiş bir hareketle "olması
    // beklenen" adımı aynı biçimde çizmek, olmamış bir şeyi olmuş gibi
    // gösterirdi.
    const bekleyen = Boolean(event.pending);
    const item = h('li', `kit-tl-item${son && !bekleyen ? ' now' : ''}${bekleyen ? ' pending' : ''}`);

    const mark = h('span', `kit-tl-dot ${tone(event.tone)}`);
    mark.setAttribute('aria-hidden', 'true');
    item.append(mark);

    const body = h('div', 'kit-tl-body');
    const head = h('div', 'kit-tl-head');
    head.append(h('span', 'kit-tl-title', String(event.title ?? '')));
    if (event.at) head.append(h('time', 'kit-tl-at', String(event.at)));
    body.append(head);
    if (event.detail) body.append(h('div', 'kit-tl-detail', String(event.detail)));
    // Bekleyen adım yazıyla da söylenir; renk/soluk çizim tek başına
    // "bu henüz olmadı" demez.
    if (bekleyen) body.append(h('div', 'kit-tl-note', 'Henüz gerçekleşmedi'));
    item.append(body);

    node.append(item);
  });

  return node;
}

/**
 * Yatay aşama şeridi — üç beş adımlı bir sürecin neresindeyiz.
 *
 *   stepper([{label: 'Sipariş alındı'}, {label: 'Kargoya verildi'},
 *            {label: 'Teslim edildi'}], 1)
 *
 * `activeIndex` TAMAMLANMIŞ SON adımın sırasıdır; ondan sonrası bekliyordur.
 * -1 verilirse hiçbir adım tamamlanmamıştır.
 *
 * NEDEN `progress` YETMEDİ. `progress` yüzde çizer ve tek satır etiket
 * gösterir; burada her adımın kendi adı ve zamanı aynı anda görünür olmalı,
 * çünkü sorulan soru "ne kadar ilerledi" değil, "hangi adımda takıldı".
 *
 * `state` — ADIMIN KENDİ DURUM YAZISI, verilirse otomatik metnin yerine geçer.
 * Bazı alanlarda adım İKİLİ DEĞİLDİR: sipariş kısmen faturalanır, kutuların
 * yarısı kargoya verilir. Böyle bir adıma "tamamlandı" yazmak olmamış bir şeyi
 * olmuş, "bekliyor" yazmak başlamış bir şeyi hiç başlamamış gösterir; ikisi de
 * yanlıştır ve ikisi de sessizdir. `activeIndex` yalnız KESİNTİSİZ tamamlanan
 * başlangıcı işaretler, yarım kalan adım kendi cümlesini kurar.
 *
 * @param {Array<{label: string, at?: string, tone?: string, state?: string}>} steps
 * @param {number} activeIndex
 */
export function stepper(steps, activeIndex = -1) {
  const list = Array.isArray(steps) ? steps.filter(Boolean) : [];
  const node = h('ol', 'kit-stepper');
  const done = Number.isFinite(activeIndex) ? Number(activeIndex) : -1;

  list.forEach((step, index) => {
    const tamam = index <= done;
    const simdi = index === done;
    const item = h('li', `kit-step${tamam ? ' done' : ''}${simdi ? ' now' : ''}`);

    const mark = h('span', `kit-step-mark ${tone(step.tone)}`);
    // Sayı okunur kalır: renksiz yazdırmada "kaçıncı adım" bilgisi
    // kaybolmasın.
    mark.textContent = String(index + 1);
    item.append(mark);

    const body = h('div', 'kit-step-body');
    body.append(h('span', 'kit-step-label', String(step.label ?? '')));
    if (step.at) body.append(h('time', 'kit-step-at', String(step.at)));
    // Durum YAZIYLA da söylenir (K-B: ekran ne olduğunu söyler).
    body.append(h('span', 'kit-step-state',
      step.state ? String(step.state) : (tamam ? 'tamamlandı' : 'bekliyor')));
    item.append(body);

    node.append(item);
  });

  return node;
}

/**
 * İki ölçüyü yan yana koyan çubuk — hangisi geçerli olduğu işaretli.
 *
 *   measureBar([
 *     {label: 'Ölçülen ağırlık', value: 2.4, text: '2,4 kg'},
 *     {label: 'Hacimsel (desi)', value: 5.0, text: '5,0 desi', governs: true},
 *   ], {note: 'Faturalanan: büyük olanın tavana yuvarlanmışı → 5 desi'})
 *
 * NEDEN GEREKLİ. İki ölçüden BÜYÜĞÜ faturalanır. Sayıları alt alta yazmak
 * hangisinin ödemeyi belirlediğini göstermez; kullanıcı küçük olana bakıp
 * ücreti yanlış bekler. `governs` işaretli satır hem vurgulanır hem
 * "faturalanan bu" yazısını taşır.
 *
 * @param {Array<{label: string, value: number, text?: string,
 *                governs?: boolean, tone?: string}>} items
 * @param {{note?: string}} [options]
 */
export function measureBar(items, { note = '' } = {}) {
  const list = (Array.isArray(items) ? items : []).filter(Boolean);
  const node = h('div', 'kit-measures');
  // Sıfıra bölme yok: hepsi sıfırsa çubuklar boş kalır, sayılar yine okunur.
  const max = list.reduce((top, item) => Math.max(top, Number(item.value) || 0), 0);

  for (const item of list) {
    const row = h('div', `kit-measure${item.governs ? ' governs' : ''}`);

    const head = h('div', 'kit-measure-head');
    head.append(h('span', 'kit-measure-label', String(item.label ?? '')));
    head.append(h('span', 'kit-measure-value',
      String(item.text ?? (Number(item.value) || 0))));
    row.append(head);

    const track = h('div', 'kit-measure-track');
    const fill = h('div', `kit-measure-fill ${tone(item.tone)}`);
    const oran = max > 0 ? Math.round(((Number(item.value) || 0) / max) * 100) : 0;
    fill.style.width = `${Math.max(0, Math.min(100, oran))}%`;
    track.append(fill);
    row.append(track);

    // GEÇERLİ OLAN YAZIYLA SÖYLENİR: kalın çizgi ya da renk tek başına
    // "ücreti bu belirliyor" anlamına gelmez.
    if (item.governs) row.append(h('div', 'kit-measure-governs', 'Faturalanan ölçü bu'));

    node.append(row);
  }

  if (note) node.append(h('div', 'kit-measure-note', String(note)));
  return node;
}
