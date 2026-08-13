"""Deneme Kulübü verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz.

NEDEN AYRI DOSYA. Bu ekranın zor kısmı listeleme değil, **sonuç yüklemesi**:
kurumdan gelen CSV'de kimlik sütunu her seferinde başka adla gelir (`Ad Soyad`
· `Adı Soyadı` · `İsim`), ondalıklar Türkçe yazılır (`12,5`), ayraç bazen `;`
bazen `,` olur ve satırlar katılımcı kayıtlarıyla eşleştirilmek zorundadır.
Bu mantık servise gömülseydi tek satırı bile ağsız test edilemezdi.

YEDİ TUZAK — her birinin karşılığı bu dosyada bir fonksiyondur:

 1. CSV ayracı belirsiz        → `detect_delimiter` başlık satırından karar verir.
 2. Türkçe ondalık (`12,5`)    → `parse_number` virgülü ondalık okur.
 3. Sütun adı standart değil   → `map_columns` eşanlamlı listesiyle çözer.
 4. Aynı kişi iki satırda      → `match_results` ikincisini `duplicate` işaretler.
 5. Ad benzerliği yanıltır     → birden çok eşleşme `ambiguous`, sessizce ilki
                                 seçilmez.
 6. Kontenjan kayıtlının altına→ `capacity_change_error` reddeder.
 7. Sıra (rank) gelmeyebilir   → `derive_ranks` puandan üretir, beraberlik
                                 aynı sırayı alır.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (`store_api`) de 10 istiyor; burada
#: TEKRAR doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Kontenjan çubuğunun "dolmak üzere" sayıldığı yüzde.
NEAR_FULL = 90

#: Sonuç yayınlandıktan sonra katılımcı kendi karnesini görüyor; ekran bunu
#: SÖYLER, "yayınla" düğmesini sessiz bir kaydetme gibi göstermez.
PUBLISH_NOTICE = ("Yayınlanan sonuç katılımcıya görünür olur ve mobil uygulamada "
                  "bildirim üretir. Geri alma tek tuşla değildir.")

PAYMENT_LABELS = {
    "free": "Ücretsiz",
    "paid": "Ödendi",
    "pending": "Bekliyor",
    "failed": "Başarısız",
    "refunded": "İade edildi",
}

ATTEND_LABELS = {
    "attended": "Katıldı",
    "absent": "Katılmadı",
    "unknown": "Bilinmiyor",
}

EXAM_STATUS_LABELS = {
    "draft": "Taslak",
    "open": "Kayıt açık",
    "ongoing": "Devam ediyor",
    "done": "Tamamlandı",
    "cancelled": "İptal",
}

#: Toplu bildirimin kime gideceği. Sunucuya İSİM değil ÇÖZÜLMÜŞ ALICI LİSTESİ
#: gönderilir; önizlenen kitle ile gönderilen kitle aynı olsun diye.
AUDIENCES = {
    "all": "Tüm katılımcılar",
    "unpaid": "Ödemesi bekleyenler",
    "attended": "Sınava katılanlar",
    "absent": "Sınava katılmayanlar",
}

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})

#: Başlık eşanlamlıları — hepsi `fold` geçmiş biçimde yazılır.
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("kayit no", "uye no", "ogrenci no", "numara", "no", "kod", "code", "uye kodu"),
    "name": ("ad soyad", "adi soyadi", "isim", "ad", "adsoyad", "name", "katilimci",
             "ogrenci", "ogrenci adi"),
    "email": ("e posta", "eposta", "email", "mail", "e mail"),
    "phone": ("telefon", "gsm", "cep", "cep telefonu", "phone", "tel"),
    "net": ("net", "toplam net"),
    "score": ("puan", "skor", "score", "toplam puan"),
    "rank": ("sira", "derece", "rank", "genel sira"),
    "correct": ("dogru", "d", "dogru sayisi"),
    "wrong": ("yanlis", "y", "yanlis sayisi"),
    "blank": ("bos", "b", "bos sayisi"),
    "attended": ("katildi", "katilim", "devam", "katilim durumu"),
}

#: Kimlik taşıyan sütunlar — en az biri olmadan eşleştirme yapılamaz.
IDENTITY_COLUMNS = ("code", "email", "phone", "name")

#: Sonuç taşıyan sütunlar — en az biri olmadan yükleyecek bir şey yok.
RESULT_COLUMNS = ("net", "score", "rank", "correct", "wrong", "blank", "attended")

#: `fold` noktalama işaretlerini attığı için burada yalnız harf/rakam durur.
_TRUE_WORDS = {"1", "e", "evet", "var", "x", "katildi", "true", "geldi"}
_FALSE_WORDS = {"0", "h", "hayir", "yok", "katilmadi", "false", "gelmedi"}


# ===================================================================== temel

def today_iso() -> str:
    """Bugünün YEREL takvim günü.

    `datetime.utcnow().date()` kullanılmaz: kayıt penceresi ve sınav günü
    mağazanın takvim gününe göre açılıp kapanır; gece yarısından sonraki üç
    saatte UTC bir gün geride kalır ve kaydı kapanmış deneme ekranda hâlâ
    açık görünürdü.
    """
    return datetime.now(UTC).astimezone().date().isoformat()


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


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return fold(value) in _TRUE_WORDS


def fold(value: Any) -> str:
    """Türkçe duyarsız karşılaştırma anahtarı: `Ayşe ÖZ` → `ayse oz`.

    Türkçe harfler ÖNCE eşlenir; `unicodedata` ile normalleştirmek `ı`yı boşa
    çıkarır ve `Işıl` → `sl` gibi anlamsız anahtarlar üretir.
    """
    folded = text(value).translate(_TR_MAP)
    folded = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode()
    folded = re.sub(r"[^a-zA-Z0-9]+", " ", folded).strip().lower()
    return re.sub(r"\s{2,}", " ", folded)


def phone_key(value: Any) -> str:
    """Telefonu 10 haneye indirger: `0532 123 45 67` → `5321234567`.

    Eşleştirmede kullanılır; biçimi bozuk numara boş anahtar döner ve o satır
    telefondan EŞLEŞMEZ — yanlış kişiye sonuç yazmaktansa eşleşmemek doğrudur.
    """
    digits = re.sub(r"\D", "", text(value))
    if len(digits) == 12 and digits.startswith("90"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 and digits.startswith("5") else ""


def mask_phone(value: Any) -> str:
    """`5321234567` → `0532 *** ** 67`. Ekranda tam numara gerekli değilse bu."""
    digits = phone_key(value)
    if not digits:
        return text(value)
    return f"0{digits[:3]} *** ** {digits[8:]}"


def to_kurus(value: Any) -> int | None:
    """Ondalık ücreti kuruşa çevirir. Çözülemezse `None` — sıfır DEĞİL.

    `float("120.35") * 100` bazı değerlerde 12034.999… verir ve `int()` bir
    kuruş aşağı yuvarlar; Decimal ile HALF_UP yuvarlanır.
    """
    if value is None:
        return None
    raw = text(value).replace(" ", "")
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
    """Kuruş → telde gidecek ondalık metin: 12035 → `120.35`."""
    return f"{Decimal(int(kurus or 0)) / 100:.2f}"


def reason_error(value: str) -> str:
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def field(raw: Any, *names: str) -> Any:
    """Alanı snake_case ya da camelCase — hangisi geldiyse ondan okur.

    BBD uçları yazılırken alan adları henüz sabitlenmedi; ekranın tek bir
    yazıma bağlanması, uç yayınlandığında sessizce boş sütun göstermek olurdu.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
        camel = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), name)
        if camel in raw and raw[camel] not in (None, ""):
            return raw[camel]
    for name in names:                      # "yok" ile "boş" ayrımı korunur
        if name in raw:
            return raw[name]
    return None


