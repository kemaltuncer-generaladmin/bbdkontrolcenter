"""FastAPI uygulama fabrikası: modül router'larını monte eder, tek biçim hata
döndürür ve her isteği izin denetiminden geçirir.

K9 — Çift kapı: arayüzde gizlemek yetkilendirme değildir. Modül router'ı
`module.yaml` → `http.requires` ile taban iznini ilan eder. **İzin ilan
etmeyen router monte EDİLMEZ** (varsayılan: kapalı).
"""

from __future__ import annotations

import secrets as pysecrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from km_core.bus.bus import EventBus
from km_core.config.loader import Config, load_config
from km_core.kernel.kernel import Kernel
from km_core.registry.registry import Registry
from km_core.security.identity import CurrentUser, Identity, PinError
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.db import Store
from km_platform.audio.player import AudioPlayer
from km_platform.notify.service import NotifyService
from km_platform.printer.cups import PrinterService
from km_platform.scheduler.weekly import WeeklyScheduler
from km_platform.secrets.vault import Vault

log = structlog.get_logger("km.http")


class LoginRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=32)


# Çekirdeğin kendi ekranları. Kimlik, ayar ve sistem sağlığı modül DEĞİLDİR
# (CLAUDE.md — kavram ayrımı); silinemezler, manifestleri yoktur.
CORE_PANELS_UI: list[dict[str, Any]] = [
    {
        "id": "core_users", "name": "Kullanıcı Yönetimi", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Kullanıcı Yönetimi", "icon": "users", "group": "Kurumsal",
                       "order": 10, "requires": ["users.view"]}},
    },
    {
        "id": "core_settings", "name": "Sistem Ayarları", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Sistem Ayarları", "icon": "sliders", "group": "Kurumsal",
                       "order": 20, "requires": ["settings.view"]}},
    },
    {
        "id": "core_health", "name": "Sistem Sağlığı", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Sistem Sağlığı", "icon": "pulse", "group": "Kurumsal",
                       "order": 30, "requires": ["settings.view"]}},
    },
]


