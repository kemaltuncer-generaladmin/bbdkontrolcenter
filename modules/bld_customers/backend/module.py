"""Müşteriler — giriş noktası.

`register(ctx)` içinde iş yapılmaz, ağa çıkılmaz, DB'ye yazılmaz: yalnız servis
kurulur ve router bağlanır. BLD erişilemez olsa bile modül yüklenir ve ekran
durumu anlatır (K7) — satış devam ediyorken müşteri ekranının hiç açılmaması,
sorunun kendisini görünmez yapardı.

AÇILIŞTA HİÇBİR MÜŞTERİ OKUNMAZ. Burada bir "ön ısıtma" ya da sayaç çağrısı
yoktur ve olmayacaktır: `control/customers/*` altındaki her okuma bir denetim
satırı yazar (`00-genel.md` §9) ve modül yüklenirken atılan bir istek, hiçbir
yöneticinin sormadığı bir soru için o deftere satır eklerdi.

Bu modül BLD'ye YALNIZ `bld.api` yeteneğinden bakar (K4). Ham `httpx` yoktur:
imzalı `X-Control-Signature` başlığı, zaman penceresi, nonce hatırlama, oran
sınırı, KVKK'nın zorunlu kıldığı `actor` sorgu parametresi ve `dry_run` taşıma
geçidin işidir ve tek yerde durur.
"""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .service import CustomersService


def register(ctx: ModuleContext) -> None:
    api = ctx.capability("bld.api")

    service = CustomersService(
        api=api,
        store=ctx.store,
        log=ctx.log,
        config=ctx.config,
        publish=ctx.publish,
    )
    ctx.add_router(bind(service))
    # Günlükte MÜŞTERİ VERİSİ GEÇMEZ — burada da, servis içindeki uyarılarda da
    # yalnız kimlik ve hata metni var. Günlük dosyası denetim izinden farklı
    # olarak dönüp temizlenmiyor; oraya bir telefon numarası düşerse orada kalır.
    ctx.log.info("müşteri ekranı hazır",
                 dry_run_default=bool(ctx.config.get("dry_run_default", False)))
