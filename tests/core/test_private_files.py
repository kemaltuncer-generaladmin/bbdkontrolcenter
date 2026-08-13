"""Kişisel veri taşıyan çıktıların disk izinleri.

Denetimde bulunan gerçek açık: indirilen yedekler, rapor PDF/CSV'leri ve QR
kart PDF'leri 0644 ile yazılıyordu; makinedeki her kullanıcı 47 öğrencinin
adını ve 47 veli telefonunu okuyabiliyordu. Bu testler düzeltmenin geri
dönmediğini diskten doğrular — kodu okumakla yetinmez.
"""

import os
import stat
from pathlib import Path

from km_core.config.loader import secure_local_file
from km_sdk import ensure_private_dir, write_private


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_dosya_yalniz_sahibine_okunur(tmp_path: Path) -> None:
    path = write_private(tmp_path / "yedek.sqlite", b"veli telefonu")
    assert mode(path) == 0o600
    assert path.read_bytes() == b"veli telefonu"


def test_klasor_de_daraltilir(tmp_path: Path) -> None:
    write_private(tmp_path / "ic" / "rapor.pdf", b"%PDF")
    assert mode(tmp_path / "ic") == 0o700


def test_umask_gevsek_olsa_bile_izin_degismez(tmp_path: Path) -> None:
    # Gevşek bir umask altında `write_bytes` 0644 üretirdi; bu yol üretmemeli.
    onceki = os.umask(0o000)
    try:
        path = write_private(tmp_path / "kartlar.pdf", b"%PDF")
    finally:
        os.umask(onceki)
    assert mode(path) == 0o600


def test_var_olan_genis_izinli_dosya_duzeltilir(tmp_path: Path) -> None:
    # Düzeltmeden ÖNCE oluşmuş dosyalar da ilk üzerine yazmada daralmalı:
    # O_CREAT kipi var olan dosyaya uygulanmaz, bu yüzden açıkça chmod ediliyor.
    path = tmp_path / "eski.csv"
    path.write_bytes(b"eski")
    path.chmod(0o644)

    write_private(path, b"yeni")
    assert mode(path) == 0o600
    assert path.read_bytes() == b"yeni"


def test_ensure_private_dir_var_olan_klasoru_de_daraltir(tmp_path: Path) -> None:
    hedef = tmp_path / "exports"
    hedef.mkdir(mode=0o755)
    ensure_private_dir(hedef)
    assert mode(hedef) == 0o700


def test_local_yaml_okunmadan_once_daraltilir(tmp_path: Path) -> None:
    # config/local.yaml sırların durduğu yer (K8) ve elle oluşturulduğu için
    # 0664 ile doğuyor. Yapılandırma OKUNMADAN önce daraltılmalı.
    path = tmp_path / "local.yaml"
    path.write_text("auth:\n  bootstrap_pin: '123456'\n", encoding="utf-8")
    path.chmod(0o664)

    assert secure_local_file(path) is True
    assert mode(path) == 0o600


def test_zaten_dar_dosyaya_dokunulmaz(tmp_path: Path) -> None:
    path = tmp_path / "local.yaml"
    path.write_text("x: 1\n", encoding="utf-8")
    path.chmod(0o600)
    assert secure_local_file(path) is False
    assert mode(path) == 0o600


def test_olmayan_dosya_hata_vermez(tmp_path: Path) -> None:
    assert secure_local_file(tmp_path / "yok.yaml") is False


def test_calistirma_biti_korunur(tmp_path: Path) -> None:
    # 0755 → 0700: sahibin çalıştırma hakkı korunur, başkasınınki düşer.
    path = tmp_path / "local.yaml"
    path.write_text("x: 1\n", encoding="utf-8")
    path.chmod(0o755)
    assert secure_local_file(path) is True
    assert mode(path) == 0o700


def test_rapor_klasoru_zinciri_daraltilir(tmp_path: Path, monkeypatch) -> None:
    # Ara klasörler mkdir(parents=True) ile 0755 doğuyordu; klasör adları da
    # bilgi taşır (hangi ay, hangi alan rapor üretilmiş).
    from datetime import datetime

    from km_core.files import outputs

    desktop = tmp_path / "Masaüstü"
    desktop.mkdir()
    monkeypatch.setattr(outputs, "desktop_dir", lambda: desktop)

    when = datetime(2026, 8, 13)
    target = outputs.report_dir("Kantin", fallback=tmp_path / "yedek", when=when)

    assert target == desktop / "Kontrol Merkezi" / "Raporlar" / "Kantin" / "2026" / "08 - Ağustos"
    for level in (
        desktop / "Kontrol Merkezi" / "Raporlar",
        desktop / "Kontrol Merkezi" / "Raporlar" / "Kantin",
        desktop / "Kontrol Merkezi" / "Raporlar" / "Kantin" / "2026",
        target,
    ):
        assert mode(level) == 0o700, level


def test_alt_kirilim_ara_rafi_acar_ve_hicbir_duzey_gevsek_kalmaz(
    tmp_path: Path, monkeypatch,
) -> None:
    # Mağaza tek düzeye sığmıyor: satış, kargo, finans ve denetim raporları aynı
    # ay klasörüne dökülürse liste okunmaz olur. Alt kırılım bir raf daha açar.
    # Buradaki asıl sınama İZİNDİR: ara raf `mkdir(parents=True)` ile umask
    # altında doğar ve daraltma zinciri onu atlarsa 0755 kalırdı.
    from datetime import datetime

    from km_core.files import outputs

    desktop = tmp_path / "Masaüstü"
    desktop.mkdir()
    monkeypatch.setattr(outputs, "desktop_dir", lambda: desktop)

    when = datetime(2026, 8, 13)
    target = outputs.report_dir(
        "Mağaza", subcategory="Kargo", fallback=tmp_path / "yedek", when=when,
    )

    root = desktop / "Kontrol Merkezi" / "Raporlar"
    assert target == root / "Mağaza" / "Kargo" / "2026" / "08 - Ağustos"
    for level in (
        root,
        root / "Mağaza",
        root / "Mağaza" / "Kargo",
        root / "Mağaza" / "Kargo" / "2026",
        target,
    ):
        assert mode(level) == 0o700, level


def test_alt_kirilim_bos_birakilinca_kantin_davranisi_degismez(
    tmp_path: Path, monkeypatch,
) -> None:
    # Geriye dönük uyum: mevcut modüller `subcategory` vermiyor ve vermemeli.
    from datetime import datetime

    from km_core.files import outputs

    desktop = tmp_path / "Masaüstü"
    desktop.mkdir()
    monkeypatch.setattr(outputs, "desktop_dir", lambda: desktop)

    when = datetime(2026, 8, 13)
    varsayilan = outputs.report_dir("Kantin", fallback=tmp_path / "y", when=when)
    bos_string = outputs.report_dir(
        "Kantin", subcategory="   ", fallback=tmp_path / "y", when=when,
    )
    assert varsayilan == bos_string


def test_kullanicinin_kendi_yolu_hiyerarsiyi_ezer(tmp_path: Path) -> None:
    from km_core.files import outputs

    hedef = tmp_path / "kendi-klasorum"
    result = outputs.report_dir("Kantin", fallback=tmp_path, configured=str(hedef))
    assert result == hedef
    assert mode(hedef) == 0o700
