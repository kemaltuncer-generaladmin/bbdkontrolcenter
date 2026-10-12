// BBD Akademi — Okuma: liste düzenleyici (F-A19). `reading.js` bölümünün parçası.
//
// LİSTE KALEMİ: hazır katalogdan (`catalogBookId`) ya da elle (ad zorunlu,
// `item_title_required`). Güncellemede kalemler TAM listedir; çıkarılan kalem
// öğrencinin rafındaki kitabı etkilemez (Akademi kuralı). Sözleşme, okunan
// kalemde not, kapak dosya kimliği ve katalog bağını döndürmez: mevcut kalemde
// not ve kapak YALNIZ değiştirildiyse gönderilir, katalog bağı hiç gönderilmez
// (gönderilmeyen alan korunur).
//
// KAPAK: dosya önce istemcide küçültülür (uzun kenar ≤ 1600 px, ≤ 600 KB;
// sözleşme), `admin_uploadFile` (`book_cover`) ile yüklenir, önizleme taze
// imzalı bağlantıdan (`admin_getFile`) nesne URL'sine iner ve pencere kapanınca
// bırakılır. Bu pencerede yüklenip kullanılmayan (değiştirilen, kaldırılan,
// vazgeçilen) dosya `admin_deleteFile` ile silinir — kurum kotası boşa dolmaz.
// Kayıtlı kalemin eski kapağının dosya kimliği okunmadığından onun temizliği
// Akademi'dedir.

import { button, h } from '../../ui-kit/kit.js';
import { badge } from '../../ui-kit/layout.js';
import { AkademiError } from './api.js';
import {
  GRADE_LEVELS, actionButton, formDialog, gradeText, multiChips, num, problem, searchBox, toolbar,
} from './common.js';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ISBN = /^[0-9]{10}([0-9]{3})?$/;
const COVER_EDGE = 1600;
const COVER_TARGET = 600 * 1024;
const COVER_MAX = 2 * 1024 * 1024;

export const gradesText = (levels) => ((levels || []).length
  ? levels.map(gradeText).join(', ') : 'Tüm seviyeler');
const gradeValue = (raw) => (raw === 'graduate' ? raw : Number(raw));

let entrySeq = 0;

/** Katalog kitabı → düzenleyici kalemi. Kurum kalemi elle kalem olarak kopyalanır. */
export function entryFromCatalog(book) {
  const platform = book.source === 'platform';
  return {
    uid: `e${entrySeq += 1}`,
    id: null,
    catalogBookId: platform ? book.id : null,
    title: book.title || '',
    author: book.author || '',
    pageCount: book.pageCount || null,
    isbn: book.isbn || '',
    note: '',
    existing: false,
    hasCover: Boolean(book.coverUrl),
    coverFileId: undefined,
    coverChanged: false,
    preview: null,
    busy: false,
  };
}

/** Kayıtlı kalem → düzenleyici kalemi (`id` = `readingListItemId`). */
function entryFromList(item) {
  const id = item.readingListItemId || String(item.id || '').replace(/^item:/, '') || null;
  return { ...entryFromCatalog({ ...item, source: 'institution' }), id: UUID.test(id || '') ? id : null,
    existing: true };
}

function itemBody(entry) {
  const body = { id: entry.id || null };
  if (entry.catalogBookId && !entry.existing) {
    body.catalogBookId = entry.catalogBookId;
  } else {
    // Mevcut kalemin katalog bağı okunan kayıtta yok; `catalogBookId`
    // gönderilmez ki bağ koparılmasın. Yeni elle kalemde açıkça null.
    if (!entry.existing) body.catalogBookId = null;
    body.title = String(entry.title || '').trim() || null;
    body.author = String(entry.author || '').trim() || null;
    body.pageCount = entry.pageCount ? Number(entry.pageCount) : null;
    body.isbn = String(entry.isbn || '').replace(/[\s-]/g, '') || null;
  }
  const note = String(entry.note || '').trim();
  // Okunan kalem notu döndürmez: mevcut kalemde boş not "değişmedi" demektir.
  if (note || !entry.existing) body.note = note || null;
  if (entry.coverChanged) body.coverFileId = entry.coverFileId || null;
  return body;
}

