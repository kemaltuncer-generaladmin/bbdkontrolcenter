// BBD Akademi — Kontrol Merkezi'nden Akademi'nin TAM yönetimi.
//
// KULLANICI KARARI: KM çekirdektir ve en yetkili uygulamadır; Akademi'nin her
// yönetim işi buradan yapılabilir, mobil yönetici pratik alt kümedir. İş kuralı
// Akademi API'sindedir: panel yalnız `/v1/admin/*` uçlarını modülün izin
// listeli vekili üzerinden çağırır (bkz. api.js, backend/proxy.py).
//
// BÖLÜM KAYDI TEK YERDE: aşağıdaki `SECTIONS` dizisi. Yeni bir yönetim bölümü =
// bir dosya (`export function section(app) → {node, shown?, destroy?}`) + bu
// diziye bir satır. Bölüm dosyası ilk açıldığında yüklenir (dinamik import),
// sonra açık kalır; geri dönüldüğünde yeniden kurulmaz.
//
// YETKİ: bölümün görünmesi `requires` (KM izinlerinden BİRİ) ile, düğmeler
// `app.api.can(operationId)` ile çizilir; asıl kapı vekildedir (K9).
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_akademi/index.js  →  shell/ui-kit/
// Kaynak modules/bbd_akademi/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, h, loadStyles, toaster } from '../../ui-kit/kit.js';
import { alertBox, emptyState, skeletonRows } from '../../ui-kit/layout.js';
import { createClient, problem } from './api.js';

const P = 'bbd_akademi';

/**
 * Bölümler. `requires`: KM izinleri (biri yeterli). `file`: bölüm modülü.
 * `group`: menüdeki başlık (aynı gruptakiler art arda yazılır). Sıra menü
 * sırasıdır. Yeni bölüm = bir dosya + buraya bir satır.
 */
