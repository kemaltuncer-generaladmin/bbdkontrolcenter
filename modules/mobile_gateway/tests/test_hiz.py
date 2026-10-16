"""Mobil Geçit hızı — paralel yetenek çağrıları, süre sınırı, önbellekler, sorgu bütçesi.

Sözleşme v1 DEĞİŞMEZ (yalnız ek başlık: `Cache-Control`, zil haftasında
`ETag`/304). Bu dosya hızın gerilemesini yakalar: uç başına geçidin kendi
veritabanı ifadesi sayılır, yavaş yetenekle eşzamanlılık ve süre sınırı
ölçülür, önbelleğin ne zaman yeniden kurduğu sınanır.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from mobile_gateway_fakes import (
    CAN,
    ELIF,
    FakeCanteen,
    FakeDirectory,
    FakeGroups,
    PreLedgerCanteen,
    bell_week,
    group_record,
)
from modules.mobile_gateway.backend.service import GatewayService

if TYPE_CHECKING:
    # Yalnız tip adı: tüm takım koşarken `conftest` adı başka modülün
    # conftest'ine çözülebilir; çalışma anında içe aktarılmaz.
    from conftest import CheckedStore, Clock, World


def error_of(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()["error"]
    assert body["code"] == code
    return body


class Counter:
    """Geçidin KENDİ deposuna giden ifadeleri sayar."""

    def __init__(self, store: CheckedStore, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        for name in ("fetch_all", "fetch_one", "execute"):
            original = getattr(store, name)
            monkeypatch.setattr(store, name, self._wrap(original))

    def _wrap(self, original: Any) -> Any:
        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.count += 1
            return await original(*args, **kwargs)

        return counted


class FilteringCanteen(PreLedgerCanteen):
    """Süzgeçli ama defter ucu olmayan kantin istemcisi: `payment_requests(student_id=)`."""

    def __init__(self) -> None:
        super().__init__()
        self.filters: list[str | None] = []

    async def payment_requests(self, student_id: str | None = None  # type: ignore[override]
                               ) -> list[dict[str, Any]]:
        self.filters.append(student_id)
        return await super().payment_requests()


class SlowCanteen(PreLedgerCanteen):
    """Her okuma `delay` sürer: sıralı mı aynı anda mı, süreden anlaşılır.

    Defter ucu yoktur — eski (dört okumalı) yolun eşzamanlılığı ölçülür.
    """

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay

    async def get_student(self, student_id: str) -> dict[str, Any]:
        await asyncio.sleep(self.delay)
        return await super().get_student(student_id)

    async def transactions(self, *, from_ms: int | None = None, to_ms: int | None = None,
                           limit: int | None = None) -> list[dict[str, Any]]:
        await asyncio.sleep(self.delay)
        return await super().transactions(from_ms=from_ms, to_ms=to_ms, limit=limit)

    async def collections(self, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        await asyncio.sleep(self.delay)
        return await super().collections(**kwargs)

    async def payment_requests(self) -> list[dict[str, Any]]:
        await asyncio.sleep(self.delay)
        return await super().payment_requests()


class VersionedDirectory(FakeDirectory):
    """Gerçek dizin yeteneği gibi `revision`, `asOf` ve `invalidate()` taşır."""

    def __init__(self) -> None:
        super().__init__()
        self.revision = 1
        self.forgotten = 0

    async def list(self, since: datetime | None = None) -> dict[str, Any]:
        result = await super().list(since)
        return {**result, "revision": self.revision, "asOf": "2026-10-12T06:29:40+00:00"}

    def invalidate(self) -> None:
        self.forgotten += 1


# ----------------------------------------------------- öğrenci süzgeci (kantin)


async def test_ogrenci_talepleri_kantine_ogrenci_suzgeciyle_gider(
        client: httpx.AsyncClient, world: World) -> None:
    canteen = FilteringCanteen()
    world.replace("canteen.api", canteen)
    response = await client.get(f"/students/{ELIF}/payment-requests")
    assert response.status_code == 200
    assert canteen.filters == [ELIF]
    # Kantin süzgeci yok saysa da (bu taklit hepsini döner) başkasınınki sızmaz.
    assert {item["ref"] for item in response.json()["items"]} == {"PR-01JPENDING", "PR-01JPAID"}

    summary = await client.get(f"/students/{CAN}/canteen")
    assert summary.status_code == 200 and canteen.filters[-1] == CAN


# ------------------------------------------------------------ eşzamanlılık


async def test_eski_kantinde_ozet_dort_okumayi_ayni_anda_yapar(
        client: httpx.AsyncClient, world: World) -> None:
    world.replace("canteen.api", SlowCanteen(delay=0.15))
    started = time.perf_counter()
    response = await client.get(f"/students/{ELIF}/canteen")
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert response.json()["balance"] == 4250
    # Sırayla 4 × 0,15 = 0,6 sn olurdu.
    assert elapsed < 0.45, elapsed


async def test_kantin_ozeti_hata_onceligi_sirali_koddaki_gibi(
        client: httpx.AsyncClient, world: World) -> None:
    error_of(await client.get("/students/STU-YOK/canteen"), 404, "student_not_found")
    world.canteen.down = True
    error_of(await client.get(f"/students/{ELIF}/canteen"), 503, "canteen_unavailable")


async def test_yavas_kantin_sure_sinirinda_kesilir(client: httpx.AsyncClient, world: World,
                                                   service: GatewayService) -> None:
    service.caps.read_timeout = 0.05
    world.replace("canteen.api", SlowCanteen(delay=2.0))
    started = time.perf_counter()
    error_of(await client.get(f"/students/{ELIF}/canteen/controls"), 503,
             "canteen_unavailable")
    assert time.perf_counter() - started < 1.0


class SlowLedgerCanteen(FakeCanteen):
    """Defter ucu `delay` sürer. `peak`: aynı anda uçuşta olan en çok defter isteği —
    eşzamanlılığı saat yerine sayıyla ölçer (yük altında oynamaz)."""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay
        self.in_flight = 0
        self.peak = 0

    async def ledger(self, student_id: str, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return await super().ledger(student_id, **kwargs)
        finally:
            self.in_flight -= 1


def canteen_calls(canteen: FakeCanteen) -> list[str]:
    return [name for name, _ in canteen.calls]


# ------------------------------------------------- kantin üst çağrı bütçesi


async def test_kantin_ozeti_tek_kantin_istegi(client: httpx.AsyncClient, world: World) -> None:
    """Defter ucu olan kantinde §3 özeti TEK kantin isteğidir (eskiden dört)."""
    response = await client.get(f"/students/{ELIF}/canteen", params={"days": 180})
    assert response.status_code == 200
    assert canteen_calls(world.canteen) == ["ledger"]
    assert world.canteen.calls[0][1]["limit"] == 200          # sayfa en büyük boyda
    assert world.canteen.calls[0][1]["to_ms"] is None          # kantin bakiyeyi ek sorgusuz verir


async def test_kantin_ozeti_tek_istegin_suresi_kadar(client: httpx.AsyncClient,
                                                     world: World) -> None:
    world.replace("canteen.api", SlowLedgerCanteen(delay=0.15))
    started = time.perf_counter()
    assert (await client.get(f"/students/{ELIF}/canteen")).status_code == 200
    assert time.perf_counter() - started < 0.3


def busy_student(canteen: FakeCanteen, count: int, *, span_ms: int) -> None:
    """ELIF'e `count` satış; deftere yazılma anları `span_ms` boyunca eşit aralıklı."""
    base = canteen.ledger_rows[1]
    now = 1791786600000
    canteen.ledger_rows = [
        {**base, "id": 10_000 - n, "at": now - 1 - n * span_ms // count,
         "sale": {**base["sale"], "serverId": f"tx-{n}",
                  "createdAt": now - 1 - n * span_ms // count}}
        for n in range(count)
    ]


async def test_kantin_ozeti_buyuk_aralikta_kalan_dilimleri_ayni_anda_okur(
        client: httpx.AsyncClient, world: World) -> None:
    """Kurum geneli 5000 satır kesintisi yok: öğrencinin 900 satışının hepsi gelir.

    İlk sayfa (200 satır) aralığın ~%22'sini kapsar; kalan ~700 satır dört
    zaman dilimine bölünür ve dilimler aynı anda okunur — süre sayfa sayısıyla
    değil dilimin kendi sayfa sayısıyla büyür.
    """
    slow = SlowLedgerCanteen(delay=0.05)
    world.replace("canteen.api", slow)
    busy_student(slow, 900, span_ms=29 * 86_400_000)
    response = await client.get(f"/students/{ELIF}/canteen", params={"days": 30})
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()["transactions"]]
    assert ids == [f"tx-{n}" for n in range(900)]          # tam, tekrar yok, sıralı
    calls = [args for name, args in slow.calls if name == "ledger"]
    assert len(calls) == 5                                   # 1 + 4 dilim
    assert [args["to_ms"] is None for args in calls].count(True) == 2  # ilk + imleçli dilim
    # Eskiden süre ölçülüyordu (< 3,5 × gecikme) ve makine yük altındayken oynuyordu.
    # Ölçülen asıl özellik eşzamanlılıktır: dört dilim AYNI ANDA uçuşta (sıralı olsa 1).
    assert slow.peak == 4


async def test_kantin_ozeti_ayni_anda_yazilmis_satirlar_kaybolmaz(
        client: httpx.AsyncClient, world: World) -> None:
    """Bütün satırlar aynı milisaniyede (dilim kestirimi en kötü durumda)."""
    base = world.canteen.ledger_rows[1]
    world.canteen.ledger_rows = [
        {**base, "id": 10_000 - n, "sale": {**base["sale"], "serverId": f"tx-{n}",
                                            "createdAt": base["at"] - n * 60_000}}
        for n in range(450)
    ]
    response = await client.get(f"/students/{ELIF}/canteen", params={"days": 30})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["transactions"]] == \
        [f"tx-{n}" for n in range(450)]


