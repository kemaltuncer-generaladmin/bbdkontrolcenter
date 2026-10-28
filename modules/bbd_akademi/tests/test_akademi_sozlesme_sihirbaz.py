"""Sözleşmeler (F-SZ1, iş 80-km-sihirbaz): "Kayıt ve ödeme planı" sihirbazı.

İki katman:

* SAF MANTIK (`ui/panel/contracts/wizard-logic.js` + ortak `shared-contract-logic.js`) Node'da yüklenir:
  satır toplamı (taslaktaki kural, yarım yukarı), eşit bölme (kuruş farkı SON taksitte), toplam farkı,
  `ContractDraft` kurucusu, "Basılı imzalı" varsayılanı (mevcut öğrencide AÇIK, yeni öğrencide KAPALI), akıllı
  varsayılanlar (ilk vade = gelecek ayın sözleşme günü, tür NAKİT, eğitim yılı), mevcut plandan satır, olay ayrıntısı.
* EKRAN (`wizard-screen.js`) kabuğun küçük DOM'unda (`tools/a11y/harness.py`) açılır; Akademi vekili taklit edilir ve
  A6'ya giden GÖVDE + Idempotency-Key denetlenir; yeni öğrenci olayı (`bbd-students:created`) aynalanmayı bekler.

`node` yoksa ATLANIR (çekirdek Python'dur, Node yalnız kabuk aracıdır).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run
from modules.bbd_akademi.tests.test_akademi_toplu_kitap import PRELUDE

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")

LOGIC = r"""
import * as w from './wizard-logic.js';
import * as s from './shared-contract-logic.js';
const out = {};
// Uydurma kişi (gerçek veri YOK): Elif Yılmaz / Ayşe Yılmaz.
const party = { party: { student: { fullName: 'Elif Yılmaz', nationalIdMasked: '*******0146', registeredOn: null },
  guardian: null,
  guardians: [
    { slot: 1, fullName: 'Ayşe Yılmaz', relation: 'mother', nationalIdMasked: '*******0278', phoneMasked: '+90 5** *** ** 67',
      homeAddress: { line: 'Örnek Mah. 1', district: 'Selçuklu', city: 'Konya' } },
    { slot: 2, fullName: 'Mehmet Yılmaz', relation: 'father', nationalIdMasked: null, phoneMasked: null, homeAddress: null }] },
  missingFields: [] };
const service = { id: 's1', name: 'GRUP DERSİ', category: 'education', unit: 'ay', unitPriceMinor: 1800000, active: true,
  sortOrder: 1 };

// satır toplamı — taslak kuralı (yarım yukarı; burs ve indirim ayrı yuvarlanır, TL indirim eklenir, tutarı aşamaz)
out.line = s.lineTotals({ unitPriceMinor: 1800000, quantity: '10', scholarshipPercent: '10', discountPercent: '0',
  extraDiscountMinor: 0 });
out.lineHalf = s.lineTotals({ unitPriceMinor: 333, quantity: '1.5', scholarshipPercent: '12.5', discountPercent: '0',
  extraDiscountMinor: 0 });
out.lineBoth = s.lineTotals({ unitPriceMinor: 100001, quantity: '1', scholarshipPercent: '0.5', discountPercent: '0.5',
  extraDiscountMinor: 99 });
out.lineOver = s.lineTotals({ unitPriceMinor: 1000, quantity: '1', scholarshipPercent: '50', discountPercent: '0',
  extraDiscountMinor: 900 });

// ilk durum: mevcut öğrenci
const st = w.initialState({ source: 'existing', today: '2026-01-31', party, services: [service] });
out.existing = { printed: st.existingSignedOn, year: st.academicYear, firstDue: st.payment.firstDue,
  method: st.payment.method, count: st.payment.count, payer: st.payerGuardianSlot, line: st.lines[0],
  registeredOn: st.registeredOn, downDue: st.payment.downPayment.dueOn, signedDate: st.existingSignedDate };
out.printedDefaults = Object.fromEntries(['new', 'existing', 'missing', 'plan'].map((k) => [k, w.printedSignedDefault(k)]));
out.newState = w.initialState({ source: 'new', today: '2026-10-28', party, services: [] });

// eşit bölme: 10 × 18.000 − %10 burs = 162.000; peşinat 20.000,01 → 141.999,99 / 8 → kuruş farkı son taksitte
st.lines[0].quantity = '10';
st.lines[0].scholarshipPercent = '10';
w.applyAuto(st);
w.setDownPayment(st, { amountMinor: 2000001 });
out.split = st.payment.installments.map((row) => [row.no, row.dueOn, row.amountMinor, row.method]);
out.splitDiff = w.paymentDiff(st);

