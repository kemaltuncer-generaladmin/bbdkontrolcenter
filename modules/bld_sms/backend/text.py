"""SMS metni — şablon kataloğu, değişken çözümü, ölçüm ve maskeleme.

BURADA AĞ YOKTUR. Bu dosya yalnız metinle uğraşır; sunucuya giden her şey
`service.py` üzerinden geçer.

İKİ ÖLÇÜ VAR VE İKİSİ DE DOĞRU — ekran ikisini birden göstermek zorunda:

* **Faturalanan (en kötü durum).** Sözleşme (`BLD/docs/control/sms.md` →
  "Uzunluk ve segment") şunu söylüyor: metinde GSM-7 tablosunda olmayan bir
  karakter varsa mesaj **UCS-2** ile gider ve tek segment 160 değil **70**
  karakterdir (çoklu segmentte 67). BLD'nin `segments` alanı budur ve
  yöneticinin ödeyeceği sayı da budur.
* **Netgsm Türkçe tablosu.** Platformun SMS şeridi (`km_sdk.plan_text`) `ğ Ğ ı
  İ ş Ş` harflerini UCS-2'ye düşürmez: Netgsm'in Türkçe tekli kaydırma
  tablosuyla gönderir, harf başına 2 septet yer kaplar ve segment 160/153
  kalır. Aynı metin bu yolla çoğu zaman daha az segmente sığar.

İkisi çakışıyor ve ARADAKİ FARK PARADIR. Tek bir sayı göstermek iki şekilde de
yanlış olurdu: yalnız iyimser sayıyı göstermek yöneticiye gerçekte ödeyeceğinin
yarısını söyler, yalnız kötümser sayıyı göstermek de metni gereksiz yere
kısalttırır. Panel ikisini `measureBar` ile yan yana çizer ve "faturalanan"
işaretini **sözleşmedeki** ölçüye koyar — çünkü faturayı gönderen taraf o.

Sadeleştirme (`ş` → `s`) ikisini de düşürür ve tek tıkla önerilir; metni
kendiliğinden DEĞİŞTİRMEYİZ, kullanıcı karar verir.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from km_sdk import offending, plan_text, simplify

#: Değişken sözdizimi: `{degisken}` — süslü parantez, boşluksuz, küçük harf ve
#: alt çizgi (sözleşme "Değişken sözdizimi"). Çift süslü parantez ya da boşluklu
#: yazım TANINMAZ ve sunucu `422` verir; ekran bunu yazmadan önce söylemeli.
VARIABLE = re.compile(r"\{([a-z0-9_]+)\}")

#: Sözleşmedeki GSM-7 temel tablosu + genişletme tablosu. Bu kümenin DIŞINDA
#: tek bir karakter varsa mesaj UCS-2'ye düşer ve segment 160 → 70 olur.
#: `km_sdk` kendi kümesini taşıyor ama oradaki kural Netgsm'in Türkçe kaydırma
#: tablosunu da hesaba katıyor; buradaki küme SÖZLEŞMENİN kuralıdır ve
#: faturalanan sayıyı verir.
_GSM7 = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    "^{}\\[~]|€\f"
)

GSM7_SINGLE, GSM7_MULTI = 160, 153
UCS2_SINGLE, UCS2_MULTI = 70, 67

#: Şablon gövdesinin sınırı (sözleşme: 1–500 karakter).
BODY_MIN, BODY_MAX = 1, 500

#: Gerekçe sınırları — panel alanları için (00-genel.md §3).
MIN_REASON, MAX_REASON = 10, 500

#: Kitle değerleri. Sıra EKRANDAKİ sıradır ve `all_customers` bilinçli olarak
#: en sondadır: iki yıl önce bir kez sipariş vermiş birine duyuru göndermek
#: spam şikâyeti ve numara kaybı demektir.
AUDIENCES: list[dict[str, str]] = [
    {"key": "active_customers", "label": "Etkin müşteriler",
     "description": "Hesabı açık ve son 180 günde siparişi olan müşteriler."},
    {"key": "subscribers", "label": "Aboneler",
     "description": "Aktif aboneliği olan müşteriler."},
    {"key": "all_customers", "label": "Bütün müşteriler",
     "description": "Hesabı açık olan herkes — uzun süredir sipariş vermeyenler dâhil."},
]

AUDIENCE_KEYS = {item["key"] for item in AUDIENCES}

#: Gönderim kaydının süzülebilir alanları (sözleşme `GET /log`).
LOG_STATUSES = ("sent", "failed")
LOG_CONTEXTS = ("auto", "test", "announcement")


def _lira(kurus: int) -> str:
    """Kuruşu SMS metnindeki gibi yazar. Para HER ZAMAN tam sayı kuruştur;
    örnek değerler de kuruştan üretilir ki ekranda kayan nokta hiç doğmasın."""
    tam, kalan = divmod(int(kurus), 100)
    return f"{tam:,}".replace(",", ".") + f",{kalan:02d}"


#: Şablon KATALOĞU — anahtar, öbek, açıklama ve ÖRNEK değerler.
#:
#: `title`, `body` ve `enabled` BURADA YOKTUR: onlar BLD'dedir ve `GET
#: /templates` ile gelir. Buradaki üç alan yalnız ekranın işidir:
#:   · `group`  — Tetikleyiciler sekmesindeki öbek
#:   · `about`  — bildirimin ne zaman gittiğini anlatan cümle
#:   · `sample` — yerel önizlemenin örnek değerleri (sunucu kendi örneğini
#:                üretir; ikisi aynı olmak zorunda değil, ikisi de örnektir)
#:
#: Anahtarlar SABİTTİR (sözleşme). `otp_login` bu listede YOKTUR ve
#: olmayacaktır: giriş kodu metni `OtpService` içindedir ve panelden
#: düzenlenebilir olsaydı kodun kendisini metinden çıkarmak ya da bağlantı
#: gömmek tek satırlık bir değişiklik olurdu.
CATALOG: dict[str, dict[str, Any]] = {
    "order_created": {
        "group": "order",
        "about": "Müşteri siparişi oluşturduğu anda gider.",
        "sample": {"order_no": "BLD-8421", "service_date": "17.08.2026",
                   "total": _lira(18000), "customer_name": "Mehmet Kaya"},
    },
    "order_confirmed": {
        "group": "order",
        "about": "Sipariş onaylandığında gider.",
        "sample": {"order_no": "BLD-8421", "service_date": "17.08.2026"},
    },
    "order_on_the_way": {
        "group": "order",
        "about": "Kurye yola çıktığında gider.",
        "sample": {"order_no": "BLD-8421", "eta": "12:30"},
    },
    "order_delivered": {
        "group": "order",
        "about": "Sipariş teslim edildiğinde gider.",
        "sample": {"order_no": "BLD-8421"},
    },
    "order_cancelled": {
        "group": "order",
        "about": "Sipariş iptal edildiğinde gider.",
        "sample": {"order_no": "BLD-8421", "service_date": "17.08.2026",
                   "reason": "Mutfak kapalı"},
    },
    "order_revised": {
        "group": "order",
        "about": "Sipariş düzenlendiğinde (revizyon) gider.",
        "sample": {"order_no": "BLD-8421", "reason": "Kalem adedi güncellendi"},
    },
    "subscription_contract": {
        "group": "subscription",
        "about": "Abonelik sözleşmesi imzaya gönderildiğinde gider.",
        "sample": {"customer_name": "Mehmet Kaya",
                   "link": "https://bld.example/s/8f2a", "expires_at": "20.08.2026"},
    },
    "subscription_payment_due": {
        "group": "subscription",
        "about": "Abonelik döneminin borcu yaklaştığında hatırlatma olarak gider.",
        "sample": {"customer_name": "Mehmet Kaya", "period": "Eylül 2026",
                   "amount": _lira(180000), "due_date": "01.09.2026"},
    },
    "invoice_issued": {
        "group": "invoice",
        "about": "Fatura belgesi kesildiğinde gider.",
        "sample": {"invoice_no": "BLD2026000148", "total": _lira(180000),
                   "link": "https://bld.example/f/148"},
    },
    "announcement": {
        "group": "announcement",
        "about": "TOPLU duyuru. Kendiliğinden gitmez; Tetikleyiciler sekmesinden "
                 "elle çalıştırılır ve kuru prova zorunludur.",
        "sample": {"customer_name": "Mehmet Kaya"},
    },
}

#: Öbek başlıkları — Tetikleyiciler sekmesinin sırası.
GROUPS: list[dict[str, str]] = [
    {"key": "order", "label": "Sipariş durumları",
     "note": "Her biri ayrı açılıp kapanır. Kapalı bir şablon için gönderim "
             "denenmez ve kayda satır yazılmaz."},
    {"key": "subscription", "label": "Abonelik olayları",
     "note": "Sözleşme bağlantısı ve dönem borcu hatırlatması."},
    {"key": "invoice", "label": "Fatura",
     "note": "Fatura belgesi kesildiğinde giden bildirim."},
    {"key": "announcement", "label": "Toplu duyuru",
     "note": "ZAMANLAYICI YOKTUR. Duyuru elle çalıştırılır; şablonun kapalı "
             "olması gönderimi de kapatır."},
]

#: Katalogda olmayan bir anahtar gelirse (sunucuya yeni şablon eklenmiş)
#: satır DÜŞÜRÜLMEZ, bu öbeğe konur. Sessizce kaybolan bir şablon, kimsenin
#: kapatamadığı bir bildirim demektir.
OTHER_GROUP = {"key": "other", "label": "Diğer",
               "note": "Sunucuda tanımlı ama bu ekranın kataloğunda olmayan "
                       "şablonlar. Metni ve durumu yine buradan yönetilir."}


# ------------------------------------------------------------------ ölçüm

def _worst_case(text: str) -> dict[str, Any]:
    """Sözleşmenin ölçüsü: FATURALANAN segment sayısı.

    Kural sözleşmede tek cümle: GSM-7 tablosuna sığmayan bir karakter varsa
    UCS-2 (70/67), aksi hâlde GSM-7 (160/153). Genişletme tablosundaki
    karakterler (`{ } [ ] ~ ^ \\ | €`) GSM-7'de İKİ birim sayılır.
    """
    gsm7 = all(char in _GSM7 for char in text)
    if gsm7:
        units = sum(2 if char in "^{}\\[~]|€\f" else 1 for char in text)
        single, multi = GSM7_SINGLE, GSM7_MULTI
    else:
        # UCS-2'de BMP dışı karakterler (emoji) iki birimdir.
        units = sum(2 if ord(char) > 0xFFFF else 1 for char in text)
        single, multi = UCS2_SINGLE, UCS2_MULTI

    if units == 0:
        segments, capacity = 0, single
    elif units <= single:
        segments, capacity = 1, single
    else:
        segments = -(-units // multi)
        capacity = segments * multi

    return {
        "encoding": "gsm7" if gsm7 else "ucs2",
        "has_turkish_chars": not gsm7,
        "units": units,
        "segments": segments,
        "per_segment": single if segments <= 1 else multi,
        "remaining": max(0, capacity - units),
    }


def measure(text: str) -> dict[str, Any]:
    """Metnin iki ölçüsü, sadeleştirme kazancı ve pahalılaştıran karakterler.

    `billed` sözleşmenin (dolayısıyla faturanın) ölçüsüdür; `provider` ise
    platformun SMS şeridinin tahminidir. Ekran ikisini birden gösterir —
    dosya başlığındaki gerekçe.
    """
    text = text or ""
    billed = _worst_case(text)
    plan = plan_text(text)
    plain = simplify(text)
    plain_billed = _worst_case(plain)

    return {
        "length": len(text),
        "billed": billed,
        "provider": {
            # `encoding` Netgsm'e gönderilecek değerdir: `tr` ya da hiç.
            "encoding": "ucs2" if plan.unicode else ("gsm7-tr" if plan.encoding else "gsm7"),
            "units": plan.units,
            "segments": plan.parts,
            "remaining": plan.remaining,
        },
        # Sadeleştirme metni DEĞİŞTİRMEZ, önerir. Kazanç sıfırsa panel öneriyi
        # hiç göstermez — kazandırmayan bir düğme, gereksiz bir karardır.
        "simplified": {
            "text": plain,
            "segments": plain_billed["segments"],
            "gain": max(0, billed["segments"] - plain_billed["segments"]),
        },
        # Metni pahalılaştıran karakterler; ekran hangisi olduğunu yazsın.
        "offending": [char for char in offending(text)][:8],
    }


def body_hash(text: str) -> str:
    """Metnin özeti — temel çizgi için. Metnin KENDİSİ yerele yazılmaz."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# -------------------------------------------------------------- değişken

