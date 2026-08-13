"""Rapor hesapları — saf. Ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. 33 raporun tamamı aynı üç işi yapıyor: mağazadan gelen ham
kaydı tek biçime çevir, dönemle süz, topla. Bu servise gömülseydi tek bir
oranın doğruluğu ancak ağa çıkarak sınanabilirdi. Burada hepsi girdi→çıktı
fonksiyonudur.

YEDİ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur; yedisi de
`bbdstore.com.tr` üzerinde SALT OKUNARAK doğrulandı, varsayım değildir:

1. PARA ONDALIK GELİR. Bagisto `grandTotal: 2` (= ₺2,00) gönderir; içeride her
   şey KURUŞ (int). `to_kurus` tek dönüşüm noktasıdır ve DECIMAL kullanır —
   float toplamı muhasebeye sızmaz.
2. ALAN ADI camelCase'TİR. Uç `grandTotal` / `createdAt` / `totalItemCount`
   döndürür, belgeler `grand_total` der. `pick` adı ayraçsız-küçük harfe
   indirerek de arar; tek yazıma bağlanan kod hiçbir şey bulamaz ve İSTİSNA DA
   ATMAZ — ekran sessizce "—" dolu görünür.
3. BOŞ GÜN ATLANIR. Grafiğe yalnız veri olan günü koymak 3 günlük satışsız
   dönemi düz çizgi gibi gösterir. `fill_buckets` aralığın tamamını doldurur.
4. ÖNCEKİ DÖNEM SIFIRSA YÜZDE TANIMSIZ. `delta` o durumda `None` döner;
   "%∞ artış" yazmak bilgi değil gürültüdür.
5. LİSTEDEKİ KALEM SIĞDIR. Sipariş listesi `items` verir ama içinde ürün
   kimliği, fiyat ve KDV YOKTUR. `usable_items` sığ kalemi YOK sayar; yoksa
   bütün ürün ve KDV raporları sessizce sıfır ciro gösterirdi.
6. ADRES `addresses[]` DİZİSİNDEDİR (`addressType`), `shipping_address` diye
   bir alan yoktur; liste ucunda hiç adres yok, yalnız `location` özeti var.
   `address_of` + `location_city` ikisini de çözer.
7. MÜŞTERİ GRUBU `customer.group.name` ALTINDADIR ve liste ucunda hiç yoktur;
   `customer_group_of` üç biçimi de tanır, servis ayrıca müşteri listesinden
   eşleştirir.

BİR ŞEY UYDURULMAZ. Hesabın dayandığı veri eksikse rapor `notes` içinde
YÖNTEMİ yazar (ör. iade kalem kırılımı sipariş kaleminden pay verilerek
tahmin ediliyorsa). Sessizce yaklaşık rakam göstermek yanlış rakam
göstermektir.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

# ============================================================ temel çeviri

_STAMP = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?")

TR_MONTHS_SHORT = ("Oca", "Şub", "Mar", "Nis", "May", "Haz",
                   "Tem", "Ağu", "Eyl", "Eki", "Kas", "Ara")
TR_DAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")

#: Bagisto sipariş durumları → ekran dili. Bilinmeyen durum OLDUĞU GİBİ
#: gösterilir; İngilizce görünmesi, yanlış çevirmekten iyidir.
STATUS_LABELS = {
    "pending": "Bekliyor",
    "pending_payment": "Ödeme bekliyor",
    "processing": "Hazırlanıyor",
    "completed": "Tamamlandı",
    "canceled": "İptal",
    "cancelled": "İptal",
    "closed": "Kapandı",
    "fraud": "Şüpheli",
    "refunded": "İade edildi",
}

#: Ciroya sayılmayan durumlar. İptal ve şüpheli siparişi ciroya katmak
#: raporu yönetimin gördüğü tek yalan hâline getirir.
VOID_STATUSES = ("canceled", "cancelled", "closed", "fraud")


def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def to_kurus(value: Any) -> int:
    """Ondalık para → kuruş (int). Yuvarlama YARIYI YUKARI, banker's değil.

    DECIMAL, FLOAT DEĞİL. `float("1.005") * 100` ikili tabanda
    `100.49999999999999` eder; yarıyı yukarı yuvarlama o satırda 101 yerine
    100 üretirdi. Bir kuruşluk sapma tek satırda görünmez ama binlerce satır
    toplanan bir KDV icmalinde mutabakatı bozar — ve rapor ekranında sayının
    "yaklaşık" olması, sayının yanlış olmasıyla aynı şeydir.
    """
    if value in (None, "", False):
        return 0
    try:
        amount = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        return 0
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _folded_key(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def pick(raw: Any, *names: str, default: Any = None) -> Any:
    """İlk DOLU alanı döner; snake_case ve camelCase yazımların İKİSİNİ de tanır.

    TUZAK 2 — CANLIDA DOĞRULANDI: `/api/admin/orders` `grandTotal`,
    `createdAt`, `totalItemCount` döndürüyor; Bagisto'nun veritabanı sütunları
    ve belgeleri ise `grand_total`, `created_at` diyor. Tek yazıma bağlanan kod
    hiçbir şey bulamaz ve İSTİSNA DA ATMAZ — ekran sessizce "—" dolu görünür.
    Bu yüzden ad, ayraçları atılıp küçük harfe indirilerek de aranır; her
    okumada iki yazımı elle saymak zorunda kalmak, birini unutmayı kaçınılmaz
    kılıyordu.

    SIRA ADIN SIRASIDIR, yazımın değil: her ad önce birebir, hemen ardından
    indirgenmiş biçimiyle aranır. Önce bütün adları birebir taramak,
    `pick(item, "payment_title", "type")` çağrısında `type` alanını
    `paymentTitle`ın önüne geçirirdi.

    Boş metin DOLU sayılmaz: `customerName: ""` gelen satırda sıradaki ada
    bakılabilsin diye.
    """
    if not isinstance(raw, dict):
        return default
    folded: dict[str, Any] | None = None
    for name in names:
        value = raw.get(name)
        if value not in (None, ""):
            return value
        if folded is None:
            folded = {_folded_key(str(key)): item for key, item in raw.items()}
        value = folded.get(_folded_key(name))
        if value not in (None, ""):
            return value
    return default


def day_of(value: Any) -> str:
    match = _STAMP.search(text(value))
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}" if match else ""


def hour_of(value: Any) -> int:
    match = _STAMP.search(text(value))
    if not match or match.group(4) is None:
        return -1
    return as_int(match.group(4), -1)


def weekday_of(day: str) -> int:
    """Pazartesi = 0. Geçersiz gün için -1."""
    parts = day.split("-")
    if len(parts) != 3:
        return -1
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2])).weekday()
    except ValueError:
        return -1


def days_between(start: str, end: str) -> int:
    """İki ISO gün arasındaki gün sayısı (uçlar dahil). Bozuk aralıkta 0."""
    left, right = _as_date(start), _as_date(end)
    if left is None or right is None or right < left:
        return 0
    return (right - left).days + 1


def _as_date(value: str) -> date | None:
    parts = text(value).split("-")
    if len(parts) != 3:
        return None
    try:
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return None


def shift_range(start: str, end: str, mode: str) -> tuple[str, str]:
    """Karşılaştırma dönemi. `mode`: 'previous' | 'year'. Yoksa ('', '')."""
    left, right = _as_date(start), _as_date(end)
    if left is None or right is None or right < left:
        return "", ""
    if mode == "previous":
        span = (right - left).days + 1
        new_end = left - timedelta(days=1)
        return (new_end - timedelta(days=span - 1)).isoformat(), new_end.isoformat()
    if mode == "year":
        # 29 Şubat geçen yıl yoktur; `replace` hata verir, 365 gün geriye düşülür.
        try:
            return left.replace(year=left.year - 1).isoformat(), \
                right.replace(year=right.year - 1).isoformat()
        except ValueError:
            return (left - timedelta(days=365)).isoformat(), \
                (right - timedelta(days=365)).isoformat()
    return "", ""


# ================================================================ kırılım

def bucket(day: str, granularity: str) -> tuple[str, str]:
    """(sıralanabilir anahtar, ekran etiketi)."""
    moment = _as_date(day)
    if moment is None:
        return "", ""
    if granularity == "month":
        return f"{moment.year:04d}-{moment.month:02d}", \
            f"{TR_MONTHS_SHORT[moment.month - 1]} {moment.year}"
    if granularity == "week":
        year, week, _ = moment.isocalendar()
        return f"{year:04d}-W{week:02d}", f"{week}. hafta"
    return moment.isoformat(), f"{moment.day:02d}.{moment.month:02d}"


def fill_buckets(start: str, end: str, granularity: str) -> list[tuple[str, str]]:
    """Aralığın TAMAMI — veri olmayan kova da girer (tuzak 3)."""
    left, right = _as_date(start), _as_date(end)
    if left is None or right is None or right < left:
        return []
    seen: dict[str, str] = {}
    cursor = left
    # Tavan: bozuk aralık sonsuz döngü yapmasın (5 yıl ≈ 1826 gün).
    for _ in range(1900):
        if cursor > right:
            break
        key, label = bucket(cursor.isoformat(), granularity)
        seen.setdefault(key, label)
        cursor += timedelta(days=1)
    return sorted(seen.items())


def series(rows: Iterable[dict[str, Any]], *, start: str, end: str, granularity: str,
           value: str = "grandTotal") -> list[dict[str, Any]]:
    """Zaman serisi: [{key, label, value, count}] — boş kovalar dahil."""
    totals: dict[str, int] = {}
    counts: dict[str, int] = {}
    for row in rows:
        key, _ = bucket(row.get("day", ""), granularity)
        if not key:
            continue
        totals[key] = totals.get(key, 0) + as_int(row.get(value))
        counts[key] = counts.get(key, 0) + 1
    return [{"key": key, "label": label, "value": totals.get(key, 0),
             "count": counts.get(key, 0)}
            for key, label in fill_buckets(start, end, granularity)]


def breakdown(rows: Iterable[dict[str, Any]], key_of: Callable[[dict[str, Any]], str], *,
              value: str = "grandTotal", empty: str = "Belirtilmemiş") -> list[dict[str, Any]]:
    """Kırılım: [{label, count, total, share}] — toplama göre azalan."""
    totals: dict[str, int] = {}
    counts: dict[str, int] = {}
    for row in rows:
        label = text(key_of(row)) or empty
        totals[label] = totals.get(label, 0) + as_int(row.get(value))
        counts[label] = counts.get(label, 0) + 1
    grand = sum(totals.values())
    out = [{"label": label, "count": counts[label], "total": total,
            "share": round(total * 100 / grand, 1) if grand else 0.0}
           for label, total in totals.items()]
    out.sort(key=lambda item: (-item["total"], item["label"]))
    return out


def delta(current: float, previous: float) -> dict[str, Any] | None:
    """Yüzde farkı. Önceki dönem 0 ise TANIMSIZ (tuzak 4) — `None` döner."""
    before = float(previous or 0)
    if before == 0:
        return None
    change = (float(current or 0) - before) / before * 100
    return {"percent": round(change, 1)}


def abc(rows: list[dict[str, Any]], value: str = "total") -> list[dict[str, Any]]:
    """Pareto sınıflandırması: kümülatif %80'e kadar A, %95'e kadar B, gerisi C."""
    ordered = sorted(rows, key=lambda item: -as_int(item.get(value)))
    grand = sum(as_int(item.get(value)) for item in ordered)
    running = 0
    out: list[dict[str, Any]] = []
    for item in ordered:
        amount = as_int(item.get(value))
        running += amount
        share = round(amount * 100 / grand, 1) if grand else 0.0
        cumulative = round(running * 100 / grand, 1) if grand else 0.0
        klass = "A" if cumulative <= 80 else ("B" if cumulative <= 95 else "C")
        out.append({**item, "share": share, "cumulativeShare": cumulative, "abc": klass})
    return out


def rfm_segment(recency_days: int, frequency: int, monetary: int) -> str:
    """Basit RFM segmenti — eşikler iş kuralıdır, tabloda da yazılır.

    Üç ölçüt tek bir ada indirgeniyor; kullanıcı "neden şampiyon" diye
    sorabilsin diye tablo R/F/M sütunlarını AYRICA gösterir.
    """
    if frequency >= 3 and recency_days <= 60 and monetary >= 100_000:
        return "Şampiyon"
    if frequency >= 2 and recency_days <= 90:
        return "Sadık"
    if recency_days <= 30:
        return "Yeni"
    if recency_days <= 180:
        return "Riskli"
    return "Kayıp"


# ============================================================ ham → tek biçim

#: Bagisto'nun adres türü etiketleri (`addresses[].addressType`).
_ADDRESS_TYPES = ("order_shipping", "order_billing")


def address_of(raw: dict[str, Any]) -> dict[str, Any]:
    """Teslimat (yoksa fatura) adresi — İKİ gövde biçiminden de çıkarır.

    CANLIDA DOĞRULANDI: `GET /orders/{id}` adresleri `addresses:
    [{addressType: "order_billing" | "order_shipping", …}]` DİZİSİNDE veriyor;
    `shipping_address` diye bir alan YOK. Yalnız düz alana bakan kod şehri
    hiç bulamıyor ve şehir raporları sessizce "Şehirsiz" doluyordu.
    """
    for key in ("shipping_address", "billing_address"):
        flat = pick(raw, key)
        if isinstance(flat, dict):
            return flat
    items = pick(raw, "addresses", default=[]) or []
    if not isinstance(items, list):
        return {}
    for wanted in _ADDRESS_TYPES:
        for item in items:
            if isinstance(item, dict) and text(pick(item, "address_type")) == wanted:
                return item
    return {}


def location_city(value: Any) -> str:
    """Liste satırındaki `location` özetinin ŞEHİR parçası.

    CANLIDA DOĞRULANDI: sipariş LİSTESİ adres taşımıyor ama
    `"Selçuklu, 42, TR"` biçiminde bir `location` özeti veriyor; ilk parça
    şehirdir. Detay okunamayan satırda şehir buradan gelir — yoksa şehir
    raporu boş çıkardı.
    """
    return text(value).split(",")[0].strip()


def customer_group_of(raw: dict[str, Any]) -> str:
    """Müşteri grubu — düz alan ya da `customer.group.name`.

    CANLIDA DOĞRULANDI: sipariş DETAYI grubu `customer.group.name` altında
    veriyor, liste ucu hiç vermiyor. Yalnız `customer_group` diye bakan kod
    her siparişi "Grupsuz" gösterirdi.
    """
    nested = pick(raw, "customer_group")
    if isinstance(nested, dict) and text(pick(nested, "name")):
        return text(pick(nested, "name"))
    direct = text(pick(raw, "customer_group_name", default=""))
    if direct:
        return direct
    customer = pick(raw, "customer")
    if isinstance(customer, dict):
        group = pick(customer, "group", "customer_group")
        if isinstance(group, dict):
            return text(pick(group, "name"))
        if text(group):
            return text(group)
    return ""


def order_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Bagisto sipariş kaydı → rapor satırı. Para KURUŞ."""
    created = text(pick(raw, "created_at", default=""))
    day = day_of(created)
    address = address_of(raw)
    customer = " ".join(part for part in (
        text(pick(raw, "customer_first_name", default="")),
        text(pick(raw, "customer_last_name", default="")),
    ) if part).strip()
    # Liste ucu adı BİRLEŞİK `customerName` olarak veriyor; ad/soyad ayrı
    # alanları yalnız detayda var (ve orada `null` olabiliyor).
    customer = customer or text(pick(raw, "customer_name", default=""))
    status = text(pick(raw, "status", default=""))
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "number": text(pick(raw, "increment_id", "id", default="")),
        "createdAt": created,
        "day": day,
        "hour": hour_of(created),
        "weekday": weekday_of(day),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status or "—"),
        "void": status in VOID_STATUSES,
        "channel": text(pick(raw, "channel_name", "channel", default="")),
        "payment": text(pick(raw, "payment_title", "payment_method", default="")),
        "customerId": as_int(pick(raw, "customer_id", default=0)),
        "customerName": customer or text(pick(raw, "customer_email", default="")),
        "customerEmail": text(pick(raw, "customer_email", default="")).lower(),
        "customerGroup": customer_group_of(raw),
        "city": text(pick(address, "city", default="")) or location_city(pick(raw, "location")),
        "state": text(pick(address, "state", default="")),
        "grandTotal": to_kurus(pick(raw, "grand_total", default=0)),
        "subTotal": to_kurus(pick(raw, "sub_total", default=0)),
        "shipping": to_kurus(pick(raw, "shipping_amount", default=0)),
        "discount": to_kurus(pick(raw, "discount_amount", default=0)),
        "tax": to_kurus(pick(raw, "tax_amount", default=0)),
        "refunded": to_kurus(pick(raw, "grand_total_refunded", default=0)),
        "coupon": text(pick(raw, "coupon_code", default="")),
        "itemCount": as_int(pick(raw, "total_item_count", "total_qty_ordered", default=0)),
        # SIĞ SATIR: liste ucu para dökümünü (ara toplam, kargo, indirim, KDV)
        # hiç taşımıyor; yalnız `GET /orders/{id}` veriyor. "0" ile "bilinmiyor"
        # ayrımı kaybolursa rapor tahsil edilmiş kargoyu sıfır gösterir.
        "shallow": not has_money_detail(raw),
        "hasItems": bool(usable_items(raw)),
    }


