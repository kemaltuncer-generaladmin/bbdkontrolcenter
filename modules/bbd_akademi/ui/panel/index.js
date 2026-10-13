// BBD Akademi — Kontrol Merkezi'nden Akademi'nin TAM yönetimi.
//
// KULLANICI KARARI: KM çekirdektir ve en yetkili uygulamadır; Akademi'nin her
// yönetim işi buradan yapılabilir, mobil yönetici pratik alt kümedir. İş kuralı
// Akademi API'sindedir: panel yalnız `/v1/admin/*` uçlarını modülün izin
// listeli vekili üzerinden çağırır (bkz. api.js, backend/proxy.py).
//
// BİLGİ MİMARİSİ TEK YERDE: `sections.js` → `AREAS` (menüde en fazla altı üst
// bölüm) ve `SECTIONS` (ekranlar; her biri bir üst bölümün altında). Yeni bir
// yönetim ekranı = bir dosya (`export function section(app) → {node, shown?,
// destroy?}`) + `SECTIONS`'a bir satır. Ekran dosyası ilk açıldığında yüklenir
// (dinamik import), sonra açık kalır; geri dönüldüğünde yeniden kurulmaz.
//
// YALNIZ AÇIK OLAN GÖRÜNÜR: kurumda kapalı özelliğin (`feature`) ekranı menüde
// yoktur; işlem düğmeleri de çizilmez (`api.js` → `featureOff`). Bayraklar
// açılışta `/km/status` → `features` ile gelir (okunamazsa her şey görünür).
//
// YETKİ: ekranın görünmesi `requires` (KM izinlerinden BİRİ) ile, düğmeler
// `app.api.can(operationId)` ile çizilir; asıl kapı vekildedir (K9).
//
// HIZ: açılış TEK çağrıdır (`/km/status?operations=1` — bağlantı, izinler,
// bayraklar ve işlem listesi birlikte); ilk ekran "Bugün" tek özet ucunu
// (`admin_getDashboard`) çağırır.
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_akademi/index.js  →  shell/ui-kit/
// Kaynak modules/bbd_akademi/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, h, loadStyles, toaster } from '../../ui-kit/kit.js';
import { alertBox, emptyState, skeletonRows } from '../../ui-kit/layout.js';
import { createClient, problem } from './api.js';
import { AREAS, SECTIONS, buildNav, featureOn, resolveTarget } from './sections.js';

export { AREAS, SECTIONS };

const P = 'bbd_akademi';

/**
 * Arayüz yetenekleri. `bbd_akademi.students` bir İŞARETTİR: çözülebiliyorsa
 * kullanıcı BBD Akademi'yi görebiliyor demektir ve "Öğrenci Yönetimi" ekranı
 * (bbd_students) kullanıcıyı buraya — Öğrenciler bölümüne — yönlendirir.
 */
