"""Uç nokta düzeyinde izin kapısı.

Router'ın tamamı `module.yaml` → `http.requires` ile taban izne bağlanır; tek
tek uç noktalar bunu DARALTIR (genişletemez). Modüller bunu `km_sdk.requires`
üzerinden kullanır — çekirdeği import etmezler (K2).

    @router.post("/students")
    async def create(user: CurrentUser = requires("bbd_students.manage")):
        ...
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, Request

from km_core.security.identity import CurrentUser


def split_permission(entry: str) -> tuple[str, str | None]:
    key, _, scope = entry.partition(":")
    return key, (scope or None)


def requires(*permissions: str) -> Any:
    """Verilen izinlerden EN AZ BİRİNE sahip kullanıcıyı döndürür."""
    if not permissions:
        raise ValueError("İzin belirtilmeyen uç nokta tanımlanamaz (K9).")

    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> CurrentUser:
        identity = getattr(request.app.state, "identity", None)
        if identity is None:
            raise HTTPException(status_code=503, detail="Kimlik hazır değil.")

        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else ""
        user = await identity.resolve_session(token) if token else None
        if user is None:
            raise HTTPException(status_code=401, detail="Oturum yok veya süresi doldu.")

        for entry in permissions:
            key, scope = split_permission(entry)
            if user.has_permission(key, scope):
                return user

        await identity.audit(
            user.id, "permission.denied", result="denied", detail=",".join(permissions)
        )
        raise HTTPException(status_code=403, detail="Bu işlem için yetkiniz yok.")

    return Depends(dependency)
