// BBD Akademi — Kontrol Merkezi'nden Akademi'nin TAM yönetimi.
//
// KULLANICI KARARI: KM çekirdektir ve en yetkili uygulamadır; Akademi'nin her
// yönetim işi buradan yapılabilir, mobil yönetici pratik alt kümedir. İş kuralı
// Akademi API'sindedir: panel yalnız `/v1/admin/*` uçlarını modülün izin
// listeli vekili üzerinden çağırır (bkz. api.js, backend/proxy.py).
//
// BİLGİ MİMARİSİ TEK YERDE: `sections.js` → `AREAS` (en çok on ana grup) ve
// `SECTIONS` (ekranlar; her biri bir ana grubun altında). Yeni bir yönetim
// ekranı = bir dosya (`export function section(app) → {node, shown?,
// destroy?}`) + `SECTIONS`'a bir satır. Ekran dosyası ilk açıldığında yüklenir
// (dinamik import), sonra açık kalır; geri dönüldüğünde yeniden kurulmaz.
//
// TEK SOL GEZİNME (15.10.2026): sol menü yalnız KM kabuğunundur; panel kendi
// sol menüsünü ÇİZMEZ. Gezinme üstte: simgeli ana grup şeridi + seçili grubun
// hap sekmeleri; panel ana sayfayla (büyük kutucuklar, `home.js`) açılır. Her
// ekran ana sayfadan tek, her yerden en çok iki tıktadır.
//
// YALNIZ AÇIK OLAN GÖRÜNÜR: kurumda kapalı özelliğin (`feature`) ekranı menüde
// yoktur; işlem düğmeleri de çizilmez (`api.js` → `featureOff`). Bayraklar
// açılışta `/km/status` → `features` ile gelir (okunamazsa her şey görünür).
//
// YETKİ: ekranın görünmesi `requires` (KM izinlerinden BİRİ) ile, düğmeler
// `app.api.can(operationId)` ile çizilir; asıl kapı vekildedir (K9).
//
// HIZ: açılış TEK çağrıdır (`/km/status?operations=1` — bağlantı, izinler,
// bayraklar ve işlem listesi birlikte); ana sayfa kutucuk sayıları için tek
// özet ucunu (`admin_getDashboard`, 60 sn önbellekli) çağırır.
//
// GÜNCELLENİYOR BANDI: Akademi dağıtılırken bir okuma "güncelleniyor" hatasıyla
// biterse (köprü sunucuda ≤ 20 sn yeniden denedi) üstte bant çıkar, geri sayımla
// son okumayı yeniden dener; Akademi dönünce bant kalkar ve açık ekran yeniden
// kurulur (pencere/çekmece açıksa kullanıcının girdisi kaybolmasın diye yalnız
// "Yenile" önerilir).
//
// Import yolu panelin KOPYALANMIŞ konumuna göredir:
//   shell/panels/bbd_akademi/index.js  →  shell/ui-kit/
// Kaynak modules/bbd_akademi/ui/panel/ altındayken '../../ui-kit/' dosya
// sisteminde ÇÖZÜLMEZ — normaldir (ADR 0011).

import { button, h, loadStyles, toaster } from '../../ui-kit/kit.js';
import { alertBox, emptyState, skeletonRows } from '../../ui-kit/layout.js';
import { createClient, problem } from './api.js';
import { updatingBand } from './updating.js';
import { ALIASES, AREAS, SECTIONS, buildNav, featureOn, homeTiles, resolveTarget } from './sections.js';
import { navIcon } from './nav-icons.js';
import { documentButtons } from './documents.js';
import { studentDeletionDoc } from './documents-logic.js';
import { installContractWizard } from './contracts/wizard-entry.js';
import { createFinanceLock } from './finance/finance-lock.js';
import { setFinanceUnlock } from './common.js';

export { AREAS, SECTIONS };

const P = 'bbd_akademi';

