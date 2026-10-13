// Zil Sistemi — "Ayarlar" sekmesi: zil sesi, anons metinleri, özel gün saat
// setleri, oturum çağrıları ve bakım. Günlük işin DIŞINDA kalan her şey burada.
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).

import { button, confirmSimple, confirmWithPin, h, menuButton } from '../../ui-kit/kit.js';
import { drawer } from '../../ui-kit/layout.js';
import { voiceBadge } from './calls.js';
import { timeListEditor } from './program.js';

export function renderSettings(app) {
  const wrap = h('div', 'bl-settings');
  wrap.append(soundCard(app), textsCard(app), profilesCard(app), sessionCallsCard(app),
    maintenanceCard(app));
  return wrap;
}

function section(title, hint) {
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, title));
  if (hint) head.append(h('span', 'bl-hint', hint));
  card.append(head);
  return card;
}

// ---------------------------------------------------------------- zil sesi

function soundCard(app) {
  const { data } = app;
  const card = section('Zil sesi', 'Otomatik ve elle zilde çalan ses.');
  const row = h('div', 'bl-row');
  const select = h('select', 'kit-select');
  select.setAttribute('aria-label', 'Zil sesi');
  for (const item of data.sounds || []) {
    const option = document.createElement('option');
    option.value = item.stem;
    option.textContent = item.name;
    select.append(option);
  }
  select.value = data.settings.bellSound;
  select.addEventListener('change', () => app.run(
    () => app.store.saveSettings({ ...data.settings, bellSound: select.value }),
    { success: 'Zil sesi değişti; zil bilgisayarına gönderiliyor.' },
  ));
  row.append(h('label', 'bl-label', 'Ses'), select);
  row.append(button('Dinle', {
    title: 'Yalnız bu bilgisayardan çalar; okula duyulmaz.',
    // SESİ KABUK ÇALAR (ADR 0026): uç sesin kendisini veri URI'si olarak verir.
    onClick: () => app.run(async () => {
      const result = await app.store.preview(select.value);
      if (!result?.ok || !result.dataUri) return result || { ok: false, detail: 'Ses alınamadı.' };
      try {
        await new Audio(result.dataUri).play();
      } catch (error) {
        return { ok: false, detail: `Ses çalınamadı: ${error?.message || error}` };
      }
      return { ok: true };
    }, { reload: false }),
  }));
  const upload = h('input');
  upload.type = 'file';
  upload.accept = '.wav,.ogg,.mp3,.flac,audio/*';
  upload.hidden = true;
  upload.addEventListener('change', async () => {
    const file = upload.files?.[0];
    upload.value = '';
    if (!file) return;
    await app.run(() => app.store.uploadSound(file),
      { success: (r) => `“${r.name}” yüklendi. Listeden seçebilirsiniz.` });
  });
  row.append(button('Dosya yükle', { onClick: () => upload.click() }), upload);

  const volume = h('div', 'bl-row');
  const range = h('input', 'bl-range');
  range.type = 'range';
  range.min = '0';
  range.max = '100';
  range.step = '5';
  range.value = String(data.settings.volume);
  range.setAttribute('aria-label', 'Ses düzeyi');
  const value = h('b', 'bl-volume-value', `%${data.settings.volume}`);
  range.addEventListener('input', () => { value.textContent = `%${range.value}`; });
  range.addEventListener('change', () => app.run(
    () => app.store.saveSettings({ ...data.settings, volume: Number(range.value) }),
    { success: `Ses düzeyi %${range.value}.` },
  ));
  volume.append(h('label', 'bl-label', 'Düzey'), range, value);

  const body = h('div', 'bl-pad');
  body.append(row, volume);
  // "Bu bilgisayardan da çal" YALNIZ bu makinede ses çalıcı varsa (masaüstü
  // yerel kip). Sunucuda (ADR 0026) hoparlör yok; seçenek orada yalan söylerdi.
  if (data.localAvailable) {
    const local = h('label', 'bl-check');
    const box = h('input');
    box.type = 'checkbox';
    box.checked = Boolean(data.settings.playLocally);
    box.addEventListener('change', () => app.run(
      () => app.store.saveSettings({ ...data.settings, playLocally: box.checked }),
      { success: box.checked ? 'Bu bilgisayardan da çalacak.' : 'Yalnız zil bilgisayarından çalacak.' },
    ));
    local.append(box, h('span', null, 'Bu bilgisayardan da çal (yedek)'));
    body.append(local);
  }
  card.append(body);
  return card;
}

