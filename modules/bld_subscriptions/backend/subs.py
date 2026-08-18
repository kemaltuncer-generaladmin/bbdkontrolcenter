"""Abonelikler — saf yardımcılar. AĞA ÇIKMAZ, DEPOYA YAZMAZ.

Burada yalnız biçim, etiket ve ön denetim var. İş kararı `service.py`'de, HTTP
kapısı `api/routes.py`'de durur; bu dosyanın tamamı yan etkisizdir ve tek tek
sınanabilir.

ÜRETİM KURALI BURADA YOKTUR VE OLMAYACAKTIR. Bir aboneliğin hangi günlerde
sipariş ürettiğini `Subscription::upcomingServiceDays()` söyler ve takvim ucu
o metodun ta kendisini çağırır (`subscriptions.md`). Ekran `service_days`
listesinden kendi takvimini türetseydi:

  · Kapalı günler (`settings.closed_days`), duraklama aralıkları ve tek-gün
    istisnaları burada TEKRAR uygulanırdı; üçünden biri sunucuda değişince
    ekran, gerçekte üretilmeyen bir günü "üretilecek" diye gösterirdi.
  · Ayrışmanın fark edileceği yer mutfak olurdu: yönetici takvime bakıp yirmi
    porsiyon söz verir, gece işi hiç sipariş üretmez.

Aynı sebeple `overdue` da hesaplanmaz: sunucu hesaplar (`subscriptions.md` →
"İstemcide hesaplansaydı saati kaymış bir panelde borç bir gün erken kırmızıya
dönerdi"), ekran alanı olduğu gibi taşır.

Kod İngilizce+ASCII, yorum Türkçe (depo kuralı).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

# =========================================================== gerekçe sınırı

#: Gerekçenin en az uzunluğu (`00-genel.md` §3). Arayüzde alanı zorunlu
#: göstermek yetkilendirme değildir (K9); backend'de TEKRAR doğrulanır.
MIN_REASON = 10

#: En çok. Abonelik alanının yazma uçlarının HEPSİ genel 500 sınırındadır
#: (`00-genel.md` §3); sipariş alanındaki 160 karakterlik daralma buraya
#: UYGULANMAZ — o sınır `veykemtu_order_revisions.reason` sütunundan geliyor
#: ve abonelik yazmaları o sütuna hiç dokunmuyor. Geçit de bu metotları
#: `reason_max=MAX_REASON` (500) ile çağırıyor; 160 yazmak, sunucunun kabul
#: edeceği bir gerekçeyi ekranın reddetmesi olurdu.
MAX_REASON = 500

#: `subscription.order.release` sipariş hedefli TEK yazmadır ama gerekçesi
#: `veykemtu_order_revisions` sütununa DEĞİL kontrol denetim iznine yazılıyor
#: (`subscriptions.md` → denetim eylemleri tablosu, `target_type = order`).
#: Bu yüzden onun da sınırı 500'dür; ayrı bir sabit tanımlamak, sözleşmede
#: olmayan bir daralma uydurmak olurdu.

# ================================================================= sözlükler

#: Abonelik durumları — `subscriptions.md` şema tablosunda AÇIKÇA sayılı.
#: Sıra İŞ AKIŞININ sırasıdır: talep `pending` doğar, sözleşme hazırlanınca
#: `awaiting_contract`, imzalanınca `awaiting_payment`, ödeme gelince `active`.
#:
#: ═══════════════════════════════════════════════════════════════════════════
#: `awaiting_contract` / `awaiting_payment` SONRADAN EKLENDİ (I1) VE BU BİR
#: ÖZELLİK DEĞİL, KAYIP BİR SATIRIN GERİ GETİRİLMESİDİR.
#:
#: BLD'nin sözleşme servisi imza onaylandığında aboneliği `awaiting_payment`
#: yapıyordu ama değer hiçbir sözlükte yoktu. Sonuç: imzayı almış, parasını
#: bekleyen abonelik BU EKRANIN HİÇBİR SEKMESİNDE görünmüyordu — süzgeç
#: "bilinmeyen durum" diye 422 alıyor, aktifleştirme 409 veriyordu. Yani
#: akışın en kritik adımındaki abonelikler kayıptı.
#: ═══════════════════════════════════════════════════════════════════════════
STATUS_CODES: tuple[str, ...] = (
    "pending", "awaiting_contract", "awaiting_payment", "active", "paused", "cancelled",
)

STATUS_LABELS: dict[str, str] = {
    "pending": "Bekliyor",
    "awaiting_contract": "Sözleşme bekliyor",
    "awaiting_payment": "Ödeme bekliyor",
    "active": "Aktif",
    "paused": "Duraklatıldı",
    "cancelled": "İptal edildi",
}

STATUS_TONES: dict[str, str] = {
    "pending": "warn",
    # İKİSİ DE `warn`: ikisi de "birinin bir şey yapması bekleniyor" demek ve
    # `info` tonuna almak onları bilgilendirme satırına indirirdi.
    "awaiting_contract": "warn",
    "awaiting_payment": "warn",
    "active": "good",
    "paused": "info",
    "cancelled": "dim",
}

#: Henüz üretime girmemiş durumlar — panelin "bekleyenler" sekmesi ve
#: aktifleştirme düğmesinin kapısı. TEK YERDE duruyor ki dördüncü bir ara
#: durum eklendiğinde biri unutulup o abonelikler yine görünmez olmasın.
STATUS_BEFORE_ACTIVE: tuple[str, ...] = (
    "pending", "awaiting_contract", "awaiting_payment",
)

#: Sözleşme durumları. `none` bir sözleşme durumu değil, SÖZLEŞMESİZLİK
#: durumudur ve yalnız abonelik listesinin `contract_status` alanında görünür.
CONTRACT_STATUS_CODES: tuple[str, ...] = (
    "none", "pending", "sent", "signed", "cancelled", "expired",
)

CONTRACT_STATUS_LABELS: dict[str, str] = {
    "none": "Sözleşme yok",
    "pending": "Hazırlandı",
    "sent": "Gönderildi",
    "signed": "İmzalandı",
    "cancelled": "İptal edildi",
    "expired": "Süresi doldu",
}

CONTRACT_STATUS_TONES: dict[str, str] = {
    "none": "dim",
    "pending": "warn",
    "sent": "info",
    "signed": "good",
    "cancelled": "dim",
    "expired": "bad",
}

#: `signed` TERMİNALDİR: imzalanmış sözleşme iptal edilemez, ancak yenisi
#: eskisinin yerini alabilir (`subscriptions.md` → durum makinesi).
CONTRACT_OPEN: tuple[str, ...] = ("pending", "sent")

#: Talep durumları — `QuoteRequest::STATUSES`. Türkçe kodlardır ve öyle kalır:
#: sözleşme onları böyle sayıyor, İngilizceye çevirmek uydurma olurdu.
REQUEST_STATUS_CODES: tuple[str, ...] = ("yeni", "okundu", "cevaplandi", "kapandi")

REQUEST_STATUS_LABELS: dict[str, str] = {
    "yeni": "Yeni",
    "okundu": "Okundu",
    "cevaplandi": "Cevaplandı",
    "kapandi": "Kapandı",
}

REQUEST_STATUS_TONES: dict[str, str] = {
    "yeni": "warn",
    "okundu": "info",
    "cevaplandi": "good",
    "kapandi": "dim",
}

MENU_MODES: dict[str, str] = {
    "daily_menu": "Günün menüsü",
    "fixed_list": "Sabit liste",
}

DELIVERY_TYPES: dict[str, str] = {
    "delivery": "Adrese teslim",
    "pickup": "Gel-al",
}

#: TEK GEÇERLİ DEĞER. Cari hesap tamamen kalktı (iş kararı 1); `account`
#: gönderilirse geçit isteği HİÇ göndermeden keser. Sözlüğü tek elemanlı
#: bırakmak, alanın ekranda görünür ama seçilemez olmasını sağlıyor —
#: gizlenseydi "peşin mi cari mi" sorusu ekranda cevapsız kalırdı.
PAYMENT_MODES: dict[str, str] = {
    "prepaid_monthly": "30 günlük peşin",
}

PAYMENT_STATUS_LABELS: dict[str, str] = {
    "pending": "Bekliyor",
    "paid": "Tahsil edildi",
    "void": "Geçersiz",
}

PAYMENT_STATUS_TONES: dict[str, str] = {
    "pending": "warn",
    "paid": "good",
    "void": "dim",
}

PAYMENT_METHODS: dict[str, str] = {
    "online": "Online",
    "cash": "Nakit",
}

#: ISO hafta günleri (1 = Pazartesi). `service_days` bu numaraları taşır.
WEEKDAYS: dict[int, str] = {
    1: "Pazartesi", 2: "Salı", 3: "Çarşamba", 4: "Perşembe",
    5: "Cuma", 6: "Cumartesi", 7: "Pazar",
}

WEEKDAYS_SHORT: dict[int, str] = {
    1: "Pzt", 2: "Sal", 3: "Çar", 4: "Per", 5: "Cum", 6: "Cmt", 7: "Paz",
}

#: `POST /{id}/generate` en fazla bu kadar ileri gidebilir
#: (`max_lookahead_days`, `subscriptions.md`). Ayardan okunmaz: sunucunun
#: sabiti burada TEKRAR EDİLMİYOR, yalnız ekranın erken uyarı verebilmesi için
#: yazılı duruyor ve kapıyı sunucu tutuyor.
MAX_LOOKAHEAD_DAYS = 7

#: Dönem aralığının en çok uzunluğu (`subscriptions.md` → `POST /{id}/payments`).
MAX_PERIOD_DAYS = 62

#: İmza bağlantısının ömrü: 1–30 gün, varsayılan 7.
MIN_EXPIRES_DAYS = 1
MAX_EXPIRES_DAYS = 30
DEFAULT_EXPIRES_DAYS = 7

#: Takvim penceresi: sunucu tavanı 92 gün, varsayılan 30.
MAX_CALENDAR_DAYS = 92

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ================================================================= çevirmenler

def text(value: Any) -> str:
    """Boşlukları kırpılmış dize. `None` boş dizeye iner."""
    return "" if value is None else str(value).strip()


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_money(value: Any) -> int | None:
    """Kuruş tam sayı ya da `None`.

    `None` "fiyat henüz anlaşılmadı" demektir ve sıfıra çevrilmez: sıfır
    fiyatlı bir abonelik ekranda "0,00 ₺" diye görünür ve yönetici anlaşma
    yapıldığını sanardı.
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    return str(value).strip().lower() in ("1", "true", "evet", "yes", "on")


