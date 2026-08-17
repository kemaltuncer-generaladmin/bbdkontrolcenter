#!/usr/bin/env python3
"""Manifest `default_roles` ile `role_permissions` tablosunu karşılaştırır.

NEDEN VAR. Modül izinleri iki adımda yerine oturur ve ikisi de bilerek
"ekleyici"dir:

  · `km_core/security/identity.py` → `grant_defaults()` yalnızca EKLER
    (`INSERT OR IGNORE`). Yöneticinin sonradan elle kaldırdığı bir izni her
    açılışta geri getirmemek için mevcut satıra hiç dokunmaz.
  · `km_core/http/app.py` KEŞFEDİLEN HER MODÜLÜ tohumlar — `enabled: false`
    iskeletler dahil. Aksi hâlde henüz kodlanmamış bir modülün ekranı, izni
    hiç oluşmadığı için menüden süzülür ve iskelet hiç görünmez.

İkisi birlikte şu boşluğu bırakır: bir modül iskeletken izinlerini geniş ilan
edip (geliştirme kuralı: beş rolün hepsi) sonra manifestini DARALTIRSA, o
manifest bir kez açılmış makinede hiçbir şeyi geri almaz. Veritabanı ilk günkü
geniş hâlini korur; daraltma yalnızca hiç açılmamış yeni kurulumlarda geçerli
olur. Ekran menüde görünmez sanılır, oysa `/api/<id>` uçları hâlâ açıktır.

İKİNCİ BOŞLUK — SİLİNEN MODÜL. Bir modül klasörüyle birlikte kaldırıldığında
izin satırları veritabanında olduğu gibi kalır. Silinen modülün manifesti
yoktur; betik eskiden yalnızca diskte manifesti DURAN modülleri karşılaştırdığı
için bu satırları hiç görmüyordu — karşılaştırılacak bir taraf yoktu. Artık
ayrı bir kutu var: "veritabanında var, diskte manifesti yok" = YETİM MODÜL.
O satırlar hiçbir ekrana bağlı değildir, hiçbir uç onları sormaz, ama rolde
durmayı sürdürür ve rol ekranında yetki gibi görünür. Asıl tehlike modül aynı
kimlikle geri gelirse çıkar: satır zaten oradadır, yeni manifestin daraltması
yine yürürlüğe girmez ve modül ilk günün geniş hâliyle açılır.

Betik bu sapmaları gösterir. OTOMATİK BUDAMA YAPMAZ: `grant_defaults`'un
ekleyici olmasının sebebi tam da yöneticinin bilinçli elle verdiği izni sessizce
geri almamaktır; otomatik budama o kararı kırardı. Betik raporlar, insan karar
verir. Ayrıntı: docs/permissions.md → "Manifest'i daraltmak tek başına yetmez".

Kullanım:
    scripts/reconcile-permissions.py                 # rapor (varsayılan)
    scripts/reconcile-permissions.py --rapor
    scripts/reconcile-permissions.py --uygula        # her kutu ayrı açık onayla
    scripts/reconcile-permissions.py --db <yol>      # başka bir veritabanı

Çıkış kodu: 0 sapma yok · 1 elle karar bekleyen sapma var · 2 betik çalışamadı.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODULES = ROOT / "modules"

# `km_core/http/app.py` içindeki `core.store_path` varsayılanının aynısı. Ayarı
# değiştirmiş bir kurulumda yol `--db` ile verilir; betik ayar katmanlarını
# yeniden çözmez (tek kaynak çekirdektir, kopyası burada tutulmaz).
DEFAULT_DB = ROOT / "data" / "kontrol-merkezi.sqlite"

# İki ayrı onay sözcüğü bilerek FARKLIDIR. İki kutu iki ayrı karardır ve
# alışkanlıkla aynı kelimeyi yazan biri, sormadığı soruya da "evet" demiş
# olmamalı.
ONAY_SOZCUGU = "UYGULA"
ONAY_SOZCUGU_YETIM = "SIL"

# Denetim izine yazılan gerekçe etiketleri. FAZLA satırlarının metni ilk
# günkü hâliyle bırakıldı; eski kayıtlarla aynı biçimde aranabilsin diye.
IZ_FAZLA = "reconcile-permissions"
IZ_YETIM = "reconcile-permissions (yetim modül)"


@dataclass(slots=True)
class Declared:
    """Manifest'in ilan ettiği tek izin."""

    module_id: str
    key: str
    entry: str                              # role_permissions.permission karşılığı
    roles: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Manifests:
    """Diskten okunanlar. Üç ayrı küme, üç ayrı soruya cevap verir."""

    declared: dict[str, Declared] = field(default_factory=dict)
    # Manifesti okunabilen ve kimliği klasör adıyla tutan modüller. Yalnız
    # bunların izinleri manifestle KARŞILAŞTIRILABİLİR.
    module_ids: set[str] = field(default_factory=set)
    # Diskte izi bulunan her modül kimliği: klasör adları ve okunabilen her
    # manifestin `id` alanı. Yetim kararı buna bakar — bozuk bir manifest
    # yüzünden yaşayan bir modülün izinleri "silinen modül" sanılmasın.
    disk_ids: set[str] = field(default_factory=set)
    # Diskte klasörü var ama KİMLİĞİ okunamayan modüller. Bir tanesi bile
    # varsa yetim satırları silinmez: hangi kimliği ilan ettiğini bilmediğimiz
    # bir modülün izinleri yanlışlıkla yetim görünebilir.
    uncertain: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Finding:
    module_id: str
    entry: str
    role: str
    note: str = ""


