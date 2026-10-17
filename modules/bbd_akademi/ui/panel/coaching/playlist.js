// BBD Akademi — YouTube oynatma listesi: ekle (önizleme → "Liste olarak ekle" / "Videoları ayrı ekle") ve liste
// ayrıntısı (sıralı videolar, durum rozetleri, "Listeyi yenile" + fark). Akademi README §14 madde 124.
//
// · Önizleme (`admin_previewVideoPlaylist`): bağlantı (`playlist?list=` ya da `watch?v=…&list=`) → liste adı, kanal,
//   video sayısı, toplam süre, durum sayıları (gömülemeyen / özel / silinmiş), bütün videolar (≤ 200). İzleme
//   bağlantısında önce sorulur: "Bu videoyu ekle · Listenin tamamını ekle". Yazmaz.
// · Ekle: kurum kütüphanesine (`admin_importPlaylistToInstitutionLibrary`, yalnız KM) ya da video havuzuna
//   (`admin_importPlaylistToVideoPool`). Konu / ders / seviye / etiket listeye toplu; video başına "Etiketle".
//   Aynı liste ikinci kez `409 playlist_already_added` → eklenmiş öğe açılır.
// · Ayrıntı (`admin_getVideoPlaylist`, YouTube'a gitmez) + "Listeyi yenile" (`admin_refreshVideoPlaylist`; sunucunun
//   "3 yeni video, 1 kaldırıldı" metni gösterilir). Listeden çıkarılan video "YouTube'dan kaldırıldı" rozetiyle kalır.
// · Anahtarsız kurulum: `422 youtube_api_key_required` → açıklama + bağlantıdaki tek video için "Bu videoyu ekle";
//   Ayarlar yetkisi olana "Ayarlar → Entegrasyonlar".
//
// Saf mantık `playlist-logic.js`'tedir (Node ile sınanır).

import { button, dialogA11y, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge } from '../../../ui-kit/layout.js';
import { problem } from '../api.js';
import {
  CURRICULUM_EXAMS, GRADE_LEVELS, actionButton, errorBox, fmtDate, fmtDateTime, formDialog, gradeText, multiChips,
  optionsOf, runAction,
} from '../common.js';
import {
  KEY_REQUIRED_TEXT, PLAYLIST_STATUS, assignableVideos, clock, countsOf, countsText, diffText, importBody, importSummary,
  parseRange, parseYoutubeLink, previewBody, rangeIds, selectionOf, selectionText, singleVideoUrl, summaryLine,
} from './playlist-logic.js';

export const PLAYLISTS = '/v1/admin/video-playlists';
const IMPORT = {
  library: { path: '/v1/admin/coach-library/import-playlist', op: 'admin_importPlaylistToInstitutionLibrary',
    existing: 'existingLibraryItemId', where: 'kurum kütüphanesinde' },
  pool: { path: '/v1/admin/video-pool/import-playlist', op: 'admin_importPlaylistToVideoPool',
    existing: 'existingVideoPoolItemId', where: 'video havuzunda' },
};

/** Stiller (pencere koçluk dışından — görev formundan — da açılır). */
export function playlistStyles() {
  loadStyles(import.meta.url, 'playlist.css');
}

// ------------------------------------------------------------------ video satırları

/**
 * Sıralı video listesi. `picked` (Set) verilirse atanabilir videolarda onay kutusu çıkar.
 * `extra(video)` → satırın sonuna düğüm (ör. "Etiketle"); `readonly()` true iken kutular kapalı. Dönüş {node, paint()}.
 */
