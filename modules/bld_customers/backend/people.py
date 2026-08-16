"""Müşteri alanının saf kuralları — ağ yok, depo yok, istisna yok.

NEDEN AYRI DOSYA: burada yalnız biçim, sözlük ve maskeleme var. Sunucudan gelen
satırın panelin okuyabileceği şekle çevrilmesi, telefonun temizlenmesi, vergi
numarasının ölçülmesi, süzgeç değerlerinin sözleşmedeki kümeye indirgenmesi ve
denetim izine yazılacak maskenin üretilmesi — hepsi ağa çıkmadan sınanabilir.
Servis bu dosyaya bakarak karar verir, kendi içinde ikinci bir sözlük tutmaz.

ALAN ADLARI SÖZLEŞMEDEN GELİR (`BLD/docs/control/customers.md` şema tablosu).
Uydurulmuş bir ad (`phone`, `company`, `name`) burada sessizce boş döner ve
ekran "veri yok" der; bu yüzden her dönüştürücü SÖZLEŞMEDEKİ adı okur ve
bulamadığında varsayılanı yazar, ikinci bir ad DENEMEZ.

MASKELEME İKİ AYRI SORUYA CEVAP VERİR VE KARIŞTIRILMAMALIDIR:

  · EKRANDA maskeleme YOKTUR. Sözleşme bunu açıkça reddediyor: yönetici
    müşteriyi telefonundan tanır ve maskeli bir listede doğru kaydı seçemez,
    hepsini tek tek açmak zorunda kalır — yani her arama için bir düzine
    denetim satırı doğar. Maskeleme orada gizliliği artırmaz, izi bozar.
  · DENETİM İZİNDE maskeleme ZORUNLUDUR (`mask_phone`). İz "ne değişti"
    sorusuna cevap vermeli, kişisel verinin ikinci bir kopyasını tutmamalı.

Bu dosyada bir `mask_email` YOKTUR ve bilerek yoktur: e-posta hiçbir uçta
yazılamıyor, dolayısıyla hiçbir denetim satırında "eskisi/yenisi" olarak
görünmez. Yazılmayan bir alan için maske üretmek, kullanılmayan bir kapıyı
kilitlemek olurdu.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------- sınırlar

#: Gerekçe alt sınırı — `00-genel.md` §3. Sunucu da denetler (K9, çift kapı).
MIN_REASON = 10

#: Gerekçe üst sınırı. Müşteri alanında 500'dür; 160'lık sıkı sınır yalnız
#: sipariş revizyonu ve durum geçişindedir ve bu ekranın işi değildir.
MAX_REASON = 500

#: Aktör (oturumdan gelir, gövdeden DEĞİL). Alt sınır KVKK okumalarının
#: zorunlu kıldığı sınırdır (`00-genel.md` §9.1): iki karakterden kısa bir ad,
#: denetim izinde kimseyi işaret etmez.
ACTOR_MIN = 2
ACTOR_MAX = 120

#: Arama en az iki karakter (sözleşme `GET /`). Tek harflik bir arama bütün
#: müşteri tablosunu döndürürdü; sayfalama onu yavaşlatır ama engellemez.
QUERY_MIN = 2

#: Sayfalama — `00-genel.md` §5. Tavan sunucununkiyle aynıdır.
PER_PAGE_DEFAULT = 25
PER_PAGE_MAX = 100

#: Telefon: temizlenmiş hâli 10–15 hane (sözleşme `PATCH /{id}`). Üst sınır
#: uluslararası numaralar içindir; alt sınır Türkiye'deki on hanedir.
PHONE_MIN_DIGITS = 10
PHONE_MAX_DIGITS = 15

#: Telefonda kabul edilen ham karakterler (sözleşme). Personel numarayı
#: "(0532) 123-45-67" diye yazabilmeli; temizleme bizim işimiz.
PHONE_ALLOWED = re.compile(r"^[0-9+()\-\s]+$")

#: Vergi numarası 10 hane, TC kimlik numarası 11 hane. Sözleşme ikisini de
#: kabul ediyor ve alan tek: kurumsal müşteri de şahıs da fatura istiyor.
TAX_NO_LENGTHS = (10, 11)

#: Ad ve soyad boş bırakılamaz (sözleşme). Üst sınır çekirdek `customers`
#: tablosunun kolon genişliğidir; sözleşme bir sayı vermiyor, bu yüzden burada
#: GEVŞEK bir sınır durur — sıkı sınır uydurmak, sunucunun kabul edeceği bir
#: adı gönderilemez kılardı.
NAME_MIN = 1
NAME_MAX = 128

#: Serbest metin kurum etiketlerinin üst sınırı. Aynı gerekçe: sözleşme sayı
#: vermiyor, burada yalnız kazara yapıştırılmış bir sayfa metnini durduracak
#: kadar gevşek bir tavan var.
LABEL_MAX = 255

# --------------------------------------------------------------- sözlükler

#: `PATCH /{id}` ile yazılabilen alanlar — SÖZLEŞMEDEKİ TAM LİSTE.
#: Listede olmayan bir anahtar REDDEDİLİR, sessizce düşürülmez: sözleşme
#: "başka bir alan gönderilirse istek TÜMÜYLE reddedilir" diyor ve gerekçesi
#: yazılı — bilinmeyen alanı yok saymak, e-posta değiştirdiğini sanan bir
#: yöneticiye "başarılı" demek olurdu.
WRITABLE_FIELDS = (
    "first_name", "last_name", "telephone",
    "org_name", "tax_office", "tax_no", "contact_person", "org_phone",
)

#: ASLA YAZILAMAYAN ve gönderildiğinde ÖZEL BİR CÜMLEYLE reddedilen alanlar.
#: Genel "tanınmayan alan" mesajı da işi görürdü ama bu üçü için yetmez:
#: yönetici e-postayı değiştirmeye çalıştığında NEDEN olmadığını okumalı,
#: yoksa aynı isteği başka bir adla tekrar dener.
FORBIDDEN_FIELDS = {
    "email": (
        "E-posta yazılamaz: giriş kimliğidir ve değiştirmek hesabı devretmek "
        "anlamına gelir, doğrulama akışı gerektirir. Yanlış yazılmış bir "
        "e-postayı müşteri kendi hesap ekranından ya da destek üzerinden "
        "düzeltir."
    ),
    "password": (
        "Parola hiçbir uçta geçmez: ne okunur, ne yazılır, ne sıfırlanır. Bir "
        "yönetim panelinden parola yazabilmek, panele erişen herkesin her "
        "müşterinin hesabına girebilmesi demektir. Parola sıfırlama müşterinin "
        "kendi akışıdır."
    ),
    "account_type": (
        "Hesap türü okunur, yazılmaz: kurumsal sipariş kapısı kaldırıldığı için "
        "bu alan artık bir yetki belirlemiyor, yalnız geçmiş kayıtların "
        "etiketi. Kurum bilgileri serbest metin etiket olarak düzenlenebilir."
    ),
    "status": (
        "Hesap durumu bu uçtan yazılmaz: kapatma ve açma ayrı bir izin ve ayrı "
        "bir denetim satırı ister. Müşteri kartındaki 'Hesabı kapat' düğmesini "
        "kullanın."
    ),
}

#: `status` süzgeci (sözleşme `GET /`). VARSAYILAN `all`: yönetimin ilk sorusu
#: çoğu zaman "bu müşteri nerede" biçiminde gelir ve cevabı "hesabı kapatılmış"
#: olabilir; varsayılan süzgeç onu gizleseydi müşteri kaybolmuş görünürdü.
STATUS_FILTERS = ("all", "active", "disabled")
STATUS_LABELS = {
    "all": "Hepsi",
    "active": "Açık hesaplar",
    "disabled": "Kapalı hesaplar",
}
DEFAULT_STATUS = "all"

#: `sort` seçenekleri (sözleşme `GET /`).
SORTS = ("name", "created", "last_order")
SORT_LABELS = {
    "name": "Ada göre",
    "created": "Kayıt tarihine göre",
    "last_order": "Son siparişe göre",
}
DEFAULT_SORT = "name"

DIRECTIONS = ("asc", "desc")
DEFAULT_DIRECTION = "asc"

#: Hesap türü etiketleri (`bld_account_type`). SALT OKUNUR.
ACCOUNT_TYPES = {
    "corporate": "Kurumsal",
    "individual": "Bireysel",
}

#: Sipariş durum kodları — `OrderStatusTransition::CODES` (`orders.md`).
#: Burada yalnız ETİKET için duruyorlar: bu ekranda durum değiştiren bir uç
#: yoktur ve olmayacaktır (o `bld_orders`'ın kulvarı).
ORDER_STATUS_LABELS = {
    "yeni": "Yeni",
    "onaylandi": "Onaylandı",
    "hazirlaniyor": "Hazırlanıyor",
    "hazir": "Hazır",
    "yolda": "Yolda",
    "teslim_edildi": "Teslim edildi",
    "iptal": "İptal",
}

#: Abonelik durumları (`subscriptions.md`).
SUBSCRIPTION_STATUS_LABELS = {
    "pending": "Onay bekliyor",
    "active": "Etkin",
    "paused": "Duraklatıldı",
    "cancelled": "İptal",
    "expired": "Süresi doldu",
}

#: Yerel yazma izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

#: Yerel KVKK erişim izinin `result` sütununun alabileceği değerler.
READ_OK = "okundu"
READ_FAILED = "hata"

#: Yerel yazma izi `action` adları. Sunucudaki `veykemtu_control_audit`
#: karşılıkları `customers.md` denetim tablosundadır ve AYNI adları taşır: iki
#: defteri yan yana koyup okuyabilmek, "gönderildi mi" sorusunun tek cevabıdır.
WRITE_ACTIONS = ("customer.update", "customer.disable", "customer.enable")

#: Yerel KVKK erişim izinin `scope` değerleri. `action` her satırda
#: `customer.read`'tir (sunucudaki adla aynı); hangi ekranın açıldığını `scope`
#: söyler. İkisini tek sütuna sıkıştırmak, iki defteri karşılaştırılamaz
#: kılardı.
READ_SCOPES = ("list", "detail", "orders", "subscriptions", "addresses", "sms")
READ_SCOPE_LABELS = {
    "list": "Müşteri araması",
    "detail": "Müşteri kartı",
    "orders": "Sipariş geçmişi",
    "subscriptions": "Abonelikler",
    "addresses": "Adres defteri",
    "sms": "SMS gönderim kaydı",
}

#: Sunucudaki okuma denetimi eylemi (`00-genel.md` §9.2).
READ_ACTION = "customer.read"

#: SMS gönderim kaydı okumasının YEREL eylem adı. `customer.read` DEĞİLDİR ve
#: bilerek değildir: o uç `control/sms/log` altındadır, `control/customers/*`
#: altında değil — sunucu onun için `customer.read` satırı YAZMAZ. Aynı adı
#: kullansaydık iki defteri karşılaştıran biri, sunucuda karşılığı olmayan
#: satırları "sunucu kayıp vermiş" diye okurdu. Ayrım raporlanmıştır.
SMS_READ_ACTION = "sms.read"


# ------------------------------------------------------------- dönüştürme

def as_int(value: Any, fallback: int = 0) -> int:
    """Sayıya çevirir; çevrilemezse yedek değer. Sunucu `null` gönderebilir."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def as_bool(value: Any) -> bool:
    """Metin, sayı ve boolean'ı tek kurala indirger.

    Tercih tablosundan `"0"` metni okunuyor ve `bool("0")` Python'da `True`;
    buradaki açık liste o tuzağı kapatır.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "evet", "yes", "on"}


def text(value: Any) -> str:
    """Kırpılmış metin; `None` boş dizeye düşer."""
    return str(value or "").strip()


def optional_text(value: Any) -> str | None:
    """Boş metni `None` yapar.

    Sözleşme kurum alanlarını `string|null` diye tanımlıyor ve boş dizeyi
    `null` sayıyor. Boş dize göndermek, "yönetici bu alanı boşalttı" ile
    "alanda boş bir metin var" arasında sunucuda anlamsız bir ayrım üretirdi.
    """
    cleaned = text(value)
    return cleaned or None


def now_iso() -> str:
    """Yerel iz için ISO 8601 UTC damgası (`00-genel.md` §6)."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def digits(value: Any) -> str:
    """Yalnız rakamlar. Telefon ve vergi numarası ölçülürken kullanılır."""
    return re.sub(r"\D", "", str(value or ""))


