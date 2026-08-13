"""Slot dönüşümleri — saf mantık, ağa çıkmaz.

Buradaki her test bir TUZAĞA karşılık gelir; testin adı tuzağın kendisidir.
"""

from __future__ import annotations

from store_home_media_backend import slots
from store_home_media_fakes import jpeg_bytes, png_bytes, png_data_url

BUGUN = "2026-08-13"


def _slot(**over: object) -> dict[str, object]:
    base = {"id": 1, "area": "slider", "title": "Okula dönüş", "alt": "Okula dönüş afişi",
            "link": "/kampanya", "status": 1, "sort_order": 1,
            "image_url": "https://bbdstore.com.tr/x.jpg", "image_width": 1920,
            "image_height": 640}
    base.update(over)
    return base


# ============================================================== ölçü kararı

def test_kucuk_gorsel_icin_mobilde_bulanik_uyarisi_metni_uretilir() -> None:
    # Ekranın ZORUNLU alt metni. Kullanıcı "neden bulanık" diye sormasın diye
    # önerilen ve yüklenen ölçü aynı cümlede durur.
    verdict = slots.size_verdict(1200, 400, (1920, 640))
    assert verdict["state"] == slots.SIZE_BLURRY
    assert verdict["note"] == "Önerilen 1920x640; yüklenen 1200x400 — mobilde bulanık."


def test_oran_farkliysa_kirpilacagi_soylenir() -> None:
    verdict = slots.size_verdict(1920, 1080, (1920, 640))
    assert verdict["state"] == slots.SIZE_RATIO
    assert "kırpılacak" in verdict["note"]


def test_hem_kucuk_hem_orani_farkli_gorsel_iki_sorunu_da_soyler() -> None:
    verdict = slots.size_verdict(600, 600, (1920, 640))
    assert verdict["state"] == slots.SIZE_BLURRY
    assert "mobilde bulanık" in verdict["note"]
    assert "oran farklı" in verdict["note"]


def test_yeterli_gorsel_uygun_der_ve_olculeri_yine_gosterir() -> None:
    verdict = slots.size_verdict(2400, 800, (1920, 640))
    assert verdict["state"] == slots.SIZE_OK
    assert verdict["note"] == "Önerilen 1920x640; yüklenen 2400x800 — uygun."


def test_gorsel_istemeyen_alanda_olcu_uyarisi_cikmaz() -> None:
    # Duyuru şeridi metindir; "0x0 önerilir" demek anlamsız olurdu.
    verdict = slots.size_verdict(0, 0, (0, 0))
    assert verdict["state"] == slots.SIZE_NONE


def test_olcu_okunamazsa_bilinmiyor_denir_uydurulmaz() -> None:
    verdict = slots.size_verdict(0, 0, (1920, 640))
    assert verdict["state"] == slots.SIZE_UNKNOWN
    assert "okunamadı" in verdict["note"]


def test_onerilen_olcu_metni_cozulur() -> None:
    assert slots.parse_size("1920x640") == (1920, 640)
    assert slots.parse_size(" 800 X 800 ") == (800, 800)
    assert slots.parse_size("bozuk") == (0, 0)


# ====================================================== oran ve kırpma planı

def test_oran_okunur_bicimde_sadelestirilir() -> None:
    assert slots.ratio_label(1920, 640) == "3:1"
    assert slots.ratio_label(1920, 1080) == "16:9"
    assert slots.ratio_label(800, 800) == "1:1"


def test_sadelesmeyen_oran_ondaliga_duser_anlamsiz_sayi_yazilmaz() -> None:
    # "1000:333" kimseye bir şey anlatmaz; "3.00:1" anlatır.
    assert slots.ratio_label(1000, 333) == "3.00:1"


def test_gorsel_genisse_soldan_sagdan_kirpilacagi_soylenir() -> None:
    # Kullanıcının sorusu "yazı kesilir mi" — cevap yüzde değil KENAR.
    plan = slots.crop_plan(1920, 480, (1920, 640))
    assert plan["axis"] == "yatay"
    assert plan["ok"] is False
    assert "soldan ve sağdan" in plan["note"]
    assert plan["percent"] == 25


