"""Sözleşmeler ekranı (F-SZ, Akademi README §14 madde 139) — KM: izin eşlemesi, "Belge çıkar" isteği kurucusu, IBAN,
sözleşme ayarları yaması, madde şablonu düzenleyici modeli, yazdırma zinciri.

Saf mantık dosyaları (`ui/panel/contracts/*-logic.js`) bilerek import'suz ve DOM'suz yazıldı: `.mjs` kopyaları Node'da
yüklenir, sonuç JSON olarak döner. `node` yoksa bu kısım ATLANIR.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from modules.bbd_akademi.backend.core.operations import Operation
from modules.bbd_akademi.backend.documents.service import AKADEMI_PDFS
from modules.bbd_akademi.backend.permission_map import (
    ALL_PERMISSIONS,
    CONTRACTS,
    FINANCE,
    SETTINGS,
    km_permissions,
)

MODULE = Path(__file__).resolve().parents[1]
CONTRACTS_UI = MODULE / "ui" / "panel" / "contracts"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")


def op(operation_id: str, *permissions: str, method: str = "GET", path: str = "/v1/admin/contracts") -> Operation:
    return Operation(method, path, operation_id, permissions, "both", feature_flag="contracts")


# --------------------------------------------------------------- izin eşlemesi


def test_sozlesme_izinleri_finans_ve_kurulum() -> None:
    """Görüntüleme + günlük iş (imza, tarama, belge çıkar) finans izni; kurulum ayrı KM izni (K12)."""
    assert km_permissions(op("admin_listContracts", "contracts.view")) == (FINANCE,)
    assert km_permissions(op("admin_buildContractDocuments", "contracts.manage", method="POST",
                             path="/v1/admin/contracts/documents")) == (FINANCE,)
    assert km_permissions(op("admin_signContract", "contracts.manage", method="POST")) == (FINANCE,)
    assert km_permissions(op("admin_reviseContract", "contracts.setup", method="POST")) == (CONTRACTS,)
    assert km_permissions(op("admin_createContractService", "contracts.setup", method="POST")) == (CONTRACTS,)


def test_olusturma_finans_izniyle_tek_basina_acilmaz() -> None:
    """A6 Akademi'de `contracts.setup` + `finance.manage` ister; KM'de "birisi yeterli" kuralı yalnız finans izniyle
    oluşturmayı açardı — işlem kurulum iznine bağlıdır."""
    both = op("admin_createContract", "contracts.setup", "finance.manage", method="POST")
    assert km_permissions(both) == (CONTRACTS,)


def test_sablon_metni_kurum_ayari() -> None:
    assert km_permissions(op("admin_saveContractTemplate", "contracts.setup", method="POST")) == (SETTINGS,)
    assert km_permissions(op("admin_getContractTemplate", "contracts.view")) == (FINANCE,)


def test_yeni_km_izni_ilan_edildi() -> None:
    manifest = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["permissions"] if item["key"] == CONTRACTS)
    assert entry["default_roles"] == ["admin", "bbd_staff"]
    assert CONTRACTS in ALL_PERMISSIONS


def test_yazdirma_zinciri_sozlesme_turleri() -> None:
    """Akademi'nin sözleşme PDF'i KM önizlemesine girer ve PDF'in kendisi yazıcıya gider (`/km/documents/akademi`)."""
    assert AKADEMI_PDFS["contract_documents"].operations == ("admin_buildContractDocuments",)
    assert AKADEMI_PDFS["contract_document"].operations == ("admin_getContractDocument",)
    assert AKADEMI_PDFS["contract_attachment"].operations == ("admin_getContractAttachmentFile",)
    assert AKADEMI_PDFS["contract_preview"].operations == ("admin_previewContract",)


# --------------------------------------------------------------- saf mantık (Node)

