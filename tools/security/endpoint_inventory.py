"""KM uç envanteri — her HTTP ucunun kimlik ve izin kapısı (iş 63, güvenlik denetimi).

Gerçek uygulama ayağa kaldırılır (geçici SQLite, ağsız) ve FastAPI'nin rota
tablosu gezilir; her uç için şunlar çıkarılır:

  · yöntem + yol, ucu tanımlayan modül (`km_mod_<id>` → `<id>`, çekirdek → `core`),
  · kimlik isteyip istemediği (oturum / servis anahtarı kapısı),
  · izin grupları: router'ın taban izni (`module.yaml` → `http.requires`) VE
    ucun kendi `requires(...)`ları — grup içinde BİRİ yeter, gruplar arasında HEPSİ,
  · gövdede ayrıca `has_permission(...)` denetimi olup olmadığı,
  · step-up: `pin` (PIN teyidi — `confirm_pin` / vekil step-up belirteci) ya da
    `gerekçe` (ADR 0012 — mağazanın gerekçeli onayı),
  · yazma mı (GET/HEAD/OPTIONS dışı).

İzinler kapıların ÜSTÜNDEKİ işaretten okunur (`km_core/http/deps.py` →
`KM_PERMISSIONS_ATTR`); tahmin yapılmaz. Gövde ve step-up sütunları kaynak
metninden çıkarılır — bilgilendirmedir, karar testlerdedir
(`tests/core/test_guvenlik_envanteri.py`).

Envanter SUNUCUNUN görünümüdür: platform `linux` sayılır (canlı kap Linux'ta
koşar), `enabled: false` modüller yüklenmez ve envantere girmez — canlıda da
uçları yoktur.

Kullanım (depo kökünden):

    .venv/bin/python tools/security/endpoint_inventory.py           # docs/security/endpoint-envanteri.md
                                                                    # + docs/permissions.md izin kataloğu bloğu
    .venv/bin/python tools/security/endpoint_inventory.py --check   # belge eskiyse çıkış kodu 1
"""

from __future__ import annotations

import argparse
import inspect
import re
import sys
import tempfile
from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "security" / "endpoint-envanteri.md"
PERMISSIONS_DOC = ROOT / "docs" / "permissions.md"
CATALOG_BEGIN = "<!-- izin-katalogu:basla — tools/security/endpoint_inventory.py üretir, elle düzenlenmez -->"
CATALOG_END = "<!-- izin-katalogu:bitir -->"

READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
PERMISSIONS_ATTR = "km_permissions"
AUTHENTICATED_ATTR = "km_authenticated"

_PIN = re.compile(r"(confirm_pin|step_up)\w*\(|\bstep_up_token\b|\bSTEP_UP_HEADER\b")
_REASON = re.compile(r"\breason\s*=\s*(body|payload|data)\b")
_BODY_CHECK = re.compile(r"\.has_permission\(")


@dataclass(frozen=True, slots=True)
class Endpoint:
    method: str
    path: str
    module: str
    authenticated: bool
    groups: tuple[tuple[str, ...], ...]
    body_check: bool
    step_up: str
    handler: str

    @property
    def write(self) -> bool:
        return self.method not in READ_METHODS

    @property
    def permissions(self) -> set[str]:
        return {entry.partition(":")[0] for group in self.groups for entry in group}

    @property
    def key(self) -> tuple[str, str]:
        return (self.method, self.path)


# ------------------------------------------------------------------ uygulama


def _ensure_path() -> None:
    for entry in (ROOT / "backend" / "src", ROOT):
        if str(entry) not in sys.path:
            sys.path.insert(0, str(entry))


