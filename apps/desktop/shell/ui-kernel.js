// ui-kernel — çekirdekle konuşan katman ve ekran keşfi.
//
// MENÜ İKİ KAYNAKTAN KURULUR (ADR 0017):
//
//  1. MODÜL EKRANLARI — kabuk hangilerinin var olduğunu BİLMEZ; sidecar'daki
//     çekirdeğe sorar (`GET /modules`). Kayıt, modüllerin `module.yaml` →
//     `ui.nav` bloklarından gelir ve kullanıcının izinlerine göre süzülür;
//     süzmeyi çekirdek yapar, kabuk yalnızca çizer (K1, K9). Panel dosyaları
//     `shell/panels/<id>/` altından servis edilir; oraya
//     `tools/build-ui-registry.py` kopyalar, kaynak her zaman modülün kendi
//     klasörüdür.
//
//  2. ÇEKİRDEK EKRANLARI — aşağıdaki `CORE_PANELS` sabit listesi. Bunlar modül
//     DEĞİLDİR: manifestleri yoktur, `registry.json`'a girmezler, kapatılamaz
//     ve `modules/` klasörü tümüyle silinse bile çalışırlar (ADR 0017 §4).
//     Dosyaları `shell/core-panels/<ad>/` altındadır ve kopyalanmaz.
//
// İki liste burada birleşir ve menüde TEK ağaç olur; çekirdek ekranları en
// altta kendi "Sistem" grubunda durur (ADR 0017 §2).
//
// BURADA MODÜL ADI YİNE GEÇMEZ. Eklenen şey modül adı değil, çekirdeğin kendi
// ekranıdır — ARCHITECTURE §6'nın kuralı korunur.

const BASE = 'http://127.0.0.1:8787';

let token = null;

export function setToken(value) {
  token = value;
}

export function hasToken() {
  return Boolean(token);
}

/**
 * Çekirdeğe istek. Oturum belirteci tek yerden eklenir.
 *
 * İSTEK KABUK ÜZERİNDEN GİDER. WebKit sayfayı `tauri://` ile güvenli köken
 * sayıyor ve oradan `http://127.0.0.1`'e gideni karışık içerik olarak kesiyor
 * ("Load failed"); Chromium loopback'i ayrık tutar, WebKitGTK tutmaz. Bu
 * yüzden isteği Rust tarafındaki `core_request` taşır.
 *
 * Tauri dışında (tarayıcıda tasarım denemesi) `fetch`e düşer.
 */
export async function api(path, options = {}) {
  const method = options.method || 'GET';
  const body = options.body === undefined ? null : JSON.stringify(options.body);

  const invoke = window.__TAURI__?.core?.invoke;
  let status;
  let text;

  if (invoke) {
    const response = await invoke('core_request', { method, path, body, token });
    status = response.status;
    text = response.body;
  } else {
    const headers = { Accept: 'application/json' };
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== null) headers['Content-Type'] = 'application/json';
    const response = await fetch(`${BASE}${path}`, { method, headers, body });
    status = response.status;
    text = await response.text();
  }

  if (status === 204 || text === '') return null;

  let payload = null;
  try {
    payload = JSON.parse(text);
  } catch {
    payload = null;
  }

  if (status >= 400) {
    const message = payload?.error?.message || payload?.detail || `İstek başarısız (${status}).`;
    const error = new Error(message);
    error.status = status;
    throw error;
  }
  return payload;
}

/**
 * Çekirdek ayakta mı? Kabuk açılışta bunu bekler.
 *
 * Başarısızlıkta SON HATAYI da döndürür: "başlatılamadı" demek yetmez, neden
 * ulaşılamadığı (süreç yok mu, istek mi engellendi) ekranda yazmalı.
 */
export async function waitForCore(timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = 'yanıt yok';
  while (Date.now() < deadline) {
    try {
      await api('/health');
      return { ok: true };
    } catch (error) {
      lastError = error?.message || String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 350));
  }
  return { ok: false, error: lastError };
}

