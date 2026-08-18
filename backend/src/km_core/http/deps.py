"""Uç nokta düzeyinde izin kapısı ve yıkıcı işlemin PIN teyidi.

Router'ın tamamı `module.yaml` → `http.requires` ile taban izne bağlanır; tek
tek uç noktalar bunu DARALTIR (genişletemez). Modüller bunu `km_sdk.requires`
üzerinden kullanır — çekirdeği import etmezler (K2).

    @router.post("/students")
    async def create(user: CurrentUser = requires("bbd_students.manage")):
        ...

Geri dönüşü olmayan işlemler ayrıca `confirm_pin` çağırır (CLAUDE.md: "Yıkıcı
işlemler izin yeterli olsa bile PIN teyidi ister"). Bu kapı bugüne kadar YALNIZ
çekirdeğin eşleme ucunda vardı (`km_platform/identity_sync/http.py`) ve modüllere
kapalıydı; `store_customers/module.yaml` bunu açıkça bekleyen bir not düşmüştü.
Üç ayrı yerde aynı doğrulamayı elle yazmak yerine tek yerde durur.
"""

from __future__ import annotations

import hmac
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from km_core.files import outputs_log
from km_core.security.identity import CurrentUser


def split_permission(entry: str) -> tuple[str, str | None]:
    key, _, scope = entry.partition(":")
    return key, (scope or None)


def requires(*permissions: str) -> Any:
    """Verilen izinlerden EN AZ BİRİNE sahip kullanıcıyı döndürür.

    İki izni birden ARAYAN uç, kapıyı iki kez kurar: biri `dependencies=[...]`
    listesinde, biri argümanda. Her ikisi de bağımsız çalışır, ikisi de
    geçilmeden gövdeye girilmez.
    """
    if not permissions:
        raise ValueError("İzin belirtilmeyen uç nokta tanımlanamaz (K9).")

    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> AsyncIterator[CurrentUser]:
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        user = await identity.resolve_session(token) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")

        if not any(user.has_permission(*split_permission(entry)) for entry in permissions):
            await identity.audit(
                user.id, "permission.denied", result="denied", detail=",".join(permissions)
            )
            raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")

        # Çıktı kaydına üreten kişiyi tanıtır (ADR 0019 §2). Kapı burada da
        # kurulur çünkü çekirdeğin kendi router'ları (`settings`, `users`)
        # `app.py` içindeki `current_user`dan GEÇMEZ — destek paketi gibi
        # oradan doğan çıktılar da sahipsiz kalmasın. Değer istek bitince
        # geri alınır; sızarsa bir sonraki istek yanlış kişiye yazılır.
        with outputs_log.use_actor(user.id):
            yield user

    return Depends(dependency)


async def confirm_pin(request: Request, actor: CurrentUser, pin: str,
                      *, action: str) -> None:
    """YIKICI İŞLEMİN İKİNCİ KAPISI: izin yeterli olsa bile PIN sorulur.

    Doğrulama, kişinin KENDİ satırındaki `secret_lookup` ile yapılır —
    pepper'lı bir HMAC'tir ve pepper kasadadır (K8). Bu, oturumu açık olan
    kişinin PIN'i BİLDİĞİNİ kanıtlar; aradığımız tam olarak budur. Oturum
    çalınmışsa ya da makine başında bırakılmışsa kapı burada kapanır.

    `Identity.login()` KULLANILMAZ, bilerek: o yol ikinci bir oturum açar ve
    denetim izine "giriş yapıldı" satırı düşürürdü. Teyit bir giriş değildir.

    Karşılaştırma sabit sürelidir. Ret 403'tür, 401 DEĞİL: oturum geçerli,
    reddedilen şey işlemdir — 401 kabuğu kullanıcıyı giriş ekranına atmaya
    iterdi ve kullanıcı yaptığı işi kaybederdi.

    `action` denetim kaydına yazılır: hangi yıkıcı işlemde PIN'in yanlış
    girildiği, izde okunabilmeli.
    """
    identity = getattr(request.app.state, "identity", None)
    if identity is None:  # pragma: no cover — lifespan kurmadan istek gelmez
        raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

    row = await identity.store.fetch_one(
        "SELECT secret_lookup FROM users WHERE id = ?", (actor.id,)
    )
    stored = str((row or {}).get("secret_lookup") or "")
    if not stored or not hmac.compare_digest(stored, identity.secret_lookup(pin)):
        await identity.audit(actor.id, action, result="denied",
                             detail="PIN teyidi başarısız")
        raise HTTPException(status_code=403, detail="PIN doğrulanamadı.")