export function videoRows({ videos, picked = null, onToggle, extra, removed = [], readonly = null } = {}) {
  const node = h('div', 'ak-pl-rows');
  node.setAttribute('role', 'list');
  const paint = () => {
    node.replaceChildren();
    for (const item of videos || []) {
      const row = h('div', `ak-pl-row${PLAYLIST_STATUS[item.status] && item.status !== 'playable' ? ` ${item.status}` : ''}`);
      row.setAttribute('role', 'listitem');
      const title = item.video?.title || item.videoId;
      if (picked) {
        const box = h('input', 'kit-check');
        box.type = 'checkbox';
        const assignable = assignableVideos([item]).length > 0;
        box.disabled = !assignable || Boolean(readonly?.());
        box.checked = assignable && picked.has(item.videoId);
        box.setAttribute('aria-label', `${item.position}. ${title}`);
        box.addEventListener('change', () => {
          if (box.checked) picked.add(item.videoId); else picked.delete(item.videoId);
          onToggle?.();
        });
        row.append(box);
      }
      const main = h('div', 'ak-pl-main');
      main.append(h('span', 'ak-pl-title', `${item.position}. ${title}`),
        h('small', 'ak-muted', [item.video?.channelTitle, item.video?.durationSeconds ? clock(item.video.durationSeconds)
          : (item.status === 'playable' || item.status === 'not_embeddable' ? 'süre yok' : '')].filter(Boolean).join(' · ')));
      row.append(main);
      if (item.status !== 'playable' && PLAYLIST_STATUS[item.status]) {
        row.append(badge(PLAYLIST_STATUS[item.status][0], PLAYLIST_STATUS[item.status][1]));
      }
      const tail = extra?.(item);
      if (tail) row.append(tail);
      node.append(row);
    }
    for (const item of removed || []) {
      const row = h('div', 'ak-pl-row removed');
      row.setAttribute('role', 'listitem');
      const main = h('div', 'ak-pl-main');
      main.append(h('span', 'ak-pl-title', item.title || item.videoId),
        h('small', 'ak-muted', `Listeden çıkarıldı · ${fmtDate(String(item.removedAt || '').slice(0, 10))}`));
      row.append(main, badge('YouTube\'dan kaldırıldı', 'warn'));
      node.append(row);
    }
    if (!(videos || []).length && !(removed || []).length) node.append(h('p', 'ak-muted', 'Listede video yok.'));
  };
  paint();
  return { node, paint };
}

/**
 * Seçim çubuğu: "Tümünü seç", "Hiçbiri", aralık ("3–7"), seçimin özeti (sayı + toplam süre).
 * Aralık seçili kimliklere eklenir (onay kutuları da işaretlenir). Dönüş {node, paint()}.
 */
export function selectionBar({ videos, picked, onChange }) {
  const node = h('div', 'ak-pl-select');
  const summary = h('span', 'ak-pl-sum');
  summary.setAttribute('aria-live', 'polite');
  const range = h('input', 'kit-input ak-pl-range');
  range.type = 'text';
  range.placeholder = '3–7';
  range.setAttribute('aria-label', 'Aralık (ör. 3–7)');
  const rangeError = h('small', 'ak-pl-error');
  rangeError.setAttribute('role', 'alert');
  const last = Math.max(0, ...(videos || []).map((video) => video.position || 0));
  const applyRange = () => {
    rangeError.textContent = '';
    const parsed = parseRange(range.value, last || 1);
    if (parsed.error) { rangeError.textContent = parsed.error; return; }
    if (!parsed.from) return;
    for (const id of rangeIds(videos, parsed.from, parsed.to)) picked.add(id);
    onChange?.();
  };
  range.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); applyRange(); }
  });
  const all = button('Tümünü seç', { variant: 'ghost', onClick: () => {
    for (const video of assignableVideos(videos)) picked.add(video.videoId);
    onChange?.();
  } });
  const none = button('Hiçbiri', { variant: 'ghost', onClick: () => { picked.clear(); onChange?.(); } });
  const rangeGo = button('Aralığı seç', { variant: 'ghost', onClick: applyRange });
  const rangeBox = h('span', 'ak-inline');
  rangeBox.append(h('span', 'ak-muted', 'Aralık'), range, rangeGo);
  node.append(all, none, rangeBox, summary, rangeError);
  const paint = () => { summary.textContent = selectionText(selectionOf(videos, [...picked])); };
  paint();
  return { node, paint };
}

/** Liste başlık kartı: ad, kanal, "12 video · 1 sa 40 dk", durum sayıları, kırpılma. */
export function playlistHead(playlist, counts = null) {
  const box = h('div', 'ak-pl-head');
  box.append(h('b', 'ak-pl-name', playlist.title || 'Oynatma listesi'),
    h('small', 'ak-muted', [playlist.channelTitle, summaryLine(playlist)].filter(Boolean).join(' · ')));
  const bad = countsText(counts || countsOf(playlist.videos));
  if (bad) box.append(h('small', 'ak-pl-warn', bad));
  if (playlist.truncated) {
    box.append(alertBox(`Liste ${playlist.itemCount} videodan uzun; ilk 200 video alındı.`, 'warn'));
  }
  return box;
}

// ------------------------------------------------------------------ anahtarsız durum

