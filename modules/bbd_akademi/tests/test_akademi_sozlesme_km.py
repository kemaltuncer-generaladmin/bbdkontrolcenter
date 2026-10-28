"""Sözleşmeler (F-SZ, iş 80 — 80-km-sozlesme): ortak saf mantık + öğrencinin Sözleşmeler sekmesi.

Kullanıcı (28.10.2026): "var olan mevcut öğrencinin istediğimiz an sözleşmesini çıkarabilmeliyiz … sözleşmeleri istersek
mobilden taratıp ekleyebilmeliyiz … şak o sözleşmeler toplu pdf gelecek yazdır diyeceğiz" · "basit bir şekilde ufak manuel
madde değişikliklerini desteklesin" · "velinin imzasını alacağız. sadece veli neyi imzaladığını görecek".

- `shared-contract-logic.js` (saf): satır toplamı (sunucu kuralının kopyası, yarım yukarı), eşit bölme (kuruş farkı son
  taksitte, ay sonu taşmaz), toplam farkı, hata zarfı → alan alan Türkçe, imza gövdesi, madde düzenleyici modeli +
  yalnız değişen türü gönderen A8 gövdesi, "Velinin gördüğü" (yalnız son imzalı sürüm), anlık olay eşlemesi;
- sekme (DOM taklidi, Akademi taklidi): tek sözleşme doğrudan açılır, durum çipleri, kimlik değişti bandı, eylemler;
  "İmzalandı" → A10 gövdesi, olayla liste tazelenir; seçili türler tek PDF → A17 gövdesi (nüsha ayarlardan);
  sözleşmesi yok → "Kayıt ve ödeme planı" sihirbaz olayını yayar.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
export default async function ({ load }) {
  const m = await load('./panels/bbd_akademi/contracts/shared-contract-logic.js');
  const lines = [
    { name: 'GRUP DERSİ', category: 'education', unitPriceMinor: 18000000, quantity: '1', scholarshipPercent: '10',
      discountPercent: '5', extraDiscountMinor: 100 },
    { name: 'Kitap seti', category: 'book', unitPriceMinor: 33333, quantity: '1,5' },
  ];
  const total = m.linesSummary(lines).totalMinor;
  const down = 299900;
  const split = m.splitEqual(total - down, 3, '2026-01-31', { interval: 'monthly', method: 'promissory' });
  const payment = { mode: 'installment', downPayment: { amountMinor: down, dueOn: '2026-01-10', method: 'cash' },
    installments: split };
  const okCheck = m.totalsCheck({ lines, payment });
  const bad = { ...payment, installments: split.map((row, i) => (i === 0 ? { ...row, amountMinor: row.amountMinor + 1 } : row)) };
  const badCheck = m.totalsCheck({ lines, payment: bad });
  const state = { payerGuardianSlot: 1, lines, payment: bad, academicYear: '2026-2027', registeredOn: '2026-01-08',
    contractDate: '2026-01-08', existingSignedOn: true, existingNumber: '', existingSignedDate: '2026-12-01',
    pastPayments: [{ receivedOn: '2026-11-01', amountMinor: 1000, method: 'cash' }] };
  const clauses = [{ title: 'TARAFLAR', items: ['a) Kurum', 'b) Veli'] }, { title: 'ÜCRET', items: ['Ücret peşin'] }];
  let edited = m.clauseOps.moveItem(clauses, 0, 1, 0);
  edited = m.clauseOps.addItem(edited, 1);
  edited = m.clauseOps.setItem(edited, 1, 1, '  Gecikmede yasal faiz  ');
  edited = m.clauseOps.addClause(edited);
  const detail = { signature: { state: 'resign_required', signedRevisionNo: 1 },
    revisions: [
      { no: 1, createdAt: '2026-01-08T09:00:00Z', documents: [{ kind: 'registration', ready: true }],
        signature: { signedOn: '2026-01-08', source: 'paper_existing', kinds: ['registration', 'meb', 'payment_plan'] } },
      { no: 2, createdAt: '2026-10-28T09:00:00Z', documents: [{ kind: 'registration', ready: false }, { kind: 'promissory', ready: false }], signature: null },
    ],
    attachments: [{ id: 'a1', revisionNo: 1 }, { id: 'a2', revisionNo: 2 }] };
  return {
    line1: m.lineTotals(lines[0]),
    line2: m.lineTotals(lines[1]),
    pct: m.lineTotals({ unitPriceMinor: 333, quantity: 1, discountPercent: '12,5' }),
    over: m.lineTotals({ unitPriceMinor: 1000, quantity: 1, discountPercent: 50, extraDiscountMinor: 900 }),
    badQty: m.lineTotals({ unitPriceMinor: 1000, quantity: '1,234' }).errors,
    split: split.map((row) => [row.no, row.dueOn, row.amountMinor, row.method]),
    weekly: m.splitEqual(100, 3, '2026-12-28', { interval: 'weekly' }).map((row) => [row.dueOn, row.amountMinor]),
    okCheck, badCheck,
    errors: m.validateDraft(state, { today: '2026-10-28' }).map((item) => [item.section, item.field]),
    body: m.draftBody({ ...state, payment, existingNumber: ' 72128 ', existingSignedDate: '2025-09-01', specialClauses: 'Servis dahil\n\n Yemek hariç ' }),
    year: [m.suggestAcademicYear('2026-10-28'), m.suggestAcademicYear('2026-06-15'), m.suggestAcademicYear('2026-09-01')],
    problems: ['contract_totals_mismatch', 'contract_number_taken'].map((code) => m.contractProblem({ code }).fields[0].field),
    missing: m.contractProblem({ code: 'contract_missing_fields', details: [{ scope: 'guardian', slot: 1, field: 'nationalId', label: 'TC kimlik no' }] }).summary,
    valErr: m.contractProblem({ code: 'validation_error', details: [{ field: 'body.payment.installments.0.dueOn', message: 'Vade geçersiz.' }] }).fields,
    hint: [m.nextNumberHint({ numberPrefix: 'BBD1-', nextNumber: 1000 }), m.nextNumberHint({})],
    settings: m.contractSettingsOf({ contracts: { registrationKinds: ['registration', 'x'], defaultCopies: { meb: 3 } } }),
    payer: [m.defaultPayerSlot({ party: { guardians: [{ slot: 1 }, { slot: 2, canPay: true, primary: true }] } }),
      m.defaultPayerSlot({ defaultPayerSlot: 1, party: { guardians: [{ slot: 1 }, { slot: 2, canPay: true }] } })],
    missingFor: m.missingFor({ party: { student: { fullName: 'Elif Yılmaz', nationalIdMasked: '*******0146' },
      guardians: [{ slot: 1, fullName: 'Ayşe Yılmaz', nationalIdMasked: null, phoneMasked: '5*****1234', homeAddress: null }] },
      missingFields: [] }, 1).map((item) => item.field),
    edited: m.normalizeClauses(edited),
    revise: m.reviseBody({ reason: 'Veli isteği', original: { registration: clauses, meb: clauses },
      edited: { registration: edited, meb: m.cloneClauses(clauses) }, specialOriginal: ['Servis dahil'], specialEdited: 'Servis dahil' }),
    reviseNone: m.reviseBody({ reason: 'Veli isteği', original: { registration: clauses }, edited: { registration: clauses } }),
    reviseShort: m.reviseBody({ reason: 'a', original: {}, edited: {} }),
    sign: m.signBody({ revisionNo: 2, signedOn: '2026-10-28', kinds: ['registration', 'payment_plan'], uploads: ['f1', 'f2'] }, { today: '2026-10-28' }),
    signFuture: m.signBody({ revisionNo: 2, signedOn: '2026-10-29', kinds: ['registration'] }, { today: '2026-10-28' }).error,
    signKinds: m.defaultSignKinds({ documents: [{ kind: 'registration', ready: true }, { kind: 'payment_plan', ready: true }] }),
    scan: [m.scanFileProblem({ name: 'a.PDF', size: 1000 }), m.scanFileProblem({ name: 'a.docx', size: 1 }),
      m.scanFileProblem({ name: 'b.jpg', size: 11 * 1024 * 1024 })],
    guardian: m.guardianView(detail),
    guardianNone: m.guardianView({ revisions: [{ no: 1, signature: null }] }).kinds,
    render: [m.needsRender(detail.revisions[1]), m.needsRender(detail.revisions[0])],
    party: [m.partyChanged(detail, '2026-10-28T10:00:00Z'), m.partyChanged(detail, '2026-10-27T10:00:00Z')],
    events: [
      m.eventConcerns({ type: 'contract.changed', studentId: 's1', contractId: 'c9' }, { studentId: 's1' }),
      m.eventConcerns({ type: 'contract.changed', studentId: 's2', contractId: 'c1' }, { studentId: 's1', contractId: 'c1' }),
      m.eventConcerns({ type: 'contract.changed', studentId: 's2' }, { studentId: 's1' }),
      m.eventConcerns({ type: 'contract.party_changed', studentId: 's1' }, { studentId: 's1' }),
      m.eventConcerns({ type: 'other' }, { studentId: 's1' }),
    ],
  };
}
"""


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


