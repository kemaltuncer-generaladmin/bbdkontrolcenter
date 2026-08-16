"""Ders Takvimi — HTTP yüzeyi. SALT OKUNUR.

`PUT /groups` ve `POST /adopt` KALDIRILDI. Bu ekran artık hiçbir şeyin sahibi
değil; yazma ucu bırakmak, arayüzde gizlenmiş ama backend'de açık duran bir
kapı olurdu (K9'un tersi). Eski uçlara gelen istek 405 alır ve bu doğrudur:
"burada yok" demek, sessizce kabul edip hiçbir yere yazmamaktan iyidir.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, CurrentUser, HTTPException, requires

from ..service import ScheduleService

router = APIRouter()

_service: ScheduleService | None = None


def bind(service: ScheduleService) -> APIRouter:
    global _service
    _service = service
    return router


def service() -> ScheduleService:
    if _service is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return _service


@router.get("/week")
async def read_week(
    user: CurrentUser = requires("bbd_class_schedule.view"),
) -> dict[str, Any]:
    """Zil Sistemi'ne girilmiş haftalık saatler ve gruplar."""
    return await service().read()