@dataclass(slots=True)
class Report:
    modules: int = 0
    declared: int = 0
    problems: list[str] = field(default_factory=list)
    uncertain: set[str] = field(default_factory=set)
    excess: list[Finding] = field(default_factory=list)      # manifest daralttı, DB veriyor
    missing: list[Finding] = field(default_factory=list)     # manifest veriyor, DB'de yok
    manual: list[Finding] = field(default_factory=list)      # elle yazılmış kapsam
    orphan_key: list[Finding] = field(default_factory=list)  # modül duruyor, anahtarı bilmiyor
    orphan_module: list[Finding] = field(default_factory=list)  # modülün manifesti diskte yok


def canonical_entry(key: str, scoped: bool) -> str:
    """`grant_defaults()` hangi satırı yazıyorsa o.

    Kapsamlı izin `izin:*` olarak tohumlanır. Bu eşleme `identity.py` ile aynı
    kalmak zorundadır; ayrılırlarsa betik var olmayan sapma uydurur.
    """
    return f"{key}:*" if scoped else key


def read_core_keys() -> set[str]:
    """Çekirdeğin kendi izin anahtarları — TEK KAYNAKTAN okunur.

    Yetim kutusu "hiçbir manifest bunu ilan etmiyor" diye karar verir ve
    çekirdek izinleri (`users.view`, `database.query`, ...) de hiçbir modül
    manifestinde geçmez. Liste burada TEKRAR EDİLİRSE, çekirdeğe eklenen her
    yeni izin ertesi gün "silinmiş modül artığı" diye silinmeye aday görünür —
    yani betik, kendisini korumak için var olduğu hatayı işlerdi.

    Proje henüz kurulabilir paket değil (tests/conftest.py ile aynı durum), bu
    yüzden kaynak dizini `sys.path`e eklenir. `km_core.security.permissions`
    salt veridir: içe aktarmak ne çekirdeği ayağa kaldırır ne veritabanına
    dokunur.
    """
    src = ROOT / "backend" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from km_core.security.permissions import CORE_PERMISSION_KEYS

    return set(CORE_PERMISSION_KEYS)