// elle düzeltme → bölme durur, fark canlı; "farkı son taksite ekle" kapatır; "Eşit böl" geri açar
w.editInstallment(st, 0, { amountMinor: 1000000 });
out.manual = { auto: st.payment.auto, diff: w.paymentDiff(st).diffMinor, text: w.diffText(w.paymentDiff(st).diffMinor) };
out.settled = w.settleOnLast(st);
out.afterSettle = { diff: w.paymentDiff(st).diffMinor, last: st.payment.installments.at(-1).amountMinor };
w.resplit(st);
// tarih alanı odaktan çıkınca AYNI değeri bildirir: eşit bölme bozulmaz
w.editInstallment(st, 0, { dueOn: st.payment.installments[0].dueOn });
w.setPayment(st, { firstDue: st.payment.firstDue });
w.setDownPayment(st, { dueOn: st.payment.downPayment.dueOn });
out.resplit = { auto: st.payment.auto, diff: w.paymentDiff(st).diffMinor, downLinked: st.payment.downLinked,
  firstDueLinked: st.payment.firstDueLinked };
out.over = w.diffText(-12345);
out.equal = w.diffText(0);

// sözleşme tarihi değişince bağlı alanlar izler (elle değiştirilen izlemez)
w.setContractDate(st, '2025-09-15');
out.dated = { registeredOn: st.registeredOn, firstDue: st.payment.firstDue, downDue: st.payment.downPayment.dueOn,
  year: st.academicYear, signed: st.existingSignedDate, firstSplit: st.payment.installments[0].dueOn };
st.registeredLinked = false;
st.registeredOn = '2025-09-01';
w.setContractDate(st, '2025-09-20');
out.unlinked = st.registeredOn;

// tür: hepsi
w.setMethodAll(st, 'promissory');
out.methods = [...new Set(st.payment.installments.map((row) => row.method))];

// peşin mod: tek ödeme = sözleşme tutarı
const cash = w.initialState({ source: 'existing', today: '2026-02-08', party, services: [service] });
w.setPayment(cash, { mode: 'cash' });
out.cash = { rows: cash.payment.installments, diff: w.paymentDiff(cash).diffMinor };

// ContractDraft kurucusu
st.existingNumber = '72128';
st.discountReasons = ['sibling'];
st.specialClauses = 'Servis dahildir.\n\n  Kitap seti ayrıca verilir. ';
st.pastPayments = [{ receivedOn: '2025-10-20', amountMinor: 500000, method: 'cash', note: ' Ekim ' },
  { receivedOn: '', amountMinor: null, method: 'cash', note: '' }];
out.draft = w.buildDraft(st);
out.ready = w.readiness(st, { today: '2026-10-28' });
const notPrinted = w.initialState({ source: 'new', today: '2026-10-28', party, services: [service] });
out.draftNew = w.buildDraft(notPrinted);
out.readyNew = w.readiness(notPrinted, { today: '2026-10-28' }).ok;
const future = w.initialState({ source: 'existing', today: '2026-10-28', party, services: [service] });
future.existingNumber = '';
future.pastPayments = [{ receivedOn: '2026-12-01', amountMinor: 100, method: 'cash' }];
out.futureErrors = w.readiness(future, { today: '2026-10-28' }).errors.map((e) => [e.section, e.field]);

// geçmiş ödemeler kısayolu
const old = w.initialState({ source: 'existing', today: '2026-01-15', party, services: [service] });
w.setContractDate(old, '2025-09-10');
w.setDownPayment(old, { amountMinor: 1000000 });
out.due = w.duePastPayments(old, '2026-01-15');

// Idempotency-Key gövdeye bağlı
out.keySame = w.createKey('km-ctr-x', out.draft) === w.createKey('km-ctr-x', JSON.parse(JSON.stringify(out.draft)));
out.keyDiff = w.createKey('km-ctr-x', out.draft) !== w.createKey('km-ctr-x', out.draftNew);

// mevcut plandan ("Sözleşme oluştur"): totalMinor indirim SONRASIDIR
const plan = { id: '11111111-1111-4111-8111-111111111111', title: '2025-2026 Eğitim Ücreti', totalMinor: 900000,
  discount: { amountMinor: 100000, listPriceMinor: 1000000 },
  charges: [
    { kind: 'down_payment', amountMinor: 300000, dueDate: '2025-09-01', status: 'paid' },
    { kind: 'installment', amountMinor: 300000, dueDate: '2025-10-01', status: 'open' },
    { kind: 'installment', amountMinor: 300000, dueDate: '2025-11-01', status: 'open' },
    { kind: 'installment', amountMinor: 50000, dueDate: '2025-12-01', status: 'cancelled' }] };
const fromPlan = w.initialState({ source: 'plan', today: '2026-01-10', party, plan, services: [service] });
out.plan = { line: s.lineTotals(fromPlan.lines[0]), unit: fromPlan.lines[0].unitPriceMinor,
  extra: fromPlan.lines[0].extraDiscountMinor, diff: w.paymentDiff(fromPlan).diffMinor,
  locked: fromPlan.payment.locked, planId: w.buildDraft(fromPlan).planId, past: w.buildDraft(fromPlan).pastPayments ?? null,
  rows: fromPlan.payment.installments.length, down: fromPlan.payment.downPayment?.amountMinor };
out.noDiscountPlan = w.planLine({ title: 'X', totalMinor: 500, charges: [] });

