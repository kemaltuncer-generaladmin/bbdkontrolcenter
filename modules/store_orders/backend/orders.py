"""Sipariş verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bagisto'nun sipariş kaydı geniş ve türetilmiş alanlarla
doludur: para ondalık gelir, "ödendi mi" diye bir alan yoktur (faturalanan
tutardan çıkarılır), durum kodu İngilizce sabittir, gönderi ve fatura ayrı
koleksiyonlardadır. Bu çeviriler servise gömülseydi tek satırı bile test
edilemezdi; burada hepsi girdi→çıktı fonksiyonudur.

ALAN ADLARI camelCase GELİR (CANLIDA DOĞRULANDI). Bagisto 2.4.8'in yönetici
REST yüzeyi `AdminResource` normalizasyonundan geçiyor: `grandTotal`,
`createdAt`, `customerFirstName`, `incrementId`, kalemde `qtyOrdered`. Bagisto'
nun VERİTABANI sütunları snake_case'tir ve belgeler o adları anlatır; uca
`grand_total` diye bakan kod hiçbir şey bulamaz, ekran sessizce boş/"—" dolu
görünür. Bu yüzden her okuma `pick()` üzerinden geçer: `pick` adı ayraçsız ve
küçük harfe indirger, iki yazım da çözülür.

LİSTE UCU SIĞDIR. `GET /orders` satırı yalnız şunları taşır: id · incrementId ·
status · channelName · customerName · customerEmail · paymentTitle ·
couponCode · totalItemCount · totalQtyOrdered · grandTotal · location ·
createdAt · (sığ) items. Fatura, gönderi, not, ara toplam, KDV, faturalanan
tutar YOKTUR — onlar yalnız `GET /orders/{id}` detayında bulunur. Bu yüzden
`has_detail()` vardır ve ödeme durumu bilgi yoksa "unknown" döner: faturalanan
tutarı görmeden "Ödenmedi" yazmak, tahsil edilmiş siparişi ödenmemiş göstermek
olurdu.

DOKUZ TUZAK — hepsinin karşılığı bu dosyada bir fonksiyondur:

 1. Ödeme durumu alanı YOK          → `payment_state` faturalanan/iade edilen
                                       tutardan çıkarır; liste ucunda o tutar
                                       hiç gelmediği için ÖDEME KANITI iki ayrı
                                       listeden toplanır (`payment_index`).
 2. Para ondalık gelir              → `to_kurus` Decimal ile çevirir, float yok.
 3. Sipariş no ≠ sipariş id         → `order_no` `increment_id` kullanır;
                                       eylem uçlarına giden HER ZAMAN `id`dir.
 4. Durum kodu İngilizce sabit      → `status_label` + yerel ad sözlüğü.
 5. Mağaza yalnız birkaç süzgeci
    uyguluyor                       → `STORE_FILTERS` ile ayrılır, gerisi
                                       taranmış küme üzerinde `matches` ile.
 6. "Geciken" göreli bir kavram     → `late_days` eşiği ayarlanabilir; ekran
                                       kaç gün olduğunu YAZAR.
 7. Kısmi fatura/kargo olağandır    → `fulfilment` üç durum döner, ikili değil.
 8. İptal geri alınamaz             → `cancel_block` pencereyi ve durumu
                                       yazmadan ÖNCE denetler.
 9. Adres serbest metindir          → `address_line` eksik parçayı atlar,
                                       "None None" üretmez.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit de (store_api) 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Bagisto sipariş durumları → Türkçe. Kullanıcı bunları Ayarlar sekmesinden
#: yeniden adlandırabilir; sözlük yalnız VARSAYILANDIR.
STATUS_LABELS = {
    "pending": "Bekliyor",
    "pending_payment": "Ödeme bekliyor",
    "processing": "Hazırlanıyor",
    "completed": "Tamamlandı",
    "canceled": "İptal",
    "closed": "Kapandı",
    "fraud": "Sahte şüphesi",
}

#: Rozet tonu. Renk TEK BAŞINA anlam taşımaz; yanında her zaman yazı durur.
STATUS_TONES = {
    "pending": "warn",
    "pending_payment": "warn",
    "processing": "info",
    "completed": "good",
    "canceled": "dim",
    "closed": "dim",
    "fraud": "bad",
}

PAYMENT_LABELS = {
    "unpaid": "Ödenmedi",
    "partial": "Kısmi",
    "paid": "Ödendi",
    "refunded": "İade edildi",
    # BANKA YANITI YOK. `unknown` / `provisioning` durumundaki bir POS denemesi
    # "başarısız" DEĞİLDİR: para çekilmiş olabilir. Bu satıra "Ödenmedi" yazmak,
    # operatöre "tekrar tahsil edin" dedirtir ve müşterinin kartından ikinci kez
    # para çıkar; fark ancak ekstre gelince, günler sonra anlaşılır.
    "uncertain": "Belirsiz — bankayla mutabakat sürüyor",
    # Kanıt penceresi bu siparişi kapsamıyor ya da kanıt okunamadı. "Ödenmedi"
    # demek, tahsil edilmiş siparişi ödenmemiş göstermek olurdu.
    "unknown": "Bilinmiyor",
}

#: Rozet tonu. `uncertain` AYRI bir ton ister: ne "iyi" ne "kötü", "bakılmalı".
#: Panel bu tonu kendi `so-pay-uncertain` sınıfıyla ayrıca boyar.
PAYMENT_TONES = {"unpaid": "bad", "partial": "warn", "paid": "good", "refunded": "dim",
                 "uncertain": "warn", "unknown": "dim"}

FULFIL_LABELS = {"none": "Yok", "partial": "Kısmi", "full": "Tam"}

#: Mağazanın sipariş listesi ucunun GERÇEKTEN uyguladığı süzgeçler
#: (`client.orders` docstring'i). Bunun dışındaki her süzgeç taranmış küme
#: üzerinde burada uygulanır; sunucuya gönderip uygulandığını varsaymak,
#: Laravel tanımadığı parametreyi sessizce yok saydığı için süzülmemiş listeyi
#: süzülmüş gibi göstermek olurdu.
STORE_FILTERS = ("status", "channel", "customer", "email", "date_from", "date_to",
                 "grand_total_from", "grand_total_to", "order_id", "sort", "order")

#: Tarih süzgecinin hangi alana uygulanacağı. Mağaza yalnız SİPARİŞ tarihini
#: süzebiliyor; ödeme ve kargo tarihi taranmış küme üzerinde uygulanır.
DATE_FIELDS = {
    "created": "Sipariş tarihi",
    "paid": "Ödeme tarihi",
    "shipped": "Kargo tarihi",
}

#: Sipariş numarası biçiminde tanınan yer tutucular. Tanınmayan `{...}` metni
#: OLDUĞU GİBİ kalır: sessizce silmek, personelin biçimi bozduğunu fark
#: etmesini engellerdi.
NO_TOKENS = ("{no}", "{id}", "{year}")

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})


#: Liste ucunun (`GET /orders`) TAŞIMADIĞI alanlara dayanan süzgeçler. Bunlardan
#: biri seçilirse taranan küme sipariş sipariş DETAYLANDIRILIR; detay olmadan
#: süzmek, "kargoya verilmemiş" diyip kargolanmışları da listelemek olurdu.
DETAIL_FILTERS = ("paymentState", "carrier", "customerGroup", "noInvoice", "noShipment",
                  "hasNote", "partialRefund")

#: Detay isteyen çipler: ikisi de gönderi durumuna bakar.
DETAIL_CHIPS = ("shipped", "late")


# ===================================================================== temel

def _folded_key(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def pick(raw: Any, *names: str, default: Any = None) -> Any:
    """Yanıttan alan okur; snake_case ve camelCase yazımların İKİSİNİ de tanır.

    TUZAK — CANLIDA DOĞRULANDI: `/api/admin/orders` `grandTotal`, `createdAt`,
    `customerFirstName` döner; Bagisto'nun veritabanı sütunları ve belgeleri ise
    `grand_total`, `created_at` der. Tek yazıma bağlanan kod hiçbir şey bulamaz
    ve ekran boş görünür — üstelik istisna atmaz, kimse fark etmez. Bu yüzden
    ad, ayraçları atılıp küçük harfe indirilerek eşleştirilir.

    `None` DEĞER YOK sayılır: `couponCode: null` gelen siparişte sıradaki ada
    bakılabilsin diye. Bulunamazsa `default`.

    TUZAK — SIRA ADIN SIRASIDIR, yazımın değil. Önce bütün adları birebir,
    sonra bütün adları indirgenmiş biçimde aramak yanlış olurdu: ödeme
    işlemlerinde `pick(item, "payment_title", "type")` çağrısı `type` alanını
    birebir bulup `paymentTitle`ın önüne geçirir, ekranda "Kredi/Banka Kartı
    ile Öde" yerine `kuveytturk` yazardı.
    """
    if not isinstance(raw, dict):
        return default
    folded: dict[str, Any] | None = None
    for name in names:
        value = raw.get(name)
        if value is not None:
            return value
        if folded is None:
            folded = {_folded_key(str(key)): item for key, item in raw.items()}
        value = folded.get(_folded_key(name))
        if value is not None:
            return value
    return default


def field(raw: Any, *names: str) -> tuple[bool, Any]:
    """Alanı VARLIĞIYLA okur: `(bulundu, değer)`. `None` değer YOKLUK DEĞİLDİR.

    TUZAK: `pick()` burada kullanılamaz — `None` gelen alanı yok sayar ve
    sıradaki ada geçer. Ödeme denemesinin `moneyTaken` alanı ÜÇ DURUMLUDUR ve
    `null` "bilinmiyor" demektir; `pick` ile okunsa `null` "alan yok" sayılır,
    bilinmeyen sessizce başka bir yoldan yeniden türetilirdi. Üç durumlu her
    alan bu yardımcıdan okunur.
    """
    if not isinstance(raw, dict):
        return False, None
    folded: dict[str, Any] | None = None
    for name in names:
        if name in raw:
            return True, raw[name]
        if folded is None:
            folded = {_folded_key(str(key)): value for key, value in raw.items()}
        key = _folded_key(name)
        if key in folded:
            return True, folded[key]
    return False, None


def has_detail(raw: Any) -> bool:
    """Bu kayıt DETAY ucundan mı geldi (`GET /orders/{id}`)?

    Liste ucu fatura/gönderi koleksiyonlarını hiç taşımıyor; detay ucu boş da
    olsa taşıyor. Ayrım önemlidir: sığ bir satırda "faturası yok" demek yanlış
    olurdu — bilgi yoktur, yokluk değil.
    """
    if not isinstance(raw, dict):
        return False
    return isinstance(pick(raw, "invoices"), list) and isinstance(pick(raw, "shipments"), list)


def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: "bugün gelen sipariş" çipi mağazanın
    takvim gününe göre açılıp kapanır ve gece yarısından sonraki üç saatte UTC
    bir gün geride kalır — sabahın ilk siparişleri çipe düşmezdi.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


def text(value: Any) -> str:
    """`None` ve `0` ayrımını koruyarak metne çevirir."""
    if value is None:
        return ""
    return str(value).strip()


def fold(value: Any) -> str:
    """Aksansız, küçük harfli arama anahtarı: `Öğrenci` → `ogrenci`."""
    return text(value).translate(_TR_MAP).lower()


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

    TUZAK: burada `float` kullanılmaz. `float("1234.35") * 100` bazı değerlerde
    123434.99999 verir ve `int()` bir kuruş aşağı yuvarlar. Sipariş toplamında
    bir kuruşluk sapma, gün sonu ciro raporunu tutmaz hâle getirir.
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


def kurus(value: Any) -> int:
    """`to_kurus` ama `None` yerine 0. Toplam hesaplarında kullanılır."""
    return to_kurus(value) or 0


def from_kurus(amount: Any) -> str:
    """Kuruş → telde gidecek ondalık metin: 123435 → `1234.35`."""
    return f"{Decimal(int(amount or 0)) / 100:.2f}"


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def day_of(value: Any) -> str:
    """Zaman damgasının takvim günü (`2026-08-13T09:12:44Z` → `2026-08-13`)."""
    return text(value)[:10]


# ================================================================== durumlar

def status_label(code: str, overrides: dict[str, str] | None = None) -> str:
    """Durum kodunun ekranda görünecek adı.

    Sıra: kullanıcının verdiği ad → varsayılan Türkçe ad → ham kod. Ham koda
    düşmek kasıtlıdır: bilinmeyen bir durum kodu geldiğinde onu "Bekliyor"
    diye göstermek, yanlış bilgiyi doğru gibi sunmak olurdu.
    """
    key = text(code)
    custom = text((overrides or {}).get(key))
    if custom:
        return custom
    return STATUS_LABELS.get(key, key or "—")


def clean_status_names(value: Any) -> dict[str, str]:
    """Ayar ekranından gelen durum adı sözlüğünü temizler.

    YALNIZ bilinen durum kodları kabul edilir: uydurma bir anahtar sözlüğe
    girse hiçbir siparişte görünmez ve kullanıcı adı değiştirdiğini sanırdı.
    """
    out: dict[str, str] = {}
    for key, name in (value or {}).items():
        code = text(key)
        label = text(name)[:40]
        if code in STATUS_LABELS and label:
            out[code] = label
    return out


def payment_state(raw: dict[str, Any]) -> str:
    """Ödeme durumu — Bagisto'da BÖYLE BİR ALAN YOK (TUZAK 1).

    Faturalanan tutar tahsil edilmiş kabul edilir; Bagisto'nun kendi ekranı da
    böyle davranır. İade edilen tutar toplamı yakaladıysa durum `refunded`
    olur: "ödendi" demek, parası geri verilmiş siparişi tahsil edilmiş
    göstermek olurdu.

    LİSTE UCU faturalanan/iade tutarını hiç göndermiyor. O durumda `unknown`
    döner — faturalanan tutarı görmeden "Ödenmedi" yazmak, tahsil edilmiş her
    siparişi ödenmemiş göstermek olurdu ve ekranda hiç fark edilmezdi.
    """
    invoiced_raw = pick(raw, "grand_total_invoiced")
    refunded_raw = pick(raw, "grand_total_refunded")
    if invoiced_raw is None and refunded_raw is None:
        return "unknown"
    total = kurus(pick(raw, "grand_total"))
    invoiced = kurus(invoiced_raw)
    refunded = kurus(refunded_raw)
    if refunded and total and refunded >= total:
        return "refunded"
    if invoiced <= 0:
        return "unpaid"
    if total and invoiced < total:
        return "partial"
    return "paid"


# ============================================================== ödeme kanıtı
#
# NEDEN AYRI BİR KATMAN. `payment_state` yalnız siparişin KENDİ gövdesine bakar
# ve liste ucu faturalanan tutarı hiç göndermez: bütün liste "Bilinmiyor"
# görünür. Bilgi başka yerde DURUYOR — iki ayrı listede:
#
#   (a) `GET /api/admin/invoices`               → `state` ("paid") + siparişin
#                                                 ARTAN NUMARASI
#   (b) `GET /api/admin/bbd/payments/attempts`  → `state` + `order_id`
#
# CANLIDA DOĞRULANDI (18 sipariş · 17 fatura · 17 POS denemesi): iki kaynak
# birbirini doğruluyor. İkisi de LİSTEDİR; sipariş başına istek atılmaz (N+1
# yasak), tek sayfa çekilip bellekte eşlenir.

#: Fatura kaydının "tahsil edildi" durumu. Diğerleri (`pending`,
#: `pending_payment`, `overdue`) TAHSİLAT DEĞİLDİR.
INVOICE_PAID = "paid"

#: POS denemesi durumu → para çekildi mi. Kaynak: mağaza paketindeki
#: `Bbd\ControlApi\Support\PaymentState`. Eşleme ÜÇ DURUMLUDUR ve `None` bir
#: "henüz false" DEĞİLDİR: banka yanıtı gelmemiştir, para çekilmiş olabilir.
POS_MONEY_TAKEN = ("captured", "order_created", "void_required", "reversed")
POS_MONEY_UNKNOWN = ("provisioning", "unknown")
POS_MONEY_NOT_TAKEN = ("initiated", "enrolling", "enrolled", "authenticated",
                       "enroll_failed", "auth_failed", "provision_failed",
                       "abandoned", "superseded")

#: Paranın müşteriye GERİ DÖNDÜĞÜ durum. `taken` ile birlikte okunur: çekilmiş
#: ve iade edilmiş bir tahsilatı "para bizde" ya da "hiç çekilmedi" diye
#: göstermemek için ikisi ayrı tutulur.
POS_REVERSED = "reversed"

#: Ödeme kaynağının ekranda görünen adı.
PAYMENT_SOURCES = {
    "invoice": "fatura kaydı",
    "pos": "sanal POS denemesi",
    "both": "fatura + sanal POS",
    "detail": "sipariş detayı (faturalanan tutar)",
    "none": "kanıt bulunamadı",
    "missing": "kanıt okunamadı",
}


def state_money_taken(state: Any) -> bool | None:
    """POS durum adı → para çekildi mi (True / False / **None = bilinmiyor**).

    TANIMADIĞI DURUM İÇİN `None` DÖNER, `False` DEĞİL. Mağaza paketine yarın
    yeni bir durum eklenirse bu ekran onu "para yok" diye sunmaz; "bilmiyorum"
    der. Fail-safe yön burada `False` değil `None`'dır.
    """
    value = text(state)
    if value in POS_MONEY_TAKEN:
        return True
    if value in POS_MONEY_NOT_TAKEN:
        return False
    return None


def attempt_view(item: Any) -> dict[str, Any]:
    """Bir POS denemesinin bu ekranı ilgilendiren üçlüsü.

    `moneyTaken` alanı yanıtta VARSA o kullanılır — eşlemenin sahibi mağaza
    paketidir, biz kopyasını tutarız. Alan gelmezse durum adından çıkarılır;
    iki yol da aynı üç kovaya düşer.
    """
    state = text(pick(item, "state"))
    found, raw = field(item, "money_taken")
    taken = (None if raw is None else bool(raw)) if found else state_money_taken(state)
    found_back, raw_back = field(item, "money_returned")
    returned = bool(raw_back) if found_back else state == POS_REVERSED
    return {"orderId": as_int(pick(item, "order_id")), "state": state,
            "taken": taken, "returned": returned}


def window_floor(items: Any, *names: str) -> str:
    """Okunan kayıtların EN ESKİ günü — kanıt penceresinin dibi.

    Tek sayfa çekiliyor (uç 50'de kırpıyor). Sayfa dolduysa daha eski kayıtlar
    OKUNMAMIŞTIR ve o kayıtların yokluğu "ödeme yok" anlamına gelmez. Pencerenin
    dibinden ESKİ ya da AYNI GÜNDEKİ siparişler için "kanıt yok" sonucuna
    varılmaz — o satırlar "Bilinmiyor" kalır.

    PENCERE ANCAK LİSTE YENİDEN ESKİYE SIRALIYSA GEÇERLİDİR ve bu VERİDEN
    doğrulanır, istekten varsayılmaz: `sort=id&order=desc` gönderiyoruz ama
    Laravel tanımadığı parametreyi sessizce yok sayar. Liste eskiden yeniye
    gelseydi elimizdeki 50 kayıt EN ESKİ 50 olurdu; o durumda "pencerenin
    dibinden yeni sipariş" ölçütü tam ters çalışır ve okunmamış faturaları
    "yok" saymamıza yol açardı. Sıra doğrulanamazsa pencere YOKTUR ("").
    """
    days = [day_of(pick(item, *names)) for item in items or [] if isinstance(item, dict)]
    days = [day for day in days if day]
    if not days:
        return ""
    if any(days[index] > days[index - 1] for index in range(1, len(days))):
        return ""
    return days[-1]


def payment_index(invoices: Any, attempts: Any) -> dict[str, Any]:
    """İki listeden sipariş başına kanıt haritası. SİPARİŞ BAŞINA İSTEK YOK."""
    by_no: dict[str, dict[str, Any]] = {}
    by_id: dict[int, dict[str, Any]] = {}
    unbound_invoices = 0

    for item in invoices or []:
        if not isinstance(item, dict):
            continue
        slot = {"count": 0, "paid": False, "states": []}
        # TUZAK — CANLIDA DOĞRULANDI: fatura kaydında `orderId` NULL geliyor.
        # Bağ `orderIncrementId` üzerinden kurulur. `orderId` ile eşleştirmeye
        # kalkan kod HİÇBİR faturayı bağlayamaz, hata da vermez ve ekranda
        # HERKES "Ödenmedi" görünür.
        number = text(pick(item, "order_increment_id"))
        order_id = as_int(pick(item, "order_id"))
        if number:
            slot = by_no.setdefault(number, slot)
        elif order_id:
            slot = by_id.setdefault(order_id, slot)
        else:
            unbound_invoices += 1
            continue
        slot["count"] += 1
        state = text(pick(item, "state"))
        slot["states"].append(state)
        slot["paid"] = slot["paid"] or state == INVOICE_PAID

    by_order: dict[int, dict[str, Any]] = {}
    unbound_attempts = 0
    for item in attempts or []:
        if not isinstance(item, dict):
            continue
        view = attempt_view(item)
        if not view["orderId"]:
            # Siparişi olmayan deneme (ör. `void_required`) bu ekranın işi
            # değildir — Sanal POS ekranı onunla ilgilenir. Sessizce yutulmaz,
            # sayılır.
            unbound_attempts += 1
            continue
        slot = by_order.setdefault(view["orderId"],
                                   {"count": 0, "taken": False, "unknown": False,
                                    "returned": False, "states": []})
        slot["count"] += 1
        slot["states"].append(view["state"])
        if view["taken"] is None:
            slot["unknown"] = True
        elif view["taken"] and view["returned"]:
            slot["returned"] = True
        elif view["taken"]:
            slot["taken"] = True

    return {
        "invoiceByNo": by_no, "invoiceById": by_id, "attemptByOrder": by_order,
        "invoiceFloorDay": window_floor(invoices, "created_at"),
        "attemptFloorDay": window_floor(attempts, "created_at"),
        "unboundInvoices": unbound_invoices, "unboundAttempts": unbound_attempts,
        # Kaynak durumu servisin işidir; burada iyimser başlar.
        "invoiceOk": True, "attemptOk": True,
        "invoiceComplete": True, "attemptComplete": True,
        "warnings": [],
    }


def empty_index() -> dict[str, Any]:
    """Kanıtsız harita: hiçbir satırı ne yükseltir ne düşürür."""
    index = payment_index([], [])
    index["invoiceOk"] = False
    index["attemptOk"] = False
    return index


def evidence_of(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    """Bir siparişin ham kanıtı: kaç fatura, kaç deneme, hangi durumda."""
    number = text(row.get("incrementId"))
    order_id = as_int(row.get("id"))
    invoice = (index.get("invoiceByNo", {}).get(number)
               or index.get("invoiceById", {}).get(order_id)
               or {"count": 0, "paid": False, "states": []})
    attempt = index.get("attemptByOrder", {}).get(order_id) or {
        "count": 0, "taken": False, "unknown": False, "returned": False, "states": []}
    return {
        "invoiceCount": invoice["count"], "invoicePaid": bool(invoice["paid"]),
        "invoiceStates": list(invoice["states"]),
        "attemptCount": attempt["count"], "taken": bool(attempt["taken"]),
        "unknown": bool(attempt["unknown"]), "returned": bool(attempt["returned"]),
        "attemptStates": list(attempt["states"]),
    }


def evidence_covers(row: dict[str, Any], index: dict[str, Any]) -> bool:
    """Bu satır için "kanıt yok, demek ki ödenmedi" sonucuna VARILABİLİR Mİ?

    İKİ KAYNAĞIN İKİSİ İÇİN de aynı iki koşul aranır: kaynak okunabilmiş olmalı
    ve ya sayfa kırpılmamış olmalı ya da sipariş kanıt penceresinin dibinden
    YENİ olmalı. Biri bile tutmuyorsa satır "Bilinmiyor" kalır — kaydın
    yokluğunu kanıt saymak, kanıtı hiç okumamakla aynı sonucu verirdi.
    """
    for ok_key, complete_key, floor_key in (
            ("invoiceOk", "invoiceComplete", "invoiceFloorDay"),
            ("attemptOk", "attemptComplete", "attemptFloorDay")):
        if not index.get(ok_key):
            return False
        if index.get(complete_key):
            continue
        floor = text(index.get(floor_key))
        # Fatura sipariş TARİHİNDEN SONRA, POS denemesi ise ÖNCE oluşur; iki
        # yönü de kapsamak için pencerenin dibindeki GÜN tamamen dışarıda
        # bırakılır.
        if not floor or text(row.get("createdDay")) <= floor:
            return False
    return True


def payment_view(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    """Satırın ödeme durumunu iki DIŞ kaynakla yeniden çözer.

    SIRA (görev tanımı): fatura `paid` VEYA POS `moneyTaken=true` → Ödendi ·
    POS `moneyTaken=null` → Belirsiz · ikisi de yok → Ödenmedi.

    EN SERT KURAL: `unknown` ve `provisioning` ASLA "Ödenmedi" yazılmaz.

    SİPARİŞİN KENDİ TUTAR DÖKÜMÜ KANITTAN KESKİNDİR ve korunur: `paid`,
    `partial`, `refunded` durumları kaç kuruşun faturalandığını/iade edildiğini
    bilir, kanıt yalnız "bir kayıt var" der. Tamamı faturalanmış bir siparişi
    havada kalmış tek bir POS denemesi yüzünden "Belirsiz" göstermek, tahsil
    edilmiş malı sevkiyattan alıkoyardı; o deneme yine de `paymentAttention`
    ile işaretlenir ve satırda yazılır.
    """
    evidence = evidence_of(row, index)
    current = text(row.get("paymentState")) or "unknown"
    sharper = current in ("paid", "partial", "refunded")
    covered = evidence_covers(row, index)
    source = "detail" if row.get("detailed") else "none"
    note = ""

    if sharper:
        state = current
        source = "detail"
        note = "Tutar dökümünden hesaplandı: faturalanan ve iade edilen tutar karşılaştırıldı."
        if evidence["unknown"]:
            note += (" AYRICA bankadan kesin yanıt gelmemiş bir POS denemesi var "
                     f"({', '.join(evidence['attemptStates'])}): bu sipariş için ikinci bir "
                     "çekim olmuş OLABİLİR, Sanal POS ekranından bakılmalı.")
    elif evidence["invoicePaid"] or evidence["taken"]:
        state = "paid"
        source = ("both" if evidence["invoicePaid"] and evidence["taken"]
                  else "invoice" if evidence["invoicePaid"] else "pos")
        note = _paid_note(evidence)
    elif evidence["unknown"]:
        state = "uncertain"
        source = "pos"
        note = ("Bankadan kesin yanıt gelmemiş bir POS denemesi var "
                f"({', '.join(evidence['attemptStates']) or 'durum yok'}). Para çekilmiş "
                "OLABİLİR; bu satıra “Ödenmedi” yazmak müşteriden ikinci kez tahsilat "
                "istemeye yol açardı. Mutabakat Sanal POS ekranından yapılır.")
    elif evidence["returned"]:
        state = "refunded"
        source = "pos"
        note = "Sanal POS denemesi iade/iptal edilmiş; para müşteriye geri dönmüş görünüyor."
    elif current == "unknown":
        state = "unpaid" if covered else "unknown"
        note = (_unpaid_note(index) if covered else _blind_note(index))
        source = "none" if covered else "missing"
    else:
        state = current
        if current == "unpaid" and not index.get("attemptOk"):
            # Fatura yok diyoruz ama POS'a bakamadık: havada kalmış bir deneme
            # olabileceğini SÖYLEMEK zorundayız.
            note = ("Faturası yok; sanal POS denemeleri OKUNAMADI, bu yüzden bankada "
                    "havada kalmış bir tahsilat olup olmadığı bilinmiyor.")
            source = "missing"

    return {
        "paymentState": state,
        "paymentLabel": PAYMENT_LABELS[state],
        "paymentTone": PAYMENT_TONES[state],
        "paymentSource": PAYMENT_SOURCES[source],
        "paymentNote": note,
        # Bankaya sorulması gereken satır: rengi ayrı, açıklaması satırda.
        "paymentAttention": bool(evidence["unknown"]),
        "paymentEvidence": evidence,
    }


def _paid_note(evidence: dict[str, Any]) -> str:
    parts = []
    if evidence["invoicePaid"]:
        parts.append(f"{evidence['invoiceCount']} fatura kaydı “paid”")
    if evidence["taken"]:
        parts.append("POS denemesi parayı çekmiş "
                     f"({', '.join(evidence['attemptStates']) or 'durum yok'})")
    return "Tahsilat kanıtı: " + " · ".join(parts) + "."


def _unpaid_note(index: dict[str, Any]) -> str:
    return ("Ne tahsil edilmiş fatura ne de para çeken bir POS denemesi bulundu. "
            f"{len(index.get('invoiceByNo', {})) + len(index.get('invoiceById', {}))} "
            "siparişin faturası ve "
            f"{len(index.get('attemptByOrder', {}))} siparişin POS denemesi tarandı.")


def _blind_note(index: dict[str, Any]) -> str:
    problems = list(index.get("warnings") or [])
    if not problems and not (index.get("invoiceOk") and index.get("attemptOk")):
        problems.append("fatura ve POS listeleri okunamadı")
    if not index.get("invoiceComplete") or not index.get("attemptComplete"):
        problems.append(
            "kanıt listesi tek sayfada bitmedi; bu siparişten daha eski kayıtlar "
            "okunmadı (fatura penceresi "
            f"{index.get('invoiceFloorDay') or '—'}, POS penceresi "
            f"{index.get('attemptFloorDay') or '—'} gününde bitiyor)")
    return ("Ödeme kanıtı bu sipariş için toplanamadı: " + " · ".join(problems)
            + ". “Ödenmedi” demek tahsil edilmiş siparişi ödenmemiş göstermek olurdu.")


def with_payment(row: dict[str, Any], index: dict[str, Any]) -> dict[str, Any]:
    """Satırı kanıtla yeniden çözülmüş ödeme durumuyla döndürür (yeni sözlük)."""
    out = {**row, **payment_view(row, index)}
    out["shipBlock"] = ship_block(out)
    out["shipNote"] = ship_note(out)
    return out


def fulfilment(done: int, ordered: int) -> str:
    """Kısmi fatura/kargo olağandır (TUZAK 7): üç durum, ikili değil."""
    if ordered <= 0 or done <= 0:
        return "none"
    if done >= ordered:
        return "full"
    return "partial"


def order_no(raw: dict[str, Any], fmt: str = "") -> str:
    """Ekranda görünen sipariş numarası (TUZAK 3).

    `increment_id` müşterinin gördüğü numaradır; `id` iç anahtardır. Eylem
    uçlarına HER ZAMAN `id` gider — numarayı uca göndermek, mağaza numara
    biçimini değiştirdiği gün sessizce yanlış siparişe dokunmak demektir.
    """
    number = text(pick(raw, "increment_id")) or text(pick(raw, "id"))
    template = text(fmt) or "#{no}"
    created = day_of(pick(raw, "created_at"))
    return (template
            .replace("{no}", number)
            .replace("{id}", text(pick(raw, "id")))
            .replace("{year}", created[:4] or ""))


def format_error(fmt: str) -> str:
    """Sipariş no biçimi kullanılabilir mi?"""
    value = text(fmt)
    if not value:
        return ""
    if len(value) > 40:
        return "Sipariş no biçimi en çok 40 karakter olabilir."
    if not any(token in value for token in NO_TOKENS):
        return ("Biçim en az bir yer tutucu içermeli: {no} (sipariş numarası), "
                "{id} (iç kimlik) ya da {year}.")
    unknown = [item for item in re.findall(r"\{[a-z]+\}", value) if item not in NO_TOKENS]
    if unknown:
        return f"Tanınmayan yer tutucu: {', '.join(sorted(set(unknown)))}"
    return ""


# =================================================================== adresler

def address_line(raw: Any) -> str:
    """Adresi tek satıra indirir. Eksik parça ATLANIR (TUZAK 9)."""
    if not isinstance(raw, dict):
        return ""
    street = pick(raw, "address")
    parts = [
        " ".join(text(item) for item in street if text(item)) if isinstance(street, list) else
        text(pick(raw, "address1", "address")),
        text(pick(raw, "postcode")),
        text(pick(raw, "city")),
        text(pick(raw, "state")),
        text(pick(raw, "country_name", "country")),
    ]
    return ", ".join(part for part in parts if part)


#: Bagisto'nun adres türü etiketleri (`addresses[].addressType`).
ADDRESS_TYPES = {"billing": "order_billing", "shipping": "order_shipping"}


def address_of(raw: dict[str, Any], kind: str) -> Any:
    """Fatura/teslimat adresini iki farklı gövde biçiminden de çıkarır.

    CANLIDA: `GET /orders/{id}` adresleri `addresses: [{addressType:
    "order_billing"|"order_shipping", …}]` dizisinde veriyor; `billing_address`
    / `shipping_address` diye bir alan YOK. Eski (ve belgelenen) biçim de
    korunur: geçit ya da sürüm değişirse ekran adresi kaybetmesin.
    """
    flat = pick(raw, f"{kind}_address")
    if isinstance(flat, dict):
        return flat
    wanted = ADDRESS_TYPES.get(kind, kind)
    for item in pick(raw, "addresses", default=[]) or []:
        if isinstance(item, dict) and text(pick(item, "address_type")) == wanted:
            return item
    return None


def nested_name(raw: dict[str, Any], key: str, flat: str) -> str:
    """`{"customer_group": {"name": …}}` ya da `{"customer_group_name": …}`.

    Bagisto admin API'si ilişkili kaydı bazen gömülü nesne, bazen düzleştirilmiş
    ad alanı olarak veriyor; hangisinin geleceği uca ve sürüme göre değişiyor.
    CANLIDA sipariş detayı üçüncü bir yol kullanıyor: `customer.group.name`.
    """
    nested = pick(raw, key)
    if isinstance(nested, dict) and text(pick(nested, "name")):
        return text(pick(nested, "name"))
    direct = text(pick(raw, flat))
    if direct:
        return direct
    customer = pick(raw, "customer")
    if isinstance(customer, dict):
        group = pick(customer, "group", "customer_group")
        if isinstance(group, dict):
            return text(pick(group, "name"))
    return ""


def field_of(*sources: Any, key: str) -> str:
    """İlk dolu adres parçası. Teslimat adresi yoksa fatura adresine düşer."""
    for source in sources:
        if isinstance(source, dict) and text(pick(source, key)):
            return text(pick(source, key))
    return ""


def location_city(value: Any) -> str:
    """Liste satırındaki `location` alanının ŞEHİR parçası.

    CANLIDA liste ucu adres taşımıyor ama `"Selçuklu, 42, TR"` biçiminde bir
    `location` özeti veriyor; ilk parça şehirdir. Şehir süzgeci sığ satırlarda
    bunu kullanır — yoksa süzgeç hep boş dönerdi.
    """
    head = text(value).split(",")[0].strip()
    return head


def address_view(raw: Any) -> dict[str, Any]:
    """Çekmecenin özet sekmesinin gösterdiği adres künyesi."""
    if not isinstance(raw, dict):
        return {"name": "", "line": "", "phone": "", "city": "", "company": ""}
    name = " ".join(part for part in (text(pick(raw, "first_name")),
                                      text(pick(raw, "last_name"))) if part)
    return {
        "name": name or text(pick(raw, "name")),
        "line": address_line(raw),
        "phone": text(pick(raw, "phone")),
        "city": text(pick(raw, "city")),
        "company": text(pick(raw, "company_name", "company")),
    }


# ============================================================== gönderi/fatura

def shipment_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Siparişin ÜZERİNDEKİ gönderi kayıtları (Bagisto'nun kendi kaydı).

    Taşıyıcıdaki gerçek hareketler burada DEĞİL; onlar `store.shipment.byOrder`
    yeteneğinden gelir ve o yetenek yoksa kargo sekmesi hiç açılmaz.
    """
    out: list[dict[str, Any]] = []
    for item in pick(raw, "shipments", default=[]) or []:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": as_int(pick(item, "id")),
            "carrier": text(pick(item, "carrier_title", "carrier_code")),
            "track": text(pick(item, "track_number")),
            "quantity": as_int(pick(item, "total_qty")),
            "createdAt": text(pick(item, "created_at"))[:19],
        })
    return out


