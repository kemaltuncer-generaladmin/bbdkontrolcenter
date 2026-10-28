"""Öğrenci Yönetimi — giriş noktası."""

from __future__ import annotations

import asyncio
from typing import Any

from km_sdk import ModuleContext

from .academic_year import YearBackfill
from .admission import StudentAdmission
from .api.routes import bind
from .canteen_events import CanteenSync
from .classes import ClassLink
from .classes_merge import ClassMerge
from .directory import StudentDirectory
from .groups import GroupService, StudentGroups
from .identity import IdentityCipher
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
    # Sözleşme kimliği (göç 014): TC / yabancı kimlik no ŞİFRELİ; anahtar sır kasasında
    # (`bbd_students.identity_key`, ilk kullanımda üretilir). Kasa ÇAĞRI ANINDA çözülür.
    cipher = IdentityCipher(lambda: ctx.try_capability("secrets"))
    directory = StudentDirectory(canteen=canteen, store=ctx.store, log=ctx.log, cipher=cipher)

    # Önce KM, sonra kantin, sonra uzlaştırma (km-merkezi-senkron §7). Profil
    # yazılınca dizinin önbelleği bırakılır: geçit eski kaydı görmesin.
    sync = StudentSync(store=ctx.store, canteen=canteen, log=ctx.log, outbox=outbox,
                       on_change=directory.invalidate)

    service = StudentService(
        canteen=canteen, store=ctx.store, log=ctx.log, config=ctx.config,
        category=CATEGORY,
        fallback_dir=ctx.module_path.parents[1] / "data" / "exports",
        on_change=directory.invalidate, sync=sync, directory=directory, cipher=cipher,
    )
    # Öğrenci grupları ve tarihli üyelik (Akademi ve zil buradan okur, K3).
    # Her yazma aynı işlemde outbox'a `group.changed` yazar; commit'ten sonra
    # süreç içi `bbd_students.groups_changed` yayınlanır (mobil geçit
    # önbelleği, zil). Yayın patlarsa yazma yine tamamdır (K7).
    async def groups_changed(revision: int) -> None:
        # Dizin `classes[].name`'i sınıf tablosundan okur: görüntü yeniden kurulsun.
        directory.invalidate(canteen=False)
        await ctx.publish("bbd_students.groups_changed", {"revision": revision})

    # SINIF = GRUP (göç 009): üyelik değişince öğrencinin türetilmiş alanları
    # (`class_ids`, eski `class_name`) + `student.updated`, grup yazmasının
    # İŞLEMİNDE; sınıfın seviyesi öğrenciye ŞABLON olarak yazılır.
    link = ClassLink(store=ctx.store, sync=sync, log=ctx.log)

    # Arşivdeki sınıf KALICI silindi (göç 011): zil o sınıfın planlı oturum
    # çağrılarını iptal eder (kendi tablosu, K5). Yük {id, revision}.
    async def group_deleted(group_id: str, revision: int) -> None:
        await ctx.publish("bbd_students.group_deleted", {"id": group_id, "revision": revision})

    groups = GroupService(store=ctx.store, log=ctx.log, publish=groups_changed, outbox=outbox,
                          students_changed=link.refresh, apply_level=link.apply_level,
                          deleted=group_deleted)
    merge = ClassMerge(store=ctx.store, groups=groups, log=ctx.log)
    # Mevcut BÜTÜN sınıflara dönem 2026-2027 (göç 011 verisi; yalnız dönemi boş satırlar).
    years = YearBackfill(store=ctx.store, groups=groups, log=ctx.log)
    upkeep_lock = asyncio.Lock()
    years_done = False

    async def class_upkeep() -> None:
        """Açılışta sınıf birleştirmesi (bir kez; idempotent) + sınıf dönemi ataması (bir
        kez; idempotent) + günlük üyelik uzlaştırması.

        Uzlaştırma turunun kancasıdır: patlaması kantin itmesini durdurmaz (K7).
        """
        nonlocal years_done
        try:
            async with upkeep_lock:
                status = await merge.status()
                if status["state"] == "":
                    await merge.run()
                if not years_done:
                    await years.run()
                    years_done = True
                await link.reconcile_all()
        except Exception as failure:  # noqa: BLE001 — bir sonraki turda yeniden denenir
            ctx.log.warning("sınıf birleştirme / uzlaştırma patladı", error=str(failure))

    sync.due_hooks.append(class_upkeep)

    # Yaşam döngüsü (pasif / yeniden etkin / kalıcı sil). Tablet kısıtı ve
    # Akademi kurum cari borcu isteğe bağlı yeteneklerdir, çağrı anında çözülür.
    lifecycle = Lifecycle(sync=sync, groups=groups, canteen=canteen, log=ctx.log,
                          directory=directory, capability=ctx.try_capability)

    ctx.add_router(bind(service, groups, lifecycle, sync, merge=merge, link=link))
    ctx.provide("bbd_students.directory", directory)
    ctx.provide("bbd_students.groups", StudentGroups(groups))
    # Dış kaynaklı öğrenci açılışı (iş 81-km-kulup): Akademi'nin Deneme Kulübü formu mobil
    # geçidin `POST /students` ucundan buraya gelir; öğrenci KM'de normal öğrencidir (K3, K5).
    ctx.provide("bbd_students.admission",
                StudentAdmission(service=service, store=ctx.store, directory=directory,
                                 log=ctx.log))
    # Kantin olay alıcısı (bbd_canteen_api) kasadaki öğrenci değişikliğini
    # buradan KM kaydına çevirir — profil tablosuna yalnız bu modül yazar (K5).
    ctx.provide("bbd_students.sync",
                CanteenSync(sync=sync, directory=directory, canteen=canteen, log=ctx.log))
    # Uzlaştırma görevi: 1 dk + kantin geri gelince; planlı pasifleştirmeler.
    sync.start()
    # Birleştirme uzlaştırma turunu (60 sn) beklemesin: açılışta hemen bir kez.
    try:
        asyncio.get_running_loop().create_task(class_upkeep(), name="km-bbd-students-classes")
    except RuntimeError:  # pragma: no cover — döngü yoksa (araçlar) ilk turda koşar
        pass
    ctx.log.info("öğrenci yönetimi hazır", kategori=CATEGORY)
