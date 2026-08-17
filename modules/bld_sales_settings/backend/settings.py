"""Satış ayarlarının saf kuralları — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın işi tek bir sözlüğe bakıp "bu değer kabul edilir
mi, kabul edilmezse yöneticiye ne yazılır" sorusuna cevap vermekten ibaret:
kesim saatinin biçimi, ileri gün tavanı, ödeme yöntemi listesi, dakika
alanlarının aralığı, hangi alanın hiç yazılamayacağı. Servise gömülselerdi tek
satırı bile ağ taklidi olmadan sınanamazdı; burada hepsi girdi→çıktı
fonksiyonudur.

ALAN ADLARI snake_case KALIR (`00-genel.md` §6). Sözleşme telde bu sözlüğü
sabitliyor ve aynı anahtarlar üç yerde birden yaşıyor: `location_options`
tablosunda, `LocationGate` erişimcilerinde ve `BLD/docs/control/settings.md`
tablosunda. Arada camelCase'e çevirmek dördüncü bir sözlük yaratır ve yanlış
çeviri SESSİZ kalır — `orderCutoff` diye yazılan anahtar sunucuda hiçbir şeye
denk gelmez, hata da vermez, yönetici kesim saatini değiştirdiğini sanır.

BEŞ TUZAK — her birinin karşılığı burada bir fonksiyondur:

 1. Sunucu dakika alanlarını KIRPIYORDU     → `clean_patch` aralık dışını
    (`LocationGate` sessizce varsayılana       REDDEDER. 700 dakika hazırlık
    çeviriyordu)                               süresi yazan yönetici sessizce 40
                                               almamalı; ne yazdığını ve neyin
                                               kabul edildiğini bilmeli.
 2. `subscription_release_time` KALDIRILDI  → `REMOVED_FIELDS` gönderilirse
    (17.08.2026)                               NEDENİYLE BİRLİKTE reddeder.
                                               "Tanımlı alan değil" demek yetmez:
                                               yönetici alanın yanlış yazıldığını
                                               sanardı, oysa ayar artık YOK.
 3. `account` ödeme yöntemi KALKTI          → `clean_payment_methods` yalnız
    (iş kararı 1)                              `online` ve `cash` kabul eder;
                                               boş liste de reddedilir.
 4. `is_open` ve `daily_package_menu_id`    → `clean_patch` gönderilmelerini
    YAZILAMAZ                                  reddeder. İlki çalışma
                                               saatlerinden türer, ikincisini
                                               göç yazar ve yanlış bir kimlik
                                               günün menüsünü sıfır liraya
                                               sattırır.
 5. `busy` anahtarını MUTFAK EKRANI DA      → `conflicts()` yazma anındaki taze
    değiştiriyor                               değeri, formun açıldığı andaki
                                               taban çizgisiyle karşılaştırır.
                                               Yönetici yarım saat önceki hâli
                                               geri yazmamalı.

İKİ KESİM SAATİ VARDIR ve karıştırılmamalı: buradaki `order_cutoff` GENEL
varsayılandır, `DailyMenuDay.cutoff_time` o servis gününe özeldir ve doluysa
genel olanı EZER. Birleştirme kuralı sunucuda tektir: `gün ?? ayar`. Bu ekran
yalnız genel olanı yazar; gün bazlı saat menü ekranının işidir.

KESİM SAATİNİN İKİNCİ İŞİ: siparişin mutfak ekranında görünür olduğu anı da o
belirliyor (`orders.bld_released_at`). Bu bir AYAR DEĞİL, kesim saatinden
TÜRETİLEN bilgidir — eskiden `subscription_release_time` diye ayrı bir anahtar
vardı ve 17.08.2026'da kaldırıldı. İki ayar iki doğruluk kaynağı demekti:
kesimi 09:00'a çekip düşme saatini 07:00'de unutan yönetici, satış hâlâ açıkken
mutfağa eksik bir listeyi tam diye gösterirdi.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

# ============================================================ gerekçe ve aktör

#: Gerekçenin en az uzunluğu. BLD tarafı da 10 istiyor (`00-genel.md` §3);
#: burada TEKRAR doğrulanır çünkü arayüzde alanı zorunlu göstermek
#: yetkilendirme değildir (K9) ve istemci gövdeyi elle kurabilir.
MIN_REASON = 10

#: En çok. Panel uçlarının tamamında 500 (`00-genel.md` §3). Sipariş
#: revizyonundaki 160'lık sınır BU ALANDA YOKTUR: buradan sipariş yazılmıyor.
MAX_REASON = 500

# =================================================================== biçimler

#: `HH:mm` — sözleşmedeki kalıbın birebir aynısı (`settings.md` → PUT /sales).
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

#: `YYYY-MM-DD`.
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# ================================================================== sınırlar

#: Kabul edilen ödeme yöntemleri. **`account` YOKTUR** — cari hesap Faz 0'da
#: kaldırıldı (iş kararı 1) ve sözleşme gönderilirse `422` veriyor. Küme
#: SABİTTİR, ayardan okunmaz: ayarı değiştirebilen biri kalkmış bir ödeme
#: yöntemini geri açardı.
PAYMENT_METHODS = ("online", "cash")

#: İleri sipariş tavanı (iş kararı 3). Sunucu sabiti bugün 30 olabilir ama
#: sözleşme 7 diyor ve üstünü `422` ile reddediyor; panelden 30 girilebilmesi
#: kararın sessizce delinmesi olurdu.
MAX_LOOKAHEAD_DAYS = 7
MIN_LOOKAHEAD_DAYS = 0

#: Hazırlık / teslimat / yoğunluk dakikaları.
MINUTE_MIN = 1
MINUTE_MAX = 480

#: Metin sınırları — sütun genişlikleri, uydurma değil (`settings.md`).
BUSY_MESSAGE_MAX = 500
CUSTOMER_MESSAGE_MAX = 300
CLOSED_DAY_DESCRIPTION_MAX = 160

#: Durdurmanın en uzun süresi. Daha uzunu "süreli" değil kapanıştır ve
#: `until: null` ile ifade edilir.
PAUSE_MAX_DAYS = 30

# ==================================================================== alanlar

#: `PUT /sales` ile yazılabilen 12 alan — sözleşmedeki sırayla
#: (`SettingsRepository::controlWritableFields()` ile birebir aynı).
WRITABLE_FIELDS = (
    "order_cutoff",
    "max_lookahead_days",
    "min_order_total_kurus",
    "delivery_fee_kurus",
    "payment_methods",
    "busy",
    "busy_message",
    "prep_minutes",
    "delivery_minutes",
    "busy_extra_minutes",
    "daily_menu_enabled",
    "auto_invoice",
)

#: Gönderilirse `422` alan alanlar. `ordering_enabled` de buradadır: kendi
#: uçları var (`/ordering/pause`, `/ordering/resume`) ve şalteri gerekçesiz,
#: süresiz çevirmek tam da durdurmanın en sık hatasını (açmayı unutmak)
#: üretirdi.
READ_ONLY_FIELDS = ("is_open", "daily_package_menu_id", "ordering_enabled")

#: KALDIRILMIŞ alanlar → gönderilirse reddedilir, SEBEBİYLE BİRLİKTE.
#:
#: "SALT OKUNUR" İLE "ARTIK YOK" AYRI ŞEYLER. `READ_ONLY_FIELDS` "bu alan var
#: ama buradan yazılmaz" diyor; burası "bu ayar bir daha gelmeyecek" diyor ve
#: yöneticiye söylenecek cümle ikisinde farklı. Sunucu tarafındaki karşılığı
#: `SettingsController::rejectRemovedFields()`.
#:
#: Alanı listeden sessizce çıkarmak yetmezdi: `clean_patch` onu "tanımlı bir
#: alan değil" diye reddeder ve yönetici adı yanlış yazdığını sanardı — oysa
#: doğru yazmış, ayar kaldırılmış. Kaldırılan şey silinmez, "kaldırıldı" diye
#: işaretlenir (`BLD/docs/00-INDEX.md`).
REMOVED_FIELDS = {
    "subscription_release_time": (
        "Abonelik serbest bırakma saati 17.08.2026'da KALDIRILDI. Sipariş artık "
        "servis gününün kesim saatinde mutfağa düşer (`order_cutoff` ve güne özel "
        "saat); ayrı bir serbest bırakma saati yoktur."
    ),
}

#: 1–480 aralığındaki üçlü.
MINUTE_FIELDS = ("prep_minutes", "delivery_minutes", "busy_extra_minutes")

#: `>= 0` tam sayı kuruş olan ikili. Para HER ZAMAN tam sayı kuruştur;
#: ondalıklı TL telde hiçbir yerde gitmez (`00-genel.md` §6).
MONEY_FIELDS = ("min_order_total_kurus", "delivery_fee_kurus")

#: Alanların Türkçe adları — hata cümleleri ve değişiklik tablosu için.
FIELD_LABELS = {
    "order_cutoff": "Sabah kesim saati",
    "max_lookahead_days": "İleri gün sınırı",
    "min_order_total_kurus": "Minimum sepet tutarı",
    "delivery_fee_kurus": "Teslimat ücreti",
    "payment_methods": "Ödeme yöntemleri",
    "busy": "Yoğunluk anahtarı",
    "busy_message": "Yoğunluk mesajı",
    "prep_minutes": "Hazırlık süresi",
    "delivery_minutes": "Teslimat süresi",
    "busy_extra_minutes": "Yoğunlukta ek süre",
    "daily_menu_enabled": "Günün menüsü rejimi",
    "auto_invoice": "Otomatik fatura belgesi",
    "ordering_enabled": "Satış şalteri",
    "paused_until": "Durdurma bitişi",
    "pause_reason": "Müşteriye gösterilen durdurma mesajı",
    "is_open": "Çalışma saati durumu",
    "daily_package_menu_id": "Günün menüsü paket ürünü",
}

# =================================================================== çevirici


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_int(value: Any, fallback: int = 0) -> int:
    """Sayıya çevirir; çevrilemezse yedeği verir. Boolean SAYI DEĞİLDİR."""
    if isinstance(value, bool):
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any) -> bool | None:
    """Üç değerli: `True` · `False` · `None` (bilinmiyor / gönderilmedi)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = text(value).lower()
    if lowered in ("1", "true", "evet", "acik", "açık"):
        return True
    if lowered in ("0", "false", "hayir", "hayır", "kapali", "kapalı"):
        return False
    return None


