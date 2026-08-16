"""Bildirim metninin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın tehlikeli tarafı hesaplamadadır, ağda değil: bir
şablon 1.800 kişiye gidiyor ve yanlış hesaplanan bir SMS parçası üç katı fatura
demek. Bu çeviriler servise gömülseydi tek satırı bile ağsız test edilemezdi.

YEDİ TUZAK — hepsinin karşılığı burada bir fonksiyondur:

 1. Bilinmeyen değişken sessizce boşa çevrilirse 1.800 kişi "Sayın ,"
    diye başlayan mesaj alır → `render` bilinmeyeni YERİNDE BIRAKIR ve
    `missing` listesiyle bildirir.
 2. Türkçe harf SMS'i pahalılaştırır (ş/ğ/ı 2 septet) → `sms_plan`
    `plan_text`/`offending` ile hangi karakterin ne kadara mal olduğunu söyler.
 3. `₺` GSM-7'de YOKTUR: tek karakter mesajı UCS-2'ye düşürür ve 160 sınırı
    70'e iner → örnek veride bilerek `TL` yazılır (aşağıdaki not).
 4. Sessiz saat penceresinin başlangıcı ile bitişi eşitse 24 saatlik pencere
    olur ve HİÇBİR bildirim gitmez → `quiet_state` bunu uygulamaz, söyler.
 5. Gece yarısını aşan pencere (22:00–08:00) düz karşılaştırmayla yanlış
    çalışır → `quiet_state` sarmayı ayrıca ele alır.
 6. Şablon kimliği iki kaynaktan gelir (mağaza e-posta şablonu / yerel SMS
    şablonu); çıplak sayı ikisini karıştırır → kimlik `store:12` · `local:3`.
 7. Maliyet bilinmiyorsa sıfır göstermek "bedava" demektir → birim fiyat
    ayarlanmamışsa `cost` `None` döner ve ekran `—` yazar.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from km_sdk import offending, plan_text, simplify

#: Gerekçenin en az uzunluğu. Geçit (store_api) de 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Toplu gönderimde tek seferde kabul edilen en çok alıcı. Canlıda müşteri
#: sayısı 1.800 civarında; tavan büyümeye yer bırakır ve bozuk bir süzgecin
#: "herkese gönder" hâline gelmesini engeller.
DEFAULT_RECIPIENT_CAP = 5_000

CHANNELS = ("email", "sms", "push", "whatsapp")

#: Toplu gönderimde seçilebilen kitleler. Serbest metin kabul edilmez: bilinmeyen
#: bir kitle adını mağaza sessizce yok sayarsa süzgeçsiz — yani HERKESE giden —
#: bir gönderim kalırdı (Laravel tanımadığı alanı yok sayar).
AUDIENCES = ("subscribers", "customers", "recent_buyers")

AUDIENCE_LABELS = {
    "subscribers": "Bülten aboneleri",
    "customers": "Kayıtlı müşteriler",
    "recent_buyers": "Son 90 günde alışveriş yapanlar",
}

CHANNEL_LABELS = {
    "email": "E-posta",
    "sms": "SMS",
    "push": "Push",
    "whatsapp": "WhatsApp",
}

#: Gönderim durumları. Sıra ekrandaki süzgeç sırasıdır ve akışı anlatır.
STATUS_LABELS = {
    "queued": "Kuyrukta",
    "sent": "Gönderildi",
    "delivered": "İletildi",
    "opened": "Açıldı",
    "failed": "Başarısız",
    "rejected": "Reddedildi",
}

#: "İş bitmedi" sayılan durumlar — başarısız çipi bunları toplar.
BAD_STATUSES = ("failed", "rejected")

#: Değişken paleti. `sample` ÖNİZLEME içindir.
#:
#: TUZAK 3 — tutar örneğinde `₺` YOKTUR. Simge GSM-7 kümesinde değildir ve tek
#: başına mesajı UCS-2'ye düşürür: 160 karakterlik sınır 70'e iner, sayaç
#: önizlemede 3 parça gösterirken gerçek şablon 1 parça olurdu. Örnek veri
#: gerçeği temsil etmeli; para birimi SMS'te `TL` yazılır.
VARIABLES = (
    ("magaza_adi", "Mağaza adı", "BBD Store"),
    ("musteri_adi", "Müşteri adı", "Ayse Yilmaz"),
    ("siparis_no", "Sipariş numarası", "SP-2026-004173"),
    ("siparis_tarihi", "Sipariş tarihi", "13.08.2026"),
    ("tutar", "Tutar", "1.249,90 TL"),
    ("urun_adi", "Ürün adı", "9. Sinif Matematik Soru Bankasi"),
    ("adet", "Adet", "2"),
    ("kargo_firma", "Kargo firması", "Aras Kargo"),
    ("kargo_takip", "Kargo takip numarası", "7350041982"),
    # TAKİP NUMARASI İLE TAKİP BAĞLANTISI AYRI ALANLARDIR. Müşteriye yalnız
    # numarayı göndermek, onu taşıyıcının sitesinde numara aratmaya zorlar;
    # bağlantı taşıyıcıdan gelir ve uydurulmaz (bkz. `lifecycle.order_values`).
    ("kargo_takip_linki", "Kargo takip bağlantısı",
     "https://bbdstore.com.tr/kargo/7350041982"),
    ("teslim_tarihi", "Tahmini teslim", "15.08.2026"),
    ("iade_no", "İade numarası", "ID-2026-000318"),
    ("talep_no", "Talep numarası", "TL-2026-000091"),
    ("stok_adet", "Kalan stok", "3"),
    ("yorum_puani", "Yorum puanı", "4"),
    ("siparis_linki", "Sipariş bağlantısı", "https://bbdstore.com.tr/s/4173"),
)

VARIABLE_KEYS = tuple(key for key, _, _ in VARIABLES)

#: Kural kurulabilen olaylar ve o olayda ANLAMLI olan değişkenler.
#: Palet olaya göre daralır: sipariş oluştu şablonuna `iade_no` koymak,
#: gönderim anında boş kalan bir alan üretir.
EVENTS = (
    ("order.created", "Sipariş oluştu",
     ("magaza_adi", "musteri_adi", "siparis_no", "siparis_tarihi", "tutar", "siparis_linki")),
    ("order.paid", "Ödeme alındı",
     ("magaza_adi", "musteri_adi", "siparis_no", "tutar", "siparis_linki")),
    ("order.shipped", "Kargoya verildi",
     ("magaza_adi", "musteri_adi", "siparis_no", "kargo_firma", "kargo_takip",
      "kargo_takip_linki", "teslim_tarihi")),
    ("order.delivered", "Teslim edildi",
     ("magaza_adi", "musteri_adi", "siparis_no", "kargo_firma", "siparis_linki")),
    ("refund.approved", "İade onaylandı",
     ("magaza_adi", "musteri_adi", "siparis_no", "iade_no", "tutar")),
    ("stock.critical", "Stok kritik",
     ("magaza_adi", "urun_adi", "stok_adet")),
    ("review.created", "Yorum geldi",
     ("magaza_adi", "musteri_adi", "urun_adi", "yorum_puani")),
    ("request.opened", "Talep açıldı",
     ("magaza_adi", "musteri_adi", "talep_no", "siparis_no")),
)

EVENT_KEYS = tuple(key for key, _, _ in EVENTS)
EVENT_LABELS = {key: label for key, label, _ in EVENTS}

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
_HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

#: SMTP ayarının `core_config` yolu — `email_config_slug` önekinden SONRASI.
#:
#: CANLIDA DOĞRULANDI (2026-08-13, `GET /api/admin/configuration?slug=emails`):
#: bağlantı bilgisi `emails.configure.smtp.*`, gönderen kimliği ise
#: `emails.configure.email_settings.*` altında duruyor — İKİ AYRI GRUP. Hepsini
#: `email_settings` altında aramak `from_address` / `from_name` gibi mağazada
#: HİÇ OLMAYAN anahtarlar üretirdi: ekran gönderen adresini "anahtar
#: bulunamadı" diye gösterir, oysa adres oradadır.
EMAIL_CONFIG_KEYS = {
    "host": "configure.smtp.host",
    "port": "configure.smtp.port",
    "encryption": "configure.smtp.encryption",
    "username": "configure.smtp.username",
    "password": "configure.smtp.password",
    "sender_email": "configure.email_settings.sender_email",
    "sender_name": "configure.email_settings.sender_name",
}

#: Ayar sözlüğünde SIR sayılan anahtar parçaları. Eşleşen değer ekrana
#: maskeli gider; ham hâli yanıtta, log'da ve raporda bulunmaz (K8).
SECRET_HINTS = ("password", "secret", "token", "api_key", "apikey", "key", "pass")


# ===================================================================== temel

def _folded_key(name: str) -> str:
    return name.replace("_", "").replace("-", "").lower()


def pick(raw: Any, *names: str, default: Any = None) -> Any:
    """Yanıttan alan okur; snake_case ve camelCase yazımların İKİSİNİ de tanır.

    TUZAK — CANLIDA DOĞRULANDI (2026-08-13, `/api/admin/marketing/subscribers`):
    mağaza `isSubscribed`, `customerId`, `customerName`, `createdAt` döndürüyor;
    Bagisto'nun veritabanı sütunları ve belgeleri ise `is_subscribed`,
    `customer_id` diyor. Tek yazıma bağlanan kod hiçbir şey bulamaz ve İSTİSNA
    DA ATMAZ — ekran sessizce "—" dolu görünür, abonelikten çıkmış müşteri
    "Abone" gözükür. Bu yüzden ad, ayraçları atılıp küçük harfe indirilerek
    eşleştirilir.

    `None` DEĞER YOK sayılır (sıradaki ada bakılabilsin diye); `False` ve `0`
    GEÇERLİ DEĞERDİR ve olduğu gibi döner.
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


