"""Katmanlı ayar yükleme (ARCHITECTURE.md §7).

Öncelik — sonraki öncekini ezer:

    config/default.yaml
      → config/environments/<env>.yaml
      → config/local.yaml            (git dışı, sır burada)
      → ortam değişkeni              (KM__server__port biçiminde)

Sır depoya yazılmaz (K8): `local.yaml` .gitignore'dadır ve kasanın da
dayanağıdır.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml

# backend/src/km_core/config/loader.py → depo kökü
ROOT = Path(__file__).resolve().parents[4]
CONFIG_DIR = ROOT / "config"

ENV_PREFIX = "KM__"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Sözlükleri iç içe birleştirir; liste ve skaler değerler EZİLİR."""
    result = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _coerce(text: str) -> Any:
    """Ortam değişkeni metnini YAML kurallarıyla çözer: '8787' → int, 'true' → bool."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _env_overlay() -> dict[str, Any]:
    """`KM__server__port=9000` → {'server': {'port': 9000}}."""
    overlay: dict[str, Any] = {}
    for name, value in os.environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        path = name[len(ENV_PREFIX):].split("__")
        cursor = overlay
        for part in path[:-1]:
            cursor = cursor.setdefault(part.lower(), {})
        cursor[path[-1].lower()] = _coerce(value)
    return overlay


class Config:
    """Okunur ayar görünümü. `get("server.port", 8787)` biçiminde sorgulanır."""

    def __init__(self, data: dict[str, Any], root: Path = ROOT) -> None:
        self._data = data
        self.root = root

    def get(self, path: str, default: Any = None) -> Any:
        cursor: Any = self._data
        for part in path.split("."):
            if not isinstance(cursor, dict) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    def section(self, path: str) -> dict[str, Any]:
        value = self.get(path, {})
        return value if isinstance(value, dict) else {}

    def module_config(self, module_id: str, defaults: dict[str, Any] | None = None) -> dict[str, Any]:
        """Modül ayarı: kendi default'u + kök ayardaki `modules.<id>` bloğu."""
        return _deep_merge(defaults or {}, self.section(f"modules.{module_id}"))

    def path(self, path: str, default: str) -> Path:
        """Ayardaki göreli yolu depo köküne göre çözer."""
        value = str(self.get(path, default))
        candidate = Path(value)
        return candidate if candidate.is_absolute() else self.root / candidate

    def as_dict(self) -> dict[str, Any]:
        return self._data


def secure_local_file(path: Path) -> bool:
    """`local.yaml`'ı yalnız sahibine okunur hâle getirir.

    Bu dosya K8 gereği sırların durduğu yerdir (kasa anahtarı yolu, açılış
    PIN'i, sunucu erişimleri). Elle oluşturulduğu için tipik olarak 0644/0664
    ile doğar ve makinedeki her kullanıcı okur. Uyarı vermek yetmez —
    okunmadan ÖNCE daraltılır. Dönüş: izin gerçekten değiştirildi mi.
    """
    try:
        if not path.is_file():
            return False
        current = stat.S_IMODE(path.stat().st_mode)
        if not current & 0o077:
            return False
        path.chmod(current & 0o700)
    except OSError:
        # İzin düzeltilemiyorsa (salt okunur bağlama) açılışı düşürmeyiz;
        # yapılandırma yine okunur, yalnız daraltma yapılamamıştır (K7).
        return False
    return True


def load_config(env: str | None = None) -> Config:
    env = env or os.environ.get("KM_ENV", "")

    secure_local_file(CONFIG_DIR / "local.yaml")

    data = _read_yaml(CONFIG_DIR / "default.yaml")
    if env:
        data = _deep_merge(data, _read_yaml(CONFIG_DIR / "environments" / f"{env}.yaml"))
    data = _deep_merge(data, _read_yaml(CONFIG_DIR / "local.yaml"))
    data = _deep_merge(data, _env_overlay())

    return Config(data)
