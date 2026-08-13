"""Bagisto sayfalaması → tam liste toplayıcı.

Bagisto admin API'si koleksiyonları `{data: [...], meta: {...}}` zarfıyla
döner (`AdminCollectionEnvelopeNormalizer`). `meta` alanları camelCase'tir:
`currentPage · perPage · lastPage · total · from · to`.

SUNUCU SINIRI 50. Kaynak koddaki `AbstractAdminCollectionProvider`
(`MAX_PER_PAGE = 50`) istenen `per_page` değerini sessizce 50'ye kırpar —
100 istemek hata vermez, yalnız yarım sayfa döner ve "hepsini aldım" sanan
istemci veri kaybeder. Bu yüzden sayfa boyu burada, istek çıkmadan kırpılır.

1.419 ürünün tamamı 29 sayfa eder. Sayfalar SIRAYLA çekilir; paralel
çekmek kantinde kilit yüzünden 5xx üretmişti (bkz. `bbd_canteen_api`
`snapshot`) ve Bagisto tarafında da her sayfa ağır bir datagrid sorgusudur.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: Sunucunun kabul ettiği en büyük sayfa boyu (`MAX_PER_PAGE`).
MAX_PER_PAGE = 50

#: Tam tarama üst sınırı. 1.419 ürün + ~600 sipariş bu sınırın altında;
#: sınır, bozuk `meta` yüzünden sonsuz döngüye girmemek için var.
HARD_ITEM_LIMIT = 5_000
HARD_PAGE_LIMIT = 400


def clamp_page_size(value: Any, *, fallback: int = MAX_PER_PAGE) -> int:
    """Sayfa boyunu 1..50 aralığına çeker."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = fallback
    if size <= 0:
        size = fallback
    return min(max(size, 1), MAX_PER_PAGE)


def envelope(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`{data, meta}` zarfını açar.

    Zarfsız yanıtlar da gelir: bazı uçlarda (`/catalog/categories/tree`,
    `/configuration`) sayfalama kapalıdır ve düz liste döner. Böyle bir
    yanıtı hata saymak ekranı boşuna düşürür — liste olarak kabul edilir.
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        meta = payload.get("meta")
        if isinstance(data, list):
            return list(data), dict(meta or {})
        # Tekil kaynak ya da düz sözlük: tek elemanlı liste gibi davranmaz,
        # çağıran zaten sözlük bekliyorsa `_item` yolunu kullanır.
        return [], dict(meta or {})
    if isinstance(payload, list):
        return list(payload), {}
    return [], {}


def page_params(page: int, per_page: int) -> dict[str, int]:
    """Bagisto'nun beklediği sayfalama parametreleri."""
    return {"page": max(1, int(page)), "per_page": clamp_page_size(per_page)}


async def collect_all(
    fetch: Callable[[int, int], Awaitable[Any]],
    *,
    page_size: int = MAX_PER_PAGE,
    max_items: int = HARD_ITEM_LIMIT,
) -> dict[str, Any]:
    """Bütün sayfaları SIRAYLA toplar.

    `fetch(page, per_page)` ham yanıtı döndürmeli. Sonuç:
    `{items, total, pages, truncated}`. `truncated` True ise sunucuda daha
    çok kayıt var ama sınıra dayanıldı — ekran bunu SÖYLEMELİ, sessizce
    eksik liste göstermemeli.
    """
    size = clamp_page_size(page_size)
    items: list[dict[str, Any]] = []
    total = 0
    pages = 0
    truncated = False

    page = 1
    while page <= HARD_PAGE_LIMIT:
        payload = await fetch(page, size)
        rows, meta = envelope(payload)
        pages = page
        if not rows:
            break

        items.extend(rows)
        total = int(meta.get("total") or 0) or total

        if len(items) >= max_items:
            del items[max_items:]
            truncated = True
            break

        last_page = int(meta.get("lastPage") or 0)
        if last_page:
            if page >= last_page:
                break
        elif len(rows) < size:
            # `meta` yoksa son sayfayı eksik dolu sayfadan anlarız.
            break
        page += 1
    else:  # pragma: no cover - 400 sayfa = 20.000 kayıt, sınıra ulaşılmaz
        truncated = True

    if not total:
        total = len(items)
    return {"items": items, "total": total, "pages": pages, "truncated": truncated}