/** `youtube_api_key_required` kutusu: açıklama + (varsa) "Bu videoyu ekle" + yöneticiye Ayarlar. */
function keyRequiredBox(app, { videoId, onSingle }) {
  const box = h('div', 'ak-stack');
  box.append(alertBox(KEY_REQUIRED_TEXT, 'warn'));
  const tools = h('div', 'ak-row-actions');
  if (videoId && onSingle) tools.append(button('Bu videoyu ekle', { variant: 'primary', onClick: onSingle }));
  if (app.can('admin_putYoutubeIntegration')) {
    tools.append(button('Ayarlar → Entegrasyonlar', { variant: 'ghost',
      onClick: () => app.go?.('settings', { tab: 'integrations' }) }));
  }
  if (tools.childElementCount) box.append(tools);
  return box;
}

// ------------------------------------------------------------------ ekle

/**
 * Oynatma listesi ekleme penceresi.
 * @param {object} app
 * @param {object} options  target: 'library' (kurum kütüphanesi) | 'pool' (video havuzu); url: başlangıç bağlantısı
 * @returns {Promise<{result}|{single: {url, videoId}}|{existing: string}|null>}
 *   result: içe aktarma sonucu · single: kullanıcı yalnız bağlantıdaki videoyu seçti (çağıran tek video penceresini
 *   açar) · existing: liste zaten ekli (çağıran o öğeyi açar)
 */
