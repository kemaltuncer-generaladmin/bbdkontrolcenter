"""Alan servisleri ve sınırlar — kalıcı kapı (ADR 0033 §6).

Depodaki bütün modüller taranır (`tools/arch_scan.py`): başka modülün kodunu
import eden, başka modülün ya da çekirdeğin tablosuna SQL atan, başka modülün
HTTP önekini çağıran, alanı/veri sınıfı eksik ya da yanlış olan modül bu testi
düşürür. Bugünkü ihlaller `tools/boundary-exceptions.yaml` listesindedir;
liste yalnız küçülür (giderilen ihlalin satırı da silinmek zorundadır).

İkinci yarı tarayıcının kendisini sahte bir depo üzerinde dener: kapı "geçti"
diyorsa gerçekten bakabildiği için geçmiş olmalı.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import arch_scan

from km_core.contracts.manifest import read_manifest


@pytest.fixture(scope="module")
def repo_scan() -> arch_scan.Scan:
    return arch_scan.scan(ROOT)


def _describe(findings: list[arch_scan.Finding]) -> str:
    return "\n".join(f"  {f.rule}: {f.module} → {f.target} ({f.file}:{f.line}) {f.detail}"
                     for f in findings)


# ------------------------------------------------------------- depo


def test_yeni_sinir_ihlali_yok(repo_scan: arch_scan.Scan) -> None:
    verdict = arch_scan.judge(repo_scan, arch_scan.load_exceptions(ROOT))
    assert not verdict.new, (
        "Yeni sınır ihlali — başka alanla yetenek (provides/consumes) ya da olay "
        "üzerinden konuşun (ADR 0033):\n" + _describe(verdict.new))
    assert not verdict.stale, (
        "Giderilmiş ihlalin istisnası listede kalmış; tools/boundary-exceptions.yaml'dan silin: "
        + ", ".join(str(item.key) for item in verdict.stale))


def test_istisnalar_gerekceli_ve_tekil() -> None:
    items = arch_scan.load_exceptions(ROOT)
    assert items, "istisna listesi okunamadı"
    keys = [item.key for item in items]
    assert len(keys) == len(set(keys)), "aynı istisna iki kez yazılmış"
    for item in items:
        assert item.reason and item.plan, f"{item.key}: reason ve plan zorunlu"
        # Alan/sınıf eksikliği istisnayla geçiştirilmez — manifest doldurulur.
        assert item.rule != "manifest", f"{item.key}: manifest bulgusu istisnaya alınamaz"


def test_her_modul_bir_alanda(repo_scan: arch_scan.Scan) -> None:
    catalog = repo_scan.catalog
    assert len(repo_scan.modules) >= 50
    for module in repo_scan.modules.values():
        assert module.domain in catalog.domains, f"{module.id}: alan '{module.domain}' katalogda yok"
        assert module.classification in catalog.classifications, module.id
    # Katalogdaki her alanın en az bir modülü var (ölü alan tutulmaz).
    used = {module.domain for module in repo_scan.modules.values()}
    assert set(catalog.domains) == used


def test_manifest_alani_cekirdekte_okunur() -> None:
    manifest = read_manifest(ROOT / "modules" / "bell", ROOT / "docs" / "schemas" / "module.schema.json")
    assert manifest.domain == "bell"
    assert manifest.data_classification == "personal"


# ---------------------------------------------------- tarayıcının kendisi


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    _write(tmp_path / "docs/architecture/domains.yaml", """
        classifications: {internal: "", personal: ""}
        domains:
          alpha: {title: A, prefixes: [alpha], classification: internal}
          beta: {title: B, prefixes: [beta], classification: personal}
    """)
    _write(tmp_path / "backend/src/km_core/store/schema.py", '''
        SQL = "CREATE TABLE IF NOT EXISTS users (id TEXT)"
        NOTE = "modül b'nin izni b.view"
    ''')
    _write(tmp_path / "modules/a/module.yaml", """
        id: a
        domain: alpha
        data_classification: personal
        http: {prefix: /api/a}
        provides: [{capability: wrong.cap, contract: x}]
    """)
    _write(tmp_path / "modules/b/module.yaml", """
        id: b
        domain: beta
        data_classification: personal
        http: {prefix: /api/b}
    """)
    _write(tmp_path / "modules/a/backend/service.py", '''
        """Belge dizgisinde mod_b_items geçmesi ihlal değildir."""
        from modules.b.backend import thing
        from km_core.store import Store

        USERS = "users"

        def run(store):
            # yorumda mod_b_items da ihlal değildir
            store.fetch_all(f"SELECT * FROM mod_b_items JOIN {USERS} u ON 1=1")
            store.fetch_all(f"SELECT * FROM {store.table('own')}")
    ''')
    _write(tmp_path / "modules/a/ui/panel/index.js", """
        import { kit } from '../../ui-kit/kit.js';
        import { own } from './own.js';
        import { other } from '../../b/index.js';
        // fetch('/api/b/yorum') sayılmaz
        export const load = (ctx) => ctx.api('/api/b/list');
    """)
    _write(tmp_path / "modules/a/ui/panel/sub/screen.js", """
        import { common } from '../index.js';
        import { kit } from '../../../ui-kit/kit.js';
        import { leak } from '../../../b/index.js';
    """)
    _write(tmp_path / "modules/a/backend/migrations/001.sql", """
        -- mod_b_items yorumda
        CREATE TABLE IF NOT EXISTS mod_a_items (id TEXT);
    """)
    _write(tmp_path / "modules/a/tests/test_x.py", "from modules.b import y\n")
    return tmp_path


def test_tarayici_ihlalleri_bulur(fake_repo: Path) -> None:
    result = arch_scan.scan(fake_repo)
    keys = {finding.key for finding in result.findings}
    assert ("module-import", "a", "b") in keys
    assert ("core-import", "a", "km_core") in keys
    assert ("foreign-table", "a", "b") in keys
    assert ("core-table", "a", "users") in keys
    assert ("module-import", "a", "../../b/index.js") in keys
    assert ("foreign-api", "a", "b") in keys
    assert ("naming", "a", "provides:wrong.cap") in keys
    assert ("manifest", "a", "data_classification:personal") in keys  # alfa'nın sınırı internal
    assert ("core-knows-module", "km_core", "b") in keys
    # Belge dizgisi, yorum, test klasörü, kit ve kendi dosyası sayılmaz.
    foreign = [f for f in result.findings if f.rule == "foreign-table"]
    assert len(foreign) == 1 and foreign[0].file.endswith("service.py")
    assert not any(f.file.endswith("test_x.py") for f in result.findings)
    assert ("module-import", "a", "../../../b/index.js") in keys  # alt klasörden başka modüle
    assert not any(f.target in ("../../ui-kit/kit.js", "./own.js", "../index.js", "../../../ui-kit/kit.js")
                   for f in result.findings)
    assert len([f for f in result.findings if f.rule == "foreign-api"]) == 1
    assert not any(f.module == "b" for f in result.findings)


def test_istisna_yeni_ve_eski_ayrimi(fake_repo: Path) -> None:
    result = arch_scan.scan(fake_repo)
    exceptions = [
        arch_scan.Exception_("module-import", "a", "b", "neden", "plan"),
        arch_scan.Exception_("foreign-table", "a", "zzz", "neden", "plan"),  # artık yok
        arch_scan.Exception_("core-table", "a", "users", "", ""),  # gerekçesiz
    ]
    verdict = arch_scan.judge(result, exceptions)
    assert not verdict.ok
    assert ("module-import", "a", "b") not in {f.key for f in verdict.new}
    assert [item.target for item in verdict.stale] == ["zzz"]
    assert [item.target for item in verdict.malformed] == ["users"]
