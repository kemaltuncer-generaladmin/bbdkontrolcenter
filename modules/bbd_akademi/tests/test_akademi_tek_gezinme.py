"""Tek gezinme (15.10.2026) — BBD Akademi paneli davranışı.

Kullanıcı: "bbd akademiye girince yan yana iki tane sol menü görünüyor." Panel
artık kendi sol menüsünü çizmez; üstte ana grup şeridi + haplar, açılışta ana
sayfa kutucukları. Burada panel Node'daki küçük DOM'da (`tools/a11y`) gerçekten
kurulur ve tıklanır:

* açılış ana sayfadır; kutucuktaki ekran bağlantısı TEK tıkta ekranı açar;
* koçluk hapları aynı koçluk ekranını paylaşır (tek kurulum), ekranın kendi
  sekme şeridi gizlidir, hap sekmeyi seçer; ekran içinden sekme değişince hap
  da değişir;
* eski derin bağlantılar yeni yerlerine gider: Ayarlar `{tab: 'sms'}` →
  İletişim › SMS, Çalışma planı `{tab: 'templates'}` → Şablonlar, eski adlar
  (`kontrol-kuyrugu`, `progress`) → yeni haplar/gruplar.

`node` yoksa atlanır.
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

COACH_READS = ["admin_getCoachQueue", "admin_listCoachReviews", "admin_listCoachingStudents",
               "admin_getCoachWeek", "admin_listCoaches", "admin_listCoachLibrary", "admin_listCoachBadges",
               "admin_listCheckins", "admin_listCoachMeetings", "admin_getSmsHealth", "admin_getDashboard",
               "admin_listFeatures", "admin_getSettings", "admin_listStudyPlanTemplates"]

ROUTES = {
    "GET /api/bbd_akademi/km/status?operations=1": {
        "connection": {"enabled": True, "hasKey": True},
        "permissions": ["bbd_akademi." + k for k in ["view", "academic", "curriculum", "resources", "attendance",
                                                     "exams", "finance", "books", "announcements", "requests",
                                                     "settings", "staff", "campus", "canteen", "learning",
                                                     "learning_setup", "evaluations", "privacy"]],
        "features": None,
        "operationList": [{"operationId": op, "allowed": True} for op in COACH_READS]},
    "GET /api/events/wait": {"items": [], "lastSeq": 3, "reset": False},
}

SCENARIO = r"""
export default async function ({ document, sleep, api, load, calls }) {
  globalThis.__apiHook = (method, path, options) => {
    if (path.startsWith('/api/events/wait?after')) return new Promise(() => {});
    if (path === '/api/bbd_akademi/km/call') {
      const body = options.body || {};
      if (body.path === '/v1/admin/dashboard') {
        return { ok: true, status: 200, headers: {}, data: { pendingRequests: { leave: 1, meeting: 0, etut: 2 },
          coaching: { openQuestions: 4, pendingReviews: 7 }, attendance: { sessionsTaken: 3, sessionsTotal: 5 } } };
      }
      return { ok: true, status: 200, data: { items: [], nextCursor: null }, headers: {} };
    }
    return undefined;
  };
  const ak = await load('./panels/bbd_akademi/index.js');
  const state = (root) => ({
    area: root.querySelector('.ak-area.on')?.textContent || null,
    pill: root.querySelector('.ak-pill.on')?.textContent || null,
    home: Boolean(root.querySelector('.ak-home') && !root.querySelector('.ak-home').hidden),
  });
  async function mount(payload) {
    const root = document.createElement('div');
    document.body.append(root);
    const cleanup = ak.mount(root, { panel: { id: 'bbd_akademi' }, api, payload, capability: () => null, open: () => {} });
    await sleep(120);
    return { root, cleanup };
  }
  const out = {};

  // 1) Açılış: ana sayfa; iki sol menü yok.
  const first = await mount(null);
  let root = first.root;
  out.start = state(root);
  out.leftMenus = root.querySelectorAll('.ak-nav, .ak-nav-item, .ak-nav-subitem').length;
  out.tiles = root.querySelectorAll('.ak-tile').length;
  out.featured = root.querySelector('.ak-tile-feature .ak-tile-open')?.textContent || null;
  out.coachStat = root.querySelector('.ak-tone-violet .ak-tile-stat')?.textContent || null;
  out.dashboardCalls = calls.filter((c) => c.path === '/api/bbd_akademi/km/call'
    && c.body?.path === '/v1/admin/dashboard').length;

  // 2) Kutucuktaki "Haftalık program" bağlantısı → Koçluk › Haftalık program (tek tık).
  root.querySelectorAll('.ak-tile-link').find((b) => b.textContent === 'Haftalık program').click();
  await sleep(150);
  out.week = state(root);
  out.coachTabsHidden = root.querySelectorAll('.ak-section .kit-tabs').filter((t) => t.hidden).length >= 1;

  // 3) Başka hap → aynı koçluk ekranı (tek kurulum), sekme değişir.
  root.querySelectorAll('.ak-pill').find((b) => b.textContent === 'Kontrol kuyruğu').click();
  await sleep(150);
  out.queue = state(root);
  out.coachSections = root.querySelectorAll('.ak-section').filter((s) => s.querySelector('.ak-coach-body')).length;

  // 4) Ekran içinden sekme değişimi (gizli şerit programla) → hap eşlenir.
  const bar = root.querySelectorAll('.ak-section .kit-tabs').find((t) => t.hidden);
  bar?.querySelectorAll('[role="tab"]').find((b) => b.textContent === 'Görüşmeler')?.click();
  await sleep(150);
  out.synced = state(root);

  // 5) "Ana sayfa" düğmesi geri getirir.
  root.querySelectorAll('.ak-area').find((b) => b.textContent === 'Ana sayfa').click();
  await sleep(60);
  out.back = state(root);
  first.cleanup?.();

  // 6) Eski derin bağlantılar.
  for (const [name, payload] of [['sms', { section: 'settings', tab: 'sms' }],
    ['planTemplates', { section: 'plan', tab: 'templates' }], ['oldQueue', { section: 'kontrol-kuyrugu' }],
    ['oldArea', { section: 'progress' }], ['coachStudent', { section: 'coaching', studentId: 'S1' }]]) {
    const view = await mount(payload);
    await sleep(150);
    out[name] = state(view.root);
    view.cleanup?.();
  }
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    try:
        data = run(SCENARIO, panels=("bbd_akademi",), routes=ROUTES)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], json.dumps(data["errors"], ensure_ascii=False)
    out: dict[str, Any] = data["result"]
    return out


