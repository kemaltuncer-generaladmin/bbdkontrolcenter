"""Ödev içerik alanları (`homework-content.js`) + seviyeli program seçicisi birlikte — Node'da davranış.

0.5.5 birleşimi: 0.5.4 ödev formunun ders / konu / kaynak alanlarını ortak `homeworkContent`
fabrikasına taşıdı (tek ödev, toplu ödev "Ne?", ödev şablonu); müfredat düzeltmesi ise ödevin
konu seçicisini seviyeli seçiciye (`curriculum-picker.js`) çevirdi ve öneriyi ödevin hedefinden
(sınıf / grup / öğrenci) aldı. İkisi birleşince seviyeli seçici ORTAK fabrikadadır; hedef
`homeworkContent(..., {target})` ile verilir. Bu test:

* hedef verilince program sorgusuna hedefin gittiğini, önerilen (hedefin seviyesindeki) programın
  kendiliğinden seçildiğini, ilk programın (5. sınıf) seçilmediğini,
* hedef sonradan seçilince (ör. toplu ödevde "Kime?"den "Ne?"ye dönüş) önerinin yeniden
  hesaplandığını, yöneticinin seçtiği program varken seçimin korunduğunu,
* hedefsiz formda (şablon) sorgunun hedefsiz gittiğini, seviye çiplerinin yine çalıştığını,
* `content()`in yalnız KONU kimliği taşıdığını ve erişilebilirlik denetiminin temiz olduğunu
  doğrular. `node` yoksa atlanır.
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

PROGRAMS = "/v1/admin/curricula/programs"


def _program(pid: str, grade: Any, level: tuple[str, str, int], *, recommended: bool = False) -> dict[str, Any]:
    return {"id": pid, "kind": "school", "exam": None, "grade": grade, "tracks": [],
            "subject": {"id": "matematik", "name": "Matematik"}, "title": "Matematik", "scope": "global",
            "levelKey": level[0], "levelLabel": level[1], "levelOrder": level[2], "recommended": recommended}


PLAIN = {"items": [
    _program("ortaokul-5-matematik", 5, ("ortaokul-5", "5. sınıf", 10)),
    _program("lise-11-matematik", 11, ("lise-11", "11. sınıf", 70)),
], "target": None}
FOR_11A = {"items": [
    _program("ortaokul-5-matematik", 5, ("ortaokul-5", "5. sınıf", 10)),
    _program("lise-11-matematik", 11, ("lise-11", "11. sınıf", 70), recommended=True),
], "target": {"studentCount": 24, "unknownLevelCount": 0,
              "levels": [{"levelKey": "lise-11", "label": "11. sınıf · SAY · YKS", "studentCount": 24}]}}
DETAIL = {"id": "lise-11-matematik", "title": "Matematik", "units": [
    {"id": "lise-11-matematik-u1", "title": "Trigonometri", "order": 1, "topics": [
        {"id": "lise-11-matematik-u1-t1", "title": "Yönlü açılar", "order": 1}]}]}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const { homeworkContent } = await load('./panels/bbd_akademi/homework-content.js');
  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  globalThis.__apiHook = (method, path) => {
    if (method !== 'GET' || !path.startsWith('/v1/admin/curricula/programs?')) return undefined;
    return path.includes('classGroupIds=g-11a') ? FOR_11A : PLAIN;
  };
  const app = { api: {
    getCached: (path, query) => api(`${path}${qs(query)}`),
    get: (path, query) => api(`${path}${qs(query)}`),
  } };
  const listCalls = () => calls.filter((c) => c.path.startsWith('/v1/admin/curricula/programs?')).map((c) => c.path);
  const out = {};

  // 1) tek ödev: hedef baştan belli (11-A) → öneri kendiliğinden seçilir, konular yüklenir
  const target = { classGroupIds: ['g-11a'], studentGroupIds: [], studentIds: [] };
  const single = homeworkContent(app, null, [{ id: 'matematik', name: 'Matematik' }], { target: () => target });
  const host = document.createElement('div');
  document.body.append(host);
  const field = single.topicField({ subjectId: 'matematik' });
  host.append(field.node);
  await sleep(20);
  out.singleQuery = listCalls();
  out.singlePicked = host.querySelector('.ak-prog-picker select').value;
  out.singleInfo = host.querySelector('.ak-prog-target').textContent;
  out.chips = host.querySelectorAll('.ak-hw-unit button').map((b) => b.textContent);
  host.querySelectorAll('.ak-hw-unit button')[0].dispatchEvent(new Event('click', { bubbles: true }));
  out.content = single.content({ title: ' Trigonometri ', subjectId: 'matematik' });
  out.violations = audit(host);
  // hedef değişse de yöneticinin (ya da önerinin) seçtiği program korunur — istek yok
  const before = listCalls().length;
  target.classGroupIds = [];
  target.studentIds = ['s-1'];
  field.update({ subjectId: 'matematik' });
  await sleep(20);
  out.keptAfterTargetChange = [host.querySelector('.ak-prog-picker select').value, listCalls().length - before];
  single.dispose();

  // 2) toplu ödev "Ne?": hedef henüz yok → hedefsiz sorgu, öneri yok; "Kime?" seçilip dönülünce yeniden
  let later = null;
  const bulk = homeworkContent(app, null, [], { target: () => later });
  const host2 = document.createElement('div');
  document.body.append(host2);
  const field2 = bulk.topicField({ subjectId: 'matematik' });
  host2.append(field2.node);
  await sleep(20);
  out.bulkFirst = [listCalls().at(-1), host2.querySelector('.ak-prog-picker select').value];
  later = { classGroupIds: ['g-11a'], studentGroupIds: [], studentIds: [] };
  field2.update({ subjectId: 'matematik' });
  await sleep(20);
  out.bulkAfterTarget = [listCalls().at(-1), host2.querySelector('.ak-prog-picker select').value];
  bulk.dispose();

  // 3) şablon (hedefsiz): seviye çipi ile seçim yöneticide
  const tpl = homeworkContent(app, null, []);
  const host3 = document.createElement('div');
  document.body.append(host3);
  const field3 = tpl.topicField({ subjectId: 'matematik' });
  host3.append(field3.node);
  await sleep(20);
  out.tplQuery = listCalls().at(-1);
  out.levelChips = host3.querySelectorAll('.ak-prog-levels button').map((b) => b.textContent);
  host3.querySelectorAll('.ak-prog-levels button').find((b) => b.textContent === '11. sınıf')
    .dispatchEvent(new Event('click', { bubbles: true }));
  await sleep(20);
  out.tplPicked = host3.querySelector('.ak-prog-picker select').value;
  out.tplOptions = host3.querySelector('.ak-prog-picker select').querySelectorAll('option').map((o) => o.textContent);
  out.tplViolations = audit(host3);
  // ders seçilmemişse yol gösterilir
  const field4 = tpl.topicField({ subjectId: '' });
  out.noSubject = field4.node.textContent;
  tpl.dispose();
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    routes = {f"GET {PROGRAMS}/lise-11-matematik": DETAIL}
    scenario = (
        f"const PLAIN = {json.dumps(PLAIN, ensure_ascii=False)};\n"
        f"const FOR_11A = {json.dumps(FOR_11A, ensure_ascii=False)};\n" + SCENARIO
    )
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_tek_odev_hedefin_seviyesinden_oneri(result: dict[str, Any]) -> None:
    assert result["singleQuery"] == [f"{PROGRAMS}?subject=matematik&classGroupIds=g-11a"]
    assert result["singlePicked"] == "lise-11-matematik"  # 5. sınıf değil
    assert "11. sınıf · SAY · YKS (24)" in result["singleInfo"]
    assert result["chips"] == ["Yönlü açılar"]


def test_icerik_yalniz_konu_kimligi_tasir(result: dict[str, Any]) -> None:
    content = result["content"]
    assert content["topicIds"] == ["lise-11-matematik-u1-t1"]
    assert content["title"] == "Trigonometri"
    assert content["subjectId"] == "matematik"


def test_secili_program_hedef_degisince_korunur(result: dict[str, Any]) -> None:
    assert result["keptAfterTargetChange"] == ["lise-11-matematik", 0]


def test_toplu_odev_hedef_sonradan_gelince_oneri(result: dict[str, Any]) -> None:
    assert result["bulkFirst"] == [f"{PROGRAMS}?subject=matematik", ""]
    assert result["bulkAfterTarget"] == [f"{PROGRAMS}?subject=matematik&classGroupIds=g-11a", "lise-11-matematik"]


def test_sablon_hedefsiz_seviye_cipiyle_secilir(result: dict[str, Any]) -> None:
    assert result["tplQuery"] == f"{PROGRAMS}?subject=matematik"
    assert result["levelChips"] == ["Tüm seviyeler", "5. sınıf", "11. sınıf"]
    assert result["tplPicked"] == "lise-11-matematik"
    assert "11. sınıf · Matematik" in result["tplOptions"]
    assert "Konu seçmek için önce ders seçin" in result["noSubject"]


def test_erisilebilirlik_temiz(result: dict[str, Any]) -> None:
    assert result["violations"] == []
    assert result["tplViolations"] == []
