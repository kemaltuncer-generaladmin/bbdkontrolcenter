// BBD Akademi — Bilgi kartları: kurum desteleri (F-A2x, öğrenme araçları §3d).
//
// Kurum, derse / konuya bağlı hazır bilgi kartı desteleri hazırlar ve
// sınıfa, çalışma grubuna ya da öğrenciye atar; öğrenci desteyi kendi Leitner
// kutularında çalışır. Bu ekran YAPISALDIR ve yalnız KM'dedir
// (`decks.manage` → KM `bbd_akademi.learning_setup`).
//
// KARTLAR TAM LİSTE OLARAK KAYDEDİLİR (`PUT` — sözleşme): düzenleyici destenin
// bütün kartlarını tutar; mevcut kart `id` ile gider (öğrencinin ilerlemesi
// korunur), yeni kartta `id` yoktur.
//
// GÖRSEL: kart görseli önce kurum dosyası olarak yüklenir
// (`POST /v1/admin/files`, `kind: flashcard_image`, ≤ 2 MB, jpeg/png/webp) ve
// karta `frontFileId` / `backFileId` ile bağlanır. Kaydedilmeden vazgeçilirse
// yeni yüklenen dosyalar silinir; kayıtta karttan çıkarılan eski görseller de
// silinir (kurum kotası boşa dolmasın). Görseller listede KENDİLİĞİNDEN
// İNMEZ: yüzlerce kartlı destede yüzlerce indirme olurdu — "Görsel" düğmesiyle
// tek tek açılır; imzalı bağlantının süresi dolmuşsa dosya kaydı tazelenir.

