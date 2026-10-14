// BBD Akademi — Koçluk → Kütüphane (F-K23 küratörlük, F-K9 video).
//
// · Kütüphane (`admin_listCoachLibrary`): kurum öğeleri bütün koçlara açık,
//   kişisel öğe yalnız sahibine; süzgeç kapsam, tür, ders, sınav, metin. Ayrıntı
//   (`admin_getCoachLibraryItem`): video meta verisi, konu ve etiketler, kullanım
//   (atanma, ortalama izlenme, durak sorusu doğruluğu — yalnız Akademi verisi).
// · Kurum öğesi ekle / düzenle / kaldır (`admin_createInstitutionLibraryItem`,
//   `admin_updateInstitutionLibraryItem`, `admin_deleteInstitutionLibraryItem`;
//   yalnız KM): YouTube videosu / kesiti / listesi (bağlantı sunucuda çözülür),
//   web bağlantısı, dosya (foto / PDF), metin notu, görev paketi.
// · Öneriler (`admin_listLibraryProposals`): koçların kuruma önerdiği kişisel
//   öğeler; onayda kurum kopyası açılır, koça bildirim (`admin_decideLibraryProposal`).

import { button, h } from '../../ui-kit/kit.js';
import { alertBox, chipRow, skeletonRows } from '../../ui-kit/layout.js';
import { problem } from './api.js';
import {
  CURRICULUM_EXAMS, GRADE_LEVELS, actionButton, confirmAction, errorBox, fileDrop, fmtDateTime, formDialog, gradeText, kv,
  multiChips, openDrawer, optionsOf, pagedList, runAction, searchBox, selectBox, statusBadge, toolbar,
} from './common.js';
import {
  LIBRARY_KINDS, LIBRARY_SCOPES, LIBRARY_STATUS, PROPOSAL_STATUS, clockText, libraryInput, pct, playlistIdOf,
} from './coaching-logic.js';
import { openFile } from './coaching-shared.js';
import { videoCard } from './coaching-video.js';

const LIBRARY = '/v1/admin/coach-library';

export function tab(app) {
  const node = h('div', 'ak-stack');
  const views = [
    app.can('admin_listCoachLibrary') && { key: 'items', label: 'Kütüphane' },
    app.can('admin_listLibraryProposals') && { key: 'proposals', label: 'Koç önerileri' },
  ].filter(Boolean);
  let view = views[0]?.key;
  const chips = chipRow(views, view, (key) => { view = key || view; chips.set(view); paint(); });
  const holder = h('div');
  const create = actionButton(app, 'admin_createInstitutionLibraryItem', 'Kuruma ekle', { variant: 'primary',
    onClick: async () => { if (await itemDialog(app)) items?.reload(); } });
  node.append(toolbar(chips.node, create), holder);
  let items = null;
  let proposals = null;
  function paint() {
    if (view === 'proposals') {
      proposals = proposals || proposalsView(app, () => items?.reload());
      holder.replaceChildren(proposals.node);
      proposals.start();
    } else {
      items = items || itemsView(app);
      holder.replaceChildren(items.node);
      items.start();
    }
  }
  let started = false;
  return {
    node,
    shown() { if (!started && view) { started = true; paint(); } },
    destroy() { items?.destroy(); proposals?.destroy(); },
  };
}

const usageText = (item) => {
  const usage = item.usage || {};
  return [`${usage.assignedCount ?? 0} atama`, usage.avgWatchPercent !== null && usage.avgWatchPercent !== undefined
    ? `izlenme ${pct(usage.avgWatchPercent)}` : '', usage.checkpointAccuracyPercent !== null && usage.checkpointAccuracyPercent !== undefined
    ? `durak ${pct(usage.checkpointAccuracyPercent)}` : ''].filter(Boolean).join(' · ');
};

