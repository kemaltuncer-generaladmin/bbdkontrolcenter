"""YouTube oynatma listesi — KM paneli (Akademi README §14 madde 124, iş 68-video-liste).

Kullanıcı (17.10.2026): "hem mobil hem km de hani youtube video linkini aktarıyorduk ya direkt playlist olarak
kütüphaneye ekleme, öğrenciye atarken ister playlist isterse playlist içi video atrama gibi güzel efektif çözümler de
olsun".

- `playlist-logic.js` (saf, Node): bağlantı ayrıştırma, süre / sayı metinleri, aralık "3–7", seçim toplamı, içe aktarma
  ve atama gövdeleri (tüm / seçili / güne yay), önizleme metinleri;
- `playlist.js` (DOM taklidiyle): bağlantı → önizleme → "Bu videoyu ekle · Listenin tamamını ekle" → "Videoları ayrı
  ekle" (aralık, video başına etiket) → gövde; `409 playlist_already_added`; anahtarsız durum; liste ayrıntısı +
  "Listeyi yenile";
- `playlist-assign.js`: sihirbaz (Güne yay → Kime? → Ne zaman?), önizlemede yinelenen uyarısı, "Zaten atanmışları
  atla", tek istek;
- `playlist-progress.js`: liste ilerleme raporu ve öğrencinin liste görevi;
- Ayarlar → Entegrasyonlar → YouTube: durum, anahtar doğrula / kaydet, geçersiz anahtar;
- Video havuzu: `?kind=all`, `watch?v=…&list=` sorusu;
- vekil: uçlar izin listesinde, KM izinleri mevcut eşlemeden; panel kapsamı.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import (
    LEARNING,
    LEARNING_SETUP,
    SETTINGS,
    VIEW,
    km_permissions,
)

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

# --------------------------------------------------------------------------- ortak taklit

FAKES = r"""
const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ').trim();
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const byText = (root, selector, value) => root.querySelectorAll(selector).find((node) => text(node) === value);
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };
const check = (node, on = true) => { node.checked = on; node.dispatchEvent(new Event('change', { bubbles: true })); };
const clean = () => document.body.querySelectorAll('.kit-overlay').forEach((node) => node.remove());

const VIDEO = (n, extra = {}) => ({ provider: 'youtube', videoId: `vid${String(n).padStart(8, '0')}`,
  url: `https://www.youtube.com/watch?v=vid${String(n).padStart(8, '0')}`, title: `Türev ${n}`, channelTitle: 'Hoca',
  durationSeconds: 600 * n, thumbnailUrl: null, embeddable: true, madeForKids: false, privacy: 'public',
  ageRestricted: false, available: true, openInYouTubeOnly: false, fetchedAt: '2026-10-17T09:00:00Z', ...extra });
const ITEM = (n, status = 'playable') => ({ position: n, videoId: `vid${String(n).padStart(8, '0')}`, status,
  video: VIDEO(n, status === 'private' ? { title: 'Özel video', available: false, durationSeconds: null } : {}),
  subjectId: null, topicIds: [], tags: [] });
const PLAYLIST = {
  playlistId: 'PLturev0000001', title: 'Türev', channelTitle: 'Hoca', thumbnailUrl: null, itemCount: 5, videoCount: 5,
  playableCount: 4, unavailableCount: 1, totalSeconds: 6000, unknownDurationCount: 0, truncated: false,
  syncedAt: '2026-10-17T09:00:00Z', lastDiff: null,
  videos: [ITEM(1), ITEM(2), ITEM(3, 'not_embeddable'), ITEM(4, 'private'), ITEM(5)],
  removedVideos: [],
};

