"""ClamAV motoru — birincil/yedek yol, eksik kurulum, atlanan yol, zaman aşımı.

Gerçek tarama koşturulmaz; `clamdscan` ve `clamscan` yerine ClamAV çıktısını
taklit eden kabuk betikleri kullanılır (bkz. conftest).
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from antivirus_backend.engine import (
    STATE_MISSING,
    STATE_PREPARING,
    STATE_READY,
    VERDICT_CLEAN,
    VERDICT_FAILED,
    VERDICT_INCOMPLETE,
    VERDICT_INFECTED,
    ClamAvEngine,
)

CLEAN = """
echo "/tmp/a.txt: OK"
echo "/tmp/b.txt: OK"
echo "----------- SCAN SUMMARY -----------"
echo "Engine version: 1.5.3"
echo "Scanned files: 2"
echo "Infected files: 0"
exit 0
"""

INFECTED = """
echo "/tmp/kotu.bin: Eicar-Test-Signature FOUND"
echo "----------- SCAN SUMMARY -----------"
echo "Scanned files: 3"
echo "Infected files: 1"
exit 1
"""

# İzin hatası: dosya VAR ama okunamadı. ClamAV bunu ERROR satırıyla bildirir ve
# 2 ile çıkar.
DENIED = """
echo "/tmp/acik.txt: OK"
echo "/root/gizli: Access denied. ERROR"
echo "----------- SCAN SUMMARY -----------"
echo "Scanned files: 1"
echo "Infected files: 0"
exit 2
"""

DAEMON_DOWN = """
echo "ERROR: Could not connect to clamd on LocalSocket /run/clamav/clamd.ctl" >&2
exit 2
"""

SLOW = """
sleep 30
"""


def build(make_binary: Callable[..., str], log: Any, *, primary: str = "",
          fallback: str = "", database: str = "") -> ClamAvEngine:
    return ClamAvEngine(
        config={
            "clamdscan": primary or "km-yok-clamdscan",
            "clamscan": fallback or "km-yok-clamscan",
            "database_path": database or "/km-yok-clamav",
        },
        log=log,
    )


# ----------------------------------------------------------------- kurulum


async def test_clamav_hic_yokken_anlasilir_hata(log: Any, tmp_path: Path) -> None:
    """Kurulu değilse modül çökmez; ekran ne yapılacağını söyler."""
    engine = build(lambda *_, **__: "", log)

    status = await engine.status()
    assert status["state"] == STATE_MISSING
    assert status["installed"] is False
    assert "install-deps.sh" in status["note"]

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=5)
    assert outcome.verdict == VERDICT_FAILED
    assert "kurulu değil" in outcome.error
    # Kurulu olmayan motor "temiz" demez.
    assert outcome.verdict != VERDICT_CLEAN


async def test_imzalar_indirilirken_hata_degil_hazirlaniyor(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
) -> None:
    """İlk kurulumda freshclam ~300 MB indirir; bu bir arıza değildir."""
    engine = build(
        make_binary, log,
        primary=make_binary("clamdscan", CLEAN),
        database=signature_dir(ready=False),
    )
    status = await engine.status()

    assert status["state"] == STATE_PREPARING
    assert status["installed"] is True
    assert status["ready"] is False
    assert "freshclam" in status["note"]
    # "hata" değil "hazırlanıyor": ekranın tonu buna bakıyor.
    assert "arıza değildir" in status["note"]


async def test_imzalar_hazirken_motor_hazir(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
) -> None:
    engine = build(
        make_binary, log,
        primary=make_binary("clamdscan", CLEAN),
        database=signature_dir(age_hours=2),
    )
    status = await engine.status()

    assert status["state"] == STATE_READY
    assert status["daemon"] is True
    assert status["database"]["ready"] is True
    assert 1.5 < status["database"]["ageHours"] < 2.5


# -------------------------------------------------------- birincil / yedek


async def test_daemon_yokken_yedege_dusulur(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """clamd kapalıysa tarama clamscan ile yapılır — sessizce başarısız olmaz."""
    primary = make_binary("clamdscan", DAEMON_DOWN, ping=2)
    fallback = make_binary("clamscan", CLEAN)
    engine = build(make_binary, log, primary=primary, fallback=fallback,
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=10)

    assert outcome.engine == fallback
    assert outcome.verdict == VERDICT_CLEAN

    # Durum ekranı da yedek yolu söyler: kullanıcı taramanın neden yavaş
    # olduğunu görmeli.
    status = await engine.status()
    assert status["state"] == STATE_READY
    assert status["daemon"] is False
    assert status["engine"] == fallback
    assert "yavaş" in status["note"]


async def test_daemon_varken_birincil_yol_kullanilir(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    primary = make_binary("clamdscan", CLEAN)
    fallback = make_binary("clamscan", INFECTED)
    engine = build(make_binary, log, primary=primary, fallback=fallback,
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=10)

    # Yedeğe DÜŞÜLMEDİ: birincil başarılı olduğunda ikinci komut hiç koşmaz.
    assert outcome.engine == primary
    assert outcome.verdict == VERDICT_CLEAN


async def test_izin_hatasi_yedege_dusurmez(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Çıkış 2 tek başına "daemon yok" demek değildir.

    İzin hatası yüzünden aynı taramayı saatler süren yavaş yoldan
    tekrarlamak, düzeltmediği bir sorun için bütün geceyi harcamaktı.
    """
    primary = make_binary("clamdscan", DENIED)
    fallback = make_binary("clamscan", CLEAN)
    engine = build(make_binary, log, primary=primary, fallback=fallback,
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=10)

    assert outcome.engine == primary
    assert outcome.verdict == VERDICT_INCOMPLETE


# ------------------------------------------------------------ atlanan yol


