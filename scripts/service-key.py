#!/usr/bin/env python3
"""Servis anahtarı yönetimi (ADR 0028) — create | list | revoke.

Servis anahtarı, başka bir sunucunun (ör. BBD Akademi API) Kontrol Merkezi'ne
sunucudan sunucuya bağlanmasını sağlar. Anahtar bir SERVİS KULLANICISINA
bağlıdır; yetkisi yalnız o kullanıcının rollerinden gelir.

HTTP ucu YOKTUR, bilerek: anahtar üretmek süresiz bir giriş kapısı açmaktır ve
bunun yolu sunucuya erişimi olan kişinin elinde kalmalıdır. Betik, çekirdeğin
açılırken kullandığı depoyu kullanır (`KM_STORE_ENGINE`, `KM_CENTRAL_DSN`);
yerelde SQLite yolu ayardan gelir ya da `--db` ile verilir.

TAM ANAHTAR YALNIZ `create` ÇIKTISINDA, BİR KEZ yazılır. Veritabanında özeti
durur; kaybolan anahtar geri getirilemez — iptal edilip yenisi üretilir.

Kullanım:
    scripts/service-key.py create --name "BBD Akademi API" [--role mobile_service]
                                  [--cidr 203.0.113.7/32 ...] [--user <kullanıcı-id>]
    scripts/service-key.py list
    scripts/service-key.py revoke <anahtar-id | önek>

Çıkış kodu: 0 başarılı · 1 istek reddedildi · 2 betik çalışamadı.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from km_core.security.identity import Identity, IdentityError
from km_core.security.service_keys import ServiceKeys
from km_core.store.base import StoreLike

#: Varsayılan rol. Çekirdek rolün VARLIĞINI garanti eder; izinlerini rolü
#: `default_roles` ile öneren modüller verir.
DEFAULT_ROLE = "mobile_service"

#: Bu betiğin açtığı `Identity` PIN doğrulamaz; pepper'a ihtiyacı yoktur.
#: Boş bırakılmaz ki yanlışlıkla bir PIN özeti üretilirse hiçbir gerçek
#: kayıtla eşleşmesin.
_NO_PEPPER = "service-key-cli/no-pin-operations"


def _open_store(db: str | None) -> StoreLike:
    from km_core.store.engine import create_store, selected_engine

    if db:
        from km_core.store.db import Store

        return Store(Path(db))
    if selected_engine() == "sqlite":
        from km_core.config.loader import load_config

        config = load_config()
        return create_store(config.path("core.store_path", "data/kontrol-merkezi.sqlite"))
    return create_store(Path("unused.sqlite"))


async def _ready(store: StoreLike) -> bool:
    """Göç 0010 uygulanmış mı? Betik göç KOŞTURMAZ — o, sunucunun açılışıdır."""
    columns = await store.table_columns("service_keys")
    if not columns:
        print("HATA: `service_keys` tablosu yok. Sunucuyu bu sürümle bir kez başlatın "
              "(çekirdek göçü 0011_service_keys).", file=sys.stderr)
        return False
    return True


def _print_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("Kayıtlı servis anahtarı yok.")
        return
    for row in rows:
        durum = f"İPTAL {row['revokedAt']}" if row.get("revokedAt") else "etkin"
        aglar = ", ".join(row.get("allowedCidrs") or []) or "her adres"
        print(f"- {row['prefix']}  {row['name']}  [{durum}]")
        print(f"    id: {row['id']}")
        print(f"    kullanıcı: {row['userName']} ({row['userId']}, {row.get('userStatus')})")
        print(f"    ağlar: {aglar}")
        print(f"    üretildi: {row['createdAt']}  son kullanım: {row.get('lastUsedAt') or '—'}")


async def _run(args: argparse.Namespace) -> int:
    store = _open_store(args.db)
    await store.open()
    try:
        if not await _ready(store):
            return 2
        identity = Identity(store, pepper=_NO_PEPPER)
        keys = ServiceKeys(identity)

        if args.command == "list":
            _print_rows(await keys.list())
            return 0

        if args.command == "revoke":
            row = await keys.revoke(args.key)
            print(f"İptal edildi: {row['prefix']} ({row['id']}) — {row['revoked_at']}")
            return 0

        # create
        user_id = args.user
        if not user_id:
            user_id = await identity.create_service_user(
                name=args.name, roles=args.role or [DEFAULT_ROLE],
                note="scripts/service-key.py ile açıldı",
            )
            print(f"Servis kullanıcısı açıldı: {args.name} ({user_id}), "
                  f"roller: {', '.join(args.role or [DEFAULT_ROLE])}")
        issued = await keys.create(user_id=user_id, name=args.name, allowed_cidrs=args.cidr)
        print(f"Anahtar kimliği: {issued.id}")
        print(f"Önek: {issued.prefix}")
        print(f"Ağlar: {', '.join(issued.allowed_cidrs) or 'her adres'}")
        print()
        print("ANAHTAR (yalnız bu kez gösterilir; güvenli bir kasaya koyun):")
        print(issued.token)
        return 0
    except IdentityError as error:
        print(f"Reddedildi: {error}", file=sys.stderr)
        return 1
    finally:
        await store.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servis anahtarı yönetimi (ADR 0028).")
    parser.add_argument("--db", help="SQLite veritabanı yolu (yerel). Sunucuda ortam "
                                     "değişkenleri kullanılır.")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="Yeni anahtar üret (tam anahtar bir kez yazılır).")
    create.add_argument("--name", required=True, help="Anahtarın / servis kullanıcısının adı.")
    create.add_argument("--user", help="Var olan servis kullanıcısının kimliği. Verilmezse "
                                       "yeni servis kullanıcısı açılır.")
    create.add_argument("--role", action="append",
                        help=f"Yeni servis kullanıcısının rolü (tekrarlanabilir; "
                             f"varsayılan {DEFAULT_ROLE}).")
    create.add_argument("--cidr", action="append", default=[],
                        help="İzinli ağ (tekrarlanabilir). Verilmezse her adres.")

    sub.add_parser("list", help="Anahtarları listele (anahtarın kendisi gösterilmez).")

    revoke = sub.add_parser("revoke", help="Anahtarı iptal et (satır silinmez).")
    revoke.add_argument("key", help="Anahtar kimliği ya da önek.")

    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except Exception as error:  # noqa: BLE001 — betik çalışamadı; sebebi yazılır
        print(f"Betik çalışamadı: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
