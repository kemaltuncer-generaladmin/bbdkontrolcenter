// Öğrenci Yönetimi paneli — CANLI KANTİN VERİSİ.
//
// Veri kantinde durur, burada kopyası tutulmaz. Ekran çekirdeğe sorar,
// çekirdek kantin geçidinden okur. Yazma da aynı yoldan gider.
//
// ÇAKIŞMAMA KURALI: yalnızca DEĞİŞTİRİLEN alanlar gönderilir. Kasa tableti
// aynı anda başka bir alanı düzenliyorsa onun yazdığı korunur — kantin API'si
// gönderilmeyen alana dokunmaz.

import {
  classSortKey, formatBalance, formatMoney, formatPhone, fullName, normalizeClass, normalizePhone,
  validate,
} from './student.js';
// Yol kabuğun KOPYALANMIŞ konumuna göredir: shell/panels/bbd_students/ →
// shell/ui-kit/. Kaynakta (`modules/bbd_students/ui/panel/`) çözülmez —
// normaldir (bkz. `tools/build-ui-registry.py`).
import { createPicker } from '../../ui-kit/picker.js';
import { reportChain } from '../../ui-kit/report.js';
import { confirmSimple } from '../../ui-kit/kit.js';
import { mountControls } from './canteen.js';
import { mountGroups, mountStudentGroups } from './groups.js';
import { askDeactivate, askDelete, mountStatus, watchEvents } from './lifecycle.js';
import {
  EXAM_CHOICES, GRADE_CHOICES, TRACK_CHOICES, describe, fillFromSuggestion, parseChoice,
  parseGrade, suggestedExam, trackAllowed,
} from './curriculum.js';

let apiCall = null;
let capabilityOf = null;  // başka modülün arayüz yeteneği (K3); yoksa null döner
let permissions = null;   // null = okunamadı; karar backend'de (K9)
let controlsView = null;  // veli kantin kontrolleri bölümü
let payCard = null;       // bbd_payment_request.student_card
let groupsView = null;    // "Gruplar" sekmesi (ilk açılışta kurulur)
let studentGroups = null; // öğrenci kartındaki "Gruplar" bölümü
let mountedFor = null;    // bölümlerin çizildiği öğrenci
let students = [];
let selectedId = null;
let original = null;      // seçili öğrencinin sunucudan gelen hâli
let draft = null;         // ekranda düzenlenen hâli
let connected = false;
// Durum süzgecinin varsayılanı "Etkin" (km-merkezi-senkron §5.2): pasifler ayrı.
let filters = { text: '', className: '', flag: '', status: 'active' };
let statusView = null;    // öğrenci kartındaki durum bölümü
let stopWatch = null;     // canlı yenileme (uzun yoklama) durdurucu
let refreshTimer = null;
let mode = 'single';      // 'single' | 'bulk' | 'groups'
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
//
// BORÇ LİMİTİ VE KURUM ENGELİ BURADA DEĞİL: ikisi de veli kantin kontrolleriyle
// birlikte "Veli kantin kontrolleri" bölümünde, kendi izniyle
// (`bbd_students.canteen_controls`) düzenlenir. Aynı alanı iki yerden yazmak,
// hangisinin son sözü söylediğini belirsiz bırakırdı.
const CANTEEN_KEYS = ['firstName', 'lastName', 'parentPhone'];
const PROFILE_KEYS = ['className', 'schoolNo', 'studentPhone', 'parentName', 'parentName2',
  'parentPhone2', 'note', 'gradeLevel', 'track', 'examTarget'];

/** Kullanıcının izni var mı? Okunamadıysa düğme açık kalır, backend karar verir. */
function can(permission) {
  if (permissions === null) return true;
  return permissions.has(permission) || permissions.has(`${permission}:*`);
}

