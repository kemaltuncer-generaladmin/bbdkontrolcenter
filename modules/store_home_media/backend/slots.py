"""Ana ekran kayan görsellerinin saf dönüşümleri — ağa çıkmaz, durum tutmaz.

NEDEN AYRI DOSYA. Bu ekranın zor kısmı ağ değil KARAR: seçilen görsel yeterli
mi, hangi kenardan ne kadar kırpılacak, gönderilen liste gerçekten yazılabilir
mi. Bunlar servise gömülseydi tek satırı bile ağ olmadan sınanamazdı; burada
hepsi girdi→çıktı fonksiyonudur.

DOSYA 911 SATIRDAN BURAYA İNDİ (18.08.2026). Ekranın dört sekmesi vardı —
kayan görseller, tanıtım görselleri, ürün grupları, üst duyuru yazısı — ve
kodun çoğu o dört şeridi birbirinden ayırmaya harcanıyordu: `AREAS`,
`area_of`, `THEME_AREAS`, alan başına etiket/tekil ad/açıklama sözlükleri,
alan bazlı sıralama ve `merged_order`. Kullanıcı kararı üç sekmenin de
KALDIRILMASI oldu: bu ekranın tek işi, siteye ilk girişte dönen ~10 görseli
değiştirmek, sıralarını belirlemek ve tıklanınca nereye gideceklerini seçmek.

O ayıklamanın bir yan faydası daha var. `area_of` "tanımadığı değeri banner
sayar" diyordu; mağazadan gelen gerçek slider kaydının tipi tanınmıyor ve
şerit YANLIŞ SEKMEDE görünüyordu. Sekme kalmayınca yanlış sekme de kalmadı.

BEŞ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Yanıt camelCase, istek snake_case  → `pick` iki yazımı da dener.
 2. Tarayıcının bildirdiği ölçüye
    güvenilmez                         → `image_dimensions` başlığı kendisi okur.
 3. Görsel base64 gövdede taşınır      → `decode_image` tavanı ve türü denetler.
 4. Sabit çerçeveli önizleme kırpmayı
    GİZLER                             → `preview_box` + `crop_plan` gerçek oranı
                                          ve hangi kenarın gittiğini söyler.
 5. Yükleme adı latin-1 başlıkta
    patlar, uzantı yalan söyler        → `safe_filename` adı ASCII'ye indirir,
                                          uzantıyı gerçek MIME'dan yazar.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
import unicodedata
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Yazılabilecek en çok slayt. Mağaza ucu da aynı tavanı uyguluyor; burada
#: tekrar denetlenir ki kullanıcı hatayı ağa çıkmadan görsün.
MAX_SLIDES = 30

#: Slayt başlığının azami uzunluğu. Başlığı MÜŞTERİ GÖRMEZ (vitrin onu yalnız
#: görsel açılmadığında `alt` olarak basar); listede tanımaya yarar.
MAX_TITLE = 160

#: Görsel ölçüsü kararları.
SIZE_OK = "ok"
SIZE_BLURRY = "blurry"
SIZE_RATIO = "ratio"
SIZE_UNKNOWN = "unknown"

#: Sessizce geçilemeyen ölçü kararları. Yükleme bu iki durumda kullanıcının
#: uyarıyı GÖRÜP onaylamasını ister; onay "uyarıya rağmen yüklendi" olarak
#: `mod_store_home_media_assets` tablosuna geçer.
WARN_STATES = (SIZE_BLURRY, SIZE_RATIO)

#: Geçidin "BBD ucu henüz yayında değil" hata kodu (store_api → errors.py).
#: store_api BURADAN İMPORT EDİLMEZ (K3): modül modülü import etmez, kod bir
#: metin sözleşmesidir ve tanınmazsa hata metnine bakılır.
PENDING_CODE = "bbd_endpoint_missing"

#: MIME → dosya uzantısı. Yükleme adı İÇERİĞE göre yazılır, kullanıcının
#: verdiği uzantıya göre değil (bkz. `safe_filename`).
MIME_EXTS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        try:
            return int(float(str(value).replace(",", ".")))
        except (TypeError, ValueError):
            return default


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama anahtarı — `Şubat` ile `subat` eşleşsin."""
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return folded.lower()


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def camel(name: str) -> str:
    """`sort_order` → `sortOrder`."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def pick(raw: Any, *names: str) -> Any:
    """Aynı bilgiyi olası adlarından hangisi varsa oradan okur (TUZAK 1).

    YANIT camelCase, İSTEK snake_case. Bagisto'nun yönetici zarfı ÇIKTIYI
    camelCase'e çeviriyor (`sortOrder`, `imageUrl`, `updatedAt`); sorgu ve
    gövde tarafında ise camelCase sessizce yok sayılıyor. Yani okurken camel,
    yazarken snake.

    BU FONKSİYON BİR DÖNEM YALNIZ VERİLEN ADI DENİYORDU ve belirtisi şuydu:
    `sortOrder` her satırda 0 görünüyordu. Değer geliyordu, biz `sort_order`
    diye arıyorduk ve bulamayınca varsayılana düşüyorduk — yani ekran, sırayı
    hep "hepsi 0" sanıyordu. Aynı düzeltme `store_customers/backend/analytics.py`
    içinde zaten yapılmıştı; iki dosya arasındaki bu fark bir karar değil,
    unutulmuş bir kopyaydı.

    "Yok" ile "boş" ayrımı korunur: önce dolu değer aranır, bulunamazsa boş
    ama VAR OLAN değer döner, o da yoksa `None`.
    """
    if not isinstance(raw, dict):
        return None
    keys: list[str] = []
    for name in names:
        keys.append(name)
        alias = camel(name)
        if alias != name:
            keys.append(alias)
    for key in keys:
        if raw.get(key) not in (None, ""):
            return raw[key]
    for key in keys:
        if key in raw:
            return raw[key]
    return None


# ================================================================ ölçü kararı

def parse_size(value: Any) -> tuple[int, int]:
    """`"1920x640"` → `(1920, 640)`. Çözülemezse `(0, 0)`."""
    match = re.fullmatch(r"\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*", text(value))
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def size_verdict(width: Any, height: Any, wanted: tuple[int, int], *,
                 sharp_ratio: float = 1.0, tolerance: int = 8) -> dict[str, Any]:
    """Yüklenen görsel bu şerit için yeterli mi — ekranın ZORUNLU alt metni.

    Üç karar üretir:
      · `unknown` — ölçü okunamadı; sessiz kalmak yerine "bilinmiyor" denir.
      · `blurry`  — çözünürlük düşük: "Önerilen 1920x640; yüklenen 1200x400 —
                    mobilde bulanık."
      · `ratio`   — oran farklı: görsel kenarlardan kırpılacak.

    Karar RENKLE DEĞİL METİNLE taşınır (kit kuralı 7): `note` her zaman
    doludur ve ekranda görselin altında durur.
    """
    want_w, want_h = wanted
    got_w, got_h = as_int(width), as_int(height)

    recommended = f"{want_w}x{want_h}" if want_w > 0 and want_h > 0 else ""
    if want_w <= 0 or want_h <= 0:
        return {"state": SIZE_UNKNOWN,
                "note": "Bu şerit için önerilen ölçü tanımlı değil; karşılaştırma yapılamıyor.",
                "recommended": "", "uploaded": f"{got_w}x{got_h}" if got_w else ""}

    if got_w <= 0 or got_h <= 0:
        return {"state": SIZE_UNKNOWN,
                "note": f"Görselin en-boy ölçüsü okunamadı; ana ekran için uygun ölçü "
                        f"{recommended} piksel.",
                "recommended": recommended, "uploaded": ""}

    uploaded = f"{got_w}x{got_h}"
    head = f"Ana ekran {recommended} piksel ister; seçtiğiniz görsel {uploaded}"

    limit = max(0.1, float(sharp_ratio or 1.0))
    small = got_w < want_w * limit or got_h < want_h * limit

    want_ratio = want_w / want_h
    got_ratio = got_w / got_h
    skew = abs(got_ratio - want_ratio) / want_ratio * 100
    off = skew > max(0, int(tolerance))

    if small and off:
        return {"state": SIZE_BLURRY,
                "note": f"{head} — küçük kaldığı için telefonda bulanık çıkar; ayrıca "
                        "en-boy ölçüsü tutmadığından kenarlarından kesilir.",
                "recommended": recommended, "uploaded": uploaded}
    if small:
        return {"state": SIZE_BLURRY,
                "note": f"{head} — küçük kaldığı için telefonda bulanık çıkar.",
                "recommended": recommended, "uploaded": uploaded}
    if off:
        return {"state": SIZE_RATIO,
                "note": f"{head} — en-boy ölçüsü tutmuyor, görselin kenarları kesilir.",
                "recommended": recommended, "uploaded": uploaded}
    return {"state": SIZE_OK, "note": f"{head} — ölçü uygun, olduğu gibi görünür.",
            "recommended": recommended, "uploaded": uploaded}


def ratio_label(width: Any, height: Any) -> str:
    """En/boy oranını okunur biçimde verir: `1920x640` → `3:1`.

    Piksel ölçüsü "1920x640 mi 1600x600 mü daha uygun" sorusuna cevap vermez;
    KIRPMA orandan çıkar. Sadeleşmiş oran anlaşılmaz büyüklükteyse (`1000:333`)
    ondalığa düşülür — `3.00:1` bir şey anlatır, `1000:333` anlatmaz.
    """
    got_w, got_h = as_int(width), as_int(height)
    if got_w <= 0 or got_h <= 0:
        return ""
    divisor = math.gcd(got_w, got_h)
    short_w, short_h = got_w // divisor, got_h // divisor
    if short_w <= 40 and short_h <= 40:
        return f"{short_w}:{short_h}"
    return f"{got_w / got_h:.2f}:1"


def crop_plan(width: Any, height: Any, wanted: tuple[int, int], *,
              tolerance: int = 8) -> dict[str, Any]:
    """Görsel önerilen orana oturtulurken HANGİ KENARDAN, NE KADAR kırpılır.

    `size_verdict` "oran farklı" demekle yetiniyor; kullanıcının sorduğu soru
    ise "afişteki yazı kesilir mi". Cevap yüzdeyle değil KENARLA verilir:
    vitrin görseli `object-fit: cover` ile çizilir, yani fazlalık kenardan
    atılır — görsel ezilmez, kırpılır.

    Tolerans içinde kalan küçük kırpma da SÖYLENİR ama sorun sayılmaz;
    kullanıcı "biraz kırpılıyor ama sorun değil" ile "yazın gidiyor" arasını
    ayırabilsin diye `ok` alanı ayrı durur.
    """
    want_w, want_h = wanted
    got_w, got_h = as_int(width), as_int(height)

    if want_w <= 0 or want_h <= 0 or got_w <= 0 or got_h <= 0:
        return {"known": False, "ok": False, "axis": "", "percent": 0,
                "note": "Görselin ölçüsü okunamadı; nereden kesileceği hesaplanamıyor."}

    want = want_w / want_h
    got = got_w / got_h
    got_label, want_label = ratio_label(got_w, got_h), ratio_label(want_w, want_h)
    if got == want:
        return {"known": True, "ok": True, "axis": "", "percent": 0,
                "note": f"En-boy ölçüsü tam tutuyor ({got_label}); hiçbir yeri kesilmez."}

    if got > want:
        # Görsel önerilenden GENİŞ: yüksekliği doldurmak için yanlar gider.
        kept = got_h * want
        percent = round((got_w - kept) / got_w * 100)
        axis, edges = "yatay", "soldan ve sağdan"
    else:
        kept = got_w / want
        percent = round((got_h - kept) / got_h * 100)
        axis, edges = "dikey", "üstten ve alttan"

    skew = abs(got - want) / want * 100
    ok = skew <= max(0, int(tolerance))
    head = f"Görselin en-boy ölçüsü {got_label}, buraya {want_label} yakışır"
    note = (f"{head} — {edges} toplam %{percent} kesilir; bu kadarı göze çarpmaz."
            if ok else
            f"{head} — {edges} toplam %{percent} KESİLECEK. Görselin kenarında yazı ya da "
            "logo varsa gider; telefonda da aynı kesme olur.")
    return {"known": True, "ok": ok, "axis": axis, "percent": percent, "note": note}


def preview_box(width: Any, height: Any, *, max_width: int = 480,
                max_height: int = 260) -> dict[str, Any]:
    """Görselin GERÇEK oranını koruyan önizleme kutusu (piksel).

    Önizlemeyi sabit bir çerçeveye `cover` ile sığdırmak, tam da uyarmaya
    çalıştığımız kırpmayı GİZLERDİ: kullanıcı ekranda düzgün duran bir görsel
    görür, vitrinde kenarları kesilmiş olanı bulurdu. Bu yüzden ölçü SUNUCUDA
    ölçülen en/boydan hesaplanır (tarayıcı ölçüsüne güvenilmez, TUZAK 2) ve
    panel kutuyu bu değerlerle çizer.

    Ölçü bilinmiyorsa `(0, 0)` döner; panel o zaman kutuyu hiç çizmez ve
    "ölçü okunamadı" der — uydurma bir oran çizmez.
    """
    got_w, got_h = as_int(width), as_int(height)
    if got_w <= 0 or got_h <= 0:
        return {"width": 0, "height": 0, "ratio": ""}
    scale = min(max(1, int(max_width)) / got_w, max(1, int(max_height)) / got_h, 1.0)
    return {"width": max(1, round(got_w * scale)),
            "height": max(1, round(got_h * scale)),
            "ratio": ratio_label(got_w, got_h)}


def is_endpoint_pending(code: Any, message: Any) -> bool:
    """Hata "uç henüz yayında değil" mi — ekran bunu KIRMIZI HATA saymaz.

    Geçit 404'ü `bbd_endpoint_missing` koduna çeviriyor. Kod her zaman elde
    olmaz: hata `RuntimeError` gibi kodsuz bir tipe sarılmış gelebilir, o
    yüzden metne de bakılır. Yanlış tarafa düşmek pahalı değil — en kötü
    ihtimalle gerçek bir hata "bekleniyor" diye anlatılır ve metni yine ekranda
    durur; tersi (uç yokken kullanıcıya kırmızı hata göstermek) her gün
    tekrarlanan bir yanlış alarm olurdu (K7).
    """
    if text(code) == PENDING_CODE:
        return True
    lowered = fold(message)
    return PENDING_CODE in lowered or "henuz yayinda degil" in lowered


def safe_filename(name: Any, mime: str = "") -> str:
    """Yükleme adını ASCII'ye indirger ve uzantıyı İÇERİĞE göre yazar.

    İki tuzak birden:
      1. multipart başlığı latin-1'dir. `Ekran Görüntüsü.png` içindeki `ö`
         `UnicodeEncodeError` üretir ve kullanıcı anlamsız bir hata görür.
      2. Uzantı yalan söyleyebilir (`afis.jpg` ama içerik PNG). Sunucu
         uzantıya bakıyorsa yanlış ad 422 üretir; uzantı gerçek MIME'dan yazılır.
    """
    stem = re.sub(r"[^a-z0-9]+", "-", fold(name).rsplit(".", 1)[0]).strip("-")
    ext = MIME_EXTS.get(text(mime).lower(), "")
    return f"{stem[:60] or 'gorsel'}" + (f".{ext}" if ext else "")


# ============================================================== görsel okuma

def image_dimensions(data: bytes) -> tuple[str, int, int]:
    """Görselin `(mime, en, boy)` ölçüsünü BAŞLIKTAN okur (TUZAK 2).

    Tarayıcının bildirdiği ölçüye güvenilmez: istek elle de kurulabilir ve
    "1920x640 yükledim" diyen bir gövde 200x67'lik bir dosya taşıyabilir.
    Ölçü, dosyanın kendi başlığından okunur.

    Pillow KURULMAZ: dört biçimin başlığı toplam kırk satır tutuyor, buna
    karşılık Pillow modülün kurulumuna 40 MB'lık bir bağımlılık ekler (K11).
    Tanınmayan biçim `("", 0, 0)` döner; çağıran "bilinmiyor" der, uydurmaz.
    """
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return ("image/png", int.from_bytes(data[16:20], "big"),
                    int.from_bytes(data[20:24], "big"))

        if data[:6] in (b"GIF87a", b"GIF89a"):
            return ("image/gif", int.from_bytes(data[6:8], "little"),
                    int.from_bytes(data[8:10], "little"))

        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return ("image/webp", *_webp_size(data))

        if data[:2] == b"\xff\xd8":
            return ("image/jpeg", *_jpeg_size(data))
    except (IndexError, ValueError):
        return ("", 0, 0)
    return ("", 0, 0)


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """JPEG çerçeve başlığını (SOFn) arar. Bulunamazsa (0, 0)."""
    pos = 2
    end = len(data)
    while pos + 9 < end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            return (0, 0)
        # SOF0..SOF15; DHT (C4), JPG (C8) ve DAC (CC) çerçeve başlığı DEĞİLDİR.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            return (int.from_bytes(data[pos + 7:pos + 9], "big"),
                    int.from_bytes(data[pos + 5:pos + 7], "big"))
        pos += 2 + length
    return (0, 0)


def _webp_size(data: bytes) -> tuple[int, int]:
    """WebP'nin üç kabı: VP8 (kayıplı), VP8L (kayıpsız), VP8X (genişletilmiş)."""
    chunk = data[12:16]
    if chunk == b"VP8X":
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    if chunk == b"VP8 ":
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return (0, 0)


