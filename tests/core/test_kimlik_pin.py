"""PIN ile giriş — ADR 0007'nin sözleşmesi (docs/identity-model.md).

Bu testler kuralı KODDAN DEĞİL DAVRANIŞTAN doğrular: gerçek SQLite açılır,
gerçek Argon2 çalışır. Deneme sayacı, kilitlenme ve kullanıcı bulunamadığında
da harcanan sabit süre ayrıca sınanır.

ADI ŞİFRE, DEĞERİ PIN: ADR 0016 reddedildi ama göçü koştu; `password_hash`
sütunu, `set_password` metodu ve `PasswordError` sınıfı adlarını korudu.
Buradaki her "password" argümanı 6 haneli bir PIN alır.

0016'nın "önce kendine sır belirle" ZORLAMASI 17.08.2026'da kaldırıldı; giriş
artık tek sırra (`secret_lookup`) bakar ve hiçbir koşulda yeni sır istemez.
Kilitlenme regresyonu: `test_giris_HICBIR_KOSULDA_yeni_sir_istemez`.

Ağa çıkılmaz.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from km_core.security import identity as identity_module
from km_core.security.identity import (
    PASSWORD_UNAVAILABLE,
    Identity,
    IdentityError,
    PasswordError,
)
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store

# Basit, tekrarlı ve ardışık elemelerine takılmayan örnekler.
PIN = "482913"
BASKA_PIN = "735204"


@pytest.fixture
async def identity(tmp_path: Path) -> AsyncIterator[Identity]:
    store = Store(tmp_path / "km.sqlite")
    await store.open()
    await apply_core_migrations(store)

    kimlik = Identity(store, pepper="deneme-pepper", max_failed_attempts=3)
    await kimlik.ensure_builtin_roles()
    await kimlik.grant_defaults(CORE_PERMISSIONS)
    yield kimlik
    await store.close()


async def yeni_kullanici(kimlik: Identity, *, pin: str = PIN,
                         first_name: str = "Kemal", last_name: str = "Tuncer",
                         roles: list[str] | None = None, **extra: Any) -> str:
    return await kimlik.create_user(
        first_name=first_name,
        last_name=last_name,
        org_scope="org",
        password=pin,
        roles=roles or ["org_staff"],
        **extra,
    )


# --------------------------------------------------------------- kurallar


async def test_alti_haneden_kisa_pin_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError) as hata:
        await yeni_kullanici(identity, pin="48291")  # 5 hane
    assert "6" in str(hata.value)


async def test_rakam_disi_pin_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError) as hata:
        await yeni_kullanici(identity, pin="4829a3")
    assert "rakam" in str(hata.value).casefold()


async def test_basit_pin_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError):
        await yeni_kullanici(identity, pin="123456")


async def test_tek_rakamin_tekrari_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError):
        await yeni_kullanici(identity, pin="444444")


async def test_ardisik_artan_pin_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError) as hata:
        await yeni_kullanici(identity, pin="345678")
    assert "ardışık" in str(hata.value).casefold()


async def test_ardisik_azalan_pin_reddedilir(identity: Identity) -> None:
    with pytest.raises(PasswordError):
        await yeni_kullanici(identity, pin="876543")


async def test_alti_haneden_uzun_pin_KABUL_EDILIR(identity: Identity) -> None:
    """Üst sınır yoktur: sözleşme yalnız ALT sınır koyar."""
    assert await yeni_kullanici(identity, pin="4829137")


# ------------------------------------------------------------ benzersizlik


async def test_ayni_pin_ikinci_kullaniciya_atanamaz(identity: Identity) -> None:
    await yeni_kullanici(identity, first_name="Ayse", last_name="Yilmaz")
    with pytest.raises(PasswordError):
        await yeni_kullanici(identity, first_name="Veli", last_name="Demir")


async def test_cakisma_hatasi_KIMINLE_cakistigini_SOYLEMEZ(identity: Identity) -> None:
    """Yöneticinin deneme yoluyla başkasının PIN'ini öğrenmesine kapı
    açmayacak: mesaj tek tiptir, ad/soyad/kimlik geçmez."""
    kurban = await yeni_kullanici(identity, first_name="Ayse", last_name="Yilmaz")

    with pytest.raises(PasswordError) as hata:
        await yeni_kullanici(identity, first_name="Veli", last_name="Demir")

    mesaj = str(hata.value)
    assert mesaj == PASSWORD_UNAVAILABLE
    assert "Ayse" not in mesaj
    assert "Yilmaz" not in mesaj
    assert kurban not in mesaj


async def test_kendi_pinini_yeniden_atamak_cakisma_sayilmaz(identity: Identity) -> None:
    user_id = await yeni_kullanici(identity)
    await identity.set_password(user_id, PIN)  # aynı PIN, aynı kişi
    assert await identity.login(PIN) is not None


# ------------------------------------------------------------------ giriş


async def test_dogru_pinle_oturum_acilir(identity: Identity) -> None:
    user_id = await yeni_kullanici(identity)
    sonuc = await identity.login(PIN)
    assert sonuc is not None
    assert sonuc.token
    assert sonuc.user.id == user_id


async def test_kilitli_kullanici_DOGRU_pinle_de_giremez(identity: Identity) -> None:
    """`locked_until` bağlayıcıdır: kilit doğru PIN'i de tutar."""
    user_id = await yeni_kullanici(identity)
    ileri = (datetime.now(UTC) + timedelta(minutes=15)).isoformat(timespec="seconds")
    await identity.store.execute(
        "UPDATE users SET locked_until = ?, failed_attempts = ? WHERE id = ?",
        (ileri, 5, user_id),
    )

    assert await identity.login(PIN) is None