def now_iso() -> str:
    """Denetim izinin zaman damgası — UTC, `Z` sonekli.

    İZ HER ZAMAN UTC'DİR ve öyle kalmalı: satırlar farklı makinelerden
    yazılıyor ve yerel saatle karışık bir defter sıralanamaz.
    """
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


#: İşletme günü — BLD ile aynı bölge (`BusinessTime::ZONE`).
#:
#: ═══════════════════════════════════════════════════════════════════════════
#: "BUGÜN" UTC'DEN OKUNAMAZ (I4-16).
#:
#: `today_iso()` UTC gününü döndürüyordu; panel ise kullanıcının yerel saatini
#: kullanıyor. Türkiye UTC+3 olduğu için gece 00:00–03:00 arasında ikisi FARKLI
#: GÜN söylüyor: ekran "bugün 26 Ağustos" derken sunucu doğrulaması 25 Ağustos'a
#: bakıyor ve "duraklatma başlangıcı bugünden geriye alınamaz" gibi bir kural,
#: kullanıcının bugün dediği günü reddediyordu. Gece vardiyası olan bir mutfakta
#: bu saat aralığı boş değil.
#:
#: Bölgeyi burada sabitlemek, ekranın makine saatine güvenmesinden iyidir:
#: kararı veren taraf BLD ve o Europe/Istanbul'da yaşıyor.
#: ═══════════════════════════════════════════════════════════════════════════
BUSINESS_ZONE = ZoneInfo("Europe/Istanbul")


