"""Akademi API sözleşmesinden vekil izin listesini üretir.

KAYNAK TEK: `bbd-akademi/docs/api/openapi.yaml` (bağlayıcı sözleşme). Liste
elle yazılmaz; sözleşme değişince bu betik yeniden koşturulur ve üretilen
`backend/allowlist/` paketi commit'lenir. Test (`tests/test_akademi_allowlist.py`)
sözleşme erişilebilirse ikisinin AYNI olduğunu da doğrular.

PAKET ALT ALAN BAŞINA DOSYADIR (ADR 0033 Aşama 2): `allowlist/<alan>.py`
(koçluk, ders programı, sınav …; eşleme aşağıda `AREAS`), `__init__.py`
hepsini `OPERATIONS` adıyla TEK demette, üreticinin sırasıyla (yol, yöntem)
birleştirir — dışarıdan görünen yüz (`from .allowlist import OPERATIONS`)
değişmedi. Eşlemede olmayan yeni bir yol kesimi `misc.py`'ye düşer.

Listeye girenler (sözleşme `docs/api/README.md` §8):

  · bütün `/v1/admin/*` işlemleri (her biri `x-surface: km | both` taşır)
  · `/v1/students/{studentId}/*` altındaki, entegrasyon anahtarıyla
    çağrılabilen (`security` içinde `integrationKey`) ve kurum kapsamlı bir
    izinle açılan GET işlemleri
  · `/v1/catalog/*` GET işlemleri (ISBN / kütüphane araması)

Kullanım (depo kökünden):

    .venv/bin/python modules/bbd_akademi/tools/gen_allowlist.py [--spec yol] [--check]
    AKADEMI_OPENAPI=/yol/openapi.yaml .venv/bin/python modules/bbd_akademi/tools/gen_allowlist.py [--check]

Varsayılan kaynak bbd-akademi deposunun `CONTRACT_REF` dalındaki
commit'lenmiş sözleşmedir (çalışma ağacı yarım düzenlenmiş olabilir).
`CONTRACT_REF` `main`'dir. `--spec` verilmezse `AKADEMI_OPENAPI` ortam
değişkeni (testin okuduğu aynı değişken — yerel `main` geride kaldığında ya da
özellik dalının sözleşmesiyle üretirken) kullanılır.

`--check`: dosyayı yazmaz; üretilen içerik commit'lenmiş olandan farklıysa
1 ile çıkar.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

MODULE = Path(__file__).resolve().parents[1]
TARGET = MODULE / "backend" / "allowlist"

#: `/v1/admin/<kesim>/…` → alt alan (panelin `ui/panel/<alan>/` klasörleriyle aynı adlar).
AREAS: dict[str, str] = {
    "dashboard": "today", "leave-requests": "today", "etut-requests": "today",
    "meeting-requests": "today",
    "students": "students", "guardians": "students", "student-groups": "students",
    "class-groups": "students", "directory": "students",
    "coaching": "coaching", "coach-assignments": "coaching", "coach-library": "coaching",
    "coach-notes": "coaching", "mistake-inbox": "coaching", "video-pool": "coaching",
    "video-playlists": "coaching", "playlist-assignments": "coaching",
    "videos": "coaching",
    "timetable": "lessons", "timetable-templates": "lessons", "sessions": "lessons",
    "lesson-events": "lessons", "calendar": "lessons", "curricula": "lessons",
    "bell-plan": "lessons", "bell-slots": "lessons", "rooms": "lessons",
    "teacher-load": "lessons", "terms": "lessons",
    "recordings": "recordings", "boards": "recordings",
    "attendance": "attendance",
    "homework": "work", "homework-templates": "work", "plan": "work", "templates": "work",
    "assignment-bundles": "work", "reading": "work", "flashcard-decks": "work",
    "bulk-jobs": "work",
    "exams": "exams", "exam-imports": "exams", "exam-import-templates": "exams",
    "exam-publisher-tables": "exams", "exam-topic-aliases": "exams",
    "exam-analytics": "exams", "evaluations": "exams", "evaluation-periods": "exams",
    "evaluation-templates": "exams", "evaluation-reports": "exams",
    "resources": "resources", "resource-assignments": "resources",
    "resource-packages": "resources", "resource-progress": "resources",
    "catalog": "resources", "store-catalog": "resources",
    "announcements": "comms", "messages": "comms", "sms": "comms",
    "parent-meeting-slots": "comms", "pa": "comms",
    "finance": "finance", "canteen": "finance", "book-sales": "finance",
    "book-requests": "finance",
    "contracts": "contracts",
    # Deneme Kulübü davetleri (iş 81-km-kulup): ekranı Sözleşmeler alanının yanında (`ui/panel/contracts/`).
    "club": "contracts",
    "insights": "reports", "early-warnings": "reports", "early-warning-settings": "reports",
    "exports": "reports",
    "settings": "admin", "branding": "admin", "features": "admin", "staff": "admin",
    "roles": "admin", "permissions": "admin", "integration-keys": "admin",
    "data-requests": "admin", "audit-log": "admin", "files": "admin", "tablets": "admin",
    "integrations": "admin",
}
DEFAULT_SPEC = MODULE.parents[3] / "bbd-akademi" / "docs" / "api" / "openapi.yaml"

#: Sözleşmenin okunduğu git dalı: bağlayıcı sözleşme `main`'dedir. (13.10.2026'ya
#: dek geçici olarak `wp/magaza` okunuyordu — KVKK talep kuyruğu oradaydı;
#: backend-birlesim-2 / 509ae50 ile `main`'e girdi.)
CONTRACT_REF = "main"

METHODS = ("get", "post", "put", "patch", "delete")
PARAM = re.compile(r"\{([A-Za-z][A-Za-z0-9]*)\}")


def _permissions(raw: Any) -> tuple[str, ...]:
    """`x-permission` → kapsamsız anahtarlar — YALNIZ kurum kapsamında geçerli olanlar.

    `schedule.view:self|children|tenant` → `schedule.view`; liste biçimi
    (`['audit.export', 'exams.export']`) "birisi yeterli" demektir.

    Entegrasyon principal'ının kapsamı `tenant`'tır (sözleşme §1). Kapsamı
    `tenant` içermeyen seçenek (`mistakes.manage:self` — öğrencinin kendisi,
    `learning.view:children` — veli) anahtarla HİÇBİR ZAMAN karşılanamaz; listeye
    girmez. Bütün seçenekleri böyle olan öğrenci okuması vekilden geçmez.
    Kapsamsız yazım kurum geneli demektir (README §3).
    """
    items = raw if isinstance(raw, list) else [raw]
    keys: list[str] = []
    for item in items:
        key, _, scope = str(item or "").partition(":")
        key = key.strip()
        if scope and "tenant" not in scope.split("|"):
            continue
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def _accepts_integration_key(operation: dict[str, Any], spec: dict[str, Any]) -> bool:
    security = operation.get("security", spec.get("security")) or []
    return any("integrationKey" in entry for entry in security)


def _multipart(operation: dict[str, Any]) -> bool:
    body = operation.get("requestBody") or {}
    return "multipart/form-data" in (body.get("content") or {})


def collect(spec: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, item in (spec.get("paths") or {}).items():
        for method in METHODS:
            operation = item.get(method)
            if not operation:
                continue
            admin = path.startswith("/v1/admin/")
            student_read = (path.startswith("/v1/students/{studentId}/") and method == "get"
                            and _accepts_integration_key(operation, spec))
            catalog_read = (path.startswith("/v1/catalog/") and method == "get"
                            and _accepts_integration_key(operation, spec))
            if not (admin or student_read or catalog_read):
                continue
            if admin and not operation.get("x-surface"):
                raise SystemExit(f"{operation.get('operationId')}: x-surface yok — sözleşme bozuk")
            permissions = _permissions(operation.get("x-permission"))
            if not permissions:
                if admin:
                    raise SystemExit(f"{operation.get('operationId')}: kurum kapsamlı izin yok "
                                     "— sözleşme bozuk")
                continue    # yalnız öğrenci/veli kapsamlı okuma: anahtar karşılayamaz
            rows.append({
                "method": method.upper(),
                "path": path,
                "operation_id": str(operation["operationId"]),
                "permissions": permissions,
                "surface": str(operation.get("x-surface") or "read"),
                "step_up": bool(operation.get("x-step-up")),
                "money": bool(operation.get("x-money")),
                "multipart": _multipart(operation),
                "feature_flag": operation.get("x-feature-flag"),
                "features": tuple(str(x) for x in operation.get("x-features") or ()),
            })
    rows.sort(key=lambda row: (row["path"], METHODS.index(row["method"].lower())))
    return rows


def area_of(path: str) -> str:
    """İşlemin alt alanı: `/v1/admin/<kesim>` eşlemesi; öğrenci okumaları
    `students`, katalog `resources`; eşlemede olmayan `misc`."""
    if path.startswith("/v1/students/"):
        return "students"
    if path.startswith("/v1/catalog/"):
        return "resources"
    segment = path.split("/")[3] if path.startswith("/v1/admin/") else ""
    return AREAS.get(segment, "misc")


def _operation_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        args = [
            f"{row['method']!r}",
            f"{row['path']!r}",
            f"{row['operation_id']!r}",
            f"{row['permissions']!r}",
            f"{row['surface']!r}",
        ]
        flags = []
        if row["step_up"]:
            flags.append("step_up=True")
        if row["money"]:
            flags.append("money=True")
        if row["multipart"]:
            flags.append("multipart=True")
        if row["feature_flag"]:
            flags.append(f"feature_flag={row['feature_flag']!r}")
        if row["features"]:
            flags.append(f"features={row['features']!r}")
        text = f"    Operation({', '.join(args + flags)}),"
        if len(text) <= 100:
            lines.append(text)
            continue
        lines.append("    Operation(")
        for part in args + flags:
            lines.append(f"        {part},")
        lines.append("    ),")
    return lines


def render_files(rows: list[dict[str, Any]], version: str) -> dict[str, str]:
    """`backend/allowlist/` paketinin dosyaları: {dosya adı: içerik}."""
    by_area: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_area.setdefault(area_of(row["path"]), []).append(row)
    files: dict[str, str] = {}
    for area, area_rows in sorted(by_area.items()):
        lines = [
            f'"""ÜRETİLEN DOSYA — elle düzenlenmez. Alt alan: {area}.',
            "",
            f"Kaynak: bbd-akademi/docs/api/openapi.yaml (info.version {version}).",
            "Üretici: modules/bbd_akademi/tools/gen_allowlist.py",
            '"""',
            "",
            "from __future__ import annotations",
            "",
            "from ..core.operations import Operation",
            "",
            "OPERATIONS: tuple[Operation, ...] = (",
            *_operation_lines(area_rows),
            ")",
            "",
        ]
        files[f"{area}.py"] = "\n".join(lines)
    areas = sorted(by_area)
    init = [
        '"""ÜRETİLEN DOSYA — elle düzenlenmez.',
        "",
        f"Kaynak: bbd-akademi/docs/api/openapi.yaml (info.version {version}).",
        "Üretici: modules/bbd_akademi/tools/gen_allowlist.py",
        "",
        "Vekilin geçirdiği işlemlerin tamamı. Burada olmayan her istek 403'tür.",
        "Akademi izni → KM izni eşlemesi `permission_map.py` içindedir. İşlemler alt",
        "alan dosyalarındadır (ADR 0033 Aşama 2); burada üreticinin sırasıyla",
        "(yol, yöntem) tek demette birleşir.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from ..core.operations import Operation",
        "from . import (",
        *[f"    {area}," for area in areas],
        ")",
        "",
        f"CONTRACT_VERSION = {version!r}",
        "",
        '_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")',
        "",
        "OPERATIONS: tuple[Operation, ...] = tuple(sorted(",
        "    (",
        *[f"        *{area}.OPERATIONS," for area in areas],
        "    ),",
        "    key=lambda operation: (operation.path, _METHODS.index(operation.method)),",
        "))",
        "",
    ]
    files["__init__.py"] = "\n".join(init)
    return files


