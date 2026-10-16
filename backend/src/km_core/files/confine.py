"""Ayardan gelen dosya yollarını izinli köke hapseder (güvenlik bulgusu KM-15).

SORUN. Sistem Ayarları'ndaki iki yol alanı sunucuda keyfi dizine dokunduruyordu:

  · `files.output_path` — destek paketi oraya YAZILIR ve o klasördeki PDF'ler
    `/api/outputs/document` ile OKUNUR. `/etc` ya da başka bir uygulamanın
    klasörü yazılınca sunucuda istenen yere dosya bırakılabiliyordu.
  · `core.log_path` — Tanılama ekranı ve destek paketi bu dosyanın kuyruğunu
    gösterir. `/etc/passwd` yazmak, maskelemeden geçse de, sunucudaki herhangi
    bir dosyayı ekrana döktürüyordu.

Ayarı yalnız `settings.manage` taşıyan yönetici değiştirebilir (düşük önem),
ama yönetici PIN'i ele geçmiş bir oturum sunucunun dosya sistemine
uzanmamalı.

KURAL. Değer çözüldükten (`~`, `..`, sembolik bağ) SONRA izinli köklerden
birinin altında olmalıdır:

  · çıktı klasörü → veri dizini (`data_dir`), masaüstündeki
    "Kontrol Merkezi/Raporlar" hiyerarşisi, kurulum kökündeki `data/`
  · günlük dosyası → veri dizini ya da kurulum kökündeki `data/`

İKİ KAPI. Kaydederken (`check_setting` → 400, Türkçe neden) ve depo katmanı
çalışan ayara uygulanırken (`confine_stored`, açılış + her kayıt sonrası):
düzeltmeden önce kaydedilmiş kök dışı değer kullanılmaz, varsayılana düşülür.
Dosya katmanı (`local.yaml`) ve ortam değişkeni kurulumu yapanın elindedir ve
hapsedilmez — saldırı yüzeyi arayüzden yazılan değerdir.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import structlog

from km_core.config.paths import DATA_SEGMENT, data_dir, resolve_path
from km_core.files.outputs import EXPORTS_SEGMENT, reports_root

log = structlog.get_logger("km.files.confine")

OUTPUT_PATH_KEY = "files.output_path"
LOG_PATH_KEY = "core.log_path"


def _resolved(paths: Iterable[Path]) -> list[Path]:
    seen: list[Path] = []
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except (OSError, RuntimeError):  # pragma: no cover — çözülemeyen kök yok sayılır
            continue
        if resolved not in seen:
            seen.append(resolved)
    return seen


def output_path_roots(root: Path) -> list[Path]:
    """Çekirdek çıktı klasörünün bulunabileceği kökler."""
    data = data_dir(root)
    return _resolved([data, reports_root(data / EXPORTS_SEGMENT), root / DATA_SEGMENT])


def log_path_roots(root: Path) -> list[Path]:
    """Günlük dosyasının bulunabileceği kökler."""
    return _resolved([data_dir(root), root / DATA_SEGMENT])


def within(path: Path, roots: Iterable[Path]) -> bool:
    """`path` (çözüldükten sonra) köklerden birinin altında mı?"""
    try:
        resolved = path.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(resolved == entry or resolved.is_relative_to(entry) for entry in roots)


def _output_candidate(value: str, root: Path) -> Path:
    # `report_dir` yapılandırılmış değeri `Path(değer).expanduser()` ile kullanır;
    # göreli değer çalışma dizinine düşer. Aynı yorumla denetlenir.
    del root
    return Path(value)


def _log_candidate(value: str, root: Path) -> Path:
    # `Config.path` ile aynı çözüm: `data/…` veri dizinine, öteki göreli değerler
    # paket köküne düşer (ADR 0023).
    return resolve_path(value, root)


_RULES: dict[str, tuple[Callable[[str, Path], Path], Callable[[Path], list[Path]], str]] = {
    OUTPUT_PATH_KEY: (_output_candidate, output_path_roots,
                      ("Çıktı klasörü yalnız uygulamanın veri klasörü ya da masaüstündeki "
                       "“Kontrol Merkezi/Raporlar” altında olabilir.")),
    LOG_PATH_KEY: (_log_candidate, log_path_roots,
                   ("Günlük dosyası yalnız uygulamanın veri klasörü altında olabilir "
                    "(ör. data/launcher.log).")),
}


def check_setting(key: str, value: object, root: Path) -> str | None:
    """Kaydedilecek değer izinli kökün dışındaysa kullanıcıya gösterilecek neden; yoksa None."""
    rule = _RULES.get(key)
    if rule is None or not isinstance(value, str) or not value.strip():
        return None
    candidate, roots, message = rule
    if "\x00" in value or not within(candidate(value.strip(), root), roots(root)):
        return message
    return None


def confine_stored(values: dict[str, Any], root: Path) -> dict[str, Any]:
    """Çekirdek deposundaki (arayüzden kaydedilmiş) ayarlardan kök dışı yolları ayıklar.

    Kaydetme kapısından ÖNCE yazılmış bir değer (bu düzeltmeden önceki sürüm)
    açılışta ya da tazelemede çalışan ayara karışmaz: o anahtar dosya katmanına
    (varsayılana) döner ve günlüğe uyarı düşer. Dosya katmanı ve ortam değişkeni
    kurulumu yapanındır; onlara dokunulmaz.
    """
    kept: dict[str, Any] = {}
    for key, value in values.items():
        if check_setting(key, value, root) is not None:
            log.warning("depodaki yol ayarı izinli kök dışında; yok sayıldı", key=key)
            continue
        kept[key] = value
    return kept
