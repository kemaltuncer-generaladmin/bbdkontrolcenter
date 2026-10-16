// BBD Akademi — İletişim › SMS (sms-kale §14 madde 58 + kurum SMS'i madde 88).
//
// 15.10.2026 sadeleştirme: eskiden Ayarlar → Kurum ayarları → SMS sekmesiydi;
// SMS bir İLETİŞİM işidir (duyurunun SMS'i, ödeme hatırlatması, deneme sonucu,
// veli giriş kodu), bu yüzden İletişim grubunda kendi ekranıdır. Gövde aynıdır
// (`settings-sms.js` → `smsTab`: sağlık, kurum SMS bütçesi ve ayarları, kurum SMS
// kuyruğu, OTP günlüğü, telefon sayacı sıfırlama). Eski derin bağlantı
// `app.go('settings', {tab: 'sms'})` buraya yönlenir (settings.js).

import { h } from '../../../ui-kit/kit.js';
import { sectionHead } from '../common.js';
import { smsTab } from '../admin/settings-sms.js';

export function section(app) {
  const node = h('div', 'ak-stack');
  node.append(sectionHead('SMS', {
    subtitle: 'SMS sağlığı ve Netgsm paketi, kurum SMS bütçesi ve ayarları (imza, ödeme hatırlatması, '
      + 'deneme sonucu), kurum SMS kuyruğu, giriş kodu günlüğü ve telefon sayacı.' }));
  const body = smsTab(app);
  node.append(body.node);
  let started = false;
  return {
    node,
    shown() {
      if (started) return;
      started = true;
      body.reload();
    },
    destroy() { body.destroy?.(); },
  };
}
