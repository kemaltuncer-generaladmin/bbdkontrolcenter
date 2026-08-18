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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from km_core.build_info import build_info
from km_core.bus.bus import EventBus
from km_core.config.loader import Config, load_config, nest, with_store_layer
from km_core.config.settings_store import SettingsStore
from km_core.files import outputs_log
from km_core.files.outputs_queue import OutputsQueue
from km_core.http.documents import create_documents_router
from km_core.http.settings import create_settings_router
from km_core.http.users import create_identity_router, user_payload
from km_core.kernel.kernel import Kernel
from km_core.registry.registry import Registry
from km_core.security.identity import CurrentUser, Identity, PasswordError
from km_core.security.permissions import CORE_PERMISSIONS
from km_core.store.bootstrap import build_schema
from km_core.store.engine import create_store, selected_engine
from km_platform.audio.player import AudioPlayer
from km_platform.identity_sync.http import create_pairing_router
from km_platform.identity_sync.service import IdentitySync
from km_platform.notify.service import NotifyService
from km_platform.printer.cups import PrinterService
from km_platform.scheduler.weekly import WeeklyScheduler
from km_platform.secrets.vault import Vault

log = structlog.get_logger("km.http")


# Çekirdeğin kendi ekranları. Kimlik, ayar ve sistem sağlığı modül DEĞİLDİR
# (CLAUDE.md — kavram ayrımı); silinemezler, manifestleri yoktur.
CORE_PANELS_UI: list[dict[str, Any]] = [
    {
        "id": "core_users", "name": "Kullanıcı Yönetimi", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Kullanıcı Yönetimi", "icon": "users", "group": "Kurumsal",
                       "order": 800, "requires": ["users.view"]}},
    },
    {
        "id": "core_settings", "name": "Sistem Ayarları", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Sistem Ayarları", "icon": "sliders", "group": "Kurumsal",
                       "order": 810, "requires": ["settings.view"]}},
    },
    {
        "id": "core_health", "name": "Sistem Sağlığı", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "Sistem Sağlığı", "icon": "pulse", "group": "Kurumsal",
                       "order": 820, "requires": ["settings.view"]}},
    },
    {
        # ADR 0021 §4 — merkeze eşlenmiş makineler. Ekran `installations.view`
        # ile menüye girer, yönetim düğmeleri ayrıca `installations.manage`
        # ister ve ikisi de backend'de yeniden sorulur (K9).
        "id": "core_pairing", "name": "KM Cihaz Eşle", "version": "0.1.0", "provides": [],
        "permissions": [],
        "ui": {"nav": {"title": "KM Cihaz Eşle", "icon": "monitor", "group": "Kurumsal",
                       "order": 830, "requires": ["installations.view"]}},
    },
]


