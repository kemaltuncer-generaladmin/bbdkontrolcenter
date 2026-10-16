"""Ders kayıtları (F-DK, akıllı tahta; Akademi README §14 madde 110) — KM paneli.

Kural: her yönetici özelliği KM'den de yönetilebilir. Bu dosya bağlar:

- `lesson-recordings-logic.js` (saf, Node): tahta durumu / mikrofon uyarısı (harici mikrofon zorunlu, HFP uyarısı),
  süzgeç → sorgu, süre / boyut (ondalık GB — sözleşmeyle aynı), kota çubuğu ve tahmini dolma cümlesi, ayar gövdesi
  (saklama 1–120, tavan 10–800 GB), paylaşım gövdesi (100 öğrenci / 20 grup), metinler web / mobil ile aynı;
- vekil: 23 işlem izin listesinde, bayrak `lesson_recordings`, KM izni `recordings`; ayar YAZMAK kurum ayarı;
- `lesson-recordings.js` (DOM taklidiyle): sekmeler, tahta ekle → eşleme kodu, iptal, tanılama, derslik ekle; kayıt
  süzgeci, gizle, paylaş, sil; depolama çubuğu; ayar kaydı (süre kısalınca uyarı); anahtar izni eksikse yol gösterir;
  hepsi erişilebilirlik denetiminden geçer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import RECORDINGS, SETTINGS, km_permissions

ROOT = Path(__file__).resolve().parents[3]
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

DK_OPERATIONS = (
    "admin_listBoards", "admin_createBoard", "admin_updateBoard", "admin_deleteBoard", "admin_createBoardPairingCode",
    "admin_cancelBoardPairingCode", "admin_revokeBoard", "admin_getBoardDiagnostics", "admin_requestBoardDiagnostics",
    "admin_listRooms", "admin_createRoom", "admin_updateRoom", "admin_deleteRoom", "admin_listRecordings",
    "admin_getRecording", "admin_updateRecording", "admin_deleteRecording", "admin_listRecordingViews", "admin_listRecordingShares",
    "admin_addRecordingShares", "admin_removeRecordingShare", "admin_getRecordingQuota", "admin_getRecordingSettings",
    "admin_putRecordingSettings",
)


def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    for op_id in DK_OPERATIONS:
        assert op_id in ops, op_id
        assert ops[op_id].feature_flag == "lesson_recordings", op_id
        assert ops[op_id].surface == "both", op_id
        expected = (SETTINGS,) if op_id == "admin_putRecordingSettings" else (RECORDINGS,)
        assert km_permissions(ops[op_id]) == expected, op_id
    # Öğrenci / veli / öğretmen / tahta uçları vekilden GEÇMEZ (entegrasyon anahtarıyla çağrılamaz).
    assert not [op for op in OPERATIONS if op.path.startswith(("/v1/board", "/v1/recordings", "/v1/teacher"))]
    assert "students_listRecordings" not in ops


LOGIC = r"""
import * as m from './logic.mjs';
const tryIt = (fn) => { try { return fn(); } catch (error) { return `HATA: ${error.message}`; } };
const dev = (extra) => ({ id: 'd1', name: 'Tahta 1', status: 'active', online: true, activeRecordingId: null,
  microphoneName: 'DJI Mic 2', microphoneKind: 'usb', microphoneFallback: false, room: { id: 'r1', name: 'Derslik 3' },
  pairingExpiresAt: null, ...extra });