/**
 * Oluştur / düzenle. `current`: kayıtlı liste (güncelleme) · `prefill`: hazır
 * listeden kopya. Dönüş: kaydedilen liste ya da vazgeçildiyse null.
 */
export async function editList(app, current, { prefill = null } = {}) {
  const [classes, groups] = await Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]);
  const source = current || prefill || {};
  const entries = current ? (current.items || []).map(entryFromList) : (prefill?.items || []);
  const files = coverSession(app);
  let latest = entries;

  const value = {
    title: source.title || '',
    description: source.description || '',
    startDate: source.startDate || '',
    endDate: source.endDate || '',
    gradeLevels: (source.gradeLevels || []).map(String),
    classGroupIds: current?.classGroupIds || [],
    studentGroupIds: current?.studentGroupIds || [],
    items: entries,
  };

  const chips = (options, key) => (draft, set) => {
    if (!options.length) return h('span', 'ak-muted', 'Tanımlı kayıt yok.');
    return multiChips(options, draft[key], (list) => set(list)).node;
  };

  const saved = await formDialog(app, {
    title: current ? `“${current.title}” listesini düzenle` : (prefill ? 'Hazır listeden okuma listesi oluştur' : 'Yeni okuma listesi'),
    description: prefill ? 'Kitaplar katalog bağlantısıyla eklenir. Hedef sınıfları seçip kaydedin.' : '',
    wide: true,
    value,
    fields: [
      { key: 'title', label: 'Liste adı', required: true, maxLength: 100, wide: true,
        placeholder: '9. sınıf ekim okuma listesi',
        validate: (raw) => (String(raw || '').trim().length < 2 ? 'Liste adı en az 2 karakter olmalı.' : null) },
      { key: 'description', label: 'Açıklama', type: 'textarea', maxLength: 500, wide: true },
      { key: 'startDate', label: 'Okuma başlangıcı', type: 'date' },
      { key: 'endDate', label: 'Okuma bitişi', type: 'date' },
      { key: 'gradeLevels', label: 'Seviye', type: 'custom',
        hint: 'Boş bırakılırsa liste seviye sınırı olmadan önerilir.',
        render: chips(GRADE_LEVELS.map((level) => ({ value: String(level), label: gradeText(level) })), 'gradeLevels') },
      { key: 'classGroupIds', label: 'Hedef sınıflar', type: 'custom',
        hint: 'Boş bırakılırsa seçilen seviyedeki herkes hedeflenir.',
        render: chips(classes.map((item) => ({ value: item.id, label: item.name })), 'classGroupIds') },
      { key: 'studentGroupIds', label: 'Çalışma grupları', type: 'custom',
        render: chips(groups.map((item) => ({ value: item.id, label: item.name })), 'studentGroupIds') },
      { key: 'items', label: 'Kitaplar', type: 'custom',
        render: (draft, set) => itemsEditor(app, draft.items, (list) => { latest = list; set(list); }, files),
        validate: (list) => {
          if (!list?.length) return 'Listeye en az bir kitap ekleyin.';
          if (list.length > 300) return 'Bir listede en çok 300 kitap olabilir.';
          if (list.some((entry) => entry.busy)) return 'Kapak yüklemesi sürüyor; bitmesini bekleyin.';
          const untitled = list.findIndex((entry) => !entry.catalogBookId && !String(entry.title || '').trim());
          return untitled >= 0 ? `${untitled + 1}. kitabın adı boş (elle eklenen kitapta ad zorunlu).` : null;
        } },
    ],
    submitLabel: current ? 'Değişiklikleri kaydet' : 'Listeyi oluştur',
    onSubmit: (draft, { key }) => {
      if (draft.startDate && draft.endDate && draft.endDate < draft.startDate) {
        throw new AkademiError({ message: 'Okuma bitişi başlangıçtan önce olamaz.' });
      }
      const body = {
        title: String(draft.title || '').trim(),
        description: String(draft.description || '').trim() || null,
        gradeLevels: (draft.gradeLevels || []).map(gradeValue),
        classGroupIds: draft.classGroupIds || [],
        studentGroupIds: draft.studentGroupIds || [],
        startDate: draft.startDate || null,
        endDate: draft.endDate || null,
        items: (draft.items || []).map(itemBody),
      };
      return current
        ? app.api.put(`/v1/admin/reading/lists/${current.id}`, body)
        : app.api.post('/v1/admin/reading/lists', body, { key });
    },
  });
  files.finish(saved ? latest : null);
  if (saved) app.toast(current ? 'Okuma listesi güncellendi.' : 'Okuma listesi oluşturuldu.', 'good');
  return saved && saved !== true ? saved : (saved ? current : null);
}

