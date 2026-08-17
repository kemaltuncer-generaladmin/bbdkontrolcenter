// KM Cihaz Eşle — ÇEKİRDEK EKRANI (ADR 0017 · ADR 0021 §4).
//
// NE YAPAR: bu kurulumun merkeze eşlenip eşlenmediğini gösterir; yeni Kontrol
// Merkezi kurulumları için TEK KULLANIMLIK, SÜRELİ eşleme kodu üretir; merkeze
// eşlenmiş makineleri listeler ve iptal eder; bu makinenin eşlemesini çözer.
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
    const cache = sync.cache || {};

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

    // SON KADRO TAZELEME. "Hiç çekilmemiş" ile "bilinmiyor" ayrı şeylerdir.
    const fetched = cache.fetchedAt;
    const kadro = fetched
      ? `${ago(fetched)} · ${cache.users || 0} kullanıcı · revizyon ${cache.revision ?? '—'}`
      : 'hiç çekilmemiş';
    box.append(fact('Son kadro tazeleme', kadro, { title: fetched ? stampIso(fetched) : '' }));

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

    body.append(codeCard());
    body.append(installationsCard());

    const etkin = state.installations.filter((row) => row.status === 'active').length;
    status.set(
      `${state.installations.length} kurulum · ${etkin} etkin`
      + (sync.paired ? ' · bu makine eşlenmiş' : ' · bu makine eşlenmemiş'),
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
