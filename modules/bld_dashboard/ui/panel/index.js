// Kontrol Paneli — BLD işletmesinin açılış ekranı.
//
// NE YAPAR: üç kutu. (1) BUGÜNÜN SATIŞI — sipariş sayısı, porsiyon, ciro,
// abonelik/serbest satış kırılımı, stok doluluk oranı ve kesim saatine kalan
// süre. (2) BEKLEYEN İŞLER — yayınlanmamış menü, cevaplanmamış teklif talebi,
// imzasız sözleşme, takılmış sipariş; her satır ilgili ekrana atlar.
// (3) CANLI SİPARİŞ AKIŞI — son siparişler belirir, durumları değişir.
//
// NE YAPMAZ — ve bunu gizlemez:
//  · SAYI HESAPLAMAZ. `active`, `fill_rate`, `seconds_to_next_cutoff` ve
//    bekleyen işlerin tamamı sunucuda üretilir (`BLD/docs/control/dashboard.md`
//    → "Buradaki sayılar tanımdır, tahmin değil"). Ekran hiçbir sayacı kendi
//    toplamaz; toplasaydı "kaç sipariş aktif" sorusunun cevabı panel sürümüne
//    göre değişirdi.
//  · CÜMLE KURMAZ. Bekleyen iş satırlarının `title`/`detail` metni sunucudan
//    gelir ve olduğu gibi yazılır. Aynı durumu iki ekranda iki farklı cümleyle
//    anlatmak, sahada telefonda konuşan iki kişinin farklı şey söylemesidir.
//  · HİÇBİR ŞEY YAZMAZ. Bu alanda BLD'ye giden yazma ucu yok. Ekrandaki tek
//    kaydetme, kullanıcının kendi görüntüleme tercihidir ve BLD'ye gitmez.
//  · İZİN DENETLEMEZ. Görünürlük sunucuda süzülür (K9); bir uç 403 dönerse
//    ekran bunu söyler ve çalışmaya devam eder.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · GERİ SAYIM SUNUCUNUN SAATİNE DAYANIR. `seconds_to_next_cutoff` yanıtla
//    birlikte gelir; ekran üzerine yalnız GEÇEN SÜREYİ ekler (`Date.now()`
//    farkı), mutlak saat kullanmaz. İstemcinin saatinden hesaplasaydı, saati
//    kaymış bir makinede olmayan bir aciliyet doğardı.
//  · MENÜ YAYINLANMAMIŞSA KAPASİTE `null`'dur, SIFIR DEĞİL. Sıfır çizmek
//    "gün doldu" demek olurdu; doluluk çubuğu o hâlde hiç çizilmez ve yerine
//    tek cümle yazılır.
//  · `overdue` `unpaid`'İN İÇİNDEDİR. İkisi üst üste değil, biri diğerinin
//    içinde gösterilir; toplamak borcu iki kez sayardı.
//  · AKIŞ KUTUSU AYRI BİR UÇTAN BESLENİR. Sözleşmenin gösterge ucu sayaç
//    döndürüyor, satır değil; akış `orders.md` listesinden gelir ve KENDİ
//    BAŞINA düşebilir. Düşerse yalnız o kutu boşalır, panel ayakta kalır (K7).
//  · SUNUCU UÇLARI HENÜZ YAYINDA OLMAYABİLİR. `control_endpoint_missing`
//    beklenen bir durumdur, hata değil: ekran bunu ayrı bir cümleyle söyler.
//  · ATLAMA HEDEFİ OLMAYAN SATIRA DÜĞME KONMAZ. Sunucu tanımadığımız bir yol
//    verirse (`link`) satır düğmesiz durur ve yol yazıyla gösterilir; hiçbir
//    yere gitmeyen bir düğme, bozuk bir düğmedir.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (docs/adr/0011). Import yolu
// KOPYALANMIŞ konuma göredir: shell/panels/bld_dashboard/ → shell/ui-kit/.
// Bu dosyanın KAYNAĞI modules/bld_dashboard/ui/panel/ altındadır; orada
// '../../ui-kit/' dosya sisteminde ÇÖZÜLMEZ — normaldir.

import {
  ago, button, h, loadStyles, money, num, percent, pollLoop, stampIso, toaster,
} from '../../ui-kit/kit.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, progress, skeletonRows,
  statusLine,
} from '../../ui-kit/layout.js';
import { stackedBar } from '../../ui-kit/charts.js';
import { timeline } from '../../ui-kit/flow.js';
import { dateField } from '../../ui-kit/datefield.js';

