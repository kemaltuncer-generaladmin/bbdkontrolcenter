"""Deneme Kulübü BBDSTORE izleyicisi (iş 81-km-kulup).

Ödenmiş (iptal/iade edilmemiş, simülasyon olmayan) her kulüp siparişi için Akademi'de TEK davet
açılır (source store, storeOrderRef = sipariş no, paymentMethod store); işlenen sipariş KM
tablosunda durur ve ikinci turda tekrar açılmaz; sonradan iptal/iade edilen siparişin daveti
iptal edilir; ayar kapalıyken hiçbir şey yapılmaz; hata → `failed` + uyarı + imleç geride kalır.
Mağaza (store.api) ve Akademi taklittir. Kişiler UYDURMA.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from modules.bbd_akademi.backend.club.watcher import StoreClubWatcher, local_phone, store_order
from modules.bbd_akademi.tests.akademi_fakes import Akademi, CheckedStore, Log


def row(number: str, *, paid: bool = True, phone: str | None = "+905321112233",
        updated: str = "2026-10-28T17:05:12+03:00", **extra: Any) -> dict[str, Any]:
    return {"orderId": int(number), "orderNumber": number, "paid": paid,
            "paidAt": "2026-10-28T17:05:12+03:00" if paid else None, "paymentType": "card",
            "simulated": False, "amountMinor": 2_000_000, "customerName": "Deniz Örnek",
            "phone": phone, "phoneValid": phone is not None and phone.startswith("+905"),
            "updatedAt": updated, "cancelled": False, "refunded": False, **extra}


class Store:
    """`store.api` taklidi — yalnız `bbd_trial_members`."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.down = False

    async def bbd_trial_members(self, filters: dict[str, Any] | None = None, *, page: int = 1,
                                per_page: int | None = None,
                                all_pages: bool = False) -> dict[str, Any]:
        self.calls.append(dict(filters or {}))
        if self.down:
            raise RuntimeError("mağaza kapalı")
        since = (filters or {}).get("updatedSince") or ""
        items = [item for item in self.rows if item["updatedAt"] >= since]
        return {"items": items, "meta": {}, "truncated": False}


class World:
    def __init__(self, akademi: Akademi, auto: bool = True) -> None:
        self.akademi = akademi
        self.auto = auto
        self.invites: dict[str, str] = {}
        self.cancel_status = 200
        self.create_status: int | None = None
        akademi.reply(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/v1/admin/settings":
            return httpx.Response(200, json={"club": {"autoInviteFromStore": self.auto}})
        if request.method == "POST" and path == "/v1/admin/club/invites":
            if self.create_status:
                return httpx.Response(self.create_status, json={"error": {
                    "code": "validation_error", "message": "Telefon geçersiz."}})
            body = json.loads(request.content)
            ref = body["storeOrderRef"]
            replay = ref in self.invites
            self.invites.setdefault(ref, f"00000000-0000-4000-8000-{len(self.invites):012d}")
            return httpx.Response(200 if replay else 201, json={"id": self.invites[ref],
                                                                 "status": "sent"})
        if request.method == "POST" and path.endswith(":cancel"):
            if self.cancel_status != 200:
                return httpx.Response(self.cancel_status, json={"error": {
                    "code": "club_invite_not_cancellable", "message": "İşlenmiş."}})
            return httpx.Response(200, json={"status": "cancelled"})
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "yok"}})

    def posts(self, suffix: str = "/v1/admin/club/invites") -> list[dict[str, Any]]:
        return [json.loads(body) for request, body in zip(self.akademi.calls,
                                                           self.akademi.bodies, strict=True)
                if request.method == "POST" and request.url.path.endswith(suffix)]


def watcher(gateway: Any, store: CheckedStore, log: Log, shop: Store | None) -> StoreClubWatcher:
    return StoreClubWatcher(gateway=gateway, store=store, log=log, store_api=lambda: shop,
                            interval=0)


async def test_odenmis_siparise_tek_davet(gateway: Any, store: CheckedStore, log: Log,
                                          akademi: Akademi) -> None:
    world = World(akademi)
    shop = Store()
    shop.rows = [row("1001"), row("1002", paid=False), row("1003", simulated=True),
                 row("1004", cancelled=True)]
    item = watcher(gateway, store, log, shop)
    result = await item.run_once()
    assert result == {"state": "ok", "seen": 4, "invited": 1, "failed": 0}
    [body] = world.posts()
    assert body == {"phone": "5321112233", "name": "Deniz Örnek", "source": "store",
                    "storeOrderRef": "1001", "amountMinor": 2_000_000, "paymentMethod": "store",
                    "paid": True, "paidAt": "2026-10-28T17:05:12+03:00"}
    create = next(r for r in akademi.calls if r.method == "POST")
    assert create.headers["Idempotency-Key"] == "store-1001"
    assert shop.calls[0] == {"paid": 1, "updatedSince": "1970-01-01T00:00:00Z", "sort": "updated"}

    # İkinci tur: imleç ilerledi, aynı sipariş tekrar gelse de davet açılmaz.
    await item.run_once()
    assert len(world.posts()) == 1
    assert shop.calls[1]["updatedSince"] == "2026-10-28T17:05:12+03:00"
    status = await item.status()
    assert status["state"] == "ok" and status["counts"]["invited"] == 1
    # Kişi verisi KM tablosunda ve log'da yok.
    rows = await store.fetch_all(f"SELECT * FROM {store.table('club_store_order')}")
    assert "5321112233" not in json.dumps(rows) and "Deniz" not in json.dumps(rows)
    assert "Deniz" not in json.dumps(log.lines, ensure_ascii=False, default=str)