import { button, h, loadStyles } from '../../../ui-kit/kit.js';
import { alertBox, badge, card, chipRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import { programLabel } from '../shared/curriculum-picker.js';
import {
  actionButton, confirmAction, empty, fileDrop, fmtDateTime, formDialog, GRADE_LEVELS,
  gradeText, kv, loader, multiChips, num, openDrawer, pagedList, problem, runAction,
  sectionHead, selectBox, staticList, studentPicker, toolbar,
} from '../common.js';

const DECKS = '/v1/admin/flashcard-decks';
const STATUS = { active: ['Etkin', 'good'], archived: ['Arşivde', 'dim'] };
const IMAGE_TYPES = ['png', 'jpg', 'jpeg', 'webp'];

const gradesText = (levels) => ((levels || []).length
  ? levels.map((level) => gradeText(level)).join(', ') : 'Tüm seviyeler');

// ------------------------------------------------------------ görseller

/**
 * İmzalı bağlantıdaki görseli köprüden indirip nesne URL'sine çevirir.
 * Bölüm/çekmece kapanınca `release()` hepsini bırakır.
 */
function imageStore(app) {
  const urls = new Set();
  return {
    async show(image) {
      if (!image?.id) return null;
      let url = image.url;
      const expired = !url || (image.urlExpiresAt && Date.parse(image.urlExpiresAt) < Date.now() + 15_000);
      if (expired) url = (await app.api.get(`/v1/admin/files/${image.id}`))?.url;
      if (!url) return null;
      const { blob } = await app.api.fetchFile(url, 'kart-gorseli');
      const objectUrl = URL.createObjectURL(blob);
      urls.add(objectUrl);
      return objectUrl;
    },
    release() {
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    },
  };
}

/** "Görsel" düğmesi → tıklanınca küçük resim olarak açılır. */
function imageToggle(app, store, image, label = 'Görsel') {
  const wrap = h('span', 'ak-dk-img');
  if (!image?.id) return wrap;
  const open = button(label, { variant: 'ghost', onClick: async () => {
    open.disabled = true;
    try {
      const url = await store.show(image);
      if (!url) { open.disabled = false; return; }
      const img = h('img', 'ak-dk-thumb');
      img.alt = label;
      img.src = url;
      wrap.replaceChildren(img);
    } catch (failure) {
      open.disabled = false;
      app.toast(problem(failure, 'Görsel açılamadı.'), 'bad');
    }
  } });
  wrap.append(open);
  return wrap;
}

// ------------------------------------------------------------------ bölüm

export function section(app) {
  loadStyles(import.meta.url, 'decks.css');
  const node = h('div', 'ak-stack ak-dk');
  const filters = { subject: '', gradeLevel: '', status: 'active' };
  let started = false;
  let list = null;

  node.append(sectionHead('Bilgi kartları', {
    subtitle: 'Kurumun hazır bilgi kartı desteleri: derse ve konuya bağla, sınıfa / gruba / öğrenciye ata. '
      + 'Öğrenci desteyi kendi tekrar kutularında çalışır.',
    actions: [actionButton(app, 'admin_createDeck', 'Yeni deste', { variant: 'primary',
      onClick: () => editDeck(app, null, (saved) => { list?.reload(); if (saved?.id) openDeck(saved.id); }) })],
  }));

  if (!app.can('admin_listDecks')) {
    node.append(empty('Desteleri görme yetkiniz yok',
      'Bilgi kartı desteleri öğrenme araçları kurulumunun parçasıdır (KM izni: Akademi öğrenme araçları kurulumu).'));
    return { node, shown() {}, destroy() {} };
  }

  const subjectSelect = selectBox([{ value: '', label: 'Tüm dersler' }], '', (value) => {
    filters.subject = value; list.reload();
  }, { label: 'Ders' });
  const gradeSelect = selectBox([{ value: '', label: 'Tüm seviyeler' },
    ...GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) }))], '', (value) => {
    filters.gradeLevel = value; list.reload();
  }, { label: 'Seviye' });
  const statusChips = chipRow([
    { key: 'active', label: 'Etkin' }, { key: 'archived', label: 'Arşivde' }, { key: 'all', label: 'Tümü' },
  ], 'active', (key) => { filters.status = key || 'active'; statusChips.set(filters.status); list.reload(); });
  node.append(toolbar(subjectSelect.node, gradeSelect.node, statusChips.node));

  list = pagedList({
    app,
    path: DECKS,
    query: () => filters,
    columns: [
      { key: 'title', label: 'Deste', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.title), h('small', '', row.subject?.name || 'Ders bağlı değil'));
        return cell;
      } },
      { key: 'grades', label: 'Seviye', width: 'minmax(0, 1fr)', cell: (row) => gradesText(row.gradeLevels) },
      { key: 'cards', label: 'Kart', align: 'num', cell: (row) => num(row.cardCount) },
      { key: 'students', label: 'Atanan öğrenci', align: 'num', cell: (row) => num(row.assignedStudentCount) },
      { key: 'status', label: 'Durum', cell: (row) => badge(...(STATUS[row.status] || [row.status, 'dim'])) },
      { key: 'updated', label: 'Güncelleme', width: '130px', cell: (row) => fmtDateTime(row.updatedAt) },
    ],
    onRow: (row) => openDeck(row.id),
    empty: { title: 'Bu süzgece uyan deste yok', text: 'Yeni bir deste oluşturun ya da süzgeci değiştirin.' },
  });
  node.append(list.node);

  function openDeck(deckId) {
    deckDrawer(app, deckId, {
      onChange: (deck) => (deck ? list.replace((row) => row.id === deck.id, { ...deck }) : list.reload()),
    });
  }

  return {
    node,
    shown(payload) {
      if (!started) {
        started = true;
        app.lookups.subjects().then((items) => subjectSelect.options([{ value: '', label: 'Tüm dersler' },
          ...items.map((item) => ({ value: item.id, label: item.name }))])).catch(() => {});
        list.reload();
      }
      if (payload?.deckId) openDeck(payload.deckId);
    },
    destroy() { list.destroy(); },
  };
}

// ---------------------------------------------------------------- çekmece

