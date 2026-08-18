"""Arka plan tarama katmanı — çağıran ASLA beklemez.

Bu dosyanın varlık sebebi somut bir arıza: nüfus/sipariş taraması liste
isteğinin içinde koşuyordu, kabuk 60 saniyede kesiyordu ve müşteri ekranı hiç
açılmıyordu. Buradaki testler o davranışın geri gelmesini engeller.
"""

from __future__ import annotations

import asyncio
from typing import Any

from store_api_backend.scan import RETRY_AFTER, BackgroundScan


class FakeLog:
    def __init__(self) -> None:
        self.records: list[tuple[str, str, dict[str, Any]]] = []

    def info(self, message: str, **fields: Any) -> None:
        self.records.append(("info", message, fields))

    def warning(self, message: str, **fields: Any) -> None:
        self.records.append(("warning", message, fields))

    def error(self, message: str, **fields: Any) -> None:
        self.records.append(("error", message, fields))


# ================================================== asıl kural: beklenmez

async def test_bitmeyen_tarama_cagirani_bekletmez() -> None:
    # Gerçek tarama dakikalarca sürüyor. `read()` onu beklerse ekran açılmaz.
    scan = BackgroundScan(log=FakeLog())
    baslasin = asyncio.Event()

    async def hic_bitmez() -> str:
        await baslasin.wait()          # test bırakana kadar askıda
        return "geldi"

    report = await asyncio.wait_for(scan.read("agir", hic_bitmez), timeout=1.0)
    assert report["state"] == "running"
    assert report["value"] is None     # SIFIR UYDURULMAZ
    assert report["running"] is True

    baslasin.set()
    await asyncio.sleep(0)             # arka plan görevi bitsin
    await asyncio.sleep(0)
    assert scan.peek("agir")["state"] == "ready"
    assert scan.peek("agir")["value"] == "geldi"
    await scan.close()


async def test_ayni_anahtarda_tek_tarama_kosar() -> None:
    # Ekran açılışında liste ve KPI uçları aynı anda ister; iki tarama
    # başlatmak mağazanın dakikalık istek bütçesini boşuna iki katı yerdi.
    scan = BackgroundScan(log=FakeLog())
    sayac = {"n": 0}

    async def yukle() -> int:
        sayac["n"] += 1
        await asyncio.sleep(0)
        return sayac["n"]

    await asyncio.gather(scan.read("k", yukle), scan.read("k", yukle),
                         scan.read("k", yukle))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sayac["n"] == 1
    await scan.close()


async def test_taze_sonuc_yeniden_taranmaz() -> None:
    scan = BackgroundScan(log=FakeLog(), default_ttl=3600)
    sayac = {"n": 0}

    async def yukle() -> int:
        sayac["n"] += 1
        return sayac["n"]

    await scan.read("k", yukle, wait=1.0)
    assert scan.peek("k")["state"] == "ready"
    report = await scan.read("k", yukle)
    assert report["value"] == 1
    assert sayac["n"] == 1              # ikinci tarama başlamadı
    await scan.close()


async def test_bekleme_isteyen_cagiran_sonucu_alir() -> None:
    # `wait` bir KOLAYLIKTIR: tarama hızlıysa ilk açılışta da dolu ekran verir.
    scan = BackgroundScan(log=FakeLog())

    async def hizli() -> str:
        return "tamam"

    report = await scan.read("k", hizli, wait=1.0)
    assert report["state"] == "ready"
    assert report["value"] == "tamam"
    await scan.close()


# ======================================================= hata ve dayanıklılık

async def test_patlayan_tarama_cagirani_dusurmez() -> None:
    scan = BackgroundScan(log=FakeLog())

    async def patlar() -> None:
        raise RuntimeError("mağaza yanıt vermedi")

    report = await scan.read("k", patlar, wait=1.0)
    assert report["state"] == "error"
    assert "mağaza yanıt vermedi" in report["error"]
    assert report["value"] is None
    await scan.close()


async def test_patlayan_tarama_hemen_yeniden_denenmez() -> None:
    # Mağaza düşmüşken her ekran tazelemesinin yeni tarama başlatması,
    # düşmüş sunucuyu döverdi.
    scan = BackgroundScan(log=FakeLog())
    sayac = {"n": 0}

    async def patlar() -> None:
        sayac["n"] += 1
        raise RuntimeError("bağlantı yok")

    await scan.read("k", patlar, wait=1.0)
    await scan.read("k", patlar, wait=1.0)
    await scan.read("k", patlar, wait=1.0)
    assert sayac["n"] == 1
    assert RETRY_AFTER > 0
    await scan.close()


async def test_kullanicinin_yenile_demesi_soguma_suresini_asar() -> None:
    scan = BackgroundScan(log=FakeLog())
    sayac = {"n": 0}

    async def patlar() -> None:
        sayac["n"] += 1
        raise RuntimeError("bağlantı yok")

    await scan.read("k", patlar, wait=1.0)
    await scan.read("k", patlar, refresh=True, wait=1.0)
    assert sayac["n"] == 2
    await scan.close()


async def test_bayat_deger_silinmez_eski_sonuc_gosterilmeye_devam_eder() -> None:
    # "Kaydettim, kayboldu" hissi vermemek için: bayat değer durur, arka planda
    # yenisi gelir.
    scan = BackgroundScan(log=FakeLog(), default_ttl=3600)

    async def yukle() -> str:
        return "ilk"

    await scan.read("k", yukle, wait=1.0)
    scan.invalidate("k")
    report = scan.peek("k")
    assert report["value"] == "ilk"          # değer duruyor
    assert report["stale"] is True           # ama bayat işaretli
    await scan.close()


# ============================================================== ad alanı

async def test_iki_modul_ayni_anahtar_adini_kullanabilir() -> None:
    scan = BackgroundScan(log=FakeLog(), default_ttl=3600)
    biri = scan.scoped("store_customers")
    oteki = scan.scoped("store_reports")

    async def bir() -> str:
        return "müşteri"

    async def iki() -> str:
        return "rapor"

    await biri.read("orders", bir, wait=1.0)
    await oteki.read("orders", iki, wait=1.0)
    assert biri.peek("orders")["value"] == "müşteri"
    assert oteki.peek("orders")["value"] == "rapor"
    await scan.close()
