"""KDS verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın kararlarının çoğu tek bir sözlüğe bakıp "bu değer
kabul edilir mi, ekranda ne yazar" sorusuna cevap vermekten ibaret: 24 yönetilen
ayarın sınırları, sekiz komutun hangisinin yıkıcı olduğu, bir revizyon
gövdesinin tam liste olup olmadığı, bir durum geçişinin matriste bulunup
bulunmadığı. Servise gömülselerdi tek satırı bile ağ taklidi olmadan
sınanamazdı; burada hepsi girdi→çıktı fonksiyonudur.

ALAN ADLARI snake_case KALIR — TEK İSTİSNA `disabled_sound_events` DEĞERİDİR.
Anahtar snake_case, içindeki olay adları (`newOrder`, `lateOrder`, …) kasadaki
`KdsSoundEvent` enum adlarıyla birebir camelCase'tir: onlar bir alan adı değil,
telde taşınan bir VERİDİR ve çevrilseydi kasada hiçbir şeye denk gelmezdi.

K-21 sözleşmesi (§2) tel üzerindeki sözlüğü
snake_case olarak sabitliyor ve aynı ayar anahtarları üç yerde birden yaşıyor:
BLD göçünde, `KitchenDeviceSettings::forDevice` çıktısında ve kasanın
`KitchenManagedSettings` okuyucusunda. Arada camelCase'e çevirmek dördüncü bir
sözlük yaratır ve yanlış çeviri SESSİZ kalır — `allowOrderEdit` diye yazılan
anahtar sunucuda hiçbir şeye denk gelmez, hata da vermez, yönetici kilidi
açtığını sanır. Bu yüzden panelin gördüğü anahtarlar da snake_case'tir.

DÖRT TUZAK — her birinin karşılığı burada bir fonksiyondur:

 1. Sunucu ayarı KIRPAR, reddetmez     → `clean_settings` aralık dışını burada
    (`KitchenDeviceSettings::normalize`)  REDDEDER. 2 saniyelik yoklama isteyen
                                          yönetici sessizce 3 almamalı; ne
                                          yazdığını ve neyin kabul edildiğini
                                          bilmeli.
 2. `null` = "yönetici dokunmadı"      → `clean_settings` `None` değerini her
    (yani SERBEST), `false` = kilit       anahtarda kabul eder: kilidi kaldırmanın
                                          tek yolu budur. Yeni 7 alanın
                                          varsayılanı `null`; alan eklenmesi
                                          bugünkü kasaları KİLİTLEMEZ.
 3. `printer_ok` üç durumludur         → `device_row` "sağlık bildirmemiş
    (`sound_ok` ve `alarm_muted` de)      cihaz"ı arızalı SAYMAZ; `None` kalır.
 4. Revizyon TAM LİSTEDİR              → `revision_items` boş listeyi reddeder;
                                          kalem farkı gönderilmez.
 5. Sesli olay listesinde `null` ile   → `sound_events_*` üçlüsü. `null`
    boş dize AYRI ŞEYLERDİR              "dokunulmadı", `""` "hiçbiri kapalı
                                          olmasın"; ikisini birleştirmek,
                                          susturmaları kaldırmayı imkânsız
                                          kılardı.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# ============================================================ gerekçe ve aktör

#: Gerekçenin en az uzunluğu. BLD tarafı da 10 istiyor (K-21 §3); burada TEKRAR
#: doğrulanır çünkü arayüzde alanı zorunlu göstermek yetkilendirme değildir (K9)
#: ve istemci gövdeyi elle kurabilir.
MIN_REASON = 10

#: En çok. Revizyon ve durum uçlarında sunucu `max:160` uyguluyor; tek bir üst
#: sınır kullanmak, 200 karakterlik gerekçenin YALNIZ bazı uçlarda patlamasını
#: önler — kullanıcı hangi ekranda ne kadar yazabileceğini ezberlemek zorunda
#: kalmaz.
MAX_REASON = 160

# =================================================================== komutlar

TEST_RECEIPT = "test_receipt"
REPRINT = "reprint"
CLEAR_FAILED = "clear_failed"
SILENCE_ALARM = "silence_alarm"
RESTART = "restart"
UPDATE = "update"
UNPAIR = "unpair"
CLEAR_QUEUE = "clear_queue"

#: `KitchenCommand::ALL` ile BİREBİR: sözleşmenin 5'i (§2.3) + K-22 §2'nin 3
#: yenisi. Buraya sunucuda karşılığı OLMAYAN bir ad eklemek, kuyruğa atılıp
#: kasada sessizce yok sayılan bir komut demektir; yönetici "gitti" sanır.
COMMANDS = (TEST_RECEIPT, REPRINT, CLEAR_FAILED, SILENCE_ALARM, RESTART,
            UPDATE, UNPAIR, CLEAR_QUEUE)

COMMAND_LABELS = {
    TEST_RECEIPT: "Test fişi bas",
    REPRINT: "Fişi yeniden bas",
    CLEAR_FAILED: "Basılamayan işleri kuyruktan düşür",
    SILENCE_ALARM: "Alarmı sustur",
    RESTART: "Uygulamayı yeniden başlat",
    UPDATE: "Kasayı güncelle",
    UNPAIR: "Eşlemeyi kaldır",
    CLEAR_QUEUE: "Kuyruğun tamamını düşür",
}

#: AYRI İZİN İSTEYEN KOMUTLAR (`bld_kds.devices`). Ortak yanları: hepsi mutfağı
#: sipariş göremez ya da fiş basamaz hâle getirebilir.
#:
#:  · `restart`     uygulamayı kapatır — systemd geri getirene kadar ekran karanlık
#:  · `clear_failed`basılamamış işleri düşürür; o fişler BİR DAHA basılmaz
#:  · `clear_queue` üstelik BEKLEYEN işleri de düşürür — `clear_failed`in üst kümesi
#:  · `update`      `.deb` kurar ve servisi yeniden başlatır; kurulum sırasında ekran gider
#:  · `unpair`      cihaz token'ını siler; kasa eşleme ekranına düşer ve sahada
#:                  yeni kod girilene kadar HİÇ sipariş göremez
#:
#: `clear_queue` K-22 §2'de açıkça "yıkıcı" diye işaretlenmedi; buraya BİLEREK
#: eklendi. Yaptığı iş `clear_failed`in üst kümesidir (hatalı + bekleyen) ve
#: dışarıda bırakmak, yalnız `bld_kds.manage` taşıyan birinin kapıdan geçen
#: `clear_failed`ten DAHA fazlasını yapabilmesi olurdu. Bu ayrım Kontrol
#: Merkezi'nin kendi izin politikasıdır; BLD tarafında karşılığı yoktur.
#:
#: Küme SABİTTİR — ayardan okunsaydı, ayarı değiştirebilen biri yıkıcı komut
#: kapısını kendine açardı.
DESTRUCTIVE_COMMANDS = frozenset({RESTART, CLEAR_FAILED, CLEAR_QUEUE, UPDATE, UNPAIR})

#: Yük İSTEYEN tek komut. Ötekilerin hepsi yüksüzdür ve boş yük gönderilmez.
PAYLOAD_COMMANDS = frozenset({REPRINT})

#: `reprint` fiş türleri. Kasa bu üç şablonu tanıyor.
REPRINT_TYPES = ("mutfak", "musteri", "kurye")

#: Teslim edilmiş ama sonucu gelmemiş komut bu süre sonra "takıldı" sayılır.
#: `KitchenCommand::STALE_AFTER_MINUTES` ile aynı; yalnız EKRANDAKİ etiketi
#: belirler, sunucudaki kaydı etkilemez.
STALE_AFTER_MINUTES = 10

# ============================================================ sipariş durumları

#: `OrderStatusTransition::CODES` — admin panelindeki sırayla.
STATUS_CODES = ("yeni", "onaylandi", "hazirlaniyor", "hazir", "yolda",
                "teslim_edildi", "iptal")

STATUS_LABELS = {
    "yeni": "Yeni",
    "onaylandi": "Onaylandı",
    "hazirlaniyor": "Hazırlanıyor",
    "hazir": "Hazır",
    "yolda": "Yolda",
    "teslim_edildi": "Teslim edildi",
    "iptal": "İptal",
}

STATUS_TONES = {
    "yeni": "warn",
    "onaylandi": "info",
    "hazirlaniyor": "info",
    "hazir": "good",
    "yolda": "info",
    "teslim_edildi": "dim",
    "iptal": "bad",
}

#: `OrderStatusTransition::MATRIX` ile BİREBİR. Burada tekrar edilmesi bir
#: KOPYA DEĞİL, bir ÖN DENETİMDİR: kararın sahibi sunucudur ve reddi o verir.
#: Amaç, imkânsız bir geçişi kullanıcıya ağ turu atmadan ve "500" yerine
#: anlaşılır bir cümleyle söylemek. Matris sunucuda değişirse buradaki ön
#: denetim gevşek kalır — sunucu yine reddeder, ekran yalnız geç uyarır.
STATUS_NEXT: dict[str, tuple[str, ...]] = {
    "yeni": ("onaylandi", "iptal"),
    "onaylandi": ("hazirlaniyor", "iptal"),
    "hazirlaniyor": ("hazir", "iptal"),
    "hazir": ("yolda", "teslim_edildi", "iptal"),
    "yolda": ("teslim_edildi", "iptal"),
    "teslim_edildi": (),
    "iptal": (),
}

# ============================================================ yönetilen ayarlar


#: KASADA TEK TEK KAPATILABİLEN SESLİ UYARILAR (`KdsSoundEvent`, K-22 §1).
#: Adlar kasadaki enum adlarıyla BİREBİR aynıdır ve camelCase'tir — burada
#: snake_case'e çevirmek, telde hiçbir şeye denk gelmeyen bir ad üretirdi.
#:
#: `connectionLost` LİSTEDE YOKTUR ve olamaz (`canBeDisabled == false`):
#: sunucuyla bağlantının koptuğunu duyuran alarm kapatılabilseydi, mutfak
#: siparişlerin akmadığını hiç fark etmezdi. Sunucu listede görürse SESSİZCE
#: eler; Kontrol Merkezi ise REDDEDER ve nedenini yazar (aşağıya bakınız).
SOUND_EVENTS: tuple[tuple[str, str], ...] = (
    ("newOrder", "Yeni sipariş"),
    ("lateOrder", "Geciken sipariş"),
    ("printerError", "Yazıcı hatası"),
    ("subscriptionReminder", "Abonelik hatırlatması"),
    ("bbdOrder", "BBD siparişi"),
)

SOUND_EVENT_KEYS = tuple(key for key, _ in SOUND_EVENTS)
SOUND_EVENT_LABELS = dict(SOUND_EVENTS)

#: Kapatılamayan olay. Ayrı sabit, çünkü hata metni onu ADIYLA anmak zorunda.
UNDISABLEABLE_SOUND_EVENT = "connectionLost"

#: Olay listesini taşıyan ayarın anahtarı.
SOUND_EVENTS_KEY = "disabled_sound_events"


@dataclass(frozen=True, slots=True)
class Setting:
    """Bir yönetilen ayarın sözleşmesi. Panel formu bunu okuyarak çizilir."""

    key: str
    kind: str            # int | bool | text | events
    label: str
    group: str           # calisma | yazici | ses | kilit
    low: int | None = None
    high: int | None = None
    max_length: int = 0
    note: str = ""


#: 24 ANAHTAR — K-21 §2.2 (23) + K-22 §1 (`disabled_sound_events`). İlk 16'sı
#: `KitchenDeviceSettings::forDevice` çıktısıyla, sınırları `normalize()` ile
#: birebir aynı. 7'si kilit politikasıdır ve HEPSİ nullable: `null` "yönetici
#: dokunmadı" demek, yani SERBEST — alanların eklenmesi sahadaki kasaları
#: kilitlemez. Kilit ancak yönetici açıkça `false` yazınca doğar.
SETTINGS: tuple[Setting, ...] = (
    # --- çalışma ---------------------------------------------------------
    Setting("poll_seconds", "int", "Yoklama aralığı (sn)", "calisma", 3, 60,
            note="Alt sınır 3: 2 saniyelik yoklama saatte 1800 istek eder ve "
                 "kasa 429 alıp sipariş göremez hâle gelir."),
    Setting("warning_after_minutes", "int", "Uyarı eşiği (dk)", "calisma", 1, 480),
    Setting("late_after_minutes", "int", "Gecikme eşiği (dk)", "calisma", 1, 480,
            note="Uyarı eşiğinden küçük olamaz; sunucu küçükse uyarı eşiğine çeker."),
    Setting("touch_mode", "bool", "Dokunmatik kip", "calisma"),
    # --- yazıcı ----------------------------------------------------------
    Setting("printer_device_path", "text", "Yazıcı aygıt yolu", "yazici", max_length=255,
            note="Makineye özgüdür; yanlış değer fiş basımını tümüyle durdurur."),
    Setting("printer_code_page", "int", "Yazıcı kod sayfası", "yazici", 0, 255,
            note="Sahada doğrulanan değer 29; yanlışı bütün Türkçe harfleri boşluk bastırır."),
    # --- ses ve alarm ----------------------------------------------------
    Setting("sound_enabled", "bool", "Ses açık", "ses"),
    Setting("volume_percent", "int", "Ses seviyesi (%)", "ses", 0, 100,
            note="Hoparlörün kendi seviyesinden ayrıdır; yalnız uygulamanın akışını kısar."),
    Setting("audio_sink", "text", "Ses çıkışı", "ses", max_length=255,
            note="BOŞ DİZE burada 'varsayılan çıkışa dön' demektir ve korunur; "
                 "diğer alanlarda boş dize 'dokunulmadı' ile aynı anlama gelir."),
    Setting("tts_enabled", "bool", "Sesli anons", "ses"),
    Setting("tts_rate_percent", "int", "Anons hızı (%)", "ses", 50, 200),
    Setting("alarm_repeat_seconds", "int", "Alarm tekrar aralığı (sn)", "ses", 0, 120,
            note="0 = aralıksız."),
    Setting("alarm_max_repeats", "int", "En çok alarm tekrarı", "ses", 0, 65_535,
            note="0 = sınırsız: onaylanana kadar susmaz."),
    Setting("alarm_silenceable", "bool", "Alarm susturulabilir", "ses"),
    Setting(SOUND_EVENTS_KEY, "events", "Kapatılan sesli uyarılar", "ses",
            max_length=160,
            note="Virgülle ayrılmış olay adları. BOŞ DİZE burada 'hiçbiri kapalı "
                 "olmasın' demektir ve korunur; `null` ise 'yönetici dokunmadı' "
                 "ve kasa kendi listesini saklar. Bağlantı kopması alarmı "
                 "(connectionLost) kapatılamaz."),
    Setting("connection_alarm_seconds", "int", "Bağlantı alarmı (sn)", "ses", 10, 600),
    Setting("health_seconds", "int", "Sağlık bildirimi aralığı (sn)", "ses", 10, 300,
            note="Komutlar sağlık yanıtıyla taşınır; bu değer komutun kaç saniyede "
                 "vardığını da belirler."),
    # --- kilit politikası (YENİ 7) ---------------------------------------
    Setting("allow_settings", "bool", "Kasada ayarlar ekranı", "kilit"),
    Setting("allow_server_change", "bool", "Sunucu adresi / eşleme sıfırlama", "kilit"),
    Setting("allow_window_controls", "bool", "Pencere düğmeleri", "kilit"),
    Setting("allow_order_edit", "bool", "Kasadan sipariş düzenleme", "kilit"),
    Setting("allow_manual_reprint", "bool", "Kasadan elle yeniden bas", "kilit"),
    Setting("allow_sales_control", "bool", "Satış şalteri ve 'bugün tükendi'", "kilit"),
    Setting("lock_message", "text", "Kilit mesajı", "kilit", max_length=160,
            note="Kilitli düğmeye basınca gösterilir. Düğme GİZLENMEZ, pasifleşir: "
                 "gizlemek personeli 'bozuldu' sanısına iter."),
)

SETTING_BY_KEY: dict[str, Setting] = {item.key: item for item in SETTINGS}

#: Kilit politikası anahtarları. Panel bunları ayrı bir bölümde ve ayrı bir
#: uyarıyla gösterir: bir kilidi `false` yapmak kasadaki bir düğmeyi öldürür.
LOCK_KEYS = tuple(item.key for item in SETTINGS if item.group == "kilit")

#: BOŞ DİZENİN ANLAM TAŞIDIĞI İKİ ALAN. Geri kalan her alanda boş dize `None`
#: ile eşdeğerdir ("yönetici dokunmadı").
#:
#:  · `audio_sink`            → "varsayılan çıkışa dön"
#:  · `disabled_sound_events` → "hiçbir ses kapalı olmasın", yani HEPSİNİ AÇ
#:
#: İkincisi K-22 §1'in açık talebi: `null` zaten "dokunmadım"a ayrılmış durumda
#: ve "kasadaki bütün susturmaları kaldır" demenin başka yolu kalmıyordu.
EMPTY_MEANS_VALUE = frozenset({"audio_sink", SOUND_EVENTS_KEY})

SETTING_GROUP_LABELS = {
    "calisma": "Çalışma",
    "yazici": "Yazıcı",
    "ses": "Ses ve alarm",
    "kilit": "Kilit politikası",
}


# ================================================================== yardımcı

def text(value: Any) -> str:
    """Değeri kırpılmış metne çevirir. `None` boş dizedir."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    """Tamsayıya çevirir; çevrilemiyorsa `default`. İstisna atmaz."""
    if isinstance(value, bool):
        return int(value)
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    """ÜÇ DURUMLU. `None` "bilinmiyor / dokunulmadı" demektir ve KORUNUR.

    `bool(None)` yazmak burada en pahalı hatalardan biri olurdu: "sağlık
    bildirmemiş cihaz" arızalı, "yönetici dokunmamış kilit" kapalı sayılırdı.
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


