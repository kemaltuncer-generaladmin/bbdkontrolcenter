// BBD Akademi — Hoca soruları (F-K14, Akademi README §14 madde 63).
//
// Kullanıcı (14.10.2026): "Hoca uzaktan soru çözümünü ses kaydı veya yazarak
// atabilsin, görsel de ekleyebilsin." Öğrencinin hocaya sorduğu sorular (koçu
// yoksa dersin öğretmenine / kurumun öğretmen havuzuna gider) burada listelenir;
// yanıt mobil öğretmen ekranıyla AYNI uçla yazılır (A13): metin + görsel + ses
// dosyası (m4a) ya da tarayıcıdan kayıt. KM bir öğretmen ADINA yazar
// (`staffId`); Akademi denetim kaydında KM kullanıcısı kalır.
//
// Saf mantık `questions-logic.js`'tedir (Node ile sınanır).

import { h, loadStyles } from '../../ui-kit/kit.js';
import { badge, chipRow } from '../../ui-kit/layout.js';
import { AkademiError, newKey, problem } from './api.js';
import {
  actionButton, confirmAction, fmtDateTime, formDialog, multiChips, openDrawer, pagedList, personName,
  runAction, sectionHead, selectBox, statusBadge, toolbar,
} from './common.js';
import {
  FILTERS, ROUTES, SLA, STATUS, answerBody, answerSummary, checkPart, clock, contextLabel,
  defaultResponder, filterQuery, MAX_ATTACHMENTS, MAX_VOICE_SECONDS, partKind, recorderMime,
  settingsPutBody, slaLevel, uploadFields,
} from './questions-logic.js';

const QUESTIONS = '/v1/admin/coaching/questions';

const studentCell = (row) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', row.student?.displayName || '—'), h('small', '', row.student?.classGroup?.name || ''));
  return cell;
};

function slaBadge(row) {
  const level = slaLevel(row);
  if (level === 'done') return statusBadge(STATUS, row.status);
  const [text, tone] = SLA[level];
  return badge(text, tone);
}

export function section(app) {
  loadStyles(import.meta.url, 'questions.css');
  const node = h('div', 'ak-stack');
  let filter = 'waiting';
  let teacherId = '';
  const settingsButton = actionButton(app, 'admin_putCoachingSettings', 'Soru ayarları',
    { variant: 'ghost', onClick: () => settingsDialog(app) });
  node.append(sectionHead('Hoca soruları', {
    subtitle: 'Öğrencilerin hocalarına sorduğu sorular. Yanıtı yazarak, görselle ya da sesli notla buradan da '
      + 'verebilirsiniz; öğrenciye "Sorun yanıtlandı" bildirimi gider.',
    actions: [settingsButton],
  }));
  const chips = chipRow(FILTERS.map((item) => ({ key: item.key, label: item.label })), filter, (key) => {
    filter = key || 'waiting';
    if (!key) chips.set('waiting');
    list.reload();
  });
  const teacherSlot = h('div');
  node.append(toolbar(chips.node, teacherSlot));
  app.lookups.options('teachers', { empty: 'Bütün öğretmenler' }).then((options) => {
    teacherSlot.append(selectBox(options, '', (value) => { teacherId = value; list.reload(); },
      { label: 'Öğretmen' }).node);
  }).catch(() => {});

  const list = pagedList({
    app,
    path: '/v1/admin/coaching/questions',
    query: () => filterQuery(filter, teacherId),
    onRow: (row) => openQuestion(app, row, (next) => list.replace((item) => item.id === next.id, next)),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.2fr)', cell: studentCell },
      { key: 'question', label: 'Soru', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', contextLabel(row.context) || 'Soru'),
          h('small', '', [row.text, row.images?.length ? `${row.images.length} görsel` : ''].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'teacher', label: 'Hoca', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', personName(row.forwardedTo || row.coach)), h('small', '', ROUTES[row.route] || ''));
        return cell;
      } },
      { key: 'asked', label: 'Soruldu', width: '150px', cell: (row) => fmtDateTime(row.askedAt) },
      { key: 'state', label: 'Durum', width: '130px', cell: slaBadge },
      { key: 'answer', label: 'Son yanıt', width: 'minmax(0, 1.4fr)', cell: (row) => answerSummary(row) || '—' },
    ],
    empty: { title: 'Bu süzgeçte soru yok', text: 'Öğrenciler uygulamadan "Hocaya sor" ile sorduğunda burada görünür.' },
  });
  node.append(list.node);

  let started = false;
  return {
    node,
    shown() {
      if (!started) { started = true; list.reload(); }
    },
    destroy() { list.destroy(); },
  };
}

