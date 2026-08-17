#!/usr/bin/env python3
"""Kabuk menüsünün okuduğu kaydı üretir.

Kabuk modül adı bilmez (K1'in arayüz tarafındaki karşılığı). Menüyü
`modules/*/module.yaml` içindeki `ui.nav` bloklarından türetiriz; bu betik o
blokları toplayıp `apps/desktop/shell/registry.json` dosyasına yazar.

GEÇİCİ OLAN NE: kaydın kaynağı. Çekirdek ayağa kalkınca `ui-kernel` aynı
JSON'u sidecar'ın modül kayıt ucundan çekecek; bu betik o gün silinir.
Manifest formatı, menü mantığı ve kabuk kodu değişmez.

BURASI ÇALIŞILAN PLATFORMA GÖRE ELER (ADR 0022). Çekirdek keşifte eliyor;
üretici elemiyordu ve fark SESSİZDİ: Windows'ta `/modules` antivirüs ekranını
hiç saymazken aynı depodan üretilen `registry.json` onu yazmayı sürdürüyordu.
Kural TEK YERDE durur — `km_core.kernel.platforms` buradan da okunur, ikinci
bir kopya yazılmaz (bu deponun tekrar tekrar ödediği bedel budur).

Sonucu: çıktı üretildiği makinenin platformunu anlatır. `--check` de aynı
platformda koşmalıdır; başka bir platformda üretilmiş bir dosyayla
karşılaştırmak farkı "güncel değil" diye okur — doğru cevaptır.

Kullanım:
    python3 tools/build-ui-registry.py [--check]

    --check   dosyayı yazmaz, yalnızca doğrular (CI için)
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

import yaml

# ÇIKTI UTF-8'DİR, KONSOLUN KOD SAYFASI NE OLURSA OLSUN. Windows'ta Python'un
# stdout'u varsayılan olarak konsolun kod sayfasını (cp1252) kullanır ve
# `errors="strict"` çalışır: bu betiğin bastığı Türkçe metin kodlanamayınca
# `print()` UnicodeEncodeError atar, betik 1 ile döner ve paket derlemesi
# "Menu kaydi uretilemedi" diyerek düşer (run 32038408984 — `→`, cp1252'de yok).
#
# İŞARET ASCII'YE DÜŞÜRÜLEREK KAÇILMAZ. Kök neden tek bir karakter değil, çıktı
# kodlamasıdır: `→` sadeleştirilseydi sıradaki "çalışmaz"ın `ı`/`ş` harfi aynı
# yerde patlardı. Kodlama bir kez burada sabitlenir; böylece betiği kim çağırırsa
# çağırsın (iş akışı, .ps1, elle) davranış aynıdır.
#
# stderr de zorlanır: oradaki eleme notları (`elendi — antivirüs …`) varsayılan
# `backslashreplace` yüzünden patlamaz ama okunamaz hâle gelirdi.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:  # yakalanmış akış (test) reconfigure taşımaz
        _reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"
SCHEMA = ROOT / "docs" / "schemas" / "module.schema.json"
SHELL = ROOT / "apps" / "desktop" / "shell"
OUT = SHELL / "registry.json"

# Platform elemesinin kuralı çekirdekte tanımlıdır; burada YENİDEN YAZILMAZ.
# Proje henüz kurulabilir paket değil (bkz. tests/conftest.py), bu yüzden
# kaynak dizini sys.path'e eklenir.
sys.path.insert(0, str(ROOT / "backend" / "src"))
from km_core.kernel.platforms import current_platform, runs_on, skip_note

# Modül panelleri buraya kopyalanır: webview yalnızca `shell/` kökünü görüyor,
# paket toplayıcı da yok. Kaynak her zaman modülün kendi klasörüdür (K6);
# burası üretilen çıktıdır, git dışıdır.
PANELS_OUT = SHELL / "panels"

# Menü gruplarının sırası SABİT DEĞİL, VERİDEN TÜRETİLİR: bir grubun sırası,
# içindeki panellerin en küçük `ui.nav.order` değeridir.
#
# BURADA `GROUP_ORDER = ["BBD", "BBD Store", …]` diye sabit bir liste duruyordu
# ve AYNISI `apps/desktop/shell/ui-kernel.js` içinde ikinci kez yazılıydı. İki
# kopya birbirinden habersizdi; yalnız biri güncellenirse build çıktısı ile
# çalışma zamanı sessizce ayrışırdı. Ayrıca grup adlarını burada tutmak, yeni
# bir grup açmayı çekirdek dosyasına dokunmaya bağlıyordu (K6'ya ters).
#
# Artık grup açmak saf `module.yaml` işidir: `ui.nav.group` adı, `ui.nav.order`
# nereye düşeceğini söyler.

# ÇEKİRDEK EKRANLARI BURADA YOKTUR (ADR 0017 §1). Kullanıcılar, Ayarlar,
# Sistem Sağlığı gibi ekranlar modül değildir: manifestleri yoktur, silinemezler
# ve `modules/` klasörü tümüyle silinse bile çalışmaları gerekir. Bu dosya
# manifest tarayıcısıdır; modül olmayan bir şeyi modülmüş gibi yazmak, hem
# manifest doğrulamasını anlamsızlaştırır hem de çekirdek ekranı `modules/`
# ile aynı kaderi paylaşan bir şeye çevirirdi.
#
# Çekirdek ekranlarının sabit listesi kabuktadır: `shell/ui-kernel.js` →
# `CORE_PANELS`. Dosyaları da `shell/core-panels/<ad>/` altındadır; buradan
# kopyalanmazlar, doğrudan servis edilirler.


def load_schema_validator():
    """Şema doğrulayıcı. jsonschema yoksa doğrulama atlanır, üretim sürer."""
    try:
        import jsonschema
    except ImportError:
        return None
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema)


def copy_panel(module_dir: pathlib.Path, entry: str | None) -> str | None:
    """Modülün panel klasörünü servis köküne kopyalar, giriş yolunu döndürür.

    Panel yoksa None döner: ekran menüde görünür, gövdesi boş kalır. Modüller
    aşama aşama kodlandığı için normal durum budur.
    """
    if not entry:
        return None

    source_file = module_dir / entry
    if not source_file.is_file():
        return None

    source_dir = source_file.parent
    target_dir = PANELS_OUT / module_dir.name
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=True)
    return f"panels/{module_dir.name}/{source_file.name}"


def collect_panels(validator, platform: str) -> tuple[list[dict], list[str], list[str]]:
    panels: list[dict] = []
    problems: list[str] = []
    skipped: list[str] = []

    for manifest_path in sorted(MODULES.glob("*/module.yaml")):
        folder = manifest_path.parent.name
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            problems.append(f"{folder}: manifest okunamadı — {exc}")
            continue

        # Geçersiz manifest yalnızca o modülü düşürür, üretimi durdurmaz.
        if validator is not None:
            errors = sorted(validator.iter_errors(manifest), key=lambda e: e.path)
            if errors:
                problems.append(f"{folder}: {errors[0].message}")
                continue

        if manifest.get("id") != folder:
            problems.append(f"{folder}: id ('{manifest.get('id')}') klasör adıyla aynı değil")
            continue

        # PLATFORM ELEMESİ, ÇEKİRDEKLE AYNI YERDE: menüye girmeyen modülün
        # paneli de yazılmaz ve dosyaları da kopyalanmaz (ADR 0022 §2).
        # `problems` değil `skipped`: eleme bir arıza değil, ilan edilmiş bir
        # karardır — aksi hâlde her Windows derlemesi "sorun var" derdi.
        if not runs_on(manifest, platform):
            skipped.append(skip_note(str(manifest.get("id", folder)), manifest))
            continue

        nav = (manifest.get("ui") or {}).get("nav")
        if not nav:
            continue  # ekranı olmayan modül — menüde yeri yok

        panel = {
            "id": manifest["id"],
            "title": nav.get("title") or manifest.get("name", manifest["id"]),
            "icon": nav.get("icon", "dot"),
            "group": nav.get("group", "Diğer"),
            "order": nav.get("order", 1000),
            "requires": nav.get("requires", []),
            "source": "module",
            "enabled": bool(manifest.get("enabled", True)),
            # Modülün ilan ettiği yetenekler. Kabuk yalnızca burada yazanı
            # kayda alır: sözleşme manifesttir (K3).
            "provides": [entry["capability"] for entry in manifest.get("provides") or []],
        }

        entry = copy_panel(manifest_path.parent, (manifest.get("ui") or {}).get("entry"))
        if entry:
            panel["entry"] = entry

        panels.append(panel)

    return panels, problems, skipped


def group_ranks(panels: list[dict]) -> dict[str, int]:
    """Grup adı → sırası. Sıra, gruptaki en küçük `order` değeridir."""
    ranks: dict[str, int] = {}
    for panel in panels:
        group = panel["group"]
        if group not in ranks or panel["order"] < ranks[group]:
            ranks[group] = panel["order"]
    return ranks


def build(*, copy_panels: bool = True, platform: str | None = None) -> dict:
    """Kayıt defterini üretir. `copy_panels=False` iken DİSKE DOKUNMAZ.

    BULUNAN HATA (2026-08-15). `--check` "dosyayı yazmaz" diye belgelenmişti
    ama `build()` koşulsuz `rmtree(PANELS_OUT)` yapıp panelleri yeniden
    kopyalıyordu. Sentinel dosyayla kanıtlandı: `--check` çalıştırınca
    `shell/panels/` altına konan dosya siliniyordu.

    CI'da salt-okunur sanılan bir komutun üretilmiş panelleri silmesi, hem
    sözleşme ihlali hem de yarış kaynağı: doğrulama koşarken uygulama açıksa
    panel klasörü bir an için boşalır.

    `platform` dışarıdan verilebilir: eleme davranışı, üzerinde koşulan
    makineye bağlı kalmadan sınanabilsin (çekirdekteki `Kernel` ile aynı
    gerekçe, ADR 0022).
    """
    if copy_panels:
        # Silinen/yeniden adlandırılan panel artığı kalmasın.
        shutil.rmtree(PANELS_OUT, ignore_errors=True)

    validator = load_schema_validator()
    panels, problems, skipped = collect_panels(validator, platform or current_platform())

    for problem in problems:
        print(f"  atlandı — {problem}", file=sys.stderr)
    # Eleme SESSİZ OLMAZ: ekranını arayan yönetici nedenini burada da bulur.
    for note in skipped:
        print(f"  elendi — {note}", file=sys.stderr)

    ranks = group_ranks(panels)
    # AYNI SIRA DEĞERİNDE AD KAZANIR: iki grup aynı `order`ı taşırsa sıra
    # rastgele kalmasın (eskiden `store_bundles` ve `store_shipping` ikisi de
    # 30'du ve menü sırası dosya sistemi sırasına kalıyordu).
    panels.sort(key=lambda p: (ranks[p["group"]], p["group"], p["order"], p["title"]))
    groups = sorted({p["group"] for p in panels}, key=lambda g: (ranks[g], g))

    return {
        "generated_by": "tools/build-ui-registry.py",
        "groups": groups,
        "panels": panels,
    }


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    # `--check` DİSKE DOKUNMAZ: panelleri kopyalamaz, klasörü silmez.
    registry = build(copy_panels=not check_only)
    payload = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"

    if check_only:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != payload:
            print("registry.json güncel değil — betiği çalıştırın.", file=sys.stderr)
            return 1
        print("registry.json güncel.")
        return 0

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(payload, encoding="utf-8")
    print(f"{len(registry['panels'])} ekran, {len(registry['groups'])} grup → {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