def sound_events(value: Any) -> list[str]:
    """Virgüllü olay dizesini KANONİK listeye çevirir — okuma yönü, HOŞGÖRÜLÜ.

    Sıra `SOUND_EVENTS` sırasıdır ve tekrarlar düşer. Kanonikleştirmenin
    sebebi görsel değil: sunucudan `lateOrder,newOrder` gelip panelden
    `newOrder,lateOrder` gitseydi `settings_diff` olmayan bir değişiklik
    görür, `settings_updated_at` boş yere ileri atılırdı.

    TANIMADIĞI ADI SESSİZCE ELER. Burası OKUMA yönüdür: sunucuda bir gün yeni
    bir olay adı belirirse ekran onu çizemez ama ÇÖKMEMELİ. Yazma yönündeki
    hoşgörüsüzlük `sound_events_error` içindedir.
    """
    raw = value if isinstance(value, str) else text(value)
    parcalar = [item.strip() for item in raw.split(",")]
    secili = {item for item in parcalar if item}
    return [key for key in SOUND_EVENT_KEYS if key in secili]


def sound_events_text(value: Any) -> str:
    """Kanonik listeyi tele giden dizeye çevirir. Boş liste → boş dize."""
    return ",".join(sound_events(value))


def sound_events_error(value: Any) -> str:
    """Yazılmak istenen olay listesi kabul edilebilir mi.

    SESSİZCE ELEMİYORUZ. Sunucu (K-22 §1) tanımadığı ve kapatılamayan adları
    sessizce eliyor ve bu ORADA doğru: yöneticinin yazım hatası mutfağı sessiz
    bırakmamalı. Kontrol Merkezi'nde ise aynı yazım hatası REDDEDİLİR, çünkü
    burada hata daha erken ve daha ucuz söylenir — sessizce elenen bir ad,
    "beş sesi de kapattım" sanan yöneticiye hiçbir şey söylemezdi. Bu,
    `clean_settings`in aralık dışını kırpmak yerine reddetmesiyle aynı karar.
    """
    ham = value if isinstance(value, str) else text(value)
    hatali: list[str] = []
    for item in (parca.strip() for parca in ham.split(",")):
        if not item or item in SOUND_EVENT_KEYS:
            continue
        if item == UNDISABLEABLE_SOUND_EVENT:
            return (f"'{UNDISABLEABLE_SOUND_EVENT}' kapatılamaz: sunucuya "
                    "ulaşılamadığını duyuran alarm susturulabilseydi mutfak "
                    "siparişlerin akmadığını hiç fark etmezdi.")
        hatali.append(item)
    if hatali:
        taninan = ", ".join(SOUND_EVENT_KEYS)
        return (f"Tanınmayan sesli uyarı adı: {', '.join(sorted(set(hatali)))}. "
                f"Tanınanlar: {taninan}.")
    return ""


