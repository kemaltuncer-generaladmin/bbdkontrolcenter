"""Slayt dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_home_media_backend import slots
from store_home_media_fakes import jpeg_bytes, png_bytes, png_data_url

# =========================================== TUZAK 1 — yanıt camelCase gelir

def test_camel_case_alan_adi_da_okunur() -> None:
    """`sortOrder` HER SATIRDA 0 GÖRÜNÜYORDU ve sebebi buydu.

    Bagisto'nun yönetici zarfı çıktıyı camelCase'e çeviriyor; `pick` yalnız
    verilen adı (snake_case) deniyor, bulamayınca varsayılana düşüyordu. Yani
    değer geliyordu, ekran onu hiç göremiyordu.
    """
    assert slots.pick({"sortOrder": 3}, "sort_order") == 3
    assert slots.pick({"imageUrl": "/a.webp"}, "image_url") == "/a.webp"
    # Ters yön de çalışmalı: uç bir gün snake_case dönerse ekran kırılmasın.
    assert slots.pick({"sort_order": 5}, "sort_order") == 5


def test_dolu_deger_bos_degere_tercih_edilir() -> None:
    # "Yok" ile "boş" ayrımı korunur: boş ama VAR OLAN değer de dönebilmeli.
    assert slots.pick({"link": "", "target": "/kampanya"}, "link", "target") == "/kampanya"
    assert slots.pick({"link": ""}, "link", "target") == ""
    assert slots.pick({}, "link") is None


def test_camel_donusumu_tek_parcali_adi_bozmaz() -> None:
    assert slots.camel("title") == "title"
    assert slots.camel("image_url") == "imageUrl"


# ============================================================== ölçü kararı

def test_kucuk_gorsel_icin_bulanik_uyarisi_metni_uretilir() -> None:
    # Ekranın ZORUNLU alt metni. Kullanıcı "neden bulanık" diye sormasın diye
    # istenen ve seçilen ölçü aynı cümlede durur.
    verdict = slots.size_verdict(1200, 400, (1920, 640))
    assert verdict["state"] == slots.SIZE_BLURRY
    assert "1920x640" in verdict["note"]
    assert "1200x400" in verdict["note"]
    assert "bulanık" in verdict["note"]


def test_oran_farkliysa_kenarlarinin_kesilecegi_soylenir() -> None:
    verdict = slots.size_verdict(1920, 1080, (1920, 640))
    assert verdict["state"] == slots.SIZE_RATIO
    assert "kesilir" in verdict["note"]


def test_hem_kucuk_hem_orani_farkli_gorsel_iki_sorunu_da_soyler() -> None:
    verdict = slots.size_verdict(600, 600, (1920, 640))
    assert verdict["state"] == slots.SIZE_BLURRY
    assert "bulanık" in verdict["note"]
    assert "kesilir" in verdict["note"]


def test_yeterli_gorsel_uygun_der_ve_olculeri_yine_gosterir() -> None:
    verdict = slots.size_verdict(1920, 640, (1920, 640))
    assert verdict["state"] == slots.SIZE_OK
    assert "1920x640" in verdict["note"]


def test_olcu_okunamazsa_bilinmiyor_denir_uydurulmaz() -> None:
    verdict = slots.size_verdict(0, 0, (1920, 640))
    assert verdict["state"] == slots.SIZE_UNKNOWN
    assert "okunamadı" in verdict["note"]


def test_onerilen_olcu_metni_cozulur() -> None:
    assert slots.parse_size("1920x640") == (1920, 640)
    assert slots.parse_size(" 800 X 800 ") == (800, 800)
    assert slots.parse_size("bozuk") == (0, 0)


# ============================================== TUZAK 4 — kırpma gizlenmez

def test_oran_okunur_bicimde_sadelestirilir() -> None:
    assert slots.ratio_label(1920, 640) == "3:1"
    assert slots.ratio_label(800, 800) == "1:1"


def test_sadelesmeyen_oran_ondaliga_duser_anlamsiz_sayi_yazilmaz() -> None:
    # `1000:333` bir şey anlatmaz; `3.00:1` anlatır.
    assert slots.ratio_label(1000, 333) == "3.00:1"


def test_gorsel_genisse_soldan_sagdan_kirpilacagi_soylenir() -> None:
    plan = slots.crop_plan(2560, 640, (1920, 640))
    assert plan["axis"] == "yatay"
    assert plan["percent"] == 25
    assert "soldan ve sağdan" in plan["note"]
    assert plan["ok"] is False


def test_gorsel_uzunsa_ustten_alttan_kirpilacagi_soylenir() -> None:
    plan = slots.crop_plan(1920, 1080, (1920, 640))
    assert plan["axis"] == "dikey"
    assert "üstten ve alttan" in plan["note"]


def test_tolerans_icindeki_kirpma_soylenir_ama_sorun_sayilmaz() -> None:
    plan = slots.crop_plan(1960, 640, (1920, 640))
    assert plan["ok"] is True
    assert "göze çarpmaz" in plan["note"]


def test_birebir_oranda_hicbir_yeri_kesilmez_denir() -> None:
    plan = slots.crop_plan(960, 320, (1920, 640))
    assert plan["percent"] == 0
    assert "hiçbir yeri kesilmez" in plan["note"]


def test_olcusu_okunamayan_gorselde_kirpma_uydurulmaz() -> None:
    plan = slots.crop_plan(0, 0, (1920, 640))
    assert plan["known"] is False
    assert "okunamadı" in plan["note"]


def test_onizleme_kutusu_gercek_orani_korur_sabit_cerceveye_sigdirmaz() -> None:
    box = slots.preview_box(1920, 640, max_width=480, max_height=260)
    assert box["width"] == 480
    assert box["height"] == 160
    assert box["ratio"] == "3:1"


def test_kucuk_gorsel_onizlemede_buyutulmez() -> None:
    box = slots.preview_box(120, 60, max_width=480, max_height=260)
    assert (box["width"], box["height"]) == (120, 60)


def test_olcusu_bilinmeyen_gorsel_icin_kutu_cizilmez() -> None:
    assert slots.preview_box(0, 0) == {"width": 0, "height": 0, "ratio": ""}


def test_ucun_yayinda_olmamasi_hatadan_ayirt_edilir() -> None:
    assert slots.is_endpoint_pending("bbd_endpoint_missing", "") is True
    assert slots.is_endpoint_pending("", "Uç henüz yayında değil") is True
    assert slots.is_endpoint_pending("http", "Sunucu 500 döndü") is False


# ============================================ TUZAK 5 — dosya adı ve uzantı

def test_dosya_adi_ascii_ye_indirilir_baslikta_patlamasin() -> None:
    assert slots.safe_filename("Ekran Görüntüsü.png", "image/png") == "ekran-goruntusu.png"


def test_uzanti_kullanicinin_dedigine_degil_icerige_gore_yazilir() -> None:
    assert slots.safe_filename("afis.jpg", "image/png") == "afis.png"


def test_adi_tamamen_bozuk_dosya_yine_de_bir_ad_alir() -> None:
    assert slots.safe_filename("...", "image/webp") == "gorsel.webp"


# ============================================ TUZAK 2 — ölçü BAŞLIKTAN okunur

def test_png_olcusu_basliktan_okunur_tarayiciya_guvenilmez() -> None:
    assert slots.image_dimensions(png_bytes(1920, 640)) == ("image/png", 1920, 640)


def test_jpeg_olcusu_cerceve_basligindan_okunur() -> None:
    assert slots.image_dimensions(jpeg_bytes(800, 600)) == ("image/jpeg", 800, 600)


def test_taninmayan_dosya_sifir_doner_patlamaz() -> None:
    assert slots.image_dimensions(b"%PDF-1.4 sahte") == ("", 0, 0)


# ================================================ TUZAK 3 — base64 denetimi

def test_base64_gorsel_cozulur_ve_olcusu_okunur() -> None:
    result = slots.decode_image(png_data_url(1920, 640), max_bytes=1_000_000,
                                allowed=("image/png",))
    assert result["ok"] is True
    assert (result["width"], result["height"]) == (1920, 640)
    assert len(result["sha256"]) == 64


def test_tavani_asan_gorsel_reddedilir_ve_kac_kb_oldugu_soylenir() -> None:
    result = slots.decode_image(png_data_url(1920, 640), max_bytes=30,
                                allowed=("image/png",))
    assert result["ok"] is False
    assert "KB" in result["error"]


def test_izinli_olmayan_tur_reddedilir() -> None:
    result = slots.decode_image(png_data_url(100, 100), max_bytes=1_000_000,
                                allowed=("image/webp",))
    assert result["ok"] is False
    assert "image/png" in result["error"]


def test_gorsel_olmayan_veri_uzantiya_degil_icerige_bakilarak_reddedilir() -> None:
    import base64
    payload = "data:image/png;base64," + base64.b64encode(b"%PDF-1.4").decode()
    result = slots.decode_image(payload, max_bytes=1_000_000, allowed=("image/png",))
    assert result["ok"] is False
    assert "tanınmadı" in result["error"]


def test_bozuk_base64_anlasilir_hata_verir() -> None:
    result = slots.decode_image("data:image/png;base64,!!!!", max_bytes=1_000_000,
                                allowed=("image/png",))
    assert result["ok"] is False
    assert "base64" in result["error"]


# ==================================================================== satır

def test_slayt_satiri_camel_case_yanittan_kurulur() -> None:
    """Mağaza ucu camelCase döndürüyor; satır o yanıttan kurulabilmeli."""
    row = slots.slide_row(
        {"index": 2, "title": "TYT", "link": "/tyt",
         "image": "storage/theme/1/sliders/a.webp",
         "imageUrl": "https://bbdstore.com.tr/storage/theme/1/sliders/a.webp"},
        index=0, wanted=(1920, 640))
    assert row["index"] == 2
    assert row["image"] == "storage/theme/1/sliders/a.webp"
    assert row["imageUrl"].startswith("https://")


def test_sira_yaniti_tasimiyorsa_listedeki_konum_kullanilir() -> None:
    row = slots.slide_row({"title": "TYT", "image": "storage/a.webp"}, index=4)
    assert row["index"] == 4


def test_olcu_bilinmiyorsa_satirda_uyari_cikmaz() -> None:
    """Mağazanın slayt ucu en/boy taşımıyor. On satırın hepsinde "ölçü
    okunamadı" yazmak, gerçekten sorunlu tek görseli gürültüde kaybederdi."""
    row = slots.slide_row({"title": "TYT", "link": "/tyt", "image": "storage/a.webp"},
                          index=0, wanted=(1920, 640))
    assert row["sizeState"] == ""
    assert row["issues"] == []