const out = {
  tabs: m.TABS.map((t) => t.label),
  states: [dev({}), dev({ activeRecordingId: 'x' }), dev({ online: false }), dev({ status: 'revoked' }),
    dev({ status: 'pending_pairing', online: false })].map((d) => m.boardState(d).label),
  mics: [dev({}), dev({ microphoneKind: 'bluetooth_hfp' }), dev({ microphoneFallback: true }),
    dev({ microphoneName: null }), dev({ microphoneKind: 'builtin' })].map((d) => m.micText(d)),
  actions: [dev({}), dev({ status: 'revoked' }), dev({ status: 'pending_pairing', pairingExpiresAt: 'x' })].map(m.boardActions),
  live: [m.liveText([dev({ activeRecordingId: 'x' }), dev({ id: 'd2', name: 'Tahta 2', room: null, activeRecordingId: 'y' })]),
    m.liveText([dev({})])],
  queries: [m.recordingQuery('all', { teacherId: ' t1 ', from: '', sort: 'zzz' }), m.recordingQuery('ready', {}),
    m.recordingQuery('hidden', { groupId: 'g1', sort: 'largest' }), m.recordingQuery('recovered'), m.recordingQuery('failed'),
    m.recordingQuery('all', { q: '  Derslik   3 ' }), m.recordingQuery('all', { q: 'a' }), m.recordingQuery('all', { q: 'x'.repeat(70) })],
  range: [m.rangeProblem('2026-10-10', '2026-10-01'), m.rangeProblem('2026-10-01', '2026-10-10')],
  durations: [45, 38 * 60, 65 * 60, 2 * 3600].map(m.durationText),
  sizes: [600_000_000, 412_000_000_000, 35_000_000, 0].map(m.gbText),
  rec: (() => {
    const r = { subject: { name: 'Matematik' }, groups: [{ name: '8-A' }], part: 2, scene: 'board_cam', room: 'Derslik 3',
      flags: ['recovered', 'fallback_mic'], status: 'ready', hidden: false, recovered: true, durationSeconds: 2280,
      sizeBytes: 600_000_000, viewStats: { watchedCount: 9, eligibleCount: 28 } };
    return { title: m.recordingTitle(r), extra: m.recordingExtra(r), state: m.recordingState(r), size: m.sizeText(r),
      views: m.viewsText(r.viewStats), hidden: m.recordingState({ ...r, hidden: true })[0],
      live: m.recordingState({ status: 'recording' })[0], hide: m.hideBody(r) };
  })(),
  share: [tryIt(() => m.shareBody({ studentIds: ['s1', 's1'], groupIds: [], notify: true })),
    tryIt(() => m.shareBody({})), tryIt(() => m.shareBody({ studentIds: Array.from({ length: 101 }, (_, i) => `s${i}`) })),
    tryIt(() => m.shareBody({ groupIds: ['g1'], notify: false }))],
  shareLabels: [m.shareLabel({ kind: 'student', student: { displayName: 'Can Yılmaz', classGroup: { name: '8-B' } } }),
    m.shareLabel({ kind: 'group', group: { name: '8-C' } })],
  intro: m.shareIntro({ groups: [{ name: '8-A' }], viewStats: { eligibleCount: 28 } }),
  quota: m.quotaView({ usedBytes: 412_000_000_000, quotaBytes: 800_000_000_000, usedPercent: 51.5,
    highWatermarkPercent: 90, lowWatermarkPercent: 80, state: 'ok' }),
  quotaHigh: m.quotaView({ usedBytes: 730_000_000_000, quotaBytes: 800_000_000_000 }),
  eta: [m.etaText({ retentionDays: 120, highWatermarkPercent: 90, lowWatermarkPercent: 80 }, '9 Aralık 2026'),
    m.etaText({ retentionDays: 60 }, null)],
  settings: [tryIt(() => m.settingsBody({ retentionDays: '90', quotaGb: '500', quality: '720p25', allowCameraScenes: false })),
    tryIt(() => m.settingsBody({ retentionDays: 121, quotaGb: 800, quality: '1080p25' })),
    tryIt(() => m.settingsBody({ retentionDays: 30, quotaGb: 900, quality: '1080p25' })),
    tryIt(() => m.settingsBody({ retentionDays: 30, quotaGb: 100, quality: '4k' }))],
  draft: m.settingsDraft({ retentionDays: 120, quotaBytes: 800_000_000_000, quality: '1080p25', allowCameraScenes: true }),
  warn: [m.settingsWarning({ retentionDays: 120, quotaGb: 800 }, { retentionDays: 90, quotaGb: 800 }),
    m.settingsWarning({ retentionDays: 90, quotaGb: 800 }, { retentionDays: 120, quotaGb: 800 })],
  room: [tryIt(() => m.roomBody({ name: '  Derslik   3 ', capacity: '30' })), tryIt(() => m.roomBody({ name: '' })),
    tryIt(() => m.roomBody({ name: 'A', capacity: 0 }))],
  board: [tryIt(() => m.boardBody({ name: ' Tahta 1 ', roomId: '' })), tryIt(() => m.boardBody({ name: ' ' }))],
  roomSub: [m.roomSub({ id: 'r1' }, [dev({})]), m.roomSub({ id: 'r9', boardCount: 0 }, [dev({})])],
  key: [m.keyLacksPermission({ code: 'forbidden', details: null }),
    m.keyLacksPermission({ code: 'forbidden', details: { operationId: 'admin_listBoards' } })],
  micEvents: [m.micEventsText({ disconnects: 2, reconnects: 2, silences: 1, fallbackSeconds: 300, silentSeconds: 0 }),
    m.micEventsText({ disconnects: 0, reconnects: 0, silences: 0, fallbackSeconds: 0, silentSeconds: 0 })],
};
process.stdout.write(JSON.stringify(out));
"""


def _node_logic(folder: Path) -> dict[str, Any]:
    if shutil.which("node") is None:
        pytest.skip("node kurulu değil")
    (folder / "logic.mjs").write_bytes((PANEL / "lesson-recordings-logic.js").read_bytes())
    (folder / "run.mjs").write_text(LOGIC, encoding="utf-8")
    done = subprocess.run(["node", "run.mjs"], cwd=folder, capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    out: dict[str, Any] = json.loads(done.stdout)
    return out


def test_saf_mantik(tmp_path: Path) -> None:
    out = _node_logic(tmp_path)
    # Tasarım panosu ve web yönetici ekranıyla aynı sekme adları.
    assert out["tabs"] == ["Tahtalar ve derslikler", "Kayıtlar", "Depolama", "Ayarlar"]
    assert out["states"] == ["Çevrimiçi", "Kayıtta", "Çevrimdışı", "İptal edildi", "Eşleme bekliyor"]
    ok, hfp, fallback, none, builtin = out["mics"]
    assert ok == {"text": "DJI Mic 2 · USB", "warn": False, "hint": ""}
    assert hfp["warn"] and hfp["hint"].startswith("Ses kalitesi düşük olabilir")
    assert fallback["hint"] == "Mikrofon bağlantısı koptu"
    assert none == {"text": "Takılı değil", "warn": True, "hint": "Harici mikrofonu tak"}
    assert builtin["warn"] and builtin["hint"] == "Harici mikrofonu tak"
    active, revoked, pending = out["actions"]
    assert active == {"pairLabel": "Eşleme kodu", "revoke": True, "cancelCode": False}
    assert revoked["pairLabel"] == "Yeniden eşle" and not revoked["revoke"]
    assert pending == {"pairLabel": "Yeni kod", "revoke": False, "cancelCode": True}
    assert out["live"] == ["Kayıt sürüyor: Derslik 3, Tahta 2", None]
    assert out["queries"] == [
        {"teacherId": "t1", "sort": "recent"},
        {"status": "ready", "hidden": False, "sort": "recent"},
        {"hidden": True, "groupId": "g1", "sort": "largest"},
        {"recovered": True, "sort": "recent"},
        {"status": "failed", "sort": "recent"},
        {"q": "Derslik 3", "sort": "recent"},
        {"sort": "recent"},
        {"q": "x" * 60, "sort": "recent"},
    ]
    assert out["range"] == ["Bitiş tarihi başlangıçtan önce olamaz.", None]
    assert out["durations"] == ["45 sn", "38 dk", "1 sa 5 dk", "2 sa"]
    assert out["sizes"] == ["0,6 GB", "412 GB", "35 MB", "0 GB"]
    rec = out["rec"]
    assert rec["title"] == "Matematik · 8-A · 2. bölüm"
    assert rec["extra"] == "tahta + kamera · Derslik 3 · Kurtarılmış kayıt · Ses kalitesi düşük olabilir"
    assert rec["state"] == ["Kurtarılmış", "warn"] and rec["hidden"] == "Gizli" and rec["live"] == "Kayıtta"
    assert rec["size"] == "38 dk · 0,6 GB" and rec["views"] == "9 / 28"
    assert rec["hide"] == {"hidden": True}
    assert out["share"][0] == {"notify": True, "studentIds": ["s1"]}
    assert out["share"][1] == "HATA: En az bir öğrenci ya da grup seçin."
    assert out["share"][2] == "HATA: Tek seferde en çok 100 öğrenci eklenebilir."
    assert out["share"][3] == {"notify": False, "groupIds": ["g1"]}
    assert out["shareLabels"] == ["Can Yılmaz (8-B)", "8-C grubu"]
    assert out["intro"] == ("Derse atanmış gruplar (8-A, 28 öğrenci) ve velileri zaten görür. Buraya ek öğrenci ya da "
                            "grup ekleyin.")
    quota = out["quota"]
    assert quota["summary"] == "412 GB / 800 GB kullanılıyor · %51,5"
    assert quota["tone"] == "good" and quota["low"] == 80 and quota["high"] == 90
    assert out["quotaHigh"]["state"] == "cleanup" and out["quotaHigh"]["tone"] == "bad"
    assert out["eta"][0] == ("Bu hızla 9 Aralık 2026 %90'a ulaşılır. O zaman en eski kayıtlar %80'e inene dek "
                             "kendiliğinden silinir; ayrıca her kayıt en çok 120 gün saklanır.")
    assert out["eta"][1] == "Bu hızla %90'a ulaşılmıyor; ayrıca her kayıt en çok 60 gün saklanır."
    assert out["settings"][0] == {"retentionDays": 90, "quotaBytes": 500_000_000_000, "quality": "720p25",
                                  "allowCameraScenes": False}
    assert out["settings"][1] == "HATA: Saklama süresi 1–120 gün olmalı."
    assert out["settings"][2] == "HATA: Depolama tavanı 10–800 GB olmalı."
    assert out["settings"][3] == "HATA: Kalite seçin."
    assert out["draft"] == {"retentionDays": 120, "quotaGb": 800, "quality": "1080p25", "allowCameraScenes": True}
    assert out["warn"][0].startswith("Saklama süresi 120 günden 90 güne iniyor") and out["warn"][1] is None
    assert out["room"][0] == {"name": "Derslik 3", "capacity": 30, "active": True}
    assert out["room"][1] == "HATA: Derslik adı zorunlu."
    assert out["room"][2] == "HATA: Kapasite 1–500 arasında tam sayı olmalı."
    assert out["board"] == [{"name": "Tahta 1", "roomId": None}, "HATA: Tahta adı zorunlu."]
    assert out["roomSub"] == ["Tahta 1 bağlı", "Tahta yok"]
    assert out["key"] == [True, False]
    assert out["micEvents"] == ["2 kez koptu · 2 kez geri geldi · 1 uzun sessizlik · 5 dk yedek mikrofonla", "Sorun yok"]


PRELUDE = r"""
const routes = {};
const sent = [];
globalThis.__apiHook = (method, path, options) => {
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: false, status: 404, error: { code: 'not_found', message: `taklit yok: ${key}` } };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  if (value && value.__error) return { ok: false, status: value.status || 422, error: value.__error };
  return { ok: true, status: 200, data: value, headers: {} };
};
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const change = (node, value) => { node.value = value; node.dispatchEvent(new Event('change', { bubbles: true })); };
const top = (root, selector) => root.querySelectorAll(selector).pop();

