#!/usr/bin/env python3
"""Bu makinenin kadrosunu merkezî kimlik servisine taşır (TEK SEFERLİK GÖÇ).

NEDEN VAR. Merkez (ADR 0021) kadro biriktikten SONRA kuruldu. Merkezin
kadrosunda yalnız dağıtımda doğan bootstrap yöneticisi vardır; bu makinenin
yerel kullanıcıları orada yoktur. Eşlenen ikinci cihaz kadroyu merkezden alır —
taşıma yapılmazsa o cihazda kimse kendi PIN'iyle giremez.

**PIN'LER KORUNUR.** Taşınan şey düz PIN değil, `password_hash` (Argon2id) ve
`secret_lookup` (peppered HMAC) ikilisidir; kimseye yeni PIN verilmez. Bu ancak
merkezin `KM_IDENTITY_PEPPER` değeri bu makinenin kasasındaki `core.pin_pepper`
ile AYNI ise çalışır — betik bunu doğrulayamaz (pepper merkezde, kasada durur ve
hiçbir uçtan okunmaz), bu yüzden kuru provada uyarı basar.

BU BETİK VERİTABANINA YAZMAZ. Kaynak `mode=ro` ile açılır: göç sırasında yerel
kadro değişmez, silinmez, işaretlenmez. Taşıma tek yönlüdür ve merkez tarafında
da yalnız EKLEYİCİDİR (`services/identity/app/roster_import.py`).

VARSAYILAN KURU PROVADIR. Gerçek gönderim açık `--uygula` ister; token yoksa
`--uygula` hiç başlamaz.

Kullanım:
    scripts/push-roster.py                          # kuru prova (varsayılan)
    scripts/push-roster.py --kuru-prova             # aynısı, açıkça
    scripts/push-roster.py --uygula                 # gerçekten gönder
    scripts/push-roster.py --bootstrap-dahil        # yerel "Sistem Yöneticisi"ni de gönder
    scripts/push-roster.py --merkez https://...     # adresi elle ver
    scripts/push-roster.py --db <yol>               # başka bir veritabanı

Ortam değişkenleri:
    KM_IDENTITY_ADMIN_TOKEN   yönetim token'ı — ZORUNLU (K8: depoya yazılmaz)
    KM_IDENTITY_URL           merkez adresi (--merkez bunu ezer)

Merkez adresi sırayla aranır: `--merkez` → `KM_IDENTITY_URL` →
`config/local.yaml` → `platform.identity_sync.base_url`. Adres depoya
gömülmedi; kurulumun kendi ayarından okunur.

Çıkış kodu: 0 sorun yok · 1 merkez bazı satırları atladı (insan kararı bekler)
· 2 betik çalışamadı.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "data" / "kontrol-merkezi.sqlite"
LOCAL_CONFIG = ROOT / "config" / "local.yaml"

TOKEN_ENV = "KM_IDENTITY_ADMIN_TOKEN"
URL_ENV = "KM_IDENTITY_URL"

# Merkezdeki uç. `main.py` → `app.include_router(roster_import.router)`.
IMPORT_PATH = "/roster/import"

# Kadroyla birlikte giden alanlar. Merkezdeki model `extra="forbid"` taşır:
# listede olmayan bir alan 422 ile döner. Sayaçlar (failed_attempts,
# locked_until, last_login_at) BİLEREK YOK — onlar bu makinedeki giriş
# denemelerinin defteridir, kadronun değil.
SELECT_USERS = """
SELECT id, first_name, last_name, title, department, org_scope,
       phone_mobile, phone_ext, email, note, directory_visible, status,
       password_hash, secret_lookup, password_set_at, created_by
