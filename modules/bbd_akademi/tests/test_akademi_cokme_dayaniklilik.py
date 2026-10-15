"""Akademi erişilemezken (çökme dayanıklılığı, 2026-10-15): devre kesici ve son bilinen veri."""

from __future__ import annotations

import httpx
import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.gateway import AkademiGateway, AkademiUnavailable, GatewayConfig
from modules.bbd_akademi.backend.operations import Allowlist
from modules.bbd_akademi.backend.proxy import AkademiProxy, StaleCache, transient
from modules.bbd_akademi.backend.stepup import StepUpStore
from modules.bbd_akademi.tests.akademi_fakes import ADMIN, BASE, Akademi, Log, Secrets


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _gateway(akademi: Akademi, clock: Clock, **config: float) -> AkademiGateway:
    return AkademiGateway(config=GatewayConfig(base_url=BASE, **config), secrets=Secrets(), log=Log(),
                          transport=httpx.MockTransport(akademi.handler), clock=clock)


def _refuse(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused")


async def test_devre_ardisik_hatalardan_sonra_acilir_ve_istek_gitmez(akademi: Akademi) -> None:
    clock = Clock()
    gateway = _gateway(akademi, clock, breaker_failures=3, breaker_seconds=10)
    akademi.reply(_refuse)
    for _ in range(3):
        with pytest.raises(AkademiUnavailable):
            await gateway.send("GET", "/v1/admin/dashboard")
    assert gateway.circuit()["open"] is True
    calls = len(akademi.calls)
    with pytest.raises(AkademiUnavailable) as caught:
        await gateway.send("GET", "/v1/admin/dashboard")
    assert caught.value.details["reason"] == "circuit_open"
    assert caught.value.details["retryable"] is True
    assert len(akademi.calls) == calls          # Akademi'ye gitmedi, beklemedi


async def test_sure_dolunca_tek_deneme_gecer_basariliysa_kapanir(akademi: Akademi) -> None:
    clock = Clock()
    gateway = _gateway(akademi, clock, breaker_failures=2, breaker_seconds=10)
    akademi.reply(_refuse)
    for _ in range(2):
        with pytest.raises(AkademiUnavailable):
            await gateway.send("GET", "/v1/admin/dashboard")
    clock.now += 11
    akademi.reply(lambda _r: httpx.Response(200, json={"ok": 1}))
    response = await gateway.send("GET", "/v1/admin/dashboard")
    await response.aclose()
    assert response.status_code == 200
    assert gateway.circuit() == {"open": False, "failures": 0, "retryAfterSeconds": 0.0}


async def test_deneme_basarisizsa_devre_yeniden_acilir(akademi: Akademi) -> None:
    clock = Clock()
    gateway = _gateway(akademi, clock, breaker_failures=2, breaker_seconds=10)
    akademi.reply(_refuse)
    for _ in range(2):
        with pytest.raises(AkademiUnavailable):
            await gateway.send("GET", "/v1/admin/dashboard")
    clock.now += 11
    with pytest.raises(AkademiUnavailable) as caught:
        await gateway.send("GET", "/v1/admin/dashboard")   # deneme isteği gitti, düştü
    assert caught.value.details["reason"] == "connect"
    assert gateway.circuit()["open"] is True


async def test_gecit_503_yaniti_baglanti_sayilir_sayaci_sifirlar(akademi: Akademi) -> None:
    clock = Clock()
    gateway = _gateway(akademi, clock, breaker_failures=2, breaker_seconds=10)
    akademi.reply(_refuse)
    with pytest.raises(AkademiUnavailable):
        await gateway.send("GET", "/v1/admin/dashboard")
    akademi.reply(lambda _r: httpx.Response(503, text="no available server"))
    response = await gateway.send("GET", "/v1/admin/dashboard")
    await response.aclose()
    assert gateway.circuit()["failures"] == 0


def test_devre_kesici_ayari_okunur_sifir_kapali() -> None:
    assert GatewayConfig.from_mapping({}).breaker_failures == 3
    assert GatewayConfig.from_mapping({"breaker_failures": 0}).breaker_failures == 0


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def proxy(store, akademi: Akademi, clock: Clock) -> AkademiProxy:  # type: ignore[no-untyped-def]
    gateway = _gateway(akademi, clock, read_retry_seconds=5, breaker_failures=3, breaker_seconds=10)
    return AkademiProxy(gateway=gateway, allowlist=Allowlist(OPERATIONS),
                        step_up=StepUpStore(store, ttl_seconds=120), log=Log(),
                        sleep=clock.sleep, clock=clock)


async def test_akademi_dusunce_son_bilinen_veri_doner(proxy: AkademiProxy, akademi: Akademi) -> None:
    akademi.reply(lambda _r: httpx.Response(200, json={"kpis": [1, 2]}))
    first = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert first["ok"] is True and "stale" not in first
    akademi.reply(_refuse)
    second = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert second["ok"] is True and second["data"] == {"kpis": [1, 2]}
    assert second["stale"]["reason"] in ("connect", "circuit_open")
    assert second["stale"]["at"].endswith("Z")
    assert second["headers"]["x-km-stale"] == "1"


async def test_devre_acikken_son_bilinen_veri_beklemeden_doner(proxy: AkademiProxy, akademi: Akademi,
                                                              clock: Clock) -> None:
    akademi.reply(lambda _r: httpx.Response(200, json={"kpis": []}))
    await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    akademi.reply(_refuse)
    await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")   # bütçe boyunca dener, devre açılır
    assert proxy.gateway.circuit()["open"] is True
    sleeps, calls = len(clock.sleeps), len(akademi.calls)
    third = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert third["stale"]["reason"] == "circuit_open"
    assert len(clock.sleeps) == sleeps and len(akademi.calls) == calls


async def test_son_bilinen_veri_yoksa_hata_ve_yazma_hic_onbellekten_donmez(proxy: AkademiProxy,
                                                                          akademi: Akademi) -> None:
    akademi.reply(_refuse)
    result = await proxy.call(ADMIN, method="GET", path="/v1/admin/dashboard")
    assert result["ok"] is False and result["error"]["code"] == "akademi_unavailable"


def test_onbellek_bayt_butcesi_ve_yasi() -> None:
    now = [0.0]
    cache = StaleCache(max_bytes=30, max_entry_bytes=25, max_age_seconds=60, clock=lambda: now[0])  # kayıt 17 bayt
    cache.put(("a",), {"x": "1" * 10}, {})
    cache.put(("b",), {"x": "2" * 10}, {})
    assert cache.get(("a",)) is None and cache.get(("b",)) is not None   # en eski düştü
    cache.put(("c",), {"x": "3" * 40}, {})                                # tek kayıt sınırını aşar
    assert cache.get(("c",)) is None
    now[0] = 61
    assert cache.get(("b",)) is None                                       # 60 sn'den eski


def test_akademinin_altyapi_503u_gecicidir() -> None:
    retryable = httpx.Response(503, json={"error": {"code": "service_unavailable"}},
                               headers={"x-akademi-retryable": "1"})
    own = httpx.Response(503, json={"error": {"code": "maintenance"}})
    assert transient(retryable) is True
    assert transient(own) is False


STALE_SCRIPT = r"""
import { createClient } from './api.mjs';
import { updatingBand, STALE_TEXT, clockText } from './updating.mjs';
class El {
  constructor() { this.children = []; this.attrs = {}; this.hidden = false; this.textContent = '';
    this.listeners = {}; this.className = ''; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  replaceChildren(...c) { this.children = c; }
  get text() { return this.children.map((c) => c.textContent).join(' | '); }
}
const doc = { createElement: () => new El() };
const timers = { setInterval: () => 1, clearInterval: () => {} };
const out = {};
const at = '2026-10-15T11:32:00Z';
let reply = { ok: true, status: 200, data: { kpis: [1] }, headers: { 'x-km-stale': '1' },
  stale: { at, reason: 'circuit_open' } };
const events = [];
const ctx = { api: async () => reply };
const client = createClient(ctx, {
  onUnavailable: (_e, stale) => events.push(['down', stale?.at || null]),
  onAvailable: () => events.push(['up']) });
out.data = await client.get('/v1/admin/dashboard');
out.events = [...events];
out.probeStale = await client.probe();
reply = { ok: true, status: 200, data: { kpis: [2] }, headers: {} };
out.probeFresh = await client.probe();
const band = updatingBand({ probe: async () => false, reload: () => {}, doc, timers });
band.show({ at });
out.text = band.node.text;
out.expected = `${STALE_TEXT} (${clockText(at)}).`;
console.log(JSON.stringify(out));
"""


def test_panel_son_bilinen_veriyi_gosterir_ve_denemeyi_surdurur(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import json
    import shutil
    import subprocess
    from pathlib import Path

    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    panel = Path(__file__).resolve().parents[1] / "ui" / "panel"
    for name in ("api", "updating"):
        (tmp_path / f"{name}.mjs").write_text((panel / f"{name}.js").read_text(encoding="utf-8"),
                                              encoding="utf-8")
    (tmp_path / "run.mjs").write_text(STALE_SCRIPT, encoding="utf-8")
    result = subprocess.run(["node", "run.mjs"], cwd=tmp_path, capture_output=True, text=True,
                            check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["data"] == {"kpis": [1]}                              # ekran veriyle çizilir
    assert out["events"] == [["down", "2026-10-15T11:32:00Z"]]       # bant açılır
    assert out["probeStale"] is False and out["probeFresh"] is True  # eski veri "ulaştı" sayılmaz
    assert out["text"].startswith(out["expected"])
    assert "ulaşılamıyor" in out["text"] and "otomatik yeniden deneniyor" in out["text"]
