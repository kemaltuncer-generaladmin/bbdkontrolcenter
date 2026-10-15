"""Öğrenci müfredat seti + sınıf müfredatı (`ui/panel/curriculum-set.js`) — Node'da küçük DOM üzerinde.

Kullanıcı (14.10.2026, iş 23): "her öğrenciye ayrı müfredat istediğimiz gibi atalım, varsayılan olsun ama
biz değiştirebilelim" · "hüseyin hocanın TYT sınıfına bağımsız müfredat (TYT mat) atayabilmeliyiz".
Bu test:

* dönem hesabını (1 Eylül'de döner) ve kaynak etiketlerini ("Otomatik", "Elle", "Sınıftan: 8-A"),
* öğrenci setinde yalnız sınıftan gelen öğenin çıkarılamadığını, "Geri ekle" / "Ekle" / "Seviyenin
  tümünü ekle"nin doğru `{add}` gövdesini gönderdiğini, elle değiştirilmiş sette "Varsayılana döndür"ün
  göründüğünü, geçmiş dönemin salt okunur olduğunu,
* sınıf müfredatında etkilenen öğrenci sayısını, atamayı ve arşivli sınıfın salt okunurluğunu,
* erişilebilirlik denetiminin temiz olduğunu doğrular. `node` yoksa atlanır.
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

STUDENT = "0192f0aa-1111-7222-8333-944455556666"
CLASS = "0192f0aa-2222-7222-8333-944455556666"
PROGRAMS = "/v1/admin/curricula/programs"


def program(pid: str, title: str, level: tuple[str, str, int]) -> dict[str, Any]:
    return {"id": pid, "title": title, "kind": "school", "subject": {"id": pid.split("-")[-1], "name": title},
            "levelKey": level[0], "levelLabel": level[1], "levelOrder": level[2], "scope": "global",
            "curriculumVersion": "meb-2024"}


MAT11 = program("lise-11-matematik", "Matematik", ("lise-11", "11. sınıf", 70))
FIZ11 = program("lise-11-fizik", "Fizik", ("lise-11", "11. sınıf", 70))
TYTMAT = program("tyt-matematik", "TYT Matematik", ("tyt", "TYT", 90))
TYTTUR = program("tyt-turkce", "TYT Türkçe", ("tyt", "TYT", 90))

STUDENT_SET = {
    "studentId": STUDENT, "academicYear": "2026-2027", "current": True, "customized": True,
    "basis": {"gradeLevel": 11, "track": "SAY", "examTarget": "YKS"},
    "items": [
        {"program": MAT11, "sources": [{"kind": "default"}], "removable": True},
        {"program": TYTMAT, "sources": [{"kind": "class", "classGroup": {"id": CLASS, "name": "TYT Hüseyin"}}],
         "removable": False},
    ],
    "removedDefaults": [FIZ11],
    "suggestions": [{"program": TYTTUR, "fromYear": "2025-2026", "reason": "previous_manual"}],
}
PAST_SET = {**STUDENT_SET, "academicYear": "2025-2026", "current": False, "suggestions": []}
CLASS_SET = {
    "classGroup": {"id": CLASS, "name": "TYT Hüseyin"}, "academicYear": "2026-2027", "current": True,
    "memberCount": 14,
    "items": [{"program": TYTMAT, "assignedAt": "2026-10-14T09:00:00+03:00", "assignedBy": "Kemal Bey"}],
    "suggestions": [],
}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const cs = await load('./panels/bbd_akademi/curriculum-set.js');
  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  const toasts = [];
  const root = document.createElement('div');
  document.body.append(root);
  const app = {
    root,
    can: () => true,
    toast: (text, tone) => toasts.push([text, tone]),
    api: {
      can: () => true,
      featureOff: () => false,
      get: (path, query) => api(`${path}${qs(query)}`),
      getCached: (path, query) => api(`${path}${qs(query)}`),
      post: (path, body) => api(path, { method: 'POST', body }),
      invalidate: () => {},
    },
  };
  const out = {};
  out.years = [cs.academicYearOf('2026-09-01'), cs.academicYearOf('2026-08-31'), cs.academicYearOf('2027-01-15')];
  out.choices = cs.yearChoices('2026-10-14').map((item) => item.value);
  out.sources = [cs.sourceText({ kind: 'default' }), cs.sourceText({ kind: 'manual', carriedFromYear: '2025-2026' }),
    cs.sourceText({ kind: 'class', classGroup: { name: '8-A' } })];
  out.levelIds = cs.levelProgramIds(PROGRAMS_LIST, 'lise-11', new Set(['lise-11-matematik']));

  // ---- öğrenci seti
  const host = document.createElement('div');
  root.append(host);
  const set = cs.studentCurriculumSet(app, STUDENT_ID);
  host.append(set.node);
  await set.reload();
  await sleep(20);
  const rows = () => host.querySelectorAll('.ak-curset-row');
  out.rows = rows().map((row) => row.textContent);
  out.removeButtons = rows().map((row) => row.querySelectorAll('button').map((b) => b.textContent));
  out.buttons = host.querySelectorAll('button').map((b) => b.textContent);
  out.violations = audit(host);
  // "Geri ekle" — onaysız, {add:[fizik]}
  rows().find((row) => row.textContent.includes('Fizik')).querySelector('button').click();
  await sleep(20);
  // ÇOKLU seçici (iş 30): seviye grupları; "Seviyenin tümü" 11. sınıf → sette olan matematik atlanır
  out.levelOptions = host.querySelectorAll('.ak-prog-multi-head b').map((b) => b.textContent);
  out.ownedDisabled = host.querySelectorAll('.ak-prog-multi-item input')
    .filter((box) => box.disabled).map((box) => box.value);
  host.querySelectorAll('button').find((b) => b.getAttribute('aria-label') === '11. sınıf: seviyenin tümü').click();
  // + TYT Türkçe tek tek; çipten bırakılıp yeniden işaretlenir (seçim sırası korunur)
  const tick = (id) => {
    const box = host.querySelectorAll('.ak-prog-multi-item input').find((b) => b.value === id);
    box.checked = !box.checked;
    box.dispatchEvent(new Event('change'));
  };
  tick('tyt-turkce');
  out.chips = host.querySelectorAll('.ak-prog-chosen button').map((b) => b.textContent);
  host.querySelectorAll('.ak-prog-chosen button').find((b) => b.textContent.includes('TYT Türkçe')).click();
  out.chipsAfter = host.querySelectorAll('.ak-prog-chosen button').length;
  tick('tyt-turkce');
  out.multiViolations = audit(host);
  host.querySelectorAll('button').find((b) => b.textContent === 'Seçilenleri ekle').click();
  await sleep(20);
  out.studentPosts = calls.filter((c) => c.method === 'POST' && c.path.endsWith('/curricula')).map((c) => c.body);
  // geçmiş dönem: salt okunur
  const yearSelect = host.querySelectorAll('select').find((s) => s.getAttribute('aria-label') === 'Dönem');
  yearSelect.value = '2025-2026';
  yearSelect.dispatchEvent(new Event('change'));
  await sleep(30);
  out.pastText = host.textContent;
  out.pastButtons = host.querySelectorAll('.ak-curset-row button').length;

  // ---- sınıf müfredatı
  const host2 = document.createElement('div');
  root.append(host2);
  const klass = cs.classCurriculumSet(app, CLASS_ID);
  host2.append(klass.node);
  await klass.reload();
  await sleep(20);
  out.classText = host2.textContent;
  out.classViolations = audit(host2);
  for (const id of ['tyt-turkce', 'lise-11-fizik']) {
    const box = host2.querySelectorAll('.ak-prog-multi-item input').find((b) => b.value === id);
    box.checked = true;
    box.dispatchEvent(new Event('change'));
  }
  host2.querySelectorAll('button').find((b) => b.textContent === 'Seçilenleri ekle').click();
  await sleep(20);
  out.classPosts = calls.filter((c) => c.method === 'POST' && c.path.includes('/class-groups/')).map((c) => c.body);
  // arşivli sınıf: salt okunur
  const host3 = document.createElement('div');
  root.append(host3);
  const archived = cs.classCurriculumSet(app, CLASS_ID, { archived: true });
  host3.append(archived.node);
  await archived.reload();
  await sleep(20);
  out.archivedText = host3.textContent;
  out.archivedButtons = host3.querySelectorAll('button').length;
  out.toasts = toasts;
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    added = {**STUDENT_SET, "removedDefaults": []}
    routes = {
        f"GET /v1/admin/students/{STUDENT}/curricula?academicYear=2026-2027": STUDENT_SET,
        f"GET /v1/admin/students/{STUDENT}/curricula?academicYear=2025-2026": PAST_SET,
        f"POST /v1/admin/students/{STUDENT}/curricula": added,
        f"GET /v1/admin/class-groups/{CLASS}/curricula?academicYear=2026-2027": CLASS_SET,
        f"POST /v1/admin/class-groups/{CLASS}/curricula": CLASS_SET,
        f"GET {PROGRAMS}": {"items": [MAT11, FIZ11, TYTMAT, TYTTUR]},
    }
    scenario = (
        f"const STUDENT_ID = {json.dumps(STUDENT)};\nconst CLASS_ID = {json.dumps(CLASS)};\n"
        f"const PROGRAMS_LIST = {json.dumps([MAT11, FIZ11, TYTMAT, TYTTUR], ensure_ascii=False)};\n" + SCENARIO
    )
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_donem_ve_kaynak_etiketi(result: dict[str, Any]) -> None:
    assert result["years"] == ["2026-2027", "2025-2026", "2026-2027"]
    assert result["choices"] == ["2026-2027", "2025-2026", "2024-2025"]
    assert result["sources"] == ["Otomatik", "Elle · 2025-2026 döneminden", "Sınıftan: 8-A"]
    assert result["levelIds"] == ["lise-11-fizik"]


def test_ogrenci_seti_kaynaklar_ve_cikarilamayan_sinif_ogesi(result: dict[str, Any]) -> None:
    rows = result["rows"]
    assert "11. sınıf · Matematik" in rows[0] and "Otomatik" in rows[0]
    assert "TYT · TYT Matematik" in rows[1] and "Sınıftan: TYT Hüseyin" in rows[1]
    assert result["removeButtons"][0] == ["Çıkar"]
    assert result["removeButtons"][1] == []              # yalnız sınıftan gelen → çıkarılamaz
    assert "Varsayılana döndür" in result["buttons"]    # elle değiştirilmiş
    assert "Bu döneme ekle" in result["buttons"]        # önceki dönemden öneri
    assert result["violations"] == []


def test_ogrenci_seti_ekleme_govdeleri(result: dict[str, Any]) -> None:
    assert result["levelOptions"] == ["11. sınıf (2)", "TYT (2)"]
    # Sette olan (matematik, sınıftan TYT Matematik) seçilemez.
    assert sorted(result["ownedDisabled"]) == ["lise-11-matematik", "tyt-matematik"]
    assert result["chips"] == ["11. sınıf · Fizik ×", "TYT · TYT Türkçe ×"]
    assert result["chipsAfter"] == 1
    # "Geri ekle" + çoklu seçim TEK istekte.
    assert result["studentPosts"] == [{"add": ["lise-11-fizik"]}, {"add": ["lise-11-fizik", "tyt-turkce"]}]
    assert result["multiViolations"] == []


def test_gecmis_donem_salt_okunur(result: dict[str, Any]) -> None:
    assert "Geçmiş dönem — salt okunur" in result["pastText"]
    assert result["pastButtons"] == 0
    assert "Müfredat ekle" not in result["pastText"]


def test_sinif_mufredati_atama_ve_arsiv(result: dict[str, Any]) -> None:
    assert "Bugün 14 öğrenci etkilenir" in result["classText"]
    assert "EKLENİR, yerine geçmez" in result["classText"]
    assert "Kemal Bey" in result["classText"]
    assert result["classPosts"] == [{"add": ["tyt-turkce", "lise-11-fizik"]}]
    assert result["classViolations"] == []
    assert "Sınıf arşivde" in result["archivedText"]
    assert result["archivedButtons"] == 0