// ---------------------------------------------------------------- ayrıntı

/** İmzalı Akademi dosyası → nesne URL'si (KM vekilinden; önbellekli). Hata defteri gelen kutusu da kullanır. */
export function fileUrlCache(app) {
  const cache = new Map();
  const urls = [];
  return {
    get(file) {
      if (!file?.url) return Promise.resolve(null);
      if (!cache.has(file.id)) {
        cache.set(file.id, app.api.fetchFile(file.url, 'akademi-dosya').then(({ blob }) => {
          const url = URL.createObjectURL(blob);
          urls.push(url);
          return url;
        }).catch(() => null));
      }
      return cache.get(file.id);
    },
    dispose() { urls.forEach((url) => URL.revokeObjectURL(url)); },
  };
}

export function imageStrip(files, cache) {
  const strip = h('div', 'ak-q-images');
  for (const file of files || []) {
    const link = h('a', 'ak-q-thumb');
    link.href = '#';
    link.setAttribute('aria-label', 'Görseli büyük aç');
    const img = h('img');
    img.alt = 'Görsel';
    link.append(img);
    cache.get(file).then((url) => {
      if (!url) { link.replaceChildren(h('span', 'ak-muted', 'Görsel açılamadı')); return; }
      img.src = url;
      link.addEventListener('click', (event) => { event.preventDefault(); window.open(url, '_blank'); });
    });
    strip.append(link);
  }
  return strip;
}

export function voicePlayer(voice, cache) {
  const wrap = h('div', 'ak-q-voice');
  const audio = h('audio');
  audio.controls = true;
  audio.preload = 'none';
  const speed = selectBox([{ value: '1', label: '1x' }, { value: '1.5', label: '1.5x' }, { value: '2', label: '2x' }], '1',
    (value) => { audio.playbackRate = Number(value) || 1; }, { label: 'Hız' });
  wrap.append(h('span', 'ak-muted', `Sesli not · ${clock(voice.durationSeconds)}`), audio, speed.node);
  cache.get(voice.file).then((url) => {
    if (url) audio.src = url;
    else wrap.append(h('span', 'ak-muted', 'Ses açılamadı'));
  });
  return wrap;
}

function answerCard(answer, cache) {
  const card = h('div', 'ak-q-answer');
  const head = h('div', 'ak-q-answer-head');
  head.append(h('strong', '', personName(answer.author)), h('span', 'ak-muted', fmtDateTime(answer.createdAt)));
  if (!answer.delivered) head.append(badge('Sessiz saat: sabah iletilecek', 'info'));
  card.append(head);
  if (answer.text) card.append(h('p', 'ak-q-text', answer.text));
  for (const voice of answer.voice || []) card.append(voicePlayer(voice, cache));
  if (answer.images?.length) card.append(imageStrip(answer.images, cache));
  if (answer.files?.length) card.append(h('small', 'ak-muted', `${answer.files.length} PDF eki`));
  return card;
}

