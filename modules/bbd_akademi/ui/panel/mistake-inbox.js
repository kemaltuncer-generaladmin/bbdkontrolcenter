// BBD Akademi — Hata defteri gelen kutusu (Akademi README §14 madde 104).
//
// Kullanıcı (16.10.2026): "öğretmen hata defterine çok karışık bi yoldan erişiyor gelen kutusuna fln da düşsün göz
// önünde olsun. ayrıca öğretmen çözüm için görsel ses kaydı vs iletebilsin." Öğrencilerin yeni hata defteri kayıtları
// sorumlu öğretmene düşer (koç → dersin öğretmeni → havuz); kurum burada hepsini görür (öğretmen, öğrenci, ders, durum
// süzgeci) ve mobil öğretmen ekranıyla AYNI uçla (A13) bir öğretmen ADINA çözüm yazar: metin + görsel + sesli not
// (dosya ya da tarayıcı kaydı) + PDF + video bağlantısı. Akademi denetim kaydında KM kullanıcısı kalır.
//
// Saf mantık `mistake-inbox-logic.js`'tedir (Node ile sınanır); dosya / ses parçaları `questions.js` ile ortak.

import { h, loadStyles } from '../../ui-kit/kit.js';
import { badge, chipRow } from '../../ui-kit/layout.js';
import { newKey, problem } from './api.js';
import {
  actionButton, confirmAction, fmtDateTime, openDrawer, pagedList, personName, runAction, sectionHead, selectBox,
  statusBadge, toolbar,
} from './common.js';
import {
  answerBody, answerSummary, defaultResponder, FILTERS, filterQuery, MAX_ATTACHMENTS, MAX_VOICE, mistakeLabel,
  ROUTES, STATUS, uploadFields, validVideoUrl,
} from './mistake-inbox-logic.js';
import { fileUrlCache, imageStrip, voicePlayer } from './questions.js';
import { checkPart, clock, MAX_VOICE_SECONDS, partKind, recorderMime } from './questions-logic.js';

const INBOX = '/v1/admin/mistake-inbox';

const studentCell = (row) => {
  const cell = h('div', 'ak-cell-stack');
  cell.append(h('span', '', row.student?.displayName || '—'), h('small', '', row.student?.classGroup?.name || ''));
  return cell;
};

export function section(app) {
  loadStyles(import.meta.url, 'questions.css');
  const node = h('div', 'ak-stack');
  let filter = 'waiting';
  let staffId = '';
  let subjectId = '';
  node.append(sectionHead('Hata defteri gelen kutusu', {
    subtitle: 'Öğrencilerin hata defterine eklediği yeni kayıtlar sorumlu öğretmene düşer. Çözümü yazarak, görselle, '
      + 'sesli notla, PDF ya da video bağlantısıyla buradan da verebilirsiniz; öğrenciye "Hocan çözüm gönderdi" '
      + 'bildirimi gider.',
  }));
  const chips = chipRow(FILTERS.map((item) => ({ key: item.key, label: item.label })), filter, (key) => {
    filter = key || 'waiting';
    if (!key) chips.set('waiting');
    list.reload();
  });
  const teacherSlot = h('div');
  const subjectSlot = h('div');
  node.append(toolbar(chips.node, teacherSlot, subjectSlot));
  app.lookups.options('teachers', { empty: 'Bütün öğretmenler' }).then((options) => {
    teacherSlot.append(selectBox(options, '', (value) => { staffId = value; list.reload(); },
      { label: 'Öğretmen' }).node);
  }).catch(() => {});
  app.lookups.options('subjects', { empty: 'Bütün dersler' }).then((options) => {
    subjectSlot.append(selectBox(options, '', (value) => { subjectId = value; list.reload(); },
      { label: 'Ders' }).node);
  }).catch(() => {});

  const list = pagedList({
    app,
    path: INBOX,
    query: () => filterQuery(filter, { staffId, subjectId }),
    rowKey: (row) => row.mistake?.id,
    onRow: (row) => openItem(app, row, (next) => list.replace((item) => item.mistake?.id === next.mistake?.id, next)),
    columns: [
      { key: 'student', label: 'Öğrenci', width: 'minmax(0, 1.2fr)', cell: studentCell },
      { key: 'mistake', label: 'Kayıt', width: 'minmax(0, 2fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', mistakeLabel(row.mistake) || 'Hata kaydı'),
          h('small', '', [row.mistake?.images?.length ? `${row.mistake.images.length} görsel` : '',
            row.questionId ? 'Hocaya sordu' : ''].filter(Boolean).join(' · ')));
        return cell;
      } },
      { key: 'teacher', label: 'Öğretmen', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.assignedTo ? personName(row.assignedTo) : '—'), h('small', '', ROUTES[row.route] || ''));
        return cell;
      } },
      { key: 'received', label: 'Geldi', width: '150px', cell: (row) => fmtDateTime(row.receivedAt || row.mistake?.createdAt) },
      { key: 'state', label: 'Durum', width: '140px', cell: (row) => statusBadge(STATUS, row.status || 'waiting') },
      { key: 'answer', label: 'Son çözüm', width: 'minmax(0, 1.4fr)', cell: (row) => answerSummary(row) || '—' },
    ],
    empty: { title: 'Bu süzgeçte kayıt yok', text: 'Öğrenciler hata defterine soru ekledikçe burada görünür.' },
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