def invoice_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in pick(raw, "invoices", default=[]) or []:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": as_int(pick(item, "id")),
            "no": text(pick(item, "increment_id")) or text(pick(item, "id")),
            "state": text(pick(item, "state")),
            "total": kurus(pick(item, "grand_total")),
            "createdAt": text(pick(item, "created_at"))[:19],
        })
    return out


def refund_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in pick(raw, "refunds", default=[]) or []:
        if not isinstance(item, dict):
            continue
        out.append({
            "id": as_int(pick(item, "id")),
            "no": text(pick(item, "increment_id")) or text(pick(item, "id")),
            "total": kurus(pick(item, "grand_total")),
            "createdAt": text(pick(item, "created_at"))[:19],
        })
    return out


def item_rows(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Sipariş kalemleri. Faturalanan/kargolanan adet kalem bazında taşınır:
    kısmi fatura ve kısmi kargo bu ekranın günlük işidir."""
    out: list[dict[str, Any]] = []
    for item in pick(raw, "items", default=[]) or []:
        if not isinstance(item, dict):
            continue
        ordered = as_int(pick(item, "qty_ordered"))
        out.append({
            "id": as_int(pick(item, "id")),
            "sku": text(pick(item, "sku")),
            "name": text(pick(item, "name")) or "(adsız)",
            "quantity": ordered,
            "invoiced": as_int(pick(item, "qty_invoiced")),
            "shipped": as_int(pick(item, "qty_shipped")),
            "refunded": as_int(pick(item, "qty_refunded")),
            "canceled": as_int(pick(item, "qty_canceled")),
            "unitPrice": kurus(pick(item, "price")),
            "discount": kurus(pick(item, "discount_amount")),
            "tax": kurus(pick(item, "tax_amount")),
            "total": kurus(pick(item, "total", "base_total")),
        })
    return out


def money_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Toplam dökümü. Her kalem AYRI gösterilir: tek "toplam" rakamı,
    "kargo neden bu kadar" sorusunu ekrandan dışarı taşırdı."""
    return {
        "subTotal": kurus(pick(raw, "sub_total")),
        "shipping": kurus(pick(raw, "shipping_amount")),
        "discount": kurus(pick(raw, "discount_amount")),
        "tax": kurus(pick(raw, "tax_amount")),
        "grandTotal": kurus(pick(raw, "grand_total")),
        "invoiced": kurus(pick(raw, "grand_total_invoiced")),
        "refunded": kurus(pick(raw, "grand_total_refunded")),
    }


# =============================================================== liste satırı

def order_row(raw: dict[str, Any], *, status_names: dict[str, str] | None = None,
              no_format: str = "", today: str = "", late_days: int = 3) -> dict[str, Any]:
    """Ham sipariş kaydı → tablonun beklediği satır. Para her yerde kuruş."""
    day = today or today_iso()
    status = text(pick(raw, "status"))
    items = item_rows(raw)
    shipments = shipment_rows(raw)
    invoices = invoice_rows(raw)
    detailed = has_detail(raw)

    ordered_qty = as_int(pick(raw, "total_qty_ordered")) or sum(row["quantity"] for row in items)
    invoiced_qty = as_int(pick(raw, "total_qty_invoiced")) or sum(row["invoiced"] for row in items)
    shipped_qty = as_int(pick(raw, "total_qty_shipped")) or sum(row["shipped"] for row in items)

    billing = address_of(raw, "billing")
    shipping = address_of(raw, "shipping")
    # Sipariş adı üç yoldan gelebiliyor: detayda ayrı ad/soyad, listede
    # birleşik `customerName`, misafir siparişinde ikisi de boş.
    customer = " ".join(part for part in (text(pick(raw, "customer_first_name")),
                                          text(pick(raw, "customer_last_name"))) if part)
    customer = customer or text(pick(raw, "customer_name"))
    payment = pick(raw, "payment") if isinstance(pick(raw, "payment"), dict) else {}
    created = day_of(pick(raw, "created_at"))

    money = money_view(raw)
    state = payment_state(raw)
    ship_state = fulfilment(shipped_qty, ordered_qty)
    invoice_state = fulfilment(invoiced_qty, ordered_qty)

    row = {
        "id": as_int(pick(raw, "id")),
        "orderNo": order_no(raw, no_format),
        "incrementId": text(pick(raw, "increment_id")),
        "createdAt": text(pick(raw, "created_at"))[:19],
        "createdDay": created,
        "status": status,
        "statusLabel": status_label(status, status_names),
        "statusTone": STATUS_TONES.get(status, ""),
        "customer": customer or text(pick(raw, "customer_email")) or "(misafir)",
        "email": text(pick(raw, "customer_email")),
        "phone": field_of(shipping, billing, key="phone"),
        "customerGroup": nested_name(raw, "customer_group", "customer_group_name"),
        "channel": text(pick(raw, "channel_name", "channel_code")),
        "city": (field_of(shipping, billing, key="city")
                 or location_city(pick(raw, "location"))),
        "coupon": text(pick(raw, "coupon_code")),
        # Detayda `paymentTitle` düz alandır; eski/gömülü biçim de korunur.
        "paymentMethod": text(pick(raw, "payment_title", "payment_method")
                              or pick(payment, "method_title", "method")),
        "paymentState": state,
        "paymentLabel": PAYMENT_LABELS[state],
        "paymentTone": PAYMENT_TONES[state],
        # Kanıt katmanı (`with_payment`) bu üçünü doldurur; satır kanıtsız
        # çizilirse alanlar VAR ama boştur — ekranın `undefined` okumaması için.
        "paymentSource": PAYMENT_SOURCES["detail" if detailed else "none"],
        "paymentNote": "",
        "paymentAttention": False,
        "itemCount": as_int(pick(raw, "total_item_count")) or len(items),
        "quantity": ordered_qty,
        **money,
        "invoiceState": invoice_state,
        "invoiceLabel": FULFIL_LABELS[invoice_state],
        "invoiceCount": len(invoices),
        "shipmentState": ship_state,
        "shipmentLabel": FULFIL_LABELS[ship_state],
        "shipmentCount": len(shipments),
        "carrier": shipments[0]["carrier"] if shipments else "",
        "track": shipments[0]["track"] if shipments else "",
        "shippedDay": day_of(shipments[0]["createdAt"]) if shipments else "",
        "paidDay": day_of(invoices[0]["createdAt"]) if invoices else "",
        "refundedTotal": money["refunded"],
        "noteCount": len(pick(raw, "comments", default=[]) or []),
        "risky": status == "fraud",
        "lateDays": late_of(created, day) if _open_for_shipping(status, ship_state) else 0,
        "late": (detailed and _open_for_shipping(status, ship_state)
                 and late_of(created, day) >= late_days),
        # Sığ liste satırında gönderi/fatura/not bilgisi YOKTUR. Ekran ve
        # süzgeçler bunu bilmeli: "kargolanmamış" ile "bilinmiyor" ayrı şeyler.
        "detailed": detailed,
    }
    # Toplu kargo uygunluğu satırın KENDİSİNDE durur: ekran onay kutusunu buna
    # göre kapatır ve NEDENİ aynı satırda yazar.
    row["shipBlock"] = ship_block(row)
    row["shipNote"] = ship_note(row)
    return row


def _open_for_shipping(status: str, ship_state: str) -> bool:
    """Kargolanmayı bekleyen sipariş mi? İptal/kapalı sipariş gecikmez."""
    return status not in ("canceled", "closed") and ship_state != "full"


def late_of(created_day: str, today: str) -> int:
    """Siparişin üzerinden geçen tam gün sayısı. Çözülemezse 0."""
    try:
        start = datetime.fromisoformat(created_day).date()
        now = datetime.fromisoformat(today).date()
    except ValueError:
        return 0
    return max(0, (now - start).days)


# ================================================================= süzgeçler

#: Anahtar süzgeçler (`Faturası kesilmemiş` gibi) → satır üzerinde bir yordam.
#: `risky` dışındakiler DETAY ister; sığ satırda fatura/gönderi/not bilgisi hiç
#: yoktur ve `row.get("detailed")` denetimi olmadan "faturası kesilmemiş" diye
#: her siparişi listelerdik.
FLAG_TESTS = {
    "noInvoice": lambda row: bool(row.get("detailed")) and row["invoiceState"] != "full",
    "noShipment": lambda row: bool(row.get("detailed")) and row["shipmentState"] != "full",
    "hasNote": lambda row: bool(row.get("detailed")) and row["noteCount"] > 0,
    "risky": lambda row: bool(row["risky"]),
    "partialRefund": lambda row: (bool(row.get("detailed"))
                                  and 0 < row["refunded"] < row["grandTotal"]),
}

CHIP_TESTS = {
    "pending": lambda row, today: row["status"] in ("pending", "pending_payment"),
    "processing": lambda row, today: row["status"] == "processing",
    "shipped": lambda row, today: bool(row.get("detailed")) and row["shipmentState"] != "none"
    and row["status"] not in ("canceled", "closed"),
    "today": lambda row, today: row["createdDay"] == today,
    "late": lambda row, today: bool(row["late"]),
    "canceled": lambda row, today: row["status"] in ("canceled", "closed"),
}


def date_value(row: dict[str, Any], field: str) -> str:
    """Tarih süzgecinin bakacağı gün. Bilinmeyen alan sipariş tarihine düşer."""
    if field == "paid":
        return row.get("paidDay") or ""
    if field == "shipped":
        return row.get("shippedDay") or ""
    return row.get("createdDay") or ""


def matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Mağazanın uygulayamadığı süzgeçler — TARANMIŞ küme üzerinde (TUZAK 5).

    Sunucuya gönderip uygulandığını varsaymak yanlış olurdu: Laravel tanımadığı
    sorgu parametresini sessizce yok sayar ve süzülmemiş listeyi süzülmüş gibi
    gösterirdik.
    """
    needle = fold(filters.get("q"))
    if needle:
        haystack = " ".join(fold(row.get(key)) for key in
                            ("orderNo", "incrementId", "customer", "email", "phone", "track"))
        if needle not in haystack:
            return False

    for key, column in (("paymentState", "paymentState"), ("channel", "channel"),
                        ("customerGroup", "customerGroup")):
        wanted = text(filters.get(key))
        if wanted and text(row.get(column)) != wanted:
            return False

    for key, column in (("paymentMethod", "paymentMethod"), ("carrier", "carrier"),
                        ("city", "city"), ("coupon", "coupon")):
        wanted = fold(filters.get(key))
        if wanted and wanted not in fold(row.get(column)):
            return False

    status = text(filters.get("status"))
    if status and text(row.get("status")) != status:
        return False

    field = text(filters.get("dateField")) or "created"
    day = date_value(row, field)
    start = text(filters.get("dateFrom"))
    end = text(filters.get("dateTo"))
    if (start or end) and not day:
        # Ödeme/kargo tarihi olmayan sipariş, o alana göre süzülünce DIŞARIDA
        # kalır. "Boş = her aralığa uyar" saymak, kargolanmamış siparişleri
        # kargo tarihi aralığının içinde göstermek olurdu.
        return False
    if start and day < start:
        return False
    if end and day > end:
        return False

    low = filters.get("totalMin")
    high = filters.get("totalMax")
    if low is not None and row.get("grandTotal", 0) < int(low):
        return False
    if high is not None and row.get("grandTotal", 0) > int(high):
        return False

    for flag, test in FLAG_TESTS.items():
        if filters.get(flag) and not test(row):
            return False

    chip = text(filters.get("chip"))
    if chip:
        test_chip = CHIP_TESTS.get(chip)
        if test_chip is None or not test_chip(row, text(filters.get("today")) or today_iso()):
            return False
    return True


def needs_scan(filters: dict[str, Any]) -> bool:
    """Bu süzgeç kümesi mağazanın sayfalı ucuyla karşılanabilir mi?

    Karşılanamıyorsa küme TARANIR (tavanlı) ve süzme burada yapılır. Karar
    açıkça verilir: "belki uygulanmıştır" diye geçmek, kullanıcıya eksik liste
    göstermenin en sessiz yoludur.
    """
    if text(filters.get("chip")):
        return True
    if any(filters.get(flag) for flag in FLAG_TESTS):
        return True
    # `channel` de buradadır: modül ayarındaki kanal KODU her isteğe gider ama
    # kullanıcının şeritten seçtiği değer kanalın ADIdır. İkisini aynı sorgu
    # parametresine koymak, kanal adını kod sanan bir uca yanlış değer
    # göndermek olurdu — süzgeç sessizce yok sayılırdı.
    for key in ("paymentState", "paymentMethod", "carrier", "customerGroup", "city", "coupon",
                "channel"):
        if text(filters.get(key)):
            return True
    if text(filters.get("dateField")) not in ("", "created"):
        return True
    # Arama mağazada yalnız e-posta/müşteri adına bakıyor; takip numarası ve
    # sipariş numarası aramayı burada karşılamak gerekiyor.
    return bool(text(filters.get("q")))


def needs_detail(filters: dict[str, Any]) -> bool:
    """Taranan küme sipariş sipariş DETAYLANDIRILMALI mı?

    Liste ucu fatura, gönderi, not, faturalanan tutar ve müşteri grubu
    taşımıyor (canlıda doğrulandı). Bunlara dayanan bir süzgeç seçildiğinde
    sığ satırla karar vermek yanlış liste üretir; detay çekilir. Detay pahalıdır
    (sipariş başına bir istek), bu yüzden yalnız GEREKİYORSA yapılır.
    """
    for key in DETAIL_FILTERS:
        value = filters.get(key)
        if value is True or text(value):
            return True
    if text(filters.get("chip")) in DETAIL_CHIPS:
        return True
    if text(filters.get("dateField")) in ("paid", "shipped"):
        return True
    # Arama takip numarası ve telefonu da kapsıyor; ikisi de yalnız detayda var.
    return bool(text(filters.get("q")))


def store_filters(filters: dict[str, Any]) -> dict[str, Any]:
    """Mağazanın gerçekten uyguladığı süzgeçleri ayıklar (TUZAK 5)."""
    out: dict[str, Any] = {}
    status = text(filters.get("status"))
    if status:
        out["status"] = status
    # Kanal BİLEREK yok. CANLIDA DOĞRULANDI: `/orders?channel=` parametresi
    # kanal KODUNU değil KİMLİĞİNİ (tamsayı) bekliyor — `channel=default` sıfır
    # kayıt, `channel=1` on yedi kayıt döndürüyor. Kod göndermek listeyi tamamen
    # boşaltır ve hata da vermez. Kimlik servisin işidir (`channel_id` ayarı);
    # kullanıcının şeritten seçtiği kanal ADI taranmış küme üzerinde süzülür.
    if text(filters.get("dateField")) in ("", "created"):
        start = text(filters.get("dateFrom"))
        end = text(filters.get("dateTo"))
        if start:
            out["date_from"] = start
        if end:
            out["date_to"] = end
    low = filters.get("totalMin")
    high = filters.get("totalMax")
    if low is not None:
        out["grand_total_from"] = from_kurus(low)
    if high is not None:
        out["grand_total_to"] = from_kurus(high)
    return out


# ================================================================ toplulaştırma

def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Süzgecin mini toplamı: adet · ciro · ortalama sepet.

    İPTAL VE KAPALI SİPARİŞLER CİROYA GİRMEZ. Girseydi "bugün ne sattık"
    sorusunun cevabı, iptal edilen siparişler yüzünden her gün fazla çıkardı.
    """
    live = [row for row in rows if row.get("status") not in ("canceled", "closed")]
    revenue = sum(row.get("grandTotal", 0) for row in live)
    return {
        "count": len(rows),
        "liveCount": len(live),
        "revenue": revenue,
        "average": revenue // len(live) if live else 0,
        "canceled": len(rows) - len(live),
    }


def chip_counts(rows: list[dict[str, Any]], today: str = "") -> dict[str, int]:
    """Durum çiplerinin sayaçları. Renk tek başına anlam taşımasın diye
    her çipin yanında bu sayı yazılır."""
    day = today or today_iso()
    return {key: sum(1 for row in rows if test(row, day)) for key, test in CHIP_TESTS.items()}


# ============================================================ yazma kapıları

def cancel_block(row: dict[str, Any], *, window_hours: int = 0, now: str = "") -> str:
    """Sipariş iptal edilebilir mi (TUZAK 8)? Engel varsa GÖSTERİLECEK metin.

    İptal geri alınamaz. İki kapı vardır: durum (zaten iptal/kapalı ya da
    kargolanmış sipariş iptal edilmez) ve süre penceresi (yönetici ayarlar;
    0 = sınırsız).
    """
    status = text(row.get("status"))
    if status in ("canceled", "closed"):
        return "Sipariş zaten iptal/kapalı."
    if row.get("shipmentState") == "full":
        return ("Sipariş tamamen kargolanmış; iptal yerine iade açılmalı "
                "(İadeler ekranı).")
    if window_hours <= 0:
        return ""
    created = text(row.get("createdAt"))
    if not created:
        return ""
    try:
        started = datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return ""
    # TUZAK: mağaza `createdAt` değerini "2026-08-13 18:27:17" biçiminde, SAAT
    # DİLİMİ OLMADAN veriyor ve bu YEREL saattir (mağaza +03). UTC saymak
    # siparişi üç saat yaşlandırır ve iptal penceresini erken kapatırdı.
    local = datetime.now(UTC).astimezone().tzinfo or UTC
    if started.tzinfo is None:
        started = started.replace(tzinfo=local)
    moment = datetime.fromisoformat(now) if now else datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=local)
    if moment - started > timedelta(hours=window_hours):
        return (f"İptal penceresi kapandı: sipariş {window_hours} saatten eski. "
                "Süre Ayarlar sekmesinden değiştirilebilir.")
    return ""


