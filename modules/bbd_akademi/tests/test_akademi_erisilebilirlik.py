"""Erişilebilirlik (WCAG 2.2 AA) ve tek giriş noktası — BBD Akademi paneli (0.5.3).

Kullanıcı: "Mesele kullanım kolaylığı; erişilebilirliği full olsun." Panelin ana
sayfası ve her grubun her hapı (15.10.2026 tek gezinme: Bugün, Öğrenciler, Koçluk,
Dersler ve Program, Yoklama, Ödev ve Deneme, İletişim, Finans ve Kantin, Raporlar,
Ayarlar) Node'da küçük bir DOM üzerinde açılır ve
axe benzeri denetimden geçer (`tools/a11y/audit.mjs`: ad, etiket, ARIA bağları,
diyalog adı, klavyeyle erişim, canlı bölge…). Ayrıca Akademi izni olmayan
öğrenci yöneticisi (muhasebe, org_staff) paneli YALNIZ "Öğrenciler" ile görür
ve Akademi'ye hiç istek atmaz. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

PANELS = ("bbd_students", "bbd_akademi", "bbd_payment_request")

ALL_SECTIONS = r"""
export default async function ({ document, audit, sleep, api, load }) {
  const students = [
    { kantinId: 'STU-1', displayName: 'Elif Kaya', className: '9-A', schoolNo: '77', balance: 0, status: 'active', guardians: [{ name: 'Ayşe', phone: '5321112233', primary: true }], parentPhone: '5321112233', canteenSync: 'ok' },
    { kantinId: 'STU-2', displayName: 'Can Demir', className: '10-B', schoolNo: '78', balance: 1200, status: 'active', guardians: [], parentPhone: '', canteenSync: 'pending' },
  ];
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path === '/api/bbd_akademi/km/call') {
      const body = options.body || {};
      if (body.path === '/v1/admin/students') return { ok: true, status: 200, data: { items: [] }, headers: {} };
      const row = (i) => ({ id: `X${i}`, displayName: `Kişi ${i}`, name: `Ad ${i}`, title: `Başlık ${i}`, status: 'active',
        externalRef: `STU-${i}`, classGroup: { id: 'C1', name: '9-A' }, createdAt: '2026-10-10T10:00:00Z', date: '2026-10-10',
        student: { id: 'S1', displayName: 'Elif' }, count: 2, total: 3 });
      return { ok: true, status: 200, data: { items: [row(1), row(2)], nextCursor: null }, headers: {} };
    }
    return undefined;
  };
  const sk = await load('./panels/bbd_students/index.js');
  const ak = await load('./panels/bbd_akademi/index.js');
  const providers = new Map();
  const ctxBase = { api, open: () => {}, capability: (name) => providers.get(name) ?? null };
  for (const [name, value] of Object.entries(sk.capabilities(ctxBase))) providers.set(name, value);
  for (const [name, value] of Object.entries(ak.capabilities(ctxBase))) providers.set(name, value);
  const root = document.createElement('div');
  root.id = 'panel-body';
  document.body.append(root);
  const cleanup = ak.mount(root, { panel: { id: 'bbd_akademi' }, api, payload: { section: 'students' },
    capability: (name) => providers.get(name) ?? null, open: () => {} });
  await sleep(80);
  const out = {};
  out.__leftMenus = root.querySelectorAll('.ak-nav, .ak-nav-item, .ak-nav-subitem').length;
  const areas = root.querySelectorAll('.ak-area').map((b) => b.textContent);
  out.__areas = areas;
  for (const label of areas) {
    root.querySelectorAll('.ak-area').find((b) => b.textContent === label).click();
    await sleep(80);
    const pills = root.querySelector('.ak-pills');
    const subs = pills && !pills.hidden ? root.querySelectorAll('.ak-pill').map((b) => b.textContent) : [];
    for (const sub of (subs.length ? subs : [null])) {
      if (sub) { root.querySelectorAll('.ak-pill').find((b) => b.textContent === sub).click(); await sleep(80); }
      out[`${label} / ${sub || '-'}`] = audit(root);
    }
  }
  cleanup?.();
  return out;
}
"""

ROUTES = {
  "GET /api/bbd_akademi/km/status?operations=1": {"connection": {"enabled": True, "hasKey": True},
     "permissions": ["bbd_akademi." + k for k in ["view","academic","curriculum","resources","attendance","exams","finance","finance_void","books","announcements","requests","settings","staff","campus","canteen","learning","learning_setup","evaluations","privacy"]],
     "features": None,
     # Koçluk hapları ve SMS ekranı okuma işlemi izinliyse görünür (coaching.js sekmesiyle aynı kural).
     "operationList": [{"operationId": op, "allowed": True} for op in [
         "admin_getCoachQueue", "admin_listCoachReviews", "admin_listCoachingStudents", "admin_getCoachWeek",
         "admin_listCoaches", "admin_listCoachLibrary", "admin_listCoachBadges", "admin_listCheckins",
         "admin_listCoachMeetings", "admin_getSmsHealth", "admin_listCoachTextTemplates"]]},
  "GET /auth/me": {"permissions": ["bbd_students.view", "bbd_students.manage", "bbd_students.groups", "bbd_students.lifecycle", "bbd_students.canteen_controls", "bbd_students.qr", "bbd_students.access_code", "bbd_akademi.view"]},
  "GET /api/bbd_students/students": {"connected": True, "students": [
    {"kantinId": "STU-1", "displayName": "Elif Kaya", "className": "9-A", "schoolNo": "77", "balance": 0, "status": "active", "guardians": [{"name": "Ayşe", "phone": "5321112233", "primary": True}], "parentPhone": "5321112233", "canteenSync": "ok"},
    {"kantinId": "STU-2", "displayName": "Can Demir", "className": "10-B", "schoolNo": "78", "balance": 1200, "status": "active", "guardians": [], "parentPhone": "", "canteenSync": "pending"}], "summary": {}},
  "GET /api/events/wait": {"items": [], "lastSeq": 3, "reset": False},
  "GET /api/bbd_students/groups?archived=live": {"groups": [], "items": []},
  "GET /api/bbd_students/groups?archived=live&year=*": {"groups": [], "items": [], "years": []},
  "GET /api/bbd_students/groups": {"groups": [], "items": [], "revision": 1},
  "GET /api/bbd_students/groups/memberships": {"items": []},
  "GET /api/bbd_students/students/STU-1": {"kantinId": "STU-1", "displayName": "Elif Kaya", "firstName": "Elif", "lastName": "Kaya", "className": "9-A", "schoolNo": "77", "status": "active", "balance": 0, "canteenSync": "ok", "guardians": []},
  "GET /api/bbd_students/students/STU-1/guardians": {"items": [], "max": 4},
  "GET /api/bbd_students/students/STU-1/groups": {"items": []},
  "GET /api/bbd_students/students/STU-1/canteen": {"available": True, "controls": {"balance": 0, "spendingLimit": None, "dailyLimit": None, "isBlocked": False, "guardianBlocked": False, "restrictedProductIds": [], "spentToday": 0}, "products": []},
  "GET /api/bbd_students/students/STU-1/status-history": {"items": []},
}


KM_ONLY = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path.startsWith('/api/bbd_akademi/')) return { __status: 403, message: 'Bu işlem için yetkiniz yok.' };
    return undefined;
  };
  const sk = await load('./panels/bbd_students/index.js');
  const ak = await load('./panels/bbd_akademi/index.js');
  const providers = new Map();
  const base = { api, open: () => {}, capability: (name) => providers.get(name) ?? null };
  for (const [name, value] of Object.entries(sk.capabilities(base))) providers.set(name, value);
  for (const [name, value] of Object.entries(ak.capabilities(base))) providers.set(name, value);
  const root = document.createElement('div');
  document.body.append(root);
  ak.mount(root, { panel: { id: 'bbd_akademi' }, api, payload: null,
    capability: (name) => providers.get(name) ?? null, open: () => {} });
  await sleep(120);
  const out = {
    nav: root.querySelectorAll('.ak-area').map((b) => b.textContent),
    tabs: root.querySelectorAll('[role="tab"]').map((b) => b.textContent.trim()),
    banner: root.querySelector('.ak-banner')?.textContent || '',
    violations: audit(root),
  };
  const before = calls.filter((c) => c.path.startsWith('/api/bbd_akademi/km/call')).length;
  root.querySelectorAll('.st-item')[0]?.click();
  await sleep(120);
  out.akademiCalls = calls.filter((c) => c.path.startsWith('/api/bbd_akademi/km/call')).length - before;
  out.recordOpen = Boolean(document.querySelector('.st-rec'));
  out.cardViolations = audit(document.body);
  return out;
}
"""


