"""Hızlı güncelleme (0.5.3): öğrenci kartında satır içi düzenleme, geri al,
hızlı işlem şeridi, kısayollar, "Akademi'ye ulaştı ✓" ve erişilebilirlik.

Kullanıcı: "Adam bir öğrenciyi güncellemek için saatlerce uğraşıp durmasın.
Mesele kullanım kolaylığı; erişilebilirliği full olsun." Hedef: tek alan
değişikliği ≤ 3 etkileşim (tıkla · yaz · Enter), < 1 sn.

Kartın kendisi (`ui/panel/record.js`) Node'da, küçük bir DOM üzerinde
(`tools/a11y/dom.mjs`) taklit çekirdekle çalıştırılır; kural dosyası
(`quick.js`) ayrıca sınanır. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_students"
STUDENT = {
    "kantinId": "STU-1", "displayName": "Elif Kaya", "firstName": "Elif", "lastName": "Kaya",
    "className": "9-A", "schoolNo": "77", "studentPhone": "", "note": "", "gradeLevel": 9,
    "track": None, "examTarget": "LGS", "status": "active", "balance": 0, "canteenSync": "ok",
    "guardians": [], "curriculumSuggestion": None,
    # Sınıf seviyeyi taşır (göç 008): seviye sınıftan, öğrenciye özel istisna kapalı.
    "classLevel": {"gradeLevel": 9, "track": None, "examTarget": "LGS"}, "levelSource": "class",
    "levelOverride": False, "ownTrack": None, "ownExamTarget": None,
}
GUARDIANS = {"items": [{"slot": 1, "name": "Ayşe Kaya", "phone": "5321112233", "relation": "mother",
                        "primary": True, "appAccess": True, "siblings": []}],
             "max": 4, "appSlots": [1, 2], "relations": []}
ROUTES: dict[str, Any] = {
    f"GET {BASE}/students/STU-1": STUDENT,
    f"GET {BASE}/students/STU-1/guardians": GUARDIANS,
    f"GET {BASE}/students/STU-1/groups": {"items": []},
    f"GET {BASE}/groups?archived=live": {"groups": [], "items": []},
    f"GET {BASE}/students/STU-1/canteen": {"available": True, "controls": {
        "balance": 0, "spendingLimit": None, "dailyLimit": 15000, "isBlocked": False,
        "guardianBlocked": False, "restrictedProductIds": [], "spentToday": 0, "controlsUpdatedAt": None},
        "products": []},
    f"GET {BASE}/students/STU-1/status-history": {"items": []},
    f"PATCH {BASE}/students/STU-1": {"kantinId": "STU-1", "canteenSync": "ok"},
    f"PUT {BASE}/students/STU-1/guardians": {"changed": True, "canteenSync": "ok"},
    f"PATCH {BASE}/students/STU-1/canteen/controls": {"controls": {
        "balance": 0, "spendingLimit": None, "dailyLimit": 20000, "isBlocked": False,
        "guardianBlocked": False, "restrictedProductIds": [], "spentToday": 0, "controlsUpdatedAt": None}},
    "GET /api/events/wait": {"items": [], "lastSeq": 41, "reset": False},
}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { mountRecord } = await load('./panels/bbd_students/record.js');
  const host = document.createElement('div');
  document.body.append(host);
  const probes = [];
  const record = mountRecord(host, { api, kantinId: 'STU-1', can: () => true, toast: () => {},
    capability: () => null,
    syncProbe: async () => { probes.push(1); return { enabled: true, deliveredSeq: probes.length >= 2 ? 41 : 40 }; } });
  await sleep(30);
  const out = {};
  const input = (label) => document.querySelectorAll('label').find((l) => l.textContent === label)
    && document.getElementById(document.querySelectorAll('label').find((l) => l.textContent === label).htmlFor);
  const key = (el, k, extra = {}) => el.dispatchEvent(new KeyboardEvent('keydown', { key: k, ...extra }));
  const type = (el, text) => { el.value = text; el.dispatchEvent(new Event('input', { bubbles: true })); };

  out.violations = audit(host);

  // 1) Sınıf: tıkla · yaz · Enter → yalnız className PATCH.
  const klass = input('Ana sınıf');
  klass.focus();
  type(klass, '10b');
  const before = calls.length;
  key(klass, 'Enter');
  await sleep(20);
  out.classPatch = calls.slice(before).filter((c) => c.method === 'PATCH');
  out.saved = document.querySelector('.st-saved-text')?.textContent;
  await sleep(700);
  out.sync = document.querySelector('.st-sync')?.textContent;
  out.probes = probes.length;

  // 2) Geri al → eski değer.
  const undo = document.querySelector('.st-undo');
  const mark = calls.length;
  undo.click();
  await sleep(20);
  out.undoPatch = calls.slice(mark).filter((c) => c.method === 'PATCH').map((c) => c.body);
  out.afterUndo = klass.value;

  // 3) Esc: kaydetmeden vazgeçer.
  const school = input('Okul No');
  school.focus();
  type(school, '999');
  const mark2 = calls.length;
  key(school, 'Escape');
  school.blur();
  await sleep(20);
  out.escValue = school.value;
  out.escCalls = calls.slice(mark2).filter((c) => c.method !== 'GET').length;

  // 4) Hatalı değer: kaydedilmez, hata alana bağlanır.
  const first = input('Ad');
  first.focus();
  type(first, '   ');
  key(first, 'Enter');
  await sleep(10);
  out.invalid = first.getAttribute('aria-invalid');
  const errId = (first.getAttribute('aria-describedby') || '').split(' ').find((id) => id.endsWith('-err'));
  out.errorText = document.getElementById(errId)?.textContent;
  out.violationsInvalid = audit(host).filter((v) => v.rule === 'invalid-describedby');
  key(first, 'Escape');

  // 5) Kısayol `e`: ilk alanı düzenler.
  document.body.focus?.();
  first.blur();
  const k = new KeyboardEvent('keydown', { key: 'e', bubbles: true });
  host.querySelector('.st-rec').dispatchEvent(k);
  out.eFocus = document.activeElement === first;

  // 6) Hızlı veli telefonu: yaz · Enter → PUT guardians (birincil yuva).
  const phone = input('Veli telefonu');
  phone.focus();
  type(phone, '0505 444 55 66');
  const mark3 = calls.length;
  key(phone, 'Enter');
  await sleep(30);
  out.phonePut = calls.slice(mark3).filter((c) => c.method === 'PUT').map((c) => c.body);

  // 7) Hızlı günlük limit.
  const limit = input('Günlük kantin limiti (₺)');
  limit.focus();
  type(limit, '200');
  const mark4 = calls.length;
  key(limit, 'Enter');
  await sleep(30);
  out.limitPatch = calls.slice(mark4).filter((c) => c.method === 'PATCH').map((c) => c.body);

  // 8) Seviye sınıftan gelir (salt okunur); öğrenciye özel istisna tek tıkla açılır.
  out.levelLine = host.querySelector('.st-level-line')?.textContent || '';
  const override = input('Bu öğrenci için farklı alan / hedef sınav');
  const mark5 = calls.length;
  override.checked = true;
  override.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(20);
  out.examPatch = calls.slice(mark5).filter((c) => c.method === 'PATCH').map((c) => c.body);

  out.quickLabels = document.querySelectorAll('.st-quick-label').map((n) => n.textContent);
  out.violationsEnd = audit(host);
  record.destroy();
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    try:
        data = run(SCENARIO, panels=("bbd_students",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_tek_alan_tikla_yaz_enter_yalniz_o_alani_yazar(result: dict[str, Any]) -> None:
    assert result["classPatch"] == [{"method": "PATCH", "path": f"{BASE}/students/STU-1",
                                     "body": {"className": "10-B"}}]
    assert result["saved"] == "“Ana sınıf” kaydedildi."


def test_akademiye_ulasti_gostergesi(result: dict[str, Any]) -> None:
    assert result["sync"].startswith("Akademi'ye ulaştı ✓")
    assert result["probes"] >= 2


def test_geri_al_eski_degeri_yazar(result: dict[str, Any]) -> None:
    assert result["undoPatch"] == [{"className": "9-A"}]
    assert result["afterUndo"] == "9-A"


def test_esc_vazgecer_istek_gitmez(result: dict[str, Any]) -> None:
    assert result["escValue"] == "77"
    assert result["escCalls"] == 0


def test_hata_alana_bagli_ve_kaydedilmez(result: dict[str, Any]) -> None:
    assert result["invalid"] == "true"
    assert result["errorText"] == "Ad boş bırakılamaz."
    assert result["violationsInvalid"] == []


def test_kisayol_e_ilk_alani_duzenler(result: dict[str, Any]) -> None:
    assert result["eFocus"] is True


def test_hizli_islemler_veli_telefonu_ve_limit(result: dict[str, Any]) -> None:
    assert result["quickLabels"] == ["Sınıf", "Veli telefonu", "Günlük kantin limiti (₺)"]
    assert result["phonePut"] == [{"items": [{"name": "Ayşe Kaya", "phone": "5054445566", "relation": "mother"}]}]
    assert result["limitPatch"] == [{"dailyLimit": 20000}]
    assert result["examPatch"] == [{"levelOverride": True}]
    assert result["levelLine"] == "9. sınıf · LGS — 9-A sınıfından"


def test_kart_erisilebilirlik_denetimi_temiz(result: dict[str, Any]) -> None:
    assert result["violations"] == [], result["violations"]
    assert result["violationsEnd"] == [], result["violationsEnd"]


QUICK = r"""
export default async function ({ load }) {
  const q = await load('./panels/bbd_students/quick.js');
  return {
    names: [q.inlineValue('firstName', '  Elif  '), q.inlineValue('firstName', ' '), q.inlineValue('lastName', 'x'.repeat(61))],
    phone: [q.inlineValue('studentPhone', '0532 111 22 33'), q.inlineValue('studentPhone', '0212 111 22 33'),
      q.inlineValue('studentPhone', '')],
    klass: [q.inlineValue('className', '11a'), q.inlineValue('className', ' 9 / b ')],
    grade: [q.inlineValue('gradeLevel', '9'), q.inlineValue('gradeLevel', 'graduate'), q.inlineValue('gradeLevel', '4'),
      q.inlineValue('gradeLevel', '')],
    patchName: q.patchFor({ firstName: 'Elif', lastName: 'Kaya' }, 'firstName', 'Ela'),
    undo: [q.undoPatch({ className: '9-A' }, 'className'), q.undoPatch({}, 'track'), q.undoPatch({ firstName: 'E', lastName: 'K' }, 'lastName')],
    same: [q.sameValue(null, ''), q.sameValue(9, '9'), q.sameValue('a', 'b')],
    delivery: [
      q.deliveryState({ seq: 5, status: { enabled: true, deliveredSeq: 5 }, elapsedMs: 820 }),
      q.deliveryState({ seq: 5, status: { enabled: true, deliveredSeq: 4 }, elapsedMs: 300 }),
      q.deliveryState({ seq: 5, status: { enabled: true, deliveredSeq: 4 }, elapsedMs: 11000 }),
      q.deliveryState({ seq: 5, status: { enabled: false } }),
      q.deliveryState({ seq: 5, status: null }),
    ],
    keys: [q.shortcut({ key: 'e' }, false), q.shortcut({ key: 'e' }, true), q.shortcut({ key: 'Escape' }, true),
      q.shortcut({ key: 'e', ctrlKey: true }, false)],
  };
}
"""


def test_kural_dosyasi() -> None:
    try:
        r = run(QUICK, panels=("bbd_students",))["result"]
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert r["names"] == [{"value": "Elif"}, {"error": "Ad boş bırakılamaz."},
                          {"error": "Soyad en çok 60 karakter."}]
    assert r["phone"][0] == {"value": "5321112233"} and "error" in r["phone"][1] and r["phone"][2] == {"value": ""}
    assert r["klass"] == [{"value": "11-A"}, {"value": "9-B"}]
    assert r["grade"] == [{"value": 9}, {"value": "graduate"}, {"error": "Seviye 5–12 ya da mezun."}, {"value": None}]
    assert r["patchName"] == {"firstName": "Ela", "lastName": "Kaya"}
    assert r["undo"] == [{"className": "9-A"}, {"track": None}, {"lastName": "K", "firstName": "E"}]
    assert r["same"] == [True, True, False]
    states = [item["state"] for item in r["delivery"]]
    assert states == ["delivered", "pending", "late", "off", "unknown"]
    assert r["delivery"][0]["text"] == "Akademi'ye ulaştı ✓ (0,8 sn)"
    assert r["keys"] == ["edit", None, "escape", None]


LIST_ROUTES: dict[str, Any] = {
    "GET /auth/me": {"permissions": ["bbd_students.view", "bbd_students.manage", "bbd_students.groups",
                                     "bbd_students.lifecycle"]},
    f"GET {BASE}/students": {"connected": True, "summary": {}, "students": [
        {"kantinId": "STU-1", "displayName": "Elif Kaya", "className": "9-A", "status": "active", "balance": 0,
         "guardians": []},
        {"kantinId": "STU-2", "displayName": "Can Demir", "className": "9-A", "status": "active", "balance": 0,
         "guardians": []},
        {"kantinId": "STU-3", "displayName": "Ali Ak", "className": "10-B", "status": "active", "balance": 0,
         "guardians": []}]},
    f"GET {BASE}/groups?archived=live": {"groups": [{"id": "G1", "name": "Etüt"}]},
    f"POST {BASE}/students/class": {"updated": ["STU-1", "STU-2"], "failed": []},
    f"POST {BASE}/students/curriculum": {"updated": ["STU-1", "STU-2", "STU-3"], "failed": []},
    f"POST {BASE}/groups/G1/members": {"skipped": []},
    "GET /api/events/wait": {"items": [], "lastSeq": 1},
}

LIST = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path) => (path.startsWith('/api/events/wait?after') ? new Promise(() => {}) : undefined);
  const sk = await load('./panels/bbd_students/index.js');
  const opened = [];
  const caps = sk.capabilities({ api, open: () => {}, capability: () => null });
  const root = document.createElement('div');
  document.body.append(root);
  const cleanup = caps['bbd_students.manager'](root, { openStudent: (s) => opened.push(s.kantinId), hideModes: true });
  await sleep(40);
  const out = {};
  const bar = root.querySelector('.st-listbulk');
  out.hiddenAtStart = bar.hidden;
  const checks = root.querySelectorAll('.st-row-check');
  checks[0].click();
  checks[1].click();
  out.count = root.querySelector('.st-listbulk-count').textContent;
  out.visible = !bar.hidden;
  out.violations = audit(root);
  // Ana sınıf: yaz · Enter
  const value = bar.querySelector('.st-listbulk-value input');
  value.value = '11a';
  const mark = calls.length;
  value.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter' }));
  await sleep(40);
  out.classPost = calls.slice(mark).filter((c) => c.method === 'POST').map((c) => [c.path, c.body]);
  out.status = bar.querySelector('.st-listbulk-status').textContent;
  out.clearedAfter = bar.hidden;
  // Tümünü seç → hedef sınav
  const all = root.querySelector('.st-select-all input');
  all.click();
  out.allCount = root.querySelector('.st-listbulk-count').textContent;
  const field = bar.querySelector('select');
  field.value = 'examTarget';
  field.dispatchEvent(new Event('change'));
  await sleep(10);
  const select = bar.querySelector('.st-listbulk-value select');
  select.value = 'YKS';
  const mark2 = calls.length;
  bar.querySelectorAll('button').find((b) => b.textContent === 'Uygula').click();
  await sleep(40);
  out.examPost = calls.slice(mark2).filter((c) => c.method === 'POST').map((c) => [c.path, c.body]);
  // Satıra tıklamak kartı açar (seçim kutusu ayrı).
  root.querySelectorAll('.st-item')[2].click();
  out.opened = opened;
  cleanup();
  return out;
}
"""


def test_listede_coklu_secim_ve_alan_ile_toplu_duzenleme() -> None:
    try:
        data = run(LIST, panels=("bbd_students",), routes=LIST_ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    r = data["result"]
    assert r["hiddenAtStart"] is True and r["visible"] is True
    assert r["count"] == "2 öğrenci seçili"
    assert r["violations"] == []
    assert r["classPost"] == [[f"{BASE}/students/class", {"students": ["STU-1", "STU-2"], "className": "11-A"}]]
    assert r["status"] == "2 öğrenci güncellendi."
    assert r["clearedAfter"] is True
    assert r["allCount"] == "3 öğrenci seçili"
    assert r["examPost"][0][0] == f"{BASE}/students/curriculum"
    assert sorted(r["examPost"][0][1]["students"]) == ["STU-1", "STU-2", "STU-3"]
    assert r["examPost"][0][1]["examTarget"] == "YKS"
    assert len(r["opened"]) == 1
