"""Koyu temaya hazır paneller (`export const darkReady = true`, ADR 0032) — 63-duzeltici.

Kabuk koyu temada yalnız `darkReady` diyen panelin kâğıdını koyulaştırır. Böyle bir
panel sabit renk yazarsa (ör. `background: #fff`) koyu kâğıtta beyaz kutu, açık yazı
kaybolur. Sınananlar:

* `darkReady` panelin CSS'inde `var(--x, yedek)` yedeği dışında sabit renk yok
  (gölge ve kabuk perdesi hariç); JS'inde satır içi renk yok.
* Koyu kâğıt jetonlarıyla (`style.css` → `.panel.km-dark-paper`) her `color:`
  bildirimi kendi zeminine (yoksa koyu kâğıda) karşı ≥ 4,5:1 — kit dahil.
* Aday listesindeki 12 panel hazır. BBD Akademi ve Öğrenci Yönetimi de jetona
  çevrildi ve koyu kâğıtta sınanır; Akademi'nin `darkReady`'si yalnız F-DK
  (`recordings/lesson-recordings.css`, öteki liderin alanı) yüzünden kapalı.
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
            "store_products", "store_shipping", "bbd_akademi"}
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


SHELL = ROOT / "apps" / "desktop" / "shell"
# Temadan BİLEREK bağımsız koyu/açık çizilenler (iki kâğıtta da doğru): bildirim
# balonu, telefon önizlemesinin cihaz çerçevesi ve logo zemini.
FIXED_SELECTORS = (".st-toast", ".ak-set-th-device", ".ak-set-th-phone-logo")


def defined_tokens(*texts: str) -> set[str]:
    names: set[str] = set()
    for text in (*texts, (SHELL / "style.css").read_text(encoding="utf-8"),
                 (SHELL / "ui-kit" / "kit.css").read_text(encoding="utf-8")):
        names |= set(re.findall(r"(--[\w-]+)\s*:", text))
    return names


def strip_fallbacks(value: str, known: set[str]) -> str:
    """TANIMLI jetonun `var(--x, #fff)` yedeğini atar (iç içe de). Tanımsız jetonun yedeği
    fiilen kullanılan renktir — o kalır ve sabit renk sayılır."""
    previous = None
    pattern = re.compile(r"var\(\s*(--[\w-]+)\s*,\s*(?:[^()]|\([^()]*\))*\)")
    while previous != value:
        previous = value
        value = pattern.sub(lambda m: "var()" if m.group(1) in known else m.group(0).replace("var(", "raw(", 1), value)
    return value


def themed_mixes(value: str) -> str:
    """Kâğıt jetonuyla karışan ton (`color-mix(in srgb, #hex 60%, var(--ink))`) temaya uyar: atılır."""
    return re.sub(r"color-mix\((?:[^()]|\([^()]*\))*var\(--[\w-]+\)(?:[^()]|\([^()]*\))*\)", "mix()", value)


def raw_colors(css: str) -> list[str]:
    body = re.sub(r"/\*[\s\S]*?\*/", "", css)
    known = defined_tokens(body)
    out = []
    for rule in re.finditer(r"(?P<sel>[^{}]+)\{(?P<body>[^{}]*)\}", body):
        selector = rule.group("sel").strip()
        if any(item in selector for item in FIXED_SELECTORS):
            continue
        for m in re.finditer(r"(?P<prop>[\w-]+)\s*:\s*(?P<val>[^;{}]+)", rule.group("body")):
            prop = m.group("prop")
            if prop.startswith(FREE_PROPS):
                continue
            if RAW.search(themed_mixes(strip_fallbacks(m.group("val"), known))):
                out.append(f"{selector} {{ {prop}: {m.group('val').strip()} }}")
    return out


def test_aday_paneller_koyuya_hazir() -> None:
    assert EXPECTED <= set(ready_modules())


# Satır içi rengi BİLEREK sabit olan dosyalar (gerekçe): temadan bağımsız doğru çizilirler.
INLINE_OK = {
    "modules/bbd_akademi/ui/panel/admin/settings-branding.js": "kurumun mobil uygulama renklerinin telefon önizlemesi",
    "modules/bbd_akademi/ui/panel/work/reading-editor.js": "tuval dışa aktarımında saydam PNG'nin beyaz zemini",
    "modules/bbd_akademi/ui/panel/exams/exams-analytics.js": "grafik çubuğu dolgusu (üstünde yazı yok)",
}
# F-DK ders kayıtları CSS'i de jetona çevrildi (ae48f1bf onayı, 16.10) → Akademi darkReady.
NOT_YET: set[str] = set()


def panel_problems(module: str) -> tuple[dict[str, list[str]], list[str]]:
    panel = MODULES / module / "ui" / "panel"
    bad = {rel: found for path in sorted(panel.rglob("*.css"))
           if (rel := str(path.relative_to(ROOT))) not in NOT_YET
           and (found := raw_colors(path.read_text(encoding="utf-8")))}
    inline = [rel for path in sorted(panel.rglob("*.js"))
              if (rel := str(path.relative_to(ROOT))) not in INLINE_OK
              and re.search(r"""['"`]#[0-9a-fA-F]{3,8}['"`]|:\s*#[0-9a-fA-F]{3,8}\b|style\.(color|background(Color)?)\s*=\s*['"`]#""",
                            path.read_text(encoding="utf-8"))]
    return bad, inline


@pytest.mark.parametrize("module", sorted(EXPECTED | {"bbd_akademi", "bbd_students"}))
def test_panelde_sabit_renk_yok(module: str) -> None:
    """Hazır paneller + BBD Akademi ve Öğrenci Yönetimi (jetona çevrildi; Akademi'nin
    darkReady'si yalnız F-DK dosyası yüzünden kapalı)."""
    bad, inline = panel_problems(module)
    assert bad == {}, bad
    assert inline == [], inline


def test_akademi_darkready_yalniz_fdk_bekliyor() -> None:
    """Yarım koyu tema açılmaz: Akademi `darkReady` diyorsa F-DK dosyası da hazır olmalı."""
    ready = "bbd_akademi" in ready_modules()
    pending = [path for path in NOT_YET if raw_colors((ROOT / path).read_text(encoding="utf-8"))]
    assert not (ready and pending), f"Akademi darkReady ama hazır olmayan dosya var: {pending}"


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
      { defaultBg: dark['--paper'], extra, skip: ['[disabled]', ':disabled', '.kit-toast', '.st-toast', '.ak-ev-score.on small'] });
    if (bad.length) out[file] = bad;
  }
  return out;
}
"""


def test_koyu_kagitta_metin_karsitligi() -> None:
    files = ["apps/desktop/shell/ui-kit/kit.css"]
    for module in sorted(set(ready_modules()) | {"bbd_akademi", "bbd_students"}):
        files += [rel for path in sorted((MODULES / module / "ui" / "panel").rglob("*.css"))
                  if (rel := str(path.relative_to(ROOT))) not in NOT_YET]
    scenario = SCENARIO.replace("__ROOT__", json.dumps(str(ROOT))).replace("__FILES__", json.dumps(files))
    try:
        data = run(scenario)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["result"] == {}, data["result"]