async function loadPermissions() {
  try {
    const me = await apiCall('/auth/me');
    permissions = new Set(Array.isArray(me?.permissions) ? me.permissions : []);
  } catch {
    permissions = null;
  }
}

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
  if (keepSelection && dirtyKeys().length > 0
      && students.some((student) => student.kantinId === selectedId)) {
    // Düzenleme sürerken taslak EZİLMEZ: yalnız sunucu hâli güncellenir.
    const fresh = students.find((student) => student.kantinId === selectedId);
    original = { ...fresh, ...Object.fromEntries(dirtyKeys().map((key) => [key, original[key]])) };
    nodes.dirty.textContent = 'Bu öğrenci başka yerde de değişti — kaydetmeden önce kontrol edin';
  } else {
    loadSelection();
  }
  mountedFor = mountedFor === selectedId && statusView ? mountedFor : null;
  renderList();
  renderDetail();
  syncBulkPicker();
}

/** Canlı yenileme: olaylar 300 ms birikip TEK yenilemeye iner. */
function scheduleRefresh(items) {
  const mine = items.some((item) => item.subject?.id === selectedId);
  const controlsTouched = items.some((item) => item.subject?.id === selectedId
    && (item.type === 'canteen.controls_changed' || item.type === 'canteen.balance_changed'));
  clearTimeout(refreshTimer);
  refreshTimer = setTimeout(async () => {
    await refresh({ keepSelection: true });
    if (mine) {
      statusView?.destroy();
      statusView = selected() ? mountStatus(nodes.statusCard, { api: apiCall, student: selected() }) : null;
    }
    // Kontrol bölümünde yazılmakta olan limit ezilmesin.
    if (controlsTouched && !nodes.controls?.contains(document.activeElement)) controlsView?.reload();
  }, 300);
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
  if (!await confirmSimple(nodes.root, {
    title: 'Yeni giriş kodu üret',
    description: `${name} için yeni bir giriş kodu üretilecek. Öğrencinin şu anki kodu geçersiz olacak.`,
    confirmLabel: 'Yeni kod üret',
    danger: true,
  })) {
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
  nodes.groups.hidden = mode !== 'groups';
  nodes.bar.hidden = mode === 'groups';
  nodes.modeSingle.classList.toggle('st-mode-active', mode === 'single');
  nodes.modeBulk.classList.toggle('st-mode-active', mode === 'bulk');
  nodes.modeGroups.classList.toggle('st-mode-active', mode === 'groups');
  if (mode === 'bulk') syncBulkPicker();
  if (mode === 'groups') {
    // Sekme ilk açılışta kurulur; sonra her açılışta listesi tazelenir.
    if (groupsView === null) {
      groupsView = mountGroups(nodes.groups, {
        api: apiCall, can, students: () => students, dialogRoot: nodes.root, toast,
      });
    } else {
      groupsView.reload();
    }
  }
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
  if (!await confirmSimple(nodes.root, {
    title: 'Toplu giriş kodu üret',
    description: `${ids.length} öğrenci için yeni giriş kodu üretilecek. Mevcut kodları geçersiz olacak.`,
    confirmLabel: 'Kodları üret',
    danger: true,
  })) {
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

/**
 * Toplu seviye/alan/hedef sınav ataması. "Değiştirme" seçili alan gönderilmez;
 * "—" alanı temizler. Kural öğrenci başına backend'de uygulanır: 10. sınıf
 * altındaki öğrenciye alan yazılmaz ve gerekçesiyle ayrı listelenir.
 */
function buildBulkCurriculum() {
  const box = h('div', 'st-bulk-plan');
  box.append(h('p', 'st-subhead', 'Seviye / alan ata'));
  const keep = ['__keep', 'değiştirme'];
  const select = (label, choices) => {
    const wrap = h('label', 'st-field');
    wrap.append(h('span', 'st-label', label));
    const input = h('select', 'st-input st-select');
    for (const [value, text] of [keep, ...choices]) input.append(new Option(text, value));
    wrap.append(input);
    return { wrap, input };
  };
  const grade = select('Seviye', GRADE_CHOICES);
  const track = select('Alan', TRACK_CHOICES);
  const exam = select('Hedef sınav', EXAM_CHOICES);
  box.append(grade.wrap, track.wrap, exam.wrap);

  const status = h('p', 'st-bulk-status', '');

  const send = async (ids, body) => apiCall('/api/bbd_students/students/curriculum', {
    method: 'POST', body: { students: ids, ...body },
  });

  const apply = h('button', 'st-btn st-btn-primary', 'Seçilenlere uygula');
  apply.type = 'button';
  apply.disabled = !can('bbd_students.manage');
  apply.addEventListener('click', async () => {
    const ids = bulkPicker?.selection() ?? [];
    if (ids.length === 0) { status.textContent = 'Önce en az bir öğrenci seçin.'; return; }
    const body = {};
    if (grade.input.value !== '__keep') body.gradeLevel = parseGrade(grade.input.value);
    if (track.input.value !== '__keep') body.track = parseChoice(track.input.value);
    if (exam.input.value !== '__keep') body.examTarget = parseChoice(exam.input.value);
    if (Object.keys(body).length === 0) { status.textContent = 'Değiştirilecek alan seçin.'; return; }
    try {
      const result = await send(ids, body);
      status.textContent = `${result.updated.length} öğrenci güncellendi.`
        + (result.failed.length ? ` ${result.failed.length} öğrenci atlandı: `
          + `${result.failed[0].error}` : '');
      await refresh({ keepSelection: true });
    } catch (error) {
      status.textContent = `Uygulanamadı: ${error.message}`;
    }
  });

  const suggest = h('button', 'st-btn', 'Sınıf adından öneriyi uygula');
  suggest.type = 'button';
  suggest.title = 'Seçilenlerin YALNIZ boş alanlarını sınıf adından gelen öneriyle doldurur.';
  suggest.disabled = !can('bbd_students.manage');
  suggest.addEventListener('click', async () => {
    const ids = new Set(bulkPicker?.selection() ?? []);
    if (ids.size === 0) { status.textContent = 'Önce en az bir öğrenci seçin.'; return; }
    // Aynı değişikliği isteyen öğrenciler tek istekte gider.
    const groups = new Map();
    for (const student of students.filter((row) => ids.has(row.kantinId))) {
      const changes = fillFromSuggestion(student);
      if (Object.keys(changes).length === 0) continue;
      const key = JSON.stringify(changes);
      if (!groups.has(key)) groups.set(key, { changes, ids: [] });
      groups.get(key).ids.push(student.kantinId);
    }
    if (groups.size === 0) {
      status.textContent = 'Seçilenlerde öneriyle doldurulacak boş alan yok.';
      return;
    }
    const total = [...groups.values()].reduce((sum, group) => sum + group.ids.length, 0);
    if (!await confirmSimple(nodes.root, {
      title: 'Öneri uygulansın mı?',
      description: `${total} öğrencinin boş seviye/alan/hedef sınav alanı sınıf adından `
        + 'gelen öneriyle doldurulacak. Dolu alanlara dokunulmaz.',
      confirmLabel: 'Uygula',
    })) return;
    let updated = 0;
    let failed = 0;
    for (const group of groups.values()) {
      try {
        const result = await send(group.ids, group.changes);
        updated += result.updated.length;
        failed += result.failed.length;
      } catch {
        failed += group.ids.length;
      }
    }
    status.textContent = `${updated} öğrenci güncellendi${failed ? `, ${failed} atlandı` : ''}.`;
    await refresh({ keepSelection: true });
  });

  box.append(h('div', 'st-bulk-actions', apply, suggest), status);
  return box;
}

// ------------------------------------------------------- yaşam döngüsü (§5)

async function lifecycleCall(path, options, done) {
  try {
    const result = await apiCall(path, options);
    await refresh({ keepSelection: true });
    mountedFor = null;
    renderDetail();
    done?.(result);
  } catch (error) {
    renderErrors([error.message]);
  }
}

async function deactivate() {
  if (!draft) return;
  const answer = await askDeactivate(nodes.root, { students: [draft] });
  if (!answer) return;
  await lifecycleCall(`/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}/deactivate`,
    { method: 'POST', body: answer }, (result) => {
      if (result?.scheduledFor) toast(`Pasifleştirme ${result.scheduledFor} için planlandı.`);
      else if (result?.canteenSync === 'pending') {
        toast('Pasifleştirildi — kantine iletilemedi, otomatik yeniden denenecek.');
      } else toast('Pasifleştirildi.');
    });
}

async function reactivate() {
  if (!draft) return;
  const scheduled = draft.status !== 'inactive';
  if (!await confirmSimple(nodes.root, {
    title: scheduled ? 'Planlı pasifleştirme iptal edilsin mi?' : 'Yeniden etkinleştir',
    description: scheduled ? 'Öğrenci etkin kalır.'
      : 'Kantin satışı ve BBD Akademi girişi yeniden açılır (kişi yeniden giriş yapar). '
        + 'GRUP ÜYELİKLERİ KENDİLİĞİNDEN AÇILMAZ — gerekiyorsa “Gruplar” sekmesinden '
        + 'elle ekleyin.',
    confirmLabel: scheduled ? 'Planı iptal et' : 'Yeniden etkinleştir',
  })) return;
  await lifecycleCall(`/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}/reactivate`,
    { method: 'POST' }, (result) => toast(result?.scheduleCancelled
      ? 'Plan iptal edildi.' : 'Yeniden etkinleştirildi — grup üyeliklerini kontrol edin.'));
}

async function removeStudent() {
  if (!draft) return;
  const confirmName = await askDelete(nodes.root, { student: draft });
  if (!confirmName) return;
  try {
    await apiCall(`/api/bbd_students/students/${encodeURIComponent(draft.kantinId)}`,
      { method: 'DELETE', body: { confirmName } });
    selectedId = null;
    await refresh({ keepSelection: false });
    toast('Öğrenci kalıcı silindi.');
  } catch (error) {
    renderErrors([`Silinemedi: ${error.message}`]);
  }
}

async function bulkDeactivate() {
  const ids = bulkPicker?.selection() ?? [];
  if (ids.length === 0) {
    nodes.bulkStatus.textContent = 'Önce en az bir öğrenci seçin.';
    return;
  }
  const chosen = students.filter((student) => ids.includes(student.kantinId));
  const answer = await askDeactivate(nodes.root, { students: chosen });
  if (!answer) return;
  try {
    const result = await apiCall('/api/bbd_students/students/deactivate',
      { method: 'POST', body: { students: ids, ...answer } });
    const parts = [`${result.deactivated?.length || 0} pasifleştirildi`];
    if (result.scheduled?.length) parts.push(`${result.scheduled.length} planlandı`);
    if (result.failed?.length) parts.push(`${result.failed.length} atlandı`);
    nodes.bulkStatus.textContent = `${parts.join(' · ')}. Kantin çağrıları sırayla gidiyor.`;
    await refresh({ keepSelection: true });
  } catch (error) {
    nodes.bulkStatus.textContent = `Pasifleştirilemedi: ${error.message}`;
  }
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

  side.append(buildBulkCurriculum());

  if (can('bbd_students.lifecycle')) {
    side.append(h('p', 'st-subhead', 'Yaşam döngüsü'));
    const off = h('button', 'st-btn st-btn-danger', 'Seçilenleri pasifleştir');
    off.type = 'button';
    off.title = 'Yıl sonu mezunlar gibi toplu pasifleştirme; her öğrenci ayrı kaydedilir.';
    off.addEventListener('click', bulkDeactivate);
    side.append(off);
  }

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
  if (filters.status && (student.status || 'active') !== filters.status) return false;
  if (filters.className && student.className !== filters.className) return false;
  if (filters.flag === 'pending' && student.canteenSync !== 'pending') return false;
  if (filters.flag === 'draft' && student.source !== 'canteen') return false;
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

    const balance = h('span', 'st-item-balance', formatBalance(student.balance));
    balance.classList.toggle('debt', Number(student.balance) > 0);
    balance.classList.toggle('credit', Number(student.balance) < 0);
    item.append(balance);

    if (student.isBlocked) {
      const badge = h('i', 'st-badge', 'engelli');
      item.append(badge);
    }
    if (student.status === 'inactive') {
      item.classList.add('st-item-inactive');
      item.append(h('i', 'st-badge st-badge-inactive', 'pasif'));
    } else if (student.scheduledDeactivation) {
      item.append(h('i', 'st-badge st-badge-warn', `${student.scheduledDeactivation} pasif`));
    }
    if (student.canteenSync === 'pending') {
      const badge = h('i', `st-badge ${student.canteenStale ? 'st-badge-bad' : 'st-badge-warn'}`,
        student.canteenStale ? 'kantine iletilemedi (24 sa+)' : 'kantine iletilemedi');
      badge.title = 'Kantine iletilemedi — otomatik yeniden denenecek.';
      item.append(badge);
    }
    if (student.source === 'canteen') {
      const badge = h('i', 'st-badge st-badge-draft', 'kasadan eklendi');
      badge.title = 'Kasadan eklendi — bilgileri tamamla.';
      item.append(badge);
    }

    item.addEventListener('click', async () => {
      if (dirtyKeys().length > 0 && !await confirmSimple(nodes.root, {
        title: 'Kaydedilmemiş değişiklikler',
        description: 'Değişiklikler kaydedilmedi. Başka öğrenciye geçilsin mi?',
        confirmLabel: 'Değişiklikleri bırak',
        danger: true,
      })) return;
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
  } else if (options.type === 'select') {
    input = h('select', 'st-input st-select');
    for (const [value, label] of options.choices || []) input.append(new Option(label, value));
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
  const event = options.type === 'checkbox' || options.type === 'select' ? 'change' : 'input';
  input.addEventListener(event, () => {
    if (!draft) return;
    draft[key] = options.parse ? options.parse(read()) : read();
    options.after?.();
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
  const inactive = student.status === 'inactive';
  nodes.deactivate.hidden = inactive || !can('bbd_students.lifecycle');
  nodes.deactivate.textContent = student.scheduledDeactivation ? 'Pasifleştirme planlı' : 'Pasifleştir';
  nodes.deactivate.disabled = Boolean(student.scheduledDeactivation);
  nodes.reactivate.hidden = !(inactive || student.scheduledDeactivation) || !can('bbd_students.lifecycle');
  nodes.reactivate.textContent = inactive ? 'Yeniden etkinleştir' : 'Planı iptal et';
  nodes.remove.hidden = !inactive || !can('bbd_students.delete');
  nodes.draftNote.hidden = student.source !== 'canteen';
  nodes.subtitle.textContent = [
    student.className || 'sınıf yok',
    `bakiye ${formatBalance(student.balance)}`,
    student.kantinId,
  ].join(' · ');

  nodes.qrOut.hidden = true;
  syncCurriculum();
  renderDirty();
  renderErrors(validate(student));
  mountSections(student);
}

/**
 * Seviye/alan kuralı ekranda da görünsün: 10. sınıf altında alan seçilemez ve
 * seviye o aralığa çekilirse alan boşalır (backend aynı kuralı uygular).
 * Sınıf adından gelen öneri yalnız GÖSTERİLİR; düğmeye basılmadan yazılmaz.
 */
function syncCurriculum() {
  if (!draft || !nodes.fields.track) return;
  const allowed = trackAllowed(draft.gradeLevel ?? null);
  nodes.fields.track.input.disabled = !allowed;
  nodes.fields.track.input.title = allowed ? ''
    : 'Alan yalnız 10. sınıf ve üstünde ya da mezunda seçilir.';
  if (!allowed && draft.track) {
    draft.track = null;
    nodes.fields.track.input.value = '';
  }

  const hint = suggestedExam(draft.gradeLevel ?? null);
  nodes.examHint.textContent = hint && !draft.examTarget ? `Önerilen: ${hint}` : '';

  nodes.suggest.replaceChildren();
  const changes = fillFromSuggestion(draft);
  if (Object.keys(changes).length === 0) return;
  nodes.suggest.append(h('span', null,
    `Sınıf adından öneri (${draft.className}): ${describe(draft.curriculumSuggestion)}`));
  const accept = h('button', 'st-btn', 'Öneriyi uygula');
  accept.type = 'button';
  accept.title = 'Yalnız boş alanları doldurur; kaydetmek için ayrıca “Kaydet”e basın.';
  accept.addEventListener('click', () => {
    Object.assign(draft, fillFromSuggestion(draft));
    for (const key of ['gradeLevel', 'track', 'examTarget']) {
      nodes.fields[key].input.value = draft[key] === null || draft[key] === undefined
        ? '' : String(draft[key]);
    }
    syncCurriculum();
    renderDirty();
  });
  nodes.suggest.append(accept);
}

/**
 * Kantin kontrolleri ve tahsilat/talep kartı — seçili öğrenci DEĞİŞİNCE kurulur.
 * Liste her tazelendiğinde yeniden kurulmaz: yazılmakta olan limit silinmesin.
 */
function mountSections(student) {
  if (mountedFor === student.kantinId) return;
  mountedFor = student.kantinId;
  statusView?.destroy();
  statusView = mountStatus(nodes.statusCard, { api: apiCall, student });
  controlsView?.destroy();
  payCard?.destroy();
  studentGroups?.destroy();
  studentGroups = mountStudentGroups(nodes.studentGroups, {
    api: apiCall, studentId: student.kantinId,
  });

  controlsView = mountControls(nodes.controls, {
    api: apiCall,
    studentId: student.kantinId,
    canEdit: can('bbd_students.canteen_controls'),
    dialogRoot: nodes.root,
    onSaved: () => refresh({ keepSelection: true }),
  });

  const draw = capabilityOf?.('bbd_payment_request.student_card');
  if (typeof draw === 'function') {
    payCard = draw(nodes.payCard, {
      studentId: student.kantinId,
      displayName: student.displayName,
      dialogRoot: nodes.root,
      onChanged: () => refresh({ keepSelection: true }),
    });
  } else {
    payCard = null;
    nodes.payCard.replaceChildren(h('p', 'st-hint',
      'Kantin tahsilatı ve ödeme talebi “Ödeme Talebi” modülünden gelir; '
      + 'modül kapalı ya da bu ekrana yüklenemedi.'));
  }
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

  // Durum (km-merkezi-senkron §5): etkin / pasif, gerekçe, geçmiş.
  nodes.draftNote = h('p', 'st-lc-warn', 'Kasadan eklendi — bilgileri tamamlayıp kaydedin '
    + '(sınıf, veli, seviye). Kaydedince rozet kalkar.');
  body.append(nodes.draftNote);
  const statusCard = h('section', 'st-card');
  statusCard.append(h('h4', null, 'Durum'));
  nodes.statusCard = h('div', 'st-lc');
  statusCard.append(nodes.statusCard);
  body.append(statusCard);

  const kantin = h('section', 'st-card');
  kantin.append(h('h4', null, 'Kantin kaydı'));
  kantin.append(h('p', 'st-hint', 'Bu alanlar kantinde durur; değişiklik kasaya yazılır.'));
  const kantinGrid = h('div', 'st-grid');
  kantinGrid.append(field('Ad', 'firstName', { maxLength: 60 }));
  kantinGrid.append(field('Soyad', 'lastName', { maxLength: 60 }));
  kantinGrid.append(field('Veli telefonu', 'parentPhone', {
    placeholder: '0532 …', display: formatPhone, parse: normalizePhone,
  }));
  kantin.append(kantinGrid);
  kantin.append(h('p', 'st-hint',
    'Borç limiti, günlük limit ve engeller aşağıdaki “Veli kantin kontrolleri” bölümünde.'));
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

  // Seviye / alan / hedef sınav — Akademi müfredatı buradan türer (KM ana kaynak).
  school.append(h('p', 'st-subhead', 'Müfredat'));
  const planGrid = h('div', 'st-grid');
  const display = (value) => (value === null || value === undefined ? '' : String(value));
  planGrid.append(field('Seviye', 'gradeLevel', {
    type: 'select', choices: GRADE_CHOICES, parse: parseGrade, display, after: syncCurriculum,
  }));
  planGrid.append(field('Alan (10. sınıf ve üstü)', 'track', {
    type: 'select', choices: TRACK_CHOICES, parse: parseChoice, display, after: syncCurriculum,
  }));
  const exam = field('Hedef sınav', 'examTarget', {
    type: 'select', choices: EXAM_CHOICES, parse: parseChoice, display, after: syncCurriculum,
  });
  nodes.examHint = h('span', 'st-hint');
  exam.append(nodes.examHint);
  planGrid.append(exam);
  school.append(planGrid);
  nodes.suggest = h('div', 'st-suggest');
  school.append(nodes.suggest);

  school.append(field('Not', 'note', { type: 'textarea', maxLength: 500, wide: true }));
  body.append(school);

  // Gruplar — tarihli üyelik, geçmiş dahil. Düzenleme "Gruplar" sekmesinde.
  const groupCard = h('section', 'st-card');
  groupCard.append(h('h4', null, 'Gruplar'));
  groupCard.append(h('p', 'st-hint',
    'Öğrencinin bütün grup üyelikleri (geçmiş dahil). Eklemek, çıkarmak ve taşımak '
    + 'için üstteki “Gruplar” sekmesi.'));
  nodes.studentGroups = h('div', 'gr-student');
  groupCard.append(nodes.studentGroups);
  body.append(groupCard);

  // Veli kantin kontrolleri — kantinde durur, satışta orada uygulanır.
  const controls = h('section', 'st-card');
  controls.append(h('h4', null, 'Veli kantin kontrolleri'));
  controls.append(h('p', 'st-hint',
    'Bu ayarlar kantinde durur ve satış anında kantin uygular. Veli de BBD Akademi '
    + 'uygulamasından değiştirebilir; burada yapılan değişiklik hemen geçerlidir.'));
  nodes.controls = h('div', 'st-cc');
  controls.append(nodes.controls);
  body.append(controls);

  // Kantin tahsilatı / ödeme talebi — akış Ödeme Talebi modülündedir (K3).
  const pay = h('section', 'st-card');
  pay.append(h('h4', null, 'Kantin tahsilatı ve ödeme talebi'));
  pay.append(h('p', 'st-hint',
    'Yalnız KANTİN hesabıdır; kurum (Akademi) ödemeleriyle karışmaz.'));
  nodes.payCard = h('div', 'st-pay');
  pay.append(nodes.payCard);
  body.append(pay);

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

/**
 * Öğrenci seçen paneller bu listeyi okur (K3). Her öğrenci bugün üyesi olduğu
 * grupları da taşır (`groups`) — seçiciler "TYT grubu" gibi süzebilsin. Grup
 * listesi okunamazsa öğrenciler yine döner, `groups` boş kalır.
 */
export function capabilities(ctx) {
  return {
    'bbd_students.list': async () => {
      const [payload, memberships] = await Promise.all([
        ctx.api('/api/bbd_students/students'),
        ctx.api('/api/bbd_students/groups/memberships').catch(() => ({ items: [] })),
      ]);
      const byStudent = new Map();
      for (const item of memberships.items || []) {
        if (!byStudent.has(item.studentId)) byStudent.set(item.studentId, []);
        byStudent.get(item.studentId).push({ id: item.groupId, name: item.groupName, kind: item.kind });
      }
      return (payload.students || []).map((student) => ({
        id: student.kantinId,
        name: student.displayName,
        className: student.className,
        groups: byStudent.get(student.kantinId) || [],
      }));
    },
  };
}

// ---------------------------------------------------------------- kurulum

export function mount(root, ctx) {
  apiCall = ctx.api;
  capabilityOf = typeof ctx.capability === 'function' ? ctx.capability : null;
  mountedFor = null;

  const styleHref = new URL('./panel.css', import.meta.url).href;
  if (!document.querySelector(`link[href="${styleHref}"]`)) {
    const style = h('link');
    style.rel = 'stylesheet';
    style.href = styleHref;
    document.head.append(style);
  }

  const view = h('div', 'st');
  nodes.root = view;

  nodes.kpi = h('div', 'st-kpi');
  view.append(nodes.kpi);

  const modeBar = h('div', 'st-mode-bar');
  nodes.modeSingle = h('button', 'st-mode-btn st-mode-active', 'Tekli düzenle');
  nodes.modeSingle.type = 'button';
  nodes.modeSingle.addEventListener('click', () => setMode('single'));
  nodes.modeBulk = h('button', 'st-mode-btn', 'Toplu işlemler');
  nodes.modeBulk.type = 'button';
  nodes.modeBulk.addEventListener('click', () => setMode('bulk'));
  nodes.modeGroups = h('button', 'st-mode-btn', 'Gruplar');
  nodes.modeGroups.type = 'button';
  nodes.modeGroups.addEventListener('click', () => setMode('groups'));
  modeBar.append(nodes.modeSingle, nodes.modeBulk, nodes.modeGroups);
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
  nodes.statusFilter = h('select', 'st-input st-select');
  for (const [value, label] of [['active', 'Etkin'], ['inactive', 'Pasif'], ['', 'Etkin + pasif']]) {
    nodes.statusFilter.append(new Option(label, value));
  }
  nodes.statusFilter.addEventListener('change', () => {
    filters.status = nodes.statusFilter.value;
    renderList();
  });

  for (const [value, label] of [['', 'Tümü'], ['debt', 'Borçlu'], ['blocked', 'Engelli'],
    ['unclassed', 'Sınıfsız'], ['pending', 'Kantine iletilemeyen'], ['draft', 'Kasadan eklenen']]) {
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

  bar.append(nodes.search, nodes.statusFilter, nodes.classFilter, nodes.flagFilter, nodes.count,
    h('div', 'st-spacer'), reload, add);
  nodes.bar = bar;
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
  nodes.deactivate = h('button', 'st-btn st-btn-danger', 'Pasifleştir');
  nodes.deactivate.type = 'button';
  nodes.deactivate.hidden = true;
  nodes.deactivate.addEventListener('click', deactivate);
  nodes.reactivate = h('button', 'st-btn', 'Yeniden etkinleştir');
  nodes.reactivate.type = 'button';
  nodes.reactivate.hidden = true;
  nodes.reactivate.addEventListener('click', reactivate);
  nodes.remove = h('button', 'st-btn st-btn-danger', 'Kalıcı sil');
  nodes.remove.type = 'button';
  nodes.remove.hidden = true;
  nodes.remove.addEventListener('click', removeStudent);
  head.append(nodes.title, nodes.subtitle, h('div', 'st-spacer'), nodes.dirty,
    nodes.deactivate, nodes.reactivate, nodes.remove, nodes.save);
  nodes.detail.append(head);

  nodes.detailBody = h('div', 'st-detail-body');
  nodes.detail.append(nodes.detailBody);
  split.append(nodes.detail);
  nodes.split = split;
  view.append(split);
  view.append(buildBulkView());
  nodes.groups = h('div', 'st-groups');
  nodes.groups.hidden = true;
  view.append(nodes.groups);

  nodes.toast = h('div', 'st-toast');
  view.append(nodes.toast);

  nodes.fields = {};
  root.replaceChildren(view);
  // İzinler önce okunur: kontrol bölümü düzenleme düğmesini buna göre kurar.
  loadPermissions().finally(() => refresh({ keepSelection: false }));
  // Canlı yenileme: KM'de, kasada ya da BBD Akademi'de olan değişiklik bu ekrana
  // 1 sn içinde düşer (km-merkezi-senkron §8). "Yenile" düğmesi yedek olarak kalır.
  stopWatch = watchEvents({
    api: apiCall,
    types: 'student.*,guardian.*,canteen.*,group.changed,tablet.policy_changed',
    onEvents: (items, { reset }) => scheduleRefresh(reset ? [] : items),
  });

  return () => {
    stopWatch?.();
    stopWatch = null;
    clearTimeout(refreshTimer);
    statusView?.destroy();
    statusView = null;
    clearTimeout(toast.timer);
    bulkPicker?.destroy();
    controlsView?.destroy();
    payCard?.destroy();
    studentGroups?.destroy();
    groupsView?.destroy();
    groupsView = null;
    root.replaceChildren();
  };
}
