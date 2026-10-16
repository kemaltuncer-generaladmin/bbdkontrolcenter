"""BBD Store kataloğundan ücretsiz toplu kaynak ataması (Akademi README §14 madde 116) — panel davranışı.

Kaynaklar → "BBD Store'dan toplu ata" üç adımlı sihirbazı Node'da küçük bir DOM üzerinde çalıştırılır
(`tools/a11y/harness.py`); Akademi vekili taklit edilir ve giden GÖVDELER sözleşmeye göre denetlenir:
katalog süzgeçleri sorguya, satır seçimi `productIds`'e, "süzülenlerin tümü" `allMatching` +
`excludeProductIds`'e çevrilir; önizleme yazmaz ve uyarıları gösterir; büyük atama (`202`)
`admin_getBulkJob` ile izlenir; sonuçtan toplu geri alma önce sayar, onayla kaldırır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run
from modules.bbd_akademi.tests.test_akademi_toplu_kitap import PRELUDE

ITEMS = r"""
const item = (id, title, extra = {}) => ({ productId: String(id), resourceId: `bbs-x-${id}`, title,
  publisher: 'Ulti Yayınları', author: null, isbn: null, coverUrl: null, series: null, kind: 'question_bank',
  gradeLevels: [8], exams: ['LGS'], subjectIds: ['matematik'], priceMinor: 0, free: true, inStock: true,
  status: 'active', ...extra });