def decode_image(payload: Any, *, max_bytes: int, allowed: tuple[str, ...]) -> dict[str, Any]:
    """`data:` URL'i ya da çıplak base64'ü çözer ve denetler (TUZAK 3).

    Tauri'de dosya sistemi eklentisi yok; görsel tarayıcıda `FileReader` ile
    base64'e çevrilip gövdede taşınıyor. Tavan, MIME ve gerçek ölçü BURADA
    denetlenir — panelde denetlemek yetkilendirme değildir (K9).

    Dönüş: `{ok, error, bytes, mime, width, height, sha256, base64}`.
    """
    raw = text(payload)
    if not raw:
        return {"ok": False, "error": "Görsel verisi boş."}

    head = ""
    if raw.startswith("data:"):
        marker = raw.find(",")
        if marker < 0:
            return {"ok": False, "error": "Görsel verisi bozuk (data: başlığı kapanmamış)."}
        head = raw[5:marker]
        raw = raw[marker + 1:]
    raw = re.sub(r"\s+", "", raw)

    # Base64 %33 şişirir: tavanı ÇÖZMEDEN ÖNCE kabaca burada da yakalarız,
    # 50 MB'lık bir gövdeyi belleğe açmak için beklemeyiz.
    if len(raw) > int(max_bytes) * 4 // 3 + 1024:
        return {"ok": False,
                "error": f"Görsel çok büyük; tavan {int(max_bytes) // 1000} KB. "
                         "Yükleme öncesi küçültün."}
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return {"ok": False, "error": "Görsel verisi base64 olarak çözülemedi."}

    if len(data) > int(max_bytes):
        return {"ok": False,
                "error": f"Görsel {len(data) // 1000} KB; tavan {int(max_bytes) // 1000} KB."}

    mime, width, height = image_dimensions(data)
    if not mime:
        declared = head.split(";")[0].strip().lower()
        return {"ok": False,
                "error": "Dosya tanınmadı; PNG, JPEG ya da WebP olmalı."
                         + (f" (gövde {declared} diyor)" if declared else "")}
    if mime not in allowed:
        return {"ok": False,
                "error": f"{mime} kabul edilmiyor. İzinli türler: {', '.join(allowed)}."}

    return {"ok": True, "error": "", "bytes": len(data), "mime": mime,
            "width": width, "height": height,
            "sha256": hashlib.sha256(data).hexdigest(),
            "base64": raw}