def mask_phone(value: Any) -> str:
    """Denetim izine yazılacak telefon maskesi: ilk 3 + `****` + son 3.

    Biçim `sms.md`'deki maskeyle AYNIDIR (`532****567`) — iki defteri yan yana
    okuyan biri iki farklı maske görmemeli.

    Kısa numara maskelenmez, tümüyle GİZLENİR: dört haneli bir numarada ilk üç
    ve son üç haneyi vermek numaranın kendisini vermektir.
    """
    only = digits(value)
    if not only:
        return ""
    if len(only) < 7:
        return "*" * len(only)
    return f"{only[:3]}****{only[-3:]}"


# ------------------------------------------------------------- doğrulama

def reason_error(reason: Any) -> str:
    """Gerekçe denetimi. Hata yoksa boş dize.

    Backend'de DE doğrulanır (K9): arayüzde zorunlu göstermek, istemcinin
    gövdeyi elle kurmasını engellemez.
    """
    cleaned = text(reason)
    if len(cleaned) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı: bu satır silinmeyen bir "
                f"deftere yazılıyor ve 'düzeltme' yazan bir gerekçe altı ay sonra "
                f"hiçbir şey anlatmıyor.")
    if len(cleaned) > MAX_REASON:
        return f"Gerekçe en çok {MAX_REASON} karakter olabilir; {len(cleaned)} karakter verildi."
    return ""