def test_gorsel_uzunsa_ustten_alttan_kirpilacagi_soylenir() -> None:
    plan = slots.crop_plan(1920, 1080, (1920, 640))
    assert plan["axis"] == "dikey"
    assert plan["ok"] is False
    assert "üstten ve alttan" in plan["note"]


def test_tolerans_icindeki_kirpma_soylenir_ama_sorun_sayilmaz() -> None:
    plan = slots.crop_plan(1920, 620, (1920, 640))
    assert plan["ok"] is True
    assert "göze çarpmaz" in plan["note"]


def test_birebir_oranda_kirpma_yok_denir() -> None:
    plan = slots.crop_plan(1200, 400, (1920, 640))
    assert plan["percent"] == 0
    assert "kırpma olmaz" in plan["note"]


def test_gorsel_istemeyen_alanda_kirpma_hesaplanmaz() -> None:
    assert slots.crop_plan(100, 40, (0, 0))["percent"] == 0


def test_olcusu_okunamayan_gorselde_kirpma_uydurulmaz() -> None:
    plan = slots.crop_plan(0, 0, (1920, 640))
    assert plan["known"] is False
    assert plan["percent"] == 0


# ================================================= gerçek oranlı önizleme

def test_onizleme_kutusu_gercek_orani_korur_sabit_cerceveye_sigdirmaz() -> None:
    # TUZAK 8: sabit çerçeveye `cover` ile sığdırmak kırpmayı GİZLERDİ.
    box = slots.preview_box(1920, 1080, max_width=480, max_height=260)
    assert box["ratio"] == "16:9"
    assert box["width"] <= 480 and box["height"] <= 260
    assert abs(box["width"] / box["height"] - 1920 / 1080) < 0.02


def test_kucuk_gorsel_onizlemede_buyutulmez() -> None:
    # Büyütmek bulanıklığı gizler; ekran görseli olduğu gibi gösterir.
    assert slots.preview_box(120, 40) == {"width": 120, "height": 40, "ratio": "3:1"}


def test_olcusu_bilinmeyen_gorsel_icin_kutu_cizilmez() -> None:
    assert slots.preview_box(0, 0)["width"] == 0


# ============================================ yükleme ucu ve dosya adı

def test_ucun_yayinda_olmamasi_hatadan_ayirt_edilir() -> None:
    # Kırmızı hata göstermek her gün tekrarlanan bir yanlış alarm olurdu (K7).
    assert slots.is_endpoint_pending("bbd_endpoint_missing", "") is True
    assert slots.is_endpoint_pending("", "BBD'ye özel uç henüz yayında değil: /x") is True
    assert slots.is_endpoint_pending("unauthorized", "Belirteç geçersiz") is False


def test_dosya_adi_ascii_ye_indirilir_baslikta_patlamasin() -> None:
    # multipart başlığı latin-1'dir; `ö` orada UnicodeEncodeError üretir.
    assert slots.safe_filename("Ekran Görüntüsü 2026.png", "image/png") == "ekran-goruntusu-2026.png"


def test_uzanti_kullanicinin_dedigine_degil_icerige_gore_yazilir() -> None:
    assert slots.safe_filename("afis.jpg", "image/png") == "afis.png"


def test_adi_tamamen_bozuk_dosya_yine_de_bir_ad_alir() -> None:
    assert slots.safe_filename("!!!.png", "image/webp") == "gorsel.webp"


# ============================================================ görsel başlığı

def test_png_olcusu_basliktan_okunur_tarayiciya_guvenilmez() -> None:
    assert slots.image_dimensions(png_bytes(1920, 640)) == ("image/png", 1920, 640)


def test_jpeg_olcusu_cerceve_basligindan_okunur() -> None:
    assert slots.image_dimensions(jpeg_bytes(1200, 400)) == ("image/jpeg", 1200, 400)


def test_taninmayan_dosya_sifir_doner_patlamaz() -> None:
    assert slots.image_dimensions(b"merhaba bu bir gorsel degil") == ("", 0, 0)
    assert slots.image_dimensions(b"") == ("", 0, 0)