async function mount(load, api, document) {
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toasts = [];
  const app = { root, api: client, status: null, toast: (text, tone) => { toasts.push([text, tone]); },
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: {
      teachers: async () => [{ id: 't1', displayName: 'Zeynep Arslan' }],
      classGroups: async () => [{ id: 'g1', name: '8-A' }, { id: 'g2', name: '8-B' }],
      studentGroups: async () => [{ id: 'g3', name: 'LGS Etüt grubu' }],
      async options(kind, { empty } = {}) {
        const list = await this[kind]();
        const mapped = list.map((item) => ({ value: item.id, label: item.name || item.displayName }));
        return empty !== undefined ? [{ value: '', label: empty }, ...mapped] : mapped;
      },
    } };
  const { section } = await load('./panels/bbd_akademi/lesson-recordings.js');
  const view = section(app);
  root.append(view.node);
  return { root, view, toasts, app };
}
"""

DEVICE = r"""
const device = (extra = {}) => ({ id: 'd1', name: 'Tahta 1', room: { id: 'r3', name: 'Derslik 3' }, status: 'active',
  online: true, lastSeenAt: new Date(Date.now() - 60_000).toISOString(), appVersion: '1.0.3', osVersion: 'Windows 11 23H2',
  microphoneName: 'DJI Mic 2', microphoneKind: 'bluetooth_hfp', microphoneFallback: false, cameraAvailable: true,
  lastDiagnosticsAt: null, activeRecordingId: 'rec-live', queuedSegments: 0, freeDiskBytes: 120_000_000_000,
  pairingExpiresAt: null, pairedAt: '2026-10-16T08:00:00Z', createdAt: '2026-10-16T07:00:00Z', ...extra });
