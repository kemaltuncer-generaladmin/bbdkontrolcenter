"""Sayfalama — saf mantık, ağa çıkmaz."""

from __future__ import annotations

from typing import Any

from store_api_backend.paging import clamp_page_size, collect_all, envelope, page_params


def test_sayfa_boyu_sunucu_siniri_olan_elliye_kirpilir() -> None:
    # Sunucu (`MAX_PER_PAGE = 50`) fazlasını sessizce kırpıyor; istemci de
    # kırpmazsa "hepsini aldım" sanıp veri kaybeder.
    assert clamp_page_size(100) == 50
    assert clamp_page_size(30) == 30
    assert clamp_page_size(0) == 50
    assert clamp_page_size("abc") == 50
    assert clamp_page_size(-5) == 50


def test_zarf_acilir_ve_zarfsiz_yanit_da_kabul_edilir() -> None:
    items, meta = envelope({"data": [{"id": 1}], "meta": {"total": 1}})
    assert items == [{"id": 1}]
    assert meta["total"] == 1

    # `/catalog/categories/tree` gibi sayfalaması kapalı uçlar düz liste döner.
    items, meta = envelope([{"id": 4}])
    assert items == [{"id": 4}]
    assert meta == {}

    assert envelope(None) == ([], {})


def test_sayfa_parametreleri_bagistonun_beklediği_adlarla_uretilir() -> None:
    assert page_params(3, 200) == {"page": 3, "per_page": 50}
    assert page_params(0, 10) == {"page": 1, "per_page": 10}


async def test_tam_tarama_sayfalari_sirayla_toplar() -> None:
    cagrilar: list[int] = []

    async def fetch(page: int, per_page: int) -> Any:
        cagrilar.append(page)
        start = (page - 1) * per_page
        rows = [{"id": start + index} for index in range(min(per_page, 120 - start))]
        return {"data": rows, "meta": {"total": 120, "lastPage": 3, "currentPage": page}}

    sonuc = await collect_all(fetch, page_size=50)
    assert cagrilar == [1, 2, 3]           # SIRAYLA, paralel değil
    assert len(sonuc["items"]) == 120
    assert sonuc["total"] == 120
    assert sonuc["truncated"] is False


async def test_meta_yoksa_eksik_sayfa_son_sayfa_sayilir() -> None:
    async def fetch(page: int, per_page: int) -> Any:
        return {"data": [{"id": 1}] * (per_page if page == 1 else 7)}

    sonuc = await collect_all(fetch, page_size=10)
    assert len(sonuc["items"]) == 17
    assert sonuc["truncated"] is False


async def test_ust_sinira_dayanan_tarama_kirpildigini_soyler() -> None:
    async def fetch(page: int, per_page: int) -> Any:
        return {"data": [{"id": page}] * per_page,
                "meta": {"total": 10_000, "lastPage": 200}}

    sonuc = await collect_all(fetch, page_size=50, max_items=120)
    assert len(sonuc["items"]) == 120
    # Sessizce eksik liste vermek yerine kırpıldığını bildirir; ekran söyler.
    assert sonuc["truncated"] is True