/**
 * Giriş. TEK ALAN: şifre (ADR 0016 — kullanıcı adı yoktur, şifre hem kimliği
 * hem girişi belirler).
 *
 * İKİ SONUÇ döner ve ikisi de normaldir:
 *  · `{mustSetPassword: false, user}` — oturum açıldı, belirteç kuruldu.
 *  · `{mustSetPassword: true, user, message}` — sır DOĞRULANDI ama oturum
 *    AÇILMADI (ADR 0016 §7). Göç yolundaki kullanıcı önce kendi şifresini
 *    kurar; belirteç `setOwnPassword()` sonunda gelir.
 *
 * Hata gövdesi ele vermez: reddin sebebi (yanlış / kilitli / hiç yok) tek tip
 * 401'dir ve kabuk da tek tip cümle gösterir.
 */
export async function login(password) {
  const result = await api('/api/auth/login', { method: 'POST', body: { password } });
  if (result?.mustSetPassword) {
    return { mustSetPassword: true, user: result.user, message: result.message || '' };
  }
  setToken(result.token);
  return { mustSetPassword: false, user: result.user };
}

/**
 * Kullanıcının kendi şifresini kurması. `currentSecret` göç yolunda ESKİ
 * sırdır (girişte yazdığı şey), şifresi olanda mevcut şifresidir.
 *
 * Uç izin istemez; oturumun değil MEVCUT SIRRIN doğrulanmasıyla korunur —
 * şifresi kurulmamış kullanıcının zaten oturumu yoktur.
 */
export async function setOwnPassword(currentSecret, password) {
  const result = await api('/api/auth/set-password', {
    method: 'POST',
    body: { currentSecret, password },
  });
  setToken(result.token);
  return result.user;
}

/**
 * Oturumu kapatır.
 *
 * SUNUCUYA HABER VERİLİR, sonra belirteç düşürülür. Yalnız `setToken(null)`
 * demek, satırı `sessions` tablosunda boşta kalma süresi dolana kadar geçerli
 * bırakırdı; kapanmış sanılan bir oturumun anahtarı hâlâ çalışıyor olurdu.
 *
 * UÇ PATLASA DA BELİRTEÇ DÜŞER. Ağ koptuğunda ekranı açık bırakmak, kullanıcı
 * "çıktım" dedikten sonra onu girişte tutmaktan kötüdür; `finally` bu yüzden.
 */
export async function logout() {
  try {
    await api('/api/auth/logout', { method: 'POST' });
  } catch {
    /* merkez ya da çekirdek ulaşılmazsa da yerel oturum kapanır */
  } finally {
    setToken(null);
  }
}

// --------------------------------------------------------------- ekranlar

/**
 * ÇEKİRDEK EKRANLARI — sabit liste (ADR 0017 §1).
 *
 * `registry.json`'a girmezler ve manifest taramasından gelmezler: modül
 * değildirler. Menüde modüllerle aynı biçimde dururlar ama kendi grubunda ve
 * her zaman EN ALTTA (§2) — kullanıcı "bu ekran uygulamanın kendisine mi, bir
 * iş alanına mı ait" sorusunu menüye bakarak yanıtlar.
 *
 * `requires` yetkilendirme DEĞİLDİR, yalnız menü görünürlüğüdür; aynı izin
 * backend'de yeniden denetlenir (K9 — çift kapı). "Çekirdek olduğu için
 * güvenilir" diye bir istisna yoktur (§3).
 *
 * `entry` yoksa ekran menüde durur ve gövdesinde "ekranı henüz yok" kartı
 * çıkar — paneli yazılmamış çekirdek ekranının bugünkü hâli budur.
 */