def today_iso() -> str:
    """İşletme günü (`YYYY-AA-GG`) — Europe/Istanbul, UTC DEĞİL."""
    return datetime.now(BUSINESS_ZONE).strftime("%Y-%m-%d")


# ================================================================== denetim

def reason_error(reason: Any) -> str:
    value = text(reason)
    if len(value) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına yazılıyor."
    if len(value) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir; {len(value)} yazıldı."
    return ""


def date_error(value: Any, *, field: str) -> str:
    """Boş kabul edilir (süzgeç verilmedi demektir); dolu ise biçim aranır."""
    raw = text(value)
    if not raw:
        return ""
    if not _DATE.match(raw):
        return f"{field} 'YYYY-AA-GG' biçiminde olmalı; '{raw}' yazıldı."
    try:
        datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError:
        return f"{field} geçerli bir gün değil: '{raw}'."
    return ""


def required_date_error(value: Any, *, field: str) -> str:
    if not text(value):
        return f"{field} zorunlu."
    return date_error(value, field=field)


#: Sözleşmenin an biçimi: ISO 8601 UTC, `Z` sonekli (`00-genel.md`).
_UTC_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def utc_stamp_in_future(value: Any) -> bool:
    """Damga sözleşmenin biçiminde VE gelecekte mi.

    Karşılaştırma dize üzerindedir ve bu yeterlidir: aynı biçimdeki `Z`
    sonekli ISO damgaları sözlük sırasında zaman sırasıyla aynıdır.

    BİÇİM TUTMUYORSA `False` DÖNER, yani kapı kapanmaz. Başka bir ofsetle
    (`+03:00`) gelen bir damgayı burada çözmek, saat dilimi aritmetiğini
    ekranda ikinci kez yazmak olurdu; yanlış yorumlanan bir damga yüzünden
    reddedilen geçerli bir tahsilat, sunucuya hiç ulaşmadığı için teşhis
    edilemezdi. Asıl denetim sunucudadır.
    """
    raw = text(value)
    if not _UTC_STAMP.match(raw):
        return False
    return raw > now_iso()


def choice_error(value: Any, allowed: Any, *, field: str) -> str:
    raw = text(value)
    if not raw:
        return ""
    if raw not in allowed:
        return f"'{raw}' tanınan bir {field.lower()} değeri değil."
    return ""


def status_filter(value: Any, allowed: tuple[str, ...]) -> tuple[list[str], str]:
    """Virgüllü ya da dizi durum süzgecini kodlara ayırır.

    Tanınmayan kod SESSİZCE DÜŞÜRÜLMEZ: düşen bir kod, kullanıcının seçtiği
    süzgeçten başka bir kümeyi "sizin süzgeciniz" diye göstermek olurdu.
    """
    if value in (None, ""):
        return [], ""
    raw = value if isinstance(value, list | tuple) else str(value).split(",")
    codes: list[str] = []
    for item in raw:
        code = text(item).lower()
        if not code:
            continue
        if code not in allowed:
            return [], f"'{code}' tanınan bir durum değil."
        if code not in codes:
            codes.append(code)
    return codes, ""


