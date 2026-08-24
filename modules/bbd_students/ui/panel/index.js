// Öğrenci Yönetimi paneli — CANLI KANTİN VERİSİ.
//
// Veri kantinde durur, burada kopyası tutulmaz. Ekran çekirdeğe sorar,
// çekirdek kantin geçidinden okur. Yazma da aynı yoldan gider.
//
// ÇAKIŞMAMA KURALI: yalnızca DEĞİŞTİRİLEN alanlar gönderilir. Kasa tableti
// aynı anda başka bir alanı düzenliyorsa onun yazdığı korunur — kantin API'si
// gönderilmeyen alana dokunmaz.

import {
  classSortKey, formatMoney, formatPhone, fullName, normalizeClass, normalizePhone, validate,
} from './student.js';
// Yol kabuğun KOPYALANMIŞ konumuna göredir: shell/panels/bbd_students/ →
// shell/ui-kit/. Kaynakta (`modules/bbd_students/ui/panel/`) çözülmez —
// normaldir (bkz. `tools/build-ui-registry.py`).
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';

let apiCall = null;
let students = [];
let selectedId = null;
let original = null;      // seçili öğrencinin sunucudan gelen hâli
let draft = null;         // ekranda düzenlenen hâli
let connected = false;
let filters = { text: '', className: '', flag: '' };
let mode = 'single';      // 'single' | 'bulk'
let bulkPicker = null;
let bulkChain = null;
const nodes = {};

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

// Kantin alanları ile bizim alanlarımız — kaydederken ayrışırlar.
const CANTEEN_KEYS = ['firstName', 'lastName', 'parentPhone', 'spendingLimit', 'isBlocked'];
const PROFILE_KEYS = ['className', 'schoolNo', 'studentPhone', 'parentName', 'parentName2', 'parentPhone2', 'note'];

function selected() {
  return draft;
}

function dirtyKeys() {
  if (!original || !draft) return [];
  return [...CANTEEN_KEYS, ...PROFILE_KEYS].filter((key) => {
    const a = original[key] ?? '';
    const b = draft[key] ?? '';
    return String(a) !== String(b);
  });
}

// ------------------------------------------------------------------ veri

/** Kuruşu şeritte okunur kılar: 1234567 → "12.345,67 ₺". */
function money(kurus) {
  if (kurus === null || kurus === undefined) return '—';
  return `${(Number(kurus) / 100).toLocaleString('tr-TR', {
    minimumFractionDigits: 2, maximumFractionDigits: 2,
  })} ₺`;
}

/**
 * Üst şerit — kasa panosuyla aynı rakamlar.
 *
 * Grafik değil, sayı: her kutu tek bir başlığı taşır (etiket kısık tonda,
 * değer koyu ve yarı kalın). Renk yalnızca gerçekten durum bildiren yerde
 * kullanılır ve tek başına anlam taşımaz — yanında yazısı da vardır.
 */
function renderSummary(summary) {
  nodes.kpi.replaceChildren();
  if (!summary) return;

  const tiles = [
    { label: 'Açık alacak', value: money(summary.openReceivables), tone: 'debt' },
    { label: 'Borçlu öğrenci', value: String(summary.debtorCount ?? '—') },
    { label: 'Öğrenci lehine', value: money(summary.creditTotal) },
    { label: 'Bugünkü satış', value: money(summary.todaySales) },
    { label: 'Bekleyen ödeme talebi', value: String(summary.pendingRequests ?? '—') },
    { label: 'Engelli öğrenci', value: String(summary.blockedStudents ?? '—'), tone: summary.blockedStudents ? 'warn' : '' },
    { label: 'Toplam öğrenci', value: String(summary.totalStudents ?? '—') },
  ];

  for (const tile of tiles) {
    const box = h('div', 'st-kpi-tile');
    box.append(h('span', 'st-kpi-label', tile.label));
    const value = h('b', `st-kpi-value${tile.tone ? ` ${tile.tone}` : ''}`, tile.value);
    box.append(value);
    nodes.kpi.append(box);
  }

  nodes.kpi.title = summary.source === 'kasa panosu'
    ? 'Rakamlar kasa panosundan okundu — tabletle aynı kaynak.'
    : 'Kasa panosu okunamadı; rakamlar öğrenci bakiyelerinden hesaplandı.';
}

