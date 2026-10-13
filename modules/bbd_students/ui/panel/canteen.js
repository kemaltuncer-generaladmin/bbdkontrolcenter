// Veli kantin kontrolleri — öğrenci detayının bir bölümü.
//
// KONTROLLER KANTİNDE DURUR ve satış anında kantin sunucusu uygular
// (bbd-akademi `kantin-veli-kontrolleri.md`). Bu ekran kopyasını tutmaz:
// açılışta okur, kaydedince yalnız DEĞİŞEN alanı yazar.
//
//   GET   /api/bbd_students/students/{id}/canteen             bbd_students.view
//   PATCH /api/bbd_students/students/{id}/canteen/controls    bbd_students.canteen_controls
//
// VELİ ENGELİ burada YALNIZ KALDIRILIR: kantini kapatmak velinin kararıdır;
// kurumun kendi engeli "Kurum engeli"dir. Backend de aynı kuralı uygular (K9).
//
// Tutarlar kuruştur; ekranda ₺ ve Türkçe biçim (ortak kit).
//
// Yol kabuğun KOPYALANMIŞ konumuna göredir (bkz. index.js).
import {
  confirmSimple, money, moneyInput, parseMoney,
} from '../../ui-kit/kit.js';

const h = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};

const BASE = '/api/bbd_students/students';

function balanceText(kurus) {
  const value = Number(kurus || 0);
  return value < 0 ? `alacak ${money(-value)}` : money(value);
}

function stamp(iso) {
  if (!iso) return 'hiç değişmedi';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleString('tr-TR', { dateStyle: 'short', timeStyle: 'short' });
}

/** Boş metin = sınırsız (null); aksi hâlde kuruş. Çözülemezse `undefined`. */
function parseLimit(text) {
  if (String(text ?? '').trim() === '') return null;
  const kurus = parseMoney(text);
  return kurus === null ? undefined : kurus;
}

/**
 * Bölümü `container` içine çizer. Dönüş `{ reload, destroy }`.
 * `options`: `{ api, studentId, canEdit, dialogRoot, onSaved? }`
 */
