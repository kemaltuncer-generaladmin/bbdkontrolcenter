"""Kargo performansının saf hesabı — ağa çıkmaz, testin hedefi.

NEDEN AYRI DOSYA. "Ortalama teslim süresi" cümlesi kolay, hesabı değil: hangi
gönderi sayılır, tarihi okunamayan ne olur, iptal edilen ortalamayı bozar mı,
teslim edilemeyen oranın paydası nedir. Bu kararların hepsi buradadır ve
tek tek test edilir; serviste olsalardı ancak ağa çıkarak sınanabilirlerdi.

ÜÇ KARAR:

 1. ORTALAMA TESLİM SÜRESİ yalnız TESLİM EDİLMİŞ ve iki tarihi de okunabilen
    gönderilerden hesaplanır. Yolda olanı 'şu ana kadar' süresiyle katmak
    ortalamayı her gün aşağı çeker ve rakam anlamsızlaşır.
 2. TESLİM EDİLEMEYEN ORANI'nın paydası SONUÇLANMIŞ gönderilerdir (teslim +
    teslim edilemedi + iade). Yolda olanı paydaya koymak, yoğun bir günün
    ardından oranı kendiliğinden düşürürdü.
 3. KÂR/ZARAR mağazanın müşteriden TAHSİL ETTİĞİ kargo bedeli ile taşıyıcıya
    ÖDENEN bedelin farkıdır. Alıcı ödemeli gönderide mağaza ne öder ne tahsil
    eder; o satır ikisine de girmez, ayrı sayılır.
"""

from __future__ import annotations

from typing import Any

from . import shipping

#: Gecikme dağılımının kovaları (gün). Son kova açık uçludur.
DELAY_BUCKETS = ((0, 1, "0–1 gün"), (2, 3, "2–3 gün"), (4, 7, "4–7 gün"), (8, 0, "8+ gün"))

SETTLED = ("delivered", "undelivered", "returned")


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def carrier_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Taşıyıcı bazlı özet. Satır sayısı çok olan üstte.

    `avgDays` yalnız teslim edilmiş gönderilerden gelir (KARAR 1); hiç teslim
    edilmemiş taşıyıcıda `None`'dır ve ekran `—` gösterir. 0 yazmak "aynı gün
    teslim" gibi okunurdu.
    """
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        code = row.get("carrier") or "?"
        bucket = buckets.setdefault(code, {
            "carrier": code, "label": shipping.carrier_label(code), "count": 0,
            "delivered": 0, "undelivered": 0, "returned": 0, "inTransit": 0,
            "cancelled": 0, "days": [], "cost": 0, "collected": 0, "receiverPaid": 0,
        })
        bucket["count"] += 1
        status = row.get("status")
        if status == "delivered":
            bucket["delivered"] += 1
            days = row.get("deliveryDays")
            if days is not None and days >= 0:
                bucket["days"].append(float(days))
        elif status == "undelivered":
            bucket["undelivered"] += 1
        elif status == "returned":
            bucket["returned"] += 1
        elif status == "cancelled":
            bucket["cancelled"] += 1
        else:
            bucket["inTransit"] += 1

        if row.get("payer") == "receiver":
            bucket["receiverPaid"] += 1
        else:
            bucket["cost"] += int(row.get("fee") or 0)
            bucket["collected"] += int(row.get("collectedFee") or 0)

    out: list[dict[str, Any]] = []
    for bucket in buckets.values():
        days = bucket.pop("days")
        settled = bucket["delivered"] + bucket["undelivered"] + bucket["returned"]
        bucket["avgDays"] = round(sum(days) / len(days), 1) if days else None
        bucket["medianDays"] = _median(days)
        bucket["settled"] = settled
        bucket["failRate"] = (round(bucket["undelivered"] * 100 / settled, 1)
                              if settled else None)
        bucket["margin"] = bucket["collected"] - bucket["cost"]
        out.append(bucket)
    out.sort(key=lambda item: item["count"], reverse=True)
    return out


def delay_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Teslim süresi dağılımı — kaç gönderi kaç günde teslim edildi."""
    counts = {label: 0 for _, _, label in DELAY_BUCKETS}
    for row in rows or []:
        days = row.get("deliveryDays")
        if row.get("status") != "delivered" or days is None or days < 0:
            continue
        for low, high, label in DELAY_BUCKETS:
            if days >= low and (not high or days <= high):
                counts[label] += 1
                break
    return [{"label": label, "count": counts[label]} for _, _, label in DELAY_BUCKETS]


def money_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Kargo maliyeti vs tahsil edilen (KARAR 3).

    `collectedFee` müşterinin siparişte ödediği kargo bedelidir; satırda yoksa
    0 sayılır ve `missingCollected` sayacı artar — ekran "tahsilat bilgisi
    olmayan N gönderi" der ve kâr/zarar rakamını mutlak doğru gibi sunmaz.
    """
    cost = 0
    collected = 0
    receiver = 0
    missing = 0
    cod_open = 0
    for row in rows or []:
        if row.get("payer") == "receiver":
            receiver += 1
        else:
            cost += int(row.get("fee") or 0)
            if row.get("collectedFee") is None:
                missing += 1
            collected += int(row.get("collectedFee") or 0)
        if "cod" in (row.get("flags") or []):
            cod_open += int(row.get("cod") or 0)
    return {
        "cost": cost, "collected": collected, "margin": collected - cost,
        "receiverPaid": receiver, "missingCollected": missing,
        "codOutstanding": cod_open, "count": len(rows or []),
    }


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI şeridinin sayıları. Hepsi tek geçişte."""
    counts = {"total": len(rows or []), "delivered": 0, "inTransit": 0, "late": 0,
              "undelivered": 0, "returning": 0, "cod": 0, "address": 0}
    for row in rows or []:
        status = row.get("status")
        marks = row.get("flags") or []
        if status == "delivered":
            counts["delivered"] += 1
        elif status in ("returning", "returned"):
            counts["returning"] += 1
        elif status == "undelivered":
            counts["undelivered"] += 1
        elif status != "cancelled":
            counts["inTransit"] += 1
        if "late" in marks:
            counts["late"] += 1
        if "cod" in marks:
            counts["cod"] += 1
        if "address" in marks:
            counts["address"] += 1
    counts["deliveredRate"] = (round(counts["delivered"] * 100 / counts["total"], 1)
                               if counts["total"] else None)
    return counts


def late_rows(rows: list[dict[str, Any]], *, limit: int = 25) -> list[dict[str, Any]]:
    """En uzun süredir hareketsiz gönderiler — "Dikkat gerektirenler" listesi."""
    late = [row for row in rows or [] if "late" in (row.get("flags") or [])]
    late.sort(key=lambda row: row.get("idleDays") or 0, reverse=True)
    return late[:max(1, limit)]