# ================================================================= kontenjan

def capacity_view(enrolled: Any, capacity: Any, *, near: int = NEAR_FULL) -> dict[str, Any]:
    """Kontenjan çubuğunun beslendiği tek yer.

    Kontenjan 0/boş ise SINIRSIZ demektir; yüzde hesaplamak sıfıra bölme ya da
    "%0 dolu" yalanı üretirdi.
    """
    taken = max(0, as_int(enrolled))
    total = max(0, as_int(capacity))
    if total <= 0:
        return {"enrolled": taken, "capacity": 0, "remaining": None, "percent": None,
                "state": "unlimited", "label": f"{taken} kayıt · kontenjan sınırsız"}

    percent = round(taken * 100 / total, 1)
    remaining = total - taken
    if taken > total:
        state = "over"
    elif taken == total:
        state = "full"
    elif percent >= near:
        state = "near"
    elif taken == 0:
        state = "empty"
    else:
        state = "filling"
    return {"enrolled": taken, "capacity": total, "remaining": remaining,
            "percent": percent, "state": state, "label": f"{taken}/{total}"}


def capacity_change_error(capacity: Any, enrolled: Any) -> str:
    """Kontenjan kayıtlı sayısının ALTINA çekilemez.

    Bagisto tarafı bunu kabul ederdi ve fazla kayıtlar sistemde asılı kalırdı:
    kimin sınava gireceği belirsizleşir, yoklama çizelgesi kontenjandan uzun
    çıkardı. Kayıt silmek bu ekranın işi değil (silme yok, ADR 0012).
    """
    wanted = as_int(capacity, -1)
    taken = max(0, as_int(enrolled))
    if wanted < 0:
        return "Kontenjan negatif olamaz."
    if wanted == 0:
        return ""                            # 0 = sınırsız, bilinçli seçim
    if wanted < taken:
        return (f"Kontenjan {wanted} yapılamaz: bu denemede {taken} kayıtlı katılımcı var. "
                "Önce katılımcı çıkarılmalı; kontenjanı kayıtlının altına çekmek "
                "kimin gireceğini belirsiz bırakır.")
    return ""


