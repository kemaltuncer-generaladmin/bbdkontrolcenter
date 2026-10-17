// BBD Akademi — Koçluk → Video havuzu (Akademi README §14 madde 105; bayrak `video_tasks`).
//
// Kurum geneli YouTube bağlantıları: öğretmen / koç / yönetici mobilde, web'de ya da burada bağlantı yapıştırır;
// başlık, kanal, süre ve küçük resim Akademi sunucusunda çözülür (API anahtarı yok — oEmbed). Görev verirken
// "Havuzdan seç" ile tek dokunuşla eklenir; kullanım sayısı Akademi'dedir.
//
// · Liste (`admin_listVideoPool`): arama (Türkçe harf duyarsız), ders, sıralama (en yeni / en çok kullanılan / ada
//   göre), "Eklediklerim". Satır → ayrıntı çekmecesi (video, konu, seviye, etiket, ekleyen, kullanım).
// · Video ekle (`admin_createVideoPoolItem`): bağlantı + isteğe bağlı başlık / not, ders + konu (müfredattan),
//   sınıf seviyesi, etiket. Kopya bağlantı "zaten havuzda" der.
// · Toplu ekle (`admin_bulkAddVideoPool`; KM en yetkili): her satıra bir bağlantı (boşluk / virgül de ayırır),
//   ortak etiketler; 50'şerlik istekler, satır başına sonuç (eklendi / zaten var / geçersiz / bulunamadı).
// · Düzenle / kaldır (`admin_updateVideoPoolItem`, `admin_deleteVideoPoolItem`): KM her öğeyi (yönetici kapsamı;
//   `coach_library.curate` / `video_pool.curate`). Kaldırılan video gönderilmiş görevleri etkilemez.
// · Oynatma listesi (Akademi README §14 madde 124): liste `?kind=all` ile birlikte listelenir ("Liste · 12 video");
//   "Liste ekle" (`admin_importPlaylistToVideoPool`) → önizleme → "Liste olarak ekle" / "Videoları ayrı ekle".
//   "Video ekle"ye `watch?v=…&list=` yapıştırılırsa sorulur: "Bu videoyu ekle · Listenin tamamını ekle".
//   Liste öğesinin ayrıntısı: sıralı videolar, "Listeyi yenile", "Öğrencilere ata".

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, skeletonRows } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import {
  GRADE_LEVELS, actionButton, confirmAction, errorBox, fmtDateTime, formDialog, gradeText, kv, multiChips, openDrawer,
  optionsOf, pagedList, runAction, searchBox, selectBox, toolbar,
} from '../common.js';
import { POOL_SORTS, clockText, poolBulkChunks, poolBulkSummary, poolTagsBody } from './coaching-logic.js';
import { videoCard } from './coaching-video.js';
import { parseYoutubeLink, singleVideoUrl, summaryLine } from './playlist-logic.js';

const POOL = '/v1/admin/video-pool';