async def test_atlanan_yol_varken_temiz_denmez(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """BAĞLAYICI KURAL (ADR 0009 §4): erişilemeyen yol varken sonuç temiz değildir."""
    engine = build(make_binary, log, primary=make_binary("clamdscan", DENIED),
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=10)

    assert outcome.verdict == VERDICT_INCOMPLETE
    assert outcome.verdict != VERDICT_CLEAN
    assert not outcome.threats

    # Atlanan yol RAPORDA LİSTELENİR; sayısı da yolu da görünür.
    blocking = outcome.blocking
    assert len(blocking) == 1
    assert blocking[0]["path"] == "/root/gizli"
    assert "Access denied" in blocking[0]["reason"]
    assert outcome.as_dict()["skippedCount"] == 1


async def test_okunamayan_yol_taramadan_once_yakalanir(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Yol VAR ama okunamıyor: tarayıcıya gitmeden atlanan sayılır."""
    engine = build(make_binary, log, primary=make_binary("clamdscan", CLEAN),
                   database=signature_dir())

    okunur = tmp_path / "acik"
    okunur.mkdir()
    kapali = tmp_path / "kapali"
    kapali.mkdir()
    kapali.chmod(0o000)
    # Kök kullanıcı her dosyayı okur; orada bu testin sınadığı durum oluşmaz.
    if os.access(str(kapali), os.R_OK):
        kapali.chmod(0o755)
        pytest.skip("kök kullanıcı olarak koşuluyor; okunamayan yol üretilemiyor")
    try:
        outcome = await engine.scan([str(okunur), str(kapali)], timeout=10)
    finally:
        kapali.chmod(0o755)

    assert outcome.verdict == VERDICT_INCOMPLETE
    assert [entry["path"] for entry in outcome.blocking] == [str(kapali)]


async def test_haric_tutulan_ve_olmayan_yol_temizi_engellemez(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Hariç tutmak yöneticinin kararı, olmayan yolda dosya da yok.

    İkisi de raporda GÖRÜNÜR ama "temiz"i engellemez: engelleyen şey,
    içinde ne olduğunu bilmediğimiz yollardır.
    """
    engine = build(make_binary, log, primary=make_binary("clamdscan", CLEAN),
                   database=signature_dir())

    okunur = tmp_path / "acik"
    okunur.mkdir()
    haric = tmp_path / "haric"
    haric.mkdir()

    outcome = await engine.scan(
        [str(okunur), str(haric), str(tmp_path / "hic-yok")],
        timeout=10, exclude=[str(haric)],
    )

    assert outcome.verdict == VERDICT_CLEAN
    assert outcome.blocking == []
    report = outcome.as_dict()
    assert report["skippedCount"] == 0
    assert report["excludedCount"] == 2


async def test_taranacak_yol_kalmadiysa_basarisiz(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Hiçbir yol taranamadıysa "temiz" değil, "başarısız"."""
    engine = build(make_binary, log, primary=make_binary("clamdscan", CLEAN),
                   database=signature_dir())

    outcome = await engine.scan([str(tmp_path / "yok-1"), str(tmp_path / "yok-2")], timeout=10)

    assert outcome.verdict == VERDICT_FAILED
    assert "Taranacak yol bulunamadı" in outcome.error


# ---------------------------------------------------------------- bulaşma


async def test_bulasma_bulununca_tehdit_listelenir(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    engine = build(make_binary, log, primary=make_binary("clamdscan", INFECTED),
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=10)

    assert outcome.verdict == VERDICT_INFECTED
    assert outcome.threats == [{"path": "/tmp/kotu.bin", "name": "Eicar-Test-Signature"}]
    assert outcome.files == 3


# ------------------------------------------------------------ zaman aşımı


async def test_zaman_asiminda_surec_oldurulur(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Takılan tarama çekirdeği kilitlemez (K7); sonuç asla "temiz" olmaz."""
    engine = build(make_binary, log, primary=make_binary("clamdscan", SLOW),
                   database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    started = time.monotonic()
    outcome = await engine.scan([str(target)], timeout=0.5)
    elapsed = time.monotonic() - started

    # 30 saniyelik betik yarım saniyede kesildi: süreç gerçekten öldürüldü.
    assert elapsed < 10
    assert outcome.verdict == VERDICT_FAILED
    assert "Zaman aşımı" in outcome.error


async def test_zaman_asiminda_yedege_dusulmez(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    """Yavaş yolu bir de baştan koşturmak, aşımı ikiye katlamak olurdu."""
    fallback = make_binary("clamscan", CLEAN)
    engine = build(make_binary, log, primary=make_binary("clamdscan", SLOW),
                   fallback=fallback, database=signature_dir())

    target = tmp_path / "veri"
    target.mkdir()
    outcome = await engine.scan([str(target)], timeout=0.5)

    assert outcome.engine != fallback
    assert outcome.verdict == VERDICT_FAILED


# --------------------------------------------------------------- durdurma


async def test_stop_calisan_taramayi_oldurur(
    make_binary: Callable[..., str], signature_dir: Callable[..., str], log: Any,
    tmp_path: Path,
) -> None:
    engine = build(make_binary, log, primary=make_binary("clamdscan", SLOW),
                   database=signature_dir())
    target = tmp_path / "veri"
    target.mkdir()

    task = asyncio.get_running_loop().create_task(engine.scan([str(target)], timeout=60))
    for _ in range(100):
        await asyncio.sleep(0.05)
        if engine.stop():
            break
    else:  # pragma: no cover - süreç açılmadıysa test zaten anlamsız
        task.cancel()
        raise AssertionError("tarama süreci hiç başlamadı")

    outcome = await task
    assert outcome.verdict == VERDICT_FAILED
    assert outcome.error == "Tarama durduruldu."
