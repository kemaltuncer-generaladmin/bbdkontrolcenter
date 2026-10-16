"""Mimari tarayıcı — modül sınırlarını ve alan haritasını KODDAN okur (ADR 0033).

İki aracın ortak kütüphanesi:
  · `tools/check-boundaries.py` — sınır ihlali kapısı (CI + pytest)
  · `tools/module-graph.py`     — envanter ve bağımlılık grafiği

Kaynak kod çalıştırılmaz; dosyalar `ast` ve düzenli ifadeyle okunur. Bu yüzden
araç modül bağımlılıkları kurulu olmayan bir makinede de koşar ve bir modülün
patlaması taramayı düşürmez.

Kurallar (her biri bir `Finding.rule`):

  module-import   K3 — modül başka bir modülün kodunu import eder
                  (Python `modules.<id>` / `km_mod_<id>`, panelde `./` ve
                  `../../ui-kit/` dışındaki her yerel import).
  core-import     K2 — modülün üretim kodu `km_core` / `km_platform` import eder.
  foreign-table   K5 — modülün SQL'i başka bir modülün `mod_<id>_*` tablosuna dokunur.
  core-table      K5 — modülün SQL'i çekirdeğin tablosuna (`users`, `outputs` …)
                  doğrudan dokunur.
  foreign-api     Modülün kodu başka bir modülün HTTP önekini (`/api/<id>`)
                  çağırır — alanlar arası iletişim yetenek/olay ile olur.
  core-knows-module  K1 — `km_core`/`km_platform`/`km_sdk` kodunda (belge
                  dizgisi ve yorum DIŞINDA) bir modül kimliği geçer.
  naming          ADR 0033 §3 — sağlanan yetenek / yayımlanan olay adı modül
                  kimliği ya da alanının öneklerinden biriyle başlamaz;
                  HTTP öneki `/api/<modül kimliği>` değildir.
  manifest        `domain` / `data_classification` eksik, katalogda yok ya da
                  sınıf alanın üst sınırını aşıyor.

Testler (`*/tests/*`) taranmaz: test sahtesi başka modülün adını taşıyabilir.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOMAINS_FILE = Path("docs") / "architecture" / "domains.yaml"
EXCEPTIONS_FILE = Path("tools") / "boundary-exceptions.yaml"

#: Taranan kaynak uzantıları.
PY = ".py"
JS = ".js"
SQL = ".sql"

#: Sınıf sırası — sonraki öncekinden yüksektir.
CLASS_ORDER = ["public", "internal", "personal", "financial"]

#: Panel importlarında izinli önekler: kendi klasörü ve kabuğun ortak kiti
#: (ADR 0011). Panel kabukta `shell/panels/<id>/` altına kopyalanır; `../../ui-kit/`
#: oradan `shell/ui-kit/`e çıkar.
UI_ALLOWED_IMPORT = ("./", "../../ui-kit/")

SQL_KEYWORD = r"(?:FROM|JOIN|INTO|UPDATE|TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?)"
_SQL_WORDS = frozenset({"if", "not", "exists", "as", "select", "from", "where", "table"})


# ------------------------------------------------------------------ veri


@dataclass(slots=True)
class Module:
    id: str
    path: Path
    manifest: dict[str, Any]

    @property
    def enabled(self) -> bool:
        return bool(self.manifest.get("enabled", True))

    @property
    def domain(self) -> str:
        return str(self.manifest.get("domain") or "")

    @property
    def classification(self) -> str:
        return str(self.manifest.get("data_classification") or "")

    @property
    def name(self) -> str:
        return str(self.manifest.get("name") or self.id)

    @property
    def http_prefix(self) -> str:
        return str((self.manifest.get("http") or {}).get("prefix") or "")

    @property
    def depends(self) -> list[str]:
        return list(self.manifest.get("depends") or [])

    @property
    def provides(self) -> list[str]:
        return [entry["capability"] for entry in self.manifest.get("provides") or []]

    @property
    def consumes(self) -> list[tuple[str, bool]]:
        return [(entry["capability"], bool(entry.get("optional", False)))
                for entry in self.manifest.get("consumes") or []]

    @property
    def publishes(self) -> list[str]:
        return [entry["name"] for entry in (self.manifest.get("events") or {}).get("publishes") or []]

    @property
    def subscribes(self) -> list[str]:
        return [entry["name"] for entry in (self.manifest.get("events") or {}).get("subscribes") or []]


@dataclass(slots=True, frozen=True)
class Finding:
    rule: str
    module: str
    target: str
    file: str
    line: int
    detail: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        """İstisna eşleme anahtarı. Satır numarası bilerek YOK: dosya düzenlenince
        istisna kaymamalı."""
        return (self.rule, self.module, self.target)


@dataclass(slots=True)
class Catalog:
    domains: dict[str, dict[str, Any]]
    classifications: dict[str, str]


@dataclass(slots=True)
class Scan:
    root: Path
    modules: dict[str, Module]
    catalog: Catalog
    findings: list[Finding] = field(default_factory=list)


# --------------------------------------------------------------- okuma


def load_catalog(root: Path = ROOT) -> Catalog:
    data = yaml.safe_load((root / DOMAINS_FILE).read_text(encoding="utf-8")) or {}
    return Catalog(domains=dict(data.get("domains") or {}),
                   classifications=dict(data.get("classifications") or {}))


def load_modules(root: Path = ROOT) -> dict[str, Module]:
    modules: dict[str, Module] = {}
    for manifest_path in sorted((root / "modules").glob("*/module.yaml")):
        data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        module_id = str(data.get("id") or manifest_path.parent.name)
        modules[module_id] = Module(id=module_id, path=manifest_path.parent, manifest=data)
    return modules


def source_files(module: Module, suffixes: tuple[str, ...] = (PY, JS, SQL)) -> Iterator[Path]:
    for path in sorted(module.path.rglob("*")):
        if not path.is_file() or path.suffix not in suffixes:
            continue
        parts = path.relative_to(module.path).parts
        if "tests" in parts or "node_modules" in parts or "__pycache__" in parts:
            continue
        yield path


def count_lines(path: Path) -> int:
    try:
        return path.read_text(encoding="utf-8", errors="replace").count("\n")
    except OSError:
        return 0


# --------------------------------------------- python: dizgi ve import


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """Modül/sınıf/fonksiyon belge dizgilerinin `id()` kümesi."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                found.add(id(body[0].value))
    return found


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """Modül düzeyindeki `AD = "değer"` dizgi sabitleri (tablo adı sabitleri)."""
    found: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        value = getattr(node, "value", None)
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            for target in targets:
                if isinstance(target, ast.Name):
                    found[target.id] = value.value
    return found


