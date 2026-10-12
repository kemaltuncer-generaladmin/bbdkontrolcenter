// Zil Sistemi — "Takvim" sekmesi: Akademi'den gelen zil planı, saat setleri,
// oturum çağrıları ve acil kapatma.
//
// Sözleşme: bbd-akademi/docs/integrations/km-ogrenci-gruplari-ve-zil.md §3.
//
// PLANI AKADEMİ YAZAR, BURADA DÜZENLENMEZ. Kurum takvimi (tatil, özel gün) ve
// ders programı Akademi'dedir; KM yalnız gösterir. KM'nin burada yazdıkları:
// SAAT SETLERİ (zil saatleri KM'nindir), oturum çağrısı ayarı ve o güne
// sınırlı ACİL KAPATMA ("Bugün zili kapat", PIN ister).
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import { button, confirmSimple, confirmWithPin, h } from '../../ui-kit/kit.js';

const BASE = '/api/bell';
const WEEKDAYS = ['Pzt', 'Sal', 'Çar', 'Per', 'Cum', 'Cmt', 'Paz'];
const MODE_LABEL = { normal: 'Normal', off: 'Tatil — zil yok', profile: 'Saat seti' };
const SOURCE_LABEL = {
  akademi: 'Akademi', km: 'KM acil kapatma', none: 'plan yok · haftalık saatler',
};
const STATE_LABEL = {
  planned: 'bekliyor', dispatched: 'gönderildi', skipped: 'atlandı', failed: 'çalmadı',
};

let selectedDay = null;

function trDate(iso) {
  const [y, m, d] = String(iso).split('-').map(Number);
  const date = new Date(y, m - 1, d);
  const weekday = WEEKDAYS[(date.getDay() + 6) % 7];
  return `${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')} ${weekday}`;
}

function clock(iso) {
  return iso ? String(iso).slice(11, 16) : '';
}

/** Bir günün kısa özeti: kip + gerekçe. */
export function describeDay(day, profiles) {
  if (day.override) return 'Bugün zil KM’de acil kapatıldı';
  if (day.mode === 'profile') {
    const set = (profiles || []).find((item) => item.id === day.profileId);
    return `${MODE_LABEL.profile}: ${set ? set.name : day.profileId}`;
  }
  return MODE_LABEL[day.mode] || day.mode;
}

/**
 * Takvim sekmesi.
 * @param {object} data — `/api/bell/state`
 * @param {{api, run, notify, root, saveSettings}} deps
 */
export function renderCalendar(data, deps) {
  const plan = data.plan || { days: [], sessionCalls: {} };
  const wrap = h('div', 'bl-calendar');

  // --- üst bant: plan nereden, ne zaman --------------------------------
  const band = h('p', `bl-status${plan.lastReceivedAt ? '' : ' warn'}`);
  band.append(h('span', null, plan.lastReceivedAt
    ? `Zil planı Akademi’den · son güncelleme ${clock(plan.lastReceivedAt)} `
      + `(${String(plan.lastReceivedAt).slice(8, 10)}.${String(plan.lastReceivedAt).slice(5, 7)})`
    : 'Akademi’den zil planı gelmedi — zil haftalık saatlerle çalıyor, oturum çağrısı yok.'));
  wrap.append(band);

  const columns = h('div', 'bl-columns');
  columns.append(daysCard(data, plan, deps), dayDetail(data, plan, deps));
  wrap.append(columns);
  wrap.append(sessionCallsCard(data, plan, deps), profilesCard(data, deps));
  return wrap;
}

function daysCard(data, plan, deps) {
  const card = h('section', 'bl-card');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Önümüzdeki 14 gün'));
  head.append(h('span', 'bl-hint', 'Kurum takvimi ve ders programı Akademi’de düzenlenir.'));
  card.append(head);

  const today = plan.today;
  if (!selectedDay) selectedDay = today;
  const list = h('div', 'bl-plan-days');
  for (const day of plan.days || []) {
    const pending = (day.calls || []).filter((call) => call.voice !== 'ready').length;
    const row = h('button', `bl-plan-day bl-mode-${day.override ? 'off' : day.mode}`
      + `${day.date === selectedDay ? ' active' : ''}`);
    row.type = 'button';
    row.append(h('b', null, trDate(day.date) + (day.date === today ? ' · bugün' : '')));
    row.append(h('span', null, describeDay(day, data.profiles)));
    row.append(h('span', 'bl-hint', day.reason || ''));
    row.append(h('span', 'bl-hint', SOURCE_LABEL[day.source] || day.source));
    const meta = h('span', 'bl-plan-meta', `${(day.calls || []).length} çağrı`);
    if (pending) meta.append(h('em', 'bl-badge wait', ` · ${pending} ses hazır değil`));
    if (day.lastError) meta.append(h('em', 'bl-badge bad', ` · reddedildi: ${day.lastError}`));
    row.append(meta);
    row.addEventListener('click', () => { selectedDay = day.date; deps.rerender(); });
    list.append(row);
  }
  card.append(list);
  return card;
}

