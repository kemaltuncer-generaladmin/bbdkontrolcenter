// BBD Akademi — Deneme tanımı › "Nasıl çözülecek?" ve uygulamada deneme ayarları (F-E9; Akademi README §14 madde 120).
//
// Deneme penceresinin (exams.js `examDialog`) iki özel alanı:
//   deliveryChoice  Kâğıt (optik okuyucu / sonuç yükleme) · Uygulamada — iki büyük seçenek kartı
//   appSettings     pencere (açılış / kapanış), süre, oturumlar (LGS Sözel / Sayısal), kitapçık kuralı, şık sayısı,
//                   sonuç açıklama kuralı, geç gönderim toleransı
// Pencere açıldıktan sonra süre / oturum / bölüm değişmez (Akademi `409 exam_window_started`): alan salt okunur
// künyeye döner, uzatma Canlı sınav sekmesindendir. Kurallar Akademi'de; burada taslak, doğrulama metni, gövde.

import { button, h } from '../../../ui-kit/kit.js';
import { alertBox, hintBox } from '../../../ui-kit/layout.js';
import { clockInput, dateInput, fmtDateTime, kv, multiChips, selectBox } from '../common.js';
import {
  BOOKLET_MODES, DELIVERY_HINT, RESULTS_RELEASE, appFacts, defaultOptionCount, sessionPreset, totalDuration,
  windowMinutes,
} from './exams-app-logic.js';

/**
 * İki seçenekli "Nasıl çözülecek?" kartları. `disabledReason` verilirse "Uygulamada" seçilemez ve sebebi yazar.
 * @returns {{node, set(mode)}}
 */
export function deliveryChoice({ value = 'paper', disabledReason = '', locked = false, onChange }) {
  const node = h('div', 'ak-ex-delivery');
  node.setAttribute('role', 'radiogroup');
  node.setAttribute('aria-label', 'Nasıl çözülecek');
  let current = value === 'app' ? 'app' : 'paper';
  const cards = new Map();
  for (const mode of ['paper', 'app']) {
    const card = h('button', 'ak-ex-delivery-card');
    card.type = 'button';
    card.setAttribute('role', 'radio');
    card.append(h('b', '', mode === 'paper' ? 'Kâğıt' : 'Uygulamada'),
      h('small', '', mode === 'app' && disabledReason ? disabledReason : DELIVERY_HINT[mode]));
    const blocked = locked || (mode === 'app' && Boolean(disabledReason) && current !== 'app');
    card.disabled = blocked;
    card.addEventListener('click', () => { if (current !== mode) { current = mode; paint(); onChange?.(mode); } });
    cards.set(mode, card);
    node.append(card);
  }
  function paint() {
    for (const [mode, card] of cards) {
      card.classList.toggle('on', mode === current);
      card.setAttribute('aria-checked', mode === current ? 'true' : 'false');
    }
  }
  paint();
  return { node, set(mode) { current = mode; paint(); } };
}

function field(label, control, hint = '') {
  const wrap = h('label', 'kit-field');
  wrap.append(h('span', 'kit-field-label', label), control);
  if (hint) wrap.append(h('span', 'kit-field-hint', hint));
  return wrap;
}

function numberInput(value, onInput, label) {
  const input = h('input', 'kit-input ak-ex-count');
  input.inputMode = 'numeric';
  input.value = value ?? '';
  input.setAttribute('aria-label', label);
  input.addEventListener('input', () => onInput(input.value.trim() === '' ? null : Number(input.value)));
  return input;
}

/**
 * Uygulamada deneme ayarları. `draft` taslağın ayar kopyasıdır (`appDraftFromExam` / `defaultAppDraft`); her
 * değişiklikte `set(kopya)`. `sections()` formdaki güncel bölümleri, `type()` güncel türü verir.
 * @returns {{node, sectionsChanged(), typeChanged(type), destroy()}}
 */
