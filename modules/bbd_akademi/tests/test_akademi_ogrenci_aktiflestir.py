"""Akademi öğrenci kartında pasif öğrenciyi AKTİFLEŞTİR (16.10.2026 kullanıcı: "KM de pasife alınan
öğrenci aktifleştirilebilsin").

Eskiden pasif öğrencinin başlığı "Yeniden etkinleştirme KM Öğrenciler modülünden yapılır" diyordu; o
ekran menüde yok (Öğrenci yönetimi Akademi'de). Şimdi başlıkta "Aktifleştir" düğmesi KM Öğrenci
Yönetimi'nin `bbd_students.reactivate` yeteneğini çağırır (izin `bbd_students.lifecycle`, onay
penceresi orada); sonuç bildirilir ve kart Akademi olayı gelince tazelenir. İzin yoksa düğme çizilmez.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

SCENARIO = r"""
export default async function ({ document, audit, sleep, load }) {
  const { renderHeader } = await load('./panels/bbd_akademi/students/students-detail.js');
  const out = { runs: [], toasts: [], refreshed: 0 };
  const make = (allowed) => ({
    async allowed() { return allowed; },
    async run(ref, options) { out.runs.push([ref, options.name]); return { status: 'active', message: 'Aktifleştirildi — 9-A sınıfına geri eklendi.' }; },
  });
  const data = { student: { id: 's1', externalRef: 'STU-1', displayName: 'Elif Kaya', status: 'inactive', gradeLevel: 9,
    classGroup: null }, statusReason: 'left', statusSince: '2026-10-12T09:00:00Z', phoneIssues: [] };
  const draw = async (allowed) => {
    const root = document.createElement('div');
    document.body.append(root);
    const app = { root, toast: (text, tone) => out.toasts.push([text, tone]), go: () => {}, openPanel: () => {},
      capability: (name) => (name === 'bbd_students.reactivate' ? make(allowed) : null), has: () => true,
      can: () => true, enabled: () => true, api: { can: () => true } };
    const ctx = { app, recordable: true, refreshSoon: () => { out.refreshed += 1; } };
    const nodes = renderHeader(ctx, data);
    root.append(...[].concat(nodes));
    await sleep(10);
    return root;
  };
  const root = await draw(true);
  const box = root.querySelector('.ak-stu-inactive');
  out.text = box?.textContent || '';
  const button = box.querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir');
  out.visible = Boolean(button) && !button.hidden;
  out.violations = audit(box);
  button.click();
  await sleep(20);
  const denied = await draw(false);
  const other = denied.querySelector('.ak-stu-inactive').querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir');
  out.deniedHidden = Boolean(other) && other.hidden;
  return out;
}
"""


def test_pasif_ogrenci_basligindan_aktiflestir() -> None:
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    assert "Pasif öğrenci" in out["text"] and "KM Öğrenciler modülünden" not in out["text"]
    assert out["visible"] is True and out["violations"] == []
    assert out["runs"] == [["STU-1", "Elif Kaya"]]
    assert out["toasts"] == [["Aktifleştirildi — 9-A sınıfına geri eklendi.", "good"]]
    assert out["refreshed"] == 1
    assert out["deniedHidden"] is True
