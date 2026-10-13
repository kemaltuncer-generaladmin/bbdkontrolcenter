"""Modül stilleri ortak kitin sınıflarını KAPSAMSIZ yeniden tanımlayamaz.

BULUNAN HATA (13.10.2026, kullanıcı: "KM'de toplu işlemler kısmında öğrenci
seçilemiyor, hatalı, UI görünmüyor"). Paneller stilini `<head>`e bir kez ekler
ve hiç kaldırmaz (`ui-kit/kit.js` → `loadStyles`). Eski kantin panelleri kitin
ESKİ BİR KOPYASINI taşıyor ve kopyanın stilleri kapsamsızdı:

    .pk-box { width: 15px; height: 15px; }      /* onay kutusu, kopyada */

Ortak kitte `.pk-box` seçicinin KÖKÜDÜR. Ödeme Talebi paneli bir arayüz
yeteneği sağladığı için (öğrenci kartı) kabuk onu HER AÇILIŞTA import ediyordu;
dosya tepesindeki `loadStyles` bu stili bütün ekranlara yayıyor ve BBD Akademi
→ Öğrenciler → "Toplu işlemler" öğrenci listesi 15 piksellik bir şeride
iniyordu. Aynı sızıntı `.kit-btn`, `.kit-dialog`, `.kit-overlay`,
`.kit-toast`, `.df-*` için de vardı (Öğle Yemeği, SMS, Toplu Satış ekranları
bir kez açıldıktan sonra bütün oturum).

KURAL. `modules/*/ui/**/*.css` içinde kitin sınıfıyla (`.kit-…`, `.pk`,
`.pk-…`, `.df-…`) başlayan seçici, modülün KENDİ sınıfını da taşımalıdır:
`.kit-drawer.ak-ev-drawer`, `.kit-drawer-body > .iv-legal` ya da kökle
kapsanmış `:where(.pr) .kit-btn`. Yalnız kit sınıflarından oluşan seçici
(`.pk-box`, `.kit-btn:hover`) bütün ekranlara uygulanır ve yasaktır.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KIT = re.compile(r"^\.(?:kit-[\w-]+|pk-[\w-]+|pk|df-[\w-]+)(?![\w-])")
CLASS = re.compile(r"\.([A-Za-z_][\w-]*)")
COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
HEADER = re.compile(r"([^{};]+)\{|[{};]")


def _selectors(text: str):
    """Kural başlıklarındaki seçiciler (@keyframes kareleri ve @-kuralları hariç)."""
    stack: list[str] = []
    for match in HEADER.finditer(COMMENT.sub(" ", text)):
        token = match.group(0)
        if match.group(1) is not None:
            header = match.group(1).strip()
            if header.startswith("@"):
                stack.append("keyframes" if "keyframes" in header else "at")
                continue
            if "keyframes" in stack:
                stack.append("frame")
                continue
            stack.append("rule")
            for part in header.split(","):
                if part.strip():
                    yield part.strip()
        elif token == "{":
            stack.append("block")
        elif token == "}" and stack:
            stack.pop()


def _violations(text: str) -> list[str]:
    bad = []
    for selector in _selectors(text):
        if not KIT.match(selector):
            continue
        own = [name for name in CLASS.findall(selector) if not KIT.match(f".{name}")]
        if not own:
            bad.append(selector)
    return bad


def test_modul_stilleri_kitin_siniflarini_kapsamsiz_tanimlamaz() -> None:
    found = {}
    for css in sorted((ROOT / "modules").glob("*/ui/**/*.css")):
        bad = _violations(css.read_text(encoding="utf-8"))
        if bad:
            found[str(css.relative_to(ROOT))] = bad[:5]
    assert not found, f"kapsamsız kit seçicileri (panel köküyle kapsayın): {found}"


def test_denetim_kendisi_dogru() -> None:
    sample = """
    /* .pk-box { width: 1px } yorum sayılmaz */
    .pk-box { width: 15px; }
    .kit-btn:hover, .xx-btn { color: red; }
    :where(.pr) .kit-btn { color: red; }
    .ak .pk-box { height: 1px; }
    .kit-drawer.ak-ev-drawer { width: 2px; }
    .kit-drawer-body > .iv-legal { margin: 0; }
    .pk-row .pk-name { color: red; }
    @media (max-width: 9px) { .df-pop { left: 0; } }
    @keyframes kit-rise { from { opacity: 0; } to { opacity: 1; } }
    """
    assert _violations(sample) == [".pk-box", ".kit-btn:hover", ".pk-row .pk-name", ".df-pop"]


def test_yetenek_saglayan_panel_stilini_tepede_yuklemez() -> None:
    """Kabuk, yetenek sağlayan panelleri açılışta import eder (`ensureProviders`)."""
    import yaml

    offenders = []
    for manifest in sorted((ROOT / "modules").glob("*/module.yaml")):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        panel = manifest.parent / "ui" / "panel" / "index.js"
        if not data.get("provides") or not panel.exists():
            continue
        for line in panel.read_text(encoding="utf-8").splitlines():
            if re.match(r"^(loadStyles|loadCss)\(", line):
                offenders.append(f"{manifest.parent.name}: {line.strip()}")
    assert not offenders, f"stil `mount()` içinde yüklenmeli: {offenders}"
