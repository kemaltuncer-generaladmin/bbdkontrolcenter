"""Kurumun Akademi özellik bayrakları — panel menüsü için salt okunur özet.

NEDEN. Panel "yalnız açık olan görünür" ilkesiyle çizilir: kurumda kapalı bir
özelliğin (erken uyarı, bilgi kartları, kurum carisi …) bölümü menüde HİÇ
görünmez; kullanıcı `403 feature_disabled` hata kartıyla karşılaşmaz. Bunun
için panel açılışında hangi bayrakların açık olduğunu bilmesi gerekir.

NEREDEN. Akademi `admin_listFeatures` (`GET /v1/admin/features`) — KM paneli
anahtarı `km_panel` kümesiyle bu uca açıktır. Okuma SUNUCU tarafında, modülün
kendi adına (sistem aktörü) yapılır: bayrağın açık/kapalı olduğu bilgisi bir
yetki değil, ekran düzenidir; KM kullanıcısının `bbd_akademi.settings` iznini
taşıması gerekmez. Bayrağı DEĞİŞTİRMEK yine Ayarlar → Özellikler'dedir ve
vekilden kullanıcının izniyle geçer (K9).

HIZ (A14). Sonuç 60 sn önbellekte durur; aynı anda gelen açılışlar tek istek
paylaşır. Akademi yavaşsa açılış beklemez: `WAIT_SECONDS` sonra `None` döner
(panel her bölümü gösterir, karar Akademi'de kalır). Ayarlar'da bayrak
değişince panel `fresh=1` ile önbelleği atlar.

GÜVENLİ TARAF. Okunamazsa `None` döner ve panel bugünkü gibi davranır (her
bölüm görünür); "bilmiyorum" hiçbir zaman "kapalı" demek değildir.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from .gateway import AkademiError, AkademiGateway, on_behalf_of, request_id

ACTOR = ("bbd_akademi", "Panel menüsü")
TTL_SECONDS = 60.0
WAIT_SECONDS = 3.0


class TenantFeatures:
    """`{bayrak: açık mı}` özeti — `/km/status` → `features`."""

    def __init__(self, *, gateway: AkademiGateway, log: Any,
                 ttl_seconds: float = TTL_SECONDS, wait_seconds: float = WAIT_SECONDS) -> None:
        self._gateway = gateway
        self._log = log
        self._ttl = ttl_seconds
        self._wait = wait_seconds
        self._value: dict[str, bool] | None = None
        self._at = 0.0
        self._inflight: asyncio.Task[dict[str, bool] | None] | None = None

    def invalidate(self) -> None:
        self._at = 0.0

    async def snapshot(self, *, fresh: bool = False) -> dict[str, bool] | None:
        if not fresh and self._value is not None and time.monotonic() - self._at < self._ttl:
            return self._value
        if self._inflight is None or self._inflight.done():
            self._inflight = asyncio.ensure_future(self._load())
        try:
            # shield: bekleyen açılış vazgeçse de okuma tamamlanıp önbelleğe girer.
            return await asyncio.wait_for(asyncio.shield(self._inflight), self._wait)
        except TimeoutError:
            self._log.warning("akademi özellik bayrakları zamanında okunamadı")
            return self._value

    async def _load(self) -> dict[str, bool] | None:
        try:
            headers = {"accept": "application/json",
                       "X-Akademi-On-Behalf-Of": on_behalf_of(*ACTOR, kind="system"),
                       "X-Request-Id": request_id(None)}
            response = await self._gateway.send("GET", "/v1/admin/features", headers=headers)
            try:
                raw = await response.aread()
            finally:
                await response.aclose()
        except AkademiError:          # anahtar yok, şalter kapalı, Akademi'ye ulaşılamıyor
            return self._value
        except Exception as failure:  # noqa: BLE001 — menü özeti açılışı düşürmez (K7)
            self._log.warning("akademi özellik bayrakları okunamadı", error=type(failure).__name__)
            return self._value
        if response.status_code != 200:
            self._log.warning("akademi özellik bayrakları okunamadı", status=response.status_code)
            return self._value
        parsed = parse(raw)
        if parsed is not None:
            self._value, self._at = parsed, time.monotonic()
        return parsed if parsed is not None else self._value


def parse(raw: bytes) -> dict[str, bool] | None:
    """`FeatureFlagList` → `{key: enabled}`; biçim tanınmazsa `None`."""
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        return None
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return None
    flags: dict[str, bool] = {}
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("key"), str) \
                and isinstance(item.get("enabled"), bool):
            flags[item["key"]] = item["enabled"]
    return flags
