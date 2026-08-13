"""Sidecar'ın dinleme adresi — kaza eseri ağa açılmayı engelleyen kapı.

Bu süreç öğrenci adı, veli telefonu ve kantin yönetim uçlarını taşır ve
kimlik doğrulaması kabuğun oturumuna dayanır. `server.host` yanlışlıkla
`0.0.0.0` yazılırsa okul ağındaki her cihaz panele ulaşabilir. Kural:
döngü dışı adres ancak `server.allow_remote` ile birlikte kabul edilir.
"""

from typing import Any

import pytest

from km_core.main import resolve_host


class FakeConfig:
    def __init__(self, values: dict[str, Any]) -> None:
        self._values = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_dongu_adresleri_oldugu_gibi_kabul_edilir(host: str) -> None:
    assert resolve_host(FakeConfig({"server.host": host})) == host


def test_ayar_yoksa_dongu_adresine_duser() -> None:
    assert resolve_host(FakeConfig({})) == "127.0.0.1"


def test_dongu_disi_adres_izinsiz_reddedilir() -> None:
    # En tehlikeli hâli: tek satırlık bir yazım hatası paneli ağa açardı.
    assert resolve_host(FakeConfig({"server.host": "0.0.0.0"})) == "127.0.0.1"


def test_dongu_disi_adres_acik_izinle_kabul_edilir() -> None:
    config = FakeConfig({"server.host": "0.0.0.0", "server.allow_remote": True})
    assert resolve_host(config) == "0.0.0.0"


def test_izin_acik_ama_adres_dongu_icinde_ise_degismez() -> None:
    config = FakeConfig({"server.host": "127.0.0.1", "server.allow_remote": True})
    assert resolve_host(config) == "127.0.0.1"
