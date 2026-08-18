"""Giriş denemesi hız sınırı.

NEDEN ŞİMDİ GEREKLİ. Kontrol Merkezi bugüne kadar yalnız `127.0.0.1`
dinliyordu: PIN'i denemek için makinenin başında olmak gerekiyordu ve altı
hane fazlasıyla yeterliydi. ADR 0026 ile backend internete bakıyor ve aynı
altı hane 1.000.000 ihtimal demek — saniyede birkaç deneme yapan biri günler
içinde bulur.

## Var olan koruma neden yetmiyor

`users.failed_attempts` sütunu duruyor ama ÇALIŞMIYOR ve tasarım gereği
çalışamaz: giriş kullanıcı adsızdır, kullanıcı PIN'in KENDİSİYLE bulunur
(`secret_lookup`). Yanlış bir PIN hiçbir satıra denk gelmez, dolayısıyla
sayacı artırılacak satır da yoktur. Sayaç yalnız doğru PIN'i bilip başka bir
yerde takılanı sayar — yani saldırganı hiç görmez.

Bu yüzden sınır KİŞİYE değil, İSTEĞİN GELDİĞİ YERE konur.

## İki katman

  · **Kaynak başına** (IP): kısa pencerede birkaç deneme. Normal bir kullanıcı
    PIN'ini yanlış girse bile bu sınıra takılmaz.
  · **Genel**: tek bir saldırgan binlerce IP'ye dağılabilir. Genel sınır,
    dağıtık denemeyi de yavaşlatır. Kasıtlı olarak GENİŞ tutulur — meşru
    kullanımın önüne geçmemeli.

Sayaçlar BELLEKTEDİR. Kalıcı olsaydı saldırgan veritabanını şişirebilirdi;
üstelik sunucu yeniden başladığında pencerenin sıfırlanması kabul edilebilir
bir maliyettir (yeniden başlatma saldırganın elinde değildir).
"""

from __future__ import annotations

import time
from collections import deque

#: Kaynak (IP) başına pencere ve sınır.
SOURCE_WINDOW_SECONDS = 60.0
SOURCE_LIMIT = 8

#: Tüm kurulum için pencere ve sınır. Meşru kullanımın çok üstünde.
GLOBAL_WINDOW_SECONDS = 60.0
GLOBAL_LIMIT = 60

#: Aynı anda izlenen en fazla kaynak. Aşılırsa en eski kaynak unutulur —
#: sınırsız sözlük, rastgele IP üreten bir saldırgan için bellek sızıntısıdır.
MAX_TRACKED_SOURCES = 10_000


class RateLimiter:
    """Kayan pencereli sayaç. Tek süreç, tek olay döngüsü — kilit gerekmez."""

    def __init__(
        self,
        *,
        source_limit: int = SOURCE_LIMIT,
        source_window: float = SOURCE_WINDOW_SECONDS,
        global_limit: int = GLOBAL_LIMIT,
        global_window: float = GLOBAL_WINDOW_SECONDS,
    ) -> None:
        self._source_limit = source_limit
        self._source_window = source_window
        self._global_limit = global_limit
        self._global_window = global_window
        self._by_source: dict[str, deque[float]] = {}
        self._all: deque[float] = deque()

    def check(self, source: str, *, now: float | None = None) -> float | None:
        """Deneme kabul edilir mi? Edilmezse KAÇ SANİYE sonra denenebileceği.

        Denemeyi de KAYDEDER: çağıran ayrıca "saydır" demek zorunda kalsaydı,
        bir yolda unutulur ve sınır sessizce delinirdi.
        """
        moment = time.monotonic() if now is None else now

        _trim(self._all, moment - self._global_window)
        stamps = self._by_source.setdefault(source, deque())
        _trim(stamps, moment - self._source_window)

        if len(stamps) >= self._source_limit:
            return max(0.0, stamps[0] + self._source_window - moment)
        if len(self._all) >= self._global_limit:
            return max(0.0, self._all[0] + self._global_window - moment)

        stamps.append(moment)
        self._all.append(moment)
        self._forget_oldest_if_needed()
        return None

    def reset(self, source: str) -> None:
        """Başarılı girişten sonra kaynağın sayacı temizlenir.

        Doğru PIN'i giren kişi, aynı ofisten çalışan bir başkasının yanlış
        denemeleri yüzünden kilitlenmemeli.
        """
        self._by_source.pop(source, None)

    def _forget_oldest_if_needed(self) -> None:
        while len(self._by_source) > MAX_TRACKED_SOURCES:
            self._by_source.pop(next(iter(self._by_source)), None)


def _trim(stamps: deque[float], cutoff: float) -> None:
    while stamps and stamps[0] < cutoff:
        stamps.popleft()
