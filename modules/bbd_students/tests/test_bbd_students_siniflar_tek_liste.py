"""TEK "Sınıflar" ekranı (sınıf = grup, iş 23) — Node'da küçük DOM üzerinde.

Kullanıcı: "sınıf=grup bak öğrenci yönetiminde burası çok karışıyor." Eski "Gruplar"
ve "Sınıflar" sekmeleri tek listedir; arayüzde "grup" kelimesi geçmez. Sınıfın
seviyesi listede ve ayrıntıda görünür; öğrenci seçici sınıf (çoklu üyelik) /
seviye / alan / ara ile süzer; eklerken sınıfın seviyesi öğrencilere yazılır
(`applyLevel`). Yeni sınıf ad + "Özel ders" kutusu + isteğe bağlı seviyeyle açılır.
Erişilebilirlik denetimi temiz olmalı. `node` yoksa atlanır.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_students/groups"
_TODAY = date.today()
#: Ekranın varsayılan dönem süzgeci: bugünün dönemi (1 Eylül'de döner).
YEAR = (f"{_TODAY.year}-{_TODAY.year + 1}" if _TODAY.month >= 9
        else f"{_TODAY.year - 1}-{_TODAY.year}")
LEVEL = {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"}


def student(sid: str, name: str, classes: list[str], **extra: Any) -> dict[str, Any]:
    return {"kantinId": sid, "displayName": name, "className": ", ".join(classes),
            "classNames": classes, "status": "active", "schoolNo": "", **extra}


STUDENTS = [
    student("STU-1", "Elif Kaya", ["11-A", "TYT Matematik"], gradeLevel=11, track="SAY"),
    student("STU-2", "Can Demir", ["TYT Matematik"], gradeLevel=12, track="SAY"),
    student("STU-3", "Ece Çelik", [], gradeLevel=9, track=None),
    student("STU-4", "Ömer Ak", ["11-A"], status="inactive"),
]

DETAIL = {"id": "C1", "name": "11-A", "callName": "", "kind": "group", "note": "", "archived": False,
          "archivedAt": None, "classLevel": LEVEL, "legacyClassName": "11-A", "origin": "class",
          "current": [{"id": "m1", "studentId": "STU-1", "validFrom": "2026-09-01", "validTo": None}],
          "members": [{"id": "m1", "studentId": "STU-1", "validFrom": "2026-09-01", "validTo": None,
                       "reason": ""}]}

ROUTES: dict[str, Any] = {
    f"GET {BASE}?archived=live&year={YEAR}": {"years": [YEAR, "2019-2020"], "groups": [
        {"id": "C1", "name": "11-A", "callName": "", "kind": "group", "memberCount": 1,
         "archived": False, "classLevel": LEVEL},
        {"id": "C2", "name": "TYT Matematik", "callName": "Tey te ye matematik", "kind": "group",
         "memberCount": 2, "archived": False, "classLevel": None},
        {"id": "C3", "name": "İlayda", "callName": "", "kind": "individual", "memberCount": 1,
         "archived": False, "classLevel": None},
    ]},
    f"GET {BASE}/C1": DETAIL,
    f"GET {BASE}/C9": {**DETAIL, "id": "C9", "name": "LGS Fen", "classLevel": None,
                       "legacyClassName": None, "origin": "group", "current": [], "members": []},
    f"GET {BASE}/C1/history": {"items": [{"at": "2026-10-14T09:00:00+03:00", "actor": "sistem",
                                          "action": "class_merge", "revision": 1, "detail": {}}]},
    f"GET {BASE}/C9/history": {"items": []},
    f"POST {BASE}/C1/members": {"added": 1, "skipped": [], "levelUpdated": 1, "group": DETAIL},
    f"POST {BASE}": {**DETAIL, "id": "C9", "name": "LGS Fen"},
    f"POST {BASE}/academic-year": {"updated": 2, "skipped": [], "academicYear": "2019-2020"},
}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { mountGroups } = await load('./panels/bbd_students/groups.js');
  const host = document.createElement('div');
  document.body.append(host);
  const toasts = [];
  mountGroups(host, { api, can: () => true, students: () => __STUDENTS__,
    toast: (t) => toasts.push(t), dialogRoot: host });
  await sleep(30);
  const out = {};
  const items = () => host.querySelectorAll('.st-item');
  out.count = host.querySelector('.st-count').textContent;
  const yearSelect = host.querySelector('select[aria-label="Dönem"]');
  out.yearValue = yearSelect.value;
  out.yearOptions = yearSelect.querySelectorAll('option').map((o) => o.value);
  // Toplu dönem ata: iki sınıf işaretlenir → çubuk görünür → POST academic-year.
  const checks = host.querySelectorAll('.st-row-check');
  checks[0].checked = true; checks[0].dispatchEvent(new Event('change'));
  checks[1].checked = true; checks[1].dispatchEvent(new Event('change'));
  const bulk = host.querySelector('.gr-bulk');
  out.bulkHidden = bulk.hidden;
  out.bulkText = bulk.querySelector('.st-label').textContent;
  bulk.querySelector('select').value = '2019-2020';
  bulk.querySelectorAll('button').find((b) => b.textContent === 'Dönem ata').click();
  await sleep(30);
  out.yearPost = calls.filter((c) => c.path.endsWith('/academic-year')).map((c) => c.body);
  out.items = items().map((node) => node.textContent);
  out.badges = items().map((node) => node.querySelectorAll('.st-badge').map((b) => b.textContent));
  items()[0].click();
  await sleep(30);
  out.subtitle = host.querySelector('.st-subtitle').textContent;
  out.member = host.querySelector('.gr-member').textContent;
  out.buttons = host.querySelector('.st-detail-head').querySelectorAll('button').map((b) => b.textContent);
  const box = host.querySelector('.pk-box');
  out.pickerGroups = box.querySelector('.pk-select').querySelectorAll('option').map((o) => o.textContent);
  out.pickerAll = box.querySelectorAll('.pk-row').map((r) => r.querySelector('.pk-name').textContent);
  // Çoklu üyelik süzgeci: "TYT Matematik" seçilince o sınıfın üyeleri.
  const groupSelect = box.querySelector('.pk-select');
  groupSelect.value = 'TYT Matematik';
  groupSelect.dispatchEvent(new Event('change'));
  out.pickerTyt = box.querySelectorAll('.pk-row').map((r) => r.querySelector('.pk-name').textContent);
  groupSelect.value = '';
  groupSelect.dispatchEvent(new Event('change'));
  // Seviye süzgeci: 9. sınıf.
  const grade = host.querySelector('.gr-filters select');
  grade.value = '9';
  grade.dispatchEvent(new Event('change'));
  out.picker9 = box.querySelectorAll('.pk-row').map((r) => r.querySelector('.pk-name').textContent);
  box.querySelectorAll('.pk-row')[0].click();
  out.applyLabel = host.querySelectorAll('label.st-check').map((l) => l.textContent)
    .find((t) => t.includes('eklenenlere yaz')) || '';
  host.querySelectorAll('button').find((b) => b.textContent === 'Seçilenleri ekle').click();
  await sleep(30);
  out.addPost = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/members')).map((c) => c.body);
  out.violations = audit(host);
  out.text = host.textContent;
  // Yeni sınıf: ad + seviye (8 → hedef LGS önerilir) → POST.
  host.querySelectorAll('button').find((b) => b.textContent === '+ Yeni sınıf').click();
  await sleep(10);
  const dialog = host.querySelector('.gr-editor');
  out.dialogTitle = dialog.querySelector('.kit-dialog-title').textContent;
  out.dialogLabels = dialog.querySelectorAll('.st-label').map((l) => l.textContent);
  dialog.querySelector('input').value = 'LGS Fen';
  const selects = dialog.querySelectorAll('select');   // [Dönem, Seviye, Alan, Hedef]
  selects[1].value = '8';
  selects[1].dispatchEvent(new Event('change'));
  out.trackDisabled = selects[2].disabled;
  out.dialogViolations = audit(dialog);
  dialog.querySelectorAll('button').find((b) => b.textContent === 'Sınıfı aç').click();
  await sleep(40);
  out.createPost = calls.filter((c) => c.method === 'POST' && c.path === '/api/bbd_students/groups')
    .map((c) => c.body);
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    scenario = SCENARIO.replace("__STUDENTS__", json.dumps(STUDENTS, ensure_ascii=False))
    try:
        data = run(scenario, panels=("bbd_students",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_tek_liste_seviye_ve_ozel_ders(out: dict[str, Any]) -> None:
    assert out["count"] == "3 sınıf"
    assert "11. sınıf · SAY · YKS" in out["items"][0]
    assert out["badges"] == [[], [], ["Özel ders"]]
    assert "11. sınıf · SAY · YKS" in out["subtitle"]
    assert "diğer: TYT Matematik" in out["member"]
    assert "Seviyeyi üyelere uygula" in out["buttons"]


def test_arayuzde_grup_kelimesi_yok(out: dict[str, Any]) -> None:
    text = out["text"].replace("İ", "i").lower()
    assert "grup" not in text, out["text"]


def test_coklu_secici_sinif_seviye_suzgeci_ve_seviye_uygulama(out: dict[str, Any]) -> None:
    # Pasif ve zaten üye olan öğrenci seçicide yok; süzgeç öğrencinin BÜTÜN sınıflarını bilir.
    assert out["pickerAll"] == ["Can Demir", "Ece Çelik"]
    assert out["pickerGroups"] == ["Tüm sınıflar", "Sınıfsız", "TYT Matematik"]
    assert out["pickerTyt"] == ["Can Demir"]
    assert out["picker9"] == ["Ece Çelik"]
    assert "11. sınıf · SAY · YKS" in out["applyLabel"]
    assert out["addPost"] == [{"studentIds": ["STU-3"], "validFrom": out["addPost"][0]["validFrom"],
                               "reason": "", "applyLevel": True}]
    assert any("seviyesi güncellendi" in toast for toast in out["toasts"])
    assert out["violations"] == []


def test_yeni_sinif_ad_ozel_ders_ve_seviye(out: dict[str, Any]) -> None:
    assert out["dialogTitle"] == "Yeni sınıf"
    assert out["dialogLabels"][:3] == ["Sınıf adı", "Dönem", "Anons adı (TTS için)"]
    assert out["trackDisabled"] is True            # 8. sınıfta alan yok
    assert out["createPost"] == [{"name": "LGS Fen", "academicYear": YEAR, "callName": "",
                                  "kind": "group", "note": "",
                                  "level": {"gradeLevel": 8, "track": None, "examTarget": "LGS"}}]
    assert out["dialogViolations"] == []


def test_donem_suzgeci_varsayilan_guncel_ve_toplu_donem_ata(out: dict[str, Any]) -> None:
    # İş 30: liste varsayılan güncel dönemle süzülür; "Tüm dönemler" + verideki dönemler.
    assert out["yearValue"] == YEAR
    assert out["yearOptions"][0] == "" and YEAR in out["yearOptions"]
    assert "2019-2020" in out["yearOptions"]
    assert out["bulkHidden"] is False and out["bulkText"] == "2 sınıf seçili"
    assert out["yearPost"] == [{"groupIds": ["C1", "C2"], "academicYear": "2019-2020"}]


ARCHIVED = {"id": "A1", "name": "Eski 9-A", "callName": "", "kind": "group", "note": "",
            "archived": True, "archivedAt": "2026-06-30T18:00:00+03:00", "classLevel": None,
            "academicYear": "2025-2026", "current": [], "members": []}

DELETE_ROUTES: dict[str, Any] = {
    f"GET {BASE}?archived=archived": {"groups": [{**ARCHIVED, "memberCount": 0}]},
    f"GET {BASE}/A1": ARCHIVED,
    f"GET {BASE}/A1/history": {"items": []},
    f"DELETE {BASE}/A1": {"deleted": True, "id": "A1", "name": "Eski 9-A",
                          "removedMemberships": 24, "students": 24, "revision": 9},
}

DELETE_SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { mountGroups } = await load('./panels/bbd_students/groups.js');
  const host = document.createElement('div');
  document.body.append(host);
  const toasts = [];
  mountGroups(host, { api, can: (p) => p !== '__DENY__', students: () => [],
    toast: (t) => toasts.push(t), dialogRoot: host });
  await sleep(20);
  const out = {};
  const years = host.querySelector('select[aria-label="Dönem"]');
  years.value = ''; years.dispatchEvent(new Event('change'));
  const arch = host.querySelector('select[aria-label="Arşiv durumu"]');
  arch.value = 'archived'; arch.dispatchEvent(new Event('change'));
  await sleep(20);
  out.rowButtons = host.querySelector('.st-row').querySelectorAll('button').map((b) => b.textContent);
  host.querySelector('.st-row').querySelectorAll('button').find((b) => b.textContent === 'Sil').click();
  await sleep(10);
  const dialog = host.querySelector('.gr-editor');
  out.title = dialog.querySelector('.kit-dialog-title').textContent;
  out.violations = audit(dialog);
  const inputs = dialog.querySelectorAll('input');
  const go = () => dialog.querySelectorAll('button').find((b) => b.textContent === 'Kalıcı olarak sil').click();
  inputs[0].value = 'Eski'; go();
  out.mismatch = dialog.querySelector('.kit-dialog-error').textContent;
  inputs[0].value = 'eski 9-a'; inputs[1].value = 'yanlış açılmıştı'; inputs[2].value = '123456';
  go();
  await sleep(30);
  out.deletes = calls.filter((c) => c.method === 'DELETE').map((c) => ({ path: c.path, body: c.body }));
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def deleted() -> dict[str, Any]:
    try:
        data = run(DELETE_SCENARIO, panels=("bbd_students",), routes=DELETE_ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_arsivden_kalici_sil_ad_gerekce_pin(deleted: dict[str, Any]) -> None:
    assert "Sil" in deleted["rowButtons"]
    assert deleted["title"] == "“Eski 9-A” sınıfı kalıcı olarak silinsin mi?"
    assert deleted["mismatch"] == "Ad eşleşmiyor."
    assert deleted["deletes"] == [{"path": f"{BASE}/A1", "body": {
        "confirmName": "eski 9-a", "reason": "yanlış açılmıştı", "pin": "123456"}}]
    assert any("kalıcı silindi" in toast for toast in deleted["toasts"])
    assert deleted["violations"] == []
