// BBD Akademi — Denemeler › Canlı sınav (F-E11; Akademi README §14 madde 120).
//
// Kullanıcı (16.10.2026): "ayrıca uzaktan ek süre verme vs o özellikler de olacak". Uygulamada çözülen
// denemenin gözetmen ekranı: sayaçlar (başlamadı / çözüyor / bitirdi / süresi doldu), öğrenci tablosu (cevaplanan,
// kalan süre, son görülme, uygulamadan çıkış), çoklu seçim → ek süre (+5 / +10 / +15 / serbest), oturumu bitir,
// yeniden aç (gerekçe zorunlu); denemenin tamamı için duraklat / sürdür (onaylı) ve pencereyi aç / kapat / uzat.
//
// ANLIK: Akademi'nin değişiklik akışı (`/v1/me/events`, `exams.attempt` / `exams.monitor` / `exams.live`) kişiye
// bağlıdır; KM vekili entegrasyon anahtarıyla konuştuğu için akışı açamaz → ekran 10 sn'de bir yoklar (sekme
// gizliyken durur) ve her işlemin yanıtındaki güncel tabloyu (`ExamLiveActionResult.live`) hemen çizer. Kalan süre
// sunucu saati farkıyla (`serverNow`) yerelde saniye saniye akar; duraklatmada donar.
//
// Yetki: `canManage=false` (öğretmen gözetmen) duraklat / yeniden aç / pencere düğmelerini görmez; asıl kapı
// vekil + Akademi'dedir.

import { button, h, menuButton, num, pollLoop } from '../../../ui-kit/kit.js';
import { alertBox, badge, chipRow, kpiRow } from '../../../ui-kit/layout.js';
import { dataTable } from '../../../ui-kit/table.js';
import {
  actionButton, confirmAction, empty, errorBox, fmtDateTime, fmtTime, formDialog, problem, searchBox, selectBox,
  statusBadge, toolbar,
} from '../common.js';
import {
  APP_STATE, LIVE_STATE, SUBMIT_REASON, actionToast, clockOffset, clockText, extendBody, filterLive, isQuiet,
  joinStamp, liveClasses, remainingSeconds, seenText, selectionInfo, splitStamp,
} from './exams-app-logic.js';
import { studentCell } from './exams-shared.js';

export const LIVE_POLL_MS = 10_000;
const EXTRA_PRESETS = [5, 10, 15];

