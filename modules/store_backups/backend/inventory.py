"""Yedek envanterinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın bütün riski hesaplarda: "son yedek ne kadar
eski", "kalan alanla kaç yedek daha alınır", "bu yedek silinebilir mi",
"dosya adı yola konabilir mi". Hepsi girdi→çıktı fonksiyonu olarak burada
durur; serviste gömülü olsalardı tek satırı bile ağ olmadan test edilemezdi.

MAĞAZA UCU HENÜZ YAYINDA DEĞİL (`/api/admin/bbd/backups`). Alan adlarının
kesin biçimi bilinmiyor; bu yüzden her alan BİRDEN ÇOK adayla aranır
(`pick`). Tek ada bağlanmak, uç yayınlandığı gün ekranı boş gösterirdi.

ALTI KURAL — hepsinin karşılığı burada bir fonksiyondur:

 1. Bilinmeyen değer UYDURULMAZ  → `disk_view` "bilinmiyor" der, 0 yazmaz.
 2. Dosya adı yola girmeden önce → `safe_name` denetler ('..', '/' reddedilir).
 3. Doğrulanmamış yedek "tamam"  → `verify_state` üç durum ayırır: ok/bad/unknown.
    sayılmaz
 4. "Kaç yedek daha sığar"       → `disk_view` SON yedeklerin ortalamasını alır,
    tek yedeğe bakmaz (kapsam değişince boyut da değişir).
 5. Yeni yedek silinemez         → `deletable` yaş kapısı; gerekçe ayrı katman.
 6. Saat dilimi                  → `parse_time` saat dilimsiz damgayı YEREL
                                   sayar (mağaza öyle gönderiyor); UTC saymak
                                   her yedeği üç saat gençleştirirdi.
 7. Uç yayında değil ≠ mağaza    → `endpoint_state` ÜÇ hâl ayırır: yayında ·
    çökmüş                         uç yok (404) · uç kapalı (503). Hepsi aynı
                                   cümleyle anlatılırsa personel boşuna sunucu
                                   arar.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

#: Gerekçenin en az uzunluğu. Geçit (store_api) de 10 istiyor; burada tekrar
#: doğrulanır çünkü arayüzde gizlemek yetkilendirme değildir (K9).
MIN_REASON = 10

#: Geçidin "BBD'ye özel uç henüz yayında değil" kodu (`StoreApiError.code`).
#: METNE BAKILMAZ: çeviri değişince metin kırılır, kod kırılmaz. Modül modülü
#: import etmediği için (K3) sınıf değil, yalnız kodun kendisi taşınır.
MISSING_CODE = "bbd_endpoint_missing"

#: Mağazanın KENDİ hata kodu — çeviri değil, makine jetonu. Uç YAYINDA ama
#: `BBD_CONTROL_API_ENABLED` anahtarı kapalı olduğunda gövdede bu gelir.
#:
#: CANLIDA DOĞRULANDI (2026-08-13): `GET /api/admin/bbd/backups` artık 404
#: DEĞİL, **HTTP 503** dönüyor:
#:     {"error": {"code": "CONTROL_API_DISABLED",
#:                "message": "Kontrol Merkezi API'si kapalı. ..."}}
#: Aynı anda `/api/admin/settings/channels` 200, `/api/admin/orders` 200 —
#: mağaza AYAKTA. Geçit 503'ü `code="server"` diye işaretlediği için
#: `MISSING_CODE` bu hâli YAKALAMAZ; jeton geçidin ürettiği hata metninde
#: taşınır (`client._fail` gövdedeki `error` alanını metne koyuyor, `mask_text`
#: yalnız sır alanlarını yıldızlıyor, `code` sırlı sayılmıyor).
DISABLED_TOKEN = "CONTROL_API_DISABLED"

#: Ucun üç hâli. İkisi de "yayında değil"dir ama ÇARESİ farklıdır ve bu ekranın
#: kuralı gereği farklı çareler aynı cümleyle anlatılmaz.
ENDPOINT_LIVE = "live"
ENDPOINT_MISSING = "missing"       # 404 — paket henüz yayınlanmadı
ENDPOINT_DISABLED = "disabled"     # 503 — paket yayında, anahtar kapalı

MISSING_TEXT = ("Mağazanın yedek uçları henüz yayında değil (/api/admin/bbd/backups). "
                "Mağaza ayakta; eksik olan bu ekranın konuştuğu paket. Paket "
                "yayınlanınca ekran kendiliğinden çalışacak.")

DISABLED_TEXT = ("Mağazadaki Kontrol Merkezi API'si KAPALI (CONTROL_API_DISABLED). "
                 "Uç yayında ve mağaza ayakta — sunucuda arıza yok, kapalı olan bir "
                 "anahtar. Mağaza sunucusunda BBD_CONTROL_API_ENABLED=true yapılıp "
                 "yapılandırma önbelleği yenilendiğinde bu ekran kendiliğinden "
                 "çalışacak. Yedek almak için sunucuya gitmeye gerek yok.")


def endpoint_state(failure: Exception) -> str:
    """Hata "uç yayında değil" mi, yoksa GERÇEK arıza mı — üç değerli cevap.

    Boş dize döndürmek "bu gerçek bir arıza" demektir; `ENDPOINT_MISSING` ve
    `ENDPOINT_DISABLED` ise mağazanın ayakta olduğu, yalnız bu ekranın
    konuştuğu paketin çalışmadığı iki ayrı hâldir.

    NEDEN İKİ HÂL. 404'te yapılacak bir şey yoktur (paket henüz yazılıyor);
    503'te bir anahtar kapalıdır ve açan biri vardır. İkisini "Mağazaya
    ulaşılamadı" diye tek cümlede toplamak, ayakta olan bir mağaza için
    personeli sunucu odasına gönderirdi — bu modülün baştan beri önlemeye
    çalıştığı hata tam olarak budur.
    """
    if getattr(failure, "code", "") == MISSING_CODE:
        return ENDPOINT_MISSING
    # Jetona bakılır, duruma değil: aynı kod ileride 403 ile de gelebilir.
    # `CONTROL_API_DISABLED` mağazanın makine jetonudur, çevrilen bir cümle
    # değil — metne bakma yasağı çevrilebilir metinler içindir.
    if DISABLED_TOKEN in str(failure):
        return ENDPOINT_DISABLED
    return ""


def endpoint_text(state: str) -> str:
    """Ucun hâlini anlatan cümle. Bilinmeyen hâl için boş döner."""
    if state == ENDPOINT_MISSING:
        return MISSING_TEXT
    if state == ENDPOINT_DISABLED:
        return DISABLED_TEXT
    return ""

#: Mağazanın kabul ettiği kapsam anahtarları (`bbd_create_backup` docstring'i).
SCOPES = ("database", "uploads", "config")

SCOPE_LABELS = {
    "database": "Veritabanı",
    "uploads": "Yüklenen dosyalar",
    "config": "Ayarlar",
}

KIND_LABELS = {
    "manual": "Manuel",
    "scheduled": "Zamanlanmış",
    "auto": "Otomatik",
    "safety": "Güvenlik",
}

VERIFY_OK = "ok"
VERIFY_BAD = "bad"
VERIFY_UNKNOWN = "unknown"

VERIFY_LABELS = {
    VERIFY_OK: "Doğrulandı",
    VERIFY_BAD: "BOZUK",
    VERIFY_UNKNOWN: "Doğrulanmadı",
}

#: Dosya adı hem URL yoluna hem yerel diske girer. Beyaz liste kullanılır:
#: kara liste ("`..` yasak") her seferinde bir kaçış biçimini atlıyor.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,180}$")


# ============================================================ temel okuyucu

def text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def pick(raw: Any, *names: str) -> Any:
    """Bir alanı olası adlarının hepsinde arar.

    BBD ucu yazılırken alan adı `created_at` da olabilir `createdAt` da;
    yayınlandığı gün hangisi geldiyse ekran çalışmalı. Bulunamazsa `None`.
    """
    if not isinstance(raw, dict):
        return None
    for name in names:
        if name in raw and raw[name] not in (None, ""):
            return raw[name]
    return None


def parse_time(value: Any) -> datetime | None:
    """Zaman damgasını okur. Çözülemezse `None` — bugün uydurulmaz.

    SAAT DİLİMSİZ DAMGA YEREL SAYILIR, UTC DEĞİL. Mağaza `"2026-08-13 19:54:51"`
    biçiminde, saat dilimsiz ve YEREL damga gönderiyor (canlıda doğrulandı;
    aynı tuzak store_orders README'sinde de yazılı). UTC saymak yedeği üç saat
    GENÇLEŞTİRİR: 50 saatlik bir yedek 47 saatlik görünür ve 48 saatlik
    bayatlama eşiğini hiç tetiklemez — durum bandı yeşil kalırken elimizdeki
    en yeni yedek çoktan eskimiş olur. Aynı kayma silme yaş kapısını da üç
    saat erken açar.
    """
    raw = text(value)
    if not raw:
        return None
    candidate = raw.replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.astimezone()


def age_hours(value: Any, now: datetime | None = None) -> float | None:
    moment = parse_time(value)
    if moment is None:
        return None
    reference = now or datetime.now(UTC)
    return (reference - moment).total_seconds() / 3600.0


def age_days(value: Any, now: datetime | None = None) -> float | None:
    hours = age_hours(value, now)
    return None if hours is None else hours / 24.0


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — boşsa/kısaysa kullanıcıya gösterilecek metin."""
    if len(text(value)) < MIN_REASON:
        return f"Gerekçe en az {MIN_REASON} karakter olmalı; denetim kaydına bu metin yazılır."
    return ""


def safe_name(value: Any) -> str:
    """Yedek adını yola/URL'ye koymadan önce denetler; kabul edilmezse boş döner.

    Ad hem `/api/admin/bbd/backups/{name}/restore` yoluna hem de indirme
    sırasında yerel dosya adına giriyor. `..%2f` ya da mutlak yol gönderen
    bozuk (veya ele geçirilmiş) bir envanter yanıtı, denetlenmezse rapor
    klasörünün dışına yazdırırdı.
    """
    name = text(value)
    if not NAME_PATTERN.fullmatch(name):
        return ""
    if ".." in name:
        return ""
    return name


# ================================================================== kapsam

def normalize_scope(value: Any) -> list[str]:
    """Kapsam listesini süzer ve SABİT sıraya sokar.

    Sıra sabittir: gerekçe metni ve denetim izi iki farklı çağrıda aynı
    görünsün diye. Bilinmeyen anahtar sessizce atılır — mağaza tanımadığı
    kapsamı yok sayıyor ve kullanıcı "ayarları da yedekledim" sanıyor.
    """
    if isinstance(value, str):
        items = [part for part in re.split(r"[,\s]+", value) if part]
    elif isinstance(value, list | tuple | set):
        items = [text(item) for item in value]
    else:
        items = []
    chosen = {item.lower() for item in items}
    return [scope for scope in SCOPES if scope in chosen]


def scope_label(scopes: list[str]) -> str:
    if not scopes:
        return "—"
    if len(scopes) == len(SCOPES):
        return "Tamamı"
    return " · ".join(SCOPE_LABELS.get(scope, scope) for scope in scopes)


# ============================================================== doğrulama

def verify_state(raw: dict[str, Any]) -> str:
    """Doğrulama durumu ÜÇ değerlidir: doğrulandı · bozuk · doğrulanmadı.

    İkiye indirmek (doğru/yanlış) en tehlikeli hatayı üretirdi: hiç
    doğrulanmamış yedek "tamam" görünür ve felaket gününde açılmaz.
    """
    value = pick(raw, "verify_state", "verifyState", "integrity", "verified", "verify")
    if isinstance(value, bool):
        return VERIFY_OK if value else VERIFY_BAD
    token = text(value).lower()
    if token in ("ok", "valid", "verified", "passed", "true", "1", "success"):
        return VERIFY_OK
    if token in ("bad", "corrupt", "invalid", "failed", "false", "0", "mismatch"):
        return VERIFY_BAD
    # Doğrulama zamanı var ama sonucu yoksa: geçmişte doğrulanmış demektir.
    if text(pick(raw, "verified_at", "verifiedAt")):
        return VERIFY_OK
    return VERIFY_UNKNOWN


# ================================================================== satır

def backup_row(raw: Any, *, now: datetime | None = None) -> dict[str, Any]:
    """Mağaza kaydını ekranın satırına çevirir. Eksik alan "—" olur, uydurulmaz."""
    if not isinstance(raw, dict):
        raw = {}
    name = text(pick(raw, "name", "file", "filename", "fileName", "id"))
    created = text(pick(raw, "created_at", "createdAt", "date", "finished_at", "finishedAt"))
    scopes = normalize_scope(pick(raw, "scope", "scopes", "parts", "contents"))
    size = as_int(pick(raw, "size", "bytes", "size_bytes", "sizeBytes", "length"))

    duration = pick(raw, "duration_seconds", "durationSeconds", "duration", "elapsed")
    seconds = as_float(duration)
    if seconds > 10_000:  # ms gelmiş olabilir; saatlerce süren yedek gerçekçi değil
        seconds = seconds / 1000.0

    state = verify_state(raw)
    kind = text(pick(raw, "kind", "type", "source", "trigger")).lower() or "manual"
    days = age_days(created, now)

    return {
        "name": name,
        "createdAt": created,
        "ageDays": None if days is None else round(days, 2),
        "scope": scopes,
        "scopeLabel": scope_label(scopes),
        "bytes": size,
        "durationSeconds": round(seconds, 1),
        "actor": text(pick(raw, "actor", "user", "created_by", "createdBy", "author")) or "—",
        "note": text(pick(raw, "note", "comment", "description")),
        "sha256": text(pick(raw, "sha256", "checksum", "hash", "digest")),
        "verifyState": state,
        "verifyLabel": VERIFY_LABELS[state],
        "verifiedAt": text(pick(raw, "verified_at", "verifiedAt")),
        "path": text(pick(raw, "path", "file_path", "filePath", "location", "url")),
        "kind": kind,
        # Tanımadığımız tür "Manuel" YAZILMAZ: rotasyonun bıraktığı bir yedeği
        # elle alınmış göstermek, "bunu ben aldım, silebilirim" dedirtir.
        # Bilinmeyen tür ham hâliyle görünür (rule 1: uydurma yok).
        "kindLabel": KIND_LABELS.get(kind) or kind or "—",
        "nameSafe": bool(safe_name(name)),
    }


def sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """En yeni en üstte. Zamanı çözülemeyen satır sona düşer ama KAYBOLMAZ."""
    def key(row: dict[str, Any]) -> tuple[int, str]:
        moment = parse_time(row.get("createdAt"))
        return (0 if moment is None else 1, moment.isoformat() if moment else "")
    return sorted(rows, key=key, reverse=True)


def find_row(rows: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    wanted = text(name)
    for row in rows:
        if row.get("name") == wanted:
            return row
    return None


# ==================================================================== disk

def disk_view(meta: Any, rows: list[dict[str, Any]], *, sample: int = 5) -> dict[str, Any]:
    """Disk göstergesi ve "yaklaşık kaç yedek daha sığar" tahmini.

    TAHMİN SON `sample` YEDEĞİN ORTALAMASINDAN çıkar, son yedeğin boyutundan
    değil: kapsamı dar bir yedek (yalnız ayarlar) birkaç yüz kilobayt olur ve
    "daha 900 yedek sığar" gibi anlamsız bir sayı üretirdi.

    Disk bilgisi gelmediyse `known: False` döner ve ekran "bilinmiyor" der.
    Sıfır göstermek "disk dolu" demekle aynı şey olurdu.
    """
    block = meta if isinstance(meta, dict) else {}
    inner = pick(block, "disk", "storage", "volume")
    if isinstance(inner, dict):
        block = {**block, **inner}

    total = as_int(pick(block, "total", "total_bytes", "totalBytes", "size"))
    free = as_int(pick(block, "free", "free_bytes", "freeBytes", "available"))
    used = as_int(pick(block, "used", "used_bytes", "usedBytes"))
    if total and free and not used:
        used = max(0, total - free)
    if total and used and not free:
        free = max(0, total - used)

    sizes = [row["bytes"] for row in rows[: max(1, sample)] if row.get("bytes")]
    average = int(sum(sizes) / len(sizes)) if sizes else 0

    known = bool(total or free)
    remaining = free // average if (free and average) else None

    return {
        "known": known,
        "totalBytes": total,
        "freeBytes": free,
        "usedBytes": used,
        "usedPercent": round(used * 100 / total, 1) if total else None,
        "averageBytes": average,
        "estimatedBackups": remaining,
        "sampleCount": len(sizes),
    }


def disk_sentence(disk: dict[str, Any]) -> str:
    """Disk göstergesinin altındaki cümle. Renk tek başına anlam taşımaz.

    Tahmin yapılamadığında EKSİK OLANIN ADI SÖYLENİR. "Kalan alan biliniyor
    ama yedek boyutu bilinmiyor" cümlesini boş alan gelmediğinde de yazmak,
    kullanıcıyı yanlış tarafa bakmaya gönderir: gidip yedek boyutunu arar,
    oysa eksik olan sunucunun boş alanıdır.
    """
    if not disk.get("known"):
        return ("Disk durumu mağazadan gelmedi; kalan alan bilinmiyor. "
                "Yedek almadan önce sunucudaki boş alanı elle kontrol edin.")
    remaining = disk.get("estimatedBackups")
    if remaining is None:
        if not disk.get("freeBytes"):
            return ("Toplam alan biliniyor ama BOŞ alan gelmedi; kaç yedek daha sığacağı "
                    "hesaplanamadı. Sunucudaki boş alanı elle kontrol edin.")
        return "Kalan alan biliniyor ama yedek boyutu bilinmediği için tahmin yapılamadı."
    if remaining <= 0:
        return "Kalan alan bir yedeğe bile yetmiyor. Eski yedekleri taşıyın."
    return (f"Kalan alanla yaklaşık {remaining} yedek daha alınabilir "
            f"(son {disk.get('sampleCount') or 0} yedeğin ortalaması esas alındı).")


# ================================================================ durum bandı

def unreadable_summary(note: str = "") -> dict[str, Any]:
    """Envanter OKUNAMADIĞINDA durum bandı. "Hiç yedek yok" DEMEZ.

    Boş liste ile okunamamış liste aynı şey değildir. Mağazaya ulaşılamadığında
    `summary([])` çağırmak ekrana "Hiç yedek alınmamış. Canlı mağaza verisi
    yedeksiz duruyor." yazdırırdı — bu, dün gece yedeği alınmış bir mağaza için
    düpedüz yalan ve gereksiz bir panik sebebidir. Bilmediğimizi söylüyoruz.
    """
    return {"has": False, "known": False, "tone": "warn", "name": "", "createdAt": "",
            "bytes": 0, "ageHours": None, "verifyState": VERIFY_UNKNOWN,
            "text": (note or "Yedek envanteri okunamadı; son yedeğin durumu BİLİNMİYOR. "
                     "Bu 'yedek yok' demek değildir — liste alınamadı.")}


def summary(rows: list[dict[str, Any]], *, now: datetime | None = None,
            stale_after_hours: int = 48) -> dict[str, Any]:
    """Durum bandı: son yedek ne zaman, ne kadar, doğrulandı mı.

    Renk TEK BAŞINA anlam taşımaz; `text` her zaman aynı bilgiyi yazıyla da
    söyler (kit kuralı 7). Envanter hiç okunamadıysa bu değil
    `unreadable_summary` çağrılır.
    """
    if not rows:
        return {"has": False, "known": True, "tone": "bad", "name": "", "createdAt": "",
                "bytes": 0, "ageHours": None, "verifyState": VERIFY_UNKNOWN,
                "text": "Hiç yedek alınmamış. Canlı mağaza verisi yedeksiz duruyor."}

    last = rows[0]
    hours = age_hours(last.get("createdAt"), now)
    state = last.get("verifyState")

    if state == VERIFY_BAD:
        tone = "bad"
        note = "SON YEDEK BOZUK — sağlama toplamı tutmuyor. Yeni yedek alın."
    elif hours is None:
        tone = "warn"
        note = "Son yedeğin tarihi okunamadı; yaşı bilinmiyor."
    elif hours > stale_after_hours:
        tone = "bad" if hours > stale_after_hours * 3 else "warn"
        note = f"Son yedek {int(hours)} saat önce alınmış ({stale_after_hours} saatten eski)."
    elif state == VERIFY_UNKNOWN:
        tone = "warn"
        note = "Son yedek hiç doğrulanmadı; açılıp açılmadığı bilinmiyor."
    else:
        tone = "good"
        note = f"Son yedek {int(hours)} saat önce alındı ve doğrulandı."

    return {"has": True, "known": True, "tone": tone, "name": last.get("name", ""),
            "createdAt": last.get("createdAt", ""), "bytes": last.get("bytes", 0),
            "ageHours": None if hours is None else round(hours, 1),
            "verifyState": state, "scopeLabel": last.get("scopeLabel", "—"), "text": note}


# ================================================================== silme

def deletable(row: dict[str, Any] | None, *, min_age_days: int) -> tuple[bool, str]:
    """Yedek silinebilir mi — YALNIZ yaş kapısı. Gerekçe ve izin ayrı katmandır.

    Yaş `backup_row` içinde hesaplanmıştır (`ageDays`); burada yeniden zaman
    okunmaz, yoksa listedeki satır ile karar farklı ana bakabilirdi.

    Yeni yedeğin silinmesi engellenir: elde kalan tek sağlam kopya çoğu zaman
    en yenisidir ve "yer açayım" refleksi tam onu siler.
    """
    if row is None:
        return False, "Yedek envanterde bulunamadı."
    days = row.get("ageDays")
    if days is None:
        return False, "Yedeğin tarihi okunamadı; yaşı bilinmeyen yedek silinmez."
    if days < min_age_days:
        return False, (f"Yedek {int(days)} günlük; yalnız {min_age_days} günden eski yedekler "
                       "silinebilir.")
    return True, ""


# ==================================================================== CSV

CSV_HEADERS = ["Dosya", "Tarih", "Kapsam", "Boyut (bayt)", "Süre (sn)", "Alan kişi",
               "Not", "Doğrulama", "Tür", "SHA-256", "Yol"]


def csv_table(rows: list[dict[str, Any]]) -> list[list[str]]:
    return [[row["name"], row["createdAt"], row["scopeLabel"], str(row["bytes"]),
             str(row["durationSeconds"]), row["actor"], row["note"], row["verifyLabel"],
             row["kindLabel"], row["sha256"], row["path"]] for row in rows]
