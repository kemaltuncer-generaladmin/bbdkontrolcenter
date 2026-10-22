// BBD Akademi — öğrenci "Öğrenme" sekmesinin parçası: Soru defteri (F-S25; Akademi README §14 madde 136).
//
// SALT OKUNUR. Öğrencinin kendi soru defteri: ders kartları → bölümler (klasör) → soru fotoğraflarının küçük
// resim ızgarası → büyük görüntü (çekmece; fotoğraf, not, "Çözüldü"). Yazan yalnız öğrencidir
// (`question_notebook.manage:self`); kurum (KM) ve öğrencinin koçları `question_notebook.view` ile okur — veli ve
// yalnız ders okutan öğretmen görmez. Bayrak `question_notebook` kapalıysa sekme hiç çizilmez (`app.can`).
//
// İSTEK: düzey başına tek liste isteği (özet / bölümler / imleçli sorular); satır başına API isteği yok. Küçük
// resimler sorunun gömülü `thumbnailUrl`'inden (5 dk imzalı) köprüyle iner — en çok 4 eşzamanlı; süresi
// dolmuşsa `students_getFile` ile taze bağlantı. Nesne URL'leri düzey değişince ve sekme kapanınca bırakılır.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow, kpiRow, skeletonRows } from '../../../ui-kit/layout.js';
import { AkademiError } from '../api.js';
import { empty, errorBox, fmtDate, fmtDateTime, kv, loader, num, openDrawer, toolbar } from '../common.js';
import { box, imageBag } from './students-learning-stats.js';

const PAGE = 48;
const SOLVED_FILTERS = [{ key: 'open', label: 'Çözülmedi' }, { key: 'solved', label: 'Çözüldü' }];
const UNSECTIONED = 'Bölümsüz';
const ALL_ITEMS = 'Tüm sorular';

const counts = (item, solved) => `${num(item)} soru${solved ? ` · ${num(solved)} çözüldü` : ''}`;

/** Küçük resim indirici: en çok 4 eşzamanlı; nesne URL'leri `release()` ile bırakılır. */
function thumbLoader(app, studentId) {
  const urls = [];
  const queue = [];
  let running = 0;
  let generation = 0;

  async function fetchThumb(file) {
    const fresh = file.thumbnailUrl && file.urlExpiresAt && Date.parse(file.urlExpiresAt) - Date.now() > 30_000;
    let blob = null;
    if (fresh) {
      try { ({ blob } = await app.api.fetchFile(file.thumbnailUrl, 'kucuk-resim')); } catch { blob = null; }
    }
    if (!blob) {
      const info = await app.api.get(`/v1/students/${studentId}/files/${file.id}`);
      const url = info?.thumbnailUrl || info?.url;
      if (!url) throw new AkademiError({ code: 'hidden', message: 'Fotoğraf gösterilemiyor.' });
      ({ blob } = await app.api.fetchFile(url, 'kucuk-resim'));
    }
    return URL.createObjectURL(blob);
  }

  function pump() {
    while (running < 4 && queue.length) {
      const job = queue.shift();
      if (job.generation !== generation) continue;
      running += 1;
      fetchThumb(job.file).then((url) => {
        if (job.generation !== generation) { URL.revokeObjectURL(url); return; }
        urls.push(url);
        const image = h('img');
        image.alt = job.alt;
        image.src = url;
        job.frame.replaceChildren(image);
      }).catch(() => {
        if (job.generation !== generation) return;
        job.frame.replaceChildren(h('span', 'ak-sl-nb-thumb-empty'));
        job.frame.title = 'Fotoğraf yüklenemedi';
      }).finally(() => { running -= 1; pump(); });
    }
  }

  return {
    /** Çerçeve döndürür; fotoğraf sıraya girer. */
    frame(file, alt) {
      const frame = h('div', 'ak-sl-nb-thumb');
      if (!file?.id || file.hidden) {
        frame.append(h('span', 'ak-sl-nb-thumb-empty'));
        return frame;
      }
      frame.append(h('span', 'ak-sl-nb-thumb-wait'));
      queue.push({ file, frame, alt, generation });
      pump();
      return frame;
    },
    release() {
      generation += 1;
      queue.length = 0;
      for (const url of urls.splice(0)) URL.revokeObjectURL(url);
    },
  };
}

