"""Ayar sekmelerinin kataloğu, çözümü ve doğrulaması (ADR 0018).

ÜÇ İŞ VAR VE ÜÇÜ DE BURADA, TEK YERDE:

1. **Katalog.** Çekirdek kendi sabit sekmelerini (Genel, Yazıcı, Güncelleme,
   Tanılama) bilir — ADR 0018 §2 bunu açıkça söyler. Geri kalan sekmeler
   modül manifestlerinden gelir ve çekirdek onları VERİ olarak okur: burada
   hiçbir modül adı geçmez, hiçbir modüle özel dal yoktur (K1). Modül
   silinince sekmesi de gider, çünkü sekme kaydın kendisinden türer.

2. **Çözüm.** Bir alanın değeri dört yerden gelebilir: alanın ilan ettiği
   varsayılan, dosya katmanı, çekirdek deposu, ortam değişkeni. Ekran "bu
   değer dosyadan geliyor, arayüzden ezildi" diyebilsin diye her alan
   KAYNAĞIYLA birlikte döner; ezilen katmanın değeri de yanında taşınır.

3. **Doğrulama.** Yazma isteği yalnız İLAN EDİLMİŞ alanlara dokunabilir ve
   değer alanın tipine uymak zorundadır. Bu, ekranın rastgele bir ayar yoluna
   yazmasını imkânsız kılar: istek gövdesi anahtar değil ALAN ADI taşır, yol
   burada katalogdan çözülür.

SIR YOKTUR (K8 · ADR 0018 §5). Şemada sır tipi tanımlı değildir; katalogda da
sır alanı bulunmaz. Şifre, token ve anahtar Kimlik Kasası ekranının işidir.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from km_core.config.loader import env_variable_name, value_at

#: Çekirdek sekmesi kimlikleri NOKTA taşır. Modül kimliği `^[a-z][a-z0-9_]*$`
#: desenindedir ve nokta içeremez; iki ad alanı böylece çakışamaz.
CORE_TAB_PREFIX = "core."

#: Ayar okumak ve yazmak için gereken çekirdek izinleri (K9).
VIEW_PERMISSION = "settings.view"
MANAGE_PERMISSION = "settings.manage"

#: Değerin geldiği katman. `default` alanın kendi ilanıdır: hiçbir katman
#: dokunmamıştır. Üçe zorlamak, "hiç yazılmamış" ile "dosyaya yazılmış"ı aynı
#: gösterirdi.
SOURCE_DEFAULT = "default"
SOURCE_FILE = "file"
SOURCE_STORE = "store"
SOURCE_ENV = "env"

_CRON = re.compile(r"^\S+(\s+\S+){4}$")


class SettingsError(ValueError):
    """Ayar yazılamadı — nedeni kullanıcıya olduğu gibi gösterilir."""


# --------------------------------------------------------------- çekirdek katalog


def core_tabs() -> list[dict[str, Any]]:
    """Çekirdeğin kendi sekmeleri.

    Alanların `path` değeri, ayarın ayar ağacındaki GERÇEK yoludur; ekran
    yalnız alan adını gönderir, yolu burası bilir. Yalnız burada listelenen
    yollara yazılabilir.

    Bu listede yer alan her alan, çalışan kodda gerçekten OKUNAN bir ayardır.
    Okunmayan bir alanı ekrana koymak, çevirdiği hiçbir şey olmayan bir düğme
    koymakla aynı şeydir.
    """
    return [
        {
            "id": "core.general",
            "title": "Genel",
            "kind": "core",
            "requires": [MANAGE_PERMISSION],
            "groups": [
                {
                    "id": "kurulum",
                    "title": "Kurulum",
                    "fields": [
                        {
                            "key": "app.name",
                            "path": "app.name",
                            "type": "text",
                            "title": "Kurulum adı",
                            "description": "Pencere başlığında ve üretilen çıktıların "
                                           "alt bilgisinde görünür.",
                            "max_length": 80,
                            "default": "Kontrol Merkezi",
                        },
                        {
                            "key": "files.output_path",
                            "path": "files.output_path",
                            "type": "path",
                            "title": "Çıktı klasörü",
                            "description": "Çekirdeğin ürettiği dosyalar (destek paketi, "
                                           "yazıcı test sayfası) buraya yazılır. Boş "
                                           "bırakılırsa masaüstündeki "
                                           "“Kontrol Merkezi/Raporlar” hiyerarşisi "
                                           "kullanılır. Modüllerin kendi rapor klasörü "
                                           "ayarı (export_path) bundan bağımsızdır.",
                            "default": "",
                        },
                    ],
                }
            ],
        },
        {
            "id": "core.printer",
            "title": "Yazıcı",
            "kind": "core",
            "requires": [MANAGE_PERMISSION],
            "groups": [
                {
                    "id": "kuyruk",
                    "title": "Kuyruk seçimi",
                    "fields": [
                        {
                            "key": "platform.printer.default_printer",
                            "path": "platform.printer.default_printer",
                            "type": "text",
                            "title": "Sabitlenen kuyruk",
                            "description": "Boş bırakılırsa kuyruk otomatik seçilir "
                                           "(önerilen). Ad yazılırsa BAĞLAYICI olur; "
                                           "tuzak kuyruk yazılırsa yine reddedilir.",
                            "max_length": 120,
                            "default": "",
                        },
                        {
                            "key": "platform.printer.usb_match",
                            "path": "platform.printer.usb_match",
                            "type": "text",
                            "title": "Ad eşleşmesi",
                            "description": "Otomatik seçimde adında bu geçen kuyruklar "
                                           "yeğlenir; termal fiş yazıcısını eler.",
                            "max_length": 60,
                            "default": "laserjet",
                        },
                        {
                            "key": "platform.printer.media",
                            "path": "platform.printer.media",
                            "type": "select",
                            "title": "Kâğıt boyutu",
                            "description": "Baskı isteğine media ve PageSize olarak "
                                           "AÇIKÇA yazılır; kullanıcının lpoptions "
                                           "varsayılanına güvenilmez.",
                            "options": ["A4", "A5", "A6", "Letter"],
                            "default": "A4",
                        },
                    ],
                }
            ],
        },
        {
            "id": "core.update",
            "title": "Güncelleme",
            "kind": "core",
            "requires": [MANAGE_PERMISSION],
            # Yazılabilir alanı YOK. Sürüm ve güncelleme durumu
            # `GET /api/settings/update` ile gelir; ekranda düğmeler ancak
            # gerçekten bir uç varsa çalışır (sahte düğme konmaz).
            "groups": [],
        },
        {
            "id": "core.diagnostics",
            "title": "Tanılama",
            "kind": "core",
            "requires": [MANAGE_PERMISSION],
            "groups": [
                {
                    "id": "gunluk",
                    "title": "Günlük",
                    "fields": [
                        {
                            "key": "core.log_level",
                            "path": "core.log_level",
                            "type": "select",
                            "title": "Günlük ayrıntı düzeyi",
                            "description": "Uygulama yeniden başlatıldığında etkinleşir.",
                            "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
                            "default": "INFO",
                        },
                        {
                            "key": "core.log_path",
                            "path": "core.log_path",
                            "type": "path",
                            "title": "Günlük dosyası",
                            "description": "Başlatıcının ve çekirdeğin yazdığı günlük. "
                                           "Aşağıdaki teknik satırlar buradan okunur.",
                            "default": "data/launcher.log",
                        },
                    ],
                }
            ],
        },
    ]


# ------------------------------------------------------------------ modül katalog


def module_defaults(module_path: Path) -> dict[str, Any]:
    """Modülün kendi `config/default.yaml` dosyası — DOSYA katmanının parçası.

    Modül ayarının zinciri kök `config/` ile bitmez: modül varsayılanını kendi
    klasöründe taşır ve kernel onu `module_config()` ile birleştirir. Ekran bu
    katmanı atlasaydı, dosyada yazan bir değeri "hiç yazılmamış" gösterirdi.
    """
    path = module_path / "config" / "default.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def module_tab(module_id: str, module_path: Path, block: dict[str, Any]) -> dict[str, Any]:
    """Doğrulanmış `settings` bloğunu sekme biçimine çevirir.

    Alan yolu `modules.<id>.<key>` olarak kurulur — modülün ayar ad alanı budur
    ve `Config.module_config()` da oradan okur. Çekirdek burada modülün ne
    yaptığını bilmez; blok veridir.
    """
    defaults = module_defaults(module_path)
    groups: list[dict[str, Any]] = []
    for group in block.get("groups") or []:
        fields: list[dict[str, Any]] = []
        for field in group.get("fields") or []:
            key = str(field["key"])
            spec = dict(field)
            spec["path"] = f"modules.{module_id}.{key}"
            # Modülün kendi varsayılan dosyası da DOSYA katmanıdır; ilanındaki
            # `default` yalnız o dosyada da karşılığı yoksa devreye girer.
            found, value = value_at(defaults, key)
            if found:
                spec["file_default"] = value
            fields.append(spec)
        if fields:
            groups.append({"id": str(group["id"]), "title": str(group["title"]), "fields": fields})

    return {
        "id": module_id,
        "title": str(block.get("tab") or module_id),
        "kind": "module",
        # Sekmeyi görmek ve YAZMAK için gereken izin blokta ilan edilir (K9).
        # Yazma ayrıca `settings.manage` ister: ikisi ayrı kapılardır.
        "requires": [str(entry) for entry in block.get("requires") or []],
        "groups": groups,
    }


# ---------------------------------------------------------------------- çözüm


def resolve_field(
    spec: dict[str, Any],
    *,
    file_layer: dict[str, Any],
    store_values: dict[str, Any],
    env_values: dict[str, Any],
) -> dict[str, Any]:
    """Bir alanın etkin değerini ve KAYNAĞINI çözer.

    Öncelik: ilan edilen varsayılan → dosya → depo → ortam değişkeni.

    Ortam değişkeni ezmişse alan YAZILAMAZ hâle gelir ve nedeni yazılır:
    depoya yazılan değer o alanda hiçbir şey değiştirmezdi ve kullanıcı
    "kaydettim ama olmadı" ile baş başa kalırdı.
    """
    path = str(spec["path"])

    value: Any = spec.get("file_default", spec.get("default"))
    source = SOURCE_DEFAULT
    if "file_default" in spec:
        source = SOURCE_FILE

    file_found, file_value = value_at(file_layer, path)
    if file_found:
        value, source = file_value, SOURCE_FILE
    elif "file_default" in spec:
        file_found, file_value = True, spec["file_default"]

    store_found = path in store_values
    store_value = store_values.get(path)
    if store_found:
        value, source = store_value, SOURCE_STORE

    env_found, env_value = value_at(env_values, path)
    if env_found:
        value, source = env_value, SOURCE_ENV

    out: dict[str, Any] = {
        "key": str(spec["key"]),
        "type": str(spec["type"]),
        "title": str(spec["title"]),
        "description": str(spec.get("description") or ""),
        "value": value,
        "default": spec.get("default"),
        "source": source,
        "layers": {
            "file": file_value if file_found else None,
            "store": store_value if store_found else None,
            "env": env_value if env_found else None,
        },
        "hasFile": file_found,
        "hasStore": store_found,
        "hasEnv": env_found,
        "editable": not env_found,
        "lockedReason": (
            f"Bu değer {env_variable_name(path)} ortam değişkeninden geliyor ve "
            "en üstteki katmandır. Buradan yazılan değer etkisiz kalırdı."
            if env_found else ""
        ),
        "envName": env_variable_name(path),
    }
    for extra in ("min", "max", "max_length"):
        if extra in spec:
            out[extra] = spec[extra]
    if spec.get("options"):
        out["options"] = normalize_options(spec["options"])
    return out


def normalize_options(options: list[Any]) -> list[dict[str, str]]:
    """Şema iki biçime izin veriyor: düz metin listesi ya da {value,title}."""
    normal: list[dict[str, str]] = []
    for option in options:
        if isinstance(option, dict):
            value = str(option.get("value", ""))
            normal.append({"value": value, "title": str(option.get("title") or value)})
        else:
            normal.append({"value": str(option), "title": str(option)})
    return normal


def resolve_tab(
    tab: dict[str, Any],
    *,
    file_layer: dict[str, Any],
    store_values: dict[str, Any],
    env_values: dict[str, Any],
) -> dict[str, Any]:
    groups = [
        {
            "id": group["id"],
            "title": group["title"],
            "fields": [
                resolve_field(
                    spec,
                    file_layer=file_layer,
                    store_values=store_values,
                    env_values=env_values,
                )
                for spec in group["fields"]
            ],
        }
        for group in tab.get("groups") or []
    ]
    return {
        "id": tab["id"],
        "title": tab["title"],
        "kind": tab["kind"],
        "requires": tab["requires"],
        "groups": groups,
    }


def field_specs(tab: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Sekmedeki alanların `key → spec` eşlemesi; yazma bunun dışına çıkamaz."""
    return {
        str(spec["key"]): spec
        for group in tab.get("groups") or []
        for spec in group["fields"]
    }


