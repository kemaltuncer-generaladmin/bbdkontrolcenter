"""CMS içeriğinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın işi metin işlemek: HTML temizlemek, SEO uzunluğu
saymak, içerik metninde aramak, SSS sayfasını soru-cevap çiftlerine ayırmak,
iç bağlantıların kırık olup olmadığına bakmak. Hepsi girdi→çıktı fonksiyonu;
servise gömülseydi tek satırı bile ağsız test edilemezdi.

YEDİ TUZAK — hepsinin karşılığı burada bir fonksiyondur:

 1. Sayfa metni çeviri tablosunda    → `field` düz kaydı ve çeviriyi birlikte okur.
 2. Kaydedilen HTML'e güvenilmez     → `sanitize_html` BEYAZ LİSTE uygular (K9).
 3. Bilinmeyen etiket silinirse metin → tanınmayan etiket AÇILIR, içeriği durur.
    kaybolur
 4. `url_key` değişirse eski bağlantı → `slug_change_notice` yönlendirme önerir.
    kırılır
 5. İçerik metninde arama sunucuda    → `page_matches` etiketleri atıp arar.
    yapılamaz
 6. Bagisto sayfada `status` taşımaz  → `page_row` "yok" der, 0 uydurmaz.
    olabilir
 7. Yönlendirme kendine dönebilir     → `redirect_error` döngüyü yazmadan yakalar.
"""

from __future__ import annotations

import re
from html import escape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

#: Gerekçenin en az uzunluğu. Geçit (store_api) de 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Yayına alınan içeriğin vitrinde görünmesi önbellek yenilenmesini bekler.
#: Ekran bunu SÖYLER; sessiz kalıp "neden değişmedi" sorusunu doğurmaz.
PUBLISH_NOTICE = "Vitrine yansıması önbellek yenilenene kadar birkaç dakika sürebilir."

#: SEO alan uzunlukları. Google'ın sabit bir sınırı yok; bunlar arama sonucunda
#: KESİLMEDEN görünen aralıklardır ve ekranda sayaç bu aralığa göre boyanır.
SEO_TITLE_MIN, SEO_TITLE_MAX = 30, 60
SEO_DESC_MIN, SEO_DESC_MAX = 70, 160

#: Çizilmesine izin verilen etiketler. Liste hem burada (kaydetmeden önce) hem
#: panelde (çizmeden önce) uygulanır — iki kapı, tek liste.
#: `tr/thead/tbody` listede: onlarsız tablo geçerli HTML olmuyor ve tarayıcı
#: hücreleri tablonun dışına atıyor.
ALLOWED_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "ul", "ol", "li", "a", "img", "strong", "em",
    "br", "table", "thead", "tbody", "tr", "td", "th",
})

#: İçeriğiyle birlikte ATILAN etiketler. Diğer tanınmayan etiketler açılır
#: (içeriği korunur); bunların içeriği metin değil koddur, korunacak bir şey yok.
DROP_TAGS = frozenset({
    "script", "style", "iframe", "frame", "frameset", "object", "embed",
    "applet", "form", "input", "button", "select", "textarea", "svg",
    "math", "noscript", "template", "link", "meta", "base",
})

VOID_TAGS = frozenset({"br", "img"})

#: Etiket başına izin verilen öznitelikler. `on*` ve `style` HİÇBİR yerde yok:
#: ilki kod çalıştırır, ikincisi sayfayı kaplayan görünmez katman kurabilir.
ALLOWED_ATTRS = {
    "a": ("href", "title"),
    "img": ("src", "alt", "title", "width", "height"),
    "td": ("colspan", "rowspan"),
    "th": ("colspan", "rowspan", "scope"),
}

#: Kabul edilen bağlantı şemaları. `data:` de reddedilir: `data:text/html`
#: tarayıcıda sayfa açar ve beyaz listeyi anlamsız kılar.
SAFE_SCHEMES = ("http", "https", "mailto", "tel")

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