async function openQuestion(app, row, onChange) {
  const cache = fileUrlCache(app);
  const view = openDrawer(app, {
    title: row.student?.displayName || 'Soru',
    subtitle: contextLabel(row.context),
    onClose: () => cache.dispose(),
  });
  let question = row;
  const render = () => {
    const body = [];
    const ask = h('div', 'ak-q-ask');
    ask.append(h('div', 'ak-q-meta', `${fmtDateTime(question.askedAt)} · ${ROUTES[question.route] || ''} · `
      + `${personName(question.forwardedTo || question.coach)}`), slaBadge(question));
    if (question.text) ask.append(h('p', 'ak-q-text', question.text));
    if (question.images?.length) ask.append(imageStrip(question.images, cache));
    if (question.studentFeedback === 'not_understood') {
      ask.append(badge(`Hâlâ anlamadım${question.feedbackNote ? `: ${question.feedbackNote}` : ''}`, 'warn'));
    } else if (question.studentFeedback === 'understood') {
      ask.append(badge('Öğrenci anladı', 'good'));
    }
    body.push(ask);
    body.push(h('h4', 'ak-q-h', question.answers?.length ? 'Yanıtlar' : 'Henüz yanıt yok'));
    for (const answer of question.answers || []) body.push(answerCard(answer, cache));
    if (question.status !== 'closed') body.push(composer(app, question, (next) => { set(next); }));
    const tools = h('div', 'ak-row-actions');
    if (question.status !== 'closed') {
      tools.append(
        actionButton(app, 'admin_forwardCoachQuestion', 'Başka öğretmene yönlendir',
          { variant: 'ghost', onClick: () => forward() }),
        actionButton(app, 'admin_closeCoachQuestion', 'Soruyu kapat', { variant: 'ghost', onClick: () => close() }),
      );
    }
    body.push(tools);
    view.body.replaceChildren(...body);
  };
  const set = (next) => {
    if (!next) return;
    question = next;
    onChange(next);
    render();
  };

  async function forward() {
    const teachers = await app.lookups.options('teachers').catch(() => []);
    const next = await formDialog(app, {
      title: 'Soruyu yönlendir',
      description: 'Seçilen öğretmen yalnız bu soruya erişir ve yanıtlar; ona bildirim gider.',
      value: { teacherId: '', note: '' },
      fields: [
        { key: 'teacherId', label: 'Öğretmen', type: 'select', options: teachers, required: true },
        { key: 'note', label: 'Not', maxLength: 500, wide: true },
      ],
      submitLabel: 'Yönlendir',
      onSubmit: (draft, { key }) => app.api.post(`${QUESTIONS}/${question.id}/forward`,
        { teacherId: draft.teacherId, note: draft.note || null }, { key }),
    });
    if (next && typeof next === 'object') { app.toast('Soru yönlendirildi.', 'good'); set(next); }
  }

  async function close() {
    const ok = await confirmAction(app, { title: 'Soruyu kapat', description: 'Öğrenci bu soruya yeni yanıt alamaz.',
      confirmLabel: 'Kapat' });
    if (!ok) return;
    set(await runAction(app, () => app.api.post(`${QUESTIONS}/${question.id}/close`, {}), { success: 'Soru kapatıldı.' }));
  }

  render();
  // Liste satırı yanıtları da taşır; imzalı bağlantılar taze olsun diye ayrıntı yeniden okunur.
  try {
    set(await app.api.get(`/v1/admin/coaching/questions/${row.id}`));
  } catch (failure) {
    view.body.prepend(h('div', 'ak-muted', problem(failure, 'Soru yeniden okunamadı.')));
  }
}

// ---------------------------------------------------------------- yanıt yazıcısı