async function refresh({ keepSelection = true } = {}) {
  nodes.status.textContent = 'Kantinden okunuyor…';
  try {
    const payload = await apiCall('/api/bbd_students/students');
    students = payload.students || [];
    connected = payload.connected;
    renderSummary(payload.summary);
    nodes.status.textContent = connected
      ? `Kantine bağlı · ${students.length} öğrenci`
      : `Kantine ulaşılamadı — ${payload.error || 'bilinmeyen hata'}`;
    nodes.status.classList.toggle('bad', !connected);
  } catch (error) {
    connected = false;
    students = [];
    nodes.status.textContent = `Çekirdek hatası: ${error.message}`;
    nodes.status.classList.add('bad');
  }

  if (!keepSelection || !students.some((student) => student.kantinId === selectedId)) {
    selectedId = students[0]?.kantinId ?? null;
  }
  loadSelection();
  renderList();
  renderDetail();
  syncBulkPicker();
}

/** Toplu şifre işlemleri sekmesindeki seçiciyi güncel öğrenci listesiyle besler. */
function syncBulkPicker() {
  if (!bulkPicker) return;
  bulkPicker.setItems(sorted().map((student) => ({
    id: student.kantinId,
    name: student.displayName || 'Adsız',
    group: student.className || 'Sınıf girilmemiş',
    meta: student.accessCode ? `kod var · ${student.schoolNo || '—'}` : `kod yok · ${student.schoolNo || '—'}`,
  })));
}

function loadSelection() {
  const found = students.find((student) => student.kantinId === selectedId) || null;
  original = found ? { ...found } : null;
  draft = found ? { ...found } : null;
}

async function saveChanges() {
  const changed = dirtyKeys();
  if (!draft || changed.length === 0) return;

  const errors = validate(draft);
  if (errors.length > 0) {
    renderErrors(errors);
    return;
  }

  const payload = {};
  for (const key of changed) {
    if (key === 'parentPhone' || key === 'parentPhone2' || key === 'studentPhone') {
      payload[key] = normalizePhone(draft[key]);
    } else if (key === 'className') {
      payload[key] = normalizeClass(draft[key]);
    } else {
      payload[key] = draft[key];
    }
  }
  // Ad ya da soyad değiştiyse kantindeki tek alanlık ad da yenilenir.
  if (changed.includes('firstName') || changed.includes('lastName')) {
    payload.firstName = draft.firstName;
    payload.lastName = draft.lastName;
  }

  nodes.save.disabled = true;
  nodes.save.textContent = 'Kaydediliyor…';
  try {
    await apiCall(`/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}`, {
      method: 'PATCH',
      body: payload,
    });
    await refresh();
    toast('Kaydedildi.');
  } catch (error) {
    renderErrors([`Kaydedilemedi: ${error.message}`]);
  } finally {
    nodes.save.textContent = 'Kaydet';
    renderDirty();
  }
}

async function createStudent() {
  const first = window.prompt('Öğrencinin adı:');
  if (!first) return;
  const last = window.prompt('Öğrencinin soyadı:') || '';

  try {
    const result = await apiCall('/api/bbd_students/students', {
      method: 'POST',
      body: { firstName: first.trim(), lastName: last.trim() },
    });
    selectedId = result.kantinId;
    await refresh();
    toast('Öğrenci kantine eklendi.');
  } catch (error) {
    renderErrors([`Eklenemedi: ${error.message}`]);
  }
}

async function makeQr() {
  if (!draft) return;
  nodes.qrOut.hidden = true;
  if (nodes.codeOut) nodes.codeOut.hidden = true;
  try {
    const result = await apiCall(`/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}/qr`);
    nodes.qrText.textContent = result.qrText;
    nodes.qrOut.hidden = false;
  } catch (error) {
    renderErrors([`QR üretilemedi: ${error.message}`]);
  }
}

/**
 * Tek tuşla giriş kodu sıfırlama.
 *
 * KOD BURADA ÜRETİLMEZ — kantinden istenir; "kimsede olmayan" güvencesini
 * yalnız kantinin unique indeksi verebilir (bkz. service.reset_access_code).
 *
 * TEYİT İSTENİR: sıfırlama öğrencinin o anki kodunu öldürür ve velinin elindeki
 * kâğıt geçersizleşir. Tek dokunuşla geri alınamaz bir iş yapılmaz.
 */
