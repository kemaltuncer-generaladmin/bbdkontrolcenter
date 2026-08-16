"""Ana sayfa slotlarının saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın zor kısmı ağ değil KARAR: yüklenen görsel yeterli
mi, yayın penceresi bugün açık mı, sıra listesi gerçekten aynı slotların bir
permütasyonu mu. Bunlar servise gömülseydi tek satırı bile ağ olmadan test
edilemezdi; burada hepsi girdi→çıktı fonksiyonudur.

DOKUZ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Sıra ucu GLOBAL liste ister      → `merged_order` diğer alanları korur.
 2. Tarayıcının bildirdiği ölçüye
    güvenilmez                       → `image_dimensions` başlığı kendisi okur.
 3. Görsel base64 gövdede taşınır    → `decode_image` tavanı ve türü denetler.
 4. `alt` metni zorunlu              → `slot_error` boş `alt` ile yazmayı reddeder.
 5. Yayın penceresi YEREL güne göre  → `today_iso` UTC'ye kaymaz.
 6. Slot silinmez, yayından kalkar   → durum yalnız `status` 0/1.
 7. BBD ucu yazılıyor, alan adları
    kesinleşmedi                     → `pick` aynı bilgiyi birkaç adda arar.
 8. Sabit çerçeveli önizleme
    kırpmayı GİZLER                  → `preview_box` + `crop_plan` gerçek oranı
                                       ve hangi kenarın gittiğini söyler.
 9. Yükleme adı latin-1 başlıkta
    patlar, uzantı yalan söyler      → `safe_filename` adı ASCII'ye indirir,
                                       uzantıyı gerçek MIME'dan yazar.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Ana sayfanın dört şeridi. Sıra, ekrandaki sekme sırasıdır ve önizlemedeki
#: yukarıdan aşağı dizilim de budur (duyuru en üstte çizilir).
AREAS = ("slider", "banner", "collection", "announcement")

#: ŞERİT ADLARI İŞ DİLİNDEDİR — "Slider" / "Banner" DEĞİL.
#:
#: NEDEN. Bu ekranı kullanan kişi yazılımcı değil; "slider" ve "banner" onun
#: sözlüğünde yok ve ekranda gördüğü kelimeyi vitrinde göreceği şeye
#: bağlayamıyor. Ad, kutunun MÜŞTERİDE NEREYE DÜŞTÜĞÜNÜ söyler; teknik
#: karşılıkları (`slider`, `banner`) anahtar olarak kodda kalır ve mağazaya
#: giden gövde değişmez.
AREA_LABELS = {
    "slider": "Ana ekran kayan görseller",
    "banner": "Tanıtım görselleri",
    "collection": "Öne çıkan ürün grupları",
    "announcement": "Üst duyuru yazısı",
}

#: Her şeridin TEKİL adı. Ekran "3 slot" değil "3 kayan görsel" der; sayının
#: yanındaki kelime neyin sayıldığını söylemezse sayı da bir şey söylemez.
AREA_ONE = {
    "slider": "kayan görsel",
    "banner": "tanıtım görseli",
    "collection": "ürün grubu",
    "announcement": "duyuru",
}

#: "Bu sekme ne işe yarar" — ekranda sekmenin altında tek cümle olarak durur.
AREA_WHAT = {
    "slider": "Ana sayfanın en üstünde sırayla dönen büyük görseller. "
              "Müşterinin siteye girer girmez gördüğü yer burasıdır.",
    "banner": "Sayfanın ortasında ve altında duran sabit kampanya görselleri.",
    "collection": "“Yeni gelenler”, “Çok satanlar” gibi ana sayfadaki ürün şeritleri.",
    "announcement": "Sayfanın en üstündeki ince şeritte duran yazı — kargo, kampanya "
                    "ya da tatil duyurusu. Görsel değil, metindir.",
}

PLACEMENTS = ("slider", "top", "middle", "bottom", "side")
PLACEMENT_LABELS = {
    "slider": "En üstteki kayan bölüm",
    "top": "Sayfanın üstü",
    "middle": "Sayfanın ortası",
    "bottom": "Sayfanın altı",
    "side": "Yan sütun",
}

DEVICES = ("all", "desktop", "mobile")
DEVICE_LABELS = {"all": "Her ekranda", "desktop": "Yalnız bilgisayarda",
                 "mobile": "Yalnız telefonda"}

LINK_KINDS = ("url", "product", "category", "cms")
#: "Tıklayınca nereye gitsin" sorusunun şıkları. "Serbest bağlantı" ve
#: "CMS sayfası" kullanıcının bilmediği iki kelimeydi; ikisi de ne olduğunu
#: söyleyen karşılıklarıyla değişti.
LINK_KIND_LABELS = {
    "url": "Adresi ben yazacağım",
    "product": "Bir ürün sayfası",
    "category": "Bir kategori sayfası",
    "cms": "Site içi bilgi sayfası (Hakkımızda, İletişim…)",
}

STATE_PUBLISHED = "published"
STATE_SCHEDULED = "scheduled"
STATE_EXPIRED = "expired"
STATE_DRAFT = "draft"

STATE_LABELS = {
    STATE_PUBLISHED: "Yayında",
    STATE_SCHEDULED: "Tarihi gelmedi",
    STATE_EXPIRED: "Süresi doldu",
    STATE_DRAFT: "Hazırlıkta",
}

#: Rozetin YANINDA duran tek cümle: "Yayında" ne demek, "Hazırlıkta" ne demek.
#: Rozet tek başına durum adını söyler ama MÜŞTERİNİN GÖRÜP GÖRMEDİĞİNİ
#: söylemez; ekranda asıl merak edilen sorudur ve yazıyla cevaplanır.
STATE_WHAT = {
    STATE_PUBLISHED: "Müşteri şu anda görüyor.",
    STATE_SCHEDULED: "Başlangıç tarihi gelmedi; o gün kendiliğinden yayına girer.",
    STATE_EXPIRED: "Bitiş tarihi geçti; müşteri artık görmüyor.",
    STATE_DRAFT: "Müşteri görmüyor. Göstermek için “Yayına al” demeniz gerekir.",
}

#: Görsel ölçüsü kararları.
SIZE_OK = "ok"
SIZE_BLURRY = "blurry"
SIZE_RATIO = "ratio"
SIZE_UNKNOWN = "unknown"
SIZE_NONE = "none"

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

#: Ekranın camelCase alan adları → mağaza gövdesindeki snake_case adlar.
#: Listede OLMAYAN hiçbir alan gövdeye konmaz: tanımadığımız bir alanı geri
#: göndermek, mağaza tarafında ne olduğunu bilmediğimiz bir değeri ezmektir.
#:
#: `status` BİLEREK YOK. Yayın durumu ayrı izin ister
#: (`store_home_media.publish`); düzenleme yamasıyla taşınsaydı yalnız
#: `manage` izni olan biri slotu yayına alabilir ve ayrı tutulan izni arka
#: kapıdan atlatırdı (K9).
FIELD_ALIASES = {
    "title": "title",
    "altText": "alt",
    "link": "link",
    "linkKind": "link_kind",
    "placement": "placement",
    "device": "device",
    "channel": "channel",
    "locale": "locale",
    "startsAt": "starts_at",
    "endsAt": "ends_at",
    "sortOrder": "sort_order",
    "area": "area",
}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: yayın penceresi mağazanın takvim
    gününe göre açılıp kapanır ve gece yarısından sonraki üç saatte UTC bir gün
    geride kalır — banner ekranda bitmiş, vitrinde sürüyor görünürdü.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


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


def as_bool(value: Any, default: bool = False) -> bool:
    """`0`/`"0"`/`False`/`"false"` hepsi kapalıdır; `None` varsayılana düşer."""
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return text(value).lower() not in ("0", "false", "no", "off", "hayır")


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama anahtarı — `Şubat` ile `subat` eşleşsin."""
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return folded.lower()