const BASE = '/api/bld_dashboard';

/**
 * Geri sayım şeridinin tik aralığı. AĞA ÇIKMAZ — yalnız ekrandaki yazıyı
 * tazeler. Dakikada bir yeter: kesim saatine kalan süre saat/dakika olarak
 * yazılıyor ve saniye göstermek, hiçbir kararı değiştirmeyen bir kıpırtı
 * olurdu.
 */
const TICK_MS = 60_000;

/** Yoklama aralığının yedek değeri; gerçeği `/overview` → `prefs`ten gelir. */
const FALLBACK_POLL_MS = 30_000;

let api = null;
let open = null;
let toast = null;
let state = freshState();
const nodes = {};

/**
 * Bağlanma sayacı. Her `mount()` bunu artırır, `cleanup` bir daha artırır.
 *
 * NEDEN GEREKLİ: açılış sözleşmesi (`/overview`) bir söz (promise) döndürüyor
 * ve yoklama döngüsü ANCAK O ÇÖZÜLDÜKTEN SONRA kuruluyor. Kullanıcı panel
 * açılırken hemen başka bir ekrana geçerse `cleanup` çoktan koşmuş olur ve
 * geciken `.then` gövdesi, hiçbir zaman durdurulmayacak bir `pollLoop` kurar —
 * ekran kapalıyken 30 saniyede bir sunucuya giden, hiçbir yerde görünmeyen bir
 * döngü. Sayacı karşılaştırmak, o gövdenin ait olduğu bağlanma hâlâ ayakta mı
 * diye sorar.
 */
let epoch = 0;

/**
 * Başlangıç durumu FONKSİYONDUR: sabit nesne olsaydı iç içe alanlar yayılırken
 * referansla kopyalanır ve panel kapanıp açıldığında önceki oturumun günü ve
 * akış geçmişi geri gelirdi.
 */
function freshState() {
  return {
    // BAĞLANTI: `ok:true` ile gelen `connected:false` (K7). Ayrı tutulur çünkü
    // "bugün sipariş yok" ile "sunucuya ulaşılamıyor" aynı görünmemeli.
    link: { connected: null, error: '', code: '' },
    contract: null,
    prefs: null,
    date: '',
    summary: null,
    loaded: false,
    // Geri sayımın tabanı: sunucunun verdiği saniye ve o yanıtın ALINDIĞI an.
    // İkisi birlikte tutulur; ekran aradaki farkı ekler, kendi saatini
    // taban almaz.
    countdown: { seconds: null, takenAt: 0, cutoffTime: '', nextDate: '' },
    // Akışta neyin YENİ olduğunu söyleyebilmek için önceki turun izi.
    // İlk turda hiçbir satır yeni sayılmaz (`flowSeeded`): açılışta her satırı
    // "yeni geldi" diye işaretlemek, on siparişlik bir yalan olurdu.
    flowSeen: new Map(),
    flowSeeded: false,
  };
}

// --------------------------------------------------------------- yardımcı

/** Sayı ya da tire. `null` "bilinmiyor" demektir ve sıfırla karıştırılmaz. */
function count(value) {
  return value === null || value === undefined ? '—' : num(value);
}

/**
 * Uç çağrısı. İSTİSNA YUTULMAZ, ÇEVRİLİR: panel her yerde aynı `{ok, error}`
 * biçimini görsün diye ağ hatası da aynı zarfa konur.
 */
async function call(path, options) {
  try {
    return await api(`${BASE}${path}`, options);
  } catch (error) {
    return { ok: false, error: error?.message || 'İstek gönderilemedi.' };
  }
}

/** K7: bağlantı yoksa ekranın tepesine ne yazılacak. */
function connectionNotice() {
  if (state.link.connected !== false) return null;
  if (state.link.code === 'control_endpoint_missing') {
    return alertBox('Sunucudaki gösterge paneli ucu bu turda henüz yayında değil. '
      + 'Bu beklenen bir durumdur: ekran hazır, sunucu tarafı yazıldığında kutular '
      + 'kendiliğinden dolar.', 'warn');
  }
  return alertBox(`${state.link.error || 'BLD sunucusuna ulaşılamadı.'} `
    + 'Aşağıdaki kutular son okunabilen veriyi değil, BOŞ değeri gösterir — '
    + 'ekrandaki tire "bilinmiyor" demektir, "sıfır" değil.', 'bad');
}