// --------------------------------------------------------------- metinler

function textsCard(app) {
  const card = section('Anons metinleri', 'Metin değişince ses bir kez yeniden üretilir.');
  const body = h('div', 'bl-pad bl-texts');

  body.append(h('h5', 'bl-sub', 'Zilden sonra'));
  body.append(textRow(app, { key: 'lessonAfter', label: 'Ders zilinden sonra',
    hint: 'Boş bırakırsanız ders zilinden sonra anons gitmez.' }));
  body.append(textRow(app, { key: 'breakAfter', label: 'Teneffüs zilinden sonra',
    hint: 'Boş bırakırsanız teneffüs zilinden sonra anons gitmez.' }));

  body.append(h('h5', 'bl-sub', 'Çağrılar'));
  const warn = templateWarnings(app);
  if (warn) body.append(warn);
  body.append(textRow(app, { key: 'call', label: 'Grup dersi çağrısı',
    hint: '{grup} yerine grubun adı yazılır. Çoğul hitap.' }));
  body.append(textRow(app, { key: 'solo', label: 'Özel ders çağrısı',
    hint: '{grup} yerine öğrencinin adı yazılır. Tekil hitap.' }));
  body.append(textRow(app, { key: 'page', label: 'Öğrenci çağırma şablonu',
    hint: 'Mobil uygulamadan “Öğrenci çağır”da kullanılır: {ad}, {soyad}, {sinif}.' }));

  // Öğretmensiz genel şablonlar seyrek değişir: katlanır.
  const more = h('details', 'bl-more');
  more.append(h('summary', null, 'Öğretmensiz genel çağrılar'));
  more.append(textRow(app, { key: 'callGeneric', label: 'Genel grup çağrısı',
    hint: 'Oturumun öğretmeni bilinmiyorsa ya da kesin metnin sesi hazır değilse okunur.' }));
  more.append(textRow(app, { key: 'soloGeneric', label: 'Genel özel ders çağrısı',
    hint: 'Özel derste aynı amaçla okunur.' }));
  body.append(more);

  card.append(body);
  return card;
}

function textRow(app, { key, label, hint }) {
  const { data } = app;
  const wrap = h('div', 'bl-text');
  const head = h('div', 'bl-text-head');
  head.append(h('label', 'bl-label', label));
  const after = data.afterTexts?.[key === 'lessonAfter' ? 'ders' : key === 'breakAfter' ? 'teneffus' : ''];
  if (after && after.voice.state !== 'off') head.append(voiceBadge(after.voice));
  wrap.append(head);

  const line = h('div', 'bl-text-line');
  const field = h('input', 'kit-input');
  field.setAttribute('aria-label', label);
  field.value = data.settings.texts[key] || '';
  field.maxLength = 300;
  const save = button('Kaydet', {
    onClick: () => {
      const text = field.value.trim();
      if (text === (data.settings.texts[key] || '')) {
        app.notify('Metin değişmedi.');
        return;
      }
      // BOŞ METİN GEÇERLİDİR (zil sonrası anonslarda "anons istemiyorum").
      app.run(() => app.store.saveSettings({
        ...data.settings, texts: { ...data.settings.texts, [key]: text },
      }), { success: 'Metin kaydedildi. Ses yeniden üretiliyor.' });
    },
  });
  field.addEventListener('keydown', (event) => { if (event.key === 'Enter') save.click(); });
  line.append(field, save);
  wrap.append(line, h('p', 'bl-hint', hint));
  return wrap;
}