const room = (extra = {}) => ({ id: 'r3', name: 'Derslik 3', capacity: 30, active: true, boardCount: 1,
  createdAt: '2026-10-16T07:00:00Z', updatedAt: null, ...extra });
"""

BOARDS_UI = PRELUDE + DEVICE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  let boards = [device()];
  routes['GET /v1/admin/boards'] = () => ({ items: boards });
  routes['GET /v1/admin/rooms'] = { items: [room(), room({ id: 'r4', name: 'Derslik 4', boardCount: 0 })] };
  routes['POST /v1/admin/boards'] = (body) => {
    const created = device({ id: 'd2', name: body.name, status: 'pending_pairing', online: false, activeRecordingId: null,
      room: body.roomId ? { id: body.roomId, name: 'Derslik 4' } : null, microphoneName: null, microphoneKind: null,
      pairingExpiresAt: '2026-10-17T09:00:00Z', pairedAt: null, lastSeenAt: null });
    boards = [...boards, created];
    return { device: created, pairing: { deviceId: 'd2', code: 'K7QM-4XPA', expiresAt: '2026-10-17T09:00:00Z' } };
  };
  routes['POST /v1/admin/boards/d1/revoke'] = () => { boards = [device({ status: 'revoked', online: false, activeRecordingId: null })]; return boards[0]; };
  routes['GET /v1/admin/boards/d1/diagnostics'] = { id: 'x', deviceId: 'd1', receivedAt: '2026-10-16T08:05:00Z',
    report: { appVersion: '1.0.3', os: 'Windows 11 Pro 23H2', cpu: 'Intel Core i5-12400', ramMb: 16384, gpus: ['Intel UHD 730'],
      encoders: ['h264_qsv', 'h264_mf'], selectedEncoder: 'h264_qsv', displays: [{ name: 'JEDI', width: 3840, height: 2160, primary: true }],
      microphones: [{ name: 'DJI Mic 2', kind: 'bluetooth_hfp', fallback: false }], cameras: ['USB Kamera'],
      test: { ok: true, durationSeconds: 30, avgVideoKbps: 2050, uploadMbps: 18.4 }, trigger: 'enroll',
      collectedAt: '2026-10-16T08:04:00Z' } };
  routes['POST /v1/admin/boards/d1/diagnostics/request'] = null;
  routes['POST /v1/admin/rooms'] = (body) => room({ id: 'r5', name: body.name, capacity: body.capacity });

  const { root, view, toasts } = await mount(load, api, document);
  view.shown();
  await sleep(60);
  const out = {};
  out.tabs = root.querySelectorAll('[role="tab"]').map((tab) => tab.textContent.trim());
  out.live = root.querySelector('.ak-rec-live')?.textContent || '';
  out.live_hidden = root.querySelector('.ak-rec-live')?.hidden;
  out.boardText = root.querySelector('.kit-card')?.textContent || '';
  out.roomsQuery = sent.find((c) => c.path === '/v1/admin/rooms')?.query;
  out.a11yBoards = audit(view.node);

  // Tahta ekle → eşleme kodu penceresi.
  click(byText(root, 'button', 'Tahta ekle'));
  await sleep(30);
  let dialog = top(root, '.kit-dialog');
  out.a11yAdd = audit(dialog);
  type(dialog.querySelector('input'), 'Tahta 2');
  change(dialog.querySelector('select'), 'r4');
  click(byText(dialog, 'button', 'Tahta ekle'));
  await sleep(60);
  out.created = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/boards').map((c) => [c.body, Boolean(c.idempotencyKey)]);
  dialog = top(root, '.kit-dialog');
  out.codeTitle = dialog?.querySelector('.kit-dialog-title')?.textContent;
  out.code = dialog?.querySelector('.ak-rec-code')?.textContent;
  out.a11yCode = audit(dialog);
  click(byText(dialog, 'button', 'Tamam'));
  await sleep(40);
  out.afterAdd = root.querySelectorAll('.kit-card')[0]?.textContent || '';

  // İptal et (onaylı).
  click(byText(root, 'button', 'İptal et'));
  await sleep(20);
  dialog = top(root, '.kit-dialog');
  out.revokeText = dialog?.textContent || '';
  click(byText(dialog, 'button', 'İptal et'));
  await sleep(60);
  out.revoked = sent.filter((c) => c.path === '/v1/admin/boards/d1/revoke').length;
  out.afterRevoke = root.querySelectorAll('.kit-card')[0]?.textContent || '';

  // Ayrıntı → tanılama.
  const detail = root.querySelectorAll('button').find((b) => b.getAttribute('aria-label') === 'Tahta 1 ayrıntısı');
  click(detail);
  await sleep(60);
  const drawer = top(root, '.kit-drawer');
  out.drawerText = drawer?.textContent || '';
  out.a11yDrawer = audit(drawer);
  click(byText(drawer, 'button', 'Tanılama iste'));
  await sleep(40);
  out.diagRequested = sent.filter((c) => c.path === '/v1/admin/boards/d1/diagnostics/request').length;
  click(byText(drawer, 'button', 'Kapat'));
  await sleep(20);

  // Derslik ekle.
  click(byText(root, 'button', 'Derslik ekle'));
  await sleep(30);
  dialog = top(root, '.kit-dialog');
  out.a11yRoom = audit(dialog);
  const inputs = dialog.querySelectorAll('input');
  type(inputs[0], 'Derslik 5');
  type(inputs[1], '24');
  click(byText(dialog, 'button', 'Derslik ekle'));
  await sleep(60);
  out.roomPosted = sent.filter((c) => c.method === 'POST' && c.path === '/v1/admin/rooms').map((c) => c.body);
  out.toasts = toasts;
  return out;
}
"""


