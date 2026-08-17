"""Var olan bir kurulumun kadrosunu merkeze taşır — `POST /roster/import`.

NEDEN VAR. Merkez ADR 0021 ile SONRADAN kuruldu; kadro o güne kadar tek
makinenin yerel veritabanında birikmişti. Merkezin kadrosunda yalnız dağıtımda
doğan bootstrap yöneticisi vardır. Bu uç olmadan ikinci cihaz eşlense bile kadro
boş gelir ve kimse kendi PIN'iyle giremez.

**PIN'LER KORUNUR, YENİDEN VERİLMEZ.** Taşınan şey düz PIN değil,
`password_hash` (Argon2id) ve `secret_lookup` (peppered HMAC) ikilisidir. Bu
mümkündür çünkü pepper iki tarafta AYNIDIR: merkezin `KM_IDENTITY_PEPPER`
değeri kurulumun kasasındaki `core.pin_pepper` ile aynı olarak dağıtılır
(`km_core/security/roster_projection.py` başlığı bu şartı yazar). Pepper farklı
olsaydı satırlar merkezde görünür ama HİÇBİR PIN'le eşleşmezdi; belirti (giriş
çalışmıyor) sebebi (pepper farklı) hiç ele vermez.

## Bu uç YALNIZ EKLER

Var olan bir `id` ikinci kez geldiğinde satır GÜNCELLENMEZ, ATLANIR ve nedeni
yanıtta bildirilir. Üç gerekçe:

  · **K9 — çift kapı.** Bu uç yalnız yönetim token'ıyla korunur; arkasında bir
    KİŞİ yoktur (`X-KM-Actor-Id` sorulmaz, izin denetlenmez). Var olan kaydı
    ezebilseydi, `PUT /users/{id}` yolunun izin denetimini atlayan ikinci ve
    denetimsiz bir yazma kanalı açılırdı. Düzenleme o yolda kalır.
  · **Merkez doğruluk kaynağıdır** (ADR 0021 §2). Göçten sonra merkezde
    düzeltilen bir ad/rol, betiğin ikinci kez koşturulmasıyla eski yerel
    kopyaya geri dönerdi — sessiz bir geri alma.
  · **İdempotenslik yine sağlanır.** İkinci koşu kadroyu ikizlemez: kimlikler
    (uuid) korunduğu için aynı kişi aynı satırdır. Yarıda kalan bir göç
    tekrarlandığında eksik kalanlar eklenir, eklenmiş olanlar atlanır.

Kayıt SİLİNMEZ, pasifleştirilmez, adı değiştirilmez. Geri alma ekleyerek
yapılır.

## Atlama nedenleri sessizce yutulmaz

`secret_lookup` sütunu merkezde de UNIQUE'tir: iki kişiye aynı PIN verilemez
(`Identity` sözleşmesi — sır kimliği belirler). Çakışan satır atlanır ve
yanıtta NEDENİYLE döner. Aynısı tanımsız rol için de geçerlidir: rol merkezde
yoksa satır reddedilir, rol kendiliğinden AÇILMAZ — `ensure_builtin_roles`
beşini kurar, altıncısını icat etmek yetki kataloğunu sessizce genişletirdi.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from km_core.security.identity import Identity, now
from km_core.store.db import Store

from . import roster
from .auth import require_admin

log = structlog.get_logger("identity.roster_import")

OrgScope = Literal["bbd", "bld", "org"]
UserStatus = Literal["active", "disabled"]

# Atlama nedenleri MAKİNE OKUNUR bir kodla da döner: betik özet satırını koda
# göre gruplar, insan mesajı okur.
SKIP_EXISTS = "zaten_var"
SKIP_SECRET = "sir_catismasi"
SKIP_ROLE = "tanimsiz_rol"
SKIP_WRITE = "yazilamadi"


class _Body(BaseModel):
    """JSON tarafı camelCase, kod tarafı snake_case — `main.py` ile aynı.

    Taban sınıf `main.py` içindeki eşinden KOPYALANMADI, aynı sözleşmeyi
    tekrarlıyor: bu modül `main.py`den import etseydi, `main.py`nin bu modülü
    import etmesiyle döngü kurulurdu.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class ImportUser(_Body):
    """Taşınan tek kullanıcı.

    `id` KORUNUR. Yeni kimlik üretmek, denetim izindeki eski kayıtları sahipsiz
    bırakır ve kaydı göçten önce tanıyan her kurulumda ikinci bir kişi gibi
    görünürdü.

    Sayaçlar (`failed_attempts`, `locked_until`, `last_login_at`) BİLEREK
    TAŞINMAZ: onlar makinedeki giriş denemelerinin defteridir, kadronun değil.
    """

    id: str = Field(min_length=8, max_length=64)
    first_name: str = Field(alias="firstName", min_length=1, max_length=120)
    last_name: str = Field(alias="lastName", min_length=1, max_length=120)
    org_scope: OrgScope = Field(alias="orgScope")
    roles: list[str] = Field(min_length=1, max_length=20)
    # Argon2id hash'i ve peppered HMAC — düz PIN HİÇBİR ZAMAN taşınmaz.
    password_hash: str = Field(alias="passwordHash", min_length=16, max_length=512)
    secret_lookup: str = Field(alias="secretLookup", min_length=16, max_length=128)
    password_set_at: str = Field(alias="passwordSetAt", min_length=1, max_length=40)
    status: UserStatus = "active"
    title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    phone_mobile: str | None = Field(default=None, alias="phoneMobile", max_length=40)
    phone_ext: str | None = Field(default=None, alias="phoneExt", max_length=20)
    email: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    directory_visible: bool = Field(default=True, alias="directoryVisible")

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class RosterImportRequest(_Body):
    # Üst sınır `AuditRequest` ile aynı gerekçeyi taşır: tek istekte sınırsız
    # satır kabul etmek, servisi tek çağrıyla saatlerce meşgul edebilirdi.
    users: list[ImportUser] = Field(min_length=1, max_length=500)


