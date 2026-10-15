// BBD Akademi — Raporlar › Rapor merkezi (15.10.2026 sadeleştirme).
//
// Raporlar eskiden ekranların içinde dağınıktı (yoklama → Raporlar sekmesi,
// ders oturumları → Aylık ders dökümü, finans → Aylık rapor, deneme → Analiz,
// okuma → Rapor …) ve "rapor nerede?" sorusu ekran ekran aranıyordu. Bu ekran
// hepsinin TEK kapısıdır: her rapor bir kart, "Aç" kartın ekranına o sekmeyle
// gider (`app.go(ekran, {tab})`). Rapor KOPYALANMAZ — gövde, PDF/CSV çıktısı ve
// yetki kapısı raporun kendi ekranındadır; burada yalnız bağlantı durur.
// Görünmeyen ekranın (yetki yok / özellik kurumda kapalı) kartı çizilmez.

import { button, h, loadStyles } from '../../ui-kit/kit.js';
import { emptyState } from '../../ui-kit/layout.js';
import { sectionHead } from './common.js';
import { navIcon } from './nav-icons.js';

/** Rapor kartları: grup → [başlık, açıklama, ekran, ek veri]. Sıra ekrandaki sıradır. */
export const REPORTS = [
  { group: 'Yoklama ve dersler', icon: 'check', tone: 'green', items: [
    ['Yoklama raporları', 'Günlük yoklama tahtası, aylık yoklama çizelgesi ve devamsızlık listesi (PDF).',
      'attendance', { tab: 'reports' }],
    ['Aylık ders dökümü', 'Öğretmen başına gerçekleşen ders saati (puantaj); yerine girilen ders girene yazılır, CSV.',
      'sessions', { tab: 'load' }],
    ['Öğretmen haftası', 'Öğretmenin haftalık dersleri, iptal ve telafiler.', 'sessions', { tab: 'teacher' }],
  ] },
  { group: 'Ölçme ve gelişim', icon: 'chart', tone: 'cyan', items: [
    ['Deneme analizi ve karşılaştırma', 'Deneme sonuçlarının sınıf ve dönem karşılaştırması, net dağılımı.',
      'exams', { tab: 'analytics' }],
    ['Konu ve hata analizi', 'Öğrencilerin en çok zorlandığı konular ve hata türleri.', 'insights', { tab: 'analysis' }],
    ['Plan uyum tablosu', 'Öğrenci × hafta çalışma planı uyumu (%).', 'plan', { tab: 'adherence' }],
    ['Okuma raporu', 'Okuma listelerinin ilerlemesi ve okuma ligi.', 'reading', { tab: 'report' }],
  ] },
  { group: 'Koçluk', icon: 'compass', tone: 'violet', items: [
    ['Koç performansı ve dışa aktarım', 'Koç yük tablosu, performans metrikleri; dönem raporları ve haftalık '
      + 'programların dışa aktarımı.', 'coachCoaches', null],
  ] },
  { group: 'Finans', icon: 'wallet', tone: 'orange', items: [
    ['Aylık finans raporu', 'Ay ay tahakkuk, tahsilat ve açık bakiye (kurum carisi; kantin ayrı).', 'finance',
      { tab: 'monthly' }],
    ['Gecikenler', 'Vadesi geçmiş taksitler ve öğrenciler.', 'finance', { tab: 'overdue' }],
  ] },
  { group: 'Kurum', icon: 'gear', tone: 'slate', items: [
    ['Denetim kaydı', 'Kim, ne zaman, neyi değiştirdi — Akademi denetim kaydı.', 'settings', { tab: 'audit' }],
  ] },
];

export function section(app) {
  loadStyles(import.meta.url, 'reports.css');
  const node = h('div', 'ak-stack ak-rep');
  node.append(sectionHead('Rapor merkezi', {
    subtitle: 'Bütün raporlar tek yerde. Kart, raporun kendi ekranını açar; PDF ve CSV çıktısı oradadır.' }));
  const body = h('div', 'ak-stack');
  node.append(body);

  function paint() {
    body.replaceChildren();
    for (const group of REPORTS) {
      const items = group.items.filter(([, , target, payload]) => app.canOpen(target, payload));
      if (!items.length) continue;
      const block = h('section', `ak-rep-group ak-tone-${group.tone}`);
      const titleId = `ak-rep-${group.icon}`;
      const head = h('h3', 'ak-rep-group-title');
      head.id = titleId;
      const icon = h('span', 'ak-rep-icon');
      icon.append(navIcon(group.icon));
      head.append(icon, document.createTextNode(group.group));
      block.setAttribute('aria-labelledby', titleId);
      const grid = h('div', 'ak-rep-grid');
      for (const [title, text, target, payload] of items) {
        const card = h('div', 'ak-rep-card');
        const open = button('Aç', { onClick: () => app.go(target, payload) });
        open.setAttribute('aria-label', `${title} — aç`);
        card.append(h('div', 'ak-rep-card-title', title), h('p', 'ak-rep-card-text', text), open);
        grid.append(card);
      }
      block.append(head, grid);
      body.append(block);
    }
    if (!body.children.length) {
      body.append(emptyState({ title: 'Görüntülenecek rapor yok',
        text: 'Rapor ekranlarına yetkiniz yok ya da ilgili özellikler kurumda kapalı.' }));
    }
  }

  return { node, shown() { paint(); } };
}
