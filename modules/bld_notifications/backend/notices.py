"""Duyuru verisinin saf dönüşümleri — ağa çıkmaz, durum tutmaz, testin hedefi.

NEDEN AYRI DOSYA. Bu ekranın kararlarının neredeyse tamamı tek bir sözlüğe
bakıp "bu değer kabul edilir mi, ekranda ne yazar" sorusuna cevap vermek:
başlık/gövde sınırları, düğme çiftinin bütünlüğü, adresin şeması, kapatılamaz
duyurunun düzeyi, gösterim penceresinin sırası. Servise gömülselerdi tek satırı
bile ağ taklidi olmadan sınanamazdı; burada hepsi girdi→çıktı fonksiyonudur.

ALAN ADLARI snake_case KALIR (`docs/control/00-genel.md` §6). Tel üzerindeki
sözlük sözleşmede sabittir ve panel de onu görür; arada camelCase'e çevirmek
dördüncü bir sözlük yaratır ve yanlış çeviri SESSİZ kalır.

BEŞ TUZAK — her birinin karşılığı burada bir fonksiyondur:

 1. `live` SUNUCUDA hesaplanır          → `notice_row` onu OLDUĞU GİBİ taşır ve
    (`notifications.md` §Şema)             yeniden hesaplamaz. İstemcide
                                           hesaplansaydı saati kaymış bir panelde
                                           duyuru bir gün erken "bitmiş" görünürdü.
 2. `trackable: false` → `null`,        → `stats_view` `None` değerini KORUR.
    sıfır DEĞİL                            Sıfır "kimse görmedi", `null`
                                           "ölçülemiyor" demektir; ikisini
                                           karıştırmak çalışan bir duyuruyu
                                           başarısız gösterirdi.
 3. `action_url` üç istemcide açılıyor  → `action_url_error` yalnız `https://`
                                           ve uygulama-içi göreli yolu geçirir;
                                           `//host` (şema-göreli) de reddedilir,
                                           çünkü tarayıcıda MUTLAK adrestir.
 4. Kapatılamaz duyuru uygulamayı       → `dismissible_error`: `dismissible=False`
    kilitleyebilir                         yalnız `critical` ile.
 5. Doğduğu anda bitmiş duyuru          → `window_error`: geçmiş `ends_at`
                                           reddedilir; yöneticinin fark etmediği
                                           bir hatadır.

GÖVDE DÜZ METİNDİR, HTML DEĞİL (sözleşme §Şema). Duyuru üç istemcide birden
çiziliyor (Next.js, Flutter müşteri, ileride başkaları) ve HTML'i üçünde
tutarlı çizmek imkânsız. Satır sonu `\\n` desteklenir, biçimlendirme yok; bu
yüzden burada bir HTML temizleyicisi YOKTUR ve olmamalıdır — beyaz liste
tutmak, HTML'in kabul edildiğini söylemek olurdu.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

# ============================================================ gerekçe ve aktör

#: Gerekçenin en az uzunluğu (`00-genel.md` §3). Sunucu da 10 istiyor; burada
#: TEKRAR doğrulanır çünkü arayüzde alanı zorunlu göstermek yetkilendirme
#: değildir (K9) ve istemci gövdeyi elle kurabilir.
MIN_REASON = 10

#: Panel uçlarının üst sınırı (`00-genel.md` §3). Duyuru uçlarının hiçbiri
#: sipariş revizyonu değildir; 160'lık dar sınır buraya UYGULANMAZ.
MAX_REASON = 500

#: `actor` 2–120 karakter (`00-genel.md` §3). Aktör GÖVDEDEN ALINMAZ, oturumdan
#: gelir; buradaki sınır yalnız sunucuya gitmeden anlaşılır hata vermek içindir.
ACTOR_MAX = 120

# =================================================================== sınırlar

#: `notifications.md` §POST doğrulama tablosundan birebir.
TITLE_MIN = 2
TITLE_MAX = 160
BODY_MIN = 2
BODY_MAX = 2000
ACTION_LABEL_MAX = 60
ACTION_URL_MAX = 255

# =================================================================== sözlükler

#: Rozet düzeyi. Sıra ekrandaki sıradır: bilgi → uyarı → kritik.
LEVELS = ("info", "warning", "critical")

LEVEL_LABELS = {
    "info": "Bilgi",
    "warning": "Uyarı",
    "critical": "Kritik",
}

#: Rozet tonu. RENK TEK BAŞINA ANLAM TAŞIMAZ (kit kuralı 7): panel tonun yanına
#: her yerde etiketi de yazar.
LEVEL_TONES = {
    "info": "info",
    "warning": "warn",
    "critical": "bad",
}

#: `notifications.md` §"`audience` ne demek" tablosu.
AUDIENCES = ("all", "customers", "subscribers")

AUDIENCE_LABELS = {
    "all": "Herkes",
    "customers": "Giriş yapmış müşteriler",
    "subscribers": "Aktif aboneler",
}

AUDIENCE_HELP = {
    "all": "Giriş yapmamış ziyaretçi dâhil herkes görür. Kimlik olmadığı için "
           "OKUNMA KAYDI YAZILAMAZ: bu duyuru istatistik üretmez.",
    "customers": "Giriş yapmış müşteriler görür; görülme ve kapatılma sayılır.",
    "subscribers": "Yalnız aktif aboneliği olan müşteriler görür.",
}

#: Kitlesi `all` olan duyuru ÖLÇÜLEMEZ (sözleşme: `trackable: false`).
UNTRACKABLE_AUDIENCE = "all"

STATUSES = ("draft", "published", "archived")

STATUS_LABELS = {
    "draft": "Taslak",
    "published": "Yayında",
    "archived": "Arşiv",
}

STATUS_TONES = {
    "draft": "dim",
    "published": "good",
    "archived": "",
}

#: `PATCH /{id}` ile yazılabilen alanlar (sözleşme §PATCH). `status` BURADA
#: YOKTUR ve olmayacaktır: durumun kendi uçları var.
WRITABLE = ("title", "body", "level", "audience", "starts_at", "ends_at",
            "action_label", "action_url", "dismissible")

#: Kısmi yazmada BİRLİKTE gönderilmesi gereken alanlar. Sebep: bu üç kuralın
#: hepsi İKİ ALANIN BİRLİKTE hâli hakkındadır ve sözleşmede tek duyuru okuyan
#: bir uç yok — yalnız birini alan bir `PATCH`, kuralı yerelde doğrulanamaz
#: bırakır ve karar tek başına sunucuya kalırdı. Beraber istemenin bedeli
#: değişmemiş bir alanı da göndermek; kazancı, kuralın burada da denetlenmesi
#: (K9 — çift kapı).
COUPLED = (("starts_at", "ends_at"), ("action_label", "action_url"))


# ================================================================= yardımcılar

def text(value: Any) -> str:
    """Metne çevirir ve kırpar. `None` boş dizedir."""
    return "" if value is None else str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool | None:
    """Üç değerli okuma: `True` · `False` · "bilinmiyor" (`None`).

    `dismissible` alanı sunucudan her zaman geliyor ama bir yanıt eksik
    gelirse onu `False` saymak, kapatılabilir bir duyuruyu kapatılamaz
    göstermek olurdu.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    lowered = str(value).strip().casefold()
    if lowered in {"1", "true", "evet", "yes", "on"}:
        return True
    if lowered in {"0", "false", "hayir", "hayır", "no", "off"}:
        return False
    return None


