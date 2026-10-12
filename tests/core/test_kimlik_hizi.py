"""Kimlik yolunun sorgu bütçesi — servis anahtarı ve kişi oturumu.

Mobil geçidin her isteği servis anahtarı doğrulamasından, her panel isteği
oturum çözümünden geçer; uzak PostgreSQL'de her sorgu bir gidiş-dönüştür.
Bütçe testle bağlanır ki gerilemesin. Güvenlik özellikleri (iptal ve
pasifleştirme ANINDA geçerli, sabit süreli karşılaştırma) aynen sınanır.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from km_core.security import identity as identity_mod
from km_core.security import service_keys as service_keys_mod
from km_core.security.identity import Identity
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.security.service_keys import ServiceKeys
from km_core.store.db import Store

PIN = "482913"


class Counting:
    """Depo ifadelerini sayan ince sarmalayıcı."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.count = 0
        for name in ("fetch_all", "fetch_one", "execute", "execute_many"):
            setattr(store, name, self._wrap(getattr(store, name)))

    def _wrap(self, original: Any) -> Any:
        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.count += 1
            return await original(*args, **kwargs)

        return counted

    def reset(self) -> None:
        self.count = 0


@pytest.fixture
async def ortam(tmp_path: Path) -> AsyncIterator[tuple[Identity, Counting]]:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)
    identity = Identity(store, pepper="deneme-pepper")
    await identity.ensure_builtin_roles()
    await identity.grant_defaults(CORE_PERMISSIONS)
    await identity.grant_defaults([{"key": "mobile_gateway.access",
                                    "default_roles": ["mobile_service"]}])
    yield identity, Counting(store)
    await store.close()


async def _key(identity: Identity) -> tuple[ServiceKeys, str, str]:
    user_id = await identity.create_service_user(name="BBD Akademi API",
                                                 roles=["mobile_service"])
    keys = ServiceKeys(identity)
    issued = await keys.create(user_id=user_id, name="test")
    return keys, issued.token, user_id


async def test_servis_anahtari_sicakken_TEK_sorgu(ortam: tuple[Identity, Counting]) -> None:
    identity, sayac = ortam
    keys, token, _ = await _key(identity)
    first = await keys.verify(token, client_ip="10.0.0.5")       # soğuk: anahtar + izinler
    assert first.user is not None
    assert first.user.has_permission("mobile_gateway.access")

    sayac.reset()
    second = await keys.verify(token, client_ip="10.0.0.5")
    assert second.user is not None and second.user.roles == ["mobile_service"]
    assert second.user.has_permission("mobile_gateway.access")
    # Anahtar + kullanıcı tek sorgu; izinler önbellekten; last_used dakikada bir.
    assert sayac.count == 1


