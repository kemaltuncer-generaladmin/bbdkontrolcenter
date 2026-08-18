// Kontrol Merkezi — kabuk davranışı.
//
// Kabuk hiçbir modülün adını bilmez (K1): menü, çekirdeğin verdiği kayıttan
// kurulur. Giriş de çekirdeğe aittir — PIN sidecar'daki `POST /api/auth/login`
// ucunda Argon2id ile doğrulanır, kabuk yalnızca sorar ve belirteci taşır.
//
// ADR 0016 (kişiye özel şifreyle giriş) REDDEDİLDİ: giriş yine 6 haneli PIN'dir
// ve ekran tuş takımıdır. Uçtaki alan adı (`password`) ve `users.set_password`
// izni, göç zaten koştuğu için olduğu gibi bırakıldı — ad şifre der, kural
// PIN'dir (docs/adr/0016-giris-sifre-ile.md — Neden reddedildi).

import { iconSvg } from './icons.js';
import {
  api, loadRegistry, login, logout, mountPanel, navTree, setNavigator, waitForCore,
} from './ui-kernel.js';

const PIN_MIN = 6;
const PIN_MAX = 12; // yerleşim sınırı; sözleşmede üst sınır yok

const el = {
  login: document.getElementById('login'),
  slots: document.getElementById('slots'),
  error: document.getElementById('error'),
  loginHint: document.getElementById('login-hint'),
  workspace: document.getElementById('workspace'),
  nav: document.getElementById('nav'),
  filter: document.getElementById('filter'),
  crumb: document.getElementById('crumb'),
  title: document.getElementById('panel-title'),
  foot: document.querySelector('.sidebar-foot'),
};

function fold(text) {
  return text
    .toLocaleLowerCase('tr')
    .replaceAll('ı', 'i').replaceAll('ş', 's').replaceAll('ğ', 'g')
    .replaceAll('ü', 'u').replaceAll('ö', 'o').replaceAll('ç', 'c');
}

const shell = {
  registry: { groups: [], panels: [] },
  panels: [],
  activeId: null,
  cleanup: null,
  user: null,
};

// ================================================================== KABUK

/** Açık bölümler kullanıcıya özeldir ve uygulama kapanınca kaybolmaz. */
const OPEN_KEY = 'km.nav.open';

function openSet() {
  try {
    const raw = JSON.parse(localStorage.getItem(OPEN_KEY) || '[]');
    return new Set(Array.isArray(raw) ? raw : []);
  } catch {
    return new Set();
  }
}

function saveOpen(set) {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify([...set]));
  } catch {
    /* depolama kapalıysa menü yine çalışır, yalnız hatırlamaz */
  }
}

function navButton(panel) {
  const item = document.createElement('button');
  item.type = 'button';
  item.className = 'nav-item';
  item.dataset.id = panel.id;
  item.setAttribute('aria-current', panel.id === shell.activeId ? 'page' : 'false');
  item.classList.toggle('active', panel.id === shell.activeId);

  const label = document.createElement('span');
  label.textContent = panel.title;
  item.append(iconSvg(panel.icon), label);

  // Yüklenmemiş modül menüde durur ama durumu belli olur.
  if (panel.state && panel.state !== 'loaded') {
    item.classList.add('idle');
    item.title = panel.reason || 'Modül yüklenmedi.';
  }

  item.addEventListener('click', () => select(panel.id));
  return item;
}

/**
 * Kenar çubuğu — iki seviyeli ve katlanabilir.
 *
 * NEDEN KATLANIYOR. Elli bir ekran düz çizildiğinde şerit ~1800px oluyor ve
 * kenar çubuğu ~800px görüyor; kullanıcı her gidişte kaydırıyordu. Katlanınca
 * aynı anda on satır görünür.
 *
 * ARAMA KATLAMAYI EZER: bir bölümde eşleşme varsa o bölüm geçici olarak
 * açılır. Aksi hâlde aranan ekran kapalı bir başlığın içinde kalır ve arama
 * "sonuç yok" der gibi görünürdü.
 *
 * ETKİN EKRANIN BÖLÜMÜ HER ZAMAN AÇIKTIR: kullanıcının nerede olduğunu
 * gizlemek, menüyü bir bilmeceye çevirir.
 */
