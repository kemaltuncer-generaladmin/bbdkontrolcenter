"""Öğrenci Yönetimi — giriş noktası."""

from __future__ import annotations

from typing import Any

from km_sdk import ModuleContext

from .api.routes import bind
from .canteen_events import CanteenSync
from .directory import StudentDirectory
from .groups import GroupService, StudentGroups
from .lifecycle import Lifecycle
from .service import StudentService
from .sync import StudentSync

#: Masaüstündeki hiyerarşide bu modülün rafı.
CATEGORY = "Öğrenci"


def register(ctx: ModuleContext) -> None:
    # Kantin geçidi olmadan bu modülün yapabileceği bir şey yok; manifestte
    # zorunlu ilan edildiği için çekirdek onu bizden önce yüklemiş olur.
    canteen = ctx.capability("canteen.api")

    # Kalıcı olay outbox'u (ADR 0029) — ÇAĞRI ANINDA çözülür. Platform
    # yeteneğidir, hep vardır; yine de yokluğu modülü düşürmez (K7).
    def outbox() -> Any:
        return ctx.try_capability("events.outbox")

    # Kart PDF'i ve şifre listesi uygulama geneli hiyerarşiye yazılır:
    # Masaüstü/Kontrol Merkezi/Raporlar/Öğrenci/<yıl>/<ay>. Yazdırma
    # `printer` (CUPS) yeteneği ÜZERİNDEN GEÇMEZ — bkz. module.yaml notu.
    # Öğrenci dizini — başka modüller (ör. mobil geçit) registry'den çözer (K3).
    # `bbd_students.list` ise ARAYÜZ yeteneğidir: panelin `capabilities()`
    # fonksiyonu verir (ui/panel/index.js), backend'de karşılığı yoktur.
    directory = StudentDirectory(canteen=canteen, store=ctx.store, log=ctx.log)

    # Önce KM, sonra kantin, sonra uzlaştırma (km-merkezi-senkron §7). Profil
    # yazılınca dizinin önbelleği bırakılır: geçit eski kaydı görmesin.
    sync = StudentSync(store=ctx.store, canteen=canteen, log=ctx.log, outbox=outbox,
                       on_change=directory.invalidate)

    service = StudentService(
        canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config,
        category=CATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
        on_change=directory.invalidate, sync=sync, directory=directory,
    )
    # Öğrenci grupları ve tarihli üyelik (Akademi ve zil buradan okur, K3).
    # Her yazma aynı işlemde outbox'a `group.changed` yazar; commit'ten sonra
    # süreç içi `bbd_students.groups_changed` yayınlanır (mobil geçit
    # önbelleği, zil). Yayın patlarsa yazma yine tamamdır (K7).
    async def groups_changed(revision: int) -> None:
        await ctx.publish("bbd_students.groups_changed", {"revision": revision})

    groups = GroupService(store=ctx.store, log=ctx.log, publish=groups_changed, outbox=outbox)

    # Yaşam döngüsü (pasif / yeniden etkin / kalıcı sil). Tablet kısıtı ve
    # Akademi kurum cari borcu isteğe bağlı yeteneklerdir, çağrı anında çözülür.
    lifecycle = Lifecycle(sync=sync, groups=groups, canteen=canteen, log=ctx.log,
                          directory=directory, capability=ctx.try_capability)

    ctx.add_router(bind(service, groups, lifecycle, sync))
    ctx.provide("bbd_students.directory", directory)
    ctx.provide("bbd_students.groups", StudentGroups(groups))
    # Kantin olay alıcısı (bbd_canteen_api) kasadaki öğrenci değişikliğini
    # buradan KM kaydına çevirir — profil tablosuna yalnız bu modül yazar (K5).
    ctx.provide("bbd_students.sync",
                CanteenSync(sync=sync, directory=directory, canteen=canteen, log=ctx.log))
    # Uzlaştırma görevi: 1 dk + kantin geri gelince; planlı pasifleştirmeler.
    sync.start()
    ctx.log.info("öğrenci yönetimi hazır", kategori=CATEGORY)
