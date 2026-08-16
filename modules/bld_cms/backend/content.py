"""Site içeriğinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın işi metin işlemek: HTML temizlemek, şekilsiz
JSON'un boyutunu ve biçimini denetlemek, slug kalıbını doğrulamak, dizi
alanlarının sınırlarını saymak, sunucudan gelen satırı ekranın okuyacağı hâle
getirmek. Hepsi girdi→çıktı fonksiyonu; servise gömülseydi tek satırı bile
ağsız test edilemezdi.

ALTI TUZAK — hepsinin karşılığı burada bir fonksiyondur:

 1. Kaydedilen HTML'e güvenilmez      → `sanitize_html` BEYAZ LİSTE uygular (K9).
 2. Bilinmeyen etiket silinirse metin  → tanınmayan etiket AÇILIR, içeriği durur.
    kaybolur
 3. `value` şemasız ama sınırsız değil → `content_value_error` 256 KB'ı ve
                                         anahtarın nesne mi dizi mi olduğunu sayar.
 4. `slug` değişirse eski bağlantı     → `slug_change_notice` uyarıyı üretir.
    kırılır
 5. Dizi alanı sessizce kırpılır       → `string_list_error` 20 eleman / 300
                                         karakter sınırını ÖNCE söyler.
 6. Yeniden çizdirme sessizce düşer    → `revalidate_view` "istendi ama olmadı"
                                         ile "hiç istenmedi"yi AYIRIR.

ÜÇ KAPILI BEYAZ LİSTE. Aynı liste üç yerde yaşıyor:

    apps/desktop/shell/ui-kit/richtext.js   → yazarken ve çizerken (arayüz)
    modules/bld_cms/backend/content.py      → göndermeden önce (bu dosya)
    BLD · Models\\SiteService/SitePost mutator (`HtmlSanitizer`) → kaydederken

Asıl kapı sonuncusudur; ilk ikisi kolaylık ve erken geri bildirimdir. Biri
genişletilip öteki unutulursa kullanıcı ekranda gördüğü biçimi kaydettiğinde
SESSİZCE kaybeder. İlk ikisinin eşitliği `tests/test_bld_cms_content.py`
içinde teste bağlıdır; üçüncüsü sunucudadır ve oraya bir test uzatılamaz —
bu yüzden liste genişletilirken sunucudaki ayna da elle değiştirilmelidir.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

#: Gerekçenin sınırları — `00-genel.md` §3. Geçit de doğruluyor; burada tekrar
#: denetlenir çünkü arayüzde zorunlu göstermek yetkilendirme değildir (K9).
MIN_REASON = 10
MAX_REASON = 500

#: `PUT /content/{key}` gövde sınırı (serileştirilmiş) — cms.md. Aşarsa sunucu
#: 422 verir; burada yakalamak, kullanıcının 200 KB'lık yapıştırmasını hız
#: kovasından pay yemeden geri çevirir.
MAX_CONTENT_BYTES = 256 * 1024

#: Dizi alanlarının sınırları — cms.md ("her eleman en çok 300 karakter, liste
#: en çok 20 eleman").
MAX_LIST_ITEMS = 20
MAX_LIST_ITEM_CHARS = 300

#: `POST /revalidate` yol listesi sınırı — cms.md ("En çok 20 yol; her biri
#: `/` ile başlamalı").
MAX_REVALIDATE_PATHS = 20

#: Metin alanlarının kolon uzunlukları (cms.md tabloları). Sunucu da kesiyor;
#: burada bilmek, 401 karakterlik özeti göndermeden önce söylemeyi sağlar.
SERVICE_LIMITS = {"slug": 96, "title": 160, "summary": 400, "icon": 48,
                  "menu_planning": 20_000, "intro": 20_000}
POST_LIMITS = {"slug": 96, "title": 200, "description": 400, "category": 64}

#: `SiteContent::KEYS` — SABİT. Listede olmayan bir anahtara `PUT` → 404.
#: Sıra cms.md'deki tablonun sırasıdır; ekran sekmeleri de bu sırada durur ve
#: yöneticiyle sözleşmeyi okuyan kişi aynı yere bakabilir.
CONTENT_KEYS: tuple[str, ...] = (
    "brand", "contact", "company", "faq", "sectors", "menus", "quality",
)

#: Anahtar künyesi: ekrandaki ad, `value` şekli ve BOŞ anahtar için önerilen
#: alanlar.
#:
#: `shape` SUNUCU KURALI DEĞİL. cms.md açıkça diyor ki sunucu içeriği
#: doğrulamaz, yalnız geçerli JSON olduğunu ve boyutunu denetler. Buradaki
#: şekil bilgisi EKRANIN çizim kararıdır: nesne için alan formu, dizi için
#: satır listesi. Yanlış şekilde bir değer gelirse ekran onu ham JSON kipinde
#: gösterir ve reddetmez — sözleşmenin izin verdiği bir şeyi arayüzün
#: yasaklaması, veriyi düzeltilemez hâle getirirdi.
#:
#: `seed` alanları cms.md'nin ÖRNEK gövdesinden birebir alınmıştır ve YALNIZ
#: anahtar tümüyle boşken önerilir. Uydurulmuş bir alan adı, siteyi okumayan
#: bir alan doldurmak olurdu; örnekte geçmeyen anahtarlar (`company`,
#: `sectors`, `menus`, `quality`) bu yüzden tohumsuzdur ve ekran şekli
#: kullanıcıya sorar.
CONTENT_SPEC: dict[str, dict[str, Any]] = {
    "brand": {"label": "Marka", "shape": "object",
              "hint": "Marka adı, slogan, logo metni.",
              "seed": ["name", "tagline"]},
    "contact": {"label": "İletişim", "shape": "object",
                "hint": "Telefon, e-posta, adres, çalışma saatleri, sosyal hesaplar.",
                "seed": ["phone", "email", "address", "working_hours"]},
    "company": {"label": "Kurumsal", "shape": "object",
                "hint": "Hakkımızda ve misyon metinleri.",
                "seed": []},
    "faq": {"label": "Sık sorulan sorular", "shape": "array",
            "hint": "Soru-cevap çiftleri.",
            "seed": ["q", "a"]},
    "sectors": {"label": "Sektörler", "shape": "array",
                "hint": "Hizmet verilen sektörler.",
                "seed": []},
    "menus": {"label": "Menü çözümleri", "shape": "array",
              "hint": "Site menüsünde tanıtılan çözümler.",
              "seed": []},
    "quality": {"label": "Kalite zinciri", "shape": "array",
                "hint": "Kalite zincirinin adımları.",
                "seed": []},
}

#: Hizmet kaydının dizi alanları — düz string listesi (cms.md).
SERVICE_LIST_FIELDS = ("audience", "how_it_works", "benefits", "quote_needs")

#: `published` süzgecinin kabul ettiği değerler (cms.md).
PUBLISHED_VALUES = ("true", "false", "all")

#: `slug` kalıbı — cms.md: `^[a-z0-9]+(-[a-z0-9]+)*$`, 2–96 karakter.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

#: Yerel düzenleme geçmişinin hedef türleri. Sunucudaki `target_type`
#: adlarıyla AYNI tutulur (cms.md · Denetim eylemleri) ki iki iz yan yana
#: okunabilsin.
TARGET_CONTENT = "site_content"
TARGET_SERVICE = "site_service"
TARGET_POST = "site_post"
TARGET_TYPES = (TARGET_CONTENT, TARGET_SERVICE, TARGET_POST)


# ================================================================ beyaz liste
#
# Aşağıdaki altı sabit `apps/desktop/shell/ui-kit/richtext.js` içindeki
# adaşlarıyla BİREBİR aynıdır ve eşitlik teste bağlıdır.

#: Çizilmesine izin verilen etiketler.
#: `tr/thead/tbody` listede: onlarsız tablo geçerli HTML olmuyor ve tarayıcı
#: hücreleri tablonun dışına atıyor.
ALLOWED_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "a", "img", "strong", "em",
    "u", "span", "br", "table", "thead", "tbody", "tr", "td", "th",
})

#: İçeriğiyle birlikte ATILAN etiketler. Diğer tanınmayan etiketler açılır
#: (içeriği korunur); bunların içeriği metin değil koddur, korunacak bir şey yok.
DROP_TAGS = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "form", "input", "button", "select", "textarea", "svg",
    "math", "noscript", "template", "link", "meta", "base",
})

VOID_TAGS = frozenset({"br", "img"})

#: Etiket başına izin verilen öznitelikler. `on*` HİÇBİR yerde yok — kod çalıştırır.
ALLOWED_ATTRS: dict[str, tuple[str, ...]] = {
    "a": ("href", "title"),
    "img": ("src", "alt", "title", "width", "height"),
    "td": ("colspan", "rowspan"),
    "th": ("colspan", "rowspan", "scope"),
}

#: `style` HER etikette kabul edilir ama HAM GEÇMEZ: `filter_style` onu üç
#: özelliğe indirger ve değerlerini de biçim denetiminden geçirir. Kaplama
#: kurmaya yetecek özelliklerin (`position`, `width`, `opacity`, `z-index`)
#: hiçbiri listede yok.
STYLE_PROPS = frozenset({"color", "background-color", "text-align"})

ALIGN_VALUES = frozenset({"left", "center", "right", "justify"})

#: Kabul edilen bağlantı şemaları. `data:` REDDEDİLİR: `data:text/html`
#: tarayıcıda sayfa açar ve beyaz listeyi anlamsız kılar. Satır içi görselin
#: "önce yükle, sonra adresi ekle" akışıyla çalışmasının sebebi de budur.
SAFE_SCHEMES = ("http", "https", "mailto", "tel")

_HEX_COLOR = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$")
_RGB_COLOR = re.compile(
    r"^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*[\d.]+\s*)?\)$"
)

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

_TAG_RE = re.compile(r"<[^>]+>")


# ===================================================================== temel

def text(value: Any) -> str:
    """`None` ile boş metni ayırmadan, kırpılmış metin döndürür."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return text(value).lower() in ("1", "true", "evet", "yes", "on")


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama biçimi.

    Türkçe harfler ÖNCE eşlenir: `str.lower()` tek başına `İ` harfini
    `i̇` (i + birleşen nokta) yapıyor ve "İade" araması "iade" ile eşleşmiyordu.
    """
    return text(value).translate(_TR_MAP).lower()


def slugify(value: str) -> str:
    """`Etkinlik Catering` → `etkinlik-catering`."""
    folded = text(value).translate(_TR_MAP)
    folded = re.sub(r"[^a-zA-Z0-9]+", "-", folded).strip("-").lower()
    return re.sub(r"-{2,}", "-", folded)


def now_iso() -> str:
    """Yerel denetim izinin damgası — saniye çözünürlüğünde, saat dilimli."""
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    clean = text(value)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına "
                "bu metin yazılır.")
    if len(clean) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir."
    return ""


def slug_error(value: str) -> str:
    """Adres parçası sözleşmedeki kalıba uyuyor mu (cms.md)."""
    clean = text(value)
    if len(clean) < 2 or len(clean) > 96:
        return "Adres parçası 2–96 karakter olmalı."
    if not SLUG_RE.match(clean):
        return ("Adres parçası yalnız küçük harf, rakam ve tek tire içerebilir "
                "(ör. `kurumsal-catering`).")
    return ""


def slug_change_notice(before: str, after: str) -> dict[str, str] | None:
    """`slug` değiştiyse ekranda gösterilecek uyarı.

    Sunucu da `warnings.slug_changed` döndürüyor ama o, yazma BİTTİKTEN sonra
    gelir. Bu uyarı ONAY KUTUSUNDA gösterilir: adresi değiştirdiğini kaydettikten
    sonra öğrenen yönetici, kırılan bağlantıları geri getiremez.
    """
    old = text(before)
    new = text(after)
    if not old or old == new:
        return None
    return {"code": "slug_changed", "from": old, "to": new,
            "note": "Eski adrese verilen bağlantılar kırılacak."}


# ================================================================ HTML kapısı

def safe_url(value: str, *, allow_relative: bool = True) -> str:
    """Bağlantıyı kabul eder ya da boşa düşürür.

    Boşluk ve satır sonu ATILIR: `java\\nscript:` yazımı bazı tarayıcılarda
    hâlâ çalışıyor ve şema denetimini atlatıyor.
    """
    raw = re.sub(r"[\s\x00-\x1f]+", "", text(value))
    if not raw:
        return ""
    if raw.startswith(("#", "/")):
        return raw if allow_relative else ""
    parts = urlsplit(raw)
    if parts.scheme:
        return raw if parts.scheme.lower() in SAFE_SCHEMES else ""
    # Şemasız ve `/` ile başlamayan değer görece yoldur (`hizmetler.html`).
    return raw if allow_relative else ""


def normalize_color(value: Any) -> str:
    """`rgb(17, 24, 39)` ve `#FFF` → `#111827` / `#ffffff`. Çözülemezse boş."""
    raw = text(value).lower()
    if not raw:
        return ""
    if _HEX_COLOR.match(raw):
        return f"#{raw[1] * 2}{raw[2] * 2}{raw[3] * 2}" if len(raw) == 4 else raw
    match = _RGB_COLOR.match(raw)
    if not match:
        return ""
    parts = (min(255, max(0, int(part))) for part in match.groups())
    return "#" + "".join(f"{part:02x}" for part in parts)


def filter_style(value: Any) -> str:
    """`style` değerini üç güvenli özelliğe indirger.

    Hiçbiri kalmazsa boş dizge döner ve öznitelik hiç yazılmaz. Tanınmayan
    özellik SESSİZCE atılır — hata vermek, tarayıcının eklediği `line-height`
    gibi zararsız artıklar yüzünden kaydetmeyi engellerdi.
    """
    out: list[str] = []
    for chunk in text(value).split(";"):
        prop, _, raw = chunk.partition(":")
        prop = prop.strip().lower()
        raw = raw.strip()
        if prop not in STYLE_PROPS:
            continue
        if prop == "text-align":
            if raw.lower() in ALIGN_VALUES:
                out.append(f"text-align:{raw.lower()}")
            continue
        color = normalize_color(raw)
        if color:
            out.append(f"{prop}:{color}")
    return ";".join(out)


class _Cleaner(HTMLParser):
    """Beyaz listeli HTML üretir. Tanınmayan etiket AÇILIR, içeriği korunur."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop = 0          # atılan etiketin içinde miyiz (script/style…)
        self._open: list[str] = []

    def result(self) -> str:
        # Kapatılmamış etiketler kapatılır: yarım kalan `<ul>` kendinden
        # sonraki bütün paragrafları içine alıyordu.
        for tag in reversed(self._open):
            self._out.append(f"</{tag}>")
        self._open.clear()
        return "".join(self._out)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._drop += 1
            return
        if self._drop or name not in ALLOWED_TAGS:
            return
        self._out.append(f"<{name}{self._attrs(name, attrs)}>")
        if name not in VOID_TAGS:
            self._open.append(name)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in DROP_TAGS or self._drop or name not in ALLOWED_TAGS:
            return
        self._out.append(f"<{name}{self._attrs(name, attrs)}>")
        if name not in VOID_TAGS:
            self._out.append(f"</{name}>")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._drop = max(0, self._drop - 1)
            return
        if self._drop or name not in ALLOWED_TAGS or name in VOID_TAGS:
            return
        if name not in self._open:
            return
        # İç içe geçmiş yanlış kapanışta aradaki etiketler de kapatılır.
        while self._open:
            current = self._open.pop()
            self._out.append(f"</{current}>")
            if current == name:
                break

    def handle_data(self, data: str) -> None:
        if self._drop:
            return
        self._out.append(escape(data, quote=False))

    @staticmethod
    def _attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = ALLOWED_ATTRS.get(tag, ())
        out: list[str] = []
        for key, value in attrs:
            name = (key or "").lower()
            # `style` her etikette kabul edilir ama süzülür; kalan hiçbir
            # özellik konum/boyut taşımadığı için kaplama kurulamaz.
            if name == "style":
                style = filter_style(value)
                if style:
                    out.append(f' style="{escape(style, quote=True)}"')
                continue
            if name not in allowed:
                continue
            raw = text(value)
            if name in ("href", "src"):
                raw = safe_url(raw)
                if not raw:
                    continue
            out.append(f' {name}="{escape(raw, quote=True)}"')
        if tag == "a" and any(item.startswith(' href="http') for item in out):
            # Dış bağlantı yeni sekmede açılır ve `rel` ile açtığı sekmeye
            # bizim pencereyi vermez (`window.opener` sızıntısı).
            out.append(' target="_blank" rel="noopener noreferrer"')
        return "".join(out)