/**
 * Kalan süreyi Türkçe yazar: 72000 → "20 saat". Saniye GÖSTERİLMEZ; şerit
 * dakikada bir tazeleniyor ve saniye yazmak, tazelenmeyen bir sayının
 * canlıymış gibi durması olurdu.
 */
function remainingText(seconds) {
  if (seconds === null || seconds === undefined) return '';
  const left = Math.max(0, Math.round(Number(seconds)));
  if (left === 0) return 'kesim saati geçti';
  const hours = Math.floor(left / 3600);
  const minutes = Math.floor((left % 3600) / 60);
  if (hours >= 24) {
    const days = Math.floor(hours / 24);
    return hours % 24 === 0 ? `${days} gün` : `${days} gün ${hours % 24} saat`;
  }
  // "20 saat 0 dakika" yerine "20 saat": sıfır olan birimi yazmak, okuyanın
  // gözünü hiçbir şey söylemeyen bir sayıya takar.
  if (hours > 0) return minutes === 0 ? `${hours} saat` : `${hours} saat ${minutes} dakika`;
  if (minutes > 0) return `${minutes} dakika`;
  return 'bir dakikadan az';
}

// ======================================================= kesim geri sayımı

/**
 * Kesim şeridi. TABAN SUNUCUNUN SAATİDİR.
 *
 * `seconds_to_next_cutoff` sunucuda hesaplanıyor (`dashboard.md` → `sales`).
 * Buraya yalnız yanıtın alınmasından bu yana GEÇEN SÜRE eklenir; mutlak saat
 * hiç kullanılmaz. `cutoff_at` alanından yerel olarak hesaplasaydık, saati
 * kaymış bir makinede yanlış bir aciliyet ya da yanlış bir rahatlık doğardı.
 */
function paintCutoff() {
  const line = nodes.cutoff;
  if (!line) return;

  const { seconds, takenAt, cutoffTime, nextDate } = state.countdown;
  if (state.link.connected === false) {
    line.set('Kesim saati okunamadı — BLD sunucusuna ulaşılamıyor.', true);
    return;
  }
  if (seconds === null || seconds === undefined) {
    line.set('Kesim saati bilinmiyor.', false);
    return;
  }

  const elapsed = Math.max(0, Math.round((Date.now() - takenAt) / 1000));
  const left = Math.max(0, seconds - elapsed);
  const parts = [`Kesime ${remainingText(left)}`];
  if (cutoffTime) parts.push(`kesim ${cutoffTime}`);
  if (nextDate) parts.push(`servis günü ${nextDate}`);

  const sales = state.summary?.sales || {};
  if (sales.ordering_enabled === false) {
    parts.push(sales.paused_until
      ? `SATIŞ DURDURULDU (${stampIso(sales.paused_until)}'a kadar)`
      : 'SATIŞ DURDURULDU');
  } else if (sales.busy === true) {
    parts.push('mutfak yoğun kipinde');
  }

  // Bir saatten az kaldıysa şerit uyarı rengine geçer. RENK TEK BAŞINA anlam
  // taşımaz: yazı zaten "23 dakika" diyor, renk yalnız onu öne çıkarır.
  line.set(parts.join(' · '), left <= 3600 || sales.ordering_enabled === false);
}

// ==================================================== 1 · bugünün satışı

