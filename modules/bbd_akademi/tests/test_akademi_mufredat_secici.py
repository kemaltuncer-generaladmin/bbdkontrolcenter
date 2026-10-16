"""Müfredat program / konu seçicisi (`ui/panel/curriculum-picker.js`) — Node'da küçük DOM üzerinde.

SORUN (13.10.2026, kullanıcı): "Konu bulunamadı diyor. Lise grubu seçince program olarak 5. sınıftan
başlatıyor." Program başlıkları yalnız ders adıdır (13 ayrı "Matematik"); liste kimlik sırasıyla
(`ortaokul-5-*` başta) geliyor ve seçici hedefin seviyesini bilmiyordu. Bu test seçicinin:

* eski Akademi kimlik sırasıyla verse de seviye sırasıyla dizdiğini (5 → 8, LGS, 9 → 12, TYT, AYT),
* etiketin seviyeyi taşıdığını ("11. sınıf · Matematik", "AYT · Matematik (Sayısal / Eşit ağırlık)"),
* hedef (sınıf) verildiğinde sorguya hedefi koyup önerilen programı kendiliğinden seçtiğini,
  hedefin seviye özetini yazdığını; seviyesi bilinmeyen hedefte yöneticiyi seviye seçmeye yönelttiğini,
* konu ağacının ünite başlıkları altında yalnız KONU kimliği seçtirdiğini (ünite / program kimliği
  `topicIds`'e girmez — "Konu bulunamadı"nın KM ayağı), boş programda yol gösterdiğini,
* erişilebilirlik denetiminin temiz olduğunu doğrular. `node` yoksa atlanır.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "a11y"))

from harness import NodeMissing, run

PROGRAMS = "/v1/admin/curricula/programs"


def _program(pid: str, *, grade: Any = None, exam: str | None = None, level: tuple[str, str, int] | None = None,
             recommended: bool = False, tracks: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": pid, "kind": "exam" if exam else "school", "exam": exam, "grade": grade,
                           "tracks": tracks or [], "subject": {"id": "matematik", "name": "Matematik"},
                           "title": "Matematik", "scope": "global", "unitCount": 2, "topicCount": 3,
                           "recommended": recommended}
    if level:
        out.update(levelKey=level[0], levelLabel=level[1], levelOrder=level[2])
    return out


# Eski Akademi (seviye alanı yok) kimlik sırası: ortaokul-5 başta.
OLD = [
    _program("ayt-matematik", exam="AYT", tracks=["SAY", "EA"]),
    _program("lgs-matematik", exam="LGS"),
    _program("lise-11-matematik", grade=11),
    _program("ortaokul-5-matematik", grade=5),
    _program("tyt-matematik", exam="TYT"),
]
# Yeni Akademi: seviye alanları + hedef özeti + önerilen.
NEW = {
    "items": [
        _program("ortaokul-5-matematik", grade=5, level=("ortaokul-5", "5. sınıf", 10)),
        _program("lise-11-matematik", grade=11, level=("lise-11", "11. sınıf", 70), recommended=True),
        _program("tyt-matematik", exam="TYT", level=("tyt", "TYT", 90)),
        _program("ayt-matematik", exam="AYT", tracks=["SAY", "EA"], level=("ayt", "AYT", 100), recommended=True),
    ],
    "target": {"studentCount": 27, "unknownLevelCount": 3,
               "levels": [{"levelKey": "lise-11", "label": "11. sınıf · SAY · YKS", "studentCount": 24}]},
}
UNKNOWN = {
    "items": [
        _program("ortaokul-5-matematik", grade=5, level=("ortaokul-5", "5. sınıf", 10)),
        _program("tyt-matematik", exam="TYT", level=("tyt", "TYT", 90)),
    ],
    "target": {"studentCount": 4, "unknownLevelCount": 4, "levels": []},
}
DETAIL = {
    "id": "lise-11-matematik", "title": "Matematik",
    "units": [
        {"id": "lise-11-matematik-u2", "title": "Fonksiyonlar", "order": 2, "topics": [
            {"id": "lise-11-matematik-u2-t1", "title": "Fonksiyon grafikleri", "order": 1},
        ]},
        {"id": "lise-11-matematik-u1", "title": "Trigonometri", "order": 1, "topics": [
            {"id": "lise-11-matematik-u1-t2", "title": "Toplam-fark formülleri", "order": 2},
            {"id": "lise-11-matematik-u1-t1", "title": "Yönlü açılar", "order": 1},
            {"id": "lise-11-matematik-u1-eski", "title": "Eski konu", "order": 3, "retired": True},
        ]},
        {"id": "lise-11-matematik-bos", "title": "Boş ünite", "order": 3, "topics": []},
    ],
}
EMPTY = {"id": "kurum-bos", "title": "Kurum programı", "units": []}

SCENARIO = r"""
export default async function ({ document, audit, sleep, api, load, calls }) {
  const picker = await load('./panels/bbd_akademi/shared/curriculum-picker.js');
  const qs = (query) => {
    const parts = Object.entries(query || {}).filter(([, v]) => v !== undefined && v !== '')
      .map(([k, v]) => `${k}=${v}`);
    return parts.length ? `?${parts.join('&')}` : '';
  };
  let mode = 'new';
  globalThis.__apiHook = (method, path) => {
    if (method !== 'GET' || !path.startsWith('/v1/admin/curricula/programs?')) return undefined;
    if (mode === 'unknown') return ROUTES_UNKNOWN;
    return undefined;
  };
  const app = { api: { getCached: (path, query) => api(`${path}${qs(query)}`) } };
  const out = {};

  // 1) eski sunucu: sıralama + etiket
  out.sorted = picker.sortPrograms(OLD).map((p) => p.id);
  out.labels = picker.sortPrograms(OLD).map((p) => picker.programLabel(p));
  out.groups = picker.programGroups(OLD).map((g) => g.label);

  // 2) hedefli seçici: öneri kendiliğinden seçilir
  const host = document.createElement('div');
  document.body.append(host);
  const changes = [];
  const programs = picker.programPicker(app, { onChange: (id) => changes.push(id) });
  host.append(programs.node);
  await programs.load({ subjectId: 'matematik', target: { classGroupIds: ['g-11a'], studentGroupIds: [], studentIds: [] } });
  out.query = calls.filter((c) => c.path.startsWith('/v1/admin/curricula/programs?')).map((c) => c.path);
  out.picked = programs.value;
  out.changes = changes;
  out.info = host.querySelector('.ak-prog-target').textContent;
  const select = host.querySelector('select');
  out.optgroups = select.querySelectorAll('optgroup').map((g) => g.label);
  out.levelChips = host.querySelectorAll('.ak-prog-levels button').map((b) => [b.textContent, b.getAttribute('aria-pressed')]);
  out.violations = audit(host);

  // 3) seviyesi bilinmeyen hedef: öneri yok, yönetici seviye seçer
  mode = 'unknown';
  const host2 = document.createElement('div');
  document.body.append(host2);
  const unknown = picker.programPicker(app, {});
  host2.append(unknown.node);
  await unknown.load({ subjectId: 'matematik', target: { classGroupIds: [], studentGroupIds: [], studentIds: ['s1'] } });
  out.unknownPicked = unknown.value;
  out.unknownInfo = host2.querySelector('.ak-prog-target').textContent;

  // 4) konu ağacı: ünite sırası, yalnız konu kimliği, boş program
  const selected = new Map();
  let changed = 0;
  const tree = picker.topicTree(app, { selected, onChange: () => { changed += 1; } });
  const host3 = document.createElement('div');
  document.body.append(host3);
  host3.append(tree.node);
  await tree.load('lise-11-matematik');
  out.units = host3.querySelectorAll('.ak-hw-unit-title').map((n) => n.textContent);
  out.chips = host3.querySelectorAll('.ak-hw-unit button').map((b) => b.textContent);
  host3.querySelectorAll('.ak-hw-unit button')[0].dispatchEvent(new Event('click', { bubbles: true }));
  out.selected = [...selected.keys()];
  out.changed = changed;
  out.treeViolations = audit(host3);
  await tree.load('kurum-bos');
  out.emptyText = host3.textContent;
  await tree.load('');
  out.noProgramText = host3.textContent;
  return out;
}
"""


@pytest.fixture(scope="module")
def result() -> dict[str, Any]:
    import json

    routes = {
        f"GET {PROGRAMS}": NEW,
        f"GET {PROGRAMS}/lise-11-matematik": DETAIL,
        f"GET {PROGRAMS}/kurum-bos": EMPTY,
    }
    scenario = (
        f"const OLD = {json.dumps(OLD, ensure_ascii=False)};\n"
        f"const ROUTES_UNKNOWN = {json.dumps(UNKNOWN, ensure_ascii=False)};\n" + SCENARIO
    )
    try:
        data = run(scenario, panels=("bbd_akademi",), routes=routes)
    except NodeMissing:
        pytest.skip("node kurulu değil")
    assert data["errors"] == [], data["errors"]
    out: dict[str, Any] = data["result"]
    return out


def test_eski_sunucu_kimlik_sirasi_seviye_sirasina_dizilir(result: dict[str, Any]) -> None:
    assert result["sorted"] == [
        "ortaokul-5-matematik", "lgs-matematik", "lise-11-matematik", "tyt-matematik", "ayt-matematik"]
    assert result["groups"] == ["5. sınıf", "LGS", "11. sınıf", "TYT", "AYT"]


def test_etiket_seviyeyi_tasir_ayni_adli_programlar_ayrisir(result: dict[str, Any]) -> None:
    assert result["labels"] == [
        "5. sınıf · Matematik", "LGS · Matematik", "11. sınıf · Matematik", "TYT · Matematik",
        "AYT · Matematik (Sayısal / Eşit ağırlık)"]
    assert len(set(result["labels"])) == len(result["labels"])


def test_hedef_sorguya_gider_onerilen_kendiliginden_secilir(result: dict[str, Any]) -> None:
    assert result["query"] == [f"{PROGRAMS}?subject=matematik&classGroupIds=g-11a"]
    # 5. sınıf değil, sınıfın seviyesine uyan ilk önerilen program
    assert result["picked"] == "lise-11-matematik"
    assert result["changes"] == ["lise-11-matematik"]
    assert result["optgroups"][0] == "Önerilen (hedefin seviyesi)"


def test_hedef_ozeti_ve_seviyesiz_ogrenci_uyarisi(result: dict[str, Any]) -> None:
    assert "11. sınıf · SAY · YKS (24)" in result["info"]
    assert "3 öğrencinin sınıfına seviye girilmemiş" in result["info"]
    assert result["levelChips"][0] == ["Tüm seviyeler", "true"]  # öneri iki seviyede → süzgeç yok
    assert [text for text, _ in result["levelChips"]] == ["Tüm seviyeler", "5. sınıf", "11. sınıf", "TYT", "AYT"]


def test_seviyesi_bilinmeyen_hedefte_secim_yoneticide(result: dict[str, Any]) -> None:
    assert result["unknownPicked"] == ""
    assert "Hedefin seviyesi bilinmiyor" in result["unknownInfo"]
    assert "seviyeyi seçin" in result["unknownInfo"]
    assert "TYT / AYT" in result["unknownInfo"]


def test_konu_agaci_yalniz_konu_kimligi_secer(result: dict[str, Any]) -> None:
    assert result["units"] == ["Trigonometri", "Fonksiyonlar"]  # sıra; boş ünite gizli
    assert result["chips"] == ["Yönlü açılar", "Toplam-fark formülleri", "Fonksiyon grafikleri"]  # emekli yok
    assert result["selected"] == ["lise-11-matematik-u1-t1"]
    assert result["changed"] == 1


def test_bos_durumlar_yol_gosterir(result: dict[str, Any]) -> None:
    assert "Bu programda konu yok" in result["emptyText"]
    assert "başka seviyeden program" in result["emptyText"]
    assert "Konu seçmek için program seçin" in result["noProgramText"]


def test_erisilebilirlik_temiz(result: dict[str, Any]) -> None:
    assert result["violations"] == []
    assert result["treeViolations"] == []
