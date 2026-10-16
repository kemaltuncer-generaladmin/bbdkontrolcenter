"""Ortak kitap seçicisi (iş 54 `kitap-senkron`; Akademi README §14 madde 98).

Kullanıcı (15.10.2026): "kitap arama, kitap satış BBD ile tam senkron olsun … bizde şak şak seçelim, tüm kitap
listesini görelim … 3D'yi atadıysam bir öğrenciye, ondan ödev verebilmeliyim."

- `book-picker-logic.js` (saf): süzgeç → sorgu (ISBN ayrı), kart alt satırı, mağaza rozeti, son kullanılanlar;
- seçici penceresi (DOM taklidi): hedefin kitapları ("Bu öğrencinin kitapları") ve son kullanılanlar önce, arama
  sorguyu yeniler (Türkçe metin olduğu gibi gider), süzgeç seçimi sorguya girer, karta tıklayınca seçim döner ve
  son kullanılanlara yazılır; erişilebilirlik denetimi.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

LOGIC = r"""
export default async function ({ load }) {
  const m = await load('./panels/bbd_akademi/shared/book-picker-logic.js');
  const store = { data: {}, getItem(k) { return this.data[k] ?? null; }, setItem(k, v) { this.data[k] = v; } };
  m.rememberRecent([{ id: 'a', title: 'A' }], store);
  m.rememberRecent([{ id: 'b', title: 'B' }, { id: 'a', title: 'A' }], store);
  return {
    empty: m.libraryQuery(m.EMPTY_FILTERS),
    full: m.libraryQuery({ ...m.EMPTY_FILTERS, q: ' tyt mat ', gradeLevel: '8', publisher: 'Ulti', inStock: true }),
    isbn: m.libraryQuery({ ...m.EMPTY_FILTERS, q: '978-605-000-001-6' }),
    meta: m.bookMeta({ publisher: 'Ulti Yayınları', gradeLevels: [8], exams: ['LGS'] }),
    metaRange: m.bookMeta({ publisher: 'X', gradeLevels: [9, 10, 11, 12], exams: [] }),
    badges: [m.storeBadge({}), m.storeBadge({ store: { status: 'active', inStock: false } }),
      m.storeBadge({ store: { status: 'removed', inStock: false } })],
    recent: m.readRecent(store).map((r) => r.id),
    broken: m.readRecent({ getItem: () => 'bozuk' }),
    labels: [m.onShelf(2, 3), m.onShelf(1, 1), m.pickLabel(0), m.pickLabel(3), m.count(1311)],
  };
}
"""

UI = r"""
const routes = {};
const sent = [];
globalThis.__apiHook = (method, path, options) => {
  if (path !== '/api/bbd_akademi/km/call') return undefined;
  const payload = options.body || {};
  sent.push(JSON.parse(JSON.stringify(payload)));
  const key = `${payload.method} ${payload.path}`;
  const hit = Object.keys(routes).find((pattern) => new RegExp(`^${pattern}$`).test(key));
  if (!hit) return { ok: false, status: 404, error: { code: 'not_found', message: `taklit yok: ${key}` } };
  const value = typeof routes[hit] === 'function' ? routes[hit](payload.body, payload) : routes[hit];
  return { ok: true, status: 200, data: value, headers: {} };
};
const book = (id, title, extra = {}) => ({ id, scope: 'tenant', origin: 'store', kind: 'question_bank', title,
  publisher: 'Ulti Yayınları', isbn: null, coverUrl: null, gradeLevels: [8], exams: ['LGS'], subjects: [{ id: 'MAT', name: 'Matematik' }],
  sectionCount: 0, questionCount: 0, store: { productId: id, priceMinor: 30000, inStock: true, status: 'active', url: null }, ...extra });