def ship_block(row: dict[str, Any]) -> str:
    """Bu sipariş TOPLU kargoya verilebilir mi? Engel varsa GÖSTERİLECEK metin.

    Ekran onay kutusunu buna göre kapatır: seçilemeyen satırın nedeni aynı
    satırda yazılıdır. "Neden seçemiyorum" sorusunu diyaloğa saklamak, işi yapan
    kişiye geç kalmış bir açıklamadır.

    ÖDEME BELİRSİZKEN KARGO ÇIKMAZ. `uncertain` satır "ödenmedi" değildir ama
    "ödendi" de değildir; mal çıkarmadan önce bankayla mutabakat gerekir. Tek
    sipariş çekmeceden yine kargolanabilir — orada karar veren kişi siparişin
    tamamını görüyor.
    """
    if row.get("status") in ("canceled", "closed"):
        return "Sipariş iptal/kapalı."
    if row.get("shipmentState") == "full":
        return "Zaten tamamı kargolanmış."
    state = text(row.get("paymentState"))
    if state == "uncertain":
        return ("Ödeme belirsiz: bankadan yanıt gelmemiş bir POS denemesi var, "
                "para çekilmiş olabilir de olmayabilir de. Mutabakat bitmeden mal çıkmaz.")
    if state == "refunded":
        return "Parası iade edilmiş; kargoya verilmez."
    if state == "unpaid":
        return "Ödenmedi: tahsilat kaydı yok, önce fatura kesilmeli."
    if row.get("detailed") and row.get("invoiceState") == "none":
        return "Faturası kesilmemiş; önce fatura kesilmeli."
    return ""


