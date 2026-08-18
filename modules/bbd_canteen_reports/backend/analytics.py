"""Rapor hesapları — saf fonksiyonlar, ağ ve veritabanı yok.

Girdi her yerde kantinin işlem listesidir:
    {serverId, studentId, studentName, total, createdAt(ms), reversedAt, items:[…]}

İPTALLİ SATIŞ HİÇBİR **HESABA** GİRMEZ. Kantin `reversedAt` damgasını veriyor;
filtreleme tek noktada, `live()` içinde yapılır ki bir kırılımda unutulmasın.

AMA DÖKÜM HESAP DEĞİLDİR. `overview` / `by_student` / `by_product` / `by_class`
ciro üretir ve `live()` üzerinden gider — iptal edilmiş bir satışı ciroya
yazmak rakamı şişirir. `ledger()` ise dönemde NE OLDUĞUNU anlatan ham listedir:
satış da, iptal de, tahsilat da içindedir; hiçbir satır düşmez, her satır
`kind` damgası taşır. İki işi karıştırmak, ya ciroyu bozar ya kullanıcının
aradığı hareketi görünmez kılar.

Tutarlar kuruş (tam sayı) kalır — bölme yalnız sunumda yapılır; ara toplamda
kayan noktaya geçmek kuruş kaybettirir.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any


def local(ms: Any) -> datetime:
    """Epoch-ms → MAKİNENİN YEREL saati.

    Kantin `Europe/Istanbul` ile çalışır ve bu uygulama da aynı makinede
    aynı saat diliminde koşar. Gün ve saat kırılımları kullanıcının gördüğü
    duvar saatine göre olmalı — UTC'de hesaplamak akşam satışlarını ertesi
    güne taşır.
    """
    return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).astimezone()


def live(transactions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Yalnız geçerli satışlar — iptal edilenler her hesabın dışında kalır."""
    return [item for item in transactions if not item.get("reversedAt")]


# --------------------------------------------------------------- işlem dökümü

#: Döküm satırının türü. Ekran, PDF ve testler AYNI damgayı okur; dizgiyi üç
#: yere ayrı ayrı yazmak, birini değiştirip diğerini unutmaya davettir.
ENTRY_SALE = "sale"
ENTRY_REVERSED = "reversed"
ENTRY_COLLECTION = "collection"

#: Tahsilat ucunun satır listesini taşıyabileceği alan adları. Kantin
#: `GET /api/reports/collections` yanıtında listeyi hangi adla verirse versin
#: döküm çalışsın diye birkaç ad denenir — eksik tahsilat, sessizce eksik
#: döküm demektir.
_COLLECTION_LIST_KEYS = ("entries", "items", "collections", "rows", "payments")