def node_run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_tahtalar_ve_derslikler() -> None:
    out = node_run(BOARDS_UI)
    assert out["tabs"] == ["Tahtalar ve derslikler", "Kayıtlar", "Depolama", "Ayarlar"]
    assert out["live_hidden"] is False and "Kayıt sürüyor: Derslik 3" in out["live"]
    text = out["boardText"]
    assert "Tahta 1" in text and "Derslik 3" in text and "Kayıtta" in text and "1.0.3" in text
    assert "DJI Mic 2 · Bluetooth HFP" in text and "Ses kalitesi düşük olabilir" in text
    assert out["roomsQuery"] == {"includeInactive": True}
    assert out["a11yBoards"] == [] and out["a11yAdd"] == [] and out["a11yCode"] == []
    assert out["created"] == [[{"name": "Tahta 2", "roomId": "r4"}, True]]
    assert out["codeTitle"] == "Tahta 2 için eşleme kodu" and out["code"] == "K7QM-4XPA"
    assert "Tahta 2" in out["afterAdd"] and "Eşleme bekliyor" in out["afterAdd"]
    assert "bağlantısı hemen kesilir" in out["revokeText"]
    assert out["revoked"] == 1
    assert "İptal edildi" in out["afterRevoke"] and "Yeniden eşle" in out["afterRevoke"]
    assert "Intel Core i5-12400" in out["drawerText"] and "h264_qsv" in out["drawerText"]
    assert "İlk eşleme" in out["drawerText"] and "18,4 Mbps" in out["drawerText"]
    assert out["a11yDrawer"] == [] and out["a11yRoom"] == []
    assert out["diagRequested"] == 1
    assert out["roomPosted"] == [{"name": "Derslik 5", "capacity": 24, "active": True}]
    assert ["Tahta 1 iptal edildi · tahtadaki oturum kapandı", "good"] in out["toasts"]