@pytest.fixture(scope="module")
def logic() -> dict[str, Any]:
    return _run(LOGIC)


def test_satir_toplami_sunucu_kurali(logic: dict[str, Any]) -> None:
    # 180.000 TL: burs %10 + indirim %5 + 1,00 TL → 27.001,00 TL indirim.
    assert logic["line1"] == {"amountMinor": 18000000, "discountMinor": 2700100, "totalMinor": 15299900, "errors": []}
    # 333,33 × 1,5 = 499,995 → yarım yukarı 500,00.
    assert logic["line2"]["amountMinor"] == 50000 and logic["line2"]["totalMinor"] == 50000
    # 3,33 × %12,5 = 0,41625 → 0,42.
    assert logic["pct"]["discountMinor"] == 42
    # İndirim tutarı satırı aşamaz (kırpılır + hata).
    assert logic["over"]["discountMinor"] == 1000 and logic["over"]["totalMinor"] == 0
    assert "discountExceeds" in logic["over"]["errors"]
    assert logic["badQty"] == ["quantity"]


def test_esit_bolme_kurus_farki_son_taksitte(logic: dict[str, Any]) -> None:
    # (15.299.900 + 50.000 − 299.900) / 3 = 15.050.000 / 3 → 5.016.666 + 5.016.666 + 5.016.668
    assert logic["split"] == [
        [1, "2026-01-31", 5016666, "promissory"],
        [2, "2026-02-28", 5016666, "promissory"],   # ay sonu taşmaz
        [3, "2026-03-31", 5016668, "promissory"],   # gün ilk vadeden (birikmez)
    ]
    assert logic["weekly"] == [["2026-12-28", 33], ["2027-01-04", 33], ["2027-01-11", 34]]


