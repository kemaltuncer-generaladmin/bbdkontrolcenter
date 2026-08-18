"""Ana Ekran Görselleri servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

from typing import Any

from store_home_media_backend import slots
from store_home_media_backend.service import HomeMediaService
from store_home_media_fakes import FakeApi, FakeApiError, FakeLog, FakeStore, png_data_url

GEREKCE = "Eylül kampanyası için afiş değişikliği"

#: Mağaza ucunun GERÇEK yanıt biçimi: camelCase ve `storage/` önekli yol.
SLAYTLAR = [
    {"index": 0, "title": "TYT Kaynakları", "link": "/tyt",
     "image": "storage/theme/1/sliders/tyt.webp",
     "imageUrl": "https://bbdstore.com.tr/storage/theme/1/sliders/tyt.webp"},
    {"index": 1, "title": "AYT Matematik", "link": "/ayt",
     "image": "storage/theme/1/sliders/ayt.webp",
     "imageUrl": "https://bbdstore.com.tr/storage/theme/1/sliders/ayt.webp"},
    {"index": 2, "title": "LGS Setleri", "link": "",
     "image": "storage/theme/1/sliders/lgs.webp",
     "imageUrl": "https://bbdstore.com.tr/storage/theme/1/sliders/lgs.webp"},
]


def _service(api: FakeApi | None = None, store: FakeStore | None = None,
             **config: Any) -> tuple[HomeMediaService, FakeApi, FakeStore]:
    api = api or FakeApi([dict(item) for item in SLAYTLAR])
    store = store or FakeStore()
    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    service = HomeMediaService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "recommended_slider": "1920x640",
                "max_image_bytes": 2_000_000, **config},
        publish=publish,
    )
    service.events = events  # type: ignore[attr-defined]
    return service, api, store


def _yazilabilir(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ekranın geri göndereceği gövde — satırdan üç alan."""
    return [{"title": row["title"], "link": row["link"], "image": row["image"]} for row in rows]


# ======================================================= K7 — ayakta kalma

async def test_magaza_dusunce_ekran_ayakta_kalir() -> None:
    api = FakeApi([])
    api.fail.add("bbd_home_slides")
    service, _, _ = _service(api)
    result = await service.slides()

    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []
    assert "patladı" in result["error"]
    # Ekranın ölçü/tavan bilgisi mağaza kapalıyken de gelir: dosya seçme
    # kutusu bağlantı düzelmeden de doğru sınırı gösterebilsin.
    assert result["recommended"] == "1920x640"


async def test_denetim_izi_okunamazsa_ekran_dusmez() -> None:
    class KorStore(FakeStore):
        async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
            raise RuntimeError("depo kilitli")

    service, _, _ = _service(store=KorStore())
    result = await service.audit()
    assert result["ok"] is True
    assert result["items"] == []
    assert "depo kilitli" in result["error"]


# ==================================================================== okuma

async def test_slaytlar_sirali_gelir_ve_camel_case_yanit_okunur() -> None:
    service, api, _ = _service()
    result = await service.slides()

    assert result["connected"] is True
    assert [row["title"] for row in result["items"]] == [
        "TYT Kaynakları", "AYT Matematik", "LGS Setleri"]
    # Mağaza yolu ile tarayıcının açacağı adres AYRI: liste yazarken yol gider.
    assert result["items"][0]["image"] == "storage/theme/1/sliders/tyt.webp"
    assert result["items"][0]["imageUrl"].startswith("https://")
    assert len(api.used("bbd_home_slides")) == 1   # liste TEK istekte gelir


async def test_kanal_ve_dil_her_istekte_gider() -> None:
    service, api, _ = _service()
    await service.slides()
    filtreler = [args[0] for name, args, _ in api.calls if name == "bbd_home_slides"]
    assert filtreler[0] == {"channel": "default", "locale": "tr"}


async def test_adresi_olmayan_slayt_eksik_olarak_isaretlenir() -> None:
    service, _, _ = _service()
    result = await service.slides()
    assert result["items"][2]["issues"] == ["tıklayınca gideceği yer yok"]
    assert result["issues"] == 1


