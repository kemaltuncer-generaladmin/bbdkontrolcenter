"""Eşleme ekranının konuştuğu uçlar.

GİRİŞTEN ÖNCE GELEN İKİSİ — oturum istemez:

    GET  /api/pairing/state          → {enabled, paired, pairingRequired, …}
    POST /api/pairing/pair  {code}   → {paired: true}

**İKİSİ DE OTURUM İSTEMEZ ve bu K9'a aykırı değildir.** Eşleme, girişten
ÖNCE gelir: eşleşmemiş bir kurulumda henüz kadro yoktur, dolayısıyla oturum
açabilecek bir kullanıcı da yoktur. Uç, oturumun değil **tek kullanımlık
kodun** doğrulanmasıyla korunur — `POST /api/auth/set-password` ile aynı
gerekçe (`km_core/http/users.py`).

Kodu doğrulayan taraf merkezdir. Buradaki sayaç yalnız **deneme hızını**
keser: sidecar 127.0.0.1'e bağlı olsa da, makinede çalışan başka bir sürecin
sekiz haneli kodu sınırsız denemesine gerek yok.

GİRİŞTEN SONRA GELEN DÖRDÜ — hepsi izin sorar (K9):

    GET  /api/pairing/installations                 [installations.view]
    POST /api/pairing/pair-code                     [installations.manage]
    POST /api/pairing/installations/{id}/revoke     [installations.manage]
    POST /api/pairing/unpair                        [installations.manage] + PIN

**"KM Cihaz Eşle" ekranının menüde görünmesi yetkilendirme DEĞİLDİR.** Aynı
izin burada yeniden sorulur; menüyü gizlemek yalnız kolaylıktır (ARCHITECTURE
§8, K9). Rol adı hiçbir yerde sorulmaz (K10) — tek soru `has_permission`.

`unpair` YIKICIDIR: merkezden yansımış kullanıcılar pasifleşir ve makine
kadroyu bir daha tazeleyemez. Bu yüzden izin yeterli olsa bile **PIN teyidi**
ister (docs/permissions.md → "Uygulama kuralları" 3).

`state` SIR DÖNDÜRMEZ: token da, kadro alanları da yok.
"""

from __future__ import annotations

import hmac
from collections.abc import Iterator
from contextlib import contextmanager
from time import monotonic
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from km_core.http.deps import requires
from km_core.security.identity import CurrentUser, Identity

from .client import IdentityResponseError
from .errors import IdentitySyncError, ManagementKeyMissing, PairRejected
from .service import IdentitySync

# Ardışık başarısız deneme sınırı ve bekleme. Kimlikteki `max_failed_attempts`
# / `lockout_minutes` ile aynı fikir, çok daha kısa ömürlü.
MAX_ATTEMPTS = 5
COOLDOWN_SECONDS = 60.0


class PairBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Sınır BİLEREK gevşek: kodun biçimini uçta ele vermeyiz, kararı merkez
    # verir ve reddi tek tiptir.
    code: str = Field(min_length=4, max_length=32)


class PairCodeBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Kodun kime verildiğini yönetici not edebilir ("Muhasebe dizüstü").
    # Merkezde `pair_codes.note` sütununda durur.
    note: str | None = Field(default=None, max_length=200)


class UnpairBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Alan adı göçten kalmıştır (ADR 0016 reddedildi); istenen şey PIN'dir.
    password: str = Field(min_length=1, max_length=64)


