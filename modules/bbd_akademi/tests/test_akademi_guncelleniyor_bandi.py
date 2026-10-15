"""Panel: "Akademi şu an güncelleniyor" bandı (`ui/panel/updating.js`) ve istemci kancaları (`api.js`).

İki dosya da import'suzdur: `.mjs` kopyaları Node'da sahte DOM ve elle ilerletilen
sayaçla yüklenir. `node` yoksa ATLANIR.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node kurulu değil")

SCRIPT = r"""
import { createClient, isUpdating, problem, AkademiError } from './api.mjs';
import { updatingBand, UPDATING_TEXT } from './updating.mjs';

class El {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.hidden = false;
    this.textContent = ''; this.listeners = {}; this.disabled = false; this.className = ''; }
  setAttribute(k, v) { this.attrs[k] = v; }
  addEventListener(k, fn) { this.listeners[k] = fn; }
  replaceChildren(...c) { this.children = c; }
  get text() { return this.children.map((c) => c.textContent).join(' | '); }
}
const doc = { createElement: (tag) => new El(tag) };
let intervals = [];
const timers = { setInterval: (fn) => { intervals.push(fn); return intervals.length; },
  clearInterval: () => { intervals = []; } };
const tick = async (n = 1) => { for (let i = 0; i < n; i += 1) { for (const fn of [...intervals]) fn(); await null; } };
const out = {};

// --- istemci: GET "güncelleniyor" → onUnavailable; probe → onAvailable
const UPD = { ok: false, status: 503, error: { code: 'akademi_unavailable', message: 'Akademi şu an güncelleniyor; …',
  details: { reason: 'updating', retryable: true } } };
let reply = UPD;
const calls = [];
const events = [];
const ctx = { api: async (url, opts) => { calls.push(opts.body); return reply; } };
const client = createClient(ctx, { onUnavailable: () => events.push('down'), onAvailable: () => events.push('up') });
try { await client.get('/v1/admin/finance/summary', { termId: 't1' }); } catch (e) {
  out.getUpdating = isUpdating(e); out.getMessage = problem(e); }
try { await client.post('/v1/admin/finance/accounts/x/payments', { a: 1 }); } catch (e) {
  out.postUpdating = isUpdating(e); }
out.eventsAfterFail = [...events];
reply = { ok: true, status: 200, data: { ok: 1 }, headers: {} };
out.probe = await client.probe();
out.probePayload = calls[calls.length - 1];
out.eventsAfterProbe = [...events];
out.gateway502 = isUpdating(new AkademiError({ code: 'akademi_error', status: 502 }));
out.appError = isUpdating(new AkademiError({ code: 'maintenance', status: 503 }));

// --- bant: geri sayım → deneme → geri çekilme → kurtarma
let probeResult = false;
let probes = 0;
let reloads = 0;
let busy = false;
const band = updatingBand({ probe: async () => { probes += 1; return probeResult; },
  reload: () => { reloads += 1; }, busy: () => busy, doc, timers });
out.initiallyHidden = band.node.hidden;
band.show();
out.shown = { hidden: band.node.hidden, state: band.state, text: band.node.text, role: band.node.attrs.role };
band.show();                      // ikinci hata sayacı sıfırlamaz
await tick(5);
await null; await null;
out.afterFirstProbe = { probes, state: band.state, text: band.node.text };
probeResult = true;
await tick(10);
await null; await null;
out.afterRecovery = { probes, reloads, state: band.state, hidden: band.node.hidden };
busy = true;
band.show();
band.recovered();                 // pencere açık: ekran kurulmaz, "Yenile" önerilir
out.busyRecovery = { reloads, state: band.state, text: band.node.text };
band.node.children[1].listeners.click();
out.afterManualReload = { reloads, state: band.state, hidden: band.node.hidden };
out.updatingText = UPDATING_TEXT;
console.log(JSON.stringify(out));
"""


def _run(tmp_path: Path) -> dict[str, Any]:
    for name in ("api", "updating"):
        (tmp_path / f"{name}.mjs").write_text((PANEL / f"{name}.js").read_text(encoding="utf-8"),
                                              encoding="utf-8")
    (tmp_path / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    result = subprocess.run(["node", "run.mjs"], cwd=tmp_path, capture_output=True, text=True,
                            check=False, timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_bant_ve_istemci_kancalari(tmp_path: Path) -> None:
    out = _run(tmp_path)
    # İstemci: okuma "güncelleniyor" → bant; yazma bandı tetiklemez; deneme son okumayı tekrarlar.
    assert out["getUpdating"] is True
    assert "güncelleniyor" in out["getMessage"]
    assert out["postUpdating"] is True
    assert out["eventsAfterFail"] == ["down"]
    assert out["probe"] is True
    assert out["probePayload"] == {"method": "GET", "path": "/v1/admin/finance/summary",
                                   "query": {"termId": "t1"}}
    assert out["eventsAfterProbe"] == ["down", "up"]
    assert out["gateway502"] is True and out["appError"] is False
    # Bant: metin kullanıcı kararıyla birebir; 5 sn sonra dener, olmazsa 10 sn bekler.
    assert out["updatingText"] == ("Akademi şu an güncelleniyor, birkaç saniye sonra otomatik "
                                   "yeniden denenecek.")
    assert out["initiallyHidden"] is True
    assert out["shown"]["hidden"] is False and out["shown"]["state"] == "waiting"
    assert "(5 sn)" in out["shown"]["text"] and "Şimdi dene" in out["shown"]["text"]
    assert out["shown"]["role"] == "status"
    assert out["afterFirstProbe"]["probes"] == 1
    assert out["afterFirstProbe"]["state"] == "waiting"
    assert "(10 sn)" in out["afterFirstProbe"]["text"]
    # Akademi döndü: bant kalkar, açık ekran yeniden kurulur.
    assert out["afterRecovery"] == {"probes": 2, "reloads": 1, "state": "hidden", "hidden": True}
    # Pencere açıkken ekran kendiliğinden kurulmaz; "Yenile" ile kurulur.
    assert out["busyRecovery"]["reloads"] == 1
    assert out["busyRecovery"]["state"] == "recovered"
    assert "Yenile" in out["busyRecovery"]["text"]
    assert out["afterManualReload"] == {"reloads": 2, "state": "hidden", "hidden": True}
