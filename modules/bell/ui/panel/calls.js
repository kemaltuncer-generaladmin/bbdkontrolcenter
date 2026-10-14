// Zil Sistemi — "Çağrı" sekmesi: öğrenci çağır (serbest anons), grup çağır,
// oturum çağrıları planı (Akademi'den, açıksa).
//
// SESİ OLMAYAN DÜĞME ÇİZİLİR AMA KAPALIDIR ve nedeni yazar. Basılabilen ama
// sessizlikle biten bir "Çağır", hiç olmayandan kötüdür.
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).

import { blockedButton, button, confirmSimple, confirmWithPin, h } from '../../ui-kit/kit.js';
import { shortDate } from './program.js';

/** Sekme: öğrenci çağır + grup çağır (+ oturum çağrıları planı). */
export function renderCalls(app, nodes) {
  const wrap = h('div', 'bl-calls');
  wrap.append(pageCard(app, nodes), groupsCard(app));
  const plan = sessionPlanCard(app);
  if (plan) wrap.append(plan);
  return wrap;
}

// ------------------------------------------------------------ öğrenci çağır

/**
 * Serbest cümle. Alan ekran tazelenirken KAYBOLMASIN diye bir kez kurulur ve
 * her çizimde aynı düğüm yerine konur (`nodes.pageForm`).
 */
function pageCard(app, nodes) {
  if (!nodes.pageForm) {
    const card = h('section', 'bl-card');
    const head = h('div', 'bl-card-head');
    head.append(h('h4', null, 'Öğrenci çağır'));
    head.append(h('span', 'bl-hint', 'Yazdığınız cümle okulun hoparlöründen duyurulur.'));
    const form = h('div', 'bl-page');
    const field = h('input', 'kit-input');
    field.type = 'text';
    field.maxLength = 160;
    field.placeholder = 'Örn. Elif Kaya, lütfen danışmaya gel.';
    field.setAttribute('aria-label', 'Anons metni');
    const send = button('Anons yap', {
      variant: 'primary',
      onClick: async () => {
        const text = field.value.trim();
        if (!text) {
          app.notify('Önce anons metnini yazın.', 'bad');
          field.focus();
          return;
        }
        const ok = await confirmSimple(app.root, {
          title: 'Anons okulun hoparlöründen çalınsın mı?',
          description: `“${text}”`,
          confirmLabel: 'Anons yap',
        });
        if (!ok) return;
        const result = await app.run(() => app.store.page(text));
        if (!result) return;
        if (result.status === 'queued') {
          app.notify(result.voiceCached ? 'Anons zil bilgisayarına gönderildi.'
            : 'Ses üretildi, anons zil bilgisayarına gönderildi.');
          field.value = '';
        } else {
          const reasons = {
            disabled: 'Zil sistemi kapalı; anons yapılmadı.',
            tts_unavailable: 'Anons sesi üretilemedi.',
            failed: 'Anons gönderilemedi.',
          };
          app.notify(`${reasons[result.status] || 'Anons yapılamadı.'} ${result.detail || ''}`.trim(),
            'bad');
        }
      },
    });
    field.addEventListener('keydown', (event) => { if (event.key === 'Enter') send.click(); });
    form.append(field, send);
    card.append(head, form,
      h('p', 'bl-hint bl-pad', 'Ses daha önce üretilmediyse birkaç saniye sürer.'));
    nodes.pageForm = card;
  }
  return nodes.pageForm;
}

// --------------------------------------------------------------- gruplar

function groupsCard(app) {
  const { data } = app;
  const card = h('section', 'bl-card');
  // TAŞIMA SONRASI liste Öğrenci Yönetimi'nden gelir: düzenleme yok, yalnız çağrı. Sınıf = grup
  // (iş 23): orada tek kavram "Sınıf" — liste bütün sınıflardır, metinlerde "sınıf" geçer.
  const migrated = Boolean(data.groupsMigrated);
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, migrated ? 'Sınıf çağır' : 'Grup çağır'));
  head.append(h('span', 'bl-hint', migrated ? 'Seçilen sınıfa anons geçer; zil çalmaz.'
    : 'Seçilen gruba anons geçer; zil çalmaz.'));
  card.append(head);

  const band = migrationBand(app);
  if (band) card.append(band);

  const list = migrated ? (data.studentGroups || []) : (data.groups || []);
  const grid = h('div', 'bl-groups');
  for (const group of list) grid.append(groupCard(app, group, { readonly: migrated, unit: migrated ? 'sınıf' : 'grup' }));
  if (!migrated) grid.append(newGroupCard(app));
  card.append(grid);

  if (!list.length) {
    card.append(h('p', 'bl-empty', migrated
      ? 'Öğrenci Yönetimi’nde henüz sınıf yok.'
      : 'Henüz grup yok. Bir grup açtığınızda anons sesi bir kez üretilir.'));
  }
  return card;
}