export default async function ({ document, audit, sleep, api, load }) {
  routes['GET /v1/catalog/library/facets'] = { totalCount: 1311, gradeLevels: [{ value: '8', label: '8. sınıf', count: 176 }],
    exams: [{ value: 'LGS', label: 'LGS', count: 180 }], subjects: [{ value: 'MAT', label: 'Matematik', count: 300 }],
    publishers: [{ value: 'Ulti Yayınları', label: 'Ulti Yayınları', count: 103 }], series: [], kinds: [], syncedAt: '2026-10-15T17:00:00Z' };
  routes['GET /v1/catalog/library'] = (_body, payload) => ({ items: payload.query?.q === 'türkçe'
    ? [book('b9', 'TYT 3D Türkçe Soru Bankası')] : [book('b1', 'LGS Matematik'), book('b2', 'LGS Fen', { store: { productId: 'b2',
      priceMinor: 1, inStock: false, status: 'removed', url: null } })], nextCursor: null });
  routes['GET /v1/admin/catalog/target-books'] = { items: [{ resource: book('t3d', 'TYT 3D Türkçe Soru Bankası'), studentCount: 1,
    targetCount: 1, sources: ['assigned'] }] };

  const { createClient } = await load('./panels/bbd_akademi/api.js');
  const root = document.createElement('div');
  root.className = 'kit-panel ak';
  document.body.append(root);
  const client = createClient({ api: (path, options) => api(path, options) });
  const app = { root, api: client, can: () => true, has: () => true, toast: () => {} };
  const { bookPicker } = await load('./panels/bbd_akademi/shared/book-picker.js');

  const done = bookPicker(app, { target: { studentIds: ['stu-1'] }, subject: '' });
  await sleep(50);
  const dialog = root.querySelector('.ak-bp-dialog');
  const headings = dialog.querySelectorAll('.ak-bp-h').map((n) => n.textContent.trim());
  const cards = dialog.querySelectorAll('.ak-bp-card').map((n) => n.getAttribute('aria-label'));
  const a11y = audit(dialog);
  const input = dialog.querySelector('input[type=search]');
  input.value = 'türkçe';
  input.dispatchEvent(new Event('input', { bubbles: true }));
  await sleep(400);
  const afterSearch = dialog.querySelectorAll('.ak-bp-card').map((n) => n.getAttribute('aria-label'));
  const grade = dialog.querySelectorAll('select').find((n) => n.getAttribute('aria-label') === 'Sınıf');
  grade.value = '8';
  grade.dispatchEvent(new Event('change', { bubbles: true }));
  await sleep(50);
  const lastQuery = sent.filter((p) => p.path === '/v1/catalog/library').pop().query;
  dialog.querySelectorAll('.ak-bp-card').find((n) => n.getAttribute('aria-label').startsWith('TYT 3D')).click();
  const picked = await done;
  return { headings, cards, a11y, afterSearch, lastQuery, picked: picked.map((b) => b.id), open: Boolean(root.querySelector('.ak-bp-dialog')),
    recent: JSON.parse(globalThis.localStorage?.getItem('bbd.recentBooks.v1') || '[]').map((b) => b.id),
    targetQuery: sent.find((p) => p.path === '/v1/admin/catalog/target-books')?.query };
}
"""


def _run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_mantik_sorgu_rozet_son_kullanilanlar() -> None:
    out = _run(LOGIC)
    assert out["empty"] == {"scope": "all"}
    assert out["full"] == {"scope": "all", "q": "tyt mat", "gradeLevel": "8", "publisher": "Ulti", "inStock": True}
    assert out["isbn"] == {"scope": "all", "isbn": "9786050000016"}
    assert out["meta"] == "Ulti Yayınları · 8. sınıf · LGS"
    assert out["metaRange"] == "X · 9. sınıf–12. sınıf"
    assert out["badges"] == [None, "Stokta yok", "Mağazadan kalktı"]
    assert out["recent"] == ["b", "a"] and out["broken"] == []
    assert out["labels"] == ["2/3 öğrencide", "Rafında", "Kitap seç", "3 kitabı seç", "1.311 kitap"]


def test_secici_hedefin_kitaplari_once_arama_suzgec_secim() -> None:
    out = _run(UI)
    assert out["headings"][0] == "Bu öğrencinin kitapları"
    assert out["headings"][-1].startswith("Tüm kitaplar")
    assert out["cards"][0].startswith("TYT 3D Türkçe Soru Bankası") and "Rafında" in out["cards"][0]
    assert any("Mağazadan kalktı" in c for c in out["cards"])
    assert out["targetQuery"] == {"studentId": "stu-1"}
    assert out["afterSearch"][-1].startswith("TYT 3D Türkçe")
    assert out["lastQuery"]["q"] == "türkçe" and out["lastQuery"]["gradeLevel"] == "8" and out["lastQuery"]["scope"] == "all"
    assert out["picked"] == ["b9"] or out["picked"] == ["t3d"]
    assert out["open"] is False
    assert out["a11y"] == [] or all(item.get("impact") != "critical" for item in out["a11y"])
