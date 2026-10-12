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
import hashlib
import hmac
import json
import math
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from km_sdk import ModuleStore

from .caps import (
    BELL_ANNOUNCE,
    BELL_WEEK,
    CANTEEN,
    TABLET_READ,
    Capabilities,
    CapabilityMissing,
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

DEFAULT_OTP_TEMPLATE = "BBD Akademi giriş kodunuz: {code}. Kodu kimseyle paylaşmayın."
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

    @property
    def actor(self) -> str:
        """Tablet/zil denetim izine giden ad: `KM kullanıcısı (asıl kişi)`."""
        return f"{self.name} ({self.on_behalf_of})" if self.on_behalf_of else self.name


@dataclass(slots=True)
class Reply:
    status: int
    body: dict[str, Any] | None
    headers: dict[str, str] = field(default_factory=dict)


def clean_header(value: str | None, limit: int = 200) -> str:
    """Denetim izine giden başlık: kontrol karakteri ayıklanır, kısaltılır."""
    return _CONTROL.sub("", str(value or "")).strip()[:limit]


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

        self.otp_template = str(config.get("otp_template") or DEFAULT_OTP_TEMPLATE)
        if "{code}" not in self.otp_template:
            raise ValueError("mobile_gateway.otp_template `{code}` yer tutucusunu içermeli.")
        templates = config.get("call_templates") or {}
        self.page_template = str(templates.get("page") or DEFAULT_PAGE_TEMPLATE)

        limits = config.get("limits") or {}
        self.auth_max_failures = int(limits.get("auth_max_failures", 5))
        self.auth_window_ms = int(limits.get("auth_window_seconds", 900)) * 1000
        self.lookup_per_minute = int(limits.get("lookup_per_minute", 10))
        self.sms_per_phone_per_hour = int(limits.get("sms_per_phone_per_hour", 5))
        self.sms_global_per_minute = int(limits.get("sms_global_per_minute", 60))
        self.call_cooldown_ms = int(limits.get("call_cooldown_seconds", 60)) * 1000

        self.idempotency_ms = int(config.get("idempotency_hours", 24)) * HOUR_MS
        self.products_cache_ms = int(config.get("products_cache_seconds", 300)) * 1000
        self.sms_retention_ms = int(config.get("sms_log_retention_days", 90)) * DAY_MS
        self.audit_retention_ms = int(config.get("audit_retention_days", 365)) * DAY_MS

        self._products: tuple[int, dict[str, Any]] | None = None
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
        except UpstreamFailure as failure:
            raise self.translate(failure, reasons=reasons, not_found=not_found) from None

    def translate(self, failure: UpstreamFailure, *, reasons: dict[str, int] | None = None,
                  not_found: GatewayError | None = None) -> GatewayError:
        if failure.kind == "not_found":
            return not_found or student_not_found()
        if failure.kind == "business" and failure.reason:
            status = (reasons or {}).get(failure.reason)
            if status is not None:
                return GatewayError(status, failure.reason,
                                    REASON_MESSAGES.get(failure.reason, "İstek reddedildi."))
            return upstream_error("Bağlı sistem isteği reddetti.", reason=failure.reason)
        if failure.kind == "invalid":
            return validation("Gönderilen veri bağlı sistem tarafından reddedildi.")
        if failure.kind == "conflict":
            return GatewayError(409, "conflict", "İşlem kaydın şu anki durumuyla çelişiyor.")
        if failure.source == "canteen":
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

    async def directory(self, since_text: str | None, if_none_match: str | None) -> Reply:
        since = self.parse_since(since_text)
        listing = await self.call(self.caps.directory_list(since))
        students = sorted(
            (self.student(record) for record in listing.get("students") or []
             if isinstance(record, dict) and record.get("id")),
            key=lambda item: str(item["id"]),
        )
        classes = sorted({str(name) for name in listing.get("classes") or [] if name})
        core = {
            "full": since is None,
            "complete": listing.get("complete") is True,
            "students": students,
            "classes": classes,
        }
        etag = digest(core)
        headers = {"ETag": f'"{etag}"'}
        if etag_matches(if_none_match, etag):
            return Reply(304, None, headers)
        body = {"generatedAt": self.iso(self.now()), **core, "etag": etag}
        return Reply(200, body, headers)

    def student(self, record: dict[str, Any]) -> dict[str, Any]:
        """Dizin kaydı → sözleşme §1 öğrenci nesnesi (beyaz liste).

        Erişim kodu, QR anahtarı ve bilinmeyen her alan burada DÜŞER.
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
            if slot not in (1, 2):
                continue
            phone = normalize_phone(guardian.get("phone"))
            if phone is None:
                if guardian.get("phone"):
                    note(f"guardian{slot}Phone")
                continue
            name = str(guardian.get("name") or "").strip() or None
            guardians.append({"slot": slot, "name": name, "phone": phone})
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

        first = str(record.get("firstName") or "").strip()
        last = str(record.get("lastName") or "").strip()
        display = str(record.get("displayName") or "").strip() or f"{first} {last}".strip()
        school_no = record.get("schoolNo")
        return {
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
        }

    @staticmethod
    def phones(record: dict[str, Any]) -> set[str]:
        """Öğrencinin kendi ve iki velisinin normalize telefonları."""
        found = {normalize_phone(record.get("studentPhone"))}
        for guardian in record.get("guardians") or []:
            if isinstance(guardian, dict) and int_or_none(guardian.get("slot")) in (1, 2):
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

    async def canteen_summary(self, student_id: str, days: int) -> dict[str, Any]:
        now = self.now_ms()
        start, end = now - days * DAY_MS, now + MINUTE_MS
        student = await self.call(self.caps.canteen_student(student_id))
        transactions = await self.call(self.caps.canteen_transactions(student_id, start, end))
        collections = await self.call(self.caps.canteen_collections(student_id, start, end))
        requests = await self.call(self.caps.payment_requests_for(student_id))

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
            "transactions": sorted((self.transaction(item) for item in transactions),
                                   key=lambda item: str(item["at"] or ""), reverse=True),
            "collections": sorted((self.collection(item) for item in collections),
                                  key=lambda item: str(item["at"] or ""), reverse=True),
            "pendingPaymentRequest": (
                {key: pending[0][key] for key in
                 ("ref", "amount", "linkUrl", "createdAt", "payToken", "amountFixed")}
                if pending else None
            ),
        }

    def transaction(self, item: dict[str, Any]) -> dict[str, Any]:
        lines = []
        for line in item.get("items") or []:
            if not isinstance(line, dict):
                continue
            qty = int_or_none(line.get("qty")) or 0
            unit = int_or_none(line.get("unitPrice")) or 0
            total = int_or_none(line.get("lineTotal"))
            lines.append({
                "name": str(line.get("name") or line.get("productName") or ""),
                "qty": qty, "unitPrice": unit,
                "lineTotal": total if total is not None else qty * unit,
            })
        identifier = item.get("serverId") or item.get("id") or item.get("localId") or ""
        return {
            "id": str(identifier),
            "at": self.iso(item.get("createdAt")),
            "total": int_or_none(item.get("total")) or 0,
            "reversed": bool(item.get("reversedAt") or item.get("reversed")),
            "items": lines,
        }

    def collection(self, item: dict[str, Any]) -> dict[str, Any]:
        method = str(item.get("method") or item.get("source") or item.get("type")
                     or item.get("channel") or "").upper()
        moment = item.get("createdAt") or item.get("at") or item.get("date")
        amount = item.get("amount")
        if amount is None:
            amount = item.get("total") if item.get("total") is not None else item.get("credit")
        return {
            "at": self.iso(moment),
            "amount": int_or_none(amount) or 0,
            "method": "CASH" if ("CASH" in method or "NAKIT" in method) else "PAYMENT",
        }

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
        text = await self.call(self.caps.card_qr_text(student_id))
        if not text:
            raise student_not_found()
        return {"qrText": text, "version": 1}

    async def student_payment_requests(self, student_id: str) -> dict[str, Any]:
        rows = await self.call(self.caps.payment_requests_for(student_id))
        items = [self.payment_request(item) for item in rows]
        items.sort(key=lambda item: str(item["createdAt"] or ""), reverse=True)
        return {"items": items}

    async def create_payment_request(self, student_id: str, amount: int | None,
                                     caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.create_payment_request(student_id, amount),
                                     reasons=PAYMENT_REQUEST_REASONS)
            fixed = result.get("amountFixed")
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
        rows = await self.call(self.caps.payment_requests_feed(since, limit))
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
        return {"items": items, "nextSince": self.next_since(rows, since)}

    async def parent_notifications_feed(self, since_text: str | None,
                                        limit: int) -> dict[str, Any]:
        since = self.parse_since(since_text)
        rows = await self.call(self.caps.parent_notifications_feed(since, limit))
        # SMS METNİ VE TELEFON DÖNMEZ: yalnız bu beş alan.
        items = [{
            "id": row.get("id"),
            "studentId": str(row.get("studentId") or "") or None,
            "kind": str(row.get("kind") or "") or None,
            "amount": int_or_none(row.get("amount")),
            "createdAt": self.iso(row.get("createdAt")),
        } for row in rows]
        return {"items": items, "nextSince": self.next_since(rows, since)}

    def next_since(self, rows: list[dict[str, Any]], since: str | None) -> str | None:
        """Bir sonraki yoklamanın imleci: en yeni `updatedAt` (yoksa `createdAt`)."""
        best: datetime | None = None
        for row in rows:
            moment = to_datetime(row.get("updatedAt") or row.get("createdAt"))
            if moment is not None and (best is None or moment > best):
                best = moment
        return self.iso(best, precise=True) if best is not None else since

    async def canteen_settings_public(self) -> dict[str, Any]:
        settings = await self.call(self.caps.canteen_settings_public())
        return {"allowPrepayment": is_truthy(settings.get("allowPrepayment")),
                "maxPrepayment": int_or_none(settings.get("maxPrepayment")) or 0}

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

    async def tablet_grant(self, student_id: str, *, package_name: str | None,
                           extra_seconds: int, reason: str, caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            result = await self.call(self.caps.tablet_grant(
                student_id, package_name=package_name, extra_seconds=extra_seconds,
                reason=reason, actor=caller.actor,
            ), not_found=self.tablet_failure())
            grant_id = result.get("grantId", result.get("id")) if isinstance(result, dict) \
                else result
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
            return {"blockedUntil": self.iso(value)}

        detail = {"until": until.isoformat() if until else None, "reason": reason}
        return await self.audited("tablet.block", caller, student_id, detail, work())

    async def tablet_unblock(self, student_id: str, *, reason: str,
                             caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            await self.call(self.caps.tablet_unblock(student_id, reason=reason,
                                                     actor=caller.actor),
                            not_found=self.tablet_failure())
            return {"blockedUntil": None}

        return await self.audited("tablet.unblock", caller, student_id, {"reason": reason},
                                  work())

    async def tablet_end_session(self, session_id: str, *, reason: str,
                                 caller: Caller) -> dict[str, Any]:
        async def work() -> dict[str, Any]:
            ended = await self.call(self.caps.tablet_end_session(
                session_id, reason=reason, actor=caller.actor,
            ), not_found=self.tablet_failure(session=True))
            if isinstance(ended, dict):
                ended = ended.get("ended", True)
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

    async def bell_week(self) -> dict[str, Any]:
        """`bbd_class_schedule` `/week` ile aynı biçim. Kaynak yoksa nedeniyle döner."""
        empty: dict[str, Any] = {"times": {day: [] for day in DAYS}, "groups": [],
                                 "available": False, "reason": None}
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
        return {
            "times": {day: [{"id": entry.get("id"), "time": entry.get("time"),
                             "label": entry.get("label"), "kind": entry.get("kind")}
                            for entry in times.get(day) or [] if isinstance(entry, dict)]
                      for day in DAYS},
            "groups": [{"id": group.get("id"), "name": group.get("name"),
                        "kind": group.get("kind"), "sort": group.get("sort")}
                       for group in groups if isinstance(group, dict)],
            "available": True,
            "reason": None,
        }

    # ========================================================== §7 OTP SMS

    async def send_otp(self, phone: str, code: str, purpose: str,
                       caller: Caller) -> dict[str, Any]:
        """Metin modül ayarındaki şablondan üretilir; istemci metin yazamaz."""
        normalized = normalize_phone(phone)
        if normalized is None:
            raise validation("Telefon 5XXXXXXXXX biçiminde olmalı.", "phone")
        await self.tick()
        now = self.now_ms()
        masked = mask_phone(normalized)
        subject = phone_subject(normalized)

        async def log(status: str, reason: str = "") -> None:
            await self.repo.log_sms(at=self.now_ms(), phone_masked=masked, purpose=purpose,
                                    status=status, reason=reason, user_id=caller.user_id,
                                    on_behalf_of=caller.on_behalf_of,
                                    request_id=caller.request_id)

        overall = await self.repo.window("sms_global", "*", now - MINUTE_MS)
        if overall.count >= self.sms_global_per_minute and overall.oldest is not None:
            await log("rate_limited", "global")
            raise rate_limited(math.ceil((overall.oldest + MINUTE_MS - now) / 1000))
        mine = await self.repo.window("sms_phone", subject, now - HOUR_MS)
        if mine.count >= self.sms_per_phone_per_hour and mine.oldest is not None:
            await log("rate_limited", "phone")
            raise rate_limited(math.ceil((mine.oldest + HOUR_MS - now) / 1000),
                               "Bu numaraya çok fazla kod gönderildi. Daha sonra deneyin.")
        await self.repo.hit("sms_global", "*", now)
        await self.repo.hit("sms_phone", subject, now)

        text = self.otp_template.replace("{code}", code)
        try:
            result = await self.caps.send_sms(normalized, text)
        except CapabilityMissing as missing:
            await log("failed", "capability_missing")
            raise capability_missing(missing.capability) from None
        except UpstreamFailure as failure:
            await log("failed", failure.reason or failure.kind)
            if failure.kind == "unavailable":
                raise canteen_unavailable() from None
            raise GatewayError(502, "sms_failed", "SMS gönderilemedi.") from None
        if not result.get("sent"):
            await log("failed", str(result.get("reason") or "unknown")[:60])
            raise GatewayError(502, "sms_failed", "SMS gönderilemedi.")
        await log("sent")
        return {"sent": True}

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
