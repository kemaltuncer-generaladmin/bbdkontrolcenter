// BBD Akademi — "Belge çıkar": seçili sözleşmelerin (ya da süzgecin tamamının) belgeleri → TEK PDF → önizle → Yazdır.
//
// KULLANICI (28.10.2026): "isteğe bağlı şu şu şu sözleşmeleri çıkar diyeceğiz. şak o sözleşmeler toplu pdf gelecek
// yazdır diyeceğiz yazdıracak". PDF'i Akademi üretir (A17 `admin_buildContractDocuments`; > 40 öğrenci iş olarak
// sıraya girer, A18 ile yoklanır); KM yalnız önizler ve PDF'in KENDİSİNİ yazıcıya verir (`previewAkademiPdf` →
// `/km/documents/akademi` → ortak önizleme penceresi: Yazdır · PDF kaydet). Nüshalar PDF'in içindedir (yazıcıya 1 kopya).
//
// EKSİK BİLGİLİ ÖĞRENCİ İŞİ DÜŞÜRMEZ: Akademi onu atlar ve `skipped` ile bildirir; burada açıkça listelenir (öğrenci +
// eksik alanlar + "Öğrenciler panelinde düzenle").

import { button, dialogA11y, h } from '../../../ui-kit/kit.js';
import { alertBox, progress } from '../../../ui-kit/layout.js';
import { newKey, problem } from '../api.js';
import { previewAkademiPdf } from '../documents.js';
import {
  DOCUMENT_KINDS, KIND_LABEL, RESOLVE_MAX, buildDocumentsRequest, defaultCopies, documentsOutcome, legalNoticeRequest,
  skippedLine, willQueue,
} from './documents-logic.js';

const PATH = '/v1/admin/contracts/documents';

/** Nüsha varsayılanları: ayar okunabiliyorsa `contracts.defaultCopies`, yoksa taslağınki. */
export async function copySetting(app) {
  if (!app.can('admin_getSettings')) return defaultCopies(null);
  try {
    const settings = await app.api.getCached('/v1/admin/settings');
    return defaultCopies(settings?.contracts?.defaultCopies);
  } catch {
    return defaultCopies(null);
  }
}

/** Basit kart pencere (kitin pencere düzeni + odak tuzağı). `close(value)` sözü çözer. */
function dialogShell(app, { title, text = '', wide = false }) {
  const overlay = h('div', 'kit-overlay');
  const card = h('div', `kit-dialog ak-dialog ak-szb-dialog${wide ? ' wide' : ''}`);
  card.setAttribute('role', 'dialog');
  card.append(h('h3', 'kit-dialog-title', title));
  if (text) card.append(h('p', 'kit-dialog-text', text));
  const body = h('div', 'ak-szb-body');
  const actions = h('div', 'kit-dialog-actions');
  card.append(body, actions);
  overlay.append(card);
  let release = () => {};
  let done = null;
  const promise = new Promise((resolve) => { done = resolve; });
  const onKey = (event) => { if (event.key === 'Escape') { event.stopPropagation(); close(null); } };
  function close(value) {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    release();
    done(value);
  }
  overlay.addEventListener('mousedown', (event) => { if (event.target === overlay) close(null); });
  document.addEventListener('keydown', onKey);
  app.root.append(overlay);
  release = dialogA11y(card);
  return { card, body, actions, close, promise };
}

/**
 * "Belge çıkar" penceresi. `target`: {selectedIds: string[], filterLabel: string, count: number|null,
 * resolve: () => Promise<{contractIds?, filter?}>} — `resolve` süzgecin tamamını isteğe çevirir.
 * Seçim yoksa hedef "süzgecin tamamı"dır.
 */
