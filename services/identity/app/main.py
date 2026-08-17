"""Merkezî kimlik servisinin uçları (ADR 0021).

    GET  /health
    GET  /roster                      [kurulum]  → {revision, users, roles, grants}
    POST /roster/import               [yönetim]  → var olan kurulumun kadrosunu taşır
    POST /users                       [kurulum + kişi]
    PUT  /users/{id}                  [kurulum + kişi]
    POST /users/{id}/status           [kurulum + kişi]
    POST /audit                       [kurulum]
    POST /installations/pair-code     [yönetim]
    POST /pair                        [kodla korunur]
    GET  /installations               [yönetim]
    POST /installations/{id}/revoke   [yönetim]
    POST /locks                       [kurulum]
    DELETE /locks/{id}                [kurulum]

Kimlik mantığı burada YENİDEN YAZILMAZ: `km_core.security.identity.Identity`
olduğu gibi kullanılır. Bu dosya yalnızca HTTP kabuğudur — doğrulama, Argon2id,
benzersizlik ve "son yönetici" kısıtı çekirdekteki tek kopyadan gelir.

**MERKEZ KADROYA KARAR VERİR, GİRİŞE DEĞİL** (ADR 0021 — elenen alternatifler).
Burada `POST /auth/login` YOKTUR ve bilerek yoktur: giriş kurulumda, yerel
önbellekten yapılır. Merkez düştüğünde kimsenin uygulamaya girememesi kabul
edilemez.
"""

from __future__ import annotations

import hashlib
import secrets as pysecrets
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any, Literal

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from km_core.security.identity import (
    Identity,
    IdentityError,
    PasswordError,
    RevisionConflict,
    UserNotFound,
)
from km_core.security.migrations import apply_core_migrations
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store

from . import installations as inst
from . import locks, roster, roster_import
from .auth import Installation, require_actor, require_admin, require_installation
from .schema import apply_service_migrations
from .settings import Settings

log = structlog.get_logger("identity.http")


def pepper_fingerprint(pepper: str) -> str:
    """Pepper'ın karşılaştırılabilir ama geri döndürülemez özeti.

    Kurulum kendi anahtarının merkezinkiyle aynı olup olmadığını buradan
    anlar. Anahtarın kendisi DEĞİLDİR — 16 hex karakter, çakışması pratikte
    imkânsız ve tersine çevrilemez.
    """
    return hashlib.sha256(pepper.encode("utf-8")).hexdigest()[:16]

SERVICE_VERSION = "0.1.0"

OrgScope = Literal["bbd", "bld", "org"]
UserStatus = Literal["active", "disabled"]


