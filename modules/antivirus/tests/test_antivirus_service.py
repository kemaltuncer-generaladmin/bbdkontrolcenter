"""Antivirüs servisi — koşu yönetimi, kayıt, olaylar, imza güncelliği.

Motor gerçek (sahte tarayıcı betikleriyle), depo gerçek (geçici SQLite +
modülün kendi göçleri). Ağa çıkılmaz, gerçek tarama koşmaz.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from antivirus_backend.engine import (
    VERDICT_CLEAN,
    VERDICT_FAILED,
    VERDICT_INCOMPLETE,
    VERDICT_INFECTED,
    ClamAvEngine,
    EngineNotReady,
)
from antivirus_backend.service import AntivirusService, ScanBusy, cron_to_triggers

CLEAN = """
echo "/tmp/a.txt: OK"
echo "----------- SCAN SUMMARY -----------"
echo "Scanned files: 4"
echo "Infected files: 0"
exit 0
"""

INFECTED = """
echo "/tmp/kotu.bin: Eicar-Test-Signature FOUND"
echo "Scanned files: 4"
echo "Infected files: 1"
exit 1
"""

DENIED = """
echo "/tmp/acik.txt: OK"
echo "/root/gizli: Access denied. ERROR"
echo "Scanned files: 1"
exit 2
"""

SLOW = """
sleep 30
"""


def build_service(store: Any, log: Any, events: Any, *, primary: str = "",
                  database: str = "", paths: list[str] | None = None,
                  extra: dict[str, Any] | None = None) -> AntivirusService:
    config: dict[str, Any] = {
        "clamdscan": primary or "km-yok-clamdscan",
        "clamscan": "km-yok-clamscan",
        "database_path": database or "/km-yok-clamav",
        "quick_paths": paths or ["/tmp"],
        "full_paths": paths or ["/tmp"],
        "exclude_paths": [],
        "quick_timeout_minutes": 1,
        "full_timeout_minutes": 1,
        "signature_max_age_hours": 48,
    }
    config.update(extra or {})
    return AntivirusService(
        store=store, log=log, config=config,
        engine=ClamAvEngine(config=config, log=log), publish=events,
    )


# ---------------------------------------------------------------- açılış


async def test_hic_tarama_yokken_durum_okunur(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str],
) -> None:
    """İlk açılışta ekran boş değil, "henüz tarama yok" der."""
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", CLEAN),
                            database=signature_dir())
    state = await service.state()

    assert state["last"] is None
    assert state["active"] is None
    assert state["engine"]["ready"] is True
    assert state["signatures"]["stale"] is False
    assert state["schedule"] == "0 3 * * *"


async def test_motor_hazir_degilse_tarama_baslamaz(
    store: Any, log: Any, events: Any,
) -> None:
    """ClamAV yoksa modül çökmez; başlatma anlaşılır bir hata verir."""
    service = build_service(store, log, events)

    with pytest.raises(EngineNotReady) as failure:
        await service.start("quick", actor="Test")

    assert "install-deps.sh" in str(failure.value)
    assert events.names() == []


# ---------------------------------------------------------------- tarama


async def test_temiz_tarama_kaydedilir_ve_olay_yayinlanir(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", CLEAN),
                            database=signature_dir(), paths=[str(target)])

    started = await service.start("quick", actor="Kemal Tuncer")
    assert started["kind"] == "quick"
    await service.wait()

    last = await service.last()
    assert last is not None
    assert last["verdict"] == VERDICT_CLEAN
    assert last["files"] == 4
    assert last["threatCount"] == 0
    assert last["skippedCount"] == 0
    assert last["actor"] == "Kemal Tuncer"

    assert events.names() == ["antivirus.scan_started", "antivirus.scan_completed"]
    assert events.payload("antivirus.scan_completed")["verdict"] == VERDICT_CLEAN


async def test_bulasmada_threat_found_yayinlanir(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    """Bildirim bu olaya bağlanır (ADR 0009); modül SMS göndermez."""
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", INFECTED),
                            database=signature_dir(), paths=[str(target)])

    await service.start("full", actor="Test")
    await service.wait()

    assert "antivirus.threat_found" in events.names()
    payload = events.payload("antivirus.threat_found")
    assert payload["count"] == 1
    assert payload["threats"][0]["name"] == "Eicar-Test-Signature"

    last = await service.last()
    assert last is not None
    assert last["verdict"] == VERDICT_INFECTED
    assert last["threatCount"] == 1


async def test_atlanan_yol_kayda_da_temiz_yazilmaz(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    """BAĞLAYICI: atlanan yol varken ne ekranda ne kayıtta "temiz" görünür."""
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", DENIED),
                            database=signature_dir(), paths=[str(target)])

    await service.start("quick", actor="Test")
    await service.wait()

    last = await service.last()
    assert last is not None
    assert last["verdict"] == VERDICT_INCOMPLETE
    assert last["verdict"] != VERDICT_CLEAN
    assert last["skippedCount"] == 1
    assert last["skipped"][0]["path"] == "/root/gizli"

    # Olay da aynı gerçeği taşır: dinleyen modül "temiz" sanmaz.
    assert events.payload("antivirus.scan_completed")["verdict"] == VERDICT_INCOMPLETE


async def test_ikinci_tarama_reddedilir(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    """İki tarama birbirinin G/Ç'sini yer; ikincisi 409 alır."""
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", SLOW),
                            database=signature_dir(), paths=[str(target)])

    await service.start("full", actor="Test")
    try:
        with pytest.raises(ScanBusy):
            await service.start("quick", actor="Test")
    finally:
        await service.cancel()
        await service.wait()

    last = await service.last()
    assert last is not None
    assert last["verdict"] == VERDICT_FAILED


