// BBD Akademi — Hizmet kataloğu (Sözleşmeler → "Hizmet kataloğu"; A19, K7).
//
// "Kayıt ve ödeme planı" sihirbazının HİZMETLER tablosu bu katalogdan satır alır (ad, MEB kalemi, birim, birim fiyat,
// KDV). MEB sözleşmesinin ücret tablosu kalem türünden dolar: "Kurumun ilan ettiği" = katalog birim fiyatı.
// Okuma `admin_listContractServices`; ekleme / düzenleme `admin_createContractService` / `admin_updateContractService`
// (KM izni: sözleşme kurulumu). Silme yok — kullanılmayan hizmet "Etkin değil" yapılır (eski sözleşmeler bozulmasın).

import { h } from '../../../ui-kit/kit.js';
import { badge } from '../../../ui-kit/layout.js';
import { actionButton, formDialog, money, optionsOf, sectionHead, staticList } from '../common.js';
import { CONTRACT_CHANGED_EVENT, MEB_CATEGORIES } from './documents-logic.js';

const PATH = '/v1/admin/contracts/services';
export const UNITS = ['Saat', 'Ay', 'Dönem', 'Adet', 'Yıl', 'Paket'];

/** Form taslağı → A19 gövdesi (sunucu esastır; burada yalnız biçim). */
export function serviceBody(draft) {
  const vat = draft.vatPercent === '' || draft.vatPercent === null || draft.vatPercent === undefined
    ? null : Math.round(Number(draft.vatPercent));
  return {
    name: String(draft.name || '').trim().replace(/\s+/g, ' '),
    category: draft.category || 'education',
    unit: String(draft.unit || '').trim() || null,
    unitPriceMinor: Math.max(0, Math.round(Number(draft.unitPriceMinor) || 0)),
    vatPercent: vat,
    active: draft.active !== false,
    sortOrder: Math.max(0, Math.round(Number(draft.sortOrder) || 0)),
  };
}

export function servicesTab(app) {
  const node = h('div', 'ak-stack');
  const canCreate = app.can('admin_createContractService');
  const canEdit = app.can('admin_updateContractService');
  let rows = [];

  const add = actionButton(app, 'admin_createContractService', 'Hizmet ekle', { variant: 'primary',
    onClick: () => edit(null) });
  node.append(sectionHead('Hizmet kataloğu', {
    subtitle: 'Sözleşmenin HİZMETLER tablosuna gelen kalemler. Fiyat değişikliği yalnız yeni sözleşmelere uygulanır.',
    actions: [add],
  }));

  const list = staticList({
    load: () => app.api.get(PATH),
    map: (items) => [...items].sort((a, b) => (a.sortOrder ?? 0) - (b.sortOrder ?? 0)
      || String(a.name).localeCompare(String(b.name), 'tr')),
    onLoaded: (items) => { rows = items; },
    onRow: canEdit ? (row) => edit(row) : undefined,
    columns: [
      { key: 'name', label: 'Hizmet', width: 'minmax(0, 1.6fr)', cell: (row) => {
        const cell = h('div', 'ak-cell-stack');
        cell.append(h('b', '', row.name || '—'), h('small', '', MEB_CATEGORIES[row.category] || row.category || ''));
        return cell;
      } },
      { key: 'unit', label: 'Birim', width: '90px', cell: (row) => row.unit || '—' },
      { key: 'price', label: 'Birim fiyat', width: '130px', align: 'num', cell: (row) => money(row.unitPriceMinor ?? 0) },
      { key: 'vat', label: 'KDV', width: '80px', align: 'num',
        cell: (row) => (row.vatPercent === null || row.vatPercent === undefined ? 'Dahil / yok' : `%${row.vatPercent}`) },
      { key: 'active', label: 'Durum', width: '100px',
        cell: (row) => (row.active === false ? badge('Etkin değil', 'dim') : badge('Etkin', 'good')) },
      { key: 'sort', label: 'Sıra', width: '60px', align: 'num', cell: (row) => String(row.sortOrder ?? 0) },
    ],
    empty: { title: 'İlk hizmeti ekleyin (ör. GRUP DERSİ)',
      text: 'Hizmetler sözleşme sihirbazında tek tıkla satır olur; birim fiyatı ve MEB kalemi buradan gelir.',
      actions: canCreate ? [actionButton(app, 'admin_createContractService', 'Hizmet ekle', { variant: 'primary',
        onClick: () => edit(null) })] : [] },
  });
  node.append(list.node);

  async function edit(row) {
    const nextOrder = rows.reduce((max, item) => Math.max(max, Number(item.sortOrder) || 0), 0) + 10;
    const saved = await formDialog(app, {
      title: row ? 'Hizmeti düzenle' : 'Hizmet ekle',
      description: row ? 'Değişiklik yalnız bundan sonra oluşturulan sözleşmelere uygulanır.' : '',
      value: row ? { ...row, vatPercent: row.vatPercent ?? '' }
        : { name: '', category: 'education', unit: 'Saat', unitPriceMinor: null, vatPercent: '', active: true,
          sortOrder: nextOrder },
      fields: [
        { key: 'name', label: 'Hizmet adı', required: true, maxLength: 120, wide: true,
          placeholder: 'ör. GRUP DERSİ' },
        { key: 'category', label: 'MEB kalemi', type: 'select', required: true, options: optionsOf(MEB_CATEGORIES),
          hint: 'MEB sözleşmesinin ücret tablosunda hangi satıra yazılacağı.' },
        { key: 'unit', label: 'Birim', type: 'select', options: UNITS.map((unit) => ({ value: unit, label: unit })) },
        { key: 'unitPriceMinor', label: 'Birim fiyat', type: 'money', required: true, min: 0 },
        { key: 'vatPercent', label: 'KDV (%)', type: 'number', min: 0, max: 100,
          hint: 'Boş: fiyat KDV dahil ya da KDV yok.' },
        { key: 'sortOrder', label: 'Sıra', type: 'number', min: 0, max: 9999 },
        { key: 'active', label: 'Etkin (sihirbazda seçilebilir)', type: 'checkbox' },
      ],
      onSubmit: async (draft, { key }) => (row
        ? app.api.patch(`${PATH}/${encodeURIComponent(row.id)}`, serviceBody(draft), { key })
        : app.api.post(PATH, serviceBody(draft), { key })),
    });
    if (!saved) return;
    app.toast(row ? 'Hizmet güncellendi.' : 'Hizmet eklendi.', 'good');
    app.api.invalidate(PATH);
    list.reload();
    // Açık sihirbaz kataloğu tazelesin (aynı olay; sözleşme kimliği yok).
    window.dispatchEvent(new CustomEvent(CONTRACT_CHANGED_EVENT, { detail: { change: 'services' } }));
  }

  return { node, reload: () => list.reload() };
}