function renderNav(query = '') {
  const needle = fold(query.trim());
  const matches = needle
    ? shell.panels.filter((panel) => fold(panel.title).includes(needle)
      || fold(panel.group).includes(needle))
    : shell.panels;

  el.nav.replaceChildren();

  if (matches.length === 0) {
    el.nav.append(message(shell.panels.length ? 'Eşleşen ekran yok.' : 'Görünür ekran yok.'));
    return;
  }

  const open = openSet();
  const aramaVar = Boolean(needle);

  for (const top of navTree(shell.registry, matches)) {
    const bolumler = top.sections;
    // Alt bölümü olmayan üst başlık ESKİSİ GİBİ düz çizilir: tek ekranlık bir
    // grubu katlanabilir yapmak, tıklanacak bir şey olmadan tık istemektir.
    if (bolumler.length === 0) {
      const heading = document.createElement('p');
      heading.className = 'nav-group';
      heading.textContent = top.title;
      el.nav.append(heading);
      for (const panel of top.panels) el.nav.append(navButton(panel));
      continue;
    }

    const heading = document.createElement('p');
    heading.className = 'nav-group';
    heading.textContent = top.title;
    el.nav.append(heading);

    // Üst başlığın doğrudan ekranları bölümlerin ÜSTÜNDE durur.
    for (const panel of top.panels) el.nav.append(navButton(panel));

    for (const section of bolumler) {
      const key = `${top.title}${' / '}${section.title}`;
      const etkin = section.panels.some((panel) => panel.id === shell.activeId);
      const acik = aramaVar || etkin || open.has(key);

      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = `nav-section${acik ? ' open' : ''}`;
      toggle.setAttribute('aria-expanded', acik ? 'true' : 'false');

      const caret = document.createElement('span');
      caret.className = 'nav-caret';
      caret.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = section.title;
      const count = document.createElement('span');
      count.className = 'nav-count';
      count.textContent = String(section.panels.length);
      toggle.append(caret, label, count);

      toggle.addEventListener('click', () => {
        const now = openSet();
        if (now.has(key)) now.delete(key);
        else now.add(key);
        saveOpen(now);
        renderNav(el.filter?.value || '');
      });
      el.nav.append(toggle);

      if (!acik) continue;
      for (const panel of section.panels) {
        const item = navButton(panel);
        item.classList.add('nav-nested');
        el.nav.append(item);
      }
    }
  }
}

function message(text) {
  const node = document.createElement('p');
  node.className = 'nav-empty';
  node.textContent = text;
  return node;
}

/**
 * Ekran değiştirir.
 *
 * `payload`: başka panelin `ctx.open()` ile gönderdiği veri.
 * `force`: aynı panele veriyle yeniden girmek için — "bu siparişi aç"
 *   denildiğinde zaten Siparişler ekranındaysak da yeniden bağlanmalı.
 */
async function select(id, { scroll = false, payload = null, force = false } = {}) {
  const panel = shell.panels.find((candidate) => candidate.id === id);
  if (!panel) return;
  if (id === shell.activeId && !force) return;

  try {
    shell.cleanup?.();
  } catch (error) {
    console.error('[kabuk] panel temizliği hata verdi:', error);
  }
  shell.cleanup = null;
  shell.activeId = id;

  for (const item of el.nav.querySelectorAll('.nav-item')) {
    const active = item.dataset.id === id;
    item.classList.toggle('active', active);
    item.setAttribute('aria-current', active ? 'page' : 'false');
    if (active && scroll) item.scrollIntoView({ block: 'nearest' });
  }

  el.crumb.textContent = panel.group;
  el.title.textContent = panel.title;
  document.title = `${panel.title} — Kontrol Merkezi`;

  const body = document.getElementById('panel-body');
  body.replaceChildren();

  const cleanup = await mountPanel(panel, body, shell.registry, payload);
  if (shell.activeId === id) shell.cleanup = cleanup;
  else cleanup?.();
}