def service_days(value: Any) -> tuple[list[int], str]:
    """`service_days` listesini denetler: 1–7 arası, tekrarsız, boş değil."""
    if not isinstance(value, list | tuple):
        return [], "Servis günleri bir liste olmalı."
    days: list[int] = []
    for item in value:
        day = as_int(item, 0)
        if day < 1 or day > 7:
            return [], f"Servis günü 1–7 arası olmalı; '{item}' yazıldı."
        if day in days:
            return [], f"{WEEKDAYS[day]} iki kez yazıldı."
        days.append(day)
    if not days:
        return [], "En az bir servis günü seçilmeli; günsüz bir kural hiçbir şey üretmez."
    return sorted(days), ""


def service_days_label(value: Any) -> str:
    """[1,2,3,4,5] → "Pzt, Sal, Çar, Per, Cum". Boşsa "—"."""
    days = [as_int(day) for day in value or [] if as_int(day) in WEEKDAYS]
    if not days:
        return "—"
    return ", ".join(WEEKDAYS_SHORT[day] for day in sorted(set(days)))


def period_error(period_start: Any, period_end: Any, due_date: Any) -> str:
    """Dönem borcunun üç tarihini birlikte denetler.

    Sunucu da denetliyor (`subscriptions.md`); buradaki kapı ağ turunu ve
    anlamsız bir denetim satırını önler. Sınırlar SÖZLEŞMEDEN alındı,
    uydurulmadı.
    """
    for value, field in ((period_start, "Dönem başlangıcı"), (period_end, "Dönem bitişi"),
                         (due_date, "Son ödeme günü")):
        problem = required_date_error(value, field=field)
        if problem:
            return problem

    start = text(period_start)
    end = text(period_end)
    due = text(due_date)
    if end <= start:
        return "Dönem bitişi başlangıçtan sonra olmalı."
    # `date` kullanılıyor, `datetime` değil: dönem sınırı GÜN sayısıdır ve
    # saat dilimi taşımayan bir `datetime` farkı, yaz saati geçişinde bir gün
    # kayardı. Biçim yukarıda zaten doğrulandı.
    span = (date.fromisoformat(end) - date.fromisoformat(start)).days
    if span > MAX_PERIOD_DAYS:
        return (f"Dönem en çok {MAX_PERIOD_DAYS} gün olabilir; {span} gün yazıldı. "
                "Daha uzun bir aralık iki döneme bölünür.")
    if due < start:
        return "Son ödeme günü dönem başlangıcından önce olamaz."
    return ""


def pause_error(start_date: Any, end_date: Any) -> str:
    """Duraklatma aralığı. `end_date` ZORUNLUDUR.

    Süresiz duraklatma iptalin adı konmamış hâlidir (`subscriptions.md`) ve
    o iş `cancel` ucunundur. Boş bırakılmasına izin vermek, yöneticinin iptal
    ettiğini sanmadan aboneliği sonsuza kadar durdurması olurdu.
    """
    problem = required_date_error(start_date, field="Duraklatma başlangıcı")
    if problem:
        return problem
    problem = required_date_error(end_date, field="Duraklatma bitişi")
    if problem:
        return (f"{problem} Süresiz duraklatma kabul edilmiyor: aralığı olmayan bir "
                "duraklatma, iptalin adı konmamış hâlidir ve iptalin kendi ucu var.")
    if text(end_date) < text(start_date):
        return "Duraklatma bitişi başlangıçtan önce olamaz."
    return ""


# ================================================================ biçimciler

def _labelled(code: str, labels: dict[str, str], tones: dict[str, str]) -> dict[str, str]:
    return {
        "code": code,
        "label": labels.get(code, code or "—"),
        "tone": tones.get(code, "dim"),
    }


