"""Yetenek adaptörü — geçidin başka modüllere uzanan TEK dosyası.

Geçit hiçbir modülü import etmez (K3); her şey registry'deki yeteneklerden
gelir. Bütün yetenek çağrıları BURADA durur ki imza farkları (birleştirme
anında ortaya çıkabilir) yalnız bu dosyayı değiştirsin.

ÇÖZÜM GECİKMELİDİR. Yetenek `register` anında değil, istek anında çözülür:
isteğe bağlı bir sağlayıcı (tablet, zil) sonradan yüklense ya da hiç
yüklenmese bile modül ayakta kalır; eksik yetenek yalnız ilgili ucu
503 `capability_missing` yapar (K7).

SENKRON / ASENKRON. Mevcut tüketiciler (ör. `bbd_class_schedule`) yeteneğin
döndürdüğünü `await` edilebilirse bekler; burada da aynısı yapılır.

HATA SINIFLANDIRMA. Başka modülün hata sınıfı import edilemez; hata, taşıdığı
alanlardan (`status`, `reason`) ve sınıf adından sınıflandırılır ve
`UpstreamFailure` olarak yukarı çıkar. HTTP koduna çevirmek servisin işidir —
aynı kantin `reason`ı uca göre farklı durum alır (ör. `no_debt` ödeme
talebinde 409, tahsilatta 422).

SÜRE SINIRI. Her yetenek çağrısı bir üst süreyle sarılır: okuma
`read_timeout`, yazma `write_timeout` saniye. Kantin istemcisinin kendi
yeniden denemeleri (GET'te üç deneme) geçidi dakikalarca bekletemez; süre
dolunca çağrı iptal edilir ve `UpstreamFailure(kind="unavailable",
reason="timeout")` yükselir (kantin/dizin → 503 `canteen_unavailable`).
Yazma süresi bilerek uzundur: yarıda kesilen bir yazma kantinde yine de
tamamlanmış olabilir.
"""

from __future__ import annotations

import asyncio
import inspect
import secrets
from collections.abc import Callable
from datetime import datetime
from typing import Any

CANTEEN = "canteen.api"
DIRECTORY = "bbd_students.directory"
TABLET_READ = "bbd_tablet.read"
TABLET_CONTROL = "bbd_tablet.control"
BELL_WEEK = "bell.week"
BELL_ANNOUNCE = "bell.announce"
BELL_PLAN = "bell.plan"
GROUPS = "bbd_students.groups"
SECRETS = "secrets"
#: Kalıcı olay outbox'u (platform yeteneği, ADR 0029).
OUTBOX = "events.outbox"

#: İdempotensi özetinin HMAC anahtarı (kasada; ilk kullanımda üretilir).
PEPPER_KEY = "mobile_gateway.idempotency_pepper"

#: Kantin tahsilat raporunun liste taşıyabildiği anahtarlar (sürüme göre değişti).
_LIST_KEYS = ("data", "items", "entries", "collections", "rows", "payments", "notifications")

#: Yetenek çağrısı üst süreleri (saniye) — `timeouts` ayarıyla ezilir.
READ_TIMEOUT = 5.0
WRITE_TIMEOUT = 20.0


class CapabilityMissing(Exception):
    """Yetenek (ya da yeteneğin istenen metodu) kayıtlı değil."""

    def __init__(self, capability: str) -> None:
        super().__init__(capability)
        self.capability = capability


class LedgerUnavailable(Exception):
    """Kantinin hesap dökümü ucu (`ledger`) kullanılamıyor — eski yola dönülür.

    İki durumda yükselir: kantin istemcisinde `ledger` metodu yoktur (eski
    çekirdek dalı) ya da istemci kantinin ucu tanımadığını bildirir
    (`reason="ledger_unsupported"`, §1.7 öncesi kantin). Hata DEĞİLDİR;
    servis aynı yanıtı eski dört okumalı yoldan kurar.
    """