def now_iso() -> str:
    """Şimdinin ISO 8601 UTC damgası. `Z` sonekli (`00-genel.md` §6)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_iso(offset_days: int = 0) -> str:
    """Bugünün `YYYY-MM-DD` hâli. Gün sınırı YEREL değil UTC'dir; panel kendi
    gününü `todayIso()` ile hesaplar ve sunucuya açıkça yazar."""
    return (datetime.now(UTC) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def iso_date(value: Any) -> str:
    """`YYYY-MM-DD` ise aynen döner, değilse boş dize.

    Boş dönmesi bilinçli: uydurma bir tarih göndermektense süzgeci hiç
    göndermemek doğrudur — sunucu varsayılanı (bugünden 365 gün) anlamlıdır.
    """
    raw = text(value)
    return raw if DATE_PATTERN.match(raw) else ""


def reason_error(reason: str) -> str:
    """Gerekçe kapısı. Boş dize = sorun yok."""
    value = text(reason)
    if len(value) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı."
    if len(value) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir."
    return ""


# ============================================================== karşılaştırma


def normalize(field: str, value: Any) -> Any:
    """Karşılaştırma için sadeleştirilmiş değer.

    `payment_methods` SIRASIZ bir kümedir: `["cash","online"]` ile
    `["online","cash"]` aynı ayardır ve birini öbürüne "değişiklik" saymak,
    denetim izine hiç olmamış bir olay yazardı.
    """
    if field == "payment_methods":
        return tuple(sorted({text(item) for item in (value or []) if text(item)}))
    if field in ("busy", "daily_menu_enabled", "auto_invoice"):
        return bool(value)
    if field in MINUTE_FIELDS or field in MONEY_FIELDS or field == "max_lookahead_days":
        return as_int(value, 0)
    if value is None:
        return None
    return text(value)


def diff(before: dict[str, Any], after: dict[str, Any],
         fields: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """`[{field, from, to}]` — yalnız GERÇEKTEN değişenler.

    Ham değerleri taşır (normalize edilmiş hâli değil): yönetici tabloda
    yazdığı şeyi görmeli, karşılaştırma biçimini değil.
    """
    rows: list[dict[str, Any]] = []
    for field in fields:
        if field not in after:
            continue
        if normalize(field, before.get(field)) == normalize(field, after.get(field)):
            continue
        rows.append({"field": field, "label": FIELD_LABELS.get(field, field),
                     "from": before.get(field), "to": after.get(field)})
    return rows


def conflicts(baseline: dict[str, Any], fresh: dict[str, Any],
              fields: tuple[str, ...] | list[str]) -> list[dict[str, Any]]:
    """Form açıldığından beri SUNUCUDA değişmiş alanlar.

    YOĞUNLUK YARIŞI. `bld_busy` anahtarını mutfak ekranı da değiştiriyor:
    yönetici formu 09:00'da açar, mutfak 09:10'da yoğunluğu açar, yönetici
    09:30'da kaydeder — gönderdiği alan yarım saat önceki hâli geri yazar ve
    kimse fark etmez. Kural yalnız `busy` için değil, YAZILAN HER ALAN için
    geçerli: iki yönetici aynı anda çalışıyor olabilir.

    Yazılmayan alanlar burada denetlenmez ve denetlenmemeli: onlara zaten
    dokunulmuyor (kısmi yazma) ve mutfağın değiştirdiği bir anahtar yüzünden
    kesim saati kaydını reddetmek, ekranı kullanılamaz yapardı.
    """
    rows: list[dict[str, Any]] = []
    for field in fields:
        if field not in baseline:
            continue
        if normalize(field, baseline.get(field)) == normalize(field, fresh.get(field)):
            continue
        rows.append({"field": field, "label": FIELD_LABELS.get(field, field),
                     "baseline": baseline.get(field), "current": fresh.get(field)})
    return rows


# ================================================================ doğrulama


def clean_payment_methods(value: Any) -> tuple[list[str], str]:
    """`(liste, hata)`. Boş hata = kabul.

    Boş liste REDDEDİLİR: hiçbir ödeme yöntemi olmayan bir satış kanalı,
    sipariş formunun son adımında çuvallayan bir mağaza demekti. Tekrar da
    reddedilmez, SESSİZCE tekilleştirilir mi? Hayır — tekrar bir yazım
    hatasıdır ve sessizce düzeltmek, yöneticinin listeyi yanlış girdiğini hiç
    öğrenmemesi olurdu.
    """
    if not isinstance(value, list | tuple):
        return [], "Ödeme yöntemleri bir liste olmalı."
    items = [text(item) for item in value]
    if not items or any(not item for item in items):
        return [], "En az bir ödeme yöntemi seçilmeli."
    if len(set(items)) != len(items):
        return [], "Ödeme yöntemi listesinde tekrar var."
    unknown = [item for item in items if item not in PAYMENT_METHODS]
    if unknown:
        # `account` en sık gelecek yanlış değer; cümlede adı geçsin ki
        # "neden kabul edilmedi" sorusu tek bakışta cevaplansın.
        return [], (f"Bilinmeyen ödeme yöntemi: {', '.join(unknown)}. "
                    "Yalnız 'online' ve 'cash' kabul edilir — cari hesap kaldırıldı.")
    return items, ""


def clean_patch(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Panelden gelen kısmi gövdeyi süzer ve doğrular.

    `(gövde, hatalar)` döner; `hatalar` boşsa gövde gönderilebilir.
    GÖNDERİLMEYEN ALAN GÖVDEYE GİRMEZ — kısmi yazmanın anlamı budur ve
    dokunulmamış bir alanı "aynı değerle" göndermek, iki yönetici aynı anda
    çalışırken öbürünün değişikliğini sessizce ezmek olurdu.
    """
    body: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    def fail(field: str, message: str) -> None:
        errors.append({"field": field, "message": message})

    for field in READ_ONLY_FIELDS:
        if field in raw:
            label = FIELD_LABELS.get(field, field)
            if field == "ordering_enabled":
                fail(field, f"{label} bu uçtan yazılamaz; satışı durdurma ve açma "
                            "işlemlerinin kendi uçları var.")
            else:
                fail(field, f"{label} salt okunurdur ve yazılamaz.")

    # KALDIRILMIŞ ALAN, BİLİNMEYEN ALAN DEĞİLDİR. Aşağıdaki "tanımlı bir alan
    # değil" cümlesi yöneticiye adı yanlış yazdığını düşündürürdü; oysa doğru
    # yazmış, ayar artık yok. Eski bir panel derlemesi de bu cümleyle ne olduğunu
    # anlar — sunucu aynı alanı `422` ile aynı gerekçeyle reddediyor.
    for field, why in REMOVED_FIELDS.items():
        if field in raw:
            fail(field, why)

    if "order_cutoff" in raw:
        value = raw["order_cutoff"]
        if value is None or text(value) == "":
            # `null` GEÇERLİ: "kesim saati yok" demektir ve gün bazlı saatler
            # devreye girer. Boş dizeyi de `null`a çeviriyoruz çünkü form boş
            # alanı boş dize olarak gönderir.
            body["order_cutoff"] = None
        elif TIME_PATTERN.match(text(value)):
            body["order_cutoff"] = text(value)
        else:
            fail("order_cutoff", "Sabah kesim saati 08:00 gibi HH:mm biçiminde olmalı.")

    if "max_lookahead_days" in raw:
        value = raw["max_lookahead_days"]
        number = as_int(value, -1)
        if number < MIN_LOOKAHEAD_DAYS or number > MAX_LOOKAHEAD_DAYS:
            fail("max_lookahead_days",
                 f"İleri gün sınırı {MIN_LOOKAHEAD_DAYS}–{MAX_LOOKAHEAD_DAYS} arasında "
                 "olmalı. Sıfır geçerlidir ve 'yalnız bugüne sipariş' demektir.")
        else:
            body["max_lookahead_days"] = number

    for field in MONEY_FIELDS:
        if field not in raw:
            continue
        number = as_int(raw[field], -1)
        if number < 0:
            fail(field, f"{FIELD_LABELS[field]} sıfır ya da daha büyük tam sayı "
                        "kuruş olmalı.")
        else:
            body[field] = number

    for field in MINUTE_FIELDS:
        if field not in raw:
            continue
        number = as_int(raw[field], -1)
        if number < MINUTE_MIN or number > MINUTE_MAX:
            # Sunucunun eski davranışı aralık dışını SESSİZCE varsayılana
            # çeviriyordu; bu uç reddediyor. Sessizce düzeltilen bir ayar,
            # yöneticinin girdiğini sandığı değerle çalışmadığını hiç
            # öğrenmemesi demektir.
            fail(field, f"{FIELD_LABELS[field]} {MINUTE_MIN}–{MINUTE_MAX} dakika "
                        "arasında olmalı.")
        else:
            body[field] = number

    if "payment_methods" in raw:
        methods, problem = clean_payment_methods(raw["payment_methods"])
        if problem:
            fail("payment_methods", problem)
        else:
            body["payment_methods"] = methods

    if "busy_message" in raw:
        value = raw["busy_message"]
        message = "" if value is None else text(value)
        if len(message) > BUSY_MESSAGE_MAX:
            fail("busy_message", f"Yoğunluk mesajı en çok {BUSY_MESSAGE_MAX} karakter "
                                 f"olabilir (şu an {len(message)}).")
        else:
            # Boş dize SUNUCUDA varsayılana döner; `null` ile aynı anlama gelir
            # ve sözleşme ikisini de kabul eder.
            body["busy_message"] = message or None

    for field in ("busy", "daily_menu_enabled", "auto_invoice"):
        if field not in raw:
            continue
        flag = as_bool(raw[field])
        if flag is None:
            fail(field, f"{FIELD_LABELS[field]} yalnız açık ya da kapalı olabilir.")
        else:
            body[field] = flag

    unknown = [key for key in raw
               if key not in WRITABLE_FIELDS and key not in READ_ONLY_FIELDS
               and key not in REMOVED_FIELDS]
    for key in unknown:
        # Bilinmeyen alan SESSİZCE DÜŞÜRÜLMEZ: sözleşmede olmayan bir anahtar
        # sunucuda da yok sayılır ve yönetici "kaydettim" der, hiçbir şey
        # değişmez. Yanlış yazılmış bir alan adının tek görünür olduğu yer
        # burasıdır.
        fail(key, f"'{key}' satış ayarlarında tanımlı bir alan değil.")

    if not body and not errors:
        errors.append({"field": "", "message": "Değişen alan yok; gönderilecek bir şey de yok."})

    return body, errors