async function resetAccessCode() {
  if (!draft) return;
  const name = fullName(draft) || 'Öğrenci';
  if (!window.confirm(
    `${name} için yeni bir giriş kodu üretilecek.\n\n`
    + 'Öğrencinin şu anki kodu geçersiz olacak. Devam edilsin mi?')) {
    return;
  }
  nodes.codeOut.hidden = true;
  try {
    const result = await apiCall(
      `/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}/access-code`,
      { method: 'POST' },
    );
    nodes.codeText.textContent = result.accessCode;
    nodes.codeOut.hidden = false;
    toast('Yeni giriş kodu üretildi.');
  } catch (error) {
    renderErrors([`Kod sıfırlanamadı: ${error.message}`]);
  }
}

// ------------------------------------------------------- toplu şifre işlemleri
//
// Tek öğrencilik akıştan (yukarısı) ayrı: burada birden çok öğrenci seçilip
// 1) mevcut kodları HİÇBİR ŞEYİ DEĞİŞTİRMEDEN listelenir/yazdırılır, ya da
// 2) hepsine TEK TIKLA yeni kod üretilip yeni liste yazdırılır. Üretim ve
// önizleme/yazdırma zinciri `reportChain` (ui-kit/report.js) ile aynı ortak
// bileşenden geçer — panel kendi önizleme/yazdırma kodunu yazmaz.

/** "Tekli düzenle" / "Toplu şifre işlemleri" sekmesi arasında geçiş. */
function setMode(next) {
  mode = next;
  nodes.split.hidden = mode !== 'single';
  nodes.bulk.hidden = mode !== 'bulk';
  nodes.modeSingle.classList.toggle('st-mode-active', mode === 'single');
  nodes.modeBulk.classList.toggle('st-mode-active', mode === 'bulk');
  if (mode === 'bulk') syncBulkPicker();
}

/**
 * Seçili öğrencilerin ŞU ANKİ kodlarını listeler — HİÇBİR ŞEY DEĞİŞTİRMEZ.
 * Kodu olmayan öğrenci listede yer almaz (`missing` ile raporlanır); onu
 * tamamlamanın yolu "sıfırla"dır, bu akış değil.
 */
async function runAccessCodeList() {
  const ids = bulkPicker?.selection() ?? [];
  if (ids.length === 0) {
    nodes.bulkStatus.textContent = 'Önce en az bir öğrenci seçin.';
    return;
  }
  const result = await bulkChain.run('access-code-list', { students: ids });
  if (!result) return; // reportChain zaten hata toast'ını gösterdi
  const parts = [`${result.count} kod listelendi.`];
  if (result.missing?.length) {
    parts.push(`${result.missing.length} öğrencinin kodu yoktu, listede yer almadı.`);
  }
  nodes.bulkStatus.textContent = parts.join(' ');
}

/**
 * Seçili öğrencilerin HEPSİNE yeni kod üretir. TEYİT İSTENİR — geri
 * alınamaz: kodu olan öğrencilerin şu anki kodu anında geçersizleşir.
 */
async function runAccessCodeReset() {
  const ids = bulkPicker?.selection() ?? [];
  if (ids.length === 0) {
    nodes.bulkStatus.textContent = 'Önce en az bir öğrenci seçin.';
    return;
  }
  if (!window.confirm(
    `${ids.length} öğrenci için yeni giriş kodu üretilecek.\n\n`
    + 'Kodu olan öğrencilerin ŞU ANKİ kodu geçersiz olacak. Devam edilsin mi?')) {
    return;
  }
  const result = await bulkChain.run('access-code-reset', { students: ids });
  if (!result) return;
  const parts = [`${result.count} yeni kod üretildi.`];
  if (result.failed?.length) {
    parts.push(`${result.failed.length} öğrenci sıfırlanamadı — tekrar deneyin.`);
  }
  nodes.bulkStatus.textContent = parts.join(' ');
  await refresh(); // az önce üretilen kodlar `accessCode` alanına yansısın
}

