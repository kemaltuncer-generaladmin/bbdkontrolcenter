// Sistem Sağlığı — ÇEKİRDEK EKRANI (ADR 0017 · ADR 0026).
//
// NE YAPAR: kabuğun HANGİ ÇEKİRDEĞE baktığını, o çekirdeğin ayakta olup
// olmadığını, gidiş-dönüş gecikmesini ve modüllerin durumunu yazar.
//
// NEDEN ŞİMDİ YAZILDI. Bu ekran menüde uzun süre `entry: null` ile durdu ve
// gövdesinde "ekranı henüz yok" kartı çıkıyordu. ADR 0026 ile veri merkeze
// taşındı; artık "uygulamam neden boş / neden açılmıyor" sorusunun cevabı
// çoğu zaman TEK BİR ŞEY: kabuk hangi adrese bakıyor ve orası cevap veriyor
// mu. O cevabın bir yeri olmalıydı.
//
// NE YAPMAZ:
//  · GÜNCELLEME YAPMAZ. "Sürüm ve güncelleme" kartı Sistem Ayarları'ndadır ve
//    orada kalır; ikinci bir güncelleme düğmesi, hangisinin çalıştığını
//    belirsiz kılardı. Buradan oraya yalnız YÖNLENDİRİLİR.
//  · AYAR DEĞİŞTİRMEZ. Sunucu adresi pakete gömülü gelir (`KM_SERVER_URL`
//    ile elle aşılır); ekrandan değiştirilebilseydi kullanıcı kendini
//    ulaşılamayan bir adrese kilitleyebilirdi.
//
// ÇİFT KAPI (K9): menüye `settings.view` ile girer; okuduğu `/health` ucu zaten
// herkese açıktır ve sır taşımaz.

import { button, h, loadStyles, toaster } from '../../ui-kit/kit.js';
import { alertBox, badge, card, skeletonRows } from '../../ui-kit/layout.js';

/** Gecikme eşikleri (ms) — renk bunlara göre seçilir. */
const LATENCY_GOOD = 400;
const LATENCY_FAIR = 1200;

/** Kabuğun komut kapısı. Tarayıcıda ya da sidecar'sız koşuda yoktur. */
const shellInvoke = () => window.__TAURI__?.core?.invoke || null;

function row(label, value, tone) {
  const line = h('div', 'sh-row');
  line.append(h('span', 'sh-label', label));
  line.append(tone ? badge(value, tone) : h('span', 'sh-value', value));
  return line;
}

export function mount(root, ctx) {
  const { api, open } = ctx;
  loadStyles(import.meta.url, 'panel.css');
  let disposed = false;
  let timer = null;

  const say = toaster(root);
  const body = h('div', 'sh-stack');
  root.replaceChildren(body);

  async function paint() {
    if (disposed) return;
    body.replaceChildren(skeletonRows(3, 2));

    // BAĞLANTI ÖLÇÜMÜ İLK İŞ. Gecikme burada ölçülür çünkü `/health` en ucuz
    // uçtur; ağır bir uçla ölçmek sunucunun değil sorgunun yavaşlığını yazardı.
    const started = performance.now();
    let health = null;
    let failure = null;
    try {
      health = await api('/health');
    } catch (error) {
      failure = error?.message || String(error);
    }
    const latency = Math.round(performance.now() - started);
    if (disposed) return;

    const cards = [];

    // ------------------------------------------------------------ bağlantı
    const link = h('div', 'sh-stack');
    let server = null;
    const invoke = shellInvoke();
    if (invoke) {
      try {
        server = await invoke('server_info');
      } catch {
        server = null;
      }
    }
    if (disposed) return;

    if (server) {
      link.append(row('Adres', server.base));
      link.append(row(
        'Kip',
        server.local ? 'Yerel çekirdek' : 'Merkezî sunucu',
        server.local ? 'warn' : 'good',
      ));
    } else {
      // Kabuk yoksa bu bir arıza DEĞİLDİR: tarayıcıda tasarım denemesi ya da
      // depodan doğrudan koşu olabilir. Sakin bir cümleyle söylenir.
      link.append(row('Adres', 'kabuk dışından açıldı — adres okunamıyor'));
    }

    if (failure) {
      link.append(alertBox(
        `Çekirdeğe ulaşılamıyor: ${failure}`,
        'bad',
      ));
    } else {
      const tone = latency <= LATENCY_GOOD ? 'good' : latency <= LATENCY_FAIR ? 'warn' : 'bad';
      link.append(row('Durum', 'Ulaşılabiliyor', 'good'));
      link.append(row('Gecikme', `${latency} ms`, tone));
    }
    cards.push(card('Bağlantı', link));

    // -------------------------------------------------------------- modüller
    if (health) {
      const mods = h('div', 'sh-stack');
      const loaded = health.modules?.loaded ?? 0;
      const total = health.modules?.total ?? 0;
      const problems = health.modules?.problems || [];
      mods.append(row(
        'Yüklenen',
        `${loaded} / ${total}`,
        problems.length ? 'warn' : 'good',
      ));
      if (problems.length) {
        // SORUN GİZLENMEZ. Bir modülün düşmesi diğerlerini düşürmez (K7) ama
        // sessizce yok sayılırsa ekranın neden eksik olduğu anlaşılmaz.
        for (const problem of problems) {
          mods.append(alertBox(
            `${problem.module || problem.id || 'modül'}: ${problem.reason || 'sebep bildirilmedi'}`,
            'warn',
          ));
        }
      }
      cards.push(card('Modüller', mods));

      // ---------------------------------------------------------- sürüm
      const build = health.build || {};
      const info = h('div', 'sh-stack');
      info.append(row('Çekirdek sürümü', build.version || 'bilinmiyor'));
      if (build.commit) info.append(row('Commit', build.commit.slice(0, 12)));
      if (build.builtAt) info.append(row('Derleme', build.builtAt));

      const actions = h('div', 'sh-actions');
      actions.append(button('Sürüm ve güncelleme →', {
        // İKİNCİ GÜNCELLEME DÜĞMESİ AÇILMAZ: akış Sistem Ayarları'nda,
        // kabuğun `update_*` komutlarına bağlı. Buradan yalnız oraya gidilir.
        onClick: () => open('core_settings'),
      }));
      info.append(actions);
      cards.push(card('Sürüm', info));
    }

    if (disposed) return;
    body.replaceChildren(...cards);
  }

  paint().catch((error) => say(error?.message || String(error), 'bad'));

  // Sayfa açık kaldıkça tazelenir: bağlantı kopunca ekran bunu KENDİ
  // söylemeli, kullanıcı yenilemeyi düşünmeden önce.
  timer = setInterval(() => { paint().catch(() => {}); }, 15000);

  return () => {
    disposed = true;
    if (timer) clearInterval(timer);
  };
}
