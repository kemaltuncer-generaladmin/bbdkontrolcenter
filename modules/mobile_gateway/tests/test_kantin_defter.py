"""Kantin hesap dökümü (`canteen.api.ledger`, kantin §1.7) üzerinden §3 uçları.

- `GET /students/{id}/canteen` defter yolunda da, eski yolda da (defter ucu
  olmayan kantin / defter bilmeyen istemci) AYNI yanıtı verir — sözleşme v1.
- `GET /students/{id}/canteen/transactions` ve `/collections` imleçlidir:
  `{items, nextCursor, hasMore}`; imleç geçidin opak imlecidir, kantinin
  imlecini olduğu gibi taşır.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from mobile_gateway_fakes import CAN, ELIF, LEDGER, MOTHER, PreLedgerCanteen

World = Any  # conftest.World


def error_of(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()["error"]
    assert body["code"] == code
    return body


async def walk(client: httpx.AsyncClient, path: str,
               **params: Any) -> tuple[list[dict[str, Any]], int]:
    """İmleci sonuna kadar izler; (bütün satırlar, sayfa sayısı)."""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(path, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "nextCursor", "hasMore"}
        assert body["hasMore"] is (body["nextCursor"] is not None)
        items.extend(body["items"])
        pages += 1
        cursor = body["nextCursor"]
        if cursor is None:
            return items, pages
        assert pages < 50


def sales(world: World, count: int) -> None:
    """ELIF'e `count` satış (yeniden eskiye kimlik: 50_000 − n)."""
    base = LEDGER[1]
    world.canteen.ledger_rows = [
        {**base, "id": 50_000 - n, "ref": f"l-{n}",
         "sale": {**base["sale"], "serverId": f"tx-{n}", "createdAt": base["at"] - n * 1000}}
        for n in range(count)
    ]


# ------------------------------------------------------------- §3 özeti


@pytest.mark.parametrize("student", [ELIF, CAN])
async def test_ozet_defter_yolu_eski_yolla_ayni(student: str, client: httpx.AsyncClient,
                                                world: World) -> None:
    from_ledger = await client.get(f"/students/{student}/canteen", params={"days": 30})
    world.replace("canteen.api", PreLedgerCanteen())
    legacy = await client.get(f"/students/{student}/canteen", params={"days": 30})
    assert from_ledger.status_code == legacy.status_code == 200
    assert from_ledger.json() == legacy.json()


async def test_ozet_eski_kantinde_ayni(client: httpx.AsyncClient, world: World) -> None:
    from_ledger = (await client.get(f"/students/{ELIF}/canteen")).json()
    world.canteen.ledger_mode = "unsupported"
    assert (await client.get(f"/students/{ELIF}/canteen")).json() == from_ledger


async def test_ozet_defterden_alanlar(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"/students/{ELIF}/canteen", params={"days": 30})
    body = response.json()
    assert [item["id"] for item in body["transactions"]] == ["8f0c-1", "8f0c-2"]
    assert body["transactions"][1]["reversed"] is True      # iptal ayrı satır olmaz
    assert body["collections"] == [
        {"at": "2026-10-05T10:00:00+03:00", "amount": 5000, "method": "PAYMENT"},
        {"at": "2026-10-01T10:00:00+03:00", "amount": 10000, "method": "CASH"},
    ]
    assert body["pendingPaymentRequest"]["ref"] == "PR-01JPENDING"
    assert "483920" not in response.text and MOTHER not in response.text
    # Aralık defterin `from`una gider (now − 30 gün), `to` gönderilmez.
    args = world.canteen.calls[0][1]
    assert args["from_ms"] == 1791786600000 - 30 * 86_400_000 and args["to_ms"] is None


async def test_ozet_kisa_aralik_eski_satirlari_almaz(client: httpx.AsyncClient,
                                                     world: World) -> None:
    body = (await client.get(f"/students/{ELIF}/canteen", params={"days": 3})).json()
    assert [item["id"] for item in body["transactions"]] == ["8f0c-1", "8f0c-2"]
    assert body["collections"] == []


