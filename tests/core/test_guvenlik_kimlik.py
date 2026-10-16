"""Kimlik/oturum güvenlik düzeltmelerinin regresyonları (iş 63 — `docs/security/bulgular-2026-10-16.md`).

- KM-01: `/api/auth/set-password` mevcut PIN'i doğruluyor ve oturum açıyordu ama hız
  sınırı YOKTU — `/auth/login` sınırını dolanan sınırsız PIN tahmin kapısıydı.
- KM-02: yıkıcı işlemin PIN teyidi (`confirm_pin`) sınırsız denenebiliyordu.
- KM-03: PIN sıfırlanınca / değişince eski oturumlar açık kalıyordu.
- KM-04: oturumun mutlak ömrü yoktu (boşta kalma süresi her istekte tazeleniyor).
- KM-05: `/api/pairing/reset` internetteki herkese açıktı.

Gerçek uygulama ayağa kalkar (geçici SQLite); ağa çıkılmaz.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.identity import Identity
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store

YONETICI_PINI = "482913"
PERSONEL_PINI = "735204"
YENI_PIN = "618342"


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client: TestClient, pin: str) -> str:
    cevap = client.post("/api/auth/login", json={"password": pin})
    assert cevap.status_code == 200, cevap.text
    return str(cevap.json()["token"])


def _personel(client: TestClient, admin: str) -> dict[str, Any]:
    cevap = client.post("/api/users", headers=_h(admin), json={
        "firstName": "Veli", "lastName": "Demir", "orgScope": "org",
        "roles": ["org_staff"], "password": PERSONEL_PINI,
    })
    assert cevap.status_code == 201, cevap.text
    return dict(cevap.json()["user"])


# ------------------------------------------------------------------ KM-01


def test_pin_degistirme_ucu_giris_hiz_sinirina_tabi(client: TestClient) -> None:
    """Yanlış `currentSecret` ile sınırsız deneme yapılamaz; kova girişle ortaktır."""
    kodlar = []
    for deneme in range(9):
        cevap = client.post("/api/auth/set-password",
                            json={"currentSecret": f"10{deneme:04d}", "password": YENI_PIN})
        kodlar.append(cevap.status_code)
    assert kodlar[:8] == [401] * 8
    assert kodlar[8] == 429
    assert "Retry-After" in client.post(
        "/api/auth/set-password", json={"currentSecret": "999999", "password": YENI_PIN}
    ).headers
    # Aynı kova: giriş de kapalı (iki uç arasında gidip gelerek sınır ikiye katlanamaz).
    assert client.post("/api/auth/login", json={"password": YONETICI_PINI}).status_code == 429


# ------------------------------------------------------------------ KM-03


def test_yonetici_pin_sifirlayinca_kisinin_eski_oturumu_kapanir(client: TestClient) -> None:
    admin = _login(client, YONETICI_PINI)
    kisi = _personel(client, admin)
    eski = _login(client, PERSONEL_PINI)
    assert client.get("/auth/me", headers=_h(eski)).status_code == 200

    cevap = client.post(f"/api/users/{kisi['id']}/password", headers=_h(admin),
                        json={"password": YENI_PIN})
    assert cevap.status_code == 200, cevap.text

    assert client.get("/auth/me", headers=_h(eski)).status_code == 401
    # Yöneticinin kendi oturumu sürer.
    assert client.get("/auth/me", headers=_h(admin)).status_code == 200


def test_kendi_pinini_degistirince_obur_oturumlar_kapanir(client: TestClient) -> None:
    admin = _login(client, YONETICI_PINI)
    _personel(client, admin)
    birinci = _login(client, PERSONEL_PINI)
    ikinci = _login(client, PERSONEL_PINI)

    cevap = client.post("/api/auth/set-password",
                        json={"currentSecret": PERSONEL_PINI, "password": YENI_PIN})
    assert cevap.status_code == 200, cevap.text
    yeni = str(cevap.json()["token"])

    assert client.get("/auth/me", headers=_h(birinci)).status_code == 401
    assert client.get("/auth/me", headers=_h(ikinci)).status_code == 401
    assert client.get("/auth/me", headers=_h(yeni)).status_code == 200


# ------------------------------------------------------------------ KM-04


@pytest.fixture
async def identity(tmp_path: Path) -> AsyncIterator[Identity]:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)
    kimlik = Identity(store, pepper="deneme-pepper", session_absolute_hours=12)
    await kimlik.ensure_builtin_roles()
    await kimlik.grant_defaults(CORE_PERMISSIONS)
    yield kimlik
    await store.close()


async def test_oturum_mutlak_omrunu_doldurunca_kapanir(identity: Identity) -> None:
    await identity.create_user(first_name="A", last_name="B", org_scope="org",
                               password=PERSONEL_PINI, roles=["org_staff"])
    sonuc = await identity.login(PERSONEL_PINI)
    assert sonuc is not None
    assert await identity.resolve_session(sonuc.token) is not None

    # Oturum 11 saat önce açılmış: hâlâ geçerli (boşta kalma süresi taze).
    on_bir = (datetime.now(UTC) - timedelta(hours=11)).isoformat(timespec="seconds")
    await identity.store.execute("UPDATE sessions SET created_at = ?", (on_bir,))
    assert await identity.resolve_session(sonuc.token) is not None

    # 13 saat önce açılmış: kullanılıyor olsa da kapanır ve satırı silinir.
    on_uc = (datetime.now(UTC) - timedelta(hours=13)).isoformat(timespec="seconds")
    await identity.store.execute("UPDATE sessions SET created_at = ?", (on_uc,))
    assert await identity.resolve_session(sonuc.token) is None
    assert await identity.store.fetch_one("SELECT token FROM sessions") is None


async def test_mutlak_omur_sifirsa_sinir_yok(identity: Identity) -> None:
    identity.session_absolute_hours = 0
    await identity.create_user(first_name="A", last_name="B", org_scope="org",
                               password=PERSONEL_PINI, roles=["org_staff"])
    sonuc = await identity.login(PERSONEL_PINI)
    assert sonuc is not None
    eski = (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")
    await identity.store.execute("UPDATE sessions SET created_at = ?", (eski,))
    assert await identity.resolve_session(sonuc.token) is not None


# ------------------------------------------------------------------ KM-02


def test_pin_teyidi_art_arda_yanlista_kapanir(client: TestClient) -> None:
    """Teyit kapısı (burada eşleme çözme) yanlış PIN'le sınırsız denenemez."""
    admin = _login(client, YONETICI_PINI)
    kodlar = [
        client.post("/api/pairing/unpair", headers=_h(admin),
                    json={"password": f"20{deneme:04d}"}).status_code
        for deneme in range(6)
    ]
    assert kodlar[:5] == [403] * 5
    assert kodlar[5] == 429
    # Kapı kapalıyken DOĞRU PIN de geçmez (kaba kuvvetin son denemesi ödüllendirilmez).
    assert client.post("/api/pairing/unpair", headers=_h(admin),
                       json={"password": YONETICI_PINI}).status_code == 429
    # Oturum açık kalır.
    assert client.get("/auth/me", headers=_h(admin)).status_code == 200


