"""Arama ve SEO — saf dönüşümler. Ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. `content.py` sayfa metniyle uğraşır (HTML temizleme, SEO
sayacı, SSS ayrıştırma); buradaki dört konu ise mağazanın PAZARLAMA
kayıtlarıdır: URL yeniden yazma, arama terimleri, eş anlamlılar, site
haritası. Aynı dosyaya doldurmak iki farklı işi tek yığın hâline getirirdi.

DÖRT TUZAK — her birinin karşılığı burada bir fonksiyondur:

 1. Alan adı biçimi UÇTAN UCA AYNI DEĞİL. Okuma yanıtı camelCase geliyor
    (canlıda doğrulandı: `redirectUrl`, `createdAt`), yazma gövdesi ise
    snake_case istiyor (`request_path`, `file_name`). `pick` ikisini de okur;
    gövdeyi kuran taraf snake_case yazar.
 2. Eş anlamlı `terms` alanı LİSTE DEĞİL, virgülle ayrılmış TEK METİNDİR.
    `synonym_text` çeviriyi tek yerde yapar.
 3. `results: 0` "aradı, bulamadı" demektir — katalog boşluğunu gösteren en
    değerli veri odur. Uçta `results` süzgeci YOK; süzme `term_matches` ile
    burada yapılır (ölçek: canlıda 19 terim).
 4. Aynı kaynak adresi iki kez yönlendirmek, hangi kaydın kazandığını
    öngörülemez kılar. `mark_conflicts` listeyi işaretler, `content.
    redirect_error` da yazmadan önce durdurur — iki kapı.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from . import content
from .content import as_int, fold, text

#: Yönlendirmenin bağlandığı kayıt türü. Mağaza bu alanı ZORUNLU istiyor;
#: göndermemek isteği doğrulamadan döndürür.
ENTITY_LABELS = {
    "product": "Ürün",
    "category": "Kategori",
    "cms_page": "CMS sayfası",
}

#: Varsayılan tür CMS sayfası: bu ekran sayfa adresleriyle uğraşıyor ve
#: yönlendirme ihtiyacı çoğunlukla bir sayfa adı değişince doğuyor.
DEFAULT_ENTITY = "cms_page"

#: Eş anlamlı grubunun en az kelime sayısı. Tek kelimelik grup arama
#: sonucunu hiç değiştirmez; kullanıcı "kaydettim ama bir şey olmadı" der.
MIN_SYNONYM_TERMS = 2


def pick(raw: Any, *names: str) -> Any:
    """Alanı verilen adların hangisiyle geldiyse onunla bulur (TUZAK 1)."""
    if not isinstance(raw, dict):
        return None
    for name in names:
        if raw.get(name) not in (None, ""):
            return raw[name]
    return None


# ======================================================== URL yeniden yazma

def rewrite_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Ham yönlendirme kaydı → tablonun beklediği satır.

    Gövde `content.redirect_row` ile ortaktır: kaynak/hedef okuması iki
    ekranda ayrışmasın. Buraya yalnız kayıt türü eklenir.
    """
    row = content.redirect_row(raw)
    entity = text(pick(raw, "entityType", "entity_type")) or DEFAULT_ENTITY
    row["entityType"] = entity
    row["entityLabel"] = ENTITY_LABELS.get(entity, entity)
    row["conflict"] = False
    return row


def mark_conflicts(rows: list[dict[str, Any]]) -> int:
    """Aynı kaynak adresi taşıyan kayıtları işaretler; kaç satır çakıştığını döner.

    Çakışmayı yalnız yazma anında yakalamak yetmez: mağaza tarafından ya da
    bu ekran yazılmadan önce girilmiş çift kayıtlar listede durur ve hangisinin
    uygulandığı belli olmaz. Ekran bunu satır satır söyler.
    """
    counts = Counter(content.normalize_path(row.get("source")) for row in rows)
    hits = 0
    for row in rows:
        row["conflict"] = counts[content.normalize_path(row.get("source"))] > 1
        hits += 1 if row["conflict"] else 0
    return hits


def entity_error(entity: str) -> str:
    if text(entity) not in ENTITY_LABELS:
        return "Kayıt türü ürün, kategori ya da CMS sayfası olmalı."
    return ""


def rewrite_matches(row: dict[str, Any], query: str) -> bool:
    wanted = fold(query)
    if not wanted:
        return True
    haystack = fold(f"{row.get('source')} {row.get('target')} {row.get('entityLabel')}")
    return all(part in haystack for part in wanted.split())


# ============================================================ arama terimleri

def term_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Arama terimi satırı. Alan adları canlıda doğrulandı (camelCase)."""
    results = as_int(pick(raw, "results"))
    return {
        "id": as_int(raw.get("id")),
        "term": text(pick(raw, "term")),
        "results": results,
        "uses": as_int(pick(raw, "uses")),
        "redirectUrl": text(pick(raw, "redirectUrl", "redirect_url")),
        "locale": text(pick(raw, "locale")),
        "channel": text(pick(raw, "channel", "channel_id")),
        # TUZAK 3: sonuçsuz arama katalog boşluğudur; ayrı bayrak taşır ki
        # ekran onu bir süzgeçle öne çıkarabilsin.
        "zeroResults": results == 0,
        "updatedAt": text(pick(raw, "updatedAt", "updated_at"))[:19],
    }


def term_matches(row: dict[str, Any], query: str) -> bool:
    wanted = fold(query)
    if not wanted:
        return True
    return all(part in fold(row.get("term")) for part in wanted.split())


def term_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Şeridin üstündeki sayılar. `zeroUses` ölçüyü verir: kaç arama boşa gitti."""
    zero = [row for row in rows if row["zeroResults"]]
    return {
        "total": len(rows),
        "zero": len(zero),
        "uses": sum(row["uses"] for row in rows),
        "zeroUses": sum(row["uses"] for row in zero),
        "redirected": len([row for row in rows if row["redirectUrl"]]),
    }


