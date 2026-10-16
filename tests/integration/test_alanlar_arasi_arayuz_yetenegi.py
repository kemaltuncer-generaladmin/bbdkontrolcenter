"""Alanlar arası SALT OKUMA arayüz yeteneği üzerinden (ADR 0033 K12, Aşama 2).

BBD Akademi paneli eskiden `/api/bbd_students/groups…` ve `/api/bell/…` uçlarını
doğrudan çağırıyordu. Artık adresler sağlayan modülün panelindeki yetenektedir
(`bbd_students.groups_read`, `bell.status`) ve Akademi `app.kmRead(ad, yöntem, …)`
ile okur. Bu test davranışın aynı kaldığını sabitler: yetenek AYNI adrese,
AYNI biçimde gider; manifestte ilan edilmiştir (kabuk ilan edilmeyeni atlar).
`node` yoksa Node kısmı atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

SCENARIO = r"""
export default async function ({ load }) {
  const calls = [];
  const shell = { api: async (path) => { calls.push(path); return { ok: true }; },
                  capability: () => null, open: () => {} };
  const students = (await load('./panels/bbd_students/index.js')).capabilities(shell);
  const bell = (await load('./panels/bell/index.js')).capabilities(shell);
  await students['bbd_students.groups_read'].list({ archived: 'all' });
  await students['bbd_students.groups_read'].rollover();
  await bell['bell.status'].state();
  await bell['bell.status'].planStatus({ from: '2026-10-16', to: '2026-10-22' });
  return { calls };
}
"""


def test_yetenekler_eski_adreslere_gider() -> None:
    try:
        data = run(SCENARIO, panels=("bbd_students", "bell"))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    assert result["calls"] == [
        "/api/bbd_students/groups?archived=all",
        "/api/bbd_students/groups/rollover",
        "/api/bell/state",
        "/api/bell/plan/status?from=2026-10-16&to=2026-10-22",
    ]


def test_yetenekler_manifestte_ilanli() -> None:
    def provides(module: str) -> set[str]:
        manifest = yaml.safe_load((ROOT / "modules" / module / "module.yaml").read_text(encoding="utf-8"))
        return {entry["capability"] for entry in manifest.get("provides") or []}

    assert "bbd_students.groups_read" in provides("bbd_students")
    assert "bell.status" in provides("bell")


def test_akademi_paneli_baska_modulun_ucunu_cagirmaz() -> None:
    panel = ROOT / "modules" / "bbd_akademi" / "ui" / "panel"
    offenders = [str(path.relative_to(ROOT)) for path in panel.rglob("*.js")
                 if "kmGet" in path.read_text(encoding="utf-8")]
    assert offenders == [], f"genel KM GET kanalı geri gelmiş: {offenders}"