function step(delta) {
  const visible = [...el.nav.querySelectorAll('.nav-item')].map((item) => item.dataset.id);
  if (visible.length === 0) return;
  const index = visible.indexOf(shell.activeId);
  const next = index === -1
    ? (delta > 0 ? 0 : visible.length - 1)
    : (index + delta + visible.length) % visible.length;
  select(visible[next], { scroll: true });
}

async function openWorkspace(user) {
  shell.user = user;
  // Kullanıcı ÇEKİRDEK EKRANLARININ görünürlüğü için verilir: modül ekranlarını
  // sidecar süzer, çekirdek ekranlarının listesi kabukta durur (ADR 0017 §1) ve
  // izin süzgeci de burada uygulanır. Her ikisinin de arkasında backend'in
  // kendi denetimi vardır (K9).
  shell.registry = await loadRegistry(user);
  shell.panels = shell.registry.panels;

  el.foot.replaceChildren();
  const who = document.createElement('span');
  who.className = 'sidebar-user';
  who.textContent = user.fullName;
  who.title = `Roller: ${user.roles.join(', ') || '—'}`;

  const cikis = document.createElement('button');
  cikis.type = 'button';
  cikis.className = 'sidebar-signout';
  cikis.textContent = 'Çıkış Yap';
  cikis.title = 'Oturumu kapat';
  cikis.addEventListener('click', signOut);

  el.foot.append(who, cikis);

  // Paneller birbirine `ctx.open(id, payload)` ile gezinir. Kabuk yalnız
  // taşıyıcıdır: hangi panelin hangisine gittiğini bilmez (K1).
  setNavigator((id, payload) => select(id, { payload, force: true, scroll: true }));

  renderNav();
  if (shell.panels.length > 0) await select(shell.panels[0].id);

  el.login.hidden = true;
  el.workspace.hidden = false;
  el.filter.focus();
}

/**
 * Oturumu kapatır ve giriş ekranına döner.
 *
 * EKRAN ÖNCE SÖKÜLÜR, sonra belirteç düşer. Etkin panelin `cleanup`'ı
 * çalışmazsa açık kalan zamanlayıcıları ve dinleyicileri, artık yetkisi
 * olmayan bir oturumun ardından istek atmaya devam ederdi.
 *
 * MENÜ VE KAYIT DA BOŞALTILIR. Yalnız giriş kartını göstermek, bir sonraki
 * kişinin ekranında bir öncekinin ekran listesini bir an için bırakırdı —
 * ekran adları da yetki bilgisidir.
 */
async function signOut() {
  try {
    shell.cleanup?.();
  } catch (error) {
    console.error('[kabuk] panel temizliği hata verdi:', error);
  }
  shell.cleanup = null;
  shell.activeId = null;
  shell.user = null;
  shell.registry = { groups: [], panels: [] };
  shell.panels = [];

  await logout();

  document.getElementById('panel-body').replaceChildren();
  el.nav.replaceChildren();
  el.foot.replaceChildren();
  el.filter.value = '';
  el.crumb.textContent = '';
  el.title.textContent = '';
  document.title = 'Kontrol Merkezi';

  el.workspace.hidden = true;
  el.login.hidden = false;
  clearError();
  resetPin();
  setHint(LOGIN_HINT);
}

// ================================================================== GİRİŞ
//
// TUŞ TAKIMI. Kullanıcı adı sorulmaz — 6 haneli PIN hem kimliği hem girişi
// belirler (docs/identity-model.md — Giriş akışı). Metin alanı YOKTUR: rakamlar
// pencere tuş dinleyicisinden toplanır, ekranda yalnız hane SAYISI görünür.
//
// EKRANIN TEK KİPİ VAR: PIN yazılır, oturum açılır ya da tek tip redde varılır.
//
// ESKİDEN İKİ KİP DAHA VARDI ('set' / 'repeat'): reddedilmiş ADR 0016'nın "önce
// kendine sır belirle" adımı. O akış 17.08.2026'da gerçek bir kurulumu kilitledi
// — kullanıcı orijinal PIN'iyle girdi, akış yerine YENİ bir sır yazdı ve eski
// PIN o günden sonra reddedildi. Kalıntı tümüyle kaldırıldı; kullanıcı kendi
// PIN'ini yalnız İSTEYEREK değiştirir (mevcut PIN'ini girerek).