export function appSettings({ draft: given, sections, type, bookletCount, lockedApp = null, set }) {
  const node = h('div', 'ak-stack ak-ex-app');
  if (lockedApp) {
    node.append(alertBox('Pencere açıldı: süre, oturumlar ve bölümler artık değişmez. Ek süre, duraklatma ve pencereyi '
      + 'uzatma “Canlı sınav” sekmesindedir.', 'info'), kv(appFacts(lockedApp, fmtDateTime)));
    return { node, sectionsChanged() {}, typeChanged() {}, destroy() {} };
  }
  const draft = { ...given, sessions: (given.sessions || []).map((item) => ({ ...item, sectionOrders: [...item.sectionOrders] })) };
  const commit = () => { set({ ...draft, sessions: draft.sessions.map((item) => ({ ...item })) }); paintSummary(); };

  // ----------------------------------------------------- pencere ve süre
  const opensDate = dateInput(draft.opensDate, (iso) => { draft.opensDate = iso || ''; commit(); }, 'Açılış tarihi');
  const opensClock = clockInput(draft.opensClock, (text) => { draft.opensClock = text; commit(); }, 'Açılış saati');
  const closesDate = dateInput(draft.closesDate, (iso) => { draft.closesDate = iso || ''; commit(); }, 'Kapanış tarihi');
  const closesClock = clockInput(draft.closesClock, (text) => { draft.closesClock = text; commit(); }, 'Kapanış saati');
  const opens = h('div', 'ak-inline');
  opens.append(opensDate.node, opensClock.node);
  const closes = h('div', 'ak-inline');
  closes.append(closesDate.node, closesClock.node);
  const duration = numberInput(draft.durationMinutes, (value) => { draft.durationMinutes = value; commit(); }, 'Süre (dakika)');
  const durationField = field('Süre (dakika)', duration, 'Tek oturumun süresi; oturumlara bölünürse oturum süreleri toplanır.');

  const booklet = selectBox(Object.entries(BOOKLET_MODES).map(([value, label]) => ({ value, label })), draft.bookletMode,
    (value) => { draft.bookletMode = value; commit(); }, { label: 'Kitapçık kuralı' });
  const options = selectBox([{ value: '', label: '' }, { value: '4', label: '4 şık (A–D)' }, { value: '5', label: '5 şık (A–E)' }],
    draft.optionCount ? String(draft.optionCount) : '', (value) => { draft.optionCount = value ? Number(value) : null; commit(); },
    { label: 'Şık sayısı' });
  const release = selectBox(Object.entries(RESULTS_RELEASE).map(([value, label]) => ({ value, label })), draft.resultsRelease,
    (value) => { draft.resultsRelease = value; commit(); }, { label: 'Sonuç açıklama' });
  const grace = selectBox([{ value: '60', label: '1 dakika' }, { value: '120', label: '2 dakika (önerilen)' },
    { value: '300', label: '5 dakika' }, { value: '600', label: '10 dakika' }], String(draft.graceSeconds ?? 120),
  (value) => { draft.graceSeconds = Number(value); commit(); }, { label: 'Geç gönderim toleransı' });

  const grid = h('div', 'kit-form-grid');
  const bookletHint = h('span', 'kit-field-hint');
  const bookletField = field('Kitapçık kuralı', booklet.node);
  bookletField.append(bookletHint);
  grid.append(
    field('Açılış', opens, 'Öğrenci bu andan önce başlayamaz.'),
    field('Kapanış', closes, 'Bu andan sonra yeni başlatma olmaz; süren oturum kapanışta biter.'),
    durationField, bookletField,
    field('Şık sayısı', options.node),
    field('Sonuç açıklama', release.node, '“Sınav kapanınca”: pencere kapanınca hesaplanır ve kendiliğinden yayımlanır.'),
    field('Geç gönderim toleransı', grace.node, 'Çevrimdışı kalan cihazın cevapları bu süre içinde gelirse sayılır.'),
  );

  // ----------------------------------------------------------- oturumlar
  const sessionBox = h('div', 'ak-stack ak-ex-sessions');
  const sessionList = h('div', 'ak-stack');
  const split = button('Oturumlara böl', { onClick: () => {
    const preset = sessionPreset(type(), sections());
    draft.sessions = preset.length ? preset : [
      { key: 'oturum-1', title: '1. Oturum', durationMinutes: Number(draft.durationMinutes) || 60,
        sectionOrders: sections().map((item, index) => index) },
    ];
    paintSessions();
    commit();
  } });
  const single = button('Tek oturum', { variant: 'ghost', onClick: () => {
    draft.durationMinutes = totalDuration(draft) || draft.durationMinutes;
    duration.value = draft.durationMinutes ?? '';
    draft.sessions = [];
    paintSessions();
    commit();
  } });
  const addSession = button('Oturum ekle', { onClick: () => {
    draft.sessions.push({ key: '', title: `${draft.sessions.length + 1}. Oturum`, durationMinutes: 60, sectionOrders: [] });
    paintSessions();
    commit();
  } });
  sessionBox.append(h('span', 'kit-field-label', 'Oturumlar'), sessionList);

  function paintSessions() {
    sessionList.replaceChildren();
    const list = sections();
    const many = draft.sessions.length > 0;
    durationField.hidden = many;
    if (!many) {
      const line = h('div', 'ak-inline');
      line.append(h('span', 'ak-muted', 'Tek oturum: bütün bölümler tek seferde, tek süreyle çözülür.'), split);
      sessionList.append(line);
      if (type() === 'lgs') sessionList.append(hintBox('LGS iki oturumludur: “Oturumlara böl” Sözel (75 dk) ve Sayısal (80 dk) '
        + 'bölümlerini kendiliğinden kurar.'));
      return;
    }
    draft.sessions.forEach((session, index) => {
      const row = h('div', 'ak-ex-session-row');
      const title = h('input', 'kit-input');
      title.value = session.title || '';
      title.maxLength = 60;
      title.setAttribute('aria-label', `${index + 1}. oturum adı`);
      title.addEventListener('input', () => { session.title = title.value; commit(); });
      const minutes = numberInput(session.durationMinutes, (value) => { session.durationMinutes = value; commit(); },
        `${index + 1}. oturum süresi (dakika)`);
      const chips = multiChips(list.map((item, order) => ({ value: order, label: item.title || `${order + 1}. bölüm` })),
        session.sectionOrders.map(String), (values) => { session.sectionOrders = values.map(Number).sort((a, b) => a - b); commit(); });
      const remove = button('Kaldır', { variant: 'ghost', onClick: () => {
        draft.sessions.splice(index, 1); paintSessions(); commit();
      } });
      const head = h('div', 'ak-inline');
      head.append(title, minutes, h('span', 'ak-muted', 'dk'), h('span', 'kit-spacer'), remove);
      row.append(head, chips.node);
      sessionList.append(row);
    });
    const tools = h('div', 'ak-inline');
    tools.append(addSession, single);
    sessionList.append(tools);
  }

  const summary = h('div', 'ak-ex-app-summary');
  summary.setAttribute('aria-live', 'polite');
  function paintSummary() {
    const window = windowMinutes(draft);
    const total = totalDuration(draft);
    const short = window !== null && total && window < total;
    summary.className = `ak-ex-app-summary${short ? ' bad' : ''}`;
    summary.textContent = window === null ? 'Açılış ve kapanışı yazın.'
      : `Pencere ${window} dk · sınav ${total || '?'} dk${short ? ' — pencere süreden kısa olamaz' : ''}`;
    const count = bookletCount();
    bookletHint.textContent = draft.bookletMode !== 'single' && count < 2
      ? 'Bu kural için en az iki kitapçık türü seçin (ör. A ve B).'
      : draft.bookletMode === 'student_picks' ? 'Öğrenci başlarken elindeki kitapçığı seçer; sonradan değişmez.' : '';
    options.node.options[0].textContent = `Türden (${defaultOptionCount(type())} şık)`;
  }

  node.append(grid, sessionBox, summary);
  paintSessions();
  paintSummary();
  commit();

  return {
    node,
    sectionsChanged() {
      const count = sections().length;
      for (const session of draft.sessions) session.sectionOrders = session.sectionOrders.filter((order) => order < count);
      paintSessions();
      commit();
    },
    typeChanged() { paintSessions(); paintSummary(); },
    destroy() { opensDate.destroy?.(); closesDate.destroy?.(); },
  };
}
