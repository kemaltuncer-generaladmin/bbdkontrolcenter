"""Promosyon verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bagisto'nun kural kaydı iki dilde konuşuyor: künye alanları
düz sütun, koşullar ise `{"attribute": "cart|base_sub_total", "operator": ">=",
"value": "500.0000"}` biçiminde serbest bir JSON dizisi. Ekranın bu dili
konuşması, EAV ayrıntısını arayüze sızdırmak olurdu; çeviriler burada
girdi→çıktı fonksiyonudur ve testler ağ olmadan çalışır.

DOKUZ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Kural kaydı KISMİ PUT kabul etmez     → `write_rule_body` oku-değiştir-yaz.
 2. Tanımadığımız koşul silinmemeli       → `decode_conditions` bilinmeyeni
                                             AYIRIR, `encode_conditions` geri koyar.
 3. `status` tek başına "yayında" demez    → `rule_status` takvimi de okur:
                                             aktif ama tarihi geçmiş kural ÖLÜDÜR.
 4. Para telde ONDALIK, içeride kuruş      → `to_kurus` / `from_kurus`.
 5. Yüzde de `discount_amount` alanında    → `action_value` eylem tipine bakar;
                                             yüzdeyi kuruşa çevirmek 100 kat şişirirdi.
 6. Kanal/grup bazen nesne bazen kimlik    → `id_list` ikisini de okur.
 7. Üst sınır alanı her sürümde yok        → `field_supported`; bulunmayan alana
                                             yazmak "kaydettim" yanılgısı üretir.
 8. OKUMA camelCase, YAZMA snake_case      → `pick`; ayrıntı aşağıda.
 9. Kuralın kolonu `apply_free_shipping`   → `free_shipping` + `apply_to_shipping`;
    değil                                    olmayan alana yazmak sessizce yutulur.

TUZAK 8 — YÖNLERİ FARKLI İKİ ALFABE. Bagisto'nun API kaynağı (`Resource`)
alanları **camelCase** yayınlar: canlıda `startsFrom` · `couponType` ·
`timesUsed` · `discountAmount` · `usagePerCustomer` · `usageLimit` ·
`expiredAt` gelir. Yazma isteği ise Eloquent'in `fillable` listesine, yani
**veritabanı sütun adlarına** (snake_case) bakar. Bu yüzden okuma `pick()`
ile İKİ ADI DA dener, gövde ise HER ZAMAN snake_case kurulur. Yalnız
snake_case okuyan bir ekran canlıda tabloyu "—" ile doldurur, her kuralı
"Duraklatıldı" gösterir ve oku-değiştir-yaz'da gövdeyi boş göndererek kaydı
BOŞALTIR — sessiz ve yıkıcıdır.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Bagisto eylem tipleri → ekran etiketi.
ACTION_LABELS = {
    "by_percent": "Yüzde indirim",
    "by_fixed": "Kalem başına sabit indirim",
    "cart_fixed": "Sepet toplamından sabit indirim",
    "buy_x_get_y": "X al Y öde",
}

#: Kuralın ekrandaki durumu. `status` alanı tek başına yetmez (TUZAK 3).
STATUS_LABELS = {
    "active": "Aktif",
    "scheduled": "Zamanlanmış",
    "expired": "Süresi doldu",
    "paused": "Duraklatıldı",
    "draft": "Taslak",
}

#: Üst sınır alanı Bagisto sürümüne göre var ya da yok. Sabit yazıp körlemesine
#: göndermek, hiçbir şeyi etkilemeyen bir alan yazmak demektir (TUZAK 7).
#: bbdstore.com.tr (Bagisto 2.4.8) bu alanı TAŞIMIYOR — ekran onu kapalı gösterir.
MAX_DISCOUNT_KEY = "max_discount_amount"

#: Ücretsiz kargo `cart_rules` tablosunda `free_shipping` sütunudur; API
#: kaynağı bunu `freeShipping` diye yayınlar. `apply_to_shipping` AYRI bir
#: sütundur ("indirim kargo bedeline de insin mi") ve bu ekran ona dokunmaz,
#: yalnız taşır. `apply_free_shipping` diye bir alan HİÇ YOKTUR (TUZAK 9).
FREE_SHIPPING_KEY = "free_shipping"

#: Ekranın koşul türleri → Bagisto koşul özniteliği.
#:
#: DİKKAT: Bagisto bu anahtarları kendi yönetim ekranında dinamik üretiyor ve
#: geçitte "koşul öznitelikleri" ucu YOK. Aşağıdaki eşleme Bagisto 2.4
#: çekirdeğinin kullandığı adlardır; mağaza başka bir ad kullanırsa o koşul
#: BİLİNMEYEN sayılır, silinmez, ekranda "bu ekranda düzenlenmiyor" der.
CONDITION_ATTRS: dict[str, tuple[str, str]] = {
    "subtotal": ("cart|base_sub_total", "price"),
    "itemsQty": ("cart|items_qty", "integer"),
    "product": ("product|product_id", "select"),
    "category": ("product|category_ids", "multiselect"),
    "payment": ("cart|payment_method", "select"),
}

CONDITION_LABELS = {
    "subtotal": "Sepet ara toplamı",
    "itemsQty": "Sepetteki ürün adedi",
    "product": "Ürün",
    "category": "Kategori",
    "payment": "Ödeme yöntemi",
}

#: Öznitelikten ekran türüne ters eşleme.
ATTR_TO_KIND = {attribute: kind for kind, (attribute, _) in CONDITION_ATTRS.items()}

#: Bagisto operatörleri. Ekran bunları olduğu gibi taşır; çevirmek, mağazadan
#: gelen ama bizim tanımadığımız bir operatörü sessizce değiştirmek olurdu.
OPERATORS = ("==", "!=", ">=", "<=", ">", "<", "{}", "!{}", "()", "!()")

OPERATOR_LABELS = {
    "==": "eşitse",
    "!=": "eşit değilse",
    ">=": "en az",
    "<=": "en çok",
    ">": "büyükse",
    "<": "küçükse",
    "{}": "içeriyorsa",
    "!{}": "içermiyorsa",
    "()": "listedeyse",
    "!()": "listede değilse",
}

#: Kuruş taşıyan koşul türleri. Sepet tutarı telde ondalık gider.
MONEY_CONDITIONS = ("subtotal",)

#: Gövdeye giren kural alanları — HEPSİ `cart_rules` SÜTUN ADIDIR (snake_case).
#: Listede olmayan hiçbir alan gövdeye elle konmaz; mevcut kayıttan aynen
#: taşınır (TUZAK 1). `uses_attribute_conditions` ve `apply_to_shipping` bu
#: ekranda düzenlenmez ama TAŞINIR: gövde tam gönderildiği için taşımamak
#: mağaza yöneticisinin ayarını sıfırlardı.
EDITABLE_RULE_FIELDS = (
    "name", "description", "starts_from", "ends_till", "status",
    "coupon_type", "use_auto_generation", "coupon_code",
    "usage_per_customer", "uses_per_coupon", "condition_type", "conditions",
    "end_other_rules", "discount_amount", "action_type",
    "discount_quantity", "discount_step", "apply_to_shipping", "free_shipping",
    "uses_attribute_conditions", "sort_order", "channels", "customer_groups",
)


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow()` kullanılmaz: kampanya penceresi mağazanın takvim gününe
    göre açılıp kapanır ve gece yarısından sonraki üç saatte UTC bir gün geride
    kalır — kampanya ekranda bitmiş, vitrinde sürüyor görünürdü.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def camel(name: str) -> str:
    """`starts_from` → `startsFrom`. Yalnız bu dosyanın okuma tarafı kullanır."""
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def pick(raw: Any, name: str, default: Any = None) -> Any:
    """Alanı ADI snake_case verilmiş hâliyle arar, bulamazsa camelCase dener.

    TUZAK 8: canlı mağaza (`bagisto-api` Resource) camelCase yayınlıyor, yazma
    tarafı snake_case sütun adı bekliyor. Kod TEK ADI — sütun adını — kullansın
    diye çeviri burada yapılır; her okuma yerinde iki anahtar yazmak, birini
    unutmayı garanti ederdi.

    `None` gelen alan YOK sayılır: canlıda boş takvim `startsFrom: null` gelir
    ve `day(None)` ile `day(eksik)` aynı sonucu vermeli.
    """
    if not isinstance(raw, dict):
        return default
    value = raw.get(name)
    if value is not None:
        return value
    value = raw.get(camel(name))
    return default if value is None else value


def has_field(raw: Any, name: str) -> bool:
    """Kayıt bu alanı (iki yazımdan biriyle) TAŞIYOR mu — değeri boş olsa bile."""
    return isinstance(raw, dict) and (name in raw or camel(name) in raw)


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
    """Bagisto'nun ONDALIK para değerini kuruşa çevirir. Çözülemezse `None`.

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar; indirim hesabında
    binde bir görünen, bulunması imkânsız bir sapmadır.
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
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def truthy(value: Any) -> bool:
    """Bagisto 1/0, "1"/"0", true/false karışık döndürür; hepsi aynı anlama gelir."""
    if isinstance(value, bool):
        return value
    return text(value).lower() in ("1", "true", "yes", "on")


