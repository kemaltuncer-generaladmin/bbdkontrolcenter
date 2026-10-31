"""Koç görevinde "Takvimden seç" (iş 83, Akademi README §14 madde 145) — saf mantık, Node'da.

Seçilebilir aralık 1 ay geri – 1 yıl ileri (ay sonuna kırpılır); hafta dışı tarih o tarihin pazartesisine
(`weekStart`) kaydedilir; metinler web (`weekPick.ts`) ile aynı. Finans kilidinin rozet / kod mantığı da burada.
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
  const m = await load('./panels/bbd_akademi/shared/task-form-logic.js');
  const f = await load('./panels/bbd_akademi/finance/finance-lock-logic.js');
  const now = Date.parse('2026-10-31T12:00:00Z');
  return {
    range: m.pickRange('2026-03-31'),
    rangeLeap: m.pickRange('2027-03-31'),
    inside: [m.inPickRange('2026-09-30', '2026-10-31'), m.inPickRange('2026-09-29', '2026-10-31'),
      m.inPickRange('2027-10-31', '2026-10-31'), m.inPickRange('2027-11-01', '2026-10-31')],
    monday: [m.weekStartOf('2026-11-01'), m.weekStartOf('2026-11-02'), m.weekStartOf('2027-01-01')],
    outside: [m.outsideWeek('2026-11-08', '2026-11-02'), m.outsideWeek('2026-11-09', '2026-11-02')],
    added: m.addedToText('2026-11-02'),
    weekOf: m.weekOfText('2027-01-04'),
    badge: [f.badgeText('2026-10-31T12:28:00Z', now), f.badgeText('2026-10-31T11:59:00Z', now)],
    code: [f.cleanCode(' 12 34-56 7'), f.isFinanceLocked({ code: 'finance_access_required' })],
    sent: f.sentText([{ name: 'Finans Yöneticisi', maskedPhone: '5** *** ** 12' }]),
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


def test_takvimden_sec_ve_kilit_mantigi() -> None:
    out = _run(LOGIC)
    assert out["range"] == {"min": "2026-02-28", "max": "2027-03-31"}
    assert out["rangeLeap"] == {"min": "2027-02-28", "max": "2028-03-31"}
    assert out["inside"] == [True, False, True, False]
    assert out["monday"] == ["2026-10-26", "2026-11-02", "2026-12-28"]
    assert out["outside"] == [False, True]
    assert out["added"] == "Görev 2 Kasım haftasına eklendi"
    assert out["weekOf"] == "4 Ocak haftası"
    assert out["badge"] == ["Finans açık · 28 dk", "Kurum finansı kilitli"]
    assert out["code"] == ["123456", True]
    assert out["sent"] == "Kod Finans Yöneticisi (5** *** ** 12) numarasına gönderildi. Kodu ondan alıp girin."