class Rejected(Exception):
    """KM içi yetenek isteği KURAL GEREĞİ reddetti (zil planı gövdesi, aralık).

    Yetenek kendi hata sınıfını (`PlanError`) yükseltir; sınıf import edilemez
    (K3), alanlarından okunur: `status` (4xx), `code`, Türkçe mesaj ve
    `details` (`{"path": "days[3].calls[0].at"}` gibi — gönderilen değer
    DEĞİL, yalnız alan yolu). Servis bunu sözleşme zarfına aynen taşır.
    """

    def __init__(self, source: str, status: int, code: str, message: str,
                 details: dict[str, Any] | None = None) -> None:
        super().__init__(f"{source}:{code}")
        self.source = source
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


def rejected(source: str, error: BaseException) -> Rejected | None:
    """Hata kodlu bir 4xx kural reddi mi? Öyleyse `Rejected`, değilse None."""
    status = getattr(error, "status", None)
    code = getattr(error, "code", None)
    if not (isinstance(status, int) and 400 <= status < 500 and isinstance(code, str) and code):
        return None
    raw = getattr(error, "details", None)
    details = {key: value for key, value in raw.items()
               if key in ("path", "missing", "profileId")} if isinstance(raw, dict) else {}
    message = " ".join(str(error).split())[:300] or "İstek reddedildi."
    return Rejected(source, status, code, message, details)


class UpstreamFailure(Exception):
    """Yetenek çağrısı hata verdi.

    kind: not_found | invalid | conflict | business | unavailable
    `reason` kantinin iş kuralı kodudur (no_debt, exceeds_debt …).
    Mesaj BİLEREK taşınmaz: kantin hata metni gönderilen değeri içerebilir.
    """

    def __init__(self, source: str, kind: str, *, status: int | None = None,
                 reason: str | None = None, details: dict[str, int] | None = None,
                 message: str | None = None) -> None:
        super().__init__(f"{source}:{kind}:{reason or status or ''}")
        self.source = source
        self.kind = kind
        self.status = status
        self.reason = reason
        # Kantin `exceeds_debt` gibi redlerde sınırı bildirir: {balance, maxAmount}.
        self.details = details
        # Yalnız KM içi yeteneklerin (tablet) Türkçe doğrulama metni; kantinin
        # metni taşınmaz (gönderilen değeri içerebilir).
        self.message = message


async def settle(value: Any) -> Any:
    """Senkron dönen yeteneği de, coroutine döneni de aynı biçimde karşılar."""
    if inspect.isawaitable(value):
        return await value
    return value


def accepts(function: Any, parameter: str) -> bool:
    """Çağrılabilir bu adlı argümanı kabul ediyor mu (ya da **kwargs)?"""
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return True
    for item in signature.parameters.values():
        if item.kind is inspect.Parameter.VAR_KEYWORD or item.name == parameter:
            return True
    return False


