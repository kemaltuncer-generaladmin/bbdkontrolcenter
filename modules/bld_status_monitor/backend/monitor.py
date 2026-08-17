"""Durum Monitörü — saf yardımcılar. AĞA ÇIKMAZ, DEPOYA YAZMAZ.

Burada yalnız biçim, etiket, parmak izi ve ön denetim var. İş kararı
`service.py`'de, HTTP kapısı `api/routes.py`'de durur; bu dosyanın tamamı yan
etkisizdir ve tek tek sınanabilir.

SAĞLIK HÜKMÜ BURADA VERİLMEZ. `health.status` (`ok` / `degraded` / `down`)
SUNUCUNUN tek cümlelik hükmüdür (`monitor.md` → `GET /summary`) ve bilerek
oradadır: üç ayrı ekranın (izleme, gösterge paneli, KDS yönetimi) aynı duruma
bakıp farklı renk göstermesi, hangisine inanılacağını belirsiz kılardı. Bu
dosya hükmü OKUR ve Türkçeleştirir; yeniden hesaplamaz.

TEK İSTİSNA `unknown`. Sunucu bu değeri hiç üretmez çünkü cevap veremediğinde
zaten cevap veremiyordur. "Bilinmiyor" hâli Kontrol Merkezi'nin kendi
gözlemidir ve `down` ile KARIŞTIRILMAZ: ilki "soramadım", ikincisi "sordum,
kötü" demektir. İkisini aynı kutuda göstermek, kopmuş bir ağı çökmüş bir
sisteme çevirirdi.

Kod İngilizce+ASCII, yorum Türkçe (depo kuralı).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any

# =========================================================== gerekçe sınırı

#: Gerekçenin en az uzunluğu (`00-genel.md` §3). Arayüzde alanı zorunlu
#: göstermek yetkilendirme değildir (K9); backend'de TEKRAR doğrulanır.
MIN_REASON = 10

#: En çok. `monitor/events/{id}/resolve` genel 500 sınırındadır ama KOMUT
#: gönderimi KDS ucundan geçiyor ve orada üst sınır yok (K-21 bir sınır
#: söylemiyor). İki ayrı sınır tutmak, kullanıcıya hangi kutuda ne kadar
#: yazabileceğini ezberletirdi; `bld_kds` ile aynı gerekçeyle TEK sınır
#: seçildi ve iki ucun da kabul ettiği dar olan alındı.
MAX_REASON = 160

#: `resolve` ucunun isteğe bağlı `note` alanı (sözleşme: en çok 500).
MAX_NOTE = 500


# ================================================================= sözlükler

#: `MonitorEvent.source` — sözleşmede AÇIKÇA sayılıdır (`monitor.md` → Şema).
#: Uydurma bir kaynak eklemek, sunucunun süzgeci tanımadığı için boş liste
#: döndürmesi ve ekranın onu "hata yok" diye göstermesi olurdu.
SOURCES: tuple[str, ...] = (
    "mutfakapp", "musteriapp", "website", "platform", "kontrol_merkezi",
)

SOURCE_LABELS = {
    "mutfakapp": "Mutfak kasası",
    "musteriapp": "Mobil uygulama",
    "website": "Web sitesi",
    "platform": "Sunucu",
    "kontrol_merkezi": "Kontrol Merkezi",
}

#: `MonitorEvent.level` — dört değer, sözleşmedeki sırayla (hafiften ağıra).
LEVELS: tuple[str, ...] = ("info", "warning", "error", "critical")

LEVEL_LABELS = {
    "info": "Bilgi",
    "warning": "Uyarı",
    "error": "Hata",
    "critical": "Kritik",
}

#: Ton TEK BAŞINA anlam taşımaz (kit kuralı 7): her rozetin içinde yazı var.
LEVEL_TONES = {
    "info": "dim",
    "warning": "warn",
    "error": "bad",
    "critical": "bad",
}

#: Varsayılan süzgeç `info` seviyesini GİZLER (sözleşme). Bilgi seviyesindeki
#: olaylar sayıca en kalabalık olanlardır ve listeyi doldurup gerçek hataları
#: görünmez kılarlar.
DEFAULT_LEVELS: tuple[str, ...] = ("warning", "error", "critical")

#: `resolved` süzgecinin üç değeri (sözleşme). Varsayılan `false` = açık olanlar.
RESOLVED_FILTERS: tuple[str, ...] = ("true", "false", "all")

#: `health.status` — SUNUCUNUN hükmü. `unknown` sözleşmede YOKTUR ve
#: sunucudan hiç gelmez; Kontrol Merkezi soruyu soramadığında kendi koyar.
HEALTH_STATUSES: tuple[str, ...] = ("ok", "degraded", "down", "unknown")

HEALTH_LABELS = {
    "ok": "Çalışıyor",
    "degraded": "Aksıyor",
    "down": "Durdu",
    "unknown": "Bilinmiyor",
}

HEALTH_TONES = {
    "ok": "good",
    "degraded": "warn",
    "down": "bad",
    "unknown": "dim",
}

#: `health.reasons` MAKİNE OKUNUR etiket listesidir ve sözleşme "panel Türkçe
#: karşılığını KENDİ yazar" diyor. Tanınmayan bir etiket gizlenmez, olduğu gibi
#: gösterilir: sunucuya yeni bir sebep eklendiğinde ekranın onu sessizce
#: yutması, ekranın eksik konuşması olurdu.
HEALTH_REASON_LABELS = {
    "printer_fault": "Yazıcı arızası bildiren kasa var",
    "critical_event_open": "Açık kritik olay var",
    "device_offline": "Çevrimdışı kasa var",
    "no_device_online": "Hiçbir kasa çevrimiçi değil",
    "queue_stuck": "Yazdırma kuyruğu akmıyor",
}

# =============================================================== bileşenler

#: EKRANDAKİ DÖRT KUTU. Her kutu bir bileşendir ve bir `source` değerine
#: bağlıdır; `kontrol_merkezi` kutuya girmez çünkü o BİZİZ — kendi kopukluğunu
#: bir bileşen arızası gibi göstermek, dört kutunun dördünü birden kırmızıya
#: boyayıp asıl sorunu (ağ) gizlerdi. O gözlem yerel geçmişe yazılır ve
#: kutular "bilinmiyor" der.
COMPONENTS: tuple[dict[str, Any], ...] = (
    {"key": "mobil", "source": "musteriapp", "label": "Mobil uygulama",
     "hint": "Müşteri uygulamasından bildirilen hatalar"},
    {"key": "web", "source": "website", "label": "Web sitesi",
     "hint": "Siteden bildirilen hatalar"},
    {"key": "kds", "source": "mutfakapp", "label": "Mutfak kasaları",
     "hint": "Kasa sağlığı, yazıcı ve baskı kuyruğu"},
    {"key": "sunucu", "source": "platform", "label": "Sunucu",
     "hint": "Sunucu tarafı hatalar ve genel hüküm"},
)

COMPONENT_KEYS: tuple[str, ...] = tuple(item["key"] for item in COMPONENTS)

# ========================================================== yerel geçmiş

#: `mod_bld_status_monitor_events.kind` — YEREL kaydın türü.
#:
#: `probe`  Kontrol Merkezi'nin araştırması: her yoklamada bir bileşenin ne
#:          durumda görüldüğü. Bunu uzak taraf TUTMUYOR.
#: `fault`  Araştırmanın kendisi başarısız: geçit patladı, uç yayında değil,
#:          imza reddedildi. Sunucuya HİÇ ULAŞMAYAN hata budur ve bir tek
#:          burada iz bırakır.
LOCAL_KINDS: tuple[str, ...] = ("probe", "fault")

#: Yerel kayıtta `result` sütununun alabileceği değerler. `probe` satırlarında
#: bileşenin görülen sağlığı, `fault` satırlarında her zaman `unknown`.
LOCAL_RESULTS: tuple[str, ...] = HEALTH_STATUSES

#: Yerel denetim izinin `result` sütununun alabileceği değerler.
TRIED = "denendi"
DONE = "ok"
DRY = "dry_run"
BLOCKED = "engellendi"
FAILED = "hata"

# ============================================================ düzeltme defteri

#: Defter kaydının kanalı.
#:
#: `bld.api` Komut geçitten gider ve GERÇEKTEN çalışır (K4).
#: `manual`  Kabuk erişimi gerektiren adım. Deftere YAZILABİLİR — "geçen sefer
#:           ne yapmıştık" sorusunun cevabı bir yerde durmalı — ama
#:           ÇALIŞTIRILAMAZ: `ssh` platform yeteneği bugün boş bir iskelet
#:           (`km_platform/ssh/`) ve olmayan bir yeteneği çağırmak yerine
#:           ekranın "bu adım elle yapılır" demesi doğrudur. Çalışmayan bir
#:           düğme bırakılmaz.
CHANNELS: tuple[str, ...] = ("bld.api", "manual")

CHANNEL_LABELS = {
    "bld.api": "BLD geçidi",
    "manual": "Elle yapılır",
}


class ActionSpec:
    """Defterden çalıştırılabilen TEK BİR eylemin tanımı.

    KAPALI LİSTE. Defter satırı bir veritabanı kaydıdır ve oradan okunan bir
    adı `getattr(api, name)` ile çağırmak, deftere yazma yetkisi olan birine
    geçidin TÜM metotlarını açardı — `cancel_order`, `void_invoice`,
    `run_sms_announcement` dâhil. Eylem adı bu tabloda yoksa çalıştırılmaz.
    """

    __slots__ = ("command", "destructive", "key", "label", "needs_device", "warning")

    def __init__(self, key: str, *, label: str, command: str, needs_device: bool,
                 destructive: bool, warning: str = "") -> None:
        self.key = key
        self.label = label
        #: `KitchenCommand` adı — `bld_kds/backend/devices.py::COMMANDS` ile
        #: BİREBİR. Sunucuda karşılığı olmayan bir ad, kuyruğa atılıp kasada
        #: sessizce yok sayılan bir komut demektir; yönetici "gitti" sanır.
        self.command = command
        self.needs_device = needs_device
        self.destructive = destructive
        self.warning = warning


#: Çalıştırılabilir eylemler. Hepsi `bld.api` → `send_command` üzerinden gider.
#:
#: `reprint` BİLEREK YOK: yük isteyen tek komut odur (`order_id` + fiş türü) ve
#: sipariş kimliği OLAYA ÖZELDİR, bir defter tanımına yazılamaz. Deftere
#: "8421 numaralı fişi bas" yazmak, ertesi gün başka bir siparişi bastırırdı.
#: Yeniden basım `bld_kds` ekranında, siparişin yanında durur.
#:
#: `unpair` DE YOK: eşlemeyi kaldırmak bir düzeltme değil, kasayı sahada yeni
#: kod girilene kadar tümüyle sipariş göremez hâle getirmektir. Bir izleme
#: ekranının "düzeltme" başlığı altında sunacağı bir şey değil.
RUNBOOK_ACTIONS: dict[str, ActionSpec] = {
    spec.key: spec for spec in (
        ActionSpec("kds.test_receipt", label="Test fişi bas", command="test_receipt",
                   needs_device=True, destructive=False),
        ActionSpec("kds.silence_alarm", label="Alarmı sustur", command="silence_alarm",
                   needs_device=True, destructive=False),
        ActionSpec("kds.clear_failed", label="Basılamayan işleri kuyruktan düşür",
                   command="clear_failed", needs_device=True, destructive=True,
                   warning="Düşen fişler BİR DAHA basılmaz."),
        ActionSpec("kds.clear_queue", label="Kuyruğun tamamını düşür",
                   command="clear_queue", needs_device=True, destructive=True,
                   warning="Bekleyen fişler de düşer; hiçbiri basılmaz."),
        ActionSpec("kds.restart", label="Uygulamayı yeniden başlat",
                   command="restart", needs_device=True, destructive=True,
                   warning="Kasa ekranı systemd geri getirene kadar karanlık kalır."),
        ActionSpec("kds.update", label="Kasayı güncelle", command="update",
                   needs_device=True, destructive=True,
                   warning="Kurulum sırasında ekran gider; mutfak sipariş göremez."),
    )
}

#: `manual` kanallı defter kaydının eylemi. Çalıştırılamaz; yalnız yazılı
#: yordamı taşır.
MANUAL_ACTION = "manual.note"

#: Defter anahtarı: küçük harf, rakam, alt çizgi ve nokta. Anahtar hem yolda
#: (`/runbook/{key}`) hem denetim izinde geçiyor; serbest metin olsaydı yol
#: kaçışıyla uğraşmak gerekirdi.
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.]{2,63}$")


# ================================================================= yardımcı

_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Parmak izi normalleştirmesi — sözleşmedeki kuralın AYNISI (`monitor.md` →
#: Tekilleştirme). Sayılar `<n>`, UUID'ler `<id>` olur; böylece "Sipariş 8421
#: basılamadı" ile "Sipariş 8422 basılamadı" AYNI olayın iki tekrarı sayılır.
#:
#: Kuralın burada TEKRAR yazılmasının sebebi: bu parmak izi YEREL satırlar
#: içindir ve sunucuya hiç gitmez. Aynı kuralı kullanmak bilinçli — iki geçmişi
#: yan yana koyan kişi, aynı hatanın iki defterde farklı bölünmüş olmasıyla
#: uğraşmasın.
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                   re.IGNORECASE)
_NUMBER = re.compile(r"\d+")


def text(value: Any) -> str:
    """Değeri kırpılmış metne çevirir. `None` boş dizedir."""
    if value is None:
        return ""
    return str(value).strip()


def foldable(value: Any) -> str:
    """Aksansız, küçük harfli arama anahtarı: "Yazıcı" → "yazici".

    Kitteki `foldText` ile AYNI kuralı uygular ve bilerek öyle: kullanıcı
    ekranda gördüğü kutuya "yazici" yazıp sonuç almalı, "yazıcı" yazmak zorunda
    kalmamalı. Kuralın burada tekrar yazılmasının sebebi K2 — modül kabuğun
    JavaScript'ini import edemez.
    """
    raw = str(value if value is not None else "").casefold()
    for source, target in (("ı", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"),
                           ("ö", "o"), ("ç", "c"), ("i̇", "i")):
        raw = raw.replace(source, target)
    return raw.strip()


def as_int(value: Any, default: int = 0) -> int:
    """Tamsayıya çevirir; çevrilemiyorsa `default`. İstisna atmaz."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    """ÜÇ DURUMLU. `None` "sunucu söylemedi" demektir ve KORUNUR.

    `printer_ok`, `sound_ok` ve `alarm_muted` alanlarının sözleşmede üç durumlu
    olmasının sebebi bu: sağlık bildirmemiş bir kasa ARIZALI SAYILMAZ. `bool(None)`
    yazmak, yeni kurulan her kasayı arıza sayacına yazardı (`monitor.md` →
    "Üç durumlu alanlar korunur").
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    lowered = text(value).lower()
    if lowered in {"1", "true", "evet", "yes", "on"}:
        return True
    if lowered in {"0", "false", "hayir", "hayır", "no", "off"}:
        return False
    return None


