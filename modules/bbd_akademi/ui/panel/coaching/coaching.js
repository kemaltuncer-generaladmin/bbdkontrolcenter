// BBD Akademi — Koçluk yönetimi (KM-4; K1 koçluk bloğu + F-K17 görüşmeler + F-K10 video).
//
// ÖĞRETMEN = KOÇ (kullanıcı kararı 14.10.2026). Koç günlük işini mobil/web
// öğretmen ekranlarında yapar; KM kurumun koçluğunu TEK yerden yönetir ve her
// koç işini koç ADINA da yapabilir (yönetici kapsamı `tenant`). Sekmeler:
//
//   · Kuyruk — koç iş kuyruğu (aciliyet sırası; koç süzgeci) ve kontrol
//     kuyruğu (teslimler: onayla / tekrar iste / yorum, seçerek ya da süzgeçle
//     toplu onay, öğrenci başına tek özet bildirim).
//   · Öğrenciler — koçluk öğrenci listesi (uyum, risk, bekleyenler) ve
//     öğrencinin koçluk kartı (360°): haftalık program, hedef önerisi, tebrik /
//     rozet, öğrenciye özel check-in soruları, görüşme, dönem raporu PDF'i,
//     öğrencinin/velinin gördüğü koçluk görünümü.
//   · Haftalık program — öğrenci × hafta geniş ızgara (ders programı ve tatil
//     gölge katman), görev oluşturucu (raf, sıradaki bölüm, zayıf konu, video,
//     ek), taslak → yayımla, kopyala, devret, PDF; şablonla toplu atama.
//   · Koçlar — yük tablosu, koç profili (kapasite, mesaj saatleri, görüşme
//     otomatik onayı), performans metrikleri, toplu koç atama (önizlemeli).
//   · Kütüphane — kurum kütüphanesi küratörlüğü (video / kesit / liste /
//     bağlantı / not), koçların önerdiği öğelere karar, kullanım sayıları.
//     Kütüphane sekmesinin "Video havuzu" görünümü: kurum geneli YouTube bağlantıları
//     (başlık / kanal / süre sunucudan), toplu yapıştırma, etiket, kullanım sayısı.
//   · Tebrik ve rozetler — koç rozeti tanımları, haftalık kutlama adayları ve
//     toplu tebrik.
//   · Check-in — kurum soru seti (pazar öz değerlendirmesine eklenir) ve
//     haftanın check-in durumu + koç yorumu (bayrak `self_review`). 16.10.2026'dan
//     beri "Tebrik ve check-in" sekmesinin ikinci görünümü.
//   · Görüşmeler — koç görüşmeleri: karar, not, eylem maddesi → görev / hedef
//     (bayrak `coach_meetings`).
//   · Şablonlar — metin şablonları (görüşme notu, soru yanıtı, check-in yorumu,
//     veli görüşmesi, mesaj, tebrik) ve program şablonları (Akademi README §14
//     madde 115).
//
// Başlıkta "Dışa aktar" (koç performansı, dönem raporları, haftalık
// programlar). Sekme yalnız okuma işlemi izinliyse ve özelliği kurumda açıksa
// çizilir; sekme dosyası ilk açılışta yüklenir.
//
// DERİN BAĞLANTI: `app.go('coaching', {coachingTab, studentId, student})`; sekmeler panelin üst
// gezinmesinde Koçluk grubunun haplarıdır (kontrol kuyruğu, haftalık program …), şerit gizlenir.
// Saf mantık `coaching-logic.js`'tedir (Node ile sınanır).

import { h, loadStyles } from '../../../ui-kit/kit.js';
import { emptyState, skeletonRows, tabBar } from '../../../ui-kit/layout.js';
import { actionButton, errorBox, sectionHead } from '../common.js';

const TABS = [
  { key: 'queue', label: 'Kuyruk', read: ['admin_getCoachQueue', 'admin_listCoachReviews'], file: './coaching-queue.js' },
  { key: 'students', label: 'Öğrenciler', read: ['admin_listCoachingStudents'], file: './coaching-students.js' },
  { key: 'week', label: 'Haftalık program', read: ['admin_getCoachWeek'], file: './coaching-week.js' },
  { key: 'coaches', label: 'Koçlar', read: ['admin_listCoaches', 'admin_bulkAssignCoach'], file: './coaching-coaches.js' },
  { key: 'library', label: 'Kütüphane', read: ['admin_listCoachLibrary', 'admin_listLibraryProposals', 'admin_listVideoPool'],
    file: './coaching-library.js' },
  // Check-in (16.10.2026) bu sekmenin ikinci görünümüdür: Koçluk 10 hap sınırında "Şablonlar"a yer açıldı.
  { key: 'motivation', label: 'Tebrik ve check-in', read: ['admin_listCoachBadges', 'admin_listCelebrationCandidates',
    'admin_listCheckins', 'admin_getCheckinQuestionSet'], file: './coaching-motivation.js' },
  { key: 'meetings', label: 'Görüşmeler', read: ['admin_listCoachMeetings'], file: './coaching-meetings.js' },
  // Akademi README §14 madde 115: metin şablonları (görüşme notu, soru yanıtı, check-in yorumu, veli görüşmesi,
  // mesaj, tebrik) + program (plan) şablonları tek yerde.
  { key: 'templates', label: 'Şablonlar', read: ['admin_listCoachTextTemplates', 'admin_listStudyPlanTemplates'],
    file: './coaching-templates.js' },
];

