"""Erişilebilirlik (WCAG 2.2 AA) — Zil Sistemi ekranı (0.5.3).

Üç sekme (Program, Çağrı, Ayarlar) Node'da küçük bir DOM üzerinde açılır ve axe
benzeri denetimden geçer (`tools/a11y/audit.mjs`). Renk karşıtlığı
`tests/integration/test_erisilebilirlik_karsitlik.py`'de. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run  # noqa: E402

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.__apiHook = (method, path) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path.startsWith('/api/bell/state')) return globalThis.__state;
    if (path === '/auth/me') return { permissions: ['bell.view', 'bell.manage', 'bell.ring', 'bell.call'] };
    if (method === 'GET') return {};
    return { ok: true };
  };
  const day = (kind) => [{ time: '08:30', kind: 'ders', label: '1. ders', sound: 'zil.mp3' }, { time: '09:10', kind: 'teneffus', label: 'Teneffüs', sound: 'zil.mp3' }];
  globalThis.__state = {
    settings: { enabled: true, groupsMigratedAt: '2026-10-10T10:00:00Z', voice: 'tr', callTemplate: '{ad}', afterDers: '', afterTeneffus: '', volume: 80, texts: { lessonAfter: '', breakAfter: '' }, bellSound: 'zil' },
    times: { mon: day(), tue: day(), wed: day(), thu: day(), fri: day(), sat: [], sun: [] },
    groups: [], sounds: [{ name: 'zil.mp3', seconds: 3 }], audio: { ready: true }, scheduler: { running: true },
    agent: { online: true, lastSeenAt: '2026-10-13T08:00:00Z' }, afterTexts: {}, queue: { pending: 0 }, log: [],
    canRing: true, callableGroups: [{ id: 'G1', name: '12 Sayısal', kind: 'group', callName: '', voice: { state: 'ready' } }],
    profiles: [], plan: { today: { date: '2026-10-13', items: [] } }, missing: [],
  };
  const bl = await load('./panels/bell/index.js');
  const root = document.createElement('div');
  document.body.append(root);
  const cleanup = bl.mount(root, { panel: { id: 'bell' }, api, payload: null, capability: () => null, open: () => {} });
  await sleep(120);
  const out = {};
  for (const label of ['Program', 'Çağrı', 'Ayarlar']) {
    root.querySelectorAll('[role="tab"], button').find((b) => b.textContent.trim() === label)?.click();
    await sleep(80);
    out[label] = audit(root);
  }
  cleanup?.();
  return out;
}
"""


def test_zil_ekrani_denetimden_gecer() -> None:
    try:
        data = run(SCENARIO, panels=("bell",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result = data["result"]
    assert set(result) == {"Program", "Çağrı", "Ayarlar"}
    bad = {name: items for name, items in result.items() if items}
    assert bad == {}, bad
