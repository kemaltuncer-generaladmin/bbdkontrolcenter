// BBD Akademi — Kantin ve Finans → Kantin.
//
// Kantinin GÜNLÜK işleri (bakiye, tahsilat, veli kontrolleri, satış, rapor)
// Kontrol Merkezi'nin kendi kantin ekranlarındadır; Akademi'de kantinin yalnız
// KURUM KURALLARI durur (limit üst sınırı, görünürlük ve bildirim
// varsayılanları). Bu ekran ikisini tek yerde toplar: kurallar (Ayarlar'dan
// taşındı; gövdesi `settings.js` → `canteenTab`) ve KM ekranlarına geçiş.
//
// İKİ PARA AYRI (A12): kantin parası kurum carisiyle hiçbir toplamda buluşmaz;
// ekran turuncu çizgiyle ayrılır. Kantin özelliği kurumda kapalıysa ekran
// menüde hiç görünmez (`sections.js` → `feature: 'canteen'`).

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { card } from '../../ui-kit/layout.js';
import { sectionHead, toolbar } from './common.js';
import { canteenTab } from './settings.js';

/** KM'nin kantin ekranları (modül kimliği → düğme). Yalnız geçiş; veri burada yok. */
const KM_SCREENS = [
  ['bbd_students', 'Öğrenci bakiyesi, tahsilat ve veli kontrolleri', { tab: 'canteen' }],
  ['bbd_canteen_reports', 'Kantin raporları', null],
  ['bbd_canteen_products', 'Kantin ürünleri', null],
];

export function section(app) {
  loadStyles(import.meta.url, 'settings.css');   // kurallar formu Ayarlar'ın stilini kullanır
  const node = h('div', 'ak-stack');
  node.append(sectionHead('Kantin', {
    subtitle: 'Kantinin kurum kuralları. Günlük kantin işleri Kontrol Merkezi\'nin kantin ekranlarındadır.' }));

  const links = h('div', 'kit-card-body ak-stack');
  links.append(h('div', 'ak-muted', 'Bakiye yükleme, borç tahsilatı, harcama limiti ve satış gibi günlük işler '
    + 'için Kontrol Merkezi ekranını açın. Kantin parası kurum carisinden ayrı tutulur.'),
  toolbar(...KM_SCREENS.map(([id, label, payload]) => (
    // Öğrenci yönetimi BBD Akademi'de: öğrencinin bakiyesi, tahsilatı ve veli
    // kontrolleri tek kartındadır (Öğrenciler → öğrenci → Kayıt ve veliler).
    id === 'bbd_students' && app.capability?.('bbd_students.manager')
      ? button('Öğrenciler (bakiye, tahsilat, veli kontrolleri kartta)', { variant: 'ghost',
        onClick: () => app.go('students', { view: 'km' }) })
      : button(label, { variant: 'ghost', onClick: () => app.openPanel(id, payload) })))));
  const linkCard = card('Günlük kantin işleri', links);
  linkCard.classList.add('ak-money-canteen');
  node.append(linkCard);

  // Kurallar: okuma yetkisi yoksa (kurum ayarları izni) kart hiç çizilmez.
  let rules = null;
  if (app.can('admin_getCanteenSettings')) {
    rules = canteenTab(app, { goTab: (tab) => app.go('settings', { tab }) });
    node.append(card('Kantin kuralları', rules.node));
  }

  let started = false;
  return {
    node,
    shown() {
      if (!started) {
        started = true;
        rules?.reload();
      }
    },
    destroy() { rules?.destroy?.(); },
  };
}
