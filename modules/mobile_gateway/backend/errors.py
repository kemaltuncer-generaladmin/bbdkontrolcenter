"""Sözleşmenin hata zarfı ve onu KENDİSİ basan rota sınıfı.

Sözleşme (km-mobile-gateway.md → "Ortak kurallar"):

    {"error": {"status": 404, "code": "student_not_found", "message": "…"}}

`code` makine içindir ve kararlıdır, `message` Türkçedir. Gerekirse zarf ek
alan taşır (`retryAfterSeconds`, `fields`, `capability`, `reason`).

NEDEN AYRI ROTA SINIFI. Çekirdeğin `HTTPException` işleyicisi metin olmayan
`detail`i "İstek reddedildi." ile değiştirir ve zarfa `code` koymaz. Bu yüzden
kodlu hata `HTTPException` ile taşınamaz. `GatewayRoute` ucun işleyicisini
sarar: geçidin kendi hatası, FastAPI'nin gövde doğrulaması (422) ve çekirdeğin
izin kapısından gelen 401/403 — hepsi aynı zarfa, kodlu olarak girer. İzin
kapısı (`http.requires`) router'a monte anında bağımlılık olarak eklenir ve bu
sınıfın işleyicisinin İÇİNDE çözülür; dolayısıyla 401/403 de buradan geçer.
Çekirdeğe tek satır eklenmez (K6).

GİRDİ ECHO EDİLMEZ. Pydantic hata kaydı gönderilen değeri (`input`) taşır;
şifre, OTP ve telefon yanıtta geri dönmesin diye mesaj yalnız alan adından
kurulur.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException as StarletteHTTPException

#: HTTP durumundan kod — yalnız geçidin kendi kodunu taşımayan hatalar için.
STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    423: "locked",
    429: "rate_limited",
    502: "upstream_error",
    503: "service_unavailable",
}

DEFAULT_MESSAGES: dict[int, str] = {
    401: "Kimlik doğrulanamadı.",
    403: "Bu işlem için yetkiniz yok.",
    404: "Kayıt bulunamadı.",
    422: "Gönderilen veri geçersiz.",
}


class GatewayError(Exception):
    """Sözleşmedeki kodlu hata. Yanıt zarfını kendisi üretir."""

    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = {key: value for key, value in extra.items() if value is not None}

    def body(self) -> dict[str, Any]:
        return envelope(self.status, self.code, self.message, **self.extra)

    def response(self) -> JSONResponse:
        headers: dict[str, str] = {}
        retry = self.extra.get("retryAfterSeconds")
        if isinstance(retry, int):
            headers["Retry-After"] = str(retry)
        return JSONResponse(status_code=self.status, content=self.body(), headers=headers)


def envelope(status: int, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": {"status": status, "code": code, "message": message, **extra}}


# --------------------------------------------------------- sık kullanılanlar


def validation(message: str, *fields: str) -> GatewayError:
    return GatewayError(422, "validation_error", message,
                        fields=list(fields) if fields else None)


def student_not_found() -> GatewayError:
    return GatewayError(404, "student_not_found", "Öğrenci bulunamadı.")


def canteen_unavailable() -> GatewayError:
    return GatewayError(503, "canteen_unavailable",
                        "Kantine şu anda ulaşılamıyor. Biraz sonra yeniden deneyin.")


def upstream_error(message: str = "Bağlı sistem beklenmedik bir yanıt verdi.",
                   **extra: Any) -> GatewayError:
    return GatewayError(502, "upstream_error", message, **extra)


def capability_missing(capability: str) -> GatewayError:
    return GatewayError(503, "capability_missing",
                        "Bu özellik Kontrol Merkezi'nde şu anda kullanılamıyor.",
                        capability=capability)


def rate_limited(retry_after: int, message: str = "Çok fazla istek. Biraz sonra deneyin.") \
        -> GatewayError:
    return GatewayError(429, "rate_limited", message, retryAfterSeconds=max(1, retry_after))


def locked(retry_after: int) -> GatewayError:
    return GatewayError(423, "locked",
                        "Çok fazla hatalı deneme yapıldı. Bir süre sonra yeniden deneyin.",
                        retryAfterSeconds=max(1, retry_after))


# ------------------------------------------------------------ çevirmenler


def validation_response(error: RequestValidationError) -> JSONResponse:
    fields: list[str] = []
    for item in error.errors():
        location = [str(step) for step in item.get("loc", ())
                    if str(step) not in ("body", "query", "path", "header")]
        name = ".".join(location) or "gövde"
        if name not in fields:
            fields.append(name)
    listed = ", ".join(f"“{name}”" for name in fields)
    message = f"Geçersiz alan: {listed}." if fields else DEFAULT_MESSAGES[422]
    return GatewayError(422, "validation_error", message, fields=fields).response()


def http_response(error: StarletteHTTPException) -> JSONResponse:
    status = int(error.status_code)
    detail = error.detail if isinstance(error.detail, str) and error.detail else None
    message = detail or DEFAULT_MESSAGES.get(status, "İstek reddedildi.")
    body = envelope(status, STATUS_CODES.get(status, "error"), message)
    return JSONResponse(status_code=status, content=body,
                        headers=dict(error.headers or {}))


class GatewayRoute(APIRoute):
    """Ucun bütün hatalarını sözleşme zarfına çeviren rota."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except GatewayError as error:
                return error.response()
            except RequestValidationError as error:
                return validation_response(error)
            except StarletteHTTPException as error:
                return http_response(error)

        return handler