def create_app(config: Config | None = None) -> FastAPI:
    # Açılışta okunan ayar. Çalışma anındaki ayar bunun ÜSTÜNE depo katmanı
    # eklenerek kurulur (aşağıda); ikisi ayrı adlarla durur ki hangisine
    # bakıldığı okurken belirsiz kalmasın.
    base_config: Config = config or load_config()
    root = base_config.root

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = base_config

        # ADR 0026 — motor ortam değişkeninden seçilir. Sunucuda PostgreSQL,
        # yerelde SQLite; değişken yoksa bugüne kadarki davranış aynen sürer.
        sqlite_path = config.path("core.store_path", "data/kontrol-merkezi.sqlite")
        store = create_store(sqlite_path)
        await store.open()

        # ADR 0019 — çıktı kaydının deposu BURADAN bildirilir. Kayıt katmanı
        # senkrondur ve ayara bakmaz; hangi depoya yazacağını ancak ayakta olan
        # uygulama söyleyebilir. Bildirilmezse çıktı yine üretilir, yalnız
        # listeye düşmez (K7) — ayrıntı `km_core/files/outputs_log.py`.
        #
        # SQLite'ta kayıt SENKRON yazılır — bugüne kadarki yol. PostgreSQL'de
        # senkron yazmak olay döngüsünü bloklardı, o yüzden araya kuyruk girer.
        # İki yol bilerek ayrı: kanıtlanmış SQLite yolunu asenkron yapmak,
        # "dosya yazıldı ama kayıt henüz düşmedi" penceresini yoktan var eder
        # ve testleri zamanlamaya bağımlı kılardı.
        outputs_queue: OutputsQueue | None = None
        if selected_engine() == "sqlite":
            outputs_log.use_database(sqlite_path)
        else:
            outputs_queue = OutputsQueue(store)
            await outputs_queue.start()
            outputs_log.use_writer(outputs_queue.writer())

        # Var olan veritabanına sır sütunları eklenir, eski PIN sütunları
        # DÜŞÜRÜLMEZ. `CORE_SCHEMA` yalnız yeni kurulumu kurar; çalışmış bir
        # makinede sütunlar ancak buradan gelir (ADR 0016 reddedildi ama göçü
        # koştu — sütun adları olduğu gibi bırakıldı).
        # `build_schema` çekirdek şemasını VE göçlerini kurar. Tek yerden
        # geçmesinin sebebi: yerel SQLite ile sunucudaki PostgreSQL'in şeması
        # ayrışamasın (`km_core/store/bootstrap.py`). Modül göçleri BURADA
        # DEĞİL, kernel'de kalır — düşen bir modül diğerlerini düşürmesin (K7).
        await build_schema(store)

        # ADR 0018 §4 — ARAYÜZDEN DEĞİŞEN AYAR ÇEKİRDEK DEPOSUNA YAZILIR ve
        # zincirde `local.yaml`'ı EZER. Katman burada, yetenekler kurulmadan
        # ÖNCE uygulanır: yazıcı kuyruğu, günlük düzeyi ve çıktı klasörü gibi
        # ayarlar nesneler kurulurken okunuyor. Sonradan uygulansaydı ekrandan
        # yapılan değişiklik hiçbir zaman etkili olmazdı.
        #
        # Ortam değişkeni en üstte kalır (`with_store_layer` onu yeniden
        # uygular): acil müdahale yolu kapanmaz. `settings` tablosunun göçü
        # yukarıdaki `build_schema` içinde uygulandı.
        config = with_store_layer(config, nest(await SettingsStore(store).values()))

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

        # Kimlik senkronu da platform yeteneğidir (ADR 0021): kurulum token'ı
        # kasadan gelir, kadro merkezden çekilir, yazma merkeze gider. Ayar
        # kapalıysa ya da merkez ulaşılamazsa HİÇBİR YETENEK GERİLEMEZ —
        # kurulum bugünkü gibi tek makinede çalışır ve giriş yereldedir (K7).
        identity_sync = IdentitySync(vault, config)
        registry.register("identity_sync", identity_sync, provider="platform")

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
        app.state.identity_sync = identity_sync

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
            # Bağ depo KAPANMADAN önce çözülür: kapanış sırasında yazılan bir
            # dosya, kapanmış bir bağlantıya kayıt düşürmeye çalışmasın.
            # SIRA ÖNEMLİ: önce kapı kapanır, sonra kuyruk boşaltılır. Ters
            # olsaydı boşaltma sırasında düşen yeni bir satır kuyrukta kalırdı.
            outputs_log.use_writer(None)
            if outputs_queue is not None:
                await outputs_queue.stop()
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
            # BAŞLIKLAR TAŞINIR. Zarfa çevirirken düşürülüyordu ve bazı
            # yanıtın anlamı başlıkta duruyor: 429'un `Retry-After`ı olmadan
            # istemci ne kadar bekleyeceğini bilemez, 401'in
            # `WWW-Authenticate`i kaybolur.
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Gövde doğrulaması (422) da AYNI zarfa girer ve TÜRKÇE konuşur.

        BURASI EKSİKTİ VE BELİRTİSİ ŞUYDU: kabuk hata metnini
        `payload?.error?.message || payload?.detail` sırasıyla okuyor
        (ui-kernel.js). `HTTPException` zarfa giriyordu ama doğrulama hatası
        FastAPI'nin varsayılan `{"detail": [{...}, {...}]}` DİZİSİ olarak
        çıkıyordu; dizi `error.message`e düşünce ekranda `[object Object]`
        yazıyordu. Tek bir eksik işleyici yüzünden HER modülde, doldurulmamış
        bir alanın karşılığı okunamayan bir hataydı.

        Metin iş dilinde yazılır: kullanıcı "field required" ya da
        "string_too_short" okumak zorunda değil. Alan adı olduğu gibi
        gösterilir — uydurma bir çeviri, kullanıcının ekranda gördüğü kutuyu
        bulmasını zorlaştırırdı.
        """
        parcalar: list[str] = []
        for hata in exc.errors():
            yer = [str(adim) for adim in hata.get("loc", ())
                   if str(adim) not in ("body", "query", "path")]
            alan = ".".join(yer) or "gövde"
            tur = str(hata.get("type", ""))
            sinir = hata.get("ctx") or {}
            if tur == "missing":
                parcalar.append(f"“{alan}” alanı zorunlu.")
            elif tur.endswith("_too_short"):
                parcalar.append(f"“{alan}” en az {sinir.get('min_length', '?')} karakter olmalı.")
            elif tur.endswith("_too_long"):
                parcalar.append(f"“{alan}” en fazla {sinir.get('max_length', '?')} karakter olabilir.")
            elif tur.startswith("greater_than"):
                parcalar.append(f"“{alan}” {sinir.get('ge', sinir.get('gt', '?'))} değerinden küçük olamaz.")
            elif tur.startswith("less_than"):
                parcalar.append(f"“{alan}” {sinir.get('le', sinir.get('lt', '?'))} değerinden büyük olamaz.")
            else:
                # TANIMADIĞIMIZ TÜR YUTULMAZ: kendi mesajıyla gösterilir.
                # Listeden düşürmek, ekranı "sorun yok" der hâle getirirdi.
                parcalar.append(f"“{alan}”: {hata.get('msg', 'geçersiz değer')}")
        return JSONResponse(
            status_code=422,
            content={"error": {"status": 422,
                               "message": " ".join(parcalar) or "Gönderilen veri geçersiz."}},
        )

    # ------------------------------------------------------------ kimlik

    async def current_user(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> AsyncIterator[CurrentUser]:
        identity: Identity = request.app.state.identity
        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization[7:].strip()
        user = await identity.resolve_session(token) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")

        # ADR 0019 §2 — "bu raporu kim aldı" sorusunun cevabı burada doğar.
        # Modüller çıktıyı SENKRON `write_private` ile yazar ve oradan oturuma
        # ulaşamaz; kimlik bu yüzden bağlam değişkeninde taşınır.
        #
        # BAĞLAM İSTEKLE BİRLİKTE BİTER: `use_actor` çıkışta değeri geri alır.
        # Bırakılsaydı, aynı olay döngüsünde sırayı devralan bir sonraki istek
        # başkasının kimliğiyle çıktı üretirdi — kaydın yanlış olması, hiç
        # olmamasından kötüdür.
        with outputs_log.use_actor(user.id):
            yield user

    app.state.current_user_dependency = current_user

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        kernel: Kernel = request.app.state.kernel
        return {
            "status": "ok",
            # KÜNYE: hangi koddan derlendiği. İzin İSTEMEZ ve sır taşımaz —
            # bir commit kısaltması. "Düzelttim" ile "bende düzelmedi"
            # arasındaki farkı ölçmenin tek yolu budur (bkz. build_info).
            "build": build_info(request.app.state.config.root),
            "modules": {
                "loaded": len(kernel.loaded()),
                "total": len(kernel.records),
                "problems": kernel.problems,
            },
        }

    # Giriş, PIN kurma, kullanıcı ve rol uçları tek yerde durur. Sözleşme
    # `/api` önekiyle yayınlanır; ayrıntı `km_core/http/users.py`.
    app.include_router(create_identity_router(), prefix="/api")

    # Sistem Ayarları çekirdek ekranıdır (ADR 0017): modül değildir, manifesti
    # yoktur, kapatılamaz. Router'ı bu yüzden `_mount_modules` kapısından değil
    # doğrudan buradan geçer — ama izin denetimi aynıdır: her uç kendi iznini
    # `km_core/http/settings.py` içinde bağımsız olarak sorar (K9).
    app.include_router(create_settings_router(), prefix="/api")
    # Üretilmiş belgenin baytları — baskı kullanıcının makinesinde yapılıyor
    # (sunucuda yazıcı yok), kabuğun PDF'i eline alması gerekiyor.
    app.include_router(create_documents_router(), prefix="/api")

    # Eşleme uçları GİRİŞTEN ÖNCE gelir (ADR 0021 §4): eşleşmemiş kurulumda
    # henüz kadro ve dolayısıyla oturum yoktur. Uçlar oturumla değil tek
    # kullanımlık KODLA korunur; gerekçe `km_platform/identity_sync/http.py`.
    app.include_router(create_pairing_router(), prefix="/api")

    @app.post("/auth/logout")
    async def logout(request: Request, authorization: str | None = Header(default=None)) -> dict[str, str]:
        identity: Identity = request.app.state.identity
        if authorization and authorization.lower().startswith("bearer "):
            await identity.logout(authorization[7:].strip())
        return {"status": "ok"}

    @app.get("/auth/me")
    async def me(user: CurrentUser = Depends(current_user)) -> dict[str, Any]:
        return user_payload(user)

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


def _permission_guard(requires: list[str], current_user: Any, identity: Identity,
                      module_id: str) -> Any:
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
            except PasswordError:
                continue
        generated = True

    try:
        # `password` argüman adı göçten kalmıştır; verilen değer PIN'dir.
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
        log.info("İlk yönetici oluşturuldu (PIN local.yaml'dan alındı).")


def _module_root() -> Path:
    return Path(__file__).resolve().parents[4]
