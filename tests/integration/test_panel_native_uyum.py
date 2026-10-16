"""Panellerin masaüstü (native) uyumları — 63-duzeltici, ADR 0032 kalanları.

* Ctrl/⌘+P: rapor araç çubuğu, PDF önizleme ve Akademi belge düğmelerindeki "Yazdır"
  `data-command="print"` taşır; kabuğun etiketle bulma yedeği yıkıcı ya da başka
  komutlu düğmeye basmaz ("Seçilenlerin kodunu sıfırla ve yazdır").
* Dosya kaydı yerel "Farklı kaydet"ten (`kit.saveFile` → `native_save_file`):
  Akademi `saveBlob`, kantin ürünleri CSV.
* El imleci yok: panel CSS'inde `cursor: pointer`, JS'te `style.cursor = 'pointer'` yok.
"""

from __future__ import annotations

import base64
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

SHELL = ROOT / "apps" / "desktop" / "shell"


def node_run(scenario: str, panels: tuple[str, ...] = ()) -> Any:
    try:
        data = run(scenario, panels=panels, timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    return data["result"]


SCENARIO = r"""
export default async function ({ document, load, sleep }) {
  const invoked = [];
  window.__TAURI__ = { core: { invoke: async (cmd, args) => { invoked.push([cmd, args]); return '/Users/x/Masaüstü/a.csv'; } } };
  const { reportToolbar } = await load('./ui-kit/report.js');
  const root = document.createElement('div');
  document.body.append(root);
  const bar = reportToolbar({ root, api: async () => ({}), toast: () => {}, produce: async () => ({}), title: 'Cari' });
  const { documentButtons } = await load('./panels/bbd_akademi/documents.js');
  const docs = documentButtons({ root, toast: () => {}, api: {}, coreApi: async () => ({}) },
    { build: async () => ({}), title: 'Makbuz' });
  const printOf = (node) => node.querySelectorAll('button').filter((b) => b.dataset.command === 'print')
    .map((b) => b.textContent);
  const { saveBlob } = await load('./panels/bbd_akademi/api.js');
  const saved = await saveBlob({ filename: 'yoklama.csv', blob: new Blob(['a;b']) });
  return { toolbar: printOf(bar.node), docs: printOf(docs), saved, invoked: invoked.map(([cmd, args]) => [cmd, args.name, args.base64]) };
}
"""


def test_yazdir_komutu_ve_yerel_kaydet() -> None:
    out = node_run(SCENARIO, panels=("bbd_akademi",))
    assert out["toolbar"] == ["Yazdır"]
    assert out["docs"] == ["Yazdır"]
    assert out["saved"] == "/Users/x/Masaüstü/a.csv"
    assert out["invoked"] == [["native_save_file", "yoklama.csv", base64.b64encode(b"a;b").decode()]]


def test_yazdir_yedegi_yikici_dugmeye_basmaz() -> None:
    app = (SHELL / "app.js").read_text(encoding="utf-8")
    body = app[app.index("function printActive()"):app.index("function exportActive()")]
    assert "[data-command=\"print\"]" in body
    assert "!node.classList.contains('kit-btn-danger')" in body and "!node.dataset.command" in body


def test_kantin_urunleri_csv_yerel_kaydet() -> None:
    panel = (ROOT / "modules" / "bbd_canteen_products" / "ui" / "panel" / "index.js").read_text(encoding="utf-8")
    body = panel[panel.index("async function exportCsv()"):panel.index("// ------------------------------------------------------------------ mount")]
    assert "saveFile({" in body and "link.download" not in body
    assert "/^[=+\\-@]/" in body   # formül enjeksiyonu önekli


def test_el_imleci_yok() -> None:
    offenders = []
    for base in (ROOT / "modules", SHELL / "core-panels", SHELL / "ui-kit"):
        for path in base.rglob("*"):
            if path.suffix not in {".css", ".js"} or "node_modules" in path.parts:
                continue
            text = re.sub(r"/\*[\s\S]*?\*/", "", path.read_text(encoding="utf-8"))
            if re.search(r"cursor:\s*pointer|\.cursor\s*=\s*['\"]pointer", text):
                offenders.append(str(path.relative_to(ROOT)))
    # F-DK (ders kaydı) öteki liderin alanı — bu dalgada dokunulmadı.
    offenders = [item for item in offenders if "/recordings/" not in item]
    assert offenders == [], offenders