def test_olcu_elde_oldugunda_karar_satira_yazilir() -> None:
    row = slots.slide_row({"title": "TYT", "link": "/tyt", "image": "storage/a.webp",
                           "imageWidth": 600, "imageHeight": 200},
                          index=0, wanted=(1920, 640))
    assert row["sizeState"] == slots.SIZE_BLURRY
    assert "görsel küçük, bulanık çıkar" in row["issues"]


def test_satir_eksikleri_metinle_listelenir_renkle_degil() -> None:
    row = slots.slide_row({"title": "", "link": "", "image": ""}, index=0)
    assert row["issues"] == ["adı yok", "görsel yok", "tıklayınca gideceği yer yok"]


# ==================================================================== yazma

def test_yama_yalniz_uc_alani_tasir() -> None:
    """Mağaza dördüncü alanı reddediyor; tanımadığımız bir alanı geri
    göndermek, orada ne olduğunu bilmediğimiz bir değeri ezmek demektir."""
    clean = slots.normalize_slides([
        {"title": "TYT", "link": "/tyt", "image": "storage/a.webp",
         "index": 3, "imageUrl": "https://ornek/a.webp", "status": 1},
    ])
    assert clean == [{"title": "TYT", "link": "/tyt", "image": "storage/a.webp"}]


