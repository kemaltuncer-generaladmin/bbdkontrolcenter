// BBD Akademi — Ayarlar (F-A15, F-A16 ve yalnız KM'den yapılan kurum yönetimi).
//
// Sekmeler: Özellikler · Kurum · Tema ve logo · Anons şablonları · Senkron
// (KM olay iletimi, settings-sync.js) · Dizin eşitleme · Entegrasyon
// anahtarları · Denetim kaydı. SMS (settings-sms.js) 15.10.2026'dan beri
// İletişim → SMS ekranındadır (sms.js; `{tab: 'sms'}` oraya yönlenir).
// "Kantin kuralları" sekmesi Finans ve Kantin → Kantin ekranındadır
// (`canteen.js`; gövdesi burada, `canteenTab`).
//
// ÖZELLİKLER İLK SEKME: panelin menüsü kurumda AÇIK özelliklere göre çizilir
// (`sections.js`); bir bayrak değişince `app.refreshFeatures()` menüyü hemen
// yeniden çizer.
// Sekme, okuma işlemine yetki yoksa hiç çizilmez; yazma düğmeleri
// `actionButton` ile gerekçeli kapanır (asıl kapı vekildedir, K9).
//
// KISMİ GÜNCELLEME: ayar formları kitin `formGrid` kirli alan takibini
// kullanır; yalnız DEĞİŞEN alanlar gönderilir. İç içe gruplarda (ör.
// `support`, `defaultNotifications`) değişen alanın GRUBU, sunucudaki
// mevcut değerlerle birleştirilip tam gönderilir — Akademi grubu ister
// birleştirsin ister değiştirsin, göndermediğimiz komşu alan boşalmaz.
//
// KANTİN (A12): "Kantin kuralları" yalnız kantinin KURUM AYARIDIR (limit üst
// sınırı, görünürlük ve bildirim varsayılanları). Para hareketi yoktur ve
// kurum finansıyla hiçbir toplamda buluşmaz; turuncu çizgiyle ayrılır.
//
// Okumalar seyrek değişir: `getCached` + yazmadan sonra `invalidate`.
// Anons şablonları önbelleği Panel bölümüyle ORTAKTIR ('/v1/admin/pa/templates').

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { badge, card, hintBox, tabBar } from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { AkademiError } from './api.js';
import {
  WEEKDAYS, actionButton, confirmAction, empty, formDialog, kv, loader, money, problem,
  runAction, sectionHead, staticList, toolbar,
} from './common.js';
import { brandingTab } from './settings-branding.js';
import { auditTab, directoryTab, keysTab } from './settings-admin.js';
import { syncTab } from './settings-sync.js';

/**
 * Sekme kaydı: anahtar → {etiket, okuma işlemi}. Sıra ekrandaki sıradır.
 * `visible(app)` verilen sekme okuma işleminden bağımsız açılır (Senkron kartı
 * geçidin iznini de tanır: Akademi tarafını göremeyen kullanıcı geçidi görebilir).
 */
const TABS = [
  { key: 'features', label: 'Özellikler', read: 'admin_listFeatures' },
  { key: 'institution', label: 'Kurum', read: 'admin_getSettings' },
  { key: 'branding', label: 'Tema ve logo', read: 'admin_getBranding' },
  { key: 'pa', label: 'Anons şablonları', read: 'admin_listPaTemplates' },
  { key: 'sync', label: 'Senkron', read: 'admin_getDirectorySyncStatus',
    visible: (app) => Boolean(app.status?.gatewayEvents?.canView) },
  { key: 'directory', label: 'Dizin eşitleme', read: 'admin_getDirectorySyncStatus' },
  { key: 'keys', label: 'Entegrasyon anahtarları', read: 'admin_listIntegrationKeys' },
  { key: 'audit', label: 'Denetim kaydı', read: 'admin_listAuditLog' },
];