/**
 * Arayüz yetenekleri. `bbd_akademi.students` bir İŞARETTİR: çözülebiliyorsa
 * kullanıcı BBD Akademi'yi görebiliyor demektir ve "Öğrenci Yönetimi" ekranı
 * (bbd_students) kullanıcıyı buraya — Öğrenciler bölümüne — yönlendirir.
 */
export function capabilities(ctx = {}) {
  return {
    'bbd_akademi.students': () => ({ section: 'students' }),
    /**
     * Toplu kalıcı silme sonucunun kurum logolu belgesi (0.5.4): Öğrenci Yönetimi
     * (bbd_students) sonuç penceresine "Önizle / Yazdır" düğmelerini bu yetenekle
     * koyar (K3 — modül modülü import etmez). Kapı sunucuda: `bbd_students.delete`.
     *   (root, {result}) → Node   (işlemi yapan, belge damgasında sunucudan yazılır)
     */
    'bbd_akademi.deletionDocument': (root, { result } = {}) => {
      const docApp = {
        root,
        toast: toaster(root),
        api: { km: (path, options) => ctx.api(`/api/${P}/km${path}`, options) },
        coreApi: (path, options) => ctx.api(path, options),
      };
      return documentButtons(docApp, { title: 'Kalıcı silme sonucu', label: 'Sonuç belgesi',
        build: async () => studentDeletionDoc(result) });
    },
  };
}

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — dosya tepesinde değil, burada

  const view = h('div', 'kit-panel ak');
  const toast = toaster(view);
  const disposers = [];
  let disposed = false;
  let permissions = null;             // null = okunamadı → karar backend'de
  let features = null;                // kurum bayrakları; null = okunamadı → her şey görünür
  // YALNIZ ÖĞRENCİ YÖNETİMİ (0.5.3, ADR 0031): Akademi izni yok ama öğrenci
  // yönetimi izni var (muhasebe, org_staff). Panel yalnız "Öğrenciler"i KM
  // kaydıyla açar; Akademi'ye hiç istek gitmez.
  let kmOnly = false;

  // Bant aşağıda (iskelet) kurulur; istemci kancaları ona geç bağlanır.
  let band = null;
  const app = {
    root: view,
    toast,
    api: createClient(ctx, {
      onUnavailable: (_error, stale) => band?.show(stale),
      onAvailable: () => band?.recovered(),
      // Kurum finansı kilidi (iş 83): vekilin bildirdiği süre / kilit durumu tek nesnede.
      onFinanceUntil: (until) => app.financeLock?.noteUntil(until),
      onFinanceLocked: () => app.financeLock?.noteLocked(),
    }),
    status: null,
    /** KM izni (`finance` → `bbd_akademi.finance`). */
    has(permission) {
      if (kmOnly) return false;
      if (permissions === null) return true;
      return permissions.has(`${P}.${permission}`);
    },
    /** Akademi izni yok, yalnız öğrenci yönetimi (KM kaydı) açık mı? */
    kmOnly: () => kmOnly,
    can: (operationId) => app.api.can(operationId),
    /**
     * Başka ekrana geç: `app.go('finance', {studentId})`. Anahtar ekran
     * (`finance`), ana grup (`money`), `home` (ana sayfa) ya da eski ad
     * olabilir (`sections.js`).
     */
    go: (key, payload = null) => (key === 'home' ? showHome() : show(key, payload)),
    /** KM'nin başka ekranına geç (ör. Öğrenciler modülü). */
    openPanel: (id, payload = null) => ctx.open?.(id, payload),
    /**
     * Başka modülün ARAYÜZ yeteneği (K3; yoksa null). Öğrenci yönetimi:
     * `bbd_students.manager` (liste + toplu işlemler + gruplar) ve
     * `bbd_students.record` (öğrenci kartındaki "Kayıt ve veliler").
     */
    capability: (name) => (typeof ctx.capability === 'function' ? ctx.capability(name) : null),
    /**
     * KM'nin başka bir modülünden SALT OKUMA — o modülün ARAYÜZ YETENEĞİ
     * üzerinden (ADR 0033 K12: başka modülün `/api/<id>` önekine doğrudan
     * gidilmez; adres sağlayan modülün kendi kodundadır). Örnek:
     * `kmRead('bell.status', 'planStatus', {from, to})`,
     * `kmRead('bbd_students.groups_read', 'list', {archived: 'all'})`.
     * İstek yine kullanıcının oturumuyla gider; o modülün kendi izni geçerlidir
     * (K9). Yetenek çözülmüyorsa (modül kapalı ya da kullanıcı o ekranı
     * göremiyor — kabuk görünmeyen paneli sağlayıcı yapmaz) 403 gibi davranır:
     * eskiden doğrudan çağrı da izinsiz kullanıcıda 403 alıyordu; çağıran ekran
     * bunu ham hata olarak göstermez, sebebini yazar.
     */
    kmRead: async (name, method, ...args) => {
      const provider = typeof ctx.capability === 'function' ? ctx.capability(name) : null;
      if (!provider || typeof provider[method] !== 'function') {
        throw Object.assign(new Error(`KM yeteneği yok: ${name}`), { status: 403, code: 'capability_missing' });
      }
      return provider[method](...args);
    },
    /**
     * Çekirdeğin istek kanalı — YALNIZ belge baskısı için (`documents.js`:
     * `ui-kit/printing.js` üretilmiş PDF'i `/api/outputs/document` ile alır;
     * kapısı çekirdeğin `outputs.print` iznidir).
     */
    coreApi: (path, options) => ctx.api(path, options),
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
  // Kurum finansı kilidi (iş 83): SMS onay kodlu giriş; kilit hatası her ekranda aynı pencereyi açar.
  app.financeLock = createFinanceLock(app);
  setFinanceUnlock(() => app.financeLock.ensure());
  disposers.push(() => { app.financeLock.dispose(); setFinanceUnlock(null); });
  // "Kayıt ve ödeme planı" sihirbazı (F-SZ1): KM "Yeni öğrenci" sonrası ve sözleşme ekranlarının açma isteği.
  disposers.push(installContractWizard(app));

  // ------------------------------------------------------------ iskelet
  // TEK SOL GEZİNME (15.10.2026): sol menü KM kabuğunundur. Panelin gezinmesi
  // ÜSTTEDİR — simgeli ana grup şeridi ve seçili grubun hap sekmeleri; ana
  // sayfa büyük kutucuklardır (`home.js`). Panel ikinci bir sol menü çizmez.
  const top = h('div', 'ak-top');
  const areasNav = h('nav', 'ak-areas');
  areasNav.setAttribute('aria-label', 'Akademi bölümleri');
  const pills = h('nav', 'ak-pills');
  pills.hidden = true;
  top.append(areasNav, pills);
  const banner = h('div', 'ak-banner');
  const main = h('div', 'ak-main');
  band = updatingBand({
    probe: () => app.api.probe(),
    reload: () => reloadCurrent(),
    busy: () => Boolean(view.querySelector('.kit-overlay, .kit-drawer')),
  });
  disposers.push(() => band.dispose());
  view.append(band.node, top, banner, main);
  main.append(skeletonRows(6, 5));
  root.replaceChildren(view);

  const built = new Map();            // ekran (paylaşılan ekranda `host`) → {node, shown, destroy}
  let menu = [];                      // görünür ana gruplar (`buildNav`)
  let current = null;                 // {area, section, payload}; ana sayfada {area: 'home'}
  let home = null;                    // ana sayfa (ilk açılışta kurulur)

  const navContext = () => ({ has: (permission) => app.has(permission), can: (operationId) => app.can(operationId),
    features, kmOnly });
  const sectionOf = (key) => SECTIONS.find((item) => item.key === key);
  /** Ekranın yuvası: aynı dosyanın hapları (`host`) TEK ekranı paylaşır. */
  const slotOf = (section) => (section ? section.host || section.key : null);
  const activeSlot = () => slotOf(sectionOf(current?.section));

  function areaButton(area) {
    const on = current?.area === area.key;
    const item = h('button', `ak-area ak-tone-${area.tone || 'slate'}`);
    item.type = 'button';
    item.title = area.hint;
    item.dataset.area = area.key;
    item.append(navIcon(area.icon), h('span', 'ak-area-label', area.label));
    item.classList.toggle('on', on);
    const single = area.key === 'home' || (area.sections || []).length < 2;
    item.setAttribute('aria-current', on ? (single ? 'page' : 'true') : 'false');
    item.addEventListener('click', () => (area.key === 'home' ? showHome() : show(area.key)));
    return item;
  }

  function paintNav() {
    menu = buildNav(navContext());
    areasNav.replaceChildren();
    if (homeTiles(menu).length) {
      areasNav.append(areaButton({ key: 'home', label: 'Ana sayfa', icon: 'home', tone: 'slate',
        hint: 'Bütün bölümler kutucuklarla; her ekran tek tıkta.' }));
    }
    for (const area of menu) areasNav.append(areaButton(area));
    // Haplar yalnız seçili grubun birden çok ekranı varsa çizilir.
    const area = menu.find((item) => item.key === current?.area);
    pills.replaceChildren();
    pills.className = `ak-pills ak-tone-${area?.tone || 'slate'}`;
    pills.hidden = !area || area.sections.length < 2;
    if (pills.hidden) return;
    pills.setAttribute('aria-label', `${area.label} ekranları`);
    for (const section of area.sections) {
      const on = current.section === section.key;
      const pill = h('button', 'ak-pill', section.label);
      pill.type = 'button';
      pill.dataset.section = section.key;
      pill.classList.toggle('on', on);
      pill.setAttribute('aria-current', on ? 'page' : 'false');
      pill.addEventListener('click', () => show(section.key));
      pills.append(pill);
    }
  }

  function hideAll() {
    if (home) home.node.hidden = current?.area !== 'home';
    const slot = activeSlot();
    for (const [name, entry] of built) entry.node.hidden = name !== slot;
  }

  /** Ana sayfa: büyük kutucuklar (her grup + ekranları), hızlı işler, günün sayıları. */
  async function showHome() {
    current = { area: 'home', section: null, payload: null };
    paintNav();
    if (!home) {
      try {
        const module = await import('./home.js');
        if (disposed) return;
        home = module.homeView(app, { tiles: () => homeTiles(menu), nav: () => menu });
        main.prepend(home.node);
      } catch (failure) {
        console.error('[bbd_akademi] ana sayfa açılamadı', failure);
        const first = menu[0]?.sections[0];
        if (first) show(first.key);
        return;
      }
    }
    hideAll();
    if (current?.area === 'home') home.shown();
    main.scrollTop = 0;
  }

  /** Açık ekranı baştan kurar (Akademi güncellemesinden sonra verisi tazelensin). */
  function reloadCurrent() {
    if (!current || disposed) return;
    if (current.area === 'home') {
      home?.shown({ fresh: true });
      return;
    }
    const key = current.section;
    const slot = activeSlot();
    const entry = built.get(slot);
    if (entry && !entry.loading) {
      try { entry.destroy?.(); } catch { /* temizlik yarıda kalmaz */ }
      entry.node.remove();
      built.delete(slot);
    }
    show(key, current.payload || null);
  }

  async function show(key, payload = null) {
    const target = resolveTarget(menu, key, payload);
    if (!target) {
      const section = sectionOf(ALIASES[key] || key);
      toast(section?.feature && !featureOn(features, section.feature)
        ? `“${section.label}” kurumda kapalı. Ayarlar → Özellikler'den açılabilir.`
        : 'Bu bölümü açma yetkiniz yok.', 'bad');
      return;
    }
    const section = sectionOf(target.section);
    // Hap, paylaşılan ekranın sekmesini kendi `payload`'ıyla seçer; çağıranınki önce gelir.
    const merged = section.payload ? { ...section.payload, ...(payload || {}) } : payload;
    const slot = slotOf(section);
    const changed = current?.section !== section.key;
    current = { ...target, payload: merged };
    paintNav();
    hideAll();
    if (changed) main.scrollTop = 0;
    let entry = built.get(slot);
    if (!entry) {
      const node = h('div', 'ak-section');
      node.append(skeletonRows(6, 5));
      main.append(node);
      entry = { node, loading: true };
      built.set(slot, entry);
      try {
        const module = await import(section.file);
        if (disposed) return;
        const instance = module.section(app);
        node.replaceChildren(instance.node);
        Object.assign(entry, instance, { node, loading: false });
      } catch (failure) {
        console.error(`[bbd_akademi] ${section.key} bölümü açılamadı`, failure);
        node.replaceChildren(emptyState({ title: `“${section.label}” açılamadı`,
          text: problem(failure, 'Bölüm yüklenemedi.') }));
        entry.loading = false;
      }
      hideAll();
    }
    if (!entry.loading) entry.shown?.(merged);
  }

  /**
   * Paylaşılan ekran kendi içinden sekme değiştirdi (ör. koçluk kartından
   * haftalık programa): üstteki hap da o sekmeye geçsin.
   */
  function syncHosted(host, payload) {
    const area = menu.find((item) => item.key === current?.area);
    const match = area?.sections.find((section) => section.host === host && section.payload
      && Object.entries(section.payload).every(([name, value]) => payload?.[name] === value));
    if (!match || match.key === current.section) return;
    current = { ...current, section: match.key, payload };
    paintNav();
  }
  app.syncHosted = syncHosted;
  /** Ekran bu kullanıcıya görünür mü (yetki + kurum özelliği)? Rapor merkezi, ana sayfa. */
  app.canOpen = (key, payload = null) => Boolean(resolveTarget(menu, key, payload));
  /** Ekranın sekmeleri üst gezinmede hap mı? (Öyleyse ekran kendi sekme şeridini gizler.) */
  app.hostsTabs = (host) => SECTIONS.some((section) => section.host === host);

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
    if (status?.financeAccess) app.financeLock.set(status.financeAccess);
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
    if (current?.area === 'home') {
      home?.shown();
      return;
    }
    // Açık ekran kapanan bir özelliğe aitse ana sayfaya (yoksa ilk ekrana) dönülür.
    if (current && !resolveTarget(menu, current.section)) start(null);
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
      if (failure?.status === 403 && typeof app.capability('bbd_students.manager') === 'function') {
        // Akademi izni yok; öğrenci yönetimi izni var → yalnız Öğrenciler.
        kmOnly = true;
        banner.replaceChildren(alertBox('Öğrenci kayıtlarını (kimlik, sınıf, veliler, kantin) '
          + 'buradan yönetirsiniz. Ders, ödev ve deneme bilgileri için BBD Akademi yetkisi gerekir.', 'info'));
      } else {
        banner.replaceChildren(alertBox(`Akademi durumu okunamadı: ${problem(failure)}`, 'bad'));
      }
    }
    if (disposed) return;
    menu = buildNav(navContext());
    main.replaceChildren();
    if (!menu[0]?.sections[0]) {
      paintNav();
      main.append(emptyState({ title: 'Görüntülenecek bölüm yok',
        text: 'Akademi bölümleri için yetkiniz yok.',
        actions: [button('Yenile', { onClick: () => boot() })] }));
      return;
    }
    start(ctx.payload || null);
  }

  /**
   * Açılış hedefi: derin bağlantı (`{section}`) varsa o ekran; yoksa ana sayfa
   * (tek grup görünüyorsa — ör. yalnız öğrenci yönetimi — doğrudan o grup).
   */
  function start(payload) {
    if (payload?.section && resolveTarget(menu, payload.section, payload)) {
      show(payload.section, payload);
    } else if (homeTiles(menu).length) {
      showHome();
    } else if (menu[0]?.sections[0]) {
      show(menu[0].sections[0].key);
    }
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

/** Koyu temaya hazır (ADR 0032): bütün panel CSS'i renk jetonlarıyla çizilir (63-duzeltici + F-DK ders kayıtları). */
export const darkReady = true;
