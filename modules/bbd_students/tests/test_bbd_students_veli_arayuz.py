"""Veli listesi ekran modeli (`ui/panel/guardian-model.js`) — Node ile.

Dosya bilerek import'suz ve DOM'suzdur: `.mjs` kopyası Node'da yüklenir,
işlevler çağrılır, sonuç JSON olarak buraya döner. Kural backend'le
(`backend/guardians.py`) AYNI olmalı; ikisi burada aynı girdilerle sınanır.
`node` yoksa ATLANIR.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from bbd_students_backend.guardians import GuardianError, clean_items
from bbd_students_backend.phones import normalize_phone

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

PHONES = ["5321112233", "05321112233", "+90 532 111 22 33", "0090 (532) 111-22-33",
          "90 532 1112233", "0212 555 11 22", "532111223", "", "abc", "053211122334"]

SCRIPT = r"""
import * as g from './guardian-model.mjs';
const phones = %s;
const rows = [
  { name: 'Ayşe', phone: '0532 111 22 33', relation: 'mother' },
  { name: 'Mehmet', phone: '+90 505 444 55 66', relation: 'father' },
  { name: 'Fatma', phone: '5427778899', relation: '' },
];
const out = {
  clean: phones.map((p) => g.cleanPhone(p)),
  show: g.showPhone('05321112233'),
  showBad: g.showPhone('0212 555'),
  valid: g.validate(rows),
  dup: g.validate([rows[0], { name: 'x', phone: '5321112233' }]),
  empty: g.validate([{ name: 'x', phone: '' }]),
  bad: g.validate([{ name: 'x', phone: '0212 555 11 22' }]),
  tooMany: g.validate([...rows, rows[0], rows[1]]).filter((e) => e.index === null).length,
  payload: g.toPayload(rows),
  primary: g.makePrimary(rows, 2).map((r) => r.name),
  primaryNoop: g.makePrimary(rows, 0).map((r) => r.name),
  removed: g.remove(rows, 0).map((r) => r.name),
  added: g.add(rows).length,
  addedFull: g.add([...rows, rows[0]]).length,
  changedSame: g.changed(rows, rows.map((r) => ({ ...r, phone: g.cleanPhone(r.phone) }))),
  changedOrder: g.changed(rows, g.makePrimary(rows, 1)),
  note: g.primaryNote(rows, g.makePrimary(rows, 1)),
  noteNone: g.primaryNote(rows, rows),
  noteGone: g.primaryNote(rows, []),
  server: g.fromServer([{ slot: 2, name: 'B', phone: '5054445566', relation: 'father' },
    { slot: 1, name: 'A', phone: '5321112233', relation: 'mother', siblings: [{ id: 'STU-2' }] }]),
  label: g.relationLabel('grandparent'),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    folder = tmp_path_factory.mktemp("guardians")
    (folder / "guardian-model.mjs").write_bytes((PANEL / "guardian-model.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT % json.dumps(PHONES), encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                          check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


def test_telefon_kurali_backend_ile_ayni(result: dict[str, Any]) -> None:
    assert result["clean"] == [normalize_phone(phone) for phone in PHONES]
    assert result["show"] == "+90 532 111 22 33"
    assert result["showBad"] == "0212 555"


def test_dogrulama_backend_ile_ayni(result: dict[str, Any]) -> None:
    assert result["valid"] == []
    assert [item["message"] for item in result["dup"]] == ["2. velinin telefonu 1. veliyle aynı."]
    assert result["empty"][0]["field"] == "phone" and "boş" in result["empty"][0]["message"]
    assert "geçersiz" in result["bad"][0]["message"]
    assert result["tooMany"] == 1
    # Ekranın gönderdiği gövdeyi backend aynen kabul eder.
    assert clean_items(result["payload"]["items"]) == [
        {"name": "Ayşe", "phone": "5321112233", "relation": "mother"},
        {"name": "Mehmet", "phone": "5054445566", "relation": "father"},
        {"name": "Fatma", "phone": "5427778899", "relation": ""}]
    for case in ("dup", "empty", "bad"):
        assert result[case], case
    with pytest.raises(GuardianError):
        clean_items([{"phone": "0212 555 11 22"}])


def test_liste_islemleri(result: dict[str, Any]) -> None:
    assert result["primary"] == ["Fatma", "Ayşe", "Mehmet"]
    assert result["primaryNoop"] == ["Ayşe", "Mehmet", "Fatma"]
    assert result["removed"] == ["Mehmet", "Fatma"]
    assert result["added"] == 4 and result["addedFull"] == 4      # en çok 4
    assert result["changedSame"] is False                          # yalnız yazım farkı → değişiklik yok
    assert result["changedOrder"] is True                          # birincil değişti
    assert "Birincil veli değişti" in result["note"]
    assert result["noteNone"] == ""
    assert "boşalır" in result["noteGone"]
    assert [row["name"] for row in result["server"]] == ["A", "B"]  # yuva sırası
    assert result["server"][0]["siblings"] == [{"id": "STU-2"}]
    assert result["label"] == "Büyükanne / büyükbaba"
