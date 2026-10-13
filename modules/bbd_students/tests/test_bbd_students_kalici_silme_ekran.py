"""Kalıcı öğrenci silme EKRANLARI (0.5.4) — öğrenci kartı ve listede toplu silme.

Kart: "Tehlikeli işlemler" EN ALTTA ve ayrıdır; önerilen yol pasifleştirme
yanında durur. "Kalıcı olarak sil…" önce ön kontrol yapar (yazma yok): engel
varsa açık Türkçe + ilgili bölüme götüren düğme; yoksa onay penceresi neyin
silineceğini söyler, adı yazdırır ve PIN ister. Enter yalnız alanlarda onaylar
("Vazgeç"teyken onaylamaz), Esc vazgeçer. Liste: çoklu seçim → "Kalıcı olarak
sil" → sayı + PIN → öğrenci başına sonuç penceresi. Erişilebilirlik denetimi
her adımda temiz.
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

CARD_ROUTES: dict[str, Any] = {
    **ROUTES,
    f"GET {BASE}/students/STU-1": {**STUDENT, "status": "inactive", "statusReason": "left", "balance": 4250},
    f"DELETE {BASE}/students/STU-1": {"studentId": "STU-1", "deleted": True, "canteenSync": "ok"},
}

CHECK_BLOCKED = {"studentId": "STU-1", "displayName": "Elif Kaya", "status": "inactive", "deletable": False,
                 "blockers": [{"code": "balance_not_zero", "details": {"balance": 4250},
                               "message": "Kantin bakiyesi 42,50 ₺ borç — önce tahsil edin ve sıfırlayın."},
                              {"code": "institution_debt", "details": {},
                               "message": "Kurum cari borcu var — önce Kurum finansı'ndan kapatın."}],
                 "notes": [], "willDelete": ["KM öğrenci kaydı silinir.", "BBD Akademi KVKK planına göre siler."]}
CHECK_OK = {**CHECK_BLOCKED, "deletable": True, "blockers": [],
            "notes": [{"code": "institution_credit", "details": {"creditMinor": 10000},
                       "message": "Kurum carisinde öğrenci lehine 100,00 ₺ alacak var."}]}

CARD = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  let checks = 0;
  globalThis.__apiHook = (method, path) => {
    if (path.startsWith('/api/events/wait')) return new Promise(() => {});
    if (path.endsWith('/delete-check')) { checks += 1; return checks === 1 ? __BLOCKED__ : __OK__; }
    return undefined;
  };
  const { mountRecord } = await load('./panels/bbd_students/record.js');
  const host = document.createElement('div');
  document.body.append(host);
  let removed = 0;
  let finance = 0;
  mountRecord(host, { api, kantinId: 'STU-1', can: () => true, toast: () => {}, capability: () => null,
    dialogRoot: document.body, onRemoved: () => { removed += 1; }, openFinance: () => { finance += 1; } });
  await sleep(40);
  const out = {};
  const sections = host.querySelectorAll('section.st-card');
  const danger = host.querySelector('.st-danger');
  out.dangerLast = sections[sections.length - 1] === danger;
  out.headDelete = host.querySelector('.st-rec-actions').textContent.includes('Kalıcı');
  out.dangerButtons = danger.querySelectorAll('button').map((b) => b.textContent);
  const go = () => danger.querySelectorAll('button').find((b) => b.textContent.startsWith('Kalıcı olarak sil'));
  // 1) Ön kontrol: engeller + bağlantılar, istek yalnız GET.
  go().focus();
  go().click();
  await sleep(30);
  out.blockers = danger.querySelector('.st-del-blockers')?.textContent || '';
  out.blockerButtons = danger.querySelectorAll('.st-del-blockers button').map((b) => b.textContent);
  out.violationsBlocked = audit(host);
  danger.querySelectorAll('.st-del-blockers button').find((b) => b.textContent === 'Kantin bölümüne git').click();
  out.focusCanteen = document.activeElement?.querySelector?.('h4')?.textContent || '';
  danger.querySelectorAll('.st-del-blockers button').find((b) => b.textContent === 'Kurum carisini aç').click();
  out.finance = finance;
  // 2) Temiz ön kontrol → onay penceresi.
  go().focus();
  go().click();
  await sleep(30);
  const dialog = () => document.querySelector('.st-lc-dialog');
  out.dialogText = dialog()?.textContent || '';
  out.violationsDialog = audit(document.body);
  const cancel = dialog().querySelectorAll('button').find((b) => b.textContent === 'Vazgeç');
  cancel.focus();
  cancel.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await sleep(10);
  out.enterOnCancelKeepsOpen = Boolean(dialog());
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  await sleep(10);
  out.escCloses = !dialog();
  out.focusBack = document.activeElement?.textContent || '';
  // 3) Yanlış ad → hata; doğru ad + PIN + Enter → DELETE.
  go().click();
  await sleep(30);
  const [name, pin] = dialog().querySelectorAll('input');
  name.value = 'Elif';
  dialog().querySelectorAll('button').find((b) => b.textContent === 'Kalıcı olarak sil').click();
  out.nameError = dialog().querySelector('.kit-dialog-error').textContent;
  name.value = 'elif kaya';
  pin.value = '246810';
  pin.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  await sleep(30);
  out.deleteCalls = calls.filter((c) => c.method === 'DELETE').map((c) => [c.path, c.body]);
  out.removed = removed;
  out.checks = checks;
  return out;
}
"""