class _Throttle:
    """Süreç ömrü boyunca yaşayan basit sayaç. Kalıcı değildir ve olmamalıdır:
    yeniden başlatmayı bekleyen biri zaten makinenin başındadır."""

    def __init__(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0

    def blocked(self) -> bool:
        return monotonic() < self.blocked_until

    def fail(self) -> None:
        self.failures += 1
        if self.failures >= MAX_ATTEMPTS:
            self.failures = 0
            self.blocked_until = monotonic() + COOLDOWN_SECONDS

    def reset(self) -> None:
        self.failures = 0
        self.blocked_until = 0.0


def _sync(request: Request) -> IdentitySync:
    service: IdentitySync | None = getattr(request.app.state, "identity_sync", None)
    if service is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik senkronu hazır değil.")
    # Denetim kuyruğunun deposu (ADR 0021 §5) burada bağlanır: yetenek `Vault`
    # ve `Config` ile kurulur, çekirdek deposunu kurucuda görmez. Çağrı
    # etkisizdir (ikinci kez bağlamaz) ve eşleme akışının kendisi de denetim
    # kaydı üretebildiği için EN ERKEN nokta burasıdır.
    store = getattr(request.app.state, "store", None)
    if store is not None:
        service.attach_store(store)
    return service


async def _confirm_pin(request: Request, actor: CurrentUser, password: str) -> None:
    """YIKICI İŞLEMİN İKİNCİ KAPISI: izin yeterli olsa bile PIN sorulur.

    Doğrulama, kişinin kendi satırındaki `secret_lookup` ile yapılır —
    `Identity.secret_lookup` pepper'lı bir HMAC'tir ve pepper kasadadır (K8).
    Bu, oturumu açık olan kişinin PIN'i BİLDİĞİNİ kanıtlar; aradığımız tam
    olarak budur.

    `Identity.login()` KULLANILMAZ, bilerek: o yol ikinci bir oturum açar ve
    denetim izine "giriş yapıldı" satırı düşürürdü. Teyit bir giriş değildir.

    Karşılaştırma sabit sürelidir. Ret 403'tür, 401 değil: oturum geçerli,
    reddedilen şey işlemdir — 401 kabuğu kullanıcıyı giriş ekranına atmaya
    iterdi.
    """
    identity: Identity | None = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

    row = await identity.store.fetch_one(
        "SELECT secret_lookup FROM users WHERE id = ?", (actor.id,)
    )
    stored = str((row or {}).get("secret_lookup") or "")
    if not stored or not hmac.compare_digest(stored, identity.secret_lookup(password)):
        await identity.audit(
            actor.id, "installations.unpair", result="denied", detail="PIN teyidi başarısız"
        )
        raise HTTPException(status_code=403, detail="PIN doğrulanamadı.")


def create_pairing_router() -> APIRouter:
    router = APIRouter(tags=["pairing"])
    throttle = _Throttle()

    @router.get("/pairing/state")
    async def state(request: Request) -> dict[str, Any]:
        return await _sync(request).state()

    @router.post("/pairing/pair")
    async def pair(request: Request, body: PairBody) -> dict[str, Any]:
        service = _sync(request)
        if throttle.blocked():
            raise HTTPException(
                status_code=429,
                detail="Çok fazla deneme yapıldı. Bir dakika sonra tekrar deneyin.",
            )
        try:
            result = await service.pair(body.code)
        except PairRejected as error:
            throttle.fail()
            raise HTTPException(status_code=401, detail=str(error)) from error
        except IdentitySyncError as error:
            # ULAŞILAMAMA SAYACA YAZILMAZ: merkez kapalıyken yapılan deneme,
            # kodun yanlış olduğunu göstermez ve kullanıcıyı cezalandırmamalı.
            raise HTTPException(status_code=503, detail=str(error)) from error
        throttle.reset()

        # ÇALIŞAN `Identity` NESNESİ TAZELENİR. Pepper açılışta kasadan okunup
        # belleğe alınıyor; eşleme merkezinkini benimsediyse bu nesne hâlâ eski
        # değeri taşır ve HİÇBİR PIN TUTMAZ. Kullanıcı eşleme ekranından çıkar,
        # giriş ekranına düşer ve kendi PIN'i "yanlış" der — sebebi hiçbir yerde
        # görünmeden. Yeniden başlatmayı beklemek, kurulumu tamamlayan kişiyi
        # kapalı bir kapıda bırakmak olurdu.
        pepper = str(result.pop("pepper", "") or "")
        if result.get("pepperAdopted") and pepper:
            identity = getattr(request.app.state, "identity", None)
            if identity is not None:
                identity.adopt_pepper(pepper)

        return result

    @router.post("/pairing/reset")
    async def reset(request: Request) -> dict[str, Any]:
        """Bu kurulumun eşlemesini sıfırlar — GİRİŞ İSTEMEZ, bilerek.

        Tam da giriş yapılamadığında gerekiyor: anahtarı merkezle uyuşmayan bir
        kurulumda kimse giremez, dolayısıyla `unpair`in istediği oturum hiç
        kurulamaz. O kilit yüzünden kurulum kendi kendini onaramıyordu.

        Riski dardır: yalnız BU makinenin eşleme durumunu düşürür. Yeniden
        eşlenmek merkezden yeni bir kod ister ve onu ancak yetkili biri üretir.
        Yerel kullanıcılara, kasadaki öteki sırlara ve özel anahtara dokunulmaz.
        """
        if throttle.blocked():
            raise HTTPException(
                status_code=429,
                detail="Çok fazla deneme yapıldı. Bir dakika sonra tekrar deneyin.",
            )
        with _translated():
            return await _sync(request).reset_pairing()

    # ------------------------------------------------------- kurulum yönetimi

    @router.get("/pairing/installations")
    async def installations(
        request: Request,
        _: CurrentUser = requires("installations.view"),
    ) -> dict[str, Any]:
        """Merkezdeki kurulum listesi. Önbelleklenmez: bayat bir liste, iptal
        edilmemiş bir makineyi iptal edilmiş sanmaya yol açardı."""
        with _translated():
            return {"installations": await _sync(request).installations()}

    @router.post("/pairing/pair-code")
    async def pair_code(
        request: Request,
        body: PairCodeBody,
        actor: CurrentUser = requires("installations.manage"),
    ) -> dict[str, Any]:
        """Yeni kurulumlar için tek kullanımlık kod.

        KOD DENETİM İZİNE YAZILMAZ — yalnız "kod üretildi" satırı düşer. İz
        satırı silinmediği için koda yazmak, kodun ömrünü sonsuz yapardı.
        """
        service = _sync(request)
        with _translated():
            result = await service.create_pair_code(body.note)
        await _audit(request, actor, "installations.pair_code", result="ok")
        return result

    @router.post("/pairing/installations/{installation_id}/revoke")
    async def revoke(
        request: Request,
        installation_id: str,
        actor: CurrentUser = requires("installations.manage"),
    ) -> dict[str, Any]:
        """Kurulumu iptal eder. SATIR SİLİNMEZ; merkez durumu değiştirir."""
        with _translated():
            row = await _sync(request).revoke_installation(installation_id)
        await _audit(request, actor, "installations.revoke", result="ok",
                     detail=installation_id)
        return {"installation": row}

    @router.post("/pairing/unpair")
    async def unpair(
        request: Request,
        body: UnpairBody,
        actor: CurrentUser = requires("installations.manage"),
    ) -> dict[str, Any]:
        """BU MAKİNENİN eşlemesini çözer — YIKICI, PIN teyidi ister.

        Merkeze GİTMEZ ve bağlantı ARAMAZ: çalınan bir dizüstünü merkezden
        koparmak, tam da ağın olmadığı anda gerekebilir. Merkezdeki satırı
        düşürmek ayrı bir düğmedir (`/installations/{id}/revoke`).
        """
        service = _sync(request)
        await _confirm_pin(request, actor, body.password)
        result = await service.unpair()
        await _audit(request, actor, "installations.unpair", result="ok",
                     detail=f"pasifleşen kullanıcı: {result['disabledUsers']}")
        return result

    return router


@contextmanager
def _translated() -> Iterator[None]:
    """Yetenek hatalarını HTTP karşılıklarına çevirir.

    **MERKEZİN KENDİ DURUM KODU KORUNUR.** Merkez "kurulum bulunamadı" (404) ya
    da "yönetim token'ı geçersiz" (401) diyorsa bunu 503'e çevirmek, kabuğa
    "servis çalışmıyor, sonra dene" dedirtirdi — oysa yeniden denemek hiçbir
    şeyi değiştirmez. Sunucu hataları (5xx) ve ulaşılamama tek başlıkta,
    503'te toplanır: ikisinin de karşılığı beklemektir.

    Kasada yönetim anahtarı olmaması da 503'tür ama KENDİ CÜMLESİYLE çıkar;
    hangi anahtarın eksik olduğunu söylemeyen bir hata, yöneticiyi ağ aramaya
    gönderirdi.
    """
    try:
        yield
    except ManagementKeyMissing as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except IdentityResponseError as error:
        status = error.status if 400 <= error.status < 500 else 503
        raise HTTPException(status_code=status, detail=str(error)) from error
    except IdentitySyncError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


async def _audit(request: Request, actor: CurrentUser, action: str, *,
                 result: str, detail: str | None = None) -> None:
    """Yıkıcı ve yönetsel işlem denetim izine yazılır (permissions.md, kural 4).

    KOD YAZILMAZ; yalnız işlemin yapıldığı yazılır.
    """
    identity: Identity | None = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        return
    await identity.audit(actor.id, action, result=result, detail=detail)