async def test_iptal_iade_daveti_iptal_eder(gateway: Any, store: CheckedStore, log: Log,
                                            akademi: Akademi) -> None:
    world = World(akademi)
    shop = Store()
    shop.rows = [row("2001")]
    item = watcher(gateway, store, log, shop)
    await item.run_once()
    shop.rows = [row("2001", refunded=True, updated="2026-10-28T18:00:00+03:00")]
    await item.run_once()
    [cancel] = world.posts(":cancel")
    assert cancel == {"reason": "BBDSTORE siparişi iptal / iade edildi"}
    assert (await item.status())["counts"]["cancelled"] == 1
    # İşlenmiş davet iptal edilemezse uyarı listesine düşer.
    shop.rows = [row("2002", updated="2026-10-28T18:30:00+03:00")]
    await item.run_once()
    world.cancel_status = 409
    shop.rows = [row("2002", cancelled=True, updated="2026-10-28T19:00:00+03:00")]
    await item.run_once()
    problems = (await item.status())["problems"]
    assert problems[0]["orderNumber"] == "2002" and problems[0]["status"] == "cancel_failed"


async def test_ayar_kapaliysa_calismaz(gateway: Any, store: CheckedStore, log: Log,
                                       akademi: Akademi) -> None:
    world = World(akademi, auto=False)
    shop = Store()
    shop.rows = [row("3001")]
    item = watcher(gateway, store, log, shop)
    assert (await item.run_once())["state"] == "disabled"
    assert shop.calls == [] and world.posts() == []
    assert (await item.status())["message"].startswith("BBDSTORE'dan otomatik davet kapalı")


async def test_hata_uyari_ve_yeniden_deneme(gateway: Any, store: CheckedStore, log: Log,
                                           akademi: Akademi) -> None:
    world = World(akademi)
    world.create_status = 422
    shop = Store()
    shop.rows = [row("4001", updated="2026-10-28T17:00:00+03:00"),
                 row("4002", updated="2026-10-28T17:10:00+03:00"),
                 row("4003", phone="0332 111 22 33", updated="2026-10-28T17:20:00+03:00")]
    item = watcher(gateway, store, log, shop)
    result = await item.run_once()
    assert result["state"] == "partial" and result["failed"] == 2
    status = await item.status()
    assert status["state"] == "partial" and status["counts"]["failed"] == 2
    assert status["counts"]["invalidPhone"] == 1
    assert {p["orderNumber"] for p in status["problems"]} == {"4001", "4002", "4003"}
    assert any(line[0] == "warning" for line in log.lines)
    # İmleç en eski hatalı siparişte kaldı; Akademi düzelince açılır.
    world.create_status = None
    await item.run_once()
    assert shop.calls[1]["updatedSince"] == "2026-10-28T17:00:00+03:00"
    assert {body["storeOrderRef"] for body in world.posts()} == {"4001", "4002"}
    assert (await item.status())["counts"] == {"invited": 2, "failed": 0, "invalidPhone": 1,
                                               "cancelled": 0, "cancelFailed": 0}


async def test_magaza_yok_ya_da_kapali(gateway: Any, store: CheckedStore, log: Log,
                                      akademi: Akademi) -> None:
    World(akademi)
    assert (await watcher(gateway, store, log, None).run_once())["state"] == "store_missing"
    shop = Store()
    shop.down = True
    item = watcher(gateway, store, log, shop)
    assert (await item.run_once())["state"] == "store_unavailable"
    assert (await item.status())["storeConnected"] is True


def test_eski_satir_bicimi_ve_telefon() -> None:
    assert local_phone("+905321112233") == "5321112233"
    assert local_phone("0532 111 22 33") == "5321112233"
    assert local_phone("03321112233") is None
    order = store_order({"increment_id": "77", "membershipAmount": "20000.0000",
                         "billing": {"phone": "05321112233"}, "name": "Ad Soyad",
                         "updated_at": "2026-10-28 17:00:00", "paid": "1"})
    assert order is not None
    assert (order["orderNumber"], order["amountMinor"], order["phone"], order["paid"]) == \
        ("77", 2_000_000, "5321112233", True)