function deckDrawer(app, deckId, { onChange } = {}) {
  const store = imageStore(app);
  const panel = openDrawer(app, { title: 'Deste', onClose: () => store.release() });
  panel.node.classList.add('ak-dk-drawer');
  let current = null;

  const view = loader(() => app.api.get(`/v1/admin/flashcard-decks/${deckId}`), (deck) => {
    current = deck;
    panel.setTitle(deck.title);
    const nodes = [];
    if (deck.status === 'archived') {
      nodes.push(alertBox('Bu deste arşivde: öğrencilere yeni atanamaz; mevcut kartlar öğrencinin '
        + 'kutularında kalır.', 'warn'));
    }
    nodes.push(card('', kv([
      ['Ders', deck.subject?.name || 'Ders bağlı değil'],
      ['Seviye', gradesText(deck.gradeLevels)],
      ['Konular', (deck.topics || []).map((topic) => topic.name).join(', ') || '—'],
      ['Açıklama', deck.description || '—'],
      ['Kart / atanan öğrenci', `${num(deck.cardCount)} kart · ${num(deck.assignedStudentCount)} öğrenci`],
      ['Sürüm', `${num(deck.version)} · ${fmtDateTime(deck.updatedAt || deck.createdAt)}`],
    ])));

    const tools = h('div', 'ak-inline');
    if (deck.status !== 'archived') {
      tools.append(
        actionButton(app, 'admin_updateDeck', 'Düzenle', { variant: 'primary',
          onClick: () => editDeck(app, deck, (saved) => { onChange?.(saved); view.reload(); }) }),
        actionButton(app, 'admin_assignDeck', 'Ata', {
          onClick: () => assignDeck(app, deck, () => { assignments.reload(); view.reload(); }) }),
        actionButton(app, 'admin_archiveDeck', 'Arşivle', { onClick: () => archive(deck) }),
      );
    }
    nodes.push(tools);

    const cardsTable = dataTable({
      columns: [
        { key: 'n', label: '#', width: '44px', align: 'num', cell: (row) => num(row._index + 1) },
        { key: 'front', label: 'Ön yüz', width: 'minmax(0, 1.4fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', 'ak-dk-text', row.front), imageToggle(app, store, row.frontImage));
          return cell;
        } },
        { key: 'back', label: 'Arka yüz', width: 'minmax(0, 1.6fr)', cell: (row) => {
          const cell = h('div', 'ak-cell-stack');
          cell.append(h('span', 'ak-dk-text', row.back), imageToggle(app, store, row.backImage));
          return cell;
        } },
        { key: 'topic', label: 'Konu', width: 'minmax(0, 1fr)', cell: (row) => row.topic?.name || '—' },
      ],
      // dataTable hücreye sıra vermez; numara satırın kendi alanında taşınır.
      rows: (deck.cards || []).map((item, index) => ({ ...item, _index: index })),
      rowKey: (row) => row.id,
      empty: empty('Destede kart yok', 'Düzenle ile kart ekleyin.'),
    });
    nodes.push(card(`Kartlar (${num((deck.cards || []).length)})`, cardsTable.node));
    nodes.push(card('Atamalar', assignments.node));
    return nodes;
  });

  const assignments = staticList({
    load: () => app.api.get(`/v1/admin/flashcard-decks/${deckId}/assignments`),
    columns: [
      { key: 'target', label: 'Hedef', width: 'minmax(0, 2fr)', cell: (row) => targetText(app, row.target) },
      { key: 'students', label: 'Öğrenci', align: 'num', cell: (row) => num(row.studentCount) },
      { key: 'by', label: 'Atayan', cell: (row) => row.createdBy || '—' },
      { key: 'at', label: 'Tarih', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'actions', label: '', width: '110px', cell: (row) => {
        const box = h('div', 'ak-row-actions');
        box.append(actionButton(app, 'admin_deleteDeckAssignment', 'Kaldır', { variant: 'ghost',
          onClick: () => removeAssignment(row) }));
        return box;
      } },
    ],
    empty: { title: 'Deste kimseye atanmamış', text: '"Ata" ile sınıfa, gruba ya da öğrenciye atayın.' },
  });

  panel.body.append(view.node);
  view.reload();
  assignments.reload();

  async function archive(deck) {
    const ok = await confirmAction(app, { title: `“${deck.title}” arşivlensin mi?`,
      description: 'Deste yeni atamalara kapanır. Öğrencilerin kutularındaki kartlar ve ilerlemeleri kalır.',
      confirmLabel: 'Arşivle', danger: true });
    if (!ok) return;
    const done = await runAction(app, async () => { await app.api.del(`/v1/admin/flashcard-decks/${deck.id}`); return true; },
      { success: 'Deste arşivlendi.' });
    if (done) { onChange?.(null); view.reload(); }
  }

  async function removeAssignment(row) {
    const ok = await confirmAction(app, { title: 'Atama kaldırılsın mı?',
      description: `${num(row.studentCount)} öğrenci desteyi yeni görevlerde görmez; çalıştıkları kartlar kutularında kalır.`,
      confirmLabel: 'Kaldır', danger: true });
    if (!ok) return;
    const done = await runAction(app, async () => {
      await app.api.del(`/v1/admin/flashcard-decks/${deckId}/assignments/${row.id}`);
      return true;
    }, { success: 'Atama kaldırıldı.' });
    if (done) { assignments.reload(); view.reload(); }
  }

  return { panel, get deck() { return current; } };
}