function salesCard() {
  const box = h('div', 'bd-stack');
  const sum = state.summary;
  const orders = sum?.orders || {};
  const capacity = sum?.capacity || {};
  const subs = sum?.subscriptions || {};

  box.append(kpiRow([
    { label: 'Bugün alınan sipariş', value: count(orders.created_today) },
    { label: 'Aktif sipariş', value: count(orders.active) },
    { label: 'Satılan porsiyon', value: count(capacity.sold_total) },
    {
      label: 'Bugünün cirosu',
      value: orders.revenue_today_kurus === null || orders.revenue_today_kurus === undefined
        ? '—' : money(orders.revenue_today_kurus),
      // Sayının TANIMI kutunun üstünde durur: "bugün oluşturulan" ile "bugün
      // servis edilen" farklı kümelerdir ve ikisi de makul görünür.
      title: 'Bugün SERVİS EDİLEN ve iptal edilmemiş siparişlerin toplamı. '
        + 'Oluşturulma gününe göre saymak, ileri tarihli siparişleri bugünün '
        + 'cirosuna yazardı.',
    },
    {
      label: 'Teslim edilen',
      value: count(orders.delivered_today),
      title: 'İşletme günü (Europe/Istanbul) sınırında sayılır; UTC gece '
        + 'yarısı kullanılsaydı gece siparişleri "dün" sayılırdı.',
    },
    {
      label: 'Geciken',
      value: count(orders.late),
      tone: Number(orders.late) > 0 ? 'bad' : '',
      title: 'Planlanan teslim saati geçmiş ve hâlâ teslim edilmemiş sipariş. '
        + '"En kısa sürede" siparişler sayılmaz — planlanmış bir saatleri yok.',
    },
  ]));

  box.append(statusBreakdown(orders));
  box.append(salesSplit(capacity));
  box.append(fillBar(capacity));

  const notes = h('div', 'bd-notes');
  if (Number(orders.unreleased_subscription_orders) > 0) {
    notes.append(hintBox(
      `${count(orders.unreleased_subscription_orders)} abonelik siparişi üretildi ama `
      + 'henüz KDS\'e düşmedi. Abonelik siparişleri mutfak ekranına 07:00\'de düşer; '
      + 'bu sayı o saatten önce sıfırdan farklı olabilir.'));
  }
  if (Number(subs.overdue_periods) > 0) {
    notes.append(alertBox(
      `Vadesi geçmiş ${count(subs.overdue_periods)} abonelik dönemi var — `
      + `${money(subs.overdue_total_kurus)}. Bu tutar aşağıdaki ödenmemiş toplamın `
      + 'İÇİNDEDİR, üstüne eklenmez.', 'warn'));
  }
  if (notes.childElementCount > 0) box.append(notes);

  box.append(subscriptionLine(subs));

  return card('Bugünün satışı', box,
    sum?.date ? `Servis günü ${sum.date}` : 'Servis günü seçilmedi');
}

/** Aktif siparişlerin durum dağılımı — yığılmış çubuk + rozetler. */
function statusBreakdown(orders) {
  const labels = state.contract?.status_labels || {};
  const codes = state.contract?.active_status_codes || [];
  const byStatus = orders?.by_status || {};

  const parts = codes.map((code) => ({
    label: labels[code] || code,
    value: Number(byStatus[code] || 0),
  }));

  const wrap = h('div', 'bd-block');
  wrap.append(h('div', 'bd-block-title', 'Aktif siparişlerin durumu'));
  wrap.append(stackedBar(parts));

  // Rozet şeridi çubuğun YERİNE DEĞİL, yanına: sıfır olan durumlar çubukta
  // hiç çizilmiyor (genişliği yok) ve yalnız çubuğa bakan biri "hazır sipariş
  // yok" ile "hazır kutusu hiç gelmedi" arasını ayırt edemezdi.
  const chips = h('div', 'bd-chips');
  for (const code of codes) {
    chips.append(badge(`${labels[code] || code} ${num(byStatus[code] || 0)}`,
      state.contract?.status_tones?.[code] || 'dim'));
  }
  wrap.append(chips);
  return wrap;
}

/** Abonelik / serbest satış kırılımı. */
function salesSplit(capacity) {
  const wrap = h('div', 'bd-block');
  wrap.append(h('div', 'bd-block-title', 'Porsiyonun kaynağı'));

  const free = capacity?.sold_orders;
  const subscription = capacity?.sold_subscriptions;
  if (free === null && subscription === null) {
    wrap.append(hintBox('Menü yayınlanmadığı için kırılım hesaplanmadı. '
      + 'Sıfır yazmak "hiç satılmadı" demek olurdu; oysa ölçülecek bir tavan yok.'));
    return wrap;
  }

  wrap.append(stackedBar([
    { label: 'Serbest satış', value: Number(free || 0) },
    { label: 'Abonelik', value: Number(subscription || 0) },
  ]));
  wrap.append(h('div', 'bd-sub',
    'Abonelikler stoku ÖNCE rezerve eder; abone gün atlarsa o porsiyon serbest '
    + 'satışa döner ve soldaki dilime geçer.'));
  return wrap;
}

