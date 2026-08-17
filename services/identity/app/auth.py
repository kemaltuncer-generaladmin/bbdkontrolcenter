"""Servisin iki ayrı anahtarı ve bir kimlik başlığı.

1. **Yönetim token'ı** (`KM_IDENTITY_ADMIN_TOKEN`) — eşleme kodu üretmek,
   kurulumları listelemek ve iptal etmek. Tanımsızsa uç **503** döner:
   "denetim yoksa serbesttir" davranışı, kurulum listesini ve kod üretimini
   internete açık bırakırdı. Kapalı olduğunu SÖYLEYEN bir kapı, sessizce açık
   bir kapıdan iyidir.

2. **Kurulum token'ı** — eşlemede üretilir, kurulumun kasasında durur.
   Veritabanında yalnız sha256'sı saklanır.

3. **`X-KM-Actor-Id`** — yazma isteğinin ARKASINDAKİ KİŞİ.

**EŞLEME TOKEN'I KULLANICI OTURUMU DEĞİLDİR** (ADR 0021 §4). Token *"bu makine
bizim"* der; kişi yine kendi PIN'ini kurulumda girer ve kurulum kimin adına
yazdığını `X-KM-Actor-Id` ile bildirir. Servis o kişinin izinlerini KENDİ
veritabanından okur ve K9'un ikinci kapısını burada kurar — token'ı oturum
saymak, çalınan bir makineyi herkesin hesabı yapardı.

Karşılaştırmaların hepsi `hmac.compare_digest` ile sabit sürelidir: `==`
karşılaştırması ilk farklı karakterde dönüp token'ı harf harf tahmin
edilebilir kılardı.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from km_core.security.identity import CurrentUser, Identity

from .settings import Settings


def hash_token(token: str) -> str:
    """Token veritabanında DÜZ DURMAZ."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return ""


@dataclass(slots=True)
class Installation:
    id: str
    machine_name: str
    platform: str
    version: str
    status: str


def _settings(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Servis hazır değil.")
    return settings


def _identity(request: Request) -> Identity:
    identity: Identity | None = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik hazır değil.")
    return identity


async def require_admin(request: Request) -> None:
    """Yönetim uçlarının kapısı. Token tanımsızsa uç KAPALIDIR (503)."""
    settings = _settings(request)
    if not settings.admin_token:
        raise HTTPException(
            status_code=503,
            detail="Yönetim token'ı tanımsız; bu uç kapalı.",
        )
    if not constant_time_equals(bearer(request), settings.admin_token):
        raise HTTPException(status_code=401, detail="Yönetim token'ı geçersiz.")


async def require_installation(request: Request) -> Installation:
    """Kurulum token'ını doğrular ve `last_seen` alanını tazeler.

    Aday satırlar teker teker `compare_digest` ile karşılaştırılır. Liste bir
    avuç kurulumdan ibarettir; SQL eşitliğine bırakmak yerine karşılaştırmayı
    sabit sürede yapmak, uçtaki tek maliyeti ödemeye değer.
    """
    store = request.app.state.store
    presented = bearer(request)
    if not presented:
        raise HTTPException(status_code=401, detail="Kurulum token'ı yok.")

    digest = hash_token(presented)
    rows = await store.fetch_all(
        "SELECT id, machine_name, platform, version, status, token_hash FROM installations "
        "WHERE status = 'active'"
    )
    matched: dict[str, Any] | None = None
    for row in rows:
        # DÖNGÜ ERKEN KIRILMAZ: ilk eşleşmede çıkmak, karşılaştırma sayısını
        # token'a bağlı hâle getirir ve sabit süreyi bozar.
        if constant_time_equals(str(row["token_hash"]), digest):
            matched = row
    if matched is None:
        # İPTAL EDİLEN KURULUM DA BURAYA DÜŞER ve aynı cevabı alır: "iptal
        # edildin" demek, geçerli bir token'ın varlığını doğrulamak olurdu.
        raise HTTPException(status_code=401, detail="Kurulum tanınmadı veya iptal edilmiş.")

    await store.execute(
        "UPDATE installations SET last_seen_at = datetime('now') WHERE id = ?",
        (matched["id"],),
    )
    return Installation(
        id=str(matched["id"]),
        machine_name=str(matched["machine_name"]),
        platform=str(matched["platform"]),
        version=str(matched["version"]),
        status=str(matched["status"]),
    )


async def require_actor(request: Request, *permissions: str) -> CurrentUser:
    """Yazma isteğinin arkasındaki kişiyi çözer ve iznini denetler (K9).

    K10 — rol adı sorulmaz; tek soru `has_permission`.
    """
    if not permissions:  # pragma: no cover — çağrı yerleri sabit
        raise ValueError("İzin belirtilmeyen uç nokta tanımlanamaz (K9).")

    identity = _identity(request)
    actor_id = (request.headers.get("x-km-actor-id") or "").strip()
    if not actor_id:
        raise HTTPException(
            status_code=401,
            detail="İşlemi yapan kullanıcı bildirilmedi (X-KM-Actor-Id).",
        )

    row: dict[str, Any] | None = await identity.store.fetch_one(
        "SELECT * FROM users WHERE id = ?", (actor_id,)
    )
    if row is None or row["status"] != "active":
        raise HTTPException(status_code=401, detail="İşlemi yapan kullanıcı tanınmadı.")

    actor = CurrentUser(
        id=actor_id,
        first_name=str(row["first_name"]),
        last_name=str(row["last_name"]),
        org_scope=str(row["org_scope"]),
        roles=await identity.roles_of(actor_id),
        permissions=await identity.permissions_of(actor_id),
    )
    for entry in permissions:
        key, _, scope = entry.partition(":")
        if actor.has_permission(key, scope or None):
            return actor

    await identity.audit(actor_id, "permission.denied", result="denied", detail=",".join(permissions))
    raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")
