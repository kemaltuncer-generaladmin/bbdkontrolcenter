"""Yüklenecek görselin SAF incelemesi — ağa çıkmaz, diske dokunmaz.

NEDEN AYRI DOSYA. Yüklemenin reddedileceği her sebep (bozuk base64,
desteklenmeyen tür, sınırı aşan boyut) ve kullanıcıya söylenmesi gereken her
şey (küçük çözünürlük, aşırı en-boy oranı) burada girdi→çıktı fonksiyonudur.
Servise gömülseydi hiçbiri ağ olmadan test edilemezdi.

NEDEN BURADA DA ÖLÇÜLÜYOR. Panel dosyayı `Image` nesnesiyle ölçüp kullanıcıya
ANINDA söyler — beklemeden. Ama arayüzdeki denetim yetkilendirme değildir
(K9): istemci şemayı atlatabilir, eski bir panel sürümü açık kalabilir.
Denetim izine yazılan "ne yüklendi" bilgisi buradan çıkar.

NEDEN KENDİ BASE64 ÇÖZÜCÜSÜ VAR. `store_api/upload.py` aynı işi yapıyor ama
modül modülü import etmez (K3), geçidin iç dosyasına bağlanmak K4'ün tek
kapısını da delerdi. Buradaki çözme ÖLÇMEK içindir; telde giden içerik yine
panelin gönderdiği metindir ve onu geçit çözer.

BOYUT OKUMA neden elle yazıldı: tek iş için (genişlik/yükseklik) Pillow
bağımlılığı eklemek, kurulumu 60 MB büyütür ve bir C kütüphanesi getirir.
Üç biçimin başlığı toplam 40 satır; okunması da bakımı da ucuz.
"""

from __future__ import annotations

import base64
import binascii
import re
from math import gcd
from typing import Any

#: Panelin ve bu ekranın kabul ettiği türler. Sunucu BMP'yi de kabul ediyor
#: ama teklif edilmez: sıkıştırmasız BMP aynı görsel için 20 kat yer kaplar ve
#: 4 MB sınırına 800×800'de bile takılır.
ACCEPTED_MIMES = ("image/jpeg", "image/png", "image/webp")

#: Uzantı ↔ tür. Sunucu dosyayı `Str::random(40).'.'.$ext` diye kaydediyor;
#: uzantı yanlışsa dosya yanlış adla diske düşer.
EXT_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}
MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

#: Tanınan ama kabul edilmeyen türlerin okunur adı. "image/gif kabul edilmiyor"
#: yerine "GIF kabul edilmiyor" demek için.
TYPE_NAMES = {
    "image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WebP",
    "image/gif": "GIF", "image/bmp": "BMP", "image/tiff": "TIFF",
    "image/avif": "AVIF", "image/heic": "HEIC", "application/pdf": "PDF",
}

#: Vitrin ızgarası kareye yakın görsel bekler; bunun üstündeki oran kırpılır.
DEFAULT_MAX_RATIO = 1.6

#: Önerilen en küçük kenar. Bagisto vitrini ürün sayfasında 800 piksel
#: genişlikte gösteriyor; altındaki görsel büyütülerek çiziliyor.
DEFAULT_MIN_SIDE = 800

#: Bunun üstü gereksiz: sayfa ağırlaşır, mobilde yavaş açılır.
HUGE_SIDE = 4000

#: JPEG çerçeve başlığı işaretçileri (SOF0…SOF15; DHT/JPG/DAC hariç).
_SOF_MARKERS = frozenset({0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})

_DATA_URI = re.compile(r"^data:[^;,]*(;[^;,]*)*,")
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ]+")

#: Base64 metnin çözülmeden önceki kaba boyutu: 4 karakter 3 bayt eder.
_B64_RATIO = 4 / 3


class ImageError(ValueError):
    """Dosya yüklenemez. Mesaj DOĞRUDAN kullanıcıya gösterilir; teknik terim yok."""


# =============================================================== çözme · ad

def decode(content: Any) -> bytes:
    """Panelden gelen içeriği bayta çevirir (`data:` öneki ve satır sonu dahil)."""
    if isinstance(content, bytes | bytearray | memoryview):
        return bytes(content)
    if not isinstance(content, str):
        raise ImageError("Dosya içeriği okunamadı; görseli yeniden seçin.")
    text = "".join(_DATA_URI.sub("", content.strip()).split())
    if not text:
        raise ImageError("Dosya boş.")
    try:
        # `validate=True`: bozuk veriyi sessizce kırpmak yerine söylesin.
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as failure:
        raise ImageError("Dosya okunamadı (içerik bozuk geldi); yeniden seçip deneyin.") \
            from failure