function groupCard(app, group, { readonly = false, unit = 'grup' } = {}) {
  const card = h('div', `bl-group bl-voice-${group.voice.state}`);
  const name = h('div', 'bl-group-name', group.name);
  name.title = group.text;
  card.append(name);

  const meta = h('div', 'bl-group-meta');
  const solo = group.kind === 'ozel' || group.kind === 'individual';
  meta.append(h('span', `bl-kind bl-kind-${solo ? 'ozel' : 'grup'}`, solo ? 'özel ders' : unit));
  meta.append(voiceBadge(group.voice));
  card.append(meta);

  const actions = h('div', 'bl-group-actions');
  if (group.voice.state === 'ready') {
    actions.append(button('Çağır', {
      variant: 'primary',
      title: group.text,
      onClick: () => app.run(() => app.store.callGroup(group.id),
        { success: `Anons gönderildi: ${group.name}` }),
    }));
  } else {
    actions.append(blockedButton('Çağır', voiceReason(group.voice)));
  }

  if (!readonly) {
    const rename = h('button', 'bl-icon-btn', '✎');
    rename.type = 'button';
    rename.title = 'Adını değiştir';
    rename.addEventListener('click', () => startRename(app, name, group));
    const drop = h('button', 'bl-icon-btn', '✕');
    drop.type = 'button';
    drop.title = 'Grubu kalıcı olarak sil';
    drop.addEventListener('click', async () => {
      const pin = await confirmWithPin(app.root, {
        title: `“${group.name}” kalıcı olarak silinsin mi?`,
        description: 'Grup kaydı, üretilmiş anons sesi, ses dosyası ve zil bilgisayarındaki '
          + 'kopyası birlikte silinir. GERİ ALINAMAZ.',
      });
      if (pin) {
        await app.run(() => app.store.removeGroup(group.id, pin),
          { success: 'Grup ve sesi kalıcı olarak silindi.' });
      }
    });
    actions.append(h('span', 'kit-spacer'), rename, drop);
  }
  card.append(actions);
  return card;
}

export function voiceBadge(voice) {
  const map = {
    ready: ['✓ ses hazır', 'ok'],
    pending: ['⏳ ses üretiliyor', 'wait'],
    error: ['✕ ses üretilemedi', 'bad'],
    missing: ['⏳ sıraya alınacak', 'wait'],
    off: ['— anons kapalı', 'dim'],
  };
  const [text, tone] = map[voice?.state] || map.missing;
  const node = h('span', `bl-badge ${tone}`, text);
  if (voice?.state === 'error') node.title = voice.detail;
  return node;
}

function voiceReason(voice) {
  if (voice.state === 'error') {
    return `Bu grubun anons sesi üretilemedi: ${voice.detail} `
      + 'Ayarlar › Bakım › “Sesleri yeniden üret” ile yeniden denenebilir.';
  }
  return 'Bu grubun anons sesi henüz üretilmedi. Birkaç saniye içinde hazır olur.';
}

/** Ad alanını yerinde düzenlemeye açar; bitince ekran tek kaynaktan çizilir. */
function startRename(app, nameNode, group) {
  const field = h('input', 'kit-input');
  field.value = group.name;
  field.maxLength = 60;
  field.setAttribute('aria-label', 'Grup adı');
  let closed = false;
  const finish = async (save) => {
    if (closed) return;
    closed = true;
    const next = field.value.trim();
    if (save && next && next !== group.name) {
      await app.run(() => app.store.renameGroup(group.id, next),
        { success: 'Grup adı değişti. Anons sesi yeniden üretiliyor.' });
    } else {
      app.rerender();
    }
  };
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') finish(true);
    if (event.key === 'Escape') finish(false);
  });
  field.addEventListener('blur', () => finish(true));
  nameNode.replaceWith(field);
  field.focus();
  field.select();
}

function newGroupCard(app) {
  const card = h('div', 'bl-group bl-group-new');
  const field = h('input', 'kit-input');
  field.placeholder = 'Yeni grup adı';
  field.setAttribute('aria-label', 'Yeni grup adı');
  field.maxLength = 60;
  // TÜR SEÇİMİ GÖRÜNÜR: cümle buna göre kuruluyor ("dersiniz" ↔ "dersin").
  const kind = h('select', 'kit-select');
  kind.setAttribute('aria-label', 'Ders türü');
  for (const [value, label] of [['grup', 'Grup dersi'], ['ozel', 'Özel ders']]) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    kind.append(option);
  }
  const submit = async () => {
    const name = field.value.trim();
    if (!name) { field.focus(); return; }
    field.value = '';
    await app.run(() => app.store.addGroup(name, kind.value),
      { success: `“${name}” eklendi. Anons sesi üretiliyor.` });
  };
  field.addEventListener('keydown', (event) => { if (event.key === 'Enter') submit(); });
  card.append(field, kind, button('Ekle', { onClick: submit }));
  return card;
}