function answerCard(answer, cache) {
  const card = h('div', 'ak-q-answer');
  const head = h('div', 'ak-q-answer-head');
  head.append(h('strong', '', personName(answer.author)), h('span', 'ak-muted', fmtDateTime(answer.createdAt)));
  card.append(head);
  if (answer.text) card.append(h('p', 'ak-q-text', answer.text));
  for (const voice of answer.voice || []) card.append(voicePlayer(voice, cache));
  if (answer.images?.length) card.append(imageStrip(answer.images, cache));
  for (const file of answer.files || []) {
    const link = h('a', '', 'PDF eki');
    link.href = '#';
    link.addEventListener('click', (event) => {
      event.preventDefault();
      cache.get(file).then((url) => { if (url) window.open(url, '_blank'); });
    });
    card.append(link);
  }
  if (answer.videoUrl) {
    const link = h('a', '', 'Video bağlantısı');
    link.href = answer.videoUrl;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    card.append(link);
  }
  if (answer.attachmentsExpired) card.append(h('small', 'ak-muted', 'Ekler saklama süresi doldu · metin duruyor'));
  return card;
}

async function openItem(app, row, onChange) {
  const cache = fileUrlCache(app);
  const view = openDrawer(app, {
    title: row.student?.displayName || 'Hata kaydı',
    subtitle: mistakeLabel(row.mistake),
    onClose: () => cache.dispose(),
  });
  let item = row;
  const render = () => {
    const m = item.mistake || {};
    const body = [];
    const head = h('div', 'ak-q-ask');
    head.append(h('div', 'ak-q-meta', `${fmtDateTime(item.receivedAt || m.createdAt)} · ${ROUTES[item.route] || ''}`
      + `${item.assignedTo ? ` · ${personName(item.assignedTo)}` : ''}`), statusBadge(STATUS, item.status || 'waiting'));
    if (m.images?.length) head.append(imageStrip(m.images.map((image) => image.file), cache));
    const facts = [m.studentAnswer ? `Öğrencinin cevabı: ${m.studentAnswer}` : '',
      m.correctAnswer ? `Doğru cevap: ${m.correctAnswer}` : ''].filter(Boolean).join(' · ');
    if (facts) head.append(h('p', 'ak-q-text', facts));
    if (m.solutionNote) head.append(h('p', 'ak-q-text', `Öğrencinin notu: ${m.solutionNote}`));
    if (item.questionId) head.append(badge('Öğrenci bu kayıttan "Hocaya sor" dedi (Hoca soruları)', 'warn'));
    body.push(head);
    const answers = m.teacherAnswers || [];
    body.push(h('h4', 'ak-q-h', answers.length ? 'Çözümler' : 'Henüz çözüm yok'));
    for (const answer of answers) body.push(answerCard(answer, cache));
    body.push(composer(app, item, (next) => set(next)));
    const tools = h('div', 'ak-row-actions');
    if (item.status && !['answered', 'closed'].includes(item.status)) {
      tools.append(actionButton(app, 'admin_closeMistakeInboxItem', 'Yanıt gerekmiyor',
        { variant: 'ghost', onClick: () => close() }));
    }
    body.push(tools);
    view.body.replaceChildren(...body);
  };
  const set = (next) => {
    if (!next) return;
    item = next;
    onChange(next);
    render();
  };

  async function close() {
    const ok = await confirmAction(app, { title: 'Yanıt gerekmiyor',
      description: 'Bu kayıt bekleyenlerden çıkar. Sonra yine çözüm yazılabilir.', confirmLabel: 'Çıkar' });
    if (!ok) return;
    set(await runAction(app, () => app.api.post(`${INBOX}/${item.mistake.id}/close`, {}),
      { success: 'Bekleyenlerden çıkarıldı.' }));
  }

  render();
  // İmzalı bağlantılar taze olsun diye ayrıntı yeniden okunur (KM bakması okundu yapmaz).
  try {
    set(await app.api.get(`/v1/admin/mistake-inbox/${row.mistake.id}`));
  } catch (failure) {
    view.body.prepend(h('div', 'ak-muted', problem(failure, 'Kayıt yeniden okunamadı.')));
  }
}

