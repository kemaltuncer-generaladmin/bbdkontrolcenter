"""Pano rakamlarının saf hesabı — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Panonun tamamı toplulaştırmadır: ciro, ortalama sepet,
karşılaştırma yüzdesi, gün/saat kırılımı, durum dağılımı. Bunlar servise
gömülseydi tek satırı bile ağsız test edilemezdi; burada hepsi girdi→çıktı
fonksiyonudur.

RAKAMLAR NEREDEN GELİR. Pano KPI'ları `dashboard_stats` ucundan OKUNMAZ,
SİPARİŞ LİSTESİNDEN HESAPLANIR. Gerekçe:

  · Ekranda rakamla birlikte o rakamı üreten siparişler duruyor ("son
    siparişler", satır → Siparişler ekranı). İki ayrı kaynak birbirini
    tutmazsa kullanıcı hangisine güveneceğini bilemez ve ikisine de güvenmez.
  · Uzak ucun hangi tarih alanını (sipariş mi ödeme mi), hangi kanalı ve
    iptalleri nasıl saydığı belgelenmemiş. Burada kural açık ve testli.
  · Aynı veriden hem KPI hem grafik hem tablo çıkıyor; tek tarama yetiyor.

SAYMA KURALLARI (ekranda da yazar, sessiz varsayım yok):
  · İptal ve şüpheli sipariş CİROYA da SİPARİŞ SAYISINA da girmez; durum
    dağılımında ayrıca görünür.
  · İade tutarı ciradan DÜŞÜLMEZ; kendi KPI'sında durur. Düşmek, iadenin
    hangi dönemin satışına ait olduğu sorusunu sessizce cevaplamak olurdu.
  · Saat kırılımı mağazanın kendi saat damgasıdır; saat dilimi ÇEVRİLMEZ.
    Çevirmek, hangi dilimde olduğunu bilmediğimiz bir damgayı kaydırmaktır.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Ciroya ve sipariş sayısına girmeyen durumlar.
CANCELLED = ("canceled", "cancelled", "fraud")

#: Henüz kargolanmamış sayılan durumlar. Bagisto siparişi tamamen sevk
#: edilene kadar `processing` durumunda tutar; durum tek başına iyi bir
#: göstergedir, gönderi kaydı varsa o da ayrıca bakılır.
AWAITING_SHIPMENT = ("pending", "pending_payment", "processing")

#: Durum dağılımının sabit sırası — çubuk her yenilemede aynı yerde dursun.
STATUS_ORDER = ("pending", "pending_payment", "processing", "completed",
                "canceled", "closed", "fraud")

STATUS_LABELS = {
    "pending": "Bekliyor",
    "pending_payment": "Ödeme bekliyor",
    "processing": "Hazırlanıyor",
    "completed": "Tamamlandı",
    "canceled": "İptal",
    "cancelled": "İptal",
    "closed": "Kapatıldı",
    "fraud": "Şüpheli",
}

#: Aralık tavanı. Daha uzun aralık, sipariş taramasını ve karşılaştırma
#: taramasını birlikte hız sınırına dayandırır; ekran dakikalarca kilitlenir.
MAX_RANGE_DAYS = 400

COMPARE_MODES = ("previous", "lastYear", "none")


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü. `utcnow().date()` gece yarısından sonraki
    üç saatte bir gün geride kalır ve "bugün" boş görünür."""
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


def to_kurus(value: Any) -> int | None:
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar; günlük ciroda
    yüzlerce satır toplanınca sapma görünür hâle gelir.
    """
    if value is None:
        return None
    raw = str(value).strip().replace(" ", "")
    if raw == "":
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def reason_error(value: str) -> str:
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def pick(raw: Any, *names: str) -> Any:
    """Sipariş/iade kaydından ilk dolu alanı okur.

    Sipariş kaydı EAV değildir ama alan adları sürüme göre değişiyor
    (`grand_total` / `base_grand_total`). Çağıran tarafın hepsini bilmesi
    gerekmesin diye tek yerde toplanır.
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


# ==================================================================== aralık

