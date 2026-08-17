"""Kurulum paketi — dağıtılan sırlar ve modül ayarları (ADR 0025).

ADR 0021 §6 "sırlar bu sürümde merkeze taşınmaz" diyordu; 0025 onu tersine
çevirdi. Sebep sahada görüldü: eşlenen bir Mac'te kimlik çalışıyor ama BLD/BBD/
mağaza geçitleri çalışmıyordu, çünkü iki şey pakete hiç girmiyor —

  · `config/local.yaml` git dışıdır ve pakete girmez. İçinde `bld_api.base_url`,
    `bbd_canteen_api.base_url`, `store_api.read_only` var.
  · Kasadaki iş sırları (`server.*.app_key`, `canteen.device_token`, …) o
    makinenin diskinde doğar ve orada kalır.

Yani her yeni kurulum elle kurulmak zorundaydı ve "her bilgisayar birebir
merkez gibi olsun" kararıyla açıkça çelişiyordu.

## Bu dosyanın taşıdığı üç karar

**1. HER DEĞER ŞİFRELİ DURUR — ayar da.** Sır ile ayarı farklı yollardan
saklamak, bir gün yanlış etiketlenmiş bir sırrın düz metne düşmesi demekti.
Tek yol vardır ve o yol şifreler; `kind` sütunu yalnız değerin kurulumda NEREYE
yazılacağını söyler (`secret` → kasa, `setting` → çekirdek ayar deposu), ne
kadar korunacağını değil.

**2. MAKİNEYE ÖZEL ANAHTARLAR BURAYA GİREMEZ.** `identity_sync.*` o makinenin
kendi kimliğidir; `core.pin_pepper` zaten eşlemeyle gelir. İkisinden biri
dağıtılsaydı bütün kurulumlar tek bir kurulum token'ını paylaşır ya da
herkesin PIN'i bir anda kırılırdı. Yasak `scripts/push-secrets.py` içinde de
var; ORADA OLMASI YETMEZ — betiği atlayıp doğrudan uca yazan bir istek aynı
zararı verirdi (K9 — çift kapı, aynı fikir).

**3. DAĞITIM DENETİM İZİNE DÜŞER.** Kim yazdı, hangi kurulum çekti, ne zaman.
DEĞERLER YAZILMAZ, yalnız anahtar ADLARI: iz satırı silinmiyor ve sırrı iz
satırına yazmak, sırrın ömrünü sonsuz yapardı (`installations.pair_code` ile
aynı gerekçe).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from cryptography.fernet import Fernet

from km_core.store.db import Store

from .vault import VaultCorrupt, decrypt, encrypt

log = structlog.get_logger("identity.provisioning")

#: `kind` sütununun aldığı iki değer. Kurulumda hedefi belirler.
KIND_SECRET = "secret"
KIND_SETTING = "setting"
KINDS = (KIND_SECRET, KIND_SETTING)

#: Merkeze YAZILAMAYAN anahtarlar (yukarıdaki 2. karar).
#:
#: `identity_sync.` bir ÖNEKTİR: altındaki her ad (token, private_key,
#: installation_id, admin_token) makineye özeldir. `core.pin_pepper` ve
#: yoldaşları tam adla yasaklanır — `core.` önekini toptan kapatmak, ileride
#: dağıtılması gerekebilecek zararsız çekirdek ayarlarını da kapatırdı.
FORBIDDEN_PREFIXES = ("identity_sync.",)
FORBIDDEN_KEYS = frozenset({
    "core.pin_pepper",
    "core.pin_pepper_auto",
    "core.pin_pepper_previous",
})


class ForbiddenKey(ValueError):
    """Bu anahtar dağıtılamaz — makineye özeldir ya da eşlemeyle gelir."""


def assert_distributable(key: str) -> None:
    """Anahtar dağıtılabilir mi? Değilse SEBEBİYLE reddedilir.

    Sebep burada ayırt edilir (eşleme kodunun aksine): bu uca yazan taraf zaten
    yönetim kapısından geçmiş bir yöneticidir, ondan bilgi saklamanın karşılığı
    yok — yanlış anahtarı sessizce reddetmek onu saatlerce aratırdı.
    """
    if key in FORBIDDEN_KEYS:
        raise ForbiddenKey(
            f"'{key}' dağıtılamaz: kimlik anahtarı eşlemeyle gelir, paketle değil."
        )
    for prefix in FORBIDDEN_PREFIXES:
        if key.startswith(prefix):
            raise ForbiddenKey(
                f"'{key}' dağıtılamaz: bu anahtar makineye özeldir "
                "(her kurulumun kendi kimliği)."
            )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ------------------------------------------------------------------ revizyon


async def revision(store: Store) -> int:
    row = await store.fetch_one("SELECT revision FROM provisioning_meta WHERE id = 1")
    return int(row["revision"]) if row else 1


async def bump_revision(store: Store) -> int:
    """Paket değişti. Kurulumlar bir sonraki turda farkı görür.

    Kadro revizyonundan AYRI sayaçtır: bir sırrın döndürülmesi kadroyu
    ilgilendirmez ve tersi de doğru. Tek sayaç, her kullanıcı eklendiğinde
    bütün kurulumlara 17 sırrı yeniden gönderirdi.
    """
    await store.execute(
        "UPDATE provisioning_meta SET revision = revision + 1, updated_at = datetime('now')"
        " WHERE id = 1"
    )
    return await revision(store)


# --------------------------------------------------------------------- okuma


async def bundle(store: Store, cipher: Fernet) -> dict[str, Any]:
    """Dağıtılacak paketin tamamı: `{revision, secrets, settings}`.

    Çözülemeyen satır SESSİZCE ATLANMAZ (`VaultCorrupt` yükselir): yarım bir
    paket, geçidi çalışmayan bir kuruluma "her şey yolunda" dedirtirdi ve
    sebebini kimse göremezdi.
    """
    rows = await store.fetch_all(
        "SELECT key, kind, value FROM provisioning_items ORDER BY key"
    )
    secrets: dict[str, Any] = {}
    settings: dict[str, Any] = {}
    for row in rows:
        key = str(row["key"])
        try:
            value = decrypt(cipher, str(row["value"]))
        except VaultCorrupt:
            log.error("kurulum paketi değeri çözülemedi", key=key)
            raise
        if str(row["kind"]) == KIND_SECRET:
            secrets[key] = value
        else:
            settings[key] = value
    return {"revision": await revision(store), "secrets": secrets, "settings": settings}


async def summary(store: Store) -> dict[str, Any]:
    """Anahtar ADLARI ve sayıları — DEĞER YOK.

    Yönetim ekranı ve `push-secrets.py` "merkezde ne var" sorusunu buradan
    yanıtlar. Paketin kendisini çekmek için kasa anahtarı gerekir; bu özet için
    gerekmez, çünkü içinde çözülecek bir şey yoktur.
    """
    rows = await store.fetch_all(
        "SELECT key, kind, updated_at, updated_by FROM provisioning_items ORDER BY key"
    )
    return {
        "revision": await revision(store),
        "items": [
            {
                "key": row["key"],
                "kind": row["kind"],
                "updatedAt": row["updated_at"],
                "updatedBy": row["updated_by"],
            }
            for row in rows
        ],
    }


# --------------------------------------------------------------------- yazma


async def put_items(store: Store, cipher: Fernet, *, secrets: dict[str, Any],
                    settings: dict[str, Any], actor_id: str | None) -> dict[str, Any]:
    """Paketi günceller. YALNIZ EKLER VE GÜNCELLER, SİLMEZ.

    Gönderilmeyen bir anahtar dokunulmadan kalır: betiğin kısmi bir listeyle
    koşturulması, merkezdeki paketi budamamalı. Bir anahtarı dağıtımdan
    çıkarmak ayrı bir karardır ve bugün elle yapılır (ADR 0025 — açık kalan
    kapılar).

    DEĞİŞMEYEN DEĞER REVİZYONU ARTIRMAZ. Fernet her şifrelemede farklı çıktı
    üretir; satırı körlemesine yazmak her koşuda revizyonu artırır ve sahadaki
    her kurulum 17 sırrı yeniden çekerdi. Bu yüzden yeni değer eskisiyle
    ÇÖZÜLMÜŞ hâlde karşılaştırılır.
    """
    for key in (*secrets, *settings):
        assert_distributable(key)

    mevcut = await store.fetch_all("SELECT key, value FROM provisioning_items")
    onceki: dict[str, Any] = {}
    for row in mevcut:
        try:
            onceki[str(row["key"])] = decrypt(cipher, str(row["value"]))
        except VaultCorrupt:
            # Çözülemeyen eski satır "farklı" sayılır ve üzerine yazılır.
            # Anahtar değişmişse tek çıkış yolu budur.
            onceki[str(row["key"])] = object()

    yazilan: list[str] = []
    for kind, items in ((KIND_SECRET, secrets), (KIND_SETTING, settings)):
        for key, value in items.items():
            if key in onceki and onceki[key] == value:
                continue
            await store.execute(
                "INSERT INTO provisioning_items (key, kind, value, updated_at, updated_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET kind = excluded.kind, "
                "value = excluded.value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (key, kind, encrypt(cipher, value), _now(), actor_id),
            )
            yazilan.append(key)

    if not yazilan:
        return {"written": 0, "keys": [], "revision": await revision(store)}

    yeni_revizyon = await bump_revision(store)
    log.info("kurulum paketi güncellendi", keys=len(yazilan), revision=yeni_revizyon)
    return {"written": len(yazilan), "keys": sorted(yazilan), "revision": yeni_revizyon}


# ------------------------------------------------------------------- denetim


async def audit(store: Store, *, action: str, result: str, user_id: str | None = None,
                installation_id: str | None = None, detail: str | None = None) -> None:
    """Denetim izine satır düşer — `installation_id` SÜTUNUYLA BİRLİKTE.

    `Identity.audit` bu sütunu bilmiyor (çekirdekte yok, servise `0002` göçüyle
    eklendi) ve "hangi kurulum çekti" sorusunun cevabı tam olarak orada durur.
    Bu yüzden satır burada elle yazılır; `POST /audit` ucu da aynısını yapıyor.

    DEĞER YAZILMAZ. `detail` yalnız sayı ve anahtar ADI taşır.
    """
    await store.execute(
        "INSERT INTO audit_log (at, user_id, action, scope, result, detail, installation_id) "
        "VALUES (?, ?, ?, NULL, ?, ?, ?)",
        (_now(), user_id, action, result, detail, installation_id),
    )
