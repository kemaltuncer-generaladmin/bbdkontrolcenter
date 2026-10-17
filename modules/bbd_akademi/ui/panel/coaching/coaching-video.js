// BBD Akademi — Koçluk: video ekleme (F-K9) ve izlenme özeti (F-K10; bayrak `video_tasks`).
//
// Herhangi bir YouTube bağlantısı sunucuda çözülür (`admin_resolveVideoLink`;
// API anahtarı istemciye gitmez): başlık, kanal, süre, gömülebilirlik. Gömülemeyen
// / yaş sınırlı video uyarıyla gelir ("yalnız YouTube'da açılır"). Oynatma listesi
// (`list=`) sayfalı çözülür (`admin_resolvePlaylist`, sayfa 50) ve videolar seçilir; bütün liste "Liste görevi
// olarak ata…" ile liste atamasına gider (Akademi README §14 madde 124: tüm / seçili / güne yay, sınıf / grup).
// Kurum / kişisel kütüphaneden de seçilebilir (`libraryItemId`). Kesit (başlangıç
// – bitiş), bölüm işaretleri ve 0–3 durak sorusu eklenir.
//
// Video izlemek puan, seri ya da rozet kazandırmaz; izlenme yalnız Akademi'deki
// izleme kayıtlarından gelir (YouTube verisinden metrik türetilmez).

import { button, dialogA11y, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import { selectBox } from '../common.js';
import { VIDEO_WARNINGS, clockText, parseClock, playlistIdOf } from './coaching-logic.js';

/** Çözülmüş video kartı. */
export function videoCard(video, { warnings = [] } = {}) {
  const box = h('div', 'ak-coach-videocard');
  box.append(h('b', '', video?.title || 'Video'),
    h('small', 'ak-muted', [video?.channelTitle, video?.durationSeconds ? clockText(video.durationSeconds) : 'canlı / süresiz',
      video?.privacy === 'unlisted' ? 'liste dışı' : ''].filter(Boolean).join(' · ')));
  if (video && video.available === false) box.append(badge('Video kaldırıldı ya da özele alındı', 'bad'));
  if (video?.openInYouTubeOnly) box.append(badge('Yalnız YouTube\'da açılır', 'warn'));
  for (const warning of warnings) {
    if (warning === 'not_embeddable' && video?.openInYouTubeOnly) continue;
    box.append(h('small', 'ak-coach-warn', VIDEO_WARNINGS[warning] || warning));
  }
  return box;
}

/**
 * Video seçme penceresi. Dönüş: [{videoId, video, libraryItemId?, playlistId?, startSeconds, endSeconds,
 * chapters, checkpoints, requireOrder}] ya da vazgeçildiyse null.
 */
export function videoDialog(app, { title = 'Video ekle', max = 10 } = {}) {
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const box = h('div', 'kit-dialog ak-dialog wide');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    box.setAttribute('aria-label', title);
    box.append(h('h3', 'kit-dialog-title', title));
    const chosen = [];
    const chosenBox = h('div', 'ak-stack');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');

    const modes = [
      app.can('admin_resolveVideoLink') && { key: 'link', label: 'YouTube bağlantısı' },
      app.can('admin_listVideoPool') && { key: 'pool', label: 'Video havuzundan' },
      app.can('admin_listCoachLibrary') && { key: 'library', label: 'Kütüphaneden' },
    ].filter(Boolean);
    const pane = h('div', 'ak-stack');
    let mode = modes[0]?.key || 'link';
    const tabs = chipRow(modes, mode, (key) => { mode = key || mode; tabs.set(mode); paintPane(); });

    const paintChosen = () => {
      chosenBox.replaceChildren();
      if (!chosen.length) { chosenBox.append(h('p', 'ak-muted', 'Henüz video seçilmedi.')); return; }
      chosen.forEach((item, index) => chosenBox.append(segmentEditor(item, index, () => { chosen.splice(index, 1); paintChosen(); })));
    };
    const add = (item) => {
      if (chosen.length >= max) { error.textContent = `En çok ${max} video eklenir.`; return; }
      if (chosen.some((entry) => (entry.libraryItemId && entry.libraryItemId === item.libraryItemId)
        || (!entry.libraryItemId && !item.libraryItemId && entry.videoId === item.videoId))) return;
      chosen.push({ startSeconds: null, endSeconds: null, chapters: [], checkpoints: [], ...item });
      paintChosen();
    };

    function paintPane() {
      error.textContent = '';
      pane.replaceChildren(mode === 'library' ? libraryPane(app, add) : mode === 'pool' ? poolPane(app, add)
        : linkPane(app, add, error));
    }

    let release = () => {};
    const close = (result) => { document.removeEventListener('keydown', onKey); overlay.remove(); release(); resolve(result); };
    const onKey = (event) => { if (event.key === 'Escape') close(null); };
    const done = button('Ekle', { variant: 'primary', onClick: () => {
      for (const item of chosen) {
        if (item.startSeconds !== null && item.endSeconds !== null && item.endSeconds <= item.startSeconds) {
          error.textContent = 'Kesitte bitiş başlangıçtan sonra olmalı.';
          return;
        }
      }
      close(chosen.map((item, order) => ({ ...item, order })));
    } });
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), done);
    box.append(modes.length > 1 ? tabs.node : h('span'), pane, h('h4', 'ak-coach-sub', 'Göreve eklenecekler'), chosenBox, error,
      alertBox('Uygulamada izlenen saniyeler ölçülür; kesitin %90\'ı izlenince tamamlanır. Video izlemek puan, seri ya da '
        + 'rozet kazandırmaz.', 'info'), actions);
    overlay.append(box);
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(box);
    paintPane();
    paintChosen();
  });
}

