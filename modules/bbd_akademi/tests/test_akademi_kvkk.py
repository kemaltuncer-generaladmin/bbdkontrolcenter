"""KVKK talepleri bölümü (sözleşme README §14 madde 41–44, `/v1/admin/data-requests*`).

* İzin: Akademi `privacy.requests` → KM `bbd_akademi.privacy` (yalnız admin,
  yıkıcı); bbd_staff'a düşmez.
* Vekil: liste süzgeci aynen geçer; karar ve "erişimi yeniden aç" yazmaları
  `X-Akademi-On-Behalf-Of` + `Idempotency-Key` ile gider; KM izni olmayan
  Akademi'ye hiç gidemez.
* Akademi'nin kendi 403'ü (anahtar izni eksik) panele `details.operationId`
  olmadan ulaşır — panel "anahtarı yeniden üretin" notunu buna göre yazar.
* Bölümün hüküm mantığı (`ui/panel/privacy-logic.js`) — Node ile.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
import yaml
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.core.operations import Allowlist
from modules.bbd_akademi.backend.permission_map import ALL_PERMISSIONS, km_permissions
from modules.bbd_akademi.tests.akademi_fakes import PIN, Akademi, Identity, auth, user

MODULE = Path(__file__).resolve().parents[1]
PANEL = MODULE / "ui" / "panel"
CALL = "/api/bbd_akademi/km/call"
RID = "0192f0aa-1111-7222-8333-944455556666"
KVKK_OPS = ("admin_listDataRequests", "admin_getDataRequest", "admin_decideDataRequest",
            "admin_reopenErasedAccess")


def _request(**extra: object) -> dict[str, object]:
    return {
        "id": RID, "kind": "account_deletion", "status": "received", "reason": None,
        "createdAt": "2026-10-01T09:00:00+03:00", "dueAt": "2026-10-31T09:00:00+03:00",
        "overdue": False, "completedAt": None, "note": None,
        "subject": {"principalId": RID, "kind": "person", "displayName": "Ayşe Veli",
                    "studentId": None, "erased": False},
        "resolvedBy": None, "downloadExpiresAt": None, "accessReopenable": False, **extra,
    }


# ------------------------------------------------------------------ izin


def test_kvkk_islemleri_listede_ve_tek_km_iznine_esli() -> None:
    allowlist = Allowlist(OPERATIONS)
    for operation_id in KVKK_OPS:
        operation = allowlist.by_id(operation_id)
        assert operation is not None, f"{operation_id} izin listesinde yok"
        assert operation.permissions == ("privacy.requests",)
        assert operation.surface == "both"
        assert km_permissions(operation) == ("bbd_akademi.privacy",)
    assert "bbd_akademi.privacy" in ALL_PERMISSIONS


def test_kvkk_izni_yalniz_yoneticide_ve_yikici() -> None:
    manifest = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["permissions"] if item["key"] == "bbd_akademi.privacy")
    assert entry["default_roles"] == ["admin"]
    assert entry.get("destructive") is True


def test_bolum_kaydi_kvkk_izniyle() -> None:
    index = (PANEL / "sections.js").read_text(encoding="utf-8")
    assert "{ key: 'privacy', label: 'KVKK talepleri', file: './admin/privacy.js', requires: ['privacy']" in index


# ------------------------------------------------------------------ vekil


async def test_liste_suzgeci_aynen_gecer(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(httpx.Response(200, json={"items": [_request()], "nextCursor": None}))
    data = (await client.post(CALL, headers=auth(), json={
        "method": "GET", "path": "/v1/admin/data-requests",
        "query": {"status": "open", "kind": "account_deletion", "limit": 50, "cursor": None}})).json()
    assert data["ok"] is True and data["data"]["items"][0]["id"] == RID
    sent = akademi.last
    assert sent.method == "GET" and sent.url.path == "/v1/admin/data-requests"
    assert dict(sent.url.params) == {"status": "open", "kind": "account_deletion", "limit": "50"}


@pytest.mark.parametrize("path,body", [
    (f"/v1/admin/data-requests/{RID}/decision", {"action": "reject", "note": "Kimlik doğrulanamadı."}),
    (f"/v1/admin/data-requests/{RID}/decision", {"action": "approve", "note": None}),
    (f"/v1/admin/data-requests/{RID}/reopen-access", None),
])
async def test_yazma_adina_ve_tekil_anahtarla_gider(client: httpx.AsyncClient, akademi: Akademi,
                                                  path: str, body: dict[str, object] | None) -> None:
    akademi.reply(httpx.Response(200, json=_request(status="rejected",
                                                    resolvedBy="km:user:u-1:Ayşe Yılmaz")))
    payload: dict[str, object] = {"method": "POST", "path": path,
                                  "idempotencyKey": f"kvkk-test-{RID}"}
    if path.endswith("/decision"):
        # KM'nin kendi PIN teyidi (iş 63, KM-07): karar tek kullanımlık belirteç ister.
        issued = await client.post("/api/bbd_akademi/km/step-up", headers=auth(),
                                   json={"pin": PIN, "operationId": "admin_decideDataRequest"})
        assert issued.status_code == 200, issued.text
        payload["stepUpToken"] = issued.json()["stepUpToken"]
    if body is not None:
        payload["body"] = body
    data = (await client.post(CALL, headers=auth(), json=payload)).json()
    assert data["ok"] is True, data
    sent = akademi.last
    assert sent.method == "POST" and sent.url.path == path
    assert sent.headers["idempotency-key"] == f"kvkk-test-{RID}"
    behalf = sent.headers["x-akademi-on-behalf-of"]
    assert behalf.isascii() and unquote(behalf) == "km:user:u-1:Ayşe Yılmaz"
    if body is not None:
        assert json.loads(akademi.bodies[-1]) == body
    # Teyit KM'de kalır: sözleşmede step-up yok, Akademi'ye başlık uydurulmaz.
    assert "x-akademi-step-up" not in sent.headers


async def test_kvkk_karari_pin_teyidisiz_akademiye_gitmez(client: httpx.AsyncClient,
                                                          akademi: Akademi) -> None:
    """KM-07: onay hesabı geri dönüşsüz siler — belirteçsiz karar reddedilir."""
    data = (await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": f"/v1/admin/data-requests/{RID}/decision",
        "body": {"action": "approve"}})).json()
    assert data["ok"] is False and data["status"] == 403
    assert data["error"]["code"] == "step_up_required"
    assert akademi.calls == []


async def test_kvkk_izni_olmayan_akademiye_gidemez(client: httpx.AsyncClient, akademi: Akademi,
                                                    identity: Identity) -> None:
    # bbd_staff benzeri: KVKK dışında her izin.
    others = [key for key in ALL_PERMISSIONS if key != "bbd_akademi.privacy"]
    identity.extra["staff"] = user("u-7", "Personel", "Kişi", *others)
    for method, path in (("GET", "/v1/admin/data-requests"),
                         ("GET", f"/v1/admin/data-requests/{RID}"),
                         ("POST", f"/v1/admin/data-requests/{RID}/decision"),
                         ("POST", f"/v1/admin/data-requests/{RID}/reopen-access")):
        denied = (await client.post(CALL, headers=auth("staff"), json={
            "method": method, "path": path, "body": {"action": "approve"}})).json()
        assert denied["ok"] is False and denied["status"] == 403, path
        assert denied["error"]["code"] == "forbidden"
        assert denied["error"]["details"]["permission"] == "bbd_akademi.privacy"
        # Vekilin kendi reddi işlem kimliğini yazar (panel bunu anahtar sorunu sanmaz).
        assert denied["error"]["details"]["operationId"].startswith("admin_")
    assert akademi.calls == []


async def test_akademinin_403u_islem_kimliksiz_ulasir(client: httpx.AsyncClient,
                                                      akademi: Akademi) -> None:
    """Eski KM panel anahtarı `privacy.requests` taşımaz → Akademi 403 forbidden."""
    akademi.reply(httpx.Response(403, json={"error": {
        "code": "forbidden", "message": "Bu işlem için yetkiniz yok.", "details": None,
        "traceId": "t-1"}}))
    data = (await client.post(CALL, headers=auth(), json={
        "method": "GET", "path": "/v1/admin/data-requests"})).json()
    assert data["ok"] is False and data["status"] == 403
    assert data["error"]["code"] == "forbidden"
    assert not (data["error"].get("details") or {}).get("operationId")


async def test_karar_cakismasi_panele_ulasir(client: httpx.AsyncClient, akademi: Akademi) -> None:
    akademi.reply(httpx.Response(409, json={"error": {
        "code": "request_not_open", "message": "Talep artık açık değil.", "details": None,
        "traceId": "t-2"}}))
    issued = await client.post("/api/bbd_akademi/km/step-up", headers=auth(),
                               json={"pin": PIN, "operationId": "admin_decideDataRequest"})
    data = (await client.post(CALL, headers=auth(), json={
        "method": "POST", "path": f"/v1/admin/data-requests/{RID}/decision",
        "body": {"action": "approve"}, "stepUpToken": issued.json()["stepUpToken"]})).json()
    assert data["ok"] is False and data["status"] == 409
    assert data["error"]["code"] == "request_not_open"


# -------------------------------------------------------- hüküm mantığı (Node)

SCRIPT = r"""
import * as s from './privacy-logic.mjs';
const now = Date.parse('2026-10-20T12:00:00+03:00');
const row = (extra) => ({ status: 'received', dueAt: '2026-10-31T09:00:00+03:00', overdue: false, ...extra });
const out = {
  left: s.daysLeft(row({}), now),
  closed: s.daysLeft(row({ status: 'completed' }), now),
  bad: s.daysLeft(row({ dueAt: 'x' }), now),
  far: s.dueBadge(row({}), now),
  week: s.dueBadge(row({ dueAt: '2026-10-27T13:00:00+03:00' }), now),
  today: s.dueBadge(row({ dueAt: '2026-10-20T23:00:00+03:00' }), now),
  late: s.dueBadge(row({ dueAt: '2026-10-19T09:00:00+03:00' }), now),
  flagged: s.dueBadge(row({ overdue: true }), now),
  flaggedClosed: s.dueBadge(row({ overdue: true, status: 'rejected' }), now),
  processing: s.dueBadge(row({ status: 'processing' }), now),
  keyGap: s.keyLacksPermission({ code: 'forbidden', details: null }),
  kmDeny: s.keyLacksPermission({ code: 'forbidden', details: { operationId: 'admin_listDataRequests' } }),
  other: s.keyLacksPermission({ code: 'request_not_open' }),
  none: s.keyLacksPermission(null),
  acts: [s.actionsFor(row({})), s.actionsFor(row({ status: 'processing' })),
    s.actionsFor({ status: 'completed', accessReopenable: true })],
  notes: [s.cleanNote('  gerekçe  '), s.cleanNote('   '), s.cleanNote(null), s.cleanNote('x'.repeat(600)).length],
  min: s.REJECT_MIN,
  labels: [s.KIND.account_deletion, s.STATUS.cancelled[0]],
};
console.log(JSON.stringify(out));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")
def test_kvkk_hukum_mantigi(tmp_path: Path) -> None:
    (tmp_path / "privacy-logic.mjs").write_text(
        (PANEL / "admin/privacy-logic.js").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(["node", str(tmp_path / "run.mjs")], capture_output=True,
                            text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["left"] == 10
    assert out["closed"] is None and out["bad"] is None
    assert out["far"] == {"text": "10 gün kaldı", "tone": ""}
    assert out["week"] == {"text": "7 gün kaldı", "tone": "warn"}
    assert out["today"] == {"text": "Bugün son gün", "tone": "warn"}
    assert out["late"] == {"text": "Süresi geçti", "tone": "bad"}
    assert out["flagged"] == {"text": "Süresi geçti", "tone": "bad"}
    assert out["flaggedClosed"] is None
    assert out["processing"]["text"] == "10 gün kaldı"
    assert out["keyGap"] is True and out["kmDeny"] is False
    assert out["other"] is False and out["none"] is False
    assert out["acts"] == [{"decide": True, "reopen": False}, {"decide": False, "reopen": False},
                           {"decide": False, "reopen": True}]
    assert out["notes"] == ["gerekçe", None, None, 500]
    assert out["min"] == 10
    assert out["labels"] == ["Hesap silme", "Talep sahibi vazgeçti"]
