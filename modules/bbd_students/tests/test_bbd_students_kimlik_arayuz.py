"""Kimlik ekran modeli (`ui/panel/identity-model.js`) — Node ile; kural backend'le AYNI.

TC sağlaması, maske, telefon, tarih ve yalnız değişen alanların gövdesi. `node` yoksa
ATLANIR. Değerler uydurmadır.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend import identity as rules

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
IDS = ["10000000146", "10000000078", "20000000046", "12345678950", "10000000147",
       "01234567890", "1000000014", "1000000014a", "99999999999", "19999999990"]
PHONES = ["0332 222 33 44", "+90 532 111 22 33", "3322223344", "0800 123", "1322223344", ""]

SCRIPT = r"""
import * as m from './identity-model.mjs';
const ids = %s;
const phones = %s;
const server = { student: { nationalId: '10000000146', birthDate: '2009-05-01', homePhone: '3322223344',
  address: { line: 'A', district: 'Selçuklu', city: 'Konya' } },
  guardians: [{ slot: 1, phone: '5321112233', nationalId: null, email: null }] };
const form = m.toForm(server);
const same = m.diffBody(server, form);
form.student.birthDate = '02.05.2009';
form.guardians[0].nationalId = '10000000078';
form.guardians[0].email = 'Ayse@Ornek.com';
const out = {
  valid: ids.map((v) => m.validNationalId(v)),
  problem: m.nationalIdProblem('01234567890'),
  mask: m.maskNationalId('10000000146'),
  phones: phones.map((p) => m.cleanAnyPhone(p)),
  show: m.showAnyPhone('3322223344'),
  days: ['01.05.2009', '2009-05-01', '31.02.2009', 'x', ''].map((d) => m.isoDay(d)),
  same,
  diff: m.diffBody(server, form),
  problems: m.problems({ student: { nationalId: '10000000147', birthDate: '01.01.2999' },
    guardians: [{ slot: 2, email: 'x@', workPhone: '12' }] }, '2026-10-28').map((p) => p.key),
  missing: m.missingLine([{ label: 'Öğrenci TC kimlik no' }]),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    folder = tmp_path_factory.mktemp("identity")
    (folder / "identity-model.mjs").write_bytes((PANEL / "identity-model.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT % (json.dumps(IDS), json.dumps(PHONES)),
                                    encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                          check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


def test_tc_saglamasi_backend_ile_ayni(result: dict[str, Any]) -> None:
    assert result["valid"] == [rules.valid_national_id(value) for value in IDS]
    assert result["valid"][:4] == [True, True, True, True]
    assert result["problem"] == rules.national_id_problem("01234567890")
    assert result["mask"] == "*******0146"


def test_telefon_ve_tarih(result: dict[str, Any]) -> None:
    assert result["phones"] == [rules.any_phone(phone) for phone in PHONES]
    assert result["show"] == "0332 222 33 44"
    assert result["days"] == ["2009-05-01", "2009-05-01", None, None, ""]


def test_yalniz_degisen_alanlar_gonderilir(result: dict[str, Any]) -> None:
    assert result["same"] is None
    assert result["diff"] == {
        "student": {"birthDate": "2009-05-02"},
        "guardians": [{"slot": 1, "phone": "5321112233", "nationalId": "10000000078",
                       "email": "ayse@ornek.com"}],
    }
    assert result["problems"] == ["student.nationalId", "student.birthDate", "g2.workPhone",
                                  "g2.email"]
    assert result["missing"] == "Sözleşme için eksik: Öğrenci TC kimlik no."