function targetText(app, target) {
  const parts = [];
  if (target?.classGroupIds?.length) parts.push(`${num(target.classGroupIds.length)} sınıf`);
  if (target?.studentGroupIds?.length) parts.push(`${num(target.studentGroupIds.length)} çalışma grubu`);
  if (target?.studentIds?.length) parts.push(`${num(target.studentIds.length)} öğrenci`);
  const node = h('span', '', parts.join(' · ') || '—');
  // Ad listesi önbellekteki sınıf/grup listelerinden (ek istek yok).
  Promise.all([app.lookups.classGroups().catch(() => []), app.lookups.studentGroups().catch(() => [])])
    .then(([classes, groups]) => {
      const names = [
        ...(target?.classGroupIds || []).map((id) => classes.find((item) => item.id === id)?.name),
        ...(target?.studentGroupIds || []).map((id) => groups.find((item) => item.id === id)?.name),
      ].filter(Boolean);
      if (names.length) node.title = names.join(', ');
      if (names.length && !target?.studentIds?.length) node.textContent = names.join(', ');
    });
  return node;
}

// ---------------------------------------------------------------- atama

async function assignDeck(app, deck, onDone) {
  const [classes, groups] = await Promise.all([
    app.lookups.classGroups().catch(() => []), app.lookups.studentGroups().catch(() => [])]);
  const picked = new Map();
  const result = await formDialog(app, {
    title: `“${deck.title}” ata`,
    description: 'Seçilen sınıf, çalışma grubu ve öğrencilerin hepsine atanır. Sonradan sınıfa katılan '
      + 'öğrenciye atama kendiliğinden geçmez.',
    value: { classGroupIds: [], studentGroupIds: [], studentIds: [], notify: true },
    wide: true,
    fields: [
      { key: 'classGroupIds', label: 'Sınıflar', type: 'custom', render: (draft, set) => multiChips(
        classes.map((item) => ({ value: item.id, label: item.name })), draft.classGroupIds, set) },
      { key: 'studentGroupIds', label: 'Çalışma grupları', type: 'custom', render: (draft, set) => (groups.length
        ? multiChips(groups.map((item) => ({ value: item.id, label: item.name })), draft.studentGroupIds, set)
        : h('span', 'ak-muted', 'Kayıtlı çalışma grubu yok.')) },
      { key: 'studentIds', label: 'Öğrenciler', type: 'custom', render: (draft, set) => {
        const wrap = h('div', 'ak-stack');
        const chips = h('div', 'ak-chips');
        const paint = () => {
          chips.replaceChildren();
          for (const [id, name] of picked) {
            const chip = h('button', 'kit-chip on', `${name} ✕`);
            chip.type = 'button';
            chip.addEventListener('click', () => { picked.delete(id); set([...picked.keys()]); paint(); });
            chips.append(chip);
          }
        };
        const picker = studentPicker(app, { onPick: (student) => {
          picked.set(student.id, student.displayName || student.id);
          set([...picked.keys()]);
          paint();
        } });
        wrap.append(picker.node, chips);
        return wrap;
      } },
      { key: 'notify', label: 'Öğrencilere bildirim gönder', type: 'checkbox' },
    ],
    submitLabel: 'Ata',
    onSubmit: (draft, { key }) => {
      const target = { classGroupIds: draft.classGroupIds || [], studentGroupIds: draft.studentGroupIds || [],
        studentIds: draft.studentIds || [] };
      if (!target.classGroupIds.length && !target.studentGroupIds.length && !target.studentIds.length) {
        throw new Error('En az bir sınıf, grup ya da öğrenci seçin.');
      }
      return app.api.post(`/v1/admin/flashcard-decks/${deck.id}/assignments`, { target, notify: Boolean(draft.notify) }, { key });
    },
  });
  if (result) {
    app.toast(`Deste ${num(result.studentCount)} öğrenciye atandı.`, 'good');
    onDone?.(result);
  }
}