def test_base64_gorsel_cozulur_ve_olcusu_okunur() -> None:
    result = slots.decode_image(png_data_url(1920, 640), max_bytes=2_000_000,
                                allowed=("image/png",))
    assert result["ok"] is True
    assert (result["width"], result["height"]) == (1920, 640)
    assert len(result["sha256"]) == 64


def test_tavani_asan_gorsel_reddedilir_ve_kac_kb_oldugu_soylenir() -> None:
    result = slots.decode_image(png_data_url(1920, 640), max_bytes=20, allowed=("image/png",))
    assert result["ok"] is False
    assert "tavan" in result["error"]


def test_izinli_olmayan_tur_reddedilir() -> None:
    result = slots.decode_image(png_data_url(100, 100), max_bytes=2_000_000,
                                allowed=("image/webp",))
    assert result["ok"] is False
    assert "image/png" in result["error"]


def test_gorsel_olmayan_veri_uzantiya_degil_icerige_bakilarak_reddedilir() -> None:
    # Gövde "image/png" diyor ama içerik PNG değil. Beyan edilen türe güvenmek,
    # vitrine bozuk bir dosya asmak demekti.
    result = slots.decode_image("data:image/png;base64,aGVsbG8gd29ybGQ=",
                                max_bytes=2_000_000, allowed=("image/png",))
    assert result["ok"] is False
    assert "tanınmadı" in result["error"]


def test_bozuk_base64_anlasilir_hata_verir() -> None:
    result = slots.decode_image("data:image/png;base64,!!!!", max_bytes=2_000_000,
                                allowed=("image/png",))
    assert result["ok"] is False
    assert "base64" in result["error"]


# ================================================================ durumlar

def test_yayin_penceresi_yerel_takvim_gunune_gore_hesaplanir() -> None:
    ileri = _slot(starts_at="2026-09-01", ends_at="2026-09-30")
    gecmis = _slot(starts_at="2026-07-01", ends_at="2026-07-31")
    acik = _slot(starts_at="2026-08-01", ends_at="2026-08-31")
    assert slots.slot_state(ileri, today=BUGUN) == slots.STATE_SCHEDULED
    assert slots.slot_state(gecmis, today=BUGUN) == slots.STATE_EXPIRED
    assert slots.slot_state(acik, today=BUGUN) == slots.STATE_PUBLISHED


def test_pasif_slot_tarihi_acik_olsa_da_taslak_gorunur() -> None:
    # "Yayında değil" bilgisi kullanıcı için tarihten daha kesindir.
    row = _slot(status=0, starts_at="2026-08-01", ends_at="2026-08-31")
    assert slots.slot_state(row, today=BUGUN) == slots.STATE_DRAFT


def test_saatli_tarih_gune_indirgenir() -> None:
    # Mağaza aynı alanı bazen saatli veriyor; saat taşımak bugün başlayan
    # banner'ı öğlene kadar kapalı gösterirdi.
    assert slots.day_of("2026-08-13 09:00:00") == "2026-08-13"
    assert slots.day_of("2026-08-13T09:00:00Z") == "2026-08-13"
    assert slots.day_of("yok") == ""


def test_bilinmeyen_alan_dusurulmez_banner_sayilir() -> None:
    # Vitrinde duran bir slot ekranda hiç görünmezse kimse onu kaldıramaz.
    assert slots.area_of({"area": "acayip"}) == "banner"
    assert slots.area_of({"type": "hero"}) == "slider"
    assert slots.area_of({"section": "ticker"}) == "announcement"


def test_alan_adi_kesinlesmediginden_ayni_bilgi_birkac_adda_aranir() -> None:
    assert slots.pick({"heading": "Merhaba"}, "title", "heading") == "Merhaba"
    assert slots.pick({"title": "", "heading": "Dolu"}, "title", "heading") == "Dolu"
    assert slots.pick({"title": ""}, "title", "heading") == ""
    assert slots.pick({}, "title") is None


# =================================================================== satır

def test_satir_eksikleri_metinle_listelenir_renkle_degil() -> None:
    row = slots.slot_row(_slot(alt="", link=""), today=BUGUN, wanted=(1920, 640))
    assert "alt metni yok" in row["issues"]
    assert "hedef bağlantı yok" in row["issues"]
    assert row["stateLabel"] == "Yayında"