#: Vitrinde CMS sayfası olmayan yol önekleri. İç bağlantı denetimi bunları
#: "bilinmiyor" sayar: ürün/kategori adresleri katalogdan gelir, burada
#: doğrulanamaz ve kırık sayılırsa her sayfa yalancı uyarı üretir.
STORE_PREFIXES = (
    "products", "product", "categories", "category", "customer", "cart",
    "checkout", "search", "compare", "wishlist", "admin", "api", "storage",
    "media", "cache",
)


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


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama biçimi.

    Türkçe harfler ÖNCE eşlenir: `str.lower()` tek başına `İ` harfini
    `i̇` (i + birleşen nokta) yapıyor ve "İade" araması "iade" ile eşleşmiyordu.
    """
    return text(value).translate(_TR_MAP).lower()


def slugify(value: str) -> str:
    """`Mesafeli Satış Sözleşmesi` → `mesafeli-satis-sozlesmesi`."""
    folded = text(value).translate(_TR_MAP)
    folded = re.sub(r"[^a-zA-Z0-9]+", "-", folded).strip("-").lower()
    return re.sub(r"-{2,}", "-", folded)


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


# ================================================================ HTML kapısı

def _safe_url(value: str, *, allow_relative: bool = True) -> str:
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
    # Şemasız ve `/` ile başlamayan değer görece yoldur (`iade.html`).
    return raw if allow_relative else ""


class _Cleaner(HTMLParser):
    """Beyaz listeli HTML üretir. Tanınmayan etiket AÇILIR, içeriği korunur."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._out: list[str] = []
        self._drop = 0          # atılan etiketin içinde miyiz (script/style…)
        self._open: list[str] = []

    def result(self) -> str:
        # Kapatılmamış etiketler kapatılır: yarım kalan `<ul>` sonraki sayfa
        # parçalarını kendi içine alıyordu.
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
            if name not in allowed:
                continue
            raw = text(value)
            if name in ("href", "src"):
                raw = _safe_url(raw)
                if not raw:
                    continue
            out.append(f' {name}="{escape(raw, quote=True)}"')
        if tag == "a" and any(item.startswith(' href="http') for item in out):
            # Dış bağlantı yeni sekmede açılır ve `rel` ile açtığı sekmeye
            # bizim pencereyi vermez (`window.opener` sızıntısı).
            out.append(' target="_blank" rel="noopener noreferrer"')
        return "".join(out)


def sanitize_html(value: Any) -> str:
    """Kaydedilecek içeriği beyaz listeye indirger (TUZAK 2).

    Panel de çizmeden önce aynı listeyi uyguluyor; bu ikinci kapıdır. İstemci
    şemayı atlatabilir, arayüzde gizlemek yetkilendirme değildir (K9).
    """
    cleaner = _Cleaner()
    cleaner.feed(text(value))
    cleaner.close()
    return cleaner.result()