function buildBulkView() {
  const view = h('div', 'st-bulk');
  view.hidden = true;

  const layout = h('div', 'st-bulk-layout');
  bulkPicker = createPicker({
    groupLabel: 'Sınıf',
    placeholder: 'Ad, sınıf, no ara',
    onChange: (ids) => {
      nodes.bulkCount.textContent = ids.length === 0 ? 'Seçim yok' : `${ids.length} öğrenci seçili`;
    },
  });
  layout.append(bulkPicker.node);

  const side = h('div', 'st-bulk-side');
  nodes.bulkCount = h('p', 'st-bulk-count', 'Seçim yok');
  side.append(nodes.bulkCount);

  const listBtn = h('button', 'st-btn', 'Mevcut kodları listele ve yazdır');
  listBtn.type = 'button';
  listBtn.title = 'Hiçbir kodu değiştirmez; kantinde hâlâ geçerli olan kodları gösterir.';
  listBtn.addEventListener('click', runAccessCodeList);
  side.append(listBtn);

  const resetBtn = h('button', 'st-btn st-btn-primary', 'Seçilenlerin kodunu sıfırla ve yazdır');
  resetBtn.type = 'button';
  resetBtn.title = 'Seçili her öğrenciye yeni kod üretir; eski kodlar geçersiz olur.';
  resetBtn.addEventListener('click', runAccessCodeReset);
  side.append(resetBtn);

  nodes.bulkStatus = h('p', 'st-bulk-status', '');
  side.append(nodes.bulkStatus);

  side.append(h('p', 'st-hint',
    'Şifreler her zaman kantin tarafından otomatik üretilir; elle şifre '
    + 'girilmez — iki öğrenciye aynı kodun verilmesi bu yüzden mümkün değildir.'));

  layout.append(side);
  view.append(layout);

  nodes.bulk = view;
  return view;
}

// ----------------------------------------------------------------- çizim

function sorted() {
  return [...students].sort((a, b) => {
    const [an, al] = classSortKey(a.className);
    const [bn, bl] = classSortKey(b.className);
    if (an !== bn) return an - bn;
    if (al !== bl) return String(al).localeCompare(String(bl), 'tr');
    return fullName(a).localeCompare(fullName(b), 'tr');
  });
}

function matches(student) {
  if (filters.className && student.className !== filters.className) return false;
  if (filters.flag === 'blocked' && !student.isBlocked) return false;
  if (filters.flag === 'debt' && Number(student.balance) <= 0) return false;
  if (filters.flag === 'unclassed' && student.className) return false;
  if (filters.text) {
    const needle = filters.text.toLocaleLowerCase('tr');
    const hay = [student.displayName, student.className, student.schoolNo,
      student.parentName, student.parentPhone, student.kantinId]
      .join(' ').toLocaleLowerCase('tr');
    if (!hay.includes(needle)) return false;
  }
  return true;
}

function renderList() {
  const all = sorted();
  const shown = all.filter(matches);
  nodes.count.textContent = all.length === shown.length
    ? `${all.length} öğrenci`
    : `${shown.length} / ${all.length}`;

  const classes = [...new Set(all.map((student) => student.className).filter(Boolean))]
    .sort((a, b) => {
      const [an, al] = classSortKey(a);
      const [bn, bl] = classSortKey(b);
      return an - bn || String(al).localeCompare(String(bl), 'tr');
    });
  const previous = nodes.classFilter.value;
  nodes.classFilter.replaceChildren(new Option('Tüm sınıflar', ''));
  for (const className of classes) nodes.classFilter.append(new Option(className, className));
  nodes.classFilter.value = classes.includes(previous) ? previous : '';

  nodes.list.replaceChildren();
  if (shown.length === 0) {
    nodes.list.append(h('p', 'st-empty', connected ? 'Süzgece uyan öğrenci yok.' : 'Liste boş.'));
    return;
  }

  let lastClass = null;
  for (const student of shown) {
    if (student.className !== lastClass) {
      lastClass = student.className;
      nodes.list.append(h('p', 'st-list-group', student.className || 'Sınıf girilmemiş'));
    }

    const item = h('button', 'st-item');
    item.type = 'button';
    item.classList.toggle('active', student.kantinId === selectedId);

    const main = h('span', 'st-item-main');
    main.append(h('b', null, student.displayName || 'Adsız'));
    main.append(h('span', 'st-item-meta',
      [student.schoolNo && `No ${student.schoolNo}`, formatPhone(student.parentPhone)]
        .filter(Boolean).join(' · ') || '—'));
    item.append(main);

    const balance = h('span', 'st-item-balance', formatMoney(student.balance));
    balance.classList.toggle('debt', Number(student.balance) > 0);
    item.append(balance);

    if (student.isBlocked) {
      const badge = h('i', 'st-badge', 'engelli');
      item.append(badge);
    }

    item.addEventListener('click', () => {
      if (dirtyKeys().length > 0 && !window.confirm('Kaydedilmemiş değişiklik var. Bırakılsın mı?')) return;
      selectedId = student.kantinId;
      loadSelection();
      renderList();
      renderDetail();
    });
    nodes.list.append(item);
  }
}