def committed_files() -> dict[str, str]:
    return {path.name: path.read_text(encoding="utf-8")
            for path in sorted(TARGET.glob("*.py"))} if TARGET.is_dir() else {}


def load_spec(path: Path | None = None) -> dict[str, Any]:
    """Sözleşmeyi okur.

    `path` verilmezse bbd-akademi deposunun `CONTRACT_REF` dalındaki
    COMMIT'LENMİŞ sürüm okunur (`git show <dal>:docs/api/openapi.yaml`):
    çalışma ağacındaki dosya o anda başka biri tarafından düzenleniyor (yarım,
    ayrıştırılamaz) olabilir ve bağlayıcı sözleşme commit'lenmiş olandır. Git,
    depo ya da dal yoksa dosya okunur.
    """
    if path is None:
        repo = DEFAULT_SPEC.parents[2]
        try:
            text = subprocess.run(
                ["git", "-C", str(repo), "show", f"{CONTRACT_REF}:docs/api/openapi.yaml"],
                capture_output=True, text=True, check=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            text = DEFAULT_SPEC.read_text(encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise SystemExit("sözleşme okunamadı")
    return loaded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--spec", type=Path, default=None,
                        help=f"sözleşme dosyası (varsayılan: bbd-akademi {CONTRACT_REF} dalı)")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    spec_path = args.spec
    if spec_path is None and os.environ.get("AKADEMI_OPENAPI"):
        spec_path = Path(os.environ["AKADEMI_OPENAPI"])
    spec = load_spec(spec_path)
    rows = collect(spec)
    files = render_files(rows, str((spec.get("info") or {}).get("version") or "?"))
    if args.check:
        if committed_files() != files:
            print("backend/allowlist/ sözleşmeyle uyuşmuyor — betiği yeniden koşturun.", file=sys.stderr)
            return 1
        print(f"backend/allowlist/ güncel ({len(rows)} işlem, {len(files) - 1} alt alan).")
        return 0
    TARGET.mkdir(exist_ok=True)
    for stale in set(committed_files()) - set(files):
        (TARGET / stale).unlink()
    for name, text in files.items():
        (TARGET / name).write_text(text, encoding="utf-8")
    admin = sum(1 for row in rows if row["path"].startswith("/v1/admin/"))
    print(f"{TARGET.relative_to(MODULE.parents[1])}/: {len(rows)} işlem ({admin} admin), "
          f"{len(files) - 1} alt alan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