LIST = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (method === 'POST' && path === '/api/bbd_students/students/delete') {
      return { deleted: 1, blocked: 1, canteenPending: 1, results: [
        { studentId: 'STU-1', displayName: 'Elif Kaya', deleted: true, code: null, message: 'Kalıcı silindi.' },
        { studentId: 'STU-2', displayName: 'Can Demir', deleted: false, code: 'not_inactive',
          message: 'Öğrenci etkin: kalıcı silmeden önce pasifleştirin.' }] };
    }
    return undefined;
  };
  const sk = await load('./panels/bbd_students/index.js');
  const caps = sk.capabilities({ api, open: () => {}, capability: () => null });
  const root = document.createElement('div');
  document.body.append(root);
  const docCalls = [];
  const capability = (name) => (name === 'bbd_akademi.deletionDocument' ? (host, spec) => {
    docCalls.push(spec.result.deleted);
    const node = document.createElement('button');
    node.textContent = 'Sonuç belgesi · önizle';
    return node;
  } : null);
  caps['bbd_students.manager'](root, { openStudent: () => {}, hideModes: true, capability });
  await sleep(40);
  const out = {};
  const bar = root.querySelector('.st-listbulk');
  const checks = root.querySelectorAll('.st-row-check');
  checks[0].click();
  checks[1].click();
  const field = bar.querySelector('select');
  out.options = field.querySelectorAll('option').map((o) => o.value);
  field.value = 'delete';
  field.dispatchEvent(new Event('change'));
  await sleep(10);
  out.hint = bar.querySelector('.st-listbulk-value').textContent;
  bar.querySelectorAll('button').find((b) => b.textContent === 'Uygula').click();
  await sleep(20);
  const dialog = () => root.querySelector('.st-lc-dialog');
  out.confirmText = dialog().textContent;
  out.violationsConfirm = audit(root);
  const [count, pin] = dialog().querySelectorAll('input');
  count.value = '3';
  const confirm = dialog().querySelectorAll('button').find((b) => b.textContent === '2 öğrenciyi kalıcı sil');
  confirm.click();
  out.countError = dialog().querySelector('.kit-dialog-error').textContent;
  count.value = '2';
  pin.value = '246810';
  confirm.click();
  await sleep(40);
  out.post = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/students/delete')).map((c) => c.body);
  out.result = dialog()?.textContent || '';
  out.violationsResult = audit(root);
  out.docCalls = docCalls;
  dialog().querySelectorAll('button').find((b) => b.textContent === 'Kapat').click();
  await sleep(40);
  out.status = bar.querySelector('.st-listbulk-status').textContent;
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


def test_kartta_tehlikeli_islemler_on_kontrol_ve_guclu_onay() -> None:
    import json
    scenario = CARD.replace("__BLOCKED__", json.dumps(CHECK_BLOCKED, ensure_ascii=False)).replace(
        "__OK__", json.dumps(CHECK_OK, ensure_ascii=False))
    out = node(scenario, CARD_ROUTES)
    assert out["dangerLast"] is True and out["headDelete"] is False
    assert out["dangerButtons"] == ["Kalıcı olarak sil…"]          # pasif öğrenci: pasifleştir yok
    assert "42,50 ₺" in out["blockers"] and "Kurum cari borcu" in out["blockers"]
    assert out["blockerButtons"] == ["Kantin bölümüne git", "Kurum carisini aç"]
    assert out["violationsBlocked"] == []
    assert out["focusCanteen"] == "Kantin"
    assert out["finance"] == 1
    assert "Silinecekler" in out["dialogText"] and "KVKK" in out["dialogText"]
    assert "100,00 ₺ alacak" in out["dialogText"] and "PIN" in out["dialogText"]
    assert out["violationsDialog"] == []
    assert out["enterOnCancelKeepsOpen"] is True
    assert out["escCloses"] is True and out["focusBack"].startswith("Kalıcı olarak sil")
    assert out["nameError"] == "Ad eşleşmiyor."
    assert out["deleteCalls"] == [[f"{BASE}/students/STU-1", {"confirmName": "elif kaya", "pin": "246810"}]]
    assert out["removed"] == 1 and out["checks"] == 3


def test_listede_toplu_kalici_silme_ogrenci_basina_sonuc() -> None:
    routes = {**LIST_ROUTES, "GET /auth/me": {"permissions": [
        "bbd_students.view", "bbd_students.manage", "bbd_students.lifecycle", "bbd_students.delete"]}}
    out = node(LIST, routes)
    assert out["options"][-1] == "delete"
    assert "Önerilen: önce pasifleştirin" in out["hint"]
    assert "GERİ ALINAMAZ" in out["confirmText"] and "Elif Kaya" in out["confirmText"]
    assert out["violationsConfirm"] == []
    assert out["countError"] == "Sayı eşleşmiyor."
    assert out["post"] == [{"students": ["STU-1", "STU-2"], "confirmCount": "2", "pin": "246810"}]
    assert "1 öğrenci silindi · 1 öğrenci silinmedi" in out["result"]
    assert "Can Demir — silinmedi: Öğrenci etkin" in out["result"]
    assert out["violationsResult"] == []
    # Akademi varsa sonuç penceresinde kurum logolu sonuç belgesi (Önizle / Yazdır).
    assert "Sonuç belgesi" in out["result"] and out["docCalls"] == [1]
    assert out["status"] == "1 öğrenci kalıcı silindi, 1 silinmedi (nedenleri gösterildi)."


def test_silme_yetkisi_yoksa_toplu_secenek_yok() -> None:
    out = node(LIST.replace("field.value = 'delete';", "out.early = true; return out;"), LIST_ROUTES)
    assert "delete" not in out["options"]