async def test_kantin_ozeti_kucuk_aralikta_tek_sayfa(client: httpx.AsyncClient,
                                                     world: World) -> None:
    busy_student(world.canteen, 200, span_ms=29 * 86_400_000)
    assert (await client.get(f"/students/{ELIF}/canteen")).status_code == 200
    assert canteen_calls(world.canteen) == ["ledger"]


async def test_eski_kantinde_bir_deneme_sonra_dort_okuma(client: httpx.AsyncClient,
                                                         world: World) -> None:
    world.canteen.ledger_mode = "unsupported"
    assert (await client.get(f"/students/{ELIF}/canteen")).status_code == 200
    assert sorted(canteen_calls(world.canteen)) == sorted(
        ["ledger", "get_student", "transactions", "collections", "payment_requests"])


async def test_defter_bilmeyen_istemcide_dogrudan_eski_yol(client: httpx.AsyncClient,
                                                           world: World) -> None:
    canteen = PreLedgerCanteen()
    world.replace("canteen.api", canteen)
    assert (await client.get(f"/students/{ELIF}/canteen")).status_code == 200
    assert sorted(canteen_calls(canteen)) == sorted(
        ["get_student", "transactions", "collections", "payment_requests"])


@pytest.mark.parametrize("path", ["transactions", "collections"])
async def test_imlecli_liste_tek_kantin_istegi(path: str, client: httpx.AsyncClient,
                                               world: World) -> None:
    response = await client.get(f"/students/{ELIF}/canteen/{path}", params={"limit": 20})
    assert response.status_code == 200
    assert canteen_calls(world.canteen) == ["ledger"]