export async function openBuildDialog(app, { selectedIds = [], filterLabel = '', filterCount = null,
  resolveFilter, opener = null } = {}) {
  const copies = await copySetting(app);
  const state = {
    target: selectedIds.length ? 'selection' : 'filter',
    kinds: new Set(['registration', 'meb', 'promissory', 'payment_plan']),
    copies: { ...copies },
    promissoryScope: 'all',
    revision: 'latest',
  };
  const shell = dialogShell(app, { title: 'Belge çıkar', wide: true,
    text: 'Seçtiğiniz belgeler tek PDF olarak hazırlanır; her belge yeni sayfadan başlar, öğrenci sırası korunur. '
      + 'Önizlemeden yazdırabilir ya da PDF olarak kaydedebilirsiniz.' });
  const { body, actions } = shell;

  // --- hedef
  const targetBox = h('fieldset', 'ak-szb-target');
  targetBox.append(h('legend', 'ak-szb-legend', 'Hangi sözleşmeler?'));
  const radio = (value, label, hint, disabled) => {
    const row = h('label', `ak-szb-radio${disabled ? ' off' : ''}`);
    const input = h('input');
    input.type = 'radio';
    input.name = 'ak-szb-target';
    input.value = value;
    input.checked = state.target === value;
    input.disabled = disabled;
    input.addEventListener('change', () => { state.target = value; paint(); });
    const text = h('span', 'ak-cell-stack');
    text.append(h('b', '', label), h('small', '', hint));
    row.append(input, text);
    return row;
  };
  targetBox.append(
    radio('selection', `Seçili ${selectedIds.length} sözleşme`, selectedIds.length
      ? 'Listede işaretlediğiniz sözleşmeler.' : 'Listeden onay kutusuyla seçin.', !selectedIds.length),
    radio('filter', filterCount === null ? 'Süzgece uyan bütün sözleşmeler'
      : `Süzgece uyan bütün sözleşmeler (${filterCount}${filterCount >= 50 ? '+' : ''})`,
    filterLabel || 'Süzgeç yok: bütün geçerli sözleşmeler.', false),
  );

  // --- türler + nüsha
  const kindsBox = h('fieldset', 'ak-szb-kinds');
  kindsBox.append(h('legend', 'ak-szb-legend', 'Belgeler ve nüsha'));
  for (const kind of DOCUMENT_KINDS) {
    const row = h('div', 'ak-szb-kind');
    const label = h('label', 'ak-szb-check');
    const box = h('input');
    box.type = 'checkbox';
    box.checked = state.kinds.has(kind.key);
    box.addEventListener('change', () => {
      if (box.checked) state.kinds.add(kind.key); else state.kinds.delete(kind.key);
      paint();
    });
    label.append(box, h('span', '', kind.label));
    const count = h('select', 'kit-select ak-szb-copies');
    count.setAttribute('aria-label', `${kind.label} nüsha sayısı`);
    for (const value of [1, 2, 3]) {
      const option = h('option', '', `${value} nüsha`);
      option.value = String(value);
      count.append(option);
    }
    count.value = String(state.copies[kind.key]);
    count.addEventListener('change', () => { state.copies[kind.key] = Number(count.value); });
    row.append(label, count);
    row.dataset.kind = kind.key;
    kindsBox.append(row);
  }

  // --- seçenekler
  const options = h('div', 'ak-szb-options');
  const scope = h('label', 'kit-field');
  const scopeSelect = h('select', 'kit-select');
  for (const [value, text] of [['all', 'Bütün taksitler'], ['open', 'Yalnız ödenmemiş taksitler']]) {
    const option = h('option', '', text);
    option.value = value;
    scopeSelect.append(option);
  }
  scopeSelect.addEventListener('change', () => { state.promissoryScope = scopeSelect.value; });
  scope.append(h('span', 'kit-field-label', 'Senet kapsamı'), scopeSelect);
  const revision = h('label', 'kit-field');
  const revisionSelect = h('select', 'kit-select');
  for (const [value, text] of [['latest', 'Son sürüm'], ['signed', 'Son imzalı sürüm']]) {
    const option = h('option', '', text);
    option.value = value;
    revisionSelect.append(option);
  }
  revisionSelect.addEventListener('change', () => { state.revision = revisionSelect.value; });
  revision.append(h('span', 'kit-field-label', 'Sözleşme sürümü'), revisionSelect,
    h('span', 'kit-field-hint', 'Ödeme planı ve yasal uyarı her zaman güncel tahsilatlarla basılır.'));
  options.append(scope, revision);

  const note = h('div', 'ak-szb-note');
  const error = h('div', 'kit-dialog-error');
  body.append(targetBox, kindsBox, options, note, error);

  const go = button('Belgeleri hazırla', { variant: 'primary', onClick: () => submit() });
  actions.append(button('Vazgeç', { onClick: () => shell.close(null) }), go);

  function paint() {
    scope.hidden = !state.kinds.has('promissory');
    for (const row of kindsBox.querySelectorAll('.ak-szb-kind')) {
      row.classList.toggle('on', state.kinds.has(row.dataset.kind));
    }
    const count = state.target === 'selection' ? selectedIds.length : filterCount;
    note.replaceChildren();
    if (count !== null && willQueue(count)) {
      note.append(alertBox(`${count}${state.target === 'filter' && count >= 50 ? '+' : ''} sözleşme: belgeler `
        + 'arka planda hazırlanır, hazır olunca önizleme açılır.', 'info'));
    }
    go.disabled = !state.kinds.size;
  }
  paint();

  async function submit() {
    error.textContent = '';
    go.disabled = true;
    go.textContent = 'Hazırlanıyor…';
    try {
      const target = state.target === 'selection' ? { contractIds: selectedIds } : await resolveFilter();
      const built = buildDocumentsRequest({ target, kinds: [...state.kinds], copies: state.copies,
        promissoryScope: state.promissoryScope, revision: state.revision });
      if (built.errors) {
        error.textContent = built.errors.join(' ');
        return;
      }
      shell.close(built.body);
    } catch (failure) {
      error.textContent = problem(failure, 'Sözleşmeler okunamadı.');
    } finally {
      if (shell.card.isConnected) {
        go.disabled = !state.kinds.size;
        go.textContent = 'Belgeleri hazırla';
      }
    }
  }

  const request = await shell.promise;
  if (!request) return null;
  return runDocuments(app, request, { opener, title: 'Sözleşme belgeleri' });
}