// kayıt seti (A17) ve olay ayrıntısı
out.docs = w.documentsBody('c-1', { registrationKinds: ['registration', 'meb', 'promissory', 'payment_plan', 'meb'],
  defaultCopies: { registration: 2, meb: 5, promissory: 1, payment_plan: 0 } });
out.target1 = w.eventTarget({ studentId: 'stu-ab12cd34ef', externalId: 'STU-AB12CD34EF', displayName: 'Elif Yılmaz' });
out.target2 = w.eventTarget({ studentId: '22222222-2222-4222-8222-222222222222', planId: 'yok' });
out.unbound = w.plansWithoutContract(
  [{ id: 'p1', status: 'active' }, { id: 'p2', status: 'active' }, { id: 'p3', status: 'cancelled' }],
  { items: [{ id: 'c', planId: 'p1', status: 'active' }] }).map((p) => p.id);
out.money = [w.moneyText(0), w.moneyText(5), w.moneyText(123456789), w.moneyText(-150)];
// aynı eğitim yılında geçerli sözleşme → istemci de durdurur; sunucu hataları bölüm bölüm
const yearState = w.initialState({ source: 'existing', today: '2026-10-28', party, services: [service] });
yearState.existingNumber = '1';
out.sameYear = w.readiness(yearState, { today: '2026-10-28', existing: { items: [
  { id: 'c', number: 'BBD1-1000', academicYear: '2026-2027', status: 'active' }] } }).errors.map((e) => e.field);
out.otherYear = w.readiness(yearState, { today: '2026-10-28', existing: [
  { id: 'c', number: 'BBD1-1000', academicYear: '2025-2026', status: 'active' },
  { id: 'd', number: 'BBD1-1001', academicYear: '2026-2027', status: 'cancelled' }] }).ok;
out.exists = w.serverProblem({ code: 'contract_exists', details: { contractId: 'c-9' } });
out.mismatch = w.serverProblem({ code: 'contract_totals_mismatch', details: { expectedMinor: 16200000, actualMinor: 16100000 } },
  s.contractProblem({ code: 'contract_totals_mismatch' }));
out.passthrough = w.serverProblem({ code: 'contract_number_taken' }, s.contractProblem({ code: 'contract_number_taken' }));

// TEK SENET: varsayılan her taksit; tek senette vade son taksiti izler (elle değişene dek); peşinde senet yok
const pn = w.initialState({ source: 'existing', today: '2026-01-31', party, services: [service] });
pn.lines[0].quantity = '10'; pn.lines[0].scholarshipPercent = '10';
w.setDownPayment(pn, { amountMinor: 2000001 });
out.promDefault = { mode: pn.promissoryMode, body: w.buildDraft(pn).promissoryMode, due: w.buildDraft(pn).promissoryDueOn ?? null,
  info: w.promissoryInfo(pn).text };
w.setPromissory(pn, { mode: 'single' });
out.promSingle = { body: w.buildDraft(pn).promissoryMode, due: w.buildDraft(pn).promissoryDueOn, info: w.promissoryInfo(pn) };
w.setPayment(pn, { count: 10 });
out.promFollows = w.buildDraft(pn).promissoryDueOn;
w.setPromissory(pn, { dueOn: '2027-06-05' });
w.setPayment(pn, { count: 4 });
out.promManual = { due: w.buildDraft(pn).promissoryDueOn, linked: pn.promissoryDueLinked };
w.setPromissory(pn, { dueOn: '2025-01-01' });
out.promEarly = w.readiness(pn, { today: '2026-01-31' }).errors.map((e) => e.field);
w.setPayment(pn, { mode: 'cash' });
const cashBody = w.buildDraft(pn);
out.promCash = { mode: cashBody.promissoryMode ?? null, due: cashBody.promissoryDueOn ?? null, info: w.promissoryInfo(pn).text };
out.promText = [
  w.promissoryText({ promissoryMode: 'single', promissoryDueOn: '2027-06-05', payment: { mode: 'installment', installments: [{ dueOn: '2027-05-05' }] } }),
  w.promissoryText({ promissoryMode: 'single', payment: { mode: 'installment', installments: [{ dueOn: '2027-05-05' }] } }),
  w.promissoryText({ promissoryMode: 'per_installment', payment: { mode: 'installment', installments: [{}] } }),
  w.promissoryText({ payment: { mode: 'cash', installments: [] } }),
];
out.promRevise = [
  w.promissoryReviseBody({ reason: 'Veli istedi', mode: 'single', dueOn: '2027-06-05', current: { mode: 'per_installment' } }),
  w.promissoryReviseBody({ reason: 'Veli istedi', mode: 'per_installment', current: { mode: 'per_installment' } }),
  w.promissoryReviseBody({ reason: 'x', mode: 'single', dueOn: '2027-06-05', current: {} }),
  w.promissoryReviseBody({ reason: 'Veli istedi', mode: 'single', dueOn: '', current: {} }),
  w.promissoryReviseBody({ reason: 'Ayrı senet', mode: 'per_installment', current: { mode: 'single', dueOn: '2027-06-05' } }),
];

