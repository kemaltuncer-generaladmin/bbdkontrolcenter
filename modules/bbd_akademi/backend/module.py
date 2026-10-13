"""BBD Akademi yönetim paneli — giriş noktası.

`register(ctx)` BİLDİRİM AŞAMASIDIR: Akademi'ye istek atılmaz, kasadan anahtar
okunmaz. Anahtar ilk vekil isteğinde kasadan alınır (`gateway.api_key`), yoksa
istek `503 akademi_not_configured` ile anlaşılır biçimde reddedilir.

Personel eşitleme döngüsü çalışan olay döngüsüne görev olarak bırakılır;
çekirdek açılışı onu beklemez ve döngü patlarsa kendini toparlar (K7).
"""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .allowlist import OPERATIONS
from .api.routes import bind
from .features import TenantFeatures
from .finance import StudentFinance
from .gateway import AkademiGateway, GatewayConfig
from .operations import Allowlist
from .permission_map import unmapped
from .proxy import AkademiProxy
from .staff import StaffService
from .stepup import StepUpStore

_LIVE: dict[str, Any] = {}


def register(ctx: ModuleContext) -> None:
    missing = unmapped(OPERATIONS)
    if missing:
        # Sözleşmeye yeni izin girmiş ama eşleme tablosu güncellenmemiş: o
        # işlemler sessizce açık ya da kapalı kalmasın, modül yüklenmesin.
        raise RuntimeError(f"permission_map.py eksik Akademi izinleri: {', '.join(missing)}")

    config = GatewayConfig.from_mapping(ctx.config)
    gateway = AkademiGateway(config=config, secrets=ctx.capability("secrets"), log=ctx.log)
    proxy = AkademiProxy(gateway=gateway, allowlist=Allowlist(OPERATIONS),
                         step_up=StepUpStore(ctx.store, ttl_seconds=config.step_up_seconds),
                         log=ctx.log)
    staff = StaffService(store=ctx.store, gateway=gateway, log=ctx.log)

    # Geçidin olay iletim durumu (Senkron kartı) isteğe bağlıdır ve geçit bu
    # modülden SONRA yüklenebilir: yetenek her istekte yeniden çözülür.
    ctx.add_router(bind(proxy, staff,
                        events=lambda: ctx.try_capability("mobile_gateway.events"),
                        features=TenantFeatures(gateway=gateway, log=ctx.log)))
    ctx.provide("akademi.student_finance", StudentFinance(gateway=gateway, log=ctx.log))
    _LIVE.update(staff=staff, gateway=gateway)
    staff.start(config.staff_sync_minutes)
    ctx.log.info("akademi paneli hazır", base_url=config.base_url,
                 operations=len(OPERATIONS), enabled=config.enabled)


def live() -> dict[str, Any]:
    return _LIVE