def day_of(value: Any) -> str:
    """Tarihi `YYYY-MM-DD` gününe indirger. Çözülemezse boş döner.

    Mağaza aynı alanı bazen `2026-08-13`, bazen `2026-08-13 09:00:00`, bazen
    ISO zaman damgası olarak veriyor. Karşılaştırma GÜN üzerinden yapılır:
    saat taşımak, "bugün başlayan" bir banner'ı öğlene kadar kapalı gösterirdi.
    """
    raw = text(value)
    if not raw:
        return ""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if not match:
        return ""
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def pick(raw: Any, *names: str) -> Any:
    """Aynı bilgiyi birkaç olası alan adında arar (TUZAK 7).

    `/api/admin/bbd/*` uçları hâlâ yazılıyor; alan adı `title` mı `heading` mi
    kesinleşmedi. Tek ada bağlanmak, uç yayınlandığı gün ekranı boş gösterirdi.
    Dolu değer önceliklidir; hiç dolu yoksa var olan boş değer döner.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if raw.get(name) not in (None, ""):
            return raw[name]
    for name in names:
        if name in raw:
            return raw[name]
    return None


# ================================================================ alan/durum

def area_of(raw: Any) -> str:
    """Slotun hangi şeride ait olduğu. Tanınmayan değer `banner` sayılır.

    Bilinmeyen alanı DÜŞÜRMEK yanlış olurdu: vitrinde duran bir slot ekranda
    hiç görünmez ve kimse onu kaldıramazdı.
    """
    value = fold(pick(raw, "area", "section", "group", "type"))
    if value in AREAS:
        return value
    if value in ("hero", "carousel", "main"):
        return "slider"
    if value in ("notice", "strip", "bar", "ticker"):
        return "announcement"
    if value in ("collections", "featured", "category"):
        return "collection"
    return "banner"


def slot_state(raw: dict[str, Any], *, today: str) -> str:
    """Yayın durumu: taslak · zamanlanmış · yayında · süresi doldu.

    Sıra önemlidir. Pasif bir slotun tarih penceresi açık olabilir; önce
    `status` bakılır, çünkü kullanıcı için "yayında değil" bilgisi tarihten
    daha kesindir.
    """
    if not as_bool(pick(raw, "status", "active", "is_active"), default=True):
        return STATE_DRAFT
    start = day_of(pick(raw, "starts_at", "start_at", "from", "published_from"))
    end = day_of(pick(raw, "ends_at", "end_at", "to", "published_to"))
    if start and start > today:
        return STATE_SCHEDULED
    if end and end < today:
        return STATE_EXPIRED
    return STATE_PUBLISHED


# ================================================================ ölçü kararı

def parse_size(value: Any) -> tuple[int, int]:
    """`"1920x640"` → `(1920, 640)`. Çözülemezse `(0, 0)`."""
    match = re.fullmatch(r"\s*(\d{1,5})\s*[xX×]\s*(\d{1,5})\s*", text(value))
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def size_verdict(width: Any, height: Any, wanted: tuple[int, int], *,
                 sharp_ratio: float = 1.0, tolerance: int = 8) -> dict[str, Any]:
    """Yüklenen görsel bu alan için yeterli mi — ekranın ZORUNLU alt metni.

    Dört karar üretir:
      · `none`    — bu alan görsel istemiyor (duyuru şeridi metindir).
      · `unknown` — ölçü okunamadı; sessiz kalmak yerine "bilinmiyor" denir.
      · `blurry`  — çözünürlük düşük: "Önerilen 1920x640; yüklenen 1200x400 —
                    mobilde bulanık."
      · `ratio`   — oran farklı: görsel kenarlardan kırpılacak.

    Karar RENKLE DEĞİL METİNLE taşınır (kit kuralı 7): `note` her zaman
    doludur ve ekranda görselin altında durur.
    """
    want_w, want_h = wanted
    got_w, got_h = as_int(width), as_int(height)

    if want_w <= 0 or want_h <= 0:
        return {"state": SIZE_NONE, "note": "Bu bölüm görsel istemez; yazı yeterli.",
                "recommended": "", "uploaded": f"{got_w}x{got_h}" if got_w else ""}

    recommended = f"{want_w}x{want_h}"
    if got_w <= 0 or got_h <= 0:
        return {"state": SIZE_UNKNOWN,
                "note": f"Görselin en-boy ölçüsü okunamadı; bu bölüm için uygun ölçü "
                        f"{recommended} piksel.",
                "recommended": recommended, "uploaded": ""}

    uploaded = f"{got_w}x{got_h}"
    head = f"Bu bölüm {recommended} piksel ister; seçtiğiniz görsel {uploaded}"

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

    if want_w <= 0 or want_h <= 0:
        return {"known": True, "ok": True, "axis": "", "percent": 0,
                "note": "Bu bölüm görsel istemez; kesilme de olmaz."}
    if got_w <= 0 or got_h <= 0:
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

def slot_row(raw: Any, *, today: str, wanted: tuple[int, int] = (0, 0),
             sharp_ratio: float = 1.0, tolerance: int = 8,
             read_only: bool = False) -> dict[str, Any]:
    """Mağaza kaydını ekranın anladığı düz satıra çevirir."""
    record = raw if isinstance(raw, dict) else {}
    area = area_of(record)
    state = slot_state(record, today=today)

    width = as_int(pick(record, "image_width", "width"))
    height = as_int(pick(record, "image_height", "height"))
    image_url = text(pick(record, "image_url", "image", "url", "path"))
    verdict = (size_verdict(width, height, wanted, sharp_ratio=sharp_ratio, tolerance=tolerance)
               if image_url or width else
               {"state": SIZE_NONE if wanted == (0, 0) else SIZE_UNKNOWN,
                "note": "Henüz görsel seçilmemiş." if wanted != (0, 0)
                        else "Bu bölüm görsel istemez; yazı yeterli.",
                "recommended": f"{wanted[0]}x{wanted[1]}" if wanted != (0, 0) else "",
                "uploaded": ""})

    placement = fold(pick(record, "placement", "position", "slot"))
    if placement not in PLACEMENTS:
        placement = "slider" if area == "slider" else "top"
    device = fold(pick(record, "device", "target_device"))
    if device not in DEVICES:
        device = "all"
    link_kind = fold(pick(record, "link_kind", "target_type"))
    if link_kind not in LINK_KINDS:
        link_kind = "url"

    row = {
        "id": as_int(pick(record, "id", "slot_id")),
        "area": area,
        "areaLabel": AREA_LABELS[area],
        # Tekil ad ekranda sayının yanında durur ("2. kayan görsel"); listedeki
        # satırın NE olduğunu şerit adını okumadan da söyler.
        "areaOne": AREA_ONE[area],
        "title": text(pick(record, "title", "heading", "name", "label")),
        "altText": text(pick(record, "alt", "alt_text", "image_alt")),
        "imageUrl": image_url,
        "imageWidth": width,
        "imageHeight": height,
        "link": text(pick(record, "link", "target", "url_target", "redirect")),
        "linkKind": link_kind,
        "linkKindLabel": LINK_KIND_LABELS[link_kind],
        "placement": placement,
        "placementLabel": PLACEMENT_LABELS[placement],
        "device": device,
        "deviceLabel": DEVICE_LABELS[device],
        "channel": text(pick(record, "channel", "channel_code")),
        "locale": text(pick(record, "locale", "locale_code")),
        "sortOrder": as_int(pick(record, "sort_order", "position", "order")),
        "startsAt": day_of(pick(record, "starts_at", "start_at", "from", "published_from")),
        "endsAt": day_of(pick(record, "ends_at", "end_at", "to", "published_to")),
        "status": as_bool(pick(record, "status", "active", "is_active"), default=True),
        "state": state,
        "stateLabel": STATE_LABELS[state],
        "stateWhat": STATE_WHAT[state],
        "clicks": as_int(pick(record, "clicks", "click_count", "hits")),
        # "0 tıklama" ile "tıklama ölçülmüyor" AYNI ŞEY DEĞİL. Uç bu alanı
        # taşımıyorsa ekran sıfır yazıp ölçüm yapılıyormuş gibi davranmaz.
        "clicksKnown": pick(record, "clicks", "click_count", "hits") is not None,
        "updatedAt": text(pick(record, "updated_at", "modified_at")),
        "sizeState": verdict["state"],
        "sizeNote": verdict["note"],
        "recommended": verdict["recommended"],
        "readOnly": bool(read_only),
    }
    row["issues"] = issues_of(row)
    return row


def issues_of(row: dict[str, Any]) -> list[str]:
    """Satırın gözle görülür eksikleri — listede ve raporda aynı metinle çıkar.

    METİNLER İŞ DİLİNDE. Eskiden "alt metni yok", "düşük çözünürlük", "oran
    farklı" yazıyordu: üçü de doğru ama üçü de kullanıcıya NE YAPACAĞINI
    söylemiyordu. Eksik, artık sonucuyla anlatılır — "bulanık çıkar",
    "kenarları kesilir" — çünkü kullanıcının kararı sonuca göre değişir.
    """
    found: list[str] = []
    if not row["altText"]:
        found.append("görsel açıklaması yok")
    if row["area"] != "announcement" and not row["imageUrl"]:
        found.append("görsel yok")
    if not row["link"]:
        found.append("tıklayınca gideceği yer yok")
    if row["sizeState"] == SIZE_BLURRY:
        found.append("görsel küçük, bulanık çıkar")
    elif row["sizeState"] == SIZE_RATIO:
        found.append("ölçü tutmuyor, kenarları kesilir")
    if row["state"] == STATE_EXPIRED:
        found.append("süresi dolmuş")
    return found


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Vitrindeki dizilim: `sortOrder`, eşitse kimlik. Ekran ne görüyorsa
    vitrinde de o sırayla duruyor demektir."""
    return sorted(rows, key=lambda row: (as_int(row.get("sortOrder")), as_int(row.get("id"))))