/** İpucu satırının parçaları: [metin, tuş?, kuyruk?]. */
const LOGIN_HINT = ["PIN'inizi yazın, ", 'Enter', ' ile girin'];

let pin = '';
let busy = false;

/**
 * GİRİŞ REDDİNİN TEK CÜMLESİ.
 *
 * Sebep AYIRT EDİLMEZ: PIN yanlış mı, hesap kilitli mi, o PIN kimseye ait değil
 * mi — üçü de aynı cümleyi verir. Farklı cümle yazmak, deneme yoluyla "bu PIN
 * birine ait" bilgisini sızdırırdı; backend de aynı nedenle tek tip 401
 * döndürüyor (`km_core/http/users.py`).
 */
const LOGIN_REJECTED = 'Giriş yapılamadı.';

function renderSlots() {
  const count = Math.max(PIN_MIN, pin.length);
  if (el.slots.childElementCount !== count) {
    el.slots.replaceChildren(
      ...Array.from({ length: count }, (_, i) => {
        const dot = document.createElement('span');
        dot.className = i < PIN_MIN ? 'slot' : 'slot extra';
        return dot;
      }),
    );
  }
  for (const [i, dot] of [...el.slots.children].entries()) {
    dot.classList.toggle('filled', i < pin.length);
  }
  el.slots.setAttribute('aria-label', `Girilen hane sayısı: ${pin.length}`);
}

function clearError() {
  el.error.classList.remove('show');
  el.error.textContent = '';
}

function failLogin(text = LOGIN_REJECTED) {
  el.error.textContent = text;
  el.error.classList.add('show');
  el.slots.classList.remove('shake');
  void el.slots.offsetWidth;
  el.slots.classList.add('shake');
  pin = '';
  renderSlots();
}

/**
 * İpucu satırı.
 *
 * METİN DÜĞÜM OLARAK YAZILIR, `innerHTML` İLE DEĞİL. Buradan ÇEKİRDEKTEN GELEN
 * veri de geçiyor (sunucunun "PIN belirleyin" cümlesi, son bağlantı hatası);
 * `innerHTML` olsaydı o veri HTML olarak yorumlanırdı. Tuş rozeti ihtiyacı bu
 * yüzden ayrı bir parça olarak alınır.
 */
function setHint([text = '', key = '', tail = '']) {
  el.loginHint.replaceChildren(document.createTextNode(text));
  if (!key) return;
  const rozet = document.createElement('kbd');
  rozet.textContent = key;
  el.loginHint.append(rozet, document.createTextNode(tail));
}

function resetPin() {
  pin = '';
  renderSlots();
}

async function enterWorkspace(user) {
  resetPin();
  el.login.classList.add('leaving');
  try {
    await openWorkspace(user);
  } finally {
    // `leaving` SINIFI HER DURUMDA KALKAR. Kart `opacity: 0` ve
    // `pointer-events: none` alıyor; menü kurulamadığında (ör. `/modules`
    // patlarsa) orada bırakmak, kullanıcıyı görünmez ve tıklanamaz bir giriş
    // ekranına kilitlerdi — hata mesajı yazılır ama kimse okuyamazdı.
    el.login.classList.remove('leaving');
  }
}

async function submitPin() {
  if (busy || pin.length < PIN_MIN) return;

  const entered = pin;
  busy = true;
  clearError();
  setHint(['Doğrulanıyor…']);
  el.loginHint.classList.add('busy');

  try {
    const result = await login(entered);
    await enterWorkspace(result.user);
  } catch (error) {
    if (error.status >= 400 && error.status < 500) {
      // 401 TEK TİPTİR (`LOGIN_REJECTED`) — sebep ayırt edilmez. Diğer 4xx
      // çekirdeğin kendi cümlesiyle gösterilir; o cümleler sır hakkında değil,
      // isteğin biçimi hakkındadır ve hiçbir şey ele vermez.
      failLogin(error.status === 401 ? LOGIN_REJECTED : error.message);
    } else {
      failLogin('Çekirdeğe ulaşılamadı.');
    }
  } finally {
    busy = false;
    el.loginHint.classList.remove('busy');
    // Giriş ekranı kapandıysa dokunulmaz: kullanıcı artık menüdedir.
    if (!el.login.hidden) setHint(LOGIN_HINT);
  }
}