RECORDINGS_UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  const recording = (extra = {}) => ({ id: 'rec-1', status: 'ready', classSessionId: 'cs1', date: '2026-10-16',
    startsAt: '2026-10-16T07:40:00Z', endsAt: '2026-10-16T08:20:00Z', subject: { id: 'matematik', name: 'Matematik' },
    topic: null, teacher: { id: 't1', displayName: 'Zeynep Arslan' }, groups: [{ id: 'g1', name: '8-A', kind: 'class' }],
    room: 'Derslik 3', part: 1, startedAt: '2026-10-16T07:41:00Z', endedAt: '2026-10-16T08:19:00Z', durationSeconds: 2280,
    sizeBytes: 600_000_000, hasCamera: true, quality: '1080p25', recovered: false, incomplete: false, hidden: false,
    flags: [], scene: 'board_cam', microphoneEvents: { disconnects: 0, reconnects: 0, silences: 0, fallbackSeconds: 0, silentSeconds: 0 },
    expiresAt: '2027-02-13T20:59:59Z', thumbnailUrl: null, playlistUrl: null, progress: null,
    viewStats: { eligibleCount: 28, watchedCount: 9, completedCount: 5, downloadCount: 2 }, canManage: true, access: 'admin', ...extra });
  routes['GET /v1/admin/boards'] = { items: [] };
  routes['GET /v1/admin/rooms'] = { items: [] };
  routes['GET /v1/admin/recordings'] = { items: [recording()], nextCursor: null };
  routes['PATCH /v1/admin/recordings/rec-1'] = (body) => recording({ hidden: body.hidden });
  routes['GET /v1/admin/recordings/rec-1'] = () => recording({ viewStats: { eligibleCount: 30, watchedCount: 12, completedCount: 6, downloadCount: 3 } });
  routes['DELETE /v1/admin/recordings/rec-1'] = null;
  let shares = [{ id: 'sh1', kind: 'student', student: { id: 's9', displayName: 'Can Yılmaz', classGroup: { id: 'g2', name: '8-B' } },
    group: null, createdAt: '2026-10-16T09:00:00Z', createdBy: null }];
  routes['GET /v1/admin/recordings/rec-1/shares'] = () => ({ items: shares });
  routes['POST /v1/admin/recordings/rec-1/shares'] = (body) => {
    shares = [...shares, { id: 'sh2', kind: 'group', student: null, group: { id: body.groupIds[0], name: '8-B', kind: 'class' },
      createdAt: '2026-10-16T09:10:00Z', createdBy: null }];
    return { items: shares };
  };
  routes['DELETE /v1/admin/recordings/rec-1/shares/sh1'] = () => { shares = shares.filter((s) => s.id !== 'sh1'); return null; };
  routes['GET /v1/admin/recordings/rec-1/views'] = { stats: { eligibleCount: 28, watchedCount: 9, completedCount: 5, downloadCount: 2 },
    items: [{ student: { id: 's1', displayName: 'Elif Kaya', classGroup: { id: 'g1', name: '8-A' } }, source: 'assigned', watched: true,
      completed: true, positionSeconds: 2200, watchedSeconds: 2280, firstWatchedAt: '2026-10-16T15:00:00Z',
      lastWatchedAt: '2026-10-16T15:40:00Z', guardianWatched: false, downloaded: true, downloadedAt: '2026-10-16T15:00:00Z' }] };
  routes['GET /v1/admin/students'] = { items: [], nextCursor: null };

  const { root, view, toasts } = await mount(load, api, document);
  view.shown({ recTab: 'recordings' });
  await sleep(60);
  const out = {};
  out.selectedTab = root.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim();
  out.firstQuery = sent.find((c) => c.path === '/v1/admin/recordings')?.query;
  out.rowText = root.querySelector('button.kit-row')?.textContent || '';
  out.a11yList = audit(view.node);
  click(byText(root, 'button', 'Gizli'));
  await sleep(60);
  out.hiddenQuery = sent.filter((c) => c.path === '/v1/admin/recordings').pop()?.query;
  const search = root.querySelector('input[type="search"]');
  out.searchLabel = search?.getAttribute('aria-label');
  type(search, 'Z');
  await sleep(400);
  out.oneLetterCalls = sent.filter((c) => c.path === '/v1/admin/recordings').length;
  type(search, 'Zeynep');
  await sleep(400);
  out.searchQuery = sent.filter((c) => c.path === '/v1/admin/recordings').pop()?.query;

  click(root.querySelector('button.kit-row'));
  await sleep(80);
  let drawer = top(root, '.kit-drawer');
  out.drawerText = drawer?.textContent || '';
  out.a11yDrawer = audit(drawer);
  out.detailCalls = sent.filter((c) => c.method === 'GET' && c.path === '/v1/admin/recordings/rec-1').length;
  out.sharesCalls = sent.filter((c) => c.path === '/v1/admin/recordings/rec-1/shares').length;
  click(byText(drawer, 'button', 'Gizle'));
  await sleep(60);
  out.patch = sent.filter((c) => c.method === 'PATCH').map((c) => c.body);
  drawer = top(root, '.kit-drawer');
  out.afterHide = drawer?.textContent || '';

  // Paylaş: grup seç → gönder.
  click(byText(drawer, 'button', 'Paylaş'));
  await sleep(60);
  const dialog = top(root, '.kit-dialog');
  out.shareDialog = dialog?.textContent || '';
  out.a11yShare = audit(dialog);
  click(byText(dialog, 'button', '8-B'));
  click(byText(dialog, 'button', 'Paylaş'));
  await sleep(80);
  out.sharePost = sent.filter((c) => c.method === 'POST' && c.path.endsWith('/shares')).map((c) => c.body);
  drawer = top(root, '.kit-drawer');
  out.sharesAfter = drawer?.querySelector('.ak-rec-shares')?.textContent || '';
  const removeShare = drawer.querySelectorAll('button').find((b) => b.getAttribute('aria-label') === 'Can Yılmaz (8-B) paylaşımını kaldır');
  click(removeShare);
  await sleep(60);
  out.shareDeleted = sent.filter((c) => c.method === 'DELETE' && c.path.endsWith('/shares/sh1')).length;

  // Sil.
  click(byText(drawer, 'button', 'Kaydı sil'));
  await sleep(20);
  const confirm = top(root, '.kit-dialog');
  out.deleteText = confirm?.textContent || '';
  click(byText(confirm, 'button', 'Kaydı sil'));
  await sleep(60);
  out.deleted = sent.filter((c) => c.method === 'DELETE' && c.path === '/v1/admin/recordings/rec-1').length;
  out.drawerClosed = !root.querySelector('.kit-drawer');
  out.rowsAfter = root.querySelectorAll('button.kit-row').length;
  out.toasts = toasts;
  return out;
}
"""


def test_kayitlar_gizle_paylas_sil() -> None:
    out = node_run(RECORDINGS_UI)
    assert out["selectedTab"] == "Kayıtlar"
    assert out["firstQuery"] == {"sort": "recent", "limit": 50}
    row = out["rowText"]
    assert "Matematik · 8-A" in row and "Zeynep Arslan" in row and "38 dk · 0,6 GB" in row and "9 / 28" in row
    assert "Yayında" in row and "tahta + kamera" in row
    assert out["a11yList"] == [] and out["a11yDrawer"] == [] and out["a11yShare"] == []
    assert out["hiddenQuery"] == {"hidden": True, "sort": "recent", "limit": 50}
    assert out["searchLabel"] == "Kayıt ara (ders, öğretmen, derslik)"
    assert out["oneLetterCalls"] == 2                      # tek harf sunucuya gitmez
    assert out["searchQuery"] == {"hidden": True, "q": "Zeynep", "sort": "recent", "limit": 50}
    # Ayrıntı admin_getRecording ile tazelenir; paylaşım listesi tazelemede yeniden istenmez.
    assert out["detailCalls"] == 1 and out["sharesCalls"] == 1
    assert "12 / 30 öğrenci" in out["drawerText"]
    assert "Kim izledi" in out["drawerText"] and "Elif Kaya" in out["drawerText"] and "Tamamladı" in out["drawerText"]
    assert "Can Yılmaz (8-B)" in out["drawerText"]
    assert "Derse atanmış gruplar (8-A, 30 öğrenci) ve velileri zaten görür." in out["drawerText"]  # tazelenen kayıttan
    assert out["patch"] == [{"hidden": True}]
    assert "Bu kayıt gizli" in out["afterHide"] and "Göster" in out["afterHide"]
    assert out["sharePost"] == [{"notify": True, "groupIds": ["g2"]}]
    assert "8-B grubu" in out["sharesAfter"]
    assert out["shareDeleted"] == 1
    assert "indirdiği kopyalar" in out["deleteText"]
    assert out["deleted"] == 1 and out["drawerClosed"] is True and out["rowsAfter"] == 0
    assert ["Kayıt gizlendi · öğrenci ve veliler görmez", "good"] in out["toasts"]
    assert ["Matematik · 8-A kaydı silindi · indirilmiş kopyalar da kaldırılır", "good"] in out["toasts"]


STORAGE_SETTINGS_UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  routes['GET /v1/admin/boards'] = { items: [] };
  routes['GET /v1/admin/rooms'] = { items: [] };
  routes['GET /v1/admin/recordings/quota'] = { usedBytes: 412_000_000_000, quotaBytes: 800_000_000_000, usedPercent: 51.5,
    highWatermarkPercent: 90, lowWatermarkPercent: 80, state: 'ok', recordingCount: 463, oldestRecordingAt: '2026-06-18T08:00:00Z',
    avgDailyBytes: 4_400_000_000, estimatedFullDate: '2026-12-09', retentionDays: 120, lastCleanupAt: '2026-10-16T01:20:00Z',
    lastCleanupDeletedCount: 3, computedAt: '2026-10-16T09:00:00Z' };
  const settings = { retentionDays: 120, quotaBytes: 800_000_000_000, quality: '1080p25', allowCameraScenes: true,
    updatedAt: null };
  routes['GET /v1/admin/recordings/settings'] = settings;
  routes['PUT /v1/admin/recordings/settings'] = (body) => ({ ...settings, ...body, updatedAt: '2026-10-16T10:00:00Z' });

  const { root, view, toasts } = await mount(load, api, document);
  view.shown({ recTab: 'storage' });
  await sleep(60);
  const out = {};
  const pane = root.querySelector('.ak-rec-quota');
  out.quotaText = pane?.parentNode?.textContent || '';
  out.meter = [pane?.querySelector('[role="meter"]')?.getAttribute('aria-valuenow'),
    pane?.querySelector('[role="meter"]')?.getAttribute('aria-valuetext')];
  out.a11yStorage = audit(view.node);

  click(byText(root, '[role="tab"]', 'Ayarlar'));
  await sleep(60);
  out.a11ySettings = audit(view.node);
  const days = root.querySelector('#ak-rec-retention');
  out.daysValue = days?.value;
  change(days, '90');
  click(root.querySelectorAll('.ak-rec-choice').find((choice) => choice.dataset.value === '720p25'));
  const cam = root.querySelector('.ak-rec-toggle input');
  cam.checked = false;
  cam.dispatchEvent(new Event('change', { bubbles: true }));
  click(byText(root, 'button', 'Kaydet'));
  await sleep(30);
  const dialog = top(root, '.kit-dialog');
  out.warn = dialog?.textContent || '';
  click(byText(dialog, 'button', 'Kaydet'));
  await sleep(60);
  out.put = sent.filter((c) => c.method === 'PUT').map((c) => c.body);
  // Geçersiz değer gitmez.
  change(root.querySelector('#ak-rec-quota-gb'), '900');
  click(byText(root, 'button', 'Kaydet'));
  await sleep(30);
  out.badError = root.querySelector('.ak-rec-error[role="alert"]')?.textContent || '';
  out.putCount = sent.filter((c) => c.method === 'PUT').length;
  out.toasts = toasts;
  return out;
}
"""