def variables(text: str) -> list[str]:
    """Metindeki değişkenler, göründükleri sırada, tekrarsız."""
    seen: dict[str, None] = {}
    for name in VARIABLE.findall(text or ""):
        seen.setdefault(name, None)
    return list(seen)


def unknown_variables(text: str, allowed: list[str] | tuple[str, ...]) -> list[str]:
    """Şablonun tanımadığı değişkenler.

    Sunucu bunları `422` ile reddediyor (`details.unknown_variables`) ve haklı:
    sessizce boş bırakılan bir değişken, müşteriye "Sayın , siparişiniz…" diye
    giden bir SMS üretirdi. Ekran bunu KAYDETMEDEN ÖNCE söyler.
    """
    izinli = set(allowed or ())
    return [name for name in variables(text) if name not in izinli]


def render(text: str, sample: dict[str, Any] | None) -> tuple[str, list[str]]:
    """Örnek değerleri yerine koyar. `(metin, çözülemeyenler)` döner.

    ÇÖZÜLEMEYEN DEĞİŞKEN OLDUĞU GİBİ BIRAKILIR (`{eta}`), boşa çevrilmez —
    sözleşmenin `unresolved_variables` kuralı. Boşa çevirmek eksiği gizler ve
    yönetici cümleyi tam sanır.
    """
    values = {str(key): "" if value is None else str(value)
              for key, value in (sample or {}).items()}
    eksik: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in values and values[name] != "":
            return values[name]
        if name not in eksik:
            eksik.append(name)
        return match.group(0)

    return VARIABLE.sub(replace, text or ""), eksik


