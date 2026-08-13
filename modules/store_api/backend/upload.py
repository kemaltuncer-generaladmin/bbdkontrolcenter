"""Dosya yükleme hazırlığı — base64 çözme, doğrulama, boyut freni.

NEDEN AYRI DOSYA: burada ağ yok. Yüklemenin yanlış gidebileceği her şey
(bozuk base64, desteklenmeyen tür, sınırı aşan boyut, yol içeren dosya adı)
istek çıkmadan burada yakalanır ve testi de ağsız yazılır.

NEDEN BASE64: panel Tauri kabuğunda çalışıyor ve fs eklentisi yok; görsel
tarayıcıya `FileReader` ile okunuyor ve JSON gövdede base64 olarak geliyor.
Çözme işi geçitte yapılır — 20 ekranın her biri kendi çözücüsünü yazmasın
(ve `data:` öneki, satır sonu, dolgu farklılıklarında farklı biçimde
yanılmasın).

İKİ SINIR VAR, KÜÇÜĞÜ UYGULANIR
    1. AYAR SINIRI (`max_upload_mb`, varsayılan 24 MB): bu kurulumun kendi
       kararı; belleğe alınacak base64 gövdenin üst sınırı.
    2. UÇ SINIRI: Bagisto'nun ürün görseli işlemcisi 4 MB'ın üstünü 422 ile
       reddediyor (`AdminCatalogProductImageProcessor::MAX_BYTES`). 10 MB'lık
       bir dosyayı gönderip sunucudan ret beklemek, hem hız kovasından pay
       yer hem de kullanıcıya "Mağaza isteği doğrulayamadı" gibi anlamsız bir
       metin gösterir. Sınır burada bilinir ve istek HİÇ GÖNDERİLMEZ.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from .errors import StoreApiError

#: Ayar verilmediğinde geçerli üst sınır (MB).
DEFAULT_MAX_UPLOAD_MB = 24

#: Ürün görseli ucunun sunucu tarafı sınırı — 4 MB.
#: Kaynak: vendor/bagisto/bagisto-api/src/Admin/State/
#: AdminCatalogProductImageProcessor.php → `MAX_BYTES = 4 * 1024 * 1024`.
PRODUCT_IMAGE_MAX_BYTES = 4 * 1024 * 1024

#: Sunucunun kabul ettiği görsel türleri (aynı dosyadaki `ALLOWED_MIMES`).
IMAGE_MIMES = ("image/bmp", "image/jpeg", "image/jpg", "image/png", "image/webp")

#: …ve uzantıları (`ALLOWED_EXTS`). Sunucu "mime UYGUN **ya da** uzantı
#: uygun" diyor; buradaki denetim aynı kuralı taşır, daha katı davranıp
#: sunucunun kabul edeceği dosyayı reddetmez.
IMAGE_EXTS = ("bmp", "jpeg", "jpg", "png", "webp")

_EXT_MIME = {"bmp": "image/bmp", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "png": "image/png", "webp": "image/webp"}

#: `data:image/png;base64,` öneki — tarayıcıdan gelen değerde bulunur.
_DATA_URI = re.compile(r"^data:[^;,]*(;[^;,]*)*,")

#: Dosya adında yalnız bunlar kalır. Yol ayracı, tırnak ve satır sonu
#: multipart başlığına yazılan bir değerde işi olmayan karakterlerdir.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._\- ]+")

#: Base64 metnin çözülmeden önceki kaba sınırı: 4 karakter 3 bayt eder.
#: Önce bunun bakılması, 200 MB'lık bir metni belleğe açmamak içindir.
_B64_RATIO = 4 / 3


def decode_content(content: Any) -> bytes:
    """Panelden gelen içeriği bayta çevirir.

    `bytes` geldiyse dokunulmaz; `str` geldiyse base64 kabul edilir
    (`data:` öneki ve satır sonları temizlenir). Bozuk base64 anlaşılır
    hataya çevrilir — kullanıcı "Invalid base64-encoded string" görmemeli.
    """
    if isinstance(content, bytes | bytearray | memoryview):
        return bytes(content)
    if not isinstance(content, str):
        raise StoreApiError("Yüklenecek dosya içeriği okunamadı: beklenen bayt ya da base64 metin.",
                            code="payload")

    text = _DATA_URI.sub("", content.strip())
    text = "".join(text.split())
    if not text:
        raise StoreApiError("Yüklenecek dosya boş.", code="payload")
    try:
        # `validate=True`: sessizce yok saymak yerine bozuk veriyi söylesin.
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as failure:
        raise StoreApiError(
            "Dosya içeriği çözülemedi: base64 bozuk. Görseli yeniden seçip deneyin.",
            code="payload",
        ) from failure


def safe_filename(filename: str, *, fallback: str = "yukleme") -> str:
    """Dosya adını multipart başlığına yazılabilir hâle getirir.

    TUZAK: ad doğrudan `Content-Disposition` başlığına giriyor. Yol ayracı,
    tırnak ve satır sonu barındıran bir ad ya sunucuda yanlış klasöre düşer
    ya da başlığı bozar. Uzantı KORUNUR — sunucu türü uzantıdan da doğruluyor.
    """
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    name = _UNSAFE_NAME.sub("_", name).strip("._ ")
    return (name or fallback)[:120]


def extension(filename: str) -> str:
    """Küçük harfli uzantı; yoksa boş metin."""
    name = safe_filename(filename)
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def max_upload_bytes(max_upload_mb: Any) -> int:
    """Ayardaki MB değerini bayta çevirir. Geçersiz değer varsayılana düşer."""
    try:
        megabytes = int(max_upload_mb)
    except (TypeError, ValueError):
        megabytes = DEFAULT_MAX_UPLOAD_MB
    if megabytes <= 0:
        megabytes = DEFAULT_MAX_UPLOAD_MB
    return megabytes * 1024 * 1024


def _too_large(size: float, limit: int) -> StoreApiError:
    """Boyut reddi — sayı MB olarak yazılır, kullanıcı bayt saymaz."""
    return StoreApiError(
        f"Dosya çok büyük: {size / (1024 * 1024):.1f} MB. "
        f"Bu uçta üst sınır {limit / (1024 * 1024):.0f} MB; dosya gönderilmedi.",
        code="payload",
    )


def prepare_upload(
    content: Any,
    *,
    filename: str,
    mime: str = "",
    field: str = "image",
    max_bytes: int,
    allowed_mimes: tuple[str, ...] = IMAGE_MIMES,
    allowed_exts: tuple[str, ...] = IMAGE_EXTS,
) -> dict[str, Any]:
    """İçeriği çözer, türünü ve boyutunu doğrular, multipart parçası üretir.

    Dönüş: `{field, filename, mime, content, size}`. Sınırı aşan ya da
    desteklenmeyen dosya için `StoreApiError` fırlatır — istek HİÇ
    GÖNDERİLMEZ. Boyut denetimi ÖNCE kaba (base64 uzunluğu), sonra kesin
    (çözülmüş bayt) yapılır: sınırı aşan bir metni belleğe açmanın anlamı yok.
    """
    limit = max(1, int(max_bytes))
    if isinstance(content, str) and len(content) / _B64_RATIO > limit:
        raise _too_large(len(content) / _B64_RATIO, limit)

    data = decode_content(content)
    if not data:
        raise StoreApiError("Yüklenecek dosya boş.", code="payload")
    if len(data) > limit:
        raise _too_large(len(data), limit)

    name = safe_filename(filename)
    ext = extension(name)
    kind = str(mime or "").strip().lower() or _EXT_MIME.get(ext, "application/octet-stream")

    # Sunucunun kuralı: mime YA DA uzantı uygunsa kabul. Aynısı burada — daha
    # katı davranıp sunucunun alacağı dosyayı reddetmek olmaz. Liste boşsa
    # (tür kısıtı olmayan uç) denetim atlanır.
    if (allowed_mimes or allowed_exts) and kind not in allowed_mimes and ext not in allowed_exts:
        raise StoreApiError(
            f"Desteklenmeyen dosya türü ({kind or 'bilinmiyor'}). "
            f"Kabul edilenler: {', '.join(allowed_exts)}.",
            code="payload",
        )
    if not ext and kind in _EXT_MIME.values():
        # Sunucu dosyayı `Str::random(40).'.'.$ext` diye kaydediyor; uzantısız
        # ad 'jpg' varsayımına düşüyor. Mime'ı biliyorsak doğru uzantıyı verelim.
        for candidate, value in _EXT_MIME.items():
            if value == kind:
                name = f"{name}.{candidate}"
                break

    return {"field": field, "filename": name, "mime": kind, "content": data, "size": len(data)}


def describe(part: dict[str, Any]) -> dict[str, Any]:
    """Denetim izine ve kuru prova yanıtına giren özet — İÇERİK GİRMEZ.

    Ham bayt ne JSON'a çevrilebilir ne de denetim izinde işe yarar; orada
    "hangi dosya, ne kadar, hangi tür" bilgisi yeterlidir.
    """
    return {"field": part["field"], "filename": part["filename"], "mime": part["mime"],
            "bytes": part["size"]}


def multipart_files(part: dict[str, Any]) -> dict[str, tuple[str, bytes, str]]:
    """`httpx`'in `files=` parametresine verilecek sözlük.

    TUZAK: değer BAYT olarak verilir, açık dosya nesnesi olarak değil. 429
    sonrası tek yineleme yapılabiliyor; dosya nesnesi ikinci istekte imleci
    sonda olduğu için BOŞ gövde gönderirdi.
    """
    return {part["field"]: (part["filename"], part["content"], part["mime"])}