def test_dusuk_cozunurluk_satirda_uyari_olarak_gorunur() -> None:
    row = slots.slot_row(_slot(image_width=1200, image_height=400), today=BUGUN,
                         wanted=(1920, 640))
    assert row["sizeState"] == slots.SIZE_BLURRY
    assert "düşük çözünürlük" in row["issues"]


def test_tiklama_olculmuyorsa_sifir_yazilmaz() -> None:
    # "0 tıklama" ile "tıklama ölçülmüyor" aynı şey değil; uç bu alanı
    # taşımıyorsa ekran ölçüm varmış gibi davranmaz.
    yok = slots.slot_row(_slot(), today=BUGUN, wanted=(1920, 640))
    var = slots.slot_row(_slot(clicks=0), today=BUGUN, wanted=(1920, 640))
    assert yok["clicksKnown"] is False
    assert var["clicksKnown"] is True and var["clicks"] == 0


def test_gorselsiz_slot_gorsel_yok_der() -> None:
    row = slots.slot_row(_slot(image_url="", image_width=0, image_height=0), today=BUGUN,
                         wanted=(1920, 640))
    assert "görsel yok" in row["issues"]
    assert row["sizeNote"] == "Görsel yüklenmemiş."


# ================================================================= süzgeç

def _rows() -> list[dict[str, object]]:
    raw = [
        _slot(id=1, area="slider", title="Okula dönüş", alt="Okula dönüş afişi",
              link="/okula-donus", sort_order=2),
        _slot(id=2, area="slider", title="Yaz indirimi", alt="Yaz afişi", link="/yaz",
              sort_order=1, starts_at="2026-06-01", ends_at="2026-07-31"),
        _slot(id=3, area="banner", title="Deneme kulübü", alt="Deneme kulübü afişi",
              link="/deneme", device="mobile", sort_order=1),
        _slot(id=4, area="announcement", title="Kargo bedava", alt="", link="/kargo",
              image_url="", sort_order=1),
    ]
    return [slots.slot_row(item, today=BUGUN, wanted=(1920, 640)) for item in raw]


def test_arama_aksansiz_eslesir() -> None:
    found = slots.filter_rows(_rows(), q="donus")
    assert [row["id"] for row in found] == [1]


def test_durum_suzgeci_hesaplanan_duruma_gore_calisir() -> None:
    found = slots.filter_rows(_rows(), status=slots.STATE_EXPIRED)
    assert [row["id"] for row in found] == [2]


def test_cihaz_suzgeci_tumu_cihazli_slotlari_de_getirir() -> None:
    # "Mobil" süzgeci yalnız mobile özel slotları göstermez: her cihazda
    # görünen slot mobilde de görünüyor ve listeden düşmesi yanıltıcı olur.
    found = slots.filter_rows(_rows(), device="mobile")
    assert {row["id"] for row in found} == {1, 2, 3, 4}


def test_yayin_araligi_suzgeci_cakisan_slotlari_getirir() -> None:
    found = slots.filter_rows(_rows(), start="2026-06-15", end="2026-06-20")
    assert 2 in {row["id"] for row in found}


def test_ozet_alt_metni_eksikleri_sayar() -> None:
    stats = slots.summary(_rows())
    assert stats["total"] == 4
    assert stats["missingAlt"] == 1
    assert stats[slots.STATE_EXPIRED] == 1


def test_siralama_sortorder_sonra_kimlik() -> None:
    ordered = [row["id"] for row in slots.sort_rows(_rows())]
    assert ordered[0] in (2, 3, 4)
    assert ordered[-1] == 1


# ================================================================== sıra

def test_sira_yalniz_kendi_alaninda_degisir_digerleri_yerinde_kalir() -> None:
    # Mağazanın reorder ucu GLOBAL liste ister; yalnız açık sekmenin sırasını
    # göndermek diğer üç şeridi karıştırırdı.
    rows = _rows()
    merged = slots.merged_order(rows, "slider", [1, 2])
    assert merged["ok"] is True
    # Sıralı global liste: 2(slider), 3(banner), 4(announcement), 1(slider).
    # Slider'ın YERLERİ korunur; sadece o iki yere yazılan kimlik değişir.
    assert merged["order"] == [1, 3, 4, 2]