async def test_hemen_durdurmak_istegi_dusurmez(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    """"Tara"dan hemen sonra "Durdur": tarayıcı henüz açılmamış olabilir.

    İstek düşerse kullanıcı düğmeye bastığını sanır ve tarama saatlerce
    koşmaya devam eder — durdurulamayan bir işten kötüsü, durdurulduğu
    sanılan bir iştir.
    """
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", SLOW),
                            database=signature_dir(), paths=[str(target)])

    await service.start("quick", actor="Test")
    result = await service.cancel()
    assert result["stopped"] is True
    await service.wait()

    last = await service.last()
    assert last is not None
    assert last["verdict"] == VERDICT_FAILED
    assert last["error"] == "Tarama durduruldu."
    assert (await service.state())["active"] is None


async def test_ilerleme_ekrandan_okunur(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str], tmp_path: Path,
) -> None:
    """Tarama sürerken `/state` "sürüyor" der ve yolları gösterir."""
    target = tmp_path / "veri"
    target.mkdir()
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", SLOW),
                            database=signature_dir(), paths=[str(target)])

    await service.start("full", actor="Test")
    state = await service.state()
    active = state["active"]
    assert active is not None
    assert active["kind"] == "full"
    assert active["paths"] == [str(target)]

    await service.cancel()
    await service.wait()


# ----------------------------------------------------------------- imza


async def test_eski_imza_olayi_bir_kez_yayinlanir(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str],
) -> None:
    """Saatlik denetim her turda olay yayınlarsa kanal kullanılamaz hâle gelir."""
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", CLEAN),
                            database=signature_dir(age_hours=100))

    first = await service.check_signatures()
    assert first["stale"] is True
    assert first["notified"] is True
    assert events.names() == ["antivirus.signatures_stale"]

    second = await service.check_signatures()
    assert second["stale"] is True
    assert second["notified"] is False
    assert events.names() == ["antivirus.signatures_stale"]


async def test_taze_imzada_olay_yok(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str],
) -> None:
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", CLEAN),
                            database=signature_dir(age_hours=3))

    result = await service.check_signatures()
    assert result["stale"] is False
    assert events.names() == []


async def test_imza_yasi_okunamazsa_eski_denmez(
    store: Any, log: Any, events: Any, make_binary: Callable[..., str],
    signature_dir: Callable[..., str],
) -> None:
    """Bilinmeyeni eski saymak, her makinede yanlış uyarı üretirdi."""
    service = build_service(store, log, events,
                            primary=make_binary("clamdscan", CLEAN),
                            database=signature_dir(ready=False))

    result = await service.check_signatures()
    assert result["known"] is False
    assert result["stale"] is False
    assert events.names() == []


# --------------------------------------------------------------- takvim


def test_cron_haftalik_tetikleyiciye_cevrilir() -> None:
    triggers = cron_to_triggers("0 3 * * *", label="tam tarama")
    assert len(triggers) == 7
    assert {item.time for item in triggers} == {"03:00"}
    assert sorted(item.day for item in triggers) == sorted(
        ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])


def test_cron_gun_listesi_desteklenir() -> None:
    triggers = cron_to_triggers("30 22 * * 0,6")
    assert sorted(item.day for item in triggers) == ["sat", "sun"]
    assert {item.time for item in triggers} == {"22:30"}


def test_anlasilmayan_cron_sessizce_yanlis_saate_kurulmaz() -> None:
    """Adım/aralık ifadeleri desteklenmiyor; boş liste dönüp varsayılana düşülür."""
    assert cron_to_triggers("*/15 * * * *") == []
    assert cron_to_triggers("0 3 1 * *") == []
    assert cron_to_triggers("bozuk") == []


async def test_bozuk_takvimde_varsayilana_dusulur(
    store: Any, log: Any, events: Any,
) -> None:
    service = build_service(store, log, events, extra={"schedule": "*/15 * * * *"})
    triggers = service.triggers()

    assert len(triggers) == 7
    assert {item.time for item in triggers} == {"03:00"}
    assert any("takvim" in message for _, message, _ in log.lines)