def filter_rows(rows: list[dict[str, Any]], *, q: str = "", area: str = "", status: str = "",
                device: str = "", channel: str = "", placement: str = "",
                start: str = "", end: str = "") -> list[dict[str, Any]]:
    """Süzme. Liste küçük (bir ana sayfada onlarca slot olur) ve TEK istekte
    gelir; sunucu tarafı sayfalama burada kazanç değil gecikme olurdu."""
    needle = fold(q)
    out = []
    for row in rows:
        if area and row["area"] != area:
            continue
        if status and row["state"] != status:
            continue
        if device and row["device"] not in (device, "all"):
            continue
        if channel and row["channel"] and row["channel"] != channel:
            continue
        if placement and row["placement"] != placement:
            continue
        if needle and needle not in fold(f"{row['title']} {row['link']} {row['altText']}"):
            continue
        # Yayın aralığı ÇAKIŞMA sorgusudur: "1–15 Eylül'de ne yayında" sorusunun
        # cevabı, o aralığa değen her slottur; tamamen içinde kalanlar değil.
        if start and row["endsAt"] and row["endsAt"] < start:
            continue
        if end and row["startsAt"] and row["startsAt"] > end:
            continue
        out.append(row)
    return out


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI şeridi: her durumdan kaç tane, kaç slotun alt metni eksik."""
    counts = {state: 0 for state in STATE_LABELS}
    for row in rows:
        counts[row["state"]] = counts.get(row["state"], 0) + 1
    return {
        "total": len(rows),
        **counts,
        "missingAlt": sum(1 for row in rows if not row["altText"]),
        "lowRes": sum(1 for row in rows if row["sizeState"] in (SIZE_BLURRY, SIZE_RATIO)),
        "clicks": sum(as_int(row.get("clicks")) for row in rows),
        "clicksKnown": any(row.get("clicksKnown") for row in rows),
    }


# ===================================================================== yazma

def normalize_patch(patch: Any) -> dict[str, Any]:
    """Ekranın yamasını mağaza adlarına çevirir. Tanınmayan anahtar DÜŞER."""
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        target = FIELD_ALIASES.get(key)
        if not target:
            continue
        if target in ("starts_at", "ends_at"):
            out[target] = day_of(value)
        elif target == "sort_order":
            out[target] = as_int(value)
        else:
            out[target] = text(value)
    return out


def write_body(current: dict[str, Any] | None, patch: dict[str, Any], *,
               channel: str, locale: str) -> dict[str, Any]:
    """OKU-DEĞİŞTİR-YAZ gövdesi (TUZAK 1'in kardeşi).

    Kısmi gövde göndermek, mağaza tarafında gönderilmeyen alanları boşaltma
    riski taşıyor. Bu yüzden slotun bilinen tüm alanları güncel değerleriyle
    geri konur, üstüne yalnız değişenler yazılır. `channel`/`locale` HER
    ZAMAN gider: kanal başına ayrı ana sayfa var ve eksik kanal bilgisi slotu
    yanlış vitrine düşürür.
    """
    body: dict[str, Any] = {}
    if current:
        for key, source in (("area", "area"), ("title", "title"), ("alt", "altText"),
                            ("link", "link"), ("link_kind", "linkKind"),
                            ("placement", "placement"), ("device", "device"),
                            ("starts_at", "startsAt"), ("ends_at", "endsAt")):
            body[key] = text(current.get(source))
        body["sort_order"] = as_int(current.get("sortOrder"))
        body["status"] = 1 if current.get("status") else 0
    body.update(patch or {})
    body["channel"] = channel
    body["locale"] = locale
    return body


def slot_error(body: dict[str, Any], *, area: str, has_image: bool) -> str:
    """Yazma öncesi iş kuralı denetimi — panelde de var ama asıl kapı burası (K9).

    `alt` metni ZORUNLUDUR (TUZAK 4): görme engelli kullanıcı için tek bilgi
    odur ve arama motoru banner'ı ondan okur. "Sonra yazarım" diye boş
    geçilen alt metni hiçbir zaman yazılmıyor; kural yazma anında uygulanır.
    """
    if area not in AREAS:
        return f"Tanınmayan bölüm: {area}"
    title = text(body.get("title"))
    if not title:
        return ("Başlık boş kalamaz. Müşteri bu adı görmez; siz listede bunu görürsünüz — "
                "“Eylül kırtasiye kampanyası” gibi kendinizin tanıyacağı bir ad yazın.")
    if area != "announcement" and not text(body.get("alt")):
        return ("Görsel açıklaması boş kalamaz. Görsel açılmadığında müşterinin okuduğu, "
                "Google’ın da gördüğü tek yazı budur. Dosya adı değil, görselde ne "
                "olduğunu yazın: “Sırt çantası kampanyası, %30 indirim” gibi.")
    if area == "announcement" and has_image:
        return ("Üstteki duyuru yazısı yalnız metin gösterir; oraya görsel konmaz. Görsel "
                "asmak için “Tanıtım görselleri” sekmesini kullanın.")
    kind = text(body.get("link_kind")) or "url"
    link = text(body.get("link"))
    if kind == "url" and link and not re.match(r"^(https?://|/)", link):
        return ("Adres ya `https://` ile (başka bir siteye gider) ya da `/` ile "
                "(kendi sitemizde kalır) başlamalı. Örnek: `/kampanya` ya da "
                "`https://bbdstore.com.tr/kampanya`.")
    start = day_of(body.get("starts_at"))
    end = day_of(body.get("ends_at"))
    if start and end and start > end:
        return "Başlangıç tarihi bitiş tarihinden sonra olamaz; iki tarihi kontrol edin."
    return ""


def merged_order(rows: list[dict[str, Any]], area: str, wanted: list[int]) -> dict[str, Any]:
    """Alanın yeni sırasını GLOBAL sıra listesine oturtur (TUZAK 1).

    Mağazanın `carousel/reorder` ucu TÜM slotların sırasını tek liste olarak
    ister. Yalnız açık sekmenin sırasını göndermek, listede olmayan slotları
    sıranın dışında bırakır ve diğer üç şeridi karıştırırdı.

    Ayrıca `wanted` gerçekten o alanın bir permütasyonu mu diye bakılır:
    eksik/fazla kimlikle gelen bir istek, sessizce yarım bir sıra yazardı.
    """
    if area not in AREAS:
        return {"ok": False, "error": f"Tanınmayan bölüm: {area}", "order": []}

    current = [as_int(row["id"]) for row in sort_rows(rows) if row["area"] == area]
    asked = [as_int(item) for item in (wanted or [])]
    if sorted(asked) != sorted(current):
        return {"ok": False,
                "error": "Ekrandaki liste ile mağazadaki liste tutmuyor — bu arada başka biri "
                         "ekleme ya da çıkarma yapmış olabilir. “Yenile” deyip sırayı "
                         "yeniden düzenleyin.",
                "order": []}
    if asked == current:
        return {"ok": False, "error": "Sıra zaten aynı; kaydedilecek bir değişiklik yok.",
                "order": []}

    queue = list(asked)
    order: list[int] = []
    for row in sort_rows(rows):
        slot_id = as_int(row["id"])
        # Alanın slotları KENDİ yerlerini korur; yalnız aralarındaki dizilim
        # değişir. Böylece diğer şeritlerin global konumu sabit kalır.
        order.append(queue.pop(0) if row["area"] == area else slot_id)
    return {"ok": True, "error": "", "order": order}


#: Bagisto tema özelleştirme tipleri → bizim şerit adlarımız. BBD ucu yayına
#: girene kadar ekran ana sayfayı bu kayıtlardan SALT OKUNUR gösterir.
THEME_AREAS = {
    "image_carousel": "slider",
    "static_content": "announcement",
    "category_carousel": "collection",
    "product_carousel": "collection",
}


def theme_slot(raw: Any) -> dict[str, Any]:
    """Tema özelleştirme kaydını slot biçimine yaklaştırır — SALT OKUNUR.

    NEDEN VAR: `/api/admin/bbd/carousel` hâlâ yazılıyor. O uç yokken ekranın
    bomboş açılması, "ana sayfada hiçbir şey yok" gibi okunur ve yanlıştır.
    Tema özelleştirmeleri Bagisto'nun kendi ucundan gelir; ana sayfada NE
    olduğunu gösterir ama slot düzenleyicisinin beklediği alanları (alt metni,
    yayın aralığı, cihaz) taşımaz. Bu yüzden yalnız GÖSTERİLİR; yazma yolu
    kapalıdır ve ekran nedenini söyler.
    """
    record = raw if isinstance(raw, dict) else {}
    kind = fold(pick(record, "type", "kind"))
    return {
        "id": as_int(pick(record, "id")),
        "area": THEME_AREAS.get(kind, "banner"),
        "title": text(pick(record, "name", "title")) or f"Adsız bölüm #{as_int(record.get('id'))}",
        "sort_order": as_int(pick(record, "sort_order", "position")),
        "status": pick(record, "status", "active"),
        "channel": text(pick(record, "channel", "channel_code")),
        "locale": text(pick(record, "locale", "locale_code")),
        "updated_at": text(pick(record, "updated_at")),
    }


def move(order: list[int], index: int, step: int) -> list[int]:
    """Klavye (`Ctrl+↑/↓`) ve sürükle-bırak aynı işi yapar; kural tek yerde."""
    target = index + step
    if index < 0 or index >= len(order) or target < 0 or target >= len(order):
        return list(order)
    out = list(order)
    out[index], out[target] = out[target], out[index]
    return out