router = APIRouter(prefix="/roster", tags=["roster"])

_INSERT_USER = """
INSERT INTO users (
    id, first_name, last_name, title, department, org_scope,
    phone_mobile, phone_ext, email, note, directory_visible, status,
    pin_hash, pin_lookup, pin_set_at,
    password_hash, secret_lookup, password_set_at,
    created_at, updated_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
"""


@router.post("/import", dependencies=[Depends(require_admin)])
async def import_roster(request: Request, body: RosterImportRequest) -> dict[str, Any]:
    """Kadroyu içe aktarır. YALNIZ YÖNETİM TOKEN'I geçer (kurulum token'ı değil).

    Yanıt: kaç eklendi, kaç atlandı ve HER ATLAMANIN NEDENİ. `updated` alanı
    yoktur; bu uç var olan satırı ezmez (modül başlığı).
    """
    store: Store = request.app.state.store
    identity: Identity = request.app.state.identity

    known_roles = {str(row["id"]) for row in await store.fetch_all("SELECT id FROM roles")}

    added: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in body.users:
        rejection = await _rejection(store, entry, known_roles)
        if rejection is not None:
            skipped.append({"id": entry.id, "name": entry.full_name, **rejection})
            continue
        try:
            await _write(store, entry)
        except sqlite3.IntegrityError as error:
            # Ön denetimler geçtiği hâlde yazma çakıştıysa (araya giren başka
            # bir istek) satır YİNE atlanır ve nedeni söylenir; yarım kayıt
            # bırakmaktansa eksik bırakmak yeğdir.
            log.warning("içe aktarma yazamadı", user=entry.id, error=str(error))
            skipped.append({
                "id": entry.id,
                "name": entry.full_name,
                "code": SKIP_WRITE,
                "reason": f"Satır yazılamadı: {error}",
            })
            continue
        await identity.audit(None, "roster.import", result="ok", detail=entry.id)
        added.append(entry.id)

    # REVİZYON YALNIZ BİR ŞEY EKLENDİYSE ARTAR. Değişmeyen kadro için numarayı
    # arttırmak, eşli her kurulumu boşuna tam kadro indirmeye zorlardı
    # (ADR 0021 §2 — değişmemişse veri çekilmez).
    revision = await roster.bump_revision(store) if added else await roster.revision(store)
    log.info("kadro içe aktarıldı", added=len(added), skipped=len(skipped), revision=revision)
    return {
        "revision": revision,
        "added": len(added),
        "addedIds": added,
        "skipped": len(skipped),
        "skips": skipped,
    }