def sanitize_html(value: Any) -> str:
    """Gönderilecek gövdeyi beyaz listeye indirger (TUZAK 1).

    Asıl kapı sunucudadır (`HtmlSanitizer`, model mutator'ında) ve o, içeriğin
    nereden geldiğine bakmaz. Buradaki kapı iki şey için var: gövdeyi
    göndermeden önce ne kaybedeceğini SÖYLEYEBİLMEK ve arayüz kapısını
    atlayan bir istemcinin ekranı beslemesini engellemek (K9).
    """
    raw = text(value)
    if not raw:
        return ""
    cleaner = _Cleaner()
    cleaner.feed(raw)
    cleaner.close()
    return cleaner.result().strip()


def html_to_text(value: Any) -> str:
    """Etiketleri atıp düz metin bırakır — sayaç ve arama bunu okur."""
    raw = sanitize_html(value)
    if not raw:
        return ""
    return re.sub(r"\s+", " ", _TAG_RE.sub(" ", raw)).strip()


def html_changed_note(before: Any, after: Any) -> str:
    """Temizlik gövdeyi değiştirdiyse kullanıcıya söylenecek cümle.

    Sessiz kalmak, yapıştırılan Word içeriğinin yarısının kaybolduğunu
    kullanıcıya siteyi açtığında öğretirdi.
    """
    if text(before) == text(after):
        return ""
    return ("Gövdedeki bazı etiketler izin listesinde olmadığı için temizlendi; "
            "kaydedilen hâl düzenleyicide gösterilen hâldir.")


