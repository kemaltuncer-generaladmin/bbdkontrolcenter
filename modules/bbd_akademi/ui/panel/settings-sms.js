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

import { h } from '../../ui-kit/kit.js';
import { alertBox, kpiRow } from '../../ui-kit/layout.js';
import {
  actionButton, fmtDateTime, formDialog, loader, pagedList, statusBadge, toolbar,
} from './common.js';

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
    if ((data.sent ?? 0) > 0 && (data.otpChannel ?? 0) === 0) {
      parts.push(alertBox('Kodlar Netgsm OTP hattı yerine olağan (toplu) hattan gidiyor — bu hat yoğunlukta '
        + 'kuyruklanabilir. Netgsm hesabına "OTP SMS paketi" tanımlatın; tanımlanınca kendiliğinden OTP hattına '
        + 'geçilir.', 'warn'));
    }
    return parts;
  }, { rows: 1, columns: 7 });
  node.append(health.node);

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
    reload: () => { health.reload(); list.reload(); },
    destroy() { list.destroy(); },
  };
}
