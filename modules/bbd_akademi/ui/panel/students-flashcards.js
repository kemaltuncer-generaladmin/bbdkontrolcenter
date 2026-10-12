// BBD Akademi — öğrenci detayı: Bilgi kartları sekmesi (salt okunur).
//
// Öğrencinin desteleri (kendi + kurumun atadıkları), Leitner kutu dağılımı,
// son 14 günün tekrar istikrarı, bugünkü tekrar kuyruğu ve kart listesi.
// Kartı öğrenci yönetir; kurum yalnız görür. Kurum destesini düzenlemek
// "Bilgi kartları" bölümündedir (`app.go('decks', {deckId})`).
//
// Bayrak (`flashcards`) kurumda kapalıysa Akademi `feature_disabled` döner;
// sekme bunu sebebiyle söyler, boş tablo göstermez.
//
// Görseller kendiliğinden inmez ("Görsel" düğmesi); öğrenci görselleri
// imzalı bağlantıyla gelir, süresi dolmuşsa dosya kaydı tazelenir
// (`/v1/students/{id}/files/{fileId}`).

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge, card, chipRow, kpiRow } from '../../ui-kit/layout.js';
import { groupedBar } from '../../ui-kit/charts.js';
import {
  empty, errorBox, fmtDate, fmtDateTime, kv, loader, num, openDrawer, pagedList, percent, problem,
  selectBox, toolbar,
} from './common.js';

const SOURCE = { own: ['Kendi', 'dim'], institution: ['Kurum', 'info'] };

function disabledNotice(app, failure) {
  if (failure?.code !== 'feature_disabled') return null;
  return empty('Bilgi kartları kurumda kapalı',
    'Özellik açılınca öğrencinin desteleri burada görünür (Ayarlar → Özellikler).',
    app.can('admin_listFeatures')
      ? [button('Özelliklere git', { variant: 'ghost', onClick: () => app.go('settings', { tab: 'features' }) })]
      : []);
}

/**
 * @returns {{node: HTMLElement, shown: Function, destroy: Function}}
 */