def actor_error(actor: Any) -> str:
    """Aktör denetimi. Hata yoksa boş dize.

    KVKK okumalarında bu alan ZORUNLUDUR (`00-genel.md` §9.1) ve geçit eksik
    aktörle isteği hiç göndermez. Aktör oturumdan geliyor, yani normalde hep
    dolu; buradaki kapı, adı olmayan bir hesabın müşteri defterini sessizce
    okumasını engeller.
    """
    cleaned = text(actor)
    if len(cleaned) < ACTOR_MIN:
        return ("Müşteri kayıtlarını görmek için oturumdaki kullanıcının adı gerekli: "
                "bu uçlarda OKUMALAR da denetim izine düşer (KVKK).")
    if len(cleaned) > ACTOR_MAX:
        return f"Kullanıcı adı en çok {ACTOR_MAX} karakter olabilir."
    return ""


def name_error(value: Any, label: str) -> str:
    """Ad/soyad denetimi. Sözleşme: boş bırakılamaz."""
    cleaned = text(value)
    if len(cleaned) < NAME_MIN:
        return (f"{label} boş bırakılamaz: müşterinin adı sistemde her yerde görünüyor "
                f"— siparişte, fişte, faturada.")
    if len(cleaned) > NAME_MAX:
        return f"{label} en çok {NAME_MAX} karakter olabilir."
    return ""


