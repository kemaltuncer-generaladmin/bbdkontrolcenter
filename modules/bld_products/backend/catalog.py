"""Ürün kataloğunun saf kuralları — ağ yok, depo yok, istisna yok.

NEDEN AYRI DOSYA: burada yalnız biçim ve sözlük var. Sunucudan gelen satırın
panelin okuyabileceği şekle çevrilmesi, gerekçenin ölçülmesi, süzgeç
değerlerinin sözleşmedeki kümeye indirgenmesi ve kategori ağacının döngüye
girip girmediği — hepsi ağa çıkmadan sınanabilir. Servis bu dosyaya bakarak
karar verir, kendi içinde ikinci bir sözlük tutmaz.

ALAN ADLARI SÖZLEŞMEDEN GELİR (`BLD/docs/control/products.md`). Uydurulmuş bir
ad (`title`, `image`, `categories`) burada sessizce boş döner ve ekran "veri
yok" der; bu yüzden her dönüştürücü SÖZLEŞMEDEKİ adı okur ve bulamadığında
varsayılanı yazar, ikinci bir ad DENEMEZ.

TÜRETİLEN ALANLAR AYRI TUTULUR. `has_image`, `sellable_today` ve
`price_locked` sunucudan gelmez; panelin sorusunu cevaplamak için burada
hesaplanır ve satırda böyle işaretlidir. Sunucudan gelen bir alanla
karıştırılırlarsa, sözleşme büyüdüğünde hangisinin normatif olduğu bilinemez.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------- sınırlar

#: Gerekçe alt sınırı — `00-genel.md` §3. Sunucu da denetler (K9, çift kapı).
MIN_REASON = 10

#: Gerekçe üst sınırı. Ürün alanında 500'dür; 160'lık sıkı sınır yalnız sipariş
#: revizyonu ve durum geçişindedir ve bu ekranın işi değildir.
MAX_REASON = 500

#: Ürün adı — sözleşme "zorunlu, 2-128 karakter" diyor.
NAME_MIN = 2
NAME_MAX = 128

#: Aktör (oturumdan gelir, gövdeden DEĞİL) — `00-genel.md` §3.
ACTOR_MAX = 120

#: Görsel sınırları. GERÇEK KAPI GEÇİTTE VE SUNUCUDADIR (`bld_api/upload.py`
#: → `PRODUCT_IMAGE_MAX_BYTES`, sunucuda `finfo_buffer`); buradaki değerler
#: yalnız PANELE gönderilen kural künyesidir ve `products.md` doğrulama
#: sırasından kopyalanmıştır. Modül modülü import etmez (K3), bu yüzden değer
#: tekrarlanır; gevşetilmesi sunucuyu gevşetmez, sıkılması ise sunucunun kabul
#: edeceği bir dosyayı gönderilemez kılar — o yüzden ikisi de yapılmaz.
IMAGE_MAX_BYTES = 5 * 1024 * 1024
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp")

#: Sayfalama — `00-genel.md` §5. Tavan sunucununkiyle aynıdır.
PER_PAGE_DEFAULT = 25
PER_PAGE_MAX = 100

# --------------------------------------------------------------- sözlükler

#: `GET /products` sıralama seçenekleri (`products.md` → liste tablosu).
SORTS = ("name", "price", "priority", "updated")
SORT_LABELS = {
    "name": "Ada göre",
    "price": "Fiyata göre",
    "priority": "Sıra numarasına göre",
    "updated": "Son değişikliğe göre",
}
DEFAULT_SORT = "name"

DIRECTIONS = ("asc", "desc")
DEFAULT_DIRECTION = "asc"

#: `status` süzgeci. VARSAYILAN `all`, `active` DEĞİL: yönetimin ilk sorusu
#: çoğu zaman "bu ürün nerede" biçiminde gelir ve cevabı "satıştan
#: kaldırılmış"tır; varsayılan süzgeç onu gizleseydi ürün kaybolmuş görünürdü.
STATUS_FILTERS = ("all", "active", "inactive")
STATUS_LABELS = {
    "all": "Hepsi",
    "active": "Satışta",
    "inactive": "Satıştan kaldırılmış",
}
DEFAULT_STATUS = "all"

#: `PATCH /products/{menu}` ile yazılabilen alanlar (`products.md`).
#: Listede olmayan bir anahtar REDDEDİLİR, sessizce düşürülmez: Laravel
#: tanımadığı alanı yok sayar ve ekran "kaydedildi" derken hiçbir şey
#: değişmemiş olurdu.
PRODUCT_PATCH_FIELDS = (
    "name", "description", "price_kurus", "minimum_qty", "priority", "status",
    "category_ids",
)

#: `PATCH /products/categories/{id}` ile yazılabilen alanlar.
#: `slug` YOKTUR — `permalink_slug` çekirdeğin `HasPermalink` özelliğiyle addan
#: üretilir; elle yazdırmak sitedeki adresi yönetici yazım hatasına bağlardı.
CATEGORY_PATCH_FIELDS = ("name", "description", "parent_id", "priority", "status")

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Yerel iz `action` adları. Sunucudaki `veykemtu_control_audit` karşılıkları
#: `products.md` denetim tablosundadır ve AYNI adları taşır: iki defteri yan
#: yana koyup okuyabilmek, "gönderildi mi" sorusunun tek cevabıdır.
ACTIONS = (
    "product.create", "product.update", "product.delete", "product.image",
    "product.image.delete", "product.sold_out", "product.sold_out.clear",
    "category.create", "category.update",
)


# ------------------------------------------------------------- dönüştürme

def as_int(value: Any, fallback: int = 0) -> int:
    """Sayıya çevirir; çevrilemezse yedek değer. Sunucu `null` gönderebilir."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any) -> bool:
    """Metin, sayı ve boolean'ı tek kurala indirger.

    Tercih tablosundan `"0"` metni okunuyor ve `bool("0")` Python'da `True`;
    buradaki açık liste o tuzağı kapatır.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "evet", "yes", "on"}


def text(value: Any) -> str:
    """Kırpılmış metin; `None` boş dizeye düşer."""
    return str(value or "").strip()


def optional_text(value: Any) -> str | None:
    """Boş metni `None` yapar — sözleşmede `description` `string|null`.

    Boş dize ile `null` arasındaki fark burada korunur: sunucuya boş dize
    yazmak, açıklamayı "boş bir açıklama" yapar; `null` "açıklama yok" der.
    """
    cleaned = text(value)
    return cleaned or None


def now_iso() -> str:
    """Yerel denetim izinin damgası — ISO 8601 UTC (`00-genel.md` §6)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def reason_error(reason: Any) -> str:
    """Gerekçe denetimi. Sorun yoksa boş dize döner.

    Arayüzde zorunlu göstermek yetkilendirme değildir (K9): istemci gövdeyi
    elle kurabilir ve gerekçesiz bir yazma denetim izini okunamaz kılardı.
    """
    cleaned = text(reason)
    if len(cleaned) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı: denetim izinde "
                "'düzeltme' yazan bir satır hiçbir şey anlatmıyor.")
    if len(cleaned) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir (şu an {len(cleaned)})."
    return ""


