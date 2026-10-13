"""Renk karşıtlığı ve hareket — WCAG 2.2 AA (0.5.3, "erişilebilirliği full olsun").

Kapsam (iş paketi km-tek-ogrenci): çekirdek kabuk ve bileşen kiti, BBD Akademi
paneli, öğrenci yönetimi (bbd_students) ve Zil Sistemi ekranı. Her `color:`
bildirimi kendi kuralının zeminine (yoksa beyaz kâğıda) karşı ≥ 4,5:1 olmalı
(`tools/a11y/audit.mjs` → `cssContrast`). Devre dışı denetimler WCAG 1.4.3'ten
muaftır. Animasyon/geçiş taşıyan dosyalar `prefers-reduced-motion`a uyar —
kabuğun genel kuralı (`style.css`) hepsini kapsar, burada da sınanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run  # noqa: E402

FILES = sorted(
    [str(path.relative_to(ROOT)) for path in (ROOT / "modules" / "bbd_akademi" / "ui" / "panel").glob("*.css")]
    + ["modules/bbd_students/ui/panel/panel.css", "modules/bell/ui/panel/panel.css",
       "apps/desktop/shell/ui-kit/kit.css"])

# Zemin rengi başka kuralda tanımlı (beyaz yazı vurgu zemininde) — yanlış uyarı.
SKIP = ["[disabled]", ":disabled", ".ak-ev-score.on small"]

SCENARIO = r"""
import { readFileSync } from 'node:fs';
const ROOT = __ROOT__;
const FILES = __FILES__;
const SKIP = __SKIP__;
export default async function ({ cssContrast, cssAudit, tokens }) {
  const kit = readFileSync(`${ROOT}/apps/desktop/shell/ui-kit/kit.css`, 'utf8');
  const shell = readFileSync(`${ROOT}/apps/desktop/shell/style.css`, 'utf8');
  // Panel jetonları kitten, kâğıt jetonları kabuktan (`--paper-*`) gelir.
  const paper = Object.fromEntries(Object.entries(tokens(shell)).filter(([key]) => key.startsWith('--paper')));
  const extra = { ...tokens(kit), ...paper };
  const out = { low: {}, motion: cssAudit(shell, { name: 'style.css' }).filter((item) => item.rule === 'reduced-motion'),
    shellMotion: /prefers-reduced-motion:\s*reduce\)\s*\{\s*\*, \*::before, \*::after/.test(shell) };
  for (const file of FILES) {
    const bad = cssContrast(readFileSync(`${ROOT}/${file}`, 'utf8'), { extra, skip: SKIP });
    if (bad.length) out.low[file] = bad;
  }
  return out;
}
"""


def test_metin_karsitligi_en_az_4_5() -> None:
    import json

    scenario = (SCENARIO.replace("__ROOT__", json.dumps(str(ROOT))).replace("__FILES__", json.dumps(FILES))
                .replace("__SKIP__", json.dumps(SKIP)))
    try:
        data = run(scenario)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    result = data["result"]
    assert result["low"] == {}, result["low"]
    # Kabuk genel kuralı: hareket azaltma tercih edilince animasyon ve geçişler durur.
    assert result["shellMotion"] is True
    assert result["motion"] == []