# =============================================================== liste satırı

def exam_row(raw: dict[str, Any], *, today: str = "", near: int = NEAR_FULL) -> dict[str, Any]:
    """Ham deneme kaydı → tablonun beklediği satır."""
    day = today or today_iso()
    exam_date = text(field(raw, "exam_date", "date", "starts_at"))[:10]
    exam_time = text(field(raw, "exam_time", "time"))[:5]
    start = text(field(raw, "registration_start", "register_start"))[:10]
    end = text(field(raw, "registration_end", "register_end"))[:10]
    capacity = capacity_view(field(raw, "enrolled", "registered_count", "member_count"),
                             field(raw, "capacity", "quota"), near=near)

    status = fold(field(raw, "status", "state"))
    if status not in EXAM_STATUS_LABELS:
        status = "cancelled" if as_bool(field(raw, "cancelled")) else ""
    published = as_bool(field(raw, "results_published", "published"))

    if start and day < start:
        registration = "not_started"
    elif end and day > end:
        registration = "closed"
    elif capacity["state"] in ("full", "over"):
        registration = "full"
    else:
        registration = "open"

    if exam_date and day > exam_date:
        results = "published" if published else "pending"
    else:
        results = "published" if published else "none"

    effective = status or ("done" if results == "published" else "open")

    return {
        "id": as_int(raw.get("id")),
        "name": text(field(raw, "name", "title")) or "(adsız deneme)",
        "examDate": exam_date,
        "examTime": exam_time,
        "venue": text(field(raw, "venue", "location", "place")),
        "grade": text(field(raw, "grade", "level", "class")),
        "feeKurus": to_kurus(field(raw, "fee", "price")),
        "registrationStart": start,
        "registrationEnd": end,
        "registrationState": registration,
        "status": effective,
        "statusLabel": EXAM_STATUS_LABELS.get(effective, "—"),
        "capacity": capacity,
        "resultsState": results,
        "resultsPublished": published,
        # `raw.get("created_at")` YAZILMAZ: canlı mağaza alan adlarını camelCase
        # veriyor (`createdAt`, doğrulandı 2026-08-13 — /api/admin/orders).
        # Düz `get` istisna atmaz, sessizce boş döner ve ekranda "—" görünürdü;
        # bu dosyadaki en sinsi hata biçimi budur. `field` iki yazımı da okur.
        "createdAt": text(field(raw, "created_at"))[:19],
    }