def name_error(name: Any) -> str:
    """Ürün/kategori adı denetimi. Sorun yoksa boş dize.

    AYNI AD ENGELLENMEZ (sözleşme kararı): "Tavuk Sote" iki farklı tarifle iki
    ürün olabilir ve adı tekilleştirmek gerçek bir işi bloke ederdi. Panel
    uyarır, sunucu engellemez, burada da engellenmez.
    """
    cleaned = text(name)
    if len(cleaned) < NAME_MIN:
        return f"Ad en az {NAME_MIN} karakter olmalı."
    if len(cleaned) > NAME_MAX:
        return f"Ad en çok {NAME_MAX} karakter olabilir (şu an {len(cleaned)})."
    return ""


def clean_sort(value: Any) -> str:
    """Sıralama anahtarını sözleşmedeki dörtlüye indirger."""
    cleaned = text(value).lower()
    return cleaned if cleaned in SORTS else DEFAULT_SORT


def clean_direction(value: Any) -> str:
    cleaned = text(value).lower()
    return cleaned if cleaned in DIRECTIONS else DEFAULT_DIRECTION


def clean_status(value: Any) -> str:
    cleaned = text(value).lower()
    return cleaned if cleaned in STATUS_FILTERS else DEFAULT_STATUS


def clean_per_page(value: Any, fallback: int = PER_PAGE_DEFAULT) -> int:
    """Sayfa boyutu; tavan sunucunun tavanıdır (100)."""
    size = as_int(value, fallback) or fallback
    return max(1, min(PER_PAGE_MAX, size))


