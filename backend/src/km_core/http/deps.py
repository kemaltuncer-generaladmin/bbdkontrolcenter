"""Uç nokta düzeyinde izin kapısı.

Router'ın tamamı `module.yaml` → `http.requires` ile taban izne bağlanır; tek
tek uç noktalar bunu DARALTIR (genişletemez). Modüller bunu `km_sdk.requires`
üzerinden kullanır — çekirdeği import etmezler (K2).

    @router.post("/students")
    async def create(user: CurrentUser = requires("bbd_students.manage")):
        ...
"""

from __future__ import annotations

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