export function section(app) {
  loadStyles(import.meta.url, 'settings.css');
  const node = h('div', 'ak-stack ak-set');
  node.append(sectionHead('Kurum ayarları', {
    subtitle: 'Hangi özelliklerin açık olduğu, kurum bilgileri, tema, senkron, entegrasyon ve denetim kaydı.' }));

  const visible = TABS.filter((tab) => app.can(tab.read) || tab.visible?.(app));
  if (!visible.length) {
    node.append(empty('Görüntülenecek ayar yok', 'Ayarlar sekmelerinden hiçbirine yetkiniz yok.'));
    return { node, shown() {}, destroy() {} };
  }

  const bodies = {};
  let active = null;
  const goTab = (key) => {
    if (!visible.some((tab) => tab.key === key)) return;
    tabs.select(key, false);
    showTab(key);
  };
  const builders = {
    institution: () => institutionTab(app),
    branding: () => brandingTab(app),
    features: () => featuresTab(app),
    pa: () => paTemplatesTab(app, { goTab }),
    keys: () => keysTab(app),
    directory: () => directoryTab(app),
    sync: () => syncTab(app),
    audit: () => auditTab(app),
  };
  const tabs = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key,
    (key) => showTab(key));
  const slot = h('div', 'ak-set-body');
  node.append(tabs.node, slot);

  function showTab(key) {
    if (active && active !== key) bodies[active]?.hidden?.();
    if (!bodies[key]) {
      bodies[key] = builders[key]();
      slot.append(bodies[key].node);
      bodies[key].reload();
    }
    for (const [name, body] of Object.entries(bodies)) body.node.hidden = name !== key;
    active = key;
    bodies[key].shown?.();
  }

  let started = false;
  return {
    node,
    shown(payload) {
      // SMS İletişim grubuna taşındı (15.10.2026): eski derin bağlantı oraya gider.
      if (payload?.tab === 'sms') {
        app.go('sms');
        return;
      }
      const wanted = payload?.tab && visible.some((tab) => tab.key === payload.tab)
        ? payload.tab : null;
      if (!started) {
        started = true;
        const first = wanted || visible[0].key;
        tabs.select(first, false);
        showTab(first);
      } else if (wanted) {
        goTab(wanted);
      }
    },
    destroy() {
      for (const body of Object.values(bodies)) {
        try { body.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}

// ------------------------------------------------------------ ortak form

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const clockRule = (label) => (value) => (value && !CLOCK.test(String(value).trim())
  ? `${label} SS:DD biçiminde olmalı (ör. 22:00).` : null);
const textOrNull = (value) => String(value ?? '').trim() || null;
const intOrNull = (value) => (value === null || value === undefined || value === ''
  ? null : Math.round(Number(value)));
const OFF = Symbol('feature-off');

/** Kapalı özellik → ekranda sebebini söyleyen durum (403 feature_disabled). */
function featureOff(failure) {
  return failure instanceof AkademiError && failure.code === 'feature_disabled';
}

/** Sunucu kaydından düz form değerlerine: alan `path` ile okunur. */
function flatten(groups, data) {
  const out = {};
  for (const group of groups) {
    for (const field of group.fields) {
      const raw = field.path.reduce((acc, part) => acc?.[part], data);
      out[field.key] = field.from ? field.from(raw)
        : raw ?? (field.type === 'checkbox' ? false : '');
    }
  }
  return out;
}

/**
 * Yalnız kirli alanlardan PATCH gövdesi. İç içe grup alanı değiştiyse grup,
 * sunucudaki mevcut değerle birleştirilip TAM gönderilir (bkz. başlık).
 */
function buildPatch(groups, dirtyKeys, draft, original) {
  const body = {};
  const fields = groups.flatMap((group) => group.fields).filter((field) => dirtyKeys.includes(field.key));
  for (const field of fields) {
    const value = field.to ? field.to(draft[field.key]) : draft[field.key];
    if (field.path.length === 1) {
      body[field.path[0]] = value;
    } else {
      const [head, tail] = field.path;
      body[head] = body[head] || { ...(original?.[head] || {}) };
      body[head][tail] = value;
    }
  }
  return body;
}

/**
 * Kart kart gruplanmış ayar formu + kaydet şeridi. Her grup ayrı `formGrid`
 * (kendi kirli takibiyle); "Kaydet" yalnız değişenleri tek PATCH'te yollar.
 */
export function settingsTab(app, spec) {
  const node = h('div', `ak-stack${spec.className ? ` ${spec.className}` : ''}`);
  let original = null;
  let grids = [];
  let bar = null;

  const view = loader(async () => {
    try {
      return await spec.read();
    } catch (failure) {
      if (spec.off && featureOff(failure)) return { [OFF]: true };
      throw failure;
    }
  }, (data) => render(data), { rows: 6, columns: 3 });
  node.append(view.node);

  function render(data) {
    for (const entry of grids) entry.grid.destroy();
    grids = [];
    if (data?.[OFF]) { bar = null; return spec.off(); }
    original = data;
    bar = saveBar(app, spec.opId, { onSave: submit, onReset: revert });
    const parts = [bar.node];
    if (spec.intro) parts.push(spec.intro());
    const flat = flatten(spec.groups, data);
    for (const group of spec.groups) {
      const grid = formGrid({ fields: group.fields, value: flat, onChange: paint });
      grid.node.classList.add('ak-set-grid');
      const box = card(group.title, grid.node, group.hint);
      if (group.className) box.classList.add(group.className);
      grids.push({ grid, keys: group.fields.map((field) => field.key) });
      parts.push(box);
    }
    if (spec.extra) parts.push(spec.extra(data));
    paint();
    return parts;
  }

  const dirtyKeys = () => grids.flatMap((entry) => entry.grid.dirty());
  function draft() {
    const out = {};
    for (const { grid, keys } of grids) {
      const values = grid.draft();
      for (const key of keys) out[key] = values[key];
    }
    return out;
  }
  function paint() { bar?.paint(dirtyKeys().length); }

  function revert() {
    const flat = flatten(spec.groups, original);
    for (const { grid } of grids) grid.reset(flat);
    paint();
  }

  async function submit() {
    for (const { grid } of grids) grid.showErrors();
    if (grids.some(({ grid }) => !grid.valid())) {
      app.toast('İşaretli alanları düzeltin.', 'bad');
      return;
    }
    const keys = dirtyKeys();
    if (!keys.length) return;
    const body = buildPatch(spec.groups, keys, draft(), original);
    bar.busy(true);
    paint();
    try {
      const next = await spec.write(body);
      app.toast(spec.saved || 'Ayarlar kaydedildi.', 'good');
      view.node.replaceChildren(...render(next && typeof next === 'object' ? next : original));
    } catch (failure) {
      app.toast(problem(failure), 'bad');
      bar?.busy(false);
      paint();
    }
  }

  return {
    node,
    reload: () => view.reload(),
    destroy() { for (const entry of grids) entry.grid.destroy(); },
  };
}

function saveBar(app, operationId, { onSave, onReset }) {
  const node = h('div', 'ak-set-savebar');
  const text = h('span', 'ak-set-savebar-text', 'Değişiklik yok');
  const reset = button('Değişiklikleri geri al', { variant: 'ghost', onClick: onReset });
  const save = actionButton(app, operationId, 'Kaydet', { variant: 'primary', onClick: onSave });
  const blocked = save.dataset.blocked === '1';
  node.append(text, h('span', 'kit-spacer'), reset, save);
  let busy = false;
  return {
    node,
    paint(count) {
      text.textContent = count ? `${count} kaydedilmemiş değişiklik` : 'Değişiklik yok';
      node.classList.toggle('dirty', count > 0);
      reset.disabled = busy || !count;
      if (!blocked) {
        save.disabled = busy || !count;
        save.textContent = busy ? 'Kaydediliyor…' : 'Kaydet';
      }
    },
    busy(flag) { busy = flag; },
  };
}

// ------------------------------------------------------------------ kurum

const WEEKDAY_OPTIONS = WEEKDAYS.map(([value, , label]) => ({ value, label }));

const INSTITUTION_GROUPS = [
  { title: 'Kurum kimliği', hint: 'Uygulamalarda ve belgelerde görünen ad.', fields: [
    { key: 'name', path: ['name'], label: 'Kurum adı', required: true, maxLength: 120, wide: true },
    { key: 'shortName', path: ['shortName'], label: 'Kısa ad', maxLength: 40, to: textOrNull,
      hint: 'Dar ekranlarda ve bildirim başlığında kullanılır.' },
    { key: 'timezone', path: ['timezone'], label: 'Saat dilimi', type: 'static',
      hint: 'Akademi bütün tarih ve saatleri bu dilimde hesaplar.' },
    { key: 'address', path: ['address'], label: 'Adres', type: 'textarea', maxLength: 300, wide: true,
      to: textOrNull },
  ] },
  { title: 'Destek iletişimi', hint: 'Veli ve öğrenci uygulamasındaki “Kuruma ulaş” ekranı.', fields: [
    { key: 'supportPhone', path: ['support', 'phone'], label: 'Telefon', maxLength: 20, to: textOrNull,
      placeholder: '0212 123 45 67' },
    { key: 'supportWhatsapp', path: ['support', 'whatsapp'], label: 'WhatsApp (cep)', type: 'phone',
      to: textOrNull },
    { key: 'supportEmail', path: ['support', 'email'], label: 'E-posta', type: 'email', maxLength: 120,
      to: textOrNull },
    { key: 'supportHours', path: ['support', 'hours'], label: 'Çalışma saatleri', maxLength: 80,
      to: textOrNull, placeholder: 'Hafta içi 09:00–18:00' },
  ] },
  { title: 'Bildirim varsayılanları',
    hint: 'Yeni kullanıcıların başlangıç tercihleri; herkes kendi uygulamasından değiştirebilir.', fields: [
      { key: 'quietStart', path: ['quietHoursDefault', 'start'], label: 'Sessiz saat başlangıcı',
        maxLength: 5, placeholder: '22:00', validate: clockRule('Sessiz saat başlangıcı'),
        to: (value) => String(value || '').trim() },
      { key: 'quietEnd', path: ['quietHoursDefault', 'end'], label: 'Sessiz saat bitişi', maxLength: 5,
        placeholder: '07:30', validate: clockRule('Sessiz saat bitişi'),
        to: (value) => String(value || '').trim(),
        hint: 'Bu aralıkta acil olmayan bildirim sessiz gelir.' },
      { key: 'notifyAbsence', path: ['attendance', 'notifyGuardiansOnAbsence'], type: 'checkbox',
        label: 'Devamsızlıkta veliye anında bildirim', to: Boolean },
      { key: 'weeklyEnabled', path: ['reports', 'weeklyReportEnabled'], type: 'checkbox',
        label: 'Haftalık gelişim raporu gönderilsin', to: Boolean },
      { key: 'weeklyDay', path: ['reports', 'weeklyReportDay'], label: 'Rapor günü', type: 'select',
        options: WEEKDAY_OPTIONS },
      { key: 'weeklyTime', path: ['reports', 'weeklyReportTime'], label: 'Rapor saati', maxLength: 5,
        placeholder: '18:00', validate: clockRule('Rapor saati'), to: (value) => String(value || '').trim() },
    ] },
  { title: 'Yoklama ve kitap', fields: [
    { key: 'lateAfter', path: ['attendance', 'lateAfterMinutes'], label: 'Geç sayılma (dakika)',
      type: 'number', min: 0, max: 120, required: true, to: intOrNull,
      hint: 'Ders başladıktan bu kadar dakika sonra gelen “geç” işaretlenir.' },
    { key: 'bookExpiry', path: ['books', 'requestExpiryDays'], label: 'Kitap talebi geçerlilik (gün)',
      type: 'number', min: 1, max: 60, required: true, to: intOrNull,
      hint: 'Veli bu sürede onaylamazsa talep düşer.' },
    { key: 'bookMax', path: ['books', 'maxQuantity'], label: 'Talep başına en çok adet', type: 'number',
      min: 1, max: 50, required: true, to: intOrNull },
  ] },
  { title: 'Öğretmen / koç',
    hint: 'Öğretmen / koç uygulamasında hangi öğrencilerle çalışılır. Kurum finansı, kantin parası, personel, '
      + 'ayarlar, KVKK ve dışa aktarımlar her durumda yalnız yöneticide kalır. Uygulama girişi: Özellikler → '
      + '“Öğretmen / koç portalı”; kimin gireceği: Personel → Akademi rolleri.',
    fields: [
      { key: 'teacherScope', path: ['teacher', 'scope'], label: 'Öğretmen kapsamı', type: 'select', wide: true,
        options: [
          { value: 'related', label: 'Yalnız ders verdiği ve koçu olduğu öğrenciler' },
          { value: 'all', label: 'Kurumdaki bütün öğrenciler' },
        ],
        from: (value) => value || 'related',
        hint: 'Bütün öğrenciler: öğretmen her öğrencide koçluk, ödev, plan, hata defteri, deneme, değerlendirme ve '
          + 'yoklama işlerini yapar. Değişiklik birkaç dakika içinde açık oturumlara da yansır.' },
    ] },
  { title: 'Atama paketleri', hint: 'Şablonlar → Atama paketleri. Paket tek tek de “otomatik” işaretli olmalı.',
    fields: [
      { key: 'autoBundles', path: ['assignments', 'autoApplyBundlesToNewStudents'], type: 'checkbox', to: Boolean,
        label: 'Atama paketlerini yeni öğrencilere kendiliğinden uygula',
        hint: 'KM\'den yeni öğrenci gelince kuralı uyan ve “otomatik” işaretli paketler (program, kaynak, plan) '
          + 'hemen verilir. Varsayılan kapalı.' },
    ] },
  { title: 'Hata defteri saklama',
    hint: 'Disk ve depolama şişmesin, defter sade kalsın. Süre boş bırakılırsa o kural kapalıdır. Öğrencinin “Sakla” '
      + 'dediği hataya dokunulmaz; özet ve analizler silinen hataları saymaya devam eder.',
    fields: [
      { key: 'mistakeRetention', path: ['mistakes', 'retentionEnabled'], type: 'checkbox', to: Boolean, wide: true,
        label: 'Eski hata kayıtlarını kendiliğinden temizle',
        hint: 'Kapalıyken hiçbir hata kaydı ya da fotoğrafı silinmez; gönderilmiş uyarılar geri çekilir.' },
      { key: 'mistakeImageDays', path: ['mistakes', 'learnedImageDays'], label: '“Öğrenildi” fotoğrafları (gün)',
        type: 'number', min: 1, max: 3650, to: intOrNull, placeholder: 'Silinmez',
        hint: 'Öğrenildi arşivine geçen hatanın fotoğrafları bu kadar gün sonra silinir; metin ve konu kalır. '
          + 'Önerilen 30.' },
      { key: 'mistakeRecordDays', path: ['mistakes', 'learnedRecordDays'], label: '“Öğrenildi” kaydı (gün)',
        type: 'number', min: 60, max: 3650, to: intOrNull, placeholder: 'Silinmez',
        hint: 'Arşivdeki kaydın kendisi bu kadar gün sonra silinir (en az 60). Önerilen 180.' },
      { key: 'mistakeStaleDays', path: ['mistakes', 'staleRecordDays'], label: 'Dokunulmayan eski hata (gün)',
        type: 'number', min: 30, max: 3650, to: intOrNull, placeholder: 'Silinmez',
        hint: 'Hiç tekrar edilmeyen / düzenlenmeyen aktif hata bu kadar gün sonra silinir. Önerilen 365.' },
      { key: 'mistakeWarnDays', path: ['mistakes', 'staleWarningDays'], label: 'Öğrenciye önceden uyarı (gün)',
        type: 'number', min: 1, max: 30, required: true, to: intOrNull,
        hint: 'Eski hata silinmeden bu kadar gün önce öğrenciye “eski hataların temizlenecek” bildirimi gider.' },
    ] },
  { title: 'Kurum finansı', className: 'ak-money-finance',
    hint: 'Yalnız kurum carisi; kantin hesabını etkilemez. Hatırlatmanın SMS ile de gitmesi: Ayarlar › SMS.', fields: [
      { key: 'receiptPrefix', path: ['finance', 'receiptPrefix'], label: 'Makbuz öneki', maxLength: 12,
        placeholder: 'BBD', hint: 'Makbuz numarasının başı (ör. BBD-2026-000123).' },
      { key: 'bookDueDays', path: ['finance', 'bookChargeDueDays'], label: 'Kitap kalemi vadesi (gün)',
        type: 'number', min: 0, max: 180, required: true, to: intOrNull },
      { key: 'reminderDays', path: ['finance', 'reminderDaysBefore'], label: 'Vade öncesi hatırlatma (gün)',
        placeholder: '7, 3, 1', maxLength: 40,
        from: (list) => (Array.isArray(list) ? list.join(', ') : ''),
        to: (text) => parseDayList(text),
        validate: (text) => (parseDayList(text) === null ? 'Günleri virgülle yazın (ör. 7, 3, 1); 0–60 arası.' : null),
        hint: 'Virgülle ayırın; boş bırakılırsa vade öncesi hatırlatma gitmez.' },
      { key: 'overdueEvery', path: ['finance', 'overdueReminderEveryDays'],
        label: 'Gecikmede hatırlatma sıklığı (gün)', type: 'number', min: 0, max: 60, to: intOrNull,
        hint: '0: gecikme hatırlatması kendiliğinden gitmez.' },
      { key: 'withdrawalDeduction', path: ['finance', 'withdrawalDeductionPercent'],
        label: 'Ayrılmada kurum kesintisi (%)', type: 'number', min: 0, max: 100, to: intOrNull,
        hint: 'Ayrılan öğrencinin iade önerisi: ödenen − okunan süre oranında hizmet bedeli − sözleşme tutarının bu '
          + 'yüzdesi. Kayıt sözleşmenizdeki oranı yazın (yasal oranı muhasebeciniz / hukukçunuzla doğrulayın); '
          + 'ödeme planı belgesinin koşullarında da bu oran yazar.' },
    ] },
];

/** "7, 3, 1" → [7, 3, 1] (büyükten küçüğe, tekil). Geçersizse null. */
function parseDayList(text) {
  const parts = String(text ?? '').split(/[,;\s]+/).filter(Boolean);
  const days = [];
  for (const part of parts) {
    if (!/^\d{1,2}$/.test(part) || Number(part) > 60) return null;
    days.push(Number(part));
  }
  return [...new Set(days)].sort((a, b) => b - a);
}

function institutionTab(app) {
  return settingsTab(app, {
    opId: 'admin_patchSettings',
    groups: INSTITUTION_GROUPS,
    read: () => app.api.getCached('/v1/admin/settings'),
    write: async (body) => {
      const next = await app.api.patch('/v1/admin/settings', body);
      app.api.invalidate('/v1/admin/settings');
      return next;
    },
    saved: 'Kurum ayarları kaydedildi.',
  });
}

// ------------------------------------------------------------------ kantin

const CANTEEN_GROUPS = [
  { title: 'Veli kontrolleri', fields: [
    { key: 'maxLimit', path: ['maxGuardianLimitMinor'], label: 'Veli limit üst sınırı', type: 'money',
      min: 0, from: (value) => value ?? null, to: (value) => (value === '' ? null : value ?? null),
      hint: 'Velinin koyabileceği harcama limitinin üst sınırı. Boş: sınır yok.' },
    { key: 'guardianCanBlock', path: ['guardianCanBlock'], type: 'checkbox', to: Boolean,
      label: 'Veli kantini kapatabilir', hint: 'Açıksa veli uygulamadan öğrencinin kantin kullanımını durdurabilir.' },
  ] },
  { title: 'Öğrenci görünürlüğü', hint: 'Yeni öğrenciler için varsayılan; veli öğrenci bazında değiştirebilir.',
    fields: [
      { key: 'seeBalance', path: ['defaultVisibility', 'studentCanSeeBalance'], type: 'checkbox',
        label: 'Öğrenci bakiyesini görür', to: Boolean },
      { key: 'seeTransactions', path: ['defaultVisibility', 'studentCanSeeTransactions'], type: 'checkbox',
        label: 'Öğrenci harcama geçmişini görür', to: Boolean },
      { key: 'seeLimit', path: ['defaultVisibility', 'studentCanSeeLimit'], type: 'checkbox',
        label: 'Öğrenci limitini görür', to: Boolean },
    ] },
  { title: 'Veli bildirimleri', hint: 'Velinin başlangıç tercihleri; veli uygulamadan değiştirebilir.', fields: [
    { key: 'nEach', path: ['defaultNotifications', 'eachPurchase'], type: 'checkbox',
      label: 'Her alışverişte', to: Boolean },
    { key: 'nDaily', path: ['defaultNotifications', 'dailySummary'], type: 'checkbox',
      label: 'Günlük özet', to: Boolean },
    { key: 'nLimit', path: ['defaultNotifications', 'limitReached'], type: 'checkbox',
      label: 'Limit dolunca', to: Boolean },
    { key: 'nBlocked', path: ['defaultNotifications', 'blockedAttempt'], type: 'checkbox',
      label: 'Engellenen alışveriş denemesinde', to: Boolean },
    { key: 'nThreshold', path: ['defaultNotifications', 'thresholdExceeded'], type: 'checkbox',
      label: 'Tek alışveriş eşiği aşınca', to: Boolean },
    { key: 'nThresholdMinor', path: ['defaultNotifications', 'notifyThresholdMinor'], type: 'money',
      label: 'Eşik tutarı', min: 0, from: (value) => value ?? null,
      to: (value) => (value === '' ? null : value ?? null),
      validate: (value, draft) => (draft.nThreshold && (value === null || value === undefined || value === '')
        ? 'Eşik bildirimi açıkken tutar yazın.' : null) },
  ] },
];

/** Kantin kuralları — Finans ve Kantin → Kantin ekranında çizilir (`canteen.js`). */
export function canteenTab(app, { goTab }) {
  return settingsTab(app, {
    opId: 'admin_patchCanteenSettings',
    className: 'ak-money-canteen ak-set-canteen',
    groups: CANTEEN_GROUPS,
    read: () => app.api.getCached('/v1/admin/canteen/settings'),
    write: async (body) => {
      const next = await app.api.patch('/v1/admin/canteen/settings', body);
      app.api.invalidate('/v1/admin/canteen/settings');
      return next;
    },
    saved: 'Kantin kuralları kaydedildi.',
    intro: () => hintBox('Kantin parası kurum finansından tamamen ayrıdır. Burada yalnız kantinin kurum '
      + 'kuralları durur; öğrenci bazında veli kontrolleri ve kantin tahsilatı öğrencinin kartındadır '
      + '(Öğrenciler → öğrenci → Kayıt ve veliler).'),
    extra: (data) => card('Ön ödeme', kv([
      ['Borçtan fazla tahsilat', data.allowPrepayment ? 'İzinli' : 'Kapalı'],
      ['En çok ön ödeme', data.allowPrepayment ? money(data.maxPrepaymentMinor) : '—'],
    ]), 'Bilgi içindir; bu ekrandan değiştirilmez.'),
    off: () => empty('Kantin özelliği kapalı',
      'Kantin özelliği kapalıyken kantin kuralları kullanılamaz.',
      [button('Özelliklere git', { onClick: () => goTab('features') })]),
  });
}

// --------------------------------------------------------------- özellikler

/** Bayrak → [ad, açıklama]. Bilinmeyen bayrak kendi anahtarıyla görünür. */
const FEATURES = {
  canteen: ['Kantin (veli ve öğrenci uygulamasında)',
    'Açıkken veli ve öğrenci uygulamada kantin bakiyesini, harcamalarını, ödeme taleplerini, limit ve yasaklı ürün '
    + 'ayarlarını görür; kantin bildirimleri gider. Kapalıyken veli ve öğrenci kantini HİÇ görmez (sekme, kart, '
    + 'bildirim, ödeme talebi yok). Kantin kasası ve Kontrol Merkezi kantin modülü etkilenmez.'],
  tablets: ['Öğrenci tabletleri', 'Tablet yönetimi ve ders modu.'],
  pa: ['Anons sistemi', 'Sınıflara sesli anons ve öğrenci çağırma.'],
  books: ['Kitap talepleri', 'Mağaza üzerinden kitap talebi, veli onayı ve sipariş.'],
  finance: ['Kurum finansı', 'Cari hesap, taksit planı ve tahsilat.'],
  'finance.card': ['Uygulamadan kartla ödeme', 'Velinin kurum borcunu uygulamadan kartla ödemesi.'],
  'teacher.portal': ['Öğretmen / koç portalı',
    'Öğretmen / koç uygulaması: Bugün (dersler, yoklama), Öğrencilerim, Gelen kutusu (sorular, kontrol, mesajlar), '
    + 'Kütüphane, Profil. Öğrenci kapsamı Kurum → Öğretmen / koç ayarındadır.'],
  'directory.source': ['Öğrenci dizini KM\'den', 'Öğrenci ve veli dizini Kontrol Merkezi\'nden eşitlenir.'],
  reading_league: ['Okuma ligi', 'Öğrenciler arası okuma sıralaması.'],
  class_comparison: ['Anonim sınıf kıyası', 'Öğrenci kendi sonucunu sınıf ortalamasıyla kıyaslar.'],
  flashcards: ['Bilgi kartları', 'Aralıklı tekrar kartları.'],
  self_review: ['Öz değerlendirme', 'Öğrencinin haftalık öz değerlendirmesi.'],
  early_warning: ['Erken uyarı', 'Düşüş gösteren öğrenciler için uyarı listesi.'],
  guardian_mistake_images: ['Velinin hata defteri görselleri',
    'Öğrenci paylaşırsa veli hata defterindeki görselleri görür.'],
  attendance_notifications: ['Veliye yoklama bildirimi',
    'Gelmedi / geç kaldı bildirimi veliye uygulama bildirimi olarak gider; devamsızlık SMS\'i gönderilmez (kurum kararı).'],
  attendance_signals: ['Varlık ipuçları', 'Kantin ve tablet hareketlerinden "kurumda" ipucu (canlı tarama).'],
  bell_plan_sync: ['Zil planı eşitleme', 'Ders programından üretilen zil planı Kontrol Merkezi\'ne itilir.'],
  coaching: ['Koçluk', 'Koç eşlemesi, haftalık program ve koçluk kartı.'],
  video_tasks: ['Video görevleri', 'Plana YouTube video görevi eklenebilir.'],
  coach_questions: ['Koça soru', 'Öğrenci koçuna soru sorar, koç yanıtlar.'],
  coach_messaging: ['Koç ↔ öğrenci mesajı', 'Koç ile öğrenci arasında yazışma.'],
  guardian_coach_messaging: ['Veli ↔ koç mesajı', 'Veli ile koç arasında yazışma.'],
  coach_meetings: ['Koç randevusu', 'Öğrenci ve veli koçtan görüşme randevusu alır.'],
  leave_request_notifications: ['İzin talebi bildirimi', 'İzin talebi onaylanınca ya da reddedilince veliye bildirim.'],
};
/** Kapatma onayında özelliğe özgü açıklama (yoksa genel metin). */
const FEATURE_OFF_NOTE = {
  canteen: 'Veli ve öğrenci uygulamasında kantin sekmesi, kartları, bildirimleri ve ödeme talepleri hemen kalkar. '
    + 'Kantin kasası ve Kontrol Merkezi kantin modülü çalışmaya devam eder; kayıtlı veri silinmez, yeniden açınca geri gelir.',
};
/** Bayrak → çalışması için açık olması gereken bayraklar. */
const REQUIRES = { 'finance.card': ['finance'] };
const featureName = (key) => FEATURES[key]?.[0] || key;

function featuresTab(app) {
  const node = h('div', 'ak-stack');
  node.append(hintBox('Kapalı özelliğin ekranları öğrenci, veli ve yönetici uygulamalarında ve bu panelin '
    + 'menüsünde görünmez; açınca hemen gelir, kayıtlı veri silinmez. “Platform kilitli” olanlar buradan '
    + 'değiştirilemez.'));
  let items = [];
  const canPatch = app.can('admin_patchFeature');
  const view = loader(() => app.api.getCached('/v1/admin/features'), (data) => {
    items = data?.items || [];
    return paint();
  }, { rows: 8, columns: 3 });
  node.append(view.node);

  function paint() {
    const list = h('div', 'kit-card ak-set-flags');
    const order = [...Object.keys(FEATURES), ...items.map((item) => item.key)
      .filter((key) => !FEATURES[key])];
    const byKey = new Map(items.map((item) => [item.key, item]));
    for (const key of order) {
      const item = byKey.get(key);
      if (item) list.append(flagRow(item, byKey));
    }
    if (!list.childElementCount) return empty('Özellik listesi boş', 'Akademi bu kurum için özellik listesi döndürmedi.');
    return list;
  }

  function flagRow(item, byKey) {
    const row = h('div', 'ak-set-flag');
    const toggle = h('input', 'ak-set-switch');
    toggle.type = 'checkbox';
    toggle.checked = Boolean(item.enabled);
    toggle.setAttribute('aria-label', featureName(item.key));
    if (!item.editable) {
      toggle.disabled = true;
      toggle.title = 'Bu bayrak platform tarafından kilitli.';
    } else if (!canPatch) {
      toggle.disabled = true;
      toggle.title = 'Bu işlem için yetkiniz yok (yöneticinizden izin isteyin).';
    }
    toggle.addEventListener('change', () => change(item, toggle));

    const text = h('div', 'ak-set-flag-text');
    const title = h('div', 'ak-set-flag-title', featureName(item.key));
    title.append(badge(item.enabled ? 'Açık' : 'Kapalı', item.enabled ? 'good' : 'dim'));
    if (!item.editable) title.append(badge('Platform kilitli', 'warn'));
    text.append(title, h('div', 'ak-muted', FEATURES[item.key]?.[1] || ''));
    const needs = (REQUIRES[item.key] || []).filter((other) => !byKey.get(other)?.enabled);
    if (needs.length) {
      text.append(h('div', 'ak-set-flag-note',
        `Çalışması için “${needs.map(featureName).join('”, “')}” de açık olmalı.`));
    }
    // Teknik ayrıntı (bayrak anahtarı, yapılandırma) ekranda yazmaz; destek için
    // satırın ipucunda durur.
    const config = item.config && typeof item.config === 'object' ? Object.entries(item.config) : [];
    row.title = [item.key, ...config.map(([name, value]) => `${name}: ${typeof value === 'object'
      ? JSON.stringify(value) : value}`)].join(' · ');
    row.append(toggle, text);
    return row;
  }

  async function change(item, toggle) {
    const next = toggle.checked;
    if (!next) {
      const dependents = Object.entries(REQUIRES)
        .filter(([key, needs]) => needs.includes(item.key) && items.find((other) => other.key === key)?.enabled)
        .map(([key]) => featureName(key));
      const ok = await confirmAction(app, {
        title: `“${featureName(item.key)}” kapatılsın mı?`,
        description: (FEATURE_OFF_NOTE[item.key] || 'Bu özelliğin ekranları öğrenci, veli ve yönetici uygulamalarında '
          + 'hemen kapanır; kayıtlı veri silinmez, yeniden açınca geri gelir.')
          + `${dependents.length ? ` Buna bağlı “${dependents.join('”, “')}” da çalışmaz.` : ''}`,
        confirmLabel: 'Kapat',
        danger: true,
      });
      if (!ok) { toggle.checked = true; return; }
    }
    toggle.disabled = true;
    const updated = await runAction(app, () => app.api.patch(`/v1/admin/features/${item.key}`,
      { enabled: next }), { success: `${featureName(item.key)} ${next ? 'açıldı' : 'kapatıldı'}.` });
    app.api.invalidate('/v1/admin/features');
    if (updated) {
      items = items.map((other) => (other.key === item.key ? { ...other, ...updated } : other));
      app.refreshFeatures?.();        // menü: kapanan ekran kalkar, açılan gelir
    } else {
      toggle.checked = !next;
    }
    view.node.replaceChildren(paint());
  }

  return { node, reload: () => view.reload() };
}

// ----------------------------------------------------------- anons şablonları

const SAMPLE = { '{ad}': 'Ayşe', '{soyad}': 'Yılmaz', '{sinif}': '12-A' };
const sampleText = (text) => String(text || '').replace(/\{(ad|soyad|sinif)\}/g, (token) => SAMPLE[token]);

function paTemplatesTab(app, { goTab }) {
  const node = h('div', 'ak-stack');
  const add = actionButton(app, 'admin_createPaTemplate', 'Yeni şablon', { variant: 'primary',
    onClick: () => create() });
  node.append(toolbar(h('span', 'ak-muted', 'Panel bölümündeki “Öğrenci çağır” bu şablonlardan seçer. '
    + 'Yer tutucular: {ad}, {soyad}, {sinif}.'), h('span', 'kit-spacer'), add));

  const list = staticList({
    load: async () => {
      try {
        return await app.api.getCached('/v1/admin/pa/templates');
      } catch (failure) {
        if (featureOff(failure)) return { items: [], off: true };
        throw failure;
      }
    },
    onLoaded: (_rows, data) => {
      list.table.update({ empty: data?.off ? empty('Anons özelliği kapalı',
        'Anons özelliği kapalıyken şablonlar kullanılamaz.',
        [button('Özelliklere git', { onClick: () => goTab('features') })])
        : empty('Şablon yok', 'Sık yapılan anonsları şablon olarak ekleyin.') });
    },
    columns: [
      { key: 'title', label: 'Başlık', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-inline');
        cell.append(h('b', '', row.title));
        if (row.builtIn) cell.append(badge('Hazır', 'info'));
        return cell;
      } },
      { key: 'text', label: 'Metin', width: 'minmax(0, 2.4fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.text));
        if (/\{(ad|soyad|sinif)\}/.test(row.text)) cell.append(h('small', '', `Örnek: ${sampleText(row.text)}`));
        return cell;
      } },
      { key: 'actions', label: '', width: '110px', cell: (row) => {
        const actions = h('div', 'ak-row-actions');
        if (row.builtIn) {
          actions.append(h('span', 'ak-muted', 'Silinemez'));
        } else {
          actions.append(actionButton(app, 'admin_deletePaTemplate', 'Sil', { variant: 'ghost',
            onClick: () => remove(row) }));
        }
        return actions;
      } },
    ],
    empty: { title: 'Şablon yok', text: 'Sık yapılan anonsları şablon olarak ekleyin.' },
  });
  node.append(list.node);

  async function create() {
    const preview = h('div', 'ak-muted ak-set-sample');
    const saved = await formDialog(app, {
      title: 'Yeni anons şablonu',
      value: { title: '', text: '' },
      fields: [
        { key: 'title', label: 'Başlık', required: true, maxLength: 60, wide: true,
          placeholder: 'Müdür odasına çağrı' },
        { key: 'text', label: 'Anons metni', type: 'textarea', required: true, maxLength: 160, wide: true,
          placeholder: '{ad} {soyad}, {sinif} sınıfından müdür odasına lütfen.',
          hint: 'En çok 160 karakter. {ad}, {soyad}, {sinif} çağrı anında doldurulur.' },
        { key: 'sample', label: 'Örnek', type: 'custom', render: () => ({ node: preview,
          update: (draft) => { preview.textContent = draft.text ? sampleText(draft.text) : '—'; } }) },
      ],
      submitLabel: 'Şablonu ekle',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/pa/templates', {
        title: String(draft.title || '').trim(), text: String(draft.text || '').trim() }, { key }),
    });
    if (saved) {
      app.toast('Şablon eklendi.', 'good');
      app.api.invalidate('/v1/admin/pa/templates');
      list.reload();
    }
  }

  async function remove(row) {
    const ok = await confirmAction(app, { title: `“${row.title}” şablonu silinsin mi?`,
      description: 'Geçmiş anons kayıtları etkilenmez.', confirmLabel: 'Sil', danger: true });
    if (!ok) return;
    const done = await runAction(app, () => app.api.del(`/v1/admin/pa/templates/${row.id}`),
      { success: 'Şablon silindi.' });
    if (done !== undefined) {
      app.api.invalidate('/v1/admin/pa/templates');
      list.reload();
    }
  }

  return { node, reload: () => list.reload() };
}