def pause_error(until: Any, *, now: str = "") -> str:
    """Durdurma bitişinin doğrulaması. Boş dize = kabul.

    `null` GEÇERLİ ve süresiz demektir (elle açılana kadar). Süre dolduğunda
    satış KENDİLİĞİNDEN açılır; arka planda bir iş yoktur, sunucu okuma anında
    karşılaştırır. Zamanlayıcıya bağlamak, zamanlayıcının çalışmadığı her
    durumda dükkânın kapalı kalması olurdu.
    """
    if until is None or text(until) == "":
        return ""
    raw = text(until)
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return "Durdurma bitişi 2026-08-16T15:00:00Z gibi ISO 8601 UTC olmalı."
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    reference = datetime.now(UTC)
    if now:
        try:
            parsed = datetime.fromisoformat(now.replace("Z", "+00:00"))
            reference = parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            reference = datetime.now(UTC)
    if moment <= reference:
        return "Durdurma bitişi geçmişte olamaz."
    if moment > reference + timedelta(days=PAUSE_MAX_DAYS):
        return (f"Durdurma en fazla {PAUSE_MAX_DAYS} gün ileri olabilir. Daha uzun bir "
                "durdurma 'süreli' değil kapanıştır ve bitiş saati boş bırakılarak "
                "(süresiz) yazılmalıdır.")
    return ""