def category_ids(value: Any) -> list[int]:
    """Kategori kimlikleri — tekilleştirilmiş, sırası korunmuş tam liste.

    `PATCH` gönderildiğinde bu liste TAM LİSTEDİR ve pivot tablo ona
    eşitlenir (`products.md`); fark göndermek, iki kategoriden birini
    kaldırmanın adını gerektirirdi.
    """
    if not isinstance(value, list | tuple | set):
        return []
    out: list[int] = []
    for item in value:
        number = as_int(item, 0)
        if number > 0 and number not in out:
            out.append(number)
    return out


def option_row(raw: Any) -> dict[str, Any]:
    """Salt okunur ürün seçeneği (`ProductOption`).

    `values[].id` sipariş revizyonundaki `option_value_ids` alanına doğrudan
    konuyor; kimlikler bu yüzden AYNEN taşınır, yeniden numaralanmaz.
    """
    data = raw if isinstance(raw, dict) else {}
    values = []
    for item in data.get("values") or []:
        if not isinstance(item, dict):
            continue
        values.append({
            "id": as_int(item.get("id")),
            "name": text(item.get("name")),
            "price_delta_kurus": as_int(item.get("price_delta_kurus")),
        })
    return {
        "id": as_int(data.get("id")),
        "name": text(data.get("name")),
        "type": text(data.get("type")),
        "required": as_bool(data.get("required")),
        "values": values,
    }


def product_row(raw: Any) -> dict[str, Any]:
    """Sunucu satırını panelin okuduğu şekle çevirir.

    TÜRETİLEN ÜÇ ALAN sondadır ve sunucudan gelmez:

      has_image      — küçük resim sütunu "yok" kutusunu buna göre çizer.
      sellable_today — satışta VE bugün tükenmemiş. İki ayrı rozet yerine tek
                       cümle kurmak için; rozetler yine ikisini de gösterir.
      price_locked   — paket ürününün fiyatı GÜNÜN MENÜSÜNDEDİR. Panel fiyat
                       alanını buna bakarak kapatır; yazmak, günün menüsünü
                       yanlış tutara satardı (sunucu da `422` ile reddeder).
    """
    data = raw if isinstance(raw, dict) else {}
    package = as_bool(data.get("is_package_product"))
    image_url = text(data.get("image_url"))
    sold_out = as_bool(data.get("sold_out_today"))
    status = as_bool(data.get("status"))
    return {
        "menu_id": as_int(data.get("menu_id")),
        "name": text(data.get("name")),
        "description": text(data.get("description")),
        "price_kurus": as_int(data.get("price_kurus")),
        "minimum_qty": as_int(data.get("minimum_qty"), 1),
        "priority": as_int(data.get("priority")),
        "status": status,
        "category_ids": category_ids(data.get("category_ids")),
        "image_url": image_url,
        "sold_out_today": sold_out,
        "sold_out_reason": text(data.get("sold_out_reason")),
        "is_package_product": package,
        "options": [option_row(item) for item in data.get("options") or []],
        "created_at": text(data.get("created_at")),
        "updated_at": text(data.get("updated_at")),
        # --- türetilen (sunucuda karşılığı yok) ---
        "has_image": bool(image_url),
        "sellable_today": status and not sold_out,
        "price_locked": package,
    }


def category_row(raw: Any) -> dict[str, Any]:
    """Kategori satırı. `parent_id` `None` kalabilir — kök kategori demektir."""
    data = raw if isinstance(raw, dict) else {}
    parent = data.get("parent_id")
    return {
        "category_id": as_int(data.get("category_id")),
        "name": text(data.get("name")),
        "description": text(data.get("description")),
        "parent_id": as_int(parent) if parent not in (None, "") else None,
        "priority": as_int(data.get("priority")),
        "status": as_bool(data.get("status")),
        "slug": text(data.get("slug")),
        "menu_count": as_int(data.get("menu_count")),
    }