def has_money_detail(raw: dict[str, Any]) -> bool:
    """Bu kayıt para dökümünü taşıyor mu (detay ucundan mı geldi)?"""
    return any(pick(raw, name) is not None
               for name in ("sub_total", "shipping_amount", "discount_amount", "tax_amount"))


def customer_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Müşteri kaydı → grup çözümlemesi için künye.

    Sipariş listesi müşteri GRUBUNU hiç taşımıyor; müşteri listesi taşıyor
    (`group.name`) ve tek sayfada iniyor. Grup kırılımını bu iki listeyi
    eşleştirerek üretmek, her siparişin detayını tek tek okumaktan çok daha
    ucuz — ve o okuma zaten tavanlı.
    """
    group = pick(raw, "group", "customer_group")
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "email": text(pick(raw, "email", default="")).lower(),
        "name": text(pick(raw, "name", default="")),
        "group": text(pick(group, "name", default="")) if isinstance(group, dict)
        else text(group),
    }


def usable_items(raw: Any) -> list[dict[str, Any]]:
    """Ciro hesabına GİREBİLECEK sipariş kalemleri.

    TUZAK — CANLIDA DOĞRULANDI, en sinsi olanı: `/api/admin/orders` LİSTESİ
    `items` alanını veriyor ama SIĞ veriyor —
    `{id, sku, name, qtyOrdered, productImage}`. Ürün kimliği, fiyat, tutar,
    KDV YOK. "Kalem var" diye kabul eden kod siparişin detayını hiç okumaz ve
    bütün ürün/KDV raporları sessizce SIFIR ciro gösterirdi; hata da atmazdı.

    Bu yüzden kalem, para dökümü ya da ürün kimliği taşıyorsa "kullanılabilir"
    sayılır; sığ kalem YOK sayılır ve servis siparişi detay ucundan okur.
    """
    items = pick(raw, "items", default=[]) or []
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if any(pick(item, name) is not None
               for name in ("product_id", "total", "base_total", "price")):
            out.append(item)
    return out


def line_rows(raw: dict[str, Any], order: dict[str, Any]) -> list[dict[str, Any]]:
    """Sipariş kalemleri → ürün satırları. Kullanılabilir kalem yoksa boş liste."""
    out: list[dict[str, Any]] = []
    for item in usable_items(raw):
        qty = as_int(pick(item, "qty_ordered", "quantity", "qty", default=0))
        out.append({
            "orderId": order["id"],
            "day": order["day"],
            "channel": order["channel"],
            "void": order["void"],
            "productId": as_int(pick(item, "product_id", default=0)),
            "sku": text(pick(item, "sku", default="")),
            "name": text(pick(item, "name", default="")),
            "qty": qty,
            "price": to_kurus(pick(item, "price", default=0)),
            "total": to_kurus(pick(item, "total", "base_total", default=0)),
            "discount": to_kurus(pick(item, "discount_amount", default=0)),
            "tax": to_kurus(pick(item, "tax_amount", default=0)),
            "taxPercent": round(as_float(pick(item, "tax_percent", default=0)), 2),
        })
    return out


def product_row(raw: dict[str, Any]) -> dict[str, Any]:
    # CANLIDA DOĞRULANDI: ürün LİSTESİ `categories` alanını `null` bırakıyor
    # ve kategoriyi düz `categoryName` olarak veriyor. Yalnız `categories`
    # dizisine bakan kod her ürünü "Kategorisiz" gösterir ve kategori süzgeci
    # sessizce hiçbir şey döndürmez.
    categories = pick(raw, "categories", default=[]) or []
    names: list[str] = []
    if isinstance(categories, list):
        for item in categories:
            if isinstance(item, dict):
                names.append(text(pick(item, "name", "label", default="")))
            else:
                names.append(text(item))
    if not [name for name in names if name]:
        names = [text(pick(raw, "category_name", default=""))]
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "sku": text(pick(raw, "sku", default="")),
        "name": text(pick(raw, "name", default="")),
        "type": text(pick(raw, "type", default="")),
        "status": as_int(pick(raw, "status", default=0)),
        "price": to_kurus(pick(raw, "price", default=0)),
        "cost": to_kurus(pick(raw, "cost", default=0)),
        "stock": as_int(pick(raw, "quantity", "qty", "total_quantity", default=0)),
        "categories": [name for name in names if name],
    }


def category_names(payload: Any) -> list[str]:
    """Kategori AĞACINI düzleştirir — süzgeç açılırının kaynağı.

    CANLIDA DOĞRULANDI: `/api/admin/catalog/categories/tree` İÇ İÇE geliyor
    (`children[]`) ve tepede tek bir "Kök" düğümü var. Yalnız üst düzeye bakan
    kod açılıra tek bir "Kök" koyuyordu; kullanıcı gerçek kategorilerin
    hiçbirini seçemiyordu. Kök düğümü listeye girmez: ürün künyesinde o adla
    bir kategori yok, seçilse boş rapor verirdi.
    """
    items = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        return []
    # Kök YALNIZCA tek düğümlü, çocuklu bir tepe varsa atlanır. Uç ileride düz
    # liste dönerse koşulsuz atlama bütün kategorileri yok ederdi.
    skip_top = (len(items) == 1 and isinstance(items[0], dict)
                and bool(pick(items[0], "children", default=[])))
    out: list[str] = []

    def walk(nodes: Any, *, skip: bool) -> None:
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = text(pick(node, "name", "label", default=""))
            if name and not skip:
                out.append(name)
            walk(pick(node, "children", default=[]), skip=False)

    walk(items, skip=skip_top)
    return sorted(set(out))


def shipment_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", "order_date", default=""))
    delivered = text(pick(raw, "delivered_at", default=""))
    day = day_of(created)
    span = days_between(day, day_of(delivered)) if delivered else 0
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "orderId": as_int(pick(raw, "order_id", default=0)),
        "day": day,
        "carrier": text(pick(raw, "carrier", "carrier_title", "provider", default="")),
        "status": text(pick(raw, "status", default="")),
        "tracking": text(pick(raw, "track_number", "tracking", default="")),
        "cost": to_kurus(pick(raw, "cost", "price", "amount", default=0)),
        "city": text(pick(raw, "city", default="")) or text(
            pick(address_of(raw), "city", default="")),
        # Teslim süresi gün cinsindendir ve UÇLAR DAHİL sayılır: aynı gün
        # teslim 1 gün görünür, 0 değil — "0 gün" tabloda hata gibi okunuyordu.
        "deliveryDays": max(0, span - 1) if span else 0,
        "delivered": bool(delivered),
    }


def invoice_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", default=""))
    return {
        "id": as_int(pick(raw, "id", default=0)),
        # CANLIDA: fatura listesi sayısal `orderId` vermiyor, siparişin
        # NUMARASINI (`orderIncrementId`) veriyor. İkisi de yoksa tabloda "—".
        "orderId": as_int(pick(raw, "order_id", "order_increment_id", default=0)),
        "number": text(pick(raw, "increment_id", "id", default="")),
        "day": day_of(created),
        "state": text(pick(raw, "state", "status", default="")),
        "grandTotal": to_kurus(pick(raw, "grand_total", default=0)),
        "tax": to_kurus(pick(raw, "tax_amount", default=0)),
        "subTotal": to_kurus(pick(raw, "sub_total", default=0)),
    }


def refund_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", default=""))
    items = pick(raw, "items", default=[]) or []
    skus: list[str] = []
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                sku = text(pick(item, "sku", default=""))
                if sku:
                    skus.append(sku)
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "orderId": as_int(pick(raw, "order_id", "order_increment_id", default=0)),
        "day": day_of(created),
        "grandTotal": to_kurus(pick(raw, "grand_total", default=0)),
        "skus": skus,
    }


def transaction_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", default=""))
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "orderId": as_int(pick(raw, "order_id", default=0)),
        "day": day_of(created),
        # `payment_title` ÖNCE gelir: `type` canlıda "kuveytturk" gibi teknik
        # bir kod taşıyor ve tabloda okunaksızdı.
        "method": text(pick(raw, "payment_title", "payment_method", "type", default="")),
        "status": text(pick(raw, "status", default="")),
        "grandTotal": to_kurus(pick(raw, "amount", "total", default=0)),
    }


def audit_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", "logged_at", default=""))
    return {
        "day": day_of(created),
        "user": text(pick(raw, "user", "user_name", "admin", "actor", default="")) or "—",
        "action": text(pick(raw, "action", "event", "method", default="")) or "—",
        "entity": text(pick(raw, "entity", "resource", "model", default="")) or "—",
        "result": text(pick(raw, "result", "status", default="")) or "—",
        "grandTotal": 0,
    }


def backup_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", default=""))
    scope = pick(raw, "scope", default=[]) or []
    return {
        "name": text(pick(raw, "name", "file", default="")),
        "day": day_of(created),
        "bytes": as_int(pick(raw, "size", "bytes", default=0)),
        "scope": ", ".join(text(item) for item in scope) if isinstance(scope, list)
        else text(scope),
        "verified": bool(pick(raw, "verified", "checksum_ok", default=False)),
        "grandTotal": 0,
    }


def notification_row(raw: dict[str, Any]) -> dict[str, Any]:
    created = text(pick(raw, "created_at", "sent_at", default=""))
    return {
        "day": day_of(created),
        "channel": text(pick(raw, "channel", "type", default="")) or "—",
        "template": text(pick(raw, "template", "template_name", default="")) or "—",
        "status": text(pick(raw, "status", "state", default="")) or "—",
        "grandTotal": to_kurus(pick(raw, "cost", default=0)),
    }


def trial_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_int(pick(raw, "id", default=0)),
        "customerId": as_int(pick(raw, "customer_id", default=0)),
        "email": text(pick(raw, "email", default="")).lower(),
        "day": day_of(pick(raw, "created_at", default="")),
        "grandTotal": 0,
    }


# ================================================================= süzme

def within(rows: Iterable[dict[str, Any]], start: str, end: str) -> list[dict[str, Any]]:
    """Gün alanına göre aralık süzgeci. Günü olmayan satır DIŞARIDA kalır."""
    return [row for row in rows if row.get("day") and start <= row["day"] <= end]


def live(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """İptal/şüpheli siparişler ciro hesabından çıkar."""
    return [row for row in rows if not row.get("void")]
