"""Panelin zil planı / anons adı mantığı (`ui/panel/bell-plan.js`) — Node ile.

`bell-plan.js` bilerek import'suz ve DOM'suz yazıldı: dosya `.mjs` kopyası
olarak Node'da yüklenir, işlevler çağrılır, sonuç JSON olarak buraya döner.
(`.mjs` kopyası şart — `node` `.js` uzantılı ESM'de sözdizimini sessizce
geçebilir; bkz. tests/integration/test_panel_sozdizimi.py.)

`node` yoksa ATLANIR: çekirdek Python'dur, Node yalnız kabuk aracıdır.
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
import * as bp from './bell-plan.mjs';
const status = {
  bellEnabled: true, sessionCallsEnabled: false,
  agent: { online: false, lastSeenAt: '2026-10-12T09:29:40+03:00' },
  days: [
    { date: '2026-10-12', mode: 'normal', source: 'akademi', calls: 18, voicesPending: 1,
      lastDispatchAt: '2026-10-12T09:00:00+03:00', override: null },
    { date: '2026-10-13', mode: 'profile', source: 'akademi', calls: '4', voicesPending: 2 },
    { date: '2026-10-14', mode: 'off', source: 'km', calls: 0,
      override: { mode: 'off', by: 'Ayşe', at: '2026-10-14T07:00:00+03:00' } },
    { date: '2026-10-15', mode: 'normal', source: 'none', calls: 0, voicesPending: null },
    { date: '2026-10-16', source: 'garip' },
    null,
  ],
};
const body = { kind: 'holiday', title: 'Bayram' };
const out = {
  clean: bp.cleanAnnounceName('  Hüseyin   hoca \n'),
  cleanNull: bp.cleanAnnounceName(null),
  errOk: bp.announceNameError(` ${'a'.repeat(60)} `),
  errLong: bp.announceNameError('a'.repeat(61)),
  errEmpty: bp.announceNameError(''),
  defHoliday: bp.defaultBellChoice('holiday'),
  defEvent: bp.defaultBellChoice('exam_day'),
  choicesNone: bp.bellChoices(null).map((o) => o.value),
  choices: bp.bellChoices([{ id: 'yarim-gun', name: 'Yarım gün' }, null, { name: 'kimliksiz' }]),
  choicesKeep: bp.bellChoices([], 'profile:silinmis').map((o) => o.value),
  fromNone: bp.choiceFromBell(null, 'holiday'),
  fromNoneEvent: bp.choiceFromBell(undefined, 'event'),
  fromProfile: bp.choiceFromBell({ mode: 'profile', profileId: 'deneme' }, 'event'),
  fromProfileNoId: bp.choiceFromBell({ mode: 'profile', profileId: null }, 'event'),
  fromOff: bp.choiceFromBell({ mode: 'off' }, 'event'),
  payloadProfile: bp.bellPayload('profile:yarim-gun', 1),
  payloadOff: bp.bellPayload('off', false),
  payloadBad: bp.bellPayload('profile:', false),
  payloadEmpty: bp.bellPayload(undefined, undefined),
  withOff: bp.withBell(body, { enabled: false, choice: 'off', cancelLessons: true }),
  withOn: bp.withBell(body, { enabled: true, choice: 'off', cancelLessons: true }),
  bodyUntouched: body,
  textProfile: bp.bellText({ mode: 'profile', profileId: 'y' }, [{ id: 'y', name: 'Yarım gün' }], true),
  textOff: bp.bellText({ mode: 'off' }),
  textNone: bp.bellText(null),
  summary: bp.summarizePlanStatus(status, '2026-10-12'),
  summaryEmpty: bp.summarizePlanStatus({}, '2026-10-12'),
  summaryAkademi: bp.summarizePlanStatus({
    bellEnabled: true, sessionCallsEnabled: true, agentOnline: true,
    agentLastSeenAt: '2026-10-12T09:00:00+03:00', lastPushAt: '2026-10-12T09:12:00+03:00',
    lastPushResult: 'partial', stale: true,
    days: [
      { date: '2026-10-12', mode: 'off', reason: 'Cumhuriyet Bayramı', source: 'akademi', inSync: true,
        calls: 0, voicesPending: 0, override: null },
      { date: '2026-10-13', mode: 'normal', source: 'km', inSync: false, calls: 3, voicesPending: 0,
        override: 'Bugün zili kapat (Ayşe)' },
    ],
  }, '2026-10-12'),
  fallback404: bp.shouldFallBack({ status: 404 }),
  fallbackFlag: bp.shouldFallBack({ status: 403, code: 'feature_disabled' }),
  fallbackForbidden: bp.shouldFallBack({ status: 403, code: 'forbidden' }),
  fallbackDown: bp.shouldFallBack({ status: 503, code: 'akademi_unavailable' }),
  akFlag: bp.planStatusProblem({ status: 403, code: 'feature_disabled' }, 'akademi'),
  akBell: bp.planStatusProblem({ status: 503, code: 'bell_unavailable' }, 'akademi'),
  modeToday: bp.todayModeText({ mode: 'off', reason: 'Cumhuriyet Bayramı' }),
  modeNone: bp.todayModeText(null),
  modeNormal: bp.todayModeText({ mode: 'normal' }),
  km404: bp.planStatusProblem({ status: 404, message: 'Not Found' }, 'km'),
  km403: bp.planStatusProblem({ status: 403 }, 'km'),
  km500: bp.planStatusProblem({ status: 500, message: 'boom' }, 'km'),
  ak404: bp.planStatusProblem({ status: 404 }, 'akademi'),
};
process.stdout.write(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def result(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    folder = tmp_path_factory.mktemp("bell_plan")
    (folder / "bell-plan.mjs").write_bytes((PANEL / "bell-plan.js").read_bytes())
    (folder / "run.mjs").write_text(SCRIPT, encoding="utf-8")
    done = subprocess.run(["node", str(folder / "run.mjs")], capture_output=True, text=True,
                          check=False, timeout=60)
    assert done.returncode == 0, done.stderr
    data: dict[str, Any] = json.loads(done.stdout)
    return data


def test_anons_adi_kirpilir_ve_60_ile_sinirli(result: dict[str, Any]) -> None:
    assert result["clean"] == "Hüseyin hoca"
    assert result["cleanNull"] == ""
    assert result["errOk"] is None
    assert result["errEmpty"] is None
    assert "60" in result["errLong"] and "61" in result["errLong"]


def test_zil_secenekleri_ve_varsayilan(result: dict[str, Any]) -> None:
    assert result["defHoliday"] == "off"
    assert result["defEvent"] == "normal"
    assert result["choicesNone"] == ["normal", "off"]
    assert [item["value"] for item in result["choices"]] == ["normal", "off", "profile:yarim-gun"]
    assert result["choices"][2]["label"] == "Saat seti: Yarım gün"
    assert result["choicesKeep"] == ["normal", "off", "profile:silinmis"]
    assert result["fromNone"] == "off"
    assert result["fromNoneEvent"] == "normal"
    assert result["fromProfile"] == "profile:deneme"
    assert result["fromProfileNoId"] == "normal"
    assert result["fromOff"] == "off"


def test_govde_salter_kapaliyken_degismez(result: dict[str, Any]) -> None:
    assert result["payloadProfile"] == {"mode": "profile", "profileId": "yarim-gun"}
    assert result["payloadOff"] == {"mode": "off", "profileId": None}
    assert result["payloadBad"]["mode"] == "normal"
    assert result["payloadEmpty"] == {"mode": "normal", "profileId": None}
    # Sözleşmede alan yokken gövdeye HİÇBİR şey eklenmez.
    assert result["withOff"] == {"kind": "holiday", "title": "Bayram"}
    assert result["withOn"]["bell"] == {"mode": "off", "profileId": None}
    assert result["withOn"]["cancelLessons"] is True
    assert result["bodyUntouched"] == {"kind": "holiday", "title": "Bayram"}
    assert result["textProfile"] == "Saat seti: Yarım gün · dersler iptal"
    assert result["textOff"].startswith("Tatil")
    assert result["textNone"] is None


def test_plan_durumu_ozeti(result: dict[str, Any]) -> None:
    summary = result["summary"]
    assert summary["total"] == 5
    assert summary["counts"] == {"akademi": 2, "km": 1, "none": 2}
    assert summary["voicesPending"] == 3
    assert summary["bellEnabled"] is True
    assert summary["sessionCalls"] is False
    assert summary["agentOnline"] is False
    assert summary["agentLastSeenAt"] == "2026-10-12T09:29:40+03:00"
    assert summary["overrides"] == [{"date": "2026-10-14", "text": "Ayşe"}]
    assert summary["outOfSync"] == 0 and summary["stale"] is False
    assert summary["lastPushAt"] is None
    today = summary["today"]
    assert today["calls"] == 18 and today["voicesPending"] == 1
    assert today["mode"] == "normal" and today["source"] == "akademi"

    empty = result["summaryEmpty"]
    assert empty["total"] == 0 and empty["today"] is None
    assert empty["bellEnabled"] is None and empty["agentOnline"] is None

    assert result["modeToday"] == "Tatil — zil çalmaz · Cumhuriyet Bayramı"
    assert result["modeNone"] == "Bugün için plan yok"
    assert result["modeNormal"] == "Normal"


def test_durum_okuma_hatasi_ham_gosterilmez(result: dict[str, Any]) -> None:
    assert result["km404"]["tone"] == "info"
    assert "desteklemiyor" in result["km404"]["text"]
    assert "Not Found" not in result["km404"]["text"]
    assert "bell.view" in result["km403"]["text"]
    assert result["km500"]["tone"] == "warn" and "boom" in result["km500"]["text"]
    assert "Akademi" in result["ak404"]["text"]


def test_akademi_bicimi_de_okunur(result: dict[str, Any]) -> None:
    """Akademi `BellPlanStatus`: ajan üst düzeyde, `override` kısa metin, `inSync`, `stale`."""
    summary = result["summaryAkademi"]
    assert summary["agentOnline"] is True
    assert summary["agentLastSeenAt"] == "2026-10-12T09:00:00+03:00"
    assert summary["lastPushAt"] == "2026-10-12T09:12:00+03:00"
    assert summary["lastPushResult"] == "partial"
    assert summary["stale"] is True
    assert summary["outOfSync"] == 1
    assert summary["counts"] == {"akademi": 1, "km": 1, "none": 0}
    assert summary["overrides"] == [{"date": "2026-10-13", "text": "Bugün zili kapat (Ayşe)"}]
    assert summary["today"]["mode"] == "off"
    assert summary["today"]["reason"] == "Cumhuriyet Bayramı"


def test_akademi_desteklemiyorsa_kmye_dusulur(result: dict[str, Any]) -> None:
    assert result["fallback404"] is True
    assert result["fallbackFlag"] is True
    # Yetki ya da bağlantı sorunu gizlenmez.
    assert result["fallbackForbidden"] is False
    assert result["fallbackDown"] is False
    assert "bell_plan_sync" in result["akFlag"]["text"]
    assert result["akBell"]["tone"] == "warn"
