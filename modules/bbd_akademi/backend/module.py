"""BBD Akademi yönetim paneli — giriş noktası.

`register(ctx)` BİLDİRİM AŞAMASIDIR: Akademi'ye istek atılmaz, kasadan anahtar
okunmaz. Anahtar ilk vekil isteğinde kasadan alınır (`gateway.api_key`), yoksa
istek `503 akademi_not_configured` ile anlaşılır biçimde reddedilir.

Personel eşitleme döngüsü çalışan olay döngüsüne görev olarak bırakılır;
çekirdek açılışı onu beklemez ve döngü patlarsa kendini toparlar (K7).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from km_sdk import ModuleContext

from .allowlist import OPERATIONS
from .api.routes import bind
from .club.watcher import StoreClubWatcher
from .core.features import TenantFeatures
from .core.gateway import AkademiGateway, GatewayConfig
from .core.operations import Allowlist
from .core.proxy import AkademiProxy
from .core.stepup import StepUpStore
from .documents.service import DocumentService, Letterheads
from .finance.access import FinanceAccess
from .finance.service import StudentFinance
from .permission_map import unmapped
from .staff.service import StaffService

_LIVE: dict[str, Any] = {}


def register(ctx: ModuleContext) -> None:
    missing = unmapped(OPERATIONS)
    if missing:
        # Sözleşmeye yeni izin girmiş ama eşleme tablosu güncellenmemiş: o
        # işlemler sessizce açık ya da kapalı kalmasın, modül yüklenmesin.
        raise RuntimeError(f"permission_map.py eksik Akademi izinleri: {', '.join(missing)}")

    config = GatewayConfig.from_mapping(ctx.config)
    gateway = AkademiGateway(config=config, secrets=ctx.capability("secrets"), log=ctx.log)
    allowlist = Allowlist(OPERATIONS)
    # Kurum finansı kilidi (iş 83): KM oturumu başına SMS onay kodlu 30 dk kayan izin — vekilin kurum-finansı
    # işlemlerinde zorunlu. Arka plan işleri (personel eşitlemesi, kulüp izleyicisi, yetenekler) vekilden geçmez.
    finance_access = FinanceAccess(gateway=gateway, log=ctx.log)
    proxy = AkademiProxy(gateway=gateway, allowlist=allowlist,
                         step_up=StepUpStore(ctx.store, ttl_seconds=config.step_up_seconds),
                         log=ctx.log, finance=finance_access)
    staff = StaffService(store=ctx.store, gateway=gateway, log=ctx.log)
    # Belgeler (önizle/yazdır): PDF rapor köküne yazılır; masaüstü yoksa
    # kurulum kökündeki data/exports (çekirdeğin `output_roots` ile aynı yer).
    module_path = getattr(ctx, "module_path", None)
    install_root = Path(module_path).parents[1] if module_path else Path.cwd()
    documents = DocumentService(
        gateway=gateway, allowlist=allowlist, log=ctx.log,
        fallback_dir=install_root / "data" / "exports",
        letterheads=Letterheads(gateway=gateway, log=ctx.log,
                                fallback_name=str((ctx.config or {}).get("document_title") or "")))

    # Deneme Kulübü BBDSTORE izleyicisi (iş 81-km-kulup): 2 dk'da bir ödenmiş kulüp siparişleri →
    # Akademi daveti. Mağaza Geçidi (`store.api`) isteğe bağlıdır ve her turda yeniden çözülür (K3, K7).
    watcher = StoreClubWatcher(
        gateway=gateway, store=ctx.store, log=ctx.log,
        store_api=lambda: ctx.try_capability("store.api"),
        interval=float((ctx.config or {}).get("club_store_watch_seconds", 120) or 0))
    # Mağaza ileride "ödendi" olayı yayınlarsa tur hemen koşar (bugün mağazanın KM'ye olay kanalı yok).
    subscribe = getattr(ctx, "subscribe", None)
    if callable(subscribe):
        subscribe("store.trial_club.order_paid", watcher.wake)

    # Geçidin olay iletim durumu (Senkron kartı) isteğe bağlıdır ve geçit bu
    # modülden SONRA yüklenebilir: yetenek her istekte yeniden çözülür.
    ctx.add_router(bind(proxy, staff,
                        events=lambda: ctx.try_capability("mobile_gateway.events"),
                        features=TenantFeatures(gateway=gateway, log=ctx.log),
                        documents=documents, club=watcher, finance=finance_access))
    ctx.provide("akademi.student_finance", StudentFinance(gateway=gateway, log=ctx.log))
    _LIVE.update(staff=staff, gateway=gateway, club_watcher=watcher)
    staff.start(config.staff_sync_minutes)
    if config.enabled:
        try:
            watcher.start()
        except RuntimeError:  # pragma: no cover — döngü yoksa (araçlar) izleyici başlamaz
            pass
    ctx.log.info("akademi paneli hazır", base_url=config.base_url,
                 operations=len(OPERATIONS), enabled=config.enabled)


def live() -> dict[str, Any]:
    return _LIVE
