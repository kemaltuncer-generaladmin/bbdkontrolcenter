"""Tek giriş noktası ve küresel arama (0.5.3, ADR 0031) — kabuk.

* `ui.nav.replacedBy`: işi başka ekrana taşınan ekran, o yeteneği veren
  görünür bir ekran varsa menüde ÇİZİLMEZ ama kayıtta kalır; yoksa menüde
  kalır (K7). Kabuk modül adı bilmez (K1).
* Küresel arama (`global-search.js`): `.search` yetenekleri + ekran adları,
  combobox/listbox deseni, ↑/↓/Enter/Esc, eski yanıt yenisini ezmez, patlayan
  sağlayıcı ötekileri düşürmez.
* Kabuk `index.html` ve `style.css` erişilebilirlik denetimi (axe benzeri,
  `tools/a11y`): etiketler, adlar, ARIA bağları, atlama bağlantısı, karşıtlık.

Node ile koşar (`tools/a11y/harness.py`); `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run  # noqa: E402

SHELL = ROOT / "apps" / "desktop" / "shell"


def node_run(scenario: str, **kwargs: object) -> dict:
    try:
        return run(scenario, **kwargs)  # type: ignore[arg-type]
    except NodeMissing:
        pytest.skip("node kurulu değil")


MENU = r"""
export default async function ({ load }) {
  const k = await load('./ui-kernel.js');
  const panel = (id, extra = {}) => ({ id, title: id, group: 'BBD', order: 1, provides: [], ...extra });
  const students = panel('bbd_students', { replacedBy: 'bbd_akademi.students',
    provides: ['bbd_students.manager', 'bbd_students.record'] });
  const akademi = panel('bbd_akademi', { provides: ['bbd_akademi.students'] });
  const self = panel('kendi', { replacedBy: 'kendi.x', provides: ['kendi.x'] });
  const ids = (list) => list.map((p) => p.id);
  return {
    both: ids(k.menuPanels({ panels: [students, akademi, panel('bell')] })),
    noAkademi: ids(k.menuPanels({ panels: [students, panel('bell')] })),
    selfProvided: ids(k.menuPanels({ panels: [self] })),
    empty: ids(k.menuPanels(null)),
  };
}
"""


def test_replaced_by_menuden_duser_kayitta_kalir() -> None:
    result = node_run(MENU)["result"]
    assert result["both"] == ["bbd_akademi", "bell"]
    # Akademi görünmüyorsa (modül kapalı / izin yok) öğrenci yönetimi menüde kalır (K7).
    assert result["noAkademi"] == ["bbd_students", "bell"]
    # Kendi verdiği yetenekle kendini gizleyemez.
    assert result["selfProvided"] == ["kendi"]
    assert result["empty"] == []


def test_manifestler_tek_giris_noktasini_ilan_eder() -> None:
    students = yaml.safe_load((ROOT / "modules/bbd_students/module.yaml").read_text(encoding="utf-8"))
    akademi = yaml.safe_load((ROOT / "modules/bbd_akademi/module.yaml").read_text(encoding="utf-8"))
    assert students["ui"]["nav"]["replacedBy"] == "bbd_akademi.students"
    assert "bbd_akademi.students" in {item["capability"] for item in akademi["provides"]}
    assert {"bbd_students.search", "bbd_students.open"} <= {item["capability"] for item in students["provides"]}
    # Akademi menüsü öğrenci yönetimi iznini taşıyana da görünür (muhasebe, org_staff).
    assert akademi["ui"]["nav"]["requires"] == ["bbd_akademi.view", "bbd_students.view"]


def test_kabuk_modul_adi_bilmez() -> None:
    """K1: kabuk dosyalarında modül adı geçmez (yorumlar dahil değil, kod)."""
    for name in ("ui-kernel.js", "global-search.js", "app.js"):
        code = "\n".join(line for line in (SHELL / name).read_text(encoding="utf-8").splitlines()
                         if not line.strip().startswith(("//", "*", "/*")))
        for module in ("bbd_students", "bbd_akademi", "bbd_tablet"):
            assert module not in code, f"{name}: {module}"


SEARCH = r"""
export default async function ({ document, parseHTML, audit, sleep, load, css }) {
  parseHTML(document, css('./index.html'));
  const { createGlobalSearch } = await load('./global-search.js');
  const fold = (t) => t.toLocaleLowerCase('tr').replaceAll('ı', 'i').replaceAll('ö', 'o').replaceAll('ğ', 'g');
  const opened = [];
  let slow = null;
  const providers = [
    { name: 'a.search', label: 'Öğrenciler', search: async (q) => {
      if (q === 'yavaş') { await new Promise((r) => { slow = r; }); return [{ id: 'old', title: 'ESKİ', open() { opened.push('old'); } }]; }
      if (!q.includes('kaya')) return [];
      return [{ id: 'S1', title: 'Elif Kaya', detail: '9-A', badge: 'Öğrenci', open() { opened.push('S1'); } },
        { id: 'S2', title: 'Ali Kaya', detail: '10-B', open() { opened.push('S2'); } }];
    } },
    { name: 'b.search', label: 'Bozuk', search: async () => { throw new Error('düştü'); } },
  ];
  const input = document.getElementById('gsearch-input');
  const search = createGlobalSearch({ input, popup: document.getElementById('gsearch-pop'),
    list: document.getElementById('gsearch-list'), status: document.getElementById('gsearch-status'), fold,
    screens: () => [{ id: 'bell', title: 'Zil Sistemi', group: 'BBD' }, { id: 'kaya', title: 'Kayaklar', group: 'X' }],
    providers: async () => providers, openScreen: (id) => opened.push(`screen:${id}`) });
  const out = {};
  out.disabledBeforeLogin = input.disabled;
  search.enable();
  search.focus();
  out.focused = document.activeElement === input;
  await search.run('kaya');
  out.options = search.options().map((o) => o.title);
  out.expanded = input.getAttribute('aria-expanded');
  out.active = input.getAttribute('aria-activedescendant');
  out.status = document.getElementById('gsearch-status').textContent;
  const key = (k) => input.dispatchEvent(new KeyboardEvent('keydown', { key: k }));
  key('ArrowDown');
  out.afterDown = input.getAttribute('aria-activedescendant');
  out.selected = document.querySelectorAll('[aria-selected="true"]').map((n) => n.textContent);
  out.violationsOpen = audit(document.body);
  key('Enter');
  out.opened = [...opened];
  out.closed = input.getAttribute('aria-expanded');
  out.cleared = input.value;
  // Esc: önce kapatır, sonra boşaltır.
  search.focus();
  input.value = 'kaya';
  await search.run('kaya');
  key('Escape');
  out.escClosed = [input.getAttribute('aria-expanded'), input.value];
  // Eski yanıt yenisini ezmez.
  const pending = search.run('yavaş');
  await sleep(5);
  await search.run('kaya');
  slow?.();
  await pending;
  out.raceOptions = search.options().map((o) => o.title);
  // Ekran sonucu ekranı açar.
  await search.run('zil');
  key('Enter');
  out.screenOpened = opened[opened.length - 1];
  search.reset();
  out.afterReset = [input.disabled, input.value, document.getElementById('gsearch-list').childElementCount];
  return out;
}
"""


def test_kuresel_arama_combobox_akisi() -> None:
    result = node_run(SEARCH)
    r = result["result"]
    assert result["errors"] == []
    assert r["disabledBeforeLogin"] is True
    assert r["focused"] is True
    # Sağlayıcı sonuçları önce, ekranlar sonra; patlayan sağlayıcı ötekileri düşürmez.
    assert r["options"] == ["Elif Kaya", "Ali Kaya", "Kayaklar"]
    assert r["expanded"] == "true" and r["active"] == "gsearch-opt-0"
    assert "3 sonuç" in r["status"]
    assert r["afterDown"] == "gsearch-opt-1"
    assert len(r["selected"]) == 1 and "Ali Kaya" in r["selected"][0]
    assert r["violationsOpen"] == []
    assert r["opened"] == ["S2"] and r["closed"] == "false" and r["cleared"] == ""
    assert r["escClosed"] == ["false", "kaya"]
    assert r["raceOptions"] == ["Elif Kaya", "Ali Kaya", "Kayaklar"]
    assert r["screenOpened"] == "screen:bell"
    assert r["afterReset"] == [True, "", 0]


SHELL_AUDIT = r"""
export default async function ({ document, parseHTML, audit, contrast, cssAudit, tokens, css }) {
  parseHTML(document, css('./index.html'));
  document.getElementById('workspace').hidden = false;
  document.getElementById('login').hidden = false;
  const style = css('./style.css');
  const t = tokens(style);
  const pairs = {
    'panel metni / kâğıt': contrast(t['--paper-ink'], t['--paper']),
    'panel ikincil metin / kâğıt': contrast(t['--paper-soft'], t['--paper']),
    'menü metni / kenar çubuğu': contrast(t['--ink-soft'], t['--sb-bg']),
    'menü soluk metin / kenar çubuğu': contrast(t['--ink-faint'], t['--sb-bg']),
    'ana metin / zemin': contrast(t['--ink'], t['--bg']),
    'odak (açık zemin)': contrast(t['--accent-strong'], t['--paper']),
    'atlama bağlantısı': contrast('#ffffff', t['--accent-strong']),
    'hata metni / zemin': contrast(t['--danger'], t['--bg']),
  };
  return { violations: audit(document.documentElement), pairs, css: cssAudit(style, { name: 'style.css' }),
    skip: document.querySelector('.skip-link')?.getAttribute('href'),
    bodyTarget: document.getElementById('panel-body')?.getAttribute('tabindex') };
}
"""


def test_kabuk_erisilebilirlik_denetimi() -> None:
    data = node_run(SHELL_AUDIT)
    r = data["result"]
    assert r["violations"] == [], r["violations"]
    assert r["css"] == [], r["css"]
    low = {name: round(value, 2) for name, value in r["pairs"].items() if value < 4.5}
    assert low == {}, low
    assert r["skip"] == "#panel-body" and r["bodyTarget"] == "-1"