def reason_error(value: str, *, max_length: int = MAX_REASON) -> str:
    """Gerekçe kabul edilebilir mi — değilse kullanıcıya gösterilecek metin."""
    clean = text(value)
    if len(clean) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı; "
                "denetim kaydına bu metin yazılır.")
    if len(clean) > max_length:
        return f"Gerekçe en çok {max_length} karakter olabilir."
    return ""


def now_iso() -> str:
    """Yerel iz için zaman damgası. UZAK VERİ DAMGALANMAZ — o sunucudan gelir."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso(value: Any) -> datetime | None:
    """ISO-8601 Zulu damgasını okur. Okunamazsa `None` — istisna atmaz.

    Sunucu damgaları `...Z` ile bitiyor; `fromisoformat` Python 3.11'den önce
    bunu tanımıyordu, `+00:00`a çevirmek her iki durumu da kapsar.
    """
    raw = text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def minutes_since(value: Any, *, now: datetime | None = None) -> float | None:
    """Damganın üzerinden kaç dakika geçti. Damga okunamazsa `None`."""
    stamp = parse_iso(value)
    if stamp is None:
        return None
    moment = now or datetime.now(UTC)
    return (moment - stamp).total_seconds() / 60.0


# =================================================================== komutlar

def is_destructive(command: str) -> bool:
    """Bu komut ayrı izin (`bld_kds.devices`) ister mi?"""
    return text(command).lower() in DESTRUCTIVE_COMMANDS


def command_error(command: str, payload: dict[str, Any] | None = None) -> str:
    """Komut ve yükü kabul edilebilir mi — değilse gösterilecek metin.

    BİLİNMEYEN KOMUT REDDEDİLİR. Kuyruğa atılıp kasada sessizce yok sayılmak,
    yöneticiye "komut gitti" demek ama hiçbir şey olmamak demektir.
    """
    name = text(command).lower()
    if not name:
        return "Komut seçilmedi."
    if name not in COMMANDS:
        taninan = ", ".join(COMMANDS)
        return f"'{command}' tanınan bir komut değil. Tanınanlar: {taninan}."
    if name != REPRINT:
        return ""

    body = payload or {}
    order_id = as_int(body.get("order_id"), 0)
    if order_id <= 0:
        return "Yeniden basım için sipariş kimliği gerekli."
    fis = text(body.get("type")).lower()
    if fis not in REPRINT_TYPES:
        return f"Fiş türü {' / '.join(REPRINT_TYPES)} olmalı."
    return ""


def command_payload(command: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Uca gidecek TEMİZ yük. Tanınmayan anahtar taşınmaz.

    Kullanıcının gövdesini olduğu gibi geçirmek, ekranın bilmediği bir alanı
    sunucuya taşımak olurdu; sözleşmede olmayan alan eklenmez (K-21 girişi).
    """
    name = text(command).lower()
    if name != REPRINT:
        return {}
    body = payload or {}
    return {"order_id": as_int(body.get("order_id"), 0),
            "type": text(body.get("type")).lower()}


