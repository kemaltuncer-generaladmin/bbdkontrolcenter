// Kullanıcı Yönetimi — ÇEKİRDEK EKRANI (ADR 0017).
//
// NE YAPAR: kadro listesi (ad, soyad, telefon, roller, durum, son giriş);
// kullanıcı ekleme ve düzenleme; ÇOKLU ROL atama; PIN atama/sıfırlama;
// pasifleştirme ve yeniden etkinleştirme.
//
// NE YAPMAZ:
//  · KULLANICI SİLMEZ. Silinen kullanıcının denetim izi ve "kim yaptı"
//    bağlantısı öksüz kalır. Her yerde pasifleştirme vardır; backend'de de
//    silme ucu yoktur.
//  · Rol TANIMLAMAZ. Roller `GET /api/roles`ten gelir; rol/izin düzenlemesi
//    ayrı bir ekranın işidir (permissions.md — "Roller ve İzinler").
//  · PIN'i hiçbir yerde SAKLAMAZ. Yazılan PIN gönderildikten sonra alandan da
//    silinir; sunucu zaten yalnız Argon2id özetini tutar.
//
// BU EKRAN MODÜL DEĞİLDİR: `modules/` klasörü tümüyle silinse bile açılmalıdır
// (ADR 0017 §4). Bu yüzden `registry.json`'a girmez, `shell/panels/` altına
// kopyalanmaz ve dosyaları doğrudan `shell/core-panels/users/` altından
// servis edilir. Import yolu bu YÜZDEN dosya sisteminde de çözülür — modül
// panellerindeki "kopyalanmadan çözülmez" uyarısı burada geçerli değildir.
//
// ÇİFT KAPI (K9). Buradaki her gizleme yalnız ARAYÜZ kolaylığıdır; aynı izin
// `km_core/http/users.py` içinde yeniden denetlenir. Ekranın kendisi de
// `users.view` ile menüye giriyor ve `GET /api/users` aynı izni tekrar soruyor.
//
// TUZAKLAR (ekranda karşılığı olanlar):
//  · İYİMSER KİLİT (ADR 0020). Düzenleme `expectedRevision` gönderir; aradan
//    başkası değiştirmişse sunucu 409 döner. O anda kullanıcının yazdıkları
//    ATILMAZ: neyin değiştiği gösterilir, karar kullanıcıya bırakılır.
//  · PIN BENZERSİZDİR ve çakışma KİMİNLE çakıştığını söylemez. Sunucunun
//    cümlesi olduğu gibi gösterilir; kendi cümlemizi yazmak, o cümlenin
//    bilerek sakladığı şeyi sızdırma riski taşır.
//  · UÇTAKİ ALAN ADI `password`, İSTENEN ŞEY 6 HANELİ PIN'dir: ADR 0016
//    reddedildi ama göçü koştu ve sütun/izin adları olduğu gibi bırakıldı.
//  · SON YÖNETİCİ pasifleştirilemez ve rolü alınamaz. Bu bir bütünlük
//    kısıtıdır ve sunucudadır; ekran reddi olduğu gibi, kalıcı bir kutuda
//    gösterir — kaybolan bir bildirim, kullanıcıya "neden olmadı" sorusunu
//    bırakırdı.
//
// ORTAK BİLEŞENLER kabuğun kitinden gelir (ADR 0011); ikinci bir bileşen seti
// doğurulmaz.

import {
  ago, blockedButton, button, confirmSimple, foldText, h, loadStyles, stampIso, toaster,
} from '../../ui-kit/kit.js';
import { dataTable } from '../../ui-kit/table.js';
import { filterBar } from '../../ui-kit/filters.js';
import {
  alertBox, badge, drawer, emptyState, skeletonRows, statusLine,
} from '../../ui-kit/layout.js';
import { formGrid, formatPhone, normalizePhone } from '../../ui-kit/form.js';