# =================================================================== satırlar

def slide_row(raw: Any, *, index: int, wanted: tuple[int, int] = (0, 0),
              sharp_ratio: float = 1.0, tolerance: int = 8) -> dict[str, Any]:
    """Mağaza kaydını ekranın anladığı düz satıra çevirir.

    SIRA, LİSTEDEKİ KONUMDUR. Mağaza tarafında slaydın ayrı bir `sort_order`
    sütunu YOK: vitrin `options.images` dizisini olduğu gibi çiziyor. Yanıt
    yine de `index` taşıyor ve okunuyor — uç bir gün açık bir sıra alanı
    eklerse ekran onu görsün diye.
    """
    record = raw if isinstance(raw, dict) else {}
    image = text(pick(record, "image", "image_url", "path"))
    width = as_int(pick(record, "image_width", "width"))
    height = as_int(pick(record, "image_height", "height"))

    row: dict[str, Any] = {
        "index": as_int(pick(record, "index", "sort_order", "position"), index),
        "title": text(pick(record, "title", "name", "heading")),
        "link": text(pick(record, "link", "target", "url")),
        "image": image,
        # Mağaza yolu (`storage/…`) ile tarayıcının açabileceği tam adres AYRI
        # tutulur: liste yazarken yol gider, ekranda önizleme adresle çizilir.
        "imageUrl": text(pick(record, "image_url", "url")) or image,
        "imageWidth": width,
        "imageHeight": height,
        "sizeState": "",
        "sizeNote": "",
        "recommended": f"{wanted[0]}x{wanted[1]}" if wanted != (0, 0) else "",
    }

    # ÖLÇÜ BİLİNMİYORSA SESSİZ KALINIR. Mağazanın slayt ucu en/boy taşımıyor;
    # on satırın hepsinde "ölçü okunamadı" yazmak, gerçekten sorunlu olan tek
    # görseli gürültünün içinde kaybederdi. Karar yalnız ölçü ELDEYKEN yazılır
    # (taze seçilen dosyada `/image/check` ölçüyor).
    if width and height and wanted != (0, 0):
        verdict = size_verdict(width, height, wanted, sharp_ratio=sharp_ratio,
                               tolerance=tolerance)
        row["sizeState"] = verdict["state"]
        row["sizeNote"] = verdict["note"]

    row["issues"] = issues_of(row)
    return row