def read_manifests() -> Manifests:
    """`modules/*/module.yaml` → ilan edilen izinler.

    Geçersiz manifest yalnızca kendini düşürür, raporu durdurmaz (K7 ile aynı
    huy) ve o modülün izinlerine HİÇ DOKUNULMAZ: okunamayan bir manifestten
    "bu rol artık önerilmiyor" sonucu çıkarılamaz. Şema doğrulaması burada
    YAPILMAZ: betik yetkiyi konuşur, manifest sağlığını
    `tools/build-ui-registry.py --check` ve çekirdek söyler.

    Klasör taraması manifest taramasından GENİŞTİR. Modülü çekirdek
    `modules/*/module.yaml` ile keşfeder, ama bu betik bir de "diskte hiç yok"
    kararı verecek; o karar yıkıcıdır. Manifesti silinmiş ya da bozulmuş bir
    klasör de diskte VARDIR — kimliğini okuyamasak bile izinlerini yetim ilan
    etmeyiz.
    """
    found = Manifests()

    if not MODULES.is_dir():
        # Yanlış kökten çalıştırılmış olabilir. Bunu "hiçbir modül yok" diye
        # okumak, veritabanındaki TÜM modül izinlerini yetim ilan ederdi.
        found.problems.append(f"modules/ dizini yok: {MODULES}")
        found.uncertain.add("modules/ dizini yok")
        return found

    for entry_path in sorted(MODULES.iterdir()):
        if not entry_path.is_dir():
            continue
        folder = entry_path.name
        found.disk_ids.add(folder)
        if not (entry_path / "module.yaml").is_file():
            found.problems.append(f"{folder}: klasör var ama module.yaml yok")
            found.uncertain.add(folder)

    for manifest_path in sorted(MODULES.glob("*/module.yaml")):
        folder = manifest_path.parent.name
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            found.problems.append(f"{folder}: manifest okunamadı — {error}")
            found.uncertain.add(folder)
            continue

        if not isinstance(manifest, dict):
            # Eşleme değil (liste, düz metin...). Kimliği okunamıyor demektir;
            # patlamak yerine kendini düşürür (K7) ama diskte SAYILMAYA devam
            # eder — bozuk YAML "bu modül silinmiş" anlamına gelmez.
            found.problems.append(f"{folder}: manifest bir eşleme değil")
            found.uncertain.add(folder)
            continue

        # Kimlik okunabildiyse klasör adıyla tutmasa bile diskte SAYILIR:
        # izinler `id` ile tohumlanır, klasör adıyla değil.
        declared_id = manifest.get("id")
        if isinstance(declared_id, str) and declared_id:
            found.disk_ids.add(declared_id)

        if declared_id != folder:
            found.problems.append(f"{folder}: id ('{declared_id}') klasör adıyla aynı değil")
            continue

        found.module_ids.add(folder)
        for permission in manifest.get("permissions") or []:
            key = permission.get("key")
            if not key:
                found.problems.append(f"{folder}: anahtarsız izin kaydı")
                continue
            entry = canonical_entry(key, bool(permission.get("scoped")))
            found.declared[entry] = Declared(
                module_id=folder,
                key=key,
                entry=entry,
                roles=set(permission.get("default_roles") or []),
            )

    return found


def read_grants(db_path: Path) -> list[tuple[str, str]]:
    """`role_permissions` tablosu — SALT OKUNUR açılır."""
    if not db_path.is_file():
        raise FileNotFoundError(f"veritabanı yok: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT role_id, permission FROM role_permissions ORDER BY permission, role_id"
        ).fetchall()
    finally:
        connection.close()
    return [(str(role), str(permission)) for role, permission in rows]


def compare(found: Manifests, core_keys: set[str], grants: list[tuple[str, str]]) -> Report:
    """Sapmayı beş kutuya ayırır. Yalnız ikisi elle karar bekler.

    Kutuların ayrı durmasının sebebi, her birinin FARKLI bir insan kararı
    gerektirmesi: biri manifestin uygulanmamış daraltması, biri henüz açılmamış
    çekirdek, biri yöneticinin elinin izi, biri yaşayan bir modülde artık var
    olmayan bir anahtar, sonuncusu da modülüyle birlikte gitmesi gerekirken
    kalmış bir satır. Hepsini tek listeye dökmek, dördünü yanlışlıkla silmeye
    davet olurdu.
    """
    report = Report(modules=len(found.module_ids), declared=len(found.declared))
    seen: set[tuple[str, str]] = set()

    for role, entry in grants:
        key = entry.split(":", 1)[0]
        module_id = key.split(".", 1)[0]

        if module_id not in found.module_ids:
            # Manifestle karşılaştırılamaz. Üç ihtimal var, yalnız sonuncusu
            # bu betiğin işi — ilk ikisi sessizce geçilir.
            if key in core_keys:
                continue                     # çekirdek izni; modül değil, silinmez
            if module_id in found.disk_ids:
                continue                     # modül diskte, manifesti okunamadı (problems)
            report.orphan_module.append(Finding(module_id, entry, role))
            continue

        seen.add((role, entry))
        target = found.declared.get(entry)
        if target is None:
            # Anahtar bu modülünkü ama manifest onu ilan etmiyor: ya elle
            # yazılmış, ya da kapsam biçimi (`izin` ↔ `izin:*`) değişmiş.
            other = [item for item in found.declared.values() if item.key == key]
            if other:
                report.manual.append(Finding(
                    module_id, entry, role,
                    f"manifest bu anahtarı '{other[0].entry}' olarak ilan ediyor",
                ))
            else:
                report.orphan_key.append(Finding(module_id, entry, role))
            continue

        if role not in target.roles:
            report.excess.append(Finding(module_id, entry, role))

    for entry, target in found.declared.items():
        for role in sorted(target.roles):
            if (role, entry) not in seen:
                report.missing.append(Finding(target.module_id, entry, role))

    for bucket in (report.excess, report.missing, report.manual,
                   report.orphan_key, report.orphan_module):
        bucket.sort(key=lambda item: (item.module_id, item.entry, item.role))

    return report