async def test_dogrulama_hatasi_sayaci_artirir_ve_KILITLER(identity: Identity) -> None:
    """`failed_attempts` → `locked_until` zinciri.

    Kullanıcı `secret_lookup` ile bulunduğu için yanlış bir PIN hiçbir kaydı
    işaret etmez; sayaç ancak satır bulunup Argon2 doğrulaması düştüğünde
    işler. O dal burada saklanan hash bozularak sınanır — koruma kaldırılırsa
    bu test düşer.
    """
    user_id = await yeni_kullanici(identity)
    await identity.store.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (identity_module._hasher.hash("610947"), user_id),
    )

    for beklenen in (1, 2, 3):  # max_failed_attempts = 3
        assert await identity.login(PIN) is None
        satir = await identity.store.fetch_one(
            "SELECT failed_attempts, locked_until FROM users WHERE id = ?", (user_id,)
        )
        assert satir is not None
        assert satir["failed_attempts"] == beklenen

    assert satir["locked_until"] is not None


async def test_basarili_giris_sayaci_sifirlar(identity: Identity) -> None:
    user_id = await yeni_kullanici(identity)
    await identity.store.execute(
        "UPDATE users SET failed_attempts = 2 WHERE id = ?", (user_id,)
    )

    assert await identity.login(PIN) is not None

    satir = await identity.store.fetch_one(
        "SELECT failed_attempts FROM users WHERE id = ?", (user_id,)
    )
    assert satir is not None
    assert satir["failed_attempts"] == 0


async def test_pasif_kullanici_giris_yapamaz(identity: Identity) -> None:
    yonetici = await yeni_kullanici(identity, roles=["admin"])
    hedef = await yeni_kullanici(
        identity, pin=BASKA_PIN, first_name="Veli", last_name="Demir"
    )
    await identity.set_status(hedef, "disabled", actor_id=yonetici)
    assert await identity.login(BASKA_PIN) is None