/** Kapsam, kullanıcının nereye bağlı olduğunu söyler; YETKİYİ BELİRLEMEZ. */
const SCOPES = [
  { value: 'org', label: 'Kurum' },
  { value: 'bbd', label: 'BBD' },
  { value: 'bld', label: 'BLD' },
];

const SCOPE_LABELS = Object.fromEntries(SCOPES.map((item) => [item.value, item.label]));

const STATUS_LABELS = { active: 'Aktif', disabled: 'Pasif' };
const STATUS_TONES = { active: 'good', disabled: 'dim' };

/** Düzenleme gövdesinde gönderilebilen alanlar → ekrandaki adı. */
const FIELD_LABELS = {
  firstName: 'Ad',
  lastName: 'Soyad',
  phoneMobile: 'Cep telefonu',
  orgScope: 'Kapsam',
  roles: 'Roller',
};

export function mount(root, ctx) {
  loadStyles(import.meta.url);        // panel.css — DOSYA TEPESİNDE DEĞİL, BURADA

  const { api } = ctx;
  const view = h('div', 'kit-panel us');
  root.append(view);

  const toast = toaster(view);
  const status = statusLine();
  const notice = h('div', 'us-notice');
  const tableWrap = h('div', 'us-table');

  const state = {
    users: [],
    roles: [],
    permissions: new Set(),
    filters: { q: '', status: '', role: '' },
  };

  let editor = null;               // açık çekmece (varsa)
  const disposers = [];

  // ------------------------------------------------------------------ izin

  /**
   * İzin sorgusu — MENÜ DEĞİL, DÜĞME içindir. Ekran `users.view` ile açılıyor
   * ama düzenleme `users.manage`, PIN atama `users.set_password` istiyor; ikisi
   * ayrı izinler ve biri olmadan öteki olabilir.
   *
   * Cevabı bilmeden düğme çizmek, tıklandığında ham bir 403 göstermek olurdu;
   * kitin kuralı bunu yasaklıyor: bir düğme YA ÇALIŞIR, YA BURADAN GEÇER, ya
   * da hiç çizilmez. Yetkiyi yine sunucu uygular (K9).
   */
  const can = (key) => state.permissions.has(key) || state.permissions.has(`${key}:*`);

  // ---------------------------------------------------------------- süzgeç

  // "Yeni kullanıcı" düğmesi izinler geldikten SONRA çizilir; yeri şimdiden
  // ayrılır ki şerit sonradan zıplamasın.
  const newButtonSlot = h('span', 'us-new');

  const filters = filterBar({
    fields: [
      { kind: 'search', key: 'q', placeholder: 'Ad, soyad veya telefon ara', width: '240px' },
      {
        kind: 'select',
        key: 'status',
        label: 'Durum',
        options: [
          { value: '', label: 'Tümü' },
          { value: 'active', label: 'Aktif' },
          { value: 'disabled', label: 'Pasif' },
        ],
      },
      { kind: 'select', key: 'role', label: 'Rol', options: [{ value: '', label: 'Tümü' }] },
    ],
    onChange: (values) => {
      state.filters = { ...state.filters, ...values };
      paintTable();
    },
    actions: [button('Yenile', { onClick: () => refresh() }), newButtonSlot],
  });
  disposers.push(() => filters.destroy());

  // ----------------------------------------------------------------- tablo

  const table = dataTable({
    columns: [
      { key: 'firstName', label: 'Ad', width: 'minmax(0, 1fr)', sortable: true },
      { key: 'lastName', label: 'Soyad', width: 'minmax(0, 1fr)', sortable: true },
      {
        key: 'phoneMobile',
        label: 'Telefon',
        width: '150px',
        cell: (row) => (row.phoneMobile ? formatPhone(row.phoneMobile) : ''),
      },
      {
        key: 'roles',
        label: 'Roller',
        width: 'minmax(0, 1.6fr)',
        // ROLLER TEK TEK ÇİZİLİR. Etkin izin rollerin BİRLEŞİMİDİR
        // (ARCHITECTURE §8); "3 rol" yazmak hangilerinin birleştiğini gizlerdi.
        cell: (row) => {
          if (!row.roles?.length) return '';
          const box = h('span', 'us-roles');
          for (const id of row.roles) box.append(badge(roleName(id), 'info'));
          return box;
        },
      },
      {
        key: 'status',
        label: 'Durum',
        width: '160px',
        // Renk TEK BAŞINA anlam taşımaz: rozetin içinde yazı da var (kural 7).
        cell: (row) => {
          const box = h('span', 'us-state');
          box.append(badge(STATUS_LABELS[row.status] || row.status, STATUS_TONES[row.status] || ''));
          if (row.mustSetPassword) box.append(badge('PIN bekliyor', 'warn'));
          else if (row.lockedUntil) box.append(badge('Kilitli', 'bad'));
          return box;
        },
      },
      {
        key: 'lastLoginAt',
        label: 'Son giriş',
        width: '150px',
        sortable: true,
        // "Hiç girmemiş" ile "bilinmiyor" AYNI ŞEY DEĞİL: boş bırakmak ikincisi
        // gibi okunurdu.
        cell: (row) => {
          if (!row.lastLoginAt) return h('span', 'us-never', 'hiç girmemiş');
          const node = h('span', undefined, ago(row.lastLoginAt));
          node.title = stampIso(row.lastLoginAt);
          return node;
        },
      },
    ],
    rows: [],
    onRow: (row) => openEditor(row),
    empty: emptyState({ title: 'Kayıt yok', text: 'Süzgeçlere uyan kullanıcı bulunmuyor.' }),
  });
  tableWrap.append(table.node);

  view.append(filters.node, status.node, notice, tableWrap);

  // --------------------------------------------------------------- yardım

  function roleName(id) {
    return state.roles.find((role) => role.id === id)?.name || id;
  }

  /**
   * Kalıcı uyarı kutusu. `toaster` beş saniyede kaybolur; "son yönetici
   * pasifleştirilemez" gibi bir ret, kullanıcı ne yapacağına karar verene
   * kadar ekranda DURMALI.
   */
  function setNotice(text, tone = 'bad') {
    notice.replaceChildren();
    if (text) notice.append(alertBox(text, tone));
  }

  function visibleRows() {
    const needle = foldText(state.filters.q || '');
    return state.users.filter((row) => {
      if (state.filters.status && row.status !== state.filters.status) return false;
      if (state.filters.role && !(row.roles || []).includes(state.filters.role)) return false;
      if (!needle) return true;
      return foldText(`${row.firstName} ${row.lastName} ${row.phoneMobile || ''}`).includes(needle);
    });
  }

  function paintTable() {
    const rows = visibleRows();
    table.update({ rows });
    const aktif = state.users.filter((row) => row.status === 'active').length;
    const bekleyen = state.users.filter((row) => row.mustSetPassword).length;
    status.set(
      `${rows.length} kayıt görünüyor · ${state.users.length} kullanıcı · `
      + `${aktif} aktif${bekleyen ? ` · ${bekleyen} kişi PIN'ini henüz belirlemedi` : ''}`,
    );
  }

  function paintNewButton() {
    // Ekleme İKİ İZİN birden ister: kaydı açmak `users.manage`, ilk PIN'i
    // atamak `users.set_password` (backend ikisini ayrı ayrı kuruyor).
    const eksik = ['users.manage', 'users.set_password'].filter((key) => !can(key));
    newButtonSlot.replaceChildren(
      eksik.length === 0
        ? button('Yeni kullanıcı', { variant: 'primary', onClick: () => openEditor(null) })
        : blockedButton('Yeni kullanıcı',
          'Kullanıcı eklemek için hem kullanıcı yönetimi hem PIN atama yetkisi gerekir.',
          { variant: 'primary' }),
    );
  }

  // ----------------------------------------------------------------- veri

  async function refresh() {
    tableWrap.replaceChildren(skeletonRows(6, 6));
    status.set('Yükleniyor…');
    try {
      const payload = await api('/api/users');
      state.users = payload.users || [];
      setNotice('');
    } catch (error) {
      state.users = [];
      status.set(error.message, true);
      setNotice(error.message);
      tableWrap.replaceChildren(table.node);
      table.update({ rows: [] });
      return;
    }
    tableWrap.replaceChildren(table.node);
    paintTable();
  }

  /**
   * Rol listesi SABİT YAZILMAZ (`GET /api/roles`). Yeni rol tanımlanabilir
   * (permissions.md); kabuğa gömülü bir liste, tanımlandığı gün eskir ve
   * kimse fark etmez.
   */
  async function loadRoles() {
    try {
      const payload = await api('/api/roles');
      state.roles = payload.roles || [];
      filters.options('role', [
        { value: '', label: 'Tümü' },
        ...state.roles.map((role) => ({ value: role.id, label: role.name })),
      ]);
    } catch (error) {
      state.roles = [];
      // Rol listesi olmadan kayıt AÇILAMAZ: en az bir rol zorunlu ve rol
      // kimlikleri uydurulamaz. Sebep ekranda kalır.
      setNotice(`Rol listesi alınamadı: ${error.message}`, 'warn');
    }
  }

  async function loadPermissions() {
    try {
      const me = await api('/auth/me');
      state.permissions = new Set(me.permissions || []);
    } catch {
      // Oturum özeti alınamazsa hiçbir yönetim düğmesi çizilmez; ekran yine
      // açılır ve liste okunur. Sessizce "yetkili" varsaymak, kullanıcıyı
      // tıkladığında 403 alan bir düğmeye gönderirdi.
      state.permissions = new Set();
    }
    paintNewButton();
  }

  // ------------------------------------------------------------- çekmece

  /** `row` null ise yeni kayıt. */
  function openEditor(row) {
    editor?.close();

    const yeni = row === null;
    // Düzenlemede temel alınan revizyon. Sunucuya bunu gönderiyoruz; aradan
    // değişmişse 409 gelir (ADR 0020).
    let baseRevision = yeni ? null : Number(row.revision || 1);

    const alanlar = [
      { key: 'firstName', label: 'Ad', type: 'text', required: true, maxLength: 120 },
      { key: 'lastName', label: 'Soyad', type: 'text', required: true, maxLength: 120 },
      {
        key: 'phoneMobile',
        label: 'Cep telefonu',
        type: 'phone',
        hint: 'İç rehberde ve bildirimlerde kullanılır. Zorunlu değil.',
      },
      {
        key: 'orgScope',
        label: 'Kapsam',
        type: 'select',
        options: SCOPES,
        hint: 'Kişinin nereye bağlı olduğunu söyler; YETKİYİ BELİRLEMEZ — yetki yalnız rollerden gelir.',
      },
      {
        // ALAN GİZLENMEZ, BİLEREK. Buradaki PIN YÖNETİCİNİN KENDİ PIN'İ DEĞİL,
        // başkasına atadığı PIN'dir ve kullanıcıya okuyup söylemesi gerekir.
        // Yıldızlarla yazılan bir alanda yazım hatası görünmez ve yönetici
        // yanlış PIN'i doğru sanarak iletir. Kişinin kendi PIN'ini yazdığı tek
        // yer giriş ekranıdır; orada tuş takımı yalnız hane SAYISINI gösterir.
        //
        // Anahtar adı (`password`) uçtaki alan adıdır ve göçten kalmıştır.
        key: 'password',
        label: yeni ? 'PIN' : 'Yeni PIN',
        type: 'text',
        required: yeni,
        maxLength: 12,
        wide: true,
        hint: yeni
          ? 'En az 6 hane, yalnızca rakam. Basit ve ardışık PIN kabul edilmez.'
          : "Boş bırakırsanız PIN değişmez. Yazarsanız kullanıcının PIN'i sıfırlanır.",
        // ERKEN CEVAP, KAPI DEĞİL: kuralın tamamı (basit PIN, ardışık dizi,
        // benzersizlik) sunucudadır ve orada yeniden denetlenir (K9). Burada
        // yalnız gönderilmeden anlaşılabilecek iki ihlal söylenir.
        validate: (value) => {
          const pin = String(value || '');
          if (!pin) return null;
          if (!/^\d+$/.test(pin)) return 'PIN yalnızca rakamlardan oluşur.';
          if (pin.length < 6) return 'PIN en az 6 hane olmalı.';
          return null;
        },
      },
    ];

    const form = formGrid({
      fields: alanlar,
      value: yeni
        ? { firstName: '', lastName: '', phoneMobile: '', orgScope: 'org', password: '' }
        : {
          firstName: row.firstName || '',
          lastName: row.lastName || '',
          phoneMobile: row.phoneMobile || '',
          orgScope: row.orgScope || 'org',
          password: '',
        },
    });

    const roles = roleChooser(yeni ? [] : row.roles || []);
    const drawerNotice = h('div', 'us-drawer-notice');

    const kaydet = button(yeni ? 'Oluştur' : 'Kaydet', {
      variant: 'primary',
      onClick: () => save(),
    });

    const actions = [];
    if (!yeni) actions.push(statusButton(row));

    editor = drawer(view, {
      title: yeni ? 'Yeni kullanıcı' : `${row.firstName} ${row.lastName}`.trim(),
      subtitle: yeni
        ? "Kayıt açılırken en az bir rol ve bir PIN zorunludur."
        : `${SCOPE_LABELS[row.orgScope] || row.orgScope} · ${STATUS_LABELS[row.status] || row.status}`
          + ` · ${row.lastLoginAt ? `son giriş ${ago(row.lastLoginAt)}` : 'hiç giriş yapmamış'}`,
      actions,
      onClose: () => {
        form.destroy();
        editor = null;
      },
    });

    const actionRow = h('div', 'us-actions');
    actionRow.append(can('users.manage')
      ? kaydet
      : blockedButton(yeni ? 'Oluştur' : 'Kaydet',
        'Kullanıcı kaydını değiştirmek için yetkiniz yok.', { variant: 'primary' }));
    editor.body.append(drawerNotice, form.node, roles.node, actionRow);

    if (!can('users.manage')) {
      drawerNotice.append(alertBox('Bu ekranı görebiliyorsunuz ama kayıt değiştiremezsiniz.', 'info'));
    }

    form.focus('firstName');

    function showDrawerNotice(node) {
      drawerNotice.replaceChildren();
      if (node) drawerNotice.append(node);
    }

    async function save() {
      showDrawerNotice(null);
      form.showErrors();

      const hatalar = form.errors();
      if (hatalar.length) {
        showDrawerNotice(alertBox(hatalar[0].message, 'bad'));
        form.focus(hatalar[0].key);
        return;
      }
      if (roles.selection().length === 0) {
        // EN AZ BİR ROL ZORUNLU. Rolsüz kullanıcı hiçbir şey göremez; sunucu da
        // reddediyor ("En az bir rol zorunlu.").
        showDrawerNotice(alertBox('En az bir rol seçin — yetki yalnız rollerden gelir.', 'bad'));
        return;
      }

      kaydet.disabled = true;
      try {
        if (yeni) await create();
        else await update();
      } catch (error) {
        // Sunucunun cümlesi OLDUĞU GİBİ gösterilir: PIN çakışması kiminle
        // çakıştığını, son yönetici kısıtı da neyi koruduğunu kendi
        // cümlesiyle anlatıyor.
        showDrawerNotice(alertBox(error.message, 'bad'));
      } finally {
        kaydet.disabled = false;
      }
    }

    /**
     * Telefon ALANDAN ÇIKARKEN normalleşir (`formGrid` 'phone' tipi), ama
     * kullanıcı alandan çıkmadan Kaydet'e basabilir. O yüzden gönderimden
     * hemen önce bir kez daha indirgenir: sunucuya "0532 123 45 67" ile
     * "5321234567" arasında gidip gelen iki farklı biçim yazılmasın.
     */
    const phoneOf = (taslak) => {
      const digits = normalizePhone(taslak.phoneMobile || '');
      return digits || null;
    };

    async function create() {
      const taslak = form.draft();
      await api('/api/users', {
        method: 'POST',
        body: {
          firstName: String(taslak.firstName || '').trim(),
          lastName: String(taslak.lastName || '').trim(),
          orgScope: taslak.orgScope || 'org',
          roles: roles.selection(),
          password: taslak.password,
          phoneMobile: phoneOf(taslak),
        },
      });
      editor.close();
      toast('Kullanıcı oluşturuldu.', 'good');
      await refresh();
    }

    async function update() {
      const taslak = form.draft();
      const govde = { expectedRevision: baseRevision };
      // GÖNDERİLMEYEN ALANA DOKUNULMAZ (sunucu `exclude_unset` kullanıyor);
      // yine de yalnız DEĞİŞENİ göndeririz — denetim kaydı okunur kalsın.
      const kirli = new Set(form.dirty());
      if (kirli.has('firstName')) govde.firstName = String(taslak.firstName || '').trim();
      if (kirli.has('lastName')) govde.lastName = String(taslak.lastName || '').trim();
      if (kirli.has('orgScope')) govde.orgScope = taslak.orgScope;
      if (kirli.has('phoneMobile')) govde.phoneMobile = phoneOf(taslak);
      if (roles.changed()) govde.roles = roles.selection();

      const sifre = String(taslak.password || '');
      const alanDegisti = Object.keys(govde).length > 1;

      if (alanDegisti) {
        try {
          const sonuc = await api(`/api/users/${row.id}`, { method: 'PUT', body: govde });
          baseRevision = Number(sonuc.user?.revision || baseRevision);
        } catch (error) {
          if (error.status === 409) {
            await handleConflict(govde);
            return;
          }
          throw error;
        }
      }

      if (sifre) {
        // PIN AYRI UÇTAN gider: `users.set_password` ayrı bir izindir ve
        // düzenleme gövdesi sır alanı kabul etmez.
        await api(`/api/users/${row.id}/password`, { method: 'POST', body: { password: sifre } });
        form.set('password', '');
      }

      if (!alanDegisti && !sifre) {
        showDrawerNotice(alertBox('Değişen bir şey yok.', 'info'));
        return;
      }

      editor.close();
      toast(sifre && !alanDegisti ? 'PIN değiştirildi.' : 'Kayıt güncellendi.', 'good');
      await refresh();
    }

    /**
     * 409 — İYİMSER KİLİT ÇAKIŞMASI (ADR 0020).
     *
     * KULLANICININ YAZDIKLARI ATILMAZ. Form olduğu gibi durur; aradan ne
     * değiştiği alan alan gösterilir ve karar kullanıcıya bırakılır:
     * yazdıklarını üste yazmak ya da sunucudakini almak. Formu sessizce
     * tazelemek, dakikalarca yazılmış bir kaydı yok etmekti.
     */
    async function handleConflict(govde) {
      let taze = null;
      try {
        const payload = await api('/api/users');
        taze = (payload.users || []).find((item) => item.id === row.id) || null;
      } catch {
        taze = null;
      }

      if (!taze) {
        showDrawerNotice(alertBox(
          'Bu kayıt siz düzenlerken değişti ve şu an okunamıyor. '
          + 'Yazdıklarınız duruyor; birazdan tekrar deneyin.', 'warn'));
        return;
      }

      const kutu = h('div', 'us-conflict');
      kutu.append(alertBox(
        `Bu kaydı siz açtıktan sonra başka biri değiştirdi `
        + `(son değişiklik: ${stampIso(taze.updatedAt) || taze.updatedAt}). `
        + 'Yazdıklarınız duruyor — ne yapılacağına siz karar verin.', 'warn'));

      const farklar = diffRows(row, taze);
      if (farklar.length) {
        const liste = h('ul', 'us-diff');
        for (const fark of farklar) {
          liste.append(h('li', undefined, `${fark.label}: “${fark.before}” → “${fark.after}”`));
        }
        kutu.append(h('p', 'us-diff-title', 'Aradan değişenler:'), liste);
      }

      const secim = h('div', 'us-conflict-actions');
      secim.append(
        button('Benim yazdıklarımı kaydet', {
          variant: 'primary',
          onClick: async () => {
            try {
              const sonuc = await api(`/api/users/${row.id}`, {
                method: 'PUT',
                body: { ...govde, expectedRevision: Number(taze.revision || 1) },
              });
              baseRevision = Number(sonuc.user?.revision || taze.revision);
              editor.close();
              toast('Kayıt güncellendi.', 'good');
              await refresh();
            } catch (error) {
              showDrawerNotice(alertBox(error.message, 'bad'));
            }
          },
        }),
        button('Sunucudakini al', {
          onClick: () => {
            // Bu düğme AÇIKÇA seçilir: yazdıklarını atmak kullanıcının kararı.
            form.reset({
              firstName: taze.firstName || '',
              lastName: taze.lastName || '',
              phoneMobile: taze.phoneMobile || '',
              orgScope: taze.orgScope || 'org',
              password: '',
            });
            roles.reset(taze.roles || []);
            baseRevision = Number(taze.revision || 1);
            showDrawerNotice(alertBox('Sunucudaki hâl yüklendi.', 'info'));
          },
        }),
      );
      kutu.append(secim);
      showDrawerNotice(kutu);
    }

    function statusButton(kayit) {
      const pasif = kayit.status !== 'active';
      const etiket = pasif ? 'Etkinleştir' : 'Pasifleştir';

      if (!can('users.manage')) {
        return blockedButton(etiket, 'Kullanıcı durumunu değiştirmek için yetkiniz yok.');
      }

      return button(etiket, {
        variant: pasif ? '' : 'danger',
        onClick: async () => {
          // SİLME YOK, PASİFLEŞTİRME VAR (kit kuralı 8). Gerekçe SORULMAZ:
          // durum ucu gerekçe alanı kabul etmiyor ve sormak, hiçbir yere
          // gitmeyen bir kutu olurdu.
          const onay = await confirmSimple(view, {
            title: `${etiket}: ${kayit.firstName} ${kayit.lastName}`.trim(),
            description: pasif
              ? 'Kullanıcı yeniden giriş yapabilecek.'
              : 'Açık oturumları kapanır ve giriş yapamaz. Kayıt silinmez.',
            confirmLabel: etiket,
            danger: !pasif,
          });
          if (!onay) return;

          try {
            await api(`/api/users/${kayit.id}/status`, {
              method: 'POST',
              body: { status: pasif ? 'active' : 'disabled' },
            });
            editor.close();
            toast(pasif ? 'Kullanıcı etkinleştirildi.' : 'Kullanıcı pasifleştirildi.', 'good');
            await refresh();
          } catch (error) {
            // SON YÖNETİCİ KISITI BURADAN GEÇER. Sunucunun cümlesi kalıcı
            // kutuda durur; kaybolan bir bildirim "neden olmadı" sorusunu
            // yanıtsız bırakırdı.
            showDrawerNotice(alertBox(error.message, 'bad'));
          }
        },
      });
    }
  }

  /**
   * ÇOKLU ROL SEÇİCİ.
   *
   * Bir kullanıcıya BİRDEN FAZLA rol atanabilir ve etkin izinler rollerin
   * BİRLEŞİMİDİR (ARCHITECTURE §8). Bu yüzden açılır kutu değil, onay kutusu:
   * açılır kutu "tek rol" der ve modeli yanlış anlatır.
   *
   * Roller azdır ve her birinin bir açıklaması vardır; `createPicker` o
   * açıklamayı aramanın arkasına gizlerdi. Kutular kitin `kit-check` ve
   * `kit-field` sınıflarıyla çizilir — ikinci bir bileşen seti değildir.
   */
  function roleChooser(initial) {
    const node = h('div', 'us-roles-field');
    node.append(h('span', 'kit-field-label', 'Roller'));
    node.append(h('span', 'kit-field-hint',
      'Birden fazla rol seçilebilir; kullanıcının izinleri seçtiklerinizin birleşimidir. '
      + 'En az bir rol zorunlu.'));

    const list = h('div', 'us-role-list');
    node.append(list);

    let original = [...initial].sort();
    const chosen = new Set(initial);
    const boxes = new Map();

    const paint = () => {
      list.replaceChildren();
      boxes.clear();
      if (state.roles.length === 0) {
        list.append(h('p', 'kit-field-error', 'Rol listesi okunamadı; kayıt açılamaz.'));
        return;
      }
      for (const role of state.roles) {
        const satir = h('label', 'us-role');
        const kutu = h('input', 'kit-check');
        kutu.type = 'checkbox';
        kutu.checked = chosen.has(role.id);
        kutu.addEventListener('change', () => {
          if (kutu.checked) chosen.add(role.id);
          else chosen.delete(role.id);
        });
        const metin = h('span', 'us-role-text');
        metin.append(h('span', 'us-role-name', role.name));
        if (role.description) metin.append(h('span', 'us-role-desc', role.description));
        satir.append(kutu, metin);
        boxes.set(role.id, kutu);
        list.append(satir);
      }
    };
    paint();

    return {
      node,
      selection: () => state.roles.map((role) => role.id).filter((id) => chosen.has(id)),
      changed() {
        const now = [...chosen].sort();
        return now.length !== original.length || now.some((id, i) => id !== original[i]);
      },
      reset(next) {
        chosen.clear();
        for (const id of next || []) chosen.add(id);
        original = [...chosen].sort();
        paint();
      },
    };
  }

  /** İki kayıt arasında EKRANDA GÖSTERİLEN alanların farkı. */
  function diffRows(before, after) {
    const out = [];
    for (const key of ['firstName', 'lastName', 'phoneMobile', 'orgScope']) {
      const eski = before[key] ?? '';
      const yeni = after[key] ?? '';
      if (String(eski) === String(yeni)) continue;
      out.push({ label: FIELD_LABELS[key] || key, before: eski || '—', after: yeni || '—' });
    }
    const eskiRoller = [...(before.roles || [])].sort().join(', ');
    const yeniRoller = [...(after.roles || [])].sort().join(', ');
    if (eskiRoller !== yeniRoller) {
      out.push({
        label: FIELD_LABELS.roles,
        before: eskiRoller || '—',
        after: yeniRoller || '—',
      });
    }
    if (before.status !== after.status) {
      out.push({
        label: 'Durum',
        before: STATUS_LABELS[before.status] || before.status,
        after: STATUS_LABELS[after.status] || after.status,
      });
    }
    return out;
  }

  // --------------------------------------------------------------- açılış
  //
  // Düğme İZİNLER GELDİKTEN SONRA çizilir. Önce "yetkiniz yok" yazan bir düğme
  // koymak, aslında yetkisi olan kullanıcıya bir an yanlış cümle göstermekti.

  (async () => {
    await loadPermissions();
    await loadRoles();
    await refresh();
  })();

  // TEMİZLİK GERÇEK KAYNAK BIRAKIR: `filterBar` ve `formGrid` global dinleyici
  // tutuyor, açık çekmece de `keydown` dinliyor.
  return () => {
    editor?.close();
    for (const dispose of disposers) dispose();
  };
}