# ------------------------------------------------------------------ doğrulama


def validate_value(spec: dict[str, Any], value: Any) -> Any:
    """Değeri alanın tipine göre doğrular; uymuyorsa `SettingsError`.

    Doğrulama ARAYÜZDE DE var ama kapı burasıdır (K9): ekranın gönderdiğine
    güvenilmez, çünkü uç noktaya ekran dışından da istek gelebilir.
    """
    title = str(spec.get("title") or spec.get("key"))
    kind = str(spec["type"])

    if kind == "bool":
        if not isinstance(value, bool):
            raise SettingsError(f"“{title}” yalnız açık/kapalı olabilir.")
        return value

    if kind == "int":
        # `bool` Python'da `int`tir; True'yu 1 diye kabul etmek sessiz bir
        # tip karışıklığı olurdu.
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"“{title}” bir tam sayı olmalı.")
        low = spec.get("min")
        high = spec.get("max")
        if low is not None and value < int(low):
            raise SettingsError(f"“{title}” en az {low} olabilir.")
        if high is not None and value > int(high):
            raise SettingsError(f"“{title}” en fazla {high} olabilir.")
        return value

    if kind in ("text", "path"):
        if not isinstance(value, str):
            raise SettingsError(f"“{title}” metin olmalı.")
        limit = spec.get("max_length")
        if limit is not None and len(value) > int(limit):
            raise SettingsError(f"“{title}” en fazla {limit} karakter olabilir.")
        return value

    if kind == "select":
        allowed = [option["value"] for option in normalize_options(spec.get("options") or [])]
        if not isinstance(value, str) or value not in allowed:
            raise SettingsError(
                f"“{title}” yalnız şu seçeneklerden biri olabilir: {', '.join(allowed)}."
            )
        return value

    if kind == "path_list":
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise SettingsError(f"“{title}” bir yol listesi olmalı.")
        return [item.strip() for item in value if item.strip()]

    if kind == "cron":
        if not isinstance(value, str) or not _CRON.match(value.strip()):
            raise SettingsError(
                f"“{title}” beş alanlı bir zamanlama olmalı (örnek: 0 3 * * *)."
            )
        return value.strip()

    # Şema bu listenin dışına izin vermiyor; yine de sessiz kalınmaz.
    raise SettingsError(f"“{title}” alanının tipi tanınmıyor: {kind}")