class _Stripper(HTMLParser):
    """Etiketleri atıp düz metin bırakır. `script`/`style` içeriği de gider."""

    BLOCKS = frozenset({"p", "h1", "h2", "h3", "h4", "li", "tr", "br", "td", "th"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._drop = 0

    def result(self) -> str:
        return re.sub(r"[ \t]{2,}", " ", " ".join("".join(self._parts).split()))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._drop += 1
        elif name in self.BLOCKS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        name = tag.lower()
        if name in DROP_TAGS:
            self._drop = max(0, self._drop - 1)
        elif name in self.BLOCKS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._drop:
            self._parts.append(data)


def strip_html(value: Any) -> str:
    """İçerik metni — arama ve karakter sayımı bunun üzerinden yapılır."""
    stripper = _Stripper()
    stripper.feed(text(value))
    stripper.close()
    return stripper.result()


def snippet(body: str, query: str, *, width: int = 90) -> str:
    """Aramanın geçtiği yerden kısa bir alıntı. Bulunamazsa baştan keser."""
    plain = text(body)
    if not plain:
        return ""
    at = fold(plain).find(fold(query)) if text(query) else -1
    if at < 0:
        return plain[:width] + ("…" if len(plain) > width else "")
    start = max(0, at - width // 3)
    end = min(len(plain), start + width)
    return ("…" if start else "") + plain[start:end] + ("…" if end < len(plain) else "")


# ============================================================== alan okuyucu

def field(raw: Any, locale: str, *names: str) -> Any:
    """Sayfa alanını düz kayıtta ya da çeviri satırında bulur (TUZAK 1).

    Bagisto CMS sayfası yanıtı sürüme göre iki biçimde geliyor: düz sözlük ya
    da `translations` dizisi. Çağıran tarafın ikisini de bilmesi gerekseydi
    her okuma dört satır olurdu.
    """
    if not isinstance(raw, dict):
        return None
    sources: list[dict[str, Any]] = [raw]
    translations = raw.get("translations")
    if isinstance(translations, list):
        wanted = [item for item in translations
                  if isinstance(item, dict) and text(item.get("locale")) == text(locale)]
        rest = [item for item in translations
                if isinstance(item, dict) and item not in wanted]
        sources.extend(wanted)
        sources.extend(rest)

    for name in names:
        for source in sources:
            if source.get(name) not in (None, ""):
                return source[name]
    for name in names:
        for source in sources:
            if name in source:
                return source[name]
    return None


def channel_codes(raw: Any) -> list[str]:
    items = (raw or {}).get("channels") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for item in items:
        code = text(item.get("code") or item.get("name")) if isinstance(item, dict) else text(item)
        if code:
            out.append(code)
    return out


def channel_ids(raw: Any) -> list[int]:
    items = (raw or {}).get("channels") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []
    out: list[int] = []
    for item in items:
        found = as_int(item.get("id")) if isinstance(item, dict) else as_int(item)
        if found:
            out.append(found)
    return out


# ================================================================ SEO ölçüsü

def _gauge(value: str, low: int, high: int, label: str) -> dict[str, Any]:
    length = len(text(value))
    if length == 0:
        state, message = "missing", f"{label} boş — arama sonucunda mağaza kendi metnini uydurur."
    elif length < low:
        state, message = "short", f"{label} kısa ({length}/{low}); aramada zayıf görünür."
    elif length > high:
        state, message = "long", f"{label} uzun ({length}/{high}); arama sonucunda kesilir."
    else:
        state, message = "ok", f"{label} uygun ({length} karakter)."
    return {"length": length, "min": low, "max": high, "state": state, "message": message}


def seo_view(*, title: str, meta_title: str, meta_description: str, slug: str,
             base_url: str = "") -> dict[str, Any]:
    """Arama sonucu önizlemesi + karakter sayaçları.

    Meta başlık boşsa arama motoru sayfa başlığını kullanır; önizleme de öyle
    yapar — boş kutu göstermek kullanıcıya yanlış bilgi verirdi.
    """
    shown_title = text(meta_title) or text(title)
    root = text(base_url).rstrip("/")
    return {
        "title": _gauge(meta_title, SEO_TITLE_MIN, SEO_TITLE_MAX, "Meta başlık"),
        "description": _gauge(meta_description, SEO_DESC_MIN, SEO_DESC_MAX, "Meta açıklama"),
        "preview": {
            "title": shown_title[:SEO_TITLE_MAX + 10],
            "url": f"{root}/{text(slug).lstrip('/')}" if root else f"/{text(slug).lstrip('/')}",
            "description": text(meta_description)[:SEO_DESC_MAX + 20],
            "usesPageTitle": not text(meta_title),
        },
        "complete": bool(text(meta_title)) and bool(text(meta_description)),
    }


# ============================================================== sayfa satırı

def page_row(raw: dict[str, Any], *, locale: str = "tr") -> dict[str, Any]:
    """Ham CMS kaydı → ağacın ve tablonun beklediği satır."""
    content = text(field(raw, locale, "html_content", "content"))
    status_raw = field(raw, locale, "status", "is_active")
    plain = strip_html(content)
    return {
        "id": as_int(raw.get("id")),
        "title": text(field(raw, locale, "page_title", "title", "name")) or "(başlıksız)",
        "slug": text(field(raw, locale, "url_key", "slug")),
        "metaTitle": text(field(raw, locale, "meta_title")),
        "metaDescription": text(field(raw, locale, "meta_description")),
        "metaKeywords": text(field(raw, locale, "meta_keywords")),
        "locale": text(field(raw, locale, "locale")) or text(locale),
        "channels": channel_codes(raw),
        "channelIds": channel_ids(raw),
        # TUZAK 6: bazı sürümlerde CMS sayfasının durum alanı YOK. `None`
        # "bilinmiyor" demektir; 0 yazmak sayfayı yayında değil göstermek olurdu.
        "status": None if status_raw is None else bool(as_int(status_raw, 0)),
        "chars": len(plain),
        "words": len(plain.split()) if plain else 0,
        "excerpt": plain[:160] + ("…" if len(plain) > 160 else ""),
        "updatedAt": text(raw.get("updated_at"))[:19],
        "createdAt": text(raw.get("created_at"))[:19],
    }


def page_group(row: dict[str, Any], *, legal_slugs: dict[str, str],
               faq_slug: str = "") -> str:
    """Sol ağacın dalı: `legal` · `faq` · `corporate` · `other`.

    Ağaç mağazadan gelmiyor (Bagisto CMS sayfalarını düz liste tutuyor); dal
    slug'a bakılarak burada kurulur. Yasal metinler en üstte dursun diye ayrı
    dal: satış sözleşmesi ile "hakkımızda" aynı yerde durmamalı.
    """
    slug = fold(row.get("slug"))
    if slug and slug in {fold(value) for value in legal_slugs.values() if text(value)}:
        return "legal"
    if faq_slug and slug == fold(faq_slug):
        return "faq"
    if any(word in slug for word in ("sozlesme", "iade", "gizlilik", "cerez", "kvkk", "mesafeli")):
        return "legal"
    if any(word in slug for word in ("sss", "sikca", "yardim", "faq")):
        return "faq"
    if any(word in slug for word in ("hakkimizda", "iletisim", "kurumsal", "biz-kimiz")):
        return "corporate"
    return "other"


GROUP_LABELS = {
    "legal": "Yasal metinler",
    "faq": "Sıkça sorulanlar",
    "corporate": "Kurumsal",
    "other": "Diğer sayfalar",
}


def page_matches(row: dict[str, Any], body: str, query: str) -> bool:
    """Başlık, slug ve İÇERİK METNİ üzerinde arama (TUZAK 5).

    Mağaza ucu içerik metninde arayamıyor; sayfa sayısı onlarla ölçüldüğü için
    liste burada süzülür. Aynı yaklaşım 1.419 üründe yasaktır — orada tam
    liste çekilmez; fark ölçektedir, keyfî değildir.
    """
    wanted = fold(query)
    if not wanted:
        return True
    haystack = " ".join((fold(row.get("title")), fold(row.get("slug")),
                         fold(row.get("metaTitle")), fold(row.get("metaDescription")),
                         fold(body)))
    return all(part in haystack for part in wanted.split())


# ============================================================== iç bağlantı

_LINK = re.compile(r"""<a\b[^>]*\bhref\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def internal_links(html: str) -> list[str]:
    """İçerikteki bağlantılar — yalnız kendi sitemize gidenler."""
    out: list[str] = []
    for href in _LINK.findall(text(html)):
        raw = text(href)
        if not raw or raw.startswith("#"):
            continue
        parts = urlsplit(raw)
        if parts.scheme and parts.scheme.lower() not in ("http", "https"):
            continue
        if parts.netloc:
            continue          # dış site — buradan doğrulanamaz
        out.append(raw)
    return out


def link_state(href: str, *, known_slugs: set[str], rewrite_sources: set[str]) -> str:
    """`ok` · `broken` · `unknown`.

    `unknown` gerçek bir cevaptır: ürün ve kategori adresleri katalogdan gelir
    ve bu ekrandan doğrulanamaz. Onları "kırık" saymak her sayfada yalancı
    uyarı üretir, "sağlam" saymak da yanlış güven verir.
    """
    path = urlsplit(text(href)).path.strip("/")
    if not path:
        return "ok"                     # ana sayfa
    if path in {text(item).strip("/") for item in rewrite_sources}:
        return "ok"
    head = path.split("/", 1)[0].lower()
    if head in STORE_PREFIXES:
        return "unknown"
    tail = path.rsplit("/", 1)[-1]
    if tail in known_slugs or path in known_slugs:
        return "ok"
    if "." in tail:                     # dosya (`katalog.pdf`) — burada bilinemez
        return "unknown"
    return "broken"


def broken_links(html: str, *, known_slugs: set[str],
                 rewrite_sources: set[str] | None = None) -> list[dict[str, str]]:
    """Kırık iç bağlantılar. Yalnız CMS sayfası olması beklenen yollar sayılır."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for href in internal_links(html):
        if href in seen:
            continue
        seen.add(href)
        state = link_state(href, known_slugs=known_slugs,
                           rewrite_sources=rewrite_sources or set())
        if state == "broken":
            out.append({"href": href, "state": state})
    return out


def slug_change_notice(before: str, after: str) -> str:
    """Slug değişince ne olacağını SÖYLER (TUZAK 4). Sessiz kalmak kabul edilmez."""
    if not text(before) or text(before) == text(after):
        return ""
    return (f"`/{text(before)}` adresi artık çalışmayacak. Eski bağlantıların kırılmaması "
            f"için Arama & SEO sekmesindeki URL yeniden yazma bölümünden `/{text(before)}` → "
            f"`/{text(after)}` (301) kaydını ekleyin.")


# ================================================================ SSS çifti

_FAQ_BLOCK = re.compile(
    r"<h3\b[^>]*>(?P<q>.*?)</h3>(?P<a>.*?)(?=<h3\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def faq_parse(html: str) -> list[dict[str, str]]:
    """SSS sayfasını soru-cevap çiftlerine ayırır.

    SÖZLEŞME: her soru bir `<h3>`, cevabı bir sonraki `<h3>`e kadarki her şey.
    Bagisto'da SSS diye bir kayıt türü yok; SSS normal bir CMS sayfasıdır ve
    yapı bu basit kuralla taşınır. Kural bozulursa (elle düzenlenmiş sayfa)
    ayrıştırma boş döner ve ekran ham HTML düzenleyicisine düşer — içerik
    yeniden yazılmaz.
    """
    pairs: list[dict[str, str]] = []
    for match in _FAQ_BLOCK.finditer(text(html)):
        question = strip_html(match.group("q"))
        answer = sanitize_html(match.group("a")).strip()
        if not question:
            continue
        pairs.append({"question": question, "answer": answer})
    return pairs


def faq_render(pairs: Any) -> str:
    """Soru-cevap çiftlerini sayfanın HTML'ine çevirir. Cevap temizlenir."""
    parts: list[str] = []
    for pair in pairs if isinstance(pairs, list) else []:
        if not isinstance(pair, dict):
            continue
        question = text(pair.get("question"))
        if not question:
            continue
        answer = sanitize_html(pair.get("answer")).strip()
        if answer and not answer.startswith("<"):
            answer = f"<p>{answer}</p>"
        parts.append(f"<h3>{escape(question, quote=False)}</h3>")
        parts.append(answer or "<p></p>")
    return "".join(parts)


# ============================================================ yazma gövdesi

#: Sayfa kaydında yazılabilir sayılan alanlar. Listede OLMAYAN hiçbir alan
#: gövdeye konmaz: tanımadığımız alanı geri göndermek, aynı anda başka bir
#: ekranın (ya da mağaza yöneticisinin) yazdığı değeri ezmektir.
EDITABLE = ("page_title", "url_key", "html_content", "meta_title",
            "meta_description", "meta_keywords", "layout")

FIELD_ALIASES = {
    "title": "page_title",
    "slug": "url_key",
    "content": "html_content",
    "metaTitle": "meta_title",
    "metaDescription": "meta_description",
    "metaKeywords": "meta_keywords",
}


def normalize_patch(patch: Any) -> dict[str, Any]:
    """Ekranın yamasını Bagisto adlarına çevirir; tanınmayan anahtar düşer."""
    out: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        target = FIELD_ALIASES.get(key, key if key in EDITABLE else "")
        if target:
            out[target] = value
    return out


def write_body(current: dict[str, Any], patch: dict[str, Any], *, locale: str,
               channel_id_list: list[int] | None = None) -> dict[str, Any]:
    """OKU-DEĞİŞTİR-YAZ gövdesi.

    Yalnız değişen alanı göndermek, isteğe girmeyen ama işlemcinin varsaydığı
    alanları (`url_key`, `html_content`) boşaltabiliyor; gövde mevcut
    değerlerden kurulur ve üzerine değişiklik yazılır.

    `html_content` HER ZAMAN temizlenir — mevcut içerik de. Mağazada duran
    kirli HTML'i olduğu gibi geri yazmak, temizlemeyi ilk kaydetmede etkisiz
    kılardı.
    """
    body: dict[str, Any] = {}
    for key in EDITABLE:
        value = field(current, locale, key)
        if value is None:
            continue
        body[key] = value

    for key, value in (patch or {}).items():
        if key in EDITABLE:
            body[key] = value

    if "html_content" in body:
        body["html_content"] = sanitize_html(body["html_content"])
    body["locale"] = locale
    ids = channel_id_list if channel_id_list is not None else channel_ids(current)
    if ids:
        body["channels"] = ids
    return body


# ============================================================ yönlendirmeler

REDIRECT_TYPES = (301, 302)


def redirect_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Ham yönlendirme kaydı → satır.

    TUZAK: yazma gövdesi snake_case (`request_path`) istiyor ama okuma yanıtı
    camelCase gelebiliyor — komşu pazarlama uçlarında (arama terimleri) canlıda
    öyle geliyor. Kayıt canlıda henüz olmadığı için hangisi olduğu
    doğrulanamadı; ikisi de okunur, boş liste göstermektense fazladan bir
    anahtar aramak ucuzdur.
    """
    kind = as_int(raw.get("redirect_type") or raw.get("redirectType") or raw.get("type"), 301)
    return {
        "id": as_int(raw.get("id")),
        "source": text(raw.get("request_path") or raw.get("requestPath") or raw.get("source")),
        "target": text(raw.get("target_path") or raw.get("targetPath") or raw.get("target")),
        "type": kind if kind in REDIRECT_TYPES else 301,
        "locale": text(raw.get("locale")),
        "updatedAt": text(raw.get("updated_at") or raw.get("updatedAt"))[:19],
    }


def normalize_path(value: str) -> str:
    """`/iade/` · `iade` · `https://site/iade` → `iade`. Karşılaştırma bunun üzerinden."""
    raw = text(value)
    if not raw:
        return ""
    parts = urlsplit(raw)
    return (parts.path or "").strip("/")


def redirect_error(source: str, target: str, kind: int,
                   existing: list[dict[str, Any]] | None = None,
                   *, rewrite_id: int = 0) -> str:
    """Yönlendirmeyi YAZMADAN ÖNCE denetler (TUZAK 7).

    Yanlış yönlendirme sayfayı 404'ten kurtarmaz, sonsuz döngüye sokar ve
    tarayıcı "çok fazla yönlendirme" der — mağaza tarafında geri almak için
    başka bir kayıt gerekir.
    """
    left, right = normalize_path(source), normalize_path(target)
    if not left:
        return "Kaynak adres boş olamaz."
    if not text(target):
        return "Hedef adres boş olamaz."
    if left == right:
        return "Kaynak ve hedef aynı; bu kayıt sonsuz döngü üretir."
    if int(kind) not in REDIRECT_TYPES:
        return "Yönlendirme türü 301 (kalıcı) ya da 302 (geçici) olmalı."

    for row in existing or []:
        if as_int(row.get("id")) == int(rewrite_id or 0):
            continue
        if normalize_path(row.get("source")) == left:
            return f"`/{left}` için zaten bir yönlendirme var (#{as_int(row.get('id'))})."
        # Tek adım zincir: A→B varken B→A yazmak döngüdür.
        if normalize_path(row.get("source")) == right and normalize_path(row.get("target")) == left:
            return (f"`/{right}` zaten `/{left}` adresine gidiyor; bu kayıt ikisini "
                    "birbirine kilitler.")
    return ""


# ================================================================ bloklar

def block_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Tema özelleştirmesi (statik blok) satırı — bu ekranda SALT OKUNUR."""
    options = raw.get("options") if isinstance(raw.get("options"), dict) else {}
    html = text(options.get("html") or options.get("content") or raw.get("content"))
    return {
        "id": as_int(raw.get("id")),
        "name": text(raw.get("name")) or "(adsız blok)",
        "type": text(raw.get("type")),
        "sortOrder": as_int(raw.get("sort_order")),
        "status": bool(as_int(raw.get("status"), 0)),
        "channels": channel_codes(raw),
        "html": sanitize_html(html),
        "chars": len(strip_html(html)),
    }


# ================================================================ yasal metin

#: Yasal metinlerin ekranda görünen adları. Slug'lar ayardan gelir: mağaza
#: bunları farklı adlandırmış olabilir ve kod içine gömmek yanlış "eksik"
#: uyarısı üretirdi.
LEGAL_LABELS = {
    "distance_sales": "Mesafeli satış sözleşmesi",
    "refund": "İade ve cayma hakkı",
    "privacy": "Gizlilik ve KVKK aydınlatma metni",
    "cookies": "Çerez politikası",
}


def legal_status(pages: list[dict[str, Any]], legal_slugs: dict[str, str]) -> list[dict[str, Any]]:
    """Dört yasal metnin durumu: var mı, boş mu, ne zaman güncellenmiş.

    Eksik yasal metin mağaza için mevzuat riskidir; ekran bunu bir satırda
    söyler ve sayfayı açacak yolu verir.
    """
    by_slug = {fold(row.get("slug")): row for row in pages}
    out: list[dict[str, Any]] = []
    for key, label in LEGAL_LABELS.items():
        slug = text(legal_slugs.get(key))
        row = by_slug.get(fold(slug)) if slug else None
        out.append({
            "key": key,
            "label": label,
            "slug": slug,
            "pageId": as_int(row.get("id")) if row else 0,
            "found": bool(row),
            "empty": bool(row) and as_int(row.get("chars")) < 200,
            "chars": as_int(row.get("chars")) if row else 0,
            "updatedAt": text(row.get("updatedAt")) if row else "",
        })
    return out