# ============================================================ görsel denetimi

async def test_gorsel_olcusu_sunucuda_olculur() -> None:
    service, _, _ = _service()
    result = service.check_image(data=png_data_url(1200, 400))
    assert result["ok"] is True
    assert result["sizeState"] == slots.SIZE_BLURRY
    assert result["needsConfirm"] is True
    assert result["previewBox"]["width"] > 0


async def test_uygun_olcude_gorsel_onay_istemez() -> None:
    service, _, _ = _service()
    result = service.check_image(data=png_data_url(1920, 640))
    assert result["sizeState"] == slots.SIZE_OK
    assert result["needsConfirm"] is False


async def test_denetim_aga_hic_cikmaz() -> None:
    service, api, _ = _service()
    service.check_image(data=png_data_url(1920, 640))
    assert api.calls == []


# ================================================================== yükleme

async def test_onaysiz_uyarili_gorsel_aga_hic_cikmaz() -> None:
    """K9: panelde onay kutusu göstermek yetkilendirme değil."""
    service, api, _ = _service()
    result = await service.upload_image(data=png_data_url(600, 200), reason=GEREKCE,
                                        actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert result["needsConfirm"] is True
    assert api.used("upload_media") == []


async def test_onaylanan_uyarili_gorsel_yuklenir_ve_izi_kalir() -> None:
    service, api, store = _service()
    result = await service.upload_image(data=png_data_url(600, 200), acknowledged=True,
                                        reason=GEREKCE, actor="Kemal", dry_run=False)
    assert result["ok"] is True
    assert len(api.used("upload_media")) == 1
    # "Bu görsel neden bulanık" sorusunun cevabı yalnız burada durur.
    assert store.assets[0]["verdict"] == slots.SIZE_BLURRY


async def test_yukleme_magazanin_verdigi_yolu_dondurur() -> None:
    """Mağaza liste gövdesinde SERBEST YOL kabul etmiyor: yalnız kendi
    yüklediği klasördeki dosyayı yazıyor."""
    service, _, _ = _service()
    result = await service.upload_image(data=png_data_url(1920, 640), filename="Afiş.png",
                                        reason=GEREKCE, actor="Kemal", dry_run=False)
    assert result["image"] == "storage/theme/1/sliders/yeni.webp"
    assert result["url"].startswith("https://")


async def test_dosya_adi_ascii_ye_indirilerek_gonderilir() -> None:
    service, api, _ = _service()
    await service.upload_image(data=png_data_url(1920, 640), filename="Ekran Görüntüsü.jpg",
                               reason=GEREKCE, actor="Kemal", dry_run=False)
    assert api.used("upload_media")[0]["filename"] == "ekran-goruntusu.png"


async def test_gerekcesiz_yukleme_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.upload_image(data=png_data_url(1920, 640), reason="kısa",
                                        actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used("upload_media") == []


async def test_uc_yayinda_degilse_hata_degil_bekleme_denir() -> None:
    """Uç bir gün geri çekilirse ekran çökmez, durumu anlatır (K7)."""
    api = FakeApi([dict(item) for item in SLAYTLAR])
    api.raises["upload_media"] = FakeApiError("Uç henüz yayında değil.",
                                              code="bbd_endpoint_missing")
    service, _, _ = _service(api)
    result = await service.upload_image(data=png_data_url(1920, 640), reason=GEREKCE,
                                        actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert result["pending"] is True


# ==================================================================== yazma

async def test_liste_tam_olarak_yazilir_sira_dahil() -> None:
    service, api, _ = _service()
    okunan = (await service.slides())["items"]
    tersi = list(reversed(_yazilabilir(okunan)))

    result = await service.save_slides(slides=tersi, reason=GEREKCE, actor="Kemal",
                                       dry_run=False)
    assert result["ok"] is True
    assert result["count"] == 3
    yazilan = api.used("bbd_save_home_slides")[0]["slides"]
    assert [item["title"] for item in yazilan] == [
        "LGS Setleri", "AYT Matematik", "TYT Kaynakları"]


async def test_govdeye_yalniz_uc_alan_konur() -> None:
    service, api, _ = _service()
    okunan = (await service.slides())["items"]
    await service.save_slides(slides=okunan, reason=GEREKCE, actor="Kemal", dry_run=False)

    yazilan = api.used("bbd_save_home_slides")[0]["slides"][0]
    assert sorted(yazilan) == ["image", "link", "title"]


async def test_bos_liste_aga_hic_cikmaz() -> None:
    """Boş liste ana sayfanın en üstünü bomboş bırakır."""
    service, api, _ = _service()
    result = await service.save_slides(slides=[], reason=GEREKCE, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert "boş kaydedilemez" in result["error"]
    assert api.used("bbd_save_home_slides") == []


async def test_gorselsiz_satir_aga_hic_cikmaz() -> None:
    service, api, _ = _service()
    result = await service.save_slides(
        slides=[{"title": "TYT", "link": "/tyt", "image": ""}],
        reason=GEREKCE, actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_save_home_slides") == []


async def test_gerekcesiz_yazma_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.save_slides(slides=_yazilabilir((await service.slides())["items"]),
                                       reason="kısa", actor="Kemal", dry_run=False)
    assert result["ok"] is False
    assert api.used("bbd_save_home_slides") == []


async def test_yazma_gerekcesiyle_denetim_izine_gecer() -> None:
    """ADR 0012: gerekçe zorunlu ve yerelde kalır — Bagisto gerekçe tutmuyor."""
    service, _, store = _service()
    await service.save_slides(slides=_yazilabilir((await service.slides())["items"]),
                              reason=GEREKCE, actor="Kemal", dry_run=False)
    kayitlar = [row for row in store.audit if row["action"] == "save_slides"]
    assert [row["result"] for row in kayitlar] == ["denendi", "ok"]
    assert all(row["reason"] == GEREKCE for row in kayitlar)
    assert all(row["actor"] == "Kemal" for row in kayitlar)


async def test_magaza_reddederse_hata_ize_gecer_ve_ekrana_doner() -> None:
    api = FakeApi([dict(item) for item in SLAYTLAR])
    api.fail.add("bbd_save_home_slides")
    service, _, store = _service(api)
    result = await service.save_slides(
        slides=[{"title": "TYT", "link": "/tyt", "image": "storage/a.webp"}],
        reason=GEREKCE, actor="Kemal", dry_run=False)

    assert result["ok"] is False
    assert "patladı" in result["error"]
    assert store.audit[-1]["result"] == "hata"


async def test_kuru_provada_olay_yayilmaz() -> None:
    service, _, _ = _service()
    await service.save_slides(slides=[{"title": "TYT", "link": "", "image": "storage/a.webp"}],
                              reason=GEREKCE, actor="Kemal", dry_run=True)
    assert service.events == []                    # type: ignore[attr-defined]


async def test_gercek_yazmada_olay_yayilir() -> None:
    service, _, _ = _service()
    await service.save_slides(slides=[{"title": "TYT", "link": "", "image": "storage/a.webp"}],
                              reason=GEREKCE, actor="Kemal", dry_run=False)
    assert service.events[0][0] == "store_home_media.layout_changed"  # type: ignore[attr-defined]


# ============================================================= hedef seçici

async def test_kisa_sorguda_magazaya_hic_gidilmez() -> None:
    service, api, _ = _service()
    result = await service.link_search(q="a")
    assert result["items"] == []
    assert api.used("product_lookup") == []


async def test_urun_aramasi_adres_uretir() -> None:
    api = FakeApi([dict(item) for item in SLAYTLAR])
    api.lookup_items = [{"id": 7, "name": "TYT Deneme", "sku": "TYT-1", "urlKey": "tyt-deneme"}]
    service, _, _ = _service(api)
    result = await service.link_search(q="deneme")
    assert result["items"][0]["url"] == "/tyt-deneme"


async def test_urun_aramasi_dusse_de_ekran_ayakta_kalir() -> None:
    api = FakeApi([dict(item) for item in SLAYTLAR])
    api.fail.add("product_lookup")
    service, _, _ = _service(api)
    result = await service.link_search(q="deneme")
    assert result["ok"] is True
    assert result["connected"] is False