function composer(app, question, onAnswered) {
  const node = h('div', 'ak-q-composer');
  node.append(h('h4', 'ak-q-h', 'Yanıt yaz'));
  if (!app.api.can('admin_answerCoachQuestion')) {
    node.append(h('div', 'ak-muted', 'Yanıt yazma yetkiniz yok.'));
    return node;
  }
  let responder = defaultResponder(question);
  let clientUuid = newKey('yanit');
  const parts = [];   // {file, kind, durationSeconds, fileId, url}
  // Yanıtlayan seçimi baştan yerinde (liste gelince dolar — düzen kaymaz).
  const responderSlot = h('label', 'kit-field ak-q-responder');
  const responderBox = selectBox([{ value: responder || '', label: 'Öğretmenler yükleniyor…' }], responder,
    (value) => { responder = value; clientUuid = newKey('yanit'); }, { label: 'Yanıtlayan öğretmen' });
  responderSlot.append(h('span', 'kit-field-label', 'Yanıtlayan öğretmen'), responderBox.node);
  app.lookups.options('teachers').then((options) => {
    responderBox.options(options.length ? options : [{ value: responder || '', label: 'Öğretmen listesi boş' }]);
    responderBox.set(responder);
  }).catch(() => responderBox.options([{ value: responder || '', label: 'Öğretmen listesi alınamadı' }]));
  const text = h('textarea', 'kit-input ak-q-input');
  text.rows = 4;
  text.maxLength = 2000;
  text.placeholder = 'Çözümü yazın…';
  text.setAttribute('aria-label', 'Yanıt metni');
  text.addEventListener('input', () => { clientUuid = newKey('yanit'); });
  // Ctrl/Cmd+Enter: gönder (koç klavyeden çıkmadan yanıtlar).
  text.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !send.disabled) { event.preventDefault(); submit(false); }
  });
  const partList = h('div', 'ak-q-parts');
  const error = h('div', 'ak-q-error');

  const picker = h('input');
  picker.type = 'file';
  picker.multiple = true;
  picker.accept = '.jpg,.jpeg,.png,.webp,.pdf,.m4a,audio/mp4,image/*';
  picker.hidden = true;
  picker.addEventListener('change', () => { for (const file of picker.files || []) addPart(file); picker.value = ''; });
  const attach = h('button', 'kit-btn ghost', 'Görsel / ses / PDF ekle');
  attach.type = 'button';
  attach.addEventListener('click', () => picker.click());
  if (!app.api.can('admin_uploadCoachQuestionFile')) {
    attach.disabled = true;
    attach.title = 'Ek yükleme yetkiniz yok.';
  }

  const mime = recorderMime(globalThis.MediaRecorder?.isTypeSupported?.bind(globalThis.MediaRecorder));
  const recordButton = h('button', 'kit-btn ghost', 'Tarayıcıdan sesli not kaydet');
  recordButton.type = 'button';
  const recordState = h('span', 'ak-muted');
  if (!mime || !navigator.mediaDevices?.getUserMedia || attach.disabled) {
    recordButton.disabled = true;
    recordButton.title = 'Bu tarayıcı m4a kaydedemiyor; telefonda kaydedip .m4a dosyasını ekleyin.';
  }
  let recorder = null;
  let stopTimer = null;
  let started = 0;
  recordButton.addEventListener('click', () => (recorder ? stopRecording() : startRecording()));

  async function startRecording() {
    error.textContent = '';
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      error.textContent = 'Mikrofon izni verilmedi.';
      return;
    }
    const chunks = [];
    recorder = new MediaRecorder(stream, { mimeType: mime, audioBitsPerSecond: 64000 });
    recorder.addEventListener('dataavailable', (event) => { if (event.data?.size) chunks.push(event.data); });
    recorder.addEventListener('stop', () => {
      stream.getTracks().forEach((track) => track.stop());
      const seconds = Math.min(MAX_VOICE_SECONDS, Math.round((Date.now() - started) / 1000));
      const blob = new Blob(chunks, { type: 'audio/mp4' });
      if (seconds >= 1 && blob.size) {
        addPart(new File([blob], `sesli-not-${Date.now()}.m4a`, { type: 'audio/mp4' }), seconds);
      }
      recorder = null;
      clearInterval(stopTimer);
      recordButton.textContent = 'Tarayıcıdan sesli not kaydet';
      recordState.textContent = '';
    });
    started = Date.now();
    recorder.start();
    recordButton.textContent = 'Kaydı bitir';
    stopTimer = setInterval(() => {
      const elapsed = Math.round((Date.now() - started) / 1000);
      recordState.textContent = `Kaydediliyor · ${clock(elapsed)} / ${clock(MAX_VOICE_SECONDS)}`;
      if (elapsed >= MAX_VOICE_SECONDS) stopRecording();
    }, 250);
  }

  function stopRecording() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }

  function addPart(file, durationSeconds = null) {
    const kind = partKind(file);
    const message = checkPart(file, { durationSeconds });
    if (message) { error.textContent = message; return; }
    if (parts.length >= MAX_ATTACHMENTS) { error.textContent = `En çok ${MAX_ATTACHMENTS} ek.`; return; }
    error.textContent = '';
    parts.push({ file, kind, durationSeconds, fileId: null, url: URL.createObjectURL(file) });
    clientUuid = newKey('yanit');
    paintParts();
    if (kind === 'voice' && !durationSeconds) {
      // Dosyanın süresi tarayıcıda okunur (sunucu da m4a'nın kendisinden ölçer).
      const probe = new Audio();
      probe.preload = 'metadata';
      probe.src = parts[parts.length - 1].url;
      const part = parts[parts.length - 1];
      probe.addEventListener('loadedmetadata', () => {
        if (Number.isFinite(probe.duration)) {
          part.durationSeconds = Math.max(1, Math.round(probe.duration));
          if (part.durationSeconds > MAX_VOICE_SECONDS) {
            error.textContent = 'Sesli not en çok 2 dakika olabilir.';
            removePart(part);
          } else {
            paintParts();
          }
        }
      });
    }
  }

  function removePart(part) {
    const index = parts.indexOf(part);
    if (index >= 0) parts.splice(index, 1);
    URL.revokeObjectURL(part.url);
    clientUuid = newKey('yanit');
    paintParts();
  }

  function paintParts() {
    partList.replaceChildren();
    for (const part of parts) {
      const item = h('div', 'ak-q-part');
      if (part.kind === 'image') {
        const img = h('img');
        img.src = part.url;
        img.alt = 'Eklenen görsel';
        item.append(img);
      } else if (part.kind === 'voice') {
        const audio = h('audio');
        audio.controls = true;
        audio.src = part.url;
        item.append(audio, h('small', 'ak-muted', part.durationSeconds ? clock(part.durationSeconds) : ''));
      } else {
        item.append(h('span', '', part.file.name));
      }
      const remove = h('button', 'kit-btn ghost', 'Kaldır');
      remove.type = 'button';
      remove.addEventListener('click', () => removePart(part));
      item.append(remove);
      partList.append(item);
    }
  }

  const send = h('button', 'kit-btn primary', 'Yanıtı gönder');
  send.type = 'button';
  send.addEventListener('click', () => submit(false));

  async function submit(acknowledge) {
    error.textContent = '';
    if (!String(text.value).trim() && !parts.length) {
      error.textContent = 'Metin, görsel ya da sesli not ekleyin.';
      return;
    }
    send.disabled = true;
    send.textContent = 'Gönderiliyor…';
    try {
      for (const part of parts) {
        if (part.fileId) continue;
        const result = await app.api.call('POST', `${QUESTIONS}/${question.id}/files`, {
          upload: { file: part.file, field: 'file',
            fields: uploadFields(part.kind, { durationSeconds: part.durationSeconds, staffId: responder }) },
        });
        part.fileId = result.data?.id;
      }
      const body = answerBody({ clientUuid, text: text.value, fileIds: parts.map((part) => part.fileId),
        staffId: responder, acknowledge });
      const next = await app.api.post(`${QUESTIONS}/${question.id}/answer`, body, { key: clientUuid });
      app.toast('Yanıt gönderildi; öğrenciye bildirim gitti.', 'good');
      parts.forEach((part) => URL.revokeObjectURL(part.url));
      parts.length = 0;
      text.value = '';
      clientUuid = newKey('yanit');
      onAnswered(next);
    } catch (failure) {
      if (failure instanceof AkademiError && failure.code === 'contact_info_warning' && !acknowledge) {
        const go = await confirmAction(app, { title: 'İletişim bilgisi',
          description: 'Yanıtta telefon ya da sosyal medya bilgisi var gibi görünüyor. Yine de gönderilsin mi?',
          confirmLabel: 'Yine de gönder' });
        if (go) { send.disabled = false; send.textContent = 'Yanıtı gönder'; await submit(true); return; }
      }
      error.textContent = problem(failure, 'Yanıt gönderilemedi.');
    } finally {
      send.disabled = false;
      send.textContent = 'Yanıtı gönder';
    }
  }

  const row = h('div', 'ak-row-actions');
  row.append(attach, recordButton, recordState, h('span', 'kit-spacer'), send);
  node.append(responderSlot, text, partList, picker, row, error,
    h('small', 'ak-muted', 'Ctrl+Enter: gönder · Görseller EXIF silinerek küçültülür; sesli not m4a (AAC), en çok 2 dakika.'));
  return node;
}