def test_depolama_ve_ayarlar() -> None:
    out = node_run(STORAGE_SETTINGS_UI)
    text = out["quotaText"]
    assert "412 GB" in text and "/ 800 GB kullanılıyor · %51,5" in text and "%80" in text and "%90" in text
    assert "Bu hızla 9 Aralık 2026 %90'a ulaşılır." in text and "463" in text
    assert out["meter"] == ["52", "412 GB / 800 GB kullanılıyor · %51,5"]
    assert out["a11yStorage"] == [] and out["a11ySettings"] == []
    assert out["daysValue"] == "120"
    assert "120 günden 90 güne iniyor" in out["warn"]
    assert out["put"] == [{"retentionDays": 90, "quotaBytes": 800_000_000_000, "quality": "720p25",
                           "allowCameraScenes": False}]
    assert out["badError"] == "Depolama tavanı 10–800 GB olmalı."
    assert out["putCount"] == 1
    assert ["Ayarlar kaydedildi · web ve mobil yönetimde de görünür", "good"] in out["toasts"]


KEY_UI = PRELUDE + r"""
export default async function ({ document, sleep, api, load }) {
  routes['GET /v1/admin/boards'] = { __error: { code: 'forbidden', message: 'Yetkiniz yok.' }, status: 403 };
  routes['GET /v1/admin/rooms'] = { items: [] };
  const { root, view } = await mount(load, api, document);
  view.shown();
  await sleep(60);
  return { text: root.textContent };
}
"""