// ------------------------------------------------------------- düzenleyici

/** Kuruma özel konu seçimi için: dersin programlarından konu listesi (önbellekli). */
async function topicOptions(app, subjectId) {
  if (!subjectId) return [];
  const programs = (await app.api.getCached('/v1/admin/curricula/programs', undefined,
    { ttlMs: 10 * 60_000 }))?.items || [];
  const mine = programs.filter((program) => program.subject?.id === subjectId).slice(0, 12);
  const details = await Promise.all(mine.map((program) => app.api.getCached(
    `/v1/admin/curricula/programs/${program.id}`, undefined, { ttlMs: 10 * 60_000 }).catch(() => null)));
  const seen = new Map();
  for (const program of details.filter(Boolean)) {
    for (const unit of program.units || []) {
      for (const topic of unit.topics || []) {
        if (topic.retired || seen.has(topic.id)) continue;
        seen.set(topic.id, { value: topic.id, label: `${topic.title} — ${programLabel(program)}` });
      }
    }
  }
  return [...seen.values()];
}

async function editDeck(app, deck, onSaved) {
  const subjects = await app.lookups.subjects().catch(() => []);
  const uploaded = new Set();         // bu pencerede yüklenen dosyalar
  const original = new Set();         // destenin kayıtlı görselleri
  for (const item of deck?.cards || []) {
    if (item.frontImage?.id) original.add(item.frontImage.id);
    if (item.backImage?.id) original.add(item.backImage.id);
  }
  const cards = (deck?.cards || []).map((item) => ({
    id: item.id, front: item.front, back: item.back, topicId: item.topic?.id || '',
    frontFileId: item.frontImage?.id || null, backFileId: item.backImage?.id || null,
    frontName: item.frontImage ? 'kayıtlı görsel' : '', backName: item.backImage ? 'kayıtlı görsel' : '',
  }));
  if (!cards.length) cards.push({ front: '', back: '', topicId: '', frontFileId: null, backFileId: null });
  let topics = deck?.subject?.id ? await topicOptions(app, deck.subject.id).catch(() => []) : [];
  for (const topic of deck?.topics || []) {
    if (!topics.some((item) => item.value === topic.id)) topics.push({ value: topic.id, label: topic.name });
  }

  let repaintTopics = () => {};
  let repaintCards = () => {};

  const saved = await formDialog(app, {
    title: deck ? `“${deck.title}” düzenle` : 'Yeni deste',
    description: 'Kartlar tam liste olarak kaydedilir; mevcut kartlarda öğrencinin ilerlemesi korunur.',
    wide: true,
    value: {
      title: deck?.title || '', description: deck?.description || '', subjectId: deck?.subject?.id || '',
      gradeLevels: (deck?.gradeLevels || []).map(String), topicIds: (deck?.topics || []).map((topic) => topic.id),
    },
    fields: [
      { key: 'title', label: 'Deste adı', required: true, maxLength: 80,
        validate: (value) => (String(value || '').trim().length < 2 ? 'En az 2 karakter.' : null) },
      { key: 'subjectId', label: 'Ders', type: 'select',
        options: [{ value: '', label: 'Ders bağlı değil' }, ...subjects.map((item) => ({ value: item.id, label: item.name }))] },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 300, wide: true },
      { key: 'gradeLevels', label: 'Seviyeler', type: 'custom', hint: 'Boş bırakılırsa tüm seviyeler.',
        render: (draft, set) => multiChips(GRADE_LEVELS.map((level) => ({ value: String(level),
          label: gradeText(level) })), draft.gradeLevels, set) },
      { key: 'topicIds', label: 'Konular', type: 'custom', hint: 'Dersin müfredat konularından.',
        render: (draft, set) => {
          const wrap = h('div');
          let lastSubject = draft.subjectId;
          let chips = null;
          const paint = () => {
            if (!draft.subjectId) {
              wrap.replaceChildren(h('span', 'ak-muted', 'Konu seçmek için önce ders seçin.'));
              return;
            }
            if (!topics.length) {
              wrap.replaceChildren(h('span', 'ak-muted', 'Bu dersin müfredatında konu bulunamadı.'));
              return;
            }
            chips = multiChips(topics, draft.topicIds || [], (value) => { set(value); repaintCards(); });
            wrap.replaceChildren(chips.node);
          };
          repaintTopics = paint;
          paint();
          return { node: wrap, update(next) {
            if (next.subjectId === lastSubject) return;
            lastSubject = next.subjectId;
            set([]);
            topics = [];
            wrap.replaceChildren(h('span', 'ak-muted', 'Konular yükleniyor…'));
            topicOptions(app, next.subjectId).then((list) => { topics = list; paint(); repaintCards(); })
              .catch(() => { topics = []; paint(); });
          } };
        } },
      { key: 'cards', label: 'Kartlar', type: 'custom', wide: true,
        validate: () => {
          const filled = cards.filter((item) => item.front.trim() || item.back.trim());
          if (!filled.length) return 'En az bir kart girin.';
          if (filled.some((item) => !item.front.trim() || !item.back.trim())) {
            return 'Her kartın ön ve arka yüzü dolu olmalı.';
          }
          return null;
        },
        render: (draft) => {
          const wrap = h('div', 'ak-dk-editor');
          const paint = () => {
            wrap.replaceChildren();
            const selected = new Set(draft.topicIds || []);
            const cardTopics = topics.filter((topic) => selected.has(topic.value));
            cards.forEach((item, index) => wrap.append(cardRow(app, item, index, {
              topics: cardTopics, uploaded, total: cards.length,
              move: (step) => {
                const target = index + step;
                if (target < 0 || target >= cards.length) return;
                [cards[index], cards[target]] = [cards[target], cards[index]];
                paint();
              },
              remove: () => { cards.splice(index, 1); if (!cards.length) cards.push(blankCard()); paint(); },
            })));
            const add = button('+ Kart ekle', { variant: 'ghost', onClick: () => {
              cards.push(blankCard());
              paint();
              wrap.querySelectorAll('textarea')[(cards.length - 1) * 2]?.focus();
            } });
            wrap.append(h('div', 'ak-muted ak-dk-count', `${num(cards.length)} kart`), add);
          };
          repaintCards = paint;
          paint();
          return wrap;
        } },
    ],
    submitLabel: deck ? 'Kaydet' : 'Desteyi oluştur',
    onSubmit: async (draft, { key }) => {
      const body = {
        title: draft.title.trim(),
        description: draft.description?.trim() || null,
        subjectId: draft.subjectId || null,
        topicIds: draft.topicIds || [],
        gradeLevels: (draft.gradeLevels || []).map((level) => (level === 'graduate' ? level : Number(level))),
        cards: cards.filter((item) => item.front.trim() || item.back.trim()).map((item) => ({
          ...(item.id ? { id: item.id } : {}),
          front: item.front.trim(),
          back: item.back.trim(),
          frontFileId: item.frontFileId || null,
          backFileId: item.backFileId || null,
          topicId: item.topicId && (draft.topicIds || []).includes(item.topicId) ? item.topicId : null,
        })),
      };
      const result = deck
        ? await app.api.put(`/v1/admin/flashcard-decks/${deck.id}`, body)
        : await app.api.post(DECKS, body, { key });
      // Karttan çıkarılan eski görseller kotayı doldurmasın.
      const still = new Set(body.cards.flatMap((item) => [item.frontFileId, item.backFileId]).filter(Boolean));
      for (const fileId of original) {
        if (!still.has(fileId)) dropFile(app, fileId);
      }
      for (const fileId of [...uploaded]) {
        if (still.has(fileId)) uploaded.delete(fileId);
      }
      return result;
    },
  });
  // Vazgeçildiyse (ya da yüklenip kullanılmadıysa) yeni dosyalar silinir.
  for (const fileId of uploaded) dropFile(app, fileId);
  if (saved) {
    app.toast(deck ? 'Deste kaydedildi.' : 'Deste oluşturuldu.', 'good');
    app.api.invalidate(DECKS);
    onSaved?.(saved);
  }
  repaintTopics = () => {};
}

