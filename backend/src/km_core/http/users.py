"""Kimlik ve kullanıcı yönetimi uçları (ADR 0007, docs/identity-model.md).

Sözleşme — kabuk bu adreslere konuşur:

    POST /api/auth/login            {password}                → {token, user}
                                                              | {mustSetPassword: true, …}
    POST /api/auth/set-password     {currentSecret, password} → {token, user}
    GET  /api/users                                           → {users: [...]}
    POST /api/users                 {firstName, …, password}
    PUT  /api/users/{id}            {…, expectedRevision?}
    POST /api/users/{id}/password   {password}
    POST /api/users/{id}/status     {status}
    GET  /api/roles                                           → {roles: [...]}

**K9 — çift kapı.** Her uç gerektirdiği izni BURADA, backend'de ilan eder ve
denetler; arayüzde gizlemek yetkilendirme sayılmaz. İzin ilan etmeyen yönetim
ucu yoktur (varsayılan: kapalı). İki `auth` ucu izin değil SIR doğrular —
gerekçesi kendi gövdelerinde yazılıdır.

**K10 — rol adı sorulmaz.** Buradaki kapıların hiçbiri role bakmaz; hepsi
`has_permission(key, scope)` üzerinden geçer.

**ADI ŞİFRE, İSTENEN PIN.** ADR 0016 (şifreyle giriş) reddedildi; giriş yine
6 haneli PIN'dir. Gövde alanı `password`, uç adı `set-password` ve izin anahtarı
`users.set_password` göçten kalmıştır — adları geri çevirmek ikinci bir göç ve
veri riski demekti. Ekran metinleri ve doğrulama her yerde PIN'i anlatır
(kural: `km_core/security/identity.py` → `validate_pin`).

Yanıtlarda `password_hash`, `secret_lookup` ve PIN sütunları HİÇBİR ZAMAN
bulunmaz: gövde `_user_row` ile açıkça beyaz listeden geçirilir.

**MERKEZÎ KİMLİK (ADR 0021) GİRİŞ YOLUNA BURADAN BAĞLANIR.** İki oturum açan uç
(`/auth/login` ve `/auth/set-password`) `_prepare_login` kapısından geçer:
merkezden çekilmiş kadro yerel tablolara yansıtılır ve önbellek yaş sınırını
aşmışsa yalnız çevrimiçi giriş kabul edilir. **Yetenek kapalıyken bu kapı hiç
çalışmaz** ve davranış bugünküyle birebir aynıdır — gerekçe `_prepare_login`
gövdesinde.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from km_core.http.deps import requires
from km_core.security.identity import (
    CurrentUser,
    Identity,
    IdentityError,
    RevisionConflict,
    UserNotFound,
)
from km_core.security.roster_projection import project_roster

OrgScope = Literal["bbd", "bld", "org"]
UserStatus = Literal["active", "disabled"]

# Önbellek yaş sınırını aşınca söylenen cümle (ADR 0021 — Sonuçlar). TEK YERDE
# durur; iki uçta ayrı ayrı yazılsaydı biri değiştiğinde öteki eskirdi.
STALE_ROSTER_MESSAGE = (
    "Kadro çok eskidi; giriş için bağlantı gerekiyor. "
    "Merkeze ulaşılamıyor, çevrimdışı giriş kabul edilmiyor."
)


class _Body(BaseModel):
    """Gövde modellerinin ortak ayarı: JSON tarafı camelCase, kod tarafı
    snake_case. API yolu ve alan adları İngilizce ASCII kalır."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class LoginRequest(_Body):
    # Sınır BİLEREK gevşek: giriş ekranı PIN kuralını ele vermez ve reddi tek
    # tip 401'dir. Kural ihlali PIN KURULURKEN söylenir.
    password: str = Field(min_length=1, max_length=256)


class SetPasswordRequest(_Body):
    # Göç yolunda `currentSecret` eski sütunda duran PIN'dir; PIN'ini kurmuş
    # kullanıcı için mevcut PIN'idir.
    current_secret: str = Field(alias="currentSecret", min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)