/** Şablonda {hoca} yoksa tek satır uyarı + öneri. KENDİLİĞİNDEN DEĞİŞMEZ. */
function templateWarnings(app) {
  const { data } = app;
  const items = data.templateWarnings || [];
  if (!items.length || !data.plan?.sessionCalls?.enabled) return null;
  const box = h('div', 'bl-band');
  box.append(h('span', null, 'Çağrı şablonunda {hoca} yok: oturum çağrıları hep aynı öğretmen adını okur.'));
  box.append(button('Öneriyi gör', {
    onClick: async () => {
      const item = items[0];
      const yes = await confirmSimple(app.root, {
        title: 'Şablon değiştirilsin mi?',
        description: items.map((entry) => `“${entry.current}” → “${entry.suggestion}”`).join('\n'),
        confirmLabel: 'Değiştir',
      });
      if (!yes || !item) return;
      const texts = { ...data.settings.texts };
      for (const entry of items) texts[entry.key] = entry.suggestion;
      await app.run(() => app.store.saveSettings({ ...data.settings, texts }),
        { success: 'Şablon değişti. Sesler yeniden üretiliyor.' });
    },
  }));
  return box;
}

// ---------------------------------------------------------- saat setleri

function profilesCard(app) {
  const { data } = app;
  const card = section('Özel gün saatleri',
    'Yarım gün, deneme günü gibi günlerin zil saatleri. Akademi kurum takviminde bir güne bağlanır.');
  const list = h('div', 'bl-profiles');
  for (const profile of data.profiles || []) {
    const item = h('div', 'bl-profile');
    const head = h('div', 'bl-profile-head');
    head.append(h('b', null, profile.name), h('span', 'bl-hint', `${profile.times.length} zil`));
    head.append(h('span', 'kit-spacer'), button('Düzenle', {
      variant: 'ghost', onClick: () => openProfileEditor(app, profile),
    }));
    item.append(head);
    const chips = h('div', 'bl-profile-times');
    for (const entry of profile.times) {
      chips.append(h('span', `bl-mini k-${entry.kind}`, entry.time));
    }
    item.append(chips);
    list.append(item);
  }
  if (!(data.profiles || []).length) {
    list.append(h('p', 'bl-empty', 'Henüz özel gün saati yok.'));
  }
  const foot = h('div', 'bl-card-foot');
  foot.append(button('+ Özel gün saati ekle', { onClick: () => openProfileEditor(app, null) }));
  card.append(list, foot);
  return card;
}

function openProfileEditor(app, profile) {
  const panel = drawer(app.root, {
    title: profile ? `${profile.name} — özel gün saatleri` : 'Yeni özel gün saatleri',
    subtitle: 'Akademi kurum takviminde “saat seti” olarak bir güne bağlanınca o gün bu saatler çalar.',
  });
  const name = h('input', 'kit-input bl-profile-name');
  name.maxLength = 40;
  name.placeholder = 'Ad (ör. Yarım gün)';
  name.setAttribute('aria-label', 'Özel gün adı');
  name.value = profile?.name || '';
  const editor = timeListEditor(profile?.times || []);

  const foot = h('div', 'bl-drawer-foot');
  if (profile) {
    foot.append(button('Sil', {
      variant: 'danger',
      onClick: async () => {
        const yes = await confirmSimple(app.root, {
          title: `“${profile.name}” silinsin mi?`,
          description: 'Zil planında kullanılan set silinemez.',
          confirmLabel: 'Sil',
          danger: true,
        });
        if (!yes) return;
        const result = await app.run(() => app.store.deleteProfile(profile.id),
          { success: 'Özel gün saatleri silindi.' });
        if (result && result.ok !== false) panel.close();
      },
    }));
  }
  foot.append(h('span', 'kit-spacer'), button('Vazgeç', { onClick: () => panel.close() }),
    button('Kaydet', {
      variant: 'primary',
      onClick: async () => {
        const { times, errors } = editor.read();
        if (errors.length) return;
        if (!name.value.trim()) {
          app.notify('Ad yazın.', 'bad');
          name.focus();
          return;
        }
        const result = await app.run(() => app.store.saveProfile(profile?.id || '',
          name.value.trim(), times), { success: 'Özel gün saatleri kaydedildi.' });
        if (result && result.ok !== false) panel.close();
      },
    }));
  const label = h('label', 'bl-label', 'Ad');
  panel.body.append(label, name, editor.node, foot);
  name.focus();
}

// -------------------------------------------------------- oturum çağrıları