/** Eski sekme anahtarları → (sekme, görünüm). `checkins` artık "Tebrik ve check-in" sekmesinin görünümü. */
const MOVED = { checkins: ['motivation', { motivationView: 'checkins' }] };

export function section(app) {
  loadStyles(import.meta.url, 'coaching.css');
  const node = h('div', 'ak-stack');
  const exportButton = actionButton(app, 'admin_exportCoaching', 'Dışa aktar', { variant: 'ghost',
    onClick: () => import('./coaching-coaches.js').then((module) => module.exportDialog(app)) });
  node.append(sectionHead('Koçluk', {
    subtitle: 'Koçların öğrencileri, haftalık programlar, kontrol kuyruğu, kütüphane, tebrikler ve görüşmeler. '
      + 'Öğretmen = koç; buradan her koç işini koç adına da yapabilirsiniz.',
    actions: [exportButton],
  }));

  const visible = TABS.filter((tab) => tab.read.some((operationId) => app.can(operationId)));
  if (!visible.length) {
    node.append(emptyState({ title: 'Koçluk ekranları için yetkiniz yok',
      text: 'Koçluk kurumda kapalı olabilir (Ayarlar → Özellikler) ya da yöneticinizden izin isteyin.' }));
    return { node };
  }

  const body = h('div', 'ak-coach-body');
  const panes = new Map();         // anahtar → {node, shown?, destroy?} | Promise
  const ctx = {
    /** Başka sekmeye geç (ör. öğrenci kartından haftalık programa). */
    go: (key, payload = null) => open(key, payload),
    /** Öğrencinin koçluk kartını aç (Öğrenciler sekmesinin çekmecesi). */
    openStudent: (student) => import('./coaching-students.js').then((module) => module.openCard(app, student, ctx)),
  };

  async function open(key, payload = null) {
    if (MOVED[key]) {
      payload = { ...(payload || {}), ...MOVED[key][1] };
      [key] = MOVED[key];
    }
    const tab = visible.find((item) => item.key === key) || visible[0];
    if (bar.active !== tab.key) bar.select(tab.key, false);
    app.syncHosted?.('coaching', { coachingTab: tab.key });   // üstteki hap da bu sekmeye geçsin
    for (const [name, entry] of panes) if (entry.node) entry.node.hidden = name !== tab.key;
    let entry = panes.get(tab.key);
    if (!entry) {
      const slot = h('div', 'ak-coach-pane');
      slot.append(skeletonRows(6, 5));
      body.append(slot);
      entry = { node: slot, loading: true };
      panes.set(tab.key, entry);
      try {
        const module = await import(tab.file);
        const instance = module.tab(app, ctx);
        slot.replaceChildren(instance.node);
        Object.assign(entry, instance, { node: slot, loading: false });
      } catch (failure) {
        console.error(`[bbd_akademi] koçluk/${tab.key} açılamadı`, failure);
        slot.replaceChildren(errorBox(failure));
        entry.loading = false;
      }
      for (const [name, item] of panes) item.node.hidden = name !== bar.active;
    }
    if (!entry.loading) entry.shown?.(payload);
  }

  const bar = tabBar(visible.map(({ key, label }) => ({ key, label })), visible[0].key, (key) => open(key));
  // TEK GEZİNME (15.10.2026): sekmeler panelin üst gezinmesinde Koçluk grubunun hapları olarak
  // durur (`sections.js` → `host: 'coaching'`); ekranın kendi sekme şeridi o zaman gizlenir.
  if (app.hostsTabs?.('coaching')) bar.node.hidden = true;
  node.append(bar.node, body);

  let started = false;
  return {
    node,
    shown(payload) {
      if (payload?.coachingTab || payload?.studentId) {
        started = true;
        const key = payload.coachingTab || (payload.studentId ? 'students' : visible[0].key);
        open(key, payload);
        if (payload.studentId && key === 'students') {
          ctx.openStudent(payload.student || { id: payload.studentId, displayName: payload.studentName || '' });
        }
        return;
      }
      if (!started) { started = true; open(visible[0].key); }
    },
    destroy() {
      for (const entry of panes.values()) {
        try { entry.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      }
    },
  };
}
