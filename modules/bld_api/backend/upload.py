"""Görsel yükleme hazırlığı — base64 çözme, tür ve boyut denetimi.

NEDEN AYRI DOSYA: burada ağ yok. Yüklemenin yanlış gidebileceği her şey
(bozuk base64, desteklenmeyen tür, sınırı aşan boyut, yol içeren dosya adı)
istek çıkmadan burada yakalanır ve testi de ağsız yazılır.

NEDEN BASE64, MULTIPART DEĞİL — SÖZLEŞMENİN EN SERT KENARI

    İmza kanonik dizesi `sha256($request->getContent())` içerir, yani
    GÖNDERİLEN HAM BAYTI imzalar. Multipart gövde sınır dizeleri (boundary)
    taşır ve gövdeyi yeniden kodlayan herhangi bir ara katman — vekil, yük
    dengeleyici, gzip, WAF — baytları değiştirir. Değişen tek bayt imzayı
    bozar ve sunucu `401 UNAUTHENTICATED` döndürür: arıza sahada "sır yanlış"
    ya da "saat kaymış" gibi görünür. Görsel yüklemenin kimlik doğrulama
    hatası kılığına girmesi, teşhis edilmesi en zor arıza türüdür.

    Bu yüzden görsel JSON gövdesinin İÇİNDE base64 olarak gider
    (`products.md` → "Neden base64, neden multipart değil"). JSON gövde
    `_encode` içinde bir kez üretilip aynen gönderiliyor ve diğer bütün yazma
    uçlarıyla aynı yoldan geçiyor.

MIME İÇERİKTEN OKUNUR, DOSYA ADINDAN DEĞİL

    Sunucu türü `finfo_buffer` ile içerikten okuyor (`products.md`, doğrulama
    sırası 3. adım). Buradaki denetim aynı kuralı taşır: sihirli baytlara
    bakılır. Uzantıya güvenmek, `.jpg` adlı bir PHP dosyasını yüklemenin en
    bilinen yoludur; istemci tarafında uzantıya güvenip sunucuda içeriğe
    bakmak ise ikisinin ayrıştığı her dosyada anlamsız bir 422 üretirdi.

İKİ SINIR VAR, KÜÇÜĞÜ UYGULANIR

    1. AYAR SINIRI (`max_upload_mb`, varsayılan 8 MB): bu kurulumun kendi
       kararı; belleğe alınacak gövdenin üst sınırı.
    2. UÇ SINIRI: ürün görseli ucu **çözülmüş** 5 MB'ın üstünü `422` ile
       reddediyor (`products.md`). 10 MB'lık bir dosyayı gönderip sunucudan
       ret beklemek hem hız kovasından pay yer hem de kullanıcıya "BLD
       sunucusu isteği doğrulayamadı" gibi anlamsız bir metin gösterir.
       Sınır burada bilinir ve istek HİÇ GÖNDERİLMEZ.
"""

from __future__ import annotations

import base64
import binascii
import re
from typing import Any

from .errors import BldApiError

#: Ayar verilmediğinde geçerli üst sınır (MB). Sekiz seçildi çünkü base64
#: ~%33 şişiriyor: 5 MB'lık bir görsel ~6,7 MB gövde eder ve ayar sınırının
#: uç sınırının altında kalması, uç hatasını ayar hatası gibi gösterirdi.
DEFAULT_MAX_UPLOAD_MB = 8

#: Ürün görseli ucunun sunucu tarafı sınırı — **çözülmüş** 5 MB
#: (`docs/control/products.md` → `PUT /{menu}/image`, doğrulama adım 2).
PRODUCT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

#: Sunucunun kabul ettiği görsel türleri (`products.md`, doğrulama adım 3).
#: `image/bmp` ve `image/gif` YOKTUR — sözleşme üçünü sayıyor, dördüncüsü
#: uydurulmaz.
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")

#: …ve karşılık gelen uzantılar. Uzantı YALNIZ dosya adını tamamlamak için
#: kullanılır; tür kararı sihirli bayttan çıkar (bkz. `sniff_mime`).
IMAGE_EXTS = ("jpg", "jpeg", "png", "webp")

_MIME_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

#: `data:image/png;base64,` öneki — tarayıcıdan/kabuktan gelen değerde bulunur.
_DATA_URI = re.compile(r"^data:[^;,]*(;[^;,]*)*,")

#: Dosya adında yalnız bunlar kalır. Yol ayracı, tırnak ve satır sonu, JSON
#: gövdesine yazılan bir değerde işi olmayan karakterlerdir; sunucu kayıt
#: adını zaten kendisi üretiyor ve ad yalnız uzantı ile görüntüleme içindir.
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
        raise BldApiError(
            "Yüklenecek dosya içeriği okunamadı: beklenen bayt ya da base64 metin.",
            code="payload",
        )

    text = _DATA_URI.sub("", content.strip())
    text = "".join(text.split())
    if not text:
        raise BldApiError("Yüklenecek dosya boş.", code="payload")
    try:
        # `validate=True`: sessizce yok saymak yerine bozuk veriyi söylesin.
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as failure:
        raise BldApiError(
            "Dosya içeriği çözülemedi: base64 bozuk. Görseli yeniden seçip deneyin.",
            code="payload",
        ) from failure