export function flashcardsPanel(app, studentId) {
  loadStyles(import.meta.url, 'students-flashcards.css');
  const node = h('div', 'ak-stack ak-sfc');
  const urls = new Set();
  let started = false;
  let cards = null;
  let queue = null;
  const filters = { deckId: '', source: '' };

  const summary = loader(async () => {
    const [stats, decks] = await Promise.all([
      app.api.get(`/v1/students/${studentId}/flashcards/stats`),
      app.api.get(`/v1/students/${studentId}/flashcard-decks`, { limit: 100 }),
    ]);
    return { stats, decks: decks?.items || [] };
  }, ({ stats, decks }) => {
    deckSelect.options([{ value: '', label: 'Tüm desteler' },
      ...decks.map((deck) => ({ value: deck.id, label: deck.title }))]);
    const boxes = [1, 2, 3, 4, 5].map((box) => ({ label: `${box}. kutu`,
      values: [stats.boxDistribution?.find((item) => item.box === box)?.count || 0] }));
    const days = (stats.last14Days || []).map((day) => ({ label: fmtDate(day.date).slice(0, 5),
      values: [day.reviewed, day.known] }));
    return [
      kpiRow([
        { label: 'Toplam kart', value: num(stats.totalCards) },
        { label: 'Bugün tekrar edilecek', value: num(stats.dueToday), tone: stats.dueToday ? 'warn' : '' },
        { label: 'Bugün tekrar edilen', value: stats.dailyGoal
          ? `${num(stats.reviewedToday)} / ${num(stats.dailyGoal)}` : num(stats.reviewedToday) },
        { label: 'İstikrar (14 gün)', value: percent(stats.consistencyPercent, 0) },
        { label: 'Seri', value: `${num(stats.streak)} gün` },
      ]),
      h('div', 'ak-muted ak-sfc-note', `Hesaplama: ${fmtDateTime(stats.computedAt)}`),
      twoCharts(boxes, days),
      decksCard(decks),
    ];
  }, { rows: 3, columns: 5 });

  function twoCharts(boxes, days) {
    const grid = h('div', 'ak-grid-2');
    grid.append(
      card('Kutu dağılımı', groupedBar(boxes, { series: ['Kart'], height: 170, format: (v) => num(v) }),
        '1. kutu yeni / zor, 5. kutu öğrenildi'),
      card('Son 14 gün', groupedBar(days, { series: ['Tekrar', 'Bildi'], height: 170, format: (v) => num(v) })),
    );
    return grid;
  }

  function decksCard(decks) {
    const list = h('div', 'ak-sfc-decks');
    if (!decks.length) list.append(empty('Deste yok', 'Öğrenci henüz deste oluşturmamış; kurum destesi de atanmamış.'));
    for (const deck of decks) {
      const row = h('button', 'ak-sfc-deck');
      row.type = 'button';
      const text = h('div', 'ak-cell-stack');
      text.append(h('b', '', deck.title), h('small', '', [deck.subject?.name,
        (deck.topics || []).map((topic) => topic.name).slice(0, 3).join(', ')].filter(Boolean).join(' · ')
        || 'Ders bağlı değil'));
      const meta = h('div', 'ak-inline');
      meta.append(badge(...SOURCE[deck.source]), h('span', 'ak-muted', `${num(deck.cardCount)} kart`),
        deck.dueCount ? badge(`${num(deck.dueCount)} tekrar bekliyor`, 'warn') : h('span'));
      if (!deck.active) meta.append(badge('Pasif', 'dim'));
      row.append(text, meta);
      row.addEventListener('click', () => openDeck(deck.id));
      list.append(row);
    }
    return card(`Desteler (${num(decks.length)})`, list);
  }

  async function openDeck(deckId) {
    const slot = h('div', 'ak-stack');
    deckSlot.replaceChildren(slot);
    try {
      const deck = await app.api.get(`/v1/students/${studentId}/flashcard-decks/${deckId}`);
      slot.append(card(deck.title, h('div', 'ak-card-pad ak-stack', null)));
      const body = slot.querySelector('.ak-card-pad');
      body.append(h('div', 'ak-muted', [deck.source === 'institution' ? 'Kurum destesi' : 'Öğrencinin kendi destesi',
        deck.assignedAt ? `atandı ${fmtDateTime(deck.assignedAt)}` : '',
        `${num(deck.cardCount)} kart · ${num(deck.dueCount)} tekrar bekliyor`].filter(Boolean).join(' · ')));
      if (deck.description) body.append(h('p', '', deck.description));
      const tools = h('div', 'ak-inline');
      tools.append(button('Kartlarını göster', { variant: 'ghost', onClick: () => {
        filters.deckId = deck.id; deckSelect.set(deck.id); cards.reload(); queue.reload();
      } }));
      if (deck.source === 'institution' && app.can('admin_getDeck')) {
        tools.append(button('Kurum destesini aç', { variant: 'ghost', onClick: () => app.go('decks', { deckId }) }));
      }
      body.append(tools);
    } catch (failure) {
      slot.replaceChildren(errorBox(failure, () => openDeck(deckId)));
    }
  }

  const deckSlot = h('div');
  const deckSelect = selectBox([{ value: '', label: 'Tüm desteler' }], '', (value) => {
    filters.deckId = value; cards.reload(); queue.reload();
  }, { label: 'Deste' });
  const sourceChips = chipRow([{ key: 'own', label: 'Kendi kartları' }, { key: 'institution', label: 'Kurum kartları' }],
    null, (key) => { filters.source = key || ''; cards.reload(); });

  function imageCell(image, label) {
    const wrap = h('span', 'ak-sfc-img');
    if (!image?.id) return wrap;
    const open = button(label, { variant: 'ghost', onClick: async () => {
      open.disabled = true;
      try {
        let url = image.url;
        if (!url || (image.urlExpiresAt && Date.parse(image.urlExpiresAt) < Date.now() + 15_000)) {
          url = (await app.api.get(`/v1/students/${studentId}/files/${image.id}`))?.url;
        }
        if (!url) throw new Error('Görsel gizli ya da silinmiş.');
        const { blob } = await app.api.fetchFile(url, 'kart');
        const objectUrl = URL.createObjectURL(blob);
        urls.add(objectUrl);
        const img = h('img', 'ak-sfc-thumb');
        img.alt = label;
        img.src = objectUrl;
        wrap.replaceChildren(img);
      } catch (failure) {
        open.disabled = false;
        app.toast(problem(failure, 'Görsel açılamadı.'), 'bad');
      }
    } });
    wrap.append(open);
    return wrap;
  }

  const cardColumns = [
    { key: 'front', label: 'Ön yüz', width: 'minmax(0, 1.4fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', 'ak-sfc-text', row.front), imageCell(row.frontImage, 'Görsel'));
      return cell;
    } },
    { key: 'back', label: 'Arka yüz', width: 'minmax(0, 1.6fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', 'ak-sfc-text', row.back), imageCell(row.backImage, 'Görsel'));
      return cell;
    } },
    { key: 'topic', label: 'Ders / konu', width: 'minmax(0, 1fr)', cell: (row) => {
      const cell = h('div', 'ak-cell-stack');
      cell.append(h('span', '', row.subject?.name || '—'), h('small', '', row.topic?.name || ''));
      return cell;
    } },
    { key: 'box', label: 'Kutu', align: 'num', width: '70px', cell: (row) => num(row.leitner?.box) },
    { key: 'due', label: 'Sıradaki tekrar', width: '120px', cell: (row) => fmtDate(row.leitner?.dueDate) },
    { key: 'score', label: 'Bildi / tekrar', align: 'num', width: '110px',
      cell: (row) => `${num(row.leitner?.knownCount)} / ${num(row.leitner?.reviewCount)}` },
    { key: 'source', label: '', width: '80px', cell: (row) => badge(...SOURCE[row.source]) },
  ];

  queue = pagedList({
    app,
    path: `/v1/students/${studentId}/flashcards/review-queue`,
    query: () => ({ deckId: filters.deckId || undefined }),
    columns: cardColumns,
    onRow: (row) => openCard(row),
    limit: 20,
    empty: { title: 'Bugün tekrar edilecek kart yok', text: 'Öğrenci bugünkü tekrarlarını bitirmiş ya da kartı yok.' },
  });

  cards = pagedList({
    app,
    path: `/v1/students/${studentId}/flashcards`,
    query: () => ({ deckId: filters.deckId || undefined, source: filters.source || undefined }),
    columns: cardColumns,
    onRow: (row) => openCard(row),
    empty: { title: 'Kart yok', text: 'Bu süzgece uyan kart bulunmadı.' },
  });

  // Kart ayrıntısı: tam metin, görseller, Leitner geçmişi.
  function openCard(row) {
    const panel = openDrawer(app, { title: 'Bilgi kartı' });
    const view = loader(() => app.api.get(`/v1/students/${studentId}/flashcards/${row.id}`), (item) => [
      card('Ön yüz', h('div', 'ak-card-pad ak-sfc-text', item.front)),
      item.frontImage ? imageCell(item.frontImage, 'Ön yüz görseli') : null,
      card('Arka yüz', h('div', 'ak-card-pad ak-sfc-text', item.back)),
      item.backImage ? imageCell(item.backImage, 'Arka yüz görseli') : null,
      card('', kv([
        ['Kaynak', item.source === 'institution' ? 'Kurum destesi' : 'Öğrencinin kendi kartı'],
        ['Ders / konu', [item.subject?.name, item.topic?.name].filter(Boolean).join(' · ') || '—'],
        ['Kutu', `${num(item.leitner?.box)}. kutu`],
        ['Sıradaki tekrar', fmtDate(item.leitner?.dueDate)],
        ['Son tekrar', fmtDateTime(item.leitner?.lastReviewedAt)],
        ['Bildi / tekrar', `${num(item.leitner?.knownCount)} / ${num(item.leitner?.reviewCount)}`],
        ['Eklendi', fmtDateTime(item.createdAt)],
      ])),
    ]);
    panel.body.append(view.node);
    view.reload();
  }

  const body = h('div', 'ak-stack');
  body.append(summary.node, deckSlot,
    card('Bugünkü tekrar kuyruğu', queue.node),
    card('Bütün kartlar', h('div', 'ak-stack ak-card-pad', null)));
  const allCards = body.lastChild.querySelector('.ak-card-pad');
  allCards.append(toolbar(deckSelect.node, sourceChips.node), cards.node);
  node.append(body);

  return {
    node,
    async shown() {
      if (started) return;
      started = true;
      // Bayrak kapalıysa bütün çağrılar aynı sebeple düşer: önce özet okunur
      // (uçuştaki aynı GET paylaşılır, ikinci istek gitmez); `feature_disabled`
      // gelirse sekme tek bir açıklamaya döner, kuyruk ve liste hiç istenmez.
      const probe = app.api.get(`/v1/students/${studentId}/flashcards/stats`);
      summary.reload();
      try {
        await probe;
      } catch (failure) {
        const notice = disabledNotice(app, failure);
        if (notice) { node.replaceChildren(notice); return; }
      }
      queue.reload();
      cards.reload();
    },
    destroy() {
      queue.destroy();
      cards.destroy();
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    },
  };
}
