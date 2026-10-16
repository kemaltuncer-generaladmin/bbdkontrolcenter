"""Müfredat atamada yetki + hedefin müfredatı (Akademi README §14 madde 102, iş 56 `mufredat-oto`).

Kullanıcı (16.10.2026): "müfredat atamada yetki hatası var … müfredatlar otomatik atansın" · "soru çözümünde ders
müfredatları atanmıyor; koç program verirken tüm görev türlerinde müfredat görülmeli".

- İzin eşlemesi: öğrenci / sınıf müfredat ATAMASI (`curriculum.assign`) akademik iştir → `bbd_akademi.academic`
  (KM personeli de atar); katalog düzenleme (`curriculum.manage`) yönetici iznidir.
- `task-form-logic.js`: çok öğrencide kapsam metni ("hepsinde" / "1/2 öğrencide" / "sınıf seviyesinden") ve sıralama.
- `curriculum-set.js`: kaynak etiketi "Otomatik", otomatik müfredatın dayanağı ("sınıftan: 11-SAY").
- Plan ataması (DOM): konu seçici HEDEFİN müfredatını kapsam etiketiyle gösterir (soru çözümü türünde de).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.permission_map import ACADEMIC, CURRICULUM, VIEW, km_permissions

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run


def _op(operation_id: str) -> Any:
    return next(op for op in OPERATIONS if op.operation_id == operation_id)


def test_mufredat_atamasi_akademik_is_katalog_yonetici() -> None:
    for operation_id in ("admin_changeStudentCurricula", "admin_resetStudentCurricula", "admin_changeClassGroupCurricula"):
        assert km_permissions(_op(operation_id)) == (ACADEMIC,), operation_id
    for operation_id in ("admin_createProgram", "admin_createProgramOverride", "admin_bulkProgramOverrides"):
        assert km_permissions(_op(operation_id)) == (CURRICULUM,), operation_id
    # öğrencinin müfredatı (konu seçici) görüntüleme izniyle
    assert km_permissions(_op("students_getCurriculum")) == (VIEW,)


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


LOGIC = r"""
export default async function ({ load }) {
  const m = await load('./panels/bbd_akademi/shared/task-form-logic.js');
  const cs = await load('./panels/bbd_akademi/shared/curriculum-set.js');
  const items = [
    { id: 'a', recommended: true, targetStudentCount: 1 },
    { id: 'b', recommended: false, targetStudentCount: null },
    { id: 'c', recommended: true, targetStudentCount: 2 },
    { id: 'd', recommended: true, targetStudentCount: 0 },
    { id: 'e', recommended: true, targetStudentCount: 2 },
  ];
  return {
    texts: [m.coverageText(2, 2), m.coverageText(1, 2), m.coverageText(0, 2), m.coverageText(1, 1),
      m.coverageText(null, 2), m.coverageText(3, 0)],
    order: m.targetPrograms(items).map((p) => p.id),
    source: cs.sourceText({ kind: 'default' }),
    basis: [cs.basisText({ gradeLevel: 11, track: 'SAY', examTarget: 'YKS', source: 'class', classGroup: { name: '11-SAY' } }),
      cs.basisText({ gradeLevel: 12, track: null, examTarget: 'YKS', source: 'km' }),
      cs.basisText({ gradeLevel: null, track: null, examTarget: null, source: null }),
      cs.basisText({ gradeLevel: 'graduate', track: null, examTarget: 'YKS', source: 'group',
        classGroup: { name: 'MEZUN GRUBU' }, guessed: true })],
  };
}
"""


def test_kapsam_metni_ve_otomatik_etiketi() -> None:
    out = _run(LOGIC)
    assert out["texts"] == ["hepsinde", "1/2 öğrencide", "sınıf seviyesinden", "", "", ""]
    assert out["order"] == ["c", "e", "a", "d"]  # hepsinde olanlar önce, eşitlikte sunucu sırası
    assert out["source"] == "Otomatik"
    assert out["basis"][0].endswith("(sınıftan: 11-SAY)") and "YKS" in out["basis"][0]
    assert "sınıftan" not in out["basis"][1]
    assert out["basis"][2].startswith("seviye girilmemiş — otomatik müfredat yok")
    assert out["basis"][3].endswith("YKS (gruptan: MEZUN GRUBU · addan tahmin)")


PLAN = r"""
const routes = {};
const sent = [];
globalThis.__apiHook = (method, path, options) => {
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: true, status: 200, data: { items: [], nextCursor: null }, headers: {} };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  return { ok: true, status: 200, data: value, headers: {} };
};
const byText = (root, selector, text) => root.querySelectorAll(selector).find((node) => node.textContent.trim() === text);
const click = (node) => { if (!node) throw new Error('düğme yok'); node.click(); };
const type = (node, value) => { node.value = value; node.dispatchEvent(new Event('input', { bubbles: true })); };

