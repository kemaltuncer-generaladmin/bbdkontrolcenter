"""Silinmiş ürün tespiti — SAF KURAL, TEK YERDE.

NEDEN AYRI DOSYA VE AYRI YETENEK. Aynı soruyu üç ekran soruyor: ürün geçmişi
(bu modül), sipariş kalemleri (`store_orders`), satış raporu (`store_reports`).
Kural üç yere kopyalansaydı bir gün ayrışır ve aynı sipariş bir ekranda
"silinmiş", ötekinde normal görünürdü. Modül modülü İMPORT ETMEZ (K3); kural
`module.yaml` içinde `provides` ile ilan edilir ve registry üzerinden paylaşılır.

KURAL — NEDEN ÇALIŞIYOR. Sipariş kalemi ürünün ADINI, SKU'sunu, FİYATINI ve
TOPLAMINI kendi satırında saklar; ürün silinince bu alanlar yerinde kalır.
`order_items.product_id` ise NULL kabul eder ve `products` tablosuna YABANCI
ANAHTAR KISITI YOKTUR (`2018_09_27_113207_create_order_items_table.php`:51,
58-59 — yalnız `order_id` ve `parent_id` için kısıt var). Yani ürün silmek
geçmişi bozmaz, sadece kalemin ürün bağlantısını boşa düşürür. Tespit bundan
çıkar: KALEM AD/SKU TAŞIYOR AMA `productId` KATALOĞA ÇÖZÜLMÜYORSA o kalem
silinmiş üründendir.

ÜÇ CEVAP VARDIR, İKİ DEĞİL. "Katalog okundu, ürün yok" ile "katalog okunamadı"
ikisi de BOŞ bir kimlik kümesi üretir; ilkinde kalem gerçekten silinmiştir,
ikincisinde hiçbir şey bilinmiyordur. İkisini tek cevaba toplamak, mağaza bir
dakika yanıt vermediğinde bütün sipariş geçmişini kırmızı "silinmiş" ile
boyardı. Bu yüzden çözüm listesinin TAM okunup okunmadığı ayrı bir bayrakla
taşınır (`lookup_complete`) ve eksikse cevap `unknown` olur.

BU DOSYA AĞA ÇIKMAZ. Kimlik çözümü (hangi ürün hâlâ katalogda) çağıranın
işidir; buradaki her şey girdi→çıktı fonksiyonudur ve testi ağsız çalışır.
"""

from __future__ import annotations

from typing import Any

from .catalog import as_int, text

#: Kullanıcının gördüğü ibare. Ekranlar bu sabiti kullanır, metni kopyalamaz —
#: kelime değişirse üç ekranda birden değişsin.
LABEL = "silinmiş"

#: Kararsız hâlin ibaresi. "silinmiş" DEĞİLDİR: mağaza okunamadığı için
#: bilinmiyor demektir ve kullanıcıya öyle söylenir.
UNKNOWN_LABEL = "doğrulanamadı"

STATE_LIVE = "live"
STATE_DELETED = "deleted"
STATE_UNKNOWN = "unknown"

#: Ekran tonları. Kırmızı (`bad`) YALNIZ kesin silinmişte kullanılır; renk tek
#: başına anlam taşımasın diye yanında her zaman yazı durur.
TONES = {STATE_LIVE: "", STATE_DELETED: "bad", STATE_UNKNOWN: "warn"}

LABELS = {STATE_LIVE: "", STATE_DELETED: LABEL, STATE_UNKNOWN: UNKNOWN_LABEL}

NOTES = {
    STATE_LIVE: "",
    STATE_DELETED: "Ürün katalogdan silindi. Kalemin adı, SKU'su ve fiyatı sipariş "
                   "satırında saklandığı için tutar ve geçmiş değişmedi.",
    STATE_UNKNOWN: "Katalog tam okunamadı; bu kalemin ürünü silinmiş olabilir de "
                   "olmayabilir de. Mağaza yanıt verince yeniden bakın.",
}

#: Sipariş kaleminde ürün kimliğinin bulunabileceği alan adları. Bagisto REST
#: yüzeyi camelCase, ham tablo snake_case veriyor; ikisi de okunur.
_ID_KEYS = ("product_id", "productId")
_NAME_KEYS = ("name", "productName", "product_name")
_SKU_KEYS = ("sku", "productSku", "product_sku")
_QTY_KEYS = ("qty_ordered", "qtyOrdered", "quantity", "qty")