/** Kurum dosyasını sessizce siler (kota temizliği; hata kullanıcıyı durdurmaz). */
function dropFile(app, fileId) {
  if (!app.can('admin_deleteFile')) return;
  app.api.del(`/v1/admin/files/${fileId}`).catch(() => {});
}

function blankCard() {
  return { front: '', back: '', topicId: '', frontFileId: null, backFileId: null };
}

function cardRow(app, item, index, { topics, uploaded, total, move, remove }) {
  const row = h('div', 'ak-dk-card');
  const head = h('div', 'ak-dk-card-head');
  head.append(h('b', '', `Kart ${index + 1}`), h('span', 'kit-spacer'));
  const up = button('↑', { variant: 'ghost', title: 'Yukarı', onClick: () => move(-1) });
  const down = button('↓', { variant: 'ghost', title: 'Aşağı', onClick: () => move(1) });
  up.disabled = index === 0;
  down.disabled = index === total - 1;
  head.append(up, down, button('Sil', { variant: 'ghost', onClick: remove }));

  const sides = h('div', 'ak-grid-2');
  sides.append(side(app, item, 'front', 'Ön yüz (soru)', 500, uploaded),
    side(app, item, 'back', 'Arka yüz (cevap)', 1000, uploaded));
  row.append(head, sides);

  if (topics.length) {
    const topic = selectBox([{ value: '', label: 'Konu yok' }, ...topics], item.topicId, (value) => {
      item.topicId = value;
    }, { label: 'Kartın konusu' });
    const line = h('div', 'ak-inline');
    line.append(h('span', 'ak-muted', 'Konu'), topic.node);
    row.append(line);
  }
  return row;
}

