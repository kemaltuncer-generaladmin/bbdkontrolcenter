#!/usr/bin/env python3
"""Akademi panel dosyalarını alt klasöre taşır ve bütün yolları yeniden yazar.

ADR 0033 §8 Aşama 2 (envanter-2026-10-16.md §10.2). Davranış değişmez:
yalnız dosya yeri ve ona göre göreli yollar değişir.

Kullanım (depo kökünden):
    python3 modules/bbd_akademi/tools/move_panel_files.py <klasör> <ad> [<ad> …]
    python3 modules/bbd_akademi/tools/move_panel_files.py coaching coaching coaching-queue …

`<ad>` uzantısızdır; aynı adlı `.js` ve `.css` birlikte taşınır. `<klasör>`
`.` ise dosya panel köküne geri gelir (geri alma).

Yeniden yazılanlar:
  · Panel JS'inde `'./x.js'`, `'../../ui-kit/y.js'` gibi bütün göreli dizgiler
    (statik/dinamik import, `sections.js` → `file:`, sekme `file:` tabloları).
    Yollar KABUKTAKİ yere göre çözülür (`shell/panels/bbd_akademi/…`,
    `shell/ui-kit/…`), çünkü panel oraya kopyalanıp oradan yüklenir.
  · `loadStyles(import.meta.url, 'x.css')` ikinci bağımsız değişkeni.
  · Testlerde `panels/bbd_akademi/<ad>` ve `PANEL / "<ad>"` biçimleri.

Dinamik `import(değişken)` yalnız iki yerde var (`index.js` → `section.file`,
`coaching.js` → `tab.file`) ve iki tablo da kendi çağıranıyla AYNI klasörde
tanımlı; dizgi tanımlandığı dosyaya göre yazıldığı için doğru çözülür.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "modules" / "bbd_akademi"
PANEL = MODULE / "ui" / "panel"
VIRTUAL = "shell/panels/bbd_akademi"
TESTS = [*sorted((MODULE / "tests").glob("*.py")),
         ROOT / "tests" / "integration" / "test_erisilebilirlik_karsitlik.py"]

_REL = re.compile(r"""(?P<q>['"`])(?P<p>\.{1,2}/[A-Za-z0-9_./-]+\.(?:js|css))(?P=q)""")
_STYLES = re.compile(r"""loadStyles\(import\.meta\.url,\s*(?P<q>['"])(?P<f>[A-Za-z0-9_./-]+\.css)(?P=q)""")


def panel_files() -> dict[str, str]:
    """Panel kökünden göreli yol → kabuktaki sanal yol."""
    return {path.relative_to(PANEL).as_posix(): f"{VIRTUAL}/{path.relative_to(PANEL).as_posix()}"
            for path in sorted(PANEL.rglob("*")) if path.is_file()}


def relative(from_file: str, to_file: str) -> str:
    rel = posixpath.relpath(to_file, posixpath.dirname(from_file))
    return rel if rel.startswith("../") else f"./{rel}"


def main(folder: str, names: list[str]) -> None:
    before = panel_files()
    moves: dict[str, str] = {}
    for name in names:
        found = [rel for rel in before if posixpath.splitext(posixpath.basename(rel))[0] == name]
        if not found:
            raise SystemExit(f"bulunamadı: {name}")
        for rel in found:
            target = posixpath.normpath(posixpath.join(folder, posixpath.basename(rel)))
            if target != rel:
                moves[rel] = target
    if not moves:
        print("taşınacak dosya yok")
        return
    old_to_new = {rel: moves.get(rel, rel) for rel in before}
    virtual_new = {f"{VIRTUAL}/{old}": f"{VIRTUAL}/{new}" for old, new in old_to_new.items()}

    # 1) Panel JS içindeki göreli yollar — eski yere göre çöz, yeni yere göre yaz.
    for old_rel, new_rel in old_to_new.items():
        if not old_rel.endswith(".js"):
            continue
        path = PANEL / old_rel
        text = path.read_text(encoding="utf-8")
        old_virtual = f"{VIRTUAL}/{old_rel}"
        new_virtual = f"{VIRTUAL}/{new_rel}"

        def fix(match: re.Match[str], *, old_virtual: str = old_virtual,
                new_virtual: str = new_virtual) -> str:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(old_virtual), match["p"]))
            target = virtual_new.get(target, target)
            return f"{match['q']}{relative(new_virtual, target)}{match['q']}"

        def fix_style(match: re.Match[str], *, old_virtual: str = old_virtual,
                      new_virtual: str = new_virtual) -> str:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(old_virtual), match["f"]))
            target = virtual_new.get(target, target)
            rel = posixpath.relpath(target, posixpath.dirname(new_virtual))
            return f"loadStyles(import.meta.url, {match['q']}{rel}{match['q']}"

        updated = _STYLES.sub(fix_style, _REL.sub(fix, text))
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    # 2) Dosyaları taşı (git geçmişi korunur).
    for old_rel, new_rel in sorted(moves.items()):
        (PANEL / new_rel).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", str(PANEL / old_rel), str(PANEL / new_rel)], check=True, cwd=ROOT)
    for directory in sorted({(PANEL / rel).parent for rel in moves}, reverse=True):
        if directory != PANEL and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()

    # 3) Testlerdeki yollar.
    for test in TESTS:
        text = test.read_text(encoding="utf-8")
        updated = text
        for old_rel, new_rel in moves.items():
            updated = updated.replace(f"panels/bbd_akademi/{old_rel}", f"panels/bbd_akademi/{new_rel}")
            updated = re.sub(rf"""(/\s*)(['"]){re.escape(old_rel)}\2""",
                             lambda m, new_rel=new_rel: f"{m.group(1)}{m.group(2)}{new_rel}{m.group(2)}",
                             updated)
        if updated != text:
            test.write_text(updated, encoding="utf-8")

    print(f"{len(moves)} dosya → {folder}/")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    main(sys.argv[1], sys.argv[2:])