def command_row(raw: dict[str, Any], *, now: datetime | None = None,
                stale_after: int = STALE_AFTER_MINUTES) -> dict[str, Any]:
    """Komut kaydının ekranda görünen hâli — ÜÇ DAMGA, DÖRT DURUM.

    `succeeded` üç durumludur: `True` başarılı, `False` başarısız, `None`
    "sonuç gelmedi". Sonucu gelmemiş komutu başarısız saymak, kasa komutu alıp
    çalıştırmışken yöneticiye "olmadı" dedirtir ve komut ikinci kez gönderilir —
    `restart` için bu ikinci bir kesinti demektir.
    """
    delivered = text(raw.get("delivered_at"))
    executed = text(raw.get("executed_at"))
    succeeded = as_bool(raw.get("succeeded"))
    gecen = minutes_since(delivered, now=now)

    if executed:
        state = "ok" if succeeded else ("hata" if succeeded is False else "sonucsuz")
    elif delivered:
        state = "takildi" if (gecen is not None and gecen >= stale_after) else "yolda"
    else:
        state = "kuyrukta"

    return {
        "id": as_int(raw.get("id")),
        "command": text(raw.get("command")),
        "command_label": COMMAND_LABELS.get(text(raw.get("command")), text(raw.get("command"))),
        "payload": raw.get("payload") if isinstance(raw.get("payload"), dict) else {},
        "created_at": text(raw.get("created_at")) or None,
        "delivered_at": delivered or None,
        "executed_at": executed or None,
        "succeeded": succeeded,
        "result": text(raw.get("result")) or None,
        "destructive": is_destructive(raw.get("command", "")),
        "state": state,
        "state_label": {
            "kuyrukta": "Kuyrukta", "yolda": "Yolda",
            "takildi": f"Takıldı ({stale_after} dk'dır sonuç yok)",
            "ok": "Tamam", "hata": "Hata", "sonucsuz": "Sonuç bildirilmedi",
        }[state],
    }