"""

SIHIRBAZ = PRELUDE + ITEMS + r"""
export default async function ({ document, audit, sleep, api, load }) {
  globalThis.api = api;
  routes['GET /v1/admin/store-catalog/facets'] = { totalCount: 1311, freeCount: 42, inStockCount: 1200,
    removedCount: 3, gradeLevels: [{ value: '8', label: '8. sınıf', count: 300 }],
    exams: [{ value: 'LGS', label: 'LGS', count: 300 }], subjects: [{ value: 'matematik', label: 'Matematik', count: 200 }],
    publishers: [{ value: 'Ulti Yayınları', label: 'Ulti Yayınları', count: 80 }],
    kinds: [{ value: 'question_bank', label: 'Soru bankası', count: 500 }], syncedAt: '2026-10-16T12:00:00Z' };
  routes['GET /v1/admin/store-catalog'] = { items: [item(101, 'LGS Matematik Soru Bankası'),
    item(102, 'LGS Fen Konu Anlatımı', { free: false, priceMinor: 30000, inStock: false })],
    page: 1, hasMore: false, totalCount: 2, syncedAt: '2026-10-16T12:00:00Z' };
  routes['POST /v1/admin/resource-assignments/bulk/preview'] = { resourceCount: 2, studentCount: 30, newCount: 58,
    alreadyCount: 2, rowCount: 2, skipped: [{ productId: '103', resourceId: null, title: 'Eski Kitap',
      reason: 'removed' }], outOfStockCount: 1, groups: [{ kind: 'class_group', id: 'c-9a', label: '9-A',
      studentCount: 30 }], inline: false, free: true };
  const result = { batchId: 'bt-1', resourceCount: 2, studentCount: 30, createdCount: 2, newCount: 58,
    alreadyCount: 2, skippedCount: 1, notifiedCount: 30 };
  routes['POST /v1/admin/resource-assignments/bulk'] = { id: 'job-tk', kind: 'resource_assign', status: 'queued',
    progress: { doneCount: 0, totalCount: 2 }, homework: null, bundle: null, resources: null, error: null,
    createdAt: '2026-10-16T13:00:00Z', finishedAt: null };
  routes['GET /v1/admin/bulk-jobs/job-tk'] = { id: 'job-tk', kind: 'resource_assign', status: 'succeeded',
    progress: { doneCount: 2, totalCount: 2 }, homework: null, bundle: null, resources: result, error: null,
    createdAt: '2026-10-16T13:00:00Z', finishedAt: '2026-10-16T13:00:02Z' };
  routes['POST /v1/admin/resource-assignments/bulk-delete'] = (body) => ({ removedCount: 2, dryRun: Boolean(body.dryRun) });
  const { app, root, toasts } = await makeApp(load, document);
  app.lookups.classGroups = async () => [{ id: 'c-9a', name: '9-A', gradeLevel: 8 }, { id: 'c-9b', name: '9-B', gradeLevel: 9 }];
  const { bulkAssignWizard } = await load('./panels/bbd_akademi/resources/resources-bulk.js');
  let shown = null;
  const done = bulkAssignWizard(app, { onShowBatch: (id) => { shown = id; } });
  await sleep(60);
  const dialog = () => root.querySelector('.ak-wiz');
  const out = {};
  out.title = dialog().querySelector('.kit-dialog-title').textContent;
  out.steps = dialog().querySelectorAll('.ak-wiz-step').map((n) => n.textContent);
  out.info = dialog().querySelector('.ak-tk-info').textContent;
  out.rows = dialog().querySelectorAll('.ak-tk-row').map((n) => n.textContent);
  out.a11yBooks = audit(dialog());
  // Boş seçimle İleri → sebep.
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  out.emptyError = dialog().querySelector('.ak-wiz-error').textContent;
  // Ücretsiz süzgeci → sorgu.
  click(dialog().querySelectorAll('.kit-chip').find((n) => n.textContent.startsWith('Yalnız ücretsiz')));
  await sleep(30);
  out.freeQuery = sent.filter((c) => c.path === '/v1/admin/store-catalog').map((c) => c.query).pop();
  // Süzülenlerin tümü, sonra bir satırı çıkar.
  click(dialog().querySelectorAll('button').find((n) => n.textContent.startsWith('Süzülenlerin tümünü seç')));
  const box = dialog().querySelectorAll('.ak-tk-row input')[1];
  box.checked = false;
  box.dispatchEvent(new Event('change', { bubbles: true }));
  out.selected = dialog().querySelector('.ak-tk-selected').textContent;
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(30);
  out.stepTarget = dialog().querySelector('.ak-wiz-step.on').textContent;
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(20);
  out.emptyTarget = dialog().querySelector('.ak-wiz-error').textContent;
  click(byText(dialog(), '.kit-chip', '9-A'));
  await sleep(10);
  out.hint = dialog().querySelector('.ak-tk-hint').textContent;
  out.a11yTarget = audit(dialog());
  click(byText(dialog(), 'button', 'İleri'));
  await sleep(40);
  out.preview = dialog().querySelector('.ak-tk-preview').textContent;
  out.finishLabel = dialog().querySelector('.ak-wiz-finish')?.textContent;
  out.a11yPreview = audit(dialog());
  click(dialog().querySelector('.ak-wiz-finish'));
  await sleep(1700);
  const resultCard = root.querySelectorAll('.kit-dialog').find((n) => n.textContent.includes('Toplu atama tamamlandı'));
  out.result = resultCard?.textContent || '';
  out.a11yResult = resultCard ? audit(resultCard) : ['sonuç penceresi yok'];
  click(byText(resultCard, 'button', 'Geri al'));
  await sleep(30);
  const confirm = root.querySelectorAll('.kit-dialog').find((n) => n.textContent.includes('Toplu atamayı geri al'));
  out.confirm = confirm?.textContent || '';
  click(byText(confirm, 'button', 'Geri al'));
  const job = await done;
  out.jobStatus = job?.status;
  out.previewBody = sent.filter((c) => c.path === '/v1/admin/resource-assignments/bulk/preview').map((c) => c.body).pop();
  out.create = sent.filter((c) => c.path === '/v1/admin/resource-assignments/bulk');
  out.deletes = sent.filter((c) => c.path === '/v1/admin/resource-assignments/bulk-delete').map((c) => c.body);
  out.toasts = toasts;
  out.shown = shown;
  out.open = Boolean(root.querySelector('.ak-wiz'));
  return out;
}
"""

MANTIK = r"""
export default async function ({ load }) {
  const m = await load('./panels/bbd_akademi/resources/resources-bulk-logic.js');
  const sel = m.selection({ resources: [{ id: 'res-1', title: 'Kurum kitabı' }] });
  const f = { ...m.EMPTY_FILTERS, subject: 'matematik', free: true };
  sel.toggle({ productId: '5' }, m.EMPTY_FILTERS);
  sel.selectAll(f, 40);
  sel.toggle({ productId: '9' }, f);
  return {
    query: m.catalogQuery({ ...m.EMPTY_FILTERS, q: '  lgs ', gradeLevel: '8', inStock: true }, { page: 2, limit: 50 }),
    body: sel.body(),
    count: sel.count(),
    text: m.selectionText(sel),
    on9: sel.isOn({ productId: '9' }, f),
    on10: sel.isOn({ productId: '10' }, f),
    grade: m.commonGrade([{ id: 'a', gradeLevel: 8 }, { id: 'b', gradeLevel: 8 }, { id: 'c', gradeLevel: 9 }], ['a', 'b']),
    mixed: m.commonGrade([{ id: 'a', gradeLevel: 8 }, { id: 'c', gradeLevel: 9 }], ['a', 'c']),
    none: m.resultText({ createdCount: 0 }),
  };
}
"""


def node_run(scenario: str) -> dict[str, Any]:
    try:
        data = run(scenario, panels=("bbd_akademi",), timeout=120)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_toplu_kaynak_sihirbazi_uc_adim_onizleme_arka_plan_geri_al() -> None:
    out = node_run(SIHIRBAZ)
    assert out["title"] == "BBD Store'dan toplu kaynak ata"
    assert [s[-len(x):] for s, x in zip(out["steps"], ["Kitaplar", "Kime", "Önizle ve ata"], strict=True)] == [
        "Kitaplar", "Kime", "Önizle ve ata"]
    assert "1.311 kitap" in out["info"] and "42 ücretsiz" in out["info"] and "ücretsizdir" in out["info"]
    assert "Ücretsiz" in out["rows"][0] and "Stokta yok" in out["rows"][1]
    assert out["a11yBooks"] == []
    assert out["emptyError"] == "En az bir kitap seçin."
    assert out["freeQuery"] == {"free": True, "page": 1, "limit": 50}
    assert out["selected"].startswith("1 kitap seçildi")
    assert out["stepTarget"].endswith("Kime")
    assert "En az bir sınıf" in out["emptyTarget"]
    assert "8. sınıf kitaplarını göster" in out["hint"]
    assert out["a11yTarget"] == []
    preview = out["preview"]
    assert "2 kitap × 30 öğrenci → 58 yeni raf kaydı" in preview
    assert "2 zaten rafında" in preview and "mağazadan kalkmış" in preview and "Eski Kitap" in preview
    assert "arka planda" in preview and "ücretsizdir" in preview
    assert out["finishLabel"] == "Ücretsiz ata"
    assert out["a11yPreview"] == []
    body = out["previewBody"]
    assert body["productIds"] == [] and body["resourceIds"] == []
    assert body["allMatching"]["free"] is True and body["excludeProductIds"] == ["102"]
    assert body["target"]["classGroupIds"] == ["c-9a"] and body["notify"] is True
    assert len(out["create"]) == 1 and out["create"][0]["body"] == body
    assert out["create"][0]["idempotencyKey"].startswith("km-wiz-")
    assert out["jobStatus"] == "succeeded" and out["open"] is False
    assert "58 kitap 30 öğrencinin rafına eklendi" in out["result"] and "30 bildirim" in out["result"]
    assert out["a11yResult"] == []
    assert "2 atama kaldırılacak" in out["confirm"]
    assert out["deletes"] == [{"batchId": "bt-1", "dryRun": True}, {"batchId": "bt-1"}]
    assert ["2 atama geri alındı.", "good"] in out["toasts"]


def test_toplu_kaynak_secim_mantigi() -> None:
    out = node_run(MANTIK)
    assert out["query"] == {"q": "lgs", "gradeLevel": "8", "inStock": True, "page": 2, "limit": 50}
    assert out["body"] == {
        "productIds": ["5"], "resourceIds": ["res-1"],
        "allMatching": {"q": None, "gradeLevel": None, "exam": None, "subject": "matematik", "publisher": None,
                        "kind": None, "inStock": False, "free": True},
        "excludeProductIds": ["9"],
    }
    assert out["count"] == 41 and out["text"].startswith("41 kitap seçildi · süzgecin tümü")
    assert out["on9"] is False and out["on10"] is True
    assert out["grade"] == "8" and out["mixed"] == ""
    assert "zaten vardı" in out["none"]
