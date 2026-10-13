"""Ortak çoklu seçici (`ui-kit/picker.js`) — davranış ve erişilebilirlik, Node'da.

Kullanıcı (13.10.2026): "KM'de toplu işlemler kısmında öğrenci seçilemiyor,
hatalı, UI görünmüyor." KM Öğrenci Yönetimi → Toplu işlemler ile BBD Akademi'nin
bütün "Kime?" seçicileri bu TEK bileşeni kullanır. Burada iki kip sınanır:

* YEREL — liste bellekte: arama (Türkçe katlama), grup süzgeci, görünenleri
  seç, yalnız seçilenler, seçili sayısı, "Daha fazla göster", klavye, boş /
  yükleniyor / hata durumları.
* UZAK — `source({query, group, cursor})`: arama ve grup sunucuya gider
  (gecikmeli, tuş başına istek yok), "Daha fazla yükle" imleçle sürer, seçim
  aramalar arasında korunur, hata "Tekrar dene" ile aynı isteği yineler.

DOM `tools/a11y/dom.mjs`, denetim `audit.mjs`; `node` yoksa ATLANIR.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run  # noqa: E402

LOCAL = r"""
export default async function ({ document, audit, sleep, load }) {
  const { createPicker } = await load('./ui-kit/picker.js');
  const root = document.createElement('div');
  root.className = 'kit-panel';
  document.body.append(root);
  const changes = [];
  const picker = createPicker({ groupLabel: 'Sınıf', placeholder: 'Ad, sınıf, no ara', label: 'Öğrenciler',
    limit: 5, onChange: (ids) => changes.push(ids.length) });
  root.append(picker.node);
  const out = {};
  const rows = () => root.querySelectorAll('.pk-row');
  const count = () => root.querySelector('.pk-count').textContent;
  const btn = (text) => root.querySelectorAll('button').find((b) => b.textContent.startsWith(text));
  const key = (el, k) => el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true }));

  out.empty = root.querySelector('.pk-list').textContent;
  picker.setLoading(true);
  out.loading = root.querySelector('.pk-list').textContent;
  out.busy = root.querySelector('.pk-list').getAttribute('aria-busy');
  let retried = 0;
  picker.setError('Öğrenci listesi okunamadı: zaman aşımı', () => { retried += 1; });
  out.error = root.querySelector('.pk-error').textContent;
  out.errorRole = root.querySelector('.pk-error').getAttribute('role');
  btn('Tekrar dene').click();
  out.retried = retried;

  const names = ['Elif Kaya', 'Ömer Şahin', 'İlayda Işık', 'Can Demir', 'Ece Çelik', 'Emir Doğan', 'Şule Ak'];
  picker.setItems(names.map((name, i) => ({ id: `S${i}`, name, group: i < 4 ? '9-A' : '10-B', meta: `no ${i}` })));
  out.rows = rows().length;                       // limit 5
  out.more = btn('Daha fazla göster')?.textContent;
  out.checks = root.querySelectorAll('.pk-check').length;
  out.tabbable = rows().filter((r) => r.tabIndex === 0).length;
  out.groups = root.querySelector('.pk-select').querySelectorAll('option').map((o) => o.textContent);
  btn('Daha fazla göster').click();
  out.rowsAfterMore = rows().length;
  out.violations = audit(root);

  // seçim: tıklama + sayı + aria-pressed
  rows()[0].click();
  rows()[2].click();
  out.count = count();
  out.pressed = rows().map((r) => r.getAttribute('aria-pressed'));
  out.countRole = root.querySelector('.pk-count').getAttribute('role');

  // Türkçe katlamalı arama: "ilayda isik" → İlayda Işık
  const search = root.querySelector('.pk-search');
  search.value = 'ilayda isik';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(120);
  out.search = rows().map((r) => r.querySelector('.pk-name').textContent);
  // klavye: aramada ↓ ilk satıra iner, Boşluk/tıklama seçer
  search.focus();
  key(search, 'ArrowDown');
  out.focusRow = document.activeElement?.querySelector?.('.pk-name')?.textContent;
  document.activeElement.click();
  out.afterKey = count();
  // boş arama sonucu + "Aramayı temizle"
  search.value = 'zzz';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(120);
  out.noMatch = root.querySelector('.pk-empty').textContent;
  btn('Aramayı temizle').click();
  out.cleared = rows().length;

  // ↑/↓/Home/End satırlar arasında; gezici tabindex
  rows()[0].focus();
  key(rows()[0], 'ArrowDown');
  out.down = document.activeElement?.dataset?.id;
  key(document.activeElement, 'End');
  out.end = document.activeElement?.dataset?.id;
  key(document.activeElement, 'Home');
  out.home = document.activeElement?.dataset?.id;
  out.tabbableAfter = rows().filter((r) => r.tabIndex === 0).map((r) => r.dataset.id);
  key(document.activeElement, 'ArrowUp');
  out.upToSearch = document.activeElement === search;

  // grup süzgeci + görünenleri seç + yalnız seçilenler
  const group = root.querySelector('.pk-select');
  group.value = '10-B';
  group.dispatchEvent(new Event('change'));
  out.groupRows = rows().length;
  btn('Görünenleri seç').click();
  out.afterVisible = count();
  group.value = '';
  group.dispatchEvent(new Event('change'));
  btn('Yalnız seçilenler').click();
  out.onlySelected = rows().map((r) => r.dataset.id);
  out.onlyPressed = btn('Yalnız seçilenler').getAttribute('aria-pressed');
  btn('Yalnız seçilenler').click();
  out.selection = picker.selection();
  out.selectedItems = picker.selectedItems().map((i) => i.id);
  btn('Seçimi temizle').click();
  out.afterClear = count();
  out.changes = changes;
  out.violationsEnd = audit(root);
  return out;
}
"""

REMOTE = r"""
export default async function ({ document, audit, sleep, load }) {
  const { createPicker } = await load('./ui-kit/picker.js');
  const root = document.createElement('div');
  root.className = 'kit-panel';
  document.body.append(root);
  const requests = [];
  let failNext = false;
  const all = Array.from({ length: 7 }, (_, i) => ({ id: `A${i}`, name: `Öğrenci ${i}`, cls: i % 2 ? 'c2' : 'c1' }));
  const source = async ({ query, group, cursor }) => {
    requests.push({ query, group, cursor });
    await sleep(5);
    if (failNext) { failNext = false; throw new Error('Akademi yanıt vermedi'); }
    const rows = all.filter((s) => (!query || s.name.includes(query)) && (!group || s.cls === group));
    const start = Number(cursor || 0);
    return { items: rows.slice(start, start + 3).map((s) => ({ id: s.id, name: s.name, meta: s.cls })),
      next: start + 3 < rows.length ? String(start + 3) : null };
  };
  const picker = createPicker({ source, groups: [{ value: 'c1', label: '9-A' }, { value: 'c2', label: '9-B' }],
    groupLabel: 'Sınıf', placeholder: 'Öğrenci ara', label: 'Öğrenciler', limit: 3 });
  root.append(picker.node);
  const out = {};
  const rows = () => root.querySelectorAll('.pk-row');
  const btn = (text) => root.querySelectorAll('button').find((b) => b.textContent.startsWith(text));
  out.loading = root.querySelector('.pk-list').textContent;
  await sleep(20);
  out.first = rows().map((r) => r.dataset.id);
  out.count = root.querySelector('.pk-count').textContent;
  btn('Daha fazla yükle').click();
  await sleep(20);
  out.paged = rows().map((r) => r.dataset.id);
  rows()[0].click();
  rows()[4].click();
  // arama: 250 ms gecikme, tek istek; seçim korunur
  const search = root.querySelector('.pk-search');
  for (const text of ['Ö', 'Öğ', 'Öğrenci 6']) {
    search.value = text;
    search.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(30);
  }
  const before = requests.length;
  await sleep(300);
  out.searchRequests = requests.slice(before).map((r) => r.query);
  out.searchRows = rows().map((r) => r.dataset.id);
  out.kept = picker.selection();
  btn('Yalnız seçilenler').click();
  out.onlySelected = rows().map((r) => r.querySelector('.pk-name').textContent);
  btn('Yalnız seçilenler').click();
  // grup süzgeci sunucuya gider
  search.value = '';
  search.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(300);
  const group = root.querySelector('.pk-select');
  out.groupOptions = group.querySelectorAll('option').map((o) => o.textContent);
  group.value = 'c2';
  group.dispatchEvent(new Event('change'));
  await sleep(20);
  out.groupRequest = requests[requests.length - 1];
  out.groupRows = rows().map((r) => r.dataset.id);
  // hata + Tekrar dene
  failNext = true;
  picker.reload();
  await sleep(20);
  out.error = root.querySelector('.pk-error')?.textContent;
  btn('Tekrar dene').click();
  await sleep(20);
  out.recovered = rows().length;
  out.items = picker.selectedItems().map((i) => i.name);
  out.violations = audit(root);
  picker.destroy();
  return out;
}
"""


def node(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_yerel_kip_durumlar_secim_arama_klavye() -> None:
    out = node(LOCAL)
    assert out["empty"].startswith("Liste boş.")
    assert out["loading"] == "Yükleniyor…" and out["busy"] == "true"
    assert out["error"].startswith("Öğrenci listesi okunamadı: zaman aşımı")
    assert out["errorRole"] == "alert" and out["retried"] == 1
    assert out["rows"] == 5 and out["more"] == "Daha fazla göster (2 kayıt daha)"
    assert out["rowsAfterMore"] == 7
    assert out["checks"] == 5                       # her satırda onay kutusu (renk tek başına değil)
    assert out["tabbable"] == 1                     # gezici tabindex: listeye tek Tab durağı
    assert out["groups"] == ["Tüm sınıflar", "10-B", "9-A"]
    assert out["violations"] == []
    assert out["count"] == "7 kayıt görünüyor · 2 seçili" and out["countRole"] == "status"
    assert out["pressed"][:3] == ["true", "false", "true"]
    assert out["search"] == ["İlayda Işık"]
    assert out["focusRow"] == "İlayda Işık"
    assert out["afterKey"] == "1 kayıt görünüyor · 1 seçili"     # S2 yeniden tıklanınca seçimden çıktı
    assert out["noMatch"].startswith("Aramaya uyan kayıt yok.")
    assert out["cleared"] == 5
    assert out["down"] == "S1" and out["end"] == "S4" and out["home"] == "S0"
    assert out["tabbableAfter"] == ["S0"]
    assert out["upToSearch"] is True
    assert out["groupRows"] == 3
    assert out["afterVisible"] == "3 kayıt görünüyor · 4 seçili"
    assert out["onlySelected"] == ["S0", "S4", "S5", "S6"] and out["onlyPressed"] == "true"
    assert out["selection"] == ["S0", "S4", "S5", "S6"]
    assert out["selectedItems"] == ["S0", "S4", "S5", "S6"]   # yerel kipte liste sırası
    assert out["afterClear"].endswith("0 seçili")
    assert out["changes"][-1] == 0
    assert out["violationsEnd"] == []


def test_uzak_kip_sayfalama_gecikmeli_arama_secim_korunur_hata() -> None:
    out = node(REMOTE)
    assert out["loading"] == "Yükleniyor…"
    assert out["first"] == ["A0", "A1", "A2"] and out["count"] == "3+ kayıt görünüyor · 0 seçili"
    assert out["paged"] == ["A0", "A1", "A2", "A3", "A4", "A5"]
    assert out["searchRequests"] == ["Öğrenci 6"]   # tuş başına istek yok (A14)
    assert out["searchRows"] == ["A6"]
    assert out["kept"] == ["A0", "A4"]               # seçim aramadan sonra korunur
    assert out["onlySelected"] == ["Öğrenci 0", "Öğrenci 4"]
    assert out["groupOptions"] == ["Tüm sınıflar", "9-A", "9-B"]
    assert out["groupRequest"] == {"query": "", "group": "c2", "cursor": None}
    assert out["groupRows"] == ["A1", "A3", "A5"]
    assert out["error"].startswith("Liste okunamadı: Akademi yanıt vermedi")
    assert out["recovered"] == 3
    assert out["items"] == ["Öğrenci 0", "Öğrenci 4"]
    assert out["violations"] == []


def test_kit_stili_satir_onay_kutusu_ve_odak_halkasi() -> None:
    css = (ROOT / "apps" / "desktop" / "shell" / "ui-kit" / "kit.css").read_text(encoding="utf-8")
    assert ".pk-row.on .pk-check" in css and ".pk-row:focus-visible" in css
    assert ".pk-more" in css and ".pk-empty.pk-error" in css