export function liveTab(app, exam, { onChanged } = {}) {
  const node = h('div', 'ak-stack ak-ex-live');
  const base = `/v1/admin/exams/${exam.id}/live`;
  let live = null;
  let offsetMs = 0;
  let loadedAt = 0;
  let failure = null;
  let busy = false;
  const filters = { state: '', classGroupId: '', q: '' };
  let ticking = [];           // {span, row} — saniyelik geri sayım hücreleri

  const head = h('div', 'ak-stack');
  const kpiSlot = h('div');
  const banner = h('div', 'ak-stack');
  const stamp = h('span', 'ak-muted ak-ex-live-stamp');
  stamp.setAttribute('aria-live', 'polite');
  const refresh = button('Yenile', { variant: 'ghost', onClick: () => poll.now() });

  const stateChips = chipRow(Object.entries(LIVE_STATE).map(([key, [label]]) => ({ key, label, count: 0 })), null,
    (key) => { filters.state = key || ''; paint(); });
  stateChips.node.classList.add('ak-ex-chips');
  const classSelect = selectBox([{ value: '', label: 'Tüm sınıflar' }], '', (value) => {
    filters.classGroupId = value; paint();
  }, { label: 'Sınıf' });
  const search = searchBox({ placeholder: 'Öğrenci ara', label: 'Öğrenci ara', delay: 150,
    onSearch: (text) => { filters.q = text; paint(); } });

  const selectionText = h('span', 'ak-muted');
  const extendSelected = menuButton('Ek süre ver', [
    ...EXTRA_PRESETS.map((minutes) => ({ label: `+${minutes} dk`, onClick: () => extend(minutes, true) })),
    { label: 'Serbest süre…', onClick: () => extend(null, true) },
  ]);
  const finishSelected = button('Oturumu bitir', { onClick: () => finish() });
  const reopenSelected = button('Yeniden aç…', { onClick: () => reopen() });
  const clearSelected = button('Seçimi kaldır', { variant: 'ghost', onClick: () => { table.clearSelection(); paintSelection(); } });
  const selectionBar = h('div', 'ak-ex-live-selection');
  selectionBar.append(selectionText, h('span', 'kit-spacer'), extendSelected, finishSelected, reopenSelected, clearSelected);
  selectionBar.setAttribute('role', 'region');
  selectionBar.setAttribute('aria-label', 'Seçili öğrencilerle işlem');

  const table = dataTable({
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(170px, 1.6fr)', cell: (row) => studentCell(row.student) },
      { key: 'state', label: 'Durum', width: 'minmax(120px, 1fr)', cell: (row) => stateCell(row) },
      { key: 'answered', label: 'Cevaplanan', width: '104px', align: 'num',
        cell: (row) => `${num(row.answeredCount)} / ${num(row.questionTotal)}` },
      { key: 'remaining', label: 'Kalan', width: '88px', align: 'num', cell: (row) => remainingCell(row) },
      { key: 'seen', label: 'Son görülme', width: '108px', cell: (row) => seenCell(row) },
      { key: 'exits', label: 'Çıkış', width: '64px', align: 'num', title: 'Uygulamadan çıkış (arka plana geçiş) sayısı',
        cell: (row) => exitsCell(row) },
      { key: 'extra', label: 'Ek süre', width: '72px', align: 'num',
        cell: (row) => (row.extraMinutes ? `+${num(row.extraMinutes)} dk` : '—') },
      { key: 'booklet', label: 'Kitapçık', width: '76px', cell: (row) => row.bookletType || '—' },
    ],
    rows: [],
    selectable: true,
    dense: true,
    rowKey: (row) => String(row.student?.id || ''),
    onSelect: () => paintSelection(),
    empty: empty('Bu süzgece uyan öğrenci yok', 'Süzgeci temizleyin ya da başka bir durum seçin.'),
  });

  const body = h('div', 'ak-stack');
  body.append(toolbar(stateChips.node, classSelect.node, search.node, h('span', 'kit-spacer'), stamp, refresh),
    selectionBar, table.node);
  const content = h('div', 'ak-stack');
  content.append(head, kpiSlot, banner, body);
  const errorSlot = h('div');
  node.append(errorSlot, content);
  content.hidden = true;
  errorSlot.append(h('div', 'ak-muted', 'Canlı sınav yükleniyor…'));

  // ------------------------------------------------------------- yükleme

  async function load() {
    try {
      const data = await app.api.get(base);
      take(data);
    } catch (error) {
      failure = error;
      if (!live) {
        content.hidden = true;
        errorSlot.replaceChildren(errorBox(error, () => poll.now()));
      } else {
        stamp.textContent = `Güncellenemedi (${problem(error, 'bağlantı yok')}); son veri ${fmtTime(new Date(loadedAt).toISOString())}`;
        stamp.classList.add('bad');
      }
    }
  }

  function take(data) {
    if (!data) return;
    live = data;
    failure = null;
    loadedAt = Date.now();
    offsetMs = clockOffset(data.serverNow, loadedAt);
    errorSlot.replaceChildren();
    content.hidden = false;
    stamp.classList.remove('bad');
    paintHead();
    paint();
  }

  // ------------------------------------------------------------- çizim

  function paintHead() {
    const settings = live.app || {};
    const paused = settings.state === 'paused';
    const actions = [];
    if (live.canManage) {
      actions.push(paused
        ? actionButton(app, 'admin_resumeExam', 'Sürdür', { variant: 'primary', onClick: () => resume() })
        : actionButton(app, 'admin_pauseExam', 'Duraklat', { onClick: () => pause() }));
      actions.push(menuButton('Pencere', [
        settings.state === 'scheduled' && app.can('admin_setExamWindow')
          ? { label: 'Şimdi aç', onClick: () => windowChange({ openNow: true }) } : null,
        ['open', 'paused'].includes(settings.state) && app.can('admin_setExamWindow')
          ? { label: 'Şimdi kapat…', onClick: () => windowChange({ closeNow: true }) } : null,
        app.can('admin_setExamWindow') ? { label: 'Açılış / kapanışı değiştir…', onClick: () => windowDialog() } : null,
      ].filter(Boolean)));
    }
    if (app.can('admin_extendExamTime')) {
      actions.push(menuButton('Herkese ek süre', [
        ...EXTRA_PRESETS.map((minutes) => ({ label: `+${minutes} dk`, onClick: () => extend(minutes, false) })),
        { label: 'Serbest süre…', onClick: () => extend(null, false) },
      ], { variant: paused ? '' : 'primary' }));
    }
    const title = h('div', 'ak-ex-live-head');
    const text = h('div', 'ak-stack ak-tight');
    const line = h('div', 'ak-inline');
    line.append(h('b', '', 'Canlı sınav'), statusBadge(APP_STATE, settings.state));
    text.append(line, h('span', 'ak-muted', [
      `Pencere ${fmtDateTime(settings.opensAt)} – ${fmtDateTime(settings.closesAt)}`,
      `süre ${num(settings.durationMinutes)} dk`,
      settings.extraMinutesAll ? `herkese +${num(settings.extraMinutesAll)} dk` : '',
    ].filter(Boolean).join(' · ')));
    const tools = h('div', 'ak-tools');
    tools.append(...actions);
    title.append(text, h('span', 'kit-spacer'), tools);
    head.replaceChildren(title);

    const counters = live.counters || {};
    kpiSlot.replaceChildren(kpiRow([
      { label: 'Hedef öğrenci', value: num(counters.totalCount) },
      { label: 'Başlamadı', value: num(counters.notStarted) },
      { label: 'Çözüyor', value: num(counters.inProgress), tone: counters.inProgress ? 'info' : '' },
      { label: 'Bitirdi', value: num(counters.submitted), tone: counters.submitted ? 'good' : '' },
      { label: 'Süresi doldu', value: num(counters.expired), tone: counters.expired ? 'warn' : '' },
    ]));
    stateChips.counts({ not_started: counters.notStarted ?? 0, in_progress: counters.inProgress ?? 0,
      submitted: counters.submitted ?? 0, expired: counters.expired ?? 0 });

    const notes = [];
    if (paused) {
      notes.push(alertBox(`Sınav duraklatıldı (${fmtTime(settings.pausedAt)}). Süre durdu, öğrencilerin formu kilitli; `
        + '“Sürdür” deyince geçen süre herkesin bitişine ve pencere kapanışına eklenir.', 'warn'));
    } else if (settings.state === 'scheduled') {
      notes.push(alertBox(`Pencere ${fmtDateTime(settings.opensAt)}'te açılır; öğrenciler o ana dek başlayamaz.`, 'info'));
    } else if (['closed', 'finalized'].includes(settings.state)) {
      notes.push(alertBox(settings.state === 'finalized'
        ? 'Sınav kapandı ve sonuçlar hesaplandı. Teknik sorun yaşayan öğrenci için oturum yeniden açılabilir (sonuç '
          + 'yayımlanmadıysa).'
        : 'Pencere kapandı; açık kalan oturumlar son kaydedilen cevaplarla kapanır.', 'info'));
    }
    if (!live.canManage) {
      notes.push(alertBox('Gözetmen görünümü: ders verdiğiniz sınıflardaki öğrencileri görürsünüz; ek süre verip '
        + 'oturum bitirebilirsiniz. Duraklatma, yeniden açma ve pencere yöneticidedir.', 'info'));
    }
    banner.replaceChildren(...notes);

    const classes = liveClasses(live.items);
    classSelect.options([{ value: '', label: 'Tüm sınıflar' }, ...classes.map((item) => ({ value: item.id, label: item.name }))]);
    classSelect.node.hidden = classes.length < 2;
  }

  function paint() {
    if (!live) return;
    ticking = [];
    table.update({ rows: filterLive(live.items, filters) });
    paintSelection();
    stamp.textContent = `Son güncelleme ${fmtTime(new Date(loadedAt).toISOString())} · 10 sn'de bir yenilenir`;
  }

  function paintSelection() {
    const rows = table.selectedRows();
    const info = selectionInfo(rows);
    selectionBar.hidden = !info.count;
    selectionText.textContent = info.count ? `${num(info.count)} öğrenci seçildi` : '';
    const canExtend = app.can('admin_extendExamTime') && info.canExtend;
    extendSelected.querySelector('button').disabled = !canExtend;
    extendSelected.hidden = !app.can('admin_extendExamTime');
    finishSelected.disabled = !info.canFinish;
    finishSelected.hidden = !app.can('admin_finishExamAttempts');
    reopenSelected.hidden = !live?.canManage || !app.can('admin_reopenExamAttempt');
    reopenSelected.disabled = !info.canReopen;
    reopenSelected.title = info.canReopen ? '' : 'Bitirmiş ya da süresi dolmuş tek bir öğrenci seçin.';
  }

  function stateCell(row) {
    const wrap = h('div', 'ak-cell-stack');
    wrap.append(statusBadge(LIVE_STATE, row.state));
    const extra = [];
    if (row.submitReason && row.state !== 'in_progress') extra.push(SUBMIT_REASON[row.submitReason] || row.submitReason);
    if (row.lateSync) extra.push('geç eşitlendi');
    if (row.reopenCount) extra.push(`${num(row.reopenCount)} kez yeniden açıldı`);
    if (row.state === 'in_progress' && row.sessionKey && (live.app?.sessions || []).length > 1) {
      extra.push(live.app.sessions.find((item) => item.key === row.sessionKey)?.title || row.sessionKey);
    }
    if (extra.length) wrap.append(h('small', '', extra.join(' · ')));
    return wrap;
  }

  function remainingCell(row) {
    const span = h('span', 'ak-ex-clock');
    if (row.state !== 'in_progress') { span.textContent = '—'; return span; }
    ticking.push({ span, row });
    paintClock(span, row);
    return span;
  }

  function paintClock(span, row) {
    const seconds = remainingSeconds(row, { offsetMs, paused: live?.app?.state === 'paused' });
    span.textContent = clockText(seconds);
    span.classList.toggle('warn', seconds !== null && seconds <= 300);
  }

  function seenCell(row) {
    const quiet = isQuiet(row, { offsetMs });
    const span = h('span', quiet ? 'ak-ex-quiet' : '', seenText(row.lastSeenAt, { offsetMs }));
    if (quiet) span.title = 'İki dakikadır cihazdan ses yok (bağlantı, pil ya da uygulama kapandı). Cevaplar cihazda saklanır.';
    return span;
  }

  function exitsCell(row) {
    if (!row.appExitCount) return '0';
    const tag = badge(num(row.appExitCount), row.appExitCount >= 3 ? 'warn' : 'dim');
    tag.title = 'Sınav sırasında uygulamadan çıkış sayısı';
    return tag;
  }

  // ------------------------------------------------------------- işlemler

  const nameOf = (id) => live?.items?.find((row) => row.student?.id === id)?.student?.displayName || 'öğrenci';

  async function act(path, body, verb) {
    if (busy) return null;
    busy = true;
    try {
      const result = await app.api.post(`${base}/${path}`, body);
      if (result?.live) take(result.live);
      else await load();
      app.toast(actionToast(verb, result, nameOf), result?.skipped?.length ? 'warn' : 'good');
      onChanged?.();
      return result;
    } catch (error) {
      app.toast(problem(error), 'bad');
      return null;
    } finally {
      busy = false;
    }
  }

  async function extend(preset, selectedOnly) {
    const ids = selectedOnly ? table.selection() : [];
    if (selectedOnly && !ids.length) { app.toast('Önce öğrenci seçin.', 'warn'); return; }
    const who = selectedOnly ? `${num(ids.length)} öğrenciye` : 'herkese';
    const canWindow = Boolean(live?.canManage);
    const draft = await formDialog(app, {
      title: preset ? `${who[0].toUpperCase()}${who.slice(1)} +${preset} dk ek süre` : `${who[0].toUpperCase()}${who.slice(1)} ek süre`,
      description: selectedOnly
        ? 'Çözmekte olan (ya da henüz başlamamış) seçili öğrencilerin süresi uzar. Bitirmiş öğrenciye ek süre yazılmaz; '
          + 'gerekirse “Yeniden aç” kullanın. Öğrenciye “Sınavına ek süre verildi” bildirimi gider.'
        : 'Bütün öğrencilerin süresi uzar (sonradan başlayanlar da alır). Öğrencilere uygulama içi bildirim gider.',
      value: { minutes: preset || 10, extendWindow: canWindow, reason: '' },
      fields: [
        preset ? null : { key: 'minutes', label: 'Ek süre (dakika)', type: 'number', required: true, min: 1, max: 240,
          validate: (raw) => (Number.isInteger(Number(raw)) && Number(raw) >= 1 && Number(raw) <= 240 ? null : '1–240 dakika.') },
        canWindow ? { key: 'extendWindow', label: 'Pencere kapanışını da aynı kadar uzat', type: 'checkbox', wide: true,
          hint: 'İşaretsizse pencere kapanışını aşan kısım kırpılır.' } : null,
        { key: 'reason', label: 'Not (isteğe bağlı)', maxLength: 300, wide: true, placeholder: 'Ör. elektrik kesintisi' },
      ],
      submitLabel: 'Ek süreyi ver',
      onSubmit: (value) => {
        const made = extendBody({ minutes: preset || value.minutes, studentIds: ids,
          extendWindow: canWindow && value.extendWindow, reason: value.reason });
        if (made.error) throw new Error(made.error);
        return made.body;
      },
    });
    if (!draft || draft === true) return;
    const result = await act('extend', draft, `+${draft.minutes} dk ek süre verildi`);
    if (result) table.clearSelection();
    paintSelection();
  }

  async function finish() {
    const rows = table.selectedRows().filter((row) => row.state === 'in_progress');
    if (!rows.length) { app.toast('Çözmekte olan öğrenci seçin.', 'warn'); return; }
    const ok = await confirmAction(app, {
      title: `${num(rows.length)} öğrencinin oturumunu bitir`,
      description: 'Oturum son kaydedilen cevaplarla biter (gözetmen bitirdi). Öğrencinin cihazında gönderilmemiş, '
        + 'ama bitirmeden önce verilmiş cevaplar geç eşitlemeyle yine sayılır.',
      confirmLabel: 'Oturumu bitir',
      danger: true,
    });
    if (!ok) return;
    const result = await act('finish', { studentIds: rows.map((row) => row.student.id) }, 'Oturum bitirildi');
    if (result) table.clearSelection();
    paintSelection();
  }

  async function reopen() {
    const [row] = table.selectedRows();
    if (!row || !['submitted', 'expired'].includes(row.state)) return;
    const draft = await formDialog(app, {
      title: `${row.student?.displayName || 'Öğrencinin'} oturumunu yeniden aç`,
      description: 'Teknik sorun (uygulama çöktü, cihaz kapandı …) yaşayan öğrenci kaldığı yerden devam eder; cevapları '
        + 'korunur. Gerekçe denetim kaydına yazılır. Pencere kapalıysa yalnız bu öğrenci için açılır.',
      value: { minutes: 15, reason: '' },
      fields: [
        { key: 'minutes', label: 'Kalan süre (dakika)', type: 'number', required: true, min: 1, max: 240,
          validate: (raw) => (Number.isInteger(Number(raw)) && Number(raw) >= 1 && Number(raw) <= 240 ? null : '1–240 dakika.') },
        { key: 'reason', label: 'Gerekçe', required: true, maxLength: 300, wide: true,
          placeholder: 'Ör. tablet kapandı, 20. dakikada', validate: (raw) => (String(raw || '').trim().length >= 3 ? null
            : 'Gerekçe en az 3 karakter.') },
      ],
      submitLabel: 'Yeniden aç',
      onSubmit: (value) => ({ studentId: row.student.id, minutes: Number(value.minutes),
        reason: String(value.reason).trim(), sessionKey: row.sessionKey || null }),
    });
    if (!draft || draft === true) return;
    const result = await act('reopen', draft, 'Oturum yeniden açıldı');
    if (result) table.clearSelection();
    paintSelection();
  }

  async function pause() {
    const ok = await confirmAction(app, {
      title: 'Sınavı duraklat',
      description: 'Bütün açık oturumların süresi durur, öğrencilerin formu kilitlenir ve yeni başlatma olmaz '
        + '(elektrik kesintisi, alarm …). “Sürdür” deyince geçen süre herkese geri verilir.',
      confirmLabel: 'Duraklat',
      danger: true,
    });
    if (ok) await act('pause', {}, 'Sınav duraklatıldı');
  }

  async function resume() {
    const ok = await confirmAction(app, {
      title: 'Sınavı sürdür',
      description: 'Formlar açılır; duraklamada geçen süre bütün açık oturumların bitişine ve pencere kapanışına eklenir.',
      confirmLabel: 'Sürdür',
    });
    if (ok) await act('resume', {}, 'Sınav sürdürüldü');
  }

  async function windowChange(body) {
    if (body.closeNow) {
      const ok = await confirmAction(app, {
        title: 'Pencereyi şimdi kapat',
        description: `Yeni başlatma olmaz; çözmekte olan ${num(live?.counters?.inProgress)} öğrencinin oturumu `
          + `${num(Math.round((live?.app?.graceSeconds ?? 120) / 60))} dk tolerans sonunda son cevaplarla kapanır.`,
        confirmLabel: 'Pencereyi kapat',
        danger: true,
      });
      if (!ok) return;
    }
    await act('window', body, body.openNow ? 'Pencere açıldı' : 'Pencere kapatıldı');
  }

  async function windowDialog() {
    const settings = live?.app || {};
    const opens = splitStamp(settings.opensAt);
    const closes = splitStamp(settings.closesAt);
    const draft = await formDialog(app, {
      title: 'Açılış / kapanışı değiştir',
      description: 'Kapanışı uzatmak süren oturumları da uzatır (süre sınırına dek); kısaltmak bitişleri kırpar.',
      value: { opensDate: opens.date, opensClock: opens.clock, closesDate: closes.date, closesClock: closes.clock, reason: '' },
      fields: [
        settings.state === 'scheduled' ? { key: 'opensDate', label: 'Açılış tarihi', type: 'date', required: true } : null,
        settings.state === 'scheduled' ? { key: 'opensClock', label: 'Açılış saati (SS:DD)', required: true, maxLength: 5 } : null,
        { key: 'closesDate', label: 'Kapanış tarihi', type: 'date', required: true },
        { key: 'closesClock', label: 'Kapanış saati (SS:DD)', required: true, maxLength: 5 },
        { key: 'reason', label: 'Not (isteğe bağlı)', maxLength: 300, wide: true },
      ],
      submitLabel: 'Kaydet',
      onSubmit: (value) => {
        const body = {};
        const closesAt = joinStamp(value.closesDate, value.closesClock);
        if (!closesAt) throw new Error('Kapanış tarihini ve saatini (SS:DD) yazın.');
        body.closesAt = closesAt;
        if (settings.state === 'scheduled') {
          const opensAt = joinStamp(value.opensDate, value.opensClock);
          if (!opensAt) throw new Error('Açılış tarihini ve saatini (SS:DD) yazın.');
          if (Date.parse(opensAt) >= Date.parse(closesAt)) throw new Error('Kapanış açılıştan sonra olmalı.');
          body.opensAt = opensAt;
        }
        if (String(value.reason || '').trim()) body.reason = String(value.reason).trim();
        return body;
      },
    });
    if (!draft || draft === true) return;
    await act('window', draft, 'Pencere güncellendi');
  }

  // ------------------------------------------------------------- döngü

  const poll = pollLoop({ every: LIVE_POLL_MS, run: () => (node.isConnected && !node.closest('[hidden]') ? load() : null) });
  const clock = globalThis.setInterval(() => {
    if (!live || failure || node.closest('[hidden]')) return;
    for (const { span, row } of ticking) paintClock(span, row);
  }, 1000);

  return {
    node,
    reload: () => load(),
    destroy() {
      poll.stop();
      globalThis.clearInterval(clock);
      search.destroy();
    },
  };
}