function linkPane(app, add, error) {
  const node = h('div', 'ak-stack');
  const input = h('input', 'kit-input');
  input.type = 'url';
  input.placeholder = 'youtube.com/watch?v=… · youtu.be/… · /shorts/… · list=…';
  input.setAttribute('aria-label', 'YouTube bağlantısı');
  const result = h('div', 'ak-stack');
  const resolveButton = button('Bağlantıyı çöz', { variant: 'primary', onClick: async () => {
    error.textContent = '';
    const url = input.value.trim();
    if (!url) { error.textContent = 'Bağlantı yapıştırın.'; return; }
    resolveButton.disabled = true;
    result.replaceChildren(h('p', 'ak-muted', 'Çözülüyor…'));
    try {
      const listId = playlistIdOf(url);
      const parts = [];
      if (!/\/playlist\?/.test(url)) {
        const data = await app.api.post('/v1/admin/videos/resolve', { url });
        parts.push(videoCard(data.video, { warnings: data.warnings }),
          button('Bu videoyu ekle', { variant: 'ghost', onClick: () => add({ videoId: data.video.videoId, video: data.video,
            startSeconds: data.startSeconds ?? null, playlistId: data.playlistId || null }) }));
      }
      if (listId && app.can('admin_resolvePlaylist')) parts.push(playlistPane(app, listId, add));
      result.replaceChildren(...parts);
    } catch (failure) {
      result.replaceChildren(alertBox(problem(failure, 'Bağlantı çözülemedi.'), 'bad'));
    } finally {
      resolveButton.disabled = false;
    }
  } });
  const row = h('div', 'ak-toolbar');
  row.append(input, resolveButton);
  node.append(row, result);
  return node;
}

function playlistPane(app, playlistId, add) {
  const node = h('div', 'ak-stack');
  const list = h('div', 'ak-stack');
  let cursor = null;
  let first = true;
  const more = button('Daha fazla video', { variant: 'ghost', onClick: () => load() });
  const head = h('div', 'ak-toolbar');
  const requireOrder = h('label', 'ak-inline');
  const orderBox = h('input', 'kit-check');
  orderBox.type = 'checkbox';
  requireOrder.append(orderBox, h('span', '', 'Sıra zorunlu (önceki %90 izlenmeden sonraki kilitli)'));
  const addAll = button('Görünenlerin hepsini ekle', { variant: 'ghost', onClick: () => {
    for (const item of loaded) add({ videoId: item.videoId, video: item, playlistId, requireOrder: orderBox.checked });
  } });
  const loaded = [];
  node.append(head, list, more);
  async function load() {
    more.disabled = true;
    try {
      const page = await app.api.post('/v1/admin/videos/resolve-playlist', { playlistId, cursor });
      if (first) {
        head.append(h('b', '', `${page.title} · ${page.itemCount} video`), requireOrder, addAll);
        // Bütün liste (≤ 200 video, güne yayma, sınıf / grup) bu görevin 10 video sınırına sığmaz: liste ataması
        // (Akademi README §14 madde 124) ayrı pencerede — görev formu açık kalır.
        if (app.can('admin_createPlaylistAssignment') && app.can('admin_previewVideoPlaylist')) {
          head.append(button('Liste görevi olarak ata…', { variant: 'ghost', onClick: () => {
            import('./playlist-assign.js').then(({ assignPlaylistWizard }) => assignPlaylistWizard(app,
              { source: { playlistId } })).catch((failure) => app.toast(problem(failure), 'bad'));
          } }));
        }
        first = false;
      }
      for (const item of page.items || []) {
        loaded.push(item);
        const line = h('div', 'ak-coach-action');
        line.append(videoCard(item), button('Ekle', { variant: 'ghost', onClick: () => add({ videoId: item.videoId, video: item,
          playlistId, requireOrder: orderBox.checked }) }));
        list.append(line);
      }
      cursor = page.nextCursor || null;
      more.hidden = !cursor;
    } catch (failure) {
      list.append(alertBox(problem(failure, 'Oynatma listesi çözülemedi.'), 'bad'));
      more.hidden = true;
    } finally {
      more.disabled = false;
    }
  }
  load();
  return node;
}