def customer_message_error(message: Any) -> str:
    value = "" if message is None else text(message)
    if len(value) > CUSTOMER_MESSAGE_MAX:
        return (f"Müşteriye gösterilecek mesaj en çok {CUSTOMER_MESSAGE_MAX} karakter "
                f"olabilir (şu an {len(value)}).")
    return ""


def closed_day_error(date: Any, description: Any) -> str:
    if not iso_date(date):
        return "Kapalı gün tarihi YYYY-MM-DD biçiminde olmalı."
    value = "" if description is None else text(description)
    if len(value) > CLOSED_DAY_DESCRIPTION_MAX:
        return (f"Açıklama en çok {CLOSED_DAY_DESCRIPTION_MAX} karakter olabilir "
                f"(şu an {len(value)}).")
    return ""


# ======================================================================= stok


def clean_stock(capacity_total: Any, items: Any) -> tuple[dict[str, Any], str]:
    """Hızlı stok şeridinin gövdesi. `(gövde, hata)`.

    **TAM LİSTEDİR, FARK DEĞİL.** Gönderilmeyen kalemin tavanı sunucuda
    `null`'a düşer; niyet "bugünün tavan tablosu şudur"dur. Panelin ekranda
    gördüğü bütün kalemleri göndermesi bu yüzden zorunludur ve buradaki
    `item_id` denetimi onun kapısıdır — eksik bir satır, o kalemin tavanını
    sessizce kaldırırdı.

    Tavanı satılmışın ALTINA çekmek serbesttir ve burada engellenmez: gerçek
    hayatta malzeme biter ve yönetici satışı kapatmak ister. Sunucu bunu
    `warnings` ile söyler, ekran da yazar.
    """
    if not isinstance(items, list):
        return {}, "Stok satırları bir liste olmalı."

    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict) or "item_id" not in item:
            return {}, ("Stok satırında kalem kimliği (`item_id`) zorunlu: eksik satır, "
                        "o kalemin tavanını sessizce kaldırırdı (liste TAM listedir).")
        item_id = as_int(item.get("item_id"), 0)
        if item_id <= 0:
            return {}, "Stok satırındaki kalem kimliği geçersiz."
        if item_id in seen:
            return {}, f"Stok listesinde {item_id} kalemi iki kez var."
        seen.add(item_id)
        capacity = item.get("capacity")
        if capacity is None or text(capacity) == "":
            # `null` = SINIRSIZ. Sıfırdan farklıdır: sıfır "hiç satılmasın"
            # demektir, `null` "tavan yok" demektir.
            rows.append({"item_id": item_id, "capacity": None})
            continue
        number = as_int(capacity, -1)
        if number < 0:
            return {}, "Kalem tavanı sıfır ya da daha büyük olmalı; sınırsız için boş bırakın."
        rows.append({"item_id": item_id, "capacity": number})

    total: int | None
    if capacity_total is None or text(capacity_total) == "":
        total = None
    else:
        total = as_int(capacity_total, -1)
        if total < 0:
            return {}, "Gün tavanı sıfır ya da daha büyük olmalı; sınırsız için boş bırakın."

    return {"capacity_total": total, "items": rows}, ""


