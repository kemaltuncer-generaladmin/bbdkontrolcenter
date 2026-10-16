"""Panel içindeki her göreli yol VAR OLAN bir dosyaya çözülür (ADR 0033 Aşama 2).

Panel kabukta `shell/panels/<id>/` altına (alt klasörleriyle) kopyalanır ve
oradan yüklenir; ortak kit `shell/ui-kit/`tedir. Bir dosya alt klasöre
taşındığında `'./x.js'`, `'../../ui-kit/y.js'`, dinamik `import('./z.js')`,
`sections.js`'in `file:` tablosu ya da `loadStyles(import.meta.url, 'a.css')`
yolu güncellenmezse hata ancak o ekran AÇILINCA görünür. Bu test bütün
modüllerin panellerinde her yolu dosya sistemine karşı çözer.
"""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "apps" / "desktop" / "shell"

_REL = re.compile(r"""(['"`])(\.{1,2}/[A-Za-z0-9_./-]+\.(?:js|css))\1""")
_STYLES = re.compile(r"""loadStyles\(import\.meta\.url,\s*(['"])([A-Za-z0-9_./-]+\.css)\1""")
_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE = re.compile(r"(^|[^:\\])//.*$", re.MULTILINE)


def _virtual_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for panel in sorted((ROOT / "modules").glob("*/ui/panel")):
        module = panel.parents[1].name
        for path in panel.rglob("*"):
            if path.is_file():
                files[f"shell/panels/{module}/{path.relative_to(panel).as_posix()}"] = path
    for path in (SHELL / "ui-kit").rglob("*"):
        if path.is_file():
            files[f"shell/ui-kit/{path.relative_to(SHELL / 'ui-kit').as_posix()}"] = path
    return files


def test_panel_goreli_yollari_cozulur() -> None:
    files = _virtual_files()
    broken: list[str] = []
    for virtual, path in files.items():
        if not virtual.startswith("shell/panels/") or path.suffix != ".js":
            continue
        code = _LINE.sub(lambda m: m.group(1), _BLOCK.sub("", path.read_text(encoding="utf-8")))
        folder = posixpath.dirname(virtual)
        targets = [m.group(2) for m in _REL.finditer(code)]
        targets += ["./" + m.group(2) for m in _STYLES.finditer(code)]
        for target in targets:
            resolved = posixpath.normpath(posixpath.join(folder, target))
            if resolved not in files:
                broken.append(f"{path.relative_to(ROOT)}: {target} → {resolved}")
    assert not broken, "Çözülmeyen panel yolu:\n" + "\n".join(broken)
