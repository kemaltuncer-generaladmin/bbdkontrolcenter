"""UÇTAN UCA, ZAMAN SİMÜLASYONLU: ekranda değiştir → zil bilgisayarı alır → doğru saatte çalar.

Kullanıcı (13.10.2026): "zil saatlerinin düzeltilmesi — bilgisayara dönüşü yok".
Bu test zincirin her halkasını GERÇEK koduyla koşturur, yalnız ağ ve saat
taklit edilir:

  ekran ucu `PUT /api/bell/times` (routes.save_times)
    → BellService (gerçek SQLite, bütün göçler) → tetik tablosu
    → GERÇEK `WeeklyScheduler._tick` (5 sn'lik turlarla sürülen saat)
    → GERÇEK `BellBridge` (httpx; köprü `MockTransport` ile bbdstore
      `BellQueue` gibi davranır: komut kuyruğu, ses deposu, son ack)
    → GERÇEK zil ajanı (`agent/agent.py`: Library, Player, Agent._tick,
      `wait_for`) — saati ve `sleep`'i simülasyona bağlı
    → ajanın cevabı (`ack`) → KM günlüğünde "zil bilgisayarı çaldı".

Kanıtlananlar: her zil TEK komut (canlıdaki çift komut arızası), ajan zili
tam `playAt` anında çalar, saat değişince eski saat çalmaz yeni saat çalar.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from bell_backend.api import routes
from bell_backend.bridge import BellBridge
from bell_backend.service import BellService
from bell_backend.voices import VoiceLibrary
from bell_fakes import FakeAudio, FakeLog, FakeSecrets, FakeSpeech

from km_platform.scheduler.weekly import WeeklyScheduler

ISTANBUL = ZoneInfo("Europe/Istanbul")
AGENT = Path(__file__).resolve().parents[1] / "agent" / "agent.py"


def load_agent() -> Any:
    spec = importlib.util.spec_from_file_location("bbdzil_agent_e2e", AGENT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Kopru:
    """bbdstore köprüsü (`BellQueue` + `BellSounds`) — bellekte."""

    def __init__(self) -> None:
        self.seq = 0
        self.commands: list[dict[str, Any]] = []
        self.sounds: dict[str, bytes] = {}
        self.agent_sounds: list[str] = []
        self.last_ack: dict[str, Any] = {}

    # --- Kontrol Merkezi tarafı (httpx) ---------------------------------
    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer kopru-belirteci"
        path = request.url.path
        body = json.loads(request.content or b"{}")
        if request.method == "POST" and path == "/api/bell/command":
            self.seq += 1
            self.commands.append({"id": self.seq, "playAt": body["playAt"],
                                  "items": body["items"]})
            return httpx.Response(200, json={"id": self.seq})
        if request.method == "GET" and path == "/api/bell/status":
            return httpx.Response(200, json={
                "online": True, "lastSeen": "2026-10-12T08:00:00+03:00",
                "lastAck": self.last_ack, "sounds": self.agent_sounds,
                "bridgeSounds": sorted(self.sounds)})
        if request.method == "POST" and path == "/api/bell/sound":
            data = base64.b64decode(body["data"])
            sound_id = hashlib.sha256(data).hexdigest()[:16]
            self.sounds[sound_id] = data
            return httpx.Response(200, json={"id": sound_id})
        if request.method == "POST" and path == "/api/bell/prune":
            keep = set(body["keep"])
            for key in [key for key in self.sounds if key not in keep]:
                del self.sounds[key]
            return httpx.Response(200, json={"removed": 0})
        return httpx.Response(404, json={})

    # --- ajan tarafı (agent.Bridge yüzü) --------------------------------
    def poll(self, since: int, have: list[str]) -> dict[str, Any]:
        self.agent_sounds = list(have)
        return {"commands": [c for c in self.commands if c["id"] > since],
                "sounds": sorted(self.sounds)}

    def ack(self, command_id: int, ok: bool, detail: str, have: list[str]) -> None:
        self.last_ack = {"id": command_id, "ok": ok, "detail": detail,
                         "at": "2026-10-12T08:40:00+03:00"}

    def download(self, sound_id: str) -> bytes:
        return self.sounds[sound_id]


class Sim:
    """Ortak simülasyon saati — KM, zamanlayıcı ve ajan aynı saate bakar."""

    def __init__(self, start: datetime) -> None:
        self.now = start


@pytest.fixture
def agent_mod(monkeypatch: pytest.MonkeyPatch) -> Any:
    return load_agent()


async def test_ekranda_degistir_ajan_alir_dogru_saatte_calar(
        store: Any, sounds: Path, tmp_path: Path, agent_mod: Any,
        monkeypatch: pytest.MonkeyPatch) -> None:
    sim = Sim(datetime(2026, 10, 12, 8, 30, tzinfo=ISTANBUL))      # pazartesi
    kopru = Kopru()
    log = FakeLog()

    # ------------------------------------------------ Kontrol Merkezi
    scheduler = WeeklyScheduler(log, timezone="Europe/Istanbul")
    voices = VoiceLibrary(store=store, log=log, speech=FakeSpeech(), sounds_path=sounds,
                          config={"min_interval_seconds": 0})
    bridge = BellBridge(secrets=FakeSecrets({"bell.bridge_token": "kopru-belirteci"}),
                        log=log, base_url="https://kopru.test", lead_seconds=30,
                        transport=httpx.MockTransport(kopru.handle))
    service = BellService(store=store, log=log, audio=FakeAudio(sounds, ready=False),
                          scheduler=scheduler, voices=voices, bridge=bridge,
                          play_locally=False,
                          today=lambda: sim.now.date(), now=lambda: sim.now)
    routes.bind(service)
    user = SimpleNamespace(full_name="Hasan Hüseyin Bardakcı")

    # ------------------------------------------------ zil bilgisayarı
    played: list[tuple[datetime, list[str]]] = []

    class AjanSaati(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> Any:  # type: ignore[override]
            return sim.now if tz is None else sim.now.astimezone(tz)

    slept: list[float] = []
    monkeypatch.setattr(agent_mod, "datetime", AjanSaati)
    monkeypatch.setattr(agent_mod, "time", SimpleNamespace(sleep=slept.append))
    library = agent_mod.Library(tmp_path / "ajan" / "sounds", kopru)
    player = agent_mod.Player(tmp_path / "ajan" / "cache")
    current: list[str] = []
    started: list[datetime] = []
    tick_from = [0]
    real_play = player.play

    def play(path: Path, volume: int) -> None:
        if not current:
            # Ajan `playAt` anına dek bekledi: ilk sesin çaldığı an.
            started.append(sim.now + timedelta(seconds=sum(slept[tick_from[0]:])))
        current.append(path.stem)
        real_play(path, volume)

    player.play = play
    agent = agent_mod.Agent.__new__(agent_mod.Agent)
    agent._bridge, agent._library, agent._player = kopru, library, player
    agent._since, agent._syncing = 0, threading.Lock()

    def agent_tick() -> None:
        library.sync(sorted(kopru.sounds))          # arka plan senkronu (iş parçacıksız)
        tick_from[0] = len(slept)
        agent._tick()
        if current:
            played.append((started.pop(), list(current)))
            current.clear()

    async def run_until(end: datetime) -> None:
        while sim.now <= end:
            await scheduler._tick(sim.now)
            agent_tick()
            sim.now += timedelta(seconds=5)

    async def prepare() -> None:
        await voices._queue.join()                  # zil sonrası anons sesi üretildi
        await service.sync_sounds()                 # sesler köprüde

    # 1) Ekrandan 08:40 ders zili girildi.
    saved = await routes.save_times(routes.TimesBody(times={
        "mon": [{"time": "08:40", "kind": "ders", "label": "1. ders"}]}), user=user)
    assert saved["ok"]
    await prepare()
    await run_until(datetime(2026, 10, 12, 8, 42, tzinfo=ISTANBUL))

    # Tek komut (çift komut arızası yok), tam saatinde, zil + "İyi dersler."
    assert [c["playAt"] for c in kopru.commands] == ["2026-10-12T08:40:00+03:00"]
    assert [item["kind"] for item in kopru.commands[0]["items"]] == ["zil", "anons"]
    assert len(played) == 1
    assert played[0][0] == datetime(2026, 10, 12, 8, 40, tzinfo=ISTANBUL)
    assert kopru.last_ack["ok"] is True and kopru.last_ack["id"] == 1, kopru.last_ack

    # KM günlüğü zil bilgisayarının cevabını gösterir.
    bridge.forget_status()
    state = await routes.state(user=user)
    row = next(item for item in state["log"] if item["command_id"] == 1)
    assert (row["agent_ok"], row["agent_detail"]) == (1, "2 ses çaldı")
    assert state["schedule"]["next"] == []          # bugün başka zil yok

    # 2) Ekranda düzeltme: 08:40 → 08:50, araya 08:45 teneffüs.
    await routes.save_times(routes.TimesBody(times={
        "mon": [{"time": "08:50", "kind": "ders"}, {"time": "08:45", "kind": "teneffus"}]}),
        user=user)
    await prepare()
    bridge.forget_status()
    state = await routes.state(user=user)
    assert [(b["time"], b["kind"]) for b in state["schedule"]["next"][:2]] == [
        ("08:45", "teneffus"), ("08:50", "ders")]
    await run_until(datetime(2026, 10, 12, 8, 52, tzinfo=ISTANBUL))

    assert [c["playAt"] for c in kopru.commands] == [
        "2026-10-12T08:40:00+03:00", "2026-10-12T08:45:00+03:00",
        "2026-10-12T08:50:00+03:00"]
    assert [moment.strftime("%H:%M:%S") for moment, _ in played] == [
        "08:40:00", "08:45:00", "08:50:00"]
    # Teneffüs zilinin arkasından teneffüs anonsu: ses farklı.
    assert played[1][1][1] != played[2][1][1]