async def test_ozet_hatalari(client: httpx.AsyncClient, world: World) -> None:
    error_of(await client.get("/students/STU-yok/canteen"), 404, "student_not_found")
    world.canteen.down = True
    error_of(await client.get(f"/students/{ELIF}/canteen"), 503, "canteen_unavailable")


async def test_ozet_ogrenciyi_vermeyen_kantinde_ayrica_okur(client: httpx.AsyncClient,
                                                            world: World) -> None:
    original = world.canteen.ledger

    async def without_student(student_id: str, **kwargs: Any) -> dict[str, Any]:
        page = await original(student_id, **kwargs)
        page.pop("student", None)
        return page

    world.canteen.ledger = without_student
    body = (await client.get(f"/students/{ELIF}/canteen")).json()
    assert body["balance"] == 4250 and world.canteen.count("get_student") == 1


# ------------------------------------------------------- imleçli işlemler


async def test_islemler_sayfa_sayfa(client: httpx.AsyncClient, world: World) -> None:
    first = await client.get(f"/students/{ELIF}/canteen/transactions", params={"limit": 1})
    assert first.status_code == 200
    body = first.json()
    assert body["items"] == [{
        "id": "8f0c-1", "at": "2026-10-11T15:41:00+03:00", "total": 3500, "reversed": False,
        "items": [{"productId": "3", "name": "Tost", "qty": 1, "unitPrice": 2500,
                   "lineTotal": 2500},
                  {"productId": "9", "name": "Ayran", "qty": 2, "unitPrice": 500,
                   "lineTotal": 1000}],
    }]
    assert body["hasMore"] is True and body["nextCursor"]

    second = (await client.get(f"/students/{ELIF}/canteen/transactions",
                               params={"limit": 1, "cursor": body["nextCursor"]})).json()
    assert [item["id"] for item in second["items"]] == ["8f0c-2"]
    assert second["items"][0]["reversed"] is True
    assert (second["nextCursor"], second["hasMore"]) == (None, False)


async def test_islemler_kantin_sayfalarini_asar_tekrar_ve_kayip_yok(
        client: httpx.AsyncClient, world: World) -> None:
    sales(world, 300)
    items, pages = await walk(client, f"/students/{ELIF}/canteen/transactions", limit=100)
    assert [item["id"] for item in items] == [f"tx-{n}" for n in range(300)]
    assert pages == 3
    # Geçidin imleci kantinin imlecini OLDUĞU GİBİ taşır (üretmez).
    sent = [args["cursor"] for name, args in world.canteen.calls if name == "ledger"]
    assert all(cursor is None or cursor.startswith("c") for cursor in sent)
    assert any(cursor is not None for cursor in sent)


async def test_islemler_sayfalar_arasinda_gelen_yeni_satis_kaydirmaz(
        client: httpx.AsyncClient, world: World) -> None:
    sales(world, 5)
    path = f"/students/{ELIF}/canteen/transactions"
    first = (await client.get(path, params={"limit": 2})).json()
    newest = {**world.canteen.ledger_rows[0], "id": 60_000,
              "sale": {**world.canteen.ledger_rows[0]["sale"], "serverId": "tx-new"}}
    world.canteen.ledger_rows.insert(0, newest)
    rest, _ = await walk(client, path, limit=2, cursor=first["nextCursor"])
    seen = [item["id"] for item in first["items"] + rest]
    assert seen == ["tx-0", "tx-1", "tx-2", "tx-3", "tx-4"]


async def test_islemler_tarih_araligi(client: httpx.AsyncClient, world: World) -> None:
    response = await client.get(f"/students/{ELIF}/canteen/transactions",
                                params={"from": "2026-10-11", "to": "2026-10-11"})
    assert [item["id"] for item in response.json()["items"]] == ["8f0c-1"]
    args = world.canteen.calls[0][1]
    # Europe/Istanbul gün sınırları: 11 Ekim 00:00 (+03) dahil, 12 Ekim 00:00 hariç.
    assert (args["from_ms"], args["to_ms"]) == (1791666000000, 1791752400000)