const itemColumns = (extra = []) => [
  { key: 'title', label: 'Öğe', width: 'minmax(0, 2fr)', cell: (row) => {
    const cell = h('div', 'ak-cell-stack');
    cell.append(h('span', '', row.title), h('small', '', [row.video?.channelTitle, row.video?.durationSeconds
      ? clockText(row.video.durationSeconds) : '', (row.topics || []).map((topic) => topic.name).join(', '),
    (row.tags || []).map((tag) => `#${tag}`).join(' ')].filter(Boolean).join(' · ')));
    return cell;
  } },
  { key: 'kind', label: 'Tür', width: '170px', cell: (row) => LIBRARY_KINDS[row.kind] || row.kind },
  { key: 'scope', label: 'Kapsam', width: '150px', cell: (row) => (row.scope === 'institution' ? 'Kurum'
    : `Kişisel · ${row.owner?.displayName || ''}`) },
  { key: 'usage', label: 'Kullanım', width: '190px', cell: usageText },
  { key: 'status', label: 'Durum', width: '150px', cell: (row) => (row.status !== 'active' ? statusBadge(LIBRARY_STATUS, row.status)
    : (row.proposal?.status && row.proposal.status !== 'none' ? statusBadge(PROPOSAL_STATUS, row.proposal.status)
      : statusBadge(LIBRARY_STATUS, 'active'))) },
  ...extra,
];

function itemsView(app) {
  const node = h('div', 'ak-stack');
  const filter = { scope: 'institution', q: '', kind: '', subjectId: '', exam: '' };
  const scope = selectBox(optionsOf(LIBRARY_SCOPES), 'institution', (value) => { filter.scope = value; list.reload(); },
    { label: 'Kapsam' });
  const kind = selectBox(optionsOf(LIBRARY_KINDS, { empty: 'Bütün türler' }), '', (value) => { filter.kind = value; list.reload(); },
    { label: 'Tür' });
  const exam = selectBox(optionsOf(CURRICULUM_EXAMS, { empty: 'Bütün sınavlar' }), '', (value) => { filter.exam = value; list.reload(); },
    { label: 'Sınav' });
  const subjectSlot = h('div');
  app.lookups.options('subjects', { empty: 'Bütün dersler' }).then((options) => {
    subjectSlot.append(selectBox(options, '', (value) => { filter.subjectId = value; list.reload(); }, { label: 'Ders' }).node);
  }).catch(() => {});
  const search = searchBox({ placeholder: 'Başlık, etiket ara', onSearch: (text) => { filter.q = text; list.reload(); } });
  node.append(toolbar(search.node, scope.node, kind.node, subjectSlot, exam.node));
  const list = pagedList({
    app,
    path: LIBRARY,
    query: () => Object.fromEntries(Object.entries(filter).filter(([, value]) => value)),
    limit: 50,
    onRow: (row) => openItem(app, row, { onChange: () => list.reload() }),
    columns: itemColumns(),
    empty: { title: 'Kütüphanede öğe yok', text: '"Kuruma ekle" ile video, bağlantı, not ya da görev paketi ekleyin.' },
  });
  node.append(list.node);
  let started = false;
  return { node, start() { if (!started) { started = true; list.reload(); } }, reload: () => started && list.reload(),
    destroy() { list.destroy(); search.destroy(); } };
}

function proposalsView(app, onApproved) {
  const node = h('div', 'ak-stack');
  let status = 'pending';
  const chips = chipRow([{ key: 'pending', label: 'Bekleyen' }, { key: 'approved', label: 'Onaylanan' },
    { key: 'rejected', label: 'Reddedilen' }], status, (key) => { status = key || 'pending'; chips.set(status); list.reload(); });
  node.append(toolbar(chips.node));
  const list = pagedList({
    app,
    path: '/v1/admin/coach-library/proposals',
    query: () => ({ status }),
    onRow: (row) => openItem(app, row, { onChange: () => { list.reload(); onApproved?.(); } }),
    columns: itemColumns(),
    empty: { title: 'Öneri yok', text: 'Koçlar kişisel kütüphanelerinden kuruma öğe önerdiğinde burada görünür.' },
  });
  node.append(list.node);
  let started = false;
  return { node, start() { if (!started) { started = true; list.reload(); } }, destroy: () => list.destroy() };
}