export const CORE_PANELS = [
  {
    id: 'core_users',
    title: 'Kullanıcı Yönetimi',
    icon: 'users',
    order: 10,
    requires: ['users.view'],
    entry: 'core-panels/users/index.js',
  },
  {
    id: 'core_settings',
    title: 'Sistem Ayarları',
    icon: 'sliders',
    order: 20,
    requires: ['settings.view'],
    entry: 'core-panels/settings/index.js',
  },
  {
    id: 'core_health',
    title: 'Sistem Sağlığı',
    icon: 'pulse',
    order: 30,
    requires: ['settings.view'],
    entry: null,
  },
  {
    // ADR 0021 §4 — merkeze eşlenmiş makineler. Menüde görünmesi için
    // `installations.view`, kod üretmek/iptal etmek için `installations.manage`
    // gerekir; ikincisini ekran düğme düğme sorar ve backend yeniden denetler.
    id: 'core_pairing',
    title: 'KM Cihaz Eşle',
    icon: 'monitor',
    order: 40,
    requires: ['installations.view'],
    entry: 'core-panels/pairing/index.js',
  },
];

/** Çekirdek ekranlarının menü grubu. Modül grupları arasına karışmaz. */
const CORE_GROUP = 'Sistem';

/**
 * Kullanıcı bu izne sahip mi? Backend'deki `has_permission` ile AYNI kural:
 * kapsamsız anahtar birebir eşleşir, `anahtar:*` her kapsamı karşılar,
 * kapsamlı sorgu birebir de eşleşebilir.
 *
 * Bu bir yetki kapısı DEĞİLDİR — menüyü çizmek için gereken görünürlük
 * kararıdır. Yetki backend'dedir (K9).
 */
function allows(permissions, entry) {
  const cut = entry.indexOf(':');
  const key = cut === -1 ? entry : entry.slice(0, cut);
  const scope = cut === -1 ? null : entry.slice(cut + 1);
  if (permissions.has(key)) return scope === null;
  if (permissions.has(`${key}:*`)) return true;
  return scope !== null && permissions.has(`${key}:${scope}`);
}

/**
 * Ekran kaydı. Çekirdek modül raporunu döner; burada menüye uygun biçime
 * çevrilir ve çekirdek ekranları eklenir. Panel dosyası olmayan ekran boş
 * gövdeyle açılır.
 *
 * `user` giriş yanıtındaki özettir; `permissions` alanı çekirdek ekranlarının
 * görünürlüğünü süzer. Verilmezse çekirdek ekranı çizilmez — izin listesi
 * bilinmeden "görünsün" demek, K9'un arayüz tarafındaki kapısını açık
 * bırakmak olurdu.
 */