def now_iso() -> str:
    """Yerel denetim izinin damgası — ISO 8601 UTC (`00-genel.md` §6)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: Any) -> datetime | None:
    """ISO 8601 anı okur. Okunamayan değer `None` döner, İSTİSNA FIRLATMAZ.

    `Z` soneki Python 3.10 öncesinde `fromisoformat` tarafından anlaşılmıyordu;
    3.11+ anlıyor ama sözleşme `Z` yazıyor ve dönüşümü elle yapmak, sürüm
    farkının sessizce başka bir dala düşmesini engeller.
    """
    raw = text(value)
    if not raw:
        return None
    if raw.endswith(("Z", "z")):
        raw = f"{raw[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def iso_of(moment: datetime | None) -> str:
    """Anı sözleşmenin biçimine getirir: ISO 8601, UTC, `Z` sonekli."""
    if moment is None:
        return ""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_between(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 3600.0


# ================================================================ doğrulamalar

def reason_error(value: str, *, max_length: int = MAX_REASON) -> str:
    reason = text(value)
    if len(reason) < MIN_REASON:
        return (f"Gerekçe en az {MIN_REASON} karakter olmalı: BLD'de yapılan her "
                "değişiklik gerekçesiyle birlikte kayda geçer (sözleşme §3).")
    if len(reason) > max_length:
        return (f"Gerekçe en çok {max_length} karakter olabilir; {len(reason)} "
                "karakter yazıldı. Sunucu taşan gerekçeyi kırpmaz, reddeder.")
    return ""


def actor_error(value: str) -> str:
    actor = text(value)
    if not actor:
        return ("İşlemi yapan kişinin adı boş: denetim izinde duyuruyu kimin "
                "yayınladığı yalnız bu alanla ayrılır (sözleşme §3).")
    if len(actor) > ACTOR_MAX:
        return f"Aktör adı en çok {ACTOR_MAX} karakter olabilir."
    return ""


def title_error(value: Any) -> str:
    title = text(value)
    if len(title) < TITLE_MIN:
        return f"Başlık en az {TITLE_MIN} karakter olmalı."
    if len(title) > TITLE_MAX:
        return (f"Başlık en çok {TITLE_MAX} karakter olabilir; {len(title)} "
                "karakter yazıldı.")
    return ""


def body_error(value: Any) -> str:
    body = text(value)
    if len(body) < BODY_MIN:
        return f"Gövde en az {BODY_MIN} karakter olmalı."
    if len(body) > BODY_MAX:
        return (f"Gövde en çok {BODY_MAX} karakter olabilir; {len(body)} "
                "karakter yazıldı.")
    return ""


def level_error(value: Any) -> str:
    level = text(value)
    if level not in LEVELS:
        return f"Düzey {' · '.join(LEVELS)} değerlerinden biri olmalı."
    return ""


def audience_error(value: Any) -> str:
    audience = text(value)
    if audience not in AUDIENCES:
        return f"Hedef kitle {' · '.join(AUDIENCES)} değerlerinden biri olmalı."
    return ""


def action_url_error(value: Any) -> str:
    """Duyuru düğmesinin adresi.

    YALNIZ `https://` VE UYGULAMA-İÇİ GÖRELİ YOL. Sözleşme `http://`,
    `javascript:` ve `data:` şemalarını açıkça reddediyor: duyuru üç istemcide
    birden açılıyor ve güvenilmeyen bir şema en az birinde çalıştırılabilir
    olurdu.

    ŞEMA-GÖRELİ ADRES (`//baska-site`) DE REDDEDİLİR ve bu sözleşmeye eklenmiş
    bir kapı değil, onun doğru okunmasıdır: tarayıcı `//host/yol` adresini
    GÖRELİ YOL DEĞİL, mutlak adres sayar. "`/` ile başlıyorsa uygulama içidir"
    kuralı bu tek durumda yanlış cevap verirdi.
    """
    url = text(value)
    if not url:
        return ""
    if len(url) > ACTION_URL_MAX:
        return f"Düğme adresi en çok {ACTION_URL_MAX} karakter olabilir."
    if any(char.isspace() for char in url):
        return "Düğme adresinde boşluk olamaz."
    if url.startswith("//"):
        return ("Düğme adresi `//` ile başlayamaz: bu bir uygulama-içi yol değil, "
                "tarayıcının başka bir siteye götürdüğü mutlak adrestir.")
    if url.startswith("/"):
        return ""
    if url.startswith("https://"):
        return ""
    return ("Düğme adresi `https://` ile ya da uygulama-içi göreli yol olarak "
            "`/` ile başlamalı. `http://`, `javascript:` ve `data:` kabul "
            "edilmez — duyuru üç istemcide birden açılıyor.")


def action_pair_error(label: Any, url: Any) -> str:
    """Etiket ve adres BİRLİKTE var ya da BİRLİKTE yok."""
    has_label = bool(text(label))
    has_url = bool(text(url))
    if has_label != has_url:
        return ("Duyuru düğmesi için etiket ve adres birlikte verilir: etiketsiz "
                "bir düğme çizilemez, adressiz bir etiket tıklanamaz.")
    if has_label and len(text(label)) > ACTION_LABEL_MAX:
        return f"Düğme etiketi en çok {ACTION_LABEL_MAX} karakter olabilir."
    return action_url_error(url)


def dismissible_error(dismissible: Any, level: Any) -> str:
    """Kapatılamaz duyuru YALNIZ `critical` olabilir.

    Kapatılamayan bir bilgilendirme duyurusu, müşteri uygulamasını kullanılamaz
    hâle getirir: şerit ekranın üstünde kalır ve kullanıcı onu kaldıramaz.
    """
    if as_bool(dismissible) is False and text(level) != "critical":
        return ("Kapatılamayan duyuru yalnız 'Kritik' düzeyiyle kullanılabilir; "
                "kapatılamayan bir bilgilendirme uygulamayı kullanılamaz hâle "
                "getirir.")
    return ""


def window_error(starts_at: Any, ends_at: Any, *, now: datetime | None = None) -> str:
    """Gösterim penceresi. İkisi de boş olabilir.

    `ends_at` GEÇMİŞTE OLAMAZ: doğduğu anda bitmiş bir duyuru, yöneticinin fark
    etmediği bir hatadır ve ekranda "yayınladım ama görünmüyor" diye geri döner.
    """
    moment = now or datetime.now(UTC)
    start_raw, end_raw = text(starts_at), text(ends_at)

    start = parse_iso(start_raw) if start_raw else None
    end = parse_iso(end_raw) if end_raw else None
    if start_raw and start is None:
        return "Başlangıç anı okunamadı; ISO 8601 UTC bekleniyor (2026-08-20T00:00:00Z)."
    if end_raw and end is None:
        return "Bitiş anı okunamadı; ISO 8601 UTC bekleniyor (2026-08-31T00:00:00Z)."
    if start and end and end <= start:
        return "Bitiş anı başlangıçtan sonra olmalı."
    if end and end <= moment:
        return ("Bitiş anı geçmişte: bu duyuru doğduğu anda bitmiş olurdu ve "
                "hiç görünmezdi.")
    return ""


def coupled_error(changes: dict[str, Any]) -> str:
    """Kısmi yazmada eş alanların birlikte gelmesi (bkz. `COUPLED`)."""
    for left, right in COUPLED:
        if (left in changes) != (right in changes):
            eksik = right if left in changes else left
            return (f"'{left}' ve '{right}' birlikte gönderilir; '{eksik}' eksik. "
                    "İkisinin birlikte hâli bir kural taşıyor ve tek başına "
                    "gelen alanla o kural burada doğrulanamaz.")
    return ""


def unknown_fields(changes: dict[str, Any]) -> list[str]:
    """Sözleşmede yazılabilir olmayan alanlar. `status` da buraya düşer."""
    return sorted(key for key in changes if key not in WRITABLE)


def draft_error(fields: dict[str, Any], *, now: datetime | None = None) -> str:
    """Yeni duyurunun (tam gövde) bütün kuralları. İlk hatayı döndürür."""
    checks = (
        title_error(fields.get("title")),
        body_error(fields.get("body")),
        level_error(fields.get("level")),
        audience_error(fields.get("audience")),
        window_error(fields.get("starts_at"), fields.get("ends_at"), now=now),
        action_pair_error(fields.get("action_label"), fields.get("action_url")),
        dismissible_error(fields.get("dismissible"), fields.get("level")),
    )
    for problem in checks:
        if problem:
            return problem
    return ""


def patch_error(changes: dict[str, Any], *, now: datetime | None = None) -> str:
    """Kısmi yazmanın kuralları. Yalnız GÖNDERİLEN alanlar denetlenir."""
    if not changes:
        return ("En az bir alan değişmeli: gönderilmeyen alan değişmez, bu yüzden "
                "boş bir güncelleme hiçbir şey yapmadan denetim izine satır yazardı.")
    unknown = unknown_fields(changes)
    if unknown:
        return (f"Bu alanlar duyuruda güncellenemez: {', '.join(unknown)}. "
                "Durum ('status') kendi uçlarından değişir.")
    problem = coupled_error(changes)
    if problem:
        return problem

    if "title" in changes and (problem := title_error(changes["title"])):
        return problem
    if "body" in changes and (problem := body_error(changes["body"])):
        return problem
    if "level" in changes and (problem := level_error(changes["level"])):
        return problem
    if "audience" in changes and (problem := audience_error(changes["audience"])):
        return problem
    if "starts_at" in changes and (problem := window_error(
            changes.get("starts_at"), changes.get("ends_at"), now=now)):
        return problem
    if "action_label" in changes and (problem := action_pair_error(
            changes.get("action_label"), changes.get("action_url"))):
        return problem
    # `dismissible` eşi `level` ile denetlenir; ikisi birlikte gelmek zorunda
    # DEĞİL çünkü kural yalnız `dismissible=False` hâlinde bağlayıcı ve o hâlde
    # düzeyi de bilmek gerekir. Panel ikisini birlikte gönderir; gövdeyi elle
    # kuran bir istemci yalnız `dismissible` yollarsa karar sunucuya kalır.
    if "dismissible" in changes and "level" in changes:
        return dismissible_error(changes["dismissible"], changes["level"])
    if changes.get("dismissible") is False and "level" not in changes:
        return ("Kapatılamaz yapılan duyurunun düzeyi de gönderilmeli: kural "
                "ikisinin birlikte hâli hakkındadır ('critical' dışında "
                "kapatılamaz duyuru olmaz).")
    return ""


# ================================================================== görünümler

def notice_row(raw: dict[str, Any], *, server_time: str = "") -> dict[str, Any]:
    """Sözleşmedeki duyuru kaydı + EKRANIN türettiği üç alan.

    `live` SUNUCUDAN gelir ve burada yeniden HESAPLANMAZ (sözleşme §Şema):
    istemcide hesaplansaydı saati kaymış bir panelde duyuru bir gün erken
    "bitmiş" görünürdü. Türetilen alanlar `live`i doğrulamaz, AÇIKLAR:

    · `visibility` — kullanıcıya yazılan cümlenin anahtarı. "Yayında ama henüz
      görünmüyor" ile "yayındaydı, süresi doldu" aynı `live: false` değerinden
      çıkar ve ikisi bambaşka şeylerdir; yalnız `live`e bakan bir ekran ikisine
      de "görünmüyor" derdi.
    · `ends_in_hours` — bitişe kalan süre (sunucu saatiyle). Panel "yakında
      bitiyor" uyarısını buradan çizer.
    · `trackable` — kitlesi `all` olan duyuru ölçülemez; liste sütununda "0"
      yerine "ölçülemez" yazdırır.
    """
    now = parse_iso(server_time) or datetime.now(UTC)
    status = text(raw.get("status")) or "draft"
    live = as_bool(raw.get("live"))
    start = parse_iso(raw.get("starts_at"))
    end = parse_iso(raw.get("ends_at"))

    if status == "archived":
        visibility = "archived"
    elif status == "draft":
        visibility = "draft"
    elif live:
        visibility = "live"
    elif end and end <= now:
        visibility = "expired"
    elif start and start > now:
        visibility = "scheduled"
    else:
        # Yayında, penceresi uygun ama sunucu `live` demiyor: sebebi
        # BİLİNMİYOR ve uydurulmaz. Panel bu değeri "sunucu görünmüyor diyor"
        # diye yazar; sessizce "görünüyor" demek yalan olurdu.
        visibility = "hidden" if live is False else "live"

    audience = text(raw.get("audience")) or "customers"
    return {
        "id": as_int(raw.get("id")),
        "title": text(raw.get("title")),
        "body": text(raw.get("body")),
        "level": text(raw.get("level")) or "info",
        "audience": audience,
        "status": status,
        "starts_at": text(raw.get("starts_at")),
        "ends_at": text(raw.get("ends_at")),
        "action_label": text(raw.get("action_label")),
        "action_url": text(raw.get("action_url")),
        "dismissible": as_bool(raw.get("dismissible")) is not False,
        "published_at": text(raw.get("published_at")),
        "live": bool(live),
        "seen_count": as_int(raw.get("seen_count")),
        "created_at": text(raw.get("created_at")),
        "updated_at": text(raw.get("updated_at")),
        # --- ekranın türettikleri ---
        "visibility": visibility,
        "ends_in_hours": hours_between(now, end),
        "trackable": audience != UNTRACKABLE_AUDIENCE,
    }


def stats_view(raw: dict[str, Any]) -> dict[str, Any]:
    """Görülme istatistiği. `null` KORUNUR, sıfıra çevrilmez.

    `trackable: false` (kitle `all`) durumunda sunucu `seen_count`, `seen_rate`
    ve `daily` alanlarını `null` döndürür. Sıfır "kimse görmedi", `null`
    "ölçülemiyor" demektir; ikisini karıştırmak, çalışan bir duyuruyu başarısız
    gösterirdi. Bu yüzden bu üç alanda `as_int` KULLANILMAZ.
    """
    trackable = as_bool(raw.get("trackable"))
    audience = text(raw.get("audience")) or "customers"
    if trackable is None:
        # Sunucu bayrağı vermediyse kitleden türetilir; sözleşme ikisini eşitliyor.
        trackable = audience != UNTRACKABLE_AUDIENCE

    def optional_int(value: Any) -> int | None:
        return None if value is None else as_int(value)

    daily = raw.get("daily")
    rows = None
    if isinstance(daily, list):
        rows = [{"date": text(item.get("date")), "seen": as_int(item.get("seen"))}
                for item in daily if isinstance(item, dict)]

    rate = raw.get("seen_rate")
    return {
        "id": as_int(raw.get("id")),
        "status": text(raw.get("status")),
        "audience": audience,
        "audience_size": optional_int(raw.get("audience_size")),
        "seen_count": optional_int(raw.get("seen_count")),
        "dismissed_count": optional_int(raw.get("dismissed_count")),
        "seen_rate": None if rate is None else float(rate),
        "first_seen_at": text(raw.get("first_seen_at")),
        "last_seen_at": text(raw.get("last_seen_at")),
        "trackable": bool(trackable),
        "daily": rows,
    }


def publish_view(raw: dict[str, Any]) -> dict[str, Any]:
    """`POST /{id}/publish` yanıtı.

    `live_from`, `starts_at` gelecekteyse doludur ve panelin "yayınlandı ama
    henüz görünmüyor" cümlesini yazmasını sağlar — yayınla düğmesine basıp
    hiçbir şey görmeyen yönetici, aksi hâlde ikinci kez basardı.
    """
    return {
        "id": as_int(raw.get("id")),
        "status": text(raw.get("status")) or "published",
        "published_at": text(raw.get("published_at")),
        "live": as_bool(raw.get("live")) is True,
        "live_from": text(raw.get("live_from")),
        "estimated_audience": None if raw.get("estimated_audience") is None
        else as_int(raw.get("estimated_audience")),
    }


def audit_detail(fields: dict[str, Any]) -> dict[str, Any]:
    """Denetim izine giden künye — GÖVDENİN TAMAMI DEĞİL.

    Sözleşme §Denetim eylemleri `payload_json` için başlık + kitle + gövde
    UZUNLUĞU diyor. Yerel iz de aynı ölçüyü tutar: 2000 karakterlik bir duyuru
    metnini her denemede tabloya yazmak, izi okunamaz kılardı.
    """
    detail: dict[str, Any] = {}
    if "title" in fields:
        detail["title"] = text(fields.get("title"))[:TITLE_MAX]
    if "audience" in fields:
        detail["audience"] = text(fields.get("audience"))
    if "level" in fields:
        detail["level"] = text(fields.get("level"))
    if "body" in fields:
        detail["body_length"] = len(text(fields.get("body")))
    return detail


def reference() -> dict[str, Any]:
    """Panelin form ve süzgeçleri çizmek için okuduğu SABİT sözleşme.

    YEREL: geçit düşse bile açılır kutular, sınırlar ve yardım metinleri
    çizilebilir (K7). Panelin bu listeleri kendi içinde ikinci kez tutması,
    sözleşme değiştiğinde iki yerden birinin unutulması demekti.
    """
    return {
        "levels": [{"value": key, "label": LEVEL_LABELS[key], "tone": LEVEL_TONES[key]}
                   for key in LEVELS],
        "audiences": [{"value": key, "label": AUDIENCE_LABELS[key],
                       "help": AUDIENCE_HELP[key],
                       "trackable": key != UNTRACKABLE_AUDIENCE}
                      for key in AUDIENCES],
        "statuses": [{"value": key, "label": STATUS_LABELS[key],
                      "tone": STATUS_TONES[key]} for key in STATUSES],
        "limits": {"title_max": TITLE_MAX, "body_max": BODY_MAX,
                   "action_label_max": ACTION_LABEL_MAX,
                   "action_url_max": ACTION_URL_MAX,
                   "reason_min": MIN_REASON, "reason_max": MAX_REASON},
    }
