"""BBD Akademi toplu işlemlerinin "Kime?" öğrenci seçicisi — Node'da davranış.

Kullanıcı (13.10.2026): "KM'de toplu işlemler kısmında öğrenci seçilemiyor,
hatalı, UI görünmüyor." Toplu ödev, atama paketi, toplu program, kaynak ataması,
toplu kitap ve plan aynı hedef alanını (`bulkTargetField` → `targetsField`)
kullanır. Eskiden öğrenci yalnız "en az 2 harf yaz · açılan kutudan tek tek
seç" ile eklenebiliyordu; açılır kutu `position: absolute` olduğu için
pencerede kesiliyordu, sınıf süzgeci / tümünü seç / sayı yoktu.

Şimdi KM'nin ortak seçicisi (`ui-kit/picker.js`, uzak kip) — `studentSelect`:
liste açılışta gelir (`admin_listStudents`, sayfa 50), arama ve sınıf süzgeci
sunucuda, imleçli "Daha fazla yükle", görünenleri seç, seçim sözleşme gövdesine
(`studentIds`, `excludeStudentIds`) aynen gider. "Hariç tut" seçicisi bölüm
açılana dek istek atmaz (A14).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

PANEL = Path(__file__).resolve().parents[1] / "ui" / "panel"
CLASSES = [{"id": "11111111-1111-4111-8111-111111111111", "name": "9-A"},
           {"id": "22222222-2222-4222-8222-222222222222", "name": "10-B"}]

SCENARIO = r"""
export default async function ({ document, audit, sleep, load }) {
  const CLASSES = __CLASSES__;
  const STUDENTS = Array.from({ length: 60 }, (_, i) => ({ id: `ak-${i}`, displayName: `Öğrenci ${i}`,
    classGroup: CLASSES[i % 2], gradeLevel: i % 2 ? 10 : 9, schoolNo: String(100 + i) }));
  const gets = [];
  let fail = false;
  const app = { api: { get: async (path, query = {}) => {
    gets.push({ path, query: JSON.parse(JSON.stringify(query)) });
    await sleep(2);
    if (fail) { fail = false; const e = new Error('Oturum süresi doldu.'); e.status = 401; throw e; }
    if (path !== '/v1/admin/students') return { items: [] };
    const rows = STUDENTS.filter((s) => (!query.q || s.displayName.includes(query.q))
      && (!query.classGroupId || s.classGroup.id === query.classGroupId));
    const start = Number(query.cursor || 0);
    const limit = query.limit || 20;
    return { items: rows.slice(start, start + limit), nextCursor: start + limit < rows.length ? String(start + limit) : null };
  } } };
  const { bulkTargetField } = await load('./panels/bbd_akademi/bulk-target.js');
  const { targetsField } = await load('./panels/bbd_akademi/resources-common.js');
  const root = document.createElement('div');
  document.body.append(root);
  let changes = 0;
  const field = bulkTargetField(app, { classes: CLASSES, groups: [], modes: ['pick'], onChange: () => { changes += 1; } });
  root.append(field.node);
  const out = {};
  await sleep(20);
  const pick = () => root.querySelector('.ak-stu-select');
  const rows = (scope) => scope.querySelectorAll('.pk-row');
  const btn = (scope, text) => scope.querySelectorAll('button').find((b) => b.textContent.startsWith(text));
  out.firstGet = gets[0];
  out.getsBeforeExclude = gets.length;
  out.rows = rows(pick()).length;
  out.meta = rows(pick())[1].querySelector('.pk-meta').textContent;
  out.classOptions = pick().querySelector('.pk-select').querySelectorAll('option').map((o) => o.textContent);
  out.problemEmpty = field.problem();
  out.violations = audit(root);
  // sınıf süzgeci sunucuya gider; görünenleri seç
  const select = pick().querySelector('.pk-select');
  select.value = CLASSES[1].id;
  select.dispatchEvent(new Event('change'));
  await sleep(20);
  out.classGet = gets[gets.length - 1].query;
  btn(pick(), 'Görünenleri seç').click();
  out.count = pick().querySelector('.pk-count').textContent;
  out.summary = field.summary();
  out.problem = field.problem();
  // "Daha fazla yükle" imleçle sürer
  select.value = '';
  select.dispatchEvent(new Event('change'));
  await sleep(20);
  btn(pick(), 'Daha fazla yükle').click();
  await sleep(20);
  out.moreGet = gets[gets.length - 1].query;
  out.rowsAfterMore = rows(pick()).length;
  // hariç tut: açılınca kurulur, seçilen excludeStudentIds'e gider
  const details = root.querySelector('.ak-bt-exclude');
  const beforeOpen = gets.length;
  details.querySelector('summary').click();
  await sleep(20);
  out.getsAfterExclude = gets.length - beforeOpen;
  const ex = details.querySelector('.pk-box');
  rows(ex)[0].click();
  out.excludeSummary = details.querySelector('summary').textContent;
  const body = field.value({ bundle: false });
  out.body = { studentIds: body.studentIds.length, excluded: body.excludeStudentIds, classGroupIds: body.classGroupIds };
  out.summary2 = field.summary();
  out.changes = changes;
  out.violationsEnd = audit(root);
  field.destroy();

  // targetsField (toplu kitap, plan): hata → "Tekrar dene"; başlangıç seçimi korunur
  fail = true;
  const host = document.createElement('div');
  document.body.append(host);
  let last = null;
  const targets = targetsField(app, { classes: CLASSES, groups: [], value: { students: [{ id: 'ak-7', displayName: 'Öğrenci 7' }] },
    onChange: (next) => { last = next; } });
  host.append(targets.node);
  await sleep(20);
  out.targetsError = host.querySelector('.pk-error')?.textContent;
  btn(host, 'Tekrar dene').click();
  await sleep(20);
  out.targetsRows = rows(host).length;
  out.targetsInitial = targets.value().studentIds;
  rows(host)[0].click();
  out.targetsAfter = last.studentIds;
  out.targetsStudents = targets.students();
  out.targetsCount = targets.count();
  return out;
}
"""


@pytest.fixture(scope="module")
def out() -> dict[str, Any]:
    scenario = SCENARIO.replace("__CLASSES__", json.dumps(CLASSES))
    try:
        data = run(scenario, panels=("bbd_akademi",))
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    result: dict[str, Any] = data["result"]
    return result


def test_liste_acilista_gelir_sinif_suzgeci_sunucuda(out: dict[str, Any]) -> None:
    assert out["firstGet"] == {"path": "/v1/admin/students", "query": {"limit": 50}}
    assert out["getsBeforeExclude"] == 1            # hariç tut bölümü kapalıyken istek yok
    assert out["rows"] == 50
    assert out["meta"] == "10-B · 10. sınıf · no 101"
    assert out["classOptions"] == ["Tüm sınıflar", "9-A", "10-B"]
    assert out["problemEmpty"] == "En az bir sınıf, çalışma grubu ya da öğrenci seçin."
    assert out["violations"] == []
    assert out["classGet"] == {"classGroupId": CLASSES[1]["id"], "limit": 50}


def test_gorunenleri_sec_sayi_ve_ozet(out: dict[str, Any]) -> None:
    assert out["count"] == "30 kayıt görünüyor · 30 seçili"
    assert out["summary"] == "30 öğrenci" and out["problem"] is None
    assert out["moreGet"] == {"cursor": "50", "limit": 50}
    assert out["rowsAfterMore"] == 60


def test_haric_tut_ayni_secici_ve_govde(out: dict[str, Any]) -> None:
    assert out["getsAfterExclude"] == 1
    assert out["excludeSummary"] == "Hariç tutulan öğrenciler (1)"
    assert out["body"] == {"studentIds": 30, "excluded": ["ak-0"], "classGroupIds": []}
    assert out["summary2"] == "30 öğrenci · 1 hariç"
    assert out["changes"] >= 2
    assert out["violationsEnd"] == []


def test_targets_field_hata_tekrar_dene_ve_baslangic_secimi(out: dict[str, Any]) -> None:
    assert out["targetsError"].startswith("Liste okunamadı: Oturum süresi doldu.")
    assert out["targetsRows"] == 50
    assert out["targetsInitial"] == ["ak-7"]
    assert out["targetsAfter"] == ["ak-7", "ak-0"]
    assert out["targetsStudents"] == [{"id": "ak-7", "displayName": "Öğrenci 7"},
                                      {"id": "ak-0", "displayName": "Öğrenci 0"}]
    assert out["targetsCount"] == 2


def test_eski_acilir_kutu_toplu_hedefte_kullanilmaz() -> None:
    """Toplu hedef alanları ortak seçiciyi kullanır; tek-öğrenci aramaları (`studentPicker`) ayrı kalır."""
    for name in ("bulk-target.js", "resources-common.js"):
        text = (PANEL / name).read_text(encoding="utf-8")
        assert "studentSelect(" in text and "studentPicker(" not in text, name
    css = (PANEL / "panel.css").read_text(encoding="utf-8")
    assert re.search(r"\.ak-stu-select \{ height: clamp\(", css)
    assert re.search(r"\.ak-wiz > \.ak-wiz-body \{[^}]*overflow: auto", css)
