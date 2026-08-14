"""Kargo verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Kargo iki ayrı sistemin arasında durur: Bagisto siparişi ve
Geliver gönderisi. Aynı bilgi iki tarafta farklı adla, farklı birimde ve
farklı durum sözlüğüyle gelir. Bu çeviriler servise gömülseydi tek satırı bile
test edilemezdi; burada hepsi girdi→çıktı fonksiyonu.

ON BİR TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Desi ≠ ağırlık               → `desi`/`billed_units` ikisinin BÜYÜĞÜNÜ alır.
 2. Desi yukarı yuvarlanır       → `billed_units` tavana yuvarlar; taşıyıcı da öyle.
 3. Kademe boşluğu sessiz kalır  → `tier_problems` boşluğu ve örtüşmeyi söyler.
 4. Para ondalık gelir           → `to_kurus` Decimal ile çevirir, float yok.
 5. Durum sözlüğü taşıyıcıya göre→ `status_of` bilinmeyeni UYDURMAZ, ham bırakır.
 6. "Geciken" = son hareketten   → `idle_days` teslim edileni saymaz.
 7. Kapıda ödeme tahsilatı ayrı  → `flags` tahsil edilmemişi işaretler.
 8. Kargoya hazır ≠ tüm siparişler → `ready_state` kalan adede bakar.
 9. En özel bölge kazanır        → `zone_for` önce ilçeye, sonra ile bakar.
10. Teslimat adresi listede       → `shipping_address` `addresses[]` içinden
    `addressType` ile ayıklar; fatura adresine DÜŞMEZ.
11. Alan yokluğu ≠ değer sıfır    → `ready_state` bilgisi olmayanı engellemez.

ALAN ADLARI CANLI YANITTAN DOĞRULANMIŞTIR. Bagisto 2.4.8 yönetici API'si
camelCase döner (`incrementId`, `grandTotal`, `totalQtyOrdered`, `qtyOrdered`);
snake_case karşılıkları yalnız eski/yerel kayıtlar için yedekte tutulur.
"""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor.
MIN_REASON = 10

#: PARA HARCAYAN işlemde gerekçe daha uzun istenir. "kargo" ya da "gönderildi"
#: gibi bir metin, iki ay sonra "bu 340 lira neden harcandı" sorusunu
#: cevaplamıyor; etiket satın alma geri alınamayan bir ödemedir.
MIN_PURCHASE_REASON = 20

#: Taşıyıcı kodları → ekranda görünen ad. Kod mağazadan gelir; listede olmayan
#: kod UYDURULMAZ, kodun kendisi gösterilir.
CARRIER_LABELS = {
    "yurtici": "Yurtiçi Kargo",
    "aras": "Aras Kargo",
    "mng": "MNG Kargo",
    "ptt": "PTT Kargo",
    "surat": "Sürat Kargo",
    "ups": "UPS",
    "sendeo": "Sendeo",
    "hepsijet": "HepsiJET",
    "kolaygelsin": "Kolay Gelsin",
}

#: Gönderi durumları. Anahtar bizim sözlüğümüz; `STATUS_ALIASES` taşıyıcıdan
#: gelen yazımları buraya çeker.
STATUS_LABELS = {
    "created": "Etiket oluşturuldu",
    "picked_up": "Teslim alındı",
    "in_transit": "Yolda",
    "at_branch": "Şubede",
    "out_for_delivery": "Dağıtımda",
    "delivered": "Teslim edildi",
    "undelivered": "Teslim edilemedi",
    "returning": "İade dönüyor",
    "returned": "İade teslim",
    "cancelled": "İptal",
}

#: Süreç bitmiş sayılan durumlar. "Geciken" hesabı bunları atlar: teslim
#: edilmiş bir gönderi 40 gündür hareketsizdir ve bu normaldir.
FINAL_STATUSES = ("delivered", "returned", "cancelled")

STATUS_ALIASES = {
    "label_created": "created", "labelcreated": "created", "draft": "created",
    "ready": "created", "pending": "created", "new": "created",
    "pickedup": "picked_up", "picked-up": "picked_up", "collected": "picked_up",
    "intransit": "in_transit", "transit": "in_transit", "shipped": "in_transit",
    "atbranch": "at_branch", "branch": "at_branch", "arrived": "at_branch",
    "outfordelivery": "out_for_delivery", "distribution": "out_for_delivery",
    "delivery": "out_for_delivery",
    "completed": "delivered", "done": "delivered",
    "failed": "undelivered", "not_delivered": "undelivered", "exception": "undelivered",
    "returning_to_sender": "returning", "return_in_transit": "returning",
    "return_delivered": "returned", "returned_to_sender": "returned",
    "canceled": "cancelled", "void": "cancelled",
}