def phone_error(value: Any, label: str) -> str:
    """Telefon denetimi. Boş dize ve `None` GEÇERLİDİR (alan `null` olur).

    Sözleşme: "rakam, boşluk, `+`, `(`, `)`, `-` karakterleri; temizlenmiş hâli
    10–15 hane. Boş dize → `null`."
    """
    raw = text(value)
    if not raw:
        return ""
    if not PHONE_ALLOWED.match(raw):
        return (f"{label} yalnız rakam, boşluk ve `+ ( ) -` içerebilir; "
                f"'{raw}' kabul edilmedi.")
    only = digits(raw)
    if len(only) < PHONE_MIN_DIGITS or len(only) > PHONE_MAX_DIGITS:
        return (f"{label} temizlendiğinde {PHONE_MIN_DIGITS}-{PHONE_MAX_DIGITS} hane "
                f"olmalı; {len(only)} hane sayıldı.")
    return ""


def tax_no_error(value: Any) -> str:
    """Vergi/TC numarası denetimi. Boş GEÇERLİDİR (alan `null` olur).

    On hane vergi numarası, on bir hane TC kimlik numarasıdır; sözleşme ikisini
    de kabul ediyor çünkü alan tek ve şahıs müşteri de fatura istiyor.
    """
    raw = text(value)
    if not raw:
        return ""
    only = digits(raw)
    if only != raw:
        return "Vergi/TC numarası yalnız rakamlardan oluşmalı."
    if len(only) not in TAX_NO_LENGTHS:
        return (f"Vergi numarası 10, TC kimlik numarası 11 hane olmalı; "
                f"{len(only)} hane verildi.")
    return ""


