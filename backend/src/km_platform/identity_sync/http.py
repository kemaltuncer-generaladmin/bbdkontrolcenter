"""Eşleme ekranının konuştuğu iki uç.

    GET  /api/pairing/state          → {enabled, paired, pairingRequired, …}
    POST /api/pairing/pair  {code}   → {paired: true}

**İKİSİ DE OTURUM İSTEMEZ ve bu K9'a aykırı değildir.** Eşleme, girişten
ÖNCE gelir: eşleşmemiş bir kurulumda henüz kadro yoktur, dolayısıyla oturum
açabilecek bir kullanıcı da yoktur. Uç, oturumun değil **tek kullanımlık
kodun** doğrulanmasıyla korunur — `POST /api/auth/set-password` ile aynı
gerekçe (`km_core/http/users.py`).

Kodu doğrulayan taraf merkezdir. Buradaki sayaç yalnız **deneme hızını**
keser: sidecar 127.0.0.1'e bağlı olsa da, makinede çalışan başka bir sürecin
sekiz haneli kodu sınırsız denemesine gerek yok.

`state` SIR DÖNDÜRMEZ: token da, kadro alanları da yok.
"""

from __future__ import annotations

from time import monotonic
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .errors import IdentitySyncError, PairRejected
from .service import IdentitySync

# Ardışık başarısız deneme sınırı ve bekleme. Kimlikteki `max_failed_attempts`
# / `lockout_minutes` ile aynı fikir, çok daha kısa ömürlü.
MAX_ATTEMPTS = 5
COOLDOWN_SECONDS = 60.0


class PairBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Sınır BİLEREK gevşek: kodun biçimini uçta ele vermeyiz, kararı merkez
    # verir ve reddi tek tiptir.
    code: str = Field(min_length=4, max_length=32)


class _Throttle:
    """Süreç ömrü boyunca yaşayan basit sayaç. Kalıcı değildir ve olmamalıdır:
    yeniden başlatmayı bekleyen biri zaten makinenin başındadır."""

    def __init__(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0

    def blocked(self) -> bool:
        return monotonic() < self.blocked_until

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= MAX_ATTEMPTS:
            self.failures = 0
            self.blocked_until = monotonic() + COOLDOWN_SECONDS

    def reset(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0


def _sync(request: Request) -> IdentitySync:
    service: IdentitySync | None = getattr(request.app.state, "identity_sync", None)
    if service is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik senkronu hazır değil.")
    # Denetim kuyruğunun deposu (ADR 0021 §5) burada bağlanır: yetenek `Vault`
    # ve `Config` ile kurulur, çekirdek deposunu kurucuda görmez. Çağrı
    # etkisizdir (ikinci kez bağlamaz) ve eşleme akışının kendisi de denetim
    # kaydı üretebildiği için EN ERKEN nokta burasıdır.
    store = getattr(request.app.state, "store", None)
    if store is not None:
        service.attach_store(store)
    return service


def create_pairing_router() -> APIRouter:
    router = APIRouter(tags=["pairing"])
    throttle = _Throttle()

    @router.get("/pairing/state")
    async def state(request: Request) -> dict[str, Any]:
        return await _sync(request).state()

    @router.post("/pairing/pair")
    async def pair(request: Request, body: PairBody) -> dict[str, Any]:
        service = _sync(request)
        if throttle.blocked():
            raise HTTPException(
                status_code=429,
                detail="Çok fazla deneme yapıldı. Bir dakika sonra tekrar deneyin.",
            )
        try:
            result = await service.pair(body.code)
        except PairRejected as error:
            throttle.fail()
            raise HTTPException(status_code=401, detail=str(error)) from error
        except IdentitySyncError as error:
            # ULAŞILAMAMA SAYACA YAZILMAZ: merkez kapalıyken yapılan deneme,
            # kodun yanlış olduğunu göstermez ve kullanıcıyı cezalandırmamalı.
            raise HTTPException(status_code=503, detail=str(error)) from error
        throttle.reset()
        return result

    return router