/** Zil grupları → öğrenci grupları taşıma bandı (§6). */
function migrationBand(app) {
  const { data } = app;
  if (data.groupsMigrated) {
    return h('p', 'bl-hint bl-pad',
      'Sınıflar Öğrenci Yönetimi › Sınıflar’da düzenlenir (anons adı dahil); bu liste oradan okunur.');
  }
  if (!data.studentGroupsAvailable) return null;
  const band = h('div', 'bl-band');
  band.append(h('span', null, 'Zil grupları Öğrenci Yönetimi’nde sınıf olarak tutulabilir.'));
  band.append(button('Grupları taşı', {
    title: 'Her zil grubu için aynı adlı sınıf açılır. İkinci basış yeni sınıf açmaz.',
    onClick: async () => {
      const yes = await confirmSimple(app.root, {
        title: 'Zil grupları Öğrenci Yönetimi’ne taşınsın mı?',
        description: 'Her zil grubu için aynı adlı (öğrencisiz) sınıf açılır; öğrencileri '
          + 'Öğrenci Yönetimi › Sınıflar’da seçersiniz. Sonra düzenleme buradan kapanır, '
          + 'çağrı listesi oradan gelir. Zil grubu kayıtları silinmez.',
        confirmLabel: 'Taşı',
      });
      if (yes) {
        await app.run(() => app.store.migrateGroups(), {
          success: (r) => `${r.moved?.length || 0} grup taşındı (${r.created || 0} yeni).`,
        });
      }
    },
  }));
  return band;
}

// ------------------------------------------------ oturum çağrıları planı

const STATE_LABEL = {
  planned: 'bekliyor', dispatched: 'gönderildi', skipped: 'atlandı', failed: 'çalmadı',
  cancelled: 'iptal (sınıf silindi)',
};
let planDay = null;

/** Akademi'den gelen oturum çağrıları — yalnız açıksa ya da plan çağrı taşıyorsa. */
function sessionPlanCard(app) {
  const plan = app.data.plan || { days: [], sessionCalls: {} };
  const withCalls = (plan.days || []).filter((day) => (day.calls || []).length);
  if (!plan.sessionCalls?.enabled && !withCalls.length) return null;

  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Oturum çağrıları'));
  head.append(h('span', 'bl-hint', plan.sessionCalls?.enabled
    ? 'Ders zilinin arkasından o saatte başlayan grupların çağrısı okunur (Akademi ders programından).'
    : 'Kapalı — Ayarlar › Oturum çağrıları. Plan geldiği gibi gösteriliyor.'));
  card.append(head);

  if (!planDay || !(plan.days || []).some((day) => day.date === planDay)) planDay = plan.today;
  const chips = h('div', 'bl-copy-days bl-pad');
  for (const day of plan.days || []) {
    const chip = h('button', `bl-chip${day.date === planDay ? ' on' : ''}`,
      `${shortDate(day.date)} · ${(day.calls || []).length}`);
    chip.type = 'button';
    chip.addEventListener('click', () => { planDay = day.date; app.rerender(); });
    chips.append(chip);
  }
  card.append(chips);

  const day = (plan.days || []).find((item) => item.date === planDay);
  const calls = day?.calls || [];
  if (!calls.length) {
    card.append(h('p', 'bl-empty', 'Bu gün için oturum çağrısı yok.'));
    return card;
  }
  const table = h('table', 'bl-table');
  const headRow = h('tr');
  for (const title of ['Saat', 'Grup', 'Öğretmen', 'Ses', 'Durum']) headRow.append(h('th', null, title));
  table.append(h('thead'), h('tbody'));
  table.tHead.append(headRow);
  for (const call of calls) {
    const row = h('tr', call.state === 'failed' ? 'bad' : '');
    row.append(h('td', 'bl-time-cell', call.at));
    row.append(h('td', null, `${call.groupName}${call.groupKind === 'individual' ? ' (özel)' : ''}`));
    row.append(h('td', null, call.teacherName || '— genel çağrı'));
    row.append(h('td', null, { ready: '✓', generic: 'genel', missing: '⏳' }[call.voice] || '—'));
    const state = h('td', 'bl-detail', STATE_LABEL[call.state] || call.state);
    if (call.detail) state.title = call.detail;
    row.append(state);
    table.tBodies[0].append(row);
  }
  card.append(table);
  return card;
}