export function capabilities() {
  return { 'bbd_akademi.students': () => ({ section: 'students' }) };
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — dosya tepesinde değil, burada

  const view = h('div', 'kit-panel ak');
  const toast = toaster(view);
  const disposers = [];
  let disposed = false;
  let permissions = null;             // null = okunamadı → karar backend'de
  let features = null;                // kurum bayrakları; null = okunamadı → her şey görünür

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
    /**
     * Başka ekrana geç: `app.go('finance', {studentId})`. Anahtar ekran
     * (`finance`), üst bölüm (`money`) ya da eski ad olabilir (`sections.js`).
     */
    go: (key, payload = null) => show(key, payload),
    /** KM'nin başka ekranına geç (ör. Öğrenciler modülü). */
    openPanel: (id, payload = null) => ctx.open?.(id, payload),
    /**
     * Başka modülün ARAYÜZ yeteneği (K3; yoksa null). Öğrenci yönetimi:
     * `bbd_students.manager` (liste + toplu işlemler + gruplar) ve
     * `bbd_students.record` (öğrenci kartındaki "Kayıt ve veliler").
     */
    capability: (name) => (typeof ctx.capability === 'function' ? ctx.capability(name) : null),
    /**
     * KM'nin başka bir modülünün SALT OKUNUR ucu (ör. Zil Sistemi
     * `/api/bell/plan/status`). Yalnız GET; o modülün kendi izni geçerlidir
     * (K9). Modül yoksa ya da uç bu sürümde yoksa 404/403 gelir — çağıran
     * ekran bunu ham hata olarak göstermez, sebebini yazar.
     */
    kmGet: (path) => ctx.api(path),
    /** Sözleşme şalteri açık mı (`backend/contract_features.py`; okunamadıysa kapalı). */
    feature: (name) => Boolean(app.status?.contractFeatures?.[name]),
    /** Kurum özellik bayrağı açık mı (Akademi `features`; okunamadıysa AÇIK sayılır). */
    enabled: (flag) => featureOn(features, flag),
    /**
     * Ayarlar → Özellikler'de bir bayrak değişince: bayrakları tazele, menüyü
     * yeniden çiz (kapanan ekranın menü satırı hemen kalkar, açılanınki gelir).
     */
    refreshFeatures: () => refreshFeatures(),
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

  const built = new Map();            // ekran → {node, shown, destroy}
  let menu = [];                      // görünür üst bölümler (`buildNav`)
  let current = null;                 // {area, section}

  const navContext = () => ({ has: (permission) => app.has(permission), features });
  const sectionOf = (key) => SECTIONS.find((item) => item.key === key);

  function paintNav() {
    menu = buildNav(navContext());
    nav.replaceChildren();
    for (const area of menu) {
      const on = current?.area === area.key;
      const item = h('button', 'ak-nav-item', area.label);
      item.type = 'button';
      item.title = area.hint;
      item.classList.toggle('on', on);
      item.setAttribute('aria-current', on && area.sections.length === 1 ? 'page' : 'false');
      item.setAttribute('aria-expanded', on ? 'true' : 'false');
      item.addEventListener('click', () => show(area.key));
      nav.append(item);
      // Alt ekranlar yalnız seçili üst bölümün altında açılır; tek ekranlı
      // bölümde (Öğrenciler) alt satır çizilmez.
      if (on && area.sections.length > 1) {
        item.classList.add('ak-nav-open');
        const sub = h('div', 'ak-nav-sub');
        sub.setAttribute('role', 'group');
        sub.setAttribute('aria-label', area.label);
        for (const section of area.sections) {
          const subOn = current.section === section.key;
          const entry = h('button', 'ak-nav-subitem', section.label);
          entry.type = 'button';
          entry.classList.toggle('on', subOn);
          entry.setAttribute('aria-current', subOn ? 'page' : 'false');
          entry.addEventListener('click', () => show(section.key));
          sub.append(entry);
        }
        nav.append(sub);
      }
    }
  }

  async function show(key, payload = null) {
    const target = resolveTarget(menu, key);
    if (!target) {
      const section = sectionOf(key);
      toast(section?.feature && !featureOn(features, section.feature)
        ? `“${section.label}” kurumda kapalı. Ayarlar → Özellikler'den açılabilir.`
        : 'Bu bölümü açma yetkiniz yok.', 'bad');
      return;
    }
    const section = sectionOf(target.section);
    current = target;
    paintNav();
    for (const [name, entry] of built) entry.node.hidden = name !== section.key;
    let entry = built.get(section.key);
    if (!entry) {
      const slot = h('div', 'ak-section');
      slot.append(skeletonRows(6, 5));
      main.append(slot);
      entry = { node: slot, loading: true };
      built.set(section.key, entry);
      try {
        const module = await import(section.file);
        if (disposed) return;
        const instance = module.section(app);
        slot.replaceChildren(instance.node);
        Object.assign(entry, instance, { node: slot, loading: false });
      } catch (failure) {
        console.error(`[bbd_akademi] ${section.key} bölümü açılamadı`, failure);
        slot.replaceChildren(emptyState({ title: `“${section.label}” açılamadı`,
          text: problem(failure, 'Bölüm yüklenemedi.') }));
        entry.loading = false;
      }
      for (const [name, item] of built) item.node.hidden = name !== current.section;
    }
    if (!entry.loading) entry.shown?.(payload);
  }

  // ------------------------------------------------------------- açılış
  function paintBanner(status) {
    banner.replaceChildren();
    const connection = status?.connection;
    if (!connection) return;
    // Teknik ayrıntı (ayar adı, kasa anahtarı) modül README'sindedir; burada
    // kurum yöneticisinin anlayacağı cümle durur.
    if (!connection.enabled) {
      banner.append(alertBox('Akademi bağlantısı kapalı; ekranlar veri getiremez. '
        + 'Kontrol Merkezi sistem yöneticisine başvurun.', 'warn'));
    } else if (!connection.hasKey) {
      banner.append(alertBox('Akademi bağlantısı henüz kurulmamış; ekranlar veri getiremez. '
        + 'Kontrol Merkezi sistem yöneticisine başvurun.', 'warn'));
    }
  }

  function applyStatus(status) {
    app.status = status;
    permissions = new Set(status?.permissions || []);
    features = status?.features && typeof status.features === 'object' ? status.features : null;
    app.api.setFeatures(features);
  }

  async function refreshFeatures() {
    try {
      const status = await ctx.api(`/api/${P}/km/status?fresh=1`);
      if (disposed) return;
      applyStatus(status);
    } catch {
      return;                          // menü eski haliyle kalır; karar Akademi'de
    }
    paintNav();
    // Açık ekran kapanan bir özelliğe aitse ilk ekrana dönülür.
    if (current && !resolveTarget(menu, current.section)) {
      const first = menu[0]?.sections[0];
      if (first) show(first.key);
    }
  }

  async function boot() {
    try {
      const status = await ctx.api(`/api/${P}/km/status?operations=1`);
      if (Array.isArray(status?.operationList)) app.api.setOperations(status.operationList);
      else await app.api.loadOperations();
      applyStatus(status);
      paintBanner(status);
    } catch (failure) {
      permissions = null;
      features = null;
      banner.replaceChildren(alertBox(`Akademi durumu okunamadı: ${problem(failure)}`, 'bad'));
    }
    if (disposed) return;
    menu = buildNav(navContext());
    main.replaceChildren();
    const first = menu[0]?.sections[0];
    if (!first) {
      paintNav();
      main.append(emptyState({ title: 'Görüntülenecek bölüm yok',
        text: 'Akademi bölümleri için yetkiniz yok.',
        actions: [button('Yenile', { onClick: () => boot() })] }));
      return;
    }
    const wanted = ctx.payload?.section && resolveTarget(menu, ctx.payload.section)
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
