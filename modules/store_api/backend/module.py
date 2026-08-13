"""BBD Store geçidi — giriş noktası.

`register(ctx)` içinde İŞ YAPILMAZ: ağa çıkılmaz, kasadan sır okunmaz, DB'ye
yazılmaz. Yalnız yetenek deftere yazılır. Mağazaya ilk istek, bir ekran
gerçekten veri isteyene kadar atılmaz — belirteç de o ana kadar okunmaz.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .client import DEFAULT_BASE_URL, StoreApi
from .upload import DEFAULT_MAX_UPLOAD_MB


def register(ctx: ModuleContext) -> None:
    config = ctx.config
    api = StoreApi(
        base_url=str(config.get("base_url") or DEFAULT_BASE_URL).strip(),
        secrets=ctx.capability("secrets"),
        log=ctx.log,
        store=ctx.store,
        timeout=float(config.get("timeout_seconds") or 30),
        # Varsayılanlar GÜVENLİ tarafta: canlı mağazada çalışıyoruz, yazmayı
        # açmak ve kuru provayı kapatmak bilinçli birer karardır.
        read_only=bool(config.get("read_only", True)),
        dry_run_default=bool(config.get("dry_run_default", True)),
        require_reason=bool(config.get("require_reason", True)),
        page_size=int(config.get("page_size") or 50),
        requests_per_minute=int(config.get("requests_per_minute") or 55),
        reference_ttl=int(config.get("reference_ttl_seconds") or 900),
        snapshot_ttl=int(config.get("snapshot_ttl_seconds") or 1800),
        max_items=int(config.get("max_items") or 5000),
        max_upload_mb=int(config.get("max_upload_mb") or DEFAULT_MAX_UPLOAD_MB),
    )

    ctx.provide("store.api", api)
    ctx.log.info("mağaza geçidi hazır", base_url=api.state()["baseUrl"],
                 read_only=api.state()["readOnly"])
