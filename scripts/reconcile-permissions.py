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

Betik bu sapmayı gösterir. OTOMATİK BUDAMA YAPMAZ: `grant_defaults`'un
ekleyici olmasının sebebi tam da yöneticinin bilinçli elle verdiği izni sessizce
geri almamaktır; otomatik budama o kararı kırardı. Betik raporlar, insan karar
verir. Ayrıntı: docs/permissions.md → "Manifest'i daraltmak tek başına yetmez".

Kullanım:
    scripts/reconcile-permissions.py                 # rapor (varsayılan)
    scripts/reconcile-permissions.py --rapor
    scripts/reconcile-permissions.py --uygula        # yalnız açık onayla
    scripts/reconcile-permissions.py --db <yol>      # başka bir veritabanı

Çıkış kodu: 0 sapma yok · 1 geri alınabilir sapma var · 2 betik çalışamadı.
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

ONAY_SOZCUGU = "UYGULA"


@dataclass(slots=True)
class Declared:
    """Manifest'in ilan ettiği tek izin."""

    module_id: str
    key: str
    entry: str                              # role_permissions.permission karşılığı
    roles: set[str] = field(default_factory=set)


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
    excess: list[Finding] = field(default_factory=list)      # manifest daralttı, DB veriyor
    missing: list[Finding] = field(default_factory=list)     # manifest veriyor, DB'de yok
    manual: list[Finding] = field(default_factory=list)      # elle yazılmış kapsam
    orphan: list[Finding] = field(default_factory=list)      # manifest bu anahtarı hiç bilmiyor


def canonical_entry(key: str, scoped: bool) -> str:
    """`grant_defaults()` hangi satırı yazıyorsa o.

    Kapsamlı izin `izin:*` olarak tohumlanır. Bu eşleme `identity.py` ile aynı
    kalmak zorundadır; ayrılırlarsa betik var olmayan sapma uydurur.
    """
    return f"{key}:*" if scoped else key


def read_manifests() -> tuple[dict[str, Declared], set[str], list[str]]:
    """`modules/*/module.yaml` → ilan edilen izinler.

    Geçersiz manifest yalnızca kendini düşürür, raporu durdurmaz (K7 ile aynı
    huy) ve o modülün izinlerine HİÇ DOKUNULMAZ: okunamayan bir manifestten
    "bu rol artık önerilmiyor" sonucu çıkarılamaz. Şema doğrulaması burada
    YAPILMAZ: betik yetkiyi konuşur, manifest sağlığını
    `tools/build-ui-registry.py --check` ve çekirdek söyler.
    """
    declared: dict[str, Declared] = {}
    module_ids: set[str] = set()
    problems: list[str] = []

    for manifest_path in sorted(MODULES.glob("*/module.yaml")):
        folder = manifest_path.parent.name
        try:
            manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as error:
            problems.append(f"{folder}: manifest okunamadı — {error}")
            continue

        if manifest.get("id") != folder:
            problems.append(f"{folder}: id ('{manifest.get('id')}') klasör adıyla aynı değil")
            continue

        module_ids.add(folder)
        for permission in manifest.get("permissions") or []:
            key = permission.get("key")
            if not key:
                problems.append(f"{folder}: anahtarsız izin kaydı")
                continue
            entry = canonical_entry(key, bool(permission.get("scoped")))
            declared[entry] = Declared(
                module_id=folder,
                key=key,
                entry=entry,
                roles=set(permission.get("default_roles") or []),
            )

    return declared, module_ids, problems


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