def issues_of(row: dict[str, Any]) -> list[str]:
    """Satırın gözle görülür eksikleri — listede aynı metinle çıkar.

    METİNLER İŞ DİLİNDE: "oran farklı" değil "kenarları kesilir". Kullanıcının
    kararı sonuca göre değişiyor, teknik tespite göre değil.
    """
    found: list[str] = []
    if not text(row.get("title")):
        found.append("adı yok")
    if not text(row.get("image")):
        found.append("görsel yok")
    if not text(row.get("link")):
        found.append("tıklayınca gideceği yer yok")
    if row.get("sizeState") == SIZE_BLURRY:
        found.append("görsel küçük, bulanık çıkar")
    elif row.get("sizeState") == SIZE_RATIO:
        found.append("ölçü tutmuyor, kenarları kesilir")
    return found


# ===================================================================== yazma

def normalize_slides(raw: Any) -> list[dict[str, str]]:
    """Ekranın gönderdiği listeyi mağaza gövdesine indirger.

    ÜÇ ALANDAN FAZLASI TAŞINMAZ: mağaza ucu da yalnız `title·link·image`
    kabul ediyor ve tanımadığı bir alanı geri göndermek, orada ne olduğunu
    bilmediğimiz bir değeri ezmek demektir.
    """
    out: list[dict[str, str]] = []
    for item in (raw or []):
        record = item if isinstance(item, dict) else {}
        out.append({
            "title": text(pick(record, "title")),
            "link": text(pick(record, "link")),
            "image": text(pick(record, "image", "image_url", "path")),
        })
    return out