#: Ödeme tipi: kargo ücretini kim öder.
PAYER_LABELS = {"sender": "Gönderici", "receiver": "Alıcı"}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: "kaç gündür yolda" hesabı mağazanın
    takvim gününe göre yapılır ve gece yarısından sonraki üç saatte UTC bir gün
    geride kalır — gönderi ekranda bir gün genç görünürdü.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def now_iso() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def to_kurus(value: Any) -> int | None:
    """Mağazanın ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("34.35") * 100` bazı değerlerde
    3434.99999 verir ve `int()` bir kuruş aşağı yuvarlar. Kargo ücretlerinde
    binde bir görünen, mutabakatta bulunması imkânsız bir sapmadır.
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


def from_kurus(kurus: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 34350 → `343.50`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def fold(value: Any) -> str:
    """Aksansız, küçük harfli karşılaştırma anahtarı: `Şırnak` → `sirnak`."""
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", folded).strip().lower()


def reason_error(value: str, minimum: int = MIN_REASON) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    if len(text(value)) < minimum:
        return (f"Gerekçe en az {minimum} karakter olmalı; denetim kaydına bu metin "
                "yazılır ve harcamanın nedeni sonradan buradan okunur.")
    return ""


def carrier_label(code: Any) -> str:
    key = fold(code)
    return CARRIER_LABELS.get(key, text(code) or "—")


def _first(source: Any, *keys: str) -> Any:
    """İlk DOLU değeri döndürür.

    Bagisto yanıtı camelCase (`qtyOrdered`), yerel/eski kayıtlar snake_case
    (`qty_ordered`). Her alanı `a or b or c` ile yazmak `0` ve `False`
    değerlerini de eler; bu yardımcı yalnız `None` ve boş metni atlar.
    """
    if not isinstance(source, dict):
        return None
    for key in keys:
        if key in source:
            value = source[key]
            if value is not None and value != "":
                return value
    return None


def _has_any(source: Any, *keys: str) -> bool:
    """Alanlardan biri yanıtta VAR MI (değeri 0 olsa bile).

    "Alan yok" ile "alan var ve 0" farklı şeylerdir: birincisi bilgi
    eksikliği, ikincisi bilginin kendisidir. `ready_state` kararını buna
    dayandırır — yoksa bilgisi olmayan bir liste "ödenmemiş" sayılırdı.
    """
    return isinstance(source, dict) and any(key in source for key in keys)


def days_between(start: str, end: str) -> int | None:
    """İki ISO gün/zaman damgası arasındaki tam gün. Çözülemezse `None`.

    Sıfır DEĞİL `None` döner: "aynı gün" ile "tarih okunamadı" farklı şeylerdir
    ve ortalama teslim süresinde ikincisi hesaba KATILMAMALIDIR.
    """
    left = _date_of(start)
    right = _date_of(end)
    if left is None or right is None:
        return None
    return (right - left).days


def _date_of(value: Any) -> Any:
    raw = text(value)[:10]
    if len(raw) != 10:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()  # noqa: DTZ007 - takvim günü
    except ValueError:
        return None


# ====================================================================== desi

def desi(width_cm: Any, height_cm: Any, length_cm: Any, *, divisor: int = 3000) -> float:
    """Hacimsel ağırlık: en × boy × yükseklik (cm) / bölen.

    Bölen sözleşmeye bağlıdır (yurt içi kara 3000, havayolu 5000) ve ayardan
    gelir. Ölçülerden biri girilmemişse 0 döner — uydurma kutu varsayılmaz.
    """
    width = max(0.0, as_float(width_cm))
    height = max(0.0, as_float(height_cm))
    length = max(0.0, as_float(length_cm))
    if width <= 0 or height <= 0 or length <= 0:
        return 0.0
    return round(width * height * length / max(1, int(divisor)), 2)


def billed_units(desi_value: Any, weight_kg: Any) -> int:
    """Ücretlendirilecek birim (TUZAK 1 ve 2).

    Taşıyıcı desi ile fiili ağırlığın BÜYÜĞÜNÜ alır ve tam sayıya YUKARI
    yuvarlar: 1,2 desi 2 desi olarak faturalanır. Aşağı yuvarlamak ekranda
    ucuz, faturada pahalı bir tahmin üretirdi.
    """
    largest = max(as_float(desi_value), as_float(weight_kg))
    if largest <= 0:
        return 0
    return max(1, math.ceil(largest - 1e-9))


def auto_measures(items: Any, *, divisor: int = 3000) -> dict[str, Any]:
    """Sipariş kalemlerinden desi/ağırlık TAHMİNİ (sihirbazın ilk değeri).

    Ürünün üzerinde ölçü varsa kullanılır; yoksa yalnız ağırlık toplanır ve
    `complete: False` döner. Ekran o zaman "ölçüleri elle girin" der — eksik
    veriden tahmini kutu ÜRETMEZ, çünkü yanlış desi doğrudan yanlış ücrettir.
    """
    weight = 0.0
    volume = 0.0
    missing: list[str] = []
    counted = 0

    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        # CANLI DOĞRULANDI: Bagisto sipariş kaleminde alan `qtyOrdered`.
        # `qty_ordered` yalnız yerel/eski biçimdir; ikisi de denenir yoksa
        # adet 1 sayılır ve toplam ağırlık sessizce eksik çıkardı.
        qty = max(1, as_int(_first(item, "qty_ordered", "qtyOrdered", "qty"), 1))
        counted += qty
        weight += as_float(item.get("weight")) * qty
        width = as_float(item.get("width"))
        height = as_float(item.get("height"))
        length = as_float(item.get("length"))
        if width > 0 and height > 0 and length > 0:
            volume += width * height * length * qty
        else:
            missing.append(text(item.get("sku") or item.get("name")) or "?")

    estimate = round(volume / max(1, int(divisor)), 2) if volume else 0.0
    return {
        "weight": round(weight, 2),
        "desi": estimate,
        "units": billed_units(estimate, weight),
        "pieces": counted,
        "complete": bool(volume) and not missing,
        "missing": missing[:20],
    }


# =============================================================== ücretlendirme

def tier_problems(tiers: Any) -> list[str]:
    """Desi kademelerinde boşluk/örtüşme var mı (TUZAK 3).

    Boşluk sessizce "ücret bulunamadı"ya, örtüşme ise sıraya göre değişen
    keyfî bir ücrete dönüşür. İkisi de kullanıcıya SÖYLENİR; ekran matrisi
    kırmızı işaretler ve hangi aralığın açıkta kaldığını yazar.
    """
    rows = [row for row in (tiers or []) if isinstance(row, dict)]
    if not rows:
        return ["Desi kademesi tanımlı değil; ücret hesaplanamaz."]

    clean = sorted(
        ({"min": as_float(row.get("min")), "max": as_float(row.get("max"), 0.0),
          "price": as_int(row.get("price"))} for row in rows),
        key=lambda row: row["min"],
    )
    problems: list[str] = []
    previous_max = 0.0
    for index, row in enumerate(clean):
        if row["min"] < previous_max - 1e-9:
            problems.append(f"{row['min']:g}–{row['max'] or '∞'} kademesi bir öncekiyle örtüşüyor.")
        elif row["min"] > previous_max + 1e-9:
            problems.append(f"{previous_max:g} ile {row['min']:g} desi arası açıkta.")
        if row["max"] and row["max"] <= row["min"]:
            problems.append(f"{row['min']:g} kademesinin üst sınırı alt sınırından küçük.")
        # Son kademe açık uçlu olabilir (max = 0); ortadaki kademe olamaz.
        if not row["max"] and index != len(clean) - 1:
            problems.append(f"{row['min']:g} kademesi açık uçlu ama sonuncu değil.")
        previous_max = row["max"] or previous_max
    return problems


def rate_for(units: Any, tiers: Any) -> dict[str, Any]:
    """Desi kademesine düşen taban ücret (kuruş). Bulunamazsa `found: False`."""
    amount = as_float(units)
    for row in tiers or []:
        if not isinstance(row, dict):
            continue
        low = as_float(row.get("min"))
        high = as_float(row.get("max"), 0.0)
        if amount + 1e-9 < low:
            continue
        if high and amount - 1e-9 > high:
            continue
        return {"found": True, "price": as_int(row.get("price")),
                "min": low, "max": high, "label": text(row.get("label"))}
    return {"found": False, "price": 0, "min": 0.0, "max": 0.0, "label": ""}


def quote(*, units: int, tiers: Any, basket_total: int = 0, free_threshold: int = 0,
          zone_surcharge: int = 0, cod: bool = False, cod_fee: int = 0,
          payer: str = "sender") -> dict[str, Any]:
    """Bir gönderinin ücret dökümü. Her satır AYRI gösterilir, toplam gizlenmez.

    Ücretsiz kargo eşiği YALNIZCA göndericinin ödediği gönderide çalışır:
    alıcı ödemeli gönderide tutarı taşıyıcı alıcıdan tahsil eder, mağazanın
    eşiği oraya karışmaz.
    """
    base = rate_for(units, tiers)
    lines: list[dict[str, Any]] = [
        {"label": f"{units} desi taban ücreti", "amount": base["price"],
         "note": "" if base["found"] else "Bu desi için kademe tanımlı değil."},
    ]
    total = base["price"]
    if zone_surcharge:
        lines.append({"label": "Bölgesel ek ücret", "amount": zone_surcharge, "note": ""})
        total += zone_surcharge
    if cod and cod_fee:
        lines.append({"label": "Kapıda ödeme hizmet bedeli", "amount": cod_fee, "note": ""})
        total += cod_fee

    free = False
    if payer == "sender" and free_threshold and basket_total >= free_threshold:
        free = True
        lines.append({"label": "Ücretsiz kargo eşiği uygulandı", "amount": -total,
                      "note": f"Sepet {from_kurus(basket_total)} ≥ {from_kurus(free_threshold)}"})
        total = 0

    return {"units": int(units), "lines": lines, "total": max(0, total),
            "free": free, "tierFound": base["found"], "payer": payer,
            "payerLabel": PAYER_LABELS.get(payer, payer)}


# ===================================================================== bölge

def zone_for(city: Any, district: Any, zones: Any) -> dict[str, Any]:
    """En ÖZEL eşleşme kazanır (TUZAK 9): önce il+ilçe, sonra ilin tamamı.

    Eşleşme bulunamazsa `found: False` döner ve ek ücret 0 sayılır — bilinmeyen
    bölgeye ceza yazmak, tanımı unutulmuş bir ilçeyi pahalı gösterirdi.
    """
    city_key = fold(city)
    district_key = fold(district)
    fallback: dict[str, Any] | None = None
    for row in zones or []:
        if not isinstance(row, dict):
            continue
        if fold(row.get("city")) != city_key:
            continue
        row_district = fold(row.get("district"))
        if row_district and row_district == district_key:
            return {"found": True, "zone": text(row.get("zone")),
                    "surcharge": as_int(row.get("surcharge")),
                    "delivers": bool(as_int(row.get("delivers"), 1)),
                    "note": text(row.get("note")), "scope": "district"}
        if not row_district:
            fallback = {"found": True, "zone": text(row.get("zone")),
                        "surcharge": as_int(row.get("surcharge")),
                        "delivers": bool(as_int(row.get("delivers"), 1)),
                        "note": text(row.get("note")), "scope": "city"}
    if fallback:
        return fallback
    return {"found": False, "zone": "", "surcharge": 0, "delivers": True,
            "note": "", "scope": ""}


# ==================================================================== durum

def status_of(raw: Any) -> str:
    """Taşıyıcının durum yazımını bizim sözlüğümüze çeker (TUZAK 5).

    Bilinmeyen durum UYDURULMAZ: ham kod olduğu gibi döner, ekran onu
    "bilinmeyen durum" rozetiyle gösterir. `in_transit` varsaymak, teslim
    edilememiş bir gönderiyi yolda gibi gösterirdi.
    """
    key = fold(raw).replace(" ", "_").replace("-", "_")
    if not key:
        return ""
    if key in STATUS_LABELS:
        return key
    return STATUS_ALIASES.get(key, key)


def status_label(code: str) -> str:
    return STATUS_LABELS.get(code, text(code) or "—")


def is_final(code: str) -> bool:
    return code in FINAL_STATUSES


def idle_days(last_move: str, *, status: str, today: str) -> int | None:
    """Son hareketten bu yana geçen gün (TUZAK 6). Bitmiş gönderide `None`."""
    if is_final(status):
        return None
    return days_between(last_move, today)


# ================================================================ satırlar

def movement_rows(raw: Any) -> list[dict[str, Any]]:
    """Hareket geçmişi — zaman çizelgesi. En yeni ÜSTTE."""
    items = raw if isinstance(raw, list) else (raw or {}).get("movements") or []
    rows: list[dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        code = status_of(item.get("status") or item.get("code"))
        rows.append({
            "at": text(item.get("at") or item.get("created_at") or item.get("date"))[:19],
            "status": code,
            "statusLabel": status_label(code),
            "location": text(item.get("location") or item.get("branch") or item.get("unit")),
            "note": text(item.get("note") or item.get("description") or item.get("message")),
        })
    rows.sort(key=lambda row: row["at"], reverse=True)
    return rows


def shipment_row(raw: dict[str, Any], *, today: str = "", idle_limit: int = 3,
                 zones: Any = None) -> dict[str, Any]:
    """Ham gönderi kaydı → tablonun beklediği satır. Para her yerde kuruş."""
    day = today or today_iso()
    status = status_of(raw.get("status"))
    address = raw.get("address") if isinstance(raw.get("address"), dict) else raw
    city = text(address.get("city") or raw.get("city"))
    district = text(address.get("district") or address.get("state") or raw.get("district"))

    created = text(raw.get("created_at") or raw.get("createdAt"))[:19]
    moved = text(raw.get("last_movement_at") or raw.get("lastMovementAt")
                 or raw.get("updated_at"))[:19] or created
    delivered = text(raw.get("delivered_at") or raw.get("deliveredAt"))[:19]

    desi_value = as_float(raw.get("desi"))
    weight = as_float(raw.get("weight"))
    units = as_int(raw.get("billed_desi")) or billed_units(desi_value, weight)

    payer = fold(raw.get("payer") or raw.get("payment_type")) or "sender"
    payer = "receiver" if payer in ("receiver", "alici", "consignee") else "sender"
    cod = as_int(to_kurus(raw.get("cod_amount", raw.get("codAmount"))) or 0)
    collected = bool(raw.get("cod_collected", raw.get("codCollected")))

    zone = zone_for(city, district, zones) if zones else None
    idle = idle_days(moved, status=status, today=day)

    row = {
        "id": as_int(raw.get("id")),
        "orderId": as_int(raw.get("order_id") or raw.get("orderId")),
        "orderNumber": text(raw.get("order_number") or raw.get("orderNumber")),
        "trackingNo": text(raw.get("tracking_number") or raw.get("trackingNo")
                           or raw.get("barcode")),
        "trackingUrl": text(raw.get("tracking_url") or raw.get("trackingUrl")),
        "customer": text(raw.get("customer_name") or raw.get("customerName")
                         or address.get("name")),
        "phone": text(address.get("phone") or raw.get("phone")),
        "city": city,
        "district": district,
        "addressText": text(address.get("address") or address.get("line1")),
        "carrier": fold(raw.get("carrier") or raw.get("provider")),
        "carrierLabel": carrier_label(raw.get("carrier") or raw.get("provider")),
        "desi": desi_value,
        "weight": weight,
        "units": units,
        "packages": max(1, as_int(raw.get("packages"), 1)),
        "fee": to_kurus(raw.get("price", raw.get("fee"))) or 0,
        # Müşterinin siparişte ÖDEDİĞİ kargo bedeli — taşıyıcıya ödediğimiz
        # `fee` ile aynı şey değildir, kâr/zarar ikisinin farkıdır.
        # `None` bırakılır (0 değil): "tahsilat yok" ile "tahsilat bilinmiyor"
        # ayrı şeylerdir ve `analytics.money_summary` ikincisini sayar.
        "collectedFee": to_kurus(_first(raw, "collected_fee", "collectedFee",
                                        "shipping_amount", "shippingAmount",
                                        "customer_paid", "customerPaid")),
        "payer": payer,
        "payerLabel": PAYER_LABELS[payer],
        "cod": cod,
        "codCollected": collected,
        "createdAt": created,
        "lastMoveAt": moved,
        "deliveredAt": delivered,
        "ageDays": days_between(created, day),
        "idleDays": idle,
        "deliveryDays": days_between(created, delivered) if delivered else None,
        "status": status,
        "statusLabel": status_label(status),
        "known": status in STATUS_LABELS,
        "final": is_final(status),
        "labelReady": bool(raw.get("label_url") or raw.get("labelUrl")
                           or raw.get("has_label") or text(raw.get("tracking_number"))),
        "zone": zone["zone"] if zone else "",
        "addressIssue": bool(raw.get("address_issue", raw.get("addressIssue"))),
        "error": text(raw.get("error") or raw.get("last_error")),
    }
    row["flags"] = flags(row, idle_limit=idle_limit)
    return row


def flags(row: dict[str, Any], *, idle_limit: int = 3) -> list[str]:
    """Satırın dikkat çeken yanları. Renk tek başına anlam taşımaz; bu
    etiketler ekranda YAZIYLA gösterilir."""
    out: list[str] = []
    idle = row.get("idleDays")
    if idle is not None and idle >= idle_limit:
        out.append("late")
    if row.get("addressIssue") or row.get("status") == "undelivered":
        out.append("address")
    if row.get("cod") and row.get("status") == "delivered" and not row.get("codCollected"):
        out.append("cod")
    if not row.get("known"):
        out.append("unknown")
    return out


FLAG_LABELS = {
    "late": "Geciken",
    "address": "Adres sorunu",
    "cod": "Tahsil edilmemiş",
    "unknown": "Bilinmeyen durum",
}


# =========================================================== kargoya hazır

#: Sipariş iptal/kapalı sayılan durumlar.
DEAD_ORDER_STATUSES = ("canceled", "cancelled", "closed", "fraud")


def shipping_address(order: Any) -> dict[str, Any]:
    """Siparişin TESLİMAT adresi (TUZAK 10).

    CANLI DOĞRULANDI: `/api/admin/orders/{id}` adresleri `shipping_address`
    diye tek alanda değil, `addresses` LİSTESİNDE `addressType` ayrımıyla
    veriyor; fatura adresi de aynı listede. Yanlış adresi almak paketi yanlış
    şehre yollamak demek olduğu için önce tip aranır, bulunamazsa fatura
    adresine DÜŞÜLMEZ — boş dönülür ve ekran adresin okunamadığını söyler.

    Sipariş LİSTESİ ucunda adres hiç gelmez (`location` metni var, ayrıştırmak
    güvenilmez): orada boş sözlük döner, ayrıntı için sipariş tek tek okunur.
    """
    if not isinstance(order, dict):
        return {}
    for key in ("shipping_address", "shippingAddress"):
        value = order.get(key)
        if isinstance(value, dict) and value:
            return value
    rows = order.get("addresses")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and fold(row.get("addressType")
                                              or row.get("address_type")) == "order_shipping":
                return row
    return {}


def address_name(address: Any) -> str:
    """Adres üzerindeki alıcı adı. `name` yoksa ad+soyaddan kurulur."""
    if not isinstance(address, dict):
        return ""
    whole = text(address.get("name"))
    if whole:
        return whole
    parts = [text(address.get("firstName") or address.get("first_name")),
             text(address.get("lastName") or address.get("last_name"))]
    return " ".join(part for part in parts if part)


def customer_name(order: Any, address: Any = None) -> str:
    """Siparişteki müşteri adı.

    CANLI DOĞRULANDI: liste ucu `customerName`, ayrıntı ucu
    `customerFirstName`/`customerLastName` veriyor; `customer_full_name` diye
    bir alan yok. Üçü de denenir, en son teslimat adresindeki isme düşülür.
    """
    if not isinstance(order, dict):
        return ""
    whole = text(_first(order, "customer_full_name", "customerFullName", "customerName"))
    if whole:
        return whole
    parts = [text(_first(order, "customerFirstName", "customer_first_name")),
             text(_first(order, "customerLastName", "customer_last_name"))]
    joined = " ".join(part for part in parts if part)
    if joined:
        return joined
    person = order.get("customer")
    if isinstance(person, dict):
        named = text(person.get("name")) or address_name(person)
        if named:
            return named
    return address_name(address)


def _qty_from_items(order: Any, *keys: str) -> int | None:
    """Kalem başına adetleri toplar. Alan hiçbir kalemde yoksa `None`.

    Sipariş başlığında `totalQtyShipped` YOK (canlı doğrulandı); kargolanan
    adet ancak kalemlerin `qtyShipped` alanından çıkar.
    """
    items = order.get("items") if isinstance(order, dict) else None
    if not isinstance(items, list):
        return None
    total = 0
    seen = False
    for item in items:
        if _has_any(item, *keys):
            seen = True
            total += max(0, as_int(_first(item, *keys)))
    return total if seen else None


def ready_state(order: dict[str, Any]) -> dict[str, Any]:
    """Sipariş kargoya hazır mı (TUZAK 8).

    Bagisto sipariş kaydı üç adet taşır: sipariş edilen, faturalanan,
    kargolanan. "Gönderisi yok" demek kargolanan adet 0 demek DEĞİLDİR:
    kısmi kargolanmış siparişin kalanı da kargoya hazırdır.

    TUZAK 11 — BİLGİ YOKLUĞU KANIT DEĞİLDİR. Sipariş LİSTESİ ucu faturalanan
    ve kargolanan adedi hiç göndermiyor (canlı doğrulandı). Bunu "0" sayıp
    "ödeme beklemede" demek listedeki HER siparişi engellerdi ve ekran boş
    açılırdı. Bu yüzden alan yoksa engel konmaz; `paymentKnown`/`shipmentKnown`
    False döner ve ekran "sipariş açılıp doğrulanmalı" der.
    """
    ordered = as_int(_first(order, "total_qty_ordered", "totalQtyOrdered"))

    shipped_known = _has_any(order, "total_qty_shipped", "totalQtyShipped")
    shipped = as_int(_first(order, "total_qty_shipped", "totalQtyShipped"))
    if not shipped_known:
        from_items = _qty_from_items(order, "qty_shipped", "qtyShipped")
        if from_items is not None:
            shipped, shipped_known = from_items, True

    invoiced_known = _has_any(order, "total_qty_invoiced", "totalQtyInvoiced")
    invoiced = as_int(_first(order, "total_qty_invoiced", "totalQtyInvoiced"))
    if not invoiced_known:
        from_items = _qty_from_items(order, "qty_invoiced", "qtyInvoiced")
        if from_items is not None:
            invoiced, invoiced_known = from_items, True

    # Faturalanan adet yoksa fatura kaydının VARLIĞI da ödeme kanıtıdır.
    invoices = order.get("invoices")
    paid = invoiced > 0
    paid_known = invoiced_known
    if isinstance(invoices, list):
        paid_known = True
        paid = paid or bool(invoices)
    if _has_any(order, "grand_total_invoiced", "grandTotalInvoiced"):
        paid_known = True
        paid = paid or (to_kurus(_first(order, "grand_total_invoiced",
                                        "grandTotalInvoiced")) or 0) > 0
    if _has_any(order, "payment_state", "paymentState"):
        paid_known = True
        paid = paid or fold(_first(order, "payment_state", "paymentState")) in ("paid", "captured")

    pending = max(0, ordered - shipped)
    status = fold(order.get("status"))

    if status in DEAD_ORDER_STATUSES:
        reason = "Sipariş iptal/kapalı."
    elif shipped_known and pending <= 0:
        reason = "Tüm kalemler kargolandı."
    elif paid_known and not paid:
        reason = "Ödeme/fatura beklemede."
    else:
        reason = ""

    return {"ready": not reason, "pending": pending, "ordered": ordered,
            "invoiced": invoiced, "shipped": shipped, "blocked": reason,
            "paymentKnown": paid_known, "shipmentKnown": shipped_known}


def ready_row(order: dict[str, Any], *, today: str = "", divisor: int = 3000,
              zones: Any = None) -> dict[str, Any]:
    """Kargoya hazır sekmesinin satırı: sipariş + adres + otomatik desi tahmini."""
    day = today or today_iso()
    state = ready_state(order)
    address = shipping_address(order)
    city = text(address.get("city"))
    district = text(address.get("district") or address.get("state"))
    measures = auto_measures(order.get("items"), divisor=divisor)
    zone = zone_for(city, district, zones) if zones else None
    created = text(_first(order, "created_at", "createdAt"))[:19]

    return {
        "orderId": as_int(order.get("id")),
        "orderNumber": text(_first(order, "increment_id", "incrementId", "id")),
        "customer": customer_name(order, address),
        "phone": text(_first(address, "phone")
                      or _first(order, "customer_phone", "customerPhone")),
        "city": city,
        "district": district,
        "addressText": text(_first(address, "address", "address1", "line1")),
        "total": to_kurus(_first(order, "grand_total", "grandTotal")) or 0,
        # CANLI DOĞRULANDI: ödeme başlığı sipariş kökünde `paymentTitle`;
        # `payment.method_title` yalnız bazı kurulumlarda var.
        "paymentTitle": text(_first(order, "payment_title", "paymentTitle")
                             or ((order.get("payment") or {}).get("method_title")
                                 if isinstance(order.get("payment"), dict) else "")),
        "createdAt": created,
        "waitingDays": days_between(created, day),
        "pending": state["pending"],
        "ordered": state["ordered"],
        "shipped": state["shipped"],
        "ready": state["ready"],
        "blocked": state["blocked"],
        # Ekran "kalan adet doğrulanamadı" uyarısını buna göre gösterir.
        "paymentKnown": state["paymentKnown"],
        "shipmentKnown": state["shipmentKnown"],
        "addressKnown": bool(address),
        "measures": measures,
        "zone": zone["zone"] if zone else "",
        "delivers": zone["delivers"] if zone else True,
        "status": fold(order.get("status")),
    }


# ================================================================ taşıyıcı

def mask(value: Any, keep: int = 4) -> str:
    """Kimlik bilgisi maskesi. Kasadaki değerin TAMAMI ekrana ASLA çıkmaz.

    Geçit maskeli döndürse bile burada tekrar maskelenir: iki tarafın da
    maskelediği bir sırrın kazayla sızması için ikisinin birden bozulması
    gerekir.
    """
    raw = text(value)
    if not raw:
        return ""
    if len(raw) <= keep:
        return "•" * len(raw)
    return "•" * max(4, len(raw) - keep) + raw[-keep:]


def carrier_row(raw: dict[str, Any], *, today: str = "") -> dict[str, Any]:
    """Taşıyıcı tanımı. Kimlik bilgileri MASKELİ, sözleşme matrisi ayrı."""
    code = fold(raw.get("code") or raw.get("carrier"))
    tiers = tier_rows(raw.get("tiers") or raw.get("desi_rates"))
    tested = text(raw.get("tested_at") or raw.get("testedAt"))[:19]
    return {
        "code": code,
        "label": carrier_label(code),
        "active": bool(as_int(raw.get("active", raw.get("status")), 0)),
        "default": bool(raw.get("is_default", raw.get("isDefault"))),
        "customerNo": mask(raw.get("customer_no") or raw.get("customerNo")),
        "apiKey": mask(raw.get("api_key") or raw.get("apiKey")),
        "apiUser": mask(raw.get("api_user") or raw.get("apiUser")),
        "testedAt": tested,
        "testedOk": bool(raw.get("tested_ok", raw.get("testedOk"))),
        "testMessage": text(raw.get("test_message") or raw.get("testMessage")),
        "testAgeDays": days_between(tested, today or today_iso()) if tested else None,
        "tiers": tiers,
        "problems": tier_problems(tiers),
    }


def tier_rows(raw: Any) -> list[dict[str, Any]]:
    """Desi→ücret matrisi satırları. Para KURUŞ, sınırlar ondalık desi."""
    out: list[dict[str, Any]] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "min": as_float(row.get("min", row.get("desi_min"))),
            "max": as_float(row.get("max", row.get("desi_max")), 0.0),
            "price": to_kurus(row.get("price", row.get("amount"))) or 0,
            "label": text(row.get("label")),
        })
    out.sort(key=lambda item: item["min"])
    return out


def tier_input(rows: Any) -> list[dict[str, Any]]:
    """EKRANDAN gelen matris satırları. `price` ZATEN KURUŞTUR.

    `tier_rows` mağazadan gelen ONDALIK değeri okur (`"45.00"` → 4500).
    İkisini aynı fonksiyonla çözmek, ekranın gönderdiği 4500 kuruşu 450000
    kuruş yapar ve tarife yüz katına çıkar. Ayrı fonksiyon tam bu yüzden var.
    """
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "min": as_float(row.get("min")),
            "max": as_float(row.get("max"), 0.0),
            "price": max(0, as_int(row.get("price"))),
            "label": text(row.get("label")),
        })
    out.sort(key=lambda item: item["min"])
    return out


def tier_payload(rows: Any) -> list[dict[str, Any]]:
    """Ekrandan gelen matrisi telin beklediği ondalık biçime çevirir."""
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        out.append({
            "min": as_float(row.get("min")),
            "max": as_float(row.get("max"), 0.0),
            "price": from_kurus(as_int(row.get("price"))),
        })
    out.sort(key=lambda item: item["min"])
    return out


# ============================================================ sihirbaz gövdesi

def wizard_body(*, order_id: int, carrier: str, packages: int, desi_value: float,
                weight: float, payer: str, cod: int, note: str = "",
                divisor: int = 3000) -> dict[str, Any]:
    """Gönderi sihirbazının ürettiği taslak gövdesi.

    Ölçüler ekranda hem otomatik hesaplanır hem elle düzeltilebilir; buraya
    gelen değer NİHAİ değerdir ve olduğu gibi taşınır. `billedDesi` de gider:
    taşıyıcı kendi yuvarlamasını yapacak olsa bile, bizim neyi beklediğimiz
    kayda geçmeli — fatura farkı ancak böyle görülür.
    """
    units = billed_units(desi_value, weight)
    payer_code = "receiver" if fold(payer) in ("receiver", "alici") else "sender"
    return {
        "orderId": int(order_id),
        "carrier": fold(carrier),
        "packages": max(1, int(packages or 1)),
        "desi": round(max(0.0, float(desi_value or 0)), 2),
        "weight": round(max(0.0, float(weight or 0)), 2),
        "billedDesi": units,
        "desiDivisor": int(divisor),
        "payer": payer_code,
        "codAmount": from_kurus(max(0, int(cod or 0))),
        "note": text(note)[:255],
    }


def wizard_problems(*, carrier: str, desi_value: float, weight: float, packages: int,
                    payer: str, cod: int, delivers: bool = True) -> list[str]:
    """Sihirbaz gönderilmeden önce kullanıcıya söylenecekler.

    Hata DEĞİL uyarı listesidir: ekran hepsini gösterir, düzeltilebilir olanı
    engeller. Sessizce düzeltmek (ör. 0 desiyi 1 yapmak) yanlış ücretin
    kaynağını gizler.
    """
    out: list[str] = []
    if not fold(carrier):
        out.append("Taşıyıcı seçilmedi.")
    if desi_value <= 0 and weight <= 0:
        out.append("Desi ve ağırlık boş; taşıyıcı ücreti hesaplayamaz.")
    if packages < 1:
        out.append("Paket sayısı en az 1 olmalı.")
    if fold(payer) in ("receiver", "alici") and cod <= 0:
        out.append("Alıcı ödemeli seçildi ama tahsil edilecek tutar girilmedi.")
    if cod > 0 and fold(payer) == "sender":
        out.append("Kapıda ödeme tutarı var ama ödeme tipi 'Gönderici'; tutar tahsil edilmez.")
    if not delivers:
        out.append("Bu bölgeye teslimat yapılmıyor olarak işaretli.")
    return out


# ===================================================== BAGISTO TEST GÖNDERİSİ
#
# İKİ AYRI KARGO YOLU VAR ve bilerek ayrıdır:
#
#   geliver  GERÇEK. Taşıyıcıya gider, etiket satın alınır, PARA HARCAR,
#            gerçek takip numarası taşıyıcıdan gelir.
#   bagisto  TEST. Bagisto'nun KENDİ gönderi kaydını açar. Taşıyıcıya hiç
#            çıkmaz, para harcamaz, takip numarasını BİZ üretiriz.
#
# Test yolu bir taklit değil: sipariş gerçekten "kargolandı" durumuna geçer,
# müşteri bilgilendirmesi çalışır, fiş basılır. Eksik olan tek şey taşıyıcıya
# çıkılması. Böylece akışın tamamı para harcamadan denenebilir.

#: Test takip numarası öneki. GERÇEK BİR TAŞIYICI KODUYLA ÇAKIŞMAMALI —
#: "TEST" öneki, numarayı gören herkesin (müşteri dâhil) bunun deneme
#: olduğunu anlamasını sağlar. Sessiz bir sahte numara, günün birinde gerçek
#: sanılıp kargo aranmasına yol açar.
TEST_TAKIP_ONEKI = "TEST"


def test_track_number(order_id: Any, *, when: str = "", suffix: Any = 1) -> str:
    """Test gönderisi için takip numarası üretir.

    BİÇİM: `TEST-<siparişNo>-<YYYYAAGG>-<sıra>` → "TEST-20-20260815-1"

    Rastgelelik YOK: aynı sipariş, aynı gün, aynı sıra için aynı numara çıkar.
    Rastgele numara üretmek, bir isteğin iki kez gönderilip iki kez
    kaydedildiğini gizlerdi — aynı numara ikinci kez göründüğünde çift kayıt
    gözle görülür.
    """
    day = (text(when) or today_iso())[:10].replace("-", "")
    return f"{TEST_TAKIP_ONEKI}-{as_int(order_id)}-{day}-{max(1, as_int(suffix, 1))}"


def is_test_track(value: Any) -> bool:
    """Bu takip numarası bizim ürettiğimiz test numarası mı."""
    return text(value).upper().startswith(f"{TEST_TAKIP_ONEKI}-")


def shippable_items(order: Any, *, source_id: int = 1) -> list[dict[str, Any]]:
    """Siparişin KALAN kargolanabilir kalemleri.

    Adet = sipariş edilen − zaten kargolanan. Kısmi kargolanmış siparişin
    kalanı gönderilebilir olmalı; sipariş edilen adedi olduğu gibi göndermek
    aynı kalemi ikinci kez kargolardı.

    Kalemde `qtyShipped` alanı YOKSA sıfır sayılır — bu güvenli yön DEĞİL ama
    tek seçenek: alan yoksa "hepsi kargolanmış" varsaymak siparişi hiç
    göndermez. Servis, gönderiden önce siparişin DETAYINI okuyarak bu alanı
    garantiler ({@see ShippingService.create_shipment}).
    """
    rows = order.get("items") if isinstance(order, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        ordered = as_int(_first(item, "qty_ordered", "qtyOrdered"))
        shipped = as_int(_first(item, "qty_shipped", "qtyShipped"))
        kalan = ordered - shipped
        if kalan <= 0:
            continue
        out.append({
            "orderItemId": as_int(_first(item, "id", "order_item_id", "orderItemId")),
            "inventorySourceId": max(1, int(source_id or 1)),
            "quantity": kalan,
        })
    return out


def bagisto_body(order: Any, *, carrier_title: str, track_number: str,
                 source_id: int = 1) -> dict[str, Any]:
    """Bagisto'nun kendi gönderi ucunun gövdesi.

    SÖZLEŞME canlı OpenAPI şemasından alındı (2026-08-15):

        POST /api/admin/orders/{orderId}/shipments
        {"source": 1,
         "items": [{"orderItemId": 42, "inventorySourceId": 1, "quantity": 3}],
         "carrierTitle": "UPS", "trackNumber": "1Z999AA1"}

    `source` ile kalemlerdeki `inventorySourceId` AYNI olmalı; ikisi
    ayrışırsa Bagisto stoğu bir depodan düşüp gönderiyi başka depoya yazar.
    Bu mağazada tek depo var (id=1, "Varsayılan") ama alan yine de tek
    yerden beslenir ki ikinci depo açıldığında ayrışma olmasın.
    """
    kaynak = max(1, int(source_id or 1))
    return {
        "source": kaynak,
        "items": shippable_items(order, source_id=kaynak),
        "carrierTitle": text(carrier_title) or "BBD Test Kargo",
        "trackNumber": text(track_number),
    }


def bagisto_problems(body: dict[str, Any]) -> list[str]:
    """Test gönderisi gönderilmeden önce yakalanacaklar."""
    out: list[str] = []
    if not body.get("items"):
        out.append("Kargolanacak kalem kalmamış: siparişin tamamı zaten gönderilmiş.")
    if not text(body.get("trackNumber")):
        out.append("Takip numarası boş.")
    for item in body.get("items") or []:
        if as_int(item.get("orderItemId")) <= 0:
            out.append("Sipariş kalemi kimliği okunamadı; gönderi kalem eşleşmeden açılamaz.")
            break
    return out