// ÖZEL DERS: 40 saat × 1.500 TL = 60.000 TL; ad "ÖZEL DERS — Matematik"; katalog fiyatı önerilir
const lessonSvc = { id: 's9', name: 'Özel Ders', category: 'education', unit: 'Saat', unitPriceMinor: 150000, active: true };
const pl = w.privateLessonLine([service, lessonSvc], '');
pl.quantity = '40';
out.private = { total: s.lineTotals(pl), name: pl.name, unit: pl.unit, serviceId: pl.serviceId, labels: w.lineLabels(pl),
  hourlyCatalog: w.isHourly({ unit: 'Saat' }), plainLabels: w.lineLabels({ unit: 'ay' }) };
w.setLesson(pl, '  Matematik  ');
out.privateName = pl.name;
w.setLesson(pl, '');
out.privateEmpty = pl.name;
const bare = w.privateLessonLine([], 'Fizik');
out.privateBare = { name: bare.name, unit: bare.unit, price: bare.unitPriceMinor, category: bare.category };
const pd = w.initialState({ source: 'existing', today: '2026-01-31', party, services: [service] });
pd.lines = [w.setLesson(Object.assign(w.privateLessonLine([lessonSvc]), { quantity: '40' }), 'Matematik'),
  Object.assign(w.privateLessonLine([lessonSvc], 'Fizik'), { quantity: '10' })];