/** Stok doluluk oranı — `progress`. */
function fillBar(capacity) {
  const wrap = h('div', 'bd-block');
  wrap.append(h('div', 'bd-block-title', 'Stok doluluğu'));

  if (capacity?.menu_published === false) {
    wrap.append(alertBox('Bu güne yayınlanmış menü yok, bu yüzden kapasite diye bir '
      + 'kavram da yok. Doluluk çubuğu çizilmedi: sıfır göstermek "gün doldu" '
      + 'demek olurdu.', 'warn'));
    return wrap;
  }
  if (capacity?.capacity_total === null || capacity?.capacity_total === undefined) {
    wrap.append(hintBox('Gün tavanı okunamadı. Tire "bilinmiyor" demektir; '
      + 'sıfır olsaydı satış kapanmış görünürdü.'));
    return wrap;
  }

  const bar = progress();
  const rate = capacity.fill_rate === null || capacity.fill_rate === undefined
    ? null : Number(capacity.fill_rate) * 100;
  bar.percent(rate === null ? 0 : rate,
    `${count(capacity.sold_total)} / ${count(capacity.capacity_total)} porsiyon`
    + `${rate === null ? '' : ` · ${percent(rate, 0)} dolu`}`
    + ` · ${count(capacity.remaining_total)} kaldı`);
  wrap.append(bar.node);

  const blocked = capacity.blocked_items || [];
  if (blocked.length > 0) {
    const list = h('div', 'bd-chips');
    for (const item of blocked) {
      list.append(badge(`${item.name || `#${item.menu_id}`} ${num(item.sold)}/${num(item.capacity)}`,
        'bad'));
    }
    const box = h('div', 'bd-blocked');
    box.append(h('div', 'bd-sub', 'Tavanı dolan ürünler — bunlar artık sipariş '
      + 'edilemiyor, gün toplamı dolmasa bile:'));
    box.append(list);
    wrap.append(box);
  }
  return wrap;
}

/** Abonelik sayaçları tek satır. */
function subscriptionLine(subs) {
  const wrap = h('div', 'bd-block');
  wrap.append(h('div', 'bd-block-title', 'Abonelikler'));
  wrap.append(kpiRow([
    { label: 'Etkin', value: count(subs?.active) },
    { label: 'Fiyat bekleyen', value: count(subs?.pending) },
    { label: 'Duraklatılmış', value: count(subs?.paused) },
    { label: 'Bugünkü porsiyon', value: count(subs?.portions_today) },
    {
      label: 'İmza bekleyen sözleşme',
      value: count(subs?.contracts_awaiting_signature),
      tone: Number(subs?.contracts_awaiting_signature) > 0 ? 'warn' : '',
    },
    {
      label: 'Ödenmemiş dönem',
      value: count(subs?.unpaid_periods),
      title: subs?.unpaid_total_kurus === null || subs?.unpaid_total_kurus === undefined
        ? 'Tutar bilinmiyor.'
        : `Toplam ${money(subs.unpaid_total_kurus)} — vadesi geçmiş tutar bunun İÇİNDEDİR.`,
    },
  ]));
  return wrap;
}

// ===================================================== 2 · bekleyen işler

function tasksCard() {
  const box = h('div', 'bd-stack');
  const tasks = state.summary?.pending_tasks || [];

  if (state.link.connected === false) {
    box.append(hintBox('Bekleyen işler sunucuda hesaplanır; bağlantı yokken bu '
      + 'liste BOŞ DEĞİL, BİLİNMİYOR. Aşağıda hiçbir şey görmemeniz "yapacak iş '
      + 'yok" demek değildir.'));
    return card('Bekleyen işler', box);
  }
  if (tasks.length === 0) {
    box.append(emptyState({
      title: 'Bekleyen iş yok',
      text: 'Menüler yayınlanmış, teklif talebi ve imza beklemesi yok, kasalar '
        + 'çevrimiçi. Bu liste sunucuda hesaplanır ve boş olması gerçekten '
        + '"yapılacak bir şey yok" demektir.',
    }));
    return card('Bekleyen işler', box);
  }

  for (const task of tasks) box.append(taskRow(task));

  return card('Bekleyen işler', box,
    `${num(tasks.length)} madde · en çok 12 gösterilir`);
}

/**
 * Tek bir bekleyen iş satırı.
 *
 * CÜMLE SUNUCUDAN GELİR (`title` + `detail`) ve burada yeniden yazılmaz.
 * Satırın yaptığı tek şey, o cümleyi seviyesine uygun kutuya koymak ve
 * yanına ilgili ekrana atlayan düğmeyi asmak.
 */