/**
 * Bu pencerede yüklenen kapaklar. `finish(entries)`: kaydedildiyse kullanılmayan
 * dosyalar, vazgeçildiyse (`null`) hepsi silinir; önizleme URL'leri bırakılır.
 */
function coverSession(app) {
  const uploaded = new Set();
  const urls = new Set();
  const later = [];
  return {
    uploaded,
    later: (fn) => later.push(fn),
    track(url) { urls.add(url); return url; },
    release(url) { if (url && urls.delete(url)) URL.revokeObjectURL(url); },
    discard(fileId) {
      if (!fileId || !uploaded.has(fileId)) return;
      uploaded.delete(fileId);
      if (app.can('admin_deleteFile')) app.api.del(`/v1/admin/files/${fileId}`).catch(() => {});
    },
    finish(entries) {
      const used = new Set((entries || []).filter((entry) => entry.coverChanged && entry.coverFileId)
        .map((entry) => entry.coverFileId));
      for (const fileId of [...uploaded]) if (!used.has(fileId)) this.discard(fileId);
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
      for (const fn of later.splice(0)) fn();
    },
  };
}

/** Kalem düzenleyici: katalog araması + elle ekleme + sıra, not ve kapak. */
function itemsEditor(app, initial, set, files) {
  const node = h('div', 'ak-stack ak-rd-editor');
  let entries = [...(initial || [])];
  const commit = () => set([...entries]);

  // --- katalog araması (250 ms) -------------------------------------
  const picker = h('div', 'ak-picker ak-rd-search');
  const results = h('div', 'ak-picker-list');
  results.hidden = true;
  let token = 0;
  const box = searchBox({ placeholder: 'Katalogda ara: kitap adı ya da yazar (en az 2 harf)', onSearch: async (text) => {
    const mine = ++token;
    if (text.length < 2) { results.hidden = true; return; }
    try {
      const data = await app.api.getCached('/v1/catalog/reading', { q: text, source: 'all', limit: 12 });
      if (mine !== token) return;
      paintResults(data?.items || []);
    } catch (failure) {
      if (mine !== token) return;
      results.replaceChildren(h('div', 'ak-picker-empty', problem(failure)));
      results.hidden = false;
    }
  } });
  picker.append(box.node, results);
  files.later(() => box.destroy());

  function paintResults(items) {
    results.replaceChildren();
    if (!items.length) results.append(h('div', 'ak-picker-empty', 'Katalogda eşleşen kitap yok. Elle ekleyebilirsiniz.'));
    for (const book of items) {
      const already = entries.some((entry) => (book.source === 'platform' && entry.catalogBookId === book.id)
        || (entry.title === book.title && entry.author === (book.author || '')));
      const row = h('button', 'ak-picker-row');
      row.type = 'button';
      row.disabled = already;
      row.append(h('b', '', `${book.title}${already ? ' · listede' : ''}`),
        h('span', '', [book.author, book.pageCount ? `${num(book.pageCount)} sayfa` : '',
          (book.lists || []).map((list) => list.title).join(', ')].filter(Boolean).join(' · ')));
      row.addEventListener('click', () => {
        results.hidden = true;
        entries.push(entryFromCatalog(book));
        commit();
        paint();
      });
      results.append(row);
    }
    results.hidden = false;
  }

  // --- elle ekleme / düzenleme --------------------------------------
  const manual = manualForm({
    onSave: (fields, editing) => {
      if (editing) Object.assign(editing, fields);
      else entries.push({ ...entryFromCatalog({ source: 'manual' }), ...fields });
      commit();
      paint();
    },
  });
  const openManual = button('Elle kitap ekle', { variant: 'ghost', onClick: () => manual.open(null) });
  node.append(toolbar(picker, openManual), manual.node);

  // --- kalemler -----------------------------------------------------
  const listNode = h('div', 'ak-rd-items');
  const count = h('div', 'ak-muted ak-rd-count');
  node.append(listNode, count);

  function move(index, step) {
    const target = index + step;
    if (target < 0 || target >= entries.length) return;
    [entries[index], entries[target]] = [entries[target], entries[index]];
    commit();
    paint();
  }

  function removeEntry(entry) {
    if (entry.coverChanged) files.discard(entry.coverFileId);
    files.release(entry.preview);
    entries = entries.filter((item) => item !== entry);
    commit();
    paint();
  }

  function paint() {
    listNode.replaceChildren();
    count.textContent = entries.length
      ? `${num(entries.length)} kitap · sıra öğrencinin gördüğü sıradır.` : '';
    if (!entries.length) {
      listNode.append(h('div', 'ak-rd-items-empty', 'Henüz kitap yok. Katalogda arayın ya da elle ekleyin.'));
      return;
    }
    entries.forEach((entry, index) => listNode.append(itemRow(entry, index)));
  }

  function itemRow(entry, index) {
    const row = h('div', 'ak-rd-item');
    row.append(h('span', 'ak-rd-item-no', String(index + 1)));

    const info = h('div', 'ak-cell-stack');
    const title = h('b', '', entry.title || 'Adsız kitap');
    info.append(title, h('small', '', [entry.author || 'Yazar yok', entry.pageCount ? `${num(entry.pageCount)} sayfa` : '',
      entry.isbn ? `ISBN ${entry.isbn}` : ''].filter(Boolean).join(' · ')));
    const tags = h('div', 'ak-inline');
    if (entry.existing) tags.append(badge('Kayıtlı', 'dim'));
    else tags.append(badge(entry.catalogBookId ? 'Katalogdan' : 'Elle', entry.catalogBookId ? 'info' : ''));
    info.append(tags);

    const note = h('input', 'kit-input ak-rd-note');
    note.type = 'text';
    note.maxLength = 300;
    note.value = entry.note || '';
    note.placeholder = entry.existing ? 'Not (boş bırakılırsa kayıtlı not korunur)' : 'Not (isteğe bağlı)';
    note.setAttribute('aria-label', `${entry.title || 'Kitap'} notu`);
    note.addEventListener('input', () => { entry.note = note.value; commit(); });

    const actions = h('div', 'ak-row-actions');
    const up = button('↑', { variant: 'ghost', title: 'Yukarı taşı', onClick: () => move(index, -1) });
    up.disabled = index === 0;
    const down = button('↓', { variant: 'ghost', title: 'Aşağı taşı', onClick: () => move(index, 1) });
    down.disabled = index === entries.length - 1;
    actions.append(up, down);
    if (!entry.catalogBookId || entry.existing) {
      actions.append(button('Düzenle', { variant: 'ghost', onClick: () => manual.open(entry) }));
    }
    actions.append(button('Çıkar', { variant: 'ghost', onClick: () => removeEntry(entry) }));

    row.append(info, note, coverCell(entry), actions);
    return row;
  }

  function coverCell(entry) {
    const cell = h('div', 'ak-rd-cover');
    if (entry.busy) {
      cell.append(h('span', 'ak-muted', 'Kapak yükleniyor…'));
      return cell;
    }
    if (entry.preview) {
      const image = h('img', 'ak-rd-thumb');
      image.alt = `${entry.title || 'Kitap'} kapağı`;
      image.src = entry.preview;
      cell.append(image);
    } else if (entry.coverChanged && !entry.coverFileId) {
      cell.append(h('span', 'ak-muted', 'Kapak kaldırılacak'));
    } else if (entry.coverChanged || entry.hasCover) {
      cell.append(badge('Kapak var', 'good'));
    } else {
      cell.append(h('span', 'ak-muted', 'Kapak yok'));
    }
    const tools = h('div', 'ak-inline');
    if (app.can('admin_uploadFile')) {
      const input = h('input');
      input.type = 'file';
      input.accept = 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp';
      input.hidden = true;
      input.addEventListener('change', () => { const file = input.files?.[0]; input.value = ''; if (file) upload(entry, file); });
      const pick = button(entry.preview || entry.hasCover || entry.coverChanged ? 'Değiştir' : 'Kapak yükle',
        { variant: 'ghost', onClick: () => input.click() });
      tools.append(input, pick);
    } else {
      tools.append(actionButton(app, 'admin_uploadFile', 'Kapak yükle', { variant: 'ghost' }));
    }
    // Bu pencerede yüklenen kapak ya da kayıtlı kalemin kapağı kaldırılabilir;
    // yeni katalog kaleminin katalog kapağı kurumun dosyası değildir.
    const uploadedHere = entry.coverChanged && Boolean(entry.coverFileId);
    if (entry.coverChanged && !entry.coverFileId) {
      tools.append(button('Geri al', { variant: 'ghost', onClick: () => {
        entry.coverChanged = false;
        entry.coverFileId = undefined;
        commit();
        paint();
      } }));
    } else if (uploadedHere || (entry.existing && entry.hasCover)) {
      tools.append(button('Kaldır', { variant: 'ghost',
        title: 'Kuruma yüklenmiş kapak kaldırılır; hazır katalog kapağı varsa o görünür.',
        onClick: () => {
          if (uploadedHere) files.discard(entry.coverFileId);
          files.release(entry.preview);
          entry.preview = null;
          const markRemoval = entry.existing && entry.hasCover;
          entry.coverChanged = markRemoval;
          entry.coverFileId = markRemoval ? null : undefined;
          commit();
          paint();
        } }));
    }
    cell.append(tools);
    return cell;
  }

  async function upload(entry, original) {
    const ext = String(original.name || '').split('.').pop().toLowerCase();
    if (!/^image\/(jpeg|png|webp)$/.test(original.type || '') && !['jpg', 'jpeg', 'png', 'webp'].includes(ext)) {
      app.toast('Kapak JPEG, PNG ya da WebP olmalı.', 'bad');
      return;
    }
    entry.busy = true;
    commit();
    paint();
    try {
      const file = await shrinkImage(original);
      if (file.size > COVER_MAX) {
        throw new AkademiError({ code: 'file_too_large', message: 'Kapak küçültüldükten sonra da 2 MB\'tan büyük.' });
      }
      const result = await app.api.call('POST', '/v1/admin/files', {
        upload: { file, field: 'file', fields: { kind: 'book_cover' } },
      });
      const uploadedFile = result.data;
      files.uploaded.add(uploadedFile.id);
      // Önceki (bu pencerede yüklenmiş) kapak artık sahipsiz: sil.
      if (entry.coverChanged) files.discard(entry.coverFileId);
      files.release(entry.preview);
      entry.coverFileId = uploadedFile.id;
      entry.coverChanged = true;
      entry.preview = await coverPreview(app, uploadedFile.id, files).catch(() => null);
      app.toast('Kapak yüklendi.', 'good');
    } catch (failure) {
      app.toast(problem(failure, 'Kapak yüklenemedi.'), 'bad');
    } finally {
      entry.busy = false;
      commit();
      paint();
    }
  }

  paint();
  return node;
}