export default async function ({ document, audit, sleep, api, load }) {
  const prog = (id, title, grade, extra) => ({ id, kind: 'school', subject: { id: 'MAT', name: 'Matematik' }, title,
    grade, levelKey: `lise-${grade}`, levelLabel: `${grade}. sınıf`, levelOrder: grade * 10, ...extra });
  routes['GET /v1/admin/curricula/programs'] = (body, payload) => ({
    items: [prog('lise-11-matematik', 'Matematik', 11, { recommended: true, targetStudentCount: 2 }),
      prog('lise-12-matematik', 'Matematik', 12, { recommended: false, targetStudentCount: null }),
      prog('tyt-matematik', 'TYT Matematik', 12, { recommended: true, targetStudentCount: 1 })],
    target: payload.query && payload.query.classGroupIds ? { studentCount: 2, unknownLevelCount: 0, levels: [] } : null,
  });
  routes['GET /v1/admin/curricula/programs/lise-11-matematik'] = { id: 'lise-11-matematik', units: [{ id: 'u1',
    title: 'Fonksiyonlar', order: 1, topics: [{ id: 't-fonk', title: 'Fonksiyon kavramı', order: 1 }] }] };
  routes['GET /v1/admin/curricula/programs/tyt-matematik'] = { id: 'tyt-matematik', units: [{ id: 'u2',
    title: 'Temel kavramlar', order: 1, topics: [{ id: 't-fonk-tyt', title: 'Fonksiyon soruları', order: 1 }] }] };
  routes['GET /v1/admin/curricula/programs/lise-12-matematik'] = { id: 'lise-12-matematik', units: [{ id: 'u3',
    title: 'Türev', order: 1, topics: [{ id: 't-turev', title: 'Fonksiyon türevi', order: 1 }] }] };
  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const { toaster } = await load('./ui-kit/kit.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const toast = toaster(root);
  const app = { root, api: client, status: null, toast: (text, tone) => toast(text, tone),
    has: () => true, can: (id) => client.can(id), go: () => {}, openPanel: () => {}, capability: () => null,
    feature: () => false, enabled: () => true, onDispose: () => {},
    lookups: { classGroups: async () => [{ id: 'c1', name: '11-SAY' }], studentGroups: async () => [], terms: async () => [],
      subjects: async () => [{ id: 'MAT', name: 'Matematik' }, { id: 'FIZ', name: 'Fizik' }] } };
  const { section } = await load('./panels/bbd_akademi/work/plan.js');
  const view = section(app);
  root.append(view.node);
  view.shown?.();
  await sleep(60);
  const out = {};
  click(byText(root, 'button', 'Yeni atama'));
  await sleep(60);
  const dialog = root.querySelector('.kit-dialog');
  click(byText(dialog, 'button', '11-SAY'));
  click(byText(dialog, 'button', '+ Görev ekle'));
  await sleep(30);
  const editor = () => dialog.querySelector('.ak-plan-editor');
  click(editor().querySelector('[data-type="question_solving"]'));
  const search = editor().querySelector('input[aria-label="Konu ara"]');
  search.dispatchEvent(new Event('focus'));
  type(search, 'fonksiyon');
  await sleep(300);
  out.results = editor().querySelectorAll('.ak-tf-result').map((node) => node.textContent);
  out.note = editor().querySelector('.ak-tf-curriculum-note').textContent;
  out.groups = editor().querySelectorAll('select[aria-label="Ders"] optgroup').map((node) => node.label);
  out.query = sent.filter((c) => c.method === 'GET' && c.path === '/v1/admin/curricula/programs').map((c) => c.query);
  out.a11y = audit(dialog);
  return out;
}
"""


def test_plan_atamasi_konu_secici_hedefin_mufredati() -> None:
    out = _run(PLAN)
    assert any(q and q.get("classGroupIds") == "c1" for q in out["query"]), out["query"]
    results = out["results"]
    assert any("Fonksiyon kavramı" in r and "hepsinde" in r for r in results), results
    assert any("Fonksiyon soruları" in r and "1/2 öğrencide" in r for r in results), results
    assert not any("Fonksiyon türevi" in r for r in results)  # hedefte olmayan program gelmez
    assert out["note"].startswith("Hedefin müfredatı: 2 öğrenci · 1 program hepsinde")
    assert out["groups"][0] == "Hedefin dersleri"
    assert out["a11y"] == []