# ------------------------------------------------------------- dizin önbelleği


async def test_dizin_ayni_revizyonda_yeniden_kurulmaz(
        client: httpx.AsyncClient, world: World, service: GatewayService,
        monkeypatch: pytest.MonkeyPatch) -> None:
    directory = VersionedDirectory()
    world.replace("bbd_students.directory", directory)
    built = {"n": 0}
    original = service.student

    def counting(record: dict[str, Any], **options: Any) -> dict[str, Any]:
        built["n"] += 1
        return original(record, **options)

    monkeypatch.setattr(service, "student", counting)

    first = await client.get("/directory")
    assert first.status_code == 200 and built["n"] == 3
    body = first.json()
    assert body["generatedAt"] == "2026-10-12T09:29:40+03:00"      # yeteneğin asOf'u
    assert first.headers["cache-control"] == "private, no-cache"

    second = await client.get("/directory")
    assert built["n"] == 3                                          # yeniden kurulmadı
    assert second.content == first.content
    same = await client.get("/directory", headers={"If-None-Match": first.headers["etag"]})
    assert same.status_code == 304 and built["n"] == 3

    # Veri değişince yetenek revizyonu değiştirir: yanıt hemen yeniden kurulur.
    directory.records[ELIF]["examTarget"] = "YKS"
    directory.revision = 2
    changed = await client.get("/directory", headers={"If-None-Match": first.headers["etag"]})
    assert changed.status_code == 200 and built["n"] == 6
    assert changed.headers["etag"] != first.headers["etag"]