def slides_error(slides: list[dict[str, str]]) -> str:
    """Yazma öncesi iş kuralı denetimi — panelde de var ama asıl kapı burası (K9).

    LİSTE BOŞ YAZILAMAZ. Boş liste ana sayfanın en üstünü bomboş bırakır ve bu,
    "Kaydet"e yanlışlıkla basmanın bedeli olamayacak kadar görünür bir sonuçtur.
    """
    if not slides:
        return ("Liste boş kaydedilemez: ana sayfanın en üstü bomboş kalır. En az bir "
                "görsel bırakın.")
    if len(slides) > MAX_SLIDES:
        return f"En çok {MAX_SLIDES} görsel olabilir; şu an {len(slides)} tane var."
    for order, slide in enumerate(slides, start=1):
        if not slide["title"]:
            return (f"{order}. görselin adı boş. Müşteri bu adı görmez; siz listede bunu "
                    "görürsünüz — “Eylül kırtasiye kampanyası” gibi tanıyacağınız bir ad yazın.")
        if len(slide["title"]) > MAX_TITLE:
            return f"{order}. görselin adı çok uzun; en çok {MAX_TITLE} karakter olabilir."
        if not slide["image"]:
            return (f"{order}. sıradaki görsel boş. Dosya seçip yükleyin ya da o satırı "
                    "listeden çıkarın.")
        link = slide["link"]
        if link and not re.match(r"^(https?://|/)", link):
            return (f"{order}. görselin adresi ya `https://` ile (başka bir siteye gider) ya "
                    "da `/` ile (kendi sitemizde kalır) başlamalı. Örnek: `/kampanya`.")
    return ""


def move(order: list[Any], index: int, step: int) -> list[Any]:
    """Klavye (`Ctrl+↑/↓`) ve sürükle-bırak aynı işi yapar; kural tek yerde."""
    target = index + step
    if index < 0 or index >= len(order) or target < 0 or target >= len(order):
        return list(order)
    out = list(order)
    out[index], out[target] = out[target], out[index]
    return out