function taskRow(task) {
  const row = h('div', `bd-task ${task.level}`);

  const head = h('div', 'bd-task-head');
  head.append(badge(task.level_label, task.tone));
  head.append(h('span', 'bd-task-title', task.title || task.code || 'Adsız madde'));
  if (Number(task.count) > 1) head.append(badge(`${num(task.count)} kayıt`, 'dim'));
  if (task.known === false && task.code) {
    // Sözleşmenin saymadığı bir kod: satır YİNE gösterilir. Sunucu yeni bir
    // madde eklediğinde panelin onu sessizce yutması, yöneticinin yapması
    // gereken bir işi hiç görmemesi olurdu.
    head.append(badge('yeni tür', 'info'));
  }
  head.append(h('span', 'kit-spacer'));

  if (task.panel) {
    head.append(button('Ekranı aç', {
      variant: 'ghost',
      title: `${task.panel} ekranına geçer (${task.link || '—'})`,
      onClick: () => open?.(task.panel, task.payload || {}),
    }));
  } else if (task.link) {
    // Tanınmayan yol: düğme YOK, yol yazıyla duruyor. Hiçbir yere gitmeyen
    // bir düğme koymak, kullanıcıya tıklattığı ama hiçbir şey olmayan bir
    // şey vermekti.
    head.append(h('code', 'bd-link', task.link));
  }

  row.append(head);
  if (task.detail) row.append(h('div', 'bd-task-detail', task.detail));
  return row;
}

// ================================================ 3 · canlı sipariş akışı

function flowCard() {
  const box = h('div', 'bd-stack');
  const flow = state.summary?.flow || {};

  if (flow.enabled === false) {
    box.append(hintBox('Canlı akış kutusu ayardan kapatıldı (`flow_enabled`). '
      + 'Kapalı kutu BOŞ KUTU DEĞİLDİR: burada "sipariş yok" yazmıyor, çünkü '
      + 'öyle bir bilgi hiç istenmedi.'));
    return card('Canlı sipariş akışı', box);
  }
  if (flow.connected === false) {
    box.append(alertBox(`Sipariş listesi okunamadı: ${flow.error || 'bilinmeyen hata'}. `
      + 'Yukarıdaki sayılar AYRI bir uçtan geliyor ve geçerliliğini koruyor.', 'bad'));
    return card('Canlı sipariş akışı', box);
  }

  const rows = flow.items || [];
  if (rows.length === 0) {
    box.append(emptyState({
      title: 'Akışta sipariş yok',
      text: 'Son günlerde kayıtlı sipariş bulunamadı. Bu kutu süzgeçsiz bakar; '
        + 'ayrıntı ve geçmiş için Sipariş Yönetimi ekranı.',
      actions: [button('Sipariş Yönetimi', { onClick: () => open?.('bld_orders') })],
    }));
    return card('Canlı sipariş akışı', box);
  }

  // SIRA ESKİDEN YENİYE. Kit `timeline` bir yolculuğu böyle çiziyor ve en yeni
  // hareketi en altta VURGULU bırakıyor; listeyi ters çevirmek, bileşenin
  // vurguladığı satırın en eski sipariş olması demekti.
  const events = [...rows].reverse().map((row) => flowEvent(row));
  box.append(timeline(events, { emptyText: 'Henüz hareket yok.' }));

  const foot = h('div', 'bd-sub',
    'Bu kutu sipariş listesinden beslenir ve süzgeç uygulamaz; gösterge '
    + 'panelinin sayaçları AYRI bir uçtan gelir. İki kutunun aynı anda farklı '
    + 'görünmesi olağandır — sayaçlar 60 saniye önbelleklenmiş olabilir.');
  box.append(foot);

  return card('Canlı sipariş akışı', box, `son ${num(rows.length)} sipariş`);
}

function flowEvent(row) {
  const previous = state.flowSeen.get(row.id);
  const parts = [];
  if (row.customer_name) parts.push(row.customer_name);
  if (row.item_count !== null && row.item_count !== undefined) {
    parts.push(`${num(row.item_count)} porsiyon`);
  }
  if (row.total_kurus !== null && row.total_kurus !== undefined) {
    parts.push(money(row.total_kurus));
  }
  if (row.is_subscription) parts.push('abonelikten');
  if (row.service_date) parts.push(`servis ${row.service_date}`);

  // YENİLİK YAZIYLA SÖYLENİR. Renk tek başına "bu satır yeni geldi" demez ve
  // ilk turda hiçbir satır yeni sayılmaz — açılışta on satırı birden "yeni"
  // işaretlemek, on kere yalan söylemekti.
  if (state.flowSeeded) {
    if (previous === undefined) parts.push('YENİ GELDİ');
    else if (previous !== row.status) {
      parts.push(`durum değişti: ${labelOf(previous)} → ${labelOf(row.status)}`);
    }
  }

  return {
    title: `${row.order_number || `#${row.id}`} · ${row.status_label}`,
    detail: parts.join(' · '),
    at: row.created_at ? ago(row.created_at) : '',
    tone: row.status_tone,
  };
}