async def test_dizin_since_istegi_tam_listeyi_bozmaz(
        client: httpx.AsyncClient, world: World, service: GatewayService,
        monkeypatch: pytest.MonkeyPatch) -> None:
    world.replace("bbd_students.directory", VersionedDirectory())
    built = {"n": 0}
    original = service.student

    def counting(record: dict[str, Any], **options: Any) -> dict[str, Any]:
        built["n"] += 1
        return original(record, **options)

    monkeypatch.setattr(service, "student", counting)
    full = await client.get("/directory")
    assert built["n"] == 3
    delta = await client.get("/directory", params={"since": "2026-10-12T09:00:00+03:00"})
    assert delta.json()["full"] is False and len(delta.json()["students"]) == 1
    assert built["n"] == 4                       # since yanıtı saklanmaz, her seferinde kurulur
    again = await client.get("/directory")
    assert again.content == full.content and again.json()["full"] is True
    assert built["n"] == 4                       # tam liste önbellekten itilmedi


async def test_revizyonsuz_eski_dizinde_her_istek_tazedir(
        client: httpx.AsyncClient, world: World) -> None:
    first = await client.get("/directory")
    world.directory.records[ELIF]["firstName"] = "Elifnur"
    second = await client.get("/directory")
    assert second.headers["etag"] != first.headers["etag"]


async def test_kantine_yazinca_dizin_onbellegi_birakilir(client: httpx.AsyncClient,
                                                         world: World) -> None:
    directory = VersionedDirectory()
    world.replace("bbd_students.directory", directory)
    patched = await client.patch(f"/students/{ELIF}/canteen/controls", json={"dailyLimit": 100})
    assert patched.status_code == 200 and directory.forgotten == 1
    collected = await client.post(f"/students/{ELIF}/canteen/collections",
                                  json={"amount": 100, "localId": "u-1"})
    assert collected.status_code == 201 and directory.forgotten == 2


async def test_qr_olmayan_ogrenciye_404_metin_hatasi_one_gecmez(
        client: httpx.AsyncClient, world: World) -> None:
    """Varlık sınaması ve QR metni aynı anda istenir; metin üretimi patlasa da
    olmayan öğrenci 404 alır (sıralı koddaki hata önceliği)."""
    def broken(student_id: str) -> str:
        raise RuntimeError("qr anahtarı yok")

    world.directory.card_qr_text = broken  # type: ignore[method-assign]
    error_of(await client.get("/students/STU-YOK/canteen/qr"), 404, "student_not_found")
    response = await client.get(f"/students/{ELIF}/canteen/qr")
    assert response.status_code in (502, 503), response.text


# ------------------------------------------------ zil haftası ve kantin ayarları


async def test_zil_haftasi_onbellek_etag_ve_olayla_birakma(
        client: httpx.AsyncClient, world: World, service: GatewayService, clock: Clock) -> None:
    calls = {"n": 0}

    async def week() -> dict[str, Any]:
        calls["n"] += 1
        return await bell_week()

    world.replace("bell.week", week)
    first = await client.get("/schedule/bell-week")
    assert first.status_code == 200 and calls["n"] == 1
    assert first.json()["available"] is True
    assert first.headers["cache-control"] == "private, max-age=60"
    assert (await client.get("/schedule/bell-week")).content == first.content
    assert calls["n"] == 1
    not_modified = await client.get("/schedule/bell-week",
                                    headers={"If-None-Match": first.headers["etag"]})
    assert not_modified.status_code == 304 and not_modified.content == b""

    service.forget_bell_week("bell.week_changed", {})      # olay veri yolundan gelir
    await client.get("/schedule/bell-week")
    assert calls["n"] == 2
    clock.advance(seconds=61)
    await client.get("/schedule/bell-week")
    assert calls["n"] == 3

    world.remove("bell.week")                               # önbellek kaldırılanı göstermez
    assert (await client.get("/schedule/bell-week")).json()["available"] is False