# ==================================================================== cihaz

def settings_view(raw: Any) -> dict[str, Any]:
    """Uzak ayar sözlüğünü 24 ANAHTARA sabitler; eksikler `None` olur.

    Panel her zaman 24 alan çizer. Eksik anahtarı hiç göstermemek, sunucuya
    yeni bir alan eklendiğinde ekranın onu sessizce gizlemesi olurdu; `None`
    göstermek "yönetici dokunmadı" demektir ve doğrudur.
    """
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    for item in SETTINGS:
        value = source.get(item.key)
        if item.kind == "bool":
            out[item.key] = as_bool(value)
        elif item.kind == "int":
            out[item.key] = None if value is None else as_int(value)
        elif item.kind == "events":
            # `None` ile boş dize AYRI: ilki "dokunulmadı", ikincisi "hiçbiri
            # kapalı değil". Dolu değer kanonik sıraya çekilir ki panelden
            # aynı liste geri geldiğinde sahte bir fark doğmasın.
            out[item.key] = None if value is None else sound_events_text(value)
        else:
            out[item.key] = None if value is None else text(value)
    return out


def settings_spec() -> list[dict[str, Any]]:
    """Panelin ayar formunu çizmek için okuduğu sözleşme.

    Sınırlar TEK YERDE durur. Panelde ayrıca yazılsalardı, `poll_seconds`
    alt sınırı 3'ten değiştiğinde iki dosyanın ikisi de düzeltilmek zorunda
    kalırdı ve unutulan taraf sessizce yanlış sınır gösterirdi.
    """
    return [{"key": item.key, "kind": item.kind, "label": item.label,
             "group": item.group, "group_label": SETTING_GROUP_LABELS[item.group],
             "low": item.low, "high": item.high,
             "max_length": item.max_length or None, "note": item.note,
             "lock": item.group == "kilit",
             # Yalnız `events` alanı için dolu: panel beş onay kutusunu bu
             # listeden çizer ve adları kendi elinde tutmaz — kasadaki enum
             # adları burada tek yerde durur.
             "options": [{"key": key, "label": label} for key, label in SOUND_EVENTS]
             if item.kind == "events" else []}
            for item in SETTINGS]