function labelOf(code) {
  return state.contract?.status_labels?.[code] || code || '—';
}

// ==================================================================== çizim

function paint() {
  const body = nodes.body;
  if (!body) return;

  const parts = [];
  const notice = connectionNotice();
  if (notice) parts.push(notice);

  if (!state.loaded) {
    // Boş beyaz alan yerine tablonun ŞEKLİ: kullanıcı ne geleceğini bilir ve
    // ekran bozuk sanılmaz.
    parts.push(card('Bugünün satışı', skeletonRows(3, 4)));
    parts.push(card('Bekleyen işler', skeletonRows(3, 2)));
    parts.push(card('Canlı sipariş akışı', skeletonRows(4, 3)));
    body.replaceChildren(...parts);
    return;
  }

  parts.push(salesCard());
  parts.push(tasksCard());
  parts.push(flowCard());

  const meta = state.summary?.meta || {};
  if (meta.cached_at) {
    // Sunucu önbellek açtıysa söylenir. Kendi tahminimizi yürütmek, olmayan
    // bir gecikmeyi ekranda var göstermekti.
    parts.push(hintBox(`Sunucu bu özeti önbellekten verdi (${stampIso(meta.cached_at)}). `
      + 'Sözleşme 60 saniyelik önbelleği isteğe bağlı bırakıyor; "satışı durdurdum '
      + 'ama panel hâlâ açık gösteriyor" durumu bir tur sürebilir.'));
  }

  body.replaceChildren(...parts);
}

// =================================================================== veri

async function refresh() {
  const query = state.date ? `?date=${encodeURIComponent(state.date)}` : '';
  const payload = await call(`/summary${query}`);

  if (payload?.ok === false) {
    // Şema/süzgeç hatası: BAĞLANTI sorunu değil. `connected` bilinmiyor kalır.
    state.link = { connected: null, error: payload.error || '', code: '' };
    // İsteğin uçuşta olduğu sırada panel kapanmış olabilir: `?.` olmasaydı
    // `pollLoop` hatayı yutar ama konsola hiç düşmeyen bir kırık kalırdı.
    nodes.status?.set(payload.error || 'İstek reddedildi.', true);
    paint();
    return;
  }

  state.link = {
    connected: payload?.connected === true,
    error: payload?.error || '',
    code: payload?.code || '',
  };
  state.summary = payload || null;
  state.loaded = true;

  const sales = payload?.sales || {};
  state.countdown = {
    seconds: sales.seconds_to_next_cutoff ?? null,
    takenAt: Date.now(),
    cutoffTime: sales.cutoff_time || '',
    nextDate: sales.next_cutoff_date || '',
  };

  const flow = payload?.flow || {};
  if (flow.enabled !== false && flow.connected === true) {
    const next = new Map();
    for (const row of flow.items || []) next.set(row.id, row.status);
    state.flowSeen = next;
    // İlk dolu turdan SONRA "yeni" işaretlemesi açılır.
    state.flowSeeded = true;
  }

  nodes.status?.set(state.link.connected
    ? `Bağlı · ${payload?.date || 'bugün'} · sunucu saati ${stampIso(payload?.server_time)}`
    : (state.link.error || 'BLD sunucusuna ulaşılamıyor.'), !state.link.connected);

  paint();
  paintCutoff();
}

async function loadContract() {
  const payload = await call('/overview');
  if (payload?.ok === false) {
    // Sözleşme okunamadıysa etiketler boş kalır ama ekran YİNE çizilir: kod
    // adları da bir şey anlatır ve boş bir panel hiçbir şey anlatmaz (K7).
    toast?.('Ekran sözleşmesi okunamadı; etiketler ham kod olarak görünecek.', 'bad');
    return;
  }
  state.contract = payload?.contract || null;
  state.prefs = payload?.prefs || null;
}