class UserPasswordRequest(_Body):
    # PIN kuralı BURADA TEKRARLANMAZ: hane sayısı, yalnız rakam ve basit PIN
    # elemesi tek yerdedir (`Identity.validate_pin`). Burada ikinci bir sınır
    # yazmak, iki kuralın zamanla ayrışması demekti; ihlal 400 ile Türkçe
    # cümlesiyle döner.
    password: str = Field(min_length=1, max_length=256)


class UserStatusRequest(_Body):
    status: UserStatus


class UserCreateRequest(_Body):
    first_name: str = Field(alias="firstName", min_length=1, max_length=120)
    last_name: str = Field(alias="lastName", min_length=1, max_length=120)
    org_scope: OrgScope = Field(alias="orgScope")
    roles: list[str] = Field(min_length=1)
    password: str = Field(min_length=1, max_length=256)
    phone_mobile: str | None = Field(default=None, alias="phoneMobile", max_length=40)
    phone_ext: str | None = Field(default=None, alias="phoneExt", max_length=20)
    title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    directory_visible: bool = Field(default=True, alias="directoryVisible")


class UserUpdateRequest(_Body):
    first_name: str | None = Field(default=None, alias="firstName", min_length=1, max_length=120)
    last_name: str | None = Field(default=None, alias="lastName", min_length=1, max_length=120)
    org_scope: OrgScope | None = Field(default=None, alias="orgScope")
    phone_mobile: str | None = Field(default=None, alias="phoneMobile", max_length=40)
    phone_ext: str | None = Field(default=None, alias="phoneExt", max_length=20)
    title: str | None = Field(default=None, max_length=120)
    department: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=180)
    note: str | None = Field(default=None, max_length=1000)
    directory_visible: bool | None = Field(default=None, alias="directoryVisible")
    roles: list[str] | None = Field(default=None, min_length=1)
    expected_revision: int | None = Field(default=None, alias="expectedRevision", ge=1)


def user_payload(user: CurrentUser) -> dict[str, Any]:
    """Oturum sahibinin kendi özeti — kabuk menüyü bununla kurar."""
    return {
        "id": user.id,
        "firstName": user.first_name,
        "lastName": user.last_name,
        "fullName": user.full_name,
        "orgScope": user.org_scope,
        "roles": user.roles,
        "permissions": sorted(user.permissions),
    }


