"""BLD sunucusuna açılan TEK kapı (K4) — `/api/control/kds/*`.

Karşı taraf: `platform/extensions/veykemtu/bridgeapi` (TastyIgniter/Laravel).
Sözleşme: K-21 köprü sözleşmesi §1-§4. Uydurma alan, yol ya da başlık
EKLENMEZ; eksik görülen şey rapora yazılır.

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

3. GEREKÇE VE AKTÖR (sözleşme §3). Her POST/PATCH gövdesinde `reason`
   (en az 10 karakter) ve `actor` ZORUNLUDUR; ikisini de geçit gövdeye
   koyar. Başlıkla TAŞINMAZ — sözleşme böyle bir başlık tanımlamıyor ve
   sunucu gerekçeyi gövdeden okuyup `veykemtu_control_audit` tablosuna
   yazıyor. İstek çıkmadan aynı bilgi `mod_bld_api_audit` tablosuna da
   yazılır: ağ koparsa "ne yapmaya çalıştık" kaydı yerelde kalır.

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

5. HIZ KOVASI. Sunucu sınırı `throttle:bld-control` = 1200/saat/IP. Kova
   dakikada 18'de tutar: 18 · 60 = 1080 < 1200, yani dakikalık bir patlama
   saatlik bütçeyi tüketemez ve pay elle atılan istekler için kalır.

GÖVDE BAYT BAYT AYNI GİDER — bunun neden zorunlu olduğu `_encode` ve
`_send` içinde anlatılıyor.
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
from .errors import BldApiError, mask_mapping, mask_text

#: Tüm kontrol uçlarının öneki (sözleşme §2).
CONTROL = "/api/control/kds"

#: Kasadaki imza sırrı. Adlandırma `server.<uygulama>.<alan>`: bu sır BLD
#: SUNUCUSUNA aittir (orada `BLD_CONTROL_SECRET` ortam değişkeni), bir
#: uygulama belirteci değildir.
SECRET_KEY = "server.bld.control_secret"

#: Yazma gerekçesinin en az uzunluğu (sözleşme §3). "ok", "düzeltme" gibi
#: metinler denetim izini işe yaramaz kılıyor; ADR 0012 ekranlarda da 10
#: karakter istiyor. Sunucu da aynı sınırı uyguluyor — çift kapı (K9).
MIN_REASON = 10

#: Revizyon ve durum uçlarında gerekçenin ÜST sınırı (sözleşme §2.5, §3).
#: Diğer uçlar için sözleşme bir üst sınır söylemiyor; uydurulmaz.
MAX_REASON_STRICT = 160

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

#: KURU PROVAYI ANLAYAN uçlar (sözleşme §2 + §4). Bu listede olmayan bir
#: yola kuru prova ile yazılamaz: bayrak sessizce yok sayılır ve prova
#: gerçek yazmaya dönüşürdü (bkz. modül başlığı, 2. politika).
_DRY_RUN_AWARE = tuple(re.compile(pattern) for pattern in (
    rf"^{CONTROL}/devices$",
    rf"^{CONTROL}/devices/\d+$",
    rf"^{CONTROL}/devices/\d+/pairing-code$",
    rf"^{CONTROL}/devices/\d+/revoke$",
    rf"^{CONTROL}/devices/\d+/settings$",
    rf"^{CONTROL}/devices/\d+/commands$",
    rf"^{CONTROL}/orders/\d+/revisions$",
    rf"^{CONTROL}/orders/\d+/status$",
))


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

    Liste metotları:  `list[dict]` (sunucunun snake_case alanları korunur)
    Tekil metotlar:   düz sözlük
    Yazma metotları:  sunucunun yanıtı — kuru provada
                      `{"ok": true, "dry_run": true, "would": {...}}`;
                      sözleşmede olmayan bir yolda ise istek gönderilmeden
                      `{"ok": true, "dry_run": true, "sent": false, ...}`
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
        self._transport = transport
        self._secret: str | None = None

        self._sleep: Callable[[float], Any] = asyncio.sleep
        self._bucket = _RateBucket(requests_per_minute, sleeper=lambda s: self._sleep(s))
        self._audit = AuditTrail(store, log)

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
    ) -> Any:
        verb = method.upper()
        if verb == "GET":
            return await self._send(verb, path, params=params)
        return await self._write(verb, path, params=params, body=body, reason=reason,
                                 actor=actor, dry_run=dry_run, action=action)

    async def _write(
        self, verb: str, path: str, *, params: dict[str, Any] | None, body: dict[str, Any] | None,
        reason: str, actor: str, dry_run: bool | None, action: str,
        reason_max: int = 0,
    ) -> Any:
        """Yazma zinciri: gerekçe → aktör → denetim izi → acil fren → kuru prova → istek."""
        reason = str(reason or "").strip()
        actor = str(actor or "").strip()

        if self._require_reason and len(reason) < MIN_REASON:
            raise BldApiError(
                "Gerekçe zorunlu: BLD sunucusunda yapılan her değişiklik en az "
                f"{MIN_REASON} karakterlik bir gerekçeyle kayda geçer (sözleşme §3).",
                code="reason_required",
            )
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
            raise BldApiError(
                "İşlemi yapan kişinin adı (actor) zorunlu: denetim izinde "
                "kasadan mı merkezden mi yapıldığı buna göre ayrılır (sözleşme §3).",
                code="actor_required",
            )

        dry = self._dry_run_default if dry_run is None else bool(dry_run)
        request_id = AuditTrail.new_request_id()

        # Gerekçe ve aktör GÖVDEYE konur (sözleşme §3). Başlıkla taşınmaz:
        # sözleşme yalnız üç imza başlığı tanımlıyor, dördüncüsü uydurma olurdu.
        payload: dict[str, Any] = dict(body or {})
        payload["reason"] = reason
        payload["actor"] = actor

        await self._audit.before(
            request_id=request_id, method=verb, path=path, action=action or path,
            reason=reason, actor=actor, dry_run=dry, body=payload,
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
        body: dict[str, Any] | None = None,
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
                "Accept": "application/json",
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