def test_bos_liste_yazilamaz() -> None:
    hata = slots.slides_error([])
    assert "boş kaydedilemez" in hata


def test_adsiz_slayt_yazilamaz() -> None:
    hata = slots.slides_error([{"title": "", "link": "", "image": "storage/a.webp"}])
    assert hata.startswith("1. görselin adı boş")


def test_gorselsiz_slayt_yazilamaz() -> None:
    hata = slots.slides_error([{"title": "TYT", "link": "", "image": ""}])
    assert "1. sıradaki görsel boş" in hata


def test_protokolsuz_adres_yazilamaz() -> None:
    hata = slots.slides_error([{"title": "TYT", "link": "bbdstore.com.tr",
                                "image": "storage/a.webp"}])
    assert "https://" in hata


def test_bos_adres_kabul_edilir_tiklanmaz_olur() -> None:
    # Adres BOŞ bırakılabilir: o görsel yalnız tıklanmaz. Zorunlu kılmak,
    # tanıtım amaçlı bir görseli kaydedilemez yapardı.
    assert slots.slides_error([{"title": "TYT", "link": "", "image": "storage/a.webp"}]) == ""


def test_tavani_asan_liste_yazilamaz() -> None:
    cok = [{"title": f"{i}", "link": "", "image": "storage/a.webp"}
           for i in range(slots.MAX_SLIDES + 1)]
    assert str(slots.MAX_SLIDES) in slots.slides_error(cok)


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert slots.reason_error("kısa")
    assert slots.reason_error("Eylül kampanyası başladı") == ""


def test_tasima_kurali_klavye_ve_surukleme_icin_tektir() -> None:
    assert slots.move([1, 2, 3], 0, 1) == [2, 1, 3]
    assert slots.move([1, 2, 3], 2, 1) == [1, 2, 3]        # sınırda değişmez
    assert slots.move([1, 2, 3], 0, -1) == [1, 2, 3]
