"""Promosyon hesapları — saf mantık: simülasyon, kupon önizlemesi, performans.

Bu dosya ağa çıkmaz, durum tutmaz ve `store.api` görmez. Üç işi vardır:

1. SİMÜLASYON. "Şu sepet gelirse ne kadar indirim düşer?" Sorunun cevabı
   ekranın en çok işe yarayan kısmı: kural yazan personel kaydetmeden önce
   sonucu görür. Motor MAĞAZANIN MOTORU DEĞİLDİR ve öyleymiş gibi de
   sunulmaz — ekran "bu bizim okumamız, kesin tutar ödeme adımında oluşur"
   der. Yine de kuralın yanlış yazıldığını (koşul hiç tutmuyor, indirim
   sepetten büyük, iki kural çakışıyor) yazmadan önce gösterir.

2. KUPON KODU ÖNİZLEMESİ. Gerçek kodları MAĞAZA üretir (benzersizliği o
   garanti eder). Buradaki üreteç yalnız "kodlar neye benzeyecek" sorusunu
   yanıtlar; ürettiği kod hiçbir yere yazılmaz.

3. PERFORMANS. Bagisto'da "kampanya cirosu" ucu yok; kupon kodu SİPARİŞ
   satırında durur. Toplulaştırma burada, sipariş listesi üzerinden yapılır.

Para her yerde KURUŞ (int). Yüzde hesabı `Decimal` ile yapılır: `int(base *
0.1)` bazı tutarlarda bir kuruş aşağı yuvarlar ve toplamda gözle görülür
sapma üretir.
"""

from __future__ import annotations

import random
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from .promotions import as_float, as_int, day, pick, text, to_kurus, today_iso