def safe_name(filename: str, *, fallback: str = "gorsel") -> str:
    """Dosya adını multipart başlığına yazılabilir hâle getirir.

    TUZAK: ad `Content-Disposition` başlığına giriyor. Yol ayracı, tırnak ya
    da satır sonu taşıyan bir ad ya yanlış klasöre düşer ya da başlığı bozar.
    """
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _UNSAFE_NAME.sub("_", name).strip("._ ")
    return (name or fallback)[:120]


def extension(filename: str) -> str:
    name = safe_name(filename)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def with_extension(filename: str, mime: str) -> str:
    """Adın uzantısını gerçek türe göre düzeltir."""
    name = safe_name(filename)
    want = MIME_EXT.get(mime, "")
    if not want:
        return name
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem or 'gorsel'}.{want}"


# ================================================================ tür · boyut

def sniff(data: bytes) -> str:
    """Türü İÇERİKTEN okur. Uzantıya güvenilmez: `.jpg` adlı PDF yüklenebiliyor."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return "image/avif"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1"):
        return "image/heic"
    if data[:5] == b"%PDF-":
        return "application/pdf"
    return ""


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """JPEG çerçeve başlığını arar.

    TUZAK: boyut dosyanın başında DEĞİL. Önce EXIF, ICC profili ve küçük
    önizleme gelir; uzunlukları değişkendir. Bu yüzden işaretçi zinciri
    yürünür ve ilk SOF bloğuna kadar gidilir. Yükseklik ÖNCE, genişlik sonra
    yazılıdır — ters okumak 800×600'ü 600×800 gösterir.
    """
    pos, size = 2, len(data)
    while pos + 9 < size:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xFF:                      # dolgu baytı
            pos += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:   # uzunluksuz işaretçi
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            return None
        if marker in _SOF_MARKERS:
            return (int.from_bytes(data[pos + 7:pos + 9], "big"),
                    int.from_bytes(data[pos + 5:pos + 7], "big"))
        pos += 2 + length
    return None


def _webp_size(data: bytes) -> tuple[int, int] | None:
    """WebP'nin ÜÇ biçimi var; hepsi boyutu başka yerde tutar."""
    if len(data) < 30:
        return None
    chunk = data[12:16]
    if chunk == b"VP8 ":                        # kayıplı
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L":                        # kayıpsız
        if data[20] != 0x2F:
            return None
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X":                        # genişletilmiş (saydamlık/animasyon)
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    return None


