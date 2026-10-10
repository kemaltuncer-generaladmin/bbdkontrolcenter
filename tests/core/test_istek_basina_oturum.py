"""Hız düzeltmeleri (10.10.2026) — oturum istek başına BİR kez çözülür,
oturum satırı her istekte yazılmaz, kapalı modül menüde görünmez.

Gerçek uygulama ayağa kaldırılır; ağa çıkılmaz.
"""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security import identity as identity_mod
from km_core.security.identity import Identity

YONETICI_PINI = "482913"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as c:
        yield c


def _token(client: TestClient) -> str:
    cevap = client.post("/api/auth/login", json={"password": YONETICI_PINI})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def test_kapali_modul_menude_gorunmez(client: TestClient) -> None:
    """`enabled: false` modül menüde görünmez; açık mağaza ekranı görünür."""
    cevap = client.get("/modules", headers={"Authorization": f"Bearer {_token(client)}"})
    assert cevap.status_code == 200
    kayit = {m["id"]: m for m in cevap.json()["modules"]}
    assert kayit["store_cms"]["state"] == "disabled"
    assert kayit["store_cms"]["visible"] is False
    assert kayit["store_orders"]["visible"] is True


def test_modul_ucunda_oturum_BIR_kez_cozulur(client: TestClient,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Router kapısı ve ucun `requires`ı aynı istekte oturumu paylaşır."""
    token = _token(client)
    sayac = {"n": 0}
    asil = Identity.resolve_session

    async def sayan(self: Identity, value: str) -> Any:
        sayac["n"] += 1
        return await asil(self, value)

    monkeypatch.setattr(Identity, "resolve_session", sayan)
    # İki kapıdan geçen bir modül ucu: router taban izni + ucun kendi izni.
    cevap = client.get("/api/store_orders/settings",
                       headers={"Authorization": f"Bearer {token}"})
    assert cevap.status_code != 401, cevap.text
    assert sayac["n"] == 1


def test_oturum_satiri_dakikada_bir_yazilir() -> None:
    simdi = datetime.now(UTC)
    assert identity_mod._stale(None, 60) is True
    assert identity_mod._stale("bozuk", 60) is True
    assert identity_mod._stale((simdi - timedelta(seconds=5)).isoformat(), 60) is False
    assert identity_mod._stale((simdi - timedelta(seconds=90)).isoformat(), 60) is True