function fakeApp(routes, extra = {}) {
  const sent = [];
  const toasts = [];
  const handle = async (method, path, body, options = {}) => {
    sent.push({ method, path, body: body === undefined ? null : JSON.parse(JSON.stringify(body)), key: options.key || null });
    const key = `${method} ${path}`;
    const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
    if (!hit) throw Object.assign(new Error(`taklit yok: ${key}`), { code: 'not_found' });
    const value = typeof routes[hit] === 'function' ? await routes[hit](body, path) : routes[hit];
    if (value && value.__error) throw Object.assign(new Error(value.__error.message), value.__error);
    return JSON.parse(JSON.stringify(value));
  };
  const app = {
    root: document.body, toasts, sent,
    can: (op) => !(extra.deny || []).includes(op),
    go: (...args) => { (app.went ||= []).push(args); },
    toast: (message, tone) => toasts.push([message, tone]),
    lookups: {
      options: async () => [{ value: '', label: 'Bütün dersler' }, { value: 'matematik', label: 'Matematik' }],
      classGroups: async () => [{ id: 'c1', name: '12-A' }], studentGroups: async () => [],
      subjects: async () => [],
    },
    api: {
      can: (op) => !(extra.deny || []).includes(op), featureOff: () => false, invalidate: () => {},
      get: (path, query) => handle('GET', path, query),
      getCached: (path, query) => handle('GET', path, query),
      post: (path, body, options) => handle('POST', path, body, options),
      put: (path, body, options) => handle('PUT', path, body, options),
      del: (path) => handle('DELETE', path),
      pages: (path, query) => {
        let done = false;
        return { get done() { return done; }, async next() { done = true; const page = await handle('GET', path, query); return page.items || []; } };
      },
    },
  };
  return app;
}
"""

LOGIC = r"""
export default async function ({ load }) {
  const L = await load('./panels/bbd_akademi/coaching/playlist-logic.js');
  const out = {};
  out.links = [
    'https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG',
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG&index=3',
    'https://youtu.be/dQw4w9WgXcQ?list=PLabcdefghij',
    'https://youtube.com/shorts/dQw4w9WgXcQ',
    'PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG',
    'merhaba',
  ].map(L.parseYoutubeLink);
  out.previewBody = [L.previewBody(''), L.previewBody('https://youtu.be/dQw4w9WgXcQ'),
    L.previewBody('PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG'),
    L.previewBody('https://www.youtube.com/playlist?list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG')];
  out.single = [L.singleVideoUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghij&index=3&t=42'),
    L.singleVideoUrl('https://www.youtube.com/playlist?list=PLabcdefghij', 'dQw4w9WgXcQ')];
  out.duration = [0, 30, 600, 3600, 6000, 5999].map(L.durationText);
  out.summary = L.summaryLine({ playableCount: 12, totalSeconds: 6000, unknownDurationCount: 1 });
  out.counts = [L.countsText({ playable: 9, notEmbeddable: 2, private: 1, deleted: 1 }),
    L.countsText({ playable: 3, notEmbeddable: 0, private: 0, deleted: 0 })];
  out.diff = [L.diffText({ message: '3 yeni video, 1 kaldırıldı' }), L.diffText({ addedVideoIds: [], removedVideoIds: [] }),
    L.diffText({ addedCount: 2, removedCount: 0 })];
  out.card = L.cardLine({ title: 'Türev', videoCount: 12, completedCount: 4, remainingSeconds: 6000 });
  out.ranges = ['3–7', '3-7', ' 3 - 7 ', '5', '7-3', '0-2', '3-99', 'abc', ''].map((value) => L.parseRange(value, 12));
  const videos = [
    { position: 1, videoId: 'aaaaaaaaaa1', status: 'playable', video: { durationSeconds: 600 } },
    { position: 2, videoId: 'aaaaaaaaaa2', status: 'private', video: { durationSeconds: null } },
    { position: 3, videoId: 'aaaaaaaaaa3', status: 'not_embeddable', video: { durationSeconds: 300 } },
    { position: 4, videoId: 'aaaaaaaaaa4', status: 'playable', video: { durationSeconds: null } },
  ];
  out.rangeIds = L.rangeIds(videos, 1, 3);
  out.selection = L.selectionOf(videos, ['aaaaaaaaaa4'], { from: 1, to: 2 });
  out.selectionText = [L.selectionText(out.selection), L.selectionText({ count: 0 })];
  out.import = [
    L.importBody({ playlistId: 'PL1', mode: 'playlist', title: '  Türev  ', tags: 'türev, limit, türev',
      gradeLevels: ['12', 'graduate'], exams: ['AYT'], subjectId: 'matematik', topicIds: ['t1'],
      overrides: { aaaaaaaaaa1: { title: 'yok sayılır', tags: 'zor' } } }),
    L.importBody({ playlistId: 'PL1', mode: 'videos', videoIds: ['aaaaaaaaaa1', 'aaaaaaaaaa3'],
      overrides: { aaaaaaaaaa1: { title: 'Türev 1. ders', subjectId: 'matematik' }, aaaaaaaaaa4: { tags: 'dışarıda' } },
      exams: ['AYT'] }, 'pool'),
    L.importBody({ mode: 'playlist' }),
  ];
  const base = { source: { libraryItemId: 'L1' }, target: { classGroupIds: ['c1'] }, startDate: '2026-10-19' };
  out.assign = {
    all: L.assignBody({ ...base, mode: 'all', picked: ['x'], requireOrder: true }),
    selected: L.assignBody({ ...base, mode: 'selected', picked: ['aaaaaaaaaa1'], range: { from: 3, to: 4 },
      dueDate: '2026-10-25', title: 'Türev haftası', skipDuplicates: false }),
    selectedEmpty: L.assignBody({ ...base, mode: 'selected', picked: [] }),
    spread: L.assignBody({ ...base, mode: 'spread', picked: ['aaaaaaaaaa1', 'aaaaaaaaaa3'], spreadKind: 'perDay',
      perDay: '2', daysOfWeek: ['fri', 'mon', 'xx'], skipHolidays: true, requireOrder: true, notify: false }),
    spreadMinutes: L.assignBody({ ...base, mode: 'spread', picked: ['aaaaaaaaaa1'], spreadKind: 'minutesPerDay',
      minutesPerDay: 45, daysOfWeek: ['mon'] }),
    spreadBad: L.assignBody({ ...base, mode: 'spread', picked: ['aaaaaaaaaa1'], spreadKind: 'perDay', perDay: 0,
      daysOfWeek: ['mon'] }),
    spreadNoDay: L.assignBody({ ...base, mode: 'spread', picked: ['aaaaaaaaaa1'], daysOfWeek: [] }),
    noSource: L.assignBody({ ...base, source: {} , mode: 'all' }),
    noTarget: L.assignBody({ ...base, target: {}, mode: 'all' }),
    dueBefore: L.assignBody({ ...base, mode: 'all', dueDate: '2026-10-01' }),
    pool: L.assignBody({ ...base, source: { videoPoolItemId: 'P1' }, mode: 'all' }),
    link: L.assignBody({ ...base, source: { playlistId: 'PL1' }, mode: 'all' }),
  };
  const preview = { studentCount: 24, videoCount: 8, taskCount: 4, totalSeconds: 4800, firstDate: '2026-10-19',
    lastDate: '2026-10-22', duplicateStudentCount: 3, duplicateVideoCount: 5,
    skippedVideos: [{ reason: 'private' }, { reason: 'removed' }, { reason: 'private' }] };
  out.preview = [L.previewHeadline(preview), L.duplicateText(preview), L.duplicateText({ duplicateStudentCount: 0 }),
    L.skippedText(preview.skippedVideos), L.dayLine({ date: '2026-10-19', videoIds: ['a', 'b'], seconds: 1500 })];
  out.assignmentLine = L.assignmentLine({ mode: 'spread', videoCount: 12, studentCount: 24, firstDate: '2026-10-19',
    lastDate: '2026-10-25', requireOrder: true });
  out.importSummary = [L.importSummary({ mode: 'playlist', addedCount: 1, skipped: [] }),
    L.importSummary({ mode: 'videos', addedCount: 3, skipped: [{ reason: 'duplicate' }, { reason: 'private' }] })];
  out.keyText = L.KEY_REQUIRED_TEXT;
  return out;
}
"""


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


@pytest.fixture(scope="module")
def logic() -> dict[str, Any]:
    return _run(LOGIC)


def test_baglanti_ayristirma(logic: dict[str, Any]) -> None:
    assert logic["links"] == [
        {"playlistId": "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", "videoId": None},
        {"playlistId": "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", "videoId": "dQw4w9WgXcQ"},
        {"playlistId": "PLabcdefghij", "videoId": "dQw4w9WgXcQ"},
        {"playlistId": None, "videoId": "dQw4w9WgXcQ"},
        {"playlistId": "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG", "videoId": None},
        {"playlistId": None, "videoId": None},
    ]
    assert logic["previewBody"][0] == {"error": "Oynatma listesi bağlantısını yapıştırın."}
    assert logic["previewBody"][1] == {"error": "Bağlantıda oynatma listesi (list=…) yok."}
    assert logic["previewBody"][2] == {"body": {"playlistId": "PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG"}}
    assert logic["previewBody"][3]["body"]["url"].endswith("list=PLx0sYbCqOb8TBPRdmBHs5Iftvv9TPboYG")
    assert logic["single"] == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42",
                               "https://www.youtube.com/watch?v=dQw4w9WgXcQ"]


def test_metinler(logic: dict[str, Any]) -> None:
    assert logic["duration"] == ["0 dk", "< 1 dk", "10 dk", "1 sa", "1 sa 40 dk", "1 sa 40 dk"]
    assert logic["summary"] == "12 video · 1 sa 40 dk · 1 videonun süresi bilinmiyor"
    assert logic["counts"] == ["2 gömülemeyen · 1 özel · 1 silinmiş", ""]
    assert logic["diff"] == ["3 yeni video, 1 kaldırıldı", "Değişiklik yok", "2 yeni video"]
    assert logic["card"] == "Türev — 12 video · 4/12 izlendi · 1 sa 40 dk kaldı"
    assert logic["keyText"] == ("Oynatma listesi için YouTube API anahtarı gerekli (yönetici KM'den girer) — şimdilik "
                                "listedeki videoları tek tek ekleyebilirsin")
    assert logic["preview"] == [
        "24 öğrenci · 8 video · 1 sa 20 dk · 4 gün (19 Eki – 22 Eki)",
        "3 öğrencide 5 video zaten atanmış",
        "",
        "3 video atlanacak (2 özel video, 1 youtube'dan kaldırıldı)",
        "19 Eki · 2 video · 25 dk",
    ]
    assert logic["assignmentLine"] == "Güne yay · 12 video · 24 öğrenci · 19 Eki – 25 Eki · Sırayla"
    assert logic["importSummary"] == ["Liste eklendi.", "3 video eklendi · 1 zaten vardı · 1 atlandı (özel / silinmiş)"]


def test_aralik_ve_secim(logic: dict[str, Any]) -> None:
    assert logic["ranges"][:3] == [{"from": 3, "to": 7}] * 3
    assert logic["ranges"][3] == {"from": 5, "to": 5}
    assert logic["ranges"][4] == {"error": "Aralığın başı sonundan büyük olamaz."}
    assert logic["ranges"][5] == {"error": "Sıra 1'den başlar."}
    assert logic["ranges"][6] == {"error": "Listede 12 video var."}
    assert logic["ranges"][7] == {"error": 'Aralığı "3–7" biçiminde yazın.'}
    assert logic["ranges"][8] == {"from": None, "to": None}
    # Özel video aralıkta olsa da seçilmez; seçim liste sırasıyla, süresi bilinmeyen ayrı sayılır.
    assert logic["rangeIds"] == ["aaaaaaaaaa1", "aaaaaaaaaa3"]
    assert logic["selection"] == {"ids": ["aaaaaaaaaa1", "aaaaaaaaaa4"], "count": 2, "seconds": 600, "unknown": 1}
    assert logic["selectionText"] == ["2 video seçildi · 10 dk · 1 videonun süresi bilinmiyor", "Video seçilmedi"]


def test_ice_aktarma_govdesi(logic: dict[str, Any]) -> None:
    playlist, videos, missing = logic["import"]
    assert playlist == {"body": {
        "playlistId": "PL1", "mode": "playlist", "title": "Türev", "subjectId": "matematik", "topicIds": ["t1"],
        "tags": ["türev", "limit"], "gradeLevels": [12, "graduate"], "exams": ["AYT"],
        "videoOverrides": [{"videoId": "aaaaaaaaaa1", "tags": ["zor"]}],
    }}
    # Havuzda sınav yok; ayrı eklemede başlık video başına; seçilmeyen videonun etiketi gitmez.
    assert videos == {"body": {
        "playlistId": "PL1", "mode": "videos", "videoIds": ["aaaaaaaaaa1", "aaaaaaaaaa3"],
        "videoOverrides": [{"videoId": "aaaaaaaaaa1", "title": "Türev 1. ders", "subjectId": "matematik"}],
    }}
    assert missing == {"error": "Önce listeyi önizleyin."}


def test_atama_govdesi_uc_kip(logic: dict[str, Any]) -> None:
    a = logic["assign"]
    target = {"classGroupIds": ["c1"], "studentGroupIds": [], "studentIds": []}
    assert a["all"] == {"body": {"libraryItemId": "L1", "target": target, "mode": "all", "startDate": "2026-10-19",
                                 "dueDate": None, "spread": None, "requireOrder": True, "skipDuplicates": True,
                                 "notify": True}}
    assert a["selected"]["body"]["videoIds"] == ["aaaaaaaaaa1"]
    assert (a["selected"]["body"]["rangeFrom"], a["selected"]["body"]["rangeTo"]) == (3, 4)
    assert a["selected"]["body"]["dueDate"] == "2026-10-25"
    assert a["selected"]["body"]["title"] == "Türev haftası"
    assert a["selected"]["body"]["skipDuplicates"] is False
    assert a["selectedEmpty"] == {"error": 'En az bir video seçin (onay kutusu ya da "3–7" aralığı).'}
    assert a["spread"]["body"]["spread"] == {"perDay": 2, "minutesPerDay": None, "daysOfWeek": ["mon", "fri"],
                                             "skipHolidays": True}
    assert a["spread"]["body"]["requireOrder"] is True and a["spread"]["body"]["notify"] is False
    assert a["spreadMinutes"]["body"]["spread"]["minutesPerDay"] == 45
    assert a["spreadBad"] == {"error": "Günlük video sayısı 1–20 olmalı."}
    assert a["spreadNoDay"] == {"error": "Görev verilecek en az bir gün seçin."}
    assert a["noSource"] == {"error": "Atanacak listeyi seçin."}
    assert a["noTarget"] == {"error": "En az bir sınıf, çalışma grubu ya da öğrenci seçin."}
    assert a["dueBefore"] == {"error": "Son tarih başlangıç gününden önce olamaz."}
    assert a["pool"]["body"]["videoPoolItemId"] == "P1" and "libraryItemId" not in a["pool"]["body"]
    assert a["link"]["body"]["playlistId"] == "PL1"


# --------------------------------------------------------------------------- ekle akışı

IMPORT = FAKES + r"""
export default async function ({ load, sleep }) {
  const { playlistImportDialog, playlistPanel } = await load('./panels/bbd_akademi/coaching/playlist.js');
  const out = {};

  // 1) İzleme bağlantısı → önce soru → "Listenin tamamını ekle" → "Videoları ayrı ekle" (aralık + etiket) → gövde
  const preview = { ...PLAYLIST, counts: { playable: 3, notEmbeddable: 1, private: 1, deleted: 0 },
    linkVideoId: 'vid00000002', linkVideoPosition: 2, existingLibraryItemId: null, existingVideoPoolItemId: null };
  const app = fakeApp({
    'POST /v1/admin/video-playlists/preview': preview,
    'POST /v1/admin/coach-library/import-playlist': (body) => ({ mode: body.mode, items: [{ id: 'I1' }, { id: 'I2' }],
      addedCount: 2, skipped: [] }),
  });
  const pending = playlistImportDialog(app, { target: 'library',
    url: 'https://www.youtube.com/watch?v=vid00000002&list=PLturev0000001' });
  await sleep(20);
  const dialog = () => document.body.querySelectorAll('.ak-pl-dialog').pop();
  out.ask = text(dialog());
  click(byText(dialog(), 'button', 'Listenin tamamını ekle'));
  await sleep(5);
  out.head = text(dialog().querySelector('.ak-pl-head'));
  out.submitPlaylist = text(dialog().querySelectorAll('.kit-dialog-actions button').pop());
  const radio = dialog().querySelectorAll('input').find((input) => input.type === 'radio' && input.value === 'videos');
  check(radio);
  await sleep(5);
  out.submitVideos = text(dialog().querySelectorAll('.kit-dialog-actions button').pop());
  click(byText(dialog(), 'button', 'Hiçbiri'));
  await sleep(5);
  out.afterNone = text(dialog().querySelector('.ak-pl-sum'));
  type(dialog().querySelector('.ak-pl-range'), '2-4');
  click(byText(dialog(), 'button', 'Aralığı seç'));
  await sleep(5);
  out.afterRange = text(dialog().querySelector('.ak-pl-sum'));
  out.privateDisabled = dialog().querySelectorAll('.ak-pl-row input').map((box) => box.disabled);
  type(dialog().querySelectorAll('.ak-pl-fields input').find((input) => input.maxLength === 330), 'türev, ayt');
  click(dialog().querySelectorAll('button').find((b) => b.getAttribute('aria-label') === '3. videonun etiketleri'));
  await sleep(20);
  const tagDialog = document.body.querySelectorAll('.kit-dialog').pop();
  type(tagDialog.querySelectorAll('input').find((input) => input.maxLength === 330), 'zor');
  click(byText(tagDialog, 'button', 'Uygula'));
  await sleep(20);
  click(dialog().querySelectorAll('.kit-dialog-actions button').pop());
  const outcome = await pending;
  out.outcome = outcome;
  out.posts = app.sent.filter((row) => row.method === 'POST');
  out.toasts = app.toasts;
  clean();

  // 2) "Bu videoyu ekle" → tek video bağlantısı (liste parametresi atılır)
  const app2 = fakeApp({ 'POST /v1/admin/video-playlists/preview': preview });
  const p2 = playlistImportDialog(app2, { target: 'pool',
    url: 'https://www.youtube.com/watch?v=vid00000002&list=PLturev0000001' });
  await sleep(20);
  click(byText(dialog(), 'button', 'Bu videoyu ekle'));
  out.single = await p2;
  clean();

  // 3) Liste zaten ekli (409) → eklenmiş öğe açılır
  const app3 = fakeApp({
    'POST /v1/admin/video-playlists/preview': { ...preview, linkVideoId: null },
    'POST /v1/admin/video-pool/import-playlist': { __error: { code: 'playlist_already_added', status: 409,
      message: 'Bu liste zaten ekli.', details: { itemId: 'P9' } } },
  });
  const p3 = playlistImportDialog(app3, { target: 'pool', url: 'https://www.youtube.com/playlist?list=PLturev0000001' });
  await sleep(20);
  click(dialog().querySelectorAll('.kit-dialog-actions button').pop());
  out.existing = await p3;
  out.existingToast = app3.toasts;
  clean();

  // 4) Anahtarsız kurulum → açıklama + "Bu videoyu ekle" + yöneticiye Ayarlar
  const app4 = fakeApp({ 'POST /v1/admin/video-playlists/preview': { __error: { code: 'youtube_api_key_required',
    status: 422, message: 'sunucu metni', details: { videoId: 'vid00000002' } } } });
  const p4 = playlistImportDialog(app4, { target: 'library',
    url: 'https://www.youtube.com/watch?v=vid00000002&list=PLturev0000001' });
  await sleep(20);
  out.keyless = text(dialog());
  click(byText(dialog(), 'button', 'Ayarlar → Entegrasyonlar'));
  out.went = app4.went;
  click(byText(dialog(), 'button', 'Bu videoyu ekle'));
  out.keylessSingle = await p4;
  clean();

  // 5) Liste ayrıntısı + "Listeyi yenile"
  const removed = { ...PLAYLIST, removedVideos: [{ videoId: 'vid00000009', title: 'Eski ders', removedAt: '2026-10-16T10:00:00Z' }] };
  const app5 = fakeApp({
    'GET /v1/admin/video-playlists/PLturev0000001': PLAYLIST,
    'POST /v1/admin/video-playlists/PLturev0000001/refresh': { playlist: removed, addedVideoIds: ['a', 'b', 'c'],
      removedVideoIds: ['vid00000009'], changed: true, skipped: false, message: '3 yeni video, 1 kaldırıldı' },
  });
  let assigned = null;
  const panel = playlistPanel(app5, { playlistId: 'PLturev0000001', onAssign: (pl) => { assigned = pl.playlistId; } });
  document.body.append(panel.node);
  await sleep(20);
  out.panelBefore = text(panel.node);
  click(byText(panel.node, 'button', 'Listeyi yenile'));
  await sleep(20);
  out.panelAfter = text(panel.node);
  out.refreshToast = app5.toasts;
  click(byText(panel.node, 'button', 'Öğrencilere ata'));
  out.assigned = assigned;
  panel.node.remove();
  return out;
}
"""


@pytest.fixture(scope="module")
def flow() -> dict[str, Any]:
    return _run(IMPORT)


def test_izleme_baglantisinda_once_sorulur(flow: dict[str, Any]) -> None:
    assert "Bağlantı “Türev” listesindeki bir videoyu gösteriyor (2. video)." in flow["ask"]
    assert "Bu videoyu ekle" in flow["ask"] and "Listenin tamamını ekle" in flow["ask"]
    assert "4 video · 1 sa 40 dk" in flow["head"]
    assert "1 gömülemeyen · 1 özel" in flow["head"]
    assert flow["submitPlaylist"] == "Liste olarak ekle"
    assert flow["submitVideos"] == "Videoları ayrı ekle (4)"


def test_videolari_ayri_ekle_aralik_ve_etiket(flow: dict[str, Any]) -> None:
    assert flow["afterNone"] == "Video seçilmedi"
    # 2–4 aralığında özel video (4.) atlanır: 2. ve 3.
    assert flow["afterRange"] == "2 video seçildi · 50 dk"
    assert flow["privateDisabled"] == [False, False, False, True, False]
    posts = [row for row in flow["posts"] if row["path"].endswith("import-playlist")]
    assert len(posts) == 1
    body = posts[0]["body"]
    assert body["mode"] == "videos"
    assert body["videoIds"] == ["vid00000002", "vid00000003"]
    assert body["tags"] == ["türev", "ayt"]
    assert body["videoOverrides"] == [{"videoId": "vid00000003", "tags": ["zor"]}]
    assert posts[0]["key"], "Idempotency-Key yok"
    assert flow["outcome"]["result"]["addedCount"] == 2
    assert flow["toasts"] == [["2 video eklendi", "good"]]


def test_bu_videoyu_ekle_ve_zaten_ekli(flow: dict[str, Any]) -> None:
    assert flow["single"] == {"single": {"url": "https://www.youtube.com/watch?v=vid00000002",
                                         "videoId": "vid00000002"}}
    assert flow["existing"] == {"existing": "P9"}
    assert flow["existingToast"] == [["Bu liste zaten ekli; eklenmiş liste açılıyor.", "warn"]]


def test_anahtarsiz_durum(flow: dict[str, Any]) -> None:
    assert ("Oynatma listesi için YouTube API anahtarı gerekli (yönetici KM'den girer) — şimdilik listedeki "
            "videoları tek tek ekleyebilirsin") in flow["keyless"]
    assert flow["went"] == [["settings", {"tab": "integrations"}]]
    assert flow["keylessSingle"]["single"]["videoId"] == "vid00000002"


def test_liste_ayrintisi_ve_yenile(flow: dict[str, Any]) -> None:
    assert "1. Türev 1" in flow["panelBefore"] and "Özel video" in flow["panelBefore"]
    assert "Yalnız YouTube'da açılır" in flow["panelBefore"]
    assert "3 yeni video, 1 kaldırıldı" in flow["panelAfter"]
    assert "Eski ders" in flow["panelAfter"] and "YouTube'dan kaldırıldı" in flow["panelAfter"]
    assert flow["refreshToast"] == [["3 yeni video, 1 kaldırıldı", "good"]]
    assert flow["assigned"] == "PLturev0000001"


# --------------------------------------------------------------------------- atama sihirbazı

ASSIGN = FAKES + r"""
export default async function ({ load, sleep }) {
  const { assignPlaylistWizard } = await load('./panels/bbd_akademi/coaching/playlist-assign.js');
  const out = {};
  const previews = [];
  const app = fakeApp({
    'POST /v1/admin/playlist-assignments/preview': (body) => {
      previews.push(body);
      return { studentCount: 3, videoCount: 3, taskCount: 2, totalSeconds: 3600, unknownDurationCount: 0,
        firstDate: '2026-10-19', lastDate: '2026-10-20',
        days: [{ date: '2026-10-19', videoIds: ['vid00000001', 'vid00000002'], seconds: 1800 },
          { date: '2026-10-20', videoIds: ['vid00000003'], seconds: 1800 }],
        skippedVideos: [], duplicates: [{ student: { id: 's1', displayName: 'Elif Kaya' }, videoIds: ['vid00000001'] }],
        duplicateStudentCount: 1, duplicateVideoCount: 1, warnings: ['has_not_embeddable'],
        students: [{ id: 's1', displayName: 'Elif Kaya' }] };
    },
    'GET /v1/admin/students': { items: [{ id: 's1', displayName: 'Elif Kaya', externalRef: '2UADN2T', classGroup: null }],
      nextCursor: null },
    'POST /v1/admin/playlist-assignments': (body) => ({ id: 'A1', title: 'Türev', mode: body.mode, videoCount: 3,
      studentCount: 3, taskCount: 2, skippedDuplicateCount: 1 }),
  });
  const pending = assignPlaylistWizard(app, { source: { libraryItemId: 'L1' }, playlist: PLAYLIST,
    target: { studentIds: ['s1'], students: [{ id: 's1', displayName: 'Elif Kaya' }] } });
  await sleep(30);
  const wiz = () => document.body.querySelectorAll('.ak-wiz').pop();
  out.step1 = text(wiz());
  // Güne yay: her gün 2 video, yalnız 3 video seçili (aralık 1–3)
  check(wiz().querySelectorAll('input').find((input) => input.type === 'radio' && input.value === 'spread'));
  await sleep(5);
  click(byText(wiz(), 'button', 'Hiçbiri'));
  click(byText(wiz(), 'button', 'İleri'));
  await sleep(10);
  out.emptyError = text(wiz().querySelector('.ak-wiz-error'));
  type(wiz().querySelector('.ak-pl-range'), '1–3');
  click(byText(wiz(), 'button', 'Aralığı seç'));
  check(wiz().querySelectorAll('label').find((label) => text(label).startsWith('Sırayla izle')).querySelector('input'));
  out.note1 = text(wiz().querySelector('.ak-wiz-note'));
  click(byText(wiz(), 'button', 'İleri'));
  await sleep(10);
  out.step2 = text(wiz().querySelectorAll('.ak-wiz-pane').filter((pane) => !pane.hidden).pop());
  click(byText(wiz(), 'button', 'İleri'));
  await sleep(40);
  out.step3 = text(wiz().querySelectorAll('.ak-wiz-pane').filter((pane) => !pane.hidden).pop());
  // "Zaten atanmışları atla" kapat → önizleme yenilenir
  const skip = wiz().querySelectorAll('label').find((label) => text(label) === 'Zaten atanmışları atla').querySelector('input');
  check(skip, false);
  await sleep(500);
  click(byText(wiz(), 'button', 'Ata'));
  out.created = await pending;
  out.previews = previews;
  out.create = app.sent.filter((row) => row.method === 'POST' && row.path === '/v1/admin/playlist-assignments');
  out.toasts = app.toasts;
  clean();
  return out;
}
"""


@pytest.fixture(scope="module")
def assign() -> dict[str, Any]:
    return _run(ASSIGN)


def test_sihirbaz_adimlari_ve_metinler(assign: dict[str, Any]) -> None:
    for label in ("Tüm liste", "Seçili videolar", "Güne yay", "Sırayla izle", "Tümünü seç", "Hiçbiri"):
        assert label in assign["step1"], label
    assert assign["emptyError"] == 'En az bir video seçin (onay kutusu ya da "3–7" aralığı).'
    assert assign["note1"] == "Güne yay · 3 video seçildi · 1 sa"
    assert "Elif Kaya" in assign["step2"]
    assert "3 öğrenci · 3 video · 1 sa · 2 gün (19 Eki – 20 Eki)" in assign["step3"]
    assert "1 öğrencide 1 video zaten atanmış" in assign["step3"]
    assert "Bazı videolar yalnız YouTube'da açılır (gömme kapalı)." in assign["step3"]
    assert "Zaten atanmışları atla" in assign["step3"]


def test_sihirbaz_tek_istek_govdesi(assign: dict[str, Any]) -> None:
    assert assign["previews"][-1]["skipDuplicates"] is False
    assert len(assign["create"]) == 1
    sent = assign["create"][0]
    assert sent["key"], "Idempotency-Key yok"
    body = sent["body"]
    assert body["libraryItemId"] == "L1"
    assert body["mode"] == "spread"
    assert body["videoIds"] == ["vid00000001", "vid00000002", "vid00000003"]
    assert body["spread"] == {"perDay": 2, "minutesPerDay": None, "daysOfWeek": ["mon", "tue", "wed", "thu", "fri"],
                              "skipHolidays": True}
    assert body["requireOrder"] is True
    assert body["skipDuplicates"] is False
    assert body["target"] == {"classGroupIds": [], "studentGroupIds": [], "studentIds": ["s1"]}
    assert assign["toasts"] == [["Liste atandı: 3 öğrenci · 3 video · 2 gün · 1 yinelenen atlandı.", "good"]]


# --------------------------------------------------------------------------- ilerleme, ayarlar, havuz

REPORT = FAKES + r"""
export default async function ({ load, sleep, audit }) {
  const out = {};
  const progress = await load('./panels/bbd_akademi/coaching/playlist-progress.js');
  const data = {
    assignment: { id: 'A1', title: 'Türev', mode: 'all', requireOrder: true, videoCount: 3, studentCount: 2, taskCount: 2,
      skippedDuplicateCount: 0, firstDate: '2026-10-19', lastDate: '2026-10-19', dueDate: null, createdByName: 'Hakan',
      createdAt: '2026-10-17T09:00:00Z', playlist: { title: 'Türev', channelTitle: 'Hoca' } },
    completedStudentCount: 1, avgPercent: 66.7,
    students: [{ student: { id: 's2', displayName: 'Can Er' }, videoCount: 3, completedCount: 1, percent: 33.3,
      watchedSeconds: 600, remainingSeconds: 3000, nextPosition: 2, lastActivityAt: null }],
    videos: [{ position: 1, videoId: 'v1', title: 'Türev 1', durationSeconds: 600, removedFromPlaylist: true,
      assignedCount: 2, completedCount: 2, avgMeasuredPercent: 95 }],
  };
  const root = document.createElement('div');
  document.body.append(root);
  root.append(...progress.progressNodes(fakeApp({}), data));
  out.report = text(root);
  out.a11y = audit(root);
  root.remove();
  const student = document.createElement('div');
  student.append(...progress.studentNodes({ id: 'A1', title: 'Türev', playlistId: 'PL1', channelTitle: 'Hoca', mode: 'all',
    requireOrder: true, dueDate: null, videoCount: 3, completedCount: 1, totalSeconds: 3600, remainingSeconds: 3000,
    nextItem: { taskId: 't1', taskVideoId: 'tv2' },
    items: [
      { position: 1, taskId: 't1', date: '2026-10-19', locked: false, taskVideo: { id: 'tv1', removedFromPlaylist: false,
        video: { title: 'Türev 1', durationSeconds: 600 }, progress: { completed: true, completionSource: 'in_app', measuredPercent: 96 } } },
      { position: 2, taskId: 't1', date: '2026-10-19', locked: false, taskVideo: { id: 'tv2', removedFromPlaylist: true,
        video: { title: 'Türev 2', durationSeconds: 1200 }, progress: null } },
      { position: 3, taskId: 't1', date: '2026-10-19', locked: true, taskVideo: { id: 'tv3', removedFromPlaylist: false,
        video: { title: 'Türev 3', durationSeconds: 1800 }, progress: null } },
    ] }));
  out.student = text(student);

  // Ayarlar → Entegrasyonlar → YouTube
  const settings = await load('./panels/bbd_akademi/admin/settings-integrations.js');
  let current = { configured: true, source: 'platform', keyHint: null, updatedAt: null, playlistsEnabled: true };
  const app = fakeApp({
    'GET /v1/admin/integrations/youtube': () => current,
    'PUT /v1/admin/integrations/youtube': (body) => {
      if (body.apiKey === 'GECERSIZ-ANAHTAR-0000000000') return { __error: { code: 'youtube_api_key_invalid', status: 422, message: 'x' } };
      current = { configured: true, source: body.apiKey ? 'tenant' : 'platform', keyHint: body.apiKey ? '…x9Qa' : null,
        updatedAt: '2026-10-17T10:00:00Z', playlistsEnabled: true };
      return current;
    },
  });
  const tab = settings.integrationsTab(app);
  document.body.append(tab.node);
  tab.reload();
  await sleep(20);
  out.platform = text(tab.node);
  const key = tab.node.querySelector('input');
  out.keyType = key.type;
  type(key, 'kisa');
  click(byText(tab.node, 'button', 'Doğrula ve kaydet'));
  await sleep(10);
  out.short = text(tab.node.querySelector('.ak-pl-error'));
  type(tab.node.querySelector('input'), 'GECERSIZ-ANAHTAR-0000000000');
  click(byText(tab.node, 'button', 'Doğrula ve kaydet'));
  await sleep(20);
  out.invalid = text(tab.node.querySelector('.ak-pl-error'));
  out.keyCleared = tab.node.querySelector('input').value;
  type(tab.node.querySelector('input'), 'AIzaSyD-gecerli-anahtar-x9Qa');
  click(byText(tab.node, 'button', 'Doğrula ve kaydet'));
  await sleep(30);
  out.tenant = text(tab.node);
  out.puts = app.sent.filter((row) => row.method === 'PUT').map((row) => row.body);
  out.settingsA11y = audit(tab.node);
  tab.node.remove();
  const denied = settings.integrationsTab(fakeApp({ 'GET /v1/admin/integrations/youtube': current },
    { deny: ['admin_putYoutubeIntegration'] }));
  denied.reload();
  await sleep(20);
  out.denied = text(denied.node);
  out.deniedInputs = denied.node.querySelectorAll('input').length;

  // Video havuzu: kind=all, liste satırı, watch?v=…&list= sorusu
  const pool = await load('./panels/bbd_akademi/coaching/coaching-video-pool.js');
  const poolApp = fakeApp({
    'GET /v1/admin/video-pool': { items: [{ id: 'P1', kind: 'playlist', title: 'Türev listesi', video: PLAYLIST.videos[0].video,
      playlist: { ...PLAYLIST, videos: undefined }, topics: [], tags: [], gradeLevels: [], addedByName: 'Hakan', useCount: 0,
      createdAt: '2026-10-17T09:00:00Z' }], nextCursor: null },
  });
  const view = pool.tab(poolApp);
  document.body.append(view.node);
  view.shown();
  await sleep(30);
  out.poolQuery = poolApp.sent.find((row) => row.path === '/v1/admin/video-pool')?.body;
  out.poolRow = text(view.node.querySelector('.kit-table'));
  out.poolButtons = view.node.querySelectorAll('button').map(text).filter((label) => ['Video ekle', 'Liste ekle', 'Toplu ekle'].includes(label));
  const dialogPromise = pool.itemDialog(poolApp);
  await sleep(20);
  const dialog = document.body.querySelectorAll('.kit-dialog').pop();
  const url = dialog.querySelectorAll('input').find((input) => input.type !== 'radio' && input.maxLength === 2048);
  type(url, 'https://www.youtube.com/watch?v=vid00000002&list=PLturev0000001');
  await sleep(10);
  out.choice = text(dialog);
  check(dialog.querySelectorAll('input').find((input) => input.type === 'radio' && input.value === 'list'));
  click(byText(dialog, 'button', 'Ekle'));
  out.choiceResult = await dialogPromise;
  view.destroy();
  view.node.remove();
  clean();
  return out;
}
"""


@pytest.fixture(scope="module")
def report() -> dict[str, Any]:
    return _run(REPORT)


def test_liste_ilerleme_raporu(report: dict[str, Any]) -> None:
    text = report["report"]
    assert "Bitiren" in text and "%67" in text
    assert "1/3 izlendi · 50 dk kaldı" in text
    assert "2. video" in text
    assert "YouTube'dan kaldırıldı" in text and "2 / 2" in text and "%95" in text
    assert "Tüm liste · sırayla izle" in text
    assert report["a11y"] == []


def test_ogrencinin_liste_gorevi(report: dict[str, Any]) -> None:
    text = report["student"]
    assert "Türev — 3 video · 1/3 izlendi · 50 dk kaldı" in text
    assert "Sıradaki video: 2. Türev 2" in text
    assert "Tamamlandı · ölçüldü %96" in text
    assert "Kilitli" in text and "YouTube'dan kaldırıldı" in text


def test_ayarlar_entegrasyonlar_youtube(report: dict[str, Any]) -> None:
    assert "Platform anahtarı kullanılıyor" in report["platform"]
    assert "Oynatma listeleri açık" in report["platform"]
    assert report["keyType"] == "password"
    assert report["short"] == "Anahtar en az 20 karakter olmalı."
    assert report["invalid"].startswith("Google bu anahtarı kabul etmedi.")
    assert report["keyCleared"] == ""
    assert "Kurum anahtarı" in report["tenant"] and "…x9Qa" in report["tenant"]
    assert "Kurum anahtarını sil" in report["tenant"]
    # Kısa anahtar gönderilmez; geçersiz ve geçerli iki istek.
    assert report["puts"] == [{"apiKey": "GECERSIZ-ANAHTAR-0000000000"}, {"apiKey": "AIzaSyD-gecerli-anahtar-x9Qa"}]
    assert "AIzaSyD" not in report["tenant"], "sır ekranda"
    assert report["settingsA11y"] == []
    assert "Anahtarı değiştirme yetkiniz yok" in report["denied"]
    assert report["deniedInputs"] == 0


def test_video_havuzu_listeler_ve_soru(report: dict[str, Any]) -> None:
    assert report["poolQuery"]["kind"] == "all"
    assert "Liste · 4 video · 1 sa 40 dk" in report["poolRow"]
    assert report["poolButtons"] == ["Video ekle", "Liste ekle", "Toplu ekle"]
    assert "Bu videoyu ekle" in report["choice"] and "Listenin tamamını ekle" in report["choice"]
    assert report["choiceResult"] == {"playlistUrl": "https://www.youtube.com/watch?v=vid00000002&list=PLturev0000001"}


LIBRARY = FAKES + r"""
export default async function ({ load, sleep }) {
  const out = {};
  const lib = await load('./panels/bbd_akademi/coaching/coaching-library.js');
  const ITEMROW = { id: 'L1', kind: 'youtube_playlist', scope: 'institution', title: 'Türev listesi', playlistId: 'PLturev0000001',
    playlist: { ...PLAYLIST, videos: undefined, removedVideos: undefined }, video: PLAYLIST.videos[0].video, topics: [], tags: [],
    exams: [], gradeLevels: [], status: 'active', proposal: { status: 'none' },
    usage: { assignedCount: 2, avgWatchPercent: null, checkpointAccuracyPercent: null }, createdAt: '2026-10-17T09:00:00Z' };
  const app = fakeApp({
    'GET /v1/admin/coach-library': { items: [ITEMROW], nextCursor: null },
    'GET /v1/admin/coach-library/L1': ITEMROW,
    'GET /v1/admin/video-playlists/PLturev0000001': PLAYLIST,
    'GET /v1/admin/playlist-assignments': { items: [{ id: 'A1', title: 'Türev', mode: 'spread', videoCount: 4, studentCount: 24,
      taskCount: 2, firstDate: '2026-10-19', lastDate: '2026-10-20', requireOrder: true, createdByName: 'Hakan',
      createdAt: '2026-10-17T09:00:00Z', playlist: { title: 'Türev', channelTitle: 'Hoca' } }], nextCursor: null },
  });
  const view = lib.tab(app);
  document.body.append(view.node);
  view.shown();
  await sleep(30);
  out.toolbar = view.node.querySelectorAll('button').map(text);
  out.row = text(view.node.querySelector('.kit-table'));
  // Satır → çekmece: liste paneli
  const row = view.node.querySelectorAll('.kit-table [role="row"]').find((node) => text(node).includes('Türev listesi'));
  click(row);
  await sleep(60);
  const drawer = document.body.querySelectorAll('.kit-drawer').pop();
  out.drawer = text(drawer);
  document.body.querySelectorAll('.kit-overlay').forEach((node) => node.remove());
  // Liste atamaları çipi
  click(byText(view.node, 'button', 'Liste atamaları'));
  await sleep(40);
  out.assignments = text(view.node);
  out.addHidden = byText(view.node, 'button', 'Oynatma listesi ekle')?.hidden;
  view.destroy();
  view.node.remove();
  return out;
}
"""


@pytest.fixture(scope="module")
def library() -> dict[str, Any]:
    return _run(LIBRARY)


def test_kutuphane_liste_ogesi_ve_atamalar(library: dict[str, Any]) -> None:
    assert "Oynatma listesi ekle" in library["toolbar"]
    assert "Liste atamaları" in library["toolbar"]
    assert "Hoca · 4 video · 1 sa 40 dk" in library["row"]
    assert "Listeyi yenile" in library["drawer"] and "Öğrencilere ata" in library["drawer"]
    assert "1. Türev 1" in library["drawer"] and "Özel video" in library["drawer"]
    assert "Güne yay · 4 video · 24 öğrenci · 19 Eki – 20 Eki · Sırayla" in library["assignments"]
    assert library["addHidden"] is True


# --------------------------------------------------------------------------- vekil

def test_vekil_izin_listesi_ve_km_izni() -> None:
    ops = {op.operation_id: op for op in OPERATIONS}
    expected = {
        "admin_previewVideoPlaylist": LEARNING, "admin_getVideoPlaylist": LEARNING,
        "admin_refreshVideoPlaylist": LEARNING, "admin_importPlaylistToVideoPool": LEARNING,
        "admin_previewPlaylistAssignment": LEARNING, "admin_createPlaylistAssignment": LEARNING,
        "admin_listPlaylistAssignments": LEARNING, "admin_getPlaylistAssignment": LEARNING,
        "admin_importPlaylistToInstitutionLibrary": LEARNING_SETUP,
        "admin_getYoutubeIntegration": SETTINGS, "admin_putYoutubeIntegration": SETTINGS,
        "students_getPlaylistAssignment": VIEW,
    }
    for op_id, permission in expected.items():
        assert op_id in ops, op_id
        assert km_permissions(ops[op_id]) == (permission,), op_id
    # İlerleme özeti: plan yöneten de koçluk panosunu gören de okur.
    assert km_permissions(ops["admin_getPlaylistAssignmentProgress"]) == (LEARNING, VIEW)
    for op_id in ("admin_importPlaylistToInstitutionLibrary", "admin_getYoutubeIntegration",
                  "admin_putYoutubeIntegration"):
        assert ops[op_id].surface == "km", op_id
    assert ops["admin_createPlaylistAssignment"].feature_flag == "video_tasks"
    assert ops["admin_importPlaylistToInstitutionLibrary"].feature_flag == "coaching"
    # Anahtar yazımı kurum ayarıdır; Akademi'ye anahtar KM'de saklanmaz (yalnız iletilir).
    assert not ops["admin_putYoutubeIntegration"].multipart
