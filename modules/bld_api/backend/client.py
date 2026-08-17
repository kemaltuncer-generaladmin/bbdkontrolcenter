"""BLD sunucusuna açılan TEK kapı (K4) — `/api/control/*`.

Karşı taraf: `platform/extensions/veykemtu/bridgeapi` (TastyIgniter/Laravel).
Sözleşme iki parçalı ve ikisi de dondurulmuş:
  · `/api/control/kds/*` → K-21 köprü sözleşmesi §1-§4 (mutfak kasaları),
  · `/api/control/<alan>/*` → `BLD/docs/control/` altındaki 13 alan dosyası
    (menu · products · settings · orders · subscriptions · customers ·
    invoices · cms · sms · notifications · monitor · dashboard · audit).
Uydurma alan, yol ya da başlık EKLENMEZ; eksik görülen şey rapora yazılır.

NEDEN TEK MODÜL, NEDEN ALAN BAŞINA GEÇİT DEĞİL

    Sunucu sınırları IP başına ve Kontrol Merkezi tek IP'den çıkıyor:
    `bld-control` = 1200/saat (yalnız `/api/control/kds/*`),
    `bld-control-panel` = 3000/saat (13 yeni alan). Geçit alan başına
    bölünseydi her parça kendi 18/dk kovasını taşır, her biri kendini uyumlu
    sanar ve toplam sunucu tavanını katlayarak aşardı — üstelik `_send` 429'u
    yalnız BİR kez yeniden deniyor. Paylaşılan bir sunucu bütçesi ancak
    paylaşılan bir istemci kovasıyla onurlandırılır; bu yüzden on üç alanın
    hepsi burada, tek kova ve tek imza üreticisiyle durur.

KİMLİK — `X-Control-Signature` (sözleşme §1)

    X-Control-Timestamp: <unix saniye>
    X-Control-Nonce:     <16-128 karakter, rastgele>
    X-Control-Signature: sha256=<64 hex>

    kanonik = METOT \\n YOL \\n ZAMAN \\n NONCE \\n sha256_hex(ham gövde)
    imza    = "sha256=" + hmac_sha256(kanonik, sır)

    Sır kasadan gelir: `server.bld.control_secret`. Kasadaki adlandırma
    kuralı `server.<uygulama>.<alan>` — bu bir SUNUCU sırrıdır, bir uygulama
    belirteci değil. Sır tanımsızsa istek HİÇ GÖNDERİLMEZ.

    NEDEN CİHAZ TOKEN'I DEĞİL: Kontrol Merkezi mutfakta duran bir kasa
    değildir. `/api/kitchen/*` uçlarına kasa token'ıyla girseydi sunucu her
    istekte o kasanın `last_seen_at` damgasını tazeler, panelde açık duran
    bir ekran mutfakta olmayan bir kasayı "çevrimiçi" gösterirdi.

DÖNÜŞ BİÇİMİ — snake_case (sözleşme §2). `store_api` camelCase döndürür;
    burada dönüştürme YAPILMAZ, sunucunun adları korunur. İki geçidin
    biçimini birbirine benzetmek, sözleşmeyle ekran arasına sessiz bir
    çeviri katmanı sokardı.

BEŞ POLİTİKA — hepsi geçitte, ekranların iyi niyetine bırakılmadan uygulanır

1. ACİL FREN (`read_only`, VARSAYILAN AÇIK). Açıkken GET dışı her istek
   geçitte reddedilir; uzağa hiç gitmez. Canlı mutfakta çalışıldığı için
   varsayılan güvenli taraftadır: yazmayı açmak bilinçli bir karardır.

2. KURU PROVA (`dry_run`, VARSAYILAN KAPALI — K-22 §4). Sözleşmedeki
   yazma uçları `dry_run` bayrağını ANLAR: hiçbir yazma yapılmaz, sunucu
   denetim satırını `result="dry_run"` ile yine de yazar ve
   `{"ok": true, "dry_run": true, "would": {...}}` döner. Bayrak bu yüzden
   gövdeyle gider ve istek gerçekten gönderilir.
   PARAMETRE DURUYOR, VARSAYILANI GİTTİ. K-22 kuru provayı arayüzden
   kaldırdı ve `dry_run_default` false oldu; bayrağın KENDİSİ kalkmadı
   çünkü sözleşme §4 additive'dir — kaldırmak, bayrağı açıkça gönderen eski
   çağrıları (ve onların testlerini) kırardı. Bir prova hâlâ istenebilir;
   yalnız artık açıkça istenir.
   TUZAK — SÖZLEŞMEDE OLMAYAN bir yola kuru prova ile yazılırsa bayrak
   gövdeye konup gönderilemez: Laravel tanımadığı alanı sessizce yok sayar
   ve "prova" sandığımız istek GERÇEK yazma olurdu. Bilinmeyen uçta istek
   HİÇ GÖNDERİLMEZ; ne gönderileceğini anlatan sentetik yanıt döner.

3. AKTÖR HER YAZMADA, GEREKÇE UÇ BAŞINA (sözleşme §3). `actor` İSTİSNASIZ
   zorunludur: "kim yaptı" sorusunun cevabı hiçbir yazmada boş kalmaz.
   `reason` ise artık KÜRESEL DEĞİL, UÇ BAŞINA istenir — kuralı `_REASON_OPTIONAL`
   kayıt defteri taşır ve VARSAYILAN "gerekçe İSTER"dir. İkisi de gövdeye
   konur; başlıkla TAŞINMAZ — sözleşme böyle bir başlık tanımlamıyor ve
   sunucu gerekçeyi gövdeden okuyup `veykemtu_control_audit` tablosuna
   yazıyor. İstek çıkmadan aynı bilgi `mod_bld_api_audit` tablosuna da
   yazılır: ağ koparsa "ne yapmaya çalıştık" kaydı yerelde kalır. Yerel
   satır GEREKÇEDEN BAĞIMSIZ olarak her yazmada açılır; gerekçe istenmeyen
   uçta `reason` sütunu boş kalır, satırın kendisi kalmaz değil.

   NEDEN UÇ BAŞINA: taslak kurmak bir taahhüt değil, yayınlamak taahhüttür.
   Bir güne beş kalem eklerken beş kez on karakter istemek "düzeltme", "ok",
   "asdasd" üretiyordu — tam da bu dosyanın `MIN_REASON` yanında yazdığı
   arıza. Az yerde istenen gerekçe, çok yerde istenenden daha değerlidir.

4. YİNELEME. GET üç kez denenir (0.4 · n sn bekleme). YAZMA YİNELENMEZ:
   zaman aşımına uğrayan yazma uzakta uygulanmış olabilir; körlemesine
   tekrarlamak ikinci iptal, ikinci revizyon, ikinci komut demektir.
   Sözleşmede idempotency anahtarı taşıyan bir başlık YOK (bkz. `audit.py`),
   yani yinelemenin güvenli hâli de yok. 429 istisnadır: hız sınırı isteği
   denetleyiciye HİÇ ulaştırmadan reddeder, yan etkisi yoktur —
   `Retry-After` kadar beklenip bir kez yinelenir.
   HER DENEME YENİDEN İMZALANIR: nonce sunucuda 600 sn hatırlanıyor ve
   ikinci kez kabul edilmiyor. Aynı başlıklarla yinelemek "Bu istek daha
   önce işlendi" hatası üretirdi.

5. HIZ KOVASI — TEK KOVA. Sunucu sınırlarının en darı `throttle:bld-control`
   = 1200/saat/IP. Kova dakikada 18'de tutar: 18 · 60 = 1080 < 1200, yani
   dakikalık bir patlama saatlik bütçeyi tüketemez ve pay elle atılan
   istekler için kalır. Alan başına ayrı kova YOKTUR (bkz. modül başlığı).

6. OKUMA ÖNBELLEĞİ (`cache.py`). On iki panelin yoklaması kovayı büyüterek
   değil, aynı cevabı ikinci kez sormayarak karşılanır. ÖNBELLEK YALNIZCA
   REFERANS VERİ İÇİNDİR: kategoriler, ödeme yöntemleri ve ayar
   varsayılanları, seçici için ürün kataloğu, denetim eylem sözlüğü.
   Sipariş, stok sayısı, müşteri, abonelik ve fatura listesi ÖNBELLEĞE
   ALINMAZ — personel "kaydettim ama listede yok" yaşamamalı. Yazan her
   metot kendi dalını düşürür, yani yeni eklenen bir ürün seçicide ilk
   okumada görünür; TTL'in dolması beklenmez. L2 (SQLite) ayrıca K7
   dayanıklılığı verir: BLD erişilemezken son bilinen hâl gösterilebilir.

GÖVDE BAYT BAYT AYNI GİDER — bunun neden zorunlu olduğu `_encode` ve
`_send` içinde anlatılıyor. Görsel yüklemenin JSON gövdesinde base64 olarak
gitmesinin sebebi de budur (`upload.py`).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import time
from collections import deque
from collections.abc import Callable
from secrets import token_hex
from typing import Any

import httpx

from .audit import AuditTrail
from .cache import ReferenceCache, SnapshotCache
from .errors import BldApiError, mask_mapping, mask_text
from .paging import (
    AUDIT_PER_PAGE,
    DEFAULT_PER_PAGE,
    clamp_page_size,
    collect_all,
    envelope,
    page_params,
)
from .upload import (
    DEFAULT_MAX_UPLOAD_MB,
    IMAGE_MIMES,
    PRODUCT_IMAGE_MAX_BYTES,
    describe,
    json_body,
    max_upload_bytes,
    prepare_upload,
)

#: Kontrol yüzeyinin kökü. Alan önekleri bundan türer; tek bir yerde
#: durmaları, bir alanın yanlış köke bağlanmasını imkânsız kılar.
CONTROL_ROOT = "/api/control"

#: KDS ailesinin öneki (K-21 §2). ADI DEĞİŞMEZ: on yedi metot ve dört test
#: dosyası buna bağlı, `bld_kds` modülü de bu yüzeyi kullanıyor.
CONTROL = f"{CONTROL_ROOT}/kds"

#: Panel alanlarının önekleri (`docs/control/00-genel.md` tablosu).
MENU = f"{CONTROL_ROOT}/menu"
PRODUCTS = f"{CONTROL_ROOT}/products"
SETTINGS = f"{CONTROL_ROOT}/settings"
ORDERS = f"{CONTROL_ROOT}/orders"
SUBSCRIPTIONS = f"{CONTROL_ROOT}/subscriptions"
CUSTOMERS = f"{CONTROL_ROOT}/customers"
INVOICES = f"{CONTROL_ROOT}/invoices"
CMS = f"{CONTROL_ROOT}/cms"
SMS = f"{CONTROL_ROOT}/sms"
NOTIFICATIONS = f"{CONTROL_ROOT}/notifications"
MONITOR = f"{CONTROL_ROOT}/monitor"
DASHBOARD = f"{CONTROL_ROOT}/dashboard"
AUDIT = f"{CONTROL_ROOT}/audit"

#: Kasadaki imza sırrı. Adlandırma `server.<uygulama>.<alan>`: bu sır BLD
#: SUNUCUSUNA aittir (orada `BLD_CONTROL_SECRET` ortam değişkeni), bir
#: uygulama belirteci değildir.
SECRET_KEY = "server.bld.control_secret"

#: Yazma gerekçesinin en az uzunluğu — GEREKÇE İSTENEN uçlarda (sözleşme §3).
#: "ok", "düzeltme" gibi metinler denetim izini işe yaramaz kılıyor; ADR 0012
#: ekranlarda da 10 karakter istiyor. Sunucu da aynı sınırı uyguluyor — çift
#: kapı (K9).
#:
#: SINIR DEĞİŞMEDİ, KAPSAMI DEĞİŞTİ. Gerekçe artık her yazmada değil, uç
#: başına isteniyor (`_REASON_OPTIONAL`); istendiği yerde hâlâ 10 karakterdir.
#: Sınırı gevşetmek, gerekçeyi seyrekleştirmenin tersi olurdu: az yerde ama
#: DOLU dolu istemek, çok yerde ve boş boş istemekten değerli.
MIN_REASON = 10

#: Sipariş revizyonu ve durum/iptal uçlarında gerekçenin ÜST sınırı
#: (K-21 §2.5 ve `docs/control/00-genel.md` §3): `veykemtu_order_revisions.reason`
#: sütunu 160 karakter. Taşan gerekçe kırpılmaz, sunucu 422 verir.
MAX_REASON_STRICT = 160

#: Diğer PANEL uçlarında gerekçenin üst sınırı (`00-genel.md` §3: "en çok 500
#: karakter"). K-21 KDS uçlarına UYGULANMAZ — o sözleşme bir üst sınır
#: söylemiyor ve uydurulmuş bir sınır, bugün çalışan bir çağrıyı yarın
#: reddederdi.
MAX_REASON = 500

#: `actor` alanının sınırları (`00-genel.md` §3: 2–120 karakter). Sunucu
#: aşanı 422 ile reddediyor; burada kesmek hız kovasından pay harcamaz ve
#: hatayı "BLD isteği doğrulayamadı" yerine anlaşılır bir cümleye çevirir.
ACTOR_MIN = 2
ACTOR_MAX = 120

GET_ATTEMPTS = 3
RETRY_AFTER_CAP = 30.0

#: İmza penceresi (sunucu: `VerifyControlSignature::WINDOW_SECONDS`). Burada
#: yalnız hata metninde kullanılır: saat kayması 401'in en sık sebebi ve
#: "imza doğrulanamadı" tek başına sahada teşhis ettirmiyor.
SIGNATURE_WINDOW_SECONDS = 300

#: Gövdesiz isteğin gövde özeti. Sözleşme §1 bu sabiti açıkça yazıyor.
EMPTY_BODY_SHA = hashlib.sha256(b"").hexdigest()

#: Kasada yönetilen 16 ayar (`KitchenDeviceSettings::forDevice`) — DEĞİŞMEZ.
DEVICE_SETTINGS = (
    "poll_seconds", "sound_enabled", "warning_after_minutes", "late_after_minutes",
    "printer_device_path", "printer_code_page", "health_seconds",
    "connection_alarm_seconds", "alarm_silenceable", "volume_percent", "audio_sink",
    "tts_enabled", "tts_rate_percent", "alarm_repeat_seconds", "alarm_max_repeats",
    "touch_mode",
)

#: Kilit politikası — 7 yeni ayar (sözleşme §2.2). HEPSİ NULLABLE.
#:
#: `null` = "yönetici dokunmadı" = kasanın bugünkü davranışı, yani SERBEST.
#: Bu varsayılan kritiktir: alan eklenmesi bugünkü kasaları kilitlemez, kilit
#: ancak yönetici açıkça `false` yazınca doğar. Geçit bu yüzden `None`
#: değerleri gövdeden DÜŞÜRMEZ; JSON `null` olarak gönderir (bkz.
#: `update_device_settings`).
DEVICE_LOCKS = (
    "allow_settings", "allow_server_change", "allow_window_controls",
    "allow_order_edit", "allow_manual_reprint", "allow_sales_control", "lock_message",
)

#: Olay bazlı sesler — K-22 §1, göç `2026_08_18_000001`. TEK ANAHTAR, virgülle
#: ayrılmış olay adları taşır (`KdsSoundEvent` adlarıyla birebir).
#:
#: ÜÇ DEĞERLİ, İKİ DEĞİL: `null` "yönetici dokunmadı" (kasa kendi listesini
#: korur), boş dize "hiçbiri kapalı olmasın" demektir. `audio_sink`'teki boş
#: dize istisnasının aynısı; ikisini karıştırmak, sesleri kapatmak isteyen bir
#: yöneticinin komutunu "hiç dokunma"ya çevirirdi.
DEVICE_SOUND_EVENTS = ("disabled_sound_events",)

#: Yönetilen ayarların tamamı: 16 + 7 + 1 = 24 (sözleşme §2.2 + K-22 §1).
MANAGED_SETTINGS = DEVICE_SETTINGS + DEVICE_LOCKS + DEVICE_SOUND_EVENTS

#: `KitchenCommand::ALL` — sözleşmenin 5'i (§2.3) + K-22 §2'nin 3 yenisi.
#:
#: `update` kasadaki `.deb` paketini indirip kurar ve servisi yeniden başlatır;
#: `unpair` cihaz token'ını siler ve kasa eşleme ekranına döner; `clear_queue`
#: BEKLEYEN işleri de düşürür (mevcut `clear_failed` yalnız hatalıları).
#: Listede olmayan bir ad için istek HİÇ GÖNDERİLMEZ: kuyruğa atılıp kasada
#: sessizce yok sayılan komut, yöneticiye "gitti" der ve hiçbir şey olmaz.
COMMANDS = ("test_receipt", "reprint", "clear_failed", "silence_alarm", "restart",
            "update", "unpair", "clear_queue")

#: `reprint` komutunun fiş türleri (sözleşme §2.3).
REPRINT_TYPES = ("mutfak", "musteri", "kurye")

#: Yol kalıplarında kullanılan tarih parçası (`YYYY-MM-DD`). Ayrı sabit,
#: çünkü f-string içinde `\d{4}` yazmak süslü parantezi biçimlendirme
#: yer tutucusu sanılmasına yol açar ve desen sessizce bozulurdu.
_DATE = r"\d{4}-\d{2}-\d{2}"

#: Yol parçasındaki tarihin tek başına doğrulanması için.
_DATE_ONLY = re.compile(rf"^{_DATE}$")

#: `Content-Disposition: attachment; filename="bld-siparisler-....csv"` içinden
#: dosya adını çeker. CSV ve fatura HTML'i bu başlıkla geliyor ve panelin adı
#: kendi uydurması, indirilen dosyanın sunucudaki adıyla ayrışması demekti.
_FILENAME = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?')


class _Unset:
    """"Alan hiç gönderilmedi" nöbetçisi — `None`'dan AYRI bir değer.

    Sözleşmedeki bütün kısmi yazmalar (`PATCH`) bu ayrımı şart koşuyor:
    "alanı hiç göndermemek 'dokunma', `null` göndermek 'boşalt'"
    (`menu.md` → `PATCH /days/{date}`). Varsayılanı `None` olan bir imza bu
    iki niyeti ayırt edemez ve `internal_note`'u temizlemek isteyen yönetici
    ile ona hiç dokunmayan yönetici aynı gövdeyi üretirdi.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - yalnız hata ayıklama kolaylığı
        return "UNSET"


#: Kısmi yazma metotlarının varsayılanı. `Any` olarak yazıldı: her alanın
#: kendi tipiyle birlikte varsayılan olarak kullanılabilsin.
UNSET: Any = _Unset()

#: KURU PROVAYI ANLAYAN yollar — KAYIT DEFTERİ.
#:
#: Bu listede olmayan bir yola kuru prova ile yazılamaz: Laravel tanımadığı
#: `dry_run` alanını sessizce yok sayar ve "prova" sanılan istek GERÇEK yazma
#: olurdu (bkz. modül başlığı, 2. politika). Yani burası bir kolaylık değil,
#: bir emniyet kilidi.
#:
#: NEDEN KAYIT DEFTERİ, NEDEN TEK BLOK DEĞİL: on üç alanın ~50 deseni tek
#: yerde toplandığında, koruduğu metotlardan yedi yüz satır uzakta duran ve
#: kimsenin okumadığı bir blok olurdu. Yeni bir alan eklerken kaydı unutmak,
#: o alanın bütün yazmalarını sessiz bir no-op'a çevirirdi. Bu yüzden her
#: alan kendi desenlerini KENDİ METOT BLOĞUNUN BAŞINDA kaydeder
#: (`BldApi` gövdesindeki `register_dry_run(...)` çağrıları) ve kapsamı bir
#: test kanıtlar: her yazma metodu gerçekten bir desene düşüyor mu.
_DRY_RUN_AWARE: list[re.Pattern[str]] = []


def register_dry_run(*patterns: str) -> None:
    """Kuru prova farkında yolları deftere yazar.

    Desenler `^...$` ile tam eşleşmelidir (`docs/control/00-genel.md` §4).
    Çağrı, koruduğu metotların hemen üstünde yapılır.
    """
    _DRY_RUN_AWARE.extend(re.compile(pattern) for pattern in patterns)


# KDS ailesi (K-21 §2 + §4) — mevcut liste, olduğu gibi.
register_dry_run(
    rf"^{CONTROL}/devices$",
    rf"^{CONTROL}/devices/\d+$",
    rf"^{CONTROL}/devices/\d+/pairing-code$",
    rf"^{CONTROL}/devices/\d+/revoke$",
    rf"^{CONTROL}/devices/\d+/settings$",
    rf"^{CONTROL}/devices/\d+/commands$",
    rf"^{CONTROL}/orders/\d+/revisions$",
    rf"^{CONTROL}/orders/\d+/status$",
)

#: GEREKÇE İSTEMEYEN yazmalar — KAYIT DEFTERİ. `(FİİL, yol deseni)` çiftleri.
#:
#: Gerekçe müşteriye GÖRÜNÜR HÂLE GELEN ve GERİ ALINMASI ZOR olan işlemlerde
#: istenir. Taslak kurmak bir taahhüt değildir; yayınlamak taahhüttür. Defterde
#: eşleşen bir yazma gerekçesiz geçer, `actor` yine zorunludur ve denetim satırı
#: yine açılır — yalnız "neden" sorusu seyrekleşir.
#:
#: NEDEN MUAFİYET DEFTERİ, NEDEN "GEREKÇE İSTEYENLER" DEFTERİ DEĞİL: ikisi de
#: bir defterdir, ama UNUTMANIN BEDELİ farklıdır. "İsteyenler" listesine
#: eklenmesi unutulan yeni bir uç sessizce gerekçesiz yazar ve denetim izinde
#: nedeni boş bir satır bırakır — kimse fark etmez. Muafiyet defterine
#: eklenmesi unutulan uç ise ilk çağrıda `reason_required` ile geri döner:
#: yanlış, ama GÜRÜLTÜLÜ. Varsayılan bu yüzden "gerekçe İSTER" tarafındadır.
#:
#: FİİL DEFTERİN PARÇASIDIR: aynı yol iki ayrı politikada olabiliyor —
#: `PATCH /menu/days/{date}` gerekçe istemez, `DELETE /menu/days/{date}` ister.
#: Yalnız yola bakan bir defter ikisini ayıramaz ve gün silmeyi (kalemleriyle
#: birlikte, geri alınamaz) sessizce gerekçesiz bırakırdı.
#:
#: KAYIT, KORUDUĞU METOTLARIN HEMEN ÜSTÜNDE yapılır — kuru prova defteriyle
#: aynı üslup ve aynı gerekçe: yedi yüz satır uzakta duran bir blok okunmaz.
_REASON_OPTIONAL: list[tuple[str, re.Pattern[str]]] = []


def register_reason_optional(*rules: tuple[str, str]) -> None:
    """Gerekçe İSTEMEYEN `(fiil, yol deseni)` çiftlerini deftere yazar.

    Desenler `^...$` ile tam eşleşmelidir (`docs/control/00-genel.md` §4).
    Defterde OLMAYAN her yazma gerekçe ister; bu bir varsayılan değil, bir
    emniyet kilididir (yukarıdaki gerekçeye bakın).
    """
    _REASON_OPTIONAL.extend(
        (verb.upper(), re.compile(pattern)) for verb, pattern in rules
    )


def canonical_payload(method: str, path: str, timestamp: int | str, nonce: str,
                      body: bytes) -> str:
    """Sözleşme §1'deki kanonik yük: METOT \\n YOL \\n ZAMAN \\n NONCE \\n sha256(gövde).

    `path` SORGU DİZESİ HARİÇ, baştaki `/` dâhil verilir — sunucu tarafı da
    `$request->getPathInfo()` okuyor. Süzgeç parametreleri imzaya girmez;
    iki tarafın sorgu sıralamasını tutturması gerekmesin diye sözleşme
    böyle kurulmuş ve yazma uçlarının tamamı gövdeli.
    """
    return "\n".join([
        method.upper(),
        path,
        str(timestamp),
        nonce,
        hashlib.sha256(body).hexdigest(),
    ])