def now_iso() -> str:
    """Yerel iz için zaman damgası. UZAK VERİ DAMGALANMAZ — o sunucudan gelir."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 Zulu damgasını okur. Okunamazsa `None` — istisna atmaz."""
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def normalize_message(value: Any) -> str:
    """Mesajı parmak izi için sabitler: sayılar `<n>`, UUID'ler `<id>`.

    UUID ÖNCE değiştirilir: sonra çalıştırılsaydı sayı deseni UUID'nin
    içindeki rakam öbeklerini yiyip her UUID'yi başka bir dizeye çevirirdi ve
    aynı hata her seferinde YENİ bir olay sayılırdı — tekilleştirme sessizce
    hiçbir işe yaramazdı.
    """
    raw = _UUID.sub("<id>", text(value))
    return _NUMBER.sub("<n>", raw).strip()


def fingerprint(*, source: str, code: str, device_id: Any = None,
                message: Any = "") -> str:
    """Yerel satırın tekilleştirme anahtarı (sözleşmedeki kuralın aynısı).

    Aynı hata saatte yüzlerce kez tekrarlanabilir; her tekrarı ayrı satır
    yazmak tabloyu bir günde okunamaz hâle getirirdi. Kaydın kendisi SİLİNMEZ:
    tekrarda `occurrence_count` artar, `last_seen_at` ilerler ve `first_seen_at`
    HİÇ DEĞİŞMEZ — "bu ne zamandır oluyor" sorusunun cevabı odur.
    """
    device = text(device_id) or "-"
    canonical = f"{text(source)}|{text(code)}|{device}|{normalize_message(message)}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reason_error(value: str) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    clean = text(value)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı; "
                "denetim kaydına bu metin yazılır.")
    if len(clean) > MAX_REASON:
        return (f"Gerekçe en çok {MAX_REASON} karakter olabilir "
                "(iki ucun kabul ettiği dar sınır bu).")
    return ""