export async function loadRegistry(user = null) {
  const payload = await api('/modules');
  const panels = [];

  for (const entry of payload.modules || []) {
    const nav = entry.ui?.nav;
    if (!nav || entry.visible === false) continue;

    // ÇEKİRDEK EKRANI BURADAN GELMEZ (ADR 0017 §1). Çekirdek kendi ekranlarını
    // da bu uçta bildiriyor; menüdeki karşılıkları aşağıdaki sabit listeden
    // kurulur, yoksa aynı ekran iki kez çizilirdi.
    if (entry.source === 'core') continue;

    panels.push({
      id: entry.id,
      title: nav.title || entry.name || entry.id,
      icon: nav.icon || 'dot',
      group: nav.group || 'Diğer',
      order: nav.order ?? 1000,
      requires: nav.requires || [],
      source: entry.source || 'module',
      state: entry.state || 'loaded',
      reason: entry.reason || '',
      provides: entry.provides || [],
      // Panel dosyası modülün klasöründen kopyalanır; adres kabuk köküne göre.
      entry: entry.ui?.entry ? `panels/${entry.id}/${entry.ui.entry.split('/').pop()}` : null,
    });
  }

  // GRUP SIRASI VERİDEN TÜRETİLİR: grubun sırası, içindeki en küçük `order`
  // değeridir.
  //
  // BURADA SABİT BİR GRUP LİSTESİ DURUYORDU (`['BBD','BBD Store',…]`) ve
  // AYNISI `tools/build-ui-registry.py` içinde ikinci kez yazılıydı. İki kopya
  // birbirinden habersizdi: yalnız biri güncellenirse çalışma zamanı ile build
  // çıktısı sessizce ayrışırdı. Dahası, kabukta grup adı tutmak K1'e ters —
  // çekirdek hangi grupların var olduğunu bilmemeli.
  //
  // Artık yeni bir grup açmak saf `module.yaml` işidir (K6): `ui.nav.group`
  // adını, `ui.nav.order` da nereye düşeceğini söyler. Kabukta tek satır
  // değişmez.
  const groupRank = new Map();
  for (const panel of panels) {
    const current = groupRank.get(panel.group);
    if (current === undefined || panel.order < current) groupRank.set(panel.group, panel.order);
  }
  const rank = (name) => groupRank.get(name) ?? Number.MAX_SAFE_INTEGER;

  panels.sort((a, b) =>
    rank(a.group) - rank(b.group)
    // AYNI SIRA DEĞERİNDE AD KAZANIR: iki grup aynı `order`ı taşırsa sıra
    // rastgele kalmasın (eskiden `store_bundles` ile `store_shipping` ikisi de
    // 30'du ve menü sırası dosya sistemi sırasına kalmıştı).
    || a.group.localeCompare(b.group, 'tr')
    || a.order - b.order
    || a.title.localeCompare(b.title, 'tr'));

  const groups = [...new Set(panels.map((panel) => panel.group))];

  // ÇEKİRDEK EKRANLARI EN SONA EKLENİR — SAYIYLA DEĞİL, KURALLA.
  //
  // Modül grupları sıralarını veriden alıyor (gruptaki en küçük `order`).
  // Çekirdeğin de büyük bir `order` yazması yeterdi ama o sıra bir yarışa
  // dönerdi: yarın 9999 yazan bir `module.yaml` Sistem grubunu ortada
  // bırakırdı ve kimse nedenini aramazdı. ADR 0017 §2 "en altta" diyor;
  // burada olan tam olarak budur ve modüllerin ne yazdığından bağımsızdır.
  const permissions = new Set(user?.permissions || []);
  const core = CORE_PANELS
    .filter((panel) => panel.requires.some((entry) => allows(permissions, entry)))
    .sort((a, b) => a.order - b.order)
    .map((panel) => ({
      ...panel,
      group: CORE_GROUP,
      source: 'core',
      state: 'loaded',
      reason: '',
      provides: [],
    }));

  if (core.length) {
    panels.push(...core);
    groups.push(CORE_GROUP);
  }

  return { groups, panels };
}

/** Grup adındaki seviye ayracı: `"BBD Store / Satış ve Kargo"`. */
const LEVEL = ' / ';

/**
 * Menüyü İKİ SEVİYELİ ağaca çevirir.
 *
 * HİYERARŞİ GRUP ADININ KENDİSİNDEN GELİR. `ui.nav.group` şemada serbest
 * metindir; ayraç eklemek şema değişikliği istemez ve kabukta bir üst-alt
 * tablosu tutmayı da gerektirmez — çekirdek hangi başlıkların var olduğunu
 * bilmemeye devam eder (K1). Yeni bölüm açmak saf `module.yaml` işidir (K6).
 *
 * AYRACI OLMAYAN GRUP TEK SEVİYE KALIR: `"BBD"` düz bir başlıktır,
 * `"BBD Store / Katalog"` ise `BBD Store` başlığının altındaki `Katalog`
 * bölümüdür. Üst başlığın DOĞRUDAN çocukları da olabilir (`"BBD Store"`
 * grubundaki Kontrol Paneli) ve bunlar bölümlerin ÜSTÜNDE durur: günde
 * onlarca kez açılan bir ekranı bir bölümü açmaya zorlamak, her seferinde
 * fazladan bir tık demektir.
 *
 * Sıra `registry.groups` sırasını izler; boş bölüm hiç çizilmez, yani bir
 * modül silinince başlığı da kendiliğinden kaybolur (K6/K7).
 */
