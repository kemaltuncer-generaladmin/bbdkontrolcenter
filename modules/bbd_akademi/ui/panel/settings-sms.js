// BBD Akademi — Ayarlar › SMS (sms-kale, Akademi README §14 madde 58).
//
// Üç parça, hepsi Akademi `/v1/admin/sms/*` uçlarından (A13; vekil izni
// `integrations.manage` → `bbd_akademi.settings`):
//  · Sağlık: son 1 saatte gönderilen / teslim / başarısız / geciken SMS, teslim
//    oranı, ortalama ve en uzun teslim süresi, Netgsm SMS paketi.
//  · Günlük: kurumun OTP SMS kaydı (son 7 gün, imleçli) — telefon MASKELİ,
//    kod ve metin yok; hat (OTP hattı / olağan hat) ve teslim durumu.
//  · Sayaç sıfırlama: "kod gelmedi" diyen velinin telefonundaki saatlik (5) ve
//    günlük (10) kod sayacını Akademi'de ve KM geçidinde sıfırlar; gerekçe
//    zorunlu, Akademi denetim kaydına yazılır. Kurumun günlük SMS bütçesi
//    değişmez.
//  · Kurum SMS'i (madde 88, V8 / İ2 / İ6): Kontrol Merkezi'nin günlük SMS
//    bütçesi (son 24 saatte kullanılan / kalan, telefon başına günlük sınır) ve
//    amaç başına son 24 saat (duyuru, ödeme hatırlatması, deneme sonucu);
//    kurum SMS ayarları (imza, ödeme hatırlatması ve deneme sonucu SMS'i —
//    varsayılan kapalı; `admin_patchSettings`); kurum SMS kuyruğu
//    (`admin_listSmsOutbox`: veli, öğrenci, maskeli telefon, durum, neden).
//    Bütçenin kendisi Mobil Geçit modül ayarıdır (`announcement_sms`).

import { h } from '../../ui-kit/kit.js';
import { alertBox, kpiRow } from '../../ui-kit/layout.js';
import { chipRow } from '../../ui-kit/layout.js';
import {
  actionButton, fmtDateTime, formDialog, loader, num, pagedList, statusBadge, toolbar,
} from './common.js';
import { settingsTab } from './settings.js';

const USAGE = { announcement: 'Duyuru', payment_reminder: 'Ödeme hatırlatması', exam_result: 'Deneme sonucu' };
const OUTBOX_STATUS = { queued: ['Bekliyor', 'info'], sent: ['Gönderildi', 'good'], failed: ['Başarısız', 'bad'],
  skipped: ['Atlandı', 'dim'] };
const AUDIENCE = [
  { value: 'non_app', label: 'Yalnız uygulamayı kullanmayan veliler' },
  { value: 'all', label: 'Bütün veliler' },
];

/** İmza alanı: boş = kurum kısa adı (null), "yok" = imza eklenmez (""). */
export const signatureFrom = (value) => (value === '' ? 'yok' : value ?? '');
export const signatureTo = (text) => {
  const value = String(text ?? '').trim();
  if (value.toLocaleLowerCase('tr-TR') === 'yok') return '';
  return value || null;
};