function side(app, item, which, label, maxLength, uploaded) {
  const box = h('div', 'ak-stack ak-dk-side');
  const text = h('textarea', 'kit-textarea');
  text.maxLength = maxLength;
  text.placeholder = label;
  text.setAttribute('aria-label', label);
  text.value = item[which] || '';
  text.addEventListener('input', () => { item[which] = text.value; });
  const fileKey = `${which}FileId`;
  const nameKey = `${which}Name`;
  const status = h('div', 'ak-inline ak-dk-file');
  const paint = () => {
    status.replaceChildren();
    if (item[fileKey]) {
      status.append(h('span', 'ak-muted', `Görsel: ${item[nameKey] || 'eklendi'}`),
        button('Görseli kaldır', { variant: 'ghost', onClick: () => {
          item[fileKey] = null; item[nameKey] = ''; paint();
        } }));
    } else if (app.can('admin_uploadFile')) {
      const drop = fileDrop({ accept: IMAGE_TYPES, maxMb: 2, label: 'Görsel ekle (isteğe bağlı)',
        onFile: async (file) => {
          status.replaceChildren(h('span', 'ak-muted', 'Yükleniyor…'));
          try {
            const result = await app.api.call('POST', '/v1/admin/files', {
              upload: { file, field: 'file', fields: { kind: 'flashcard_image' } } });
            item[fileKey] = result.data?.id || null;
            item[nameKey] = file.name;
            if (item[fileKey]) uploaded.add(item[fileKey]);
          } catch (failure) {
            app.toast(problem(failure, 'Görsel yüklenemedi.'), 'bad');
          }
          paint();
        } });
      status.append(drop.node);
    }
  };
  paint();
  box.append(text, status);
  return box;
}