def _has(raw: Any, name: str) -> bool:
    """Alan YANITTA VAR MI — değeri `false`, `0` ya da `null` olsa bile.

    "Alan yok" ile "alan var ama kapalı" ayrımı kural satırında hayatidir:
    `active: false` gelen kapalı bir kural, alan yok sanılıp `status`a
    düşülürse ekranda AÇIK görünürdü.
    """
    if not isinstance(raw, dict):
        return False
    if name in raw:
        return True
    wanted = _folded_key(name)
    return any(_folded_key(str(key)) == wanted for key in raw)


def text(value: Any) -> str:
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


#: `as_bool` için doğru/yanlış sayılan metinler. Mağaza aynı bilgiyi üç ayrı
#: biçimde veriyor: JSON `true`, metin `"1"` ve durum kelimesi `"active"`.
_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "active", "enabled", "evet"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "inactive", "draft", "disabled",
                          "hayir", "hayır"})


def as_bool(value: Any, default: bool = False) -> bool:
    """Mağazanın üç ayrı "evet" biçimini tek yerde çözer.

    TUZAK — `as_int` BURADA KULLANILAMAZ. Canlı yanıtta bu alanlar JSON
    mantıksalı (`isSubscribed: false`) ya da durum kelimesi (`status:
    "inactive"`) olarak geliyor; ikisi de `int()` ile çözülemez ve `as_int`
    varsayılanına düşer. `as_int(False, 1)` → 1: abonelikten çıkmış müşteri
    ekranda "Abone" görünürdü ve toplu gönderim listesine girerdi.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = text(value).lower()
    if raw in _TRUE_WORDS:
        return True
    if raw in _FALSE_WORDS:
        return False
    return default


def to_kurus(value: Any) -> int | None:
    """Ondalık para değerini kuruşa çevirir. Çözülemezse `None`.

    `float` KULLANILMAZ: `float("0.35") * 100` bazı değerlerde 34.999… verir ve
    `int()` bir kuruş aşağı yuvarlar. Binlerce SMS satırının toplamında bu
    sapma raporu gözle bulunamaz hâle getirir.
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