export function tab(app) {
  const node = h('div', 'ak-stack');
  // `kind=all`: tek videolar ve listeler birlikte (sözleşme varsayılanı `video` — eski istemciler için).
  const filter = { q: '', subjectId: '', sort: 'recent', mine: '', kind: 'all' };
  const search = searchBox({ placeholder: 'Başlık, kanal, etiket ara', label: 'Havuzda ara',
    onSearch: (text) => { filter.q = text; list.reload(); } });
  const sort = selectBox(optionsOf(POOL_SORTS), 'recent', (value) => { filter.sort = value; list.reload(); }, { label: 'Sıralama' });
  const subjectSlot = h('div');
  app.lookups.options('subjects', { empty: 'Bütün dersler' }).then((options) => {
    subjectSlot.append(selectBox(options, '', (value) => { filter.subjectId = value; list.reload(); }, { label: 'Ders' }).node);
  }).catch(() => {});
  const add = actionButton(app, 'admin_createVideoPoolItem', 'Video ekle', { variant: 'primary',
    onClick: async () => {
      const saved = await itemDialog(app);
      if (saved?.playlistUrl) await importList(saved.playlistUrl);
      else if (saved) list.reload();
    } });
  const importList = async (url = '') => {
    const { playlistImportDialog } = await import('./playlist.js');
    const outcome = await playlistImportDialog(app, { target: 'pool', url });
    if (!outcome) return;
    if (outcome.single) {
      if (await itemDialog(app, null, { url: outcome.single.url, single: true })) list.reload();
      return;
    }
    if (outcome.existing) {
      openItem(app, { id: outcome.existing, title: 'Oynatma listesi' }, { onChange: () => list.reload() });
      return;
    }
    list.reload();
    const first = outcome.result?.items?.[0];
    if (outcome.result?.mode === 'playlist' && first) openItem(app, first, { onChange: () => list.reload() });
  };
  const addList = actionButton(app, 'admin_importPlaylistToVideoPool', 'Liste ekle', { variant: 'ghost',
    onClick: () => importList() });
  const bulk = actionButton(app, 'admin_bulkAddVideoPool', 'Toplu ekle', { variant: 'ghost',
    onClick: async () => { if (await bulkDialog(app)) list.reload(); } });
  node.append(alertBox('Kurumun video havuzu: öğretmenler görev verirken buradan tek dokunuşla seçer. Bağlantıyı yapıştırın; '
    + 'başlık, kanal ve süre kendiliğinden gelir.', 'info'),
  toolbar(search.node, subjectSlot, sort.node, add, addList, bulk));
  const list = pagedList({
    app,
    path: POOL,
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    onRow: (row) => openItem(app, row, { onChange: () => list.reload() }),
    columns: [
      { key: 'title', label: 'Video', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const list = row.kind === 'playlist' && row.playlist;
        cell.append(h('span', '', row.title), h('small', '', [list ? `Liste · ${summaryLine(row.playlist)}` : '',
          list ? row.playlist.channelTitle : row.video?.channelTitle,
          !list && row.video?.durationSeconds ? clockText(row.video.durationSeconds) : '',
          (row.topics || []).map((topic) => topic.name).join(', '),
          (row.tags || []).map((tag) => `#${tag}`).join(' ')].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'grades', label: 'Seviye', width: '140px', cell: (row) => (row.gradeLevels || []).map(gradeText).join(', ') || '—' },
      { key: 'addedBy', label: 'Ekleyen', width: '170px', cell: (row) => row.addedByName || '—' },
      { key: 'useCount', label: 'Kullanım', width: '110px', cell: (row) => (row.useCount ? `${row.useCount} kez` : 'Henüz yok') },
      { key: 'createdAt', label: 'Eklendi', width: '150px', cell: (row) => fmtDateTime(row.createdAt) },
    ],
    empty: { title: 'Havuzda video yok', text: '"Video ekle" ya da "Toplu ekle" ile YouTube bağlantılarını ekleyin.' },
  });
  node.append(list.node);
  let started = false;
  return {
    node,
    shown() { if (!started) { started = true; list.reload(); } },
    destroy() { list.destroy(); search.destroy(); },
  };
}

async function openItem(app, row, { onChange }) {
  const view = openDrawer(app, { title: row.title || 'Video', subtitle: 'Video havuzu' });
  view.body.replaceChildren(skeletonRows(6, 2));
  let item = row;
  const render = () => {
    const tools = h('div', 'ak-row-actions');
    tools.append(button('YouTube\'da aç', { variant: 'ghost', onClick: () => {
      if (item.kind === 'playlist' && item.playlist?.playlistId) {
        window.open(`https://www.youtube.com/playlist?list=${encodeURIComponent(item.playlist.playlistId)}`, '_blank', 'noopener');
        return;
      }
      const url = new URL(item.video.url);
      if (item.startSeconds) url.searchParams.set('t', String(item.startSeconds));
      window.open(url.toString(), '_blank', 'noopener');
    } }));
    if (item.canEdit !== false) {
      tools.append(actionButton(app, 'admin_updateVideoPoolItem', 'Düzenle', { variant: 'ghost', onClick: async () => {
        const saved = await itemDialog(app, item);
        if (saved) { item = saved; onChange?.(); render(); }
      } }),
      actionButton(app, 'admin_deleteVideoPoolItem', 'Havuzdan kaldır', { variant: 'ghost', onClick: async () => {
        if (!await confirmAction(app, { title: 'Video havuzdan kaldırılsın mı?',
          description: 'Gönderilmiş görevlerdeki video etkilenmez.', confirmLabel: 'Kaldır', danger: true })) return;
        const done = await runAction(app, () => app.api.del(`${POOL}/${item.id}`).then(() => true), { success: 'Havuzdan kaldırıldı.' });
        if (done) { view.close(); onChange?.(); }
      } }));
    }
    const listId = item.kind === 'playlist' ? item.playlist?.playlistId : null;
    const head = listId && app.can('admin_getVideoPlaylist') ? h('div') : videoCard({ ...item.video, title: item.title });
    if (head.childElementCount === 0 && listId) {
      // Oynatma listesi: sıralı videolar, "Listeyi yenile", "Öğrencilere ata" (README §14 madde 124).
      import('./playlist.js').then(({ playlistPanel }) => {
        head.append(playlistPanel(app, { playlistId: listId, onChange: () => onChange?.(),
          onAssign: (playlist) => import('./playlist-assign.js').then(({ assignPlaylistWizard }) => assignPlaylistWizard(app,
            { source: { videoPoolItemId: item.id }, playlist })) }).node);
      }).catch((failure) => head.append(alertBox(problem(failure), 'bad')));
    }
    view.body.replaceChildren(head, kv([
      item.startSeconds ? ['Başlangıç', clockText(item.startSeconds)] : null,
      item.note ? ['Not', item.note] : null,
      ['Konu', (item.topics || []).map((topic) => topic.name).join(', ') || '—'],
      ['Seviye', (item.gradeLevels || []).map(gradeText).join(', ') || '—'],
      ['Etiketler', (item.tags || []).join(', ') || '—'],
      ['Ekleyen', item.addedByName || '—'],
      ['Kullanım', item.useCount ? `${item.useCount} kez gönderildi` : 'Henüz gönderilmedi'],
      ['Eklendi', fmtDateTime(item.createdAt)],
    ]), tools);
  };
  try {
    item = { ...row, ...(await app.api.get(`/v1/admin/video-pool/${row.id}`)) };
    render();
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openItem(app, row, { onChange })));
  }
}

