"""Servis anahtarları — makineden makineye kimlik (ADR 0028).

NEDEN VAR. Kontrol Merkezi'nin bugüne kadar tek kimliği kişiydi: 6 haneli PIN,
boşta kalınca düşen oturum. Başka bir sunucunun (ör. bir mobil uygulamanın
arka ucu) KM'ye sunucudan sunucuya konuşabilmesi için ne PIN uygundur (bir
makinenin PIN'i, PIN'le giriş yapabilen bir makine demektir) ne de oturum
(30 dakikada düşer, biri her gün yeniden açmak zorunda kalırdı).

Servis anahtarı bir KULLANICIYA bağlıdır — `users.kind = 'service'` olan bir
satıra. Yetki yine YALNIZ o kullanıcının rollerinden gelir; izin denetimi
kişininkiyle aynı yoldan geçer (`CurrentUser.has_permission`, K9/K10). Anahtar
yeni bir yetki modeli değil, aynı modele ikinci bir giriş kapısıdır.

## Biçim

    kmsk_<önek 8>_<sır>

  · `kmsk_` işarettir: kapı belirteci ilk bakışta oturumdan ayırır
    (`km_core/http/deps.py`). Oturum belirteci bu önekle başlayamaz
    (`Identity._open_session`).
  · Önek 8 karakter `[a-z0-9]`dur ve veritabanında DÜZ durur: anahtarı tek
    indeksli sorguyla bulur, listede ve denetim izinde anahtarı anar.
  · Sır en az 32 bayt rastgeleliktir (`secrets.token_urlsafe(32)`).

Veritabanında anahtarın kendisi DEĞİL, sha256 özeti durur. Özet tuzsuzdur ve
bu kasıtlıdır: 256 bitlik rastgele bir sırrı sözlükle aramak anlamsızdır,
tuz yalnız her istekte ikinci bir okuma demek olurdu. Karşılaştırma sabit
sürelidir. Tam anahtar yalnız üretildiği anda BİR KEZ gösterilir.

## Ağ kısıtı

`allowed_cidrs` boşsa anahtar her adresten kullanılabilir. Doluysa istemci
adresi listedeki ağlardan birinde olmalıdır. İstemci adresinin nasıl
belirlendiği (en sağdaki `X-Forwarded-For`) HTTP katmanının işidir; burada
yalnız karar verilir.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from km_core.security.identity import (
    PERSON_KIND,
    SERVICE_KIND,
    SERVICE_TOKEN_MARK,
    CurrentUser,
    Identity,
    IdentityError,
    UserNotFound,
    now,
)

#: Önek uzunluğu ve alfabesi. Alt çizgi YOK: biçim `_` ile bölünür.
PREFIX_LENGTH = 8
_PREFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

#: Sırrın rastgelelik baytı. Sözleşme "en az 32 bayt" der.
SECRET_BYTES = 32

#: `last_used_at` en sık bu aralıkla yazılır (saniye). Her istekte yazmak,
#: yoğun bir istemcinin her çağrısını bir UPDATE'e çevirirdi.
LAST_USED_TOUCH_SECONDS = 60

_TOKEN = re.compile(
    rf"^{re.escape(SERVICE_TOKEN_MARK)}(?P<prefix>[a-z0-9]{{{PREFIX_LENGTH}}})_(?P<secret>[A-Za-z0-9_-]{{32,256}})$"
)


class ServiceKeyError(IdentityError):
    """Servis anahtarı sözleşmesi ihlali (kullanıcı, ağ listesi, durum)."""


@dataclass(slots=True)
class Verdict:
    """Bir anahtar denemesinin sonucu.

    `user` doluysa anahtar geçerlidir. Değilse `reason` denetim izine yazılacak
    gerekçedir — istemciye SÖYLENMEZ (tek tip 401).
    """

    user: CurrentUser | None = None
    reason: str = ""
    key_id: str | None = None
    prefix: str | None = None
    user_id: str | None = None


@dataclass(slots=True)
class IssuedKey:
    """Yeni üretilen anahtar. `token` yalnız bu nesnede bulunur."""

    id: str
    token: str
    prefix: str
    user_id: str
    name: str
    allowed_cidrs: list[str] = field(default_factory=list)


def parse_token(token: str) -> tuple[str, str] | None:
    """`kmsk_<önek>_<sır>` → (önek, sır). Biçime uymayan dizgi `None`."""
    match = _TOKEN.match(token or "")
    if match is None:
        return None
    return match.group("prefix"), match.group("secret")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_cidrs(values: list[str] | None) -> list[str]:
    """Ağ listesini doğrular ve kanonik yazar (`10.0.0.5` → `10.0.0.5/32`).

    Geçersiz bir giriş SESSİZCE ATLANMAZ: atlansaydı yönetici kısıt koyduğunu
    sanırken anahtar her adrese açık kalabilirdi (boş liste = her adres).
    """
    result: list[str] = []
    for raw in values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError as error:
            raise ServiceKeyError(f"Geçersiz ağ: {text}") from error
        canonical = str(network)
        if canonical not in result:
            result.append(canonical)
    return result


def address_allowed(address: str | None, cidrs: list[str]) -> bool:
    """İstemci adresi izinli ağlardan birinde mi? Liste boşsa her adres izinli."""
    if not cidrs:
        return True
    try:
        ip = ipaddress.ip_address(str(address or "").strip())
    except ValueError:
        return False
    for cidr in cidrs:
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


def _new_prefix() -> str:
    return "".join(secrets.choice(_PREFIX_ALPHABET) for _ in range(PREFIX_LENGTH))


def _stale(value: Any, seconds: int) -> bool:
    if not value:
        return True
    try:
        seen = datetime.fromisoformat(str(value))
    except ValueError:
        return True
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=UTC)
    return (datetime.now(UTC) - seen).total_seconds() >= seconds


def _cidrs_of(row: dict[str, Any]) -> list[str]:
    try:
        value = json.loads(str(row.get("allowed_cidrs") or "[]"))
    except ValueError:
        # Bozuk liste "her adres" sayılmaz: anahtar KAPANIR. Yanlış kapanmış
        # bir anahtar fark edilir; yanlış açılmış bir anahtar edilmez.
        return ["0.0.0.0/32"]
    return [str(item) for item in value] if isinstance(value, list) else ["0.0.0.0/32"]


class ServiceKeys:
    """Servis anahtarlarının deposu ve doğrulayıcısı.

    Denetim izini `Identity.audit` ile yazar — iki ayrı denetim yolu olmasın.
    """

    def __init__(self, identity: Identity) -> None:
        self.identity = identity
        self.store = identity.store

    # ------------------------------------------------------------- yönetim

    async def create(self, *, user_id: str, name: str,
                     allowed_cidrs: list[str] | None = None,
                     created_by: str | None = None) -> IssuedKey:
        """Anahtar üretir. Tam anahtar YALNIZ dönen nesnede bulunur."""
        label = str(name or "").strip()
        if not label:
            raise ServiceKeyError("Anahtar adı zorunlu.")
        cidrs = normalize_cidrs(allowed_cidrs)

        row = await self.identity.get_user(user_id)
        if str(row.get("kind") or PERSON_KIND) != SERVICE_KIND:
            # Kişiye anahtar verilmez: PIN'i ve oturumu olan birinin bir de
            # süresiz anahtarı olması, oturum kurallarını (boşta kalma, kilit)
            # bütünüyle atlatmak demekti.
            raise ServiceKeyError("Anahtar yalnız servis kullanıcısına verilir.")

        # Önek benzersizdir (indeks). Çakışma olasılığı ~1/36^8; yine de denenir.
        for _ in range(5):
            prefix = _new_prefix()
            taken = await self.store.fetch_one(
                "SELECT id FROM service_keys WHERE prefix = ?", (prefix,)
            )
            if taken is None:
                break
        else:  # pragma: no cover — pratikte erişilmez
            raise ServiceKeyError("Benzersiz önek üretilemedi, tekrar deneyin.")

        token = f"{SERVICE_TOKEN_MARK}{prefix}_{secrets.token_urlsafe(SECRET_BYTES)}"
        key_id = str(uuid.uuid4())
        await self.store.execute(
            "INSERT INTO service_keys (id, user_id, name, prefix, key_hash, allowed_cidrs, "
            "created_at, created_by, last_used_at, revoked_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (key_id, user_id, label[:120], prefix, hash_token(token),
             json.dumps(cidrs), now(), created_by),
        )
        await self.identity.audit(created_by, "service_keys.create", result="ok",
                                  detail=f"{prefix} → {user_id}")
        return IssuedKey(id=key_id, token=token, prefix=prefix, user_id=user_id,
                         name=label[:120], allowed_cidrs=cidrs)

    async def list(self) -> list[dict[str, Any]]:
        """Anahtar kayıtları. Özet (`key_hash`) DÖNMEZ."""
        rows = await self.store.fetch_all(
            "SELECT k.id, k.user_id, k.name, k.prefix, k.allowed_cidrs, k.created_at, "
            "k.created_by, k.last_used_at, k.revoked_at, u.first_name, u.last_name, u.status "
            "FROM service_keys k LEFT JOIN users u ON u.id = k.user_id "
            "ORDER BY k.created_at, k.id"
        )
        return [
            {
                "id": row["id"],
                "prefix": row["prefix"],
                "name": row["name"],
                "userId": row["user_id"],
                "userName": f"{row.get('first_name') or ''} {row.get('last_name') or ''}".strip(),
                "userStatus": row.get("status"),
                "allowedCidrs": _cidrs_of(row),
                "createdAt": row["created_at"],
                "createdBy": row.get("created_by"),
                "lastUsedAt": row.get("last_used_at"),
                "revokedAt": row.get("revoked_at"),
            }
            for row in rows
        ]

    async def revoke(self, key: str, *, actor_id: str | None = None) -> dict[str, Any]:
        """Anahtarı iptal eder. Satır SİLİNMEZ: kimin ne zaman kullandığı kalır.

        `key` kayıt kimliği ya da önektir. Zaten iptal edilmiş anahtar için
        tarih değişmez.
        """
        row = await self.store.fetch_one(
            "SELECT id, prefix, revoked_at FROM service_keys WHERE id = ? OR prefix = ?",
            (key, key),
        )
        if row is None:
            raise UserNotFound("Anahtar bulunamadı.")
        if not row.get("revoked_at"):
            await self.store.execute(
                "UPDATE service_keys SET revoked_at = ? WHERE id = ?", (now(), row["id"])
            )
            await self.identity.audit(actor_id, "service_keys.revoke", result="ok",
                                      detail=str(row["prefix"]))
        refreshed = await self.store.fetch_one(
            "SELECT id, prefix, revoked_at FROM service_keys WHERE id = ?", (row["id"],)
        )
        return dict(refreshed or row)

    # ----------------------------------------------------------- doğrulama

    async def verify(self, token: str, *, client_ip: str | None) -> Verdict:
        """Anahtarı doğrular ve bağlı kullanıcıyı çözer.

        Başarısızlıkta sebep yalnız `Verdict.reason`ta durur; çağıran denetim
        izine yazar, istemciye tek tip 401 döner.
        """
        parsed = parse_token(token)
        if parsed is None:
            return Verdict(reason="biçimsiz anahtar")
        prefix, _secret = parsed

        row = await self.store.fetch_one(
            "SELECT * FROM service_keys WHERE prefix = ?", (prefix,)
        )
        if row is None:
            # Kayıt yokken de özet hesaplanır: yanıt süresi "önek var mı"
            # sorusunu ele vermesin.
            hmac.compare_digest(hash_token(token), "0" * 64)
            return Verdict(reason="bilinmeyen anahtar", prefix=prefix)

        verdict = Verdict(key_id=str(row["id"]), prefix=prefix, user_id=str(row["user_id"]))
        if not hmac.compare_digest(str(row["key_hash"]), hash_token(token)):
            verdict.reason = "anahtar eşleşmedi"
            return verdict
        if row.get("revoked_at"):
            verdict.reason = "iptal edilmiş anahtar"
            return verdict
        if not address_allowed(client_ip, _cidrs_of(row)):
            verdict.reason = f"izinli ağ dışı: {client_ip or 'bilinmeyen'}"
            return verdict

        user = await self.store.fetch_one(
            "SELECT * FROM users WHERE id = ?", (row["user_id"],)
        )
        if user is None:
            verdict.reason = "kullanıcısı yok"
            return verdict
        if str(user.get("kind") or PERSON_KIND) != SERVICE_KIND:
            verdict.reason = "kullanıcı servis değil"
            return verdict
        if user.get("status") != "active":
            verdict.reason = "pasif servis kullanıcısı"
            return verdict

        if _stale(row.get("last_used_at"), LAST_USED_TOUCH_SECONDS):
            await self.store.execute(
                "UPDATE service_keys SET last_used_at = ? WHERE id = ?", (now(), row["id"])
            )

        user_id = str(user["id"])
        verdict.user = CurrentUser(
            id=user_id,
            first_name=str(user["first_name"]),
            last_name=str(user["last_name"] or ""),
            org_scope=str(user["org_scope"]),
            roles=await self.identity.roles_of(user_id),
            permissions=await self.identity.permissions_of(user_id),
            kind=SERVICE_KIND,
        )
        return verdict