function field(label, key, options = {}) {
  const wrap = h('label', `st-field${options.wide ? ' st-field-wide' : ''}`);
  wrap.append(h('span', 'st-label', label));

  let input;
  if (options.type === 'textarea') {
    input = h('textarea', 'st-input st-textarea');
  } else if (options.type === 'checkbox') {
    input = h('input', 'st-check');
    input.type = 'checkbox';
  } else {
    input = h('input', 'st-input');
    input.type = 'text';
    if (options.placeholder) input.placeholder = options.placeholder;
  }
  if (options.maxLength) input.maxLength = options.maxLength;
  if (options.readOnly) input.readOnly = true;

  const read = () => (options.type === 'checkbox' ? input.checked : input.value);
  input.addEventListener(options.type === 'checkbox' ? 'change' : 'input', () => {
    if (!draft) return;
    draft[key] = options.parse ? options.parse(read()) : read();
    renderDirty();
    renderErrors(validate(draft));
  });

  wrap.append(input);
  nodes.fields[key] = { input, options };
  return wrap;
}

function renderDetail() {
  const student = selected();
  nodes.detail.classList.toggle('empty', !student);

  if (!student) {
    nodes.detailBody.replaceChildren(h('p', 'st-empty',
      connected ? 'Soldan bir öğrenci seçin.' : 'Kantine bağlanılamadı.'));
    return;
  }

  if (nodes.detailBody.dataset.built !== '1') {
    buildDetail();
    nodes.detailBody.dataset.built = '1';
  }

  for (const [key, { input, options }] of Object.entries(nodes.fields)) {
    const value = student[key] ?? '';
    if (options.type === 'checkbox') input.checked = Boolean(value);
    else input.value = options.display ? options.display(value) : value;
  }

  nodes.title.textContent = student.displayName || 'Adsız öğrenci';
  nodes.subtitle.textContent = [
    student.className || 'sınıf yok',
    `bakiye ${formatMoney(student.balance)}`,
    student.kantinId,
  ].join(' · ');

  nodes.qrOut.hidden = true;
  renderDirty();
  renderErrors(validate(student));
}

function renderDirty() {
  const changed = dirtyKeys();
  nodes.save.disabled = changed.length === 0;
  nodes.dirty.textContent = changed.length === 0
    ? 'Değişiklik yok'
    : `${changed.length} alan değişti — yalnızca bunlar gönderilir`;
  nodes.dirty.classList.toggle('show', changed.length > 0);
}

function renderErrors(errors) {
  nodes.errors.replaceChildren(...errors.map((text) => h('li', null, text)));
  nodes.errors.classList.toggle('show', errors.length > 0);
}

function toast(text) {
  nodes.toast.textContent = text;
  nodes.toast.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => nodes.toast.classList.remove('show'), 3000);
}