/** Sunucuda işlenmiş kapağı (EXIF silinmiş, küçültülmüş) taze bağlantıyla gösterir. */
async function coverPreview(app, fileId, files) {
  const info = await app.api.get(`/v1/admin/files/${fileId}`);
  if (!info?.url) return null;
  const { blob } = await app.api.fetchFile(info.url, 'kapak');
  return files.track(URL.createObjectURL(blob));
}

/** Elle kitap: ad (zorunlu), yazar, sayfa, ISBN. Kalemi düzenlerken de kullanılır. */
function manualForm({ onSave }) {
  const node = h('div', 'ak-rd-manual');
  node.hidden = true;
  let editing = null;
  const input = (label, { maxLength = 120, numeric = false, placeholder = '' } = {}) => {
    const field = h('label', 'kit-field');
    const control = h('input', 'kit-input');
    control.type = 'text';
    control.maxLength = maxLength;
    if (numeric) control.inputMode = 'numeric';
    if (placeholder) control.placeholder = placeholder;
    field.append(h('span', 'kit-field-label', label), control);
    return { field, control };
  };
  const title = input('Kitap adı *', { maxLength: 200 });
  const author = input('Yazar', { maxLength: 120 });
  const pages = input('Sayfa sayısı', { maxLength: 4, numeric: true });
  const isbn = input('ISBN', { maxLength: 17, numeric: true, placeholder: '10 ya da 13 hane' });
  const error = h('div', 'kit-field-error');
  const grid = h('div', 'ak-rd-manual-grid');
  grid.append(title.field, author.field, pages.field, isbn.field);
  const save = button('Ekle', { variant: 'primary', onClick: () => submit() });
  const cancel = button('Vazgeç', { variant: 'ghost', onClick: () => close() });
  const actions = h('div', 'ak-inline');
  actions.append(save, cancel, error);
  node.append(grid, actions);
  for (const control of [title.control, author.control, pages.control, isbn.control]) {
    control.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') { event.preventDefault(); submit(); }
    });
  }

  function submit() {
    error.textContent = '';
    const name = title.control.value.trim();
    const pageText = pages.control.value.trim();
    const isbnText = isbn.control.value.replace(/[\s-]/g, '');
    if (!name) { error.textContent = 'Kitap adı zorunlu.'; return; }
    if (pageText && (!/^\d+$/.test(pageText) || Number(pageText) < 1 || Number(pageText) > 5000)) {
      error.textContent = 'Sayfa sayısı 1–5000 arasında olmalı.'; return;
    }
    if (isbnText && !ISBN.test(isbnText)) { error.textContent = 'ISBN 10 ya da 13 rakam olmalı.'; return; }
    onSave({ title: name, author: author.control.value.trim(), pageCount: pageText ? Number(pageText) : null,
      isbn: isbnText }, editing);
    close();
  }

  function close() {
    editing = null;
    node.hidden = true;
  }

  return {
    node,
    open(entry) {
      editing = entry;
      title.control.value = entry?.title || '';
      author.control.value = entry?.author || '';
      pages.control.value = entry?.pageCount ? String(entry.pageCount) : '';
      isbn.control.value = entry?.isbn || '';
      save.textContent = entry ? 'Güncelle' : 'Ekle';
      error.textContent = '';
      node.hidden = false;
      title.control.focus();
    },
  };
}

