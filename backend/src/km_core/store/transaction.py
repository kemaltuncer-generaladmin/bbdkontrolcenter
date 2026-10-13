"""Modül işlemi (ADR 0029) — iki motorun ortak işlem nesnesi.

`Store.transaction()` (SQLite) ve `PostgresStore.transaction()` aynı biçimde
davranır:

  · işlem `contextvars` ile TAŞINIR: işlem bloğu içindeki her `fetch_*` /
    `execute` çağrısı — kim çağırırsa çağırsın (modülün `ScopedStore`'u, bir
    platform yeteneği) — aynı bağlantıya gider. Çağıranın bağlantı nesnesi
    taşıması gerekmez;
  · İÇ İÇE ÇAĞRI DIŞ İŞLEME KATILIR. Kayıt noktası (savepoint) yoktur: içte
    yükselen hata dışarı çıkarsa bütün işlem geri alınır. İçteki hatayı yakalayıp
    devam eden çağıran, iç bloğun o ana kadarki yazmalarıyla devam eder;
  · işlem başka depoya geçmez: bağlam değişkeni hangi deponun işlemi olduğunu
    taşır, başka bir depo nesnesi onu görmez.

KANCALAR. Platform yetenekleri (ör. `events.outbox`) commit'e bağlanabilir:
`before_commit` commit'ten hemen önce, AYNI bağlantıda koşar (hata verirse
işlem geri alınır); `after_commit` ancak commit başarılıysa, `after_end` her
durumda (commit ya da geri alma) bir kez koşar. Kilit bırakma gibi temizlik
`after_end`e yazılır.

İŞLEM GÖVDESİNDE DIŞ ÇAĞRI YAPILMAZ. Ağ isteği, başka bir görevi beklemek ya da
uzun hesap işlem süresince bağlantıyı (SQLite'ta bütün depoyu) tutar. Kantin
gibi dış sistemlere yazma işlem BİTTİKTEN sonra yapılır (km-merkezi-senkron
§7: önce KM, sonra kantin).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import structlog

log = structlog.get_logger("core.store.transaction")

Hook = Callable[[], Awaitable[None] | None]


class Transaction:
    """Açık işlemin tanıtıcısı. Modül bunu yalnız bağlam yöneticisi olarak görür."""

    __slots__ = ("_after_commit", "_after_end", "_before_commit", "closed", "connection",
                 "depth", "owner", "state")

    def __init__(self, owner: object, connection: Any = None) -> None:
        #: İşlemin ait olduğu depo nesnesi. Başka depo bu işlemi görmez.
        self.owner = owner
        #: PostgreSQL'de havuzdan alınan bağlantı; SQLite'ta None (tek bağlantı).
        self.connection = connection
        self.depth = 1
        self.closed = False
        #: Yeteneklerin işlem başına küçük durumu (ör. outbox'un tampon olayları).
        self.state: dict[str, Any] = {}
        self._before_commit: list[Hook] = []
        self._after_commit: list[Hook] = []
        self._after_end: list[Hook] = []

    # ------------------------------------------------------------ kancalar

    def before_commit(self, hook: Hook) -> None:
        self._before_commit.append(hook)

    def after_commit(self, hook: Hook) -> None:
        self._after_commit.append(hook)

    def after_end(self, hook: Hook) -> None:
        self._after_end.append(hook)

    async def run_before_commit(self) -> None:
        # Kanca başka kanca ekleyebilir (ör. ilk olay kilidi alır): liste
        # dolaşılırken büyüyebilir, bu yüzden sırayla tüketilir.
        index = 0
        while index < len(self._before_commit):
            result = self._before_commit[index]()
            if result is not None:
                await result
            index += 1

    async def run_after(self, *, committed: bool) -> None:
        """Commit sonrası ve bitiş kancaları. YÜKSELTMEZ: işlem zaten bitti."""
        hooks = (self._after_commit if committed else []) + self._after_end
        self._after_commit, self._after_end, self._before_commit = [], [], []
        for hook in hooks:
            try:
                result = hook()
                if result is not None:
                    await result
            except Exception as failure:  # noqa: BLE001 — işlem bitti, kanca onu bozamaz
                log.error("işlem kancası patladı", error=str(failure))


#: Görev bağlamındaki açık işlem. Alt görevler bağlamı kopyalar: işlem içinde
#: `asyncio.gather` ile koşan sorgular da aynı işleme katılır. İşlem bittikten
#: sonra yaşayan bir alt görev `closed` işlemi görür ve normal yola düşer.
CURRENT: ContextVar[Transaction | None] = ContextVar("km_store_transaction", default=None)


def active_for(owner: object) -> Transaction | None:
    """Bu deponun bağlamdaki AÇIK işlemi; yoksa None."""
    transaction = CURRENT.get()
    if transaction is None or transaction.closed or transaction.owner is not owner:
        return None
    return transaction