def test_toplam_farki(logic: dict[str, Any]) -> None:
    ok = logic["okCheck"]
    assert ok["contractMinor"] == 15349900 and ok["paidMinor"] == 15349900 and ok["diffMinor"] == 0 and ok["ok"] is True
    bad = logic["badCheck"]
    assert bad["diffMinor"] == -1 and bad["ok"] is False


def test_taslak_dogrulama_ve_govde(logic: dict[str, Any]) -> None:
    errors = {tuple(item) for item in logic["errors"]}
    assert ("payment", "totals") in errors
    assert ("past", "pastPayments.0.receivedOn") in errors          # gelecek tarihli tahsilat
    assert ("contract", "existingSigned.number") in errors           # basılı imzalıda eski no zorunlu
    assert ("contract", "existingSigned.signedOn") in errors         # imza tarihi gelecekte olamaz
    body = logic["body"]
    assert body["existingSigned"] == {"number": "72128", "signedOn": "2025-09-01"}
    assert body["specialClauses"] == ["Servis dahil", "Yemek hariç"]
    assert body["lines"][1]["quantity"] == 1.5 and body["lines"][0]["scholarshipPercent"] == 10
    assert body["payment"]["downPayment"] == {"amountMinor": 299900, "dueOn": "2026-01-10", "method": "cash"}
    assert [row["no"] for row in body["payment"]["installments"]] == [1, 2, 3]
    assert body["pastPayments"] == [{"receivedOn": "2026-11-01", "amountMinor": 1000, "method": "cash"}]
    assert logic["year"] == ["2026-2027", "2025-2026", "2026-2027"]
    assert logic["hint"] == ["Sonraki: BBD1-1000", ""]
    assert logic["settings"]["registrationKinds"] == ["registration"]
    assert logic["settings"]["defaultCopies"]["meb"] == 3 and logic["settings"]["defaultCopies"]["registration"] == 2
    assert logic["payer"] == [2, 1]
    assert logic["missingFor"] == ["nationalId", "homeAddress"]


def test_hata_zarfi_alan_alan(logic: dict[str, Any]) -> None:
    assert logic["problems"] == ["totals", "existingSigned.number"]
    assert "Veli 1: TC kimlik no" in logic["missing"] and "Öğrenciler panelinde" in logic["missing"]
    assert logic["valErr"] == [{"section": "payment", "field": "payment.installments.0.dueOn", "message": "Vade geçersiz."}]