def stock_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Stok yanıtını ekranın okuyacağı sade şekle indirger.

    `sold` REZERVE edilmiş porsiyondur, teslim edilmiş değil; abonelikler onu
    önceden tutar (iş kararı 5). İki bileşen AYRI durur çünkü yönetici "34
    porsiyon kaldı" dediğinde bunun 20'sinin aboneliğe ayrıldığını bilmelidir.
    """
    day = payload.get("day") if isinstance(payload.get("day"), dict) else {}
    rows = payload.get("items") if isinstance(payload.get("items"), list) else []
    blocking = payload.get("blocking") if isinstance(payload.get("blocking"), dict) else {}
    return {
        "date": text(payload.get("date")),
        "day": {
            "capacity": day.get("capacity"),
            "sold": as_int(day.get("sold"), 0),
            "sold_orders": as_int(day.get("sold_orders"), 0),
            "sold_subscriptions": as_int(day.get("sold_subscriptions"), 0),
            "remaining": day.get("remaining"),
            "full": bool(day.get("full")),
        },
        "items": [
            {
                "item_id": as_int(row.get("item_id"), 0),
                "menu_id": as_int(row.get("menu_id"), 0),
                "name": text(row.get("name")),
                "capacity": row.get("capacity"),
                "sold": as_int(row.get("sold"), 0),
                "sold_orders": as_int(row.get("sold_orders"), 0),
                "sold_subscriptions": as_int(row.get("sold_subscriptions"), 0),
                "remaining": row.get("remaining"),
                "full": bool(row.get("full")),
                # `sold_out` MUTFAĞIN ELLE koyduğu işarettir; `full` tavanın
                # dolmasıdır ve otomatiktir. Bir ürün tavanı dolmadan da
                # tükenmiş olabilir (malzeme bitti) — ikisi karıştırılmamalı.
                "sold_out": bool(row.get("sold_out")),
            }
            for row in rows if isinstance(row, dict)
        ],
        "blocking": {
            "day": bool(blocking.get("day")),
            "items": [as_int(value, 0) for value in (blocking.get("items") or [])],
        },
    }
