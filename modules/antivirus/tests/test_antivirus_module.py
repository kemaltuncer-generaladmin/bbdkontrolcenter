"""Modül sözleşmesi — manifest, kayıt (`register`) ve zamanlanmış görevler.

Manifest SABİT bir sözleşmedir; burada onun hem şemaya uyduğu hem de kodun
ilan edilenle aynı şeyi yaptığı sınanır. Manifestte yazan ama kodda olmayan
bir handler, hiç koşmayan bir gece taraması demektir ve sessizce kaybolur.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from antivirus_backend import module as entry

from km_core.bus.bus import EventBus
from km_core.contracts.manifest import read_manifest
from km_core.contracts.module import ModuleContext
from km_core.kernel.module_settings import SettingsSchema
from km_core.kernel.platforms import runs_on
from km_core.registry.registry import Registry

MODULE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_ROOT.parents[1]
SCHEMA = REPO_ROOT / "docs" / "schemas" / "module.schema.json"


def manifest_data() -> dict[str, Any]:
    return yaml.safe_load((MODULE_ROOT / "module.yaml").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ manifest


def test_manifest_semaya_uyar() -> None:
    manifest = read_manifest(MODULE_ROOT, SCHEMA)

    assert manifest.id == "antivirus"
    assert manifest.enabled is True
    assert manifest.http is not None
    assert manifest.http.prefix == "/api/antivirus"
    assert manifest.http.requires == ["antivirus.view"]


def test_yalniz_linuxta_yuklenir() -> None:
    """ADR 0022: tarama ekranı Windows'ta da macOS'ta da bulunmaz."""
    data = manifest_data()

    assert data["platforms"] == ["linux"]
    assert runs_on(data, "linux") is True
    assert runs_on(data, "windows") is False
    assert runs_on(data, "macos") is False


def test_sozlesmedeki_olaylar_ilan_edilmis() -> None:
    published = {item["name"] for item in manifest_data()["events"]["publishes"]}

    assert published == {
        "antivirus.scan_started",
        "antivirus.scan_completed",
        "antivirus.threat_found",
        "antivirus.signatures_stale",
    }


def test_zamanlanmis_gorev_handlerlari_gercekten_var() -> None:
    """Manifestteki `handler` yolu koda çözülmeli; yoksa iş hiç koşmaz."""
    for task in manifest_data()["tasks"]:
        path, _, attribute = task["handler"].partition(":")
        file_path = MODULE_ROOT / Path(*path.split(".")).with_suffix(".py")
        assert file_path.is_file(), f"{task['name']}: {file_path} yok"

        loaded = __import__(f"antivirus_backend.{path.partition('.')[2]}",
                            fromlist=[attribute])
        assert callable(getattr(loaded, attribute))


def test_ayar_sekmesi_semaya_uyar() -> None:
    """ADR 0018: geçersiz `settings` bloğu sekmeyi düşürür, modülü değil."""
    block, error = SettingsSchema.load(SCHEMA).validate(manifest_data())

    assert error == ""
    assert block is not None
    assert block["requires"] == ["antivirus.manage"]
    keys = {field["key"] for group in block["groups"] for field in group["fields"]}
    assert keys == {"schedule", "quick_paths", "exclude_paths", "signature_max_age_hours"}


def test_ayar_anahtarlari_varsayilan_dosyada_karsiliksiz_degil() -> None:
    """Sekmedeki her anahtarın `config/default.yaml` içinde bir evi olmalı.

    Olmasaydı ekrandan yazılan değer, hiçbir kodun okumadığı bir ad alanına
    düşerdi ve kullanıcı ayarın neden işlemediğini bulamazdı.
    """
    block = manifest_data()["settings"]
    defaults = yaml.safe_load(
        (MODULE_ROOT / "config" / "default.yaml").read_text(encoding="utf-8"))

    for group in block["groups"]:
        for field in group["fields"]:
            assert field["key"] in defaults, f"{field['key']} default.yaml içinde yok"


# --------------------------------------------------------------- register


class FakeScheduler:
    """`scheduler` yeteneğinin testteki karşılığı."""

    def __init__(self) -> None:
        self.plans: dict[str, Any] = {}

    def set_plan(self, owner: str, triggers: list[Any], handler: Any) -> int:
        self.plans[owner] = (triggers, handler)
        return len(triggers)


def build_context(store: Any, log: Any, config: dict[str, Any],
                  scheduler: FakeScheduler) -> ModuleContext:
    registry = Registry()
    registry.register("scheduler", scheduler, provider="platform")
    return ModuleContext(
        module_id="antivirus",
        module_path=MODULE_ROOT,
        config=config,
        store=store,
        log=log,
        _registry=registry,
        _bus=EventBus(),
        _declared_provides={"antivirus.scan"},
        _declared_consumes={"scheduler", "notify", "ssh", "secrets"},
    )


@pytest.fixture
async def registered(store: Any, log: Any, make_binary: Callable[..., str],
                     signature_dir: Callable[..., str]) -> Any:
    """Modülü gerçekten kaydeder; arka plan döngüsü test sonunda kapatılır.

    Fixture ASENKRON: `register()` imza denetimi döngüsünü çalışan olay
    döngüsüne bırakır ve döngü olmadan kurulum yapılamaz — tıpkı çekirdekte
    olduğu gibi.
    """
    scheduler = FakeScheduler()
    config = {
        "clamdscan": make_binary("clamdscan", "exit 0"),
        "clamscan": "km-yok-clamscan",
        "database_path": signature_dir(),
        "quick_paths": ["/tmp"],
        "full_paths": ["/tmp"],
        "schedule": "0 4 * * *",
    }
    context = build_context(store, log, config, scheduler)
    entry.register(context)

    yield context, scheduler

    for task in asyncio.all_tasks():
        if task.get_name() == "antivirus-signatures":
            task.cancel()
    entry._LIVE = None


async def test_register_router_yetenek_ve_takvim_kurar(registered: Any) -> None:
    context, scheduler = registered

    # Router bağlandı.
    assert len(context.routers) == 1
    # Yetenek deftere yazıldı (manifestte `provides` ile ilan edilmiş).
    provider = context._registry.resolve("antivirus.scan")
    assert hasattr(provider, "scan")
    assert hasattr(provider, "last")
    # Takvim manifestteki cron'dan değil, AYARDAN kuruldu.
    triggers, handler = scheduler.plans["antivirus"]
    assert len(triggers) == 7
    assert {item.time for item in triggers} == {"04:00"}
    assert callable(handler)


async def test_register_agir_is_yapmaz(registered: Any) -> None:
    """`register` bildirim aşamasıdır: tarama başlamaz, kayıt yazılmaz."""
    service = entry.live()

    assert service is not None
    assert (await service.state())["active"] is None
    assert await service.last() is None


# ------------------------------------------------------- zamanlanmış işler


async def test_gorevler_modul_yokken_sessizce_atlar() -> None:
    """Modül yüklenmemişken koşan görev patlamaz, nedenini söyler (K7)."""
    from antivirus_backend.tasks import scan as scan_task
    from antivirus_backend.tasks import signatures as signature_task

    previous = entry._LIVE
    entry._LIVE = None
    try:
        assert (await scan_task.run_scheduled())["ok"] is False
        assert (await signature_task.check())["ok"] is False
    finally:
        entry._LIVE = previous


async def test_zamanlanmis_imza_denetimi_canli_servisi_kullanir(registered: Any) -> None:
    from antivirus_backend.tasks import signatures as signature_task

    result = await signature_task.check()

    assert result["ok"] is True
    assert "ageHours" in result


def test_modul_yalniz_km_sdk_import_eder() -> None:
    """K2/K3: modül `km_core`, `km_platform` ya da başka bir modülü import edemez.

    Yalnız IMPORT SATIRLARINA bakılır: yorumda `km_platform/audio/player.py`
    desenine gönderme yapmak bir bağımlılık değil, bir açıklamadır.
    """
    for path in sorted((MODULE_ROOT / "backend").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if not text.startswith(("import ", "from ")):
                continue
            where = f"{path.name}:{number}"
            assert "km_core" not in text, f"{where}: km_core importu (K2)"
            assert "km_platform" not in text, f"{where}: km_platform importu (K2)"
            assert not text.startswith("from modules."), f"{where}: modül importu (K3)"


def test_test_paketi_kendi_adiyla_yuklenir() -> None:
    """Ad çakışması olmasın: `backend` adı elli modülde ortak."""
    assert "antivirus_backend" in sys.modules
