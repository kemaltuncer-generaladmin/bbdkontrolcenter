#!/usr/bin/env python3
"""Kabuk menüsünün okuduğu kaydı üretir.

Kabuk modül adı bilmez (K1'in arayüz tarafındaki karşılığı). Menüyü
`modules/*/module.yaml` içindeki `ui.nav` bloklarından türetiriz; bu betik o
blokları toplayıp `apps/desktop/shell/registry.json` dosyasına yazar.

GEÇİCİ OLAN NE: kaydın kaynağı. Çekirdek ayağa kalkınca `ui-kernel` aynı
JSON'u sidecar'ın modül kayıt ucundan çekecek; bu betik o gün silinir.
Manifest formatı, menü mantığı ve kabuk kodu değişmez.

Kullanım:
    python3 tools/build-ui-registry.py [--check]

    --check   dosyayı yazmaz, yalnızca doğrular (CI için)
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
SCHEMA = ROOT / "docs" / "schemas" / "module.schema.json"
SHELL = ROOT / "apps" / "desktop" / "shell"
OUT = SHELL / "registry.json"

# Modül panelleri buraya kopyalanır: webview yalnızca `shell/` kökünü görüyor,
# paket toplayıcı da yok. Kaynak her zaman modülün kendi klasörüdür (K6);
# burası üretilen çıktıdır, git dışıdır.
PANELS_OUT = SHELL / "panels"

# Menü gruplarının sırası. Grup = kurumsal yapı, modül adı değil.
# Listede olmayan grup sona alfabetik eklenir.
GROUP_ORDER = ["BBD", "BBD Store", "BLD", "Kurumsal"]

# Çekirdeğin kendi ekranları. Bunlar modül DEĞİLDİR ve silinemez: kimlik,
# ayar ve sistem sağlığı çekirdeğe aittir (CLAUDE.md — kavram ayrımı,
# ADR 0007). Menüde modüllerle aynı biçimde görünürler.
CORE_PANELS = [
    {
        "id": "core_users",
        "title": "Kullanıcı Yönetimi",
        "icon": "users",
        "group": "Kurumsal",
        "order": 10,
        "requires": ["users.view"],
    },
    {
        "id": "core_settings",
        "title": "Sistem Ayarları",
        "icon": "sliders",
        "group": "Kurumsal",
        "order": 20,
        "requires": ["settings.view"],
    },
    {
        "id": "core_health",
        "title": "Sistem Sağlığı",
        "icon": "pulse",
        "group": "Kurumsal",
        "order": 30,
        "requires": ["settings.view"],
    },
]


def load_schema_validator():
    """Şema doğrulayıcı. jsonschema yoksa doğrulama atlanır, üretim sürer."""
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def copy_panel(module_dir: pathlib.Path, entry: str | None) -> str | None:
    """Modülün panel klasörünü servis köküne kopyalar, giriş yolunu döndürür.

    Panel yoksa None döner: ekran menüde görünür, gövdesi boş kalır. Modüller
    aşama aşama kodlandığı için normal durum budur.
    """
    if not entry:
        return None

    source_file = module_dir / entry
    if not source_file.is_file():
        return None

    source_dir = source_file.parent
    target_dir = PANELS_OUT / module_dir.name
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    return f"panels/{module_dir.name}/{source_file.name}"


def collect_panels(validator) -> tuple[list[dict], list[str]]:
    panels: list[dict] = []
    problems: list[str] = []

    for manifest_path in sorted(MODULES.glob("*/module.yaml")):
        folder = manifest_path.parent.name
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{folder}: manifest okunamadı — {exc}")
            continue

        # Geçersiz manifest yalnızca o modülü düşürür, üretimi durdurmaz.
        if validator is not None:
            errors = sorted(validator.iter_errors(manifest), key=lambda e: e.path)
            if errors:
                problems.append(f"{folder}: {errors[0].message}")
                continue

        if manifest.get("id") != folder:
            problems.append(f"{folder}: id ('{manifest.get('id')}') klasör adıyla aynı değil")
            continue

        nav = (manifest.get("ui") or {}).get("nav")
        if not nav:
            continue  # ekranı olmayan modül — menüde yeri yok

        panel = {
            "id": manifest["id"],
            "title": nav.get("title") or manifest.get("name", manifest["id"]),
            "icon": nav.get("icon", "dot"),
            "group": nav.get("group", "Diğer"),
            "order": nav.get("order", 1000),
            "requires": nav.get("requires", []),
            "source": "module",
            "enabled": bool(manifest.get("enabled", True)),
            # Modülün ilan ettiği yetenekler. Kabuk yalnızca burada yazanı
            # kayda alır: sözleşme manifesttir (K3).
            "provides": [entry["capability"] for entry in manifest.get("provides") or []],
        }

        entry = copy_panel(manifest_path.parent, (manifest.get("ui") or {}).get("entry"))
        if entry:
            panel["entry"] = entry

        panels.append(panel)

    for panel in CORE_PANELS:
        panels.append({**panel, "source": "core", "enabled": True, "provides": []})

    return panels, problems


def group_key(name: str) -> tuple[int, str]:
    return (GROUP_ORDER.index(name), "") if name in GROUP_ORDER else (len(GROUP_ORDER), name)


def build(*, copy_panels: bool = True) -> dict:
    """Kayıt defterini üretir. `copy_panels=False` iken DİSKE DOKUNMAZ.

    BULUNAN HATA (2026-08-15). `--check` "dosyayı yazmaz" diye belgelenmişti
    ama `build()` koşulsuz `rmtree(PANELS_OUT)` yapıp panelleri yeniden
    kopyalıyordu. Sentinel dosyayla kanıtlandı: `--check` çalıştırınca
    `shell/panels/` altına konan dosya siliniyordu.

    CI'da salt-okunur sanılan bir komutun üretilmiş panelleri silmesi, hem
    sözleşme ihlali hem de yarış kaynağı: doğrulama koşarken uygulama açıksa
    panel klasörü bir an için boşalır.
    """
    if copy_panels:
        # Silinen/yeniden adlandırılan panel artığı kalmasın.
        shutil.rmtree(PANELS_OUT, ignore_errors=True)

    validator = load_schema_validator()
    panels, problems = collect_panels(validator)

    for problem in problems:
        print(f"  atlandı — {problem}", file=sys.stderr)

    panels.sort(key=lambda p: (group_key(p["group"]), p["order"], p["title"]))
    groups = sorted({p["group"] for p in panels}, key=group_key)

    return {
        "generated_by": "tools/build-ui-registry.py",
        "groups": groups,
        "panels": panels,
    }


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    # `--check` DİSKE DOKUNMAZ: panelleri kopyalamaz, klasörü silmez.
    registry = build(copy_panels=not check_only)
    payload = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"

    if check_only:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != payload:
            print("registry.json güncel değil — betiği çalıştırın.", file=sys.stderr)
            return 1
        print("registry.json güncel.")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(payload, encoding="utf-8")
    print(f"{len(registry['panels'])} ekran, {len(registry['groups'])} grup → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