def date_error(value: Any, *, field: str) -> str:
    """Tarih `YYYY-MM-DD` mi? Boş değer serbesttir (süzgeç verilmemiş demektir)."""
    raw = text(value)
    if not raw:
        return ""
    if not _DATE_ONLY.match(raw):
        return f"{field} `YYYY-MM-DD` biçiminde olmalı; '{raw}' verildi."
    return ""


def since_error(value: Any) -> str:
    """`since` süzgeci ISO 8601 UTC bir AN olmalı (`monitor.md` → `GET /events`).

    Gün de kabul edilir ve gün verildiğinde sunucuya olduğu gibi gider: yalnız
    biçim denetlenir, değer DÖNÜŞTÜRÜLMEZ. Dönüştürmek, "16 Ağustos" diye
    süzenin gece yarısını hangi saat diliminde saydığımıza bağlı bir sonuç
    almasıydı ve o farkı ekranda hiçbir yerde göremezdi.
    """
    raw = text(value)
    if not raw:
        return ""
    if _DATE_ONLY.match(raw):
        return ""
    if parse_iso(raw) is None:
        return f"`since` ISO 8601 UTC bir an olmalı; '{raw}' okunamadı."
    return ""


def csv_filter(value: Any, allowed: tuple[str, ...], *, field: str) -> tuple[list[str], str]:
    """Virgüllü kapalı liste süzgecini KANONİK listeye çevirir. `(kodlar, hata)`.

    TANINMAYAN KOD REDDEDİLİR, sessizce elenmez. Sunucu tanımadığı kodu süzgece
    koyar ve sonuç boş döner; ekran o boşluğu "hata yok" diye gösterirdi — yani
    bir yazım hatası, izleme ekranının en tehlikeli yalanını söyletirdi.
    """
    if value is None:
        return [], ""
    if isinstance(value, str):
        parts = [item.strip() for item in value.split(",")]
    elif isinstance(value, list | tuple | set):
        parts = [text(item) for item in value]
    else:
        return [], f"{field} süzgeci okunamadı."

    wanted = [item for item in parts if item]
    unknown = sorted({item for item in wanted if item not in allowed})
    if unknown:
        return [], (f"Tanınmayan {field}: {', '.join(unknown)}. "
                    f"Tanınanlar: {', '.join(allowed)}.")
    # Sıra sözleşmedeki sıraya çekilir ve tekrarlar düşer: aynı süzgecin iki
    # farklı yazımı aynı isteği üretsin.
    return [code for code in allowed if code in set(wanted)], ""