/** Kurum SMS ayarları (README §14 madde 88): `TenantSettings.sms`. */
export const SMS_SETTINGS_GROUPS = [
  { title: 'Kurum SMS\'i', hint: 'Duyuru, ödeme hatırlatması ve deneme sonucu SMS\'leri Kontrol Merkezi üzerinden '
      + 'kantinin Netgsm hattıyla gider; günlük bütçe ortaktır. Duyuru SMS\'i hemen, otomatik SMS\'ler (ödeme '
      + 'hatırlatması, deneme sonucu) yalnız gündüz 10:00–20:00 arasında gider.', fields: [
    { key: 'signature', path: ['sms', 'signature'], label: 'SMS imzası', maxLength: 30, from: signatureFrom,
      to: signatureTo, placeholder: 'Boş: kurum kısa adı',
      hint: 'Metnin sonuna “ - imza” olarak eklenir; veli SMS\'in kimden geldiğini buradan anlar. İmza istemiyorsanız “yok” yazın.' },
  ] },
  { title: 'Ödeme hatırlatması SMS\'i', className: 'ak-money-finance',
    hint: 'Yalnız kurum carisi (kantin ayrı). Vade hatırlatması her gün 09:00\'da; gecikme hatırlatması 09:00 işi ve '
      + '“Gecikenlere hatırlat” ile — öğrenci başına günde en çok bir SMS, yalnız ödeme yetkili veliye.', fields: [
    { key: 'payEnabled', path: ['sms', 'paymentReminders', 'enabled'], type: 'checkbox', to: Boolean,
      label: 'Ödeme hatırlatmasını SMS ile de gönder' },
    { key: 'payAudience', path: ['sms', 'paymentReminders', 'audience'], label: 'Kime', type: 'select',
      options: AUDIENCE, from: (value) => value || 'non_app' },
    { key: 'payDue', path: ['sms', 'paymentReminders', 'dueSoon'], type: 'checkbox', to: Boolean,
      from: (value) => value ?? true, label: 'Vadesi yaklaşan taksit' },
    { key: 'payOverdue', path: ['sms', 'paymentReminders', 'overdue'], type: 'checkbox', to: Boolean,
      from: (value) => value ?? true, label: 'Geciken ödeme' },
  ] },
  { title: 'Deneme sonucu SMS\'i', hint: 'Deneme bildirimle yayımlanınca veliye kısa özet: toplam net ve kurum sırası.',
    fields: [
      { key: 'examEnabled', path: ['sms', 'examResults', 'enabled'], type: 'checkbox', to: Boolean,
        label: 'Deneme sonucunu veliye SMS ile de gönder' },
      { key: 'examAudience', path: ['sms', 'examResults', 'audience'], label: 'Kime', type: 'select',
        options: AUDIENCE, from: (value) => value || 'non_app' },
    ] },
];

/** Bütçe kartı satırları: KM bütçesi (eski KM'de yok) + amaç başına son 24 saat. */
export function budgetRows(data) {
  const budget = data?.textBudget;
  const kpis = budget ? [
    { label: 'Günlük SMS bütçesi (24 sa)', value: `${num(budget.used)} / ${num(budget.dailyBudget)}`,
      tone: budget.remaining <= 0 ? 'bad' : budget.remaining < budget.dailyBudget * 0.1 ? 'warn' : '' },
    { label: 'Kalan', value: num(budget.remaining), tone: budget.remaining <= 0 ? 'bad' : '' },
    { label: 'Numara başına günde', value: num(budget.perPhonePerDay) },
  ] : [];
  for (const row of data?.usage || []) {
    const extra = [row.failed ? `${num(row.failed)} başarısız` : '', row.waiting ? `${num(row.waiting)} bekliyor` : '']
      .filter(Boolean).join(' · ');
    kpis.push({ label: `${USAGE[row.purpose] || row.purpose} (24 sa)`,
      value: `${num(row.sent)} veli · ${num(row.segments)} SMS${extra ? ` · ${extra}` : ''}`,
      tone: row.failed ? 'warn' : '' });
  }
  return { kpis, enabled: budget ? budget.enabled : null, known: Boolean(budget) };
}

const DELIVERY = {
  delivered: ['Teslim edildi', 'good'],
  sending: ['Gönderiliyor', 'info'],
  delayed: ['Gecikti', 'warn'],
  failed: ['Teslim edilemedi', 'bad'],
  unknown: ['Bilinmiyor', 'dim'],
};
const CHANNEL = { otp: 'OTP hattı', normal: 'Olağan hat' };
const PURPOSE = { login: 'Giriş', step_up: 'Onay', switch: 'Rol geçişi' };
const PHONE = /^[+\d][\d\s()-]{9,19}$/;

const seconds = (value) => (value === null || value === undefined ? '—' : `${String(value).replace('.', ',')} sn`);