def now_hm() -> str:
    """Yerel saat `HH:MM`. UTC kullanılmaz: sessiz saatler kullanıcının
    duvar saatine göre tanımlanır, gece 23:00 UTC'de 02:00 olabilir."""
    return datetime.now(UTC).astimezone().strftime("%H:%M")


def today_iso() -> str:
    return datetime.now(UTC).astimezone().date().isoformat()


def reason_error(value: str) -> str:
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def channel_error(value: str) -> str:
    if text(value) not in CHANNELS:
        names = ", ".join(CHANNEL_LABELS[item] for item in CHANNELS)
        return f"Bilinmeyen kanal: {text(value) or '(boş)'}. Beklenen: {names}."
    return ""


def audience_error(value: str) -> str:
    """Toplu gönderim kitlesi TANINAN listede mi.

    EN TEHLİKELİ SESSİZ HATA BURADA. `AUDIENCES` sabiti bu doğrulama için
    yazılmıştı ama hiçbir yerden çağrılmıyordu: uç `audience` alanını serbest
    metin olarak alıp mağazaya geçiriyordu. Laravel TANIMADIĞI ALANI SESSİZCE
    YOK SAYAR (canlıda kanıtlandı: `?uydurma_suzgec=xyz` → 5 kayıt, hata yok);
    yani yazım hatası içeren bir kitle adı süzgeci düşürür ve iş "kime?"
    sorusu olmadan — yani HERKESE giden bir gönderim olarak — kalırdı.
    Canlıda alıcı sayısı 1.800+; geri alınamaz ve parası harcanmıştır.
    """
    if text(value) not in AUDIENCES:
        names = ", ".join(AUDIENCE_LABELS[item] for item in AUDIENCES)
        return (f"Bilinmeyen alıcı kitlesi: {text(value) or '(boş)'}. Beklenen: {names}. "
                "Tanınmayan kitle adını mağaza sessizce yok sayar ve gönderim süzgeçsiz "
                "kalırdı.")
    return ""