def create_app(config: Config | None = None) -> FastAPI:
    config = config or load_config()
    root = config.root

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        store = Store(config.path("core.store_path", "data/kontrol-merkezi.sqlite"))
        await store.open()

        registry = Registry()
        bus = EventBus()

        # Platform yetenekleri çekirdekle birlikte gelir; kapatılamaz.
        # Bunlar MODÜL DEĞİLDİR: çekirdek burada yalnız altyapıyı kaydeder,
        # hangi modülün kullandığını bilmez (K1 korunur).
        vault = Vault(store, config)
        await vault.open()
        registry.register("secrets", vault, provider="platform")

        audio = AudioPlayer(config, log)
        registry.register("audio", audio, provider="platform")

        scheduler = WeeklyScheduler(log)
        registry.register("scheduler", scheduler, provider="platform")

        # Baskı da platform yeteneğidir: modül `lp` çağırmaz, buradan geçer (K4).
        registry.register("printer", PrinterService(config, log), provider="platform")

        # Bildirim (SMS) katmanı yazılıydı ama registry'ye hiç bağlanmamıştı;
        # dolayısıyla hiçbir modül ona ulaşamıyordu. Kayıt burada yapılır —
        # sağlayıcı ayardan seçilir, kimlik bilgisi kasadan gelir, çekirdek
        # hangi modülün SMS gönderdiğini bilmez (K1). Kimlik bilgisi yoksa
        # kurulum PATLAMAZ: yetenek yine kayıtlıdır, `ready()` durumu anlatır
        # ve gönderim denendiğinde anlaşılır hata döner (K7).
        registry.register("notify", NotifyService(vault, config, log), provider="platform")

        identity = Identity(
            store,
            pepper=await vault.pepper(),
            pin_min_length=int(config.get("auth.pin_min_length", 6)),
            max_failed_attempts=int(config.get("auth.max_failed_attempts", 5)),
            lockout_minutes=int(config.get("auth.lockout_minutes", 15)),
            session_idle_minutes=int(config.get("auth.session_idle_minutes", 30)),
        )
        await identity.ensure_builtin_roles()
        await identity.grant_defaults(CORE_PERMISSIONS)

        kernel = Kernel(config, store, registry, bus)
        kernel.discover(root / "modules", root / "docs" / "schemas" / "module.schema.json")
        await kernel.load_all()

        # Modüllerin ilan ettiği izinler rollere önerildiği gibi düşer (K6).
        #
        # KEŞFEDİLEN HER MODÜL, yüklenmiş olsun ya da olmasın: izin manifestte
        # ilan edilmiştir, kodun hazır olmasına bağlı değildir. Aksi hâlde
        # `enabled: false` bir modülün ekranı, izni hiç oluşmadığı için menüden
        # süzülür ve iskelet hiç görünmez.
        for record in kernel.records.values():
            await identity.grant_defaults([
                {
                    "key": permission.key,
                    "scoped": permission.scoped,
                    "default_roles": permission.default_roles,
                }
                for permission in record.manifest.permissions
            ])

        await _bootstrap_admin(identity, config)
        _mount_modules(app, kernel, identity)

        app.state.config = config
        app.state.store = store
        app.state.registry = registry
        app.state.bus = bus
        app.state.kernel = kernel
        app.state.identity = identity

        log.info(
            "çekirdek hazır",
            modules=len(kernel.loaded()),
            problems=len(kernel.problems),
        )
        # Zamanlayıcı modüller planlarını kurduktan SONRA başlar; böylece ilk tur
        # dolu tabloyla döner ve açılış anındaki tetikleyici kaçmaz.
        scheduler.start()

        try:
            yield
        finally:
            await scheduler.stop()
            await store.close()

    app = FastAPI(
        title="Kontrol Merkezi",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,       # sidecar; tarayıcı arayüzü sunmaz
        redoc_url=None,
    )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "İstek reddedildi."
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"status": exc.status_code, "message": detail}},
        )

    # ------------------------------------------------------------ kimlik

    async def current_user(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> CurrentUser:
        identity: Identity = request.app.state.identity
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        user = await identity.resolve_session(token) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")
        return user

    app.state.current_user_dependency = current_user

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        kernel: Kernel = request.app.state.kernel
        return {
            "status": "ok",
            "modules": {
                "loaded": len(kernel.loaded()),
                "total": len(kernel.records),
                "problems": kernel.problems,
            },
        }

    @app.post("/auth/login")
    async def login(request: Request, body: LoginRequest) -> dict[str, Any]:
        identity: Identity = request.app.state.identity
        result = await identity.login(body.pin)
        if result is None:
            # Tek tip mesaj: sebep ayırt edilmez (kilitli mi, yok mu, yanlış mı).
            raise HTTPException(status_code=401, detail="Giriş yapılamadı.")
        token, user = result
        return {"token": token, "user": _user_payload(user)}

    @app.post("/auth/logout")
    async def logout(request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
        identity: Identity = request.app.state.identity
        if authorization and authorization.lower().startswith("bearer "):
            await identity.logout(authorization[7:].strip())
        return {"status": "ok"}

    @app.get("/auth/me")
    async def me(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
        return _user_payload(user)

    @app.get("/modules")
    async def modules(request: Request, user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
        """Kabuğun menüyü kurduğu kayıt. Ekranlar kullanıcının izinlerine göre süzülür."""
        kernel: Kernel = request.app.state.kernel

        def visibility(nav: dict[str, Any] | None) -> bool:
            requires = list((nav or {}).get("requires") or [])
            return not requires or any(
                user.has_permission(*_split_permission(item)) for item in requires
            )

        report = [
            {**entry, "source": "module", "visible": visibility((entry.get("ui") or {}).get("nav"))}
            for entry in kernel.report()
        ]

        # Çekirdeğin kendi ekranları modül değildir ama menüde aynı biçimde durur.
        report += [
            {**panel, "source": "core", "state": "loaded", "reason": "",
             "visible": visibility(panel["ui"]["nav"])}
            for panel in CORE_PANELS_UI
        ]

        return {"modules": report}

    return app


# --------------------------------------------------------------------- yardım


def _split_permission(entry: str) -> tuple[str, str | None]:
    key, _, scope = entry.partition(":")
    return key, (scope or None)


def _user_payload(user: CurrentUser) -> dict[str, Any]:
    return {
        "id": user.id,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "fullName": user.full_name,
        "orgScope": user.org_scope,
        "roles": user.roles,
        "permissions": sorted(user.permissions),
    }


def _mount_modules(app: FastAPI, kernel: Kernel, identity: Identity) -> None:
    """Yüklenen modüllerin router'larını izin kapısıyla monte eder."""
    current_user = app.state.current_user_dependency

    for record in kernel.loaded():
        manifest = record.manifest
        context = record.context
        if context is None or not context.routers:
            continue

        spec = manifest.http
        if spec is None or not spec.requires:
            # İzin ilan etmeyen uç nokta reddedilir (K9).
            log.warning("router monte edilmedi — izin ilan edilmemiş", module=manifest.id)
            record.reason = "http.requires boş — router monte edilmedi (K9)"
            continue

        # Modül kendi izin anahtarını ilan etmiş olmalı.
        unknown = [item for item in spec.requires if _split_permission(item)[0] not in manifest.permission_keys]
        if unknown:
            log.warning("router monte edilmedi — bilinmeyen izin", module=manifest.id, keys=unknown)
            record.reason = f"tanımsız izin: {', '.join(unknown)}"
            continue

        guard = _permission_guard(spec.requires, current_user, identity, manifest.id)
        for router in context.routers:
            app.include_router(
                router,
                prefix=spec.prefix,
                tags=list(spec.tags or [manifest.id]),
                dependencies=[Depends(guard)],
            )
        log.info("router monte edildi", module=manifest.id, prefix=spec.prefix)


def _permission_guard(requires: list[str], current_user: Any, identity: Identity, module_id: str):
    async def guard(user: CurrentUser = Depends(current_user)) -> CurrentUser:
        for entry in requires:
            key, scope = _split_permission(entry)
            if user.has_permission(key, scope):
                return user
        await identity.audit(
            user.id, f"{module_id}.access", result="denied", detail=",".join(requires)
        )
        raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")

    return guard


async def _bootstrap_admin(identity: Identity, config: Config) -> None:
    """İlk açılışta yönetici hesabı.

    PIN `config/local.yaml` içindeki `auth.bootstrap_pin` alanından okunur
    (git dışı — K8). Yoksa rastgele üretilir ve YALNIZCA bir kez loga yazılır;
    yönetici girip kendi PIN'ini belirlemelidir.
    """
    if await identity.user_count() > 0:
        return

    pin = str(config.get("auth.bootstrap_pin") or "")
    generated = False
    if not pin:
        while True:
            pin = "".join(pysecrets.choice("0123456789") for _ in range(6))
            try:
                identity.validate_pin(pin)
                break
            except PinError:
                continue
        generated = True

    try:
        identity.validate_pin(pin)
    except PinError as error:
        log.error("bootstrap PIN reddedildi", detail=str(error))
        return

    await identity.create_user(
        first_name="Sistem",
        last_name="Yöneticisi",
        org_scope="org",
        pin=pin,
        roles=["admin"],
    )
    if generated:
        log.warning("İLK YÖNETİCİ OLUŞTURULDU — girip PIN'inizi değiştirin", pin=pin)
    else:
        log.info("İlk yönetici oluşturuldu (PIN local.yaml'dan alındı).")


def _module_root() -> Path:
    return Path(__file__).resolve().parents[4]