def python_strings(source: str) -> list[tuple[int, str]]:
    """Belge dizgisi OLMAYAN her dizgi sabiti, SQL yorumları (`-- …`) atılmış.

    f-dizginin sabit parçaları birleştirilir. Yer tutucu modül düzeyinde bir
    dizgi sabitine çözülüyorsa (`OUTPUTS = "outputs"` → `f"FROM {OUTPUTS}"`)
    değeri yazılır, çözülmüyorsa `{}` olur. Böylece tablo adını sabite koyan
    SQL de okunur; sabitin kendisi (ör. yetenek adı `"secrets"`) SQL sayılmaz.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    docstrings = _docstring_nodes(tree)
    constants = _module_constants(tree)
    inside_fstring: set[int] = set()
    out: list[tuple[int, str]] = []

    def piece(part: ast.expr) -> str:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            return part.value
        if isinstance(part, ast.FormattedValue) and isinstance(part.value, ast.Name) \
                and part.value.id in constants:
            return constants[part.value.id]
        return "{}"

    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                inside_fstring.add(id(part))
            out.append((node.lineno, "".join(piece(part) for part in node.values)))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings and id(node) not in inside_fstring:
            out.append((node.lineno, node.value))
    return [(line, sql_code(text)) for line, text in out]


def python_imports(source: str) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.lineno, node.module))
    return out


# --------------------------------------------------------- js ve sql

_JS_IMPORT = re.compile(r"""(?:\bimport\s*(?:[\w*{}\s,]+?\s*from\s*)?|\bimport\s*\(\s*|\bexport\s*[\w*{}\s,]+?\s*from\s*)['"]([^'"]+)['"]""")
_JS_LINE_COMMENT = re.compile(r"(^|[^:\\])//.*$", re.MULTILINE)
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_SQL_COMMENT = re.compile(r"--.*$", re.MULTILINE)


def _keep_lines(pattern: re.Pattern[str], text: str) -> str:
    """Yorumu siler ama satır sayısını korur (bulgu satırı doğru kalsın)."""
    return pattern.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def js_code(source: str) -> str:
    return _JS_LINE_COMMENT.sub(lambda m: m.group(1), _keep_lines(_BLOCK_COMMENT, source))


def js_imports(source: str) -> list[tuple[int, str]]:
    code = js_code(source)
    return [(code.count("\n", 0, match.start()) + 1, match.group(1)) for match in _JS_IMPORT.finditer(code)]


def sql_code(source: str) -> str:
    return _SQL_COMMENT.sub("", _keep_lines(_BLOCK_COMMENT, source))


def text_units(path: Path) -> list[tuple[int, str]]:
    """Dosyanın 'kod' parçaları (satır, metin): Python'da dizgi sabitleri,
    SQL/JS'de yorumu atılmış satırlar."""
    source = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == PY:
        return python_strings(source)
    code = sql_code(source) if path.suffix == SQL else js_code(source)
    return [(number, line) for number, line in enumerate(code.splitlines(), start=1) if line.strip()]


# ------------------------------------------------------------- yardım


def core_tables(root: Path = ROOT) -> set[str]:
    """Çekirdek/platform şemasının tablo adları (`CREATE TABLE` taraması)."""
    names: set[str] = set()
    pattern = re.compile(r"CREATE\s+TABLE(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-z_][a-z0-9_]*)", re.IGNORECASE)
    constants = re.compile(r"^([A-Z_]+_TABLE)\s*=\s*['\"]([a-z_][a-z0-9_]*)['\"]", re.MULTILINE)
    for path in (root / "backend" / "src").rglob("*"):
        if path.suffix not in (PY, SQL) or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        names.update(match.group(1).lower() for match in pattern.finditer(text))
        names.update(match.group(2) for match in constants.finditer(text))
    # `CREATE TABLE IF NOT EXISTS {AD}` biçiminde ad yer tutucudur; düzenli
    # ifade o zaman `if` sözcüğünü yakalar — SQL sözcükleri ad değildir.
    return {name for name in names if not name.startswith("mod_") and name not in _SQL_WORDS}


def owner_of_table(table: str, ids_longest_first: list[str]) -> str | None:
    """`mod_bbd_canteen_api_audit` → `bbd_canteen_api` (en uzun eşleşen kimlik)."""
    for module_id in ids_longest_first:
        if table.startswith(f"mod_{module_id}_") or table == f"mod_{module_id}":
            return module_id
    return None


def _prefix_head(name: str) -> str:
    return re.split(r"[._]", name, maxsplit=1)[0]


def name_allowed(name: str, module: Module, catalog: Catalog) -> bool:
    if name.startswith(f"{module.id}."):
        return True
    prefixes = (catalog.domains.get(module.domain) or {}).get("prefixes") or []
    for prefix in prefixes:
        if name.startswith((f"{prefix}.", f"{prefix}_")):
            return True
    return False


# --------------------------------------------------------------- tarama


def scan(root: Path = ROOT) -> Scan:
    modules = load_modules(root)
    catalog = load_catalog(root)
    result = Scan(root=root, modules=modules, catalog=catalog)
    ids_longest_first = sorted(modules, key=len, reverse=True)
    tables = core_tables(root)
    add = result.findings.append

    table_ref = re.compile(r"\bmod_([a-z0-9_]+)")
    core_ref = re.compile(SQL_KEYWORD + r"\s+(" + "|".join(sorted(map(re.escape, tables))) + r")\b",
                          re.IGNORECASE) if tables else None
    api_ref = re.compile(r"/api/([a-z0-9_]+)")

    for module in modules.values():
        def rel(path: Path) -> str:
            return path.relative_to(root).as_posix()

        # ---------------------------------------------------- manifest
        if not module.domain:
            add(Finding("manifest", module.id, "domain", rel(module.path / "module.yaml"), 0,
                        "`domain` alanı yok"))
        elif module.domain not in catalog.domains:
            add(Finding("manifest", module.id, f"domain:{module.domain}",
                        rel(module.path / "module.yaml"), 0, "alan katalogda yok"))
        if not module.classification:
            add(Finding("manifest", module.id, "data_classification",
                        rel(module.path / "module.yaml"), 0, "`data_classification` alanı yok"))
        elif module.classification not in catalog.classifications:
            add(Finding("manifest", module.id, f"data_classification:{module.classification}",
                        rel(module.path / "module.yaml"), 0, "sınıf katalogda yok"))
        elif module.domain in catalog.domains:
            ceiling = catalog.domains[module.domain].get("classification")
            if ceiling in CLASS_ORDER and CLASS_ORDER.index(module.classification) > CLASS_ORDER.index(ceiling):
                add(Finding("manifest", module.id, f"data_classification:{module.classification}",
                            rel(module.path / "module.yaml"), 0,
                            f"alanın üst sınırı '{ceiling}'"))

        # ---------------------------------------------------- adlandırma
        if module.domain in catalog.domains:
            for capability in module.provides:
                if not name_allowed(capability, module, catalog):
                    add(Finding("naming", module.id, f"provides:{capability}",
                                rel(module.path / "module.yaml"), 0, "yetenek adı alan önekiyle başlamıyor"))
            for event in module.publishes:
                if not name_allowed(event, module, catalog):
                    add(Finding("naming", module.id, f"publishes:{event}",
                                rel(module.path / "module.yaml"), 0, "olay adı alan önekiyle başlamıyor"))
        if module.http_prefix and not re.match(rf"^/api/{re.escape(module.id)}(/|$)", module.http_prefix):
            add(Finding("naming", module.id, f"http:{module.http_prefix}",
                        rel(module.path / "module.yaml"), 0, "HTTP öneki /api/<modül kimliği> değil"))

        # ---------------------------------------------------- kaynak
        for path in source_files(module):
            source = path.read_text(encoding="utf-8", errors="replace")
            in_ui = "ui" in path.relative_to(module.path).parts[:1]

            if path.suffix == PY:
                for line, name in python_imports(source):
                    head = name.split(".")[0]
                    if head == "modules":
                        target = name.split(".")[1] if "." in name else "?"
                        if target != module.id:
                            add(Finding("module-import", module.id, target, rel(path), line, name))
                    elif head.startswith("km_mod_") and head[len("km_mod_"):] != module.id:
                        add(Finding("module-import", module.id, head[len("km_mod_"):], rel(path), line, name))
                    elif head in ("km_core", "km_platform"):
                        add(Finding("core-import", module.id, head, rel(path), line, name))

            if path.suffix == JS:
                for line, spec in js_imports(source):
                    if not spec.startswith("."):
                        continue  # adres ya da paket adı — yerel dosya değil
                    own_folder = spec.startswith("./") and ".." not in spec.split("/")
                    if not (own_folder or spec.startswith("../../ui-kit/")):
                        add(Finding("module-import", module.id, spec, rel(path), line, "panel importu"))

            for line, text in text_units(path):
                for match in table_ref.finditer(text):
                    owner = owner_of_table("mod_" + match.group(1), ids_longest_first)
                    if owner and owner != module.id:
                        add(Finding("foreign-table", module.id, owner, rel(path), line,
                                    "mod_" + match.group(1)))
                if core_ref is not None and path.suffix in (PY, SQL):
                    for match in core_ref.finditer(text):
                        add(Finding("core-table", module.id, match.group(1).lower(), rel(path), line,
                                    " ".join(match.group(0).split())))
                for match in api_ref.finditer(text):
                    owner = match.group(1)
                    if owner in modules and owner != module.id:
                        add(Finding("foreign-api", module.id, owner, rel(path), line,
                                    "ui" if in_ui else "backend"))

    # -------------------------------------------------------- K1: çekirdek
    word = re.compile(r"\b(" + "|".join(map(re.escape, ids_longest_first)) + r")\b") if modules else None
    for package in ("km_core", "km_platform", "km_sdk"):
        base = root / "backend" / "src" / package
        if word is None or not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            for line, text in python_strings(path.read_text(encoding="utf-8", errors="replace")):
                for match in word.finditer(text):
                    module_id = match.group(1)
                    # `print` sıradan bir kelimedir; yalnız izin/ad biçiminde sayılır.
                    if module_id == "print" and not re.search(r"\bprint[._]", text):
                        continue
                    add(Finding("core-knows-module", package, module_id,
                                path.relative_to(root).as_posix(), line, text.strip()[:80]))

    # Aynı (kural, modül, hedef, dosya, satır) bir kez.
    unique = {(f.rule, f.module, f.target, f.file, f.line): f for f in result.findings}
    result.findings = sorted(unique.values(), key=lambda f: (f.rule, f.module, f.target, f.file, f.line))
    return result


# ------------------------------------------------------------ istisnalar


@dataclass(slots=True)
class Exception_:
    rule: str
    module: str
    target: str
    reason: str
    plan: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.rule, self.module, self.target)


def load_exceptions(root: Path = ROOT) -> list[Exception_]:
    path = root / EXCEPTIONS_FILE
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Exception_(rule=str(item.get("rule", "")), module=str(item.get("module", "")),
                       target=str(item.get("target", "")), reason=str(item.get("reason", "")).strip(),
                       plan=str(item.get("plan", "")).strip())
            for item in data.get("exceptions") or []]


@dataclass(slots=True)
class Verdict:
    new: list[Finding]
    allowed: list[Finding]
    stale: list[Exception_]
    malformed: list[Exception_]

    @property
    def ok(self) -> bool:
        return not (self.new or self.stale or self.malformed)


def judge(result: Scan, exceptions: list[Exception_]) -> Verdict:
    keys = {item.key for item in exceptions}
    seen = {finding.key for finding in result.findings}
    return Verdict(
        new=[f for f in result.findings if f.key not in keys],
        allowed=[f for f in result.findings if f.key in keys],
        stale=[item for item in exceptions if item.key not in seen],
        malformed=[item for item in exceptions if not (item.reason and item.plan)],
    )


def as_json(result: Scan) -> str:
    return json.dumps([asdict(finding) for finding in result.findings], ensure_ascii=False, indent=2)
