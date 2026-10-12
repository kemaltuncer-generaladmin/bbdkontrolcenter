"""Kantin Geçidi — HTTP yüzeyi: kantinden gelen olaylar (km-merkezi-senkron §6.1).

TEK UÇ: `POST /api/bbd_canteen_api/v1/events`. İstemci kantin backend'idir;
kimliği ADR 0028 servis anahtarıdır (`Authorization: Bearer kmsk_…`, önerilen
rol `canteen_service`, anahtarda `allowed_cidrs` = kantin sunucusu). Çift kapı
(K9): router `bbd_canteen_api.events.push` ister, uç da ayrıca sorar.

Gövde (kantin `KmEventPusher`): `{"source": "canteen", "sentAt": iso,
"events": [{id, type, opaqueId, payload, createdAt}]}` (en çok 500). Yanıt
`{"received", "duplicates", "lastId"}`; 2xx'te kantin satırları itilmiş sayar.
"""

from __future__ import annotations

from typing import Any

from km_sdk import APIRouter, BaseModel, CurrentUser, Field, HTTPException, requires

from ..events import CanteenEvents

router = APIRouter()

_events: CanteenEvents | None = None


def bind(events: CanteenEvents) -> APIRouter:
    global _events
    _events = events
    return router


class EventBatch(BaseModel):
    """Kantin olay paketi. `items` eski adla da kabul edilir."""

    source: str | None = Field(default=None, max_length=32)
    sentAt: str | None = Field(default=None, max_length=40)
    events: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    items: list[dict[str, Any]] | None = Field(default=None, max_length=500)


@router.post("/events")
async def push_events(
    body: EventBatch,
    user: CurrentUser = requires("bbd_canteen_api.events.push"),
) -> dict[str, int]:
    if _events is None:  # pragma: no cover - yükleme sırası garanti eder
        raise HTTPException(status_code=503, detail="Modül hazır değil.")
    return await _events.receive(list(body.events or body.items or []))
