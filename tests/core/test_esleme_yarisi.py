"""Eşleme kodunun yakılması ATOMİKTİR — `services/identity/app/installations.py`.

Buradaki üç davranış ADR 0021 §4'ün "tek kullanımlık kod" sözünün gerçekten
tutulup tutulmadığını sorar. Üçü de gerçek bir veritabanında koşar; ağa
çıkılmaz.

BULUNAN İKİ HATA (17.08.2026):

  a. **Tüketim atomik değildi.** `_consume_code` önce
     `SELECT ... WHERE used_at IS NULL` ile satırı buluyor, sonra
     `UPDATE ... WHERE code_hash = ?` ile yazıyordu — koşul yazmanın içinde
     YOKTU. Aynı kodu taşıyan iki `/pair` isteği aradaki `await` noktasında
     birbirine giriyor, ikisi de boş satırı görüyor ve TEK KULLANIMLIK kod iki
     makineyi birden eşleyebiliyordu. Belirtisi sinsi: iki kurulum da geçerli
     token alıyor, listede iki satır görünüyor ve kimse kodun bir kez
     verildiğini hatırlamıyordu.

  b. **Kod, kurulum satırı yazılmadan ÖNCE yakılıyordu.** INSERT patlarsa
     (kısıt ihlali, dolu disk, süreç ölümü) kod yanmış ama hiçbir kurulum
     eşlenmemiş oluyordu; sahadaki makinenin elindeki kod sessizce ölüyordu.

Ayrıca ekranın söz verdiği kural burada sınanır: **yeni kod bekleyen eski
kodları geçersiz kılar.** Ekranda yazılı olan bir cümlenin kodda karşılığı
olmalı; olmasaydı yönetici "kodu yeniledim" derken arkada hâlâ çalışan,
hiçbir ekranda görünmeyen bir kod bırakırdı.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from services.identity.app import installations as inst
from services.identity.app.schema import apply_service_migrations

from km_core.security.migrations import apply_core_migrations
from km_core.store.db import Store

TTL = 600


@pytest.fixture
async def depo(tmp_path: Path) -> AsyncIterator[Store]:
    store = Store(tmp_path / "identity.sqlite")
    await store.open()
    await apply_core_migrations(store)
    await apply_service_migrations(store)
    yield store
    await store.close()


def _govde(makine: str) -> dict[str, str]:
    return {
        "public_key": f"-----BEGIN PUBLIC KEY-----{makine}",
        "machine_name": makine,
        "platform": "Linux",
        "version": "0.1.0",
    }


async def test_ayni_kod_ESZAMANLI_iki_kurulumu_eslemez(depo: Store) -> None:
    """İki istek aynı anda gelirse YALNIZ BİRİ eşlenir.

    `asyncio.gather` iki `pair()` çağrısını aynı olay döngüsünde yürütür ve
    aiosqlite her işlemde denetimi bırakır — hatanın doğduğu yer tam olarak
    burasıydı.
    """
    kod = (await inst.create_pair_code(depo, ttl_seconds=TTL))["code"]

    sonuclar = await asyncio.gather(
        inst.pair(depo, code=kod, **_govde("MSI")),
        inst.pair(depo, code=kod, **_govde("Flex5")),
        return_exceptions=True,
    )

    basarili = [s for s in sonuclar if isinstance(s, dict)]
    reddedilen = [s for s in sonuclar if isinstance(s, inst.PairError)]
    assert len(basarili) == 1, f"kod iki kez tüketildi: {sonuclar}"
    assert len(reddedilen) == 1
    assert str(reddedilen[0]) == "Eşleme kodu geçersiz."

    # VERİTABANI DA TEK SATIR GÖRMELİ: "biri hata aldı" yetmez, ikinci kurulum
    # gerçekten yazılmamış olmalı.
    kurulumlar = await inst.listing(depo)
    assert len(kurulumlar) == 1
    assert kurulumlar[0]["id"] == basarili[0]["installationId"]

    # Kod kullanılmış işaretlenir ve KİMİN kullandığı yazılır.
    satir = await depo.fetch_one("SELECT used_at, used_by FROM pair_codes")
    assert satir is not None
    assert satir["used_at"]
    assert satir["used_by"] == basarili[0]["installationId"]


async def test_kurulum_yazilamazsa_KOD_YANMAZ(depo: Store,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """INSERT patlarsa tüketim de geri alınır; kod hâlâ kullanılabilir.

    Patlama, `installations.token_hash` sütunundaki UNIQUE kısıtıyla
    üretilir: token üreticisi sabitlenince ikinci eşleme aynı özeti yazmaya
    çalışır ve veritabanı reddeder.
    """
    monkeypatch.setattr(inst.secrets, "token_urlsafe", lambda _n: "sabit-token")

    ilk = await inst.create_pair_code(depo, ttl_seconds=TTL)
    await inst.pair(depo, code=ilk["code"], **_govde("MSI"))

    ikinci = await inst.create_pair_code(depo, ttl_seconds=TTL)
    with pytest.raises(Exception) as hata:
        await inst.pair(depo, code=ikinci["code"], **_govde("Flex5"))
    assert not isinstance(hata.value, inst.PairError)   # kod değil, yazma patladı

    # KOD YANMADI: satır hâlâ kullanılmamış.
    satir = await depo.fetch_one(
        "SELECT used_at FROM pair_codes WHERE code_hash = ?",
        (inst.hash_token(ikinci["code"]),),
    )
    assert satir is not None
    assert satir["used_at"] is None

    # Ve gerçekten yeniden kullanılabilir: token üreticisi normale dönünce
    # aynı kod eşler.
    monkeypatch.undo()
    sonuc = await inst.pair(depo, code=ikinci["code"], **_govde("Flex5"))
    assert sonuc["installationId"]
    assert len(await inst.listing(depo)) == 2


async def test_yeni_kod_bekleyen_eskisini_GECERSIZ_KILAR(depo: Store) -> None:
    """Ekranda yazılı olan cümlenin kodda karşılığı."""
    eski = (await inst.create_pair_code(depo, ttl_seconds=TTL))["code"]
    yeni = (await inst.create_pair_code(depo, ttl_seconds=TTL))["code"]

    with pytest.raises(inst.PairError):
        await inst.pair(depo, code=eski, **_govde("MSI"))

    sonuc = await inst.pair(depo, code=yeni, **_govde("MSI"))
    assert sonuc["token"]

    # ESKİ SATIR SİLİNMEZ ve "kullanıldı" da denmez — süresi bitirilmiştir.
    # `used_at` işaretlemek, hiç kullanılmamış bir kodu kullanılmış gösterirdi.
    eski_satir = await depo.fetch_one(
        "SELECT used_at, used_by FROM pair_codes WHERE code_hash = ?",
        (inst.hash_token(eski),),
    )
    assert eski_satir is not None
    assert eski_satir["used_at"] is None
    assert eski_satir["used_by"] is None


async def test_kullanilmis_kod_ikinci_kez_ESLEMEZ(depo: Store) -> None:
    """Ardışık (yarışsız) ikinci deneme de reddedilir — eski davranış korunur."""
    kod = (await inst.create_pair_code(depo, ttl_seconds=TTL))["code"]
    await inst.pair(depo, code=kod, **_govde("MSI"))

    with pytest.raises(inst.PairError):
        await inst.pair(depo, code=kod, **_govde("Flex5"))
    assert len(await inst.listing(depo)) == 1
