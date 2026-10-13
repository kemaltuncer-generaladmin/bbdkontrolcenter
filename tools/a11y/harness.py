"""Panel JS'ini Node'da çalıştırma düzeneği (erişilebilirlik ve davranış testleri).

Kabuğun ÇALIŞMA ZAMANI yerleşimi kurulur — panel dosyaları
`shell/panels/<modül>/` altına, kit `shell/ui-kit/` altına kopyalanır
(`tools/build-ui-registry.py` ile aynı), böylece panellerin
`../../ui-kit/kit.js` importları çözülür. Senaryo bir ES modülüdür:

    export default async function ({ document, audit, contrast, sleep, api, load }) { … return veri; }

`api` taklit çekirdektir: `routes` sözlüğü (`"GET /api/x"` → yanıt ya da
`(yol, seçenek) => yanıt`); bilinmeyen yol 404 fırlatır. Dönen değer JSON
olarak Python'a gelir. `node` yoksa `NodeMissing` yükselir (test atlar).

Bağımlılık yok (K11): DOM `dom.mjs`'tir, denetim `audit.mjs`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SHELL = ROOT / "apps" / "desktop" / "shell"
HERE = Path(__file__).resolve().parent


class NodeMissing(RuntimeError):
    pass


RUNNER = r"""
import { install, parseHTML } from './a11y/dom.mjs';
import { audit, contrast, cssAudit, tokens, accessibleName } from './a11y/audit.mjs';
import { readFileSync } from 'node:fs';
const document = install();
const routes = JSON.parse(readFileSync(new URL('./routes.json', import.meta.url), 'utf8'));
const calls = [];
async function api(path, options = {}) {
  const method = (options.method || 'GET').toUpperCase();
  calls.push({ method, path, body: options.body ?? null });
  const clean = String(path);
  const keys = [`${method} ${clean}`, `${method} ${clean.split('?')[0]}`];
  for (const key of keys) {
    if (key in routes) {
      const value = routes[key];
      if (value && value.__status) {
        const error = new Error(value.message || `İstek başarısız (${value.__status}).`);
        error.status = value.__status;
        throw error;
      }
      return JSON.parse(JSON.stringify(value));
    }
  }
  // önek eşlemesi: "GET /api/x/*"
  for (const [key, value] of Object.entries(routes)) {
    if (key.endsWith('*') && `${method} ${clean}`.startsWith(key.slice(0, -1))) return JSON.parse(JSON.stringify(value));
  }
  const error = new Error(`taklit yol yok: ${method} ${clean}`);
  error.status = 404;
  throw error;
}
const sleep = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
const load = (path) => import(new URL(path, import.meta.url).href);
const scenario = (await import('./scenario.mjs')).default;
const result = await scenario({ document, parseHTML, audit, contrast, cssAudit, tokens, accessibleName, sleep, api, load, calls,
  css: (path) => readFileSync(new URL(path, import.meta.url), 'utf8') });
const errors = (globalThis.__domErrors || []).map((e) => String(e?.stack || e).split('\n').slice(0, 3).join(' | '));
process.stdout.write(JSON.stringify({ result, errors }));
process.exit(0);
"""


def run(scenario: str, *, panels: tuple[str, ...] = (), routes: dict[str, Any] | None = None,
        timeout: float = 60) -> dict[str, Any]:
    """Senaryoyu çalıştırır; `{"result": …, "errors": [olay dinleyicisi hataları]}` döner."""
    node = shutil.which("node")
    if node is None:
        raise NodeMissing("node kurulu değil")
    with tempfile.TemporaryDirectory(prefix="km-a11y-") as folder:
        base = Path(folder)
        (base / "package.json").write_text('{"type": "module"}', encoding="utf-8")
        shutil.copytree(SHELL / "ui-kit", base / "ui-kit")
        for name in ("ui-kernel.js", "global-search.js", "icons.js", "style.css", "index.html"):
            if (SHELL / name).exists():
                shutil.copy(SHELL / name, base / name)
        (base / "a11y").mkdir()
        for name in ("dom.mjs", "audit.mjs"):
            shutil.copy(HERE / name, base / "a11y" / name)
        for module in panels:
            source = ROOT / "modules" / module / "ui" / "panel"
            shutil.copytree(source, base / "panels" / module)
        (base / "routes.json").write_text(json.dumps(routes or {}, ensure_ascii=False), encoding="utf-8")
        (base / "scenario.mjs").write_text(scenario, encoding="utf-8")
        (base / "run.mjs").write_text(RUNNER, encoding="utf-8")
        done = subprocess.run([node, str(base / "run.mjs")], capture_output=True, text=True,
                              check=False, timeout=timeout, cwd=base)
        if done.returncode != 0:
            raise AssertionError(f"node çıkış {done.returncode}\n{done.stderr[-4000:]}")
        data: dict[str, Any] = json.loads(done.stdout)
        return data