def sign(secret: str, canonical: str) -> str:
    """`X-Control-Signature` değeri: `sha256=<hex>`."""
    digest = hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256)
    return "sha256=" + digest.hexdigest()


def _encode(body: Any) -> bytes:
    """Gövdeyi HAM BAYTA çevirir — imzalanan ve gönderilen dizi budur.

    TUZAK: `httpx`'e `json=` verilirse gövdeyi httpx kendi serileştirir.
    İmzayı burada üretilen baytlar üzerinden hesaplayıp gövdeyi httpx'e
    yeniden kurdurmak, aradaki en küçük farkta (ayraç boşluğu, Unicode
    kaçışı, anahtar sırası) sunucuda `hash('sha256', $request->getContent())`
    başka bir özet üretir ve imza doğrulanamaz. Üstelik hata "gövde bozuk"
    demez, "imza doğrulanamadı" der — sahada teşhis edilemez.
    Bu yüzden gövde BURADA bir kez üretilir ve `content=` ile aynen gider.

    `ensure_ascii=False`: Türkçe karakterler UTF-8 olarak gider, `\\uXXXX`
    kaçışıyla değil. İki biçim de geçerli JSON'dur ama farklı BAYTTIR;
    imza baytı imzaladığı için biçim burada sabitlenir.
    """
    if body is None:
        return b""
    if isinstance(body, bytes):
        return body
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _RateBucket:
    """Kayan pencereli hız kovası: son 60 saniyede en çok `limit` istek.

    Kilit uykuyu da kapsar; bekleyenler sıraya girer. Kasıtlıdır: geçidin
    tamamı sıralı davranmalı, aynı anda uyanan on istek sunucuya salkım
    hâlinde gitmemeli.
    """

    def __init__(self, limit: int, sleeper: Callable[[float], Any] = asyncio.sleep) -> None:
        self._limit = max(1, int(limit))
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._sleep = sleeper

    async def take(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 60:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await self._sleep(max(0.05, 60 - (now - self._stamps[0])))


class BldApi:
    """`bld.api` yeteneğinin uygulaması.

    KDS liste metotları:  `list[dict]` (K-21; `bld_kds` buna bağlı, değişmez)
    Panel liste metotları: `{"items": [...], "meta": {...}}`
    Tekil metotlar:       düz sözlük
    Belge metotları:      `{"content": bayt, "text", "content_type", ...}`
    Yazma metotları:      sunucunun yanıtı — kuru provada
                          `{"ok": true, "dry_run": true, "would": {...}}`;
                          sözleşmede olmayan bir yolda ise istek gönderilmeden
                          `{"ok": true, "dry_run": true, "sent": false, ...}`

    İKİ LİSTE BİÇİMİ BİLEREK FARKLI. KDS metotları düz dizi döndürüyor ve
    `bld_kds` paneli onu öyle okuyor; değiştirmek çalışan bir ekranı kırardı.
    Panel uçları ise sayfalı (`meta.page/per_page/total/last_page`) ve `meta`
    atılırsa "kaç sayfa var" sorusunun cevabı kaybolur. Zarfı tek biçime
    zorlamak, iki sözleşmeden birini bozmak demekti.
    """

    def __init__(
        self,
        *,
        base_url: str = "",
        secrets: Any,
        log: Any,
        store: Any = None,
        timeout: float = 30.0,
        read_only: bool = True,
        dry_run_default: bool = False,
        require_reason: bool = True,
        requests_per_minute: int = 18,
        page_size: int = DEFAULT_PER_PAGE,
        reference_ttl: int = 900,
        snapshot_ttl: int = 1800,
        max_items: int = 5000,
        max_upload_mb: int = DEFAULT_MAX_UPLOAD_MB,
        transport: Any = None,
    ) -> None:
        # Taban adres DEPODA DURMAZ: canlı adres `config/local.yaml` içindeki
        # `modules.bld_api.base_url` ile gelir (K8 ile aynı gerekçe — hedef
        # sistemin adresi de dağıtıma özgü bir veridir).
        self._base = (base_url or "").strip().rstrip("/")
        self._secrets = secrets
        self._log = log
        self._timeout = float(timeout)
        self._read_only = bool(read_only)
        self._dry_run_default = bool(dry_run_default)
        self._require_reason = bool(require_reason)
        self._page_size = clamp_page_size(page_size)
        self._max_items = max(1, int(max_items))
        self._max_upload = max_upload_bytes(max_upload_mb)
        self._transport = transport
        self._secret: str | None = None

        self._sleep: Callable[[float], Any] = asyncio.sleep
        self._bucket = _RateBucket(requests_per_minute, sleeper=lambda s: self._sleep(s))
        self._audit = AuditTrail(store, log)
        self._reference = ReferenceCache(reference_ttl)
        self._snapshot = SnapshotCache(store, log, snapshot_ttl)

    # ================================================================ durum

    def state(self) -> dict[str, Any]:
        """Geçidin o anki kuralları. Ekranlar acil freni bu bilgiyle gösterir.

        Anahtarlar snake_case: BLD sözleşmesinin tamamı öyle (§2) ve aynı
        ekranda iki adlandırma bulundurmak yazım hatasını sessiz bırakır.
        """
        return {
            "base_url": self._base,
            "read_only": self._read_only,
            "dry_run_default": self._dry_run_default,
            "require_reason": self._require_reason,
            "page_size": self._page_size,
            "max_upload_bytes": self._max_upload,
        }

    async def health(self) -> dict[str, Any]:
        """Sunucu ayakta ve imza geçerli mi — GET /api/control/kds/overview.

        Özet ucu en ucuz imzalı çağrıdır: hem erişimi hem sırrın/saatin
        doğruluğunu tek istekte sınar. Ayrı bir sağlık ucu sözleşmede yok;
        uydurulmaz.
        """
        started = time.monotonic()
        try:
            await self._request("GET", f"{CONTROL}/overview")
        except BldApiError as failure:
            return {"ok": False, "error": failure.message, "code": failure.code,
                    "status": failure.status,
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
        return {"ok": True, "error": "", "code": "", "status": 200,
                "elapsed_ms": int((time.monotonic() - started) * 1000)}

    async def audit_trail(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """Geçidin YEREL yazma izi (`mod_bld_api_audit`). Sunucuya gitmez."""
        return await self._audit.recent(limit=limit)

    # ============================================================= alt yapı

    async def _control_secret(self) -> str:
        if self._secret:
            return self._secret
        value = await self._secrets.get(SECRET_KEY)
        if not value:
            raise BldApiError(
                "BLD kontrol sırrı kasada yok; istek gönderilmedi. config/local.yaml "
                f"içine secrets.{SECRET_KEY} yazılmalı — değer sunucudaki "
                "BLD_CONTROL_SECRET ortam değişkeniyle aynı olmalıdır.",
                code="config_missing",
            )
        self._secret = str(value).strip()
        return self._secret

    def _scrub(self, text: str) -> str:
        """Yüklenmiş sırrın ÇIPLAK hâlini metinden siler.

        Ad tabanlı maskeleme (`errors.mask_text`) `secret: <değer>` biçimini
        yakalar; sunucu sırrı alan adı olmadan yankılarsa onu yakalayamaz.
        Sır rastgele bir dizedir, deseni yoktur — tek çare bilinen değeri
        aramaktır. Kısa değerler (< 8 karakter) atlanır: masum metni
        yıldızlarla doldurup hatayı okunamaz kılardı.
        """
        if self._secret and len(self._secret) >= 8:
            return text.replace(self._secret, "***")
        return text

    @staticmethod
    def _query(**filters: Any) -> dict[str, Any] | None:
        """Boş süzgeçleri düşürür. YALNIZ sorgu dizesi için — gövdede
        kullanılmaz: orada `None` gerçek bir değerdir (bkz. `DEVICE_LOCKS`)."""
        params = {key: value for key, value in filters.items() if value not in (None, "")}
        return params or None

    @staticmethod
    def _items(payload: Any) -> list[dict[str, Any]]:
        """Liste yanıtının zarfını açar.

        Sözleşme §2 liste yanıtları için bir zarf SABİTLEMİYOR (yalnız alan
        adlarının snake_case olduğunu söylüyor). Bu yüzden iki biçim de
        kabul edilir: düz dizi ve `{"data": [...]}`. Üçüncü bir ad
        (`items`, `rows`) UYDURULMAZ — gelirse liste boş döner ve eksik
        rapor edilir, sessizce başka bir alan okunmaz.
        """
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, list):
                return [dict(row) for row in data if isinstance(row, dict)]
        return []

    @staticmethod
    def _object(payload: Any) -> dict[str, Any]:
        """Tekil kaydın zarfını açar; `{"data": {...}}` de düz sözlük de olur."""
        if isinstance(payload, dict):
            data = payload.get("data")
            return dict(data) if isinstance(data, dict) else dict(payload)
        return {}

    @staticmethod
    def _dry_run_aware(path: str) -> bool:
        return any(pattern.match(path) for pattern in _DRY_RUN_AWARE)

    @staticmethod
    def _reason_optional(verb: str, path: str) -> bool:
        """Bu `(fiil, yol)` gerekçe istemiyor mu — defterden okunur.

        Defterde OLMAYAN her yazma gerekçe ister. Kilidin güvenli tarafı bu
        yöndedir: kaydı unutulan uç 422 değil `reason_required` alır ve hatayı
        yazan kişi ilk denemede görür.
        """
        return any(method == verb and pattern.match(path)
                   for method, pattern in _REASON_OPTIONAL)

    @staticmethod
    def _csv(value: Any) -> str:
        """Virgüllü süzgeç değeri. Liste de düz metin de kabul edilir.

        Sözleşme birkaç süzgeci virgüllü liste olarak tanımlıyor
        (`status`, `level`, `source`, `result`). Ekranın her seferinde
        `",".join(...)` yazması, boş elemanı ve baştaki virgülü yirmi yerde
        yeniden unutması demekti.
        """
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list | tuple | set):
            return ",".join(str(item).strip() for item in value if str(item).strip())
        return str(value)

    @staticmethod
    def _flag(value: bool | None) -> str:
        """Sorgu dizesindeki üç değerli bayrak: `true` · `false` · yok.

        Sorgu dizesinde boolean ancak METİN olarak ifade edilebilir. `None`
        boş metin döner ve `_query` onu düşürür — "süzgeç yok" ile "false"
        farklı şeyler (`resolved`, `published`, `sold_out` üçünde de).
        """
        return "" if value is None else ("true" if value else "false")

    def _paging(self, page: Any, per_page: Any) -> dict[str, int]:
        """`page` + `per_page`; boyut verilmezse ayardaki varsayılan."""
        return page_params(page, self._page_size if per_page is None else per_page)

    async def _list(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Liste ucunu `{"items": [...], "meta": {...}}` biçiminde açar.

        `meta` sayfalı uçlarda `page/per_page/total/last_page`, sayfalanmayan
        uçlarda özet alanları (`sms/log` → `segment_total`, `cms/posts` →
        `categories`) taşır; boşsa boş sözlüktür. Ayrımı `page` anahtarının
        bulunup bulunmaması verir (`00-genel.md` §5).
        """
        rows, meta = envelope(await self._request("GET", path, params=params or None))
        return {"items": rows, "meta": meta}

    async def _list_all(
        self, path: str, params: dict[str, Any] | None = None, *, max_items: int | None = None
    ) -> dict[str, Any]:
        """Bütün sayfaları sırayla toplar — yalnız REFERANS listeler için.

        Sipariş ya da müşteri listesinde kullanılmaz: on bin kaydı belleğe
        almak, ekranın göstermediği veriyi taşımak ve hız kovasını tek
        ekranda boşaltmak olurdu.
        """
        async def fetch(page: int, per_page: int) -> Any:
            return await self._request(
                "GET", path, params={**(params or {}), **page_params(page, per_page)}
            )

        return await collect_all(fetch, max_items=max_items or self._max_items)

    async def _cached(self, key: str, loader: Callable[[], Any]) -> Any:
        """Referans okuması — L1 önbellekli (varsayılan 900 sn).

        `loader` bir eşyordam üretmeli. Önbellek YALNIZCA referans veri
        içindir; kuralın tamamı `cache.py` başlığında.
        """
        hit = self._reference.get(key)
        if hit is not None:
            return hit
        return self._reference.put(key, await loader())

    def forget_reference(self, prefix: str = "") -> None:
        """Referans önbelleğini düşürür. Yazan metotlar kendi dalını çağırır."""
        self._reference.drop(prefix)

    @staticmethod
    def _document(response: httpx.Response) -> dict[str, Any]:
        """JSON OLMAYAN yanıtın zarfı — CSV dışa aktarım ve fatura HTML'i.

        İki uç sözleşmede açıkça "yanıt JSON değildir" diyor
        (`orders.md` → `GET /export`, `invoices.md` → `GET /{id}/html`) ve
        taşıdıkları bilgi gövdeye SIĞMIYOR: satır sayısı ve kesilme bilgisi
        `X-Total-Rows` / `X-Truncated` başlıklarında, dosya adı
        `Content-Disposition` içinde geliyor. Ham baytı döndürüp başlıkları
        atmak, "dosya kesildi mi" sorusunu cevapsız bırakırdı.

        `content` BAYTTIR ve tek doğru kaynaktır: CSV dosyası UTF-8 BOM ile
        başlıyor (`EF BB BF`) ve diske o baytlar yazılmalı. `text` yalnız
        görüntüleme kolaylığıdır.
        """
        kind = str(response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        found = _FILENAME.search(str(response.headers.get("Content-Disposition") or ""))
        rows = str(response.headers.get("X-Total-Rows") or "").strip()
        return {
            "content_type": kind,
            "status": response.status_code,
            "filename": found.group(1).strip('"') if found else "",
            "content": response.content,
            "text": response.text if kind.startswith("text/") else "",
            "bytes": len(response.content),
            "total_rows": int(rows) if rows.isdigit() else None,
            "truncated": str(response.headers.get("X-Truncated") or "").strip().lower()
            in ("1", "true", "yes"),
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        reason: str = "",
        actor: str = "",
        dry_run: bool | None = None,
        action: str = "",
        reason_max: int = 0,
        raw: bool = False,
        audit_body: dict[str, Any] | None = None,
    ) -> Any:
        verb = method.upper()
        if verb == "GET":
            return await self._send(verb, path, params=params, raw=raw)
        return await self._write(verb, path, params=params, body=body, reason=reason,
                                 actor=actor, dry_run=dry_run, action=action,
                                 reason_max=reason_max, audit_body=audit_body)

    async def _write(
        self, verb: str, path: str, *, params: dict[str, Any] | None, body: dict[str, Any] | None,
        reason: str, actor: str, dry_run: bool | None, action: str,
        reason_max: int = 0, audit_body: dict[str, Any] | None = None,
    ) -> Any:
        """Yazma zinciri: gerekçe → aktör → denetim izi → acil fren → kuru prova → istek."""
        reason = str(reason or "").strip()
        actor = str(actor or "").strip()

        # Gerekçe UÇ BAŞINA istenir; defterde olmayan her yol ister
        # (`_REASON_OPTIONAL` başlığındaki gerekçe). `require_reason` ayarı
        # bunun ÜSTÜNDEKİ küresel şalterdir ve kapatıldığında hiçbir uçta
        # gerekçe aranmaz.
        optional = self._reason_optional(verb, path)
        if self._require_reason and not optional and len(reason) < MIN_REASON:
            raise BldApiError(
                "Gerekçe zorunlu: bu işlem müşteriye görünür hâle geliyor ya da geri "
                f"alınması zor, bu yüzden en az {MIN_REASON} karakterlik bir gerekçeyle "
                "kayda geçer (sözleşme §3).",
                code="reason_required",
            )
        # ÜST SINIR MUAF UÇTA DA GEÇERLİ: gerekçe isteğe bağlı olduğunda bile
        # sunucunun kolon genişliği değişmiyor ve taşan metin 422 ile dönerdi.
        if reason_max and len(reason) > reason_max:
            raise BldApiError(
                f"Gerekçe en çok {reason_max} karakter olabilir; {len(reason)} karakter "
                "verildi. Sunucu bu uçta uzun gerekçeyi reddeder (sözleşme §2.5).",
                code="reason_required",
            )
        if self._require_reason and not actor:
            # Sözleşme §3 aktörü de ZORUNLU sayıyor: revizyon kaydında
            # `created_by_staff` = "Kontrol Merkezi" + aktör yazılıyor ve
            # kasadan mı merkezden mi yapıldığı ancak böyle ayrılıyor.
            # Sunucuya boş göndermek 422 ile dönerdi; burada söylemek daha
            # anlaşılır ve hız kovasından pay harcamaz.
            #
            # MUAFİYET DEFTERİ AKTÖRÜ KAPSAMAZ: seyrekleşen soru "neden",
            # "kim" değil. Gerekçesiz bir kalem eklemesi bile kimin yaptığını
            # kayda geçirir.
            raise BldApiError(
                "İşlemi yapan kişinin adı (actor) zorunlu: denetim izinde "
                "kasadan mı merkezden mi yapıldığı buna göre ayrılır (sözleşme §3).",
                code="actor_required",
            )
        if actor and len(actor) > ACTOR_MAX:
            raise BldApiError(
                f"Aktör adı en çok {ACTOR_MAX} karakter olabilir; {len(actor)} karakter "
                "verildi. Sunucu bu alanı 422 ile reddeder (sözleşme §3).",
                code="actor_required",
            )

        dry = self._dry_run_default if dry_run is None else bool(dry_run)
        request_id = AuditTrail.new_request_id()

        # Gerekçe ve aktör GÖVDEYE konur (sözleşme §3). Başlıkla taşınmaz:
        # sözleşme yalnız üç imza başlığı tanımlıyor, dördüncüsü uydurma olurdu.
        #
        # GEREKÇE İSTEMEYEN UÇTA BOŞ GEREKÇE GÖVDEYE HİÇ KONMAZ. `reason: ""`
        # göndermek "gerekçe verildi ve boştu" der; alanı hiç göndermemek
        # "bu uçta gerekçe sorulmadı" der. Sunucu ikincisini bekliyor
        # (`sometimes|nullable`) ve ayrım denetim izinde de görünüyor.
        # ELLE VERİLEN GEREKÇE KORUNUR: muaf bir uca yine de gerekçe geçen
        # çağrının notunu sessizce düşürmek, yazılan bilgiyi yok etmek olurdu.
        payload: dict[str, Any] = dict(body or {})
        if reason or not optional:
            payload["reason"] = reason
        payload["actor"] = actor

        # DENETİM İZİNE GİDEN GÖVDE, GÖNDERİLENDEN AYRI OLABİLİR. Görsel
        # yükleme gövdesi megabaytlık base64 taşıyor ve sözleşme §8.2 onu
        # denetime yazmayı açıkça yasaklıyor ("yalnız bytes ve mime"). Aynı
        # kural yerel iz için de geçerli: 4 KB'lik base64 parçaları, izi
        # okunamaz ve tabloyu yönetilemez kılardı.
        logged = payload if audit_body is None else {**audit_body, "reason": reason,
                                                     "actor": actor}
        await self._audit.before(
            request_id=request_id, method=verb, path=path, action=action or path,
            reason=reason, actor=actor, dry_run=dry, body=logged,
        )

        if self._read_only:
            await self._audit.after(request_id, result="blocked")
            raise BldApiError(
                "Acil fren açık: BLD sunucusuna yazma kapalı, istek gönderilmedi. "
                "Ayar: modules.bld_api.read_only",
                code="read_only",
            )

        if dry and not self._dry_run_aware(path):
            # Sözleşmede olmayan yol: `dry_run` bayrağını Laravel sessizce yok
            # sayar ve "prova" gerçek yazma olurdu.
            await self._audit.after(request_id, result="dry_run")
            return {
                "ok": True, "dry_run": True, "sent": False, "request_id": request_id,
                "method": verb, "path": path, "body": mask_mapping(payload),
                "message": "Kuru prova: bu uç sözleşmede tanımlı değil, istek "
                           "gönderilmedi. Uygulamak için dry_run=False verin.",
            }

        if dry:
            payload["dry_run"] = True

        try:
            result = await self._send(verb, path, params=params, body=payload)
        except BldApiError as failure:
            await self._audit.after(request_id, result=f"error:{failure.code}",
                                    status=failure.status)
            raise
        await self._audit.after(request_id, result="ok", status=200)
        if isinstance(result, dict):
            result.setdefault("dry_run", dry)
        return result

    async def _send(
        self, verb: str, path: str, *, params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None, raw: bool = False,
    ) -> Any:
        if not self._base:
            raise BldApiError(
                "BLD sunucusunun adresi tanımsız; istek gönderilmedi. config/local.yaml "
                "içine modules.bld_api.base_url yazılmalı.",
                code="config_missing",
            )
        secret = await self._control_secret()

        url = f"{self._base}{path}"
        # İmzalanan yol, GÖNDERİLEN yolun ta kendisi olsun diye URL'den geri
        # okunur. `base_url` yanlışlıkla bir yol parçası taşırsa (".../v1")
        # elle birleştirilen dize ile sunucunun `getPathInfo()` çıktısı
        # ayrışır ve hata "imza doğrulanamadı" olarak görünürdü.
        signed_path = httpx.URL(url).path
        content = _encode(body)

        attempts = GET_ATTEMPTS if verb == "GET" else 1
        rate_retry = 1
        attempt = 0
        response: httpx.Response | None = None

        while True:
            await self._bucket.take()

            # HER DENEME YENİDEN İMZALANIR. Nonce sunucuda 600 sn hatırlanıyor
            # (`VerifyControlSignature::NONCE_TTL_SECONDS`) ve ikinci kez kabul
            # edilmiyor; aynı başlıkları tekrar göndermek 401 "Bu istek daha
            # önce işlendi" üretirdi. Zaman damgası da tazelenir: hız kovasında
            # beklenen süre ±300 sn'lik pencereyi yiyebilir.
            headers = {
                # Belge uçları (CSV, fatura HTML) JSON döndürmüyor; `*/*`
                # olmadan bir vekil ya da Laravel'in içerik pazarlığı isteği
                # 406 ile kesebilirdi.
                "Accept": "*/*" if raw else "application/json",
                **self._signature_headers(secret, verb, signed_path, content),
            }
            if content:
                headers["Content-Type"] = "application/json"

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport, follow_redirects=False
                ) as client:
                    # `content=` HAM BAYT gönderir. `json=` verilseydi httpx
                    # gövdeyi yeniden serileştirir, imza başka bir baytı
                    # imzalamış olurdu (bkz. `_encode`).
                    response = await client.request(
                        verb, url, headers=headers, params=params or None,
                        content=content if content else None,
                    )
            except httpx.TransportError as failure:
                attempt += 1
                if verb == "GET" and attempt < attempts:
                    await self._sleep(0.4 * attempt)
                    continue
                # Yazma yinelenmez: zaman aşımına uğrayan istek uzakta
                # uygulanmış olabilir ve sözleşmede idempotency anahtarı yok.
                raise BldApiError(
                    f"{verb} {path} → BLD sunucusuna ulaşılamadı: {failure}",
                    code="transport",
                ) from failure

            if response.status_code == 429 and rate_retry > 0:
                rate_retry -= 1
                await self._sleep(self._retry_after(response))
                continue

            if response.status_code >= 500 and verb == "GET":
                attempt += 1
                if attempt < attempts:
                    self._log.warning("BLD sunucusu geçici hata verdi, yineleniyor",
                                      path=path, status=response.status_code)
                    await self._sleep(0.4 * attempt)
                    continue
            break

        if response.status_code >= 400:
            self._fail(verb, path, response)
        if raw:
            return self._document(response)
        return response.json() if response.content else None

    def _signature_headers(self, secret: str, verb: str, path: str,
                           content: bytes) -> dict[str, str]:
        """Sözleşme §1'in üç başlığı. Nonce her istekte yeniden üretilir."""
        timestamp = int(time.time())
        # 16 bayt → 32 karakter hex: sunucunun 16-128 karakter sınırının içinde.
        nonce = token_hex(16)
        canonical = canonical_payload(verb, path, timestamp, nonce, content)
        return {
            "X-Control-Timestamp": str(timestamp),
            "X-Control-Nonce": nonce,
            "X-Control-Signature": sign(secret, canonical),
        }

    @staticmethod
    def _retry_after(response: httpx.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After") or 1.0)
        except ValueError:
            wait = 1.0
        return min(max(wait, 0.1), RETRY_AFTER_CAP)

    def _fail(self, verb: str, path: str, response: httpx.Response) -> None:
        status = response.status_code
        message = self._safe(response.text[:300])
        try:
            data = response.json()
        except ValueError:
            data = None
        # Sözleşme §1: hata biçimi `{"error": {"code", "message", "details"}}`.
        # Laravel'in KENDİ 404'ü ise `error` alanında düz METİN taşır ya da
        # hiç JSON döndürmez. Aradaki fark "istek denetleyiciye ULAŞTI mı"
        # sorusunun cevabıdır ve aşağıda 404'ü ikiye ayırmak için kullanılır.
        inner = data.get("error") if isinstance(data, dict) else None
        reached_controller = isinstance(inner, dict)
        if reached_controller and inner.get("message"):
            message = self._safe(str(inner["message"]))
        elif isinstance(data, dict):
            for key in ("message", "error", "detail"):
                if data.get(key):
                    message = self._safe(str(data[key]))
                    break

        if status == 401:
            # Sır yanlış olabilir; bir sonraki çağrı kasadan yeniden okusun
            # (yönetici local.yaml'ı düzeltince yeniden başlatma gerekmesin).
            self._secret = None
            raise BldApiError(
                f"BLD kontrol imzası reddedildi: {message} Üç olası sebep var — "
                f"kasadaki {SECRET_KEY} sunucudaki BLD_CONTROL_SECRET ile aynı değil, "
                f"iki makinenin saati ±{SIGNATURE_WINDOW_SECONDS} sn'den fazla kaymış, "
                "ya da aynı istek yeniden oynatıldı.",
                status=status, code="unauthorized",
            )
        if status == 403:
            raise BldApiError(
                f"BLD sunucusu bu işleme izin vermedi ({verb} {path}): {message}",
                status=status, code="forbidden",
            )
        if status == 404 and reached_controller:
            # UÇ VAR, KAYIT YOK. İkisini ayırmak şart: "uç yayında değil"
            # demek, aslında var olan bir ekranı süresiz kapalı gösterirdi.
            raise BldApiError(f"Kayıt bulunamadı ({verb} {path}): {message}",
                              status=status, code="not_found")
        if status in (404, 405):
            # 405, "uç yok"un SESSİZ HÂLİDİR: Laravel yolu tanıyıp yalnız
            # metodu tanımadığında 404 değil 405 döndürür. İkisi de aynı şeyi
            # anlatır — kontrol uçları sunucuya henüz dağıtılmamış.
            raise BldApiError(
                f"BLD kontrol ucu sunucuda yayında değil: {verb} {path}. Sunucu "
                "tarafındaki eklenti güncellenince bu ekran kendiliğinden çalışacak.",
                status=status, code="control_endpoint_missing",
            )
        if status == 409:
            raise BldApiError(f"BLD sunucusu bu işlemi çakışma yüzünden reddetti: {message}",
                              status=status, code="conflict")
        if status == 422:
            raise BldApiError(f"BLD sunucusu isteği doğrulayamadı: {message}",
                              status=status, code="validation")
        if status == 429:
            raise BldApiError(
                "BLD kontrol hız sınırı doldu (saatte 1200 istek). Biraz sonra "
                "yeniden deneyin.",
                status=status, code="rate_limited",
            )
        if status >= 500:
            raise BldApiError(f"BLD sunucusu hata verdi ({status}): {message}",
                              status=status, code="server")
        raise BldApiError(f"{verb} {path} → {status}: {message}", status=status, code="http")

    def _safe(self, text: str) -> str:
        """Ad tabanlı maskeleme + bilinen sır değerinin silinmesi."""
        return self._scrub(mask_text(text))

    # ========================================================== 1 · ÖZET

    async def overview(self) -> dict[str, Any]:
        """Panel açılış özeti — GET /api/control/kds/overview (sözleşme §2.6).

        `{"devices": {...}, "orders": {...}, "print_jobs": {...}, "server_time": ...}`
        """
        return self._object(await self._request("GET", f"{CONTROL}/overview"))

    # ========================================================= 2 · CİHAZ

    async def devices(self) -> list[dict[str, Any]]:
        """Kasa listesi — GET /api/control/kds/devices (sözleşme §2.1).

        Her satır `device` nesnesidir: `id · name · online · last_seen_at ·
        created_at · revoked_at · pairing · health · settings ·
        settings_updated_at · pending_command_count`.

        `online` sunucuda hesaplanır (`last_seen_at >= now - 3 dk` ve iptal
        edilmemiş); burada TÜRETİLMEZ — iki ayrı hesap iki farklı cevap verir.
        Eşleme kodu listede yalnızca `pairing.usable` iken döner.
        """
        return self._items(await self._request("GET", f"{CONTROL}/devices"))

    async def create_device(self, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni kasa açar, eşleme koduyla döner — POST /api/control/kds/devices."""
        return await self._request("POST", f"{CONTROL}/devices", body={"name": name},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="create_device")

    async def rename_device(self, device_id: int, *, name: str, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Kasanın adını değiştirir — PATCH /api/control/kds/devices/{id}.

        Sözleşme §2.1: bu uç YALNIZ adı yazar.
        """
        return await self._request("PATCH", f"{CONTROL}/devices/{int(device_id)}",
                                   body={"name": name}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="rename_device")

    async def new_pairing_code(self, device_id: int, *, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni eşleme kodu üretir (10 dk geçerli) —
        POST /api/control/kds/devices/{id}/pairing-code."""
        return await self._request("POST", f"{CONTROL}/devices/{int(device_id)}/pairing-code",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="new_pairing_code")

    async def revoke_device(self, device_id: int, *, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Kasayı iptal eder — POST /api/control/kds/devices/{id}/revoke.

        SATIR SİLİNMEZ: iptal edilen kasa bir daha eşleşemez, geçmişi durur.
        Gövdedeki `reason` alanı zaten sözleşme §3'ün gerekçesidir; ayrı bir
        alan gönderilmez.
        """
        return await self._request("POST", f"{CONTROL}/devices/{int(device_id)}/revoke",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="revoke_device")

    async def update_device_settings(self, device_id: int, *, settings: dict[str, Any],
                                     reason: str, actor: str,
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Yönetilen ayarları KISMİ yazar — PATCH /api/control/kds/devices/{id}/settings.

        `settings` sözleşme §2.2'deki 23 anahtar + K-22 §1'in `disabled_sound_events`
        anahtarından oluşur (toplam 24); tanınmayan anahtar için istek HİÇ
        GÖNDERİLMEZ. Sebep: Laravel tanımadığı alanı sessizce yok sayar, ekran
        "kaydedildi" der ve değer hiçbir yere yazılmamış olur.

        `None` DEĞERLER KORUNUR. Kilit alanlarında `null` "yönetici dokunmadı"
        demektir (§2.2) ve `false`tan farklıdır: `null` serbest bırakır,
        `false` kilitler. `None`'ları düşürmek bir kilidi kaldırmayı sessizce
        imkânsız kılardı.

        GÖVDE BİÇİMİ — sözleşmede yazmıyor. Ayarlar `settings` nesnesinin
        içine konur; §2.1'deki `device` nesnesi de onları orada taşıyor ve
        §3'ün zorunlu `reason`/`actor` alanlarıyla karışma ihtimali böyle
        ortadan kalkıyor. Sunucu tarafı yazılırken teyit edilmelidir.
        """
        if not settings:
            raise BldApiError("En az bir ayar anahtarı verilmeli.", code="payload")
        unknown = [key for key in settings if key not in MANAGED_SETTINGS]
        if unknown:
            raise BldApiError(
                f"Tanınmayan ayar anahtarı: {', '.join(sorted(unknown))}. Yönetilen "
                f"ayarlar {len(MANAGED_SETTINGS)} tanedir (sözleşme §2.2); istek "
                "gönderilmedi.",
                code="payload",
            )
        return await self._request(
            "PATCH", f"{CONTROL}/devices/{int(device_id)}/settings",
            body={"settings": dict(settings)}, reason=reason, actor=actor,
            dry_run=dry_run, action="update_device_settings",
        )

    # ========================================================= 3 · KOMUT

    async def device_commands(self, device_id: int) -> list[dict[str, Any]]:
        """Son 50 komut — GET /api/control/kds/devices/{id}/commands.

        Satır: `{id, command, payload, created_at, delivered_at, executed_at,
        succeeded, result}`. Üç damga birden "kuyruğa girdi / kasaya ulaştı /
        çalıştı" ayrımını verir; ikisi boşsa komut hâlâ bekliyordur.
        """
        return self._items(
            await self._request("GET", f"{CONTROL}/devices/{int(device_id)}/commands")
        )

    async def send_command(self, device_id: int, *, command: str,
                           payload: dict[str, Any] | None = None, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Komutu kuyruğa atar — POST /api/control/kds/devices/{id}/commands.

        `command` ∈ `test_receipt · reprint · clear_failed · silence_alarm ·
        restart · update · unpair · clear_queue` (`KitchenCommand::ALL`,
        sözleşme §2.3 + K-22 §2). Listede olmayan bir ad için istek gönderilmez.

        Son üçü YÜKSÜZDÜR. `update` kasadaki `.deb` akışını çalıştırır ve
        kurulum başarısızsa komut `succeeded=false` döner — KASA ESKİ SÜRÜMDE
        ÇALIŞMAYA DEVAM EDER, yani başarısız güncelleme mutfağı durdurmaz.
        `unpair` cihaz token'ını siler; kasa eşleme ekranına döner ve yeni bir
        eşleme kodu girilene kadar sipariş göremez.

        `reprint` yükü: `{"order_id": int, "type": "mutfak"|"musteri"|"kurye"}`.
        """
        if command not in COMMANDS:
            raise BldApiError(
                f"Tanınmayan komut: {command}. Geçerli komutlar: {', '.join(COMMANDS)}.",
                code="payload",
            )
        body: dict[str, Any] = {"command": command}
        if command == "reprint":
            kind = str((payload or {}).get("type") or "")
            if kind not in REPRINT_TYPES:
                raise BldApiError(
                    f"reprint komutu için fiş türü zorunlu: {', '.join(REPRINT_TYPES)}.",
                    code="payload",
                )
        if payload is not None:
            body["payload"] = dict(payload)
        return await self._request("POST", f"{CONTROL}/devices/{int(device_id)}/commands",
                                   body=body, reason=reason, actor=actor, dry_run=dry_run,
                                   action=f"command:{command}")

    # ==================================================== 4 · FİŞ KUYRUĞU

    async def print_jobs(self, *, device_id: int | None = None, order_id: int | None = None,
                         limit: int | None = None) -> list[dict[str, Any]]:
        """Basılan fişlerin DENETİM KAYDI — GET /api/control/kds/print-jobs.

        Satır: `{id, order_id, order_number, type, revision, printed_at,
        device_id, device_name}`; en yeni önce.

        BU BİR KUYRUK DEĞİLDİR. Kasanın kendi disk kuyruğu sunucuda YOK
        (sözleşme §2.4); burada yalnız basılmış işlerin izi durur. "Bekleyen
        iş" sayısı cihaz sağlığından okunur:
        `device["health"]["print_queue_pending"]` ve `print_queue_failed`.
        Bu tabloyu kuyruk sanan bir ekran, bekleyen işi hiç göremezdi.
        """
        return self._items(await self._request(
            "GET", f"{CONTROL}/print-jobs",
            params=self._query(device_id=device_id, order_id=order_id, limit=limit),
        ))

    # ====================================================== 5 · SİPARİŞ

    async def orders(self, *, include_completed: bool = False,
                     since: str = "") -> list[dict[str, Any]]:
        """Aktif siparişler — GET /api/control/kds/orders (sözleşme §2.5).

        `include_completed=True` tamamlananları da katar; `since` ISO-8601
        Zulu damgasıdır.
        """
        return self._items(await self._request(
            "GET", f"{CONTROL}/orders",
            params=self._query(include_completed="true" if include_completed else None,
                               since=since),
        ))

    async def order(self, order_id: int) -> dict[str, Any]:
        """Tek sipariş, düzenlenebilir görünüm (`OrderPresenter::editable`) —
        GET /api/control/kds/orders/{id}."""
        return self._object(await self._request("GET", f"{CONTROL}/orders/{int(order_id)}"))

    async def order_revisions(self, order_id: int) -> list[dict[str, Any]]:
        """Revizyon geçmişi — GET /api/control/kds/orders/{id}/revisions."""
        return self._items(
            await self._request("GET", f"{CONTROL}/orders/{int(order_id)}/revisions")
        )

    async def create_order_revision(
        self, order_id: int, *, items: list[dict[str, Any]], reason: str, actor: str,
        note: str = "", requested_at: str = "", customer_note: str = "",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Sipariş revizyonu yazar — POST /api/control/kds/orders/{id}/revisions.

        Gövde `/api/kitchen/orders/{id}/revisions` ile BİREBİR AYNIDIR
        (sözleşme §2.5) ve sunucuda aynı `OrderEditor` yeniden kullanılır.

        `items` KALEM FARKI DEĞİL, TAM LİSTEDİR: gönderilen liste siparişin
        yeni hâlidir. Boş liste reddedilir — "hepsini sil" anlamına gelirdi
        ve iptal işi durum ucunun işidir. Geçit boş listeyi istek çıkmadan
        keser.

        `created_by_device_id` sunucuda NULL kalır, `created_by_staff`
        "Kontrol Merkezi" + `actor` olur; kasadan mı merkezden mi yapıldığı
        denetim izinde böyle ayrılır.
        """
        if not items:
            raise BldApiError(
                "Revizyon kalem listesi boş olamaz: gönderilen liste siparişin TAM "
                "hâlidir, kalem farkı değil (sözleşme §2.5).",
                code="payload",
            )
        body: dict[str, Any] = {"items": [dict(item) for item in items]}
        if note:
            body["note"] = note
        if requested_at:
            body["requested_at"] = requested_at
        if customer_note:
            body["customer_note"] = customer_note
        return await self._write(
            "POST", f"{CONTROL}/orders/{int(order_id)}/revisions", params=None, body=body,
            reason=reason, actor=actor, dry_run=dry_run, action="create_order_revision",
            reason_max=MAX_REASON_STRICT,
        )

    async def set_order_status(self, order_id: int, *, status: str, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Sipariş durumunu değiştirir — POST /api/control/kds/orders/{id}/status.

        Sunucuda `OrderStatusTransition` yeniden kullanılır; geçerli durumlar
        ve izinli geçişler ORADA tanımlıdır. Sözleşme listeyi saymadığı için
        burada kopyalanmaz — kopyalanan liste sunucudakiyle sessizce ayrışırdı.
        Geçersiz durum `validation` koduyla döner.
        """
        return await self._write(
            "POST", f"{CONTROL}/orders/{int(order_id)}/status", params=None,
            body={"status": status}, reason=reason, actor=actor, dry_run=dry_run,
            action="set_order_status", reason_max=MAX_REASON_STRICT,
        )

    # ==================================================== PANEL YARDIMCILARI

    @staticmethod
    def _date(value: Any) -> str:
        """Yol parçasındaki tarihi doğrular: `YYYY-MM-DD` (`00-genel.md` §6).

        Biçim BURADA zorlanır çünkü yol kuru prova defterine bu kalıpla
        kayıtlı. `2026-8-1` gibi bir değer desene düşmez ve kuru prova
        "istek gönderilmedi" diye dönerdi — yani bir tarih yazım hatası,
        "bu uç sözleşmede yok" gibi görünürdü.
        """
        text = str(value or "").strip()
        if not _DATE_ONLY.match(text):
            raise BldApiError(
                f"Tarih `YYYY-MM-DD` biçiminde olmalı; '{text}' verildi.",
                code="payload",
            )
        return text

    @staticmethod
    def _patch(**fields: Any) -> dict[str, Any]:
        """Kısmi yazma gövdesi: `UNSET` alanlar gövdeye KONMAZ, `None` konur.

        Ayrım sözleşmenin kendisi (bkz. `_Unset`). Boş gövde reddedilir:
        yalnız `reason`/`actor` taşıyan bir `PATCH`, hiçbir şey değiştirmeden
        denetim izine satır yazardı.
        """
        body = {key: value for key, value in fields.items()
                if not isinstance(value, _Unset)}
        if not body:
            raise BldApiError(
                "En az bir alan verilmeli: gönderilmeyen alan değişmez, bu yüzden "
                "boş bir güncelleme isteği hiçbir şey yapmaz.",
                code="payload",
            )
        return body

    def _read_actor(self, actor: str) -> str:
        """KVKK okuma denetimi için zorunlu `actor` (`00-genel.md` §9).

        `control/customers/*` altındaki her `GET` bunu sorgu dizesinde ister
        ve sunucu eksikse 422 verir; okumanın kendisi denetim izine
        `customer.read` olarak düşüyor. Sorgu dizesi imzaya girmediği için
        alan kriptografik olarak bağlanmaz — sınır sözleşmede yazılı ve
        bilinçli.
        """
        text = str(actor or "").strip()
        if len(text) < ACTOR_MIN or len(text) > ACTOR_MAX:
            raise BldApiError(
                "Müşteri kayıtlarını okumak için işlemi yapan kişinin adı (actor) "
                f"zorunlu, {ACTOR_MIN}-{ACTOR_MAX} karakter: bu uçlarda OKUMALAR da "
                "denetim izine düşer (KVKK, sözleşme §9).",
                code="actor_required",
            )
        return text

    # ==================================================== 6 · GÜNLÜK MENÜ

    # Kuru prova defterine kayıt, alanın metotlarının HEMEN ÜSTÜNDE yapılır:
    # deseni unutmak, o alanın bütün yazmalarını sessiz bir no-op'a çevirir.
    register_dry_run(
        rf"^{MENU}/days$",
        rf"^{MENU}/days/{_DATE}$",
        rf"^{MENU}/days/{_DATE}/(publish|unpublish|duplicate|stock)$",
        rf"^{MENU}/days/{_DATE}/items$",
        rf"^{MENU}/days/{_DATE}/items/\d+$",
    )

    # GEREKÇE MUAFİYETİ — yalnız `control/menu` alanında, uç başına.
    #
    # Ölçüt: işlem müşteriye GÖRÜNÜR HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu.
    # Taslak kurmak ikisi de değildir; yayınlamak ikisi de.
    #
    # Muaf (aşağıda):  POST days · PATCH days/{date} · POST/PATCH/DELETE items
    #                  · PUT stock
    # Gerekçe ister:   POST publish (müşteriye açar) · POST unpublish (satıştan
    #                  düşürür) · DELETE days/{date} (gün ve TÜM kalemleri
    #                  geri alınamaz biçimde gider) · POST duplicate (toplu ve
    #                  `overwrite` ile bir taslağın üzerine yazabilir).
    #
    # `DELETE days/{date}` ile `PATCH days/{date}` AYNI YOLDUR, ayrı fiillerdir:
    # muafiyet yalnız `PATCH`e verilir. Defter bu yüzden fiili de tutuyor.
    register_reason_optional(
        ("POST", rf"^{MENU}/days$"),
        ("PATCH", rf"^{MENU}/days/{_DATE}$"),
        ("POST", rf"^{MENU}/days/{_DATE}/items$"),
        ("PATCH", rf"^{MENU}/days/{_DATE}/items/\d+$"),
        ("DELETE", rf"^{MENU}/days/{_DATE}/items/\d+$"),
        ("PUT", rf"^{MENU}/days/{_DATE}/stock$"),
    )

    async def menu_calendar(self, *, date_from: str, date_to: str,
                            location_id: int | None = None) -> dict[str, Any]:
        """Takvim ızgarası — GET /api/control/menu/calendar (`menu.md`).

        Aralıktaki HER GÜN döner, menüsü olmayanlar `id: null` ile: eksik
        günü atlamak, ızgarayı çizen ekranı boşlukları kendi hesaplamaya
        zorlardı. Aralık en çok 92 gün; aşarsa sunucu 422 verir.

        Parametre adları `date_from` / `date_to`, çünkü `from` Python'da
        ayrılmış bir sözcüktür; telde `from` ve `to` olarak gider.

        `remaining_total` yalnız tavan doluyken sayıdır; `null` "tavan
        konmamış" demektir ve sıfırla ("doldu") karıştırılmamalıdır.
        """
        return await self._list(f"{MENU}/calendar", self._query(**{
            "from": self._date(date_from), "to": self._date(date_to),
            "location_id": location_id,
        }))

    async def menu_day(self, date: str, *, location_id: int | None = None) -> dict[str, Any]:
        """Tek günün tam menüsü, kalemler dâhil — GET /menu/days/{date}.

        O güne menü yoksa `not_found` döner: boş bir gövde, "menü yok" ile
        "boş menü var"ı ayırt edilemez kılardı.
        """
        return self._object(await self._request(
            "GET", f"{MENU}/days/{self._date(date)}",
            params=self._query(location_id=location_id),
        ))

    async def create_menu_day(
        self, *, date: str, title: str | None = None, description: str | None = None,
        internal_note: str | None = None, package_price_kurus: int | None = None,
        components_sellable: bool = True, cutoff_time: str | None = None,
        capacity_total: int | None = None, items: list[dict[str, Any]] | None = None,
        location_id: int | None = None, reason: str = "", actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Yeni menü günü — POST /menu/days. **Gerekçe İSTEMEZ.**

        Taslak kurmak bir taahhüt değildir: gün HER ZAMAN `draft` doğar,
        müşteriye görünmez ve yayın ayrı bir eylemdir. Gerekçe orada istenir;
        burada verilirse gövdeye konur, verilmezse alan hiç gönderilmez.

        `items` boş bırakılabilir (önce günü kur, kalemleri sonra ekle).
        Aynı `(location_id, date)` varsa `conflict`.

        `package_price_kurus` **kuruştur** ve verilirse sıfırdan büyük
        olmalıdır; `null` "paket satılmıyor" demektir.
        """
        body: dict[str, Any] = {
            "date": self._date(date), "title": title, "description": description,
            "internal_note": internal_note, "package_price_kurus": package_price_kurus,
            "components_sellable": bool(components_sellable), "cutoff_time": cutoff_time,
            "capacity_total": capacity_total,
            "items": [dict(item) for item in (items or [])],
        }
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("POST", f"{MENU}/days", body=body, reason=reason,
                                   actor=actor, dry_run=dry_run, action="menu.day.create",
                                   reason_max=MAX_REASON)

    async def update_menu_day(
        self, date: str, *, title: Any = UNSET, description: Any = UNSET,
        internal_note: Any = UNSET, package_price_kurus: Any = UNSET,
        components_sellable: Any = UNSET, cutoff_time: Any = UNSET,
        capacity_total: Any = UNSET, image_path: Any = UNSET,
        location_id: int | None = None, reason: str = "", actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Gün başlığı/fiyatı/notu — PATCH /menu/days/{date}. **Kısmi.**

        **Gerekçe İSTEMEZ** (`DELETE` aynı yolda ister — defter fiili de tutar).

        `date`, `location_id` ve `status` YAZILAMAZ: günü taşımak yeni gün
        kurup eskisini silmektir, durum değişimi kendi uçlarındadır.
        Yayınlanmış güne yazmak serbesttir; kesim saati geçmiş güne yazmak
        `conflict` alır.
        """
        body = self._patch(
            title=title, description=description, internal_note=internal_note,
            package_price_kurus=package_price_kurus,
            components_sellable=components_sellable, cutoff_time=cutoff_time,
            capacity_total=capacity_total, image_path=image_path,
        )
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("PATCH", f"{MENU}/days/{self._date(date)}", body=body,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="menu.day.update", reason_max=MAX_REASON)

    async def delete_menu_day(self, date: str, *, location_id: int | None = None,
                              reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Günü siler — DELETE /menu/days/{date}. Yalnız **taslak** günler.

        **GEREKÇE İSTER.** Gün kaydı ve TÜM kalemleri (fiyat geçersiz
        kılmaları, tavanları, etiketleri) birlikte gider ve geri getirmenin
        yolu hepsini elle yeniden girmektir; taslak bir haftalık menü tek
        çağrıda yok edilebilir.

        Yayınlanmış gün ya da o güne sipariş girmiş gün `conflict` verir.
        Kalemler günle birlikte gider: bağımsız bir varlık değiller.
        """
        return await self._request(
            "DELETE", f"{MENU}/days/{self._date(date)}",
            body=self._query(location_id=location_id) or {}, reason=reason, actor=actor,
            dry_run=dry_run, action="menu.day.delete", reason_max=MAX_REASON,
        )

    async def publish_menu_day(self, date: str, *, location_id: int | None = None,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Günü yayınlar — POST /menu/days/{date}/publish.

        **GEREKÇE İSTER.** Politikanın kalbi bu satırdır: taslak kurmak bir
        taahhüt değil, yayınlamak taahhüttür — gün bu çağrıyla müşteriye
        görünür hâle gelir ve sipariş almaya başlar.

        Ön denetimler kuru provada da koşar: gün taslak olmalı, en az bir
        kalem bulunmalı, paket fiyatı yoksa bileşen satışı açık olmalı ve
        kalemlerin ürünleri satışta olmalı.
        """
        return await self._request(
            "POST", f"{MENU}/days/{self._date(date)}/publish",
            body=self._query(location_id=location_id) or {}, reason=reason, actor=actor,
            dry_run=dry_run, action="menu.publish", reason_max=MAX_REASON,
        )

    async def unpublish_menu_day(self, date: str, *, location_id: int | None = None,
                                 reason: str, actor: str,
                                 dry_run: bool | None = None) -> dict[str, Any]:
        """Günü taslağa çeker — POST /menu/days/{date}/unpublish.

        **GEREKÇE İSTER.** Şalter geri alınabilir ama etkisi müşteride anında
        görünür: yayındaki bir gün satış kanalından düşer. "Neden kapattık"
        sorusu ertesi gün mutlaka soruluyor.

        O güne sipariş girmişse `conflict`: satılmış bir günü gizlemek,
        siparişin bağlandığı menüyü müşteriden kaçırmak olurdu.
        """
        return await self._request(
            "POST", f"{MENU}/days/{self._date(date)}/unpublish",
            body=self._query(location_id=location_id) or {}, reason=reason, actor=actor,
            dry_run=dry_run, action="menu.unpublish", reason_max=MAX_REASON,
        )

    async def create_menu_item(
        self, date: str, *, menu_id: int, quantity: int = 1, sort_order: int | None = None,
        label: str | None = None, price_override_kurus: int | None = None,
        is_required: bool = False, sellable_alone: bool = False,
        capacity: int | None = None, location_id: int | None = None,
        reason: str = "", actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Güne kalem ekler — POST /menu/days/{date}/items. **Gerekçe İSTEMEZ.**

        Bir güne beş ürün koymak beş çağrıdır; her birinde on karakter istemek
        "düzeltme", "ok", "asdasd" üretiyordu ve denetim izini işe yaramaz
        kılıyordu (bkz. `MIN_REASON`).

        `sort_order` VERİLMEZSE GÖVDEYE KONMAZ; sunucu mevcut en büyük + 10
        atar. `null` göndermek bu davranışı tetiklemeyebilir — Laravel'in
        gördüğü şey "alan var ama boş" olurdu.

        Aynı `menu_id` zaten varsa `conflict` (tekil indeks).
        """
        body: dict[str, Any] = {
            "menu_id": int(menu_id), "quantity": int(quantity), "label": label,
            "price_override_kurus": price_override_kurus,
            "is_required": bool(is_required), "sellable_alone": bool(sellable_alone),
            "capacity": capacity,
        }
        if sort_order is not None:
            body["sort_order"] = int(sort_order)
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("POST", f"{MENU}/days/{self._date(date)}/items", body=body,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="menu.item.create", reason_max=MAX_REASON)

    async def update_menu_item(
        self, date: str, item_id: int, *, quantity: Any = UNSET, sort_order: Any = UNSET,
        label: Any = UNSET, price_override_kurus: Any = UNSET, is_required: Any = UNSET,
        sellable_alone: Any = UNSET, capacity: Any = UNSET, reason: str = "", actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Kalemi günceller — PATCH /menu/days/{date}/items/{item}. **Kısmi.**

        **Gerekçe İSTEMEZ** — yayınlanmış bir günün kaleminde bile. Bu bilinçli
        bir karardır (sahip böyle istedi) ve politikanın kendi ölçütüyle
        gerilimlidir: yayındaki bir günde fiyat ya da etiket değiştirmek
        müşteriye görünen şeyi değiştirir. Sıkılaştırma ucuzdur — defterdeki
        `PATCH items` kaydını kaldırmak yeter — ama BUGÜN İSTENMEDİ.

        `menu_id` YAZILAMAZ: ürünü değiştirmek kalemi silip yenisini
        eklemektir ve denetim izinde iki ayrı satır olarak görünmelidir.
        """
        return await self._request(
            "PATCH", f"{MENU}/days/{self._date(date)}/items/{int(item_id)}",
            body=self._patch(
                quantity=quantity, sort_order=sort_order, label=label,
                price_override_kurus=price_override_kurus, is_required=is_required,
                sellable_alone=sellable_alone, capacity=capacity,
            ),
            reason=reason, actor=actor, dry_run=dry_run, action="menu.item.update",
            reason_max=MAX_REASON,
        )

    async def delete_menu_item(self, date: str, item_id: int, *, reason: str = "",
                               actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Kalemi siler — DELETE /menu/days/{date}/items/{item}.

        **Gerekçe İSTEMEZ** — gün silmenin aksine. Ayrım şu: gün silmek gün
        kaydını ve TÜM kalemlerini birlikte götürür, tek kalem silmek ise
        menüyü kurarken yapılan olağan bir düzeltmedir ve yeniden eklemenin
        maliyeti bir seçicidir.

        Bugünkü siparişlerde kullanılmış bir kalem `conflict` verir: geçmiş
        bozulmaz (sipariş satırı kendi kopyasını taşır), engel yöneticinin
        farkında olmadan mutfağın pişirdiği bir kalemi düşürmesini önler.
        """
        return await self._request(
            "DELETE", f"{MENU}/days/{self._date(date)}/items/{int(item_id)}", body={},
            reason=reason, actor=actor, dry_run=dry_run, action="menu.item.delete",
            reason_max=MAX_REASON,
        )

    async def menu_stock(self, date: str, *,
                         location_id: int | None = None) -> dict[str, Any]:
        """Gün ve kalem stok durumu — GET /menu/days/{date}/stock.

        ÖNBELLEĞE ALINMAZ. `sold` rezerve edilmiş porsiyondur ve abonelikler
        onu önceden tutar; bir dakika bayat bir sayı, yöneticiye dolmuş bir
        günü açık gösterirdi.

        `sold_out` (mutfağın elle koyduğu işaret) ile `full` (tavan doldu)
        AYRI şeylerdir: bir ürün tavanı dolmadan da tükenmiş olabilir.
        """
        return self._object(await self._request(
            "GET", f"{MENU}/days/{self._date(date)}/stock",
            params=self._query(location_id=location_id),
        ))

    async def set_menu_stock(
        self, date: str, *, capacity_total: int | None, items: list[dict[str, Any]],
        location_id: int | None = None, reason: str = "", actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Stok tavanlarını yazar — PUT /menu/days/{date}/stock.

        **Gerekçe İSTEMEZ.** Tavan yazmak gün içinde sık tekrarlanan bir
        ayardır (malzeme bitti, tavanı düşür) ve emniyeti gerekçede değil iki
        adımlı akıştadır: kuru prova uyarıları hesaplar, uygulama jetonla
        gelir ve temel çizgiyi doğrular.

        **TAM LİSTEDİR, FARK DEĞİL.** Gönderilmeyen kalemin tavanı `null`'a
        düşer; niyet "bugünün tavan tablosu şudur"dur. Fark göndermek, iki
        sekmede açık iki yöneticinin birbirinin tavanını sessizce koruması
        demekti.

        Tavanı satılmışın altına çekmek serbesttir ve `409` vermez — malzeme
        biter, yönetici satışı kapatmak ister. Yanıt bunu `warnings` ve
        `oversold` ile açıkça söyler. Kuru prova uyarıları da hesaplar; asıl
        işi budur.
        """
        rows: list[dict[str, Any]] = []
        for item in items:
            if "item_id" not in item:
                raise BldApiError(
                    "Stok satırında `item_id` zorunlu: eksik satır, o kalemin tavanını "
                    "sessizce kaldırırdı (liste TAM listedir).",
                    code="payload",
                )
            rows.append({"item_id": int(item["item_id"]), "capacity": item.get("capacity")})
        body: dict[str, Any] = {"capacity_total": capacity_total, "items": rows}
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("PUT", f"{MENU}/days/{self._date(date)}/stock", body=body,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="menu.stock", reason_max=MAX_REASON)

    async def duplicate_menu_day(
        self, date: str, *, target_date: str, overwrite: bool = False,
        location_id: int | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Günü başka güne kopyalar — POST /menu/days/{date}/duplicate.

        **GEREKÇE İSTER.** Tek çağrıda bir günün tamamını yazar ve `overwrite`
        ile mevcut bir taslağın üzerine geçebilir: tek tıkla en çok veri
        değiştiren menü işlemi budur.

        Hedef gün HER ZAMAN `draft` doğar, kaynak yayında olsa bile.
        Görsel ve iç not kopyalanmaz: güne özgüdürler ve kopyalamak yanlış
        fotoğrafı yayınlatırdı. `overwrite` yalnız taslak bir hedefin üzerine
        yazabilir.
        """
        body: dict[str, Any] = {"target_date": self._date(target_date),
                                "overwrite": bool(overwrite)}
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("POST", f"{MENU}/days/{self._date(date)}/duplicate",
                                   body=body, reason=reason, actor=actor, dry_run=dry_run,
                                   action="menu.duplicate", reason_max=MAX_REASON)

    # ================================================= 7 · ÜRÜN KATALOĞU

    register_dry_run(
        rf"^{PRODUCTS}$",
        rf"^{PRODUCTS}/\d+$",
        rf"^{PRODUCTS}/\d+/image$",
        rf"^{PRODUCTS}/\d+/sold-out$",
        rf"^{PRODUCTS}/categories$",
        rf"^{PRODUCTS}/categories/\d+$",
    )

    async def products(
        self, *, q: str = "", category_id: int | None = None, status: str = "",
        sold_out: bool | None = None, sort: str = "", direction: str = "",
        page: int = 1, per_page: int | None = None,
    ) -> dict[str, Any]:
        """Ürün listesi (sayfalı) — GET /api/control/products.

        ÖNBELLEĞE ALINMAZ: bu yönetim listesi, kaydedilen bir ürünün hemen
        görünmesi gereken yerdir. Seçici için önbellekli sürüm
        `product_picker()`.

        `status` varsayılanı sunucuda **`all`**'dır, `active` değil: yönetimin
        ilk sorusu çoğu zaman "bu ürün nerede" biçiminde gelir ve cevabı
        "satıştan kaldırılmış"tır.

        Listede `options` boş dizi döner; dolu hâli yalnız `product()` içinde.
        """
        return await self._list(PRODUCTS, self._query(
            q=q, category_id=category_id, status=status, sold_out=self._flag(sold_out),
            sort=sort, direction=direction, **self._paging(page, per_page),
        ))

    async def product(self, menu_id: int) -> dict[str, Any]:
        """Tek ürün, seçenekleriyle — GET /products/{menu}.

        `options` SALT OKUNURDUR (sözleşme kararı): seçenek düzenlemek
        TastyIgniter admin panelinde kalıyor. `values[].id` değerleri sipariş
        revizyonundaki `option_value_ids` alanına doğrudan konur.
        """
        return self._object(await self._request("GET", f"{PRODUCTS}/{int(menu_id)}"))

    async def product_picker(self, *, only_active: bool = True) -> dict[str, Any]:
        """Seçici için ürün kataloğu — önbellekli tam tarama.

        Günlük menü kalemi eklerken açılan listeyi besler ve panel açılışında
        her ekranda yeniden istenirdi. REFERANS veridir: `menu_id → ad`
        eşlemesi haftalarca değişmez. Ürün yazan her metot bu dalı düşürür,
        yani yeni eklenen ürün ilk okumada görünür — TTL beklenmez.

        Dönüş `{items, total, pages, truncated}`; `truncated` True ise sunucuda
        daha çok kayıt var ve ekran bunu SÖYLEMELİ.
        """
        key = f"product:picker:{'active' if only_active else 'all'}"

        async def load() -> dict[str, Any]:
            return await self._list_all(PRODUCTS, {
                "status": "active" if only_active else "all", "sort": "name",
            })

        return await self._cached(key, load)

    async def create_product(
        self, *, name: str, price_kurus: int, description: str | None = None,
        minimum_qty: int = 1, priority: int = 0, status: bool = True,
        category_ids: list[int] | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Yeni ürün — POST /products.

        `price_kurus` **kuruştur** ve sıfır geçerlidir (paket bileşeni olarak
        satılan ekmek, ayran). Aynı adda ürün engellenmez: "Tavuk Sote" iki
        farklı tarifle iki ürün olabilir.
        """
        result = await self._request("POST", PRODUCTS, body={
            "name": name, "description": description, "price_kurus": int(price_kurus),
            "minimum_qty": int(minimum_qty), "priority": int(priority),
            "status": bool(status), "category_ids": [int(x) for x in (category_ids or [])],
        }, reason=reason, actor=actor, dry_run=dry_run, action="product.create",
            reason_max=MAX_REASON)
        self.forget_reference("product")
        return result

    async def update_product(
        self, menu_id: int, *, name: Any = UNSET, description: Any = UNSET,
        price_kurus: Any = UNSET, minimum_qty: Any = UNSET, priority: Any = UNSET,
        status: Any = UNSET, category_ids: Any = UNSET, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Ürünü günceller — PATCH /products/{menu}. **Kısmi.**

        `category_ids` gönderilirse **tam listedir**; pivot tablo ona
        eşitlenir. Paket ürününe (`is_package_product: true`) `price_kurus`
        yazmak `validation` verir — o ürünün fiyatı günün menüsündedir.
        """
        result = await self._request(
            "PATCH", f"{PRODUCTS}/{int(menu_id)}",
            body=self._patch(
                name=name, description=description, price_kurus=price_kurus,
                minimum_qty=minimum_qty, priority=priority, status=status,
                category_ids=category_ids,
            ),
            reason=reason, actor=actor, dry_run=dry_run, action="product.update",
            reason_max=MAX_REASON,
        )
        self.forget_reference("product")
        return result

    async def delete_product(self, menu_id: int, *, reason: str, actor: str,
                             dry_run: bool | None = None) -> dict[str, Any]:
        """Ürünü YUMUŞAK kaldırır — DELETE /products/{menu} (`menu_status = 0`).

        Satır silinmez: gerçek silme, geçmiş siparişlerin ürün bağını kırar ve
        "bu sipariş neydi" sorusunu cevapsız bırakır. Ürün yayınlanmış bir
        günlük menüde kullanılıyorsa `conflict` döner. Geri açmak
        `update_product(status=True)`'tur; ayrı bir "restore" ucu yok.
        """
        result = await self._request("DELETE", f"{PRODUCTS}/{int(menu_id)}", body={},
                                     reason=reason, actor=actor, dry_run=dry_run,
                                     action="product.delete", reason_max=MAX_REASON)
        self.forget_reference("product")
        return result

    async def set_product_image(
        self, menu_id: int, *, content: Any, filename: str, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Ürün görseli yükler — PUT /products/{menu}/image. **base64, JSON gövde.**

        Dosya İSTEK KURULMADAN ÖNCE çözülür, boyutu ve türü doğrulanır
        (`upload.py`): sınırı aşan ya da desteklenmeyen dosya için hız
        kovasından pay bile harcanmaz. Tür dosya adından değil İÇERİKTEN
        okunur — sunucu da öyle yapıyor.

        Sınır ikisinin küçüğüdür: ayar (`max_upload_mb`) ve ucun kendi sınırı
        (çözülmüş 5 MB). Multipart KULLANILMAZ; gerekçesi `upload.py`
        başlığında ve `products.md` içinde.
        """
        part = prepare_upload(
            content, filename=filename,
            max_bytes=min(self._max_upload, PRODUCT_IMAGE_MAX_BYTES),
            allowed_mimes=IMAGE_MIMES,
        )
        result = await self._request(
            "PUT", f"{PRODUCTS}/{int(menu_id)}/image", body=json_body(part), reason=reason,
            actor=actor, dry_run=dry_run, action="product.image", reason_max=MAX_REASON,
            # Denetim izine base64 İÇERİK YAZILMAZ, yalnız künye (sözleşme §8.2).
            audit_body=describe(part),
        )
        self.forget_reference("product")
        if isinstance(result, dict):
            # Kuru provada sunucu `would` döndürüyor; ne gönderdiğimizin künyesi
            # ekranda da görünsün. İÇERİK EKLENMEZ (sözleşme §8.2).
            result.setdefault("upload", describe(part))
        return result

    async def delete_product_image(self, menu_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Ürün görselini kaldırır — DELETE /products/{menu}/image.

        Görseli olmayan üründen görsel silmek HATA DEĞİLDİR: işlem sonuç
        odaklıdır, istenen son hâl zaten geçerli.
        """
        result = await self._request("DELETE", f"{PRODUCTS}/{int(menu_id)}/image", body={},
                                     reason=reason, actor=actor, dry_run=dry_run,
                                     action="product.image.delete", reason_max=MAX_REASON)
        self.forget_reference("product")
        return result

    async def mark_product_sold_out(self, menu_id: int, *, note: str | None = None,
                                    reason: str, actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Bugün için tükendi işareti — POST /products/{menu}/sold-out.

        BUGÜNE ÖZELDİR ve ertesi gün kendiliğinden düşer; kalıcı kaldırma
        `delete_product()`'tır. Normalde işareti KDS koyar; buradan da
        konabilmesinin sebebi somut: kasa çöktüğünde ya da yönetici sahada
        değilken bir ürünü satıştan çekmenin başka yolu kalmıyor.

        Zaten işaretliyse gerekçe güncellenir, `409` verilmez.
        """
        body: dict[str, Any] = {}
        if note is not None:
            body["note"] = note
        result = await self._request("POST", f"{PRODUCTS}/{int(menu_id)}/sold-out", body=body,
                                     reason=reason, actor=actor, dry_run=dry_run,
                                     action="product.sold_out", reason_max=MAX_REASON)
        self.forget_reference("product")
        return result

    async def clear_product_sold_out(self, menu_id: int, *, reason: str, actor: str,
                                     dry_run: bool | None = None) -> dict[str, Any]:
        """Tükendi işaretini kaldırır — DELETE /products/{menu}/sold-out.

        İşaret yoksa `ok: true` döner; sonuç odaklı.
        """
        result = await self._request("DELETE", f"{PRODUCTS}/{int(menu_id)}/sold-out", body={},
                                     reason=reason, actor=actor, dry_run=dry_run,
                                     action="product.sold_out.clear", reason_max=MAX_REASON)
        self.forget_reference("product")
        return result

    async def categories(self) -> dict[str, Any]:
        """Kategori listesi — GET /products/categories. **Önbellekli (L1).**

        Sayfalanmaz: kategori sayısı onlarla ifade edilir ve ekran hepsini bir
        ağaç olarak çizer. Kategori yazan metotlar dalı düşürür.
        """
        return await self._cached("category:list",
                                  lambda: self._list(f"{PRODUCTS}/categories"))

    async def create_category(
        self, *, name: str, description: str | None = None, parent_id: int | None = None,
        priority: int = 0, status: bool = True, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Yeni kategori — POST /products/categories.

        `slug` GÖNDERİLMEZ: `permalink_slug` çekirdeğin `HasPermalink`
        özelliğiyle addan üretilir. Elle slug yazdırmak, sitedeki adresin
        yönetici yazım hatasına bağlı olması demekti.
        """
        result = await self._request("POST", f"{PRODUCTS}/categories", body={
            "name": name, "description": description, "parent_id": parent_id,
            "priority": int(priority), "status": bool(status),
        }, reason=reason, actor=actor, dry_run=dry_run, action="category.create",
            reason_max=MAX_REASON)
        self.forget_reference("category")
        return result

    async def update_category(
        self, category_id: int, *, name: Any = UNSET, description: Any = UNSET,
        parent_id: Any = UNSET, priority: Any = UNSET, status: Any = UNSET,
        reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Kategoriyi günceller — PATCH /products/categories/{id}. **Kısmi.**

        `DELETE /categories/{id}` YOKTUR: kategori silmek altındaki ürünleri
        kategorisiz bırakır ve site menüsünü sessizce boşaltır. Gizlemek
        `status=False` yazmaktır.
        """
        result = await self._request(
            "PATCH", f"{PRODUCTS}/categories/{int(category_id)}",
            body=self._patch(name=name, description=description, parent_id=parent_id,
                             priority=priority, status=status),
            reason=reason, actor=actor, dry_run=dry_run, action="category.update",
            reason_max=MAX_REASON,
        )
        self.forget_reference("category")
        return result

    # ================================================== 8 · SATIŞ AYARLARI

    register_dry_run(
        rf"^{SETTINGS}/sales$",
        rf"^{SETTINGS}/ordering/(pause|resume)$",
        rf"^{SETTINGS}/closed-days$",
        rf"^{SETTINGS}/closed-days/{_DATE}$",
    )

    async def sales_settings(self, *, location_id: int | None = None) -> dict[str, Any]:
        """Satış ayarları — GET /api/control/settings/sales.

        ÖNBELLEĞE ALINMAZ ve alınmamalıdır: gövdede `ordering_enabled`,
        `paused_until` ve `busy` gibi CANLI şalterler var. "Satışı durdurdum
        ama panel hâlâ açık gösteriyor" cümlesi, önbelleğin en pahalı hâlidir.
        Değişmeyen kısım (`meta`) için `settings_reference()`.

        İKİ KESİM SAATİ VARDIR: buradaki `order_cutoff` geneldir, günün kendi
        `cutoff_time` alanı onu ezer. Birleştirme kuralı `gün ?? ayar`.
        """
        return self._object(await self._request(
            "GET", f"{SETTINGS}/sales", params=self._query(location_id=location_id)))

    async def settings_reference(self, *, location_id: int | None = None) -> dict[str, Any]:
        """Ayarın DEĞİŞMEYEN yüzü — `GET /settings/sales` yanıtının `meta`'sı.

        `available_payment_methods` ve `defaults` (yönetici alanı boşaltınca
        hangi değerin geçerli olacağı). Panelin gri "ipucu" metinleri bundan
        çizilir ve istemcinin kendi varsayılanını gömmesi, sunucu varsayılanı
        değiştiğinde iki farklı gerçek üretirdi.

        **Önbellekli (L1).** Aynı ucu çağırır ama yalnız canlı olmayan kısmı
        saklar; ayar yazan metotlar dalı düşürür.
        """
        key = f"settings:reference:{location_id or 'default'}"

        async def load() -> dict[str, Any]:
            payload = await self._request("GET", f"{SETTINGS}/sales",
                                          params=self._query(location_id=location_id))
            meta = payload.get("meta") if isinstance(payload, dict) else None
            return dict(meta) if isinstance(meta, dict) else {}

        return await self._cached(key, load)

    async def update_sales_settings(
        self, *, order_cutoff: Any = UNSET, max_lookahead_days: Any = UNSET,
        subscription_release_time: Any = UNSET, min_order_total_kurus: Any = UNSET,
        delivery_fee_kurus: Any = UNSET, payment_methods: Any = UNSET,
        busy: Any = UNSET, busy_message: Any = UNSET, prep_minutes: Any = UNSET,
        delivery_minutes: Any = UNSET, busy_extra_minutes: Any = UNSET,
        daily_menu_enabled: Any = UNSET, auto_invoice: Any = UNSET,
        location_id: int | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Satış ayarlarını yazar — PUT /settings/sales. **Kısmi.**

        `ordering_enabled` BURADA YAZILAMAZ; kendi uçları var
        (`pause_ordering` / `resume_ordering`). Şalteri gerekçesiz ve süresiz
        çevirmek, tam da durdurmanın en sık hatasını (açmayı unutmak)
        üretirdi. `is_open` ve `daily_package_menu_id` de yazılamaz.

        `payment_methods` yalnız `online` ve `cash` kabul eder — cari hesap
        kalktı (iş kararı 1). `max_lookahead_days` tavanı 7'dir (iş kararı 3).
        Dakika alanları 1–480 aralığının dışında `validation` alır: sunucunun
        eski `LocationGate` davranışı sessizce varsayılana çeviriyordu, bu uç
        REDDEDİYOR — sessizce düzeltilen bir ayar, yöneticinin girdiğini
        sandığı değerle çalışmadığını hiç öğrenmemesi demektir.

        Yanıttaki `changed`, yalnız GERÇEKTEN değişen alanları listeler.
        """
        body = self._patch(
            order_cutoff=order_cutoff, max_lookahead_days=max_lookahead_days,
            subscription_release_time=subscription_release_time,
            min_order_total_kurus=min_order_total_kurus,
            delivery_fee_kurus=delivery_fee_kurus, payment_methods=payment_methods,
            busy=busy, busy_message=busy_message, prep_minutes=prep_minutes,
            delivery_minutes=delivery_minutes, busy_extra_minutes=busy_extra_minutes,
            daily_menu_enabled=daily_menu_enabled, auto_invoice=auto_invoice,
        )
        if location_id is not None:
            body["location_id"] = int(location_id)
        result = await self._request("PUT", f"{SETTINGS}/sales", body=body, reason=reason,
                                     actor=actor, dry_run=dry_run, action="settings.sales",
                                     reason_max=MAX_REASON)
        self.forget_reference("settings")
        return result

    async def pause_ordering(
        self, *, until: str | None = None, customer_message: str | None = None,
        location_id: int | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Satışı durdurur — POST /settings/ordering/pause.

        `busy` ile KARIŞTIRILMAMALI: `busy` yalnız uyarır, bu satışı gerçekten
        keser. `until` `null` ise süresiz (elle açılana kadar); en fazla 30 gün
        ileri olabilir — daha uzunu "süreli" değil kapanıştır.

        `customer_message` MÜŞTERİYE gösterilir; `reason` gösterilmez, o
        denetim izi içindir. İkisinin ayrı olması bilinçlidir: "buzdolabı
        arızası" cümlesi müşteriye söylenecek şey değildir.
        """
        body: dict[str, Any] = {"until": until, "customer_message": customer_message}
        if location_id is not None:
            body["location_id"] = int(location_id)
        result = await self._request("POST", f"{SETTINGS}/ordering/pause", body=body,
                                     reason=reason, actor=actor, dry_run=dry_run,
                                     action="settings.ordering.pause", reason_max=MAX_REASON)
        self.forget_reference("settings")
        return result

    async def resume_ordering(self, *, location_id: int | None = None, reason: str,
                              actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Satışı açar — POST /settings/ordering/resume.

        Durdurma izlerini (`paused_until`, `pause_reason`) temizler. Zaten
        açıksa `ok: true` döner.
        """
        result = await self._request(
            "POST", f"{SETTINGS}/ordering/resume",
            body=self._query(location_id=location_id) or {}, reason=reason, actor=actor,
            dry_run=dry_run, action="settings.ordering.resume", reason_max=MAX_REASON,
        )
        self.forget_reference("settings")
        return result

    async def closed_days(self, *, date_from: str = "",
                          date_to: str = "") -> dict[str, Any]:
        """Kapalı gün listesi — GET /settings/closed-days.

        **Global**: bütün vitrinler için geçerli, `location_id` yok sayılır.
        Aralık verilmezse sunucu bugünden itibaren 365 günü döndürür; geçmişi
        varsayılan olarak döndürmek listeyi her yıl biraz daha uzatırdı.
        """
        return await self._list(f"{SETTINGS}/closed-days", self._query(**{
            "from": self._date(date_from) if date_from else "",
            "to": self._date(date_to) if date_to else "",
        }))

    async def create_closed_day(self, *, date: str, description: str | None = None,
                                reason: str, actor: str,
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Kapalı gün ekler — POST /settings/closed-days.

        Geçmiş tarih KABUL EDİLİR (rapor tutarlılığı için sonradan
        işaretlenebilmeli); o güne sipariş varsa yanıt `warnings` taşır ama
        engel çıkmaz. Aynı tarih varsa `conflict`.
        """
        return await self._request("POST", f"{SETTINGS}/closed-days", body={
            "date": self._date(date), "description": description,
        }, reason=reason, actor=actor, dry_run=dry_run,
            action="settings.closed_day.create", reason_max=MAX_REASON)

    async def delete_closed_day(self, date: str, *, reason: str, actor: str,
                                dry_run: bool | None = None) -> dict[str, Any]:
        """Kapalı günü kaldırır — DELETE /settings/closed-days/{date}.

        Yol parçası TARİHTİR, kimlik değil: `closed_on` tekil ve yönetici
        takvimden bir güne tıklıyor. Gün kayıtlı değilse `not_found` — burada
        "zaten öyle" hoşgörüsü uygulanmaz: var olmayan bir tatili silmeye
        çalışan yönetici muhtemelen yanlış tarihe bakıyor ve bunu bilmeli.

        Silme GERÇEK silmedir; kapalı gün bir belge değil bir kuraldır ve
        tarihçesi denetim izindedir.
        """
        return await self._request("DELETE", f"{SETTINGS}/closed-days/{self._date(date)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="settings.closed_day.delete",
                                   reason_max=MAX_REASON)

    # ============================================== 9 · SİPARİŞ (PANEL YOLU)
    #
    # `control/kds/orders*` altındaki beş uç YAYINDA KALIR ve `bld_kds` onları
    # kullanıyor (yukarıdaki 5. bölüm). Buradaki metotlar AYNI denetleyiciye
    # ikinci bir rotadan bakar; tek fark hız sınırı kovasıdır. Panel
    # siparişleri KDS bütçesinden (`bld-control`, 1200/saat) yeseydi, yoğun
    # bir liste ekranı mutfağın kasa yönetimini kilitlerdi.

    register_dry_run(
        rf"^{ORDERS}$",
        rf"^{ORDERS}/\d+/revisions$",
        rf"^{ORDERS}/\d+/status$",
        rf"^{ORDERS}/\d+/cancel$",
    )

    # GEREKÇE MUAFİYETİ — bu alanda YALNIZ `POST /orders` (elle sipariş).
    #
    # Ölçüt `control/menu` alanında konulanın aynısı: işlem müşteriye GÖRÜNÜR
    # HÂLE GELİYOR mu ve GERİ ALINMASI ZOR mu. Telefon siparişini kaydetmek
    # rutin bir veri girişidir; personel müşteriyle konuşurken on karakter
    # yazmak zorunda kalsaydı sınırın kaçındığı metinlerin ta kendisi
    # üretilirdi ("sipariş", "asdasd"). Sunucu da bu uçta `reasonRequired:
    # false` diyor (`Control\OrderController::store`); defter ondan ayrı
    # kalsaydı geçit, sunucunun kabul ettiği bir çağrıyı yerelde keserdi.
    #
    # ALANIN GERİSİ MUAF DEĞİL: revizyon, durum geçişi ve iptal gerekçe ister
    # — üçü de müşteriye görünür ve geri alınması zordur. Sipariş AÇMAK ile
    # sipariş İPTAL ETMEK arasındaki fark tam olarak budur.
    register_reason_optional(("POST", rf"^{ORDERS}$"))

    async def order_list(
        self, *, service_date: str = "", date_from: str = "", date_to: str = "",
        status: Any = None, delivery_type: str = "", customer_id: int | None = None,
        subscription_id: int | None = None, source: str = "", q: str = "",
        page: int = 1, per_page: int | None = None,
    ) -> dict[str, Any]:
        """Sipariş listesi (sayfalı) — GET /api/control/orders.

        `orders()` (KDS yolu) BUGÜNÜN AKTİF siparişlerini döndürür ve mutfak
        panosuyla aynı kümedir; bu uç GEÇMİŞE bakar, süzülür ve sayfalanır.
        İkisi aynı yolda olsaydı KDS ekranının gördüğü küme değişirdi.

        Süzgeç verilmezse sunucu son 7 günü döndürür. Durum kodları
        `OrderStatusTransition::CODES`'tan gelir ve burada kopyalanmaz —
        kopyalanan liste sunucudakiyle sessizce ayrışırdı.

        ÖNBELLEĞE ALINMAZ.
        """
        return await self._list(ORDERS, self._query(
            service_date=self._date(service_date) if service_date else "",
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            status=self._csv(status), delivery_type=delivery_type,
            customer_id=customer_id, subscription_id=subscription_id, source=source, q=q,
            **self._paging(page, per_page),
        ))

    async def create_order(
        self, *, service_date: str, delivery_type: str, payment_method: str,
        items: list[dict[str, Any]], customer_id: int | None = None,
        customer: dict[str, Any] | None = None, address: dict[str, Any] | None = None,
        customer_note: str = "", location_id: int | None = None,
        reason: str = "", actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Elle sipariş — POST /api/control/orders. **Gerekçe İSTEMEZ.**

        Müşteri telefonla arıyor, personel siparişi giriyor. Bu uç TastyIgniter
        yönetim panelindeki `Admin\\PhoneOrders` ekranını devralıyor ve o panel
        kapandığında **elle sipariş girmenin başka yolu kalmıyor.**

        SİPARİŞ PENCERESİ BİLEREK ATLANIYOR. Sunucu `OrderFactory::create()`'i
        `adminContext: true` ile çağırıyor: kesim saati, ileri görüş penceresi,
        satış şalteri, asgari sepet tutarı ve menü üyeliği denetlenmiyor. Bu bir
        ONAY akışı değil KAYIT akışıdır — istisnayı telefondaki insan verdi,
        sistemin aynı kararı ikinci kez sorgulaması işi yapılamaz kılardı.
        Atlanmayan tek kapı ödeme yöntemi (aşağıda) — vitrinde tanımlı olmayan
        bir yöntemle açılan sipariş tahsilat tarafında karşılıksız kalır.

        SİPARİŞ `onaylandi` DOĞAR. `yeni` bırakılsaydı mutfağa hiç düşmez, tek
        belirtisi aç kalan bir müşteri olurdu. Bugüne açılan sipariş ANINDA
        KDS'e düşer, ileri tarihli olan o günün kesim anında.

        `service_date` ZORUNLU VE TÜRETİLMEZ: sessizce bugüne düşmesi, yarına
        alınan bir siparişin bugün pişmesi demekti. **`requested_at` alanı
        YOKTUR** — saati sunucu çözer (bugünse "şimdi", ileriyse 12:00);
        gerekirse `revise_order` üzerinden yazılır.

        Müşteri İKİ KİPTEN BİRİYLE verilir: `customer_id` (kayıtlı) ya da
        `customer={"name": ..., "phone": ...}` (telefonda ilk kez arayan).
        Yenisi `bld_account_type = corporate` ve yer tutucu e-posta ile açılır;
        **aynı telefonla ikinci sipariş ikinci müşteri yaratmaz** çünkü yer
        tutucu adres ulusal numaradan türüyor. Yanıttaki `customer.created`
        hangisinin olduğunu söyler. Kayıtlı bir müşterinin telefonu
        EŞLEŞTİRİLMEZ (santral numarası birden çok kayıtta durabilir); arama
        `customers()` ucundadır.

        Stok tavanı AŞILABİLİR (`allowOvershoot: true`): personel "bir porsiyon
        daha çıkarırız" diyebilir, sipariş reddedilmez, aşım kayda geçer.

        KURU PROVA KALEMİ, FİYATI VE STOĞU DENETLEMEZ — yalnız ödeme yöntemi
        kapısını ve müşteri çözümlemesini gerçekten koşar. "Gövde doğru mu"
        sorusunun cevabıdır, "sipariş geçecek mi" sorusunun değil; satılmayan
        bir ürün gerçek gönderimde `validation` (`ITEM_UNAVAILABLE`) verir.

        Yanıt `data` (liste satırının aynı biçimi), `customer` ve `warnings`
        taşır. `warnings` dolu ve `ok` yine `true` ise sipariş YAZILDI ama
        `onaylandi` geçişi patladı: kaybolmadı, `change_order_status` ile elle
        onaylanır.
        """
        if payment_method not in ("online", "cash"):
            raise BldApiError(
                f"Tanınmayan ödeme yöntemi: {payment_method}. Geçerli değerler: "
                "online, cash. Cari hesap (`account`) iş modelinden kalktı ve "
                "sunucu bu değeri 422 ile reddeder.",
                code="payload",
            )
        if bool(customer_id) == bool(customer):
            # İKİSİ BİRDEN DE HATA: sunucu `customer_id`'yi seçer ve `customer`
            # nesnesini sessizce yok sayar. Ekran yeni müşteri açtığını sanırken
            # sipariş başka bir hesaba yazılırdı — ve fark, o hesabın sahibi
            # yemediği bir yemeğin faturasını görene kadar anlaşılmazdı.
            raise BldApiError(
                "Müşteri iki kipten biriyle verilir: kayıtlı müşteri için "
                "`customer_id`, telefonda ilk kez arayan için "
                "`customer={'name': ..., 'phone': ...}`. İkisi birden ya da hiçbiri "
                "gönderilemez.",
                code="payload",
            )
        new_customer = dict(customer or {})
        if customer is not None and not (str(new_customer.get("name") or "").strip()
                                         and str(new_customer.get("phone") or "").strip()):
            # TELEFON YER TUTUCU E-POSTAYI ÜRETİYOR (`tel-<numara>@bld.invalid`)
            # ve `customers.email` çekirdekte TEKİL: rakamsız bir giriş
            # `tel-@bld.invalid` üretir, ikinci rakamsız müşteri de aynı adrese
            # düşer ve iki ayrı kurum tek kayda karışırdı.
            raise BldApiError(
                "Yeni müşteri `name` ve `phone` ister: yer tutucu e-posta "
                "telefondan türüyor ve numarasız bir kayıt başka bir numarasız "
                "kayıtla aynı adrese düşerdi.",
                code="payload",
            )
        if not items:
            raise BldApiError(
                "Sipariş en az bir kalem ister: kalemsiz bir sipariş mutfağa boş "
                "bir fiş olarak düşerdi.",
                code="payload",
            )
        delivery_address = dict(address or {})
        if delivery_type == "delivery" and not all(
            str(delivery_address.get(field) or "").strip()
            for field in ("line1", "district", "city")
        ):
            raise BldApiError(
                "Teslimatlı siparişte adres zorunlu: `line1`, `district` ve `city` "
                "dolu olmalı (`note` isteğe bağlı). Eksik adresle kurye siparişi "
                "nereye götüreceğini bilemezdi.",
                code="payload",
            )
        body: dict[str, Any] = {
            "service_date": self._date(service_date),
            "delivery_type": delivery_type,
            "payment_method": payment_method,
            # KALEMLER OLDUĞU GİBİ GEÇER, AYIKLANMAZ: bilinen alanları seçip
            # gerisini atan bir dönüşüm `option_value_ids`'i düşürürdü —
            # "ekstra peynir" silinir, sipariş ucuzlar, mutfak yanlış yemeği
            # yapar. Aynı gerekçe `revise_order` içinde de yazılı.
            "items": [dict(item) for item in items],
        }
        if customer_id:
            body["customer_id"] = int(customer_id)
        else:
            body["customer"] = new_customer
        if delivery_type == "delivery":
            # `pickup` siparişte adres GÖNDERİLMEZ: sunucu onu zaten `null`'a
            # çeviriyor ve yollamak, denetim izine hiçbir yerde kullanılmayan
            # bir adres yazdırırdı.
            body["address"] = delivery_address
        if customer_note:
            body["customer_note"] = customer_note
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("POST", ORDERS, body=body, reason=reason, actor=actor,
                                   dry_run=dry_run, action="order.create",
                                   reason_max=MAX_REASON)

    async def order_detail(self, order_id: int) -> dict[str, Any]:
        """Tek sipariş, düzenlenebilir görünüm — GET /control/orders/{order}.

        BİLEŞEN SATIRLARI LİSTEDE GÖRÜNMEZ. Günün menüsü bir paket satırı +
        sıfır fiyatlı bileşen satırları olarak yazılıyor; bileşenler de
        listelenseydi panel onları geri gönderir, sunucu tek tek satılan
        ürünler gibi fiyatlandırır ve toplam kendiliğinden şişerdi. Personel
        paketi TEK BİRİM olarak düzenler, sunucu bileşenleri yeniden açar.
        """
        return self._object(await self._request("GET", f"{ORDERS}/{int(order_id)}"))

    async def order_revision_history(self, order_id: int) -> dict[str, Any]:
        """Revizyon geçmişi — GET /control/orders/{order}/revisions.

        `created_by_device_id` `null` ise revizyon MERKEZDEN geldi; dolu ise
        mutfak kasasından.
        """
        return await self._list(f"{ORDERS}/{int(order_id)}/revisions")

    async def revise_order(
        self, order_id: int, *, items: list[dict[str, Any]], reason: str, actor: str,
        note: str = "", requested_at: str = "", customer_note: str = "",
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Sipariş revizyonu — POST /control/orders/{order}/revisions.

        Gövde `control/kds` ucuyla BİREBİR AYNIDIR ve sunucuda aynı
        `OrderEditor` yeniden kullanılır.

        `items` KALEM FARKI DEĞİL, TAM LİSTEDİR. Boş liste reddedilir —
        "hepsini sil" anlamına gelirdi ve iptal işi `cancel_order`'ındır.
        Kalemler OLDUĞU GİBİ geçer, ayıklanmaz: bilinen alanları seçip
        gerisini atan bir dönüşüm `option_value_ids`'i düşürürdü ve "ekstra
        peynir" sessizce silinirdi.

        Gerekçe en çok 160 karakter (`veykemtu_order_revisions.reason`).
        """
        if not items:
            raise BldApiError(
                "Revizyon kalem listesi boş olamaz: gönderilen liste siparişin TAM "
                "hâlidir, kalem farkı değil.",
                code="payload",
            )
        body: dict[str, Any] = {"items": [dict(item) for item in items]}
        if note:
            body["note"] = note
        if requested_at:
            body["requested_at"] = requested_at
        if customer_note:
            body["customer_note"] = customer_note
        return await self._request("POST", f"{ORDERS}/{int(order_id)}/revisions", body=body,
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="order.revise", reason_max=MAX_REASON_STRICT)

    async def change_order_status(self, order_id: int, *, status: str, reason: str,
                                  actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Durum geçişi — POST /control/orders/{order}/status.

        Geçiş matrisi izin vermezse `validation` (`INVALID_TRANSITION`).
        Gerekçe `status_history`'ye de yorum olarak düşer: "bu sipariş neden
        iptal edildi" sorusunun cevabı siparişin kendi geçmişinde durmalı.
        Geri alma penceresi 120 saniyedir.
        """
        return await self._request("POST", f"{ORDERS}/{int(order_id)}/status",
                                   body={"status": status}, reason=reason, actor=actor,
                                   dry_run=dry_run, action="order.status",
                                   reason_max=MAX_REASON_STRICT)

    async def cancel_order(self, order_id: int, *, refund: bool = True,
                           notify_customer: bool = True, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Siparişi iptal eder — POST /control/orders/{order}/cancel.

        `change_order_status(status="iptal")` İLE AYNI ŞEY DEĞİLDİR ve ayrı
        bir uçtur çünkü para hareketi üretir: ödenmiş siparişin iadesi,
        aboneliğe bağlı siparişin üretim defterinden düşülmesi.

        Yanıttaki `stock_released` iptalin en önemli yan etkisidir: iptal
        edilen porsiyonlar gün ve ürün tavanından düşer, yani o kadar sipariş
        yeniden alınabilir hâle gelir. Ekran bunu göstermezse yönetici "neden
        birden 12 yer açıldı" diye sorar.

        `refund=False` ödenmiş bir siparişte serbesttir (para elden iade
        edilmiş olabilir) ve yanıt `warnings` taşır. Abonelikten üretilmiş
        siparişi iptal etmek ABONELİĞİ DURDURMAZ.
        """
        return await self._request(
            "POST", f"{ORDERS}/{int(order_id)}/cancel",
            body={"refund": bool(refund), "notify_customer": bool(notify_customer)},
            reason=reason, actor=actor, dry_run=dry_run, action="order.cancel",
            reason_max=MAX_REASON_STRICT,
        )

    async def export_orders(
        self, *, service_date: str = "", date_from: str = "", date_to: str = "",
        status: Any = None, delivery_type: str = "", customer_id: int | None = None,
        subscription_id: int | None = None, source: str = "", q: str = "",
        max_rows: int | None = None,
    ) -> dict[str, Any]:
        """CSV dışa aktarım — GET /control/orders/export. **JSON DEĞİL.**

        Süzgeçler `order_list` ile aynıdır (`page`/`per_page` hariç). Dönüş
        `_document` zarfıdır: `content` (bayt), `filename`, `total_rows`,
        `truncated`.

        DİSKE `content` YAZILIR, `text` DEĞİL: dosya UTF-8 BOM ile başlıyor
        (`EF BB BF`) ve BOM'suz kaydedilen bir CSV'yi açan muhasebeci "ğ"
        yerine kutu görür.

        `truncated` True ise sunucu satırları kesti ve bu HATA DEĞİLDİR —
        kesilmiş bir dosya hiç dosya olmamasından iyidir; ekran bunu söylemeli.
        """
        return await self._request("GET", f"{ORDERS}/export", raw=True, params=self._query(
            service_date=self._date(service_date) if service_date else "",
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            status=self._csv(status), delivery_type=delivery_type,
            customer_id=customer_id, subscription_id=subscription_id, source=source, q=q,
            format="csv", max_rows=max_rows,
        ))

    async def order_invoice(self, order_id: int) -> dict[str, Any]:
        """Siparişin fatura belgesi — GET /control/orders/{order}/invoice.

        Belge YOKSA ÜRETMEZ (`not_found`); üretim `create_invoice()` işidir.
        """
        return self._object(await self._request("GET", f"{ORDERS}/{int(order_id)}/invoice"))

    # ======================================================= 10 · ABONELİK
    #
    # ROTA SIRASI KIRILGAN: sunucuda sabit parçalı yollar (`requests`,
    # `contracts`, `payments`, `orders`) `{subscription}` ÖNÜNDE kayıtlı
    # olmalı, yoksa birer kimlik sanılıp 404 döner. İstemci tarafında karşılığı
    # şu: bu metotlar kimliği asla o parçaların yerine koymaz.

    register_dry_run(
        rf"^{SUBSCRIPTIONS}$",
        rf"^{SUBSCRIPTIONS}/\d+$",
        rf"^{SUBSCRIPTIONS}/\d+/(activate|pause|resume|cancel|generate)$",
        rf"^{SUBSCRIPTIONS}/\d+/exceptions$",
        rf"^{SUBSCRIPTIONS}/\d+/exceptions/{_DATE}$",
        rf"^{SUBSCRIPTIONS}/\d+/contracts$",
        rf"^{SUBSCRIPTIONS}/\d+/payments$",
        rf"^{SUBSCRIPTIONS}/contracts/\d+/(resend|cancel)$",
        rf"^{SUBSCRIPTIONS}/payments/\d+/mark-paid$",
        rf"^{SUBSCRIPTIONS}/orders/\d+/release$",
        rf"^{SUBSCRIPTIONS}/requests/\d+$",
        rf"^{SUBSCRIPTIONS}/requests/\d+/convert$",
    )

    async def subscriptions(
        self, *, status: Any = None, customer_id: int | None = None, q: str = "",
        service_day: int | None = None, active_on: str = "", page: int = 1,
        per_page: int | None = None,
    ) -> dict[str, Any]:
        """Abonelik listesi (sayfalı) — GET /api/control/subscriptions.

        `unpaid_periods` ve `unpaid_total_kurus` LİSTEDE VARDIR çünkü bu
        ekranın asıl sorusu "kim ödemedi"dir; her satır için ayrı bir ödeme
        çağrısı yapmak dokuz abonelikte dokuz istek demekti.

        `active_on` o gün üretim yapacak abonelikleri süzer.
        """
        return await self._list(SUBSCRIPTIONS, self._query(
            status=self._csv(status), customer_id=customer_id, q=q,
            service_day=service_day,
            active_on=self._date(active_on) if active_on else "",
            **self._paging(page, per_page),
        ))

    async def subscription(self, subscription_id: int) -> dict[str, Any]:
        """Tek abonelik — GET /subscriptions/{id}.

        Alt kayıtlar (`lines`, `delivery_points`, `pauses`, `exceptions`) ve
        en son imzalı `contract` gövdenin içindedir; ayrı uç yoktur.
        """
        return self._object(await self._request(
            "GET", f"{SUBSCRIPTIONS}/{int(subscription_id)}"))

    async def create_subscription(
        self, *, customer_id: int, start_date: str, service_days: list[int],
        default_quantity: int, delivery_type: str = "delivery",
        menu_mode: str = "daily_menu", payment_mode: str = "prepaid_monthly",
        end_date: str | None = None, delivery_time_from: str | None = None,
        delivery_time_to: str | None = None, agreed_unit_price_kurus: int | None = None,
        lines: list[dict[str, Any]] | None = None,
        delivery_points: list[dict[str, Any]] | None = None,
        location_id: int | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Yeni abonelik — POST /subscriptions.

        Abonelik `pending` DOĞAR: fiyatı ve sözleşmesi tamamlanmadan üretim
        yapmamalı. `menu_mode` varsayılanı `daily_menu` (iş kararı 8: abonelik
        = günün menüsü otomatik); `fixed_list` seçilirse `lines` zorunludur ve
        `daily_menu` seçilirse `lines` GÖNDERİLEMEZ.

        `payment_mode` yalnız `prepaid_monthly` kabul eder — cari hesap
        tamamen kalktı (iş kararı 1). Geçit `account` değerini istek çıkmadan
        keser: sunucu 422 verirdi ve hata "geçersiz ödeme yöntemi" diye değil,
        anlamsız bir doğrulama metniyle görünürdü.

        Kuru provanın asıl faydası `first_service_dates`: yönetici kuralın
        gerçekten hangi günleri ürettiğini kaydetmeden görür.
        """
        if payment_mode != "prepaid_monthly":
            raise BldApiError(
                f"Tanınmayan ödeme kipi: {payment_mode}. Cari hesap kalktığı için tek "
                "geçerli değer `prepaid_monthly`; istek gönderilmedi.",
                code="payload",
            )
        body: dict[str, Any] = {
            "customer_id": int(customer_id), "start_date": self._date(start_date),
            "end_date": self._date(end_date) if end_date else None,
            "delivery_type": delivery_type, "delivery_time_from": delivery_time_from,
            "delivery_time_to": delivery_time_to,
            "service_days": [int(day) for day in service_days],
            "menu_mode": menu_mode, "default_quantity": int(default_quantity),
            "agreed_unit_price_kurus": agreed_unit_price_kurus,
            "payment_mode": payment_mode,
            "delivery_points": [dict(point) for point in (delivery_points or [])],
        }
        if menu_mode != "daily_menu":
            body["lines"] = [dict(line) for line in (lines or [])]
        if location_id is not None:
            body["location_id"] = int(location_id)
        return await self._request("POST", SUBSCRIPTIONS, body=body, reason=reason,
                                   actor=actor, dry_run=dry_run,
                                   action="subscription.create", reason_max=MAX_REASON)

    async def update_subscription(
        self, subscription_id: int, *, end_date: Any = UNSET, delivery_type: Any = UNSET,
        delivery_time_from: Any = UNSET, delivery_time_to: Any = UNSET,
        service_days: Any = UNSET, menu_mode: Any = UNSET, default_quantity: Any = UNSET,
        agreed_unit_price_kurus: Any = UNSET, lines: Any = UNSET,
        delivery_points: Any = UNSET, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Abonelik kuralını günceller — PATCH /subscriptions/{id}. **Kısmi.**

        `customer_id`, `location_id`, `start_date` ve `status` yazılamaz.
        `lines` / `delivery_points` gönderilirse TAM listedir: `id` taşıyan
        satır güncellenir, taşımayan eklenir, listede olmayan silinir.

        KURAL DEĞİŞİKLİĞİ ÜRETİLMİŞ SİPARİŞLERİ ETKİLEMEZ; yanıt bunu
        `warnings.generated_orders_unaffected` ile söyler ve o siparişleri
        düzeltmek `revise_order()` işidir. İptal edilmiş aboneliğe `PATCH`
        `conflict` verir.
        """
        return await self._request(
            "PATCH", f"{SUBSCRIPTIONS}/{int(subscription_id)}",
            body=self._patch(
                end_date=end_date, delivery_type=delivery_type,
                delivery_time_from=delivery_time_from, delivery_time_to=delivery_time_to,
                service_days=service_days, menu_mode=menu_mode,
                default_quantity=default_quantity,
                agreed_unit_price_kurus=agreed_unit_price_kurus, lines=lines,
                delivery_points=delivery_points,
            ),
            reason=reason, actor=actor, dry_run=dry_run, action="subscription.update",
            reason_max=MAX_REASON,
        )

    async def activate_subscription(self, subscription_id: int, *, reason: str, actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Aboneliği aktifleştirir — POST /subscriptions/{id}/activate.

        Ön denetimler kuru provada da koşar: anlaşılan birim fiyat dolu olmalı
        ve İMZALI SÖZLEŞME bulunmalı (iş kararı 9). Fiyatsız bir abonelik
        sipariş üretir ve o siparişin tutarı sıfır olurdu.
        """
        return await self._request("POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/activate",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="subscription.activate", reason_max=MAX_REASON)

    async def pause_subscription(
        self, subscription_id: int, *, start_date: str, end_date: str,
        pause_reason: str | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Aboneliği aralıklı duraklatır — POST /subscriptions/{id}/pause.

        DURAKLATMA İPTAL DEĞİLDİR: aralık boyunca üretim durur, sonra aynı
        fiyatla devam eder. `end_date` ZORUNLUDUR — süresiz duraklatma,
        iptalin adı konmamış hâlidir ve o iş `cancel_subscription`'ındır.

        Aralıktaki üretilmiş siparişler OTOMATİK İPTAL EDİLMEZ; yanıt onları
        `warnings.generated_orders_in_range` ile listeler ve yönetici tek tek
        karar verir.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/pause",
            body={"start_date": self._date(start_date), "end_date": self._date(end_date),
                  "pause_reason": pause_reason},
            reason=reason, actor=actor, dry_run=dry_run, action="subscription.pause",
            reason_max=MAX_REASON,
        )

    async def resume_subscription(self, subscription_id: int, *, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Duraklamayı bitirir — POST /subscriptions/{id}/resume.

        Açık duraklamayı bugün itibarıyla kapatır; satırı SİLMEZ, çünkü "ne
        zaman duraklatıldı, ne zaman devam edildi" sorusunun cevabı kalmalı.
        """
        return await self._request("POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/resume",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="subscription.resume", reason_max=MAX_REASON)

    async def cancel_subscription(self, subscription_id: int, *, effective_date: str,
                                  reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Aboneliği iptal eder — POST /subscriptions/{id}/cancel. **GERİ DÖNÜŞSÜZ.**

        Yeniden başlatmak yeni abonelik açmaktır: iptal edilmiş bir kuralı
        canlandırmak, iptal tarihinden sonraki günlerin hangi kurala tabi
        olduğunu belirsiz kılardı. Sonraki üretilmiş siparişler `warnings` ile
        listelenir; onları düşürmek `cancel_order()` işidir.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/cancel",
            body={"effective_date": self._date(effective_date)}, reason=reason, actor=actor,
            dry_run=dry_run, action="subscription.cancel", reason_max=MAX_REASON,
        )

    async def subscription_calendar(self, subscription_id: int, *, date_from: str = "",
                                    days: int | None = None) -> dict[str, Any]:
        """Önümüzdeki servis günleri — GET /subscriptions/{id}/calendar.

        Kaynak, üretim işinin kullandığı metodun TA KENDİSİDİR; takvim kendi
        mantığını yazsaydı ekranda görünen günler ile gerçekte üretilenler
        zamanla ayrışır ve bu ayrışmanın fark edileceği yer mutfak olurdu.

        Yalnız üretim yapılacak günler döner; kapalı günler `closed: true` ile
        GÖRÜNÜR — "o gün neden üretim yok" sorusunun cevabı listede olmalı.
        """
        return await self._list(f"{SUBSCRIPTIONS}/{int(subscription_id)}/calendar",
                                self._query(**{
                                    "from": self._date(date_from) if date_from else "",
                                    "days": days,
                                }))

    async def create_subscription_exception(
        self, subscription_id: int, *, service_date: str, skip: bool = False,
        quantity_override: int | None = None, note: str | None = None,
        reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Tek-gün istisnası — POST /subscriptions/{id}/exceptions.

        "Yarın 20 değil 12" ya da "yarın atla". Kural değişikliği değildir.
        Aynı gün için ikinci istisna ÜZERİNE YAZILIR (`409` verilmez): yönetici
        aynı güne iki kez karar verebilir ve son karar geçerlidir.

        `skip=True` iken adet gönderilemez — "atla ama 12 yap" tutarsız; geçit
        bunu istek çıkmadan keser. O gün için sipariş zaten üretilmişse
        `conflict` gelir ve doğru yol revizyondur.
        """
        if skip and quantity_override is not None:
            raise BldApiError(
                "`skip=True` ile adet birlikte gönderilemez: 'atla ama 12 yap' tutarsız.",
                code="payload",
            )
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/exceptions",
            body={"service_date": self._date(service_date), "skip": bool(skip),
                  "quantity_override": quantity_override, "note": note},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.exception.create", reason_max=MAX_REASON,
        )

    async def delete_subscription_exception(self, subscription_id: int, service_date: str, *,
                                            reason: str, actor: str,
                                            dry_run: bool | None = None) -> dict[str, Any]:
        """İstisnayı kaldırır — DELETE /subscriptions/{id}/exceptions/{date}.

        Gerçek silme: istisna bir belge değil bir kuraldır. Kayıt yoksa
        `not_found`.
        """
        return await self._request(
            "DELETE",
            f"{SUBSCRIPTIONS}/{int(subscription_id)}/exceptions/{self._date(service_date)}",
            body={}, reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.exception.delete", reason_max=MAX_REASON,
        )

    async def subscription_runs(self, subscription_id: int, *, date_from: str = "",
                                date_to: str = "", page: int = 1,
                                per_page: int | None = None) -> dict[str, Any]:
        """Üretim defteri (sayfalı) — GET /subscriptions/{id}/runs.

        İdempotency kaydıdır: bir (abonelik × nokta × gün) en fazla bir
        sipariş ve güvence koddaki `if` değil, veritabanı kısıtıdır.

        `order_id: null` olan satır, üretimin DENENDİĞİ ama sipariş
        oluşmadığı anlamına gelir (kapalı gün, menü yayınlanmamış, stok dolu).
        Satır yine de yazılır ki gece işi ertesi koşuda aynı günü yeniden
        denemesin.
        """
        return await self._list(f"{SUBSCRIPTIONS}/{int(subscription_id)}/runs", self._query(
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            **self._paging(page, per_page),
        ))

    async def generate_subscription_orders(
        self, subscription_id: int, *, service_date: str, release_now: bool = False,
        reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Belirli gün için elle üretim — POST /subscriptions/{id}/generate.

        Gece işini beklemeden üretir; kural gece işiyle AYNIDIR, ayrı bir kopya
        yazılmaz. Defterde satır varsa `conflict` (idempotency veritabanı
        kısıtından gelir). O günün tavanı dolmuşsa `validation` içinde
        `STOCK_EXCEEDED` gelir: abonelikler stoku önce rezerve eder ve elle
        üretim o rezervasyonun dışında kalan bir taleptir.

        `release_now=True` siparişi ANINDA KDS'e düşürür; varsayılan `False`,
        yani normal serbest bırakma saati (varsayılan 07:00) geçerlidir.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/generate",
            body={"service_date": self._date(service_date), "release_now": bool(release_now)},
            reason=reason, actor=actor, dry_run=dry_run, action="subscription.generate",
            reason_max=MAX_REASON,
        )

    async def release_subscription_order(self, order_id: int, *, reason: str, actor: str,
                                         dry_run: bool | None = None) -> dict[str, Any]:
        """Üretilmiş siparişi KDS'e düşürür — POST /subscriptions/orders/{order}/release.

        Abonelik siparişleri gece üretilir ve mutfağa 07:00'de düşer (iş
        kararı 7); bu uç bir siparişi o saatten önce açar. Gecikmenin sebebi:
        gece 00:12'de üretilen kırk sipariş, sabah işbaşı yapan mutfağın
        ekranını doldurur ve o an gelen GERÇEK bir siparişi görünmez kılardı.

        Zaten serbest bırakılmışsa `ok: true` döner; `409` verilmez.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/orders/{int(order_id)}/release", body={},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.order.release", reason_max=MAX_REASON,
        )

    # ------------------------------------------------------------- talepler

    async def quote_requests(self, *, status: Any = None, q: str = "", date_from: str = "",
                             date_to: str = "", page: int = 1,
                             per_page: int | None = None) -> dict[str, Any]:
        """Teklif talepleri (sayfalı) — GET /subscriptions/requests.

        Siteden gelen "Teklif Al" kayıtları. LİSTEDE İLETİŞİM BİLGİSİ
        MASKELİDİR (soyadın ilk harfi, telefonun ilk 3 + son 3 hanesi);
        arayacak kişi kaydı açar. Maskeleme sunucuda yapılır.

        Bu uçlar müşteri okuma denetimine (KVKK) tabi DEĞİLDİR: bu kayıtlar
        müşteri değil, henüz iletişime geçilmemiş adaylardır ve liste günde
        birkaç kez açılan bir iş kuyruğudur.
        """
        return await self._list(f"{SUBSCRIPTIONS}/requests", self._query(
            status=self._csv(status), q=q,
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            **self._paging(page, per_page),
        ))

    async def quote_request(self, request_id: int) -> dict[str, Any]:
        """Tek talep, MASKESİZ — GET /subscriptions/requests/{id}.

        `kvkk_accepted_at` her zaman doludur; onaysız kayıt hiç oluşmaz.
        """
        return self._object(await self._request(
            "GET", f"{SUBSCRIPTIONS}/requests/{int(request_id)}"))

    async def update_quote_request(self, request_id: int, *, status: Any = UNSET,
                                   admin_note: Any = UNSET, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Talebin durumu ve iç notu — PATCH /subscriptions/requests/{id}.

        Yazılabilir YALNIZ `status` ve `admin_note`. Ziyaretçinin yazdığı
        içerik değiştirilemez: bir kaydın içeriğini düzeltebilen panel, o
        kaydın delil değerini yok eder.
        """
        return await self._request(
            "PATCH", f"{SUBSCRIPTIONS}/requests/{int(request_id)}",
            body=self._patch(status=status, admin_note=admin_note), reason=reason,
            actor=actor, dry_run=dry_run, action="subscription.request.update",
            reason_max=MAX_REASON,
        )

    async def convert_quote_request(self, request_id: int, *, customer_id: int,
                                    subscription: dict[str, Any], reason: str, actor: str,
                                    dry_run: bool | None = None) -> dict[str, Any]:
        """Talebi aboneliğe çevirir — POST /subscriptions/requests/{id}/convert.

        Talep SİLİNMEZ: `status = kapandi` olur ve `converted_subscription_id`
        dolar. `customer_id` ZORUNLUDUR ve bu uç müşteri YARATMAZ — hesap
        açmak parola ve e-posta doğrulaması gerektirir, ikisi de bu
        sözleşmenin dışındadır.

        `subscription` bloğu `create_subscription` gövdesiyle aynı
        doğrulamalardan geçer; abonelik yine `pending` doğar.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/requests/{int(request_id)}/convert",
            body={"customer_id": int(customer_id), "subscription": dict(subscription)},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.request.convert", reason_max=MAX_REASON,
        )

    # ---------------------------------------------------------- sözleşmeler

    async def subscription_contracts(self, subscription_id: int) -> dict[str, Any]:
        """Aboneliğin sözleşmeleri — GET /subscriptions/{id}/contracts.

        `terms_snapshot` İMZALANDIĞI ANDAKİ koşullardır: abonelik sonradan
        değişse bile sözleşme değişmez, çünkü "neyi imzaladı" sorusunun cevabı
        orada durmalı. `token` ve imza bağlantısı hiçbir zaman dönmez.
        """
        return await self._list(f"{SUBSCRIPTIONS}/{int(subscription_id)}/contracts")

    async def create_subscription_contract(
        self, subscription_id: int, *, phone: str = "", expires_in_days: int = 7,
        send_sms: bool = True, reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Sözleşme oluşturur ve linki gönderir — POST /subscriptions/{id}/contracts.

        İş kararı 9: imzalı link + SMS OTP onayı. Sözleşme bir PDF değil, tek
        kullanımlık bir bağlantıdır.

        `sign_url` YALNIZ `send_sms=False` iken doludur. SMS gönderildiğinde
        `null` döner: bağlantı zaten müşterinin telefonunda ve panelde de
        göstermek onu ikinci bir yerde sızdırılabilir kılardı.

        Aynı abonelikte açık (`pending`/`sent`) bir sözleşme varsa `conflict`:
        iki geçerli bağlantı, hangisinin imzalandığını belirsiz kılardı.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/contracts",
            body=(self._query(phone=phone) or {}) | {"expires_in_days": int(expires_in_days),
                                                     "send_sms": bool(send_sms)},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.contract.create", reason_max=MAX_REASON,
        )

    async def subscription_contract(self, contract_id: int) -> dict[str, Any]:
        """Tek sözleşme — GET /subscriptions/contracts/{contract}.

        `token` ve `sign_url` DÖNMEZ; dönselerdi imzalı bağlantı denetim
        ekranından okunabilir hâle gelirdi.
        """
        return self._object(await self._request(
            "GET", f"{SUBSCRIPTIONS}/contracts/{int(contract_id)}"))

    async def resend_subscription_contract(self, contract_id: int, *,
                                           expires_in_days: int | None = None,
                                           reason: str, actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        """Linki yeniden gönderir — POST /subscriptions/contracts/{c}/resend.

        YENİ TOKEN ÜRETİLMEZ: müşterinin elindeki eski SMS'in çalışmaya devam
        etmesi, "hangi linke tıklayacağım" sorusunu ortadan kaldırır. İmzalı
        ya da iptal edilmiş sözleşmede `conflict`.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/contracts/{int(contract_id)}/resend",
            body=self._query(expires_in_days=expires_in_days) or {}, reason=reason,
            actor=actor, dry_run=dry_run, action="subscription.contract.resend",
            reason_max=MAX_REASON,
        )

    async def cancel_subscription_contract(self, contract_id: int, *, reason: str,
                                           actor: str,
                                           dry_run: bool | None = None) -> dict[str, Any]:
        """Sözleşmeyi iptal eder — POST /subscriptions/contracts/{c}/cancel.

        İmzalanmış sözleşmede `conflict`: imzalanmış bir sözleşmeyi iptal
        edilmiş göstermek, imzanın kendisini geçersiz kılmaktır. Yeni koşullar
        yeni bir sözleşme gerektirir.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/contracts/{int(contract_id)}/cancel", body={},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.contract.cancel", reason_max=MAX_REASON,
        )

    # -------------------------------------------------------------- ödemeler

    async def subscription_payments(self, subscription_id: int, *, status: Any = None,
                                    date_from: str = "",
                                    date_to: str = "") -> dict[str, Any]:
        """Dönem ödemeleri — GET /subscriptions/{id}/payments. Sayfalanmaz.

        Cari hesap kalktığı için (iş kararı 1) bu tablo aboneliğin TEK para
        defteridir. `overdue` sunucuda hesaplanır; istemcide hesaplansaydı
        saati kaymış bir panelde borç bir gün erken kırmızıya dönerdi.

        `meta` dönem toplamlarını taşır (`total_kurus`, `paid_kurus`,
        `pending_kurus`, `overdue_kurus`).
        """
        return await self._list(f"{SUBSCRIPTIONS}/{int(subscription_id)}/payments",
                                self._query(
                                    status=self._csv(status),
                                    **{"from": self._date(date_from) if date_from else "",
                                       "to": self._date(date_to) if date_to else ""},
                                ))

    async def create_subscription_payment(
        self, subscription_id: int, *, period_start: str, period_end: str, due_date: str,
        amount_kurus: int | None = None, note: str | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Dönem borcu oluşturur — POST /subscriptions/{id}/payments.

        `amount_kurus` `None` GÖNDERİLİRSE SUNUCU HESAPLAR: dönemdeki
        üretilmiş ve iptal edilmemiş siparişlerin toplamı. Elle tutar yazmak
        serbesttir ama varsayılan hesaplanmış olmalı — yönetici her ay çarpma
        yapmamalı. Yanıttaki `amount_source` ikisini ayırır.

        Aynı dönem varsa `conflict` (tekil kısıt). Kuru prova hesabı GERÇEKTEN
        yapar ve `order_count` ile döner.
        """
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/{int(subscription_id)}/payments",
            body={"period_start": self._date(period_start),
                  "period_end": self._date(period_end), "amount_kurus": amount_kurus,
                  "due_date": self._date(due_date), "note": note},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.payment.create", reason_max=MAX_REASON,
        )

    async def mark_subscription_payment_paid(
        self, payment_id: int, *, method: str, paid_at: str = "", reference: str = "",
        create_invoice: bool = False, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Tahsil edildi işaretler — POST /subscriptions/payments/{p}/mark-paid.

        `method` zorunlu ve yalnız `online` veya `cash` (iş kararı 1). Zaten
        `paid` ise `conflict`: ikinci kez tahsil işaretlemek tutarı iki kez
        saydırırdı — aynı kural, müşterinin uygulamadan ödediği yolu da
        kapsar.

        ÖDEMEYİ GERİ ALMAK İÇİN UÇ YOKTUR. Yanlış işaretlenen bir tahsilat
        yeni bir dönem kaydıyla düzeltilir; para defterinde silme yoktur.
        """
        if method not in ("online", "cash"):
            raise BldApiError(
                f"Tanınmayan ödeme yöntemi: {method}. Geçerli değerler: online, cash.",
                code="payload",
            )
        return await self._request(
            "POST", f"{SUBSCRIPTIONS}/payments/{int(payment_id)}/mark-paid",
            body=(self._query(paid_at=paid_at, reference=reference) or {})
            | {"method": method, "create_invoice": bool(create_invoice)},
            reason=reason, actor=actor, dry_run=dry_run,
            action="subscription.payment.paid", reason_max=MAX_REASON,
        )

    # ================================================ 11 · MÜŞTERİ (KVKK)
    #
    # BU ALANDA OKUMALAR DA DENETLENİR. Sistemdeki en geniş kişisel veri
    # yüzeyi burası: ad, telefon, e-posta, kurum bilgisi, adres defteri ve
    # sipariş geçmişi tek ekranda birleşiyor. "Kim, ne zaman, kimin kaydını
    # açtı" sorusunun bir cevabı olmalı — sızıntı çoğu zaman bir yazma değil
    # bir okumadır. Bu yüzden her GET `actor` sorgu parametresi ister ve
    # sunucu `customer.read` satırı yazar (`00-genel.md` §9).
    #
    # BU EKRANLAR YOKLANMAZ. Panel burada otomatik yenileme KURMAMALIDIR:
    # 15 saniyede bir yoklayan bir ekran, denetim izini günde binlerce anlamsız
    # satırla doldurup içindeki gerçek erişimi görünmez kılardı.

    register_dry_run(
        rf"^{CUSTOMERS}/\d+$",
        rf"^{CUSTOMERS}/\d+/(disable|enable)$",
    )

    async def customers(
        self, *, actor: str, q: str = "", status: str = "",
        has_subscription: bool | None = None, sort: str = "", direction: str = "",
        page: int = 1, per_page: int | None = None,
    ) -> dict[str, Any]:
        """Müşteri arama (sayfalı) — GET /api/control/customers.

        `actor` ZORUNLUDUR (KVKK) ve sorgu dizesinde gider. `q` en az iki
        karakter olmalı; tek harflik arama bütün müşteri tablosunu döndürürdü.

        LİSTE MASKELENMEZ: yönetici müşteriyi telefonundan tanır ve maskeli
        bir listede doğru kaydı seçemez, hepsini tek tek açmak zorunda kalır —
        yani her arama için bir düzine denetim satırı doğar. Maskeleme burada
        gizliliği artırmaz, izi bozar.
        """
        return await self._list(CUSTOMERS, self._query(
            actor=self._read_actor(actor), q=q, status=status,
            has_subscription=self._flag(has_subscription), sort=sort, direction=direction,
            **self._paging(page, per_page),
        ))

    async def customer(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Tek müşteri + istatistikleri — GET /customers/{id}.

        `stats` BURADA döner, ayrı bir uçta değil: müşteri kartını açan
        yönetici zaten bu sayıları görmek istiyor ve ayrı bir çağrı ikinci bir
        denetim satırı yazardı.

        `unpaid_total_kurus` abonelik dönem borçlarından gelir; cari hesap
        kalktığı için başka bir borç kaynağı yoktur.
        """
        return self._object(await self._request(
            "GET", f"{CUSTOMERS}/{int(customer_id)}",
            params={"actor": self._read_actor(actor)},
        ))

    async def update_customer(
        self, customer_id: int, *, first_name: Any = UNSET, last_name: Any = UNSET,
        telephone: Any = UNSET, org_name: Any = UNSET, tax_office: Any = UNSET,
        tax_no: Any = UNSET, contact_person: Any = UNSET, org_phone: Any = UNSET,
        reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """İletişim ve kurum etiketleri — PATCH /customers/{id}. **Kısmi.**

        YAZILABİLİR ALANLAR BUNLARLA SINIRLIDIR. Başka bir alan gönderilirse
        sunucu isteği TÜMÜYLE reddeder; bilinmeyen alanı sessizce yok saymak,
        e-posta değiştirdiğini sanan bir yöneticiye "başarılı" demek olurdu.

        PAROLA hiçbir uçta geçmez: ne okunur, ne yazılır, ne sıfırlanır.
        E-POSTA yazılamaz — giriş kimliğidir ve değiştirmek hesabı devretmek
        anlamına gelir. `account_type` da yazılamaz: kurumsal sipariş kapısı
        kalktığı için (iş kararı 2) artık bir yetki belirlemiyor, yalnız
        geçmiş kayıtların etiketi.
        """
        return await self._request(
            "PATCH", f"{CUSTOMERS}/{int(customer_id)}",
            body=self._patch(
                first_name=first_name, last_name=last_name, telephone=telephone,
                org_name=org_name, tax_office=tax_office, tax_no=tax_no,
                contact_person=contact_person, org_phone=org_phone,
            ),
            reason=reason, actor=actor, dry_run=dry_run, action="customer.update",
            reason_max=MAX_REASON,
        )

    async def customer_orders(self, customer_id: int, *, actor: str, status: Any = None,
                              date_from: str = "", date_to: str = "", page: int = 1,
                              per_page: int | None = None) -> dict[str, Any]:
        """Müşterinin sipariş geçmişi (sayfalı) — GET /customers/{id}/orders.

        Satır biçimi `order_list()` ile AYNIDIR; iki farklı sipariş şekli
        tanımlamak, panelin iki ayrı tablo bileşeni yazması demekti.
        """
        return await self._list(f"{CUSTOMERS}/{int(customer_id)}/orders", self._query(
            actor=self._read_actor(actor), status=self._csv(status),
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            **self._paging(page, per_page),
        ))

    async def customer_subscriptions(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Müşterinin abonelikleri — GET /customers/{id}/subscriptions.

        Sayfalanmaz: bir müşterinin abonelik sayısı tek hanelidir.
        """
        return await self._list(f"{CUSTOMERS}/{int(customer_id)}/subscriptions",
                                {"actor": self._read_actor(actor)})

    async def customer_addresses(self, customer_id: int, *, actor: str) -> dict[str, Any]:
        """Adres defteri — GET /customers/{id}/addresses. **SALT OKUNUR.**

        Adres yazan uç YOKTUR: adres siparişe kopyalanıyor, bağlanmıyor ve
        defteri panelden düzenlemek geçmiş siparişlerin adresini değiştirmez —
        yönetici değiştirdiğini sanır. Adresi müşteri kendi uygulamasından
        yönetir.
        """
        return await self._list(f"{CUSTOMERS}/{int(customer_id)}/addresses",
                                {"actor": self._read_actor(actor)})

    async def disable_customer(self, customer_id: int, *, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Hesabı kapatır — POST /customers/{id}/disable.

        Kapalı hesap giriş yapamaz ve sipariş veremez. Zaten kapalıysa
        `ok: true`. Aktif aboneliği olan bir hesabı kapatmak `warnings`
        üretir ama ENGELLENMEZ: abonelik üretimi durmaz (kural hesaba değil
        aboneliğe bağlı) ve yönetici bunu bilmeli.

        HESAP KAPATMAK VERİ SİLMEZ. Silme ucu yoktur ve olmayacaktır: geçmiş
        siparişlerin müşterisi olmayan kayıtlara dönüşmesi geri alınamaz bir
        kayıptır.
        """
        return await self._request("POST", f"{CUSTOMERS}/{int(customer_id)}/disable", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="customer.disable", reason_max=MAX_REASON)

    async def enable_customer(self, customer_id: int, *, reason: str, actor: str,
                              dry_run: bool | None = None) -> dict[str, Any]:
        """Hesabı açar — POST /customers/{id}/enable. Zaten açıksa `ok: true`."""
        return await self._request("POST", f"{CUSTOMERS}/{int(customer_id)}/enable", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="customer.enable", reason_max=MAX_REASON)

    # ================================================== 12 · FATURA BELGESİ
    #
    # BU BELGENİN MALİ DEĞERİ YOKTUR (iş kararı 10): yazdırılabilir bir A4
    # belge. Resmî fatura değildir, e-Fatura/e-Arşiv değildir, GİB'e gitmez,
    # vergi hesaplamaz.

    register_dry_run(
        rf"^{INVOICES}$",
        rf"^{INVOICES}/\d+/void$",
    )

    async def invoices(
        self, *, customer_id: int | None = None, subscription_id: int | None = None,
        order_id: int | None = None, status: str = "", date_from: str = "",
        date_to: str = "", q: str = "", page: int = 1, per_page: int | None = None,
    ) -> dict[str, Any]:
        """Belge listesi (sayfalı) — GET /api/control/invoices.

        `meta.issued_total_kurus` SÜZGEÇLENMİŞ kümenin toplamıdır, sayfanın
        değil: ekranın alt satırındaki toplam, sayfa değiştirince
        değişmemeli. İptal edilmiş belgeler bu toplama girmez.
        """
        return await self._list(INVOICES, self._query(
            customer_id=customer_id, subscription_id=subscription_id, order_id=order_id,
            status=status,
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            q=q, **self._paging(page, per_page),
        ))

    async def invoice(self, invoice_id: int) -> dict[str, Any]:
        """Tek belge (JSON), `snapshot_json` dâhil — GET /invoices/{id}.

        `snapshot_json` belgenin DONMUŞ içeriğidir: müşteri adı, kurum
        unvanı, kalemler ve fiyatlar sonradan değişse bile basılmış belge aynı
        kalmalı. Canlı tablodan okunan bir belge, iki farklı zamanda iki
        farklı kâğıt üretirdi.
        """
        return self._object(await self._request("GET", f"{INVOICES}/{int(invoice_id)}"))

    async def invoice_html(self, invoice_id: int) -> dict[str, Any]:
        """Yazdırılabilir A4 belge — GET /invoices/{id}/html. **JSON DEĞİL.**

        Dönüş `_document` zarfıdır; HTML `text` alanındadır. Belge tek dosya
        ve dış bağımlılıksızdır (CSS gömülü, görsel yok): panelde yeni sekmede
        açılıp yazdırılıyor ve dışarıdan kaynak çeken bir sayfa, ağ yokken boş
        basardı.

        İptal edilmiş belgenin üzerinde çapraz "İPTAL" filigranı bulunur;
        temiz basılabilmesi, elindeki kâğıdın geçerli olduğunu sanan bir
        müşteri üretirdi.
        """
        return await self._request("GET", f"{INVOICES}/{int(invoice_id)}/html", raw=True)

    async def create_invoice(
        self, *, order_id: int | None = None, subscription_id: int | None = None,
        period_start: str = "", period_end: str = "",
        subscription_payment_id: int | None = None, reason: str, actor: str,
        dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Belge üretir — POST /invoices. İki kip vardır, BİRİ SEÇİLMELİDİR.

        Sipariş belgesi (`order_id`) ya da dönem belgesi (`subscription_id` +
        dönem). İkisi birden ya da hiçbiri gönderilirse geçit isteği keser:
        sunucu da 422 verirdi ama hata "hangi kipi seçtiğimi bilmiyorum"
        demezdi.

        Aynı sipariş/dönem için geçerli bir belge varsa `conflict`. Kuru prova
        NUMARA ÜRETMEZ (seride boşluk açardı) ama toplamı hesaplar.
        """
        if bool(order_id) == bool(subscription_id):
            raise BldApiError(
                "Fatura belgesi iki kipten biriyle üretilir: `order_id` (sipariş belgesi) "
                "ya da `subscription_id` (dönem belgesi). İkisi birden ya da hiçbiri "
                "gönderilemez.",
                code="payload",
            )
        body: dict[str, Any] = {}
        if order_id:
            body["order_id"] = int(order_id)
        else:
            body["subscription_id"] = int(subscription_id or 0)
            body["period_start"] = self._date(period_start)
            body["period_end"] = self._date(period_end)
            if subscription_payment_id is not None:
                body["subscription_payment_id"] = int(subscription_payment_id)
        return await self._request("POST", INVOICES, body=body, reason=reason, actor=actor,
                                   dry_run=dry_run, action="invoice.create",
                                   reason_max=MAX_REASON)

    async def void_invoice(self, invoice_id: int, *, reason: str, actor: str,
                           dry_run: bool | None = None) -> dict[str, Any]:
        """Belgeyi iptal eder — POST /invoices/{id}/void.

        `PATCH` ve `DELETE` YOKTUR: kesilmiş bir belgenin içeriği
        değiştirilemez (yanlışsa iptal edilip yenisi kesilir) ve numara
        boşluğu bırakan bir seri, "44 nerede" sorusunu cevapsız bırakır.

        `void_reason` alanına ortak `reason` metni yazılır; ayrı bir alan
        istenmez, ikisinin çelişmesine yol açardı. İptal, bağlı dönem
        ödemesinin durumunu DEĞİŞTİRMEZ: belge ile tahsilat ayrı şeylerdir.
        """
        return await self._request("POST", f"{INVOICES}/{int(invoice_id)}/void", body={},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="invoice.void", reason_max=MAX_REASON)

    # =================================================== 13 · SİTE İÇERİĞİ

    register_dry_run(
        rf"^{CMS}/content/[a-z_]+$",
        rf"^{CMS}/services$",
        rf"^{CMS}/services/\d+$",
        rf"^{CMS}/posts$",
        rf"^{CMS}/posts/\d+$",
        rf"^{CMS}/revalidate$",
    )

    async def site_content(self) -> dict[str, Any]:
        """Tüm içerik anahtarları — GET /api/control/cms/content.

        `GET /content/{key}` YOKTUR: yedi anahtarın tamamı birlikte okunur,
        tek anahtar için ayrı bir uç panelin yedi istek atması demekti.
        Kaydı olmayan anahtar da döner (boş değer, `updated_at: null`) —
        atlamak, panelin "bu alan yok mu, yoksa boş mu" sorusunu kendi
        cevaplamasını gerektirirdi.
        """
        return self._object(await self._request("GET", f"{CMS}/content"))

    async def set_site_content(self, key: str, *, value: Any, revalidate: bool = True,
                               reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Tek anahtarı yazar — PUT /cms/content/{key}.

        `value` TAM DEĞERDİR, birleştirilmez: kısmi yazma iç içe geçmiş
        JSON'da "hangi seviyede birleştiriliyor" sorusunu doğururdu ve iki
        farklı cevabı olan bir kural sessizce veri kaybettirir.

        Anahtarlar sabittir (`brand`, `contact`, `company`, `faq`, `sectors`,
        `menus`, `quality`); listede olmayan anahtara yazmak `not_found`.
        Boyut sınırı 256 KB. `revalidate=False` ile art arda birkaç anahtar
        yazıp sonunda bir kez `revalidate_site()` çağrılabilir.
        """
        return await self._request(
            "PUT", f"{CMS}/content/{key}",
            body={"value": value, "revalidate": bool(revalidate)}, reason=reason,
            actor=actor, dry_run=dry_run, action="cms.content.update",
            reason_max=MAX_REASON,
        )

    async def site_services(self, *, published: str = "") -> dict[str, Any]:
        """Hizmet sayfaları — GET /cms/services. Sayfalanmaz.

        `published`: `true` · `false` · `all` (varsayılan `all`).
        """
        return await self._list(f"{CMS}/services", self._query(published=published))

    async def create_site_service(self, *, slug: str, title: str, fields: dict[str, Any],
                                  revalidate: bool = True, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni hizmet sayfası — POST /cms/services.

        `fields` sözleşmedeki geri kalan alanları taşır (`summary`, `intro`,
        `icon`, `body_html`, `audience`, `how_it_works`, `benefits`,
        `menu_planning`, `quote_needs`, `sort_order`, `is_published`).
        OLDUĞU GİBİ geçer, ayıklanmaz: bilinen alanları seçen bir dönüşüm,
        sözleşmeye yeni bir alan eklendiğinde onu sessizce düşürürdü.

        `body_html` KAYIT ANINDA TEMİZLENİR (sunucu tarafı) ve yanıt
        temizlenmiş hâli döner; panel onu göstermeli — gönderdiğini geri
        okumayan bir editör, yaptığı yapıştırmanın kaybolduğunu fark etmez.
        """
        return await self._request(
            "POST", f"{CMS}/services",
            body={**dict(fields), "slug": slug, "title": title,
                  "revalidate": bool(revalidate)},
            reason=reason, actor=actor, dry_run=dry_run, action="cms.service.create",
            reason_max=MAX_REASON,
        )

    async def update_site_service(self, service_id: int, *, fields: dict[str, Any],
                                  revalidate: bool = True, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Hizmeti günceller — PATCH /cms/services/{id}. **Kısmi.**

        `slug` yazılabilir ama yanıt `warnings.slug_changed` taşır: eski
        adrese verilen bağlantılar kırılır ve yönetici bunu bilmeli.
        """
        if not fields:
            raise BldApiError("En az bir alan verilmeli.", code="payload")
        return await self._request(
            "PATCH", f"{CMS}/services/{int(service_id)}",
            body={**dict(fields), "revalidate": bool(revalidate)}, reason=reason,
            actor=actor, dry_run=dry_run, action="cms.service.update",
            reason_max=MAX_REASON,
        )

    async def delete_site_service(self, service_id: int, *, revalidate: bool = True,
                                  reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Hizmeti siler — DELETE /cms/services/{id}. **Gerçek silme.**

        Hizmet kayıtları başka hiçbir tabloya bağlı değil; yumuşak silme için
        `is_published = false` zaten var ve gerçekten silmek isteyen yönetici
        onu kastediyor.
        """
        return await self._request(
            "DELETE", f"{CMS}/services/{int(service_id)}",
            body={"revalidate": bool(revalidate)}, reason=reason, actor=actor,
            dry_run=dry_run, action="cms.service.delete", reason_max=MAX_REASON,
        )

    async def site_posts(self, *, q: str = "", category: str = "", published: str = "",
                         page: int = 1, per_page: int | None = None) -> dict[str, Any]:
        """Bilgi merkezi yazıları (sayfalı) — GET /cms/posts.

        `meta.categories` mevcut kategorilerin DAMITILMIŞ listesidir: kategori
        ayrı bir tablo değil, serbest bir metin alanı; panel açılır listeyi
        buradan doldurur ve yönetici her seferinde yeni bir kategori uydurmaz.
        """
        return await self._list(f"{CMS}/posts", self._query(
            q=q, category=category, published=published, **self._paging(page, per_page)))

    async def create_site_post(self, *, slug: str, title: str, body_html: str,
                               fields: dict[str, Any] | None = None,
                               revalidate: bool = True, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Yeni yazı — POST /cms/posts.

        `body_html` ZORUNLUDUR ve boş olamaz: boş gövdeli bir yazı, sitede
        başlığı olan boş bir sayfa üretirdi. `fields` kalan alanları taşır
        (`description`, `category`, `published_at`, `reading_minutes`,
        `is_published`).
        """
        if not str(body_html or "").strip():
            raise BldApiError(
                "Yazı gövdesi (`body_html`) boş olamaz: başlığı olan boş bir sayfa üretirdi.",
                code="payload",
            )
        return await self._request(
            "POST", f"{CMS}/posts",
            body={**dict(fields or {}), "slug": slug, "title": title,
                  "body_html": body_html, "revalidate": bool(revalidate)},
            reason=reason, actor=actor, dry_run=dry_run, action="cms.post.create",
            reason_max=MAX_REASON,
        )

    async def update_site_post(self, post_id: int, *, fields: dict[str, Any],
                               revalidate: bool = True, reason: str, actor: str,
                               dry_run: bool | None = None) -> dict[str, Any]:
        """Yazıyı günceller — PATCH /cms/posts/{id}. **Kısmi.**"""
        if not fields:
            raise BldApiError("En az bir alan verilmeli.", code="payload")
        return await self._request(
            "PATCH", f"{CMS}/posts/{int(post_id)}",
            body={**dict(fields), "revalidate": bool(revalidate)}, reason=reason,
            actor=actor, dry_run=dry_run, action="cms.post.update", reason_max=MAX_REASON,
        )

    async def delete_site_post(self, post_id: int, *, revalidate: bool = True, reason: str,
                               actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Yazıyı siler — DELETE /cms/posts/{id}. **Gerçek silme.**"""
        return await self._request(
            "DELETE", f"{CMS}/posts/{int(post_id)}", body={"revalidate": bool(revalidate)},
            reason=reason, actor=actor, dry_run=dry_run, action="cms.post.delete",
            reason_max=MAX_REASON,
        )

    async def revalidate_site(self, *, paths: list[str] | None = None, reason: str,
                              actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Siteyi yeniden çizdirir — POST /cms/revalidate.

        `paths` `None` ise tümü; liste verilirse en çok 20 yol ve her biri
        `/` ile başlamalı. Diğer uçlardaki `revalidate=True` bayrağı bunun
        AYNISINI çağırır; bu uç, bayrağı kapalı bırakıp toplu çizdirmek
        isteyen yönetici içindir.

        ÇİZDİRME BAŞARISIZ OLURSA İSTEK BAŞARISIZ SAYILMAZ (`warnings`):
        içerik zaten yazıldı. Hata yüzünden 500 döndürmek, yöneticiye
        "kaydedilmedi" dedirtir ve o kaydı ikinci kez yazar.
        """
        return await self._request("POST", f"{CMS}/revalidate",
                                   body={"paths": list(paths) if paths else None},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="cms.revalidate", reason_max=MAX_REASON)

    # ============================================================ 14 · SMS
    #
    # Push bildirimi (FCM) YOKTUR (iş kararı 11). Müşteriye ulaşmanın iki yolu
    # var: SMS (bu alan) ve uygulama-içi duyuru (15. bölüm).

    register_dry_run(
        rf"^{SMS}/templates/[a-z0-9_]+$",
        rf"^{SMS}/templates/[a-z0-9_]+/preview$",
        rf"^{SMS}/send-test$",
        rf"^{SMS}/announcement$",
        rf"^{SMS}/announcement/run$",
    )

    async def sms_templates(self) -> dict[str, Any]:
        """Şablon listesi — GET /api/control/sms/templates.

        `meta.sender_configured` `false` ise sağlayıcı sırrı tanımsızdır ve
        gönderimler yalnız günlüğe yazılır. PANEL BUNU AÇIKÇA GÖSTERMELİ;
        aksi hâlde "SMS gitti" diyen bir ekran hiçbir şey göndermemiş olur.

        `otp_login` (giriş kodu) bu listede YOKTUR ve olmayacaktır: kimlik
        doğrulama metni yönetim yüzeyinden uzak durur.

        Önbelleğe ALINMAZ: şablonlar buradan düzenleniyor ve bayat bir metin,
        yöneticinin kaydettiğini sandığı değişikliği görmemesi demekti.
        """
        return await self._list(f"{SMS}/templates")

    async def update_sms_template(self, key: str, *, body: Any = UNSET,
                                  enabled: Any = UNSET, reason: str, actor: str,
                                  dry_run: bool | None = None) -> dict[str, Any]:
        """Şablon metnini/durumunu yazar — PATCH /sms/templates/{key}. **Kısmi.**

        `title` YAZILAMAZ: şablonun adı sistemin kendi sözlüğüdür. Tanınmayan
        bir `{degisken}` kaydedilmez (`validation`); sessizce boş bırakılan
        bir değişken, müşteriye "Sayın , siparişiniz…" diye giden bir SMS
        üretirdi.

        `enabled=False` o bildirimi tamamen kapatır: gönderim denenmez ve
        kayda satır yazılmaz.
        """
        return await self._request(
            "PATCH", f"{SMS}/templates/{key}", body=self._patch(body=body, enabled=enabled),
            reason=reason, actor=actor, dry_run=dry_run, action="sms.template.update",
            reason_max=MAX_REASON,
        )

    async def preview_sms_template(self, key: str, *, body: str = "",
                                   sample: dict[str, Any] | None = None, reason: str,
                                   actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Şablonu örnek veriyle işler — POST /sms/templates/{key}/preview.

        OKUMA GİBİ GÖRÜNÜR AMA POST'TUR: örnek veriyi gövdeyle taşıyor ve
        gövdesiz bir GET onu sorgu dizesine, yani imzanın dışına koyardı.
        Yazma kabuğundan geçtiği için gerekçe ister ve kuru prova anlar.

        HİÇBİR ŞEY GÖNDERİLMEZ; bu uç ağa çıkmaz. `body` verilmezse kayıtlı
        şablon işlenir. `unresolved_variables` dolu ise değişken metinde
        OLDUĞU GİBİ bırakılır — yöneticinin eksiği görmesi gerekiyor.
        """
        payload: dict[str, Any] = {}
        if body:
            payload["body"] = body
        if sample is not None:
            # `sample` GÖNDERİLMEZSE sunucu gerçekçi örnek değerler üretir;
            # boş bir sözlük göndermek o davranışı kapatır ve önizleme
            # "Sayın , siparişiniz…" diye görünürdü.
            payload["sample"] = dict(sample)
        return await self._request(
            "POST", f"{SMS}/templates/{key}/preview", body=payload, reason=reason,
            actor=actor, dry_run=dry_run, action="sms.template.preview",
            reason_max=MAX_REASON,
        )

    async def send_test_sms(self, *, phone: str, template_key: str = "", body: str = "",
                            sample: dict[str, Any] | None = None, reason: str, actor: str,
                            dry_run: bool | None = None) -> dict[str, Any]:
        """Tek numaraya deneme SMS'i — POST /sms/send-test.

        `template_key` VEYA `body` verilir, ikisi birden verilemez; geçit bunu
        istek çıkmadan keser. Metnin başına `[DENEME]` eklenir ve
        kaldırılamaz: deneme SMS'inin gerçek bir bildirimden ayırt
        edilememesi, yanlış numaraya giden bir mesajın müşteride panik
        yaratması demekti.

        Sağlayıcı hata verirse yanıt `ok: true` + `data.status: "failed"`
        olur, `502` DEĞİL: gönderim denemesi kayda geçti, isteğin kendisi
        başarısız değil.
        """
        if bool(template_key) == bool(body):
            raise BldApiError(
                "Deneme SMS'i ya bir şablonla (`template_key`) ya da serbest metinle "
                "(`body`) gönderilir; ikisi birden ya da hiçbiri olmaz.",
                code="payload",
            )
        payload: dict[str, Any] = {"phone": phone}
        if template_key:
            payload["template_key"] = template_key
        else:
            payload["body"] = body
        if sample is not None:
            payload["sample"] = dict(sample)
        return await self._request("POST", f"{SMS}/send-test", body=payload, reason=reason,
                                   actor=actor, dry_run=dry_run, action="sms.send_test",
                                   reason_max=MAX_REASON)

    async def sms_log(self, *, phone: str = "", template_key: str = "", status: str = "",
                      context: str = "", customer_id: int | None = None,
                      date_from: str = "", date_to: str = "", page: int = 1,
                      per_page: int | None = None) -> dict[str, Any]:
        """Gönderim kaydı (sayfalı) — GET /sms/log.

        Sağlayıcının kendi panelinden bağımsız, bizim tarafımızdaki gerçek.
        Telefon MASKELİ döner ve gövde 120 karakterde kırpılır: gönderim
        kaydı bir iletişim defterine dönüşmemeli.

        `meta.segment_total` süzgeçlenmiş kümenin segment toplamıdır —
        maliyet sorusunun cevabı. Kayıt silinemez; silme ucu yoktur.
        """
        return await self._list(f"{SMS}/log", self._query(
            phone=phone, template_key=template_key, status=status, context=context,
            customer_id=customer_id,
            **{"from": self._date(date_from) if date_from else "",
               "to": self._date(date_to) if date_to else ""},
            **self._paging(page, per_page),
        ))

    async def sms_announcement(self) -> dict[str, Any]:
        """Duyuru taslağı ve alıcı tahmini — GET /sms/announcement.

        `estimate` HER OKUMADA yeniden hesaplanır: alıcı sayısı sürekli
        değişiyor ve donmuş bir tahmin, yöneticinin sandığından fazla SMS
        göndermesi demekti. Bu yüzden önbelleğe de alınmaz.
        """
        return self._object(await self._request("GET", f"{SMS}/announcement"))

    async def set_sms_announcement(self, *, body: str, audience: str, reason: str,
                                   actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Duyuru taslağını yazar — PUT /sms/announcement. **GÖNDERMEZ.**

        Gönderme ayrı bir eylemdir ve ayrı bir gerekçe ister; metni yazmakla
        göndermeyi tek tuşta birleştirmek, yazım hatasını yüzlerce kişiye
        ulaştırır.

        `audience`: `active_customers` · `subscribers` · `all_customers`.
        Sonuncusu panelde ek onay ister — iki yıl önce bir kez sipariş vermiş
        birine duyuru göndermek, spam şikâyeti ve numara kaybı demektir.
        """
        return await self._request("PUT", f"{SMS}/announcement",
                                   body={"body": body, "audience": audience},
                                   reason=reason, actor=actor, dry_run=dry_run,
                                   action="sms.announcement.update", reason_max=MAX_REASON)

    async def run_sms_announcement(self, *, confirm_recipients: int, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Duyuruyu gönderir — POST /sms/announcement/run.

        `confirm_recipients` ZORUNLUDUR ve sunucunun o andaki hesabıyla
        birebir eşleşmelidir; aksi hâlde `conflict`. Yönetici ekranda 186
        görüp onayladıysa ve arada beş müşteri daha eklendiyse, gönderimin
        sessizce büyümemesi gerekir.

        Son çalıştırmadan 10 dakika geçmeden ikinci gönderim de `conflict`
        alır: çift tıklama ile aynı duyuruyu iki kez almak, müşterinin gördüğü
        tek şeydir.

        `failures` en çok 20 satır taşır; fazlası `sms_log(status="failed")`.
        """
        return await self._request(
            "POST", f"{SMS}/announcement/run",
            body={"confirm_recipients": int(confirm_recipients)}, reason=reason,
            actor=actor, dry_run=dry_run, action="sms.announcement.run",
            reason_max=MAX_REASON,
        )

    # ========================================= 15 · UYGULAMA-İÇİ DUYURU

    register_dry_run(
        rf"^{NOTIFICATIONS}$",
        rf"^{NOTIFICATIONS}/\d+$",
        rf"^{NOTIFICATIONS}/\d+/publish$",
    )

    async def notifications(self, *, status: str = "", audience: str = "", level: str = "",
                            live: bool | None = None, q: str = "", page: int = 1,
                            per_page: int | None = None) -> dict[str, Any]:
        """Duyuru listesi (sayfalı) — GET /api/control/notifications.

        SMS'ten farkı ittirilmemesidir: müşteri uygulamayı açtığında görür.
        `live` alanı SUNUCUDA hesaplanır (yayında + tarih aralığının içinde);
        istemcide hesaplansaydı saati kaymış bir panelde duyuru bir gün erken
        "bitmiş" görünürdü.

        `meta.live_count` şu an GERÇEKTEN görünen duyuru sayısıdır: "üç duyuru
        yayında" ile "üçü de tarih aralığının dışında" arasındaki farkı
        görmeyen yönetici, duyurusunun neden görünmediğini anlayamaz.
        """
        return await self._list(NOTIFICATIONS, self._query(
            status=status, audience=audience, level=level, live=self._flag(live), q=q,
            **self._paging(page, per_page),
        ))

    async def create_notification(
        self, *, title: str, body: str, level: str = "info", audience: str = "customers",
        starts_at: str | None = None, ends_at: str | None = None,
        action_label: str | None = None, action_url: str | None = None,
        dismissible: bool = True, reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Yeni duyuru — POST /notifications. **Her zaman `draft` doğar.**

        `body` DÜZ METİNDİR, HTML değil: duyuru üç uygulamada birden
        gösteriliyor ve HTML'i üçünde tutarlı çizmek imkânsız. Satır sonu
        `\\n` desteklenir.

        `action_url` verilirse `action_label` de zorunludur (ve tersi):
        etiketsiz bir düğme çizilemez, adressiz bir etiket tıklanamaz. Adres
        `https://` ya da uygulama-içi göreli yol olmalı.

        `dismissible=False` yalnız `level="critical"` ile kullanılabilir;
        kapatılamayan bir bilgilendirme duyurusu uygulamayı kullanılamaz
        hâle getirir. Geçit bu ikisini istek çıkmadan denetler.
        """
        if bool(action_url) != bool(action_label):
            raise BldApiError(
                "Duyuru düğmesi için `action_url` ve `action_label` birlikte verilir: "
                "etiketsiz düğme çizilemez, adressiz etiket tıklanamaz.",
                code="payload",
            )
        if not dismissible and level != "critical":
            raise BldApiError(
                "Kapatılamayan duyuru yalnız `level='critical'` ile kullanılabilir; "
                "kapatılamayan bir bilgilendirme uygulamayı kullanılamaz hâle getirir.",
                code="payload",
            )
        return await self._request("POST", NOTIFICATIONS, body={
            "title": title, "body": body, "level": level, "audience": audience,
            "starts_at": starts_at, "ends_at": ends_at, "action_label": action_label,
            "action_url": action_url, "dismissible": bool(dismissible),
        }, reason=reason, actor=actor, dry_run=dry_run, action="notification.create",
            reason_max=MAX_REASON)

    async def update_notification(
        self, notification_id: int, *, title: Any = UNSET, body: Any = UNSET,
        level: Any = UNSET, audience: Any = UNSET, starts_at: Any = UNSET,
        ends_at: Any = UNSET, action_label: Any = UNSET, action_url: Any = UNSET,
        dismissible: Any = UNSET, reason: str, actor: str, dry_run: bool | None = None,
    ) -> dict[str, Any]:
        """Duyuruyu günceller — PATCH /notifications/{id}. **Kısmi.**

        Yayınlanmış duyuru düzenlenebilir ve bu bilinçlidir: yazım hatası
        düzeltmek, tarihi uzatmak gerçek ihtiyaçlar. `audience` değişimi
        `warnings` üretir; görülme kayıtları SİLİNMEZ, kapsam daralınca kimin
        gördüğü bilgisi kaybolmamalı. `status` yazılamaz (kendi uçları var).
        """
        return await self._request(
            "PATCH", f"{NOTIFICATIONS}/{int(notification_id)}",
            body=self._patch(
                title=title, body=body, level=level, audience=audience,
                starts_at=starts_at, ends_at=ends_at, action_label=action_label,
                action_url=action_url, dismissible=dismissible,
            ),
            reason=reason, actor=actor, dry_run=dry_run, action="notification.update",
            reason_max=MAX_REASON,
        )

    async def publish_notification(self, notification_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Duyuruyu yayınlar — POST /notifications/{id}/publish.

        `published_at` İLK yayında yazılır ve sonra değişmez; arşivden geri
        yayınlanan bir duyurunun ilk yayın tarihi korunur. Yanıttaki
        `live_from`, `starts_at` gelecekteyse doludur ve panelin "yayınlandı
        ama henüz görünmüyor" mesajını yazmasını sağlar — aksi hâlde hiçbir
        şey görmeyen yönetici düğmeye ikinci kez basardı.
        """
        return await self._request(
            "POST", f"{NOTIFICATIONS}/{int(notification_id)}/publish", body={},
            reason=reason, actor=actor, dry_run=dry_run, action="notification.publish",
            reason_max=MAX_REASON,
        )

    async def archive_notification(self, notification_id: int, *, reason: str, actor: str,
                                   dry_run: bool | None = None) -> dict[str, Any]:
        """Duyuruyu arşivler — DELETE /notifications/{id}. **Yumuşak.**

        `status = archived` yazılır, satır silinmez. Arşivlenen duyuru ANINDA
        görünmez olur, `ends_at` beklenmez; görülme kayıtları kalır.
        `POST /{id}/unpublish` YOKTUR: yayından kaldırmanın üçüncü bir yolu,
        "duyuru neden görünmüyor" sorusunun üç ayrı cevabı olması demekti.
        """
        return await self._request("DELETE", f"{NOTIFICATIONS}/{int(notification_id)}",
                                   body={}, reason=reason, actor=actor, dry_run=dry_run,
                                   action="notification.archive", reason_max=MAX_REASON)

    async def notification_stats(self, notification_id: int) -> dict[str, Any]:
        """Görülme istatistiği — GET /notifications/{id}/stats.

        `trackable: false` (kitle `all`) durumunda `seen_count`, `seen_rate`
        ve `daily` **`null`** döner, sıfır DEĞİL: sıfır "kimse görmedi",
        `null` "ölçülemiyor" demektir ve ikisini karıştırmak, çalışan bir
        duyuruyu başarısız gösterirdi.
        """
        return self._object(await self._request(
            "GET", f"{NOTIFICATIONS}/{int(notification_id)}/stats"))

    # ========================================================= 16 · İZLEME

    register_dry_run(
        rf"^{MONITOR}/events/\d+/resolve$",
    )

    async def monitor_events(self, *, source: Any = None, level: Any = None,
                             code: str = "", device_id: int | None = None,
                             since: str = "", resolved: str = "", q: str = "",
                             page: int = 1, per_page: int | None = None) -> dict[str, Any]:
        """Hata/uyarı olayları (sayfalı) — GET /api/control/monitor/events.

        Olaylar parmak izine göre BİRLEŞTİRİLİR: aynı hata saatte yüzlerce kez
        tekrarlanabilir ve her tekrarı ayrı satır yazmak tabloyu bir günde
        okunamaz hâle getirirdi. `occurrence_count` tekrar sayısı,
        `first_seen_at` "bu ne zamandır oluyor" sorusunun cevabıdır.

        Varsayılanlar sunucuda: `level = warning,error,critical` (yani `info`
        gizli) ve `resolved = false` (açık olanlar). `context` LİSTEDE DÖNMEZ;
        tek olayda döner.

        `meta.open_counts` SÜZGEÇTEN BAĞIMSIZDIR: panel bunu sekme
        rozetlerinde kullanıyor ve süzgece göre değişen bir rozet yanıltıcı
        olurdu.
        """
        return await self._list(f"{MONITOR}/events", self._query(
            source=self._csv(source), level=self._csv(level), code=code,
            device_id=device_id, since=since, resolved=resolved, q=q,
            **self._paging(page, per_page),
        ))

    async def monitor_event(self, event_id: int) -> dict[str, Any]:
        """Tek olay + `context` + `related` — GET /monitor/events/{id}.

        `related` bloğu, olay bir cihaza bağlıysa o cihazın ŞU ANKİ sağlığını
        taşır: olay 05:12'de kaydedildi, yönetici 09:00'da bakıyor ve asıl
        merak ettiği "hâlâ bozuk mu" sorusudur.

        `context` kişisel veri taşımamalıdır; sunucu bilinen anahtarları kayıt
        anında maskeler.
        """
        return self._object(await self._request("GET", f"{MONITOR}/events/{int(event_id)}"))

    async def resolve_monitor_event(self, event_id: int, *, note: str = "", reason: str,
                                    actor: str, dry_run: bool | None = None) -> dict[str, Any]:
        """Olayı çözüldü işaretler — POST /monitor/events/{id}/resolve.

        Zaten çözülmüşse `conflict`: ikinci bir çözüm notu, ilkini gizlerdi.
        Olay YENİDEN GELİRSE otomatik yeniden açılır ama çözüm notu silinmez —
        "geçen sefer ne yapılmıştı" bilgisi, aynı hatanın ikinci kez
        teşhisinde en kısa yoldur.

        `DELETE` yoktur: bir hata kaydını silmek, o hatanın hiç olmadığını
        iddia etmektir.
        """
        return await self._request(
            "POST", f"{MONITOR}/events/{int(event_id)}/resolve",
            body=self._query(note=note) or {}, reason=reason, actor=actor, dry_run=dry_run,
            action="monitor.resolve", reason_max=MAX_REASON,
        )

    async def monitor_devices(self) -> dict[str, Any]:
        """Kasa sağlık özeti — GET /monitor/devices.

        `control/kds/devices` ucunun DAR BİR YÜZÜ: ayar, komut ve eşleme
        bilgisi taşımaz. Ayrı uç olmasının sebebi yetki: izleme ekranı
        `bld_monitor.view` ile açılıyor ve o yetkiyi taşıyan kişinin cihaz
        ayarlarını görmesi gerekmiyor.

        ÜÇ DURUMLU ALANLAR KORUNUR: `printer_ok`, `sound_ok` ve `alarm_muted`
        `null` olabilir ve `null` "bilinmiyor" demektir, `false` değil. Sağlık
        bildirmemiş bir kasa arızalı sayılmaz.

        `queue_oldest_age_minutes` sunucuda hesaplanır ve en çok işe yarayan
        alandır: "kuyrukta 4 iş var" ile "en eskisi 41 dakikadır bekliyor"
        arasındaki fark, sahaya gitme kararını değiştirir.
        """
        return await self._list(f"{MONITOR}/devices")

    async def monitor_summary(self) -> dict[str, Any]:
        """Tek istekte izleme durumu — GET /monitor/summary. Rozet bunu yoklar.

        `health.status` SUNUCUNUN tek cümlelik hükmüdür (`ok` · `degraded` ·
        `down`) ve istemcide TÜRETİLMEZ: üç ayrı ekranın aynı duruma bakıp
        farklı renk göstermesi, hangisine inanılacağını belirsiz kılardı.
        `reasons` makine okunur etiket listesidir; Türkçe karşılığını panel
        kendi yazar.
        """
        return self._object(await self._request("GET", f"{MONITOR}/summary"))

    # ================================================ 17 · GÖSTERGE PANELİ

    async def dashboard_overview(self, *, location_id: int | None = None,
                                 date: str = "") -> dict[str, Any]:
        """Panel açılış özeti — GET /api/control/dashboard/overview. **Tek uç.**

        `control/kds/overview` İLE KARIŞTIRILMAMALI: o uç KDS yönetimi
        ekranının özetidir (cihaz, fiş, aktif sipariş) ve olduğu gibi kalır;
        bu uç işletmenin tamamına bakar.

        `pending_tasks` gösterge panelinin asıl değeridir: sayılar durumu
        anlatır, o liste EYLEMİ söyler. `detail` metinleri Türkçe ve doğrudan
        gösterilebilirdir — panel kendi cümlesini kurmaz, aynı durumun iki
        ekranda iki farklı cümleyle anlatılması sahada telefonda konuşan iki
        kişinin farklı şey söylemesi demektir. `link` alanı Kontrol
        Merkezi'nin KENDİ yoludur, BLD API yolu değil.

        ÖNBELLEĞE ALINMAZ (istemci tarafında): gövde canlı sayaç taşıyor ve
        yoklama aralığı zaten 30 saniye. Sunucu 60 saniyelik bir önbellek
        açarsa yanıtta `meta.cached_at` taşır.
        """
        return self._object(await self._request(
            "GET", f"{DASHBOARD}/overview", params=self._query(
                location_id=location_id, date=self._date(date) if date else "")))

    # =================================================== 18 · DENETİM İZİ
    #
    # SALT OKUNUR. Bu alanda yazma ucu YOKTUR ve olmayacaktır: denetim izini
    # silebilen bir denetim izi, denetim izi değildir. `register_dry_run`
    # çağrısı da bu yüzden yok.

    async def server_audit(
        self, *, actor: str = "", action: Any = None, target_type: str = "",
        target_id: int | None = None, result: Any = None, date_from: str = "",
        date_to: str = "", q: str = "", page: int = 1, per_page: int | None = None,
    ) -> dict[str, Any]:
        """Sunucunun denetim izi (sayfalı) — GET /api/control/audit.

        `audit_trail()` İLE KARIŞTIRILMAMALI: o metot geçidin YEREL izini
        (`mod_bld_api_audit`) okur ve sunucuya hiç gitmez; buradaki iz
        BLD'nin kendi tablosudur (`veykemtu_control_audit`). İkisi ayrı
        sorulara cevap verir — yerel iz "gönderemediklerimizi" bilir, uzak iz
        "uygulananları".

        Varsayılan sayfa boyu 50'dir (diğer uçlarda 25): denetim izi bir
        tarama ekranıdır. `action` ÖNEK kabul eder (`menu.*`). Varsayılan
        pencere son 30 gün.

        `payload_json` listede 2 KB'de kırpılır (`payload_truncated: true`);
        tam hâli `server_audit_entry()` ile okunur.
        """
        return await self._list(AUDIT, self._query(
            actor=actor, action=self._csv(action), target_type=target_type,
            target_id=target_id, result=self._csv(result),
            **{"from": date_from, "to": date_to}, q=q,
            **page_params(page, AUDIT_PER_PAGE if per_page is None else per_page),
        ))

    async def server_audit_entry(self, audit_id: int) -> dict[str, Any]:
        """Tek denetim satırı, `payload_json` kırpılmadan — GET /audit/{id}.

        Başarısız bir satırda `payload_json.error` doludur. Kuru provanın
        sonucu HİÇBİR ZAMAN `failed` olmaz; ön denetim başarısız olsa bile
        satır `dry_run` kalır, aksi hâlde provalar gerçek yazma denemeleriyle
        karışırdı.
        """
        return self._object(await self._request("GET", f"{AUDIT}/{int(audit_id)}"))

    async def audit_actions(self) -> dict[str, Any]:
        """Bilinen eylem adları sözlüğü — GET /audit/actions. **Önbellekli (L1).**

        Panelin süzgeç açılır listesini doldurur. Eylem adlarını panele gömmek,
        sunucuya yeni bir eylem eklendiğinde süzgecin eksik kalması demekti.
        `label` Türkçedir ve SUNUCUDAN gelir.

        Hiç kullanılmamış eylemler de `count: 0` ile döner; yalnız
        kullanılanları döndürmek, yeni bir eylemi ilk kullanılana kadar
        gizlerdi.
        """
        return await self._cached("audit:actions", lambda: self._list(f"{AUDIT}/actions"))

    # ============================================ 19 · REFERANS GÖRÜNTÜSÜ

    async def reference_snapshot(self, *, refresh: bool = False) -> dict[str, Any]:
        """Referans kümesinin tek çağrılık anlık görüntüsü (L1 + L2).

        Panel açılışında dört ayrı referans listesi isteniyor. Bu metot onları
        tek yerde toplar, SQLite'a yazar ve süreç yeniden başladığında BLD'ye
        hiç gitmeden döndürür.

        BLD ERİŞİLEMEZKEN de son bilinen hâli verir (`stale: true`) ve hangi
        parçaların alınamadığını `errors` içinde söyler — ekran ayakta kalır
        (K7) ama verinin bayat olduğunu gizlemez. Hiç anlık görüntü yoksa hata
        yukarı gider: bayat veri iyi, uydurma veri değil.

        Buraya SİPARİŞ, STOK, MÜŞTERİ ya da FATURA KONMAZ (`cache.py`).
        """
        if refresh:
            self._reference.drop()
        cached = await self._snapshot.get("reference")
        if not refresh and cached and not cached["stale"]:
            return {**cached["payload"], "stale": False, "stored_at": cached["stored_at"],
                    "age_seconds": cached["age_seconds"], "errors": {}}

        payload: dict[str, Any] = {}
        errors: dict[str, str] = {}
        parts: tuple[tuple[str, Callable[[], Any]], ...] = (
            ("categories", self.categories),
            ("settings", self.settings_reference),
            ("products", self.product_picker),
            ("audit_actions", self.audit_actions),
        )
        for name, loader in parts:
            try:
                payload[name] = await loader()
            except BldApiError as failure:
                errors[name] = failure.message

        if errors and cached:
            # Bir parça bile alınamadıysa taze görüntüyü YAZMAYIZ: eksik bir
            # görüntü, tam olanın üzerine geçerse hata kalıcılaşırdı.
            self._log.warning("referans görüntüsü eksik, bayat sürüm veriliyor",
                              missing=sorted(errors))
            return {**cached["payload"], "stale": True, "stored_at": cached["stored_at"],
                    "age_seconds": cached["age_seconds"], "errors": errors}
        if errors:
            raise BldApiError(
                "BLD referans verisi alınamadı ve yerelde anlık görüntü yok: "
                + "; ".join(f"{name}: {text}" for name, text in sorted(errors.items())),
                code="transport",
            )

        await self._snapshot.put("reference", payload)
        return {**payload, "stale": False, "stored_at": "", "age_seconds": 0, "errors": {}}