def subscription_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Liste satırı — `GET /subscriptions` gövdesinden.

    `unpaid_periods` / `unpaid_total_kurus` LİSTEDEN gelir ve burada
    HESAPLANMAZ: bu ekranın asıl sorusu "kim ödemedi"dir ve her satır için ayrı
    bir ödeme çağrısı yapmak dokuz abonelikte dokuz istek demekti (sözleşme).
    """
    status = text(raw.get("status"))
    contract_status = text(raw.get("contract_status")) or "none"
    unpaid = as_int(raw.get("unpaid_periods"))
    return {
        "id": as_int(raw.get("id")),
        "customer_id": as_int(raw.get("customer_id")),
        "customer_label": text(raw.get("customer_label")) or "—",
        "status": status,
        "status_label": STATUS_LABELS.get(status, status or "—"),
        "status_tone": STATUS_TONES.get(status, "dim"),
        "start_date": text(raw.get("start_date")),
        "end_date": text(raw.get("end_date")),
        "service_days": [as_int(day) for day in raw.get("service_days") or []],
        "service_days_label": service_days_label(raw.get("service_days")),
        "menu_mode": text(raw.get("menu_mode")),
        "menu_mode_label": MENU_MODES.get(text(raw.get("menu_mode")), "—"),
        "default_quantity": as_int(raw.get("default_quantity")),
        "agreed_unit_price_kurus": as_money(raw.get("agreed_unit_price_kurus")),
        "payment_mode": text(raw.get("payment_mode")),
        "payment_mode_label": PAYMENT_MODES.get(text(raw.get("payment_mode")), "—"),
        "delivery_point_count": as_int(raw.get("delivery_point_count")),
        "contract_status": contract_status,
        "contract_status_label": CONTRACT_STATUS_LABELS.get(contract_status, contract_status),
        "contract_status_tone": CONTRACT_STATUS_TONES.get(contract_status, "dim"),
        "next_service_date": text(raw.get("next_service_date")),
        "unpaid_periods": unpaid,
        "unpaid_total_kurus": as_int(raw.get("unpaid_total_kurus")),
        # FİYATSIZLIK EKRANDA AYRI BİR İŞARET: `pending` bir aboneliğin fiyatı
        # `null` olabilir ve o abonelik aktifleştirilemez. Rozeti listede
        # göstermek, "neden aktifleşmiyor" sorusunu tabloda cevaplar.
        "needs_price": as_money(raw.get("agreed_unit_price_kurus")) is None,
    }


def subscription_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Tek abonelik — alt kayıtlarıyla birlikte.

    Alt kayıtlar (`lines`, `delivery_points`, `pauses`, `exceptions`) gövdenin
    İÇİNDEDİR ve ayrı uçları yoktur (sözleşme); burada ne eklenir ne ayıklanır.
    """
    row = subscription_row(raw)
    contract = raw.get("contract")
    row.update({
        "location_id": as_int(raw.get("location_id")),
        "delivery_type": text(raw.get("delivery_type")),
        "delivery_type_label": DELIVERY_TYPES.get(text(raw.get("delivery_type")), "—"),
        "delivery_time_from": text(raw.get("delivery_time_from")),
        "delivery_time_to": text(raw.get("delivery_time_to")),
        "lines": [line_row(item) for item in raw.get("lines") or []],
        "delivery_points": [point_row(item) for item in raw.get("delivery_points") or []],
        "pauses": [pause_row(item) for item in raw.get("pauses") or []],
        "exceptions": [exception_row(item) for item in raw.get("exceptions") or []],
        "contract": contract_row(contract) if isinstance(contract, dict) else None,
        "created_at": text(raw.get("created_at")),
        "updated_at": text(raw.get("updated_at")),
    })
    # Sözleşme durumu TEKİL gövdede `contract` altındadır, listedeki
    # `contract_status` alanı burada yoktur. İkisini aynı ada indirgemek,
    # çekmecenin "sözleşme yok" demesi olurdu.
    if row["contract"]:
        code = row["contract"]["status"]
        row["contract_status"] = code
        row["contract_status_label"] = CONTRACT_STATUS_LABELS.get(code, code)
        row["contract_status_tone"] = CONTRACT_STATUS_TONES.get(code, "dim")
    row["flow"] = flow_steps(row)
    return row


def line_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_int(raw.get("id")),
        "menu_id": as_int(raw.get("menu_id")),
        "quantity": as_int(raw.get("quantity")),
        "agreed_unit_price_kurus": as_money(raw.get("agreed_unit_price_kurus")),
        "label": text(raw.get("label")),
    }


def point_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_int(raw.get("id")),
        "address_id": as_int(raw.get("address_id")),
        "quantity": as_int(raw.get("quantity")),
        "note": text(raw.get("note")),
    }


def pause_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": as_int(raw.get("id")),
        "start_date": text(raw.get("start_date")),
        "end_date": text(raw.get("end_date")),
        "reason": text(raw.get("reason")),
    }


def exception_row(raw: dict[str, Any]) -> dict[str, Any]:
    skip = bool(raw.get("skip"))
    override = raw.get("quantity_override")
    return {
        "id": as_int(raw.get("id")),
        "service_date": text(raw.get("service_date")),
        "skip": skip,
        "quantity_override": None if override is None else as_int(override),
        "note": text(raw.get("note")),
        # Etiketi burada kurmak, ekranın üç alandan cümle üretmesini önler:
        # "atla" ile "12 porsiyon" aynı sütunda okunur olmalı.
        "label": "Atlanacak" if skip else (
            f"{as_int(override)} porsiyon" if override is not None else "Değişiklik yok"),
    }