async def test_kantin_ayarlari_kisa_sure_onbellekte(client: httpx.AsyncClient, world: World,
                                                    clock: Clock) -> None:
    for _ in range(3):
        assert (await client.get("/canteen/settings/public")).status_code == 200
    assert world.canteen.count("canteen_settings_public") == 1
    clock.advance(seconds=59)
    await client.get("/canteen/settings/public")
    assert world.canteen.count("canteen_settings_public") == 1
    clock.advance(seconds=2)
    await client.get("/canteen/settings/public")
    assert world.canteen.count("canteen_settings_public") == 2


# --------------------------------------------------------------- sorgu bütçesi


READ_ENDPOINTS = [
    "/directory",
    f"/students/{ELIF}/canteen",
    f"/students/{ELIF}/canteen/qr",
    f"/students/{ELIF}/canteen/transactions",
    f"/students/{ELIF}/canteen/collections",
    f"/students/{ELIF}/payment-requests",
    f"/students/{ELIF}/canteen/controls",
    "/canteen/payment-requests",
    "/canteen/parent-notifications",
    "/canteen/settings/public",
    "/canteen/products",
    "/tablets",
    "/tablets/usage",
    f"/students/{ELIF}/tablet-usage",
    "/schedule/bell-week",
    "/groups",
    "/bell/plan/status",
    "/settings/public",
]


@pytest.mark.parametrize("path", READ_ENDPOINTS)
async def test_okuma_uclari_gecit_veritabanina_gitmez(
        path: str, client: httpx.AsyncClient, store: CheckedStore,
        monkeypatch: pytest.MonkeyPatch) -> None:
    """Okuma uçlarının geçidin kendi tablolarında işi yoktur (0 ifade).

    Kimlik doğrulamanın bütçesi çekirdekte bağlıdır
    (`tests/core/test_kimlik_hizi.py`: servis anahtarı sıcakken 1 sorgu).
    """
    counter = Counter(store, monkeypatch)
    response = await client.get(path)
    assert response.status_code == 200, response.text
    assert counter.count == 0