def patch_error(fields: Any) -> str:
    """Kısmi yazma gövdesinin kapısı. Hata yoksa boş dize.

    ÜÇ AYRI RET, ÜÇ AYRI CÜMLE:
      1. Boş gövde — hiçbir şey değiştirmeden denetim izine satır yazardı.
      2. Yasak alan (`email`, `password`, `account_type`, `status`) — kendi
         gerekçesiyle reddedilir, çünkü yönetici NEDEN olmadığını okumalı.
      3. Tanınmayan alan — sözleşmenin dışında bir ad; sunucu zaten isteği
         tümüyle reddediyor, biz onu hiç göndermiyoruz.
    """
    if not isinstance(fields, dict) or not fields:
        return ("En az bir alan gönderilmeli: gönderilmeyen alan değişmez, bu yüzden "
                "boş bir güncelleme hiçbir şey yapmaz.")
    for key, message in FORBIDDEN_FIELDS.items():
        if key in fields:
            return message
    unknown = sorted(key for key in fields if key not in WRITABLE_FIELDS)
    if unknown:
        return (f"Tanınmayan alan: {', '.join(unknown)}. Yazılabilenler: "
                f"{', '.join(WRITABLE_FIELDS)}.")
    return ""


def clean_patch(fields: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Kısmi gövdeyi doğrular ve temizler. `(gövde, hata)` döner.

    `patch_error` önce çağrılmış olmalı; burada yalnız DEĞER doğrulaması var.
    Temizlenmiş gövdede boş metinler `None`'a düşer (sözleşme: "Boş dize →
    `null`") ama AD VE SOYAD böyle değildir: onlar boş bırakılamaz ve zaten
    yukarıdaki denetimden geçemez.
    """
    body: dict[str, Any] = {}

    if "first_name" in fields:
        error = name_error(fields["first_name"], "Ad")
        if error:
            return {}, error
        body["first_name"] = text(fields["first_name"])
    if "last_name" in fields:
        error = name_error(fields["last_name"], "Soyad")
        if error:
            return {}, error
        body["last_name"] = text(fields["last_name"])

    if "telephone" in fields:
        error = phone_error(fields["telephone"], "Telefon")
        if error:
            return {}, error
        body["telephone"] = optional_text(fields["telephone"])
    if "org_phone" in fields:
        error = phone_error(fields["org_phone"], "Kurum telefonu")
        if error:
            return {}, error
        body["org_phone"] = optional_text(fields["org_phone"])

    if "tax_no" in fields:
        error = tax_no_error(fields["tax_no"])
        if error:
            return {}, error
        body["tax_no"] = optional_text(fields["tax_no"])

    for key, label in (("org_name", "Kurum adı"), ("tax_office", "Vergi dairesi"),
                       ("contact_person", "Yetkili kişi")):
        if key not in fields:
            continue
        value = text(fields[key])
        if len(value) > LABEL_MAX:
            return {}, f"{label} en çok {LABEL_MAX} karakter olabilir."
        body[key] = optional_text(value)

    return body, ""


# --------------------------------------------------------------- süzgeçler

def clean_status(value: Any) -> str:
    cleaned = text(value).lower()
    return cleaned if cleaned in STATUS_FILTERS else DEFAULT_STATUS


def clean_sort(value: Any) -> str:
    cleaned = text(value).lower()
    return cleaned if cleaned in SORTS else DEFAULT_SORT


def clean_direction(value: Any) -> str:
    cleaned = text(value).lower()
    return cleaned if cleaned in DIRECTIONS else DEFAULT_DIRECTION


def clean_per_page(value: Any, fallback: int = PER_PAGE_DEFAULT) -> int:
    """Sayfa boyunu 1..100 aralığına çeker (`00-genel.md` §5).

    Tavanın ÜSTÜ sunucuda sessizce kırpılıyor: 250 istemek hata vermez, yalnız
    100 döner ve "hepsini aldım" sanan istemci veri kaybeder. Bu yüzden kırpma
    burada da yapılır.
    """
    size = as_int(value, 0)
    if size <= 0:
        size = as_int(fallback, PER_PAGE_DEFAULT)
    return min(max(size, 1), PER_PAGE_MAX)


def clean_query(value: Any) -> str:
    """Aramayı temizler; iki karakterden kısaysa BOŞ döner.

    Kısa arama isteğe KONMAZ (gönderilse sunucu `422` verirdi ve kullanıcı
    yazmaya devam ederken hata görürdü). Boş arama ise geçerlidir ve ilk sayfayı
    getirir — sözleşme "süzgeçsiz istek serbesttir" diyor.
    """
    cleaned = text(value)
    return cleaned if len(cleaned) >= QUERY_MIN else ""


def filter_spec() -> dict[str, Any]:
    """Panelin süzgeç kutularını çizmek için kullandığı sözleşme künyesi.

    Sözlükler SUNUCUDAN GELMİYOR ve buradan gidiyor: panel kendi listesini
    yazsaydı iki liste sessizce ayrışır ve ekran, sunucunun tanımadığı bir
    süzgeç değeri gönderirdi.
    """
    return {
        "status": [{"value": key, "label": STATUS_LABELS[key]} for key in STATUS_FILTERS],
        "sort": [{"value": key, "label": SORT_LABELS[key]} for key in SORTS],
        "direction": [{"value": "asc", "label": "Artan"}, {"value": "desc", "label": "Azalan"}],
        "query_min": QUERY_MIN,
        "per_page_max": PER_PAGE_MAX,
        "writable_fields": list(WRITABLE_FIELDS),
        "read_scopes": [{"value": key, "label": READ_SCOPE_LABELS[key]}
                        for key in READ_SCOPES],
    }


def page_meta(meta: Any, *, page: int, per_page: int, rows: int) -> dict[str, Any]:
    """Sayfalama künyesi. Sunucu vermezse elde olandan üretir.

    `total` sunucudan gelmezse ELDEKİ SATIR SAYISI yazılır ve `last_page` 1
    olur: bilinmeyen bir toplamı sıfır yazmak, sayfalayıcıyı "kayıt yok"
    göstermeye zorlardı — oysa ekranda satırlar duruyor.
    """
    source = meta if isinstance(meta, dict) else {}
    total = as_int(source.get("total"), rows)
    size = clean_per_page(source.get("per_page"), per_page)
    last = as_int(source.get("last_page"), 0)
    if last <= 0:
        last = max(1, -(-total // size)) if total else 1
    return {
        "page": max(1, as_int(source.get("page"), page)),
        "per_page": size,
        "total": total,
        "last_page": last,
    }


# ------------------------------------------------------------ satır şekli

def customer_row(raw: Any) -> dict[str, Any]:
    """`GET /` satırı → panelin okuduğu şekil.

    LİSTE MASKELENMEZ (sözleşme). Telefon ve e-posta olduğu gibi taşınır;
    yöneticinin müşteriyi tanıması için tek yol budur.

    `full_name` TÜRETİLMİŞTİR ve sunucudan gelmez: tabloda tek sütun isteniyor
    ve birleştirmeyi yirmi yerde tekrarlamak yerine bir kez burada yapıyoruz.
    """
    row = raw if isinstance(raw, dict) else {}
    first = text(row.get("first_name"))
    last = text(row.get("last_name"))
    account = text(row.get("account_type"))
    return {
        "customer_id": as_int(row.get("customer_id")),
        "first_name": first,
        "last_name": last,
        # TÜRETİLMİŞ — sözleşmede yok, tablo sütunu için burada üretiliyor.
        "full_name": f"{first} {last}".strip(),
        "email": text(row.get("email")),
        "telephone": text(row.get("telephone")),
        "status": as_bool(row.get("status")),
        "is_activated": as_bool(row.get("is_activated")),
        "account_type": account,
        # TÜRETİLMİŞ — etiket sözlüğü panelde ikinci kez yazılmasın diye.
        "account_type_label": ACCOUNT_TYPES.get(account, account or "—"),
        "org_name": text(row.get("org_name")),
        "order_count": as_int(row.get("order_count")),
        "last_order_at": text(row.get("last_order_at")),
        "subscription_count": as_int(row.get("subscription_count")),
        "created_at": text(row.get("created_at")),
    }


def customer_detail(raw: Any) -> dict[str, Any]:
    """`GET /{id}` gövdesi → panelin okuduğu şekil.

    `stats` BURADA gelir, ayrı bir uçta değil (sözleşme): müşteri kartını açan
    yönetici zaten bu sayıları görmek istiyor ve ayrı bir çağrı ikinci bir
    denetim satırı yazardı.

    Eksik bir `stats` bloğu SIFIRLARLA doldurulmaz: `-1` "bilinmiyor" demektir
    ve panel o kutuyu çizmez. Sıfır yazmak, "hiç sipariş vermemiş" ile "sayı
    gelmedi"yi aynı gösterirdi.
    """
    row = raw if isinstance(raw, dict) else {}
    base = customer_row(row)
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
    return {
        **base,
        "tax_office": text(row.get("tax_office")),
        "tax_no": text(row.get("tax_no")),
        "contact_person": text(row.get("contact_person")),
        "org_phone": text(row.get("org_phone")),
        "last_login": text(row.get("last_login")),
        "stats": {
            "order_count": as_int(stats.get("order_count"), -1),
            "cancelled_order_count": as_int(stats.get("cancelled_order_count"), -1),
            "total_spent_kurus": as_int(stats.get("total_spent_kurus"), -1),
            "first_order_at": text(stats.get("first_order_at")),
            "last_order_at": text(stats.get("last_order_at")),
            "active_subscription_count": as_int(stats.get("active_subscription_count"), -1),
            "unpaid_total_kurus": as_int(stats.get("unpaid_total_kurus"), -1),
            "address_count": as_int(stats.get("address_count"), -1),
        },
    }


def order_row(raw: Any) -> dict[str, Any]:
    """Müşterinin sipariş satırı. Biçim `orders.md` → `GET /` ile AYNIDIR.

    İki farklı sipariş şekli tanımlamak, panelin iki ayrı tablo bileşeni
    yazması demekti (sözleşme bunu açıkça söylüyor). Bu ekran siparişi
    DEĞİŞTİRMEZ; düzenleme `bld_orders`'ın işidir ve buradan oraya bir düğme
    de konmaz — bir iş eylemi tek ekranda durur.
    """
    row = raw if isinstance(raw, dict) else {}
    status = text(row.get("status"))
    return {
        "id": as_int(row.get("id")),
        "order_number": text(row.get("order_number")),
        "status": status,
        "status_label": ORDER_STATUS_LABELS.get(status, status or "—"),
        "service_date": text(row.get("service_date")),
        "delivery_type": text(row.get("delivery_type")),
        "item_count": as_int(row.get("item_count")),
        "total_kurus": as_int(row.get("total_kurus")),
        "payment_method": text(row.get("payment_method")),
        "payment_status": text(row.get("payment_status")),
        "is_subscription": as_bool(row.get("is_subscription")),
        "subscription_id": as_int(row.get("subscription_id")) or None,
        "has_invoice": as_bool(row.get("has_invoice")),
        "created_at": text(row.get("created_at")),
    }


def subscription_row(raw: Any) -> dict[str, Any]:
    """Müşterinin abonelik satırı. Biçim `subscriptions.md` → `GET /` ile aynı.

    Bu ekran aboneliği DEĞİŞTİRMEZ (etkinleştirme, duraklatma, üretim
    `bld_subscriptions`'ın işidir). Burada yalnız "bu müşterinin aboneliği var
    mı, borcu var mı" sorusu cevaplanır — hesabı kapatmadan önce sorulması
    gereken soru budur.
    """
    row = raw if isinstance(raw, dict) else {}
    status = text(row.get("status"))
    days = row.get("service_days")
    return {
        "id": as_int(row.get("id")),
        "status": status,
        "status_label": SUBSCRIPTION_STATUS_LABELS.get(status, status or "—"),
        "start_date": text(row.get("start_date")),
        "end_date": text(row.get("end_date")),
        "service_days": [as_int(day) for day in days] if isinstance(days, list) else [],
        "menu_mode": text(row.get("menu_mode")),
        "default_quantity": as_int(row.get("default_quantity")),
        "agreed_unit_price_kurus": as_int(row.get("agreed_unit_price_kurus"), -1),
        "payment_mode": text(row.get("payment_mode")),
        "contract_status": text(row.get("contract_status")),
        "next_service_date": text(row.get("next_service_date")),
        "unpaid_periods": as_int(row.get("unpaid_periods")),
        "unpaid_total_kurus": as_int(row.get("unpaid_total_kurus")),
    }


def address_row(raw: Any) -> dict[str, Any]:
    """Adres defteri satırı. SALT OKUNUR.

    Adres yazan bir uç YOKTUR ve burada uydurulmaz: adres siparişe
    KOPYALANIYOR, bağlanmıyor; defteri panelden düzenlemek geçmiş siparişlerin
    adresini değiştirmez ve yönetici değiştirdiğini sanır.

    Koordinatlar taşınır ama HARİTA ÇİZİLMEZ: panelden dış bir harita
    servisine istek atmak, müşterinin ev adresini üçüncü bir tarafa
    göndermek olurdu.
    """
    row = raw if isinstance(raw, dict) else {}
    return {
        "address_id": as_int(row.get("address_id")),
        "label": text(row.get("label")),
        "line_1": text(row.get("line_1")),
        "line_2": text(row.get("line_2")),
        "city": text(row.get("city")),
        "district": text(row.get("district")),
        "neighbourhood": text(row.get("neighbourhood")),
        "postcode": text(row.get("postcode")),
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "is_default": as_bool(row.get("is_default")),
    }


def sms_row(raw: Any) -> dict[str, Any]:
    """SMS gönderim kaydı satırı (`sms.md` → `GET /log`).

    TELEFON SUNUCUDA MASKELENİR ve burada TEKRAR maskelenmez: gelen değer zaten
    `532****567` biçimindedir ve ikinci bir maske onu `532****567` yerine
    `532****567`in maskesine çevirirdi — yani okunamaz hâle getirirdi.
    Gövde de sunucuda 120 karakterde kırpılmış gelir.
    """
    row = raw if isinstance(raw, dict) else {}
    return {
        "id": as_int(row.get("id")),
        "template_key": text(row.get("template_key")),
        "phone": text(row.get("phone")),
        "order_id": as_int(row.get("order_id")) or None,
        "subscription_id": as_int(row.get("subscription_id")) or None,
        "body": text(row.get("body")),
        "segments": as_int(row.get("segments")),
        "status": text(row.get("status")),
        "error": text(row.get("error")),
        "context": text(row.get("context")),
        "sent_at": text(row.get("sent_at")),
    }


def change_log(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """Denetim izine yazılacak değişiklik listesi — TELEFON MASKELİ.

    Sözleşme (`PATCH /{id}`) biçimi sabitliyor:
    `{"changes": [{"field": "telephone", "from": "532****567", "to": "532****543"}]}`

    Maskelenen alanlar telefonlardır. Kurum adı ve vergi numarası maskelenmez:
    ikisi de ticari kayıttır, faturada zaten basılıdır ve maskelenirse "ne
    değişti" sorusu cevapsız kalır. E-posta hiç yazılamadığı için listede
    görünmez.
    """
    masked = {"telephone", "org_phone"}
    changes: list[dict[str, Any]] = []
    for field in WRITABLE_FIELDS:
        if field not in after:
            continue
        old = before.get(field)
        new = after.get(field)
        if text(old) == text(new):
            continue
        if field in masked:
            changes.append({"field": field, "from": mask_phone(old), "to": mask_phone(new)})
        else:
            changes.append({"field": field, "from": text(old), "to": text(new)})
    return changes