async def test_iptal_ANINDA_gecerli_onbellek_sicakken_bile(
        ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    keys, token, _ = await _key(identity)
    assert (await keys.verify(token, client_ip=None)).user is not None
    # Başka süreçteki komut satırı gibi: başka bir ServiceKeys nesnesi iptal eder.
    await ServiceKeys(identity).revoke(token.split("_")[1])
    verdict = await keys.verify(token, client_ip=None)
    assert verdict.user is None and verdict.reason == "iptal edilmiş anahtar"


async def test_pasif_kullanici_ANINDA_reddedilir(ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    keys, token, user_id = await _key(identity)
    assert (await keys.verify(token, client_ip=None)).user is not None
    await identity.store.execute("UPDATE users SET status = 'disabled' WHERE id = ?",
                                 (user_id,))
    verdict = await keys.verify(token, client_ip=None)
    assert verdict.user is None and verdict.reason == "pasif servis kullanıcısı"


async def test_ag_kisiti_her_istekte_taze(ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    keys, token, _ = await _key(identity)
    assert (await keys.verify(token, client_ip="10.0.0.5")).user is not None
    await identity.store.execute("UPDATE service_keys SET allowed_cidrs = ?",
                                 ('["192.168.1.0/24"]',))
    assert (await keys.verify(token, client_ip="10.0.0.5")).user is None


async def test_rol_degisikligi_bu_surecte_hemen_gecerli(
        ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    keys, token, user_id = await _key(identity)
    assert (await keys.verify(token, client_ip=None)).user.has_permission(  # type: ignore[union-attr]
        "mobile_gateway.access")
    # Rolü alınır: `update_user` izin sürümünü artırır, önbellek bırakılır.
    await identity.store.execute(
        "INSERT OR IGNORE INTO roles (id, name, description, builtin) VALUES (?, ?, ?, 0)",
        ("bos_rol", "Boş", None),
    )
    await identity.update_user(user_id, fields={}, roles=["bos_rol"])
    user = (await keys.verify(token, client_ip=None)).user
    assert user is not None and user.roles == ["bos_rol"]
    assert not user.has_permission("mobile_gateway.access")


async def test_izin_onbellegi_kisa_omurlu(ortam: tuple[Identity, Counting],
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Başka süreçten (komut satırı) gelen rol değişikliğini süre sınırlar."""
    identity, sayac = ortam
    keys, token, user_id = await _key(identity)
    await keys.verify(token, client_ip=None)
    saat = {"t": 1000.0}
    monkeypatch.setattr(service_keys_mod.time, "monotonic", lambda: saat["t"])
    keys.forget_grants()
    await keys.verify(token, client_ip=None)                        # t=1000'de dolar
    # Doğrudan SQL ile (sürüm artmadan) rol silinir: süre dolana kadar eski küme.
    await identity.store.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    saat["t"] += service_keys_mod.GRANTS_CACHE_SECONDS - 1
    assert (await keys.verify(token, client_ip=None)).user.roles == ["mobile_service"]  # type: ignore[union-attr]
    saat["t"] += 2
    sayac.reset()
    assert (await keys.verify(token, client_ip=None)).user.roles == []  # type: ignore[union-attr]
    assert sayac.count == 2


async def test_onbellek_kopyasi_disaridan_bozulamaz(ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    keys, token, _ = await _key(identity)
    user = (await keys.verify(token, client_ip=None)).user
    assert user is not None
    user.permissions.add("users.manage")
    user.roles.append("admin")
    again = (await keys.verify(token, client_ip=None)).user
    assert again is not None
    assert "users.manage" not in again.permissions and again.roles == ["mobile_service"]


async def test_kisi_oturumu_IKI_sorgu_ve_izinler_taze(
        ortam: tuple[Identity, Counting]) -> None:
    identity, sayac = ortam
    user_id = await identity.create_user(first_name="Ayşe", last_name="Y", org_scope="org",
                                         password=PIN, roles=["org_staff"])
    result = await identity.login(PIN)
    assert result is not None
    await identity.resolve_session(result.token)   # oturum dokunuşu (dakikada bir)

    sayac.reset()
    user = await identity.resolve_session(result.token)
    assert user is not None and user.roles == ["org_staff"]
    assert sayac.count == 2

    # Kişinin izni ÖNBELLEĞE ALINMAZ: değişiklik bir sonraki istekte geçerli.
    await identity.store.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
    again = await identity.resolve_session(result.token)
    assert again is not None and again.roles == [] and again.permissions == set()


async def test_grants_of_eski_iki_sorguyla_ayni(ortam: tuple[Identity, Counting]) -> None:
    identity, _ = ortam
    await identity.store.execute(
        "INSERT OR IGNORE INTO roles (id, name, description, builtin) VALUES (?, ?, ?, 0)",
        ("bos_rol", "Boş", None),
    )
    user_id = await identity.create_user(first_name="Can", last_name="D", org_scope="bbd",
                                         password=PIN, roles=["bbd_staff", "bos_rol",
                                                              "admin"])
    roles, permissions = await identity.grants_of(user_id)
    assert roles == await identity.roles_of(user_id)
    assert permissions == await identity.permissions_of(user_id)
    assert "bos_rol" in roles


def test_izin_surumu_grant_defaults_ile_artar() -> None:
    before = identity_mod.grants_epoch()
    identity_mod._grants_changed()
    assert identity_mod.grants_epoch() == before + 1