def test_madde_duzenleyici_ve_a8_govdesi(logic: dict[str, Any]) -> None:
    assert logic["edited"] == [
        {"title": "TARAFLAR", "items": ["b) Veli", "a) Kurum"]},
        {"title": "ÜCRET", "items": ["Ücret peşin", "Gecikmede yasal faiz"]},
    ]  # boş eklenen madde atılır, öğeler kırpılır
    revise = logic["revise"]["body"]
    assert revise["reason"] == "Veli isteği"
    assert list(revise["clauses"]) == ["registration"]                # yalnız DEĞİŞEN tür gider
    assert "specialClauses" not in revise                             # özel hususlar aynı
    assert logic["reviseNone"] == {"error": "Değişiklik yok."}
    assert logic["reviseShort"]["error"].startswith("Gerekçe")


def test_imza_ve_veli_gorunurlugu(logic: dict[str, Any]) -> None:
    assert logic["sign"] == {"body": {"revisionNo": 2, "signedOn": "2026-10-28", "kinds": ["registration"],
                                      "scanFileIds": ["f1", "f2"]}}
    assert logic["signFuture"] == "İmza tarihi gelecekte olamaz."
    assert logic["signKinds"] == ["registration"]
    assert logic["scan"][0] == "" and "yalnız PDF" in logic["scan"][1] and "10 MB" in logic["scan"][2]
    guardian = logic["guardian"]
    # Veli yalnız SON İMZALI sürümü görür; ödeme planı imzalanan tür değildir; imzasız sürümün eki görünmez.
    assert guardian["revisionNo"] == 1 and guardian["kinds"] == ["registration", "meb"]
    assert [item["id"] for item in guardian["attachments"]] == ["a1"]
    assert logic["guardianNone"] == []
    assert logic["render"] == [True, False]
    assert logic["party"] == [True, False]
    assert logic["events"] == [True, True, False, True, False]


