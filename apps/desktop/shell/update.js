// update.js — arka planda güncelleme (ADR 0032 §Güncelleme).
//
// ESKİDEN: güncelleme yalnız Sistem Ayarları → Güncelleme sekmesinden, üç
// elle adımla (denetle → indir → kur) yapılıyordu; çoğu kullanıcı oraya hiç
// gitmiyordu. ŞİMDİ: kabuk açıldıktan bir süre sonra (ve 6 saatte bir) sessizce
// denetler, varsa ARKADA indirir; paket hazır olunca altta ince bir şerit ve
// (pencere arkadaysa) sistem bildirimi: "Sürüm X hazır — Yeniden başlat ve
// güncelle". KURULUM YALNIZ KULLANICI BASINCA olur: kasada satış girilirken
// uygulama kendi kendine kapanmaz ("Sonra" der, şerit bir sonraki açılışa
// kadar bir daha gelmez).
//
// Rust komutları aynı: `update_support` / `update_check` / `update_download` /
// `update_progress` / `update_install` (main.rs). Sistem Ayarları ekranı aynı
// durumu görür — orada açılırsa indirmenin ilerlemesini gösterir.

const FIRST_CHECK_MS = 45 * 1000;
const EVERY_MS = 6 * 60 * 60 * 1000;
const POLL_MS = 1500;

/**
 * @param {object} o
 *   invoke(cmd, args)        — Tauri komutu
 *   notify(title, body, opt) — sistem bildirimi (ui-kit/native.js)
 *   toast(text, tone)        — kabuk bildirimi
 */
export function createUpdater({ invoke, notify, toast }) {
  let timer = null;
  let busy = false;
  let bar = null;
  let dismissed = false;
  let ready = null; // indirilen sürüm

  function showBar(version, notes) {
    if (dismissed || bar) return;
    bar = document.createElement('div');
    bar.className = 'km-update';
    bar.setAttribute('role', 'status');
    const text = document.createElement('span');
    text.textContent = `Kontrol Merkezi ${version} indirildi ve hazır.`;
    if (notes) text.title = notes;
    const install = document.createElement('button');
    install.type = 'button';
    install.className = 'km-btn km-btn-primary';
    install.textContent = 'Yeniden başlat ve güncelle';
    const later = document.createElement('button');
    later.type = 'button';
    later.className = 'km-btn';
    later.textContent = 'Sonra';
    install.addEventListener('click', async () => {
      install.disabled = true;
      install.textContent = 'Kuruluyor…';
      try {
        await invoke('update_install');
      } catch (error) {
        install.disabled = false;
        install.textContent = 'Yeniden başlat ve güncelle';
        toast(String(error?.message || error), 'bad');
      }
    });
    later.addEventListener('click', () => {
      dismissed = true;
      bar?.remove();
      bar = null;
    });
    bar.append(text, install, later);
    document.body.append(bar);
  }

  async function waitDownload() {
    for (;;) {
      const progress = await invoke('update_progress');
      if (progress.state === 'done') return progress;
      if (progress.state === 'error' || progress.state === 'idle') throw new Error(progress.error || 'İndirme yarım kaldı.');
      await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    }
  }

  /**
   * Denetle → (varsa) arkada indir → şerit. `manual`: kullanıcı menüden
   * istedi; sonuç her durumda söylenir.
   */
  async function check({ manual = false } = {}) {
    if (busy) {
      if (manual) toast('Güncelleme zaten denetleniyor.');
      return;
    }
    busy = true;
    try {
      const support = await invoke('update_support');
      if (!support.ready || support.dev) {
        if (manual) toast(support.dev ? 'Bu bir geliştirme derlemesi; güncelleme yayınlanan pakette çalışır.' : (support.reason || 'Güncelleyici bu yapıda kullanılamıyor.'));
        return;
      }
      if (ready) {
        if (manual) { dismissed = false; showBar(ready.version, ready.notes); }
        return;
      }
      const progress = await invoke('update_progress');
      if (progress.state === 'running') {
        // Sistem Ayarları'ndan başlatılmış bir indirme sürüyor: onu bekle.
        const done = await waitDownload();
        ready = { version: done.version || '', notes: '' };
      } else if (progress.state === 'done' && progress.version) {
        ready = { version: progress.version, notes: '' };
      } else {
        const found = await invoke('update_check');
        if (!found.available) {
          if (manual) toast(`Kontrol Merkezi güncel (${found.currentVersion}).`, 'ok');
          return;
        }
        if (manual) toast(`Sürüm ${found.version} bulundu; arka planda indiriliyor…`);
        await invoke('update_download');
        await waitDownload();
        ready = { version: found.version, notes: found.notes || '' };
      }
      showBar(ready.version, ready.notes);
      notify('Güncelleme hazır', `Kontrol Merkezi ${ready.version} indirildi. Uygun olduğunuzda "Yeniden başlat ve güncelle"ye basın.`,
        { whenInactive: true });
    } catch (error) {
      // Arka plan denetimi sessiz kalır (ağ yok, yayın yok): kullanıcıyı
      // rahatsız edecek bir şey değil. Elle istenmişse söylenir.
      if (manual) toast(String(error?.message || error), 'bad');
      else console.warn('[güncelleme] arka plan denetimi:', error);
    } finally {
      busy = false;
    }
  }

  return {
    start() {
      if (timer) return;
      timer = setTimeout(function tick() {
        check();
        timer = setTimeout(tick, EVERY_MS);
      }, FIRST_CHECK_MS);
    },
    check,
  };
}
