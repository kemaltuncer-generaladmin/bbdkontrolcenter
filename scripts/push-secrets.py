#!/usr/bin/env python3
"""Bu makinenin iş sırlarını ve geçit ayarlarını merkeze yükler (ADR 0025).

NEDEN VAR. Eşlenen bir kurulumda kimlik çalışıyor ama BLD/BBD/mağaza geçitleri
çalışmıyordu. İki şey yeni makineye hiç geçmiyor:

  · `config/local.yaml` git dışıdır ve PAKETE GİRMEZ — içinde
    `modules.bld_api.base_url`, `modules.bbd_canteen_api.base_url` ve
    `modules.store_api.read_only` var.
  · Kasadaki iş sırları (`server.*.app_key`, `canteen.device_token`, …) o
    makinenin diskinde doğar ve orada kalır.

Bu betik ikisini merkeze iter; merkez de eşlenmiş her kuruluma dağıtır
(`GET /provisioning`). Bir kez koşturulur, sonra yalnız bir sır döndüğünde.

**GÖNDERİLECEK ANAHTARLAR AÇIK LİSTEDİR** (aşağıda `SECRET_KEYS` ve
`SETTING_KEYS`). Kasadaki her şeyi süpürmek yerine ad ad yazılmasının sebebi
şu: kasa bir gün makineye özel bir anahtar daha doğurabilir ve "hepsini
gönder" kuralı onu da gönderirdi. `identity_sync.*` ve `core.pin_pepper`
ayrıca ADIYLA yasaklıdır (`YASAK_*`) — biri makineye özeldir (her kurulumun
kendi kimliği), öteki zaten eşlemeyle gelir. Aynı yasak merkezde de
uygulanır (`services/identity/app/provisioning.py`); buradaki kopya ikinci
kapıdır, tek kapı değil.

**BU BETİK HİÇBİR ŞEYE YAZMAZ.** Kaynak veritabanı `mode=ro` ile açılır ve
kasa anahtarı dosyası VARSA okunur, YOKSA üretilmez — üretmek, çözülemeyecek
bir kasa yaratıp asıl sorunu (anahtar kayıp) gizlerdi.

VARSAYILAN KURU PROVADIR. Gerçek gönderim açık `--uygula` ister; token yoksa
`--uygula` hiç başlamaz. Kuru provada TEK BİR AĞ İSTEĞİ YAPILMAZ.

DEĞERLER HİÇBİR ZAMAN EKRANA YAZILMAZ. Kuru prova anahtar adını, uzunluğu ve
sha256'nın ilk 8 hanesini gösterir — "doğru sır mı" sorusuna yeter, sırrı
terminale ve oradan kayıtlara düşürmez.

Kullanım:
    scripts/push-secrets.py                     # kuru prova (varsayılan)
    scripts/push-secrets.py --kuru-prova        # aynısı, açıkça
    scripts/push-secrets.py --uygula            # gerçekten gönder
    scripts/push-secrets.py --yalniz-ayar       # sır gönderme, yalnız ayarlar
    scripts/push-secrets.py --merkez https://…  # adresi elle ver
    scripts/push-secrets.py --db <yol>          # başka bir veritabanı

Ortam değişkenleri:
    KM_IDENTITY_ADMIN_TOKEN   yönetim token'ı — ZORUNLU (K8: depoya yazılmaz)
    KM_IDENTITY_URL           merkez adresi (--merkez bunu ezer)

Çıkış kodu: 0 sorun yok · 1 bazı anahtarlar bulunamadı (insan kararı bekler)
· 2 betik çalışamadı.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
# Proje henüz kurulabilir paket değil (CLAUDE.md); kaynak dizini yola eklenir —
# `tests/conftest.py` de aynısını yapar.
sys.path.insert(0, str(ROOT / "backend" / "src"))

from km_core.config.loader import (
    Config,
    load_config,
    nest,
    with_store_layer,
)
from km_platform.secrets.vault import Vault

DEFAULT_DB = ROOT / "data" / "kontrol-merkezi.sqlite"

TOKEN_ENV = "KM_IDENTITY_ADMIN_TOKEN"
URL_ENV = "KM_IDENTITY_URL"

#: Merkezdeki uç (`services/identity/app/main.py` → `put_provisioning`).
PROVISIONING_PATH = "/provisioning"

# --------------------------------------------------------------- AÇIK LİSTE

#: Kasadan alınıp dağıtılacak İŞ SIRLARI. Ad ad yazılır; desen kullanılmaz.
#:
#: Kaynak ikilidir ve `Vault.get` ikisine de bakar: `config/local.yaml` →
#: `secrets.<anahtar>` ve çekirdek deposundaki şifreli `secrets` tablosu.
#: Bir anahtar iki yerde de yoksa gönderilmez ve raporda "bulunamadı" olarak
#: görünür — sessizce atlamak, geçidi çalışmayan bir kuruluma "gönderdim"
#: dedirtirdi.
SECRET_KEYS: tuple[str, ...] = (
    # BLD kontrol API'si (K-21) — sunucudaki BLD_CONTROL_SECRET ile aynı olmalı.
    "server.bld.control_secret",
    # Kantin cihazı ve QR
    "canteen.enrollment_secret",
    "canteen.device_token",
    "canteen.qr_key",
    # Mağaza (Bagisto) yönetim belirteci
    "store.admin_token",
    # Zil sistemi (ADR 0013) — anons sesi ve köprü
    "bell.vertex_service_account",
    "bell.bridge_token",
    # Sunucu uygulama anahtarları ve webhook sırları
    "server.coolify.app_key",
    "server.kantin.app_key",
    "server.kantin.bbdstore_webhook_secret",
    "server.kantin.device_enrollment_secret",
    "server.kantin.payment_link_secret",
    "server.odeme.app_key",
    "server.odeme.kantin_webhook_secret",
    "server.odeme.payment_link_secret",
    "server.store.app_key",
    "server.store.bld_webhook_secret",
)

#: Dağıtılacak MODÜL AYARLARI. Sır değildir: adres ve anahtar/fren bayrağı.
#: Kurulumda çekirdek ayar deposuna yazılır (ADR 0018 §4) ve `local.yaml`
#: olmadan da geçitler ayağa kalkar.
SETTING_KEYS: tuple[str, ...] = (
    "modules.bld_api.base_url",
    "modules.bld_api.read_only",
    "modules.bld_api.dry_run_default",
    "modules.bbd_canteen_api.base_url",
    "modules.bbd_canteen_api.device_name",
    "modules.store_api.read_only",
)

#: Hiçbir koşulda gönderilmeyecek anahtarlar. Liste yukarıda zaten dar, ama
#: bir gün oraya yanlışlıkla eklenirse bu kapı tutar.
YASAK_ONEKLER = ("identity_sync.",)
YASAK_ANAHTARLAR = frozenset({
    "core.pin_pepper",
    "core.pin_pepper_auto",
    "core.pin_pepper_previous",
})


def yasakli(key: str) -> bool:
    return key in YASAK_ANAHTARLAR or any(key.startswith(o) for o in YASAK_ONEKLER)


def parmak_izi(value: Any) -> str:
    """Değerin karşılaştırılabilir ama geri döndürülemez özeti.

    Merkezdeki `pepper_fingerprint` ile aynı fikir: 8 hex hane, "aynı mı"
    sorusuna yeter, değeri ele vermez.
    """
    metin = value if isinstance(value, str) else repr(value)
    return hashlib.sha256(metin.encode("utf-8")).hexdigest()[:8]


# ------------------------------------------------------------ salt okunur depo


class SaltOkunurDepo:
    """`Vault` ve `SettingsStore`un ihtiyaç duyduğu iki okuma yöntemi — YAZMA YOK.

    NEDEN `Store` KULLANILMIYOR. Çekirdeğin `Store.open()` yöntemi şemayı
    uygular, WAL kipini açar ve dosya izinlerini düzeltir; hepsi YAZMADIR.
    Bu betiğin asıl kaydı değiştirmesi için hiçbir sebep yok ve yanlışlıkla
    yazan bir sorgu, geri alınamayacak şeyi (şifreli sır satırı) bozardı.
    `push-roster.py` de aynı gerekçeyle `mode=ro` kullanıyor.

    Kasanın ÇÖZME mantığı burada TEKRARLANMAZ: `Vault` olduğu gibi kullanılır
    ve yalnız deposu bu sınıfla değiştirilir. İki ayrı çözme yolu, biri
    değiştiğinde ötekini sessizce eskitirdi.
    """

    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row

    async def fetch_one(self, sql: str, params: Any = ()) -> dict[str, Any] | None:
        row = self._connection.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    async def fetch_all(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._connection.execute(sql, params)]

    def close(self) -> None:
        self._connection.close()


# --------------------------------------------------------------------- okuma


async def _kasadan_oku(vault: Vault, keys: tuple[str, ...]) -> tuple[dict[str, str], list[str]]:
    bulunan: dict[str, str] = {}
    eksik: list[str] = []
    for key in keys:
        if yasakli(key):
            # Listeye yanlışlıkla girmiş: sessizce göndermek yerine raporda görünsün.
            eksik.append(f"{key} (YASAK — gönderilmez)")
            continue
        try:
            value = await vault.get(key)
        except RuntimeError as error:
            # Kasa anahtarı değişmiş: satır çözülemiyor. Betiği düşürmek yerine
            # eksik say — geri kalanı yine gönderilebilir.
            eksik.append(f"{key} ({error})")
            continue
        if value in (None, ""):
            eksik.append(f"{key} (kasada ve local.yaml'da yok)")
            continue
        bulunan[key] = str(value)
    return bulunan, eksik


def _ayarlardan_oku(config: Config, keys: tuple[str, ...]) -> tuple[dict[str, Any], list[str]]:
    bulunan: dict[str, Any] = {}
    eksik: list[str] = []
    sentinel = object()
    for key in keys:
        value = config.get(key, sentinel)
        if value is sentinel:
            eksik.append(f"{key} (ayarda yok)")
            continue
        bulunan[key] = value
    return bulunan, eksik


async def topla(db_path: Path, *, yalniz_ayar: bool) -> tuple[dict[str, str], dict[str, Any], list[str]]:
    """Bu makinenin gönderilecek sırlarını ve ayarlarını okur.

    Ayar zinciri uygulamanın gördüğünün AYNISIDIR: dosya katmanları + çekirdek
    ayar deposu + ortam değişkeni (`km_core/http/app.py` ile aynı sıra). Yalnız
    `local.yaml`a bakmak, ekrandan değiştirilmiş bir adresi kaçırırdı.
    """
    depo = SaltOkunurDepo(db_path)
    try:
        config = load_config()
        ayar_satirlari = await depo.fetch_all("SELECT key, value FROM settings")
        duz: dict[str, Any] = {}
        for row in ayar_satirlari:
            try:
                duz[str(row["key"])] = json.loads(str(row["value"]))
            except ValueError:
                continue
        config = with_store_layer(config, nest(duz))

        ayarlar, ayar_eksik = _ayarlardan_oku(config, SETTING_KEYS)
        if yalniz_ayar:
            return {}, ayarlar, ayar_eksik

        vault = Vault(depo, config)  # type: ignore[arg-type]
        anahtar_yolu = config.path("core.secret_key_path", "data/secret.key")
        if not anahtar_yolu.is_file():
            raise RuntimeError(
                f"Kasa anahtarı bulunamadı: {anahtar_yolu}. "
                "Betik anahtar ÜRETMEZ — üretmek, çözülemeyecek bir kasa yaratırdı."
            )
        await vault.open()
        sirlar, sir_eksik = await _kasadan_oku(vault, SECRET_KEYS)
        return sirlar, ayarlar, [*sir_eksik, *ayar_eksik]
    finally:
        depo.close()


def resolve_merkez(explicit: str | None) -> str | None:
    """Merkez adresi: bayrak → ortam değişkeni → ayar zinciri.

    `push-roster.py` ile aynı sıra; adres depoya gömülmez.
    """
    if explicit:
        return explicit.rstrip("/")
    from_env = os.environ.get(URL_ENV, "").strip()
    if from_env:
        return from_env.rstrip("/")
    try:
        config = load_config()
    except OSError:  # pragma: no cover — ayar dosyası okunamıyor
        return None
    address = str(config.get("platform.identity_sync.base_url") or "").strip()
    return address.rstrip("/") or None


# ------------------------------------------------------------------ gönderim


def _gonder(merkez: str, token: str, payload: dict[str, Any],
            *, timeout: float = 30.0) -> dict[str, Any]:
    """TEK ağ çağrısı. Kuru provada BU FONKSİYON HİÇ ÇAĞRILMAZ.

    `httpx` fonksiyon içinde import edilir — `push-roster.py` ve
    `km_platform/identity_sync/client.py` ile aynı gerekçe (K7/K11).
    """
    try:
        import httpx
    except ImportError as error:
        raise RuntimeError("httpx kurulu değil; gönderim yapılamaz.") from error

    response = httpx.put(
        f"{merkez}{PROVISIONING_PATH}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=payload,
        timeout=timeout,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Merkez {response.status_code} döndü: {response.text[:500]}")
    result: dict[str, Any] = response.json()
    return result


# ---------------------------------------------------------------------- rapor


def print_plan(sirlar: dict[str, str], ayarlar: dict[str, Any], eksik: list[str],
               *, db_path: Path, merkez: str | None, uygula: bool, token_var: bool) -> None:
    kip = "UYGULA — gerçekten gönderilecek" if uygula else "KURU PROVA — hiçbir şey gönderilmez"
    print(f"Kaynak   : {db_path}  (SALT OKUNUR) + config/ ayar zinciri")
    print(f"Merkez   : {merkez or '— bulunamadı —'}")
    print(f"Kip      : {kip}")
    print(f"Token    : {TOKEN_ENV} {'tanımlı' if token_var else 'TANIMSIZ'}")
    print()

    print(f"Gönderilecek {len(sirlar)} sır (DEĞERLER YAZILMAZ):")
    for key in sorted(sirlar):
        value = sirlar[key]
        print(f"  · {key:<42} {len(value):>5} karakter  sha256:{parmak_izi(value)}")
    if not sirlar:
        print("  (yok)")

    print(f"\nGönderilecek {len(ayarlar)} ayar:")
    for key in sorted(ayarlar):
        # AYAR SIR DEĞİLDİR ve değeri yazılır: adresi göremeyen bir kuru prova,
        # yanlış sunucuya gönderim yapıldığını fark ettirmezdi.
        print(f"  · {key:<42} {ayarlar[key]!r}")
    if not ayarlar:
        print("  (yok)")

    if eksik:
        print(f"\nBulunamayan {len(eksik)} anahtar (gönderilmeyecek):")
        for satir in eksik:
            print(f"  · {satir}")

    print("\nMERKEZDE ŞİFRELİ DURUR. Değerler `KM_IDENTITY_VAULT_KEY` ile şifrelenip")
    print("saklanır; anahtar tanımsızsa merkez bu ucu 503 ile KAPATIR ve düz metin")
    print("yazmaz. Dağıtım eşlenmiş, iptal edilmemiş kurulumlara yapılır ve her")
    print("dağıtım denetim izine düşer.")


def print_result(result: dict[str, Any]) -> None:
    print(f"\nMerkez yanıtı: {result.get('written', 0)} anahtar yazıldı · "
          f"paket revizyonu {result.get('revision')}")
    for key in result.get("keys") or []:
        print(f"  YAZILDI  {key}")
    if not result.get("keys"):
        print("  (değişen anahtar yok — merkezdeki paket zaten güncel)")


# ------------------------------------------------------------------------ ana


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Bu makinenin iş sırlarını ve geçit ayarlarını merkeze yükler (ADR 0025).",
    )
    kip = parser.add_mutually_exclusive_group()
    kip.add_argument("--kuru-prova", action="store_true",
                     help="ne gönderileceğini yazar, GÖNDERMEZ (varsayılan)")
    kip.add_argument("--uygula", action="store_true",
                     help="sırları ve ayarları gerçekten merkeze gönderir")
    parser.add_argument("--yalniz-ayar", action="store_true",
                        help="sır gönderme; yalnız modül ayarlarını yükle")
    parser.add_argument("--merkez", default=None,
                        help=f"merkez adresi (varsayılan: {URL_ENV} ya da ayar zinciri)")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB,
                        help=f"çekirdek veritabanı (varsayılan: {DEFAULT_DB})")
    args = parser.parse_args(argv)

    token = os.environ.get(TOKEN_ENV, "").strip()
    merkez = resolve_merkez(args.merkez)

    try:
        sirlar, ayarlar, eksik = asyncio.run(topla(args.db, yalniz_ayar=args.yalniz_ayar))
    except (OSError, sqlite3.Error, RuntimeError) as error:
        print(f"Kaynak okunamadı: {error}", file=sys.stderr)
        return 2

    print_plan(sirlar, ayarlar, eksik, db_path=args.db, merkez=merkez,
               uygula=args.uygula, token_var=bool(token))

    if not args.uygula:
        print("\nGerçekten göndermek için: scripts/push-secrets.py --uygula")
        if not token:
            print(f"Önce {TOKEN_ENV} tanımlanmalı (depoya yazılmaz).")
        return 1 if eksik else 0

    # --uygula yolunun kapıları. Hiçbiri "varsayılanla devam" etmez.
    if not token:
        print(f"\n{TOKEN_ENV} tanımsız; gönderim yapılmadı.", file=sys.stderr)
        return 2
    if not merkez:
        print("\nMerkez adresi bulunamadı; --merkez ile verin.", file=sys.stderr)
        return 2
    if not sirlar and not ayarlar:
        print("\nGönderilecek anahtar yok; hiçbir şey yapılmadı.")
        return 2

    try:
        result = _gonder(merkez, token, {"secrets": sirlar, "settings": ayarlar})
    except RuntimeError as error:
        print(f"\nGönderim başarısız: {error}", file=sys.stderr)
        return 2

    print_result(result)
    # Eksik anahtar sessizce geçilmez: çıkış kodu insan kararı beklendiğini
    # söyler (`push-roster.py` ile aynı sözleşme).
    return 1 if eksik else 0


if __name__ == "__main__":
    raise SystemExit(main())