def test_acilis_ana_sayfa_ve_tek_sol_menu(result: dict[str, Any]) -> None:
    assert result["start"] == {"area": "Ana sayfa", "pill": None, "home": True}
    assert result["leftMenus"] == 0, "panel kendi sol menüsünü çizmemeli"
    assert result["tiles"] == 10
    assert result["featured"] == "Koçluk"                 # koçluk öne çıkar (geniş kutucuk)
    assert result["coachStat"] == "4 soru · 7 kontrol"    # günün sayısı özet yükünden
    assert result["dashboardCalls"] == 1                   # ana sayfa tek özet çağrısı


def test_kutucuk_baglantisi_tek_tikta_ekrani_acar(result: dict[str, Any]) -> None:
    assert result["week"] == {"area": "Koçluk", "pill": "Haftalık program", "home": False}
    assert result["coachTabsHidden"] is True               # sekmeler hapta; ekranın şeridi gizli


def test_kocluk_haplari_tek_ekrani_paylasir(result: dict[str, Any]) -> None:
    assert result["queue"] == {"area": "Koçluk", "pill": "Kontrol kuyruğu", "home": False}
    assert result["coachSections"] == 1
    assert result["synced"]["pill"] == "Görüşmeler"
    assert result["back"]["home"] is True and result["back"]["area"] == "Ana sayfa"


def test_eski_derin_baglantilar_yeni_yerinde(result: dict[str, Any]) -> None:
    assert result["sms"] == {"area": "İletişim", "pill": "SMS", "home": False}
    assert result["planTemplates"] == {"area": "Ödev ve Deneme", "pill": "Şablonlar", "home": False}
    assert result["oldQueue"] == {"area": "Koçluk", "pill": "Kontrol kuyruğu", "home": False}
    assert result["oldArea"] == {"area": "Ödev ve Deneme", "pill": "Ödevler", "home": False}
    assert result["coachStudent"] == {"area": "Koçluk", "pill": "Koçluk öğrencileri", "home": False}