UI = r"""
const sent = [];
const state = { items: [], signed: false, settingsReads: 0 };
const summary = { id: 'c1', number: 'BBD1-1000', student: { id: 'stu-1', displayName: 'Elif Yılmaz' },
  payerGuardian: { slot: 1, name: 'Ayşe Yılmaz' }, academicYear: '2026-2027', contractDate: '2026-10-28',
  registeredOn: '2026-10-28', status: 'active', signature: { state: 'awaiting', signedRevisionNo: null, signedOn: null, source: null },
  latestRevisionNo: 1, totalMinor: 18000000, planId: 'p1', createdAt: '2026-10-28T09:00:00Z' };
const detail = () => ({ ...summary,
  signature: state.signed ? { state: 'signed', signedRevisionNo: 1, signedOn: '2026-10-28', source: 'paper' } : summary.signature,
  lines: [{ category: 'education', name: 'GRUP DERSİ', unitPriceMinor: 18000000, quantity: 1, amountMinor: 18000000,
    discountMinor: 0, totalMinor: 18000000 }],
  discountReasons: [], specialClauses: [],
  payment: { mode: 'installment', downPayment: { amountMinor: 2000000, dueOn: '2026-10-28', method: 'cash' },
    installments: [{ no: 1, dueOn: '2026-11-28', amountMinor: 8000000, method: 'promissory', status: 'open', paidMinor: 0 },
      { no: 2, dueOn: '2026-12-28', amountMinor: 8000000, method: 'promissory', status: 'open', paidMinor: 0 }] },
  totals: { contractMinor: 18000000, installmentsMinor: 16000000, downPaymentMinor: 2000000, paidMinor: 0, remainingMinor: 18000000, overdueMinor: 0 },
  clauses: { registration: [{ title: 'TARAFLAR', items: ['Kurum ve veli'] }], meb: [], promissory: [], legal_notice: [] },
  revisions: [{ no: 1, createdAt: '2026-10-28T09:00:00Z', createdByLabel: 'Kemal T.', reason: 'İlk oluşturma',
    documents: [{ kind: 'registration', ready: true }, { kind: 'meb', ready: true }, { kind: 'promissory', ready: true },
      { kind: 'payment_plan', ready: true }],
    signature: state.signed ? { signedOn: '2026-10-28', source: 'paper', kinds: ['registration', 'meb', 'promissory'], byLabel: 'Kemal T.' } : null }],
  attachments: [], party: {}, missingFields: [] });

globalThis.__apiHook = (method, path, options) => {
  if (path === '/api/bbd_akademi/km/documents/akademi') {
    sent.push({ method: 'KM', path, body: options.body });
    return { ok: true, path: '/rapor/Akademi/x.pdf', name: 'x.pdf', bytes: 10, pages: [], pageCount: 1, shown: 0, previewError: '' };
  }
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = JSON.parse(JSON.stringify(options.body || {}));
  sent.push(payload);
  const key = `${payload.method} ${payload.path}`;
  const reply = (data) => ({ ok: true, status: 200, data, headers: {} });
  if (key === 'GET /v1/admin/students/stu-1/contracts') return reply({ items: state.items });
  if (key === 'GET /v1/admin/contracts/c1') return reply(detail());
  if (key === 'GET /v1/admin/students/stu-1/contract-party') return reply({ party: {}, missingFields: [], identityUpdatedAt: '2026-10-28T12:00:00Z' });
  if (key === 'POST /v1/admin/contracts/c1/signatures') { state.signed = true; return reply(detail()); }
  if (key === 'GET /v1/admin/settings') { state.settingsReads += 1; return reply({ contracts: { defaultCopies: { registration: 2, meb: 2, promissory: 1, payment_plan: 1 } } }); }
  if (key === 'POST /v1/admin/contracts/c1/revisions') return reply(detail());
  if (key === 'POST /v1/admin/contracts/documents') return reply({ status: 'ready', file: { url: 'https://dosya.example/c.pdf', fileName: 'c.pdf', expiresAt: 'x' }, jobId: null, includedCount: 1, skipped: [] });
  return { ok: false, status: 404, error: { code: 'not_found', message: `taklit yok: ${key}` } };
};

export default async function ({ document, audit, sleep, api, load }) {
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const toasts = [];
  const client = createClient({ api: (path, options) => api(path, options) });
  const app = { root, api: client, can: () => true, has: () => true, enabled: () => true, toast: (text, tone) => toasts.push([tone, text]),
    capability: () => null, go: () => {}, coreApi: () => ({}) };
  const { contractsPart } = await load('./panels/bbd_akademi/contracts/contract-tab.js');
  const text = (node) => node.textContent.replace(/\s+/g, ' ').trim();
  const buttons = (node) => node.querySelectorAll('button');
  const click = (node, label) => buttons(node).find((item) => text(item) === label).click();

  // 1) Sözleşmesi yok → sihirbaz olayı.
  const wizardEvents = [];
  window.addEventListener('bbd-akademi:open-contract-wizard', (event) => wizardEvents.push(event.detail));
  const emptyPart = contractsPart({ app, id: 'stu-1', student: { student: { externalRef: 'STU-1', displayName: 'Elif Yılmaz' } } });
  root.append(emptyPart.node);
  emptyPart.shown();
  await sleep(50);
  const emptyText = text(emptyPart.node);
  buttons(emptyPart.node).filter((item) => text(item) === 'Kayıt ve ödeme planı').pop().click();
  emptyPart.destroy();
  emptyPart.node.remove();

  // 2) Tek sözleşme → ayrıntı doğrudan açık.
  state.items = [summary];
  const part = contractsPart({ app, id: 'stu-1', student: { student: { externalRef: 'STU-1', displayName: 'Elif Yılmaz' } } });
  root.append(part.node);
  part.shown();
  await sleep(120);
  const opened = text(part.node);
  const stripHidden = part.node.querySelector('.ak-ct-strip').hidden;
  const actionLabels = buttons(part.node).map(text);
  const a11y = audit(part.node);

  // 3) İmzalandı → A10 gövdesi; olayla liste + ayrıntı tazelenir.
  const listReadsBefore = sent.filter((p) => p.path === '/v1/admin/students/stu-1/contracts').length;
  click(part.node, 'İmzalandı');
  await sleep(20);
  const dialog = root.querySelector('.kit-dialog');
  const dialogText = text(dialog);
  click(dialog, 'İmzalandı olarak kaydet');
  await sleep(150);
  const signBody = sent.find((p) => p.path === '/v1/admin/contracts/c1/signatures');
  const listReadsAfter = sent.filter((p) => p.path === '/v1/admin/students/stu-1/contracts').length;
  const afterSign = text(part.node);

  // 4) Seçili türler tek PDF → A17 gövdesi (nüsha ayarlardan) → KM önizleme zinciri.
  click(part.node, 'Seçilenler · tek PDF önizle');
  await sleep(150);
  const docsBody = sent.find((p) => p.path === '/v1/admin/contracts/documents');
  const kmImport = sent.find((p) => p.method === 'KM');
  // 5) Maddeleri düzenle → öğe ekle, gerekçe → A8 gövdesi (yalnız değişen tür).
  click(part.node, 'Maddeleri düzenle');
  await sleep(20);
  const editor = root.querySelector('.ak-ct-editor');
  const resignWarning = text(editor).includes('YENİDEN İMZA');
  click(editor, '+ Öğe');
  const item = editor.querySelector('[data-item="0.1"]');
  item.value = 'Ek madde';
  item.dispatchEvent(new Event('input', { bubbles: true }));
  const reasonInput = editor.querySelectorAll('input').find((n) => n.getAttribute('aria-label') === 'Gerekçe');
  reasonInput.value = 'Velinin isteği';
  click(editor, 'Yeni sürümü kaydet');
  await sleep(120);
  const revise = sent.find((p) => p.path === '/v1/admin/contracts/c1/revisions');

  // 6) Velinin gördüğü (imzalı sürüm 1).
  click(part.node, 'Velinin gördüğü');
  await sleep(20);
  const guardianText = text(root.querySelector('.ak-ct-guardian'));
  click(root.querySelector('.ak-ct-guardian'), 'Kapat');
  part.destroy();
  return { resignWarning, revise: revise && revise.body, guardianText, emptyText, wizardEvents, opened, stripHidden, actionLabels, a11y, dialogText,
    signBody: signBody && { body: signBody.body, key: Boolean(signBody.idempotencyKey) },
    listReadsBefore, listReadsAfter, afterSign, docsBody: docsBody && docsBody.body,
    kmImport: kmImport && kmImport.body, toasts, today: new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Istanbul' }) };
}
"""