export function mountControls(container, options) {
  const { api, studentId, canEdit } = options;
  let data = null;
  let destroyed = false;
  let restricted = new Set();
  let filter = '';

  async function reload() {
    container.replaceChildren(h('p', 'st-hint', 'Kantin kontrolleri okunuyor…'));
    try {
      data = await api(`${BASE}/${encodeURIComponent(studentId)}/canteen`);
    } catch (error) {
      data = { available: false, error: error.message || String(error) };
    }
    if (!destroyed) render();
  }

  function render() {
    container.replaceChildren();
    if (!data?.available) {
      container.append(h('p', 'st-status bad',
        data?.notFound ? 'Öğrenci kantinde bulunamadı.'
          : `Kantin kontrolleri okunamadı — ${data?.error || 'bilinmeyen hata'}`));
      return;
    }
    const controls = data.controls;
    restricted = new Set(controls.restrictedProductIds || []);

    const facts = h('div', 'st-cc-facts');
    facts.append(
      h('span', null, `Bugün harcanan ${controls.spentToday === null
        || controls.spentToday === undefined ? '—' : money(controls.spentToday)}`),
      h('span', null, `Bakiye ${balanceText(controls.balance)}`),
      h('span', null, `Son değişiklik ${stamp(controls.controlsUpdatedAt)}`),
    );
    container.append(facts);

    const msg = h('div', 'st-cc-msg');

    if (controls.guardianBlocked) {
      const banner = h('div', 'st-cc-guardian');
      banner.append(h('i', 'st-badge', 'veli engeli'),
        h('span', null, 'Veli bu öğrencinin kantin alışverişini kapattı.'));
      const lift = h('button', 'st-btn', 'Veli engelini kaldır');
      lift.type = 'button';
      lift.disabled = !canEdit;
      if (!canEdit) lift.title = 'Kantin kontrollerini düzenleme izniniz yok.';
      lift.addEventListener('click', async () => {
        if (!await confirmSimple(options.dialogRoot || container, {
          title: 'Veli engeli kaldırılsın mı?',
          description: 'Veli kantini kapatmıştı. Kaldırırsanız öğrenci kasada yeniden '
            + 'alışveriş yapabilir; veli uygulamadan tekrar kapatabilir.',
          confirmLabel: 'Engeli kaldır',
          danger: true,
        })) return;
        await save({ guardianBlocked: false }, msg);
      });
      banner.append(lift);
      container.append(banner);
    }

    const grid = h('div', 'st-grid');
    const spending = limitField('Borç limiti (₺, boş = sınırsız)', controls.spendingLimit);
    const daily = limitField('Günlük harcama limiti (₺, boş = sınırsız)', controls.dailyLimit);
    grid.append(spending.wrap, daily.wrap);
    container.append(grid);

    const blockWrap = h('label', 'st-field st-field-inline');
    const blocked = h('input', 'st-check');
    blocked.type = 'checkbox';
    blocked.checked = Boolean(controls.isBlocked);
    blockWrap.append(blocked, h('span', 'st-label', 'Kurum engeli — kantin alışverişi kapalı'));
    container.append(blockWrap);

    container.append(productPicker());

    const save_ = h('button', 'st-btn st-btn-primary', 'Kontrolleri kaydet');
    save_.type = 'button';
    save_.disabled = !canEdit;
    if (!canEdit) save_.title = 'Kantin kontrollerini düzenleme izniniz yok.';
    save_.addEventListener('click', async () => {
      const patch = {};
      const spendingValue = parseLimit(spending.input.value);
      const dailyValue = parseLimit(daily.input.value);
      if (spendingValue === undefined || dailyValue === undefined) {
        msg.textContent = 'Limitleri 150,00 gibi yazın ya da sınırsız için boş bırakın.';
        msg.className = 'st-cc-msg bad';
        return;
      }
      if (spendingValue !== (controls.spendingLimit ?? null)) patch.spendingLimit = spendingValue;
      if (dailyValue !== (controls.dailyLimit ?? null)) patch.dailyLimit = dailyValue;
      if (blocked.checked !== Boolean(controls.isBlocked)) patch.isBlocked = blocked.checked;
      const ids = [...restricted].sort((a, b) => a - b);
      const before = [...(controls.restrictedProductIds || [])].sort((a, b) => a - b);
      if (ids.join(',') !== before.join(',')) patch.restrictedProductIds = ids;
      if (Object.keys(patch).length === 0) {
        msg.textContent = 'Değişiklik yok.';
        msg.className = 'st-cc-msg';
        return;
      }
      await save(patch, msg);
    });
    // `h()` üçüncü argümanı METİNDİR: düğme ayrıca eklenir (eskiden ekranda
    // "[object HTMLButtonElement]" yazıyordu ve kaydet düğmesi görünmüyordu).
    const actions = h('div', 'st-cc-actions');
    actions.append(save_);
    container.append(actions, msg);
  }

  function limitField(label, value) {
    const wrap = h('label', 'st-field');
    wrap.append(h('span', 'st-label', label));
    const input = h('input', 'st-input');
    input.type = 'text';
    input.inputMode = 'decimal';
    input.placeholder = 'sınırsız';
    input.value = value === null || value === undefined ? '' : moneyInput(value);
    input.disabled = !canEdit;
    wrap.append(input);
    return { wrap, input };
  }

  function productPicker() {
    const box = h('div', 'st-cc-products');
    const head = h('div', 'st-cc-products-head');
    const count = h('span', 'st-hint');
    const search = h('input', 'st-input st-cc-search');
    search.type = 'search';
    search.placeholder = 'Ürün ara';
    search.value = filter;
    head.append(h('span', 'st-label', 'Yasaklı ürünler'), count, search);
    box.append(head);

    const list = h('div', 'st-cc-product-list');
    box.append(list);

    const products = data.products || [];
    if (data.productsError) {
      box.append(h('p', 'st-status bad', `Ürün listesi okunamadı — ${data.productsError}`));
    }

    const paint = () => {
      count.textContent = restricted.size ? `${restricted.size} ürün yasaklı` : 'yasaklı ürün yok';
      list.replaceChildren();
      const needle = filter.toLocaleLowerCase('tr');
      const known = new Set(products.map((item) => item.id));
      const rows = [
        ...products,
        // Listede olmayan ama yasaklı kimlikler de görünür (silinmiş ürün).
        ...[...restricted].filter((id) => !known.has(id))
          .map((id) => ({ id, name: `Ürün #${id}`, price: null, active: false })),
      ].filter((item) => !needle || item.name.toLocaleLowerCase('tr').includes(needle));
      if (!rows.length) {
        list.append(h('p', 'st-hint', 'Aramaya uyan ürün yok.'));
        return;
      }
      for (const item of rows) {
        const row = h('label', `st-cc-product${item.active ? '' : ' passive'}`);
        const check = h('input');
        check.type = 'checkbox';
        check.checked = restricted.has(item.id);
        check.disabled = !canEdit;
        check.addEventListener('change', () => {
          if (check.checked) restricted.add(item.id);
          else restricted.delete(item.id);
          count.textContent = restricted.size ? `${restricted.size} ürün yasaklı`
            : 'yasaklı ürün yok';
        });
        row.append(check, h('span', null, item.name),
          h('span', 'st-hint', item.price === null ? '' : money(item.price)
            + (item.active ? '' : ' · pasif')));
        list.append(row);
      }
    };
    search.addEventListener('input', () => { filter = search.value.trim(); paint(); });
    paint();
    return box;
  }

  async function save(patch, msg) {
    msg.textContent = 'Kantine yazılıyor…';
    msg.className = 'st-cc-msg';
    try {
      const result = await api(`${BASE}/${encodeURIComponent(studentId)}/canteen/controls`, {
        method: 'PATCH', body: patch,
      });
      data = { ...data, controls: result.controls, available: true };
      if (destroyed) return;
      render();
      const note = container.querySelector('.st-cc-msg');
      if (note) {
        note.textContent = 'Kantin kontrolleri kaydedildi.';
        note.className = 'st-cc-msg good';
      }
      options.onSaved?.();
    } catch (error) {
      msg.textContent = `Kaydedilemedi: ${error.message}`;
      msg.className = 'st-cc-msg bad';
    }
  }

  reload();
  return {
    reload,
    destroy() {
      destroyed = true;
      container.replaceChildren();
    },
  };
}