// ---------------------------------------------------------------- çözüm yazıcısı

function composer(app, item, onAnswered) {
  const node = h('div', 'ak-q-composer');
  node.append(h('h4', 'ak-q-h', 'Çözüm yaz'));
  if (!app.api.can('admin_answerMistake')) {
    node.append(h('div', 'ak-muted', 'Çözüm yazma yetkiniz yok.'));
    return node;
  }
  let responder = defaultResponder(item);
  let clientUuid = newKey('cozum');
  const parts = [];   // {file, kind, durationSeconds, fileId, url}
  const responderSlot = h('label', 'kit-field ak-q-responder');
  const responderBox = selectBox([{ value: responder || '', label: 'Öğretmenler yükleniyor…' }], responder,
    (value) => { responder = value; clientUuid = newKey('cozum'); }, { label: 'Çözümü yazan öğretmen' });
  responderSlot.append(h('span', 'kit-field-label', 'Çözümü yazan öğretmen'), responderBox.node);
  app.lookups.options('teachers').then((options) => {
    responderBox.options(options.length ? options : [{ value: responder || '', label: 'Öğretmen listesi boş' }]);
    responderBox.set(responder);
  }).catch(() => responderBox.options([{ value: responder || '', label: 'Öğretmen listesi alınamadı' }]));
  const text = h('textarea', 'kit-input ak-q-input');
  text.rows = 4;
  text.maxLength = 2000;
  text.placeholder = 'Çözümü yazın…';
  text.setAttribute('aria-label', 'Çözüm metni');
  text.addEventListener('input', () => { clientUuid = newKey('cozum'); });
  text.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !send.disabled) { event.preventDefault(); submit(); }
  });
  const video = h('input', 'kit-input');
  video.type = 'url';
  video.maxLength = 2048;
  video.placeholder = 'Video bağlantısı (isteğe bağlı, https://…)';
  video.setAttribute('aria-label', 'Video bağlantısı');
  video.addEventListener('input', () => { clientUuid = newKey('cozum'); });
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
  if (!app.api.can('admin_uploadMistakeAnswerFile')) {
    attach.disabled = true;
    attach.title = 'Ek yükleme yetkiniz yok.';
  }

  const mime = recorderMime(globalThis.MediaRecorder?.isTypeSupported?.bind(globalThis.MediaRecorder));
  const recordButton = h('button', 'kit-btn ghost', 'Tarayıcıdan sesli not kaydet');
  recordButton.type = 'button';
  const recordState = h('span', 'ak-muted');
  if (!mime || !navigator.mediaDevices?.getUserMedia || attach.disabled) {
    recordButton.disabled = true;
    // Firefox m4a kaydedemez (Akademi kuralı): dosya yükleme yolu açık kalır.
    recordButton.title = 'Bu tarayıcıda sesli not kaydı yok; Chrome, Safari ya da Edge kullanın ya da .m4a dosyası ekleyin.';
  }
  let recorder = null;
  let stopTimer = null;
  let started = 0;
  recordButton.addEventListener('click', () => (recorder ? stopRecording() : startRecording()));

  async function startRecording() {
    error.textContent = '';
    if (parts.filter((part) => part.kind === 'voice').length >= MAX_VOICE) {
      error.textContent = `En çok ${MAX_VOICE} sesli not.`;
      return;
    }
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
      if (seconds >= 1 && blob.size) addPart(new File([blob], `sesli-not-${Date.now()}.m4a`, { type: 'audio/mp4' }), seconds);
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
    if (kind === 'voice' && parts.filter((part) => part.kind === 'voice').length >= MAX_VOICE) {
      error.textContent = `En çok ${MAX_VOICE} sesli not.`;
      return;
    }
    error.textContent = '';
    const part = { file, kind, durationSeconds, fileId: null, url: URL.createObjectURL(file) };
    parts.push(part);
    clientUuid = newKey('cozum');
    paintParts();
    if (kind === 'voice' && !durationSeconds) {
      const probe = new Audio();
      probe.preload = 'metadata';
      probe.src = part.url;
      probe.addEventListener('loadedmetadata', () => {
        if (!Number.isFinite(probe.duration)) return;
        part.durationSeconds = Math.max(1, Math.round(probe.duration));
        if (part.durationSeconds > MAX_VOICE_SECONDS) {
          error.textContent = 'Sesli not en çok 2 dakika olabilir.';
          removePart(part);
        } else {
          paintParts();
        }
      });
    }
  }

  function removePart(part) {
    const index = parts.indexOf(part);
    if (index >= 0) parts.splice(index, 1);
    URL.revokeObjectURL(part.url);
    clientUuid = newKey('cozum');
    paintParts();
  }

  function paintParts() {
    partList.replaceChildren();
    for (const part of parts) {
      const row = h('div', 'ak-q-part');
      if (part.kind === 'image') {
        const img = h('img');
        img.src = part.url;
        img.alt = 'Eklenen görsel';
        row.append(img);
      } else if (part.kind === 'voice') {
        const audio = h('audio');
        audio.controls = true;
        audio.src = part.url;
        row.append(audio, h('small', 'ak-muted', part.durationSeconds ? clock(part.durationSeconds) : ''));
      } else {
        row.append(h('span', '', part.file.name));
      }
      const remove = h('button', 'kit-btn ghost', 'Kaldır');
      remove.type = 'button';
      remove.addEventListener('click', () => removePart(part));
      row.append(remove);
      partList.append(row);
    }
  }

  const send = h('button', 'kit-btn primary', 'Çözümü gönder');
  send.type = 'button';
  send.addEventListener('click', () => submit());

  async function submit() {
    error.textContent = '';
    const link = String(video.value || '').trim();
    if (link && !validVideoUrl(link)) { error.textContent = 'Geçerli bir bağlantı girin (http ya da https).'; return; }
    if (!String(text.value).trim() && !parts.length && !link) {
      error.textContent = 'Metin, görsel, sesli not, PDF ya da video bağlantısı ekleyin.';
      return;
    }
    send.disabled = true;
    send.textContent = 'Gönderiliyor…';
    try {
      for (const part of parts) {
        if (part.fileId) continue;
        const result = await app.api.call('POST', `${INBOX}/${item.mistake.id}/files`, {
          upload: { file: part.file, field: 'file',
            fields: uploadFields(part.kind, { durationSeconds: part.durationSeconds, staffId: responder }) },
        });
        part.fileId = result.data?.id;
      }
      const body = answerBody({ clientUuid, text: text.value, fileIds: parts.map((part) => part.fileId),
        videoUrl: link, staffId: responder });
      const next = await app.api.post(`${INBOX}/${item.mistake.id}/answers`, body, { key: clientUuid });
      app.toast('Çözüm gönderildi; öğrenciye bildirim gitti.', 'good');
      parts.forEach((part) => URL.revokeObjectURL(part.url));
      parts.length = 0;
      text.value = '';
      video.value = '';
      clientUuid = newKey('cozum');
      onAnswered(next);
    } catch (failure) {
      error.textContent = problem(failure, 'Çözüm gönderilemedi.');
    } finally {
      send.disabled = false;
      send.textContent = 'Çözümü gönder';
    }
  }

  const row = h('div', 'ak-row-actions');
  row.append(attach, recordButton, recordState, h('span', 'kit-spacer'), send);
  node.append(responderSlot, text, video, partList, picker, row, error,
    h('small', 'ak-muted', 'Ctrl+Enter: gönder · Görseller EXIF silinerek küçültülür; sesli not m4a (AAC), en çok 2 '
      + 'dakika; PDF en çok 10 MB; en çok 6 ek.'));
  return node;
}
