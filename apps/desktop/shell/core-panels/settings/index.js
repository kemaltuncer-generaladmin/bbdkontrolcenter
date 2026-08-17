// Sistem Ayarları — ÇEKİRDEK EKRANI (ADR 0017 · ADR 0018).
//
// NE YAPAR: dört sabit sekme (Genel, Yazıcı, Güncelleme, Tanılama) ve
// modüllerin manifestlerinde ilan ettiği sekmeler. Ayar değerini gösterir,
// KAYNAĞINI söyler ve değiştirileni çekirdek deposuna yazdırır.
//
// NE YAPMAZ:
//  · SIR YAZMAZ (K8). Şifre, token ve anahtar bu ekrandan geçmez; ayar
//    sözleşmesinde sır tipi yoktur (ADR 0018 §5) ve sunucu da böyle bir alan
//    ilan etmez. Kasa ayrı bir ekranın işidir.
//  · MODÜL ADI BİLMEZ (K1). Sekme listesi `GET /api/settings`ten gelir; burada
//    hiçbir modül adı yazılı değildir. `modules/` silinse ekran açılır ve
//    yalnız çekirdek sekmeleri kalır.
//  · SAHTE DÜĞME KOYMAZ. Güncelleme uçları yoksa düğmeler `blockedButton` ile
//    kapalıdır ve NEDENİ üzerlerinde yazılıdır; tıklanınca ham 404 gösteren
//    bir düğme bırakılmaz.
//
// KATMAN CÜMLESİ EKRANIN ASIL İŞİ. Ayar zinciri
// `dosya → çekirdek deposu → ortam değişkeni` biçiminde ve depo katmanı
// `local.yaml`'ı EZER (ADR 0018 §4). Bunu söylemeyen bir ekran, `local.yaml`'a
// yazan yöneticiyi "neden işlemedi" sorusuyla baş başa bırakırdı. Bu yüzden
// ezilen her alanın yanında rozeti ve dosyadaki değeri durur; ortamdan gelen
// alan ise hiç yazılamaz ve değişkenin adı yazılıdır.
//
// ÇİFT KAPI (K9). Buradaki her gizleme yalnız arayüz kolaylığıdır; aynı izin
// `km_core/http/settings.py` içinde yeniden denetlenir. Modül sekmesi kendi
// `requires` iznini ayrıca sorar — `settings.manage` tek başına yetmez.
//
// BU EKRAN MODÜL DEĞİLDİR: `registry.json`'a girmez, `shell/panels/` altına
// kopyalanmaz; dosyaları doğrudan `shell/core-panels/settings/` altından
// servis edilir.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (ADR 0011); `ui-kit/` değiştirilmez.

import {
  blockedButton, button, bytes, confirmSimple, copyText, h, loadStyles, stampIso, toaster,
} from '../../ui-kit/kit.js';
import {
  alertBox, badge, card, emptyState, hintBox, kpiRow, skeletonRows, statusLine, tabBar,
} from '../../ui-kit/layout.js';
import { formGrid } from '../../ui-kit/form.js';
import { dataTable } from '../../ui-kit/table.js';

/** Çekirdek sekmelerinin kimlikleri. Sunucu da aynı adları kullanır. */
const TAB_PRINTER = 'core.printer';
const TAB_UPDATE = 'core.update';
const TAB_DIAGNOSTICS = 'core.diagnostics';

/** Sunucunun bildirdiği kaynak → ekrandaki rozet. */
const SOURCE_BADGE = {
  file: { text: 'dosyadan', tone: 'dim' },
  store: { text: 'arayüzden ezildi', tone: 'warn' },
  env: { text: 'ortam değişkeni', tone: 'info' },
  default: { text: 'varsayılan', tone: 'dim' },
};

const STATE_LABELS = {
  loaded: 'çalışıyor',
  disabled: 'kapalı',
  failed: 'hata verdi',
  pending: 'bekliyor',
};

