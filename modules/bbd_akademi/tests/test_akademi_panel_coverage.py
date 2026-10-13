"""Panel kapsamı: Akademi'nin her yönetim işi KM'den yapılabiliyor mu?

Kullanıcı kararı: KM en yetkili uygulamadır; Akademi'nin her yönetim işi
buradan yapılabilir. Bu dosya bunu bağlar:

* izin listesindeki her işlemin panelde karşılığı vardır (yazma = işlem
  kimliği, okuma = kimlik ya da yol) ya da `NOT_SURFACED` tablosunda
  gerekçesiyle durur;
* panelde yazılan her işlem kimliği ve her `/v1/...` yolu GERÇEKTEN izin
  listesinde vardır — yazım hatası çalışma anında 403 olurdu ve hiçbir test
  görmezdi;
* bölüm kaydı (`index.js` → `SECTIONS`) ile dosyalar ve izinler tutarlıdır.

Tarayıcı JS motoru gerektirmez; aranan şey kaynaktaki bildirimin biçimidir.
Tarayıcının kendisi de sınanır (`test_tarayici_gercekten_ayirt_eder`): boş
dönen bir tarayıcı bütün testleri yalancı yeşile çevirirdi.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import yaml
from modules.bbd_akademi.backend.allowlist import OPERATIONS
from modules.bbd_akademi.backend.operations import Operation
from modules.bbd_akademi.backend.permission_map import ALL_PERMISSIONS

MODULE = Path(__file__).resolve().parents[1]
PANEL = MODULE / "ui" / "panel"


def _tool():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("bbd_akademi_ui_coverage",
                                                  MODULE / "tools" / "ui_coverage.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclass çözümlemesi modülü sys.modules'ta arar.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


coverage = _tool()
IDS = {op.operation_id for op in OPERATIONS}


# ------------------------------------------------------------------ kapsam


def test_her_islem_panelde_ya_da_gerekceli() -> None:
    missing = coverage.uncovered(coverage.scan())
    assert missing == [], "panelde karşılığı olmayan işlemler:\n" + "\n".join(
        f"  {hit.operation.method} {hit.operation.path} ({hit.operation.operation_id})"
        for hit in missing)


def test_bilerek_yok_tablosu_gecerli() -> None:
    hits = {hit.operation.operation_id: hit for hit in coverage.scan()}
    for name, table in (("NOT_SURFACED", coverage.NOT_SURFACED), ("PENDING", coverage.PENDING)):
        for operation_id, reason in table.items():
            assert operation_id in IDS, f"{operation_id}: izin listesinde yok — {name}'den çıkarın"
            assert len(reason.strip()) >= 30, f"{operation_id}: gerekçe yazılmalı"
            assert not hits[operation_id].covered, (
                f"{operation_id} artık panelde var — {name}'den çıkarın")
    assert not set(coverage.NOT_SURFACED) & set(coverage.PENDING), "işlem iki tabloda birden"



def test_tarayici_gercekten_ayirt_eder() -> None:
    write = Operation("POST", "/v1/admin/exams", "admin_createExam", ("exams.manage",), "both")
    read = Operation("GET", "/v1/admin/exams/{examId}/results", "admin_listExamResults",
                     ("exams.view",), "both")
    ops = (write, read)

    def covered(source: str) -> dict[str, bool]:
        sources = {"x.js": coverage.strip_comments(source)}
        return {hit.operation.operation_id: hit.covered for hit in coverage.scan(sources, ops)}

    # Yalnız yol: okuma karşılanır, yazma KARŞILANMAZ (aynı yolun GET'i olabilir).
    assert covered("api.post('/v1/admin/exams', body); api.get(`/v1/admin/exams/${id}/results`)") \
        == {"admin_createExam": False, "admin_listExamResults": True}
    # Kimlik düğmede: yazma karşılanır.
    assert covered("actionButton(app, 'admin_createExam', 'Ekle')")["admin_createExam"] is True
    # Yorumdaki ad sayılmaz.
    assert covered("// admin_createExam '/v1/admin/exams/{x}/results'\n/* 'admin_createExam' */") \
        == {"admin_createExam": False, "admin_listExamResults": False}
    # Daha uzun bir yolun öneki eşleşme sayılmaz.
    assert covered("api.get(`/v1/admin/exams/${id}/results-archive`)")["admin_listExamResults"] \
        is False


# --------------------------------------------------------- yazım hataları


def _sources() -> dict[str, str]:
    return coverage.panel_sources()


_ID_USE = re.compile(
    r"(?:actionButton\(\s*app\s*,|\.can\(|stepUp\(|pinStepUp\(\s*app\s*,|operation\()\s*"
    r"['\"`]([A-Za-z_]+)['\"`]")
_PATH = re.compile(r"['\"`](/v1/[^'\"`?\s]*)")


def test_paneldeki_islem_kimlikleri_listede() -> None:
    unknown = sorted({(name, match) for name, text in _sources().items()
                      for match in _ID_USE.findall(text) if match not in IDS})
    assert unknown == [], f"izin listesinde olmayan işlem kimlikleri: {unknown}"


def test_paneldeki_yollar_listede() -> None:
    """Paneldeki her `/v1/...` dizgesi bir işlemin yolu (ya da yol öneki) olmalı.

    Şablon parçası (`${id}`) tek yol parçasına karşılık gelir; önek kullanımı
    (`invalidate('/v1/admin/class-groups')`) bir işlem yolunun başıysa geçerlidir.
    """
    templates = sorted({re.sub(r"\{[A-Za-z0-9]+\}", "{}", op.path) for op in OPERATIONS})
    unknown: set[tuple[str, str]] = set()
    for name, text in _sources().items():
        for raw in _PATH.findall(text):
            pattern = re.sub(r"\$\{[^}]*\}", "{}", raw).rstrip("/")
            regex = re.compile(re.escape(pattern).replace(r"\{\}", "[^/]+") + r"(?:/|$)")
            if not any(regex.match(template) for template in templates):
                unknown.add((name, raw))
    assert sorted(unknown) == [], f"izin listesinde karşılığı olmayan yollar: {sorted(unknown)}"


# ------------------------------------------------------------ bölüm kaydı


def _sections() -> list[dict[str, object]]:
    # Bilgi mimarisi `sections.js`te (import'suz; index.js yeniden dışa verir).
    source = coverage.strip_comments((PANEL / "sections.js").read_text(encoding="utf-8"))
    block = source[source.index("export const SECTIONS"):]
    block = block[: block.index("];") + 2]
    rows = []
    for match in re.finditer(r"\{\s*key:\s*'([^']+)'.*?file:\s*'([^']+)'.*?requires:\s*\[([^\]]*)\]"
                             r"(.*?)\}", block, re.DOTALL):
        rest = match.group(4)
        area = re.search(r"area:\s*'([^']+)'", rest)
        feature = re.search(r"feature:\s*'([^']+)'", rest)
        rows.append({"key": match.group(1), "file": match.group(2),
                     "requires": re.findall(r"'([^']+)'", match.group(3)),
                     "area": area.group(1) if area else None,
                     "feature": feature.group(1) if feature else None})
    return rows


def _areas() -> list[str]:
    source = coverage.strip_comments((PANEL / "sections.js").read_text(encoding="utf-8"))
    block = source[source.index("export const AREAS"):]
    block = block[: block.index("];") + 2]
    return re.findall(r"\{\s*key:\s*'([^']+)'", block)


def test_bolum_kaydi_dosyalar_ve_izinler() -> None:
    sections = _sections()
    assert len(sections) >= 14, "SECTIONS okunamadı — ayrıştırıcı kaymış olabilir"
    manifest = yaml.safe_load((MODULE / "module.yaml").read_text(encoding="utf-8"))
    declared = {item["key"] for item in manifest["permissions"]}
    assert set(ALL_PERMISSIONS) == declared, "module.yaml ile permission_map.ALL_PERMISSIONS ayrışmış"
    keys = [section["key"] for section in sections]
    assert len(keys) == len(set(keys)), "aynı bölüm anahtarı iki kez"
    for section in sections:
        path = PANEL / str(section["file"]).removeprefix("./")
        assert path.exists(), f"{section['key']}: {path.name} yok"
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^export function section\(app\)", text, re.MULTILINE), (
            f"{path.name}: `export function section(app)` yok")
        for short in section["requires"]:  # type: ignore[union-attr]
            assert f"bbd_akademi.{short}" in declared, f"{section['key']}: bilinmeyen izin {short}"


def test_bilgi_mimarisi_alti_ust_bolum() -> None:
    """Sadeleştirme (13.10.2026): menüde en fazla altı üst bölüm; her ekran birine bağlı,
    hiçbir üst bölüm boş değil; `feature` gerçek bir Akademi özellik bayrağı."""
    areas = _areas()
    assert 1 <= len(areas) <= 6, f"üst bölüm sayısı {len(areas)} — en fazla 6"
    sections = _sections()
    for section in sections:
        assert section["area"] in areas, f"{section['key']}: üst bölümü yok ya da bilinmiyor"
    assert {section["area"] for section in sections} == set(areas), "boş üst bölüm var"
    flags = {op.feature_flag for op in OPERATIONS if op.feature_flag}
    for section in sections:
        if section["feature"]:
            assert section["feature"] in flags, f"{section['key']}: bilinmeyen özellik {section['feature']}"
    # Kantin ve kurum carisi ayrı ekranlardır (A12) ve kendi bayraklarına bağlıdır.
    by_key = {section["key"]: section for section in sections}
    assert by_key["finance"]["feature"] == "finance" and by_key["canteen"]["feature"] == "canteen"


def test_bolum_css_oneki() -> None:
    """Panel CSS'i `document.head`'e eklenir ve kalkmaz: her seçici `ak-` ile kapsanır.

    Her seçicinin İLK sınıfı `ak`/`kit-` önekli olmalı (`.ak-plan-heat.low`,
    `.ak-res-topic-pick > .pk-box` geçer; çıplak `.low` ya da `.pk-box {…}`
    bütün kabuğa sızardı). Durum sınıfları (`.low`, `.dirty`) yalnız önekli
    bir sınıfa eklenerek kullanılır.
    """
    bad: list[str] = []
    for path in sorted(PANEL.glob("*.css")):
        text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.DOTALL)
        text = re.sub(r"@(media|supports)[^{]*\{", "", text)
        for block in re.findall(r"([^{}]+)\{", text):
            for selector in block.split(","):
                classes = re.findall(r"\.([A-Za-z][\w-]*)", selector)
                if classes and not classes[0].startswith(("ak", "kit-")):
                    bad.append(f"{path.name}: {selector.strip()}")
    assert bad == [], f"öneksiz seçiciler: {sorted(set(bad))}"