def test_eksik_kimlikli_sira_reddedilir() -> None:
    rows = _rows()
    merged = slots.merged_order(rows, "slider", [1])
    assert merged["ok"] is False
    assert "eşleşmiyor" in merged["error"]


def test_degismeyen_sira_yazilmaz() -> None:
    rows = _rows()
    merged = slots.merged_order(rows, "slider", [2, 1])
    assert merged["ok"] is False
    assert merged["error"] == "Sıra değişmedi."


def test_tasima_kurali_klavye_ve_surukleme_icin_tektir() -> None:
    assert slots.move([1, 2, 3], 0, 1) == [2, 1, 3]
    assert slots.move([1, 2, 3], 0, -1) == [1, 2, 3]     # sınır dışına taşmaz
    assert slots.move([1, 2, 3], 2, 1) == [1, 2, 3]


# ================================================================== yazma

def test_alt_metni_olmadan_yazma_reddedilir() -> None:
    body = {"title": "Okula dönüş", "alt": "", "link": "/x"}
    assert "alt metni" in slots.slot_error(body, area="slider", has_image=True)


def test_duyuru_seridi_gorsel_kabul_etmez() -> None:
    body = {"title": "Kargo bedava", "link": "/kargo"}
    assert "görsel yüklenmez" in slots.slot_error(body, area="announcement", has_image=True)


def test_serbest_baglanti_protokolsuz_yazilamaz() -> None:
    body = {"title": "X", "alt": "X afişi", "link": "bbdstore.com.tr", "link_kind": "url"}
    assert "https://" in slots.slot_error(body, area="banner", has_image=False)
    body["link"] = "/kampanya"
    assert slots.slot_error(body, area="banner", has_image=False) == ""


def test_bitis_baslangictan_once_olamaz() -> None:
    body = {"title": "X", "alt": "X afişi", "starts_at": "2026-09-10",
            "ends_at": "2026-09-01"}
    assert "bitişten sonra" in slots.slot_error(body, area="banner", has_image=False)


def test_gerekce_on_karakterden_kisa_olamaz() -> None:
    assert slots.reason_error("kısa") != ""
    assert slots.reason_error("Eylül kampanyası başlıyor") == ""


def test_yama_yalniz_taninan_alanlari_tasir() -> None:
    patch = slots.normalize_patch({"title": "Yeni", "altText": "Yeni afiş", "uydurma": 1,
                                   "startsAt": "2026-09-01T00:00:00"})
    assert patch == {"title": "Yeni", "alt": "Yeni afiş", "starts_at": "2026-09-01"}


def test_duzenleme_yamasi_yayin_durumunu_degistiremez() -> None:
    # Yayın durumu ayrı izin ister (store_home_media.publish). Yamayla
    # taşınsaydı yalnız `manage` izni olan biri slotu vitrine düşürebilirdi.
    assert slots.normalize_patch({"status": True, "title": "X"}) == {"title": "X"}


def test_yazma_govdesi_dokunulmayan_alanlari_geri_koyar() -> None:
    # Kısmi gövde göndermek, gönderilmeyen alanları boşaltma riski taşıyor.
    current = slots.slot_row(_slot(), today=BUGUN, wanted=(1920, 640))
    body = slots.write_body(current, {"title": "Yeni başlık"}, channel="default", locale="tr")
    assert body["title"] == "Yeni başlık"
    assert body["alt"] == "Okula dönüş afişi"
    assert body["link"] == "/kampanya"
    assert body["channel"] == "default" and body["locale"] == "tr"


def test_tema_kaydi_slot_bicimine_yaklastirilir() -> None:
    mapped = slots.theme_slot({"id": 7, "type": "image_carousel", "name": "Ana slider",
                               "sort_order": 1, "status": 1})
    assert mapped["area"] == "slider"
    assert mapped["title"] == "Ana slider"