export function mount(root, ctx) {
  loadStyles(import.meta.url);          // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA

  const { api } = ctx;
  const view = h('div', 'kit-panel sa');
  root.append(view);

  const toast = toaster(view);
  const status = statusLine();
  const notice = h('div', 'sa-notice');
  const tabSlot = h('div', 'sa-tabs');
  const body = h('div', 'kit-body');

  view.append(tabSlot, status.node, notice, body);

  const state = {
    tabs: [],
    dropped: [],
    canManage: false,
    outputDir: '',
    active: null,
  };

  let form = null;                       // açık formGrid
  let disposed = false;

  // --------------------------------------------------------------- yardım

  function setNotice(node) {
    notice.replaceChildren();
    if (node) notice.append(node);
  }

  function currentTab() {
    return state.tabs.find((tab) => tab.id === state.active) || null;
  }

  function fields(tab) {
    return (tab.groups || []).flatMap((group) => group.fields);
  }

  /**
   * Sunucudaki alan tipi → kitin form alanı.
   *
   * `path_list` metin kutusuna açılır (satır başına bir yol): kitte liste
   * alanı yok ve bunun için kite alan eklemek, tek ekran uğruna paylaşılan
   * bileşen setini genişletmek olurdu.
   */
  function toFormField(field) {
    const kilitli = field.editable === false;
    const parcalar = [field.description, sourceSentence(field)].filter(Boolean);
    const ortak = {
      key: field.key,
      label: field.title,
      hint: parcalar.join(' · '),
      readOnly: kilitli,
      wide: field.type === 'path' || field.type === 'path_list' || field.type === 'cron',
    };

    switch (field.type) {
      case 'bool':
        return { ...ortak, type: 'checkbox' };
      case 'int':
        return {
          ...ortak,
          type: 'number',
          ...(field.min === undefined ? {} : { min: field.min }),
          ...(field.max === undefined ? {} : { max: field.max }),
        };
      case 'select':
        return {
          ...ortak,
          type: 'select',
          options: (field.options || []).map((option) => ({
            value: option.value, label: option.title,
          })),
        };
      case 'path_list':
        return {
          ...ortak,
          type: 'textarea',
          placeholder: 'Satır başına bir yol',
        };
      case 'cron':
        return {
          ...ortak,
          type: 'text',
          placeholder: '0 3 * * *',
          // ERKEN CEVAP, KAPI DEĞİL: kuralın kendisi sunucudadır (K9).
          validate: (value) => {
            const text = String(value || '').trim();
            if (!text) return null;
            return text.split(/\s+/).length === 5
              ? null
              : 'Beş alan olmalı: dakika saat gün ay hafta günü (örnek: 0 3 * * *).';
          },
        };
      default:
        return {
          ...ortak,
          type: 'text',
          ...(field.max_length ? { maxLength: field.max_length } : {}),
        };
    }
  }

  /** Alanın altında duran katman cümlesi — ekranın asıl işi. */
  function sourceSentence(field) {
    if (field.source === 'env') return field.lockedReason;
    if (field.source === 'store' && field.hasFile) {
      return `Bu değer dosyadan geliyordu, arayüzden ezildi. Dosyadaki değer: `
        + `“${display(field.layers?.file)}”.`;
    }
    if (field.source === 'store') return 'Bu değer arayüzden yazıldı.';
    if (field.source === 'file') return 'Bu değer ayar dosyasından geliyor.';
    return 'Hiçbir katmanda yazılı değil; ilan edilen varsayılan kullanılıyor.';
  }

  function display(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (Array.isArray(value)) return value.join(', ');
    if (typeof value === 'boolean') return value ? 'açık' : 'kapalı';
    return String(value);
  }

  /** Sunucudan gelen değer → form taslağı. */
  function toDraft(field) {
    if (field.type === 'path_list') return (field.value || []).join('\n');
    if (field.type === 'bool') return Boolean(field.value);
    if (field.type === 'int') return field.value === null || field.value === undefined
      ? null : Number(field.value);
    return field.value === null || field.value === undefined ? '' : String(field.value);
  }

  /** Form taslağı → sunucuya gidecek değer. */
  function fromDraft(field, raw) {
    if (field.type === 'path_list') {
      return String(raw || '').split('\n').map((line) => line.trim()).filter(Boolean);
    }
    if (field.type === 'bool') return Boolean(raw);
    if (field.type === 'int') {
      const sayi = Number(raw);
      return raw === '' || raw === null || Number.isNaN(sayi) ? null : Math.trunc(sayi);
    }
    return String(raw ?? '');
  }

  /**
   * Kaynağı ROZETLE de göster.
   *
   * Cümle zaten ipucunda duruyor; rozet TARAMAK içindir — on alanlık bir
   * sekmede hangisinin ezildiği bir bakışta görünmeli (renk tek başına anlam
   * taşımaz, rozetin içinde yazı da var — kit kuralı 7).
   *
   * Kitin çıktısına dokunuyoruz ama kiti DEĞİŞTİRMİYORUZ: alanlar `formGrid`
   * içinde verildikleri sırayla `.kit-field` olarak dizilir. Yapı değişirse
   * rozet çıkmaz, ekran çalışmaya devam eder — bilgi ipuçlarında da var.
   */
  function decorate(node, list) {
    const wraps = node.querySelectorAll(':scope > .kit-field');
    list.forEach((field, index) => {
      const label = wraps[index]?.querySelector('.kit-field-label');
      if (!label) return;
      const mark = SOURCE_BADGE[field.source] || SOURCE_BADGE.default;
      label.append(badge(mark.text, mark.tone));
    });
  }

  // ----------------------------------------------------------------- veri

  async function refresh(keepActive = true) {
    body.replaceChildren(skeletonRows(5, 2));
    status.set('Yükleniyor…');
    let payload;
    try {
      payload = await api('/api/settings');
    } catch (error) {
      status.set(error.message, true);
      setNotice(alertBox(error.message, 'bad'));
      body.replaceChildren(emptyState({
        title: 'Ayarlar okunamadı',
        text: 'Çekirdek yanıt vermedi. Uygulamanın geri kalanı çalışıyor olabilir.',
        actions: [button('Yeniden dene', { onClick: () => refresh() })],
      }));
      return;
    }
    if (disposed) return;

    state.tabs = payload.tabs || [];
    state.dropped = payload.dropped || [];
    state.canManage = Boolean(payload.canManage);
    state.outputDir = payload.outputDir || '';
    setNotice(state.canManage
      ? null
      : alertBox('Ayarları görebiliyorsunuz ama değiştiremezsiniz.', 'info'));

    const oncekiId = keepActive ? state.active : null;
    state.active = state.tabs.some((tab) => tab.id === oncekiId)
      ? oncekiId
      : state.tabs[0]?.id || null;

    paintTabs();
    paintBody();
  }

  function paintTabs() {
    const bar = tabBar(
      state.tabs.map((tab) => ({ key: tab.id, label: tab.title })),
      state.active,
      (key) => { state.active = key; paintBody(); },
    );
    tabSlot.replaceChildren(bar.node);
  }

  function paintBody() {
    form?.destroy();
    form = null;

    const tab = currentTab();
    if (!tab) {
      body.replaceChildren(emptyState({
        title: 'Görülebilecek ayar yok',
        text: 'Bu hesabın erişebildiği bir ayar sekmesi bulunmuyor.',
      }));
      status.set('');
      return;
    }

    const parts = [];

    if (tab.state && tab.state !== 'loaded') {
      // Modül kapalıysa ayarı yine gösterilir: çoğu zaman düzeltilmesi gereken
      // şey tam olarak odur. Ama durumu söylenmeden gösterilmez.
      parts.push(alertBox(
        `Bu bölüm şu an ${STATE_LABELS[tab.state] || tab.state}`
        + `${tab.reason ? ` — ${tab.reason}` : ''}. Ayarı değiştirebilirsiniz; `
        + 'yeniden açıldığında geçerli olur.',
        'warn',
      ));
    }

    parts.push(...formCards(tab));

    if (tab.id === TAB_PRINTER) parts.push(printerCard());
    if (tab.id === TAB_UPDATE) parts.push(updateCard());
    if (tab.id === TAB_DIAGNOSTICS) parts.push(...diagnosticsCards());

    body.replaceChildren(...parts);
    status.set(`${tab.title} · ${fields(tab).length} ayar`);
  }

  /** Sekmenin alan kartları ve kaydetme şeridi. */
  function formCards(tab) {
    const list = fields(tab);
    if (!list.length) return [];

    const draft = {};
    for (const field of list) draft[field.key] = toDraft(field);

    form = formGrid({ fields: list.map(toFormField), value: draft });

    // ALAN DÜĞÜMLERİ BİR KEZ OKUNUR. Aşağıda öbek öbek taşınıyorlar; her
    // turda yeniden sorulsaydı ilk öbek `form.node`'dan çıktığı anda kalan
    // dilimler kayar ve alanlar yanlış başlığın altına düşerdi.
    const wraps = [...form.node.querySelectorAll(':scope > .kit-field')];

    const cards = [];
    let index = 0;
    for (const group of tab.groups) {
      const box = h('div', 'sa-group');
      // Alanlar tek bir `formGrid` içinde duruyor (kirli alan takibi tek
      // yerde kalsın); öbek başlıkları için düğümleri gruplara dağıtıyoruz.
      const own = wraps.slice(index, index + group.fields.length);
      index += group.fields.length;
      box.append(...own);
      decorate(box, group.fields);
      cards.push(card(group.title, box));
    }

    const ezilenler = list.filter((field) => field.hasStore);
    const actions = h('div', 'sa-actions');

    actions.append(tab.canWrite
      ? button('Kaydet', { variant: 'primary', onClick: () => save(tab, list) })
      : blockedButton('Kaydet',
        'Ayarları değiştirmek için yetkiniz yok.', { variant: 'primary' }));

    if (ezilenler.length) {
      actions.append(tab.canWrite
        ? button(`Arayüzden ezilenleri geri al (${ezilenler.length})`, {
          onClick: () => revert(tab, ezilenler),
        })
        : blockedButton(`Arayüzden ezilenleri geri al (${ezilenler.length})`,
          'Ayarları değiştirmek için yetkiniz yok.'));
    }

    cards.push(actions);
    return cards;
  }

  async function save(tab, list) {
    form.showErrors();
    const hatalar = form.errors();
    if (hatalar.length) {
      setNotice(alertBox(hatalar[0].message, 'bad'));
      form.focus(hatalar[0].key);
      return;
    }

    const kirli = new Set(form.dirty());
    const taslak = form.draft();
    const values = {};
    for (const field of list) {
      if (!kirli.has(field.key) || field.editable === false) continue;
      values[field.key] = fromDraft(field, taslak[field.key]);
    }

    if (Object.keys(values).length === 0) {
      setNotice(alertBox('Değişen bir şey yok.', 'info'));
      return;
    }

    try {
      const sonuc = await api('/api/settings', { method: 'PUT', body: { tab: tab.id, values } });
      toast('Ayar kaydedildi.', 'good');
      // UYARI TAZELEMEDEN SONRA YAZILIR. `refresh()` bildirim alanını kendi
      // durumuna göre temizliyor; önce yazılsaydı kullanıcı hiç görmeden
      // silinirdi (tarayıcıda ölçüldü).
      await refresh();
      // "KAYDEDİLDİ" İLE "ETKİLİ OLDU" AYNI ŞEY DEĞİL: platform yetenekleri
      // ayarlarını açılışta okuyor. Sunucu bunu söylüyor, ekran da söyler.
      if (sonuc?.restartRequired) {
        setNotice(alertBox('Kaydedildi. Bazı ayarlar uygulama yeniden '
          + 'başlatıldığında geçerli olur.', 'warn'));
      }
    } catch (error) {
      // Sunucunun cümlesi OLDUĞU GİBİ gösterilir: sınır ihlalini, ortam
      // değişkeni çakışmasını ve yetki reddini en iyi o anlatıyor.
      setNotice(alertBox(error.message, 'bad'));
    }
  }

  async function revert(tab, ezilenler) {
    const onay = await confirmSimple(view, {
      title: 'Arayüzden yapılan değişiklikleri geri al',
      description: `${ezilenler.map((field) => field.title).join(', ')} alanları `
        + 'ayar dosyasındaki değerlerine döner. Dosyaya dokunulmaz.',
      confirmLabel: 'Geri al',
    });
    if (!onay) return;

    const values = {};
    for (const field of ezilenler) values[field.key] = null;
    try {
      await api('/api/settings', { method: 'PUT', body: { tab: tab.id, values } });
      toast('Dosyadaki değerlere dönüldü.', 'good');
      await refresh();
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
    }
  }

  // -------------------------------------------------------------- yazıcı

  function printerCard() {
    // `h(tag, class, text)` ÜÇÜNCÜ ARGÜMANI METİNDİR, çocuk listesi değil.
    // Düğümleri oraya vermek `[object HTMLDivElement]` yazdırır — bu ekranda
    // tam olarak bu oldu ve tarayıcıda görülene kadar hiçbir test yakalamadı.
    const box = h('div', 'sa-stack');
    const slot = h('div', 'sa-printer-body');

    const actions = h('div', 'sa-actions');
    const testButton = state.canManage
      ? button('Test sayfası bas', { onClick: () => printTestPage(testButton) })
      : blockedButton('Test sayfası bas', 'Baskı denemek için yetkiniz yok.');
    actions.append(button('Yenile', { onClick: () => loadPrinter(slot) }), testButton);
    box.append(slot, actions);

    loadPrinter(slot);
    return card('Yazıcı durumu', box,
      'Kuyruğun ne dediği ile kâğıdın çıkması ayrı şeylerdir.');
  }

  async function loadPrinter(slot) {
    slot.replaceChildren(skeletonRows(3, 4));
    let payload;
    try {
      payload = await api('/api/settings/printer');
    } catch (error) {
      slot.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    if (disposed) return;

    if (!payload.available) {
      slot.replaceChildren(alertBox(payload.reason || 'Baskı bu kurulumda yok.', 'info'));
      return;
    }

    const durum = payload.status || {};
    const parts = [];

    if (durum.error) parts.push(alertBox(durum.error, 'bad'));
    else if (durum.warning) parts.push(alertBox(durum.warning, 'warn'));

    const hedef = durum.target || null;
    parts.push(kpiRow([
      {
        label: 'Durum',
        value: durum.ready ? 'Hazır' : 'Basılamaz',
        tone: durum.ready ? 'good' : 'bad',
      },
      { label: 'Seçilen kuyruk', value: hedef?.name || '—' },
      { label: 'Bağlantı', value: hedef?.connection || '—' },
      {
        label: 'Toner',
        value: durum.device?.tonerPercent === undefined || durum.device?.tonerPercent === null
          ? '—' : `%${durum.device.tonerPercent}`,
      },
    ]));

    const kuyruklar = durum.printers || [];
    if (kuyruklar.length) {
      const table = dataTable({
        columns: [
          { key: 'name', label: 'Kuyruk', width: 'minmax(0, 1.4fr)' },
          { key: 'connection', label: 'Bağlantı', width: '150px' },
          { key: 'state', label: 'Hâli', width: '130px' },
          {
            key: 'ready',
            label: 'Baskı alır mı',
            width: 'minmax(0, 1fr)',
            // İKİ AYRI DURUM, İKİ AYRI CÜMLE: "iş kabul etmiyor" ile "işi
            // kabul eder ama kâğıt çıkmaz" aynı rozetle gösterilemez.
            cell: (row) => {
              const kutu = h('span', 'sa-flags');
              if (row.trapped) {
                kutu.append(badge('cihaza ulaşamıyor', 'bad'));
              } else if (!row.accepting) {
                kutu.append(badge('iş kabul etmiyor', 'warn'));
              } else if (row.ready) {
                kutu.append(badge('evet', 'good'));
              } else {
                kutu.append(badge('hayır', 'bad'));
              }
              return kutu;
            },
          },
        ],
        rows: kuyruklar,
        rowKey: (row) => row.name,
        empty: emptyState({ title: 'Kuyruk yok', text: 'Sistemde tanımlı yazıcı bulunmuyor.' }),
      });
      parts.push(table.node);
    }

    if (kuyruklar.some((row) => row.trapped)) {
      parts.push(hintBox(
        'Bu kuyruk işi kabul eder, “tamamlandı” yazar ve kâğıt çıkmaz: USB '
        + 'aygıtını `ipp-usb` servisi tekeline almış durumda. Sürücüsüz '
        + 'kuyruğu seçin ya da yazıcıyı sistem ayarlarından yeniden ekleyin.',
      ));
    }

    slot.replaceChildren(...parts);
  }

  async function printTestPage(node) {
    const onay = await confirmSimple(view, {
      title: 'Test sayfası bas',
      description: 'Yazıcıdan bir sayfa çıkacak. Kâğıt çıkmazsa sorun kuyrukta '
        + 'değil, cihaza giden yoldadır.',
      confirmLabel: 'Bas',
    });
    if (!onay) return;

    node.disabled = true;
    try {
      const sonuc = await api('/api/settings/printer/test-page', { method: 'POST' });
      toast(`Baskı gönderildi: ${sonuc.printer || 'yazıcı'}`, 'good');
      setNotice(alertBox(
        `Test sayfası “${sonuc.printer}” kuyruğuna gönderildi`
        + `${sonuc.job ? ` (iş: ${sonuc.job})` : ''}. Kâğıt çıkmadıysa aşağıdaki `
        + 'kuyruk listesine bakın.', 'info',
      ));
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
    } finally {
      node.disabled = false;
    }
  }

  // ---------------------------------------------------------- güncelleme

  function updateCard() {
    const slot = h('div', 'sa-stack');
    loadUpdate(slot);
    return card('Sürüm ve güncelleme', slot);
  }

  async function loadUpdate(slot) {
    slot.replaceChildren(skeletonRows(2, 3));
    let payload;
    try {
      payload = await api('/api/settings/update');
    } catch (error) {
      slot.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    if (disposed) return;

    const actions = h('div', 'sa-actions');
    // DÜĞME YA ÇALIŞIR YA NEDENİ YAZILI DURUR. Bugün güncelleme ucu yok ve
    // bunu ekran uydurmuyor — sunucu söylüyor (`canCheck` / `canInstall`).
    actions.append(payload.canCheck && payload.checkPath
      ? button('Denetle', { onClick: () => checkUpdate(payload.checkPath, slot) })
      : blockedButton('Denetle', payload.reason));
    actions.append(payload.canInstall && payload.installPath
      ? button('Şimdi kur', {
        variant: 'primary',
        onClick: () => installUpdate(payload.installPath),
      })
      : blockedButton('Şimdi kur', payload.reason, { variant: 'primary' }));

    slot.replaceChildren(
      kpiRow([
        { label: 'Kurulu sürüm', value: payload.version || '—' },
        {
          label: 'Son denetim',
          value: payload.lastChecked ? stampIso(payload.lastChecked) : 'hiç',
        },
      ]),
      alertBox(payload.reason || 'Güncelleme durumu bilinmiyor.', 'info'),
      actions,
    );
  }

  /**
   * Denetleme ucunun ADRESİNİ DE SUNUCU VERİR. `loadUpdate` yalnız durumu
   * okur; "Denetle" ise uzaktaki sürümü sorar. İkisini aynı şey saymak,
   * hiçbir şey denetlemeyen bir düğme bırakmaktı.
   */
  async function checkUpdate(path, slot) {
    try {
      await api(path, { method: 'POST' });
      await loadUpdate(slot);
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
    }
  }

  /**
   * Kurulum ucunun ADRESİNİ SUNUCU VERİR (`installPath`).
   *
   * Adresi ekrana yazmak, uç eklendiği gün iki yerin birbirinden habersiz
   * ayrışması demekti; düğme de "var ama yanlış yere gidiyor" olurdu. Adres
   * gelmediği sürece bu işlev hiç çağrılmaz — düğme kapalıdır.
   */
  async function installUpdate(path) {
    try {
      await api(path, { method: 'POST' });
      toast('Güncelleme başlatıldı.', 'good');
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
    }
  }

  // ------------------------------------------------------------- tanılama

  function diagnosticsCards() {
    const cards = [];

    if (state.dropped.length) {
      const liste = h('ul', 'sa-dropped');
      for (const entry of state.dropped) {
        liste.append(h('li', undefined, `${entry.name || entry.module}: ${entry.reason}`));
      }
      cards.push(card('Kurulamayan ayar sekmeleri', liste,
        'Bloğu geçersiz olan bölüm sekmesiz yüklenir; gerisi çalışmaya devam eder.'));
    }

    const slot = h('div', 'sa-stack');
    loadDiagnostics(slot);
    cards.push(card('Teknik günlük', slot,
      'Sunucu adresi, belirteç ve telefon MASKELENMİŞTİR.'));
    return cards;
  }

  async function loadDiagnostics(slot) {
    slot.replaceChildren(skeletonRows(6, 1));
    let payload;
    try {
      payload = await api('/api/settings/diagnostics');
    } catch (error) {
      slot.replaceChildren(alertBox(error.message, 'bad'));
      return;
    }
    if (disposed) return;

    const ozet = payload.summary || {};
    const moduller = ozet.moduller || [];
    const calisan = moduller.filter((row) => row.state === 'loaded').length;

    const actions = h('div', 'sa-actions');
    actions.append(button('Yenile', { onClick: () => loadDiagnostics(slot) }));
    actions.append(state.canManage
      ? button('Destek paketi indir', { variant: 'primary', onClick: () => downloadBundle() })
      : blockedButton('Destek paketi indir',
        'Destek paketi üretmek için ayar yönetimi yetkisi gerekir.', { variant: 'primary' }));

    const gunluk = h('pre', 'sa-log');
    gunluk.textContent = payload.lines?.length
      ? payload.lines.join('\n')
      : `Günlük dosyası okunamadı ya da boş: ${payload.logPath || '—'}`;

    slot.replaceChildren(
      kpiRow([
        { label: 'Sürüm', value: ozet.surum || '—' },
        {
          label: 'Platform',
          value: `${ozet.platform?.sistem || '—'} ${ozet.platform?.surum || ''}`.trim(),
          title: `Python ${ozet.platform?.python || '—'} · ${ozet.platform?.mimari || ''}`,
        },
        {
          label: 'Bölümler',
          value: `${calisan}/${moduller.length}`,
          tone: calisan === moduller.length ? 'good' : 'warn',
        },
      ]),
      hintBox(`Günlük: ${payload.logPath || '—'} · Çıktı klasörü: `
        + `${payload.outputDir || state.outputDir || '—'}`),
      gunluk,
      actions,
    );
  }

  async function downloadBundle() {
    try {
      const sonuc = await api('/api/settings/support-bundle', { method: 'POST' });
      toast('Destek paketi hazırlandı.', 'good');

      const kutu = h('div', 'sa-bundle');
      kutu.append(alertBox(
        `Destek paketi kaydedildi: ${sonuc.path} (${bytes(sonuc.bytes)}). `
        + 'İçerik maskelenmiştir: sunucu adresi, belirteç parçası ve telefon '
        + 'numarası çıkarılmıştır.', 'good',
      ));
      const satir = h('div', 'sa-actions');
      satir.append(button('Yolu kopyala', {
        onClick: async () => {
          toast(await copyText(sonuc.path) ? 'Yol kopyalandı.' : 'Kopyalanamadı.', '');
        },
      }));
      kutu.append(satir);
      setNotice(kutu);
    } catch (error) {
      setNotice(alertBox(error.message, 'bad'));
    }
  }

  // --------------------------------------------------------------- açılış

  refresh(false);

  return () => {
    disposed = true;
    form?.destroy();
  };
}