// ==================================================================== mount

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA
  epoch += 1;
  api = ctx.api;
  open = ctx.open;
  state = freshState();

  const view = h('div', 'kit-panel bd');   // 'kit-panel' ZORUNLU + kendi önekimiz
  nodes.root = view;
  toast = toaster(view);

  // `<input type="date">` YASAK (WebKitGTK'da açılır takvim kapanmıyor);
  // `dateField` kitin kendi takvimini kullanır. Boş bırakmak "bugün" demektir
  // ve BUGÜNÜN NE OLDUĞUNA SUNUCU KARAR VERİR (işletme günü, Europe/Istanbul);
  // burada `todayIso()` yazmak, gece yarısından sonra açılan bir panelde
  // sunucununkinden başka bir gün istemek olurdu.
  nodes.date = dateField({
    value: '',
    label: 'Servis günü',
    onChange: (iso) => {
      state.date = iso || '';
      state.loaded = false;
      // Gün değişti: akış izi sıfırlanır, yoksa dünün siparişleri "yeni geldi"
      // diye işaretlenirdi.
      state.flowSeen = new Map();
      state.flowSeeded = false;
      paint();
      refresh();
    },
  });

  const bar = h('div', 'bd-topbar');
  bar.append(h('span', 'bd-label', 'Servis günü'));
  bar.append(nodes.date.node);
  bar.append(button('Bugün', {
    title: 'Gün seçimini bırakır; sunucu işletme gününü kendisi belirler.',
    onClick: () => {
      nodes.date.set('');
      state.date = '';
      state.loaded = false;
      state.flowSeen = new Map();
      state.flowSeeded = false;
      paint();
      refresh();
    },
  }));
  bar.append(h('span', 'kit-spacer'));
  // Döngü henüz kurulmamış olabilir (açılış sözleşmesi çözülene kadar);
  // o aralıkta düğmenin hiçbir şey yapmaması, "bozuk" diye bildirilirdi.
  bar.append(button('Yenile', { onClick: () => (nodes.poll ? nodes.poll.now() : refresh()) }));

  nodes.status = statusLine();
  nodes.cutoff = statusLine();
  nodes.body = h('div', 'bd-body');

  view.append(bar, nodes.status.node, nodes.cutoff.node, nodes.body);
  root.replaceChildren(view);

  paint();
  nodes.cutoff.set('Kesim saati okunuyor…');
  nodes.status.set('Gösterge özeti okunuyor…');

  // AÇILIŞ SIRASI: önce sözleşme (ağa çıkmaz, etiketleri getirir), sonra
  // canlı gövde. Ters sırada ilk çizim ham kod adlarıyla yapılır ve bir tur
  // sonra kendiliğinden düzelirdi — kullanıcı ekranın "titrediğini" görürdü.
  const mounted = epoch;
  loadContract().then(() => {
    // Panel bu arada kapandıysa döngü HİÇ KURULMAZ: kurulsaydı `cleanup`
    // çoktan koşmuş olacağı için onu durduracak kimse kalmazdı.
    if (mounted !== epoch) return;
    const seconds = Number(state.prefs?.poll_seconds || 0);
    const every = seconds > 0 ? seconds * 1000 : FALLBACK_POLL_MS;
    // Sekme gizliyken durur ve üst üste binmez; paylaşılan 3000/saat bütçesi
    // arka planda duran bir pencerede boşuna yanmaz.
    nodes.poll = pollLoop({ every, run: () => refresh(), immediate: true });
  });

  // Geri sayım AYRI bir döngüdür ve AĞA ÇIKMAZ: yoklama 30 saniyede bir,
  // şerit dakikada bir tazeleniyor. Tek döngüye bağlasaydık ya gereksiz yere
  // sık istek atardık ya da şerit 30 saniyeden seyrek tazelenirdi.
  nodes.ticker = pollLoop({ every: TICK_MS, run: () => paintCutoff() });

  return () => {
    epoch += 1;                  // uçuştaki `.then` gövdeleri artık geçersiz
    nodes.poll?.stop();          // zamanlayıcı + `visibilitychange` dinleyicisi
    nodes.ticker?.stop();
    nodes.date?.destroy();       // takvim GLOBAL dinleyici tutar
    nodes.poll = null;
    nodes.ticker = null;
    nodes.date = null;
    nodes.body = null;
    nodes.status = null;
    nodes.cutoff = null;
    nodes.root = null;
    root.replaceChildren();
    state = freshState();
    toast = null;
    api = null;
    open = null;
  };
}