/** "Gecikenlere Yasal Uyarı": gecikmesi olan bütün sözleşmeler için yasal uyarı — tek tık, önizleme açılır. */
export async function overdueLegalNotice(app, { opener = null } = {}) {
  const copies = await copySetting(app);
  return runDocuments(app, legalNoticeRequest(copies), { opener, title: 'Gecikenlere yasal uyarı' });
}

/** Hazırlanıyor penceresi: aşamalar + "Arka planda bırak" (yoklama sürer). */
function busyDialog(app, title) {
  const shell = dialogShell(app, { title });
  const bar = progress(['Gönderildi', 'Belgeler hazırlanıyor', 'Önizleme']);
  const text = h('div', 'ak-muted');
  text.setAttribute('aria-live', 'polite');
  shell.body.append(bar.node, text);
  const state = { background: false };
  shell.actions.append(button('Arka planda bırak', { variant: 'ghost', onClick: () => {
    state.background = true;
    shell.close(null);
  } }));
  return {
    state,
    step(index, label = '') { bar.step(index); text.textContent = label; },
    close: () => { if (shell.card.isConnected) shell.close(null); },
  };
}

/** A18 yoklaması (artan aralık, en çok ~10 dk). */
async function waitJob(app, jobId, onTick) {
  const wait = (ms) => new Promise((resolve) => { setTimeout(resolve, ms); });
  let delay = 1000;
  const deadline = Date.now() + 10 * 60_000;
  let outcome = { state: 'queued', jobId };
  while (outcome.state === 'queued' && Date.now() < deadline) {
    await wait(delay); // eslint-disable-line no-await-in-loop
    delay = Math.min(Math.round(delay * 1.5), 5000);
    outcome = documentsOutcome(await app.api.get(`${PATH}/${encodeURIComponent(jobId)}`)); // eslint-disable-line no-await-in-loop
    onTick?.(outcome);
  }
  return outcome;
}

/**
 * İsteği gönderir, gerekirse işi bekler, atlananları gösterir ve önizlemeyi açar. Hata kullanıcıya bildirimle
 * söylenir; dönüş son durum (`documentsOutcome`) ya da null.
 */