# ------------------------------------------------------------------- çıktı


def _print_bucket(title: str, explanation: str, findings: list[Finding]) -> None:
    print(f"\n{title} ({len(findings)})")
    print(f"  {explanation}")
    if not findings:
        print("  — yok")
        return

    grouped: dict[str, list[Finding]] = defaultdict(list)
    for finding in findings:
        grouped[finding.entry].append(finding)

    for entry in sorted(grouped):
        items = grouped[entry]
        roles = ", ".join(item.role for item in items)
        note = items[0].note
        print(f"  {entry:<34} {roles}" + (f"   ({note})" if note else ""))


def print_report(report: Report, db_path: Path, *, readonly: bool) -> None:
    kip = "salt-okunur" if readonly else "yazılabilir"
    print("Kontrol Merkezi — izin uzlaştırma raporu")
    print(f"  veritabanı : {db_path} ({kip})")
    print(f"  manifest   : {report.modules} modül, {report.declared} izin ilanı")

    for problem in report.problems:
        print(f"  atlandı — {problem}", file=sys.stderr)

    _print_bucket(
        "FAZLA — manifest daralttı, veritabanı hâlâ veriyor",
        "Geri alınabilir. Bu satırlar `grant_defaults` biçiminde yazılmış ve"
        " manifest artık o rolü önermiyor.",
        report.excess,
    )
    _print_bucket(
        "EKSİK — manifest öneriyor, veritabanında yok",
        "Elle iş gerektirmez: çekirdek bir sonraki açılışta ekler.",
        report.missing,
    )
    _print_bucket(
        "ELLE — kapsam biçimi manifestten farklı",
        "DOKUNULMAZ. Bu satır `grant_defaults` çıktısı olamaz; birileri bilerek"
        " yazmıştır.",
        report.manual,
    )
    _print_bucket(
        "YETİM ANAHTAR — modül duruyor ama anahtarı ilan etmiyor",
        "DOKUNULMAZ. Anahtar silinmiş ya da yeniden adlandırılmış olabilir;"
        " ayrımı insan yapar.",
        report.orphan_key,
    )
    _print_bucket(
        "YETİM MODÜL — veritabanında var, diskte manifesti yok",
        "Silinebilir. Modül kaldırılmış, izin satırları kalmış: hiçbir ekran"
        " sormuyor, hiçbir manifest geri getirmiyor, ama rolde duruyor.",
        report.orphan_module,
    )

    if report.orphan_module and report.uncertain:
        # Silmenin dayanağı "diskte yok" iddiasıdır; diskteki modül listesi
        # eksik okunmuşken o iddia doğrulanamaz.
        print("\nYETİM MODÜL silinemez: diskteki modül listesi tam okunamadı"
              f" ({', '.join(sorted(report.uncertain))}). Önce yukarıdaki"
              " 'atlandı' satırları giderilir.")

    actions: list[str] = []
    if report.excess:
        actions.append(f"{len(report.excess)} satır geri alınabilir (FAZLA)")
    if report.orphan_module and not report.uncertain:
        actions.append(f"{len(report.orphan_module)} satır silinebilir (YETİM MODÜL)")

    if actions:
        print("\n" + " · ".join(actions) + ":")
        print("  scripts/reconcile-permissions.py --uygula")
    else:
        print("\nGeri alınacak satır yok.")


# ------------------------------------------------------------------ uygula


def _onay(soru: str, sozcuk: str) -> bool:
    """Tek bir kutu için açık onay ister."""
    print(f"\n{soru}\nOnaylıyorsanız {sozcuk} yazın (başka her şey vazgeçer): ", end="", flush=True)
    return input().strip() == sozcuk


