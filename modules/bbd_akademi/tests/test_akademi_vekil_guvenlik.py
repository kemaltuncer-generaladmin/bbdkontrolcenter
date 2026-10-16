"""KM → Akademi vekili: KM kullanıcısının izni olmadan geçen işlem var mı? (iş 63, denetim 1d)

Akademi KM'yi TEK entegrasyon anahtarıyla görür; kişi başına yetkinin tamamı KM'deki
haritadadır (`permission_map.py`). Bu yüzden harita ile izin listesi (`allowlist.py`)
arasındaki her boşluk bir yetki açığıdır. Burada sınananlar:

- haritanın KM tarafındaki her izni modül manifestinde ilan edilmiştir (rollere düşer),
- YAZMA işlemi taban görüntüleme iznine (`bbd_akademi.view`) eşlenmez — gerekçeli iki istisna,
- yıkıcı ilan edilmiş KM iznine eşlenen her yazma işlemi PIN teyidi ister,
- haritada karşılığı olmayan işlem 500 değil, AÇIK bir 403'le reddedilir (kapalı kalır),
- izin listesinin dışındaki ya da haritası olmayan hiçbir istek Akademi'ye ulaşmaz.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import yaml
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.core import proxy as proxy_module
from modules.bbd_akademi.backend.core.operations import Operation
from modules.bbd_akademi.backend.permission_map import (
    KM_STEP_UP_OPERATIONS,
    OPERATION_OVERRIDES,
    PERMISSION_MAP,
    VIEW,
    km_permissions,
    requires_step_up,
)
from modules.bbd_akademi.tests.akademi_fakes import Akademi, auth

MODULE = Path(__file__).resolve().parents[1]
CALL = "/api/bbd_akademi/km/call"

#: Görüntüleme iznine eşlenen yazma işlemleri — durum değiştirmezler.
VIEW_WRITES: dict[str, str] = {
    "admin_refreshBookSaleOrder": "Mağazadaki sipariş durumunu yeniden okur; kayıt değiştirmez.",
    "admin_requestCoachingReport": "Koçluk raporu (PDF) üretir; veri değiştirmez.",
}


def _manifest_permissions() -> dict[str, dict[str, object]]:
    data = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    return {str(item["key"]): item for item in data["permissions"]}


def test_haritanin_her_km_izni_manifestte_ilan_edilmis() -> None:
    declared = _manifest_permissions()
    used = set(PERMISSION_MAP.values()) | set(OPERATION_OVERRIDES.values())
    assert used - set(declared) == set()


def test_yazma_islemi_goruntuleme_iznine_esli_degil() -> None:
    writes = {op.operation_id for op in OPERATIONS if op.is_write and VIEW in km_permissions(op)}
    assert writes == set(VIEW_WRITES), "yazma işlemi yalnız bbd_akademi.view ile açılıyor"


def test_yikici_km_iznine_eslenen_yazma_pin_ister() -> None:
    destructive = {key for key, item in _manifest_permissions().items() if item.get("destructive")}
    assert destructive, "manifestte yıkıcı izin bekleniyordu (finance_void, privacy)"
    missing = sorted(op.operation_id for op in OPERATIONS
                     if op.is_write and set(km_permissions(op)) & destructive
                     and not requires_step_up(op))
    # Erişimi yeniden açmak silmenin tersidir (veri geri gelmez, hesap boş döner): PIN istemez.
    assert missing == ["admin_reopenErasedAccess"]
    assert KM_STEP_UP_OPERATIONS <= {op.operation_id for op in OPERATIONS}


async def test_haritasiz_islem_403_ve_akademiye_gitmez(client: httpx.AsyncClient,
                                                       akademi: Akademi) -> None:
    proxy = proxy_module_proxy()
    rogue = Operation("GET", "/v1/admin/rogue-report", "admin_rogueReport",
                      ("rogue.view",), "km")
    original = proxy.allowlist
    proxy.allowlist = type(original)((*original.operations, rogue))
    try:
        data = (await client.post(CALL, headers=auth(), json={
            "method": "GET", "path": "/v1/admin/rogue-report"})).json()
        raw = await client.get("/api/bbd_akademi/v1/admin/rogue-report", headers=auth())
    finally:
        proxy.allowlist = original
    assert data["ok"] is False and data["status"] == 403
    assert data["error"]["code"] == "permission_unmapped"
    assert raw.status_code == 403
    assert raw.json()["error"]["code"] == "permission_unmapped"
    assert akademi.calls == []


def proxy_module_proxy() -> proxy_module.AkademiProxy:
    from modules.bbd_akademi.backend.api import routes

    return routes.proxy()