// ---------------------------------------------------------------- ayarlar

async function settingsDialog(app) {
  let current;
  try {
    current = await app.api.get('/v1/admin/coaching/settings');
  } catch (failure) {
    app.toast(problem(failure, 'Ayarlar okunamadı.'), 'bad');
    return;
  }
  const teachers = await app.lookups.options('teachers').catch(() => []);
  const saved = await formDialog(app, {
    title: 'Soru ayarları',
    description: 'Koçu olmayan ve dersin öğretmeni bulunamayan öğrencinin sorusu bu öğretmenlerden en az '
      + 'sorusu olana gider. Hiç seçilmezse bütün öğretmenler havuzdadır.',
    value: { pool: current.questionPoolStaffIds || [], notifyGuardian: Boolean(current.questionAnswerNotifyGuardian) },
    fields: [
      { key: 'pool', label: 'Öğretmen havuzu', type: 'custom', wide: true,
        render: (draft, set) => multiChips(teachers, draft.pool, (list) => set(list)).node },
      { key: 'notifyGuardian', label: 'Soru yanıtlanınca veliye de bildirim gönder', type: 'checkbox' },
    ],
    onSubmit: (draft, { key }) => app.api.put('/v1/admin/coaching/settings',
      settingsPutBody(current, { questionPoolStaffIds: draft.pool || [], questionAnswerNotifyGuardian: Boolean(draft.notifyGuardian) }),
      { key }),
  });
  if (saved) {
    app.api.invalidate?.('/v1/admin/coaching/settings');
    app.toast('Soru ayarları kaydedildi.', 'good');
  }
}
