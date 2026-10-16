"""İzin listesiyle sınırlı vekil — KM kullanıcısı ile Akademi arasında.

İKİ KAPI (K9), sırası değişmez:

1. **İzin listesi.** Yöntem + yol sözleşmeden üretilmiş listede yoksa
   `403 operation_not_allowed`. Liste dışı hiçbir Akademi yolu çağrılamaz.
2. **KM izni.** İşlemin Akademi izni `permission_map.py` ile KM iznine
   çevrilir; kullanıcı bunu taşımıyorsa `403 forbidden` (`details.permission`).
   Rol adı sorulmaz (K10) — `CurrentUser.has_permission`.

Step-up isteyen işlemde (sözleşmede `x-step-up`) ayrıca tek kullanımlık PIN
teyidi belirteci aranır (`stepup.py`); yoksa `403 step_up_required`.

İKİ GİRİŞ, TEK KURAL:

* **Ham vekil** `/api/bbd_akademi/v1/...` — gövde, sorgu, `Idempotency-Key`
  aynen geçer; çok parçalı yükleme (`multipart/form-data`) ve ikili indirme
  AKIŞ olarak geçer, belleğe alınmaz. Akademi'nin hata zarfı olduğu gibi döner.
* **Panel köprüsü** `POST /api/bbd_akademi/km/call` — masaüstü kabuğunun
  istek kanalı yalnız JSON metin taşıyor ve özel başlık geçiremiyor
  (`ui-kernel.js` → `api()`); hata yanıtında da yalnız `message` kalıyor.
  Köprü AYNI kapılardan geçirip Akademi yanıtını `{ok, status, data, error,
  headers}` zarfında döndürür: panel `error.code`/`details`'i (ör.
  `timetable_conflict`, `validation_error` alanları) görebilir, dosya base64
  olarak gider ve gelir.

Akademi'nin 401'i (`invalid_integration_key`) istemciye 502 olarak döner: KM
API'sinde 401 "KM oturumun yok" demektir; entegrasyon anahtarının reddi
kullanıcının oturumuyla ilgili değildir. Gövde (Akademi zarfı) değişmez.

AKADEMİ GÜNCELLENİRKEN (dağıtım anı, 2026-10-15): ağ geçidi (Traefik/Cloudflare)
kısa süre JSON olmayan 502/503/504 verir ya da bağlantı kurulamaz. Panel
köprüsünde OKUMA (GET) istekleri bu durumda kısa geri çekilmeyle yeniden denenir
(toplam `read_retry_seconds`, varsayılan 20 sn); yine olmazsa `503
akademi_unavailable` + `details.retryable` döner ve panel "güncelleniyor"
bandını gösterip kendisi yeniler. YAZMALAR YENİDEN DENENMEZ (ikinci kayıt
riski); panel aynı `Idempotency-Key` ile elle tekrar gönderirse Akademi ilk
sonucu döndürür (A9) — güvenli olan yol budur. Akademi'nin KENDİ hata zarfı
(JSON 503 dahil) yeniden denenmez; yalnız `X-Akademi-Retryable: 1` taşıyan
`503 service_unavailable` (Akademi'nin veritabanı/Redis'i o an yeniden başlıyor)
geçici sayılır.

SON BİLİNEN VERİ (çökme dayanıklılığı, 2026-10-15): köprüde başarılı her OKUMA
(200, JSON) kullanıcı + işlem + yol + sorgu anahtarıyla bellekte tutulur
(`StaleCache`, en çok 16 MB / 24 sa). Akademi'ye ulaşılamazsa (yeniden
denemeler bitti ya da devre kesici açık) aynı okuma bu veriyle döner:
`ok: true` + `stale: {at, reason}`; panel "Akademi sunucusuna şu an
ulaşılamıyor, otomatik yeniden deneniyor — son bilinen bilgiler (14:32)"
bandını gösterir. Kapılar (izin listesi + KM izni) önbellekten önce yine
uygulanır; anahtar kullanıcıya özeldir (başkasının gördüğü veri sızmaz).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import re
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import Message
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from km_sdk import CurrentUser

from ..permission_map import km_permissions, requires_step_up
from .gateway import (
    FORWARD_REQUEST_HEADERS,
    FORWARD_RESPONSE_HEADERS,
    AkademiError,
    AkademiGateway,
    AkademiUnavailable,
    on_behalf_of,
    request_id,
)
from .operations import Allowlist, Resolved
from .stepup import StepUpStore

STEP_UP_HEADER = "x-km-step-up"
IDEMPOTENCY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class ProxyError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


def envelope(status: int, code: str, message: str, details: Any = None,
             trace: str | None = None) -> dict[str, Any]:
    """Akademi zarfıyla AYNI biçim (`{error: {code, message, details, traceId}}`).

    `status` alanı fazladan yazılır: kabuğun çekirdek zarfı da onu taşır.
    """
    return {"error": {"code": code, "message": message, "details": details,
                      "traceId": trace, "status": status}}


def error_response(failure: ProxyError | AkademiError, trace: str) -> JSONResponse:
    if isinstance(failure, ProxyError):
        status, code, message, details = (failure.status, failure.code, failure.message,
                                          failure.details)
    else:
        status, code, message, details = (failure.status, failure.code, str(failure),
                                          failure.details)
    return JSONResponse(status_code=status, content=envelope(status, code, message, details, trace),
                        headers={"X-Request-Id": trace})


@dataclass(slots=True)
class Upload:
    field: str
    filename: str
    content_type: str
    data: bytes
    fields: dict[str, str]


#: Ağ geçidinin geçici yanıtları: Traefik "no available server" (503), kapanmakta olan arka uç (502), ağ geçidi
#: zaman aşımı (504), Cloudflare kaynak hataları (520–523). JSON gövdeli olanlar Akademi'nin kendi yanıtıdır.
TRANSIENT_STATUS = frozenset({502, 503, 504, 520, 521, 522, 523})
#: Yeniden denemeler arası bekleme (sn); toplam `read_retry_seconds` ile sınırlı.
RETRY_DELAYS = (0.5, 1.0, 2.0, 3.0, 4.0, 5.0)
UPDATING_MESSAGE = ("Akademi şu an güncelleniyor ya da yeniden başlıyor; birkaç saniye sonra "
                    "otomatik olarak yeniden denenecek.")
UPDATING_WRITE_MESSAGE = ("Akademi şu an güncelleniyor; işlem kaydedilmemiş olabilir. Birkaç saniye "
                          "sonra listeyi yenileyip tekrar deneyin.")


def transient(upstream: httpx.Response) -> bool:
    """Ağ geçidinin geçici hatası mı (Akademi'nin kendi JSON hata zarfı değil)? Akademi'nin altyapı kesintisi
    yanıtı (`503 service_unavailable`, `X-Akademi-Retryable: 1`) da geçicidir."""
    if upstream.status_code == 503 and upstream.headers.get("x-akademi-retryable") == "1":
        return True
    return (upstream.status_code in TRANSIENT_STATUS
            and "json" not in upstream.headers.get("content-type", ""))


class StaleCache:
    """Son başarılı okumalar (LRU, bayt bütçeli). Yalnız süreç belleğinde; diske yazılmaz."""

    def __init__(self, *, max_bytes: int = 16 * 1024 * 1024, max_entry_bytes: int = 1024 * 1024,
                 max_age_seconds: float = 24 * 3600, clock: Callable[[], float] = time.time) -> None:
        self.max_bytes = max_bytes
        self.max_entry_bytes = max_entry_bytes
        self.max_age_seconds = max_age_seconds
        self._clock = clock
        self._items: OrderedDict[tuple[str, ...], tuple[float, int, Any, dict[str, str]]] = OrderedDict()
        self._bytes = 0

    def put(self, key: tuple[str, ...], data: Any, headers: Mapping[str, str]) -> None:
        try:
            size = len(json.dumps(data, ensure_ascii=False, default=str))
        except (TypeError, ValueError):
            return
        self.drop(key)
        if size > self.max_entry_bytes:
            return
        kept = {k: v for k, v in headers.items() if k in ("etag", "last-modified")}
        self._items[key] = (self._clock(), size, data, kept)
        self._bytes += size
        while self._bytes > self.max_bytes and self._items:
            _, (_, old, _, _) = self._items.popitem(last=False)
            self._bytes -= old

    def get(self, key: tuple[str, ...]) -> tuple[float, Any] | None:
        item = self._items.get(key)
        if item is None:
            return None
        at, _, data, _ = item
        if self._clock() - at > self.max_age_seconds:
            self.drop(key)
            return None
        self._items.move_to_end(key)
        return at, data

    def drop(self, key: tuple[str, ...]) -> None:
        item = self._items.pop(key, None)
        if item is not None:
            self._bytes -= item[1]

    def __len__(self) -> int:
        return len(self._items)


class AkademiProxy:
    def __init__(self, *, gateway: AkademiGateway, allowlist: Allowlist,
                 step_up: StepUpStore, log: Any,
                 sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.gateway = gateway
        self.allowlist = allowlist
        self.step_up = step_up
        self._log = log
        self._sleep = sleep
        self._clock = clock
        self.stale = StaleCache()

    async def _read_with_retry(self, send: Callable[[], Awaitable[httpx.Response]], *,
                               operation_id: str, trace: str) -> httpx.Response:
        """OKUMA isteği: bağlantı hatası ya da ağ geçidinin geçici 5xx'inde kısa geri çekilmeyle
        yeniden dener (toplam `read_retry_seconds`). Bütçe biterse `AkademiUnavailable`
        (`details.retryable`)."""
        budget = max(float(self.gateway.config.read_retry_seconds), 0.0)
        deadline = self._clock() + budget
        attempt = 0
        while True:
            attempt += 1
            upstream: httpx.Response | None = None
            reason = "connect"
            try:
                upstream = await send()
            except AkademiUnavailable as failure:
                # Zaman aşımı yeniden denenmez: istek zaten `timeout_seconds` bekledi. Devre açıksa
                # (gateway devre kesicisi) bağlantı hatası gibi beklenir: süre dolunca deneme isteği geçer.
                if (failure.details or {}).get("reason") not in ("connect", "circuit_open"):
                    raise
            else:
                if not transient(upstream):
                    if attempt > 1:
                        self._log.info("akademi yeniden denemede yanıt verdi", operation=operation_id,
                                       attempts=attempt, trace=trace)
                    return upstream
                reason = f"gateway_{upstream.status_code}"
                await upstream.aclose()
            delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
            if self._clock() + delay > deadline:
                self._log.warning("akademi güncelleniyor/erişilemiyor", operation=operation_id,
                                  attempts=attempt, reason=reason, trace=trace)
                raise AkademiUnavailable(UPDATING_MESSAGE, details={
                    "reason": "updating" if upstream is not None else "connect",
                    "upstreamStatus": upstream.status_code if upstream is not None else None,
                    "retryable": True, "attempts": attempt})
            await self._sleep(delay)

    @property
    def log(self) -> Any:
        return self._log

    # ------------------------------------------------------------ kapılar

    def authorize(self, user: CurrentUser, method: str, path: str) -> Resolved:
        resolved = self.allowlist.resolve(method, path)
        if resolved is None:
            raise ProxyError(403, "operation_not_allowed",
                             "Bu Akademi işlemi Kontrol Merkezi üzerinden açık değil.",
                             {"method": method.upper(), "path": "/" + path.strip("/")})
        try:
            needed = km_permissions(resolved.operation)
        except KeyError:
            # KAPALI KALIR (iş 63): haritada karşılığı olmayan Akademi izni hiçbir
            # KM iznine düşmez — işlem 500 değil, açık bir 403'le reddedilir.
            raise ProxyError(403, "permission_unmapped",
                             "Bu Akademi işleminin Kontrol Merkezi izni tanımlı değil.",
                             {"operationId": resolved.operation.operation_id}) from None
        if not any(user.has_permission(key) for key in needed):
            raise ProxyError(403, "forbidden", "Bu işlem için yetkiniz yok.",
                             {"permission": needed[0], "anyOf": list(needed),
                              "operationId": resolved.operation.operation_id})
        return resolved

    async def require_step_up(self, resolved: Resolved, user: CurrentUser,
                              token: str | None) -> bool:
        """Step-up işlemi değilse False; işlemse belirteci tüketir ya da reddeder.

        True yalnız SÖZLEŞMEDEKİ step-up işlemlerinde döner (Akademi'ye
        `X-Akademi-Step-Up` başlığı gider); KM'nin kendi istediği teyitte
        (`KM_STEP_UP_OPERATIONS`) belirteç yine tüketilir ama başlık gitmez.
        """
        if not requires_step_up(resolved.operation):
            return False
        if await self.step_up.consume(token, user_id=user.id,
                                      operation_id=resolved.operation.operation_id):
            return resolved.operation.step_up
        raise ProxyError(403, "step_up_required",
                         "Bu işlem için PIN'inizi yeniden girmeniz gerekiyor.",
                         {"operationId": resolved.operation.operation_id})

    def headers_for(self, user: CurrentUser, trace: str, *, step_up: bool,
                    incoming: Mapping[str, str] | None = None,
                    idempotency_key: str | None = None,
                    if_none_match: str | None = None) -> dict[str, str]:
        headers: dict[str, str] = {}
        for name in FORWARD_REQUEST_HEADERS:
            value = (incoming or {}).get(name)
            if value:
                headers[name] = value
        if idempotency_key:
            headers["idempotency-key"] = idempotency_key
        if if_none_match:
            headers["if-none-match"] = if_none_match
        headers.setdefault("accept", "application/json")
        headers["X-Akademi-On-Behalf-Of"] = on_behalf_of(user.id, user.full_name)
        headers["X-Request-Id"] = trace
        if step_up:
            headers["X-Akademi-Step-Up"] = "km-pin"
        return headers

    def _log_call(self, user: CurrentUser, resolved: Resolved, status: int, trace: str) -> None:
        if resolved.operation.is_write or status >= 400:
            self._log.info("akademi işlemi", operation=resolved.operation.operation_id,
                           user=user.id, status=status, trace=trace)

    # ------------------------------------------------------------ ham vekil

    async def forward(self, request: Request, user: CurrentUser, path: str) -> Response:
        trace = request_id(request.headers.get("x-request-id"))
        try:
            resolved = self.authorize(user, request.method, path)
            step_up = await self.require_step_up(resolved, user,
                                                 request.headers.get(STEP_UP_HEADER))
            limit = self.gateway.config.max_upload_bytes
            declared = request.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > limit:
                raise ProxyError(413, "payload_too_large",
                                 f"Dosya en çok {self.gateway.config.max_upload_mb} MB olabilir.")
            headers = self.headers_for(user, trace, step_up=step_up, incoming=request.headers)
            content: AsyncIterator[bytes] | None = None
            if request.method not in ("GET", "DELETE") or declared not in (None, "0"):
                content = _limited(request.stream(), limit)
            upstream = await self.gateway.send(
                request.method, resolved.upstream_path, query=request.url.query,
                headers=headers, content=content, upload=resolved.operation.multipart)
        except (ProxyError, AkademiError) as failure:
            return error_response(failure, trace)
        self._log_call(user, resolved, upstream.status_code, trace)
        return _stream_back(upstream, trace)

    async def download(self, url: str, trace: str) -> Response:
        try:
            upstream = await self.gateway.fetch(url)
        except AkademiError as failure:
            return error_response(failure, trace)
        return _stream_back(upstream, trace)

    # --------------------------------------------------------- panel köprüsü

    async def call(self, user: CurrentUser, *, method: str, path: str,
                   query: Mapping[str, Any] | None = None, body: Any = None,
                   idempotency_key: str | None = None, step_up_token: str | None = None,
                   if_none_match: str | None = None, upload: Upload | None = None,
                   trace: str | None = None) -> dict[str, Any]:
        trace = request_id(trace)
        try:
            if idempotency_key and not IDEMPOTENCY.match(idempotency_key):
                raise ProxyError(400, "idempotency_key_invalid",
                                 "İşlem anahtarı geçersiz (8–128 karakter, harf/rakam/._:-).")
            path_only, _, inline_query = path.partition("?")
            resolved = self.authorize(user, method, path_only)
            step_up = await self.require_step_up(resolved, user, step_up_token)
            headers = self.headers_for(user, trace, step_up=step_up,
                                       idempotency_key=idempotency_key,
                                       if_none_match=if_none_match)
            query_text = _encode_query(query, inline_query)
            if upload is not None:
                if len(upload.data) > self.gateway.config.max_upload_bytes:
                    raise ProxyError(413, "payload_too_large",
                                     f"Dosya en çok {self.gateway.config.max_upload_mb} MB "
                                     "olabilir.")
                upstream = await self.gateway.send(
                    method.upper(), resolved.upstream_path, query=query_text, headers=headers,
                    files={upload.field: (upload.filename, upload.data, upload.content_type)},
                    data=upload.fields or None, upload=True)
            else:
                send_json = body is not None and method.upper() != "GET"
                if send_json:
                    headers["content-type"] = "application/json"

                def send() -> Awaitable[httpx.Response]:
                    return self.gateway.send(
                        method.upper(), resolved.upstream_path, query=query_text, headers=headers,
                        content=json.dumps(body, ensure_ascii=False).encode("utf-8")
                        if send_json else None)

                if resolved.operation.is_write:
                    upstream = await send()
                else:
                    stale_key = (str(user.id), resolved.operation.operation_id,
                                 resolved.upstream_path, query_text)
                    # Devre açıksa ve son bilinen veri varsa beklemeden onu ver.
                    if self.gateway.circuit()["open"] and (hit := self.stale.get(stale_key)):
                        return _stale_envelope(hit, trace, "circuit_open")
                    try:
                        upstream = await self._read_with_retry(
                            send, operation_id=resolved.operation.operation_id, trace=trace)
                    except AkademiUnavailable as failure:
                        if hit := self.stale.get(stale_key):
                            reason = str((failure.details or {}).get("reason") or "unavailable")
                            self._log.warning("akademi erişilemiyor — son bilinen veri", trace=trace,
                                              operation=resolved.operation.operation_id)
                            return _stale_envelope(hit, trace, reason)
                        raise
            if transient(upstream):
                # Yazma (yeniden denenmez) ya da yükleme: ağ geçidi hatası Akademi'nin zarfı gibi değil,
                # "güncelleniyor" olarak döner; panel aynı Idempotency-Key ile güvenle tekrar gönderebilir.
                status_code = upstream.status_code
                await upstream.aclose()
                raise AkademiUnavailable(UPDATING_WRITE_MESSAGE, details={
                    "reason": "updating", "upstreamStatus": status_code, "retryable": False})
        except (ProxyError, AkademiError) as failure:
            if isinstance(failure, ProxyError):
                status, code, message, details = (failure.status, failure.code,
                                                  failure.message, failure.details)
            else:
                status, code, message, details = (failure.status, failure.code, str(failure),
                                                  failure.details)
            return {"ok": False, "status": status, "data": None,
                    "error": envelope(status, code, message, details, trace)["error"],
                    "headers": {"requestId": trace}}
        try:
            result = await _buffer(upstream, self.gateway.config.max_upload_bytes * 2)
        except ProxyError as failure:
            return {"ok": False, "status": failure.status, "data": None,
                    "error": envelope(failure.status, failure.code, failure.message,
                                      failure.details, trace)["error"],
                    "headers": {"requestId": trace}}
        finally:
            await upstream.aclose()
        self._log_call(user, resolved, upstream.status_code, trace)
        result["headers"].setdefault("requestId", trace)
        if (method.upper() == "GET" and upload is None and upstream.status_code == 200
                and result.get("file") is None and result.get("data") is not None):
            self.stale.put((str(user.id), resolved.operation.operation_id, resolved.upstream_path,
                            query_text), result["data"], result["headers"])
        return result

    async def fetch_file(self, url: str) -> dict[str, Any]:
        trace = request_id(None)
        try:
            upstream = await self.gateway.fetch(url)
        except AkademiError as failure:
            return {"ok": False, "status": failure.status, "data": None,
                    "error": envelope(failure.status, failure.code, str(failure),
                                      failure.details, trace)["error"],
                    "headers": {"requestId": trace}}
        try:
            return await _buffer(upstream, self.gateway.config.max_upload_bytes * 2,
                                 force_file=True)
        except ProxyError as failure:
            return {"ok": False, "status": failure.status, "data": None,
                    "error": envelope(failure.status, failure.code, failure.message,
                                      failure.details, trace)["error"],
                    "headers": {"requestId": trace}}
        finally:
            await upstream.aclose()


# ----------------------------------------------------------------- yardım


def _stale_envelope(hit: tuple[float, Any], trace: str, reason: str) -> dict[str, Any]:
    at, data = hit
    stamp = datetime.fromtimestamp(at, UTC).isoformat().replace("+00:00", "Z")
    return {"ok": True, "status": 200, "data": data,
            "headers": {"requestId": trace, "x-km-stale": "1"},
            "stale": {"at": stamp, "reason": reason}}


async def _limited(stream: AsyncIterator[bytes], limit: int) -> AsyncIterator[bytes]:
    total = 0
    async for chunk in stream:
        total += len(chunk)
        if total > limit:
            raise ProxyError(413, "payload_too_large", "Gönderilen dosya izin verilen boyutu aşıyor.")
        yield chunk


def _encode_query(query: Mapping[str, Any] | None, inline: str = "") -> str:
    pairs: list[tuple[str, str | int | float | bool | None]] = []
    for key, value in (query or {}).items():
        if value is None or value == "":
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            if item is None or item == "":
                continue
            if isinstance(item, bool):
                item = "true" if item else "false"
            pairs.append((str(key), str(item)))
    encoded = str(httpx.QueryParams(pairs)) if pairs else ""
    return "&".join(part for part in (inline, encoded) if part)


def _response_headers(upstream: httpx.Response, trace: str) -> dict[str, str]:
    headers = {name: upstream.headers[name] for name in FORWARD_RESPONSE_HEADERS
               if name in upstream.headers}
    headers.setdefault("x-request-id", trace)
    return headers


def _status(upstream: httpx.Response) -> int:
    return 502 if upstream.status_code == 401 else upstream.status_code


def _stream_back(upstream: httpx.Response, trace: str) -> Response:
    headers = _response_headers(upstream, trace)
    if upstream.status_code == 401:
        headers["x-akademi-upstream-status"] = "401"
    if upstream.status_code in (204, 304):
        return Response(status_code=upstream.status_code, headers=headers,
                        background=BackgroundTask(upstream.aclose))
    return StreamingResponse(upstream.aiter_bytes(), status_code=_status(upstream),
                             headers=headers, background=BackgroundTask(upstream.aclose))


def _filename(disposition: str | None, fallback: str) -> str:
    if not disposition:
        return fallback
    message = Message()
    message["content-disposition"] = disposition
    name = message.get_filename()
    return str(name) if name else fallback


async def _buffer(upstream: httpx.Response, limit: int, *, force_file: bool = False
                  ) -> dict[str, Any]:
    chunks: list[bytes] = []
    total = 0
    async for chunk in upstream.aiter_bytes():
        total += len(chunk)
        if total > limit:
            raise ProxyError(502, "response_too_large", "Akademi yanıtı çok büyük.")
        chunks.append(chunk)
    raw = b"".join(chunks)
    status = upstream.status_code
    headers: dict[str, Any] = {}
    if "etag" in upstream.headers:
        headers["etag"] = upstream.headers["etag"]
    if "retry-after" in upstream.headers:
        headers["retryAfter"] = upstream.headers["retry-after"]
    if upstream.headers.get("idempotency-replayed", "").lower() == "true":
        headers["idempotencyReplayed"] = True
    if "x-request-id" in upstream.headers:
        headers["requestId"] = upstream.headers["x-request-id"]

    content_type = upstream.headers.get("content-type", "")
    is_json = "json" in content_type
    if force_file and status < 400:
        is_json = False
    payload: Any = None
    if raw and is_json:
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = None
    ok = 200 <= status < 300 or status == 304
    result: dict[str, Any] = {"ok": ok, "status": _status(upstream), "data": None,
                              "error": None, "headers": headers}
    if not ok:
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, dict):
            error = {"code": "akademi_error", "message": f"Akademi isteği reddetti ({status}).",
                     "details": None, "traceId": headers.get("requestId")}
        result["error"] = {**error, "status": _status(upstream)}
        return result
    if is_json:
        result["data"] = payload
    elif raw:
        result["file"] = {
            "filename": _filename(upstream.headers.get("content-disposition"), "akademi-dosya"),
            "contentType": content_type or "application/octet-stream",
            "size": len(raw),
            "dataBase64": base64.b64encode(raw).decode("ascii"),
        }
    return result


def decode_upload(raw: Mapping[str, Any]) -> Upload:
    """Köprü gövdesindeki dosya (`dataBase64` ya da `data:` URI)."""
    text = str(raw.get("dataBase64") or "")
    if text.startswith("data:"):
        text = text.split(",", 1)[1] if "," in text else ""
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as failure:
        raise ProxyError(400, "upload_invalid", "Dosya okunamadı (base64 bozuk).") from failure
    if not data:
        raise ProxyError(400, "upload_invalid", "Dosya boş.")
    fields = {str(key): str(value) for key, value in (raw.get("fields") or {}).items()
              if value is not None}
    return Upload(field=str(raw.get("field") or "file"),
                  filename=str(raw.get("filename") or "dosya"),
                  content_type=str(raw.get("contentType") or "application/octet-stream"),
                  data=data, fields=fields)
