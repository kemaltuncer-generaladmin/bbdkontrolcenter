"""Uç nokta düzeyinde izin kapısı ve yıkıcı işlemin PIN teyidi.

Router'ın tamamı `module.yaml` → `http.requires` ile taban izne bağlanır; tek
tek uç noktalar bunu DARALTIR (genişletemez). Modüller bunu `km_sdk.requires`
üzerinden kullanır — çekirdeği import etmezler (K2).

    @router.post("/students")
    async def create(user: CurrentUser = requires("bbd_students.manage")):
        ...

Geri dönüşü olmayan işlemler ayrıca `confirm_pin` çağırır (CLAUDE.md: "Yıkıcı
işlemler izin yeterli olsa bile PIN teyidi ister"). Bu kapı bugüne kadar YALNIZ
çekirdeğin eşleme ucunda vardı (`km_platform/identity_sync/http.py`) ve modüllere
kapalıydı; `store_customers/module.yaml` bunu açıkça bekleyen bir not düşmüştü.
Üç ayrı yerde aynı doğrulamayı elle yazmak yerine tek yerde durur.

**İKİ TÜR BELİRTEÇ (ADR 0028).** `Authorization: Bearer` ya bir kişinin oturum
belirtecidir ya da `kmsk_` ile başlayan bir servis anahtarı. İkisi de AYNI
`CurrentUser`a çözülür; izin denetimi ve denetim izi aynı yoldan geçer. Servis
anahtarı ayrıca:

  · istemci adresini güvenilir vekil zincirinden okur (`client_address`,
    ADR 0030: Traefik + Cloudflare arkasında `CF-Connecting-IP`; başlıklar
    yalnız güvenilir vekilden gelindiğinde okunur, uydurulamaz),
  · kendi hız sınırı kovasında sayılır (PIN girişinin kovasıyla karışmaz),
  · `X-KM-On-Behalf-Of` başlığını denetim izine taşır. Başlık YETKİ VERMEZ ve
    kişi oturumunda yok sayılır.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any, cast

from fastapi import Depends, Header, HTTPException, Request

from km_core.files import outputs_log
from km_core.http.client_address import client_address
from km_core.security import audit_context
from km_core.security.identity import SERVICE_TOKEN_MARK, CurrentUser, Identity
from km_core.security.rate_limit import RateLimiter
from km_core.security.service_keys import ServiceKeys

#: Servis anahtarı başına dakikadaki istek sınırı. `auth.service_key_requests_per_minute`
#: ile ezilir. Kişi girişinin kovasından AYRIDIR (ADR 0028).
SERVICE_REQUESTS_PER_MINUTE = 1200

#: Geçersiz servis anahtarı denemesi — kaynak (IP) başına, dakikada.
SERVICE_FAILURES_PER_MINUTE = 10


def split_permission(entry: str) -> tuple[str, str | None]:
    key, _, scope = entry.partition(":")
    return key, (scope or None)


async def resolve_once(request: Request, identity: Identity, token: str) -> CurrentUser | None:
    """Oturum AYNI İSTEKTE bir kez çözülür.

    Modül uçları iki kapıdan geçer: router'ın taban izni (`app.py` →
    `current_user`) ve ucun kendi `requires(...)`ı. İkisi de oturumu ayrı ayrı
    çözüyordu — istek başına iki kez oturum+kullanıcı, rol ve izin sorgusu.
    Sonuç istek ömrü boyunca `request.state` üzerinde tutulur; belirteç
    değişmediği sürece ikinci kapı aynı kullanıcıyı görür. İstek bitince
    kaybolur — istekler arası önbellek DEĞİLDİR, izin değişikliği bir sonraki
    istekte geçerlidir.
    """
    cached = getattr(request.state, "km_session", None)
    if cached is not None and cached[0] == token:
        return cast("CurrentUser | None", cached[1])
    if token.startswith(SERVICE_TOKEN_MARK):
        user = await _resolve_service_key(request, identity, token)
    else:
        user = await identity.resolve_session(token) if token else None
    request.state.km_session = (token, user)
    return user


def bearer_token(authorization: str | None) -> str:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def client_ip(request: Request) -> str:
    """İsteğin geldiği GERÇEK adres — servis anahtarının ağ kısıtı ve hız sınırları.

    ADR 0030 (ADR 0028 §4'ü değiştirir): başlıklara yalnız güvenilir vekilden
    (yerel Traefik ya da Cloudflare) gelindiğinde bakılır; Cloudflare durağında
    `CF-Connecting-IP`, değilse `X-Forwarded-For` sağdan sola ilk güvenilmeyen
    durak. Ayrıntı ve sahteleme gerekçesi: `client_address.py`.
    """
    return client_address(request)


def _state_limiter(request: Request, name: str, **limits: Any) -> RateLimiter:
    """Uygulama başına tek sınırlayıcı (bkz. `users._limiter` notu)."""
    limiter: RateLimiter | None = getattr(request.app.state, name, None)
    if limiter is None:
        limiter = RateLimiter(**limits)
        setattr(request.app.state, name, limiter)
    return limiter


def _service_keys(request: Request, identity: Identity) -> ServiceKeys:
    keys: ServiceKeys | None = getattr(request.app.state, "service_keys", None)
    if keys is None or keys.identity is not identity:
        keys = ServiceKeys(identity)
        request.app.state.service_keys = keys
    return keys


def _config_int(request: Request, path: str, default: int) -> int:
    config = getattr(request.app.state, "config", None)
    try:
        return int(config.get(path, default)) if config is not None else default
    except (TypeError, ValueError):
        return default


def _too_many(wait: float) -> HTTPException:
    seconds = int(wait) + 1
    return HTTPException(
        status_code=429,
        detail=f"Çok fazla istek. {seconds} saniye sonra deneyin.",
        headers={"Retry-After": str(seconds)},
    )


async def _resolve_service_key(request: Request, identity: Identity,
                               token: str) -> CurrentUser | None:
    """Servis anahtarını çözer (ADR 0028). Geçersizse `None` → 401.

    Sıra: önce kaynağın başarısız deneme sayacına bakılır (kaba kuvvet
    denemesi doğrulamaya ulaşmasın), sonra anahtar doğrulanır, en son
    anahtarın kendi istek kovası sayılır.
    """
    source = client_ip(request) or "bilinmeyen"
    # Genel sınır BİLEREK fiilen kapalı: başka adreslerden gelen çöp
    # denemeler, geçerli anahtarı taşıyan istemciyi kilitlememeli. 256 bitlik
    # sır kaba kuvvetle bulunamaz; bu sayaç yalnız denetim izini ve depoyu
    # aynı kaynağın yağmurundan korur.
    failures = _state_limiter(
        request, "service_key_failures",
        source_limit=SERVICE_FAILURES_PER_MINUTE, source_window=60.0,
        global_limit=1_000_000, global_window=60.0,
    )
    wait = failures.peek(source)
    if wait is not None:
        raise _too_many(wait)

    verdict = await _service_keys(request, identity).verify(token, client_ip=source)
    if verdict.user is None:
        await identity.audit(verdict.user_id, "auth.service_key", result="fail",
                             detail=f"{verdict.prefix or '-'}: {verdict.reason}")
        wait = failures.check(source)
        if wait is not None:
            raise _too_many(wait)
        return None

    per_minute = _config_int(request, "auth.service_key_requests_per_minute",
                             SERVICE_REQUESTS_PER_MINUTE)
    requests_bucket = _state_limiter(
        request, "service_key_requests",
        source_limit=max(1, per_minute), source_window=60.0,
        global_limit=max(1, per_minute) * 4, global_window=60.0,
    )
    wait = requests_bucket.check(verdict.prefix or verdict.user.id)
    if wait is not None:
        raise _too_many(wait)

    user = verdict.user
    user.on_behalf_of = audit_context.clean_header(
        request.headers.get("x-km-on-behalf-of"), audit_context.ON_BEHALF_OF_MAX
    )
    return user


@contextmanager
def request_context(request: Request, user: CurrentUser) -> Iterator[None]:
    """İstek boyunca çıktı kaydının sahibi ve denetim bağlamı.

    `on_behalf_of` YALNIZ servis sorumlusunda taşınır; kişi oturumunda
    "kimin adına" sorusunun cevabı oturumun sahibidir. `X-Request-Id` iki
    türde de denetim izine geçer. İki değer de istek bitince geri alınır —
    sızarsa bir sonraki istek yanlış kişiye yazılır.
    """
    on_behalf_of = user.on_behalf_of if user.is_service else None
    request_id = audit_context.clean_header(
        request.headers.get("x-request-id"), audit_context.REQUEST_ID_MAX
    )
    with outputs_log.use_actor(user.id), audit_context.use(on_behalf_of, request_id):
        yield


def requires(*permissions: str) -> Any:
    """Verilen izinlerden EN AZ BİRİNE sahip kullanıcıyı döndürür.

    İki izni birden ARAYAN uç, kapıyı iki kez kurar: biri `dependencies=[...]`
    listesinde, biri argümanda. Her ikisi de bağımsız çalışır, ikisi de
    geçilmeden gövdeye girilmez.
    """
    if not permissions:
        raise ValueError("İzin belirtilmeyen uç nokta tanımlanamaz (K9).")

    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> AsyncIterator[CurrentUser]:
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

        token = bearer_token(authorization)
        user = await resolve_once(request, identity, token)
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")

        # Çıktı kaydına üreten kişiyi tanıtır (ADR 0019 §2); denetim izine
        # "kimin adına" ve istek kimliğini taşır (ADR 0028). Kapı burada da
        # kurulur çünkü çekirdeğin kendi router'ları (`settings`, `users`)
        # `app.py` içindeki `current_user`dan GEÇMEZ — destek paketi gibi
        # oradan doğan çıktılar da sahipsiz kalmasın. Değer istek bitince
        # geri alınır; sızarsa bir sonraki istek yanlış kişiye yazılır.
        with request_context(request, user):
            if not any(user.has_permission(*split_permission(entry)) for entry in permissions):
                await identity.audit(
                    user.id, "permission.denied", result="denied", detail=",".join(permissions)
                )
                raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")
            yield user

    return Depends(dependency)


async def confirm_pin(request: Request, actor: CurrentUser, pin: str,
                      *, action: str) -> None:
    """YIKICI İŞLEMİN İKİNCİ KAPISI: izin yeterli olsa bile PIN sorulur.

    Doğrulama, kişinin KENDİ satırındaki `secret_lookup` ile yapılır —
    pepper'lı bir HMAC'tir ve pepper kasadadır (K8). Bu, oturumu açık olan
    kişinin PIN'i BİLDİĞİNİ kanıtlar; aradığımız tam olarak budur. Oturum
    çalınmışsa ya da makine başında bırakılmışsa kapı burada kapanır.

    `Identity.login()` KULLANILMAZ, bilerek: o yol ikinci bir oturum açar ve
    denetim izine "giriş yapıldı" satırı düşürürdü. Teyit bir giriş değildir.

    Karşılaştırma sabit sürelidir. Ret 403'tür, 401 DEĞİL: oturum geçerli,
    reddedilen şey işlemdir — 401 kabuğu kullanıcıyı giriş ekranına atmaya
    iterdi ve kullanıcı yaptığı işi kaybederdi.

    `action` denetim kaydına yazılır: hangi yıkıcı işlemde PIN'in yanlış
    girildiği, izde okunabilmeli.
    """
    identity = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

    row = await identity.store.fetch_one(
        "SELECT secret_lookup FROM users WHERE id = ?", (actor.id,)
    )
    stored = str((row or {}).get("secret_lookup") or "")
    if not stored or not hmac.compare_digest(stored, identity.secret_lookup(pin)):
        await identity.audit(actor.id, action, result="denied",
                             detail="PIN teyidi başarısız")
        raise HTTPException(status_code=403, detail="PIN doğrulanamadı.")
