#!/usr/bin/env python3
"""Akademi backend dosyalarını alt pakete taşır ve bütün importları yeniden yazar.

ADR 0033 §8 Aşama 2 (envanter-2026-10-16.md §10.3). Davranış değişmez: yalnız
dosya yeri ve import yolu değişir.

Kullanım (depo kökünden):
    python3 modules/bbd_akademi/tools/move_backend_modules.py eski=yeni [eski=yeni …]
    python3 modules/bbd_akademi/tools/move_backend_modules.py gateway=core.gateway proxy=core.proxy

Adlar `backend/` altına göre noktalı modül yoludur. Yeniden yazılanlar:
  · backend içindeki göreli importlar (`from .gateway import X`,
    `from .. import contract_features`, `from . import document_pdf`) — yeni
    yere göre yeniden hesaplanır; ad değiştiyse `import yeni as eski` yazılır
    ki gövdedeki kullanım (`document_pdf.render(...)`) değişmesin;
  · testler ve araçlardaki mutlak importlar (`modules.bbd_akademi.backend.X`);
  · module.yaml'daki `backend.X:Y` giriş noktaları (görev, sözleşme).
Yeni paketlere boş `__init__.py` konur.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "modules" / "bbd_akademi"
BACKEND = MODULE / "backend"
ABS = "modules.bbd_akademi.backend"
EXTERNAL = [*sorted((MODULE / "tests").glob("*.py")), *sorted((MODULE / "tools").glob("*.py")),
            *sorted((ROOT / "tools").rglob("*.py")), *sorted((ROOT / "tests").rglob("*.py"))]

_FROM = re.compile(r"^(?P<indent>\s*)from (?P<dots>\.*)(?P<mod>[A-Za-z_][\w.]*)? import (?P<names>.+)$")


def module_of(path: Path) -> str:
    rel = path.relative_to(BACKEND).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["backend", *parts])


def package_of(path: Path) -> list[str]:
    mod = module_of(path).split(".")
    return mod if path.name == "__init__.py" else mod[:-1]


def resolve(package: list[str], dots: str, mod: str | None) -> str:
    base = package[: len(package) - (len(dots) - 1)] if len(dots) > 1 else package
    return ".".join([*base, *(mod.split(".") if mod else [])])


def relative(from_package: list[str], target: str) -> tuple[str, str]:
    """`target` (backend.a.b) için `from_package`ten göreli (nokta, ad) çifti."""
    parts = target.split(".")
    common = 0
    while common < min(len(from_package), len(parts)) and from_package[common] == parts[common]:
        common += 1
    up = len(from_package) - common
    return "." * (up + 1), ".".join(parts[common:])


def rename(target: str, moves: dict[str, str]) -> str:
    for old, new in moves.items():
        if target == old or target.startswith(old + "."):
            return new + target[len(old):]
    return target


def rewrite_line(line: str, package: list[str] | None, moves: dict[str, str]) -> list[str]:
    match = _FROM.match(line)
    if not match:
        return [line]
    indent, dots, mod, names = match["indent"], match["dots"], match["mod"], match["names"]
    if dots:
        if package is None:
            return [line]
        absolute = resolve(package, dots, mod)
    elif mod and (mod == ABS or mod.startswith(ABS + ".")):
        absolute = "backend" + mod[len(ABS):]
    else:
        return [line]

    def emit(target: str, imported: str) -> str:
        if package is None:
            return f"{indent}from {ABS}{target[len('backend'):]} import {imported}"
        rel_dots, rel_mod = relative(package, target)
        return f"{indent}from {rel_dots}{rel_mod} import {imported}"

    # `from paket import modül[, modül]` — taşınan modül adı olarak alınıyorsa.
    plain = names.strip()
    if not plain.startswith("(") and "(" not in plain:
        items = [item.strip() for item in plain.split(",") if item.strip()]
        out: list[str] = []
        changed = False
        for item in items:
            name, _, alias = item.partition(" as ")
            candidate = f"{absolute}.{name.strip()}"
            if candidate in moves:
                new = moves[candidate]
                parent, _, leaf = new.rpartition(".")
                local = alias.strip() or name.strip()
                out.append(emit(parent, leaf if leaf == local else f"{leaf} as {local}"))
                changed = True
            else:
                out.append(emit(rename(absolute, moves), item))
        if changed or rename(absolute, moves) != absolute or package is not None:
            merged: dict[str, list[str]] = {}
            for statement in out:
                head, _, tail = statement.partition(" import ")
                merged.setdefault(head, []).append(tail)
            lines = [f"{head} import {', '.join(tails)}" for head, tails in merged.items()]
            return lines if lines != [line] else [line]
        return [line]
    return [emit(rename(absolute, moves), names)]


def main(pairs: list[str]) -> None:
    moves = {}
    for pair in pairs:
        old, _, new = pair.partition("=")
        moves[f"backend.{old}"] = f"backend.{new}"

    # 1) Yeni yerleri hesapla, sonra taşı.
    files = sorted(BACKEND.rglob("*.py"))
    new_location: dict[Path, Path] = {}
    for path in files:
        mod = module_of(path)
        target = moves.get(mod)
        if target:
            new_path = BACKEND / Path(*target.split(".")[1:]).with_suffix(".py")
            new_location[path] = new_path
    old_package = {path: package_of(path) for path in files}

    # 2) İçeriği ESKİ paket bağlamında çöz, YENİ bağlamda yaz.
    rewritten: dict[Path, str] = {}
    for path in files:
        new_path = new_location.get(path, path)
        new_pkg = package_of(new_path)
        out: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
            body = line.rstrip("\n")
            match = _FROM.match(body)
            if match and match["dots"]:
                absolute = resolve(old_package[path], match["dots"], match["mod"])
                fake_line = f"{match['indent']}from {ABS}{absolute[len('backend'):]} import {match['names']}"
                result = rewrite_line(fake_line, None, moves)
                # Mutlakları yeni pakete göre gölge göreliye çevir.
                converted = []
                for item in result:
                    m2 = _FROM.match(item)
                    assert m2 and m2["mod"]
                    target = "backend" + m2["mod"][len(ABS):]
                    rel_dots, rel_mod = relative(new_pkg, target)
                    converted.append(f"{m2['indent']}from {rel_dots}{rel_mod} import {m2['names']}")
                out.extend(item + "\n" for item in converted)
            else:
                out.append(line)
        rewritten[path] = "".join(out)

    for path, text in rewritten.items():
        path.write_text(text, encoding="utf-8")
    for old_path, new_path in new_location.items():
        new_path.parent.mkdir(parents=True, exist_ok=True)
        init = new_path.parent / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")
            subprocess.run(["git", "add", str(init)], check=True, cwd=ROOT)
        subprocess.run(["git", "mv", str(old_path), str(new_path)], check=True, cwd=ROOT)

    # 3) Testler/araçlar: mutlak importlar ve dizgi içindeki yollar.
    for path in EXTERNAL:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        lines: list[str] = []
        for line in text.splitlines(keepends=True):
            body = line.rstrip("\n")
            if _FROM.match(body):
                lines.extend(item + "\n" for item in rewrite_line(body, None, moves))
                continue
            # İmport olmayan satırda dizgi içindeki yol (ör. monkeypatch hedefi).
            for old, new in moves.items():
                line = re.sub(rf"{re.escape(ABS)}{re.escape(old[len('backend'):])}\b(?![.\w]*\.py)",
                              f"{ABS}{new[len('backend'):]}", line)
            lines.append(line)
        updated = "".join(lines)
        if updated != text:
            path.write_text(updated, encoding="utf-8")

    # 4) Manifest giriş noktaları.
    manifest = MODULE / "module.yaml"
    text = manifest.read_text(encoding="utf-8")
    updated = text
    for old, new in moves.items():
        updated = re.sub(rf"\b{re.escape(old)}:", f"{new}:", updated)
    if updated != text:
        manifest.write_text(updated, encoding="utf-8")
    print(f"{len(new_location)} dosya taşındı: " + ", ".join(f"{a}→{b}" for a, b in moves.items()))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(sys.argv[1:])