def choice_error(value: Any, allowed: tuple[str, ...], *, field: str) -> str:
    """Kapalı listeli tekil süzgeç denetimi. Boş değer serbesttir."""
    raw = text(value)
    if not raw or raw in allowed:
        return ""
    return f"{field} yalnız şunlardan biri olabilir: {', '.join(allowed)}."


# ============================================================ satır biçimi

def event_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Sunucudaki hata olayı — YALNIZ ETİKET EKLER, hiçbir alanı ayıklamaz.

    Sözleşme additive büyüyor (`AGENTS.md` §2.3): bilinen alanları seçip
    gerisini atan bir dönüşüm, sunucuya eklenen her yeni alanı SESSİZCE
    düşürürdü. Satır olduğu gibi geçer; üzerine yalnız ekranın kendi etiketleri
    eklenir ve hepsi `*_label` / `*_tone` ile adlandırılır.
    """
    level = text(raw.get("level"))
    source = text(raw.get("source"))
    resolved = text(raw.get("resolved_at"))
    return {
        **raw,
        "level_label": LEVEL_LABELS.get(level, level or "—"),
        "level_tone": LEVEL_TONES.get(level, "dim"),
        "source_label": SOURCE_LABELS.get(source, source or "—"),
        "resolved": bool(resolved),
        # TEKRAR SAYISI EKRANDA DURMALI: 47 kez tekrarlanmış bir hata ile bir
        # kez görülmüş bir hata aynı satır yüksekliğinde ama aynı aciliyette
        # değil. Sözleşme alanı zaten veriyor; burada yalnız güvenli okunuyor.
        "occurrence_count": max(1, as_int(raw.get("occurrence_count"), 1)),
        "component": component_of(source),
    }


def component_of(source: Any) -> str:
    """Kaynağın hangi kutuya düştüğü. Bilinmeyen kaynak hiçbir kutuya girmez."""
    raw = text(source)
    for item in COMPONENTS:
        if item["source"] == raw:
            return str(item["key"])
    return ""


def device_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Kasa sağlık satırı. ÜÇ DURUMLU ALANLAR KORUNUR.

    `printer_ok` `None` ise kasa sağlık BİLDİRMEMİŞTİR ve arızalı SAYILMAZ.
    `queue_oldest_age_minutes` SUNUCUDA hesaplanır ve burada yeniden
    hesaplanmaz: "kuyrukta 4 iş var" ile "kuyrukta 4 iş var ve en eskisi 41
    dakikadır bekliyor" arasındaki fark sahaya gitme kararını değiştirir, ve o
    farkı istemcinin kaymış saatiyle hesaplamak yanlış karar verdirirdi.
    """
    printer = as_bool(raw.get("printer_ok"))
    sound = as_bool(raw.get("sound_ok"))
    muted = as_bool(raw.get("alarm_muted"))
    online = as_bool(raw.get("online"))
    revoked = bool(raw.get("revoked"))

    if revoked:
        tone, label = "dim", "İptal edilmiş"
    elif online is False:
        tone, label = "bad", "Çevrimdışı"
    elif online is None:
        tone, label = "dim", "Bilinmiyor"
    elif printer is False:
        tone, label = "warn", "Çevrimiçi · yazıcı arızalı"
    else:
        tone, label = "good", "Çevrimiçi"

    return {
        **raw,
        "online": online,
        "printer_ok": printer,
        "sound_ok": sound,
        "alarm_muted": muted,
        "revoked": revoked,
        "state_label": label,
        "state_tone": tone,
        "printer_label": _tri_label(printer, good="Yazıcı çalışıyor",
                                   bad="Yazıcı arızalı", unknown="Yazıcı bildirilmedi"),
        "sound_label": _tri_label(sound, good="Ses çalışıyor", bad="Ses arızalı",
                                  unknown="Ses bildirilmedi"),
        "alarm_label": _tri_label(muted, good="Alarm susturulmuş", bad="Alarm açık",
                                  unknown="Alarm durumu bildirilmedi"),
    }