function buildDetail() {
  nodes.fields = {};
  const body = nodes.detailBody;
  body.replaceChildren();

  const kantin = h('section', 'st-card');
  kantin.append(h('h4', null, 'Kantin kaydı'));
  kantin.append(h('p', 'st-hint', 'Bu alanlar kantinde durur; değişiklik kasaya yazılır.'));
  const kantinGrid = h('div', 'st-grid');
  kantinGrid.append(field('Ad', 'firstName', { maxLength: 60 }));
  kantinGrid.append(field('Soyad', 'lastName', { maxLength: 60 }));
  kantinGrid.append(field('Veli telefonu', 'parentPhone', {
    placeholder: '0532 …', display: formatPhone, parse: normalizePhone,
  }));
  kantinGrid.append(field('Harcama limiti (kuruş, boş = sınırsız)', 'spendingLimit', {
    parse: (value) => (String(value).trim() === '' ? null : Number(String(value).replace(/\D/g, ''))),
  }));
  kantin.append(kantinGrid);
  const blocked = field('Kantin alışverişi engelli', 'isBlocked', { type: 'checkbox' });
  blocked.classList.add('st-field-inline');
  kantin.append(blocked);
  body.append(kantin);

  const school = h('section', 'st-card');
  school.append(h('h4', null, 'Okul bilgileri'));
  school.append(h('p', 'st-hint',
    'Kantinde bu alanlar YOK; Kontrol Merkezi kaydında durur ve kantin kimliğiyle eşleşir.'));
  const schoolGrid = h('div', 'st-grid');
  schoolGrid.append(field('Sınıf', 'className', { placeholder: '11-A', maxLength: 20 }));
  schoolGrid.append(field('Okul No', 'schoolNo', { maxLength: 20 }));
  schoolGrid.append(field('Öğrenci telefonu', 'studentPhone', {
    placeholder: '0532 …', display: formatPhone, parse: normalizePhone,
  }));
  schoolGrid.append(field('Veli adı', 'parentName', { maxLength: 80 }));
  schoolGrid.append(field('İkinci veli', 'parentName2', { maxLength: 80 }));
  schoolGrid.append(field('İkinci veli telefonu', 'parentPhone2', {
    placeholder: '0532 …', display: formatPhone, parse: normalizePhone,
  }));
  school.append(schoolGrid);
  school.append(field('Not', 'note', { type: 'textarea', maxLength: 500, wide: true }));
  body.append(school);

  const card = h('section', 'st-card');
  card.append(h('h4', null, 'Kart QR’ı'));
  card.append(h('p', 'st-hint',
    'QR kasadaki anahtarla sunucuda üretilir; anahtar bu ekrana hiç inmez. '
    + 'Tablette basılmış kartlar geçerliliğini korur — anahtar aynıdır.'));
  const qrButton = h('button', 'st-btn st-btn-primary', 'QR üret');
  qrButton.type = 'button';
  qrButton.addEventListener('click', makeQr);
  card.append(qrButton);

  nodes.qrOut = h('div', 'st-qr-out');
  nodes.qrText = h('code', 'st-qr-text');
  const copy = h('button', 'st-btn', 'Kopyala');
  copy.type = 'button';
  copy.addEventListener('click', () => navigator.clipboard?.writeText(nodes.qrText.textContent));
  nodes.qrOut.append(nodes.qrText, copy);
  nodes.qrOut.hidden = true;
  card.append(nodes.qrOut);
  body.append(card);

  // Giriş kodu — "tek şifre" kipinde öğrencinin kasada tuşladığı 6 hane.
  const codeCard = h('section', 'st-card');
  codeCard.append(h('h4', null, 'Giriş kodu'));
  codeCard.append(h('p', 'st-hint',
    'Kantin "tek şifre" kipindeyken öğrenci bu 6 haneli kodu kasada tuşlar. '
    + 'Kod kantinde üretilir ve kimsede olmayan bir sayı seçilir; '
    + 'sıfırlandığında öğrencinin eski kodu geçersiz olur.'));
  const codeButton = h('button', 'st-btn st-btn-primary', 'Yeni kod üret');
  codeButton.type = 'button';
  codeButton.addEventListener('click', resetAccessCode);
  codeCard.append(codeButton);

  nodes.codeOut = h('div', 'st-qr-out');
  nodes.codeText = h('code', 'st-code-text');
  const copyCode = h('button', 'st-btn', 'Kopyala');
  copyCode.type = 'button';
  copyCode.addEventListener('click',
    () => navigator.clipboard?.writeText(nodes.codeText.textContent));
  nodes.codeOut.append(nodes.codeText, copyCode);
  // Kod YALNIZ üretildiği anda gösterilir; ekran yeniden çizilince kaybolur.
  nodes.codeOut.hidden = true;
  codeCard.append(nodes.codeOut);
  body.append(codeCard);

  nodes.errors = h('ul', 'st-errors');
  body.append(nodes.errors);
}

// --------------------------------------------------------------- yetenek