def category_tree(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Kategorileri ağaç sırasına dizer ve her satıra `depth` yazar.

    Ekran hepsini bir ağaç olarak çiziyor ve liste sayfalanmıyor (sözleşme:
    "kategori sayısı onlarla ifade edilir"). Sıralama önce `priority`, eşitse
    ada göre — sitedeki sıra da budur.

    ÖKSÜZ SATIR KAYBOLMAZ: `parent_id` listede olmayan bir kimliği gösteriyorsa
    (üst kategori gizlenmiş ya da başka bir sorguda kalmış olabilir) satır KÖK
    seviyesine alınır. Sessizce düşürmek, ekranda hiç görünmeyen ama sitede
    duran bir kategori bırakırdı.
    """
    known = {row["category_id"] for row in rows}
    children: dict[int | None, list[dict[str, Any]]] = {}
    for row in rows:
        parent = row["parent_id"] if row["parent_id"] in known else None
        children.setdefault(parent, []).append(row)
    for bucket in children.values():
        bucket.sort(key=lambda item: (item["priority"], item["name"].lower()))

    out: list[dict[str, Any]] = []
    seen: set[int] = set()

    def walk(parent: int | None, depth: int) -> None:
        for row in children.get(parent, []):
            key = row["category_id"]
            if key in seen:          # bozuk veride döngü olsa bile durur
                continue
            seen.add(key)
            out.append({**row, "depth": depth})
            walk(key, depth + 1)

    walk(None, 0)
    # Döngüye takılıp hiç yazılmayan satırlar yine de listelenir: eksik veri
    # göstermek, veriyi hiç göstermemekten iyidir.
    out.extend({**row, "depth": 0} for row in rows if row["category_id"] not in seen)
    return out


def would_cycle(rows: list[dict[str, Any]], category_id: int, parent_id: Any) -> bool:
    """Yeni üst kategori kendisine ya da kendi alt ağacına mı işaret ediyor?

    Sunucu bunu `422` (`details.reason = "cycle"`) ile reddediyor; buradaki
    denetim isteği HİÇ GÖNDERMEDEN aynı cevabı verir. Çekirdek `NestedTree`
    böyle bir kaydı kabul edip ağacı bozardı ve hata ancak site menüsü
    çizilemediğinde fark edilirdi.
    """
    target = as_int(parent_id, 0)
    if target <= 0:
        return False
    if target == int(category_id):
        return True
    parents = {row["category_id"]: row["parent_id"] for row in rows}
    seen: set[int] = set()
    cursor: Any = target
    while cursor:
        node = as_int(cursor, 0)
        if node in seen:             # bozuk veri: zaten döngü var
            return True
        seen.add(node)
        if node == int(category_id):
            return True
        cursor = parents.get(node)
    return False


def page_meta(meta: Any, *, page: int, per_page: int, rows: int) -> dict[str, int]:
    """Sayfalama künyesi. Sunucu `meta` göndermediyse ekranı çıkmaza sokmaz.

    Eksik `total` sıfır yazılmaz: sıfır "kayıt yok" demektir ve dolu bir
    listenin altında "0 kayıt" yazan bir şerit, kullanıcıya kendi gözüne
    inanmamasını söyler. Bilinmiyorsa GÖRÜNEN satır sayısı yazılır.
    """
    data = meta if isinstance(meta, dict) else {}
    size = clean_per_page(data.get("per_page"), per_page)
    total = as_int(data.get("total"), -1)
    if total < 0:
        total = (page - 1) * size + rows
    last = as_int(data.get("last_page"), 0)
    if last < 1:
        last = max(1, -(-total // size)) if size else 1
    return {
        "page": max(1, as_int(data.get("page"), page)),
        "per_page": size,
        "total": total,
        "last_page": last,
    }


def image_rules() -> dict[str, Any]:
    """Panelin `imageField` kurallarını sunucudan okuması için künye.

    Panel bu değerleri KENDİ İÇİNDE tutmaz: sözleşme sınırı değiştiğinde
    ekranın yanlış cümle yazması ("en çok 5 MB" derken sunucunun 8 kabul
    etmesi) kullanıcıyı gönderemediği bir dosyayla baş başa bırakır.
    """
    return {
        "accept": list(IMAGE_MIMES),
        "max_bytes": IMAGE_MAX_BYTES,
        # Tek dosya: ürünün bir görseli var (`media` → `thumb`). Çoklu seçim
        # sunmak, ikincisinin nereye gittiği sorusunu doğururdu.
        "multiple": False,
    }


def filter_spec() -> dict[str, Any]:
    """Süzgeç şeridinin sözleşmesi — panel kutuları bundan çizer.

    YEREL OLARAK ÜRETİLİR: geçit düşse bile süzgeçler ve boş liste çizilebilir
    (K7). Sunucudan gelen tek şey satırlardır.
    """
    return {
        "sorts": [{"value": key, "label": SORT_LABELS[key]} for key in SORTS],
        "statuses": [{"value": key, "label": STATUS_LABELS[key]} for key in STATUS_FILTERS],
        "default_sort": DEFAULT_SORT,
        "default_direction": DEFAULT_DIRECTION,
        "default_status": DEFAULT_STATUS,
        "per_page_max": PER_PAGE_MAX,
        "name_max": NAME_MAX,
        "reason_min": MIN_REASON,
        "reason_max": MAX_REASON,
        "image": image_rules(),
    }
