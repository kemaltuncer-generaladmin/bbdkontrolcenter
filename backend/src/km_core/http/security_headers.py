"""Güvenlik başlıkları — her yanıta (iş 63, bulgu KM-08; OWASP ASVS 14.4).

ADR 0026'dan beri backend internete bakıyor (Traefik + Cloudflare arkasında). Kabuk
(Tauri) yanıtı tarayıcı gibi yorumlamaz ama aynı adres bir tarayıcıda da açılabilir;
başlıklar o yolu kapatır:

  · `X-Content-Type-Options: nosniff` — JSON/indirme gövdesi betik ya da HTML diye
    koklanmaz,
  · `X-Frame-Options: DENY` + `Content-Security-Policy: frame-ancestors 'none'` — yanıt
    başka bir sayfaya çerçevelenemez (tıklama kaçırma),
  · `Referrer-Policy: no-referrer` — belirteç taşıyan bir indirme bağlantısı başka siteye
    sızmaz,
  · kimlik uçlarında `Cache-Control: no-store` — oturum belirteci hiçbir ara önbellekte
    (Cloudflare, vekil) durmaz.

Uç kendi değerini yazdıysa (ör. PDF indirmede `Content-Security-Policy`) EZİLMEZ.
`Strict-Transport-Security` Cloudflare/Traefik'te verilir: uygulama TLS'i görmez.

Saf ASGI ara katmanıdır (BaseHTTPMiddleware değil): akış yanıtlarını (vekil indirme,
uzun yoklama) belleğe almaz, yalnız başlık satırına dokunur.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

DEFAULT_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
)

#: Gövdesinde oturum belirteci dönen / kimlik değiştiren yollar.
NO_STORE_PREFIXES: tuple[str, ...] = ("/api/auth/", "/auth/", "/api/pairing/")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        no_store = str(scope.get("path", "")).startswith(NO_STORE_PREFIXES)

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                present = {name.lower() for name, _ in headers}
                headers += [(name, value) for name, value in DEFAULT_HEADERS if name not in present]
                if no_store and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
