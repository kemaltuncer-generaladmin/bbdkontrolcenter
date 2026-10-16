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

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, badge, skeletonRows } from '../../ui-kit/layout.js';
import { problem } from './api.js';
import {
  GRADE_LEVELS, actionButton, confirmAction, errorBox, fmtDateTime, formDialog, gradeText, kv, multiChips, openDrawer,
  optionsOf, pagedList, runAction, searchBox, selectBox, toolbar,
} from './common.js';
import { POOL_SORTS, clockText, poolBulkChunks, poolBulkSummary, poolTagsBody } from './coaching-logic.js';
import { videoCard } from './coaching-video.js';

const POOL = '/v1/admin/video-pool';

export function tab(app) {
  const node = h('div', 'ak-stack');
  const filter = { q: '', subjectId: '', sort: 'recent', mine: '' };
  const search = searchBox({ placeholder: 'Başlık, kanal, etiket ara', label: 'Havuzda ara',
    onSearch: (text) => { filter.q = text; list.reload(); } });
  const sort = selectBox(optionsOf(POOL_SORTS), 'recent', (value) => { filter.sort = value; list.reload(); }, { label: 'Sıralama' });
  const subjectSlot = h('div');
  app.lookups.options('subjects', { empty: 'Bütün dersler' }).then((options) => {
    subjectSlot.append(selectBox(options, '', (value) => { filter.subjectId = value; list.reload(); }, { label: 'Ders' }).node);
  }).catch(() => {});
  const add = actionButton(app, 'admin_createVideoPoolItem', 'Video ekle', { variant: 'primary',
    onClick: async () => { if (await itemDialog(app)) list.reload(); } });
  const bulk = actionButton(app, 'admin_bulkAddVideoPool', 'Toplu ekle', { variant: 'ghost',
    onClick: async () => { if (await bulkDialog(app)) list.reload(); } });
  node.append(alertBox('Kurumun video havuzu: öğretmenler görev verirken buradan tek dokunuşla seçer. Bağlantıyı yapıştırın; '
    + 'başlık, kanal ve süre kendiliğinden gelir.', 'info'),
  toolbar(search.node, subjectSlot, sort.node, add, bulk));
  const list = pagedList({
    app,
    path: POOL,
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    onRow: (row) => openItem(app, row, { onChange: () => list.reload() }),
    columns: [
      { key: 'title', label: 'Video', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.title), h('small', '', [row.video?.channelTitle,
          row.video?.durationSeconds ? clockText(row.video.durationSeconds) : '', (row.topics || []).map((topic) => topic.name).join(', '),
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
    view.body.replaceChildren(videoCard({ ...item.video, title: item.title }), kv([
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
    import('./shared/task-form.js').then(({ subjectTopicField }) => {
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
export async function itemDialog(app, item = null) {
  const form = { subjectId: item?.subjectId || '', topicId: item?.topicIds?.[0] || '', topicName: item?.topics?.[0]?.name || '' };
  const saved = await formDialog(app, {
    title: item ? 'Videoyu düzenle' : 'Havuza video ekle',
    description: item ? '' : 'YouTube bağlantısını yapıştırın; başlık, kanal, süre ve küçük resim kendiliğinden gelir.',
    wide: true,
    fields: [
      item ? null : { key: 'url', label: 'YouTube bağlantısı', required: true, maxLength: 2048, wide: true },
      { key: 'title', label: item ? 'Başlık' : 'Başlık (boşsa videonun başlığı)', maxLength: 300, wide: true, required: Boolean(item) },
      { key: 'note', label: 'Not', type: 'textarea', maxLength: 500, wide: true },
      topicField(app, form),
      gradeField(),
      { key: 'tags', label: 'Etiketler (virgülle)', maxLength: 330 },
    ],
    value: { url: '', title: item?.title || '', note: item?.note || '', tags: (item?.tags || []).join(', '),
      gradeLevels: (item?.gradeLevels || []).map(String) },
    submitLabel: item ? 'Kaydet' : 'Ekle',
    onSubmit: (draft, { key }) => {
      const tags = tagsOf(form, draft);
      const note = String(draft.note || '').trim() || null;
      const title = String(draft.title || '').trim();
      if (item) return app.api.put(`${POOL}/${item.id}`, { ...tags, title: title || item.title, note });
      return app.api.post(POOL, { ...tags, url: String(draft.url || '').trim(), title: title || null, note }, { key })
        .catch((failure) => {
          if (failure?.code === 'video_pool_duplicate') throw new Error('Bu video havuzda zaten var.');
          throw failure;
        });
    },
  });
  if (saved) app.toast(item ? 'Video güncellendi.' : 'Havuza eklendi.', 'good');
  return saved;
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