async function openItem(app, row, { onChange }) {
  const view = openDrawer(app, { title: row.title || 'Kütüphane öğesi', subtitle: LIBRARY_KINDS[row.kind] || '' });
  view.body.replaceChildren(skeletonRows(6, 2));
  let item = row;
  const render = () => {
    const parts = [];
    if (item.video) parts.push(videoCard(item.video));
    parts.push(kv([
      ['Tür', LIBRARY_KINDS[item.kind] || item.kind],
      ['Kapsam', item.scope === 'institution' ? 'Kurum kütüphanesi' : `Kişisel (${item.owner?.displayName || '—'})`],
      item.startSeconds !== null && item.startSeconds !== undefined
        ? ['Kesit', `${clockText(item.startSeconds)} – ${clockText(item.endSeconds)}`] : null,
      item.url ? ['Bağlantı', item.url] : null,
      item.text ? ['Not', item.text] : null,
      item.tasks?.length ? ['Görevler', `${item.tasks.length} görev`] : null,
      ['Konular', (item.topics || []).map((topic) => topic.name).join(', ') || '—'],
      ['Etiketler', (item.tags || []).join(', ') || '—'],
      ['Sınav / seviye', [...(item.exams || []), ...(item.gradeLevels || []).map(gradeText)].join(', ') || '—'],
      ['Kullanım', usageText(item)],
      ['Durum', statusBadge(LIBRARY_STATUS, item.status)],
      item.proposal?.status && item.proposal.status !== 'none'
        ? ['Öneri', `${PROPOSAL_STATUS[item.proposal.status]?.[0] || item.proposal.status}${item.proposal.note ? ` — ${item.proposal.note}` : ''}`]
        : null,
      ['Eklendi', fmtDateTime(item.createdAt)],
    ]));
    if (item.status !== 'active') {
      parts.push(alertBox('Video YouTube\'da kaldırıldı ya da özele alındı; atanmış görevlerde oynatılamaz. Yerine yeni video seçin.', 'warn'));
    }
    const tools = h('div', 'ak-row-actions');
    if (item.file) tools.append(button('Dosyayı aç', { variant: 'ghost', onClick: () => openFile(app, item.file, item.title) }));
    if (item.proposal?.status === 'pending') {
      tools.append(actionButton(app, 'admin_decideLibraryProposal', 'Kuruma al (onayla)', { variant: 'primary',
        onClick: () => decide('approve') }),
      actionButton(app, 'admin_decideLibraryProposal', 'Reddet', { variant: 'ghost', onClick: () => decide('reject') }));
    }
    if (item.scope === 'institution') {
      tools.append(actionButton(app, 'admin_updateInstitutionLibraryItem', 'Düzenle', { variant: 'ghost', onClick: async () => {
        const saved = await itemDialog(app, item);
        if (saved) { item = saved; onChange?.(); render(); }
      } }),
      actionButton(app, 'admin_deleteInstitutionLibraryItem', 'Kaldır', { variant: 'ghost', onClick: async () => {
        if (!await confirmAction(app, { title: 'Kurum öğesini kaldır', description: 'Öğe kurum kütüphanesinden kalkar; '
          + 'önceden atanmış görevler etkilenmez.', confirmLabel: 'Kaldır', danger: true })) return;
        const done = await runAction(app, () => app.api.del(`${LIBRARY}/${item.id}`).then(() => true), { success: 'Öğe kaldırıldı.' });
        if (done) { view.close(); onChange?.(); }
      } }));
    }
    parts.push(tools);
    view.body.replaceChildren(...parts);
  };
  async function decide(decision) {
    const result = await formDialog(app, {
      title: decision === 'approve' ? 'Öneriyi onayla' : 'Öneriyi reddet',
      description: decision === 'approve' ? 'Öğenin kurum kopyası açılır; öneren koça bildirim gider.'
        : 'Öneren koça bildirim gider.',
      fields: [{ key: 'note', label: 'Not (koç görür)', type: 'textarea', maxLength: 300, wide: true }],
      value: { note: '' },
      submitLabel: decision === 'approve' ? 'Onayla' : 'Reddet',
      danger: decision === 'reject',
      onSubmit: (draft, { key }) => app.api.post(`${LIBRARY}/${item.id}/proposal-decision`,
        { decision, note: String(draft.note || '').trim() || null }, { key }),
    });
    if (!result) return;
    item = { ...item, ...result };
    app.toast(decision === 'approve' ? 'Öğe kurum kütüphanesine alındı.' : 'Öneri reddedildi.', 'good');
    onChange?.();
    render();
  }
  try {
    item = { ...row, ...(await app.api.get(`/v1/admin/coach-library/${row.id}`)) };
    render();
  } catch (failure) {
    view.body.replaceChildren(errorBox(failure, () => openItem(app, row, { onChange })));
  }
}