def safe_filename(filename: str, *, fallback: str = "yukleme") -> str:
    """Dosya adını gövdeye yazılabilir hâle getirir.

    Ad sunucuda **kayıt adı olarak kullanılmaz** (`products.md`, adım 4:
    "İstemciden gelen yolu diske yazmak yol geçişi demekti"), yalnız uzantı ve
    görüntüleme için okunur. Yine de yol ayracı ve tırnak burada temizlenir:
    denetim izine ve panele düşen bir değerin `../` taşıması için sebep yok.
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


def sniff_mime(data: bytes) -> str:
    """Sihirli bayttan tür okur; tanınmazsa boş metin.

    Sunucunun `finfo_buffer` çağrısının bu üç tür için karşılığı. Kapsam
    bilinçli olarak dar: sözleşme üç tür kabul ediyor ve dördüncüsünü burada
    tanımak, sunucunun reddedeceği bir dosyayı gönderilebilir kılardı.
    """
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    # WebP: "RIFF" + 4 bayt uzunluk + "WEBP". Uzunluk alanı atlanır.
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _too_large(size: float, limit: int) -> BldApiError:
    """Boyut reddi — sayı MB olarak yazılır, kullanıcı bayt saymaz."""
    return BldApiError(
        f"Görsel çok büyük: {size / (1024 * 1024):.1f} MB. "
        f"Bu uçta üst sınır {limit / (1024 * 1024):.0f} MB; dosya gönderilmedi.",
        code="payload",
    )


def prepare_upload(
    content: Any,
    *,
    filename: str,
    max_bytes: int,
    allowed_mimes: tuple[str, ...] = IMAGE_MIMES,
) -> dict[str, Any]:
    """İçeriği çözer, boyutunu ve türünü doğrular, gövde parçası üretir.

    Dönüş: `{filename, mime, content, size, content_base64}`. Sınırı aşan ya
    da desteklenmeyen dosya için `BldApiError` fırlatır — istek HİÇ
    GÖNDERİLMEZ.

    DOĞRULAMA SIRASI SÖZLEŞMEDEKİ SIRADIR (`products.md`): önce base64, sonra
    boyut, sonra tür. Sıranın kendisi sözleşmede yazılı olduğu için burada da
    aynıdır: aynı bozuk dosya iki tarafta aynı hatayı almalı, yoksa "panelde
    başka, sunucuda başka hata" diye bir arıza sınıfı doğar.

    Boyut denetimi ÖNCE kaba (base64 uzunluğu), sonra kesin (çözülmüş bayt)
    yapılır: sınırı aşan bir metni belleğe açmanın anlamı yok.
    """
    limit = max(1, int(max_bytes))
    if isinstance(content, str) and len(content) / _B64_RATIO > limit:
        raise _too_large(len(content) / _B64_RATIO, limit)

    data = decode_content(content)
    if not data:
        raise BldApiError("Yüklenecek dosya boş.", code="payload")
    if len(data) > limit:
        raise _too_large(len(data), limit)

    mime = sniff_mime(data)
    if allowed_mimes and mime not in allowed_mimes:
        raise BldApiError(
            f"Desteklenmeyen görsel türü ({mime or 'tanınmadı'}). Kabul edilenler: "
            f"{', '.join(allowed_mimes)}. Tür dosya adından değil İÇERİKTEN okunur; "
            "uzantısını değiştirmek yardımcı olmaz.",
            code="payload",
        )

    name = safe_filename(filename)
    if extension(name) not in IMAGE_EXTS:
        # Uzantısız ya da yanlış uzantılı ad: türü bildiğimize göre doğrusunu
        # ekleyelim. Sunucu adı yalnız uzantı için okuyor.
        name = f"{name}.{_MIME_EXT[mime]}"

    # Gövdeye giden metin BURADA yeniden üretilir: gelen dizede `data:` öneki,
    # satır sonu ya da dolgu farkı olabilir ve gövde bayt bayt imzalandığı
    # için "ne gönderdiğimizi" tek bir yerden bilmek gerekir.
    return {
        "filename": name,
        "mime": mime,
        "content": data,
        "size": len(data),
        "content_base64": base64.b64encode(data).decode("ascii"),
    }


def describe(part: dict[str, Any]) -> dict[str, Any]:
    """Denetim izine ve kuru prova yanıtına giren özet — İÇERİK GİRMEZ.

    `00-genel.md` §8.2 base64 içeriğin denetim izine yazılmasını açıkça
    yasaklıyor: "Görselde yalnız `{"bytes": ..., "mime": ...}` yazılır."
    Ham bayt ne JSON'a çevrilebilir ne de denetim izinde işe yarar.
    """
    return {"filename": part["filename"], "mime": part["mime"], "bytes": part["size"]}


def json_body(part: dict[str, Any]) -> dict[str, Any]:
    """`PUT /control/products/{menu}/image` gövdesinin görsel alanları.

    Alan adları sözleşmeden: `filename` + `content_base64`. Üçüncü bir ad
    (`image`, `data`) uydurulmaz — Laravel tanımadığı alanı sessizce yok
    sayar ve ekran "yüklendi" derken hiçbir şey yüklenmemiş olurdu.
    """
    return {"filename": part["filename"], "content_base64": part["content_base64"]}
