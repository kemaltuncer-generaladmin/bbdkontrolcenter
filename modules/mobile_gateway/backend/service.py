"""Mobil Geçit iş kuralları — sözleşme v1 (km-mobile-gateway.md + Ekler).

Bu katman HTTP bilmez; `GatewayError` fırlatır, rota onu zarfa çevirir.
Yeteneklere yalnız `Capabilities` üzerinden gidilir (K3).

KİŞİSEL VERİ KURALI: öğrenci şifresi, OTP kodu ve SMS metni log'a, tabloya,
denetim izine ya da hata mesajına YAZILMAZ. Telefon hız sınırı tablosunda
sha256 özeti, SMS kaydında maskeli tutulur. Akışlarda (feed) telefon ve SMS
metni hiç dönmez — çıktı her uçta beyaz listeyle kurulur.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, TypeVar
from urllib.parse import unquote
from uuid import uuid4
from zoneinfo import ZoneInfo

from km_sdk import ModuleStore

from .caps import (
    BELL_ANNOUNCE,
    BELL_PLAN,
    BELL_WEEK,
    CANTEEN,
    GROUPS,
    TABLET_READ,
    Capabilities,
    CapabilityMissing,
    LedgerUnavailable,
    Rejected,
    UpstreamFailure,
)
from .errors import (
    GatewayError,
    canteen_unavailable,
    capability_missing,
    locked,
    rate_limited,
    student_not_found,
    upstream_error,
    validation,
)
from .repository import Repository

T = TypeVar("T")

#: OTP metni ASCII (sms-kale): Netgsm OTP SMS hattı Türkçe karakter kabul etmez ve
#: tek boydur (başlıklı SMS 155 karakter). Ayardaki şablon da ASCII'ye indirilir.
DEFAULT_OTP_TEMPLATE = "BBD Akademi giris kodunuz: {code}. Kodu kimseyle paylasmayin."
#: Teslim raporu Netgsm'de jobId başına dakikada bir sorulabilir; geçit en sık bu
#: aralıkla kantine sorar (kantin de ayrıca önbellekler).
SMS_REPORT_EVERY_MS = 55_000
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_APP_HASH_RE = re.compile(r"^[A-Za-z0-9+/]{11}$")
_ASCII_FOLD = str.maketrans("çÇğĞıİöÖşŞüÜâÂîÎûÛ", "cCgGiIoOsSuUaAiIuU")


def ascii_sms(text: str) -> str:
    """Türkçe harfleri ASCII karşılığına indirir, kalan ASCII dışını atar."""
    folded = text.translate(_ASCII_FOLD)
    return " ".join("".join(ch for ch in folded if ch.isspace() or 32 <= ord(ch) < 127).split())
DEFAULT_PAGE_TEMPLATE = "{ad} {soyad}, lütfen danışmaya gel."
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
CALL_PLACEHOLDERS = {"ad", "soyad", "sinif"}
TRACKS = {"SAY", "EA", "SOZ", "DIL"}
EXAM_TARGETS = {"LGS", "YKS"}
GRADUATE = "graduate"

MINUTE_MS = 60_000
HOUR_MS = 60 * MINUTE_MS
DAY_MS = 24 * HOUR_MS
#: İşlenirken düşen isteğin idempotensi satırı bu kadar sonra devralınabilir.
STALE_CLAIM_MS = 2 * MINUTE_MS
PURGE_EVERY_MS = 10 * MINUTE_MS

#: Kantin hesap dökümünün (`ledger`) sayfa üst sınırı (kantin doğrulaması).
LEDGER_PAGE_MAX = 200
#: Kantin özeti en çok bu kadar defter sayfası okur (200 × 50 = 10.000 satır;
#: bir öğrencinin 180 günde buna ulaşması pratikte olmaz). Aşılırsa log'a
#: uyarı düşer — eski yolun kurum geneli 5000 satır kesintisinin yerine.
SUMMARY_LEDGER_PAGES = 50
#: İlk defter sayfası aralığı bitiremezse kalan aralık en çok bu kadar zaman
#: dilimine bölünüp dilimler AYNI ANDA okunur (A14 §3.9: dış çağrılar paralel).
SUMMARY_LEDGER_SLICES = 4
#: İmleçli liste ucu bir sayfayı doldurmak için kantine en çok bu kadar gider;
#: dolmazsa kısa sayfa + geçerli imleç döner.
PAGE_FILL_CALLS = 5
#: İmleçli liste uçlarının sayfa boyu (Akademi `Limit` parametresiyle aynı).
PAGE_LIMIT_DEFAULT = 20
PAGE_LIMIT_MAX = 100
#: Defter ucu olmayan kantinde `from` verilmezse geriye bakılan gün sayısı.
FALLBACK_DAYS = 180

#: Zil planı (km-ogrenci-gruplari-ve-zil.md §3.3). Gövde üst sınırı ve aralıklar.
BELL_PLAN_MAX_BYTES = 512 * 1024
BELL_PLAN_WINDOW_DAYS = 14
BELL_STATUS_MAX_DAYS = 31
#: `GET /tablets/usage` en çok bu kadar gün geriye bakar (bugün dahil; tabletin sınırı).
TABLET_USAGE_MAX_DAYS = 62

#: Merkezi senkron uçları (geçit sözleşmesi §9).
DIRECTORY_IDS_MAX = 100
#: `since` bundan eskiyse mezar taşı penceresi kaçmıştır → 422, istemci tam liste ister.
DIRECTORY_SINCE_MAX_DAYS = 90
BALANCES_IDS_MAX = 500
EVENTS_LIMIT_DEFAULT = 100
EVENTS_LIMIT_MAX = 500
STUDENT_STATUSES = frozenset({"active", "inactive"})
#: Veli yuvaları (sözleşme §1): 1 birincil (telefonu kantinde), 2 profil, 3–4 ek veliler
#: (`bbd_students` göç 007). Dört yuvanın velisi de uygulamaya girer.
GUARDIAN_SLOTS = (1, 2, 3, 4)
#: Velinin öğrenciye yakınlığı — beyaz liste (`bbd_students/backend/guardians.py`).
GUARDIAN_RELATIONS = frozenset({"mother", "father", "guardian", "grandparent", "sibling", "other"})
STATUS_REASONS = frozenset({"left", "graduated", "suspended", "other"})
#: Gün sonucu ve hata kodları — beyaz liste (zil yeteneğinin döndürdüğü).
BELL_PLAN_RESULTS = frozenset({"applied", "unchanged", "stale", "overridden", "rejected"})
BELL_PLAN_ERRORS = frozenset({"revision_conflict", "unknown_profile"})
#: `GET /bell/plan/status` gün alanları (sözleşme + ekleyici: profileId, reason,
#: lastResult, lastError — Akademi'nin `BellPlanDay`'i ilk ikisini ister).
BELL_STATUS_DAY_KEYS = ("date", "mode", "profileId", "reason", "source", "revision",
                        "planHash", "calls", "voicesReady", "voicesPending",
                        "lastDispatchAt", "override", "lastResult", "lastError")
#: Defter satırı türleri → liste ucu.
SALE_KINDS = frozenset({"sale"})
COLLECTION_KINDS = frozenset({"payment", "cash"})

#: Kantin `reason` → HTTP durumu, uca göre (sözleşme hata listeleri).
PAYMENT_REQUEST_REASONS = {"no_debt": 409, "no_phone": 409, "exceeds_debt": 422,
                           "invalid_amount": 422}
COLLECTION_REASONS = {"no_debt": 422, "exceeds_debt": 422, "invalid_amount": 422}

REASON_MESSAGES = {
    "no_debt": "Öğrencinin ödenecek kantin borcu yok.",
    "no_phone": "Velinin telefon numarası kantinde kayıtlı değil.",
    "exceeds_debt": "Tutar izin verilen sınırı (borç + ön ödeme) aşıyor.",
    "invalid_amount": "Tutar geçersiz.",
}

_IDEMPOTENCY_KEY = re.compile(r"^[\x21-\x7e]{1,255}$")
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


# ------------------------------------------------------------------ yardım


@dataclass(frozen=True, slots=True)
class Caller:
    """İsteği yapan servis kullanıcısı ve uygulamadaki asıl kişi (yalnız iz)."""

    user_id: str
    name: str
    on_behalf_of: str = ""
    request_id: str = ""
    #: Olayın `causationId`'si: `Idempotency-Key`, yoksa `X-Request-Id` (§8).
    causation_id: str = ""

    @property
    def actor(self) -> str:
        """Tablet/zil denetim izine giden ad: `KM kullanıcısı (asıl kişi)`."""
        return f"{self.name} ({self.on_behalf_of})" if self.on_behalf_of else self.name


@dataclass(slots=True)
class Reply:
    status: int
    body: dict[str, Any] | None
    headers: dict[str, str] = field(default_factory=dict)
    #: Önceden kodlanmış JSON gövdesi (önbellekten). Doluysa `body` yerine gider.
    raw: bytes | None = None


def encode(body: dict[str, Any]) -> bytes:
    """`JSONResponse` ile AYNI baytlar (Starlette'in `render` ayarları)."""
    return json.dumps(body, ensure_ascii=False, allow_nan=False, indent=None,
                      separators=(",", ":")).encode("utf-8")


@dataclass(slots=True)
class _Cached:
    """Bir uç yanıtının sunucu önbelleği: anahtar, ETag, kodlanmış gövde, damga."""

    key: Any
    etag: str
    raw: bytes
    at_ms: int


def clean_header(value: str | None, limit: int = 200, *, decode: bool = True) -> str:
    """Denetim izine giden başlık: çözülür, kontrol karakteri ayıklanır, kısaltılır.

    HTTP başlığı ASCII'dir; "Ayşe Yılmaz" gibi bir ad ya yüzde kodlu UTF-8
    (`Ay%C5%9Fe`) gelir ya da ham UTF-8 bayt olarak gelip Starlette'te latin-1
    diye okunur. İkisi de burada düz metne döner.
    """
    text = str(value or "")
    if decode:
        try:
            text = text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        if "%" in text:
            text = unquote(text)
    return _CONTROL.sub("", text).strip()[:limit]


def normalize_phone(value: Any) -> str | None:
    """`5XXXXXXXXX` (10 hane) ya da None — ham metin asla dönmez."""
    if value is None or isinstance(value, bool):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if len(digits) == 12 and digits.startswith("90"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10 and digits.startswith("5"):
        return digits
    return None


def mask_phone(phone: str) -> str:
    return f"{phone[:3]}****{phone[-3:]}" if len(phone) >= 6 else "****"


def phone_subject(phone: str) -> str:
    """Sayaç tablosunda telefonun yerine geçen özet (düz numara yazılmaz)."""
    return hashlib.sha256(f"mobile_gateway:phone:{phone}".encode()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def to_datetime(value: Any) -> datetime | None:
    """Epoch (sn/ms), ISO metni ya da datetime → saat dilimli datetime.

    Kantin zamanı epoch-milisaniye verir; Laravel kaynakları ISO (`Z`) verir.
    Saat dilimsiz değer UTC sayılır (KM depo damgaları UTC'dir).
    """
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        seconds = value / 1000 if abs(value) >= 1e11 else float(value)
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("-").isdigit():
            return to_datetime(int(text))
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def encode_cursor(state: dict[str, Any]) -> str:
    """Geçidin opak sayfa imleci: base64url(JSON). Kişisel veri taşımaz.

    Defter kipinde (`m: "l"`) kantinin imleci (`c`, ilk sayfada yok) ve o
    kantin sayfasında son verilen defter satırının kimliği (`a`) durur: bir
    sonraki istek AYNI kantin sayfasını okur ve `a` ile daha yeni satırları
    atlar (sayfa ortasında kesilen istek tam yerinden sürer; arada eklenen
    yeni satır sayfayı kaydırmaz). Eski yolda (`m: "o"`) sıra numarası (`o`).
    """
    raw = json.dumps(state, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(text: str | None) -> dict[str, Any] | None:
    """`encode_cursor`ın tersi; bozuk imleç 422 `validation_error` (alan `cursor`)."""
    if text is None or not text.strip():
        return None
    raw = text.strip()
    try:
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict) or data.get("m") not in ("l", "o"):
        raise validation("Geçersiz sayfa imleci.", "cursor")
    if data["m"] == "l":
        kantin, after = data.get("c"), data.get("a")
        if (kantin is not None and not isinstance(kantin, str)) or \
                (after is not None and (not isinstance(after, int) or isinstance(after, bool))):
            raise validation("Geçersiz sayfa imleci.", "cursor")
    else:
        offset = data.get("o")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise validation("Geçersiz sayfa imleci.", "cursor")
    return data


def is_truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "evet")
    return bool(value)


def etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    for part in header.split(","):
        token = part.strip()
        token = token.removeprefix("W/")
        token = token.strip().strip('"')
        if token == "*" or token == etag:
            return True
    return False


def grade_level(value: Any) -> int | str | None:
    if isinstance(value, str):
        text = value.strip().lower()
        if text == GRADUATE:
            return GRADUATE
        if text.isdigit():
            value = int(text)
    if isinstance(value, int) and not isinstance(value, bool) and 5 <= value <= 12:
        return value
    return None


def choice(value: Any, allowed: set[str]) -> str | None:
    if isinstance(value, str) and value.strip().upper() in allowed:
        return value.strip().upper()
    return None


# ------------------------------------------------------------------ servis


class GatewayService:
    def __init__(self, *, store: ModuleStore, caps: Capabilities, config: dict[str, Any],
                 log: Any, clock: Callable[[], datetime] | None = None) -> None:
        self.repo = Repository(store)
        self.caps = caps
        self._log = log
        self._clock = clock or (lambda: datetime.now(UTC))

        self.institution_name = str(config.get("institution_name") or "Benim Başarı Dünyam")
        self.timezone = str(config.get("timezone") or "Europe/Istanbul")
        self.zone = ZoneInfo(self.timezone)

        self.otp_template = ascii_sms(str(config.get("otp_template") or DEFAULT_OTP_TEMPLATE))
        # Android SMS Retriever (otomatik doldurma): metin 11 karakterlik uygulama karmasını İÇERMELİ
        # (konumu serbest; Google). Satır sonu yerine boşlukla eklenir — Netgsm OTP hattı tek boy ASCII.
        self.otp_app_hashes = [h for h in (config.get("otp_app_hashes") or [])
                               if isinstance(h, str) and _APP_HASH_RE.match(h)][:2]
        if "{code}" not in self.otp_template:
            raise ValueError("mobile_gateway.otp_template `{code}` yer tutucusunu içermeli.")
        templates = config.get("call_templates") or {}
        self.page_template = str(templates.get("page") or DEFAULT_PAGE_TEMPLATE)

        limits = config.get("limits") or {}
        self.auth_max_failures = int(limits.get("auth_max_failures", 5))
        self.auth_window_ms = int(limits.get("auth_window_seconds", 900)) * 1000
        self.lookup_per_minute = int(limits.get("lookup_per_minute", 10))
        self.sms_per_phone_per_hour = int(limits.get("sms_per_phone_per_hour", 5))
        self.sms_per_phone_per_day = int(limits.get("sms_per_phone_per_day", 10))
        self.sms_global_per_minute = int(limits.get("sms_global_per_minute", 60))
        self.call_cooldown_ms = int(limits.get("call_cooldown_seconds", 60)) * 1000

        self.idempotency_ms = int(config.get("idempotency_hours", 24)) * HOUR_MS
        self.products_cache_ms = int(config.get("products_cache_seconds", 300)) * 1000
        self.bell_week_cache_ms = int(config.get("bell_week_cache_seconds", 60)) * 1000
        self.canteen_settings_cache_ms = \
            int(config.get("canteen_settings_cache_seconds", 60)) * 1000
        self.sms_retention_ms = int(config.get("sms_log_retention_days", 90)) * DAY_MS
        self.audit_retention_ms = int(config.get("audit_retention_days", 365)) * DAY_MS

        self._products: tuple[int, dict[str, Any]] | None = None
        #: Dizin yanıtı — dizin yeteneğinin `revision`ı değişmedikçe yeniden kurulmaz.
        #: Anahtar `balance` (bakiyeli / bakiyesiz yanıt ayrı saklanır, §9).
        self._directory: dict[bool, _Cached] = {}
        self._bell_week: _Cached | None = None
        #: Tam grup listesi — grup revizyonu değişmedikçe yeniden kurulmaz.
        self._groups: _Cached | None = None
        self.groups_cache_ms = int(config.get("groups_cache_seconds", 60)) * 1000
        self._canteen_settings: tuple[int, dict[str, Any]] | None = None
        self.canteen_totals_cache_ms = \
            int(config.get("canteen_totals_cache_seconds", 30)) * 1000
        self._canteen_totals: tuple[int, dict[str, Any]] | None = None
        self._last_purge = 0
        self._pepper: bytes | None = None
        self._pepper_lock = asyncio.Lock()

    # ------------------------------------------------------------- zaman

    def now(self) -> datetime:
        return self._clock()

    def now_ms(self) -> int:
        return int(self.now().timestamp() * 1000)

    def iso(self, value: Any, *, precise: bool = False) -> str | None:
        moment = to_datetime(value)
        if moment is None:
            return value if isinstance(value, str) and value else None
        local = moment.astimezone(self.zone)
        spec = "milliseconds" if precise and local.microsecond else "seconds"
        return local.isoformat(timespec=spec)

    def parse_since(self, text: str | None) -> str | None:
        """`since` sorgu değeri → saat dilimli ISO. URL'de `+` boşluğa dönmüşse onarılır."""
        if text is None or not text.strip():
            return None
        raw = text.strip()
        moment: datetime | None = None
        for candidate in (raw, raw.replace(" ", "+")):
            try:
                moment = datetime.fromisoformat(candidate)
                break
            except ValueError:
                continue
        if moment is None:
            raise validation("`since` ISO-8601 biçiminde olmalı.", "since")
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.zone)
        return moment.astimezone(self.zone).isoformat()

    # ------------------------------------------------------- hata çevirisi

    async def call(self, awaitable: Awaitable[T], *, reasons: dict[str, int] | None = None,
                   not_found: GatewayError | None = None) -> T:
        try:
            return await awaitable
        except CapabilityMissing as missing:
            raise capability_missing(missing.capability) from None
        except Rejected as refusal:
            path = refusal.details.get("path")
            raise GatewayError(refusal.status, refusal.code, refusal.message,
                               fields=[path] if isinstance(path, str) and path else None,
                               details=refusal.details or None) from None
        except UpstreamFailure as failure:
            raise self.translate(failure, reasons=reasons, not_found=not_found) from None

    def translate(self, failure: UpstreamFailure, *, reasons: dict[str, int] | None = None,
                  not_found: GatewayError | None = None) -> GatewayError:
        if failure.kind == "not_found":
            return not_found or student_not_found()
        if failure.kind == "business" and failure.reason:
            status = (reasons or {}).get(failure.reason)
            if status is not None:
                # exceeds_debt gibi redlerde kantinin sınırı: {balance, maxAmount}.
                return GatewayError(status, failure.reason,
                                    REASON_MESSAGES.get(failure.reason, "İstek reddedildi."),
                                    details=failure.details)
            return upstream_error("Bağlı sistem isteği reddetti.", reason=failure.reason)
        if failure.kind == "invalid":
            return validation(failure.message
                              or "Gönderilen veri bağlı sistem tarafından reddedildi.")
        if failure.kind == "conflict":
            return GatewayError(409, "conflict", "İşlem kaydın şu anki durumuyla çelişiyor.")
        # Dizin ve tablet yetenekleri de kantine dayanır; kök neden kantinse öyle söylenir.
        if failure.source in ("canteen", "directory") or failure.reason == "canteen_unavailable":
            return canteen_unavailable()
        return upstream_error()

    # ------------------------------------------------------- iz ve bakım

    async def audit(self, action: str, caller: Caller, *, student_id: str = "",
                    result: str = "ok", detail: dict[str, Any] | None = None) -> None:
        await self.repo.audit(
            at=self.now_ms(), action=action, student_id=student_id, user_id=caller.user_id,
            on_behalf_of=caller.on_behalf_of, request_id=caller.request_id, result=result,
            detail=detail or {},
        )
        self._log.info("mobil geçit işlemi", action=action, result=result,
                       request_id=caller.request_id or None)

    async def audited(self, action: str, caller: Caller, student_id: str,
                      detail: dict[str, Any], work: Awaitable[T]) -> T:
        """İşlemi koşar ve sonucu (başarı ya da hata kodu) denetim izine yazar."""
        try:
            value = await work
        except GatewayError as error:
            await self.audit(action, caller, student_id=student_id, result=error.code,
                             detail=detail)
            raise
        await self.audit(action, caller, student_id=student_id, detail=detail)
        return value

    async def tick(self) -> None:
        """Süresi dolan satırları en çok on dakikada bir temizler."""
        now = self.now_ms()
        if now - self._last_purge < PURGE_EVERY_MS:
            return
        self._last_purge = now
        await self.repo.purge(
            idempotency_before=now - self.idempotency_ms, hits_before=now - DAY_MS,
            sms_before=now - self.sms_retention_ms, audit_before=now - self.audit_retention_ms,
        )

    # ----------------------------------------------------------- idempotensi

    async def idempotent(self, key: str | None, route: str, payload: Any, status: int,
                         produce: Callable[[], Awaitable[dict[str, Any]]]) -> Reply:
        """Aynı anahtar + aynı gövde → saklı yanıt; farklı gövde → 409.

        Yalnız BAŞARILI yanıt saklanır: hata dönen istek aynı anahtarla
        yeniden denenebilir (ör. kantin geçici olarak kapalıydı).
        """
        await self.tick()
        if key is None or key == "":
            return Reply(status, await produce())
        if not _IDEMPOTENCY_KEY.match(key):
            raise validation("Idempotency-Key 1–255 görünür ASCII karakter olmalı.",
                             "Idempotency-Key")

        request_hash = hmac.new(await self.pepper(), canonical({"route": route, "body": payload})
                                .encode("utf-8"), hashlib.sha256).hexdigest()
        now = self.now_ms()
        record = await self.repo.idempotency_get(key)
        if record is not None and (
            record.created_at < now - self.idempotency_ms
            or (record.status_code == 0 and record.created_at < now - STALE_CLAIM_MS)
        ):
            await self.repo.idempotency_drop(key)
            record = None

        if record is None:
            owner = uuid4().hex
            if await self.repo.idempotency_claim(key, route, request_hash, owner, now):
                try:
                    body = await produce()
                except BaseException:
                    await self.repo.idempotency_release(key, owner)
                    raise
                await self.repo.idempotency_finish(key, owner, status, body)
                return Reply(status, body)
            record = await self.repo.idempotency_get(key)

        if record is None or record.route != route or record.request_hash != request_hash:
            raise GatewayError(409, "idempotency_conflict",
                               "Bu Idempotency-Key farklı bir istekle kullanılmış.")
        if record.status_code == 0:
            raise GatewayError(409, "idempotency_conflict",
                               "Aynı Idempotency-Key ile gönderilen istek hâlâ işleniyor.",
                               retryAfterSeconds=2)
        return Reply(record.status_code, json.loads(record.response),
                     {"Idempotent-Replayed": "true"})

    async def pepper(self) -> bytes:
        """İdempotensi özetinin anahtarı (kasadan, süreç boyunca önbellekte).

        Kasa okunamazsa süreç içi rastgele anahtar kullanılır: kod yine
        tabloya geri bulunabilir biçimde yazılmaz; bedeli, yeniden başlatma
        sonrası aynı anahtarla gelen tekrarın 409 alabilmesidir.
        """
        if self._pepper is not None:
            return self._pepper
        async with self._pepper_lock:
            if self._pepper is None:
                try:
                    self._pepper = (await self.caps.idempotency_pepper()).encode("utf-8")
                except (CapabilityMissing, UpstreamFailure):
                    self._log.warning("idempotensi anahtarı kasadan okunamadı; süreç içi anahtar")
                    self._pepper = secrets.token_bytes(32)
        return self._pepper

    # ------------------------------------------------------- sayaç ve kilit

    async def _ensure_unlocked(self, bucket: str, subject: str) -> None:
        now = self.now_ms()
        window = await self.repo.window(bucket, subject, now - self.auth_window_ms)
        if window.count >= self.auth_max_failures and window.oldest is not None:
            raise locked(math.ceil((window.oldest + self.auth_window_ms - now) / 1000))

    async def _lookup_budget(self) -> None:
        """Kantin lookup-code bütçesi: geçidin payı dakikada `lookup_per_minute`."""
        now = self.now_ms()
        window = await self.repo.window("lookup", "*", now - MINUTE_MS)
        if window.count >= self.lookup_per_minute and window.oldest is not None:
            raise rate_limited(math.ceil((window.oldest + MINUTE_MS - now) / 1000),
                               "Şifre doğrulama şu anda yoğun. Biraz sonra yeniden deneyin.")
        await self.repo.hit("lookup", "*", now)

    # ================================================================ §1 dizin

    async def directory(self, since_text: str | None, if_none_match: str | None, *,
                        ids_text: str | None = None, balance: bool = True) -> Reply:
        """§1 dizin (+ §9: `ids`, `balance=false`, `since` → `removed`).

        Yanıt, dizin yeteneğinin `revision`ı değişmedikçe yeniden KURULMAZ.
        2.000 öğrencilik listeyi beyaz listeden geçirmek, sıralamak, kanonik
        JSON'unu özetlemek ve kodlamak isteğin asıl maliyetiydi. Dizin yeteneği
        verisi değiştiğinde `revision`ını değiştirir (kantin listesi tazelendi
        ya da profil yazıldı); aynı revizyonda veri birebir aynıdır, bu yüzden
        hesaplanmış ETag ve kodlanmış gövde aynen kullanılır. Yalnız TAM liste
        saklanır (bakiyeli ve bakiyesiz ayrı). `revision` vermeyen (eski)
        yetenekte her istek baştan kurulur.

        `balance=False`: öğrencilerde `canteen.balance` YOK ve ETag bakiyesiz
        hesaplanır — okul saatinde satış ETag'i değiştirmez (§9).
        `ids`: en çok 100 öğrenci; `full: false`, `classes` boş, bulunamayanlar
        `missingIds`; ETag'siz. `since`: yanıt `removed` (90 günlük mezar
        taşı) taşır; 90 günden eski `since` → 422.

        `generatedAt`: yetenek `asOf` verirse odur (kantin listesinin okunduğu
        an — bir sonraki `since` için güvenli imleç), vermezse şimdi.
        """
        if ids_text is not None:
            return await self._directory_ids(ids_text, balance)
        since = self.parse_since(since_text)
        if since is not None and datetime.fromisoformat(since) < \
                self.now() - timedelta(days=DIRECTORY_SINCE_MAX_DAYS):
            raise validation(f"`since` en çok {DIRECTORY_SINCE_MAX_DAYS} gün geriye gidebilir; "
                             "tam liste isteyin.", "since")
        listing = await self.call(self.caps.directory_list(since))
        revision = listing.get("revision")
        keep = revision is not None and since is None
        cached = self._directory.get(balance) if keep else None
        if cached is None or cached.key != revision:
            students = sorted(
                (self.student(record, balance=balance)
                 for record in listing.get("students") or []
                 if isinstance(record, dict) and record.get("id")),
                key=lambda item: str(item["id"]),
            )
            classes = sorted({str(name) for name in listing.get("classes") or [] if name})
            core: dict[str, Any] = {
                "full": since is None,
                "complete": listing.get("complete") is True,
                "students": students,
                "classes": classes,
            }
            if since is not None:
                removed = listing.get("removed")
                if not isinstance(removed, list):
                    removed = await self.call(self.caps.directory_removed(since))
                core["removed"] = sorted(
                    ({"id": str(item["id"]), "at": self.iso(item.get("at"))}
                     for item in removed if isinstance(item, dict) and item.get("id")),
                    key=lambda item: str(item["id"]))
            etag = digest(core)
            generated = self.iso(listing.get("asOf")) or self.iso(self.now())
            cached = _Cached(key=revision, etag=etag, at_ms=self.now_ms(),
                             raw=encode({"generatedAt": generated, **core, "etag": etag}))
            if keep:
                self._directory[balance] = cached
        headers = {"ETag": f'"{cached.etag}"', "Cache-Control": "private, no-cache"}
        if etag_matches(if_none_match, cached.etag):
            return Reply(304, None, headers)
        return Reply(200, None, headers, raw=cached.raw)

    async def _directory_ids(self, ids_text: str, balance: bool) -> Reply:
        ids = list(dict.fromkeys(item.strip() for item in ids_text.split(",") if item.strip()))
        if not ids or len(ids) > DIRECTORY_IDS_MAX:
            raise validation(f"`ids` 1–{DIRECTORY_IDS_MAX} öğrenci kimliği olmalı.", "ids")
        result = await self.call(self.caps.directory_many(ids))
        students = sorted(
            (self.student(record, balance=balance) for record in result.get("students") or []
             if isinstance(record, dict) and record.get("id") in ids),
            key=lambda item: str(item["id"]),
        )
        found = {item["id"] for item in students}
        body = {"generatedAt": self.iso(self.now()), "full": False, "complete": True,
                "students": students, "classes": [],
                "missingIds": [item for item in ids if item not in found]}
        return Reply(200, body, {"Cache-Control": "private, no-cache"})

    async def student_one(self, student_id: str, *, balance: bool = True) -> dict[str, Any]:
        """§9 `GET /students/{id}` — dizin öğesiyle birebir; silinmiş ya da yoksa 404."""
        record = await self.call(self.caps.directory_get(student_id))
        if not record or not record.get("id"):
            raise student_not_found()
        return self.student(record, balance=balance)

    async def canteen_balances(self, ids_text: str | None,
                               if_none_match: str | None) -> Reply:
        """§9 `GET /canteen/balances` — kantin `GET /api/students/balances` üstünde.

        ETag kantin defterinin son satır kimliğidir; değişmediyse 304.
        """
        ids: list[str] | None = None
        if ids_text is not None:
            ids = list(dict.fromkeys(item.strip() for item in ids_text.split(",")
                                     if item.strip()))
            if not ids or len(ids) > BALANCES_IDS_MAX:
                raise validation(f"`ids` 1–{BALANCES_IDS_MAX} öğrenci kimliği olmalı.", "ids")
        try:
            page = await self.caps.canteen_balances(ids, if_none_match or None)
        except CapabilityMissing as missing:
            raise capability_missing(missing.capability) from None
        except UpstreamFailure as failure:
            if failure.reason == "not_supported":
                raise capability_missing("canteen.api.balances") from None
            raise self.translate(failure) from None
        if page is None:
            return Reply(304, None, {"ETag": str(if_none_match),
                                     "Cache-Control": "private, no-cache"})
        ledger = int_or_none(page.get("ledgerSeq")) or 0
        items = [
            {"studentId": str(item.get("opaqueId") or item.get("studentId") or ""),
             "balance": int_or_none(item.get("balance")) or 0,
             "ledgerSeq": int_or_none(item.get("ledgerSeq")) or 0}
            for item in page.get("items") or [] if isinstance(item, dict)
        ]
        items = [item for item in items if item["studentId"]]
        etag = str(page.get("etag") or f'"{ledger}"')
        return Reply(200, {"ledgerSeq": ledger, "items": items},
                     {"ETag": etag, "Cache-Control": "private, no-cache"})

    async def events_after(self, after: int, limit: int) -> dict[str, Any]:
        """§9 `GET /events?after=&limit=` — KM outbox'undan çekme (itmenin yedeği).

        Zarf webhook ile aynıdır. `after` saklama penceresinden eskiyse
        `410 events_expired`: istemci tam uzlaştırma yapar.
        """
        box = self.caps.outbox()
        if box is None:
            raise capability_missing("events.outbox")
        if after < 0:
            raise validation("`after` 0 ya da pozitif olmalı.", "after")
        size = max(1, min(int(limit), EVENTS_LIMIT_MAX))
        if await box.expired(after):
            raise GatewayError(410, "events_expired",
                               "İstenen olaylar saklama süresini aştı; tam uzlaştırma yapın.")
        items = await box.read_after(after, size)
        last = int(items[-1]["seq"]) if items else int(after)
        has_more = len(items) == size and last < int(await box.last_seq())
        return {"items": items, "lastSeq": last, "hasMore": has_more}

    def student(self, record: dict[str, Any], *, balance: bool = True) -> dict[str, Any]:
        """Dizin kaydı → sözleşme §1 (+ §9) öğrenci nesnesi (beyaz liste).

        Erişim kodu, QR anahtarı ve bilinmeyen her alan burada DÜŞER.
        `balance=False`: `canteen.balance` anahtarı hiç yazılmaz.
        """
        issues = [str(item) for item in record.get("phoneIssues") or []
                  if isinstance(item, str)]

        def note(name: str) -> None:
            if name not in issues:
                issues.append(name)

        raw_phone = record.get("studentPhone")
        student_phone = normalize_phone(raw_phone)
        if raw_phone and student_phone is None:
            note("studentPhone")

        guardians: list[dict[str, Any]] = []
        for guardian in record.get("guardians") or []:
            if not isinstance(guardian, dict):
                continue
            slot = int_or_none(guardian.get("slot"))
            if slot not in GUARDIAN_SLOTS:
                continue
            phone = normalize_phone(guardian.get("phone"))
            if phone is None:
                if guardian.get("phone"):
                    note(f"guardian{slot}Phone")
                continue
            name = str(guardian.get("name") or "").strip() or None
            relation = str(guardian.get("relation") or "").strip().lower()
            guardians.append({"slot": slot, "name": name, "phone": phone,
                              "relation": relation if relation in GUARDIAN_RELATIONS else None,
                              "primary": slot == 1})
        guardians.sort(key=lambda item: int(item["slot"]))

        canteen = record.get("canteen")
        canteen_out: dict[str, Any] | None = None
        if isinstance(canteen, dict):
            canteen_out = {
                "balance": int_or_none(canteen.get("balance")),
                "spendingLimit": int_or_none(canteen.get("spendingLimit")),
                "isBlocked": bool(canteen.get("isBlocked")),
                "dailyLimit": int_or_none(canteen.get("dailyLimit")),
                "guardianBlocked": bool(canteen.get("guardianBlocked")),
            }
            if not balance:
                canteen_out.pop("balance")

        status = str(record.get("status") or "active")
        status = status if status in STUDENT_STATUSES else "active"
        reason = record.get("statusReason") if status == "inactive" else None
        first = str(record.get("firstName") or "").strip()
        last = str(record.get("lastName") or "").strip()
        display = str(record.get("displayName") or "").strip() or f"{first} {last}".strip()
        school_no = record.get("schoolNo")
        out = {
            "id": str(record["id"]),
            "firstName": first,
            "lastName": last,
            "displayName": display,
            "className": str(record.get("className") or "").strip() or None,
            "schoolNo": str(school_no).strip() or None if school_no is not None else None,
            "studentPhone": student_phone,
            "guardians": guardians,
            "canteen": canteen_out,
            "gradeLevel": grade_level(record.get("gradeLevel")),
            "track": choice(record.get("track"), TRACKS),
            "examTarget": choice(record.get("examTarget"), EXAM_TARGETS),
            "updatedAt": self.iso(record.get("updatedAt")),
            "phoneIssues": issues,
            # Merkezi senkron (§9): yaşam döngüsü, revizyon, kantine yazılma durumu.
            "status": status,
            "statusReason": reason if reason in STATUS_REASONS else None,
            "statusSince": self.iso(record.get("statusSince")),
            "revision": int_or_none(record.get("revision")) or 0,
            "canteenSync": "pending" if record.get("canteenSync") == "pending" else "ok",
        }
        if "classLevel" in record:
            # Sınıf seviyeyi taşır (bbd_students göç 008): ana sınıfın tanımı ya da null.
            # Eski öğrenci modülünde anahtar hiç yoktur; Akademi o zaman addan tahmin eder.
            level = record.get("classLevel")
            out["classLevel"] = {
                "gradeLevel": grade_level(level.get("gradeLevel")),
                "track": choice(level.get("track"), TRACKS),
                "examTarget": choice(level.get("examTarget"), EXAM_TARGETS),
            } if isinstance(level, dict) and grade_level(level.get("gradeLevel")) is not None else None
        return out

    @staticmethod
    def phones(record: dict[str, Any]) -> set[str]:
        """Öğrencinin kendi ve (en çok dört) velisinin normalize telefonları."""
        found = {normalize_phone(record.get("studentPhone"))}
        for guardian in record.get("guardians") or []:
            if isinstance(guardian, dict) and int_or_none(guardian.get("slot")) in GUARDIAN_SLOTS:
                found.add(normalize_phone(guardian.get("phone")))
        return {phone for phone in found if phone}

    # ======================================================== §2 öğrenci şifresi

    async def auth_student_code(self, phone: str, code: str) -> dict[str, Any]:
        """Telefon + şifre → öğrenci. Kantin `lookup-code` YALNIZ BİR KEZ çağrılır."""
        normalized = normalize_phone(phone)
        if normalized is None:
            raise validation("Telefon 5XXXXXXXXX biçiminde olmalı.", "phone")
        await self.tick()
        subject = phone_subject(normalized)
        await self._ensure_unlocked("auth_phone_fail", subject)
        await self._lookup_budget()

        student_id = await self.call(self.caps.lookup_code(code))
        if student_id:
            record = await self.call(self.caps.directory_get(student_id))
            if record is not None and normalized in self.phones(record):
                await self.repo.clear("auth_phone_fail", subject)
                return {"match": True, "studentId": student_id}
        await self.repo.hit("auth_phone_fail", subject, self.now_ms())
        return {"match": False}

    async def verify_code(self, student_id: str, code: str) -> dict[str, Any]:
        """Kod bu öğrencinin mi? Başkasınınsa da yalnız `valid: false` (sızıntı yok)."""
        await self.tick()
        await self._ensure_unlocked("verify_student_fail", student_id)
        await self._lookup_budget()
        found = await self.call(self.caps.lookup_code(code))
        if found == student_id:
            await self.repo.clear("verify_student_fail", student_id)
            return {"valid": True}
        await self.repo.hit("verify_student_fail", student_id, self.now_ms())
        return {"valid": False}

    # ================================================================ §3 kantin

    async def parallel(self, *calls: Awaitable[Any]) -> list[Any]:
        """Bağımsız yetenek çağrılarını AYNI ANDA koşar (`self.call` ile sarılı).

        Hepsi biter (her biri kendi süre sınırıyla), sonra ilk hata VERİLİŞ
        SIRASIYLA yükseltilir — sıralı koddaki hata önceliği korunur (ör. önce
        öğrenci yoksa `student_not_found`). Biri patlasa da diğerinin hatası
        sahipsiz kalmaz.
        """
        results = await asyncio.gather(*(self.call(item) for item in calls),
                                       return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return list(results)

    async def canteen_summary(self, student_id: str, days: int) -> dict[str, Any]:
        """§3 kantin özeti — kantinin hesap dökümünden TEK istekle.

        Defter ucu (kantin §1.7) öğrenciyi, bekleyen talebi ve aralıktaki
        satış/tahsilat satırlarını birlikte verir. Aralık 200 satırı aşarsa
        kalan kısım zaman dilimleriyle aynı anda okunur (`_ledger_rest`;
        öğrencinin satırları, kurum geneli 5000 satır kesintisi yoktur). Kantin bu ucu tanımıyorsa (eski sürüm)
        ya da kantin istemcisi `ledger` bilmiyorsa eski yol: dört bağımsız
        okuma aynı anda. Yanıt biçimi iki yolda da aynıdır (sözleşme v1).
        """
        now = self.now_ms()
        start = now - days * DAY_MS
        try:
            return await self._summary_from_ledger(student_id, start)
        except LedgerUnavailable:
            return await self._summary_legacy(student_id, start, now + MINUTE_MS)

    async def _summary_from_ledger(self, student_id: str, start: int) -> dict[str, Any]:
        first = await self.call(self.caps.canteen_ledger(student_id, limit=LEDGER_PAGE_MAX,
                                                         from_ms=start))
        entries: list[dict[str, Any]] = list(first["items"])
        if first["hasMore"] and first["nextCursor"] and entries:
            entries = self._unique_entries(
                entries + await self._ledger_rest(student_id, start, first))

        student = first["student"]
        if student is None:
            # Kantin ilk sayfada öğrenciyi vermediyse (beklenmez) ayrıca okunur.
            student = await self.call(self.caps.canteen_student(student_id))
        pending = first["pendingPaymentRequest"]
        transactions = [self.transaction(entry["sale"]) for entry in entries
                        if entry.get("kind") in SALE_KINDS and isinstance(entry.get("sale"), dict)]
        collections = [self.ledger_collection(entry) for entry in entries
                       if entry.get("kind") in COLLECTION_KINDS]
        return self.summary_body(student, transactions, collections,
                                 [pending] if pending is not None else [])

    async def _ledger_rest(self, student_id: str, start: int,
                           first: dict[str, Any]) -> list[dict[str, Any]]:
        """İlk sayfadan sonra kalan aralık `[start, en eski satır]` — dilimler aynı anda.

        Dilim sayısı ilk sayfanın yoğunluğundan kestirilir (1–`SUMMARY_LEDGER_SLICES`).
        En yeni dilim kantinin imleciyle (`cursor` = ilk sayfanın `nextCursor`'u,
        yalnız `from`) okunur, daha eskiler `[from, to)` ile; her dilim kendi
        içinde imleçle sonuna kadar izlenir. Defter kimliği yazılma sırasıdır,
        zaman sınırında iki dilime düşen satır kimlikle ayıklanır (kayıp yok:
        ilk sayfa en yeni 200 kimliği, imleçli dilim ondan küçükleri en eski
        satırın anından, diğerleri daha öncesini kapsar).
        """
        rows = first["items"]
        oldest = int_or_none(rows[-1].get("at")) or start
        oldest = max(start, oldest)
        newest = max(oldest + 1, int_or_none(rows[0].get("at")) or oldest + 1, self.now_ms())
        expected = len(rows) * (oldest - start) / max(1, newest - oldest)
        slices = max(1, min(SUMMARY_LEDGER_SLICES, math.ceil(expected / LEDGER_PAGE_MAX)))
        bounds = [start + (oldest - start) * index // slices for index in range(slices)]
        budget = max(1, (SUMMARY_LEDGER_PAGES - 1) // slices)
        walks = [self._ledger_walk(student_id, bounds[-1], None, first["nextCursor"], budget)]
        walks += [self._ledger_walk(student_id, bounds[index], bounds[index + 1], None, budget)
                  for index in range(slices - 1)]
        parts = await self.parallel(*walks)
        return [row for part in parts for row in part]

    async def _ledger_walk(self, student_id: str, start: int, end: int | None,
                           cursor: str | None, budget: int) -> list[dict[str, Any]]:
        """Bir defter diliminin bütün satırları, imleç sonuna kadar (en çok `budget` sayfa)."""
        found: list[dict[str, Any]] = []
        for _ in range(budget):
            page = await self.call(self.caps.canteen_ledger(
                student_id, limit=LEDGER_PAGE_MAX, cursor=cursor, from_ms=start, to_ms=end))
            found.extend(page["items"])
            if not (page["hasMore"] and page["nextCursor"]) or page["nextCursor"] == cursor:
                return found
            cursor = page["nextCursor"]
        self._log.warning("kantin özeti defter sayfa sınırına ulaştı",
                          student_id=student_id, pages=budget)
        return found

    @staticmethod
    def _unique_entries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Defter satırları kimliğe göre tekil, yeniden eskiye."""
        unique: dict[Any, dict[str, Any]] = {}
        for row in rows:
            unique.setdefault(row.get("id"), row)
        return sorted(unique.values(), key=lambda row: int_or_none(row.get("id")) or 0,
                      reverse=True)

    async def _summary_legacy(self, student_id: str, start: int, end: int) -> dict[str, Any]:
        """Defter ucu olmayan kantin: dört kantin okuması aynı anda (süre en
        yavaşı kadar). İşlemler aralıkta en çok 5000 satırdır (README)."""
        student, transactions, collections, requests = await self.parallel(
            self.caps.canteen_student(student_id),
            self.caps.canteen_transactions(student_id, start, end),
            self.caps.canteen_collections(student_id, start, end),
            self.caps.payment_requests_for(student_id),
        )
        return self.summary_body(
            student, [self.transaction(item) for item in transactions],
            [self.collection(item) for item in collections], requests,
        )

    def summary_body(self, student: dict[str, Any], transactions: list[dict[str, Any]],
                     collections: list[dict[str, Any]],
                     requests: list[dict[str, Any]]) -> dict[str, Any]:
        """§3 yanıtı — iki yolun ortak biçimi (sözleşme v1, alan alan aynı).

        `requests` kantinin ham talep satırlarıdır; yalnız durumu açıkça
        PENDING olan en yenisi `pendingPaymentRequest` olur.
        """
        pending = [self.payment_request(item) for item in requests
                   if str(item.get("status") or "").upper() == "PENDING"]
        pending.sort(key=lambda item: str(item["createdAt"] or ""), reverse=True)
        controls = self.controls_body(student)
        return {
            "balance": controls["balance"],
            "spendingLimit": controls["spendingLimit"],
            "isBlocked": controls["isBlocked"],
            "dailyLimit": controls["dailyLimit"],
            "guardianBlocked": controls["guardianBlocked"],
            "restrictedProductIds": controls["restrictedProductIds"],
            "spentToday": controls["spentToday"],
            "transactions": sorted(transactions, key=lambda item: str(item["at"] or ""),
                                   reverse=True),
            "collections": sorted(collections, key=lambda item: str(item["at"] or ""),
                                  reverse=True),
            "pendingPaymentRequest": (
                {key: pending[0][key] for key in
                 ("ref", "amount", "linkUrl", "createdAt", "payToken", "amountFixed")}
                if pending else None
            ),
        }

    # ------------------------------------------- imleçli liste (defterden)

    def page_window(self, date_from: date | None,
                    date_to: date | None) -> tuple[int | None, int | None]:
        """`from`/`to` günleri (dahil, kurum saat dilimi) → epoch-ms `[from, to)`."""
        if date_from is not None and date_to is not None and date_from > date_to:
            raise validation("`from` tarihi `to` tarihinden sonra olamaz.", "from", "to")

        def midnight(day: date) -> int:
            local = datetime(day.year, day.month, day.day, tzinfo=self.zone)
            return int(local.timestamp() * 1000)

        start = midnight(date_from) if date_from is not None else None
        end = midnight(date_to + timedelta(days=1)) if date_to is not None else None
        return start, end

    async def canteen_transactions_page(self, student_id: str, date_from: date | None,
                                        date_to: date | None, cursor: str | None,
                                        limit: int) -> dict[str, Any]:
        """Öğrencinin satışları, yeniden eskiye, imleçli —
        `{items, nextCursor, hasMore}` (Akademi `CanteenTransactionPage`).

        Satır §3 `transactions` satırıdır; kalemler ayrıca `productId` taşır.
        İptal edilen satış listede kalır (`reversed: true`); iptal kaydı ayrı
        satır olmaz.
        """
        start, end = self.page_window(date_from, date_to)
        state = decode_cursor(cursor)
        try:
            entries, next_cursor = await self._ledger_page(
                student_id, SALE_KINDS, start, end, state, limit,
                fetch=min(LEDGER_PAGE_MAX, limit + 25))
        except LedgerUnavailable:
            rows = await self.call(self.caps.canteen_transactions(
                student_id, *self._fallback_window(start, end)))
            items = sorted((self.transaction(item, product_ids=True) for item in rows),
                           key=lambda item: str(item["at"] or ""), reverse=True)
            return self._offset_page(items, state, limit)
        items = [self.transaction(entry["sale"], product_ids=True) for entry in entries]
        return {"items": items, "nextCursor": next_cursor, "hasMore": next_cursor is not None}

    async def canteen_collections_page(self, student_id: str, date_from: date | None,
                                       date_to: date | None, cursor: str | None,
                                       limit: int) -> dict[str, Any]:
        """Öğrencinin tahsilatları (online ödeme + nakit), yeniden eskiye, imleçli —
        `{items: [{id, at, amount, method, ref}], nextCursor, hasMore}`."""
        start, end = self.page_window(date_from, date_to)
        state = decode_cursor(cursor)
        try:
            entries, next_cursor = await self._ledger_page(
                student_id, COLLECTION_KINDS, start, end, state, limit, fetch=LEDGER_PAGE_MAX)
        except LedgerUnavailable:
            rows = await self.call(self.caps.canteen_collections(
                student_id, *self._fallback_window(start, end)))
            items = sorted((self.collection(item, identified=True) for item in rows),
                           key=lambda item: str(item["at"] or ""), reverse=True)
            return self._offset_page(items, state, limit)
        items = [self.ledger_collection(entry, identified=True) for entry in entries]
        return {"items": items, "nextCursor": next_cursor, "hasMore": next_cursor is not None}

    async def _ledger_page(self, student_id: str, kinds: frozenset[str], start: int | None,
                           end: int | None, state: dict[str, Any] | None, limit: int, *,
                           fetch: int) -> tuple[list[dict[str, Any]], str | None]:
        """Defterden `kinds` türündeki en çok `limit` satır + geçidin sonraki imleci.

        Kantinin imleci olduğu gibi kullanılır (opak; üretilmez). Sayfa bir
        kantin sayfasının ortasında dolarsa imleç o kantin sayfasını ve son
        verilen satırın kimliğini taşır (bkz. `encode_cursor`).
        """
        # Eski yolun imleci (`m: "o"`): defter hâlâ yoksa ilk çağrı
        # `LedgerUnavailable` verir ve çağıran eski yolda sürer (istemci bunu
        # kantine gitmeden bilir); defter artık varsa kantin bu arada
        # güncellenmiştir — sıra numarası deftere çevrilemez, istemci baştan başlar.
        offset_cursor = state is not None and state["m"] != "l"
        kantin_cursor: str | None = None if offset_cursor or not state else state.get("c")
        after: int | None = None if offset_cursor or not state else state.get("a")
        need_sale = bool(kinds & SALE_KINDS)

        def wanted(row: dict[str, Any]) -> bool:
            return row.get("kind") in kinds and \
                (not need_sale or isinstance(row.get("sale"), dict))

        found: list[dict[str, Any]] = []
        for _ in range(PAGE_FILL_CALLS):
            page = await self.call(self.caps.canteen_ledger(
                student_id, limit=fetch, cursor=kantin_cursor, from_ms=start, to_ms=end))
            if offset_cursor:
                raise validation("Sayfa imlecinin süresi doldu; liste baştan istenmeli.",
                                 "cursor")
            rows = [row for row in page["items"]
                    if after is None or (int_or_none(row.get("id")) or 0) < after]
            for index, row in enumerate(rows):
                if not wanted(row):
                    continue
                found.append(row)
                if len(found) == limit:
                    rest = rows[index + 1:]
                    if any(wanted(item) for item in rest):
                        return found, encode_cursor({"m": "l", "c": kantin_cursor,
                                                     "a": int_or_none(row.get("id"))})
                    if page["hasMore"] and page["nextCursor"]:
                        return found, encode_cursor({"m": "l", "c": page["nextCursor"]})
                    return found, None
            if not (page["hasMore"] and page["nextCursor"]) or page["nextCursor"] == kantin_cursor:
                return found, None
            kantin_cursor, after = page["nextCursor"], None
        # Sayfa dolmadı ama kantine yeterince gidildi: kısa sayfa, geçerli imleç.
        return found, encode_cursor({"m": "l", "c": kantin_cursor})

    def _fallback_window(self, start: int | None, end: int | None) -> tuple[int, int]:
        """Eski yolun aralığı: `from` yoksa son `FALLBACK_DAYS` gün, `to` yoksa şimdi."""
        now = self.now_ms()
        return (start if start is not None else now - FALLBACK_DAYS * DAY_MS,
                end if end is not None else now + MINUTE_MS)

    @staticmethod
    def _offset_page(items: list[dict[str, Any]], state: dict[str, Any] | None,
                     limit: int) -> dict[str, Any]:
        """Eski yolun sayfası: aralık her istekte yeniden okunur, sıra numarasıyla kesilir."""
        if state is not None and state["m"] != "o":
            raise validation("Sayfa imlecinin süresi doldu; liste baştan istenmeli.", "cursor")
        offset = int(state["o"]) if state else 0
        chunk = items[offset:offset + limit]
        more = offset + limit < len(items)
        return {"items": chunk,
                "nextCursor": encode_cursor({"m": "o", "o": offset + limit}) if more else None,
                "hasMore": more}

    def transaction(self, item: dict[str, Any], *, product_ids: bool = False) -> dict[str, Any]:
        """Satış satırı (§3). Kaynak kantinin işlem satırı ya da defter satırının
        `sale` alanıdır — ikisi aynı alanları taşır. `product_ids` yalnız imleçli
        liste ucunda kalemlere `productId` ekler (§3 özeti değişmez)."""
        lines = []
        for line in item.get("items") or []:
            if not isinstance(line, dict):
                continue
            qty = int_or_none(line.get("qty")) or 0
            unit = int_or_none(line.get("unitPrice")) or 0
            total = int_or_none(line.get("lineTotal"))
            entry: dict[str, Any] = {}
            if product_ids:
                product = line.get("productId")
                entry["productId"] = str(product) if product not in (None, "") else None
            entry.update({
                "name": str(line.get("name") or line.get("productName") or ""),
                "qty": qty, "unitPrice": unit,
                "lineTotal": total if total is not None else qty * unit,
            })
            lines.append(entry)
        identifier = item.get("serverId") or item.get("id") or item.get("localId") or ""
        return {
            "id": str(identifier),
            "at": self.iso(item.get("createdAt")),
            "total": int_or_none(item.get("total")) or 0,
            "reversed": bool(item.get("reversedAt") or item.get("reversed")),
            "items": lines,
        }

    def collection(self, item: dict[str, Any], *, identified: bool = False) -> dict[str, Any]:
        """Tahsilat satırı — eski yolun (kantin tahsilat raporu) biçiminden."""
        method = str(item.get("method") or item.get("source") or item.get("type")
                     or item.get("channel") or "").upper()
        moment = item.get("createdAt") or item.get("at") or item.get("date")
        amount = item.get("amount")
        if amount is None:
            amount = item.get("total") if item.get("total") is not None else item.get("credit")
        body: dict[str, Any] = {
            "at": self.iso(moment),
            "amount": int_or_none(amount) or 0,
            "method": "CASH" if ("CASH" in method or "NAKIT" in method) else "PAYMENT",
        }
        if identified:
            ref = item.get("ref") or item.get("localId")
            body = {"id": str(item.get("id") or ref or ""), **body,
                    "ref": str(ref) if ref else None}
        return body

    def ledger_collection(self, entry: dict[str, Any], *,
                          identified: bool = False) -> dict[str, Any]:
        """Tahsilat satırı — defter satırından (`kind`: payment | cash)."""
        body: dict[str, Any] = {
            "at": self.iso(entry.get("at")),
            "amount": int_or_none(entry.get("amount")) or 0,
            "method": "CASH" if entry.get("kind") == "cash" else "PAYMENT",
        }
        if identified:
            ref = entry.get("ref")
            body = {"id": str(entry.get("id") or ""), **body, "ref": str(ref) if ref else None}
        return body

    def payment_request(self, item: dict[str, Any]) -> dict[str, Any]:
        """Ödeme talebi satırı (öğrenci listesi ve bekleyen talep)."""
        return {
            "ref": str(item.get("ref") or ""),
            "amount": int_or_none(item.get("amount")) or 0,
            "status": str(item.get("status") or "PENDING").upper(),
            "createdAt": self.iso(item.get("createdAt")),
            "paidAt": self.iso(item.get("paidAt")),
            "linkUrl": item.get("linkUrl") or item.get("shortUrl") or None,
            "payToken": item.get("payToken") or None,
            "amountFixed": is_truthy(item.get("amountFixed")),
        }

    async def canteen_qr(self, student_id: str) -> dict[str, Any]:
        # QR üreteci her kimliği kodlar; olmayan öğrenciye kart metni verilmez.
        # Varlık sınaması ve metin üretimi bağımsız: aynı anda. Hata önceliği
        # sıralı koddaki gibi: önce dizin hatası, sonra "öğrenci yok", en son
        # metin üretiminin hatası (olmayan öğrenciye QR hatası değil 404 döner).
        record, text = await asyncio.gather(self.call(self.caps.directory_get(student_id)),
                                            self.call(self.caps.card_qr_text(student_id)),
                                            return_exceptions=True)
        if isinstance(record, BaseException):
            raise record
        if record is None:
            raise student_not_found()
        if isinstance(text, BaseException):
            raise text
        if not text:
            raise student_not_found()
        return {"qrText": text, "version": 1}

    async def student_payment_requests(self, student_id: str,
                                       ret: str | None = None) -> dict[str, Any]:
        rows = await self.call(self.caps.payment_requests_for(student_id, ret))
        items = [self.payment_request(item) for item in rows]
        items.sort(key=lambda item: str(item["createdAt"] or ""), reverse=True)
        return {"items": items}

    async def create_payment_request(self, student_id: str, amount: int | None,
                                     caller: Caller, ret: str | None = None) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.create_payment_request(student_id, amount, ret),
                                     reasons=PAYMENT_REQUEST_REASONS)
            fixed = result.get("amountFixed")
            ref = str(result.get("ref") or "")
            status = str(result.get("status") or "PENDING").upper()
            # §8: Akademi'nin açtığı talep → olay (`origin: akademi`); kantinin
            # aynı talep için iteceği olay yankı belleğiyle bastırılır.
            self.caps.remember_payment_request(student_id, ref)
            await self.caps.emit("canteen.payment_request_changed", ("student", student_id),
                                 fields=["paymentRequests"],
                                 data={"ref": ref, "status": status},
                                 causation_id=caller.causation_id)
            return {
                "ref": str(result.get("ref") or ""),
                "amount": int_or_none(result.get("amount")) or int(amount or 0),
                "status": str(result.get("status") or "PENDING").upper(),
                "linkUrl": result.get("linkUrl") or result.get("shortUrl") or None,
                "amountFixed": is_truthy(fixed) if fixed is not None else amount is not None,
                "payToken": result.get("payToken") or None,
            }

        return await self.audited("canteen.payment_request.create", caller, student_id,
                                  {"amount": amount}, work())

    # ------------------------------------------------- kantin veli kontrolleri

    def controls_body(self, student: dict[str, Any]) -> dict[str, Any]:
        restricted = [value for value in (int_or_none(item) for item in
                                          student.get("restrictedProductIds") or [])
                      if value is not None]
        return {
            "spendingLimit": int_or_none(student.get("spendingLimit")),
            "dailyLimit": int_or_none(student.get("dailyLimit")),
            "isBlocked": bool(student.get("isBlocked")),
            "guardianBlocked": bool(student.get("guardianBlocked")),
            "restrictedProductIds": restricted,
            "spentToday": int_or_none(student.get("spentToday")) or 0,
            "balance": int_or_none(student.get("balance")) or 0,
            "controlsUpdatedAt": self.iso(student.get("controlsUpdatedAt")),
        }

    async def controls(self, student_id: str) -> dict[str, Any]:
        return self.controls_body(await self.call(self.caps.canteen_student(student_id)))

    async def update_controls(self, student_id: str, patch: dict[str, Any],
                              caller: Caller) -> dict[str, Any]:
        if not patch:
            raise validation("Değiştirilecek alan yok.")

        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.update_controls(student_id, patch))
            self.caps.directory_changed()   # dizindeki kantin alanları değişti
            await self.caps.emit("canteen.controls_changed", ("student", student_id),
                                 fields=sorted(patch), causation_id=caller.causation_id)
            if not result:
                result = await self.call(self.caps.canteen_student(student_id))
            return self.controls_body(result)

        return await self.audited("canteen.controls.update", caller, student_id,
                                  {"changes": patch}, work())

    async def record_collection(self, student_id: str, amount: int, local_id: str,
                                note: str | None, caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.record_collection(student_id, amount, local_id),
                                     reasons=COLLECTION_REASONS)
            self.caps.directory_changed()   # bakiye değişti
            balance = int_or_none(result.get("balance"))
            if balance is None:
                student = await self.call(self.caps.canteen_student(student_id))
                balance = int_or_none(student.get("balance")) or 0
            return {"amount": int_or_none(result.get("amount")) or amount, "balance": balance}

        detail = {"amount": amount, "localId": local_id, "note": (note or "")[:200]}
        return await self.audited("canteen.collection.create", caller, student_id, detail,
                                  work())

    # ------------------------------------------------------- kurum geneli akış

    async def payment_requests_feed(self, since_text: str | None, limit: int) -> dict[str, Any]:
        since = self.parse_since(since_text)
        page = await self.call(self.caps.payment_requests_feed(since, limit))
        rows: list[dict[str, Any]] = page["items"]
        items = [{
            "ref": str(row.get("ref") or ""),
            "studentId": str(row.get("studentId") or ""),
            "amount": int_or_none(row.get("amount")) or 0,
            "status": str(row.get("status") or "").upper(),
            "amountFixed": is_truthy(row.get("amountFixed")),
            "linkUrl": row.get("linkUrl") or row.get("shortUrl") or None,
            "payToken": row.get("payToken") or None,
            "origin": row.get("origin") or None,
            "createdAt": self.iso(row.get("createdAt")),
            "updatedAt": self.iso(row.get("updatedAt")),
            "paidAt": self.iso(row.get("paidAt")),
        } for row in rows]
        return self.feed(items, page, rows, since)

    async def parent_notifications_feed(self, since_text: str | None,
                                        limit: int) -> dict[str, Any]:
        since = self.parse_since(since_text)
        page = await self.call(self.caps.parent_notifications_feed(since, limit))
        rows: list[dict[str, Any]] = page["items"]
        # SMS METNİ VE TELEFON DÖNMEZ: yalnız bu beş alan.
        items = [{
            "id": row.get("id"),
            "studentId": str(row.get("studentId") or "") or None,
            "kind": str(row.get("kind") or "") or None,
            "amount": int_or_none(row.get("amount")),
            "createdAt": self.iso(row.get("createdAt")),
        } for row in rows]
        return self.feed(items, page, rows, since)

    def feed(self, items: list[dict[str, Any]], page: dict[str, Any],
             rows: list[dict[str, Any]], since: str | None) -> dict[str, Any]:
        """`{items, nextSince, hasMore}`. Kantin imleç verirse o (ISO'ya çevrilmiş)
        kullanılır, vermezse satırlardan hesaplanır. Sınır DAHİLDİR: aynı satır
        bir sonraki sayfada yeniden gelebilir, istemci ref/id ile ayıklar."""
        cursor = page.get("nextSince")
        next_since = self.iso(cursor, precise=True) if cursor not in (None, "") \
            else self.next_since(rows, since)
        more = page.get("hasMore")
        return {"items": items, "nextSince": next_since,
                "hasMore": is_truthy(more) if more is not None else False}

    def next_since(self, rows: list[dict[str, Any]], since: str | None) -> str | None:
        """Bir sonraki yoklamanın imleci: en yeni `updatedAt` (yoksa `createdAt`)."""
        best: datetime | None = None
        for row in rows:
            moment = to_datetime(row.get("updatedAt") or row.get("createdAt"))
            if moment is not None and (best is None or moment > best):
                best = moment
        return self.iso(best, precise=True) if best is not None else since

    async def canteen_settings_public(self) -> dict[str, Any]:
        """Ön ödeme ayarı — `canteen_settings_cache_seconds` (60 sn) önbellekte.

        Seyrek değişir; her istekte kantine gitmek gereksizdi.
        """
        now = self.now_ms()
        cached = self._canteen_settings
        if cached is not None and now - cached[0] < self.canteen_settings_cache_ms:
            return dict(cached[1])
        settings = await self.call(self.caps.canteen_settings_public())
        body = {"allowPrepayment": is_truthy(settings.get("allowPrepayment")),
                "maxPrepayment": int_or_none(settings.get("maxPrepayment")) or 0}
        self._canteen_settings = (now, body)
        return dict(body)

    async def canteen_totals(self) -> dict[str, Any]:
        """`GET /canteen/totals` — kurum geneli kantin sayıları (Akademi yönetici paneli).

        Kantinin yönetim panosu (`GET /api/reports/dashboard`) üstündedir: açık
        alacak = pozitif bakiyelerin toplamı (kantin öğrenci başına kontrol
        noktasından tek sorguyla hesaplar, defter taranmaz), bugünkü satış
        (iptaller hariç, kantin sunucu günü), bekleyen ödeme talebi, engelli ve
        toplam öğrenci. Yalnız kantin parasıdır; kurum carisiyle toplanmaz.
        `canteen_totals_cache_seconds` (30 sn) önbellekte.
        """
        now = self.now_ms()
        cached = self._canteen_totals
        if cached is not None and now - cached[0] < self.canteen_totals_cache_ms:
            return dict(cached[1])
        stats = await self.call(self.caps.canteen_dashboard())
        body = {
            "totalDebt": max(0, int_or_none(stats.get("openReceivables")) or 0),
            "spentToday": max(0, int_or_none(stats.get("todaySales")) or 0),
            "pendingPaymentRequests": max(0, int_or_none(stats.get("pendingRequests")) or 0),
            "blockedStudents": max(0, int_or_none(stats.get("blockedStudents")) or 0),
            "totalStudents": max(0, int_or_none(stats.get("totalStudents")) or 0),
            "generatedAt": self.iso(self.now()),
        }
        self._canteen_totals = (now, body)
        return dict(body)

    async def products(self) -> dict[str, Any]:
        """Kantin ürünleri — `products_cache_seconds` (5 dk) önbellekte."""
        now = self.now_ms()
        if self._products is not None and now - self._products[0] < self.products_cache_ms:
            return self._products[1]
        rows = await self.call(self.caps.products())
        items = []
        for row in rows:
            active = row.get("isActive", row.get("active", True))
            items.append({
                "id": int_or_none(row.get("id")),
                "name": str(row.get("name") or ""),
                "price": int_or_none(row.get("price")) or 0,
                "active": is_truthy(active),
                "imageUrl": row.get("imageUrl") or None,
            })
        body = {"items": items}
        self._products = (now, body)
        return body

    # ============================================================== §4 tablet

    def tablet_failure(self, *, session: bool = False) -> GatewayError:
        if session:
            return GatewayError(404, "session_not_found", "Tablet oturumu bulunamadı.")
        return student_not_found()

    async def tablets(self) -> dict[str, Any]:
        board = await self.call(self.caps.tablet_board())
        raw_summary = board.get("summary")
        summary: dict[str, Any] = raw_summary if isinstance(raw_summary, dict) else {}
        devices = []
        for device in board.get("devices") or []:
            if not isinstance(device, dict):
                continue
            session = device.get("activeSession")
            devices.append({
                "id": device.get("id"),
                "name": str(device.get("name") or ""),
                "state": str(device.get("state") or ""),
                "battery": int_or_none(device.get("battery")),
                "charging": bool(device.get("charging")),
                "lastSeenAt": self.iso(device.get("lastSeenAt")),
                "activeSession": {
                    "id": session.get("id"),
                    "studentId": session.get("studentId"),
                    "studentName": session.get("studentName"),
                    "loginAt": self.iso(session.get("loginAt")),
                } if isinstance(session, dict) else None,
            })
        return {
            "summary": {key: int_or_none(summary.get(key)) or 0
                        for key in ("free", "inUse", "offline", "locked")},
            "devices": devices,
        }

    async def tablet_usage(self, student_id: str, date_from: date | None,
                           date_to: date | None) -> dict[str, Any]:
        today = self.now().astimezone(self.zone).date()
        end = date_to or today
        start = date_from or date.fromordinal(end.toordinal() - 6)
        if start > end:
            raise validation("`from` tarihi `to` tarihinden sonra olamaz.", "from", "to")
        if (end - start).days + 1 > 62:
            raise validation("Tarih aralığı en çok 62 gün olabilir.", "from", "to")
        usage = await self.call(
            self.caps.tablet_usage(student_id, start.isoformat(), end.isoformat()),
            not_found=self.tablet_failure(),
        )

        def short_session(item: Any) -> dict[str, Any] | None:
            if not isinstance(item, dict):
                return None
            return {"id": item.get("id"), "deviceName": item.get("deviceName"),
                    "loginAt": self.iso(item.get("loginAt"))}

        return {
            "days": [{
                "date": str(day.get("date") or ""),
                "totalSeconds": int_or_none(day.get("totalSeconds")) or 0,
                "apps": [{"package": str(app.get("package") or app.get("packageName") or ""),
                          "label": app.get("label") or None,
                          "seconds": int_or_none(app.get("seconds")) or 0}
                         for app in day.get("apps") or [] if isinstance(app, dict)],
            } for day in usage.get("days") or [] if isinstance(day, dict)],
            "sessions": [{
                "id": item.get("id"), "deviceName": item.get("deviceName"),
                "loginAt": self.iso(item.get("loginAt")),
                "endedAt": self.iso(item.get("endedAt")),
                "endReason": item.get("endReason") or None,
            } for item in usage.get("sessions") or [] if isinstance(item, dict)],
            "activeSession": short_session(usage.get("activeSession")),
            "blockedUntil": self.iso(usage.get("blockedUntil")),
        }

    async def tablets_daily_usage(self, day: date | None) -> dict[str, Any]:
        """§4 `GET /tablets/usage?date=` — kurum geneli, o günün öğrenci başına süresi.

        Akademi `TabletProvider.daily_usage` (yönetici listesi, gece 23:55 özeti)
        okur. Satır o gün kullanımı olan ya da kısıtı şu an süren öğrencidir;
        `blockedUntil` çağrı anının kısıtıdır. Tek yanıt (≈ 2.000 öğrencide
        < 120 KB), imleç yok. Gün verilmezse bugün; gelecek gün ya da 62 günden
        eskisi 422.
        """
        today = self.now().astimezone(self.zone).date()
        day = day or today
        if day > today:
            raise validation("Gelecek bir gün sorulamaz.", "date")
        if (today - day).days >= TABLET_USAGE_MAX_DAYS:
            raise validation(f"En çok {TABLET_USAGE_MAX_DAYS} gün geriye bakılabilir.", "date")
        usage = await self.call(self.caps.tablet_daily_usage(day.isoformat()))
        items = []
        for item in usage.get("items") or []:
            if not isinstance(item, dict) or not item.get("studentId"):
                continue
            items.append({"studentId": str(item["studentId"]),
                          "totalSeconds": max(0, int_or_none(item.get("totalSeconds")) or 0),
                          "blockedUntil": self.iso(item.get("blockedUntil"))})
        return {"date": day.isoformat(), "items": items}

    async def tablet_grant(self, student_id: str, *, package_name: str | None,
                           extra_seconds: int, reason: str, caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.tablet_grant(
                student_id, package_name=package_name, extra_seconds=extra_seconds,
                reason=reason, actor=caller.actor,
            ), not_found=self.tablet_failure())
            grant_id = result.get("grantId", result.get("id")) if isinstance(result, dict) \
                else result
            await self.caps.emit("tablet.policy_changed", ("student", student_id),
                                 fields=["grants"], causation_id=caller.causation_id)
            return {"grantId": grant_id}

        detail = {"packageName": package_name, "extraSeconds": extra_seconds, "reason": reason}
        return await self.audited("tablet.grant", caller, student_id, detail, work())

    async def tablet_block(self, student_id: str, *, until: datetime | None, reason: str,
                           caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.tablet_block(
                student_id, until=until, reason=reason, actor=caller.actor,
            ), not_found=self.tablet_failure())
            value = result.get("blockedUntil") if isinstance(result, dict) else result
            await self.caps.emit("tablet.policy_changed", ("student", student_id),
                                 fields=["blockedUntil"], causation_id=caller.causation_id)
            return {"blockedUntil": self.iso(value)}

        detail = {"until": until.isoformat() if until else None, "reason": reason}
        return await self.audited("tablet.block", caller, student_id, detail, work())

    async def tablet_unblock(self, student_id: str, *, reason: str,
                             caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            await self.call(self.caps.tablet_unblock(student_id, reason=reason,
                                                     actor=caller.actor),
                            not_found=self.tablet_failure())
            await self.caps.emit("tablet.policy_changed", ("student", student_id),
                                 fields=["blockedUntil"], causation_id=caller.causation_id)
            return {"blockedUntil": None}

        return await self.audited("tablet.unblock", caller, student_id, {"reason": reason},
                                  work())

    async def tablet_end_session(self, session_id: str, *, reason: str,
                                 caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            owner = await self.caps.tablet_session_student(session_id)
            ended = await self.call(self.caps.tablet_end_session(
                session_id, reason=reason, actor=caller.actor,
            ), not_found=self.tablet_failure(session=True))
            if isinstance(ended, dict):
                ended = ended.get("ended", True)
            if ended:
                await self.caps.emit("tablet.policy_changed", ("student", owner),
                                     fields=["sessions"], causation_id=caller.causation_id)
            return {"ended": bool(ended)}

        return await self.audited("tablet.session.end", caller, "",
                                  {"sessionId": session_id, "reason": reason}, work())

    # ============================================================== §5 anons

    async def call_student(self, student_id: str, template: str, text: str | None,
                           caller: Caller) -> dict[str, Any]:
        if template == "custom":
            if not text or not text.strip():
                raise validation("Özel anons için metin zorunlu.", "text")
            raw = text.strip()
        else:
            raw = self.page_template
        unknown = sorted({name for name in _PLACEHOLDER.findall(raw)
                          if name not in CALL_PLACEHOLDERS})
        if unknown:
            raise validation("Tanınmayan yer tutucu: " +
                             ", ".join(f"{{{name}}}" for name in unknown), "text")
        if not self.caps.has(BELL_ANNOUNCE):
            raise capability_missing(BELL_ANNOUNCE)

        await self.tick()
        record = await self.call(self.caps.directory_get(student_id))
        if record is None:
            raise student_not_found()

        now = self.now_ms()
        window = await self.repo.window("call_student", student_id, now - self.call_cooldown_ms)
        if window.count and window.oldest is not None:
            raise rate_limited(
                math.ceil((window.oldest + self.call_cooldown_ms - now) / 1000),
                "Bu öğrenci az önce çağrıldı. Biraz sonra yeniden deneyin.",
            )
        # Bekleme ÖNCE yazılır: aynı anda gelen iki çağrıdan yalnız biri anons olur.
        await self.repo.hit("call_student", student_id, now)

        spoken = (raw.replace("{ad}", str(record.get("firstName") or "").strip())
                  .replace("{soyad}", str(record.get("lastName") or "").strip())
                  .replace("{sinif}", str(record.get("className") or "").strip()))
        spoken = " ".join(spoken.split())
        cache_key = f"mobile_gateway:call:page:{student_id}" if template == "page" else None

        async def work() -> dict[str, Any]:
            try:
                result = await self.call(self.caps.bell_page(
                    spoken, cache_key=cache_key, actor=caller.actor,
                    request_id=caller.request_id or None,
                ))
            except GatewayError:
                await self.repo.clear("call_student", student_id)
                raise
            status = str(result.get("status") or "")
            if status == "queued":
                return {"status": "queued", "voiceCached": bool(result.get("voiceCached")),
                        "etaSeconds": int_or_none(result.get("etaSeconds")) or 0}
            await self.repo.clear("call_student", student_id)
            if status == "disabled":
                raise GatewayError(409, "bell_disabled", "Zil sistemi kapalı; anons yapılamıyor.")
            if status == "tts_unavailable":
                raise GatewayError(503, "tts_unavailable", "Anons sesi şu anda üretilemiyor.")
            raise upstream_error("Zil sistemi beklenmedik bir yanıt verdi.")

        return await self.audited("announcement.call", caller, student_id,
                                  {"template": template}, work())

    # ======================================================== §6 zil saatleri

    def forget_bell_week(self, *_: Any) -> None:
        """Zil haftası değişti (`bell.week_changed` olayı): önbellek bırakılır."""
        self._bell_week = None

    async def bell_week_reply(self, if_none_match: str | None) -> Reply:
        """§6 zil haftası — sunucu önbelleği + ETag (`If-None-Match` → 304).

        Haftalık plan seyrek değişir. Yanıt `bell_week_cache_seconds` (60 sn)
        bellekte durur; Zil Sistemi planı değiştirdiğinde yayınladığı
        `bell.week_changed` olayı önbelleği HEMEN bırakır (olay yayınlamayan eski
        zil sürümünde süre sınırlar). Kaynak yoksa ya da okunamazsa yanıt
        önbelleğe girmez: zil geri geldiğinde ilk istek görür.
        """
        now = self.now_ms()
        cached = self._bell_week
        if cached is None or now - cached.at_ms >= self.bell_week_cache_ms \
                or not self.caps.has(BELL_WEEK):
            body = await self.bell_week()
            raw = encode(body)
            cached = _Cached(key=None, etag=hashlib.sha256(raw).hexdigest(), raw=raw, at_ms=now)
            self._bell_week = cached if body["available"] else None
        max_age = max(0, (self.bell_week_cache_ms - (now - cached.at_ms)) // 1000)
        headers = {"ETag": f'"{cached.etag}"', "Cache-Control": f"private, max-age={max_age}"}
        if etag_matches(if_none_match, cached.etag):
            return Reply(304, None, headers)
        return Reply(200, None, headers, raw=cached.raw)

    async def bell_week(self) -> dict[str, Any]:
        """`bbd_class_schedule` `/week` ile aynı biçim. Kaynak yoksa nedeniyle döner."""
        empty: dict[str, Any] = {"times": {day: [] for day in DAYS}, "profiles": [],
                                 "groups": [], "available": False, "reason": None}
        if not self.caps.has(BELL_WEEK):
            empty["reason"] = "Zil Sistemi Kontrol Merkezi'nde etkin değil."
            return empty
        try:
            payload = await self.caps.bell_week()
        except (CapabilityMissing, UpstreamFailure):
            self._log.warning("zil saatleri okunamadı")
            empty["reason"] = "Zil saatleri şu anda okunamıyor."
            return empty
        raw_times, raw_groups = payload.get("times"), payload.get("groups")
        times: dict[str, Any] = raw_times if isinstance(raw_times, dict) else {}
        groups: list[Any] = raw_groups if isinstance(raw_groups, list) else []
        raw_profiles = payload.get("profiles")
        # Saat setleri (§3.1, ekleyici). Eski zil sürümü vermezse boş liste.
        profiles: list[Any] = raw_profiles if isinstance(raw_profiles, list) else []
        return {
            "times": {day: [{"id": entry.get("id"), "time": entry.get("time"),
                             "label": entry.get("label"), "kind": entry.get("kind")}
                            for entry in times.get(day) or [] if isinstance(entry, dict)]
                      for day in DAYS},
            "profiles": [{"id": profile.get("id"), "name": profile.get("name"),
                          "times": [{"time": entry.get("time"), "label": entry.get("label"),
                                     "kind": entry.get("kind")}
                                    for entry in profile.get("times") or []
                                    if isinstance(entry, dict)]}
                         for profile in profiles
                         if isinstance(profile, dict) and profile.get("id")],
            "groups": [{"id": group.get("id"), "name": group.get("name"),
                        "kind": group.get("kind"), "sort": group.get("sort")}
                       for group in groups if isinstance(group, dict)],
            "available": True,
            "reason": None,
        }

    # ================================================= §9 öğrenci grupları

    def forget_groups(self, *_: Any) -> None:
        """Gruplar değişti (`bbd_students.groups_changed` olayı): önbellek bırakılır."""
        self._groups = None

    @staticmethod
    def parse_revision(text: str | None) -> int | None:
        """`since` grup revizyonudur: negatif olmayan tamsayı (yoksa tam liste)."""
        if text is None or not text.strip():
            return None
        raw = text.strip()
        if not raw.isdigit() or len(raw) > 18:
            raise validation("`since` negatif olmayan bir tamsayı (grup revizyonu) olmalı.",
                             "since")
        return int(raw)

    @staticmethod
    def group(record: dict[str, Any]) -> dict[str, Any]:
        """Grup kaydı → sözleşme §2.4 nesnesi (beyaz liste).

        Öğrenci telefonu ya da kişisel alan TAŞIMAZ — üyelikte yalnız kimlik ve
        tarih aralığı; grubun notu, oluşturanı, üyelik gerekçesi düşer."""
        members = [{"studentId": str(item.get("studentId")),
                    "validFrom": item.get("validFrom"),
                    "validTo": item.get("validTo") or None}
                   for item in record.get("members") or []
                   if isinstance(item, dict) and item.get("studentId")]
        members.sort(key=lambda item: (item["studentId"], str(item["validFrom"] or "")))
        return {
            "id": str(record.get("id")),
            "name": str(record.get("name") or ""),
            "callName": str(record.get("callName") or ""),
            "kind": "individual" if record.get("kind") == "individual" else "group",
            "archived": bool(record.get("archived")),
            "archivedAt": record.get("archivedAt") or None,
            "updatedAt": record.get("updatedAt") or None,
            "revision": int_or_none(record.get("revision")) or 0,
            "members": members,
        }

    def _groups_cached(self, listing: dict[str, Any], *, full: bool) -> _Cached:
        """Yetenek yanıtından kodlanmış gövde + ETag (`generatedAt` özete girmez)."""
        groups = sorted((self.group(record) for record in listing.get("groups") or []
                         if isinstance(record, dict) and record.get("id")),
                        key=lambda item: (item["revision"], item["id"]))
        revision = int_or_none(listing.get("revision")) or 0
        core = {"revision": revision, "full": full,
                "complete": listing.get("complete") is True, "groups": groups}
        etag = digest(core)
        return _Cached(key=revision, etag=etag, at_ms=self.now_ms(),
                       raw=encode({"generatedAt": self.iso(self.now()), **core, "etag": etag}))

    async def groups(self, since_text: str | None, if_none_match: str | None) -> Reply:
        """§2.4 grup senkronu — `since` + ETag; tam liste sunucu önbelleğinde.

        Tam liste (≈200 grup, ≈4.000 üyelik) yalnız grup revizyonu değişince
        yeniden kurulur: her istekte yeteneğin `revision()`ı (tek satır) okunur;
        aynıysa kodlanmış gövde aynen gider. `revision()` sunmayan yetenekte
        `groups_cache_seconds` süresi sınırlar. Ayrıca
        `bbd_students.groups_changed` olayı önbelleği hemen bırakır.

        `since` verilirse `revision > since` olan gruplar TAM üye listesiyle
        döner (önbelleğe girmez — istemciye özgüdür). `since` KM'nin
        revizyonundan büyükse (KM yedekten döndü) tam liste döner: istemci
        `full: true` görünce aynasını baştan kurar.
        """
        since = self.parse_revision(since_text)
        if not self.caps.has(GROUPS):
            raise capability_missing(GROUPS)
        cached: _Cached | None = None
        if since is None:
            current = await self.call(self.caps.groups_revision())
            held = self._groups
            if held is not None and (
                held.key == current if current is not None
                else self.now_ms() - held.at_ms < self.groups_cache_ms
            ):
                cached = held
        if cached is None:
            listing = await self.call(self.caps.groups_list(since))
            full = since is None
            if since is not None and (int_or_none(listing.get("revision")) or 0) < since:
                listing = await self.call(self.caps.groups_list(None))
                full = True
            cached = self._groups_cached(listing, full=full)
            if full and listing.get("complete") is True:
                self._groups = cached
        headers = {"ETag": f'"{cached.etag}"', "Cache-Control": "private, no-cache"}
        if etag_matches(if_none_match, cached.etag):
            return Reply(304, None, headers)
        return Reply(200, None, headers, raw=cached.raw)

    # ===================================================== §10 zil planı

    def plan_day_result(self, item: dict[str, Any]) -> dict[str, Any]:
        result = str(item.get("result") or "")
        out: dict[str, Any] = {
            "date": item.get("date"),
            "result": result if result in BELL_PLAN_RESULTS else "rejected",
            "revision": int_or_none(item.get("revision")),
        }
        error = item.get("error")
        if isinstance(error, str) and error:
            out["error"] = error if error in BELL_PLAN_ERRORS else "rejected"
            details = item.get("details")
            if isinstance(details, dict) and details.get("profileId"):
                out["details"] = {"profileId": str(details["profileId"])}
        return out

    async def put_bell_plan(self, body: dict[str, Any], caller: Caller) -> dict[str, Any]:
        """§3.3 `PUT /bell/plan` — Akademi'nin 14 günlük planı.

        Gövdenin kuralları (en çok 15 gün, aralıktaki HER gün, alan sınırları)
        zil yeteneğinde tek yerdedir; red `422 validation_error` + `details.path`.
        Günler BAĞIMSIZ uygulanır: `unknown_profile` ve `revision_conflict` o
        günü `rejected` yapar (`error` gün satırında), diğer günler uygulanır —
        yanıt yine 200'dür. Ses üretimi isteğin dışında kuyruktadır.
        """
        if not self.caps.has(BELL_PLAN):
            raise capability_missing(BELL_PLAN)
        days = body.get("days")
        detail = {"from": str(body.get("from") or "")[:10], "to": str(body.get("to") or "")[:10],
                  "days": len(days) if isinstance(days, list) else 0}

        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.bell_plan_apply(body, actor=caller.actor))
            out = [self.plan_day_result(item) for item in result.get("days") or []
                   if isinstance(item, dict)]
            counts: dict[str, int] = {}
            for item in out:
                counts[item["result"]] = counts.get(item["result"], 0) + 1
            detail["results"] = counts
            conflicts = [item["date"] for item in out if item.get("error")]
            if conflicts:
                self._log.warning("zil planı günleri reddedildi", dates=conflicts[:15],
                                  request_id=caller.request_id or None)
            return {"days": out,
                    "voicesPending": int_or_none(result.get("voicesPending")) or 0,
                    "bellEnabled": bool(result.get("bellEnabled"))}

        return await self.audited("bell.plan.put", caller, "", detail, work())

    def status_window(self, date_from: date | None, date_to: date | None) -> tuple[date, date]:
        """`from` yoksa bugün, `to` yoksa `from` + 13 gün; aralık en çok 31 gün."""
        start = date_from or self.now().astimezone(self.zone).date()
        end = date_to or start + timedelta(days=BELL_PLAN_WINDOW_DAYS - 1)
        if end < start:
            raise validation("`to`, `from`'dan önce olamaz.", "to")
        if (end - start).days + 1 > BELL_STATUS_MAX_DAYS:
            raise validation(f"Aralık en çok {BELL_STATUS_MAX_DAYS} gün olabilir.", "to")
        return start, end

    def status_day(self, item: dict[str, Any]) -> dict[str, Any]:
        out = {key: item.get(key) for key in BELL_STATUS_DAY_KEYS}
        for key in ("calls", "voicesReady", "voicesPending"):
            out[key] = int_or_none(out[key]) or 0
        override = item.get("override")
        out["override"] = ({"mode": override.get("mode"), "by": override.get("by") or None,
                            "at": override.get("at") or None}
                           if isinstance(override, dict) else None)
        return out

    async def bell_plan_status(self, date_from: date | None, date_to: date | None,
                               if_none_match: str | None) -> Reply:
        """§3.3 `GET /bell/plan/status` — gün başına kip, kaynak, revizyon, ses durumu.

        Zil modülünün yerel tablolarından okunur (önbellek yok, ≤ 80 ms hedefi);
        ETag gövdenin özetidir, Akademi'nin 15 dk'lık uzlaştırması çoğunlukla 304 alır.
        """
        start, end = self.status_window(date_from, date_to)
        if not self.caps.has(BELL_PLAN):
            raise capability_missing(BELL_PLAN)
        result = await self.call(self.caps.bell_plan_status(start.isoformat(), end.isoformat()))
        raw_agent = result.get("agent")
        agent: dict[str, Any] = raw_agent if isinstance(raw_agent, dict) else {}
        body = {
            "bellEnabled": bool(result.get("bellEnabled")),
            "sessionCallsEnabled": bool(result.get("sessionCallsEnabled")),
            "agent": {"online": bool(agent.get("online")),
                      "lastSeenAt": self.iso(agent.get("lastSeenAt"))},
            "days": [self.status_day(item) for item in result.get("days") or []
                     if isinstance(item, dict)],
        }
        raw = encode(body)
        etag = hashlib.sha256(raw).hexdigest()
        headers = {"ETag": f'"{etag}"', "Cache-Control": "private, no-cache"}
        if etag_matches(if_none_match, etag):
            return Reply(304, None, headers)
        return Reply(200, None, headers, raw=raw)

    # ========================================================== §7 OTP SMS

    async def send_otp(self, phone: str, code: str, purpose: str,
                       caller: Caller) -> dict[str, Any]:
        """Metin modül ayarındaki şablondan üretilir; istemci metin yazamaz.

        sms-kale: kantine `purpose="otp"` (Netgsm OTP hattı) gider; yanıt
        `{sent, jobId?, channel?}` — jobId ile `GET /sms/otp/{jobId}` teslim durumu.
        Sınırlar: telefon başına saatte `sms_per_phone_per_hour`, günde
        `sms_per_phone_per_day`; hepsinde toplam dakikada `sms_global_per_minute`.
        """
        normalized = normalize_phone(phone)
        if normalized is None:
            raise validation("Telefon 5XXXXXXXXX biçiminde olmalı.", "phone")
        await self.tick()
        now = self.now_ms()
        masked = mask_phone(normalized)
        subject = phone_subject(normalized)

        async def log(status: str, reason: str = "", job_id: str = "",
                      channel: str = "") -> None:
            await self.repo.log_sms(at=self.now_ms(), phone_masked=masked, purpose=purpose,
                                    status=status, reason=reason, user_id=caller.user_id,
                                    on_behalf_of=caller.on_behalf_of,
                                    request_id=caller.request_id, job_id=job_id,
                                    channel=channel)

        overall = await self.repo.window("sms_global", "*", now - MINUTE_MS)
        if overall.count >= self.sms_global_per_minute and overall.oldest is not None:
            await log("rate_limited", "global")
            raise rate_limited(math.ceil((overall.oldest + MINUTE_MS - now) / 1000))
        mine = await self.repo.window("sms_phone", subject, now - HOUR_MS)
        if mine.count >= self.sms_per_phone_per_hour and mine.oldest is not None:
            await log("rate_limited", "phone")
            raise rate_limited(math.ceil((mine.oldest + HOUR_MS - now) / 1000),
                               "Bu numaraya çok fazla kod gönderildi. Daha sonra deneyin.")
        daily = await self.repo.window("sms_phone_day", subject, now - DAY_MS)
        if daily.count >= self.sms_per_phone_per_day and daily.oldest is not None:
            await log("rate_limited", "phone_day")
            raise rate_limited(math.ceil((daily.oldest + DAY_MS - now) / 1000),
                               "Bu numaraya bugün çok fazla kod gönderildi. Daha sonra deneyin.")
        await self.repo.hit("sms_global", "*", now)
        await self.repo.hit("sms_phone", subject, now)
        await self.repo.hit("sms_phone_day", subject, now)

        text = self.otp_template.replace("{code}", code)
        if self.otp_app_hashes:
            text = f"{text} {' '.join(self.otp_app_hashes)}"
        try:
            result = await self.caps.send_sms(normalized, text, purpose="otp")
        except CapabilityMissing as missing:
            await log("failed", "capability_missing")
            raise capability_missing(missing.capability) from None
        except UpstreamFailure as failure:
            await log("failed", failure.reason or failure.kind)
            if failure.kind == "unavailable":
                raise canteen_unavailable() from None
            raise GatewayError(502, "sms_failed", "SMS gönderilemedi.") from None
        if not result.get("sent"):
            await log("failed", str(result.get("reason") or "unknown")[:60],
                      channel=str(result.get("channel") or ""))
            raise GatewayError(502, "sms_failed", "SMS gönderilemedi.")
        job_id = str(result.get("jobId") or "")
        channel = str(result.get("channel") or "")
        await log("sent", job_id=job_id, channel=channel)
        reply: dict[str, Any] = {"sent": True}
        if job_id:
            reply["jobId"] = job_id
        if channel:
            reply["channel"] = channel
        return reply

    async def otp_status(self, job_id: str) -> dict[str, Any]:
        """Bu geçitten gönderilmiş bir OTP'nin teslim durumu (sms-kale).

        Yalnız `sms_log`'da kaydı olan jobId sorulabilir (servis anahtarı keyfî
        Netgsm görevi sorgulayamaz). Kesin sonuç (delivered/failed) saklanır;
        bekleyen sonuç kantine en çok `SMS_REPORT_EVERY_MS`de bir sorulur.
        """
        if not _JOB_ID_RE.match(job_id):
            raise validation("Görev kimliği geçersiz.", "jobId")
        row = await self.repo.sms_by_job(job_id)
        if row is None:
            raise GatewayError(404, "sms_not_found", "Bu görev kimliğiyle gönderilmiş kod yok.")
        now = self.now_ms()
        delivery = str(row.get("delivery") or "") or "pending"
        checked_at = int(row.get("checked_at") or 0)
        delivered_at = row.get("delivered_at")
        if delivery not in ("delivered", "failed") and now - checked_at >= SMS_REPORT_EVERY_MS:
            try:
                report = await self.caps.sms_report(job_id)
            except (CapabilityMissing, UpstreamFailure):
                report = {}
            status = report.get("status")
            if status in ("pending", "delivered", "failed"):
                delivery = str(status)
            moment = to_datetime(report.get("deliveredAt")) if delivery == "delivered" else None
            delivered_at = int(moment.timestamp() * 1000) if moment is not None else delivered_at
            checked_at = now
            error_code = report.get("errorCode")
            await self.repo.sms_delivery(
                int(row["id"]), delivery=delivery, delivered_at=delivered_at,
                checked_at=checked_at,
                reason=(f"netgsm_{report.get('netgsmStatus')}:{error_code}"
                        if delivery == "failed" else None),
            )
        created_at = int(row.get("created_at") or 0)
        return {
            "jobId": job_id,
            "status": delivery,
            "channel": str(row.get("channel") or "") or None,
            "sentAt": self.iso(created_at),
            "deliveredAt": self.iso(delivered_at) if delivered_at else None,
            "deliverySeconds": (round((int(delivered_at) - created_at) / 1000, 1)
                                if delivered_at else None),
            "checkedAt": self.iso(checked_at) if checked_at else None,
        }

    async def reset_sms_limits(self, phone: str, reason: str,
                               caller: Caller) -> dict[str, Any]:
        """Bir telefonun OTP sayaçlarını (saatlik + günlük) sıfırlar; denetim izine yazar.
        Akademi yönetici ucu (`POST /v1/admin/sms/limits/reset`) bunu çağırır."""
        normalized = normalize_phone(phone)
        if normalized is None:
            raise validation("Telefon 5XXXXXXXXX biçiminde olmalı.", "phone")
        subject = phone_subject(normalized)
        now = self.now_ms()
        hour = await self.repo.window("sms_phone", subject, now - HOUR_MS)
        day = await self.repo.window("sms_phone_day", subject, now - DAY_MS)
        await self.repo.clear("sms_phone", subject)
        await self.repo.clear("sms_phone_day", subject)
        await self.audit("sms_limits_reset", caller, detail={
            "phoneMasked": mask_phone(normalized), "reason": reason[:200],
            "clearedHour": hour.count, "clearedDay": day.count,
        })
        return {"reset": True, "clearedHour": hour.count, "clearedDay": day.count}

    async def sms_health(self) -> dict[str, Any]:
        """Son 1 saatin OTP özeti + Netgsm paket/kredi (izleme/alarm için, kişisel veri yok)."""
        now = self.now_ms()
        stats = await self.repo.sms_stats(now - HOUR_MS)
        balance: dict[str, Any] = {}
        try:
            balance = await self.caps.sms_balance()
        except (CapabilityMissing, UpstreamFailure):
            balance = {}
        sms_count = balance.get("smsCount")
        return {
            "windowSeconds": 3600,
            **stats,
            "balance": {
                "ok": bool(balance.get("ok")),
                "smsCount": sms_count if isinstance(sms_count, int) else None,
            },
        }

    # ========================================================= §8 genel ayarlar

    def public_settings(self) -> dict[str, Any]:
        return {
            "institutionName": self.institution_name,
            "timezone": self.timezone,
            "features": {
                "canteen": self.caps.has(CANTEEN),
                "tablet": self.caps.has(TABLET_READ),
                "bell": self.caps.has(BELL_ANNOUNCE),
            },
        }
