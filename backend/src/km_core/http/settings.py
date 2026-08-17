"""Sistem Ayarları uçları (ADR 0017 · ADR 0018).

Sözleşme — kabuk bu adreslere konuşur:

    GET  /api/settings                     → {tabs, app, canManage, dropped}
    PUT  /api/settings                     {tab, values}  → {tab, changed, restartRequired}
    GET  /api/settings/printer             → yazıcı durumu ve tanılaması
    POST /api/settings/printer/test-page   → test sayfası basar
    GET  /api/settings/update              → çekirdek sürümü + güncelleme nerede
    GET  /api/settings/diagnostics         → maskelenmiş teknik günlük
    POST /api/settings/support-bundle      → destek paketi (zip) üretir

**K9 — çift kapı.** Her uç izni BURADA, bağımsız olarak denetler. Okuma
`settings.view`, yazma `settings.manage` ister. Modül sekmesi ayrıca KENDİ
`requires` iznini sorar (ADR 0018 §3): `settings.manage` sahibi olmak, bir
modülün ayarını değiştirme yetkisi vermez.

**K1 — çekirdek modül bilmez.** Modül sekmeleri `ModuleRecord.settings`
bloğundan, VERİ olarak kurulur. Bu dosyada hiçbir modül adı geçmez; `modules/`
silinse ekran açılır ve yalnız çekirdek sekmeleri kalır.

**K8 — sır bu ekrandan yazılmaz.** Ayar sözleşmesinde sır tipi yoktur; kasa
ayrı bir ekranın işidir. Tanılama çıktısı maskelenmeden dışarı çıkmaz
(`km_core/http/masking.py`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from km_core.config.loader import (
    Config,
    env_layer,
    file_layers,
    nest,
    value_at,
    with_store_layer,
)
from km_core.config.settings_catalog import (
    MANAGE_PERMISSION,
    VIEW_PERMISSION,
    SettingsError,
    core_tabs,
    field_specs,
    module_tab,
    resolve_tab,
    validate_value,
)
from km_core.config.settings_store import SettingsStore
from km_core.files.outputs import report_dir
from km_core.files.private import write_private
from km_core.http.deps import requires, split_permission
from km_core.http.masking import mask_text
from km_core.http.support_bundle import app_summary, bundle_bytes, bundle_name, read_log_tail
from km_core.kernel.kernel import Kernel
from km_core.security.identity import CurrentUser, Identity
from km_core.store.db import Store

log = structlog.get_logger("km.http.settings")

#: Tanılama ekranında gösterilecek satır sayısı. Tamamı gösterilmez: dosya
#: aylarca büyüyor ve ekran onu çizmeye çalışırken donardı.
LOG_LINES = 300

#: Destek paketi ve test sayfası bu rafa yazılır (ADR 0019'un hiyerarşisi).
OUTPUT_CATEGORY = "Destek"


class _Body(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class SettingsUpdate(_Body):
    """Yazma isteği.

    `values` anahtarları ALAN ADIDIR, ayar yolu değil. Yol katalogtan çözülür;
    böylece ekran (ya da uca doğrudan istek atan biri) rastgele bir ayar yoluna
    yazamaz — yalnız ilan edilmiş alanlara dokunabilir.
    """

    tab: str = Field(min_length=1, max_length=120)
    values: dict[str, Any]


# ------------------------------------------------------------------- yardım


def _kernel(request: Request) -> Kernel:
    kernel: Kernel | None = getattr(request.app.state, "kernel", None)
    if kernel is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Çekirdek hazır değil.")
    return kernel


def _config(request: Request) -> Config:
    config: Config | None = getattr(request.app.state, "config", None)
    if config is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Ayarlar hazır değil.")
    return config


def _settings_store(request: Request) -> SettingsStore:
    store: Store | None = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover
        raise HTTPException(status_code=503, detail="Depo hazır değil.")
    return SettingsStore(store)


def _identity(request: Request) -> Identity | None:
    identity: Identity | None = getattr(request.app.state, "identity", None)
    return identity


def _allows(user: CurrentUser, entries: list[str]) -> bool:
    """İlan edilen izinlerden EN AZ BİRİ yeterlidir (deps.requires ile aynı kural)."""
    return any(user.has_permission(*split_permission(entry)) for entry in entries)


def _catalog(kernel: Kernel) -> list[dict[str, Any]]:
    """Çekirdek sekmeleri + modül sekmeleri.

    Modül sekmesi, kaydın kendisinden türer: modül klasörü silinince kayıt da
    yoksa sekme kendiliğinden kaybolur (ADR 0018 §2). Yüklenememiş modülün
    sekmesi DÜŞÜRÜLMEZ — ayarı düzeltmek çoğu zaman tam da o durumda gerekir;
    ekran modülün hâlini ayrıca gösterir.
    """
    tabs = core_tabs()
    for record in sorted(kernel.records.values(), key=lambda item: item.id):
        if not record.settings:
            continue
        tab = module_tab(record.id, record.manifest.path, record.settings)
        tab["state"] = record.state
        tab["reason"] = record.reason
        tabs.append(tab)
    return tabs


def _layers() -> tuple[dict[str, Any], dict[str, Any]]:
    """(dosya katmanı, ortam katmanı).

    Dosya katmanı diskten TAZE okunur: `local.yaml` elle düzenlenebilir ve
    ekranın “bu değer dosyadan geliyor, arayüzden ezildi” cümlesi çalışan
    sürecin belleğindeki eski bir kopyaya değil, dosyanın o anki hâline
    dayanmalıdır.
    """
    return file_layers(), env_layer()


def _dropped_tabs(kernel: Kernel) -> list[dict[str, str]]:
    """Bloğu geçersiz olduğu için sekmesi kurulmayan modüller (K7).

    Sessiz kalınmaz: modülü yazan kişi sekmesinin neden çıkmadığını ekranda
    görür, günlüğü açmak zorunda kalmaz.
    """
    return [
        {"module": record.id, "name": record.manifest.name, "reason": record.settings_error}
        for record in sorted(kernel.records.values(), key=lambda item: item.id)
        if record.settings_error
    ]


def _output_dir(config: Config) -> Path:
    """Çekirdeğin ürettiği dosyaların klasörü — Genel sekmesindeki ayar."""
    configured = str(config.get("files.output_path") or "").strip()
    return report_dir(
        OUTPUT_CATEGORY,
        fallback=config.root / "data" / "exports",
        configured=configured,
    )


def _log_path(config: Config) -> Path:
    return config.path("core.log_path", "data/launcher.log")


def _module_report(kernel: Kernel) -> list[dict[str, Any]]:
    return [
        {
            "id": record.id,
            "name": record.manifest.name,
            "version": record.manifest.version,
            "state": record.state,
            "reason": record.reason,
        }
        for record in sorted(kernel.records.values(), key=lambda item: item.id)
    ]


async def _refresh_config(request: Request) -> None:
    """Depo katmanını çalışan ayara yeniden uygular.

    Bu, "kaydettim ve hemen etkili oldu" demek DEĞİLDİR: platform yetenekleri
    ayarlarını açılışta okuyor ve nesneleri o anda kuruluyor. Burada yapılan,
    bundan sonra ayarı okuyan her yerin (ve bir sonraki GET'in) yeni değeri
    görmesidir; yanıt ayrıca `restartRequired` ile durumu açıkça söyler.
    """
    config = _config(request)
    stored = await _settings_store(request).values()
    request.app.state.config = with_store_layer(config, nest(stored))


# ------------------------------------------------------------------- router


def create_settings_router() -> APIRouter:
    router = APIRouter(tags=["settings"])

    @router.get("/settings")
    async def read_settings(
        request: Request,
        user: CurrentUser = requires(VIEW_PERMISSION),
    ) -> dict[str, Any]:
        kernel = _kernel(request)
        file_layer, env_values = _layers()
        store_values = await _settings_store(request).values()
        can_manage = user.has_permission(*split_permission(MANAGE_PERMISSION))

        tabs: list[dict[str, Any]] = []
        for tab in _catalog(kernel):
            # Çekirdek sekmesini görmek `settings.view` ile gelinmiş olmak
            # demektir; modül sekmesi kendi iznini AYRICA sorar (ADR 0018 §3).
            if tab["kind"] == "module" and not _allows(user, tab["requires"]):
                continue
            view = resolve_tab(
                tab,
                file_layer=file_layer,
                store_values=store_values,
                env_values=env_values,
            )
            view["canWrite"] = can_manage and (
                tab["kind"] == "core" or _allows(user, tab["requires"])
            )
            view["state"] = tab.get("state", "loaded")
            view["reason"] = tab.get("reason", "")
            tabs.append(view)

        config = _config(request)
        return {
            "app": {
                "name": str(config.get("app.name", "Kontrol Merkezi")),
                "version": request.app.version,
            },
            "canManage": can_manage,
            "tabs": tabs,
            "dropped": _dropped_tabs(kernel),
            "outputDir": str(_output_dir(config)),
        }

    @router.put("/settings", dependencies=[requires(MANAGE_PERMISSION)])
    async def write_settings(
        request: Request,
        body: SettingsUpdate,
        user: CurrentUser = requires(VIEW_PERMISSION),
    ) -> dict[str, Any]:
        kernel = _kernel(request)
        tab = next((entry for entry in _catalog(kernel) if entry["id"] == body.tab), None)
        if tab is None:
            raise HTTPException(status_code=404, detail="Böyle bir ayar sekmesi yok.")

        # İKİNCİ KAPI: `settings.manage` yukarıda soruldu, sekmenin kendi izni
        # burada. Biri geçilmeden gövdeye girilmez.
        if tab["kind"] == "module" and not _allows(user, tab["requires"]):
            identity = _identity(request)
            if identity is not None:
                await identity.audit(
                    user.id, "settings.update", result="denied", detail=body.tab
                )
            raise HTTPException(status_code=403, detail="Bu ayar sekmesi için yetkiniz yok.")

        specs = field_specs(tab)
        unknown = [key for key in body.values if key not in specs]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Bu sekmede tanımlı olmayan alan: {', '.join(sorted(unknown))}.",
            )

        _, env_values = _layers()
        settings_store = _settings_store(request)
        identity = _identity(request)
        changed: list[str] = []

        for key, raw in body.values.items():
            spec = specs[key]
            path = str(spec["path"])

            # Ortam değişkeni en üstte: oraya yazmak sessizce etkisiz kalırdı.
            locked, _ = value_at(env_values, path)
            if locked:
                raise HTTPException(
                    status_code=409,
                    detail=f"“{spec['title']}” ortam değişkeninden geliyor; "
                           "arayüzden değiştirilemez.",
                )

            if raw is None:
                # Ezmeyi kaldır: değer dosya katmanına döner.
                await settings_store.clear(path)
            else:
                try:
                    value = validate_value(spec, raw)
                except SettingsError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                await settings_store.put(path, value, actor_id=user.id)
            changed.append(path)

            if identity is not None:
                # DEĞER DENETİM İZİNE YAZILMAZ: ayar sır taşımaz ama yol ve
                # kim/ne zaman yeterlidir; değeri de yazmak izi gereksizce
                # şişirir ve ileride sır tipi eklenirse sızıntı yolu olur.
                await identity.audit(
                    user.id,
                    "settings.update",
                    result="ok",
                    scope=body.tab,
                    detail=f"{path} {'sıfırlandı' if raw is None else 'değiştirildi'}",
                )

        await _refresh_config(request)

        file_layer, env_values = _layers()
        refreshed = resolve_tab(
            tab,
            file_layer=file_layer,
            store_values=await settings_store.values(),
            env_values=env_values,
        )
        refreshed["canWrite"] = True
        refreshed["state"] = tab.get("state", "loaded")
        refreshed["reason"] = tab.get("reason", "")
        log.info("ayar değişti", tab=body.tab, keys=changed, user=user.id)
        return {
            "tab": refreshed,
            "changed": changed,
            # Yetenekler ayarlarını AÇILIŞTA okuyor. "Kaydedildi" demek
            # "etkili oldu" demek değil; ekran bunu kullanıcıya söylemeli.
            "restartRequired": bool(changed),
        }

    # ------------------------------------------------------------- yazıcı

    @router.get("/settings/printer", dependencies=[requires(VIEW_PERMISSION)])
    async def printer_status(request: Request) -> dict[str, Any]:
        """Yazıcı tanılaması. HATA FIRLATMAZ, ANLATIR.

        Yetenek kayıtlı değilse bu bir arıza değildir: ekran "bu makinede
        baskı yok" der ve test düğmesini hiç çizmez.
        """
        registry = getattr(request.app.state, "registry", None)
        printer = registry.try_resolve("printer") if registry is not None else None
        if printer is None:
            return {
                "available": False,
                "reason": "Bu kurulumda yazıcı yeteneği kayıtlı değil.",
                "status": {},
            }
        try:
            state = await printer.status()
        except Exception as failure:  # noqa: BLE001 — yetenek sınırı (K7)
            log.warning("yazıcı durumu okunamadı", error=str(failure))
            return {"available": True, "reason": str(failure), "status": {}}
        return {"available": True, "reason": "", "status": state}

    @router.post("/settings/printer/test-page", dependencies=[requires(MANAGE_PERMISSION)])
    async def printer_test_page(
        request: Request,
        user: CurrentUser = requires(VIEW_PERMISSION),
    ) -> dict[str, Any]:
        """Test sayfası basar.

        “Gönderildi” ile “kâğıt çıktı” aynı şey değildir (km_platform/printer
        başlığındaki ders). Bu yüzden dönen gövde hangi kuyruğa gittiğini ve iş
        numarasını söyler; kullanıcı kâğıdı göremezse nereye bakacağını bilir.
        """
        registry = getattr(request.app.state, "registry", None)
        printer = registry.try_resolve("printer") if registry is not None else None
        if printer is None:
            raise HTTPException(status_code=503, detail="Yazıcı yeteneği kayıtlı değil.")

        config = _config(request)
        try:
            from km_core.files.reports import ExportError, build_pdf
        except ImportError as error:  # pragma: no cover — çekirdekle gelir
            raise HTTPException(status_code=503, detail="Çıktı üreteci yüklenemedi.") from error

        stamp = datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
        try:
            pdf = build_pdf(
                title="Yazıcı test sayfası",
                subtitle=f"{config.get('app.name', 'Kontrol Merkezi')} · {stamp}",
                sections=[
                    {
                        "kind": "tiles",
                        "title": "Baskı ayarı",
                        "tiles": [
                            ("Kâğıt boyutu", str(config.get("platform.printer.media", "A4"))),
                            ("Sabitlenen kuyruk",
                             str(config.get("platform.printer.default_printer") or "otomatik")),
                            ("Tarih", stamp),
                        ],
                    },
                    {
                        "kind": "note",
                        "text": "Türkçe karakter denetimi: ğ Ğ ı İ ö Ö ş Ş ü Ü ç Ç. "
                                "Bu satır düzgün okunuyorsa yazı tipi doğru gömülmüştür.",
                    },
                    {
                        "kind": "note",
                        "text": "Bu sayfa çıkmadıysa baskı kuyruğu “tamamlandı” dese bile "
                                "cihaza ulaşamıyor olabilir; Yazıcı sekmesindeki kuyruk "
                                "listesine bakın.",
                    },
                ],
            )
        except ExportError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

        target = _output_dir(config) / f"yazici-test-{datetime.now().astimezone():%Y-%m-%d-%H%M%S}.pdf"
        write_private(target, pdf)

        try:
            result = await printer.print_file(target, title="Yazıcı test sayfası")
        except Exception as failure:
            # Yetenek sınırı (K7): yazıcı patlarsa ekran nedenini görür,
            # çekirdek ayakta kalır. Mesaj olduğu gibi geçer — `PrinterError`
            # cümleleri (ipp-usb tuzağı, kâğıt uyuşmazlığı) kullanıcıya ne
            # yapacağını söyleyen en iyi metindir.
            raise HTTPException(status_code=502, detail=str(failure)) from failure

        identity = _identity(request)
        if identity is not None:
            await identity.audit(user.id, "settings.printer_test", result="ok",
                                 detail=str(result.get("printer") or ""))
        return {"ok": True, "path": str(target), **result}

    # -------------------------------------------------------- güncelleme

    @router.get("/settings/update", dependencies=[requires(VIEW_PERMISSION)])
    async def update_state(request: Request) -> dict[str, Any]:
        """Çekirdeğin sürümü ve güncellemenin NEREDE çalıştığı.

        GÜNCELLEYİCİ KABUKTADIR, BU SÜREÇTE DEĞİL — ve bu gövde bunu açıkça
        söyler. Yerine konacak dosyalar kabuğun ikilisi ve yanındaki
        kaynaklardır; sidecar tam da o ağacın içinden çalışıyor, kendi
        altındaki zemini değiştiremez. Bu yüzden `canCheck`/`canInstall`
        BİLEREK yanlıştır: burada böyle bir uç yok ve olmayacak.

        Uygulama sürümünü de kabuk söyler (`update_support`): güncelleyicinin
        karşılaştırdığı sayı `tauri.conf.json` içindeki sürümdür. Buradaki
        `version` çekirdeğin kendi sürümüdür; ekran ikisini ayrı ayrı yazar,
        çünkü paket dışı bir kurulumda (depodan koşarken) bu ikisi ayrışır ve
        tek bir sayı göstermek yanlış olurdu.

        UÇ ADRESİ ARTIK BURADAN GELMİYOR. Eski gövde `checkPath`/`installPath`
        veriyordu ve gerekçesi doğruydu: adresi veren, o adresi karşılayan
        taraf olmalı. Bugün karşılayan taraf kabuk; komutların adı da onun
        ikilisine gömülü. Adı burada ikinci kez yazmak, hiçbir zaman
        denetlenmeyen bir kopya tutmak olurdu.
        """
        return {
            # Çekirdeğin sürümü. Kabuk kendi sürümünü ayrıca bildirir.
            "version": request.app.version,
            # Bu SÜREÇTE denetleme/kurulum ucu yok (yukarıdaki gerekçe).
            "canCheck": False,
            "canInstall": False,
            "checkPath": None,
            "installPath": None,
            # Güncellemeyi kim yürütüyor: kabuk (Tauri güncelleyicisi).
            "handledBy": "shell",
            "source": "GitHub Releases",
            "reason": "Güncelleme uygulamanın kabuğunda çalışır: sürüm listesi "
                      "GitHub Releases'ten okunur, paket imzasıyla birlikte "
                      "indirilir ve kurulum kullanıcının onayıyla yapılır. "
                      "Çekirdek kendi altındaki dosyaları değiştiremeyeceği "
                      "için burada bir kurulum ucu yoktur.",
        }

    # ----------------------------------------------------------- tanılama

    @router.get("/settings/diagnostics", dependencies=[requires(VIEW_PERMISSION)])
    async def diagnostics(request: Request) -> dict[str, Any]:
        """Teknik günlük ve künye — MASKELENMİŞ.

        Ekranda da maskelenir: destek paketiyle aynı süzgeçten geçer. Ekranda
        ham, dosyada maskeli olsaydı iki farklı gerçek doğardı ve kullanıcı
        ekrandaki satırı kopyalayıp yapıştırarak maskelemeyi delerdi.
        """
        config = _config(request)
        kernel = _kernel(request)
        path = _log_path(config)
        raw = read_log_tail(path, lines=LOG_LINES)
        return {
            "logPath": str(path),
            "logExists": path.is_file(),
            "masked": True,
            "lines": mask_text(raw).splitlines() if raw else [],
            "summary": app_summary(version=request.app.version, modules=_module_report(kernel)),
            "outputDir": str(_output_dir(config)),
        }

    @router.post("/settings/support-bundle", dependencies=[requires(MANAGE_PERMISSION)])
    async def support_bundle(
        request: Request,
        user: CurrentUser = requires(VIEW_PERMISSION),
    ) -> dict[str, Any]:
        """Destek paketi üretir ve nereye yazıldığını söyler.

        Paket her zaman maskelidir: maskeleme `bundle_bytes` içindedir ve
        atlanamaz (K8). Yazma `settings.manage` ister — dosya üretmek, ekranda
        okumaktan bir adım ötesidir.
        """
        config = _config(request)
        kernel = _kernel(request)

        rows = await _settings_store(request).rows()
        summary = app_summary(
            version=request.app.version,
            modules=_module_report(kernel),
            extra={"ayarKlasoru": str(_output_dir(config))},
        )
        payload = bundle_bytes(
            summary=summary,
            settings=[
                {
                    "anahtar": str(row["key"]),
                    "deger": row["value"],
                    "kim": row.get("updated_by"),
                    "neZaman": row.get("updated_at"),
                }
                for row in rows
            ],
            log_text=read_log_tail(_log_path(config), lines=2000),
        )

        target = _output_dir(config) / bundle_name()
        write_private(target, payload)

        identity = _identity(request)
        if identity is not None:
            await identity.audit(user.id, "settings.support_bundle", result="ok",
                                 detail=target.name)
        log.info("destek paketi üretildi", path=str(target), bytes=len(payload))
        return {
            "name": target.name,
            "path": str(target),
            "bytes": len(payload),
            "masked": True,
        }

    return router
