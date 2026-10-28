"""Deneme Kulübü (iş 81-km-kulup) — ekranın ve sihirbaz türünün SAF mantığı (Node).

* `club-logic.js`: kurumda kayıt gövdesi (source branch, telefon 10 hane, yöntem nakit/POS/havale), düğme kuralları
  (yeniden gönder / iptal / yeniden dene), kulüp ayarları yaması (yalnız değişen `club.*`), SMS {link} ≤ 160.
* `wizard-logic.js`: tür seçimi → tek satır "DENEME KULÜBÜ — paket" (birim deneme × sayı), senet yok, gövdede
  `type` + `examClub` (kayıt sözleşmesinde `type` gönderilmez), tekillik TÜR başına, kulüp belge seti.

`node` yoksa ATLANIR. Kişiler UYDURMA.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel" / "contracts"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")

SCRIPT = r"""
import * as c from './club-logic.js';
import * as w from './wizard-logic.js';
const out = {};
out.body = c.branchInviteBody({ name: ' Deniz Örnek ', phone: '0532 111 22 33', paymentMethod: 'card',
  amountMinor: 2000000, paid: true, send: false });
out.bodyBadMethod = c.branchInviteBody({ phone: '5321112233', paymentMethod: 'store', amountMinor: 0 }).paymentMethod;
out.errors = Object.keys(c.branchInviteErrors({ phone: '123', paymentMethod: 'store', amountMinor: null }));
out.actions = Object.fromEntries(['sent', 'opened', 'processing', 'completed', 'failed', 'expired', 'cancelled']
  .map((status) => [status, c.inviteActions({ status })]));
out.query = c.inviteQuery({ status: 'failed', source: '', q: '  1001 ' });
const original = { className: 'Deneme Kulübü', smsText: 'Kayıt: {link}', autoInviteFromStore: true, priceMinor: 2000000 };
const draft = { ...c.clubSettings({ club: original }), priceMinor: 2500000, autoInviteFromStore: false };
out.patch = c.settingsPatch(original, draft);
out.noPatch = c.settingsPatch(original, c.clubSettings({ club: original }));
out.smsErrors = c.settingsErrors({ ...draft, smsText: 'link yok', inviteValidDays: 7 });
out.flat = c.clubSettings({ 'club.className': 'Kulüp A' }).className;

const party = { party: { student: { fullName: 'Deniz Örnek' }, guardians: [{ slot: 1, fullName: 'Ayla Örnek' }] },
  missingFields: [] };
const st = w.initialState({ source: 'existing', today: '2026-10-28', party, services: [] });
st.payment.method = 'promissory';
const settings = { club: { packageName: 'TYT Kulübü', examType: 'TYT', examCount: 20, seasonStart: '2026-11-01',
  seasonEnd: '2027-06-01', priceMinor: 2000000 } };
w.setContractType(st, 'exam_club', { settings });
out.clubLine = st.lines[0];
out.clubMethod = st.payment.method;
out.promissory = w.hasPromissory(st);
const ready = w.readiness(st, { today: '2026-10-28', existing: [
  { id: 'k1', status: 'active', academicYear: st.academicYear, type: 'registration', number: 'BBD1-1000' }] });
out.clubBody = { type: ready.body.type, examClub: ready.body.examClub, prom: 'promissoryMode' in ready.body };
out.clubErrors = ready.errors.map((item) => item.field);
w.setClub(st, { examCount: 7 });
out.oddLine = [st.lines[0].quantity, st.lines[0].unitPriceMinor];
out.sameClub = Boolean(w.sameYearContract([{ status: 'active', academicYear: '2026-2027', type: 'exam_club' }],
  '2026-2027', 'exam_club'));
out.docs = w.documentsBody('c1', undefined, 'exam_club').kinds;
w.setContractType(st, 'registration');
out.backType = { type: 'type' in w.buildDraft(st), name: st.lines[0].name };
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    folder = tmp_path_factory.mktemp("kulup_ekrani")
    (folder / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    for name in ("club-logic.js", "wizard-logic.js", "shared-contract-logic.js"):
        (folder / name).write_bytes((PANEL / name).read_bytes())
    (folder / "run.js").write_text(SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.js")], capture_output=True, text=True, check=False,
                          timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


def test_kurumda_kayit_govdesi(result: dict[str, Any]) -> None:
    assert result["body"] == {"phone": "5321112233", "name": "Deniz Örnek", "source": "branch",
                              "amountMinor": 2000000, "paymentMethod": "card", "paid": True, "send": False}
    assert result["bodyBadMethod"] == "cash"          # kurumda BBDSTORE seçilmez
    assert set(result["errors"]) == {"phone", "paymentMethod", "amountMinor"}


def test_dugme_kurallari(result: dict[str, Any]) -> None:
    actions = result["actions"]
    assert [s for s, a in actions.items() if a["resend"]] == ["sent", "opened", "expired"]
    assert [s for s, a in actions.items() if a["retry"]] == ["failed"]
    assert not actions["processing"]["cancel"] and not actions["completed"]["cancel"]
    assert actions["sent"]["cancel"] and actions["failed"]["cancel"]
    assert result["query"] == {"status": "failed", "q": "1001"}


def test_kulup_ayarlari_yamasi(result: dict[str, Any]) -> None:
    assert result["patch"] == {"club": {"priceMinor": 2500000, "autoInviteFromStore": False}}
    assert result["noPatch"] is None
    assert "smsText" in result["smsErrors"]
    assert result["flat"] == "Kulüp A"


def test_sihirbaz_kulup_turu(result: dict[str, Any]) -> None:
    line = result["clubLine"]
    assert line["name"] == "DENEME KULÜBÜ — TYT Kulübü" and line["unit"] == "deneme"
    assert (line["quantity"], line["unitPriceMinor"]) == ("20", 100000)   # 20 × 1.000 TL
    assert result["clubMethod"] == "cash" and result["promissory"] is False
    assert result["clubBody"] == {"type": "exam_club", "examClub": {
        "packageName": "TYT Kulübü", "examType": "TYT", "examCount": 20, "seasonStart": "2026-11-01",
        "seasonEnd": "2027-06-01", "channel": "in_person"}, "prom": False}
    # Aynı yılda KAYIT sözleşmesi var ama tekillik tür başına: kulüp sözleşmesi engellenmez.
    assert "academicYear" not in result["clubErrors"]
    assert result["oddLine"] == ["1", 2000000]                       # tam bölünmüyor → miktar 1 + tutar
    assert result["sameClub"] is True
    assert result["docs"] == ["exam_club", "exam_club_consent", "payment_plan"]
    assert result["backType"] == {"type": False, "name": ""}
