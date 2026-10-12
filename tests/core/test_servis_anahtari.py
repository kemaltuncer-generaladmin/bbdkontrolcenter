"""Servis anahtarları — ADR 0028.

İki katman sınanır: depo/doğrulayıcı (`ServiceKeys`) gerçek SQLite üzerinde ve
HTTP kapısı gerçek uygulama ayağa kaldırılarak. Ağa çıkılmaz.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from km_core.config.loader import ROOT, Config, load_config
from km_core.http.app import create_app
from km_core.security.identity import SERVICE_TOKEN_MARK, Identity, IdentityError
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.security.rate_limit import RateLimiter
from km_core.security.service_keys import (
    ServiceKeyError,
    ServiceKeys,
    address_allowed,
    normalize_cidrs,
    parse_token,
)
from km_core.store.db import Store

PIN = "482913"
YONETICI_PINI = "593174"


@pytest.fixture
async def kimlik(tmp_path: Path) -> AsyncIterator[Identity]:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)
    identity = Identity(store, pepper="deneme-pepper")
    await identity.ensure_builtin_roles()
    await identity.grant_defaults(CORE_PERMISSIONS)
    yield identity
    await store.close()


# ------------------------------------------------------------------ biçim


def test_anahtar_bicimi_ayristirilir() -> None:
    sir = "A" * 43
    assert parse_token(f"kmsk_abcd1234_{sir}") == ("abcd1234", sir)
    assert parse_token(f"kmsk_ABCD1234_{sir}") is None      # önek küçük harf
    assert parse_token(f"kmsk_abc_{sir}") is None           # önek kısa
    assert parse_token("kmsk_abcd1234_kisa") is None        # sır < 32
    assert parse_token(f"oturum_{sir}") is None
    assert parse_token("") is None


def test_ag_listesi_kanonik_ve_gecersiz_reddedilir() -> None:
    assert normalize_cidrs(["10.0.0.5", " 10.0.0.0/8 ", "", "10.0.0.5/32"]) == [
        "10.0.0.5/32", "10.0.0.0/8",
    ]
    with pytest.raises(ServiceKeyError):
        normalize_cidrs(["10.0.0.300"])


def test_ag_kisiti() -> None:
    assert address_allowed("1.2.3.4", []) is True
    assert address_allowed("10.1.2.3", ["10.0.0.0/8"]) is True
    assert address_allowed("11.1.2.3", ["10.0.0.0/8"]) is False
    assert address_allowed("bozuk", ["10.0.0.0/8"]) is False
    assert address_allowed("2001:db8::1", ["2001:db8::/32"]) is True


def test_baslik_metni_cozulur() -> None:
    from km_core.security.audit_context import clean_header

    assert clean_header("Ay%C5%9Fe Y%C4%B1lmaz", 160) == "Ayşe Yılmaz"
    ham = "Ayşe Yılmaz".encode().decode("latin-1")    # sunucunun gördüğü
    assert clean_header(ham, 160) == "Ayşe Yılmaz"
    assert clean_header("a\nb\x00c", 160) == "abc"
    assert clean_header("x" * 300, 160) == "x" * 160
    assert clean_header("   ", 160) is None


def test_hiz_siniri_peek_kaydetmez() -> None:
    limiter = RateLimiter(source_limit=2, source_window=60.0)
    assert limiter.peek("a", now=0.0) is None
    assert limiter.peek("a", now=0.0) is None
    limiter.check("a", now=0.0)
    limiter.check("a", now=1.0)
    wait = limiter.peek("a", now=2.0)
    assert wait is not None and 57.9 < wait < 58.1
    assert limiter.peek("b", now=2.0) is None


# ------------------------------------------------------------ depo/doğrulama


async def test_servis_kullanicisi_pinsiz_ve_rehber_disi(kimlik: Identity) -> None:
    user_id = await kimlik.create_service_user(name="BBD Akademi API", roles=["mobile_service"])
    row = await kimlik.get_user(user_id)
    assert row["kind"] == "service"
    assert row["secret_lookup"] is None and row["password_hash"] is None
    assert row["directory_visible"] == 0
    assert row["roles"] == ["mobile_service"]


async def test_servis_kullanicisina_pin_atanamaz(kimlik: Identity) -> None:
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    with pytest.raises(IdentityError):
        await kimlik.set_password(user_id, PIN)


async def test_kisiye_anahtar_verilmez(kimlik: Identity) -> None:
    kisi = await kimlik.create_user(first_name="Ayşe", last_name="Y", org_scope="org",
                                    password=PIN, roles=["org_staff"])
    with pytest.raises(ServiceKeyError):
        await ServiceKeys(kimlik).create(user_id=kisi, name="x")


async def test_anahtar_dogrulanir_ve_ozet_saklanir(kimlik: Identity) -> None:
    keys = ServiceKeys(kimlik)
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    issued = await keys.create(user_id=user_id, name="Akademi")

    assert issued.token.startswith(f"{SERVICE_TOKEN_MARK}{issued.prefix}_")
    secret = issued.token.split("_", 2)[2]
    assert len(secret) >= 43  # 32 bayt urlsafe

    row = await kimlik.store.fetch_one("SELECT * FROM service_keys WHERE id = ?", (issued.id,))
    assert row is not None
    assert issued.token not in json.dumps(row)          # anahtarın kendisi yazılmaz
    assert "keyHash" not in json.dumps(await keys.list())

    verdict = await keys.verify(issued.token, client_ip="203.0.113.7")
    assert verdict.user is not None
    assert verdict.user.id == user_id
    assert verdict.user.is_service
    assert verdict.user.roles == ["mobile_service"]


async def test_yanlis_iptal_ve_pasif_anahtar_reddedilir(kimlik: Identity) -> None:
    keys = ServiceKeys(kimlik)
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    issued = await keys.create(user_id=user_id, name="Akademi")

    sahte = issued.token[:-4] + ("AAAA" if not issued.token.endswith("AAAA") else "BBBB")
    assert (await keys.verify(sahte, client_ip=None)).reason == "anahtar eşleşmedi"
    assert (await keys.verify("kmsk_zzzzzzzz_" + "A" * 43, client_ip=None)).reason \
        == "bilinmeyen anahtar"

    await kimlik.set_status(user_id, "disabled")
    assert (await keys.verify(issued.token, client_ip=None)).reason == "pasif servis kullanıcısı"
    await kimlik.set_status(user_id, "active")

    await keys.revoke(issued.prefix)
    assert (await keys.verify(issued.token, client_ip=None)).reason == "iptal edilmiş anahtar"
    # İkinci iptal tarihi değiştirmez, satır silinmez.
    first = (await keys.list())[0]["revokedAt"]
    await keys.revoke(issued.id)
    assert (await keys.list())[0]["revokedAt"] == first


async def test_ag_kisitli_anahtar(kimlik: Identity) -> None:
    keys = ServiceKeys(kimlik)
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    issued = await keys.create(user_id=user_id, name="Akademi", allowed_cidrs=["203.0.113.0/24"])
    assert (await keys.verify(issued.token, client_ip="203.0.113.9")).user is not None
    verdict = await keys.verify(issued.token, client_ip="198.51.100.1")
    assert verdict.user is None and verdict.reason.startswith("izinli ağ dışı")


async def test_son_kullanim_dakikada_bir_yazilir(kimlik: Identity) -> None:
    keys = ServiceKeys(kimlik)
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    issued = await keys.create(user_id=user_id, name="Akademi")
    yazilan: list[str] = []
    asil = kimlik.store.execute

    async def sayan(sql: str, params: Any = ()) -> None:
        if "last_used_at" in sql:
            yazilan.append(sql)
        await asil(sql, params)

    kimlik.store.execute = sayan  # type: ignore[method-assign]
    for _ in range(3):
        assert (await keys.verify(issued.token, client_ip=None)).user is not None
    assert len(yazilan) == 1


async def test_servis_kullanicisi_pin_ile_giremez(kimlik: Identity) -> None:
    user_id = await kimlik.create_service_user(name="Servis", roles=["mobile_service"])
    # Sırrı olmayan satıra hiçbir PIN denk gelmez; giriş başarısız olur.
    assert await kimlik.login(PIN) is None
    row = await kimlik.get_user(user_id)
    assert row["last_login_at"] is None


# ------------------------------------------------------------------ HTTP


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
    with TestClient(create_app(Config(data, root=ROOT)), raise_server_exceptions=False) as c:
        yield c


def _issue(client: TestClient, *, roles: list[str] | None = None,
           cidrs: list[str] | None = None) -> tuple[str, str]:
    app = client.app
    identity: Identity = app.state.identity  # type: ignore[attr-defined]
    keys: ServiceKeys = app.state.service_keys  # type: ignore[attr-defined]

    async def create() -> tuple[str, str]:
        user_id = await identity.create_service_user(
            name="BBD Akademi API", roles=roles or ["mobile_service"]
        )
        issued = await keys.create(user_id=user_id, name="Akademi", allowed_cidrs=cidrs)
        return user_id, issued.token

    return client.portal.call(create)  # type: ignore[union-attr]


def _audit(client: TestClient, action: str) -> list[dict[str, Any]]:
    identity: Identity = client.app.state.identity  # type: ignore[attr-defined]

    async def read() -> list[dict[str, Any]]:
        return await identity.store.fetch_all(
            "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
        )

    return client.portal.call(read)  # type: ignore[union-attr]


def test_anahtar_ayni_kullaniciya_cozulur(client: TestClient) -> None:
    user_id, token = _issue(client)
    cevap = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert cevap.status_code == 200, cevap.text
    govde = cevap.json()
    assert govde["id"] == user_id
    assert govde["kind"] == "service"
    assert govde["roles"] == ["mobile_service"]


def test_izin_denetimi_kisiyle_ayni_ve_iz_on_behalf_tasir(client: TestClient) -> None:
    _, token = _issue(client)
    cevap = client.get("/api/users", headers={
        "Authorization": f"Bearer {token}",
        # Başlık ASCII taşır: Türkçe ad yüzde kodlanır (ham UTF-8 de kabul).
        "X-KM-On-Behalf-Of": "akademi:staff:42:Ay%C5%9Fe%20Y%C4%B1lmaz%1B[31m",
        "X-Request-Id": "istek-123",
    })
    assert cevap.status_code == 403
    satir = _audit(client, "permission.denied")[-1]
    assert satir["on_behalf_of"] == "akademi:staff:42:Ayşe Yılmaz[31m"  # kaçış dizisi atıldı
    assert satir["request_id"] == "istek-123"


def test_izinli_rol_uca_girer(client: TestClient) -> None:
    identity: Identity = client.app.state.identity  # type: ignore[attr-defined]

    async def rol() -> None:
        await identity.store.execute(
            "INSERT INTO roles (id, name, description, builtin) VALUES ('svc_view', 'x', '', 0)"
        )
        await identity.store.execute(
            "INSERT INTO role_permissions (role_id, permission) VALUES ('svc_view', 'users.view')"
        )

    client.portal.call(rol)  # type: ignore[union-attr]
    _, token = _issue(client, roles=["svc_view"])
    cevap = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})
    assert cevap.status_code == 200, cevap.text
    kayitlar = {row["id"]: row for row in cevap.json()["users"]}
    assert any(row["kind"] == "service" for row in kayitlar.values())


def test_kisi_oturumunda_on_behalf_yok_sayilir(client: TestClient) -> None:
    identity: Identity = client.app.state.identity  # type: ignore[attr-defined]

    async def kisi() -> None:
        await identity.create_user(first_name="Ayşe", last_name="Y", org_scope="org",
                                   password=PIN, roles=["org_staff"])

    client.portal.call(kisi)  # type: ignore[union-attr]
    giris = client.post("/api/auth/login", json={"password": PIN})
    token = giris.json()["token"]
    assert not token.startswith(SERVICE_TOKEN_MARK)
    cevap = client.get("/api/users", headers={
        "Authorization": f"Bearer {token}", "X-KM-On-Behalf-Of": "uydurma",
        "X-Request-Id": "kisi-1",
    })
    assert cevap.status_code == 403
    satir = _audit(client, "permission.denied")[-1]
    assert satir["on_behalf_of"] is None
    assert satir["request_id"] == "kisi-1"


def test_gecersiz_anahtar_401_ve_kaynak_basina_429(client: TestClient) -> None:
    _, token = _issue(client)
    yanlis = token[:-3] + ("xyz" if not token.endswith("xyz") else "abc")
    durumlar = [
        client.get("/auth/me", headers={"Authorization": f"Bearer {yanlis}",
                                         "X-Forwarded-For": "9.9.9.9, 198.51.100.4"}).status_code
        for _ in range(12)
    ]
    assert durumlar[0] == 401
    assert 429 in durumlar
    # Başka bir kaynaktan gelen GEÇERLİ anahtar etkilenmez.
    cevap = client.get("/auth/me", headers={"Authorization": f"Bearer {token}",
                                             "X-Forwarded-For": "198.51.100.5"})
    assert cevap.status_code == 200
    assert _audit(client, "auth.service_key")[0]["result"] == "fail"


def test_ag_kisiti_en_sagdaki_xff_ile(client: TestClient) -> None:
    _, token = _issue(client, cidrs=["203.0.113.0/24"])
    # İlk değer istemcinin uydurduğu; en sağdaki vekilin gördüğü adres.
    uydurma = client.get("/auth/me", headers={
        "Authorization": f"Bearer {token}", "X-Forwarded-For": "203.0.113.9, 198.51.100.4",
    })
    assert uydurma.status_code == 401
    dogru = client.get("/auth/me", headers={
        "Authorization": f"Bearer {token}", "X-Forwarded-For": "198.51.100.4, 203.0.113.9",
    })
    assert dogru.status_code == 200


def test_servis_istek_kovasi_ayri(client: TestClient) -> None:
    _, token = _issue(client)
    client.app.state.service_key_requests = RateLimiter(  # type: ignore[attr-defined]
        source_limit=2, source_window=60.0, global_limit=100, global_window=60.0,
    )
    basliklar = {"Authorization": f"Bearer {token}"}
    assert client.get("/auth/me", headers=basliklar).status_code == 200
    assert client.get("/auth/me", headers=basliklar).status_code == 200
    cevap = client.get("/auth/me", headers=basliklar)
    assert cevap.status_code == 429
    assert "Retry-After" in cevap.headers
    # PIN girişinin kovası etkilenmedi.
    assert client.post("/api/auth/login", json={"password": YONETICI_PINI}).status_code == 200