def member_exam_id(raw: dict[str, Any]) -> int:
    """Katılımcının bağlı olduğu denemenin kimliği; çözülemezse 0.

    TUZAK: kimlik düz alanda (`exam_id`) gelebileceği gibi iç içe bir nesnede
    (`exam: {id: 5}`) de gelebilir — BBD ucu yazılırken biçim sabitlenmedi.
    Tek yazıma bağlanmak burada 0 döndürürdü ve 0, "bu satır hangi denemenin
    olduğunu söylemiyor" anlamına geldiği için `_all_members` süzgecin
    uygulandığını kanıtlayamaz ve işlemi durdurur.
    """
    direct = as_int(field(raw, "exam_id", "trial_exam_id"))
    if direct:
        return direct
    for key in ("exam", "trial_exam", "trialExam"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            value = as_int(nested.get("id"))
            if value:
                return value
    return 0


def member_exam_name(raw: dict[str, Any]) -> str:
    """Denemenin adı; iç içe nesneden de okunur (bkz. `member_exam_id`)."""
    direct = field(raw, "exam_name", "trial_exam_name")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for key in ("exam", "trial_exam", "trialExam"):
        nested = raw.get(key)
        if isinstance(nested, dict):
            value = text(field(nested, "name", "title"))
            if value:
                return value
        elif isinstance(nested, str) and nested.strip():
            return nested.strip()
    return ""


def member_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Ham katılımcı kaydı → tablo satırı."""
    payment = fold(field(raw, "payment_status", "payment", "paymentState"))
    if payment not in PAYMENT_LABELS:
        payment = "free" if to_kurus(field(raw, "paid_amount", "amount")) in (0, None) \
            else "paid"

    attended = field(raw, "attended", "attendance")
    if attended is None or text(attended) == "":
        attendance = "unknown"
    else:
        attendance = "attended" if as_bool(attended) else "absent"

    phone = text(field(raw, "phone", "mobile", "gsm"))
    return {
        "id": as_int(raw.get("id")),
        "code": text(field(raw, "code", "member_no", "registration_no")),
        "name": text(field(raw, "name", "full_name", "customer_name")) or "(adsız)",
        "email": text(field(raw, "email")).lower(),
        "phone": phone,
        "phoneMasked": mask_phone(phone),
        "customerId": as_int(field(raw, "customer_id")),
        "examId": member_exam_id(raw),
        "examName": member_exam_name(raw),
        "grade": text(field(raw, "grade", "level", "class")),
        "city": text(field(raw, "city", "state")),
        "district": text(field(raw, "district", "county")),
        "registeredAt": text(field(raw, "created_at", "registered_at"))[:19],
        "payment": payment,
        "paymentLabel": PAYMENT_LABELS.get(payment, "—"),
        "paidKurus": to_kurus(field(raw, "paid_amount", "amount")),
        "attendance": attendance,
        "attendanceLabel": ATTEND_LABELS[attendance],
        "net": parse_number(field(raw, "net")),
        "score": parse_number(field(raw, "score", "point")),
        "rank": as_int(field(raw, "rank", "position")) or None,
    }


def result_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Sonuç kaydı → tablo satırı. Katılımcı satırıyla aynı alan adlarını
    kullanır ki karne üreteci ikisini de aynı işlevle basabilsin."""
    row = member_row(raw)
    row["correct"] = parse_number(field(raw, "correct", "true_count"))
    row["wrong"] = parse_number(field(raw, "wrong", "false_count"))
    row["blank"] = parse_number(field(raw, "blank", "empty_count"))
    row["memberId"] = as_int(field(raw, "member_id")) or row["id"]
    return row


# ====================================================================== CSV

def detect_delimiter(header: str) -> str:
    """Ayracı başlık satırından seçer.

    TUZAK: Türkçe ondalık virgüldür. `;` varsa ayraç odur ve virgüller
    ondalıktır — bu en yaygın (Excel TR) durumdur. `;` hiç yoksa sekme, o da
    yoksa virgül denenir; virgüllü dosyada `12,5` ikiye bölünür ve bunu
    sessizce yapmamak için çağıran uyarı gösterir.
    """
    line = header or ""
    counts = {";": line.count(";"), "\t": line.count("\t"), ",": line.count(",")}
    best = max(counts, key=lambda key: counts[key])
    return best if counts[best] else ";"


def map_columns(header: list[str]) -> dict[str, int]:
    """Başlık hücrelerini mantıksal sütunlara eşler. İlk eşleşme kazanır."""
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        key = fold(cell)
        if not key:
            continue
        for logical, aliases in HEADER_ALIASES.items():
            if logical in found:
                continue
            if key in aliases:
                found[logical] = index
                break
    return found


def parse_number(value: Any) -> float | None:
    """`12,5` → 12.5 · `` → None · `abc` → None.

    Sıfır ile "girilmemiş" farklı şeylerdir: birincisi sıfır net, ikincisi
    kâğıdı okunmamış öğrenci. Bu yüzden çözülemeyen değer 0 değil None döner.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = text(value).replace(" ", "")
    if raw == "":
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "") if raw.rfind(".") > raw.rfind(",") else \
            raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def parse_attended(value: Any) -> bool | None:
    key = fold(value)
    if key in _TRUE_WORDS:
        return True
    if key in _FALSE_WORDS:
        return False
    return None


def parse_result_csv(content: str, *, max_rows: int = 2000) -> dict[str, Any]:
    """Sonuç CSV'sini satır sözlüklerine çevirir. AĞA ÇIKMAZ, EŞLEŞTİRMEZ.

    Dönüş: `{ok, error, delimiter, columns, rows, warnings, truncated}`.
    Her satır kendi `line` numarasını taşır: eşleştirme önizlemesinde kullanıcı
    "hangi satır" sorusunu dosyaya bakarak yanıtlayabilsin.
    """
    raw = (content or "").lstrip("﻿")
    if not raw.strip():
        return {"ok": False, "error": "Dosya boş.", "rows": [], "columns": {},
                "warnings": [], "delimiter": ";", "truncated": False}

    first = next((line for line in raw.splitlines() if line.strip()), "")
    delimiter = detect_delimiter(first)
    records = [row for row in csv.reader(io.StringIO(raw), delimiter=delimiter)
               if any(text(cell) for cell in row)]
    if len(records) < 2:
        return {"ok": False, "error": "Dosyada başlık satırından başka satır yok.",
                "rows": [], "columns": {}, "warnings": [], "delimiter": delimiter,
                "truncated": False}

    columns = map_columns(records[0])
    warnings: list[str] = []
    if delimiter == ",":
        warnings.append("Ayraç virgül olarak okundu; Türkçe ondalıklı sayılar (12,5) "
                        "bölünmüş olabilir. Dosyayı noktalı virgülle kaydetmek daha güvenli.")

    identity = [name for name in IDENTITY_COLUMNS if name in columns]
    if not identity:
        return {"ok": False, "columns": columns, "rows": [], "warnings": warnings,
                "delimiter": delimiter, "truncated": False,
                "error": "Kimlik sütunu bulunamadı. Dosyada `Ad Soyad`, `E-posta`, "
                         "`Telefon` ya da `Kayıt No` başlıklarından biri olmalı."}
    if not any(name in columns for name in RESULT_COLUMNS):
        return {"ok": False, "columns": columns, "rows": [], "warnings": warnings,
                "delimiter": delimiter, "truncated": False,
                "error": "Sonuç sütunu bulunamadı. `Net`, `Puan`, `Sıra`, `Doğru`, "
                         "`Yanlış` ya da `Katıldı` başlıklarından biri gerekir."}

    rows: list[dict[str, Any]] = []
    truncated = False
    for line_no, record in enumerate(records[1:], start=2):
        if len(rows) >= max(1, int(max_rows)):
            truncated = True
            break

        def cell(name: str, record: list[str] = record) -> str:
            index = columns.get(name)
            if index is None or index >= len(record):
                return ""
            return text(record[index])

        row: dict[str, Any] = {
            "line": line_no,
            "code": cell("code"),
            "name": cell("name"),
            "email": cell("email").lower(),
            "phone": cell("phone"),
            "net": parse_number(cell("net")),
            "score": parse_number(cell("score")),
            "rank": as_int(cell("rank")) or None,
            "correct": parse_number(cell("correct")),
            "wrong": parse_number(cell("wrong")),
            "blank": parse_number(cell("blank")),
            "attended": parse_attended(cell("attended")) if "attended" in columns else None,
        }
        row["hasIdentity"] = any(row[name] for name in IDENTITY_COLUMNS)
        row["hasResult"] = any(row[name] is not None for name in RESULT_COLUMNS)
        rows.append(row)

    if truncated:
        warnings.append(f"Dosya {max_rows} satırda kesildi; kalan satırlar okunmadı.")

    return {"ok": True, "error": "", "delimiter": delimiter, "columns": columns,
            "rows": rows, "warnings": warnings, "truncated": truncated}


# ============================================================== eşleştirme

def _index_members(members: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_code: dict[str, dict[str, Any]] = {}
    by_email: dict[str, dict[str, Any]] = {}
    by_phone: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        code = fold(member.get("code"))
        if code and code not in by_code:
            by_code[code] = member
        email = text(member.get("email")).lower()
        if email and email not in by_email:
            by_email[email] = member
        phone = phone_key(member.get("phone"))
        if phone and phone not in by_phone:
            by_phone[phone] = member
        name = fold(member.get("name"))
        if name:
            by_name.setdefault(name, []).append(member)
    return {"code": by_code, "email": by_email, "phone": by_phone, "name": by_name}


def match_results(rows: list[dict[str, Any]],
                  members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CSV satırlarını katılımcılarla eşler — ÖNİZLEME BUNUN ÜZERİNE KURULUR.

    Sıra: kayıt no → e-posta → telefon → ad. Ad en zayıf ölçüttür ve YALNIZ
    diğerleri sonuç vermezse kullanılır; aynı ada sahip iki katılımcı varsa
    satır `ambiguous` olur ve **hiçbiri seçilmez**. Sessizce ilkini seçmek,
    kardeşlerin sonucunu birbirine yazmak demekti.

    Aynı katılımcıya ikinci kez denk gelen satır `duplicate`: kurumdan gelen
    dosyada mükerrer kayıt olağandır ve ikincisi birincinin üstüne yazarsa
    hangi netin geçerli olduğu belirsiz kalır.
    """
    index = _index_members(members)
    used: dict[int, int] = {}
    out: list[dict[str, Any]] = []

    for row in rows:
        found: dict[str, Any] | None = None
        how = ""
        state = "unmatched"
        note = ""

        if not row.get("hasIdentity"):
            note = "Satırda kimlik alanı yok."
        else:
            code = fold(row.get("code"))
            email = text(row.get("email")).lower()
            phone = phone_key(row.get("phone"))
            name = fold(row.get("name"))
            if code and code in index["code"]:
                found, how = index["code"][code], "kayıt no"
            elif email and email in index["email"]:
                found, how = index["email"][email], "e-posta"
            elif phone and phone in index["phone"]:
                found, how = index["phone"][phone], "telefon"
            elif name and name in index["name"]:
                candidates = index["name"][name]
                if len(candidates) == 1:
                    found, how = candidates[0], "ad"
                else:
                    state = "ambiguous"
                    note = (f"`{row.get('name')}` adına {len(candidates)} katılımcı var; "
                            "hangisi olduğu belirsiz — elle eşleştirilmeli.")

        if found is not None:
            member_id = as_int(found.get("id"))
            if member_id in used:
                state = "duplicate"
                note = f"Bu katılımcı {used[member_id]}. satırda zaten eşleşti."
            else:
                state = "matched"
                used[member_id] = row["line"]
        elif state == "unmatched" and not note:
            note = "Katılımcı listesinde karşılığı bulunamadı."

        if state == "matched" and not row.get("hasResult"):
            state = "empty"
            note = "Eşleşti ama satırda sonuç değeri yok; yazılacak bir şey yok."

        out.append({
            "line": row["line"],
            "csvName": text(row.get("name")),
            "csvEmail": text(row.get("email")),
            "csvPhone": text(row.get("phone")),
            "memberId": as_int(found.get("id")) if found else 0,
            "memberName": text(found.get("name")) if found else "",
            "matchedBy": how,
            "state": state,
            "note": note,
            "net": row.get("net"),
            "score": row.get("score"),
            "rank": row.get("rank"),
            "correct": row.get("correct"),
            "wrong": row.get("wrong"),
            "blank": row.get("blank"),
            "attended": row.get("attended"),
        })
    return out


def missing_members(matches: list[dict[str, Any]],
                    members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dosyada karşılığı OLMAYAN katılımcılar.

    Ekran bunu gösterir: 40 kayıtlı, 37 satır gelmişse üç kişi sınava girmemiş
    olabilir ya da kurum dosyayı eksik göndermiştir. İkisi de kullanıcının
    bilmesi gereken bir şeydir.
    """
    matched = {item["memberId"] for item in matches if item["state"] == "matched"}
    return [{"id": as_int(member.get("id")), "name": text(member.get("name")),
             "code": text(member.get("code"))}
            for member in members if as_int(member.get("id")) not in matched]


def match_summary(matches: list[dict[str, Any]], *, member_count: int = 0) -> dict[str, Any]:
    counts = {"matched": 0, "unmatched": 0, "ambiguous": 0, "duplicate": 0, "empty": 0}
    for item in matches:
        counts[item["state"]] = counts.get(item["state"], 0) + 1
    return {"total": len(matches), "memberCount": member_count, **counts,
            "writable": counts["matched"]}


def upload_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mağazaya gidecek gövde — YALNIZ `matched` satırlar.

    Belirsiz, mükerrer ve eşleşmeyen satırlar dışarıda kalır; onları da
    göndermek "yükledim" diyen ama yarısını sessizce düşüren bir uç olurdu.
    """
    body: list[dict[str, Any]] = []
    for item in matches:
        if item["state"] != "matched":
            continue
        row: dict[str, Any] = {"memberId": item["memberId"]}
        for key in ("net", "score", "rank", "correct", "wrong", "blank", "attended"):
            if item.get(key) is not None:
                row[key] = item[key]
        body.append(row)
    return body


# ==================================================================== sonuç

def derive_ranks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sıra gelmemişse puandan/netten üretir; BERABERLİK AYNI SIRAYI ALIR.

    Beraberliği kopararak sıralamak (1, 2, 3) iki eşit netten birini haksız
    yere geride gösterirdi. Standart rekabet sıralaması kullanılır: 1, 1, 3.
    """
    scored = [row for row in rows if row.get("score") is not None or row.get("net") is not None]
    ordered = sorted(scored, key=lambda row: (-(row.get("score") if row.get("score") is not None
                                                else -1e12),
                                              -(row.get("net") if row.get("net") is not None
                                                else -1e12)))
    place = 0
    previous: tuple[Any, Any] | None = None
    for index, row in enumerate(ordered, start=1):
        key = (row.get("score"), row.get("net"))
        if key != previous:
            place = index
            previous = key
        row["derivedRank"] = place
    for row in rows:
        row.setdefault("derivedRank", None)
        if row.get("rank") is None:
            row["rank"] = row["derivedRank"]
    return rows


def score_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Karnede "sen neredesin" sorusunu yanıtlayan sayılar."""
    nets = [row["net"] for row in rows if row.get("net") is not None]
    scores = [row["score"] for row in rows if row.get("score") is not None]
    attended = len([row for row in rows if row.get("attendance") == "attended"])
    return {
        "count": len(rows),
        "attended": attended,
        "netAverage": round(sum(nets) / len(nets), 2) if nets else None,
        "netBest": max(nets) if nets else None,
        "scoreAverage": round(sum(scores) / len(scores), 2) if scores else None,
        "scoreBest": max(scores) if scores else None,
    }


def attendance_rows(members: list[dict[str, Any]], *, spare: int = 0) -> list[list[str]]:
    """Yoklama çizelgesinin satırları — SIRA · AD · SINIF · KAYIT NO · İMZA.

    TELEFON VE E-POSTA BASILMAZ. Çizelge sınıfta elden ele dolaşır ve masada
    kalır; iletişim bilgisi orada işe yaramaz ama kişisel veriyi kâğıda
    çıkarır. Ekranda görünür, kâğıtta görünmez.
    """
    ordered = sorted(members, key=lambda row: fold(row.get("name")))
    out = [[str(index), text(row.get("name")), text(row.get("grade")) or "—",
            text(row.get("code")) or "—", ""]
           for index, row in enumerate(ordered, start=1)]
    for extra in range(max(0, int(spare))):
        out.append([str(len(ordered) + extra + 1), "", "", "", ""])
    return out


def audience_members(members: list[dict[str, Any]], audience: str) -> list[dict[str, Any]]:
    """Toplu bildirimin ALICI LİSTESİ. Bilinmeyen kitle boş liste döndürür."""
    if audience == "all":
        return list(members)
    if audience == "unpaid":
        return [row for row in members if row.get("payment") == "pending"]
    if audience == "attended":
        return [row for row in members if row.get("attendance") == "attended"]
    if audience == "absent":
        return [row for row in members if row.get("attendance") == "absent"]
    return []


def recipients(members: list[dict[str, Any]], channel: str) -> dict[str, Any]:
    """Kitleyi kanala göre ulaşılabilir/ulaşılamaz diye ayırır.

    Telefonu bozuk olan kişi "gönderildi" sayılmaz: SMS sağlayıcısı numarayı
    reddeder, biz de kullanıcıya 40 kişiye gitti deriz ama 37'sine gider.
    """
    reachable: list[dict[str, Any]] = []
    unreachable: list[dict[str, Any]] = []
    for row in members:
        target = phone_key(row.get("phone")) if channel == "sms" else text(row.get("email"))
        if target and (channel != "email" or "@" in target):
            reachable.append({"memberId": as_int(row.get("id")), "name": text(row.get("name")),
                              "phone": phone_key(row.get("phone")),
                              "email": text(row.get("email"))})
        else:
            unreachable.append({"memberId": as_int(row.get("id")),
                                "name": text(row.get("name")),
                                "reason": "Telefon numarası geçersiz" if channel == "sms"
                                          else "E-posta adresi yok"})
    return {"reachable": reachable, "unreachable": unreachable}