export function smsTab(app) {
  const node = h('div', 'ak-stack');

  const reset = actionButton(app, 'admin_resetSmsLimits', 'Telefon sayacını sıfırla', {
    onClick: () => resetDialog(),
  });
  node.append(toolbar(h('span', 'kit-spacer'), reset));

  const health = loader(() => app.api.get('/v1/admin/sms/health'), (data) => {
    const parts = [];
    const rate = data.deliveryRate === null || data.deliveryRate === undefined
      ? '—' : `%${Math.round(Number(data.deliveryRate) * 100)}`;
    parts.push(kpiRow([
      { label: 'Gönderilen (1 sa)', value: data.sent ?? 0 },
      { label: 'Teslim', value: data.delivered ?? 0, tone: 'good' },
      { label: 'Geciken (3 dk+)', value: data.late ?? 0, tone: data.late ? 'warn' : '' },
      { label: 'Başarısız', value: data.failed ?? 0, tone: data.failed ? 'bad' : '' },
      { label: 'Teslim oranı', value: rate },
      { label: 'Ort. / en uzun', value: `${seconds(data.avgDeliverySeconds)} / ${seconds(data.maxDeliverySeconds)}` },
      { label: 'Netgsm SMS paketi', value: data.smsRemaining ?? '—', tone: data.smsLow ? 'bad' : '' },
    ]));
    if (!data.providerOk) {
      parts.push(alertBox('SMS sağlayıcısına (KM geçidi → kantin → Netgsm) şu an ulaşılamıyor; kodlar '
        + 'gönderilemeyebilir. Kantin ve KM çalışıyor mu kontrol edin.', 'bad'));
    } else if (data.smsLow) {
      parts.push(alertBox('Netgsm SMS paketi azaldı. Netgsm panelinden paket yükleyin; paket biterse '
        + 'giriş kodları gitmez.', 'warn'));
    }
    return parts;
  }, { rows: 1, columns: 7 });
  node.append(health.node);

  const budget = loader(() => app.api.get('/v1/admin/sms/health'), (data) => {
    const rows = budgetRows(data);
    const parts = [h('h3', 'kit-card-title', 'Kurum SMS\'i: bütçe ve son 24 saat'), kpiRow(rows.kpis)];
    if (!rows.known) {
      parts.push(alertBox('Kontrol Merkezi bu sürümde günlük SMS bütçesini bildirmiyor; bütçe dolarsa SMS\'ler '
        + 'kuyrukta bekler ve bütçe açıldıkça gönderilir.', 'info'));
    } else if (rows.enabled === false) {
      parts.push(alertBox('Kurum SMS\'i Kontrol Merkezi\'nde kapalı (Mobil Geçit › announcement_sms). Duyuru ve '
        + 'hatırlatma SMS\'leri bekler.', 'warn'));
    } else if (data.textBudget.remaining <= 0) {
      parts.push(alertBox('Günlük SMS bütçesi doldu; bekleyen SMS\'ler bütçe açıldıkça gönderilir. Bütçe Mobil '
        + 'Geçit modül ayarındadır (announcement_sms.daily_budget).', 'warn'));
    }
    return parts;
  }, { rows: 1, columns: 6 });
  node.append(budget.node);

  const smsSettings = settingsTab(app, {
    opId: 'admin_patchSettings',
    groups: SMS_SETTINGS_GROUPS,
    read: () => app.api.getCached('/v1/admin/settings'),
    write: async (body) => {
      const next = await app.api.patch('/v1/admin/settings', body);
      app.api.invalidate('/v1/admin/settings');
      return next;
    },
    saved: 'SMS ayarları kaydedildi.',
  });
  node.append(smsSettings.node);

  let purpose = '';
  const chips = chipRow([
    { key: 'payment_reminder', label: 'Ödeme hatırlatması' }, { key: 'exam_result', label: 'Deneme sonucu' },
  ], null, (key) => { purpose = key || ''; outbox.reload(); });
  const outbox = pagedList({
    app,
    path: '/v1/admin/sms/outbox',
    limit: 50,
    query: () => ({ purpose: purpose || undefined }),
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'purpose', label: 'Amaç', width: '150px', cell: (row) => USAGE[row.purpose] || row.purpose },
      { key: 'guardianName', label: 'Veli', width: 'minmax(0, 1fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('span', '', row.guardianName || '—'), h('small', '', row.studentName || ''));
        return cell;
      } },
      { key: 'phoneMasked', label: 'Telefon', width: '150px', cell: (row) => row.phoneMasked || '—' },
      { key: 'status', label: 'Durum', width: '110px', cell: (row) => statusBadge(OUTBOX_STATUS, row.status) },
      { key: 'reason', label: 'Neden', width: 'minmax(0, 1fr)', cell: (row) => row.reasonLabel
        || (row.sentAt ? fmtDateTime(row.sentAt) : '—') },
    ],
    empty: { title: 'Kurum SMS\'i yok', text: 'Ödeme hatırlatması ve deneme sonucu SMS\'leri (açıksa) burada görünür.' },
  });
  node.append(h('h3', 'kit-card-title', 'Kurum SMS kuyruğu (son 90 gün)'), chips.node, outbox.node);

  const list = pagedList({
    app,
    path: '/v1/admin/sms/otp-log',
    limit: 50,
    query: () => ({}),
    columns: [
      { key: 'createdAt', label: 'Zaman', width: '130px', cell: (row) => fmtDateTime(row.createdAt) },
      { key: 'phoneMasked', label: 'Telefon', width: 'minmax(0, 1fr)', cell: (row) => row.phoneMasked || '—' },
      { key: 'purpose', label: 'Amaç', width: '100px', cell: (row) => PURPOSE[row.purpose] || row.purpose },
      { key: 'channel', label: 'Hat', width: '110px', cell: (row) => CHANNEL[row.channel] || '—' },
      { key: 'delivery', label: 'Teslim', width: '150px', cell: (row) => statusBadge(DELIVERY, row.delivery) },
      { key: 'deliverySeconds', label: 'Süre', align: 'num', width: '80px',
        cell: (row) => seconds(row.deliverySeconds) },
      { key: 'used', label: 'Kullanıldı', width: '90px', cell: (row) => (row.used ? 'Evet' : '—') },
    ],
    empty: { title: 'Son 7 günde SMS kodu yok', text: 'Veli ve personel girişlerinde gönderilen kodlar burada görünür.' },
  });
  node.append(list.node);

  async function resetDialog() {
    const done = await formDialog(app, {
      title: 'Telefonun kod sayacını sıfırla',
      description: 'Bu telefona saatte 5, günde 10 kod gönderilebilir. Sıfırlama sayaçları ve 60 sn bekleme '
        + 'süresini Akademi\'de ve Kontrol Merkezi\'nde temizler; kurumun günlük SMS bütçesi değişmez. '
        + 'İşlem denetim kaydına gerekçesiyle yazılır.',
      value: { phone: '', reason: '' },
      fields: [
        { key: 'phone', label: 'Telefon', required: true, maxLength: 20, placeholder: '0532 123 45 67',
          validate: (value) => (PHONE.test(String(value || '').trim()) ? null : 'Telefon numarası geçersiz.') },
        { key: 'reason', label: 'Gerekçe', required: true, maxLength: 200, wide: true,
          placeholder: 'Veli kod alamadığını bildirdi',
          validate: (value) => (String(value || '').trim().length >= 3 ? null : 'Gerekçe en az 3 karakter.') },
      ],
      submitLabel: 'Sayacı sıfırla',
      onSubmit: (draft, { key }) => app.api.post('/v1/admin/sms/limits/reset', {
        phone: String(draft.phone || '').trim(),
        reason: String(draft.reason || '').trim(),
      }, { key }),
    });
    if (!done || typeof done !== 'object') return;
    app.toast?.(done.kmReset
      ? 'Sayaç Akademi\'de ve Kontrol Merkezi\'nde sıfırlandı.'
      : 'Sayaç Akademi\'de sıfırlandı (Kontrol Merkezi geçidi bu sürümde sıfırlama desteklemiyor).', 'good');
    health.reload();
    list.reload();
  }

  return {
    node,
    reload: () => { health.reload(); budget.reload(); smsSettings.reload(); outbox.reload(); list.reload(); },
    destroy() { list.destroy(); outbox.destroy(); smsSettings.destroy(); },
  };
}
