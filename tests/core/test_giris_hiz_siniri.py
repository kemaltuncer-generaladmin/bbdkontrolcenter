"""Giriş hız sınırı (ADR 0026).

Backend internete bakınca 6 haneli PIN tek başına yetmiyor: 1.000.000 ihtimal,
saniyede birkaç deneme yapan biri günler içinde bulur. `users.failed_attempts`
bu saldırıyı GÖREMEZ — giriş kullanıcı adsızdır, yanlış PIN hiçbir satıra denk
gelmez ve artırılacak sayaç yoktur. Sınır bu yüzden isteğin GELDİĞİ YERE konur.

Zaman `now` parametresiyle verilir; gerçek saat beklemek testi hem yavaş hem
kırılgan yapardı.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.rate_limit import RateLimiter

YONETICI_PINI = "593174"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
        "log_path": str(tmp_path / "gunluk.log"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    data["files"] = {"output_path": str(tmp_path / "cikti")}

    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_sinira_kadar_kabul_eder() -> None:
    limiter = RateLimiter(source_limit=3, source_window=60.0)
    assert limiter.check("1.2.3.4", now=0.0) is None
    assert limiter.check("1.2.3.4", now=1.0) is None
    assert limiter.check("1.2.3.4", now=2.0) is None


def test_sinir_asilinca_bekleme_suresi_doner() -> None:
    limiter = RateLimiter(source_limit=2, source_window=60.0)
    limiter.check("1.2.3.4", now=0.0)
    limiter.check("1.2.3.4", now=1.0)

    wait = limiter.check("1.2.3.4", now=2.0)
    assert wait is not None
    # İlk deneme 0.0'daydı, pencere 60 sn → 58 sn kaldı.
    assert 57.9 < wait < 58.1


def test_pencere_kayar() -> None:
    """Eski denemeler pencereden düşünce yeniden hak doğar."""
    limiter = RateLimiter(source_limit=2, source_window=60.0)
    limiter.check("1.2.3.4", now=0.0)
    limiter.check("1.2.3.4", now=1.0)
    assert limiter.check("1.2.3.4", now=2.0) is not None
    assert limiter.check("1.2.3.4", now=61.5) is None


def test_kaynaklar_birbirini_etkilemez() -> None:
    """Bir IP'nin denemeleri başkasını kilitlemez."""
    limiter = RateLimiter(source_limit=1, source_window=60.0)
    assert limiter.check("1.1.1.1", now=0.0) is None
    assert limiter.check("1.1.1.1", now=1.0) is not None
    assert limiter.check("2.2.2.2", now=1.0) is None


def test_genel_sinir_dagitik_denemeyi_de_yavaslatir() -> None:
    """Saldırgan binlerce IP'ye dağılsa bile genel sınır devrede."""
    limiter = RateLimiter(source_limit=100, source_window=60.0,
                          global_limit=3, global_window=60.0)
    for index in range(3):
        assert limiter.check(f"10.0.0.{index}", now=float(index)) is None
    assert limiter.check("10.0.0.99", now=4.0) is not None


def test_basarili_giristen_sonra_sayac_temizlenir() -> None:
    """Aynı ofisten birinin yanlış denemeleri doğru PIN'i gireni kilitlemesin."""
    limiter = RateLimiter(source_limit=2, source_window=60.0)
    limiter.check("1.2.3.4", now=0.0)
    limiter.check("1.2.3.4", now=1.0)
    assert limiter.check("1.2.3.4", now=2.0) is not None

    limiter.reset("1.2.3.4")
    assert limiter.check("1.2.3.4", now=3.0) is None


# ------------------------------------------------------------ uçtan uca

def test_giris_ucu_429_dondurur(client: TestClient) -> None:
    """Uç gerçekten sınırlanıyor mu — sınırlayıcı bağlanmamış olsaydı bu test
    yanlış PIN için hep 401 görür ve hiçbir şey fark etmezdi."""
    client.app.state.login_limiter = RateLimiter(source_limit=2, source_window=60.0)

    assert client.post("/api/auth/login", json={"password": "000001"}).status_code == 401
    assert client.post("/api/auth/login", json={"password": "000002"}).status_code == 401

    blocked = client.post("/api/auth/login", json={"password": "000003"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert "giriş denemesi" in blocked.json()["error"]["message"]