export async function runDocuments(app, request, { opener = null, title = 'Belgeler' } = {}) {
  const busy = busyDialog(app, `${title} hazırlanıyor`);
  busy.step(0, `${request.kinds.map((kind) => KIND_LABEL[kind]).join(', ')}`);
  let outcome;
  try {
    const response = await app.api.call('POST', PATH, { body: request, key: newKey('km-szb') });
    outcome = documentsOutcome(response.data);
    if (outcome.state === 'queued' && outcome.jobId) {
      busy.step(1, 'Çok sayıda belge var; Akademi arka planda hazırlıyor. Bu pencereyi kapatabilirsiniz, '
        + 'hazır olunca önizleme açılır.');
      outcome = await waitJob(app, outcome.jobId, () => {});
    }
  } catch (failure) {
    busy.close();
    app.toast?.(problem(failure, 'Belgeler hazırlanamadı.'), 'bad');
    return null;
  }
  const background = busy.state.background;
  busy.close();
  if (outcome.state === 'queued') {
    app.toast?.('Belgeler hâlâ hazırlanıyor; biraz sonra "Belge çıkar"ı tekrar deneyin.', 'warn');
    return outcome;
  }
  if (outcome.state === 'failed') {
    app.toast?.(outcome.error || 'Belgeler hazırlanamadı.', 'bad');
    return outcome;
  }
  if (background) app.toast?.(`${title} hazır.`, 'good');
  if (outcome.skipped.length || outcome.state !== 'ready') {
    const next = await skippedDialog(app, outcome, { title });
    if (next !== 'preview') return outcome;
  }
  await previewAkademiPdf(app, { kind: 'contract_documents', url: outcome.url,
    name: outcome.fileName.replace(/\.pdf$/i, '') || 'sozlesme-belgeleri', opener });
  return outcome;
}

/**
 * Atlananlar raporu: "N öğrencinin belgesi hazır · M öğrenci atlandı" + öğrenci, sebep ve eksik alanlar +
 * "Öğrenciler panelinde düzenle". Hazır belge varsa "Önizle ve yazdır" ile devam edilir.
 * @returns {Promise<'preview'|null>}
 */
export function skippedDialog(app, outcome, { title = 'Belgeler' } = {}) {
  const ready = outcome.state === 'ready';
  const shell = dialogShell(app, { title: ready ? `${title} hazır` : `${title} hazırlanamadı`, wide: true,
    text: ready
      ? `${outcome.included} öğrencinin belgesi hazır. ${outcome.skipped.length} öğrenci atlandı: eksik bilgiler `
        + 'tamamlanınca yeniden çıkarabilirsiniz.'
      : 'Seçimde basılabilecek belge yok. Atlanan öğrencilerin eksiklerini tamamlayıp tekrar deneyin.' });
  const list = h('ul', 'ak-szb-skipped');
  for (const item of outcome.skipped) {
    const row = h('li', 'ak-szb-skip');
    const text = h('div', 'ak-cell-stack');
    text.append(h('b', '', item.classGroup ? `${item.name} · ${item.classGroup}` : item.name),
      h('small', '', skippedLine(item)));
    row.append(text);
    if (item.studentId && item.missing.length) {
      row.append(button('Öğrenciler panelinde düzenle', { variant: 'ghost', onClick: () => {
        shell.close(null);
        app.go('students', { studentId: item.studentId, tab: 'record' });
      } }));
    }
    list.append(row);
  }
  if (outcome.skipped.length) shell.body.append(list);
  shell.actions.append(button(ready ? 'Kapat' : 'Tamam', { onClick: () => shell.close(null) }));
  if (ready) {
    const go = button(`Önizle ve yazdır (${outcome.included})`, { variant: 'primary',
      onClick: () => shell.close('preview') });
    shell.actions.append(go);
    go.focus();
  }
  return shell.promise;
}

/** Süzgecin tamamını kimliklere çevirirken üst sınır aşıldıysa çağıranın söyleyeceği metin. */
export function truncatedNote(count) {
  return count >= RESOLVE_MAX
    ? `Süzgece uyan sözleşme ${RESOLVE_MAX}'den fazla; ilk ${RESOLVE_MAX} sözleşme basılır. Süzgeci daraltın.`
    : '';
}