function dayDetail(data, plan, deps) {
  const card = h('section', 'bl-card');
  const day = (plan.days || []).find((item) => item.date === selectedDay);
  if (!day) {
    card.append(h('p', 'bl-empty', 'Soldan bir gün seçin.'));
    return card;
  }
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, `${trDate(day.date)} — ${describeDay(day, data.profiles)}`));
  card.append(head);
  if (day.reason) card.append(h('p', 'bl-hint', day.reason));

  // ACİL KAPATMA — yalnız bugün; PIN ister. Akademi takvimine yazılmaz.
  if (day.date === plan.today) {
    if (day.override) {
      card.append(h('p', 'bl-status bad',
        `Bugünün zili kapatıldı · ${day.override.by || ''} ${clock(day.override.at)}`));
      card.append(button('Kapatmayı kaldır', {
        onClick: async () => {
          const yes = await confirmSimple(deps.root, {
            title: 'Bugünün zili yeniden açılsın mı?',
            description: 'Kalan zil saatleri ve çağrılar plandaki gibi çalar.',
            confirmLabel: 'Yeniden aç',
          });
          if (yes) {
            await deps.run(() => deps.api(`${BASE}/plan/override/clear`,
              { method: 'POST', body: { date: day.date } }), { success: 'Bugünün zili açıldı.' });
          }
        },
      }));
    } else {
      card.append(button('Bugün zili kapat', {
        variant: 'danger',
        title: 'Acil durum: bugün gün sonuna dek zil, zil sonrası anons ve çağrı çalmaz.',
        onClick: async () => {
          const pin = await confirmWithPin(deps.root, {
            title: 'Bugünün zili kapatılsın mı?',
            description: 'Gün sonuna dek zil, zil sonrası anons ve oturum çağrıları ÇALMAZ. '
              + 'Akademi takvimi değişmez; yarın plan yine geçerlidir. Elle zil ve elle '
              + 'çağrı çalışmaya devam eder.',
            confirmLabel: 'Bugün zili kapat',
          });
          if (pin) {
            await deps.run(() => deps.api(`${BASE}/plan/override`,
              { method: 'POST', body: { date: day.date, pin } }),
            { success: 'Bugünün zili kapatıldı.' });
          }
        },
      }));
    }
  }

  const calls = day.calls || [];
  if (!calls.length) {
    card.append(h('p', 'bl-empty', day.mode === 'off' ? 'Tatil: zil ve çağrı yok.'
      : 'Bu gün için oturum çağrısı yok.'));
    return card;
  }
  const table = h('table', 'bl-table');
  const headRow = h('tr');
  for (const title of ['Saat', 'Grup', 'Öğretmen', 'Derslik / ders', 'Ses', 'Durum']) {
    headRow.append(h('th', null, title));
  }
  table.append(h('thead', null), h('tbody'));
  table.tHead.append(headRow);
  for (const call of calls) {
    const row = h('tr', call.state === 'failed' ? 'bad' : '');
    row.append(h('td', 'bl-time-cell', call.at));
    row.append(h('td', null, `${call.groupName}${call.groupKind === 'individual' ? ' (özel)' : ''}`));
    row.append(h('td', null, call.teacherName || '— genel çağrı'));
    row.append(h('td', null, [call.room, call.subject].filter(Boolean).join(' · ') || '—'));
    row.append(h('td', null, { ready: '✓', generic: 'genel', missing: '⏳' }[call.voice] || '—'));
    const state = h('td', 'bl-detail', STATE_LABEL[call.state] || call.state);
    if (call.detail) state.title = call.detail;
    row.append(state);
    table.tBodies[0].append(row);
  }
  card.append(table);
  card.append(h('p', 'bl-hint',
    'Sıra: zil → zil sonrası anons → çağrılar (grup adına göre). Kesin metnin sesi hazır '
    + 'değilse grubun genel çağrısı, o da yoksa çağrı atlanır; zil yine çalar.'));
  return card;
}

