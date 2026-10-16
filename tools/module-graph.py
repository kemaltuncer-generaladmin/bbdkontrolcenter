#!/usr/bin/env python3
"""Modül envanteri ve bağımlılık grafiği (ADR 0033).

`modules/*/module.yaml` + kaynak koddan şunları üretir:
  · alan haritası (alan → modüller, satır, açık/kapalı)
  · modül → modül kenarları: `depends`, yetenek (`provides`→`consumes`),
    olay (`publishes`→`subscribes`); alanlar arası olanlar işaretli
  · sınır bulguları (tools/arch_scan.py — check-boundaries ile aynı tarayıcı)
  · ortak kod kopyaları: modüller arasında birebir aynı dosyalar ve gövdesi
    aynı Python fonksiyonları
  · ölü kod adayları: giriş noktalarından (entrypoint, router, görev, olay
    dinleyicisi, panel girişi) hiçbir import zinciriyle ulaşılamayan dosyalar

Kullanım:
    python3 tools/module-graph.py                 # Markdown'u stdout'a
    python3 tools/module-graph.py --write         # docs/architecture/module-graph.md
    python3 tools/module-graph.py --dot out.dot   # Graphviz (alan kümeli)

Çıktı ÜRETİLMİŞTİR; elle düzenlenmez. Yorum ve karar belgeleri
(`envanter-*.md`, ADR 0033) bu çıktıya atıf yapar.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arch_scan
from arch_scan import JS, PY, SQL, Module, Scan

for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8")

OUT = Path("docs") / "architecture" / "module-graph.md"

#: Platform yetenekleri — modül sağlamaz, çekirdek kaydeder.
PLATFORM_CAPS = {"ssh", "database", "printer", "audio", "scheduler", "secrets", "notify", "events.outbox"}


# ------------------------------------------------------------------ kenar


def edges(scan: Scan) -> list[tuple[str, str, str, str]]:
    """(kaynak, hedef, tür, etiket) — kaynak hedefe BAĞIMLI."""
    provider: dict[str, str] = {}
    for module in scan.modules.values():
        for capability in module.provides:
            provider[capability] = module.id
    publishers: dict[str, list[str]] = defaultdict(list)
    for module in scan.modules.values():
        for event in module.publishes:
            publishers[event].append(module.id)

    out: set[tuple[str, str, str, str]] = set()
    for module in scan.modules.values():
        for target in module.depends:
            out.add((module.id, target, "depends", ""))
        for capability, optional in module.consumes:
            if capability in PLATFORM_CAPS:
                continue
            target = provider.get(capability)
            out.add((module.id, target or "?", "capability", capability + (" (ops.)" if optional else "")))
        for event in module.subscribes:
            for target in publishers.get(event) or ["?"]:
                out.add((module.id, target, "event", event))
        # Panelin arayüz yeteneği çağrıları (ADR 0031: `ctx.capability`/`ctx.open`).
        for path in arch_scan.source_files(module, (JS,)):
            code = arch_scan.js_code(path.read_text(encoding="utf-8", errors="replace"))
            for name in _UI_CAPABILITY.findall(code):
                target = provider.get(name)
                if target and target != module.id:
                    out.add((module.id, target, "ui", name))
    return sorted(out)


def lines_of(module: Module) -> int:
    return sum(arch_scan.count_lines(path) for path in arch_scan.source_files(module, (PY, JS, SQL, ".css")))


# --------------------------------------------------------------- kopyalar


def duplicate_files(scan: Scan) -> list[tuple[str, list[str]]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for module in scan.modules.values():
        for path in arch_scan.source_files(module, (PY, JS, ".css")):
            data = path.read_bytes()
            if len(data) < 400:
                continue
            groups[hashlib.sha1(data).hexdigest()].append(path.relative_to(scan.root).as_posix())
    found = []
    for files in groups.values():
        owners = {file.split("/")[1] for file in files}
        if len(owners) > 1:
            found.append((Path(files[0]).name, sorted(files)))
    return sorted(found, key=lambda item: (-len(item[1]), item[0]))


def duplicate_functions(scan: Scan, min_statements: int = 4) -> list[tuple[str, list[str]]]:
    """Gövdesi (adı ve belge dizgisi hariç) aynı Python fonksiyonları."""
    groups: dict[str, list[str]] = defaultdict(list)
    for module in scan.modules.values():
        for path in arch_scan.source_files(module, (PY,)):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                body = list(node.body)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    body = body[1:]
                if sum(1 for _ in ast.walk(ast.Module(body=body, type_ignores=[]))) < min_statements * 6:
                    continue
                key = hashlib.sha1((ast.dump(node.args) + "".join(ast.dump(stmt) for stmt in body))
                                   .encode()).hexdigest()
                groups[key].append(f"{path.relative_to(scan.root).as_posix()}::{node.name}")
    found = []
    for places in groups.values():
        owners = {place.split("/")[1] for place in places}
        if len(owners) > 1:
            found.append((places[0].split("::")[1], sorted(places)))
    return sorted(found, key=lambda item: (-len(item[1]), item[0]))


# --------------------------------------------------------------- ölü kod

_UI_CAPABILITY = re.compile(r"""\b(?:capability|open)\(\s*['"]([a-z][a-z0-9_]*(?:\.[A-Za-z0-9_]+)+)['"]""")
_JS_DYNAMIC = re.compile(r"""['"](\./[\w./-]+\.js)['"]""")


def _python_roots(module: Module) -> set[str]:
    """Manifestin işaret ettiği Python modülleri (nokta yolu, modül köküne göre)."""
    manifest = module.manifest
    roots = {str(manifest.get("entrypoint", "")).partition(":")[0]}
    roots.add(str((manifest.get("http") or {}).get("router", "")).partition(":")[0])
    for item in manifest.get("tasks") or []:
        roots.add(str(item.get("handler", "")).partition(":")[0])
    for item in (manifest.get("events") or {}).get("subscribes") or []:
        roots.add(str(item.get("handler", "")).partition(":")[0])
    return {root for root in roots if root}


def _resolve_relative(current: str, level: int, name: str | None, is_package: bool) -> str:
    parts = current.split(".")
    base = parts if is_package else parts[:-1]
    base = base[: len(base) - (level - 1)] if level > 1 else base
    return ".".join([*base, *(name.split(".") if name else [])])


def unreachable_files(module: Module) -> tuple[list[str], list[str]]:
    """(python, js) — giriş noktalarından ulaşılamayan dosyalar."""
    py_files: dict[str, Path] = {}
    for path in arch_scan.source_files(module, (PY,)):
        rel = path.relative_to(module.path).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        py_files[".".join(parts)] = path

    seen: set[str] = set()
    queue = [root for root in _python_roots(module) if root in py_files]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        path = py_files[current]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        is_package = path.name == "__init__.py"
        # Paketin __init__'i de yüklenir.
        parent = current.rsplit(".", 1)[0] if "." in current else ""
        if parent in py_files:
            queue.append(parent)
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.level > 0:
                base = _resolve_relative(current, node.level, node.module, is_package)
                targets.append(base)
                targets.extend(f"{base}.{alias.name}" for alias in node.names)
            elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "import_module" \
                    and node.args and isinstance(node.args[0], ast.Constant):
                targets.append(str(node.args[0].value).lstrip("."))
            for target in targets:
                if target in py_files and target not in seen:
                    queue.append(target)

    dead_py = sorted(path.relative_to(module.path).as_posix() for key, path in py_files.items()
                     if key not in seen and not key.startswith(("tools", "scripts"))
                     and "migrations" not in key.split("."))

    js_files = {path.relative_to(module.path).as_posix(): path
                for path in arch_scan.source_files(module, (JS,))}
    entry = str((module.manifest.get("ui") or {}).get("entry") or "")
    seen_js: set[str] = set()
    stack = [entry] if entry in js_files else []
    while stack:
        current = stack.pop()
        if current in seen_js:
            continue
        seen_js.add(current)
        source = js_files[current].read_text(encoding="utf-8", errors="replace")
        specs = [spec for _, spec in arch_scan.js_imports(source)] + _JS_DYNAMIC.findall(source)
        folder = Path(current).parent
        for spec in specs:
            if not spec.startswith("./"):
                continue
            target = (folder / spec[2:]).as_posix()
            if target in js_files:
                stack.append(target)
    dead_js = sorted(rel for rel in js_files if rel not in seen_js and rel.startswith("ui/")) if entry else []
    return dead_py, dead_js


# --------------------------------------------------------------- çıktı


def _n(value: int) -> str:
    """Türkçe binlik ayırıcı: 293202 → 293.202."""
    return f"{value:,}".replace(",", ".")


def _cell(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "—"


def markdown(scan: Scan) -> str:
    catalog = scan.catalog
    by_domain: dict[str, list[Module]] = defaultdict(list)
    for module in scan.modules.values():
        by_domain[module.domain or "?"].append(module)
    sizes = {module.id: lines_of(module) for module in scan.modules.values()}
    all_edges = edges(scan)
    domain_of = {module.id: module.domain for module in scan.modules.values()}

    out: list[str] = []
    w = out.append
    w("# Modül grafiği (ÜRETİLMİŞ — elle düzenlemeyin)")
    w("")
    w("Üretici: `python3 tools/module-graph.py --write` · Tarayıcı: `tools/arch_scan.py` · "
      "Karar: [ADR 0033](../adr/0033-alan-servisleri-ve-sinirlar.md) · "
      "Yorum: [envanter-2026-10-16.md](envanter-2026-10-16.md)")
    w("")
    total = sum(sizes.values())
    enabled = sum(1 for module in scan.modules.values() if module.enabled)
    w(f"**{len(scan.modules)} modül** ({enabled} açık, {len(scan.modules) - enabled} kapalı) · "
      f"{_n(total)} satır (py/js/sql/css, testler hariç) · {len(catalog.domains)} alan")
    w("")

    w("## 1. Alan haritası")
    w("")
    w("| Alan | Ad | Modül | Açık | Satır | Sınıf üst sınırı |")
    w("|---|---|---:|---:|---:|---|")
    for domain_id, info in catalog.domains.items():
        modules = by_domain.get(domain_id, [])
        w(f"| `{domain_id}` | {info.get('title', '')} | {len(modules)} | "
          f"{sum(1 for module in modules if module.enabled)} | "
          f"{_n(sum(sizes[module.id] for module in modules))} | {info.get('classification', '')} |")
    w("")

    w("## 2. Modüller")
    w("")
    w("| Modül | Alan | Sınıf | Durum | Satır | depends | sağlar | tüketir (modül yeteneği) | yayımlar | dinler |")
    w("|---|---|---|---|---:|---|---|---|---|---|")
    for domain_id in catalog.domains:
        for module in sorted(by_domain.get(domain_id, []), key=lambda m: m.id):
            consumed = [cap for cap, _ in module.consumes if cap not in PLATFORM_CAPS]
            w(f"| `{module.id}` | {module.domain} | {module.classification} | "
              f"{'açık' if module.enabled else '**kapalı**'} | {_n(sizes[module.id])} | "
              f"{_cell(module.depends)} | {_cell(module.provides)} | {_cell(consumed)} | "
              f"{_cell(module.publishes)} | {_cell(module.subscribes)} |")
    w("")

    w("## 3. Alanlar arası kenarlar")
    w("")
    w("Kaynak hedefe bağımlıdır. Alan içi kenarlar grafikte, burada yalnız alan DIŞI olanlar.")
    w("")
    w("| Kaynak (alan) | Hedef (alan) | Tür | Ne |")
    w("|---|---|---|---|")
    for source, target, kind, label in all_edges:
        if domain_of.get(source) != domain_of.get(target):
            w(f"| `{source}` ({domain_of.get(source, '?')}) | `{target}` ({domain_of.get(target, '?')}) | "
              f"{kind} | {('`' + label + '`') if label else ''} |")
    w("")

    w("## 4. Alan grafiği")
    w("")
    w("```mermaid")
    w("flowchart LR")
    domain_edges: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source, target, kind, _ in all_edges:
        a, b = domain_of.get(source, "?"), domain_of.get(target, "?")
        if a != b:
            domain_edges[(a, b)].add(kind)
    for domain_id, info in catalog.domains.items():
        w(f"  {domain_id}[\"{info.get('title', domain_id)}<br/>{len(by_domain.get(domain_id, []))} modül\"]")
    for (a, b), kinds in sorted(domain_edges.items()):
        w(f"  {a} -->|{' + '.join(sorted(kinds))}| {b}")
    w("```")
    w("")

    w("## 5. Modül grafiği (açık modüller)")
    w("")
    w("```mermaid")
    w("flowchart LR")
    for domain_id, info in catalog.domains.items():
        members = [m for m in by_domain.get(domain_id, []) if m.enabled]
        if not members:
            continue
        w(f"  subgraph {domain_id}[\"{info.get('title', domain_id)}\"]")
        for module in sorted(members, key=lambda m: m.id):
            w(f"    {module.id}")
        w("  end")
    enabled_ids = {module.id for module in scan.modules.values() if module.enabled}
    seen_pairs: set[tuple[str, str]] = set()
    for source, target, kind, _ in all_edges:
        if source in enabled_ids and target in enabled_ids and (source, target) not in seen_pairs:
            seen_pairs.add((source, target))
            arrow = "-.->" if kind == "event" else "-->"
            w(f"  {source} {arrow} {target}")
    w("```")
    w("")

    verdict = arch_scan.judge(scan, arch_scan.load_exceptions(scan.root))
    w("## 6. Sınır bulguları")
    w("")
    w(f"{len(scan.findings)} bulgu · istisnada {len(verdict.allowed)} · yeni {len(verdict.new)} "
      f"(kapı: `python3 tools/check-boundaries.py`).")
    w("")
    w("| Kural | Modül | Hedef | Yer |")
    w("|---|---|---|---|")
    for finding in scan.findings:
        w(f"| {finding.rule} | `{finding.module}` | `{finding.target}` | `{finding.file}:{finding.line}` |")
    w("")

    w("## 7. Modüller arası birebir aynı dosyalar")
    w("")
    for name, files in duplicate_files(scan):
        w(f"- **{name}** ({len(files)} kopya): " + ", ".join(f"`{file}`" for file in files))
    w("")
    w("## 8. Modüller arası gövdesi aynı Python fonksiyonları")
    w("")
    for name, places in duplicate_functions(scan):
        modules = sorted({place.split("/")[1] for place in places})
        w(f"- `{name}` — {len(places)} yer, {len(modules)} modül: " + ", ".join(f"`{m}`" for m in modules))
    w("")

    w("## 9. Ölü kod adayları (giriş noktasından ulaşılamayan dosyalar)")
    w("")
    w("Statik import zinciri; `importlib`, dizgiyle kurulan yol ya da test-yalnız kullanım "
      "kaçırılabilir — silmeden önce elle doğrulanır.")
    w("")
    for module in sorted(scan.modules.values(), key=lambda m: m.id):
        dead_py, dead_js = unreachable_files(module)
        if dead_py or dead_js:
            w(f"- `{module.id}`{' (kapalı)' if not module.enabled else ''}: "
              + ", ".join(f"`{file}`" for file in [*dead_py, *dead_js]))
    w("")
    return "\n".join(out)


def dot(scan: Scan) -> str:
    out = ["digraph km {", "  rankdir=LR; node [shape=box, fontsize=10];"]
    for domain_id, info in scan.catalog.domains.items():
        out.append(f'  subgraph cluster_{domain_id} {{ label="{info.get("title", domain_id)}";')
        for module in scan.modules.values():
            if module.domain == domain_id:
                style = "" if module.enabled else ", style=dashed"
                out.append(f'    "{module.id}" [label="{module.id}"{style}];')
        out.append("  }")
    for source, target, kind, label in edges(scan):
        style = {"event": "dashed", "capability": "solid", "depends": "dotted", "ui": "bold"}[kind]
        out.append(f'  "{source}" -> "{target}" [style={style}, tooltip="{label}"];')
    out.append("}")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=arch_scan.ROOT)
    parser.add_argument("--write", action="store_true", help=f"{OUT} dosyasına yaz")
    parser.add_argument("--dot", type=Path, help="Graphviz çıktısı")
    args = parser.parse_args(argv)
    scan = arch_scan.scan(args.root)
    if args.dot:
        args.dot.write_text(dot(scan) + "\n", encoding="utf-8")
    text = markdown(scan) + "\n"
    if args.write:
        (args.root / OUT).write_text(text, encoding="utf-8")
        print(f"yazıldı: {OUT}")
    elif not args.dot:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
