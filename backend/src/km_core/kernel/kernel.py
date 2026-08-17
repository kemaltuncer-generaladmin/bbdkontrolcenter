"""Modül keşfi, sıralaması ve yaşam döngüsü (ARCHITECTURE.md §5).

Sıra: keşfet → doğrula → platforma göre ele → bağımlılığa göre diz → göçleri
uygula → `register(ctx)`.

K7 — İzolasyon: her adım modül düzeyinde sınırlanır. Patlayan modül `failed`
işaretlenir, nedeni saklanır; çekirdek ve diğer modüller ayakta kalır.
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from km_core.bus.bus import EventBus
from km_core.config.loader import Config
from km_core.contracts.manifest import Manifest, ManifestError, read_manifest
from km_core.contracts.module import ModuleContext, ScopedStore
from km_core.kernel.module_settings import SettingsSchema
from km_core.kernel.platforms import current_platform, runs_on, skip_note
from km_core.registry.registry import Registry
from km_core.store.base import StoreLike

log = structlog.get_logger("km.kernel")

SDK_VERSION = "0.1.0"


@dataclass(slots=True)
class ModuleRecord:
    manifest: Manifest
    state: str = "pending"      # pending | loaded | disabled | failed
    reason: str = ""
    context: ModuleContext | None = None
    # Doğrulanmış `settings` bloğu (ADR 0018). Blok yoksa ya da geçersizse
    # None kalır: modül SEKMESİZ yüklenir, nedeni `settings_error` içindedir.
    settings: dict[str, Any] | None = None
    settings_error: str = ""

    @property
    def id(self) -> str:
        return self.manifest.id


class Kernel:
    def __init__(
        self,
        config: Config,
        store: StoreLike,
        registry: Registry,
        bus: EventBus,
        platform: str | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.registry = registry
        self.bus = bus
        # Platform dışarıdan verilebilir: eleme davranışı, üzerinde koşulan
        # makineye bağlı kalmadan sınanabilsin (ADR 0022).
        self.platform = platform or current_platform()
        self.records: dict[str, ModuleRecord] = {}
        self.problems: list[str] = []
        # Bu platformda çalışmadığı için hiç yüklenmeyen modüller. `problems`
        # değildir: eleme bir arıza değil, ilan edilmiş bir karardır.
        self.skipped: list[str] = []

    # ---------------------------------------------------------------- keşif

    def discover(self, modules_dir: Path, schema_path: Path) -> None:
        # ŞEMA OKUNAMIYORSA UYGULAMA YİNE AYAĞA KALKAR (ARCHITECTURE §5).
        #
        # `read_manifest` şemayı her modül için yeniden okur ve oradaki
        # `read_text()` korumasızdır; dosya yoksa `FileNotFoundError` atar.
        # Aşağıdaki döngü yalnız `ManifestError` yakaladığı için o hata dışarı
        # kaçar, `lifespan` içinden geçip UYGULAMAYI HİÇ AÇTIRMAZDI.
        #
        # v0.1.1 paketinde tam olarak bu oldu: `docs/schemas/module.schema.json`
        # kaynak listesine konmamıştı, kurulu uygulamada çekirdek açılırken
        # patlıyor, 8787'de dinleyen kimse olmuyor ve kabuk "Çekirdeğe
        # ulaşılamadı — bağlantı reddedildi" diyordu. Şema artık pakete giriyor;
        # bu denetim ikinci kapıdır, çünkü tek bir eksik dosyanın bütün
        # uygulamayı kapatması sözleşmeye aykırıdır.
        if not schema_path.is_file():
            sorun = f"manifest şeması bulunamadı: {schema_path}"
            self.problems.append(sorun)
            log.error("manifest şeması yok — hiçbir modül yüklenmeyecek",
                      path=str(schema_path))
            return

        settings_schema = SettingsSchema.load(schema_path)
        for manifest_file in sorted(modules_dir.glob("*/module.yaml")):
            try:
                manifest = read_manifest(manifest_file.parent, schema_path)
            except ManifestError as error:
                # Geçersiz manifest yalnızca o modülü düşürür.
                self.problems.append(str(error))
                log.warning("manifest reddedildi", detail=str(error))
                continue

            # Platform elemesi KEŞİFTE olur: elenen modül kayda hiç girmez,
            # dolayısıyla router'ı monte edilmez, göçü koşmaz, görevi
            # planlanmaz ve menüye girmez (ADR 0022 §2). Çekirdek modül adına
            # bakmaz; `platforms` alanını veri olarak okur (K1).
            if not runs_on(manifest.raw, self.platform):
                note = skip_note(manifest.id, manifest.raw)
                self.skipped.append(note)
                # Eleme sessiz olmaz: ekranını arayan yönetici nedenini bulur.
                log.info("modül bu platformda çalışmaz",
                         module=manifest.id, running=self.platform, detail=note)
                continue

            record = ModuleRecord(manifest)
            record.settings, record.settings_error = settings_schema.validate(manifest.raw)
            if record.settings_error:
                # Ayar bloğunun bozukluğu modülü düşürmez (K7).
                log.warning("ayar sekmesi kurulmadı",
                            module=manifest.id, detail=record.settings_error)
            self.records[manifest.id] = record

    # ------------------------------------------------------------- sıralama

    def order(self) -> list[str]:
        """Bağımlılığa göre topolojik sıra. Döngü → ilgili modüller düşer."""
        pending = {
            record.id: [dep for dep in record.manifest.depends if dep in self.records]
            for record in self.records.values()
        }
        ordered: list[str] = []

        while pending:
            ready = sorted(mid for mid, deps in pending.items() if not deps)
            if not ready:
                for mid in sorted(pending):
                    self._fail(mid, "bağımlılık döngüsü")
                break
            for mid in ready:
                ordered.append(mid)
                del pending[mid]
            for deps in pending.values():
                for mid in ready:
                    if mid in deps:
                        deps.remove(mid)

        return ordered

    # ------------------------------------------------------------- yükleme

    async def load_all(self) -> None:
        for module_id in self.order():
            record = self.records[module_id]
            try:
                await self._load(record)
            except Exception as error:  # noqa: BLE001 — modül sınırı
                self._fail(module_id, f"{type(error).__name__}: {error}")
                log.error("modül yüklenemedi", module=module_id, error=str(error))

    async def _load(self, record: ModuleRecord) -> None:
        manifest = record.manifest

        if not manifest.enabled:
            record.state = "disabled"
            record.reason = "manifestte enabled: false"
            return

        if not _sdk_matches(manifest.sdk, SDK_VERSION):
            record.state = "disabled"
            record.reason = f"SDK uyumsuz: {manifest.sdk} ≠ {SDK_VERSION}"
            return

        # Bağımlı olduğu modül düştüyse bu da yüklenmez.
        for dependency in manifest.depends:
            dependant = self.records.get(dependency)
            if dependant is None or dependant.state != "loaded":
                record.state = "disabled"
                record.reason = f"bağımlılık yüklenmedi: {dependency}"
                return

        # Zorunlu yetenek eksikse modül devre dışı — nedeni raporlanır.
        for consumed in manifest.consumes:
            if not consumed.optional and not self.registry.has(consumed.capability):
                record.state = "disabled"
                record.reason = f"yetenek yok: {consumed.capability}"
                return

        await self._migrate(manifest)

        module = _import_module(manifest)
        entry_attr = manifest.entrypoint.split(":")[-1]
        register = getattr(module, entry_attr, None)
        if not callable(register):
            raise TypeError(f"{manifest.entrypoint} bulunamadı")

        context = ModuleContext(
            module_id=manifest.id,
            module_path=manifest.path,
            config=self.config.module_config(manifest.id, _module_defaults(manifest)),
            store=ScopedStore(self.store, manifest.id),
            log=structlog.get_logger(f"km.module.{manifest.id}"),
            _registry=self.registry,
            _bus=self.bus,
            _declared_provides={entry.capability for entry in manifest.provides},
            _declared_consumes={entry.capability for entry in manifest.consumes},
        )

        result = register(context)
        if hasattr(result, "__await__"):
            await result

        record.context = context
        record.state = "loaded"
        log.info("modül yüklendi", module=manifest.id, routers=len(context.routers))

    async def _migrate(self, manifest: Manifest) -> None:
        if not manifest.migrations:
            return
        directory = manifest.path / manifest.migrations
        if not directory.is_dir():
            return

        applied = await self.store.applied_migrations(manifest.id)
        for path in sorted(directory.glob("*.sql")):
            if path.name in applied:
                continue
            await self.store.apply_migration(manifest.id, path.name, path.read_text(encoding="utf-8"))
            log.info("göç uygulandı", module=manifest.id, migration=path.name)

    def _fail(self, module_id: str, reason: str) -> None:
        record = self.records.get(module_id)
        if record is None:
            return
        record.state = "failed"
        record.reason = reason
        # Yarım kalmış kayıtları temizle: düşen modül arkasında yetenek bırakmaz.
        self.registry.unregister_provider(module_id)
        self.bus.unsubscribe_all(module_id)

    # ---------------------------------------------------------------- rapor

    def loaded(self) -> list[ModuleRecord]:
        return [record for record in self.records.values() if record.state == "loaded"]

    def report(self) -> list[dict[str, Any]]:
        return [
            {
                "id": record.id,
                "name": record.manifest.name,
                "version": record.manifest.version,
                "state": record.state,
                "reason": record.reason,
                "provides": [entry.capability for entry in record.manifest.provides],
                "ui": _reported_ui(record.manifest),
                "permissions": [
                    {
                        "key": permission.key,
                        "description": permission.description,
                        "scoped": permission.scoped,
                        "destructive": permission.destructive,
                        "default_roles": permission.default_roles,
                    }
                    for permission in record.manifest.permissions
                ],
            }
            for record in sorted(self.records.values(), key=lambda item: item.id)
        ]


# ------------------------------------------------------------------ yardım


def _reported_ui(manifest: Manifest) -> dict[str, Any]:
    """Rapora giren `ui` bloğu: diskte OLMAYAN panel girişi ilan sayılmaz.

    BULUNAN HATA (2026-08-16). Aynı soruya iki yerden iki farklı yanıt
    veriliyordu. `tools/build-ui-registry.py` → `copy_panel()` dosya yoksa
    `None` döner, kayıt defterine `entry` hiç yazılmaz; kabuk `entry` boş
    görünce "ekranı henüz yok" diyen GRİ kartı çizer — bu bir arıza değil,
    bir eksiktir. Çalışma anında ise burası ham manifesti olduğu gibi
    döndürüyordu: kabuk var olmayan dosya için `import()` deniyor, hata
    yakalanıp yığın iziyle KIRMIZI arıza kartı çiziliyordu. Kodlanmamış
    iskelet modüller bozukmuş gibi görünüyordu.

    Kural GENELDİR ve modül bilmez (K1): "bildirilmiş ama diskte olmayan
    giriş, giriş değildir." Modül adı geçmez, modüle özel dal yoktur.

    Hata kabukta yakalanıp geri düşülerek KAPATILMAZ: o çözüm "dosya yok" ile
    "panelde sözdizimi hatası var" durumlarını ayırt edemez ve ikincisi
    kırmızıyı gerçekten hak eder. Gerekçe: ADR 0015.
    """
    ui = manifest.ui
    entry = ui.get("entry")
    if not entry or (manifest.path / str(entry)).is_file():
        return ui
    # Manifest nesnesi bozulmasın: yalnız rapor kopyasından düşürülür.
    trimmed = dict(ui)
    del trimmed["entry"]
    return trimmed


def _module_defaults(manifest: Manifest) -> dict[str, Any]:
    """Modülün kendi `config/default.yaml` dosyası."""
    import yaml

    path = manifest.path / "config" / "default.yaml"
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _import_module(manifest: Manifest) -> Any:
    """`backend.module:register` giriş noktasını yükler.

    Her modül `km_mod_<id>` adında KENDİ paketi olarak kaydedilir ve dosyaları
    o paketin altından çözülür. Böylece:
      · iki modülün `backend/` klasörü birbirini gölgelemez,
      · modül içinde `from .client import X` göreli importu çalışır,
      · modül `sys.path`e sızmaz.
    """
    module_path, _, _ = manifest.entrypoint.partition(":")
    file_path = manifest.path / Path(*module_path.split(".")).with_suffix(".py")
    if not file_path.is_file():
        raise FileNotFoundError(f"giriş dosyası yok: {file_path.relative_to(manifest.path)}")

    package_name = f"km_mod_{manifest.id}"
    if package_name not in sys.modules:
        package = types.ModuleType(package_name)
        # __path__ verilince altındaki klasörler ad alanı paketi olarak bulunur.
        package.__path__ = [str(manifest.path)]
        sys.modules[package_name] = package

    return importlib.import_module(f"{package_name}.{module_path}")


_RANGE = re.compile(r"^(>=|<=|==|<|>)\s*([0-9.]+)$")


def _version_tuple(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def _sdk_matches(spec: str, version: str) -> bool:
    """'>=0.1,<1.0' biçimini denetler. Anlaşılmayan ifade uyumsuz sayılır."""
    current = _version_tuple(version)
    for part in spec.split(","):
        match = _RANGE.match(part.strip())
        if match is None:
            return False
        operator, raw = match.groups()
        target = _version_tuple(raw)
        # Karşılaştırma aynı uzunlukta yapılır: 0.1 ↔ 0.1.0
        left = current[: len(target)] if len(target) < len(current) else current
        right = target
        if operator == ">=" and not left >= right:
            return False
        if operator == ">" and not left > right:
            return False
        if operator == "<=" and not left <= right:
            return False
        if operator == "<" and not left < right:
            return False
        if operator == "==" and left != right:
            return False
    return True
