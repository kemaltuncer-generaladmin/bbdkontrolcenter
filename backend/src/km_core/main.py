"""Sidecar giriş noktası.

Masaüstü kabuğu bu süreci başlatır ve `127.0.0.1` üzerinden konuşur (ADR 0002).
Dışarı açılmaz: host ayarı varsayılan olarak 127.0.0.1'dir.

    .venv/bin/python -m km_core.main
"""

from __future__ import annotations

import logging
import sys

import structlog
import uvicorn

from km_core.config.loader import load_config
from km_core.http.app import create_app


def setup_logging(level: str = "INFO") -> None:
    """Yapısal log — insan okur biçimde, korelasyon için zaman damgalı."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="%H:%M:%S"),
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level, logging.INFO)),
        cache_logger_on_first_use=True,
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
    setup_logging(str(config.get("core.log_level", "INFO")))

    uvicorn.run(
        create_app(config),
        host=resolve_host(config),
        port=int(config.get("server.port", 8787)),
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