def calendar_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Takvim günü. `generated`/`released_at` sunucudan gelir, türetilmez."""
    weekday = as_int(raw.get("weekday"))
    exception = raw.get("exception")
    return {
        "date": text(raw.get("date")),
        "weekday": weekday,
        "weekday_label": WEEKDAYS.get(weekday, "—"),
        "quantity": as_int(raw.get("quantity")),
        "closed": bool(raw.get("closed")),
        "note": text(raw.get("note")),
        "exception": exception_row(exception) if isinstance(exception, dict) else None,
        "generated": bool(raw.get("generated")),
        "order_id": as_int(raw.get("order_id")),
        "released_at": text(raw.get("released_at")),
        # ÜÇ DURUM, İKİ DEĞİL: üretilmiş ve serbest bırakılmış · üretilmiş ama
        # 07:00'ı bekliyor · henüz üretilmemiş. İkiye indirmek, "mutfak bunu
        # görüyor mu" sorusunu cevapsız bırakırdı.
        "release_state": (
            "released" if raw.get("released_at")
            else "waiting" if raw.get("generated")
            else "none"),
    }


def run_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Üretim defteri satırı.

    `order_id` boş bir satır, üretimin DENENDİĞİ ama sipariş oluşmadığı
    anlamına gelir (kapalı gün, menü yayınlanmamış, stok dolu). Satır yine de
    yazılır ki gece işi ertesi koşuda aynı günü yeniden denemesin — ekran bunu
    "başarısız" değil "sipariş oluşmadı" diye söylemeli.
    """
    order_id = as_int(raw.get("order_id"))
    return {
        "id": as_int(raw.get("id")),
        "service_date": text(raw.get("service_date")),
        "delivery_point_id": as_int(raw.get("delivery_point_id")),
        "order_id": order_id,
        "order_number": text(raw.get("order_number")),
        "order_status": text(raw.get("order_status")),
        "quantity": as_int(raw.get("quantity")),
        "released_at": text(raw.get("released_at")),
        "created_at": text(raw.get("created_at")),
        "produced": bool(order_id),
        "outcome_label": (
            "Sipariş üretildi" if order_id else "Denendi, sipariş oluşmadı"),
        "outcome_tone": "good" if order_id else "warn",
    }


def contract_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Sözleşme satırı. `token` ve `sign_url` BURADAN GEÇMEZ.

    Sunucu ikisini de döndürmüyor (imzalı bağlantı denetim ekranından
    okunabilir hâle gelirdi); yine de alan alan kopyalamak yerine seçerek
    kurmak, sunucu bir gün yanlışlıkla `token` gönderse bile onun ekrana
    çıkmamasını sağlar.
    """
    status = text(raw.get("status"))
    snapshot = raw.get("terms_snapshot")
    return {
        "id": as_int(raw.get("id")),
        "subscription_id": as_int(raw.get("subscription_id")),
        "status": status,
        "status_label": CONTRACT_STATUS_LABELS.get(status, status or "—"),
        "status_tone": CONTRACT_STATUS_TONES.get(status, "dim"),
        "sent_to_phone": text(raw.get("sent_to_phone")),
        "sent_at": text(raw.get("sent_at")),
        "expires_at": text(raw.get("expires_at")),
        "signed_at": text(raw.get("signed_at")),
        "otp_verified_at": text(raw.get("otp_verified_at")),
        "cancelled_at": text(raw.get("cancelled_at")),
        "cancel_reason": text(raw.get("cancel_reason")),
        # İMZALANDIĞI ANDAKİ KOŞULLAR. Abonelik sonradan değişirse sözleşme
        # değişmez; "neyi imzaladı" sorusunun cevabı burada durmalı.
        "terms_snapshot": dict(snapshot) if isinstance(snapshot, dict) else {},
        "created_at": text(raw.get("created_at")),
        "open": status in CONTRACT_OPEN,
        "terminal": status == "signed",
    }


def payment_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Dönem ödemesi. `overdue` SUNUCUDAN gelir, burada hesaplanmaz."""
    status = text(raw.get("status"))
    method = text(raw.get("method"))
    return {
        "id": as_int(raw.get("id")),
        "period_start": text(raw.get("period_start")),
        "period_end": text(raw.get("period_end")),
        "amount_kurus": as_int(raw.get("amount_kurus")),
        "due_date": text(raw.get("due_date")),
        "status": status,
        "status_label": PAYMENT_STATUS_LABELS.get(status, status or "—"),
        "status_tone": PAYMENT_STATUS_TONES.get(status, "dim"),
        "method": method,
        "method_label": PAYMENT_METHODS.get(method, "—") if method else "—",
        "paid_at": text(raw.get("paid_at")),
        "reference": text(raw.get("reference")),
        "note": text(raw.get("note")),
        "invoice_id": as_int(raw.get("invoice_id")),
        "overdue": bool(raw.get("overdue")),
        "overdue_days": as_int(raw.get("overdue_days")),
        "period_label": f"{text(raw.get('period_start'))} → {text(raw.get('period_end'))}",
        # TAHSİL EDİLEBİLİR Mİ: yalnız `pending`. `paid` bir kaydı ikinci kez
        # işaretlemek tutarı iki kez saydırırdı ve sunucu `409` verir.
        "payable": status == "pending",
    }