SCRIPT = r"""
import * as d from './documents-logic.mjs';
import * as s from './settings-logic.mjs';
import * as t from './templates-logic.mjs';
const out = {};

// Belge çıkar isteği kurucusu
out.selection = d.buildDocumentsRequest({ target: { contractIds: ['c2', 'c1', 'c2', ''] },
  kinds: ['payment_plan', 'registration', 'promissory', 'yok', 'registration'],
  copies: { registration: 5, promissory: 0, payment_plan: '2' }, promissoryScope: 'open', revision: 'signed' });
out.noKinds = d.buildDocumentsRequest({ target: { contractIds: ['c1'] }, kinds: [] });
out.noTarget = d.buildDocumentsRequest({ target: {}, kinds: ['meb'] });
out.filterTarget = d.buildDocumentsRequest({ target: { filter: { classId: 'k8', overdueOnly: true } }, kinds: ['meb'] });
out.legal = d.legalNoticeRequest({ legal_notice: 3 });
out.legalDefault = d.legalNoticeRequest(null);
out.filters = {
  plain: d.documentsFilter({ chip: 'all' }),
  akademi: d.documentsFilter({ chip: 'awaiting', classId: 'k8', overdueOnly: true }),
  search: d.documentsFilter({ chip: 'signed', q: 'ayşe' }),
  year: d.documentsFilter({ academicYear: '2026-2027' }),
  cancelled: d.documentsFilter({ chip: 'cancelled' }),
};
out.query = d.listQuery({ chip: 'resign_required', classId: '', q: '  can ', academicYear: '2026-2027', overdueOnly: false });
out.queryCancelled = d.listQuery({ chip: 'cancelled', overdueOnly: true });
out.copies = d.defaultCopies({ registration: 3, meb: 9, promissory: null });
out.outcomeReady = d.documentsOutcome({ status: 'ready', file: { url: 'https://x/f.pdf', fileName: 'sozlesmeler.pdf' },
  includedCount: 3, skipped: [{ student: { id: 's9', displayName: 'Elif Yılmaz', classGroup: { name: '8-A' } },
    code: 'contract_missing_fields', missingFields: [{ field: 'nationalId', label: 'Veli TC kimlik no' },
      { field: 'homeAddress', label: 'Veli ev adresi' }] }] });
out.outcomeQueued = d.documentsOutcome({ status: 'queued', jobId: 'j1', file: null, includedCount: 0, skipped: [] });
out.outcomeEmpty = d.documentsOutcome({ status: 'ready', file: null, includedCount: 0, skipped: [] });
out.skipLine = d.skippedLine(out.outcomeReady.skipped[0]);
out.queue = [d.willQueue(40), d.willQueue(41)];
out.fpSame = d.listFingerprint([{ id: 'a', latestRevisionNo: 1, signature: { state: 'awaiting' }, status: 'active' }])
  === d.listFingerprint([{ id: 'a', latestRevisionNo: 1, signature: { state: 'awaiting' }, status: 'active', other: 1 }]);
out.fpSigned = d.listFingerprint([{ id: 'a', latestRevisionNo: 1, signature: { state: 'awaiting' } }])
  === d.listFingerprint([{ id: 'a', latestRevisionNo: 1, signature: { state: 'signed' } }]);
out.years = d.academicYearOptions(new Date(2026, 9, 28), 9, 1);
out.yearsSpring = d.academicYearOptions(new Date(2027, 2, 1), 9, 0);
out.numbers = [d.contractNumber({ number: 'BBD1-1000' }), d.contractNumber({ number: 72128 }), d.contractNumber({})];
out.labels = d.DOCUMENT_KINDS.map((k) => k.label);

// IBAN ve ayarlar
out.iban = {
  ok: s.ibanError('TR33 0006 1005 1978 6457 8413 26'),
  okLower: s.ibanError('tr330006100519786457841326'),
  badCheck: s.ibanError('TR340006100519786457841326'),
  short: s.ibanError('TR33000610051978645784132'),
  foreign: s.ibanError('DE89370400440532013000'),
  letters: s.ibanError('TR33000610051978645784132X'),
  empty: s.ibanError('  '),
  format: s.formatIban('tr330006100519786457841326'),
};
out.numberPreview = [s.nextNumberPreview('BBD1-', 1000), s.nextNumberPreview('', '72'), s.nextNumberPreview('X-', 0)];
const server = { legalName: 'BBD Kurs', courtCity: 'KONYA', numberPrefix: 'BBD1-', nextNumber: 1000,
  bankAccounts: [{ bank: 'Ziraat', holder: 'BBD', branch: null, iban: 'TR330006100519786457841326' }],
  defaultCopies: { registration: 2 } };
const draft = s.settingsDraft(server);
out.draftIban = draft.bankAccounts[0].iban;
out.draftCopies = draft.defaultCopies;
out.noChange = s.settingsPatch(server, draft);
const changed = s.settingsDraft(server);
changed.courtCity = 'izmir';
changed.nextNumber = '1005';
changed.bankAccounts.push({ bank: 'Vakıf', holder: 'BBD', branch: '', iban: 'TR33 0006 1005 1978 6457 8413 26' });
out.patch = s.settingsPatch(server, changed);
out.errors = s.validateSettings(changed);
const bad = s.settingsDraft(server);
bad.numberPrefix = 'ÇOK-UZUN-ÖNEK';
bad.nextNumber = 0;
bad.registrationKinds = [];
bad.signatureLabels.guardian = ' ';
bad.bankAccounts.push({ bank: '', holder: '', branch: '', iban: 'TR12' });
out.badErrors = s.validateSettings(bad);
out.upper = s.upperTr('istanbul ılgın');
out.dirty = s.dirtyCount(server, changed);

// Madde şablonu modeli
let c = t.cloneClauses([{ title: 'Taraflar', items: ['a) {kurumAdi} ile veli'] }, { title: null, items: [] }]);
out.cloned = c;
c = t.addClause(c, 1);
c = t.setTitle(c, 1, 'Ücret');
c = t.setItem(c, 1, 0, 'Ücret {tutar} TL, kesinti %{kesintiOrani}');
c = t.addItem(c, 1);
c = t.setItem(c, 1, 1, '   ');
c = t.moveClause(c, 1, -1);
out.moved = t.normalizeClauses(c);
out.moveOut = t.moveClause(c, 0, -1).map((x) => x.title);
out.removedItem = t.removeItem([{ title: 'X', items: ['tek'] }], 0, 0);
out.movedItem = t.moveItem([{ title: null, items: ['1', '2', '3'] }], 0, 2, -2)[0].items;
out.insert = t.insertToken('Kurum: .', 7, 7, 'kurumAdi');
out.insertReplace = t.insertToken('abcXYZdef', 3, 6, '{il}');
const placeholders = [{ key: 'kurumAdi', label: 'Kurum adı', example: 'BBD Kurs' },
  { key: '{kesintiOrani}', label: 'Kesinti oranı', example: '10' }];
out.unknown = t.unknownPlaceholders(c, placeholders);
out.preview = t.previewModel(c, placeholders);
out.valid = t.validateClauses(c);
out.invalid = t.validateClauses([{ title: 'Boş', items: [''] }, { title: '', items: [''] }]);
out.empty = t.validateClauses([]);
out.equal = [t.clausesEqual([{ title: ' A ', items: ['x', ''] }], [{ title: 'A', items: ['x'] }]),
  t.clausesEqual([{ title: 'A', items: ['x'] }], [{ title: 'A', items: ['y'] }])];
out.summary = t.changeSummary([{ title: 'A', items: ['x'] }], [{ title: 'A', items: ['x', 'y'] }]);
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    folder = tmp_path_factory.mktemp("sozlesme-belge")
    for name in ("documents-logic", "settings-logic", "templates-logic"):
        (folder / f"{name}.mjs").write_bytes((CONTRACTS_UI / f"{name}.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


@needs_node
def test_belge_cikar_istegi_secimden(result: dict[str, Any]) -> None:
    body = result["selection"]["body"]
    assert body["contractIds"] == ["c2", "c1"]                       # tekil, boş atılır, sıra korunur
    assert body["kinds"] == ["registration", "promissory", "payment_plan"]   # belge sırası, bilinmeyen atılır
    assert body["options"] == {"copies": {"registration": 3, "promissory": 1, "payment_plan": 2},
                               "promissoryScope": "open", "revision": "signed"}
    assert "filter" not in body and "studentIds" not in body
    assert result["noKinds"] == {"errors": ["En az bir belge türü seçin."]}
    assert result["noTarget"] == {"errors": ["Belge çıkarılacak sözleşmeleri seçin."]}
    filtered = result["filterTarget"]["body"]
    assert filtered["filter"] == {"classId": "k8", "overdueOnly": True}
    assert "promissoryScope" not in filtered["options"]            # senet seçili değilse gönderilmez
    assert filtered["options"]["copies"] == {"meb": 2}


@needs_node
def test_gecikenlere_yasal_uyari_tek_tik(result: dict[str, Any]) -> None:
    assert result["legal"] == {"filter": {"overdueOnly": True}, "kinds": ["legal_notice"],
                               "options": {"copies": {"legal_notice": 3}, "revision": "latest"}}
    assert result["legalDefault"]["options"]["copies"] == {"legal_notice": 1}


@needs_node
def test_suzgecin_tamami_akademi_suzgecine_ya_da_kimliklere(result: dict[str, Any]) -> None:
    filters = result["filters"]
    assert filters["plain"] == {}
    assert filters["akademi"] == {"classId": "k8", "signature": "awaiting", "overdueOnly": True}
    # Akademi'nin toplu süzgecinde olmayan koşul → null (liste sayfalanıp kimliğe çevrilir).
    assert filters["search"] is None and filters["year"] is None and filters["cancelled"] is None
    assert result["query"] == {"signature": "resign_required", "academicYear": "2026-2027", "q": "can"}
    assert result["queryCancelled"] == {"status": "cancelled", "overdueOnly": True}
    assert result["copies"] == {"registration": 3, "meb": 3, "promissory": 1, "payment_plan": 1, "legal_notice": 1}


@needs_node
def test_yanit_ve_atlananlar(result: dict[str, Any]) -> None:
    ready = result["outcomeReady"]
    assert ready["state"] == "ready" and ready["url"] == "https://x/f.pdf" and ready["included"] == 3
    assert ready["skipped"] == [{"studentId": "s9", "name": "Elif Yılmaz", "classGroup": "8-A",
                                 "code": "contract_missing_fields", "reason": "Eksik bilgi",
                                 "missing": ["Veli TC kimlik no", "Veli ev adresi"]}]
    assert result["skipLine"] == "Eksik bilgi: Veli TC kimlik no, Veli ev adresi"
    assert result["outcomeQueued"]["state"] == "queued" and result["outcomeQueued"]["jobId"] == "j1"
    assert result["outcomeEmpty"]["state"] == "empty"
    assert result["queue"] == [False, True]


@needs_node
def test_liste_izi_ve_etiketler(result: dict[str, Any]) -> None:
    assert result["fpSame"] is True and result["fpSigned"] is False
    assert result["years"] == ["2027-2028", "2026-2027", "2025-2026"]
    assert result["yearsSpring"] == ["2027-2028", "2026-2027"]
    assert result["numbers"] == ["BBD1-1000", "72128", "—"]
    assert result["labels"] == ["Öğrenci Kayıt Sözleşmesi", "MEB Kayıt Sözleşmesi", "Senetler", "Ödeme Planı",
                                "Yasal Uyarı"]


@needs_node
def test_iban_dogrulama(result: dict[str, Any]) -> None:
    iban = result["iban"]
    assert iban["ok"] is None and iban["okLower"] is None
    assert "sağlaması" in iban["badCheck"]
    assert "26 karakter" in iban["short"]
    assert '"TR"' in iban["foreign"]
    assert "yalnız rakam" in iban["letters"]
    assert iban["empty"] == "IBAN yazın."
    assert iban["format"] == "TR33 0006 1005 1978 6457 8413 26"


@needs_node
def test_sozlesme_ayarlari_yamasi(result: dict[str, Any]) -> None:
    assert result["numberPreview"] == ["BBD1-1000", "72", "X-?"]
    assert result["draftIban"] == "TR33 0006 1005 1978 6457 8413 26"
    assert result["draftCopies"] == {"registration": 2, "meb": 2, "promissory": 1, "payment_plan": 1, "legal_notice": 1}
    assert result["noChange"] is None
    patch = result["patch"]["contracts"]
    assert set(patch) == {"courtCity", "nextNumber", "bankAccounts"}      # yalnız değişen anahtarlar
    assert patch["courtCity"] == "İZMİR"                                   # Türkçe büyük harf
    assert patch["nextNumber"] == 1005
    assert patch["bankAccounts"][1] == {"bank": "Vakıf", "holder": "BBD", "branch": None,
                                        "iban": "TR330006100519786457841326"}
    assert result["errors"] == {"bank.1.iban": "Bu IBAN listede zaten var."}
    bad = result["badErrors"]
    assert set(bad) >= {"numberPrefix", "nextNumber", "registrationKinds", "signature.guardian", "bank.1.bank",
                        "bank.1.holder", "bank.1.iban"}
    assert result["upper"] == "İSTANBUL ILGIN"
    assert result["dirty"] == 3


@needs_node
def test_madde_sablonu_modeli(result: dict[str, Any]) -> None:
    assert result["cloned"] == [{"title": "Taraflar", "items": ["a) {kurumAdi} ile veli"]},
                                {"title": "", "items": [""]}]
    assert result["moved"] == [
        {"title": "Ücret", "items": ["Ücret {tutar} TL, kesinti %{kesintiOrani}"]},
        {"title": "Taraflar", "items": ["a) {kurumAdi} ile veli"]},
    ]                                                                      # boş öğe ve boş madde atılır
    assert result["moveOut"][0] == "Ücret"                                 # sınır dışına taşıma değiştirmez
    assert result["removedItem"] == [{"title": "X", "items": [""]}]
    assert result["movedItem"] == ["3", "1", "2"]
    assert result["insert"] == {"text": "Kurum: {kurumAdi}.", "caret": 17}
    assert result["insertReplace"] == {"text": "abc{il}def", "caret": 7}
    assert result["unknown"] == ["tutar"]
    assert result["preview"][0] == {"no": 1, "title": "ÜCRET", "items": ["Ücret {tutar} TL, kesinti %10"]}
    assert result["preview"][1]["items"] == ["a) BBD Kurs ile veli"]
    assert result["valid"] == []
    assert result["invalid"] == ["1. maddede metin yok (başlığın altına en az bir öğe yazın)."]
    assert result["empty"] == ["Şablonda en az bir madde olmalı."]
    assert result["equal"] == [True, False]
    assert result["summary"] == "1 madde, 2 öğe (önce 1 madde, 1 öğe)"


# --------------------------------------------------------------- ekran (DOM taklidi)

UI = r"""
const routes = {};
const sent = [];
const imported = [];
globalThis.__apiHook = (method, path, options) => {
  if (path === '/api/bbd_akademi/km/documents/akademi') {
    imported.push(options.body);
    return { ok: true, path: '/rapor/sozlesme.pdf', name: 'sozlesme.pdf', bytes: 10, number: '', parts: 1,
      pages: [], pageCount: 3, shown: 0, previewError: '' };
  }
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: false, status: 404, error: { code: 'not_found', message: `taklit yok: ${key}` } };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  if (value && value.__error) return { ok: false, status: value.status || 422, error: value.__error };
  return { ok: true, status: 200, data: value, headers: {} };
};
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const top = (root, selector) => root.querySelectorAll(selector).pop();
const student = (id, name) => ({ id, displayName: name, classGroup: { id: 'k8', name: '8-A' }, externalRef: `STU-${id}` });
const contract = (id, no, name, state) => ({ id, number: no, student: student(`s${id}`, name),
  payerGuardian: { slot: 1, name: 'Ayşe Yılmaz' }, academicYear: '2026-2027', contractDate: '2026-10-28',
  registeredOn: '2026-10-28', status: 'active', signature: { state, signedRevisionNo: null, signedOn: null, source: null },
  latestRevisionNo: 1, totalMinor: 18000000, planId: 'p1', createdAt: '2026-10-28T10:00:00Z' });