def node_run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=PANELS, routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_butun_bolumler_denetimden_gecer() -> None:
    result = node_run(ALL_SECTIONS)
    # TEK SOL GEZİNME (15.10.2026): panel kendi sol menüsünü çizmez; üstte ana sayfa + on ana grup.
    assert result.pop("__leftMenus") == 0
    assert result.pop("__areas") == ["Ana sayfa", "Bugün", "Öğrenciler", "Koçluk", "Dersler ve Program",
                                      "Yoklama", "Ödev ve Deneme", "İletişim", "Finans ve Kantin",
                                      "Raporlar", "Ayarlar"]
    # Ana sayfa + 38 ekran: koçluk ekranının sekmeleri artık Koçluk grubunun hapları (8), SMS
    # İletişim'de kendi ekranı, Rapor merkezi yeni (dağınık raporların tek kapısı).
    assert len(result) == 40, sorted(result)  # +hata defteri gelen kutusu (§14 madde 104)
    assert result["Ana sayfa / -"] == []
    assert result["Koçluk / Hoca soruları"] == []
    assert result["Dersler ve Program / Ders oturumları"] == []
    assert result["İletişim / Mesajlar"] == []
    assert result["Koçluk / Haftalık program"] == []
    assert result["İletişim / SMS"] == []
    assert result["Raporlar / Rapor merkezi"] == []
    bad = {name: items for name, items in result.items() if items}
    assert bad == {}, bad


def test_akademi_izni_olmayan_yalniz_ogrencileri_gorur() -> None:
    result = node_run(KM_ONLY)
    assert result["nav"] == ["Öğrenciler"]
    # Sınıf = grup (iş 23): eski "Gruplar" sekmesi kalktı — TEK "Sınıflar" (KM kaydı, Akademi izni istemez).
    assert result["tabs"] == ["Öğrenciler", "Toplu işlemler", "Sınıflar"]
    assert "Öğrenci kayıtlarını" in result["banner"]
    assert result["violations"] == []
    # Kart yalnız KM kaydıdır: Akademi'ye istek gitmez.
    assert result["akademiCalls"] == 0
    assert result["recordOpen"] is True
    assert result["cardViolations"] == []
