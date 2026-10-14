// BBD Akademi — Duyurular (F-A13, F-00.6).
//
// Oluştur → hedefle (tümü / veliler / öğrenciler × kurum / sınıf / çalışma
// grubu / öğrenci) → şimdi ya da zamanlı yayımla → okunma oranı. Taslak
// düzenlenebilir; yayımlanmış duyuru da düzeltilebilir (Akademi kuralı).
// Metin Markdown'dır ve olduğu gibi gönderilir; panel HTML üretmez.
//
// "SMS olarak da gönder" (bbd-akademi README §14 madde 81): yalnız velilere, varsayılan
// kapalı; karakter sayacı, "yalnız uygulamayı kullanmayan velilere", alıcı ve SMS adedi
// önizlemesi (`admin_previewAnnouncementSms`). Metin SMS'te ASCII'ye indirilir.

import { button, h, loadStyles, percent } from '../../ui-kit/kit.js';
import { badge, card, chipRow, kpiRow } from '../../ui-kit/layout.js';
import {
  actionButton, confirmAction, fmtDateTime, formDialog, kv, loader, multiChips, num, openDrawer,
  pagedList, runAction, sectionHead, statusBadge, studentPicker, todayIst,
} from './common.js';

const STATUS = { draft: ['Taslak', 'dim'], scheduled: ['Zamanlandı', 'info'],
  published: ['Yayında', 'good'], archived: ['Arşiv', 'dim'] };
const RECIPIENTS = { all: 'Veliler ve öğrenciler', guardians: 'Yalnız veliler', students: 'Yalnız öğrenciler' };
const SCOPES = { tenant: 'Bütün kurum', class_groups: 'Sınıflar', student_groups: 'Çalışma grupları',
  students: 'Seçili öğrenciler' };

// ------------------------------------------------------------------ SMS (README §14 madde 81)
// Akademi `announcements/sms.py` ile aynı kural: Türkçe harf → Latin, ASCII dışı atılır, boşluk
// tekleşir; GSM-7 birimi (genişletme karakterleri 2), tek SMS 160, çok parçalı 153; en çok 459.
export const SMS_MAX = 459;
const SMS_FOLD = { 'ç': 'c', 'Ç': 'C', 'ğ': 'g', 'Ğ': 'G', 'ı': 'i', 'İ': 'I', 'ö': 'o', 'Ö': 'O', 'ş': 's',
  'Ş': 'S', 'ü': 'u', 'Ü': 'U', 'â': 'a', 'Â': 'A', 'î': 'i', 'Î': 'I', 'û': 'u', 'Û': 'U', '‘': "'", '’': "'",
  '`': "'", '“': '"', '”': '"', '–': '-', '—': '-', '…': '...', '•': '-', '€': 'EUR', '₺': 'TL' };
const SMS_EXT = new Set(['^', '{', '}', '\\', '[', '~', ']', '|']);

export function smsFold(text) {
  const mapped = [...String(text || '')].map((ch) => SMS_FOLD[ch] ?? ch).join('');
  const kept = [...mapped].filter((ch) => /\s/.test(ch) || (ch >= ' ' && ch <= '~')).join('');
  return kept.split(/\s+/).filter(Boolean).join(' ');
}

export function smsUnits(folded) {
  let units = 0;
  for (const ch of folded) units += SMS_EXT.has(ch) ? 2 : 1;
  return units;
}

export function smsSegments(folded) {
  const units = smsUnits(folded);
  if (!units) return 0;
  return units <= 160 ? 1 : Math.ceil(units / 153);
}