export function playlistImportDialog(app, { target = 'library', url = '' } = {}) {
  playlistStyles();
  const spec = IMPORT[target] || IMPORT.library;
  return new Promise((resolve) => {
    const overlay = h('div', 'kit-overlay');
    const card = h('div', 'kit-dialog ak-dialog wide ak-pl-dialog');
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-modal', 'true');
    card.append(h('h3', 'kit-dialog-title', target === 'pool' ? 'Havuza oynatma listesi ekle' : 'Kuruma oynatma listesi ekle'),
      h('p', 'kit-dialog-text', 'YouTube oynatma listesi bağlantısını yapıştırın; liste adı, video sayısı ve toplam süre '
        + 'kendiliğinden gelir.'));

    const input = h('input', 'kit-input');
    input.type = 'url';
    input.placeholder = 'youtube.com/playlist?list=… · watch?v=…&list=…';
    input.setAttribute('aria-label', 'Oynatma listesi bağlantısı');
    input.value = url;
    const previewButton = button('Önizle', { variant: 'primary', onClick: () => load() });
    const linkRow = h('div', 'ak-toolbar');
    linkRow.append(input, previewButton);
    const status = h('div', 'ak-pl-status');
    status.setAttribute('aria-live', 'polite');
    const body = h('div', 'ak-stack');
    const error = h('div', 'kit-dialog-error');
    error.setAttribute('role', 'alert');
    const submit = button('Liste olarak ekle', { variant: 'primary', onClick: () => save() });
    submit.hidden = true;
    const actions = h('div', 'kit-dialog-actions');
    actions.append(button('Vazgeç', { onClick: () => close(null) }), submit);
    card.append(linkRow, status, body, error, actions);

    const state = {
      preview: null, whole: false, mode: 'playlist', picked: new Set(), overrides: {},
      form: { subjectId: '', topicId: '', topicName: '' }, gradeLevels: [], exams: [], tags: '', title: '', description: '',
    };
    let release = () => {};
    let token = 0;

    function close(result) {
      document.removeEventListener('keydown', onKey);
      overlay.remove();
      release();
      resolve(result);
    }
    const onKey = (event) => { if (event.key === 'Escape') close(null); };

    const single = (videoId) => close({ single: { url: singleVideoUrl(input.value, videoId), videoId } });

    async function load() {
      error.textContent = '';
      if (!app.can('admin_previewVideoPlaylist')) {
        status.replaceChildren(alertBox('Oynatma listesi önizleme yetkiniz yok (yöneticinizden izin isteyin).', 'bad'));
        return;
      }
      const built = previewBody(input.value);
      if (built.error) { status.replaceChildren(alertBox(built.error, 'bad')); return; }
      const mine = ++token;
      previewButton.disabled = true;
      submit.hidden = true;
      body.replaceChildren();
      status.replaceChildren(h('span', 'ak-muted', 'Liste okunuyor…'));
      try {
        const data = await app.api.post(`${PLAYLISTS}/preview`, built.body);
        if (mine !== token) return;
        state.preview = data;
        state.whole = !data.linkVideoId;
        state.picked = new Set(assignableVideos(data.videos).map((video) => video.videoId));
        state.overrides = {};
        state.title = '';
        status.replaceChildren();
        paint();
      } catch (failure) {
        if (mine !== token) return;
        state.preview = null;
        if (failure?.code === 'youtube_api_key_required') {
          const videoId = failure.details?.videoId || parseYoutubeLink(input.value).videoId;
          status.replaceChildren(keyRequiredBox(app, { videoId, onSingle: () => single(videoId) }));
        } else {
          status.replaceChildren(alertBox(problem(failure, 'Oynatma listesi okunamadı.'), 'bad'));
        }
      } finally {
        if (mine === token) previewButton.disabled = false;
      }
    }

    function paint() {
      const data = state.preview;
      body.replaceChildren();
      submit.hidden = true;
      if (!data) return;
      // İzleme bağlantısı (watch?v=…&list=): önce hangisi?
      if (!state.whole) {
        const ask = h('div', 'ak-pl-ask');
        const position = data.linkVideoPosition ? ` (${data.linkVideoPosition}. video)` : '';
        ask.append(h('span', '', `Bağlantı “${data.title}” listesindeki bir videoyu gösteriyor${position}.`));
        const tools = h('div', 'ak-row-actions');
        tools.append(button('Bu videoyu ekle', { onClick: () => single(data.linkVideoId) }),
          button('Listenin tamamını ekle', { variant: 'primary', onClick: () => { state.whole = true; paint(); } }));
        ask.append(tools);
        body.append(playlistHead(data, data.counts), ask);
        return;
      }
      const existing = data[spec.existing];
      body.append(playlistHead(data, data.counts));
      if (existing) {
        const note = h('div', 'ak-pl-ask');
        note.append(h('span', '', `Bu liste ${spec.where} zaten var. Yeni videoları görmek için “Listeyi yenile”yi kullanın.`),
          button('Eklenmiş listeyi aç', { variant: 'primary', onClick: () => close({ existing }) }));
        body.append(note);
      }

      // Ekleme biçimi
      const modes = h('div', 'ak-bt-modes');
      modes.setAttribute('role', 'radiogroup');
      modes.setAttribute('aria-label', 'Ekleme biçimi');
      for (const [key, label, hint] of [
        ['playlist', 'Liste olarak ekle', 'Tek öğe; videolar sırasıyla saklanır, “Listeyi yenile” ile güncellenir.'],
        ['videos', 'Videoları ayrı ekle', 'Seçtiğiniz her video ayrı öğe olur.'],
      ]) {
        const option = h('label', 'ak-bt-mode');
        const radio = h('input');
        radio.type = 'radio';
        radio.name = 'ak-pl-mode';
        radio.value = key;
        radio.checked = state.mode === key;
        radio.addEventListener('change', () => { if (radio.checked) { state.mode = key; paint(); } });
        option.append(radio, h('span', '', label));
        option.title = hint;
        modes.append(option);
      }
      body.append(modes, h('small', 'ak-muted', state.mode === 'playlist'
        ? 'Tek öğe; videolar sırasıyla saklanır, “Listeyi yenile” ile güncellenir.'
        : 'Seçtiğiniz her video ayrı öğe olur; konu ve etiketler hepsine uygulanır, “Etiketle” ile video başına değişir.'));

      body.append(commonFields());

      // Videolar
      const listTitle = h('h4', 'ak-coach-sub', state.mode === 'videos' ? 'Eklenecek videolar' : 'Listedeki videolar');
      let bar = null;
      const rows = videoRows({
        videos: data.videos,
        picked: state.mode === 'videos' ? state.picked : null,
        onToggle: () => { bar?.paint(); paintSubmit(); },
        extra: (video) => {
          if (!assignableVideos([video]).length) return null;
          const own = state.overrides[video.videoId];
          const tag = button(own ? 'Etiketli ✓' : 'Etiketle', { variant: 'ghost', onClick: async () => {
            const next = await overrideDialog(app, video, own, state.mode);
            if (next === undefined) return;
            if (next) state.overrides[video.videoId] = next; else delete state.overrides[video.videoId];
            rows.paint();
          } });
          tag.setAttribute('aria-label', `${video.position}. videonun etiketleri`);
          return tag;
        },
      });
      if (state.mode === 'videos') {
        bar = selectionBar({ videos: data.videos, picked: state.picked, onChange: () => { rows.paint(); bar.paint(); paintSubmit(); } });
        body.append(listTitle, bar.node, rows.node);
      } else {
        body.append(listTitle, rows.node);
      }
      paintSubmit();
    }

    function paintSubmit() {
      submit.hidden = !state.preview || !state.whole;
      const count = selectionOf(state.preview?.videos || [], [...state.picked]).count;
      submit.textContent = state.mode === 'playlist' ? 'Liste olarak ekle' : `Videoları ayrı ekle (${count})`;
      submit.disabled = state.mode === 'videos' && !count;
    }

    function commonFields() {
      const box = h('div', 'ak-pl-fields');
      const field = (label, control, wide = false) => {
        const wrap = h('label', `kit-field${wide ? ' kit-field-wide' : ''}`);
        wrap.append(h('span', 'kit-field-label', label), control);
        return wrap;
      };
      if (state.mode === 'playlist') {
        const title = h('input', 'kit-input');
        title.maxLength = 120;
        title.placeholder = state.preview.title;
        title.value = state.title;
        title.addEventListener('input', () => { state.title = title.value; });
        box.append(field('Başlık (boşsa liste adı)', title, true));
      }
      const topicHolder = h('div');
      import('../shared/task-form.js').then(({ subjectTopicField }) => {
        topicHolder.append(subjectTopicField(app, { form: state.form, onChange: () => {} }).node);
      }).catch((failure) => topicHolder.append(alertBox(problem(failure), 'bad')));
      const topicWrap = h('div', 'kit-field kit-field-wide');
      topicWrap.append(h('span', 'kit-field-label', 'Ders ve konu (müfredattan; hepsine)'), topicHolder);
      box.append(topicWrap);
      const grades = multiChips(GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) })),
        state.gradeLevels, (list) => { state.gradeLevels = list; });
      grades.node.setAttribute('role', 'group');
      grades.node.setAttribute('aria-label', 'Sınıf seviyesi');
      const gradeWrap = h('div', 'kit-field kit-field-wide');
      gradeWrap.append(h('span', 'kit-field-label', 'Sınıf seviyesi'), grades.node);
      box.append(gradeWrap);
      if (target === 'library') {
        const exams = multiChips(optionsOf(CURRICULUM_EXAMS), state.exams, (list) => { state.exams = list; });
        exams.node.setAttribute('role', 'group');
        exams.node.setAttribute('aria-label', 'Sınav');
        const examWrap = h('div', 'kit-field');
        examWrap.append(h('span', 'kit-field-label', 'Sınav'), exams.node);
        box.append(examWrap);
      }
      const tags = h('input', 'kit-input');
      tags.maxLength = 330;
      tags.value = state.tags;
      tags.addEventListener('input', () => { state.tags = tags.value; });
      box.append(field('Etiketler (virgülle)', tags));
      const note = h('textarea', 'kit-textarea');
      note.maxLength = target === 'pool' ? 500 : 1000;
      note.rows = 2;
      note.value = state.description;
      note.addEventListener('input', () => { state.description = note.value; });
      box.append(field(target === 'pool' ? 'Not' : 'Açıklama', note, true));
      return box;
    }

    async function save() {
      error.textContent = '';
      const data = state.preview;
      const built = importBody({
        playlistId: data?.playlistId, mode: state.mode, videoIds: selectionOf(data?.videos || [], [...state.picked]).ids,
        title: state.title, description: state.description, subjectId: state.form.subjectId,
        topicIds: state.form.topicId ? [state.form.topicId] : [], tags: state.tags, gradeLevels: state.gradeLevels,
        exams: state.exams, overrides: state.overrides,
      }, target);
      if (built.error) { error.textContent = built.error; return; }
      submit.disabled = true;
      const label = submit.textContent;
      submit.textContent = 'Ekleniyor…';
      try {
        const result = await app.api.post(spec.path, built.body, { key: saveKey });
        app.toast(importSummary(result), 'good');
        close({ result });
      } catch (failure) {
        submit.disabled = false;
        submit.textContent = label;
        if (failure?.code === 'playlist_already_added' && failure.details?.itemId) {
          app.toast('Bu liste zaten ekli; eklenmiş liste açılıyor.', 'warn');
          close({ existing: failure.details.itemId });
          return;
        }
        if (failure?.code === 'youtube_api_key_required') {
          const videoId = failure.details?.videoId || parseYoutubeLink(input.value).videoId;
          status.replaceChildren(keyRequiredBox(app, { videoId, onSingle: () => single(videoId) }));
          return;
        }
        error.textContent = problem(failure);
      }
    }
    // Pencere boyunca sabit Idempotency-Key: çift tıklama ikinci liste açmaz.
    const saveKey = `km-pl-${(globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`).slice(0, 80)}`;

    input.addEventListener('paste', () => setTimeout(() => load(), 0));
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); event.stopPropagation(); load(); }
    });
    overlay.append(card);
    overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
    document.addEventListener('keydown', onKey);
    app.root.append(overlay);
    release = dialogA11y(card);
    input.focus();
    if (url) load();
  });
}

/** Video başına etiket: başlık (yalnız ayrı ekleme), ders, etiketler. Dönüş: değer | null (temizle) | undefined (vazgeç). */
async function overrideDialog(app, video, current, mode) {
  const subjects = await app.lookups.options('subjects', { empty: 'Listenin dersi' }).catch(() => []);
  const result = await formDialog(app, {
    title: `${video.position}. video — etiketler`,
    description: 'Boş bırakılan alan listenin değerini alır.',
    fields: [
      mode === 'videos' ? { key: 'title', label: 'Öğe başlığı', maxLength: 120, wide: true,
        placeholder: video.video?.title || '' } : null,
      { key: 'subjectId', label: 'Ders', type: 'select', options: subjects },
      { key: 'tags', label: 'Etiketler (virgülle)', maxLength: 330 },
    ],
    value: { title: current?.title || '', subjectId: current?.subjectId || '', tags: current?.tags || '' },
    submitLabel: 'Uygula',
    onSubmit: (draft) => ({ title: String(draft.title || '').trim(), subjectId: draft.subjectId || '',
      tags: String(draft.tags || '').trim() }),
  });
  if (!result) return undefined;
  return result.title || result.subjectId || result.tags ? result : null;
}

// ------------------------------------------------------------------ ayrıntı

/**
 * Saklanan listenin ayrıntısı (çekmece gövdesi): özet, son eşitleme, "Listeyi yenile" (fark), sıralı videolar ve
 * listeden çıkarılanlar. `onAssign(playlist)` verilirse "Öğrencilere ata" düğmesi çıkar.
 * Dönüş {node, reload()}.
 */
export function playlistPanel(app, { playlistId, onAssign = null, onChange = null }) {
  playlistStyles();
  const node = h('div', 'ak-stack ak-pl-panel');
  let playlist = null;
  let lastMessage = '';
  const render = () => {
    const parts = [playlistHead(playlist)];
    const sync = h('small', 'ak-muted', [`Son eşitleme: ${fmtDateTime(playlist.syncedAt)}`,
      playlist.lastDiff ? `son değişiklik ${fmtDate(String(playlist.lastDiff.at).slice(0, 10))}: `
        + `${diffText({ addedCount: playlist.lastDiff.addedCount, removedCount: playlist.lastDiff.removedCount })}` : '']
      .filter(Boolean).join(' · '));
    parts.push(sync);
    if (lastMessage) parts.push(alertBox(lastMessage, 'info'));
    const tools = h('div', 'ak-row-actions');
    const refresh = actionButton(app, 'admin_refreshVideoPlaylist', 'Listeyi yenile', { variant: 'ghost',
      onClick: async () => {
        const result = await runAction(app, () => app.api.post(`${PLAYLISTS}/${encodeURIComponent(playlistId)}/refresh`),
          { button: refresh });
        if (!result) return;
        playlist = result.playlist || playlist;
        lastMessage = diffText(result);
        app.toast(lastMessage, result.changed ? 'good' : 'info');
        render();
        if (result.changed) onChange?.(playlist);
      } });
    tools.append(refresh);
    if (onAssign) {
      tools.append(actionButton(app, 'admin_createPlaylistAssignment', 'Öğrencilere ata', { variant: 'primary',
        onClick: () => onAssign(playlist) }));
    }
    parts.push(tools, h('h4', 'ak-coach-sub', 'Videolar (liste sırasıyla)'),
      videoRows({ videos: playlist.videos, removed: playlist.removedVideos }).node);
    node.replaceChildren(...parts);
  };
  async function reload() {
    node.replaceChildren(h('p', 'ak-muted', 'Liste yükleniyor…'));
    try {
      playlist = await app.api.get(`/v1/admin/video-playlists/${encodeURIComponent(playlistId)}`);
      render();
    } catch (failure) {
      node.replaceChildren(errorBox(failure, reload));
    }
  }
  reload();
  return { node, reload };
}
