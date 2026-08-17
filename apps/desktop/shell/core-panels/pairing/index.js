// KM Cihaz Eşle — ÇEKİRDEK EKRANI (ADR 0017 · ADR 0021 §4).
//
// NE YAPAR: bu kurulumun merkeze eşlenip eşlenmediğini gösterir; MERKEZDEN NE
// ALDIĞINI (kadro revizyonu, dağıtılan ayar/sır, son tazeleme) yazar ve elle
// tazeler; yeni Kontrol Merkezi kurulumları için TEK KULLANIMLIK, SÜRELİ eşleme
// kodu üretir; merkeze eşlenmiş makineleri listeler ve iptal eder; bu makinenin
// eşlemesini çözer. Kurulum bozuksa (anahtar uyuşmazlığı, merkezde iptal,
// eşlenmemişlik) NE YAPILACAĞINI adım adım söyler.
//
// NE YAPMAZ:
//  · KOD GİRMEZ. Kodu GİREN taraf ilk açılıştaki eşleme ekranıdır
//    (`shell/app.js`); burası kodu ÜRETEN taraftır. İkisini aynı ekrana koymak,
//    eşleşmemiş bir kurulumda hiç açılamayacak bir ekrana bağlanmak olurdu.
//  · KAYIT SİLMEZ. İptal edilen kurulumun satırı merkezde DURUR, yalnız durumu
//    değişir (`revoked_at`): hangi makinenin ne zaman eşlendiği ve ne zaman
//    koparıldığı denetimin parçasıdır.
//  · KODU HİÇBİR YERE YAZMAZ. Kod bir sırdır; denetim izine geçmez (iz satırı
//    silinmiyor, kodun ömrü on dakika yerine sonsuz olurdu), panoya yalnız
//    kullanıcı isterse gider.
//  · MERKEZÎ KİMLİK ŞALTERİNİ AÇMAZ. `platform.identity_sync.enabled` bir ayar
//    kararıdır ve Sistem Ayarları'nın işidir; burada yalnız DURUMU okunur.
//
// SÜREYİ SUNUCU SÖYLER. Geri sayım `expiresAt` alanından türer; arayüz süreyi
// hesaplamaz. Kendi başına "10 dakika" sayan bir arayüz, merkezin ayarı
// değiştiği gün sessizce yalan söylerdi.
//
// ÇİFT KAPI (K9). Buradaki her gizleme yalnız arayüz kolaylığıdır; aynı izin
// `km_platform/identity_sync/http.py` içinde yeniden denetlenir. Ekran
// `installations.view` ile menüye giriyor, yönetim düğmeleri ayrıca
// `installations.manage` istiyor ve backend ikisini de kendisi soruyor.
//
// BU EKRAN MODÜL DEĞİLDİR: `registry.json`'a girmez, `shell/panels/` altına
// kopyalanmaz; dosyaları doğrudan `shell/core-panels/pairing/` altından servis
// edilir (ADR 0017 §4).
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (ADR 0011); ikinci bir bileşen seti
// doğurulmaz.