def request_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Talep listesi satırı — İLETİŞİM BİLGİSİ MASKELİ GELİR.

    Maskeyi SUNUCU uygular (`subscriptions.md`) ve burada açılmaz; açmanın yolu
    kaydı tek tek okumaktır. Ekranın maskeyi kendisi kurması, sunucu bir gün
    maskesiz gönderdiğinde bunun fark edilmemesi olurdu.
    """
    status = text(raw.get("status"))
    converted = as_int(raw.get("converted_subscription_id"))
    return {
        "id": as_int(raw.get("id")),
        "full_name": text(raw.get("full_name")) or "—",
        "organization": text(raw.get("organization")),
        "telephone": text(raw.get("telephone")),
        "email": text(raw.get("email")),
        "service_type": text(raw.get("service_type")),
        "headcount": as_int(raw.get("headcount")),
        "frequency": text(raw.get("frequency")),
        "start_date": text(raw.get("start_date")),
        "location": text(raw.get("location")),
        "status": status,
        "status_label": REQUEST_STATUS_LABELS.get(status, status or "—"),
        "status_tone": REQUEST_STATUS_TONES.get(status, "dim"),
        "converted_subscription_id": converted,
        "converted": bool(converted),
        "created_at": text(raw.get("created_at")),
    }


def request_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Tek talep — MASKESİZ. Ziyaretçinin yazdığı içerik DEĞİŞTİRİLEMEZ.

    Yazılabilen yalnız `status` ve `admin_note`; bir kaydın içeriğini
    düzeltebilen panel, o kaydın delil değerini yok eder (sözleşme).
    """
    row = request_row(raw)
    row.update({
        "menu_preference": text(raw.get("menu_preference")),
        "kitchen_note": text(raw.get("kitchen_note")),
        "message": text(raw.get("message")),
        # ONAYSIZ KAYIT HİÇ OLUŞMAZ (sözleşme): alan her zaman doludur. Boş
        # gelirse ekran bunu SÖYLEMELİ — sessizce "—" yazmak, KVKK onayı
        # olmayan bir kaydı olağan göstermek olurdu.
        "kvkk_accepted_at": text(raw.get("kvkk_accepted_at")),
        "kvkk_missing": not text(raw.get("kvkk_accepted_at")),
        "submitted_at": text(raw.get("submitted_at")),
        "admin_note": text(raw.get("admin_note")),
    })
    return row


# ================================================================== akış

#: Abonelik akışının beş adımı (`stepper`). Adım adları iş kararlarından gelir:
#: talep → fiyat → sözleşme SMS → OTP onayı → aktif.
FLOW_STEPS: tuple[tuple[str, str], ...] = (
    ("request", "Talep"),
    ("price", "Anlaşılan fiyat"),
    ("contract", "Sözleşme gönderildi"),
    ("otp", "Müşteri onayladı (OTP)"),
    ("active", "Aktif"),
)


def flow_steps(row: dict[str, Any]) -> dict[str, Any]:
    """Aboneliğin akışta nerede durduğunu SUNUCUDAN GELEN ALANLARDAN türetir.

    Hiçbir adım tahmin edilmez; her adımın kaynağı bir sözleşme alanıdır:

        Talep     — abonelik kaydının varlığı (talepten mi elle mi açıldığı
                    sunucudan gelmiyor; adım "kayıt açıldı" demektir)
        Fiyat     — `agreed_unit_price_kurus` dolu mu
        Sözleşme  — `contract_status` ∈ {sent, signed}
        OTP       — `contract_status == signed` (OTP doğrulaması imzanın
                    kendisidir; `otp_verified_at` yalnız sözleşme gövdesinde
                    var ve listede yok)
        Aktif     — `status == active`

    `index` KESİNTİSİZ tamamlanan son adımdır (kit `stepper` sözleşmesi):
    fiyat girilmeden imzalanmış bir sözleşme olamayacağı için zincir kesintisiz
    ilerler, ama veri öyle gelirse ekran adımı "tamamlandı" göstermez —
    atlanmış bir adımı dolu saymak, eksik olanı görünmez kılardı.
    """
    contract_status = text(row.get("contract_status"))
    status = text(row.get("status"))
    done = {
        "request": bool(as_int(row.get("id"))),
        "price": as_money(row.get("agreed_unit_price_kurus")) is not None,
        "contract": contract_status in ("sent", "signed"),
        "otp": contract_status == "signed",
        "active": status == "active",
    }

    index = -1
    for position, (key, _label) in enumerate(FLOW_STEPS):
        if not done[key]:
            break
        index = position

    steps = [{"key": key, "label": label, "done": done[key]}
             for key, label in FLOW_STEPS]

    # SIRADAKİ ADIM ekranda yazıyla söylenir: şeride bakıp "şimdi ne yapmalıyım"
    # sorusunu cevaplamak, rozet saymaktan daha güvenilir.
    nexts = {
        "request": "Aboneliği açın ya da talebi aboneliğe çevirin.",
        "price": "Anlaşılan birim fiyatı girin (kuruş); fiyatsız abonelik aktifleşmez.",
        "contract": "Sözleşme sekmesinden imza bağlantısını gönderin.",
        "otp": "Müşterinin telefonundaki kodu girmesi bekleniyor; bu adım SUNUCUDA yürür.",
        "active": "Aboneliği aktifleştirin.",
    }
    pending = next((key for key, _ in FLOW_STEPS if not done[key]), "")
    if status == "cancelled":
        hint = "Abonelik iptal edildi; akış burada durur ve geri dönüşü yoktur."
    elif status == "paused":
        hint = "Abonelik duraklatıldı; aralık bitince ya da 'Devam ettir' ile üretim sürer."
    else:
        hint = nexts.get(pending, "Akış tamamlandı.")

    return {"steps": steps, "index": index, "next_key": pending, "next_hint": hint}


