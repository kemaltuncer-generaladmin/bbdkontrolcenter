#!/usr/bin/env python3
"""Alan sınırı kapısı (ADR 0033 §6).

Modül başka bir modülün kodunu import edemez (K3), çekirdeği doğrudan import
edemez (K2), başka modülün ya da çekirdeğin tablosuna SQL atamaz (K5), başka
modülün HTTP önekini çağıramaz; çekirdek modül adı taşıyamaz (K1). Her modül
bir alana (`domain`) ve veri sınıfına bağlıdır; yetenek/olay adları alanın
önekini taşır.

Bugünkü ihlaller `tools/boundary-exceptions.yaml` listesindedir (gerekçe +
azaltma planı). Listede olmayan bulgu ya da artık bulunmayan istisna kapıyı
düşürür: liste yalnız küçülür.

Kullanım:
    python3 tools/check-boundaries.py            # kapı (çıkış 0/1)
    python3 tools/check-boundaries.py --verbose  # istisnadaki bulguları da yaz
    python3 tools/check-boundaries.py --json     # bütün bulgular JSON
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import arch_scan

for _stream in (sys.stdout, sys.stderr):  # Windows konsolunda Türkçe (bkz. build-ui-registry)
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=arch_scan.ROOT)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = arch_scan.scan(args.root)
    if args.json:
        print(arch_scan.as_json(result))
        return 0

    verdict = arch_scan.judge(result, arch_scan.load_exceptions(args.root))
    by_rule = Counter(finding.rule for finding in result.findings)
    print(f"{len(result.modules)} modül tarandı · {len(result.findings)} bulgu "
          f"({', '.join(f'{rule} {count}' for rule, count in sorted(by_rule.items())) or 'yok'}) · "
          f"istisnada {len(verdict.allowed)}")

    if args.verbose:
        for finding in verdict.allowed:
            print(f"  [istisna] {finding.rule:18} {finding.module} → {finding.target}  "
                  f"{finding.file}:{finding.line}  {finding.detail}")

    for finding in verdict.new:
        print(f"YENİ İHLAL  {finding.rule:18} {finding.module} → {finding.target}  "
              f"{finding.file}:{finding.line}  {finding.detail}", file=sys.stderr)
    for item in verdict.stale:
        print(f"ESKİ İSTİSNA  ({item.rule}, {item.module}, {item.target}) artık bulunmuyor — "
              f"tools/boundary-exceptions.yaml'dan silin.", file=sys.stderr)
    for item in verdict.malformed:
        print(f"EKSİK İSTİSNA  ({item.rule}, {item.module}, {item.target}) reason/plan boş.",
              file=sys.stderr)

    if not verdict.ok:
        print("Sınır denetimi BAŞARISIZ. Kural ve çözüm: docs/adr/0033-alan-servisleri-ve-sinirlar.md",
              file=sys.stderr)
        return 1
    print("Sınır denetimi geçti.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
