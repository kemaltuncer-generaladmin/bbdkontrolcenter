"""Öğrenci Yönetimi → "Toplu işlemler" seçicisi (BBD Akademi içinde gömülü).

Kullanıcı (13.10.2026): "KM'de toplu işlemler kısmında öğrenci seçilemiyor,
hatalı, UI görünmüyor." Kök nedenler ve korumaları:

* Ödeme Talebi panelinin kapsamsız stili kitin seçicisini 15 px'e indiriyordu
  → `tests/integration/test_panel_css_kapsam.py`.
* Gömülü görünümde (`.st-embedded`) seçici bütün listeyi alt alta diziyordu
  (340 öğrencide ~9.000 px) → yükseklik görüntü alanına bağlı (CSS kuralı
  aşağıda sınanır).
* Seçici okuma bitmeden "Liste boş." diyordu, kantine ulaşılamayınca sebebi
  söylemiyordu → yükleniyor / hata + "Tekrar dene".

Senaryo `createManager`'ı (`bbd_students.manager` yeteneği) Akademi'nin
yaptığı gibi `hideModes` ile kurar, `setMode('bulk')` ile sekmeyi açar.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

BASE = "/api/bbd_students"
PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"


def student(i: int, name: str, klass: str, **extra: Any) -> dict[str, Any]:
    return {"kantinId": extra.pop("kantinId", f"STU-{i}"), "displayName": name, "firstName": name.split()[0],
            "lastName": name.split()[-1], "className": klass, "schoolNo": str(100 + i), "status": "active",
            "accessCode": "", "balance": 0, "guardians": [], "canteenSync": "ok", **extra}


STUDENTS = [student(1, "Elif Kaya", "9-A"), student(2, "Can Demir", "9-A"),
            student(3, "Veli Yazılım", "10-B", kantinId="veliyazılım"),
            student(4, "Ece Çelik", "10-B"), student(5, "Ömer Ak", "", status="inactive")]

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  let listCalls = 0;
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait')) return new Promise(() => {});
    if (method === 'GET' && path === '/api/bbd_students/students') {
      listCalls += 1;
      if (listCalls === 1) return { __status: 500, message: 'Kantin zaman aşımı' };
      return { students: __STUDENTS__, connected: true, summary: null };
    }
    if (method === 'POST' && path === '/api/bbd_students/students/class') {
      return { updated: options.body.students, failed: [] };
    }
    if (method === 'POST' && path.endsWith('/members')) return { added: options.body.studentIds.length, skipped: [] };
    return undefined;
  };
  const sk = await load('./panels/bbd_students/index.js');
  const caps = sk.capabilities({ api, open: () => {}, capability: () => null });
  const root = document.createElement('div');
  document.body.append(root);
  const cleanup = caps['bbd_students.manager'](root, { openStudent: () => {}, hideModes: true });
  cleanup.setMode('bulk');
  const out = {};
  const bulk = () => root.querySelector('.st-bulk');
  const box = () => bulk().querySelector('.pk-box');
  const rows = () => box().querySelectorAll('.pk-row');
  const btn = (scope, text) => scope.querySelectorAll('button').find((b) => b.textContent.startsWith(text));
  out.embedded = root.querySelector('.st').className;
  out.initial = box().querySelector('.pk-list').textContent;
  await sleep(40);
  // 1) ilk okuma 500 → seçici sebebi ve "Tekrar dene"yi gösterir (boş liste değil)
  out.error = box().querySelector('.pk-error')?.textContent || '';
  btn(box(), 'Tekrar dene').click();
  await sleep(40);
  out.rows = rows().map((r) => r.querySelector('.pk-name').textContent);
  out.groups = box().querySelector('.pk-select').querySelectorAll('option').map((o) => o.textContent);
  out.idle = bulk().querySelector('.st-bulk-count').textContent;
  out.countRole = bulk().querySelector('.st-bulk-count').getAttribute('role');
  out.violations = audit(root);
  // 2) sınıf süzgeci + görünenleri seç → sayı
  const group = box().querySelector('.pk-select');
  group.value = '10-B';
  group.dispatchEvent(new Event('change'));
  btn(box(), 'Görünenleri seç').click();
  out.count = bulk().querySelector('.st-bulk-count').textContent;
  // 3) klavye: aramada ↓ → satır, Boşluk (tıklama) seçer
  group.value = '';
  group.dispatchEvent(new Event('change'));
  const search = box().querySelector('.pk-search');
  search.value = 'elif';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(120);
  search.focus();
  search.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true }));
  document.activeElement.click();
  out.afterKey = bulk().querySelector('.st-bulk-count').textContent;
  // 4) "Sınıfa ata" seçilen ÜÇ öğrenciyi gönderir (Türkçe harfli kimlik dahil)
  const classBox = bulk().querySelectorAll('.st-bulk-plan')[0];
  const input = classBox.querySelector('input');
  input.value = '11-A';
  btn(classBox, 'Seçilenleri bu sınıfa ata').click();
  await sleep(10);
  const ok = root.querySelectorAll('button').find((b) => b.textContent === 'Ata');
  ok?.click();
  await sleep(40);
  out.classPost = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/students/class')).map((c) => c.body);
  out.classStatus = classBox.querySelector('.st-bulk-status').textContent;
  // 5) "Gruba ekle" de aynı seçimi gönderir
  const groupBox = bulk().querySelectorAll('.st-bulk-plan')[1];
  const selects = groupBox.querySelectorAll('select');
  await sleep(10);
  selects[1].value = 'G1';
  btn(groupBox, 'Uygula').click();
  await sleep(40);
  out.groupPost = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/members')).map((c) => [c.path, c.body]);
  out.violationsEnd = audit(root);
  cleanup();
  return out;
}
"""