function onLoginKey(event) {
  if (event.key >= '0' && event.key <= '9') {
    if (busy || pin.length >= PIN_MAX) return;
    clearError();
    pin += event.key;
    renderSlots();
  } else if (event.key === 'Backspace') {
    if (busy || pin.length === 0) return;
    clearError();
    pin = pin.slice(0, -1);
    renderSlots();
  } else if (event.key === 'Escape') {
    if (busy) return;
    clearError();
    pin = '';
    renderSlots();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    submitPin();
  }
}

// ================================================================= OLAYLAR

el.filter.addEventListener('input', () => renderNav(el.filter.value));

el.filter.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    el.filter.value = '';
    renderNav();
    el.filter.blur();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    el.filter.blur();
  }
});

function isTyping(target) {
  return target instanceof HTMLElement
    && (target.isContentEditable
      || target.tagName === 'INPUT'
      || target.tagName === 'TEXTAREA'
      || target.tagName === 'SELECT');
}

window.addEventListener('keydown', (event) => {
  if (event.ctrlKey || event.altKey || event.metaKey) return;

  if (!el.login.hidden) {
    onLoginKey(event);
    return;
  }

  const typing = isTyping(event.target);

  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    if (typing && event.target !== el.filter) return;
    event.preventDefault();
    step(event.key === 'ArrowDown' ? 1 : -1);
    return;
  }

  if (typing) return;

  if (event.key === '/') {
    event.preventDefault();
    el.filter.focus();
    el.filter.select();
  }
});

window.addEventListener('contextmenu', (event) => event.preventDefault());

// ================================================================ EŞLEME
//
// ADR 0021 §4 — CİHAZ EŞLEMESİ. Eşleşmemiş bir kurulum giriş ekranını hiç
// görmez: önce "bu makine bizim" demesi gerekir. Yönetici merkezde tek
// kullanıcılık, süreli bir kod üretir; kod buraya girilir ve kurulum karşılığında
// kendi token'ını alır.
//
// EŞLEME TOKEN'I OTURUM DEĞİLDİR. Kod girildikten sonra kullanıcı yine kendi
// PIN'ini yazar; aşağıdaki tuş takımına ve giriş akışına DOKUNULMAZ. Bu bölüm
// yalnızca onun ÖNÜNE eklenir ve iş bitince kendini tümüyle söker.
//
// YENİ CSS YOK: ekran, giriş kartının sınıflarını (`login`, `login-card`,
// `slots`) yeniden kullanır. Kod da PIN gibi rakamdır ve tuş takımıyla girilir —
// metin alanı açmak, ekranın kendi diline yabancı bir kutu koymak olurdu.

const PAIR_LENGTH = 8;

// Eşleme etkinken DOM düğümlerini ve durumu taşır; kapanınca null olur.
let pairing = null;

/**
 * Çekirdeğe eşleme durumunu sorar.
 *
 * HATA "EŞLEME GEREKMİYOR" SAYILIR. Uç yoksa, yetenek kapalıysa ya da istek
 * düşerse kurulum bugünkü gibi tek makinede açılmalıdır (ADR 0021 — Sonuçlar):
 * merkez yüzünden giriş ekranını göstermemek, tam da o kararın yasakladığı
 * gerilemedir.
 */
async function pairingRequired() {
  try {
    const state = await api('/api/pairing/state');
    return Boolean(state?.pairingRequired);
  } catch {
    return false;
  }
}

function pairSlots() {
  const dots = [...pairing.slots.children];
  for (const [i, dot] of dots.entries()) {
    dot.classList.toggle('filled', i < pairing.code.length);
  }
  pairing.slots.setAttribute('aria-label', `Girilen hane sayısı: ${pairing.code.length}`);
}

function pairFail(text) {
  pairing.error.textContent = text;
  pairing.error.classList.add('show');
  pairing.slots.classList.remove('shake');
  void pairing.slots.offsetWidth;
  pairing.slots.classList.add('shake');
  pairing.code = '';
  pairSlots();
}