def build_config(workdir: Path) -> Any:
    _ensure_path()
    from km_core.config.loader import ROOT as CONFIG_ROOT
    from km_core.config.loader import Config, load_config

    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(workdir / "km.sqlite"),
        "secret_key_path": str(workdir / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": "482913"}
    return Config(data, root=CONFIG_ROOT)


def collect(app: Any) -> list[Endpoint]:
    """Ayakta olan (lifespan'i koşmuş) uygulamanın uçları."""
    from fastapi.routing import APIRoute

    found: dict[tuple[str, str], Endpoint] = {}
    for path, route, include_deps in _walk(app.router.routes, "", ()):
        if not isinstance(route, APIRoute):
            continue
        calls = [dep.dependency for dep in include_deps if dep.dependency is not None]
        calls += list(_calls(route.dependant))
        groups: list[tuple[str, ...]] = []
        authenticated = False
        for call in calls:
            perms = getattr(call, PERMISSIONS_ATTR, None)
            if perms:
                authenticated = True
                group = tuple(str(item) for item in perms)
                if group not in groups:
                    groups.append(group)
            if getattr(call, AUTHENTICATED_ATTR, False):
                authenticated = True
        source = _source(route.endpoint)
        step_up = "pin" if _PIN.search(source) else ("gerekçe" if _REASON.search(source) else "")
        for method in sorted(route.methods or ()):
            item = Endpoint(
                method=method,
                path=path,
                module=_module_of(route.endpoint),
                authenticated=authenticated,
                groups=tuple(groups),
                body_check=bool(_BODY_CHECK.search(source)),
                step_up=step_up,
                handler=f"{route.endpoint.__module__}.{route.endpoint.__qualname__}",
            )
            # Aynı yöntem+yol iki kez tanımlıysa ilk eşleşen kazanır (Starlette sırası).
            found.setdefault(item.key, item)
    return sorted(found.values(), key=lambda e: (e.module, e.path, e.method))


def _walk(routes: Sequence[Any], prefix: str, deps: tuple[Any, ...]) -> Iterator[tuple[str, Any, tuple[Any, ...]]]:
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":  # FastAPI ≥ 0.140 tembel dahil etme
            context = route.include_context
            yield from _walk(route.original_router.routes, prefix + str(context.prefix or ""),
                             deps + tuple(context.dependencies or ()))
        elif hasattr(route, "dependant"):
            yield prefix + route.path, route, deps


def _calls(dependant: Any) -> Iterator[Any]:
    for child in dependant.dependencies:
        if child.call is not None:
            yield child.call
        yield from _calls(child)


def _source(func: Any) -> str:
    try:
        return inspect.getsource(inspect.unwrap(func))
    except (OSError, TypeError):
        return ""


def _module_of(func: Any) -> str:
    name = str(getattr(func, "__module__", "") or "")
    if name.startswith("km_mod_"):
        return name[len("km_mod_"):].split(".", 1)[0]
    if name.startswith("km_platform."):
        return "platform"
    return "core"


def inventory(workdir: Path | None = None) -> tuple[list[Endpoint], list[dict[str, Any]]]:
    """Uygulamayı kurar, uçları ve modül raporunu döndürür."""
    _ensure_path()
    from fastapi.testclient import TestClient

    import km_core.kernel.kernel as kernel_module
    from km_core.http.app import create_app

    original = kernel_module.current_platform
    kernel_module.current_platform = lambda: "linux"  # sunucu görünümü (ADR 0026)
    try:
        with tempfile.TemporaryDirectory(prefix="km-envanter-") as tmp:
            app = create_app(build_config(Path(workdir or tmp)))
            with TestClient(app):
                return collect(app), app.state.kernel.report()
    finally:
        kernel_module.current_platform = original


# ------------------------------------------------------------------ belge


def _group_text(groups: tuple[tuple[str, ...], ...]) -> str:
    if not groups:
        return "—"
    return " ∧ ".join("(" + " ∨ ".join(f"`{p}`" for p in group) + ")" if len(group) > 1
                      else f"`{group[0]}`" for group in groups)


def render(endpoints: list[Endpoint], modules: list[dict[str, Any]]) -> str:
    total = len(endpoints)
    writes = sum(1 for e in endpoints if e.write)
    open_ = [e for e in endpoints if not e.authenticated]
    pinned = sum(1 for e in endpoints if e.step_up == "pin")
    reasoned = sum(1 for e in endpoints if e.step_up == "gerekçe")
    loaded = sorted(m["id"] for m in modules if m.get("state") == "loaded")
    missing = sorted(f"{m['id']} ({m.get('state')})" for m in modules if m.get("state") != "loaded")

    lines = [
        "# KM uç envanteri",
        "",
        "ÜRETİLEN BELGE — elle düzenlenmez. Üretici: `tools/security/endpoint_inventory.py`",
        "(denetim: `tests/core/test_guvenlik_envanteri.py`). Sunucu görünümü: platform `linux`,",
        "yalnız yüklenen modüller. Model ve kurallar: [README.md](README.md).",
        "",
        (f"- Uç: **{total}** · yazma: **{writes}** · kimliksiz: **{len(open_)}** · "
        f"PIN step-up: **{pinned}** · gerekçeli onay (ADR 0012): **{reasoned}**"),
        f"- Yüklü modüller ({len(loaded)}): " + ", ".join(f"`{m}`" for m in loaded),
        f"- Envanterde olmayan modüller ({len(missing)}): " + (", ".join(missing) or "—"),
        "",
        "Sütunlar: **İzin** — gruplar arasında ∧ (hepsi), grup içinde ∨ (biri yeter); ilk grup",
        "çoğunlukla router'ın taban iznidir (`module.yaml` → `http.requires`). **Gövde** — uç",
        "gövdesinde ek `has_permission` denetimi var. **Step-up** — `pin` (PIN teyidi) ya da",
        "`gerekçe` (ADR 0012). **Kimlik** — `—` ise uç oturum istemez; gerekçesi",
        "`tests/core/test_guvenlik_envanteri.py` → `PUBLIC_ENDPOINTS`.",
        "",
    ]
    current = None
    for item in endpoints:
        if item.module != current:
            current = item.module
            count = sum(1 for e in endpoints if e.module == current)
            lines += ["", f"## {current} ({count})", "",
                      "| Yöntem | Yol | İzin | Gövde | Step-up | Kimlik |",
                      "|---|---|---|---|---|---|"]
        lines.append(
            f"| {item.method} | `{item.path}` | {_group_text(item.groups)} | "
            f"{'evet' if item.body_check else ''} | {item.step_up} | "
            f"{'oturum' if item.authenticated else '—'} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def render_catalog(modules: list[dict[str, Any]]) -> str:
    """`docs/permissions.md` içindeki tam modül izni kataloğu (manifestlerden)."""
    lines = [
        CATALOG_BEGIN,
        "",
        "## Modül izinleri — tam katalog (üretilir)",
        "",
        "Kaynak: her modülün `module.yaml` → `permissions`. Keşfedilen HER modül (kapalılar dahil:",
        "izin manifestte ilan edilir, rollere düşer). Yıkıcı = `destructive: true` — uçta PIN",
        "teyidi ya da gerekçeli onay beklenir (`tests/core/test_guvenlik_envanteri.py`).",
        "",
        "| İzin | Modül | Durum | Yıkıcı | Varsayılan roller | Açıklama |",
        "|---|---|---|---|---|---|",
    ]
    for module in sorted(modules, key=lambda m: str(m["id"])):
        for perm in sorted(module.get("permissions") or [], key=lambda p: str(p["key"])):
            text = " ".join(str(perm.get("description") or "").split()).replace("|", "/")
            roles = ", ".join(f"`{r}`" for r in perm.get("default_roles") or []) or "—"
            lines.append(
                f"| `{perm['key']}` | `{module['id']}` | {module.get('state')} | "
                f"{'evet' if perm.get('destructive') else ''} | {roles} | {text} |"
            )
    lines += ["", CATALOG_END]
    return "\n".join(lines) + "\n"


def with_catalog(document: str, catalog: str) -> str:
    """Belgedeki üretilen bloğu yeniler (yoksa sona ekler)."""
    if CATALOG_BEGIN in document and CATALOG_END in document:
        head = document[: document.index(CATALOG_BEGIN)]
        tail = document[document.index(CATALOG_END) + len(CATALOG_END):].lstrip("\n")
        return head + catalog + ("\n" + tail if tail else "")
    return document.rstrip("\n") + "\n\n" + catalog


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="belge güncel değilse 1 ile çık")
    args = parser.parse_args(argv)
    endpoints, modules = inventory()
    text = render(endpoints, modules)
    permissions_doc = PERMISSIONS_DOC.read_text(encoding="utf-8")
    catalog_doc = with_catalog(permissions_doc, render_catalog(modules))
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        stale = [path for path, now, want in ((OUTPUT, current, text),
                                              (PERMISSIONS_DOC, permissions_doc, catalog_doc))
                 if now != want]
        for path in stale:
            print(f"{path.relative_to(ROOT)} eski — betiği --check olmadan koşturun.")
        return 1 if stale else 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(text, encoding="utf-8")
    PERMISSIONS_DOC.write_text(catalog_doc, encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)}: {len(endpoints)} uç; {PERMISSIONS_DOC.relative_to(ROOT)}: katalog")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