function libraryPane(app, add) {
  const node = h('div', 'ak-stack');
  const list = h('div', 'ak-stack');
  const input = h('input', 'kit-input');
  input.type = 'search';
  input.placeholder = 'Kütüphanede ara';
  input.setAttribute('aria-label', 'Kütüphanede ara');
  let token = 0;
  const load = async () => {
    const mine = ++token;
    list.replaceChildren(h('p', 'ak-muted', 'Yükleniyor…'));
    try {
      const page = await app.api.get('/v1/admin/coach-library', { scope: 'all', q: input.value.trim() || undefined, limit: 30 });
      if (mine !== token) return;
      list.replaceChildren();
      const items = (page?.items || []).filter((item) => item.kind?.startsWith('youtube_') && item.kind !== 'youtube_playlist');
      if (!items.length) list.append(h('p', 'ak-muted', 'Kütüphanede video yok.'));
      for (const item of items) {
        const line = h('div', 'ak-coach-action');
        line.append(videoCard(item.video || { title: item.title }), badge(item.scope === 'institution' ? 'Kurum' : 'Kişisel', 'info'),
          button('Ekle', { variant: 'ghost', onClick: () => add({ libraryItemId: item.id, videoId: item.videoId, video: item.video,
            startSeconds: item.startSeconds ?? null, endSeconds: item.endSeconds ?? null }) }));
        list.append(line);
      }
    } catch (failure) {
      list.replaceChildren(alertBox(problem(failure), 'bad'));
    }
  };
  input.addEventListener('change', load);
  node.append(input, list);
  load();
  return node;
}

/** Video havuzundan seç (Akademi README §14 madde 105): en çok kullanılanlar önce, arama Türkçe harf duyarsız. */
function poolPane(app, add) {
  const node = h('div', 'ak-stack');
  const list = h('div', 'ak-stack');
  const input = h('input', 'kit-input');
  input.type = 'search';
  input.placeholder = 'Havuzda ara (başlık, kanal, etiket)';
  input.setAttribute('aria-label', 'Havuzda ara');
  let token = 0;
  const load = async () => {
    const mine = ++token;
    list.replaceChildren(h('p', 'ak-muted', 'Yükleniyor…'));
    try {
      const page = await app.api.get('/v1/admin/video-pool', { q: input.value.trim() || undefined, sort: 'popular', limit: 30 });
      if (mine !== token) return;
      list.replaceChildren();
      const items = page?.items || [];
      if (!items.length) list.append(h('p', 'ak-muted', 'Havuzda video yok. Koçluk → Video havuzu\'ndan ekleyin.'));
      for (const item of items) {
        const line = h('div', 'ak-coach-action');
        line.append(videoCard({ ...item.video, title: item.title }), badge(`${item.useCount || 0} kez`, 'info'),
          button('Ekle', { variant: 'ghost', onClick: () => add({ videoPoolItemId: item.id, videoId: item.video.videoId,
            video: item.video, startSeconds: item.startSeconds ?? null }) }));
        list.append(line);
      }
    } catch (failure) {
      list.replaceChildren(alertBox(problem(failure), 'bad'));
    }
  };
  input.addEventListener('change', load);
  node.append(input, list);
  load();
  return node;
}