def test_sekme_ac_imzala_tek_pdf() -> None:
    out = _run(UI)
    assert "Sözleşmesi yok" in out["emptyText"]
    assert out["wizardEvents"] == [{"studentId": "stu-1", "planId": None}]
    opened = out["opened"]
    assert "Sözleşme BBD1-1000" in opened and "İmza bekliyor" in opened and "Geçerli" in opened
    assert "Kimlik bilgileri değişti" in opened                       # kimlik aynası sürümden yeni
    assert "180.000,00" in opened and "SENET" in opened and "Peşinat" in opened
    assert out["stripHidden"] is True                                  # tek sözleşmede şerit yok
    for label in ("İmzalandı", "Tarama ekle", "Maddeleri düzenle", "Güncel bilgilerle yenile", "Velinin gördüğü",
                  "İptal et", "Seçilenler · tek PDF önizle", "Yazdır"):
        assert label in out["actionLabels"], label
    assert all(item.get("impact") != "critical" for item in out["a11y"]), out["a11y"]
    assert "Öğrenci Kayıt Sözleşmesi" in out["dialogText"] and "Senetler" in out["dialogText"]
    sign = out["signBody"]
    assert sign["key"] is True
    assert sign["body"] == {"revisionNo": 1, "signedOn": out["today"], "kinds": ["registration", "meb", "promissory"]}
    assert out["listReadsAfter"] > out["listReadsBefore"]             # yerel olay → liste tazelendi
    assert "İmzalı" in out["afterSign"] and "İmzayı geri al" in out["afterSign"]
    docs = out["docsBody"]
    assert docs["contractIds"] == ["c1"]
    assert docs["kinds"] == ["registration", "meb", "promissory", "payment_plan"]   # yasal uyarı varsayılan seçili değil
    assert docs["options"] == {"copies": {"registration": 2, "meb": 2, "promissory": 1, "payment_plan": 1},
                               "promissoryScope": "all", "revision": "latest"}
    assert out["resignWarning"] is True                               # imzalı sözleşmede yeniden imza uyarısı
    assert out["revise"] == {"reason": "Velinin isteği",
                             "clauses": {"registration": [{"title": "TARAFLAR", "items": ["Kurum ve veli", "Ek madde"]}]}}
    assert "Sürüm 1" in out["guardianText"] and "Öğrenci Kayıt Sözleşmesi" in out["guardianText"]
    assert "Ödeme Planı" not in out["guardianText"]
    assert out["kmImport"] == {"kind": "contract_documents", "url": "https://dosya.example/c.pdf", "name": "c.pdf",
                               "preview": True}