/** Duyurudan SMS metni: "Başlık: düz metin" (Markdown işaretleri atılır), en çok 459 birim. */
export function smsFromAnnouncement(title, bodyMarkdown) {
  const plain = String(bodyMarkdown || '').replace(/[*_`#>\[\]()~|]+/g, ' ');
  let text = smsFold(`${String(title || '').trim()}: ${plain}`);
  while (smsUnits(text) > SMS_MAX) text = text.slice(0, -1);
  return text.trim();
}

function smsDeliveryText(delivery) {
  if (!delivery) return '—';
  const parts = [`${num(delivery.sent)} gönderildi`];
  if (delivery.queued) parts.push(`${num(delivery.queued)} bekliyor`);
  if (delivery.failed) parts.push(`${num(delivery.failed)} başarısız`);
  if (delivery.skipped) parts.push(`${num(delivery.skipped)} atlandı`);
  return `${parts.join(' · ')} (${num(delivery.recipients)} veli)`;
}

export function section(app) {
  loadStyles(import.meta.url, 'announcements.css');
  const node = h('div', 'ak-stack');
  let status = '';
  const add = actionButton(app, 'admin_createAnnouncement', 'Yeni duyuru', { variant: 'primary',
    onClick: () => edit(app, null, () => list.reload()) });
  node.append(sectionHead('Duyurular', { subtitle: 'Velilere ve öğrencilere uygulama içi duyuru.', actions: [add] }));
  const chips = chipRow([
    { key: 'draft', label: 'Taslak' }, { key: 'scheduled', label: 'Zamanlanan' },
    { key: 'published', label: 'Yayında' }, { key: 'archived', label: 'Arşiv' },
  ], null, (key) => { status = key || ''; list.reload(); });
  node.append(chips.node);

  const list = pagedList({
    app,
    path: '/v1/admin/announcements',
    query: () => ({ status: status || undefined }),
    columns: [
      { key: 'title', label: 'Başlık', width: 'minmax(0, 2.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        const title = h('span', '', row.title);
        if (row.pinned) title.append(' ', badge('Sabit', 'info'));
        cell.append(title, h('small', '', audienceText(row.audience)));
        return cell;
      } },
      { key: 'status', label: 'Durum', cell: (row) => statusBadge(STATUS, row.status) },
      { key: 'when', label: 'Yayın', width: '140px', cell: (row) => fmtDateTime(row.publishedAt || row.scheduledAt) },
      { key: 'sms', label: 'SMS', width: '70px', cell: (row) => (row.sms?.enabled ? badge('SMS', 'info') : '') },
      { key: 'read', label: 'Okunma', align: 'num', cell: (row) => (row.readRatePercent === null
        || row.readRatePercent === undefined ? '—' : percent(row.readRatePercent, 0)) },
      { key: 'by', label: 'Yazan', cell: (row) => row.createdBy || '—' },
    ],
    onRow: (row) => open(row.id),
    empty: { title: 'Henüz duyuru yok', text: 'Velilere ve öğrencilere uygulama içi duyuru gönderin.',
      actions: [actionButton(app, 'admin_createAnnouncement', 'Duyuru yaz', { variant: 'primary',
        onClick: () => add.click() })] },
  });
  node.append(list.node);

  function open(id) {
    detail(app, id, { onChange: (next) => (next ? list.replace((row) => row.id === next.id, next) : list.reload()) });
  }

  let started = false;
  return {
    node,
    shown(payload) {
      if (!started) { started = true; list.reload(); }
      if (payload?.announcementId) open(payload.announcementId);
    },
    destroy() { list.destroy(); },
  };
}

function audienceText(audience) {
  if (!audience) return '';
  const count = (audience.classGroupIds?.length || 0) + (audience.studentGroupIds?.length || 0)
    + (audience.studentIds?.length || 0);
  return `${RECIPIENTS[audience.recipients] || ''} · ${SCOPES[audience.scope] || ''}${count ? ` (${num(count)})` : ''}`;
}

/** Oluştur / düzenle. Hedefleme: kapsam seçimine göre sınıf / grup / öğrenci. */
async function edit(app, current, onDone) {
  const [classes, groups] = await Promise.all([
    app.lookups.classGroups().catch(() => []),
    app.lookups.studentGroups().catch(() => []),
  ]);
  const chosenStudents = new Map((current?.audience?.studentIds || []).map((id) => [id, id]));
  const value = current ? {
    title: current.title, bodyMarkdown: current.bodyMarkdown, pinned: current.pinned,
    recipients: current.audience?.recipients || 'all', scope: current.audience?.scope || 'tenant',
    classGroupIds: current.audience?.classGroupIds || [], studentGroupIds: current.audience?.studentGroupIds || [],
    expiresDay: current.expiresAt ? current.expiresAt.slice(0, 10) : '',
    sms: { enabled: Boolean(current.sms?.enabled), text: current.sms?.text || '',
      nonAppOnly: Boolean(current.sms?.nonAppOnly) },
  } : { recipients: 'all', scope: 'tenant', pinned: false, classGroupIds: [], studentGroupIds: [],
    sms: { enabled: false, text: '', nonAppOnly: false } };

  const audienceOf = (draft) => ({
    recipients: draft.recipients || 'all',
    scope: draft.scope || 'tenant',
    classGroupIds: draft.scope === 'class_groups' ? draft.classGroupIds || [] : [],
    studentGroupIds: draft.scope === 'student_groups' ? draft.studentGroupIds || [] : [],
    studentIds: draft.scope === 'students' ? [...chosenStudents.keys()] : [],
  });

  // "SMS olarak da gönder": anahtar, metin + sayaç, yalnız uygulamasızlar, önizleme.
  const smsPanel = (draft, set) => {
    const sms = { ...(draft.sms || { enabled: false, text: '', nonAppOnly: false }) };
    const push = () => { draft.sms = { ...sms }; set(draft.sms); };
    const wrap = h('div', 'ak-stack ak-ann-sms');
    const toggle = h('label', 'ak-inline');
    const box = h('input');
    box.type = 'checkbox';
    box.checked = sms.enabled;
    toggle.append(box, h('span', '', 'SMS olarak da gönder'));
    const hint = h('span', 'ak-muted', 'Yalnız velilere gider; SMS ücretlidir.');
    const body = h('div', 'ak-stack');
    const area = h('textarea', 'kit-input ak-ann-sms-text');
    area.rows = 3;
    area.maxLength = 1000;
    area.setAttribute('aria-label', 'SMS metni');
    area.value = sms.text;
    const counter = h('span', 'ak-ann-sms-count');
    const fill = button('Duyurudan doldur', { variant: 'ghost' });
    const note = h('span', 'ak-muted', 'Türkçe harfler SMS\'te Latin harfe çevrilir (ş → s, ı → i).');
    const onlyLabel = h('label', 'ak-inline');
    const only = h('input');
    only.type = 'checkbox';
    only.checked = sms.nonAppOnly;
    onlyLabel.append(only, h('span', '', 'Yalnız uygulamayı kullanmayan velilere'));
    const preview = h('div', 'ak-ann-sms-preview');
    const refresh = actionButton(app, 'admin_previewAnnouncementSms', 'Alıcıları yenile', { variant: 'ghost',
      onClick: () => load() });
    const counterRow = h('div', 'ak-inline');
    counterRow.append(counter, fill);
    body.append(h('span', 'kit-field-label', 'SMS metni'), area, counterRow, note, onlyLabel, preview, refresh);
    wrap.append(toggle, hint, body);

    let timer = null;
    let seq = 0;
    const paintCounter = () => {
      const folded = smsFold(area.value);
      const units = smsUnits(folded);
      counter.textContent = `${num(units)}/${num(SMS_MAX)} karakter · ${num(smsSegments(folded))} SMS`;
      counter.classList.toggle('over', units > SMS_MAX);
    };
    const load = async () => {
      if (!sms.enabled) return;
      if ((draft.recipients || 'all') === 'students') {
        preview.replaceChildren(h('span', 'ak-muted', 'SMS yalnız velilere gider; alıcılara veliler dahil olmalı.'));
        return;
      }
      const mine = ++seq;
      preview.replaceChildren(h('span', 'ak-muted', 'Alıcılar hesaplanıyor…'));
      try {
        const out = await app.api.post('/v1/admin/announcements/sms-preview',
          { audience: audienceOf(draft), text: area.value, nonAppOnly: sms.nonAppOnly });
        if (mine !== seq) return;
        const lines = [
          h('strong', '', `${num(out.recipients)} veliye SMS gidecek · toplam ${num(out.totalSegments)} SMS`),
          h('span', 'ak-muted', `Hedefteki veli: ${num(out.guardians)} · uygulamayı kullanan: ${num(out.appUsers)}`),
        ];
        if (out.smsRemaining !== null && out.smsRemaining !== undefined) {
          lines.push(h('span', 'ak-muted', `Netgsm paketinde kalan: ${num(out.smsRemaining)} SMS`));
        }
        if (out.deferredUntil) {
          const at = new Date(out.deferredUntil).toLocaleTimeString('tr-TR',
            { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Istanbul' });
          lines.push(h('span', 'ak-muted', `Sessiz saat: SMS'ler ${at}'de gönderilir.`));
        }
        preview.replaceChildren(...lines);
      } catch {
        if (mine === seq) preview.replaceChildren(h('span', 'ak-muted', 'Alıcı sayısı alınamadı.'));
      }
    };
    const later = () => { clearTimeout(timer); timer = setTimeout(load, 400); };
    const paint = () => { body.hidden = !sms.enabled; paintCounter(); };
    box.addEventListener('change', () => { sms.enabled = box.checked; push(); paint(); load(); });
    area.addEventListener('input', () => { sms.text = area.value; push(); paintCounter(); later(); });
    only.addEventListener('change', () => { sms.nonAppOnly = only.checked; push(); load(); });
    fill.addEventListener('click', () => {
      area.value = smsFromAnnouncement(draft.title, draft.bodyMarkdown);
      sms.text = area.value;
      push();
      paintCounter();
      later();
    });
    push();
    paint();
    load();
    return { node: wrap, update: () => later() };
  };

  // Hedef seçici: "Kapsam" değişince (formDialog `update`) yeniden çizilir.
  const targetPicker = (draft, set) => {
    const wrap = h('div', 'ak-stack');
    let shownScope = null;
    const paint = () => {
      const scope = draft.scope || 'tenant';
      if (scope === shownScope) return;
      shownScope = scope;
      wrap.replaceChildren();
      if (scope === 'class_groups') {
        const chips = multiChips(classes.map((item) => ({ value: item.id, label: item.name })),
          draft.classGroupIds, (list) => { draft.classGroupIds = list; set(list); });
        wrap.append(classes.length ? chips.node : h('span', 'ak-muted', 'Sınıf bulunamadı.'));
      } else if (scope === 'student_groups') {
        const chips = multiChips(groups.map((item) => ({ value: item.id, label: item.name })),
          draft.studentGroupIds, (list) => { draft.studentGroupIds = list; set(list); });
        wrap.append(groups.length ? chips.node : h('span', 'ak-muted', 'Çalışma grubu yok.'));
      } else if (scope === 'students') {
        const picked = h('div', 'ak-chips');
        const paintPicked = () => {
          picked.replaceChildren();
          for (const [id, name] of chosenStudents) {
            const chip = h('button', 'kit-chip on', `${name} ✕`);
            chip.type = 'button';
            chip.addEventListener('click', () => {
              chosenStudents.delete(id);
              paintPicked();
              set([...chosenStudents.keys()]);
            });
            picked.append(chip);
          }
          if (!chosenStudents.size) picked.append(h('span', 'ak-muted', 'Öğrenci seçilmedi.'));
        };
        const picker = studentPicker(app, { onPick: (student) => {
          chosenStudents.set(student.id, student.displayName);
          paintPicked();
          set([...chosenStudents.keys()]);
        } });
        paintPicked();
        wrap.append(picker.node, picked);
      } else {
        wrap.append(h('span', 'ak-muted', 'Bütün kuruma gider.'));
      }
    };
    paint();
    return { node: wrap, update: paint };
  };

  const saved = await formDialog(app, {
    title: current ? 'Duyuruyu düzenle' : 'Yeni duyuru',
    wide: true,
    value,
    fields: [
      { key: 'title', label: 'Başlık', required: true, maxLength: 120, wide: true },
      { key: 'bodyMarkdown', label: 'Metin', type: 'textarea', required: true, maxLength: 5000, wide: true,
        hint: 'Markdown: **kalın**, *eğik*, - madde. En çok 5.000 karakter.' },
      { key: 'recipients', label: 'Kime', type: 'select', options: Object.entries(RECIPIENTS)
        .map(([key, label]) => ({ value: key, label })) },
      { key: 'scope', label: 'Kapsam', type: 'select', options: Object.entries(SCOPES)
        .map(([key, label]) => ({ value: key, label })) },
      { key: 'targets', label: 'Hedef', type: 'custom', render: targetPicker, wide: true,
        validate: (_raw, draft) => {
          if (draft.scope === 'class_groups' && !(draft.classGroupIds || []).length) return 'En az bir sınıf seçin.';
          if (draft.scope === 'student_groups' && !(draft.studentGroupIds || []).length) return 'En az bir grup seçin.';
          if (draft.scope === 'students' && !chosenStudents.size) return 'En az bir öğrenci seçin.';
          return null;
        } },
      { key: 'pinned', label: 'Sabitle (listenin üstünde dursun)', type: 'checkbox' },
      { key: 'expiresDay', label: 'Bitiş günü (isteğe bağlı)', type: 'date' },
      { key: 'sms', label: 'SMS', type: 'custom', render: smsPanel, wide: true,
        validate: (raw, draft) => {
          if (!raw?.enabled) return null;
          if ((draft.recipients || 'all') === 'students') return 'SMS yalnız velilere gider; alıcılara veliler dahil olmalı.';
          const folded = smsFold(raw.text);
          if (!folded) return 'SMS metni boş olamaz.';
          if (smsUnits(folded) > SMS_MAX) return `SMS metni en çok ${SMS_MAX} karakter (3 SMS) olabilir.`;
          return null;
        } },
    ],
    submitLabel: current ? 'Kaydet' : 'Taslak olarak kaydet',
    onSubmit: (draft, { key }) => {
      const body = {
        title: String(draft.title || '').trim(),
        bodyMarkdown: String(draft.bodyMarkdown || ''),
        audience: audienceOf(draft),
        pinned: Boolean(draft.pinned),
        expiresAt: draft.expiresDay ? `${draft.expiresDay}T23:59:00+03:00` : null,
        sms: { enabled: Boolean(draft.sms?.enabled), text: draft.sms?.text || null,
          nonAppOnly: Boolean(draft.sms?.nonAppOnly) },
      };
      return current ? app.api.put(`/v1/admin/announcements/${current.id}`, body)
        : app.api.post('/v1/admin/announcements', body, { key });
    },
  });
  if (saved) {
    app.toast(current ? 'Duyuru güncellendi.' : 'Taslak kaydedildi. Yayımlamak için açın.', 'good');
    onDone?.(saved);
  }
}

function detail(app, id, { onChange } = {}) {
  const panel = openDrawer(app, { title: 'Duyuru' });
  const view = loader(() => Promise.all([
    app.api.get(`/v1/admin/announcements/${id}`),
    app.api.get(`/v1/admin/announcements/${id}/stats`).catch(() => null),
  ]), ([item, stats]) => {
    panel.setTitle(item.title);
    const nodes = [];
    const tools = h('div', 'ak-inline');
    if (item.status === 'draft' || item.status === 'scheduled') {
      tools.append(actionButton(app, 'admin_publishAnnouncement', item.status === 'draft' ? 'Yayımla' : 'Zamanı değiştir',
        { variant: 'primary', onClick: () => publish(item) }));
    }
    tools.append(actionButton(app, 'admin_updateAnnouncement', 'Düzenle', {
      onClick: () => edit(app, item, (next) => { onChange?.(next); view.reload(); }) }));
    tools.append(actionButton(app, 'admin_deleteAnnouncement', 'Sil', { variant: 'ghost', onClick: async () => {
      const yes = await confirmAction(app, { title: `“${item.title}” silinsin mi?`,
        description: item.status === 'published' ? 'Duyuru uygulamadan kalkar.' : '', confirmLabel: 'Sil', danger: true });
      if (!yes) return;
      const done = await runAction(app, () => app.api.del(`/v1/admin/announcements/${id}`), { success: 'Duyuru silindi.' });
      if (done !== undefined) { panel.close(); onChange?.(null); }
    } }));
    nodes.push(tools);
    nodes.push(card('', kv([
      ['Durum', statusBadge(STATUS, item.status)],
      ['Hedef', audienceText(item.audience)],
      ['Sabit', item.pinned ? 'Evet' : 'Hayır'],
      ['Zamanlama', fmtDateTime(item.scheduledAt)],
      ['Yayın', fmtDateTime(item.publishedAt)],
      ['Bitiş', fmtDateTime(item.expiresAt)],
      ['Yazan', `${item.createdBy || '—'} · ${fmtDateTime(item.createdAt)}`],
    ])));
    const body = h('pre', 'ak-ann-body', item.bodyMarkdown || '');
    nodes.push(card('Metin', body));
    if (item.sms?.enabled) {
      const delivery = stats?.sms || item.sms.delivery;
      nodes.push(card('SMS', kv([
        ['Metin', h('pre', 'ak-ann-body', smsFold(item.sms.text))],
        ['Alıcı', item.sms.nonAppOnly ? 'Yalnız uygulamayı kullanmayan veliler' : 'Hedefteki bütün veliler'],
        ['Veli başına', `${num(item.sms.segmentsPerMessage)} SMS`],
        ['Gönderim', delivery ? smsDeliveryText(delivery) : 'Yayımlanınca gönderilir.'],
      ])));
    }
    if (stats) {
      nodes.push(card('Okunma', kpiRow([
        { label: 'Alıcı', value: num(stats.recipients) },
        { label: 'Okuyan', value: num(stats.read) },
        { label: 'Oran', value: percent(stats.readRatePercent, 0) },
        { label: 'Veliler', value: stats.byRecipient?.guardians
          ? `${num(stats.byRecipient.guardians.read)}/${num(stats.byRecipient.guardians.recipients)}` : '—' },
        { label: 'Öğrenciler', value: stats.byRecipient?.students
          ? `${num(stats.byRecipient.students.read)}/${num(stats.byRecipient.students.recipients)}` : '—' },
      ])));
    }
    return nodes;
  });
  panel.body.append(view.node);
  view.reload();

  async function publish(item) {
    const result = await formDialog(app, {
      title: `“${item.title}” yayımlansın`,
      description: 'Boş bırakırsanız hemen yayımlanır. Bildirim seçiliyse alıcılara uygulama bildirimi gider.'
        + (item.sms?.enabled ? ' SMS de gönderilecek (yalnız velilere; sessiz saatte sabahı bekler).' : ''),
      value: { notify: true, day: '', clock: '09:00' },
      fields: [
        { key: 'day', label: 'Zamanlı yayın günü (isteğe bağlı)', type: 'date' },
        { key: 'clock', label: 'Saat (SS:DD)', maxLength: 5,
          validate: (raw) => (raw && !/^([01]\d|2[0-3]):[0-5]\d$/.test(raw) ? 'Saati 09:30 gibi yazın.' : null) },
        { key: 'notify', label: 'Bildirim gönder', type: 'checkbox' },
      ],
      submitLabel: 'Yayımla',
      onSubmit: (draft, { key }) => app.api.post(`/v1/admin/announcements/${item.id}/publish`, {
        scheduledAt: draft.day && draft.day >= todayIst() ? `${draft.day}T${draft.clock || '09:00'}:00+03:00` : null,
        notify: Boolean(draft.notify) }, { key }),
    });
    if (result) {
      app.toast(result.status === 'scheduled' ? 'Duyuru zamanlandı.' : 'Duyuru yayımlandı.', 'good');
      onChange?.(result);
      view.reload();
    }
  }
}