export default async function ({ document, audit, sleep, api, load }) {
  let listCalls = 0;
  routes['GET /v1/admin/contracts'] = () => { listCalls += 1; return { items: [
    contract('1', 'BBD1-1000', 'Elif Yılmaz', 'awaiting'), contract('2', '72128', 'Can Demir', 'signed')],
    nextCursor: null }; };
  routes['GET /v1/admin/contracts/missing'] = { items: [{ student: student('s3', 'Deniz Kaya'), planId: null }],
    nextCursor: null };
  routes['GET /v1/admin/settings'] = { contracts: { numberPrefix: 'BBD1-', nextNumber: 1000,
    defaultCopies: { registration: 2, meb: 2, promissory: 1, payment_plan: 1, legal_notice: 1 }, bankAccounts: [] } };
  routes['PATCH /v1/admin/settings'] = (body) => ({ contracts: { ...body.contracts } });
  routes['POST /v1/admin/contracts/documents'] = (body) => ({ status: 'ready', jobId: null, includedCount: 1,
    file: { url: 'https://akademi.test/f/x.pdf', expiresAt: '2026-10-28T11:00:00Z', fileName: 'sozlesmeler.pdf' },
    skipped: [{ student: student('s2', 'Can Demir'), code: 'contract_missing_fields',
      missingFields: [{ scope: 'guardian', slot: 1, field: 'nationalId', label: 'Veli TC kimlik no', blocks: ['promissory'] }] }] });
  routes['GET /v1/admin/contracts/services'] = { items: [] };
  routes['POST /v1/admin/contracts/services'] = (body) => ({ id: 'sv1', ...body });
  routes['GET /v1/admin/contracts/templates'] = { items: [], placeholders: [
    { key: 'kurumAdi', label: 'Kurum adı', example: 'BBD Kurs' }] };
  routes['GET /v1/admin/contracts/templates/registration'] = { kind: 'registration',
    active: { version: 3, clauses: [{ title: 'Taraflar', items: ['a) {kurumAdi} ile veli arasında'] }],
      createdAt: '2026-10-28T09:00:00Z', createdByLabel: 'Yönetici', note: null }, history: [] };
  routes['POST /v1/admin/contracts/templates/registration'] = (body) => ({ kind: 'registration', active: { version: 4 } });

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const gone = [];
  const app = { root, api: client, status: null, toast: (text, tone) => { toasts.push([text, tone]); },
    has: () => true, can: (id) => client.can(id), go: (key, payload) => gone.push([key, payload]), openPanel: () => {},
    capability: () => null, feature: () => false, enabled: () => true, onDispose: () => {}, canOpen: () => true,
    lookups: { classGroups: async () => [{ id: 'k8', name: '8-A' }] } };
  const { section } = await load('./panels/bbd_akademi/contracts/documents-screen.js');
  const view = section(app);
  root.append(view.node);
  view.shown();
  await sleep(80);
  const out = {};
  out.tabs = root.querySelectorAll('[role="tab"]').map((tab) => tab.textContent.trim());
  out.numbers = root.querySelectorAll('.ak-szb-no').map((node) => node.textContent);
  out.a11yList = audit(view.node);

  // Süzgeç çipi → sorgu.
  click(byText(root, 'button', 'İmza bekleyen'));
  await sleep(30);
  out.filterQuery = sent.filter((c) => c.path === '/v1/admin/contracts').pop().query;
  click(byText(root, 'button', 'Tümü'));
  await sleep(30);

  // İki satır seç → Belge çıkar → MEB'i kaldır, senet yalnız ödenmemiş → hazırla.
  for (const box of root.querySelectorAll('.kit-row .kit-check')) click(box);
  await sleep(10);
  out.selectionText = root.querySelector('.ak-szb-selection-text').textContent;
  click(byText(root, 'button', 'Belge çıkar'));
  await sleep(40);
  let dialog = top(root, '.kit-dialog');
  out.a11yBuild = audit(dialog);
  const mebRow = dialog.querySelectorAll('.ak-szb-kind').find((row) => row.dataset.kind === 'meb');
  click(mebRow.querySelector('input'));
  const scope = dialog.querySelectorAll('select').find((node) => node.querySelector('option')?.value === 'all');
  scope.value = 'open';
  scope.dispatchEvent(new Event('change', { bubbles: true }));
  click(byText(dialog, 'button', 'Belgeleri hazırla'));
  await sleep(80);
  const request = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/contracts/documents').pop();
  out.request = request?.body;
  out.requestKey = Boolean(request?.idempotencyKey);
  dialog = top(root, '.kit-dialog');
  out.skippedTitle = dialog?.querySelector('.kit-dialog-title')?.textContent;
  out.skippedText = dialog?.querySelector('.ak-szb-skip')?.textContent;
  click(byText(dialog, 'button', 'Önizle ve yazdır (1)'));
  await sleep(80);
  out.imported = imported;

  // Sözleşme değişti olayı → liste tazelenir.
  const before = listCalls;
  window.dispatchEvent(new CustomEvent('bbd-akademi:contract-changed', { detail: { contractId: '1', change: 'signed' } }));
  await sleep(40);
  out.reloaded = listCalls > before;

  // Sözleşmesi olmayanlar → sihirbaz olayı.
  const wizard = [];
  window.addEventListener('bbd-akademi:open-contract-wizard', (event) => wizard.push(event.detail));
  click(byText(root, '[role="tab"]', 'Sözleşmesi olmayanlar'));
  await sleep(60);
  click(byText(root, 'button', 'Kayıt ve ödeme planı'));
  out.wizard = wizard;

  // Ayarlar: hatalı IBAN kaydedilmez; düzeltilince yalnız değişenler gider.
  click(byText(root, '[role="tab"]', 'Sözleşme ayarları'));
  await sleep(80);
  out.nextPreview = root.querySelector('.ak-szb-preview-no')?.textContent;
  click(byText(root, 'button', 'Hesap ekle'));
  const bank = root.querySelector('.ak-szb-bank');
  const inputs = bank.querySelectorAll('input');
  type(inputs[0], 'Ziraat Bankası');
  type(inputs[1], 'BBD Eğitim');
  type(inputs[3], 'TR34 0006 1005 1978 6457 8413 26');
  click(byText(root, 'button', 'Kaydet'));
  await sleep(30);
  out.ibanError = bank.querySelectorAll('.kit-field-error').map((node) => node.textContent).filter(Boolean);
  out.patchesBefore = sent.filter((c) => c.method === 'PATCH').length;
  type(inputs[3], 'TR33 0006 1005 1978 6457 8413 26');
  click(byText(root, 'button', 'Kaydet'));
  await sleep(60);
  out.patch = sent.filter((c) => c.method === 'PATCH').map((c) => c.body);

  // Şablon: madde ekle → kaydet (notlu yeni sürüm).
  click(byText(root, '[role="tab"]', 'Madde şablonları'));
  await sleep(80);
  out.templateHead = root.querySelector('.ak-szb-templates .ak-muted')?.textContent;
  click(byText(root, 'button', 'Madde ekle'));
  const clauses = root.querySelectorAll('.ak-szb-clause');
  const last = clauses[clauses.length - 1];
  type(last.querySelector('input'), 'Ücret');
  const area = last.querySelector('textarea');
  area.dispatchEvent(new Event('focus'));
  type(area, 'Ücret peşin ödenir.');
  out.preview = root.querySelector('.ak-szb-paper')?.textContent;
  click(byText(root, 'button', 'Yeni sürüm olarak kaydet'));
  await sleep(30);
  dialog = top(root, '.kit-dialog');
  type(dialog.querySelector('input'), 'Ücret maddesi eklendi');
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.template = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/contracts/templates/registration')
    .map((c) => c.body);
  out.toasts = toasts;
  out.gone = gone;
  return out;
}
"""


def test_ekran_belge_cikar_sihirbaz_ayar_sablon() -> None:
    import sys

    sys.path.insert(0, str(MODULE.parents[1] / "tools" / "a11y"))
    from harness import NodeMissing, run

    try:
        data = run(UI, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    assert out["tabs"] == ["Sözleşmeler", "Sözleşmesi olmayanlar", "Hizmet kataloğu", "Madde şablonları",
                           "Sözleşme ayarları"]
    assert out["numbers"] == ["BBD1-1000", "72128"]
    assert out["filterQuery"]["signature"] == "awaiting"
    assert out["selectionText"] == "2 sözleşme seçili"
    assert out["request"] == {"contractIds": ["1", "2"], "kinds": ["registration", "promissory", "payment_plan"],
                              "options": {"copies": {"registration": 2, "promissory": 1, "payment_plan": 1},
                                          "promissoryScope": "open", "revision": "latest"}}
    assert out["requestKey"] is True
    assert out["skippedTitle"] == "Sözleşme belgeleri hazır"
    assert "Can Demir" in out["skippedText"] and "Veli TC kimlik no" in out["skippedText"]
    assert out["imported"] == [{"kind": "contract_documents", "url": "https://akademi.test/f/x.pdf",
                                "name": "sozlesmeler"}]
    assert out["reloaded"] is True
    assert out["wizard"] == [{"studentId": "s3", "planId": None}]
    assert out["nextPreview"] == "Sonraki: BBD1-1000"
    assert out["ibanError"] == ["IBAN sağlaması tutmuyor — rakamları kontrol edin."] and out["patchesBefore"] == 0
    assert out["patch"] == [{"contracts": {"bankAccounts": [{"bank": "Ziraat Bankası", "holder": "BBD Eğitim",
                                                             "branch": None, "iban": "TR330006100519786457841326"}]}}]
    assert out["templateHead"].startswith("Etkin sürüm 3")
    assert "2. ÜCRET:" in out["preview"] and "a) BBD Kurs ile veli arasında" in out["preview"]
    assert out["template"] == [{"clauses": [{"title": "Taraflar", "items": ["a) {kurumAdi} ile veli arasında"]},
                                            {"title": "Ücret", "items": ["Ücret peşin ödenir."]}],
                                "note": "Ücret maddesi eklendi"}]
    for key in ("a11yList", "a11yBuild"):
        assert out[key] == [], (key, out[key])
