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
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_students/groups"
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
    f"GET {BASE}?archived=live": {"groups": [
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
  const selects = dialog.querySelectorAll('select');
  selects[0].value = '8';
  selects[0].dispatchEvent(new Event('change'));
  out.trackDisabled = selects[1].disabled;
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
    assert out["dialogLabels"][:2] == ["Sınıf adı", "Anons adı (TTS için)"]
    assert out["trackDisabled"] is True            # 8. sınıfta alan yok
    assert out["createPost"] == [{"name": "LGS Fen", "callName": "", "kind": "group", "note": "",
                                  "level": {"gradeLevel": 8, "track": None, "examTarget": "LGS"}}]
    assert out["dialogViolations"] == []