# ================================================================ sözleşme

def screen_contract() -> dict[str, Any]:
    """Ekranın sözlükleri — AĞA ÇIKMADAN verilir (K7).

    Panel durum çiplerini, seçim kutularını ve etiketleri buradan çizer; kod
    listelerini JavaScript'te tekrarlamak, sunucudaki liste değiştiğinde
    ekranın yanlış çip göstermesi olurdu.
    """
    return {
        "statuses": [_labelled(code, STATUS_LABELS, STATUS_TONES)
                     for code in STATUS_CODES],
        "contract_statuses": [_labelled(code, CONTRACT_STATUS_LABELS,
                                        CONTRACT_STATUS_TONES)
                              for code in CONTRACT_STATUS_CODES],
        "request_statuses": [_labelled(code, REQUEST_STATUS_LABELS,
                                       REQUEST_STATUS_TONES)
                             for code in REQUEST_STATUS_CODES],
        "payment_statuses": [_labelled(code, PAYMENT_STATUS_LABELS,
                                       PAYMENT_STATUS_TONES)
                             for code in ("pending", "paid", "void")],
        "payment_methods": [{"code": code, "label": label}
                            for code, label in PAYMENT_METHODS.items()],
        "menu_modes": [{"code": code, "label": label}
                       for code, label in MENU_MODES.items()],
        "delivery_types": [{"code": code, "label": label}
                           for code, label in DELIVERY_TYPES.items()],
        # TEK ELEMANLI VE ÖYLE KALACAK: cari hesap kalktı (iş kararı 1).
        "payment_modes": [{"code": code, "label": label}
                          for code, label in PAYMENT_MODES.items()],
        "weekdays": [{"code": day, "label": WEEKDAYS[day], "short": WEEKDAYS_SHORT[day]}
                     for day in sorted(WEEKDAYS)],
        "flow": [{"key": key, "label": label} for key, label in FLOW_STEPS],
        "reason": {"min": MIN_REASON, "max": MAX_REASON},
        "rules": {
            "max_lookahead_days": MAX_LOOKAHEAD_DAYS,
            "max_period_days": MAX_PERIOD_DAYS,
            "max_calendar_days": MAX_CALENDAR_DAYS,
            "expires_in_days": {"min": MIN_EXPIRES_DAYS, "max": MAX_EXPIRES_DAYS,
                                "default": DEFAULT_EXPIRES_DAYS},
        },
    }


def warnings_of(payload: Any) -> list[dict[str, Any]]:
    """Yanıttaki `warnings` dizisi — ÜÇ YERDE BİRDEN aranır.

    UYARI HATA DEĞİLDİR ve yutulmaz: "kural değişti ama üretilmiş siparişler
    etkilenmedi" cümlesini görmeyen bir yönetici, yarının siparişini
    değiştirdiğini sanır. Sözleşme bunları `{code, ...}` sözlükleri olarak
    veriyor; biçim OLDUĞU GİBİ taşınır ve ekran Türkçeleştirir.

    ─────────────────────────────────────────────────────────────────────────
    NEDEN YALNIZ ÜST DÜZEYE BAKMAK YETMİYOR (I3).

    `mark-paid` ucu uyarıyı uzun süre `data.warnings` içine gömüyordu; burası
    yalnız üst düzeye baktığı için o uyarı HİÇBİR EKRANDA görünmedi. Kaybolan
    uyarı da önemsiz değildi: "fatura kesilmedi". Yönetici onay kutusunu
    işaretliyor, hiçbir şey olmuyor ve kimse fark etmiyordu.

    Sunucu tarafı düzeltildi (uyarı artık üst düzeyde) ama BURASI ÜÇÜNE DE
    BAKIYOR: eski bir BLD sürümüne bakan bir kurulumda uyarının yine
    kaybolması, düzeltmenin yarısını hiç yapmamak olurdu. Kuru provada gövde
    `would` altında geliyor ve orası da taranıyor.

    AYNI KOD İKİ YERDE GEÇERSE BİR KEZ DÖNER: yinelenen bir uyarı satırı,
    ekranda aynı cümleyi iki kez yazdırırdı.
    ─────────────────────────────────────────────────────────────────────────
    """
    if not isinstance(payload, dict):
        return []

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    for scope in (payload, payload.get("data"), payload.get("would")):
        if not isinstance(scope, dict):
            continue
        raw = scope.get("warnings")
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = json.dumps(item, sort_keys=True, ensure_ascii=False)
            if key in seen:
                continue
            seen.add(key)
            out.append(dict(item))

    return out