FROM users
WHERE origin = 'local'
ORDER BY first_name, last_name
"""


@dataclass(slots=True)
class Kullanici:
    row: dict[str, Any]
    roles: list[str] = field(default_factory=list)
    # Doluysa satır GÖNDERİLMEZ; sebebi kuru provada yazılır.
    atlama: str | None = None

    @property
    def id(self) -> str:
        return str(self.row["id"])

    @property
    def ad(self) -> str:
        return f"{self.row['first_name']} {self.row['last_name']}".strip()

    def payload(self) -> dict[str, Any]:
        row = self.row
        return {
            # KİMLİK KORUNUR: yeni uuid üretmek, denetim izindeki eski kayıtları
            # sahipsiz bırakır ve kişiyi göçten önceki kurulumda ikinci bir
            # kullanıcı gibi gösterirdi.
            "id": self.id,
            "firstName": row["first_name"],
            "lastName": row["last_name"],
            "orgScope": row["org_scope"],
            "roles": self.roles,
            "passwordHash": row["password_hash"],
            "secretLookup": row["secret_lookup"],
            "passwordSetAt": row["password_set_at"],
            "status": row["status"],
            "title": row["title"],
            "department": row["department"],
            "phoneMobile": row["phone_mobile"],
            "phoneExt": row["phone_ext"],
            "email": row["email"],
            "note": row["note"],
            "directoryVisible": bool(row["directory_visible"]),
        }


# --------------------------------------------------------------- okuma


def read_local_roster(db_path: Path, *, bootstrap_dahil: bool) -> list[Kullanici]:
    """Yerel kadroyu SALT OKUNUR açar.

    `mode=ro` bir kolaylık değil, sözleşmedir: göç betiğinin asıl kaydı
    değiştirmesi için hiçbir sebep yoktur ve yanlışlıkla yazan bir sorgu, geri
    alınamayacak tek şeyi (PIN hash'i) bozardı.

    Yalnız `origin='local'` satırlar okunur. Merkezden yansımış satırlar
    (`origin='central'`) merkeze geri gönderilmez — ikinci bir makinede
    koşturulduğunda betiğin hiçbir şey yapmamasının sebebi budur.
    """
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        users = [dict(row) for row in connection.execute(SELECT_USERS)]
        roles: dict[str, list[str]] = {}
        for row in connection.execute(
            "SELECT user_id, role_id FROM user_roles ORDER BY user_id, role_id"
        ):
            roles.setdefault(str(row["user_id"]), []).append(str(row["role_id"]))
    finally:
        connection.close()

    kadro = [Kullanici(row=row, roles=roles.get(str(row["id"]), [])) for row in users]
    for kullanici in kadro:
        kullanici.atlama = _yerel_atlama(kullanici, bootstrap_dahil=bootstrap_dahil)
    return kadro


def _yerel_atlama(kullanici: Kullanici, *, bootstrap_dahil: bool) -> str | None:
    """Satır merkeze gönderilmeli mi? Gönderilmemeliyse sebebi.

    BOOTSTRAP YÖNETİCİSİ VARSAYILAN OLARAK GÖNDERİLMEZ. Merkezde dağıtım anında
    kendi "Sistem Yöneticisi" kaydı doğdu (`main.py` → `_bootstrap_admin`) ve o
    kayıt merkezin tek yöneticisidir. Buradakini de göndermek, kadroda aynı adı
    taşıyan İKİ yönetici bırakırdı ve ikisi de her eşlenen cihaza yansırdı.

    Satır ADINDAN değil, `created_by` alanının BOŞ olmasından tanınır: bootstrap
    yolu kaydı kimse adına açmaz, uygulamadan açılan her kullanıcı ise açanın
    kimliğini taşır. Ada bakmak, "Sistem" adlı gerçek bir kişiyi eleyebilirdi.
    """
    if not bootstrap_dahil and not kullanici.row.get("created_by"):
        return ("kurulumun bootstrap yöneticisi (created_by boş); merkezin kendi "
                "yöneticisi zaten var — göndermek için --bootstrap-dahil")
    if not kullanici.row.get("password_hash") or not kullanici.row.get("secret_lookup"):
        # PIN hash'i olmayan satır taşınamaz: merkeze gitse bile o kişi hiçbir
        # PIN'le giremez. Sessizce göndermek, çalıştığı sanılan bir kayıt bırakırdı.
        return "PIN hash'i yok; bu kayıt taşınamaz (merkezde giriş yapamazdı)"
    if not kullanici.roles:
        return "hiç rolü yok; merkez rolsüz kullanıcı kabul etmez"
    return None


def resolve_merkez(explicit: str | None) -> str | None:
    """Merkez adresi: bayrak → ortam değişkeni → `config/local.yaml`."""
    if explicit:
        return explicit.rstrip("/")
    from_env = os.environ.get(URL_ENV, "").strip()
    if from_env:
        return from_env.rstrip("/")
    try:
        raw = yaml.safe_load(LOCAL_CONFIG.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    platform = raw.get("platform") or {}
    sync = platform.get("identity_sync") or {}
    address = str(sync.get("base_url") or "").strip()
    return address.rstrip("/") or None


# ------------------------------------------------------------- gönderim


def _gonder(merkez: str, token: str, payload: dict[str, Any],
            *, timeout: float = 30.0) -> dict[str, Any]:
    """TEK ağ çağrısı. Kuru provada BU FONKSİYON HİÇ ÇAĞRILMAZ.

    `httpx` fonksiyon içinde import edilir — `km_platform/identity_sync/client.py`
    ile aynı gerekçe (K7/K11): paket kurulu değilse betik anlaşılır hata verir.
    """
    try:
        import httpx
    except ImportError as error:
        raise RuntimeError("httpx kurulu değil; gönderim yapılamaz.") from error

    response = httpx.post(
        f"{merkez}{IMPORT_PATH}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Merkez {response.status_code} döndü: {response.text[:500]}")
    result: dict[str, Any] = response.json()
    return result


# ---------------------------------------------------------------- rapor


def print_plan(kadro: list[Kullanici], *, db_path: Path, merkez: str | None,
               uygula: bool, token_var: bool) -> None:
    gidecek = [k for k in kadro if k.atlama is None]
    atlanan = [k for k in kadro if k.atlama is not None]

    kip = "UYGULA — gerçekten gönderilecek" if uygula else "KURU PROVA — hiçbir şey gönderilmez"
    print(f"Kaynak   : {db_path}  (SALT OKUNUR)")
    print(f"Merkez   : {merkez or '— bulunamadı —'}")
    print(f"Kip      : {kip}")
    print(f"Token    : {TOKEN_ENV} {'tanımlı' if token_var else 'TANIMSIZ'}")
    print()

    print(f"Gönderilecek {len(gidecek)} kullanıcı:")
    for kullanici in gidecek:
        roller = ", ".join(kullanici.roles)
        print(f"  · {kullanici.ad:<24} {kullanici.row['org_scope']:<4} "
              f"[{kullanici.row['status']}]  {roller}")
        print(f"    {kullanici.id}  ·  PIN hash'i olduğu gibi taşınır")
    if not gidecek:
        print("  (yok)")

    if atlanan:
        print(f"\nGönderilmeyecek {len(atlanan)} kayıt:")
        for kullanici in atlanan:
            print(f"  · {kullanici.ad:<24} {kullanici.atlama}")

    print("\nPEPPER UYARISI. Taşınan hash'ler, merkezin KM_IDENTITY_PEPPER değeri bu")
    print("makinenin kasasındaki core.pin_pepper ile AYNI ise çalışır. Farklıysa")
    print("kullanıcılar merkezde görünür ama hiçbir PIN'le giremez ve belirti sebebi")
    print("ele vermez. Göndermeden önce doğrulayın.")


def print_result(result: dict[str, Any]) -> None:
    print(f"\nMerkez yanıtı: {result.get('added', 0)} eklendi · "
          f"{result.get('skipped', 0)} atlandı · "
          f"kadro revizyonu {result.get('revision')}")
    for skip in result.get("skips") or []:
        print(f"  ATLANDI  {skip.get('name')} [{skip.get('code')}]: {skip.get('reason')}")


# ----------------------------------------------------------------- ana


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Yerel kadroyu merkezî kimlik servisine taşır (tek seferlik göç).",
    )
    kip = parser.add_mutually_exclusive_group()
    kip.add_argument("--kuru-prova", action="store_true",
                     help="ne gönderileceğini yazar, GÖNDERMEZ (varsayılan)")
    kip.add_argument("--uygula", action="store_true",
                     help="kadroyu gerçekten merkeze gönderir")
    parser.add_argument("--bootstrap-dahil", action="store_true",
                        help="kurulumun bootstrap yöneticisini de gönder"
                             " (varsayılan: gönderilmez)")
    parser.add_argument("--merkez", default=None,
                        help=f"merkez adresi (varsayılan: {URL_ENV} ya da config/local.yaml)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"çekirdek veritabanı (varsayılan: {DEFAULT_DB})")
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "").strip()
    merkez = resolve_merkez(args.merkez)

    try:
        kadro = read_local_roster(args.db, bootstrap_dahil=args.bootstrap_dahil)
    except (OSError, sqlite3.Error) as error:
        print(f"Veritabanı okunamadı: {error}", file=sys.stderr)
        return 2

    print_plan(kadro, db_path=args.db, merkez=merkez, uygula=args.uygula,
               token_var=bool(token))
    gidecek = [k for k in kadro if k.atlama is None]

    if not args.uygula:
        print("\nGerçekten göndermek için: scripts/push-roster.py --uygula")
        if not token:
            print(f"Önce {TOKEN_ENV} tanımlanmalı (depoya yazılmaz).")
        return 0

    # --uygula yolunun kapıları. Hiçbiri "varsayılanla devam" etmez.
    if not token:
        print(f"\n{TOKEN_ENV} tanımsız; gönderim yapılmadı.", file=sys.stderr)
        return 2
    if not merkez:
        print("\nMerkez adresi bulunamadı; --merkez ile verin.", file=sys.stderr)
        return 2
    if not gidecek:
        print("\nGönderilecek kullanıcı yok; hiçbir şey yapılmadı.")
        return 0

    payload = {"users": [k.payload() for k in gidecek]}
    try:
        result = _gonder(merkez, token, payload)
    except RuntimeError as error:
        print(f"\nGönderim başarısız: {error}", file=sys.stderr)
        return 2

    print_result(result)
    if result.get("skips"):
        # Atlanan satır sessizce geçilmez: çıkış kodu insan kararı beklendiğini
        # söyler (`reconcile-permissions.py` ile aynı sözleşme).
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
