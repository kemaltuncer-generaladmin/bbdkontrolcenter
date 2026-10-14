// BBD Akademi — Erken uyarı (F-A22) etiketleri ve küçük ortak parçalar.
//
// Erken uyarı bölümü (insights.js + -analysis/-settings) ile öğrenci
// çekmecesinin Koçluk sekmesi (students-coaching.js) aynı kural, önem ve durum
// adlarını kullanır; tek yerde dursun diye buradadır.
//
// `early_warning` kiracı bayrağıdır: kapalıyken uyarı uçları
// `403 feature_disabled` döner. Ekran boş tablo değil SEBEP gösterir ve
// yetkisi olana Ayarlar → Özellikler bağlantısı verir.

import { button, h } from '../../ui-kit/kit.js';
import { badge } from '../../ui-kit/layout.js';
import { empty, net, num } from './common.js';

/** Kural → [ad, eşik birimi, eşik açıklaması]. Sıra ayarlar tablosunun sırasıdır. */
export const RULES = {
  no_activity: ['Çalışma kaydı yok', 'gün', 'Öğrenci bu kadar gün soru/çalışma kaydı girmezse.'],
  streak_broken: ['Seri koptu', 'gün', 'En az bu uzunluktaki çalışma serisi koparsa.'],
  low_plan_adherence: ['Düşük plan uyumu', '%', 'Haftalık plan uyumu bu yüzdenin altında kalırsa.'],
  exam_net_drop: ['Deneme neti düşüyor', 'net', 'Toplam net art arda denemelerde bu farktan fazla düşerse.'],
  absence: ['Devamsızlık', 'gün', 'Pencere içindeki devamsızlık bu sayıya ulaşırsa.'],
  homework_overdue: ['Geciken ödev', 'ödev', 'Geciken ödev sayısı bu sayıya ulaşırsa.'],
  low_motivation: ['Düşük motivasyon', 'puan',
    'Haftalık öz değerlendirmede motivasyon bu puan ya da altındaysa (öz değerlendirme özelliği açık olmalı).'],
};

export const SEVERITY = { high: ['Yüksek', 'bad'], medium: ['Orta', 'warn'], low: ['Düşük', 'info'] };
export const SEVERITY_ORDER = ['high', 'medium', 'low'];
export const WARNING_STATUS = { open: ['Açık', 'bad'], in_progress: ['İşlemde', 'warn'],
  resolved: ['Çözüldü', 'good'] };
export const ACTION_TYPES = { note: ['Not eklendi', 'info'], meeting: ['Görüşme planlandı', 'info'],
  notify_guardians: ['Veliye bildirim', 'info'], resolve: ['Çözüldü', 'good'],
  reopen: ['Yeniden açıldı', 'warn'], retriggered: ['Kural yeniden tetiklendi', 'bad'] };
export const RESOLVE_REASONS = { contacted: 'Öğrenci / veliyle görüşüldü', improved: 'Durum düzeldi',
  false_alarm: 'Yanlış alarm', other: 'Diğer' };
export const MISTAKE_REASONS = { knowledge_gap: 'Bilgi eksiği', misconception: 'Kavram yanılgısı',
  calculation_error: 'İşlem hatası', careless: 'Dikkatsizlik', misread_question: 'Soruyu yanlış okuma',
  time_shortage: 'Süre yetmedi', guess: 'Tahmin', unspecified: 'Belirtilmemiş' };

export const ruleName = (rule) => RULES[rule]?.[0] || rule || '—';

/** Kuralın ölçtüğü değer birimiyle: "4 gün", "%38", "-6,25 net". */
export function ruleValue(rule, value) {
  if (value === null || value === undefined) return '—';
  const unit = RULES[rule]?.[1] || '';
  if (unit === '%') return `%${num(value)}`;
  if (unit === 'net') return `${net(value)} net`;
  return `${num(value)} ${unit}`.trim();
}

export function severityBadge(severity) {
  const [text, tone] = SEVERITY[severity] || [severity || '—', 'dim'];
  return badge(text, tone);
}

export function statusBadgeOf(status) {
  const [text, tone] = WARNING_STATUS[status] || [status || '—', 'dim'];
  return badge(text, tone);
}

/** Öğrenci hücresi: ad + okul kaydı · sınıf. */
export function studentCell(student) {
  const node = h('div', 'ak-cell-stack');
  node.append(h('b', '', student?.displayName || '—'),
    h('small', '', [student?.externalRef, student?.classGroup?.name].filter(Boolean).join(' · ')));
  return node;
}

/**
 * `feature_disabled` → açıklamalı boş durum (yoksa null). Bayrağı açma
 * Ayarlar → Özellikler'dedir; düğme yalnız oraya erişebilene çizilir.
 */
export function featureNotice(app, failure, { title = 'Erken uyarı kurumda kapalı',
  text = 'Kurallı risk listesi kurum ayarlarından açılınca uyarılar burada görünür. '
    + 'Eşikleri şimdiden düzenleyebilirsiniz; bayrak kapalıyken uyarı üretilmez.' } = {}) {
  if (failure?.code !== 'feature_disabled') return null;
  const actions = app.can('admin_listFeatures')
    ? [button('Özelliklere git', { variant: 'ghost', onClick: () => app.go('settings', { tab: 'features' }) })]
    : [];
  return empty(title, text, actions);
}
