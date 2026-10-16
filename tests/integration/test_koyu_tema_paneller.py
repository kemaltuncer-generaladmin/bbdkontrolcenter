"""Koyu temaya hazır paneller (`export const darkReady = true`, ADR 0032) — 63-duzeltici.

Kabuk koyu temada yalnız `darkReady` diyen panelin kâğıdını koyulaştırır. Böyle bir
panel sabit renk yazarsa (ör. `background: #fff`) koyu kâğıtta beyaz kutu, açık yazı
kaybolur. Sınananlar:

* `darkReady` panelin CSS'inde `var(--x, yedek)` yedeği dışında sabit renk yok
  (gölge ve kabuk perdesi hariç); JS'inde satır içi renk yok.
* Koyu kâğıt jetonlarıyla (`style.css` → `.panel.km-dark-paper`) her `color:`
  bildirimi kendi zeminine (yoksa koyu kâğıda) karşı ≥ 4,5:1 — kit dahil.
* Aday listesindeki 12 panel ve kabuk çekirdek ekranları hazır.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

MODULES = ROOT / "modules"
EXPECTED = {"antivirus", "print", "bbd_canteen_devices", "store_notifications", "store_udit_logs",
            "store_refunds", "store_bundles", "store_dashboard", "store_customers", "store_orders",
            "store_products", "store_shipping"}
READY = re.compile(r"export\s+const\s+darkReady\s*=\s*true")
RAW = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
# Gölge, perde ve geçiş dışı: bunlar temadan bağımsız (koyu gölge her iki kâğıtta doğru).
FREE_PROPS = ("box-shadow", "text-shadow", "filter")


def ready_modules() -> list[str]:
    found = []
    for path in sorted(MODULES.glob("*/ui/panel/index.js")):
        if READY.search(path.read_text(encoding="utf-8")):
            found.append(path.parents[2].name)
    return found


def strip_fallbacks(value: str) -> str:
    """`var(--x, #fff)` yedeklerini atar (iç içe de)."""
    previous = None
    while previous != value:
        previous = value
        value = re.sub(r"var\(\s*--[\w-]+\s*,\s*([^()]|\([^()]*\))*\)", "var()", value)
    return value


def raw_colors(css: str) -> list[str]:
    body = re.sub(r"/\*[\s\S]*?\*/", "", css)
    out = []
    for m in re.finditer(r"(?P<prop>[\w-]+)\s*:\s*(?P<val>[^;{}]+)", body):
        prop = m.group("prop")
        if prop.startswith("--") or prop.startswith(FREE_PROPS):
            continue
        if RAW.search(strip_fallbacks(m.group("val"))):
            out.append(f"{prop}: {m.group('val').strip()}")
    return out


def test_aday_paneller_koyuya_hazir() -> None:
    assert EXPECTED <= set(ready_modules())


@pytest.mark.parametrize("module", sorted(EXPECTED))
def test_hazir_panelde_sabit_renk_yok(module: str) -> None:
    panel = MODULES / module / "ui" / "panel"
    bad = {str(path.relative_to(ROOT)): found for path in sorted(panel.rglob("*.css"))
           if (found := raw_colors(path.read_text(encoding="utf-8")))}
    assert bad == {}, bad
    inline = [str(path.relative_to(ROOT)) for path in sorted(panel.rglob("*.js"))
              if re.search(r"""['"`]#[0-9a-fA-F]{3,8}['"`]|style\.(color|background(Color)?)\s*=\s*['"`]#""",
                           path.read_text(encoding="utf-8"))]
    assert inline == [], inline


SCENARIO = r"""
import { readFileSync } from 'node:fs';
const ROOT = __ROOT__;
const FILES = __FILES__;
export default async function ({ cssContrast, tokens }) {
  const kit = readFileSync(`${ROOT}/apps/desktop/shell/ui-kit/kit.css`, 'utf8');
  const shell = readFileSync(`${ROOT}/apps/desktop/shell/style.css`, 'utf8');
  const light = Object.fromEntries(Object.entries(tokens(shell)).filter(([key]) => key.startsWith('--paper')));
  const dark = tokens(shell, '.panel.km-dark-paper');
  const extra = { ...tokens(kit, '.kit-panel'), ...light, ...dark };
  const out = {};
  for (const file of FILES) {
    // Toast ve kabuk perdesi temadan bağımsız koyu: kendi zeminleri var.
    const bad = cssContrast(readFileSync(`${ROOT}/${file}`, 'utf8'),
      { defaultBg: dark['--paper'], extra, skip: ['[disabled]', ':disabled', '.kit-toast'] });
    if (bad.length) out[file] = bad;
  }
  return out;
}
"""


def test_koyu_kagitta_metin_karsitligi() -> None:
    files = ["apps/desktop/shell/ui-kit/kit.css"]
    for module in ready_modules():
        files += [str(path.relative_to(ROOT)) for path in sorted((MODULES / module / "ui" / "panel").rglob("*.css"))]
    scenario = SCENARIO.replace("__ROOT__", json.dumps(str(ROOT))).replace("__FILES__", json.dumps(files))
    try:
        data = run(scenario)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["result"] == {}, data["result"]
