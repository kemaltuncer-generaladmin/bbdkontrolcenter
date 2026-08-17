"""Katmanlı ayar yükleme (ARCHITECTURE.md §7, ADR 0018 §4).

Öncelik — sonraki öncekini ezer:

    config/default.yaml
      → config/environments/<env>.yaml
      → config/local.yaml            (git dışı, sır burada)
      → çekirdek deposu              (Sistem Ayarları ekranından yazılan)
      → ortam değişkeni              (KM__server__port biçiminde)

Depo katmanı `local.yaml`'ı **ezer**: arayüzden değişen ayarın elle yazılmış,
yorum taşıyan bir dosyanın üzerine yazılması yanlış olurdu (ADR 0018 §4). Bu
yüzden ekran bir değerin dosyadan gelip arayüzden ezildiğini görünür biçimde
söyleyebilmek zorundadır — katmanlar bu dosyada AYRI AYRI okunabilir durur
(`file_layers`, `env_layer`), yalnız birleşmiş sonuç değil.

Ortam değişkeni en üstte kalır: acil müdahale yolu kapanmaz. Bu yüzden depo
katmanı eklendikten SONRA ortam katmanı yeniden uygulanır (`with_store_layer`).

Sır depoya yazılmaz (K8): `local.yaml` .gitignore'dadır ve kasanın da
dayanağıdır.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import yaml

from km_core.config.paths import resolve_path

# backend/src/km_core/config/loader.py → depo kökü.
#
# Kurulu uygulamada burası deponun değil, PAKETİN köküdür (kaynak dosyaları
# oraya kopyalanır). Okunan dosyalar — `config/`, `modules/` — orada durur;
# YAZILAN dosyalar durmaz, onların adresi `paths.data_dir` ile ayrılır
# (ADR 0023).
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
        """Ayardaki yolu diskteki gerçek yola çevirir (ADR 0023).

        Çözüm `config/paths.py` içindeki TEK fonksiyondan geçer: `data/` ile
        başlayan değerler yazılabilir veri dizinine, ötekiler paket köküne
        düşer. Burada ikinci bir kural yazılmaz.
        """
        return resolve_path(str(self.get(path, default)), self.root)

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


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """`_deep_merge`in dışa açık adı — katmanları birleştiren tek kural."""
    return _deep_merge(base, overlay)


def file_layers(env: str | None = None) -> dict[str, Any]:
    """DOSYA katmanı: default + environments/<env> + local, birleşmiş hâli.

    Ortam değişkeni BİLEREK dışarıdadır: ayar ekranı "bu değer dosyadan mı,
    depodan mı, ortamdan mı geliyor" sorusunu ancak katmanlar ayrı okunabilirse
    yanıtlayabilir.
    """
    env = env or os.environ.get("KM_ENV", "")
    secure_local_file(CONFIG_DIR / "local.yaml")

    data = _read_yaml(CONFIG_DIR / "default.yaml")
    if env:
        data = _deep_merge(data, _read_yaml(CONFIG_DIR / "environments" / f"{env}.yaml"))
    return _deep_merge(data, _read_yaml(CONFIG_DIR / "local.yaml"))


def env_layer() -> dict[str, Any]:
    """ORTAM katmanı: `KM__server__port=9000` → {'server': {'port': 9000}}."""
    return _env_overlay()


def env_variable_name(path: str) -> str:
    """`platform.printer.media` → `KM__platform__printer__media`.

    Ekran, ortam değişkeniyle ezilmiş bir alanın YANINDA değişkenin adını
    yazabilsin diye burada, tek yerde türetilir; iki ayrı yerde yazılan bir ad
    kuralı zamanla ayrışırdı.
    """
    return ENV_PREFIX + "__".join(path.split("."))


def value_at(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    """`(var mı, değer)`. `None` DEĞERİ ile "hiç yok" ayrı şeylerdir."""
    cursor: Any = data
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return False, None
        cursor = cursor[part]
    return True, cursor


def nest(flat: dict[str, Any]) -> dict[str, Any]:
    """`{'a.b': 1}` → `{'a': {'b': 1}}` — deponun düz anahtarları katmana döner.

    Aynı ön ekin altında hem yaprak hem dal olan anahtar (`a` ve `a.b`) çakışır;
    böyle bir durumda DAL kazanır ve yaprak atılır. Sessiz kalmak, ayarın neden
    işlemediğini bulunamaz hâle getirirdi — çağıran bunu loglar.
    """
    tree: dict[str, Any] = {}
    for key in sorted(flat):
        parts = key.split(".")
        cursor = tree
        for part in parts[:-1]:
            branch = cursor.get(part)
            if not isinstance(branch, dict):
                branch = {}
                cursor[part] = branch
            cursor = branch
        leaf = parts[-1]
        if isinstance(cursor.get(leaf), dict):
            continue
        cursor[leaf] = flat[key]
    return tree


def with_store_layer(config: Config, store_layer: dict[str, Any]) -> Config:
    """Depo katmanını mevcut ayarın ÜSTÜNE, ortam katmanının ALTINA yerleştirir.

    `config` zaten dosya + ortam birleşimidir; depo katmanı doğrudan üzerine
    konursa ortam değişkenini de ezerdi. Bu yüzden ortam katmanı en sonda bir
    kez daha uygulanır — ADR 0018 §4'ün "ortam değişkeni en üstte kalır"
    kuralı böylece koda bağlanır.

    Verilen `config` DEĞİŞTİRİLMEZ; yeni bir görünüm döner.
    """
    data = _deep_merge(config.as_dict(), store_layer)
    data = _deep_merge(data, _env_overlay())
    return Config(data, root=config.root)


def load_config(env: str | None = None) -> Config:
    return Config(_deep_merge(file_layers(env), _env_overlay()))