def mask_secret(value: Any) -> str:
    """Sırrın yalnız son 4 hanesi görünür: `••••4821`. Kısa değer tamamen kapanır."""
    raw = text(value)
    if not raw:
        return ""
    if len(raw) <= 4:
        return "•" * len(raw)
    return "•" * min(8, len(raw) - 4) + raw[-4:]


def is_secret_key(key: str) -> bool:
    lowered = text(key).lower()
    return any(hint in lowered for hint in SECRET_HINTS)


# ================================================================== şablon

def variable_palette(event: str = "") -> list[dict[str, Any]]:
    """Değişken paleti. Olay verilirse o olayda anlamlı olanlar öne alınır.

    Olay dışı değişkenler LİSTEDEN ÇIKARILMAZ, `relevant: False` ile işaretlenir:
    kullanıcı bilerek başka bir alan koyabilir, ama ekran hangisinin o olayda
    dolacağını söyler.
    """
    wanted: tuple[str, ...] | None = None
    for key, _, keys in EVENTS:
        if key == event:
            wanted = keys
            break
    out: list[dict[str, Any]] = []
    for key, label, sample in VARIABLES:
        relevant = True if wanted is None else key in wanted
        out.append({"key": key, "token": "{{" + key + "}}", "label": label,
                    "sample": sample, "relevant": relevant})
    out.sort(key=lambda item: (not item["relevant"], VARIABLE_KEYS.index(item["key"])))
    return out


def sample_values() -> dict[str, str]:
    """Önizlemenin kullandığı örnek veri — paletle aynı kaynaktan."""
    return {key: sample for key, _, sample in VARIABLES}