async def _rejection(store: Store, entry: ImportUser,
                     known_roles: set[str]) -> dict[str, str] | None:
    """Satır neden alınamaz? Alınabiliyorsa `None`.

    SIRA ÖNEMLİDİR: kimlik denetimi öne alınır. Aynı satır ikinci kez
    geldiğinde kendi `secret_lookup` değeri veritabanında zaten durur; sıra ters
    olsaydı satır kendisiyle çakışır ve neden "sır çakışması" diye yanlış
    raporlanırdı.
    """
    var_olan = await store.fetch_one("SELECT id FROM users WHERE id = ?", (entry.id,))
    if var_olan is not None:
        return {
            "code": SKIP_EXISTS,
            "reason": "Bu kimlik merkezde zaten kayıtlı; içe aktarma var olan satırı ezmez.",
        }

    unknown = sorted(role for role in entry.roles if role not in known_roles)
    if unknown:
        return {
            "code": SKIP_ROLE,
            "reason": f"Merkezde tanımsız rol: {', '.join(unknown)}",
        }

    sahip = await store.fetch_one(
        "SELECT id FROM users WHERE secret_lookup = ?", (entry.secret_lookup,)
    )
    if sahip is not None:
        # KİMİNLE çakıştığı söylenmez — `Identity._assert_password_free` ile
        # aynı kural: aksi hâli, deneme yoluyla başkasının PIN'ini öğrenmeye
        # kapı açardı. Yönetici için kimliği bilmek de gerekmez; çözüm o kişinin
        # PIN'ini değiştirmektir.
        return {
            "code": SKIP_SECRET,
            "reason": "PIN merkezde başka bir kullanıcıda; satır atlandı, o kişinin "
                      "PIN'i değişmeden taşınamaz.",
        }
    return None


async def _write(store: Store, entry: ImportUser) -> None:
    """Kullanıcıyı ve rollerini yazar.

    Eski PIN sütunları (`pin_hash` / `pin_lookup` / `pin_set_at`) DÜŞÜRÜLMEDİ ve
    `NOT NULL`. `Identity.create_user` ile aynı yer tutucu yazılır: `pin-yok:<id>`
    HMAC hexdigest biçiminde olmadığı için hiçbir sırla çakışmaz ve kimlik başına
    benzersizdir (sütun UNIQUE).

    `created_by` BOŞ BIRAKILIR: kaydı açan kişi bu makinede değil, göçten önceki
    kurulumdaydı; merkezde var olmayan bir kimliğe işaret etmek yanlış olurdu.
    """
    stamp = now()
    await store.execute(_INSERT_USER, (
        entry.id, entry.first_name, entry.last_name, entry.title, entry.department,
        entry.org_scope, entry.phone_mobile, entry.phone_ext, entry.email, entry.note,
        int(entry.directory_visible), entry.status,
        "", f"pin-yok:{entry.id}", stamp,
        entry.password_hash, entry.secret_lookup, entry.password_set_at,
        stamp, stamp,
    ))
    await store.execute_many(
        "INSERT OR IGNORE INTO user_roles (user_id, role_id) VALUES (?, ?)",
        [(entry.id, role) for role in entry.roles],
    )
