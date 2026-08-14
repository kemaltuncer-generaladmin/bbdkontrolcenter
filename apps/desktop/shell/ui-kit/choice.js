// Tek seçenekli alan — çizilmez, kendiliğinden dolar.
//
// Tek kopya (ADR 0011). Bu dosya BİR KURALI uygular ve o kural tek cümledir:
//
//   Kullanıcıya sorulan sorunun tek cevabı varsa, soru sorulmaz.
//
// NEDEN VAR. Bu mağazada kanal bir tane ("default"), dil bir tane (tr), para
// birimi bir tane (TRY), stok kaynağı bir tane, vergi kategorisi bir tane.
// Yirmi ekranda bunların açılır kutusu çiziliyordu ve her biri kullanıcıya
// "seç" diyordu — seçilecek bir şey olmadan. Boş sorunun bedeli yalnız
// kalabalık değil: personel "acaba yanlış kanalı mı seçtim" diye düşünüyor ve
// gerçekte var olmayan bir ayrım arıyor.
//
// SERT KODLAMA YOK — ekranlarda "kanal her zaman default" yazmaz. Karar
// VERİDEN çıkar ve üç hâli vardır:
//
//   seçenek > 1  → kutu çizilir (bugün olmayan hâl; yarın ikinci kanal
//                  açılırsa ekran kendiliğinden geri gelir)
//   seçenek = 1  → kutu ÇİZİLMEZ; form alanı o değeri kendiliğinden gönderir
//   seçenek = 0  → kutu ÇİZİLMEZ; ekran DURUMU SÖYLER (sessizce boş kalmaz)
//
// K9 — GİZLEMEK YETKİLENDİRME DEĞİLDİR. Burada gizlenen şey bir YETKİ değil,
// tek cevaplı bir sorudur. Backend ne gönderiyorsa göndermeye devam eder;
// hiçbir uç kısılmaz, hiçbir izin bu dosyaya bakmaz.
//
// ─────────────────────────────────────────────────────────────────────────────
// SÜZGEÇ İLE FORM ALANI AYNI ŞEY DEĞİLDİR — tek seçenekte davranışları AYRIŞIR
//
//   FORM alanı (yazma): tek seçenek KENDİLİĞİNDEN SEÇİLİR ve gövdeye yazılır.
//                       Kayıt bir kanala/dile ait olmak zorundadır; boş
//                       göndermek mağazada varsayılana düşmek demektir ve
//                       hangi varsayılan olduğu uçtan uca aynı değildir.
//
//   SÜZGEÇ (okuma):     tek seçenek SEÇİLMEZ ve gönderilmez. Gerekçe ölçülmüş
//                       bir hatadır: `channel=default` gönderilen sipariş
//                       listesi HTTP 200 ile SIFIR kayıt döndürüyordu (bkz.
//                       store_api `_drop_channel`). Tek kanallı bir mağazada
//                       "kanala göre süz" hiçbir satır elemez — yani süzgeci
//                       göndermek en iyi ihtimalle etkisiz, en kötü ihtimalle
//                       listeyi sessizce boşaltan bir istektir. Etkisiz olanı
//                       göndermeyiz.
//
// Bileşen VERİ SÜZMEZ ve İSTEK ATMAZ; yalnız "bu alan çizilsin mi, değeri ne
// olsun" sorusunu cevaplar.

import { h } from './kit.js';
import { alertBox } from './layout.js';

/**
 * Seçenek listesini tek biçime indirger.
 *
 * Kabul edilen girdiler — mağaza uçları üçünü de kullanıyor:
 *   ['default']                          → değer = etiket
 *   [{value, label}]                     → olduğu gibi
 *   [{id, name}] · [{code, name}]        → kimlik/kod + ad
 */
export function normalizeOptions(options) {
  const out = [];
  for (const item of options || []) {
    if (item === null || item === undefined) continue;
    if (typeof item === 'string' || typeof item === 'number') {
      const value = String(item);
      if (value === '') continue;
      out.push({ value, label: value });
      continue;
    }
    const value = item.value ?? item.id ?? item.code ?? '';
    const label = item.label ?? item.name ?? item.title ?? String(value);
    if (value === '' || value === null || value === undefined) continue;
    out.push({ value: String(value), label: String(label) });
  }
  return out;
}

/**
 * Alanın üç hâlinden hangisinde olduğunu söyler.
 *
 * @returns {{mode:'many'|'single'|'none', options:Array, value:string}}
 *          `value` yalnız 'single' hâlinde doludur.
 */
export function resolveChoice(options) {
  const list = normalizeOptions(options);
  if (list.length > 1) return { mode: 'many', options: list, value: '' };
  if (list.length === 1) return { mode: 'single', options: list, value: list[0].value };
  return { mode: 'none', options: [], value: '' };
}

/**
 * SÜZGEÇ alanı üretir — tek/ sıfır seçenekte `null` döner.
 *
 * `filterBar` yanlış (null) alanları atlar; dönen değeri doğrudan `fields`
 * dizisine koymak yeterlidir:
 *
 *     fields: [
 *       {kind:'search', key:'q'},
 *       choiceFilter({key:'channel', label:'Kanal', allLabel:'Tümü — kanal',
 *                     options: reference.channels}),
 *     ].filter(Boolean)
 *
 * @param {object} spec
 * @param {string} spec.key
 * @param {string} spec.label
 * @param {Array}  spec.options — HAM seçenekler ("Tümü" sentinel'i EKLENMEZ
 *                 (burada eklenir); çağıran yalnız gerçek seçenekleri verir).
 * @param {string} [spec.allLabel] — "hepsi" satırının metni.
 * @param {string} [spec.value] — önceden seçili değer.
 */
