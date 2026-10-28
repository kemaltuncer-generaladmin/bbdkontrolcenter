// BBD Akademi — öğrenci kartı → "Cari, kantin ve kitap" → **Sözleşmeler** (F-SZ, iş 80; kullanıcı 1, 2, 5, 6, 12).
//
// A3 tek okuma (yeniden eskiye) → sözleşme şeridi; seçilen sözleşmenin ayrıntısı altta (`contract-detail.js`, A7).
// Tek sözleşme varsa (çoğu öğrenci) doğrudan açık gelir: "aç → yazdır / İmzalandı" iki tık.
// Sözleşmesi yoksa: "Sözleşmesi yok" + "Kayıt ve ödeme planı" (sihirbaz 80-km-sihirbaz'ın; olayla açılır).
//
// ANLIK: yerel `contract.changed` / `contract.party_changed` olayı (KM'deki her yazma, sihirbaz dahil) → liste ve açık
// ayrıntı hemen tazelenir; başka cihazdaki değişiklik 15 sn'lik liste yoklamasıyla (sekme gizliyken durur); KM'de
// kimlik düzenlenince (`bbd-students:updated`) "Kimlik bilgileri değişti" bandı yeniden hesaplanır.
// Derin bağlantı: `app.go('students', {studentId, tab: 'contracts', contracts: {contractId}})`.

import { h, loadStyles } from '../../../ui-kit/kit.js';
import { badge } from '../../../ui-kit/layout.js';
import { empty, errorBox, fmtDate, money, toolbar } from '../common.js';
import { contractDetail } from './contract-detail.js';
import { onContractEvent, paths, watchStudentContracts, wizardButton } from './shared-contract.js';
import { CONTRACT_STATUSES, eventConcerns, signatureChip } from './shared-contract-logic.js';

/**
 * Öğrenci kartının bölümü: `ctx` = {app, id (Akademi öğrenci kimliği), ready, student, dispose}.
 * @returns {{node, shown(payload), focus(payload), destroy()}}
 */
export function contractsPart(ctx) {
  loadStyles(import.meta.url, 'contract-ui.css');
  const { app, id: studentId } = ctx;
  const node = h('div', 'ak-stack ak-ct');
  if (!app.api.can('admin_listStudentContracts')) {
    // Asıl kapı vekildedir; bu yalnız dürüst arayüz (izin ya da sözleşme özelliği yok).
    node.append(empty('Sözleşmeleri görme yetkiniz yok', 'Yöneticinizden sözleşme iznini isteyin.'));
    return { node };
  }
  const strip = h('div', 'ak-ct-strip');
  strip.setAttribute('role', 'tablist');
  strip.setAttribute('aria-label', 'Sözleşmeler');
  const body = h('div', 'ak-ct-body');
  node.append(toolbar(h('span', 'ak-muted', 'Kayıt ve MEB sözleşmesi, senetler, ödeme planı, yasal uyarı · imza ve '
    + 'taranmış nüshalar. Veli yalnız imzaladığını görür; öğrenci hiçbirini görmez.'), h('span', 'kit-spacer'),
  wizardButton(app, studentId, { label: 'Kayıt ve ödeme planı' })), strip, body);

  const kmId = () => ctx.student?.student?.externalRef || null;
  const name = () => ctx.student?.student?.displayName || '';
  let items = [];
  let selected = null;
  let detail = null;
  let started = false;
  let token = 0;
  let wanted = null;

  async function load() {
    const mine = ++token;
    if (!items.length) body.replaceChildren(h('div', 'ak-muted', 'Sözleşmeler yükleniyor…'));
    try {
      const data = await app.api.get(paths.studentContracts(studentId));
      if (mine !== token) return;
      items = Array.isArray(data) ? data : (data?.items || []);
      watch.seen(items);
      paint();
    } catch (failure) {
      if (mine !== token) return;
      strip.replaceChildren();
      body.replaceChildren(errorBox(failure, () => load()));
    }
  }

  function pick(id) {
    if (selected === id && detail) return;
    selected = id;
    detail?.destroy();
    detail = contractDetail(app, id, { studentId, kmId, name, onChanged: () => load() });
    body.replaceChildren(detail.node);
    paintStrip();
  }

  function paintStrip() {
    strip.replaceChildren();
    strip.hidden = items.length < 2;
    for (const item of items) {
      const [signText, signTone] = signatureChip(item);
      const chip = h('button', `ak-ct-chip${item.id === selected ? ' on' : ''}`);
      chip.type = 'button';
      chip.setAttribute('role', 'tab');
      chip.setAttribute('aria-selected', item.id === selected ? 'true' : 'false');
      const status = CONTRACT_STATUSES[item.status] || [item.status, 'dim'];
      chip.append(h('b', '', item.number || '—'), h('span', '', `${item.academicYear || ''} · ${money(item.totalMinor)}`),
        badge(item.status === 'active' ? signText : status[0], item.status === 'active' ? signTone : status[1]));
      chip.title = `Sözleşme tarihi ${fmtDate(item.contractDate)}`;
      chip.addEventListener('click', () => pick(item.id));
      strip.append(chip);
    }
  }

  function paint() {
    if (!items.length) {
      selected = null;
      detail?.destroy();
      detail = null;
      strip.replaceChildren();
      strip.hidden = true;
      body.replaceChildren(empty('Sözleşmesi yok', 'Kayıt ve ödeme planı ile sözleşme oluşturun — mevcut öğrencinin basılı '
        + 'imzalı eski sözleşmesi de buradan aktarılır (numarası ve imza tarihiyle).',
      [wizardButton(app, studentId, { label: 'Kayıt ve ödeme planı', variant: 'primary' })]));
      return;
    }
    const keep = wanted && items.some((item) => item.id === wanted) ? wanted
      : selected && items.some((item) => item.id === selected) ? selected
        : (items.find((item) => item.status === 'active') || items[0]).id;
    wanted = null;
    if (keep !== selected || !detail) pick(keep);
    else { paintStrip(); detail.reload(); }
  }

  const watch = watchStudentContracts(app, studentId, (why) => {
    if (!started) return;
    if (why === 'party') detail?.refreshParty();
    else load();
  });
  const off = onContractEvent((event) => {
    if (!started) return;
    if (event.type === 'contract.party_changed' && event.kmId && event.kmId === kmId()) {
      setTimeout(() => detail?.refreshParty(), 1500);
      return;
    }
    if (!eventConcerns(event, { studentId, contractId: selected })) return;
    if (event.type === 'contract.party_changed') { detail?.refreshParty(); return; }
    if (event.contractId && event.change === 'created') wanted = event.contractId;
    load();
  });

  return {
    node,
    shown(payload) {
      if (payload?.contracts?.contractId) wanted = payload.contracts.contractId;
      if (!started) { started = true; load(); return; }
      if (wanted) load();
    },
    focus(payload) {
      if (payload?.contracts?.contractId) { wanted = payload.contracts.contractId; load(); }
    },
    destroy() {
      token += 1;
      started = false;
      watch.stop();
      off();
      detail?.destroy();
    },
  };
}