function sessionCallsCard(data, plan, deps) {
  const card = h('section', 'bl-card bl-card-wide');
  card.append(h('h4', null, 'Oturum çağrıları'));
  const config = plan.sessionCalls || {};
  const akademiDays = (plan.days || []).filter((day) => day.source === 'akademi').length;
  card.append(h('p', 'bl-hint',
    'Açıkken ders zilinin ve “İyi dersler.”in arkasından o saatte başlayan oturumların '
    + 'grup çağrısı okunur. Akademi 14 günün planını gönderince açın '
    + `(şu an ${akademiDays}/14 gün Akademi’den). Kapatmak bugünkü davranışa döndürür.`));

  const row = h('div', 'bl-row');
  const toggle = h('label', 'bl-check');
  const box = h('input');
  box.type = 'checkbox';
  box.checked = Boolean(config.enabled);
  toggle.append(box, h('span', null, 'Oturum çağrıları açık'));
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
  const save = button('Kaydet', {
    variant: 'primary',
    onClick: async () => {
      if (box.checked && !config.enabled && akademiDays < 14) {
        const yes = await confirmSimple(deps.root, {
          title: 'Plan eksikken çağrılar açılsın mı?',
          description: `Akademi yalnız ${akademiDays} günün planını gönderdi. Planı olmayan `
            + 'günlerde çağrı yapılmaz, zil haftalık saatlerle çalar.',
          confirmLabel: 'Yine de aç',
        });
        if (!yes) return;
      }
      await deps.run(() => deps.saveSettings({
        ...data.settings,
        sessionCalls: {
          enabled: box.checked,
          gapSeconds: Number(gap.value || 0),
          maxPerSlot: Number(max.value || 12),
          offsetSeconds: Number(offset.value || 0),
        },
      }), { success: 'Oturum çağrısı ayarı kaydedildi.' });
    },
  });
  row.append(toggle, gapWrap, maxWrap, offWrap, save);
  card.append(row);
  return card;
}

function profilesCard(data, deps) {
  const card = h('section', 'bl-card bl-card-wide');
  const head = h('div', 'bl-card-head');
  head.append(h('h4', null, 'Saat setleri'));
  head.append(h('span', 'bl-hint',
    'Özel günler için adlı zil saatleri (“Yarım gün”, “Deneme günü”). Akademi kurum '
    + 'takviminde bir güne bağlar; o gün haftalık saatler yerine set çalar.'));
  card.append(head);

  for (const profile of data.profiles || []) card.append(profileEditor(profile, deps));
  card.append(profileEditor(null, deps));
  return card;
}

/** Saatler tek metin alanında: her satır "09:00 ders" / "10:40 teneffus Büyük teneffüs". */
export function parseProfileTimes(text) {
  const times = [];
  const errors = [];
  for (const raw of String(text || '').split('\n')) {
    const line = raw.trim();
    if (!line) continue;
    const [time, kindWord = 'ders', ...label] = line.split(/\s+/);
    const match = /^([01]?\d|2[0-3])[:.]?([0-5]\d)$/.exec(time);
    if (!match) {
      errors.push(line);
      continue;
    }
    const kind = /^ten/i.test(kindWord) ? 'teneffus' : 'ders';
    const rest = /^(ders|ten)/i.test(kindWord) ? label : [kindWord, ...label];
    times.push({ time: `${match[1].padStart(2, '0')}:${match[2]}`, kind,
      label: rest.join(' ').slice(0, 40) });
  }
  return { times, errors };
}