def day(value: Any) -> str:
    """Tarih alanının YALNIZ gün kısmı: `2026-08-13 00:00:00` → `2026-08-13`."""
    return text(value)[:10]


def id_list(value: Any) -> list[int]:
    """Kanal/grup listesi bazen kimlik dizisi, bazen nesne dizisi gelir (TUZAK 6)."""
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        found = as_int(item.get("id")) if isinstance(item, dict) else as_int(item)
        if found:
            out.append(found)
    return sorted(set(out))


def field_supported(sample: Any, key: str) -> bool:
    """Mağazadan gelen kayıt bu alanı taşıyor mu (TUZAK 7).

    Taşımıyorsa ekran alanı KAPALI gösterir ve gövdeye koymaz: bulunmayan alana
    yazmak Laravel tarafında sessizce yok sayılır ve kullanıcı "üst sınırı
    ayarladım" sanır. İki yazım da denenir (TUZAK 8).
    """
    return has_field(sample, key)


# ================================================================== koşullar

def decode_conditions(raw: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    """Bagisto koşul dizisini ekran satırlarına çevirir.

    TANIMADIĞIMIZ KOŞUL SİLİNMEZ (TUZAK 2): ikinci listede ham hâliyle döner ve
    `encode_conditions` onu gövdeye geri koyar. Aksi hâlde mağaza yöneticisinin
    kendi ekranından eklediği bir koşul, bizim "kaydet" düğmemizle yok olurdu.
    """
    known: list[dict[str, Any]] = []
    unknown: list[Any] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            unknown.append(item)
            continue
        kind = ATTR_TO_KIND.get(text(item.get("attribute")))
        if not kind:
            unknown.append(item)
            continue
        operator = text(item.get("operator")) or "=="
        if operator not in OPERATORS:
            unknown.append(item)
            continue
        known.append({
            "kind": kind,
            "label": CONDITION_LABELS[kind],
            "operator": operator,
            "operatorLabel": OPERATOR_LABELS.get(operator, operator),
            "value": _decode_value(kind, item.get("value")),
        })
    return known, unknown


def _decode_value(kind: str, value: Any) -> Any:
    if kind in MONEY_CONDITIONS:
        return to_kurus(value) or 0
    if kind == "itemsQty":
        return as_int(value)
    if kind in ("product", "category"):
        if isinstance(value, list):
            return [as_int(item) for item in value if as_int(item)]
        return [as_int(part) for part in text(value).split(",") if as_int(part)]
    return text(value)


def _encode_value(kind: str, value: Any) -> str:
    if kind in MONEY_CONDITIONS:
        return from_kurus(as_int(value))
    if kind == "itemsQty":
        return str(as_int(value))
    if kind in ("product", "category"):
        items = value if isinstance(value, list) else text(value).split(",")
        return ",".join(str(as_int(item)) for item in items if as_int(item))
    return text(value)


def encode_conditions(rows: Any, unknown: Any = None) -> list[dict[str, Any]]:
    """Ekran satırlarını Bagisto koşul dizisine çevirir; bilinmeyenleri korur."""
    out: list[dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        kind = text(row.get("kind"))
        if kind not in CONDITION_ATTRS:
            continue
        attribute, attribute_type = CONDITION_ATTRS[kind]
        operator = text(row.get("operator")) or "=="
        if operator not in OPERATORS:
            operator = "=="
        out.append({
            "attribute": attribute,
            "attribute_type": attribute_type,
            "operator": operator,
            "value": _encode_value(kind, row.get("value")),
        })
    out.extend(unknown or [])
    return out


def condition_error(rows: Any) -> str:
    """Koşul satırlarının kabul edilebilirliği. Boş değer sessizce geçmez."""
    for index, row in enumerate(rows if isinstance(rows, list) else [], start=1):
        if not isinstance(row, dict):
            return f"{index}. koşul okunamadı."
        kind = text(row.get("kind"))
        if kind not in CONDITION_ATTRS:
            return f"{index}. koşulun türü tanınmadı: {kind or '(boş)'}"
        value = row.get("value")
        if kind in ("product", "category"):
            if not _decode_value(kind, value):
                return f"{index}. koşulda ({CONDITION_LABELS[kind]}) hiçbir kayıt seçilmedi."
        elif kind == "payment":
            if not text(value):
                return f"{index}. koşulda ödeme yöntemi seçilmedi."
        elif as_int(value) <= 0:
            return f"{index}. koşulda ({CONDITION_LABELS[kind]}) değer sıfırdan büyük olmalı."
    return ""


# ==================================================================== durum

def rule_status(raw: dict[str, Any], today: str = "") -> str:
    """Kuralın GERÇEK durumu (TUZAK 3).

    `status=0` → duraklatıldı. `status=1` ama başlangıç ileri tarihli →
    zamanlanmış; bitiş geçmiş → süresi doldu. Hiç tarihi ve adı olmayan yeni
    kayıt taslaktır. Yalnız `status` alanına bakan bir ekran, üç ay önce
    bitmiş kampanyayı "Aktif" gösterirdi.
    """
    now = today or today_iso()
    start = day(pick(raw, "starts_from"))
    end = day(pick(raw, "ends_till"))
    if not truthy(pick(raw, "status")):
        return "paused"
    if start and now < start:
        return "scheduled"
    if end and now > end:
        return "expired"
    return "active"


def usage_view(used: Any, limit: Any) -> dict[str, Any]:
    """Kullanım çubuğunun verisi. Limit yoksa oran YOKTUR — %0 yazmak yalandır."""
    used_count = max(0, as_int(used))
    limit_count = max(0, as_int(limit))
    ratio = round(min(100.0, used_count * 100 / limit_count), 1) if limit_count else None
    return {
        "used": used_count,
        "limit": limit_count,
        "ratio": ratio,
        "exhausted": bool(limit_count and used_count >= limit_count),
        "label": f"{used_count}/{limit_count}" if limit_count else f"{used_count}/sınırsız",
    }


def action_value(raw: dict[str, Any]) -> int | float:
    """İndirim değeri — yüzdeyse SAYI, tutarsa KURUŞ (TUZAK 5).

    İkisi de `discount_amount` alanında durur. Yüzdeyi kuruşa çevirmek %10'u
    1.000 kuruşa dönüştürür ve ekranda "%1000 indirim" yazardı.
    """
    kind = text(pick(raw, "action_type")) or "by_percent"
    if kind == "by_percent":
        return round(as_float(pick(raw, "discount_amount")), 2)
    return to_kurus(pick(raw, "discount_amount")) or 0


def action_display(raw: dict[str, Any]) -> str:
    """İndirimin tek satırlık okunur özeti — tablo sütunu için.

    KARGO KURALI "%0" DEĞİLDİR. Canlıda ücretsiz kargo kampanyaları
    `actionType: by_percent, discountAmount: 0, freeShipping: 1` olarak duruyor;
    tabloya "%0" yazmak kuralı bozukmuş gibi gösteriyordu. Değer sıfır ve kargo
    bedava ise sütun kuralın GERÇEK işini söyler.
    """
    kind = text(pick(raw, "action_type")) or "by_percent"
    value = action_value(raw)
    if not value and truthy(pick(raw, FREE_SHIPPING_KEY)):
        return "ücretsiz kargo"
    if kind == "by_percent":
        return f"%{value:g}".replace(".", ",")
    if kind == "buy_x_get_y":
        return (f"{as_int(pick(raw, 'discount_step'))} al "
                f"{as_int(pick(raw, 'discount_quantity'))} öde")
    return f"{Decimal(int(value)) / 100:.2f} ₺".replace(".", ",")


# ============================================================== sepet kuralı

def cart_rule_row(raw: dict[str, Any], *, today: str = "",
                  expiring_days: int = 7) -> dict[str, Any]:
    """Liste satırı. Tablo bunu çizer; düzenleyici `cart_rule_detail` kullanır."""
    now = today or today_iso()
    state = rule_status(raw, now)
    end = day(pick(raw, "ends_till"))
    usage = usage_view(pick(raw, "times_used"), pick(raw, "uses_per_coupon"))
    kind = text(pick(raw, "action_type")) or "by_percent"
    coupon_type = "specific" if as_int(pick(raw, "coupon_type")) == 1 else "none"
    # LİSTE UCU kapsamı vermiyor: canlıda `channels`/`customerGroups` yalnız
    # TEKİL kayıtta dolu gelir, listede `null`dur. Boş liste "kapsam yok"
    # demek DEĞİLDİR; `scopeKnown` bu ikisini ayırır ve süzgeç ona bakar.
    return {
        "id": as_int(pick(raw, "id")),
        "name": text(pick(raw, "name")) or f"#{as_int(pick(raw, 'id'))}",
        "description": text(pick(raw, "description")),
        "code": text(pick(raw, "coupon_code")),
        "couponType": coupon_type,
        "autoGenerated": truthy(pick(raw, "use_auto_generation")),
        "actionType": kind,
        "actionLabel": ACTION_LABELS.get(kind, kind),
        "value": action_value(raw),
        "valueLabel": action_display(raw),
        "freeShipping": truthy(pick(raw, FREE_SHIPPING_KEY)),
        "startsFrom": day(pick(raw, "starts_from")),
        "endsTill": end,
        "status": state,
        "statusLabel": STATUS_LABELS[state],
        "usage": usage,
        "priority": as_int(pick(raw, "sort_order")),
        "endOtherRules": truthy(pick(raw, "end_other_rules")),
        "channels": id_list(pick(raw, "channels")),
        "customerGroups": id_list(pick(raw, "customer_groups")),
        "scopeKnown": pick(raw, "channels") is not None
                      or pick(raw, "customer_groups") is not None,
        "neverUsed": usage["used"] == 0,
        "expiringSoon": _expiring(end, now, expiring_days),
    }


def _expiring(end: str, today: str, window: int) -> bool:
    if not end or end < today:
        return False
    try:
        left = (datetime.fromisoformat(end).date() - datetime.fromisoformat(today).date()).days
    except ValueError:
        return False
    return 0 <= left <= max(0, window)


def cart_rule_detail(raw: dict[str, Any], *, coupons: Any = None,
                     today: str = "") -> dict[str, Any]:
    """Düzenleyicinin okuduğu tam model: künye + koşullar + eylem + kısıt + takvim."""
    row = cart_rule_row(raw, today=today)
    known, unknown = decode_conditions(pick(raw, "conditions"))
    max_discount = to_kurus(pick(raw, MAX_DISCOUNT_KEY)) \
        if field_supported(raw, MAX_DISCOUNT_KEY) else None
    return {
        **row,
        "conditionType": "any" if as_int(pick(raw, "condition_type"), 1) == 2 else "all",
        "conditions": known,
        "unknownConditions": len(unknown),
        "action": {
            "kind": row["actionType"],
            "value": row["value"],
            "maxDiscount": max_discount,
            "maxDiscountSupported": field_supported(raw, MAX_DISCOUNT_KEY),
            "freeShipping": row["freeShipping"],
            "step": as_int(pick(raw, "discount_step")),
            "quantity": as_int(pick(raw, "discount_quantity")),
        },
        "limits": {
            "perCoupon": as_int(pick(raw, "uses_per_coupon")),
            "perCustomer": as_int(pick(raw, "usage_per_customer")),
            "endOtherRules": row["endOtherRules"],
            "priority": row["priority"],
        },
        "coupons": [coupon_row(item, today=today) for item in (coupons or [])
                    if isinstance(item, dict)],
    }


def write_rule_body(current: dict[str, Any], patch: dict[str, Any], *,
                    channel_ids: list[int] | None = None) -> dict[str, Any]:
    """Kural gövdesi — OKU-DEĞİŞTİR-YAZ (TUZAK 1).

    Bagisto bu uçta gövdenin tamamını bekliyor: göndermediğiniz alan boşalıyor.
    Bu yüzden taban HER ZAMAN mağazadan taze okunan kayıttır; ekran yalnız
    dokunduğu alanları üzerine yazar. Tanımadığımız alanlar (mağazanın kendi
    eklentilerinin yazdıkları) olduğu gibi taşınır.

    GÖVDE HER ZAMAN snake_case'tir (TUZAK 8): mağaza camelCase OKUTUYOR ama
    snake_case YAZDIRIYOR. Taban `pick()` ile okunur; okunamayan alanı gövdeye
    hiç koymamak, tam gövde bekleyen uçta o alanı SIFIRLAMAK olurdu.
    """
    body: dict[str, Any] = {}
    for key in EDITABLE_RULE_FIELDS:
        if has_field(current, key):
            body[key] = pick(current, key)
    if field_supported(current, MAX_DISCOUNT_KEY):
        # Mağazada VARSA taşınır: gövde tam gönderildiği için taşımamak,
        # ekranın hiç dokunmadığı üst sınırı sıfırlardı.
        body[MAX_DISCOUNT_KEY] = pick(current, MAX_DISCOUNT_KEY)

    known, unknown = decode_conditions(pick(current, "conditions"))
    body["conditions"] = encode_conditions(known, unknown)
    body["channels"] = id_list(pick(current, "channels")) or list(channel_ids or [])
    body["customer_groups"] = id_list(pick(current, "customer_groups"))

    if "name" in patch:
        body["name"] = text(patch["name"])
    if "description" in patch:
        body["description"] = text(patch["description"])
    if "startsFrom" in patch:
        body["starts_from"] = day(patch["startsFrom"])
    if "endsTill" in patch:
        body["ends_till"] = day(patch["endsTill"])
    if "status" in patch:
        body["status"] = 1 if truthy(patch["status"]) else 0
    if "couponType" in patch:
        specific = text(patch["couponType"]) == "specific"
        body["coupon_type"] = 1 if specific else 0
        if not specific:
            # Kupon tipi kapatılırken kod alanı da temizlenir; kalırsa mağaza
            # "bu kod başka kuralda kullanılıyor" diyerek kaydı reddediyor.
            body["coupon_code"] = ""
    if "couponCode" in patch:
        body["coupon_code"] = text(patch["couponCode"])
    if "autoGenerated" in patch:
        body["use_auto_generation"] = 1 if truthy(patch["autoGenerated"]) else 0
    if "conditionType" in patch:
        body["condition_type"] = 2 if text(patch["conditionType"]) == "any" else 1
    if "conditions" in patch:
        body["conditions"] = encode_conditions(patch["conditions"], unknown)
    if "customerGroups" in patch:
        body["customer_groups"] = [as_int(item) for item in (patch["customerGroups"] or [])
                                   if as_int(item)]
    if "channels" in patch:
        body["channels"] = [as_int(item) for item in (patch["channels"] or []) if as_int(item)]

    action = patch.get("action") if isinstance(patch.get("action"), dict) else {}
    if action:
        kind = text(action.get("kind")) or text(body.get("action_type")) or "by_percent"
        body["action_type"] = kind
        if "value" in action:
            body["discount_amount"] = (f"{as_float(action['value']):g}" if kind == "by_percent"
                                       else from_kurus(as_int(action["value"])))
        if "freeShipping" in action:
            body[FREE_SHIPPING_KEY] = 1 if truthy(action["freeShipping"]) else 0
        if "step" in action:
            body["discount_step"] = as_int(action["step"])
        if "quantity" in action:
            body["discount_quantity"] = as_int(action["quantity"])
        # Üst sınır YALNIZCA mağaza kaydında bu alan varsa yazılır (TUZAK 7).
        if "maxDiscount" in action and field_supported(current, MAX_DISCOUNT_KEY):
            value = action["maxDiscount"]
            body[MAX_DISCOUNT_KEY] = from_kurus(as_int(value)) if as_int(value) > 0 else None

    limits = patch.get("limits") if isinstance(patch.get("limits"), dict) else {}
    if "perCoupon" in limits:
        body["uses_per_coupon"] = max(0, as_int(limits["perCoupon"]))
    if "perCustomer" in limits:
        # Sütun adı `usage_per_customer` — `uses_per_customer` DEĞİL. Yanlış ad
        # Eloquent'te sessizce yutulur ve limit hiç yazılmazdı.
        body["usage_per_customer"] = max(0, as_int(limits["perCustomer"]))
    if "endOtherRules" in limits:
        body["end_other_rules"] = 1 if truthy(limits["endOtherRules"]) else 0
    if "priority" in limits:
        body["sort_order"] = max(0, as_int(limits["priority"]))

    return body


def new_rule_body(patch: dict[str, Any], *, channel_ids: list[int],
                  group_ids: list[int]) -> dict[str, Any]:
    """Yeni kuralın gövdesi. TASLAK AÇILIR: `status=0`.

    Yeni kural kendiliğinden yayına girmez — yayına almak ayrı izin ister
    (`store_promotions.activate`), çünkü yayındaki kural her siparişte para
    indirir.
    """
    base: dict[str, Any] = {
        "name": "", "description": "", "starts_from": "", "ends_till": "", "status": 0,
        "coupon_type": 0, "use_auto_generation": 0, "coupon_code": "",
        "usage_per_customer": 0, "uses_per_coupon": 0, "condition_type": 1, "conditions": [],
        "end_other_rules": 0, "discount_amount": "0", "action_type": "by_percent",
        "discount_quantity": 0, "discount_step": 0, "apply_to_shipping": 0,
        FREE_SHIPPING_KEY: 0, "uses_attribute_conditions": 0, "sort_order": 0,
        "channels": list(channel_ids), "customer_groups": list(group_ids),
    }
    body = write_rule_body(base, {**patch, "status": False}, channel_ids=channel_ids)
    body["status"] = 0
    return body


def rule_error(body: dict[str, Any]) -> str:
    """Kaydetmeden önceki iş kuralı denetimi. Ekranda gizlemek yetmez (K9)."""
    if not text(body.get("name")):
        return "Kural adı zorunlu."
    start = day(body.get("starts_from"))
    end = day(body.get("ends_till"))
    if start and end and end < start:
        return "Bitiş tarihi başlangıçtan önce olamaz."
    kind = text(body.get("action_type"))
    if kind not in ACTION_LABELS:
        return f"Bilinmeyen indirim tipi: {kind or '(boş)'}"
    amount = as_float(body.get("discount_amount"))
    # SIFIR İNDİRİM + ÜCRETSİZ KARGO GEÇERLİ BİR KURALDIR. Canlıda iki kampanya
    # tam olarak böyle duruyor ("750 TL Üzeri Ücretsiz Kargo"). Sıfırı koşulsuz
    # reddeden bir doğrulama o kuralların adını bile değiştirilemez yapıyordu:
    # oku-değiştir-yaz kaydı taze okur, kendi denetimimize takılır ve kullanıcı
    # "kaydedilemedi" der.
    free = truthy(body.get(FREE_SHIPPING_KEY))
    if kind == "by_percent" and not 0 <= amount <= 100:
        return "Yüzde indirim 0 ile 100 arasında olmalı."
    if kind in ("by_fixed", "cart_fixed") and amount < 0:
        return "İndirim tutarı eksi olamaz."
    if kind in ("by_percent", "by_fixed", "cart_fixed") and amount <= 0 and not free:
        return ("İndirim sıfır ve ücretsiz kargo da kapalı; bu kural hiçbir şey yapmaz. "
                "Bir değer girin ya da ücretsiz kargoyu açın.")
    if kind == "buy_x_get_y" and (as_int(body.get("discount_step")) <= 0
                                  or as_int(body.get("discount_quantity")) <= 0):
        return "X al Y öde kuralında iki adet de sıfırdan büyük olmalı."
    if as_int(body.get("coupon_type")) == 1 and not truthy(body.get("use_auto_generation")) \
            and not text(body.get("coupon_code")):
        return "Kupon kodu zorunlu: kupon tipi seçili ama kod yazılmamış."
    return ""


# ============================================================ katalog kuralı

def catalog_rule_row(raw: dict[str, Any], *, today: str = "") -> dict[str, Any]:
    """Katalog kuralı satırı — sepete değil, VİTRİN FİYATINA uygulanır."""
    now = today or today_iso()
    state = rule_status(raw, now)
    kind = text(pick(raw, "action_type")) or "by_percent"
    return {
        "id": as_int(pick(raw, "id")),
        "name": text(pick(raw, "name")) or f"#{as_int(pick(raw, 'id'))}",
        "description": text(pick(raw, "description")),
        "actionType": kind,
        "actionLabel": ACTION_LABELS.get(kind, kind),
        "value": action_value(raw),
        "valueLabel": action_display(raw),
        "startsFrom": day(pick(raw, "starts_from")),
        "endsTill": day(pick(raw, "ends_till")),
        "status": state,
        "statusLabel": STATUS_LABELS[state],
        "priority": as_int(pick(raw, "sort_order")),
        "endOtherRules": truthy(pick(raw, "end_other_rules")),
        "channels": id_list(pick(raw, "channels")),
        "customerGroups": id_list(pick(raw, "customer_groups")),
    }


def catalog_rule_detail(raw: dict[str, Any], *, today: str = "") -> dict[str, Any]:
    row = catalog_rule_row(raw, today=today)
    known, unknown = decode_conditions(pick(raw, "conditions"))
    return {
        **row,
        "conditionType": "any" if as_int(pick(raw, "condition_type"), 1) == 2 else "all",
        "conditions": known,
        "unknownConditions": len(unknown),
        "action": {"kind": row["actionType"], "value": row["value"]},
        "limits": {"endOtherRules": row["endOtherRules"], "priority": row["priority"]},
    }


#: Katalog kuralının yazılabilir alanları. Kupon ve ücretsiz kargo YOKTUR:
#: katalog kuralı sepete değil ürün fiyatına uygulanır.
EDITABLE_CATALOG_FIELDS = (
    "name", "description", "starts_from", "ends_till", "status", "condition_type",
    "conditions", "end_other_rules", "discount_amount", "action_type", "sort_order",
    "channels", "customer_groups",
)


def write_catalog_body(current: dict[str, Any], patch: dict[str, Any], *,
                       channel_ids: list[int] | None = None) -> dict[str, Any]:
    """Katalog kuralı gövdesi — sepet kuralıyla aynı oku-değiştir-yaz kuralı."""
    body: dict[str, Any] = {}
    for key in EDITABLE_CATALOG_FIELDS:
        if has_field(current, key):
            body[key] = pick(current, key)

    known, unknown = decode_conditions(pick(current, "conditions"))
    body["conditions"] = encode_conditions(known, unknown)
    body["channels"] = id_list(pick(current, "channels")) or list(channel_ids or [])
    body["customer_groups"] = id_list(pick(current, "customer_groups"))

    if "name" in patch:
        body["name"] = text(patch["name"])
    if "description" in patch:
        body["description"] = text(patch["description"])
    if "startsFrom" in patch:
        body["starts_from"] = day(patch["startsFrom"])
    if "endsTill" in patch:
        body["ends_till"] = day(patch["endsTill"])
    if "status" in patch:
        body["status"] = 1 if truthy(patch["status"]) else 0
    if "conditionType" in patch:
        body["condition_type"] = 2 if text(patch["conditionType"]) == "any" else 1
    if "conditions" in patch:
        body["conditions"] = encode_conditions(patch["conditions"], unknown)
    if "customerGroups" in patch:
        body["customer_groups"] = [as_int(item) for item in (patch["customerGroups"] or [])
                                   if as_int(item)]
    if "channels" in patch:
        body["channels"] = [as_int(item) for item in (patch["channels"] or []) if as_int(item)]

    action = patch.get("action") if isinstance(patch.get("action"), dict) else {}
    if action:
        kind = text(action.get("kind")) or text(body.get("action_type")) or "by_percent"
        if kind not in ("by_percent", "by_fixed"):
            # Katalog kuralında sepet eylemleri yoktur; sessizce dönüştürmek
            # yerine yüzdede bırakılır ve `rule_error` uyarır.
            kind = "by_percent"
        body["action_type"] = kind
        if "value" in action:
            body["discount_amount"] = (f"{as_float(action['value']):g}" if kind == "by_percent"
                                       else from_kurus(as_int(action["value"])))
    limits = patch.get("limits") if isinstance(patch.get("limits"), dict) else {}
    if "endOtherRules" in limits:
        body["end_other_rules"] = 1 if truthy(limits["endOtherRules"]) else 0
    if "priority" in limits:
        body["sort_order"] = max(0, as_int(limits["priority"]))
    return body


def new_catalog_body(patch: dict[str, Any], *, channel_ids: list[int],
                     group_ids: list[int]) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "", "description": "", "starts_from": "", "ends_till": "", "status": 0,
        "condition_type": 1, "conditions": [], "end_other_rules": 0, "discount_amount": "0",
        "action_type": "by_percent", "sort_order": 0,
        "channels": list(channel_ids), "customer_groups": list(group_ids),
    }
    body = write_catalog_body(base, {**patch, "status": False}, channel_ids=channel_ids)
    body["status"] = 0
    return body


# ==================================================================== kupon

def coupon_row(raw: dict[str, Any], *, today: str = "") -> dict[str, Any]:
    """Tek kupon kodu satırı — kullanım çubuğunu ve son kullanma durumunu taşır."""
    now = today or today_iso()
    expires = day(pick(raw, "expired_at"))
    usage = usage_view(pick(raw, "times_used"), pick(raw, "usage_limit"))
    expired = bool(expires and expires < now)
    return {
        "id": as_int(pick(raw, "id")),
        "code": text(pick(raw, "code")),
        "usage": usage,
        "perCustomer": as_int(pick(raw, "usage_per_customer")),
        "expiresAt": expires,
        "expired": expired,
        "state": "expired" if expired else ("exhausted" if usage["exhausted"] else "usable"),
        "stateLabel": ("Süresi doldu" if expired
                       else ("Limiti doldu" if usage["exhausted"] else "Kullanılabilir")),
        "removable": usage["used"] == 0,
    }


def coupon_payload(*, prefix: str, count: int, length: int, fmt: str = "alphanumeric",
                   suffix: str = "") -> dict[str, Any]:
    """Toplu kupon üretim gövdesi.

    Alan adları Bagisto'nun `MassGenerate` isteğinin adlarıdır. Son kullanma
    tarihi BURADA YOKTUR: çekirdek uç kuponun süresini kuralın bitiş tarihinden
    alır; gövdeye `expired_at` koymak Laravel tarafında sessizce yok sayılır ve
    kullanıcı "son kullanma verdim" sanırdı.
    """
    return {
        "coupon_qty": max(1, as_int(count)),
        "code_length": max(1, as_int(length)),
        "code_format": fmt if fmt in ("alphanumeric", "alphabetical", "numeric")
        else "alphanumeric",
        "code_prefix": text(prefix),
        "code_suffix": text(suffix),
    }