# ============================================================ içerik anahtarı

def content_key_error(key: str) -> str:
    """Anahtar `SiteContent::KEYS` listesinde mi (cms.md — uydurulamaz)."""
    clean = text(key)
    if clean in CONTENT_KEYS:
        return ""
    return (f"'{clean}' bilinen bir içerik anahtarı değil. Anahtarlar sabittir: "
            + ", ".join(CONTENT_KEYS) + ".")


def json_size(value: Any) -> int:
    """Serileştirilmiş boyut (bayt). Sunucu da bunu ölçüyor."""
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return -1


def content_value_error(key: str, value: Any) -> str:
    """`value` gönderilebilir mi.

    ŞEKİL DENETİMİ YUMUŞAKTIR. cms.md sunucunun içeriği doğrulamadığını
    söylüyor; buradaki denetim yalnız üç şeye bakar: JSON'a çevrilebiliyor mu,
    256 KB'ı aşıyor mu ve anahtarın bilinen şekli ile UYUŞUYOR mu. Üçüncüsü
    reddetme sebebi değil, UYARI sebebidir — sözleşmenin izin verdiğini
    arayüzün yasaklaması, veriyi düzeltilemez hâle getirirdi. Bu yüzden
    yalnız ilk iki durumda hata metni döner.
    """
    size = json_size(value)
    if size < 0:
        return "Değer JSON'a çevrilemedi; ham JSON kipinde yazımı denetleyin."
    if size > MAX_CONTENT_BYTES:
        return (f"Değer {size // 1024} KB; sunucu sınırı "
                f"{MAX_CONTENT_BYTES // 1024} KB. Uzun metinleri kısaltın ya da "
                "görselleri adresle bağlayın.")
    return ""