/** Ders Takvimi grup kurarken bu listeyi okur (K3). */
export function capabilities(ctx) {
  return {
    'bbd_students.list': async () => {
      const payload = await ctx.api('/api/bbd_students/students');
      return (payload.students || []).map((student) => ({
        id: student.kantinId,
        name: student.displayName,
        className: student.className,
      }));
    },
  };
}

// ---------------------------------------------------------------- kurulum

export function mount(root, ctx) {
  apiCall = ctx.api;

  const styleHref = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${styleHref}"]`)) {
    const style = h('link');
    style.rel = 'stylesheet';
    style.href = styleHref;
    document.head.append(style);
  }

  const view = h('div', 'st');

  nodes.kpi = h('div', 'st-kpi');
  view.append(nodes.kpi);

  const modeBar = h('div', 'st-mode-bar');
  nodes.modeSingle = h('button', 'st-mode-btn st-mode-active', 'Tekli düzenle');
  nodes.modeSingle.type = 'button';
  nodes.modeSingle.addEventListener('click', () => setMode('single'));
  nodes.modeBulk = h('button', 'st-mode-btn', 'Toplu şifre işlemleri');
  nodes.modeBulk.type = 'button';
  nodes.modeBulk.addEventListener('click', () => setMode('bulk'));
  modeBar.append(nodes.modeSingle, nodes.modeBulk);
  view.append(modeBar);

  bulkChain = reportChain({ api: apiCall, root, toast, base: '/api/bbd_students' });

  const bar = h('header', 'st-bar');
  nodes.search = h('input', 'st-search');
  nodes.search.type = 'search';
  nodes.search.placeholder = 'Ad, sınıf, no, telefon ara';
  nodes.search.addEventListener('input', () => {
    filters.text = nodes.search.value.trim();
    renderList();
  });

  nodes.classFilter = h('select', 'st-input st-select');
  nodes.classFilter.addEventListener('change', () => {
    filters.className = nodes.classFilter.value;
    renderList();
  });

  nodes.flagFilter = h('select', 'st-input st-select');
  for (const [value, label] of [['', 'Tümü'], ['debt', 'Borçlu'], ['blocked', 'Engelli'], ['unclassed', 'Sınıfsız']]) {
    nodes.flagFilter.append(new Option(label, value));
  }
  nodes.flagFilter.addEventListener('change', () => {
    filters.flag = nodes.flagFilter.value;
    renderList();
  });

  nodes.count = h('span', 'st-count');

  const reload = h('button', 'st-btn', 'Yenile');
  reload.type = 'button';
  reload.addEventListener('click', () => refresh());

  const add = h('button', 'st-btn st-btn-primary', '+ Yeni öğrenci');
  add.type = 'button';
  add.addEventListener('click', createStudent);

  bar.append(nodes.search, nodes.classFilter, nodes.flagFilter, nodes.count,
    h('div', 'st-spacer'), reload, add);
  view.append(bar);

  nodes.status = h('p', 'st-status', 'Bağlanıyor…');
  view.append(nodes.status);

  const split = h('div', 'st-split');
  nodes.list = h('nav', 'st-list');
  split.append(nodes.list);

  nodes.detail = h('section', 'st-detail');
  const head = h('header', 'st-detail-head');
  nodes.title = h('h3');
  nodes.subtitle = h('span', 'st-subtitle');
  nodes.dirty = h('span', 'st-dirty');
  nodes.save = h('button', 'st-btn st-btn-primary', 'Kaydet');
  nodes.save.type = 'button';
  nodes.save.disabled = true;
  nodes.save.addEventListener('click', saveChanges);
  head.append(nodes.title, nodes.subtitle, h('div', 'st-spacer'), nodes.dirty, nodes.save);
  nodes.detail.append(head);

  nodes.detailBody = h('div', 'st-detail-body');
  nodes.detail.append(nodes.detailBody);
  split.append(nodes.detail);
  nodes.split = split;
  view.append(split);
  view.append(buildBulkView());

  nodes.toast = h('div', 'st-toast');
  view.append(nodes.toast);

  nodes.fields = {};
  root.replaceChildren(view);
  refresh({ keepSelection: false });

  return () => {
    clearTimeout(toast.timer);
    bulkPicker?.destroy();
    root.replaceChildren();
  };
}
