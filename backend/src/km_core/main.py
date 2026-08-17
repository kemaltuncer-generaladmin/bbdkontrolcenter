"""Sidecar giriş noktası.

Masaüstü kabuğu bu süreci başlatır ve `127.0.0.1` üzerinden konuşur (ADR 0002).
Dışarı açılmaz: host ayarı varsayılan olarak 127.0.0.1'dir.

    .venv/bin/python -m km_core.main
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import structlog
import uvicorn

from km_core.config.loader import load_config
from km_core.http.app import create_app


def _file_handler(log_file: Path) -> logging.Handler | None:
    """Log dosyası tutucusu — kurulan uygulamanın TEK çıktı kanalı.

    NEDEN VAR. Paketlenmiş uygulamada `sys.stdout` HİÇBİR YERE ÇIKMAZ: kabuk
    `windows_subsystem = "windows"` ile derlenir ve sidecar `CREATE_NO_WINDOW`
    ile başlatılır. Konsol yoktur. Bu, ilk kurulumda somut bir kilide yol
    açıyordu — veritabanı boşken açılış yöneticisinin PIN'i üretilip YALNIZCA
    loga yazılıyor (`http/app.py` → `_bootstrap_admin`); o log yutulunca PIN'i
    kimse öğrenemiyor ve uygulamaya HİÇ girilemiyor.

    DOSYA DAR İZİNLE AÇILIR (0600). İçinden açılış PIN'i geçiyor; makinedeki
    her kullanıcının okuyabildiği bir dosyaya yazmak, sırrı stdout'tan
    kurtarıp diske sermek olurdu. İzin Windows'ta `chmod`'un karşılığı
    olmadığı için sessizce yok sayılır — orada koruma dosya sisteminin kendi
    ACL'lerine kalır.

    LOG DÖNER (5 MB × 3). Sınırsız büyüyen bir dosya, aylar sonra veri
    dizinini şişirirdi.

    Yazılamıyorsa sidecar AYAĞA KALKMAYA DEVAM EDER: log tutamamak, uygulamayı
    hiç açmamak için yeterli bir sebep değildir.
    """
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        if not log_file.exists():
            descriptor = os.open(log_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            os.close(descriptor)
        handler = RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
    except OSError as error:
        print(f"log dosyası açılamadı ({log_file}): {error}", file=sys.stderr)
        return None
    handler.setFormatter(logging.Formatter("%(message)s"))
    return handler


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Yapısal log — insan okur biçimde, korelasyon için zaman damgalı.

    `log_file` verilirse çıktı hem konsola hem dosyaya gider. Konsol tutucusu
    KALDIRILMAZ: geliştirme makinesinde `python -m km_core.main` çalıştıran
    kişi çıktıyı bugünkü gibi terminalinde görmeye devam eder.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_file is not None:
        file_handler = _file_handler(log_file)
        if file_handler is not None:
            handlers.append(file_handler)
    for handler in handlers:
        handler.setFormatter(logging.Formatter("%(message)s"))

    logging.basicConfig(level=level, handlers=handlers, force=True)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        # ÇIKTI STDLIB `logging` ÜZERİNDEN GİDER — bu satır olmadan dosya
        # tutucusu boş kalır. structlog'un varsayılanı `PrintLoggerFactory`'dir
        # ve doğrudan `sys.stdout`'a yazar; stdlib tutucularına HİÇ uğramaz.
        # Yani yukarıdaki `basicConfig` yalnız uvicorn/kütüphane loglarını
        # taşır, structlog satırlarını değil. Kurulu uygulamada stdout hiçbir
        # yere çıkmadığı için bu, logun tamamen kaybolması demekti.
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=False,
    )


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def resolve_host(config: object) -> str:
    """Dinlenecek adres — döngü dışına çıkmak için açık izin şarttır.

    Bu süreç öğrenci adlarını, veli telefonlarını ve kantin yönetim uçlarını
    KİMLİK DOĞRULAMASIZ AĞA değil, yalnız kabuğa açar. `server.host` yanlışlıkla
    `0.0.0.0` yazılırsa okuldaki her cihaz panele ulaşır. O yüzden döngü dışı
    adres ancak `server.allow_remote: true` ile birlikte kabul edilir; tek başına
    yazılırsa yok sayılır ve 127.0.0.1'e dönülür.
    """
    host = str(config.get("server.host", "127.0.0.1"))  # type: ignore[attr-defined]
    if host in _LOOPBACK:
        return host
    if bool(config.get("server.allow_remote", False)):  # type: ignore[attr-defined]
        structlog.get_logger().warning(
            "sunucu ağa açık dinliyor — kişisel veri taşır, güvenlik duvarını denetleyin",
            host=host,
        )
        return host
    structlog.get_logger().error(
        "server.host döngü dışı ama server.allow_remote kapalı — 127.0.0.1'e dönüldü",
        istenen=host,
    )
    return "127.0.0.1"


def main() -> None:
    config = load_config()
    # Yol `config.path` ile çözülür: `data/` ile başlayan değer YAZILABİLİR veri
    # dizinine düşer (ADR 0023), paketin içine değil — kurulu uygulamada program
    # klasörü salt okunurdur.
    setup_logging(
        str(config.get("core.log_level", "INFO")),
        config.path("core.log_file", "data/logs/kontrol-merkezi.log"),
    )

    uvicorn.run(
        create_app(config),
        host=resolve_host(config),
        port=int(config.get("server.port", 8787)),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
