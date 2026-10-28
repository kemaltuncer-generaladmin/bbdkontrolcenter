// BBD Akademi — "Kayıt ve ödeme planı" sihirbazının GİRİŞLERİ (F-SZ1, 80-km-sihirbaz). Hafif: ekran (`wizard-screen.js`)
// ilk açılışta yüklenir.
//
//   openContractWizard(app, {studentId, planId?, source?, kmRef?, name?})   sihirbazı açar (doğrudan çağrı)
//   installContractWizard(app)    panel açılışında (index.js) — iki olayı dinler, kapatan işlevi döndürür:
//       `bbd-akademi:open-contract-wizard` {studentId, planId?, source?}  öğrencinin Sözleşmeler sekmesi (80-km-sozlesme,
//           `shared-contract.js` → requestWizard) ve "Sözleşmesi olmayanlar" listesi (80-km-belge) yayınlar;
//       `bbd-students:created` {studentId, externalId, displayName}      KM "Yeni öğrenci" kaydı (80-km-kimlik, bbd_students)
//           → "Kayıt ve ödeme planı" adımı (Basılı imzalı KAPALI; öğrenci Akademi'ye aynalanana dek bekleme göstergesi).
//   contractEntry(app, {studentId, plans?})   finans hesabı / öğrenci kartı: "Kayıt ve ödeme planı" + planı olup
//       sözleşmesi olmayan her ETKİN plan için "Sözleşme oluştur" (planId).
//
// Yetki: sözleşme oluşturma `admin_createContract` (contracts.setup + finance.manage, yalnız KM). Kurumda `contracts`
// kapalıysa düğme çizilmez ve olaylar yok sayılır; izin yoksa düğme gerekçeli kapalıdır.

import { blockedButton, button, h } from '../../../ui-kit/kit.js';
import { eventTarget, plansWithoutContract } from './wizard-logic.js';

const OPEN_EVENT = 'bbd-akademi:open-contract-wizard';
const CREATED_EVENT = 'bbd-students:created';
const CREATE_OP = 'admin_createContract';

/** Bu kullanıcı bu kurumda sözleşme oluşturabilir mi (özellik açık + izin)? */
export function canCreateContract(app) {
  if (app?.kmOnly?.()) return false;
  if (app?.enabled && app.enabled('contracts') === false) return false;
  if (app?.api?.featureOff?.(CREATE_OP)) return false;
  return Boolean(app?.api?.can?.(CREATE_OP));
}

function hiddenByFeature(app) {
  return Boolean(app?.kmOnly?.() || (app?.enabled && app.enabled('contracts') === false)
    || app?.api?.featureOff?.(CREATE_OP));
}

/** Sihirbazı açar; ekran dosyası ilk kullanımda yüklenir. Dönüş: oluşturulan sözleşme ya da null. */
export async function openContractWizard(app, { studentId = null, planId = null, source = 'existing', kmRef = null,
  name = '' } = {}) {
  try {
    const module = await import('./wizard-screen.js');
    return await module.openWizardScreen(app, { studentId, planId, source, kmRef, name });
  } catch (failure) {
    console.error('[bbd_akademi] sözleşme sihirbazı açılamadı', failure);
    app.toast?.('“Kayıt ve ödeme planı” açılamadı.', 'bad');
    return null;
  }
}

/** Panel açılışında çağrılır (index.js); dönen işlev dinleyicileri bırakır. */
export function installContractWizard(app) {
  const onOpen = (event) => {
    const detail = event?.detail || {};
    const target = eventTarget(detail);
    if (!target.akademiId && !target.refs.length) return;
    if (!canCreateContract(app)) {
      app.toast?.(hiddenByFeature(app) ? 'Sözleşmeler kurumda kapalı (Ayarlar → Özellikler).'
        : 'Sözleşme oluşturma yetkiniz yok (yöneticinizden izin isteyin).', 'bad');
      return;
    }
    const source = ['new', 'existing', 'missing', 'plan'].includes(detail.source) ? detail.source
      : (target.planId ? 'plan' : 'existing');
    openContractWizard(app, { studentId: target.akademiId, kmRef: target.refs[0] || null, planId: target.planId,
      source, name: target.name });
  };
  const onCreated = (event) => {
    const target = eventTarget(event?.detail || {});
    if (!target.akademiId && !target.refs.length) return;
    if (!canCreateContract(app)) return;     // izni olmayan kişiye ek adım yok (öğrenci "Sözleşmesi olmayanlar"da kalır)
    openContractWizard(app, { studentId: target.akademiId, kmRef: target.refs[0] || null, source: 'new',
      name: target.name });
  };
  window.addEventListener(OPEN_EVENT, onOpen);
  window.addEventListener(CREATED_EVENT, onCreated);
  return () => {
    window.removeEventListener(OPEN_EVENT, onOpen);
    window.removeEventListener(CREATED_EVENT, onCreated);
  };
}

/**
 * Giriş düğmeleri (finans hesabı araç çubuğu, öğrenci kartı → Kurum cari). `plans` verilirse (finans hesabı yükü)
 * sözleşmesiz etkin planlar için "Sözleşme oluştur" eklenir; öğrencinin sözleşmeleri tek okumayla (A3) gelir.
 * @returns {HTMLElement}
 */
export function contractEntry(app, { studentId, plans = null, onDone = null } = {}) {
  const node = h('span', 'ak-inline ak-wz-entry');
  if (hiddenByFeature(app)) return node;
  const label = 'Kayıt ve ödeme planı';
  if (!app.api.can(CREATE_OP)) {
    node.append(blockedButton(label, 'Sözleşme oluşturma yetkiniz yok (yöneticinizden izin isteyin).'));
    return node;
  }
  const open = async (planId = null) => {
    const result = await openContractWizard(app, { studentId, planId, source: planId ? 'plan' : 'existing' });
    if (result) onDone?.(result);
  };
  node.append(button(label, { title: 'Öğrenci ve veli, hizmetler, ödeme planı ve sözleşme — tek ekranda, tek onay.',
    onClick: () => open() }));
  const live = (plans || []).filter((plan) => plan.status === 'active');
  if (live.length && app.api.can('admin_listStudentContracts')) {
    app.api.get(`/v1/admin/students/${studentId}/contracts`).then((contracts) => {
      for (const plan of plansWithoutContract(live, contracts)) {
        node.append(button(`Sözleşme oluştur · ${plan.title}`, { variant: 'ghost',
          title: 'Bu taksit planının sözleşmesi yok: planı bağlayarak sözleşme çıkarır (yeni plan kurulmaz).',
          onClick: () => open(plan.id) }));
      }
    }).catch(() => { /* sözleşme listesi okunamazsa yalnız ana düğme kalır */ });
  }
  return node;
}