/** Ders + konu (müfredattan) — görev formunun ortak alanı (`task-form.js`). */
const topicField = (app, form) => ({ key: '_topic', label: 'Ders ve konu (müfredattan)', type: 'custom', wide: true,
  render: () => {
    const holder = h('div');
    import('../shared/task-form.js').then(({ subjectTopicField }) => {
      holder.append(subjectTopicField(app, { form, onChange: () => {} }).node);
    }).catch((failure) => holder.append(alertBox(problem(failure), 'bad')));
    return holder;
  } });

const gradeField = () => ({ key: 'gradeLevels', label: 'Sınıf seviyesi', type: 'custom', render: (draft, set) => multiChips(
  GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) })), (draft.gradeLevels || []).map(String),
  (value) => set(value)).node });

const tagsOf = (form, draft) => poolTagsBody({ subjectId: form.subjectId, topicIds: form.topicId ? [form.topicId] : [],
  gradeLevels: draft.gradeLevels, tags: draft.tags });

/** Video ekle / düzenle. Dönüş: kaydedilen öğe | null. */
export async function itemDialog(app, item = null, { url: startUrl = '', single = false } = {}) {
  const form = { subjectId: item?.subjectId || '', topicId: item?.topicIds?.[0] || '', topicName: item?.topics?.[0]?.name || '' };
  const canList = !single && app.can('admin_importPlaylistToVideoPool');
  const saved = await formDialog(app, {
    title: item ? 'Videoyu düzenle' : 'Havuza video ekle',
    description: item ? '' : 'YouTube bağlantısını yapıştırın; başlık, kanal, süre ve küçük resim kendiliğinden gelir.',
    wide: true,
    fields: [
      item ? null : { key: 'url', label: 'YouTube bağlantısı', required: true, maxLength: 2048, wide: true },
      item || !canList ? null : listChoiceField(),
      { key: 'title', label: item ? 'Başlık' : 'Başlık (boşsa videonun başlığı)', maxLength: 300, wide: true, required: Boolean(item) },
      { key: 'note', label: 'Not', type: 'textarea', maxLength: 500, wide: true },
      topicField(app, form),
      gradeField(),
      { key: 'tags', label: 'Etiketler (virgülle)', maxLength: 330 },
    ],
    value: { url: startUrl, title: item?.title || '', note: item?.note || '', tags: (item?.tags || []).join(', '),
      gradeLevels: (item?.gradeLevels || []).map(String), listChoice: 'video' },
    submitLabel: item ? 'Kaydet' : 'Ekle',
    onSubmit: (draft, { key }) => {
      if (!item && canList) {
        // Liste bağlantısı: yalnız liste (`playlist?list=`) ya da "Listenin tamamını ekle" → liste penceresi.
        const link = parseYoutubeLink(draft.url);
        if (link.playlistId && (!link.videoId || draft.listChoice === 'list')) return { playlistUrl: String(draft.url).trim() };
      }
      const tags = tagsOf(form, draft);
      const note = String(draft.note || '').trim() || null;
      const title = String(draft.title || '').trim();
      if (item) return app.api.put(`${POOL}/${item.id}`, { ...tags, title: title || item.title, note });
      const link = String(draft.url || '').trim();
      // "Bu videoyu ekle": listeye ait parametreler atılır (havuz tek video saklar).
      const url = parseYoutubeLink(link).playlistId && parseYoutubeLink(link).videoId ? singleVideoUrl(link) : link;
      return app.api.post(POOL, { ...tags, url, title: title || null, note }, { key })
        .catch((failure) => {
          if (failure?.code === 'video_pool_duplicate') throw new Error('Bu video havuzda zaten var.');
          throw failure;
        });
    },
  });
  if (saved && !saved.playlistUrl) app.toast(item ? 'Video güncellendi.' : 'Havuza eklendi.', 'good');
  return saved;
}

