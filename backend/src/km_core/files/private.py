"""Kişisel veri taşıyan dosyaları yalnız kullanıcıya okunur biçimde yazmak.

NEDEN AYRI BİR YER. Modüller diske PDF, CSV ve veritabanı yedeği yazıyor;
bu dosyaların içinde öğrenci adı, veli telefonu ve bakiye var. `write_bytes`
dosyayı umask'a bırakır ve tipik masaüstünde 0644 çıkar — makinedeki her
kullanıcı ve her süreç okuyabilir. Denetimde tam olarak bu bulundu:
`data/kontrol-merkezi.sqlite` 0644 idi ve 47 öğrenci + 47 veli telefonu
açıkta duruyordu.

TASARIM. İzin dosya OLUŞTURULURKEN verilir, sonradan `chmod` ile değil:
arada geçen kısa sürede içerik zaten diske yazılmış ve okunabilir olur.
`os.open(..., 0o600)` bu yarışı kapatır.

ÇIKTI KAYDI DA BURADA DOĞAR (ADR 0019). Dosyayı yazan tek yer burasıdır;
"çıktı üretildi" kaydını her modülün ayrıca bildirmesi yirmi ayrı çağrı
noktası demekti ve biri mutlaka unutulurdu. Kayıt `outputs_log` içinde,
başarısız olsa bile dosya üretimini durdurmadan düşer (K7).
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

log = structlog.get_logger("km.files")

# Dosya: sahibi okur/yazar. Klasör: sahibi girer/listeler.
FILE_MODE = 0o600
DIR_MODE = 0o700


def ensure_private_dir(path: Path) -> Path:
    """Klasörü oluşturur ve 0700'e çeker (varsa da düzeltir)."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(DIR_MODE)
    return path


def write_private(path: Path, content: bytes) -> Path:
    """`content`'i `path`'e 0600 ile yazar; klasörünü 0700'e çeker.

    Var olan bir dosyanın üzerine yazılırken izni de tazelenir — daha önce
    geniş izinle oluşmuş bir dosya böylece kendiliğinden düzelir.
    """
    ensure_private_dir(path.parent)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(handle, "wb") as target:
        target.write(content)
    # O_CREAT kipi yalnız YENİ dosyada geçerlidir; var olan dosyanın izni
    # değişmez. Üzerine yazma durumunda da daraltmak için açıkça uygulanır.
    path.chmod(FILE_MODE)

    # Kayıt EN SONDA düşer: dosya artık tamdır ve izni daraltılmıştır. Import
    # fonksiyon içindedir — `km_core/store/db.py` bu modülü import ediyor,
    # ters yöndeki bağımlılık açılışta döngü kurmasın.
    try:
        from km_core.files.outputs_log import record_write

        record_write(path, content)
    except Exception as failure:  # noqa: BLE001 — kayıt düşmezse dosya yine teslim edilir (K7)
        log.warning("çıktı kaydı düşürülemedi", path=str(path), error=str(failure))

    return path