/**
 * Öğrenci detayı › Öğrenme › Soru defteri.
 * @returns {{node: HTMLElement, reload: () => Promise<void>, destroy: () => void}}
 */
export function notebookTab(app, studentId) {
  const node = h('div', 'ak-stack ak-sl-nb');
  const thumbs = thumbLoader(app, studentId);
  /** Düzey: ders kartları → (ders) bölümler → (ders + bölüm) sorular. */
  const state = { subject: null, section: null, solved: null, sectionNames: new Map() };
  const crumbs = h('nav', 'ak-sl-nb-crumbs');
  crumbs.setAttribute('aria-label', 'Soru defteri konumu');
  const body = h('div', 'ak-stack');
  node.append(crumbs, body,
    h('div', 'ak-muted ak-sl-note', 'Soru defterini yalnız öğrenci düzenler; kurum ve öğrencinin koçları salt okur.'));
  let token = 0;

  function paintCrumbs() {
    const parts = [];
    const link = (label, onClick) => button(label, { variant: 'ghost', onClick });
    if (!state.subject) {
      parts.push(h('b', '', 'Dersler'));
    } else {
      parts.push(link('Dersler', () => openSubjects()));
      parts.push(h('span', 'ak-sl-nb-sep', '›'));
      if (!state.section || state.section.auto) {
        parts.push(h('b', '', state.subject.name));
      } else {
        parts.push(link(state.subject.name, () => openSubject(state.subject)));
        parts.push(h('span', 'ak-sl-nb-sep', '›'));
        parts.push(h('b', '', state.section.name));
      }
    }
    crumbs.replaceChildren(...parts);
  }

  async function show(load, render, rows = 3) {
    const mine = ++token;
    thumbs.release();
    paintCrumbs();
    body.replaceChildren(skeletonRows(rows, 4));
    try {
      const data = await load();
      if (mine !== token) return;
      body.replaceChildren(...[].concat(render(data)).filter(Boolean));
    } catch (failure) {
      if (mine !== token) return;
      body.replaceChildren(errorBox(failure, () => show(load, render, rows)));
    }
  }

  // ------------------------------------------------------------ ders kartları

  function openSubjects() {
    state.subject = null;
    state.section = null;
    return show(() => app.api.get(`/v1/students/${studentId}/question-notebook`), (data) => {
      const subjects = (data?.subjects || []).filter((row) => row.itemCount || row.sectionCount);
      if (!subjects.length) {
        return empty('Soru defteri boş', 'Öğrenci uygulamada ders seçip isterse bölüm oluşturur ve soru '
          + 'fotoğraflarını yükler; eklediği sorular burada görünür.');
      }
      const grid = h('div', 'ak-sl-nb-grid');
      for (const row of subjects) {
        const tile = h('button', 'ak-sl-nb-card');
        tile.type = 'button';
        tile.append(thumbs.frame(row.lastThumbnail, `${row.subject?.name || 'Ders'} son soru`));
        const text = h('div', 'ak-sl-nb-card-text');
        text.append(h('b', '', row.subject?.name || '—'),
          h('small', '', [counts(row.itemCount, row.solvedCount),
            row.sectionCount ? `${num(row.sectionCount)} bölüm` : ''].filter(Boolean).join(' · ')),
          h('small', 'ak-muted', row.lastAddedAt ? `Son ekleme ${fmtDate(row.lastAddedAt)}` : 'Soru yok'));
        tile.append(text);
        tile.addEventListener('click', () => openSubject(row.subject));
        grid.append(tile);
      }
      return [kpiRow([
        { label: 'Toplam soru', value: num(data.itemCount) },
        { label: 'Çözüldü', value: num(data.solvedCount), tone: data.solvedCount ? 'good' : '' },
        { label: 'Çözülmedi', value: num(Math.max(0, (data.itemCount || 0) - (data.solvedCount || 0))) },
        { label: 'Ders', value: num(subjects.length) },
      ]), grid];
    });
  }

  // ---------------------------------------------------------------- bölümler

  function openSubject(subject) {
    state.subject = { id: subject?.id, name: subject?.name || 'Ders' };
    state.section = null;
    return show(() => app.api.get(`/v1/students/${studentId}/question-notebook/sections`,
      { subjectId: state.subject.id }), (data) => {
      const sections = data?.items || [];
      state.sectionNames = new Map(sections.map((row) => [row.id, row.name]));
      const total = sections.reduce((sum, row) => sum + (row.itemCount || 0), 0) + (data?.unsectionedCount || 0);
      const solved = sections.reduce((sum, row) => sum + (row.solvedCount || 0), 0)
        + (data?.unsectionedSolvedCount || 0);
      // Bölüm yoksa ara adım yok: doğrudan sorular (tık sayısı az).
      if (!sections.length) {
        queueMicrotask(() => openItems({ id: null, name: ALL_ITEMS, all: true, auto: true }));
        return skeletonRows(3, 4);
      }
      const grid = h('div', 'ak-sl-nb-grid');
      const folder = (name, itemCount, solvedCount, cover, target) => {
        const tile = h('button', 'ak-sl-nb-card folder');
        tile.type = 'button';
        tile.append(thumbs.frame(cover, `${name} kapak`));
        const text = h('div', 'ak-sl-nb-card-text');
        text.append(h('b', '', name), h('small', '', counts(itemCount, solvedCount)));
        tile.append(text);
        tile.addEventListener('click', () => openItems(target));
        grid.append(tile);
      };
      folder(ALL_ITEMS, total, solved, null, { id: null, name: ALL_ITEMS, all: true });
      for (const row of sections) {
        folder(row.name, row.itemCount, row.solvedCount, row.coverThumbnail, { id: row.id, name: row.name });
      }
      if (data?.unsectionedCount) {
        folder(UNSECTIONED, data.unsectionedCount, data.unsectionedSolvedCount, null,
          { id: null, name: UNSECTIONED });
      }
      return [h('div', 'ak-muted ak-sl-note', `${num(sections.length)} bölüm · ${counts(total, solved)}`), grid];
    });
  }

  // ------------------------------------------------------------------ sorular

  function openItems(section) {
    state.section = section;
    state.solved = null;
    const mine = ++token;
    thumbs.release();
    paintCrumbs();
    const query = () => ({
      subjectId: state.subject.id,
      sectionId: section.id || undefined,
      unsectioned: !section.id && !section.all ? true : undefined,
      solved: state.solved === null ? undefined : state.solved === 'solved',
    });
    const filter = chipRow(SOLVED_FILTERS, null, (key) => { state.solved = key; restart(); });
    const grid = h('div', 'ak-sl-nb-grid tiles');
    const status = h('span', 'ak-list-count');
    const more = button('Daha fazla', { variant: 'ghost', onClick: () => loadMore() });
    more.hidden = true;
    const slot = h('div');
    body.replaceChildren(filter.node, slot, toolbar(status, h('span', 'kit-spacer'), more));
    let iterator = null;
    let items = [];
    let busy = false;
    let pass = 0;

    function restart() {
      pass += 1;
      thumbs.release();
      items = [];
      grid.replaceChildren();
      slot.replaceChildren(skeletonRows(3, 6));
      iterator = app.api.pages(`/v1/students/${studentId}/question-notebook/items`, query(), { limit: PAGE });
      busy = false;
      return loadMore();
    }

    async function loadMore() {
      if (busy || !iterator || iterator.done) return;
      busy = true;
      more.disabled = true;
      const mine2 = pass;
      try {
        const page = await iterator.next();
        if (mine !== token || mine2 !== pass) return;
        const start = items.length;
        items = items.concat(page);
        page.forEach((item, index) => grid.append(tile(item, start + index)));
        slot.replaceChildren(items.length ? grid : empty(state.solved
          ? 'Bu süzgece uyan soru yok' : 'Bu bölümde soru yok', state.solved ? 'Süzgeci kaldırın.' : ''));
      } catch (failure) {
        if (mine !== token || mine2 !== pass) return;
        slot.replaceChildren(errorBox(failure, () => restart()));
      } finally {
        if (mine === token && mine2 === pass) {
          busy = false;
          more.disabled = false;
          more.hidden = !iterator || iterator.done;
          status.textContent = items.length ? `${num(items.length)} soru${iterator?.done ? '' : '+'}` : '';
        }
      }
    }

    function tile(item, index) {
      const node2 = h('button', `ak-sl-nb-tile${item.solved ? ' solved' : ''}`);
      node2.type = 'button';
      const first = (item.images || [])[0];
      node2.append(thumbs.frame(first, `Soru ${index + 1}`));
      const marks = h('div', 'ak-sl-nb-marks');
      if (item.solved) marks.append(badge('Çözüldü', 'good'));
      if ((item.images || []).length > 1) marks.append(badge(`${num(item.images.length)} fotoğraf`, 'dim'));
      node2.append(marks);
      const foot = h('div', 'ak-sl-nb-tile-foot');
      foot.append(h('small', 'ak-muted', fmtDate(item.createdAt)));
      if (item.note) foot.append(h('small', 'ak-sl-nb-note', item.note));
      node2.append(foot);
      node2.title = item.note || 'Soruyu büyük gör';
      node2.addEventListener('click', () => itemDrawer(app, studentId, () => items, index, state));
      return node2;
    }

    paintCrumbs();
    return restart();
  }

  return {
    node,
    reload: () => openSubjects(),
    destroy() { token += 1; thumbs.release(); },
  };
}