def content_shape_warning(key: str, value: Any) -> str:
    """Değerin şekli anahtarın alışılmış şekliyle uyuşmuyorsa uyarı metni."""
    spec = CONTENT_SPEC.get(text(key))
    if not spec:
        return ""
    expected = spec["shape"]
    actual = "array" if isinstance(value, list) else (
        "object" if isinstance(value, dict) else "scalar")
    if actual == expected:
        return ""
    names = {"array": "liste", "object": "nesne", "scalar": "düz değer"}
    return (f"'{spec['label']}' anahtarı sitede {names[expected]} olarak "
            f"okunuyor; gönderilen değer {names[actual]}. Sunucu bunu kabul "
            "eder ama site alanı bulamayabilir.")


def content_view(payload: Any) -> dict[str, Any]:
    """`GET /content` yanıtını ekranın okuyacağı hâle getirir.

    KAYDI OLMAYAN ANAHTAR DA DÖNER (cms.md): boş değer ve `updated_at: null`
    ile. Sunucu bunu zaten yapıyor; burada bir kez daha tamamlanır çünkü
    eksik anahtarı atlamak, panelin "bu alan yok mu, yoksa boş mu" sorusunu
    kendi cevaplamasını gerektirirdi.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        data = payload if isinstance(payload, dict) else {}

    rows: list[dict[str, Any]] = []
    for key in CONTENT_KEYS:
        spec = CONTENT_SPEC[key]
        raw = data.get(key)
        value = raw.get("value") if isinstance(raw, dict) else raw
        updated = raw.get("updated_at") if isinstance(raw, dict) else None
        if value is None:
            value = [] if spec["shape"] == "array" else {}
        rows.append({
            "key": key,
            "label": spec["label"],
            "hint": spec["hint"],
            "shape": spec["shape"],
            "seed": list(spec["seed"]),
            "value": value,
            "updated_at": text(updated),
            "bytes": max(0, json_size(value)),
            "count": len(value) if isinstance(value, (list, dict)) else 0,
            "filled": bool(value),
        })
    return {"items": rows, "keys": list(CONTENT_KEYS)}


# ==================================================================== satırlar

def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text(item) for item in value if text(item)]


def string_list_error(label: str, value: Any) -> str:
    """Dizi alanının sınırları (cms.md: en çok 20 eleman, her biri 300 karakter)."""
    if value is None:
        return ""
    if not isinstance(value, list):
        return f"'{label}' bir liste olmalı."
    if len(value) > MAX_LIST_ITEMS:
        return f"'{label}' en çok {MAX_LIST_ITEMS} satır alabilir; {len(value)} satır var."
    for item in value:
        if len(text(item)) > MAX_LIST_ITEM_CHARS:
            return (f"'{label}' satırlarından biri {MAX_LIST_ITEM_CHARS} karakteri "
                    "aşıyor.")
    return ""


def service_row(raw: Any) -> dict[str, Any]:
    """`GET /services` satırı → ekran satırı. Alan adları sözleşmedendir."""
    row = raw if isinstance(raw, dict) else {}
    body = text(row.get("body_html"))
    return {
        "id": as_int(row.get("id")),
        "slug": text(row.get("slug")),
        "title": text(row.get("title")),
        "summary": text(row.get("summary")),
        "intro": text(row.get("intro")),
        "icon": text(row.get("icon")),
        "body_html": body,
        "body_text": html_to_text(body),
        "audience": _string_list(row.get("audience")),
        "how_it_works": _string_list(row.get("how_it_works")),
        "benefits": _string_list(row.get("benefits")),
        "menu_planning": text(row.get("menu_planning")),
        "quote_needs": _string_list(row.get("quote_needs")),
        "sort_order": as_int(row.get("sort_order")),
        "is_published": bool(row.get("is_published")),
        "created_at": text(row.get("created_at")),
        "updated_at": text(row.get("updated_at")),
    }


def post_row(raw: Any) -> dict[str, Any]:
    """`GET /posts` satırı → ekran satırı.

    `reading_minutes` ile `reading_minutes_effective` AYRI TUTULUR: ilki elle
    girilen değer (boş olabilir), ikincisi sunucunun gövdeden hesapladığı.
    Panel "hesaplandı" ipucunu ancak ikisini ayrı görerek gösterebilir; tek
    alana katlamak, yöneticiye kendi yazdığı sanılan bir sayı gösterirdi.
    """
    row = raw if isinstance(raw, dict) else {}
    body = text(row.get("body_html"))
    manual = row.get("reading_minutes")
    return {
        "id": as_int(row.get("id")),
        "slug": text(row.get("slug")),
        "title": text(row.get("title")),
        "description": text(row.get("description")),
        "category": text(row.get("category")),
        "body_html": body,
        "body_text": html_to_text(body),
        "published_at": text(row.get("published_at")),
        "reading_minutes": None if manual in (None, "") else as_int(manual),
        "reading_minutes_effective": as_int(row.get("reading_minutes_effective")),
        "reading_estimated": manual in (None, ""),
        "is_published": bool(row.get("is_published")),
        "created_at": text(row.get("created_at")),
        "updated_at": text(row.get("updated_at")),
    }


def published_filter(value: Any) -> str:
    """`published` süzgeci — tanınmayan değer `all`a düşer (cms.md varsayılanı)."""
    clean = text(value).lower()
    return clean if clean in PUBLISHED_VALUES else "all"


# =============================================================== yazma gövdesi

def service_fields_error(fields: dict[str, Any], *, creating: bool) -> str:
    """Hizmet gövdesinin ön denetimi. Boş metin = gövde gönderilebilir."""
    if creating:
        if not text(fields.get("title")):
            return "Başlık zorunlu."
        problem = slug_error(fields.get("slug", ""))
        if problem:
            return problem
    elif "slug" in fields:
        problem = slug_error(fields.get("slug", ""))
        if problem:
            return problem

    for key, limit in SERVICE_LIMITS.items():
        if key in ("slug",) or key not in fields:
            continue
        if len(text(fields.get(key))) > limit:
            return f"'{key}' alanı en çok {limit} karakter olabilir."

    labels = {"audience": "Kimler için", "how_it_works": "Nasıl işler",
              "benefits": "Ne kazandırır", "quote_needs": "Teklif için gerekenler"}
    for key in SERVICE_LIST_FIELDS:
        if key not in fields:
            continue
        problem = string_list_error(labels[key], fields.get(key))
        if problem:
            return problem
    return ""


def post_fields_error(fields: dict[str, Any], *, creating: bool) -> str:
    """Yazı gövdesinin ön denetimi.

    `body_html` YAZI İÇİN ZORUNLUDUR ve boş olamaz (kolon `NOT NULL`); boş
    gövdeli bir yazı, sitede başlığı olan boş bir sayfa üretirdi.
    """
    if creating:
        if not text(fields.get("title")):
            return "Başlık zorunlu."
        problem = slug_error(fields.get("slug", ""))
        if problem:
            return problem
        if not html_to_text(fields.get("body_html")):
            return ("Yazı gövdesi boş olamaz: başlığı olan boş bir sayfa "
                    "üretirdi.")
    else:
        if "slug" in fields:
            problem = slug_error(fields.get("slug", ""))
            if problem:
                return problem
        if "body_html" in fields and not html_to_text(fields.get("body_html")):
            return ("Yazı gövdesi boş olamaz: başlığı olan boş bir sayfa "
                    "üretirdi.")

    for key, limit in POST_LIMITS.items():
        if key == "slug" or key not in fields:
            continue
        if len(text(fields.get(key))) > limit:
            return f"'{key}' alanı en çok {limit} karakter olabilir."

    if "published_at" in fields:
        problem = date_error(fields.get("published_at"))
        if problem:
            return problem

    if fields.get("reading_minutes") not in (None, ""):
        minutes = as_int(fields.get("reading_minutes"), -1)
        if minutes < 1 or minutes > 240:
            return "Okuma süresi 1–240 dakika arasında olmalı ya da boş bırakılmalı."
    return ""


def date_error(value: Any) -> str:
    """`YYYY-MM-DD` — an değil TARİH (cms.md: yayın günü yazarın kararıdır)."""
    clean = text(value)
    if not clean:
        return ""
    try:
        datetime.strptime(clean, "%Y-%m-%d")  # noqa: DTZ007 — saat dilimi yok, bu bir tarih
    except ValueError:
        return "Yayın tarihi `YYYY-AA-GG` biçiminde olmalı (ör. 2026-08-01)."
    return ""


def revalidate_paths_error(paths: Any) -> str:
    """`POST /revalidate` yol listesi (cms.md: en çok 20, her biri `/` ile başlar)."""
    if paths in (None, ""):
        return ""
    if not isinstance(paths, list):
        return "Yol listesi bir dizi olmalı."
    if len(paths) > MAX_REVALIDATE_PATHS:
        return f"En çok {MAX_REVALIDATE_PATHS} yol verilebilir; {len(paths)} yol var."
    for item in paths:
        clean = text(item)
        if not clean.startswith("/"):
            return f"'{clean or 'boş'}' geçersiz: her yol `/` ile başlamalı."
    return ""


def clean_paths(paths: Any) -> list[str]:
    """Yol listesini kırpar ve boşları atar. Hiç kalmazsa boş liste = tümü."""
    if not isinstance(paths, list):
        return []
    return [text(item) for item in paths if text(item)]


# ============================================================== yeniden çizdirme

def revalidate_view(payload: Any, *, requested: bool) -> dict[str, Any]:
    """Yazma yanıtından "site tazelendi mi" sorusunun cevabını çıkarır.

    DÖRT HÂL BİRBİRİNE KARIŞTIRILMAZ:

        skipped  — hiç istenmedi (yönetici bilerek kapattı, toplu çizdirecek)
        ok       — istendi ve OLDUĞU SÖYLENDİ
        failed   — istendi, olmadı — ama kayıt YAZILDI
        unknown  — istendi, sunucu sonucu BİLDİRMEDİ

    Üçüncüsü bu ekranın var oluş sebebine dokunuyor: sunucu bilerek 200
    döndürüyor (cms.md), çünkü içerik gerçekten kaydedildi. Ekran bunu
    söylemezse yönetici "kaydettim ama sitede yok" der ve aynı kaydı ikinci
    kez yazar. Söylerse, elindeki tek doğru eylemi — yeniden çizdirmeyi —
    yapabilir.

    DÖRDÜNCÜSÜ NEDEN AYRI: sözleşme yazma yanıtında `revalidated` bayrağını
    vaat ediyor, ama bayrak hiç gelmediğinde "tazelendi" demek BİLMEDİĞİMİZ
    bir şeyi söylemek olurdu. Sessizce iyimser davranan bir ekran, bu modülün
    engellemek için var olduğu tam o cümleyi ("kaydettim ama sitede yok")
    kurdurur — bu yüzden bilinmeyen hâl kendi adıyla anılır ve yeniden
    çizdirme yolu açık bırakılır.
    """
    if not requested:
        return {"status": "skipped", "error": "",
                "note": "Site yeniden çizdirilmedi; değişiklik yayında görünmeyebilir."}

    body = payload if isinstance(payload, dict) else {}
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    warnings = body.get("warnings") if isinstance(body.get("warnings"), list) else []
    codes = {text(item.get("code")) for item in warnings if isinstance(item, dict)}

    status = text(data.get("status"))
    flag = body.get("revalidated")
    if "revalidate_failed" in codes or status == "failed" or flag is False:
        reason = text(data.get("error")) or "Sunucu ayrıntı vermedi."
        return {"status": "failed", "error": reason,
                "note": ("Kayıt YAZILDI ama site yeniden çizdirilemedi: " + reason
                         + " İçerik, önbellek kendiliğinden tazelenene kadar "
                           "eski hâliyle görünebilir.")}
    if flag is True or status == "ok":
        return {"status": "ok", "error": "", "note": "Site yeniden çizdirildi."}
    return {"status": "unknown", "error": "",
            "note": ("Kayıt YAZILDI ama sunucu yeniden çizdirmenin sonucunu "
                     "bildirmedi. Sitede eski hâli görüyorsanız yeniden "
                     "çizdirin.")}


def warnings_of(payload: Any) -> list[dict[str, Any]]:
    """Yanıttaki uyarıları OLDUĞU GİBİ taşır — ekran hepsini gösterir."""
    body = payload if isinstance(payload, dict) else {}
    items = body.get("warnings")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def audit_id_of(payload: Any) -> int:
    """Sunucunun denetim satırı kimliği; yoksa 0 — UYDURULMAZ."""
    body = payload if isinstance(payload, dict) else {}
    return as_int(body.get("audit_id"))
