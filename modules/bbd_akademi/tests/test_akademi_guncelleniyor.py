"""Akademi güncellenirken (dağıtım anı): okumada kısa geri çekilmeli yeniden deneme, yazmada asla."""

from __future__ import annotations

import httpx
import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.gateway import AkademiGateway, GatewayConfig
from modules.bbd_akademi.backend.operations import Allowlist
from modules.bbd_akademi.backend.proxy import AkademiProxy
from modules.bbd_akademi.backend.stepup import StepUpStore
from modules.bbd_akademi.tests.akademi_fakes import ADMIN, BASE, Akademi, Log, Secrets

SID = "0192f0aa-1111-7222-8333-944455556666"
NO_SERVER = httpx.Response(503, text="no available server", headers={"content-type": "text/plain"})


class Clock:
    """Sahte saat: `sleep` gerçekte beklemez, saati ilerletir."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def proxy(store, akademi: Akademi, clock: Clock) -> AkademiProxy:  # type: ignore[no-untyped-def]
    gateway = AkademiGateway(config=GatewayConfig(base_url=BASE, read_retry_seconds=20),
                             secrets=Secrets(), log=Log(),
                             transport=httpx.MockTransport(akademi.handler))
    return AkademiProxy(gateway=gateway, allowlist=Allowlist(OPERATIONS),
                        step_up=StepUpStore(store, ttl_seconds=120), log=Log(),
                        sleep=clock.sleep, clock=clock)


def _sequence(*responses: httpx.Response | Exception):  # type: ignore[no-untyped-def]
    queue = list(responses)

    def reply(_request: httpx.Request) -> httpx.Response:
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    return reply


async def test_okuma_gecici_503_sonra_basarili(proxy: AkademiProxy, akademi: Akademi,
                                               clock: Clock) -> None:
    akademi.reply(_sequence(NO_SERVER, httpx.Response(502, text="Bad Gateway"),
                            httpx.Response(200, json={"kpis": []})))
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["ok"] is True and result["data"] == {"kpis": []}
    assert len(akademi.calls) == 3
    assert clock.sleeps == [0.5, 1.0]


async def test_okuma_baglanti_hatasi_yeniden_denenir(proxy: AkademiProxy, akademi: Akademi,
                                                    clock: Clock) -> None:
    akademi.reply(_sequence(httpx.ConnectError("refused"), httpx.Response(200, json={"ok": 1})))
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["ok"] is True
    assert clock.sleeps == [0.5]


async def test_okuma_butce_dolunca_guncelleniyor_hatasi(proxy: AkademiProxy, akademi: Akademi,
                                                       clock: Clock) -> None:
    akademi.reply(NO_SERVER)
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["ok"] is False and result["status"] == 503
    error = result["error"]
    assert error["code"] == "akademi_unavailable"
    assert "güncelleniyor" in error["message"]
    assert error["details"]["retryable"] is True
    assert error["details"]["upstreamStatus"] == 503
    # Toplam bekleme 20 sn bütçeyi aşmaz.
    assert sum(clock.sleeps) <= 20
    assert clock.sleeps[:4] == [0.5, 1.0, 2.0, 3.0]
    assert len(akademi.calls) == len(clock.sleeps) + 1


async def test_akademinin_kendi_json_503u_yeniden_denenmez(proxy: AkademiProxy, akademi: Akademi,
                                                          clock: Clock) -> None:
    akademi.reply(httpx.Response(503, json={"error": {"code": "maintenance",
                                                      "message": "Bakımdayız", "details": None}}))
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["error"]["code"] == "maintenance"
    assert len(akademi.calls) == 1 and clock.sleeps == []


async def test_yazma_asla_yeniden_denenmez(proxy: AkademiProxy, akademi: Akademi,
                                           clock: Clock) -> None:
    akademi.reply(NO_SERVER)
    result = await proxy.call(ADMIN, method="POST",
                              path=f"/v1/admin/finance/accounts/{SID}/payments",
                              body={"method": "cash", "amountMinor": 5000, "mode": "free_amount"},
                              idempotency_key="pay-key-000001")
    assert len(akademi.calls) == 1 and clock.sleeps == []
    assert result["ok"] is False and result["status"] == 503
    assert result["error"]["code"] == "akademi_unavailable"
    assert result["error"]["details"]["retryable"] is False
    assert "kaydedilmemiş olabilir" in result["error"]["message"]


async def test_zaman_asimi_yeniden_denenmez(proxy: AkademiProxy, akademi: Akademi,
                                           clock: Clock) -> None:
    akademi.reply(_sequence(httpx.ReadTimeout("yavaş")))
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["error"]["code"] == "akademi_unavailable"
    assert len(akademi.calls) == 1 and clock.sleeps == []


def test_ayar_sifir_kapali_ve_okunur() -> None:
    assert GatewayConfig.from_mapping({"read_retry_seconds": 0}).read_retry_seconds == 0
    assert GatewayConfig.from_mapping({}).read_retry_seconds == 20