def device_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Cihaz kaydının ekranda görünen hâli.

    `online` SUNUCUDAN GELİR, burada yeniden hesaplanmaz: eşik sunucunun
    (`KitchenDevice::ONLINE_THRESHOLD_MINUTES`), saat de sunucunun. Kontrol
    Merkezi'nin saati üç dakika kaysa bütün mutfak "çevrimdışı" görünürdü.
    """
    health = raw.get("health") if isinstance(raw.get("health"), dict) else {}
    pairing = raw.get("pairing") if isinstance(raw.get("pairing"), dict) else {}
    revoked_at = text(raw.get("revoked_at"))
    online = bool(raw.get("online")) and not revoked_at
    printer_ok = as_bool(health.get("printer_ok"))
    # ZENGİNLEŞTİRİLMİŞ TELEMETRİ (K-22 §3) — hepsi OPSİYONEL. Eski kasalar bu
    # alanları hiç göndermiyor ve sunucu `null` yazıyor; üç durumlu okumak
    # şart, `bool(None)` yazmak "ses altsistemi bozuk" ve "alarm susturulmuş"
    # gibi olmayan arızalar uydururdu.
    alarm_muted = as_bool(health.get("alarm_muted"))
    sound_ok = as_bool(health.get("sound_ok"))
    settings = settings_view(raw.get("settings"))

    state = "revoked" if revoked_at else ("online" if online else "offline")
    return {
        "id": as_int(raw.get("id")),
        "name": text(raw.get("name")),
        "online": online,
        "revoked": bool(revoked_at),
        "revoked_at": revoked_at or None,
        "last_seen_at": text(raw.get("last_seen_at")) or None,
        "created_at": text(raw.get("created_at")) or None,
        "state": state,
        "state_label": {"online": "Çevrimiçi", "offline": "Çevrimdışı",
                        "revoked": "İptal edildi"}[state],
        "tone": {"online": "good", "offline": "warn", "revoked": "dim"}[state],
        "pairing": {
            "code": text(pairing.get("code")) or None,
            "expires_at": text(pairing.get("expires_at")) or None,
            # Kod YALNIZ kullanılabilirken listede döner; `usable` yoksa kod da
            # yoktur ve ekran "kod üret" der.
            "usable": bool(pairing.get("usable")) and bool(text(pairing.get("code"))),
        },
        "health": {
            "reported_at": text(health.get("reported_at")) or None,
            # ÜÇ DURUMLU. Hiç sağlık bildirmemiş cihaz ARIZALI DEĞİLDİR,
            # bilinmiyordur; `False` yazmak mutfağı olmayan bir yazıcı arızası
            # için ayağa kaldırırdı.
            "printer_ok": printer_ok,
            "print_queue_pending": as_int(health.get("print_queue_pending")),
            "print_queue_failed": as_int(health.get("print_queue_failed")),
            "app_version": text(health.get("app_version")) or None,
            "reported": bool(text(health.get("reported_at"))),
            # --- K-22 §3: salt okunur telemetri --------------------------
            # "Kuyrukta 3 iş var" ile "en eskisi 40 dakikadır bekliyor"
            # arasındaki fark, sahaya gitme kararını değiştirir. Sayı tek
            # başına o kararı verdirmiyordu.
            "queue_oldest_at": text(health.get("queue_oldest_at")) or None,
            "last_error": text(health.get("last_error")) or None,
            "alarm_muted": alarm_muted,
            "alarm_mute_reason": text(health.get("alarm_mute_reason")) or None,
            "sound_ok": sound_ok,
        },
        "printer_fault": printer_ok is False,
        # Yazıcı arızasının SES karşılığı. Ayrı bir bayrak, çünkü sesi olmayan
        # bir mutfak siparişin geldiğini duymaz ve ekrana bakmıyorsa hiç
        # görmez — yazıcı arızası kadar sahaya çıkartan bir durumdur.
        "sound_fault": sound_ok is False,
        # Susturulmuş alarm arıza DEĞİLDİR (personel susturmuş olabilir) ama
        # görünmek zorundadır: susturulduğu unutulan bir alarm, çalıştığı
        # sanılan bir alarmdır.
        "alarm_muted": alarm_muted is True,
        "settings": settings,
        "settings_updated_at": text(raw.get("settings_updated_at")) or None,
        "pending_command_count": as_int(raw.get("pending_command_count")),
        # Kasadaki hangi düğmelerin KAPATILDIĞI (yalnız açıkça `False` olanlar).
        # `settings` içinde zaten var; ayrıca verilmesinin nedeni panelin cihaz
        # satırında "2 düğme kilitli" rozetini 23 alanı taramadan çizebilmesi.
        # `None` taşıyan anahtar buraya GİRMEZ: dokunulmamış kilit, kilit
        # değildir.
        "locked": [key for key in LOCK_KEYS
                   if key != "lock_message" and settings.get(key) is False],
        "lock_message": settings.get("lock_message"),
    }


def device_error(name: str) -> str:
    """Cihaz adı kabul edilebilir mi."""
    clean = text(name)
    if len(clean) < 2:
        return "Cihaz adı en az 2 karakter olmalı."
    if len(clean) > 64:
        return "Cihaz adı en çok 64 karakter olabilir."
    return ""


# ============================================================== ayar yazımı

def clean_settings(values: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Kısmi ayar gövdesini denetler. `(temiz, hatalar)` döner.

    ARALIK DIŞI DEĞER REDDEDİLİR, KIRPILMAZ. Sunucu (`normalize`) kırpıyor:
    `poll_seconds: 2` gönderilirse 3 yazılır ve yanıt "tamam" der. Yönetici
    yazdığı değerin tutmadığını ancak ekranı yenileyip dikkatle bakarsa görür.
    Burada reddetmek, ona ne yazabileceğini SÖYLER.

    TANINMAYAN ANAHTAR DA REDDEDİLİR: sessizce atılan bir anahtar, panelde
    yapılan yazım hatasını "kaydedildi" gibi gösterirdi.
    """
    source = values if isinstance(values, dict) else {}
    clean: dict[str, Any] = {}
    errors: list[str] = []

    for key, value in source.items():
        spec = SETTING_BY_KEY.get(key)
        if spec is None:
            errors.append(f"'{key}' yönetilen bir ayar değil.")
            continue

        # `None` HER ALANDA geçerlidir: "yöneticinin dokunmadığı" hâle dönmenin
        # ve bir kilidi kaldırmanın tek yolu budur.
        if value is None:
            clean[key] = None
            continue

        if spec.kind == "bool":
            parsed = as_bool(value)
            if parsed is None:
                errors.append(f"{spec.label}: evet/hayır bekleniyor.")
                continue
            clean[key] = parsed
            continue

        if spec.kind == "int":
            if text(value) == "":
                clean[key] = None
                continue
            number = as_int(value, default=-(10**9))
            if number == -(10**9):
                errors.append(f"{spec.label}: sayı bekleniyor.")
                continue
            low = spec.low if spec.low is not None else number
            high = spec.high if spec.high is not None else number
            if number < low or number > high:
                errors.append(f"{spec.label}: {spec.low}-{spec.high} arasında olmalı "
                              f"(verilen: {number}).")
                continue
            clean[key] = number
            continue

        if spec.kind == "events":
            problem = sound_events_error(value)
            if problem:
                errors.append(f"{spec.label}: {problem}")
                continue
            # Boş dize KORUNUR: "hiçbiri kapalı olmasın" demektir ve `None`
            # ("dokunulmadı") ile aynı şey değildir.
            clean[key] = sound_events_text(value)
            continue

        written = text(value)
        if written == "" and key not in EMPTY_MEANS_VALUE:
            # Boş dize "dokunulmadı" ile aynı anlama gelir — `audio_sink` hariç,
            # orada "varsayılan çıkışa dön" demektir ve KORUNUR.
            clean[key] = None
            continue
        if spec.max_length and len(written) > spec.max_length:
            errors.append(f"{spec.label}: en çok {spec.max_length} karakter.")
            continue
        clean[key] = written

    return clean, errors