def _bmp_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 26:
        return None
    # Yükseklik NEGATİF olabilir: yukarıdan aşağı taranan BMP'yi böyle işaretler.
    return (abs(int.from_bytes(data[18:22], "little", signed=True)),
            abs(int.from_bytes(data[22:26], "little", signed=True)))


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    return (int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little"))


def dimensions(data: bytes) -> tuple[int, int] | None:
    """(genişlik, yükseklik) — okunamazsa `None`. Sıfır uydurulmaz."""
    reader = {
        "image/png": _png_size, "image/jpeg": _jpeg_size, "image/webp": _webp_size,
        "image/bmp": _bmp_size, "image/gif": _gif_size,
    }.get(sniff(data))
    size = reader(data) if reader else None
    if not size or size[0] <= 0 or size[1] <= 0:
        return None
    return size


# ============================================================ kalite uyarısı

def ratio_text(width: int, height: int) -> str:
    """`1200×400` → `3:1`. Sadeleşmeyen oran ondalıkla yazılır (`1,8:1` gibi)."""
    if width <= 0 or height <= 0:
        return "—"
    divisor = gcd(width, height) or 1
    short_w, short_h = width // divisor, height // divisor
    if short_w <= 20 and short_h <= 20:
        return f"{short_w}:{short_h}"
    if width >= height:
        return f"{width / height:.1f}".replace(".", ",") + ":1"
    return "1:" + f"{height / width:.1f}".replace(".", ",")


def quality_notes(width: int, height: int, *, min_width: int = DEFAULT_MIN_SIDE,
                  min_height: int = DEFAULT_MIN_SIDE,
                  max_ratio: float = DEFAULT_MAX_RATIO) -> list[str]:
    """Somut uyarılar. "Görsel küçük" DEMEZ; kaç piksel olduğunu ve sonucunu söyler."""
    notes: list[str] = []
    if width <= 0 or height <= 0:
        return notes
    if width < min_width or height < min_height:
        notes.append(
            f"Önerilen en az {min_width}×{min_height}; yüklenen {width}×{height} — "
            "listede ve ürün sayfasında bulanık görünür.")
    long_side, short_side = max(width, height), min(width, height)
    if max_ratio > 0 and long_side > short_side * max_ratio:
        notes.append(
            f"Görsel {ratio_text(width, height)} oranında ({width}×{height}); vitrin "
            "ızgarası kareye yakın görsel bekler, kenarlardan kırpılır.")
    if long_side > HUGE_SIDE:
        notes.append(
            f"{width}×{height} gereğinden büyük; vitrin en çok {HUGE_SIDE} piksel "
            "kullanıyor, fazlası sayfayı yavaşlatır.")
    return notes


# ================================================================= inceleme

def inspect_upload(content: Any, *, filename: str, mime: str = "", max_bytes: int,
                   min_width: int = DEFAULT_MIN_SIDE, min_height: int = DEFAULT_MIN_SIDE,
                   max_ratio: float = DEFAULT_MAX_RATIO) -> dict[str, Any]:
    """Yüklemeden ÖNCEKİ tek kapı: çöz, türü doğrula, boyutu ölç, uyarıları üret.

    Dönüş: `{filename, mime, bytes, width, height, warnings}`.
    Reddedilen dosya için `ImageError` fırlatır — istek hiç kurulmaz.

    Boyut denetimi ÖNCE kaba (base64 uzunluğundan), sonra kesin (çözülmüş
    bayt) yapılır: 40 MB'lık bir metni ölçmek için belleğe açmanın anlamı yok.
    """
    limit = max(1, int(max_bytes))
    if isinstance(content, str) and len(content) / _B64_RATIO > limit:
        raise _too_large(int(len(content) / _B64_RATIO), limit)

    data = decode(content)
    if not data:
        raise ImageError("Dosya boş.")
    if len(data) > limit:
        raise _too_large(len(data), limit)

    kind = sniff(data)
    if not kind:
        raise ImageError(
            f"`{safe_name(filename)}` bir görsel değil (içeriğinden tanınamadı). "
            "JPEG, PNG ya da WebP yükleyin.")
    if kind not in ACCEPTED_MIMES:
        raise ImageError(
            f"`{safe_name(filename)}` {TYPE_NAMES.get(kind, kind)} biçiminde; kabul "
            "edilenler JPEG, PNG ve WebP. Görseli bu biçimlerden birine çevirin.")

    warnings: list[str] = []
    name = safe_name(filename)
    declared = str(mime or "").strip().lower()
    fitting = {MIME_EXT[kind], "jpeg"} if kind == "image/jpeg" else {MIME_EXT[kind]}
    if extension(name) not in fitting:
        # Uzantı içerikle uyuşmuyor (ya da hiç yok). Sunucu türü uzantıdan da
        # doğruluyor ve dosyayı o uzantıyla kaydediyor; yanlış ad, vitrinde
        # açılmayan görsel demektir.
        fixed = with_extension(name, kind)
        warnings.append(
            f"Dosya adı `{name}` ama içerik {TYPE_NAMES[kind]}; ad `{fixed}` olarak düzeltildi.")
        name = fixed
    elif declared and declared != kind:
        warnings.append(
            f"Tarayıcı türü `{declared}` dedi, içerik {TYPE_NAMES[kind]}; içerik esas alındı.")

    size = dimensions(data)
    if size is None:
        # Tür tanındı ama başlık çözülemedi: dosya yarım inmiş olabilir.
        # Yüklemeyi ENGELLEMEZ (sunucu son sözü söyler) ama sessiz de kalmaz.
        warnings.append(
            "Görselin ölçüsü okunamadı; dosya yarım inmiş olabilir. Yüklendikten sonra "
            "ürün sayfasında gözle doğrulayın.")
        width = height = 0
    else:
        width, height = size
        warnings.extend(quality_notes(width, height, min_width=min_width,
                                      min_height=min_height, max_ratio=max_ratio))

    return {"filename": name, "mime": kind, "bytes": len(data),
            "width": width, "height": height, "warnings": warnings}


def _too_large(size: int, limit: int) -> ImageError:
    """Boyut reddi MB ile yazılır; kullanıcı bayt saymaz."""
    return ImageError(
        f"Dosya {size / (1024 * 1024):.1f} MB; bu ekranın sınırı "
        f"{limit / (1024 * 1024):.0f} MB. Görseli küçültüp yeniden deneyin — "
        "istek mağazaya gönderilmedi.")
