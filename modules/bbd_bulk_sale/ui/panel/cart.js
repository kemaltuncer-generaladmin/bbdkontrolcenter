// Sepet kurucu — ürün ara / barkod okut / adet gir.
//
// Fiyat otoritesi kantindir: birim fiyat üründen gelir ve ekranda gösterilir.
// Gerekirse satır bazında değiştirilebilir (etkinlik indirimi vb.); değişen
// fiyat görsel olarak işaretlenir ki yanlışlıkla gönderilmesin.

import { button, foldText, h, money, moneyInput, parseMoney } from './kit.js';

export function createCart({ onChange }) {
  let products = [];
  let lines = [];              // {productId, name, qty, unitPrice, basePrice}

  const root = h('div', 'ct');

  const search = h('input', 'ct-search');
  search.type = 'search';
  search.placeholder = 'Ürün ara ya da barkod okut';
  search.autocomplete = 'off';

  const results = h('div', 'ct-results');
  const list = h('div', 'ct-lines');
  const total = h('div', 'ct-total');

  // Barkod okuyucu klavye taklidi yapar ve Enter'la biter: tam eşleşen barkod
  // varsa doğrudan sepete atar, alanı temizler — kesintisiz okutma.
  search.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    const code = search.value.trim();
    const exact = products.find((product) => String(product.barcode || '') === code);
    if (exact) {
      add(exact);
      search.value = '';
      renderResults();
      return;
    }
    const matches = matching();
    if (matches.length === 1) {
      add(matches[0]);
      search.value = '';
      renderResults();
    }
  });
  search.addEventListener('input', renderResults);

  root.append(search, results, list, total);
  // Açılışta da çiz: boş sepet yönergesi görünsün, alan sessizce boş kalmasın.
  // `notify: false` şart — bu noktada panel henüz kurulmadı, `onChange` içindeki
  // düğüm başvuruları hazır değil.
  render({ notify: false });

  function matching() {
    const needle = foldText(search.value);
    if (!needle) return [];
    return products
      .filter((product) => foldText(`${product.name} ${product.barcode || ''}`).includes(needle))
      .slice(0, 8);
  }

  function renderResults() {
    results.replaceChildren();
    for (const product of matching()) {
      const row = h('button', 'ct-result');
      row.type = 'button';
      row.append(
        h('span', 'ct-result-name', product.name),
        h('span', 'ct-result-price', money(product.price)),
        h('span', 'ct-result-stock', `stok ${product.stock}`),
      );
      row.addEventListener('click', () => {
        add(product);
        search.value = '';
        renderResults();
        search.focus();
      });
      results.append(row);
    }
  }

  function add(product, qty = 1) {
    const existing = lines.find((line) => line.productId === product.id);
    if (existing) existing.qty += qty;
    else {
      lines.push({
        productId: product.id, name: product.name, qty,
        unitPrice: Number(product.price), basePrice: Number(product.price),
      });
    }
    render();
  }

  function render({ notify = true } = {}) {
    list.replaceChildren();

    if (lines.length === 0) {
      list.append(h('div', 'ct-empty', 'Sepet boş. Ürün arayın ya da barkod okutun.'));
      total.textContent = '';
      if (notify) onChange?.();
      return;
    }

    for (const line of lines) {
      const row = h('div', 'ct-line');
      row.append(h('span', 'ct-line-name', line.name));

      const qty = h('input', 'ct-qty');
      qty.type = 'number';
      qty.min = '1';
      qty.max = '1000';
      qty.value = String(line.qty);
      qty.addEventListener('input', () => {
        line.qty = Math.max(1, Number(qty.value) || 1);
        renderTotal();
        onChange?.();
      });

      const price = h('input', `ct-price${line.unitPrice !== line.basePrice ? ' changed' : ''}`);
      price.type = 'text';
      price.value = moneyInput(line.unitPrice);
      price.title = line.unitPrice !== line.basePrice
        ? `Kantindeki fiyat ${money(line.basePrice)} — elle değiştirildi.`
        : 'Kantindeki güncel fiyat. Değiştirebilirsiniz.';
      price.addEventListener('input', () => {
        const parsed = parseMoney(price.value);
        if (parsed !== null && parsed >= 1) {
          line.unitPrice = parsed;
          price.classList.toggle('changed', line.unitPrice !== line.basePrice);
          renderTotal();
          onChange?.();
        }
      });

      const drop = h('button', 'ct-drop', '×');
      drop.type = 'button';
      drop.title = 'Satırı kaldır';
      drop.addEventListener('click', () => {
        lines = lines.filter((item) => item !== line);
        render();
      });

      row.append(qty, price, h('span', 'ct-line-total', money(line.qty * line.unitPrice)), drop);
      list.append(row);
    }
    renderTotal();
    if (notify) onChange?.();
  }

  function renderTotal() {
    const sum = lines.reduce((acc, line) => acc + line.qty * line.unitPrice, 0);
    total.replaceChildren(
      h('span', 'ct-total-label', `${lines.length} kalem`),
      h('b', 'ct-total-value', money(sum)),
    );
    // Satır toplamlarını da tazele.
    for (const [index, node] of [...list.querySelectorAll('.ct-line-total')].entries()) {
      const line = lines[index];
      if (line) node.textContent = money(line.qty * line.unitPrice);
    }
  }

  return {
    node: root,
    setProducts(list_) { products = list_ || []; renderResults(); },
    lines: () => lines.map((line) => ({
      productId: line.productId, qty: line.qty, unitPrice: line.unitPrice, name: line.name,
    })),
    amount: () => lines.reduce((acc, line) => acc + line.qty * line.unitPrice, 0),
    size: () => lines.length,
    load(cart) {
      lines = (cart || []).map((line) => {
        const product = products.find((item) => item.id === line.productId);
        return {
          productId: line.productId,
          name: line.name || product?.name || `#${line.productId}`,
          qty: Number(line.qty) || 1,
          // Şablondaki fiyat eski olabilir: kantindeki GÜNCEL fiyat esas alınır.
          unitPrice: Number(product?.price ?? line.unitPrice ?? 0),
          basePrice: Number(product?.price ?? line.unitPrice ?? 0),
        };
      });
      render();
    },
    clear() { lines = []; render(); },
    focus() { search.focus(); },
    addButton: (label, handler) => button(label, { onClick: handler }),
  };
}
