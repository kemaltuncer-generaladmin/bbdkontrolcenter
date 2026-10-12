"""Mobil Geçit hızı — paralel yetenek çağrıları, süre sınırı, önbellekler, sorgu bütçesi.

Sözleşme v1 DEĞİŞMEZ (yalnız ek başlık: `Cache-Control`, zil haftasında
`ETag`/304). Bu dosya hızın gerilemesini yakalar: uç başına geçidin kendi
veritabanı ifadesi sayılır, yavaş yetenekle eşzamanlılık ve süre sınırı
ölçülür, önbelleğin ne zaman yeniden kurduğu sınanır.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from mobile_gateway_fakes import CAN, ELIF, FakeCanteen, FakeDirectory, bell_week
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


class FilteringCanteen(FakeCanteen):
    """Yeni kantin istemcisi: `payment_requests(student_id=)` tanır."""

    def __init__(self) -> None:
        super().__init__()
        self.filters: list[str | None] = []

    async def payment_requests(self, student_id: str | None = None  # type: ignore[override]
                               ) -> list[dict[str, Any]]:
        self.filters.append(student_id)
        return await super().payment_requests()


class SlowCanteen(FakeCanteen):
    """Her okuma `delay` sürer: sıralı mı aynı anda mı, süreden anlaşılır."""

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


async def test_kantin_ozeti_dort_okumayi_ayni_anda_yapar(
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


# ------------------------------------------------------------- dizin önbelleği


async def test_dizin_ayni_revizyonda_yeniden_kurulmaz(
        client: httpx.AsyncClient, world: World, service: GatewayService,
        monkeypatch: pytest.MonkeyPatch) -> None:
    directory = VersionedDirectory()
    world.replace("bbd_students.directory", directory)
    built = {"n": 0}
    original = service.student

    def counting(record: dict[str, Any]) -> dict[str, Any]:
        built["n"] += 1
        return original(record)

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

    def counting(record: dict[str, Any]) -> dict[str, Any]:
        built["n"] += 1
        return original(record)

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
    f"/students/{ELIF}/payment-requests",
    f"/students/{ELIF}/canteen/controls",
    "/canteen/payment-requests",
    "/canteen/parent-notifications",
    "/canteen/settings/public",
    "/canteen/products",
    "/tablets",
    f"/students/{ELIF}/tablet-usage",
    "/schedule/bell-week",
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