def ship_note(row: dict[str, Any]) -> str:
    """Engel YOK ama bilgi de eksikse söylenecek şey.

    Sığ liste satırında fatura ve gönderi kaydı hiç yoktur. Böyle bir satırı
    seçtirmemek toplu kargoyu varsayılan görünümde tamamen kullanılamaz
    kılardı; seçtirip sessiz kalmak ise "uygun sanıp" tıklatırdı. Üçüncü yol:
    seçtir, ama neyin doğrulanmadığını YAZ — önizleme siparişi tek tek okur.
    """
    if row.get("shipBlock"):
        return ""
    if not row.get("detailed"):
        return "Fatura ve kargo durumu bu satırda yok; önizleme siparişi tek tek doğrular."
    return ""


def batch_rows(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Toplu kargo/fatura ÖNİZLEMESİ — fark tablosu.

    Uygulanmadan önce hangi siparişin atlanacağı ve NEDEN atlandığı yazılır.
    "12 siparişin 5'i zaten faturalı" bilgisini uygulamadan sonra vermek, işi
    yapan kişiye geç kalmış bir açıklamadır.

    Kargo engelleri `ship_block` ile TEK YERDEN gelir: listedeki onay kutusunu
    kapatan kural ile önizlemede satırı atlayan kural ayrışırsa, kullanıcı
    seçebildiği bir siparişin neden atlandığını anlayamaz.
    """
    out: list[dict[str, Any]] = []
    for row in rows:
        if kind == "ship":
            note = ship_block(row)
        elif row.get("status") in ("canceled", "closed"):
            note = "Sipariş iptal/kapalı."
        elif row.get("invoiceState") == "full":
            note = "Zaten tamamı faturalanmış."
        else:
            note = ""
        out.append({
            "id": row["id"],
            "orderNo": row["orderNo"],
            "customer": row["customer"],
            "total": row["grandTotal"],
            "quantity": row.get("quantity", 0),
            "statusLabel": row["statusLabel"],
            "paymentLabel": row.get("paymentLabel", ""),
            "skipped": bool(note),
            "note": note,
        })
    return out


def batch_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Onay ekranının rakamları: kaç sipariş, kaç adet, ne kadar tutar.

    DESİ BURADA YOKTUR ve uydurulmaz: desi ürünün en/boy/yükseklik ölçüsünden
    hesaplanır, sipariş gövdesi o ölçüyü taşımaz ve hesabın sahibi Kargo
    Yönetimi ekranıdır (`store_shipping`). Buradan sahte bir desi vermek,
    taşıyıcı faturası geldiğinde tutmayan bir rakam olurdu.
    """
    ready = [row for row in rows if not row.get("skipped")]
    return {
        "total": len(rows),
        "ready": len(ready),
        "skipped": len(rows) - len(ready),
        "amount": sum(row.get("total", 0) for row in ready),
        "quantity": sum(row.get("quantity", 0) for row in ready),
    }