/** Büyük görüntü: fotoğraflar (tıkla → gerçek boy), not, çözüldü; önceki / sonraki soru. */
function itemDrawer(app, studentId, list, start, state) {
  let index = start;
  let images = imageBag(app, studentId);
  const panel = openDrawer(app, { title: 'Soru', subtitle: 'Soru defteri · salt okunur',
    onClose: () => images.close() });
  panel.node.classList.add('ak-sl-drawer');
  const slot = h('div', 'ak-stack');
  panel.body.append(slot);

  let token = 0;
  /** Soruyu taze okur (imzalı bağlantılar 5 dk; not / çözüldü güncel); okunamazsa listedeki satırla çizer. */
  async function paint() {
    const mine = ++token;
    const items = list();
    const listed = items[index];
    if (!listed) { slot.replaceChildren(alertBox('Soru bulunamadı.', 'warn')); return; }
    slot.replaceChildren(skeletonRows(4, 2));
    let item = listed;
    let gone = false;
    try {
      const fresh = await app.api.get(`/v1/students/${studentId}/question-notebook/items/${listed.id}`);
      if (fresh?.id === listed.id) item = fresh;
    } catch (failure) {
      gone = failure instanceof AkademiError && failure.status === 404;
    }
    if (mine !== token) return;
    render(item, items.length);
    if (gone) slot.prepend(alertBox('Öğrenci bu soruyu silmiş.', 'warn'));
  }

  function render(item, total) {
    images.close();
    images = imageBag(app, studentId);
    panel.setTitle(`${item.subject?.name || state.subject?.name || 'Ders'} · Soru ${index + 1}`);
    const gallery = h('div', 'ak-sl-gallery ak-sl-nb-viewer');
    (item.images || []).forEach((file, at) => {
      const figure = images.figure(file, item.images.length > 1 ? `Fotoğraf ${at + 1} / ${item.images.length}` : 'Fotoğraf');
      figure.title = 'Büyütmek / küçültmek için tıklayın';
      figure.tabIndex = 0;
      figure.setAttribute('role', 'button');
      const toggle = () => figure.classList.toggle('zoom');
      figure.addEventListener('click', toggle);
      figure.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(); }
      });
      gallery.append(figure);
    });
    const nav = toolbar(
      button('‹ Önceki soru', { variant: 'ghost', disabled: index <= 0, onClick: () => { index -= 1; paint(); } }),
      h('span', 'ak-muted', `${num(index + 1)} / ${num(total)}`),
      button('Sonraki soru ›', { variant: 'ghost', disabled: index >= total - 1,
        onClick: () => { index += 1; paint(); } }),
    );
    slot.replaceChildren(nav,
      (item.images || []).length ? gallery : alertBox('Bu sorunun fotoğrafı yok.', 'info'),
      box(kv([
        ['Durum', item.solved
          ? badge(`Çözüldü${item.solvedAt ? ` · ${fmtDate(item.solvedAt)}` : ''}`, 'good') : badge('Çözülmedi', 'dim')],
        ['Not', item.note || '—'],
        ['Bölüm', item.sectionId ? state.sectionNames.get(item.sectionId) || '—' : UNSECTIONED],
        ['Eklendi', fmtDateTime(item.createdAt)],
        ['Güncellendi', fmtDateTime(item.updatedAt)],
      ])));
  }
  paint();
}