def compare(declared: dict[str, Declared], module_ids: set[str],
            grants: list[tuple[str, str]]) -> Report:
    """Sapmayı dört kutuya ayırır. Yalnız ilki geri alınabilir.

    Kutuların ayrı durmasının sebebi, her birinin FARKLI bir insan kararı
    gerektirmesi: biri manifestin uygulanmamış daraltması, biri henüz açılmamış
    çekirdek, biri yöneticinin elinin izi, biri de artık var olmayan bir
    anahtar. Hepsini tek listeye dökmek, üçünü yanlışlıkla silmeye davet olurdu.
    """
    report = Report(modules=len(module_ids), declared=len(declared))
    seen: set[tuple[str, str]] = set()

    for role, entry in grants:
        module_id = entry.split(".", 1)[0]
        if module_id not in module_ids:
            # Çekirdek izni ya da silinmiş modül artığı — bu betiğin işi değil.
            continue

        seen.add((role, entry))
        target = declared.get(entry)
        if target is None:
            # Anahtar bu modülünkü ama manifest onu ilan etmiyor: ya elle
            # yazılmış, ya da kapsam biçimi (`izin` ↔ `izin:*`) değişmiş.
            key = entry.split(":", 1)[0]
            other = [item for item in declared.values() if item.key == key]
            if other:
                report.manual.append(Finding(
                    module_id, entry, role,
                    f"manifest bu anahtarı '{other[0].entry}' olarak ilan ediyor",
                ))
            else:
                report.orphan.append(Finding(module_id, entry, role))
            continue

        if role not in target.roles:
            report.excess.append(Finding(module_id, entry, role))

    for entry, target in declared.items():
        for role in sorted(target.roles):
            if (role, entry) not in seen:
                report.missing.append(Finding(target.module_id, entry, role))

    for bucket in (report.excess, report.missing, report.manual, report.orphan):
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
        "YETİM — modül duruyor ama anahtarı ilan etmiyor",
        "DOKUNULMAZ. Anahtar silinmiş ya da yeniden adlandırılmış olabilir;"
        " ayrımı insan yapar.",
        report.orphan,
    )

    if report.excess:
        print(f"\n{len(report.excess)} satır geri alınabilir:"
              " scripts/reconcile-permissions.py --uygula")
    else:
        print("\nGeri alınacak satır yok.")


# ------------------------------------------------------------------ uygula


def apply(report: Report, db_path: Path) -> int:
    """Yalnız `FAZLA` kutusunu, yalnız açık onayla geri alır."""
    if not report.excess:
        print("Geri alınacak satır yok; hiçbir şey yapılmadı.")
        return 0

    if not sys.stdin.isatty():
        # Onay kapısı bilerek İNSANA bağlı: betiğin bir zamanlayıcıya ya da
        # kuruluma bağlanması, "otomatik budama yok" kararını dolambaçlı
        # yoldan bozardı.
        print("Onay alınamıyor: bu kip etkileşimli bir terminal ister.", file=sys.stderr)
        return 2

    print(f"\n{len(report.excess)} satır silinecek. Onaylıyorsanız {ONAY_SOZCUGU} yazın: ", end="")
    if input().strip() != ONAY_SOZCUGU:
        print("Vazgeçildi; hiçbir şey değişmedi.")
        return 0

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            for finding in report.excess:
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
                    (stamp, f"reconcile-permissions: {finding.entry} ← {finding.role}"),
                )
    finally:
        connection.close()

    print(f"{len(report.excess)} satır silindi.")
    print("Çekirdek bunları geri getirmez: manifest o rolleri artık önermiyor.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manifest izinleri ile role_permissions tablosunu uzlaştırır.",
    )
    kip = parser.add_mutually_exclusive_group()
    kip.add_argument("--rapor", action="store_true",
                     help="sapmayı listeler, hiçbir şeyi değiştirmez (varsayılan)")
    kip.add_argument("--uygula", action="store_true",
                     help="yalnız manifestin daralttığı izinleri, açık onayla geri alır")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"çekirdek veritabanı (varsayılan: {DEFAULT_DB})")
    args = parser.parse_args()

    declared, module_ids, problems = read_manifests()
    try:
        grants = read_grants(args.db)
    except (OSError, sqlite3.Error) as error:
        print(f"Veritabanı okunamadı: {error}", file=sys.stderr)
        return 2

    report = compare(declared, module_ids, grants)
    report.problems = problems
    print_report(report, args.db, readonly=not args.uygula)

    if args.uygula:
        return apply(report, args.db)
    return 1 if report.excess else 0


if __name__ == "__main__":
    raise SystemExit(main())