class _Body(BaseModel):
    """JSON tarafı camelCase, kod tarafı snake_case — Kontrol Merkezi ile aynı."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class PairCodeRequest(_Body):
    note: str | None = Field(default=None, max_length=200)


class PairRequest(_Body):
    code: str = Field(min_length=4, max_length=32)
    public_key: str = Field(alias="publicKey", min_length=1, max_length=4000)
    machine_name: str = Field(alias="machineName", min_length=1, max_length=200)
    platform: str = Field(min_length=1, max_length=60)
    version: str = Field(min_length=1, max_length=40)


class UserCreateRequest(_Body):
    first_name: str = Field(alias="firstName", min_length=1, max_length=120)
    last_name: str = Field(alias="lastName", min_length=1, max_length=120)
    org_scope: OrgScope = Field(alias="orgScope")
    roles: list[str] = Field(min_length=1)
    password: str = Field(min_length=1, max_length=256)
    phone_mobile: str | None = Field(default=None, alias="phoneMobile", max_length=40)
    phone_ext: str | None = Field(default=None, alias="phoneExt", max_length=20)
    title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    directory_visible: bool = Field(default=True, alias="directoryVisible")


class UserUpdateRequest(_Body):
    first_name: str | None = Field(default=None, alias="firstName", min_length=1, max_length=120)
    last_name: str | None = Field(default=None, alias="lastName", min_length=1, max_length=120)
    org_scope: OrgScope | None = Field(default=None, alias="orgScope")
    phone_mobile: str | None = Field(default=None, alias="phoneMobile", max_length=40)
    phone_ext: str | None = Field(default=None, alias="phoneExt", max_length=20)
    title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    directory_visible: bool | None = Field(default=None, alias="directoryVisible")
    roles: list[str] | None = Field(default=None, min_length=1)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=1)


class UserStatusRequest(_Body):
    status: UserStatus


class AuditEntry(_Body):
    at: str = Field(min_length=1, max_length=40)
    action: str = Field(min_length=1, max_length=120)
    result: str = Field(min_length=1, max_length=40)
    user_id: str | None = Field(default=None, alias="userId", max_length=64)
    scope: str | None = Field(default=None, max_length=120)
    detail: str | None = Field(default=None, max_length=2000)


class AuditRequest(_Body):
    # Kurulum biriktirdiği kayıtları toptan iter (ADR 0021 §5). Üst sınır,
    # düşmüş bir kurulumun günlerce biriktirdiği yığını tek istekte
    # göndermesini engeller; kalanı bir sonraki turda gelir.
    entries: list[AuditEntry] = Field(min_length=1, max_length=500)


class LockRequest(_Body):
    resource: str = Field(min_length=1, max_length=200)
    holder_name: str = Field(alias="holderName", min_length=1, max_length=200)
    ttl_seconds: int | None = Field(default=None, alias="ttlSeconds", ge=1, le=3600)


@contextmanager
def _translated_errors() -> Iterator[None]:
    """Kimlik hatalarını tekdüze HTTP zarfına çevirir — Kontrol Merkezi ile
    AYNI eşleme (`km_core/http/users.py`). 409 ayrı tutulur: iyimser kilit
    çakışması geçersiz veri değildir (ADR 0020)."""
    try:
        yield
    except UserNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RevisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IdentityError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(Path(settings.db_path))
        await store.open()
        # GÖÇ AÇILIŞTA KOŞAR. Kaptaki tek yol budur: elle `migrate` çalıştıran
        # bir adım, dağıtımdan sonra unutulduğu gün servisi yarı şemayla
        # bırakır.
        await apply_core_migrations(store)
        await apply_service_migrations(store)

        identity = Identity(
            store,
            pepper=settings.pepper,
            pin_min_length=settings.pin_min_length,
            max_failed_attempts=settings.max_failed_attempts,
            lockout_minutes=settings.lockout_minutes,
        )
        await identity.ensure_builtin_roles()
        await identity.grant_defaults(CORE_PERMISSIONS)
        await _bootstrap_admin(identity, settings)

        app.state.settings = settings
        app.state.store = store
        app.state.identity = identity
        log.info("kimlik servisi hazır", db=settings.db_path)
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(
        title="Kontrol Merkezi — Kimlik Servisi",
        version=SERVICE_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"status": exc.status_code, "message": detail}},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        alanlar = [
            ".".join(str(adim) for adim in hata.get("loc", ()) if str(adim) != "body")
            or "gövde"
            for hata in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "status": 422,
                    "message": f"Gönderilen veri geçersiz: {', '.join(alanlar)}.",
                }
            },
        )

    # -------------------------------------------------------------- sağlık

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        store: Store = request.app.state.store
        return {
            "status": "ok",
            "version": SERVICE_VERSION,
            "rosterRevision": await roster.revision(store),
        }

    # --------------------------------------------------------------- kadro

    @app.get("/roster")
    async def get_roster(
        request: Request,
        known_revision: int | None = None,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        """Kadro. `known_revision` gönderilir ve değişmemişse VERİ ÇEKİLMEZ
        (ADR 0021 §2) — yanıt yalnız "değişmedi" der."""
        store: Store = request.app.state.store
        current = await roster.revision(store)
        # PEPPER PARMAK İZİ HER YANITTA GİDER — "değişmedi" yanıtında da.
        #
        # Kurulumun pepper'ı merkezinkiyle uyuşmuyorsa çekilen kadro hiçbir
        # PIN'le eşleşmez ve BELİRTİ SEBEBİ ELE VERMEZ: kullanıcı yalnız "PIN
        # yanlış" görür. Parmak izi, kurulumun bu durumu KENDİ BAŞINA fark
        # edip söyleyebilmesi içindir.
        #
        # Parmak izidir, pepper'ın kendisi DEĞİL: karşılaştırmaya yeter,
        # anahtarı ele vermez. Pepper yalnız `/pair` yanıtında, tek kullanımlık
        # kodu doğru bilene bir kez gider.
        izi = pepper_fingerprint(request.app.state.settings.pepper)
        if known_revision is not None and int(known_revision) == current:
            return {"revision": current, "changed": False, "pepperFingerprint": izi}
        payload = await roster.build(store)
        log.info("kadro gönderildi", installation=installation.id, revision=current)
        return {**payload, "changed": True, "pepperFingerprint": izi}

    # ---------------------------------------------------------- kullanıcılar
    #
    # YAZMA YALNIZ ÇEVRİMİÇİDİR (ADR 0021 §3): kurulum bunları yerelde
    # yazmaz, buraya gönderir. İki kapı birden geçilir — makine (kurulum
    # token'ı) ve kişi (`X-KM-Actor-Id` + izin).

    @app.post("/users", status_code=201)
    async def create_user(
        request: Request,
        body: UserCreateRequest,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        identity: Identity = request.app.state.identity
        actor = await require_actor(request, "users.manage")
        await require_actor(request, "users.set_password")
        with _translated_errors():
            user_id = await identity.create_user(
                first_name=body.first_name,
                last_name=body.last_name,
                org_scope=body.org_scope,
                password=body.password,
                roles=body.roles,
                created_by=actor.id,
                phone_mobile=body.phone_mobile,
                phone_ext=body.phone_ext,
                title=body.title,
                department=body.department,
                email=body.email,
                note=body.note,
                directory_visible=body.directory_visible,
            )
        revision = await roster.bump_revision(request.app.state.store)
        log.info("kullanıcı açıldı", installation=installation.id, revision=revision)
        row = await identity.get_user(user_id)
        return {"revision": revision, "user": roster.user_entry(row, row.get("roles") or [])}

    @app.put("/users/{user_id}")
    async def update_user(
        request: Request,
        user_id: str,
        body: UserUpdateRequest,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        identity: Identity = request.app.state.identity
        actor = await require_actor(request, "users.manage")
        fields = body.model_dump(exclude_unset=True, by_alias=False)
        fields.pop("expected_revision", None)
        roles = fields.pop("roles", None)
        with _translated_errors():
            row = await identity.update_user(
                user_id,
                fields=fields,
                roles=roles,
                expected_revision=body.expected_revision,
                actor_id=actor.id,
            )
        revision = await roster.bump_revision(request.app.state.store)
        log.info("kullanıcı güncellendi", installation=installation.id, revision=revision)
        return {"revision": revision, "user": roster.user_entry(row, row.get("roles") or [])}

    @app.post("/users/{user_id}/status")
    async def set_user_status(
        request: Request,
        user_id: str,
        body: UserStatusRequest,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        identity: Identity = request.app.state.identity
        actor = await require_actor(request, "users.manage")
        with _translated_errors():
            row = await identity.set_status(user_id, body.status, actor_id=actor.id)
        revision = await roster.bump_revision(request.app.state.store)
        log.info("kullanıcı durumu değişti", installation=installation.id, revision=revision)
        return {"revision": revision, "user": roster.user_entry(row, row.get("roles") or [])}

    # ------------------------------------------------------------- denetim

    @app.post("/audit")
    async def push_audit(
        request: Request,
        body: AuditRequest,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        """Kurulumlardan gelen denetim kayıtları (ADR 0021 §5).

        Kayıtlar OLDUĞU GİBİ alınır; kurulumun yazdığı zaman damgası korunur —
        merkeze ulaştığı an değil, olayın olduğu an sorulur. `installation_id`
        merkezde eklenir: kurulumun kendi kimliğini bildirmesine güvenilmez.
        """
        store: Store = request.app.state.store
        await store.execute_many(
            "INSERT INTO audit_log (at, user_id, action, scope, result, detail, installation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (item.at, item.user_id, item.action, item.scope, item.result, item.detail,
                 installation.id)
                for item in body.entries
            ],
        )
        return {"accepted": len(body.entries)}

    # ----------------------------------------------------------- kurulumlar

    @app.post("/installations/pair-code", dependencies=[Depends(require_admin)])
    async def pair_code(request: Request, body: PairCodeRequest) -> dict[str, Any]:
        store: Store = request.app.state.store
        return await inst.create_pair_code(
            store,
            ttl_seconds=request.app.state.settings.pair_code_ttl_seconds,
            note=body.note,
        )

    @app.post("/pair")
    async def do_pair(request: Request, body: PairRequest) -> dict[str, Any]:
        """Eşleme. İZİN İSTEMEZ ve bu K9'a aykırı değildir: uç, oturumun değil
        TEK KULLANIMLIK KODUN doğrulanmasıyla korunur — eşleşmemiş bir makinenin
        zaten hiçbir kimliği yoktur.

        **Dönen token kullanıcı oturumu değildir**: "bu makine bizim" der, o
        kadar. Kişi yine kurulumda kendi PIN'ini girer (ADR 0021 §4).
        """
        store: Store = request.app.state.store
        try:
            result = await inst.pair(
                store,
                code=body.code,
                public_key=body.public_key,
                machine_name=body.machine_name,
                platform=body.platform,
                version=body.version,
            )
        except inst.PairError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        log.info("kurulum eşlendi", installation=result["installationId"],
                 machine=body.machine_name)
        # PEPPER EŞLEME YANITIYLA GİDER — ve bu, kadro çekmenin ÖN KOŞULUDUR.
        #
        # `secret_lookup` sabit anahtarlı bir HMAC'tir. Kurulumun kasasındaki
        # `core.pin_pepper` merkezinkiyle aynı olmazsa çekilen kadro hiçbir
        # PIN'le eşleşmez — kullanıcı tabloda görünür, girişi reddedilir ve
        # BELİRTİ SEBEBİ HİÇ ELE VERMEZ. `roster_projection.py` bu şartı
        # yazıyordu ama onu sağlayacak hiçbir yol yoktu: taze kurulum kendi
        # rastgele pepper'ını üretiyor ve eşleştikten sonra bile kimse
        # giremiyordu (17.08.2026, ilk macOS kurulumu).
        #
        # KANAL YETERİNCE DAR: HTTPS üzerinden, tek kullanımlık ve süreli bir
        # kodu doğru bilene, bir kez. Aynı yanıt zaten kadronun TAMAMINI —
        # Argon2 parola hash'leri dahil — çekebilen token'ı veriyor; pepper
        # ondan daha az hassastır. Ayrı bir uç açmak, aynı sırrı ikinci bir
        # kapıdan da sunmak olurdu.
        return {**result, "pepper": request.app.state.settings.pepper}

    @app.get("/installations", dependencies=[Depends(require_admin)])
    async def list_installations(request: Request) -> dict[str, Any]:
        return {"installations": await inst.listing(request.app.state.store)}

    @app.post("/installations/{installation_id}/revoke",
              dependencies=[Depends(require_admin)])
    async def revoke_installation(request: Request, installation_id: str) -> dict[str, Any]:
        row = await inst.revoke(request.app.state.store, installation_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Kurulum bulunamadı.")
        log.warning("kurulum iptal edildi", installation=installation_id)
        return {"installation": row}

    # -------------------------------------------------------------- kilitler

    @app.post("/locks")
    async def acquire_lock(
        request: Request,
        body: LockRequest,
        installation: Installation = Depends(require_installation),
    ) -> JSONResponse:
        settings_now: Settings = request.app.state.settings
        row, acquired = await locks.acquire(
            request.app.state.store,
            resource=body.resource,
            installation_id=installation.id,
            holder_name=body.holder_name,
            ttl_seconds=body.ttl_seconds or settings_now.lock_default_ttl_seconds,
            max_ttl_seconds=settings_now.lock_max_ttl_seconds,
        )
        # ALINAMAYAN KİLİT HATA DEĞİLDİR (ADR 0020 §2): kilit engellemez,
        # uyarır. 200 döner ve kimin tuttuğunu söyler; 409 dönmek, ekranı
        # "yazamazsın" sanmaya iterdi.
        return JSONResponse(status_code=200, content={"acquired": acquired, "lock": row})

    @app.delete("/locks/{lock_id}")
    async def release_lock(
        request: Request,
        lock_id: str,
        installation: Installation = Depends(require_installation),
    ) -> dict[str, Any]:
        released = await locks.release(
            request.app.state.store, lock_id, installation_id=installation.id
        )
        return {"released": released}

    # --------------------------------------------------------- kadro göçü
    #
    # Tek satırlık kayıt; mantık `roster_import.py` içindedir. Göç ucu buraya
    # yazılmadı çünkü bu dosya kadronun GÜNLÜK sözleşmesidir, tek seferlik bir
    # taşımanın kuralları onun içine karışmamalı.
    app.include_router(roster_import.router)

    return app


async def _bootstrap_admin(identity: Identity, settings: Settings) -> None:
    """İlk açılışta yönetici hesabı.

    PIN `KM_IDENTITY_BOOTSTRAP_PIN` ile verilir (K8 — depoya yazılmaz). Yoksa
    rastgele üretilir ve YALNIZCA bir kez loga düşer. Merkezde hiç kullanıcı
    yoksa hiçbir yazma isteği geçemezdi: `X-KM-Actor-Id` sahibi olmayan bir
    sistemde ilk kullanıcıyı kimse açamaz.
    """
    if await identity.user_count() > 0:
        return

    pin = settings.bootstrap_pin
    generated = False
    if not pin:
        while True:
            pin = "".join(pysecrets.choice("0123456789") for _ in range(6))
            try:
                identity.validate_pin(pin)
                break
            except PasswordError:
                continue
        generated = True

    try:
        await identity.create_user(
            first_name="Sistem",
            last_name="Yöneticisi",
            org_scope="org",
            password=pin,
            roles=["admin"],
        )
    except PasswordError as error:
        log.error("bootstrap PIN reddedildi", detail=str(error))
        return

    if generated:
        log.warning("İLK YÖNETİCİ OLUŞTURULDU — girip PIN'inizi değiştirin", pin=pin)
    else:
        log.info("İlk yönetici oluşturuldu (PIN ortam değişkeninden alındı).")