def _user_row(row: dict[str, Any]) -> dict[str, Any]:
    """Yönetim listesi kaydı. SIR ALANLARI BEYAZ LİSTEDE YOKTUR."""
    return {
        "id": row["id"],
        "firstName": row["first_name"],
        "lastName": row["last_name"],
        "fullName": f"{row['first_name']} {row['last_name']}".strip(),
        "title": row.get("title"),
        "department": row.get("department"),
        "orgScope": row["org_scope"],
        "phoneMobile": row.get("phone_mobile"),
        "phoneExt": row.get("phone_ext"),
        "email": row.get("email"),
        "note": row.get("note"),
        "directoryVisible": bool(row.get("directory_visible", 1)),
        "status": row["status"],
        "roles": row.get("roles") or [],
        "revision": int(row.get("revision") or 1),
        # ADR 0021 — kaydın merkezin kopyası mı yoksa bu kurulumun kendi kaydı
        # mı olduğu. Yönetici, merkezden gelen bir satırı burada düzenlemeye
        # çalışmadan önce görebilmeli: merkez kaydının yazması merkeze gider.
        "origin": row.get("origin") or "local",
        # PIN'ini yeni sütuna hiç kurmamış (göçten gelen) kullanıcı işaretlenir:
        # yönetici kimin hâlâ eski sütunda olduğunu görebilsin.
        "mustSetPassword": row.get("password_hash") is None,
        "passwordSetAt": row.get("password_set_at"),
        "lastLoginAt": row.get("last_login_at"),
        "lockedUntil": row.get("locked_until"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _identity(request: Request) -> Identity:
    identity: Identity | None = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik hazır değil.")
    return identity


async def _prepare_login(request: Request) -> None:
    """Merkezî kimliğin giriş yoluna bağlandığı TEK kapı (ADR 0021 §2).

    Sırasıyla:

      1. **Yetenek kapalıysa hiçbir şey yapılmaz.** `platform.identity_sync`
         kapalıyken (ya da adres verilmemişken) bu fonksiyon ilk satırında
         döner: giriş bugünkü gibi yerel depodan yapılır, tek ek sorgu bile
         koşmaz. ADR 0021 — Sonuçlar: "servis yoksa hiçbir yetenek gerilemez".
      2. **Önbellek hiç yoksa da hiçbir şey yapılmaz.** Merkezden kadro almamış
         bir kurulumun elinde yalnız kendi kayıtları vardır; onları eski sayıp
         girişi kesmek, eşleme yapılmış ama henüz senkron olmamış bir makineyi
         (ve içindeki ilk yöneticiyi) kilitlerdi.
      3. **Yaş sınırı aşıldıysa yalnız çevrimiçi giriş.** Önbellek ayardaki
         sınırdan eskiyse önce tazelenmeye çalışılır; merkeze ulaşılamıyorsa
         giriş 503 ile reddedilir. Pasifleştirilen bir kullanıcının çevrimdışı
         bir makinede sonsuza dek giriş yapabilmesi kabul edilemez.
      4. **Kadro yerel tablolara yansıtılır.** Merkezde açılan kullanıcının bu
         kurulumda giriş yapabilmesinin tek yolu budur; `revision` değişmemişse
         yansıtma hiçbir şey yazmaz.

    Yansıtma kendi hatasını yutar (K7): yazılamazsa giriş yerel kayıtla devam
    eder, ama sessiz kalmaz — loga düşer.
    """
    sync: Any | None = getattr(request.app.state, "identity_sync", None)
    if sync is None or not getattr(sync, "configured", False):
        return
    store = getattr(request.app.state, "store", None)
    if store is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        return

    # Denetim kuyruğunun deposu (ADR 0021 §5) — etkisiz, ikinci kez bağlamaz.
    sync.attach_store(store)

    payload = sync.cache.read()
    if payload is None:
        return

    if sync.login_policy() == "online_only":
        result = await sync.sync()
        if not result.get("synced"):
            raise HTTPException(status_code=503, detail=STALE_ROSTER_MESSAGE)
        payload = sync.cache.read() or payload

    await project_roster(store, payload)


@contextmanager
def _translated_errors() -> Iterator[None]:
    """Kimlik hatalarını tekdüze HTTP zarfına çevirir.

    409 ayrı tutulur: iyimser kilit çakışması, istemcinin yenileyip yeniden
    denemesi gereken bir durumdur — geçersiz veri değildir (ADR 0020).
    """
    try:
        yield
    except UserNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RevisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except IdentityError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


def create_identity_router() -> APIRouter:
    router = APIRouter(tags=["identity"])

    # --------------------------------------------------------------- giriş

    @router.post("/auth/login")
    async def login(request: Request, body: LoginRequest) -> dict[str, Any]:
        identity = _identity(request)
        # Merkezden gelen kadro DOĞRULAMADAN ÖNCE yansıtılır: merkezde açılan
        # kullanıcı ilk denemesinde girebilmeli (ADR 0021 §2).
        await _prepare_login(request)
        result = await identity.login(body.password)
        if result is None:
            # TEK TİP MESAJ: sebep ayırt edilmez (kilitli mi, yok mu, yanlış mı).
            raise HTTPException(status_code=401, detail="Giriş yapılamadı.")

        if result.must_set_password:
            # Sır doğrulandı ama OTURUM AÇILMADI. Belirteç yok; kabuk PIN kurma
            # adımına geçer ve eski sırrı `currentSecret` olarak geri gönderir.
            # İzin listesi de dönmez — oturum yoktur.
            return {
                "mustSetPassword": True,
                "message": "Devam etmek için kendinize bir PIN belirleyin.",
                "user": {
                    "id": result.user.id,
                    "firstName": result.user.first_name,
                    "lastName": result.user.last_name,
                    "fullName": result.user.full_name,
                },
            }

        return {"token": result.token, "user": user_payload(result.user)}

    @router.post("/auth/set-password")
    async def set_own_password(request: Request, body: SetPasswordRequest) -> dict[str, Any]:
        """Kullanıcının kendi PIN'ini kurması/değiştirmesi.

        İzin İSTEMEZ; bu K9'a aykırı değildir çünkü uç, oturumun değil MEVCUT
        SIRRIN doğrulanmasıyla korunur. Kişi kendi sırrını bilmeden buradan
        hiçbir şey değiştiremez. Yeni sütunda PIN'i olmayan göç kullanıcısının
        oturumu da zaten yoktur; başka türlü kilitlenirdi.
        """
        identity = _identity(request)
        # Bu uç da OTURUM AÇAR; kapı girişle aynı olmalı, aksi hâlde yaş sınırı
        # `/auth/login` üzerinden kapalı, buradan açık bir yol bırakırdı.
        await _prepare_login(request)
        with _translated_errors():
            result = await identity.set_password_with_secret(body.current_secret, body.password)
        if result is None:
            raise HTTPException(status_code=401, detail="Giriş yapılamadı.")
        token, user = result
        return {"token": token, "user": user_payload(user)}

    # ---------------------------------------------------------- kullanıcılar

    @router.get("/users", dependencies=[requires("users.view")])
    async def list_users(request: Request) -> dict[str, Any]:
        rows = await _identity(request).list_users()
        return {"users": [_user_row(row) for row in rows]}

    # İKİ KAPI BİRDEN: kaydı açmak `users.manage`, ilk PIN'i atamak
    # `users.set_password` ister. `requires` VEYA ile çalıştığı için ikisi
    # ayrı ayrı kurulur — biri geçilmeden gövdeye girilmez (K9).
    @router.post("/users", status_code=201,
                 dependencies=[requires("users.set_password")])
    async def create_user(
        request: Request,
        body: UserCreateRequest,
        actor: CurrentUser = requires("users.manage"),
    ) -> dict[str, Any]:
        identity = _identity(request)
        with _translated_errors():
            user_id = await identity.create_user(
                first_name=body.first_name,
                last_name=body.last_name,
                org_scope=body.org_scope,
                password=body.password,
                roles=body.roles,
                created_by=actor.id,
                phone_mobile=body.phone_mobile,
                phone_ext=body.phone_ext,
                title=body.title,
                department=body.department,
                email=body.email,
                note=body.note,
                directory_visible=body.directory_visible,
            )
            return {"user": _user_row(await identity.get_user(user_id))}

    @router.put("/users/{user_id}")
    async def update_user(
        request: Request,
        user_id: str,
        body: UserUpdateRequest,
        actor: CurrentUser = requires("users.manage"),
    ) -> dict[str, Any]:
        identity = _identity(request)
        # `exclude_unset`: gönderilmeyen alana DOKUNULMAZ. Aksi hâlde tek bir
        # alanı düzenleyen ekran geri kalanını sessizce NULL'a çekerdi.
        fields = body.model_dump(exclude_unset=True, by_alias=False)
        fields.pop("expected_revision", None)
        roles = fields.pop("roles", None)
        with _translated_errors():
            row = await identity.update_user(
                user_id,
                fields=fields,
                roles=roles,
                expected_revision=body.expected_revision,
                actor_id=actor.id,
            )
        return {"user": _user_row(row)}

    @router.post("/users/{user_id}/password")
    async def set_user_password(
        request: Request,
        user_id: str,
        body: UserPasswordRequest,
        actor: CurrentUser = requires("users.set_password"),
    ) -> dict[str, Any]:
        identity = _identity(request)
        with _translated_errors():
            await identity.set_password(user_id, body.password, actor_id=actor.id)
            return {"user": _user_row(await identity.get_user(user_id))}

    @router.post("/users/{user_id}/status")
    async def set_user_status(
        request: Request,
        user_id: str,
        body: UserStatusRequest,
        actor: CurrentUser = requires("users.manage"),
    ) -> dict[str, Any]:
        identity = _identity(request)
        with _translated_errors():
            row = await identity.set_status(user_id, body.status, actor_id=actor.id)
        return {"user": _user_row(row)}

    # ---------------------------------------------------------------- roller

    @router.get("/roles", dependencies=[requires("roles.view")])
    async def list_roles(request: Request) -> dict[str, Any]:
        return {"roles": await _identity(request).list_roles()}

    return router