import {
  ago, blockedButton, button, confirmSimple, copyText, h, loadStyles, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import {
  alertBox, badge, card, emptyState, skeletonRows, statusLine,
} from '../../ui-kit/layout.js';

/** Merkezin bildirdiği kurulum durumu → ekrandaki rozet. */
const STATUS_LABELS = { active: 'Etkin', revoked: 'İptal edildi' };
const STATUS_TONES = { active: 'good', revoked: 'bad' };

/**
 * "Şimdi tazele" düğmesinin gittiği uç.
 *
 * BUGÜN (18.08.2026) ÇEKİRDEKTE HENÜZ YOK. `km_platform/identity_sync/http.py`
 * altı uç yayınlıyor (`state`, `pair`, `reset`, `installations`, `pair-code`,
 * `installations/{id}/revoke`, `unpair`) ve kadro tazelemeyi yalnız GİRİŞ YOLU
 * tetikliyor (`km_core/http/users.py` → `_prepare_login` → `sync.sync()`).
 * Yani kullanıcının elinde "şimdi tazele" diyecek bir düğme yoktu; tazelemek
 * için çıkıp yeniden girmek gerekiyordu.
 *
 * Uç `km_platform/identity_sync` üzerinde ÇALIŞAN BAŞKA BİR AJANIN işidir; bu
 * ekran ona göre yazıldı ve adı burada TEK YERDE durur.
 *
 * BEKLENEN YANIT `IdentitySync.sync()`in sözlüğüdür — uydurulmuş bir biçim
 * değil, servisin bugün zaten döndürdüğü şey:
 *
 *     {synced, changed, revision, provisioning: {applied, changed, revision,
 *      secrets, settings, reason}, reason?, reset?}
 *
 * `provisioning` alt sözlüğü kurulum paketinden gelir (ADR 0025) ve ayar/sır
 * SAYILARININ TEK KAYNAĞIDIR: `/api/pairing/state` yalnız
 * `provisioningRevision` taşır.
 *
 * KALICI DEĞERLER YİNE DE YANITTAN OKUNMAZ: tazelemeden sonra
 * `/api/pairing/state` yeniden çekilir ve kartlar oradan çizilir. Yanıt
 * beklenenden farklı çıkarsa ekran yanlış revizyon göstermez; yalnız o turun
 * sayıları eksik kalır. Uç hiç yoksa 404 gelir ve ekran bunu SÖYLER (aşağıda).
 */
const REFRESH_PATH = '/api/pairing/refresh';

/** Giriş politikası — `identity_sync.login_policy()` ile aynı iki değer. */
const POLICY_TEXT = {
  local: 'Çevrimdışı giriş kabul ediliyor.',
  online_only: 'Önbellek yaş sınırını aştı; şu an yalnız çevrimiçi giriş kabul ediliyor.',
};

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA

  const { api } = ctx;
  const view = h('div', 'kit-panel ce');
  root.append(view);

  const toast = toaster(view);
  const status = statusLine();
  const notice = h('div', 'ce-notice');
  const body = h('div', 'kit-body');

  view.append(status.node, notice, body);

  const state = {
    permissions: new Set(),
    sync: null,          // GET /api/pairing/state yanıtı
    installations: [],
    installationsError: '',
    code: null,          // {code, expiresAt} — YALNIZ BELLEKTE
    busy: false,         // tazeleme sürüyor: düğme iki kez basılmasın
    refreshNote: null,   // {text, tone} — son tazelemenin sonucu
    // Son tazelemenin kurulum paketi bölümü (ADR 0025). Kalıcı değildir:
    // sayılar yalnız uygulama anında bildiriliyor, `/api/pairing/state`
    // yalnız revizyonu taşıyor.
    provisioning: null,
    // Merkez bu kurulumu iptal etmiş ve tazeleme sırasında öğrenildi. Durum
    // `/api/pairing/state`ten OKUNAMAZ: iptal öğrenildiği anda eşleme
    // sıfırlanıyor ve geriye yalnız "eşlenmemiş" görüntüsü kalıyor. Sebebi
    // burada tutulmazsa ekran, kullanıcının hiç yapmadığı bir "eşlemeyi çöz"
    // işleminden farksız görünürdü.
    revoked: false,
  };

  let ticker = null;     // canlı geri sayım
  let disposed = false;

  // ------------------------------------------------------------------ izin

  /**
   * İzin sorgusu — MENÜ DEĞİL, DÜĞME içindir. Ekran `installations.view` ile
   * açılıyor ama kod üretmek, iptal etmek ve eşlemeyi çözmek
   * `installations.manage` istiyor.
   *
   * Cevabı bilmeden düğme çizmek, tıklandığında ham bir 403 göstermek olurdu;
   * kitin kuralı bunu yasaklıyor: bir düğme YA ÇALIŞIR, YA BURADAN GEÇER, ya
   * da hiç çizilmez. Yetkiyi yine sunucu uygular (K9).
   */
  const can = (key) => state.permissions.has(key) || state.permissions.has(`${key}:*`);

  /**
   * Yönetim düğmelerinin ortak kapısı. ÜÇ AYRI SEBEP, ÜÇ AYRI CÜMLE — çünkü
   * kullanıcının yapacağı iş farklı: yetki istenir, ayar açılır, anahtar
   * kasaya yazılır. Tek bir "yapılamıyor" cümlesi üçünü de gizlerdi.
   */
  function blockReason() {
    if (!can('installations.manage')) {
      return 'Bu işlem için kurulum yönetimi yetkiniz yok.';
    }
    if (!state.sync?.configured) {
      return 'Merkezî kimlik servisi bu kurulumda ayarlanmamış.';
    }
    if (!state.sync?.managementKey) {
      return 'Merkezin yönetim anahtarı kasada yok (identity_sync.admin_token).';
    }
    return '';
  }

  /** `blockReason()` boşsa çalışan düğme, doluysa nedenini söyleyen kapalı düğme. */
  function gatedButton(label, options) {
    const reason = blockReason();
    return reason
      ? blockedButton(label, reason, { variant: options.variant || '' })
      : button(label, options);
  }

  // --------------------------------------------------------------- yardım

  function setNotice(node) {
    notice.replaceChildren();
    if (node) notice.append(node);
  }

  function stopTicker() {
    if (ticker !== null) {
      window.clearInterval(ticker);
      ticker = null;
    }
  }

  /**
   * Kodun kalan süresi. Dolmuşsa AÇIKÇA söyler — sessizce sıfırda duran bir
   * sayaç, kullanıcıyı çalışmayan bir kodu okumaya gönderirdi.
   */
  function remaining(iso) {
    const ms = Date.parse(iso);
    if (!ms || Number.isNaN(ms)) return { text: 'süre bilinmiyor', over: true };
    const seconds = Math.round((ms - Date.now()) / 1000);
    if (seconds <= 0) return { text: 'süresi doldu', over: true };
    const minutes = Math.floor(seconds / 60);
    return {
      text: minutes > 0
        ? `${minutes} dk ${String(seconds % 60).padStart(2, '0')} sn kaldı`
        : `${seconds} sn kaldı`,
      over: false,
    };
  }

  /** Etiket + değer satırı. Değer yoksa em-dash; boş bırakmak "bilinmiyor" gibi okunur. */
  function fact(label, value, { mono = false, title = '' } = {}) {
    const row = h('div', 'ce-fact');
    row.append(h('span', 'ce-fact-label', label));
    const node = h('span', `ce-fact-value${mono ? ' mono' : ''}`);
    if (value instanceof Node) node.append(value);
    else node.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
    if (title) node.title = title;
    row.append(node);
    return row;
  }

  // -------------------------------------------------------------- PIN kutusu

  /**
   * PIN teyidi kutusu — YIKICI İŞLEMİN ARAYÜZ TARAFI.
   *
   * `confirmWithReason` KULLANILMAZ: o kutu gerekçe alır (mağaza yıkıcı
   * işlemleri, ADR 0012) ve yazılanı EKRANDA GÖSTERİR. Burada istenen şey
   * kişinin KENDİ PIN'idir; giriş ekranında olduğu gibi maskelenir ve hiçbir
   * yere kopyalanmaz. Kutu kitin kendi sınıflarıyla (`kit-overlay`,
   * `kit-dialog`, `kit-input`) çizilir — ikinci bir görsel dil doğmaz (ADR
   * 0011).
   *
   * PIN yalnızca bir sonraki isteğin gövdesinde yaşar; state'e yazılmaz.
   */
  function askPin({ title, description, confirmLabel }) {
    return new Promise((resolve) => {
      const overlay = h('div', 'kit-overlay');
      const box = h('div', 'kit-dialog');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      box.append(h('h3', 'kit-dialog-title', title));
      if (description) box.append(h('p', 'kit-dialog-text', description));

      const input = h('input', 'kit-input');
      input.type = 'password';
      input.inputMode = 'numeric';
      input.autocomplete = 'off';
      input.maxLength = 32;
      input.placeholder = 'PIN';
      input.setAttribute('aria-label', 'PIN');
      box.append(input);

      const error = h('div', 'kit-dialog-error');
      box.append(error);

      const close = (value) => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
        resolve(value);
      };
      const submit = () => {
        const pin = input.value;
        if (!pin) {
          error.textContent = 'PIN yazılmadan onaylanamaz.';
          input.focus();
          return;
        }
        close(pin);
      };
      const onKey = (event) => {
        if (event.key === 'Escape') close(null);
        if (event.key === 'Enter') submit();
      };

      const actions = h('div', 'kit-dialog-actions');
      actions.append(
        button('Vazgeç', { onClick: () => close(null) }),
        button(confirmLabel, { variant: 'danger', onClick: submit }),
      );
      box.append(actions);
      overlay.append(box);
      overlay.addEventListener('mousedown', (event) => {
        if (event.target === overlay) close(null);
      });
      document.addEventListener('keydown', onKey);
      view.append(overlay);
      input.focus();
    });
  }

  // ------------------------------------------------------------- 1. kurulum

  /** Bu makinenin künyesi ve eşleme durumu. */
  function machineCard() {
    const sync = state.sync || {};
    const machine = sync.machine || {};

    const box = h('div', 'ce-facts');

    const durum = h('span', 'ce-badges');
    if (!sync.configured) durum.append(badge('Merkez ayarlanmamış', 'dim'));
    else if (sync.paired) durum.append(badge('Eşlenmiş', 'good'));
    else durum.append(badge('Eşlenmemiş', 'warn'));
    if (sync.configured && sync.online === false) durum.append(badge('Merkeze ulaşılamıyor', 'bad'));
    box.append(fact('Durum', durum));

    box.append(fact('Makine adı', machine.machineName));
    box.append(fact('Platform', `${machine.platform || '—'} · sürüm ${machine.version || '—'}`));
    box.append(fact('Kurulum kimliği', sync.installationId, { mono: true }));
    box.append(fact('Merkez adresi', sync.baseUrl || 'tanımsız', { mono: true }));

    // KADRO VE KURULUM PAKETİ AYRI KARTTA: "bu makine kim" ile "bu makine
    // merkezden ne aldı" iki ayrı sorudur ve ikincisi artık tek satıra sığmıyor.
    box.append(fact('Giriş', POLICY_TEXT[sync.loginPolicy] || sync.loginPolicy || '—'));

    // Kuyrukta bekleyen denetim kaydı — "asla düşürülmez" sözünün görünür
    // karşılığı. Sayı büyüyorsa merkez ulaşılamıyordur (ADR 0021 §5).
    if (sync.auditPending) {
      box.append(fact('Bekleyen denetim kaydı',
        `${sync.auditPending} kayıt merkeze gönderilmeyi bekliyor`));
    }

    const actions = h('div', 'ce-actions');
    if (sync.paired) {
      // "Eşlemeyi çöz" YIKICIDIR ve merkeze GİTMEZ; bu yüzden `blockReason`
      // yerine yalnız izin sorulur — çalınan bir makineyi koparmak tam da ağın
      // olmadığı anda gerekebilir.
      actions.append(can('installations.manage')
        ? button('Eşlemeyi çöz', { variant: 'danger', onClick: () => unpair() })
        : blockedButton('Eşlemeyi çöz',
          'Bu işlem için kurulum yönetimi yetkiniz yok.', { variant: 'danger' }));
    }
    actions.append(button('Yenile', { onClick: () => refresh() }));
    box.append(actions);

    return card('Bu kurulum', box,
      sync.configured ? '' : 'merkezî kimlik kapalı — kurulum tek makinede çalışıyor');
  }

  /**
   * Merkez ayarlanmamışken ekran SAKİNCE söyler ve PATLAMAZ.
   *
   * Bu bir arıza değil, bir ayar durumudur: `platform.identity_sync.enabled`
   * kapalıyken Kontrol Merkezi tek makinede çalışır ve hiçbir yetenek gerilemez
   * (ADR 0021 — Sonuçlar). Kırmızı kutu, kullanıcıyı olmayan bir hatayı aramaya
   * gönderirdi.
   */
  function unconfiguredCard() {
    const box = h('div', 'ce-stack');
    box.append(h('p', 'ce-text',
      'Merkezî kimlik servisi bu kurulumda kapalı. Kontrol Merkezi tek makinede '
      + 'çalışıyor: kullanıcılar ve roller yerel veritabanında duruyor, giriş '
      + 'buradan yapılıyor ve hiçbir özellik eksik değil.'));
    box.append(h('p', 'ce-text',
      'Cihaz eşleme, merkezî kimlik açıldıktan sonra anlam kazanır. Şalter bir '
      + 'ayar kararıdır ve Sistem Ayarları ekranından açılır; bu ekran yalnız '
      + 'durumu okur.'));
    return card('Cihaz eşleme kapalı', box);
  }

  // -------------------------------------------------- 1.5 merkezden gelenler

  /** Sayı mı geldi? `0` geçerli bir cevaptır, `null`/eksik değildir. */
  const count = (value) => (typeof value === 'number' && Number.isFinite(value) ? value : null);

  /**
   * KURULUM PAKETİNİN İKİ AYRI KAYNAĞI — karıştırılmaz.
   *
   *   · `state.sync.provisioningRevision` — KALICI. Kasadaki paket revizyonu
   *     (`identity_sync.provisioning_revision`), `/api/pairing/state` içinde
   *     her açılışta gelir. `null` = paket hiç alınmamış.
   *   · `state.provisioning` — GEÇİCİ. Yalnız bu oturumda "Şimdi tazele"
   *     denmişse dolar ve `sync.fetch_provisioning()`in döndürdüğü sayıları
   *     taşır: `{applied, changed, revision, secrets, settings, reason}`.
   *
   * SAYILAR NEDEN KALICI DEĞİL: merkez paket sayılarını yalnız paketi
   * UYGULARKEN bildiriyor (ADR 0025) ve yazılan sayı "kaç ayar dağıtıldı"
   * değil, "kaçı DEĞİŞTİ" demek — değişmeyene dokunulmuyor. Bu iki farkı
   * gizleyip tek bir "12 ayar" yazmak, ekranı yanlış konuşturmak olurdu.
   */
  function lastProvisioning() {
    const paket = state.provisioning;
    return paket && typeof paket === 'object' ? paket : null;
  }

  /**
   * "BU KURULUM NE ALDI" — ekranın bugüne kadar söylemediği şey.
   *
   * Eşleme ekranı, eşlemenin KENDİSİNİ gösteriyordu ama eşlemenin NE İŞE
   * YARADIĞINI göstermiyordu: kadro hangi revizyonda, merkez kaç ayar/sır
   * dağıttı, en son ne zaman tazelendi. Bu üç sayı olmadan "bu makine merkezle
   * aynı mı" sorusu ancak giriş denenerek yanıtlanıyordu.
   */
  function receivedCard() {
    const sync = state.sync || {};
    const cache = sync.cache || {};
    const paket = lastProvisioning();

    const box = h('div', 'ce-facts');

    // KADRO. "Hiç çekilmemiş" ile "bilinmiyor" ayrı şeylerdir.
    const revizyon = cache.revision ?? null;
    box.append(fact('Kadro revizyonu',
      revizyon === null ? 'hiç çekilmemiş' : `revizyon ${revizyon}`));
    box.append(fact('Kadro içeriği', cache.present
      ? `${count(cache.users) ?? 0} kullanıcı · ${count(cache.roles) ?? 0} rol`
      : 'önbellekte kadro yok'));

    // SON KADRO TAZELEME — kesin an ipucunda (`title`) durur.
    const kadroTarih = cache.fetchedAt;
    box.append(fact('Son kadro tazeleme', kadroTarih ? ago(kadroTarih) : 'hiç',
      { title: kadroTarih ? stampIso(kadroTarih) : '' }));

    // KURULUM PAKETİ (ADR 0025) — merkezin dağıttığı ayar ve sırlar.
    const paketRev = sync.provisioningRevision ?? null;
    box.append(fact('Kurulum paketi',
      paketRev === null ? 'hiç alınmamış' : `revizyon ${paketRev}`));

    if (paket && (count(paket.settings) !== null || count(paket.secrets) !== null)) {
      box.append(fact('Son tazelemede yazılan',
        `${count(paket.settings) ?? 0} ayar · ${count(paket.secrets) ?? 0} sır`,
        { title: 'Değişmeyen ayar ve sırlara dokunulmaz; sayı yalnız yazılanları gösterir.' }));
    } else if (paket && paket.changed === false) {
      box.append(fact('Son tazelemede yazılan', 'değişiklik yoktu'));
    } else if (paket && paket.reason) {
      // Merkez eski sürümse ya da paket çekilemediyse SEBEP olduğu gibi yazılır.
      box.append(fact('Son tazelemede yazılan', `alınamadı — ${paket.reason}`));
    } else {
      box.append(fact('Son tazelemede yazılan',
        'bilinmiyor — "Şimdi tazele" sayıları getirir'));
    }

    const actions = h('div', 'ce-actions');
    // TAZELEME YÖNETİM İŞİ DEĞİLDİR: makine kendi kadrosunu çekiyor, merkezde
    // hiçbir şey değiştirmiyor. Bu yüzden `installations.manage` sorulmaz —
    // sorulsaydı, kadrosu eskimiş bir makinedeki personel kendi makinesini
    // tazeleyemez, yönetici arardı. Yetkiyi yine backend uygular (K9).
    if (!sync.paired) {
      actions.append(blockedButton('Şimdi tazele',
        'Bu kurulum merkezle eşlenmemiş; tazelenecek bir kadro yok.'));
    } else if (state.busy) {
      actions.append(button('Tazeleniyor…', { disabled: true }));
    } else {
      actions.append(button('Şimdi tazele', {
        variant: 'primary',
        title: 'Kadroyu ve kurulum paketini merkezden yeniden çeker.',
        onClick: () => refreshFromCenter(),
      }));
    }
    box.append(actions);

    if (state.refreshNote) {
      // Sonuç DÜĞMENİN YANINDA durur, ekranın tepesinde değil: tazelemeyi
      // isteyen kişi gözünü oraya dikmiş oluyor.
      const noteBox = h('div', 'ce-note');
      noteBox.append(alertBox(state.refreshNote.text, state.refreshNote.tone));
      box.append(noteBox);
    }

    return card('Merkezden gelenler', box,
      'kadro her girişte kendiliğinden tazelenir; bu düğme beklemeden çeker');
  }

  // ------------------------------------------------- 1.6 bozukluk ve yönerge

  /**
   * Kurulum bozuksa HANGİ bozukluk olduğu ve NE YAPILACAĞI.
   *
   * Üç durum, üç ayrı yönerge; hiçbiri ötekinin yerine geçmez:
   *
   *   · `mismatch` — merkezin kimlik anahtarı bu kurulumunkiyle ayrışmış. Bu
   *     makinede HİÇBİR PIN çalışmaz (`_check_pepper`).
   *   · `revoked`  — merkez bu makineyi iptal etmiş; tazeleme sırasında
   *     öğrenildi ve eşleme kendiliğinden düştü.
   *   · `unpaired` — merkez ayarlı ama makine eşlenmemiş.
   *
   * BU EKRAN BUGÜNE KADAR ÜÇÜNDE DE SESSİZDİ: `pepperMismatch` alanı
   * `/api/pairing/state` içinde geliyordu ama hiçbir yerde okunmuyordu, iptal
   * yalnız girişte 409 olarak görünüyordu. Kullanıcının gördüğü tek şey "giriş
   * yapılamadı" oluyordu ve sebebi hiçbir ekranda yazmıyordu (17.08.2026).
   */
  function troubleKind() {
    const sync = state.sync;
    if (!sync || !sync.configured) return '';
    if (sync.pepperMismatch) return 'mismatch';
    if (state.revoked) return 'revoked';
    if (!sync.paired) return 'unpaired';
    return '';
  }

  /** Numaralı yönerge listesi. Sıra ÖNEMLİ: adımlar birbirinin önkoşulu. */
  function steps(items) {
    const list = h('ol', 'ce-steps');
    items.forEach((text) => list.append(h('li', 'ce-step', text)));
    return list;
  }

  function troubleCard(kind) {
    const box = h('div', 'ce-stack');

    if (kind === 'mismatch') {
      box.append(alertBox(
        'Bu kurulumun kimlik anahtarı merkezinkiyle uyuşmuyor. Bu makinede '
        + 'hiçbir PIN çalışmaz: giriş ekranı doğru PIN’e de "yanlış" der.', 'bad'));
      box.append(steps([
        'Merkeze eşli başka bir makinede, kurulum yönetimi yetkisi olan biri '
        + 'bu ekrandan "Eşleme kodu üret" desin. Kod tek kullanımlıktır ve '
        + 'süresini merkez belirler.',
        'Bu makinede uygulamayı kapatıp yeniden açın. Kurulum uyuşmazlığı '
        + 'gördüğünde kendi eşlemesini düşürür ve açılışta EŞLEME EKRANI gelir.',
        'Kodu eşleme ekranına yazın. Kurulum merkezin anahtarını benimser, '
        + 'kadro yeniden çekilir ve merkezdeki kullanıcılar girebilir.',
        'Bu makinede YERELDE açılmış kullanıcılar (ilk kurulumun yöneticisi '
        + 'gibi) eski anahtarla kaldıkları için giremez; onların merkezde '
        + 'yeniden açılması gerekir.',
      ]));
    } else if (kind === 'revoked') {
      box.append(alertBox(
        'Bu kurulum merkezde iptal edilmiş. Kadro çekilemiyor, merkeze yazı '
        + 'gönderilemiyor; eşleme kendiliğinden sıfırlandı.', 'bad'));
      box.append(steps([
        'İptal isteyerek yapılmadıysa merkezdeki "Kurulumlar" listesine bakın: '
        + 'hangi kaydın ne zaman iptal edildiği orada durur, satır silinmez.',
        'Yetkili biri merkeze eşli bir makineden yeni bir eşleme kodu üretsin.',
        'Bu makinede uygulamayı yeniden başlatın; eşleme ekranı gelir.',
        'Kodu yazın. Merkez makineyi aynı kimlikle tanır (özel anahtar kasada '
        + 'kaldı); iptal kaydı listede kalmaya devam eder.',
      ]));
    } else {
      box.append(alertBox(
        'Merkezî kimlik açık ama bu makine eşlenmemiş. Merkezdeki kullanıcılar '
        + 'burada giriş yapamaz; yalnız yerelde açılmış kayıtlar çalışır.', 'warn'));
      box.append(steps([
        'Merkeze eşli bir makinede yetkili biri "Eşleme kodu üret" desin.',
        'Bu makinede uygulamayı yeniden başlatın; eşleme ekranı açılışta gelir.',
        'Kodu yazın; kadro ilk girişte çekilir.',
      ]));
    }

    box.append(h('p', 'ce-text',
      'Kod bu ekrandan ÜRETİLİR, buraya GİRİLMEZ: kodu giren taraf ilk '
      + 'açılıştaki eşleme ekranıdır. Takılırsanız kurulum günlüklerine bakın — '
      + 'yerleri deploy/README.md → "Yeni cihaz kurulumu" bölümünde yazıyor.'));

    const titles = {
      mismatch: 'Kimlik anahtarı uyuşmuyor — yapılacaklar',
      revoked: 'Bu kurulum merkezde iptal edilmiş — yapılacaklar',
      unpaired: 'Bu makine eşlenmemiş — yapılacaklar',
    };
    return card(titles[kind] || 'Yapılacaklar', box);
  }

  // ----------------------------------------------------------- 2. eşleme kodu

  function codeCard() {
    const box = h('div', 'ce-stack');

    if (state.code) {
      const row = h('div', 'ce-code-row');
      // KOD BÜYÜK VE OKUNUR: telefonla okunacak, elle yazılacak.
      row.append(h('code', 'ce-code', state.code.code));
      row.append(button('Kopyala', {
        title: 'Eşleme kodunu panoya kopyala',
        onClick: async () => {
          const ok = await copyText(state.code.code);
          toast(ok ? 'Eşleme kodu kopyalandı.' : 'Pano kullanılamadı.', ok ? 'good' : 'warn');
        },
      }));
      box.append(row);

      const left = h('div', 'ce-left');
      const tick = () => {
        const info = remaining(state.code.expiresAt);
        left.textContent = info.text;
        left.classList.toggle('over', info.over);
        if (info.over) stopTicker();
      };
      tick();
      stopTicker();
      ticker = window.setInterval(tick, 1000);
      box.append(left);
      box.append(h('span', 'ce-sub', `Son geçerlilik: ${stampIso(state.code.expiresAt)}`));
      box.append(h('p', 'ce-text',
        'Kodu yeni kurulumun ilk açılıştaki eşleme ekranına yazın. Kod TEK '
        + 'KULLANIMLIKTIR: bir makine eşlendiği anda yanar.'));
    } else {
      box.append(h('p', 'ce-text',
        'Yeni bir Kontrol Merkezi kurulumunu eşlemek için kod üretin. Kod tek '
        + 'kullanımlıktır ve süresi merkezde belirlenir.'));
    }

    const actions = h('div', 'ce-actions');
    actions.append(gatedButton(state.code ? 'Yeni kod üret' : 'Eşleme kodu üret', {
      variant: 'primary',
      title: 'Yeni kod üretmek, bekleyen eski kodları geçersiz kılar.',
      onClick: () => createCode(),
    }));
    box.append(actions);

    return card('Eşleme kodu', box, 'yeni kod bekleyen eski kodları geçersiz kılar');
  }

  // ----------------------------------------------------------- 3. kurulumlar

  const table = dataTable({
    columns: [
      { key: 'machineName', label: 'Makine', width: 'minmax(0, 1.4fr)', sortable: true },
      { key: 'platform', label: 'Platform', width: '120px' },
      { key: 'version', label: 'Sürüm', width: '90px' },
      {
        key: 'lastSeenAt',
        label: 'Son görülme',
        width: '150px',
        sortable: true,
        cell: (row) => {
          if (!row.lastSeenAt) return h('span', 'ce-never', 'hiç bağlanmamış');
          const node = h('span', undefined, ago(row.lastSeenAt));
          node.title = stampIso(row.lastSeenAt);
          return node;
        },
      },
      {
        key: 'status',
        label: 'Durum',
        width: '170px',
        // Renk TEK BAŞINA anlam taşımaz: rozetin içinde yazı da var.
        cell: (row) => {
          const box = h('span', 'ce-badges');
          box.append(badge(STATUS_LABELS[row.status] || row.status, STATUS_TONES[row.status] || ''));
          if (row.revokedAt) {
            const when = h('span', 'ce-sub', ago(row.revokedAt));
            when.title = stampIso(row.revokedAt);
            box.append(when);
          }
          return box;
        },
      },
      {
        key: 'actions',
        label: '',
        width: '110px',
        cell: (row) => {
          // İPTAL EDİLMİŞ KURULUMUN DÜĞMESİ ÇİZİLMEZ: satır listede kalır ama
          // yapılacak bir iş yoktur.
          if (row.status !== 'active') return h('span', 'ce-sub', '—');
          return gatedButton('İptal et', {
            variant: 'danger',
            onClick: (event) => {
              event.stopPropagation();
              revoke(row);
            },
          });
        },
      },
    ],
    rows: [],
    empty: emptyState({
      title: 'Kurulum yok',
      text: 'Merkeze eşlenmiş makine bulunmuyor. Yukarıdan kod üretip yeni bir '
        + 'kurulumu eşleyebilirsiniz.',
    }),
  });

  function installationsCard() {
    const box = h('div', 'ce-stack');
    if (state.installationsError) {
      box.append(alertBox(state.installationsError, 'warn'));
    }
    box.append(table.node);
    table.update({ rows: state.installations });
    return card('Kurulumlar', box, 'iptal edilen kayıt silinmez, durumu değişir');
  }

  // ----------------------------------------------------------------- çizim

  function paint() {
    if (disposed) return;
    stopTicker();
    body.replaceChildren();

    const sync = state.sync;
    if (!sync) {
      body.append(skeletonRows(4, 2));
      return;
    }

    body.append(machineCard());
    if (!sync.configured) {
      body.append(unconfiguredCard());
      status.set('Merkezî kimlik kapalı — cihaz eşleme kullanılmıyor.');
      return;
    }

    // YÖNERGE EN ÜSTTE (künyeden hemen sonra): bozuk bir kurulumda kullanıcının
    // okuması gereken ilk şey "ne yapacağım"dır, kurulum listesi değil.
    const trouble = troubleKind();
    if (trouble) body.append(troubleCard(trouble));

    body.append(receivedCard());
    body.append(codeCard());
    body.append(installationsCard());

    const etkin = state.installations.filter((row) => row.status === 'active').length;
    const kadro = sync.cache?.revision ?? null;
    status.set(
      `${state.installations.length} kurulum · ${etkin} etkin`
      + (sync.paired ? ' · bu makine eşlenmiş' : ' · bu makine eşlenmemiş')
      + (kadro === null ? '' : ` · kadro revizyonu ${kadro}`),
    );
  }

  // ------------------------------------------------------------------ veri

  async function loadPermissions() {
    try {
      const me = await api('/auth/me');
      state.permissions = new Set(me.permissions || []);
    } catch {
      // Oturum özeti alınamazsa hiçbir yönetim düğmesi çizilmez; ekran yine
      // açılır. Sessizce "yetkili" varsaymak, kullanıcıyı tıkladığında 403 alan
      // bir düğmeye gönderirdi.
      state.permissions = new Set();
    }
  }

  async function refresh() {
    try {
      state.sync = await api('/api/pairing/state');
      setNotice(null);
    } catch (error) {
      state.sync = null;
      status.set(error.message, true);
      setNotice(alertBox(error.message, 'bad'));
      return;
    }

    await loadInstallations();
    paint();
  }

  /**
   * Kurulum listesi. LİSTE ALINAMAMASI EKRANI DÜŞÜRMEZ (K7): kurulumun kendi
   * durumu yerelde okunuyor ve o kısım her hâlükârda görünmeli. Sebep listenin
   * üstünde durur.
   */
  async function loadInstallations() {
    state.installations = [];
    state.installationsError = '';

    if (!state.sync?.configured) return;
    if (!can('installations.view')) {
      state.installationsError = 'Kurulum listesini görme yetkiniz yok.';
      return;
    }
    if (!state.sync.managementKey) {
      state.installationsError = 'Merkezin yönetim anahtarı kasada yok '
        + '(identity_sync.admin_token); kurulum listesi çekilemiyor.';
      return;
    }

    try {
      const payload = await api('/api/pairing/installations');
      state.installations = payload.installations || [];
    } catch (error) {
      state.installationsError = error.message;
    }
  }

  // --------------------------------------------------------------- eylemler

  /**
   * "ŞİMDİ TAZELE" — kadroyu ve kurulum paketini merkezden çeker.
   *
   * NEDEN VAR: tazeleme bugüne kadar yalnız GİRİŞ yolunda koşuyordu
   * (`_prepare_login`). Merkezde açılan bir kullanıcının bu makineye ulaşması
   * için birinin çıkıp yeniden girmesi gerekiyordu ve bunu kimse bilmiyordu.
   *
   * KALICI DEĞERLER YANITTAN OKUNMAZ: tazeleme bittikten sonra
   * `/api/pairing/state` yeniden çekilir ve kartlar oradan çizilir. Yanıttan
   * yalnız o turun kendi bilgisi alınır — "kurulum iptal edilmiş" işareti
   * (`reset`) ve kurulum paketinin yazılan sayıları (`provisioning`), ki
   * ikisi de başka hiçbir yerde durmaz. Gerekçe `REFRESH_PATH` başlığında.
   */
  async function refreshFromCenter() {
    if (state.busy) return;
    state.busy = true;
    state.refreshNote = null;
    paint();

    let result = null;
    let failure = null;
    try {
      result = await api(REFRESH_PATH, { method: 'POST' });
    } catch (error) {
      failure = error;
    }
    state.busy = false;

    if (failure) {
      // UÇ HENÜZ YOKSA BU BİR ARIZA DEĞİLDİR, ESKİ ÇEKİRDEKTİR. Ham "İstek
      // başarısız (404)" cümlesi kullanıcıyı ağ aramaya gönderirdi.
      state.refreshNote = failure.status === 404
        ? {
          text: `Bu kurulumun çekirdeği elle tazelemeyi tanımıyor (${REFRESH_PATH}). `
            + 'Uygulamayı güncelleyin. O zamana kadar kadro her girişte '
            + 'kendiliğinden tazelenir: çıkıp yeniden girmek aynı işi yapar.',
          tone: 'warn',
        }
        : { text: failure.message, tone: 'bad' };
      await refresh();
      return;
    }

    // Yanıt `IdentitySync.sync()`in döndürdüğü sözlüktür:
    //   {synced, changed, revision, provisioning: {...}, reason?, reset?}
    // `provisioning` alt sözlüğü `fetch_provisioning()`ten gelir ve sayıları
    // yalnız orada bulunur (ADR 0025) — bu yüzden saklanır.
    const paket = result && typeof result.provisioning === 'object'
      ? result.provisioning : null;
    state.provisioning = paket;

    // `reset: true` → merkez bu kurulumu iptal etmiş ve eşleme düşürüldü.
    // Yönergeyi `troubleCard` yazar; burada yalnız işaret saklanır.
    state.revoked = Boolean(result && result.reset);
    if (state.revoked) {
      state.refreshNote = {
        text: 'Merkez bu kurulumu iptal etmiş. Eşleme sıfırlandı; yapılacaklar '
          + 'yukarıda yazıyor.',
        tone: 'bad',
      };
    } else if (result && result.synced === false) {
      // Ağ yoksa senkron hata YÜKSELTMEZ, "olmadı" der (K7). Sessiz geçilmez.
      state.refreshNote = {
        text: `Tazelenemedi: ${result.reason || 'merkeze ulaşılamadı'}. Eldeki `
          + 'kadroyla çalışılmaya devam ediliyor.',
        tone: 'warn',
      };
    } else {
      // KADRO VE PAKET AYRI DEFTERDİR: kadro değişmemişken paket değişmiş
      // olabilir (ve tersi). Tek bir "güncel" cümlesi ikisini birden söylemiş
      // gibi okunurdu.
      const kadroText = result && result.changed === false
        ? 'Kadroda değişiklik yok'
        : `Kadro tazelendi${result && result.revision !== undefined && result.revision !== null
          ? ` (revizyon ${result.revision})` : ''}`;
      let paketText = 'kurulum paketi sorulmadı';
      if (paket && paket.applied) {
        paketText = `pakette ${count(paket.settings) ?? 0} ayar, `
          + `${count(paket.secrets) ?? 0} sır yazıldı`;
      } else if (paket && paket.changed === false) {
        paketText = 'pakette değişiklik yok';
      } else if (paket && paket.reason) {
        paketText = `paket alınamadı — ${paket.reason}`;
      }
      state.refreshNote = { text: `${kadroText}; ${paketText}.`, tone: 'good' };
    }

    await refresh();
  }

  async function createCode() {
    try {
      const result = await api('/api/pairing/pair-code', { method: 'POST', body: { note: null } });
      state.code = { code: String(result.code || ''), expiresAt: result.expiresAt || '' };
      toast('Eşleme kodu üretildi.', 'good');
    } catch (error) {
      // Sunucunun cümlesi OLDUĞU GİBİ gösterilir: "bağlantı gerekiyor" ile
      // "yönetim anahtarı yok" farklı işler ister.
      setNotice(alertBox(error.message, 'bad'));
      return;
    }
    await loadInstallations();
    paint();
  }

  async function revoke(row) {
    const onay = await confirmSimple(view, {
      title: `Kurulumu iptal et: ${row.machineName || row.id}`,
      description: 'Bu makine bir daha kadro çekemez ve merkeze yazamaz. Kaydı '
        + 'SİLİNMEZ; ne zaman eşlendiği ve ne zaman iptal edildiği listede kalır. '
        + 'Makine sahada yeniden eşlenmek isterse yeni bir kod gerekir.',
      confirmLabel: 'İptal et',
      danger: true,
    });
    if (!onay) return;

    try {
      await api(`/api/pairing/installations/${encodeURIComponent(row.id)}/revoke`,
        { method: 'POST' });
      toast('Kurulum iptal edildi.', 'good');
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
      return;
    }
    await loadInstallations();
    paint();
  }

  /**
   * BU MAKİNENİN eşlemesini çözer. YIKICIDIR → PIN teyidi ister (izin yeterli
   * olsa bile; docs/permissions.md — "Uygulama kuralları" 3).
   */
  async function unpair() {
    const pin = await askPin({
      title: 'Bu kurulumun eşlemesini çöz',
      description: 'Kurulum token’ı kasadan silinir ve makine merkezden kadro '
        + 'çekmeyi bırakır. Merkezden gelen kullanıcılar bu makinede '
        + 'PASİFLEŞİR (silinmez); yerelde açılmış kullanıcılara dokunulmaz. '
        + 'Makine yeniden eşlenirse merkez onu aynı kimlikle tanır. '
        + 'Onaylamak için PIN’inizi yazın.',
      confirmLabel: 'Eşlemeyi çöz',
    });
    if (!pin) return;

    try {
      const result = await api('/api/pairing/unpair', { method: 'POST', body: { password: pin } });
      state.code = null;
      // Eşlemeyi kullanıcı KENDİ çözdü: "merkez iptal etti" yönergesi, son
      // tazeleme notu ve paket sayıları artık yanlış cümlelerdir.
      state.revoked = false;
      state.refreshNote = null;
      state.provisioning = null;
      toast(result.disabledUsers
        ? `Eşleme çözüldü; ${result.disabledUsers} merkez kullanıcısı pasifleşti.`
        : 'Eşleme çözüldü.', 'good');
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
      return;
    }
    await refresh();
  }

  // --------------------------------------------------------------- açılış
  //
  // İzinler ÖNCE gelir: düğmeler onlara göre çiziliyor ve "yetkiniz yok" yazan
  // bir düğmeyi bir an bile yetkili kullanıcıya göstermek yanlış cümledir.

  (async () => {
    await loadPermissions();
    await refresh();
  })();

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR: geri sayım her saniye çalışan bir
  // zamanlayıcıdır ve panel kapandıktan sonra da çalışmaya devam ederdi.
  return () => {
    disposed = true;
    stopTicker();
  };
}
