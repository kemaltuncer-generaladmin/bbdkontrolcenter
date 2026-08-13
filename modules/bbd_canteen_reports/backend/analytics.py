"""Rapor hesapları — saf fonksiyonlar, ağ ve veritabanı yok.

Girdi her yerde kantinin işlem listesidir:
    {serverId, studentId, studentName, total, createdAt(ms), reversedAt, items:[…]}

İPTALLİ SATIŞ HİÇBİR HESABA GİRMEZ. Kantin `reversedAt` damgasını veriyor;
filtreleme tek noktada, `live()` içinde yapılır ki bir kırılımda unutulmasın.

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