#: Kupon gövdesinde KULLANILMAYAN harfler. `0/O`, `1/I/L` telefonda okunurken
#: ve elle yazılırken karışıyor; kampanya kodunun yanlış yazılması müşteriyi
#: "kod geçersiz" ekranına düşürüyor.
UNSAFE = "0O1IL"
ALPHABETS = {
    "alphanumeric": "".join(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" if c not in UNSAFE),
    "alphabetical": "".join(c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in UNSAFE),
    "numeric": "23456789",
}

#: Ciroya SAYILMAYAN sipariş durumları. İptal edilmiş siparişin kuponunu
#: "işe yaradı" saymak, kampanyayı olduğundan başarılı gösterir.
DEAD_STATUSES = ("canceled", "cancelled", "closed", "fraud")

ITEM_CONDITIONS = ("product", "category")


# ================================================================ simülasyon

def _pct(base: int, percent: float) -> int:
    """Yüzde indirimi kuruş olarak, yarım yukarı yuvarlayarak."""
    value = Decimal(int(base)) * Decimal(str(percent)) / Decimal(100)
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _compare(left: Any, operator: str, right: Any) -> bool:
    """Sayısal karşılaştırma — koşulun operatörü Bagisto'dan geldiği gibi."""
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    if operator == ">=":
        return left >= right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == "<":
        return left < right
    return False


def _item_matches(item: dict[str, Any], condition: dict[str, Any]) -> bool:
    kind = text(condition.get("kind"))
    operator = text(condition.get("operator")) or "=="
    wanted = condition.get("value")
    ids = [as_int(one) for one in (wanted if isinstance(wanted, list) else [wanted])]
    if kind == "product":
        found = as_int(item.get("productId")) in ids
    else:
        categories = {as_int(one) for one in (item.get("categoryIds") or [])}
        found = bool(categories & set(ids))
    return not found if operator.startswith("!") else found


def _cart_condition(condition: dict[str, Any], *, subtotal: int, quantity: int,
                    payment: str) -> bool:
    kind = text(condition.get("kind"))
    operator = text(condition.get("operator")) or "=="
    if kind == "subtotal":
        return _compare(subtotal, operator, as_int(condition.get("value")))
    if kind == "itemsQty":
        return _compare(quantity, operator, as_int(condition.get("value")))
    if kind == "payment":
        same = payment == text(condition.get("value"))
        return (not same) if operator in ("!=", "!{}", "!()") else same
    return False


def evaluate(rule: dict[str, Any], cart: dict[str, Any]) -> dict[str, Any]:
    """Kuralın koşulları bu sepette tutuyor mu — ve hangi kalemlere?

    Dönüş: `{ok, items, reason}`. `items` indirimin uygulanacağı kalemlerdir:
    ürün/kategori koşulu varsa yalnız eşleşenler, yoksa sepetin tamamı.
    """
    items = list(cart.get("items") or [])
    subtotal = sum(as_int(item.get("price")) * max(1, as_int(item.get("qty"), 1))
                   for item in items)
    quantity = sum(max(1, as_int(item.get("qty"), 1)) for item in items)
    payment = text(cart.get("paymentMethod"))
    conditions = [row for row in (rule.get("conditions") or []) if isinstance(row, dict)]
    any_mode = text(rule.get("conditionType")) == "any"

    if not conditions:
        return {"ok": True, "items": items, "reason": ""}

    # Kalem eşleşmesi KİMLİKLE değil SIRA NUMARASIYLA tutulur: sepette aynı
    # ürünün iki satırı olabilir ve sözlük eşitliği ikisini tek satır sanar.
    results: list[bool] = []
    hit_sets: list[set[int]] = []
    for condition in conditions:
        kind = text(condition.get("kind"))
        if kind in ITEM_CONDITIONS:
            hits = {index for index, item in enumerate(items)
                    if _item_matches(item, condition)}
            hit_sets.append(hits)
            results.append(bool(hits))
        else:
            results.append(_cart_condition(condition, subtotal=subtotal, quantity=quantity,
                                           payment=payment))

    item_seen = bool(hit_sets)
    if not hit_sets:
        matched_indexes: set[int] = set(range(len(items)))
    elif any_mode:
        matched_indexes = set().union(*hit_sets)
    else:
        matched_indexes = set(hit_sets[0]).intersection(*hit_sets[1:])
    matched = [item for index, item in enumerate(items) if index in matched_indexes]

    ok = any(results) if any_mode else all(results)
    if not ok:
        failed = [CONDITION_TEXT.get(text(condition.get("kind")), text(condition.get("kind")))
                  for condition, result in zip(conditions, results, strict=False) if not result]
        joiner = " ya da " if any_mode else " ve "
        return {"ok": False, "items": [], "reason": f"koşul tutmadı: {joiner.join(failed)}"}
    return {"ok": True, "items": matched if item_seen else items, "reason": ""}


CONDITION_TEXT = {
    "subtotal": "sepet tutarı",
    "itemsQty": "ürün adedi",
    "product": "ürün",
    "category": "kategori",
    "payment": "ödeme yöntemi",
}


def _eligible(rule: dict[str, Any], cart: dict[str, Any], coupon: str) -> str:
    """Kural bu sepete BAKABİLİR mi — koşullardan önceki kapılar.

    Boş metin "bakabilir" demektir; dolu metin kullanıcıya gösterilecek
    gerekçedir. "Neden uygulanmadı" sorusunun cevabı ekranın yarısıdır.
    """
    if text(rule.get("status")) != "active":
        return f"kural {text(rule.get('statusLabel')) or 'yayında değil'}"
    if text(rule.get("couponType")) == "specific":
        code = text(rule.get("code"))
        if not coupon:
            return "kupon kodu isteniyor, sepette kod yok"
        if code and code.casefold() != coupon.casefold():
            return "kupon kodu bu kurala ait değil"
        if not code and not rule.get("autoGenerated"):
            return "kuralda kupon kodu tanımlı değil"
    groups = [as_int(one) for one in (rule.get("customerGroups") or [])]
    group = as_int(cart.get("customerGroupId"))
    if groups and group and group not in groups:
        return "müşteri grubu kapsam dışı"
    channels = [as_int(one) for one in (rule.get("channels") or [])]
    channel = as_int(cart.get("channelId"))
    if channels and channel and channel not in channels:
        return "kanal kapsam dışı"
    usage = rule.get("usage") or {}
    if usage.get("exhausted"):
        return f"kullanım limiti dolmuş ({usage.get('label', '')})"
    return ""


def _discount(rule: dict[str, Any], items: list[dict[str, Any]], subtotal: int) -> tuple[int, str]:
    """Kuralın indirimi ve tek cümlelik açıklaması."""
    action = rule.get("action") if isinstance(rule.get("action"), dict) else {}
    kind = text(action.get("kind")) or text(rule.get("actionType")) or "by_percent"
    base = sum(as_int(item.get("price")) * max(1, as_int(item.get("qty"), 1)) for item in items)

    if kind == "by_percent":
        percent = as_float(action.get("value", rule.get("value")))
        amount = _pct(base, percent)
        note = f"%{percent:g} × {len(items)} kalem"
    elif kind == "by_fixed":
        unit = as_int(action.get("value", rule.get("value")))
        quantity = sum(max(1, as_int(item.get("qty"), 1)) for item in items)
        amount = min(base, unit * quantity)
        note = f"kalem başına sabit × {quantity} adet"
    elif kind == "cart_fixed":
        amount = min(subtotal, as_int(action.get("value", rule.get("value"))))
        note = "sepet toplamından sabit"
    elif kind == "buy_x_get_y":
        amount, note = _buy_x_get_y(action, items)
    else:
        return 0, "bilinmeyen indirim tipi"

    cap = as_int(action.get("maxDiscount"))
    if cap and amount > cap:
        return cap, f"{note} → üst sınıra takıldı"
    return amount, note


def _buy_x_get_y(action: dict[str, Any], items: list[dict[str, Any]]) -> tuple[int, str]:
    """X al Y öde: her (X+Y) adette Y adet bedava — EN UCUZ kalemler bedava.

    En pahalıyı bedava vermek mağazanın aleyhinedir ve Bagisto da en ucuzu
    seçer; ekranın iyimser bir tutar göstermesi kampanyayı yanlış planlatır.
    """
    step = max(0, as_int(action.get("step")))
    free_each = max(0, as_int(action.get("quantity")))
    if step <= 0 or free_each <= 0:
        return 0, "X al Y öde adetleri eksik"

    units: list[int] = []
    for item in items:
        units.extend([as_int(item.get("price"))] * max(1, as_int(item.get("qty"), 1)))
    units.sort()
    free_count = (len(units) // (step + free_each)) * free_each
    if free_count <= 0:
        return 0, f"sepette {step + free_each} adede ulaşılmadı"
    return sum(units[:free_count]), f"{free_count} adet bedava (en ucuzlar)"


def simulate(cart: dict[str, Any], rules: Any, *, coupon: str = "",
             today: str = "") -> dict[str, Any]:
    """Örnek sepete kuralları uygular ve HER kural için sonucu anlatır.

    Kurallar `sort_order` (öncelik) sırasına göre işlenir; `endOtherRules`
    taşıyan bir kural uygulandığında zincir DURUR — Bagisto da böyle davranır
    ve iki kampanyanın üst üste binmesi en sık yapılan hatadır.
    """
    stamp = today or today_iso()
    items = [dict(item) for item in (cart.get("items") or []) if isinstance(item, dict)]
    subtotal = sum(as_int(item.get("price")) * max(1, as_int(item.get("qty"), 1))
                   for item in items)
    shipping = as_int(cart.get("shipping"))
    coupon_code = text(coupon or cart.get("coupon"))

    ordered = sorted(
        [rule for rule in (rules or []) if isinstance(rule, dict)],
        key=lambda rule: (as_int(rule.get("priority")), as_int(rule.get("id"))),
    )

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total = 0
    free_shipping = False
    stopped = ""

    for rule in ordered:
        name = text(rule.get("name")) or f"#{as_int(rule.get('id'))}"
        if stopped:
            skipped.append({"id": as_int(rule.get("id")), "name": name,
                            "reason": f"önceki kural ({stopped}) zinciri durdurdu"})
            continue
        gate = _eligible(rule, {**cart, "items": items}, coupon_code)
        if gate:
            skipped.append({"id": as_int(rule.get("id")), "name": name, "reason": gate})
            continue
        verdict = evaluate(rule, {**cart, "items": items})
        if not verdict["ok"]:
            skipped.append({"id": as_int(rule.get("id")), "name": name,
                            "reason": verdict["reason"]})
            continue

        amount, note = _discount(rule, verdict["items"], subtotal)
        # İndirim sepetten büyük olamaz: kalan tutarla sınırlanır. Sınırsız
        # bırakmak "eksi ödeme" üretir ve hatayı ödeme adımında görürsünüz.
        amount = max(0, min(amount, subtotal - total))
        action = rule.get("action") if isinstance(rule.get("action"), dict) else {}
        ships_free = bool(action.get("freeShipping") or rule.get("freeShipping"))
        if amount <= 0 and not ships_free:
            skipped.append({"id": as_int(rule.get("id")), "name": name,
                            "reason": f"indirim çıkmadı ({note})"})
            continue

        total += amount
        free_shipping = free_shipping or ships_free
        applied.append({
            "id": as_int(rule.get("id")), "name": name, "amount": amount,
            "note": note + (" · ücretsiz kargo" if ships_free else ""),
            "items": len(verdict["items"]),
        })
        if rule.get("limits", {}).get("endOtherRules") or rule.get("endOtherRules"):
            stopped = name

    payable = max(0, subtotal - total) + (0 if free_shipping else shipping)
    return {
        "date": stamp,
        "subtotal": subtotal,
        "shipping": shipping,
        "discount": total,
        "freeShipping": free_shipping,
        "payable": payable,
        "applied": applied,
        "skipped": skipped,
        "chainStopped": stopped,
    }


# =========================================================== kupon önizleme

def code_preview(*, prefix: str = "", length: int = 8, fmt: str = "alphanumeric",
                 count: int = 3, suffix: str = "",
                 rng: random.Random | None = None) -> list[str]:
    """"Kodlar neye benzeyecek" örneği. HİÇBİR YERE YAZILMAZ.

    Gerçek kodları mağaza üretir; benzersizliği garanti edebilecek tek yer
    orasıdır. Burada üretilen kodu kupon sanıp müşteriye vermek, var olmayan
    bir kod dağıtmak olurdu — ekran bunu açıkça yazar.
    """
    pool = ALPHABETS.get(fmt, ALPHABETS["alphanumeric"])
    picker = rng or random.SystemRandom()
    head = text(prefix)
    tail = text(suffix)
    size = max(1, as_int(length))
    return [head + "".join(picker.choice(pool) for _ in range(size)) + tail
            for _ in range(max(1, as_int(count)))]


def generator_error(*, count: int, length: int, min_length: int, max_length: int,
                    max_count: int) -> str:
    """Üreteç sınırları. Aşan istek mağaza tarafında zaman aşımına uğruyor."""
    if not 0 < as_int(count) <= max_count:
        return f"Adet 1 ile {max_count} arasında olmalı."
    if not min_length <= as_int(length) <= max_length:
        return f"Kod uzunluğu {min_length} ile {max_length} arasında olmalı."
    return ""


def new_codes(before: Any, after: Any) -> list[str]:
    """Üretimden sonra GERÇEKTEN eklenen kodlar.

    Mağazanın toplu üretim ucu ürettiği kodları yanıtta döndürmüyor; kupon
    listesi üretimden önce ve sonra okunup farkı alınır. Sayının istenen adede
    eşit olmaması "bazıları üretilemedi" demektir ve ekran bunu söyler.
    """
    seen = {text(item.get("code")) if isinstance(item, dict) else text(item)
            for item in (before or [])}
    out = []
    for item in after or []:
        code = text(item.get("code")) if isinstance(item, dict) else text(item)
        if code and code not in seen:
            out.append(code)
    return out


# ================================================================ performans

def coupon_performance(orders: Any, *, rules: Any = None) -> dict[str, Any]:
    """Sipariş listesinden kupon başına performans.

    NEDEN SİPARİŞTEN: Bagisto'nun raporlama ucu kampanya kırılımı vermiyor,
    kupon kodu sipariş satırında duruyor. Toplulaştırma bu yüzden burada.

    İPTAL EDİLEN SİPARİŞ ciroya girmez ama ayrı sayılır: kampanyanın iptal
    oranı yüksekse bunu görmek gerekir.

    ALAN ADLARI `pick()` ile okunur: canlı mağaza `grandTotal` · `couponCode` ·
    `discountAmount` · `createdAt` yayınlıyor (TUZAK 8).

    İNDİRİM TUTARI LİSTE UCUNDA YOKTUR — yalnız sipariş DETAYINDA gelir. Servis
    kuponlu siparişlerin detayını okuyup buraya `discountAmount` olarak
    ekliyor; alan hiç gelmediyse `discountKnown: False` döner ve ekran indirimi
    "—" gösterir. Eksik veriye 0 yazmak kampanyayı bedavaymış gibi gösterirdi.
    """
    names = {text(rule.get("code")).casefold(): text(rule.get("name"))
             for rule in (rules or []) if isinstance(rule, dict) and text(rule.get("code"))}

    buckets: dict[str, dict[str, Any]] = {}
    totals = {"orders": 0, "revenue": 0, "discount": 0, "couponOrders": 0,
              "couponRevenue": 0, "couponDiscount": 0, "canceled": 0,
              "discountKnown": 0, "discountMissing": 0}

    for raw in orders or []:
        if not isinstance(raw, dict):
            continue
        status = text(raw.get("status")).lower()
        dead = status in DEAD_STATUSES
        revenue = to_kurus(pick(raw, "grand_total") or pick(raw, "base_grand_total")) or 0
        has_discount = any(pick(raw, name) is not None
                           for name in ("discount_amount", "base_discount_amount"))
        discount = to_kurus(pick(raw, "discount_amount")
                            or pick(raw, "base_discount_amount")) or 0
        code = text(pick(raw, "coupon_code"))

        if dead:
            totals["canceled"] += 1
        else:
            totals["orders"] += 1
            totals["revenue"] += revenue
            totals["discount"] += discount

        if not code:
            continue
        key = code.casefold()
        bucket = buckets.setdefault(key, {
            "code": code, "rule": names.get(key, ""), "orders": 0, "canceled": 0,
            "revenue": 0, "discount": 0, "first": "", "last": "", "discountMissing": 0,
        })
        created = day(pick(raw, "created_at"))
        if created:
            bucket["first"] = min(bucket["first"] or created, created)
            bucket["last"] = max(bucket["last"], created)
        if dead:
            bucket["canceled"] += 1
            continue
        bucket["orders"] += 1
        bucket["revenue"] += revenue
        bucket["discount"] += discount
        totals["couponOrders"] += 1
        totals["couponRevenue"] += revenue
        totals["couponDiscount"] += discount
        if has_discount:
            totals["discountKnown"] += 1
        else:
            bucket["discountMissing"] += 1
            totals["discountMissing"] += 1

    rows = []
    for bucket in buckets.values():
        orders_count = bucket["orders"]
        rows.append({
            **bucket,
            # İndirim rakamı EKSİKSE oran ve tutar yalan olur; ekran "—" yazsın.
            "discountKnown": bucket["discountMissing"] == 0,
            "average": round(bucket["revenue"] / orders_count) if orders_count else 0,
            # İndirimin ciroya oranı: "1 lira indirim kaç lira ciro getirdi"
            # sorusunun tersi. Yüzde, ciro sıfırken TANIMSIZDIR — 0 yazmak
            # kampanyayı bedavaymış gibi gösterir.
            "costRatio": (round(bucket["discount"] * 100 / bucket["revenue"], 1)
                          if bucket["revenue"] and not bucket["discountMissing"] else None),
        })
    rows.sort(key=lambda row: row["revenue"], reverse=True)

    totals["average"] = (round(totals["couponRevenue"] / totals["couponOrders"])
                         if totals["couponOrders"] else 0)
    totals["plainOrders"] = totals["orders"] - totals["couponOrders"]
    totals["plainRevenue"] = totals["revenue"] - totals["couponRevenue"]
    totals["plainAverage"] = (round(totals["plainRevenue"] / totals["plainOrders"])
                              if totals["plainOrders"] else 0)
    totals["discountComplete"] = totals["discountMissing"] == 0
    totals["costRatio"] = (round(totals["couponDiscount"] * 100 / totals["couponRevenue"], 1)
                           if totals["couponRevenue"] and totals["discountComplete"] else None)
    return {"rows": rows, "totals": totals}


def rule_usage_bars(rules: Any) -> list[dict[str, Any]]:
    """Kullanım çubuğu verisi — en çok kullanılan kural üstte."""
    rows = []
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        usage = rule.get("usage") or {}
        rows.append({
            "id": as_int(rule.get("id")),
            "name": text(rule.get("name")),
            "used": as_int(usage.get("used")),
            "limit": as_int(usage.get("limit")),
            "ratio": usage.get("ratio"),
            "label": text(usage.get("label")),
            "status": text(rule.get("status")),
        })
    rows.sort(key=lambda row: row["used"], reverse=True)
    return rows
