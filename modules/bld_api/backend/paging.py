"""BLD sayfalaması → zarf açıcı ve tam liste toplayıcı.

SUNUCUNUN PARAMETRE ADLARI DOĞRULANDI, VARSAYILMADI. Kaynak:
`platform/extensions/veykemtu/bridgeapi/src/Http/Controllers/OrderController.php`
→ `index()`:

    $perPage = min(100, max(1, (int) $request->query('per_page', '25')));
    $page    = max(1, (int) $request->query('page', '1'));

ve yanıt `meta` alanları `page · per_page · total · last_page`
(`docs/control/00-genel.md` §5 bu biçimi dondurdu). Yani parametre adları
`page` + `per_page`, **snake_case**, ve `meta.last_page` da snake_case.

NEDEN BU DOSYA VAR — KARDEŞ GEÇİTTE YAŞANMIŞ HATA. `store_api` sayfa boyunu
`per_page` diye gönderiyordu; BBD paketi isteği `limit` diye okuyordu ve
Laravel tanımadığı sorgu parametresini SESSİZCE yok sayıyor. Geçit 50 satır
istediğini sanarken 25 alıyordu, üstelik `collect_all` "eksik dolu sayfa =
son sayfa" kuralına düşüp taramayı ilk sayfada bitiriyordu — yani "hepsini
aldım" diyen sessiz bir veri kaybı. Ad bu yüzden burada, denetleyici kodundan
okunarak sabitlenir.

`limit` / `offset` BLD'de **kullanılmaz.** Tek istisna mevcut
`GET /api/control/kds/print-jobs` ucudur: orada `limit` yayınlanmış bir alan
ve `AGENTS.md` §2.3 gereği değişmez — o uç bu dosyadan geçmez.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

#: Sunucunun varsayılan sayfa boyu (`per_page` verilmezse).
DEFAULT_PER_PAGE = 25

#: Denetim izi ekranının varsayılanı (`00-genel.md` §5, tek istisna): yirmi
#: beşer satır, "dün ne oldu" sorusunu cevaplamak için sekiz kez sayfa
#: çevirmek demekti.
AUDIT_PER_PAGE = 50

#: Sunucunun kabul ettiği en büyük sayfa boyu. Üstü sessizce kırpılır
#: (`min(100, ...)`), yani 250 istemek hata vermez — yalnız 100 döner ve
#: "hepsini aldım" sanan istemci veri kaybeder. Bu yüzden burada kırpılır.
MAX_PER_PAGE = 100

#: Tam tarama üst sınırları. Sınır, bozuk `meta` yüzünden sonsuz döngüye
#: girmemek için var; normal veri kümeleri (birkaç yüz ürün, birkaç bin
#: sipariş) bunun çok altında.
HARD_ITEM_LIMIT = 5_000
HARD_PAGE_LIMIT = 400


def clamp_page_size(value: Any, *, fallback: int = DEFAULT_PER_PAGE) -> int:
    """Sayfa boyunu 1..100 aralığına çeker."""
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = fallback
    if size <= 0:
        size = fallback
    return min(max(size, 1), MAX_PER_PAGE)


def page_params(page: Any, per_page: Any) -> dict[str, int]:
    """Sunucunun beklediği sayfalama parametreleri: `page` + `per_page`."""
    try:
        number = int(page)
    except (TypeError, ValueError):
        number = 1
    return {"page": max(1, number), "per_page": clamp_page_size(per_page)}


def envelope(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """`{data, meta}` zarfını açar.

    Sözleşme (`00-genel.md` §5) sayfalanan uçlarda `data` + `meta`, sayfalanmayan
    liste uçlarında yalnız `data` diyor; bazı sayfalanmayan uçlar yine de özet
    taşıyan bir `meta` veriyor (`cms/posts` → `meta.categories`,
    `sms/log` → `meta.segment_total`). Üçü de burada aynı biçimde açılır.

    Zarfsız düz dizi de kabul edilir: eski `control/kds` uçları öyle dönüyor ve
    böyle bir yanıtı hata saymak ekranı boşuna düşürürdü.
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        meta = payload.get("meta")
        if isinstance(data, list):
            return [dict(row) for row in data if isinstance(row, dict)], dict(meta or {})
        # Tekil kaynak ya da düz sözlük: liste gibi davranmaz. Çağıran sözlük
        # bekliyorsa `BldApi._object` yolunu kullanır.
        return [], dict(meta or {})
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)], {}
    return [], {}


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

    Sayfalar sırayla çekilir. Paralel çekmek hız kovasını tek anda boşaltır
    ve sunucu tarafında her sayfa ağır bir sorgudur.
    """
    size = clamp_page_size(page_size)
    items: list[dict[str, Any]] = []
    total = 0
    pages = 0
    truncated = False

    page = 1
    while page <= HARD_PAGE_LIMIT:
        rows, meta = envelope(await fetch(page, size))
        pages = page
        if not rows:
            break

        items.extend(rows)
        total = int(meta.get("total") or 0) or total

        if len(items) >= max_items:
            del items[max_items:]
            truncated = True
            break

        last_page = int(meta.get("last_page") or 0)
        if last_page:
            if page >= last_page:
                break
        elif len(rows) < size:
            # `meta` yoksa son sayfayı eksik dolu sayfadan anlarız.
            break
        page += 1
    else:  # pragma: no cover - 400 sayfa = 40.000 kayıt, sınıra ulaşılmaz
        truncated = True

    if not total:
        total = len(items)
    return {"items": items, "total": total, "pages": pages, "truncated": truncated}