def test_anahtar_izni_eksikse_yol_gosterir() -> None:
    out = node_run(KEY_UI)
    assert "KM panel anahtarı ders kaydı izinlerini (boards.manage, recordings.manage) taşımıyor" in out["text"]
    assert "Tekrar dene" in out["text"]


DEEP_LINK_UI = PRELUDE + r"""
export default async function ({ document, audit, sleep, api, load }) {
  routes['GET /v1/admin/recordings'] = { items: [], nextCursor: null };
  routes['GET /v1/admin/recordings/rec-9'] = { id: 'rec-9', status: 'ready', date: '2026-10-15', startsAt: '2026-10-15T07:40:00Z',
    endsAt: '2026-10-15T08:20:00Z', subject: { id: 'fen', name: 'Fen Bilimleri' }, topic: null,
    teacher: { id: 't2', displayName: 'Ali Demir' }, groups: [{ id: 'g1', name: '8-A', kind: 'class' }], room: 'Derslik 4', part: 1,
    startedAt: '2026-10-15T07:41:00Z', endedAt: '2026-10-15T08:08:00Z', durationSeconds: 1620, sizeBytes: 400_000_000,
    hasCamera: false, quality: '720p25', recovered: true, incomplete: false, hidden: false, flags: ['recovered'], scene: 'board',
    microphoneEvents: null, expiresAt: '2027-02-12T20:59:59Z', viewStats: { eligibleCount: 28, watchedCount: 12, completedCount: 4,
      downloadCount: 0 }, canManage: true, access: 'admin' };
  routes['GET /v1/admin/recordings/rec-9/shares'] = { items: [] };
  routes['GET /v1/admin/recordings/rec-9/views'] = { stats: { eligibleCount: 28, watchedCount: 12, completedCount: 4, downloadCount: 0 }, items: [] };
  routes['GET /v1/admin/recordings/gone'] = { __error: { code: 'recording_not_found', message: 'Kayıt bulunamadı.' }, status: 404 };
  routes['GET /v1/admin/recordings/gone/shares'] = { items: [] };
  routes['GET /v1/admin/recordings/gone/views'] = { stats: {}, items: [] };
  const { root, view } = await mount(load, api, document);
  view.shown({ recordingId: 'rec-9' });
  await sleep(80);
  const drawer = top(root, '.kit-drawer');
  const out = { title: drawer?.querySelector('.kit-drawer-title')?.textContent, text: drawer?.textContent || '',
    a11y: audit(drawer), tab: root.querySelector('[role="tab"][aria-selected="true"]')?.textContent.trim() };
  click(byText(drawer, 'button', 'Kapat'));
  view.shown({ recordingId: 'gone' });
  await sleep(80);
  out.goneText = top(root, '.kit-drawer')?.textContent || '';
  return out;
}
"""


def test_derin_baglanti_admin_get_recording() -> None:
    out = node_run(DEEP_LINK_UI)
    assert out["tab"] == "Kayıtlar"
    assert out["title"] == "Fen Bilimleri · 8-A"
    text = out["text"]
    assert "Ali Demir" in text and "Derslik 4" in text and "27 dk · 0,4 GB" in text and "720p · 25 kare" in text
    assert "Kurtarılmış kayıt" in text and "12 / 28 öğrenci" in text
    assert "Derse atanmış gruplar (8-A, 28 öğrenci)" in text   # paylaşım açıklaması tazelenen kayıttan
    assert out["a11y"] == []
    assert "Kayıt bulunamadı (silinmiş olabilir)." in out["goneText"]