/**
 * Kapağı yüklemeden önce küçültür (uzun kenar ≤ 1600 px, hedef ≤ 600 KB, JPEG).
 * Zaten küçükse ya da tarayıcı çözemezse dosya olduğu gibi döner; asıl kapı
 * Akademi'dir (2 MB, sihirli bayt denetimi, EXIF silme).
 */
async function shrinkImage(file) {
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise((resolve, reject) => {
      const element = new Image();
      element.onload = () => resolve(element);
      element.onerror = () => reject(new Error('decode'));
      element.src = url;
    });
    const longest = Math.max(image.naturalWidth, image.naturalHeight) || 1;
    const scale = Math.min(1, COVER_EDGE / longest);
    if (scale === 1 && file.size <= COVER_TARGET) return file;
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
    canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
    const context = canvas.getContext('2d');
    context.fillStyle = '#fff';                // saydam PNG JPEG'de kararmasın
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.drawImage(image, 0, 0, canvas.width, canvas.height);
    let quality = 0.86;
    let blob = null;
    do {
      // eslint-disable-next-line no-await-in-loop
      blob = await new Promise((resolve) => { canvas.toBlob(resolve, 'image/jpeg', quality); });
      quality -= 0.12;
    } while (blob && blob.size > COVER_TARGET && quality > 0.4);
    if (!blob) return file;
    const base = String(file.name || 'kapak').replace(/\.[^.]+$/, '');
    return new File([blob], `${base}.jpg`, { type: 'image/jpeg' });
  } catch {
    return file;
  } finally {
    URL.revokeObjectURL(url);
  }
}