def settings_diff(current: dict[str, Any], proposed: dict[str, Any]) -> list[dict[str, Any]]:
    """Gerçekten DEĞİŞEN alanlar. Onay ekranı ve kuru prova bunu gösterir.

    Aynı değeri yeniden yazmak zararsız görünür ama `settings_updated_at`
    damgasını ileri atar; "bu cihazın ayarına en son ne zaman dokunuldu"
    sorusunun cevabı bozulur.
    """
    out: list[dict[str, Any]] = []
    for key, value in proposed.items():
        spec = SETTING_BY_KEY.get(key)
        if spec is None:
            continue
        before = current.get(key)
        if before == value:
            continue
        out.append({"key": key, "label": spec.label, "group": spec.group,
                    "lock": spec.group == "kilit", "from": before, "to": value})
    return out


def threshold_warning(current: dict[str, Any], proposed: dict[str, Any]) -> str:
    """Sunucunun SESSİZCE düzelteceği tek durum: gecikme < uyarı eşiği.

    `KitchenDeviceSettings::update` bu durumda `late_after_minutes` değerini
    uyarı eşiğine ÇEKER ve bunu kimseye söylemez. Kısmi yazmada tuzak büyür:
    yalnız uyarı eşiğini yükselten yönetici, gecikme eşiğinin de değiştiğini
    hiç görmez. Engellemiyoruz — geçerli bir istek — ama önizlemede YAZIYORUZ.
    """
    birlesik = {**current, **proposed}
    uyari = birlesik.get("warning_after_minutes")
    geciken = birlesik.get("late_after_minutes")
    if not isinstance(uyari, int) or not isinstance(geciken, int):
        return ""
    if geciken >= uyari:
        return ""
    return (f"Gecikme eşiği ({geciken} dk) uyarı eşiğinden ({uyari} dk) küçük; "
            f"sunucu gecikme eşiğini {uyari} dakikaya çekecek.")


# ================================================================ siparişler