function profileEditor(profile, deps) {
  const box = h('div', 'bl-profile');
  const name = h('input', 'kit-input');
  name.maxLength = 40;
  name.placeholder = 'Saat setinin adı';
  name.value = profile?.name || '';
  const times = h('textarea', 'kit-textarea');
  times.rows = Math.max(3, (profile?.times || []).length + 1);
  times.placeholder = '09:00 ders\n09:40 teneffus\n10:00 ders';
  times.value = (profile?.times || [])
    .map((item) => `${item.time} ${item.kind}${item.label ? ` ${item.label}` : ''}`).join('\n');
  const actions = h('div', 'bl-tools');
  actions.append(button(profile ? 'Kaydet' : 'Saat seti ekle', {
    variant: profile ? '' : 'primary',
    onClick: async () => {
      const parsed = parseProfileTimes(times.value);
      if (parsed.errors.length) {
        deps.notify(`Anlaşılmayan satır: ${parsed.errors[0]}`, 'bad');
        return;
      }
      const body = { name: name.value.trim(), times: parsed.times };
      await deps.run(() => deps.api(profile
        ? `${BASE}/profiles/${encodeURIComponent(profile.id)}` : `${BASE}/profiles`,
      { method: profile ? 'PUT' : 'POST', body }), { success: 'Saat seti kaydedildi.' });
    },
  }));
  if (profile) {
    actions.append(button('Kaldır', {
      variant: 'danger',
      onClick: async () => {
        const yes = await confirmSimple(deps.root, {
          title: `“${profile.name}” kaldırılsın mı?`,
          description: 'Zil planında kullanılan set kaldırılamaz.',
          confirmLabel: 'Kaldır',
          danger: true,
        });
        if (yes) {
          await deps.run(() => deps.api(`${BASE}/profiles/${encodeURIComponent(profile.id)}/delete`,
            { method: 'POST' }), { success: 'Saat seti kaldırıldı.' });
        }
      },
    }));
    actions.append(h('span', 'bl-hint', `kimlik: ${profile.id}`));
  }
  const head = h('div', 'bl-row');
  head.append(name);
  box.append(head, times, actions);
  return box;
}

/**
 * Şablonda {hoca} yoksa uyarı + tek tıkla öneri. KENDİLİĞİNDEN DEĞİŞMEZ:
 * kullanıcı onaylamadan şablon yazılmaz.
 */
export function templateWarnings(data, deps) {
  const items = data.templateWarnings || [];
  if (!items.length) return null;
  const box = h('div', 'bl-warn');
  for (const item of items) {
    const line = h('div', 'bl-warn-row');
    line.append(h('b', null, item.key === 'solo' ? 'Özel ders şablonu: ' : 'Grup dersi şablonu: '),
      h('span', null, item.detail));
    line.append(button('{hoca} ile değiştir', {
      title: item.suggestion,
      onClick: async () => {
        const yes = await confirmSimple(deps.root, {
          title: 'Şablon değiştirilsin mi?',
          description: `Şu an: “${item.current}”\nYeni: “${item.suggestion}”\n`
            + 'Oturum çağrılarında öğretmenin anons adı okunur. Gruplarının sesleri yeniden '
            + 'üretilir.',
          confirmLabel: 'Değiştir',
        });
        if (yes) {
          await deps.run(() => deps.saveSettings({
            ...data.settings,
            texts: { ...data.settings.texts, [item.key]: item.suggestion },
          }), { success: 'Şablon değişti. Sesler yeniden üretiliyor.' });
        }
      },
    }));
    box.append(line);
  }
  return box;
}

/** Zil grupları → öğrenci grupları taşıma bandı (§6). */
export function migrationBand(data, deps) {
  if (data.groupsMigrated) {
    return h('p', 'bl-hint',
      'Gruplar Öğrenci Yönetimi’nde düzenlenir (Öğrenci Yönetimi › Gruplar); '
      + 'bu liste oradan okunur.');
  }
  const band = h('div', 'bl-warn');
  band.append(h('span', null, 'Gruplar artık Öğrenci Yönetimi’nde.'));
  if (!data.studentGroupsAvailable) {
    band.append(h('span', 'bl-hint', ' Öğrenci Yönetimi modülü kapalı; taşıma yapılamaz.'));
    return band;
  }
  band.append(button('Grupları taşı', {
    variant: 'primary',
    title: 'Her zil grubu için aynı adlı öğrenci grubu açılır. İkinci basış yeni grup açmaz.',
    onClick: async () => {
      const yes = await confirmSimple(deps.root, {
        title: 'Zil grupları Öğrenci Yönetimi’ne taşınsın mı?',
        description: 'Her grup için aynı adlı (öğrencisiz) öğrenci grubu açılır; öğrencileri '
          + 'Öğrenci Yönetimi › Gruplar’da seçersiniz. Sonra grup düzenleme buradan kapanır, '
          + 'çağrı listesi oradan gelir. Zil grubu kayıtları silinmez.',
        confirmLabel: 'Taşı',
      });
      if (yes) {
        await deps.run(() => deps.api(`${BASE}/groups/migrate`, { method: 'POST' }), {
          success: (r) => `${r.moved?.length || 0} grup taşındı (${r.created || 0} yeni).`,
        });
      }
    },
  }));
  return band;
}