def unwrap(payload: Any) -> Any:
    """Laravel kaynak sarmalayıcısı `{"data": {...}}` açılır."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def rows(payload: Any) -> list[dict[str, Any]]:
    """Liste ya da `{"data"|"items"|…: [...]}` biçimindeki yanıttan satırlar."""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in _LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


#: Kantin red gövdesinden dışarı taşınabilen sayısal ayrıntılar (yalnız bunlar).
_DETAIL_KEYS = ("balance", "maxAmount")


def failure_details(error: BaseException) -> dict[str, int] | None:
    """Hata nesnesinin taşıdığı gövdeden `{balance, maxAmount}` — varsa.

    İstemci gövdeyi `details`, `body`, `payload` ya da `data` adıyla taşıyabilir
    (sürüme göre). Yalnız tamsayı alanlar alınır; metin asla.
    """
    for attribute in ("details", "body", "payload", "data"):
        source = getattr(error, attribute, None)
        if not isinstance(source, dict):
            continue
        if isinstance(source.get("details"), dict):
            source = source["details"]
        found = {key: int(source[key]) for key in _DETAIL_KEYS
                 if isinstance(source.get(key), int) and not isinstance(source.get(key), bool)}
        if found:
            return found
    return None


def page(payload: Any) -> dict[str, Any]:
    """Akış yanıtı: satırlar + kantinin imleci (`nextSince`) + `hasMore`.

    Kantinin `updatedSince` sınırı DAHİLDİR; tekrar gelen satır ref/id ile
    ayıklanır (istemcinin işi). İmleç verilmezse servis satırlardan hesaplar.
    """
    meta = payload if isinstance(payload, dict) else {}
    return {"items": rows(payload), "nextSince": meta.get("nextSince"),
            "hasMore": meta.get("hasMore")}


def canteen_failure(error: BaseException) -> UpstreamFailure:
    status = getattr(error, "status", None)
    status = status if isinstance(status, int) else None
    reason = getattr(error, "reason", None)
    reason = reason if isinstance(reason, str) and reason else None
    if reason == "student_not_found":
        return UpstreamFailure("canteen", "not_found", status=status, reason=reason)
    if reason == "validation_error":
        return UpstreamFailure("canteen", "invalid", status=status, reason=reason)
    if reason:
        return UpstreamFailure("canteen", "business", status=status, reason=reason,
                               details=failure_details(error))
    if status == 404:
        return UpstreamFailure("canteen", "not_found", status=status)
    if status in (400, 422):
        return UpstreamFailure("canteen", "invalid", status=status)
    if status == 409:
        return UpstreamFailure("canteen", "conflict", status=status)
    return UpstreamFailure("canteen", "unavailable", status=status)


def module_failure(source: str, error: BaseException) -> UpstreamFailure:
    """Tablet / zil / dizin hataları — `reason`/`code` alanından, yoksa sınıf adından.

    `bbd_tablet` yeteneği `TabletCapabilityError(reason=…)` fırlatır:
    validation_error · student_not_found · session_not_found ·
    canteen_unavailable. Dizin kantine ulaşamazsa `DirectoryUnavailable` verir.
    """
    name = type(error).__name__
    code = getattr(error, "reason", None) or getattr(error, "code", None)
    code = code if isinstance(code, str) and code else None
    if code is not None and code.endswith("_not_found"):
        return UpstreamFailure(source, "not_found", reason=code)
    if code == "validation_error":
        text = " ".join(str(error).split())[:200] or None
        return UpstreamFailure(source, "invalid", reason=code, message=text)
    if code == "canteen_unavailable" or "Unavailable" in name:
        return UpstreamFailure(source, "unavailable", reason="canteen_unavailable")
    if "NotFound" in name or isinstance(error, LookupError):
        return UpstreamFailure(source, "not_found", reason=code)
    if "Conflict" in name:
        return UpstreamFailure(source, "conflict", reason=code)
    if isinstance(error, ValueError):
        return UpstreamFailure(source, "invalid", reason=code)
    return UpstreamFailure(source, "unavailable", reason=code)


class Capabilities:
    """Geçidin tükettiği yeteneklerin tipli, hata çevirili yüzü."""

    def __init__(self, resolve: Callable[[str], Any], *, read_timeout: float = READ_TIMEOUT,
                 write_timeout: float = WRITE_TIMEOUT) -> None:
        # `ctx.try_capability`: manifestte ilan edilmemiş adı reddeder.
        self._resolve = resolve
        self.read_timeout = read_timeout
        self.write_timeout = write_timeout

    def _limit(self, write: bool) -> float:
        return self.write_timeout if write else self.read_timeout

    # ---------------------------------------------------------- çözüm

    def has(self, name: str) -> bool:
        return self._resolve(name) is not None

    def _get(self, name: str) -> Any:
        instance = self._resolve(name)
        if instance is None:
            raise CapabilityMissing(name)
        return instance

    def _method(self, capability: str, *names: str) -> Any:
        """Yeteneğin ilk bulunan metodu. Hiçbiri yoksa `capability.method` eksiktir."""
        instance = self._get(capability)
        for name in names:
            method = getattr(instance, name, None)
            if callable(method):
                return method
        raise CapabilityMissing(f"{capability}.{names[0]}")

    async def _canteen(self, *names: str, args: tuple[Any, ...] = (),
                       kwargs: dict[str, Any] | None = None, write: bool = False) -> Any:
        method = self._method(CANTEEN, *names)
        try:
            return await asyncio.wait_for(settle(method(*args, **(kwargs or {}))),
                                          self._limit(write))
        except CapabilityMissing:
            raise
        except TimeoutError:
            raise UpstreamFailure("canteen", "unavailable", reason="timeout") from None
        except Exception as error:  # noqa: BLE001 — kantin dışarısı
            raise canteen_failure(error) from None

    async def _module(self, source: str, capability: str, name: str | None,
                      args: tuple[Any, ...] = (), kwargs: dict[str, Any] | None = None,
                      write: bool = False) -> Any:
        target = self._get(capability) if name is None else self._method(capability, name)
        if name is None and not callable(target):
            raise CapabilityMissing(capability)
        try:
            return await asyncio.wait_for(settle(target(*args, **(kwargs or {}))),
                                          self._limit(write))
        except CapabilityMissing:
            raise
        except TimeoutError:
            raise UpstreamFailure(source, "unavailable", reason="timeout") from None
        except Exception as error:  # noqa: BLE001 — başka modül (K7)
            raise module_failure(source, error) from None

    # ---------------------------------------------------------------- secrets

    async def idempotency_pepper(self) -> str:
        """İdempotensi özetinin anahtarı. Yoksa üretilip kasaya yazılır.

        Gövde öğrenci şifresi ya da OTP taşıyabilir; 6 haneli bir kodun düz
        sha256'sı tablodan geri bulunabilirdi. Anahtarlı özet bunu kapatır.
        """
        vault = self._get(SECRETS)

        async def read_or_create() -> Any:
            value = await settle(vault.get(PEPPER_KEY))
            if not value:
                value = secrets.token_hex(32)
                await settle(vault.set(PEPPER_KEY, value))
            return value

        try:
            value = await asyncio.wait_for(read_or_create(), self.write_timeout)
        except TimeoutError:
            raise UpstreamFailure("secrets", "unavailable", reason="timeout") from None
        except Exception as error:  # noqa: BLE001 — platform yeteneği (K7)
            raise module_failure("secrets", error) from None
        return str(value)

    # ---------------------------------------------------- önbellek bildirimi

    def directory_changed(self) -> None:
        """Geçit kantine öğrenci verisi yazdı (kontrol, tahsilat): dizin yeteneğinin
        önbelleği bırakılsın ki bir sonraki dizin okuması eski bakiyeyi vermesin.

        `invalidate` yeteneğin isteğe bağlı parçasıdır; yoksa (eski sürüm) dizin
        kendi kısa süresi dolunca tazelenir.
        """
        directory = self._resolve(DIRECTORY)
        forget = getattr(directory, "invalidate", None)
        if callable(forget):
            try:
                forget()
            except Exception:  # noqa: BLE001 — önbellek bildirimi işi düşürmez (K7)
                return

    # ---------------------------------------------------------- events.outbox

    def outbox(self) -> Any:
        """Kalıcı olay outbox'u; yoksa None (eski çekirdek)."""
        return self._resolve(OUTBOX)

    async def emit(self, kind: str, subject: tuple[str, str | None], *,
                   fields: list[str] | None = None, data: dict[str, Any] | None = None,
                   causation_id: str | None = None) -> None:
        """Geçitten yapılan (Akademi) yazmanın olayı — `origin: akademi`.

        Yazma tamamlandıktan SONRA çağrılır; olay yazılamazsa yazma yine
        başarılıdır (K7) — Akademi kendi SSE ipucunu zaten basar (§8), öteki
        cihazlar 2 dk yoklamayla yetişir.
        """
        box = self.outbox()
        if box is None:
            return
        try:
            await settle(box.emit(kind, subject, fields=list(fields or []), data=data,
                                  origin="akademi", causation_id=causation_id or None))
        except Exception:  # noqa: BLE001 — olay yazması yanıtı bozmaz
            return

    def remember_payment_request(self, student_id: str, ref: str) -> None:
        """Kantinin bu talep için iteceği olayın KM'de ikinci olay üretmemesi için
        kantin istemcisinin yankı belleğine yazar (isteğe bağlı metod)."""
        canteen = self._resolve(CANTEEN)
        remember = getattr(canteen, "remember_write", None)
        if callable(remember) and ref:
            try:
                remember(student_id, "payment_request", [ref])
            except Exception:  # noqa: BLE001
                return

    # ---------------------------------------------------- bbd_students.directory

    async def directory_list(self, since: str | None) -> dict[str, Any]:
        """`list(since: datetime | None) -> {"students", "classes", "complete"}`."""
        moment = datetime.fromisoformat(since) if since else None
        result = await self._module("directory", DIRECTORY, "list", kwargs={"since": moment})
        return result if isinstance(result, dict) else {}

    async def directory_removed(self, since: str) -> list[dict[str, Any]]:
        """Kalıcı silinenler (mezar taşı). Eski dizin yeteneğinde boş liste."""
        directory = self._get(DIRECTORY)
        if not callable(getattr(directory, "removed_since", None)):
            return []
        result = await self._module("directory", DIRECTORY, "removed_since",
                                    (datetime.fromisoformat(since),))
        return [item for item in result or [] if isinstance(item, dict)]

    async def directory_many(self, ids: list[str]) -> dict[str, Any]:
        """`ids` ile toplu okuma: `get_many` yoksa tekil okumalar (eski yetenek)."""
        directory = self._get(DIRECTORY)
        if callable(getattr(directory, "get_many", None)):
            result = await self._module("directory", DIRECTORY, "get_many", (ids,))
            return result if isinstance(result, dict) else {"students": [], "missingIds": ids}
        found: list[dict[str, Any]] = []
        missing: list[str] = []
        for student_id in ids:
            record = await self.directory_get(student_id)
            if record is None:
                missing.append(student_id)
            else:
                found.append(record)
        return {"students": found, "missingIds": missing}

    async def directory_get(self, student_id: str) -> dict[str, Any] | None:
        try:
            result = await self._module("directory", DIRECTORY, "get", (student_id,))
        except UpstreamFailure as failure:
            if failure.kind == "not_found":
                return None
            raise
        return result if isinstance(result, dict) and result else None

    async def card_qr_text(self, student_id: str) -> str | None:
        try:
            result = await self._module("directory", DIRECTORY, "card_qr_text", (student_id,))
        except UpstreamFailure as failure:
            if failure.kind == "not_found":
                return None
            raise
        return result if isinstance(result, str) and result else None

    # --------------------------------------------------------------- canteen.api

    async def lookup_code(self, code: str) -> str | None:
        """Kodun sahibi öğrencinin kimliği; kod yoksa None. KOD LOG'A YAZILMAZ."""
        try:
            result = await self._canteen("lookup_student_by_code", args=(code,))
        except UpstreamFailure as failure:
            if failure.kind in ("not_found", "invalid"):
                return None
            raise
        student_id = result.get("id") if isinstance(result, dict) else None
        return student_id if isinstance(student_id, str) and student_id else None

    async def canteen_balances(self, ids: list[str] | None,
                               etag: str | None) -> dict[str, Any] | None:
        """Kantin bakiye uzlaştırması; `etag` tutarsa None (304)."""
        return await self._canteen("balances", args=(ids or None,), kwargs={"etag": etag})

    async def canteen_student(self, student_id: str) -> dict[str, Any]:
        """Tek öğrenci (bakiye, limitler, veli kontrolleri, `spentToday`)."""
        result = unwrap(await self._canteen("get_student", "student", args=(student_id,)))
        if not isinstance(result, dict) or not result:
            raise UpstreamFailure("canteen", "not_found", status=404)
        return result

    async def canteen_transactions(self, student_id: str, from_ms: int,
                                   to_ms: int) -> list[dict[str, Any]]:
        """Öğrencinin işlemleri — YALNIZ defter ucu olmayan kantinde (eski yol).

        İstemci `student_id` süzgeci tanıyorsa kullanılır; tanımıyorsa aralık
        okunur ve burada süzülür (kantin aralıkta en çok 5000 satır verir —
        bkz. README "Bilinen sınırlar")."""
        method = self._method(CANTEEN, "transactions")
        kwargs: dict[str, Any] = {"from_ms": from_ms, "to_ms": to_ms, "limit": 5000}
        if accepts(method, "student_id"):
            kwargs["student_id"] = student_id
        payload = await self._canteen("transactions", kwargs=kwargs)
        return [item for item in rows(payload)
                if str(item.get("studentId") or "") == student_id]

    async def canteen_collections(self, student_id: str, from_ms: int,
                                  to_ms: int) -> list[dict[str, Any]]:
        payload = await self._canteen("collections", kwargs={
            "from_ms": from_ms, "to_ms": to_ms, "student_id": student_id,
        })
        found = rows(payload)
        # Süzgeci yok sayan bir kantin sürümüne karşı: başkasının tahsilatı sızmaz.
        return [item for item in found
                if str(item.get("studentId") or student_id) == student_id]

    async def canteen_ledger(self, student_id: str, *, limit: int,
                             cursor: str | None = None, from_ms: int | None = None,
                             to_ms: int | None = None) -> dict[str, Any]:
        """Öğrencinin hesap dökümünden bir sayfa (kantin §1.7, tek istek).

        Dönüş: `{"items": [satır], "nextCursor": str | None, "hasMore": bool,
        "student": dict | None, "pendingPaymentRequest": dict | None,
        "first": bool}`. `student` ve `pendingPaymentRequest` yalnız ilk
        (imleçsiz) sayfada gelir; `first` kantinin onları gönderip
        göndermediğidir. Satır: `id, at, kind, direction, amount,
        balanceAfter, ref, sale`.

        Uç ya da istemci metodu yoksa `LedgerUnavailable` (servis eski yola
        döner). Öğrenci yoksa `UpstreamFailure(kind="not_found")`.
        """
        instance = self._get(CANTEEN)
        if not callable(getattr(instance, "ledger", None)):
            raise LedgerUnavailable()
        kwargs: dict[str, Any] = {"limit": limit}
        if cursor:
            kwargs["cursor"] = cursor
        if from_ms is not None:
            kwargs["from_ms"] = from_ms
        if to_ms is not None:
            kwargs["to_ms"] = to_ms
        try:
            payload = await self._canteen("ledger", args=(student_id,), kwargs=kwargs)
        except UpstreamFailure as failure:
            if failure.reason == "ledger_unsupported":
                raise LedgerUnavailable() from None
            raise
        meta = payload if isinstance(payload, dict) else {}
        if not isinstance(meta.get("data"), list):
            raise UpstreamFailure("canteen", "unavailable", reason="invalid_response")
        student = meta.get("student")
        pending = meta.get("pendingPaymentRequest")
        next_cursor = meta.get("nextCursor")
        return {
            "items": rows(meta),
            "nextCursor": str(next_cursor) if next_cursor else None,
            "hasMore": bool(meta.get("hasMore")) and bool(next_cursor),
            "student": unwrap(student) if isinstance(student, dict) else None,
            "pendingPaymentRequest": pending if isinstance(pending, dict) else None,
            "first": "student" in meta,
        }

    async def payment_requests_for(self, student_id: str) -> list[dict[str, Any]]:
        """Öğrencinin ödeme talepleri.

        İstemci `student_id` süzgecini tanıyorsa öğrenci kantine gönderilir:
        kantin süzgeci desteklediğinde (kantin-veli dalı, `GET
        /api/payment-requests?studentId=`) yalnız o öğrencinin talepleri gelir.
        Desteklemeyen kantin parametreyi yok sayar ve son 100 talebi verir;
        satırlar HER DURUMDA burada da süzülür — başka öğrencinin talebi sızmaz.
        """
        method = self._method(CANTEEN, "payment_requests")
        kwargs: dict[str, Any] = {}
        if accepts(method, "student_id"):
            kwargs["student_id"] = student_id
        payload = await self._canteen("payment_requests", kwargs=kwargs)
        return [item for item in rows(payload)
                if str(item.get("studentId") or "") == student_id]

    async def create_payment_request(self, student_id: str,
                                     amount: int | None) -> dict[str, Any]:
        method = self._method(CANTEEN, "create_payment_request")
        kwargs: dict[str, Any] = {}
        if amount is not None:
            if not accepts(method, "amount"):
                raise CapabilityMissing(f"{CANTEEN}.create_payment_request(amount)")
            kwargs["amount"] = amount
        # Kantin talebin kaynağını tutar (admin | bulk | threshold | api).
        if accepts(method, "origin"):
            kwargs["origin"] = "api"
        result = await self._canteen("create_payment_request", args=(student_id,),
                                     kwargs=kwargs, write=True)
        # Eski istemci başarısızlıkta da 200 döner: {"queued": false, "reason": …}
        if isinstance(result, dict) and result.get("queued") is False:
            reason = result.get("reason")
            raise canteen_failure(_Reason(reason if isinstance(reason, str) else None))
        data = unwrap(result)
        return data if isinstance(data, dict) else {}

    async def update_controls(self, student_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        result = unwrap(await self._canteen("update_controls", args=(student_id, patch),
                                            write=True))
        return result if isinstance(result, dict) else {}

    async def record_collection(self, student_id: str, amount: int,
                                local_id: str) -> dict[str, Any]:
        result = unwrap(await self._canteen(
            "record_collection", "collect_cash", args=(student_id,),
            kwargs={"amount": amount, "local_id": local_id}, write=True,
        ))
        return result if isinstance(result, dict) else {}

    async def products(self) -> list[dict[str, Any]]:
        return rows(await self._canteen("products"))

    async def payment_requests_feed(self, since: str | None, limit: int) -> dict[str, Any]:
        """`{"items", "nextSince", "hasMore"}` — istemci liste de dönebilir."""
        return page(await self._canteen("payment_requests_feed",
                                        kwargs={"updated_since": since, "limit": limit}))

    async def parent_notifications_feed(self, since: str | None, limit: int) -> dict[str, Any]:
        return page(await self._canteen("parent_notifications_feed",
                                        kwargs={"updated_since": since, "limit": limit}))

    async def canteen_settings_public(self) -> dict[str, Any]:
        result = unwrap(await self._canteen("canteen_settings_public"))
        return result if isinstance(result, dict) else {}

    async def send_sms(self, phone: str, message: str) -> dict[str, Any]:
        """Kantinin Netgsm hesabıyla tek SMS. Yanıtın `text` alanı ÇAĞIRANA DÖNMEZ."""
        result = await self._canteen("send_sms", kwargs={"phone": phone, "message": message},
                                     write=True)
        if not isinstance(result, dict):
            return {"sent": False, "reason": "unknown"}
        return {"sent": bool(result.get("sent")), "reason": result.get("reason")}

    # ------------------------------------------------------------- bbd_tablet

    async def tablet_board(self) -> dict[str, Any]:
        result = await self._module("tablet", TABLET_READ, "board")
        return result if isinstance(result, dict) else {}

    async def tablet_usage(self, student_id: str, date_from: str,
                           date_to: str) -> dict[str, Any]:
        result = await self._module("tablet", TABLET_READ, "student_usage", (student_id,),
                                    {"date_from": date_from, "date_to": date_to})
        return result if isinstance(result, dict) else {}

    async def tablet_daily_usage(self, local_date: str) -> dict[str, Any]:
        """Kurum geneli günlük kullanım. Eski `bbd_tablet` bu metodu sunmazsa
        `bbd_tablet.read.daily_usage` eksiktir (503 capability_missing)."""
        result = await self._module("tablet", TABLET_READ, "daily_usage", (local_date,))
        return result if isinstance(result, dict) else {}

    async def tablet_grant(self, student_id: str, *, package_name: str | None,
                           extra_seconds: int, reason: str, actor: str) -> Any:
        return await self._module("tablet", TABLET_CONTROL, "grant", (student_id,), {
            "package_name": package_name, "extra_seconds": extra_seconds,
            "reason": reason, "actor": actor,
        }, write=True)

    async def tablet_block(self, student_id: str, *, until: Any, reason: str,
                           actor: str) -> Any:
        return await self._module("tablet", TABLET_CONTROL, "block", (student_id,),
                                  {"until": until, "reason": reason, "actor": actor},
                                  write=True)

    async def tablet_unblock(self, student_id: str, *, reason: str, actor: str) -> Any:
        return await self._module("tablet", TABLET_CONTROL, "unblock", (student_id,),
                                  {"reason": reason, "actor": actor}, write=True)

    async def tablet_session_student(self, session_id: str) -> str | None:
        """Oturumun öğrencisi — eski tablet yeteneğinde None (olay konusu boş kalır)."""
        control = self._resolve(TABLET_CONTROL)
        if control is None or not callable(getattr(control, "session_student", None)):
            return None
        try:
            result = await self._module("tablet", TABLET_CONTROL, "session_student",
                                        (session_id,))
        except (UpstreamFailure, CapabilityMissing):
            return None
        return str(result) if result else None

    async def tablet_end_session(self, session_id: str, *, reason: str, actor: str) -> Any:
        return await self._module("tablet", TABLET_CONTROL, "end_session", (session_id,),
                                  {"reason": reason, "actor": actor}, write=True)

    # ------------------------------------------------------------------- bell

    async def bell_week(self) -> dict[str, Any]:
        result = await self._module("bell", BELL_WEEK, None)
        return result if isinstance(result, dict) else {}

    async def bell_page(self, text: str, *, cache_key: str | None, actor: str | None,
                        request_id: str | None) -> dict[str, Any]:
        announce = self._get(BELL_ANNOUNCE)
        name = "page" if callable(getattr(announce, "page", None)) else None
        if name is None and not callable(announce):
            raise CapabilityMissing(f"{BELL_ANNOUNCE}.page")
        result = await self._module("bell", BELL_ANNOUNCE, name, (text,), {
            "cache_key": cache_key, "actor": actor, "request_id": request_id,
        }, write=True)
        return result if isinstance(result, dict) else {}

    async def _plan(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any] | None = None,
                    *, write: bool = False) -> dict[str, Any]:
        """`bell.plan` çağrısı. Kural reddi (`PlanError`) `Rejected` olarak çıkar."""
        method = self._method(BELL_PLAN, name)
        try:
            result = await asyncio.wait_for(settle(method(*args, **(kwargs or {}))),
                                            self._limit(write))
        except CapabilityMissing:
            raise
        except TimeoutError:
            raise UpstreamFailure("bell", "unavailable", reason="timeout") from None
        except Exception as error:  # noqa: BLE001 — başka modül (K7)
            refusal = rejected("bell", error)
            if refusal is not None:
                raise refusal from None
            raise module_failure("bell", error) from None
        return result if isinstance(result, dict) else {}

    async def bell_plan_apply(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        """`apply_days(body, *, actor)` → `{days: [{date, result, revision[, error,
        details]}], voicesPending, bellEnabled}`. Plan yerel tablolara yazılır."""
        return await self._plan("apply_days", (body,), {"actor": actor}, write=True)

    async def bell_plan_status(self, date_from: str, date_to: str) -> dict[str, Any]:
        """`status(from, to)` → `{bellEnabled, sessionCallsEnabled, agent, days}`."""
        return await self._plan("status", (date_from, date_to))

    # ----------------------------------------------------- bbd_students.groups

    async def groups_list(self, since: int | None) -> dict[str, Any]:
        """`list(since_revision=None)` → `{revision, full, groups, complete}`."""
        result = await self._module("groups", GROUPS, "list",
                                    kwargs={"since_revision": since})
        return result if isinstance(result, dict) else {}

    async def groups_revision(self) -> int | None:
        """Kalıcı grup revizyonu — yetenek `revision()` sunmuyorsa None.

        Tam listeyi okumadan önbelleğin hâlâ geçerli olduğunu söyleyen tek
        satırlık okuma (`mod_bbd_students_group_rev`)."""
        instance = self._get(GROUPS)
        if not callable(getattr(instance, "revision", None)):
            return None
        result = await self._module("groups", GROUPS, "revision")
        return result if isinstance(result, int) and not isinstance(result, bool) else None


class _Reason(Exception):
    """Eski istemcinin `queued: false` yanıtını kodlu hataya çevirmek için."""

    def __init__(self, reason: str | None) -> None:
        super().__init__(reason or "")
        self.reason = reason
        self.status = 422