def sample_for(key: str, given: dict[str, Any] | None = None) -> dict[str, Any]:
    """Şablonun örnek değerleri; verilen değerler kataloğun üstüne yazılır."""
    base = dict(CATALOG.get(key, {}).get("sample") or {})
    for name, value in (given or {}).items():
        if str(value).strip() != "":
            base[str(name)] = value
    return base


# --------------------------------------------------------------- maskeleme

def mask_phone(raw: str) -> str:
    """`5321234567` → `532****567`.

    Sunucu kaydı zaten maskeli veriyor; bu işlev DENETİM SATIRINA yazılan
    numara için. Denetim satırına açık numara yazılmaz (sözleşme "Denetim
    eylemleri"): gönderim kaydı bir iletişim defterine dönüşmemeli.
    """
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 6:
        return "***"
    return f"{digits[:3]}****{digits[-3:]}"


# --------------------------------------------------------------- yardımcı

def now_iso() -> str:
    """Şimdinin ISO 8601 UTC damgası. Yerel saat KULLANILMAZ: denetim satırı
    başka bir makinede okunacak ve saat dilimi orada başka olabilir."""
    return datetime.now(UTC).isoformat(timespec="seconds")


def older_than(iso: str, minutes: int) -> bool:
    """Damga verilen dakikadan eski mi. Çözülemeyen damga ESKİ sayılır —
    okunamayan bir jetonu geçerli saymak, süresi dolmuş bir provayla gerçek
    gönderim yapmak olurdu."""
    try:
        stamp = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - stamp > timedelta(minutes=max(1, int(minutes)))


def as_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any) -> bool | None:
    """Üç değerli: `True` · `False` · `None` (dokunulmadı)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    text_value = str(value).strip().lower()
    if text_value in {"1", "true", "evet", "acik", "açık"}:
        return True
    if text_value in {"0", "false", "hayir", "hayır", "kapali", "kapalı"}:
        return False
    return None


def text_of(value: Any) -> str:
    return str(value or "").strip()


def reason_error(reason: str) -> str:
    """Gerekçe backend'de DE doğrulanır (K9): arayüzde zorunlu göstermek,
    istemcinin gövdeyi elle kurmasını engellemez."""
    clean = text_of(reason)
    if len(clean) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı."
    if len(clean) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir."
    return ""
