"""Destek paketi — tanılamayı tek dosyada dışarı çıkarmak.

NE VAR İÇİNDE: uygulama sürümü, platform künyesi, modüllerin durumu, ayarların
hangi katmandan geldiği ve günlüğün son satırları. Yani "bende çalışmıyor"
diyen kişinin anlatmakta zorlandığı her şey.

MASKELEME BU DOSYANIN İÇİNDE, DIŞINDA DEĞİL. Paketi kuran tek fonksiyon ham
metni alır ve maskelenmiş baytları döndürür; çağıranın maskelemeyi "unutması"
mümkün değildir. Çıkan zip e-postaya, sohbete, hata kaydına gidiyor — K8 orada
da geçerli.

ZIP NEDEN DOSYAYA YAZILIYOR DA GÖVDEDE DÖNMÜYOR. Kabuk ile çekirdek arasındaki
taşıyıcı (`core_request`, src-tauri/src/main.rs) yanıtı METİN olarak okur
(`String::from_utf8_lossy`); ikili bir gövde oradan sağlam geçmez. Bu yüzden
paket diske yazılır ve uç yalnız yolunu döndürür — ekran da kullanıcıya nereye
kaydedildiğini söyler.
"""

from __future__ import annotations

import io
import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from km_core.http.masking import mask_text, mask_value

README = """Kontrol Merkezi — destek paketi
================================

Bu paket bir arıza kaydına eklenmek üzere üretildi.

MASKELENMİŞTİR. Sunucu adresleri, belirteç/anahtar parçaları, telefon
numaraları ve e-posta adresleri içerikten çıkarılmıştır; yerlerinde
"<sunucu>" ve "⟨gizlendi⟩" işaretleri durur. Biçim korunur, içerik gitmiştir:
satırın ne anlattığı okunur, kimliği okunmaz.

Yerel döngü adresi (127.0.0.1) bilerek maskelenmemiştir — bir sır değil,
uygulamanın mimarisidir.

İçindekiler
-----------
  ozet.json      Sürüm, platform, modül durumları
  ayarlar.json   Ayarların hangi katmandan geldiği (DEĞERLER MASKELİ)
  gunluk.log     Günlüğün son satırları
"""


def read_log_tail(path: Path, lines: int = 400, max_bytes: int = 512_000) -> str:
    """Günlüğün son satırları. Dosya yoksa boş döner — bu bir arıza değildir.

    Dosyanın tamamı okunmaz: `data/launcher.log` aylarca büyüyor ve tanılama
    için gereken son sayfadır. Okuma sondan `max_bytes` ile sınırlanır ki
    yüz megabaytlık bir günlük belleği doldurmasın.
    """
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > max_bytes:
                handle.seek(size - max_bytes)
                handle.readline()  # yarım kalan ilk satır atılır
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def app_summary(*, version: str, modules: list[dict[str, Any]],
                extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Paketin ve Tanılama sekmesinin ortak künyesi.

    İkisi aynı yerden beslenir: ekranda görünen ile pakete giren ayrışırsa
    destek eden kişi ile kullanıcı farklı şeylere bakar.
    """
    return {
        "olusturuldu": datetime.now().astimezone().isoformat(timespec="seconds"),
        "surum": version,
        "platform": {
            "sistem": platform.system(),
            "surum": platform.release(),
            "mimari": platform.machine(),
            "python": sys.version.split()[0],
        },
        "moduller": modules,
        **(extra or {}),
    }


def bundle_bytes(*, summary: dict[str, Any], settings: list[dict[str, Any]],
                 log_text: str) -> bytes:
    """Zip'i kurar. GİREN HAM, ÇIKAN MASKELİ — arada başka yol yoktur."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("OKUBENI.txt", README)
        archive.writestr(
            "ozet.json",
            json.dumps(mask_value(summary), ensure_ascii=False, indent=2),
        )
        archive.writestr(
            "ayarlar.json",
            json.dumps(mask_value(settings), ensure_ascii=False, indent=2),
        )
        archive.writestr("gunluk.log", mask_text(log_text))
    return buffer.getvalue()


def bundle_name(when: datetime | None = None) -> str:
    moment = when or datetime.now().astimezone()
    return f"destek-paketi-{moment.strftime('%Y-%m-%d-%H%M')}.zip"