export const SECTIONS = [
  { key: 'dashboard', label: 'Panel', file: './dashboard.js', requires: ['view'] },
  { key: 'students', label: 'Öğrenciler', file: './students.js', requires: ['view'] },
  { key: 'curriculum', label: 'Müfredat', file: './curriculum.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'resources', label: 'Kaynaklar', file: './resources.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'timetable', label: 'Ders programı', file: './timetable.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'calendar', label: 'Kurum takvimi', file: './institution-calendar.js',
    requires: ['view'], group: 'Akademik' },
  { key: 'attendance', label: 'Yoklama', file: './attendance.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'homework', label: 'Ödevler', file: './homework.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'exams', label: 'Denemeler', file: './exams.js', requires: ['view'],
    group: 'Akademik' },
  { key: 'plan', label: 'Çalışma planı', file: './plan.js', requires: ['view'],
    group: 'Öğrenme araçları' },
  { key: 'reading', label: 'Okuma', file: './reading.js', requires: ['view'],
    group: 'Öğrenme araçları' },
  { key: 'decks', label: 'Bilgi kartları', file: './decks.js', requires: ['learning_setup'],
    group: 'Öğrenme araçları' },
  { key: 'evaluations', label: 'Değerlendirmeler', file: './evaluations.js',
    requires: ['evaluations', 'learning_setup'], group: 'Öğrenme araçları' },
  { key: 'insights', label: 'Erken uyarı', file: './insights.js', requires: ['view'],
    group: 'Öğrenme araçları' },
  { key: 'finance', label: 'Kurum finansı', file: './finance.js', requires: ['finance'],
    group: 'Kurum' },
  { key: 'books', label: 'Kitap talepleri', file: './books.js', requires: ['view'],
    group: 'Kurum' },
  { key: 'announcements', label: 'Duyurular', file: './announcements.js',
    requires: ['announcements'], group: 'Kurum' },
  { key: 'requests', label: 'Talepler', file: './requests.js', requires: ['requests'],
    group: 'Kurum' },
  { key: 'settings', label: 'Ayarlar', file: './settings.js', requires: ['settings'],
    group: 'Yönetim' },
  { key: 'staff', label: 'Personel', file: './staff.js', requires: ['staff'], group: 'Yönetim' },
];

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — dosya tepesinde değil, burada

  const view = h('div', 'kit-panel ak');
  const toast = toaster(view);
  const disposers = [];
  let disposed = false;
  let permissions = null;             // null = okunamadı → karar backend'de

  const app = {
    root: view,
    toast,
    api: createClient(ctx),
    status: null,
    /** KM izni (`finance` → `bbd_akademi.finance`). */
    has(permission) {
      if (permissions === null) return true;
      return permissions.has(`${P}.${permission}`);
    },
    can: (operationId) => app.api.can(operationId),
    /** Başka bölüme geç: `app.go('finance', {studentId})`. */
    go: (key, payload = null) => show(key, payload),
    /** KM'nin başka ekranına geç (ör. Öğrenciler modülü). */
    openPanel: (id, payload = null) => ctx.open?.(id, payload),
    onDispose: (fn) => disposers.push(fn),
    lookups: null,
  };
  app.lookups = lookups(app);

  // ------------------------------------------------------------ iskelet
  const nav = h('nav', 'ak-nav');
  nav.setAttribute('aria-label', 'Akademi bölümleri');
  const banner = h('div', 'ak-banner');
  const main = h('div', 'ak-main');
  const layout = h('div', 'ak-layout');
  layout.append(nav, main);
  view.append(banner, layout);
  main.append(skeletonRows(6, 5));
  root.replaceChildren(view);

  const built = new Map();            // key → {node, shown, destroy}
  const navButtons = new Map();
  let current = null;

  function visibleSections() {
    return SECTIONS.filter((section) => section.requires.some((perm) => app.has(perm)));
  }

  function paintNav() {
    nav.replaceChildren();
    navButtons.clear();
    let group = null;
    for (const section of visibleSections()) {
      if ((section.group || null) !== group) {
        group = section.group || null;
        if (group) nav.append(h('div', 'ak-nav-group', group));
      }
      const item = h('button', 'ak-nav-item', section.label);
      item.type = 'button';
      item.addEventListener('click', () => show(section.key));
      navButtons.set(section.key, item);
      nav.append(item);
    }
    paintActive();
  }

  function paintActive() {
    for (const [key, item] of navButtons) {
      item.classList.toggle('on', key === current);
      item.setAttribute('aria-current', key === current ? 'page' : 'false');
    }
  }

  async function show(key, payload = null) {
    const section = visibleSections().find((item) => item.key === key);
    if (!section) {
      toast('Bu bölümü açma yetkiniz yok.', 'bad');
      return;
    }
    current = key;
    paintActive();
    for (const [name, entry] of built) entry.node.hidden = name !== key;
    let entry = built.get(key);
    if (!entry) {
      const slot = h('div', 'ak-section');
      slot.append(skeletonRows(6, 5));
      main.append(slot);
      entry = { node: slot, loading: true };
      built.set(key, entry);
      try {
        const module = await import(section.file);
        if (disposed) return;
        const instance = module.section(app);
        slot.replaceChildren(instance.node);
        Object.assign(entry, instance, { node: slot, loading: false });
      } catch (failure) {
        console.error(`[bbd_akademi] ${key} bölümü açılamadı`, failure);
        slot.replaceChildren(emptyState({ title: `“${section.label}” açılamadı`,
          text: problem(failure, 'Bölüm yüklenemedi.') }));
        entry.loading = false;
      }
      for (const [name, item] of built) item.node.hidden = name !== current;
    }
    if (!entry.loading) entry.shown?.(payload);
  }

  // ------------------------------------------------------------- açılış
  function paintBanner(status) {
    banner.replaceChildren();
    const connection = status?.connection;
    if (!connection) return;
    if (!connection.enabled) {
      banner.append(alertBox('Akademi bağlantısı ayarlardan kapatılmış '
        + '(modules.bbd_akademi.enabled). Ekranlar veri getiremez.', 'warn'));
    } else if (!connection.hasKey) {
      banner.append(alertBox('Akademi entegrasyon anahtarı tanımlı değil. Anahtar sır kasasına '
        + '“bbd_akademi.api_key” adıyla yazılmalı (kurulum: modül README).', 'warn'));
    }
  }

  async function boot() {
    try {
      const [status] = await Promise.all([ctx.api(`/api/${P}/km/status`),
        app.api.loadOperations()]);
      app.status = status;
      permissions = new Set(status?.permissions || []);
      paintBanner(status);
    } catch (failure) {
      permissions = null;
      banner.replaceChildren(alertBox(`Akademi durumu okunamadı: ${problem(failure)}`, 'bad'));
    }
    if (disposed) return;
    paintNav();
    main.replaceChildren();
    const first = visibleSections()[0];
    if (!first) {
      main.append(emptyState({ title: 'Görüntülenecek bölüm yok',
        text: 'Akademi bölümleri için yetkiniz yok.',
        actions: [button('Yenile', { onClick: () => boot() })] }));
      return;
    }
    const wanted = ctx.payload?.section && visibleSections().some((s) => s.key === ctx.payload.section)
      ? ctx.payload.section : first.key;
    show(wanted, ctx.payload || null);
  }

  boot();

  return () => {
    disposed = true;
    for (const entry of built.values()) {
      try { entry.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
    }
    for (const fn of disposers.splice(0)) {
      try { fn(); } catch { /* temizlik yarıda kalmaz */ }
    }
    root.replaceChildren();
  };
}

/**
 * Seyrek değişen listeler — ETag'li önbellek (60 sn taze). Bölümler sınıf,
 * dönem, ders, personel listesini her açılışta yeniden çekmez.
 */
function lookups(app) {
  const items = (data) => (Array.isArray(data?.items) ? data.items : []);
  return {
    classGroups: () => app.api.getCached('/v1/admin/class-groups', { active: true }).then(items),
    terms: () => app.api.getCached('/v1/admin/terms').then(items),
    studentGroups: () => app.api.getCached('/v1/admin/student-groups').then(items),
    subjects: () => app.api.getCached('/v1/admin/curricula/subjects', undefined,
      { ttlMs: 10 * 60_000 }).then(items),
    staff: () => app.api.getCached('/v1/admin/staff', { active: true }).then(items),
    teachers: () => app.api.getCached('/v1/admin/staff', { active: true, role: 'teacher' })
      .then(items),
    bellSlots: () => app.api.getCached('/v1/admin/bell-slots', undefined, { ttlMs: 5 * 60_000 }),
    /** Seçenek listesine çevirir: [{value: id, label: name}]. */
    async options(kind, { empty } = {}) {
      const list = await this[kind]();
      const mapped = list.map((item) => ({ value: item.id || item.key,
        label: item.name || item.displayName || item.key }));
      return empty !== undefined ? [{ value: '', label: empty }, ...mapped] : mapped;
    },
  };
}