export function navTree(registry, panels) {
  const order = registry.groups.length
    ? registry.groups
    : [...new Set(panels.map((panel) => panel.group))];

  const tops = [];
  const index = new Map();

  for (const group of order) {
    const cut = group.indexOf(LEVEL);
    const topName = cut === -1 ? group : group.slice(0, cut);
    const sectionName = cut === -1 ? '' : group.slice(cut + LEVEL.length);
    const rows = panels.filter((panel) => panel.group === group);
    if (rows.length === 0) continue;

    let top = index.get(topName);
    if (!top) {
      top = { title: topName, panels: [], sections: [] };
      index.set(topName, top);
      tops.push(top);
    }
    if (sectionName) top.sections.push({ title: sectionName, panels: rows });
    else top.panels.push(...rows);
  }

  return tops;
}

/** Ekranları menü gruplarına ayırır (tek seviye — rapor ve testler kullanır). */
export function groupPanels(registry, panels) {
  const order = registry.groups.length
    ? registry.groups
    : [...new Set(panels.map((panel) => panel.group))];

  return order
    .map((group) => ({ group, panels: panels.filter((panel) => panel.group === group) }))
    .filter((entry) => entry.panels.length > 0);
}

// -------------------------------------------------------------- yetenekler

const providers = new Map();
let providersReady = null;

/**
 * Panellerin ilan ettiği arayüz yetenekleri.
 *
 * K3: modül modülü import etmez. Veriyi veren modülün paneli
 * `capabilities(ctx)` dışa vurur, isteyen `ctx.capability(ad)` ile çözer.
 * Sağlayıcı panelin açık olması gerekmez — dosya gerektiğinde yüklenir.
 */
async function ensureProviders(registry) {
  if (providersReady) return providersReady;

  providersReady = (async () => {
    const sources = (registry.panels || [])
      .filter((panel) => panel.entry && (panel.provides || []).length);

    // DOSYALAR AYNI ANDA İSTENİR. Döngüdeki `await import(...)` her paneli bir
    // öncekinin inmesini bekletiyordu; aralarında hiçbir bağ yok ve açılıştaki
    // gecikme sağlayıcı sayısıyla doğru orantılı büyüyordu.
    //
    // K7 KORUNUR: her yükleme kendi `catch`ini taşır, bu yüzden `Promise.all`
    // reject etmez — patlayan bir panel ötekilerin yeteneklerini düşürmez.
    const loaded = await Promise.all(sources.map(async (panel) => {
      try {
        return { panel, module: await import(`./${panel.entry}`) };
      } catch (error) {
        console.error(`[ui-kernel] ${panel.id} yetenekleri okunamadı:`, error);
        return null;
      }
    }));

    // KAYIT SIRALI KALIR — yükleme paralel, kayıt değil. Aynı adı iki panel
    // ilan ederse kazanan kayıttaki SON paneldir; "önce inen kazansın"
    // deseydik hangi sağlayıcının geçerli olduğu açılıştan açılışa değişirdi.
    for (const entry of loaded) {
      if (!entry) continue;
      const { panel, module } = entry;
      try {
        // GEZİNME DE VERİLİR. Bir yetenek başka ekranın içine çiziliyor ve
        // oradan "asıl evinde aç" demesi gerekiyor; `open` verilmezse o düğme
        // sessizce hiçbir şey yapmayan bir düğme olurdu — kitin kendi kuralı
        // bunu yasaklıyor ("bir düğme YA ÇALIŞIR, YA BURADAN GEÇER, ya da hiç
        // çizilmez"). Yüzey `mount` ile aynı: aynı sözleşmeyi iki ayrı biçimde
        // öğrenmek gerekmesin.
        const declared = typeof module.capabilities === 'function'
          ? module.capabilities({
            api,
            open: (id, data = null) => {
              if (!navigate) {
                console.warn('[ui-kernel] gezinme bağlanmadı; open() yok sayıldı');
                return;
              }
              navigate(id, data);
            },
          })
          : {};
        for (const [name, factory] of Object.entries(declared || {})) {
          if (!panel.provides.includes(name)) {
            console.warn(`[ui-kernel] ${panel.id}: '${name}' manifestte ilan edilmemiş, atlandı`);
            continue;
          }
          providers.set(name, factory);
        }
      } catch (error) {
        console.error(`[ui-kernel] ${panel.id} yetenekleri okunamadı:`, error);
      }
    }
  })();

  return providersReady;
}