async def test_yazma_ucunun_sorgu_butcesi(client: httpx.AsyncClient, store: CheckedStore,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Anahtarsız yazma 1 ifade (denetim izi); anahtarlıda en çok 5.

    İlk istek süresi dolan satırları temizler (on dakikada bir, 4 ifade);
    bütçe ondan sonraki isteklerde ölçülür.
    """
    await client.patch(f"/students/{ELIF}/canteen/controls", json={"dailyLimit": 50})
    counter = Counter(store, monkeypatch)
    await client.patch(f"/students/{ELIF}/canteen/controls", json={"dailyLimit": 100})
    assert counter.count == 1                       # denetim izi
    counter.count = 0
    await client.patch(f"/students/{ELIF}/canteen/controls", json={"dailyLimit": 200},
                       headers={"Idempotency-Key": "k-1"})
    assert counter.count <= 5                       # oku, ayır, oku, bitir, iz


# ------------------------------------------- öğrenci grupları ve zil planı (§5)


def many_groups(groups: FakeGroups, *, count: int = 200, students: int = 2000) -> None:
    """200 grup, 4.000 üyelik satırı (2.000 öğrenci × 2 grup) — sözleşme §5 ölçeği."""
    groups.records = []
    for index in range(count):
        members = [{"studentId": f"STU-{(index * 20 + offset) % students:010d}",
                    "validFrom": "2026-09-15", "validTo": None if offset % 3 else "2026-10-09"}
                   for offset in range(20)]
        groups.records.append(group_record(f"g{index:03d}", f"Grup {index}",
                                           revision=index + 1, members=members))
    groups.current = count


async def test_gruplar_ayni_revizyonda_yeniden_kurulmaz(
        client: httpx.AsyncClient, world: World, service: GatewayService) -> None:
    many_groups(world.groups)
    first = await client.get("/groups")
    assert first.status_code == 200 and len(first.json()["groups"]) == 200
    # §5 tahmini "< 300 KB"; ölçülen ≈ 330 KB ham JSON. Çekirdeğin GZip ara
    # katmanı (`Accept-Encoding: gzip`) bunu ~30 KB'a indirir; çoğu istek zaten 304.
    assert len(first.content) < 360_000, len(first.content)
    assert len(gzip.compress(first.content)) < 60_000

    for _ in range(10):
        again = await client.get("/groups")
    assert again.content == first.content
    assert world.groups.lists == [None]                    # yalnız revizyon okundu
    assert world.groups.revisions == 11

    world.groups.current += 1                              # başka süreç yazdı
    await client.get("/groups")
    assert world.groups.lists == [None, None]
    service.forget_groups("bbd_students.groups_changed", {"revision": 202})
    await client.get("/groups")
    assert world.groups.lists == [None, None, None]

    # Geçidin sunucu payı (HTTP taşıması ve istemcinin 330 KB okuması hariç):
    # soğuk ≤ 150 ms, önbellekten ≤ 30 ms (§5). Taklit yetenek anında döner.
    service.forget_groups()
    started = time.perf_counter()
    await service.groups(None, None)
    cold = time.perf_counter() - started
    started = time.perf_counter()
    for _ in range(10):
        await service.groups(None, None)
    warm = (time.perf_counter() - started) / 10
    assert cold < 0.15 and warm < 0.03, (cold, warm)


async def test_gruplar_since_istegi_tam_listeyi_bozmaz(client: httpx.AsyncClient,
                                                       world: World) -> None:
    full = await client.get("/groups")
    delta = await client.get("/groups", params={"since": 4181})
    assert delta.json()["full"] is False
    again = await client.get("/groups")
    assert again.content == full.content and world.groups.lists == [None, 4181]


async def test_revizyonsuz_grup_yeteneginde_sure_sinirlar(
        client: httpx.AsyncClient, world: World, clock: Clock) -> None:
    world.replace("bbd_students.groups", FakeGroups(with_revision=False))
    groups = world.registry.try_resolve("bbd_students.groups")
    await client.get("/groups")
    await client.get("/groups")
    assert groups.lists == [None]
    clock.advance(seconds=61)
    await client.get("/groups")
    assert groups.lists == [None, None]


def busy_plan(days: int = 14, calls_per_day: int = 61) -> dict[str, Any]:
    """14 gün × ~61 çağrı ≈ 850 satır (§5 `PUT /bell/plan` ölçeği)."""
    out = []
    for offset in range(days):
        day = f"2026-10-{12 + offset:02d}"
        calls = [{"ref": f"{day}-{index}", "at": f"{9 + index // 8:02d}:{(index % 8) * 5:02d}",
                  "groupKind": "group", "groupName": f"Grup {index}",
                  "groupRef": f"km_group:g{index:03d}", "teacherName": "Zeynep hoca",
                  "room": "B-2", "subject": "Fizik", "kind": "lesson"}
                 for index in range(calls_per_day)]
        out.append({"date": day, "mode": "normal", "profileId": None, "reason": None,
                    "revision": 900 + offset, "planHash": f"h{offset}", "calls": calls})
    return {"from": out[0]["date"], "to": out[-1]["date"], "days": out}


async def test_zil_plani_850_cagri_gecit_payi(client: httpx.AsyncClient, world: World,
                                              store: CheckedStore,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    """Geçidin payı (gövde okuma, idempotensi, iz) 850 çağrıda ≤ 300 ms bütçenin altında;
    asıl yazma zil modülünde ölçülür (`modules/bell/tests`)."""
    body = busy_plan()
    assert sum(len(day["calls"]) for day in body["days"]) >= 850
    await client.put("/bell/plan", json=busy_plan(1, 1))   # temizlik turu bütçe dışı
    counter = Counter(store, monkeypatch)
    started = time.perf_counter()
    response = await client.put("/bell/plan", json=body)
    elapsed = time.perf_counter() - started
    assert response.status_code == 200, response.text
    assert counter.count == 1                               # yalnız denetim izi
    assert elapsed < 0.3, elapsed
    assert {day["result"] for day in response.json()["days"]} <= {"applied", "unchanged"}


async def test_kurum_geneli_tablet_kullanimi_2000_ogrenci(client: httpx.AsyncClient,
                                                          world: World, store: CheckedStore,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """`GET /tablets/usage`: 2.000 öğrenci satırında geçidin payı ≤ 80 ms, gövde < 150 KB,
    geçidin kendi deposuna 0 ifade. Asıl okuma tablet modülünde iki indeksli sorgudur
    (`modules/bbd_tablet/tests/test_capabilities.py`)."""
    world.tablet_read.items = [
        {"studentId": f"STU-{index:010d}", "totalSeconds": index * 7,
         "blockedUntil": "2026-10-20T15:00:00Z" if index % 50 == 0 else None}
        for index in range(2000)
    ]
    await client.get("/tablets/usage")                       # ısınma turu bütçe dışı
    counter = Counter(store, monkeypatch)
    started = time.perf_counter()
    response = await client.get("/tablets/usage", params={"date": "2026-10-11"})
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    assert len(response.json()["items"]) == 2000
    assert len(response.content) < 150_000
    assert counter.count == 0
    assert elapsed < 0.08, elapsed


# ============================================ merkezi senkron uçları (§9 bütçeleri)


class Okul:
    """2.000 öğrencilik sahte kantin (öğrenci dizininin tükettiği iki metot)."""

    def __init__(self) -> None:
        self.rows = [{"id": f"STU-{index:010d}", "displayName": f"Öğrenci {index} Soyad",
                      "parentPhone": f"532{index:07d}", "balance": index * 5,
                      "spendingLimit": None, "isBlocked": False,
                      "updatedAt": 1791786600000} for index in range(2000)]
        self.by_id = {row["id"]: row for row in self.rows}

    async def students(self) -> list[dict[str, Any]]:
        await asyncio.sleep(0.005)  # kantin listesinin ağ payı
        return [dict(row) for row in self.rows]

    async def student(self, opaque_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.003)
        return dict(self.by_id[opaque_id])


async def gercek_dizin(store: CheckedStore, world: World) -> Any:
    """Öğrenci Yönetimi'nin GERÇEK dizin yeteneği (aynı depoda kendi göçleriyle)."""
    from pathlib import Path

    from modules.bbd_students.backend.directory import StudentDirectory

    from km_core.contracts.module import ScopedStore

    migrations = Path(__file__).resolve().parents[2] / "bbd_students" / "backend" / "migrations"
    for path in sorted(migrations.glob("*.sql")):
        await store.database.apply_migration("bbd_students", path.name,
                                             path.read_text(encoding="utf-8"))

    class Log:
        def warning(self, *_a: Any, **_k: Any) -> None:
            pass

        info = error = warning

    directory = StudentDirectory(canteen=Okul(), store=ScopedStore(store.database,
                                                                   "bbd_students"), log=Log())
    world.replace("bbd_students.directory", directory)
    return directory


def p95(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, int(len(ordered) * 0.95) - 1)]