function sessionCallsCard(app) {
  const { data } = app;
  const plan = data.plan || { days: [], sessionCalls: {} };
  const config = plan.sessionCalls || {};
  const akademiDays = (plan.days || []).filter((day) => day.source === 'akademi').length;
  const card = section('Oturum çağrıları',
    'Açıkken ders zilinin arkasından, Akademi ders programında o saatte başlayan grupların çağrısı okunur.');
  const row = h('div', 'bl-row bl-pad');
  const toggle = h('label', 'bl-check');
  const box = h('input');
  box.type = 'checkbox';
  box.checked = Boolean(config.enabled);
  toggle.append(box, h('span', null, 'Açık'));
  const number = (label, key, min, max) => {
    const field = h('input', 'kit-input bl-number');
    field.type = 'number';
    field.min = String(min);
    field.max = String(max);
    field.value = String(config[key] ?? '');
    const wrap = h('label', 'bl-inline');
    wrap.append(h('span', 'bl-hint', label), field);
    return [wrap, field];
  };
  const [gapWrap, gap] = number('Çağrılar arası (sn)', 'gapSeconds', 0, 10);
  const [maxWrap, max] = number('Bir zilde en çok', 'maxPerSlot', 1, 40);
  const [offWrap, offset] = number('Kayma (sn)', 'offsetSeconds', 0, 120);
  row.append(toggle, gapWrap, maxWrap, offWrap, button('Kaydet', {
    onClick: async () => {
      if (box.checked && !config.enabled && akademiDays < 14) {
        const yes = await confirmSimple(app.root, {
          title: 'Plan eksikken çağrılar açılsın mı?',
          description: `Akademi ${akademiDays}/14 günün planını gönderdi. Planı olmayan günlerde `
            + 'çağrı yapılmaz, zil haftalık saatlerle çalar.',
          confirmLabel: 'Yine de aç',
        });
        if (!yes) return;
      }
      await app.run(() => app.store.saveSettings({
        ...data.settings,
        sessionCalls: {
          enabled: box.checked,
          gapSeconds: Number(gap.value || 0),
          maxPerSlot: Number(max.value || 12),
          offsetSeconds: Number(offset.value || 0),
        },
      }), { success: 'Oturum çağrısı ayarı kaydedildi.' });
    },
  }));
  card.append(row);
  return card;
}

// ------------------------------------------------------------------- bakım

function maintenanceCard(app) {
  const { data } = app;
  const card = section('Bakım', `Ses üretim kuyruğunda ${data.queue?.queued || 0} ses.`);
  const row = h('div', 'bl-row bl-pad');
  row.append(menuButton('Bakım işlemleri', [
    { label: 'Sesleri yeniden üret', title: 'Eksik ya da hata almış sesleri yeniden sıraya alır.',
      onClick: () => app.run(app.store.rebuildVoices, {
        success: (r) => (r.queued ? `${r.queued} ses sıraya alındı.` : 'Eksik ses yok.') }) },
    { label: 'Zil bilgisayarını eşitle', title: 'Sesleri zil bilgisayarına hemen gönderir.',
      onClick: () => app.run(app.store.syncAgent, {
        success: (r) => (r.ok ? `${r.uploaded} ses gönderildi.` : r.detail) }) },
    { label: 'Zil saatlerini yeniden kur', title: 'Tetik tablosunu kayıtlı saatlerden yeniden kurar.',
      onClick: () => app.run(app.store.reschedule, {
        success: (r) => `${r.triggers ?? 0} tetik kuruldu.` }) },
    { label: 'Kullanılmayan sesleri sil…', title: 'Hiçbir yere bağlı olmayan anons seslerini siler.',
      onClick: async () => {
        const pin = await confirmWithPin(app.root, {
          title: 'Kullanılmayan anons sesleri silinsin mi?',
          description: 'Şu an hiçbir grubun ve hiçbir anonsun kullanmadığı ses kayıtları kalıcı '
            + 'silinir. Kullanımdaki sesler etkilenmez. GERİ ALINAMAZ.',
        });
        if (pin) {
          await app.run(() => app.store.pruneVoices(pin), {
            success: (r) => (r.removed ? `${r.removed} ses silindi.` : 'Silinecek ses yoktu.'),
          });
        }
      } },
  ]));
  card.append(row);
  return card;
}