/**
 * Panelden panele gezinme.
 *
 * Kabuk hangi panellerin var olduğunu bilir ama HANGİSİNİN HANGİSİNE
 * gideceğini bilmez — kimliği çağıran panel verir (K1). "Siparişler'den
 * Kargo'ya geç" kararı Siparişler panelinindir.
 */
let navigate = null;

/** Kabuk açılışta kendi `select` fonksiyonunu buraya bağlar. */
export function setNavigator(fn) {
  navigate = fn;
}

/**
 * Modül panelini yükler ve gövdeye bağlar.
 *
 * SÖZLEŞME:
 *     export function mount(root, ctx) { …; return () => { /* temizlik *\/ } }
 *
 * `ctx`:
 *   · `panel`      — ekran künyesi
 *   · `api`        — çekirdeğe istek (oturum belirteci eklenmiş)
 *   · `capability` — başka modülün ilan ettiği yetenek; yoksa null
 *   · `payload`    — başka panelin `open()` ile gönderdiği veri; yoksa null
 *   · `open`       — başka panele geç: `open('store_shipping', {orderId: 12})`
 *
 * Paneli olmayan ya da patlayan ekran boş açılır; kabuk düşmez (K7).
 */
export async function mountPanel(panel, root, registry = { panels: [] }, payload = null) {
  // ARAYÜZÜ OLMAYAN EKRAN DA AÇIKLANIR. Burası eskiden sessizce `null`
  // dönüyordu; `select()` gövdeyi zaten temizlediği için kullanıcı başlığı
  // değişmiş BOMBOŞ bir ekrana bakıyordu. `panelError` aynı dersi çökme dalı
  // için öğrenmişti (aşağıdaki yorum), giriş-yok dalı atlanmıştı.
  if (!panel.entry) {
    root.replaceChildren(panelUnavailable(panel));
    return null;
  }

  try {
    await ensureProviders(registry);
    const module = await import(`./${panel.entry}`);
    if (typeof module.mount !== 'function') {
      console.warn(`[ui-kernel] ${panel.id}: mount() dışa vurulmamış`);
      return null;
    }
    const cleanup = module.mount(root, {
      panel,
      api,
      payload,
      capability: (name) => providers.get(name) ?? null,
      open: (id, data = null) => {
        if (!navigate) {
          console.warn('[ui-kernel] gezinme bağlanmadı; open() yok sayıldı');
          return;
        }
        navigate(id, data);
      },
    });
    return typeof cleanup === 'function' ? cleanup : null;
  } catch (error) {
    console.error(`[ui-kernel] ${panel.id} paneli yüklenemedi:`, error);
    root.replaceChildren(panelError(panel, error));
    return null;
  }
}

/**
 * Arayüzü olmayan / yüklenmemiş panelin yerine geçen kart.
 *
 * İKİ AYRI DURUM, İKİ AYRI CÜMLE — çünkü kullanıcının yapacağı iş farklı:
 *  · Modül KAPALI ya da yüklenememiş (`state !== 'loaded'`): nedeni çekirdek
 *    söyler (`reason`) ve olduğu gibi gösterilir; tahmin yürütülmez.
 *  · Modül ayakta ama ARAYÜZÜ YOK (`entry` boş): özellik henüz ekrana
 *    bağlanmamıştır. "Hata" demek yanlış olurdu — kırılan bir şey yok.
 *
 * Kırmızı kullanılmaz: bu bir arıza değil, bir eksik. Kırmızı kart kullanıcıyı
 * olmayan bir hatayı aramaya gönderirdi.
 */