async def test_tekil_ogrenci_ve_toplu_okuma_butceleri(client: httpx.AsyncClient,
                                                      world: World,
                                                      store: CheckedStore) -> None:
    """§9: `GET /students/{id}` ≤ 30 ms önbellekten, ≤ 80 ms soğuk; `ids` (100) ≤ 80 ms;
    `since&balance=false` değişiklik yokken ≤ 30 ms ve < 1 KB. 2.000 öğrenci, gerçek dizin."""
    directory = await gercek_dizin(store, world)
    warm = await client.get("/directory?balance=false")       # ısınma (bütçe dışı)
    assert warm.status_code == 200 and len(warm.json()["students"]) == 2000

    cached: list[float] = []
    for index in range(40):
        started = time.perf_counter()
        response = await client.get(f"/students/STU-{index * 37:010d}")
        cached.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200
    cold: list[float] = []
    for index in range(10):
        directory.invalidate()
        started = time.perf_counter()
        response = await client.get(f"/students/STU-{index * 101:010d}")
        cold.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200
    await client.get("/directory?balance=false")
    ids = ",".join(f"STU-{index * 19:010d}" for index in range(100))
    many: list[float] = []
    for _ in range(10):
        started = time.perf_counter()
        response = await client.get(f"/directory?ids={ids}")
        many.append((time.perf_counter() - started) * 1000)
        assert len(response.json()["students"]) == 100
    as_of = warm.json()["generatedAt"]
    quiet: list[float] = []
    for _ in range(20):
        started = time.perf_counter()
        response = await client.get("/directory", params={"since": as_of, "balance": "false"})
        quiet.append((time.perf_counter() - started) * 1000)
    assert response.json()["students"] == [] and response.json()["removed"] == []
    size = len(response.content)
    print(f"\n[§9] tekil önbellek p95 {p95(cached):.1f} ms · soğuk p95 {p95(cold):.1f} ms · "
          f"ids(100) p95 {p95(many):.1f} ms · since&balance=false p95 {p95(quiet):.1f} ms, "
          f"{size} B")
    assert p95(cached) <= 30 and p95(cold) <= 80
    assert p95(many) <= 80
    assert p95(quiet) <= 30 and size < 1024
