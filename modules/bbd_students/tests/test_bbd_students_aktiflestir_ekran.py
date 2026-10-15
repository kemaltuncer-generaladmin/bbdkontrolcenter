"""Pasif öğrenciyi AKTİFLEŞTİRME ekranları (16.10.2026 kullanıcı: "KM de pasife alınan öğrenci
aktifleştirilebilsin").

Kart: pasif öğrencide başlıkta "Aktifleştir" → onay penceresi ("Pasifleştirmede kapanan sınıflarına
bugünden geri ekle" varsayılan açık) → `POST …/reactivate {restoreClasses}` → sonuç cümlesi açılan
sınıfları söyler. Liste: durum seçeneklerinde sayılar ("Pasif (1)"), pasif satırda tek tık
"Aktifleştir", "Pasif" süzgecinde toplu işlemin varsayılanı "Aktifleştir (pasifler)" →
`POST /students/reactivate`. Yeteneği BBD Akademi'deki öğrenci kartı kullanır
(`bbd_students.reactivate`). Erişilebilirlik denetimi her pencerede temiz.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run
from test_bbd_students_hizli_guncelleme import BASE, LIST_ROUTES, ROUTES, STUDENT

RESULT_ONE = {"studentId": "STU-1", "status": "active", "groupsReopened": True,
              "classesRestored": ["9-A"], "canteenSync": "ok", "tablet": "unblocked"}

CARD_ROUTES: dict[str, Any] = {
    **ROUTES,
    "GET /auth/me": {"permissions": ["bbd_students.view", "bbd_students.manage", "bbd_students.lifecycle"]},
    f"GET {BASE}/students/STU-1": {**STUDENT, "status": "inactive", "statusReason": "left"},
    f"POST {BASE}/students/STU-1/reactivate": RESULT_ONE,
}

CARD = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path) => (path.startsWith('/api/events/wait') ? new Promise(() => {}) : undefined);
  const { mountRecord } = await load('./panels/bbd_students/record.js');
  const host = document.createElement('div');
  document.body.append(host);
  const toasts = [];
  mountRecord(host, { api, kantinId: 'STU-1', can: () => true, toast: (text) => toasts.push(text),
    capability: () => null, dialogRoot: document.body });
  await sleep(40);
  const out = {};
  const head = host.querySelector('.st-rec-actions');
  const on = head.querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir' && !b.hidden);
  out.headButton = Boolean(on);
  on.click();
  await sleep(10);
  const dialog = () => document.querySelector('.st-lc-dialog');
  out.dialogText = dialog()?.textContent || '';
  out.restoreChecked = dialog().querySelector('input[type=checkbox]').checked;
  out.violations = audit(document.body);
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  await sleep(10);
  out.escCloses = !dialog();
  out.postsAfterEsc = calls.filter((c) => c.method === 'POST').length;
  on.click();
  await sleep(10);
  dialog().querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir').click();
  await sleep(40);
  out.posts = calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body]);
  out.toasts = toasts;
  return out;
}
"""

LIST = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (method === 'POST' && path === '/api/bbd_students/students/reactivate') {
      return { reactivated: ['STU-2'], failed: [], classesRestored: 1, canteenPending: 1 };
    }
    if (method === 'POST' && path === '/api/bbd_students/students/STU-2/reactivate') {
      return { studentId: 'STU-2', status: 'active', groupsReopened: false, classesRestored: [], canteenSync: 'ok' };
    }
    return undefined;
  };
  const sk = await load('./panels/bbd_students/index.js');
  const caps = sk.capabilities({ api, open: () => {}, capability: () => null });
  const root = document.createElement('div');
  document.body.append(root);
  caps['bbd_students.manager'](root, { openStudent: () => {}, hideModes: true, capability: () => null });
  await sleep(40);
  const out = {};
  const status = root.querySelector('select[aria-label="Durum"]');
  out.statusOptions = status.querySelectorAll('option').map((o) => o.textContent);
  status.value = 'inactive';
  status.dispatchEvent(new Event('change'));
  await sleep(10);
  out.rows = root.querySelectorAll('.st-row .st-item b').map((b) => b.textContent);
  const rowButton = root.querySelector('.st-row-action');
  out.rowButton = rowButton?.textContent || '';
  out.rowLabel = rowButton?.getAttribute('aria-label') || '';
  // Satırdan tek tık
  rowButton.click();
  await sleep(10);
  const dialog = () => root.querySelector('.st-lc-dialog');
  out.violationsRow = audit(root);
  dialog().querySelector('input[type=checkbox]').click();          // sınıfları açma
  dialog().querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir').click();
  await sleep(30);
  out.rowPost = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/STU-2/reactivate')).map((c) => c.body);
  // Toplu: "Pasif" süzgecinde varsayılan alan aktifleştir
  root.querySelector('.st-row-check').click();
  const bar = root.querySelector('.st-listbulk');
  out.bulkField = bar.querySelector('select').value;
  out.bulkHint = bar.querySelector('.st-listbulk-value').textContent;
  bar.querySelectorAll('button').find((b) => b.textContent === 'Uygula').click();
  await sleep(20);
  out.bulkDialog = dialog()?.textContent || '';
  dialog().querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir').click();
  await sleep(40);
  out.bulkPost = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/students/reactivate')).map((c) => c.body);
  out.bulkStatus = bar.querySelector('.st-listbulk-status').textContent;
  // Akademi kartının kullandığı yetenek
  out.capAllowed = await caps['bbd_students.reactivate'].allowed();
  return out;
}
"""


def node(scenario: str, routes: dict[str, Any]) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_students",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_kartta_pasif_ogrenci_aktiflestirilir_siniflariyla() -> None:
    out = node(CARD, CARD_ROUTES)
    assert out["headButton"] is True
    assert "aktifleştir" in out["dialogText"] and "sınıflarına bugünden geri ekle" in out["dialogText"]
    assert out["restoreChecked"] is True
    assert out["violations"] == []
    assert out["escCloses"] is True and out["postsAfterEsc"] == 0
    assert out["posts"] == [[f"{BASE}/students/STU-1/reactivate", {"restoreClasses": True}]]
    assert out["toasts"] == ["Aktifleştirildi — 9-A sınıfına geri eklendi."]


def test_listede_pasif_sayisi_satirdan_ve_toplu_aktiflestirme() -> None:
    students = [
        {"kantinId": "STU-1", "displayName": "Elif Kaya", "className": "9-A", "status": "active", "balance": 0,
         "guardians": []},
        {"kantinId": "STU-2", "displayName": "Can Demir", "className": "9-A", "status": "inactive",
         "statusReason": "left", "balance": 0, "guardians": []},
    ]
    routes = {**LIST_ROUTES, f"GET {BASE}/students": {"connected": True, "summary": {}, "students": students}}
    out = node(LIST, routes)
    assert out["statusOptions"] == ["Etkin (1)", "Pasif (1)", "Etkin + pasif"]
    assert out["rows"] == ["Can Demir"]
    assert out["rowButton"] == "Aktifleştir" and out["rowLabel"] == "Can Demir aktifleştir"
    assert out["violationsRow"] == []
    assert out["rowPost"] == [{"restoreClasses": False}]
    assert out["bulkField"] == "reactivate" and "yalnız pasif olanlar" in out["bulkHint"]
    assert "Can Demir" in out["bulkDialog"]
    assert out["bulkPost"] == [{"students": ["STU-2"], "restoreClasses": True}]
    assert out["bulkStatus"] == "1 öğrenci aktifleştirildi, 1 sınıf üyeliği geri açıldı."
    assert out["capAllowed"] is True