async def test_kullanici_YOKKEN_de_dogrulama_suresi_harcanir(
    identity: Identity, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yanıt süresinden "bu PIN kimseye ait değil" bilgisi SIZMAZ.

    Sayaç, sahte doğrulamanın gerçekten koştuğunu gösterir; süre ölçümü de
    çağrının boşa dönmediğini (Argon2'nin gerçekten çalıştığını) doğrular.
    """
    sayac = _SayanHasher(identity_module._hasher)
    monkeypatch.setattr(identity_module, "_hasher", sayac)

    basladi = time.monotonic()
    assert await identity.login("907142") is None
    gecen = time.monotonic() - basladi

    assert sayac.calls == 1, "kullanıcı bulunamadı diye doğrulama atlanmış"
    assert gecen > 0.005, "sahte doğrulama süresi harcanmıyor"


class _SayanHasher:
    """Argon2 sarmalayıcı — çağrı sayar, davranışı değiştirmez."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0

    def hash(self, secret: str) -> str:
        return str(self._inner.hash(secret))

    def verify(self, hash_: str, secret: str) -> bool:
        self.calls += 1
        return bool(self._inner.verify(hash_, secret))


# --------------------------------------------- kendi PIN'ini DEĞİŞTİRME
#
# ADR 0016'nın "önce kendine sır belirle" ZORLAMASI kaldırıldı (17.08.2026); geri
# kalan tek yol kullanıcının kendi isteğiyle PIN değiştirmesidir. Aşağıdaki
# testler o yolun sürdüğünü ve hiçbir yerden zorlama doğmadığını sınar.


async def test_kullanici_kendi_pinini_ISTEYEREK_degistirir(identity: Identity) -> None:
    user_id = await yeni_kullanici(identity)

    sonuc = await identity.set_password_with_secret(PIN, BASKA_PIN)
    assert sonuc is not None
    token, user = sonuc
    assert token
    assert user.id == user_id

    # Yeni PIN geçer, eskisi GEÇMEZ.
    giris = await identity.login(BASKA_PIN)
    assert giris is not None and giris.token
    assert await identity.login(PIN) is None


async def test_hatali_mevcut_sirla_pin_degistirilemez(identity: Identity) -> None:
    await yeni_kullanici(identity)
    assert await identity.set_password_with_secret("805213", BASKA_PIN) is None
    # Eski PIN yerinde durur.
    assert await identity.login(PIN) is not None


async def test_pin_degisince_eski_PIN_SUTUNLARI_SILINMEZ(identity: Identity) -> None:
    """`pin_hash` / `pin_lookup` düşürülmez; hiçbir satır kaybolmaz."""
    user_id = await yeni_kullanici(identity)
    once = await identity.store.fetch_one(
        "SELECT pin_hash, pin_lookup, pin_set_at FROM users WHERE id = ?", (user_id,)
    )
    await identity.set_password_with_secret(PIN, BASKA_PIN)

    sonra = await identity.store.fetch_one(
        "SELECT pin_hash, pin_lookup, pin_set_at FROM users WHERE id = ?", (user_id,)
    )
    assert once is not None and sonra is not None
    assert dict(sonra) == dict(once)


async def test_giris_HICBIR_KOSULDA_yeni_sir_istemez(identity: Identity) -> None:
    """KİLİTLENME REGRESYONU — 17.08.2026.

    O gün olan şuydu: kullanıcı orijinal PIN'iyle girdi, `must_set_password`
    akışı devreye girdi, yeni bir sır yazıldı, `secret_lookup` değişti ve
    orijinal PIN "bilinmeyen sır" ile reddedilir oldu.

    Bu test o zinciri baştan kurar: giriş sonucunun oturum AÇMAYAN bir ara
    durumu olamaz, giriş sırrı YAZMAZ ve aynı PIN ikinci kez de çalışır.
    """
    user_id = await yeni_kullanici(identity)
    once = await identity.store.fetch_one(
        "SELECT password_hash, secret_lookup FROM users WHERE id = ?", (user_id,)
    )

    ilk = await identity.login(PIN)
    assert ilk is not None
    assert ilk.token, "giriş oturum açmadı — sır belirlemeye zorlanmış olabilir"
    # Ara durum diye bir ŞEY yoktur: alanın kendisi de kaldırıldı.
    assert not hasattr(ilk, "must_set_password")

    sonra = await identity.store.fetch_one(
        "SELECT password_hash, secret_lookup FROM users WHERE id = ?", (user_id,)
    )
    assert once is not None and sonra is not None
    assert dict(sonra) == dict(once), "giriş sırrı yeniden yazmış"

    # AYNI PIN İKİNCİ KEZ DE ÇALIŞIR. Kilitlenmenin görünür belirtisi buydu.
    ikinci = await identity.login(PIN)
    assert ikinci is not None and ikinci.token


# -------------------------------------------------------------- kısıtlar


async def test_son_yonetici_pasiflestirilemez(identity: Identity) -> None:
    yonetici = await yeni_kullanici(identity, roles=["admin"])
    with pytest.raises(IdentityError):
        await identity.set_status(yonetici, "disabled")


async def test_son_yoneticinin_rolu_alinamaz(identity: Identity) -> None:
    yonetici = await yeni_kullanici(identity, roles=["admin"])
    with pytest.raises(IdentityError):
        await identity.update_user(yonetici, fields={}, roles=["org_staff"])


async def test_ikinci_yonetici_varken_birincisi_pasiflestirilebilir(identity: Identity) -> None:
    birinci = await yeni_kullanici(identity, roles=["admin"])
    await yeni_kullanici(
        identity, pin=BASKA_PIN, first_name="Veli", last_name="Demir", roles=["admin"]
    )
    kayit = await identity.set_status(birinci, "disabled")
    assert kayit["status"] == "disabled"


async def test_rolsuz_kullanici_acilamaz(identity: Identity) -> None:
    with pytest.raises(IdentityError):
        await identity.create_user(
            first_name="Kemal", last_name="Tuncer", org_scope="org",
            password=PIN, roles=[],
        )


async def test_tanimsiz_rol_reddedilir(identity: Identity) -> None:
    with pytest.raises(IdentityError):
        await yeni_kullanici(identity, roles=["boyle_bir_rol_yok"])
