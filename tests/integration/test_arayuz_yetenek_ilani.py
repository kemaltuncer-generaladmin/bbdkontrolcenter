"""Arayüz yeteneği İLANI — panelin verdiği her yetenek manifestte yazılı olmalı (63-duzeltici).

CANLI HATA (16.10.2026): BBD Akademi öğrenci kartındaki "Aktifleştir" düğmesi hiç
çizilmiyordu. `bbd_students` paneli `capabilities()` içinde `bbd_students.reactivate`
veriyordu ama `module.yaml → provides` listesinde yoktu; kabuk (`ui-kernel.js →
ensureProviders`) ilan edilmeyen yeteneği SESSİZCE atlar ("manifestte ilan edilmemiş,
atlandı" yalnız konsola düşer). Akademi `capability('bbd_students.reactivate')` →
null aldı ve düğme yerine "yetki gerekir" yazdı. Var olan test yeteneği elle
taklit ettiği için kabuğun bu süzgecini hiç görmedi.

Aynı desende iki açık daha vardı: `bbd_akademi.deletionDocument` (toplu silme
sonucunun Önizle / Yazdır düğmeleri çizilmiyordu) ve `store.customer.card`
(manifestte vardı, hiçbir panel vermiyordu — Talepler çekmecesinin "Müşteri
künyesi" bölümü hiç çizilmiyordu).

Sınananlar (Node, `tools/a11y/harness.py`; `node` yoksa atlanır):
* `capabilities()` dışa vuran HER panelin verdiği her ad kendi manifestinde ilanlı.
* Panellerin `capability('…')` / `kmRead('…')` ile istediği her ad bir panelden
  gerçekten gelir (bilinçli istisna aşağıda gerekçesiyle).
* Uçtan uca, GERÇEK kabukla: manifestten kurulan kayıt + `ui-kernel.mountPanel`
  → Akademi kartında "Aktifleştir" çizilir, onayla `POST …/reactivate` gider.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

MODULES = ROOT / "modules"

# İstenen ama hiçbir panelin vermediği yetenekler — YALNIZ tüketenin yedek yolu
# varsa ve davranış eksik değilse buraya yazılır.
KNOWN_UNPROVIDED = {
    # Mağaza panosu: kritik stok satırı ürün künyesini "yerinde" açmak için
    # bakar; yoksa (her zaman) Ürünler ekranına gider. Davranış eksik değil.
    "store.product.card",
}

REQUEST = re.compile(r"""(?:capability\??\.?|kmRead)\(\s*['"]([a-z_]+(?:\.[A-Za-z_]+)+)['"]""")


def manifest(module: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((MODULES / module / "module.yaml").read_text(encoding="utf-8"))
    return data


def provides(module: str) -> list[str]:
    return [entry["capability"] for entry in manifest(module).get("provides") or []]


def panel_dir(module: str) -> Path | None:
    entry = (manifest(module).get("ui") or {}).get("entry")
    return (MODULES / module / entry).parent if entry else None


def provider_modules() -> list[str]:
    found = []
    for path in sorted(MODULES.glob("*/module.yaml")):
        module = path.parent.name
        folder = panel_dir(module)
        if folder and (folder / "index.js").exists() and re.search(
                r"export\s+(?:async\s+)?function\s+capabilities\b",
                (folder / "index.js").read_text(encoding="utf-8")):
            found.append(module)
    return found


def node_run(scenario: str, panels: tuple[str, ...]) -> Any:
    try:
        data = run(scenario, panels=panels, timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    return data["result"]


def declared_by_panels() -> dict[str, list[str]]:
    modules = provider_modules()
    scenario = """
export default async function ({ load }) {
  const out = {};
  const ctx = { api: async () => ({}), capability: () => null, open: () => {} };
  for (const id of MODULES_JSON) {
    const module = await load('./panels/' + id + '/index.js');
    out[id] = Object.keys(module.capabilities(ctx) || {});
  }
  return out;
}
""".replace("MODULES_JSON", json.dumps(modules))
    result: dict[str, list[str]] = node_run(scenario, tuple(modules))
    return result


def test_panel_yetenekleri_manifestte_ilanli() -> None:
    declared = declared_by_panels()
    assert "bbd_students" in declared and "bbd_akademi" in declared
    missing = {module: sorted(set(names) - set(provides(module)))
               for module, names in declared.items() if set(names) - set(provides(module))}
    assert missing == {}, f"panel veriyor, manifest ilan etmiyor (kabuk atlar): {missing}"


def test_istenen_her_yetenek_bir_panelden_gelir() -> None:
    given = {name for names in declared_by_panels().values() for name in names}
    wanted: dict[str, set[str]] = {}
    for path in sorted(MODULES.glob("*/ui/**/*.js")):
        for name in REQUEST.findall(path.read_text(encoding="utf-8")):
            wanted.setdefault(name, set()).add(str(path.relative_to(ROOT)))
    orphan = {name: sorted(where) for name, where in wanted.items()
              if name not in given and name not in KNOWN_UNPROVIDED}
    assert orphan == {}, f"isteniyor ama hiçbir panel vermiyor (her zaman null): {orphan}"
    assert "bbd_students.reactivate" in wanted


# --------------------------------------------------------- uçtan uca, gerçek kabuk

E2E = r"""
import { writeFileSync } from 'node:fs';
export default async function ({ load, sleep, document }) {
  const calls = [];
  window.__TAURI__ = { core: { invoke: async (cmd, args) => {
    if (cmd !== 'core_request') return null;
    calls.push([args.method, args.path, args.body ? JSON.parse(args.body) : null]);
    if (args.path === '/auth/me') {
      return { status: 200, body: JSON.stringify({ permissions: ['bbd_students.view', 'bbd_students.lifecycle'] }) };
    }
    if (args.method === 'POST' && args.path === '/api/bbd_students/students/STU-1/reactivate') {
      return { status: 200, body: JSON.stringify({ status: 'active', classesRestored: 1, canteenPending: 0 }) };
    }
    return { status: 200, body: '{}' };
  } } };
  writeFileSync(new URL('./probe.js', import.meta.url),
    'export function mount(root, ctx) { globalThis.__probe = ctx; return () => {}; }');
  const kernel = await load('./ui-kernel.js');
  const registry = { panels: [...REGISTRY, { id: 'probe', entry: 'probe.js', provides: [] }] };
  const host = document.createElement('div');
  document.body.append(host);
  await kernel.mountPanel({ id: 'probe', entry: 'probe.js' }, host, registry);
  const capability = globalThis.__probe.capability;
  const out = { resolved: Boolean(capability('bbd_students.reactivate')),
    deletionDocument: Boolean(capability('bbd_akademi.deletionDocument')),
    customerCard: Boolean(capability('store.customer.card')), toasts: [] };

  const { renderHeader } = await load('./panels/bbd_akademi/students/students-detail.js');
  const root = document.createElement('div');
  document.body.append(root);
  const app = { root, toast: (text, tone) => out.toasts.push([text, tone]), go: () => {}, openPanel: () => {},
    capability, has: () => true, can: () => true, enabled: () => true, api: { can: () => true } };
  const data = { student: { id: 's1', externalRef: 'STU-1', displayName: 'Elif Kaya', status: 'inactive',
    gradeLevel: 9, classGroup: null }, statusReason: 'left', statusSince: '2026-10-12T09:00:00Z', phoneIssues: [] };
  root.append(...[].concat(renderHeader({ app, recordable: true, refreshSoon: () => {} }, data)));
  await sleep(30);
  const box = root.querySelector('.ak-stu-inactive');
  out.text = box?.textContent || '';
  const button = box.querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir');
  out.visible = Boolean(button) && !button.hidden;
  if (out.visible) {
    button.click();
    await sleep(20);
    const dialog = document.querySelector('.st-lc-dialog');
    out.dialog = Boolean(dialog);
    dialog?.querySelectorAll('button').find((b) => b.textContent === 'Aktifleştir')?.click();
    await sleep(40);
  }
  out.posts = calls.filter((c) => c[0] === 'POST').map((c) => c[1]);
  return out;
}
"""


def test_akademi_kartinda_aktiflestir_gercek_kabukla() -> None:
    modules = ("bbd_students", "bbd_akademi", "store_customers")
    registry = [{"id": module, "entry": f"panels/{module}/index.js", "provides": provides(module)}
                for module in modules]
    out = node_run(E2E.replace("REGISTRY", json.dumps(registry, ensure_ascii=False)), modules)
    assert out["resolved"] is True, "kabuk bbd_students.reactivate yeteneğini kayda almadı"
    assert out["deletionDocument"] is True and out["customerCard"] is True
    assert "yetkisi gerekir" not in out["text"]
    assert out["visible"] is True and out["dialog"] is True
    assert out["posts"] == ["/api/bbd_students/students/STU-1/reactivate"]
    assert out["toasts"] and out["toasts"][0][1] == "good"


STORE = r"""
export default async function ({ load }) {
  const paths = [];
  const reply = { '/api/store_customers/customers/12': { ok: true, customer: { id: 12, name: 'Ayşe',
    groupName: 'Genel', phone: '05550000000', orders: 3, spend: 1250 } } };
  const api = async (path) => { paths.push(path); if (reply[path]) return reply[path]; throw new Error('yok'); };
  const caps = (await load('./panels/store_customers/index.js')).capabilities({ api, capability: () => null, open: () => {} });
  const ok = await caps['store.customer.card'](12);
  const bad = await caps['store.customer.card'](99);
  return { paths, ok, bad };
}
"""


def test_magaza_musteri_kunyesi_yetenegi() -> None:
    out = node_run(STORE, ("store_customers",))
    assert out["paths"] == ["/api/store_customers/customers/12", "/api/store_customers/customers/99"]
    assert out["ok"]["ok"] is True
    customer = out["ok"]["customer"]
    assert (customer["groupName"], customer["phone"], customer["orderCount"], customer["totalSpent"]) \
        == ("Genel", "05550000000", 3, 1250)
    assert out["bad"]["ok"] is False
