"""Öğrenci Yönetimi — giriş noktası."""

from __future__ import annotations

from km_sdk import ModuleContext

from .api.routes import bind
from .directory import StudentDirectory
from .groups import GroupService, StudentGroups
from .service import StudentService

#: Masaüstündeki hiyerarşide bu modülün rafı.
CATEGORY = "Öğrenci"


def register(ctx: ModuleContext) -> None:
    # Kantin geçidi olmadan bu modülün yapabileceği bir şey yok; manifestte
    # zorunlu ilan edildiği için çekirdek onu bizden önce yüklemiş olur.
    canteen = ctx.capability("canteen.api")

    # Kart PDF'i ve şifre listesi uygulama geneli hiyerarşiye yazılır:
    # Masaüstü/Kontrol Merkezi/Raporlar/Öğrenci/<yıl>/<ay>. Yazdırma
    # `printer` (CUPS) yeteneği ÜZERİNDEN GEÇMEZ — bkz. module.yaml notu.
    # Öğrenci dizini — başka modüller (ör. mobil geçit) registry'den çözer (K3).
    # `bbd_students.list` ise ARAYÜZ yeteneğidir: panelin `capabilities()`
    # fonksiyonu verir (ui/panel/index.js), backend'de karşılığı yoktur.
    directory = StudentDirectory(canteen=canteen, store=ctx.store, log=ctx.log)

    service = StudentService(
        canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config,
        category=CATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
        # Öğrenci yazılınca dizinin önbelleği bırakılır: geçit eski kaydı görmesin.
        on_change=directory.invalidate,
    )
    # Öğrenci grupları ve tarihli üyelik (Akademi ve zil buradan okur, K3).
    # Her yazmanın ardından `bbd_students.groups_changed` yayınlanır: mobil
    # geçit önbelleğini bırakır. Yayın patlarsa yazma yine tamamdır (K7).
    async def groups_changed(revision: int) -> None:
        await ctx.publish("bbd_students.groups_changed", {"revision": revision})

    groups = GroupService(store=ctx.store, log=ctx.log, publish=groups_changed)

    ctx.add_router(bind(service, groups))
    ctx.provide("bbd_students.directory", directory)
    ctx.provide("bbd_students.groups", StudentGroups(groups))
    ctx.log.info("öğrenci yönetimi hazır", kategori=CATEGORY)