/** Seçilen video satırı: kesit, bölüm işareti, durak soruları. */
function segmentEditor(item, index, onRemove) {
  const node = h('div', 'ak-coach-segment');
  node.append(videoCard(item.video || { title: item.videoId || 'Kütüphane öğesi' }));
  const clock = (value, label, onChange) => {
    const input = h('input', 'kit-input ak-coach-num');
    input.type = 'text';
    input.placeholder = 'dd:ss';
    input.value = value === null || value === undefined ? '' : clockText(value);
    input.setAttribute('aria-label', label);
    input.addEventListener('change', () => {
      const parsed = parseClock(input.value);
      input.classList.toggle('bad', Boolean(input.value.trim()) && parsed === null);
      onChange(parsed);
    });
    return input;
  };
  const row = h('div', 'ak-toolbar');
  row.append(h('span', 'ak-muted', 'Kesit'), clock(item.startSeconds, `${index + 1}. video başlangıç`, (value) => { item.startSeconds = value; }),
    h('span', 'ak-muted', '–'), clock(item.endSeconds, `${index + 1}. video bitiş`, (value) => { item.endSeconds = value; }),
    button('Çıkar', { variant: 'ghost', onClick: onRemove }));
  node.append(row);
  const chapters = h('textarea', 'kit-textarea');
  chapters.placeholder = 'Bölüm işaretleri — her satır "07:20 türev tanımı"';
  chapters.setAttribute('aria-label', `${index + 1}. video bölüm işaretleri`);
  chapters.value = (item.chapters || []).map((chapter) => `${clockText(chapter.atSeconds)} ${chapter.label}`).join('\n');
  chapters.addEventListener('change', () => {
    item.chapters = chapters.value.split('\n').map((line) => line.trim()).filter(Boolean).slice(0, 20).map((line) => {
      const [time, ...rest] = line.split(/\s+/);
      const at = parseClock(time);
      return at === null ? null : { atSeconds: at, label: rest.join(' ').slice(0, 80) || 'Bölüm' };
    }).filter(Boolean);
  });
  node.append(chapters);
  const checkpoints = h('div', 'ak-stack');
  const addCheckpoint = button('Durak sorusu ekle', { variant: 'ghost', onClick: () => {
    if (item.checkpoints.length >= 3) return;
    item.checkpoints.push({ atSeconds: null, prompt: '', kind: 'single_choice', options: ['', ''], correctIndex: 0 });
    paint();
  } });
  const paint = () => {
    checkpoints.replaceChildren();
    item.checkpoints.forEach((checkpoint, position) => {
      const line = h('div', 'ak-coach-qedit');
      const prompt = h('input', 'kit-input');
      prompt.type = 'text';
      prompt.maxLength = 300;
      prompt.value = checkpoint.prompt;
      prompt.placeholder = 'Soru';
      prompt.setAttribute('aria-label', `Durak sorusu ${position + 1}`);
      prompt.addEventListener('input', () => { checkpoint.prompt = prompt.value; });
      const at = clock(checkpoint.atSeconds, `Durak ${position + 1} zamanı (boş = sonda)`, (value) => { checkpoint.atSeconds = value; });
      const kind = selectBox([{ value: 'single_choice', label: 'Tek seçim' }, { value: 'short_text', label: 'Kısa yanıt' }],
        checkpoint.kind, (value) => { checkpoint.kind = value; paint(); }, { label: `Durak ${position + 1} türü` });
      line.append(prompt, at, kind.node);
      if (checkpoint.kind === 'single_choice') {
        const options = h('textarea', 'kit-textarea');
        options.placeholder = 'Seçenekler (her satıra bir; ilk satır doğru cevap)';
        options.setAttribute('aria-label', `Durak ${position + 1} seçenekleri`);
        options.value = (checkpoint.options || []).join('\n');
        options.addEventListener('change', () => {
          checkpoint.options = options.value.split('\n').map((text) => text.trim()).filter(Boolean).slice(0, 5)
            .map((text) => text.slice(0, 120));
          checkpoint.correctIndex = 0;
        });
        line.append(options);
      }
      line.append(button('Kaldır', { variant: 'ghost', onClick: () => { item.checkpoints.splice(position, 1); paint(); } }));
      checkpoints.append(line);
    });
    addCheckpoint.disabled = item.checkpoints.length >= 3;
  };
  paint();
  node.append(checkpoints, addCheckpoint);
  return node;
}

