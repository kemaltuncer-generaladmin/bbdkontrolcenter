"""Ağır taramalar için arka plan önbelleği — İSTEK BEKLEMEZ.

NEDEN VAR (canlıda ölçüldü): mağaza ekranlarının çoğu, listeyi çizebilmek için
önce nüfusun/siparişlerin TAMAMINI tarıyordu. Tarama `collect_all` ile sayfa
sayfa ve sırayla yapılıyor, geçidin hız kovası dakikada 55 isteğe izin veriyor;
1.400 kayıt ≈ 29 sayfa, 600 sipariş ≈ 12 sayfa, ikisi birden dakikaları buluyor.
Kabuk ise isteği **60 saniyede** kesiyor (`apps/desktop/src-tauri/src/main.rs`).
Sonuç: müşteri ekranı hiç açılmıyordu — panel zaman aşımını yakalayıp boş tablo
ve kırmızı durum satırı gösteriyordu. Ekranın kendisi doğruydu; onu besleyen
istek hiç dönmüyordu.

ÇÖZÜM: taramayı istekten AYIR. `read()` hiçbir zaman taramayı beklemez; son
bilinen değeri hemen verir ve gerekiyorsa arka planda yenisini başlatır. İlk
açılışta değer yoktur — o zaman `state: "running"` döner, ekran listeyi yine
gösterir, taramaya bağlı sütunlar "Bilinmiyor" kalır ve tarama bitince ekran
bir kez tazelenir. SIFIR UYDURULMAZ.

NEDEN GEÇİTTE: aynı desen yedi mağaza modülünde tekrarlanıyordu
(`store_customers`, `store_reports`, `store_dashboard`, `store_promotions`,
`store_invoices`, `store_orders`, `store_products`, `store_refunds`). Her biri
kendi kilidini, kendi TTL'ini ve kendi "bayat mı" hesabını yazsaydı — ki
yazmıştı — yedi yerde ayrı ayrı yanlış olurdu. Geçit zaten hepsinin ortak
bağımlılığı (`depends: [store_api]`), yetenek buradan sunulur (K3).

ÖNBELLEK DEĞİL, TAZELEYİCİ: burada tutulan şey mağazadan gelen ham liste değil,
ÇAĞIRANIN hesapladığı toplulaştırmadır (müşteri başına sipariş sayısı gibi).
Ham liste önbelleğe alınmaz — personel "kaydettim ama listede yok" yaşamamalı
(bkz. `cache.py`). Değerler yalnız BELLEKTEDİR, diske yazılmaz: kişisel verinin
ikinci bir kopyası olmasın (KVKK).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

#: Bir taramanın taze sayıldığı süre. Çağıran kendi TTL'ini geçebilir.
DEFAULT_TTL = 300

#: `read(wait=...)` ile beklenebilecek en uzun süre. Kabuğun 60 saniyelik
#: kesme sınırının çok altında tutulur: bekleme bir KOLAYLIKTIR, tarama
#: hızlıysa ilk açılışta da dolu ekran verir; ama hiçbir koşulda isteğin
#: dönmesini geciktirmeye yetkisi yoktur.
MAX_WAIT = 5.0

#: Başarısız taramadan sonra en erken yeniden deneme. Mağaza düşmüşken her
#: ekran tazelemesinin yeni bir tarama başlatması, düşmüş sunucuyu döverdi.
RETRY_AFTER = 60

STATE_EMPTY = "empty"
STATE_RUNNING = "running"
STATE_READY = "ready"
STATE_ERROR = "error"

Loader = Callable[[], Awaitable[Any]]


def _now() -> str:
    return datetime.now(UTC).astimezone().isoformat(timespec="seconds")


class _Entry:
    """Tek bir tarama anahtarının durumu.

    `value is None` = HİÇ başarılı olmadı. Bu ayrım önemli: boş liste dönen
    başarılı bir tarama ile hiç yapılmamış tarama aynı şey değildir; ikincisinde
    ekran "bilinmiyor" demeli, "hiç yok" dememeli.
    """

    __slots__ = ("at", "error", "error_at", "stamp", "task", "value")

    def __init__(self) -> None:
        self.value: Any = None
        self.at: float = 0.0          # monotonik — başarı anı
        self.stamp: str = ""          # duvar saati — ekranda gösterilir
        self.error: str = ""
        self.error_at: float = 0.0
        self.task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


def _report(entry: _Entry | None, life: int) -> dict[str, Any]:
    """Çağırana verilen zarf. İstisna taşımaz, her zaman doludur."""
    if entry is None:
        return {"state": STATE_EMPTY, "value": None, "stale": True, "running": False,
                "at": "", "ageSeconds": 0, "error": ""}

    running = entry.running
    age = int(time.monotonic() - entry.at) if entry.at > 0.0 else 0
    has_value = entry.value is not None
    # `at == 0` iki anlama gelir: hiç taranmadı YA DA `invalidate()` bayatlattı.
    # İkisinde de yaş sıfırdır ama değer taze DEĞİLDİR; yaşa bakıp "taze" demek
    # bayatlatmayı sessizce etkisiz bırakırdı.
    stale = (not has_value) or entry.at <= 0.0 or age >= life

    if has_value:
        state = STATE_READY
    elif running:
        state = STATE_RUNNING
    elif entry.error:
        state = STATE_ERROR
    else:
        state = STATE_EMPTY

    return {
        "state": state,
        "value": entry.value,
        "stale": stale,
        "running": running,
        "at": entry.stamp,
        "ageSeconds": age,
        "error": entry.error,
    }


class BackgroundScan:
    """Anahtar başına en çok bir tarama koşar; çağıran hiç beklemez."""

    def __init__(self, *, log: Any = None, default_ttl: int = DEFAULT_TTL) -> None:
        self._log = log
        self._ttl = max(1, int(default_ttl))
        self._entries: dict[str, _Entry] = {}

    def scoped(self, namespace: str) -> ScopedScan:
        """Modüle özel görünüm. Anahtarlar önekle ayrılır: iki modülün
        "orders" anahtarı birbirinin sonucunu okumasın."""
        return ScopedScan(self, str(namespace))

    def peek(self, key: str) -> dict[str, Any]:
        """Tarama başlatmadan durum sorar — yoklama uçları için."""
        return _report(self._entries.get(key), self._ttl)

    async def read(self, key: str, loader: Loader, *, ttl: int | None = None,
                   refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        """Son bilinen değeri döndürür; gerekiyorsa arka planda yeniler.

        `wait` verilirse en çok o kadar (ve en çok `MAX_WAIT`) beklenir. Bekleme
        boşa çıkarsa hata değildir: bayat/boş zarf döner, ekran nedenini yazar.
        """
        entry = self._entries.get(key)
        if entry is None:
            entry = _Entry()
            self._entries[key] = entry

        life = max(1, int(ttl if ttl else self._ttl))
        now = time.monotonic()
        fresh = entry.at > 0.0 and (now - entry.at) < life

        if fresh and not refresh:
            return _report(entry, life)

        # Az önce patladıysa hemen tekrar denenmez — ama kullanıcı açıkça
        # "yenile" dediyse (`refresh`) beklenmez, denenir.
        cooling = (entry.error_at > 0.0 and (now - entry.error_at) < RETRY_AFTER)
        if cooling and not refresh:
            return _report(entry, life)

        self._start(key, entry, loader)
        task = entry.task
        if wait > 0.0 and task is not None and not task.done():
            await asyncio.wait({task}, timeout=min(float(wait), MAX_WAIT))
        return _report(entry, life)

    def invalidate(self, key: str) -> None:
        """Değeri BAYAT işaretler; silmez.

        Yazma sonrası çağrılır. Değeri silmek, bir sonraki ekranı boş bırakır
        ve "kaydettim, kayboldu" hissi verirdi; bayat işaretlemek eskiyi
        göstermeye devam eder ve arka planda yenisini getirir. Soğuma sayacı da
        sıfırlanır: kullanıcının kendi yazması yeniden denemeyi hak eder.
        """
        entry = self._entries.get(key)
        if entry is None:
            return
        entry.at = 0.0
        entry.error_at = 0.0

    async def close(self) -> None:
        """Koşan taramaları iptal eder. Testlerin sızıntı bırakmaması için."""
        tasks = [entry.task for entry in self._entries.values()
                 if entry.task is not None and not entry.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._entries.clear()

    # ---------------------------------------------------------------- iç

    def _start(self, key: str, entry: _Entry, loader: Loader) -> None:
        if entry.running:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - async bağlam dışından çağrı
            return
        # Görev nesnesine GÜÇLÜ referans `entry.task`'ta durur; aksi hâlde
        # asyncio zayıf referans tuttuğu için tarama ortasında toplanabilir.
        entry.task = loop.create_task(self._run(key, entry, loader),
                                      name=f"store-scan:{key}")

    async def _run(self, key: str, entry: _Entry, loader: Loader) -> None:
        try:
            value = await loader()
        except asyncio.CancelledError:
            raise
        except Exception as failure:  # noqa: BLE001 — tarama çağıranı düşürmez (K7)
            entry.error = str(failure).strip() or "Tarama yapılamadı."
            entry.error_at = time.monotonic()
            if self._log is not None:
                self._log.warning("arka plan taraması başarısız", key=key,
                                  error=entry.error)
            return
        entry.value = value
        entry.at = time.monotonic()
        entry.stamp = _now()
        entry.error = ""
        entry.error_at = 0.0


@dataclass(frozen=True, slots=True)
class ScopedScan:
    """Bir modülün `BackgroundScan` üzerindeki kendi köşesi."""

    scan: BackgroundScan
    namespace: str

    def _key(self, key: str) -> str:
        return f"{self.namespace}:{key}"

    def peek(self, key: str) -> dict[str, Any]:
        return self.scan.peek(self._key(key))

    def invalidate(self, key: str) -> None:
        self.scan.invalidate(self._key(key))

    async def read(self, key: str, loader: Loader, *, ttl: int | None = None,
                   refresh: bool = False, wait: float = 0.0) -> dict[str, Any]:
        return await self.scan.read(self._key(key), loader, ttl=ttl,
                                    refresh=refresh, wait=wait)