ROUTES: dict[str, Any] = {
    "GET /auth/me": {"permissions": ["bbd_students.view", "bbd_students.manage", "bbd_students.groups"]},
    f"GET {BASE}/groups?archived=live": {"groups": [{"id": "G1", "name": "LGS Etüt", "kind": "group"}]},
    f"GET {BASE}/groups/memberships": {"items": []},
}


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    import json

    scenario = SCENARIO.replace("__STUDENTS__", json.dumps(STUDENTS, ensure_ascii=False))
    try:
        data = run(scenario, panels=("bbd_students",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_gomulu_ve_ilk_okumada_bos_liste_demez(out: dict[str, Any]) -> None:
    assert "st-embedded" in out["embedded"]
    assert out["initial"] == "Yükleniyor…"
    assert out["error"].startswith("Öğrenci listesi okunamadı: Kantin zaman aşımı")


def test_liste_sinif_suzgeci_ve_sayi(out: dict[str, Any]) -> None:
    # Sıra: sınıf (9-A, 10-B, sınıfsız), sınıf içinde ad (KM listesiyle aynı).
    assert out["rows"] == ["Can Demir", "Elif Kaya", "Ece Çelik", "Veli Yazılım", "Ömer Ak"]
    assert out["groups"] == ["Tüm sınıflar", "10-B", "9-A", "Sınıf girilmemiş"]
    assert out["idle"].startswith("Seçim yok") and out["countRole"] == "status"
    assert out["violations"] == []
    assert out["count"] == "2 öğrenci seçili"
    assert out["afterKey"] == "3 öğrenci seçili"


def test_secim_toplu_islemlere_gider(out: dict[str, Any]) -> None:
    assert out["classPost"] == [{"students": ["STU-4", "veliyazılım", "STU-1"], "className": "11-A"}]
    assert out["classStatus"].startswith("3 öğrenci “11-A” sınıfına atandı")
    assert out["groupPost"] == [[f"{BASE}/groups/G1/members",
                                 {"studentIds": ["STU-4", "veliyazılım", "STU-1"]}]]
    assert out["violationsEnd"] == []


def test_gomulu_secici_yuksekligi_goruntu_alanina_bagli() -> None:
    """Gömülü kökte `height: auto` → seçici yüksekliği açıkça sınırlanmalı (liste içte kayar)."""
    css = (PANEL / "panel.css").read_text(encoding="utf-8")
    rule = re.search(r"\.st-embedded \.st-bulk-layout \.pk-box \{([^}]*)\}", css)
    assert rule and "height: clamp(" in rule.group(1) and "position: sticky" in rule.group(1)
    assert re.search(r"\.st-embedded \.st-bulk-layout \{[^}]*height: auto", css)
    assert re.search(r"@media \(max-width: 1000px\) \{\s*\.st-bulk-layout \{ grid-template-columns: minmax\(0, 1fr\)",
                     css)