def term_sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    """Sonuçsuz aramalar en üstte, aranma sayısı çok olan önce.

    Sıralama sunucudan istenmiyor: `sort=results` sonuçsuzları öne alır ama
    aynı anda `uses` sırasını kaybettirir; ikisi birlikte tek çağrıda yok.
    """
    return (0 if row["zeroResults"] else 1, -row["uses"], fold(row.get("term")))


# =============================================================== eş anlamlılar

def synonym_terms(value: Any) -> list[str]:
    """`"kalem, tükenmez"` ya da `["kalem","tükenmez"]` → temiz liste (TUZAK 2)."""
    parts = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        word = text(part)
        if not word or fold(word) in seen:
            continue
        seen.add(fold(word))
        out.append(word)
    return out


def synonym_text(value: Any) -> str:
    """Mağazanın beklediği biçim: virgülle ayrılmış TEK metin."""
    return ",".join(synonym_terms(value))


def synonym_row(raw: dict[str, Any]) -> dict[str, Any]:
    terms = synonym_terms(pick(raw, "terms"))
    return {
        "id": as_int(raw.get("id")),
        "name": text(pick(raw, "name")) or "(adsız grup)",
        "terms": terms,
        "termText": ",".join(terms),
        "count": len(terms),
        "locale": text(pick(raw, "locale")),
        "updatedAt": text(pick(raw, "updatedAt", "updated_at"))[:19],
    }


def synonym_matches(row: dict[str, Any], query: str) -> bool:
    wanted = fold(query)
    if not wanted:
        return True
    haystack = fold(f"{row.get('name')} {' '.join(row.get('terms') or [])}")
    return all(part in haystack for part in wanted.split())


def synonym_error(name: str, terms: Any, existing: list[dict[str, Any]] | None = None,
                  *, synonym_id: int = 0) -> str:
    """Eş anlamlı grubunu YAZMADAN ÖNCE denetler."""
    words = synonym_terms(terms)
    if not text(name):
        return "Grup adı zorunlu."
    if len(words) < MIN_SYNONYM_TERMS:
        return ("Bir grup en az iki kelime ister; tek kelimelik grup arama sonucunu "
                "değiştirmez.")
    for row in existing or []:
        if as_int(row.get("id")) == int(synonym_id or 0):
            continue
        if fold(row.get("name")) == fold(name):
            return f"`{text(name)}` adında bir grup zaten var (#{as_int(row.get('id'))})."
        theirs = {fold(word) for word in (row.get("terms") or [])}
        clash = [word for word in words if fold(word) in theirs]
        if clash:
            # Aynı kelime iki grupta: arama hangi grubu genişleteceğini
            # bilemez ve sonuç kayıttan kayda değişir.
            return (f"`{', '.join(clash)}` kelimesi `{text(row.get('name'))}` grubunda da "
                    "var; aynı kelimeyi iki gruba koymak arama sonucunu öngörülemez kılar.")
    return ""


# ============================================================== site haritası

def sitemap_row(raw: dict[str, Any], *, base_url: str = "") -> dict[str, Any]:
    """Site haritası tanımı. Alan adları DOĞRULANMADI (canlıda 0 kayıt) —
    bu yüzden hem camelCase hem snake_case okunur."""
    file_name = text(pick(raw, "fileName", "file_name"))
    path = text(pick(raw, "path")) or "/"
    root = text(base_url).rstrip("/")
    # Parçalar tek tek birleştirilir: `root + path + ad` metin toplamı,
    # `https://` içindeki çift eğik çizgiyi de ezen bir temizliğe zorluyordu.
    url = "/".join(part for part in (root, path.strip("/"), file_name) if part)
    return {
        "id": as_int(raw.get("id")),
        "fileName": file_name or "(adsız)",
        "path": path,
        "generatedAt": text(pick(raw, "generatedAt", "generated_at"))[:19],
        "updatedAt": text(pick(raw, "updatedAt", "updated_at"))[:19],
        "links": as_int(pick(raw, "links", "linksCount", "links_count")),
        "url": url if root and file_name else "",
    }


def sitemap_error(file_name: str, path: str, existing: list[dict[str, Any]] | None = None,
                  *, sitemap_id: int = 0) -> str:
    """Tanımı YAZMADAN ÖNCE denetler. Tanım DOSYA ÜRETMEZ (TUZAK 4)."""
    name = text(file_name)
    if not name:
        return "Dosya adı zorunlu (ör. sitemap.xml)."
    if "/" in name or "\\" in name:
        return "Dosya adı klasör içeremez; klasörü yol alanına yazın."
    if not name.lower().endswith(".xml"):
        return "Dosya adı `.xml` ile bitmeli; arama motorları başka uzantıyı okumaz."
    if not text(path):
        return "Yol zorunlu; kök dizin için `/` yazın."
    for row in existing or []:
        if as_int(row.get("id")) == int(sitemap_id or 0):
            continue
        same_path = text(row.get("path")).strip("/") == text(path).strip("/")
        if fold(row.get("fileName")) == fold(name) and same_path:
            return (f"`{name}` aynı yolda zaten tanımlı (#{as_int(row.get('id'))}); "
                    "ikinci tanım birincinin üstüne yazar.")
    return ""


def sitemap_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    generated = [row for row in rows if row["generatedAt"]]
    return {
        "total": len(rows),
        "generated": len(generated),
        "never": len(rows) - len(generated),
        # En son üretim: "site haritası güncel mi" sorusunun tek satırlık cevabı.
        "lastGeneratedAt": max((row["generatedAt"] for row in generated), default=""),
    }