w.applyAuto(pd);
out.privateDraft = w.buildDraft(pd).lines;
out.suggest = [w.missingSuggestions([]).map((i) => i.name), w.missingSuggestions([{ name: 'grup dersi' }]).map((i) => i.name),
  w.missingSuggestions([{ name: 'GRUP DERSİ' }, lessonSvc]).length];
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def logic(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    folder = tmp_path_factory.mktemp("sozlesme_sihirbaz")
    (folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    for name in ("wizard-logic.js", "shared-contract-logic.js"):
        (folder / name).write_bytes((PANEL / "contracts" / name).read_bytes())
    (folder / "run.js").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.js")], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


def test_satir_toplami_taslak_kurali_yarim_yukari(logic: dict[str, Any]) -> None:
    assert logic["line"] == {"amountMinor": 18000000, "discountMinor": 1800000, "totalMinor": 16200000, "errors": []}
    # 333 × 1,5 = 499,5 → 500; %12,5 burs = 62,5 → 63 (yarım yukarı)
    assert logic["lineHalf"]["amountMinor"] == 500
    assert logic["lineHalf"]["discountMinor"] == 63
    assert logic["lineHalf"]["totalMinor"] == 437
    # %0,5 + %0,5 AYRI yuvarlanır (500,005 → 500 iki kez) + 99 TL kuruş
    assert logic["lineBoth"]["discountMinor"] == 500 + 500 + 99
    # indirim satır tutarını aşamaz
    assert logic["lineOver"]["discountMinor"] == 1000 and "discountExceeds" in logic["lineOver"]["errors"]


def test_esit_bolme_kurus_farki_son_taksitte(logic: dict[str, Any]) -> None:
    split = logic["split"]
    assert len(split) == 8
    amounts = [row[2] for row in split]
    # 16.200.000 − 2.000.001 = 14.199.999 → 1.774.999 × 7 + 1.775.006
    assert amounts[:7] == [1774999] * 7
    assert amounts[7] == 14199999 - 1774999 * 7
    assert sum(amounts) == 14199999
    assert [row[1] for row in split][:3] == ["2026-02-28", "2026-03-28", "2026-04-28"]
    assert {row[3] for row in split} == {"cash"}
    assert logic["splitDiff"]["diffMinor"] == 0 and logic["splitDiff"]["ok"] is True


def test_toplam_farki_ve_duzeltmeler(logic: dict[str, Any]) -> None:
    assert logic["manual"]["auto"] is False
    assert logic["manual"]["diff"] == 1774999 - 1000000
    assert logic["manual"]["text"]["tone"] == "bad" and "7.749,99 ₺" in logic["manual"]["text"]["text"]
    assert logic["settled"] is True and logic["afterSettle"]["diff"] == 0
    assert logic["resplit"] == {"auto": True, "diff": 0, "downLinked": True, "firstDueLinked": True}
    assert "fazla" in logic["over"]["text"] and "123,45 ₺" in logic["over"]["text"]
    assert logic["equal"]["tone"] == "good"


def test_akilli_varsayilanlar(logic: dict[str, Any]) -> None:
    existing = logic["existing"]
    assert existing["printed"] is True
    assert existing["year"] == "2025-2026"
    assert existing["firstDue"] == "2026-02-28"          # gelecek ayın sözleşme günü (ay sonu taşmaz)
    assert existing["method"] == "cash" and existing["count"] == 8
    assert existing["payer"] == 1
    assert existing["line"]["serviceId"] == "s1" and existing["line"]["unitPriceMinor"] == 1800000
    assert existing["registeredOn"] == existing["downDue"] == existing["signedDate"] == "2026-01-31"
    new = logic["newState"]
    assert new["existingSignedOn"] is False and new["academicYear"] == "2026-2027"
    assert new["payment"]["firstDue"] == "2026-11-28"
    assert new["lines"][0]["serviceId"] is None              # katalog boş → serbest satır


def test_basili_imzali_varsayilani(logic: dict[str, Any]) -> None:
    assert logic["printedDefaults"] == {"new": False, "existing": True, "missing": True, "plan": True}
    assert logic["draftNew"]["existingSigned"] is None
    assert logic["draft"]["existingSigned"] == {"number": "72128", "signedOn": "2025-09-20"}


def test_sozlesme_tarihi_bagli_alanlari_surukler(logic: dict[str, Any]) -> None:
    dated = logic["dated"]
    assert dated == {"registeredOn": "2025-09-15", "firstDue": "2025-10-15", "downDue": "2025-09-15",
                     "year": "2025-2026", "signed": "2025-09-15", "firstSplit": "2025-10-15"}
    assert logic["unlinked"] == "2025-09-01"
    assert logic["methods"] == ["promissory"]


def test_pesin_tek_odeme(logic: dict[str, Any]) -> None:
    rows = logic["cash"]["rows"]
    assert len(rows) == 1 and rows[0]["amountMinor"] == 1800000 and rows[0]["dueOn"] == "2026-02-08"
    assert logic["cash"]["diff"] == 0


def test_contract_draft_kurucusu(logic: dict[str, Any]) -> None:
    draft = logic["draft"]
    assert set(draft) >= {"academicYear", "registeredOn", "contractDate", "payerGuardianSlot", "lines", "payment"}
    assert draft["lines"] == [{"category": "education", "name": "GRUP DERSİ", "unitPriceMinor": 1800000,
                               "quantity": 10, "scholarshipPercent": 10, "discountPercent": 0,
                               "extraDiscountMinor": 0, "serviceId": "s1", "unit": "ay"}]
    payment = draft["payment"]
    assert payment["mode"] == "installment"
    assert payment["downPayment"] == {"amountMinor": 2000001, "dueOn": "2025-09-20", "method": "cash"}
    assert [row["no"] for row in payment["installments"]] == list(range(1, 9))
    assert {row["method"] for row in payment["installments"]} == {"promissory"}
    assert draft["discountReasons"] == ["sibling"]
    assert draft["specialClauses"] == ["Servis dahildir.", "Kitap seti ayrıca verilir."]
    # boş geçmiş ödeme satırı gitmez, not kırpılır
    assert draft["pastPayments"] == [{"receivedOn": "2025-10-20", "amountMinor": 500000, "method": "cash", "note": "Ekim"}]
    assert "planId" not in draft
    assert logic["ready"]["ok"] is True
    assert logic["readyNew"] is True


def test_dogrulama_alan_alan(logic: dict[str, Any]) -> None:
    errors = logic["futureErrors"]
    assert ["past", "pastPayments.0.receivedOn"] in errors          # gelecek tarihli tahsilat
    assert ["contract", "existingSigned.number"] in errors          # basılı imzalıda eski no zorunlu


def test_vadesi_gelenleri_odendi_say(logic: dict[str, Any]) -> None:
    due = logic["due"]
    assert due[0] == {"receivedOn": "2025-09-10", "amountMinor": 1000000, "method": "cash", "note": "Peşinat"}
    assert [row["receivedOn"] for row in due[1:]] == ["2025-10-10", "2025-11-10", "2025-12-10", "2026-01-10"]
    assert all(row["note"].endswith(". taksit") for row in due[1:])


def test_idempotency_anahtari_govdeye_bagli(logic: dict[str, Any]) -> None:
    assert logic["keySame"] is True and logic["keyDiff"] is True


def test_mevcut_plandan_sozlesme(logic: dict[str, Any]) -> None:
    plan = logic["plan"]
    # iptal edilen kalem dışarıda; liste fiyatı + indirim TL → satır toplamı = canlı kalem toplamı (900.000)
    assert plan["unit"] == 1000000 and plan["extra"] == 100000
    assert plan["line"]["totalMinor"] == 900000
    assert plan["diff"] == 0 and plan["locked"] is True
    assert plan["planId"] == "11111111-1111-4111-8111-111111111111"
    assert plan["past"] is None and plan["rows"] == 2 and plan["down"] == 300000
    assert logic["noDiscountPlan"]["unitPriceMinor"] == 500 and logic["noDiscountPlan"]["extraDiscountMinor"] == 0


def test_kayit_seti_ve_olay_ayrintisi(logic: dict[str, Any]) -> None:
    assert logic["docs"] == {"contractIds": ["c-1"], "kinds": ["registration", "meb", "promissory", "payment_plan"],
                             "options": {"copies": {"registration": 2, "meb": 3, "promissory": 1, "payment_plan": 1},
                                         "promissoryScope": "all", "revision": "latest"}}
    assert logic["target1"] == {"akademiId": None, "refs": ["STU-AB12CD34EF"], "name": "Elif Yılmaz", "planId": None}
    assert logic["target2"]["akademiId"] == "22222222-2222-4222-8222-222222222222"
    assert logic["target2"]["planId"] is None
    assert logic["unbound"] == ["p2"]
    assert logic["money"] == ["0,00 ₺", "0,05 ₺", "1.234.567,89 ₺", "-1,50 ₺"]


def test_tek_senet_varsayilan_ve_govde(logic: dict[str, Any]) -> None:
    assert logic["promDefault"] == {"mode": "per_installment", "body": "per_installment", "due": None, "info": "8 senet"}
    single = logic["promSingle"]
    assert single["body"] == "single" and single["due"] == "2026-09-28"           # son (8.) taksitin vadesi
    assert single["info"]["count"] == 1 and single["info"]["amountMinor"] == 16200000 - 2000001
    assert single["info"]["text"] == "1 senet, 141.999,99 ₺"
    assert logic["promFollows"] == "2026-11-28"                                    # sayı 10 → son vade izler
    assert logic["promManual"] == {"due": "2027-06-05", "linked": False}         # elle vade izlemeyi bırakır
    assert "promissoryDueOn" in logic["promEarly"]                                # sözleşme tarihinden önce olamaz
    assert logic["promCash"] == {"mode": None, "due": None, "info": "Senet yok (peşin ödeme)"}
    assert logic["promText"] == ["Tek senet (vade 05.06.2027)", "Tek senet (vade 05.05.2027)", "Her taksit için ayrı",
                                 "Senet yok (peşin)"]
    revise = logic["promRevise"]
    assert revise[0] == {"body": {"reason": "Veli istedi", "promissory": {"mode": "single", "dueOn": "2027-06-05"}}}
    assert revise[1] == {"error": "Değişiklik yok."}
    assert "Gerekçe" in revise[2]["error"]
    assert "vadesini" in revise[3]["error"]
    assert revise[4] == {"body": {"reason": "Ayrı senet", "promissory": {"mode": "per_installment"}}}


def test_ozel_ders_satiri(logic: dict[str, Any]) -> None:
    private = logic["private"]
    assert private["total"]["amountMinor"] == 6000000 and private["total"]["totalMinor"] == 6000000   # 40 × 1.500 TL
    assert private["name"] == "ÖZEL DERS" and private["unit"] == "saat" and private["serviceId"] == "s9"
    assert private["labels"] == {"quantity": "Ders saati", "unitPrice": "Saat ücreti"}
    assert private["hourlyCatalog"] is True
    assert private["plainLabels"] == {"quantity": "Miktar", "unitPrice": "Birim fiyat"}
    assert logic["privateName"] == "ÖZEL DERS — Matematik"
    assert logic["privateEmpty"] == "ÖZEL DERS"
    assert logic["privateBare"] == {"name": "ÖZEL DERS — Fizik", "unit": "saat", "price": None, "category": "education"}
    lines = logic["privateDraft"]
    assert [line["name"] for line in lines] == ["ÖZEL DERS — Matematik", "ÖZEL DERS — Fizik"]
    assert all("lesson" not in line and "privateLesson" not in line for line in lines)     # yalnız ekran alanı
    assert lines[0]["unit"] == "saat" and lines[0]["quantity"] == 40 and lines[0]["unitPriceMinor"] == 150000
    assert logic["suggest"] == [["GRUP DERSİ", "ÖZEL DERS"], ["ÖZEL DERS"], 0]


def test_ayni_yil_ve_sunucu_hatalari(logic: dict[str, Any]) -> None:
    assert logic["sameYear"] == ["academicYear"]
    assert logic["otherYear"] is True
    assert logic["exists"]["contractId"] == "c-9" and logic["exists"]["fields"][0]["section"] == "contract"
    assert "162.000,00 ₺" in logic["mismatch"]["summary"] and "161.000,00 ₺" in logic["mismatch"]["summary"]
    assert logic["mismatch"]["fields"][0]["section"] == "payment"
    assert logic["passthrough"]["fields"][0]["field"] == "existingSigned.number"


# ----------------------------------------------------------------------------- ekran

SCREEN = PRELUDE + r"""
const SID = '33333333-3333-4333-8333-333333333333';
const PARTY = { party: { student: { fullName: 'Elif Yılmaz', nationalIdMasked: '*******0146', foreignIdMasked: null,
    birthDate: '2011-05-01', gender: 'female', registeredOn: null, address: { line: 'Örnek Mah. 1', district: 'Selçuklu',
    city: 'Konya' }, motherName: 'Ayşe Yılmaz', fatherName: null, studentNo: 'STU-AB12CD34EF', classGroup: '8-A' },
  guardian: { slot: 1, fullName: 'Ayşe Yılmaz' },
  guardians: [{ slot: 1, fullName: 'Ayşe Yılmaz', relation: 'mother', nationalIdMasked: '*******0278',
    phoneMasked: '+90 5** *** ** 67', occupation: null, email: null, homeAddress: { line: 'Örnek Mah. 1', district: 'Selçuklu',
    city: 'Konya' }, workAddress: null }] }, missingFields: [], identityUpdatedAt: '2026-10-28T10:00:00Z' };
const fire = (node, type) => node.dispatchEvent(new Event(type, { bubbles: true }));
const type = (node, text) => { node.value = text; fire(node, 'input'); };

export default async function ({ document, sleep, api, load }) {
  globalThis.api = api;
  routes[`GET /v1/admin/students/${SID}/contract-party`] = PARTY;
  routes[`GET /v1/admin/students/${SID}`] = { student: { id: SID, displayName: 'Elif Yılmaz', externalRef: 'STU-AB12CD34EF' } };
  routes['GET /v1/admin/contracts/services'] = { items: [{ id: 'svc-1', name: 'GRUP DERSİ', category: 'education', unit: 'ay',
    unitPriceMinor: 1800000, vatPercent: null, active: true, sortOrder: 1 }] };
  routes['GET /v1/admin/settings'] = { 'contracts.nextNumber': 1000, 'contracts.numberPrefix': 'BBD1-' };
  routes[`GET /v1/admin/students/${SID}/contracts`] = { items: [] };
  let created = 0;
  routes[`POST /v1/admin/students/${SID}/contracts`] = (body) => {
    created += 1;
    return { id: 'ctr-1', number: body.existingSigned ? body.existingSigned.number : 'BBD1-1000', student: { id: SID } };
  };
  const out = {};
  const { app, root, toasts } = await makeApp(load, document, { allowed: ['admin_createContract', 'admin_getSettings',
    'admin_listStudentContracts', 'admin_getFinanceAccount'] });
  const changes = [];
  window.addEventListener('bbd-akademi:contract-changed', (event) => changes.push(event.detail));
  const { installContractWizard, contractEntry } = await load('./panels/bbd_akademi/contracts/wizard-entry.js');
  const stop = installContractWizard(app);

  // 1) mevcut öğrenci: sözleşme olayından açılış (80-km-sozlesme / 80-km-belge yayınlar)
  window.dispatchEvent(new CustomEvent('bbd-akademi:open-contract-wizard', { detail: { studentId: SID } }));
  await sleep(80);
  const dialog = root.querySelector('.ak-wz');
  out.opened = Boolean(dialog);
  out.title = dialog.querySelector('.kit-dialog-title').textContent;
  out.sections = [...dialog.querySelectorAll('.ak-wz-sec-title')].map((n) => n.textContent);
  out.printedOn = dialog.querySelector('[data-field="existingSignedOn"]').checked;
  out.printHidden = [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Kaydet ve yazdır').hidden;
  out.missingBadge = dialog.querySelector('.ak-wz-missing').textContent;
  // miktar 10, burs %10 → 162.000 ₺; peşinat 20.000 → 8 taksit
  const lines = dialog.querySelector('.ak-wz-lines');
  type(lines.querySelector('[data-field="quantity"]'), '10');
  type(lines.querySelector('[data-field="scholarshipPercent"]'), '10');
  type(dialog.querySelector('[data-field="downPayment.amountMinor"]'), '20.000');
  await sleep(10);
  out.rowCount = dialog.querySelectorAll('.ak-wz-inst .ak-wz-row:not(.head)').length;
  out.sum = dialog.querySelector('.ak-wz-sum').textContent;
  const confirm = [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Onayla');
  // basılı imzalı: eski no yazılmadan onay → alan iletisi, istek gitmez
  confirm.click();
  await sleep(20);
  out.blockedErrors = dialog.querySelector('[data-section="contract"] .ak-wz-sec-errors').textContent;
  out.createdBefore = created;
  type(dialog.querySelector('[data-field="existingNumber"]'), '72128');
  // taksit satırı elle bozulunca Onayla kapanır
  const amount = dialog.querySelector('.ak-wz-inst [data-field="installmentAmount"]');
  type(amount, '1,00');
  await sleep(10);
  out.disabledOnDiff = confirm.disabled;
  [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Eşit böl').click();
  await sleep(10);
  out.enabledAfterSplit = !confirm.disabled;
  // özel ders: + Özel ders ekle → ders, saat ücreti, ders saati (etiketler saatlik); toplam canlı, taksitler yeniden bölünür
  [...dialog.querySelectorAll('button')].find((b) => b.textContent === '+ Özel ders ekle').click();
  await sleep(10);
  const lessonRow = [...dialog.querySelectorAll('.ak-wz-lines .ak-wz-row')].at(-1);
  type(lessonRow.querySelector('[data-field="lesson"]'), 'Matematik');
  type(lessonRow.querySelector('[data-field="unitPriceMinor"]'), '1.500');
  type(lessonRow.querySelector('[data-field="quantity"]'), '40');
  await sleep(10);
  out.lessonLabels = [...lessonRow.querySelectorAll('.ak-wz-mini > span')].map((n) => n.textContent);
  out.lessonTotal = lessonRow.querySelector('.ak-wz-total').textContent;
  // tek senet: radyo → vade alanı (son taksit), alt şeritte senet bilgisi
  const single = dialog.querySelector('[data-field="promissory.single"]');
  single.checked = true;
  fire(single, 'change');
  await sleep(10);
  out.promDue = dialog.querySelector('[data-field="promissoryDueOn"] input')?.value || '';
  out.sumAfter = dialog.querySelector('.ak-wz-sum').textContent;
  confirm.click();
  await sleep(60);
  const post = sent.filter((p) => p.method === 'POST' && p.path.endsWith('/contracts'));
  out.posts = post.length;
  out.body = post[0]?.body;
  out.key = post[0]?.idempotencyKey || '';
  out.closed = !root.querySelector('.ak-wz');
  out.changes = changes;
  out.toasts = toasts.map((t) => t[0]);

  // 2) yeni öğrenci: KM olayı → Akademi'de aranır (ilk arama boş, sonra bulunur) → Basılı imzalı KAPALI
  let searches = 0;
  routes['GET /v1/admin/students'] = (_body, payload) => {
    searches += 1;
    return { items: searches < 2 ? [] : [{ id: SID, externalRef: payload.query.externalRef, displayName: 'Elif Yılmaz' }],
      nextCursor: null };
  };
  window.dispatchEvent(new CustomEvent('bbd-students:created', { detail: { studentId: 'STU-AB12CD34EF',
    externalId: 'STU-AB12CD34EF', displayName: 'Elif Yılmaz' } }));
  await sleep(30);
  out.waiting = root.querySelector('.ak-wz .ak-wz-wait')?.textContent || '';
  await sleep(1500);
  const second = root.querySelector('.ak-wz');
  out.newSearches = searches;
  out.newPrinted = second?.querySelector('[data-field="existingSignedOn"]')?.checked;
  out.newPrintVisible = !([...second.querySelectorAll('button')].find((b) => b.textContent === 'Kaydet ve yazdır').hidden);
  out.newSections = [...second.querySelectorAll('.ak-wz-sec-title')].map((n) => n.textContent);
  // Esc: değişiklik yoksa sormadan kapanır
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  await sleep(20);
  out.escClosed = !root.querySelector('.ak-wz');

  // 3) giriş düğmesi: sözleşmesiz etkin plan → "Sözleşme oluştur"
  routes[`GET /v1/admin/students/${SID}/contracts`] = { items: [{ id: 'c0', planId: 'p-bagli', status: 'active' }] };
  const entry = contractEntry(app, { studentId: SID, plans: [{ id: 'p-bagli', title: 'Eski', status: 'active' },
    { id: 'p-serbest', title: '2025-2026 Eğitim Ücreti', status: 'active' }] });
  await sleep(20);
  out.entry = [...entry.querySelectorAll('button')].map((b) => b.textContent);
  stop();
  return out;
}
"""


def test_ekran_mevcut_ogrenci_tek_onay_ve_yeni_ogrenci_adimi() -> None:
    try:
        data = run(SCREEN, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == []
    out = data["result"]
    assert out["opened"] is True and out["title"] == "Kayıt ve ödeme planı — Elif Yılmaz"
    assert out["sections"] == ["Öğrenci ve veli", "Hizmetler", "Ödeme", "Geçmiş ödemeler", "Sözleşme"]
    assert out["printedOn"] is True and out["printHidden"] is True
    assert "Belge bilgileri tam" in out["missingBadge"]
    assert out["rowCount"] == 8
    assert "162.000,00 ₺" in out["sum"]
    assert "Eski sözleşme numarasını yazın." in out["blockedErrors"] and out["createdBefore"] == 0
    assert out["disabledOnDiff"] is True and out["enabledAfterSplit"] is True
    assert out["posts"] == 1
    body = out["body"]
    assert body["existingSigned"]["number"] == "72128"
    assert body["payerGuardianSlot"] == 1
    assert body["lines"][0]["quantity"] == 10 and body["lines"][0]["scholarshipPercent"] == 10
    assert body["payment"]["downPayment"]["amountMinor"] == 2000000
    assert sum(row["amountMinor"] for row in body["payment"]["installments"]) == 16200000 + 6000000 - 2000000
    assert body["lines"][1] == {"category": "education", "name": "ÖZEL DERS — Matematik", "unitPriceMinor": 150000,
                                "quantity": 40, "scholarshipPercent": 0, "discountPercent": 0, "extraDiscountMinor": 0,
                                "unit": "saat"}
    assert out["lessonLabels"] == ["Saat ücreti", "Ders saati"]
    assert out["lessonTotal"] == "60.000,00 ₺"
    assert body["promissoryMode"] == "single"
    assert body["promissoryDueOn"] == body["payment"]["installments"][-1]["dueOn"]
    assert out["promDue"] == ".".join(reversed(body["promissoryDueOn"].split("-")))
    assert "Senet: 1 senet, 202.000,00 ₺" in out["sumAfter"]
    assert out["key"].startswith("km-ctr-")
    assert out["closed"] is True
    assert out["changes"] == [{"contractId": "ctr-1", "studentId": "33333333-3333-4333-8333-333333333333",
                               "change": "created"}]
    assert any("72128 imzalı kaydedildi" in text for text in out["toasts"])

    assert "aktarılıyor" in out["waiting"]
    assert out["newSearches"] >= 2
    assert out["newPrinted"] is False and out["newPrintVisible"] is True
    assert out["newSections"] == ["Öğrenci ve veli", "Hizmetler", "Ödeme", "Sözleşme"]
    assert out["escClosed"] is True
    assert out["entry"] == ["Kayıt ve ödeme planı", "Sözleşme oluştur · 2025-2026 Eğitim Ücreti"]
