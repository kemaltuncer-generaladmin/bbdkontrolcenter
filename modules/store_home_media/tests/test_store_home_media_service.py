"""Ana Ekran Görselleri servisi — iş kuralları. Ağa çıkmaz; `store.api` taklit edilir."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from store_home_media_backend import slots
from store_home_media_backend.service import HomeMediaService
from store_home_media_fakes import FakeApi, FakeApiError, FakeLog, FakeStore, png_data_url

GEREKCE = "Eylül kampanyası için afiş değişikliği"

SLOTLAR = [
    {"id": 1, "area": "slider", "title": "Okula dönüş", "alt": "Okula dönüş afişi",
     "link": "/okula-donus", "status": 1, "sort_order": 2,
     "image_url": "https://bbdstore.com.tr/1.jpg", "image_width": 1920, "image_height": 640},
    {"id": 2, "area": "slider", "title": "Yaz indirimi", "alt": "Yaz afişi", "link": "/yaz",
     "status": 1, "sort_order": 1, "image_url": "https://bbdstore.com.tr/2.jpg",
     "image_width": 1200, "image_height": 400},
    {"id": 3, "area": "banner", "title": "Deneme kulübü", "alt": "Deneme kulübü afişi",
     "link": "/deneme", "status": 0, "sort_order": 1},
]


def _service(api: FakeApi | None = None, store: FakeStore | None = None, printer: Any = None,
             **config: Any) -> tuple[HomeMediaService, FakeApi, FakeStore]:
    api = api or FakeApi([dict(item) for item in SLOTLAR])
    store = store or FakeStore()
    events: list[tuple[str, dict[str, Any]]] = []

    async def publish(name: str, payload: dict[str, Any]) -> None:
        events.append((name, payload))

    service = HomeMediaService(
        api=api, store=store, log=FakeLog(),
        config={"channel": "default", "locale": "tr", "recommended_slider": "1920x640",
                "recommended_banner": "1200x400", "max_image_bytes": 2_000_000, **config},
        printer=printer, publish=publish, fallback_dir=Path("/tmp/km-test-raporlar"),
    )
    service.events = events  # type: ignore[attr-defined]
    return service, api, store


# ======================================================= K7 — ayakta kalma

async def test_bbd_ucu_yoksa_ekran_tema_kayitlarindan_salt_okunur_doldurulur() -> None:
    # Uç henüz yayında değil. Boş bir ekran "ana sayfada hiçbir şey yok" gibi
    # okunur ve yanlıştır; ne olduğunu gösterip yazmayı kapatmak doğrudur.
    api = FakeApi([])
    api.fail.add("bbd_carousel")
    api.theme_items = [{"id": 7, "type": "image_carousel", "name": "Ana slider",
                        "sort_order": 1, "status": 1}]
    service, _, _ = _service(api)

    result = await service.overview()
    assert result["ok"] is True
    assert result["source"] == "themes"
    assert result["readOnly"] is True
    assert result["items"][0]["title"] == "Ana slider"
    assert "patladı" in result["error"]


async def test_ikisi_de_okunamazsa_ekran_bos_ama_ayakta_kalir() -> None:
    api = FakeApi([])
    api.fail.update({"bbd_carousel", "themes"})
    service, _, _ = _service(api)

    result = await service.overview()
    assert result["ok"] is True
    assert result["connected"] is False
    assert result["items"] == []
    assert result["error"]


async def test_referans_parcasi_patlarsa_gerisi_yine_dolar() -> None:
    api = FakeApi([])
    api.fail.add("category_tree")
    service, _, _ = _service(api)

    result = await service.reference()
    assert result["ok"] is True
    assert result["categories"] == []
    assert result["pages"][0]["title"] == "Mesafeli Satış"
    assert any("kategoriler" in item for item in result["warnings"])


# ================================================================ okuma

async def test_onizleme_suzgecten_etkilenmez_liste_etkilenir() -> None:
    # Filtre koyunca vitrin boşalmış gibi görünmemeli: önizleme ana sayfanın
    # temsilidir, listenin değil.
    service, _, _ = _service()
    result = await service.overview(q="yaz")
    assert [row["id"] for row in result["items"]] == [2]
    assert len(result["preview"]["slider"]) == 2
    assert result["counts"]["banner"] == 1


async def test_kanal_ve_dil_her_istekte_gider() -> None:
    service, api, _ = _service()
    await service.overview()
    filters = api.calls[0][1][0]
    assert filters["channel"] == "default"
    assert filters["locale"] == "tr"


async def test_dusuk_cozunurluklu_slot_listede_uyarisiyla_gelir() -> None:
    service, _, _ = _service()
    result = await service.overview(area="slider")
    yaz = next(row for row in result["items"] if row["id"] == 2)
    assert yaz["sizeState"] == slots.SIZE_BLURRY
    assert yaz["sizeNote"] == ("Bu bölüm 1920x640 piksel ister; seçtiğiniz görsel 1200x400 "
                               "— küçük kaldığı için telefonda bulanık çıkar.")


async def test_ozet_ve_onerilen_olculer_yanitla_birlikte_gelir() -> None:
    service, _, _ = _service()
    result = await service.overview()
    assert result["summary"]["total"] == 3
    assert result["recommended"]["slider"] == "1920x640"
    assert result["maxImageBytes"] == 2_000_000


# ========================================================= görsel denetimi

async def test_gorsel_denetimi_yazmadan_once_karari_verir_aga_cikmaz() -> None:
    service, api, _ = _service()
    verdict = service.check_image(area="slider", data=png_data_url(1200, 400))
    assert verdict["ok"] is True
    assert verdict["sizeState"] == slots.SIZE_BLURRY
    assert verdict["sizeNote"].endswith("telefonda bulanık çıkar.")
    assert api.calls == []          # denetim tamamen yerel


async def test_govdedeki_olcu_beyanina_degil_dosya_basligina_bakilir() -> None:
    # Panel "1920x640 yükledim" dese de karar dosyanın kendi başlığındandır.
    service, _, store = _service()
    result = await service.save(None, patch={"area": "slider", "title": "Yeni",
                                             "altText": "Yeni afiş", "link": "/yeni",
                                             "imageWidth": 1920, "imageHeight": 640},
                                image=png_data_url(600, 200), reason=GEREKCE,
                                actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert result["sizeState"] == slots.SIZE_BLURRY
    assert store.assets[0]["width"] == 600


# ================================================================= yazma

async def test_alt_metni_bos_birakilan_slot_kaydedilmez() -> None:
    service, api, _ = _service()
    result = await service.save(1, patch={"altText": ""}, reason=GEREKCE, actor="Ayşe",
                                dry_run=False)
    assert result["ok"] is False
    assert "Görsel açıklaması" in result["error"]
    assert "Dosya adı değil" in result["error"]          # NE yazılacağı da söylenir
    assert api.used("bbd_save_carousel_slot") == []      # mağazaya HİÇ gidilmedi


async def test_gerekce_kisa_ise_aga_hic_cikilmaz() -> None:
    service, api, _ = _service()
    result = await service.save(1, patch={"title": "Yeni"}, reason="ok", actor="Ayşe")
    assert result["ok"] is False
    assert api.calls == []


async def test_kaydetme_taze_okur_ve_dokunulmayan_alanlari_geri_gonderir() -> None:
    service, api, _ = _service()
    result = await service.save(1, patch={"title": "Sonbahar"}, reason=GEREKCE, actor="Ayşe",
                                dry_run=False)
    assert result["ok"] is True
    body = api.used("bbd_save_carousel_slot")[0]["payload"]
    assert body["title"] == "Sonbahar"
    assert body["alt"] == "Okula dönüş afişi"       # dokunulmadı, korundu
    assert body["link"] == "/okula-donus"
    assert body["channel"] == "default"


async def test_kuru_prova_olay_yaymaz_gercek_yazma_yayar() -> None:
    service, _, _ = _service()
    await service.save(1, patch={"title": "Sonbahar"}, reason=GEREKCE, actor="Ayşe",
                       dry_run=True)
    assert service.events == []                      # type: ignore[attr-defined]
    await service.save(1, patch={"title": "Sonbahar"}, reason=GEREKCE, actor="Ayşe",
                       dry_run=False)
    assert service.events[0][0] == "store_home_media.layout_changed"  # type: ignore[attr-defined]


async def test_yazma_denemesi_de_gerekcesiyle_ize_gecer() -> None:
    # Ağ koparsa "ne yapmaya çalıştık" kaydı yalnız burada kalır.
    api = FakeApi([dict(item) for item in SLOTLAR])
    api.fail.add("bbd_save_carousel_slot")
    service, _, store = _service(api)

    result = await service.save(1, patch={"title": "Sonbahar"}, reason=GEREKCE, actor="Ayşe",
                                dry_run=False)
    assert result["ok"] is False
    assert [row["result"] for row in store.audit] == ["denendi", "hata"]
    assert store.audit[0]["reason"] == GEREKCE


async def test_yeni_slot_taslak_acilir_kaydeder_kaydetmez_vitrine_dusmez() -> None:
    service, api, _ = _service()
    result = await service.save(None, patch={"area": "banner", "title": "Yeni",
                                             "altText": "Yeni afiş", "link": "/yeni"},
                                reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert api.used("bbd_save_carousel_slot")[0]["payload"]["status"] == 0


async def test_duzenleme_ucu_yayin_durumunu_degistiremez() -> None:
    # Yayın ayrı izindir; `manage` yamasıyla taşınsaydı arka kapı olurdu (K9).
    service, api, _ = _service()
    await service.save(3, patch={"status": True, "title": "Deneme kulübü"}, reason=GEREKCE,
                       actor="Ayşe", dry_run=False)
    assert api.used("bbd_save_carousel_slot")[0]["payload"]["status"] == 0


async def test_duyuru_seridine_gorsel_yuklenmez() -> None:
    service, api, _ = _service()
    result = await service.save(None, patch={"area": "announcement", "title": "Kargo bedava",
                                             "link": "/kargo"},
                                image=png_data_url(100, 40), reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert "yalnız metin gösterir" in result["error"]
    assert "Tanıtım görselleri" in result["error"]       # sıradaki adım
    assert api.used("bbd_save_carousel_slot") == []


async def test_salt_okunur_kayit_duzenlenemez_ve_nedeni_soylenir() -> None:
    api = FakeApi([])
    api.fail.add("bbd_carousel")
    api.theme_items = [{"id": 7, "type": "image_carousel", "name": "Ana slider", "status": 1}]
    service, _, _ = _service(api)

    result = await service.save(7, patch={"title": "Yeni"}, reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    # ENGEL İKİ CÜMLEDİR: neden + sıradaki adım (bkz. geliver.py BLOCKER_ACTIONS).
    assert "yalnız BAKABİLİRSİNİZ" in result["error"]
    assert "Sıradaki adım" in result["error"]


# =========================================================== yayın durumu

async def test_yayindan_kaldirma_silme_degildir_slot_listede_kalir() -> None:
    service, api, _ = _service()
    result = await service.set_status(1, published=False, reason=GEREKCE, actor="Ayşe",
                                      dry_run=False)
    assert result["ok"] is True
    body = api.used("bbd_save_carousel_slot")[0]["payload"]
    assert body["status"] == 0
    assert body["title"] == "Okula dönüş"        # kayıt duruyor, silinmedi


async def test_ayni_duruma_ikinci_kez_yazilmaz() -> None:
    service, api, _ = _service()
    result = await service.set_status(3, published=False, reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert result["error"] == "Bu zaten istediğiniz durumda; değişecek bir şey yok."
    assert api.used("bbd_save_carousel_slot") == []


async def test_olmayan_slot_anlasilir_hata_verir() -> None:
    service, _, _ = _service()
    result = await service.set_status(404, published=True, reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert "artık listede yok" in result["error"]
    assert "Yenile" in result["error"]                   # sıradaki adım


# ================================================================== sıra

async def test_sira_global_listeye_oturtulur() -> None:
    service, api, _ = _service()
    result = await service.reorder(area="slider", order=[1, 2], reason=GEREKCE, actor="Ayşe",
                                   dry_run=False)
    assert result["ok"] is True
    # Global sıralı liste: 2(slider), 3(banner), 1(slider) → slider'ın yerleri korunur.
    assert api.used("bbd_reorder_carousel")[0]["order"] == [1, 3, 2]


async def test_bayat_ekrandan_gelen_sira_reddedilir() -> None:
    service, api, _ = _service()
    result = await service.reorder(area="slider", order=[1, 2, 99], reason=GEREKCE,
                                   actor="Ayşe")
    assert result["ok"] is False
    assert api.used("bbd_reorder_carousel") == []


async def test_salt_okunur_gorunumde_sira_yazilmaz() -> None:
    api = FakeApi([])
    api.fail.add("bbd_carousel")
    api.theme_items = [{"id": 7, "type": "image_carousel", "name": "Ana slider", "status": 1}]
    service, _, _ = _service(api)

    result = await service.reorder(area="slider", order=[7], reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    # İKİ NEDEN AYRI AYRI sayılır ve kullanıcının suçlu olmadığı söylenir.
    assert "sizden kaynaklanmıyor" in result["error"]
    assert "Sıradaki adım" in result["error"]


# ========================================================= görsel yükleme

async def test_yuklemede_oran_uyarisi_onaylanmadan_aga_cikilmaz() -> None:
    # ORAN DENETİMİ ZORUNLU (K9): panelde onay kutusu göstermek yetmez, kapı
    # burada. Onay yoksa dosya mağazaya HİÇ gitmez.
    service, api, _ = _service()
    result = await service.upload_image(data=png_data_url(1920, 1080), area="slider",
                                        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert result["needsConfirm"] is True
    assert "KESİLECEK" in result["cropNote"]
    assert api.used("upload_media") == []


async def test_uyari_onaylanirsa_yuklenir_ve_iz_uyariyi_saklar() -> None:
    # "Bu banner neden bulanık" sorusunun cevabı yalnız bu satırda kalır.
    service, api, store = _service()
    result = await service.upload_image(data=png_data_url(1200, 400), area="slider",
                                        filename="Okula Dönüş.PNG", acknowledged=True,
                                        slot_id=1, reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert api.used("upload_media")[0]["filename"] == "okula-donus.png"
    assert store.assets[0]["verdict"] == slots.SIZE_BLURRY
    assert store.assets[0]["slot_id"] == 1


async def test_uygun_gorsel_onay_istemeden_yuklenir() -> None:
    service, api, _ = _service()
    result = await service.upload_image(data=png_data_url(1920, 640), area="slider",
                                        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is True
    assert result["needsConfirm"] is False
    assert result["aspect"] == "3:1"
    assert api.used("upload_media")[0]["slot"] == "slider"


async def test_uc_yayinda_degilse_ekran_hata_degil_bekleniyor_der() -> None:
    # K7: kullanıcı yanlış bir şey yapmadı; mağaza tarafındaki paket çıkmadı.
    api = FakeApi([dict(item) for item in SLOTLAR])
    api.raises["upload_media"] = FakeApiError(
        "BBD'ye özel uç henüz yayında değil: /api/admin/bbd/home/slides",
        code="bbd_endpoint_missing", status=404)
    service, _, store = _service(api)

    result = await service.upload_image(data=png_data_url(1920, 640), area="slider",
                                        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert result["pending"] is True
    # "Bu sizin hatanız değil" + "yapacağınız iş değişmiyor": bekleyen bir uç,
    # kullanıcıya hata gibi gösterilmez (K7).
    assert "sizin hatanız değil" in result["error"]
    assert "Kaydet" in result["error"]
    assert [row["result"] for row in store.audit] == ["denendi", "beklemede"]


async def test_gercek_hata_bekleniyor_diye_yutulmaz() -> None:
    api = FakeApi([dict(item) for item in SLOTLAR])
    api.raises["upload_media"] = FakeApiError("Mağaza belirteci geçersiz",
                                              code="unauthorized", status=401)
    service, _, store = _service(api)

    result = await service.upload_image(data=png_data_url(1920, 640), area="slider",
                                        reason=GEREKCE, actor="Ayşe", dry_run=False)
    assert result["ok"] is False
    assert result.get("pending") is not True
    assert "belirteci" in result["error"]
    assert store.audit[-1]["result"] == "hata"


async def test_yuklemede_gerekce_kisa_ise_dosya_hic_cozulmez() -> None:
    service, api, _ = _service()
    result = await service.upload_image(data=png_data_url(1920, 640), area="slider",
                                        reason="ok", actor="Ayşe")
    assert result["ok"] is False
    assert api.calls == []


async def test_duyuru_seridine_dosya_yuklenmez() -> None:
    service, api, _ = _service(recommended_announcement="0x0")
    result = await service.upload_image(data=png_data_url(600, 200), area="announcement",
                                        reason=GEREKCE, actor="Ayşe")
    assert result["ok"] is False
    assert "yalnız metin gösterir" in result["error"]
    assert "Tanıtım görselleri" in result["error"]       # sıradaki adım
    assert api.used("upload_media") == []


async def test_onizleme_kutusu_yanitla_birlikte_gelir_panel_gercek_orani_cizsin() -> None:
    service, _, _ = _service()
    result = service.check_image(area="slider", data=png_data_url(1920, 1080))
    assert result["previewBox"]["ratio"] == "16:9"
    assert result["recommendedAspect"] == "3:1"
    assert result["needsConfirm"] is True


# ================================================================ arama

async def test_kisa_sorguda_magazaya_hic_gidilmez() -> None:
    service, api, _ = _service()
    result = await service.link_search(q="a")
    assert result["items"] == []
    assert api.used("product_lookup") == []


async def test_urun_aramasi_hedef_baglanti_uretir() -> None:
    api = FakeApi([])
    api.lookup_items = [{"id": 5, "name": "Kalem", "sku": "KLM-1", "url_key": "kalem"}]
    service, _, _ = _service(api)
    result = await service.link_search(q="kalem")
    assert result["items"][0]["url"] == "/kalem"


# =============================================================== denetim

async def test_denetim_izi_slot_bazinda_okunur() -> None:
    service, _, store = _service()
    await service.save(1, patch={"title": "Sonbahar"}, reason=GEREKCE, actor="Ayşe",
                       dry_run=False)
    result = await service.audit(slot_id=1)
    assert result["items"][0]["reason"] == GEREKCE
    assert result["items"][0]["actor"] == "Ayşe"
    assert len(store.audit) == 2


# ================================================================= rapor

async def test_rapor_yolu_disindaki_dosya_basilmaz() -> None:
    # Serbest yol kabul etmek, `lp` ile makinedeki herhangi bir dosyayı kâğıda
    # döktürmeye açık kapı bırakırdı.
    class Yazici:
        async def print_file(self, path: Path, **kwargs: Any) -> dict[str, Any]:
            raise AssertionError("bu dosya basılmamalıydı")

    service, _, _ = _service(printer=Yazici())
    result = await service.print_report("/etc/passwd")
    assert result["ok"] is False
    assert "rapor klasöründe değil" in result["error"]


async def test_bilinmeyen_rapor_turu_reddedilir() -> None:
    service, _, _ = _service()
    result = await service.build_report("uydurma", {})
    assert result["ok"] is False