async def test_islemler_dogrulama(client: httpx.AsyncClient, world: World) -> None:
    path = f"/students/{ELIF}/canteen/transactions"
    for params in ({"limit": 0}, {"limit": 101}, {"from": "dün"}):
        error_of(await client.get(path, params=params), 422, "validation_error")
    error_of(await client.get(path, params={"from": "2026-10-12", "to": "2026-10-11"}),
             422, "validation_error")
    for bad in ("bozuk", base64.urlsafe_b64encode(b'{"m":"x"}').decode(),
                base64.urlsafe_b64encode(b'{"m":"l","a":"9"}').decode()):
        error_of(await client.get(path, params={"cursor": bad}), 422, "validation_error")
    assert world.canteen.count("ledger") == 0           # kantine bozuk istek gitmez


async def test_islemler_hatalari(client: httpx.AsyncClient, world: World) -> None:
    error_of(await client.get("/students/STU-yok/canteen/transactions"), 404,
             "student_not_found")
    world.canteen.down = True
    error_of(await client.get(f"/students/{ELIF}/canteen/transactions"), 503,
             "canteen_unavailable")


# ------------------------------------------------------ imleçli tahsilatlar


async def test_tahsilatlar_sayfa_sayfa(client: httpx.AsyncClient, world: World) -> None:
    items, pages = await walk(client, f"/students/{ELIF}/canteen/collections", limit=1)
    assert items == [
        {"id": "902", "at": "2026-10-05T10:00:00+03:00", "amount": 5000, "method": "PAYMENT",
         "ref": "PR-01JPAID"},
        {"id": "901", "at": "2026-10-01T10:00:00+03:00", "amount": 10000, "method": "CASH",
         "ref": "col-1"},
    ]
    assert pages == 2


async def test_tahsilatlar_seyrekse_sayfa_kisa_kalir_imlec_gecerli(
        client: httpx.AsyncClient, world: World) -> None:
    sales(world, 1200)                                    # tahsilat yok, satış çok
    world.canteen.ledger_rows.append({**LEDGER[-1], "id": 1})
    path = f"/students/{ELIF}/canteen/collections"
    first = (await client.get(path, params={"limit": 5})).json()
    assert first["items"] == [] and first["nextCursor"]   # 5 kantin turunda bulunamadı
    assert world.canteen.count("ledger") == 5
    items, _ = await walk(client, path, limit=5, cursor=first["nextCursor"])
    assert [item["id"] for item in items] == ["1"]


# ------------------------------------------------------------- eski yol


async def test_imlecli_uclar_eski_kantinde_sira_numarasiyla(client: httpx.AsyncClient,
                                                            world: World) -> None:
    world.replace("canteen.api", PreLedgerCanteen())
    items, pages = await walk(client, f"/students/{ELIF}/canteen/transactions", limit=1)
    assert [item["id"] for item in items] == ["8f0c-1", "8f0c-2"] and pages == 2
    assert items[0]["items"][0]["productId"] == "3"
    collections, _ = await walk(client, f"/students/{ELIF}/canteen/collections", limit=5)
    assert [(item["amount"], item["method"]) for item in collections] == \
        [(5000, "PAYMENT"), (10000, "CASH")]


async def test_kip_degisince_imlec_bastan_istenir(client: httpx.AsyncClient,
                                                  world: World) -> None:
    path = f"/students/{ELIF}/canteen/transactions"
    ledger_cursor = (await client.get(path, params={"limit": 1})).json()["nextCursor"]
    world.canteen.ledger_mode = "unsupported"
    error_of(await client.get(path, params={"cursor": ledger_cursor}), 422, "validation_error")
    offset_cursor = (await client.get(path, params={"limit": 1})).json()["nextCursor"]
    assert json.loads(base64.urlsafe_b64decode(offset_cursor + "=="))["m"] == "o"
    world.canteen.ledger_mode = "ok"
    error_of(await client.get(path, params={"cursor": offset_cursor}), 422, "validation_error")