def apply(report: Report, db_path: Path) -> int:
    """`FAZLA` ve `YETİM MODÜL` kutularını, her birini AYRI açık onayla siler.

    İki kutu tek soruya bağlanmaz: biri manifestin daralttığı rolleri geri
    alır, diğeri artık var olmayan bir modülün artığını temizler. Biri
    istenirken diğerinin de sessizce gitmesi, bu betiğin bütün gerekçesine
    aykırı olurdu.
    """
    if not report.excess and not report.orphan_module:
        print("Geri alınacak satır yok; hiçbir şey yapılmadı.")
        return 0

    if not sys.stdin.isatty():
        # Onay kapısı bilerek İNSANA bağlı: betiğin bir zamanlayıcıya ya da
        # kuruluma bağlanması, "otomatik budama yok" kararını dolambaçlı
        # yoldan bozardı.
        print("Onay alınamıyor: bu kip etkileşimli bir terminal ister.", file=sys.stderr)
        return 2

    planned: list[tuple[Finding, str]] = []

    if report.excess and _onay(
        f"{len(report.excess)} satır silinecek — FAZLA: manifestin daralttığı roller.",
        ONAY_SOZCUGU,
    ):
        planned.extend((finding, IZ_FAZLA) for finding in report.excess)

    if report.orphan_module:
        if report.uncertain:
            print("\nYETİM MODÜL kutusu sorulmadı: diskteki modül listesi tam okunamadı"
                  f" ({', '.join(sorted(report.uncertain))}). Diskte gerçekten yok mu,"
                  " doğrulanamıyor.")
        elif _onay(
            f"{len(report.orphan_module)} satır silinecek — YETİM MODÜL: modülü kaldırılmış"
            " izinler.",
            ONAY_SOZCUGU_YETIM,
        ):
            planned.extend((finding, IZ_YETIM) for finding in report.orphan_module)

    if not planned:
        print("Vazgeçildi; hiçbir şey değişmedi.")
        return 0

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            for finding, iz in planned:
                connection.execute(
                    "DELETE FROM role_permissions WHERE role_id = ? AND permission = ?",
                    (finding.role, finding.entry),
                )
                # Denetim izi: yetki değişikliği iz bırakmadan olmaz
                # (docs/permissions.md — uygulama kuralı 4). Betiğin bir
                # oturumu yok, bu yüzden `user_id` boştur.
                connection.execute(
                    "INSERT INTO audit_log (at, user_id, action, scope, result, detail) "
                    "VALUES (?, NULL, 'roles.manage', NULL, 'ok', ?)",
                    (stamp, f"{iz}: {finding.entry} ← {finding.role}"),
                )
    finally:
        connection.close()

    silinen_fazla = sum(1 for _, iz in planned if iz == IZ_FAZLA)
    silinen_yetim = len(planned) - silinen_fazla
    print(f"\n{len(planned)} satır silindi"
          f" (FAZLA: {silinen_fazla} · YETİM MODÜL: {silinen_yetim}).")
    print("Çekirdek bunları geri getirmez: ne manifest o rolleri öneriyor,"
          " ne de silinen modülün manifesti var.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manifest izinleri ile role_permissions tablosunu uzlaştırır.",
    )
    kip = parser.add_mutually_exclusive_group()
    kip.add_argument("--rapor", action="store_true",
                     help="sapmayı listeler, hiçbir şeyi değiştirmez (varsayılan)")
    kip.add_argument("--uygula", action="store_true",
                     help="manifestin daralttığı ve silinen modülden kalan izinleri,"
                          " her kutuyu ayrı açık onayla siler")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"çekirdek veritabanı (varsayılan: {DEFAULT_DB})")
    args = parser.parse_args()

    found = read_manifests()
    try:
        core_keys = read_core_keys()
    except ImportError as error:
        # Çekirdek kataloğu okunamadan yetim kararı verilemez: `users.view` de
        # hiçbir manifestte yoktur ve silinmeye aday görünürdü.
        print(f"Çekirdek izin kataloğu okunamadı: {error}", file=sys.stderr)
        return 2

    try:
        grants = read_grants(args.db)
    except (OSError, sqlite3.Error) as error:
        print(f"Veritabanı okunamadı: {error}", file=sys.stderr)
        return 2

    report = compare(found, core_keys, grants)
    report.problems = found.problems
    report.uncertain = found.uncertain
    print_report(report, args.db, readonly=not args.uygula)

    if args.uygula:
        return apply(report, args.db)
    return 1 if report.excess or report.orphan_module else 0


if __name__ == "__main__":
    raise SystemExit(main())