function panelUnavailable(panel) {
  const yuklenmedi = Boolean(panel.state && panel.state !== 'loaded');

  const box = document.createElement('div');
  box.style.cssText = 'margin:24px;padding:20px 22px;border:1px solid #2a3346;'
    + 'border-radius:10px;background:#141a26;color:#c7d0e2;max-width:760px;'
    + 'font:13px/1.6 system-ui,sans-serif';

  const title = document.createElement('h3');
  title.textContent = yuklenmedi
    ? `“${panel.title || panel.id}” şu an kullanılamıyor`
    : `“${panel.title || panel.id}” ekranı henüz yok`;
  title.style.cssText = 'margin:0 0 8px;font-size:15px;color:#e9eefa';

  const hint = document.createElement('p');
  hint.textContent = yuklenmedi
    ? 'Bu bölüm kapalı olduğu için açılamıyor. Uygulamanın geri kalanı '
      + 'normal çalışıyor.'
    : 'Bu bölümün arayüzü henüz hazırlanmadı. Bir arıza değil; özellik '
      + 'listede duruyor ama ekranı yazılmamış. Uygulamanın geri kalanı '
      + 'normal çalışıyor.';
  hint.style.margin = '0 0 10px';
  box.append(title, hint);

  // NEDEN VARSA OLDUĞU GİBİ GÖSTERİLİR: çekirdeğin yazdığı gerekçeyi kendi
  // cümlemizle değiştirmek gerçek sebebi gizler.
  if (yuklenmedi && panel.reason) {
    const detail = document.createElement('p');
    detail.textContent = panel.reason;
    detail.style.cssText = 'margin:0;padding:10px 12px;border-radius:6px;'
      + 'background:#0d1220;color:#8b95ad;font-size:12.5px';
    box.append(detail);
  }

  return box;
}

/**
 * Patlayan panelin yerine geçen kart.
 *
 * ÖNCEDEN BURASI EKRANI BOŞALTIYORDU. K7 "modül patlarsa kabuk düşmesin" der;
 * "kullanıcı bomboş bir ekrana baksın" demez. Sessiz boş ekran, hatanın
 * kendisinden daha pahalıdır — kimse konsolu açıp bakmaz, ekran bozuk sanılır.
 */
function panelError(panel, error) {
  const box = document.createElement('div');
  box.className = 'km-panel-error';
  box.style.cssText = 'margin:24px;padding:20px 22px;border:1px solid #e5484d;'
    + 'border-radius:10px;background:#2a1417;color:#ffd7d9;max-width:760px;'
    + 'font:13px/1.6 system-ui,sans-serif';

  const title = document.createElement('h3');
  title.textContent = `“${panel.title || panel.id}” ekranı açılamadı`;
  title.style.cssText = 'margin:0 0 8px;font-size:15px;color:#ff9ea3';

  const hint = document.createElement('p');
  hint.textContent = 'Bu ekran bir hata verdi; uygulamanın geri kalanı çalışmaya '
    + 'devam ediyor. Aşağıdaki satır sorunun teknik karşılığıdır.';
  hint.style.margin = '0 0 10px';

  const detail = document.createElement('pre');
  detail.textContent = `${error?.name || 'Hata'}: ${error?.message || String(error)}`;
  detail.style.cssText = 'margin:0;padding:10px 12px;border-radius:6px;'
    + 'background:#1b0e10;color:#ffb4b8;white-space:pre-wrap;word-break:break-word;'
    + 'font:12px/1.5 ui-monospace,monospace';

  box.append(title, hint, detail);
  return box;
}