/**
 * `watch?v=…&list=` yapıştırılınca iki seçenek: "Bu videoyu ekle" · "Listenin tamamını ekle" (README §14 madde 124).
 * Yalnız liste bağlantısında (`playlist?list=`) "Ekle" liste penceresini açar.
 */
function listChoiceField() {
  return { key: 'listChoice', label: 'Oynatma listesi', type: 'custom', wide: true, render: (draft, set) => {
    const holder = h('div', 'ak-stack');
    let shown = '';
    const update = (next) => {
      const link = parseYoutubeLink(next.url);
      const state = link.playlistId ? (link.videoId ? 'both' : 'list') : '';
      if (state === shown) return;
      shown = state;
      holder.closest?.('.kit-field')?.toggleAttribute?.('hidden', !state);
      holder.replaceChildren();
      if (state === 'list') {
        holder.append(h('small', 'ak-muted', 'Bu bir oynatma listesi bağlantısı: “Ekle” liste önizlemesini açar '
          + '(liste olarak ya da videoları ayrı ekleyebilirsiniz).'));
        return;
      }
      if (state !== 'both') return;
      const group = h('div', 'ak-bt-modes');
      group.setAttribute('role', 'radiogroup');
      group.setAttribute('aria-label', 'Bağlantı bir oynatma listesindeki videoyu gösteriyor');
      for (const [key, label] of [['video', 'Bu videoyu ekle'], ['list', 'Listenin tamamını ekle']]) {
        const option = h('label', 'ak-bt-mode');
        const radio = h('input');
        radio.type = 'radio';
        radio.name = 'ak-pool-list-choice';
        radio.value = key;
        radio.checked = (next.listChoice || 'video') === key;
        radio.addEventListener('change', () => { if (radio.checked) set(key); });
        option.append(radio, h('span', '', label));
        group.append(option);
      }
      holder.append(h('small', 'ak-muted', 'Bağlantı bir oynatma listesindeki videoyu gösteriyor.'), group);
    };
    queueMicrotask(() => update(draft));
    return { node: holder, update };
  } };
}

/** Toplu ekle: her satıra bir bağlantı; 50'şerlik istekler. Dönüş: en az biri eklendiyse true. */
export async function bulkDialog(app) {
  const form = { subjectId: '', topicId: '', topicName: '' };
  const results = [];
  const done = await formDialog(app, {
    title: 'Havuza toplu video ekle',
    description: 'Her satıra bir YouTube bağlantısı yapıştırın (boşluk ya da virgül de ayırır). Etiketler hepsine uygulanır; '
      + 'havuzda zaten olan video atlanır.',
    wide: true,
    fields: [
      { key: 'urls', label: 'Bağlantılar', type: 'textarea', required: true, wide: true, maxLength: 60000 },
      topicField(app, form),
      gradeField(),
      { key: 'tags', label: 'Etiketler (virgülle)', maxLength: 330 },
    ],
    value: { urls: '', tags: '', gradeLevels: [] },
    submitLabel: 'Ekle',
    onSubmit: async (draft) => {
      const { chunks, count } = poolBulkChunks(draft.urls);
      if (!count) throw new Error('En az bir bağlantı yapıştırın.');
      const tags = tagsOf(form, draft);
      for (const urls of chunks) {
        const page = await app.api.post(`${POOL}/bulk`, { ...tags, urls });
        results.push(...(page.results || []));
      }
      return true;
    },
  });
  if (!done) return false;
  app.toast(poolBulkSummary(results), results.some((row) => row.status === 'added') ? 'good' : 'warn');
  const failed = results.filter((row) => !['added', 'duplicate'].includes(row.status));
  if (failed.length) {
    const view = openDrawer(app, { title: 'Eklenemeyen bağlantılar', subtitle: `${failed.length} satır` });
    const listNode = h('div', 'ak-stack');
    for (const row of failed) {
      const line = h('div', 'ak-coach-action');
      line.append(h('small', '', row.url), badge(row.status === 'not_found' ? 'Video bulunamadı'
        : row.status === 'unavailable' ? 'Şu an çözülemedi' : 'Geçersiz bağlantı', 'warn'));
      if (row.message) line.append(h('small', 'ak-muted', row.message));
      listNode.append(line);
    }
    view.body.replaceChildren(listNode);
  }
  return results.some((row) => row.status === 'added');
}