function pairHint(text) {
  // Metin DÜĞÜM olarak yazılır, `innerHTML` ile değil: buradan çekirdeğin ve
  // merkezin cümleleri geçiyor (giriş ekranındaki `setHint` ile aynı gerekçe).
  pairing.hint.replaceChildren(document.createTextNode(text));
}

async function submitPairCode() {
  if (pairing.busy || pairing.code.length !== PAIR_LENGTH) return;

  const code = pairing.code;
  pairing.busy = true;
  pairing.error.classList.remove('show');
  pairing.error.textContent = '';
  pairHint('Eşleniyor…');

  try {
    await api('/api/pairing/pair', { method: 'POST', body: { code } });

    // EŞLEMEDEN SONRA UYGULAMA YENİDEN BAŞLAR — ve bu bir kolaylık değil,
    // zorunluluk.
    //
    // Modüller çekirdek açılırken yükleniyor; eşlemeyle inen geçit ayarları
    // (BLD, kantin, mağaza adresleri) ise eşleme ANINDA geliyor. Yani ilk
    // eşlemede modüller o ayarları göremeden yüklenmiş oluyor: kantin geçidi
    // "adres yok" diyerek hiç yüklenmiyor, BLD geçidi boş adresle ve salt
    // okunur açılıyor. Ölçüldü — yeniden başlatınca üçü de doğru bağlanıyor
    // ve 49/49 modül sorunsuz yükleniyor.
    //
    // KULLANICIYA "yeniden başlatın" DEMİYORUZ: okumasına bağlı kalırdı,
    // okumazsa ekranlar boş gelir ve sebebi hiçbir yerde görünmezdi. Eşleme
    // girişten önce, kimse çalışmaya başlamadan yapılıyor — kaybolacak iş yok.
    //
    // TARAYICIDA (Tauri dışında) komut yoktur: orada ekran yalnız kapanır ve
    // bugünkü davranış sürer.
    pairHint('Kurulum tamamlandı — uygulama yeniden başlatılıyor…');
    const invoke = window.__TAURI__?.core?.invoke;
    if (invoke) {
      try {
        await invoke('restart_app');
        return;
      } catch (error) {
        console.error('[kabuk] yeniden başlatılamadı:', error);
      }
    }
    pairing.done();
  } catch (error) {
    // MERKEZİN CÜMLESİ OLDUĞU GİBİ GÖSTERİLİR: kod geçersiz mi, merkeze mi
    // ulaşılamıyor, deneme sınırına mı takıldı — üçü ayrı ekran davranışı
    // ister ve kendi cümlemizle değiştirmek durumu gizlerdi.
    pairFail(error.message || 'Eşleme yapılamadı.');
    pairing.busy = false;
    pairHint(`Merkezden aldığınız ${PAIR_LENGTH} haneli kodu yazın.`);
  }
}

function onPairKey(event) {
  if (event.ctrlKey || event.altKey || event.metaKey) return;

  if (event.key >= '0' && event.key <= '9') {
    if (pairing.busy || pairing.code.length >= PAIR_LENGTH) return;
    pairing.error.classList.remove('show');
    pairing.code += event.key;
    pairSlots();
  } else if (event.key === 'Backspace') {
    if (pairing.busy || pairing.code.length === 0) return;
    pairing.code = pairing.code.slice(0, -1);
    pairSlots();
  } else if (event.key === 'Escape') {
    if (pairing.busy) return;
    pairing.code = '';
    pairSlots();
  } else if (event.key === 'Enter') {
    event.preventDefault();
    submitPairCode();
  } else {
    return;
  }
  // OLAY AŞAĞI GEÇMEZ. Dinleyici YAKALAMA evresindedir; giriş ekranının kendi
  // tuş dinleyicisi bu sırada hiç çalışmaz ve ona tek satır dokunulmamış olur.
  event.stopPropagation();
}

