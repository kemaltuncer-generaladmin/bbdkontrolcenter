"""Log dosyası — kurulan uygulamanın TEK çıktı kanalı.

NEDEN BU TEST VAR. Paketlenmiş uygulamada `sys.stdout` hiçbir yere çıkmaz: kabuk
`windows_subsystem = "windows"` ile derlenir, sidecar `CREATE_NO_WINDOW` ile
başlatılır, konsol yoktur. Bu, temiz bir kurulumda somut bir kilit üretiyordu —
veritabanı boşken açılış yöneticisinin PIN'i üretilip YALNIZCA loga yazılıyor
(`http/app.py` → `_bootstrap_admin`); o log yutulunca PIN'i kimse öğrenemiyor ve
uygulamaya HİÇ girilemiyor.

SINANAN ŞEY "dosya oluştu mu" DEĞİL, "SATIR DOSYAYA DÜŞTÜ MÜ". Aradaki fark bu
işi bir kez zaten kırdı: `structlog`un varsayılanı `PrintLoggerFactory`'dir ve
doğrudan `sys.stdout`a yazar, stdlib `logging` tutucularına HİÇ uğramaz. Dosya
tutucusu eklenmişti, dosya doğru izinle oluşuyordu, ve dosya BOŞTU. Sadece
varlığa bakan bir test o hatayı yeşil geçerdi.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from km_core.main import setup_logging


def test_structlog_satiri_dosyaya_dusuyor(tmp_path: Path) -> None:
    hedef = tmp_path / "logs" / "kontrol-merkezi.log"
    setup_logging("INFO", hedef)

    structlog.get_logger("deneme").warning("açılış yöneticisi", pin="815571")

    icerik = hedef.read_text(encoding="utf-8")
    assert "açılış yöneticisi" in icerik
    # Değer de yazılmalı: PIN'i okunamayan bir satır, sorunu hiç çözmezdi.
    assert "815571" in icerik


def test_log_dosyasi_yalniz_sahibine_okunur(tmp_path: Path) -> None:
    """İçinden açılış PIN'i geçiyor.

    Sırrı stdout'tan kurtarıp makinedeki herkesin okuyabildiği bir dosyaya
    sermek, çözdüğünden büyük bir sorun açardı.
    """
    hedef = tmp_path / "logs" / "km.log"
    setup_logging("INFO", hedef)
    structlog.get_logger("deneme").info("bir şey")

    assert hedef.stat().st_mode & 0o077 == 0, "log dosyası başkasına açık"


def test_turkce_karakter_bozulmadan_yaziliyor(tmp_path: Path) -> None:
    """Dosya UTF-8'dir, işletim sisteminin kod sayfası ne olursa olsun.

    Windows'ta varsayılan cp1252'dir; kodlama sabitlenmezse Türkçe satırlar ya
    bozulur ya da `UnicodeEncodeError` ile logu tümüyle düşürür — aynı hata
    paketleme betiğini de bir kez düşürmüştü.
    """
    hedef = tmp_path / "logs" / "km.log"
    setup_logging("INFO", hedef)
    structlog.get_logger("deneme").warning("girip PIN'inizi değiştirin — şğüöç")

    assert "değiştirin — şğüöç" in hedef.read_text(encoding="utf-8")


def test_yazilamayan_yol_sidecari_dusurmez(tmp_path: Path) -> None:
    """Log tutamamak, uygulamayı hiç açmamak için yeterli bir sebep değildir."""
    engel = tmp_path / "engel"
    engel.write_text("bu bir dosya, klasör değil")

    # `engel/logs/km.log` — var olan bir DOSYANIN altına klasör açılamaz.
    setup_logging("INFO", engel / "logs" / "km.log")
    structlog.get_logger("deneme").info("yine de koştu")