def render(body: str, values: dict[str, Any] | None = None) -> dict[str, Any]:
    """`{{degisken}}` yerlerini doldurur (TUZAK 1).

    Bilinmeyen ya da değeri verilmemiş değişken YERİNDE BIRAKILIR. Boşa
    çevirmek "Sayın , siparişiniz kargolandı" gibi bir metni 1.800 kişiye
    göndermek demektir; yerinde kalan `{{musteri_adi}}` ise önizlemede hemen
    görülür ve `missing` listesiyle ayrıca bildirilir.
    """
    source = values or {}
    missing: list[str] = []
    used: list[str] = []

    def swap(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in used:
            used.append(key)
        value = source.get(key)
        if value is None or text(value) == "":
            if key not in missing:
                missing.append(key)
            return match.group(0)
        return text(value)

    rendered = _PLACEHOLDER.sub(swap, body or "")
    unknown = [key for key in used if key not in VARIABLE_KEYS]
    return {"text": rendered, "missing": missing, "used": used, "unknown": unknown}


def template_problem(*, name: str, channel: str, subject: str, body: str) -> str:
    """Şablon kaydedilebilir mi — kullanıcıya gösterilecek metin ya da boş.

    KONU YALNIZ PUSH VE WHATSAPP'TA VARDIR.

    CANLIDA DOĞRULANDI (2026-08-13, `/api/admin/docs` →
    `AdminMarketingTemplate`): mağazanın e-posta şablonu kaynağı yalnız
    `name` · `status` · `content` alanlarını tanıyor, POST gövdesinde bu üçü
    ZORUNLU ve `subject` diye bir alan YOK. Yazılan konu Laravel tarafından
    sessizce yok sayılırdı — kullanıcı metnini kaybettiğini fark etmeli. Bu
    yüzden e-postada konu ZORUNLU DEĞİL, KABUL EDİLMEZDİR. SMS'te de konu
    kavramı yoktur.
    """
    if len(text(name)) < 3:
        return "Şablon adı en az 3 karakter olmalı."
    problem = channel_error(channel)
    if problem:
        return problem
    if not text(body):
        return "Şablon gövdesi boş olamaz."
    if channel == "email" and text(subject):
        return ("Mağazanın e-posta şablonunda konu alanı yoktur; konu, gövdenin kendi "
                "başlığından gelir. Konu satırını boşaltın ve metni gövdeye taşıyın.")
    if channel == "sms" and text(subject):
        return "SMS şablonunda konu alanı yoktur; konuyu boş bırakın."
    return ""


def template_id(source: str, raw_id: Any) -> str:
    """Şablon kimliği (TUZAK 6): `store:12` · `local:3`.

    Çıplak sayı iki kaynağı karıştırır ve yanlış şablonun üzerine yazar:
    mağazadaki 12 numaralı e-posta şablonu ile yereldeki 12 numaralı SMS
    şablonu aynı ekranda yan yana durur.
    """
    return f"{source}:{as_int(raw_id)}"


def split_template_id(value: str) -> tuple[str, int]:
    """`store:12` → `("store", 12)`. Tanınmayan biçim `("", 0)` döner."""
    raw = text(value)
    source, _, number = raw.partition(":")
    if source not in ("store", "local") or not number.isdigit():
        return "", 0
    return source, int(number)


#: Mağazanın e-posta şablonu durumu. CANLI ŞEMADA DOĞRULANDI (2026-08-13,
#: `/api/admin/docs` → `AdminMarketingTemplate`): alan bir sayı değil, üç
#: değerli bir KELİMEDİR (`enum: active|inactive|draft`) ve POST'ta zorunludur.
#: `1`/`0` göndermek 422 döndürür, `as_int` ile okumak her şablonu "aktif"
#: gösterirdi.
STORE_TEMPLATE_STATUSES = ("active", "inactive", "draft")


def store_template_status(active: bool) -> str:
    """Ekranın mantıksal `active` değerini mağazanın kelime durumuna çevirir.

    Mağazaya giden HER şablon yazması bu fonksiyondan geçmelidir; `1`/`0`
    göndermek uçtan 422 döndürür ve kullanıcı şablonun kaydedildiğini sanır.
    """
    return "active" if active else "inactive"


def store_template_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Mağazanın e-posta şablonu satırı → ekranın beklediği biçim.

    CANLI ALAN ADLARI (doğrulandı): `{id, name, status, content, createdAt,
    updatedAt}`. Zarf camelCase'tir ve KONU ALANI YOKTUR — Bagisto'nun şablon
    kaynağında `subject` diye bir alan bulunmuyor, konu içeriğin kendisindedir.
    Boş bir "Konu" satırı göstermek, kullanıcıya doldurabileceği bir alan varmış
    gibi gösterirdi; `subject` bu yüzden her zaman boştur (bkz. `template_problem`).
    """
    body = text(pick(raw, "content", "body", "html"))
    status = pick(raw, "status")
    return {
        "id": template_id("store", raw.get("id")),
        "source": "store",
        "name": text(pick(raw, "name")) or "(adsız)",
        "channel": "email",
        "channelLabel": CHANNEL_LABELS["email"],
        "subject": "",
        "body": body,
        "event": "",
        "active": as_bool(status, True),
        "status": text(status),
        "updatedAt": text(pick(raw, "updated_at"))[:19],
        "editable": True,
    }


def local_template_row(row: dict[str, Any]) -> dict[str, Any]:
    channel = text(row.get("channel")) or "sms"
    return {
        "id": template_id("local", row.get("id")),
        "source": "local",
        "name": text(row.get("name")) or "(adsız)",
        "channel": channel,
        "channelLabel": CHANNEL_LABELS.get(channel, channel),
        "subject": text(row.get("subject")),
        "body": text(row.get("body")),
        "event": text(row.get("event")),
        "active": as_bool(row.get("active"), True),
        "status": "",
        "updatedAt": text(row.get("updated_at"))[:19],
        "editable": True,
    }


# ==================================================================== SMS

def sms_plan(body: str, *, recipients: int = 1, price_kurus: int = 0) -> dict[str, Any]:
    """Karakter / parça / kredi sayacı (TUZAK 2, 7).

    `plan_text` septet hesabını yapar; `offending` metni pahalılaştıran
    karakterleri, `simplify` ASCII karşılığını verir. Metin OTOMATİK
    SADELEŞTİRİLMEZ — öneri sunulur, kararı kullanıcı verir.
    """
    source = body or ""
    plan = plan_text(source)
    plain = simplify(source)
    plain_plan = plan_text(plain)
    people = max(0, as_int(recipients))
    credits = plan.parts * people
    unit = max(0, as_int(price_kurus))
    return {
        "chars": len(source),
        "units": plan.units,
        "capacity": plan.capacity,
        "remaining": plan.remaining,
        "parts": plan.parts,
        "encoding": plan.encoding or "",
        "unicode": plan.unicode,
        "offending": offending(source),
        "recipients": people,
        "credits": credits,
        # Birim fiyat girilmemişse maliyet UYDURULMAZ: `None` → ekran "—" yazar.
        "cost": credits * unit if unit else None,
        "unitPrice": unit or None,
        "simplified": plain if plain != source else "",
        "simplifiedParts": plain_plan.parts,
        "savedCredits": max(0, plan.parts - plain_plan.parts) * people,
    }


# ========================================================== sessiz saatler

def valid_hm(value: str) -> bool:
    return bool(_HM.match(text(value)))


def quiet_state(current: str, *, start: str, end: str,
                enabled: bool = True) -> dict[str, Any]:
    """Şu an sessiz saat içinde miyiz (TUZAK 4, 5).

    Gece yarısını aşan pencere (22:00–08:00) düz `start <= now < end`
    karşılaştırmasıyla HİÇBİR ZAMAN doğru olmaz; sarma ayrıca ele alınır.
    """
    window = f"{text(start)}–{text(end)}"
    if not enabled:
        return {"active": False, "window": window, "error": "", "message": ""}
    if not valid_hm(start) or not valid_hm(end):
        return {"active": False, "window": window,
                "error": "Sessiz saat biçimi SS:DD olmalı; pencere uygulanmadı.",
                "message": ""}
    if text(start) == text(end):
        # Başlangıç = bitiş, 24 saatlik pencere demek olurdu ve tek bir
        # bildirim bile çıkmazdı. Sessizce uygulamak yerine söylenir.
        return {"active": False, "window": window,
                "error": "Sessiz saat başlangıcı ile bitişi aynı; pencere uygulanmadı.",
                "message": ""}

    started, ended, moment = text(start), text(end), text(current)
    inside = (started <= moment < ended) if started < ended else \
        (moment >= started or moment < ended)
    return {
        "active": inside,
        "window": window,
        "error": "",
        "message": (f"Sessiz saatler açık ({window}); toplu gönderim yapılmaz."
                    if inside else ""),
    }


def limit_state(sent_today: int, limit: int, *, wanted: int = 0) -> dict[str, Any]:
    """Günlük gönderim limiti. `limit <= 0` → sınır yok."""
    cap = max(0, as_int(limit))
    used = max(0, as_int(sent_today))
    if cap <= 0:
        return {"limited": False, "remaining": None, "used": used, "limit": 0, "error": ""}
    remaining = max(0, cap - used)
    error = ""
    if wanted > remaining:
        error = (f"Günlük gönderim limiti {cap}; bugün {used} gönderildi, "
                 f"kalan {remaining}. Bu iş {wanted} alıcıya gidecek.")
    return {"limited": True, "remaining": remaining, "used": used, "limit": cap,
            "error": error}


# ================================================================= kurallar

def rule_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Mağazanın kural satırı → tablo satırı.

    CANLI ALAN ADLARI (2026-08-16, `GET /api/admin/bbd/notifications/rules`):
    `{id, title, description, trigger, event, schedule, channel, audience,
    handler, enabled, blockedBy}`. Bu satır bir dönem yalnız VARSAYILAN bir
    şema üzerine yazılmıştı (uç o zaman yayında değildi); uç yayına girince üç
    yer birden yanlış çizmeye başlamıştı ve üçü de burada düzeltildi:

    · **Kimlik SAYI DEĞİL.** Canlı kimlik `order.created.telegram` gibi bir
      dizedir — dinleyicinin adıdır, tablo satırı numarası değil. `as_int`
      hepsini `0` yapıyordu: beş ayrı kural ekranda aynı kimliği taşırdı.
    · **Açıklık alanı `enabled`.** `active` de `status` da canlı yanıtta YOK;
      ikisine bakan kod varsayılana düşüp `enabled: true` olan beş kuralı da
      "Kapalı" gösteriyordu — yani ekran, gerçekten çalışan bildirimleri
      kapalı sanıyordu. Sıra `active` → `enabled` → `status`: mağaza hangisini
      gönderirse o okunur.
    · **Olay adı mağazanın kendi olayı.** Canlıda `checkout.order.save.after`
      geliyor; bu ad `EVENTS` listesinde yok ve her satır "tanınmayan olay"
      rozeti alıyordu. Mağaza kuralı kendi `title` alanıyla ZATEN anlatıyor;
      rozet artık yalnız hiçbir tarafın adlandırmadığı olay için çıkar.
    """
    event = text(raw.get("event"))
    channel = text(raw.get("channel")) or "email"
    title = text(pick(raw, "title"))
    return {
        # Kimlik OLDUĞU GİBİ taşınır (dize ya da sayı): mağaza ne verdiyse o.
        "id": text(raw.get("id")),
        "event": event,
        "eventLabel": EVENT_LABELS.get(event) or title or event or "(tanımsız)",
        "known": event in EVENT_KEYS or bool(title),
        "description": text(pick(raw, "description")),
        "channel": channel,
        "channelLabel": CHANNEL_LABELS.get(channel, channel),
        "templateId": text(pick(raw, "template_id")),
        "templateName": text(pick(raw, "template_name")),
        "condition": text(pick(raw, "condition")),
        "delayMinutes": as_int(pick(raw, "delay_minutes")),
        # `or` ZİNCİRİ KULLANILMAZ: `active: false` yanlış olduğu için sıradaki
        # alana düşer ve kapalı kural açık görünürdü. `pick` de `False`ı geçerli
        # değer sayar; yalnız alan HİÇ YOKSA sıradakine bakılır.
        "active": as_bool(
            pick(raw, "active") if _has(raw, "active")
            else pick(raw, "enabled") if _has(raw, "enabled")
            else pick(raw, "status"),
            False),
        "lastFiredAt": text(pick(raw, "last_fired_at"))[:19],
        "firedCount": as_int(pick(raw, "fired_count")),
    }


def rule_problem(*, event: str, channel: str, template: str,
                 delay_minutes: int) -> str:
    if text(event) not in EVENT_KEYS:
        return f"Bilinmeyen olay: {text(event) or '(boş)'}."
    problem = channel_error(channel)
    if problem:
        return problem
    source, number = split_template_id(template)
    if not source or not number:
        return "Kural bir şablona bağlanmalı."
    if not 0 <= as_int(delay_minutes) <= 10_080:
        return "Gecikme 0 ile 10.080 dakika (7 gün) arasında olmalı."
    return ""


# ================================================================== geçmiş

def history_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Mağazanın gönderim kaydı → tablo satırı. Para KURUŞ."""
    channel = text(pick(raw, "channel")) or "email"
    status = text(pick(raw, "status")).lower() or "queued"
    subject = text(pick(raw, "subject"))
    body = text(pick(raw, "body", "content"))
    event = text(pick(raw, "event"))
    return {
        "id": as_int(raw.get("id")),
        "createdAt": text(pick(raw, "created_at"))[:19],
        "channel": channel,
        "channelLabel": CHANNEL_LABELS.get(channel, channel),
        "template": text(pick(raw, "template_name", "template")),
        "recipient": text(pick(raw, "recipient", "to")),
        "summary": subject or body[:80],
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "failed": status in BAD_STATUSES,
        "attempts": as_int(pick(raw, "attempts", "attempt"), 1),
        "parts": as_int(pick(raw, "parts")),
        "cost": to_kurus(pick(raw, "cost")),
        "event": event,
        "eventLabel": EVENT_LABELS.get(event, event),
        "orderNo": text(pick(raw, "order_no")),
        "error": text(pick(raw, "error", "error_message")),
    }


def history_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """KPI şeridi ve rapor özeti. Maliyeti bilinmeyen satır TOPLAMA GİRMEZ."""
    known = [row["cost"] for row in rows if row.get("cost") is not None]
    return {
        "total": len(rows),
        "delivered": len([row for row in rows if row["status"] in ("delivered", "opened")]),
        "failed": len([row for row in rows if row["failed"]]),
        "queued": len([row for row in rows if row["status"] == "queued"]),
        "parts": sum(as_int(row.get("parts")) for row in rows),
        "cost": sum(known) if known else None,
        "costUnknown": len(rows) - len(known),
    }


def cost_by_key(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    """SMS maliyet icmali için kırılım. Maliyeti olmayan satır sayılır ama
    tutara girmez; rapor "12 satırın maliyeti bilinmiyor" der."""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = text(row.get(key)) or "(belirsiz)"
        bucket = buckets.setdefault(name, {"key": name, "count": 0, "parts": 0,
                                           "cost": 0, "unknown": 0})
        bucket["count"] += 1
        bucket["parts"] += as_int(row.get("parts"))
        if row.get("cost") is None:
            bucket["unknown"] += 1
        else:
            bucket["cost"] += int(row["cost"])
    return sorted(buckets.values(), key=lambda item: (-item["cost"], -item["count"]))


# ================================================================ abonelik

def subscriber_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Bülten abonesi satırı.

    CANLI ALAN ADLARI (2026-08-13, `/api/admin/marketing/subscribers`, 5 kayıt):
    `{id, email, isSubscribed, customerId, customerName, channel, createdAt}` —
    zarf **camelCase**'tir ve `isSubscribed` bir JSON MANTIKSALIDIR (`true` /
    `false`), sayı değil.

    İKİ TUZAK BİR ARADA. `is_subscribed` diye okuyan kod hiçbir şey bulmaz ve
    istisna da atmaz; üstüne `as_int(None, 1)` varsayılana düşüp 1 verirdi.
    Sonuç: abonelikten ÇIKMIŞ müşteri ekranda "Abone" görünür ve toplu
    gönderim listesine girerdi. Ad `pick` ile iki yazımda da aranır, değer
    `as_bool` ile çözülür.
    """
    return {
        "id": as_int(raw.get("id")),
        "email": text(pick(raw, "email")),
        "customerId": as_int(pick(raw, "customer_id")),
        "customerName": text(pick(raw, "customer_name", "name")),
        "subscribed": as_bool(pick(raw, "is_subscribed", "subscribed"), True),
        "channel": text(pick(raw, "channel_name", "channel")),
        "createdAt": text(pick(raw, "created_at"))[:19],
    }


# ==================================================================== ayar

def config_entry(values: Any, key: str) -> dict[str, Any]:
    """Mağaza ayarının MEVCUT değeri; bulunamazsa "yok" denir, sıfır uydurulmaz.

    Anahtar adı Bagisto sürümüne göre değişebiliyor. Bulunmayan anahtara
    yazmak `core_config` içinde hiçbir şeyi etkilemeyen yeni bir satır açar ve
    kullanıcı ayarı değiştirdiğini sanır.
    """
    if not isinstance(values, dict):
        return {"key": key, "found": False, "value": None, "masked": ""}
    found_key, found_value, found = key, None, False
    if key in values:
        found_key, found_value, found = key, values[key], True
    else:
        tail = key.rsplit(".", 1)[-1]
        for name, value in values.items():
            if str(name).rsplit(".", 1)[-1] == tail:
                found_key, found_value, found = str(name), value, True
                break
    secret = is_secret_key(found_key)
    return {
        "key": found_key,
        "found": found,
        # Sır ham hâliyle yanıtta DURMAZ: maskelenmiş kopya gider (K8).
        "value": None if secret else found_value,
        "masked": mask_secret(found_value) if secret else "",
        "secret": secret,
    }


def mobile_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Mobil uygulama ayarları — `MobileAppSettings` karşılığı.

    TUZAK — MANTIKSAL ALANDA `as_int` KULLANILMAZ. Uç JSON mantıksalı
    döndürüyor ve `as_int(True, 0)` → `int("True")` patlar, sessizce
    varsayılana (0) düşer: PUSH BİLDİRİMLERİ AÇIKKEN ekran "kapalı" gösterir
    ve personel açık olan bir anahtarı bir daha açmaya çalışır. Mantıksal
    alanların hepsi `as_bool`dan geçer.
    """
    return {
        "pushEnabled": as_bool(pick(raw, "push_enabled"), False),
        "androidVersion": text(pick(raw, "android_version")),
        "iosVersion": text(pick(raw, "ios_version")),
        "minSupported": text(pick(raw, "min_supported")),
        "forceUpdate": as_bool(pick(raw, "force_update"), False),
        "maintenance": as_bool(pick(raw, "maintenance"), False),
        "maintenanceText": text(pick(raw, "maintenance_text")),
        "pushProvider": text(pick(raw, "push_provider")),
        "pushKeyMasked": mask_secret(pick(raw, "push_key")),
    }