export function choiceFilter({ key, label, options, allLabel, value = '' }) {
  const choice = resolveChoice(options);
  if (choice.mode !== 'many') return null;
  return {
    kind: 'select',
    key,
    label,
    value,
    options: [{ value: '', label: allLabel || `Tümü — ${label}` }, ...choice.options],
  };
}

/**
 * SEÇENEKLER VERİDEN SONRA GELDİĞİNDE kullanılan yol.
 *
 * Süzgeç şeridi panel açılır açılmaz çizilmek zorunda; kanal/dil listesi ise
 * ilk `reference` isteğinden sonra geliyor. Bu yüzden kutu önce `hidden: true`
 * ile çizilir, liste gelince burası karar verir:
 *
 *     filterBar({fields: [
 *       {kind:'select', key:'channel', label:'Kanal', hidden: true, options: []},
 *     ]});
 *     …
 *     applyChoiceFilter(nodes.filters, 'channel', payload.channels,
 *                       {allLabel: 'Tümü — kanal'});
 *
 * @returns {{mode:string, options:Array}} kararın kendisi (test/kayıt için)
 */
export function applyChoiceFilter(bar, key, options, { allLabel = '' } = {}) {
  const choice = resolveChoice(options);
  if (choice.mode === 'many') {
    bar?.options?.(key, [{ value: '', label: allLabel || 'Tümü' }, ...choice.options]);
    bar?.visible?.(key, true);
  } else {
    // Tek ya da sıfır seçenek: kutu çizilmez VE değeri gönderilmez.
    // Gerekçe dosya başlığında (ölçülmüş `channel=default` → 0 kayıt hatası).
    bar?.visible?.(key, false);
  }
  return choice;
}

/**
 * FORM alanı üretir — tek/sıfır seçenekte `null` döner.
 *
 * `formGrid` yanlış (null) alanları atlar. Tek seçenekte alan çizilmez ama
 * DEĞER KAYBOLMAZ: `choiceValues()` onu taslağa koyar.
 */
export function choiceField({ key, label, options, hint, required = false, wide = false }) {
  const choice = resolveChoice(options);
  if (choice.mode !== 'many') return null;
  return {
    key, label, type: 'select', hint, required, wide,
    options: choice.options,
  };
}

/**
 * Tek seçenekli alanların KENDİLİĞİNDEN SEÇİLEN değerleri.
 *
 * Form taslağına konur; kutu çizilmese de gövde o değerle gider.
 *
 *     const singles = {channel: reference.channels, locale: reference.locales};
 *     const value = {...record, ...choiceValues(singles)};
 *
 * @param {Record<string, Array>} specs — alan anahtarı → ham seçenek listesi
 * @returns {Record<string, string>} yalnız TEK seçenekli alanlar
 */
export function choiceValues(specs) {
  const out = {};
  for (const [key, options] of Object.entries(specs || {})) {
    const choice = resolveChoice(options);
    if (choice.mode === 'single') out[key] = choice.value;
  }
  return out;
}

/**
 * SIFIR seçenekli alanların durum şeridi — yoksa `null`.
 *
 * "Seçenek yok" hâli SESSİZ GEÇİLMEZ: kutuyu çizmemek ile mağazada hiç kayıt
 * olmadığını söylememek aynı şey değildir. Kullanıcı kaydın neden
 * yazılamadığını ekranda okumalıdır.
 *
 * @param {Record<string, {label:string, options:Array, hint?:string}>} specs
 */
export function choiceNotice(specs) {
  const missing = [];
  for (const [, spec] of Object.entries(specs || {})) {
    if (resolveChoice(spec?.options).mode !== 'none') continue;
    missing.push(spec.hint
      ? `${spec.label}: ${spec.hint}`
      : `${spec.label}: mağazadan hiç seçenek gelmedi.`);
  }
  if (!missing.length) return null;
  return alertBox(
    `Şu alanlar doldurulamıyor — ${missing.join(' · ')} Kayıt bu alan olmadan `
    + 'gönderilir; mağaza kendi varsayılanını uygular.',
    'warn',
  );
}

/**
 * Tek seçenekli alanların ne olduğunu söyleyen sessiz satır.
 *
 * Kutu çizilmediği için değer de görünmez olurdu; bu satır "kanal: default"
 * bilgisini ekranda TUTAR ama kullanıcıdan seçim istemez. Hiçbir alan tek
 * seçenekli değilse `null` döner.
 *
 * @param {Record<string, {label:string, options:Array}>} specs
 */
export function choiceSummary(specs) {
  const parts = [];
  for (const [, spec] of Object.entries(specs || {})) {
    const choice = resolveChoice(spec?.options);
    if (choice.mode !== 'single') continue;
    parts.push(`${spec.label}: ${choice.options[0].label}`);
  }
  if (!parts.length) return null;
  const node = h('div', 'kit-choice-summary', parts.join(' · '));
  node.title = 'Mağazada tek seçenek var; alan sorulmadan bu değerle gönderilir.';
  return node;
}
