"""İzin listesi: sözleşmeyle birebir, liste dışı her yol 403, her yol doğru KM iznini ister."""

from __future__ import annotations

import importlib.util
import os
import re
from pathlib import Path

import httpx
import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.operations import Allowlist, Operation
from modules.bbd_akademi.backend.permission_map import (
    OPERATION_OVERRIDES,
    PERMISSION_MAP,
    km_permissions,
    unmapped,
)
from modules.bbd_akademi.tests.akademi_fakes import Akademi, Identity, auth, user

MODULE = Path(__file__).resolve().parents[1]
SPEC = Path(os.environ.get("AKADEMI_OPENAPI")
            or MODULE.parents[3] / "bbd-akademi" / "docs" / "api" / "openapi.yaml")
SAMPLE = "0192f0aa-1111-7222-8333-944455556666"


def concrete(operation: Operation) -> str:
    return re.sub(r"\{[A-Za-z0-9]+\}", SAMPLE, operation.path)


def _generator():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("gen_allowlist",
                                                  MODULE / "tools" / "gen_allowlist.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- sözleşme


@pytest.mark.skipif(not SPEC.exists(), reason="Akademi sözleşmesi bu makinede yok")
def test_her_admin_islemi_listede_ve_uretilen_dosya_guncel() -> None:
    generator = _generator()
    # AKADEMI_OPENAPI verilmezse bbd-akademi CONTRACT_REF dalındaki commit'lenmiş sözleşme.
    spec = generator.load_spec(Path(os.environ["AKADEMI_OPENAPI"])
                               if os.environ.get("AKADEMI_OPENAPI") else None)
    admin = {op["operationId"] for path, item in spec["paths"].items()
             if path.startswith("/v1/admin/")
             for method, op in item.items() if method in ("get", "post", "put", "patch", "delete")}
    listed = {op.operation_id for op in OPERATIONS}
    assert admin <= listed, f"listede olmayan admin işlemleri: {sorted(admin - listed)}"

    rendered = generator.render(generator.collect(spec), str(spec["info"]["version"]))
    committed = (MODULE / "backend" / "allowlist.py").read_text(encoding="utf-8")
    assert rendered == committed, "allowlist.py eski — tools/gen_allowlist.py yeniden koşturun"


def test_liste_bicimi() -> None:
    admin = [op for op in OPERATIONS if op.path.startswith("/v1/admin/")]
    assert len(admin) >= 183
    assert all(op.surface in ("km", "both") for op in admin)
    # Öğrenci ve katalog satırları YALNIZ okuma.
    assert all(op.method == "GET" for op in OPERATIONS if not op.path.startswith("/v1/admin/"))
    ids = [op.operation_id for op in OPERATIONS]
    assert len(ids) == len(set(ids))
    # Akademi'nin kendi kimlik / ödeme uçları vekilden geçmez.
    assert not [op for op in OPERATIONS if op.path.startswith(("/v1/auth", "/v1/payments",
                                                               "/v1/webhooks", "/v1/me"))]


def test_her_akademi_izni_tek_km_iznine_esli() -> None:
    assert unmapped(OPERATIONS) == []
    used = {key for op in OPERATIONS for key in op.permissions}
    assert set(PERMISSION_MAP) == used, "tabloda kullanılmayan ya da eksik izin var"
    assert set(OPERATION_OVERRIDES) <= {op.operation_id for op in OPERATIONS}
    # Kantin parası kurum finansına hiç eşlenmez (A12).
    assert all(not value.endswith(".finance") for key, value in PERMISSION_MAP.items()
               if key.startswith("canteen."))
    void = Allowlist(OPERATIONS).by_id("admin_voidPayment")
    assert void is not None and void.step_up
    assert km_permissions(void) == ("bbd_akademi.finance_void",)


def test_sabit_parca_parametreden_once_eslesir() -> None:
    allowlist = Allowlist(OPERATIONS)
    resolved = allowlist.resolve("GET", "/v1/admin/exam-analytics/compare")
    assert resolved and resolved.operation.operation_id == "admin_compareStudents"
    resolved = allowlist.resolve("PUT", "/v1/admin/staff/sync")
    assert resolved and resolved.operation.operation_id == "admin_syncStaff"
    resolved = allowlist.resolve("GET", f"/v1/admin/finance/accounts/{SAMPLE}/statement")
    assert resolved and resolved.operation.operation_id == "admin_getFinanceStatement"


@pytest.mark.parametrize("method,path", [
    ("GET", "/v1/admin/students/../../auth/identify"),
    ("GET", "/v1/admin/students/%2e%2e"),
    ("GET", "/v1/admin/students/.hidden"),
    ("GET", "/v1/admin/students/a/b"),
    ("GET", "/v1/admin/nope"),
    ("DELETE", "/v1/admin/dashboard"),
    ("POST", "/v1/auth/identify"),
    ("POST", f"/v1/students/{SAMPLE}/leave-requests"),
    ("GET", f"/v1/students/{SAMPLE}/canteen/qr"),
    ("POST", "/v1/webhooks/odeme"),
])
def test_listede_olmayan_eslesmez(method: str, path: str) -> None:
    assert Allowlist(OPERATIONS).resolve(method, path) is None


# ------------------------------------------------------------------ HTTP


@pytest.mark.parametrize("path", [
    "v1/auth/identify", "v1/admin/unknown", "v1/me", "anything",
    f"v1/students/{SAMPLE}/canteen/qr",
])
async def test_liste_disi_yol_403(client: httpx.AsyncClient, akademi: Akademi,
                                  path: str) -> None:
    response = await client.get(f"/api/bbd_akademi/{path}", headers=auth())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "operation_not_allowed"
    assert akademi.calls == []


async def test_liste_disi_yontem_403(client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await client.post(f"/api/bbd_akademi/v1/students/{SAMPLE}/leave-requests",
                                 headers=auth(), json={})
    assert response.status_code == 403
    assert akademi.calls == []


async def test_oturum_yoksa_401(client: httpx.AsyncClient, akademi: Akademi) -> None:
    response = await client.get("/api/bbd_akademi/v1/admin/dashboard")
    assert response.status_code == 401
    assert akademi.calls == []


@pytest.mark.parametrize("operation", OPERATIONS, ids=lambda op: op.operation_id)
async def test_her_yol_eslenen_km_iznini_ister(client: httpx.AsyncClient, akademi: Akademi,
                                              identity: Identity, operation: Operation) -> None:
    needed = km_permissions(operation)
    # İzinsiz: yalnız taban izin (görüntüleme dışı işlemler için) — 403, Akademi'ye gidilmez.
    others = {"bbd_akademi.view"} - set(needed)
    identity.extra["without"] = user("u-9", "İzin", "Yok", *others)
    identity.extra["with"] = user("u-8", "İzin", "Var", "bbd_akademi.view", needed[0])
    url = f"/api/bbd_akademi{concrete(operation)}"

    if "bbd_akademi.view" not in needed:
        denied = await client.request(operation.method, url, headers=auth("without"),
                                      json={} if operation.is_write else None)
        assert denied.status_code == 403, operation.operation_id
        assert denied.json()["error"]["code"] == "forbidden"
        assert denied.json()["error"]["details"]["permission"] == needed[0]
        assert akademi.calls == []

    headers = auth("with")
    if operation.step_up:
        # Step-up işlemi belirteçsiz reddedilir; geçiş yolu ayrı testte (test_step_up).
        refused = await client.request(operation.method, url, headers=headers, json={})
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "step_up_required"
        return
    allowed = await client.request(operation.method, url, headers=headers,
                                   json={} if operation.is_write else None)
    assert allowed.status_code == 200, (operation.operation_id, allowed.text)
    assert akademi.last.method == operation.method
    assert akademi.last.url.path == concrete(operation)