/** Kurum öğesi ekle / düzenle. Dönüş: kaydedilen öğe | null. */
export async function itemDialog(app, item = null) {
  const subjects = await app.lookups.options('subjects', { empty: 'Ders yok' }).catch(() => []);
  const state = { videoId: item?.videoId || null, playlistId: item?.playlistId || null, fileId: item?.fileId || null,
    video: item?.video || null, tasks: item?.tasks || [] };
  const resolver = (draft) => {
    const box = h('div', 'ak-stack');
    const input = h('input', 'kit-input');
    input.type = 'url';
    input.placeholder = 'YouTube video ya da liste bağlantısı';
    input.setAttribute('aria-label', 'YouTube bağlantısı');
    const result = h('div');
    if (state.video) result.append(videoCard(state.video));
    else if (state.playlistId) result.append(h('small', 'ak-muted', `Liste: ${state.playlistId}`));
    const go = button('Bağlantıyı çöz', { variant: 'ghost', onClick: async () => {
      const url = input.value.trim();
      if (!url) return;
      go.disabled = true;
      try {
        const listId = playlistIdOf(url);
        if (draft.kind === 'youtube_playlist') {
          if (!listId) throw new Error('Bağlantıda oynatma listesi (list=…) yok.');
          const page = await app.api.post('/v1/admin/videos/resolve-playlist', { playlistId: listId });
          state.playlistId = listId;
          state.videoId = null;
          result.replaceChildren(h('b', '', `${page.title} · ${page.itemCount} video`));
          if (!draft.title) draft.title = page.title;
        } else {
          const data = await app.api.post('/v1/admin/videos/resolve', { url });
          state.videoId = data.video.videoId;
          state.video = data.video;
          if (data.startSeconds && !draft.start) draft.start = clockText(data.startSeconds);
          result.replaceChildren(videoCard(data.video, { warnings: data.warnings }));
          if (!draft.title) draft.title = data.video.title.slice(0, 120);
        }
      } catch (failure) {
        result.replaceChildren(alertBox(problem(failure, 'Bağlantı çözülemedi.'), 'bad'));
      } finally {
        go.disabled = false;
      }
    } });
    const row = h('div', 'ak-toolbar');
    row.append(input, go);
    box.append(row, result);
    return box;
  };
  const uploader = () => {
    const box = h('div', 'ak-stack');
    if (!app.can('admin_uploadCoachingFile')) {
      box.append(h('small', 'ak-muted', 'Dosya yükleme yetkiniz yok.'));
      return box;
    }
    const status = h('small', 'ak-muted', state.fileId ? 'Dosya yüklü.' : '');
    const drop = fileDrop({ accept: ['pdf', 'png', 'jpg', 'jpeg', 'webp'], maxMb: 10, onFile: async (file) => {
      status.textContent = 'Yükleniyor…';
      try {
        const result = await app.api.call('POST', '/v1/admin/coaching/files', {
          upload: { file, field: 'file', fields: { kind: 'coach_attachment' } } });
        state.fileId = result.data?.id || null;
        status.textContent = state.fileId ? `Yüklendi: ${file.name}` : 'Yüklenemedi.';
      } catch (failure) {
        status.textContent = problem(failure, 'Dosya yüklenemedi.');
      }
    } });
    box.append(drop.node, status);
    return box;
  };
  const packEditor = () => {
    const box = h('div', 'ak-stack');
    import('./plan-tasks.js').then(({ taskListField, taskBody }) => {
      const field = taskListField(app, { mode: 'date', tasks: state.tasks.map((task) => ({ ...task })),
        onChange: (tasks) => { state.tasks = tasks.map((task) => taskBody(task, 'date')); } });
      box.append(field.node);
    }).catch((failure) => box.append(alertBox(problem(failure), 'bad')));
    return box;
  };
  const conditional = (kinds, label, render) => ({ key: `_${label}`, label, type: 'custom', wide: true,
    render: (draft) => {
      const holder = h('div');
      const paint = () => {
        holder.replaceChildren();
        holder.closest?.('.kit-field')?.toggleAttribute?.('hidden', !kinds.includes(draft.kind));
        if (kinds.includes(draft.kind)) holder.append(render(draft));
      };
      paint();
      let last = draft.kind;
      return { node: holder, update(next) { if (next.kind !== last) { last = next.kind; paint(); } } };
    } });
  return formDialog(app, {
    title: item ? 'Kurum öğesini düzenle' : 'Kurum kütüphanesine ekle',
    description: 'Kurum öğeleri bütün koçların kütüphanesinde görünür ve göreve eklenebilir.',
    wide: true,
    fields: [
      { key: 'kind', label: 'Tür', type: 'select', required: true, options: optionsOf(LIBRARY_KINDS) },
      { key: 'title', label: 'Başlık', required: true, maxLength: 120, wide: true },
      conditional(['youtube_video', 'youtube_clip', 'youtube_playlist'], 'YouTube', resolver),
      { key: 'start', label: 'Kesit başlangıcı (yalnız kesit; dd:ss)', maxLength: 8 },
      { key: 'end', label: 'Kesit bitişi (yalnız kesit; dd:ss)', maxLength: 8 },
      { key: 'url', label: 'Web bağlantısı (yalnız bağlantı)', maxLength: 2048, wide: true },
      conditional(['file'], 'Dosya', uploader),
      { key: 'text', label: 'Not metni (yalnız metin notu)', type: 'textarea', maxLength: 4000, wide: true },
      conditional(['task_pack'], 'Görevler', packEditor),
      { key: 'subjectId', label: 'Ders', type: 'select', options: subjects },
      { key: 'tags', label: 'Etiketler (virgülle)', maxLength: 330 },
      { key: 'exams', label: 'Sınav', type: 'custom', render: (draft, set) => multiChips(optionsOf(CURRICULUM_EXAMS),
        draft.exams || [], (value) => set(value)).node },
      { key: 'gradeLevels', label: 'Seviye', type: 'custom', render: (draft, set) => multiChips(GRADE_LEVELS.map((level) => ({
        value: String(level), label: gradeText(level) })), (draft.gradeLevels || []).map(String), (value) => set(value)).node },
    ],
    value: { kind: item?.kind || 'youtube_video', title: item?.title || '',
      start: item?.startSeconds !== null && item?.startSeconds !== undefined ? clockText(item.startSeconds) : '',
      end: item?.endSeconds ? clockText(item.endSeconds) : '', url: item?.url || '', text: item?.text || '',
      subjectId: item?.subjectId || '', tags: (item?.tags || []).join(', '), exams: item?.exams || [],
      gradeLevels: (item?.gradeLevels || []).map(String) },
    submitLabel: item ? 'Kaydet' : 'Ekle',
    onSubmit: (draft, { key }) => {
      const built = libraryInput({ ...draft, videoId: state.videoId, playlistId: state.playlistId, fileId: state.fileId,
        tasks: state.tasks, topicIds: item?.topicIds || [] });
      if (built.error) throw new Error(built.error);
      return item ? app.api.put(`${LIBRARY}/${item.id}`, built.body) : app.api.post(LIBRARY, built.body, { key });
    },
  }).then((saved) => {
    if (saved) app.toast(item ? 'Öğe güncellendi.' : 'Öğe kurum kütüphanesine eklendi.', 'good');
    return saved;
  });
}