def _pick(item: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(item, dict):
        return None
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return None


def product_id_of(item: Any) -> int:
    return as_int(_pick(item, _ID_KEYS))


def name_of(item: Any) -> str:
    return text(_pick(item, _NAME_KEYS))


def sku_of(item: Any) -> str:
    return text(_pick(item, _SKU_KEYS))


def qty_of(item: Any) -> int:
    return as_int(_pick(item, _QTY_KEYS))


def product_ids(items: Any) -> list[int]:
    """Çözülmesi gereken benzersiz ürün kimlikleri — sıra korunur.

    Çağıran bu listeyi kataloğa sorar; aynı ürün on kalemde geçse bile TEK
    istek eder. Sipariş kalemlerinin çoğu aynı birkaç üründen gelir.
    """
    out: list[int] = []
    for item in items or []:
        found = product_id_of(item)
        if found and found not in out:
            out.append(found)
    return out


def state_of(item: Any, *, known_ids: Any = (), lookup_complete: bool = True) -> str:
    """Kalem hangi hâlde: `live` · `deleted` · `unknown`.

    `known_ids` KATALOĞA ÇÖZÜLEN kimliklerdir. `lookup_complete` o çözümün tam
    yapıldığını söyler; yarım kaldıysa hiçbir kalem "silinmiş" sayılmaz.
    """
    if not lookup_complete:
        return STATE_UNKNOWN

    known = {as_int(value) for value in (known_ids or ())}
    found = product_id_of(item)
    if found and found in known:
        return STATE_LIVE

    # Ad ve SKU'nun İKİSİ birden boşsa elimizdeki satır bir sipariş kalemi
    # değildir (ya da öyle bozuktur ki hiçbir şey iddia edilemez). "Silinmiş"
    # demek için kalemin ürünü TEMSİL ETTİĞİNE dair kanıt gerekiyor.
    if not name_of(item) and not sku_of(item):
        return STATE_UNKNOWN

    # Kimlik hiç yok ya da katalogda karşılığı yok — ikisi de aynı sonuç:
    # bu kalemin arkasında duran ürün artık katalogda değil.
    return STATE_DELETED


def mark_item(item: Any, *, known_ids: Any = (), lookup_complete: bool = True) -> dict[str, Any]:
    """Tek kalemi ibaresiyle birlikte döndürür. Kaynak satır DEĞİŞTİRİLMEZ."""
    state = state_of(item, known_ids=known_ids, lookup_complete=lookup_complete)
    return {
        "productId": product_id_of(item),
        "name": name_of(item),
        "sku": sku_of(item),
        "qty": qty_of(item),
        "state": state,
        "deleted": state == STATE_DELETED,
        "label": LABELS[state],
        "tone": TONES[state],
        "note": NOTES[state],
    }


def mark_items(items: Any, *, known_ids: Any = (),
               lookup_complete: bool = True) -> list[dict[str, Any]]:
    return [mark_item(item, known_ids=known_ids, lookup_complete=lookup_complete)
            for item in (items or []) if isinstance(item, dict)]


def summary(rows: Any) -> dict[str, int]:
    """Kaç kalem hangi hâlde. Rapor başlığı bunu yazar."""
    marked = [row for row in (rows or []) if isinstance(row, dict)]
    return {
        "total": len(marked),
        "live": len([row for row in marked if row.get("state") == STATE_LIVE]),
        "deleted": len([row for row in marked if row.get("state") == STATE_DELETED]),
        "unknown": len([row for row in marked if row.get("state") == STATE_UNKNOWN]),
    }


def ids_from_audit(rows: Any) -> set[int]:
    """YERELDE KESİN bilinen silinmişler: bu ekrandan silinip `ok` dönenler.

    Denetim izi bizim tablomuz olduğu için ağ gerektirmez ve mağaza düşse bile
    doğrudur. Kataloğa sormanın yerine geçmez (mağaza yöneticisi kendi
    panelinden de silmiş olabilir); onun ÜSTÜNE eklenir.
    """
    out: set[int] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        action = text(row.get("action") or row.get("islem"))
        result = text(row.get("result"))
        if action == "delete_product" and result == "ok":
            found = as_int(row.get("product_id") or row.get("productId"))
            if found:
                out.add(found)
    return out


class Marker:
    """Registry'ye konan yüz — `store_products.deleted_marker`.

    Başka modül bu nesneyi `ctx.capability(...)` ile alır; dosyayı import
    ETMEZ (K3). Metotlar modül düzeyindeki saf fonksiyonların ta kendisidir:
    burada ikinci bir uygulama yoktur, olsaydı kural yine ikiye ayrılırdı.
    """

    label = LABEL
    unknown_label = UNKNOWN_LABEL
    tone = TONES[STATE_DELETED]

    @staticmethod
    def product_ids(items: Any) -> list[int]:
        return product_ids(items)

    @staticmethod
    def state(item: Any, *, known_ids: Any = (), lookup_complete: bool = True) -> str:
        return state_of(item, known_ids=known_ids, lookup_complete=lookup_complete)

    @staticmethod
    def mark(items: Any, *, known_ids: Any = (),
             lookup_complete: bool = True) -> list[dict[str, Any]]:
        return mark_items(items, known_ids=known_ids, lookup_complete=lookup_complete)

    @staticmethod
    def summary(rows: Any) -> dict[str, int]:
        return summary(rows)
