"""Modül manifesti (`module.yaml`) — okuma ve doğrulama.

Şemanın tek kaynağı `docs/schemas/module.schema.json`. Burada şema yeniden
yazılmaz; okunur ve uygulanır. Geçersiz manifest yalnızca o modülü düşürür,
uygulamayı düşürmez (ARCHITECTURE.md §5).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml


@dataclass(slots=True)
class Provided:
    capability: str
    contract: str
    description: str = ""


@dataclass(slots=True)
class Consumed:
    capability: str
    optional: bool = False
    reason: str = ""


@dataclass(slots=True)
class Permission:
    key: str
    description: str = ""
    scoped: bool = False
    destructive: bool = False
    default_roles: list[str] = field(default_factory=list)


@dataclass(slots=True)
class HttpSpec:
    prefix: str
    router: str
    tags: list[str] = field(default_factory=list)
    requires: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Manifest:
    id: str
    name: str
    version: str
    sdk: str
    entrypoint: str
    path: Path
    description: str = ""
    enabled: bool = True
    depends: list[str] = field(default_factory=list)
    provides: list[Provided] = field(default_factory=list)
    consumes: list[Consumed] = field(default_factory=list)
    permissions: list[Permission] = field(default_factory=list)
    http: HttpSpec | None = None
    ui: dict[str, Any] = field(default_factory=dict)
    migrations: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def permission_keys(self) -> set[str]:
        return {permission.key for permission in self.permissions}


class ManifestError(ValueError):
    """Manifest okunamadı veya şemaya uymuyor."""


def _load_schema(schema_path: Path) -> jsonschema.Draft202012Validator:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def read_manifest(module_dir: Path, schema_path: Path) -> Manifest:
    manifest_file = module_dir / "module.yaml"
    try:
        data = yaml.safe_load(manifest_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as error:
        raise ManifestError(f"{module_dir.name}: manifest okunamadı — {error}") from error

    validator = _load_schema(schema_path)
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.path) or "kök"
        raise ManifestError(f"{module_dir.name}: şemaya uymuyor ({where}): {first.message}")

    if data["id"] != module_dir.name:
        raise ManifestError(
            f"{module_dir.name}: manifest kimliği '{data['id']}' klasör adıyla aynı değil."
        )

    # İzin anahtarı modülün kendi önekini taşımak zorundadır (K10'un veri tarafı).
    for permission in data.get("permissions") or []:
        if not permission["key"].startswith(f"{data['id']}."):
            raise ManifestError(
                f"{data['id']}: '{permission['key']}' izni modül kimliğiyle başlamalı."
            )

    http_data = data.get("http") or None
    return Manifest(
        id=data["id"],
        name=data["name"],
        version=data["version"],
        sdk=data["sdk"],
        entrypoint=data["entrypoint"],
        path=module_dir,
        description=data.get("description", ""),
        enabled=bool(data.get("enabled", True)),
        depends=list(data.get("depends") or []),
        provides=[Provided(**entry) for entry in (data.get("provides") or [])],
        consumes=[Consumed(**entry) for entry in (data.get("consumes") or [])],
        permissions=[Permission(**entry) for entry in (data.get("permissions") or [])],
        http=HttpSpec(
            prefix=http_data["prefix"],
            router=http_data["router"],
            tags=list(http_data.get("tags") or []),
            requires=list(http_data.get("requires") or []),
        ) if http_data and http_data.get("router") else None,
        ui=data.get("ui") or {},
        migrations=data.get("migrations"),
        raw=data,
    )