/** Eşleme ekranını kurar ve kod girilene kadar bekleyen bir söz döndürür. */
function runPairing() {
  const section = document.createElement('section');
  section.className = 'login';

  const aurora = document.createElement('div');
  aurora.className = 'aurora';
  aurora.setAttribute('aria-hidden', 'true');

  const card = document.createElement('div');
  card.className = 'login-card';

  // Marka işareti giriş kartından KOPYALANIR: SVG'yi elle kurmak aynı çizimi
  // ikinci kez yazmak olurdu.
  const mark = document.querySelector('#login .mark');
  if (mark) card.append(mark.cloneNode(true));

  const heading = document.createElement('h1');
  heading.textContent = 'Kontrol Merkezi';
  const org = document.createElement('p');
  org.className = 'org';
  org.textContent = 'Bu kurulum henüz eşlenmedi';

  const slots = document.createElement('div');
  slots.className = 'slots';
  slots.setAttribute('role', 'img');
  slots.append(...Array.from({ length: PAIR_LENGTH }, () => {
    const dot = document.createElement('span');
    dot.className = 'slot';
    return dot;
  }));

  const error = document.createElement('p');
  error.className = 'error';
  error.setAttribute('role', 'alert');
  error.setAttribute('aria-live', 'assertive');

  const hint = document.createElement('p');
  hint.className = 'login-hint';

  card.append(heading, org, slots, error, hint);
  section.append(aurora, card);
  document.body.append(section);

  // Giriş kartı bu sırada GİZLENİR ve sonunda geri açılır; kendisine
  // dokunulmaz.
  el.login.hidden = true;

  return new Promise((resolve) => {
    pairing = {
      section, slots, error, hint, code: '', busy: false,
      done: () => {
        window.removeEventListener('keydown', onPairKey, true);
        section.remove();
        pairing = null;
        el.login.hidden = false;
        resolve();
      },
    };
    pairSlots();
    pairHint(`Merkezden aldığınız ${PAIR_LENGTH} haneli kodu yazın.`);
    window.addEventListener('keydown', onPairKey, true);
  });
}

// ================================================================ AÇILIŞ

/**
 * Kabuğun hangi adrese baktığı. Kabuk dışında (tarayıcı denemesi) `null`.
 *
 * Adres RUST TARAFINDA belirlenir ve tek yerdedir (`server_base`); arayüz onu
 * kendi başına hesaplasaydı iki taraf ayrışabilir ve ekran bir adresi
 * yazarken istek başka bir adrese giderdi.
 */
async function serverInfo() {
  const invoke = window.__TAURI__?.core?.invoke;
  if (!invoke) return null;
  try {
    return await invoke('server_info');
  } catch {
    return null;
  }
}

async function start() {
  el.login.hidden = false;
  renderSlots();

  // KİPİ ÖNCE SÖYLE. Sunucu kipinde "çekirdek başlatılıyor" cümlesi yanlıştır:
  // burada başlatılan bir şey yok, uzak bir sunucuya bağlanılıyor (ADR 0026).
  // Yanlış cümle, arıza anında kullanıcıyı yanlış yere baktırır — makinede
  // olmayan bir süreci aramaya.
  const server = await serverInfo();
  const remote = server && !server.local;
  setHint([remote ? `${server.base} adresine bağlanılıyor…` : 'Çekirdek başlatılıyor…']);

  const ready = await waitForCore();
  if (!ready.ok) {
    // SON HATA OLDUĞU GİBİ YAZILIR ama METİN olarak: içeriği çekirdekten
    // geliyor ve `innerHTML` ile basılırsa yorumlanırdı.
    //
    // SUNUCU KİPİNDE NE YAPILACAĞI DA YAZILIR. "Ulaşılamadı" tek başına
    // kullanıcıya hiçbir şey yaptırmaz; internet mi, sunucu mu, adres mi
    // sorusunun ilk adımı söylenir.
    setHint(remote
      ? [
        'Merkeze ulaşılamıyor — uygulama veriyi merkezden alır.',
        `Adres: ${server.base}`,
        `Sebep: ${ready.error}`,
        'İnternet bağlantınızı denetleyin; sorun sürerse yöneticinize bildirin.',
      ]
      : [`Çekirdeğe ulaşılamadı — ${ready.error}`]);
    return;
  }

  // EŞLEME GİRİŞTEN ÖNCE GELİR (ADR 0021 §4). Gerekmiyorsa bu satır hiçbir şey
  // yapmaz ve ekran bugünkü gibi doğrudan PIN tuş takımıyla açılır.
  if (await pairingRequired()) await runPairing();

  setHint(LOGIN_HINT);
}

start();