def normalize_range(start: str, end: str, *, today: str = "") -> dict[str, Any]:
    """Aralığı doğrular ve gerekiyorsa düzeltir.

    Ters verilmiş aralık (bitiş < başlangıç) REDDEDİLMEZ, çevrilir: kullanıcı
    tarihleri yanlış sırada yazdıysa boş ekran göstermek yardımcı olmaz.
    """
    day = today or today_iso()
    first = text(start)[:10] or day
    last = text(end)[:10] or day
    note = ""
    if not _is_day(first) or not _is_day(last):
        return {"start": day, "end": day, "days": 1,
                "note": "Tarih okunamadı; bugün gösteriliyor."}
    if last < first:
        first, last = last, first
        note = "Tarih aralığı ters verilmişti; çevrildi."
    days = span_days(first, last)
    if days > MAX_RANGE_DAYS:
        first = (date.fromisoformat(last) - timedelta(days=MAX_RANGE_DAYS - 1)).isoformat()
        days = MAX_RANGE_DAYS
        note = f"Aralık {MAX_RANGE_DAYS} günle sınırlandı."
    return {"start": first, "end": last, "days": days, "note": note}


def _is_day(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def span_days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1


def previous_range(start: str, end: str, mode: str = "previous") -> dict[str, str]:
    """Karşılaştırma aralığı. `mode`: previous · lastYear · none.

    `lastYear` aynı takvim günlerini bir yıl geriye taşır; 29 Şubat 28'e
    düşürülür (`replace` orada `ValueError` atar ve delta hiç hesaplanamazdı).
    """
    if mode == "none":
        return {"start": "", "end": ""}
    first = date.fromisoformat(start)
    last = date.fromisoformat(end)
    if mode == "lastYear":
        return {"start": _year_back(first).isoformat(), "end": _year_back(last).isoformat()}
    length = (last - first).days + 1
    previous_end = first - timedelta(days=1)
    return {"start": (previous_end - timedelta(days=length - 1)).isoformat(),
            "end": previous_end.isoformat()}


def _year_back(value: date) -> date:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:
        return value.replace(year=value.year - 1, day=28)


def in_range(day: str, start: str, end: str) -> bool:
    """ISO gün karşılaştırması — metin sıralaması ISO'da takvim sırasıdır."""
    if not day:
        return False
    return (not start or day >= start) and (not end or day <= end)


def merge_ranges(current: dict[str, str],
                 previous: dict[str, str]) -> dict[str, Any] | None:
    """İki dönemi TEK sorguya indirebiliyorsak birleşik aralığı verir.

    NEDEN VAR. Pano dönem ve karşılaştırma dönemi için AYNI ucu iki kez
    çağırıyordu (sipariş, iade, müşteri → altı istek). Mağaza tarih süzgecini
    gerçekten uyguluyor (canlıda ölçüldü 2026-08-14: `date_from`/`date_to`
    verilen sorgu 18 yerine 0 satır döndürdü), yani iki sorgu ayrık iki küme
    getiriyor. Bitişik iki aralık tek sorguyla da alınabilir ve dönemlere
    AYIRMA işi zaten YERELDE yapılıyor (`in_range`) — böylece rakam değişmez,
    yalnız istek sayısı yarıya iner.

    NE ZAMAN BİRLEŞTİRİLMEZ — iki ayrı sebep, ikisi de tavanla ilgili:

    1. ARALIKLAR UZAKSA. `lastYear` kipinde iki aralık bir yıl uzaktır;
       birleşik sorgu aradaki 11 ayı da çeker, tarama tavanını (`scan_cap`)
       boşa yer ve "rakamlar eksik" uyarısını gereksiz yere açardı. Kural
       ölçülebilir: birleşik aralık, iki aralığın toplam gün sayısını
       AŞIYORSA birleştirilmez. Bitişik dönemlerde eşitlik sağlanır.

    2. BİRLEŞİK ARALIK ÇOK UZUNSA. Tavan satır sayısına göre işler; iki
       dönemi tek sorguya koymak, önce ayrı ayrı sığan satırları TEK tavanın
       altına toplar. Bitişik ama çok uzun aralıklarda bu, eskiden kesilmeyen
       bir taramayı keserdi. Sınır ekranın kendi aralık tavanıdır
       (`MAX_RANGE_DAYS`): panonun hazır çipleri (1–31 gün) her zaman
       birleşir, elle verilmiş çok uzun aralıklar eski davranışta kalır.

    Dönüş: `{"start", "end", "days"}` ya da birleştirilemiyorsa `None`.
    """
    first, last = text(current.get("start")), text(current.get("end"))
    other_first, other_last = text(previous.get("start")), text(previous.get("end"))
    if not (first and last and other_first and other_last):
        return None
    if not all(_is_day(value) for value in (first, last, other_first, other_last)):
        return None

    start = min(first, other_first)
    end = max(last, other_last)
    total = span_days(first, last) + span_days(other_first, other_last)
    merged = span_days(start, end)
    if merged > total or merged > MAX_RANGE_DAYS:
        return None
    return {"start": start, "end": end, "days": merged}


# =============================================================== sipariş satırı

def order_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Ham sipariş kaydı → panonun beklediği satır. Para her yerde kuruş."""
    created = text(pick(raw, "created_at", "createdAt"))
    status = text(pick(raw, "status")).lower()
    customer = text(pick(raw, "customer_full_name", "customerFullName"))
    if not customer:
        nested = raw.get("customer")
        if isinstance(nested, dict):
            customer = text(nested.get("name") or
                            f"{text(nested.get('first_name'))} {text(nested.get('last_name'))}")
        else:
            customer = f"{text(pick(raw, 'customer_first_name'))} " \
                       f"{text(pick(raw, 'customer_last_name'))}".strip()

    shipments = raw.get("shipments")
    shipped_qty = as_int(pick(raw, "total_qty_shipped", "totalQtyShipped"), 0)
    ordered_qty = as_int(pick(raw, "total_qty_ordered", "totalQtyOrdered"), 0)
    shipped = bool(isinstance(shipments, list) and shipments) or \
        (ordered_qty > 0 and shipped_qty >= ordered_qty)
    invoices = raw.get("invoices")

    payment = raw.get("payment")
    payment_label = text(payment.get("method_title") or payment.get("method")) \
        if isinstance(payment, dict) else text(pick(raw, "payment_title"))

    return {
        "id": as_int(raw.get("id")),
        "number": text(pick(raw, "increment_id", "incrementId")) or f"#{as_int(raw.get('id'))}",
        "createdAt": created[:19],
        "date": created[:10],
        "hour": as_int(created[11:13], -1),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, text(pick(raw, "status_label")) or status or "—"),
        "customer": customer.strip() or "—",
        "email": text(pick(raw, "customer_email", "customerEmail")),
        "channel": text(pick(raw, "channel_name", "channelName", "channel")),
        "paymentLabel": payment_label,
        "total": to_kurus(pick(raw, "grand_total", "base_grand_total", "grandTotal")) or 0,
        "itemCount": ordered_qty or len(raw.get("items") or []),
        "shipped": shipped,
        "invoiced": bool(isinstance(invoices, list) and invoices),
        "cancelled": status in CANCELLED,
        "items": item_rows(raw),
    }


def item_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Sipariş kalemleri. Liste ucu kalem taşımayabilir — o zaman boş döner
    ve "en çok satan" kartı kaynağının olmadığını SÖYLER, sıfır uydurmaz."""
    items = raw.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        out.append({
            "name": text(pick(item, "name", "product_name")) or "(adsız)",
            "sku": text(pick(item, "sku")),
            "qty": as_int(pick(item, "qty_ordered", "qtyOrdered", "qty"), 0),
            "total": to_kurus(pick(item, "total", "base_total", "price")) or 0,
        })
    return out


def refund_total(raw: dict[str, Any]) -> int:
    return to_kurus(pick(raw, "grand_total", "base_grand_total", "adjustment_refund")) or 0


def created_day(raw: dict[str, Any]) -> str:
    return text(pick(raw, "created_at", "createdAt"))[:10]


# ============================================================= toplulaştırma

def revenue(rows: list[dict[str, Any]]) -> int:
    """Ciro — iptal ve şüpheli hariç, kuruş."""
    return sum(row["total"] for row in rows if not row["cancelled"])


def counted(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ciroya ve sipariş sayısına giren siparişler."""
    return [row for row in rows if not row["cancelled"]]


def average_basket(rows: list[dict[str, Any]]) -> int:
    live = counted(rows)
    if not live:
        return 0
    return round(revenue(rows) / len(live))


def awaiting_shipment(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows
            if row["status"] in AWAITING_SHIPMENT and not row["shipped"]]


def pending_orders(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["status"] in ("pending", "pending_payment")]


def daily_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Gün → ciro. Boş günler BURADA doldurulmaz: ekran `fillDays()` ile
    aralığın tamamını doldurur, böylece veri olmayan gün 0 olarak çizilir."""
    totals: dict[str, int] = {}
    for row in counted(rows):
        if not row["date"]:
            continue
        totals[row["date"]] = totals.get(row["date"], 0) + row["total"]
    return [{"date": day, "value": totals[day]} for day in sorted(totals)]


def status_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Durum dağılımı — sabit sırada, sayılarıyla. Renk tek başına anlam
    taşımaz kuralı gereği her parçanın sayısı da taşınır."""
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"] or "—"] = counts.get(row["status"] or "—", 0) + 1
    known = [key for key in STATUS_ORDER if key in counts]
    extra = sorted(key for key in counts if key not in STATUS_ORDER)
    return [{"key": key, "label": STATUS_LABELS.get(key, key), "value": counts[key]}
            for key in [*known, *extra]]


def hour_counts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """24 saatlik şerit. Saati okunamayan sipariş HİÇBİR saate yazılmaz."""
    counts = dict.fromkeys(range(24), 0)
    for row in rows:
        if 0 <= row["hour"] <= 23:
            counts[row["hour"]] += 1
    return [{"hour": hour, "count": counts[hour]} for hour in range(24)]


def top_products(rows: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """En çok satan ürünler — sipariş KALEMLERİNDEN, adede göre.

    Anahtar SKU'dur; SKU yoksa ada düşülür. Aynı ürünün iki farklı yazımı
    tek satıra toplanmasın diye ad anahtarı küçültülmez: "Kalem" ile "kalem"
    ayrı satırlar olarak durur ve bu, veride bir sorun olduğunu gösterir.
    """
    totals: dict[str, dict[str, Any]] = {}
    for row in counted(rows):
        for item in row["items"]:
            key = item["sku"] or item["name"]
            slot = totals.setdefault(key, {"name": item["name"], "sku": item["sku"],
                                           "qty": 0, "total": 0})
            slot["qty"] += item["qty"]
            slot["total"] += item["total"]
    ordered = sorted(totals.values(), key=lambda item: (-item["qty"], -item["total"],
                                                        item["name"]))
    return ordered[:limit]


def report_products(payload: Any, limit: int = 10) -> list[dict[str, Any]]:
    """Mağazanın kendi ürün raporundan "en çok satan" satırları.

    Sipariş listesi kalem taşımadığında kullanılır. Alan adları belgelenmemiş
    olduğu için birden çok ad denenir; hiçbiri tutmazsa boş liste döner ve
    kart "kaynak yok" der — uydurma satır üretilmez.
    """
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        for key in ("products", "items", "data", "rows"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        qty = as_int(pick(item, "total_qty_ordered", "orders", "qty", "count"), 0)
        name = text(pick(item, "name", "product_name", "title"))
        if not name:
            continue
        out.append({"name": name, "sku": text(pick(item, "sku")), "qty": qty,
                    "total": to_kurus(pick(item, "revenue", "total", "grand_total")) or 0})
    out.sort(key=lambda item: (-item["qty"], -item["total"], item["name"]))
    return out[:limit]


# ================================================================== karşılaştırma

def delta(current: float, previous: float) -> dict[str, Any]:
    """İki dönem farkı → {percent, direction, delta}.

    Önceki dönem 0 iken yüzde TANIMSIZDIR ve `None` döner: "%∞ artış" bilgi
    değil gürültüdür; ekran o durumda yalnız yeni değeri gösterir.
    """
    now = float(current or 0)
    before = float(previous or 0)
    difference = now - before
    if before == 0:
        return {"percent": None, "direction": "up" if difference > 0 else "flat",
                "delta": difference}
    percent = round((difference / before) * 100, 1)
    return {"percent": percent,
            "direction": "up" if difference > 0 else "down" if difference < 0 else "flat",
            "delta": difference}


#: KPI kutularının sırası ve türü. `kind` ekranın biçimlendirmesini belirler:
#: `money` kuruş, `count` adet. Sunucu HAM SAYI döner, biçim ekranındır.
KPI_SPECS = (
    ("revenue", "Ciro", "money", True),
    ("orders", "Sipariş", "count", True),
    ("basket", "Ortalama sepet", "money", True),
    ("customers", "Yeni müşteri", "count", True),
    ("refunds", "İade tutarı", "money", True),
    ("pending", "Bekleyen sipariş", "count", False),
    ("unshipped", "Kargolanmayan", "count", False),
    ("outOfStock", "Tükenen ürün", "count", False),
)


def kpi_tiles(current: dict[str, Any], previous: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Sekiz KPI kutusu. Değeri `None` olan kutu "—" gösterir; 0 GÖSTERMEZ.

    "Bilinmiyor" ile "sıfır" farklı şeylerdir. Gerekçe burada bir dönem "uç
    henüz yayında değilse" diye yazılıydı; o cümle ARTIK GEÇERSİZ — BBD uçları
    2026-08-16 itibarıyla yayında. Kural yine de aynı ve `outOfStock` hâlâ
    boş: tükenen ürün sayısını veren bir uç YOK (katalog sağlığı ucu stok
    alanı taşımıyor — bkz. `service.OUT_OF_STOCK_NOTE`). Ayrıca uç okunamadığı
    ya da geri çekildiği her durumda değer bilinmezdir, sıfır değil.
    """
    tiles: list[dict[str, Any]] = []
    for key, label, kind, comparable in KPI_SPECS:
        value = current.get(key)
        tile: dict[str, Any] = {"key": key, "label": label, "kind": kind, "value": value,
                                "note": text(current.get(f"{key}Note"))}
        if comparable and previous is not None and value is not None:
            before = previous.get(key)
            if before is not None:
                tile["delta"] = delta(value, before)
                tile["previous"] = before
        tiles.append(tile)
    return tiles


def snapshot_numbers(rows: list[dict[str, Any]], *, refunds: int | None,
                     new_customers: int | None, out_of_stock: int | None) -> dict[str, Any]:
    """Bir dönemin sekiz rakamı. `None` = bilinmiyor (sıfır değil)."""
    live = counted(rows)
    return {
        "revenue": revenue(rows),
        "orders": len(live),
        "basket": average_basket(rows),
        "customers": new_customers,
        "refunds": refunds,
        "pending": len(pending_orders(rows)),
        "unshipped": len(awaiting_shipment(rows)),
        "outOfStock": out_of_stock,
        "cancelled": len(rows) - len(live),
    }


# ====================================================================== ayar

def config_value(values: Any, key: str) -> dict[str, Any]:
    """Ayarın MEVCUT değeri — bulunamazsa "yok" denir, sıfır uydurulmaz.

    Anahtar adı Bagisto sürümüne göre değişebiliyor. Bulunmayan anahtara
    yazmak `core_config` içinde hiçbir şeyi etkilemeyen yeni bir satır açar
    ve kullanıcı ayarı değiştirdiğini sanır — sessiz başarısızlık.
    """
    if not isinstance(values, dict):
        return {"key": key, "found": False, "value": None}
    if key in values:
        return {"key": key, "found": True, "value": values[key]}
    tail = key.rsplit(".", 1)[-1]
    for name, value in values.items():
        if str(name).rsplit(".", 1)[-1] == tail:
            return {"key": str(name), "found": True, "value": value}
    return {"key": key, "found": False, "value": None}


def newest_backup(items: Any) -> dict[str, Any] | None:
    """Yedek envanterinin en yenisi. Tarihi olmayan kayıt atlanır: "en yeni"
    diye tarihi bilinmeyen bir dosyayı göstermek yanlış güven verir."""
    best: dict[str, Any] | None = None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        created = text(pick(item, "created_at", "createdAt", "date"))
        if not created:
            continue
        row = {"name": text(pick(item, "name", "file", "filename")),
               "createdAt": created[:19],
               "bytes": as_int(pick(item, "bytes", "size"), 0),
               "scope": text(pick(item, "scope"))}
        if best is None or row["createdAt"] > best["createdAt"]:
            best = row
    return best


def backup_age_days(created: str, *, today: str = "") -> int | None:
    """Yedeğin kaç gün önce alındığı. Çözülemezse `None`."""
    day = text(created)[:10]
    if not _is_day(day):
        return None
    return (date.fromisoformat(today or today_iso()) - date.fromisoformat(day)).days