def _tri_label(value: bool | None, *, good: str, bad: str, unknown: str) -> str:
    """Üç durumlu alanın YAZIYLA karşılığı. Renk tek başına anlam taşımaz."""
    if value is None:
        return unknown
    return good if value else bad


def summary_view(payload: Any) -> dict[str, Any]:
    """`GET /summary` gövdesini eksiksiz bir iskelete oturtur.

    Alan eksik gelse bile iskelet DURUR: panel `undefined` okumaz ve sıfır ile
    "bilinmiyor" ayrımı burada kaybolmaz. Sayaçların hepsi tamsayıdır ve
    `open_total` sunucudan gelir — dört seviyeyi toplamak, sunucu ileride
    beşinci bir seviye eklediğinde sessizce yanlış toplam üretirdi.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    data = data if isinstance(data, dict) else (payload if isinstance(payload, dict) else {})

    events = data.get("events") if isinstance(data.get("events"), dict) else {}
    devices = data.get("devices") if isinstance(data.get("devices"), dict) else {}
    health = data.get("health") if isinstance(data.get("health"), dict) else {}

    status = text(health.get("status")).lower()
    if status not in HEALTH_STATUSES:
        status = "unknown"
    reasons = health.get("reasons")
    reasons = [text(item) for item in reasons if text(item)] if isinstance(reasons, list) else []

    return {
        "events": {
            "open": _level_counts(events.get("open")),
            "open_total": as_int(events.get("open_total")),
            "critical_open": as_int(events.get("critical_open")),
            "last_24h": _level_counts(events.get("last_24h")),
            "oldest_open_at": text(events.get("oldest_open_at")) or None,
            "by_source": _source_counts(events.get("by_source")),
        },
        "devices": {
            "total": as_int(devices.get("total")),
            "online": as_int(devices.get("online")),
            "revoked": as_int(devices.get("revoked")),
            "printer_fault": as_int(devices.get("printer_fault")),
            "queue_pending": as_int(devices.get("queue_pending")),
            "queue_failed": as_int(devices.get("queue_failed")),
            "queue_oldest_age_minutes": as_int(devices.get("queue_oldest_age_minutes")),
        },
        "health": {
            "status": status,
            "label": HEALTH_LABELS[status],
            "tone": HEALTH_TONES[status],
            "reasons": reasons,
            "reason_labels": [HEALTH_REASON_LABELS.get(item, item) for item in reasons],
        },
    }


def _level_counts(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    return {level: as_int(raw.get(level)) for level in LEVELS}


def _source_counts(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    return {source: as_int(raw.get(source)) for source in SOURCES}


def component_tiles(summary: dict[str, Any], devices_meta: dict[str, Any], *,
                    connected: bool) -> list[dict[str, Any]]:
    """Dört KPI kutusu. Bağlantı yoksa dördü de `unknown` olur.

    KUTU BAŞINA HÜKÜM SUNUCUDAN GELMEZ ve gelemez: sözleşme `health.status`
    değerini SİSTEMİN BÜTÜNÜ için veriyor, bileşen başına değil. Buradaki
    türetme bu yüzden var ve KURALI TEK SATIRLIK: bileşenin açık `critical`
    olayı varsa `down`, açık `error`/`warning` olayı ya da (kasalarda) donanım
    arızası varsa `degraded`, hiçbiri yoksa `ok`.

    Bu, sunucunun hükmüyle YARIŞMAZ: bütünün hükmü ayrıca ve olduğu gibi
    gösterilir. Kutular "hangi bileşene bakmalıyım" sorusunu cevaplar, "sistem
    ayakta mı" sorusunu değil.
    """
    by_source = summary.get("events", {}).get("by_source", {})
    open_counts = summary.get("events", {}).get("open", {})
    critical_open = as_int(summary.get("events", {}).get("critical_open"))

    tiles: list[dict[str, Any]] = []
    for item in COMPONENTS:
        source = str(item["source"])
        count = as_int(by_source.get(source))
        notes: list[str] = []

        if not connected:
            status = "unknown"
            notes.append("Sunucuya ulaşılamadığı için durum okunamadı.")
        else:
            status = "ok"
            if count > 0:
                # `by_source` AÇIK olayları sayar (sözleşme). Seviye dağılımı
                # kaynak başına verilmiyor; bu yüzden kritik varlığı yalnız
                # BÜTÜN için bilinir ve tek kaynak varsa ona atfedilir.
                status = "degraded"
                notes.append(f"{count} açık olay")
            if critical_open > 0 and count > 0 and _only_source_with_events(by_source, source):
                status = "down"
                notes.append("Açık kritik olay bu bileşende")

        if item["key"] == "kds" and connected:
            status, extra = _kds_status(status, devices_meta)
            notes.extend(extra)

        tiles.append({
            "key": item["key"],
            "label": item["label"],
            "hint": item["hint"],
            "source": source,
            "open_events": count,
            "status": status,
            "status_label": HEALTH_LABELS[status],
            "tone": HEALTH_TONES[status],
            "notes": notes,
        })

    # `open` seviyeleri kutulara bölünemiyor; toplamı ekranın başka bir yerinde
    # göstermek için taşınır (panel bunu rozet olarak yazar).
    for tile in tiles:
        tile["open_levels"] = dict(open_counts) if tile["key"] == "sunucu" else {}
    return tiles


def _only_source_with_events(by_source: dict[str, Any], source: str) -> bool:
    """Açık olayı olan TEK kaynak bu mu?

    Kritik olayın hangi bileşende olduğunu sözleşme SÖYLEMİYOR (`critical_open`
    bütün için tek sayı). Yalnız tek bir kaynağın açık olayı varsa kritik onun
    olmak zorundadır; ötesi TAHMİNDİR ve tahmin edilmez — birden çok kaynakta
    olay varken kutulardan birini kırmızıya boyamak, yanlış ekibi sahaya
    gönderirdi.
    """
    with_events = [key for key, value in by_source.items() if as_int(value) > 0]
    return with_events == [source]


def _kds_status(status: str, devices_meta: dict[str, Any]) -> tuple[str, list[str]]:
    """Kasa kutusuna DONANIM durumunu ekler.

    Olay sayısı tek başına yetmez: yazıcısı bozuk ama hata bildirmemiş bir kasa
    olay üretmeyebilir ve kutu yeşil kalırdı. `meta` alanları sunucuda
    hesaplanıyor ve `printer_fault` YALNIZ `printer_ok === false` olanları
    sayıyor — bildirmemiş kasa arıza sayılmaz.
    """
    notes: list[str] = []
    total = as_int(devices_meta.get("total"))
    online = as_int(devices_meta.get("online"))
    fault = as_int(devices_meta.get("printer_fault"))
    failed = as_int(devices_meta.get("queue_failed"))
    rank = {"ok": 0, "degraded": 1, "down": 2, "unknown": 3}
    worst = status

    def raise_to(level: str) -> str:
        return level if rank[level] > rank[worst] else worst

    if total > 0 and online == 0:
        worst = raise_to("down")
        notes.append("Hiçbir kasa çevrimiçi değil")
    elif total > online:
        worst = raise_to("degraded")
        notes.append(f"{total - online} kasa çevrimdışı")
    if fault > 0:
        worst = raise_to("degraded")
        notes.append(f"{fault} kasada yazıcı arızası")
    if failed > 0:
        worst = raise_to("degraded")
        notes.append(f"{failed} baskı işi başarısız")
    return worst, notes


# ============================================================ düzeltme defteri

def runbook_error(*, key: str, title: str, channel: str, action: str,
                  device_id: int) -> str:
    """Defter kaydı kabul edilebilir mi — değilse gösterilecek metin."""
    if not _KEY_PATTERN.match(text(key)):
        return ("Defter anahtarı küçük harfle başlamalı; harf, rakam, alt çizgi "
                "ve nokta içerebilir (3-64 karakter).")
    if not 3 <= len(text(title)) <= 120:
        return "Başlık 3-120 karakter olmalı."
    if text(channel) not in CHANNELS:
        return f"Kanal yalnız şunlardan biri olabilir: {', '.join(CHANNELS)}."

    name = text(action)
    if text(channel) == "manual":
        if name and name != MANUAL_ACTION:
            return (f"Elle yapılan adımın eylemi yalnız '{MANUAL_ACTION}' olabilir; "
                    "geçitten geçmeyen bir komut çalıştırılamaz.")
        return ""

    spec = RUNBOOK_ACTIONS.get(name)
    if spec is None:
        return (f"'{action}' çalıştırılabilir bir eylem değil. Tanınanlar: "
                f"{', '.join(sorted(RUNBOOK_ACTIONS))}.")
    if spec.needs_device and as_int(device_id) < 1:
        return f"'{spec.label}' bir kasaya gönderilir; cihaz seçilmedi."
    return ""


def runbook_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Defter satırının ekranda görünen hâli."""
    channel = text(raw.get("channel"))
    action = text(raw.get("action"))
    spec = RUNBOOK_ACTIONS.get(action)
    enabled = as_bool(raw.get("enabled"))
    return {
        "key": text(raw.get("key")),
        "title": text(raw.get("title")),
        "description": text(raw.get("description")),
        "channel": channel,
        "channel_label": CHANNEL_LABELS.get(channel, channel or "—"),
        "action": action,
        "action_label": spec.label if spec else "Elle yapılır",
        "device_id": as_int(raw.get("device_id")),
        # ÇALIŞTIRILABİLİR Mİ — ekran düğmeyi buna bakarak çizer. `manual`
        # kayıtlarda `False` ve nedeni yazıyla söylenir; çalışmayan bir düğme
        # bırakılmaz (kit `blockedButton`).
        "runnable": bool(spec) and channel == "bld.api",
        "destructive": bool(spec and spec.destructive),
        "warning": spec.warning if spec else "",
        "enabled": True if enabled is None else enabled,
        "actor": text(raw.get("actor")),
        "updated_at": text(raw.get("updated_at")) or None,
        "created_at": text(raw.get("created_at")) or None,
    }


