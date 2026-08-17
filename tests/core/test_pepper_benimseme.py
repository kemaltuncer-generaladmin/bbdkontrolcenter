"""Eşlenen kurulum merkezin kimlik anahtarını benimser.

NEDEN VAR. `secret_lookup` sabit anahtarlı (pepper) bir HMAC'tir. Merkezden
çekilen kadro bu değerleri TAŞIR; kurulum ise girilen PIN'in HMAC'ini KENDİ
kasasındaki anahtarla hesaplar. İkisi aynı değilse hiçbir PIN tutmaz.

Taze bir kurulumda kasa boştur ve `Vault.pepper()` RASTGELE bir anahtar
üretir. Yani eşleme başarıyla tamamlansa, kadro doğru çekilse ve kullanıcı
tabloda görünse bile giriş reddediliyordu — 17.08.2026'da ilk macOS ve Windows
kurulumlarında yaşanan buydu. `roster_projection.py` şartı yazıyordu ama onu
SAĞLAYACAK BİR YOL YOKTU.

BELİRTİ SEBEBİ ELE VERMİYOR: kullanıcı yalnız "PIN yanlış" görür. Bu yüzden
burada sınanan şey "eşleme başarılı mı" değil, EŞLEMEDEN SONRA GİRİŞ OLUYOR MU.
"""

from __future__ import annotations

import pytest

from km_core.security.identity import Identity
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store

MERKEZ_PEPPER = "merkezin-degismez-anahtari"
TAZE_PEPPER = "taze-kurulumun-rastgele-anahtari"
PIN = "482913"


async def _kur(path, pepper: str) -> tuple[Store, Identity]:
    store = Store(path)
    await store.open()
    await apply_core_migrations(store)
    identity = Identity(store, pepper=pepper)
    await identity.ensure_builtin_roles()
    await identity.grant_defaults(CORE_PERMISSIONS)
    return store, identity


@pytest.mark.asyncio
async def test_pepper_benimsenmeden_giris_olmuyor_benimsenince_oluyor(tmp_path) -> None:
    merkez, merkez_kimlik = await _kur(tmp_path / "merkez.sqlite", MERKEZ_PEPPER)
    uid = await merkez_kimlik.create_user(
        first_name="Zahide", last_name="BLD", org_scope="org",
        password=PIN, roles=["bld_staff"],
    )
    satir = await merkez_kimlik.get_user(uid)

    kurulum, kurulum_kimlik = await _kur(tmp_path / "kurulum.sqlite", TAZE_PEPPER)
    # Kadro yansıtması: hash'ler merkezden OLDUĞU GİBİ gelir.
    await kurulum.execute(
        "INSERT INTO users (id, first_name, last_name, org_scope, status, "
        "pin_hash, pin_lookup, pin_set_at, password_hash, secret_lookup, "
        "password_set_at, revision, origin, created_at, updated_at) "
        "VALUES (?,?,?,?,?,'',?,?,?,?,?,1,'central',?,?)",
        (uid, "Zahide", "BLD", "org", "active", f"pin-yok:{uid}",
         satir["password_set_at"], satir["password_hash"], satir["secret_lookup"],
         satir["password_set_at"], satir["created_at"], satir["updated_at"]),
    )

    # Bugünkü hata: kullanıcı tabloda var ama giremiyor.
    assert await kurulum_kimlik.login(PIN) is None

    kurulum_kimlik.adopt_pepper(MERKEZ_PEPPER)

    sonuc = await kurulum_kimlik.login(PIN)
    assert sonuc is not None, "pepper benimsendikten sonra giriş yapılabilmeli"
    assert sonuc.user.full_name == "Zahide BLD"

    await merkez.close()
    await kurulum.close()


@pytest.mark.asyncio
async def test_kendiliginden_dogan_pepper_isaretleniyor(tmp_path) -> None:
    """Eşleme yalnız KENDİLİĞİNDEN doğmuş bir anahtarı ezebilir.

    Elle konmuş ya da daha önce benimsenmiş bir anahtarı ezmek, o makinedeki
    herkesin girişini bir anda kırardı ve düz PIN'ler hiçbir yerde saklanmadığı
    için geri getirilemezdi.
    """
    from km_core.config.loader import ROOT, Config
    from km_platform.secrets.vault import Vault

    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)
    vault = Vault(store, Config({"core": {"secret_key_path": str(tmp_path / "secret.key")}},
                                root=ROOT))
    await vault.open()

    uretilen = await vault.pepper()
    assert await vault.pepper_is_auto() is True, "rastgele doğan anahtar işaretlenmeli"
    assert uretilen == await vault.pepper(), "ikinci çağrı aynı değeri vermeli"

    await vault.adopt_pepper(MERKEZ_PEPPER)
    assert await vault.get("core.pin_pepper") == MERKEZ_PEPPER
    assert await vault.pepper_is_auto() is False, "benimsenen anahtar artık otomatik değil"

    await store.close()