def order_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Sipariş satırı — YALNIZ ETİKET EKLER, hiçbir alanı ayıklamaz.

    Sözleşme siparişin alanlarını saymıyor: gövde `OrderPresenter::editable`
    çıktısıdır ve sunucu tarafı büyüyor (`option_value_ids` eklenmesi gibi).
    Bilinen alanları seçip gerisini atan bir dönüşüm, sunucuya eklenen her yeni
    alanı SESSİZCE düşürürdü — ve düşen alan `option_value_ids` olsaydı,
    revizyon "ekstra peynir"i siler, sipariş ucuzlar, kimse fark etmezdi.
    Bu yüzden satır olduğu gibi geçer; üzerine yalnız ekranın kendi etiketleri
    eklenir.
    """
    status = text(raw.get("status"))
    return {
        **raw,
        "status_label": STATUS_LABELS.get(status, status or "—"),
        "status_tone": STATUS_TONES.get(status, "dim"),
        "next_statuses": [
            {"code": code, "label": STATUS_LABELS.get(code, code)}
            for code in STATUS_NEXT.get(status, ())
        ],
    }


def status_error(current: str, target: str) -> str:
    """Geçiş matriste var mı — ÖN DENETİM, karar sunucunundur.

    Sunucu `OrderStatusTransition` ile aynı denetimi yapıyor ve son sözü o
    söylüyor. Buradaki denetimin işi, imkânsız geçişi kullanıcıya ağ turu
    atmadan ve anlaşılır bir cümleyle söylemek.
    """
    hedef = text(target).lower()
    if hedef not in STATUS_CODES:
        return f"'{target}' tanınan bir sipariş durumu değil."
    simdi = text(current).lower()
    if simdi not in STATUS_NEXT:
        # Durumu okunamayan siparişi burada engellemek, sunucunun kabul
        # edeceği bir geçişi ekranın reddetmesi olurdu. Karar sunucuya kalır.
        return ""
    if hedef == simdi:
        return f"Sipariş zaten '{STATUS_LABELS.get(simdi, simdi)}' durumunda."
    izinli = STATUS_NEXT[simdi]
    if not izinli:
        return (f"'{STATUS_LABELS.get(simdi, simdi)}' son durumdur; "
                "buradan başka bir duruma geçilemez.")
    if hedef not in izinli:
        adlar = ", ".join(STATUS_LABELS.get(code, code) for code in izinli)
        return (f"'{STATUS_LABELS.get(simdi, simdi)}' durumundan yalnız şunlara "
                f"geçilebilir: {adlar}.")
    return ""


def revision_items(items: Any) -> tuple[list[dict[str, Any]], str]:
    """Revizyon kalemlerini DENETLER, ayıklamaz. `(kalemler, hata)` döner.

    TAM LİSTE GÖNDERİLİR, KALEM FARKI DEĞİL. Boş liste reddedilir: sunucu
    (`OrderEditor`) "En az bir kalem olmalı" diyor ama asıl tehlike buraya
    gelmeden önce — panelin "değişmeyen kalemleri göndermeye gerek yok" diye
    yalnız değişenleri yollaması siparişi BOŞALTIRDI, çünkü gönderilen liste
    siparişin YENİ hâlidir.

    KALEM OLDUĞU GİBİ GEÇER. Bilinen dört alanı seçip gerisini atmak,
    sunucuya eklenen her yeni alanı sessizce düşürürdü; `option_value_ids`
    tam da böyle eklenen bir alandır ve düşerse "ekstra peynir" silinir,
    sipariş ucuzlar, mutfak yanlış yemeği yapar ve hata hiçbir yerde
    görünmez. Denetim yalnız ZORUNLU alanların varlığını ve sınırlarını
    doğrular; tanımadığı alana dokunmaz.
    """
    if not isinstance(items, list) or not items:
        return [], ("Revizyon siparişin TAM kalem listesini ister; boş liste "
                    "siparişi boşaltır. Değişmeyen kalemler de gönderilmelidir.")

    clean: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return [], f"{index}. kalem okunamadı."
        if as_int(item.get("menu_id")) < 1:
            return [], f"{index}. kalemde ürün (menu_id) eksik."
        quantity = as_int(item.get("quantity"))
        if quantity < 1 or quantity > 999:
            return [], f"{index}. kalemin adedi 1-999 arasında olmalı."

        secenekler = item.get("option_value_ids")
        if secenekler is not None:
            if not isinstance(secenekler, list):
                return [], f"{index}. kalemin seçenek listesi okunamadı."
            if any(as_int(option) < 1 for option in secenekler):
                return [], f"{index}. kalemde geçersiz seçenek kimliği."

        if len(text(item.get("note"))) > 255:
            return [], f"{index}. kalemin notu en çok 255 karakter olabilir."

        clean.append(dict(item))

    return clean, ""


def revision_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Revizyon geçmişi satırı. Para KURUŞ gelir ve kuruş kalır — burada
    bölünmez; biçimlendirme panelin işidir ve float'a çevirmek kuruş kaybettirir."""
    return {
        "revision_no": as_int(raw.get("revision_no")),
        "reason": text(raw.get("reason")),
        "note": text(raw.get("note")) or None,
        "refund_kurus": as_int(raw.get("refund_kurus")),
        "extra_charge_kurus": as_int(raw.get("extra_charge_kurus")),
        "created_at": text(raw.get("created_at")) or None,
    }


def print_job_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Fiş kuyruğu DENETİM satırı.

    Bu tablo bir "bekleyen işler kuyruğu" DEĞİLDİR: KDS'in kendi disk kuyruğu
    sunucuda yok, bu yalnız BASILMIŞ fişlerin kaydıdır. "Bekleyen iş" sayısı
    cihaz sağlığından (`health.print_queue_pending`) okunur.
    """
    return {
        "id": as_int(raw.get("id")),
        "order_id": as_int(raw.get("order_id")),
        "order_number": text(raw.get("order_number")) or None,
        "type": text(raw.get("type")),
        "revision": as_int(raw.get("revision")),
        "printed_at": text(raw.get("printed_at")) or None,
        "device_id": as_int(raw.get("device_id")),
        "device_name": text(raw.get("device_name")) or None,
    }


def overview_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Özet yanıtını eksiksiz bir iskelete oturtur.

    Sunucu bir bölümü göndermezse panel `undefined` okumamalı; sayaçlar sıfır
    görünür ve ekran hangi bölümün gelmediğini `sections` ile söyler.
    """
    devices = raw.get("devices") if isinstance(raw.get("devices"), dict) else {}
    orders = raw.get("orders") if isinstance(raw.get("orders"), dict) else {}
    jobs = raw.get("print_jobs") if isinstance(raw.get("print_jobs"), dict) else {}
    by_status = orders.get("by_status") if isinstance(orders.get("by_status"), dict) else {}

    return {
        "devices": {
            "total": as_int(devices.get("total")),
            "online": as_int(devices.get("online")),
            "revoked": as_int(devices.get("revoked")),
            "printer_fault": as_int(devices.get("printer_fault")),
            "queue_pending": as_int(devices.get("queue_pending")),
            "queue_failed": as_int(devices.get("queue_failed")),
            # Çevrimdışı sayısı sunucudan gelmiyor; toplam ve çevrimiçiden
            # çıkarılır. İptal edilenler zaten çevrimiçi sayılmıyor.
            "offline": max(0, as_int(devices.get("total"))
                           - as_int(devices.get("online"))
                           - as_int(devices.get("revoked"))),
        },
        "orders": {
            "active": as_int(orders.get("active")),
            "today": as_int(orders.get("today")),
            "late": as_int(orders.get("late")),
            "by_status": [
                {"code": code, "label": STATUS_LABELS.get(code, code),
                 "tone": STATUS_TONES.get(code, "dim"),
                 "count": as_int(by_status.get(code))}
                for code in STATUS_CODES
            ],
        },
        "print_jobs": {"today": as_int(jobs.get("today"))},
        "server_time": text(raw.get("server_time")) or None,
        "sections": {
            "devices": isinstance(raw.get("devices"), dict),
            "orders": isinstance(raw.get("orders"), dict),
            "print_jobs": isinstance(raw.get("print_jobs"), dict),
        },
    }