# ------------------------------------------------------------------ KM-05


def test_esleme_sifirlama_kimliksiz_istekle_yapilamaz(client: TestClient) -> None:
    """Sunucuda (yetenek kapalı) sıfırlanacak eşleme yoktur; uç kasaya dokunmaz."""
    cevap = client.post("/api/pairing/reset")
    assert cevap.status_code == 409, cevap.text


@pytest.fixture
def esli_client(tmp_path: Path) -> Iterator[TestClient]:
    """Merkezî kimlik yeteneği AÇIK kurulum (adres erişilemez; ağa çıkılmaz)."""
    data = deepcopy(load_config().as_dict())
    data["core"] = {
        **data.get("core", {}),
        "store_path": str(tmp_path / "km.sqlite"),
        "secret_key_path": str(tmp_path / "secret.key"),
    }
    data["auth"] = {**data.get("auth", {}), "bootstrap_pin": YONETICI_PINI}
    platform = dict(data.get("platform", {}))
    platform["identity_sync"] = {**platform.get("identity_sync", {}),
                                 "enabled": True, "base_url": "https://kimlik.invalid"}
    data["platform"] = platform
    app = create_app(Config(data, root=ROOT))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_esleme_sifirlama_internetten_kimliksiz_reddedilir(esli_client: TestClient) -> None:
    sync = esli_client.app.state.identity_sync  # type: ignore[attr-defined]
    assert sync.configured
    cevap = esli_client.post("/api/pairing/reset", headers={"X-Forwarded-For": "203.0.113.9"})
    assert cevap.status_code == 403, cevap.text


def test_esleme_sifirlama_kurulum_yoneticisine_acik(esli_client: TestClient) -> None:
    admin = _login(esli_client, YONETICI_PINI)
    cevap = esli_client.post("/api/pairing/reset", headers=_h(admin))
    assert cevap.status_code == 200, cevap.text
    assert cevap.json() == {"reset": True}


def test_esleme_sifirlama_anahtar_uyusmazliginda_kimliksiz_acik(esli_client: TestClient) -> None:
    """Ucun var olma sebebi: anahtarı uyuşmayan kurulumda kimse giremez."""
    esli_client.app.state.identity_sync.pepper_mismatch = True  # type: ignore[attr-defined]
    cevap = esli_client.post("/api/pairing/reset")
    assert cevap.status_code == 200, cevap.text