def _first(row: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    """Birden çok olası alan adından ilk DOLU olanı verir."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def collection_entries(payload: Any, *,
                       student_id: str | None = None) -> list[dict[str, Any]]:
    """Tahsilat ucundan gelen CREDIT satırlarını döküm satırına çevirir.

    TAHSİLAT DA BİR İŞLEMDİR. Kantinde satış `transactions`, tahsilat ise cari
    defterin CREDIT satırıdır ve ayrı uçtan gelir; ikisini birleştirmeden
    "öğrencinin tüm işlemleri" gösterilemez — veli "borcumu şu gün ödedim"
    dediğinde dökümde karşılığı çıkmıyordu.

    Tutar POZİTİF kalır: bu bir alacak kaydıdır, satışla toplanmaz. Ekran ve
    PDF satırı `kind` damgasından ayırır.
    """
    if isinstance(payload, list):
        raw: list[Any] = payload
    elif isinstance(payload, dict):
        raw = next(
            (list(payload[key]) for key in _COLLECTION_LIST_KEYS
             if isinstance(payload.get(key), list)),
            [],
        )
    else:
        raw = []

    rows: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        owner = _first(item, ("studentId", "student_id", "opaqueId"), "")
        # Uç `studentId` ile süzülerek çağrılıyor ama süzgeci yok sayan bir
        # sürüme karşı burada da bakılır: başkasının tahsilatı öğrenci
        # dökümüne SIZAMAZ.
        if student_id is not None and owner and str(owner) != str(student_id):
            continue
        rows.append({
            "kind": ENTRY_COLLECTION,
            "studentId": str(owner or (student_id or "")),
            "studentName": _first(item, ("studentName", "student_name"), ""),
            "createdAt": int(_first(item, ("createdAt", "created_at", "at", "date"), 0) or 0),
            "total": int(_first(item, ("amount", "total", "value", "credit"), 0) or 0),
            "method": str(_first(item, ("method", "source", "type", "channel"), "") or ""),
            "reference": str(_first(item, ("reference", "localId", "id", "serverId"), "") or ""),
            "items": [],
        })
    return rows


def ledger(transactions: list[dict[str, Any]],
           collections: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """İşlem dökümü — dönemin TAMAMI, yeniden eskiye.

    HAM LİSTEDİR, HESAP DEĞİLDİR. `live()` burada uygulanmaz: iptal edilen
    satış da o gün yaşanmış bir olaydır ve kullanıcı dökümde tam olarak onu
    arar. Satır düşürmek yerine `kind` damgası konur; ciro hesapları
    (`overview`, `by_student`, `by_product`) yine `live()` üzerinden gider,
    yani iptal hiçbir toplama yazılmaz.
    """
    rows: list[dict[str, Any]] = [
        {**item, "kind": ENTRY_REVERSED if item.get("reversedAt") else ENTRY_SALE}
        for item in transactions
    ]
    rows.extend(collections or [])
    return sorted(rows, key=lambda item: int(item.get("createdAt") or 0), reverse=True)


def entry_counts(entries: list[dict[str, Any]]) -> dict[str, int]:
    """Dökümdeki satır türlerinin sayımı — başlıkta 'kaç satış, kaç iptal'."""
    counts = {ENTRY_SALE: 0, ENTRY_REVERSED: 0, ENTRY_COLLECTION: 0}
    for row in entries:
        key = str(row.get("kind") or ENTRY_SALE)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _hour(ms: Any) -> int:
    try:
        return local(ms).hour
    except (TypeError, ValueError, OSError):
        return -1


def _day(ms: Any) -> str:
    try:
        return local(ms).date().isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def overview(transactions: list[dict[str, Any]]) -> dict[str, Any]:
    """Üst şerit rakamları + günlük seri + saat dağılımı."""
    rows = live(transactions)
    total = sum(int(item.get("total") or 0) for item in rows)
    students = {str(item.get("studentId")) for item in rows if item.get("studentId")}

    by_day: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "count": 0})
    by_hour: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "count": 0})
    units = 0

    for item in rows:
        day = _day(item.get("createdAt"))
        if day:
            by_day[day]["total"] += int(item.get("total") or 0)
            by_day[day]["count"] += 1
        hour = _hour(item.get("createdAt"))
        if hour >= 0:
            by_hour[hour]["total"] += int(item.get("total") or 0)
            by_hour[hour]["count"] += 1
        units += sum(int(line.get("qty") or 0) for line in item.get("items") or [])

    reversed_rows = [item for item in transactions if item.get("reversedAt")]

    return {
        "total": total,
        "count": len(rows),
        "students": len(students),
        "units": units,
        # Ortalama sepet: işlem yoksa sıfıra bölme yok.
        "average": total // len(rows) if rows else 0,
        "perStudent": total // len(students) if students else 0,
        "reversedCount": len(reversed_rows),
        "reversedTotal": sum(int(item.get("total") or 0) for item in reversed_rows),
        "byDay": [
            {"day": day, **values}
            for day, values in sorted(by_day.items())
        ],
        "byHour": [
            {"hour": hour, **by_hour[hour]} if hour in by_hour else
            {"hour": hour, "total": 0, "count": 0}
            for hour in range(24)
        ],
    }


def by_student(transactions: list[dict[str, Any]], *,
               balances: dict[str, int] | None = None,
               classes: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Öğrenci kırılımı — işlem, adet, tutar, ortalama, son işlem, favori ürün."""
    balances = balances or {}
    classes = classes or {}
    bucket: dict[str, dict[str, Any]] = {}

    for item in live(transactions):
        key = str(item.get("studentId") or "")
        if not key:
            continue
        row = bucket.setdefault(key, {
            "studentId": key,
            "name": item.get("studentName") or "—",
            "className": classes.get(key, ""),
            "total": 0, "count": 0, "units": 0, "last": 0,
            "products": defaultdict(int),
        })
        row["total"] += int(item.get("total") or 0)
        row["count"] += 1
        row["last"] = max(row["last"], int(item.get("createdAt") or 0))
        for line in item.get("items") or []:
            quantity = int(line.get("qty") or 0)
            row["units"] += quantity
            row["products"][str(line.get("name") or "—")] += quantity

    result = []
    for row in bucket.values():
        favourite = max(row["products"].items(), key=lambda pair: pair[1], default=("—", 0))
        result.append({
            **{key: value for key, value in row.items() if key != "products"},
            "average": row["total"] // row["count"] if row["count"] else 0,
            "balance": int(balances.get(row["studentId"], 0)),
            "favourite": favourite[0],
            "favouriteQty": favourite[1],
        })
    return sorted(result, key=lambda row: row["total"], reverse=True)


def by_product(transactions: list[dict[str, Any]], *,
               products: dict[int, dict[str, Any]] | None = None,
               days: int = 1) -> list[dict[str, Any]]:
    """Ürün kırılımı + ABC (Pareto) sınıfı + stok tükenme tahmini."""
    products = products or {}
    bucket: dict[str, dict[str, Any]] = {}

    for item in live(transactions):
        student = str(item.get("studentId") or "")
        for line in item.get("items") or []:
            # productId kantinde dizgi gelebiliyor; anahtar olarak dizgi tutulur.
            key = str(line.get("productId") or f"ad:{line.get('name')}")
            row = bucket.setdefault(key, {
                "productId": key,
                "name": line.get("name") or "—",
                "qty": 0, "total": 0, "count": 0,
                "buyers": set(),
            })
            row["qty"] += int(line.get("qty") or 0)
            row["total"] += int(line.get("lineTotal") or 0)
            row["count"] += 1
            if student:
                row["buyers"].add(student)

    rows = sorted(bucket.values(), key=lambda row: row["total"], reverse=True)
    grand = sum(row["total"] for row in rows) or 1
    span = max(1, days)

    result = []
    cumulative = 0
    for row in rows:
        cumulative += row["total"]
        share = row["total"] * 100 / grand
        cumulative_share = cumulative * 100 / grand
        # ABC: cironun ilk %80'i A, sonraki %15'i B, kalanı C.
        abc = "A" if cumulative_share <= 80 else ("B" if cumulative_share <= 95 else "C")

        product = products.get(int(row["productId"])) if row["productId"].isdigit() else None
        stock = int(product.get("stock") or 0) if product else None
        per_day = row["qty"] / span
        days_left = round(stock / per_day, 1) if stock is not None and per_day > 0 else None

        result.append({
            "productId": row["productId"],
            "name": row["name"],
            "qty": row["qty"],
            "total": row["total"],
            "count": row["count"],
            "buyers": len(row["buyers"]),
            "share": round(share, 2),
            "cumulativeShare": round(cumulative_share, 2),
            "abc": abc,
            "stock": stock,
            "perDay": round(per_day, 2),
            "daysLeft": days_left,
            "isActive": bool(product.get("isActive")) if product else None,
        })
    return result


def dead_stock(product_rows: list[dict[str, Any]],
               products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dönemde hiç satılmamış ama stokta duran aktif ürünler — bağlanmış para."""
    sold = {row["productId"] for row in product_rows}
    return sorted(
        (
            {
                "productId": str(product["id"]),
                "name": product["name"],
                "stock": int(product.get("stock") or 0),
                "price": int(product.get("price") or 0),
                "value": int(product.get("stock") or 0) * int(product.get("price") or 0),
            }
            for product in products
            if product.get("isActive")
            and int(product.get("stock") or 0) > 0
            and str(product["id"]) not in sold
        ),
        key=lambda row: row["value"], reverse=True,
    )


def by_class(student_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sınıf kırılımı. Sınıf bilgisi yoksa tek grup ('—') olur."""
    bucket: dict[str, dict[str, Any]] = {}
    for row in student_rows:
        key = row.get("className") or "—"
        group = bucket.setdefault(key, {
            "className": key, "total": 0, "count": 0, "units": 0, "students": 0,
        })
        group["total"] += row["total"]
        group["count"] += row["count"]
        group["units"] += row["units"]
        group["students"] += 1

    return sorted(
        (
            {**group, "perStudent": group["total"] // group["students"] if group["students"] else 0}
            for group in bucket.values()
        ),
        key=lambda row: row["total"], reverse=True,
    )


def compare(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    """İki dönemin karşılaştırması. Önceki sıfırsa yüzde YOKTUR (None) — 'sonsuz artış'
    yazmak yerine oranın tanımsız olduğunu söylemek doğrudur."""
    def delta(key: str) -> dict[str, Any]:
        now, before = int(current.get(key) or 0), int(previous.get(key) or 0)
        return {
            "current": now,
            "previous": before,
            "diff": now - before,
            "percent": round((now - before) * 100 / before, 1) if before else None,
        }

    return {key: delta(key) for key in ("total", "count", "students", "units", "average")}
