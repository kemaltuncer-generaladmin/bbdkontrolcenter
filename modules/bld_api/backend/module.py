"""BLD geçidi — giriş noktası.

`register(ctx)` içinde İŞ YAPILMAZ: ağa çıkılmaz, kasadan sır okunmaz, DB'ye
yazılmaz. Yalnız yetenek deftere yazılır. BLD sunucusuna ilk istek, bir ekran
gerçekten veri isteyene kadar atılmaz — imza sırrı da o ana kadar okunmaz.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .client import BldApi


def register(ctx: ModuleContext) -> None:
    config = ctx.config
    api = BldApi(
        # Taban adres depoda durmaz; canlı değer config/local.yaml'dan gelir.
        # Boş bırakılırsa geçit ilk istekte anlaşılır bir `config_missing`
        # hatası döner — sessizce localhost'a gitmez.
        base_url=str(config.get("base_url") or "").strip(),
        secrets=ctx.capability("secrets"),
        log=ctx.log,
        store=ctx.store,
        timeout=float(config.get("timeout_seconds") or 30),
        # ACİL FREN varsayılan AÇIK: canlı mutfakta çalışıyoruz, yazmayı açmak
        # bilinçli bir karardır. KURU PROVA VARSAYILANI ise KAPALI (K-22 §4):
        # arayüzde şalteri kalktı ve panel artık bayrağı hiç göndermiyor.
        # Buradaki yedek değerin de `False` olması şart — `True` kalsaydı,
        # ayar dosyası okunamadığında her yazma sessizce bir provaya döner ve
        # ekran "yazıldı" derken sunucuda hiçbir şey değişmezdi.
        read_only=bool(config.get("read_only", True)),
        dry_run_default=bool(config.get("dry_run_default", False)),
        require_reason=bool(config.get("require_reason", True)),
        # İKİ PENCERE: kısa pencere etkileşimli patlamaya izin verir, uzun
        # pencere sunucunun saatlik sınırını korur. Tek dakikalık kova
        # (eski 18) panel açılışlarını saniyelerce uyutuyor ve saatlik
        # bütçenin büyük kısmını kullanılmadan bırakıyordu.
        requests_per_minute=int(config.get("requests_per_minute") or 45),
        requests_per_hour=int(config.get("requests_per_hour") or 1100),
        # Sayfalama ve önbellek: on üç panel alanının yoklamasını tek hız
        # kovasının altında tutan iki ayar. Önbellek YALNIZCA referans veri
        # içindir (bkz. `cache.py`); değeri sıfırlamak onu kapatır.
        page_size=int(config.get("page_size") or 25),
        reference_ttl=int(config.get("reference_ttl_seconds") or 900),
        snapshot_ttl=int(config.get("snapshot_ttl_seconds") or 1800),
        max_items=int(config.get("max_items") or 5000),
        max_upload_mb=int(config.get("max_upload_mb") or 8),
    )

    ctx.provide("bld.api", api)
    ctx.log.info("BLD geçidi hazır", base_url=api.state()["base_url"],
                 read_only=api.state()["read_only"])
