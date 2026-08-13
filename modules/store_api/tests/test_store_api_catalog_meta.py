"""Nitelik/aile, arama-SEO ve yapılandırma metotları — yol ve gövde denetimi.

Ağa çıkılmaz. Sorulan şey "uç doğru yere mi gidiyor, gövde doğru mu, önbellek
yazmadan sonra düşüyor mu".
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from store_api_backend.client import StoreApi
from store_api_backend.errors import StoreApiError
from store_api_fakes import FakeLog, FakeSecrets, FakeStore

TOKEN = "12|cokGizliBelirtec"
GEREKCE = "Katalog düzenlemesi 2026-08 planına göre yapıldı"


def gateway(handler: Any, **options: Any) -> tuple[StoreApi, FakeStore, FakeLog]:
    depo, gunluk = FakeStore(), FakeLog()
    ayar: dict[str, Any] = {"read_only": False, "dry_run_default": False}
    ayar.update(options)
    api = StoreApi(base_url="https://ornek.test", secrets=FakeSecrets({"store.admin_token": TOKEN}),
                   log=gunluk, store=depo, transport=httpx.MockTransport(handler), **ayar)
    api._sleep = _uyuma
    return api, depo, gunluk


async def _uyuma(_seconds: float) -> None:
    return None


def kaydeden() -> tuple[Any, list[httpx.Request]]:
    istekler: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request)
        return httpx.Response(200, json={"data": [{"id": 1}], "meta": {"total": 1, "lastPage": 1}})

    return handler, istekler


# --------------------------------------------------------------- nitelik

async def test_nitelik_yazma_uclari_dogru_yola_gider() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.create_attribute(payload={"code": "yayinevi"}, reason=GEREKCE)
    await api.update_attribute(39, payload={"admin_name": "Desi"}, reason=GEREKCE)
    await api.delete_attribute(39, reason=GEREKCE)
    await api.create_attribute_option(39, payload={"admin_name": "Ciltli"}, reason=GEREKCE)
    await api.update_attribute_option(39, 7, payload={"sort_order": 2}, reason=GEREKCE)
    await api.delete_attribute_option(39, 7, reason=GEREKCE)

    assert [(i.method, i.url.path) for i in istekler] == [
        ("POST", "/api/admin/catalog/attributes"),
        ("PUT", "/api/admin/catalog/attributes/39"),
        ("DELETE", "/api/admin/catalog/attributes/39"),
        ("POST", "/api/admin/catalog/attributes/39/options"),
        ("PUT", "/api/admin/catalog/attributes/39/options/7"),
        ("DELETE", "/api/admin/catalog/attributes/39/options/7"),
    ]


async def test_nitelik_yazmasi_referans_onbellegini_dusurur() -> None:
    istekler: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        istekler.append(request.method)
        return httpx.Response(200, json={"data": [{"id": 1}], "meta": {"total": 1, "lastPage": 1}})

    api, _, _ = gateway(handler)
    await api.attributes()
    await api.attributes()                     # önbellekten
    await api.create_attribute(payload={"code": "x"}, reason=GEREKCE)
    await api.attributes()                     # önbellek düştü, yeniden istenir

    assert istekler == ["GET", "POST", "GET"]


async def test_suzgecli_nitelik_listesi_onbellege_girmez() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.attributes({"type": "select"})
    await api.attributes({"type": "select"})

    # Önbellek anahtarı süzgeci taşımıyor; süzgeçli listeyi önbelleklemek
    # "renk araması" sonrası açılan ekranı eksik listeyle doldururdu.
    assert len(istekler) == 2
    assert istekler[0].url.params["type"] == "select"


async def test_nitelik_secenekleri_detay_ucundan_okunur() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/admin/catalog/attributes/39"
        return httpx.Response(200, json={"id": 39, "code": "cilt",
                                         "options": [{"id": 101, "adminName": "Ciltli"}]})

    api, _, _ = gateway(handler)
    sonuc = await api.attribute_options(39)

    # Ayrı liste ucu yok; dönüş yine {items, meta} ki ekran aynı kodu kullansın.
    assert sonuc["items"] == [{"id": 101, "adminName": "Ciltli"}]
    assert sonuc["meta"]["total"] == 1


async def test_dokuzyuzdokuz_cakismasi_anlasilir_hata_doner() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "Attribute is part of one or more families."})

    api, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.delete_attribute(39, reason=GEREKCE)

    assert hata.value.code == "conflict"
    assert "families" in hata.value.message


# ------------------------------------------------------------------ aile

async def test_aile_uclari_ve_eski_adin_ayni_yere_gitmesi() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.families()
    await api.family(2)
    await api.create_family(payload={"code": "dergi", "name": "Dergi"}, reason=GEREKCE)
    await api.update_family(2, payload={"name": "Kitap"}, reason=GEREKCE)
    await api.delete_family(2, reason=GEREKCE)
    api.forget_reference()
    await api.attribute_families()             # eski ad — anlık görüntü bunu çağırıyor

    assert [(i.method, i.url.path) for i in istekler] == [
        ("GET", "/api/admin/catalog/families"),
        ("GET", "/api/admin/catalog/families/2"),
        ("POST", "/api/admin/catalog/families"),
        ("PUT", "/api/admin/catalog/families/2"),
        ("DELETE", "/api/admin/catalog/families/2"),
        ("GET", "/api/admin/catalog/families"),
    ]


# ------------------------------------------------------------ arama · SEO

async def test_seo_uclari_dogru_yola_gider() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.create_url_rewrite(payload={"request_path": "eski"}, reason=GEREKCE)
    await api.update_url_rewrite(3, payload={"target_path": "yeni"}, reason=GEREKCE)
    await api.delete_url_rewrite(3, reason=GEREKCE)
    await api.update_search_term(19, payload={"term": "matematik"}, reason=GEREKCE)
    await api.delete_search_term(19, reason=GEREKCE)
    await api.create_search_synonym(payload={"name": "kalem", "terms": "kalem,tükenmez"},
                                    reason=GEREKCE)
    await api.update_search_synonym(4, payload={"terms": "kalem"}, reason=GEREKCE)
    await api.delete_search_synonym(4, reason=GEREKCE)
    await api.create_sitemap(payload={"file_name": "sitemap.xml", "path": "/"}, reason=GEREKCE)
    await api.update_sitemap(1, payload={"path": "/tr"}, reason=GEREKCE)
    await api.delete_sitemap(1, reason=GEREKCE)
    await api.generate_sitemap(1, reason=GEREKCE)

    assert [(i.method, i.url.path) for i in istekler] == [
        ("POST", "/api/admin/marketing/url-rewrites"),
        ("PUT", "/api/admin/marketing/url-rewrites/3"),
        ("DELETE", "/api/admin/marketing/url-rewrites/3"),
        ("PUT", "/api/admin/marketing/search-terms/19"),
        ("DELETE", "/api/admin/marketing/search-terms/19"),
        ("POST", "/api/admin/marketing/search-synonyms"),
        ("PUT", "/api/admin/marketing/search-synonyms/4"),
        ("DELETE", "/api/admin/marketing/search-synonyms/4"),
        ("POST", "/api/admin/marketing/sitemaps"),
        ("PUT", "/api/admin/marketing/sitemaps/1"),
        ("DELETE", "/api/admin/marketing/sitemaps/1"),
        ("POST", "/api/admin/marketing/sitemaps/1/generate"),
    ]


async def test_eski_save_url_rewrite_adi_calismaya_devam_eder() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.save_url_rewrite(payload={"request_path": "a"}, reason=GEREKCE)
    await api.save_url_rewrite(payload={"request_path": "a"}, rewrite_id=5, reason=GEREKCE)

    assert [(i.method, i.url.path) for i in istekler] == [
        ("POST", "/api/admin/marketing/url-rewrites"),
        ("PUT", "/api/admin/marketing/url-rewrites/5"),
    ]


async def test_site_haritasi_listesi_suzgec_ve_sayfa_alir() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.sitemaps({"file_name": "sitemap"}, page=2)

    assert istekler[0].url.params["file_name"] == "sitemap"
    assert istekler[0].url.params["page"] == "2"


# ----------------------------------------------------------- yapılandırma

async def test_slugsuz_yapilandirma_cagrisi_istek_gondermeden_reddedilir() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("istek gitmemeliydi")

    api, _, _ = gateway(handler)
    with pytest.raises(StoreApiError) as hata:
        await api.configuration("")

    # Sunucu 422 + çevrilmemiş 'slug-required' anahtarı döndürüyor; o metin
    # kullanıcıya hiçbir şey anlatmaz.
    assert hata.value.code == "payload"
    assert "configuration_slugs" in hata.value.message


async def test_yapilandirma_tek_elemanli_liste_zarfindan_acilir() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["slug"] == "sales.order_settings"
        return httpx.Response(200, json=[{
            "slug": "sales.order_settings", "channel": "default", "locale": "tr",
            "values": {"sales.order_settings.reorder.admin": "1"},
        }])

    api, _, _ = gateway(handler)
    sonuc = await api.configuration("sales.order_settings")

    # Uç sayfalama kapalı olduğu için düz liste veriyor; zarf açılmazsa ekran
    # "ayar bulunamadı" derdi.
    assert sonuc["values"]["sales.order_settings.reorder.admin"] == "1"


async def test_yapilandirma_yazmasi_slug_ve_degerleri_govdeye_koyar() -> None:
    govdeler: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        govdeler.append(json.loads(request.content))
        return httpx.Response(200, json={"success": True})

    api, _, _ = gateway(handler)
    await api.update_configuration("sales.order_settings",
                                   values={"sales.order_settings.reorder.shop": "0"},
                                   channel="default", locale="tr", reason=GEREKCE)

    assert govdeler[0]["slug"] == "sales.order_settings"
    assert govdeler[0]["values"] == {"sales.order_settings.reorder.shop": "0"}
    assert govdeler[0]["channel"] == "default"


async def test_ayar_agaci_suzgecsizken_onbellekten_gelir() -> None:
    handler, istekler = kaydeden()
    api, _, _ = gateway(handler)

    await api.configuration_menu()
    await api.configuration_menu()
    await api.configuration_menu(slug="sales.order_settings")

    assert len(istekler) == 2
    assert istekler[1].url.params["slug"] == "sales.order_settings"