def local_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Yerel araştırma/hata satırı — bu modülün KENDİ defterinden.

    Sunucudaki satırla aynı adları taşır (`source`, `level`, `code`, `message`,
    `occurrence_count`, `first_seen_at`, `last_seen_at`) ki panel ikisini aynı
    tabloda gösterebilsin; ayrımı `origin` alanı taşır ve o alan hiçbir zaman
    boş olmaz.
    """
    level = text(raw.get("level"))
    source = text(raw.get("source"))
    kind = text(raw.get("kind"))
    result = text(raw.get("result"))
    return {
        "id": as_int(raw.get("id")),
        "origin": "yerel",
        "kind": kind,
        "kind_label": "Araştırma" if kind == "probe" else "Ulaşılamadı",
        "source": source,
        "source_label": SOURCE_LABELS.get(source, source or "—"),
        "component": text(raw.get("component")),
        "level": level,
        "level_label": LEVEL_LABELS.get(level, level or "—"),
        "level_tone": LEVEL_TONES.get(level, "dim"),
        "code": text(raw.get("code")),
        "message": text(raw.get("message")),
        "result": result,
        "result_label": HEALTH_LABELS.get(result, result or "—"),
        "result_tone": HEALTH_TONES.get(result, "dim"),
        "occurrence_count": max(1, as_int(raw.get("occurrence_count"), 1)),
        "first_seen_at": text(raw.get("first_seen_at")) or None,
        "last_seen_at": text(raw.get("last_seen_at")) or None,
        "fingerprint": text(raw.get("fingerprint")),
    }


def timeline_event(row: dict[str, Any]) -> dict[str, Any]:
    """Yerel satırı zaman çizelgesi öğesine çevirir (kit `timeline` sözleşmesi).

    ÖĞE `pending` TAŞIMAZ: bunların hepsi OLMUŞ şeylerdir. `pending` "olması
    beklenen adım" demektir ve geçmişte böyle bir şey yok.
    """
    count = max(1, as_int(row.get("occurrence_count"), 1))
    detail = row.get("message") or ""
    if count > 1:
        detail = f"{detail} · {count} kez".strip(" ·")
    return {
        "title": f"{row.get('source_label') or '—'} — {row.get('result_label') or '—'}",
        "detail": detail,
        "at": row.get("last_seen_at") or row.get("first_seen_at") or "",
        "tone": row.get("result_tone") or "dim",
    }


# ================================================================ sözleşme

def screen_contract() -> dict[str, Any]:
    """Panelin süzgeç şeridini, kutularını ve rozetlerini çizmek için okuduğu
    sözlük.

    YEREL VE AĞSIZ: geçit düşse bile süzgeçler, etiketler ve gerekçe sınırları
    çizilebilir (K7). Panelin bu listeleri kendi içinde tutması, sözleşme
    değiştiğinde iki yerde düzeltme demekti.
    """
    return {
        "components": [
            {"key": item["key"], "source": item["source"], "label": item["label"],
             "hint": item["hint"]}
            for item in COMPONENTS
        ],
        "sources": [{"code": code, "label": SOURCE_LABELS[code]} for code in SOURCES],
        "levels": [
            {"code": code, "label": LEVEL_LABELS[code], "tone": LEVEL_TONES[code],
             "in_default_filter": code in DEFAULT_LEVELS}
            for code in LEVELS
        ],
        "default_levels": list(DEFAULT_LEVELS),
        "resolved_filters": [
            {"code": "false", "label": "Açık olanlar"},
            {"code": "true", "label": "Çözülenler"},
            {"code": "all", "label": "Hepsi"},
        ],
        "health": [
            {"code": code, "label": HEALTH_LABELS[code], "tone": HEALTH_TONES[code]}
            for code in HEALTH_STATUSES
        ],
        "channels": [{"code": code, "label": CHANNEL_LABELS[code]} for code in CHANNELS],
        "actions": [
            {"code": spec.key, "label": spec.label, "command": spec.command,
             "needs_device": spec.needs_device, "destructive": spec.destructive,
             "warning": spec.warning}
            for spec in sorted(RUNBOOK_ACTIONS.values(), key=lambda item: item.key)
        ],
        "manual_action": MANUAL_ACTION,
        "reason": {"min": MIN_REASON, "max": MAX_REASON, "note_max": MAX_NOTE},
    }
